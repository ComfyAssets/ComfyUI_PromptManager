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

    const MAX_DEPTH = 25;
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
     * Tracing context: the API graph plus the text widget of each PromptManager
     * node in the UI workflow, keyed by node id.
     *
     * Images saved before 3.2.3 had every PromptManager node's `text` overwritten
     * with the last-run node's combined prompt; the workflow keeps the real value.
     */
    function createContext(graph, workflow) {
        const widgetText = new Map();
        for (const n of workflowNodes(workflow)) {
            const text = widgetsOf(n)[0];
            if (/promptmanager/i.test(n.type || "") && typeof text === "string") {
                widgetText.set(String(n.id), text);
            }
        }
        return { graph, widgetText };
    }

    /** Returns [nodeId, node] for a link, or [] when it does not resolve. */
    function follow(ctx, link) {
        if (!isLink(link)) return [];
        const id = String(link[0]);
        const node = ctx.graph[id];
        return node && node.inputs ? [id, node] : [];
    }

    /** Resolve a scalar input, following a link to a primitive node if needed. */
    function resolveScalar(ctx, value, depth = 0) {
        if (!isLink(value)) return value;
        const [, node] = follow(ctx, value);
        if (!node || depth > MAX_DEPTH) return undefined;
        for (const v of Object.values(node.inputs)) {
            const resolved = resolveScalar(ctx, v, depth + 1);
            if (resolved !== undefined && typeof resolved !== "object") return resolved;
        }
        return undefined;
    }

    /** Read the first of `keys` from the node a link points to, resolving further links. */
    function resolveLinkedInput(ctx, link, keys) {
        const [, node] = follow(ctx, link);
        if (!node) return undefined;
        const key = keys.find((k) => k in node.inputs);
        return key === undefined ? undefined : resolveScalar(ctx, node.inputs[key]);
    }

    /** Resolve a string input; text-producing nodes may be chained (concat, primitives). */
    function resolveString(ctx, value, depth = 0) {
        if (typeof value === "string") return value;
        const [id, node] = follow(ctx, value);
        if (!node || depth > MAX_DEPTH) return "";
        if ("text" in node.inputs) return composeNodeText(ctx, id, node, depth + 1);
        const delimiter = typeof node.inputs.delimiter === "string" ? node.inputs.delimiter : " ";
        return Object.entries(node.inputs)
            .filter(([key]) => TEXT_KEYS.test(key))
            .map(([, v]) => resolveString(ctx, v, depth + 1))
            .filter((s) => s.trim())
            .join(delimiter);
    }

    /** Text of an encoder node, matching PromptManager's prepend + text + append join. */
    function composeNodeText(ctx, id, node, depth) {
        const { inputs } = node;
        const raw = ctx.widgetText.has(id) ? ctx.widgetText.get(id) : resolveString(ctx, inputs.text, depth);
        const text = raw.trim();
        if (!text) return "";
        const prepend = resolveString(ctx, inputs.prepend_text, depth).trim();
        const append = resolveString(ctx, inputs.append_text, depth).trim();
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
    function traceConditioning(ctx, link, role, depth = 0) {
        const [id, node] = follow(ctx, link);
        if (!node || depth > MAX_DEPTH) return "";
        if ("text" in node.inputs) return composeNodeText(ctx, id, node, depth + 1);
        // Pass-through nodes (ControlNet apply, conditioning combine/set area, ...)
        const { inputs } = node;
        const next = inputs[role] || inputs.conditioning || inputs.conditioning_to || inputs.conditioning_1;
        return traceConditioning(ctx, next, role, depth + 1);
    }

    /** Follow the sampler's model link upstream (through LoRA loaders etc.) to a loader. */
    function traceModelName(ctx, link, depth = 0) {
        const [, node] = follow(ctx, link);
        if (!node || depth > MAX_DEPTH) return undefined;
        const key = MODEL_NAME_KEYS.find((k) => typeof node.inputs[k] === "string");
        return key ? node.inputs[key] : traceModelName(ctx, node.inputs.model, depth + 1);
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
        const [, guider] = custom ? follow(ctx, custom.inputs.guider) : [];
        const sampler = isSampler(guider) ? guider : nodes.find(isSampler);
        if (!sampler) return { checkpoint: findAnyModelName(nodes) };

        const { inputs } = sampler;
        const fromCustom = (link, keys) => (custom ? resolveLinkedInput(ctx, link, keys) : undefined);
        return {
            positivePrompt: traceConditioning(ctx, inputs.positive, "positive"),
            negativePrompt: traceConditioning(ctx, inputs.negative, "negative"),
            checkpoint: traceModelName(ctx, inputs.model) || findAnyModelName(nodes),
            seed:
                resolveScalar(ctx, inputs.seed ?? inputs.noise_seed) ??
                fromCustom(custom && custom.inputs.noise, ["noise_seed", "seed"]),
            steps: resolveScalar(ctx, inputs.steps) ?? fromCustom(custom && custom.inputs.sigmas, ["steps"]),
            cfgScale: resolveScalar(ctx, inputs.cfg),
            sampler:
                resolveScalar(ctx, inputs.sampler_name) ??
                fromCustom(custom && custom.inputs.sampler, ["sampler_name"]),
        };
    }

    /** Best effort for images that only carry the UI workflow (no API graph). */
    function fromWorkflow(workflow) {
        const nodes = workflowNodes(workflow);
        const result = {};

        const loader = nodes.find((n) => /checkpointloader/i.test(n.type || ""));
        if (loader && typeof widgetsOf(loader)[0] === "string") result.checkpoint = widgetsOf(loader)[0];

        const sampler = nodes.find((n) => n.type === "KSampler");
        if (sampler) {
            const w = widgetsOf(sampler);
            // The seed widget is followed by a hidden control_after_generate value
            const shift = SEED_CONTROL_VALUES.has(w[1]) ? 1 : 0;
            result.seed = w[0];
            result.steps = w[1 + shift];
            result.cfgScale = w[2 + shift];
            result.sampler = w[3 + shift];
        }
        return result;
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
        const fallback = fromWorkflow(data.workflow);
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
