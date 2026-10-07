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
    function createContext(graph, workflow) {
        const widgets = new Map();
        for (const n of workflowNodes(workflow)) {
            const text = widgetsOf(n)[0];
            if (/promptmanager/i.test(n.type || "") && typeof text === "string") {
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
        return { graph, widgetText };
    }

    /**
     * Returns [nodeId, node] for a link, or [] when it does not resolve or the node
     * was already expanded in this lookup. The shared `seen` set keeps every lookup
     * linear in graph size, even for cyclic or heavily fanned-out graphs.
     */
    function enter(ctx, link, seen) {
        if (!isLink(link) || seen.size >= MAX_VISITS) return [];
        const id = String(link[0]);
        const node = ctx.graph[id];
        if (!node || !node.inputs || seen.has(id)) return [];
        seen.add(id);
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

    function fromPromptGraph(graph, workflow) {
        const ctx = createContext(graph, workflow);
        const nodes = Object.values(graph);
        const custom = nodes.find(isCustomSampler);
        const [, guider] = custom ? enter(ctx, custom.inputs.guider, new Set()) : [];
        const sampler = isSampler(guider) ? guider : nodes.find(isSampler);
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
        CheckpointLoader: ["config_name", "ckpt_name"],
        UNETLoader: ["unet_name", "weight_dtype"],
    };
    const PASS_THROUGH_TYPES = new Set(["Reroute", "GetNode", "SetNode"]);
    const MODE_MUTED = 2;
    const MODE_BYPASSED = 4;

    function widgetNames(n) {
        const fromInputs = (n.inputs || []).filter((i) => i && i.widget && i.widget.name).map((i) => i.widget.name);
        if (fromInputs.length) return fromInputs;
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

    /**
     * Convert a UI workflow ({nodes, links}) into the API graph shape so images
     * without a `prompt` chunk are traced the same way. Set/Get pairs, Reroutes and
     * bypassed nodes are resolved to their real source; muted nodes are dropped.
     */
    function workflowToGraph(workflow) {
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

    const api = { extractGenerationParams, PLACEHOLDERS };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    else root.ComfyMetadata = api;
})(typeof window !== "undefined" ? window : globalThis);
