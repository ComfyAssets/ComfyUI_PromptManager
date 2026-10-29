/**
 * Native Library editor state: fresh initialization, guarded saves and honest recovery.
 *
 * UI-agnostic. A dialog renders `controller.state` and calls its methods. Every
 * controller owns its reads: a newer read, a retry or dispose() supersedes older
 * ones, so a late response can never enable Save or supply an epoch. Writes are
 * sent once, without an abort signal (aborting a request cannot undo a write),
 * and are never replayed automatically.
 */
const MODES = new Set(['new', 'metadata', 'revision', 'negative']);
const KINDS = new Set(['metadata', 'create', 'add-negative', 'archive-pair', 'restore-pair']);
// D153 (#447): the workspace edits no 1–5 rating; a stored one is left exactly as it is.
export const EDITOR_FIELDS = Object.freeze(['title', 'positive_text', 'negative_text', 'category', 'tags', 'notes']);
const FIELDS = EDITOR_FIELDS;
const RECOVERY = {'metadata-conflict': 'conflict', 'storage-changed': 'storage-changed'};
// A proxy timeout or bad gateway cannot tell whether the server committed the write.
const UNCONFIRMED_STATUS = new Set([502, 503, 504]);
const SUBMITTABLE = new Set(['ready', 'write-error']);
const RELOADABLE = new Set(['ready', 'write-error', 'conflict', 'storage-changed', 'unknown-outcome', 'initialization-error']);
const UNCATEGORIZED = 'Uncategorized';
const VARIANT_LIMIT = 40;
const OPTIONS_URL = '/prompt_manager/library/options';

const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const uuid = value => typeof value === 'string' && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(value);
const token = value => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const id = value => Number.isSafeInteger(value) && value > 0;
const count = value => Number.isSafeInteger(value) && value >= 0;
const nullableString = value => value === null || typeof value === 'string';
const strings = value => Array.isArray(value) && value.every(item => typeof item === 'string');
const rating = value => value === null || (Number.isInteger(value) && value >= 1 && value <= 5);

function requireValue(valid, message = 'Invalid Library response') {if (!valid) throw new TypeError(message);}
function freeze(value) {
    if (value && typeof value === 'object' && !Object.isFrozen(value)) {Object.values(value).forEach(freeze); Object.freeze(value);}
    return value;
}
function storageChanged() {
    return Object.assign(new Error('Prompt storage was restored; refresh the library'), {code: 'storage-changed'});
}
// Duck-typed by name: this ES module cannot import the UMD api-client.js, and a
// browser page may load a different ApiError class instance than the Node tests.
function isApiError(error) {return error instanceof Error && error.name === 'ApiError' && Number.isInteger(error.status);}

function validateParent(value, positiveId) {
    requireValue(object(value) && id(value.id) && typeof value.text === 'string' && nullableString(value.title) &&
        nullableString(value.category) && strings(value.tags) && rating(value.rating) && nullableString(value.notes) &&
        token(value.edit_token) && (positiveId === undefined || value.id === positiveId), 'Invalid prompt owner');
}
function validatePair(value, owner) {
    requireValue(object(value) && id(value.id) && id(value.positive_prompt_id) && typeof value.positive_text === 'string' &&
        typeof value.negative_text === 'string' && typeof value.archived === 'boolean' && count(value.image_count) &&
        count(value.run_count), 'Invalid saved negative variant');
    requireValue(value.positive_prompt_id === owner.id && value.positive_text === owner.text,
        'Saved negative variant belongs to another prompt');
}

const fresh = (api, url, signal) => api.get(url, {signal, cache: 'no-store'});
async function readOptions(api, signal) {
    const value = await fresh(api, OPTIONS_URL, signal);
    requireValue(object(value) && value.success === true && uuid(value.library_epoch) && strings(value.categories), 'Invalid Library options');
    return value;
}
async function readDetail(api, positiveId, signal) {
    const value = await fresh(api, `/prompt_manager/library/${positiveId}?variant_limit=${VARIANT_LIMIT}`, signal);
    requireValue(object(value) && value.success === true && uuid(value.library_epoch) && Array.isArray(value.variants), 'Invalid prompt history');
    validateParent(value.positive, positiveId);
    return value;
}
/** The exact pair by ID; ownership is checked against the parent read by the caller. */
async function readPair(api, pairId, signal) {
    const value = await fresh(api, `/prompt_manager/pairs/${pairId}`, signal);
    requireValue(object(value) && value.success === true && object(value.pair) && value.pair.id === pairId, 'Invalid saved negative variant');
    return value.pair;
}

/** Detail, options and pair read together; snapshots from different epochs are rejected. */
async function readOwner(api, {positiveId, pairId, withOptions}, signal) {
    const [detail, options, pair] = await Promise.all([readDetail(api, positiveId, signal),
        withOptions ? readOptions(api, signal) : null, pairId === null ? null : readPair(api, pairId, signal)]);
    if (options && options.library_epoch !== detail.library_epoch) throw storageChanged();
    if (pair) validatePair(pair, detail.positive);
    return {epoch: detail.library_epoch, owner: detail.positive, pair, categories: options ? options.categories : [], detail, options};
}

/** New prompt has no owner: only a fresh options epoch makes it ready. */
async function readNewSnapshot(api, signal) {
    const options = await readOptions(api, signal);
    return {epoch: options.library_epoch, owner: null, pair: null, categories: options.categories};
}

/**
 * Re-read the saved owner after a confirmed receipt. Reads only: detail, options
 * and (when the commit names one) the exact pair, kept apart from detail.variants.
 */
export async function reconcileLibraryReceipt(api, commit, {signal} = {}) {
    requireValue(object(commit) && KINDS.has(commit.kind) && object(commit.receipt), 'Invalid editor commit');
    const {receipt} = commit;
    const positiveId = receipt.positive ? receipt.positive.id : receipt.pair?.positive_prompt_id;
    requireValue(id(positiveId), 'Invalid editor commit');
    const pairId = receipt.pair ? receipt.pair.id : null;
    const {detail, options, pair} = await readOwner(api, {positiveId, pairId, withOptions: true}, signal);
    if (receipt.library_epoch !== detail.library_epoch) throw storageChanged();
    return freeze({commit, detail, pair, options});
}

export function fallbackTitle(text) {return text.split('\n')[0].trim();}
export function parseTags(value) {return [...new Set(value.split(',').map(tag => tag.trim()).filter(Boolean))];}
const sameTags = (a, b) => a.length === b.length && new Set([...a, ...b]).size === a.length;
const stored = value => (value ?? '').trim();
const blankDraft = () => ({title: '', positive_text: '', negative_text: '', category: '', tags: '', notes: ''});
function seedDraft(mode, {owner, pair}) {
    if (!owner) return blankDraft();
    if (mode === 'negative') return {...blankDraft(), positive_text: owner.text};
    return {title: stored(owner.title) ? owner.title : fallbackTitle(owner.text), positive_text: owner.text,
        negative_text: pair ? pair.negative_text : '', category: owner.category ?? '', tags: owner.tags.join(', '),
        notes: owner.notes ?? ''};
}
/** Keep fields the user changed from the previous seed; the rest follow the new seed. */
function overlay(next, draft, previous) {
    return Object.fromEntries(FIELDS.map(name => [name, draft[name] !== previous[name] ? draft[name] : next[name]]));
}
function baselineOf(owner) {
    return owner && {title: owner.title, category: owner.category, tags: [...owner.tags], notes: owner.notes};
}

/** Only changed fields; an untouched fallback title is not a title edit. */
function metadataChanges(draft, seed, baseline) {
    const changes = {};
    const title = draft.title.trim(), category = draft.category.trim(), tags = parseTags(draft.tags);
    if (draft.title !== seed.title && title !== stored(baseline.title)) changes.title = title;
    if (category !== stored(baseline.category)) changes.category = category;
    if (!sameTags(tags, baseline.tags)) changes.tags = tags;
    if (draft.notes !== (baseline.notes ?? '')) changes.notes = draft.notes;
    return changes;
}
function createMetadata(draft) {
    return {title: draft.title.trim(), category: draft.category.trim() || UNCATEGORIZED, tags: parseTags(draft.tags),
        notes: draft.notes};
}
// A blank category is stored as NULL; "Uncategorized" is only the UI's label for it.
const categoryKey = value => (stored(value) === UNCATEGORIZED ? '' : stored(value));
function sameMetadata(sent, owner) {
    return sent.title === stored(owner.title) && categoryKey(sent.category) === categoryKey(owner.category) && sameTags(sent.tags, owner.tags) &&
        sent.notes === (owner.notes ?? '');
}
function draftProblem(mode, draft) {
    if (mode !== 'negative' && !draft.title.trim()) return Object.assign(new Error('Title is required'), {code: 'invalid-draft'});
    if ((mode === 'new' || mode === 'revision') && !draft.positive_text.trim()) {
        return Object.assign(new Error('Positive prompt is required'), {code: 'invalid-draft'});
    }
    return null;
}

/** Phase for a failed write: only an exact 409 code drives recovery. */
function failedPhase(error) {
    if (!isApiError(error)) return 'unknown-outcome';
    if (error.status === 409 && Object.hasOwn(RECOVERY, error.code)) return RECOVERY[error.code];
    if ((error.status >= 200 && error.status < 300) || UNCONFIRMED_STATUS.has(error.status)) return 'unknown-outcome';
    return 'write-error';
}

function validateReceipt(command, value, {positiveId, owner, pairId}) {
    requireValue(object(value) && value.success === true, 'Invalid save receipt');
    if (command.kind === 'archive-pair' || command.kind === 'restore-pair') {
        validatePair(value.pair, owner);
        requireValue(value.pair.id === pairId && value.pair.archived === (command.kind === 'archive-pair'), 'Invalid save receipt');
        return {success: true, library_epoch: command.epoch, pair: value.pair};
    }
    requireValue(value.library_epoch === command.body.expected_epoch, 'Invalid save receipt');
    validateParent(value.positive, command.kind === 'create' ? undefined : positiveId);
    if (command.kind === 'metadata') return value;
    validatePair(value.pair, value.positive);
    requireValue(typeof value.positive_created === 'boolean' && typeof value.pair_created === 'boolean' &&
        value.parent_metadata_applied === value.positive_created && (value.pair_created || !value.positive_created) &&
        (command.kind === 'create' || !value.positive_created), 'Invalid save receipt');
    return value;
}
function outcomeOf(command, receipt) {
    const recipe = command.kind === 'create' || command.kind === 'add-negative';
    return {kind: command.kind, positiveCreated: recipe && receipt.positive_created, pairCreated: recipe && receipt.pair_created,
        metadataNotApplied: command.kind === 'create' && !receipt.parent_metadata_applied &&
            !sameMetadata(command.body.parent_metadata, receipt.positive),
        pairArchived: receipt.pair ? receipt.pair.archived : null};
}

export function createLibraryEditorController({api, mode, positiveId = null, pairId = null, onChange = () => {},
    onCommitted = () => {}, onReconciled = () => {}} = {}) {
    requireValue(MODES.has(mode), 'Unknown editor mode');
    const existing = mode !== 'new';
    requireValue(existing ? id(positiveId) : positiveId === null && pairId === null, 'Invalid editor owner');
    requireValue(pairId === null || id(pairId), 'Invalid editor pair');
    const withOptions = mode !== 'negative';
    let disposed = false, generation = 0, reads = null, started = null, review = false;
    let seed = blankDraft(), pending = null, commit = null;
    let state = freeze({phase: 'initializing', draft: blankDraft(), baseline: null, receipt: null, error: null,
        reviewRequired: false, epoch: null, owner: null, pair: null, pairRead: 'none', pairReadError: null, categories: [], outcome: null,
        reconciled: null, unchanged: false});
    const emit = patch => {if (disposed) return; state = freeze({...state, ...patch}); onChange(state);};

    /** Start a read that supersedes every older one; current() is false once stale. */
    function beginRead() {
        reads?.abort();
        const controller = reads = new AbortController(), attempt = ++generation;
        return {signal: controller.signal, current() {
            if (disposed || attempt !== generation) return false;
            reads = null; return true;
        }};
    }

    async function load(keepDraft) {
        const read = beginRead();
        emit({phase: 'initializing', error: null});
        try {
            const snapshot = existing ? await readOwner(api, {positiveId, pairId, withOptions}, read.signal)
                : await readNewSnapshot(api, read.signal);
            if (!read.current()) return;
            const next = seedDraft(mode, snapshot);
            const draft = keepDraft ? overlay(next, state.draft, seed) : next;
            seed = next;
            emit({phase: 'ready', draft, baseline: baselineOf(snapshot.owner), epoch: snapshot.epoch, owner: snapshot.owner,
                pair: snapshot.pair, pairRead: snapshot.pair ? 'fresh' : 'none', pairReadError: null, categories: snapshot.categories,
                error: null, reviewRequired: review && keepDraft});
        } catch (error) {
            if (read.current()) emit({phase: 'initialization-error', error});
        }
    }

    function command() {
        const {draft, epoch, owner} = state;
        if (mode === 'metadata') {
            const metadata = metadataChanges(draft, seed, state.baseline);
            return Object.keys(metadata).length ? {kind: 'metadata', method: 'patch', url: `/prompt_manager/library/${positiveId}`,
                body: {expected_epoch: epoch, if_match: owner.edit_token, metadata}} : null;
        }
        if (mode === 'negative') {
            return {kind: 'add-negative', method: 'post', url: `/prompt_manager/library/${positiveId}/variants`,
                body: {expected_epoch: epoch, if_match: owner.edit_token, negative_text: draft.negative_text}};
        }
        return {kind: 'create', method: 'post', url: '/prompt_manager/library', body: {expected_epoch: epoch,
            positive_text: draft.positive_text, negative_text: draft.negative_text, parent_metadata: createMetadata(draft)}};
    }

    async function send(next) {
        pending = next;
        emit({phase: 'saving', error: null});
        let value;
        try {
            value = await api[next.method](next.url, next.body);
        } catch (error) {
            if (disposed) return;
            const phase = failedPhase(error);
            if (phase !== 'unknown-outcome') pending = null;
            emit({phase, error});
            return;
        }
        let receipt;
        try {
            receipt = freeze(structuredClone(validateReceipt(next, value, {positiveId, owner: state.owner, pairId: state.pair?.id})));
        } catch (error) {
            emit({phase: 'unknown-outcome', error});
            return;
        }
        pending = null;
        commit = freeze({kind: next.kind, receipt});
        // A confirmed write still invalidates cached Library views after the dialog went away; nothing else runs then.
        if (disposed) {onCommitted(commit); return;}
        emit({receipt, outcome: outcomeOf(next, receipt)});
        onCommitted(commit);
        await reconcile();
    }

    async function reconcile() {
        const read = beginRead();
        try {
            const result = await reconcileLibraryReceipt(api, commit, {signal: read.signal});
            if (!read.current()) return;
            emit({phase: 'complete', reconciled: result, error: null});
            onReconciled(result);
        } catch (error) {
            if (read.current()) emit({phase: 'saved-refresh-error', error});
        }
    }

    /**
     * The pair route has no epoch guard, so every pair write, first or retried,
     * first confirms with a fresh read that storage was not restored since
     * `next.epoch`. A failed check read keeps `failurePhase` and writes nothing.
     */
    async function sendFencedPair(next, failurePhase) {
        emit({phase: 'saving', error: null});
        const read = beginRead();
        let current;
        try {
            current = await readOptions(api, read.signal);
        } catch (error) {
            if (read.current()) emit({phase: failurePhase, error});
            return;
        }
        if (!read.current()) return;
        if (current.library_epoch !== next.epoch) {
            pending = null;
            emit({phase: 'storage-changed', error: storageChanged()});
            return;
        }
        await send(next);
    }

    async function setArchive(value) {
        if (typeof value !== 'boolean') throw new TypeError('Archive state must be a boolean');
        if (disposed || !existing || !SUBMITTABLE.has(state.phase)) return;
        if (!state.pair) {emit({error: new Error('Choose a saved negative variant first.')}); return;}
        await sendFencedPair(freeze({kind: value ? 'archive-pair' : 'restore-pair', method: 'patch',
            url: `/prompt_manager/pairs/${state.pair.id}`, body: {archived: value}, epoch: state.epoch}), 'write-error');
    }

    return {
        get state() {return state;},
        initialize() {
            if (!started) started = disposed ? Promise.resolve() : load(true);
            return started;
        },
        setField(name, value) {
            if (!FIELDS.includes(name)) throw new TypeError(`Unknown editor field: ${name}`);
            if (typeof value !== 'string') throw new TypeError(`${name} must be a string`);
            emit({draft: {...state.draft, [name]: value}});
        },
        async submit() {
            if (disposed || !SUBMITTABLE.has(state.phase)) return;
            const problem = draftProblem(mode, state.draft);
            if (problem) {emit({error: problem}); return;}
            const next = command();
            if (!next) {emit({phase: 'complete', error: null, unchanged: true}); return;}
            await send(freeze(next));
        },
        setArchive,
        /**
         * Re-read the selected pair (for example its linked counts) through the same
         * exact-ID read and owner check as initialization. A failure keeps the
         * previous pair and is reported as `pairRead: 'error'` with `pairReadError`.
         */
        async refreshPair() {
            if (disposed || !existing || !state.pair || !SUBMITTABLE.has(state.phase)) return;
            const read = beginRead();
            emit({pairRead: 'reading', pairReadError: null});
            try {
                const pair = await readPair(api, state.pair.id, read.signal);
                validatePair(pair, state.owner);
                if (read.current()) emit({pair, pairRead: 'fresh'});
            } catch (error) {
                if (read.current()) emit({pairRead: 'error', pairReadError: error});
            }
        },
        async reloadOwner({keepDraft} = {}) {
            if (typeof keepDraft !== 'boolean') throw new TypeError('reloadOwner needs keepDraft');
            if (disposed || !RELOADABLE.has(state.phase)) return;
            pending = null;
            review = keepDraft;
            await load(keepDraft);
        },
        async retryInitialization() {
            if (!disposed && state.phase === 'initialization-error') await load(true);
        },
        async retryReconciliation() {
            if (!disposed && state.phase === 'saved-refresh-error') await reconcile();
        },
        /** Explicit user action only: resend the identical unconfirmed payload once. */
        async retryUnconfirmed() {
            if (disposed || state.phase !== 'unknown-outcome' || !pending) return;
            if (pending.kind === 'archive-pair' || pending.kind === 'restore-pair') {
                // The outcome stays unknown if the fence read itself fails.
                await sendFencedPair(pending, 'unknown-outcome');
                return;
            }
            if (pending.kind !== 'metadata') {await send(pending); return;}
            // A metadata retry needs the current owner reviewed, never a replayed token.
            pending = null;
            review = true;
            await load(true);
        },
        dispose() {
            if (disposed) return;
            disposed = true;
            reads?.abort();
            reads = null;
            state = freeze({...state, phase: 'disposed'});
        },
    };
}
