// Byte-exact ISO-BMFF (MP4/MOV/M4V) and Matroska (MKV/WebM) builders for the local
// video tag reader tests, laid out as ffmpeg's muxers write them (see the dumps in
// tests/fixtures/local-inspect). The File-like blobs come from png-bytes.mjs.
import { concat, u32, utf8, latin1 } from "./png-bytes.mjs";

export { concat, u32, utf8, latin1 };
export { sparseBlob, recordingBlob, slowBlob, failingBlob, blobOf } from "./png-bytes.mjs";

const toBytes = (part) => (typeof part === "string" ? utf8(part) : Buffer.from(part));

function u64(n) {
    const out = new Uint8Array(8);
    new DataView(out.buffer).setBigUint64(0, BigInt(n));
    return out;
}

// ---------------------------------------------------------------- ISO-BMFF

/** A box: 32-bit size, 4-byte type (string or raw bytes), payload. `size` overrides the field. */
export function box(type, ...parts) {
    const options = parts.length && parts[parts.length - 1] && parts[parts.length - 1].__options ? parts.pop() : {};
    const payload = concat(...parts.map(toBytes));
    const typeBytes = typeof type === "string" ? latin1(type) : Buffer.from(type);
    const total = 8 + payload.length;
    if (options.largesize) return concat(u32(1), typeBytes, u64(options.largesize === true ? total + 8 : options.largesize), payload);
    return concat(u32(options.size ?? total), typeBytes, payload);
}

/** Marker for `box` options: box('ftyp', ..., opts({size: 3})). */
export function opts(options) {
    return { __options: true, ...options };
}

export function fullBox(type, ...parts) {
    return box(type, u32(0), ...parts);
}

export function ftyp(brand = "isom", compatible = "isomiso2avc1mp41") {
    return box("ftyp", latin1(brand), u32(512), latin1(compatible));
}

export function mdat(payload = [0, 0, 0, 0]) {
    return box("mdat", payload);
}

export const moov = (...children) => box("moov", ...children);
export const udta = (...children) => box("udta", ...children);
export const free = (count = 0) => box("free", new Uint8Array(count));

/** `meta` as ffmpeg writes it: a FullBox (version/flags) whose first child is hdlr. */
export function meta(...children) {
    return fullBox("meta", ...children);
}

export function hdlr(handler = "mdta") {
    return fullBox("hdlr", u32(0), latin1(handler), new Uint8Array(12), [0]);
}

/** `keys`: one mdta entry per name (an entry may be [name, namespace]); `count` overrides the count field. */
export function keys(names, { count } = {}) {
    const entries = names.map((entry) => {
        const [name, namespace] = Array.isArray(entry) ? entry : [entry, "mdta"];
        const bytes = toBytes(name);
        return concat(u32(8 + bytes.length), latin1(namespace), bytes);
    });
    return fullBox("keys", u32(count ?? names.length), ...entries);
}

export const ilst = (...items) => box("ilst", ...items);

/** A `data` atom: type indicator (1 = UTF-8), locale, payload. */
export function data(payload, { type = 1, locale = 0, size } = {}) {
    return box("data", u32(type), u32(locale), toBytes(payload), ...(size === undefined ? [] : [opts({ size })]));
}

/** An ilst item addressed by its 1-based `keys` index. */
export function item(index, ...children) {
    return box(u32(index), ...children);
}

/** An ilst item addressed by a legacy fourcc such as "©cmt" (Latin-1 bytes). */
export function legacyItem(fourcc, ...children) {
    return box(latin1(fourcc), ...children);
}

/** `moov/udta/meta` carrying per-key mdta tags, as core SaveVideo writes through ffmpeg. */
export function mdtaUdta(tags) {
    const names = Object.keys(tags);
    return udta(meta(hdlr("mdta"), keys(names), ilst(...names.map((name, index) => item(index + 1, data(tags[name]))))));
}

/** `moov/udta/meta` with legacy ilst atoms: {comment, description} → ©cmt / desc. */
export function legacyUdta({ comment, description, tool = "Lavf" } = {}) {
    const items = [legacyItem("©too", data(tool))];
    if (comment !== undefined) items.push(legacyItem("©cmt", data(comment)));
    if (description !== undefined) items.push(legacyItem("desc", data(description)));
    return udta(meta(hdlr("mdir"), ilst(...items)));
}

/** A whole MP4: ftyp, (moov first when faststart), mdat, moov with the given udta. */
export function mp4(udtaBox, { brand = "isom", faststart = false, extraMoov = [], payload } = {}) {
    const movie = moov(...extraMoov, udtaBox);
    const media = mdat(payload);
    return faststart ? concat(ftyp(brand), movie, media) : concat(ftyp(brand), free(), media, movie);
}

export function perKeyMp4(tags, options) {
    return mp4(mdtaUdta(tags), options);
}

/** Top-level boxes of `bytes` as [{type, start, end}]. */
export function topLevelBoxes(bytes) {
    const out = [];
    let offset = 0;
    while (offset + 8 <= bytes.length) {
        const view = new DataView(bytes.buffer, bytes.byteOffset + offset);
        let size = view.getUint32(0);
        if (size === 1) size = Number(view.getBigUint64(8));
        if (size === 0) size = bytes.length - offset;
        out.push({ type: latin1Of(bytes.subarray(offset + 4, offset + 8)), start: offset, end: offset + size });
        offset += size;
    }
    return out;
}

function latin1Of(bytes) {
    return Buffer.from(bytes).toString("latin1");
}

// ---------------------------------------------------------------- Matroska

export const ID = {
    EBML: 0x1a45dfa3,
    EBMLVersion: 0x4286,
    DocType: 0x4282,
    DocTypeVersion: 0x4287,
    Segment: 0x18538067,
    SeekHead: 0x114d9b74,
    Seek: 0x4dbb,
    SeekID: 0x53ab,
    SeekPosition: 0x53ac,
    Void: 0xec,
    CRC32: 0xbf,
    Info: 0x1549a966,
    Tracks: 0x1654ae6b,
    Tags: 0x1254c367,
    Tag: 0x7373,
    Targets: 0x63c0,
    TargetTypeValue: 0x68ca,
    TagTrackUID: 0x63c5,
    SimpleTag: 0x67c8,
    TagName: 0x45a3,
    TagString: 0x4487,
    TagLanguage: 0x447a,
    TagDefault: 0x4484,
    TagBinary: 0x4485,
    Cluster: 0x1f43b675,
    Timestamp: 0xe7,
    Cues: 0x1c53bb6b,
};

/** The ID's bytes (EBML IDs carry their own length marker). */
export function idBytes(id) {
    const out = [];
    let value = id;
    while (value > 0) {
        out.unshift(value & 0xff);
        value = Math.floor(value / 256);
    }
    return Uint8Array.from(out);
}

/** A size VINT: minimal width unless `width` is given; `unknown` writes all ones. */
export function vint(value, { width, unknown = false } = {}) {
    let length = width;
    if (!length) {
        length = 1;
        while (length < 8 && value >= 2 ** (7 * length) - 1) length += 1;
    }
    const out = new Uint8Array(length);
    if (unknown) {
        out.fill(0xff);
        out[0] = 0xff >> (length - 1);
        return out;
    }
    let rest = BigInt(value);
    for (let index = length - 1; index >= 0; index -= 1) {
        out[index] = Number(rest & 0xffn);
        rest >>= 8n;
    }
    out[0] |= 0x80 >> (length - 1);
    return out;
}

/** An element: ID, size VINT, payload. `size`/`width`/`unknown` shape the size field. */
export function el(id, ...parts) {
    const options = parts.length && parts[parts.length - 1] && parts[parts.length - 1].__options ? parts.pop() : {};
    const payload = concat(...parts.map(toBytes));
    const size = vint(options.size ?? payload.length, { width: options.width, unknown: options.unknown });
    return concat(idBytes(id), size, payload);
}

export function uint(value, width) {
    const out = [];
    let rest = BigInt(value);
    do {
        out.unshift(Number(rest & 0xffn));
        rest >>= 8n;
    } while (rest > 0n);
    while (width && out.length < width) out.unshift(0);
    return Uint8Array.from(out);
}

export function ebmlHeader(docType = "matroska") {
    return el(ID.EBML, el(ID.EBMLVersion, [1]), el(ID.DocType, docType), el(ID.DocTypeVersion, [4]));
}

/** Segment with an 8-byte size field as ffmpeg writes it; `unknown` makes it unknown-sized. */
export function segment(children, { unknown = false } = {}) {
    return el(ID.Segment, ...children, opts({ width: 8, unknown }));
}

/** SeekHead entries: [[id, position]] with positions relative to the Segment payload. */
export function seekHead(entries) {
    return el(ID.SeekHead, ...entries.map(([id, position]) => el(ID.Seek, el(ID.SeekID, idBytes(id)), el(ID.SeekPosition, uint(position)))));
}

export function voidEl(count = 4) {
    return el(ID.Void, new Uint8Array(count));
}

/** A SimpleTag with TagName and TagString (`value` undefined omits the string). */
export function simpleTag(name, value, { lang, def, binary, nested = [], extra = [] } = {}) {
    const children = [el(ID.TagName, name)];
    if (lang !== undefined) children.push(el(ID.TagLanguage, lang));
    if (def !== undefined) children.push(el(ID.TagDefault, [def]));
    if (value !== undefined) children.push(el(ID.TagString, value));
    if (binary !== undefined) children.push(el(ID.TagBinary, binary));
    return el(ID.SimpleTag, ...children, ...nested, ...extra);
}

/** A Tag whose Targets carry the given track UID (none → global). */
export function tag(simpleTags, { trackUid, targetType } = {}) {
    const targets = [];
    if (targetType !== undefined) targets.push(el(ID.TargetTypeValue, uint(targetType)));
    if (trackUid !== undefined) targets.push(el(ID.TagTrackUID, uint(trackUid, 8)));
    return el(ID.Tag, el(ID.Targets, ...targets), ...simpleTags);
}

export function tagsEl(...tagElements) {
    return el(ID.Tags, ...tagElements);
}

/** A Cluster of `size` payload bytes (zeros); `unknown` makes its size unknown. */
export function cluster(size = 8, { unknown = false, payload } = {}) {
    return el(ID.Cluster, payload ?? new Uint8Array(size), opts({ unknown }));
}

/** A Cluster header only, declaring `size` payload bytes (for sparse layouts). */
export function clusterHeader(size) {
    return concat(idBytes(ID.Cluster), vint(size, { width: 8 }));
}

/** A whole Matroska file: EBML header + Segment(children). */
export function mkv(children, { docType = "matroska", unknown = false } = {}) {
    return concat(ebmlHeader(docType), segment(children, { unknown }));
}

/** A global Tags element from {name: value} (ffmpeg upper-cases names at mux time). */
export function globalTags(tags, options) {
    return tagsEl(tag(Object.entries(tags).map(([name, value]) => simpleTag(name, value)), options));
}

/** A Matroska file whose global Tags hold {name: value}, ffmpeg layout. */
export function perKeyMkv(tags, { docType = "matroska" } = {}) {
    return mkv([el(ID.Info, new Uint8Array(4)), globalTags(tags), cluster()], { docType });
}
