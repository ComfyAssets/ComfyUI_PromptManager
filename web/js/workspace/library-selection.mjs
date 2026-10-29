import { createButton } from '../shared/toolkit/core.js';
import { selectionLabel, toggleSelection } from './images-state.mjs';
import { applyKeys, rangeKeys } from './images-multiselect.mjs';
import { CATEGORY_DIALOG, CATEGORY_FAILED, CATEGORY_MAX_LENGTH, setOutcome } from './images-category.mjs';
import { NO_TAGS, parseBulkTags } from './images-tagging-bulk.mjs';
import { openInputDialog } from './workspace-dialogs.mjs';
import { countOf } from './plural.mjs';
import { showWorkspaceToast } from './toast.mjs';

/**
 * The Prompt library's selection and its bulk actions (D148, beyond the concept): "Add tags" (#442) and "Set
 * category" (#437), the legacy admin page's bulk Add Tags and Set Category in the workspace. Built from the concept's
 * Images parts only: each card's `.image-overlays` checkbox and the `.bulkbar` row ("N selected", the actions in the
 * Images bar's order, Clear). The selection is a set of
 * prompt ids over the shelf's loaded items, so windowed-out cards keep their state; Shift+click on a checkbox applies
 * the anchor's range (the Images helpers, D135). The dialog, the range helpers, the count and the toast text are the
 * Images bar's own; one bar core shared with images-selection.mjs is a follow-up (D148).
 *
 * Apply posts `{prompt_ids, category|tags, expected_epoch}` to `POST /prompt_manager/bulk/category` or `/bulk/tags`:
 * the epoch the shelf was read under, so a restored storage answers 409 `storage-changed` and nothing is written.
 * Add tags writes prompt tags only, never image tags or auto-tags.
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
export const TAGS_URL = '/prompt_manager/bulk/tags';
export const TAG_OPTIONS_URL = '/prompt_manager/tags';
export const PROMPT_TAGS_DIALOG = Object.freeze({ title: 'Tag selected prompts', label: 'Tags, separated by commas', action: 'Apply' });
// What the editor's save accepts for one prompt (`validate_tags`): the route refuses more.
export const MAX_PROMPT_TAGS = 20;
export const MAX_TAG_LENGTH = 50;
export const TAG_PROBLEMS = Object.freeze({ none: NO_TAGS, many: 'Enter at most 20 tags.',
    long: 'A tag can have at most 50 characters.', control: 'A tag cannot contain control characters.' });
export const TAGS_FAILED = 'Tags could not be added. Try again.';
export const TAGS_STORAGE_CHANGED = 'Prompt storage was restored since this page loaded. No tags were added.';
export const TAGS_UNCONFIRMED = 'Adding the tags could not be confirmed. The Library was reloaded.';
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
export function writeOutcome(result, failure, kind = 'category') {
    const copy = WRITES[kind];
    if (failure === null && result?.success) {
        const found = ['updated', 'unchanged', 'over_limit'].reduce((sum, key) => sum + (Number(result[key]) || 0), 0);
        if (!found) return { toast: NONE_FOUND, committed: false, reload: true };
        return { toast: copy.success(result), committed: (Number(result.updated) || 0) > 0, reload: true };
    }
    if (failure?.code === 'storage-changed') return { toast: copy.storage, committed: false, reload: true };
    const status = failure?.status;
    if (!Number.isInteger(status) || UNCONFIRMED_STATUSES.has(status)) return { toast: copy.unconfirmed, committed: true, reload: true };
    return { toast: copy.failed, committed: false, reload: false };
}

/** The Add tags toast (#442): what was added, then what already had them, was skipped or could not be found. */
export function tagsOutcome(result) {
    const updated = Number(result?.updated) || 0, unchanged = Number(result?.unchanged) || 0;
    const skipped = Number(result?.over_limit) || 0, missing = Number(result?.not_found) || 0;
    const names = Array.isArray(result?.tags) ? result.tags.join(', ') : '';
    const parts = [];
    if (updated) parts.push(`Added “${names}” to ${countOf(updated, 'prompt')}.`);
    if (unchanged) parts.push(`${countOf(unchanged, 'prompt')} already had ${updated ? 'them' : `“${names}”`}.`);
    if (skipped) parts.push(`${countOf(skipped, 'prompt')} would pass ${MAX_PROMPT_TAGS} tags and ${skipped === 1 ? 'was' : 'were'} skipped.`);
    if (missing) parts.push(`${missing.toLocaleString('en-US')} could not be found.`);
    return parts.join(' ');
}

const WRITES = Object.freeze({
    category: { success: result => setOutcome(result, result.category, 'prompt'), storage: STORAGE_CHANGED,
        unconfirmed: UNCONFIRMED, failed: CATEGORY_FAILED },
    tags: { success: tagsOutcome, storage: TAGS_STORAGE_CHANGED, unconfirmed: TAGS_UNCONFIRMED, failed: TAGS_FAILED },
});

/**
 * "Select all N loaded" (#444 ruling, D148; the legacy page's select-all checkbox): offered while some loaded prompts
 * are unchecked and no more are loaded than an action accepts. It sits where D135's "Select all N matching" sits on
 * Images, but that offer appears once every loaded image is checked; this one reaches only the loaded prompts.
 */
export function offersLoaded(selected, keys) {
    return selected.size > 0 && keys.length <= MAX_CATEGORY_PROMPTS && keys.some(key => !selected.has(key));
}
export const loadedOffer = count => `Select all ${count.toLocaleString('en-US')} loaded`;

/** The Add tags field's check before Apply: the parsed names, as `validate_tags` would judge them. */
export function tagsProblem(text) {
    const tags = parseBulkTags(text);
    if (!tags.length) return TAG_PROBLEMS.none;
    if (tags.length > MAX_PROMPT_TAGS) return TAG_PROBLEMS.many;
    if (tags.some(tag => tag.length > MAX_TAG_LENGTH)) return TAG_PROBLEMS.long;
    if (tags.some(tag => /[\x00-\x1f]/.test(tag))) return TAG_PROBLEMS.control;
    return null;
}

/** The field's check before Apply: what `validate_category` refuses beyond the length the field already limits. */
export function categoryProblem(value) {
    return /[\x00-\x1f]/.test(value) ? CONTROL_CHARACTERS : null;
}

/**
 * Mount the selection on the shelf `grid`. `getItems()` is the shelf's loaded items in order, `getEpoch()` the
 * storage epoch they were read under, `getCategories()` the Library's category list (the field's suggestions).
 * `onCommitted(kind, receipt)` (`category` or `tags`) runs after a write that may have changed prompts, even once the page is gone (the page
 * invalidates its cached Library views); `onReload()` then reloads the mounted shelf (which calls `clear`). Focus moves
 * to `focusFallback` when it was on the bar the reload hides.
 */
export function mountLibrarySelection({ scope, api, grid, getItems, getEpoch, getCategories, focusFallback, onCommitted,
    onReload }) {
    let checked = Object.freeze(new Set()), anchor = null, shiftBox = null, writing = false;
    const element = el('div');
    element.id = 'library-bulkbar';
    const bulk = el('div', undefined, 'bulkbar'), count = el('strong');
    const addTags = createButton('Add tags');
    const setCategory = createButton('Set category');
    const clearButton = createButton('Clear');
    const selectLoaded = createButton('');
    const actions = [addTags, setCategory, selectLoaded];
    bulk.append(count, addTags, setCategory, clearButton);
    scope.listen(selectLoaded, 'click', () => {
        if (writing) return;
        selectAll();
        // The pressed offer left the bar: focus moves to its first action, as after D135's offer.
        addTags.focus({ preventScroll: true });
    });
    scope.listen(addTags, 'click', () => openTags());
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
        const keys = ids();
        if (offersLoaded(checked, keys)) {
            const offer = loadedOffer(keys.length);
            if (selectLoaded.textContent !== offer) selectLoaded.textContent = offer;
            if (count.nextElementSibling !== selectLoaded) count.after(selectLoaded);
        } else selectLoaded.remove();
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
    /** Every loaded prompt, windowed-out cards included; the anchor stays. */
    function selectAll() {
        update(applyKeys(checked, ids(), true));
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

    /** The selected loaded prompts and the shelf epoch, or null after a toast says why nothing can be sent. */
    function target(failed) {
        if (writing) return null;
        const loaded = new Set(ids()), selected = [...checked].filter(id => loaded.has(id));
        if (selected.length > MAX_CATEGORY_PROMPTS) { showWorkspaceToast(TOO_MANY_PROMPTS); return null; }
        const epoch = getEpoch();
        if (!selected.length || typeof epoch !== 'string') { showWorkspaceToast(failed); return null; }
        return { prompt_ids: selected, expected_epoch: epoch };
    }
    function openCategory() {
        const chosen = target(CATEGORY_FAILED);
        if (!chosen) return;
        openInputDialog({ scope, trigger: setCategory, title: PROMPT_CATEGORY_DIALOG.title, label: PROMPT_CATEGORY_DIALOG.label,
            action: PROMPT_CATEGORY_DIALOG.action, maxLength: CATEGORY_MAX_LENGTH, suggestions: getCategories(),
            validate: categoryProblem, onApply: category => write(CATEGORY_URL, { ...chosen, category }, 'category') });
    }
    /** Add tags (#442): the tag vocabulary as suggestions, the editor's list; a failed read opens without them. */
    async function openTags() {
        const chosen = target(TAGS_FAILED);
        if (!chosen) return;
        let suggestions = null;
        try {
            const result = await api.get(TAG_OPTIONS_URL, { signal: scope.signal, cache: 'no-store' });
            if (Array.isArray(result?.tags)) suggestions = result.tags.filter(tag => typeof tag === 'string');
        } catch {
            // Suggestions are auxiliary; the field works without them.
        }
        if (scope.signal.aborted) return;
        openInputDialog({ scope, trigger: addTags, ...PROMPT_TAGS_DIALOG, suggestions, validate: tagsProblem,
            onApply: text => write(TAGS_URL, { ...chosen, tags: parseBulkTags(text) }, 'tags') });
    }
    /** The write carries no page signal: once sent it completes even if the page is left. A failure keeps the selection. */
    async function write(url, body, kind) {
        writing = true;
        for (const button of actions) button.setAttribute('aria-disabled', 'true');
        let result = null, failure = null;
        try {
            result = await api.post(url, body);
        } catch (error) {
            failure = error;
        } finally {
            writing = false;
            for (const button of actions) button.removeAttribute('aria-disabled');
        }
        const outcome = writeOutcome(result, failure, kind);
        // A left page still invalidates what it cached, so a return never shows the old categories.
        if (outcome.committed) onCommitted(kind, result?.success ? result : { phase: 'unconfirmed' });
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
        hasSelection: () => checked.size > 0,
        clear,
        selectAll,
        /** A loaded batch: the count and the offer follow it. */
        listingChanged: () => { if (checked.size) render(); },
        /** Whether a Set category write is still in flight. */
        writing: () => writing,
    };
}
