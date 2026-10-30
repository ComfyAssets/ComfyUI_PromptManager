import { keepingPlace, publishBarHeight } from './sticky-chrome.mjs';
import { createButton } from '../shared/toolkit/core.js';
import { imageKey, toggleSelection, selectionLabel } from './images-state.mjs';
import { inspectPath, toInspectionRecord } from './inspection-state.mjs';
import { BUNDLE_FILENAME, metadataBundle, collectImagesMetadata, exportOutcome } from './images-export.mjs';
import { CATEGORY_DIALOG, CATEGORY_MAX_LENGTH, CATEGORY_URL, MAX_CATEGORY_IMAGES, REMOVE_DIALOG, USE_PROMPT_CATEGORY,
    NONE_FOUND, clearOutcome, failureMessage, removeQuestion, setOutcome } from './images-category.mjs';
import { closeWorkspaceDialog, openContentDialog, openInputDialog } from './workspace-dialogs.mjs';
import { TOO_MANY, bulkTargets, openAddTags, openAutoTag } from './images-tagging-bulk.mjs';
import { showWorkspaceToast } from './toast.mjs';
import { applyKeys, createLongPress, matchingLabel, matchingOffer, offersAllMatching, rangeKeys } from './images-multiselect.mjs';
import { openAddToCollection, removeFromCollection } from './images-collections.mjs';
import { openDeleteImages } from './images-delete.mjs';

const CHECKBOX = '.image-overlays input[type="checkbox"]';
const CHECKBOX_LABEL = '.image-overlays > label';
const CARD = 'article[data-image-key]';
// D135: Export metadata reads every image's inspection in the browser, so it waits for loaded images.
export const EXPORT_LOADED_ONLY = 'Export metadata reads each image in the browser, so it works on loaded images only.';

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
 * - `onCategorySet()` reloads the categories and the listing after a successful Set category or Use prompt category
 *   (the reload clears the selection through `clear`); focus then moves to `focusFallback` if it was on the hidden
 *   action. `getCategories()` gives the toolbar's categories, the field's suggestions.
 * - `retain(kept)` drops the checked files a refreshed listing no longer holds (a tag save under a filter, I7).
 * - With the Activity store `activity`, Add tags and Auto-tag selected (D85) follow the count, as the concept's Add tags
 *   leads its actions; `onTagged(keys)` re-reads the files a finished tagging job tagged. The selection stays.
 *
 * File-manager selection (#392, D135, beyond the concept's checkbox): the anchor is the last image toggled on its own;
 * Shift+click (a checkbox or anywhere on a card) applies the anchor's range, Ctrl/Cmd+click toggles a card without
 * opening it, a touch long-press starts touch selection mode in which taps toggle. `selectAll`, `extend` and
 * `clear` serve the grid's keys (images.mjs). `getListing()` gives `{total, hasMore, positiveCount}` and
 * `getFilter()` the listing filter: once every loaded image is checked and more match, the bar offers "Select all N
 * matching", whose actions send that filter to the server.
 *
 * Collections (#331 D138, design §7): with the client store `collections`, Add to collection follows Set category;
 * while a collection filter is active (`collection`, its id) Remove from collection follows it, and a confirmed remove
 * calls `onCollectionsChanged()` to reload the listing. A stale source on either calls `onSourceChanged()`.
 */
export function mountSelectionBar({ scope, api, grid, getItems, focusFallback, onCategorySet, activity = null, onTagged,
    getListing, getFilter, getCategories, checked: initial = Object.freeze(new Set()), collections = null,
    collection = null, onCollectionsChanged = null, onSourceChanged = null, onCollectionAdded = null, onDeleted = null, removedView = null,
    onReturn = null }) {
    let checked = initial;
    let exporting = false, categorizing = false;
    // D135: the range anchor, "all N matching" mode, touch selection mode and a pending Shift+click on a checkbox.
    let anchor = null, allMatching = false, touchMode = false, lastPointer = 'mouse', shiftBox = null;
    const element = el('div');
    element.id = 'bulkbar';
    publishBarHeight(scope, element);
    const bulk = el('div', undefined, 'bulkbar'), count = el('strong');
    const selectMatching = createButton('');
    scope.listen(selectMatching, 'click', () => {
        allMatching = true;
        render();
        focusAfterOffer();
    });
    const setCategory = createButton('Set category');
    scope.listen(setCategory, 'click', () => openCategory(setCategory));
    const exportMetadata = createButton('Export metadata');
    scope.listen(exportMetadata, 'click', () => { if (!allMatching) exportChecked(exportMetadata); });
    const clearButton = createButton('Clear');
    // Clear hides the bar it sits in, so focus moves to the page heading instead of falling to the page.
    scope.listen(clearButton, 'click', () => { clear(); focusFallback.focus({ preventScroll: true }); });
    bulk.append(count);
    if (activity) {
        const addTags = createButton('Add tags');
        scope.listen(addTags, 'click', () => openAddTags({ scope, store: activity, images: checkedImages(), trigger: addTags, onTagged,
            ...matchingTarget() }));
        const autoTag = createButton('Auto-tag selected'); // D85
        scope.listen(autoTag, 'click', () => openAutoTag({ api, scope, store: activity, images: checkedImages(), trigger: autoTag, onTagged,
            ...matchingTarget() }));
        bulk.append(addTags, autoTag);
    }
    bulk.append(setCategory);
    if (collections) {
        const addToCollection = createButton('Add to collection');
        scope.listen(addToCollection, 'click', () => openAddToCollection({ scope, collections, images: checkedImages(),
            trigger: addToCollection, ...collectionSelection(), onSourceChanged, onAdded: onCollectionAdded }));
        bulk.append(addToCollection);
    }
    if (collections && collection) {
        const removeButton = createButton('Remove from collection');
        scope.listen(removeButton, 'click', () => removeChecked(removeButton));
        bulk.append(removeButton);
    }
    bulk.append(exportMetadata);
    // #480 (D159): the concept's "Remove links…" (`danger`) place, after Export metadata and before Clear.
    if (onDeleted) {
        const deleteButton = createButton('Delete…', 'danger');
        scope.listen(deleteButton, 'click', () => openDeleteImages({ api, scope, store: activity, images: checkedImages(),
            ...matchingTarget(), trigger: deleteButton, onDeleted }));
        bulk.append(deleteButton);
    }
    bulk.append(clearButton);
    // #498 (D162): in Show removed images the bar holds only Return to library and Clear; every other action writes
    // records, which would show a removed file again without reading it.
    const normalActions = [...bulk.children].filter(node => node !== count && node !== clearButton);
    const returnButton = onReturn ? createButton('Return to library') : null;
    if (returnButton) {
        scope.listen(returnButton, 'click', () => {
            const target = matchingTarget();
            onReturn(checkedImages(), target, returnButton);
        });
    }
    let shownRemoved = false;
    function paintMode() {
        const removed = Boolean(returnButton && removedView?.());
        if (removed === shownRemoved) return;
        shownRemoved = removed;
        for (const node of [...normalActions, returnButton]) node?.remove();
        clearButton.before(...(removed ? [returnButton] : normalActions));
    }

    /** The count updates in place: the same bar and `strong`, its text written only when it changes. */
    function render() { keepingPlace(element, paintBar); }
    function paintBar() {
        if (!checked.size && !allMatching) { touchMode = false; bulk.remove(); return; }
        paintMode();
        const listing = getListing();
        const label = allMatching ? matchingLabel(listing.total) : selectionLabel(checked.size);
        if (count.textContent !== label) count.textContent = label;
        // "Select all N matching" (D135) follows the count while every loaded image is checked and more match.
        if (!allMatching && offersAllMatching(checked, loadedKeys(), listing)) {
            const offer = matchingOffer(listing.total);
            if (selectMatching.textContent !== offer) selectMatching.textContent = offer;
            if (count.nextElementSibling !== selectMatching) count.after(selectMatching);
        } else selectMatching.remove();
        if (allMatching) {
            exportMetadata.setAttribute('aria-disabled', 'true');
            exportMetadata.title = EXPORT_LOADED_ONLY;
        } else if (exportMetadata.title) {
            exportMetadata.removeAttribute('title');
            if (!exporting) exportMetadata.removeAttribute('aria-disabled');
        }
        if (bulk.parentNode !== element) element.append(bulk);
    }
    /** Every rendered checkbox shows the selection (cards mounted later read `isChecked`). */
    function paint() {
        for (const box of grid.querySelectorAll(CHECKBOX)) {
            const on = isChecked(box.closest(CARD).dataset.imageKey);
            if (box.checked !== on) box.checked = on;
        }
    }
    function update(next) {
        checked = next;
        paint();
        render();
    }
    /** Leaving "all N matching": the loaded images become the explicit selection the next change applies to. */
    function settleMatching() {
        if (!allMatching) return;
        allMatching = false;
        checked = Object.freeze(new Set(loadedKeys()));
    }
    function clear() {
        anchor = null; touchMode = false;
        if (!checked.size && !allMatching) return;
        allMatching = false;
        update(Object.freeze(new Set()));
    }
    /** Keep only the checked files `kept(key)` still lists (a refreshed result), so the count is what the actions apply to. */
    function retain(kept) {
        if (allMatching) { render(); return; }
        const next = [...checked].filter(kept);
        if (next.length === checked.size) return;
        checked = Object.freeze(new Set(next));
        render();
    }
    function loadedKeys() {
        return getItems().map(imageKey);
    }
    function isChecked(key) {
        return allMatching || checked.has(key);
    }
    function checkedImages() {
        return allMatching ? getItems() : getItems().filter(image => checked.has(imageKey(image)));
    }
    /** The actions' images for "all N matching": the listing filter and its total, which the server resolves. */
    function matchingTarget() {
        return allMatching ? { filter: getFilter(), count: getListing().total } : {};
    }
    /** The collection actions' view of "all N matching" (#331 ruling): the loaded images and the match count. */
    function collectionSelection() {
        return { allMatching, total: allMatching ? getListing().total : checked.size };
    }
    /** Remove from collection: one write at a time; the selection stays (a reload keeps what is still listed). */
    let removing = false;
    async function removeChecked(button) {
        if (removing) return;
        removing = true;
        const release = busy(button);
        try {
            await removeFromCollection({ scope, collections, collectionId: Number(collection), images: checkedImages(),
                ...collectionSelection(), onChanged: onCollectionsChanged, onSourceChanged });
        } finally {
            removing = false;
            release();
        }
    }
    /** Toggle one image on its own; it becomes the anchor. */
    function toggle(key, on = !isChecked(key)) {
        settleMatching();
        anchor = key;
        update(toggleSelection(checked, key, on));
    }
    /** The anchor's range to `key` takes `on`; without an anchor `key` is toggled on and becomes it. */
    function range(key, on = true) {
        settleMatching();
        const keys = rangeKeys(loadedKeys(), anchor, key);
        if (anchor === null || keys.length === 1) anchor = key;
        update(applyKeys(checked, keys, on));
    }
    /** Shift+arrow keys: the range from the anchor (`from`, the card focus left, when there is none) to `to`. */
    function extend(from, to) {
        if (anchor === null || !loadedKeys().includes(anchor)) anchor = from;
        range(to, true);
    }
    function selectAll() {
        if (allMatching) return;
        update(applyKeys(checked, loadedKeys(), true));
    }
    function focusAfterOffer() {
        // The pressed offer left the bar: focus moves to the count's first action instead of the page.
        (bulk.querySelector('button:not([aria-disabled="true"])') ?? focusFallback).focus({ preventScroll: true });
    }
    // Every card carries its image key; its only form control is the checkbox, so a toggle never opens or inspects.
    scope.listen(grid, 'change', event => {
        const box = event.target;
        if (!box.matches(CHECKBOX)) return;
        const key = box.closest(CARD).dataset.imageKey;
        if (shiftBox === key) range(key, box.checked);
        else toggle(key, box.checked);
        shiftBox = null;
    });
    // D135: modified clicks select instead of opening, inspecting or starring. Captured, so the page's own click
    // handler (images.mjs) never sees them; a plain click keeps its meaning.
    grid.addEventListener('click', event => {
        const card = event.target.closest(CARD);
        if (!card) return;
        const key = card.dataset.imageKey;
        if (longPress.consumeClick()) { swallow(event); return; }
        if (event.target.closest(CHECKBOX_LABEL)) {
            // The box toggles natively; its change event applies the range.
            if (event.shiftKey) shiftBox = key;
            return;
        }
        // A keyboard click (Enter, Space: `detail` 0) keeps its plain meaning in touch selection mode.
        const tapped = touchMode && lastPointer !== 'mouse' && event.detail !== 0;
        if (event.shiftKey) { swallow(event); range(key, true); }
        else if (event.ctrlKey || event.metaKey || tapped) { swallow(event); toggle(key); }
    }, { capture: true, signal: scope.signal });
    function swallow(event) {
        event.preventDefault();
        event.stopPropagation();
    }
    // A touch long-press selects the card and starts touch selection mode (D135).
    const longPress = createLongPress({ onLongPress: key => {
        touchMode = true;
        anchor = key;
        if (!isChecked(key)) toggle(key, true);
    } });
    scope.cleanup(() => longPress.cancel());
    scope.listen(grid, 'pointerdown', event => {
        lastPointer = event.pointerType || 'mouse';
        shiftBox = null;
        const card = event.target.closest(CARD);
        if (lastPointer === 'mouse' || !card || !event.isPrimary) { longPress.cancel(); return; }
        longPress.down(event.clientX, event.clientY, card.dataset.imageKey);
    });
    scope.listen(grid, 'pointermove', event => longPress.move(event.clientX, event.clientY));
    for (const type of ['pointerup', 'pointercancel', 'pointerleave']) scope.listen(grid, type, () => longPress.up());
    // A long-press's context menu (Android) would cover the selection it just started.
    scope.listen(grid, 'contextmenu', event => {
        if (lastPointer !== 'mouse' && (touchMode || longPress.pending())) event.preventDefault();
    });
    // Escape in the bar clears the selection; the bar hides, so focus moves to the page heading as after Clear.
    scope.listen(element, 'keydown', event => {
        if (event.key !== 'Escape' || (!checked.size && !allMatching)) return;
        event.preventDefault();
        clear();
        focusFallback.focus({ preventScroll: true });
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
     * Set category (app.js bulk-category → simpleInput, #332 D140): Apply sets each selected image's own category, linked
     * or not; "Use prompt category" removes it after a confirmation that states the count. Up to 500 images (DC2): an
     * explicit selection or "all N matching" (the listing filter, resolved by the server); more opens nothing.
     */
    function openCategory(button) {
        if (categorizing) return;
        const count = allMatching ? getListing().total : checked.size;
        const targets = allMatching ? {filter: getFilter()} : {items: bulkTargets(checkedImages())};
        if (count > MAX_CATEGORY_IMAGES) { showWorkspaceToast(TOO_MANY); return; }
        // Checked files the listing no longer holds (a refresh dropped them): nothing to send.
        if (!allMatching && !targets.items?.length) { showWorkspaceToast(NONE_FOUND); return; }
        openInputDialog({ scope, trigger: button, title: CATEGORY_DIALOG.title, label: CATEGORY_DIALOG.label,
            action: CATEGORY_DIALOG.action, maxLength: CATEGORY_MAX_LENGTH, suggestions: getCategories(),
            secondary: { label: USE_PROMPT_CATEGORY, onClick: () => confirmRemove(button, targets, count) },
            onApply: category => writeCategory(button, () => api.put(CATEGORY_URL, {...targets, category}),
                result => setOutcome(result, category)) });
    }
    /** "Use prompt category": nothing is sent until Remove; Cancel leaves every category as it was. */
    function confirmRemove(button, targets, count) {
        const text = el('p', removeQuestion(count));
        const actions = el('div', undefined, 'dialog-actions');
        const cancel = createButton('Cancel'), remove = createButton(REMOVE_DIALOG.action, 'danger');
        actions.append(cancel, remove);
        cancel.addEventListener('click', () => closeWorkspaceDialog());
        remove.addEventListener('click', () => {
            closeWorkspaceDialog();
            writeCategory(button, () => api.del(CATEGORY_URL, {body: targets}), clearOutcome);
        });
        openContentDialog({ scope, trigger: button, title: REMOVE_DIALOG.title, nodes: [text, actions] });
        cancel.focus();
    }
    /**
     * The write carries no page signal: once sent it finishes even if the page is left (`writing` then tells the page
     * not to save a visit that predates it). A failure keeps the selection, except a changed source, which reloads the
     * listing as a success does (`onCategorySet`).
     */
    async function writeCategory(button, send, outcome) {
        categorizing = true;
        const release = busy(button);
        let result = null, failure = null;
        try {
            result = await send();
        } catch (error) {
            failure = error;
        } finally {
            categorizing = false;
            release();
        }
        if (scope.signal.aborted) return;
        const focused = document.activeElement === button;
        if (failure !== null || !result) {
            showWorkspaceToast(failureMessage(failure));
            // A changed source reloads the listing (clearing the selection); any other failure keeps it.
            if (failure?.code !== 'source-changed') return;
            onCategorySet();
            if (focused) focusFallback.focus({ preventScroll: true });
            return;
        }
        showWorkspaceToast(outcome(result));
        onCategorySet();
        // The reload hides the bar with the focused action in it, as Clear does: focus moves to the page heading.
        if (focused) focusFallback.focus({ preventScroll: true });
    }

    render();
    return {
        element,
        isChecked,
        // A saved visit keeps "all N matching" as its loaded images; the offer then shows again.
        checked: () => (allMatching ? Object.freeze(new Set(loadedKeys())) : checked),
        hasSelection: () => allMatching || checked.size > 0,
        clear,
        retain,
        selectAll,
        extend,
        /** A loaded batch or a refreshed total: the count and the offer follow it. */
        listingChanged: () => { if (checked.size || allMatching) render(); },
        /** Whether a Set category write is still in flight. */
        writing: () => categorizing,
    };
}
