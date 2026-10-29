/**
 * System › Overview (#249 System S2 Task 6): the concept's `renderSystem` "general" body (app.js 658–675) on real
 * values, its Statistics dialog ("Usage statistics", a1600–1604) and its health report (`health-check`, a1439–1456).
 *
 * - The four `.summary-metrics` and the two `.two-col` surfaces ("Library health", "Workspace preferences") come from
 *   one `GET /prompt_manager/system/overview`. D93: metric 4 is the last health result (Not checked / Passed /
 *   N issues / Incomplete); the review, backup, thumbnail and sources rows say what the server reports, and nothing
 *   reads 0 before the read answers: a load shows a status line, a failure an alert and Retry. The thumbnail row is
 *   its own read of `/images/thumbnails/status` (a walk of every source): "Checking thumbnails…" until it answers.
 *   S9: the Backup status row is its own read of `/backup/status`, worded as Backup & export's status line.
 *   D99: its Review thumbnails opens the same "Thumbnail maintenance" dialog as Sources (thumbnail-review.mjs).
 *   #317: it reads "N missing · M outdated · K failed"; while previews are outdated (another format than the
 *   configured one) a second button, "Convert outdated", opens that dialog on its Convert outdated scope (D131).
 * - D96: the Statistics dialog lists the real runs per category, then "Library totals" (D153: no rating column or
 *   rated-images total).
 * - D95: the "Health report" lists every check the server ran, Missing files per image source, with a chip per real
 *   result and when it was checked. The check runs from Maintenance's notice ("Run health checks", D123) as a
 *   read job of the registry, tracked in the Activity store (its event stream); the report opens when it ends.
 *
 * Reads run through `latestOnly()` on the section's scope, so a late answer never repaints a newer section.
 */
import { createButton } from '../shared/toolkit/core.js';
import { navigateTo } from './state.mjs';
import { closeWorkspaceDialog, openContentDialog } from './workspace-dialogs.mjs';
import { showWorkspaceToast } from './toast.mjs';
import { noun } from './plural.mjs';
import { operationRow } from './system-controls.mjs';
import { openThumbnailReview } from './thumbnail-review.mjs';
import { isLocation, locationRow, onMoveEnded } from './library-location.mjs';
import { STATUS_LOADING, STATUS_URL, backupStatusText } from './system-backups.mjs';

export const OVERVIEW_URL = '/prompt_manager/system/overview';
export const STATISTICS_URL = '/prompt_manager/system/statistics';
export const HEALTH_URL = '/prompt_manager/system/health';
export const THUMBNAILS_URL = '/prompt_manager/images/thumbnails/status';
export const HEALTH_KIND = 'health';

export const LOADING_TEXT = 'Loading the overview…';
export const THUMBNAILS_LOADING = 'Checking thumbnails…';
export const LOAD_FAILED_TEXT = 'The overview could not be loaded. Your saved data has not changed.';
export const STATISTICS_FAILED_TEXT = 'Statistics could not be loaded. Your saved data has not changed.';
export const HEALTH_FAILED_TEXT = 'The health check could not finish. Nothing was changed.';
export const NOT_AVAILABLE = 'Not available yet.';

const number = value => Number(value).toLocaleString();

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

/**
 * D93: metric 4, the last health result of this server session: the checks that found issues, even when another
 * check could not run; otherwise Passed, or Incomplete while a check was unavailable or failed.
 */
export function integrityText(health) {
    if (!health) return 'Not checked';
    if (health.issues > 0) return `${number(health.issues)} ${noun(health.issues, 'issue')}`;
    return health.status === 'passed' ? 'Passed' : 'Incomplete';
}

/** The concept's four metrics `[value, label]`, in its order. */
export function metricValues(overview) {
    return [
        [number(overview.library.positive_prompts), 'Positive prompts'],
        [number(overview.library.negative_variants), 'Negative variants'],
        [number(overview.images.indexed), 'Images'],
        [integrityText(overview.health), 'Database integrity'],
    ];
}

/** D93 (Q6): the review row's title; a count only once the Needs review queue reports one. */
export function ambiguousTitle(needsReview) {
    const pending = needsReview?.pending;
    if (!Number.isSafeInteger(pending)) return 'Ambiguous image links';
    return `${number(pending)} ambiguous image ${noun(pending, 'link')}`;
}


/**
 * D93: the thumbnail row's title from the `/images/thumbnails/status` answer: undefined while it is read, null when
 * the read failed; with no image source (`sources.effective` 0) there is nothing to count.
 */
export function thumbnailTitle(status, sources) {
    if (status === undefined) return THUMBNAILS_LOADING;
    const missing = status?.pending;
    if (!Number.isSafeInteger(missing) || sources?.effective === 0) return 'Thumbnail status unavailable';
    const parts = [missing === 0 ? 'No missing thumbnails' : `${number(missing)} missing ${noun(missing, 'thumbnail')}`];
    // #317: previews in another format than the configured one (Convert outdated replaces them).
    if (convertOffered(status)) parts.push(`${number(status.outdated)} outdated`);
    // #249: files whose automatic preview could not be made this session (they show the original).
    if (Number.isSafeInteger(status.failed) && status.failed > 0) parts.push(`${number(status.failed)} failed`);
    return parts.join(' · ');
}

/** #317: whether the status counts previews in an older format (the row then offers Convert outdated). */
export function convertOffered(status) {
    return Number.isSafeInteger(status?.outdated) && status.outdated > 0;
}

/** #317: the row's "Convert outdated" button, present only while previews are outdated (D131). */
function paintConvert(row, status, { scope, openConvert }) {
    const actions = row.querySelector('.row-actions');
    let button = actions.querySelector('[data-convert]');
    if (!convertOffered(status)) { button?.remove(); return; }
    if (button) return;
    button = createButton('Convert outdated');
    button.dataset.convert = '';
    scope.listen(button, 'click', () => openConvert(button));
    actions.append(button);
}

const SOURCES_SHARED = 'Monitoring and enrichment share one source list.';

/** D93: the sources row, `[title, description]`: the sources in use and how they were chosen. */
export function sourcesCopy(sources) {
    const { configured, effective, auto_detected: auto } = sources;
    const title = `${number(effective)} output ${noun(effective, 'source')}`;
    if (auto) return [title, `None configured is available; ComfyUI's output folder is used. ${SOURCES_SHARED}`];
    if (configured !== effective) return [title, `${number(effective)} of ${number(configured)} configured ${noun(configured, 'source')} available. ${SOURCES_SHARED}`];
    return [title, SOURCES_SHARED];
}

// D95: an issue chip names what was found; the concept's own wording for missing files and ambiguous metadata.
const ISSUE_NOUNS = Object.freeze({
    integrity: ['problem found', 'problems found'],
    foreign_keys: ['broken reference', 'broken references'],
    canonical_hashes: ['hash does not match its text', 'hashes do not match their text'],
    pair_uniqueness: ['duplicate pair row', 'duplicate pair rows'],
    missing_files: ['link needs review', 'links need review'],
    ambiguous_metadata: ['image needs review', 'images need review'],
    consistency: ['inconsistency', 'inconsistencies'],
});

/** D95: a check's chip from its real status. */
export function checkChip(check) {
    if (check.status === 'passed') return 'Passed';
    if (check.status === 'failed') return 'Could not check';
    if (check.status === 'unavailable') return check.id === 'ambiguous_metadata' ? NOT_AVAILABLE.slice(0, -1) : 'Not available';
    const [one, many] = ISSUE_NOUNS[check.id] ?? ['issue', 'issues'];
    if (!Number.isSafeInteger(check.count)) return 'Issues found';
    // SQLite stops listing integrity problems at its limit, so a capped count is a lower bound ("100+").
    if (check.capped === true) return `${number(check.count)}+ ${many}`;
    return `${number(check.count)} ${check.count === 1 ? one : many}`;
}

function sourceChip(source) {
    if (source.status !== 'checked') return 'Not available';
    return source.count === 0 ? 'Passed' : checkChip({ id: 'missing_files', status: 'issues', count: source.count });
}

/** D95: the report's rows `[label, chip]` in the server's order; Missing files once per image source when several. */
export function healthRows(report) {
    return report.checks.flatMap(check => {
        const sources = check.id === 'missing_files' && Array.isArray(check.sources) ? check.sources : [];
        if (sources.length < 2) return [[check.label, checkChip(check)]];
        return sources.map(source => [`${check.label} · ${source.name}`, sourceChip(source)]);
    });
}

/** D95: when the report was taken, and whether it is complete. */
export function healthSummary(report) {
    const when = new Date(report.checked_at);
    const time = Number.isNaN(when.getTime()) ? '' : `Checked ${when.toLocaleString()}. `;
    if (report.status === 'passed') return `${time}Every check passed.`;
    if (report.status === 'incomplete') return `${time}Some checks could not run, so this report is incomplete.`;
    return `${time}Nothing was changed; review the checks with issues.`;
}

/** D96: the concept's "Runs by category" rows `[category, runs]`; D153 drops its average rating column. */
export function categoryRows(statistics) {
    return statistics.by_category.map(row => [row.category ?? 'Uncategorized', number(row.runs)]);
}

/** D96: the added "Library totals" rows `[label, count]` (the legacy statistics; D153 drops the rated images). */
export function totalsRows(statistics) {
    const { totals } = statistics;
    return [['Prompts', totals.total_prompts], ['Tags', totals.total_tags], ['Images', totals.total_images]]
        .map(([label, value]) => [label, number(value)]);
}

function table(caption, head, rows, empty) {
    const node = el('table');
    const header = el('tr');
    for (const text of head) {
        const cell = el('th', text);
        cell.scope = 'col';
        header.append(cell);
    }
    const thead = el('thead');
    thead.append(header);
    const tbody = el('tbody');
    for (const values of rows) {
        const row = el('tr');
        row.append(...values.map(text => el('td', text)));
        tbody.append(row);
    }
    if (rows.length === 0) {
        const row = el('tr');
        const cell = el('td', empty);
        cell.colSpan = head.length;
        row.append(cell);
        tbody.append(row);
    }
    node.append(el('caption', caption), thead, tbody);
    return node;
}

/** The Statistics dialog: "Runs by category" (the concept's table), then "Library totals" (D96). */
export function statisticsNodes(statistics) {
    return [
        table('Runs by category', ['Category', 'Runs'], categoryRows(statistics), 'No prompts in the library yet.'),
        table('Library totals', ['Total', 'Count'], totalsRows(statistics), ''),
    ];
}

/** The concept's health report body: `.operation-list` of `h3` + chip, the D95 summary, then Close / Review missing links. */
export function healthNodes(report, { scope, onReviewMissing }) {
    const list = el('div', undefined, 'operation-list');
    for (const [label, chip] of healthRows(report)) {
        const row = el('div', undefined, 'operation');
        row.append(el('h3', label), el('span', chip, 'chip'));
        list.append(row);
    }
    const close = createButton('Close');
    const review = createButton('Review missing links', 'ca-primary');
    scope.listen(close, 'click', () => closeWorkspaceDialog());
    scope.listen(review, 'click', () => onReviewMissing(review));
    const actions = el('div', undefined, 'dialog-actions');
    actions.append(close, review);
    return [list, el('p', healthSummary(report), 'small'), actions];
}

/**
 * Wait until the job `id` has ended, following it in the Activity store (`activity`), which streams the job's events
 * and marks it lost when the server no longer lists it; nothing here polls. Resolves its final state ('finished',
 * 'failed', 'cancelled' or 'lost').
 */
export function waitForJob(activity, id, { signal } = {}) {
    if (signal?.aborted) return Promise.reject(signal.reason);
    return new Promise((resolve, reject) => {
        const check = () => {
            const state = activity.find(id)?.state;
            if (state && state !== 'running') finish(resolve, state);
        };
        const onAbort = () => finish(reject, signal.reason);
        const unsubscribe = activity.subscribe(check);
        function finish(settle, value) {
            unsubscribe();
            signal?.removeEventListener('abort', onAbort);
            settle(value);
        }
        signal?.addEventListener('abort', onAbort, { once: true });
        check();
    });
}

/**
 * Run the health check and resolve its report. The started job is tracked in the Activity store; a check already
 * running (409 busy, another tab) is found in the store's job list and waited for instead of refused. Rejects when the
 * check fails or ends without a report.
 */
export async function runHealthCheck(api, { activity, signal } = {}) {
    let id;
    try {
        const { job } = await api.post(HEALTH_URL, {}, { signal });
        id = activity.track(job).id;
    } catch (error) {
        if (error?.status !== 409 || error?.code !== 'busy') throw error;
        await activity.load();
        id = activity.running().find(job => job.kind === HEALTH_KIND)?.id;
        // The other check ended between the refusal and this read: its report is the latest.
    }
    const state = id ? await waitForJob(activity, id, { signal }) : 'finished';
    if (state !== 'finished') throw new Error(HEALTH_FAILED_TEXT);
    const { report } = await api.get(HEALTH_URL, { signal, cache: 'no-store' });
    if (!report) throw new Error(HEALTH_FAILED_TEXT);
    return report;
}

/**
 * Open the Health report for `report` (Maintenance's notice and its Check action). Its "Review missing links" runs
 * `onReviewMissing(trigger, pressed)`: Maintenance reviews a fresh Missing-file links plan (S7), never a cleanup,
 * holding the pressed button while it is read; without one it goes to Maintenance.
 */
export function openHealthReport(report, { scope, trigger = null, navigate = navigateTo, onReviewMissing = null }) {
    openContentDialog({ scope, title: 'Health report', trigger, nodes: healthNodes(report, { scope,
        onReviewMissing: pressed => {
            if (onReviewMissing) onReviewMissing(trigger, pressed);
            else { closeWorkspaceDialog(); navigate('#/system/maintenance'); }
        } }) });
}

/**
 * Run the read-only health check from `button` and open its report: the button reads "Checking…" and is aria-disabled
 * until the check ended (a second press is ignored), then reads `label` again.
 */
export async function runHealthAction({ api, activity, scope, button, navigate = navigateTo, notify = showWorkspaceToast,
    onReviewMissing = null }) {
    if (button.getAttribute('aria-disabled') === 'true') return;
    const label = button.textContent;
    button.setAttribute('aria-disabled', 'true');
    button.textContent = 'Checking…';
    try {
        const report = await runHealthCheck(api, { activity, signal: scope.signal });
        if (scope.signal.aborted) return;
        openHealthReport(report, { scope, trigger: button, navigate, onReviewMissing });
    } catch (error) {
        if (!scope.signal.aborted) notify(HEALTH_FAILED_TEXT);
    } finally {
        if (!scope.signal.aborted) {
            button.removeAttribute('aria-disabled');
            button.textContent = label;
        }
    }
}

/**
 * The concept's Maintenance notice ("Check first. Review changes. Then apply." + "Run health checks", a689), D123: the
 * press runs the read-only check, then opens the report.
 */
export function healthNotice({ api, activity, scope, navigate = navigateTo, notify = showWorkspaceToast, onReviewMissing = null }) {
    const notice = el('div', undefined, 'notice');
    const text = el('div');
    text.append(el('h2', 'Check first. Review changes. Then apply.'),
        el('p', 'One home for database and media maintenance. Scanning is separate from changing or removing records.'));
    const run = createButton('Run health checks', 'ca-primary');
    notice.append(text, run);
    scope.listen(run, 'click', () => runHealthAction({ api, activity, scope, button: run, navigate, notify, onReviewMissing }));
    return notice;
}

function metricsNode(overview) {
    const metrics = el('div', undefined, 'summary-metrics');
    for (const [value, label] of metricValues(overview)) {
        const metric = el('div', undefined, 'metric');
        metric.append(el('strong', value), el('span', label));
        metrics.append(metric);
    }
    return metrics;
}

function surface(heading, rows) {
    const section = el('section', undefined, 'surface');
    section.append(el('h2', heading), ...rows);
    return section;
}

/** The Overview's nodes, and its thumbnail row (painted by its own read). */
function overviewNodes(overview, { api, activity, notify, scope, navigate, openStatistics, openThumbnails }) {
    const go = section => () => navigate(`#/system/${section}`);
    const review = operationRow({ scope, title: ambiguousTitle(overview.needs_review),
        description: Number.isSafeInteger(overview.needs_review?.pending)
            ? 'Review metadata before assigning a positive / negative pair.' : NOT_AVAILABLE,
        label: 'Review metadata', onClick: () => navigate('#/review') });
    const [sourcesTitle, sourcesDescription] = sourcesCopy(overview.sources);
    const thumbnails = operationRow({ scope, title: THUMBNAILS_LOADING, description: 'Generate only missing previews when necessary.',
        label: 'Review thumbnails', onClick: openThumbnails });
    const backupRow = operationRow({ scope, title: 'Backup status', description: STATUS_LOADING, label: 'View backups',
        onClick: go('backups') });
    const health = surface('Library health', [
        review,
        // S9: what GET /backup/status reports, as Backup & export's status line says it (its own read).
        backupRow,
        // D129: where the library lives and why; Move library when it is in the node folder.
        ...(isLocation(overview.location)
            ? [locationRow(overview.location, { api, activity, scope, notify, navigate })] : []),
        // D99: Review thumbnails opens the thumbnail dialog (the concept's row starts a sample job directly).
        thumbnails,
    ]);
    const preferences = surface('Workspace preferences', [
        operationRow({ scope, title: 'One display configuration', description: 'Grid and density settings are shared across views.',
            label: 'Appearance', onClick: go('appearance') }),
        operationRow({ scope, title: sourcesTitle, description: sourcesDescription, label: 'Manage sources', onClick: go('sources') }),
        operationRow({ scope, title: 'Statistics', description: 'Runs by category, with library totals.', label: 'Explore',
            onClick: openStatistics }),
    ]);
    const columns = el('div', undefined, 'two-col');
    columns.append(health, preferences);
    return { nodes: [metricsNode(overview), columns], thumbnails, backup: backupRow };
}

/**
 * The Overview section's mount (`mountSystem`'s `sections.overview`): returns its loading line, which the server's
 * answer replaces in place (the concept's metrics and columns sit directly in `.settings-layout`). A read that a newer
 * one superseded, or one answering after the section left, paints nothing.
 */
export function mountOverview(container, { api, activity, scope, navigate = navigateTo, notify = showWorkspaceToast,
    latestOnly = window.ApiClient.latestOnly, STALE = window.ApiClient.STALE }) {
    const overviewRead = latestOnly({ lifetime: scope.signal });
    const statisticsRead = latestOnly({ lifetime: scope.signal });
    const thumbnailsRead = latestOnly({ lifetime: scope.signal });
    const backupRead = latestOnly({ lifetime: scope.signal });
    let shown = [];
    // The backup status (undefined while read, null when it failed), painted into its row once both are there (S9).
    let backupStatus;
    let backupRow = null;
    const paintBackup = () => {
        if (backupRow) backupRow.querySelector('p').textContent = backupStatusText(backupStatus);
    };
    // The thumbnail status (undefined while read, null when it failed), painted into the row whenever both are there.
    let thumbnailStatus;
    let thumbnailRow = null;
    let sources = null;
    const openConvert = trigger => openThumbnailReview({ api, store: activity, scope, trigger, navigate, mode: 'convert' });
    const paintThumbnails = () => {
        if (!thumbnailRow) return;
        thumbnailRow.querySelector('h3').textContent = thumbnailTitle(thumbnailStatus, sources);
        paintConvert(thumbnailRow, thumbnailStatus, { scope, openConvert });
    };

    function swap(nodes) {
        const anchor = shown.find(node => node.isConnected);
        if (anchor) anchor.before(...nodes);
        for (const node of shown) node.remove();
        shown = nodes;
        return nodes;
    }

    async function openStatistics(button) {
        try {
            const statistics = await statisticsRead(signal => api.get(STATISTICS_URL, { signal, cache: 'no-store' }));
            if (statistics === STALE || scope.signal.aborted) return;
            openContentDialog({ scope, title: 'Usage statistics', trigger: button, nodes: statisticsNodes(statistics) });
        } catch (error) {
            if (!scope.signal.aborted) notify(STATISTICS_FAILED_TEXT);
        }
    }

    function loading() {
        const status = el('p', LOADING_TEXT, 'small');
        status.setAttribute('role', 'status');
        return [status];
    }

    async function load() {
        try {
            const overview = await overviewRead(signal => api.get(OVERVIEW_URL, { signal, cache: 'no-store' }));
            if (overview === STALE || scope.signal.aborted) return;
            const { nodes, thumbnails, backup } = overviewNodes(overview, { api, activity, notify, scope, navigate, openStatistics,
                openThumbnails: trigger => openThumbnailReview({ api, store: activity, scope, trigger, navigate }) });
            thumbnailRow = thumbnails;
            backupRow = backup;
            sources = overview.sources;
            swap(nodes);
            paintThumbnails();
            paintBackup();
        } catch (error) {
            if (scope.signal.aborted) return;
            const alert = el('p', LOAD_FAILED_TEXT);
            alert.setAttribute('role', 'alert');
            const retry = createButton('Retry');
            scope.listen(retry, 'click', () => {
                swap(loading());
                load();
                loadThumbnails();
                loadBackup();
            });
            const panel = el('section', undefined, 'surface');
            panel.append(alert, retry);
            swap([panel]);
        }
    }
    async function loadThumbnails() {
        let status;
        try {
            status = await thumbnailsRead(signal => api.get(THUMBNAILS_URL, { signal, cache: 'no-store' }));
        } catch (error) {
            status = null;
        }
        if (status === STALE || scope.signal.aborted) return;
        thumbnailStatus = status ?? null;
        paintThumbnails();
    }
    async function loadBackup() {
        let status;
        try {
            status = await backupRead(signal => api.get(STATUS_URL, { signal, cache: 'no-store' }));
        } catch (error) {
            status = null;
        }
        if (status === STALE || scope.signal.aborted) return;
        backupStatus = status ?? null;
        paintBackup();
    }
    const first = swap(loading());
    // D129: a finished Move library changes where the library lives.
    scope.cleanup(onMoveEnded(activity, load));
    load();
    loadThumbnails();
    loadBackup();
    return first;
}
