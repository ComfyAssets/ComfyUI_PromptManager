import { createScope, createButton } from '../shared/toolkit/core.js';
import { createImageStream, withItem, imageKey, countLine, captionDate, scopeLabel, activeRefinements, initialImagesView, visitDefaults, listingFilter } from './images-state.mjs';
import { createImageCard } from './image-card.mjs';
import { layoutImages, visibleImages, gridMetrics, stacksCardInfo } from './image-layout.mjs';
import { mountFileInspector } from './file-inspector.mjs';
import { createInspectionController } from './inspection-state.mjs';
import { mountSelectionBar } from './images-selection.mjs';
import { createPromptSender } from './prompt-sender.mjs';
import { promptRoles } from './prompt-blocks.mjs';
import { createWorkspaceIcon } from './icons.mjs';
import { createMediaViewer } from './media-viewer.mjs';
import { openDeleteImages } from './images-delete.mjs';
import { noun } from './plural.mjs';
import { pageHeading } from './heading.mjs';
import { mountToolbar } from './toolbar.mjs';
import { RESCAN_FAILED, RESCAN_STARTED, openImportDialog, openRefineDialog, rescanFinished } from './workspace-dialogs.mjs';
import { openMetadataScan } from './activity-scan.mjs';
import { showWorkspaceToast } from './toast.mjs';
import { imagesFilters, imagesViewState } from './saved-views.mjs';
import { VIEW_COLLECTION_DELETED, isCollectionGone } from './collections.mjs';
import { IMAGE_CATEGORIES_URL, mergeCategories } from './images-category.mjs';
import { displayPreferences } from './preferences.mjs';
import { onImageTagsSaved } from './image-tagging.mjs';
import { followImageTagSaves, openCardTagging } from './images-tagging.mjs';
import { favoriteBusy, paintStar, receiveFavorites, setFavoriteVisitTarget, toggleFavorite } from './images-favorites.mjs';
import { createSourceRecovery, recoveredSource } from './images-source-recovery.mjs';
export { onFavoriteChange } from './images-favorites.mjs';

/** The concept's panel without an image (app.js updateImages a456–458). */
function noSelection() {
    const inner = el('div', undefined, 'detail-inner');
    inner.append(el('h2', 'No image selected'), el('p', 'Clear the filters or choose a matching image.', 'small'));
    return inner;
}
function el(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
}

/**
 * Live regions re-announce replaced text even when it is the same, and a patched publish (a favorite) repeats the
 * same status: write text and attributes only when they change.
 */
function setText(element, text) {
    if (element.textContent !== text) element.textContent = text;
}
function setAttr(element, name, value) {
    if (element.getAttribute(name) !== value) element.setAttribute(name, value);
}

function selectOf(id, options, value) {
    const select = el('select');
    select.id = id;
    select.replaceChildren(...options.map(([optionValue, label]) => {
        const node = el('option', label);
        node.value = optionValue;
        return node;
    }));
    select.value = value;
    return select;
}

// The concept's sorts first, then the production ones it lacks (D14); values are the listing's sorts (B3). D153 (#447)
// drops the concept's "Highest rated".
const IMAGE_SORT_OPTIONS = Object.freeze([['newest', 'Recently added'], ['name', 'Name A–Z'],
    ['oldest', 'Oldest first'], ['name-desc', 'Name Z–A']]);
const MEDIA_OPTIONS = Object.freeze([['all', 'All media'], ['image', 'Images'], ['video', 'Videos']]);
const RETRY_RESCAN = 'Rescan from Import to retry.';
// The concept's empty collection (app.js updateImages).
const NO_MATCHES = 'No matching images. Try another search or category.';
const RETRY_REFRESH = 'Refresh image collection to retry.';
const CURSOR_STALE = 'The image collection changed. Refresh image collection to continue.';
/** One alert for whichever of the source discovery and the categories failed, so two failures announce once. */
function loadFailure(sourcesRetry, categoriesFailed) {
    if (sourcesRetry && categoriesFailed) return `Sources, folders and categories could not be loaded. ${sourcesRetry}`;
    if (sourcesRetry) return `Sources and folders could not be loaded. ${sourcesRetry}`;
    return categoriesFailed ? `Categories could not be loaded. ${RETRY_RESCAN}` : '';
}
// The concept card's first `.media-info` reservation and list row (image-layout.mjs); real rows are measured.
const INITIAL_METRICS = Object.freeze({columns:1, gap:13, footerHeight:54, listHeight:97});

let lastVisit = null;
// The star's answers patch the saved visit while no Images page is mounted (D7).
setFavoriteVisitTarget((key, favorite) => {
    if (lastVisit?.state) lastVisit = {...lastVisit, state: withItem(lastVisit.state, key, {favorite})};
});

// A tag save made while Images is left (Tags › Auto-tag images) may change what a filtered visit lists: it reloads (M6).
onImageTagsSaved(() => { if (lastVisit?.filters.tags.length || lastVisit?.filters.q) lastVisit = {...lastVisit, stale: true}; });
// The filter state a Tags & categories Browse hands to the next Images mount (#332 MC10).
let browseState = null;
/**
 * Open Images filtered by `category` only (every other filter at its default), as Browse opens the Library: Tags &
 * categories' Browse of a category only images use (#332 MC10, D143).
 */
export function browseImages({category} = {}) {
    if (typeof category !== 'string' || !category) throw new TypeError('browseImages needs a category');
    browseState = {category};
    window.location.hash = '#/images';
}
/** Forget the last Images visit (its listing, scroll and selection): the next visit reads everything again (S9). */
export function forgetImagesVisit() {
    lastVisit = null;
}
/** The image selected in Images when it was last left (the concept's `state.selectedImage`, for Tags › Auto-tag images). */
export function selectedImage() {
    const key = lastVisit?.selection;
    return key ? lastVisit.state?.items.find(item => imageKey(item) === key) ?? null : null;
}

/**
 * An embedded collection (the history page's shared gallery and its All images and Repeat run dialogs, D47) has no
 * caption: its page heading or dialog title already names it, and `focusTarget` takes a stranded focus.
 * `savedState` opens a saved view (#249 D16) instead of the last visit; `onSaveView(trigger, state)` is Save view.
 * `favorites` is the Favorites page (#249 D51): only favorites are listed, and it neither restores nor replaces the
 * Images last visit. `collection` (an id) is a collection page (#331 D137): only its members are listed, favorites is
 * off, and it neither restores nor replaces the last visit either. A listing that answers `collection-not-found` calls
 * `onCollectionMissing()` when given (the shell shows its deleted-collection page); otherwise (a saved view on a
 * deleted collection, D140) the page lists nothing and says so. `collections` (the shell's client store) adds the
 * selection bar's Add to collection, and Remove from collection while a collection is open (D138).
 */
export function mountImages(main, api, parentScope, {embedded = false, fetchPage, emptyText, focusTarget = null, savedState = null,
    favorites = false, collection: collectionId = null, onCollectionMissing = null, onSaveView = () => {}, activity = null,
    collections = null} = {}) {
    const scope = createScope();
    const dispose = () => scope.dispose();
    parentScope.signal.addEventListener('abort', dispose, {once:true});
    scope.cleanup(() => parentScope.signal.removeEventListener('abort', dispose));
    if (parentScope.signal.aborted) { scope.dispose(); return {dispose}; }
    const sender = createPromptSender({scope});
    let detailInspector;
    // System › Appearance: the display defaults this visit starts from (preferences.mjs); null without gallery-settings.js.
    const preferences = displayPreferences();
    const inspection = createInspectionController({api, autoLoad:preferences?.autoLoadMetadata !== false, cache:preferences?.cacheMetadata !== false});
    // The panel's listed file: a changed source is looked up once, the panel's Refresh stays the fallback (#430).
    let selectedImage = null;
    scope.cleanup(inspection.subscribe(next => {
        detailInspector?.setState(next);
        if (next.refreshRequired && next.code === 'source-changed' && selectedImage && imageKey(selectedImage) === selection) {
            recovery.recover(selectedImage);
        }
    }));
    scope.cleanup(() => inspection.dispose());
    scope.cleanup(() => detailInspector?.dispose());
    // A Tags & categories Browse (browseImages) is handed to this mount once: a fresh Images visit filtered by its
    // category, which becomes the Images return state like any plain visit (as Browse does for the Library).
    const imagesPage = !embedded && !favorites && collectionId === null;
    const handed = imagesPage && !savedState ? browseState : null;
    if (imagesPage) browseState = null;
    const saved = embedded || !savedState ? null : imagesFilters(savedState);
    // The open collection: the route's, else a saved view's (D140); '' on every other page.
    const openCollection = embedded ? '' : collectionId !== null ? String(collectionId) : saved?.collection || '';
    const prior = embedded || saved || favorites || openCollection || handed ? null : lastVisit;
    // A stale visit keeps its filters, layout and size, and lists again instead of restoring (M6).
    const visit = prior?.stale ? null : prior;
    // The layout and size defaults are the Images page's: an embedded gallery (a prompt history's, its dialogs) has no
    // toolbar to change them, so it keeps its fixed Grid/Comfortable. The metadata and card defaults apply everywhere.
    const start = initialImagesView({prior, saved, preferences:embedded ? null : preferences});
    // The defaults this visit started from: a return follows defaults saved since unless the toolbar changed a value (#451).
    const startDefaults = visitDefaults(embedded ? null : preferences);
    let density = start.density;
    let metrics = {...INITIAL_METRICS};
    let layout = start.layout, selection = visit?.selection || null, frame = 0, loadFrame = 0, restoreFrame = 0, observer;
    let state, geometry = { positions: [], columns: 1, height: 0, maxHeight: 0 }, galleryWidth = 0, optionsRequest, categoryRequest, recovering = false, rescanning = false;
    // Discovered sources (each with its own folders) and every folder, for "Refine this view" and the scope chip.
    let roots = [], folders = [];
    // The toolbar's current category list (loadCategories), also the Set category suggestions.
    let categoryNames = [];
    // The open "Refine this view", so a discovery that settles after it opened still lists its sources and folders.
    let refine = null;
    const rendered = new Map();
    const itemIndexes = new Map();
    const filters = { root: 'all', q: '', category: '', tags: [], subfolder: '', media: 'all', sort: 'newest', ...prior?.filters, ...saved,
        ...(handed ? {category: handed.category} : {}),
        // A collection and favorites are never both the filter (DC10).
        favorites: !openCollection && (favorites || saved?.favorites === true), collection: openCollection };
    filters.tags = [...filters.tags];
    if (!IMAGE_SORT_OPTIONS.some(([value]) => value === filters.sort)) filters.sort = 'newest';
    // A saved Source or Folder is only listed once discovery confirms it still exists; until then nothing is fetched.
    let savedScopePending = Boolean(saved) && (filters.root !== 'all' || filters.subfolder !== '');
    // The first discovery settled (found or failed): only then may a pending saved scope be kept or dropped (#249 M3).
    let discoverySettled = false;
    scope.cleanup(() => {
        // A Set category write still in flight may change what the saved collection shows: the return reloads instead.
        // A favorites-only collection or a saved view is not the plain Images collection, so it never becomes the
        // Images return state (#249 D51, and M2 for saved views).
        if (!embedded && !filters.favorites && !filters.collection && !saved) lastVisit = state && !bar.writing() ? { filters: {...filters, tags: [...filters.tags]}, state, layout, density, defaults: startDefaults, selection, checked: bar.checked(), scrollY: window.scrollY } : null;
        cancelAnimationFrame(restoreFrame);
    });
    main.classList.add('images-main');
    scope.cleanup(() => main.classList.remove('images-main'));
    // The concept's Images heading (app.js renderImages): description, then Import before the shell's New prompt.
    const heading = embedded ? null : pageHeading(main);
    let importButton = null, importDialog = null;
    if (heading) {
        heading.description.textContent = 'Your generations, their prompts, and the next idea.';
        importButton = createButton('');
        importButton.append(createWorkspaceIcon('upload'), el('span', 'Import'));
        heading.actions.prepend(importButton);
        scope.cleanup(() => importButton.remove());
        scope.listen(importButton, 'click', () => {
            importDialog = openImportDialog({scope, trigger:importButton, rescanBusy:rescanning,
                onInspect:() => { window.location.hash = '#/inspect'; }, onRescan:rescan,
                // D55: Scan for metadata reviews the session scan job over every discovered source.
                onMetadataScan:activity ? trigger => openMetadataScan({scope, store:activity, sources:roots.length, trigger}) : null});
        });
    }
    // The shared concept toolbar: search (filename, linked prompt text, title and tags), category and sort (D12–D14).
    const toolbar = embedded ? null : mountToolbar(main, {scope, categories:filters.category ? [filters.category] : [],
        sorts:IMAGE_SORT_OPTIONS, value:{q:filters.q, category:filters.category, sort:filters.sort},
        activeFilters:activeRefinements(filters),
        onChange:value => {
            if (recovering) return;
            const next = {q:value.q.trim(), category:value.category, sort:value.sort};
            // A debounced search report can land after a select change already applied the same values; reloading
            // then would refetch the first page and drop the reader's scroll position.
            if (Object.entries(next).every(([key, item]) => filters[key] === item)) return;
            Object.assign(filters, next);
            reset();
        }, onFilters:openFilters, onSaveView:trigger => {
            const value = toolbar.value();
            onSaveView(trigger, imagesViewState({...filters, q:value.q.trim(), category:value.category, sort:value.sort}));
        }});
    const toolbarControls = toolbar ? [...toolbar.element.querySelectorAll('input, select, button')] : [];
    const optionsStatus = el('p', '', 'options-status muted');
    optionsStatus.hidden = true; optionsStatus.setAttribute('role', 'alert');
    // The failed loads' retry hint (null once sources load) and whether the categories failed.
    let sourcesRetry = null, categoriesFailed = false;
    if (!embedded) main.append(optionsStatus);
    // The concept's filter row: layouts, thumbnail size, the scope chip and Group by positive prompt (D14, D15).
    const filterRow = el('div', undefined, 'filter-row');
    const layouts = el('div', undefined, 'segmented');
    layouts.setAttribute('role', 'group'); layouts.setAttribute('aria-label', 'Image layout');
    for (const mode of ['grid', 'list', 'masonry']) {
        const button = createButton(mode[0].toUpperCase() + mode.slice(1));
        button.prepend(createWorkspaceIcon(mode));
        button.setAttribute('aria-pressed', String(mode === layout));
        scope.listen(button, 'click', () => {
            layout = mode;
            for (const child of layouts.children) child.setAttribute('aria-pressed', String(child === button));
            updateLayout();
        });
        layouts.append(button);
    }
    const sizeLabel = el('label', 'Thumbnail size', 'small');
    sizeLabel.htmlFor = 'image-size';
    const thumbnails = selectOf('image-size', [['comfortable', 'Comfortable'], ['large', 'Large'], ['compact', 'Compact']], density);
    scope.listen(thumbnails, 'change', () => { density = thumbnails.value; updateLayout(); });
    filterRow.append(layouts, sizeLabel, thumbnails);
    const scopeText = document.createTextNode('');
    if (!embedded) {
        const chip = el('span', undefined, 'chip');
        chip.append(createWorkspaceIcon('folder'), scopeText);
        const group = createButton('Group by positive prompt →', 'chip');
        scope.listen(group, 'click', () => { window.location.hash = '#/library'; });
        filterRow.append(chip, group);
    }
    main.append(filterRow);
    const body = el('div', undefined, 'gallery-layout image-workspace');
    const collection = el('section', undefined, 'gallery-body');
    collection.setAttribute('aria-label', 'Images');
    // The concept's caption replaces the old "All images" heading.
    const caption = el('div', undefined, 'section-caption');
    const count = el('span', 'Loading images…');
    count.id = 'result-count';
    const captionDay = el('span');
    captionDay.hidden = true;
    caption.append(count, captionDay);
    const focusFallback = focusTarget || heading?.h1 || main;
    const grid = el('div', undefined, 'image-grid');
    grid.setAttribute('role', 'list'); grid.setAttribute('aria-label', 'Image collection');
    const continuation = el('div', undefined, 'infinite-continuation');
    // The concept's ids (app.js renderImages); the Library-embedded gallery has its own page around it.
    if (!embedded) { grid.id = 'image-grid'; continuation.id = 'gallery-continuation'; }
    const message = el('p', '', 'small');
    const more = createButton('Load more images');
    const retry = createButton('Retry');
    continuation.append(message, more, retry);
    // The concept's empty collection (app.js updateImages), beside the grid because the grid only holds positioned cards.
    // An embedded gallery shows one only when its page names the concept's copy (the history gallery, app.js a620).
    const showsEmpty = !embedded || typeof emptyText === 'string';
    const empty = el('div', embedded ? emptyText : NO_MATCHES, 'empty');
    empty.hidden = true;
    // D47: the history page's shared gallery and dialogs are named by their own heading or title, so they have no caption.
    if (!embedded) collection.append(caption);
    collection.append(grid);
    if (showsEmpty) collection.append(empty);
    collection.append(continuation);
    // After a Load more or Retry press, the index of the first image that batch appends (concept: focus it).
    let focusAfterLoad = null;
    // The concept's side panel (app.js a426, D20); the Library-embedded gallery has its own page around it.
    const details = el('aside', undefined, 'detail-panel');
    if (!embedded) details.id = 'image-inspector';
    details.setAttribute('aria-label', 'Selected image details');
    details.append(noSelection());
    body.append(collection, details);
    // The checked cards and the concept's selection bar (#249 D8), between the filter row and the gallery layout. The
    // selection is kept across appended batches and cleared with the collection; the Library-embedded gallery has none.
    const bar = embedded ? null : mountSelectionBar({scope, api, grid, getItems:() => state?.items ?? [], focusFallback,
        checked:visit?.checked, onCategorySet:() => { loadCategories(); reset(); }, activity, onTagged:keys => refreshTagged(keys),
        onDeleted:deleted,
        // The Set category field suggests the toolbar's categories (#332 MC13).
        getCategories:() => categoryNames,
        // #392 "Select all N matching": the listing's total and its filter, as the server lists them.
        getListing:() => ({total:state?.total ?? 0, hasMore:Boolean(state?.hasMore), positiveCount:state?.positiveCount ?? null}),
        // A Source filter carries its root's scope, so a renumbered source is refused (source-changed), not acted on.
        getFilter:() => {
            const filter = listingFilter(filters), scope = state?.items[0]?.root_scope;
            return filter.root !== undefined && typeof scope === 'string' ? {...filter, root_scope:scope} : filter;
        },
        // #331 D138: Add to collection, and Remove from collection on an open collection, which then lists it again.
        collections, collection:filters.collection || null, onCollectionsChanged:collectionChanged,
        onSourceChanged:refreshInspectionCollection});
    if (bar) main.append(bar.element);
    main.append(body);
    // #331 D139: a change the inspector or the viewer confirmed that touches the open collection lists it again; the
    // viewer reports only once it closes.
    const inspectorCollections = embedded ? null : collections;
    const collectionsChangedBy = ({added, removed}) => {
        if (filters.collection && [...added, ...removed].includes(Number(filters.collection))) collectionChanged();
    };
    const mediaViewer = createMediaViewer({api,container:main,parentScope:scope,inspectionController:inspection,sender,
        collections:inspectorCollections,onCollectionsChanged:collectionsChangedBy,
        onDelete:embedded ? null : (item,button) => deleteOne(item,button,() => mediaViewer.close()),
        onSelect:image => {if (selection !== imageKey(image)) inspect(image);else inspection.select(image);},
        onRefresh:refreshInspectionCollection,onClose:scheduleLoad,onMediaError:image => recovery.recover(image),onRestoreFocus:image => {
            const index = state?.items.findIndex(entry => imageKey(entry) === imageKey(image));
            if (index >= 0) focusImage(index);else focusFallback.focus({preventScroll:true});
        }});
    scope.cleanup(() => mediaViewer.dispose());

    function releaseVideo(video) {
        video.pause(); video.removeAttribute('src'); video.load();
    }
    function inspect(image) {
        detailInspector?.dispose();
        selection = imageKey(image);
        selectedImage = image;
        for (const video of details.querySelectorAll('video')) releaseVideo(video);
        detailInspector = mountFileInspector(details, {sender, parentScope:scope, variant:'panel',
            onTagFilter:embedded ? undefined : filterByTag, onRetry:() => inspection.retry(), onLoad:() => inspection.load(), onRefresh:refreshInspectionCollection,
            collections:inspectorCollections, onCollectionsChanged:collectionsChangedBy,
            onDelete:embedded ? null : (target, button) => deleteOne(target, button)});
        detailInspector.setState(inspection.state);
        inspection.select(image);
        for (const [key, card] of rendered) markSelected(card, key === selection);
    }
    /** The concept's `.media-card.selected` (D19); as in the concept, Info carries no pressed state. */
    function markSelected(card, selected) {
        card.classList.toggle('selected', selected);
    }
    function previewImage(image) {
        if (!image) return;
        mediaViewer.open({items:state.items,selectedKey:imageKey(image),trigger:document.activeElement});
    }
    /** The concept media card (image-card.mjs): the image opens the viewer, Info inspects (D7–D11, D19). */
    function cardFor(image) {
        const key = imageKey(image);
        const card = createImageCard(image, {checked:bar ? bar.isChecked(key) : null, favoriteBusy:favoriteBusy(key),
            // D97: "Show image details on cards" off hides the title and file line; Info and Tag stay.
            showInfo:preferences?.showImageInfo !== false});
        markSelected(card, key === selection);
        return card;
    }
    function setFavorite(key, favorite) {
        if (scope.signal.aborted) return;
        stream.updateItem(key, {favorite});
        const button = rendered.get(key)?.querySelector('.star-button');
        if (button) paintStar(button, favorite, favoriteBusy(key));
    }
    // The Images page and every Library-embedded gallery receive favorite answers (#249 D7, PR E).
    receiveFavorites({scope, embedded, setFavorite});
    scope.listen(grid, 'click', event => {
        const card = event.target.closest('article[data-image-key]');
        if (!card) return;
        const image = state.items.find(item => imageKey(item) === card.dataset.imageKey);
        if (!image) return;
        const action = event.target.closest('.image-card-actions button');
        if (event.target.closest('.star-button')) toggleFavorite(api, image);
        else if (event.target.closest('.image-open')) previewImage(image);
        else if (action && !openCardTagging({api, scope, image, action, embedded, onSourceChanged:recovery.recover,
            onRefresh:refreshInspectionCollection})) inspect(image);
    });

    function renderVisible() {
        if (!state || scope.signal.aborted) return;
        const bounds = grid.getBoundingClientRect();
        const scale = bounds.width / Number.parseFloat(getComputedStyle(grid).width) || 1;
        const top = Math.max(0, -bounds.top / scale);
        const viewportHeight = window.innerHeight / scale;
        const active = document.activeElement?.closest('article[data-image-key]')?.dataset.imageKey;
        const focused = active ? itemIndexes.get(active) ?? -1 : -1;
        const indexes = state.items.length <= 120 ? state.items.map((_, index) => index) :
            visibleImages(geometry.positions, top, viewportHeight, focused, geometry.maxHeight);
        const keep = new Set(indexes.map(index => imageKey(state.items[index])));
        for (const [key, card] of rendered) if (!keep.has(key)) {
            for (const video of card.querySelectorAll('video')) releaseVideo(video);
            card.remove(); rendered.delete(key);
        }
        for (const index of indexes) {
            const image = state.items[index], key = imageKey(image);
            let card = rendered.get(key);
            if (!card) { card = cardFor(image); rendered.set(key, card); }
            positionCard(card, index);
        }
        // Keep keyboard order aligned with the collection while preserving existing nodes.
        let previous = null;
        for (const index of indexes) {
            const card = rendered.get(imageKey(state.items[index]));
            if (previous ? previous.nextElementSibling !== card : grid.firstElementChild !== card) grid.insertBefore(card, previous ? previous.nextElementSibling : grid.firstChild);
            previous = card;
        }
        // Natural footer measurements and absolute reservations share unzoomed CSS units. The fractional height, as the
        // concept's own flow lays it out: `offsetHeight` rounds a wrapped 67.5px row up and adds half a pixel per row.
        const footerHeight = Math.max(metrics.footerHeight, ...[...rendered.values()].map(card => card.querySelector('.media-info').getBoundingClientRect().height / scale));
        // A small epsilon: under zoom `rect / scale` carries float noise that must not relayout and scroll for nothing.
        if (footerHeight > metrics.footerHeight + 0.01) {
            const anchor = indexes.find(index => geometry.positions[index].y + geometry.positions[index].height >= top && geometry.positions[index].y <= top + viewportHeight);
            const anchorTop = anchor === undefined ? 0 : geometry.positions[anchor].y;
            metrics = {...metrics, footerHeight, listHeight: Math.max(metrics.listHeight, footerHeight + 2)};
            updateGeometry();
            for (const index of indexes) positionCard(rendered.get(imageKey(state.items[index])), index);
            // A later mounted footer must not move the current visible card.
            const correction = anchor === undefined ? 0 : geometry.positions[anchor].y - anchorTop;
            if (top > 0 && correction) window.scrollBy(0, correction * scale);
            scheduleVisible();
        }
    }
    function positionCard(card, index) {
        const image = state.items[index], position = geometry.positions[index];
        card.dataset.index = index;
        markSelected(card, imageKey(image) === selection);
        card.setAttribute('aria-posinset', String(index + 1)); card.setAttribute('aria-setsize', String(state.total));
        Object.assign(card.style, { left: `${position.x}px`, top: `${position.y}px`, width: `${position.width}px`, height: `${position.height}px` });
    }
    function focusImage(index, actions = false, {scroll = true} = {}) {
        if (index < 0 || index >= state.items.length) return;
        const image = state.items[index], key = imageKey(image);
        let card = rendered.get(key);
        if (!card) {
            card = cardFor(image); rendered.set(key, card); positionCard(card, index);
            grid.insertBefore(card, [...grid.children].find(child => Number(child.dataset.index) > index) || null);
        }
        card.querySelector(actions ? '.image-card-actions button' : '.image-open').focus({ preventScroll: true });
        if (scroll) card.scrollIntoView({ block: 'nearest' });
        cancelAnimationFrame(frame); frame = 0;
        renderVisible();
    }
    scope.listen(grid, 'keydown', event => {
        const card = event.target.closest('article[data-image-key]');
        if (!card) return;
        const index = Number(card.dataset.index);
        const columns = geometry.columns;
        const target = { Home: 0, End: state.items.length - 1, ArrowRight: index + 1,
            ArrowLeft: index - 1, ArrowDown: index + columns, ArrowUp: index - columns }[event.key];
        if (target !== undefined) {
            event.preventDefault();
            const next = Math.max(0, Math.min(state.items.length - 1, target));
            focusImage(next);
            // D135: Shift extends the selection from the anchor (this card when there is none) to the new card.
            if (event.shiftKey && bar) bar.extend(card.dataset.imageKey, imageKey(state.items[next]));
        } else if (bar && (event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === 'a') {
            event.preventDefault();
            bar.selectAll();
        } else if (bar && event.key === 'Escape' && bar.hasSelection()) {
            event.preventDefault();
            bar.clear();
        } else if (event.key === 'Tab') {
            const backward = event.shiftKey && event.target.matches('.image-open');
            const forward = !event.shiftKey && event.target.closest('.image-card-actions');
            const adjacent = backward ? index - 1 : index + 1;
            if ((backward || forward) && state.items[adjacent] && !rendered.has(imageKey(state.items[adjacent]))) {
                event.preventDefault(); focusImage(adjacent, backward);
            }
        }
    });
    function scheduleVisible() { if (!frame) frame = requestAnimationFrame(() => { frame = 0; renderVisible(); }); }
    function updateGeometry() {
        geometry = layoutImages(state.items, galleryWidth, layout, metrics);
        // Below the narrowest bounded card (extreme zoom) the info row stacks (the production overlay, #249 D31).
        grid.classList.toggle('narrow-cards', stacksCardInfo(galleryWidth, layout));
        grid.style.height = `${geometry.height}px`;
        grid.style.setProperty('--image-footer-height', `${metrics.footerHeight}px`);
    }
    function updateLayout() {
        if (!state || scope.signal.aborted) return;
        cancelAnimationFrame(frame); frame = 0;
        // The concept's grid classes (app.js updateImages); the concept stylesheet then owns the columns and gap (D3).
        grid.className = ['image-grid', density === 'comfortable' ? '' : density,
            layout === 'list' ? 'list-grid' : layout === 'masonry' ? 'masonry-grid' : ''].filter(Boolean).join(' ');
        const width = Number.parseFloat(getComputedStyle(grid).width);
        const retained = state.items.length && width === galleryWidth && grid.dataset.layout === layout && grid.dataset.density === density ? metrics : null;
        galleryWidth = width;
        metrics = {...INITIAL_METRICS, ...gridMetrics(grid)};
        if (retained) {
            metrics.footerHeight = retained.footerHeight;
            metrics.listHeight = retained.listHeight;
        }
        grid.dataset.layout = layout;
        grid.dataset.density = density;
        updateGeometry();
        renderVisible();
    }
    let resizeFrame = 0, observedWidth;
    const resize = new ResizeObserver(entries => {
        const width = entries[0].contentRect.width;
        if (width === observedWidth) return;
        observedWidth = width;
        if (!resizeFrame) resizeFrame = requestAnimationFrame(() => {
            resizeFrame = 0;
            updateLayout();
        });
    });
    resize.observe(grid);
    scope.listen(window, 'scroll', scheduleVisible);
    scope.listen(window, 'resize', () => {
        if (!resizeFrame) resizeFrame = requestAnimationFrame(() => { resizeFrame = 0; updateLayout(); });
    });
    scope.cleanup(() => { resize.disconnect(); observer?.disconnect(); cancelAnimationFrame(resizeFrame); cancelAnimationFrame(frame); cancelAnimationFrame(loadFrame); });
    /**
     * The listing refused the open collection: it was deleted. A saved view says so in place (MC6, D140); a collection
     * page reports it (`onCollectionMissing`) once per failed read and meanwhile shows the listing's error and Retry,
     * so a page whose shell cannot confirm the deletion is never left blank.
     */
    const collectionGone = next => Boolean(filters.collection) && !onCollectionMissing && isCollectionGone(next.error);
    let reportedMissing = null;
    function continuationText(next) {
        if (recovering) return 'Images paused until sources and folders are available.';
        if (collectionGone(next)) return '';
        if (next.error?.code === 'cursor-stale') return CURSOR_STALE;
        if (next.error) return 'Images could not be loaded. Retry or rescan the collection.';
        if (next.loading && !next.loaded) return 'Loading images…';
        if (next.hasMore) return `${next.loaded.toLocaleString('en-US')} of ${next.total.toLocaleString('en-US')} ${noun(next.total, 'image')} · more load as you scroll`;
        if (embedded) return next.loaded ? 'You’re all caught up.' : 'No linked media available for this selection.';
        // The concept's end of the collection (app.js updateImageContinuation).
        return `${next.loaded ? 'You’re all caught up' : 'No matching images'} · ${next.total.toLocaleString('en-US')} ${noun(next.total, 'image')}`;
    }
    function renderState(next, {patch, replaced = false} = {}) {
        const previous = state;
        // A patched item (a favorite) keeps every card's size and place; setFavorite repaints its card.
        const changed = !patch && state?.items !== next.items;
        if (changed) {
            // A refreshed collection (`replaced`) is indexed again from its start; a loaded page only appends.
            if (!next.items.length || replaced) itemIndexes.clear();
            for (let index = replaced ? 0 : state?.items.length || 0; index < next.items.length; index++) itemIndexes.set(imageKey(next.items[index]), index);
        }
        // The selected file left the refreshed result: the panel follows the collection, as after a reload; the
        // selection bar keeps only the checked files still listed (I7).
        if (replaced && selection !== null && !itemIndexes.has(selection)) dropSelection();
        if (replaced) bar?.retain(key => itemIndexes.has(key));
        state = next;
        bar?.listingChanged();
        if (!embedded) {
            setText(count, countLine(next));
            setText(captionDay, captionDate(next.items, filters.sort));
            if (captionDay.hidden !== !captionDay.textContent) captionDay.hidden = !captionDay.textContent;
        }
        if (changed) updateLayout();
        // The concept's Load more toast (app.js loadMoreImages) for every batch appended to a loaded collection.
        if (!embedded && changed && !replaced && previous?.loaded && next.loaded > previous.loaded) {
            const added = next.loaded - previous.loaded;
            showWorkspaceToast(`${added.toLocaleString('en-US')} more ${noun(added, 'image')} loaded. ${next.loaded.toLocaleString('en-US')} of ${next.total.toLocaleString('en-US')}.`);
        }
        const gone = collectionGone(next);
        setText(message, continuationText(next));
        setAttr(message, 'role', next.error && !gone ? 'alert' : 'status');
        retry.hidden = !next.error || gone;
        // A cursor the server can no longer verify is never resent: Retry becomes the collection refresh (#430).
        setText(retry, next.error?.code === 'cursor-stale' ? 'Refresh image collection' : 'Retry');
        more.hidden = !next.hasMore || !!next.error;
        more.setAttribute('aria-disabled', String(next.loading || recovering));
        if (onCollectionMissing && filters.collection && isCollectionGone(next.error) && reportedMissing !== next.error) {
            reportedMissing = next.error;
            queueMicrotask(() => { if (!scope.signal.aborted) onCollectionMissing(); });
        }
        if (showsEmpty) {
            setText(empty, gone ? VIEW_COLLECTION_DELETED : embedded ? emptyText : NO_MATCHES);
            empty.hidden = !gone && (recovering || next.loading || !!next.error || next.hasMore || next.loaded > 0);
        }
        if (focusAfterLoad !== null && !next.loading) settleLoadFocus(next);
        if (!selection && next.items.length) inspect(next.items[0]);
        scheduleLoad();
    }
    const stream = createImageStream({ api, fetchPage, onChange: renderState, restore: visit ? {filters, state:visit.state} : undefined });
    const refreshTagged = followImageTagSaves({scope, stream, inspection, selection:() => selection, filtered:() => !embedded && !recovering && Boolean(filters.tags.length || filters.q),
        onRefreshing:busy => more.setAttribute('aria-disabled', String(busy || Boolean(state?.loading) || recovering))});
    scope.cleanup(() => {
        stream.dispose();
        for (const video of body.querySelectorAll('video')) releaseVideo(video);
    });
    function pressLoad() {
        // A refresh re-reads the loaded range: Load more waits for it (and focus stays where it is, M3).
        if (recovering || state?.loading || stream.refreshing) return;
        focusAfterLoad = state?.loaded ?? 0;
        stream.loadMore();
    }
    /** The concept focuses the first appended image; focus never stays on a control that just hid. */
    function settleLoadFocus(next) {
        const first = focusAfterLoad;
        focusAfterLoad = null;
        const active = document.activeElement;
        // Only while focus is still on the pressed control: a viewer or another control the user moved to keeps it.
        const stranded = !active || active === document.body || active === more || active === retry;
        if (!stranded) return;
        if (next.error) { retry.focus(); return; }
        // Without scrolling: the visible cards keep their place (bounded-DOM anchor); the next arrow key reveals it.
        if (next.loaded > first) focusImage(first, false, {scroll:false});
        else if (more.hidden) {
            if (next.loaded) focusImage(next.loaded - 1, false, {scroll:false});
            else focusFallback.focus({preventScroll:true});
        }
    }
    scope.listen(more, 'click', pressLoad);
    scope.listen(retry, 'click', () => (state?.error?.code === 'cursor-stale' ? refreshInspectionCollection() : pressLoad()));
    function maybeLoad() {
        if (!observer || recovering || scope.signal.aborted || !state?.hasMore || state.loading || state.error || main.querySelector('#viewer-dialog[open]')) return;
        const bounds = continuation.getBoundingClientRect();
        if (bounds.top <= window.innerHeight + 250 && bounds.bottom >= 0) stream.loadMore();
    }
    function scheduleLoad() {
        if (!scope.signal.aborted && !loadFrame) loadFrame = requestAnimationFrame(() => { loadFrame = 0; maybeLoad(); });
    }
    if (typeof IntersectionObserver === 'function') {
        observer = new IntersectionObserver(entries => {
            if (entries.some(entry => entry.isIntersecting)) maybeLoad();
        }, { rootMargin: '250px' });
        observer.observe(continuation);
    }
    function updateScope() {
        scopeText.textContent = ` ${scopeLabel(filters, roots)}`;
        toolbar?.setActiveFilters(activeRefinements(filters));
    }
    /** No selected file; `keepPanel` (recovering sources) leaves the panel's details shown. */
    function dropSelection({keepPanel = false} = {}) {
        for (const video of details.querySelectorAll('video')) releaseVideo(video);
        if (!keepPanel) {
            inspection.clear();
            detailInspector?.dispose(); detailInspector = null;
            details.replaceChildren(noSelection());
        }
        selection = null;
        selectedImage = null;
    }
    // Bumped by every reset: a source recovery that began before it leaves the page alone (#430).
    let generation = 0;
    const recovery = createSourceRecovery({api, signal:scope.signal, generation:() => generation, apply:recoveredSource({
        selected:image => selection === imageKey(image), inspect, dropSelection, stream, filters, updateScope,
        closeViewer:() => { mediaViewer.stopSlideshow(); main.querySelector('#viewer-dialog[open]')?.close(); },
        clearChecks:() => bar?.clear(), listsNow:() => !recovering,
        current:started => !scope.signal.aborted && started === generation,
        rediscover:() => loadOptions().then(found => { if (found) renderLoadStatus(); })})});
    /** #480: a finished delete job reloads the listing (the deleted files leave it, and the selection clears). */
    function deleted() {
        loadCategories();
        reset();
    }
    /** #480: Delete… for one file, from the inspector or the viewer (`after` closes the viewer first). */
    function deleteOne(target, trigger, after = null) {
        openDeleteImages({api, scope, store:activity, images:[target], trigger, onDeleted:() => { after?.(); deleted(); }});
    }
    function reset() {
        generation += 1;
        mediaViewer.stopSlideshow();
        main.querySelector('#viewer-dialog[open]')?.close();
        dropSelection({keepPanel:recovering});
        focusAfterLoad = null;
        // The ruled selection lifetime: a replaced collection may no longer hold the checked files.
        bar?.clear();
        // A toolbar change while discovery is still pending keeps the saved Source and Folder; the settled discovery
        // then lists the view with every current value (#249 M3).
        if (savedScopePending && !discoverySettled) { updateScope(); return; }
        settleSavedScope();
        updateScope();
        stream.reset(filters, {load:!recovering});
    }
    /** A saved view's Source and Folder apply only while the discovered sources still list them (#249 D16). */
    function settleSavedScope() {
        if (!savedScopePending) return;
        savedScopePending = false;
        if (filters.root !== 'all' && !roots.some(root => String(root.index) === filters.root)) filters.root = 'all';
        if (!folderOptions(filters.root).some(([path]) => path === filters.subfolder)) filters.subfolder = '';
    }
    /** A panel tag chip (D21): the Images Tags filter becomes exactly that tag, as "Refine this view" would set it. */
    function filterByTag(tag) {
        if (recovering || !tag.trim() || tag.includes(',')) return;
        if (filters.tags.length === 1 && filters.tags[0] === tag) return;
        filters.tags = [tag];
        reset();
    }
    /** "Refine this view": Source, Folder, Media and Tags, the filters the old field row held (D14). */
    function openFilters(trigger) {
        if (recovering) return;
        refine = openRefineDialog({scope, trigger, fields:[
            {key:'source', options:sourceOptions()},
            {key:'folder', options:folderOptions('all'), optionsFor:folderOptions},
            {key:'media', options:MEDIA_OPTIONS},
            {key:'tags'},
        ], values:{source:filters.root, folder:filters.subfolder, media:filters.media, tags:filters.tags.join(', ')},
        onApply:applyRefinement, onClear:cleared => {
            toolbar.setValue({category:cleared.category});
            applyRefinement(cleared);
        }});
    }
    function sourceOptions() {
        return [['all', 'All sources'], ...roots.map(root => [String(root.index), root.label])];
    }
    /** The chosen Source's folders, as the old field row reloaded them; every folder under All sources. */
    function folderOptions(source) {
        const root = source === 'all' ? null : roots.find(item => String(item.index) === source);
        return [['', 'All output folders'], ...(root?.subfolders ?? folders).map(path => [path, path])];
    }
    /** Apply the dialog's values (Clear adds `category`); unchanged filters keep the loaded page, as the toolbar does. */
    function applyRefinement({source, folder, media, tags, category = filters.category}) {
        if (recovering) return;
        const next = {root:source, subfolder:folder, media, category,
            tags:[...new Set(tags.split(',').map(tag => tag.trim()).filter(Boolean))]};
        const same = Object.entries(next).every(([key, value]) => key === 'tags'
            ? value.length === filters.tags.length && value.every((tag, index) => tag === filters.tags[index])
            : filters[key] === value);
        if (same) return;
        Object.assign(filters, next);
        reset();
    }
    function setControlsDisabled(disabled) {
        for (const control of [...toolbarControls, more, retry]) control.disabled = disabled;
        if (importButton) importButton.disabled = disabled;
    }
    /**
     * Import → "Scan an output folder" (D15): rescan the output roots, then reload the options and the listing. A toast
     * says it started and how it ended; meanwhile a reopened Import dialog shows Rescan as running.
     */
    async function rescan() {
        if (rescanning || recovering || scope.signal.aborted) return;
        rescanning = true;
        optionsRequest?.abort(); optionsRequest = null;
        showWorkspaceToast(RESCAN_STARTED);
        try {
            const result = await api.post('/prompt_manager/gallery/rescan', {}, {signal:scope.signal});
            if (scope.signal.aborted) return;
            const [found] = await Promise.all([loadOptions(), loadCategories()]);
            if (scope.signal.aborted) return;
            renderLoadStatus();
            if (found) reset();
            if (!scope.signal.aborted) announceRescan(rescanFinished(result));
        } catch {
            // A failed rescan leaves the collection and any discovery alert as they were.
            if (!scope.signal.aborted) announceRescan(RESCAN_FAILED);
        } finally {
            rescanning = false;
            importDialog?.setRescanBusy(false);
        }
    }
    /**
     * D42: the concept's toast stays fixed at the bottom, so a discovery alert the rescan leaves is scrolled clear of
     * the toast's band (its `scroll-margin-block-end`) only when it is in that band; elsewhere the page stays put.
     */
    function announceRescan(message) {
        showWorkspaceToast(message);
        if (optionsStatus.hidden) return;
        const {top, bottom} = optionsStatus.getBoundingClientRect();
        const band = parseFloat(getComputedStyle(optionsStatus).scrollMarginBlockEnd) || 0;
        if (bottom > innerHeight - band && top < innerHeight) optionsStatus.scrollIntoView({block:'nearest', inline:'nearest'});
    }
    /**
     * A confirmed collection change that may change this listing (a remove from the open collection): the loaded range
     * is read again in place, keeping the scroll and the checked files still listed. A failed re-read lists the page
     * again from its start, whose failure shows the usual Retry (or the deleted-collection page).
     */
    async function collectionChanged() {
        if (scope.signal.aborted || !filters.collection || recovering) return;
        try {
            await stream.refresh();
        } catch {
            if (!scope.signal.aborted) reset();
        }
    }
    async function refreshInspectionCollection() {
        if (scope.signal.aborted) return;
        recovery.reset();
        // Numeric roots may now name another source; retire the old stream first.
        recovering = true;
        filters.root = 'all';filters.subfolder = '';
        if (toolbar) filters.q = toolbar.value().q.trim();
        roots = [];folders = [];
        setControlsDisabled(true);
        sourcesRetry = null;
        renderLoadStatus();
        reset();
        const discovery = loadOptions(), request = optionsRequest;
        const found = await discovery;
        if (scope.signal.aborted || request !== optionsRequest) return;
        renderLoadStatus();
        if (!found) return;
        recovering = false;
        setControlsDisabled(false);
        reset();
    }
    function renderLoadStatus() {
        const text = loadFailure(sourcesRetry, categoriesFailed);
        setText(optionsStatus, text);
        optionsStatus.hidden = !text;
    }
    /** Discover the sources and folders; the caller renders the outcome with renderLoadStatus. */
    async function loadOptions() {
        optionsRequest?.abort();
        const request = new AbortController(); optionsRequest = request;
        try {
            // Every source's folders (with ancestors), listed per source too, so the dialog can follow its Source.
            const params = new URLSearchParams({mode:'workspace', include_ancestors:'true'});
            const data = await api.get(`/prompt_manager/gallery/subfolders?${params}`, {signal:request.signal});
            if (scope.signal.aborted || request !== optionsRequest) return false;
            if (!Array.isArray(data?.roots) || !Array.isArray(data.subfolders)) throw new TypeError('Invalid image sources');
            const paths = list => Array.isArray(list) ? list.filter(path => typeof path === 'string' && path) : null;
            roots = data.roots.filter(root => Number.isSafeInteger(root?.index) && typeof root.label === 'string')
                .map(root => ({index:root.index, label:root.label, subfolders:paths(root.subfolders)}));
            folders = paths(data.subfolders);
            refine?.setOptions('source', sourceOptions());
            updateScope();
            sourcesRetry = null;
            return true;
        } catch {
            if (scope.signal.aborted || request !== optionsRequest) return false;
            sourcesRetry = recovering ? RETRY_REFRESH : RETRY_RESCAN;
            return false;
        }
    }
    /**
     * The toolbar's categories: the Library's (prompts and recipes) and the listed images' own ones (D141, #332), read
     * in parallel. If only the image list fails, the prompt categories show and the categories alert is raised.
     */
    async function loadCategories() {
        if (!toolbar) return;
        categoryRequest?.abort();
        const request = new AbortController(); categoryRequest = request;
        const [library, images] = await Promise.allSettled([
            api.get('/prompt_manager/library/options', {signal:request.signal}),
            api.get(IMAGE_CATEGORIES_URL, {signal:request.signal}),
        ]);
        if (scope.signal.aborted || request !== categoryRequest) return;
        const valid = result => result.status === 'fulfilled' && Array.isArray(result.value?.categories);
        if (!valid(library)) { categoriesFailed = true; return; }
        // D36: alphabetical (case-insensitive) whatever order the server sends, a restored category among them.
        const names = new Set(mergeCategories(library.value.categories, valid(images) ? images.value.categories : []));
        if (filters.category) names.add(filters.category);
        categoryNames = [...names].sort((a, b) => a.localeCompare(b, 'en-US', {sensitivity:'base'}) || (a < b ? -1 : a > b ? 1 : 0));
        toolbar.setCategories(categoryNames);
        categoriesFailed = !valid(images);
    }
    scope.cleanup(() => { optionsRequest?.abort(); categoryRequest?.abort(); });
    updateScope();
    // The page's one discovery alert is written once both first loads settled (#249 final review m6).
    if (!embedded) Promise.all([loadOptions(), loadCategories()]).then(() => {
        if (scope.signal.aborted) return;
        discoverySettled = true;
        renderLoadStatus();
        if (savedScopePending) reset();
    });
    if (visit) {
        renderState(stream.state);
        const selected = stream.state.items.find(image => imageKey(image) === selection);
        if (selected) inspect(selected);
        restoreFrame = requestAnimationFrame(() => {
            if (!scope.signal.aborted) window.scrollTo(0, visit.scrollY);
        });
        if (visit.state.loading || visit.state.error) stream.loadMore();
    } else if (savedScopePending) updateScope();
    else reset();
    /** D6: the selected file's prompt pair, only once its inspection loaded with both roles resolved. */
    function selectedPair() {
        const {record, prompts, loading, error, refreshRequired} = inspection.state;
        if (scope.signal.aborted || selection === null || loading || error || refreshRequired || record?.identity?.key !== selection) return null;
        const roles = promptRoles(prompts);
        return Object.hasOwn(roles, 'positive') && Object.hasOwn(roles, 'negative') ? roles : null;
    }
    return {dispose, selectedPair, sender};
}
