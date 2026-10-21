/**
 * One job's Activity view (#249 Activity-1, D56): `#/activity/<id>`, the approved mockup `activity-scan.html` in the
 * concept's classes.
 *
 * - Header: the page heading names the job; its line is the Activity item's (D54), and its actions are Download report
 *   (disabled until Activity-2) and, while the job runs, "Cancel scan" (danger).
 * - Progress (`section.surface`): `<progress>`, the server's "Processing file N of M · about X min left", and the
 *   outcome counters as `dl.stat-row` tiles: Added to Library (green), Already known, Needs review (warning), No usable
 *   metadata, Errors (red).
 * - Results (`section.surface`): the outcome list (`button[aria-pressed]` with counts, in the mockup's order), a path
 *   filter, and the selected outcome's listed rows (at most 200 per outcome, so "Showing N of M"). Rows under Added and
 *   Already known open the file in the existing viewer through its listing identity (D57); the others are plain text.
 * - Log: `details.term` "Scan log", collapsed, holding the raw timestamped lines in `pre[tabindex=0]`.
 *
 * The summary follows the Activity store (one SSE stream per running job); the listed rows and the log are read from
 * `GET /prompt_manager/jobs/<id>` at most once a second while the job runs, and once more when it ends.
 */
import { createButton } from '../shared/toolkit/core.js';
import { pageHeading } from './heading.mjs';
import { showWorkspaceToast } from './toast.mjs';
import { noun } from './plural.mjs';
import { parseJob, JOBS_URL, LOST_DETAIL } from './activity-store.mjs';
import { jobDetail, CANCEL_FAILED_TOAST } from './activity.mjs';
import { createMediaViewer } from './media-viewer.mjs';
import { createInspectionController } from './inspection-state.mjs';
import { createPromptSender } from './prompt-sender.mjs';
import { imageKey } from './images-state.mjs';

export const DETAIL_INTERVAL_MS = 1000;
export const NOT_FOUND = 'This job is not in this session’s Activity. Jobs are kept until ComfyUI restarts.';
export const EMPTY_GROUP = 'No files in this group.';
export const NO_MATCH = 'No listed file matches this filter.';
export const VIEWER_HINT = 'each row opens the image in the viewer';
export const FILE_GONE = 'This file is no longer in Images. Rescan from Import and try again.';
export const OPEN_FAILED = 'The file could not be opened. Try again.';
const LISTING_LIMIT = 100;
const MAX_QUERY = 500;

/** The counter tiles, in the spec's order and the mockup's tones. */
export const TILES = Object.freeze([
    ['added', 'Added to Library', 'added'], ['known', 'Already known', ''], ['review', 'Needs review', 'warn'],
    ['no_metadata', 'No usable metadata', ''], ['errors', 'Errors', 'bad'],
]);
/** The outcome list, in the mockup's order: what got added and what went wrong first. */
export const OUTCOMES = Object.freeze([
    ['added', 'Added to Library'], ['review', 'Needs review'], ['errors', 'Errors'],
    ['no_metadata', 'No usable metadata'], ['known', 'Already known'],
]);
const OPENABLE = new Set(['added', 'known']);

function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

const number = value => value.toLocaleString();
const isText = value => typeof value === 'string';

/** A job's detail read: the summary plus its listed rows per outcome and its log; null when it is not one. */
export function parseDetail(raw) {
    const job = parseJob(raw);
    if (!job) return null;
    const outcomes = {};
    const groups = raw.outcomes !== null && typeof raw.outcomes === 'object' ? raw.outcomes : {};
    for (const [group, rows] of Object.entries(groups)) {
        if (!Array.isArray(rows)) continue;
        outcomes[group] = rows.filter(row => row && isText(row.path) && row.path)
            .map(row => ({ path: row.path, root: isText(row.root) ? row.root : '', detail: isText(row.detail) ? row.detail : '' }));
    }
    const log = Array.isArray(raw.log) ? raw.log.filter(isText) : [];
    return { job, outcomes, log };
}

/** The progress line: the server's while running, then what the job got through. */
export function progressLine(job) {
    const files = `${number(job.processed)} of ${number(job.total)} ${noun(job.total, 'file')}`;
    if (job.state === 'running') return job.status_line || `${job.progress}%`;
    if (job.state === 'finished') return `${files} processed`;
    if (job.state === 'cancelled') return `Cancelled after ${files}`;
    if (job.state === 'lost') return LOST_DETAIL;
    return job.status_line || 'The job stopped on an error.';
}

/** The note under the rows: "Showing N of M" when not every file is listed, and the viewer hint for openable rows. */
export function resultsNote(shown, total, openable) {
    const parts = [];
    if (shown < total) parts.push(`Showing ${number(shown)} of ${number(total)}`);
    if (openable && shown > 0) parts.push(VIEWER_HINT);
    if (!parts.length) return '';
    const text = parts.join(' · ');
    return parts.length === 1 && openable && shown >= total ? `${text[0].toUpperCase()}${text.slice(1)}.` : text;
}

/** Rows whose path (or root label and path) holds the case-folded filter. */
export function filterRows(rows, filter) {
    const wanted = filter.trim().toLocaleLowerCase();
    if (!wanted) return rows;
    return rows.filter(row => `${row.root}/${row.path}`.toLocaleLowerCase().includes(wanted));
}

/** The first outcome with files, so a view opens on what happened; Added when nothing has yet. */
export function defaultOutcome(counters) {
    return OUTCOMES.find(([group]) => (counters[group] ?? 0) > 0)?.[0] ?? 'added';
}

function section(title, id) {
    const node = element('section', undefined, 'surface');
    const heading = element('div', undefined, 'panel-heading');
    const h2 = element('h2', title);
    h2.id = id;
    node.setAttribute('aria-labelledby', id);
    heading.append(h2);
    node.append(heading);
    return { node, heading };
}

/** `#/activity/<id>`: one job's view. */
export function mountActivityJobView(main, { store, scope, api, jobId }) {
    const parts = pageHeading(main);
    const download = createButton('Download report');
    download.disabled = true;
    const cancel = createButton('Cancel scan', 'danger');

    const progressPart = section('Progress', 'job-progress-title');
    const status = element('span', '', 'small');
    progressPart.heading.append(status);
    const bar = element('progress');
    bar.max = 100;
    const stats = element('dl', undefined, 'stat-row');
    const tileValues = new Map();
    for (const [group, label, tone] of TILES) {
        const tile = element('div', undefined, tone ? `stat ${tone}` : 'stat');
        const value = element('dd', '0');
        tile.append(element('dt', label), value);
        tileValues.set(group, value);
        stats.append(tile);
    }
    progressPart.node.append(bar, stats);

    const resultsPart = section('Results', 'job-results-title');
    const search = element('label', undefined, 'search-field');
    const input = element('input');
    input.type = 'search';
    input.placeholder = 'Filter by file path…';
    input.setAttribute('aria-label', 'Filter results');
    search.append(input);
    resultsPart.heading.append(search);
    const outcomes = element('div', undefined, 'outcomes');
    const list = element('div', undefined, 'outcome-list');
    list.setAttribute('role', 'group');
    list.setAttribute('aria-label', 'Outcomes');
    const files = element('div', undefined, 'outcome-files');
    const buttons = new Map();
    for (const [group, label] of OUTCOMES) {
        const button = createButton('');
        const count = element('span', '0');
        button.append(element('span', label), count);
        button.setAttribute('aria-pressed', 'false');
        button.count = count;
        buttons.set(group, button);
        list.append(button);
    }
    outcomes.append(list, files);
    resultsPart.node.append(outcomes);

    const log = element('details', undefined, 'term');
    const summary = element('summary');
    const logLabel = document.createTextNode('');
    summary.append(logLabel, element('span', ' — raw detail, click to show'));
    const pre = element('pre', '');
    pre.tabIndex = 0;
    log.append(summary, pre);

    const missing = element('section', undefined, 'surface');
    const back = element('a', 'All activity');
    back.href = '#/activity';
    missing.append(element('p', NOT_FOUND, 'empty-state'), back);

    let detail = null;
    let selected = null;
    let mounted = false;
    let lastRead = 0;
    let reading = false;
    let readAgain = false;
    let readTimer = null;
    let roots = null;
    let lastState = null;
    const current = () => store.find(jobId) ?? detail?.job ?? null;

    const inspection = createInspectionController({ api });
    scope.cleanup(() => inspection.dispose());
    const viewer = createMediaViewer({ api, container: main, parentScope: scope, inspectionController: inspection,
        sender: createPromptSender({ scope }), onSelect: image => inspection.select(image) });

    function showBody(job) {
        if (mounted) return;
        mounted = true;
        missing.remove();
        parts.actions.append(download, cancel);
        main.append(progressPart.node, resultsPart.node, log);
        selected ??= defaultOutcome(job.counters);
    }

    // Listed rows only ever grow, so a row keeps its node across reads: a focused row (or the viewer's trigger) stays.
    let rowNodes = new Map();
    function rowNode(row, index, openable) {
        const key = `${selected}\n${index}\n${row.root}\n${row.path}`;
        let node = rowNodes.get(key);
        if (!node) {
            node = openable ? createButton('', 'file-row') : element('div', undefined, 'file-row');
            node.append(element('span', row.path, 'path'), element('span', row.detail, 'why'));
            if (openable) node.row = row;
        }
        return [key, node];
    }

    function renderRows(job) {
        const rows = detail?.outcomes[selected] ?? [];
        const openable = OPENABLE.has(selected);
        const wanted = filterRows(rows.map((row, index) => ({ ...row, index })), input.value);
        const kept = new Map(wanted.map(row => rowNode(row, row.index, openable)));
        rowNodes = kept;
        const nodes = [...kept.values()];
        // The counter is exact; at most 200 of its files are listed, and the filter narrows those.
        const total = Math.max(job.counters[selected] ?? 0, rows.length);
        if (!nodes.length) nodes.push(element('p', rows.length ? NO_MATCH : EMPTY_GROUP, 'empty-state'));
        const note = wanted.length ? resultsNote(wanted.length, total, openable) : '';
        if (note) nodes.push(element('p', note, 'small'));
        for (const child of [...files.children]) if (!nodes.includes(child)) child.remove();
        nodes.forEach((node, index) => {
            // Only a node out of place moves, so focus survives a re-read.
            if (files.children[index] !== node) files.insertBefore(node, files.children[index] ?? null);
        });
    }

    function render() {
        const job = current();
        if (!job) return;
        showBody(job);
        const scan = job.kind === 'scan';
        parts.h1.textContent = job.title;
        document.title = `${job.title} · Prompt Manager`;
        parts.description.textContent = jobDetail(job);
        cancel.textContent = scan ? 'Cancel scan' : 'Cancel job';
        if (job.state === 'running') {
            if (!cancel.isConnected) parts.actions.append(cancel);
        } else {
            cancel.remove();
        }
        status.textContent = progressLine(job);
        bar.value = job.progress;
        bar.textContent = `${job.progress}%`;
        bar.setAttribute('aria-label', scan ? 'Scan progress' : `${job.title} progress`);
        for (const [group, value] of tileValues) value.textContent = number(job.counters[group] ?? 0);
        for (const [group, button] of buttons) {
            button.count.textContent = number(job.counters[group] ?? 0);
            button.setAttribute('aria-pressed', String(group === selected));
        }
        logLabel.textContent = `● ${scan ? 'Scan' : 'Job'} log`;
        const text = detail?.log.length ? detail.log.join('\n') : 'No log lines yet.';
        if (pre.textContent !== text) {
            const atEnd = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 2;
            pre.textContent = text;
            if (atEnd) pre.scrollTop = pre.scrollHeight;
        }
        renderRows(job);
    }

    /** Read the rows and the log, at most once a second; a request while one is in flight reads once more after it. */
    function scheduleRead() {
        if (scope.signal.aborted) return;
        if (reading) { readAgain = true; return; }
        if (readTimer !== null) return;
        const wait = Math.max(0, lastRead + DETAIL_INTERVAL_MS - Date.now());
        readTimer = setTimeout(() => { readTimer = null; read(); }, wait);
    }
    scope.cleanup(() => { if (readTimer !== null) clearTimeout(readTimer); });

    async function read() {
        reading = true;
        lastRead = Date.now();
        try {
            const result = await api.get(`${JOBS_URL}/${jobId}`, { signal: scope.signal, cache: 'no-store' });
            if (scope.signal.aborted) return;
            const parsed = parseDetail(result?.job);
            if (parsed && parsed.job.id === jobId) detail = parsed;
        } catch (error) {
            if (scope.signal.aborted) return;
            // An unknown job: the page says so unless the store still knows it (then the rows wait for a later read).
            if (error?.status === 404 && !current()) main.append(missing);
        } finally {
            reading = false;
        }
        render();
        if (readAgain) { readAgain = false; scheduleRead(); }
    }

    function follow() {
        const job = store.find(jobId);
        render();
        if (!job) return;
        // The listed rows change while it runs, and once more as it ends; a job lost with the server cannot be read.
        if (job.state === 'running' || (lastState === 'running' && job.state !== 'lost')) scheduleRead();
        lastState = job.state;
    }

    scope.listen(cancel, 'click', async () => {
        if (cancel.getAttribute('aria-disabled') === 'true') return;
        cancel.setAttribute('aria-disabled', 'true');
        try {
            await store.cancel(jobId);
        } catch {
            if (!scope.signal.aborted) showWorkspaceToast(CANCEL_FAILED_TOAST);
        } finally {
            cancel.removeAttribute('aria-disabled');
        }
    });
    scope.listen(list, 'click', event => {
        const button = event.target.closest('button');
        if (!button) return;
        selected = [...buttons].find(([, node]) => node === button)?.[0] ?? selected;
        render();
    });
    scope.listen(input, 'input', () => { const job = current(); if (job) renderRows(job); });
    scope.listen(files, 'click', event => {
        const button = event.target.closest('button.file-row');
        if (button?.row) openRow(button.row, button);
    });

    /** The listing's own item for a row (its source by label, then its path), so the viewer reads it as Images does. */
    async function listed(row) {
        if (!roots) {
            const data = await api.get('/prompt_manager/gallery/subfolders?mode=workspace', { signal: scope.signal });
            roots = Array.isArray(data?.roots) ? data.roots : [];
        }
        const root = roots.find(item => item?.label === row.root && Number.isSafeInteger(item.index));
        if (!root) return null;
        const query = row.path.length > MAX_QUERY ? row.path.split('/').at(-1).slice(0, MAX_QUERY) : row.path;
        const params = new URLSearchParams({ mode: 'workspace', root: String(root.index), q: query,
            limit: String(LISTING_LIMIT), sort: 'newest' });
        const page = await api.get(`/prompt_manager/images/output?${params}`, { signal: scope.signal });
        return (Array.isArray(page?.images) ? page.images : []).find(item => item?.path === row.path
            && item.root_index === root.index) ?? null;
    }

    async function openRow(row, trigger) {
        if (trigger.getAttribute('aria-disabled') === 'true') return;
        trigger.setAttribute('aria-disabled', 'true');
        try {
            const item = await listed(row);
            if (scope.signal.aborted) return;
            if (!item) { showWorkspaceToast(FILE_GONE); return; }
            viewer.open({ items: [item], selectedKey: imageKey(item), trigger });
        } catch {
            if (!scope.signal.aborted) showWorkspaceToast(OPEN_FAILED);
        } finally {
            trigger.removeAttribute('aria-disabled');
        }
    }

    lastState = store.find(jobId)?.state ?? null;
    scope.cleanup(store.subscribe(follow));
    render();
    read();
    // A job that finished before this page loaded is only in the list read.
    store.load();
}
