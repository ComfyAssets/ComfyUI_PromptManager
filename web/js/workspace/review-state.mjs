/**
 * Needs review state (#249 PR F): the pending queue, the selected item's sampler paths and one explicit decision, over
 * the review routes (`py/api/review.py`).
 *
 * - The queue is read 40 at a time, oldest first; `total` is the exact pending count. The cursor is bound to the
 *   storage epoch only, so Load more keeps working while new items arrive.
 * - Selecting an item reads its detail: one "Sampler <id> · <positive> / <negative>" option per candidate, unusable
 *   ones disabled with the server's reason, the first workable one chosen (rulings R2/R3). "Leave unresolved" is its
 *   own action (#536 D179, `leaveUnresolved()`). With nothing workable `unreadable` says why (D180).
 * - `link(choice)` (#536) records the Library prompt the reviewer chose, under the same capture and retry rules.
 * - A recorded decision opens the item after it (else the one before) and re-reads the queue quietly: the page stays
 *   (#536, overturning ruling R7). `decisions` counts them, for the page's toast.
 * - Apply takes a fresh capture of the file (`observe`), then posts the decision; the server releases a capture it
 *   used. A refused decision releases the capture and keeps the selection with the server's sentence as the warning;
 *   a restored storage or a changed queue reads the queue again. A request that got no answer keeps its capture and
 *   its exact body, and the next Apply of the same choice (`retry()`) resends that body once.
 * - Leave for later writes nothing and opens the next item, wrapping. Tagging the image (the shared Tag dialog) never changes the item.
 * - `refresh()` re-reads the queue quietly when the server says the queue changed (F2: the review change stream:
 *   a scan or the monitor added items, another tab decided one). The open item, its choice and an unanswered decision
 *   stay; when the open item has left the queue the next one opens with a note. Refreshes asked for while one runs
 *   collapse into one more read. Every re-read keeps the loaded depth (F3): it reads page after page until it holds as
 *   many items as were shown, so Load more pages stay and the page does not jump.
 * - `sync(version)` is the stream's `ready`: it re-reads only when the queue read last is older than the version the
 *   stream names (`{library_epoch, review_revision}`, the pair every list read answers), so a visit reads its queue
 *   once (F3). An unknown version re-reads.
 * - A change heard while a decision is in flight is owed: it is read once the decision is refused or unanswered (a
 *   recorded one leaves the page). Load more asked for during a re-read waits for it and continues from the queue it
 *   read; a re-read started during Load more takes the click over and reads that page too.
 * - Apply fences on the epoch of the detail it decides: an observe under another storage releases its capture and
 *   reads the queue and the item again. An unanswered decision that a re-read shows landed finishes as recorded.
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
    limited: 'This file has too many sampler paths to list. Link it to a prompt or leave it unresolved.',
    // #536 D180: nothing to apply.
    unreadable: count => `${count === 1 ? "This file's sampler path" : `None of this file's ${count} sampler paths`} could be traced to a complete prompt, so there is nothing to apply. Choose the prompt yourself, or leave it unresolved.`,
    lostLink: 'The link may not have been saved. Press Link image again to send the same choice once more.',
    unavailable: 'This file is not under a current image source, so it cannot be opened. You can leave it unresolved.',
    lost: 'The decision may not have been saved. Apply again to send the same decision once more.',
    storageChanged: 'Prompt storage was restored; reload the review queue.',
    // D119 (F2): the open item left the queue in a re-read, and a failed re-read.
    decidedElsewhere: name => `${name} was decided elsewhere, so the next image is open.`,
    refreshFailed: 'The review queue could not be read again. Reload the page to see the latest images.',
    failed: 'The decision could not be saved. Try again.',
    applied: 'Association accepted.',
    leftUnresolved: 'Left unresolved.',
    linked: title => `Linked to ${title}.`,
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

/** The Sampler context options of a detail: its candidates, unusable ones disabled with the server's reason. */
export function contextOptions(detail) {
    const usable = detail.target !== null && !detail.unavailable;
    return Object.freeze((Array.isArray(detail.candidates) ? detail.candidates : []).map(candidate => {
        const readable = workable(candidate);
        return Object.freeze({
            value: candidate.hash, candidate, disabled: !usable || !readable,
            label: readable || !candidate.reason ? optionLabel(candidate) : `${optionLabel(candidate)} (${candidate.reason})`,
        });
    }));
}

/**
 * When no candidate of an available, listed file can be applied: their count and each distinct reason with its count
 * (first seen first). Null otherwise.
 */
export function unreadableOf(detail) {
    const candidates = Array.isArray(detail.candidates) ? detail.candidates : [];
    if (detail.limited || detail.unavailable || detail.target === null || candidates.some(workable)) return null;
    const reasons = new Map();
    for (const { reason } of candidates) if (reason) reasons.set(reason, (reasons.get(reason) ?? 0) + 1);
    return Object.freeze({ count: candidates.length,
        reasons: Object.freeze([...reasons].map(([reason, count]) => Object.freeze({ reason, count }))) });
}

/** The prompt blocks of the chosen option: its two role texts, or nothing without a choice. */
function promptsOf(option) {
    if (!option?.candidate) return null;
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
    unreadable: null, phase: 'idle', warning: '', canRetry: false, toast: null, decisions: 0,
});

/**
 * @param {{api: {get: Function, post: Function, del: Function}}} options
 * @returns the controller: `state`, `subscribe(listener)`, `load()`, `refresh()`, `loadMore()`, `select(id)`, `choose(value)`,
 *   `sync(version)`, `apply()`, `leaveUnresolved()`, `link(choice)`, `retry()`, `leave()`, `dispose()`.
 */
export function createReviewController({ api }) {
    let state = Object.freeze(initial());
    const listeners = new Set();
    const lifetime = new AbortController();
    let listRead = null, detailRead = null, pending = null, disposed = false;
    let refreshing = null, refreshAgain = false;
    // A change heard while a decision was in flight (I1); read when the phase is idle again.
    let owedRefresh = false;
    // Load more's own read, the one waiting for a re-read, and a click a re-read took over (I2).
    let moreRead = null, moreTask = null, moreOwed = false, listing = null;
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
    function load(options) {
        const run = listing = loadQueue(options);
        run.finally(() => { if (listing === run) listing = null; });
        return run;
    }

    async function loadQueue({ keepWarning = false, stale = false, quiet = false } = {}) {
        listRead?.abort();
        if (moreRead) { moreRead.abort(); moreRead = null; moreOwed = true; }
        const read = listRead = new AbortController();
        if (!quiet) set({ status: state.items.length ? state.status : 'loading', ...(keepWarning ? {} : { warning: '' }) });
        let readOk = false;
        try {
            // One page, or as many as Load more had shown (and one page more for a click this read took over).
            const depth = state.items.length + (moreOwed ? PAGE_LIMIT : 0);
            const queue = await readQueue(read, depth > PAGE_LIMIT ? depth : 0);
            if (read !== listRead || disposed) return;
            moreOwed = false;
            // M3: another storage (a restore) makes the open item's detail stale too.
            if (state.epoch !== null && queue.first.library_epoch !== state.epoch) stale = true;
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
            if (quiet && left && !keep && pending?.id === left.id) {
                // M5: the decision that got no answer was recorded (the server used its capture).
                const { toast } = pending;
                pending = null;
                set({ warning: '', canRetry: false, toast, decisions: state.decisions + 1 });
                if (items.length) await select(items[0].id);
                else clearSelection();
            } else if (quiet && left && !keep && items.length) {
                set({ warning: COPY.decidedElsewhere(left.filename) });
                await select(items[0].id, { keepWarning: true });
            } else if (items.length) await select((keep ?? items[0]).id, { keepWarning });
            else clearSelection();
        } catch (error) {
            if (read !== listRead || disposed || isAbort(error)) return;
            // M7: a click this read took over can be made again.
            moreOwed = false;
            if (quiet && state.status === 'ready') set({ warning: COPY.refreshFailed, more: 'idle' });
            else set({ status: 'error', more: 'idle' });
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

    function clearSelection() {
        set({ selectedId: null, detail: null, detailStatus: 'idle', options: Object.freeze([]), choice: null, prompts: null,
            unreadable: null });
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
                // A decision in flight finishes first: the page leaves when it is recorded, and a refused or
                // unanswered one reads the owed change (I1).
                if (state.phase === 'applying') { owedRefresh = true; return; }
                await load({ keepWarning: true, quiet: true });
            } while (refreshAgain && !disposed);
        })().finally(() => { refreshing = null; });
        return refreshing;
    }

    function loadMore() {
        if (moreTask) return moreTask;
        if (!state.nextCursor || state.more === 'loading') return Promise.resolve();
        set({ more: 'loading' });
        moreTask = readMore().finally(() => { moreTask = null; });
        return moreTask;
    }

    async function readMore() {
        // I2: a re-read in flight finishes first; the next page continues after the queue it read.
        while ((refreshing || listing) && !disposed) await (refreshing ?? listing);
        if (disposed || !state.nextCursor || state.status !== 'ready') {
            if (state.more === 'loading') set({ more: 'idle' });
            return;
        }
        if (state.more !== 'loading') set({ more: 'loading' });
        const read = moreRead = new AbortController();
        try {
            const query = new URLSearchParams({ limit: String(PAGE_LIMIT), cursor: state.nextCursor });
            const page = await api.get(`${REVIEW_URL}?${query}`, { signal: signalOf(read), cache: 'no-store' });
            if (read !== moreRead || disposed) return;
            moreRead = null;
            const known = new Set(state.items.map(entry => entry.id));
            const added = (page.items ?? []).filter(entry => !known.has(entry.id)).map(entry => Object.freeze({ ...entry }));
            set({ items: Object.freeze([...state.items, ...added]), total: page.total, nextCursor: page.next_cursor ?? null,
                more: 'idle' });
        } catch (error) {
            if (read !== moreRead || disposed || isAbort(error)) return;
            moreRead = null;
            if (RELOAD.has(error?.code)) { set({ more: 'idle' }); await load({ stale: true }); return; }
            set({ more: 'error' });
        }
    }

    async function select(id, { keepWarning = false } = {}) {
        if (!state.items.some(entry => entry.id === id) || state.phase === 'applying') return;
        detailRead?.abort();
        dropPending();
        const read = detailRead = new AbortController();
        set({ selectedId: id, detail: null, detailStatus: 'loading', options: Object.freeze([]), choice: null, prompts: null,
            unreadable: null, canRetry: false, ...(keepWarning ? {} : { warning: '' }) });
        try {
            const detail = await api.get(`${REVIEW_URL}/${encodeURIComponent(id)}`, { signal: signalOf(read), cache: 'no-store' });
            if (read !== detailRead || disposed) return;
            const options = contextOptions(detail);
            const chosen = options.find(option => !option.disabled);
            set({ detail: Object.freeze(detail), detailStatus: 'ready', options, choice: chosen?.value ?? null,
                prompts: promptsOf(chosen), unreadable: unreadableOf(detail), warning: (keepWarning && state.warning) || warningOf(detail) });
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
        if (pending && pending.key !== value) { dropPending(); set({ canRetry: false }); }
        set({ choice: value, prompts: promptsOf(option) });
    }

    /** The phase is idle again after a refused or unanswered decision: read a change heard meanwhile (I1). */
    function idleAgain(reloading) {
        const owed = owedRefresh;
        owedRefresh = false;
        if (owed && !reloading && !disposed) refresh();
    }

    /** The item recorded: it leaves the page's queue, the one after it opens and the queue is read again. */
    async function advance(id, total, toast) {
        const index = state.items.findIndex(entry => entry.id === id);
        const items = Object.freeze(state.items.filter(entry => entry.id !== id));
        const next = items[index] ?? items[index - 1] ?? null;
        set({ phase: 'idle', items, total, warning: '', toast, decisions: state.decisions + 1 });
        owedRefresh = false;
        if (next) await select(next.id);
        else clearSelection();
        // Not refresh(): an answer already on its way may predate the decision, and this read aborts it.
        await load({ keepWarning: true, quiet: true });
    }

    /** Send one decision; true once it is recorded. */
    async function send({ path, body, key, toast, lost = COPY.lost }) {
        const id = state.selectedId;
        set({ phase: 'applying', canRetry: false });
        try {
            const result = await api.post(`${REVIEW_URL}/${encodeURIComponent(id)}/${path}`, body, { signal: lifetime.signal });
            pending = null;
            await advance(id, result.total, toast);
            return true;
        } catch (error) {
            // Leaving the page mid-request: the server may never see it, so the capture is released here (a release
            // of a capture the server already used is a harmless 404).
            if (disposed || isAbort(error)) { release(body.capture_token); return false; }
            if (isLost(error)) {
                pending = { id, path, body, key, toast, lost };
                set({ phase: 'idle', warning: lost, canRetry: true });
                idleAgain(false);
                return false;
            }
            pending = null;
            release(body.capture_token);
            set({ phase: 'idle', warning: error?.message || COPY.failed });
            idleAgain(RELOAD.has(error?.code));
            if (RELOAD.has(error?.code)) await load({ keepWarning: true, stale: true });
            return false;
        }
    }

    /** A fresh capture of the open file, or null when there is no target; `false` when the decision must stop. */
    async function capture(detail) {
        if (!detail.target) return null;
        let observed;
        try {
            // M4: not aborted on leave, so the capture it answers can be released.
            observed = await api.post(OBSERVE_URL, { target: detail.target });
        } catch (error) {
            if (disposed || isAbort(error)) return false;
            set({ phase: 'idle', warning: error?.message || COPY.failed });
            idleAgain(RELOAD.has(error?.code));
            if (RELOAD.has(error?.code)) await load({ keepWarning: true, stale: true });
            return false;
        }
        const taken = observed.capture;
        if (disposed) { release(taken?.token); return false; }
        // M3: the file was observed under another storage than the detail was read from.
        if (observed.library_epoch !== detail.library_epoch) {
            release(taken?.token);
            set({ phase: 'idle', warning: COPY.storageChanged });
            idleAgain(true);
            await load({ keepWarning: true, stale: true });
            return false;
        }
        return taken ?? null;
    }

    /** One decision from the idle page: resend an unanswered same one, else capture and send. */
    async function decide({ key, path, fields, toast, lost }) {
        const { detail } = state;
        if (state.phase !== 'idle' || state.detailStatus !== 'ready' || !detail) return false;
        // After an unanswered request, the same decision resends that body once (its capture and body).
        if (pending?.id === state.selectedId && pending.key === key) return retry();
        dropPending();
        set({ phase: 'applying', warning: '', canRetry: false });
        const taken = await capture(detail);
        if (taken === false) return false;
        return send({ path, key, toast, lost, body: {
            expected_epoch: detail.library_epoch, expected_revision: detail.revision, capture_token: taken?.token ?? null,
            sha256: taken?.sha256 ?? detail.sha256, dependency_fingerprint: detail.dependency_fingerprint, ...fields,
        } });
    }

    function apply() {
        const { choice } = state;
        if (!choice) return Promise.resolve(false);
        return decide({ key: choice, path: 'decision', fields: { choice }, toast: COPY.applied });
    }

    function leaveUnresolved() {
        return decide({ key: UNRESOLVED, path: 'decision', fields: { choice: UNRESOLVED }, toast: COPY.leftUnresolved });
    }

    /** Link item `itemId`, which must still be the open one, to prompt `positiveId` with recipe `pairId` (null: no negative). */
    function link({ itemId, positiveId, pairId, title }) {
        if (state.selectedId !== itemId || !state.detail?.target) return Promise.resolve(false);
        return decide({ key: `link:${positiveId}:${pairId}`, path: 'link', toast: COPY.linked(title), lost: COPY.lostLink,
            fields: { positive_id: positiveId, pair_id: pairId } });
    }

    /** Resend the unanswered decision once, with its own capture; false when there is nothing to resend. */
    async function retry() {
        if (!pending || state.phase !== 'idle' || pending.id !== state.selectedId) return false;
        const decision = pending;
        pending = null;
        return send(decision);
    }

    /** Leave for later: nothing is written; the next item opens, wrapping. */
    function leave() {
        if (state.phase === 'applying') return Promise.resolve();
        dropPending();
        const index = state.items.findIndex(entry => entry.id === state.selectedId);
        const next = state.items[index + 1] ?? state.items[0];
        if (!next || next.id === state.selectedId) { set({ canRetry: false, warning: '' }); return Promise.resolve(); }
        return select(next.id);
    }

    return {
        get state() { return state; },
        subscribe(listener) {
            listeners.add(listener);
            return () => listeners.delete(listener);
        },
        load: () => load(), refresh, sync, loadMore, select: id => select(id), choose, apply, leaveUnresolved, link, retry, leave,
        dispose() {
            if (disposed) return;
            dropPending();
            disposed = true;
            lifetime.abort();
            listeners.clear();
        },
    };
}
