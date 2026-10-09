// Run with: node --test tests/js/
const test = require("node:test");
const assert = require("node:assert/strict");
const {
    initialState,
    reduceScanEvent,
    fromStatus,
    dismissAction,
    progressLabel,
} = require("../../web/js/scan-progress.js");

test("initialState is idle with zero counts", () => {
    assert.deepEqual(initialState(), {
        status: "idle", progress: 0, statusText: "", processed: 0, found: 0, added: 0, linked: 0, message: "",
    });
});

test("a progress event moves the scan to running and copies the counts", () => {
    const before = initialState();
    const after = reduceScanEvent(before, {
        type: "progress", progress: 42, status: "Processing file 84/200...", processed: 84, found: 12,
    });
    assert.equal(after.status, "running");
    assert.equal(after.progress, 42);
    assert.equal(after.statusText, "Processing file 84/200...");
    assert.equal(after.processed, 84);
    assert.equal(after.found, 12);
    assert.equal(before.status, "idle", "reducer must not mutate its input");
});

test("a complete event finishes the scan with the final totals", () => {
    const running = reduceScanEvent(initialState(), { type: "progress", progress: 90, status: "x", processed: 9, found: 1 });
    const done = reduceScanEvent(running, { type: "complete", processed: 10, found: 3, added: 2, linked: 1 });
    assert.equal(done.status, "done");
    assert.equal(done.progress, 100);
    assert.deepEqual([done.processed, done.found, done.added, done.linked], [10, 3, 2, 1]);
});

test("an error event fails the scan and keeps the server message", () => {
    const failed = reduceScanEvent(initialState(), { type: "error", message: "No output directories found." });
    assert.equal(failed.status, "failed");
    assert.equal(failed.message, "No output directories found.");
});

test("unknown events leave the state untouched", () => {
    const state = reduceScanEvent(initialState(), { type: "progress", progress: 5, status: "s", processed: 1, found: 0 });
    assert.equal(reduceScanEvent(state, { type: "mystery" }), state);
    assert.equal(reduceScanEvent(state, null), state);
});

test("fromStatus rebuilds a running scan from the status endpoint", () => {
    const state = fromStatus({
        running: true,
        last_event: { type: "progress", progress: 30, status: "Processing file 3/10...", processed: 3, found: 1 },
    });
    assert.equal(state.status, "running");
    assert.equal(state.progress, 30);
    assert.equal(state.processed, 3);
});

test("fromStatus is idle when nothing is running, even if a last event exists", () => {
    assert.equal(fromStatus({ running: false, last_event: { type: "complete", processed: 10 } }).status, "idle");
    assert.equal(fromStatus({ running: false, last_event: null }).status, "idle");
    assert.equal(fromStatus(null).status, "idle");
});

test("dismissing the modal minimizes a running scan and closes otherwise", () => {
    const running = reduceScanEvent(initialState(), { type: "progress", progress: 1, status: "s", processed: 0, found: 0 });
    assert.equal(dismissAction(running), "minimize");
    assert.equal(dismissAction(initialState()), "close");
    assert.equal(dismissAction(reduceScanEvent(running, { type: "complete" })), "close");
});

test("progressLabel summarises a running scan for the minimized pill", () => {
    const running = reduceScanEvent(initialState(), { type: "progress", progress: 42, status: "s", processed: 84, found: 12 });
    assert.equal(progressLabel(running), "Scanning 42% · 84 files · 12 prompts");
    assert.equal(progressLabel(initialState()), "");
});
