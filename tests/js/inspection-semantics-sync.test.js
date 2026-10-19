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
