// One chosen local File, read entirely in this browser, as the inspection wire
// the workspace already renders. The readers are ports of the server's
// (png-metadata.js readPngMetadataFile/readImageHeader, local-video.mjs); the
// projection is local-projection.mjs. Nothing here builds a URL or sends bytes:
// the only URL a handle carries is the session-owned blob: URL it was created with.
import { readVideoTags, selectVideoCarrier } from './local-video.mjs';
import { projectMetadata, fitInspectionPayload, MAX_RESPONSE_BYTES } from './local-projection.mjs';
import { projectInspectionPayload } from './inspection-state.mjs';

export const LOCAL_OBSERVATION = 'selected-local-file';
export const LOCAL_SOURCE_LABEL = 'Selected local file';
// Interim roles until PR L3 ports the inspection resolver: every role is open, and
// the legacy fill in projectInspectionPayload shows what the legacy pages show.
const LOCAL_RESOLUTION_REASON = 'Resolved in this browser from the workflow.';
const LOCAL_LIBRARY = Object.freeze({ status: 'unavailable', scope: 'saved-library', tags: [], category_scope: null, tags_scope: null });
const LOCAL_SCOPE = 'local';
const PREFIX_BYTES = 32;
const MAX_FILENAME_CHARS = 255;
const MEDIA_TYPES = new Set(['image', 'video']);

const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const pngReader = () => globalThis.PngMetadata || globalThis.window?.PngMetadata;

/** A display name with no path separators or control characters; never empty. */
export function sanitizeLocalFilename(name) {
    const text = typeof name === 'string' ? name : '';
    const cleaned = text.replace(/[\x00-\x1f\x7f]/g, '').replace(/[\\/]/g, '_').trim();
    return [...cleaned].slice(0, MAX_FILENAME_CHARS).join('') || 'file';
}

/** `pathlib.Path(name).suffix.lower()`. */
function localExtension(filename) {
    const dot = filename.lastIndexOf('.');
    return dot > 0 && dot < filename.length - 1 ? filename.slice(dot).toLowerCase() : '';
}

function classificationOf(value) {
    if (!object(value) || !MEDIA_TYPES.has(value.media) || typeof value.kind !== 'string' || !value.kind) throw new TypeError('Unsupported local file');
    return Object.freeze({ media: value.media, kind: value.kind });
}

/**
 * Run the readers the server would run for these bytes: the bounded PNG reader,
 * the JPEG/WebP header reader, or the container tag readers plus the carrier rule.
 * Returns `{classification, result}` in the oracle's shape; `result` is null for
 * bytes no reader claims.
 */
export async function readLocalFile(file, { classification, signal, pngMetadata = pngReader() } = {}) {
    if (!(file instanceof Blob)) throw new TypeError('A local file is required');
    const reader = () => {
        if (!pngMetadata || typeof pngMetadata.classifyPrefix !== 'function') throw new TypeError('The local PNG reader is unavailable');
        return pngMetadata;
    };
    const prefix = new Uint8Array(await file.slice(0, Math.min(PREFIX_BYTES, file.size)).arrayBuffer());
    const classified = classification === undefined ? reader().classifyPrefix(prefix) : classification;
    if (classified === null) return { classification: null, result: null };
    const { media, kind } = classificationOf(classified);
    if (kind === 'png') return { classification: classified, result: await reader().readPngMetadataFile(file, { signal, prefix }) };
    if (media === 'image') return { classification: classified, result: await reader().readImageHeader(file, kind) };
    const read = await readVideoTags(file, { signal, prefix, kind });
    return { classification: classified, result: { read, selection: read.status === 'complete' ? selectVideoCarrier(read.tags, { limited: read.limited }) : null } };
}

/**
 * The server's InspectionRead for a reader result: `read` is null when the server
 * would have raised MetadataReadError (unreadable), else
 * `{records, record_formats, carrierText, limited}`; `format`, `width` and
 * `height` follow `read_inspection_file` (a video has none).
 */
export function toInspectionRead(classification, result) {
    const { media, kind } = classificationOf(classification);
    const none = { read: null, format: null, width: null, height: null };
    if (kind === 'png') {
        if (!object(result) || result.status !== 'complete') return none;
        const limited = [...(result.limited ?? [])];
        const record_formats = Object.fromEntries(Object.keys(result.records).map(key => [key, 'original-text']));
        for (const key of limited) record_formats[key] = 'limited';
        return { read: { records: { ...result.records }, record_formats, carrierText: null, limited }, format: 'PNG', width: result.width ?? null, height: result.height ?? null };
    }
    if (media === 'image') {
        if (!object(result)) return none;
        return { read: { records: {}, record_formats: {}, carrierText: null, limited: [] }, format: result.format ?? null, width: result.width ?? null, height: result.height ?? null };
    }
    const selection = result?.selection;
    if (result?.read?.status !== 'complete' || !object(selection) || selection.status !== 'complete') return none;
    return { read: { records: { ...selection.records }, record_formats: { ...selection.record_formats }, carrierText: selection.carrierText ?? null, limited: [...(selection.limited ?? [])] }, format: null, width: null, height: null };
}

const dimension = value => Number.isSafeInteger(value) && value > 0 ? value : null;

/** The `inspection_version: 1` wire for one local read, before the response budget. */
export function buildLocalPayload({ read, file, classification }) {
    const classified = classificationOf(classification);
    const inspection = toInspectionRead(classified, read);
    const filename = sanitizeLocalFilename(file?.name);
    const size = Number.isSafeInteger(file?.size) && file.size >= 0 ? file.size : null;
    const modified = typeof file?.lastModified === 'number' && Number.isFinite(file.lastModified) && file.lastModified >= 0 ? file.lastModified / 1000 : null;
    return {
        success: true, inspection_version: 1,
        file: {
            observation: LOCAL_OBSERVATION, source_label: LOCAL_SOURCE_LABEL, media_type: classified.media, extension: localExtension(filename),
            format: inspection.format, size, width: dimension(inspection.width), height: dimension(inspection.height), modified_time: modified,
        },
        prompts: { positive: { status: 'unresolved' }, negative: { status: 'unresolved' } },
        resolution: { state: 'unresolved', sampler_id: null, reason: LOCAL_RESOLUTION_REASON, reason_code: null, node: null },
        metadata: projectMetadata(inspection.read),
        library: { ...LOCAL_LIBRARY, tags: [] },
    };
}

/** The server's 8 MiB response budget, applied to the local wire with the same omission order. */
export function fitLocalPayload(wire, limit = MAX_RESPONSE_BYTES, options = {}) {
    return fitInspectionPayload(wire, limit, options);
}

// Handles this module created, by a private token: the controller and the viewer
// session spread-copy descriptors, so a copy of an owned handle is the same
// selection, while a hand-made descriptor or a tampered URL is foreign.
const TOKEN = Symbol('local-file');
const OWNED = new WeakMap();
let sequence = 0;

/**
 * One selection of a local file. `url` is the session's own `blob:` URL (or null
 * when none could be created); every selection gets a new key, so choosing the
 * same bytes twice never reuses a stale read.
 */
export function createLocalFileHandle({ file, url = null, classification, filename = file?.name }) {
    if (!(file instanceof Blob)) throw new TypeError('A local file is required');
    if (url !== null && (typeof url !== 'string' || !url.startsWith('blob:'))) throw new TypeError('Local media must be a blob: URL');
    const classified = classificationOf(classification);
    const token = Object.freeze({});
    const secret = Object.freeze({ key: `local:${++sequence}`, filename: sanitizeLocalFilename(filename), url, file, classification: classified });
    OWNED.set(token, secret);
    return Object.freeze({ kind: 'local-file', key: secret.key, filename: secret.filename, is_video: classified.media === 'video', url, file, classification: classified, [TOKEN]: token });
}

/**
 * Ends a selection: the handle and every copy of it become foreign, so a replaced
 * or released file can no longer be read, downloaded or shown.
 */
export function releaseLocalFileHandle(handle) {
    if (object(handle) && handle[TOKEN]) OWNED.delete(handle[TOKEN]);
}

/** The owned selection behind a handle (or a copy of one); throws for anything else. */
function owned(handle) {
    const secret = object(handle) ? OWNED.get(handle[TOKEN]) : undefined;
    if (!secret || secret.key !== handle.key || secret.url !== handle.url || secret.file !== handle.file || secret.filename !== handle.filename) throw new TypeError('Unknown local file');
    return secret;
}

function validate(handle) {
    owned(handle);
}

function originalDownload(handle) {
    const secret = owned(handle);
    if (secret.url === null) throw new TypeError('Original media is unavailable');
    return { url: secret.url, filename: secret.filename };
}

export const localFileAdapter = Object.freeze({
    validate,
    key(handle) { return owned(handle).key; },
    scope(handle) { owned(handle); return LOCAL_SCOPE; },
    // An inert sentinel: the controller's same-key check works and no URL is ever built.
    inspectionPath(handle) { return `local-file:${owned(handle).key}`; },
    async read(handle, { signal } = {}) {
        const secret = owned(handle);
        let result = null;
        try {
            ({ result } = await readLocalFile(secret.file, { classification: secret.classification, signal }));
        } catch (error) {
            // The browser could not read the bytes (removed, moved or denied after choosing): the server's
            // MetadataReadError, an unreadable record. Abort and programming errors still reject.
            if (!(error instanceof DOMException) || error.name === 'AbortError' || signal?.aborted) throw error;
        }
        return fitLocalPayload(buildLocalPayload({ read: result, file: { name: secret.filename, size: secret.file.size, lastModified: secret.file.lastModified }, classification: secret.classification }), MAX_RESPONSE_BYTES, { signal });
    },
    toRecord(result, handle, extractFacts) {
        const secret = owned(handle);
        if (!object(result) || result.success !== true || result.inspection_version !== 1) throw new TypeError('Invalid local inspection');
        return projectInspectionPayload(result, { kind: 'local-file', key: secret.key, path: secret.filename },
            secret.url === null ? null : originalDownload(handle), extractFacts);
    },
    originalDownload,
    // Session-owned, never resolved through the API client.
    mediaUrl(handle) { return owned(handle).url; },
});
