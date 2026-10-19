// Run with: node --test tests/js/
// Characterization, not a specification: pins what the legacy viewers
// (web/metadata.html, web/js/gallery.js) show today on the synthetic inspection
// parity fixtures, so the #240 workspace work cannot change them by accident.
// Some answers are known to be wrong (f01b shows the preview-only detailer text
// as both roles; f16 shows the last of two conflicting prompt chunks). They are
// recorded as-is and intentionally unchanged (spec section 11).
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { parsePngTextChunks } = require("../../web/js/png-metadata.js");
const { extractGenerationParams } = require("../../web/js/comfy-metadata.js");

const FIXTURES = path.join(__dirname, "..", "fixtures", "inspection_parity");
const DETAILER = "Detailer pass: refine the face";

// The legacy pages' NaN cleanup and field order (web/metadata.html extractComfyUIData).
function legacyComfyData(metadata) {
    const parse = text => JSON.parse(text.replace(/:\s*NaN\b/g, ": null").replace(/\bNaN\b/g, "null"));
    const first = fields => {
        for (const field of fields) {
            if (!metadata[field]) continue;
            try { return parse(metadata[field]); } catch { /* the pages try the next field */ }
        }
        return null;
    };
    return { workflow: first(["workflow", "Workflow", "comfy", "ComfyUI"]), prompt: first(["prompt", "Prompt", "parameters", "Parameters"]) };
}

function legacyParams(name) {
    const bytes = fs.readFileSync(path.join(FIXTURES, `${name}.png`));
    const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
    return extractGenerationParams(legacyComfyData(parsePngTextChunks(buffer) || {}));
}

const EXPECTED = {
    f01b: { positivePrompt: "Detailer pass: refine the face", negativePrompt: "Detailer pass: refine the face", checkpoint: "flux-2-klein-9b-fp8.safetensors", steps: 20, cfgScale: 1, sampler: "euler", seed: 468212954408038 },
    f02: { positivePrompt: "a red fox in snow, detailed fur", negativePrompt: "blurry, lowres", checkpoint: "sdxl_base.safetensors", steps: 25, cfgScale: 6.5, sampler: "euler", seed: 42 },
    f03: { positivePrompt: "a red fox in snow, detailed fur", negativePrompt: "blurry, lowres", checkpoint: "sdxl_base.safetensors", steps: 25, cfgScale: 6.5, sampler: "euler", seed: 42 },
    f06a: { positivePrompt: "No prompt found", negativePrompt: "blurry, lowres", checkpoint: "sdxl_base.safetensors", steps: 25, cfgScale: 6.5, sampler: "euler", seed: 42 },
    f06b: { positivePrompt: "No prompt found", negativePrompt: "blurry, lowres", checkpoint: "sdxl_base.safetensors", steps: 25, cfgScale: 6.5, sampler: "euler", seed: 42 },
    f06c: { positivePrompt: "a red fox in snow, detailed fur", negativePrompt: "blurry, lowres", checkpoint: "sdxl_base.safetensors", steps: 25, cfgScale: 6.5, sampler: "euler", seed: 42 },
    f06d: { positivePrompt: "a red fox in snow, detailed fur", negativePrompt: "blurry, lowres", checkpoint: "sdxl_base.safetensors", steps: 25, cfgScale: 6.5, sampler: "euler", seed: 42 },
    f06e: { positivePrompt: "a red fox in snow, detailed fur", negativePrompt: "blurry, lowres", checkpoint: "sdxl_base.safetensors", steps: 25, cfgScale: 6.5, sampler: "euler", seed: 42 },
    f07a: { positivePrompt: "a red fox in snow, detailed fur", negativePrompt: "blurry, lowres", checkpoint: "sdxl_base.safetensors", steps: 25, cfgScale: 6.5, sampler: "euler", seed: 42 },
    f07b: { positivePrompt: "a red fox in snow, detailed fur", negativePrompt: "blurry, lowres", checkpoint: "sdxl_base.safetensors", steps: 25, cfgScale: 6.5, sampler: "euler", seed: 42 },
    f08: { positivePrompt: "a red fox in snow, detailed fur, winter", negativePrompt: "blurry, lowres", checkpoint: "sdxl_base.safetensors", steps: 25, cfgScale: 6.5, sampler: "euler", seed: 42 },
    f09: { positivePrompt: "a red fox in snow, detailed fur", negativePrompt: "blurry, lowres", checkpoint: "sdxl_base.safetensors", steps: 25, cfgScale: 6.5, sampler: "euler", seed: 42 },
    f10: { positivePrompt: "a red fox in snow, detailed fur", negativePrompt: "blurry, lowres", checkpoint: "sdxl_base.safetensors", steps: 25, cfgScale: 6.5, sampler: "euler", seed: 42 },
    f11: { positivePrompt: "a red fox in snow, detailed fur", negativePrompt: "blurry, lowres", checkpoint: "sdxl_base.safetensors", steps: 25, cfgScale: 6.5, sampler: "euler", seed: 42 },
    f12: { positivePrompt: "a red fox in snow, detailed fur", negativePrompt: "blurry, lowres", checkpoint: "sdxl_base.safetensors", steps: 25, cfgScale: 6.5, sampler: "euler", seed: 42 },
    f14: { positivePrompt: "a red fox in snow, detailed fur", negativePrompt: "blurry, lowres", checkpoint: "sdxl_base.safetensors", steps: 25, cfgScale: 6.5, sampler: "euler", seed: 42 },
    f15: { positivePrompt: "a red fox in snow, detailed fur", negativePrompt: "blurry, lowres", checkpoint: "sdxl_base.safetensors", steps: 25, cfgScale: 6.5, sampler: "euler", seed: 42 },
    f16: { positivePrompt: "a grey wolf in fog", negativePrompt: "blurry, lowres", checkpoint: "sdxl_base.safetensors", steps: 25, cfgScale: 6.5, sampler: "euler", seed: 42 },
    f17: { positivePrompt: "No prompt found", negativePrompt: "No negative prompt found", checkpoint: "flux1-dev.safetensors", steps: "Unknown", cfgScale: "Unknown", sampler: "euler", seed: "Unknown" },
};

test("legacy extractGenerationParams characterization covers every committed fixture", () => {
    const committed = fs.readdirSync(FIXTURES).filter(file => file.endsWith(".png")).map(file => file.slice(0, -4)).sort();
    assert.deepEqual(Object.keys(EXPECTED).sort(), committed);
});

for (const [name, expected] of Object.entries(EXPECTED)) {
    test(`legacy extractGenerationParams characterization: ${name}`, () => {
        assert.deepEqual(legacyParams(name), expected);
    });
}

test("legacy extractGenerationParams characterization keeps f01b's detailer text as both roles", () => {
    const params = legacyParams("f01b");
    assert.equal(params.positivePrompt, DETAILER);
    assert.equal(params.negativePrompt, DETAILER);
});

test("legacy extractGenerationParams characterization replaces NaN before parsing", () => {
    const params = legacyParams("f14");
    assert.equal(params.positivePrompt, "a red fox in snow, detailed fur");
    assert.equal(params.seed, 42);
});
