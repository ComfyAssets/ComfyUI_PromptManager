import { imageKey } from './images-state.mjs';
import { promptRoles } from './prompt-blocks.mjs';

const SOURCE_CHANGED = 'The source changed. Refresh the image collection before inspecting this file.';
const RECORD_KEYS = ['prompt', 'workflow', 'parameters', 'comfyassets_prompt_pairs'];
const FACT_FIELDS = ['seed', 'steps', 'cfg', 'sampler', 'scheduler', 'model'];
// `invalid` and `limited` records carry no text. Only a workflow can be `limited`:
// role records over their budget fail the whole read on the server.
const RECORD_FORMATS = ['original-text', 'reserialized-json', 'invalid', 'limited'];
const TEXTLESS_FORMATS = ['invalid', 'limited'];
const REASON_CODES = ['work-limit', 'depth-limit', 'text-limit', 'graph-ceiling', 'unsupported-node', 'runtime-value', 'composite', 'no-sampler', 'malformed', 'multistage-differs'];
// Only these codes name the blocking node; node IDs and class names are bounded.
const NAMED_CODES = ['unsupported-node', 'runtime-value', 'composite'];
const NAME_LENGTH = 200, MESSAGE_LENGTH = 512;
const boundedText = (value, limit) => typeof value === 'string' && value.length > 0 && value.length <= limit;
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const validScope = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value) && value.length === 64;

export class InspectionSourceChangedError extends Error {
    constructor() {
        super(SOURCE_CHANGED);
        this.name = 'InspectionSourceChangedError';
        this.refreshRequired = true;
    }
}

function descriptorScope(image) {
    if (!Object.hasOwn(image, 'root_scope')) return null;
    if (!validScope(image.root_scope)) throw new TypeError('Invalid inspection source scope');
    return image.root_scope;
}

function validateIdentity(image) {
    if (!object(image) || !Number.isSafeInteger(image.root_index) || image.root_index < 0 ||
        typeof image.path !== 'string' || !image.path || image.path.includes('\\') || /^[a-z]:/i.test(image.path) || /[\x00-\x1f\x7f]/.test(image.path) ||
        image.path.split('/').some(part => !part || part === '.' || part === '..')) throw new TypeError('Invalid inspection identity');
    return descriptorScope(image);
}

export function inspectPath(image) {
    const scope = validateIdentity(image);
    const query = new URLSearchParams({ root: String(image.root_index) });
    if (scope !== null) query.set('root_scope', scope);
    return `/prompt_manager/images/inspect/${image.path.split('/').map(encodeURIComponent).join('/')}?${query}`;
}

/** One path segment as the server quotes it (Python `quote(..., safe="/")` also escapes `!'()*`, #249 M11). */
const quoteSegment = segment => encodeURIComponent(segment).replace(/[!'()*]/g, c => `%${c.charCodeAt(0).toString(16).toUpperCase()}`);

function downloadFor(image, required) {
    try {
        if (typeof image.url !== 'string' || !image.url) throw new TypeError('Missing original media URL');
        const location = globalThis.location || globalThis.window?.location;
        const reference = new URL(location?.href || 'http://localhost/');
        const resolveUrl = globalThis.window?.ApiClient?.resolveUrl || globalThis.ApiClient?.resolveUrl || (url => url);
        const value = resolveUrl(image.url);
        const url = new URL(value, reference);
        const sameOrigin = 'Origin' in globalThis ?
            globalThis.Origin.from(url).isSameOrigin(globalThis.Origin.from(reference)) :
            url.origin !== 'null' && url.origin === reference.origin;
        const expected = new URL(resolveUrl(`/prompt_manager/images/serve/${image.path.split('/').map(quoteSegment).join('/')}`), reference);
        const scope = descriptorScope(image);
        if (!sameOrigin || !['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.hash ||
            url.pathname !== expected.pathname || url.searchParams.getAll('root').length !== 1 || url.searchParams.get('root') !== String(image.root_index) ||
            url.searchParams.getAll('root_scope').length > 1 || (scope !== null && url.searchParams.get('root_scope') !== scope) ||
            (url.searchParams.has('root_scope') && !validScope(url.searchParams.get('root_scope')))) throw new TypeError('Invalid original media identity');
        return { url: value, filename: image.path.split('/').at(-1) };
    } catch (error) {
        if (required) throw new TypeError('Invalid original media identity', { cause: error });
        return null;
    }
}

// The server's video extensions (py/api/inspection_service.py VIDEO_EXTENSIONS): their preview is a JPEG.
const VIDEO_EXTENSIONS = new Set(['.mp4', '.webm', '.avi', '.mov', '.mkv', '.m4v', '.wmv']);

/** `thumbnails/<path>_thumb<ext>` for a listed file, as the server names its preview (images.media_thumbnail_rel). */
function previewPath(path) {
    const slash = path.lastIndexOf('/'), dot = path.lastIndexOf('.');
    const cut = dot > slash ? dot : path.length, extension = path.slice(cut).toLowerCase();
    return `thumbnails/${path.slice(0, cut)}_thumb${VIDEO_EXTENSIONS.has(extension) ? '.jpg' : extension}`;
}

/**
 * #249: the listing's thumbnail of `image` as `{url}` for the inspector's cover, or null. Only the same-origin
 * serve URL of this file's own preview under the same root and source scope is used; anything else keeps the
 * original (a missing preview is made by the server on first view).
 */
function previewFor(image) {
    try {
        if (typeof image.thumbnail_url !== 'string' || !image.thumbnail_url) return null;
        const location = globalThis.location || globalThis.window?.location;
        const reference = new URL(location?.href || 'http://localhost/');
        const resolveUrl = globalThis.window?.ApiClient?.resolveUrl || globalThis.ApiClient?.resolveUrl || (url => url);
        const value = resolveUrl(image.thumbnail_url), url = new URL(value, reference);
        const expected = new URL(resolveUrl(`/prompt_manager/images/serve/${previewPath(image.path).split('/').map(quoteSegment).join('/')}`), reference);
        const scope = descriptorScope(image);
        if (url.origin !== reference.origin || url.username || url.password || url.hash || url.pathname !== expected.pathname ||
            url.searchParams.getAll('root').length !== 1 || url.searchParams.get('root') !== String(image.root_index) ||
            url.searchParams.getAll('root_scope').length > 1 || (scope !== null && url.searchParams.get('root_scope') !== scope)) return null;
        return { url: value };
    } catch {
        return null;
    }
}

export function originalMediaDownload(image) {
    validateIdentity(image);
    return downloadFor(image, true);
}

function promptsFor(value) {
    if (!object(value)) throw new TypeError('Invalid inspection prompts');
    const confirmed = promptRoles(value);
    for (const role of ['positive', 'negative']) {
        const record = value[role];
        if (record === undefined) continue;
        if (!object(record) || typeof record.status !== 'string' ||
            (record.text !== undefined && record.text !== null && typeof record.text !== 'string') ||
            (record.status === 'resolved' && !Object.hasOwn(confirmed, role)) ||
            (record.message !== undefined && (record.status === 'resolved' || !boundedText(record.message, MESSAGE_LENGTH)))) throw new TypeError('Invalid inspection role');
    }
    return value;
}

function fileFor(value, identity) {
    // A local record observes the selected file itself; every other adapter observes a current server file.
    const observation = identity?.kind === 'local-file' ? 'selected-local-file' : 'current-file';
    if (!object(value) || value.observation !== observation || typeof value.source_label !== 'string' ||
        !['image', 'video'].includes(value.media_type) || typeof value.extension !== 'string' ||
        (value.format !== null && typeof value.format !== 'string')) throw new TypeError('Invalid inspection file');
    const file = { observation: value.observation, source_label: value.source_label, media_type: value.media_type, extension: value.extension, format: value.format };
    for (const key of ['size', 'width', 'height', 'modified_time']) {
        const item = value[key] ?? null;
        if (item !== null && (typeof item !== 'number' || !Number.isFinite(item) ||
            (['width', 'height'].includes(key) ? !Number.isSafeInteger(item) || item <= 0 : item < 0))) throw new TypeError('Invalid inspection file observation');
        file[key] = item;
    }
    return file;
}

function graphFor(value) {
    if (!object(value) || !['parsed', 'absent', 'malformed', 'limited'].includes(value.status) ||
        (value.status === 'parsed' ? !object(value.value) : value.value !== null)) throw new TypeError('Invalid inspection graph');
    return { status: value.status, value: value.value };
}

function metadataFor(value) {
    if (!object(value) || !['available', 'absent', 'unreadable', 'limited'].includes(value.status) ||
        ![null, 'response-size-limit'].includes(value.omitted_reason) || !object(value.raw) || !object(value.record_formats) ||
        (value.parameters !== null && typeof value.parameters !== 'string')) throw new TypeError('Invalid inspection metadata');
    const raw = {}, formats = {};
    for (const [key, format] of Object.entries(value.record_formats)) {
        if (!RECORD_KEYS.includes(key) || !RECORD_FORMATS.includes(format) || (format === 'limited' && key !== 'workflow')) throw new TypeError('Invalid inspection record format');
        formats[key] = format;
    }
    for (const [key, text] of Object.entries(value.raw)) {
        if (!RECORD_KEYS.includes(key) || typeof text !== 'string' || !Object.hasOwn(formats, key) || TEXTLESS_FORMATS.includes(formats[key])) throw new TypeError('Invalid inspection raw record');
        raw[key] = text;
    }
    if (value.parameters !== (raw.parameters ?? null)) throw new TypeError('Invalid inspection parameters projection');
    const prompt = graphFor(value.prompt_graph), workflow = graphFor(value.workflow_graph);
    const wholeRawOmitted = value.status === 'limited';
    if (wholeRawOmitted && (Object.keys(raw).length || value.parameters !== null)) throw new TypeError('Invalid atomic raw omission');
    if (!wholeRawOmitted && Object.entries(formats).some(([key, format]) => !TEXTLESS_FORMATS.includes(format) && !Object.hasOwn(raw, key))) throw new TypeError('Missing represented raw record');
    // The response budget omits the workflow graph, then the prompt graph, then all raw.
    // An invalid record's graph is otherwise malformed, so a limited one marks omission.
    const promptGraphOmitted = formats.prompt === 'invalid' && prompt.status === 'limited';
    for (const [key, graph] of [['prompt', prompt], ['workflow', workflow]]) {
        const hasRecord = Object.hasOwn(formats, key);
        if ((formats[key] === 'invalid' && !['malformed', 'limited'].includes(graph.status)) ||
            (formats[key] === 'limited' && graph.status !== 'limited') ||
            hasRecord !== (graph.status !== 'absent')) throw new TypeError('Invalid graph representation provenance');
        if (hasRecord && (wholeRawOmitted || (promptGraphOmitted && key === 'workflow')) && graph.status !== 'limited') throw new TypeError('Invalid atomic graph omission');
    }
    if ((value.status === 'limited') !== (value.omitted_reason === 'response-size-limit')) throw new TypeError('Invalid metadata omission status');
    if (['absent', 'unreadable'].includes(value.status) && (Object.keys(raw).length || Object.keys(formats).length || prompt.status !== 'absent' || workflow.status !== 'absent')) throw new TypeError('Invalid unavailable metadata');
    return { status: value.status, omitted_reason: value.omitted_reason, raw, record_formats: formats, prompt_graph: prompt, workflow_graph: workflow, parameters: value.parameters };
}

function resolutionFor(value) {
    if (!object(value) || !['resolved', 'absent', 'ambiguous', 'unresolved'].includes(value.state) ||
        (value.sampler_id !== null && (typeof value.sampler_id !== 'string' || !value.sampler_id)) || typeof value.reason !== 'string') throw new TypeError('Invalid inspection resolution');
    const code = value.reason_code ?? null, node = value.node ?? null;
    // Codes explain a non-resolved outcome; a blocking node is named only with its code.
    if ((code !== null && (!REASON_CODES.includes(code) || ['resolved', 'absent'].includes(value.state))) ||
        (node !== null && (!NAMED_CODES.includes(code) || !object(node) || Object.keys(node).sort().join() !== 'class_type,id' ||
            !boundedText(node.id, NAME_LENGTH) || (node.class_type !== null && !boundedText(node.class_type, NAME_LENGTH))))) throw new TypeError('Invalid inspection resolution reason');
    return value;
}

function libraryFor(value) {
    if (!object(value) || !['linked', 'none', 'ambiguous', 'unavailable', 'limited'].includes(value.status) || value.scope !== 'saved-library' ||
        !Array.isArray(value.tags) || value.tags.some(tag => typeof tag !== 'string') ||
        ![value.category_scope, value.tags_scope].every(scope => [null, 'pair', 'positive'].includes(scope))) throw new TypeError('Invalid inspection library');
    // D39: each field names its own source, so a recipe's category can sit beside its positive's tags.
    const library = { status: value.status, scope: value.scope, tags: [...value.tags], category_scope: value.category_scope, tags_scope: value.tags_scope };
    for (const key of ['positive_id', 'pair_id', 'title', 'category']) {
        const item = value[key] ?? null;
        if (item !== null && (key.endsWith('_id') ? !Number.isSafeInteger(item) || item <= 0 : typeof item !== 'string')) throw new TypeError('Invalid inspection library field');
        library[key] = item;
    }
    return library;
}

// #249 PR C (D88): the file's own current image tags (S1). `none` and `unavailable` carry no names; a name is a
// vocabulary tag name (1–50 characters, trimmed, no comma or control character), at most 100 of them.
const IMAGE_TAG_STATES = ['current', 'none', 'unavailable'], MAX_IMAGE_TAGS = 100, IMAGE_TAG_LENGTH = 50;
const imageTagName = name => typeof name === 'string' && name !== '' && name === name.trim() && [...name].length <= IMAGE_TAG_LENGTH &&
    !/[,\x00-\x1f\x7f]/.test(name);

function imageTagsFor(value) {
    if (value === undefined) return null;
    if (!object(value) || Object.keys(value).sort().join() !== 'state,tags' || !IMAGE_TAG_STATES.includes(value.state) ||
        !Array.isArray(value.tags) || value.tags.length > MAX_IMAGE_TAGS || !value.tags.every(imageTagName) ||
        (value.state !== 'current' && value.tags.length)) throw new TypeError('Invalid inspection image tags');
    return { state: value.state, tags: [...value.tags] };
}

// The legacy workflow parse (comfy-metadata.js, as of #170) also feeds the gallery and
// admin pages; it fills any prompt or setting the server could not decide, so this page
// never shows less than they do. A definite server answer is never replaced.
const LEGACY_FACTS = [['seed', 'seed'], ['steps', 'steps'], ['cfg', 'cfgScale'], ['sampler', 'sampler'], ['model', 'checkpoint']];

function comfyMetadata() {
    return globalThis.ComfyMetadata || globalThis.window?.ComfyMetadata;
}

// #249: the legacy parse follows ConditioningZeroOut back to its input, so it reads the
// positive text as the negative. Only the negative it actually read is checked: a direct
// zero-out shows the server's zeroed state; a zero-out elsewhere on
// that path, or a subgraph instance whose definition holds one, leaves the negative
// unfilled. Any other negative keeps the legacy fill, so this page never shows less.
const ZERO_OUT = 'ConditioningZeroOut';
const ZEROED_NEGATIVE = Object.freeze({ status: 'resolved', text: '', source: 'zeroed-conditioning' });
const MAX_NEGATIVE_VISITS = 2000;
const MODE_MUTED = 2, MODE_BYPASSED = 4;
const isLink = value => Array.isArray(value) && value.length === 2 && ['string', 'number'].includes(typeof value[0]);

/** Subgraph definition IDs that run a zero-out, directly or through a nested instance. */
function zeroOutSubgraphs(workflow) {
    const definitions = Array.isArray(workflow?.definitions?.subgraphs) ? workflow.definitions.subgraphs.filter(object) : [];
    const active = item => Array.isArray(item.nodes) ? item.nodes.filter(node => object(node) && ![MODE_MUTED, MODE_BYPASSED].includes(node.mode)) : [];
    const found = new Set();
    for (let changed = true; changed;) {
        changed = false;
        for (const item of definitions) {
            if (found.has(item.id) || !active(item).some(node => node.type === ZERO_OUT || found.has(node.type))) continue;
            found.add(item.id); changed = true;
        }
    }
    return found;
}

/** 'zeroed', 'undecided' (do not fill), or null (the legacy fill applies). */
function zeroOutNegative(source, workflow) {
    if (!source || !object(source.graph) || !isLink(source.link)) return null;
    const { graph } = source, subgraphs = zeroOutSubgraphs(workflow);
    const zeroing = node => node.class_type === ZERO_OUT || subgraphs.has(node.class_type);
    if (!Object.values(graph).some(node => object(node) && zeroing(node))) return null;
    const node = graph[String(source.link[0])];
    if (!object(node)) return 'undecided';
    if (node.class_type === ZERO_OUT) return 'zeroed';
    // One bounded walk upstream; an index pointer keeps it linear in the nodes visited.
    const queue = [node], seen = new Set();
    for (let index = 0; index < queue.length; index++) {
        const current = queue[index];
        if (zeroing(current) || seen.size >= MAX_NEGATIVE_VISITS) return 'undecided';
        for (const value of object(current.inputs) ? Object.values(current.inputs) : []) {
            const id = isLink(value) ? String(value[0]) : null;
            if (id === null || seen.has(id) || !object(graph[id])) continue;
            seen.add(id); queue.push(graph[id]);
        }
    }
    return null;
}

function withWorkflowParams(record, parser = comfyMetadata()) {
    const graph = record.metadata.prompt_graph.status === 'parsed' ? record.metadata.prompt_graph.value : null;
    const workflow = record.metadata.workflow_graph.status === 'parsed' ? record.metadata.workflow_graph.value : null;
    if (typeof parser?.extractGenerationParams !== 'function' || (!graph && !workflow)) return;
    let params;
    // The browser parser throws on some malformed graphs; then only the server result shows.
    try { params = parser.extractGenerationParams({ prompt: graph, workflow }); } catch { return; }
    const found = key => typeof params[key] === 'string' || typeof params[key] === 'number' ?
        String(params[key]) !== '' && params[key] !== parser.PLACEHOLDERS?.[key] : false;
    const prompts = { ...record.prompts };
    const read = prompts.negative?.status === 'resolved' || typeof parser.legacyNegativeSource !== 'function' ? null :
        parser.legacyNegativeSource({ prompt: graph, workflow });
    const zeroed = zeroOutNegative(read, workflow);
    for (const [role, key] of [['positive', 'positivePrompt'], ['negative', 'negativePrompt']]) {
        const current = prompts[role];
        if (current?.status === 'resolved') continue;
        if (role === 'negative' && zeroed !== null) {
            if (zeroed === 'zeroed') prompts.negative = { ...ZEROED_NEGATIVE };
            continue;
        }
        if (!found(key)) continue;
        prompts[role] = { status: 'resolved', text: params[key], source: 'graph-inferred' };
    }
    record.prompts = prompts;
    for (const [field, key] of LEGACY_FACTS) {
        // Only open answers are filled: a known value or a definite "not recorded" stays.
        // An ambiguous field (e.g. a compare grid's per-branch model) is filled too, on
        // purpose: the legacy pages show this value, and this page never shows less.
        if (!found(key) || !['unresolved', 'ambiguous'].includes(record.generation[field]?.status)) continue;
        record.generation[field] = { status: 'known', value: String(params[key]), source: graph ? 'api-graph' : 'workflow', evidence: 'configuration', reason: null, sampler_id: null };
    }
}

function unavailableFacts() {
    return Object.fromEntries(FACT_FIELDS.map(key => [key, { status: 'unresolved', value: null, source: null, evidence: null, reason: null, sampler_id: null }]));
}

export function toInspectionRecord(result, image, extractFacts = globalThis.ComfyMetadata?.extractGenerationFacts || globalThis.window?.ComfyMetadata?.extractGenerationFacts, parser = comfyMetadata()) {
    const scope = validateIdentity(image);
    if (scope !== null && (!object(result) || result.root_scope !== scope)) throw new InspectionSourceChangedError();
    if (!object(result) || result.success === false) throw new TypeError('Invalid inspection response');
    const legacy = result.inspection_version === undefined;
    if (!legacy && result.inspection_version !== 1) throw new TypeError('Unsupported inspection version');
    if (!legacy && result.success !== true) throw new TypeError('Invalid inspection success status');
    if ((!legacy || result.image !== undefined) && (!object(result.image) || result.image.root_index !== image.root_index || result.image.path !== image.path || result.image.filename !== image.path.split('/').at(-1))) throw new TypeError('Mismatched inspection identity');
    const record = projectInspectionPayload(result,
        { kind: 'rooted-file', key: imageKey(image), root_index: image.root_index, path: image.path, root_scope: scope },
        downloadFor(image, !legacy || scope !== null), extractFacts, legacy, parser);
    record.preview = record.download ? previewFor(image) : null;
    return record;
}

/** Shared bounded payload projection; adapters validate identity before entering here. */
export function projectInspectionPayload(result, identity, download, extractFacts = globalThis.ComfyMetadata?.extractGenerationFacts || globalThis.window?.ComfyMetadata?.extractGenerationFacts, legacy = false, parser = comfyMetadata()) {
    const record = {
        identity, file: null, prompts: promptsFor(result.prompts), library: null, metadata: null,
        generation: unavailableFacts(), historyTarget: null, download, imageTags: null, preview: null,
    };
    if (legacy) return record;
    record.imageTags = imageTagsFor(result.image_tags);
    const resolution = resolutionFor(result.resolution);
    record.file = fileFor(result.file, identity);
    record.metadata = metadataFor(result.metadata);
    record.library = libraryFor(result.library);
    if (typeof extractFacts !== 'function') throw new TypeError('Inspection facts parser unavailable');
    record.generation = extractFacts({ prompt_graph: record.metadata.prompt_graph, workflow_graph: record.metadata.workflow_graph,
        parameters: record.metadata.parameters, recordFormats: record.metadata.record_formats, metadataStatus: record.metadata.status, resolution });
    withWorkflowParams(record, parser);
    if (record.library.status === 'linked' && record.library.positive_id !== null) record.historyTarget = `#/library/${record.library.positive_id}`;
    return record;
}

export const rootedFileAdapter = Object.freeze({
    validate: validateIdentity, key: imageKey, scope: descriptorScope,
    inspectionPath: inspectPath, originalDownload: originalMediaDownload, toRecord: toInspectionRecord,
});

export function createInspectionLoader(api, onChange, { extractFacts, adapter = rootedFileAdapter } = {}) {
    let request = null, disposed = false;
    return {
        /** `quiet`: publish only the answer, so the shown record stays until it lands (a re-read after a write). */
        async load(image, { quiet = false } = {}) {
            if (disposed) return;
            request?.abort();
            const current = request = new AbortController();
            if (!quiet) onChange({ loading: true });
            try {
                // An adapter that reads by itself (a file chosen in this browser) never reaches the API.
                const result = adapter.read ? await adapter.read(image, { signal: current.signal }) :
                    await api.get(adapter.inspectionPath(image), { signal: current.signal });
                if (disposed || request !== current) return;
                const record = adapter.toRecord(result, image, extractFacts);
                onChange({ prompts: record.prompts, record });
            } catch (error) {
                if (disposed || request !== current) return;
                if (error?.status === 409 && error.code === 'source-changed') error = new InspectionSourceChangedError();
                const historyRecovery = adapter !== rootedFileAdapter && error?.status === 409 && ['history-changed', 'candidate-stale', 'capture-changed'].includes(error.code);
                const refreshRequired = error instanceof InspectionSourceChangedError || error?.refreshRequired === true || historyRecovery;
                onChange({ error: true, refreshRequired, code: error?.code ?? null, recovery: error?.recovery ?? null,
                    message: error instanceof InspectionSourceChangedError ? SOURCE_CHANGED : refreshRequired ?
                        error.code === 'history-changed' ? 'Recorded history changed. Reload recorded history.' : 'Current output locations changed. Reload reported outputs.' : 'Could not read details from this file.' });
            }
        },
        dispose() { disposed = true; request?.abort(); request = null; },
    };
}

const emptyState = () => ({ loading: false, error: false, deferred: false, record: null, prompts: undefined, message: '', refreshRequired: false, code: null, recovery: null });

/**
 * One selection's inspection. #249 System S3 (Appearance): `autoLoad: false` ("Load metadata on selection" off) makes a
 * selection publish `deferred` instead of reading, and `load()` reads it once on request; `cache: false` ("Cache
 * metadata in this browser" off) re-reads the selected file when it is selected again, where the default reuses the one
 * current record. A read in flight is shared either way, and a file whose source changed is never re-read by selecting
 * it again (as `retry()` and `refresh()` refuse).
 */
export function createInspectionController({ api, extractFacts, adapter = rootedFileAdapter, autoLoad = true, cache = true }) {
    let state = Object.freeze(emptyState()), current = null, pending = null, disposed = false;
    let attempted, hasAttempt = false;
    const listeners = new Set();
    function publish(update) {
        if (disposed) return;
        state = Object.freeze({ ...emptyState(), ...update });
        for (const listener of listeners) listener(state);
    }
    const makeLoader = () => createInspectionLoader(api, publish, { extractFacts, adapter });
    let loader = makeLoader();
    function read(image, options) {
        pending = loader.load(image, options);
        return pending;
    }
    return {
        get state() { return state; },
        select(image) {
            if (disposed) return;
            let selected = null;
            try {
                adapter.validate(image);
                const scope = adapter.scope(image), key = adapter.key(image), path = adapter.inspectionPath(image);
                if (current?.key === key && current.scope === scope && current.path === path && (cache || state.loading || state.deferred || state.refreshRequired)) return pending;
                selected = { key, scope, path };
            } catch {
                // The loader publishes validation errors after invalidating the previous source.
            }
            loader.dispose(); loader = makeLoader(); current = selected;
            attempted = object(image) ? { ...image, ...(adapter !== rootedFileAdapter ? {occurrence:{...image.occurrence}, reference:{...image.reference}} : {}) } : image; hasAttempt = true;
            if (!autoLoad && selected) { pending = null; publish({ deferred: true }); return undefined; }
            return read(attempted);
        },
        /** Read a deferred selection (the inspector's "Load metadata"); a read already made or running is reused. */
        load() {
            if (disposed || !hasAttempt || !state.deferred) return pending ?? undefined;
            return read(attempted);
        },
        retry() {
            if (disposed || !hasAttempt || state.refreshRequired) return;
            return read(attempted);
        },
        /** Re-read the selected file keeping the shown record until the answer (a retry when none is shown). */
        refresh() {
            // A deferred selection loads only through `load()` (the user's "Load metadata").
            if (disposed || !hasAttempt || state.refreshRequired || state.deferred) return;
            return read(attempted, { quiet: Boolean(state.record) && !state.loading && !state.error });
        },
        clear() {
            if (disposed) return;
            loader.dispose(); loader = makeLoader(); current = null; pending = null; attempted = undefined; hasAttempt = false;
            publish({});
        },
        subscribe(listener) {
            if (disposed) return () => {};
            listeners.add(listener); listener(state);
            return () => listeners.delete(listener);
        },
        dispose() {
            if (disposed) return;
            disposed = true; loader.dispose(); listeners.clear(); current = null; pending = null; attempted = undefined; hasAttempt = false; state = Object.freeze(emptyState());
        },
    };
}
