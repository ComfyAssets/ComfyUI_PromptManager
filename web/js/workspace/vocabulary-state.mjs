/**
 * Native Tags & categories state: bounded vocabulary pages and guarded commands.
 *
 * UI-agnostic. The page renders `stream.state` / `command.state` and calls their
 * methods. Reads are owned by their stream: reset() or dispose() aborts the read
 * in flight and a superseded response never lands. Writes are sent once, without
 * an abort signal (aborting cannot undo a write), and are never replayed.
 *
 * Category pages are keyed by each value's lowest prompt id, which can move
 * between pages after an edit, so appended rows are deduplicated (first wins)
 * and every command that may have changed storage calls `onInvalidate` so the
 * page resets its streams to the first page.
 */
import { countOf } from './plural.mjs';

const LIMIT = 100;
const BASE = '/prompt_manager/vocabulary';
const KINDS = {
    tags: {key: row => row.id, valid: row => id(row.id) && typeof row.name === 'string' && row.name.length > 0},
    categories: {key: row => row.value, valid: row => typeof row.value === 'string' && row.value.length > 0},
};
const RECOVERY = {'storage-changed': 'storage-changed', 'vocabulary-conflict': 'conflict'};
// A proxy timeout or bad gateway cannot tell whether the server committed the write.
const UNCONFIRMED_STATUS = new Set([502, 503, 504]);
// Phases that may send a command; done, storage-changed and unknown-outcome need reset().
const READY = new Set(['idle', 'previewed', 'write-error', 'conflict']);
const IMPACT_FIELDS = ['positive_prompts', 'other_prompts', 'negative_variants', 'archived_negative_variants'];

const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const id = value => Number.isSafeInteger(value) && value > 0;
const count = value => Number.isSafeInteger(value) && value >= 0;
const epochOf = value => typeof value === 'string' && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(value);
const hexToken = value => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);

function requireValue(valid, message) {if (!valid) throw new TypeError(message);}
function freeze(value) {
    if (value && typeof value === 'object' && !Object.isFrozen(value)) {Object.values(value).forEach(freeze); Object.freeze(value);}
    return value;
}
function storageChanged() {
    return Object.assign(new Error('Prompt storage was restored. Reload the lists before continuing.'), {code: 'storage-changed'});
}
// Duck-typed by name: this ES module cannot import the UMD api-client.js.
function isApiError(error) {return error instanceof Error && error.name === 'ApiError' && Number.isInteger(error.status);}

function validatePage(page, kind, cursor) {
    const rows = page?.[kind];
    const {key, valid} = KINDS[kind];
    requireValue(object(page) && page.success === true && epochOf(page.library_epoch) && Array.isArray(rows) &&
        rows.every(row => object(row) && valid(row) && count(row.prompt_count)) &&
        new Set(rows.map(key)).size === rows.length && count(page.total) && typeof page.has_more === 'boolean' &&
        (page.next_cursor === null || (typeof page.next_cursor === 'string' && page.next_cursor.length > 0)) &&
        page.has_more === (page.next_cursor !== null) && !(page.has_more && page.next_cursor === cursor),
    `Invalid ${kind} page`);
}

/** One bounded, append-only list of tags or categories. */
export function createVocabularyStream({api, kind, onChange = () => {}}) {
    requireValue(Object.hasOwn(KINDS, kind), 'Unknown vocabulary kind');
    const {key} = KINDS[kind];
    const initial = () => ({items: Object.freeze([]), total: 0, cursor: null, hasMore: true, loading: false, error: null,
        epoch: null, resetRequired: false});
    let state = Object.freeze(initial());
    let request = null, disposed = false, seen = new Set();
    const emit = patch => {state = Object.freeze({...state, ...patch}); onChange(state);};

    async function loadMore() {
        if (disposed || state.loading || !state.hasMore || state.resetRequired) return 0;
        const current = request = new AbortController();
        const cursor = state.cursor;
        const query = new URLSearchParams({limit: String(LIMIT)});
        if (cursor !== null) query.set('cursor', cursor);
        emit({loading: true, error: null});
        try {
            const page = await api.get(`${BASE}/${kind}?${query}`, {signal: current.signal, cache: 'no-store'});
            if (disposed || current !== request) return 0;
            validatePage(page, kind, cursor);
            request = null;
            if (state.epoch !== null && page.library_epoch !== state.epoch) {
                emit({loading: false, error: storageChanged(), resetRequired: true});
                return 0;
            }
            const incoming = page[kind].filter(row => !seen.has(key(row))).map(row => Object.freeze({...row}));
            incoming.forEach(row => seen.add(key(row)));
            emit({items: incoming.length ? Object.freeze([...state.items, ...incoming]) : state.items, total: page.total,
                cursor: page.next_cursor, hasMore: page.has_more, loading: false, error: null, epoch: page.library_epoch});
            return incoming.length;
        } catch (error) {
            if (!disposed && current === request) {request = null; emit({loading: false, error});}
            return 0;
        }
    }

    return {
        get state() {return state;},
        loadMore,
        /** Drop every loaded row and read again from the first page. */
        reset() {
            if (disposed) return Promise.resolve(0);
            request?.abort();
            request = null;
            seen = new Set();
            emit(initial());
            return loadMore();
        },
        dispose() {disposed = true; request?.abort(); request = null;},
    };
}

function validateTag(value) {return object(value) && KINDS.tags.valid(value) && count(value.prompt_count);}
function validateImpact(value) {return object(value) && IMPACT_FIELDS.every(name => count(value[name]));}
function requireRow(row) {requireValue(object(row) && id(row.id) && typeof row.name === 'string', 'Invalid tag row');}
function requireEpoch(epoch) {requireValue(epochOf(epoch), 'Invalid library epoch');}
function requireText(value, name) {requireValue(typeof value === 'string', `${name} must be a string`);}

const RECEIPTS = {
    'create-tag': value => typeof value.created === 'boolean' && validateTag(value.tag),
    'rename-tag': value => typeof value.merged === 'boolean' && validateTag(value.tag) &&
        count(value.components_changed) && count(value.pairs_changed),
    'delete-tag': value => value.deleted === true && count(value.components_changed) && count(value.pairs_changed),
    'apply-category': (value, body) => value.source === body.source && value.target === body.target &&
        typeof value.consolidated === 'boolean' && validateImpact(value.impact),
};

function writePhase(error) {
    if (!isApiError(error)) return 'unknown-outcome';
    if (error.status === 409 && Object.hasOwn(RECOVERY, error.code)) return RECOVERY[error.code];
    if ((error.status >= 200 && error.status < 300) || UNCONFIRMED_STATUS.has(error.status)) return 'unknown-outcome';
    return 'write-error';
}

/**
 * One dialog's command. Phases: idle, saving, previewed, write-error, conflict,
 * storage-changed, unknown-outcome, done (and disposed). `onInvalidate({phase,
 * kind, receipt})` fires whenever stored vocabulary may differ from the loaded
 * lists: after a receipt, a conflict, a restore or an unconfirmed write, including
 * one that lands after `dispose()`.
 */
export function createVocabularyCommand({api, onChange = () => {}, onInvalidate = () => {}}) {
    let state = freeze({phase: 'idle', kind: null, error: null, preview: null, receipt: null});
    let disposed = false;
    // Set when the dialog's inputs change while a category preview is in flight.
    let stalePreview = false;
    const emit = patch => {state = freeze({...state, ...patch}); onChange(state);};
    const invalidate = () => onInvalidate(freeze({phase: state.phase, kind: state.kind, receipt: state.receipt}));

    async function write(kind, method, url, body) {
        emit({phase: 'saving', kind, error: null, preview: null, receipt: null});
        let phase = 'done', error = null, receipt = null;
        try {
            const value = await api[method](url, body);
            if (object(value) && value.success === true && value.library_epoch === body.expected_epoch && RECEIPTS[kind](value, body)) {
                receipt = freeze({...value});
            } else {
                // The request reached the server, so the write may have committed.
                phase = 'unknown-outcome';
                error = new TypeError('The server response could not be confirmed.');
            }
        } catch (failure) {
            phase = writePhase(failure);
            error = failure;
        }
        if (disposed) {
            // A write that may have committed still invalidates cached Library views after the dialog went away
            // (forced close, Back); nothing is rendered then.
            if (phase !== 'write-error') onInvalidate(freeze({phase, kind, receipt}));
            return;
        }
        emit({phase, error, receipt});
        if (phase !== 'write-error') invalidate();
    }

    async function preview(body) {
        emit({phase: 'saving', kind: 'preview-category', error: null, preview: null, receipt: null});
        stalePreview = false;
        try {
            const value = await api.post(`${BASE}/categories/preview`, body);
            requireValue(object(value) && value.success === true && value.library_epoch === body.expected_epoch &&
                value.source === body.source && value.target === body.target.trim() && typeof value.target_exists === 'boolean' &&
                validateImpact(value.impact) && hexToken(value.impact_token), 'Invalid category preview');
            // Inputs edited while it was pending: it describes values no longer on screen, so Preview is needed again.
            if (!disposed) emit(stalePreview ? {phase: 'idle'} : {phase: 'previewed', preview: freeze({...value})});
        } catch (error) {
            if (disposed) return;
            // A preview writes nothing: only a restore or a changed category needs recovery.
            const phase = isApiError(error) && error.status === 409 && Object.hasOwn(RECOVERY, error.code) ? RECOVERY[error.code] : 'write-error';
            if (phase === 'write-error' && stalePreview) {emit({phase: 'idle'}); return;}
            emit({phase, error});
            if (phase !== 'write-error') invalidate();
        }
    }

    const ready = () => !disposed && READY.has(state.phase);
    return {
        get state() {return state;},
        createTag(name, epoch) {
            requireText(name, 'Tag name');
            requireEpoch(epoch);
            if (!ready()) return Promise.resolve();
            return write('create-tag', 'post', `${BASE}/tags`, {expected_epoch: epoch, name});
        },
        renameTag(row, newName, epoch) {
            requireRow(row);
            requireText(newName, 'New name');
            requireEpoch(epoch);
            if (!ready()) return Promise.resolve();
            return write('rename-tag', 'patch', `${BASE}/tags/${row.id}`, {expected_epoch: epoch, name: row.name, new_name: newName});
        },
        deleteTag(row, epoch) {
            requireRow(row);
            requireEpoch(epoch);
            if (!ready()) return Promise.resolve();
            return write('delete-tag', 'post', `${BASE}/tags/${row.id}/delete`, {expected_epoch: epoch, name: row.name});
        },
        previewCategory(source, target, epoch) {
            requireText(source, 'Category');
            requireText(target, 'New category name');
            requireEpoch(epoch);
            if (!ready()) return Promise.resolve();
            return preview({expected_epoch: epoch, source, target});
        },
        /** Apply only the preview currently shown; anything else is ignored. */
        applyCategory(shown) {
            if (!ready() || state.phase !== 'previewed' || shown !== state.preview) return Promise.resolve();
            return write('apply-category', 'post', `${BASE}/categories/apply`, {expected_epoch: shown.library_epoch,
                source: shown.source, target: shown.target, impact_token: shown.impact_token});
        },
        /** The user changed a dialog input: a shown or pending preview no longer describes it. */
        edit() {
            if (disposed) return;
            if (state.phase === 'previewed') emit({phase: 'idle', preview: null});
            else if (state.phase === 'saving' && state.kind === 'preview-category') stalePreview = true;
        },
        /** Explicit user recovery (after re-reading the lists); never resends anything. */
        reset() {
            if (!disposed && state.phase !== 'saving') emit({phase: 'idle', kind: null, error: null, preview: null, receipt: null});
        },
        dispose() {
            disposed = true;
            state = freeze({...state, phase: 'disposed'});
        },
    };
}

/** Library filters for a row's Browse: exactly one constraint, the stored value. */
export function browseFilters(kind, value) {
    requireValue(Object.hasOwn(KINDS, kind), 'Unknown vocabulary kind');
    requireValue(typeof value === 'string' && value.length > 0, 'Invalid vocabulary value');
    return kind === 'tags'
        ? {text: '', category: '', tags: [value], sort: 'newest'}
        : {text: '', category: value, tags: [], sort: 'newest'};
}

/** The exact tag's current owner counts, for the delete confirmation. Throws on any unconfirmed shape. */
export function readTagImpact(api, row, {signal} = {}) {
    requireRow(row);
    return api.get(`${BASE}/tags/${row.id}/impact`, {signal, cache: 'no-store'}).then(value => {
        requireValue(object(value) && value.success === true && epochOf(value.library_epoch) && validateImpact(value.impact), 'Invalid tag impact');
        return freeze({library_epoch: value.library_epoch, impact: {...value.impact}});
    });
}

/** Owner kinds with a nonzero count, in sentence form ("a, b and c"); empty when nothing is linked. */
function impactList(impact) {
    const negatives = impact.negative_variants + impact.archived_negative_variants;
    const parts = [];
    if (impact.positive_prompts) parts.push(countOf(impact.positive_prompts, 'positive prompt'));
    if (negatives) {
        const archived = impact.archived_negative_variants ? ` (${impact.archived_negative_variants} archived)` : '';
        parts.push(`${countOf(negatives, 'negative variant')}${archived}`);
    }
    if (impact.other_prompts) parts.push(countOf(impact.other_prompts, 'other saved prompt'));
    return parts.length > 1 ? `${parts.slice(0, -1).join(', ')} and ${parts.at(-1)}` : parts.join('');
}

/** The Manage categories preview sentence (decision D9); zero counts are left out. */
export function categoryPreviewText(shown) {
    const outcome = shown.target_exists ? `consolidates it into the existing “${shown.target}”` : `renames it to “${shown.target}”`;
    return `“${shown.source}” is used by ${impactList(shown.impact)}. Apply ${outcome}.`;
}

/** The Delete tag confirmation sentence (decision D14). */
export function deleteTagText(name, impact) {
    const list = impactList(impact);
    if (!list) return `“${name}” is not linked to any saved prompt. Deleting it removes only the name.`;
    return `Deleting “${name}” removes the name and its links from ${list}. The prompts, negative variants, images and history themselves are kept.`;
}
