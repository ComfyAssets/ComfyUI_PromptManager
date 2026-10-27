/**
 * System › Diagnostics & logs (#249 System S12, #329): the concept's `renderSystem` "diagnostics" body (app.js 694–695)
 * with live data, built with the DOM.
 *
 * - "Diagnostics": "Run checks" opens the shared Health report (D95), as Overview and Maintenance do. The concept's
 *   `div.meta-grid` reads one `GET /system/diagnostics` (Database, Sources, Viewer.js) and the send bridge (Host
 *   renderers, D106's line). "Download diagnostic report" saves a fresh report (the server's listed fields, no secret,
 *   no absolute path) with the renderer line added. "Image-link diagnostics" → Inspect opens the read-only Inspect a
 *   file page. D110 adds "Test image link": the legacy synthetic link, confirmed first, naming the prompt it links.
 * - "Logs": the concept's toolbar (Level, Refresh, Download log) over the in-memory buffer, newest first, as
 *   `HH:MM:SS LEVEL message`. D109 adds: Lines 1–500, all six minimum levels, the statistics line, CRITICAL as a
 *   runtime level, the descriptions that say a runtime value lasts until the server restarts, the retention line, the
 *   log files with their downloads and "Clear all logs" (confirmed). "Runtime log level" and "Console logging" are
 *   runtime drafts saved by the one Save row (`POST /logs/config`). "Review log cleanup" reviews the `rotated_logs`
 *   maintenance plan (D100), which deletes only the rotated copies it listed.
 *
 * Every request goes through the page's api client; every listener sits on the section's `scope`, and a read that
 * answers after the section was left paints nothing.
 */
import { createButton } from '../shared/toolkit/core.js';
import { settingRow, operationRow } from './system-controls.mjs';
import { runHealthAction } from './system-overview.mjs';
import { reviewMissingLinks } from './system-maintenance.mjs';
import { bridgeFactory, rendererText, RENDERER_COPY } from './system-integrations.mjs';
import { closeWorkspaceDialog, openContentDialog } from './workspace-dialogs.mjs';
import { reviewPlan } from './maintenance-review.mjs';
import { backupFilename, saveResponse } from './activity-scan.mjs';
import { showWorkspaceToast } from './toast.mjs';
import { navigateTo } from './state.mjs';
import { noun } from './plural.mjs';

export const REPORT_URL = '/prompt_manager/system/diagnostics';
export const RECENT_URL = '/prompt_manager/recent?limit=1';
export const TEST_LINK_URL = '/prompt_manager/diagnostics/test-link';
export const LOGS_URL = '/prompt_manager/logs';
export const STATS_URL = '/prompt_manager/logs/stats';
export const FILES_URL = '/prompt_manager/logs/files';
export const CONFIG_URL = '/prompt_manager/logs/config';
export const TRUNCATE_URL = '/prompt_manager/logs/truncate';
export const DOWNLOAD_URL = '/prompt_manager/logs/download/';
export const ACTIVE_LOG = 'prompt_manager.log';
export const TEST_LINK_FILE = 'prompt_manager_test_link.png';

export const LOADING = 'Loading…';
export const UNREADABLE = 'Could not be read.';
export const META_TERMS = Object.freeze(['Database', 'Sources', 'Viewer.js', 'Host renderers']);
/** S12-8: a minimum level; `''` asks for every level. */
export const LEVEL_OPTIONS = Object.freeze([['', 'All levels'], ['DEBUG', 'DEBUG'], ['INFO', 'INFO'], ['WARNING', 'WARNING'],
    ['ERROR', 'ERROR'], ['CRITICAL', 'CRITICAL']]);
/** The concept's runtime levels in its order, then CRITICAL (D109). */
export const RUNTIME_LEVELS = Object.freeze(['INFO', 'WARNING', 'DEBUG', 'ERROR', 'CRITICAL']);
export const DEFAULT_LIMIT = 100;
export const MAX_LIMIT = 500;
export const KIND_LABELS = Object.freeze({ active: 'Active log', rotated: 'Rotated copy', link: 'Link, not downloaded',
    other: 'Other file' });

export const COPY = Object.freeze({
    runtimeLevel: 'How much the server writes to the log. Applied until the server restarts.',
    console: 'Print Prompt Manager’s warnings and errors in the ComfyUI console (everything at DEBUG). Applied until the server restarts.',
    inspect: 'Check a file-to-prompt association without changing it.',
    testLink: 'Write a placeholder image record to check that linking works.',
    noPrompt: 'Add a prompt first.',
    promptUnread: 'The most recent prompt could not be read.',
    noEntries: 'No log entries at this level.',
    logUnread: 'The log could not be read.',
    statsUnread: 'The log statistics could not be read.',
    filesUnread: 'The log files could not be read.',
    configUnread: 'The retention settings could not be read.',
    runtimeUnread: 'The current value could not be read.',
    limitHint: 'Lines to show: a whole number from 1 to 500.',
    logDownloadFailed: 'The log could not be downloaded.',
    reportDone: 'Diagnostic report downloaded.',
    reportFailed: 'The diagnostic report could not be downloaded. Nothing was saved to this device.',
    testLinkFailed: 'The test link could not be created. Nothing was changed.',
    clearFailed: 'The logs could not be cleared. Read the log files again before retrying.',
    clearDescription: 'Empty the active log and delete its rotated copies.',
});

const number = value => value.toLocaleString('en-US');

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

/** A byte count as the server's plan labels it: `1000 bytes`, `2.0 KB`, `10.0 MB`, `1.6 GB` (1 KB = 1024 bytes). */
export function sizeText(bytes) {
    const size = Number.isFinite(bytes) && bytes > 0 ? Math.floor(bytes) : 0;
    if (size < 1024) return `${number(size)} ${noun(size, 'byte')}`;
    for (const [unit, scale] of [['KB', 2 ** 10], ['MB', 2 ** 20]]) {
        if (size < scale * 1024) return `${(size / scale).toFixed(1)} ${unit}`;
    }
    return `${(size / 2 ** 30).toFixed(1)} GB`;
}

/** The text for a read that was refused: a `busy` refusal names the running job (D57's naming). */
export function refusalText(error) {
    if (error?.code === 'busy') return `Busy: ${error.running?.title || 'another job'} is running.`;
    return UNREADABLE;
}

/** A `dd` text from a read's state: `{loading}`, `{error}` or `{report}` handed to `value(report)`. */
function stateText(state, value) {
    if (!state || state.loading) return LOADING;
    if (state.error !== undefined) return refusalText(state.error);
    return value(state.report);
}

/** Database: "Readable · 1.6 GB · 19,956 images", or why it is not readable. */
export function databaseText(state) {
    return stateText(state, report => {
        const check = report?.checks?.database;
        if (check?.status !== 'ok') return `Not readable: ${check?.message || 'no answer from the database'}`;
        const images = Number.isSafeInteger(check.image_count) ? check.image_count : 0;
        return `Readable · ${sizeText(report.database_info?.size_bytes)} · ${number(images)} ${noun(images, 'image')}`;
    });
}

/** Sources: the first match of the design's precedence table (§4). */
export function sourcesText(state) {
    return stateText(state, report => {
        const count = key => (Number.isSafeInteger(report?.sources?.[key]) ? report.sources[key] : 0);
        const configured = count('configured');
        const watching = count('watching');
        const unavailable = count('unavailable');
        if (configured === 0) return `ComfyUI output folder · ${watching ? 'watching' : 'manual scan'}`;
        if (unavailable > 0) return `${number(configured)} ${noun(configured, 'source')} · ${number(unavailable)} not available`;
        return `${number(configured)} ${noun(configured, 'source')} · ${number(watching)} watching`;
    });
}

/** Viewer.js: the pinned vendor version the server ships. */
export function viewerText(state) {
    return stateText(state, report => `${report?.viewerjs || 'unknown'} · pinned vendor`);
}

const pad = value => String(value).padStart(2, '0');

/** `prompt-manager-diagnostics-YYYYMMDD-HHMM.json` in local time. */
export function reportFilename(now = new Date()) {
    const day = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}`;
    return `prompt-manager-diagnostics-${day}-${pad(now.getHours())}${pad(now.getMinutes())}.json`;
}

/** The report as saved: the server's fields and the renderer line this page saw (null while it looks). */
export function mergeReport(report, hostRenderers) {
    return { ...report, host_renderers: typeof hostRenderers === 'string' ? hostRenderers : null };
}

/** One buffer entry as the concept prints it: `09:42:18 INFO  message`. */
export function logLine(entry) {
    const time = typeof entry?.timestamp === 'string' ? entry.timestamp.slice(11, 19) : '--:--:--';
    return `${time} ${String(entry?.level ?? '').padEnd(5)} ${entry?.message ?? ''}`;
}

/** Lines: a whole number 1–500 after trimming, else null. */
export function validLimit(value) {
    const text = String(value ?? '').trim();
    if (!/^\d+$/.test(text)) return null;
    const limit = Number(text);
    return limit >= 1 && limit <= MAX_LIMIT ? limit : null;
}

/** The `/logs` request for a level (`''`: every level) and a valid limit. */
export function logsUrl(level, limit) {
    const query = new URLSearchParams({ limit: String(limit) });
    if (level) query.set('level', level);
    return `${LOGS_URL}?${query}`;
}

/** D109: "1,000 entries in memory · 3 errors · 12 warnings · 5 files · 41.2 MB on disk" (errors: ERROR + CRITICAL). */
export function statsText(stats) {
    const counts = stats?.level_counts ?? {};
    const of = key => (Number.isSafeInteger(counts[key]) ? counts[key] : 0);
    const entries = Number.isSafeInteger(stats?.buffer_count) ? stats.buffer_count : 0;
    const errors = of('ERROR') + of('CRITICAL');
    const warnings = of('WARNING');
    const files = Number.isSafeInteger(stats?.log_files_count) ? stats.log_files_count : 0;
    return [`${number(entries)} ${noun(entries, 'entry', 'entries')} in memory`, `${number(errors)} ${noun(errors, 'error')}`,
        `${number(warnings)} ${noun(warnings, 'warning')}`, `${number(files)} ${noun(files, 'file')}`,
        `${sizeText(stats?.total_log_size)} on disk`].join(' · ');
}

const megabytes = bytes => {
    const value = (Number.isFinite(bytes) ? bytes : 0) / 2 ** 20;
    return Number.isInteger(value) ? number(value) : value.toFixed(1);
};

/** D109: the read-only retention values. */
export function retentionText(config) {
    const copies = Number.isSafeInteger(config?.backup_count) ? config.backup_count : 0;
    const buffer = Number.isSafeInteger(config?.buffer_size) ? config.buffer_size : 0;
    return `Rotates at ${megabytes(config?.max_file_size)} MB · keeps ${number(copies)} rotated ${noun(copies, 'copy', 'copies')}`
        + ` · file logging ${config?.file_logging === false ? 'off' : 'on'} · memory buffer ${number(buffer)} ${noun(buffer, 'entry', 'entries')}`;
}

const rotatedOf = files => (Array.isArray(files) ? files : []).filter(row => row?.kind === 'rotated');

/** The concept's line under "Log files & retention": "prompt_manager.log · 1.4 MB · rotated copies: 3". */
export function fileLine(files) {
    const active = (Array.isArray(files) ? files : []).find(row => row?.kind === 'active');
    const size = active ? sizeText(active.size) : 'not written yet';
    return `${ACTIVE_LOG} · ${size} · rotated copies: ${number(rotatedOf(files).length)}`;
}

/** "2026-10-25 14:02" from the server's naive local ISO time. */
export function modifiedText(iso) {
    return typeof iso === 'string' && iso.length >= 16 ? iso.slice(0, 16).replace('T', ' ') : 'unknown time';
}

/** One listed file's line: "<size> · <modified> · <kind>". */
export function fileDetail(row) {
    return `${sizeText(row?.size)} · ${modifiedText(row?.modified)} · ${KIND_LABELS[row?.kind] ?? KIND_LABELS.other}`;
}

/** CUJ 7: what Clear all logs does, from the files read last (null: they could not be read). */
export function clearSentence(files) {
    const tail = 'and clears the lines shown here. The other files in the log folder are kept. This cannot be undone.';
    if (!Array.isArray(files)) return `Empties ${ACTIVE_LOG}, deletes its rotated copies ${tail}`;
    const rotated = rotatedOf(files);
    if (!rotated.length) return `Empties ${ACTIVE_LOG} ${tail}`;
    const size = rotated.reduce((sum, row) => sum + (Number.isFinite(row.size) ? row.size : 0), 0);
    return `Empties ${ACTIVE_LOG}, deletes ${number(rotated.length)} rotated ${noun(rotated.length, 'copy', 'copies')} (${sizeText(size)}) ${tail}`;
}

/** CUJ 7: the toast for `/logs/truncate`'s `results` ({truncated: [names], errors: [messages]}). */
export function clearToast(results) {
    const truncated = Array.isArray(results?.truncated) ? results.truncated : [];
    const deleted = truncated.filter(name => typeof name === 'string' && name.toLowerCase() !== ACTIVE_LOG).length;
    const failed = Array.isArray(results?.errors) ? results.errors.length : 0;
    const parts = ['Logs cleared', `${number(deleted)} rotated ${noun(deleted, 'copy', 'copies')} deleted`];
    if (failed) parts.push(`${number(failed)} could not be deleted`);
    return `${parts.join(' · ')}.`;
}

/** A prompt's short name for the test-link dialog: its first words, on one line. */
export function promptName(prompt) {
    const text = String(prompt?.text ?? '').replace(/\s+/g, ' ').trim();
    return text.length > 60 ? `${text.slice(0, 59)}…` : text;
}

/** CUJ 8: the confirmation, naming the prompt the placeholder record links to. */
export function testLinkSentence(prompt) {
    return `Writes a placeholder image record (${TEST_LINK_FILE}) linked to prompt #${prompt.id} “${promptName(prompt)}”. `
        + 'Maintenance › Missing-file links removes it later.';
}

/** S12-9: the runtime drafts from `GET /logs/config`'s `config`. */
export function runtimeValues(config) {
    return { runtime_level: config?.level, console_logging: config?.console_logging };
}

/** S12-9: the `POST /logs/config` body for the changed runtime drafts only. */
export function runtimeBody(changes) {
    const body = {};
    if (Object.hasOwn(changes ?? {}, 'runtime_level')) body.level = changes.runtime_level;
    if (Object.hasOwn(changes ?? {}, 'console_logging')) body.console_logging = changes.console_logging;
    return body;
}

/** Fetch `url` through the api-client URL and save it under the name the server gave (or `fallback`). */
async function download(url, fallback, fetchUrl) {
    const response = await fetchUrl(url);
    if (!response.ok) throw new Error(`Download failed with ${response.status}`);
    const blob = await response.blob();
    saveResponse(blob, backupFilename(response.headers.get('Content-Disposition'), fallback));
}

/** Run `work` with `button` aria-disabled; a second press meanwhile is ignored. */
async function pressing(button, work) {
    if (button.getAttribute('aria-disabled') === 'true') return;
    button.setAttribute('aria-disabled', 'true');
    try {
        await work();
    } finally {
        button.removeAttribute('aria-disabled');
    }
}

function confirmDialog({ scope, trigger, title, sentence, label, danger = false, onConfirm }) {
    const cancel = createButton('Cancel');
    const confirm = createButton(label, danger ? 'danger' : 'ca-primary');
    cancel.addEventListener('click', () => closeWorkspaceDialog());
    confirm.addEventListener('click', () => {
        closeWorkspaceDialog();
        onConfirm();
    });
    const actions = el('div', undefined, 'dialog-actions');
    actions.append(cancel, confirm);
    openContentDialog({ scope, title, trigger, nodes: [el('p', sentence), actions] });
}

/** The "Test image link" operation (D110): disabled until the most recent prompt was read. */
function testLinkRow({ api, scope, notify }) {
    let prompt = null;
    const row = operationRow({ scope, title: 'Test image link', description: COPY.testLink, label: 'Create test link…',
        onClick: button => {
            if (button.getAttribute('aria-disabled') === 'true' || !prompt) return;
            const target = prompt;
            // The button is held until the write answered, so a second press cannot write a second record.
            confirmDialog({ scope, trigger: button, title: 'Create test link', sentence: testLinkSentence(target), label: 'Create',
                onConfirm: () => pressing(button, async () => {
                    try {
                        const answer = await api.post(TEST_LINK_URL, { prompt_id: target.id });
                        if (!scope.signal.aborted) notify(`Test link created (image record ${answer?.result?.image_id}).`);
                    } catch {
                        if (!scope.signal.aborted) notify(COPY.testLinkFailed);
                    }
                }) });
        } });
    const button = row.querySelector('.row-actions > button');
    const description = row.querySelector('p');
    button.setAttribute('aria-disabled', 'true');
    api.get(RECENT_URL, { signal: scope.signal, cache: 'no-store' }).then(answer => {
        if (scope.signal.aborted) return;
        const first = Array.isArray(answer?.results) ? answer.results[0] : null;
        if (first && first.id !== undefined && first.id !== null) {
            prompt = first;
            button.removeAttribute('aria-disabled');
        } else {
            description.textContent = `${COPY.testLink} ${COPY.noPrompt}`;
        }
    }, () => {
        if (!scope.signal.aborted) description.textContent = `${COPY.testLink} ${COPY.promptUnread}`;
    });
    return row;
}

/** The "Diagnostics" surface. */
function diagnosticsSurface({ api, activity, scope, navigate, notify, clientFactory }) {
    const surface = el('section', undefined, 'surface');
    const heading = el('div', undefined, 'panel-heading');
    const run = createButton('Run checks');
    heading.append(el('h2', 'Diagnostics'), run);
    const grid = el('div', undefined, 'meta-grid');
    const cells = {};
    for (const term of META_TERMS) {
        const entry = el('div');
        cells[term] = el('dd', LOADING);
        entry.append(el('dt', term), cells[term]);
        grid.append(entry);
    }
    const report = createButton('Download diagnostic report');
    const inspect = operationRow({ scope, title: 'Image-link diagnostics', description: COPY.inspect, label: 'Inspect',
        onClick: () => navigate('#/inspect') });
    surface.append(heading, grid, report, inspect, testLinkRow({ api, scope, notify }));

    // D95: the same Health report as Overview and Maintenance; its "Review missing links" reviews a fresh plan.
    const onReviewMissing = reviewMissingLinks({ api, store: activity, scope, notify, navigate });
    scope.listen(run, 'click', () => runHealthAction({ api, activity, scope, button: run, navigate, notify, onReviewMissing }));

    const paint = state => {
        cells.Database.textContent = databaseText(state);
        cells.Sources.textContent = sourcesText(state);
        cells['Viewer.js'].textContent = viewerText(state);
    };
    paint({ loading: true });
    api.get(REPORT_URL, { signal: scope.signal, cache: 'no-store' })
        .then(answer => { if (!scope.signal.aborted) paint({ report: answer }); },
            error => { if (!scope.signal.aborted) paint({ error }); });

    // S12-13: the renderer line Integrations shows (D106), from the send bridge.
    let renderers = null;
    let client = null;
    scope.cleanup(() => client?.dispose());
    (async () => {
        let text;
        try {
            client = clientFactory();
            text = rendererText(await client.discover({ waitMs: 300 }));
        } catch {
            text = RENDERER_COPY.unavailable;
        }
        if (scope.signal.aborted) return;
        renderers = text;
        cells['Host renderers'].textContent = text;
    })();

    scope.listen(report, 'click', () => pressing(report, async () => {
        try {
            const answer = await api.get(REPORT_URL, { signal: scope.signal, cache: 'no-store' });
            const body = JSON.stringify(mergeReport(answer, renderers), null, 2);
            saveResponse(new Blob([body], { type: 'application/json' }), reportFilename());
            if (!scope.signal.aborted) notify(COPY.reportDone);
        } catch (error) {
            if (scope.signal.aborted) return;
            notify(error?.code === 'busy' ? `${refusalText(error)} Try again when it ends.` : COPY.reportFailed);
        }
    }));
    return surface;
}

/** The two runtime rows: drafts of the `runtime` domain (S12-9), saved by Save preferences. */
function runtimeRows({ scope, drafts }) {
    const level = settingRow({ id: 'runtime-level', label: 'Runtime log level', description: COPY.runtimeLevel,
        control: { type: 'select', options: RUNTIME_LEVELS }, scope,
        onChange: value => drafts.set('runtime', 'runtime_level', value) });
    const consoleRow = settingRow({ id: 'console-log', label: 'Console logging', description: COPY.console,
        control: { type: 'toggle', checked: false }, scope, onChange: value => drafts.set('runtime', 'console_logging', value) });
    const select = level.querySelector('[data-setting="runtime-level"]');
    const toggle = consoleRow.querySelector('[data-setting="console-log"]');
    select.dataset.key = 'runtime_level';
    toggle.dataset.key = 'console_logging';
    const repaint = () => {
        const value = drafts.get('runtime', 'runtime_level');
        select.disabled = typeof value !== 'string';
        if (typeof value === 'string') select.value = value;
        const on = drafts.get('runtime', 'console_logging');
        toggle.disabled = typeof on !== 'boolean';
        if (typeof on === 'boolean') toggle.checked = on;
    };
    scope.cleanup(drafts.subscribe(repaint));
    repaint();
    // A failed read leaves both controls disabled; each says why (the value the server holds is not known).
    const notes = [level, consoleRow].map(row => {
        const note = el('p', COPY.runtimeUnread, 'small');
        return () => row.children[0].append(note);
    });
    const reload = () => drafts.load('runtime').then(() => {}, () => {
        if (!scope.signal.aborted) for (const show of notes) show();
    });
    reload();
    return { rows: [level, consoleRow], reload };
}

/** The "Logs" surface. */
function logsSurface({ api, activity, scope, drafts, navigate, notify, latestOnly, STALE, fetchUrl }) {
    const surface = el('section', undefined, 'surface');
    const toolbar = el('div', undefined, 'toolbar spaced-16');
    const levelLabel = el('label', 'Level', 'small');
    levelLabel.htmlFor = 'log-level';
    const level = el('select');
    level.id = 'log-level';
    for (const [value, label] of LEVEL_OPTIONS) {
        const option = el('option', label);
        option.value = value;
        level.append(option);
    }
    const limitLabel = el('label', 'Lines', 'small');
    limitLabel.htmlFor = 'log-limit';
    const limit = el('input');
    limit.id = 'log-limit';
    limit.type = 'number';
    limit.min = '1';
    limit.max = String(MAX_LIMIT);
    limit.step = '1';
    limit.value = String(DEFAULT_LIMIT);
    limit.title = COPY.limitHint;
    const refresh = createButton('Refresh');
    const downloadLog = createButton('Download log');
    toolbar.append(levelLabel, level, limitLabel, limit, refresh, downloadLog);

    const lines = el('pre');
    lines.id = 'log-lines';
    lines.tabIndex = 0;
    const code = el('code', LOADING);
    lines.append(code);
    const stats = el('p', LOADING, 'small');
    stats.id = 'log-stats';
    stats.setAttribute('role', 'status');
    stats.setAttribute('aria-label', 'Log statistics');

    const files = el('details');
    const line = el('p', LOADING, 'small');
    const review = createButton('Review log cleanup');
    const retention = el('p', LOADING, 'small');
    const list = el('div', undefined, 'operation-list');
    let listed = null;
    // The confirmation names what Clear deletes now: the files are read again before it opens (a read that fails
    // gives the general sentence, never an old count).
    const clear = operationRow({ scope, title: 'Clear all logs', description: COPY.clearDescription, label: 'Clear all logs…',
        danger: true, onClick: button => pressing(button, async () => {
            await readFiles();
            if (scope.signal.aborted) return;
            confirmDialog({ scope, trigger: button, title: 'Clear all logs', sentence: clearSentence(listed), label: 'Clear',
                danger: true, onConfirm: clearLogs });
        }) });
    files.append(el('summary', 'Log files & retention'), line, review, retention, list, clear);
    const runtime = runtimeRows({ scope, drafts });
    surface.append(el('h2', 'Logs'), toolbar, lines, stats, ...runtime.rows, files);

    const logsRead = latestOnly({ lifetime: scope.signal });
    async function readLogs() {
        const count = validLimit(limit.value);
        if (count === null) {
            limit.setAttribute('aria-invalid', 'true');
            return;
        }
        limit.removeAttribute('aria-invalid');
        let answer;
        try {
            answer = await logsRead(signal => api.get(logsUrl(level.value, count), { signal, cache: 'no-store' }));
        } catch {
            if (!scope.signal.aborted) code.textContent = COPY.logUnread;
            return;
        }
        if (answer === STALE || scope.signal.aborted) return;
        const entries = Array.isArray(answer?.logs) ? answer.logs : [];
        code.textContent = entries.length ? entries.map(logLine).join('\n') : COPY.noEntries;
    }

    /** One latest-only read of `url` painting `paint(answer)`, or `failed()` when it could not be read. */
    const reader = (url, paint, failed) => {
        const read = latestOnly({ lifetime: scope.signal });
        return async () => {
            let answer;
            try {
                answer = await read(signal => api.get(url, { signal, cache: 'no-store' }));
            } catch {
                if (!scope.signal.aborted) failed();
                return;
            }
            if (answer !== STALE && !scope.signal.aborted) paint(answer);
        };
    };
    const readStats = reader(STATS_URL, answer => { stats.textContent = statsText(answer?.stats); },
        () => { stats.textContent = COPY.statsUnread; });
    const readConfig = reader(CONFIG_URL, answer => { retention.textContent = retentionText(answer?.config); },
        () => { retention.textContent = COPY.configUnread; });
    const readFiles = reader(FILES_URL, answer => {
        listed = Array.isArray(answer?.files) ? answer.files : [];
        line.textContent = fileLine(listed);
        list.replaceChildren(...listed.map(fileRow));
    }, () => {
        listed = null;
        line.textContent = COPY.filesUnread;
        list.replaceChildren();
    });

    function fileRow(row) {
        const item = el('div', undefined, 'operation');
        const text = el('div');
        text.append(el('h3', row.filename), el('p', fileDetail(row)));
        item.append(text);
        if (row.kind !== 'link') {
            const actions = el('div', undefined, 'row-actions');
            const button = createButton('Download');
            button.setAttribute('aria-label', `Download ${row.filename}`);
            scope.listen(button, 'click', () => saveLog(button, row.filename));
            actions.append(button);
            item.append(actions);
        }
        return item;
    }

    function saveLog(button, name) {
        return pressing(button, async () => {
            try {
                await download(`${DOWNLOAD_URL}${encodeURIComponent(name)}`, name, fetchUrl);
            } catch {
                if (!scope.signal.aborted) notify(COPY.logDownloadFailed);
            }
        });
    }

    function readAll() {
        readLogs();
        readStats();
        readFiles();
        readConfig();
    }

    async function clearLogs() {
        let answer;
        try {
            answer = await api.post(TRUNCATE_URL, {});
        } catch {
            if (!scope.signal.aborted) notify(COPY.clearFailed);
        }
        if (scope.signal.aborted) return;
        if (answer) notify(clearToast(answer.results));
        readAll();
        runtime.reload();
    }

    scope.listen(level, 'change', readLogs);
    scope.listen(limit, 'change', readLogs);
    scope.listen(refresh, 'click', () => { readLogs(); readStats(); readFiles(); });
    scope.listen(downloadLog, 'click', () => saveLog(downloadLog, ACTIVE_LOG));
    scope.listen(review, 'click', () => reviewPlan({ api, store: activity, scope, operation: 'rotated_logs', trigger: review,
        notify, navigate }));
    readAll();
    return surface;
}

/**
 * Mount Diagnostics & logs in `container` (System's `.settings-layout`); returns its two surfaces in concept order.
 * `drafts` is the System draft store (its runtime domain holds the log level and console setting), `activity` the
 * page's Activity store.
 */
export function mountDiagnostics(container, { api, activity, scope, drafts, navigate = navigateTo, notify = showWorkspaceToast,
    clientFactory = bridgeFactory(), latestOnly = window.ApiClient.latestOnly, STALE = window.ApiClient.STALE,
    fetchUrl = window.ApiClient.fetchUrl }) {
    return [
        diagnosticsSurface({ api, activity, scope, navigate, notify, clientFactory }),
        logsSurface({ api, activity, scope, drafts, navigate, notify, latestOnly, STALE, fetchUrl }),
    ];
}
