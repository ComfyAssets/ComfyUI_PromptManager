// Local video tag readers for the Inspect page (#249): bounded ISO-BMFF (MP4/MOV/M4V)
// and Matroska (MKV/WebM) walkers that read only the container tags the server asks
// ffprobe for (utils/prompt_inspector.py _video_metadata), and a port of the carrier
// rule utils/video_metadata.py select_video_carrier plus the record budgets of
// read_inspection_file. Nothing here uploads, stores or logs the file. The walkers
// slice box and element headers, the tag names and the retained tag values only, so
// a multi-gigabyte video costs at most VIDEO_READ_CEILING bytes of reads and never a
// byte inside mdat or a Cluster. tests/test_local_inspect_parity.py proves the
// answers equal the server's on the committed fixtures and recorded tag maps.
//
// What ffprobe reports is the oracle: ISO-BMFF mdta keys are reported verbatim and
// the legacy ilst atoms ©cmt/desc as comment/description; Matroska TagNames are
// reported as written (ffmpeg upper-cases them at mux time). Envelope names are
// exact-case (comment, description) and the per-key names fold ASCII case in the
// selector, exactly as the server folds them.

import { INT_MAX_STR_DIGITS } from "./local-projection.mjs";

// Role records (prompt, parameters, the pair execution record) share one budget:
// past it the read fails. A workflow has its own budget; past it only the workflow
// is "limited". Duplicate tags draw from separate budgets per class. The total of
// tag bytes read is the server's ffprobe stdout bound.
export const ROLE_METADATA_BYTES = 2 * 1024 * 1024;
export const WORKFLOW_METADATA_BYTES = 4 * 1024 * 1024;
export const DUPLICATE_WORKFLOW_BYTES = 2 * WORKFLOW_METADATA_BYTES;
export const DUPLICATE_ROLE_BYTES = 2 * ROLE_METADATA_BYTES;
export const VIDEO_TAG_READ_BYTES = 2 * (ROLE_METADATA_BYTES + WORKFLOW_METADATA_BYTES) + 64 * 1024;
// The server's role-only re-probe bound, taken when the first probe overflows.
export const VIDEO_REPROBE_BYTES = 2 * ROLE_METADATA_BYTES + 64 * 1024;
// A first per-key workflow over its budget is never sliced; this marker holds its
// place in the tag map, as the server's first probe still reports the tag.
export const OVERSIZED_WORKFLOW = Object.freeze({ oversized: "workflow" });
export const MAX_VIDEO_ELEMENTS = 10000;
const MAX_HEADER_BYTES = 16; // a 64-bit ISO-BMFF box header; an EBML header is at most 12
const MAX_NAME_BYTES = 64; // the longest carrier name is 24 bytes; longer names are never ours
// Every byte the readers can slice from a video of any size: each element's header
// and name plus the retained tag values.
export const VIDEO_READ_CEILING = MAX_VIDEO_ELEMENTS * (MAX_HEADER_BYTES + MAX_NAME_BYTES) + VIDEO_TAG_READ_BYTES;
const PREFIX_BYTES = 32;
const YIELD_INTERVAL_MS = 8;
const JSON_MAX_DEPTH = 1000; // Python's default recursion limit

const ROLE_KEYS = ["prompt", "parameters", "comfyassets_prompt_pairs"];
const METADATA_KEYS = ["prompt", "workflow", "parameters", "comfyassets_prompt_pairs"];
const ENVELOPE_KEYS = ["comment", "description"];
const PER_KEY_NAMES = new Set(METADATA_KEYS);
const ENVELOPE_NAMES = new Set(ENVELOPE_KEYS);
// ffprobe's names for the legacy ilst atoms that can carry an envelope.
const LEGACY_ATOMS = new Map([["©cmt", "comment"], ["desc", "description"], ["©des", "description"]]);
const ASF_GUID = [0x30, 0x26, 0xb2, 0x75, 0x8e, 0x66, 0xcf, 0x11, 0xa6, 0xd9, 0x00, 0xaa, 0x00, 0x62, 0xce, 0x6c];

const EBML = {
    header: 0x1a45dfa3,
    docType: 0x4282,
    segment: 0x18538067,
    seekHead: 0x114d9b74,
    seek: 0x4dbb,
    seekId: 0x53ab,
    seekPosition: 0x53ac,
    tags: 0x1254c367,
    tag: 0x7373,
    targets: 0x63c0,
    trackUid: 0x63c5,
    chapterUid: 0x63c4,
    attachmentUid: 0x63c6,
    simpleTag: 0x67c8,
    tagName: 0x45a3,
    tagString: 0x4487,
    tagLanguage: 0x447a,
    tagDefault: 0x4484,
    cluster: 0x1f43b675,
};
const TAGS_ID_BYTES = [0x12, 0x54, 0xc3, 0x67];

class VideoReadError extends Error {
    constructor(status, reason, message) {
        super(message);
        this.name = "VideoReadError";
        this.status = status;
        this.reason = reason;
    }
}

const invalid = (message) => new VideoReadError("unreadable", "invalid-container", message);
const limited = (reason, message) => new VideoReadError("limited", reason, message);
const readFailure = (message) => new VideoReadError("unreadable", "read-failure", message);

function abortError() {
    return new DOMException("The file read was aborted", "AbortError");
}

function throwIfAborted(signal) {
    if (signal && signal.aborted) throw abortError();
}

function toBytes(input) {
    if (input instanceof Uint8Array) return input;
    if (input instanceof ArrayBuffer) return new Uint8Array(input);
    if (Array.isArray(input)) return Uint8Array.from(input);
    return null;
}

function latin1(bytes, start, end) {
    let out = "";
    for (let index = start; index < end; index += 1) out += String.fromCharCode(bytes[index]);
    return out;
}

function startsWith(bytes, prefix, at = 0) {
    if (bytes.length < at + prefix.length) return false;
    for (let index = 0; index < prefix.length; index += 1) if (bytes[at + index] !== prefix[index]) return false;
    return true;
}

function readUint32(bytes, offset) {
    return ((bytes[offset] << 24) >>> 0) + (bytes[offset + 1] << 16) + (bytes[offset + 2] << 8) + bytes[offset + 3];
}

/** A big-endian unsigned integer of up to 8 bytes as a Number, or null past MAX_SAFE_INTEGER. */
function readUintBytes(bytes, start, end) {
    let value = 0n;
    for (let index = start; index < end; index += 1) value = (value << 8n) | BigInt(bytes[index]);
    return value <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(value) : null;
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

/** ffmpeg stores tag text as a C string: the first NUL ends it. */
function untilNul(bytes) {
    const nul = bytes.indexOf(0);
    return nul < 0 ? bytes : bytes.subarray(0, nul);
}

const STRICT_UTF8 = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });
const REPLACEMENT = "\ufffd";
// av_utf8_decode's smallest code point per continuation count (overlong below it).
const OVERLONG_MINIMUM = [0, 0x80, 0x800, 0x10000];

/**
 * One av_utf8_decode step (flags 0, as ffprobe calls it) from `start`: the next
 * offset and whether the sequence is valid. A lead byte that is a continuation or
 * 0xFE/0xFF, an overlong form, a surrogate, a code above U+10FFFF, U+FFFE/U+FFFF
 * and every 5/6-byte form fail as the whole sequence; a missing or bad
 * continuation byte fails the lead byte alone.
 */
function ffprobeStep(bytes, start) {
    const lead = bytes[start];
    if ((lead & 0xc0) === 0x80 || lead >= 0xfe) return [start + 1, false];
    let tail = 0;
    if (lead & 0x80) while (lead & (0x40 >> tail)) tail += 1;
    let code = tail ? lead & (0x3f >> tail) : lead;
    let next = start + 1;
    for (let index = 0; index < tail; index += 1, next += 1) {
        if (next >= bytes.length || (bytes[next] & 0xc0) !== 0x80) return [start + 1, false];
        code = code * 64 + (bytes[next] & 0x3f);
    }
    const valid = tail <= 3 && code >= OVERLONG_MINIMUM[tail] && code <= 0x10ffff && !(code >= 0xd800 && code <= 0xdfff) && code !== 0xfffe && code !== 0xffff;
    return [next, valid];
}

/**
 * Tag text as ffprobe prints it with its default string_validation=replace
 * (fftools validate_string): each failing av_utf8_decode step becomes one U+FFFD,
 * so Python's json.loads of ffprobe's output never meets invalid UTF-8. This
 * differs from WHATWG decoding in how many replacements a bad sequence yields.
 */
function ffprobeText(bytes) {
    let text = null;
    try {
        text = STRICT_UTF8.decode(bytes);
    } catch (error) {
        text = null;
    }
    if (text !== null && !/[\ufffe\uffff]/.test(text)) return text;
    const parts = [];
    let run = 0;
    for (let offset = 0; offset < bytes.length; ) {
        const [next, valid] = ffprobeStep(bytes, offset);
        if (!valid) {
            if (offset > run) parts.push(STRICT_UTF8.decode(bytes.subarray(run, offset)));
            parts.push(REPLACEMENT);
            run = next;
        }
        offset = next;
    }
    if (bytes.length > run) parts.push(STRICT_UTF8.decode(bytes.subarray(run)));
    return parts.join("");
}

/** UTF-8 length as Python's ``encode("utf-8", "surrogatepass")`` counts it. */
function utf8Length(text) {
    let total = 0;
    for (let index = 0; index < text.length; index += 1) {
        const code = text.charCodeAt(index);
        if (code < 0x80) total += 1;
        else if (code < 0x800) total += 2;
        else if (code >= 0xd800 && code <= 0xdbff && index + 1 < text.length) {
            const next = text.charCodeAt(index + 1);
            if (next >= 0xdc00 && next <= 0xdfff) {
                total += 4;
                index += 1;
            } else total += 3;
        } else total += 3;
    }
    return total;
}

function defaultYield() {
    return new Promise((resolve) => setTimeout(resolve, 0));
}

/** Hands the event loop a turn about every YIELD_INTERVAL_MS of work. */
function createPacer(yieldControl, signal) {
    let last = performance.now();
    return {
        async maybeYield() {
            if (performance.now() - last < YIELD_INTERVAL_MS) return;
            await yieldControl();
            throwIfAborted(signal);
            last = performance.now();
        },
    };
}

/**
 * Logical byte ranges of `file`, served from the retained `prefix` where it covers
 * them (the twin of png-metadata.js's reader). Every sliced byte is charged against
 * VIDEO_READ_CEILING, and every range is checked against `file.size` before slicing.
 */
function createSliceReader(file, prefix, signal) {
    const retained = prefix || new Uint8Array(0);
    let charged = 0;
    return {
        async read(start, end) {
            throwIfAborted(signal);
            if (!Number.isSafeInteger(end) || end > file.size || start > end) throw invalid("Incomplete video container");
            if (end <= retained.length) return retained.subarray(start, end);
            const head = start < retained.length ? retained.subarray(start) : null;
            const sliceStart = head ? retained.length : start;
            charged += end - sliceStart;
            if (charged > VIDEO_READ_CEILING) throw readFailure("Video read exceeds the inspection ceiling");
            const buffer = await file.slice(sliceStart, end).arrayBuffer();
            throwIfAborted(signal);
            const bytes = new Uint8Array(buffer);
            if (bytes.length !== end - sliceStart) throw readFailure("The file changed while it was being read");
            return head ? concatBytes([head, bytes], end - start) : bytes;
        },
    };
}

/** Container kind from the first bytes, mirroring png-metadata.js classifyPrefix. */
function detectKind(bytes) {
    if (!bytes) return null;
    if (bytes.length >= 12 && latin1(bytes, 0, 4) === "RIFF") return latin1(bytes, 8, 12) === "AVI " ? "avi" : null;
    if (bytes.length >= 8 && latin1(bytes, 4, 8) === "ftyp") return "iso-bmff";
    if (startsWith(bytes, [0x1a, 0x45, 0xdf, 0xa3])) return "matroska";
    if (startsWith(bytes, ASF_GUID)) return "asf";
    return null;
}

/**
 * The shared walk state: the element count, the tag budgets and the collected tags.
 * `value` is the one gate every tag value passes before it is sliced.
 */
function createWalk(reader, signal, pacer) {
    const tags = {};
    const limitedRecords = [];
    const seen = new Set();
    let count = 0;
    let role = 0;
    let workflow = 0;
    let duplicateRole = 0;
    let duplicateWorkflow = 0;
    let total = 0;
    let workflowRead = 0; // bytes of per-key workflow tags read, which the re-probe leaves out
    let oversized = null; // {name, size}: the unread first per-key workflow
    const overLimit = () => limited("metadata-limit", "Video metadata exceeds inspection limits");
    const forgetOversized = () => {
        oversized = null;
        limitedRecords.splice(limitedRecords.indexOf("workflow"), 1);
    };
    return {
        tags,
        limited: limitedRecords,
        async header(offset, length) {
            count += 1;
            if (count > MAX_VIDEO_ELEMENTS) throw limited("chunk-limit", "Video container exceeds inspection limits");
            await pacer.maybeYield();
            return reader.read(offset, offset + length);
        },
        read: (start, end) => reader.read(start, end),
        /** Whether `name` is a carrier the server reads (per-key folded, envelope exact). */
        known(name) {
            return PER_KEY_NAMES.has(name.toLowerCase()) || ENVELOPE_NAMES.has(name);
        },
        remove(name) {
            if (oversized && oversized.name === name) forgetOversized();
            delete tags[name];
        },
        /**
         * Settles the oversized workflow as the server's probes would: when the
         * first probe fits, the tag keeps its marker; when it overflows, the
         * role-only re-probe reports no per-key workflow and has its own bound.
         */
        finish() {
            if (!oversized || total + oversized.size <= VIDEO_TAG_READ_BYTES) return;
            for (const name of Object.keys(tags)) if (name.toLowerCase() === "workflow") delete tags[name];
            if (total - workflowRead > VIDEO_REPROBE_BYTES) throw overLimit();
        },
        /**
         * Reads and keeps the tag `name` (a known carrier, see `known`) whose value
         * spans [start, end), within the budgets. `join` appends to an existing value
         * with that separator (ISO-BMFF repeats, as ffprobe reports them); `text`
         * stores an already decoded value.
         */
        async value(name, start, end, { join = null, text = null } = {}) {
            const size = end - start;
            const folded = name.toLowerCase();
            if (PER_KEY_NAMES.has(folded)) {
                const first = !seen.has(folded);
                seen.add(folded);
                if (folded === "workflow") {
                    if (first && size > WORKFLOW_METADATA_BYTES) {
                        oversized = { name, size };
                        tags[name] = OVERSIZED_WORKFLOW;
                        limitedRecords.push("workflow");
                        return;
                    }
                    if (tags[name] === OVERSIZED_WORKFLOW) {
                        // An ISO-BMFF repeat joins onto the unread value, still over budget.
                        if (join !== null) {
                            oversized.size += size;
                            return;
                        }
                        // Matroska keeps a name's last value: the oversized one is gone.
                        forgetOversized();
                    }
                    workflowRead += size;
                    if (first) workflow += size;
                    else duplicateWorkflow += size;
                    if (duplicateWorkflow > DUPLICATE_WORKFLOW_BYTES) throw overLimit();
                } else {
                    if (first) role += size;
                    else duplicateRole += size;
                    if (role > ROLE_METADATA_BYTES || duplicateRole > DUPLICATE_ROLE_BYTES) throw overLimit();
                }
            }
            total += size;
            if (total > VIDEO_TAG_READ_BYTES) throw overLimit();
            const value = text ?? ffprobeText(untilNul(await reader.read(start, end)));
            tags[name] = join !== null && name in tags ? `${tags[name]}${join}${value}` : value;
        },
    };
}

// ------------------------------------------------------------------ ISO-BMFF

/** One box header at `offset` inside a parent ending at `parentEnd`, or null when fewer than 8 bytes remain. */
async function readBox(walk, offset, parentEnd) {
    if (parentEnd - offset < 8) return null;
    const bytes = await walk.header(offset, Math.min(MAX_HEADER_BYTES, parentEnd - offset));
    let size = readUint32(bytes, 0);
    let bodyStart = offset + 8;
    if (size === 1) {
        if (bytes.length < 16) throw invalid("Truncated 64-bit box size");
        const large = readUintBytes(bytes, 8, 16);
        if (large === null || large < 16) throw invalid("Invalid 64-bit box size");
        size = large;
        bodyStart = offset + 16;
    } else if (size === 0) {
        size = parentEnd - offset;
    } else if (size < 8) {
        throw invalid("Box size below its header");
    }
    const end = offset + size;
    if (end > parentEnd) throw invalid("Box extends past its parent");
    return { type: latin1(bytes, 4, 8), raw: bytes.subarray(4, 8), start: offset, bodyStart, end };
}

async function readIsoBmff(walk, fileSize) {
    const state = { hdlrMdta: false, keys: null, format: null };
    let offset = 0;
    let box;
    while ((box = await readBox(walk, offset, fileSize))) {
        if (box.type === "ftyp" && box.end - box.bodyStart >= 4) {
            state.format = latin1(await walk.read(box.bodyStart, box.bodyStart + 4), 0, 4).replace(/[\s\0]+$/, "");
        } else if (box.type === "moov") {
            await readMoov(walk, box, state);
            return state.format;
        }
        offset = box.end;
    }
    throw invalid("No moov box");
}

async function readMoov(walk, moov, state) {
    let offset = moov.bodyStart;
    let box;
    while ((box = await readBox(walk, offset, moov.end))) {
        if (box.type === "meta") await readMeta(walk, box, state);
        else if (box.type === "udta") {
            let inner = box.bodyStart;
            let child;
            while ((child = await readBox(walk, inner, box.end))) {
                if (child.type === "meta") await readMeta(walk, child, state);
                inner = child.end;
            }
        }
        offset = box.end;
    }
}

/** `meta` as ffmpeg reads it: scan 4-byte words for the first child's `hdlr` type. */
async function readMeta(walk, meta, state) {
    const scan = await walk.read(meta.bodyStart, Math.min(meta.end, meta.bodyStart + MAX_NAME_BYTES));
    let childrenStart = null;
    for (let word = 4; word + 4 <= scan.length; word += 4) {
        if (latin1(scan, word, word + 4) === "hdlr") {
            childrenStart = meta.bodyStart + word - 4;
            break;
        }
    }
    if (childrenStart === null) return;
    let offset = childrenStart;
    let box;
    while ((box = await readBox(walk, offset, meta.end))) {
        if (box.type === "hdlr") {
            if (box.end - box.bodyStart >= 12) {
                const handler = await walk.read(box.bodyStart + 8, box.bodyStart + 12);
                if (latin1(handler, 0, 4) === "mdta") state.hdlrMdta = true;
            }
        } else if (box.type === "keys") await readKeys(walk, box, state);
        else if (box.type === "ilst") await readIlst(walk, box, state);
        offset = box.end;
    }
}

/**
 * The `keys` box: 1-based mdta names; other namespaces and overlong names address
 * nothing. As ffmpeg, only the first keys box with a count field is read.
 */
async function readKeys(walk, box, state) {
    if (state.keys || box.end - box.bodyStart < 8) return;
    const head = await walk.read(box.bodyStart, box.bodyStart + 8);
    const count = readUint32(head, 4);
    const keys = [null];
    let offset = box.bodyStart + 8;
    for (let index = 1; index <= count; index += 1) {
        if (box.end - offset < 8) throw invalid("Keys box count exceeds its entries");
        const entry = await walk.header(offset, 8);
        const keySize = readUint32(entry, 0);
        if (keySize < 8 || offset + keySize > box.end) throw invalid("Keys entry exceeds its box");
        const nameLength = keySize - 8;
        let name = null;
        if (latin1(entry, 4, 8) === "mdta" && nameLength <= MAX_NAME_BYTES) {
            name = ffprobeText(await walk.read(offset + 8, offset + keySize));
        }
        keys.push(name);
        offset += keySize;
    }
    state.keys = keys;
}

/** ffprobe's text for an integer `data` atom (types 21 and 22) of 1, 2, 4 or 8 bytes, else null. */
function integerText(bytes, signed) {
    if (![1, 2, 4, 8].includes(bytes.length)) return null;
    let value = 0n;
    for (const byte of bytes) value = (value << 8n) | BigInt(byte);
    if (signed) value = BigInt.asIntN(bytes.length * 8, value);
    return value.toString();
}

/**
 * ilst items: a legacy fourcc or a `keys` index, then the leading run of `data`
 * children. As ffprobe reports them, repeated values of one name join with ";",
 * types 0, 1 and 4 are text, 21 and 22 are integers, other types are not tags.
 */
async function readIlst(walk, ilst, state) {
    let offset = ilst.bodyStart;
    let item;
    while ((item = await readBox(walk, offset, ilst.end))) {
        offset = item.end;
        let name = LEGACY_ATOMS.get(item.type);
        if (name === undefined && state.hdlrMdta && state.keys) {
            const index = readUint32(item.raw, 0);
            // ffmpeg warns that the index is out of range and skips the item.
            if (index === 0 || index >= state.keys.length) continue;
            name = state.keys[index];
        }
        if (!name || !walk.known(name)) continue;
        let inner = item.bodyStart;
        while (item.end - inner >= 16) {
            const data = await walk.header(inner, 16);
            const dataSize = readUint32(data, 0);
            if (latin1(data, 4, 8) !== "data" || dataSize < 16 || dataSize > item.end - inner) break;
            const type = readUint32(data, 8);
            const start = inner + 16;
            const end = inner + dataSize;
            inner = end;
            if (type === 0 || type === 1 || type === 4) await walk.value(name, start, end, { join: ";" });
            else if ((type === 21 || type === 22) && end - start <= 8) {
                const text = integerText(await walk.read(start, end), type === 21);
                if (text !== null) await walk.value(name, start, end, { join: ";", text });
            }
        }
    }
}

// ------------------------------------------------------------------ Matroska

/** One EBML element header at `offset` inside a parent ending at `parentEnd`. */
async function readElement(walk, offset, parentEnd) {
    if (parentEnd - offset < 2) return null;
    const bytes = await walk.header(offset, Math.min(12, parentEnd - offset));
    const idLength = Math.clz32(bytes[0]) - 23;
    if (bytes[0] === 0 || idLength > 4 || bytes.length < idLength + 1) throw invalid("Invalid EBML element id");
    const id = readUintBytes(bytes, 0, idLength);
    const sizeByte = bytes[idLength];
    const sizeLength = Math.clz32(sizeByte) - 23;
    if (sizeByte === 0 || bytes.length < idLength + sizeLength) throw invalid("Invalid EBML element size");
    let size = BigInt(sizeByte & (0xff >> sizeLength));
    for (let index = idLength + 1; index < idLength + sizeLength; index += 1) size = (size << 8n) | BigInt(bytes[index]);
    const unknown = size === (1n << BigInt(7 * sizeLength)) - 1n;
    if (!unknown && size > BigInt(Number.MAX_SAFE_INTEGER)) throw invalid("EBML element size exceeds inspection limits");
    const dataStart = offset + idLength + sizeLength;
    const end = unknown ? null : dataStart + Number(size);
    if (end !== null && end > parentEnd) throw invalid("EBML element extends past its parent");
    return { id, dataStart, end, unknown };
}

/** Children of a sized master element, in order. */
async function* children(walk, element) {
    let offset = element.dataStart;
    let child;
    while ((child = await readElement(walk, offset, element.end))) {
        if (child.unknown) throw invalid("Unknown-size element inside a master element");
        yield child;
        offset = child.end;
    }
}

async function readMatroska(walk, fileSize) {
    const header = await readElement(walk, 0, fileSize);
    if (!header || header.id !== EBML.header || header.unknown) throw invalid("Not an EBML file");
    let docType = null;
    for await (const child of children(walk, header)) {
        if (child.id === EBML.docType && child.end - child.dataStart <= MAX_NAME_BYTES) {
            docType = latin1(untilNul(await walk.read(child.dataStart, child.end)), 0, Math.min(child.end - child.dataStart, MAX_NAME_BYTES));
        }
    }
    if (docType !== "matroska" && docType !== "webm") throw invalid("Unsupported EBML DocType");
    const segment = await readElement(walk, header.end, fileSize);
    if (!segment || segment.id !== EBML.segment) throw invalid("No Segment element");
    const segmentEnd = segment.unknown ? fileSize : segment.end;
    // As ffmpeg's demuxer: level-1 elements are read up to the first Cluster (of known
    // or unknown size), then SeekHead entries locate Tags stored behind the clusters.
    const seekTargets = [];
    let offset = segment.dataStart;
    let element;
    while ((element = await readElement(walk, offset, segmentEnd))) {
        if (element.id === EBML.cluster) break;
        if (element.unknown) throw invalid("Unknown-size element that is not a Segment or Cluster");
        if (element.id === EBML.seekHead) await readSeekHead(walk, element, segment.dataStart, seekTargets);
        else if (element.id === EBML.tags) await readTags(walk, element);
        offset = element.end;
    }
    for (const target of seekTargets) {
        if (target < offset || target >= segmentEnd) continue;
        const pointed = await readElement(walk, target, segmentEnd);
        if (!pointed || pointed.id !== EBML.tags || pointed.unknown) continue;
        await readTags(walk, pointed);
    }
    return docType;
}

async function readSeekHead(walk, seekHead, segmentStart, targets) {
    for await (const seek of children(walk, seekHead)) {
        if (seek.id !== EBML.seek) continue;
        let isTags = false;
        let position = null;
        for await (const field of children(walk, seek)) {
            const length = field.end - field.dataStart;
            if (field.id === EBML.seekId && length <= 4) {
                isTags = startsWith(await walk.read(field.dataStart, field.end), TAGS_ID_BYTES) && length === 4;
            } else if (field.id === EBML.seekPosition && length <= 8) {
                position = readUintBytes(await walk.read(field.dataStart, field.end), 0, length);
            }
        }
        if (isTags && position !== null) targets.push(segmentStart + position);
    }
}

async function readTags(walk, tags) {
    for await (const tag of children(walk, tags)) {
        if (tag.id !== EBML.tag) continue;
        let targeted = false;
        const simpleTags = [];
        for await (const child of children(walk, tag)) {
            if (child.id === EBML.simpleTag) simpleTags.push(child);
            else if (child.id === EBML.targets) targeted = await readTargets(walk, child);
        }
        if (targeted) continue;
        for (const simpleTag of simpleTags) await readSimpleTag(walk, simpleTag);
    }
}

/** Whether the Targets name a track, chapter or attachment (ffmpeg files those tags elsewhere). */
async function readTargets(walk, targets) {
    let targeted = false;
    for await (const field of children(walk, targets)) {
        const length = field.end - field.dataStart;
        if ((field.id === EBML.trackUid || field.id === EBML.chapterUid || field.id === EBML.attachmentUid) && length <= 8) {
            if ((await walk.read(field.dataStart, field.end)).some((byte) => byte !== 0)) targeted = true;
        }
    }
    return targeted;
}

/**
 * One SimpleTag as ffmpeg's matroska_convert_tag files it: under its plain name when
 * it has no language other than und or TagDefault is set (the "-lang" name is never
 * ours), a string-less tag deletes the name, nested SimpleTags become PARENT/CHILD
 * (never ours).
 */
async function readSimpleTag(walk, simpleTag) {
    let name = null;
    let language = null;
    let isDefault = 0;
    let string = null;
    for await (const field of children(walk, simpleTag)) {
        const length = field.end - field.dataStart;
        if (field.id === EBML.tagName) {
            name = length <= MAX_NAME_BYTES ? ffprobeText(untilNul(await walk.read(field.dataStart, field.end))) : "";
        } else if (field.id === EBML.tagLanguage && length <= MAX_NAME_BYTES) {
            language = latin1(untilNul(await walk.read(field.dataStart, field.end)), 0, Math.min(length, MAX_NAME_BYTES));
        } else if (field.id === EBML.tagDefault && length <= 8) {
            isDefault = readUintBytes(await walk.read(field.dataStart, field.end), 0, length) ?? 0;
        } else if (field.id === EBML.tagString) {
            string = field;
        }
    }
    if (name === null || !walk.known(name)) return;
    const lang = language && language !== "und" ? language : null;
    if (isDefault === 0 && lang) return;
    if (string === null) walk.remove(name);
    else await walk.value(name, string.dataStart, string.end);
}

/**
 * Reads the prompt carrier tags of a video File without touching its media data.
 *
 * @param {Blob} file - the chosen File (only `size` and `slice` are used)
 * @param {Object} [options]
 * @param {AbortSignal} [options.signal] - rejects the read with an AbortError
 * @param {Uint8Array} [options.prefix] - the file's first bytes, already read for classification
 * @param {Function} [options.yieldControl] - awaited about every 8 ms of work
 * @param {string} [options.kind] - the classifier's kind, when the prefix was not kept
 * @returns {Promise<{status:'complete', container:'iso-bmff'|'matroska', format:string|null, tags:Object<string,string>, limited:string[]}
 *   | {status:'unreadable', reason:'invalid-container'|'read-failure'}
 *   | {status:'limited', reason:'metadata-limit'|'chunk-limit'}
 *   | {status:'unsupported', reason:'video-container-unsupported'}>}
 */
export async function readVideoTags(file, options = {}) {
    const { signal, yieldControl = defaultYield } = options;
    try {
        throwIfAborted(signal);
        if (!file || !Number.isSafeInteger(file.size) || typeof file.slice !== "function") throw readFailure("Not a readable file");
        let prefix = toBytes(options.prefix);
        if (!prefix && options.kind === undefined) {
            prefix = await createSliceReader(file, null, signal).read(0, Math.min(PREFIX_BYTES, file.size));
        }
        const container = options.kind ?? detectKind(prefix);
        if (container === "avi" || container === "asf") return { status: "unsupported", reason: "video-container-unsupported" };
        if (container !== "iso-bmff" && container !== "matroska") throw invalid("Not a supported video container");
        const walk = createWalk(createSliceReader(file, prefix, signal), signal, createPacer(yieldControl, signal));
        const format = container === "iso-bmff" ? await readIsoBmff(walk, file.size) : await readMatroska(walk, file.size);
        walk.finish();
        return { status: "complete", container, format, tags: walk.tags, limited: walk.limited };
    } catch (error) {
        if (error && error.name === "AbortError") throw error;
        if (error instanceof VideoReadError) return { status: error.status, reason: error.reason };
        return { status: "unreadable", reason: "read-failure" };
    }
}

// ------------------------------------------------- Python json port (loads/dumps)

export class PythonJsonError extends Error {
    constructor(message, recursion = false) {
        super(message);
        this.name = "PythonJsonError";
        this.recursion = recursion;
    }
}

/** A JSON number as Python keeps it: the lexeme, and whether it is a float. */
class PyNumber {
    constructor(text, isFloat) {
        this.text = text;
        this.isFloat = isFloat;
    }
}

const NUMBER = /-?(?:0|[1-9]\d*)(\.\d+)?([eE][-+]?\d+)?/y;
const STRING_STOP = /["\\\u0000-\u001f]/g;
const WHITESPACE = /[ \t\n\r]*/y;
const ESCAPES = { '"': '"', "\\": "\\", "/": "/", b: "\b", f: "\f", n: "\n", r: "\r", t: "\t" };
const LITERALS = [["true", true], ["false", false], ["null", null], ["NaN", new PyNumber("NaN", true)], ["Infinity", new PyNumber("Infinity", true)], ["-Infinity", new PyNumber("-Infinity", true)]];

/**
 * Python's ``json.loads``: objects become Maps (insertion order, last duplicate wins in
 * the first position), numbers keep their lexeme, NaN/Infinity are accepted, raw
 * control characters and trailing data are rejected.
 */
export function pythonJsonLoads(text) {
    if (typeof text !== "string") throw new PythonJsonError("Not a string");
    let pos = 0;
    const fail = (message) => new PythonJsonError(`${message} at ${pos}`);
    const skip = () => {
        WHITESPACE.lastIndex = pos;
        pos = WHITESPACE.exec(text)[0].length + pos;
    };
    function string() {
        pos += 1;
        let out = "";
        for (;;) {
            STRING_STOP.lastIndex = pos;
            const stop = STRING_STOP.exec(text);
            if (!stop) throw fail("Unterminated string");
            out += text.slice(pos, stop.index);
            pos = stop.index + 1;
            const char = stop[0];
            if (char === '"') return out;
            if (char !== "\\") throw fail("Invalid control character");
            const escape = text[pos];
            if (escape === "u") {
                const hex = text.slice(pos + 1, pos + 5);
                if (!/^[0-9a-fA-F]{4}$/.test(hex)) throw fail("Invalid \\uXXXX escape");
                pos += 5;
                let code = parseInt(hex, 16);
                if (code >= 0xd800 && code <= 0xdbff && text[pos] === "\\" && text[pos + 1] === "u") {
                    const low = text.slice(pos + 2, pos + 6);
                    if (/^[0-9a-fA-F]{4}$/.test(low)) {
                        const lowCode = parseInt(low, 16);
                        if (lowCode >= 0xdc00 && lowCode <= 0xdfff) {
                            out += String.fromCharCode(code, lowCode);
                            pos += 6;
                            continue;
                        }
                    }
                }
                out += String.fromCharCode(code);
            } else if (escape in ESCAPES) {
                out += ESCAPES[escape];
                pos += 1;
            } else throw fail("Invalid \\escape");
        }
    }
    function value(depth) {
        if (depth > JSON_MAX_DEPTH) throw new PythonJsonError("Maximum recursion depth exceeded", true);
        const char = text[pos];
        if (char === '"') return string();
        if (char === "{") {
            pos += 1;
            const map = new Map();
            skip();
            if (text[pos] === "}") {
                pos += 1;
                return map;
            }
            for (;;) {
                if (text[pos] !== '"') throw fail("Expecting property name enclosed in double quotes");
                const key = string();
                skip();
                if (text[pos] !== ":") throw fail("Expecting ':' delimiter");
                pos += 1;
                skip();
                map.set(key, value(depth + 1));
                skip();
                if (text[pos] === "}") {
                    pos += 1;
                    return map;
                }
                if (text[pos] !== ",") throw fail("Expecting ',' delimiter");
                pos += 1;
                skip();
            }
        }
        if (char === "[") {
            pos += 1;
            const list = [];
            skip();
            if (text[pos] === "]") {
                pos += 1;
                return list;
            }
            for (;;) {
                list.push(value(depth + 1));
                skip();
                if (text[pos] === "]") {
                    pos += 1;
                    return list;
                }
                if (text[pos] !== ",") throw fail("Expecting ',' delimiter");
                pos += 1;
                skip();
            }
        }
        for (const [literal, result] of LITERALS) {
            if (text.startsWith(literal, pos)) {
                pos += literal.length;
                return result;
            }
        }
        NUMBER.lastIndex = pos;
        const number = NUMBER.exec(text);
        if (!number || number.index !== pos || !number[0]) throw fail("Expecting value");
        pos += number[0].length;
        const isFloat = Boolean(number[1] || number[2]);
        // CPython's int() refuses more digits than sys.get_int_max_str_digits().
        if (!isFloat && number[0].replace("-", "").length > INT_MAX_STR_DIGITS) throw fail("Exceeds the limit for integer string conversion");
        return new PyNumber(number[0], isFloat);
    }
    skip();
    const result = value(0);
    skip();
    if (pos !== text.length) throw fail("Extra data");
    return result;
}

/** Python's ``repr(float)``: shortest round-trip digits, exponent when decpt <= -4 or > 16. */
function pythonFloatRepr(number) {
    if (Number.isNaN(number)) return "NaN";
    if (number === Infinity) return "Infinity";
    if (number === -Infinity) return "-Infinity";
    if (number === 0) return Object.is(number, -0) ? "-0.0" : "0.0";
    const sign = number < 0 ? "-" : "";
    const [mantissa, exponent] = Math.abs(number).toExponential().split("e");
    const digits = mantissa.replace(".", "");
    const decpt = Number(exponent) + 1;
    if (decpt <= -4 || decpt > 16) {
        const shown = digits.length > 1 ? `${digits[0]}.${digits.slice(1)}` : digits;
        const power = decpt - 1;
        return `${sign}${shown}e${power < 0 ? "-" : "+"}${String(Math.abs(power)).padStart(2, "0")}`;
    }
    if (decpt <= 0) return `${sign}0.${"0".repeat(-decpt)}${digits}`;
    if (decpt >= digits.length) return `${sign}${digits}${"0".repeat(decpt - digits.length)}.0`;
    return `${sign}${digits.slice(0, decpt)}.${digits.slice(decpt)}`;
}

/** Python's ``json.dumps`` default ``ensure_ascii`` string escaping. */
function pythonEscape(text) {
    let out = '"';
    for (let index = 0; index < text.length; index += 1) {
        const char = text[index];
        const code = text.charCodeAt(index);
        if (char === '"') out += '\\"';
        else if (char === "\\") out += "\\\\";
        else if (char === "\n") out += "\\n";
        else if (char === "\r") out += "\\r";
        else if (char === "\t") out += "\\t";
        else if (char === "\b") out += "\\b";
        else if (char === "\f") out += "\\f";
        else if (code < 0x20 || code > 0x7e) out += `\\u${code.toString(16).padStart(4, "0")}`;
        else out += char;
    }
    return `${out}"`;
}

/** Python's ``json.dumps(value)`` with default separators and ``ensure_ascii``, on a loads() value. */
export function pythonJsonDumps(value) {
    if (value === null) return "null";
    if (value === true) return "true";
    if (value === false) return "false";
    if (typeof value === "string") return pythonEscape(value);
    if (value instanceof PyNumber) {
        if (!value.isFloat) return value.text === "-0" ? "0" : value.text;
        return pythonFloatRepr(Number(value.text));
    }
    if (Array.isArray(value)) return `[${value.map(pythonJsonDumps).join(", ")}]`;
    if (value instanceof Map) return `{${[...value].map(([key, item]) => `${pythonEscape(key)}: ${pythonJsonDumps(item)}`).join(", ")}}`;
    throw new PythonJsonError("Not a JSON value");
}

// ------------------------------------------------------------ carrier selection

/** utils/video_metadata._record: (text or null, record format or null). */
function record(value, key) {
    if (key === "workflow" && !(typeof value === "string" || value instanceof Map)) return [null, "invalid"];
    if (typeof value === "string") return [value, "original-text"];
    if (value instanceof Map) return [pythonJsonDumps(value), "reserialized-json"];
    return [null, null];
}

/**
 * The one container carrier of prompt metadata, with the server's record budgets
 * applied (utils/video_metadata.select_video_carrier, then the metadata loop of
 * utils/prompt_inspector.read_inspection_file).
 *
 * @param {Object<string,string>} tags - container tags as ffprobe would report them
 * @param {Object} [options]
 * @param {string[]} [options.limited] - records the reader left out (`['workflow']`)
 * @returns {{status:'complete', records:Object<string,string>, record_formats:Object<string,string>, carrierText:string|null, limited:string[]}
 *   | {status:'unreadable', reason:'duplicate-metadata'|'invalid-metadata'}
 *   | {status:'limited', reason:'metadata-limit'}}
 */
export function selectVideoCarrier(tags, options = {}) {
    let limitedRecords = new Set(options.limited || []);
    const folded = new Map();
    const entries = tags && typeof tags === "object" && !Array.isArray(tags) ? Object.entries(tags) : [];
    for (const [key, value] of entries) {
        if (ENVELOPE_NAMES.has(key)) {
            folded.set(key, value);
            continue;
        }
        const name = key.toLowerCase();
        if (!PER_KEY_NAMES.has(name)) continue;
        if (folded.has(name) && folded.get(name) !== value) return { status: "unreadable", reason: "duplicate-metadata" };
        folded.set(name, value);
    }
    const candidates = [];
    const perKey = new Map(METADATA_KEYS.filter((key) => folded.has(key)).map((key) => [key, folded.get(key)]));
    if (perKey.size) candidates.push([perKey, null]);
    for (const tag of ENVELOPE_KEYS) {
        const raw = folded.get(tag);
        if (typeof raw !== "string") continue;
        let envelope;
        try {
            envelope = pythonJsonLoads(raw);
        } catch (error) {
            if (error instanceof PythonJsonError && error.recursion) return { status: "unreadable", reason: "invalid-metadata" };
            continue;
        }
        if (envelope instanceof Map) candidates.push([envelope, raw]);
    }
    const selected =
        candidates.find(([fields]) => ROLE_KEYS.some((key) => fields.has(key))) ?? candidates.find(([fields]) => fields.has("workflow")) ?? null;
    const formats = {};
    if (selected === null) {
        for (const key of limitedRecords) formats[key] = "limited";
        return { status: "complete", records: {}, record_formats: formats, carrierText: null, limited: [...limitedRecords] };
    }
    const [fields, carrierText] = selected;
    // An envelope carrier is whole; a left-out per-key workflow belonged to the carrier that lost.
    if (carrierText !== null) limitedRecords = new Set();
    const metadata = {};
    for (const key of METADATA_KEYS) {
        if (!fields.has(key)) continue;
        if (fields.get(key) === OVERSIZED_WORKFLOW) {
            // The reader left it unread: the server reads it, then finds it over budget.
            limitedRecords.add(key);
            continue;
        }
        const [text, format] = record(fields.get(key), key);
        if (format === "invalid") {
            formats[key] = format;
            continue;
        }
        metadata[key] = text;
        if (format !== null) formats[key] = format;
    }
    const records = {};
    let roleSize = 0;
    for (const key of METADATA_KEYS) {
        if (!(key in metadata)) continue;
        const value = metadata[key];
        if (typeof value !== "string") return { status: "unreadable", reason: "invalid-metadata" };
        const size = utf8Length(value);
        if (key === "workflow") {
            if (size > WORKFLOW_METADATA_BYTES) {
                limitedRecords.add(key);
                continue;
            }
        } else {
            roleSize += size;
            if (roleSize > ROLE_METADATA_BYTES) return { status: "limited", reason: "metadata-limit" };
        }
        records[key] = value;
        if (!(key in formats)) formats[key] = "original-text";
    }
    for (const key of limitedRecords) formats[key] = "limited";
    return { status: "complete", records, record_formats: formats, carrierText, limited: [...limitedRecords] };
}
