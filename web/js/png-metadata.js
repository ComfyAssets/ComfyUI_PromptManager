/**
 * PNG text-chunk reader: the one place that walks a PNG's chunks to pull out
 * the tEXt/iTXt entries ComfyUI writes (`prompt`, `workflow`, ...).
 *
 * Only uncompressed text is returned. zTXt chunks and iTXt chunks with the
 * compression flag set are skipped rather than decoded as garbage; ComfyUI
 * never writes those. Nothing throws: unreadable input yields {}.
 *
 * Loadable in the browser (window.PngMetadata) and in Node for tests.
 */
(function (root) {
    "use strict";

    // Typed arrays cannot be frozen; the module keeps this private copy and only reads it
    const PNG_SIGNATURE = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
    const CHUNK_HEADER = 8; // 4-byte length + 4-byte type
    const CHUNK_CRC = 4;

    function toBytes(input) {
        if (input instanceof Uint8Array) return input;
        if (typeof ArrayBuffer !== "undefined" && input instanceof ArrayBuffer) return new Uint8Array(input);
        if (ArrayBuffer.isView(input)) return new Uint8Array(input.buffer, input.byteOffset, input.byteLength);
        return null;
    }

    function hasSignature(bytes) {
        if (bytes.length < PNG_SIGNATURE.length) return false;
        for (let i = 0; i < PNG_SIGNATURE.length; i++) if (bytes[i] !== PNG_SIGNATURE[i]) return false;
        return true;
    }

    function latin1(bytes, start, end) {
        let out = "";
        for (let i = start; i < end; i++) out += String.fromCharCode(bytes[i]);
        return out;
    }

    function utf8(bytes, start, end) {
        return new TextDecoder("utf-8").decode(bytes.subarray(start, end));
    }

    function indexOfNul(bytes, start, end) {
        for (let i = start; i < end; i++) if (bytes[i] === 0) return i;
        return -1;
    }

    /** tEXt: keyword NUL text, both Latin-1. */
    function readTEXt(bytes, start, end) {
        const nul = indexOfNul(bytes, start, end);
        if (nul === -1) return null;
        return [latin1(bytes, start, nul), latin1(bytes, nul + 1, end)];
    }

    /** iTXt: keyword NUL flag method language NUL translated NUL text(UTF-8). */
    function readITXt(bytes, start, end) {
        const keywordEnd = indexOfNul(bytes, start, end);
        if (keywordEnd === -1 || keywordEnd + 2 >= end) return null;
        const compressed = bytes[keywordEnd + 1] !== 0;
        const langEnd = indexOfNul(bytes, keywordEnd + 3, end);
        if (langEnd === -1) return null;
        const translatedEnd = indexOfNul(bytes, langEnd + 1, end);
        if (translatedEnd === -1) return null;
        if (compressed) return null;
        return [latin1(bytes, start, keywordEnd), utf8(bytes, translatedEnd + 1, end)];
    }

    /**
     * @param {Uint8Array|ArrayBuffer|ArrayBufferView} input - PNG file bytes
     * @returns {Object<string, string>} keyword -> text for every readable text chunk
     */
    function parsePngTextChunks(input) {
        const bytes = toBytes(input);
        if (!bytes || !hasSignature(bytes)) return {};

        const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
        const result = {};
        let offset = PNG_SIGNATURE.length;

        while (offset + CHUNK_HEADER <= bytes.length) {
            const length = view.getUint32(offset);
            const type = latin1(bytes, offset + 4, offset + 8);
            const dataStart = offset + CHUNK_HEADER;
            const dataEnd = dataStart + length;
            if (dataEnd + CHUNK_CRC > bytes.length) break; // truncated or bogus length
            if (type === "IEND") break;

            let entry = null;
            if (type === "tEXt") entry = readTEXt(bytes, dataStart, dataEnd);
            else if (type === "iTXt") entry = readITXt(bytes, dataStart, dataEnd);
            if (entry) result[entry[0]] = entry[1];

            offset = dataEnd + CHUNK_CRC;
        }
        return result;
    }

    const api = { parsePngTextChunks, PNG_SIGNATURE };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    else root.PngMetadata = api;
})(typeof window !== "undefined" ? window : globalThis);
