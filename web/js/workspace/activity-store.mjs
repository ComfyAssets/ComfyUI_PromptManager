/**
 * The Activity store (#249 Activity-1): this server session's jobs, for the top-bar button and pill and the Activity
 * page. One `GET /prompt_manager/jobs` read, then one SSE stream (`/jobs/<id>/events`) per running job.
 *
 * - A dropped stream re-reads the list after a backoff that doubles from 1 s and is capped at 30 s, so a stopped
 *   server never sees a retry storm. A successful stream message resets the backoff.
 * - While the tab is visible the list is read again every 15 s, so a job started elsewhere (another tab, the legacy
 *   admin page) appears without a reload.
 * - Jobs live only in server memory. A running job the server no longer lists (ComfyUI restarted), or one whose
 *   server cannot be read at all, is shown as ended (`lost`), so no pill stays stuck.
 * - Toasts announce what happened; Activity is where progress lives. "Scan started." follows this tab's own start;
 *   the finish toast follows any job this store saw running when it ends.
 */
import { showWorkspaceToast } from './toast.mjs';
import { noun } from './plural.mjs';

export const JOBS_URL = '/prompt_manager/jobs';
export const BACKOFF_FIRST_MS = 1000;
export const BACKOFF_MAX_MS = 30000;
export const POLL_MS = 15000;
export const LOST_AFTER_FAILURES = 2;
export const LOST_DETAIL = 'This job ended when ComfyUI restarted.';
const SERVER_STATES = new Set(['running', 'finished', 'cancelled', 'failed']);
const FINAL_STATES = new Set(['finished', 'cancelled', 'failed']);
const JOB_ID = /^[0-9a-f]{32}$/;
// The pill's verb for each job kind; a kind without one shows its title (D89: image tagging jobs read "Tagging").
const PILL_VERBS = Object.freeze({ scan: 'Scanning', 'image-tags': 'Tagging' });
const START_TOASTS = Object.freeze({ scan: 'Scan started.' });

/** The scan's job view (D56): its counter tiles `[group, label, tone]` and outcome list in the mockup's orders. */
const SCAN_VIEW = Object.freeze({
    tiles: Object.freeze([
        ['added', 'Added to Library', 'added'], ['known', 'Already known', ''], ['review', 'Needs review', 'warn'],
        ['no_metadata', 'No usable metadata', ''], ['errors', 'Errors', 'bad'],
    ].map(Object.freeze)),
    // What got added and what went wrong first.
    outcomes: Object.freeze([
        ['added', 'Added to Library'], ['review', 'Needs review'], ['errors', 'Errors'],
        ['no_metadata', 'No usable metadata'], ['known', 'Already known'],
    ].map(Object.freeze)),
    // Rows that open the file in the viewer (D57); the others are plain text.
    openable: Object.freeze(new Set(['added', 'known'])),
    log: 'Scan log',
    cancel: 'Cancel scan',
});

/**
 * Each job kind's view: its tiles, outcome list, openable rows, log label and cancel label. D89: an image tagging job
 * (`image-tags`) counts Tagged (green), Unchanged, Skipped (warning) and Errors (red), lists what got tagged and what
 * went wrong first, and opens Tagged and Unchanged rows in the viewer like the scan's (D57).
 */
export const JOB_VIEWS = Object.freeze({
    scan: SCAN_VIEW,
    'image-tags': Object.freeze({
        tiles: Object.freeze([['tagged', 'Tagged', 'added'], ['unchanged', 'Unchanged', ''], ['skipped', 'Skipped', 'warn'],
            ['errors', 'Errors', 'bad']].map(Object.freeze)),
        outcomes: Object.freeze([['tagged', 'Tagged'], ['errors', 'Errors'], ['skipped', 'Skipped'],
            ['unchanged', 'Unchanged']].map(Object.freeze)),
        openable: Object.freeze(new Set(['tagged', 'unchanged'])),
        log: 'Tagging log',
        cancel: 'Cancel tagging',
    }),
});
// Another kind keeps the scan's tiles and outcomes under the generic labels.
const GENERIC_VIEW = Object.freeze({ ...SCAN_VIEW, log: 'Job log', cancel: 'Cancel job' });

/** The view of a job `kind`; an unknown kind gets the generic one. */
export function jobView(kind) {
    return Object.hasOwn(JOB_VIEWS, kind) ? JOB_VIEWS[kind] : GENERIC_VIEW;
}

/** The wait before the `attempt`-th re-read (0-based): 1 s, 2 s, 4 s … never more than 30 s. */
export function backoffDelay(attempt) {
    const step = Number.isSafeInteger(attempt) && attempt > 0 ? Math.min(attempt, 16) : 0;
    return Math.min(BACKOFF_MAX_MS, BACKOFF_FIRST_MS * 2 ** step);
}

const count = value => Number.isSafeInteger(value) && value >= 0 ? value : 0;
const time = value => typeof value === 'number' && Number.isFinite(value) ? value : null;
const plainObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);

/** A job summary from the API, or null when it is not one (unknown state, bad id). */
export function parseJob(raw) {
    if (!plainObject(raw) || typeof raw.id !== 'string' || !JOB_ID.test(raw.id) || !SERVER_STATES.has(raw.state)) return null;
    const counters = {};
    if (plainObject(raw.counters)) for (const [key, value] of Object.entries(raw.counters)) counters[key] = count(value);
    // What the job's detail lists per outcome, and its log length: a view re-reads the detail only when they change.
    const listed = {};
    if (plainObject(raw.listed)) for (const [key, value] of Object.entries(raw.listed)) listed[key] = count(value);
    const progress = typeof raw.progress === 'number' && Number.isFinite(raw.progress) ? Math.round(raw.progress) : 0;
    return {
        id: raw.id,
        kind: typeof raw.kind === 'string' ? raw.kind : '',
        title: typeof raw.title === 'string' && raw.title.trim() ? raw.title : 'Background job',
        state: raw.state,
        started_at: time(raw.started_at),
        finished_at: time(raw.finished_at),
        progress: Math.max(0, Math.min(100, progress)),
        status_line: typeof raw.status_line === 'string' ? raw.status_line : '',
        processed: count(raw.processed),
        total: count(raw.total),
        counters,
        scope: plainObject(raw.scope) ? { ...raw.scope } : {},
        listed,
        log_length: count(raw.log_length),
        // A held legacy-page job cannot be cancelled; an older server sends no key (cancellable then).
        cancellable: raw.cancellable !== false,
    };
}

// Grouped as the server writes its status lines ("8,240 of 19,622"), whatever the browser's locale.
const number = value => value.toLocaleString('en-US');
const errorsOf = job => `${number(job.counters.errors ?? 0)} ${noun(job.counters.errors ?? 0, 'error')}`;
/** "1,420 added". */
export const addedOf = job => `${number(job.counters.added ?? 0)} added`;

/**
 * The toast when a job ends: "Scan finished · N added · M errors", "Scan cancelled · N added"; an image tagging job
 * the same way with its own title and count (D89): "Auto-tag images finished · N tagged · M errors".
 */
export function finishToast(job) {
    if (job.kind === 'scan') {
        if (job.state === 'cancelled') return `Scan cancelled · ${addedOf(job)}`;
        if (job.state === 'failed') return `Scan failed · ${addedOf(job)} · ${errorsOf(job)}`;
        return `Scan finished · ${addedOf(job)} · ${errorsOf(job)}`;
    }
    if (job.kind === 'image-tags') {
        if (job.state === 'cancelled') return `${job.title} cancelled · ${taggedOf(job)}`;
        return `${job.title} ${job.state === 'failed' ? 'failed' : 'finished'} · ${outcomeLine(job)}`;
    }
    if (job.state === 'cancelled') return `${job.title} was cancelled.`;
    if (job.state === 'failed') return `${job.title} failed.`;
    return `${job.title} finished.`;
}

// D85: an image tagging job says which of its two modes started.
const IMAGE_TAG_START_TOASTS = Object.freeze({ add: 'Tagging started.', auto: 'Auto-tagging started.' });

/** The toast after this tab starts a job. */
export function startToast(job) {
    if (job.kind === 'image-tags' && IMAGE_TAG_START_TOASTS[job.scope.mode]) return IMAGE_TAG_START_TOASTS[job.scope.mode];
    return START_TOASTS[job.kind] ?? `${job.title} started.`;
}

/** The pill's text: "Scanning · 42% · 8,240 / 19,622"; the file counts only once the total is known. */
export function pillParts(job) {
    const label = `${PILL_VERBS[job.kind] ?? job.title} · ${job.progress}%`;
    const files = job.total > 0 ? ` · ${number(job.processed)} / ${number(job.total)}` : '';
    return { label, files, text: label + files };
}

/** "12 tagged" (D89). */
export const taggedOf = job => `${number(job.counters.tagged ?? 0)} tagged`;

/** The counters as one line: "N added · M errors" for a scan, "N tagged · M errors" for image tagging (D89). */
export function outcomeLine(job) {
    if (job.kind === 'scan') return `${addedOf(job)} · ${errorsOf(job)}`;
    if (job.kind === 'image-tags') return `${taggedOf(job)} · ${errorsOf(job)}`;
    return '';
}

function defaultConnect(url) {
    const resolve = globalThis.ApiClient?.resolveUrl ?? (value => value);
    return new EventSource(resolve(url));
}

/**
 * @param {{api: {get: Function, post: Function}, scope: {signal: AbortSignal, cleanup: Function},
 *   connect?: (url: string) => {onmessage: Function|null, onerror: Function|null, close: Function},
 *   notify?: (message: string) => void, timers?: {setTimeout: Function, clearTimeout: Function},
 *   pollMs?: number, isVisible?: () => boolean, lifecycle?: EventTarget}} options
 *
 * `lifecycle` (the window) ends the store's work on `pagehide`: the periodic and backoff reads stop, the streams close
 * and a read in flight is aborted, so nothing answers into a page that is going away (a closed tab, a test teardown).
 * A `pageshow` from the back-forward cache reads the list again.
 */
export function createActivityStore({ api, scope, connect = defaultConnect, notify = showWorkspaceToast,
    timers = globalThis, pollMs = POLL_MS, isVisible = () => globalThis.document?.visibilityState !== 'hidden',
    lifecycle = globalThis.window ?? null } = {}) {
    let jobs = [];
    let status = 'idle';
    let attempt = 0;
    // Failed list reads in a row: one (a proxy's 502, a dropped poll) is not yet a restart.
    let failures = 0;
    let retryTimer = null;
    let pollTimer = null;
    let reads = 0;
    let disposed = false;
    let suspended = false;
    let reading = null;
    const streams = new Map();
    const listeners = new Set();

    const emit = () => { for (const listener of [...listeners]) listener(store); };
    const find = id => jobs.find(job => job.id === id);

    function announce(previous, next) {
        if (previous?.state === 'running' && FINAL_STATES.has(next.state)) notify(finishToast(next));
    }

    function closeStream(id) {
        const source = streams.get(id);
        if (!source) return;
        streams.delete(id);
        source.onmessage = null;
        source.onerror = null;
        source.close();
    }

    function follow() {
        for (const id of [...streams.keys()]) if (find(id)?.state !== 'running') closeStream(id);
        for (const job of jobs) if (job.state === 'running' && !streams.has(job.id)) openStream(job.id);
    }

    function openStream(id) {
        const source = connect(`${JOBS_URL}/${id}/events`);
        streams.set(id, source);
        source.onmessage = event => {
            let job;
            try { job = parseJob(JSON.parse(event.data)); } catch { job = null; }
            if (!job || job.id !== id) return;
            attempt = 0;
            upsert(job);
        };
        source.onerror = () => {
            closeStream(id);
            if (find(id)?.state === 'running') scheduleResync({ drop: true });
        };
    }

    /** A job never runs again once it has ended, whatever an older read in flight still says. */
    const settled = (previous, next) => FINAL_STATES.has(previous?.state) && next.state === 'running' ? previous : next;

    function upsert(job) {
        const previous = find(job.id);
        job = settled(previous, job);
        jobs = previous ? jobs.map(item => item.id === job.id ? job : item) : [job, ...jobs];
        announce(previous, job);
        follow();
        emit();
    }

    /**
     * Replace the jobs with the server's list. A running job it no longer has ended with the server and stays as
     * ended; a finished job it no longer lists was pruned there and is dropped here too.
     */
    function merge(list) {
        const next = list.map(parseJob).filter(Boolean).map(job => settled(find(job.id), job));
        const ids = new Set(next.map(job => job.id));
        const carried = jobs.filter(job => !ids.has(job.id) && (job.state === 'running' || job.state === 'lost'))
            .map(job => ({ ...job, state: 'lost' }));
        for (const job of next) announce(find(job.id), job);
        jobs = [...next, ...carried];
        follow();
    }

    function markLost() {
        jobs = jobs.map(job => job.state === 'running' ? { ...job, state: 'lost' } : job);
        follow();
    }

    /** Re-read after the backoff; `drop` marks a re-read for a dropped stream, which keeps the backoff growing. */
    function scheduleResync({ drop = false } = {}) {
        if (disposed || suspended || retryTimer !== null) return;
        const delay = backoffDelay(attempt);
        attempt += 1;
        retryTimer = timers.setTimeout(() => {
            retryTimer = null;
            load({ drop });
        }, delay);
    }

    /** The next periodic read; a hidden tab skips its turn. */
    function schedulePoll() {
        if (disposed || suspended || pollMs <= 0) return;
        if (pollTimer !== null) timers.clearTimeout(pollTimer);
        pollTimer = timers.setTimeout(() => {
            pollTimer = null;
            if (isVisible()) load();
            else schedulePoll();
        }, pollMs);
    }

    /** Read this session's jobs. A failed read marks running jobs ended and tries again after the backoff. */
    async function load({ drop = false } = {}) {
        if (disposed || suspended) return;
        const read = ++reads;
        // Only the newest read applies, so an older one still in flight is aborted.
        reading?.abort();
        const controller = new AbortController();
        reading = controller;
        try {
            const result = await api.get(JOBS_URL, { signal: controller.signal, cache: 'no-store' });
            // An older read answering late is dropped.
            if (disposed || suspended || read !== reads) return;
            if (!Array.isArray(result?.jobs)) throw new TypeError('Invalid job list');
            merge(result.jobs);
            status = 'ready';
            // A good read ends the backoff (the server is back, or nothing dropped), except for a stream that keeps
            // dropping while the list reads fine: that waits longer each time, and a received message resets it.
            if (failures > 0 || !drop) attempt = 0;
            failures = 0;
            schedulePoll();
        } catch (error) {
            if (disposed || suspended || scope.signal?.aborted || controller.signal.aborted || read !== reads) return;
            failures += 1;
            if (failures >= LOST_AFTER_FAILURES) markLost();
            status = 'error';
            scheduleResync();
        }
        emit();
    }

    const store = {
        get status() { return status; },
        jobs: () => [...jobs],
        running: () => jobs.filter(job => job.state === 'running'),
        find,
        load,
        subscribe(listener) {
            listeners.add(listener);
            return () => listeners.delete(listener);
        },
        /** Start a job of `kind`: {job}, or {busy, message} naming the running job that holds its slot. */
        async start(kind, body = {}) {
            try {
                const result = await api.post(`${JOBS_URL}/${encodeURIComponent(kind)}`, body, { signal: scope.signal });
                const job = parseJob(result?.job);
                if (!job) throw new TypeError('Invalid job');
                upsert(job);
                notify(startToast(job));
                return { job };
            } catch (error) {
                if (error?.status !== 409 || error?.code !== 'busy') throw error;
                await load();
                return { busy: store.running()[0] ?? null, message: error.message };
            }
        },
        /** Ask a running job to stop at its next file boundary; false once it has ended. */
        async cancel(id) {
            const result = await api.post(`${JOBS_URL}/${encodeURIComponent(id)}/cancel`, {}, { signal: scope.signal });
            return result?.cancellation_requested === true;
        },
    };

    /** Stop every timer, stream and read in flight. */
    function halt() {
        if (retryTimer !== null) timers.clearTimeout(retryTimer);
        if (pollTimer !== null) timers.clearTimeout(pollTimer);
        retryTimer = null;
        pollTimer = null;
        reading?.abort();
        reading = null;
        for (const id of [...streams.keys()]) closeStream(id);
    }
    const onPageHide = () => { suspended = true; halt(); };
    const onPageShow = event => {
        if (!suspended || !event.persisted) return;
        suspended = false;
        load();
    };
    lifecycle?.addEventListener('pagehide', onPageHide);
    lifecycle?.addEventListener('pageshow', onPageShow);

    scope.cleanup(() => {
        disposed = true;
        halt();
        lifecycle?.removeEventListener('pagehide', onPageHide);
        lifecycle?.removeEventListener('pageshow', onPageShow);
        listeners.clear();
    });
    return store;
}
