// Run with: node --test tests/js/
// comfy-metadata.js stays a classic script for the legacy pages, so its facts
// tables are inline literals. This test keeps them equal to the one semantics
// table (web/js/inspection-semantics.json) that Python also loads.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const metadata = require("../../web/js/comfy-metadata.js");

const tablePath = path.join(__dirname, "..", "..", "web", "js", "inspection-semantics.json");
const semantics = JSON.parse(fs.readFileSync(tablePath, "utf8"));

function plain(value) {
    if (value instanceof Set) return [...value];
    if (Array.isArray(value)) return value.map(plain);
    if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, plain(item)]));
    return value;
}

test("facts tables equal semantics.facts", () => {
    assert.equal(typeof metadata._factsTables, "object", "comfy-metadata exposes its facts tables for this test");
    assert.deepStrictEqual(plain(metadata._factsTables), semantics.facts);
});

test("facts guider providers are guiders in the semantics table", () => {
    const providers = Object.entries(semantics.facts.configuration_outputs).filter(([, adapter]) => adapter.kind === "guider");
    assert.ok(providers.length > 0);
    for (const [name] of providers) assert.ok(Object.hasOwn(semantics.guiders, name), name);
});

test("the facts tables are not part of the legacy page contract", () => {
    assert.deepStrictEqual(Object.keys(metadata).filter(key => !key.startsWith("_")).sort(), ["PLACEHOLDERS", "extractGenerationFacts", "extractGenerationParams"]);
    assert.ok(Object.isFrozen(metadata._factsTables));
});

// Task 2.1 checked only `facts`; the facts code also reads the samplers table
// (which classes are samplers, their facts shape and a detailer's image input).
test("sampler facts table equals semantics.samplers", () => {
    assert.equal(typeof metadata._samplerTable, "object", "comfy-metadata exposes its sampler table for this test");
    // Task 2.7b: plus `fact_overrides`; `prompt_overrides` is read only by the resolver.
    const expected = Object.fromEntries(Object.entries(semantics.samplers).map(([name, entry]) => [name, {
        facts: entry.facts,
        ...(entry.image_stage === undefined ? {} : { image_stage: entry.image_stage }),
        ...(entry.fact_overrides === undefined ? {} : { fact_overrides: entry.fact_overrides }),
    }]));
    assert.deepStrictEqual(plain(metadata._samplerTable), expected);
    assert.ok(Object.isFrozen(metadata._samplerTable));
});

test("detailer-stage samplers, and only they, name an image stage", () => {
    for (const [name, entry] of Object.entries(semantics.samplers)) {
        assert.equal(Object.hasOwn(metadata._samplerTable[name], "image_stage"), entry.strategy === "detailer-stage", name);
    }
});

// The stage walk treats every table class as known (the resolver's `all_classes`).
test("stage walk table classes equal every class in the semantics table", () => {
    const classes = new Set();
    for (const category of ["samplers", "guiders", "selectors", "composites", "savers"]) Object.keys(semantics[category]).forEach(name => classes.add(name));
    for (const category of ["conditioning", "encoders", "strings"]) semantics[category].forEach(entry => classes.add(entry.class));
    for (const category of ["runtime", "non_file_outputs"]) semantics[category].forEach(name => classes.add(name));
    assert.deepStrictEqual([...metadata._tableClasses].sort(), [...classes].sort());
});

// Review I-2: one source for the stage-walk input lists (Python reads them there too).
test("stage walk inputs equal semantics.stage_inputs", () => {
    assert.equal(typeof semantics.stage_inputs, "object");
    assert.equal(typeof metadata._stageInputs, "object", "comfy-metadata exposes its stage inputs for this test");
    assert.deepStrictEqual(plain(metadata._stageInputs), semantics.stage_inputs);
    assert.ok(Object.isFrozen(metadata._stageInputs));
});

// Review M-3: JS treats every samplers entry as a candidate; the server only these strategies.
test("every samplers entry is a candidate strategy", () => {
    for (const [name, entry] of Object.entries(semantics.samplers)) {
        assert.ok(["ksampler", "custom-advanced", "detailer-stage"].includes(entry.strategy), name);
    }
});
