/**
 * Scan for metadata (#249 Activity-1, D55): Images → Import → "Scan for metadata" → Review scan opens the concept's
 * "Review metadata scan". Its backup box downloads the existing `/prompt_manager/backup` copy and toasts the outcome;
 * Start scan starts the session job through the Activity store over the chosen scope (new and unresolved files, or
 * all files with the D58 checkbox), which toasts "Scan started.", and opens the job's view.
 * A start refused because another record-changing job runs toasts "Another job is running: <title>." with a link to
 * that job (D57).
 */
import { openScanReviewDialog } from './workspace-dialogs.mjs';
import { showWorkspaceToast } from './toast.mjs';
import { navigateTo } from './state.mjs';

export const BACKUP_URL = '/prompt_manager/backup';
export const BACKUP_DONE = 'Database backup downloaded.';
export const BACKUP_FAILED = 'The database could not be backed up. Try again.';
export const REPORT_DONE = 'Job report downloaded.';
export const REPORT_FAILED = 'The report could not be downloaded. Try again.';
const JOBS_REPORT_BASE = '/prompt_manager/jobs';
export const START_FAILED = 'The scan could not start. Try again.';
const FALLBACK_NAME = 'prompts_backup.db';
const REPORT_FALLBACK_NAME = 'prompt-manager-report.json';

/** The download name from a Content-Disposition header, or `fallback`. */
export function backupFilename(disposition, fallback = FALLBACK_NAME) {
    const match = /filename="?([^";]+)"?/i.exec(disposition || '');
    return match ? match[1] : fallback;
}

/** Save a fetched response through a temporary link, named by `filename`. */
function saveResponse(blob, filename) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Download the prompts database copy the legacy admin page also uses; toasts the outcome. */
export async function downloadDatabaseBackup({ fetchUrl = window.ApiClient.fetchUrl, notify = showWorkspaceToast } = {}) {
    try {
        const response = await fetchUrl(BACKUP_URL);
        if (!response.ok) throw new Error(`Backup failed with ${response.status}`);
        const blob = await response.blob();
        saveResponse(blob, backupFilename(response.headers.get('Content-Disposition')));
        notify(BACKUP_DONE);
        return true;
    } catch {
        notify(BACKUP_FAILED);
        return false;
    }
}

/**
 * Download a job's JSON report (Activity-2, D59) through the api-client URL, so a proxy mount works; toasts the outcome
 * unless `signal` (the view's scope) was aborted meanwhile.
 */
export async function downloadJobReport(jobId, { fetchUrl = window.ApiClient.fetchUrl, notify = showWorkspaceToast,
    signal = null } = {}) {
    try {
        const response = await fetchUrl(`${JOBS_REPORT_BASE}/${encodeURIComponent(jobId)}/report`);
        if (!response.ok) throw new Error(`Report failed with ${response.status}`);
        const blob = await response.blob();
        saveResponse(blob, backupFilename(response.headers.get('Content-Disposition'), REPORT_FALLBACK_NAME));
        // The file is saved either way; a view left meanwhile (`signal` aborted) shows no toast.
        if (!signal?.aborted) notify(REPORT_DONE);
        return true;
    } catch {
        if (!signal?.aborted) notify(REPORT_FAILED);
        return false;
    }
}

/** The toast for a refused start, linking the job that holds the slot. */
export function busyToast(running) {
    if (!running) return 'Another job is running. Try again when it ends.';
    return ['Another job is running: ', { text: running.title, href: `#/activity/${running.id}` }, '.'];
}

/**
 * Start the scan job over `files` ('new': new and unresolved files, or 'all') and open its view; a refused or failed
 * start says why and stays on the page. `roots` (System › Sources, S4) limits it to those effective source indexes.
 */
export async function startMetadataScan({ store, scope, files = 'new', roots = null, notify = showWorkspaceToast,
    navigate = navigateTo }) {
    try {
        const result = await store.start('scan', roots ? { scope: files, roots } : { scope: files });
        if (scope.signal.aborted) return;
        if (result.busy !== undefined) {
            notify(busyToast(result.busy));
            return;
        }
        navigate(`#/activity/${result.job.id}`);
    } catch {
        if (!scope.signal.aborted) notify(START_FAILED);
    }
}

/**
 * Open "Review metadata scan" for `sources` output folders; with `roots` (one source's index, System › Sources) the
 * scan reads only those.
 */
export function openMetadataScan({ scope, store, sources, roots = null, trigger = null }) {
    openScanReviewDialog({ scope, sources, trigger,
        onBackup: () => downloadDatabaseBackup(),
        onStart: ({ scope: files }) => startMetadataScan({ store, scope, files, roots }) });
}
