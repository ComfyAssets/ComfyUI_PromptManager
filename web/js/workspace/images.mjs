import { createScope, createButton } from '../shared/toolkit/core.js';
import { createImageStream, withItem, imageKey, countLine, captionDate, scopeLabel, activeRefinements, cardTitle, cardSubtitle, mediaRatio } from './images-state.mjs';
import { layoutImages, visibleImages, gridMetrics, MINIMUM_CARD_WIDTH } from './image-layout.mjs';
import { mountFileInspector } from './file-inspector.mjs';
import { createInspectionController } from './inspection-state.mjs';
import { mountSelectionBar } from './images-selection.mjs';
import { createPromptSender } from './prompt-sender.mjs';
import { promptRoles } from './prompt-blocks.mjs';
import { createWorkspaceIcon } from './icons.mjs';
import { createMediaViewer } from './media-viewer.mjs';
import { noun } from './plural.mjs';
import { pageHeading } from './heading.mjs';
import { mountToolbar } from './toolbar.mjs';
import { openImportDialog, openRefineDialog } from './workspace-dialogs.mjs';
import { showWorkspaceToast } from './toast.mjs';

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

// The concept's three sorts first, then the production ones it lacks (D14); values are the listing's sorts (B3).
const IMAGE_SORT_OPTIONS = Object.freeze([['newest', 'Recently added'], ['name', 'Name A–Z'], ['rating', 'Highest rated'],
    ['oldest', 'Oldest first'], ['name-desc', 'Name Z–A']]);
const MEDIA_OPTIONS = Object.freeze([['all', 'All media'], ['image', 'Images'], ['video', 'Videos']]);
const RETRY_RESCAN = 'Rescan from Import to retry.';
const RETRY_REFRESH = 'Refresh image collection to retry.';
/** One alert for whichever of the source discovery and the categories failed, so two failures announce once. */
function loadFailure(sourcesRetry, categoriesFailed) {
    if (sourcesRetry && categoriesFailed) return `Sources, folders and categories could not be loaded. ${sourcesRetry}`;
    if (sourcesRetry) return `Sources and folders could not be loaded. ${sourcesRetry}`;
    return categoriesFailed ? `Categories could not be loaded. ${RETRY_RESCAN}` : '';
}
// Import → Rescan (D15): the concept has no rescan copy, so its toasts follow the workspace pattern. The answer
// counts every listed file across the output roots it walked.
const RESCAN_STARTED = 'Rescanning output folders…';
const RESCAN_FAILED = 'Rescan could not finish. Try again.';
function rescanFinished(result) {
    const {total, roots} = result ?? {};
    if (![total, roots].every(value => Number.isSafeInteger(value) && value >= 0)) return 'Rescan finished.';
    return `Rescan finished. ${total.toLocaleString('en-US')} ${noun(total, 'file')} in ${roots.toLocaleString('en-US')} output ${noun(roots, 'folder')}.`;
}
// D7 = A: the concept's star (app.js actions.favorite) against the favorites store; its toasts without the demo's
// "in this tab". The concept has no failure copy, so a failure follows the workspace "could not be …" pattern.
const FAVORITE_URL = '/prompt_manager/images/favorite';
const FAVORITE_DONE = Object.freeze({true:'Added to favorites.', false:'Removed from favorites.'});
const FAVORITE_FAILED = Object.freeze({true:'Favorite could not be added. Try again.', false:'Favorite could not be removed. Try again.'});
// The concept card's first `.media-info` reservation and list row (image-layout.mjs); real rows are measured.
const INITIAL_METRICS = Object.freeze({columns:1, gap:13, footerHeight:54, listHeight:97});

let lastVisit = null;
// One favorite request per file at a time (TESTING.md): the requests outlive the Images page that sent them, so the keys
// in flight and the page that receives each answer live here, beside the saved visit.
const favoritePending = new Set();
let favoriteTarget = null;
// Library-embedded galleries showing media cards (#249 PR E); keyed like the Images page, so each repaints its own copy.
const embeddedFavoriteTargets = new Set();
/**
 * Give a favorite state to the mounted Images page, or to the saved visit while none is mounted, and to every mounted
 * Library-embedded gallery.
 */
function deliverFavorite(key, favorite) {
    if (favoriteTarget) favoriteTarget(key, favorite);
    else if (lastVisit?.state) lastVisit = {...lastVisit, state: withItem(lastVisit.state, key, {favorite})};
    for (const target of embeddedFavoriteTargets) target(key, favorite);
}

export function mountImages(main, api, parentScope, {embedded = false, fetchPage} = {}) {
    const scope = createScope();
    const dispose = () => scope.dispose();
    parentScope.signal.addEventListener('abort', dispose, {once:true});
    scope.cleanup(() => parentScope.signal.removeEventListener('abort', dispose));
    if (parentScope.signal.aborted) { scope.dispose(); return {dispose}; }
    const { resolveUrl } = window.ApiClient;
    const sender = createPromptSender({scope});
    let detailInspector;
    const inspection = createInspectionController({api});
    scope.cleanup(inspection.subscribe(next => detailInspector?.setState(next)));
    scope.cleanup(() => inspection.dispose());
    scope.cleanup(() => detailInspector?.dispose());
    const visit = embedded ? null : lastVisit;
    let density = visit?.density || 'comfortable';
    let metrics = {...INITIAL_METRICS};
    let layout = visit?.layout || 'grid', selection = visit?.selection || null, frame = 0, loadFrame = 0, restoreFrame = 0, observer;
    let state, geometry = { positions: [], columns: 1, height: 0, maxHeight: 0 }, galleryWidth = 0, optionsRequest, categoryRequest, recovering = false, rescanning = false;
    // Discovered sources (each with its own folders) and every folder, for "Refine this view" and the scope chip.
    let roots = [], folders = [];
    const rendered = new Map();
    const itemIndexes = new Map();
    const filters = { root: 'all', q: '', category: '', tags: [], subfolder: '', media: 'all', sort: 'newest', ...visit?.filters };
    filters.tags = [...filters.tags];
    scope.cleanup(() => {
        // A Set category write still in flight may change what the saved collection shows: the return reloads instead.
        if (!embedded) lastVisit = state && !bar.writing() ? { filters: {...filters, tags: [...filters.tags]}, state, layout, density, selection, checked: bar.checked(), scrollY: window.scrollY } : null;
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
                onInspect:() => { window.location.hash = '#/inspect'; }, onRescan:rescan});
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
        }, onFilters:openFilters});
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
    // The concept's caption replaces the old "All images" heading; the embedded gallery keeps its own heading.
    const caption = el('div', undefined, 'section-caption');
    const collectionHeading = embedded ? el('h2', 'Linked images') : null;
    if (collectionHeading) { collectionHeading.tabIndex = -1; caption.append(collectionHeading); }
    const count = el('span', 'Loading images…');
    if (!embedded) count.id = 'result-count';
    const captionDay = el('span');
    captionDay.hidden = true;
    caption.append(count);
    if (!embedded) caption.append(captionDay);
    const focusFallback = collectionHeading || heading?.h1 || main;
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
    const empty = el('div', 'No matching images. Try another search or category.', 'empty');
    empty.hidden = true;
    collection.append(caption, grid);
    if (!embedded) collection.append(empty);
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
    const bar = embedded ? null : mountSelectionBar({scope, api, grid, getItems:() => state.items, focusFallback,
        checked:visit?.checked, onCategorySet:() => { loadCategories(); reset(); }});
    if (bar) main.append(bar.element);
    main.append(body);
    const mediaViewer = createMediaViewer({container:main,parentScope:scope,inspectionController:inspection,sender,
        onSelect:image => {if (selection !== imageKey(image)) inspect(image);else inspection.select(image);},
        onRefresh:refreshInspectionCollection,onClose:scheduleLoad,onRestoreFocus:image => {
            const index = state?.items.findIndex(entry => imageKey(entry) === imageKey(image));
            if (index >= 0) focusImage(index);else focusFallback.focus({preventScroll:true});
        }});
    scope.cleanup(() => mediaViewer.dispose());

    function mediaElement(image, lazy = false) {
        const video = image.is_video && !image.thumbnail_url;
        const node = el(video ? 'video' : 'img');
        node.src = resolveUrl(image.thumbnail_url || image.url);
        if (video) { node.muted = true; node.playsInline = true; node.preload = 'metadata'; }
        else {
            // The concept card image: alt is the card title, with the listing's intrinsic size when known.
            node.alt = cardTitle(image); node.decoding = 'async'; if (lazy) node.loading = 'lazy';
            if ([image.width, image.height].every(value => Number.isFinite(value) && value > 0)) {
                node.width = image.width; node.height = image.height;
            }
        }
        return node;
    }
    function releaseVideo(video) {
        video.pause(); video.removeAttribute('src'); video.load();
    }
    function inspect(image) {
        detailInspector?.dispose();
        selection = imageKey(image);
        for (const video of details.querySelectorAll('video')) releaseVideo(video);
        detailInspector = mountFileInspector(details, {sender, parentScope:scope, variant:'panel',
            onTagFilter:embedded ? undefined : filterByTag, onRetry:() => inspection.retry(), onRefresh:refreshInspectionCollection});
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
    /** The concept media card (app.js mediaCard): the image opens the viewer, Info inspects (D7–D11, D19). */
    function cardFor(image) {
        const key = imageKey(image);
        const card = el('article', undefined, 'media-card');
        card.dataset.imageKey = key; card.setAttribute('role', 'listitem');
        card.style.setProperty('--media-ratio', mediaRatio(image));
        const art = el('div', undefined, 'media-art');
        const open = createButton('', 'image-open');
        open.setAttribute('aria-label', `Open ${image.filename}`);
        open.append(mediaElement(image, true));
        // D8: the selection checkbox (Images only: Library-embedded media has no selection bar), then D7: the star,
        // wherever the listing reports favorites (Images and Library-embedded media).
        const overlays = el('div', undefined, 'image-overlays');
        if (bar) {
            const label = el('label'), box = el('input');
            box.type = 'checkbox'; box.checked = bar.isChecked(key);
            box.setAttribute('aria-label', `Select ${image.filename}`);
            label.append(box);
            overlays.append(label);
        }
        if (typeof image.favorite === 'boolean') {
            const star = createButton('', 'star-button');
            star.setAttribute('aria-label', `Favorite ${image.filename}`);
            paintStar(star, image.favorite, favoritePending.has(key));
            overlays.append(star);
        }
        art.append(open, overlays);
        const info = el('div', undefined, 'media-info');
        const text = el('div');
        const title = el('h3', cardTitle(image)); title.title = image.path;
        text.append(title, el('p', cardSubtitle(image)));
        const actions = el('div', undefined, 'image-card-actions');
        // D9: the tag button waits for the image-annotations slice.
        const inspectButton = createButton('', 'icon-btn');
        inspectButton.append(createWorkspaceIcon('info'));
        inspectButton.setAttribute('aria-label', `Inspect ${image.filename}`);
        actions.append(inspectButton);
        info.append(text, actions);
        card.append(art, info);
        markSelected(card, key === selection);
        return card;
    }
    /** A star whose request is still in flight is marked busy: pressing it again does nothing until the answer. */
    function paintStar(button, favorite, pending) {
        button.textContent = favorite ? '★' : '☆';
        button.setAttribute('aria-pressed', String(favorite));
        if (pending) button.setAttribute('aria-disabled', 'true'); else button.removeAttribute('aria-disabled');
    }
    function setFavorite(key, favorite) {
        if (scope.signal.aborted) return;
        stream.updateItem(key, {favorite});
        const button = rendered.get(key)?.querySelector('.star-button');
        if (button) paintStar(button, favorite, favoritePending.has(key));
    }
    // The Images page and every Library-embedded gallery receive favorite answers (#249 D7, PR E).
    if (!embedded) {
        favoriteTarget = setFavorite;
        scope.cleanup(() => { if (favoriteTarget === setFavorite) favoriteTarget = null; });
    } else {
        embeddedFavoriteTargets.add(setFavorite);
        scope.cleanup(() => embeddedFavoriteTargets.delete(setFavorite));
    }
    /** Optimistic toggle: the star changes at once and returns to its previous state if the store refuses. */
    async function toggleFavorite(image) {
        const key = imageKey(image);
        if (favoritePending.has(key)) return;
        const favorite = !image.favorite;
        favoritePending.add(key);
        deliverFavorite(key, favorite);
        const body = {root_index: image.root_index, path: image.path};
        let saved = false;
        try {
            await (favorite ? api.put(FAVORITE_URL, body) : api.del(FAVORITE_URL, {body}));
            saved = true;
        } catch {
            // Announced below; the star returns to its previous state.
        }
        favoritePending.delete(key);
        deliverFavorite(key, saved ? favorite : !favorite);
        showWorkspaceToast(saved ? FAVORITE_DONE[favorite] : FAVORITE_FAILED[favorite]);
    }
    scope.listen(grid, 'click', event => {
        const card = event.target.closest('article[data-image-key]');
        if (!card) return;
        const image = state.items.find(item => imageKey(item) === card.dataset.imageKey);
        if (!image) return;
        if (event.target.closest('.star-button')) toggleFavorite(image);
        else if (event.target.closest('.image-open')) previewImage(image);
        else if (event.target.closest('.image-card-actions button')) inspect(image);
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
        if (target !== undefined) { event.preventDefault(); focusImage(Math.max(0, Math.min(state.items.length - 1, target))); }
        else if (event.key === 'Tab') {
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
        grid.classList.toggle('narrow-cards', galleryWidth < MINIMUM_CARD_WIDTH);
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
    function continuationText(next) {
        if (recovering) return 'Images paused until sources and folders are available.';
        if (next.error) return 'Images could not be loaded. Retry or rescan the collection.';
        if (next.loading && !next.loaded) return 'Loading images…';
        if (next.hasMore) return `${next.loaded.toLocaleString('en-US')} of ${next.total.toLocaleString('en-US')} ${noun(next.total, 'image')} · more load as you scroll`;
        if (embedded) return next.loaded ? 'You’re all caught up.' : 'No linked media available for this selection.';
        // The concept's end of the collection (app.js updateImageContinuation).
        return `${next.loaded ? 'You’re all caught up' : 'No matching images'} · ${next.total.toLocaleString('en-US')} ${noun(next.total, 'image')}`;
    }
    function renderState(next, {patch} = {}) {
        const previous = state;
        // A patched item (a favorite) keeps every card's size and place; setFavorite repaints its card.
        const changed = !patch && state?.items !== next.items;
        if (changed) {
            if (!next.items.length) itemIndexes.clear();
            for (let index = state?.items.length || 0; index < next.items.length; index++) itemIndexes.set(imageKey(next.items[index]), index);
        }
        state = next;
        setText(count, embedded ? `${next.loaded.toLocaleString('en-US')} loaded · ${next.total.toLocaleString('en-US')} ${noun(next.total, 'linked image')}` : countLine(next));
        if (!embedded) {
            setText(captionDay, captionDate(next.items, filters.sort));
            if (captionDay.hidden !== !captionDay.textContent) captionDay.hidden = !captionDay.textContent;
        }
        if (changed) updateLayout();
        // The concept's Load more toast (app.js loadMoreImages) for every batch appended to a loaded collection.
        if (!embedded && changed && previous?.loaded && next.loaded > previous.loaded) {
            const added = next.loaded - previous.loaded;
            showWorkspaceToast(`${added.toLocaleString('en-US')} more ${noun(added, 'image')} loaded. ${next.loaded.toLocaleString('en-US')} of ${next.total.toLocaleString('en-US')}.`);
        }
        setText(message, continuationText(next));
        setAttr(message, 'role', next.error ? 'alert' : 'status');
        retry.hidden = !next.error;
        more.hidden = !next.hasMore || !!next.error;
        more.setAttribute('aria-disabled', String(next.loading || recovering));
        if (!embedded) empty.hidden = recovering || next.loading || !!next.error || next.hasMore || next.loaded > 0;
        if (focusAfterLoad !== null && !next.loading) settleLoadFocus(next);
        if (!selection && next.items.length) inspect(next.items[0]);
        scheduleLoad();
    }
    const stream = createImageStream({ api, fetchPage, onChange: renderState, restore: visit ? {filters, state:visit.state} : undefined });
    scope.cleanup(() => {
        stream.dispose();
        for (const video of body.querySelectorAll('video')) releaseVideo(video);
    });
    function pressLoad() {
        if (recovering || state?.loading) return;
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
    scope.listen(retry, 'click', pressLoad);
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
    function reset() {
        mediaViewer.stopSlideshow();
        main.querySelector('#viewer-dialog[open]')?.close();
        if (!recovering) {
            inspection.clear();
            detailInspector?.dispose(); detailInspector = null;
            details.replaceChildren(noSelection());
        }
        selection = null;
        focusAfterLoad = null;
        // The ruled selection lifetime: a replaced collection may no longer hold the checked files.
        bar?.clear();
        for (const video of details.querySelectorAll('video')) releaseVideo(video);
        updateScope();
        stream.reset(filters, {load:!recovering});
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
        openRefineDialog({scope, trigger, fields:[
            {key:'source', options:[['all', 'All sources'], ...roots.map(root => [String(root.index), root.label])]},
            {key:'folder', options:folderOptions('all'), optionsFor:folderOptions},
            {key:'media', options:MEDIA_OPTIONS},
            {key:'tags'},
        ], values:{source:filters.root, folder:filters.subfolder, media:filters.media, tags:filters.tags.join(', ')},
        onApply:applyRefinement, onClear:cleared => {
            toolbar.setValue({category:cleared.category});
            applyRefinement(cleared);
        }});
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
            if (!scope.signal.aborted) showWorkspaceToast(rescanFinished(result));
        } catch {
            // A failed rescan leaves the collection and any discovery alert as they were.
            if (!scope.signal.aborted) showWorkspaceToast(RESCAN_FAILED);
        } finally {
            rescanning = false;
            importDialog?.setRescanBusy(false);
        }
    }
    async function refreshInspectionCollection() {
        if (scope.signal.aborted) return;
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
            updateScope();
            sourcesRetry = null;
            return true;
        } catch {
            if (scope.signal.aborted || request !== optionsRequest) return false;
            sourcesRetry = recovering ? RETRY_REFRESH : RETRY_RESCAN;
            return false;
        }
    }
    /** The toolbar's categories are the Library's (the linked positive's category, D13). */
    async function loadCategories() {
        if (!toolbar) return;
        categoryRequest?.abort();
        const request = new AbortController(); categoryRequest = request;
        try {
            const data = await api.get('/prompt_manager/library/options', {signal:request.signal});
            if (scope.signal.aborted || request !== categoryRequest) return;
            if (!Array.isArray(data?.categories)) throw new TypeError('Invalid categories');
            // D36: alphabetical (case-insensitive) whatever order the server sends, a restored category among them.
            const names = new Set([...data.categories.filter(value => typeof value === 'string' && value), filters.category]);
            names.delete('');
            toolbar.setCategories([...names].sort((a, b) => a.localeCompare(b, 'en-US', {sensitivity:'base'}) || (a < b ? -1 : a > b ? 1 : 0)));
            categoriesFailed = false;
        } catch {
            if (scope.signal.aborted || request !== categoryRequest) return;
            categoriesFailed = true;
        }
    }
    scope.cleanup(() => { optionsRequest?.abort(); categoryRequest?.abort(); });
    updateScope();
    // The page's one discovery alert is written once both first loads settled (#249 final review m6).
    if (!embedded) Promise.all([loadOptions(), loadCategories()]).then(() => { if (!scope.signal.aborted) renderLoadStatus(); });
    if (visit) {
        renderState(stream.state);
        const selected = stream.state.items.find(image => imageKey(image) === selection);
        if (selected) inspect(selected);
        restoreFrame = requestAnimationFrame(() => {
            if (!scope.signal.aborted) window.scrollTo(0, visit.scrollY);
        });
        if (visit.state.loading || visit.state.error) stream.loadMore();
    } else reset();
    /** D6: the selected file's prompt pair, only once its inspection loaded with both roles resolved. */
    function selectedPair() {
        const {record, prompts, loading, error, refreshRequired} = inspection.state;
        if (scope.signal.aborted || selection === null || loading || error || refreshRequired || record?.identity?.key !== selection) return null;
        const roles = promptRoles(prompts);
        return Object.hasOwn(roles, 'positive') && Object.hasOwn(roles, 'negative') ? roles : null;
    }
    return {dispose, selectedPair, sender};
}
