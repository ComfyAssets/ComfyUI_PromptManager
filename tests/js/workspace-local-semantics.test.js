// web/js/workspace/local-semantics.mjs (#249 local Inspect, PR L3 Task 10): the node table local Inspect resolves
// with is the server's own web/js/inspection-semantics.json, fetched once through the API client before a file is
// chosen. A table that cannot be fetched or used answers as the server does when its table is unusable.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

const TABLE = fs.readFileSync(new URL("../../web/js/inspection-semantics.json", import.meta.url), "utf8");
let instance = 0;
/** A fresh module instance (its cache is per page) with `fetch` and the API client stubbed. */
async function load(respond) {
    const calls = [];
    globalThis.window = { ApiClient: { resolveUrl: (path) => `/proxy${path}` } };
    globalThis.fetch = async (url, init) => { calls.push([url, init]); return respond(); };
    const module = await import(`../../web/js/workspace/local-semantics.mjs?instance=${++instance}`);
    return { module, calls };
}
test.after(() => { delete globalThis.window; delete globalThis.fetch; });

test("the table is fetched once through the API client and compiled", async () => {
    const { module, calls } = await load(() => new Response(TABLE, { status: 200 }));
    const [first, second] = await Promise.all([module.loadInspectionSemantics(), module.loadInspectionSemantics()]);
    assert.equal(first, second);
    assert.ok(first.samplers.has("KSampler"));
    assert.equal(await module.loadInspectionSemantics(), first);
    assert.deepEqual(calls, [["/proxy/prompt_manager/js/inspection-semantics.json", { credentials: "same-origin" }]]);
});

test("a failed or unusable table is null, and a later selection asks again", async () => {
    for (const respond of [() => new Response("nope", { status: 404 }), () => new Response("{\"schema\": 2}", { status: 200 }), () => { throw new TypeError("offline"); }]) {
        const { module, calls } = await load(respond);
        assert.equal(await module.loadInspectionSemantics(), null);
        assert.equal(await module.loadInspectionSemantics(), null);
        assert.equal(calls.length, 2);
    }
});

test("without the API client nothing is fetched", async () => {
    const { module, calls } = await load(() => new Response(TABLE));
    delete globalThis.window;
    assert.equal(await module.loadInspectionSemantics(), null);
    assert.deepEqual(calls, []);
});
