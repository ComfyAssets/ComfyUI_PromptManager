/**
 * PNG text-chunk reader: the one place that walks a PNG's chunks to pull out
 * the tEXt/iTXt entries ComfyUI writes (`prompt`, `workflow`, ...).
 *
 * `parsePngTextChunks` (the legacy pages): only uncompressed text is returned.
 * zTXt chunks and iTXt chunks with the compression flag set are skipped rather
 * than decoded as garbage; ComfyUI never writes those. Nothing throws:
 * unreadable input yields {}.
 *
 * `readPngMetadataFile`, `classifyPrefix` and `readImageHeader` (the Inspect
 * page, #249): bounded readers over a File that port the server's inspection
 * reader so a local file shows exactly what Images shows for the same bytes.
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

    // ------------------------------------------------------------------------
    // Local File readers for the Inspect page (#249): a port of the server's
    // bounded PNG reader (utils/prompt_inspector.py: _png_metadata, _PngRecords,
    // _png_text, _inflate, _batch_normalized), a media classifier and a JPEG/WebP
    // header reader. Nothing here uploads, stores or logs the file. The reader
    // slices only chunk headers, keyword prefixes and the retained records, so a
    // multi-gigabyte PNG costs at most PNG_READ_CEILING bytes of reads.
    // tests/test_local_inspect_parity.py proves it answers as the server does.
    // ------------------------------------------------------------------------

    // Role records (prompt, parameters, the pair execution record) share one
    // budget, encoded and decoded: past it the read fails. A workflow has its own
    // budget; past it only the workflow is "limited". Duplicate chunks draw from
    // separate budgets per class, so repeats cost bounded reads, not file size.
    const ROLE_METADATA_BYTES = 2 * 1024 * 1024;
    const WORKFLOW_METADATA_BYTES = 4 * 1024 * 1024;
    const DUPLICATE_WORKFLOW_BYTES = 2 * WORKFLOW_METADATA_BYTES;
    const DUPLICATE_ROLE_BYTES = 2 * ROLE_METADATA_BYTES;
    const MAX_KEYWORD_BYTES = 80; // a 1-79 byte keyword plus its NUL separator
    const MAX_PNG_CHUNKS = 10000;
    const MAX_CHUNK_LENGTH = 0x7fffffff;
    const IHDR_LENGTH = 13;
    // Every byte the reader can slice from a PNG of any size: the signature, each
    // chunk's header, CRC and keyword prefix, the retained-data budgets and IHDR.
    const PNG_READ_CEILING =
        PNG_SIGNATURE.length +
        MAX_PNG_CHUNKS * (CHUNK_HEADER + CHUNK_CRC + MAX_KEYWORD_BYTES) +
        ROLE_METADATA_BYTES +
        WORKFLOW_METADATA_BYTES +
        DUPLICATE_WORKFLOW_BYTES +
        DUPLICATE_ROLE_BYTES +
        IHDR_LENGTH; // 19,794,389
    const HEADER_READ_BYTES = 64 * 1024;
    const INFLATE_WRITE_BYTES = 256;
    const YIELD_INTERVAL_MS = 8;
    const LATIN1_SLICE = 8192;
    const ROLE_KEYS = new Set(["prompt", "parameters", "comfyassets_prompt_pairs"]);
    const METADATA_KEYS = new Set([...ROLE_KEYS, "workflow"]);
    const TEXT_CHUNKS = new Set(["tEXt", "zTXt", "iTXt"]);
    // The server's _FOOTER (with its MULTILINE anchors spelled out: a line start is
    // the text start or after "\n"; a line end is before "\n" or the text end) and
    // _BATCH_INDEX, with Python's Unicode \d.
    const FOOTER = /(?:^|(?<=\n))Steps:\s*\p{Nd}+\s*,[^\n]*(?=\n|$)/gu;
    const BATCH_INDEX = /(?:^|(?<=, ))Batch index: \p{Nd}+(?=, |$)/gu;
    const ASF_GUID = [0x30, 0x26, 0xb2, 0x75, 0x8e, 0x66, 0xcf, 0x11, 0xa6, 0xd9, 0x00, 0xaa, 0x00, 0x62, 0xce, 0x6c];
    const JPEG_SOF = new Set([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf]);
    const JPEG_SOS = 0xda;

    const CRC_TABLE = (() => {
        const table = new Uint32Array(256);
        for (let n = 0; n < 256; n++) {
            let c = n;
            for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
            table[n] = c;
        }
        return table;
    })();

    function crc32(...parts) {
        let c = 0xffffffff;
        for (const bytes of parts) for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
        return (c ^ 0xffffffff) >>> 0;
    }

    function readUint32(bytes, offset) {
        return bytes[offset] * 0x1000000 + ((bytes[offset + 1] << 16) | (bytes[offset + 2] << 8) | bytes[offset + 3]);
    }

    function startsWith(bytes, prefix, at = 0) {
        if (bytes.length < at + prefix.length) return false;
        for (let i = 0; i < prefix.length; i++) if (bytes[at + i] !== prefix[i]) return false;
        return true;
    }

    function sameBytes(a, b) {
        if (a.length !== b.length) return false;
        for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
        return true;
    }

    function concatBytes(parts, total) {
        const out = new Uint8Array(total);
        let offset = 0;
        for (const part of parts) {
            out.set(part, offset);
            offset += part.length;
        }
        return out;
    }

    /** ISO-8859-1 in slices: TextDecoder's "latin1" label is windows-1252, not this. */
    function latin1Text(bytes) {
        let out = "";
        for (let i = 0; i < bytes.length; i += LATIN1_SLICE) out += String.fromCharCode.apply(null, bytes.subarray(i, i + LATIN1_SLICE));
        return out;
    }

    /** UTF-8 length of the Latin-1 decoding: each byte >= 0x80 becomes two bytes. */
    function latin1Utf8Length(bytes) {
        let high = 0;
        for (let i = 0; i < bytes.length; i++) if (bytes[i] >= 0x80) high++;
        return bytes.length + high;
    }

    function isAscii(bytes) {
        for (let i = 0; i < bytes.length; i++) if (bytes[i] >= 0x80) return false;
        return true;
    }

    function utf8Length(text) {
        return new TextEncoder().encode(text).length;
    }

    class PngReadError extends Error {
        constructor(reason, message) {
            super(message);
            this.name = "PngReadError";
            this.reason = reason;
        }
    }

    const invalid = (message) => new PngReadError("invalid-png", message);
    const overLimit = () => new PngReadError("metadata-limit", "Metadata exceeds inspection limits");
    const readFailure = (message) => new PngReadError("read-failure", message);

    function abortError() {
        return new DOMException("The file read was aborted", "AbortError");
    }

    function throwIfAborted(signal) {
        if (signal && signal.aborted) throw abortError();
    }

    function quiet(promise) {
        Promise.resolve(promise).catch(() => {});
    }

    /** `promise`, or an AbortError as soon as `signal` aborts (the promise is left handled). */
    function untilAborted(promise, signal) {
        if (!signal) return promise;
        if (signal.aborted) {
            quiet(promise);
            return Promise.reject(abortError());
        }
        return new Promise((resolve, reject) => {
            const onAbort = () => {
                quiet(promise);
                reject(abortError());
            };
            signal.addEventListener("abort", onAbort, { once: true });
            promise.then(
                (value) => {
                    signal.removeEventListener("abort", onAbort);
                    resolve(value);
                },
                (error) => {
                    signal.removeEventListener("abort", onAbort);
                    reject(error);
                },
            );
        });
    }

    /** Strict UTF-8 (invalid sequences and encoded surrogates fail), BOM kept, as Python's decode. */
    function utf8Strict(bytes) {
        try {
            return new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes);
        } catch (error) {
            throw invalid("Media metadata could not be read");
        }
    }

    function defaultYield() {
        return new Promise((resolve) => setTimeout(resolve, 0));
    }

    /** Hands the event loop a turn about every YIELD_INTERVAL_MS of work. */
    function createPacer(yieldControl) {
        let last = performance.now();
        return {
            async maybeYield() {
                if (performance.now() - last < YIELD_INTERVAL_MS) return;
                await yieldControl();
                last = performance.now();
            },
        };
    }

    /**
     * Logical byte ranges of `file`, served from the retained `prefix` where it
     * covers them. Every sliced byte is charged against PNG_READ_CEILING, and every
     * range is checked against `file.size` before it is sliced.
     */
    function createSliceReader(file, prefix, signal) {
        const retained = prefix || new Uint8Array(0);
        let charged = 0;
        return {
            async read(start, end) {
                if (!Number.isSafeInteger(end) || end > file.size) throw invalid("Incomplete PNG metadata");
                if (end <= retained.length) return retained.subarray(start, end);
                const head = start < retained.length ? retained.subarray(start) : null;
                const sliceStart = head ? retained.length : start;
                charged += end - sliceStart;
                if (charged > PNG_READ_CEILING) throw readFailure("PNG read exceeds the inspection ceiling");
                const buffer = await file.slice(sliceStart, end).arrayBuffer();
                throwIfAborted(signal);
                const bytes = new Uint8Array(buffer);
                if (bytes.length !== end - sliceStart) throw readFailure("The file changed while it was being read");
                return head ? concatBytes([head, bytes], end - start) : bytes;
            },
        };
    }

    /**
     * Inflates zlib data through `decompress` (a DecompressionStream constructor)
     * with one <=256-byte write in flight, keeping at most `limit` output bytes.
     * Returns null when the output is over `limit`, as the server's _inflate does.
     */
    function createInflater(decompress, signal, pacer) {
        return async function inflate(compressed, limit) {
            if (typeof decompress !== "function") {
                throw new PngReadError("decompression-unavailable", "This browser cannot inflate compressed PNG text");
            }
            const stream = new decompress("deflate");
            const writer = stream.writable.getWriter();
            const reader = stream.readable.getReader();
            const parts = [];
            let total = 0;
            let over = false;
            const stop = () => {
                quiet(writer.abort());
                quiet(reader.cancel());
            };
            if (signal) signal.addEventListener("abort", stop, { once: true });
            try {
                const drain = (async () => {
                    for (;;) {
                        const { value, done } = await untilAborted(reader.read(), signal);
                        if (done) return;
                        total += value.byteLength;
                        if (total > limit) {
                            over = true;
                            stop();
                            return;
                        }
                        parts.push(value);
                    }
                })();
                const feed = (async () => {
                    for (let offset = 0; offset < compressed.length; offset += INFLATE_WRITE_BYTES) {
                        await pacer.maybeYield();
                        await untilAborted(writer.ready, signal);
                        await untilAborted(writer.write(compressed.subarray(offset, offset + INFLATE_WRITE_BYTES)), signal);
                    }
                    await untilAborted(writer.close(), signal);
                })();
                const outcomes = await Promise.allSettled([drain, feed]);
                throwIfAborted(signal);
                if (over) return null;
                if (outcomes.some((outcome) => outcome.status === "rejected")) {
                    throw invalid("Compressed metadata is invalid or exceeds limits");
                }
                return concatBytes(parts, total);
            } finally {
                if (signal) signal.removeEventListener("abort", stop);
            }
        };
    }

    /** The keyword before the first NUL of a <=80-byte prefix: 1-79 Latin-1 bytes. */
    function keywordOf(prefix) {
        const nul = indexOfNul(prefix, 0, prefix.length);
        if (nul < 1 || nul > MAX_KEYWORD_BYTES - 1) throw invalid("Malformed PNG text metadata");
        return latin1(prefix, 0, nul);
    }

    /** Splits on NUL at most `limit` times (Python's bytes.split(b"\\0", limit)). */
    function splitNul(bytes, limit) {
        const fields = [];
        let start = 0;
        while (fields.length < limit) {
            const nul = indexOfNul(bytes, start, bytes.length);
            if (nul === -1) break;
            fields.push(bytes.subarray(start, nul));
            start = nul + 1;
        }
        fields.push(bytes.subarray(start));
        return fields;
    }

    /**
     * Decodes one retained text chunk (the server's _png_text): {text, size} with
     * size the UTF-8 length of text, or null when it inflates past `limit`.
     */
    async function pngText(kind, data, limit, inflate) {
        let body = data.subarray(indexOfNul(data, 0, data.length) + 1);
        let unicode = false;
        if (kind === "zTXt") {
            if (body.length === 0 || body[0] !== 0) throw invalid("Unsupported PNG text compression");
            body = await inflate(body.subarray(1), limit);
        } else if (kind === "iTXt") {
            if (body.length < 2 || body[0] > 1) throw invalid("Malformed international PNG metadata");
            const flag = body[0];
            const method = body[1];
            const fields = splitNul(body.subarray(2), 2);
            if (fields.length !== 3 || (flag && method !== 0)) throw invalid("Malformed international PNG metadata");
            if (!isAscii(fields[0])) throw invalid("Media metadata could not be read");
            utf8Strict(fields[1]);
            body = flag ? await inflate(fields[2], limit) : fields[2];
            unicode = true;
        }
        if (body === null) return null;
        if (indexOfNul(body, 0, body.length) !== -1) throw invalid("Malformed PNG text metadata");
        if (unicode) return { text: utf8Strict(body), size: body.length };
        return { text: latin1Text(body), size: latin1Utf8Length(body) };
    }

    /** The server's _batch_normalized: the footer's single batch index blanked, else null. */
    function batchNormalized(text) {
        const footers = [...text.matchAll(FOOTER)];
        if (footers.length !== 1) return null;
        const [footer] = footers;
        let count = 0;
        const line = footer[0].replace(BATCH_INDEX, () => {
            count++;
            return "Batch index: #";
        });
        if (count !== 1) return null;
        return text.slice(0, footer.index) + line + text.slice(footer.index + footer[0].length);
    }

    /** Retained records with per-class budgets and byte-identical duplicates (the server's _PngRecords). */
    class PngRecords {
        constructor() {
            this.found = {};
            this.limited = new Set();
            this.retained = new Map(); // key -> {kind, data} of the kept chunk, compared byte for byte
            this.roleEncoded = 0;
            this.roleDecoded = 0;
            this.duplicateWorkflow = 0;
            this.duplicateRole = 0;
            this.duplicateDecoded = 0;
        }

        /** Decide from the keyword and length alone whether to seek past a chunk. */
        skip(key, length) {
            if (!METADATA_KEYS.has(key) || this.limited.has(key)) return true; // never retained, or a duplicate of a limited workflow
            const kept = this.retained.get(key);
            if (kept) {
                if (kept.data.length !== length && key !== "parameters") throw invalid("Duplicate prompt metadata");
                if (key === "workflow") {
                    if (this.duplicateWorkflow + length > DUPLICATE_WORKFLOW_BYTES) {
                        this.limited.add(key);
                        delete this.found[key];
                        return true;
                    }
                    this.duplicateWorkflow += length;
                } else {
                    if (this.duplicateRole + length > DUPLICATE_ROLE_BYTES) throw overLimit();
                    this.duplicateRole += length;
                }
                return false; // same length: read and compare bytes
            }
            if (key === "workflow") {
                if (length > WORKFLOW_METADATA_BYTES) {
                    this.limited.add(key);
                    return true;
                }
                return false;
            }
            this.roleEncoded += length;
            if (this.roleEncoded > ROLE_METADATA_BYTES) throw overLimit();
            return false;
        }

        async add(kind, data, inflate) {
            const key = keywordOf(data.subarray(0, MAX_KEYWORD_BYTES));
            const kept = this.retained.get(key);
            if (kept) {
                if (kept.kind !== kind || !sameBytes(kept.data, data)) {
                    if (key !== "parameters") throw invalid("Duplicate prompt metadata");
                    await this.batchCopy(kind, data, inflate);
                    // The kept copy is the last one; later repeats compare with it.
                    this.retained.set(key, { kind, data });
                }
                return;
            }
            this.retained.set(key, { kind, data });
            const workflow = key === "workflow";
            const limit = workflow ? WORKFLOW_METADATA_BYTES : ROLE_METADATA_BYTES - this.roleDecoded;
            const decoded = await pngText(kind, data, limit, inflate);
            if (workflow) {
                if (decoded === null || decoded.size > WORKFLOW_METADATA_BYTES) {
                    this.limited.add(key);
                    return;
                }
            } else {
                this.roleDecoded += decoded === null ? limit + 1 : decoded.size;
                if (this.roleDecoded > ROLE_METADATA_BYTES) throw overLimit();
            }
            this.found[key] = decoded.text;
        }

        /**
         * Keep a later `parameters` copy that differs only in its batch index. Each
         * copy must carry exactly one `Batch index: N` settings field; any other
         * difference is a conflict. Decoding every copy draws from the duplicate
         * role budget, so small compressed copies stay bounded.
         */
        async batchCopy(kind, data, inflate) {
            const decoded = await pngText(kind, data, ROLE_METADATA_BYTES, inflate);
            const size = decoded === null ? ROLE_METADATA_BYTES + 1 : decoded.size;
            this.duplicateDecoded += size;
            if (decoded === null || this.duplicateDecoded > DUPLICATE_ROLE_BYTES) throw overLimit();
            const kept = this.found.parameters;
            const normalized = batchNormalized(kept);
            if (normalized === null || normalized !== batchNormalized(decoded.text)) throw invalid("Duplicate prompt metadata");
            this.roleDecoded += Math.max(0, size - utf8Length(kept));
            if (this.roleDecoded > ROLE_METADATA_BYTES) throw overLimit();
            this.found.parameters = decoded.text;
        }
    }

    /**
     * Walks the chunk layout (the server's _png_metadata): CRC-checks IHDR, IEND
     * and retained text chunks, reads keyword prefixes, and seeks past everything
     * else by length, so pixel data and unknown text are never read.
     */
    async function readPngChunks(reader, fileSize, { signal, inflate, pacer }) {
        const records = new PngRecords();
        let sawHeader = false;
        let sawPixels = false;
        let width = 0;
        let height = 0;
        let offset = PNG_SIGNATURE.length;
        for (let index = 0; index < MAX_PNG_CHUNKS; index++) {
            await pacer.maybeYield();
            throwIfAborted(signal);
            const header = await reader.read(offset, offset + CHUNK_HEADER);
            const length = readUint32(header, 0);
            const kindBytes = header.subarray(4, 8);
            const kind = latin1(header, 4, 8);
            const dataStart = offset + CHUNK_HEADER;
            const next = dataStart + length + CHUNK_CRC;
            if (length > MAX_CHUNK_LENGTH || next > fileSize) throw invalid("Incomplete PNG chunk");
            if (index === 0 && (kind !== "IHDR" || length !== IHDR_LENGTH)) throw invalid("Missing PNG header");
            let data;
            let checksum;
            if (TEXT_CHUNKS.has(kind)) {
                const prefix = await reader.read(dataStart, dataStart + Math.min(length, MAX_KEYWORD_BYTES));
                if (records.skip(keywordOf(prefix), length)) {
                    offset = next;
                    continue;
                }
                const rest = await reader.read(dataStart + prefix.length, next);
                data = concatBytes([prefix, rest.subarray(0, rest.length - CHUNK_CRC)], length);
                checksum = rest.subarray(rest.length - CHUNK_CRC);
            } else if (kind === "IHDR" || kind === "IEND") {
                const bytes = await reader.read(dataStart, next);
                data = bytes.subarray(0, length);
                checksum = bytes.subarray(length);
            } else {
                sawPixels = sawPixels || kind === "IDAT";
                offset = next;
                continue;
            }
            if (crc32(kindBytes, data) !== readUint32(checksum, 0)) throw invalid("PNG metadata checksum mismatch");
            if (kind === "IHDR") {
                const w = readUint32(data, 0);
                const h = readUint32(data, 4);
                if (sawHeader || !w || !h) throw invalid("Malformed PNG header");
                sawHeader = true;
                width = w;
                height = h;
            } else if (kind === "IEND") {
                if (length || !sawPixels) throw invalid("Incomplete PNG image");
                return { records: { ...records.found }, limited: [...records.limited].sort(), width, height };
            } else {
                await records.add(kind, data, inflate);
            }
            offset = next;
        }
        throw new PngReadError("metadata-limit", "PNG chunk count exceeds inspection limits");
    }

    /**
     * Reads the retained text records and the IHDR size of a PNG File without
     * reading its pixels or sending it anywhere.
     *
     * @param {Blob} file - the chosen File (only `size` and `slice` are used)
     * @param {Object} [options]
     * @param {AbortSignal} [options.signal] - rejects the read with an AbortError
     * @param {Uint8Array} [options.prefix] - the file's first bytes, already read for classification
     * @param {Function} [options.yieldControl] - awaited about every 8 ms of work
     * @param {Function} [options.decompress] - DecompressionStream constructor; pass undefined where none exists
     * @returns {Promise<{status:'complete', records:Object<string,string>, limited:string[], width:number, height:number}
     *   | {status:'unreadable', reason:'invalid-png'|'read-failure'|'metadata-limit'|'decompression-unavailable'}>}
     */
    async function readPngMetadataFile(file, options = {}) {
        const { signal, prefix, yieldControl = defaultYield } = options;
        const decompress = Object.hasOwn(options, "decompress") ? options.decompress : globalThis.DecompressionStream;
        try {
            throwIfAborted(signal);
            if (!file || !Number.isSafeInteger(file.size) || typeof file.slice !== "function") throw readFailure("Not a readable file");
            const reader = createSliceReader(file, toBytes(prefix), signal);
            const pacer = createPacer(yieldControl);
            const signature = await reader.read(0, PNG_SIGNATURE.length);
            if (!hasSignature(signature)) throw invalid("Not a PNG file");
            const inflate = createInflater(decompress, signal, pacer);
            const result = await readPngChunks(reader, file.size, { signal, inflate, pacer });
            return { status: "complete", ...result };
        } catch (error) {
            if (error && error.name === "AbortError") throw error;
            if (error instanceof PngReadError) return { status: "unreadable", reason: error.reason };
            return { status: "unreadable", reason: "read-failure" };
        }
    }

    /**
     * Media kind from a file's first bytes (<= 32), or null for anything else.
     * @returns {{media:'image'|'video', kind:'png'|'jpeg'|'webp'|'iso-bmff'|'matroska'|'avi'|'asf'}|null}
     */
    function classifyPrefix(input) {
        const bytes = toBytes(input);
        if (!bytes) return null;
        if (hasSignature(bytes)) return { media: "image", kind: "png" };
        if (startsWith(bytes, [0xff, 0xd8, 0xff])) return { media: "image", kind: "jpeg" };
        if (bytes.length >= 12 && latin1(bytes, 0, 4) === "RIFF") {
            const form = latin1(bytes, 8, 12);
            if (form === "WEBP") return { media: "image", kind: "webp" };
            if (form === "AVI ") return { media: "video", kind: "avi" };
            return null;
        }
        if (bytes.length >= 8 && latin1(bytes, 4, 8) === "ftyp") return { media: "video", kind: "iso-bmff" };
        if (startsWith(bytes, [0x1a, 0x45, 0xdf, 0xa3])) return { media: "video", kind: "matroska" };
        if (startsWith(bytes, ASF_GUID)) return { media: "video", kind: "asf" };
        return null;
    }

    /** [width, height] from the first SOFn segment, walking markers as Pillow does. */
    function jpegSize(bytes) {
        if (!startsWith(bytes, [0xff, 0xd8])) return null;
        let offset = 2;
        while (offset + 1 < bytes.length) {
            if (bytes[offset] !== 0xff) {
                offset++; // junk between segments
                continue;
            }
            const marker = bytes[offset + 1];
            if (marker === 0xff) {
                offset++; // padding
                continue;
            }
            if (marker === 0x00 || marker === 0x01 || (marker >= 0xd0 && marker <= 0xd9)) {
                offset += 2; // escaped 0xFF, TEM, RSTn, SOI, EOI: no length
                continue;
            }
            if (marker === JPEG_SOS || offset + 4 > bytes.length) return null;
            if (JPEG_SOF.has(marker)) {
                if (offset + 9 > bytes.length) return null;
                return [(bytes[offset + 7] << 8) | bytes[offset + 8], (bytes[offset + 5] << 8) | bytes[offset + 6]];
            }
            offset += 2 + ((bytes[offset + 2] << 8) | bytes[offset + 3]);
        }
        return null;
    }

    /** [width, height] from the first WebP chunk: VP8 frame, VP8L header or VP8X canvas. */
    function webpSize(bytes) {
        if (bytes.length < 30 || latin1(bytes, 0, 4) !== "RIFF" || latin1(bytes, 8, 12) !== "WEBP") return null;
        const kind = latin1(bytes, 12, 16);
        if (kind === "VP8 ") {
            if (!startsWith(bytes, [0x9d, 0x01, 0x2a], 23)) return null;
            return [(bytes[26] | (bytes[27] << 8)) & 0x3fff, (bytes[28] | (bytes[29] << 8)) & 0x3fff];
        }
        if (kind === "VP8L") {
            if (bytes[20] !== 0x2f) return null;
            const packed = bytes[21] | (bytes[22] << 8) | (bytes[23] << 16) | (bytes[24] << 24);
            return [(packed & 0x3fff) + 1, ((packed >>> 14) & 0x3fff) + 1];
        }
        if (kind === "VP8X") {
            return [1 + (bytes[24] | (bytes[25] << 8) | (bytes[26] << 16)), 1 + (bytes[27] | (bytes[28] << 8) | (bytes[29] << 16))];
        }
        return null;
    }

    /**
     * Format and dimensions of a JPEG or WebP File from at most 64 KiB of its head.
     * @returns {Promise<{format:'JPEG'|'WEBP'|null, width:number|null, height:number|null}>}
     */
    async function readImageHeader(file, kind) {
        const format = kind === "jpeg" ? "JPEG" : kind === "webp" ? "WEBP" : null;
        if (!format) return { format: null, width: null, height: null };
        const bytes = new Uint8Array(await file.slice(0, Math.min(file.size, HEADER_READ_BYTES)).arrayBuffer());
        const size = (format === "JPEG" ? jpegSize : webpSize)(bytes);
        return { format, width: size ? size[0] : null, height: size ? size[1] : null };
    }

    const api = { parsePngTextChunks, PNG_SIGNATURE, classifyPrefix, readPngMetadataFile, readImageHeader };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    else root.PngMetadata = api;
})(typeof window !== "undefined" ? window : globalThis);
