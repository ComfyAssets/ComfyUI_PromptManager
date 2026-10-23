// web/js/workspace/local-inspection.mjs turns one chosen File's local reads into
// the inspection wire the workspace already renders, fits it into the 8 MiB
// response budget exactly as the server does, and plugs into the shared
// inspection controller, inspector and viewer through localFileAdapter.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { raw, ihdr, idat, iend, tEXt, iTXt, utf8 } from "./helpers/png-bytes.mjs";
import { createInspectionController, toInspectionRecord, projectInspectionPayload } from "../../web/js/workspace/inspection-state.mjs";
import { inspectionDownloads } from "../../web/js/workspace/file-inspector.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const PngMetadata = require("../../web/js/png-metadata.js");
const ComfyMetadata = require("../../web/js/comfy-metadata.js");
const { extractGenerationFacts } = ComfyMetadata;
// The page loads both as scripts; the legacy fill and the PNG reader find them on the global.
globalThis.PngMetadata = PngMetadata;
globalThis.ComfyMetadata = ComfyMetadata;
// The page's API client and the node table it serves (local-semantics.mjs fetches it once).
const SEMANTICS = fs.readFileSync(path.join(here, "..", "..", "web", "js", "inspection-semantics.json"), "utf8");
globalThis.window = { ApiClient: { resolveUrl: (value) => value } };
// A test can hold the table request (`tableGate`) to stand for a slow or stalled proxy.
let tableGate = null;
globalThis.fetch = async (url) => {
    if (url !== "/prompt_manager/js/inspection-semantics.json") throw new Error(`unexpected request ${url}`);
    if (tableGate) await tableGate;
    return new Response(SEMANTICS, { status: 200 });
};
const m = await import("../../web/js/workspace/local-inspection.mjs").catch((error) => {
    if (error.code !== "ERR_MODULE_NOT_FOUND") throw error;
    return {};
});

const FIXTURES = path.join(here, "..", "fixtures", "local-inspect");
const PARITY = path.join(here, "..", "fixtures", "inspection_parity");
const PROMPT = '{"3":{"class_type":"KSampler","inputs":{"seed":7,"steps":20,"cfg":8.0,"sampler_name":"euler","scheduler":"normal","denoise":1.0,"model":["4",0],"positive":["6",0],"negative":["7",0],"latent_image":["5",0]}},"4":{"class_type":"CheckpointLoaderSimple","inputs":{"ckpt_name":"synthetic-v1.safetensors"}},"5":{"class_type":"EmptyLatentImage","inputs":{"width":512,"height":512,"batch_size":1}},"6":{"class_type":"CLIPTextEncode","inputs":{"text":"a red fox in snow, detailed fur","clip":["4",1]}},"7":{"class_type":"CLIPTextEncode","inputs":{"text":"blurry, lowres","clip":["4",1]}}}';
const WORKFLOW = '{"last_node_id":7,"nodes":[{"id":3,"type":"KSampler","widgets_values":[7,"fixed",20,8.0,"euler","normal",1.0]}],"links":[],"version":0.4}';
const CONTAINER_WORKFLOW = "Original workflow JSON text is unavailable for this container record. Download metadata or the original file.";
const LIMIT = 8388608;
const plain = (value) => JSON.parse(JSON.stringify(value));
const bytes = (value) => Buffer.byteLength(JSON.stringify(value), "utf8");

function pngFile(name, chunks, { lastModified = 1_700_000_000_123 } = {}) {
    return new File([raw(ihdr(640, 480), idat(), ...chunks, iend())], name, { type: "image/png", lastModified });
}
const fixtureFile = (name, directory = FIXTURES) => new File([fs.readFileSync(path.join(directory, name))], name, { lastModified: 1_700_000_000_000 });
const classify = async (file) => PngMetadata.classifyPrefix(new Uint8Array(await file.slice(0, 32).arrayBuffer()));
async function handleFor(file, { url = "blob:http://host.test/11111111-2222-3333-4444-555555555555", filename } = {}) {
    return m.createLocalFileHandle({ file, url, classification: await classify(file), filename });
}
async function recordFor(file, options) {
    const handle = await handleFor(file, options);
    const wire = await m.localFileAdapter.read(handle);
    return { handle, wire, record: m.localFileAdapter.toRecord(wire, handle, extractGenerationFacts) };
}

test("contract", () => {
    for (const name of ["buildLocalPayload", "fitLocalPayload", "createLocalFileHandle", "readLocalFile", "toInspectionRead", "sanitizeLocalFilename"]) {
        assert.equal(typeof m[name], "function", `${name} is exported`);
    }
    assert.equal(typeof m.localFileAdapter, "object");
    for (const name of ["validate", "key", "scope", "inspectionPath", "read", "toRecord", "originalDownload", "mediaUrl"]) {
        assert.equal(typeof m.localFileAdapter[name], "function", `adapter.${name}`);
    }
    assert.ok(Object.isFrozen(m.localFileAdapter));
});

// M3: the first read waits for the page's one table request; an aborted, replaced or cleared selection must not.
test("a read waiting for the node table ends with its selection", async () => {
    let release;
    tableGate = new Promise((resolve) => { release = resolve; });
    try {
        const file = pngFile("slow.png", [tEXt("prompt", PROMPT)]);
        const aborted = new AbortController();
        const reading = m.localFileAdapter.read(await handleFor(file), { signal: aborted.signal });
        const settled = (promise) => Promise.race([promise.then(() => "resolved", (error) => error.name), new Promise((resolve) => setTimeout(() => resolve("pending"), 200))]);
        await new Promise((resolve) => setTimeout(resolve, 20));
        aborted.abort();
        assert.equal(await settled(reading), "AbortError");
        // Through the controller: clearing the selection ends its read; nothing is published for it.
        const controller = createInspectionController({ api: null, adapter: m.localFileAdapter, extractFacts: extractGenerationFacts });
        const states = [];
        controller.subscribe((state) => states.push(state));
        const selected = controller.select(await handleFor(file));
        await new Promise((resolve) => setTimeout(resolve, 20));
        controller.clear();
        assert.equal(await settled(selected), "resolved");
        assert.ok(states.every((state) => state.record === null && state.error === false), "no record or error for a cleared selection");
        controller.dispose();
    } finally {
        tableGate = null;
        release();
    }
    // The shared request still lands for the next selection.
    const { wire } = await recordFor(pngFile("next.png", [tEXt("prompt", PROMPT)]));
    assert.equal(wire.resolution.state, "resolved");
});

test("a PNG File becomes the server-shaped wire with local provenance", async () => {
    const file = pngFile("Fox Render.PNG", [tEXt("prompt", PROMPT), tEXt("workflow", WORKFLOW)]);
    const { wire, record, handle } = await recordFor(file);
    assert.deepEqual(wire.file, { observation: "selected-local-file", source_label: "Selected local file", media_type: "image", extension: ".png", format: "PNG",
        size: file.size, width: 640, height: 480, modified_time: 1_700_000_000.123 });
    assert.equal(wire.success, true);
    assert.equal(wire.inspection_version, 1);
    // The roles the server's inspector resolves for the same records (local-resolver.mjs).
    assert.deepEqual(wire.prompts, {
        positive: { status: "resolved", text: "a red fox in snow, detailed fur", source: "graph-inferred" },
        negative: { status: "resolved", text: "blurry, lowres", source: "graph-inferred" },
    });
    assert.deepEqual(wire.resolution, { state: "resolved", sampler_id: "3", reason: "", reason_code: null, node: null });
    assert.deepEqual(wire.library, { status: "unavailable", scope: "saved-library", tags: [], category_scope: null, tags_scope: null });
    assert.equal(wire.metadata.status, "available");
    assert.deepEqual(wire.metadata.raw, { prompt: PROMPT, workflow: WORKFLOW });
    assert.deepEqual(wire.metadata.record_formats, { prompt: "original-text", workflow: "original-text" });
    assert.equal(wire.metadata.prompt_graph.status, "parsed");
    assert.equal(plain(wire.metadata.prompt_graph.value)["3"].inputs.cfg, "8.0");
    assert.equal(wire.metadata.workflow_graph.status, "parsed");
    assert.equal(wire.metadata.parameters, null);
    for (const key of Object.keys(wire)) assert.ok(["success", "inspection_version", "file", "prompts", "resolution", "metadata", "library"].includes(key), key);
    // The existing projection renders it: facts from the API graph, legacy fill for the roles, no library link.
    assert.deepEqual(record.identity, { kind: "local-file", key: handle.key, path: "Fox Render.PNG" });
    assert.equal(record.file.observation, "selected-local-file");
    assert.equal(record.generation.seed.value, "7");
    assert.equal(record.generation.cfg.value, "8.0");
    assert.equal(record.prompts.positive.text, "a red fox in snow, detailed fur");
    assert.equal(record.prompts.positive.source, "graph-inferred");
    assert.equal(record.prompts.negative.text, "blurry, lowres");
    assert.equal(record.historyTarget, null);
    assert.deepEqual(record.download, { url: "blob:http://host.test/11111111-2222-3333-4444-555555555555", filename: "Fox Render.PNG" });
    const downloads = inspectionDownloads(record);
    assert.equal(downloads.metadata.available, true);
    assert.equal(downloads.workflow.bodyText, WORKFLOW);
    assert.equal(downloads.metadata.filename, "Fox Render.metadata.json");
    const exported = JSON.parse(downloads.metadata.bodyText);
    assert.equal(exported.identity.kind, "local-file");
    assert.equal(exported.file.source_label, "Selected local file");
});

test("roles answer as the server's inspector: unreadable, unavailable table, ambiguous", async () => {
    const png = { media: "image", kind: "png" };
    const unreadable = m.buildLocalPayload({ read: null, file: { name: "a.png" }, classification: png });
    assert.deepEqual(unreadable.resolution, { state: "unresolved", sampler_id: null, reason: "Media metadata could not be read", reason_code: null, node: null });
    assert.equal(unreadable.prompts.positive.message, "Metadata in this file could not be read.");
    const records = { prompt: PROMPT };
    const read = { status: "complete", records, limited: [], width: 2, height: 3 };
    // Without a table (it could not be fetched) the roles stay open, with the server's own line.
    const missing = m.buildLocalPayload({ read, file: { name: "a.png" }, classification: png });
    assert.equal(missing.resolution.reason, "Inspection node semantics are unavailable");
    assert.equal(missing.prompts.positive.message, "The inspector's node table is unavailable.");
    const semantics = await (await import("../../web/js/workspace/local-semantics.mjs")).loadInspectionSemantics();
    const graph = JSON.parse(PROMPT);
    graph["9"] = { ...graph["3"], inputs: { ...graph["3"].inputs, positive: ["8", 0] } };
    graph["8"] = { class_type: "CLIPTextEncode", inputs: { text: "a cat", clip: ["4", 1] } };
    const several = m.buildLocalPayload({ read: { ...read, records: { prompt: JSON.stringify(graph) } }, file: { name: "a.png" }, classification: png, semantics });
    assert.equal(several.resolution.reason, "Multiple sampler branches");
    assert.deepEqual(several.prompts.positive, { status: "ambiguous", text: null, source: null });
    // The legacy fill then applies exactly as it does to the server's answer for the same bytes in Images.
    const handle = await handleFor(pngFile("grid.png", [tEXt("prompt", JSON.stringify(graph))]));
    const local = m.localFileAdapter.toRecord(several, handle, extractGenerationFacts);
    const asServed = { ...several, file: { ...several.file, observation: "current-file", source_label: "output" } };
    const served = projectInspectionPayload(asServed, { kind: "rooted-file", key: "0:grid.png", root_index: 0, path: "grid.png", root_scope: null }, null, extractGenerationFacts);
    assert.deepEqual(local.prompts, served.prompts);
    assert.deepEqual(local.generation, served.generation);
});

test("file facts follow the server: extension like Path.suffix, null when unknown", async () => {
    const cases = [["a.b.PnG", ".png"], [".hidden", ""], ["noext", ""], ["dot.", ""], ["x.tar.gz", ".gz"]];
    for (const [name, extension] of cases) {
        const wire = m.buildLocalPayload({ read: { status: "complete", records: {}, limited: [], width: 2, height: 3 }, file: { name, size: 10, lastModified: 0 }, classification: { media: "image", kind: "png" } });
        assert.equal(wire.file.extension, extension, name);
        assert.equal(wire.file.modified_time, 0);
    }
    const sparse = m.buildLocalPayload({ read: { status: "complete", records: {}, limited: [], width: 2, height: 3 }, file: { name: "a.png" }, classification: { media: "image", kind: "png" } });
    assert.deepEqual([sparse.file.size, sparse.file.modified_time], [null, null]);
    assert.equal(m.buildLocalPayload({ read: { status: "complete", records: {}, limited: [], width: 2, height: 3 }, file: { name: "a.png", size: -1, lastModified: NaN }, classification: { media: "image", kind: "png" } }).file.size, null);
    assert.throws(() => m.buildLocalPayload({ read: null, file: { name: "a" }, classification: null }), TypeError);
    assert.throws(() => m.buildLocalPayload({ read: null, file: { name: "a" }, classification: { media: "text", kind: "svg" } }), TypeError);
});

test("sanitized filenames carry no separators or control characters", () => {
    assert.equal(m.sanitizeLocalFilename("../a/b\\c\u0000\u001f\u007f.png"), ".._a_b_c.png");
    assert.equal(m.sanitizeLocalFilename("  spaced .png  "), "spaced .png");
    assert.equal(m.sanitizeLocalFilename(""), "file");
    assert.equal(m.sanitizeLocalFilename(undefined), "file");
    assert.equal(m.sanitizeLocalFilename("\u0000"), "file");
    assert.equal([...m.sanitizeLocalFilename("😀".repeat(300))].length, 255);
});

test("unreadable PNG metadata is unreadable, not absent", async () => {
    const { wire, record } = await recordFor(pngFile("bad.png", [tEXt("prompt", PROMPT, { crc: 0 })]));
    assert.equal(wire.metadata.status, "unreadable");
    assert.deepEqual([wire.file.format, wire.file.width, wire.file.height], [null, null, null]);
    assert.equal(record.generation.seed.reason, "metadata-unreadable");
    assert.equal(record.prompts.positive.status, "unresolved");
    assert.equal(JSON.parse(inspectionDownloads(record).metadata.bodyText).metadata.status, "unreadable");
    const limited = await recordFor(pngFile("big.png", [tEXt("prompt", PROMPT), tEXt("workflow", "[" + "0,".repeat(2_097_153) + "]")]));
    assert.deepEqual(limited.wire.metadata.record_formats, { prompt: "original-text", workflow: "limited" });
    assert.equal(limited.wire.metadata.workflow_graph.status, "limited");
    assert.equal(limited.record.metadata.record_formats.workflow, "limited");
});

test("JPEG and WebP previews have header facts and absent metadata", async () => {
    for (const [name, format, width, height] of [["tiny.jpg", "JPEG", 48, 32], ["tiny.webp", "WEBP", 40, 24]]) {
        const { wire, record } = await recordFor(fixtureFile(name));
        assert.deepEqual([wire.file.format, wire.file.width, wire.file.height, wire.file.media_type], [format, width, height, "image"], name);
        assert.equal(wire.metadata.status, "absent");
        assert.equal(record.generation.seed.status, "not-recorded");
    }
});

test("video payloads", async () => {
    const legacy = await recordFor(fixtureFile("legacy-comment.mp4"));
    assert.deepEqual([legacy.wire.file.media_type, legacy.wire.file.format, legacy.wire.file.width, legacy.wire.file.extension], ["video", null, null, ".mp4"]);
    assert.equal(legacy.wire.metadata.record_formats.workflow, "reserialized-json");
    assert.equal(legacy.wire.metadata.record_formats.prompt, "original-text");
    assert.equal(legacy.wire.metadata.workflow_graph.status, "parsed");
    assert.equal(inspectionDownloads(legacy.record).workflow.available, false);
    assert.equal(inspectionDownloads(legacy.record).workflow.reason, CONTAINER_WORKFLOW);
    assert.equal(legacy.record.prompts.positive.text, "a red fox in snow, detailed fur");
    // Ruling M1: a Matroska COMMENT tag is not the exact-case `comment` envelope, so it is absent, as Images reports it.
    const webm = await recordFor(fixtureFile("legacy-comment.webm"));
    assert.equal(webm.wire.metadata.status, "absent");
    const tags = await recordFor(fixtureFile("core-tags.webm"));
    assert.deepEqual(tags.wire.metadata.record_formats, { prompt: "original-text", workflow: "original-text" });
    assert.equal(tags.record.generation.seed.value, "7");
    // AVI and ASF are recognised, previewable, and never read.
    const avi = new File([utf8("RIFF"), new Uint8Array([0, 0, 0, 0]), utf8("AVI LIST"), new Uint8Array(64)], "clip.avi");
    const aviRecord = await recordFor(avi);
    assert.equal(aviRecord.wire.metadata.status, "unreadable");
    assert.equal(aviRecord.wire.file.media_type, "video");
    assert.equal(aviRecord.record.generation.seed.reason, "metadata-unreadable");
    const asf = new File([new Uint8Array([0x30, 0x26, 0xb2, 0x75, 0x8e, 0x66, 0xcf, 0x11, 0xa6, 0xd9, 0x00, 0xaa, 0x00, 0x62, 0xce, 0x6c]), new Uint8Array(64)], "clip.wmv");
    assert.equal((await recordFor(asf)).wire.metadata.status, "unreadable");
    // A pre-classified video needs no PNG reader; an image without one is an error, not a silent absence.
    const clip = fixtureFile("core.mkv");
    assert.equal((await m.readLocalFile(clip, { classification: { media: "video", kind: "matroska" }, pngMetadata: null })).result.read.status, "complete");
    await assert.rejects(m.readLocalFile(clip, { pngMetadata: null }), /PNG reader/);
    await assert.rejects(m.readLocalFile(fixtureFile("tiny.jpg"), { classification: { media: "image", kind: "jpeg" }, pngMetadata: null }), /PNG reader/);
    await assert.rejects(m.readLocalFile("not a blob"), TypeError);
    assert.deepEqual(await m.readLocalFile(new File([utf8("<svg/>")], "x.png"), { pngMetadata: PngMetadata }), { classification: null, result: null });
    // A container that fails to parse is unreadable too.
    const broken = new File([utf8("\0\0\0\x08ftypisom"), utf8("\0\0\0\x01moov")], "broken.mp4");
    assert.equal((await recordFor(broken)).wire.metadata.status, "unreadable");
});

test("local cap measures escaped UTF-8 atomically", async () => {
    // A 1 MiB Latin-1 prompt of 'é' (2 UTF-8 bytes, 1 code unit each) and a 4 MiB UTF-8 workflow of
    // emoji (4 bytes, 2 units): the UTF-16 length of the wire is well under the limit while its UTF-8
    // size is not, so a cap counting code units would keep too much.
    const prompt = `{"1":{"class_type":"A","inputs":{"t":"${"é".repeat(1_000_000)}"}}}`;
    const workflow = `{"nodes":[],"u":"${"😀".repeat(1_000_000)}"}`;
    const file = pngFile("wide.png", [iTXt("workflow", workflow), tEXt("prompt", prompt)]);
    const handle = await handleFor(file);
    const raw = await m.readLocalFile(handle.file, { classification: handle.classification, pngMetadata: PngMetadata });
    assert.equal(raw.result.status, "complete");
    const wire = m.buildLocalPayload({ read: raw.result, file: { name: file.name, size: file.size, lastModified: file.lastModified }, classification: handle.classification });
    const text = JSON.stringify(wire);
    assert.ok(text.length < LIMIT && bytes(wire) > LIMIT, `${text.length} code units, ${bytes(wire)} bytes`);
    const fitted = await m.fitLocalPayload(wire);
    assert.ok(bytes(fitted) <= LIMIT);
    assert.equal(fitted.metadata.workflow_graph.status, "limited", "the workflow graph goes first");
    assert.equal(fitted.metadata.prompt_graph.status, "parsed");
    assert.deepEqual(Object.keys(fitted.metadata.raw).sort(), ["prompt", "workflow"], "raw is kept");
    const record = m.localFileAdapter.toRecord(fitted, handle, extractGenerationFacts);
    assert.equal(inspectionDownloads(record).metadata.available, true);
    assert.equal(inspectionDownloads(record).workflow.available, false);
    // Whole-raw omission is atomic and marks the response-size limit; the inspector offers no metadata download.
    const squeezed = await m.fitLocalPayload(wire, bytes(fitted) - 1);
    assert.deepEqual([squeezed.metadata.workflow_graph.status, squeezed.metadata.prompt_graph.status], ["limited", "limited"]);
    const tiny = await m.fitLocalPayload(wire, 2000);
    assert.equal(tiny.metadata.status, "limited");
    assert.equal(tiny.metadata.omitted_reason, "response-size-limit");
    assert.deepEqual(tiny.metadata.raw, {});
    assert.equal(tiny.metadata.parameters, null);
    assert.ok(bytes(tiny) <= 2000);
    const omitted = m.localFileAdapter.toRecord(tiny, handle, extractGenerationFacts);
    assert.equal(inspectionDownloads(omitted).metadata.available, false);
    assert.equal(omitted.generation.seed.reason, "projection-limited");
    await assert.rejects(m.fitLocalPayload(wire, 100), /exceeds/);
    assert.equal(wire.metadata.status, "available", "the input wire is not mutated");
    assert.equal((await m.localFileAdapter.read(handle)).metadata.workflow_graph.status, "limited", "adapter.read returns the fitted wire");
});

test("adapter rejects foreign blob URLs and never resolves owned ones", async () => {
    const file = pngFile("a.png", [tEXt("prompt", PROMPT)]);
    const classification = await classify(file);
    const owned = m.createLocalFileHandle({ file, url: "blob:http://host.test/0b0e", classification });
    assert.ok(Object.isFrozen(owned));
    assert.deepEqual(m.localFileAdapter.originalDownload(owned), { url: "blob:http://host.test/0b0e", filename: "a.png" });
    assert.equal(m.localFileAdapter.mediaUrl(owned), "blob:http://host.test/0b0e");
    // The controller and the viewer session spread-copy descriptors: a copy is the same selection.
    const copy = { ...owned, occurrence: {}, reference: {} };
    assert.equal(m.localFileAdapter.key(copy), owned.key);
    assert.equal(m.localFileAdapter.mediaUrl(copy), "blob:http://host.test/0b0e");
    // A tampered URL, key, File or name, or a descriptor this module never made, is foreign.
    const tampered = [{ ...owned, url: "blob:http://host.test/other" }, { ...owned, key: "local:1" }, { ...owned, file: new File([], "x.png") }, { ...owned, filename: "other.png" },
        { kind: "local-file", key: owned.key, filename: owned.filename, url: owned.url, file, is_video: false, classification }, null, "local:1"];
    for (const foreign of tampered) {
        for (const method of ["validate", "originalDownload", "mediaUrl", "key", "scope", "inspectionPath"]) assert.throws(() => m.localFileAdapter[method](foreign), TypeError, method);
        await assert.rejects(m.localFileAdapter.read(foreign), TypeError);
        assert.throws(() => m.localFileAdapter.toRecord({ success: true, inspection_version: 1 }, foreign, extractGenerationFacts), TypeError);
    }
    for (const url of ["http://host.test/x.png", "/prompt_manager/images/serve/a.png", "javascript:alert(1)", "data:image/png;base64,AA==", "", 5]) {
        assert.throws(() => m.createLocalFileHandle({ file, url, classification }), TypeError, String(url));
    }
    assert.throws(() => m.createLocalFileHandle({ file: "not a blob", url: "blob:http://host.test/1", classification }), TypeError);
    assert.throws(() => m.createLocalFileHandle({ file, url: "blob:http://host.test/1", classification: null }), TypeError);
    assert.throws(() => m.createLocalFileHandle({ file, url: "blob:http://host.test/1", classification: { media: "text", kind: "svg" } }), TypeError);
    // A proxy-mounted host resolves API URLs; blob URLs are session-owned and bypass it.
    const before = Object.getOwnPropertyDescriptor(globalThis, "window");
    Object.defineProperty(globalThis, "window", { configurable: true, value: { location: { href: "https://example.test/proxy/prompt_manager/workspace" }, ApiClient: { resolveUrl: (url) => `/proxy${url}` } } });
    try {
        assert.equal(m.localFileAdapter.mediaUrl(owned), "blob:http://host.test/0b0e");
        assert.equal(m.localFileAdapter.originalDownload(owned).url, "blob:http://host.test/0b0e");
    } finally {
        if (before) Object.defineProperty(globalThis, "window", before); else delete globalThis.window;
    }
    // Without an object URL the record has no download and the viewer cannot show the original.
    const previewless = m.createLocalFileHandle({ file, url: null, classification });
    assert.equal(m.localFileAdapter.mediaUrl(previewless), null);
    assert.throws(() => m.localFileAdapter.originalDownload(previewless), TypeError);
    const record = m.localFileAdapter.toRecord(await m.localFileAdapter.read(previewless), previewless, extractGenerationFacts);
    assert.equal(record.download, null);
    assert.equal(inspectionDownloads(record).metadata.filename, "a.metadata.json");
});

test("select reuses the same local key", async () => {
    const file = pngFile("a.png", [tEXt("prompt", PROMPT)]);
    const classification = await classify(file);
    const first = m.createLocalFileHandle({ file, url: "blob:http://host.test/1", classification });
    const second = m.createLocalFileHandle({ file, url: "blob:http://host.test/2", classification });
    assert.notEqual(first.key, second.key, "the same bytes chosen twice are two selections");
    assert.equal(m.localFileAdapter.scope(first), "local");
    assert.equal(m.localFileAdapter.inspectionPath(first), `local-file:${first.key}`);
    assert.equal(m.localFileAdapter.key(first), first.key);
    const reads = [];
    // The controller calls adapter.read itself (PR L2 Task 4); the API is never reached.
    const api = new Proxy({}, { get: (_, key) => assert.fail(`api.${String(key)} touched`) });
    const adapter = { ...m.localFileAdapter, read: (handle, options) => { reads.push(m.localFileAdapter.inspectionPath(handle)); return m.localFileAdapter.read(handle, options); } };
    const controller = createInspectionController({ api, extractFacts: extractGenerationFacts, adapter });
    const pending = controller.select(first);
    assert.equal(controller.select(first), pending, "the same handle returns the pending read");
    await pending;
    assert.deepEqual(reads, [`local-file:${first.key}`]);
    assert.equal(controller.state.record.identity.key, first.key);
    assert.equal(controller.state.record.prompts.positive.text, "a red fox in snow, detailed fur");
    await controller.select(second);
    assert.equal(reads.length, 2);
    assert.equal(controller.state.record.identity.key, second.key);
    await controller.select({ ...second, occurrence: {}, reference: {} });
    assert.equal(reads.length, 2, "the controller's own copy is the same selection");
    await controller.select({ ...second, url: "blob:http://host.test/evil" });
    assert.equal(controller.state.error, true, "a foreign handle is published as an error, never read");
    assert.equal(reads.length, 2);
    controller.dispose();
});

test("fileFor admits selected-local-file only for a local identity", async () => {
    const file = pngFile("a.png", [tEXt("prompt", PROMPT)]);
    const { wire, handle } = await recordFor(file);
    const rooted = { root_index: 0, path: "folder/a.png", filename: "a.png", url: "/prompt_manager/images/serve/folder/a.png?root=0" };
    assert.throws(() => toInspectionRecord({ ...wire, image: { root_index: 0, path: "folder/a.png", filename: "a.png" } }, rooted, extractGenerationFacts), /Invalid inspection file/);
    assert.throws(() => projectInspectionPayload({ ...wire, file: { ...wire.file, observation: "current-file" } }, { kind: "local-file", key: handle.key, path: "a.png" }, null, extractGenerationFacts), /Invalid inspection file/);
    assert.throws(() => m.localFileAdapter.toRecord({ ...wire, success: false }, handle, extractGenerationFacts), TypeError);
    assert.throws(() => m.localFileAdapter.toRecord({ ...wire, inspection_version: 2 }, handle, extractGenerationFacts), TypeError);
    assert.throws(() => m.localFileAdapter.toRecord(null, handle, extractGenerationFacts), TypeError);
});

test("the parity corpus PNGs read into renderable records", async () => {
    for (const name of ["f02.png", "f06a.png", "f23.png"]) {
        const { wire, record } = await recordFor(fixtureFile(name, PARITY));
        assert.equal(wire.metadata.status, "available", name);
        assert.equal(record.file.format, "PNG");
        assert.ok(["parsed", "limited", "malformed"].includes(wire.metadata.prompt_graph.status));
    }
});

// M4: resolution runs on the page's main thread, so a read yields between its bounded steps (the busy state paints
// and input is handled) and no step may exceed a budget on the largest record the readers keep (2 MiB of roles).
const ROLE_BYTES = 2 * 1024 * 1024;
// Generous for a shared CI runner: a desktop resolves these in 130-455 ms.
const STEP_BUDGET_MS = 2500;
const ENCODE = (text) => ({ class_type: "CLIPTextEncode", inputs: { text, clip: ["4", 1] } });
const KSAMPLER = (positive, negative, latent) => ({ class_type: "KSampler", inputs: { seed: 1, steps: 20, cfg: 8.0, sampler_name: "euler", scheduler: "normal",
    denoise: 1.0, model: ["4", 0], positive, negative, latent_image: latent } });
const LOADERS = { "4": { class_type: "CheckpointLoaderSimple", inputs: { ckpt_name: "m.safetensors" } }, "5": { class_type: "EmptyLatentImage", inputs: { width: 64, height: 64, batch_size: 1 } } };
/** The largest graph of this shape whose JSON fits the role budget. */
function maximal(build, high) {
    let low = 1;
    while (low < high) {
        const middle = (low + high + 1) >> 1;
        if (Buffer.byteLength(JSON.stringify(build(middle)), "utf8") <= ROLE_BYTES) low = middle;
        else high = middle - 1;
    }
    return JSON.stringify(build(low));
}
const MAXIMAL_GRAPHS = {
    // Many candidates: every sampler is a final stage with its own pair.
    independent: maximal((count) => {
        const graph = { ...LOADERS };
        for (let index = 0; index < count; index++) {
            graph[`s${index}`] = KSAMPLER([`p${index}`, 0], [`n${index}`, 0], ["5", 0]);
            graph[`p${index}`] = ENCODE(`fox ${index}`);
            graph[`n${index}`] = ENCODE(`blur ${index}`);
        }
        return graph;
    }, 6666),
    // One long stage chain through the latent: the deepest stage walk.
    chain: maximal((count) => {
        const graph = { ...LOADERS, "6": ENCODE("fox"), "7": ENCODE("blur") };
        for (let index = 0; index < count; index++) graph[`s${index}`] = KSAMPLER(["6", 0], ["7", 0], index ? [`s${index - 1}`, 0] : ["5", 0]);
        return graph;
    }, 19996),
};

test("resolution of a maximal 2 MiB graph stays within the step budget", async () => {
    const { resolveLocalEvidence } = await import("../../web/js/workspace/local-resolver.mjs");
    const semantics = await (await import("../../web/js/workspace/local-semantics.mjs")).loadInspectionSemantics();
    for (const [name, prompt] of Object.entries(MAXIMAL_GRAPHS)) {
        assert.ok(Buffer.byteLength(prompt, "utf8") > ROLE_BYTES - 1024, `${name} fills the role budget`);
        const started = performance.now();
        const evidence = resolveLocalEvidence({ prompt }, semantics);
        const elapsed = performance.now() - started;
        assert.ok(elapsed < STEP_BUDGET_MS, `${name}: ${Math.round(elapsed)} ms`);
        assert.notEqual(evidence.resolution.reason_code, "malformed", name);
    }
});

test("a read yields between bounded steps and answers byte for byte as one synchronous build", async () => {
    const semantics = await (await import("../../web/js/workspace/local-semantics.mjs")).loadInspectionSemantics();
    const file = pngFile("maximal.png", [tEXt("prompt", MAXIMAL_GRAPHS.independent)]);
    const handle = await handleFor(file);
    const marks = [];
    const yieldControl = async (signal) => {
        signal?.throwIfAborted();
        marks.push(performance.now());
        await new Promise((resolve) => setTimeout(resolve, 0));
    };
    const started = performance.now();
    const wire = await m.localFileAdapter.read(handle, { signal: new AbortController().signal, yieldControl });
    marks.push(performance.now());
    // Resolution and the projection each run in a turn of their own after the table is in.
    assert.ok(marks.length >= 3, `yields: ${marks.length - 1}`);
    const gaps = marks.map((mark, index) => mark - (index ? marks[index - 1] : started));
    assert.ok(Math.max(...gaps) < STEP_BUDGET_MS, `longest step ${Math.round(Math.max(...gaps))} ms`);
    const { result } = await m.readLocalFile(file, { classification: handle.classification });
    const direct = await m.fitLocalPayload(m.buildLocalPayload({ read: result, file: { name: handle.filename, size: file.size, lastModified: file.lastModified },
        classification: handle.classification, semantics }));
    assert.equal(JSON.stringify(wire), JSON.stringify(direct));
    assert.equal(wire.resolution.state, "ambiguous");
    // An abort at any yield ends the read there.
    for (let stop = 0; stop < marks.length - 1; stop++) {
        const controller = new AbortController();
        let seen = 0;
        const stopping = async (signal) => {
            if (seen++ === stop) controller.abort();
            signal.throwIfAborted();
            await new Promise((resolve) => setTimeout(resolve, 0));
        };
        await assert.rejects(m.localFileAdapter.read(handle, { signal: controller.signal, yieldControl: stopping }), { name: "AbortError" }, `yield ${stop}`);
    }
});
