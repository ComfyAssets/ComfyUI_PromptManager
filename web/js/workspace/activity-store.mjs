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
export const LOST_DETAIL = 'This job ended when ComfyUI restarted.';
const SERVER_STATES = new Set(['running', 'finished', 'cancelled', 'failed']);
const FINAL_STATES = new Set(['finished', 'cancelled', 'failed']);
const JOB_ID = /^[0-9a-f]{32}$/;
// The pill's verb for each job kind; a kind without one shows its title.
const PILL_VERBS = Object.freeze({ scan: 'Scanning' });
const START_TOASTS = Object.freeze({ scan: 'Scan started.' });

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
    };
}

const number = value => value.toLocaleString();
const errorsOf = job => `${number(job.counters.errors ?? 0)} ${noun(job.counters.errors ?? 0, 'error')}`;
/** "1,420 added". */
export const addedOf = job => `${number(job.counters.added ?? 0)} added`;

/** The toast when a job ends: "Scan finished · N added · M errors", "Scan cancelled · N added". */
export function finishToast(job) {
    if (job.kind === 'scan') {
        if (job.state === 'cancelled') return `Scan cancelled · ${addedOf(job)}`;
        if (job.state === 'failed') return `Scan failed · ${addedOf(job)} · ${errorsOf(job)}`;
        return `Scan finished · ${addedOf(job)} · ${errorsOf(job)}`;
    }
    if (job.state === 'cancelled') return `${job.title} was cancelled.`;
    if (job.state === 'failed') return `${job.title} failed.`;
    return `${job.title} finished.`;
}

/** The toast after this tab starts a job. */
export function startToast(job) {
    return START_TOASTS[job.kind] ?? `${job.title} started.`;
}

/** The pill's text: "Scanning · 42% · 8,240 / 19,622"; the file counts only once the total is known. */
export function pillParts(job) {
    const label = `${PILL_VERBS[job.kind] ?? job.title} · ${job.progress}%`;
    const files = job.total > 0 ? ` · ${number(job.processed)} / ${number(job.total)}` : '';
    return { label, files, text: label + files };
}

/** The scan's counters as one line: "N added · M errors"; other kinds have none yet. */
export function outcomeLine(job) {
    return job.kind === 'scan' ? `${addedOf(job)} · ${errorsOf(job)}` : '';
}

function defaultConnect(url) {
    const resolve = globalThis.ApiClient?.resolveUrl ?? (value => value);
    return new EventSource(resolve(url));
}

/**
 * @param {{api: {get: Function, post: Function}, scope: {signal: AbortSignal, cleanup: Function},
 *   connect?: (url: string) => {onmessage: Function|null, onerror: Function|null, close: Function},
 *   notify?: (message: string) => void, timers?: {setTimeout: Function, clearTimeout: Function},
 *   pollMs?: number, isVisible?: () => boolean}} options
 */
export function createActivityStore({ api, scope, connect = defaultConnect, notify = showWorkspaceToast,
    timers = globalThis, pollMs = POLL_MS, isVisible = () => globalThis.document?.visibilityState !== 'hidden' } = {}) {
    let jobs = [];
    let status = 'idle';
    let attempt = 0;
    let retryTimer = null;
    let pollTimer = null;
    let reads = 0;
    let disposed = false;
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
            if (find(id)?.state === 'running') scheduleResync();
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

    /** Replace the jobs with the server's list; a running job it no longer has has ended with the server. */
    function merge(list) {
        const next = list.map(parseJob).filter(Boolean).map(job => settled(find(job.id), job));
        const ids = new Set(next.map(job => job.id));
        const carried = jobs.filter(job => !ids.has(job.id))
            .map(job => job.state === 'running' ? { ...job, state: 'lost' } : job);
        for (const job of next) announce(find(job.id), job);
        jobs = [...next, ...carried];
        follow();
    }

    function markLost() {
        jobs = jobs.map(job => job.state === 'running' ? { ...job, state: 'lost' } : job);
        follow();
    }

    function scheduleResync() {
        if (disposed || retryTimer !== null) return;
        const delay = backoffDelay(attempt);
        attempt += 1;
        retryTimer = timers.setTimeout(() => {
            retryTimer = null;
            load();
        }, delay);
    }

    /** The next periodic read; a hidden tab skips its turn. */
    function schedulePoll() {
        if (disposed || pollMs <= 0) return;
        if (pollTimer !== null) timers.clearTimeout(pollTimer);
        pollTimer = timers.setTimeout(() => {
            pollTimer = null;
            if (isVisible()) load();
            else schedulePoll();
        }, pollMs);
    }

    /** Read this session's jobs. A failed read marks running jobs ended and tries again after the backoff. */
    async function load() {
        if (disposed) return;
        const read = ++reads;
        try {
            const result = await api.get(JOBS_URL, { signal: scope.signal, cache: 'no-store' });
            // Only the newest read applies; an older one answering late is dropped.
            if (disposed || read !== reads) return;
            if (!Array.isArray(result?.jobs)) throw new TypeError('Invalid job list');
            merge(result.jobs);
            status = 'ready';
            schedulePoll();
        } catch (error) {
            if (disposed || scope.signal?.aborted || read !== reads) return;
            markLost();
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

    scope.cleanup(() => {
        disposed = true;
        if (retryTimer !== null) timers.clearTimeout(retryTimer);
        if (pollTimer !== null) timers.clearTimeout(pollTimer);
        retryTimer = null;
        pollTimer = null;
        for (const id of [...streams.keys()]) closeStream(id);
        listeners.clear();
    });
    return store;
}
