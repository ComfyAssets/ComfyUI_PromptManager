// Run with: node --test tests/js/
// Legacy parser rules ported to the inspection facts (#240, Task 2.7b). Synthetic texts.
const test = require("node:test");
const assert = require("node:assert/strict");
const { extractGenerationFacts } = require("../../web/js/comfy-metadata.js");

const fields = ["seed", "steps", "cfg", "sampler", "scheduler", "model"];
const FOOTER = "Steps: 20, Sampler: Euler, CFG scale: 7, Seed: 5";

function parameterFacts(parameters) {
    const facts = extractGenerationFacts({
        prompt_graph: { status: "absent", value: null },
        workflow_graph: { status: "absent", value: null },
        parameters,
        recordFormats: { parameters: "original-text" },
        metadataStatus: "available",
    });
    return Object.fromEntries(fields.map(field => [field, [facts[field].status, facts[field].value]]));
}

const FOUR_KNOWN = {
    seed: ["known", "5"], steps: ["known", "20"], cfg: ["known", "7"], sampler: ["known", "Euler"],
    scheduler: ["not-recorded", null], model: ["not-recorded", null],
};
const ALL_UNRESOLVED = Object.fromEntries(fields.map(field => [field, ["unresolved", null]]));

test("R4: text starting with the negative delimiter has no positive, as with a leading newline", () => {
    for (const text of [`Negative prompt: bad\n${FOOTER}`, `\nNegative prompt: bad\n${FOOTER}`, `Negative prompt: a\nNegative prompt: b\n${FOOTER}`]) {
        assert.deepEqual(parameterFacts(text), ALL_UNRESOLVED, text);
    }
    assert.deepEqual(parameterFacts(`a cat\nNegative prompt: bad\n${FOOTER}`), FOUR_KNOWN);
});

test("R5: template records after the footer and a bare Steps footer", () => {
    const base = `a cat\nNegative prompt: bad\n${FOOTER}`;
    for (const trailer of ["\nTemplate: a {cat|dog}", "\nTemplate: a {cat|dog}\nNegative Template: bad", "\nNegative Template: bad\n"]) {
        assert.deepEqual(parameterFacts(base + trailer), FOUR_KNOWN, trailer);
    }
    for (const trailer of ["\nTemplate: a\nTemplate: b", "\nNegative Template: bad\nTemplate: a", "\nTemplate: a\nmore", "\ntrailing"]) {
        assert.deepEqual(parameterFacts(base + trailer), ALL_UNRESOLVED, trailer);
    }
    const bare = parameterFacts("a cat\nNegative prompt: bad\nSteps: 20\nTemplate: a {cat|dog}");
    assert.deepEqual(bare.steps, ["known", "20"]);
    assert.ok(["seed", "cfg", "sampler", "scheduler", "model"].every(field => bare[field][0] === "not-recorded"));
    // A full footer wins over a bare `Steps: N` line in the prompt; two bare ones are not unique.
    assert.deepEqual(parameterFacts(`a cat\nSteps: 3\nNegative prompt: bad\n${FOOTER}`), FOUR_KNOWN);
    assert.deepEqual(parameterFacts("a cat\nSteps: 3\nNegative prompt: bad\nSteps: 20"), ALL_UNRESOLVED);
});

test("R10: commas inside quoted settings values do not split them", () => {
    assert.deepEqual(parameterFacts(`a cat\nNegative prompt: bad\n${FOOTER}, ADetailer prompt: "face, smile"`), FOUR_KNOWN);
    assert.deepEqual(parameterFacts(`a cat\n${FOOTER}, Note: "say \\"hi, there\\"", Model: base`), { ...FOUR_KNOWN, model: ["known", "base"] });
    // A quoted value of a fact is its JSON string.
    assert.deepEqual(parameterFacts(`a cat\n${FOOTER}, Model: "base, v2"`).model, ["known", "base, v2"]);
    // Unquoted JSON (comfy-image-saver hashes) keeps the historical split, field by field.
    assert.deepEqual(parameterFacts(`a cat\n${FOOTER}, Hashes: {"model": "abc", "lora:x": "def"}, Model: base`).model, ["known", "base"]);
    // An unbalanced quote is not a quoted value: the historical split, as A1111 reads it.
    assert.deepEqual(parameterFacts(`a cat\n${FOOTER}, Note: "open, Model: base`).model, ["known", "base"]);
});

function graphFacts(graph, resolution) {
    const facts = extractGenerationFacts({
        prompt_graph: { status: "parsed", value: graph },
        workflow_graph: { status: "absent", value: null },
        metadataStatus: "available",
        resolution,
    });
    return Object.fromEntries(fields.map(field => [field, [facts[field].status, facts[field].value]]));
}

function modelGraph(between) {
    return {
        ckpt: { class_type: "CheckpointLoaderSimple", inputs: { ckpt_name: "base.safetensors" } },
        hop: { class_type: between, inputs: { model: ["ckpt", 0], clip: ["ckpt", 1] } },
        s: { class_type: "KSampler", inputs: { seed: 5, steps: 20, cfg: 7, sampler_name: "euler", scheduler: "normal", model: ["hop", 0] } },
    };
}

test("R9: model pass-throughs verified in their sources lead to the loader", () => {
    for (const name of ["Lora Loader (LoraManager)", "Power Lora Loader (rgthree)", "ModelPatchTorchSettings", "CFGNorm", "PerturbedAttentionGuidance"]) {
        assert.deepEqual(graphFacts(modelGraph(name)).model, ["known", "base.safetensors"], name);
        // Only output 0 is the model.
        const other = modelGraph(name); other.s.inputs.model = ["hop", 1];
        assert.deepEqual(graphFacts(other).model, ["unresolved", null], name);
    }
    assert.deepEqual(graphFacts(modelGraph("ModelMergeSDXL")).model, ["unresolved", null]);
});

test("R6: upscale and SEGS detailers supply stage facts like FaceDetailer", () => {
    for (const [class_type, image] of [["UltimateSDUpscale", "image"], ["UltimateSDUpscaleNoUpscale", "upscaled_image"], ["DetailerForEach", "image"]]) {
        const graph = modelGraph("CFGNorm");
        graph.decode = { class_type: "VAEDecode", inputs: { samples: ["s", 0], vae: ["ckpt", 2] } };
        graph.d = { class_type, inputs: { [image]: ["decode", 0], model: ["hop", 0], seed: 7, steps: 12, cfg: 7, sampler_name: "euler", scheduler: "normal" } };
        assert.deepEqual(graphFacts(graph, { state: "resolved", sampler_id: "d" }).seed, ["known", "7"], class_type);
        const merged = graphFacts(graph, { state: "ambiguous", sampler_id: "d" });
        assert.deepEqual([merged.seed[0], merged.steps[0], merged.cfg], ["ambiguous", "ambiguous", ["known", "7"]], class_type);
    }
});
