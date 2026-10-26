/**
 * Generation-parameter extraction from ComfyUI PNG metadata (#75).
 *
 * Works on the API-format `prompt` graph ({id: {class_type, inputs}}) by
 * tracing links from the sampler, instead of guessing by node type or by
 * keywords in the text. Falls back to the UI `workflow` graph only for
 * fields the API graph could not provide.
 *
 * Shared by admin.js, gallery.js and metadata.html; also loadable in Node
 * for tests.
 */
(function (root) {
    "use strict";

    const PLACEHOLDERS = Object.freeze({
        positivePrompt: "No prompt found",
        negativePrompt: "No negative prompt found",
        checkpoint: "Unknown",
        steps: "Unknown",
        cfgScale: "Unknown",
        sampler: "Unknown",
        seed: "Unknown",
    });

    // Upper bound on nodes expanded per lookup; crafted PNGs can contain any graph
    const MAX_VISITS = 500;
    const MODEL_NAME_KEYS = ["ckpt_name", "unet_name", "model_name", "gguf_name"];
    const TEXT_KEYS = /^(text|string|prompt|value)(_?[a-z0-9]+)?$/i;
    const SEED_CONTROL_VALUES = new Set(["fixed", "increment", "decrement", "randomize"]);

    const isLink = (v) =>
        Array.isArray(v) &&
        v.length === 2 &&
        (typeof v[0] === "string" || typeof v[0] === "number") &&
        typeof v[1] === "number";

    // KSampler or a guider (CFGGuider); the model link excludes ControlNet-style pass-throughs
    const isSampler = (node) =>
        node &&
        node.inputs &&
        isLink(node.inputs.positive) &&
        isLink(node.inputs.negative) &&
        isLink(node.inputs.model);

    // SamplerCustomAdvanced-style nodes split settings across noise/sampler/sigmas nodes
    const isCustomSampler = (node) =>
        node && node.inputs && isLink(node.inputs.guider) && isLink(node.inputs.sigmas);

    const workflowNodes = (workflow) => (Array.isArray(workflow && workflow.nodes) ? workflow.nodes : []);
    const widgetsOf = (n) => (Array.isArray(n.widgets_values) ? n.widgets_values : []);

    /**
     * Tracing context: the API graph plus recovered text for PromptManager nodes
     * whose saved `text` was corrupted.
     *
     * Before 3.2.3 a SaveImage patch overwrote every PromptManager node's `text`
     * with the last-run node's prompt. The signature is two or more PromptManager
     * nodes with identical API text whose workflow widgets differ; only those get
     * their workflow widget text back. Otherwise the API graph is what actually ran
     * (expanded dynamic prompts, linked text) and always wins.
     */
    function createContext(graph, workflow, conservative = false) {
        const widgets = new Map();
        for (const n of workflowNodes(workflow)) {
            const text = widgetsOf(n)[0];
            if (n.type !== "PromptManagerPair" && /promptmanager/i.test(n.type || "") && typeof text === "string") {
                widgets.set(String(n.id), text);
            }
        }

        const byApiText = new Map();
        for (const id of widgets.keys()) {
            const apiText = graph[id] && graph[id].inputs && graph[id].inputs.text;
            if (typeof apiText !== "string") continue;
            byApiText.set(apiText, [...(byApiText.get(apiText) || []), id]);
        }

        const widgetText = new Map();
        for (const ids of byApiText.values()) {
            const distinctWidgets = new Set(ids.map((id) => widgets.get(id)));
            if (ids.length > 1 && distinctWidgets.size > 1) {
                ids.forEach((id) => widgetText.set(id, widgets.get(id)));
            }
        }
        return { graph, widgetText, conservative };
    }

    /**
     * Returns [nodeId, node] for a link, or [] when it does not resolve or the node
     * was already expanded in this lookup. The shared `seen` set keeps every lookup
     * linear in graph size, even for cyclic or heavily fanned-out graphs.
     */
    function enter(ctx, link, seen) {
        if (!isLink(link) || seen.size >= MAX_VISITS) return [];
        if (ctx.conservative && (!Number.isSafeInteger(link[1]) || link[1] < 0 || seen.size >= 64)) return [];
        const id = String(link[0]);
        const node = !ctx.conservative || Object.hasOwn(ctx.graph, id) ? ctx.graph[id] : undefined;
        const key = `${id}:${link[1]}`;
        if (!node || !node.inputs || seen.has(key)) return [];
        seen.add(key);
        return [id, node];
    }

    /**
     * Resolve a scalar input, following a link to a primitive node if needed.
     * Multi-output settings nodes (e.g. SamplerCombo) expose several values, so an
     * input with the same `name` as the requested field wins over the first scalar.
     */
    function resolveScalar(ctx, value, name, seen = new Set()) {
        if (!isLink(value)) return value;
        const [, node] = enter(ctx, value, seen);
        if (!node) return undefined;
        if (ctx.conservative) {
            if (node[WORKFLOW_PASSTHROUGH]) return node[WORKFLOW_PASSTHROUGH] === "value" && value[1] === 0 ? resolveScalar(ctx, node.inputs.value, name, seen) : undefined;
            const kind = ["seed", "noise_seed", "steps", "cfg"].includes(name) ? "number" : "string";
            return value[1] === 0 && SCALAR_OUTPUTS[node.class_type] === kind && Object.hasOwn(node.inputs, "value") ?
                resolveScalar(ctx, node.inputs.value, name, seen) : undefined;
        }
        if (name && Object.prototype.hasOwnProperty.call(node.inputs, name)) {
            return resolveScalar(ctx, node.inputs[name], name, seen);
        }
        for (const v of Object.values(node.inputs)) {
            const resolved = resolveScalar(ctx, v, name, seen);
            if (resolved !== undefined && typeof resolved !== "object") return resolved;
        }
        return undefined;
    }

    /** Read the first of `keys` from the node a link points to, resolving further links. */
    function resolveLinkedInput(ctx, link, keys) {
        const seen = new Set();
        const [, node] = enter(ctx, link, seen);
        if (!node) return undefined;
        const key = keys.find((k) => k in node.inputs);
        return key === undefined ? undefined : resolveScalar(ctx, node.inputs[key], key, seen);
    }

    /** Resolve a string input; text-producing nodes may be chained (concat, primitives). */
    function resolveString(ctx, value, seen = new Set()) {
        if (typeof value === "string") return value;
        const [id, node] = enter(ctx, value, seen);
        if (!node) return "";
        if (node.class_type === "PromptManagerPair") {
            if (value[1] === 3) return resolveString(ctx, node.inputs.negative_text, seen);
            return value[1] === 2 ? composeNodeText(ctx, id, node, seen) : "";
        }
        if ("text" in node.inputs) return composeNodeText(ctx, id, node, seen);
        const delimiter = typeof node.inputs.delimiter === "string" ? node.inputs.delimiter : " ";
        return Object.entries(node.inputs)
            .filter(([key]) => TEXT_KEYS.test(key))
            .map(([, v]) => resolveString(ctx, v, seen))
            .filter((s) => s.trim())
            .join(delimiter);
    }

    /** Text of an encoder node, matching PromptManager's prepend + text + append join. */
    function composeNodeText(ctx, id, node, seen) {
        const { inputs } = node;
        const raw = ctx.widgetText.has(id) ? ctx.widgetText.get(id) : resolveString(ctx, inputs.text, seen);
        const text = raw.trim();
        if (!text) return "";
        // One shared set even across siblings: copying it per branch reintroduces
        // exponential work on crafted chains
        const prepend = resolveString(ctx, inputs.prepend_text, seen).trim();
        const append = resolveString(ctx, inputs.append_text, seen).trim();
        // Older saves stored the already-combined prompt in `text`
        return [
            prepend && !text.startsWith(prepend) ? prepend : "",
            text,
            append && !text.endsWith(append) ? append : "",
        ]
            .filter(Boolean)
            .join(" ");
    }

    /** Follow a conditioning link upstream to the encoder that produced it. */
    function traceConditioning(ctx, link, role, seen = new Set()) {
        const [id, node] = enter(ctx, link, seen);
        if (!node) return "";
        if (node.class_type === "PromptManagerPair") {
            if (link[1] === 1) return resolveString(ctx, node.inputs.negative_text, seen);
            return link[1] === 0 ? composeNodeText(ctx, id, node, seen) : "";
        }
        if ("text" in node.inputs) return composeNodeText(ctx, id, node, seen);
        // Pass-through nodes (ControlNet apply, conditioning combine/set area, ...)
        const { inputs } = node;
        const next = inputs[role] || inputs.conditioning || inputs.conditioning_to || inputs.conditioning_1;
        return traceConditioning(ctx, next, role, seen);
    }

    /** Follow the sampler's model link upstream (through LoRA loaders etc.) to a loader. */
    function traceModelName(ctx, link, seen = new Set()) {
        const [, node] = enter(ctx, link, seen);
        if (!node) return undefined;
        if (ctx.conservative) {
            if (link[1] !== 0) return undefined;
            if (node[WORKFLOW_PASSTHROUGH]) return traceModelName(ctx, node.inputs.value, seen);
            const key = Object.hasOwn(MODEL_OUTPUTS, node.class_type) ? MODEL_OUTPUTS[node.class_type] : undefined;
            if (key) return resolveScalar(ctx, node.inputs[key], key, seen);
            return MODEL_PASSTHROUGHS.has(node.class_type) ? traceModelName(ctx, node.inputs.model, seen) : undefined;
        }
        const key = MODEL_NAME_KEYS.find((k) => typeof node.inputs[k] === "string");
        return key ? node.inputs[key] : traceModelName(ctx, node.inputs.model, seen);
    }

    function findAnyModelName(nodes) {
        for (const node of nodes) {
            const inputs = (node && node.inputs) || {};
            const key = MODEL_NAME_KEYS.find((k) => typeof inputs[k] === "string");
            if (key) return inputs[key];
        }
        return undefined;
    }

    /** The sampler the legacy parse reads: a custom sampler's guider, else the first sampler. */
    function selectSampler(ctx, nodes) {
        const custom = nodes.find(isCustomSampler);
        const [, guider] = custom ? enter(ctx, custom.inputs.guider, new Set()) : [];
        return { custom, sampler: isSampler(guider) ? guider : nodes.find(isSampler) };
    }

    function fromPromptGraph(graph, workflow) {
        const ctx = createContext(graph, workflow);
        const nodes = Object.values(graph);
        const { custom, sampler } = selectSampler(ctx, nodes);
        if (!sampler) {
            const loose = nodes.find((n) => n && n.inputs && /ksampler/i.test(n.class_type || ""));
            const inputs = (loose && loose.inputs) || {};
            return {
                checkpoint: findAnyModelName(nodes),
                seed: resolveScalar(ctx, inputs.seed ?? inputs.noise_seed, "seed"),
                steps: resolveScalar(ctx, inputs.steps, "steps"),
                cfgScale: resolveScalar(ctx, inputs.cfg, "cfg"),
                sampler: resolveScalar(ctx, inputs.sampler_name, "sampler_name"),
            };
        }

        const { inputs } = sampler;
        const fromCustom = (link, keys) => (custom ? resolveLinkedInput(ctx, link, keys) : undefined);
        return {
            positivePrompt: traceConditioning(ctx, inputs.positive, "positive"),
            negativePrompt: traceConditioning(ctx, inputs.negative, "negative"),
            checkpoint: traceModelName(ctx, inputs.model) || findAnyModelName(nodes),
            seed:
                resolveScalar(ctx, inputs.seed ?? inputs.noise_seed, "seed" in inputs ? "seed" : "noise_seed") ??
                fromCustom(custom && custom.inputs.noise, ["noise_seed", "seed"]),
            steps: resolveScalar(ctx, inputs.steps, "steps") ?? fromCustom(custom && custom.inputs.sigmas, ["steps"]),
            cfgScale: resolveScalar(ctx, inputs.cfg, "cfg"),
            sampler:
                resolveScalar(ctx, inputs.sampler_name, "sampler_name") ??
                fromCustom(custom && custom.inputs.sampler, ["sampler_name"]),
        };
    }

    // Widget order for core nodes, used when a workflow carries no widget names
    const PROMPT_MANAGER_WIDGETS = ["text", "category", "tags", "search_text", "prepend_text", "append_text"];
    const WIDGET_POSITIONS = {
        KSampler: ["seed", "steps", "cfg", "sampler_name", "scheduler", "denoise"],
        KSamplerAdvanced: [
            "add_noise", "noise_seed", "steps", "cfg", "sampler_name", "scheduler",
            "start_at_step", "end_at_step", "return_with_leftover_noise",
        ],
        CLIPTextEncode: ["text"],
        PromptManager: PROMPT_MANAGER_WIDGETS,
        PromptManagerText: PROMPT_MANAGER_WIDGETS,
        PromptManagerFocused: PROMPT_MANAGER_WIDGETS,
        PromptManagerSplit: PROMPT_MANAGER_WIDGETS,
        PromptManagerPair: ["text", "negative_text", "category", "tags", "search_text", "prepend_text", "append_text"],
        CheckpointLoader: ["config_name", "ckpt_name"],
        UNETLoader: ["unet_name", "weight_dtype"],
    };
    const PASS_THROUGH_TYPES = new Set(["Reroute", "GetNode", "SetNode"]);
    const MODE_MUTED = 2;
    const MODE_BYPASSED = 4;

    function widgetNames(n) {
        const fromInputs = (n.inputs || []).filter((i) => i && i.widget && i.widget.name).map((i) => i.widget.name);
        if (fromInputs.length) {
            // Socket inputs that are not widget inputs (#343: the editors' text) would shift the positional values.
            const known = typeof n.type === "string" && Object.prototype.hasOwnProperty.call(WIDGET_POSITIONS, n.type) ? WIDGET_POSITIONS[n.type] : null;
            return known && fromInputs.some((name, index) => known[index] !== name) ? known : fromInputs;
        }
        if (WIDGET_POSITIONS[n.type]) return WIDGET_POSITIONS[n.type];
        if (/checkpointloader/i.test(n.type || "")) return ["ckpt_name"];
        if (/unet/i.test(n.type || "")) return ["unet_name"];
        return [];
    }

    /** Map a workflow node's widget values to input names, skipping control_after_generate. */
    function namedWidgets(n) {
        const named = n.widgets_values_named;
        if (named && typeof named === "object" && !Array.isArray(named)) return { ...named };
        const values = widgetsOf(n);
        const out = {};
        let v = 0;
        for (const name of widgetNames(n)) {
            if (v >= values.length) break;
            out[name] = values[v++];
            if (/seed$/.test(name) && SEED_CONTROL_VALUES.has(values[v])) v++;
        }
        return out;
    }

    function factWorkflowGraph(workflow) {
        const nodes = workflowNodes(workflow), links = new Map(), setters = new Map();
        for (const link of workflow.links || []) {
            if (Array.isArray(link)) links.set(link[0], { from: link[1], slot: link[2] });
            else links.set(link.id, { from: link.origin_id, slot: link.origin_slot });
        }
        const source = id => {
            const link = links.get(id);
            if (!link || !["string", "number"].includes(typeof link.from) || !Number.isSafeInteger(link.slot) || link.slot < 0) return undefined;
            return [String(link.from), link.slot];
        };
        for (const node of nodes) {
            const name = widgetsOf(node)[0];
            if (node.type === "SetNode" && node.mode !== MODE_MUTED && typeof name === "string") setters.set(name, node.inputs?.length === 1 ? node.inputs[0].link : undefined);
        }
        const graph = Object.create(null);
        for (const node of nodes) {
            if (node.mode === MODE_MUTED) continue;
            let pass = PASS_THROUGH_TYPES.has(node.type), link, kind = "value";
            if (pass) {
                const name = widgetsOf(node)[0];
                if (node.type === "GetNode") link = typeof name === "string" ? setters.get(name) : undefined;
                else if (node.inputs?.length === 1) link = node.inputs[0].link;
            }
            else if (node.mode === MODE_BYPASSED) {
                if (!MODEL_PASSTHROUGHS.has(node.type)) continue;
                const models = (node.inputs || []).filter(input => input.name === "model");
                pass = true; kind = "model"; link = models.length === 1 ? models[0].link : undefined;
            }
            if (pass) {
                // Retain the actual alias hop so a fact's full original ancestry shares one depth/visit set.
                graph[String(node.id)] = { class_type: node.type, inputs: { value: source(link) }, [WORKFLOW_PASSTHROUGH]: kind };
                continue;
            }
            const inputs = namedWidgets(node);
            for (const input of node.inputs || []) {
                if (input.link !== null && input.link !== undefined) inputs[input.name] = source(input.link);
            }
            graph[String(node.id)] = { class_type: node.type, inputs };
        }
        return graph;
    }

    /**
     * Convert a UI workflow ({nodes, links}) into the API graph shape so images
     * without a `prompt` chunk are traced the same way. Set/Get pairs, Reroutes and
     * bypassed nodes are resolved to their real source; muted nodes are dropped.
     */
    function workflowToGraph(workflow, conservative = false) {
        if (conservative) return factWorkflowGraph(workflow);
        const nodes = workflowNodes(workflow).filter((n) => n && n.id !== undefined && n.id !== null);
        const byId = new Map(nodes.map((n) => [String(n.id), n]));
        const links = new Map();
        for (const l of Array.isArray(workflow && workflow.links) ? workflow.links : []) {
            if (Array.isArray(l)) links.set(l[0], { from: l[1], slot: l[2], type: l[5] });
            else if (l && typeof l === "object") links.set(l.id, { from: l.origin_id, slot: l.origin_slot, type: l.type });
        }
        const setters = new Map();
        for (const n of nodes) {
            if (n.type === "SetNode") setters.set(widgetsOf(n)[0], n.inputs && n.inputs[0] && n.inputs[0].link);
        }

        const source = (linkId, hops = 0) => {
            const link = links.get(linkId);
            const node = link && byId.get(String(link.from));
            if (!node || node.mode === MODE_MUTED || hops > MAX_VISITS) return undefined;
            const next = (id) => source(id, hops + 1);
            if (node.type === "Reroute") return next(node.inputs && node.inputs[0] && node.inputs[0].link);
            if (node.type === "GetNode") return next(setters.get(widgetsOf(node)[0]));
            if (node.mode === MODE_BYPASSED) {
                const same = (node.inputs || []).find(
                    (i) => i && i.link !== null && i.link !== undefined && (!link.type || link.type === "*" || i.type === link.type),
                );
                return same ? next(same.link) : undefined;
            }
            return [String(node.id), link.slot];
        };

        const graph = {};
        for (const n of nodes) {
            if (n.mode === MODE_MUTED || n.mode === MODE_BYPASSED || PASS_THROUGH_TYPES.has(n.type)) continue;
            const inputs = namedWidgets(n);
            for (const i of n.inputs || []) {
                const src = i && i.link !== null && i.link !== undefined ? source(i.link) : undefined;
                if (src) inputs[i.name] = src;
            }
            graph[String(n.id)] = { class_type: n.type, inputs };
        }
        return graph;
    }

    const hasValue = (v) => v !== undefined && v !== null && v !== "";

    /**
     * @param {{prompt?: Object, workflow?: Object}} comfyData - Parsed PNG metadata
     * @returns {{positivePrompt, negativePrompt, checkpoint, steps, cfgScale, sampler, seed}}
     */
    function extractGenerationParams(comfyData) {
        const data = comfyData || {};
        const primary =
            data.prompt && typeof data.prompt === "object" ? fromPromptGraph(data.prompt, data.workflow) : {};
        const fallback = data.workflow ? fromPromptGraph(workflowToGraph(data.workflow), null) : {};
        const merged = {};
        for (const key of Object.keys(PLACEHOLDERS)) {
            const value = hasValue(primary[key]) ? primary[key] : fallback[key];
            merged[key] = hasValue(value) ? value : PLACEHOLDERS[key];
        }
        return merged;
    }

    /**
     * Where `extractGenerationParams` reads its negative from (#249): the graph it used
     * (the API graph, else the workflow converted the same way) and the read sampler's
     * `negative` link, or null when it found no negative. It changes no reading.
     */
    function legacyNegativeSource(comfyData) {
        const data = comfyData || {};
        const sources = [];
        if (data.prompt && typeof data.prompt === "object") sources.push([data.prompt, data.workflow]);
        if (data.workflow) sources.push([workflowToGraph(data.workflow), null]);
        for (const [graph, workflow] of sources) {
            const ctx = createContext(graph, workflow);
            const { sampler } = selectSampler(ctx, Object.values(graph));
            if (sampler && hasValue(traceConditioning(ctx, sampler.inputs.negative, "negative"))) {
                return { graph, link: sampler.inputs.negative };
            }
        }
        return null;
    }

    const FACT_FIELDS = ["seed", "steps", "cfg", "sampler", "scheduler", "model"];
    const WORKFLOW_PASSTHROUGH = Symbol("inspection workflow passthrough");
    // These tables must equal inspection-semantics.json: SAMPLERS its `samplers` section
    // (facts shape, and a detailer stage's image input), the other four its `facts`
    // section (tests/js/inspection-semantics-sync.test.js). They stay inline literals
    // because the legacy pages load this file as a classic script.
    const SAMPLERS = Object.freeze({
        KSampler: { facts: "ksampler" },
        KSamplerAdvanced: { facts: "ksampler" },
        SamplerCustom: { facts: "custom" },
        SamplerCustomAdvanced: { facts: "custom-advanced" },
        FaceDetailer: { facts: "ksampler", image_stage: "image" },
        DetailerForEach: { facts: "ksampler", image_stage: "image" },
        UltimateSDUpscale: { facts: "ksampler", image_stage: "image" },
        UltimateSDUpscaleNoUpscale: { facts: "ksampler", image_stage: "upscaled_image" },
        // `fact_overrides`: inputs that, when set, replace the settings read here.
        ClownsharKSampler_Beta: { facts: "ksampler", fact_overrides: ["options", "sigmas"] },
        "KSampler (Efficient)": { facts: "ksampler", fact_overrides: ["script"] },
        "ttN KSampler_v2": { facts: "ksampler", fact_overrides: ["adv_xyPlot"] },
    });
    // Stages merged for one result: the resolver's candidate cap. Together with
    // MAX_VISITS per candidate walk these are deliberately tighter than the server's
    // budgets (2,000 visits, depth 64), so the JS walk always fails closed first.
    const MAX_STAGES = 32;
    // Every class the semantics table lists (the resolver's `all_classes`): a stage walk
    // passes these, and treats any other node shaped like a sampler as an unknown stage.
    const TABLE_CLASSES = new Set([
        "KSampler", "KSamplerAdvanced", "SamplerCustom", "SamplerCustomAdvanced", "FaceDetailer",
        "CFGGuider", "BasicGuider", "easy ifElse", "ConditioningCombine", "ConditioningConcat",
        "ConditioningAverage", "SaveImage", "SaveAnimatedWEBP", "SaveAnimatedPNG", "SaveVideo",
        "SaveWEBM", "VHS_VideoCombine", "FluxGuidance", "ReferenceLatent", "ConditioningSetArea",
        "ConditioningSetAreaPercentage", "ConditioningSetAreaStrength", "ConditioningSetMask", "ConditioningSetTimestepRange", "ControlNetApply",
        "ControlNetApplyAdvanced", "ControlNetApplySD3", "InpaintModelConditioning", "WanImageToVideo", "ConditioningZeroOut",
        "CLIPTextEncode", "CLIPTextEncodeSDXL", "CLIPTextEncodeFlux", "CLIPTextEncodeSD3", "TextEncodeQwenImageEdit",
        "TextEncodeQwenImageEditPlus", "TextEncodeQwenImage21", "KikoTextEncode", "StringConstant", "StringConstantMultiline",
        "JoinStringMulti", "PromptSearchList", "TriggerWord Toggle (LoraManager)", "PreviewImage", "PreviewAny", "SaveImageWebsocket",
        "Image Comparer (rgthree)", "DetailerForEach", "UltimateSDUpscale", "UltimateSDUpscaleNoUpscale",
        "ControlNetInpaintingAliMamaApply", "LTXVConditioning", "FluxKontextMultiReferenceLatentMethod", "Text to Conditioning",
        "TextInput", "DisplayText", "ClownsharKSampler_Beta", "KSampler (Efficient)", "ttN KSampler_v2",
        "SeedVarianceEnhancer", "ApplyInstantIDAdvanced", "Switch conditioning [Crystools]", "smZ CLIPTextEncode", "ttN text",
        "CR Text", "CR Prompt Text", "TextBoxMira",
    ]);
    // The table's `stage_inputs` (the resolver reads the same lists): inputs that make an
    // unlisted node a possible stage, and those that do so beside a linked `model`.
    const STAGE_INPUTS = Object.freeze({
        shaped: ["latent_image", "guider", "positive", "negative", "pipe", "basic_pipe", "detailer_pipe", "sampler", "sigmas", "noise", "text_embeds", "hyvid_embeds"],
        with_model: ["conditioning", "neg_conditioning", "samples", "latent"],
    });
    // Fixed output0 contracts from the pinned core node schemas, not input-name inference.
    const SCALAR_OUTPUTS = Object.freeze({
        PrimitiveInt: "number", PrimitiveFloat: "number",
        PrimitiveString: "string", PrimitiveStringMultiline: "string",
    });
    const MODEL_OUTPUTS = Object.freeze({
        CheckpointLoader: "ckpt_name", CheckpointLoaderSimple: "ckpt_name",
        unCLIPCheckpointLoader: "ckpt_name", UNETLoader: "unet_name",
    });
    const MODEL_PASSTHROUGHS = new Set([
        "LoraLoader", "LoraLoaderModelOnly", "ModelSamplingFlux", "ModelSamplingAuraFlow", "ModelSamplingSD3",
        "Lora Loader (LoraManager)", "Power Lora Loader (rgthree)", "ModelPatchTorchSettings", "CFGNorm", "PerturbedAttentionGuidance",
        "CR Apply LoRA Stack",
    ]);
    const CONFIGURATION_OUTPUTS = Object.freeze({
        RandomNoise: { kind: "noise", fields: ["noise_seed"] },
        KSamplerSelect: { kind: "sampler", fields: ["sampler_name"] },
        BasicScheduler: { kind: "sigmas", fields: ["steps", "scheduler"] },
        // D8: no scheduler input; the node type is the scheduler configuration.
        Flux2Scheduler: { kind: "sigmas", fields: ["steps"], type_fields: ["scheduler"] },
        CFGGuider: { kind: "guider", fields: ["cfg", "model"] },
        BasicGuider: { kind: "guider", fields: ["model"] },
    });
    const object = value => value !== null && typeof value === "object" && !Array.isArray(value);

    function fact(status, source = null, samplerId = null, value = null, reason = null) {
        return { status, value, source, evidence: source === "embedded-parameters" ? source : source ? "configuration" : null, reason, sampler_id: samplerId };
    }

    function factSet(status, source = null, reason = null) {
        return Object.fromEntries(FACT_FIELDS.map(name => [name, fact(status, source, null, null, reason)]));
    }

    function exactFactValue(value, field) {
        if (typeof value === "number") {
            // The projection carries decimals as strings; a float here cannot prove exactness.
            if (!["seed", "steps", "cfg"].includes(field) || !Number.isFinite(value) || !Number.isSafeInteger(value)) return null;
            value = String(value);
        }
        if (typeof value !== "string" || !value.trim()) return null;
        if (field === "seed" || field === "steps") {
            return /^\d+$/.test(value) && !/[\r\n]/.test(value) && (field === "seed" || /[1-9]/.test(value)) ? value : null;
        }
        if (field === "cfg") return /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(value) && value.trim() === value ? value : null;
        return value === "Unknown" ? null : value;
    }

    function scalarFact(ctx, inputs, key, field, source, samplerId, seen = new Set()) {
        if (!Object.hasOwn(inputs, key)) return fact("not-recorded", source, samplerId);
        const value = exactFactValue(resolveScalar(ctx, inputs[key], key, seen), field);
        return fact(value === null ? "unresolved" : "known", source, samplerId, value);
    }

    function configurationOutput(ctx, link, kind, seen) {
        const [id, node] = enter(ctx, link, seen);
        if (node?.[WORKFLOW_PASSTHROUGH]) return node[WORKFLOW_PASSTHROUGH] === "value" && link[1] === 0 ? configurationOutput(ctx, node.inputs.value, kind, seen) : [];
        const adapter = node && CONFIGURATION_OUTPUTS[node.class_type];
        return node && link[1] === 0 && adapter?.kind === kind ? [id, node, adapter] : [];
    }

    function providerFact(ctx, link, kind, keys, field, source, samplerId) {
        const seen = new Set();
        const [, node, adapter] = configurationOutput(ctx, link, kind, seen);
        if (!node) return fact("unresolved", source, samplerId);
        if (keys.some(name => adapter.type_fields?.includes(name))) {
            const value = exactFactValue(node.class_type, field);
            return fact(value === null ? "unresolved" : "known", source, samplerId, value);
        }
        const key = keys.find(name => adapter.fields.includes(name) && Object.hasOwn(node.inputs, name));
        return key === undefined ? fact("not-recorded", source, samplerId) : scalarFact(ctx, node.inputs, key, field, source, samplerId, seen);
    }

    const samplerEntry = (graph, id) => (Object.hasOwn(graph, id) && Object.hasOwn(SAMPLERS, graph[id].class_type) ? SAMPLERS[graph[id].class_type] : undefined);

    // The resolver's `_link`: an integer or string id and a non-negative integer slot.
    // Stage walks use it so they cross exactly the server's edges; the legacy `isLink` stays.
    const stageLink = value => Array.isArray(value) && value.length === 2 &&
        (typeof value[0] === "string" || Number.isInteger(value[0])) && Number.isInteger(value[1]) && value[1] >= 0;
    const linked = (inputs, key) => Object.hasOwn(inputs, key) && stageLink(inputs[key]);
    /** An unlisted node that may itself be a sampler stage: the walk cannot judge it. */
    const stageShaped = ({ class_type, inputs }) => !TABLE_CLASSES.has(class_type) &&
        (STAGE_INPUTS.shaped.some(key => linked(inputs, key)) || (linked(inputs, "model") && STAGE_INPUTS.with_model.some(key => linked(inputs, key))));

    /** Samplers reached from a detailer's image input, breadth-first, stopping at listed samplers. */
    function upstreamStages(graph, origin, value, budget) {
        if (!stageLink(value)) return null;
        const found = [], queue = [String(value[0])], queued = new Set(queue);
        for (let head = 0; head < queue.length; head++) {
            const id = queue[head];
            if (id === origin || ++budget.visits > MAX_VISITS || !Object.hasOwn(graph, id)) return null;
            if (samplerEntry(graph, id)) { found.push(id); continue; }
            if (stageShaped(graph[id])) return null;
            for (const input of Object.values(graph[id].inputs)) {
                const next = stageLink(input) ? String(input[0]) : null;
                if (next !== null && !queued.has(next)) { queued.add(next); queue.push(next); }
            }
        }
        return found;
    }

    /**
     * Collect a final stage and every stage in its image ancestry into `stages`, as the
     * resolver judges a detailer chain. `later` holds the detailers already being judged
     * below this one, so a cyclic chain fails instead of recursing. False when the chain
     * cannot be walked within the bounds.
     */
    // `stages` is shared on purpose across one result's candidates: re-adding a stage is
    // harmless, and MAX_STAGES caps the union, not each candidate.
    function collectStages(graph, id, later, stages, budget) {
        stages.add(id);
        if (stages.size > MAX_STAGES || later.size >= MAX_STAGES) return false;
        const entry = samplerEntry(graph, id);
        if (entry.image_stage === undefined) return true;
        const inputs = graph[id].inputs;
        const upstream = upstreamStages(graph, id, Object.hasOwn(inputs, entry.image_stage) ? inputs[entry.image_stage] : undefined, budget);
        if (upstream === null || upstream.some(stage => later.has(stage))) return false;
        const below = new Set(later).add(id);
        return upstream.every(stage => collectStages(graph, stage, below, stages, budget));
    }

    /** Spec section 10 merge: equal known values stay known, differing ones are ambiguous. */
    function mergeFacts(sets, source) {
        if (sets.length === 1) return sets[0];
        return Object.fromEntries(FACT_FIELDS.map(field => {
            const facts = sets.map(set => set[field]);
            if (new Set(facts.filter(item => item.status === "known").map(item => item.value)).size > 1) return [field, fact("ambiguous", source)];
            const [first] = facts;
            const agreed = facts.every(item => item.status === first.status && item.value === first.value);
            // A merged fact names no single sampler: it is every stage's value.
            return [field, agreed ? fact(first.status, source, null, first.value, first.reason) : fact("unresolved", source)];
        }));
    }

    function samplerFacts(ctx, samplerId, source) {
        const sampler = ctx.graph[samplerId];
        const inputs = sampler.inputs;
        const { facts: shape, fact_overrides: overrides = [] } = SAMPLERS[sampler.class_type];
        // A set override (a script, an XY plot, custom sigmas) replaces the settings below.
        if (overrides.some(name => Object.hasOwn(inputs, name) && inputs[name] !== null && inputs[name] !== "")) {
            return Object.fromEntries(FACT_FIELDS.map(name => [name, fact("unresolved", source, samplerId)]));
        }
        const advanced = shape === "custom-advanced";
        const custom = advanced || shape === "custom";
        let settings = inputs;
        let settingsAdapter;
        const settingsPath = new Set();
        if (advanced) {
            const [, guider, adapter] = configurationOutput(ctx, inputs.guider, "guider", settingsPath);
            settings = guider ? guider.inputs : null;
            settingsAdapter = adapter;
        }
        const fromSettings = (key, field) => {
            if (!settings) return fact("unresolved", source, samplerId);
            if (settingsAdapter && !settingsAdapter.fields.includes(key)) return fact(Object.hasOwn(settings, key) ? "unresolved" : "not-recorded", source, samplerId);
            return scalarFact(ctx, settings, key, field, source, samplerId, new Set(settingsPath));
        };
        const seedKey = Object.hasOwn(inputs, "seed") ? "seed" : "noise_seed";
        const facts = {
            seed: advanced ? providerFact(ctx, inputs.noise, "noise", ["noise_seed"], "seed", source, samplerId) : scalarFact(ctx, inputs, seedKey, "seed", source, samplerId),
            steps: custom ? providerFact(ctx, inputs.sigmas, "sigmas", ["steps"], "steps", source, samplerId) : scalarFact(ctx, inputs, "steps", "steps", source, samplerId),
            cfg: fromSettings("cfg", "cfg"),
            sampler: custom ? providerFact(ctx, inputs.sampler, "sampler", ["sampler_name"], "sampler", source, samplerId) : scalarFact(ctx, inputs, "sampler_name", "sampler", source, samplerId),
            scheduler: Object.hasOwn(inputs, "scheduler") || !custom ? scalarFact(ctx, inputs, "scheduler", "scheduler", source, samplerId) : providerFact(ctx, inputs.sigmas, "sigmas", ["scheduler"], "scheduler", source, samplerId),
            model: fact(settings && !Object.hasOwn(settings, "model") ? "not-recorded" : "unresolved", source, samplerId),
        };
        if (settings && Object.hasOwn(settings, "model")) {
            const model = exactFactValue(traceModelName(ctx, settings.model, new Set(settingsPath)), "model");
            facts.model = fact(model === null ? "unresolved" : "known", source, samplerId, model);
        }
        return facts;
    }

    /**
     * Facts follow the server's resolution. Its `sampler_id` drives them alone when
     * resolved. Any other result naming a sampler (an ambiguous multistage chain, or an
     * absent or unresolved prompt of the branch the server chose, e.g. after dropping a
     * preview-only stage) merges per field across that sampler's stages, independent of
     * any selection (D5). Ambiguous without a named sampler (no candidates on the wire
     * until PR3), and unresolved or absent over several listed samplers, merge the same
     * way across every listed sampler and its stages. Otherwise (no resolution, or one
     * listed sampler) exactly one listed sampler supplies them; several leave every field ambiguous.
     * A resolved pair without a named sampler (identical branches, #249) merges like
     * ambiguous when several samplers are listed.
     */
    function graphFacts(graph, source, resolution) {
        if (!object(graph)) return factSet("unresolved", source);
        // Graph size is not a bound: each lookup is bounded by its MAX_VISITS seen-set,
        // and the server's node ceiling arrives as a `limited` projection.
        const entries = Object.entries(graph);
        if (entries.some(([, node]) => !object(node) || !object(node.inputs) || typeof node.class_type !== "string")) return factSet("unresolved", source);
        const ctx = createContext(graph, null, true);
        const state = object(resolution) ? resolution.state : undefined;
        const named = typeof resolution?.sampler_id === "string" ? resolution.sampler_id : null;
        if (["resolved", "ambiguous", "absent", "unresolved"].includes(state) && named !== null) {
            if (!samplerEntry(graph, named)) return factSet("unresolved", source);
            if (state === "resolved") return samplerFacts(ctx, named, source);
            const stages = new Set();
            if (!collectStages(graph, named, new Set(), stages, { visits: 0 })) return factSet("unresolved", source);
            return mergeFacts([...stages].map(id => samplerFacts(ctx, id, source)), source);
        }
        const candidates = entries.filter(([id]) => samplerEntry(graph, id));
        // Review I-1: an unresolved or absent prompt does not make the run's settings
        // unknown, so several listed samplers merge the same way. So does a pair the
        // server resolved across several branches without naming one (#249).
        if (state === "ambiguous" || (["unresolved", "absent", "resolved"].includes(state) && candidates.length > 1)) {
            // The server's branches: every listed sampler, a detailer judged with its stages.
            const stages = new Set();
            if (!candidates.length || !candidates.every(([id]) => collectStages(graph, id, new Set(), stages, { visits: 0 }))) return factSet("unresolved", source);
            return mergeFacts([...stages].map(id => samplerFacts(ctx, id, source)), source);
        }
        if (candidates.length !== 1) return factSet(candidates.length > 1 ? "ambiguous" : "unresolved", source);
        return samplerFacts(ctx, candidates[0][0], source);
    }

    function validFactWorkflow(workflow) {
        if (!object(workflow) || !Array.isArray(workflow.nodes) || (workflow.links !== undefined && !Array.isArray(workflow.links))) return false;
        const ids = new Set(), links = new Set(), setters = new Set();
        for (const node of workflow.nodes) {
            if (!object(node) || !["string", "number"].includes(typeof node.id) || typeof node.type !== "string" ||
                (node.inputs !== undefined && (!Array.isArray(node.inputs) || node.inputs.some(input => !object(input) || typeof input.name !== "string"))) || ids.has(String(node.id))) return false;
            ids.add(String(node.id));
            if (node.type === "SetNode") {
                const name = widgetsOf(node)[0];
                if (setters.has(name)) return false;
                setters.add(name);
            }
        }
        for (const link of workflow.links || []) {
            const id = Array.isArray(link) ? link[0] : link?.id;
            if (id === undefined || links.has(id)) return false;
            links.add(id);
        }
        return true;
    }

    // The inspection resolver's `_footer` (Python): R5 also reads a bare `Steps: N`
    // footer, and sd-dynamic-prompts' one-line template records after it.
    const FOOTER_TRAILERS = ["Template:", "Negative Template:"];
    function trailerLines(text) {
        const lines = text.trim().split(/\r?\n|\r/).filter((line, index, all) => all.length > 1 || line);
        const order = lines.map(line => FOOTER_TRAILERS.findIndex(name => line.startsWith(name)));
        return !order.includes(-1) && order.every((value, index) => index === 0 || value > order[index - 1]);
    }
    function settingsFooter(parameters) {
        let matches = [...parameters.matchAll(/^Steps:\s*\d+\s*,[^\n]*$/gm)];
        if (!matches.length) matches = [...parameters.matchAll(/^Steps:\s*\d+\s*$/gm)];
        if (matches.length !== 1) return null;
        return trailerLines(parameters.slice(matches[0].index + matches[0][0].length)) ? matches[0] : null;
    }
    /** R10: split settings on commas outside A1111's quoted values; unbalanced quotes split as before. */
    function settingsParts(line) {
        const parts = [];
        let start = 0, quoted = false;
        for (let index = 0; index < line.length; index++) {
            if (quoted && line[index] === "\\") index++;
            else if (line[index] === '"') quoted = !quoted;
            else if (line[index] === "," && !quoted) { parts.push(line.slice(start, index)); start = index + 1; }
        }
        if (quoted) return line.split(",");
        parts.push(line.slice(start));
        return parts;
    }
    function settingValue(value) {
        if (!/^"(?:\\.|[^\\"])*"$/.test(value)) return value;
        try { return JSON.parse(value); } catch { return value; }
    }

    function parameterFacts(parameters) {
        const source = "embedded-parameters";
        const footer = settingsFooter(parameters);
        if (!footer) return factSet("unresolved", source);
        // R4: like the resolver, text starting with the delimiter has an empty positive.
        const body = "\n" + parameters.slice(0, footer.index).replace(/[\r\n]+$/, "");
        if (!body.split("\nNegative prompt:")[0].trim() || body.split("\nNegative prompt:").length > 2) return factSet("unresolved", source);
        const settings = new Map();
        for (const part of settingsParts(footer[0])) {
            const match = part.match(/^\s*([^:]+):\s*(.*?)\s*$/);
            if (!match) return factSet("unresolved", source);
            const [, key, value] = match;
            settings.set(key, settings.has(key) ? null : settingValue(value));
        }
        const keys = { seed: "Seed", steps: "Steps", cfg: "CFG scale", sampler: "Sampler", scheduler: "Schedule type", model: "Model" };
        return Object.fromEntries(FACT_FIELDS.map(field => {
            const key = keys[field];
            if (!settings.has(key)) return [field, fact("not-recorded", source)];
            let value = exactFactValue(settings.get(key), field);
            if (field === "scheduler" && value !== null && !/^(automatic|normal|karras|exponential|sgm[ _]uniform|simple|ddim[ _]uniform|beta|uniform|linear[ _]quadratic|kl[ _]optimal)$/i.test(value)) value = null;
            return [field, fact(value === null ? "unresolved" : "known", source, null, value)];
        }));
    }

    /** Consume only the exact, bounded inspection projection; never reparse raw copies. */
    function extractGenerationFacts({ prompt_graph, workflow_graph, parameters, recordFormats = {}, metadataStatus, resolution } = {}) {
        for (const [record, source] of [[prompt_graph, "api-graph"], [workflow_graph, "workflow"]]) {
            if (!record || record.status === "absent") continue;
            if (record.status === "limited") return factSet("unresolved", source, "projection-limited");
            if (record.status !== "parsed") return factSet("unresolved", source);
            if (source === "workflow") {
                if (!validFactWorkflow(record.value)) return factSet("unresolved", source);
                return graphFacts(workflowToGraph(record.value, true), source, null);
            }
            return graphFacts(record.value, source, resolution);
        }
        if (metadataStatus === "limited") return factSet("unresolved", null, "projection-limited");
        if (metadataStatus === "unreadable") return factSet("unresolved", null, "metadata-unreadable");
        if (parameters !== undefined && parameters !== null || Object.hasOwn(recordFormats, "parameters")) {
            return recordFormats.parameters === "original-text" && typeof parameters === "string" ? parameterFacts(parameters) : factSet("unresolved");
        }
        return factSet(metadataStatus === "available" || metadataStatus === "absent" ? "not-recorded" : "unresolved");
    }

    const api = { extractGenerationParams, extractGenerationFacts, legacyNegativeSource, PLACEHOLDERS };
    // For the semantics sync test only; not part of the legacy page contract.
    Object.defineProperty(api, "_factsTables", {
        value: Object.freeze({
            configuration_outputs: CONFIGURATION_OUTPUTS,
            model_outputs: MODEL_OUTPUTS,
            model_passthroughs: MODEL_PASSTHROUGHS,
            scalar_outputs: SCALAR_OUTPUTS,
        }),
    });
    Object.defineProperty(api, "_samplerTable", { value: SAMPLERS });
    Object.defineProperty(api, "_tableClasses", { value: TABLE_CLASSES });
    Object.defineProperty(api, "_stageInputs", { value: STAGE_INPUTS });
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    else root.ComfyMetadata = api;
})(typeof window !== "undefined" ? window : globalThis);
