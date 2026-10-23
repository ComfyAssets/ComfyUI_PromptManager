// web/js/workspace/local-session.mjs owns one local selection at a time: it
// classifies the chosen File by its bytes, creates the blob: URL only for a
// classified file, hands an owned handle to a real inspection controller, and
// releases the URL, the handle and the record on replacement, dispose, parent
// abort and page leave.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { raw, ihdr, idat, iend, tEXt } from "./helpers/png-bytes.mjs";
import { createScope } from "../../web/js/shared/toolkit/core.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const PngMetadata = require("../../web/js/png-metadata.js");
const ComfyMetadata = require("../../web/js/comfy-metadata.js");
globalThis.PngMetadata = PngMetadata;
globalThis.ComfyMetadata = ComfyMetadata;
const m = await import("../../web/js/workspace/local-session.mjs").catch((error) => {
    if (error.code !== "ERR_MODULE_NOT_FOUND") throw error;
    return {};
});
const { localFileAdapter } = await import("../../web/js/workspace/local-inspection.mjs");

const FIXTURES = path.join(here, "..", "fixtures", "local-inspect");
const promptFor = (text) => JSON.stringify({ 3: { class_type: "KSampler", inputs: { seed: 7, steps: 20, cfg: 8, sampler_name: "euler", scheduler: "normal", model: ["4", 0], positive: ["6", 0], negative: ["7", 0] } },
    4: { class_type: "CheckpointLoaderSimple", inputs: { ckpt_name: "m.safetensors" } }, 6: { class_type: "CLIPTextEncode", inputs: { text, clip: ["4", 1] } }, 7: { class_type: "CLIPTextEncode", inputs: { text: "blurry", clip: ["4", 1] } } });
const MODIFIED = 1_700_000_000_000;

/** A File that records each slice and lets a test hold slice reads (`gate(index)` returns a promise). */
class RecordingFile extends File {
    constructor(parts, name, { gate, ...options } = {}) {
        super(parts, name, { lastModified: MODIFIED, ...options });
        this.ranges = [];
        this.gate = gate;
    }
    slice(start, end) {
        const index = this.ranges.push([start, end]) - 1;
        const part = super.slice(start, end), gate = this.gate;
        return { size: part.size, arrayBuffer: async () => { if (gate) await gate(index); return part.arrayBuffer(); } };
    }
}
const pngFile = (name, text = "a red fox", options) => new RecordingFile([raw(ihdr(64, 48), idat(), tEXt("prompt", promptFor(text)), iend())], name, { type: "image/png", ...options });
const fixture = (name, as = name) => new RecordingFile([fs.readFileSync(path.join(FIXTURES, name))], as);
function deferred() {
    let resolve, reject;
    const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
    return { promise, resolve, reject };
}
const settle = async () => { for (let i = 0; i < 30; i++) await new Promise((resolve) => setImmediate(resolve)); };

function urls({ fail = false } = {}) {
    const created = [], revoked = [];
    return {
        created, revoked,
        createObjectURL(file) { if (fail) throw new Error("no object URLs"); const url = `blob:http://localhost/${created.length + 1}`; created.push({ url, file }); return url; },
        revokeObjectURL(url) { revoked.push(url); },
    };
}
function pageTarget() {
    const listeners = new Map();
    return {
        listeners,
        addEventListener(type, callback) { listeners.set(type, callback); },
        removeEventListener(type, callback) { if (listeners.get(type) === callback) listeners.delete(type); },
        fire(type) { listeners.get(type)?.({ type }); },
    };
}
function session(options = {}) {
    const parentScope = options.parentScope ?? createScope(), spies = options.urls ?? urls(), page = options.page ?? pageTarget();
    const value = m.createLocalSession({ parentScope, createObjectURL: spies.createObjectURL, revokeObjectURL: spies.revokeObjectURL,
        schedulerYield: () => Promise.resolve(), pageTarget: page, extractFacts: ComfyMetadata.extractGenerationFacts, ...options.overrides });
    return { session: value, parentScope, urls: spies, page };
}
/** Resolves with the first controller state `predicate` accepts. */
function until(controller, predicate) {
    return new Promise((resolve) => {
        let stop = null, done = false;
        stop = controller.subscribe((state) => { if (!done && predicate(state)) { done = true; resolve(state); stop?.(); } });
        if (done) stop();
    });
}
const recorded = (controller) => until(controller, (state) => state.record !== null || state.error);

test("contract", () => {
    assert.equal(typeof m.createLocalSession, "function", "createLocalSession is exported");
    assert.equal(typeof m.createYield, "function", "createYield is exported");
    assert.deepEqual(Object.keys(m.LOCAL_SESSION_MESSAGES).sort(), ["containerOnly", "directory", "multiple", "previewFailed", "unreadable", "unsupported"]);
    const { session: value } = session();
    for (const name of ["select", "retry", "subscribe", "dispose"]) assert.equal(typeof value[name], "function", name);
    assert.equal(value.state.phase, "empty");
    assert.equal(value.state.preview, null);
    assert.equal(value.entry, null);
    assert.equal(typeof value.controller.select, "function");
    value.dispose();
});

test("replacement synchronously clears old capabilities", async () => {
    const { session: value, urls: spies } = session();
    const states = [];
    value.subscribe((state) => states.push({ ...state, revokedAtPublish: spies.revoked.length }));
    // Two different Files with the same name, size and time are two selections.
    const a = pngFile("same.png", "first fox"), b = pngFile("same.png", "other fox");
    assert.equal(a.size, b.size);
    assert.equal(value.select([a]), "accepted");
    assert.equal(value.state.phase, "classifying");
    const first = await recorded(value.controller);
    assert.equal(first.record.prompts.positive.text, "first fox");
    const entryA = value.entry, urlA = spies.created[0].url;
    assert.deepEqual([entryA.filename, entryA.is_video], ["same.png", false]);
    assert.deepEqual(value.state.preview, { url: urlA, media: "image" });
    assert.equal(localFileAdapter.originalDownload(entryA).url, urlA);

    const before = states.length;
    assert.equal(value.select([b]), "accepted");
    // Synchronously: no record, no entry, no preview, the old URL revoked once and the old handle foreign.
    assert.equal(value.controller.state.record, null);
    assert.equal(value.entry, null);
    assert.equal(value.state.preview, null);
    assert.deepEqual(spies.revoked, [urlA]);
    for (const method of ["originalDownload", "mediaUrl", "key"]) assert.throws(() => localFileAdapter[method](entryA), TypeError, method);
    assert.throws(() => localFileAdapter.originalDownload({ ...entryA }), TypeError, "a copy of a released handle is foreign too");
    // The preview left the published state before its URL was revoked.
    assert.deepEqual([states[before].phase, states[before].preview, states[before].revokedAtPublish], ["classifying", null, 0]);
    const second = await recorded(value.controller);
    assert.equal(second.record.prompts.positive.text, "other fox");
    assert.notEqual(value.entry.key, entryA.key);
    assert.equal(spies.revoked.length, 1);

    // A read still in flight when the next file arrives never publishes.
    const held = deferred(), published = [];
    value.controller.subscribe((state) => { if (state.record) published.push(state.record.identity.key); });
    const slow = pngFile("slow.png", "late fox", { gate: (index) => (index < 2 ? Promise.resolve() : held.promise) });
    value.select([slow]);
    await until(value.controller, (state) => state.loading);
    const slowKey = value.entry.key;
    assert.equal(value.select([pngFile("next.png", "current fox")]), "accepted");
    held.resolve();
    const current = await recorded(value.controller);
    await settle();
    assert.equal(current.record.prompts.positive.text, "current fox");
    assert.ok(!published.includes(slowKey), "the replaced read never published");
    assert.equal(value.controller.state.record.prompts.positive.text, "current fox");
    value.dispose();
    assert.equal(new Set(spies.revoked).size, spies.revoked.length, "each URL revoked once");
    assert.deepEqual(spies.revoked.sort(), spies.created.map((item) => item.url).sort());
});

test("cancel and invalid drops keep the previous selection", async () => {
    const { session: value, urls: spies } = session();
    value.select([pngFile("kept.png", "kept fox")]);
    await recorded(value.controller);
    const entry = value.entry, state = value.state;
    const directory = new File([], "folder");
    const drops = [
        [[], "rejected-empty"], [{ files: [], items: [] }, "rejected-empty"], [null, "ignored"], [undefined, "ignored"],
        [[pngFile("one.png"), pngFile("two.png")], "rejected-multiple"],
        [{ files: [pngFile("one.png"), pngFile("two.png")], items: [{ kind: "file" }, { kind: "file" }] }, "rejected-multiple"],
        [{ files: [directory], items: [{ kind: "file", webkitGetAsEntry: () => ({ isDirectory: true, isFile: false }) }] }, "rejected-directory"],
        [{ files: [], items: [{ kind: "string" }] }, "rejected-empty"],
    ];
    for (const [input, outcome] of drops) {
        assert.equal(value.select(input), outcome, JSON.stringify(input));
        assert.equal(value.entry, entry);
        assert.equal(value.state, state);
        assert.equal(value.controller.state.record.prompts.positive.text, "kept fox");
    }
    assert.equal(spies.created.length, 1);
    assert.deepEqual(spies.revoked, []);
    // One File in a DataTransfer whose entry is a file is accepted.
    assert.equal(value.select({ files: [pngFile("dropped.png", "dropped fox")], items: [{ kind: "file", webkitGetAsEntry: () => ({ isDirectory: false, isFile: true }) }] }), "accepted");
    assert.equal((await recorded(value.controller)).record.prompts.positive.text, "dropped fox");
    value.dispose();
});

test("unclassified input creates no object URL", async () => {
    const { session: value, urls: spies } = session();
    const text = (body, name) => new RecordingFile([body], name);
    for (const file of [text("plain text", "a.png"), text("<!doctype html><p>hi", "a.mp4"), text('<svg xmlns="http://www.w3.org/2000/svg"/>', "a.png"),
        text("#EXTM3U\n#EXT-X-VERSION:3\n", "a.mp4"), new RecordingFile([], "empty.png")]) {
        assert.equal(value.select([file]), "accepted");
        await settle();
        assert.equal(value.state.phase, "unsupported", file.name);
        assert.equal(value.state.message, m.LOCAL_SESSION_MESSAGES.unsupported);
        assert.equal(value.state.preview, null);
        assert.equal(value.entry, null);
        assert.equal(value.controller.state.record, null);
        assert.ok(file.ranges.every(([start, end]) => start === 0 && end <= 32), "only the prefix is read");
    }
    // A file that cannot be read after choosing (removed or denied) is not an unsupported type: when the browser
    // declares a supported type it opens the inspector's unreadable state, with no preview and no URL (ledger ruling).
    const gone = pngFile("gone.png", "fox", { gate: () => Promise.reject(new DOMException("The file was removed", "NotReadableError")) });
    assert.equal(value.select([gone]), "accepted");
    // The read yields between its steps (local-inspection.mjs): wait for the answer, not a fixed number of turns.
    const unreadable = await recorded(value.controller);
    assert.ok(unreadable.record, "the unreadable record is published");
    assert.deepEqual([value.state.phase, value.state.message, value.state.preview], ["ready", m.LOCAL_SESSION_MESSAGES.unreadable, null]);
    assert.equal(m.LOCAL_SESSION_MESSAGES.unreadable, "Embedded metadata could not be read.");
    assert.equal(value.entry.filename, "gone.png");
    assert.equal(unreadable.record.metadata.status, "unreadable");
    assert.equal(unreadable.record.download, null);
    assert.equal(spies.created.length, 0);
    // A prefix read that fails for another reason than the browser's read failure is not claimed as unreadable.
    const broken = pngFile("broken.png", "fox", { gate: () => Promise.reject(new TypeError("not a read failure")) });
    assert.equal(value.select([broken]), "accepted");
    await settle();
    assert.deepEqual([value.state.phase, value.state.message, value.entry], ["unsupported", m.LOCAL_SESSION_MESSAGES.unsupported, null]);
    // A read failure for a selection already replaced publishes nothing for it.
    const slow = deferred();
    const replaced = pngFile("replaced.png", "fox", { gate: () => slow.promise });
    value.select([replaced]);
    await settle();
    value.select([new RecordingFile([new Uint8Array(4)], "next.txt")]);
    slow.reject(new DOMException("The file was removed", "NotReadableError"));
    await settle();
    assert.deepEqual([value.state.phase, value.entry], ["unsupported", null]);
    // An unreadable file the browser declares no supported type for stays unsupported.
    const unknown = new RecordingFile([new Uint8Array(8)], "gone.bin", { gate: () => Promise.reject(new DOMException("The file was removed", "NotReadableError")) });
    assert.equal(value.select([unknown]), "accepted");
    await settle();
    assert.deepEqual([value.state.phase, value.state.message, value.entry], ["unsupported", m.LOCAL_SESSION_MESSAGES.unsupported, null]);
    assert.equal(spies.created.length, 0);
    // A real MP4 named .png is a video by its bytes.
    value.select([fixture("core-faststart.mp4", "clip.png")]);
    await settle();
    assert.equal(value.state.phase, "ready");
    assert.equal(value.state.preview.media, "video");
    assert.equal(value.entry.is_video, true);
    assert.equal(spies.created.length, 1);
    value.dispose();
});

test("video record publishes even if preview fails", async () => {
    const { session: value } = session({ urls: urls({ fail: true }) });
    value.select([fixture("core-tags.webm")]);
    const state = await recorded(value.controller);
    assert.equal(value.state.phase, "ready");
    assert.equal(value.state.preview, null);
    assert.equal(value.state.message, m.LOCAL_SESSION_MESSAGES.previewFailed);
    assert.equal(value.entry.is_video, true);
    assert.equal(state.error, false);
    assert.equal(state.record.file.media_type, "video");
    assert.equal(state.record.metadata.status, "available");
    assert.equal(state.record.generation.seed.value, "7");
    assert.equal(state.record.download, null, "no original without an object URL");
    value.dispose();
    // AVI keeps its preview and says its metadata is not read.
    const avi = session();
    avi.session.select([new RecordingFile([Buffer.from("RIFF"), new Uint8Array(4), Buffer.from("AVI LIST"), new Uint8Array(64)], "clip.avi")]);
    await recorded(avi.session.controller);
    assert.equal(avi.session.state.message, m.LOCAL_SESSION_MESSAGES.containerOnly);
    assert.equal(avi.session.state.preview.media, "video");
    avi.session.dispose();
});

test("disposal settles a pending fallback yield", async () => {
    const timers = new Map(), cleared = [];
    let serial = 0;
    const setTimer = (callback, delay) => { assert.equal(delay, 0); timers.set(++serial, callback); return serial; };
    const clearTimer = (token) => { cleared.push(token); timers.delete(token); };
    // The yield itself: a timer that rejects with AbortError on abort and removes its listener.
    const yieldControl = m.createYield({ schedulerYield: null, setTimer, clearTimer });
    const controller = new AbortController();
    const pending = yieldControl(controller.signal);
    assert.equal(timers.size, 1);
    controller.abort();
    await assert.rejects(pending, { name: "AbortError" });
    assert.deepEqual(cleared, [1], "the timer is cleared on abort (the once listener is gone with it)");
    // A turn that completes removes its abort listener, so a long-lived signal does not collect them.
    const kept = new AbortController(), removed = [];
    const remove = kept.signal.removeEventListener.bind(kept.signal);
    kept.signal.removeEventListener = (type, callback) => { removed.push(type); remove(type, callback); };
    const ok = yieldControl(kept.signal);
    timers.get(2)();
    await ok;
    assert.deepEqual(removed, ["abort"]);
    await assert.rejects(yieldControl(AbortSignal.abort()), { name: "AbortError" });
    // The session: a selection waiting on the fallback yield is settled by dispose and does no work.
    timers.clear(); cleared.length = 0;
    const { session: value, urls: spies } = session({ overrides: { schedulerYield: null, setTimer, clearTimer } });
    const file = pngFile("a.png");
    value.select([file]);
    assert.equal(timers.size, 1);
    const [token] = timers.keys();
    value.dispose();
    assert.deepEqual(cleared, [token]);
    await settle();
    assert.deepEqual(file.ranges, []);
    assert.equal(spies.created.length, 0);
});

test("old scheduler.yield continuation is fenced", async () => {
    const turns = [];
    const { session: value, urls: spies } = session({ overrides: { schedulerYield: () => { const turn = deferred(); turns.push(turn); return turn.promise; } } });
    const a = pngFile("a.png", "old fox"), b = pngFile("b.png", "new fox");
    value.select([a]);
    value.select([b]);
    assert.equal(turns.length, 2);
    turns[0].resolve();
    await settle();
    assert.deepEqual(a.ranges, [], "the replaced selection never reads");
    assert.equal(spies.created.length, 0);
    assert.equal(value.state.phase, "classifying");
    turns[1].resolve();
    const state = await recorded(value.controller);
    assert.equal(state.record.prompts.positive.text, "new fox");
    assert.deepEqual(spies.created.map((item) => item.file), [b]);
    // A continuation that resumes after dispose is fenced too.
    value.select([pngFile("c.png")]);
    const late = turns.at(-1);
    value.dispose();
    late.resolve();
    await settle();
    assert.equal(spies.created.length, 1);
});

test("retry rereads identically", async () => {
    const { session: value } = session();
    const file = pngFile("a.png", "retry fox");
    value.select([file]);
    const first = await recorded(value.controller);
    const classified = 1, reads = file.ranges.slice(classified);
    assert.ok(reads.length > 1);
    const key = value.entry.key;
    await value.retry();
    const again = value.controller.state;
    assert.deepEqual(file.ranges.slice(classified + reads.length), reads, "the same slice ranges");
    assert.equal(again.record.identity.key, key);
    assert.equal(again.record.identity.key, first.record.identity.key);
    assert.equal(again.record.prompts.positive.text, "retry fox");
    value.dispose();
    assert.equal(value.retry(), undefined, "a disposed session never reads");
    assert.equal(file.ranges.length, classified + 2 * reads.length);
});

test("already disposed parent does zero work", () => {
    const parentScope = createScope();
    parentScope.dispose();
    const fail = (name) => () => assert.fail(`${name} called`);
    const page = { addEventListener: fail("addEventListener"), removeEventListener: fail("removeEventListener") };
    const value = m.createLocalSession({ parentScope, createObjectURL: fail("createObjectURL"), revokeObjectURL: fail("revokeObjectURL"),
        schedulerYield: fail("schedulerYield"), setTimer: fail("setTimer"), clearTimer: fail("clearTimer"), pageTarget: page });
    const file = pngFile("a.png");
    assert.equal(value.select([file]), "ignored");
    assert.equal(value.retry(), undefined);
    assert.equal(value.entry, null);
    assert.equal(value.state.phase, "empty");
    const unsubscribe = value.subscribe(fail("listener"));
    unsubscribe();
    value.dispose(); value.dispose();
    assert.deepEqual(file.ranges, []);
});

test("dispose, parent abort and page leave each release the URL once", async () => {
    // dispose: the preview is cleared for subscribers before its URL is revoked.
    const first = session(), seen = [];
    first.session.select([pngFile("a.png")]);
    await recorded(first.session.controller);
    first.session.subscribe((state) => seen.push([state.phase, state.preview, first.urls.revoked.length]));
    first.session.dispose(); first.session.dispose();
    assert.deepEqual(seen.at(-1), ["empty", null, 0]);
    assert.deepEqual(first.urls.revoked, [first.urls.created[0].url]);
    assert.equal(first.session.select([pngFile("b.png")]), "ignored");
    assert.equal(first.page.listeners.size, 0, "the page listener is removed");
    // Parent abort disposes.
    const second = session();
    second.session.select([pngFile("a.png")]);
    await recorded(second.session.controller);
    second.parentScope.dispose();
    assert.deepEqual(second.urls.revoked, [second.urls.created[0].url]);
    assert.equal(second.session.controller.state.record, null);
    assert.equal(second.page.listeners.size, 0);
    // pagehide releases the selection; a page restored from the back/forward cache can choose again.
    const third = session();
    third.session.select([pngFile("a.png")]);
    await recorded(third.session.controller);
    const entry = third.session.entry;
    third.page.fire("pagehide");
    assert.deepEqual(third.urls.revoked, [third.urls.created[0].url]);
    assert.equal(third.session.entry, null);
    assert.equal(third.session.state.phase, "empty");
    assert.equal(third.session.controller.state.record, null);
    assert.throws(() => localFileAdapter.originalDownload(entry), TypeError);
    third.page.fire("pagehide");
    assert.equal(third.urls.revoked.length, 1);
    assert.equal(third.session.select([pngFile("b.png", "after return")]), "accepted");
    assert.equal((await recorded(third.session.controller)).record.prompts.positive.text, "after return");
    third.session.dispose();
    assert.equal(third.urls.revoked.length, 2);
    // A page that owns page leave passes no pageTarget and ends the selection with clear(), as pagehide would.
    const owned = session({ overrides: { pageTarget: null } });
    owned.session.select([pngFile("a.png")]);
    await recorded(owned.session.controller);
    assert.equal(owned.page.listeners.size, 0, "no pagehide listener of its own");
    owned.session.clear();
    assert.deepEqual(owned.urls.revoked, [owned.urls.created[0].url]);
    assert.equal(owned.session.entry, null);
    assert.equal(owned.session.state.phase, "empty");
    owned.session.clear();
    assert.equal(owned.urls.revoked.length, 1, "clear is idempotent");
    assert.equal(owned.session.select([pngFile("b.png")]), "accepted");
    await recorded(owned.session.controller);
    owned.session.dispose();
    owned.session.clear();
    assert.equal(owned.urls.revoked.length, 2);
});
