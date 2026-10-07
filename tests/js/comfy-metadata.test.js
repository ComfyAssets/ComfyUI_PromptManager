// Run with: node --test tests/js/
const test = require("node:test");
const assert = require("node:assert/strict");
const { extractGenerationParams } = require("../../web/js/comfy-metadata.js");

// Same shape as the workflow attached to #75: PromptManager nodes are saved as
// CLIPTextEncode with extra inputs, and the checkpoint loader is pysssss'.
const issue75Prompt = {
    1: {
        class_type: "KSampler",
        inputs: {
            seed: 480655185510613, steps: 25, cfg: 5.5,
            sampler_name: "euler_ancestral", scheduler: "normal", denoise: 0.8,
            model: ["20", 0], positive: ["39", 0], negative: ["40", 0], latent_image: ["36", 0],
        },
    },
    8: { class_type: "CLIPSetLastLayer", inputs: { stop_at_clip_layer: -2, clip: ["20", 1] } },
    20: {
        class_type: "CheckpointLoader|pysssss",
        inputs: { ckpt_name: "Anime\\Model-XL.safetensors", prompt: "[none]", example: "[none]" },
    },
    39: {
        class_type: "CLIPTextEncode",
        inputs: {
            text: "1girl, cat_ears, smile", category: "", tags: "", search_text: "",
            prepend_text: "masterpiece, best quality, ", append_text: "", clip: ["8", 0],
        },
    },
    40: {
        class_type: "CLIPTextEncode",
        inputs: {
            text: "3d, crowd", category: "", tags: "", search_text: "",
            prepend_text: "bad quality, worst quality, ", append_text: "", clip: ["8", 0],
        },
    },
};

test("#75: traces PromptManager prompts and pysssss checkpoint from the sampler", () => {
    const r = extractGenerationParams({ prompt: issue75Prompt });
    assert.equal(r.positivePrompt, "masterpiece, best quality, 1girl, cat_ears, smile");
    assert.equal(r.negativePrompt, "bad quality, worst quality, 3d, crowd");
    assert.equal(r.checkpoint, "Anime\\Model-XL.safetensors");
    assert.deepEqual(
        [r.seed, r.steps, r.cfgScale, r.sampler],
        [480655185510613, 25, 5.5, "euler_ancestral"],
    );
});

test("#75: workflow blob does not override values from the prompt graph", () => {
    const workflow = { nodes: [{ type: "KSampler", widgets_values: [1, "randomize", 99, 7, "ddim"] }] };
    const r = extractGenerationParams({ prompt: issue75Prompt, workflow });
    assert.equal(r.steps, 25);
    assert.equal(r.cfgScale, 5.5);
});

test("#75: recovers per-node text when SaveImage patch overwrote every PromptManager node", () => {
    // Pre-fix images: both nodes carry the final positive text in the prompt graph,
    // but the workflow graph still has each node's real widget values.
    const positiveFinal = "masterpiece, best quality, 1girl, smile";
    const prompt = {
        1: { class_type: "KSampler", inputs: { seed: 1, steps: 20, cfg: 7, sampler_name: "euler", positive: ["39", 0], negative: ["40", 0], model: ["20", 0] } },
        20: { class_type: "CheckpointLoaderSimple", inputs: { ckpt_name: "m.safetensors" } },
        39: { class_type: "PromptManager", inputs: { text: positiveFinal, prepend_text: "masterpiece, best quality,", append_text: "" } },
        40: { class_type: "PromptManager", inputs: { text: positiveFinal, prepend_text: "bad quality,", append_text: "" } },
    };
    const workflow = {
        nodes: [
            { id: 39, type: "PromptManager", widgets_values: ["1girl, smile", "", "", "", "masterpiece, best quality,", ""] },
            { id: 40, type: "PromptManager", widgets_values: ["3d, crowd", "", "", "", "bad quality,", ""] },
        ],
    };
    const r = extractGenerationParams({ prompt, workflow });
    assert.equal(r.positivePrompt, "masterpiece, best quality, 1girl, smile");
    assert.equal(r.negativePrompt, "bad quality, 3d, crowd");
});

test("prepend/append are not doubled when text is already the combined prompt", () => {
    const prompt = {
        1: { class_type: "KSampler", inputs: { positive: ["2", 0], negative: ["3", 0], model: ["4", 0] } },
        2: { class_type: "PromptManager", inputs: { text: "best quality, a cat, 8k", prepend_text: "best quality,", append_text: "8k" } },
        3: { class_type: "CLIPTextEncode", inputs: { text: "blurry" } },
    };
    assert.equal(extractGenerationParams({ prompt }).positivePrompt, "best quality, a cat, 8k");
});

test("linked prepend/append inputs are resolved (no longer baked into text since 3.2.3)", () => {
    const prompt = {
        1: { class_type: "KSampler", inputs: { positive: ["2", 0], negative: ["3", 0], model: ["4", 0] } },
        2: { class_type: "PromptManager", inputs: { text: "a fox", prepend_text: ["5", 0], append_text: ["6", 0] } },
        3: { class_type: "CLIPTextEncode", inputs: { text: "blurry" } },
        5: { class_type: "PrimitiveString", inputs: { value: "masterpiece," } },
        6: { class_type: "PrimitiveString", inputs: { value: "8k" } },
    };
    assert.equal(extractGenerationParams({ prompt }).positivePrompt, "masterpiece, a fox 8k");
});

test("negative prompt is identified by link, not by keywords", () => {
    const prompt = {
        3: { class_type: "KSampler", inputs: { seed: 1, steps: 20, cfg: 7, sampler_name: "euler", positive: ["6", 0], negative: ["7", 0], model: ["4", 0] } },
        4: { class_type: "CheckpointLoaderSimple", inputs: { ckpt_name: "sd15.safetensors" } },
        6: { class_type: "CLIPTextEncode", inputs: { text: "embedding:goodstyle, a lighthouse" } },
        7: { class_type: "CLIPTextEncode", inputs: { text: "blurry" } },
    };
    const r = extractGenerationParams({ prompt });
    assert.equal(r.positivePrompt, "embedding:goodstyle, a lighthouse");
    assert.equal(r.negativePrompt, "blurry");
});

test("follows LoRA chains, ControlNet pass-through, concatenated text and primitive inputs", () => {
    const prompt = {
        1: { class_type: "KSamplerAdvanced", inputs: { noise_seed: 42, steps: ["9", 0], cfg: 6, sampler_name: "dpmpp_2m", positive: ["5", 0], negative: ["5", 1], model: ["3", 0] } },
        2: { class_type: "CheckpointLoaderSimple", inputs: { ckpt_name: "base.safetensors" } },
        3: { class_type: "LoraLoader", inputs: { lora_name: "style.safetensors", model: ["2", 0], clip: ["2", 1] } },
        5: { class_type: "ControlNetApplyAdvanced", inputs: { positive: ["6", 0], negative: ["7", 0], strength: 1 } },
        6: { class_type: "CLIPTextEncode", inputs: { text: ["8", 0] } },
        7: { class_type: "CLIPTextEncode", inputs: { text: "lowres" } },
        8: { class_type: "Text Concatenate", inputs: { delimiter: ", ", text_a: "a castle", text_b: "at dusk" } },
        9: { class_type: "PrimitiveInt", inputs: { value: 30 } },
    };
    const r = extractGenerationParams({ prompt });
    assert.equal(r.positivePrompt, "a castle, at dusk");
    assert.equal(r.negativePrompt, "lowres");
    assert.equal(r.checkpoint, "base.safetensors");
    assert.equal(r.seed, 42);
    assert.equal(r.steps, 30);
});

test("custom sampler graphs (Flux: SamplerCustomAdvanced + CFGGuider) resolve every field", () => {
    const prompt = {
        85: { class_type: "UNETLoader", inputs: { unet_name: "flux2-klein.safetensors" } },
        135: { class_type: "Lora Loader (LoraManager)", inputs: { model: ["85", 0], clip: ["90", 0] } },
        149: { class_type: "PromptManager", inputs: { text: "a red fox", prepend_text: ["133", 0], append_text: "" } },
        152: { class_type: "SeedHistory", inputs: { seed: 1725922648 } },
        // ControlNet-style node with positive/negative but no model: must not be taken as the sampler
        200: { class_type: "ControlNetApplyAdvanced", inputs: { positive: ["149", 0], negative: ["274", 0], strength: 1 } },
        265: { class_type: "SamplerCustomAdvanced", inputs: { noise: ["267", 0], guider: ["268", 0], sampler: ["269", 0], sigmas: ["266", 0] } },
        266: { class_type: "Flux2Scheduler", inputs: { steps: 50, width: ["153", 1] } },
        267: { class_type: "RandomNoise", inputs: { noise_seed: ["152", 0] } },
        268: { class_type: "CFGGuider", inputs: { cfg: 5.0, model: ["135", 0], positive: ["149", 0], negative: ["274", 0] } },
        269: { class_type: "KSamplerSelect", inputs: { sampler_name: "uni_pc" } },
        274: { class_type: "CLIPTextEncode", inputs: { text: "watermark" } },
    };
    const r = extractGenerationParams({ prompt });
    assert.deepEqual(
        [r.positivePrompt, r.negativePrompt, r.checkpoint, r.cfgScale, r.seed, r.steps, r.sampler],
        ["a red fox", "watermark", "flux2-klein.safetensors", 5.0, 1725922648, 50, "uni_pc"],
    );
});

test("workflow-only images account for the hidden control_after_generate widget", () => {
    const workflow = {
        nodes: [
            { type: "CheckpointLoader|pysssss", widgets_values: ["model.safetensors", "[none]"] },
            { type: "KSampler", widgets_values: [123, "randomize", 25, 5.5, "euler", "normal", 1] },
        ],
    };
    const r = extractGenerationParams({ workflow });
    assert.deepEqual(
        [r.checkpoint, r.seed, r.steps, r.cfgScale, r.sampler],
        ["model.safetensors", 123, 25, 5.5, "euler"],
    );
});

test("missing or empty metadata yields placeholders", () => {
    const r = extractGenerationParams({});
    assert.equal(r.positivePrompt, "No prompt found");
    assert.equal(r.negativePrompt, "No negative prompt found");
    assert.equal(r.checkpoint, "Unknown");
    assert.equal(extractGenerationParams(null).seed, "Unknown");
});
