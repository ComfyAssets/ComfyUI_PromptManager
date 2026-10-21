/**
 * One job's Activity view (#249 Activity-1, D56): `#/activity/<id>`, the approved mockup `activity-scan.html` in the
 * concept's classes.
 *
 * - Header: the page heading names the job; its line is the Activity item's (D54), and its actions are Download report
 *   (the job's JSON report, D59) and, while the job runs, "Cancel scan" (danger).
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
import { downloadJobReport } from './activity-scan.mjs';
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
const LISTING_LIMIT = 5;

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

// Grouped as the server writes its lines, whatever the browser's locale (as images.mjs does).
const number = value => value.toLocaleString('en-US');
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
            .map(row => ({ path: row.path, root: isText(row.root) ? row.root : '',
                root_index: Number.isSafeInteger(row.root_index) && row.root_index >= 0 ? row.root_index : null,
                detail: isText(row.detail) ? row.detail : '' }));
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
    let logOpened = false;
    // Set once this page's own list read answered, or the detail read said the job is unknown.
    let listedOnce = false;
    let notFound = false;
    // The store is the truth once it has read the list: a job it no longer has was pruned by the server.
    const current = () => store.find(jobId) ?? (listedOnce ? null : detail?.job ?? null);

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

    /** The job is not (or no longer) in this session: say so instead of an empty view. */
    function showMissing() {
        if (mounted) {
            mounted = false;
            const focused = main.contains(document.activeElement) && document.activeElement !== parts.h1;
            download.remove();
            cancel.remove();
            progressPart.node.remove();
            resultsPart.node.remove();
            log.remove();
            parts.h1.textContent = 'Activity';
            parts.description.textContent = '';
            document.title = 'Activity · Prompt Manager';
            if (focused) parts.h1.focus();
        }
        if (!missing.isConnected) main.append(missing);
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
        if (!job) {
            if (listedOnce || notFound) showMissing();
            return;
        }
        showBody(job);
        const scan = job.kind === 'scan';
        parts.h1.textContent = job.title;
        document.title = `${job.title} · Prompt Manager`;
        parts.description.textContent = jobDetail(job);
        cancel.textContent = scan ? 'Cancel scan' : 'Cancel job';
        if (job.state === 'running') {
            if (!cancel.isConnected) parts.actions.append(cancel);
        } else if (cancel.isConnected) {
            // A focused Cancel that goes away hands its focus to the job's heading, never to the page body.
            if (document.activeElement === cancel) parts.h1.focus();
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
            // Replacing the text clamps the scroll position, so keep it: pinned readers follow the end, others stay.
            const { scrollTop } = pre;
            const atEnd = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 2;
            pre.textContent = text;
            pre.scrollTop = atEnd ? pre.scrollHeight : scrollTop;
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
            if (error?.status === 404) notFound = true;
        } finally {
            reading = false;
        }
        render();
        if (readAgain) { readAgain = false; scheduleRead(); }
    }

    /**
     * Whether the detail read would change the page: more listed rows in any outcome, or (with the log open) more log
     * lines. Counters come with the summary, so a job whose lists are capped and whose log is closed is not re-read.
     */
    function detailChanged(job) {
        if (!detail) return true;
        const groups = new Set([...Object.keys(job.listed), ...Object.keys(detail.outcomes)]);
        for (const group of groups) if ((job.listed[group] ?? 0) !== (detail.outcomes[group]?.length ?? 0)) return true;
        return log.open && job.log_length !== detail.log.length;
    }

    function follow() {
        const job = store.find(jobId);
        render();
        if (!job) return;
        // A job lost with the server cannot be read; any other is read while what it lists changes.
        if (job.state !== 'lost' && (job.state === 'running' || lastState === 'running') && detailChanged(job)) scheduleRead();
        lastState = job.state;
    }

    scope.listen(log, 'toggle', () => {
        // A log opened for the first time starts at its newest line.
        if (log.open && !logOpened) { logOpened = true; pre.scrollTop = pre.scrollHeight; }
        const job = store.find(jobId);
        if (log.open && job && job.state !== 'lost' && detailChanged(job)) scheduleRead();
    });

    scope.listen(download, 'click', async () => {
        if (download.getAttribute('aria-disabled') === 'true') return;
        download.setAttribute('aria-disabled', 'true');
        try {
            await downloadJobReport(jobId);
        } finally {
            download.removeAttribute('aria-disabled');
        }
    });
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

    /**
     * The listing's own item for a row, by its exact identity (D57): the root index the scan recorded, still carrying
     * the row's label (so a reordered or replaced source never opens another root's file), and its exact relative path.
     */
    async function listed(row) {
        if (row.root_index === null) return null;
        if (!roots) {
            const data = await api.get('/prompt_manager/gallery/subfolders?mode=workspace', { signal: scope.signal });
            roots = Array.isArray(data?.roots) ? data.roots : [];
        }
        const root = roots.find(item => item?.index === row.root_index);
        if (!root || root.label !== row.root) return null;
        const params = new URLSearchParams({ mode: 'workspace', root: String(root.index), path: row.path,
            limit: String(LISTING_LIMIT) });
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
    // A job that finished before this page loaded is only in the list read; once it answered, the store decides.
    store.load().then(() => {
        if (scope.signal.aborted || store.status !== 'ready') return;
        listedOnce = true;
        render();
    });
}
