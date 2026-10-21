/**
 * Scan for metadata (#249 Activity-1, D55): Images → Import → "Scan for metadata" → Review scan opens the concept's
 * "Review metadata scan". Its backup box downloads the existing `/prompt_manager/backup` copy and toasts the outcome;
 * Start scan starts the session job through the Activity store, which toasts "Scan started.", and opens the job's view.
 * A start refused because another record-changing job runs toasts "Another job is running: <title>." with a link to
 * that job (D57).
 */
import { openScanReviewDialog } from './workspace-dialogs.mjs';
import { showWorkspaceToast } from './toast.mjs';
import { navigateTo } from './state.mjs';

export const BACKUP_URL = '/prompt_manager/backup';
export const BACKUP_DONE = 'Database backup downloaded.';
export const BACKUP_FAILED = 'The database could not be backed up. Try again.';
export const START_FAILED = 'The scan could not start. Try again.';
const FALLBACK_NAME = 'prompts_backup.db';

/** The download name from a Content-Disposition header, or the fallback. */
export function backupFilename(disposition) {
    const match = /filename="?([^";]+)"?/i.exec(disposition || '');
    return match ? match[1] : FALLBACK_NAME;
}

/** Download the prompts database copy the legacy admin page also uses; toasts the outcome. */
export async function downloadDatabaseBackup({ fetchUrl = window.ApiClient.fetchUrl, notify = showWorkspaceToast } = {}) {
    try {
        const response = await fetchUrl(BACKUP_URL);
        if (!response.ok) throw new Error(`Backup failed with ${response.status}`);
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = backupFilename(response.headers.get('Content-Disposition'));
        document.body.append(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        notify(BACKUP_DONE);
        return true;
    } catch {
        notify(BACKUP_FAILED);
        return false;
    }
}

/** The toast for a refused start, linking the job that holds the slot. */
export function busyToast(running) {
    if (!running) return 'Another job is running. Try again when it ends.';
    return ['Another job is running: ', { text: running.title, href: `#/activity/${running.id}` }, '.'];
}

/** Start the scan job and open its view; a refused or failed start says why and stays on the page. */
export async function startMetadataScan({ store, scope, notify = showWorkspaceToast, navigate = navigateTo }) {
    try {
        const result = await store.start('scan');
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

/** Open "Review metadata scan" for `sources` output folders. */
export function openMetadataScan({ scope, store, sources, trigger = null }) {
    openScanReviewDialog({ scope, sources, trigger,
        onBackup: () => downloadDatabaseBackup(),
        onStart: () => startMetadataScan({ store, scope }) });
}
