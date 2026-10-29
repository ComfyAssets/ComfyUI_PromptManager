/**
 * The Images selection bar's collection actions (#331 D138, design §7.2–7.4):
 *
 * - **Add to collection** (the concept's `bulk-collection` simpleInput, app.js a1392–1398): "Add selected images to
 *   collection", "Collection name", Add, with the existing names offered as suggestions. A name that matches a
 *   collection ignoring case adds to it; a new name creates it. The selection stays and the listing is not reloaded
 *   (an add never changes it).
 * - **Remove from collection** (beyond the concept, only while a collection filter is active): no confirmation; the
 *   listing then reloads through `onChanged`, keeping the checked files still listed.
 *
 * The routes take at most 500 explicit files. "All N matching" (#392, D135) is accepted only when every match is
 * loaded and checked and there are at most 500 of them; otherwise the action toasts "Select at most 500 images." and
 * sends nothing (ruling for #331 PR 3: the collection routes have no filter body).
 */
import { bulkTargets } from './images-tagging-bulk.mjs';
import { ADD_DIALOG_ACTION, ADD_DIALOG_LABEL, ADD_DIALOG_TITLE, ADD_FAILED_TOAST, FILES_NOT_FOUND_TOAST, MAX_COLLECTION_NAME_LENGTH,
    REMOVE_FAILED_TOAST, TOO_MANY_TOAST, addedMessage, isCollectionGone, normalizeCollectionName, removedMessage,
    writeFailure } from './collections.mjs';
import { openInputDialog } from './workspace-dialogs.mjs';
import { showWorkspaceToast } from './toast.mjs';

/**
 * The files an action sends for the checked `images` (listing order): `{targets}`, or `{refusal}` with the toast.
 * With `allMatching`, `total` is the listing's match count; only a fully loaded match is sent.
 */
export function collectionTargets(images, { allMatching = false, total = images.length } = {}) {
    if (allMatching && total !== images.length) return { refusal: TOO_MANY_TOAST };
    const targets = bulkTargets(images);
    if (!targets) return { refusal: TOO_MANY_TOAST };
    return targets.length ? { targets } : { refusal: FILES_NOT_FOUND_TOAST };
}

/**
 * Add to collection over `images` (the checked files, taken now). `collections` is the client store; `openInput` is
 * the dialog seam. A stale source (409) calls `onSourceChanged()` after its toast, so the page reloads the listing.
 */
export function openAddToCollection({ scope, collections, images, trigger = null, allMatching = false, total = images.length,
    notify = showWorkspaceToast, openInput = openInputDialog, onSourceChanged = null }) {
    const { targets, refusal } = collectionTargets(images, { allMatching, total });
    if (refusal) { notify(refusal); return; }
    openInput({ scope, trigger, title: ADD_DIALOG_TITLE, label: ADD_DIALOG_LABEL, action: ADD_DIALOG_ACTION,
        maxLength: MAX_COLLECTION_NAME_LENGTH, suggestions: collections.collections.map(item => item.name),
        onApply: async text => {
            const name = normalizeCollectionName(text);
            if (name === null) { notify(ADD_FAILED_TOAST); return; }
            try {
                const result = await collections.add({ name }, targets);
                if (!scope.signal.aborted) notify(addedMessage(result));
            } catch (error) {
                if (scope.signal.aborted) return;
                notify(writeFailure(error, ADD_FAILED_TOAST));
                if (error?.code === 'source-changed') onSourceChanged?.();
            }
        } });
}

/**
 * Remove the checked `images` from the open collection `collectionId`. Resolves once the toast is shown; a confirmed
 * remove, or a collection found deleted meanwhile, calls `onChanged()` so the page lists it again.
 */
export async function removeFromCollection({ scope, collections, collectionId, images, allMatching = false, total = images.length,
    notify = showWorkspaceToast, onChanged = null, onSourceChanged = null }) {
    const { targets, refusal } = collectionTargets(images, { allMatching, total });
    if (refusal) { notify(refusal); return; }
    try {
        const result = await collections.remove(collectionId, targets);
        if (scope.signal.aborted) return;
        notify(removedMessage(result.collection.name, result.removed));
        onChanged?.();
    } catch (error) {
        if (scope.signal.aborted) return;
        notify(writeFailure(error, REMOVE_FAILED_TOAST));
        if (isCollectionGone(error)) onChanged?.();
        else if (error?.code === 'source-changed') onSourceChanged?.();
    }
}
