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
        const expected = new URL(resolveUrl(`/prompt_manager/images/serve/${image.path.split('/').map(encodeURIComponent).join('/')}`), reference);
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
            (record.status === 'resolved' && !Object.hasOwn(confirmed, role))) throw new TypeError('Invalid inspection role');
    }
    return value;
}

function fileFor(value) {
    if (!object(value) || value.observation !== 'current-file' || typeof value.source_label !== 'string' ||
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
        (node !== null && (code === null || !object(node) || typeof node.id !== 'string' || (node.class_type !== null && typeof node.class_type !== 'string')))) throw new TypeError('Invalid inspection resolution reason');
    return value;
}

function libraryFor(value) {
    if (!object(value) || !['linked', 'none', 'ambiguous', 'unavailable', 'limited'].includes(value.status) || value.scope !== 'saved-library' ||
        !Array.isArray(value.tags) || value.tags.some(tag => typeof tag !== 'string') || ![null, 'pair', 'positive'].includes(value.metadata_scope)) throw new TypeError('Invalid inspection library');
    const library = { status: value.status, scope: value.scope, tags: [...value.tags], metadata_scope: value.metadata_scope };
    for (const key of ['positive_id', 'pair_id', 'title', 'category']) {
        const item = value[key] ?? null;
        if (item !== null && (key.endsWith('_id') ? !Number.isSafeInteger(item) || item <= 0 : typeof item !== 'string')) throw new TypeError('Invalid inspection library field');
        library[key] = item;
    }
    return library;
}

function unavailableFacts() {
    return Object.fromEntries(FACT_FIELDS.map(key => [key, { status: 'unresolved', value: null, source: null, evidence: null, reason: null, sampler_id: null }]));
}

export function toInspectionRecord(result, image, extractFacts = globalThis.ComfyMetadata?.extractGenerationFacts || globalThis.window?.ComfyMetadata?.extractGenerationFacts) {
    const scope = validateIdentity(image);
    if (scope !== null && (!object(result) || result.root_scope !== scope)) throw new InspectionSourceChangedError();
    if (!object(result) || result.success === false) throw new TypeError('Invalid inspection response');
    const legacy = result.inspection_version === undefined;
    if (!legacy && result.inspection_version !== 1) throw new TypeError('Unsupported inspection version');
    if (!legacy && result.success !== true) throw new TypeError('Invalid inspection success status');
    if ((!legacy || result.image !== undefined) && (!object(result.image) || result.image.root_index !== image.root_index || result.image.path !== image.path || result.image.filename !== image.path.split('/').at(-1))) throw new TypeError('Mismatched inspection identity');
    return projectInspectionPayload(result,
        { kind: 'rooted-file', key: imageKey(image), root_index: image.root_index, path: image.path, root_scope: scope },
        downloadFor(image, !legacy || scope !== null), extractFacts, legacy);
}

/** Shared bounded payload projection; adapters validate identity before entering here. */
export function projectInspectionPayload(result, identity, download, extractFacts = globalThis.ComfyMetadata?.extractGenerationFacts || globalThis.window?.ComfyMetadata?.extractGenerationFacts, legacy = false) {
    const record = {
        identity, file: null, prompts: promptsFor(result.prompts), library: null, metadata: null,
        generation: unavailableFacts(), historyTarget: null, download,
    };
    if (legacy) return record;
    const resolution = resolutionFor(result.resolution);
    record.file = fileFor(result.file);
    record.metadata = metadataFor(result.metadata);
    record.library = libraryFor(result.library);
    if (typeof extractFacts !== 'function') throw new TypeError('Inspection facts parser unavailable');
    record.generation = extractFacts({ prompt_graph: record.metadata.prompt_graph, workflow_graph: record.metadata.workflow_graph,
        parameters: record.metadata.parameters, recordFormats: record.metadata.record_formats, metadataStatus: record.metadata.status, resolution });
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
        async load(image) {
            if (disposed) return;
            request?.abort();
            const current = request = new AbortController();
            onChange({ loading: true });
            try {
                const result = await api.get(adapter.inspectionPath(image), { signal: current.signal });
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

const emptyState = () => ({ loading: false, error: false, record: null, prompts: undefined, message: '', refreshRequired: false, code: null, recovery: null });

export function createInspectionController({ api, extractFacts, adapter = rootedFileAdapter }) {
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
    function read(image) {
        pending = loader.load(image);
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
                if (current?.key === key && current.scope === scope && current.path === path) return pending;
                selected = { key, scope, path };
            } catch {
                // The loader publishes validation errors after invalidating the previous source.
            }
            loader.dispose(); loader = makeLoader(); current = selected;
            attempted = object(image) ? { ...image, ...(adapter !== rootedFileAdapter ? {occurrence:{...image.occurrence}, reference:{...image.reference}} : {}) } : image; hasAttempt = true;
            return read(attempted);
        },
        retry() {
            if (disposed || !hasAttempt || state.refreshRequired) return;
            return read(attempted);
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
