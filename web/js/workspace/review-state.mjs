/**
 * Needs review state (#249 PR F): the pending queue, the selected item's sampler paths and one explicit decision, over
 * the review routes (`py/api/review.py`).
 *
 * - The queue is read 40 at a time, oldest first; `total` is the exact pending count. The cursor is bound to the
 *   storage epoch only, so Load more keeps working while new items arrive.
 * - Selecting an item reads its detail: one "Sampler <id> · <positive> / <negative>" option per candidate, unusable
 *   ones disabled, the first workable one chosen, then "Leave unresolved" (rulings R2/R3).
 * - Apply takes a fresh capture of the file (`observe`), then posts the decision; the server releases a capture it
 *   used. A refused decision releases the capture and keeps the selection with the server's sentence as the warning;
 *   a restored storage or a changed queue reads the queue again. A request that got no answer keeps its capture and
 *   its exact body, and the next Apply of the same choice (`retry()`) resends that body once.
 * - Leave for later writes nothing. Tagging the image (the shared Tag dialog) never changes the item.
 * - `refresh()` re-reads the queue quietly when the server says the queue changed (F2: the review change stream:
 *   a scan or the monitor added items, another tab decided one). The open item, its choice and an unanswered decision
 *   stay; when the open item has left the queue the next one opens with a note. Refreshes asked for while one runs
 *   collapse into one more read. Every re-read keeps the loaded depth (F3): it reads page after page until it holds as
 *   many items as were shown, so Load more pages stay and the page does not jump.
 * - `sync(version)` is the stream's `ready`: it re-reads only when the queue read last is older than the version the
 *   stream names (`{library_epoch, review_revision}`, the pair every list read answers), so a visit reads its queue
 *   once (F3). An unknown version re-reads.
 */
import { noun } from './plural.mjs';

export const REVIEW_URL = '/prompt_manager/review/items';
const OBSERVE_URL = '/prompt_manager/image-annotations/observe';
const CAPTURE_URL = '/prompt_manager/image-annotations/capture/';
export const PAGE_LIMIT = 40;
export const UNRESOLVED = 'unresolved';

export const COPY = Object.freeze({
    title: 'Metadata review',
    description: 'Uncertain evidence stays visible until you decide.',
    notice: 'Multiple sampler paths were found. The existing image links were preserved. Choose the correct context or leave them unresolved.',
    // D86 (plan D63, ruling R6): the empty queue's sentence.
    empty: 'Ambiguous images are added when a metadata scan or a new generation finds more than one sampler path.',
    panelTitle: 'Resolve the association',
    leaveUnresolved: 'Leave unresolved',
    // The states the concept lacks are text only (Global Constraints); D119.
    loading: 'Loading the review queue…',
    loadFailed: 'The review queue could not be read.',
    detailLoading: 'Loading sampler paths…',
    detailFailed: 'This item could not be read. Select it again to retry.',
    moreFailed: 'More items could not be loaded. Try again.',
    limited: 'This file has too many sampler paths to list. You can leave it unresolved.',
    unavailable: 'This file is not under a current image source, so it cannot be opened. You can leave it unresolved.',
    lost: 'The decision may not have been saved. Apply again to send the same decision once more.',
    // D119 (F2): the open item left the queue in a re-read, and a failed re-read.
    decidedElsewhere: name => `${name} was decided elsewhere, so the next image is open.`,
    refreshFailed: 'The review queue could not be read again. Reload the page to see the latest images.',
    failed: 'The decision could not be saved. Try again.',
    applied: 'Association accepted.',
    leftUnresolved: 'Left unresolved.',
});

// Answers after which the queue itself is out of date: read it again.
const RELOAD = new Set(['storage-changed', 'review-conflict', 'review-missing']);

/** The notice heading: "No images need a decision", "1 image needs a decision", "3 images need a decision". */
export function noticeTitle(count) {
    if (!count) return 'No images need a decision';
    return `${count.toLocaleString('en-US')} ${noun(count, 'image')} ${count === 1 ? 'needs' : 'need'} a decision`;
}

/** One Sampler context option's text. */
export function optionLabel(candidate) {
    return `Sampler ${candidate.sampler_id} · ${candidate.label}`;
}

const workable = candidate => candidate?.state === 'resolved' && typeof candidate.positive?.text === 'string' &&
    typeof candidate.negative?.text === 'string';

/** The Sampler context options of a detail: its candidates (unusable ones disabled), then Leave unresolved. */
export function contextOptions(detail) {
    const usable = detail.target !== null && !detail.unavailable;
    const options = (Array.isArray(detail.candidates) ? detail.candidates : []).map(candidate => Object.freeze({
        value: candidate.hash, label: optionLabel(candidate), disabled: !usable || !workable(candidate), candidate,
    }));
    options.push(Object.freeze({ value: UNRESOLVED, label: COPY.leaveUnresolved, disabled: false, candidate: null }));
    return Object.freeze(options);
}

/** The prompt blocks of the chosen option: its two role texts, or the ambiguous status for Leave unresolved. */
function promptsOf(option) {
    if (!option?.candidate) return { positive: { status: 'ambiguous' }, negative: { status: 'ambiguous' } };
    const { positive, negative } = option.candidate;
    return { positive: { status: 'resolved', text: positive.text }, negative: { status: 'resolved', text: negative.text } };
}

function warningOf(detail) {
    if (detail.unavailable || detail.target === null) return COPY.unavailable;
    if (detail.limited) return COPY.limited;
    return '';
}

const isAbort = error => error?.name === 'AbortError';
// No answer at all (the network, or a body that was not the server's JSON): the decision may have been saved.
const isLost = error => !isAbort(error) && !error?.code && !(error?.status >= 400 && error?.status < 500);

const initial = () => ({
    status: 'idle', epoch: null, total: 0, items: Object.freeze([]), nextCursor: null, more: 'idle',
    selectedId: null, detail: null, detailStatus: 'idle', options: Object.freeze([]), choice: null, prompts: null,
    phase: 'idle', warning: '', canRetry: false, toast: null,
});

/**
 * @param {{api: {get: Function, post: Function, del: Function}}} options
 * @returns the controller: `state`, `subscribe(listener)`, `load()`, `refresh()`, `loadMore()`, `select(id)`, `choose(value)`,
 *   `sync(version)`, `apply()`, `retry()`, `leave()`, `dispose()`.
 */
export function createReviewController({ api }) {
    let state = Object.freeze(initial());
    const listeners = new Set();
    const lifetime = new AbortController();
    let listRead = null, detailRead = null, pending = null, disposed = false;
    let refreshing = null, refreshAgain = false;
    // What the first read owes once it answers: nothing, a re-read ('always'), or a re-read unless it is this version.
    let afterLoad = null;
    // The version of the queue read last: {epoch, revision}.
    let shown = null;

    function set(update) {
        if (disposed) return;
        state = Object.freeze({ ...state, ...update });
        for (const listener of [...listeners]) listener(state);
    }
    const signalOf = controller => AbortSignal.any ? AbortSignal.any([lifetime.signal, controller.signal]) : controller.signal;

    function release(token) {
        if (!token) return;
        api.del(`${CAPTURE_URL}${encodeURIComponent(token)}`, { keepalive: true }).catch(() => {});
    }
    function dropPending() {
        release(pending?.body.capture_token);
        pending = null;
    }

    const isVersion = version => typeof version?.library_epoch === 'string' && Number.isSafeInteger(version?.review_revision);
    const current = version => isVersion(version) && shown !== null && shown.epoch === version.library_epoch &&
        shown.revision >= version.review_revision;

    /** One list page; `cursor` continues after the last item already read. */
    function readPage(read, cursor) {
        const query = new URLSearchParams({ limit: String(PAGE_LIMIT) });
        if (cursor) query.set('cursor', cursor);
        return api.get(`${REVIEW_URL}?${query}`, { signal: signalOf(read), cache: 'no-store' });
    }

    /**
     * The queue down to `depth` items: the first page, then the next ones while fewer than `depth` are held. A restore
     * met after the first page (its cursor names the old storage) reads once more from the first page.
     */
    async function readQueue(read, depth) {
        for (let attempt = 0; ; attempt += 1) {
            const first = await readPage(read, null);
            const items = [...(first.items ?? [])];
            let { total } = first;
            let next = first.next_cursor ?? null;
            try {
                while (next && items.length < depth) {
                    const page = await readPage(read, next);
                    const known = new Set(items.map(entry => entry.id));
                    items.push(...(page.items ?? []).filter(entry => !known.has(entry.id)));
                    total = page.total;
                    next = page.next_cursor ?? null;
                }
            } catch (error) {
                if (attempt === 0 && RELOAD.has(error?.code)) continue;
                throw error;
            }
            return { first, items, total, next };
        }
    }

    /**
     * Read the queue as deep as it is shown; `stale` re-reads the selected item's detail too (its revision or epoch
     * moved). `quiet` (a refresh) keeps the page as it is while reading and after a failed read.
     */
    async function load({ keepWarning = false, stale = false, quiet = false } = {}) {
        listRead?.abort();
        const read = listRead = new AbortController();
        if (!quiet) set({ status: state.items.length ? state.status : 'loading', ...(keepWarning ? {} : { warning: '' }) });
        let readOk = false;
        try {
            // One page, or as many as Load more had shown.
            const queue = await readQueue(read, state.items.length > PAGE_LIMIT ? state.items.length : 0);
            if (read !== listRead || disposed) return;
            const items = Object.freeze(queue.items.map(entry => Object.freeze({ ...entry })));
            const left = state.items.find(entry => entry.id === state.selectedId);
            shown = Number.isSafeInteger(queue.first.review_revision)
                ? { epoch: queue.first.library_epoch, revision: queue.first.review_revision } : null;
            set({ status: 'ready', epoch: queue.first.library_epoch, total: queue.total, items, nextCursor: queue.next,
                more: 'idle' });
            readOk = true;
            const keep = items.find(entry => entry.id === state.selectedId);
            // A re-read keeps the open item as it is (even a failed read of it, which has its own Retry).
            if (keep && !stale && (quiet || state.detail)) return;
            if (quiet && left && !keep && items.length) {
                set({ warning: COPY.decidedElsewhere(left.filename) });
                await select(items[0].id, { keepWarning: true });
            } else if (items.length) await select((keep ?? items[0]).id, { keepWarning });
            else set({ selectedId: null, detail: null, detailStatus: 'idle', options: Object.freeze([]), choice: null, prompts: null });
        } catch (error) {
            if (read !== listRead || disposed || isAbort(error)) return;
            if (quiet && state.status === 'ready') set({ warning: COPY.refreshFailed });
            else set({ status: 'error' });
        } finally {
            // A change heard while the first read ran: that read may predate it, so read once more (unless the
            // stream's ready named a version this read already holds).
            const owed = afterLoad;
            if (readOk && owed && !disposed) {
                afterLoad = null;
                if (owed === 'always' || !current(owed)) refresh();
            }
        }
    }

    /** The stream's `ready`: re-read only when the queue shown is older than `version`. */
    function sync(version) {
        if (disposed) return Promise.resolve();
        if (state.status === 'loading') {
            // A second version, or a change, while the first read runs: no longer one version to compare.
            afterLoad = afterLoad === null && isVersion(version) ? version : 'always';
            return Promise.resolve();
        }
        if (state.status === 'ready' && current(version)) return Promise.resolve();
        return refresh();
    }

    /** Re-read the queue after a change elsewhere; calls made while one runs collapse into one more read. */
    function refresh() {
        if (disposed) return Promise.resolve();
        // Before the queue has been read there is nothing to keep: the first read (or Retry) reads the latest.
        if (state.status !== 'ready') {
            if (state.status === 'loading') afterLoad = 'always';
            return Promise.resolve();
        }
        if (refreshing) {
            refreshAgain = true;
            return refreshing;
        }
        refreshing = (async () => {
            do {
                refreshAgain = false;
                // A decision in flight finishes first: the page leaves when it is recorded.
                if (state.phase === 'applying') return;
                await load({ keepWarning: true, quiet: true });
            } while (refreshAgain && !disposed);
        })().finally(() => { refreshing = null; });
        return refreshing;
    }

    async function loadMore() {
        if (!state.nextCursor || state.more === 'loading') return;
        const read = listRead = new AbortController();
        set({ more: 'loading' });
        try {
            const query = new URLSearchParams({ limit: String(PAGE_LIMIT), cursor: state.nextCursor });
            const page = await api.get(`${REVIEW_URL}?${query}`, { signal: signalOf(read), cache: 'no-store' });
            if (read !== listRead || disposed) return;
            const known = new Set(state.items.map(entry => entry.id));
            const added = (page.items ?? []).filter(entry => !known.has(entry.id)).map(entry => Object.freeze({ ...entry }));
            set({ items: Object.freeze([...state.items, ...added]), total: page.total, nextCursor: page.next_cursor ?? null,
                more: 'idle' });
        } catch (error) {
            if (read !== listRead || disposed || isAbort(error)) return;
            if (RELOAD.has(error?.code)) { await load({ stale: true }); return; }
            set({ more: 'error' });
        }
    }

    async function select(id, { keepWarning = false } = {}) {
        if (!state.items.some(entry => entry.id === id) || state.phase === 'applying') return;
        detailRead?.abort();
        dropPending();
        const read = detailRead = new AbortController();
        set({ selectedId: id, detail: null, detailStatus: 'loading', options: Object.freeze([]), choice: null, prompts: null,
            canRetry: false, ...(keepWarning ? {} : { warning: '' }) });
        try {
            const detail = await api.get(`${REVIEW_URL}/${encodeURIComponent(id)}`, { signal: signalOf(read), cache: 'no-store' });
            if (read !== detailRead || disposed) return;
            const options = contextOptions(detail);
            const chosen = options.find(option => !option.disabled);
            set({ detail: Object.freeze(detail), detailStatus: 'ready', options, choice: chosen.value, prompts: promptsOf(chosen),
                warning: (keepWarning && state.warning) || warningOf(detail) });
        } catch (error) {
            if (read !== detailRead || disposed || isAbort(error)) return;
            if (RELOAD.has(error?.code)) { set({ warning: error.message }); await load({ keepWarning: true, stale: true }); return; }
            set({ detailStatus: 'error', warning: COPY.detailFailed });
        }
    }

    function choose(value) {
        const option = state.options.find(entry => entry.value === value);
        if (!option || option.disabled || state.phase === 'applying') return;
        // Another choice is another decision: an unanswered one is no longer resent.
        if (pending && pending.body.choice !== value) { dropPending(); set({ canRetry: false }); }
        set({ choice: value, prompts: promptsOf(option) });
    }

    /** Send `body`; true once the decision is recorded. */
    async function send(body) {
        const id = state.selectedId;
        const choice = body.choice;
        set({ phase: 'applying', canRetry: false });
        try {
            const result = await api.post(`${REVIEW_URL}/${encodeURIComponent(id)}/decision`, body, { signal: lifetime.signal });
            pending = null;
            set({ phase: 'done', total: result.total, warning: '',
                toast: choice === UNRESOLVED ? COPY.leftUnresolved : COPY.applied });
            return true;
        } catch (error) {
            // Leaving the page mid-request: the server may never see it, so the capture is released here (a release
            // of a capture the server already used is a harmless 404).
            if (disposed || isAbort(error)) { release(body.capture_token); return false; }
            if (isLost(error)) {
                pending = { id, body };
                set({ phase: 'idle', warning: COPY.lost, canRetry: true });
                return false;
            }
            pending = null;
            release(body.capture_token);
            set({ phase: 'idle', warning: error?.message || COPY.failed });
            if (RELOAD.has(error?.code)) await load({ keepWarning: true, stale: true });
            return false;
        }
    }

    async function apply() {
        const { detail, choice } = state;
        if (state.phase !== 'idle' || state.detailStatus !== 'ready' || !detail || !choice) return false;
        // After an unanswered request, Apply resends that same decision once (its capture and body).
        if (pending?.id === state.selectedId && pending.body.choice === choice) return retry();
        dropPending();
        set({ phase: 'applying', warning: '', canRetry: false });
        let capture = null, epoch = detail.library_epoch;
        if (detail.target) {
            try {
                const observed = await api.post(OBSERVE_URL, { target: detail.target }, { signal: lifetime.signal });
                capture = observed.capture;
                epoch = observed.library_epoch;
            } catch (error) {
                if (disposed || isAbort(error)) return false;
                set({ phase: 'idle', warning: error?.message || COPY.failed });
                if (RELOAD.has(error?.code)) await load({ keepWarning: true, stale: true });
                return false;
            }
            if (disposed) { release(capture?.token); return false; }
        }
        return send({
            expected_epoch: epoch, expected_revision: detail.revision, capture_token: capture?.token ?? null,
            sha256: capture?.sha256 ?? detail.sha256, dependency_fingerprint: detail.dependency_fingerprint, choice,
        });
    }

    /** Resend the unanswered decision once, with its own capture; false when there is nothing to resend. */
    async function retry() {
        if (!pending || state.phase !== 'idle' || pending.id !== state.selectedId) return false;
        const { body } = pending;
        pending = null;
        return send(body);
    }

    function leave() {
        if (state.phase === 'applying') return;
        dropPending();
        set({ phase: 'done', toast: null, canRetry: false });
    }

    return {
        get state() { return state; },
        subscribe(listener) {
            listeners.add(listener);
            return () => listeners.delete(listener);
        },
        load: () => load(), refresh, sync, loadMore, select: id => select(id), choose, apply, retry, leave,
        dispose() {
            if (disposed) return;
            dropPending();
            disposed = true;
            lifetime.abort();
            listeners.clear();
        },
    };
}
