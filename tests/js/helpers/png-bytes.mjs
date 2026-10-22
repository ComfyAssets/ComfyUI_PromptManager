// Byte-exact PNG builders for the local reader tests (real CRC32 and zlib), and
// File-like blobs: one that records every [start, end) slice over a sparse layout
// that is never allocated, one that answers slowly, one that fails or shrinks.
import zlib from "node:zlib";

export const SIGNATURE = Uint8Array.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

export function u32(n) {
    return Uint8Array.from([(n >>> 24) & 0xff, (n >>> 16) & 0xff, (n >>> 8) & 0xff, n & 0xff]);
}

export function latin1(text) {
    return Buffer.from(text, "latin1");
}

export function utf8(text) {
    return Buffer.from(text, "utf8");
}

export function concat(...parts) {
    return Uint8Array.from(Buffer.concat(parts.map((part) => Buffer.from(part))));
}

/**
 * One chunk with a real CRC32 over type + data. `crc` overrides the checksum and
 * `declaredLength` the length field (to declare more data than is present).
 */
export function chunk(type, data, { crc, declaredLength } = {}) {
    const typeBytes = latin1(type);
    const body = Buffer.from(data);
    const sum = crc ?? zlib.crc32(Buffer.concat([typeBytes, body]));
    return concat(u32(declaredLength ?? body.length), typeBytes, body, u32(sum));
}

export function ihdr(width = 1, height = 1, options) {
    return chunk("IHDR", concat(u32(width), u32(height), [8, 6, 0, 0, 0]), options);
}

export function idat(data = [0]) {
    return chunk("IDAT", data);
}

export function iend(options) {
    return chunk("IEND", [], options);
}

/** tEXt: keyword NUL text, Latin-1 (text may be a string or raw bytes). */
export function tEXt(keyword, text, options) {
    const body = typeof text === "string" ? latin1(text) : Buffer.from(text);
    return chunk("tEXt", concat(latin1(keyword), [0], body), options);
}

/** zTXt: keyword NUL method zlib(text). `compressed` replaces the deflated body. */
export function zTXt(keyword, text, { method = 0, compressed, level = 6 } = {}) {
    const body = compressed ?? zlib.deflateSync(typeof text === "string" ? latin1(text) : Buffer.from(text), { level });
    return chunk("zTXt", concat(latin1(keyword), [0, method], body));
}

/** iTXt: keyword NUL flag method lang NUL translated NUL text (UTF-8, deflated when flag). */
export function iTXt(keyword, text, { flag = 0, method = 0, lang = "", translated = "", compressed } = {}) {
    const raw = typeof text === "string" ? utf8(text) : Buffer.from(text);
    const body = compressed ?? (flag ? zlib.deflateSync(raw) : raw);
    const langBytes = typeof lang === "string" ? latin1(lang) : Buffer.from(lang);
    const translatedBytes = typeof translated === "string" ? utf8(translated) : Buffer.from(translated);
    return concat(chunk("iTXt", concat(latin1(keyword), [0, flag, method], langBytes, [0], translatedBytes, [0], body)));
}

/** A whole PNG: signature, IHDR, IDAT, the given chunks, IEND. */
export function png(...chunks) {
    return concat(SIGNATURE, ihdr(), idat(), ...chunks, iend());
}

/** Signature plus exactly the given chunks (no implied IHDR/IDAT/IEND). */
export function raw(...chunks) {
    return concat(SIGNATURE, ...chunks);
}

export function blobOf(bytes) {
    return new Blob([bytes]);
}

/**
 * A File-like object over a sparse layout: `parts` are [{offset, bytes}] placed in a
 * file of `size` bytes, zero everywhere else; nothing of `size` is allocated. Every
 * slice is recorded in `ranges` as [start, end).
 */
export function sparseBlob(parts, size) {
    const ranges = [];
    function bytesOf(start, end) {
        const out = new Uint8Array(end - start);
        for (const part of parts) {
            const from = Math.max(start, part.offset);
            const to = Math.min(end, part.offset + part.bytes.length);
            if (from < to) out.set(part.bytes.subarray(from - part.offset, to - part.offset), from - start);
        }
        return out;
    }
    return {
        size,
        ranges,
        slice(start, end) {
            ranges.push([start, end]);
            return { arrayBuffer: async () => bytesOf(start, end).buffer };
        },
    };
}

/** A recording blob over real bytes. */
export function recordingBlob(bytes) {
    return sparseBlob([{ offset: 0, bytes }], bytes.length);
}

/** Every slice resolves after `delayMs`. */
export function slowBlob(bytes, delayMs) {
    return {
        size: bytes.length,
        slice(start, end) {
            return {
                arrayBuffer: () => new Promise((resolve) => setTimeout(() => resolve(bytes.slice(start, end).buffer), delayMs)),
            };
        },
    };
}

/** Slices fail (as a browser does when the file vanished). */
export function failingBlob(size) {
    return {
        size,
        slice() {
            return { arrayBuffer: () => Promise.reject(new Error("NotReadableError")) };
        },
    };
}

/** Reports `size` but every slice beyond `actual` bytes comes back short. */
export function shrunkBlob(bytes, size) {
    return {
        size,
        slice(start, end) {
            return { arrayBuffer: async () => bytes.slice(start, Math.min(end, bytes.length)).buffer };
        },
    };
}

/** An A1111 `parameters` record with one settings footer carrying a batch index. */
export function parametersText(positive, batchIndex, { seedField = "Seed: 1" } = {}) {
    return `${positive}\nNegative prompt: blurry\nSteps: 20, Sampler: Euler, Batch index: ${batchIndex}, ${seedField}`;
}
