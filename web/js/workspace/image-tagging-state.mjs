/**
 * Tag image dialog state: observe, staged suggestions and byte-exact saves (#249 C1).
 *
 * UI-agnostic. A dialog renders `controller.state` (a frozen snapshot replaced
 * on every change) and calls its methods. open() captures the file and reads its
 * saved tags; generate() stages the top suggestions as removable chips, written
 * only by save(). Every read is guarded by a serial: a model change, a reopen,
 * cancel() or dispose() supersedes older requests, so a late answer can never
 * stage chips or supply a token. Writes are sent once, without an abort signal
 * (aborting cannot undo a write); a lost answer is resent only by retry(), with
 * the identical body (the server answers a duplicate as `replayed`).
 */
import {noun} from './plural.mjs';

export const MODEL_OPTIONS = Object.freeze([
    Object.freeze({id: 'wd14-swinv2', label: 'WD14 SwinV2 · ~400 MB', classifier: true}),
    Object.freeze({id: 'wd14-vit', label: 'WD14 ViT · ~400 MB', classifier: true}),
    Object.freeze({id: 'gguf', label: 'GGUF JoyCaption (Recommended) · ~4.5 GB', classifier: false}),
    Object.freeze({id: 'hf', label: 'HuggingFace JoyCaption · ~16 GB', classifier: false}),
]);

const OBSERVE_URL = '/prompt_manager/image-annotations/observe';
const SUGGEST_URL = '/prompt_manager/image-annotations/suggest';
const TAGS_URL = '/prompt_manager/image-annotations/tags';
const CAPTURE_URL = '/prompt_manager/image-annotations/capture/';
const MODELS_URL = '/prompt_manager/autotag/models';
const SOURCES = new Set(['images', 'library', 'review', 'tags', 'bulk']);
const OPTION_KEYS = new Set(['general_threshold', 'character_threshold', 'instructions', 'suggestion_limit']);
const DEFAULTS = Object.freeze({general_threshold: 0.35, character_threshold: 0.85, instructions: '', suggestion_limit: 10});
const MAX_SUGGESTIONS = 100;
const MAX_INSTRUCTION_CHARS = 4000;
const EDITABLE = new Set(['ready', 'generate-error', 'write-error', 'conflict']);
const RETRYABLE = new Set(['observe-error', 'generate-error', 'unknown-outcome']);
// Answers after which only a fresh observation can save (the capture or epoch is gone).
const REOPEN = {'storage-changed': 'storage-changed', 'file-changed': 'file-changed', 'source-changed': 'file-changed', 'capture-expired': 'capture-expired'};
// Generic failure envelopes whose fixed sentence the dialog words itself.
const GENERIC_CODES = new Set(['observe-failed', 'suggest-failed', 'save-failed', 'capture-error']);
// A proxy timeout or bad gateway cannot tell whether the server committed the write.
const UNCONFIRMED_STATUS = new Set([502, 503, 504]);

const COPY = Object.freeze({
    initial: 'Choose a model, then generate tags. Remove anything you do not want to add.', // D60
    modelChanged: 'Generate suggestions with the selected model before approving.',
    allRemoved: 'All suggestions removed. Generate again or enter tags manually.', // D60
    empty: 'No suggestions met this threshold. Adjust it or enter tags manually.',
    generating: 'Generating suggestions…',
    observeFailed: 'The image could not be opened for tagging.',
    generateFailed: 'Auto-tagging failed. Check server logs for details.',
    saveFailed: 'The tags could not be saved.',
    unknown: 'The save could not be confirmed. Retry sends the same tags again.',
    reviewSuffix: ' Prompt association still awaits review.',
});

const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const uuid = value => typeof value === 'string' && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(value);
const hex64 = value => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const captureToken = value => typeof value === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(value);
const count = value => Number.isSafeInteger(value) && value >= 0;
const dimension = value => value === null || (Number.isSafeInteger(value) && value > 0);
const strings = value => Array.isArray(value) && value.every(item => typeof item === 'string');
const unit = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1;
const limit = value => Number.isInteger(value) && value >= 1 && value <= MAX_SUGGESTIONS;

function requireValue(valid, message) {if (!valid) throw new TypeError(message);}
function freeze(value) {
    if (value && typeof value === 'object' && !Object.isFrozen(value)) {Object.values(value).forEach(freeze); Object.freeze(value);}
    return value;
}
// Duck-typed by name: this ES module cannot import the UMD api-client.js.
function isApiError(error) {return error instanceof Error && error.name === 'ApiError' && Number.isInteger(error.status);}
function messageOf(error, fallback) {
    return isApiError(error) && error.code && !GENERIC_CODES.has(error.code) && error.message ? error.message : fallback;
}
const modelOf = id => MODEL_OPTIONS.find(option => option.id === id);
// An input's value arrives as text; blank text is not zero.
const numberOf = value => (typeof value === 'string' ? (value.trim() === '' ? NaN : Number(value)) : value);

/** Trimmed, non-blank names with case-insensitive repeats dropped; the first spelling and position win. */
export function dedupeTags(names) {
    const seen = new Set();
    const result = [];
    for (const value of Array.isArray(names) ? names : []) {
        if (typeof value !== 'string') continue;
        const name = value.trim();
        const key = name.toLowerCase();
        if (!name || seen.has(key)) continue;
        seen.add(key);
        result.push(name);
    }
    return result;
}

/** The manual field's comma-separated names. */
export function parseManualTags(text) {
    return dedupeTags(typeof text === 'string' ? text.split(',') : []);
}

/** `null` when *options* are valid for *model*, else `{field, message}` (nothing is sent). */
export function optionError(model, options) {
    const option = modelOf(model);
    requireValue(option, 'Unknown tagging model');
    if (option.classifier) {
        for (const field of ['general_threshold', 'character_threshold']) {
            if (!unit(numberOf(options[field]))) return {field, message: 'Enter a confidence from 0 to 1.'};
        }
    } else {
        const text = options.instructions;
        if (typeof text !== 'string' || !text.trim() || text.length > MAX_INSTRUCTION_CHARS) {
            return {field: 'instructions', message: `Enter tagging instructions of at most ${MAX_INSTRUCTION_CHARS} characters.`};
        }
    }
    if (!limit(numberOf(options.suggestion_limit))) {
        return {field: 'suggestion_limit', message: `Enter a whole number of suggestions from 1 to ${MAX_SUGGESTIONS}.`};
    }
    return null;
}

function requestOptions(model, options) {
    const body = {suggestion_limit: numberOf(options.suggestion_limit)};
    if (modelOf(model).classifier) {
        return {general_threshold: numberOf(options.general_threshold), character_threshold: numberOf(options.character_threshold), ...body};
    }
    return {instructions: options.instructions, ...body};
}

function validateTarget(target) {
    requireValue(object(target) && Number.isSafeInteger(target.root_index) && target.root_index >= 0 &&
        hex64(target.root_scope) && typeof target.path === 'string' && target.path.length > 0 && target.path.length <= 1024,
    'Invalid image target');
    return Object.freeze({root_index: target.root_index, root_scope: target.root_scope, path: target.path});
}

function validateObserved(value) {
    requireValue(object(value) && value.success === true && uuid(value.library_epoch) && object(value.capture), 'Invalid observation');
    const capture = value.capture;
    requireValue(captureToken(capture.token) && hex64(capture.sha256) && typeof capture.preview_url === 'string' &&
        capture.preview_url.startsWith(CAPTURE_URL) && dimension(capture.width) && dimension(capture.height) &&
        ['image', 'video'].includes(capture.media_type) && object(capture.generate) &&
        typeof capture.generate.supported === 'boolean' &&
        (capture.generate.reason === null || typeof capture.generate.reason === 'string'), 'Invalid capture');
    requireValue(typeof value.filename === 'string' && (value.title === null || typeof value.title === 'string') &&
        strings(value.tags) && count(value.tag_revision), 'Invalid observation');
    return value;
}

function validateSuggestions(value, cap) {
    requireValue(object(value) && value.success === true && Array.isArray(value.suggestions), 'Invalid suggestions');
    const seen = new Set();
    const rows = [];
    for (const row of value.suggestions) {
        requireValue(object(row) && typeof row.name === 'string' && (row.confidence === null || unit(row.confidence)), 'Invalid suggestion');
        const name = row.name.trim();
        const key = name.toLowerCase();
        if (!name || seen.has(key)) continue;
        seen.add(key);
        const label = row.confidence === null ? 'Suggested' : `${Math.round(row.confidence * 100)}%`;
        rows.push({name, confidence: row.confidence, label});
    }
    return rows.slice(0, cap);
}

function validateReceipt(value) {
    requireValue(object(value) && value.success === true && strings(value.tags) && count(value.tag_revision), 'Invalid tag receipt');
    return value;
}

/** Thresholds, instructions and suggestion count from the server, else the documented defaults. */
async function readDefaults(api, signal) {
    try {
        const value = await api.get(MODELS_URL, {signal, cache: 'no-store'});
        if (!object(value)) return DEFAULTS;
        return {
            general_threshold: unit(value.wd14_general_threshold) ? value.wd14_general_threshold : DEFAULTS.general_threshold,
            character_threshold: unit(value.wd14_character_threshold) ? value.wd14_character_threshold : DEFAULTS.character_threshold,
            instructions: typeof value.default_prompt === 'string' ? value.default_prompt : DEFAULTS.instructions,
            suggestion_limit: limit(value.suggestion_limit) ? value.suggestion_limit : DEFAULTS.suggestion_limit,
        };
    } catch {
        // The defaults keep the dialog usable; Generate reports a real failure itself.
        return DEFAULTS;
    }
}

function initialState(source) {
    return {
        phase: 'observing', closed: false, source, showScopeNote: source === 'review',
        image: null, savedTags: [], tagRevision: 0, currentTags: null, manual: '',
        model: MODEL_OPTIONS[0].id, options: {...DEFAULTS}, generated: false, suggestions: [],
        suggestionsText: COPY.initial, statusText: '', generateSupported: false, generateReason: null, toast: null,
    };
}

/** Derived labels and permissions; never stored independently of the fields they follow. */
function derive(state) {
    const editable = EDITABLE.has(state.phase);
    return {
        ...state,
        primaryLabel: state.generated ? 'Approve selected tags' : 'Save manual tags',
        countText: state.generated ? `${state.suggestions.length} ${noun(state.suggestions.length, 'suggestion')}` : 'Not generated',
        canSave: editable,
        canGenerate: editable && state.generateSupported,
        canRetry: RETRYABLE.has(state.phase),
    };
}

export function createImageTaggingController({api, target, source = 'images'} = {}) {
    requireValue(object(api) && ['get', 'post', 'put', 'del'].every(name => typeof api[name] === 'function'), 'An api client is required');
    const imageTarget = validateTarget(target);
    requireValue(SOURCES.has(source), 'Unknown tagging source');

    let state = freeze(derive(initialState(source)));
    const listeners = new Set();
    let closed = false;
    let capture = null;
    let epoch = null;
    let defaults = DEFAULTS;
    let openSerial = 0;
    let generateSerial = 0;
    let saveSerial = 0;
    let readAbort = null;
    let generateAbort = null;
    let lastBody = null;

    function set(patch) {
        if (closed) return;
        state = freeze(derive({...state, ...patch}));
        for (const listener of [...listeners]) listener(state);
    }

    function releaseToken(token) {
        // Best effort: an unreleased capture expires on the server's idle timeout.
        api.del(CAPTURE_URL + encodeURIComponent(token)).catch(() => {});
    }
    function releaseCapture() {
        if (capture) releaseToken(capture.token);
        capture = null;
    }
    function stopGenerating() {
        generateSerial += 1;
        generateAbort?.abort();
        generateAbort = null;
    }
    function reasonText() {
        return state.generateSupported ? '' : (state.generateReason || '');
    }

    async function open() {
        if (closed) return;
        const serial = ++openSerial;
        stopGenerating();
        releaseCapture();
        readAbort?.abort();
        readAbort = new AbortController();
        const defaultsRead = readDefaults(api, readAbort.signal);
        lastBody = null;
        set({phase: 'observing', statusText: '', toast: null, currentTags: null, generated: false, suggestions: [], suggestionsText: COPY.initial});
        let value;
        try {
            const answer = await api.post(OBSERVE_URL, {target: {...imageTarget}});
            if (closed || serial !== openSerial) {
                if (captureToken(answer?.capture?.token)) releaseToken(answer.capture.token);
                return;
            }
            try {
                value = validateObserved(answer);
            } catch (error) {
                if (captureToken(answer?.capture?.token)) releaseToken(answer.capture.token);
                throw error;
            }
        } catch (error) {
            if (closed || serial !== openSerial) return;
            set({phase: 'observe-error', statusText: messageOf(error, COPY.observeFailed)});
            return;
        }
        capture = {token: value.capture.token, sha256: value.capture.sha256};
        epoch = value.library_epoch;
        defaults = await defaultsRead;
        if (closed || serial !== openSerial) return;
        const {width, height, media_type: mediaType, preview_url: previewUrl, generate} = value.capture;
        set({
            phase: 'ready',
            image: {filename: value.filename, title: value.title, width, height, mediaType, previewUrl,
                dimensions: width && height ? `${width} × ${height}` : null},
            savedTags: value.tags.slice(), tagRevision: value.tag_revision, manual: value.tags.join(', '),
            options: {...defaults}, generateSupported: generate.supported, generateReason: generate.reason,
            statusText: generate.supported ? '' : (generate.reason || ''),
        });
    }

    function setManual(text) {
        set({manual: typeof text === 'string' ? text : ''});
    }

    function setModel(id) {
        requireValue(modelOf(id), 'Unknown tagging model');
        if (closed || id === state.model) return;
        stopGenerating();
        const phase = state.phase === 'generating' || state.phase === 'generate-error' ? 'ready' : state.phase;
        set({
            phase, model: id, generated: false, suggestions: [], suggestionsText: COPY.modelChanged, statusText: reasonText(),
            // Like the mock, a model change re-renders its fields with the defaults; the count is kept (D68).
            options: {...state.options, general_threshold: defaults.general_threshold,
                character_threshold: defaults.character_threshold, instructions: defaults.instructions},
        });
    }

    function setOption(key, value) {
        requireValue(OPTION_KEYS.has(key), 'Unknown tagging option');
        set({options: {...state.options, [key]: value}});
    }

    async function generate() {
        if (closed || !state.canGenerate || optionError(state.model, state.options)) return false;
        stopGenerating();
        const serial = generateSerial;
        const controller = new AbortController();
        generateAbort = controller;
        const model = state.model;
        const options = requestOptions(model, state.options);
        const stale = () => closed || serial !== generateSerial;
        set({phase: 'generating', statusText: COPY.generating});
        let rows;
        try {
            const value = await api.post(SUGGEST_URL, {
                target: {...imageTarget}, capture_token: capture.token, expected_epoch: epoch, model_type: model, options,
            }, {signal: controller.signal});
            if (stale()) return false;
            rows = validateSuggestions(value, options.suggestion_limit);
        } catch (error) {
            if (stale()) return false;
            const phase = isApiError(error) && REOPEN[error.code];
            set(phase ? {phase, statusText: error.message} : {phase: 'generate-error', statusText: messageOf(error, COPY.generateFailed)});
            return false;
        }
        generateAbort = null;
        set({
            phase: 'ready', generated: true, suggestions: rows, suggestionsText: rows.length ? '' : COPY.empty,
            statusText: `Suggestions from ${modelOf(model).label}. Approve tags to save.`,
        });
        return true;
    }

    function remove(name) {
        const suggestions = state.suggestions.filter(row => row.name !== name);
        if (closed || suggestions.length === state.suggestions.length) return;
        set({suggestions, suggestionsText: suggestions.length ? '' : COPY.allRemoved});
    }

    async function send(body) {
        const serial = ++saveSerial;
        lastBody = body;
        set({phase: 'saving', statusText: ''});
        let value;
        try {
            value = validateReceipt(await api.put(TAGS_URL, body));
        } catch (error) {
            if (closed || serial !== saveSerial) return false;
            failSave(error);
            return false;
        }
        if (closed || serial !== saveSerial) return false;
        lastBody = null;
        const n = value.tags.length;
        set({
            phase: 'saved', savedTags: value.tags.slice(), tagRevision: value.tag_revision, currentTags: null, statusText: '',
            toast: `${n} ${noun(n, 'tag')} saved for ${state.image.filename}.${source === 'review' ? COPY.reviewSuffix : ''}`,
        });
        return true;
    }

    function failSave(error) {
        if (!isApiError(error) || error.status < 400 || UNCONFIRMED_STATUS.has(error.status)) {
            set({phase: 'unknown-outcome', statusText: COPY.unknown});
            return;
        }
        lastBody = null;
        if (error.code === 'tag-conflict') {
            // The user reviews the current set; a second Save is against the revision now shown.
            const current = error.current;
            set({phase: 'conflict', statusText: error.message, currentTags: current ? current.tags.slice() : null,
                ...(current ? {tagRevision: current.tag_revision} : {})});
            return;
        }
        if (REOPEN[error.code]) {
            set({phase: REOPEN[error.code], statusText: error.message});
            return;
        }
        set({phase: 'write-error', statusText: messageOf(error, COPY.saveFailed)});
    }

    async function save() {
        if (closed || !state.canSave) return false;
        const tags = dedupeTags([...parseManualTags(state.manual), ...state.suggestions.map(row => row.name)]);
        return send({
            target: {...imageTarget}, capture_token: capture.token, sha256: capture.sha256,
            expected_epoch: epoch, expected_tag_revision: state.tagRevision, tags,
        });
    }

    async function retry() {
        if (closed || !state.canRetry) return false;
        if (state.phase === 'observe-error') return open();
        if (state.phase === 'generate-error') return generate();
        return send(lastBody);
    }

    function cancel() {
        if (closed) return;
        set({closed: true});
        closed = true;
        openSerial += 1;
        saveSerial += 1;
        stopGenerating();
        readAbort?.abort();
        readAbort = null;
        releaseCapture();
    }

    function dispose() {
        listeners.clear();
        cancel();
    }

    function subscribe(listener) {
        requireValue(typeof listener === 'function', 'A listener function is required');
        listeners.add(listener);
        return () => listeners.delete(listener);
    }

    return {
        get state() {return state;},
        subscribe, open, setManual, setModel, setOption, generate, remove, save, retry, cancel, dispose,
    };
}
