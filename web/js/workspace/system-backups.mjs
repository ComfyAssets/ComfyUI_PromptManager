/**
 * System › Backup & export (#249 System S9 Task 24): the concept's `renderSystem` "backups" body (app.js 691–693), its
 * `restore` dialog ("Restore preview", a1472–1476) and its `import` dialog (a1483–1487) on the S8 backend.
 *
 * - "Database backup": D103 "Back up" (the concept's "Simulate backup") downloads the verified copy of
 *   `GET /prompt_manager/backup` through the api client (`downloadDatabaseBackup`, the same download as every backup
 *   box), then reads the status again. The `p.small` under the actions is `GET /prompt_manager/backup/status` only:
 *   this session's last download (verified or not) and the newest safety backup on disk, else "No backup recorded."
 *   Overview's "Backup status" row says the same (`backupStatusText`).
 * - "Review restore…" picks a `.db` file and stages it (`POST /prompt_manager/restore/preview`); D105: "Restore
 *   preview" shows the server's answer in the shared review (`openReviewDialog`): the concept's Snapshot / Integrity /
 *   Migration / Safety backup rows with real values, plus what the backup holds and what the library holds now, the
 *   hold line, the D55 backup box and Cancel / "Restore" (`danger`). Leaving it any way but Restore discards the stage
 *   (`DELETE /prompt_manager/restore/preview/<token>`). Restore starts the "Database restore" Activity job (exclusive,
 *   not cancellable) and opens its view; a finished restore reloads the open views (`onRestoreFinished`, index.mjs).
 *   I1: a Restore the server refuses as busy reopens the same review (Restore retries, Discard releases); a job that
 *   hands its review back (the leases did not drain) reopens it in this page (`onRestoreHandedBack`, index.mjs);
 *   every other path that abandons the token discards it. The picker has no type filter (M3: the safety backups have
 *   no `.db` extension); a file the server refuses toasts its validator's reason, and nothing is staged.
 * - "Portable export": D103 "Export JSON" downloads the Library document (`/library/export`); the secondary row
 *   downloads every prompt (`/export`, the legacy admin Export) and the saved pairs (`/pairs/export`). "Import / inspect file"
 *   opens the shared "Import & inspect" (D15, D55): Inspect a local image, Rescan and Scan for metadata.
 * - D104 "Recovery policy" (the concept's "Proposed" chip kept, Q4): "Create a safety backup before maintenance" is
 *   the server's `safety_backup` policy (its line says what it does), drafted behind the Save row and saved with the
 *   revision; "Scheduled database
 *   backups" offers Manual only, Daily and Weekly disabled with the reason; the warning note is truthful.
 *
 * Every request goes through the api client (or `fetchUrl` for a download or an upload); every listener sits on the
 * section's `scope`.
 */
import { createButton } from '../shared/toolkit/core.js';
import { navigateTo } from './state.mjs';
import { showWorkspaceToast } from './toast.mjs';
import { settingRow } from './system-controls.mjs';
import { busyToast, downloadDatabaseBackup, openMetadataScan, saveResponse } from './activity-scan.mjs';
import { applyPlan, holding } from './maintenance-review.mjs';
import { RESCAN_FAILED, RESCAN_STARTED, openImportDialog, openReviewDialog, rescanFinished } from './workspace-dialogs.mjs';
import { noun } from './plural.mjs';

export const STATUS_URL = '/prompt_manager/backup/status';
export const PREVIEW_URL = '/prompt_manager/restore/preview';
export const APPLY_URL = '/prompt_manager/restore/apply';
const SETTINGS_URL = '/prompt_manager/settings';
const RESCAN_URL = '/prompt_manager/gallery/rescan';
export const RESTORE_KIND = 'restore';
export const RESTORE_TITLE = 'Restore preview';
export const RESTORE_LABEL = 'Restore';

export const STATUS_LOADING = 'Checking backups…';
export const STATUS_FAILED = 'The backup status could not be read.';
export const NO_BACKUP = 'No backup recorded.';
export const REVIEW_FAILED = 'The backup could not be reviewed. Nothing was changed.';
export const EXPORT_FAILED = 'The export could not be downloaded. Nothing was saved to this device.';
export const SOURCES_FAILED = 'The output folders could not be read. Try again.';
export const HOST_UNAVAILABLE = 'Server settings could not be read.';
export const SCHEDULE_REASON = 'Daily and Weekly are not available yet: back up with Back up whenever you choose.';
export const WARNING_NOTE = 'Restores are staged, migrated and validated before replacing live data.';
// D104 (M2): what the safety-backup policy does (the concept's "Preview a verified backup…" line misstates it).
export const SAFETY_DESCRIPTION = 'Keep a verified copy of the database on disk before maintenance, cleanups and LoRA ' +
    'imports change records. A restore always keeps one.';
// I1: a third review while two are held (another window's): what the user can do about it.
export const REVIEWS_FULL = 'Two restores are already being reviewed in another window. Discard one there, or try again ' +
    'when it expires (15 minutes after it was reviewed).';
export const RESTORE_BACKUP_COPY = Object.freeze({ heading: 'Back up the database first (recommended)',
    text: 'Downloads a copy of the prompts database before the restore replaces it.' });

/** D103: the concept's Export plus the two secondary exports: `[label, url, fallback file name]`. */
export const EXPORTS = Object.freeze([
    ['Export JSON', '/prompt_manager/library/export', 'prompt-manager-library-v1.json'],
    // N1: the legacy admin page's Export: every prompt, as `{export_date, total_prompts, prompts}`.
    ['Export all prompts JSON', '/prompt_manager/export', 'prompt_manager_prompts.json'],
    ['Export saved pairs JSON', '/prompt_manager/pairs/export', 'prompt-pairs.json'],
]);

const number = value => value.toLocaleString('en-US');
const WHEN = Object.freeze({ dateStyle: 'medium', timeStyle: 'short' });

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

/** A server time as the reader's local date and time, or null when it is not one. */
export function whenText(value) {
    const when = new Date(value);
    return typeof value === 'string' && !Number.isNaN(when.getTime()) ? when.toLocaleString(undefined, WHEN) : null;
}

/**
 * The status line (and Overview's Backup status) from `GET /backup/status`: undefined while it is read, null when it
 * failed. Only what the server reports is named: this session's last download and the newest safety backup on disk.
 */
export function backupStatusText(status) {
    if (status === undefined) return STATUS_LOADING;
    if (status === null || typeof status !== 'object') return STATUS_FAILED;
    const parts = [];
    const download = status.last_download;
    const downloaded = whenText(download?.at);
    if (downloaded) parts.push(`Downloaded backup · ${downloaded} · ${download.verified === true ? 'verified' : 'not verified'}.`);
    const safety = Array.isArray(status.safety_backups) ? status.safety_backups : [];
    const newest = whenText(safety[0]?.modified_at);
    if (newest) parts.push(`Safety backup · ${newest} · ${number(safety.length)} on disk.`);
    return parts.join(' ') || NO_BACKUP;
}

/** "1,234 prompts · 56 image links · 3 saved pairs · 10 runs" for a restore summary (backup or live library). */
export function countsText(counts) {
    const parts = [['prompts', 'prompt'], ['image_links', 'image link'], ['pairs', 'saved pair'], ['runs', 'run']]
        .filter(([key]) => Number.isSafeInteger(counts?.[key]))
        .map(([key, word]) => `${number(counts[key])} ${noun(counts[key], word)}`);
    return parts.join(' · ') || 'Not reported';
}

/** The Integrity value: verified, with what the stage repaired or could not (S8's `documents_line`). */
export function integrityText(preview) {
    const verified = preview?.integrity === 'ok' ? 'Verified' : 'Not verified';
    const documents = preview?.documents ?? {};
    const repaired = Number.isSafeInteger(documents.repaired) ? documents.repaired : 0;
    const cleared = Number.isSafeInteger(documents.cleared) ? documents.cleared : 0;
    const parts = [verified];
    if (repaired) parts.push(`repaired ${number(repaired)} damaged workflow ${noun(repaired, 'document')}`);
    if (cleared) {
        const images = Number.isSafeInteger(documents.images) ? documents.images : 0;
        parts.push(`could not repair ${number(cleared)} workflow ${noun(cleared, 'document')}: ${number(images)} ` +
            `${noun(images, 'image')} ${images === 1 ? 'shows' : 'show'} no workflow`);
    }
    return parts.join(' · ');
}

/** A file size: whole KB below 1 MB (never "0.0 MB"), else MB with one decimal. */
function sizeText(bytes) {
    if (bytes < 2 ** 20) return `${number(Math.max(1, Math.round(bytes / 1024)))} KB`;
    return `${(bytes / 2 ** 20).toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} MB`;
}

/** The Snapshot value: the chosen file's name, size and date. */
export function snapshotText(file) {
    const size = Number.isFinite(file?.size) ? ` · ${sizeText(file.size)}` : '';
    const when = Number.isFinite(file?.lastModified) ? whenText(new Date(file.lastModified).toISOString()) : null;
    return `${file?.name ?? 'Backup file'}${size}${when ? ` · ${when}` : ''}`;
}

/** D105: the concept's four rows with the review's values, then the two the decision needs. */
export function restoreGrid(preview, file) {
    const destination = preview?.safety_backup?.destination_label;
    return [
        ['Snapshot', snapshotText(file)],
        ['Integrity', integrityText(preview)],
        ['Migration', preview?.schema?.migrated === true ? 'Upgraded to the current schema' : 'Current schema'],
        ['Safety backup', `Created before replacing${destination ? ` (${destination})` : ''}`],
        ['In the backup', countsText(preview?.summary)],
        ['In the library now', countsText(preview?.live)],
    ];
}

/** D105: the `p.small` line: what Restore does and until when the review holds. */
export function restoreNote(preview, { now = Date.now() } = {}) {
    const parts = ['Restore replaces the whole library with this backup and runs alone in Activity.'];
    const expires = new Date(preview?.expires_at);
    if (!Number.isNaN(expires.getTime())) {
        const minutes = Math.max(1, Math.round((expires.getTime() - now) / 60000));
        parts.push(`Nothing changes until you restore. This review holds for ${number(minutes)} ${noun(minutes, 'minute')}.`);
    } else {
        parts.push('Nothing changes until you restore.');
    }
    return parts.join(' ');
}

/** The toast for a review the server refused (its reason, a running job) or that could not be read. */
export function reviewRefusal(error) {
    if (error?.code === 'busy') return busyToast(error.running);
    if (error?.status === 429) return REVIEWS_FULL;
    if (error?.refused === true && typeof error.message === 'string' && error.message) {
        if (error.code === 'invalid-backup') return `This file can't be restored: ${error.message.replace(/\.?$/, '.')}`;
        return error.message;
    }
    return REVIEW_FAILED;
}

/** Stage `file` for review; the server's answer, or an ApiError naming its refusal. */
export async function uploadRestore(file, { fetchUrl = window.ApiClient.fetchUrl, ApiError = window.ApiClient.ApiError,
    signal } = {}) {
    const form = new FormData();
    form.append('database_file', file, file.name);
    const response = await fetchUrl(PREVIEW_URL, { method: 'POST', body: form, cache: 'no-store', credentials: 'same-origin',
        signal });
    let data = null;
    try {
        data = await response.json();
    } catch {
        data = null;
    }
    const message = data && typeof data === 'object' ? data.error : undefined;
    if (!response.ok || !data || data.success === false || typeof data.token !== 'string') {
        throw new ApiError(response.status, message || `HTTP ${response.status}`, data?.code, null, data);
    }
    return data;
}

/** Discard a staged review; a failure only leaves it to expire on the server. */
export function discardRestore(token, { fetchUrl = window.ApiClient.fetchUrl } = {}) {
    return fetchUrl(`${PREVIEW_URL}/${encodeURIComponent(token)}`, { method: 'DELETE', keepalive: true, credentials: 'same-origin' })
        .then(() => {}, () => {});
}

/**
 * I1: what to do with a reviewed token once Restore was pressed: `follow` the started job (the page keeps the review
 * until it ends, in case the job hands it back), `reopen` the review after a busy refusal (the server keeps the token:
 * Restore retries, Discard releases), and `release` it on every other path (a refusal that dropped it, a used one, a
 * lost answer): a DELETE of a token the server no longer holds, or a job took, changes nothing.
 */
export function afterApply(outcome) {
    if (outcome?.job) return 'follow';
    if (outcome?.refused?.code === 'busy') return 'reopen';
    return 'release';
}

// I1: the reviews whose restore job is running, by job id, so a job that hands its review back reopens it here.
const startedReviews = new Map();

/** Keep `review` (`{token, preview, file}`) until the restore job `jobId` ends. */
export function rememberRestore(jobId, review) {
    startedReviews.set(jobId, review);
}

/**
 * Call `onKept(review)` when a restore job this page started fails and the server kept its review
 * (`scope.review_kept`, the leases did not drain); any other end forgets the review (the server discarded or used its
 * stage). Returns the unsubscribe.
 */
export function onRestoreHandedBack(activity, onKept) {
    return activity.subscribe(() => {
        for (const job of activity.jobs()) {
            if (job.kind !== RESTORE_KIND || job.state === 'running' || !startedReviews.has(job.id)) continue;
            const review = startedReviews.get(job.id);
            startedReviews.delete(job.id);
            if (job.state === 'failed' && job.scope?.review_kept === true) onKept(review);
        }
    });
}

/**
 * D105: "Restore preview" for a staged `preview` of `file`. Leaving it any way but Restore discards the stage; after a
 * refusal or a handed-back job (`again`), Cancel reads "Discard". Restore applies exactly this token and acts on the
 * outcome (`afterApply`).
 */
export function showRestoreReview({ api, store, scope, preview, file, trigger = null, onBackup = () => downloadDatabaseBackup(),
    again = false, reviewAgain = null, notify = showWorkspaceToast, navigate = navigateTo, fetchUrl = window.ApiClient.fetchUrl }) {
    const token = preview.token;
    const show = (retry, shownScope = scope) => showRestoreReview({ api, store, scope: shownScope, preview, file, trigger,
        onBackup, again: retry, reviewAgain, notify, navigate, fetchUrl });
    openReviewDialog({ scope, trigger, title: RESTORE_TITLE, grid: restoreGrid(preview, file),
        sentence: restoreNote(preview), backup: { copy: RESTORE_BACKUP_COPY, onBackup },
        cancelLabel: again ? 'Discard' : 'Cancel', primary: { label: RESTORE_LABEL, danger: true },
        onDismiss: () => discardRestore(token, { fetchUrl }),
        onStart: async () => {
            const outcome = await applyPlan({ api, store, scope, token, reviewAgain, notify, navigate, url: APPLY_URL });
            const next = afterApply(outcome);
            if (next === 'follow') rememberRestore(outcome.job.id, { token, preview, file });
            else if (next === 'reopen' && !scope.signal.aborted) show(true);
            else discardRestore(token, { fetchUrl });
        } });
}

/**
 * Stage `file`, then show "Restore preview"; Restore applies exactly that token. A review the server asks to redo (it
 * expired, or the library changed) stages the same file again.
 */
export async function reviewRestore({ api, store, scope, file, trigger = null, hold = trigger, onBackup,
    notify = showWorkspaceToast, navigate = navigateTo, fetchUrl = window.ApiClient.fetchUrl }) {
    await holding(hold, 'Reviewing…', async () => {
        let preview;
        try {
            preview = await uploadRestore(file, { fetchUrl, signal: scope.signal });
        } catch (error) {
            if (!scope.signal.aborted) notify(reviewRefusal(error));
            return;
        }
        if (scope.signal.aborted) {
            discardRestore(preview.token, { fetchUrl });
            return;
        }
        const reviewAgain = () => reviewRestore({ api, store, scope, file, trigger, onBackup, notify, navigate, fetchUrl });
        showRestoreReview({ api, store, scope, preview, file, trigger, onBackup, reviewAgain, notify, navigate, fetchUrl });
    });
}

/** The file name a download's Content-Disposition names, or `fallback`. */
function filenameOf(response, fallback) {
    const match = /filename="?([^";]+)"?/i.exec(response.headers.get('Content-Disposition') || '');
    return match ? match[1] : fallback;
}

/** Download one export through `fetchUrl`; toasts a failure, and nothing is saved then. */
export async function downloadExport([, url, fallback], { fetchUrl = window.ApiClient.fetchUrl, notify = showWorkspaceToast,
    signal } = {}) {
    try {
        const response = await fetchUrl(url, { cache: 'no-store', credentials: 'same-origin', signal });
        if (!response.ok) throw new Error(`Export failed with ${response.status}`);
        const blob = await response.blob();
        if (signal?.aborted) return false;
        saveResponse(blob, filenameOf(response, fallback));
        return true;
    } catch {
        if (!signal?.aborted) notify(EXPORT_FAILED);
        return false;
    }
}

/**
 * Run `onFinished()` once each time a restore this page saw running ends finished (the library was replaced). A
 * failed restore changed nothing. Returns the unsubscribe.
 */
export function onRestoreFinished(activity, onFinished) {
    const running = new Set();
    return activity.subscribe(() => {
        let finished = false;
        for (const job of activity.jobs()) {
            if (job.kind !== RESTORE_KIND) continue;
            if (job.state === 'running') running.add(job.id);
            else if (running.delete(job.id) && job.state === 'finished') finished = true;
        }
        if (finished) onFinished();
    });
}

const OPEN_DIALOG = 'dialog[open]';
const DIALOG_MUTATIONS = Object.freeze({ subtree: true, childList: true, attributes: true, attributeFilter: ['open'] });

/**
 * #415: the S9 reload after a finished restore never runs under an open dialog, which it would close with whatever
 * was typed in it. `request()` runs `reload()` at once when no dialog is open; otherwise it runs once the last open
 * dialog has closed (repeated requests meanwhile run it once). The dialog keeps its entries, and its next write is
 * refused by the restored storage with its own note and Reload, as when this page never saw the restore. A
 * MutationObserver sees every dialog close: removing `open` and removing the node are synchronous, while the dialog
 * `close` event is a later task that never bubbles, and every dialog here is removed in the task that closes it.
 * `dispose()` drops a waiting reload. Returns `{request, dispose}`.
 */
export function createRestoreReload(reload, { doc = globalThis.document, Observer = globalThis.MutationObserver } = {}) {
    let observer = null;
    const dialogOpen = () => doc.querySelector(OPEN_DIALOG) !== null;
    const stop = () => {
        observer?.disconnect();
        observer = null;
    };
    return {
        request() {
            if (observer) return;
            if (!dialogOpen()) {
                reload();
                return;
            }
            observer = new Observer(() => {
                if (dialogOpen()) return;
                stop();
                reload();
            });
            observer.observe(doc.documentElement, DIALOG_MUTATIONS);
        },
        dispose: stop,
    };
}

function surface(heading, ...nodes) {
    const section = el('section', undefined, 'surface');
    section.append(heading, ...nodes);
    return section;
}

/** The Backup & export section's mount (`mountSystem`'s `sections.backups`). */
export function mountBackups(container, { api, activity, scope, drafts, notify = showWorkspaceToast, navigate = navigateTo,
    fetchUrl = window.ApiClient.fetchUrl, latestOnly = window.ApiClient.latestOnly, STALE = window.ApiClient.STALE }) {
    const statusRead = latestOnly({ lifetime: scope.signal });
    const status = el('p', STATUS_LOADING, 'small');
    status.id = 'backup-status';
    status.setAttribute('role', 'status');
    async function loadStatus() {
        let answer;
        try {
            answer = await statusRead(signal => api.get(STATUS_URL, { signal, cache: 'no-store' }));
        } catch {
            answer = null;
        }
        if (answer === STALE || scope.signal.aborted) return;
        status.textContent = backupStatusText(answer);
    }
    const backupNow = async () => {
        const done = await downloadDatabaseBackup({ fetchUrl, notify });
        if (done && !scope.signal.aborted) loadStatus();
        return done;
    };

    // Database backup: Back up, Review restore… and the status line.
    const backup = createButton('Back up', 'ca-primary');
    const restore = createButton('Review restore…', 'danger');
    const picker = el('input');
    picker.type = 'file';
    // M3: no type filter: the safety backups (`prompts.db.backup_<time>`) have no `.db` extension; the server validates.
    picker.hidden = true;
    picker.id = 'restore-file';
    picker.setAttribute('aria-label', 'Backup file to restore');
    // Outside the concept's surface, for as long as the section is shown.
    document.body.append(picker);
    scope.cleanup(() => picker.remove());
    scope.listen(backup, 'click', () => holding(backup, 'Backing up…', backupNow));
    scope.listen(restore, 'click', () => { if (restore.getAttribute('aria-disabled') !== 'true') picker.click(); });
    scope.listen(picker, 'change', () => {
        const [file] = picker.files ?? [];
        picker.value = '';
        if (!file) return;
        reviewRestore({ api, store: activity, scope, file, trigger: restore, onBackup: backupNow, notify, navigate, fetchUrl });
    });
    const backupActions = el('div', undefined, 'row-actions spaced');
    backupActions.append(backup, restore);
    const database = surface(el('h2', 'Database backup'),
        el('p', 'A complete recovery snapshot, including prompt pairs, metadata and image links. Media files are separate.'),
        backupActions, status);

    // Portable export: Export JSON and Import / inspect file, then the two secondary exports (D103).
    const exportButtons = EXPORTS.map(entry => {
        const button = createButton(entry[0]);
        scope.listen(button, 'click', () => holding(button, 'Exporting…',
            () => downloadExport(entry, { fetchUrl, notify, signal: scope.signal })));
        return button;
    });
    const importButton = createButton('Import / inspect file');
    let rescanning = false;
    let importDialog = null;
    async function rescan() {
        if (rescanning || scope.signal.aborted) return;
        rescanning = true;
        notify(RESCAN_STARTED);
        try {
            const result = await api.post(RESCAN_URL, {}, { signal: scope.signal });
            if (!scope.signal.aborted) notify(rescanFinished(result));
        } catch {
            if (!scope.signal.aborted) notify(RESCAN_FAILED);
        } finally {
            rescanning = false;
            importDialog?.setRescanBusy(false);
        }
    }
    async function metadataScan(trigger) {
        let settings;
        try {
            settings = await api.get(SETTINGS_URL, { signal: scope.signal, cache: 'no-store' });
        } catch {
            if (!scope.signal.aborted) notify(SOURCES_FAILED);
            return;
        }
        if (scope.signal.aborted) return;
        const sources = Array.isArray(settings?.sources?.effective) ? settings.sources.effective.length : 0;
        openMetadataScan({ scope, store: activity, sources, trigger });
    }
    scope.listen(importButton, 'click', () => {
        importDialog = openImportDialog({ scope, trigger: importButton, rescanBusy: rescanning,
            onInspect: () => navigate('#/inspect'), onRescan: rescan, onMetadataScan: metadataScan });
    });
    const exportActions = el('div', undefined, 'row-actions spaced');
    exportActions.append(exportButtons[0], importButton);
    const moreExports = el('div', undefined, 'row-actions spaced');
    moreExports.append(...exportButtons.slice(1));
    const portable = surface(el('h2', 'Portable export'),
        el('p', 'Export reusable recipes and tags as versioned JSON. This is distinct from a complete database backup.'),
        exportActions, moreExports);
    const columns = el('div', undefined, 'two-col');
    columns.append(database, portable);

    // Recovery policy (D104): the real safety-backup policy and the schedule the server does not have yet.
    const heading = el('h2', 'Recovery policy ');
    heading.append(el('span', 'Proposed', 'chip blue'));
    const safety = settingRow({ id: 'backup-before', label: 'Create a safety backup before maintenance',
        description: SAFETY_DESCRIPTION,
        control: { type: 'toggle' }, scope, onChange: value => drafts.set('host', 'safety_backup', value) });
    const safetyInput = safety.querySelector('#backup-before');
    safetyInput.dataset.key = 'safety_backup';
    const schedule = settingRow({ id: 'backup-schedule', label: 'Scheduled database backups', description: SCHEDULE_REASON,
        control: { type: 'select', options: ['Manual only', 'Daily', 'Weekly'], value: 'Manual only' } });
    for (const option of schedule.querySelectorAll('option')) option.disabled = option.value !== 'Manual only';
    const policy = surface(heading, safety, schedule, el('p', WARNING_NOTE, 'warning-note'));
    let hostNote = null;
    function paintPolicy() {
        const value = drafts.get('host', 'safety_backup');
        safetyInput.disabled = typeof value !== 'boolean';
        if (typeof value === 'boolean') safetyInput.checked = value;
        const unavailable = drafts.hostState?.() === 'failed' && typeof value !== 'boolean';
        if (unavailable && !hostNote) {
            hostNote = el('p', HOST_UNAVAILABLE, 'small');
            safety.children[0].append(hostNote);
        } else if (!unavailable && hostNote) {
            hostNote.remove();
            hostNote = null;
        }
    }
    scope.cleanup(drafts.subscribe(paintPolicy));
    paintPolicy();
    loadStatus();
    return [columns, policy];
}
