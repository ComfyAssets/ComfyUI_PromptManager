// Run with: node --test tests/js/
// The browser video tag readers mirror what the server reads through ffprobe
// (utils/prompt_inspector.py _video_metadata) and the carrier rule of
// utils/video_metadata.py select_video_carrier. Each rule names its server twin;
// tests/test_local_inspect_parity.py proves the two agree on the committed fixtures
// and on recorded tag maps.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import * as v from "./helpers/video-bytes.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const FIXTURES = path.join(here, "..", "fixtures", "local-inspect");
const RECORDED = JSON.parse(fs.readFileSync(path.join(FIXTURES, "video-tags-v1.json"), "utf8")).fixtures;
const m = await import("../../web/js/workspace/local-video.mjs").catch((error) => {
    if (error.code !== "ERR_MODULE_NOT_FOUND") throw error;
    return {};
});
const { readVideoTags, selectVideoCarrier, pythonJsonDumps, pythonJsonLoads } = m;

const MiB = 1024 * 1024;
const GiB = 1024 * MiB;
const ROLE_BUDGET = 2 * MiB;
const WORKFLOW_BUDGET = 4 * MiB;
const TAG_READ_BYTES = 12 * MiB + 64 * 1024;
const PER_KEY = new Set(["prompt", "workflow", "parameters", "comfyassets_prompt_pairs"]);
const GRAPH = '{"3":{"class_type":"KSampler","inputs":{"seed":7}}}';
const WORKFLOW = '{"nodes":[],"version":0.4}';

function knownTags(map) {
    return Object.fromEntries(Object.entries(map).filter(([name]) => PER_KEY.has(name.toLowerCase()) || name === "comment" || name === "description"));
}

function fixture(name) {
    return new Uint8Array(fs.readFileSync(path.join(FIXTURES, name)));
}

async function read(bytes, options = {}) {
    return readVideoTags(v.blobOf(bytes), options);
}

function fill(byte, count) {
    return Buffer.alloc(count, byte);
}

function complete(tags, extra = {}) {
    return { status: "complete", tags, limited: [], ...extra };
}

test("contract: the readers, the carrier rule and the json port are exported", () => {
    assert.equal(typeof readVideoTags, "function");
    assert.equal(typeof selectVideoCarrier, "function");
    assert.equal(typeof pythonJsonDumps, "function");
    assert.equal(typeof pythonJsonLoads, "function");
});

test("iso-bmff reader finds per-key tags with moov after mdat", async () => {
    for (const [name, recorded] of Object.entries(RECORDED)) {
        const blob = v.recordingBlob(fixture(name));
        const result = await readVideoTags(blob, { prefix: fixture(name).subarray(0, 32) });
        assert.equal(result.status, "complete", name);
        assert.deepEqual(result.tags, knownTags(recorded), name);
        assert.equal(result.container, name.endsWith(".mkv") || name.endsWith(".webm") ? "matroska" : "iso-bmff", name);
        assert.ok(["isom", "qt", "M4V", "matroska", "webm"].includes(result.format), `${name}: ${result.format}`);
        assert.deepEqual(result.limited, [], name);
        assert.ok(blob.ranges.length > 0, name);
    }
    // core-moov-end.mp4 behind a 3 GiB mdat: only the box headers and the tag bytes are sliced.
    const bytes = fixture("core-moov-end.mp4");
    const boxes = v.topLevelBoxes(bytes);
    const mdat = boxes.find((box) => box.type === "mdat");
    const moov = boxes.find((box) => box.type === "moov");
    const mdatSize = 3 * GiB + 16;
    const header = v.concat(v.u32(1), v.latin1("mdat"), new Uint8Array(8));
    new DataView(header.buffer).setBigUint64(8, BigInt(mdatSize));
    const moovOffset = mdat.start + mdatSize;
    const blob = v.sparseBlob(
        [
            { offset: 0, bytes: bytes.subarray(0, mdat.start) },
            { offset: mdat.start, bytes: header },
            { offset: moovOffset, bytes: bytes.subarray(moov.start, moov.end) },
        ],
        moovOffset + (moov.end - moov.start),
    );
    const result = await readVideoTags(blob, { prefix: bytes.subarray(0, 32) });
    assert.deepEqual(result.tags, knownTags(RECORDED["core-moov-end.mp4"]));
    const inside = blob.ranges.filter(([start]) => start >= mdat.start && start < moovOffset);
    assert.ok(inside.every(([start, end]) => start === mdat.start && end - start <= 16), JSON.stringify(inside));
    const total = blob.ranges.reduce((sum, [start, end]) => sum + (end - start), 0);
    assert.ok(total <= m.VIDEO_READ_CEILING && total < 64 * 1024, `${total}`);
});

test("iso-bmff: legacy ilst atoms, moov/meta, sticky mdta handler and data types", async () => {
    // ©cmt and desc map to comment and description, as ffprobe reports them.
    const legacy = await read(v.mp4(v.legacyUdta({ comment: '{"prompt":"{}"}', description: "note" })));
    assert.deepEqual(legacy, complete({ comment: '{"prompt":"{}"}', description: "note" }, { container: "iso-bmff", format: "isom" }));
    // moov/meta (ISO style) is read like moov/udta/meta.
    const isoMeta = await read(v.mp4(v.box("meta", v.u32(0), v.hdlr("mdta"), v.keys(["prompt"]), v.ilst(v.item(1, v.data(GRAPH))))));
    assert.deepEqual(isoMeta.tags, { prompt: GRAPH });
    // meta without the version/flags word (QuickTime) still finds hdlr.
    const qtMeta = await read(v.mp4(v.udta(v.box("meta", v.hdlr("mdta"), v.keys(["prompt"]), v.ilst(v.item(1, v.data(GRAPH)))))));
    assert.deepEqual(qtMeta.tags, { prompt: GRAPH });
    // Index items need an mdta handler and a keys box; with a legacy handler they are skipped.
    const noKeys = await read(v.mp4(v.udta(v.meta(v.hdlr("mdir"), v.ilst(v.item(1, v.data(GRAPH)))))));
    assert.deepEqual(noKeys.tags, {});
    // The mdta handler is sticky across the file, as ffmpeg's found_hdlr_mdta.
    const sticky = await read(v.mp4(v.udta(v.meta(v.hdlr("mdta"), v.keys(["prompt"])), v.meta(v.hdlr("mdir"), v.ilst(v.item(1, v.data(GRAPH)))))));
    assert.deepEqual(sticky.tags, { prompt: GRAPH });
    // As ffprobe reports them: consecutive data children join with ";", integer data
    // types print as decimals, other types are not tags, text types 0/1/4 are read, NUL
    // truncates, and the run of data children stops at the first other child.
    const mixed = await read(v.mp4(v.udta(v.meta(
        v.hdlr("mdta"),
        v.keys(["prompt", "workflow", "parameters", "comfyassets_prompt_pairs", "comment", "description"]),
        v.ilst(
            v.item(1, v.data("first"), v.data("second"), v.box("name", "x"), v.data("not read")),
            v.item(2, v.data([0, 0, 0, 7], { type: 21 }), v.data([0xff, 0xfe], { type: 21 }), v.data([0xff, 0xfe], { type: 22 })),
            v.item(3, v.data("cut\0off")),
            v.item(4, v.data("sort", { type: 4 }), v.data("mac", { type: 0 })),
            v.item(5, v.data([0, 0, 0, 0, 0, 0, 0], { type: 21 })),
            v.item(6, v.data("float", { type: 23 })),
        ),
    ))));
    assert.deepEqual(mixed.tags, { prompt: "first;second", workflow: "7;-2;65534", parameters: "cut", comfyassets_prompt_pairs: "sort;mac" });
    // Keys outside the mdta namespace address nothing; unknown names are skipped unread.
    const blob = v.recordingBlob(v.mp4(v.udta(v.meta(
        v.hdlr("mdta"),
        v.keys([["prompt", "udta"], "encoder", "workflow"]),
        v.ilst(v.item(1, v.data(GRAPH)), v.item(2, v.data(fill(0x41, 5000))), v.item(3, v.data(WORKFLOW))),
    ))));
    const result = await readVideoTags(blob);
    assert.deepEqual(result.tags, { workflow: WORKFLOW });
    assert.ok(blob.ranges.every(([start, end]) => end - start < 5000), "the encoder value was never sliced");
    // Legacy fourccs are mapped even in mdta mode, and a repeated name joins with ";".
    const repeated = await read(v.mp4(v.udta(v.meta(
        v.hdlr("mdta"),
        v.keys(["prompt", "prompt", "comment"]),
        v.ilst(v.item(1, v.data("one")), v.item(2, v.data("two")), v.item(3, v.data("mdta")), v.legacyItem("©cmt", v.data("c"))),
    ))));
    assert.deepEqual(repeated.tags, { prompt: "one;two", comment: "mdta;c" });
    // Apple's namespaced keys are reported verbatim, so they are never an envelope.
    const apple = await read(v.perKeyMp4({ "com.apple.quicktime.description": '{"prompt":"{}"}', prompt: GRAPH }));
    assert.deepEqual(apple.tags, { prompt: GRAPH });
    // An item whose first child is not a data atom, or a data atom shorter than its
    // 16-byte header, is skipped as ffmpeg skips it.
    const junk = await read(v.mp4(v.udta(v.meta(v.hdlr("mdta"), v.keys(["prompt"]), v.ilst(v.item(1, v.box("name", "x"), v.data(GRAPH)))))));
    assert.deepEqual(junk.tags, {});
    const short = await read(v.mp4(v.udta(v.meta(v.hdlr("mdta"), v.keys(["prompt"]), v.ilst(v.item(1, v.data(GRAPH, { size: 12 })))))));
    assert.deepEqual(short.tags, {});
    // A box declared with size 0 runs to the end of its parent; faststart puts moov first.
    const fast = await read(v.perKeyMp4({ prompt: GRAPH }, { faststart: true, brand: "mp42" }));
    assert.deepEqual(fast, complete({ prompt: GRAPH }, { container: "iso-bmff", format: "mp42" }));
    const toEnd = await read(v.concat(v.ftyp(), v.mdat(), v.moov(v.mdtaUdta({ prompt: GRAPH }), v.opts({ size: 0 }))));
    assert.deepEqual(toEnd.tags, { prompt: GRAPH });
});

test("matroska reads global Tags and skips clusters", async () => {
    const { ID } = v;
    // ffmpeg layout: SeekHead, Info, Tracks, Tags, Cluster, Cues; names upper-cased.
    const plain = await read(v.perKeyMkv({ PROMPT: GRAPH, WORKFLOW, ENCODER: "Lavf" }, { docType: "webm" }));
    assert.deepEqual(plain, complete({ PROMPT: GRAPH, WORKFLOW }, { container: "matroska", format: "webm" }));
    // Tags after a 3 GiB Cluster, located through the SeekHead: no slice inside the cluster.
    const tags = v.globalTags({ PROMPT: GRAPH });
    const clusterSize = 3 * GiB;
    // The Tags position (relative to the Segment payload) is a 4-byte uint either way,
    // so the SeekHead's length can be measured with a placeholder first.
    const seekLength = v.seekHead([[ID.Tags, clusterSize]]).length;
    const seekHead = v.seekHead([[ID.Tags, seekLength + 12 + clusterSize]]);
    assert.equal(seekHead.length, seekLength);
    const head = v.concat(v.ebmlHeader(), v.idBytes(ID.Segment), v.vint(seekHead.length + 12 + clusterSize + tags.length, { width: 8 }), seekHead, v.clusterHeader(clusterSize));
    const clusterStart = v.ebmlHeader().length + 12 + seekHead.length;
    const tagsOffset = head.length + clusterSize;
    const blob = v.sparseBlob([{ offset: 0, bytes: head }, { offset: tagsOffset, bytes: tags }], tagsOffset + tags.length);
    const seeked = await readVideoTags(blob, { prefix: head.subarray(0, 32) });
    assert.deepEqual(seeked.tags, { PROMPT: GRAPH });
    const inside = blob.ranges.filter(([start]) => start >= clusterStart && start < tagsOffset);
    assert.ok(inside.every(([start, end]) => start === clusterStart && end - start <= 16), JSON.stringify(inside));
    assert.ok(blob.ranges.reduce((sum, [start, end]) => sum + (end - start), 0) <= m.VIDEO_READ_CEILING);
    // ffprobe reads level-1 elements up to the first Cluster: Tags behind it are found
    // only through a SeekHead, so without one they are not read (nor sliced).
    const behind = v.recordingBlob(v.mkv([v.cluster(64), v.globalTags({ PROMPT: GRAPH }), el(ID.Cues, new Uint8Array(2))]));
    const sequential = await readVideoTags(behind);
    assert.deepEqual(sequential.tags, {});
    assert.ok(behind.ranges.every(([, end]) => end <= v.ebmlHeader().length + 12 + 12 + 32), JSON.stringify(behind.ranges));
    // Track-targeted Tags are ignored; chapter and attachment targets too.
    const targeted = await read(v.mkv([
        v.tagsEl(
            v.tag([v.simpleTag("PROMPT", "track")], { trackUid: 1 }),
            v.tag([v.simpleTag("WORKFLOW", WORKFLOW)], { targetType: 50 }),
            v.tag([v.simpleTag("PROMPT", "zero uid is global")], { trackUid: 0 }),
        ),
        v.cluster(),
    ]));
    assert.deepEqual(targeted.tags, { WORKFLOW, PROMPT: "zero uid is global" });
    // An unknown-size Cluster (live recordings) is the same stop; a SeekHead still works.
    const hidden = await read(v.mkv([v.cluster(8, { unknown: true }), v.globalTags({ PROMPT: GRAPH })]));
    assert.deepEqual(hidden, complete({}, { container: "matroska", format: "matroska" }));
    const before = await read(v.mkv([v.globalTags({ PROMPT: GRAPH }), v.cluster(8, { unknown: true })]));
    assert.deepEqual(before.tags, { PROMPT: GRAPH });
    const info = el(ID.Info, new Uint8Array(4));
    const clusterUnknown = v.cluster(8, { unknown: true });
    const seekToTags = v.seekHead([[ID.Tags, 0x7f]]); // a one-byte position, like the real one
    const viaSeek = await read(v.mkv([v.seekHead([[ID.Tags, seekToTags.length + info.length + clusterUnknown.length]]), info, clusterUnknown, v.globalTags({ PROMPT: GRAPH })]));
    assert.deepEqual(viaSeek.tags, { PROMPT: GRAPH });
    // An unknown-size Segment is fine (live recordings); other unknown sizes are invalid.
    const liveSegment = await read(v.mkv([v.globalTags({ PROMPT: GRAPH }), v.cluster()], { unknown: true }));
    assert.deepEqual(liveSegment.tags, { PROMPT: GRAPH });
    const unknownTags = await read(v.mkv([el(ID.Tags, v.tag([v.simpleTag("PROMPT", GRAPH)]), v.opts({ unknown: true }))]));
    assert.deepEqual(unknownTags, { status: "unreadable", reason: "invalid-container" });
    // ffmpeg's tag naming (checked against ffprobe 9.0.2): a language other than und
    // files the tag under NAME-lang only, unless TagDefault is set; nested SimpleTags
    // become PARENT/CHILD (never ours); a string-less tag deletes the key; a repeated
    // name keeps the last value; NUL ends the string; Void and CRC-32 are skipped.
    const naming = await read(v.mkv([
        v.voidEl(8),
        v.tagsEl(
            el(ID.CRC32, new Uint8Array(4)),
            v.tag([
                v.simpleTag("PROMPT", "eng only", { lang: "eng" }),
                v.simpleTag("WORKFLOW", "eng only", { lang: "eng", def: 0 }),
                v.simpleTag("workflow", "default and eng", { lang: "eng", def: 1 }),
                v.simpleTag("PARAMETERS", "und", { lang: "und", nested: [v.simpleTag("prompt", "nested")] }),
                v.simpleTag("comfyassets_prompt_pairs", "cut\0here"),
                v.simpleTag("description", undefined, { binary: [1, 2, 3] }),
                v.simpleTag("comment", "note"),
                v.simpleTag("comment", undefined),
                v.simpleTag("prompt", "first"),
                v.simpleTag("prompt", "last", { binary: [1] }),
            ]),
        ),
        v.cluster(),
    ]));
    assert.deepEqual(naming.tags, { workflow: "default and eng", PARAMETERS: "und", comfyassets_prompt_pairs: "cut", prompt: "last" });
    // Long names are never ours and are skipped without reading their strings.
    const longName = v.recordingBlob(v.mkv([v.tagsEl(v.tag([v.simpleTag("N".repeat(300), fill(0x41, 4000)), v.simpleTag("PROMPT", GRAPH)])), v.cluster()]));
    const skipped = await readVideoTags(longName);
    assert.deepEqual(skipped.tags, { PROMPT: GRAPH });
    assert.ok(longName.ranges.every(([start, end]) => end - start < 300));
    function el(id, ...parts) {
        return v.el(id, ...parts);
    }
});

test("containers fail closed", async () => {
    const { ID } = v;
    const invalid = { status: "unreadable", reason: "invalid-container" };
    const rows = [
        ["box size below its header", v.concat(v.ftyp(), v.box("free", v.opts({ size: 4 })), v.mdat(), v.moov(v.mdtaUdta({ prompt: GRAPH })))],
        ["child box past its parent", v.concat(v.ftyp(), v.mdat(), v.moov(v.udta(v.meta(v.hdlr("mdta"), v.keys(["prompt"]), v.ilst(v.item(1, v.data(GRAPH), v.opts({ size: 5000 })))))))],
        ["top-level box past the end of the file", v.concat(v.ftyp(), v.mdat(), v.box("moov", v.opts({ size: 4000 })))],
        ["64-bit size above MAX_SAFE_INTEGER", v.concat(v.ftyp(), v.box("mdat", v.opts({ largesize: 2 ** 60 })), v.moov(v.mdtaUdta({ prompt: GRAPH })))],
        ["64-bit size below its header", v.concat(v.ftyp(), v.box("mdat", v.opts({ largesize: 12 })), v.moov(v.mdtaUdta({ prompt: GRAPH })))],
        ["keys count larger than the entries", v.mp4(v.udta(v.meta(v.hdlr("mdta"), v.keys(["prompt"], { count: 3 }), v.ilst(v.item(1, v.data(GRAPH))))))],
        ["keys entry overrunning the box", v.mp4(v.udta(v.meta(v.hdlr("mdta"), v.box("keys", v.u32(0), v.u32(1), v.u32(90), v.latin1("mdta"), v.utf8("prompt")), v.ilst(v.item(1, v.data(GRAPH))))))],
        ["ilst index 0", v.mp4(v.udta(v.meta(v.hdlr("mdta"), v.keys(["prompt"]), v.ilst(v.item(0, v.data(GRAPH))))))],
        ["ilst index out of range", v.mp4(v.udta(v.meta(v.hdlr("mdta"), v.keys(["prompt"]), v.ilst(v.item(2, v.data(GRAPH))))))],
        ["invalid UTF-8 in a tag value", v.perKeyMp4({ prompt: [0x7b, 0xff, 0xfe, 0x7d] })],
        ["invalid UTF-8 in a key name", v.mp4(v.udta(v.meta(v.hdlr("mdta"), v.keys([Uint8Array.from([0xc3, 0x28])]), v.ilst(v.item(1, v.data(GRAPH))))))],
        ["no moov box", v.concat(v.ftyp(), v.mdat(), v.free(32))],
        ["not a container at all", v.utf8("#EXTM3U\n#EXT-X-VERSION:3\n".padEnd(64, "x"))],
        ["bad VINT: zero ID byte", v.concat(v.ebmlHeader(), v.idBytes(ID.Segment), v.vint(20, { width: 8 }), [0x00, 0x81, 0x00], new Uint8Array(17))],
        ["bad VINT: zero size byte", v.concat(v.ebmlHeader(), v.idBytes(ID.Segment), v.vint(20, { width: 8 }), v.idBytes(ID.Info), [0x00], new Uint8Array(17))],
        ["element past its parent", v.mkv([v.el(ID.Tags, v.tag([v.simpleTag("PROMPT", GRAPH)]), v.opts({ size: 5000 }))])],
        ["element past the end of the file", v.concat(v.ebmlHeader(), v.idBytes(ID.Segment), v.vint(5000, { width: 8 }), v.globalTags({ PROMPT: GRAPH }))],
        ["unknown DocType", v.perKeyMkv({ PROMPT: GRAPH }, { docType: "avi" })],
        ["EBML header without a DocType", v.concat(v.el(ID.EBML, v.el(ID.EBMLVersion, [1])), v.segment([v.globalTags({ PROMPT: GRAPH })]))],
        ["invalid UTF-8 in a TagString", v.mkv([v.tagsEl(v.tag([v.simpleTag("PROMPT", [0xff, 0xfe])]))])],
        ["invalid UTF-8 in a TagName", v.mkv([v.tagsEl(v.tag([v.simpleTag([0xff], GRAPH)]))])],
        ["Matroska bytes after a stray size", v.concat(v.ebmlHeader(), v.idBytes(ID.Segment), v.vint(3, { width: 8 }), [0xec, 0x82, 0x00])],
    ];
    for (const [name, bytes] of rows) {
        assert.deepEqual(await read(bytes), invalid, name);
    }
    // Trailing bytes shorter than a box header are ignored, as ffmpeg does.
    assert.deepEqual((await read(v.concat(v.perKeyMp4({ prompt: GRAPH }), [1, 2, 3]))).tags, { prompt: GRAPH });
    // 10,001 boxes stop the read.
    const many = v.concat(v.ftyp(), ...Array.from({ length: 10001 }, () => v.free()), v.mdat(), v.moov(v.mdtaUdta({ prompt: GRAPH })));
    assert.deepEqual(await read(many), { status: "limited", reason: "chunk-limit" });
    const manyElements = v.mkv([...Array.from({ length: 10001 }, () => v.voidEl(1)), v.globalTags({ PROMPT: GRAPH })]);
    assert.deepEqual(await read(manyElements), { status: "limited", reason: "chunk-limit" });
    // Exactly 10,000 elements still read (ftyp, mdat, moov, udta, meta, hdlr, keys, its entry, ilst, the item and its data atom).
    const exact = v.concat(v.ftyp(), ...Array.from({ length: 10000 - 11 }, () => v.free()), v.mdat(), v.moov(v.mdtaUdta({ prompt: GRAPH })));
    assert.deepEqual((await read(exact)).tags, { prompt: GRAPH });
    // A vanished file is a read failure, not a container error.
    assert.deepEqual(await readVideoTags(v.failingBlob(4096), { prefix: v.ftyp() }), { status: "unreadable", reason: "read-failure" });
    assert.deepEqual(await readVideoTags({}), { status: "unreadable", reason: "read-failure" });
});

test("budgets: roles share 2 MiB, an oversized workflow is left unread, duplicates and the total are bounded", async () => {
    const limited = { status: "limited", reason: "metadata-limit" };
    // Role tags over 2 MiB together stop the read (the server fails the whole read).
    assert.deepEqual(await read(v.perKeyMp4({ prompt: fill(0x41, ROLE_BUDGET + 1) })), limited);
    assert.deepEqual(await read(v.perKeyMkv({ PROMPT: fill(0x41, ROLE_BUDGET - 10), PARAMETERS: fill(0x42, 11) })), limited);
    const atRole = await read(v.perKeyMp4({ prompt: fill(0x41, ROLE_BUDGET - 10), comfyassets_prompt_pairs: fill(0x42, 10) }));
    assert.equal(atRole.status, "complete");
    assert.equal(atRole.tags.prompt.length, ROLE_BUDGET - 10);
    // A per-key workflow over 4 MiB is not sliced; the result says the workflow is limited.
    const big = v.recordingBlob(v.perKeyMp4({ prompt: GRAPH, workflow: fill(0x7b, WORKFLOW_BUDGET + 1) }));
    const over = await readVideoTags(big);
    assert.deepEqual(over, { status: "complete", container: "iso-bmff", format: "isom", tags: { prompt: GRAPH }, limited: ["workflow"] });
    assert.ok(big.ranges.every(([start, end]) => end - start < WORKFLOW_BUDGET), "the oversized workflow was never sliced");
    const bigMkv = await read(v.perKeyMkv({ WORKFLOW: fill(0x7b, WORKFLOW_BUDGET + 1), PROMPT: GRAPH }));
    assert.deepEqual([bigMkv.limited, Object.keys(bigMkv.tags)], [["workflow"], ["PROMPT"]]);
    const atWorkflow = await read(v.perKeyMp4({ workflow: fill(0x7b, WORKFLOW_BUDGET) }));
    assert.deepEqual([atWorkflow.limited, atWorkflow.tags.workflow.length], [[], WORKFLOW_BUDGET]);
    // Fold-duplicates draw from the duplicate budgets (2x each class), then stop.
    const twice = await read(v.perKeyMkv({ PROMPT: fill(0x41, ROLE_BUDGET), prompt: fill(0x41, ROLE_BUDGET) }));
    assert.deepEqual(Object.keys(twice.tags).sort(), ["PROMPT", "prompt"]);
    const thrice = await read(v.perKeyMkv({ PROMPT: fill(0x41, ROLE_BUDGET), prompt: fill(0x41, ROLE_BUDGET), Prompt: fill(0x41, ROLE_BUDGET + 1) }));
    assert.deepEqual(thrice, limited);
    const workflowTwice = await read(v.perKeyMkv({ WORKFLOW: fill(0x7b, WORKFLOW_BUDGET), workflow: fill(0x7b, WORKFLOW_BUDGET) }));
    assert.equal(workflowTwice.status, "complete");
    // Envelopes count against the total only; the total is the server's probe bound.
    const envelope = await read(v.mp4(v.legacyUdta({ comment: fill(0x41, 5 * MiB) })));
    assert.equal(envelope.tags.comment.length, 5 * MiB);
    const tooMuch = await read(v.mp4(v.legacyUdta({ comment: fill(0x41, TAG_READ_BYTES - 10), description: fill(0x42, 11) })));
    assert.deepEqual(tooMuch, limited);
    assert.equal(m.VIDEO_TAG_READ_BYTES, TAG_READ_BYTES);
});

test("avi and asf are recognised but not read", async () => {
    const avi = v.concat(v.latin1("RIFF"), v.u32(1000), v.latin1("AVI LIST"), new Uint8Array(16));
    const asf = v.concat([0x30, 0x26, 0xb2, 0x75, 0x8e, 0x66, 0xcf, 0x11, 0xa6, 0xd9, 0x00, 0xaa, 0x00, 0x62, 0xce, 0x6c], new Uint8Array(16));
    for (const bytes of [avi, asf]) {
        const blob = v.recordingBlob(v.concat(bytes, new Uint8Array(4096)));
        assert.deepEqual(await readVideoTags(blob, { prefix: bytes }), { status: "unsupported", reason: "video-container-unsupported" });
        assert.deepEqual(blob.ranges, []);
    }
    // The classifier's kind can be handed in instead of a prefix.
    const blob = v.recordingBlob(new Uint8Array(64));
    assert.deepEqual(await readVideoTags(blob, { kind: "avi" }), { status: "unsupported", reason: "video-container-unsupported" });
    assert.deepEqual(blob.ranges, []);
    // Without a prefix the reader slices the first 32 bytes itself.
    const mp4 = v.recordingBlob(v.perKeyMp4({ prompt: GRAPH }));
    assert.deepEqual((await readVideoTags(mp4)).tags, { prompt: GRAPH });
    assert.deepEqual(mp4.ranges[0], [0, 32]);
});

test("abort during a moov walk settles once", async () => {
    const unhandled = [];
    const onUnhandled = (reason) => unhandled.push(reason);
    process.on("unhandledRejection", onUnhandled);
    try {
        const bytes = v.concat(v.ftyp(), ...Array.from({ length: 50 }, () => v.free()), v.mdat(), v.moov(v.mdtaUdta({ prompt: GRAPH, workflow: WORKFLOW })));
        const controller = new AbortController();
        let slices = 0;
        const blob = {
            size: bytes.length,
            slice(start, end) {
                slices += 1;
                if (slices === 3) controller.abort();
                return { arrayBuffer: () => new Promise((resolve) => setTimeout(() => resolve(bytes.slice(start, end).buffer), 2)) };
            },
        };
        await assert.rejects(readVideoTags(blob, { signal: controller.signal }), { name: "AbortError" });
        const already = new AbortController();
        already.abort();
        await assert.rejects(readVideoTags(v.blobOf(bytes), { signal: already.signal }), { name: "AbortError" });
        const slow = v.slowBlob(bytes, 1);
        const during = new AbortController();
        const pending = readVideoTags(slow, { signal: during.signal, yieldControl: async () => during.abort() });
        await assert.rejects(pending, { name: "AbortError" });
        await new Promise((resolve) => setTimeout(resolve, 10));
        assert.deepEqual(unhandled, []);
    } finally {
        process.off("unhandledRejection", onUnhandled);
    }
});

test("yieldControl is awaited while walking many boxes", async () => {
    let yields = 0;
    const many = v.concat(v.ftyp(), ...Array.from({ length: 3000 }, () => v.free()), v.mdat(), v.moov(v.mdtaUdta({ prompt: GRAPH })));
    const result = await readVideoTags(v.slowBlob(many, 0), { yieldControl: async () => { yields += 1; } });
    assert.equal(result.status, "complete");
    assert.ok(yields > 0, "the reader handed control back");
});

test("carrier selection: per-key tags, then the comment envelope, then description, never merged", () => {
    const ENVELOPE_WORKFLOW = '{"last_node_id": 9, "nodes": [{"id": 3, "type": "KSampler", "widgets_values": [7, "fixed", 20, 8.0, "euler", "normal", 1.0]}, {"id": 6, "type": "CLIPTextEncode", "widgets_values": ["a red fox in snow, detailed fur"]}, {"id": 7, "type": "CLIPTextEncode", "widgets_values": ["blurry, lowres"]}], "links": [], "version": 0.4}';
    const original = (keys) => Object.fromEntries(keys.map((key) => [key, "original-text"]));
    const text = (tags, keys) => Object.fromEntries(keys.map((key) => [key, tags[key] ?? tags[key.toUpperCase()]]));
    // The recorded fixtures.
    for (const name of ["core-faststart.mp4", "core-moov-end.mp4", "core.mov", "core.m4v", "core.mkv", "core-tags.webm"]) {
        const tags = RECORDED[name];
        const keys = Object.keys(tags).map((key) => key.toLowerCase()).filter((key) => PER_KEY.has(key));
        assert.deepEqual(selectVideoCarrier(tags), { status: "complete", records: text(tags, keys), record_formats: original(keys), carrierText: null, limited: [] }, name);
    }
    const legacy = RECORDED["legacy-comment.mp4"];
    const comment = pythonJsonLoads(legacy.comment);
    assert.deepEqual(selectVideoCarrier(legacy), {
        status: "complete",
        records: { prompt: comment.get("prompt"), workflow: ENVELOPE_WORKFLOW },
        record_formats: { prompt: "original-text", workflow: "reserialized-json" },
        carrierText: legacy.comment,
        limited: [],
    });
    assert.ok(comment.get("prompt").startsWith('{"3":{"class_type":"KSampler"'));
    // Matroska upper-cases COMMENT, and envelope names are exact-case (ruling M1): nothing is read.
    assert.deepEqual(selectVideoCarrier(RECORDED["legacy-comment.webm"]), { status: "complete", records: {}, record_formats: {}, carrierText: null, limited: [] });
    // Synthetic maps.
    const envelope = JSON.stringify({ prompt: '{"9":{}}', workflow: { v: 1 } });
    const cases = [
        ["per-key wins over comment", { prompt: GRAPH, workflow: WORKFLOW, comment: envelope }, { records: { prompt: GRAPH, workflow: WORKFLOW }, record_formats: original(["prompt", "workflow"]), carrierText: null }],
        ["Matroska names fold", { PROMPT: GRAPH, Workflow: WORKFLOW, ENCODER: "Lavf" }, { records: { prompt: GRAPH, workflow: WORKFLOW }, record_formats: original(["prompt", "workflow"]), carrierText: null }],
        ["identical fold-duplicates are one record", { PROMPT: GRAPH, prompt: GRAPH }, { records: { prompt: GRAPH }, record_formats: original(["prompt"]), carrierText: null }],
        ["differing fold-duplicates fail", { PROMPT: GRAPH, prompt: '{"9":{}}' }, { status: "unreadable", reason: "duplicate-metadata" }],
        ["differing workflow duplicates fail even with a prompt", { Workflow: WORKFLOW, WORKFLOW: "{}", prompt: GRAPH }, { status: "unreadable", reason: "duplicate-metadata" }],
        ["envelope names never fold", { comment: envelope, COMMENT: "a free-text note" }, { records: { prompt: '{"9":{}}', workflow: '{"v": 1}' }, record_formats: { prompt: "original-text", workflow: "reserialized-json" }, carrierText: envelope }],
        ["non-JSON comment falls through to description", { comment: "not json", description: envelope }, { records: { prompt: '{"9":{}}', workflow: '{"v": 1}' }, record_formats: { prompt: "original-text", workflow: "reserialized-json" }, carrierText: envelope }],
        ["a JSON array comment is not an envelope", { comment: '["prompt"]', description: '{"workflow": {}}' }, { records: { workflow: "{}" }, record_formats: { workflow: "reserialized-json" }, carrierText: '{"workflow": {}}' }],
        ["a role envelope beats a workflow-only per-key carrier", { workflow: WORKFLOW, comment: envelope }, { records: { prompt: '{"9":{}}', workflow: '{"v": 1}' }, record_formats: { prompt: "original-text", workflow: "reserialized-json" }, carrierText: envelope }],
        ["role description beats workflow-only comment", { comment: '{"workflow": {"a": 1}}', description: '{"parameters": "cat"}' }, { records: { parameters: "cat" }, record_formats: { parameters: "original-text" }, carrierText: '{"parameters": "cat"}' }],
        ["workflow-only carriers: the first wins, never merged", { comment: '{"workflow": {"a": 1}}', description: '{"workflow": {"b": 2}}' }, { records: { workflow: '{"a": 1}' }, record_formats: { workflow: "reserialized-json" }, carrierText: '{"workflow": {"a": 1}}' }],
        ["description only", { description: envelope }, { records: { prompt: '{"9":{}}', workflow: '{"v": 1}' }, record_formats: { prompt: "original-text", workflow: "reserialized-json" }, carrierText: envelope }],
        ["no known key", { encoder: "Lavf", major_brand: "isom" }, { records: {}, record_formats: {}, carrierText: null }],
        ["envelope with only unknown keys is no carrier", { comment: '{"title": "x"}' }, { records: {}, record_formats: {}, carrierText: null }],
        ["null role marker is refused", { comment: '{"prompt": null, "workflow": {}}' }, { status: "unreadable", reason: "invalid-metadata" }],
        ["numeric role is refused", { description: '{"parameters": 5}' }, { status: "unreadable", reason: "invalid-metadata" }],
        ["a non-text non-object workflow is invalid but the roles stay", { comment: '{"prompt": "{}", "workflow": null}' }, { records: { prompt: "{}" }, record_formats: { prompt: "original-text", workflow: "invalid" }, carrierText: '{"prompt": "{}", "workflow": null}' }],
        ["an object prompt is reserialized", { comment: '{"prompt": {"1": {"class_type": "A"}}}' }, { records: { prompt: '{"1": {"class_type": "A"}}' }, record_formats: { prompt: "reserialized-json" }, carrierText: '{"prompt": {"1": {"class_type": "A"}}}' }],
        ["a non-text per-key role marker is refused", { prompt: 5, workflow: WORKFLOW }, { status: "unreadable", reason: "invalid-metadata" }],
        ["a non-text per-key workflow is invalid", { prompt: GRAPH, workflow: 5 }, { records: { prompt: GRAPH }, record_formats: { prompt: "original-text", workflow: "invalid" }, carrierText: null }],
        ["a workflow-only envelope with an invalid workflow is still the carrier", { comment: '{"workflow": 5}' }, { records: {}, record_formats: { workflow: "invalid" }, carrierText: '{"workflow": 5}' }],
        ["not a map", null, { records: {}, record_formats: {}, carrierText: null }],
    ];
    for (const [name, tags, expected] of cases) {
        const want = expected.status ? expected : { status: "complete", limited: [], ...expected };
        assert.deepEqual(selectVideoCarrier(tags), want, name);
    }
    // The server's budget loop: a 4 MiB workflow is limited, roles over 2 MiB fail, exact sizes pass.
    const bigWorkflow = "{" + "0".repeat(WORKFLOW_BUDGET);
    assert.deepEqual(selectVideoCarrier({ prompt: GRAPH, workflow: bigWorkflow }), { status: "complete", records: { prompt: GRAPH }, record_formats: { prompt: "original-text", workflow: "limited" }, carrierText: null, limited: ["workflow"] });
    assert.deepEqual(selectVideoCarrier({ workflow: bigWorkflow.slice(1) }).records, { workflow: bigWorkflow.slice(1) });
    assert.deepEqual(selectVideoCarrier({ comment: JSON.stringify({ prompt: "x", workflow: { k: "0".repeat(WORKFLOW_BUDGET) } }) }).record_formats, { prompt: "original-text", workflow: "limited" });
    assert.deepEqual(selectVideoCarrier({ prompt: "a".repeat(ROLE_BUDGET), parameters: "b" }), { status: "limited", reason: "metadata-limit" });
    assert.deepEqual(selectVideoCarrier({ prompt: "é".repeat(ROLE_BUDGET / 2) }).records.prompt.length, ROLE_BUDGET / 2);
    assert.deepEqual(selectVideoCarrier({ prompt: "é".repeat(ROLE_BUDGET / 2) + "a" }), { status: "limited", reason: "metadata-limit" });
    assert.deepEqual(selectVideoCarrier({ prompt: "\ud800".repeat(ROLE_BUDGET / 3) + "aaa" }), { status: "limited", reason: "metadata-limit" });
    assert.deepEqual(selectVideoCarrier({ prompt: "\u{1f600}".repeat(ROLE_BUDGET / 4) }).records.prompt.length, ROLE_BUDGET / 2);
    assert.deepEqual(selectVideoCarrier({ prompt: "\u{1f600}".repeat(ROLE_BUDGET / 4) + "a" }), { status: "limited", reason: "metadata-limit" });
    // The reader's limited workflow (the server's role-only re-probe): kept with the per-key
    // carrier or no carrier, cleared when an envelope wins.
    assert.deepEqual(selectVideoCarrier({ prompt: GRAPH }, { limited: ["workflow"] }).record_formats, { prompt: "original-text", workflow: "limited" });
    assert.deepEqual(selectVideoCarrier({ encoder: "x" }, { limited: ["workflow"] }), { status: "complete", records: {}, record_formats: { workflow: "limited" }, carrierText: null, limited: ["workflow"] });
    assert.deepEqual(selectVideoCarrier({ comment: envelope }, { limited: ["workflow"] }).limited, []);
    assert.deepEqual(selectVideoCarrier({ comment: envelope }, { limited: ["workflow"] }).record_formats, { prompt: "original-text", workflow: "reserialized-json" });
});

test("pythonJsonLoads and pythonJsonDumps spell values as Python's json module does", () => {
    const rows = [
        ["1.0", "1.0"], ["8.0", "8.0"], ["0.1", "0.1"], ["1e16", "1e+16"], ["1e15", "1000000000000000.0"],
        ["123456789012345678", "123456789012345678"], ["0.0001", "0.0001"], ["0.00001", "1e-05"], ["1e-7", "1e-07"],
        ["1.5e300", "1.5e+300"], ["1e400", "Infinity"], ["-0", "0"], ["-0.0", "-0.0"], ["1E2", "100.0"], ["2.5", "2.5"],
        ["100.0", "100.0"], ["1e21", "1e+21"], ["123456.789", "123456.789"], ["0.000123", "0.000123"], ["-1e-05", "-1e-05"],
        ["9007199254740993", "9007199254740993"], ["1.7976931348623157e308", "1.7976931348623157e+308"], ["5e-324", "5e-324"],
        ["NaN", "NaN"], ["-Infinity", "-Infinity"], ["Infinity", "Infinity"], ["[1.0e+2]", "[100.0]"], ["-1e400", "-Infinity"],
        ["[]", "[]"], ["{}", "{}"], ["[true, false, null]", "[true, false, null]"], ['{"k":[1,{"z":1}]}', '{"k": [1, {"z": 1}]}'],
        [' \t\n\r{"a" : 1 } ', '{"a": 1}'],
        ['{"a":"é\\u0001\u007f\\"\\\\\\n\\t\\b\\f\\r\\/ 😀 \\ud800"}', '{"a": "\\u00e9\\u0001\\u007f\\"\\\\\\n\\t\\b\\f\\r/ \\ud83d\\ude00 \\ud800"}'],
        ['{"a":"\\ud83d\\ude00"}', '{"a": "\\ud83d\\ude00"}'],
        ['{"10":1,"3":2,"b":3,"a":4}', '{"10": 1, "3": 2, "b": 3, "a": 4}'],
        ['{"a":1,"b":2,"a":3}', '{"a": 3, "b": 2}'],
    ];
    for (const [input, output] of rows) {
        assert.equal(pythonJsonDumps(pythonJsonLoads(input)), output, input);
    }
    const bad = ['{"a":1} x', '{"a":"\u0001"}', '{"a":"\\x"}', "01", "1.", ".5", "+1", "[1,]", '{"a":1,}', "{a:1}", "'a'", "", "   ", "[", "nul", "-", "1e", '"\\u12"', "[1 2]", "tru", "Infinit"];
    for (const input of bad) {
        assert.throws(() => pythonJsonLoads(input), { name: "PythonJsonError" }, JSON.stringify(input));
    }
    assert.throws(() => pythonJsonLoads("[".repeat(2000) + "]".repeat(2000)), { name: "PythonJsonError", recursion: true });
    assert.throws(() => pythonJsonDumps(undefined), { name: "PythonJsonError" });
    assert.throws(() => pythonJsonLoads(5), { name: "PythonJsonError" });
    assert.equal(pythonJsonDumps(pythonJsonLoads("[".repeat(500) + "]".repeat(500))), "[".repeat(500) + "]".repeat(500));
    const parsed = pythonJsonLoads('{"s":"x","n":1,"f":1.5,"b":true,"z":null,"l":[1]}');
    assert.ok(parsed instanceof Map);
    assert.deepEqual([...parsed.keys()], ["s", "n", "f", "b", "z", "l"]);
    assert.equal(parsed.get("s"), "x");
    assert.equal(parsed.get("b"), true);
    assert.equal(parsed.get("z"), null);
    assert.ok(Array.isArray(parsed.get("l")));
});
