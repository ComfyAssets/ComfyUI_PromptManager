// Run with: node --test tests/js/
const test = require("node:test");
const assert = require("node:assert/strict");
const { parseSseChunk, connect } = require("../../web/js/sse-stream.js");

test("parseSseChunk splits complete events and keeps the unfinished tail", () => {
    const { events, rest } = parseSseChunk('data: {"a":1}\n\ndata: {"b":2}\n\ndata: {"c"');
    assert.deepEqual(events.map((e) => e.data), ['{"a":1}', '{"b":2}']);
    assert.equal(rest, 'data: {"c"');
});

test("parseSseChunk joins multi-line data, reads event names and ignores comments", () => {
    const { events, rest } = parseSseChunk(": keep-alive\nevent: progress\ndata: line1\ndata: line2\nid: 7\n\n");
    assert.deepEqual(events, [{ event: "progress", data: "line1\nline2" }]);
    assert.equal(rest, "");
});

test("parseSseChunk accepts CRLF line endings and a missing space after the colon", () => {
    const { events } = parseSseChunk("data:x\r\n\r\ndata: y\r\n\r\n");
    assert.deepEqual(events.map((e) => e.data), ["x", "y"]);
});

test("parseSseChunk returns nothing for an empty or whitespace buffer", () => {
    assert.deepEqual(parseSseChunk(""), { events: [], rest: "" });
    assert.deepEqual(parseSseChunk("\n\n"), { events: [], rest: "" });
});

function streamOf(chunks) {
    const encoder = new TextEncoder();
    return new ReadableStream({
        start(controller) {
            for (const c of chunks) controller.enqueue(encoder.encode(c));
            controller.close();
        },
    });
}

function until(check, timeoutMs = 500) {
    return new Promise((resolve, reject) => {
        const started = Date.now();
        (function poll() {
            if (check()) return resolve();
            if (Date.now() - started > timeoutMs) return reject(new Error("timed out"));
            setTimeout(poll, 2);
        })();
    });
}

test("connect POSTs and delivers each event to onmessage, then onclose", async () => {
    const seen = [];
    let closed = false;
    const calls = [];
    const fetchImpl = async (url, init) => {
        calls.push({ url, init });
        return new Response(streamOf(['data: {"n":1}\n\ndata: {"n', '":2}\n\n']), { status: 200 });
    };
    const source = connect("/prompt_manager/autotag/start?x=1", { method: "POST", fetchImpl });
    source.onmessage = (e) => seen.push(JSON.parse(e.data).n);
    source.onclose = () => { closed = true; };
    await until(() => closed);
    assert.deepEqual(seen, [1, 2]);
    assert.equal(calls[0].init.method, "POST");
    assert.equal(calls[0].url, "/prompt_manager/autotag/start?x=1");
    assert.equal(source.readyState, source.CLOSED);
});

test("connect reports a non-2xx response through onerror and never onmessage", async () => {
    let error = null;
    const fetchImpl = async () => new Response("nope", { status: 405 });
    const source = connect("/x", { fetchImpl });
    source.onmessage = () => assert.fail("no message expected");
    source.onerror = (err) => { error = err; };
    await until(() => error !== null);
    assert.match(error.message, /405/);
    assert.equal(source.readyState, source.CLOSED);
});

test("connect reports a network failure through onerror", async () => {
    let error = null;
    const source = connect("/x", { fetchImpl: async () => { throw new TypeError("Failed to fetch"); } });
    source.onerror = (err) => { error = err; };
    await until(() => error !== null);
    assert.equal(error.message, "Failed to fetch");
});

test("close() aborts the request and suppresses later callbacks", async () => {
    let aborted = false;
    let errors = 0;
    const fetchImpl = (url, init) =>
        new Promise((resolve, reject) => {
            init.signal.addEventListener("abort", () => {
                aborted = true;
                reject(new DOMException("aborted", "AbortError"));
            });
            resolve(new Response(new ReadableStream({ start() {} }), { status: 200 }));
        });
    const source = connect("/x", { fetchImpl });
    source.onerror = () => errors++;
    await until(() => source.readyState === source.OPEN);
    source.close();
    assert.equal(aborted, true);
    assert.equal(source.readyState, source.CLOSED);
    await new Promise((r) => setTimeout(r, 10));
    assert.equal(errors, 0);
});

test("connect forwards a request body and an Accept header", async () => {
    let init = null;
    const fetchImpl = async (_url, i) => { init = i; return new Response(streamOf([]), { status: 200 }); };
    const source = connect("/x", { method: "POST", body: '{"a":1}', fetchImpl });
    await until(() => source.readyState === source.CLOSED);
    assert.equal(init.body, '{"a":1}');
    assert.equal(init.headers.Accept, "text/event-stream");
});

test("events arriving with no onmessage handler are dropped without error", async () => {
    const fetchImpl = async () => new Response(streamOf(["data: 1\n\n", "data: 2\n\n"]), { status: 200 });
    const source = connect("/x", { fetchImpl });
    let errors = 0;
    source.onerror = () => errors++;
    await until(() => source.readyState === source.CLOSED);
    assert.equal(errors, 0);
});

test("close() during delivery stops further messages and skips onclose", async () => {
    const seen = [];
    let closed = false;
    const fetchImpl = async () => new Response(streamOf(["data: 1\n\ndata: 2\n\ndata: 3\n\n"]), { status: 200 });
    const source = connect("/x", { fetchImpl });
    source.onmessage = (e) => { seen.push(e.data); if (e.data === "1") source.close(); };
    source.onclose = () => { closed = true; };
    await until(() => source.readyState === source.CLOSED);
    await new Promise((r) => setTimeout(r, 10));
    assert.deepEqual(seen, ["1"]);
    assert.equal(closed, false);
});

test("close() before the response arrives discards it silently", async () => {
    let resolveFetch;
    let errors = 0;
    const fetchImpl = () => new Promise((resolve) => { resolveFetch = resolve; });
    const source = connect("/x", { fetchImpl });
    source.onerror = () => errors++;
    source.close();
    resolveFetch(new Response(streamOf(["data: late\n\n"]), { status: 200 }));
    await new Promise((r) => setTimeout(r, 10));
    assert.equal(errors, 0);
    assert.equal(source.readyState, source.CLOSED);
});

test("connect defaults to GET and the global fetch", async () => {
    const original = globalThis.fetch;
    let init = null;
    globalThis.fetch = async (_url, i) => { init = i; return new Response(streamOf([]), { status: 200 }); };
    try {
        const source = connect("/x");
        await until(() => source.readyState === source.CLOSED);
        assert.equal(init.method, "GET");
    } finally {
        globalThis.fetch = original;
    }
});
