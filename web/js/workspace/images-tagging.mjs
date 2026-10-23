/**
 * The Images page's side of image tagging (#249 PR C): a card's Tag opens the Tag image dialog, and every confirmed
 * tag save re-reads what it may have changed on a mounted page.
 *
 * Under a Tags filter or a search the saved file may start or stop matching, so the loaded range is re-read in place
 * (`createImageStream().refresh()`), keeping the scroll and the selection while it still matches; a failed re-read
 * toasts and leaves the cards as they were. The selected file's inspection is then re-read quietly (its envelope
 * carries the image tags), without a loading flash.
 */
import { isTagButton, onImageTagsSaved, openImageTagging } from './image-tagging.mjs';
import { showWorkspaceToast } from './toast.mjs';

export const REFRESH_FAILED = 'Images could not be refreshed after the tag save. Refresh image collection to retry.';

/** Open Tag image for `image` when `action` is its card's Tag button (without selecting the card, as the concept's
 * `image-tag` does); false for any other control. */
export function openCardTagging({api, scope, image, action, embedded}) {
    if (!isTagButton(action)) return false;
    openImageTagging({api, scope, image, source: embedded ? 'library' : 'images', trigger: action});
    return true;
}

/**
 * Follow confirmed tag saves until `scope` ends. `filtered()` tells whether a Tags filter or a search is active,
 * `selection()` is the selected image key, and `onRefreshing(busy)` marks Load more busy while the re-read runs.
 * Returns `refresh(keys)`, the same re-read for the files a bulk tagging job tagged (once per job, D85).
 */
export function followImageTagSaves({scope, stream, inspection, filtered, selection, onRefreshing}) {
    scope.cleanup(onImageTagsSaved(({target}) => refresh(new Set([`${target.root_index}:${target.path}`]))));
    return refresh;

    async function refresh(keys) {
        if (filtered()) {
            onRefreshing(true);
            try {
                await stream.refresh();
            } catch {
                if (!scope.signal.aborted) showWorkspaceToast(REFRESH_FAILED);
            } finally {
                if (!scope.signal.aborted) onRefreshing(false);
            }
        }
        if (!scope.signal.aborted && keys.has(selection())) inspection.refresh();
    }
}
