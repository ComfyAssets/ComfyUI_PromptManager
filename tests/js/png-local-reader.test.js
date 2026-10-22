// Run with: node --test tests/js/
// The browser PNG File reader is a port of utils/prompt_inspector.py's bounded
// reader (_png_metadata, _PngRecords, _png_text, _inflate, _batch_normalized). Each
// rule below names the Python branch it mirrors; tests/test_local_inspect_parity.py
// proves the two agree on the committed parity corpus.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const zlib = require("node:zlib");
const { classifyPrefix, readPngMetadataFile, readImageHeader } = require("../../web/js/png-metadata.js");
const bytes = require("./helpers/png-bytes.mjs");

const { png, raw, ihdr, idat, iend, tEXt, zTXt, iTXt, chunk, blobOf, sparseBlob, recordingBlob, slowBlob, failingBlob, shrunkBlob, concat, u32, latin1, parametersText } = bytes;

const MiB = 1024 * 1024;
const ROLE_BUDGET = 2 * MiB;
const WORKFLOW_BUDGET = 4 * MiB;
const PNG_READ_CEILING = 19794389;
const FIXTURES = path.join(__dirname, "..", "fixtures", "local-inspect");

async function read(fileBytes, options) {
    return readPngMetadataFile(blobOf(fileBytes), options);
}

function fill(byte, count) {
    return Buffer.alloc(count, byte);
}

test("contract: the reader, classifier and header reader are exported", () => {
    assert.equal(typeof readPngMetadataFile, "function");
    assert.equal(typeof classifyPrefix, "function");
    assert.equal(typeof readImageHeader, "function");
});

test("reads the four retained records and the IHDR size from a core SaveImage PNG", async () => {
    const result = await read(raw(
        ihdr(640, 480),
        idat(),
        tEXt("prompt", '{"1":{"class_type":"KSampler"}}'),
        iTXt("workflow", '{"nodes":[]}'),
        tEXt("parameters", "cat\nSteps: 20, Seed: 1"),
        zTXt("comfyassets_prompt_pairs", '{"pairs":[]}'),
        tEXt("Software", "ComfyUI"),
        iend(),
    ));
    assert.deepEqual(result, {
        status: "complete",
        records: {
            prompt: '{"1":{"class_type":"KSampler"}}',
            workflow: '{"nodes":[]}',
            parameters: "cat\nSteps: 20, Seed: 1",
            comfyassets_prompt_pairs: '{"pairs":[]}',
        },
        limited: [],
        width: 640,
        height: 480,
    });
});

test("local PNG skips sparse pixels and reads post-IDAT text", async () => {
    // Two 1.5 GiB IDAT chunks (each under the 0x7FFFFFFF chunk ceiling) with the
    // prompt after them: nothing of the 3 GiB is read or allocated.
    const half = 1.5 * 1024 * 1024 * 1024;
    const head = concat(bytes.SIGNATURE, ihdr(8192, 8192), u32(half), latin1("IDAT"));
    const idat1 = { start: bytes.SIGNATURE.length + ihdr().length, end: bytes.SIGNATURE.length + ihdr().length + 8 + half + 4 };
    const idat2 = { start: idat1.end, end: idat1.end + 8 + half + 4 };
    const between = concat(u32(0), u32(half), latin1("IDAT"));
    const tail = concat(u32(0), tEXt("prompt", '{"3":{"class_type":"KSampler"}}'), iend());
    const size = idat2.end - 4 + tail.length;
    const file = sparseBlob([
        { offset: 0, bytes: head },
        { offset: idat1.end - 4, bytes: between },
        { offset: idat2.end - 4, bytes: tail },
    ], size);

    const result = await readPngMetadataFile(file);

    assert.equal(result.status, "complete");
    assert.deepEqual(result.records, { prompt: '{"3":{"class_type":"KSampler"}}' });
    assert.deepEqual([result.width, result.height], [8192, 8192]);
    const insideIdat = file.ranges.filter(([start, end]) => [idat1, idat2].some((region) => start >= region.start && end <= region.end));
    assert.ok(insideIdat.length >= 2, "the IDAT headers were read");
    for (const [start, end] of insideIdat) assert.ok(end - start <= 12, `range inside IDAT longer than 12: [${start}, ${end})`);
    const total = file.ranges.reduce((sum, [start, end]) => sum + (end - start), 0);
    assert.ok(total <= PNG_READ_CEILING, `read ${total} bytes`);
});

test("serves the retained prefix without slicing it again", async () => {
    const whole = png(tEXt("prompt", "p"));
    const file = recordingBlob(whole);
    const result = await readPngMetadataFile(file, { prefix: whole.subarray(0, 32) });
    assert.equal(result.status, "complete");
    assert.ok(file.ranges.every(([start]) => start >= 32), JSON.stringify(file.ranges));
    assert.deepEqual(result.records, { prompt: "p" });
});

test("unknown text keys are skipped by length without reading their body", async () => {
    const body = fill(0x41, 1000);
    const whole = png(tEXt("Software", body), tEXt("prompt", "p"));
    const softwareStart = bytes.SIGNATURE.length + ihdr().length + idat().length;
    const bodyStart = softwareStart + 8 + "Software".length + 1;
    const bodyEnd = bodyStart + body.length + 4;
    const file = recordingBlob(whole);
    const result = await readPngMetadataFile(file);
    assert.equal(result.status, "complete");
    const insideBody = file.ranges.filter(([start, end]) => start < bodyEnd && end > softwareStart + 8 + 80);
    assert.deepEqual(insideBody, [], "the body past the 80-byte keyword prefix was sliced");
});

const WF_OVER = WORKFLOW_BUDGET + 1;
const bigWorkflow = () => tEXt("workflow", fill(0x7b, WF_OVER - "workflow".length - 1));

// One row per Python branch (prompt_inspector.py:91-260).
const RULES = [
    { name: "bad IHDR CRC", file: () => raw(ihdr(1, 1, { crc: 1 }), idat(), iend()), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "bad text CRC", file: () => png(tEXt("prompt", "p", { crc: 1 })), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "bad IEND CRC", file: () => raw(ihdr(), idat(), iend({ crc: 1 })), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "first chunk not IHDR", file: () => raw(idat(), ihdr(), iend()), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "IHDR not 13 bytes", file: () => raw(chunk("IHDR", concat(u32(1), u32(1), [8, 6, 0, 0])), idat(), iend()), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "second IHDR", file: () => raw(ihdr(), idat(), ihdr(), iend()), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "zero width", file: () => raw(ihdr(0, 1), idat(), iend()), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "zero height", file: () => raw(ihdr(1, 0), idat(), iend()), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "length 0x80000000", file: () => raw(ihdr(), idat(), chunk("tEXt", latin1("k\0v"), { declaredLength: 0x80000000 }), iend()), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "end past size", file: () => raw(ihdr(), idat(), chunk("tEXt", latin1("k\0v"), { declaredLength: 100 }), iend()), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "file ends inside a chunk header", file: () => raw(ihdr(), idat(), u32(5)), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "file ends before any chunk", file: () => raw(), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "too short for a signature", file: () => bytes.SIGNATURE.subarray(0, 5), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "not a PNG", file: () => latin1("GIF89a................................."), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "IEND without IDAT", file: () => raw(ihdr(), tEXt("prompt", "p"), iend()), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "nonzero IEND length", file: () => raw(ihdr(), idat(), chunk("IEND", [1])), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "duplicate key with a different length", file: () => png(tEXt("prompt", "one"), tEXt("prompt", "other")), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "duplicate key, same length, different bytes", file: () => png(tEXt("prompt", "one"), tEXt("prompt", "two")), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "duplicate key, same bytes, different chunk type", file: () => png(tEXt("prompt", "one"), chunk("zTXt", latin1("prompt\0one"))), expect: { status: "unreadable", reason: "invalid-png" } },
    {
        name: "byte-identical duplicate kept once",
        file: () => png(tEXt("prompt", "same"), tEXt("prompt", "same"), tEXt("prompt", "same")),
        expect: { status: "complete", records: { prompt: "same" }, limited: [], width: 1, height: 1 },
    },
    {
        name: "parameters copies differing only in Batch index keep the last",
        file: () => png(tEXt("parameters", parametersText("cat", 0)), tEXt("parameters", parametersText("cat", 1)), tEXt("parameters", parametersText("cat", 12))),
        expect: { status: "complete", records: { parameters: parametersText("cat", 12) }, limited: [], width: 1, height: 1 },
    },
    { name: "parameters copies with any other difference", file: () => png(tEXt("parameters", parametersText("cat", 0)), tEXt("parameters", parametersText("dog", 1))), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "parameters copies without a settings footer", file: () => png(tEXt("parameters", "cat"), tEXt("parameters", "dog")), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "parameters copies with two footers", file: () => png(tEXt("parameters", `${parametersText("cat", 0)}\nSteps: 1, x`), tEXt("parameters", `${parametersText("cat", 1)}\nSteps: 1, x`)), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "parameters copies with two batch indexes", file: () => png(tEXt("parameters", parametersText("cat", 0, { seedField: "Batch index: 0" })), tEXt("parameters", parametersText("cat", 1, { seedField: "Batch index: 1" }))), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "parameters copies whose batch index is not a whole settings field", file: () => png(tEXt("parameters", "Steps: 20, Batch index: 0 trailing"), tEXt("parameters", "Steps: 20, Batch index: 1 trailing")), expect: { status: "unreadable", reason: "invalid-png" } },
    {
        name: "workflow 4 MiB + 1 encoded is limited, other records intact",
        file: () => png(tEXt("prompt", "p"), bigWorkflow(), tEXt("parameters", "cat\nSteps: 1, x")),
        expect: { status: "complete", records: { prompt: "p", parameters: "cat\nSteps: 1, x" }, limited: ["workflow"], width: 1, height: 1 },
    },
    {
        name: "a duplicate of a limited workflow is skipped",
        file: () => png(bigWorkflow(), bigWorkflow(), tEXt("prompt", "p")),
        expect: { status: "complete", records: { prompt: "p" }, limited: ["workflow"], width: 1, height: 1 },
    },
    {
        name: "workflow over 4 MiB decoded (zTXt) is limited",
        file: () => png(zTXt("workflow", fill(0x7b, WORKFLOW_BUDGET + 1), { level: 9 }), tEXt("prompt", "p")),
        expect: { status: "complete", records: { prompt: "p" }, limited: ["workflow"], width: 1, height: 1 },
    },
    {
        name: "workflow at exactly 4 MiB decoded is kept",
        file: () => png(zTXt("workflow", fill(0x7b, WORKFLOW_BUDGET), { level: 9 })),
        check: (result) => {
            assert.equal(result.status, "complete");
            assert.equal(result.records.workflow.length, WORKFLOW_BUDGET);
            assert.deepEqual(result.limited, []);
        },
    },
    {
        name: "workflow under 4 MiB inflated but over 4 MiB as UTF-8 is limited",
        file: () => png(zTXt("workflow", fill(0xe9, WORKFLOW_BUDGET - 10), { level: 9 }), tEXt("prompt", "p")),
        expect: { status: "complete", records: { prompt: "p" }, limited: ["workflow"], width: 1, height: 1 },
    },
    { name: "roles 2 MiB + 1 encoded", file: () => png(tEXt("prompt", fill(0x41, ROLE_BUDGET + 1 - "prompt".length - 1))), expect: { status: "unreadable", reason: "metadata-limit" } },
    { name: "roles summed over 2 MiB encoded", file: () => png(tEXt("prompt", fill(0x41, 1.5 * MiB)), tEXt("parameters", fill(0x41, 0.5 * MiB))), expect: { status: "unreadable", reason: "metadata-limit" } },
    { name: "roles 2 MiB + 1 decoded via zTXt", file: () => png(zTXt("prompt", fill(0x41, ROLE_BUDGET + 1), { level: 9 })), expect: { status: "unreadable", reason: "metadata-limit" } },
    { name: "roles over 2 MiB as UTF-8 from Latin-1 high bytes", file: () => png(tEXt("prompt", fill(0xe9, MiB + 1))), expect: { status: "unreadable", reason: "metadata-limit" } },
    {
        name: "roles share the decoded budget across records",
        file: () => png(zTXt("prompt", fill(0x41, 1.5 * MiB), { level: 9 }), zTXt("parameters", fill(0x41, 0.5 * MiB + 1), { level: 9 })),
        expect: { status: "unreadable", reason: "metadata-limit" },
    },
    {
        name: "roles at exactly 2 MiB decoded are kept",
        file: () => png(zTXt("prompt", fill(0x41, 1.5 * MiB), { level: 9 }), zTXt("parameters", fill(0x41, 0.5 * MiB), { level: 9 })),
        check: (result) => {
            assert.equal(result.status, "complete");
            assert.equal(result.records.prompt.length + result.records.parameters.length, ROLE_BUDGET);
        },
    },
    {
        name: "duplicate workflow bytes capped at 8 MiB: the workflow becomes limited",
        file: () => {
            const workflow = tEXt("workflow", fill(0x7b, 3 * MiB));
            return png(workflow, workflow, workflow, workflow, tEXt("prompt", "p"));
        },
        expect: { status: "complete", records: { prompt: "p" }, limited: ["workflow"], width: 1, height: 1 },
    },
    {
        name: "duplicate workflow bytes within 8 MiB keep the workflow",
        file: () => {
            const workflow = tEXt("workflow", fill(0x7b, 3 * MiB));
            return png(workflow, workflow, workflow);
        },
        check: (result) => {
            assert.equal(result.status, "complete");
            assert.equal(result.records.workflow.length, 3 * MiB);
            assert.deepEqual(result.limited, []);
        },
    },
    {
        name: "duplicate role bytes capped at 4 MiB",
        file: () => {
            const prompt = tEXt("prompt", fill(0x41, 1.5 * MiB));
            return png(prompt, prompt, prompt, prompt);
        },
        expect: { status: "unreadable", reason: "metadata-limit" },
    },
    {
        name: "duplicate decoded parameters capped at 4 MiB",
        file: () => {
            const copy = (index) => zTXt("parameters", parametersText(fill(0x41, 1.5 * MiB).toString("latin1"), index), { level: 9 });
            return png(copy(0), copy(1), copy(2), copy(3));
        },
        expect: { status: "unreadable", reason: "metadata-limit" },
    },
    {
        name: "a longer batch copy draws the growth from the role budget",
        file: () => {
            const base = parametersText("", 9);
            const positive = fill(0x41, ROLE_BUDGET - base.length - 1).toString("latin1");
            return png(zTXt("parameters", parametersText(positive, 9), { level: 9 }), zTXt("parameters", parametersText(positive, 100), { level: 9 }));
        },
        expect: { status: "unreadable", reason: "metadata-limit" },
    },
    {
        name: "a longer batch copy within the role budget is kept",
        file: () => {
            const base = parametersText("", 9);
            const positive = fill(0x41, ROLE_BUDGET - base.length - 2).toString("latin1");
            return png(zTXt("parameters", parametersText(positive, 9), { level: 9 }), zTXt("parameters", parametersText(positive, 10), { level: 9 }));
        },
        check: (result) => {
            assert.equal(result.status, "complete");
            assert.ok(result.records.parameters.endsWith("Batch index: 10, Seed: 1"));
        },
    },
    {
        name: "a batch copy inflating past the role budget",
        file: () => png(tEXt("parameters", parametersText("cat", 0)), zTXt("parameters", parametersText(fill(0x41, ROLE_BUDGET).toString("latin1"), 1), { level: 9 })),
        expect: { status: "unreadable", reason: "metadata-limit" },
    },
    {
        name: "10,000 chunks without IEND",
        file: () => concat(bytes.SIGNATURE, ihdr(), idat(), ...Array.from({ length: 9999 }, () => chunk("xxXx", [])), iend()),
        expect: { status: "unreadable", reason: "metadata-limit" },
    },
    {
        name: "IEND as the 10,000th chunk is read",
        file: () => concat(bytes.SIGNATURE, ihdr(), idat(), ...Array.from({ length: 9997 }, () => chunk("xxXx", [])), iend()),
        expect: { status: "complete", records: {}, limited: [], width: 1, height: 1 },
    },
    { name: "zTXt method not 0", file: () => png(zTXt("prompt", "p", { method: 1 })), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "zTXt without a method byte", file: () => png(chunk("zTXt", latin1("prompt\0"))), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "iTXt flag 2", file: () => png(iTXt("prompt", "p", { flag: 2 })), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "iTXt compressed with method 1", file: () => png(iTXt("prompt", "p", { flag: 1, method: 1 })), expect: { status: "unreadable", reason: "invalid-png" } },
    {
        name: "iTXt uncompressed ignores the method byte",
        file: () => png(iTXt("prompt", "p", { flag: 0, method: 7 })),
        expect: { status: "complete", records: { prompt: "p" }, limited: [], width: 1, height: 1 },
    },
    { name: "iTXt shorter than flag and method", file: () => png(chunk("iTXt", latin1("prompt\0\0"))), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "iTXt missing the translated keyword separator", file: () => png(chunk("iTXt", concat(latin1("prompt\0"), [0, 0], latin1("en\0text")))), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "iTXt non-ASCII language tag", file: () => png(iTXt("prompt", "p", { lang: [0xc3, 0xa9] })), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "iTXt invalid UTF-8 translated keyword", file: () => png(iTXt("prompt", "p", { translated: [0xff] })), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "iTXt invalid UTF-8 text", file: () => png(iTXt("prompt", [0xc3, 0x28])), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "iTXt encoded surrogate", file: () => png(iTXt("prompt", [0xed, 0xa0, 0x80])), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "NUL in tEXt text", file: () => png(tEXt("prompt", "a\0b")), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "NUL in inflated zTXt text", file: () => png(zTXt("prompt", "a\0b")), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "NUL in iTXt text", file: () => png(iTXt("prompt", "a\0b")), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "keyword of 0 bytes", file: () => png(chunk("tEXt", latin1("\0v"))), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "keyword of 80 bytes", file: () => png(tEXt("k".repeat(80), "v")), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "keyword without a NUL", file: () => png(chunk("tEXt", latin1("nonul"))), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "empty text chunk", file: () => png(chunk("tEXt", [])), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "malformed keyword on an unknown key still fails", file: () => png(tEXt("prompt", "p"), chunk("tEXt", latin1("x".repeat(100)))), expect: { status: "unreadable", reason: "invalid-png" } },
    {
        name: "keyword of 79 bytes is read",
        file: () => png(tEXt("k".repeat(79), "v"), tEXt("prompt", "p")),
        expect: { status: "complete", records: { prompt: "p" }, limited: [], width: 1, height: 1 },
    },
    {
        name: "Latin-1 0xE9 is an e-acute in tEXt and zTXt",
        file: () => png(tEXt("prompt", [0xe9]), zTXt("parameters", [0xe9, 0x0a, 0x53, 0x74, 0x65, 0x70, 0x73, 0x3a, 0x20, 0x31])),
        expect: { status: "complete", records: { prompt: "é", parameters: "é\nSteps: 1" }, limited: [], width: 1, height: 1 },
    },
    {
        name: "iTXt BOM is preserved, compressed and not",
        file: () => png(iTXt("prompt", "﻿{}"), iTXt("workflow", "﻿[]", { flag: 1, lang: "en", translated: "wörkflow" })),
        expect: { status: "complete", records: { prompt: "﻿{}", workflow: "﻿[]" }, limited: [], width: 1, height: 1 },
    },
    { name: "truncated zlib data", file: () => png(zTXt("prompt", "x".repeat(500), { compressed: zlib.deflateSync(latin1("x".repeat(500))).subarray(0, 8) })), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "trailing bytes after the zlib stream", file: () => png(zTXt("prompt", "p", { compressed: concat(zlib.deflateSync(latin1("p")), [1, 2, 3]) })), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "empty zlib data", file: () => png(zTXt("prompt", "p", { compressed: [] })), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "garbage zlib data", file: () => png(zTXt("prompt", "p", { compressed: [1, 2, 3, 4, 5, 6, 7, 8] })), expect: { status: "unreadable", reason: "invalid-png" } },
    { name: "truncated compressed iTXt", file: () => png(iTXt("prompt", "p", { flag: 1, compressed: zlib.deflateSync(latin1("x".repeat(500))).subarray(0, 8) })), expect: { status: "unreadable", reason: "invalid-png" } },
    {
        name: "chunks after IEND are ignored",
        file: () => concat(png(tEXt("prompt", "p")), tEXt("workflow", "w")),
        expect: { status: "complete", records: { prompt: "p" }, limited: [], width: 1, height: 1 },
    },
    {
        name: "non-ASCII chunk types are skipped",
        file: () => png(chunk("\xff\xfe\x80\x81", [1, 2, 3]), tEXt("prompt", "p")),
        expect: { status: "complete", records: { prompt: "p" }, limited: [], width: 1, height: 1 },
    },
];

for (const rule of RULES) {
    test(`server rules, one by one: ${rule.name}`, async () => {
        const result = await read(rule.file());
        if (rule.check) rule.check(result);
        else assert.deepEqual(result, rule.expect);
    });
}

test("decompress: undefined with zTXt is decompression-unavailable and yields no records", async () => {
    const result = await read(png(tEXt("prompt", "p"), zTXt("workflow", "w")), { decompress: undefined });
    assert.deepEqual(result, { status: "unreadable", reason: "decompression-unavailable" });
    const plain = await read(png(tEXt("prompt", "p"), zTXt("Comment", "skipped")), { decompress: undefined });
    assert.deepEqual(plain.records, { prompt: "p" });
});

function instrumentedStream(transform) {
    const stats = { writes: [], inFlight: 0, maxInFlight: 0 };
    return {
        stats,
        Stream: class {
            constructor() {
                const stream = new TransformStream({
                    async transform(chunk, controller) {
                        stats.writes.push(chunk.byteLength);
                        stats.inFlight += 1;
                        stats.maxInFlight = Math.max(stats.maxInFlight, stats.inFlight);
                        await transform(chunk, controller);
                        stats.inFlight -= 1;
                    },
                });
                this.writable = stream.writable;
                this.readable = stream.readable;
            }
        },
    };
}

test("inflation keeps one <=256-byte write in flight", async () => {
    const body = fill(0x41, 3000);
    const { stats, Stream } = instrumentedStream(async (chunk, controller) => {
        await new Promise((resolve) => setTimeout(resolve, 1));
        controller.enqueue(chunk);
    });
    // The fake "inflates" by passing bytes through, so the stored body is the text.
    const result = await read(png(chunk("zTXt", concat(latin1("prompt\0"), [0], body))), { decompress: Stream });
    assert.equal(result.status, "complete");
    assert.equal(result.records.prompt, body.toString("latin1"));
    assert.ok(stats.writes.every((size) => size <= 256), JSON.stringify(stats.writes));
    assert.equal(stats.writes.reduce((sum, size) => sum + size, 0), body.length);
    assert.equal(stats.maxInFlight, 1);
});

test("inflation settles once on abort with an AbortError and no unhandled rejection", async () => {
    const unhandled = [];
    const onUnhandled = (reason) => unhandled.push(reason);
    process.on("unhandledRejection", onUnhandled);
    try {
        const { stats, Stream } = instrumentedStream(() => new Promise(() => {}));
        const controller = new AbortController();
        const pending = readPngMetadataFile(blobOf(png(zTXt("prompt", "p".repeat(1000)))), { decompress: Stream, signal: controller.signal });
        let settled = 0;
        pending.then(() => settled++, () => settled++);
        await new Promise((resolve) => setTimeout(resolve, 5));
        assert.equal(settled, 0, "the read waits on the stream");
        controller.abort();
        await assert.rejects(pending, (error) => error.name === "AbortError");
        await new Promise((resolve) => setTimeout(resolve, 10));
        assert.equal(settled, 1);
        assert.equal(stats.maxInFlight, 1);
        assert.deepEqual(unhandled, []);
    } finally {
        process.off("unhandledRejection", onUnhandled);
    }
});

test("a stream error under a live signal is still invalid-png, not an abort", async () => {
    const controller = new AbortController();
    const result = await read(png(zTXt("prompt", "p", { compressed: [1, 2, 3, 4, 5, 6, 7, 8] })), { signal: controller.signal });
    assert.deepEqual(result, { status: "unreadable", reason: "invalid-png" });
    assert.equal(controller.signal.aborted, false);
});

test("an already aborted signal rejects before any slice", async () => {
    const controller = new AbortController();
    controller.abort();
    const file = recordingBlob(png(tEXt("prompt", "p")));
    await assert.rejects(readPngMetadataFile(file, { signal: controller.signal }), (error) => error.name === "AbortError");
    assert.deepEqual(file.ranges, []);
});

test("aborting between slices rejects with an AbortError", async () => {
    const controller = new AbortController();
    const whole = png(tEXt("prompt", "p"));
    let slices = 0;
    const file = {
        size: whole.length,
        slice(start, end) {
            return {
                arrayBuffer: async () => {
                    if (++slices === 3) controller.abort();
                    return whole.slice(start, end).buffer;
                },
            };
        },
    };
    await assert.rejects(readPngMetadataFile(file, { signal: controller.signal }), (error) => error.name === "AbortError");
});

test("aborting while inflating a real deflate stream rejects with an AbortError", async () => {
    const controller = new AbortController();
    let inflating = false;
    class Tracked extends DecompressionStream {
        constructor(format) {
            super(format);
            inflating = true;
        }
    }
    // The slow slices make the pacer yield inside the inflater's feed loop; that
    // yield aborts, so the real stream is torn down mid-inflation.
    const yieldControl = async () => {
        if (inflating) controller.abort();
    };
    const slow = slowBlob(png(zTXt("prompt", "p", { compressed: require("node:crypto").randomBytes(5000) })), 10);
    await assert.rejects(readPngMetadataFile(slow, { signal: controller.signal, yieldControl, decompress: Tracked }), (error) => error.name === "AbortError");
    assert.equal(inflating, true);
});

test("yields control about every 8 ms while the file answers slowly", async () => {
    let yields = 0;
    const yieldControl = async () => {
        yields += 1;
    };
    const result = await readPngMetadataFile(slowBlob(png(tEXt("prompt", "p")), 10), { yieldControl });
    assert.equal(result.status, "complete");
    assert.ok(yields >= 1, `yielded ${yields} times`);
});

test("the default yield lets the event loop turn", async () => {
    const result = await readPngMetadataFile(slowBlob(png(tEXt("prompt", "p")), 10));
    assert.deepEqual(result.records, { prompt: "p" });
});

test("a slice that fails is a read failure", async () => {
    assert.deepEqual(await readPngMetadataFile(failingBlob(100)), { status: "unreadable", reason: "read-failure" });
    assert.deepEqual(await readPngMetadataFile(null), { status: "unreadable", reason: "read-failure" });
});

test("a file that shrank under the reader is a read failure", async () => {
    const whole = png(tEXt("prompt", "p"));
    assert.deepEqual(await readPngMetadataFile(shrunkBlob(whole.subarray(0, 40), whole.length)), { status: "unreadable", reason: "read-failure" });
});

test("classifier", () => {
    assert.deepEqual(classifyPrefix(bytes.SIGNATURE), { media: "image", kind: "png" });
    assert.deepEqual(classifyPrefix(Uint8Array.from([0xff, 0xd8, 0xff, 0xe0])), { media: "image", kind: "jpeg" });
    assert.deepEqual(classifyPrefix(latin1("RIFF\0\0\0\0WEBPVP8 ")), { media: "image", kind: "webp" });
    assert.deepEqual(classifyPrefix(latin1("\0\0\0\x18ftypisom\0\0\0\0")), { media: "video", kind: "iso-bmff" });
    assert.deepEqual(classifyPrefix(latin1("\0\0\0\x14ftypqt  ")), { media: "video", kind: "iso-bmff" });
    assert.deepEqual(classifyPrefix(Uint8Array.from([0x1a, 0x45, 0xdf, 0xa3, 0x01])), { media: "video", kind: "matroska" });
    assert.deepEqual(classifyPrefix(latin1("RIFF\0\0\0\0AVI LIST")), { media: "video", kind: "avi" });
    const asf = Uint8Array.from([0x30, 0x26, 0xb2, 0x75, 0x8e, 0x66, 0xcf, 0x11, 0xa6, 0xd9, 0x00, 0xaa, 0x00, 0x62, 0xce, 0x6c, 0, 0]);
    assert.deepEqual(classifyPrefix(asf), { media: "video", kind: "asf" });
    assert.deepEqual(classifyPrefix(asf.buffer), { media: "video", kind: "asf" });
    for (const text of ["plain text file", "<!DOCTYPE html><html>", '<?xml version="1.0"?><svg', "#EXTM3U\n#EXT-X-VERSION:3", "RIFF\0\0\0\0WAVEfmt ", "\0\0\0\x18moov", ""]) {
        assert.equal(classifyPrefix(latin1(text)), null, text);
    }
    assert.equal(classifyPrefix(bytes.SIGNATURE.subarray(0, 7)), null);
    assert.equal(classifyPrefix(Uint8Array.from([0xff, 0xd8])), null);
    assert.equal(classifyPrefix("not bytes"), null);
    assert.equal(classifyPrefix(null), null);
});

test("image header: JPEG and WebP fixtures match Pillow's dimensions", async () => {
    // tests/test_local_inspect_parity.py asserts the same two files against Pillow.
    const jpeg = blobOf(fs.readFileSync(path.join(FIXTURES, "tiny.jpg")));
    assert.deepEqual(await readImageHeader(jpeg, "jpeg"), { format: "JPEG", width: 48, height: 32 });
    const webp = blobOf(fs.readFileSync(path.join(FIXTURES, "tiny.webp")));
    assert.deepEqual(await readImageHeader(webp, "webp"), { format: "WEBP", width: 40, height: 24 });
});

test("image header: JPEG marker walk skips padding, junk, standalone markers and tables", async () => {
    const segment = (marker, body) => concat([0xff, marker], u32(body.length + 2).subarray(2), body);
    const sof = (marker, width, height) => segment(marker, concat([8], u32(height).subarray(2), u32(width).subarray(2), [3]));
    const sized = concat([0xff, 0xd8], segment(0xe1, fill(0, 20)), [0xff, 0xff, 0xff, 0x00, 0x00, 0x11], [0xff, 0xd0], [0xff, 0x01], segment(0xc4, fill(0, 4)), segment(0xc8, []), segment(0xcc, [1]), sof(0xc2, 300, 200));
    assert.deepEqual(await readImageHeader(blobOf(sized), "jpeg"), { format: "JPEG", width: 300, height: 200 });
    for (const marker of [0xc0, 0xc1, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf]) {
        assert.deepEqual(await readImageHeader(blobOf(concat([0xff, 0xd8], sof(marker, 7, 9))), "jpeg"), { format: "JPEG", width: 7, height: 9 }, marker.toString(16));
    }
    const none = { format: "JPEG", width: null, height: null };
    assert.deepEqual(await readImageHeader(blobOf(concat([0xff, 0xd8], segment(0xda, [1]))), "jpeg"), none, "SOS before SOF");
    assert.deepEqual(await readImageHeader(blobOf(concat([0xff, 0xd8], [0xff])), "jpeg"), none, "ends on a marker byte");
    assert.deepEqual(await readImageHeader(blobOf(concat([0xff, 0xd8], [0xff, 0xe1, 0x00])), "jpeg"), none, "ends inside a length");
    assert.deepEqual(await readImageHeader(blobOf(concat([0xff, 0xd8], [0xff, 0xc0, 0x00, 0x08, 8, 0])), "jpeg"), none, "ends inside SOF");
    assert.deepEqual(await readImageHeader(blobOf(concat([0xff, 0xd8], segment(0xe1, []))), "jpeg"), none, "no SOF at all");
    assert.deepEqual(await readImageHeader(blobOf(Uint8Array.from([0xff])), "jpeg"), none, "too short");
    assert.deepEqual(await readImageHeader(blobOf(latin1("RIFF")), "jpeg"), none, "not a JPEG");
    // A SOF past the 64 KiB read gives no dimensions rather than reading on.
    assert.deepEqual(await readImageHeader(blobOf(concat([0xff, 0xd8], segment(0xe1, fill(0, 65000)), segment(0xe2, fill(0, 1000)), sof(0xc0, 5, 5))), "jpeg"), none, "SOF beyond 64 KiB");
});

test("image header: WebP VP8, VP8L and VP8X headers", async () => {
    const riff = (...rest) => concat(latin1("RIFF"), u32(0), latin1("WEBP"), ...rest);
    const chunkHeader = (type, size) => concat(latin1(type), Uint8Array.from([size & 0xff, (size >>> 8) & 0xff, (size >>> 16) & 0xff, 0]));
    const vp8 = riff(chunkHeader("VP8 ", 100), [0x30, 0x0b, 0x00, 0x9d, 0x01, 0x2a], [40, 0x40], [24, 0x80], fill(0, 20));
    assert.deepEqual(await readImageHeader(blobOf(vp8), "webp"), { format: "WEBP", width: 40, height: 24 }, "scale bits are masked");
    // 14-bit width-1 = 63 and height-1 = 47, little-endian bit-packed after the 0x2f signature.
    const vp8l = riff(chunkHeader("VP8L", 100), [0x2f], [0x3f, 0xc0, 0x0b, 0x00, 0x00], fill(0, 20));
    assert.deepEqual(await readImageHeader(blobOf(vp8l), "webp"), { format: "WEBP", width: 64, height: 48 });
    const vp8x = riff(chunkHeader("VP8X", 10), [0x10, 0, 0, 0], [0xff, 0x00, 0x01], [0x2f, 0x00, 0x00], fill(0, 20));
    assert.deepEqual(await readImageHeader(blobOf(vp8x), "webp"), { format: "WEBP", width: 65792, height: 48 });
    const none = { format: "WEBP", width: null, height: null };
    assert.deepEqual(await readImageHeader(blobOf(riff(chunkHeader("VP8 ", 100), [0x30, 0x0b, 0x00, 0x00, 0x01, 0x2a], fill(0, 24))), "webp"), none, "bad start code");
    assert.deepEqual(await readImageHeader(blobOf(riff(chunkHeader("VP8L", 100), [0x00], fill(0, 24))), "webp"), none, "bad VP8L signature");
    assert.deepEqual(await readImageHeader(blobOf(riff(chunkHeader("ALPH", 100), fill(0, 24))), "webp"), none, "unknown first chunk");
    assert.deepEqual(await readImageHeader(blobOf(riff(chunkHeader("VP8 ", 100))), "webp"), none, "too short");
    assert.deepEqual(await readImageHeader(blobOf(latin1("RIFF\0\0\0\0WAVEfmt ".padEnd(40, "\0"))), "webp"), none, "not WebP");
});

test("image header: other kinds give no format and the read stays within 64 KiB", async () => {
    assert.deepEqual(await readImageHeader(blobOf(bytes.SIGNATURE), "png"), { format: null, width: null, height: null });
    const file = recordingBlob(concat([0xff, 0xd8], fill(0, 200000)));
    await readImageHeader(file, "jpeg");
    assert.deepEqual(file.ranges, [[0, 65536]]);
    const small = recordingBlob(concat([0xff, 0xd8, 0xff]));
    await readImageHeader(small, "jpeg");
    assert.deepEqual(small.ranges, [[0, 3]]);
});
