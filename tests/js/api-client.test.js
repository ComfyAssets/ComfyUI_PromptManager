// Run with: node --test tests/js/
const test = require("node:test");
const assert = require("node:assert/strict");
const { createApiClient, ApiError, latestOnly, STALE } = require("../../web/js/api-client.js");

/** A fetch stand-in whose responses the test resolves by hand, in any order. */
function fakeFetch() {
    const calls = [];
    const impl = (url, init = {}) =>
        new Promise((resolve, reject) => {
            const call = { url, init, resolve, reject, aborted: false };
            if (init.signal) {
                init.signal.addEventListener("abort", () => {
                    call.aborted = true;
                    reject(new DOMException("The operation was aborted.", "AbortError"));
                });
            }
            calls.push(call);
        });
    impl.calls = calls;
    return impl;
}

function jsonResponse(status, body, { invalid = false } = {}) {
    return {
        ok: status >= 200 && status < 300,
        status,
        statusText: status === 200 ? "OK" : `Status ${status}`,
        json: async () => {
            if (invalid) throw new SyntaxError("Unexpected token");
            return body;
        },
    };
}

test("get returns the parsed body of a successful response", async () => {
    const fetch = fakeFetch();
    const api = createApiClient(fetch);
    const pending = api.get("/prompt_manager/recent?page=1");
    fetch.calls[0].resolve(jsonResponse(200, { success: true, results: [1, 2] }));
    assert.deepEqual(await pending, { success: true, results: [1, 2] });
    assert.equal(fetch.calls[0].init.method, "GET");
});

test("get forwards an AbortSignal to fetch", async () => {
    const fetch = fakeFetch();
    const api = createApiClient(fetch);
    const controller = new AbortController();
    const pending = api.get("/x", { signal: controller.signal });
    assert.equal(fetch.calls[0].init.signal, controller.signal);
    controller.abort();
    await assert.rejects(pending, (err) => err.name === "AbortError");
});

test("non-2xx responses throw ApiError carrying the status and server message", async () => {
    const fetch = fakeFetch();
    const api = createApiClient(fetch);
    const pending = api.get("/x");
    fetch.calls[0].resolve(jsonResponse(404, { success: false, error: "Prompt not found" }));
    await assert.rejects(pending, (err) => {
        assert.ok(err instanceof ApiError);
        assert.ok(err instanceof Error);
        assert.equal(err.status, 404);
        assert.equal(err.message, "Prompt not found");
        return true;
    });
});

test("non-2xx responses without a JSON body fall back to the status text", async () => {
    const fetch = fakeFetch();
    const api = createApiClient(fetch);
    const pending = api.get("/x");
    fetch.calls[0].resolve(jsonResponse(500, null, { invalid: true }));
    await assert.rejects(pending, (err) => err.status === 500 && err.message === "Status 500");
});

test("a 2xx body with success:false throws ApiError", async () => {
    const fetch = fakeFetch();
    const api = createApiClient(fetch);
    const pending = api.post("/x", {});
    fetch.calls[0].resolve(jsonResponse(200, { success: false, error: "Nope" }));
    await assert.rejects(pending, (err) => err instanceof ApiError && err.status === 200 && err.message === "Nope");
});

test("a 2xx body that is not JSON throws ApiError", async () => {
    const fetch = fakeFetch();
    const api = createApiClient(fetch);
    const pending = api.get("/x");
    fetch.calls[0].resolve(jsonResponse(200, null, { invalid: true }));
    await assert.rejects(pending, (err) => err instanceof ApiError && err.status === 200);
});

test("post, put and del send JSON bodies with the right method", async () => {
    const fetch = fakeFetch();
    const api = createApiClient(fetch);
    const p1 = api.post("/a", { text: "x" });
    const p2 = api.put("/b", { rating: 3 });
    const p3 = api.del("/c");
    assert.equal(fetch.calls[0].init.method, "POST");
    assert.equal(fetch.calls[0].init.body, JSON.stringify({ text: "x" }));
    assert.equal(fetch.calls[0].init.headers["Content-Type"], "application/json");
    assert.equal(fetch.calls[1].init.method, "PUT");
    assert.equal(fetch.calls[1].init.body, JSON.stringify({ rating: 3 }));
    assert.equal(fetch.calls[2].init.method, "DELETE");
    assert.equal(fetch.calls[2].init.body, undefined);
    for (const c of fetch.calls) c.resolve(jsonResponse(200, { success: true }));
    await Promise.all([p1, p2, p3]);
});

test("network failures propagate as ordinary errors", async () => {
    const fetch = fakeFetch();
    const api = createApiClient(fetch);
    const pending = api.get("/x");
    fetch.calls[0].reject(new TypeError("Failed to fetch"));
    await assert.rejects(pending, (err) => err instanceof TypeError && !(err instanceof ApiError));
});

test("createApiClient without an argument uses the global fetch", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = async () => jsonResponse(200, { success: true, via: "global" });
    try {
        assert.deepEqual(await createApiClient().get("/x"), { success: true, via: "global" });
    } finally {
        globalThis.fetch = original;
    }
});

test("latestOnly: a newer call aborts the older one and the older result is STALE", async () => {
    const fetch = fakeFetch();
    const api = createApiClient(fetch);
    const latest = latestOnly();

    const first = latest((signal) => api.get("/search?q=a", { signal }));
    const second = latest((signal) => api.get("/search?q=ab", { signal }));

    assert.equal(fetch.calls[0].aborted, true, "first request aborted");
    assert.equal(fetch.calls[1].aborted, false);

    fetch.calls[1].resolve(jsonResponse(200, { success: true, q: "ab" }));
    assert.deepEqual(await second, { success: true, q: "ab" });
    assert.equal(await first, STALE);
});

test("latestOnly: an older response that arrives after a newer request is STALE even without abort", async () => {
    const latest = latestOnly();
    let resolveFirst;
    const first = latest(() => new Promise((r) => { resolveFirst = r; }));
    const second = latest(async () => "new");
    assert.equal(await second, "new");
    resolveFirst("old");
    assert.equal(await first, STALE);
});

test("latestOnly: sequential calls each return their own result", async () => {
    const latest = latestOnly();
    assert.equal(await latest(async () => 1), 1);
    assert.equal(await latest(async () => 2), 2);
});

test("latestOnly: errors from the current call propagate, errors from superseded calls do not", async () => {
    const latest = latestOnly();
    let rejectFirst;
    const first = latest(() => new Promise((_, rej) => { rejectFirst = rej; }));
    const second = latest(async () => { throw new Error("boom"); });
    await assert.rejects(second, /boom/);
    rejectFirst(new Error("old failure"));
    assert.equal(await first, STALE);
});
