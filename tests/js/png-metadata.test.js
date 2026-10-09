// Run with: node --test tests/js/
const test = require("node:test");
const assert = require("node:assert/strict");
const { parsePngTextChunks, PNG_SIGNATURE } = require("../../web/js/png-metadata.js");

const SIGNATURE = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];

function u32(n) {
    return [(n >>> 24) & 0xff, (n >>> 16) & 0xff, (n >>> 8) & 0xff, n & 0xff];
}

/** A chunk with a dummy CRC (the parser does not verify it). */
function chunk(type, dataBytes) {
    return [...u32(dataBytes.length), ...Buffer.from(type, "latin1"), ...dataBytes, 0, 0, 0, 0];
}

const IHDR = chunk("IHDR", [...u32(1), ...u32(1), 8, 6, 0, 0, 0]);
const IEND = chunk("IEND", []);

function tEXt(keyword, text) {
    return chunk("tEXt", [...Buffer.from(keyword, "latin1"), 0, ...Buffer.from(text, "latin1")]);
}

function iTXt(keyword, text, { compressed = false } = {}) {
    return chunk("iTXt", [
        ...Buffer.from(keyword, "latin1"), 0,
        compressed ? 1 : 0, 0, // compression flag, compression method
        0, // empty language tag
        0, // empty translated keyword
        ...Buffer.from(text, "utf8"),
    ]);
}

function png(...chunks) {
    return Uint8Array.from([...SIGNATURE, ...IHDR, ...chunks.flat(), ...IEND]);
}

test("exports the PNG signature", () => {
    assert.deepEqual([...PNG_SIGNATURE], SIGNATURE);
});

test("reads a tEXt chunk written by ComfyUI", () => {
    const bytes = png(tEXt("prompt", '{"a":1}'));
    assert.deepEqual(parsePngTextChunks(bytes), { prompt: '{"a":1}' });
});

test("reads several text chunks and an uncompressed iTXt chunk with UTF-8 text", () => {
    const bytes = png(tEXt("prompt", '{"a":1}'), iTXt("workflow", '{"b":"日本語 🙂"}'), tEXt("Software", "ComfyUI"));
    assert.deepEqual(parsePngTextChunks(bytes), {
        prompt: '{"a":1}',
        workflow: '{"b":"日本語 🙂"}',
        Software: "ComfyUI",
    });
});

test("tEXt is Latin-1, so byte 0xE9 is an e-acute", () => {
    const bytes = png(chunk("tEXt", [...Buffer.from("Comment", "latin1"), 0, 0xe9]));
    assert.deepEqual(parsePngTextChunks(bytes), { Comment: "é" });
});

test("skips compressed iTXt and zTXt payloads instead of returning garbage", () => {
    const zTXt = chunk("zTXt", [...Buffer.from("Comment", "latin1"), 0, 0, 0x78, 0x9c, 1, 2, 3]);
    const bytes = png(iTXt("workflow", "zzz", { compressed: true }), zTXt, tEXt("prompt", "ok"));
    assert.deepEqual(parsePngTextChunks(bytes), { prompt: "ok" });
});

test("ignores non-text chunks and a text chunk without a NUL separator", () => {
    const bytes = png(chunk("IDAT", [1, 2, 3]), chunk("tEXt", [...Buffer.from("nonul", "latin1")]), tEXt("k", "v"));
    assert.deepEqual(parsePngTextChunks(bytes), { k: "v" });
});

test("accepts an ArrayBuffer as well as a Uint8Array", () => {
    const bytes = png(tEXt("prompt", "x"));
    assert.deepEqual(parsePngTextChunks(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength)), { prompt: "x" });
});

test("returns {} for garbage, empty and non-PNG input", () => {
    assert.deepEqual(parsePngTextChunks(new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12])), {});
    assert.deepEqual(parsePngTextChunks(new Uint8Array(0)), {});
    assert.deepEqual(parsePngTextChunks(Uint8Array.from(Buffer.from("GIF89a....................", "latin1"))), {});
    assert.deepEqual(parsePngTextChunks(null), {});
    assert.deepEqual(parsePngTextChunks("not bytes"), {});
});

test("a file truncated inside a text chunk yields only the chunks that were complete", () => {
    const full = png(tEXt("prompt", "first"), tEXt("workflow", "second-second-second"));
    const cutInsideSecond = full.subarray(0, full.length - IEND.length - 6);
    assert.deepEqual(parsePngTextChunks(cutInsideSecond), { prompt: "first" });
    const cutInsideFirst = full.subarray(0, SIGNATURE.length + IHDR.length + 10);
    assert.deepEqual(parsePngTextChunks(cutInsideFirst), {});
});

test("a chunk whose declared length overruns the file is ignored", () => {
    const bogus = [...u32(0x7fffffff), ...Buffer.from("tEXt", "latin1"), ...Buffer.from("k\0v", "latin1")];
    const bytes = Uint8Array.from([...SIGNATURE, ...IHDR, ...tEXt("ok", "1"), ...bogus]);
    assert.deepEqual(parsePngTextChunks(bytes), { ok: "1" });
});

test("stops at IEND even if bytes follow it", () => {
    const bytes = Uint8Array.from([...png(tEXt("a", "1")), ...tEXt("b", "2")]);
    assert.deepEqual(parsePngTextChunks(bytes), { a: "1" });
});
