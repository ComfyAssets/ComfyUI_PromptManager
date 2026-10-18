import {projectInspectionPayload} from './inspection-state.mjs';

const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const uuid = value => typeof value === 'string' && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(value);
const digest = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
function scalar(value, bytes, nonempty = false) {
    if (typeof value !== 'string' || (nonempty && !value) || value.includes('\0') || /[\ud800-\udfff]/u.test(value) || new TextEncoder().encode(value).length > bytes) throw new TypeError('Invalid reported location scalar');
}
function coordinate(value) {
    if (!object(value) || !uuid(value.run_id) || !Number.isSafeInteger(value.image_ordinal) || value.image_ordinal < 0 || value.image_ordinal > 255) throw new TypeError('Invalid output occurrence');
    scalar(value.output_node_id, 512, true);
    if ([...value.output_node_id].length > 128) throw new TypeError('Invalid output node');
}
function reference(value) {
    if (!object(value) || !['output', 'temp'].includes(value.folder_type) || !['posix', 'windows'].includes(value.path_style) || !(value.root_key === null || digest(value.root_key))) throw new TypeError('Invalid output reference');
    scalar(value.filename, 1024, true); scalar(value.subfolder, 2048);
    const windows = value.path_style === 'windows', separators = windows ? /[/\\]/ : /\//;
    if (['.', '..'].includes(value.filename) || separators.test(value.filename) || value.subfolder.startsWith('/') ||
        (windows && (/^[\\/]/.test(value.subfolder) || /:/.test(value.filename + value.subfolder))) ||
        (value.subfolder && value.subfolder.split(separators).some(part => !part || part === '.' || part === '..'))) throw new TypeError('Unsafe output reference');
    if (128 + new TextEncoder().encode(value.folder_type + value.path_style + value.subfolder + value.filename + (value.root_key || '')).length > 4096) throw new TypeError('Output reference exceeds limit');
}
export function occurrenceKey(occurrence) {
    coordinate(occurrence);
    return JSON.stringify([occurrence.run_id, occurrence.output_node_id, occurrence.image_ordinal]);
}
function validateTarget(target) {
    if (!object(target) || target.kind !== 'reported-location' || !uuid(target.history_epoch) || !digest(target.current_scope) || !digest(target.location_key) || typeof target.is_video !== 'boolean') throw new TypeError('Invalid reported location');
    coordinate(target.occurrence); reference(target.reference);
    if (target.filename !== target.reference.filename) throw new TypeError('Mismatched reported filename');
}
function urlFor(target, field, endpoint) {
    const location = globalThis.location || globalThis.window?.location;
    const base = new URL(location?.href || 'http://localhost/');
    const resolve = globalThis.window?.ApiClient?.resolveUrl || globalThis.ApiClient?.resolveUrl || (value => value);
    if (typeof target[field] !== 'string' || !target[field]) throw new TypeError('Missing reported media URL');
    const value = resolve(target[field]), url = new URL(value, base);
    const expected = new URL(resolve(`/prompt_manager/history/runs/${target.occurrence.run_id}/output/current/${endpoint}`), base);
    const identity = {node:target.occurrence.output_node_id, ordinal:String(target.occurrence.image_ordinal), epoch:target.history_epoch, current_scope:target.current_scope};
    if (!['http:', 'https:'].includes(url.protocol) || url.origin !== base.origin || url.username || url.password || url.hash ||
        url.pathname !== expected.pathname || /\/(?:\.|%2e){1,2}(?:\/|%2f)/i.test(value) ||
        [...url.searchParams.keys()].some(key => !Object.hasOwn(identity, key) && key !== '_pm_viewer') || url.searchParams.getAll('_pm_viewer').length > 1 ||
        Object.entries(identity).some(([key, expected]) => url.searchParams.getAll(key).length !== 1 || url.searchParams.get(key) !== expected)) throw new TypeError('Invalid reported media identity');
    return value;
}
function validate(target) {
    validateTarget(target);
    urlFor(target, 'url', 'media'); urlFor(target, 'inspection_url', 'inspection'); urlFor(target, 'thumbnail_url', 'thumbnail');
}
function changed(code) {
    return Object.assign(new Error('Current recorded location changed; reload before using this file.'), {status:409, refreshRequired:true, code, recovery:{action:code === 'history-changed' ? 'refresh-history' : 'reload-outputs'}});
}
function sameReference(a, b) {
    return object(a) && ['folder_type', 'subfolder', 'filename', 'path_style', 'root_key'].every(key => a[key] === b[key]);
}
export function toHistoryInspectionRecord(result, target, extractFacts) {
    validate(target);
    if (!object(result) || result.success !== true || result.history_version !== 1 || result.inspection_version !== 1) throw new TypeError('Invalid history inspection');
    if (result.history_epoch !== target.history_epoch) throw changed('history-changed');
    const current = result.current_output;
    if (!object(current) || current.history_epoch !== target.history_epoch || current.current_scope !== target.current_scope || current.location_key !== target.location_key ||
        !object(current.occurrence) || ['run_id', 'output_node_id', 'image_ordinal'].some(key => current.occurrence[key] !== target.occurrence[key]) || !sameReference(current.reference, target.reference)) throw changed('candidate-stale');
    if (current.observation !== 'current-file' || current.evidence_class !== 'locator_candidate' || current.file_identity !== 'unverified' || current.newly_generated !== 'unknown' || current.context_membership !== 'unknown' || current.transient !== (target.reference.folder_type === 'temp')) throw new TypeError('Invalid occurrence evidence');
    const path = [target.reference.subfolder, target.reference.filename].filter(Boolean).join('/');
    const identity = {kind:'reported-location', key:target.location_key, path:target.reference.path_style === 'windows' ? path.replaceAll('\\', '/') : path,
        occurrence:{...target.occurrence}, history_epoch:target.history_epoch, current_scope:target.current_scope, reference:{...target.reference}};
    return projectInspectionPayload(result, identity, {url:urlFor(target, 'url', 'media'), filename:target.filename}, extractFacts);
}
export const reportedLocationAdapter = Object.freeze({
    validate,
    key(target) {validateTarget(target); return target.location_key;},
    scope(target) {validateTarget(target); return `${target.history_epoch}:${target.current_scope}`;},
    inspectionPath(target) {validate(target); return target.inspection_url;},
    originalDownload(target) {validate(target); return {url:urlFor(target, 'url', 'media'), filename:target.filename};},
    toRecord:toHistoryInspectionRecord,
});

/** Only loaded, currently available locations enter a frozen Viewer opening. */
export function distinctCurrentMedia(receipts, selectedOccurrence) {
    if (!Array.isArray(receipts)) throw new TypeError('Invalid loaded receipts');
    const selected = selectedOccurrence === null || selectedOccurrence === undefined ? null : occurrenceKey(selectedOccurrence);
    const locations = new Map();
    for (const receipt of receipts) {
        if (receipt?.current_candidate?.status !== 'available') continue;
        const target = receipt.current_candidate.media;
        validate(target);
        if (occurrenceKey(receipt.occurrence) !== occurrenceKey(target.occurrence) || !sameReference(receipt.reference, target.reference)) throw new TypeError('Mismatched receipt media');
        if (!locations.has(target.location_key) || occurrenceKey(target.occurrence) === selected) locations.set(target.location_key, target);
    }
    return [...locations.values()];
}
