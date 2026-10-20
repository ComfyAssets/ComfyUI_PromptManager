import { createScope, createButton } from '../shared/toolkit/core.js';
import { createImageStream, imageKey, countLine, captionDate, scopeLabel } from './images-state.mjs';
import { layoutImages, visibleImages, workspaceImageMetrics } from './image-layout.mjs';
import { mountFileInspector } from './file-inspector.mjs';
import { createInspectionController } from './inspection-state.mjs';
import { createPromptSender } from './prompt-sender.mjs';
import { promptRoles } from './prompt-blocks.mjs';
import { createWorkspaceIcon } from './icons.mjs';
import { createMediaViewer } from './media-viewer.mjs';
import { noun } from './plural.mjs';
import { pageHeading } from './heading.mjs';
import { mountToolbar } from './toolbar.mjs';
import { openImportDialog, openRefineDialog } from './workspace-dialogs.mjs';
import { showWorkspaceToast } from './toast.mjs';

function el(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
}

function formatBytes(value) {
    return value >= 1048576 ? `${(value / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(value / 1024))} KB`;
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
const SOURCES_FAILED = 'Sources and folders could not be loaded. Rescan from Import to retry.';
const CATEGORIES_FAILED = 'Categories could not be loaded. Rescan from Import to retry.';
const RESCAN_FAILED = 'Output folders could not be rescanned. Try Rescan again.';

let lastVisit = null;

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
    let metrics = workspaceImageMetrics(window.innerWidth, density);
    let layout = visit?.layout || 'grid', selection = visit?.selection || null, frame = 0, loadFrame = 0, restoreFrame = 0, observer;
    let state, geometry = { positions: [], height: 0, maxHeight: 0 }, galleryWidth = 0, optionsRequest, categoryRequest, recovering = false, rescanning = false;
    // Discovered sources and folders for "Refine this view" and the scope chip.
    let roots = [], folders = [];
    const rendered = new Map();
    const itemIndexes = new Map();
    const filters = { root: 'all', q: '', category: '', tags: [], subfolder: '', media: 'all', sort: 'newest', ...visit?.filters };
    filters.tags = [...filters.tags];
    scope.cleanup(() => {
        if (!embedded) lastVisit = state ? { filters: {...filters, tags: [...filters.tags]}, state, layout, density, selection, scrollY: window.scrollY } : null;
        cancelAnimationFrame(restoreFrame);
    });
    main.classList.add('images-main');
    scope.cleanup(() => main.classList.remove('images-main'));
    // The concept's Images heading (app.js renderImages): description, then Import before the shell's New prompt.
    const heading = embedded ? null : pageHeading(main);
    let importButton = null;
    if (heading) {
        heading.description.textContent = 'Your generations, their prompts, and the next idea.';
        importButton = createButton('');
        importButton.append(createWorkspaceIcon('upload'), el('span', 'Import'));
        heading.actions.prepend(importButton);
        scope.cleanup(() => importButton.remove());
        scope.listen(importButton, 'click', () => openImportDialog({scope, trigger:importButton,
            onInspect:() => { window.location.hash = '#/inspect'; }, onRescan:rescan}));
    }
    // The shared concept toolbar: search (filename, linked prompt text, title and tags), category and sort (D12–D14).
    const toolbar = embedded ? null : mountToolbar(main, {scope, categories:filters.category ? [filters.category] : [],
        sorts:IMAGE_SORT_OPTIONS, value:{q:filters.q, category:filters.category, sort:filters.sort},
        onChange:value => {
            if (recovering) return;
            Object.assign(filters, {q:value.q.trim(), category:value.category, sort:value.sort});
            reset();
        }, onFilters:openFilters});
    const toolbarControls = toolbar ? [...toolbar.element.querySelectorAll('input, select, button')] : [];
    const optionsStatus = el('p', '', 'options-status muted');
    optionsStatus.hidden = true; optionsStatus.setAttribute('role', 'alert');
    const categoryStatus = el('p', '', 'options-status muted');
    categoryStatus.hidden = true; categoryStatus.setAttribute('role', 'alert');
    if (!embedded) main.append(optionsStatus, categoryStatus);
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
    const details = el('section', undefined, 'image-details panel');
    details.setAttribute('aria-label', 'Image details');
    details.append(el('h2', 'Image details'), el('p', 'Select an image to inspect it.'));
    body.append(collection, details);
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
        else { node.alt = image.filename; node.decoding = 'async'; if (lazy) node.loading = 'lazy'; }
        return node;
    }
    function releaseVideo(video) {
        video.pause(); video.removeAttribute('src'); video.load();
    }
    function inspect(image) {
        detailInspector?.dispose();
        selection = imageKey(image);
        for (const video of details.querySelectorAll('video')) releaseVideo(video);
        detailInspector = mountFileInspector(details, {sender, parentScope:scope, inline:true,
            onPreview:() => previewImage(image), onRetry:() => inspection.retry(), onRefresh:refreshInspectionCollection});
        detailInspector.setState(inspection.state);
        inspection.select(image);
        for (const [key, card] of rendered) {
            card.classList.toggle('selected', key === selection);
            card.querySelector('.image-open').setAttribute('aria-pressed', String(key === selection));
        }
    }
    function previewImage(image) {
        if (!image) return;
        mediaViewer.open({items:state.items,selectedKey:imageKey(image),trigger:document.activeElement});
    }
    function cardFor(image) {
        const key = imageKey(image);
        const card = el('article', undefined, 'image-card');
        card.dataset.imageKey = key; card.setAttribute('role', 'listitem');
        const open = createButton('');
        open.className = 'image-open'; open.setAttribute('aria-label', `Open ${image.filename}`);
        open.title = 'Show image details'; open.setAttribute('aria-pressed', String(key === selection));
        const img = mediaElement(image, true);
        open.append(img);
        const footer = el('div', undefined, 'image-card-footer');
        const title = el('strong', image.filename); title.title = image.path;
        const dimensions = image.width && image.height ? `${image.width} × ${image.height} · ` : '';
        const type = image.media_type === 'video' ? 'Video' : image.filename.split('.').pop().toUpperCase();
        const sub = el('small', `${dimensions}${type} · ${formatBytes(image.size)}`);
        const preview = createButton('', 'ca-icon-button');
        preview.append(createWorkspaceIcon('images'));
        preview.setAttribute('aria-label', `Preview ${image.filename}`);
        footer.append(title, sub, preview); card.append(open, footer);
        return card;
    }
    scope.listen(grid, 'click', event => {
        const card = event.target.closest('article[data-image-key]');
        if (!card) return;
        const image = state.items.find(item => imageKey(item) === card.dataset.imageKey);
        if (event.target.closest('.image-card-footer button')) previewImage(image);
        else if (event.target.closest('button')) inspect(image);
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
        // Natural footer measurements and absolute reservations share unzoomed CSS units.
        const footerHeight = Math.max(metrics.footerHeight, ...[...rendered.values()].map(card => card.querySelector('.image-card-footer').offsetHeight));
        if (footerHeight > metrics.footerHeight) {
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
        card.classList.toggle('selected', imageKey(image) === selection);
        card.querySelector('.image-open').setAttribute('aria-pressed', String(imageKey(image) === selection));
        card.setAttribute('aria-posinset', String(index + 1)); card.setAttribute('aria-setsize', String(state.total));
        Object.assign(card.style, { left: `${position.x}px`, top: `${position.y}px`, width: `${position.width}px`, height: `${position.height}px` });
    }
    function focusImage(index, preview = false, {scroll = true} = {}) {
        if (index < 0 || index >= state.items.length) return;
        const image = state.items[index], key = imageKey(image);
        let card = rendered.get(key);
        if (!card) {
            card = cardFor(image); rendered.set(key, card); positionCard(card, index);
            grid.insertBefore(card, [...grid.children].find(child => Number(child.dataset.index) > index) || null);
        }
        card.querySelector(preview ? '.image-card-footer button' : '.image-open').focus({ preventScroll: true });
        if (scroll) card.scrollIntoView({ block: 'nearest' });
        cancelAnimationFrame(frame); frame = 0;
        renderVisible();
    }
    scope.listen(grid, 'keydown', event => {
        const card = event.target.closest('article[data-image-key]');
        if (!card) return;
        const index = Number(card.dataset.index);
        const columns = layout === 'list' ? 1 : metrics.columns;
        const target = { Home: 0, End: state.items.length - 1, ArrowRight: index + 1,
            ArrowLeft: index - 1, ArrowDown: index + columns, ArrowUp: index - columns }[event.key];
        if (target !== undefined) { event.preventDefault(); focusImage(Math.max(0, Math.min(state.items.length - 1, target))); }
        else if (event.key === 'Tab') {
            const backward = event.shiftKey && event.target.matches('.image-open');
            const forward = !event.shiftKey && event.target.closest('.image-card-footer');
            const adjacent = backward ? index - 1 : index + 1;
            if ((backward || forward) && state.items[adjacent] && !rendered.has(imageKey(state.items[adjacent]))) {
                event.preventDefault(); focusImage(adjacent, backward);
            }
        }
    });
    function scheduleVisible() { if (!frame) frame = requestAnimationFrame(() => { frame = 0; renderVisible(); }); }
    function updateGeometry() {
        geometry = layoutImages(state.items, galleryWidth, layout, 240, metrics);
        grid.style.height = `${geometry.height}px`;
        grid.style.setProperty('--image-footer-height', `${metrics.footerHeight}px`);
    }
    function updateLayout() {
        if (!state || scope.signal.aborted) return;
        cancelAnimationFrame(frame); frame = 0;
        const width = Number.parseFloat(getComputedStyle(grid).width);
        const retained = state.items.length && width === galleryWidth && grid.dataset.layout === layout && grid.dataset.density === density ? metrics : null;
        galleryWidth = width;
        metrics = workspaceImageMetrics(window.innerWidth, density, galleryWidth);
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
    function renderState(next) {
        const previous = state;
        const changed = state?.items !== next.items;
        if (changed) {
            if (!next.items.length) itemIndexes.clear();
            for (let index = state?.items.length || 0; index < next.items.length; index++) itemIndexes.set(imageKey(next.items[index]), index);
        }
        state = next;
        count.textContent = embedded ? `${next.loaded.toLocaleString('en-US')} loaded · ${next.total.toLocaleString('en-US')} ${noun(next.total, 'linked image')}` : countLine(next);
        if (!embedded) {
            captionDay.textContent = captionDate(next.items, filters.sort);
            captionDay.hidden = !captionDay.textContent;
        }
        if (changed) updateLayout();
        // The concept's Load more toast (app.js loadMoreImages) for every batch appended to a loaded collection.
        if (!embedded && changed && previous?.loaded && next.loaded > previous.loaded) {
            const added = next.loaded - previous.loaded;
            showWorkspaceToast(`${added.toLocaleString('en-US')} more ${noun(added, 'image')} loaded. ${next.loaded.toLocaleString('en-US')} of ${next.total.toLocaleString('en-US')}.`);
        }
        message.textContent = continuationText(next);
        if (next.error) message.setAttribute('role', 'alert'); else message.setAttribute('role', 'status');
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
        if (!observer || recovering || scope.signal.aborted || !state?.hasMore || state.loading || state.error || main.querySelector('.image-preview[open]')) return;
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
    }
    function reset() {
        mediaViewer.stopSlideshow();
        main.querySelector('.image-preview[open]')?.close();
        if (!recovering) {
            inspection.clear();
            detailInspector?.dispose(); detailInspector = null;
            details.replaceChildren(el('h2', 'Image details'), el('p', 'Select an image to inspect it.'));
        }
        selection = null;
        focusAfterLoad = null;
        for (const video of details.querySelectorAll('video')) releaseVideo(video);
        updateScope();
        stream.reset(filters, {load:!recovering});
    }
    /** "Refine this view": Source, Folder, Media and Tags, the filters the old field row held (D14). */
    function openFilters(trigger) {
        if (recovering) return;
        openRefineDialog({scope, trigger, fields:[
            {key:'source', options:[['all', 'All sources'], ...roots.map(root => [String(root.index), root.label])]},
            {key:'folder', options:[['', 'All output folders'], ...folders.map(path => [path, path])]},
            {key:'media', options:MEDIA_OPTIONS},
            {key:'tags'},
        ], values:{source:filters.root, folder:filters.subfolder, media:filters.media, tags:filters.tags.join(', ')},
        onApply:applyRefinement, onClear:cleared => {
            toolbar.setValue({category:cleared.category});
            filters.category = cleared.category;
            applyRefinement(cleared);
        }});
    }
    function applyRefinement({source, folder, media, tags}) {
        if (recovering) return;
        Object.assign(filters, {root:source, subfolder:folder, media,
            tags:[...new Set(tags.split(',').map(tag => tag.trim()).filter(Boolean))]});
        reset();
    }
    function setControlsDisabled(disabled) {
        for (const control of [...toolbarControls, more, retry]) control.disabled = disabled;
        if (importButton) importButton.disabled = disabled;
    }
    /** Import → "Scan an output folder" (D15): rescan the output roots, then reload the options and the listing. */
    async function rescan() {
        if (rescanning || recovering || scope.signal.aborted) return;
        rescanning = true;
        optionsRequest?.abort(); optionsRequest = null;
        optionsStatus.hidden = true;
        try {
            await api.post('/prompt_manager/gallery/rescan', {}, {signal:scope.signal});
            if (scope.signal.aborted) return;
            loadCategories();
            if (await loadOptions() && !scope.signal.aborted) reset();
        } catch {
            if (scope.signal.aborted) return;
            optionsStatus.textContent = RESCAN_FAILED;
            optionsStatus.hidden = false;
        } finally {
            rescanning = false;
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
        optionsStatus.hidden = true;
        reset();
        const discovery = loadOptions(), request = optionsRequest;
        if (!await discovery || scope.signal.aborted || request !== optionsRequest) return;
        recovering = false;
        setControlsDisabled(false);
        reset();
    }
    async function loadOptions() {
        optionsRequest?.abort();
        const request = new AbortController(); optionsRequest = request;
        try {
            // Every source's folders (with ancestors), so any Source and Folder pair can be chosen in one dialog.
            const params = new URLSearchParams({mode:'workspace', include_ancestors:'true'});
            const data = await api.get(`/prompt_manager/gallery/subfolders?${params}`, {signal:request.signal});
            if (scope.signal.aborted || request !== optionsRequest) return false;
            if (!Array.isArray(data?.roots) || !Array.isArray(data.subfolders)) throw new TypeError('Invalid image sources');
            roots = data.roots.filter(root => Number.isSafeInteger(root?.index) && typeof root.label === 'string');
            folders = data.subfolders.filter(path => typeof path === 'string' && path);
            updateScope();
            optionsStatus.hidden = true;
            return true;
        } catch {
            if (scope.signal.aborted || request !== optionsRequest) return false;
            optionsStatus.textContent = recovering ? 'Sources and folders could not be loaded. Refresh image collection to retry.' : SOURCES_FAILED;
            optionsStatus.hidden = false;
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
            toolbar.setCategories([...data.categories.filter(value => typeof value === 'string'), filters.category]);
            categoryStatus.hidden = true;
        } catch {
            if (scope.signal.aborted || request !== categoryRequest) return;
            categoryStatus.textContent = CATEGORIES_FAILED;
            categoryStatus.hidden = false;
        }
    }
    scope.cleanup(() => { optionsRequest?.abort(); categoryRequest?.abort(); });
    updateScope();
    if (!embedded) { loadOptions(); loadCategories(); }
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
