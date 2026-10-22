// web/js/workspace/local-projection.mjs ports utils/inspection_response.py: the
// strict Decimal/duplicate-key parse, the prompt/workflow/carrier projection, the
// depth and node ceilings, _metadata and the 8 MiB omission steps. These units pin
// the rules the plan names; tests/test_local_inspect_parity.py proves byte parity
// with the server over the committed corpus.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const m = await import("../../web/js/workspace/local-projection.mjs").catch((error) => {
    if (error.code !== "ERR_MODULE_NOT_FOUND") throw error;
    return {};
});
const CASES = JSON.parse(fs.readFileSync(path.join(here, "..", "fixtures", "local-inspect", "projection-cases.json"), "utf8")).cases;

const plain = (value) => JSON.parse(JSON.stringify(value));
const bytes = (value) => Buffer.byteLength(JSON.stringify(value), "utf8");
const graphOf = (text, kind = "workflow") => plain(m.projectGraph(text, kind));
const nested = (depth, close = "]", open = "[") => open.repeat(depth) + close.repeat(depth);
const nodes = (count) => `{${Array.from({ length: count }, (_, i) => `"${i}":{"class_type":"A","inputs":{}}`).join(",")}}`;

test("contract", () => {
    for (const name of ["projectGraph", "projectMetadata", "fitInspectionPayload", "inspectionBytes", "parseStrictJson"]) {
        assert.equal(typeof m[name], "function", `${name} is exported`);
    }
    assert.equal(m.MAX_RESPONSE_BYTES, 8388608);
    assert.equal(m.MAX_PROJECTION_DEPTH, 64);
    assert.equal(m.GRAPH_NODE_CEILING, 20000);
});

test("projection spells numbers like the server", () => {
    // literal -> Python str(Decimal(literal)) (or the int when it is a safe integer)
    const table = [
        ["1e3", "1E+3"], ["1e0", "1"], ["-0.0", "-0.0"], ["6.50", "6.50"], ["0.0100", "0.0100"], ["1.0e2", "1.0E+2"],
        ["0e5", "0E+5"], ["0.0000001", "1E-7"], ["0.000001", "0.000001"], ["1E+3", "1E+3"], ["1e-0", "1"], ["0.5e1", "5"],
        ["0.05e1", "0.5"], ["0.0e0", "0.0"], ["1.5e+300", "1.5E+300"], ["123e-8", "0.00000123"], ["2.5e-3", "0.0025"],
        ["1.0", "1.0"], ["-1.50", "-1.50"], ["0.0", "0.0"], ["100.0", "100.0"], ["1234567e-6", "1.234567"], ["0.5E-6", "5E-7"],
        ["0.5E-5", "0.000005"], ["1e22", "1E+22"], ["0.000000", "0.000000"], ["-0.000000e-3", "-0E-9"], ["7.0e-6", "0.0000070"],
        ["7.0e-7", "7.0E-7"], ["12345678901234567890.123", "12345678901234567890.123"], ["9007199254740992.0", "9007199254740992.0"],
        ["1e999999999999999999", "1E+999999999999999999"], ["1e-1999999999999999997", "1E-1999999999999999997"],
        ["0.0e1000000000000000000", "0E+999999999999999999"], ["1.5e999999999999999999", "1.5E+999999999999999999"],
        ["0.1e-1999999999999999996", "1E-1999999999999999997"],
        ["9007199254740991", 9007199254740991], ["-9007199254740991", -9007199254740991], ["9007199254740992", "9007199254740992"],
        ["-9007199254740992", "-9007199254740992"], ["10000000000000000", "10000000000000000"], ["123456789012345678901234567890", "123456789012345678901234567890"],
        ["0", 0], ["-0", 0], ["42", 42], ["-7", -7],
    ];
    for (const [literal, expected] of table) {
        const graph = graphOf(`{"n":${literal}}`);
        assert.equal(graph.status, "parsed", literal);
        assert.deepEqual(graph.value, { n: expected }, literal);
    }
    assert.deepEqual(graphOf('{"n":[NaN,Infinity,-Infinity]}').value, { n: [null, null, null] });
    // A 4300-digit integer is Python's int() limit; one more digit fails the parse.
    assert.deepEqual(graphOf(`{"n":${"1".repeat(4300)}}`).value, { n: "1".repeat(4300) });
    assert.equal(graphOf(`{"n":${"1".repeat(4301)}}`).status, "malformed");
    assert.equal(graphOf(`{"n":-${"1".repeat(4301)}}`).status, "malformed");
    // Exact Decimal construction fails past the C library's exponent range.
    for (const literal of ["1e1000000000000000000", "15e999999999999999999", "1e-1999999999999999998", "1.5e-1999999999999999997",
        "0e1000000000000000000", "1e9223372036854775808", "0.0e1000000000000000001", "1e99999999999999999999999999"]) {
        assert.equal(graphOf(`{"n":${literal}}`).status, "malformed", literal);
    }
    // Escapes: surrogate pairs join, lone surrogates stay, control characters decode.
    const text = graphOf('{"s":"\\u00E9\\uD83D\\uDE00\\uD800\\u0000\\u001f\\/\\\\\\"\\b\\f\\n\\r\\t\\uD800\\u0041"}').value.s;
    assert.equal(text, "é\u{1f600}\ud800\u0000\u001f/\\\"\b\f\n\r\t\ud800A");
    assert.equal(JSON.stringify(m.projectGraph('{"s":"\\ud800"}', "workflow").value), '{"s":"\\ud800"}');
});

test("projection rejects what Python's json rejects and nothing else", () => {
    const rejected = ["﻿{}", "\f1", "  1", '{"a":1,}', "[1,]", "{'a':1}", '{"a"}', "[1 2]", "tru", "nul", "-NaN", "1.", ".5", "01", "+1",
        "1e", "-", '"\\x"', '"abc', "", "  ", '"\u0001"', '{"a":1}x', '"\\u12"', '{"a":1,"a":2}', '{"a":{"b":1,"b":1}}', "[1,[2,{\"k\":1,\"k\":2}]]",
        '{"a":1 "b":2}', "[,1]", "{,}", '{"a":}', "[1}", "{1:2}", " []", "[]\u000b", "0x10", "1_000", "--1", "1e+", "1E-", '"\\uD800\\uDC"'];
    for (const text of rejected) assert.equal(graphOf(text).status, "malformed", JSON.stringify(text));
    for (const text of [" \t\r\n{}\n\t ", '{"a":[1,{"b":null}],"c":"d"}', '{"\\u0000":1}', '{"a":1,"A":1}', '{"":[]}']) {
        assert.equal(graphOf(text).status, "parsed", JSON.stringify(text));
    }
    // Top-level values that are not objects parse but are not graphs.
    for (const text of ["[]", '"x"', "1", "null", "true", "NaN", "1.5", "-Infinity"]) assert.equal(graphOf(text).status, "malformed", text);
    assert.equal(graphOf(undefined).status, "malformed");
    assert.equal(graphOf(null).status, "malformed");
});

test("projection limits", () => {
    // A workflow's root is depth 1; the 64th nested container is kept, the 65th is limited.
    assert.equal(graphOf(`{"a":${nested(63)}}`).status, "parsed");
    assert.equal(graphOf(`{"a":${nested(64)}}`).status, "limited");
    // Prompt: root 1, node 2, inputs 3; a value chain reaching depth 64 is kept, 65 is limited.
    assert.equal(graphOf(`{"1":{"class_type":"A","inputs":{"v":${nested(61)}}}}`, "prompt").status, "parsed");
    assert.equal(graphOf(`{"1":{"class_type":"A","inputs":{"v":${nested(62)}}}}`, "prompt").status, "limited");
    // Dropped node keys are still validated for depth.
    assert.equal(graphOf(`{"1":{"class_type":"A","inputs":{},"_meta":{"v":${nested(61)}}}}`, "prompt").status, "parsed");
    assert.equal(graphOf(`{"1":{"class_type":"A","inputs":{},"_meta":{"v":${nested(62)}}}}`, "prompt").status, "limited");
    // A workflow is normalized before its shape is checked, so a deep non-object is limited; a
    // prompt checks its root first. Depth never overflows the parser (100,000 nested arrays).
    assert.equal(graphOf(nested(10)).status, "malformed");
    assert.equal(graphOf(nested(5000)).status, "limited");
    assert.equal(graphOf(nested(5000), "prompt").status, "malformed");
    assert.equal(graphOf(`{"a":${nested(100000)}}`).status, "limited");
    // Node ceilings: 20,000 API nodes parse, 20,001 are limited.
    assert.equal(graphOf(nodes(20000), "prompt").status, "parsed");
    assert.equal(graphOf(nodes(20001), "prompt").status, "limited");
    const list = (count) => `[${Array.from({ length: count }, () => "{}").join(",")}]`;
    assert.equal(graphOf(`{"nodes":${list(20000)}}`).status, "parsed");
    assert.equal(graphOf(`{"nodes":${list(20001)}}`).status, "limited");
    assert.equal(graphOf(`{"nodes":${list(10001)},"definitions":{"subgraphs":[{"nodes":${list(10000)}},5,{"nodes":3}]}}`).status, "limited");
    assert.equal(graphOf(`{"nodes":${list(10000)},"definitions":{"subgraphs":[{"nodes":${list(10000)}}]}}`).status, "parsed");
    assert.equal(graphOf(`{"nodes":{"${"x".repeat(5)}":1},"definitions":[]}`).status, "parsed");
    // Duplicate keys anywhere are malformed, including inside dropped node keys.
    assert.equal(graphOf('{"1":{"class_type":"A","inputs":{},"_meta":{"t":1,"t":2}}}', "prompt").status, "malformed");
    assert.equal(graphOf('{"1":{"class_type":"A","inputs":{"a":1,"a":1}}}', "prompt").status, "malformed");
});

test("prompt nodes keep only class_type and inputs on null-prototype objects", () => {
    const text = '{"3":{"class_type":"KSampler","inputs":{"seed":9007199254740993,"cfg":8.0,"n":NaN},"_meta":{"title":"x"},"is_changed":[1]},"4":5,"5":[1,2.50],"__proto__":{"class_type":"P","inputs":{}},"constructor":"c"}';
    const graph = m.projectGraph(text, "prompt");
    assert.equal(graph.status, "parsed");
    assert.equal(Object.getPrototypeOf(graph.value), null);
    assert.equal(Object.getPrototypeOf(graph.value["3"]), null);
    assert.equal(Object.getPrototypeOf(graph.value["3"].inputs), null);
    // JSON.parse, like the projection, makes "__proto__" an own key; an object literal would not.
    assert.deepEqual(plain(graph.value), JSON.parse('{"3":{"class_type":"KSampler","inputs":{"seed":"9007199254740993","cfg":"8.0","n":null}},"4":5,"5":[1,"2.50"],"__proto__":{"class_type":"P","inputs":{}},"constructor":"c"}'));
    assert.ok(Object.hasOwn(graph.value, "__proto__"));
    assert.deepEqual(plain(graph.value["__proto__"]), { class_type: "P", inputs: {} });
    assert.equal(graph.value.constructor, "c");
    for (const text of ["[]", '"3"', "5", "null", '{"nodes":[]}x']) assert.equal(m.projectGraph(text, "prompt").status, text === '{"nodes":[]}x' ? "malformed" : "malformed", text);
    // A workflow keeps everything, normalized.
    assert.deepEqual(graphOf('{"nodes":[{"id":1,"widgets_values":[7,"fixed",8.0,1e3]}],"links":[],"version":0.4}').value,
        { nodes: [{ id: 1, widgets_values: [7, "fixed", "8.0", "1E+3"] }], links: [], version: "0.4" });
});

test("projectMetadata mirrors _metadata for records, formats and carriers", () => {
    const prompt = '{"1":{"class_type":"A","inputs":{"x":1.0}}}', workflow = '{"nodes":[],"version":0.4}';
    const read = (records, record_formats, carrierText = null, limited = []) => ({ records, record_formats, carrierText, limited });
    assert.deepEqual(plain(m.projectMetadata(null)), { status: "unreadable", omitted_reason: null, raw: {}, record_formats: {}, parameters: null,
        prompt_graph: { status: "absent", value: null }, workflow_graph: { status: "absent", value: null } });
    assert.deepEqual(plain(m.projectMetadata(read({}, {}))), { status: "absent", omitted_reason: null, raw: {}, record_formats: {}, parameters: null,
        prompt_graph: { status: "absent", value: null }, workflow_graph: { status: "absent", value: null } });
    const full = plain(m.projectMetadata(read({ prompt, workflow, parameters: "cat\nSteps: 1", comfyassets_prompt_pairs: "{}", other: "x" },
        { prompt: "original-text", workflow: "original-text", parameters: "original-text", comfyassets_prompt_pairs: "original-text", other: "original-text" })));
    assert.deepEqual(full, { status: "available", omitted_reason: null,
        raw: { prompt, workflow, parameters: "cat\nSteps: 1", comfyassets_prompt_pairs: "{}" },
        record_formats: { prompt: "original-text", workflow: "original-text", parameters: "original-text", comfyassets_prompt_pairs: "original-text" },
        parameters: "cat\nSteps: 1", prompt_graph: { status: "parsed", value: { 1: { class_type: "A", inputs: { x: "1.0" } } } },
        workflow_graph: { status: "parsed", value: { nodes: [], version: "0.4" } } });
    // limited records lose their text; invalid is malformed; reserialized without a carrier is limited.
    const limited = plain(m.projectMetadata(read({ prompt, workflow }, { prompt: "original-text" }, null, ["workflow"])));
    assert.deepEqual(limited.raw, { prompt });
    assert.deepEqual(limited.record_formats, { prompt: "original-text", workflow: "limited" });
    assert.equal(limited.workflow_graph.status, "limited");
    assert.equal(plain(m.projectMetadata(read({ prompt }, { prompt: "original-text", workflow: "invalid" }))).workflow_graph.status, "malformed");
    assert.equal(plain(m.projectMetadata(read({ workflow }, { workflow: "reserialized-json" }))).workflow_graph.status, "limited");
    assert.equal(plain(m.projectMetadata(read({ prompt: "{" }, { prompt: "original-text" }))).prompt_graph.status, "malformed");
    // Unknown formats are dropped from record_formats but the raw text still projects.
    const odd = plain(m.projectMetadata(read({ prompt }, { prompt: "weird" })));
    assert.deepEqual(odd.record_formats, {});
    assert.equal(odd.prompt_graph.status, "parsed");
    // A non-text record vanishes from raw and its named graph is malformed.
    assert.equal(plain(m.projectMetadata(read({ prompt: 5 }, { prompt: "original-text" }))).prompt_graph.status, "malformed");
    // Carriers: strings re-parse by kind, objects were projected at depth 2; a bad carrier fails every present graph.
    const envelope = JSON.stringify({ prompt, workflow: { nodes: [], v: 1 } });
    const carried = plain(m.projectMetadata(read({ prompt, workflow: '{"nodes": [], "v": 1}' }, { prompt: "original-text", workflow: "reserialized-json" }, envelope)));
    assert.deepEqual(carried.prompt_graph, { status: "parsed", value: { 1: { class_type: "A", inputs: { x: "1.0" } } } });
    assert.deepEqual(carried.workflow_graph, { status: "parsed", value: { nodes: [], v: 1 } });
    const broken = plain(m.projectMetadata(read({ prompt, workflow }, { prompt: "original-text", workflow: "original-text" }, "[1]")));
    assert.deepEqual([broken.prompt_graph.status, broken.workflow_graph.status], ["malformed", "malformed"]);
    const deep = plain(m.projectMetadata(read({ prompt }, { prompt: "original-text" }, `{"title":${nested(70)},"prompt":${JSON.stringify(prompt)}}`)));
    assert.equal(deep.prompt_graph.status, "limited");
    const partial = plain(m.projectMetadata(read({ prompt, workflow }, { prompt: "original-text", workflow: "original-text" }, '{"prompt":"{","workflow":5}')));
    assert.deepEqual([partial.prompt_graph.status, partial.workflow_graph.status], ["malformed", "malformed"]);
    const missing = plain(m.projectMetadata(read({ prompt }, { prompt: "original-text" }, '{"workflow":{}}')));
    assert.deepEqual([missing.prompt_graph.status, missing.workflow_graph.status], ["malformed", "absent"]);
    // The carrier's own prompt object is projected: depth 2, node keys filtered.
    const objectPrompt = plain(m.projectMetadata(read({ prompt: "x" }, { prompt: "reserialized-json" }, '{"prompt":{"1":{"class_type":"A","inputs":{},"_meta":{}}}}')));
    assert.deepEqual(objectPrompt.prompt_graph, { status: "parsed", value: { 1: { class_type: "A", inputs: {} } } });
});

test("fitInspectionPayload omits the workflow graph, then the prompt graph, then all raw atomically", async () => {
    const payload = {
        success: true, inspection_version: 1, metadata: {
            status: "available", omitted_reason: null, raw: { prompt: "é".repeat(100), workflow: "{}" }, record_formats: { prompt: "original-text", workflow: "original-text" },
            parameters: null, prompt_graph: { status: "parsed", value: { 1: { class_type: "A", inputs: { t: "\ud800".repeat(50) } } } },
            workflow_graph: { status: "parsed", value: { nodes: Array.from({ length: 50 }, (_, i) => ({ id: i })) } },
        },
    };
    const text = JSON.stringify(payload);
    // UTF-8 bytes, not UTF-16 code units: 'é' is 2 bytes, an escaped lone surrogate 6.
    assert.equal(m.inspectionBytes(payload), Buffer.byteLength(text, "utf8"));
    assert.ok(m.inspectionBytes(payload) > text.length);
    // Measured in bounded chunks: pairs and 2-byte characters across every chunk edge count once.
    for (const shift of [0, 1, 2, 3]) {
        const wide = { s: "x".repeat(shift) + "é😀".repeat(200000) + "\ud800", t: "a😀".repeat(70000) };
        assert.equal(m.inspectionBytes(wide), Buffer.byteLength(JSON.stringify(wide), "utf8"), `shift ${shift}`);
    }
    const whole = await m.fitInspectionPayload(payload, bytes(payload));
    assert.equal(whole, payload, "a payload within the limit is returned as is");
    const first = await m.fitInspectionPayload(payload, bytes(payload) - 1);
    assert.equal(first.metadata.workflow_graph.status, "limited");
    assert.equal(first.metadata.prompt_graph.status, "parsed");
    assert.deepEqual(first.metadata.raw, payload.metadata.raw);
    assert.ok(bytes(first) <= bytes(payload) - 1);
    const second = await m.fitInspectionPayload(payload, bytes(first) - 1);
    assert.deepEqual([second.metadata.workflow_graph.status, second.metadata.prompt_graph.status], ["limited", "limited"]);
    assert.equal(second.metadata.status, "available");
    assert.deepEqual(second.metadata.raw, payload.metadata.raw);
    const third = await m.fitInspectionPayload(payload, bytes(second) - 1);
    assert.deepEqual(third.metadata, { ...second.metadata, raw: {}, parameters: null, status: "limited", omitted_reason: "response-size-limit" });
    assert.ok(bytes(third) <= bytes(second) - 1);
    await assert.rejects(m.fitInspectionPayload(payload, bytes(third) - 1), /exceeds/);
    // The caller's payload is never mutated and absent graphs are left alone.
    assert.equal(JSON.stringify(payload), text);
    const absent = { ...payload, metadata: { ...payload.metadata, workflow_graph: { status: "absent", value: null } } };
    const fitted = await m.fitInspectionPayload(absent, bytes(absent) - 1);
    assert.equal(fitted.metadata.workflow_graph.status, "absent");
    assert.equal(fitted.metadata.prompt_graph.status, "limited");
    assert.equal((await m.fitInspectionPayload(payload)).metadata.status, "available", "the default limit is 8 MiB");
    // Every omission step after the first measure hands the event loop a turn, and an
    // abort between steps rejects with AbortError; the answer itself is unchanged.
    let turns = 0;
    const counted = await m.fitInspectionPayload(payload, bytes(second) - 1, { yieldControl: async () => { turns += 1; } });
    assert.equal(JSON.stringify(counted), JSON.stringify(third));
    assert.equal(turns, 3);
    assert.equal(await m.fitInspectionPayload(payload, bytes(payload), { yieldControl: async () => { turns += 1; } }), payload);
    assert.equal(turns, 3, "a payload within the limit is measured once, without a turn");
    const controller = new AbortController();
    await assert.rejects(m.fitInspectionPayload(payload, bytes(second) - 1, { signal: controller.signal, yieldControl: async () => controller.abort() }), { name: "AbortError" });
});

test("the committed projection corpus has every outcome and parses with the oracle entry shape", () => {
    assert.ok(CASES.length >= 40);
    const names = new Set(), statuses = new Set(), graphs = new Set();
    for (const entry of CASES) {
        assert.equal(typeof entry.name, "string");
        assert.ok(!names.has(entry.name), `duplicate case ${entry.name}`);
        names.add(entry.name);
        const read = entry.records === null ? null : { records: entry.records, record_formats: entry.record_formats ?? Object.fromEntries(Object.keys(entry.records).map((key) => [key, "original-text"])),
            carrierText: entry.carrier_text ?? null, limited: entry.limited ?? [] };
        const metadata = m.projectMetadata(read);
        statuses.add(metadata.status);
        graphs.add(metadata.prompt_graph.status);
        graphs.add(metadata.workflow_graph.status);
        if (entry.expect) {
            for (const [field, status] of Object.entries(entry.expect)) assert.equal(metadata[field].status, status, `${entry.name} ${field}`);
        }
    }
    assert.deepEqual([...statuses].sort(), ["absent", "available", "unreadable"]);
    assert.deepEqual([...graphs].sort(), ["absent", "limited", "malformed", "parsed"]);
});
