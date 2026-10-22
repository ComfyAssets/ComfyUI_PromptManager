import { createButton } from '../shared/toolkit/core.js';
import { imageKey, toggleSelection, selectionLabel } from './images-state.mjs';
import { inspectPath, toInspectionRecord } from './inspection-state.mjs';
import { BUNDLE_FILENAME, metadataBundle, collectImagesMetadata, exportOutcome } from './images-export.mjs';
import { CATEGORY_DIALOG, CATEGORY_FAILED, CATEGORY_MAX_LENGTH, CATEGORY_NONE_LINKED, categoryOutcome, categoryTargets, updatedCount } from './images-category.mjs';
import { openInputDialog } from './workspace-dialogs.mjs';
import { showWorkspaceToast } from './toast.mjs';

const CHECKBOX = '.image-overlays input[type="checkbox"]';
const CATEGORY_URL = '/prompt_manager/bulk/category';

function el(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
}

/**
 * The Images page's checked cards and the concept's selection bar (#249 D8; app.js renderImages, updateBulk and the
 * bulk handlers). `element` is the `#bulkbar` the page places between its filter row and gallery layout: empty while
 * nothing is checked, else "N selected" and the real actions in the concept's order. The page renders each card's
 * checkbox from `isChecked`; this module follows the boxes through the grid's `change` events.
 *
 * - `getItems()` is the listing in its order; `checked` restores a saved visit's selection.
 * - `onCategorySet()` reloads the categories and the listing after a successful Set category (the reload clears the
 *   selection through `clear`); focus then moves to `focusFallback` if it was on the hidden action.
 * - `retain(kept)` drops the checked files a refreshed listing no longer holds (a tag save under a filter, I7).
 */
export function mountSelectionBar({ scope, api, grid, getItems, focusFallback, onCategorySet, checked: initial = Object.freeze(new Set()) }) {
    let checked = initial;
    let exporting = false, categorizing = false;
    const element = el('div');
    element.id = 'bulkbar';
    const bulk = el('div', undefined, 'bulkbar'), count = el('strong');
    const setCategory = createButton('Set category');
    scope.listen(setCategory, 'click', () => openCategory(setCategory));
    const exportMetadata = createButton('Export metadata');
    scope.listen(exportMetadata, 'click', () => exportChecked(exportMetadata));
    const clearButton = createButton('Clear');
    // Clear hides the bar it sits in, so focus moves to the page heading instead of falling to the page.
    scope.listen(clearButton, 'click', () => { clear(); focusFallback.focus({ preventScroll: true }); });
    bulk.append(count, setCategory, exportMetadata, clearButton);

    /** The count updates in place: the same bar and `strong`, its text written only when it changes. */
    function render() {
        if (!checked.size) { bulk.remove(); return; }
        const label = selectionLabel(checked.size);
        if (count.textContent !== label) count.textContent = label;
        if (bulk.parentNode !== element) element.append(bulk);
    }
    function clear() {
        if (!checked.size) return;
        checked = Object.freeze(new Set());
        for (const box of grid.querySelectorAll(CHECKBOX)) box.checked = false;
        render();
    }
    /** Keep only the checked files `kept(key)` still lists (a refreshed result), so the count is what the actions apply to. */
    function retain(kept) {
        const next = [...checked].filter(kept);
        if (next.length === checked.size) return;
        checked = Object.freeze(new Set(next));
        render();
    }
    function checkedImages() {
        return getItems().filter(image => checked.has(imageKey(image)));
    }
    // Every card carries its image key; its only form control is the checkbox, so a toggle never opens or inspects.
    scope.listen(grid, 'change', event => {
        const box = event.target;
        if (!box.matches(CHECKBOX)) return;
        checked = toggleSelection(checked, box.closest('article[data-image-key]').dataset.imageKey, box.checked);
        render();
    });

    /** Busy until it settles: `aria-disabled` now, released in a `finally` so a throw cannot leave it stuck. */
    function busy(button) {
        button.setAttribute('aria-disabled', 'true');
        return () => button.removeAttribute('aria-disabled');
    }
    /**
     * Export metadata: the checked images' inspector envelopes in listing order, as one JSON download. A second press
     * while it runs is ignored and the selection is kept. Leaving the page aborts the reads: no download, no toast.
     */
    async function exportChecked(button) {
        if (exporting) return;
        exporting = true;
        const release = busy(button);
        try {
            const { envelopes, failed } = await collectImagesMetadata(checkedImages(),
                async image => toInspectionRecord(await api.get(inspectPath(image), { signal: scope.signal }), image));
            if (scope.signal.aborted) return;
            if (envelopes.length) downloadJson(BUNDLE_FILENAME, metadataBundle(envelopes, new Date().toISOString()));
            showWorkspaceToast(exportOutcome(envelopes.length, envelopes.length + failed));
        } finally {
            exporting = false;
            release();
        }
    }
    /** A Blob, an anchor and a deferred revoke, as the concept's exportSample does. */
    function downloadJson(filename, value) {
        const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }));
        let timer, released = false;
        const release = () => { if (released) return; released = true; clearTimeout(timer); URL.revokeObjectURL(url); };
        scope.cleanup(release);
        const anchor = el('a'); anchor.href = url; anchor.download = filename;
        element.append(anchor); anchor.click(); anchor.remove();
        timer = setTimeout(release, 1000);
    }
    /**
     * Set category (app.js bulk-category → simpleInput): images have no category, so Apply sets it on the selection's
     * distinct linked positives, taken when the (modal) dialog opens. With no linked image there is nothing to change:
     * a toast says so and no dialog opens.
     */
    function openCategory(button) {
        if (categorizing) return;
        const targets = categoryTargets(checkedImages());
        if (!targets.promptIds.length) { showWorkspaceToast(CATEGORY_NONE_LINKED); return; }
        openInputDialog({ scope, trigger: button, title: CATEGORY_DIALOG.title, label: CATEGORY_DIALOG.label,
            action: CATEGORY_DIALOG.action, maxLength: CATEGORY_MAX_LENGTH, onApply: category => applyCategory(button, targets, category) });
    }
    /**
     * The write carries no page signal: once sent it finishes even if the page is left (`writing` then tells the page
     * not to save a visit that predates it). A failure keeps the selection; a success reloads through `onCategorySet`.
     */
    async function applyCategory(button, { promptIds, skipped }, category) {
        categorizing = true;
        const release = busy(button);
        let result = null;
        try {
            result = await api.post(CATEGORY_URL, { prompt_ids: promptIds, category });
        } catch {
            // Reported below; the selection stays as it was.
        } finally {
            categorizing = false;
            release();
        }
        if (scope.signal.aborted) return;
        if (!result) { showWorkspaceToast(CATEGORY_FAILED); return; }
        showWorkspaceToast(categoryOutcome(updatedCount(result, promptIds.length), skipped));
        const focused = document.activeElement === button;
        onCategorySet();
        // The reload hides the bar with the focused action in it, as Clear does: focus moves to the page heading.
        if (focused) focusFallback.focus({ preventScroll: true });
    }

    render();
    return {
        element,
        isChecked: key => checked.has(key),
        checked: () => checked,
        clear,
        retain,
        /** Whether a Set category write is still in flight. */
        writing: () => categorizing,
    };
}
