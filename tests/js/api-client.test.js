// Run with: node --test tests/js/
const test = require("node:test");
const assert = require("node:assert/strict");
const { createApiClient, resolveUrl, fetchUrl, ApiError, latestOnly, STALE } = require("../../web/js/api-client.js");

test("Prompt Manager URLs follow the page's proxy mount without losing query strings", () => {
    const url = "/prompt_manager/images/output?limit=100&subfolder=2025%2F04%2F02";
    assert.equal(resolveUrl(url, "/ComfyBackendDirect/prompt_manager/gallery"),
        "/ComfyBackendDirect" + url);
    assert.equal(resolveUrl(url, "/prompt_manager/gallery"), url);
    assert.equal(resolveUrl(url, "/nested/proxy/prompt_manager/admin"), "/nested/proxy" + url);
    assert.equal(resolveUrl("/other/api?x=1", "/ComfyBackendDirect/prompt_manager/gallery"), "/other/api?x=1");
    assert.equal(resolveUrl("/ComfyBackendDirect/prompt_manager/images/serve/a.png", "/ComfyBackendDirect/prompt_manager/gallery"),
        "/ComfyBackendDirect/prompt_manager/images/serve/a.png");
    assert.equal(resolveUrl("/prompt_manager/images/serve/a.png?root=1", "/ComfyBackendDirect/prompt_manager/gallery"),
        "/ComfyBackendDirect/prompt_manager/images/serve/a.png?root=1");
});

test("fetchUrl sends Prompt Manager requests under the current proxy mount", async () => {
    const previousFetch = globalThis.fetch;
    const previousLocation = globalThis.location;
    const calls = [];
    globalThis.location = { pathname: "/ComfyBackendDirect/prompt_manager/admin" };
    globalThis.fetch = async (url, init) => {
        calls.push([url, init]);
        return { ok: true };
    };
    try {
        await fetchUrl("/prompt_manager/settings?show=all", { method: "GET" });
        assert.deepEqual(calls, [["/ComfyBackendDirect/prompt_manager/settings?show=all", { method: "GET" }]]);
    } finally {
        globalThis.fetch = previousFetch;
        globalThis.location = previousLocation;
    }
});

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

test("ApiError preserves only a bounded machine code without changing status or message", async () => {
    assert.equal(new ApiError(409, "unchanged").code, null);
    for (const [code, expected] of [
        ["source-changed", "source-changed"], ["a".repeat(64), "a".repeat(64)],
        [undefined, null], [null, null], [4, null], [{ code: "x" }, null],
        ["", null], ["Source-changed", null], ["source_changed", null],
        ["source-changed\n", null], ["a".repeat(65), null],
    ]) {
        for (const status of [409, 200]) {
            const api = createApiClient(async () => jsonResponse(status, {
                success: false, error: "unchanged", code, private: "never expose",
            }));
            await assert.rejects(api.get("/x"), error => {
                assert.ok(error instanceof ApiError);
                assert.equal(error.status, status);
                assert.equal(error.message, "unchanged");
                assert.equal(error.code, expected);
                assert.equal(error.private, undefined);
                assert.equal(error.body, undefined);
                return true;
            });
        }
    }
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

test("del sends an optional JSON body", async () => {
    const fetch = fakeFetch();
    const api = createApiClient(fetch);
    const controller = new AbortController();
    const pending = api.del("/prompt_manager/images/favorite", { body: { root_index: 1, path: "a/b.png" }, signal: controller.signal });
    assert.equal(fetch.calls[0].init.method, "DELETE");
    assert.equal(fetch.calls[0].init.body, JSON.stringify({ root_index: 1, path: "a/b.png" }));
    assert.equal(fetch.calls[0].init.headers["Content-Type"], "application/json");
    assert.equal(fetch.calls[0].init.signal, controller.signal);
    fetch.calls[0].resolve(jsonResponse(200, { success: true }));
    await pending;
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

test("latestOnly with a lifetime: once it ends, no request starts and the call is STALE", async () => {
    const fetch = fakeFetch();
    const api = createApiClient(fetch);
    const page = new AbortController();
    const latest = latestOnly({ lifetime: page.signal });
    page.abort();
    assert.equal(await latest((signal) => api.get("/recent", { signal })), STALE);
    assert.equal(fetch.calls.length, 0, "a leaving page starts no read");
});

test("latestOnly with a lifetime: its end aborts the request in flight, which is STALE, not an error", async () => {
    const fetch = fakeFetch();
    const api = createApiClient(fetch);
    const page = new AbortController();
    const latest = latestOnly({ lifetime: page.signal });
    const call = latest((signal) => api.get("/recent", { signal }));
    page.abort();
    assert.equal(fetch.calls[0].aborted, true, "the read in flight is aborted");
    fetch.calls[0].reject(new DOMException("The operation was aborted.", "AbortError"));
    assert.equal(await call, STALE);
});

test("latestOnly with a lifetime: real errors still propagate while the page lives", async () => {
    const page = new AbortController();
    const latest = latestOnly({ lifetime: page.signal });
    await assert.rejects(latest(async () => { throw new Error("server down"); }), /server down/);
    assert.equal(await latest(async () => "fine"), "fine");
});

test("ApiError exposes only validated history recovery actions on all error paths", async () => {
    const permitted = [null, { action: "refresh-history" }, { action: "reload-outputs" },
        { action: "retry-read" }, { action: "smaller-limit", limit: 1 },
        { action: "smaller-limit", limit: 100 }];
    const denied = [undefined, false, "refresh-history", [], {}, { action: "unknown" },
        { action: "refresh-history", private: "secret" }, { action: "retry-read", limit: 1 },
        { action: "smaller-limit" }, { action: "smaller-limit", limit: 0 },
        { action: "smaller-limit", limit: 101 }, { action: "smaller-limit", limit: true },
        { action: "smaller-limit", limit: "1" }, { action: "smaller-limit", limit: 1.5 },
        { action: "smaller-limit", limit: NaN }, { action: "smaller-limit", limit: Infinity },
        { action: "smaller-limit", limit: 10, url: "/private" },
        Object.assign(Object.create({ action: "retry-read" }), { private: "secret" })];
    assert.equal(new ApiError(409, "old caller").recovery, null);
    for (const recovery of permitted.concat(denied)) {
        for (const status of [409, 200]) {
            const api = createApiClient(async () => jsonResponse(status, {
                success: false, error: "bounded", code: "history-changed", recovery,
            }));
            await assert.rejects(api.get("/x"), error => {
                assert.ok(error instanceof ApiError);
                assert.equal(error.code, "history-changed");
                assert.deepEqual(error.recovery, permitted.includes(recovery) ? recovery : null);
                if (recovery && permitted.includes(recovery)) {
                    assert.notEqual(error.recovery, recovery, "server objects must be copied");
                }
                return true;
            });
        }
    }
});

test("patch sends a JSON body with the PATCH method and forwards an AbortSignal", async () => {
    const fetch = fakeFetch();
    const api = createApiClient(fetch);
    assert.equal(typeof api.patch, "function");
    const controller = new AbortController();
    const pending = api.patch("/prompt_manager/library/7", { metadata: { rating: null } }, { signal: controller.signal });
    assert.equal(fetch.calls[0].init.method, "PATCH");
    assert.equal(fetch.calls[0].init.body, JSON.stringify({ metadata: { rating: null } }));
    assert.equal(fetch.calls[0].init.headers["Content-Type"], "application/json");
    assert.equal(fetch.calls[0].init.signal, controller.signal);
    fetch.calls[0].resolve(jsonResponse(409, { success: false, code: "metadata-conflict", error: "changed" }));
    await assert.rejects(pending, error => error instanceof ApiError && error.status === 409 && error.code === "metadata-conflict");
});

test("get forwards an explicit cache mode and keeps the default request otherwise", async () => {
    const fetch = fakeFetch();
    const api = createApiClient(fetch);
    const fresh = api.get("/prompt_manager/library/options", { cache: "no-store" });
    const plain = api.get("/prompt_manager/library/options");
    assert.equal(fetch.calls[0].init.cache, "no-store");
    assert.equal("cache" in fetch.calls[1].init, false);
    assert.deepEqual(fetch.calls[1].init, { method: "GET", headers: {} });
    for (const call of fetch.calls) call.resolve(jsonResponse(200, { success: true }));
    await Promise.all([fresh, plain]);
    const writes = api.post("/x", {}, { cache: "no-store" });
    assert.equal("cache" in fetch.calls[2].init, false, "only reads take a cache mode");
    fetch.calls[2].resolve(jsonResponse(200, { success: true }));
    await writes;
});

test("ApiError exposes only a validated current tag set from a tag-conflict body", async () => {
    const permitted = [{ tags: [], tag_revision: 0 }, { tags: ["warm", "Dune"], tag_revision: 7 }];
    const denied = [undefined, null, { tags: ["a"] }, { tag_revision: 1 }, { tags: "a", tag_revision: 1 },
        { tags: [1], tag_revision: 1 }, { tags: ["a"], tag_revision: -1 }, { tags: ["a"], tag_revision: 1.5 },
        { tags: ["a"], tag_revision: true }, { tags: Array(101).fill("a"), tag_revision: 1 }];
    assert.equal(new ApiError(409, "old caller").current, null);
    for (const current of permitted.concat(denied)) {
        const api = createApiClient(async () => jsonResponse(409, {
            success: false, error: "changed", code: "tag-conflict", ...(current || {}),
        }));
        await assert.rejects(api.put("/x", {}), error => {
            assert.ok(error instanceof ApiError);
            assert.deepEqual(error.current, permitted.includes(current) ? current : null);
            if (permitted.includes(current)) assert.notEqual(error.current.tags, current.tags, "server arrays must be copied");
            return true;
        });
    }
});

test("keepalive is forwarded only when asked, so a request can outlive its page", async () => {
    const fetch = fakeFetch();
    const api = createApiClient(fetch);
    const writes = [api.del("/x", { keepalive: true }), api.put("/x", {}, { keepalive: true }), api.post("/x", {}, { keepalive: true }),
        api.del("/x"), api.put("/x", {}), api.get("/x")];
    assert.deepEqual(fetch.calls.map(call => call.init.keepalive), [true, true, true, undefined, undefined, undefined]);
    assert.equal("keepalive" in fetch.calls[3].init, false);
    for (const call of fetch.calls) call.resolve(jsonResponse(200, { success: true }));
    await Promise.all(writes);
});

test("ApiError keeps the job that applied a used plan (plan-used, System S7)", () => {
    const body = { job: { id: "b".repeat(32), title: "Optimize database" } };
    const used = new ApiError(409, "This plan was already applied.", "plan-used", null, body);
    assert.deepEqual(used.job, { id: "b".repeat(32), title: "Optimize database" });
    assert.notEqual(used.job, body.job, "server objects must be copied");
    // The applying job may already be pruned: its title is null, its id still links Activity.
    assert.deepEqual(new ApiError(409, "used", "plan-used", null, { job: { id: "c".repeat(32), title: null } }).job,
        { id: "c".repeat(32), title: null });
    assert.equal(new ApiError(409, "busy", "busy", null, body).job, null);
    for (const job of [null, "x", { id: 1, title: "t" }, { id: "a b", title: "t" }, { id: "a", title: 7 },
        { id: "a", title: "x".repeat(201) }, { id: "a".repeat(65), title: "t" }]) {
        assert.equal(new ApiError(409, "used", "plan-used", null, { job }).job, null);
    }
});

test("ApiError keeps a validated running job on busy and a validated source on invalid-source", () => {
    const body = { running: { id: "a".repeat(32), title: "Scan for metadata" }, source: "output/gone" };
    const busy = new ApiError(409, "busy", "busy", null, body);
    assert.deepEqual([busy.running, busy.source], [{ id: "a".repeat(32), title: "Scan for metadata" }, null]);
    assert.notEqual(busy.running, body.running, "server objects must be copied");
    const invalid = new ApiError(400, "refused", "invalid-source", null, body);
    assert.deepEqual([invalid.running, invalid.source], [null, "output/gone"]);
    for (const running of [null, "x", { id: 1, title: "t" }, { id: "a b", title: "t" }, { id: "a", title: "" },
        { id: "a", title: "x".repeat(201) }, { id: "a".repeat(65), title: "t" }]) {
        assert.equal(new ApiError(409, "busy", "busy", null, { running }).running, null);
    }
    for (const source of [null, 7, "", "x".repeat(4097)]) {
        assert.equal(new ApiError(400, "refused", "invalid-source", null, { source }).source, null);
    }
    assert.deepEqual([new ApiError(409, "old caller").running, new ApiError(409, "old caller").source], [null, null]);
});

test("ApiError says whether the server itself refused (a parsed success:false body; S7 review I1)", () => {
    assert.equal(new ApiError(400, "bad", null, null, { success: false, error: "bad" }).refused, true);
    assert.equal(new ApiError(409, "busy", "busy", null, { success: false, code: "busy" }).refused, true);
    // A proxy's page, a body that was not JSON, or no body at all: nothing says the server refused.
    for (const body of [null, undefined, "<html>", { success: true }, [], { error: "x" }]) {
        assert.equal(new ApiError(504, "Gateway Timeout", null, null, body).refused, false);
    }
});
