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

test("a PNG File becomes the server-shaped wire with local provenance", async () => {
    const file = pngFile("Fox Render.PNG", [tEXt("prompt", PROMPT), tEXt("workflow", WORKFLOW)]);
    const { wire, record, handle } = await recordFor(file);
    assert.deepEqual(wire.file, { observation: "selected-local-file", source_label: "Selected local file", media_type: "image", extension: ".png", format: "PNG",
        size: file.size, width: 640, height: 480, modified_time: 1_700_000_000.123 });
    assert.equal(wire.success, true);
    assert.equal(wire.inspection_version, 1);
    assert.deepEqual(wire.prompts, { positive: { status: "unresolved" }, negative: { status: "unresolved" } });
    assert.deepEqual(wire.resolution, { state: "unresolved", sampler_id: null, reason: "Resolved in this browser from the workflow.", reason_code: null, node: null });
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
    // Until the loader seam lands (PR L2 Task 4) the controller reaches the adapter through api.get.
    const api = { get: (sentinel) => { reads.push(sentinel); return m.localFileAdapter.read(reads.length === 1 || sentinel === `local-file:${first.key}` ? first : second); } };
    const controller = createInspectionController({ api, extractFacts: extractGenerationFacts, adapter: m.localFileAdapter });
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
