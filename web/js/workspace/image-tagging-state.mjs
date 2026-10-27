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
 * the identical body (the server answers a duplicate as `replayed`). A save in
 * flight when the dialog closes still settles: the capture is released after it,
 * and `onLateSave(outcome)` reports what happened. Writes and the capture release
 * are sent with `keepalive`, so they finish while the page unloads.
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
// Answers after which only a fresh observation can save (the capture or epoch is gone); Retry observes again (D116).
const REOPEN = {'storage-changed': 'storage-changed', 'file-changed': 'file-changed', 'source-changed': 'file-changed', 'capture-expired': 'capture-expired'};
const REOBSERVE = new Set(Object.values(REOPEN));
const RETRYABLE = new Set(['observe-error', 'generate-error', 'unknown-outcome', ...REOBSERVE]);
// Generic failure envelopes whose fixed sentence the dialog words itself.
const GENERIC_CODES = new Set(['observe-failed', 'suggest-failed', 'save-failed', 'capture-error']);
// A proxy timeout or bad gateway cannot tell whether the server committed the write.
const UNCONFIRMED_STATUS = new Set([502, 503, 504]);

const COPY = Object.freeze({
    // The concept's on-screen opening sentence (renderTagModelOptions a1062 replaces its markup's at once).
    modelChanged: 'Generate suggestions with the selected model before approving.',
    chipHint: 'Remove anything you do not want to add.', // D83
    changedElsewhere: 'The tags of this image changed since the dialog opened.',
    allRemoved: 'All suggestions removed. Generate again or enter tags manually.', // D83
    empty: 'No suggestions met this threshold. Adjust it or enter tags manually.',
    generating: 'Generating suggestions…',
    observeFailed: 'The image could not be opened for tagging.',
    generateFailed: 'Auto-tagging failed. Check server logs for details.',
    saveFailed: 'The tags could not be saved.',
    unknown: 'The save could not be confirmed. Retry sends the same tags again.',
    unknownClosed: 'The tag save could not be confirmed. Open Tag image again to check the saved tags.',
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

/** The server's current set ahead of the user's draft: what the dialog shows after another writer saved. */
function mergeCurrent(current, draft) {
    return dedupeTags([...current, ...parseManualTags(draft)]).join(', ');
}
const savedNow = tags => `Saved now: ${tags.length ? tags.join(', ') : 'no tags'}.`;

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

/** The options a request sends for `model`: numbers parsed, only the model's own keys. */
export function requestOptions(model, options) {
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

/**
 * Thresholds, instructions and suggestion count from the server, else the documented defaults, and the models'
 * download status (`models`, null when unknown).
 */
async function readDefaults(api, signal) {
    try {
        const value = await api.get(MODELS_URL, {signal, cache: 'no-store'});
        if (!object(value)) return {defaults: DEFAULTS, models: null};
        return {
            defaults: {
                general_threshold: unit(value.wd14_general_threshold) ? value.wd14_general_threshold : DEFAULTS.general_threshold,
                character_threshold: unit(value.wd14_character_threshold) ? value.wd14_character_threshold : DEFAULTS.character_threshold,
                instructions: typeof value.default_prompt === 'string' ? value.default_prompt : DEFAULTS.instructions,
                suggestion_limit: limit(value.suggestion_limit) ? value.suggestion_limit : DEFAULTS.suggestion_limit,
            },
            models: object(value.models) ? value.models : null,
        };
    } catch {
        // The defaults keep the dialog usable; Generate reports a real failure itself.
        return {defaults: DEFAULTS, models: null};
    }
}

export const UNAVAILABLE_HINT = 'Download it in System › Auto-tagging.';
/**
 * Why `model` cannot run, from the `/autotag/models` status (the server's own `model-unavailable` sentence), else ''
 * (downloaded, or the status is unknown: the job then reports a missing model itself).
 */
export function modelUnavailable(models, model) {
    const option = modelOf(model);
    requireValue(option, 'Unknown tagging model');
    const status = object(models) && object(models[model]) ? models[model] : null;
    if (!status || status.downloaded !== false) return '';
    const name = typeof status.name === 'string' && status.name.trim() ? status.name : option.label.split(' · ')[0];
    return `${name} is not downloaded. ${UNAVAILABLE_HINT}`;
}

function initialState(source, preset) {
    return {
        phase: 'observing', closed: false, source, showScopeNote: source === 'review',
        image: null, savedTags: [], tagRevision: 0, currentTags: null, manual: '',
        model: preset?.model ?? MODEL_OPTIONS[0].id, options: {...DEFAULTS, ...preset?.options}, generated: false, suggestions: [],
        suggestionsText: COPY.modelChanged, statusText: '', generateSupported: false, generateReason: null, toast: null,
        // Bumped whenever the controller itself rewrites the option fields or the manual draft, never while typing.
        optionsRevision: 0, draftRevision: 0,
    };
}

/** Derived labels and permissions; never stored independently of the fields they follow. */
function derive(state) {
    const editable = EDITABLE.has(state.phase);
    return {
        ...state,
        primaryLabel: state.generated ? 'Approve selected tags' : 'Save manual tags',
        countText: state.generated ? `${state.suggestions.length} ${noun(state.suggestions.length, 'suggestion')}` : 'Not generated',
        chipHint: state.suggestions.length ? COPY.chipHint : '',
        canSave: editable,
        canGenerate: editable && state.generateSupported,
        canRetry: RETRYABLE.has(state.phase),
    };
}

/** A `{model, options}` preset (the bulk review's choice) when valid, else a TypeError. */
function validatePreset(preset) {
    if (preset === null || preset === undefined) return null;
    requireValue(object(preset) && modelOf(preset.model) && object(preset.options), 'Invalid tagging preset');
    const options = {};
    for (const [key, value] of Object.entries(preset.options)) {
        requireValue(OPTION_KEYS.has(key), 'Unknown tagging option');
        options[key] = value;
    }
    requireValue(!optionError(preset.model, {...DEFAULTS, ...options}), 'Invalid tagging preset');
    return Object.freeze({model: preset.model, options: Object.freeze(options)});
}

/**
 * `preset` ({model, options}, D90's review session) starts the dialog on that model, and every observation applies
 * those options over the server's defaults.
 */
export function createImageTaggingController({api, target, source = 'images', onLateSave = () => {}, preset = null} = {}) {
    requireValue(object(api) && ['get', 'post', 'put', 'del'].every(name => typeof api[name] === 'function'), 'An api client is required');
    const imageTarget = validateTarget(target);
    requireValue(SOURCES.has(source), 'Unknown tagging source');
    requireValue(typeof onLateSave === 'function', 'onLateSave must be a function');
    const chosen = validatePreset(preset);

    let state = freeze(derive(initialState(source, chosen)));
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
    // A save in flight: closing hands the capture release to its completion.
    let saving = false;
    let observedOnce = false;

    function set(patch) {
        if (closed) return;
        state = freeze(derive({...state, ...patch}));
        for (const listener of [...listeners]) listener(state);
    }

    function releaseToken(token) {
        // Best effort: an unreleased capture expires on the server's idle timeout.
        api.del(CAPTURE_URL + encodeURIComponent(token), {keepalive: true}).catch(() => {});
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

    /** Observe the file; `keepDraft` (a Retry after a changed file, storage or capture) keeps the typed draft. */
    async function open({keepDraft = false} = {}) {
        if (closed) return;
        const serial = ++openSerial;
        const draft = keepDraft ? state.manual : null;
        const seenRevision = state.tagRevision;
        stopGenerating();
        releaseCapture();
        readAbort?.abort();
        readAbort = new AbortController();
        const defaultsRead = readDefaults(api, readAbort.signal);
        lastBody = null;
        set({phase: 'observing', statusText: '', toast: null, currentTags: null, generated: false, suggestions: [], suggestionsText: COPY.modelChanged});
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
        ({defaults} = await defaultsRead);
        if (closed || serial !== openSerial) return;
        observedOnce = true;
        const {width, height, media_type: mediaType, preview_url: previewUrl, generate} = value.capture;
        // A kept draft stays as typed unless another writer changed the set meanwhile: then the set leads it (I2).
        const changed = draft !== null && value.tag_revision !== seenRevision;
        const reason = generate.supported ? '' : (generate.reason || '');
        set({
            phase: 'ready',
            image: {filename: value.filename, title: value.title, width, height, mediaType, previewUrl,
                dimensions: width && height ? `${width} × ${height}` : null},
            savedTags: value.tags.slice(), tagRevision: value.tag_revision,
            manual: draft === null ? value.tags.join(', ') : changed ? mergeCurrent(value.tags, draft) : draft,
            draftRevision: state.draftRevision + 1,
            options: {...defaults, ...chosen?.options}, optionsRevision: state.optionsRevision + 1,
            generateSupported: generate.supported, generateReason: generate.reason,
            statusText: changed ? `${COPY.changedElsewhere} ${savedNow(value.tags)}${reason ? ` ${reason}` : ''}` : reason,
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
            optionsRevision: state.optionsRevision + 1,
            // Like the mock, a model change re-renders its fields with the defaults; the count is kept (D91).
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
        saving = true;
        set({phase: 'saving', statusText: ''});
        let value = null, failure = null;
        try {
            value = validateReceipt(await api.put(TAGS_URL, body, {keepalive: true}));
        } catch (error) {
            failure = error;
        } finally {
            saving = false;
        }
        if (serial !== saveSerial) return false;
        if (closed) return settleClosed(value, failure);
        if (failure) {
            set(failedSave(failure));
            return false;
        }
        lastBody = null;
        set({phase: 'saved', savedTags: value.tags.slice(), tagRevision: value.tag_revision, currentTags: null, statusText: '',
            toast: toastOf(value)});
        return true;
    }
    // The mock's toast verbatim (app.js:1545, without the demo's "in this tab"): "tags" even for one.
    function toastOf(value) {
        return `${value.tags.length} tags saved for ${state.image.filename}.${source === 'review' ? COPY.reviewSuffix : ''}`;
    }
    /** The dialog closed while its save was in flight: release the capture now, then report the outcome. */
    function settleClosed(value, failure) {
        releaseCapture();
        if (!failure) {
            onLateSave({saved: true, filename: state.image.filename, tags: value.tags.slice(), toast: toastOf(value)});
            return true;
        }
        const {phase, statusText} = failedSave(failure);
        // A conflict's own sentence, not its merged "Saved now" view: the dialog that would show it is gone.
        const message = phase === 'unknown-outcome' ? COPY.unknownClosed : phase === 'conflict' ? failure.message : statusText;
        onLateSave({saved: false, message});
        return false;
    }

    /** The state patch of a failed save. */
    function failedSave(error) {
        if (!isApiError(error) || error.status < 400 || UNCONFIRMED_STATUS.has(error.status)) {
            return {phase: 'unknown-outcome', statusText: COPY.unknown};
        }
        lastBody = null;
        if (error.code === 'tag-conflict') {
            // The current set is shown (status and field) ahead of the draft; a second Save is against its revision.
            const current = error.current;
            if (!current) return {phase: 'conflict', statusText: error.message, currentTags: null};
            return {phase: 'conflict', currentTags: current.tags.slice(), tagRevision: current.tag_revision,
                statusText: `${error.message} ${savedNow(current.tags)}`,
                manual: mergeCurrent(current.tags, state.manual), draftRevision: state.draftRevision + 1};
        }
        if (REOPEN[error.code]) return {phase: REOPEN[error.code], statusText: error.message};
        return {phase: 'write-error', statusText: messageOf(error, COPY.saveFailed)};
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
        if (state.phase === 'observe-error') return open({keepDraft: observedOnce});
        if (REOBSERVE.has(state.phase)) return open({keepDraft: true});
        if (state.phase === 'generate-error') return generate();
        return send(lastBody);
    }

    function cancel() {
        if (closed) return;
        set({closed: true});
        closed = true;
        openSerial += 1;
        stopGenerating();
        readAbort?.abort();
        readAbort = null;
        // A save in flight keeps its capture until it settles (send releases it); aborting cannot undo the write.
        if (!saving) releaseCapture();
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

/**
 * The model and options of the bulk "Auto-tag selected images" dialog (D85): the same fields as the Tag image dialog's
 * model panel, without an image. open() reads the server's defaults and the models' download status once; until it
 * lands Start waits (`canStart` false). `unavailable` is why the chosen model cannot run ('' when it can), so the
 * dialog refuses to start a job that would fail at once.
 */
export function createTaggingOptionsController({api} = {}) {
    requireValue(object(api) && typeof api.get === 'function', 'An api client is required');
    const listeners = new Set();
    let closed = false;
    let defaults = DEFAULTS;
    let models = null;
    let abort = null;
    const deriveOptions = value => ({...value, canGenerate: false, unavailable: modelUnavailable(models, value.model),
        canStart: value.phase === 'ready' && !modelUnavailable(models, value.model)});
    let state = freeze(deriveOptions({phase: 'loading', model: MODEL_OPTIONS[0].id, options: {...DEFAULTS}, optionsRevision: 0}));

    function set(patch) {
        if (closed) return;
        state = freeze(deriveOptions({...state, ...patch}));
        for (const listener of [...listeners]) listener(state);
    }
    async function open() {
        if (closed || abort) return;
        abort = new AbortController();
        const read = await readDefaults(api, abort.signal);
        if (closed) return;
        ({defaults, models} = read);
        set({phase: 'ready', options: {...defaults}, optionsRevision: state.optionsRevision + 1});
    }
    function setModel(id) {
        requireValue(modelOf(id), 'Unknown tagging model');
        if (closed || id === state.model) return;
        // As in the Tag image dialog: the model's fields start from the defaults again; the count is kept (D91).
        set({model: id, optionsRevision: state.optionsRevision + 1,
            options: {...state.options, general_threshold: defaults.general_threshold,
                character_threshold: defaults.character_threshold, instructions: defaults.instructions}});
    }
    function setOption(key, value) {
        requireValue(OPTION_KEYS.has(key), 'Unknown tagging option');
        set({options: {...state.options, [key]: value}});
    }
    /** `{model_type, options}` for a job or a review session, or null while the options are invalid. */
    function request() {
        if (optionError(state.model, state.options)) return null;
        return {model_type: state.model, options: requestOptions(state.model, state.options)};
    }
    function dispose() {
        closed = true;
        listeners.clear();
        abort?.abort();
    }
    function subscribe(listener) {
        requireValue(typeof listener === 'function', 'A listener function is required');
        listeners.add(listener);
        return () => listeners.delete(listener);
    }
    return {
        get state() {return state;},
        subscribe, open, setModel, setOption, request, dispose,
    };
}
