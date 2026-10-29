import { createButton } from '../shared/toolkit/core.js';
import { selectionLabel, toggleSelection } from './images-state.mjs';
import { applyKeys, rangeKeys } from './images-multiselect.mjs';
import { CATEGORY_DIALOG, CATEGORY_FAILED, CATEGORY_MAX_LENGTH, setOutcome } from './images-category.mjs';
import { openInputDialog } from './workspace-dialogs.mjs';
import { showWorkspaceToast } from './toast.mjs';

/**
 * The Prompt library's selection and its one bulk action, "Set category" (#437, D148, beyond the concept): the
 * legacy admin page's "Set Category" in the workspace. Built from the concept's Images parts only: each card's
 * `.image-overlays` checkbox and the `.bulkbar` row ("N selected", the action, Clear). The selection is a set of
 * prompt ids over the shelf's loaded items, so windowed-out cards keep their state; Shift+click on a checkbox applies
 * the anchor's range (the Images helpers, D135). The dialog, the range helpers, the count and the toast text are the
 * Images bar's own; one bar core shared with images-selection.mjs is a follow-up (D148).
 *
 * Apply posts `{prompt_ids, category, expected_epoch}` to `POST /prompt_manager/bulk/category`: the epoch the shelf
 * was read under, so a restored storage answers 409 `storage-changed` and nothing is written.
 */
export const CATEGORY_URL = '/prompt_manager/bulk/category';
export const PROMPT_CATEGORY_DIALOG = Object.freeze({ ...CATEGORY_DIALOG, title: 'Categorize selected prompts' });
// The route's cap (`BULK_CATEGORY_MAX_PROMPTS`): more opens nothing.
export const MAX_CATEGORY_PROMPTS = 1000;
export const TOO_MANY_PROMPTS = 'Select at most 1,000 prompts.';
export const STORAGE_CHANGED = 'Prompt storage was restored since this page loaded. No category was changed.';
export const UNCONFIRMED = 'The category change could not be confirmed. The Library was reloaded.';
export const NONE_FOUND = 'None of the selected prompts could be found. The Library was reloaded.';
export const CONTROL_CHARACTERS = 'A category cannot contain control characters.';
export { CATEGORY_FAILED };
// Answers that may follow a committed write (the Library editor's unknown outcome): no status, or a gateway's.
const UNCONFIRMED_STATUSES = new Set([502, 503, 504]);

const CHECKBOX = '.image-overlays input[type="checkbox"]';
const CARD = 'article[data-prompt-id]';

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

/**
 * What a settled write means for the page: `toast` to show, whether the write may have `committed` (the page then
 * invalidates its cached Library views) and whether the shelf must `reload` (which clears the selection).
 */
export function writeOutcome(result, failure) {
    if (failure === null && result?.success) {
        const found = (Number(result.updated) || 0) + (Number(result.unchanged) || 0);
        if (!found) return { toast: NONE_FOUND, committed: false, reload: true };
        return { toast: setOutcome(result, result.category, 'prompt'), committed: true, reload: true };
    }
    if (failure?.code === 'storage-changed') return { toast: STORAGE_CHANGED, committed: false, reload: true };
    const status = failure?.status;
    if (!Number.isInteger(status) || UNCONFIRMED_STATUSES.has(status)) return { toast: UNCONFIRMED, committed: true, reload: true };
    return { toast: CATEGORY_FAILED, committed: false, reload: false };
}

/** The field's check before Apply: what `validate_category` refuses beyond the length the field already limits. */
export function categoryProblem(value) {
    return /[\x00-\x1f]/.test(value) ? CONTROL_CHARACTERS : null;
}

/**
 * Mount the selection on the shelf `grid`. `getItems()` is the shelf's loaded items in order, `getEpoch()` the
 * storage epoch they were read under, `getCategories()` the Library's category list (the field's suggestions).
 * `onCommitted(receipt)` runs after a write that may have changed prompts, even once the page is gone (the page
 * invalidates its cached Library views); `onReload()` then reloads the mounted shelf (which calls `clear`). Focus moves
 * to `focusFallback` when it was on the bar the reload hides.
 */
export function mountLibrarySelection({ scope, api, grid, getItems, getEpoch, getCategories, focusFallback, onCommitted,
    onReload }) {
    let checked = Object.freeze(new Set()), anchor = null, shiftBox = null, writing = false;
    const element = el('div');
    element.id = 'library-bulkbar';
    const bulk = el('div', undefined, 'bulkbar'), count = el('strong');
    const setCategory = createButton('Set category');
    const clearButton = createButton('Clear');
    bulk.append(count, setCategory, clearButton);
    scope.listen(setCategory, 'click', () => openCategory());
    scope.listen(clearButton, 'click', () => { clear(); focusFallback.focus({ preventScroll: true }); });
    // Escape in the bar clears the selection; the bar hides, so focus moves to the page heading as after Clear.
    scope.listen(element, 'keydown', event => {
        if (event.key !== 'Escape' || !checked.size) return;
        event.preventDefault();
        clear();
        focusFallback.focus({ preventScroll: true });
    });

    const ids = () => getItems().map(item => item.id);
    function render() {
        if (!checked.size) { bulk.remove(); return; }
        const label = selectionLabel(checked.size);
        if (count.textContent !== label) count.textContent = label;
        if (bulk.parentNode !== element) element.append(bulk);
    }
    function paint() {
        for (const box of grid.querySelectorAll(CHECKBOX)) {
            const on = checked.has(Number(box.closest(CARD).dataset.promptId));
            if (box.checked !== on) box.checked = on;
        }
    }
    function update(next) { checked = next; paint(); render(); }
    function clear() {
        anchor = null;
        if (checked.size) update(Object.freeze(new Set()));
    }
    // The checkbox toggles natively; a Shift+click applies the anchor's range with the box's new state.
    scope.listen(grid, 'click', event => {
        const box = event.target.closest('.image-overlays > label');
        if (box && event.shiftKey) shiftBox = Number(box.closest(CARD).dataset.promptId);
    });
    scope.listen(grid, 'change', event => {
        if (!event.target.matches(CHECKBOX)) return;
        const id = Number(event.target.closest(CARD).dataset.promptId), on = event.target.checked;
        if (shiftBox === id) {
            const keys = rangeKeys(ids(), anchor, id);
            if (anchor === null || keys.length === 1) anchor = id;
            update(applyKeys(checked, keys, on));
        } else {
            anchor = id;
            update(toggleSelection(checked, id, on));
        }
        shiftBox = null;
    });

    /** The card's overlay: the concept's `.image-overlays > label > input`, checked from the selection. */
    function overlayFor(item, name) {
        const overlays = el('div', undefined, 'image-overlays'), label = el('label'), box = el('input');
        box.type = 'checkbox';
        box.checked = checked.has(item.id);
        box.setAttribute('aria-label', `Select ${name}`);
        label.append(box);
        overlays.append(label);
        return overlays;
    }

    function openCategory() {
        if (writing) return;
        const loaded = new Set(ids()), selected = [...checked].filter(id => loaded.has(id));
        if (selected.length > MAX_CATEGORY_PROMPTS) { showWorkspaceToast(TOO_MANY_PROMPTS); return; }
        const epoch = getEpoch();
        if (!selected.length || typeof epoch !== 'string') { showWorkspaceToast(CATEGORY_FAILED); return; }
        openInputDialog({ scope, trigger: setCategory, title: PROMPT_CATEGORY_DIALOG.title, label: PROMPT_CATEGORY_DIALOG.label,
            action: PROMPT_CATEGORY_DIALOG.action, maxLength: CATEGORY_MAX_LENGTH, suggestions: getCategories(),
            validate: categoryProblem, onApply: category => write({ prompt_ids: selected, category, expected_epoch: epoch }) });
    }
    /** The write carries no page signal: once sent it completes even if the page is left. A failure keeps the selection. */
    async function write(body) {
        writing = true;
        setCategory.setAttribute('aria-disabled', 'true');
        let result = null, failure = null;
        try {
            result = await api.post(CATEGORY_URL, body);
        } catch (error) {
            failure = error;
        } finally {
            writing = false;
            setCategory.removeAttribute('aria-disabled');
        }
        const outcome = writeOutcome(result, failure);
        // A left page still invalidates what it cached, so a return never shows the old categories.
        if (outcome.committed) onCommitted(result?.success ? result : { phase: 'unconfirmed' });
        if (scope.signal.aborted) return;
        const focused = element.contains(document.activeElement);
        showWorkspaceToast(outcome.toast);
        if (!outcome.reload) return;
        onReload();
        if (focused) focusFallback.focus({ preventScroll: true });
    }

    render();
    return {
        element,
        overlayFor,
        isChecked: id => checked.has(id),
        clear,
        /** Whether a Set category write is still in flight. */
        writing: () => writing,
    };
}
