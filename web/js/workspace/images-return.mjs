/**
 * Images › Show removed images › Return to library (#498, D162).
 *
 * A removed image is a file "Remove from the library only" (#480) hid: it stays on disk without records. Return to
 * library posts the checked images (or "all N matching" in the removed view) to `POST /images/removed/return`, which
 * starts a scan job, "Return images to the library": the marks go, then those files are scanned, so their prompt
 * links come back from their metadata. Tags, categories, collections and favorites do not come back. Nothing is
 * deleted or moved, so there is no review (as the Library's Restore, D152).
 */
import { followJobEnd } from './images-tagging-bulk.mjs';
import { showWorkspaceToast } from './toast.mjs';
import { countOf } from './plural.mjs';

export const RETURN_URL = '/prompt_manager/images/removed/return';
export const MAX_RETURN = 500;
export const TOO_MANY = 'Select at most 500 images.';
export const RETURN_LABEL = 'Return to library';
export const SHOW_REMOVED = 'Show removed images';
export const NOT_BACK = 'Tags, categories, collections and favorites do not come back.';
export const RETURN_FAILED = 'The images could not be returned. Nothing was changed.';
export const NONE_REMOVED = 'None of the selected images are removed from the library.';
export const SOURCE_CHANGED = 'The sources changed. Refresh the images and try again.';

/** The heading toggle's text: "Show removed images (N)", without the count while unknown. */
export function toggleLabel(total) {
    return Number.isSafeInteger(total) && total >= 0 ? `${SHOW_REMOVED} (${total.toLocaleString('en-US')})` : SHOW_REMOVED;
}

/** The removed view's caption: "N removed images · files on disk, hidden from Images". */
export function removedCaption(total) {
    return `${countOf(total, 'removed image')} · files on disk, hidden from Images`;
}

/** The removed view's empty text. */
export function removedEmpty(filtered) {
    return filtered ? 'No removed images match.' : 'No removed images.';
}

export function startToast(count) {
    return `Returning ${countOf(count, 'image')} to the library…`;
}

/** The toast when the return job ends: how many came back and how many the scan linked. */
export function finishedToast(job, returned) {
    if (job?.state === 'failed') return `Returning images stopped: ${job.status_line || 'see Activity'}.`;
    if (job?.state === 'cancelled') return 'Returning images was cancelled. The images already returned stay in the library.';
    const linked = Number(job?.counters?.added ?? 0) || 0;
    const back = `Returned ${countOf(returned, 'image')} to the library`;
    const links = linked ? `; ${countOf(linked, 'image')} linked to ${linked === 1 ? 'its prompt' : 'their prompts'}` : '';
    return `${back}${links}. ${NOT_BACK}`;
}

export function returnFailure(error) {
    if (error?.code === 'no-images') return NONE_REMOVED;
    if (error?.code === 'too-many-images') return TOO_MANY;
    if (error?.code === 'source-changed') return SOURCE_CHANGED;
    if (error?.code === 'busy' && error.message) return error.message;
    return RETURN_FAILED;
}

/**
 * Return `images` (the checked ones, or the inspector's one) or `filter` ("all N matching" in the removed view, `count`
 * of them). `trigger` is aria-disabled until the server answers; a second press is ignored. `onReturned(job)` runs
 * when the job ends (at once without an Activity store).
 */
export async function returnImages({api, scope, store, images = [], filter = null, count = images.length, trigger = null,
    onReturned = () => {}, notify = showWorkspaceToast}) {
    if (count > MAX_RETURN) { notify(TOO_MANY); return null; }
    if (!filter && !images.length) return null;
    if (trigger?.getAttribute('aria-disabled') === 'true') return null;
    trigger?.setAttribute('aria-disabled', 'true');
    let answer;
    try {
        const body = filter ? {filter: {...filter, removed: '1'}}
            : {items: images.map(({root_index, root_scope, path}) => ({root_index, root_scope, path}))};
        answer = await api.post(RETURN_URL, body, {signal: scope.signal});
    } catch (error) {
        if (!scope.signal.aborted) notify(returnFailure(error));
        return null;
    } finally {
        trigger?.removeAttribute('aria-disabled');
    }
    if (scope.signal.aborted) return null;
    const returned = Number(answer?.returned ?? count) || 0;
    notify(startToast(returned));
    let job;
    try {
        job = store ? store.track(answer.job) : answer.job;
    } catch {
        store?.load?.();
        onReturned(null);
        return null;
    }
    if (store) followJobEnd({store, scope, id: job.id, onEnd: ended => { notify(finishedToast(ended, returned)); onReturned(ended); }});
    else onReturned(job);
    return job;
}
