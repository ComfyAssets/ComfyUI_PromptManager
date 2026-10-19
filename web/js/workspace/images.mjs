import { createScope, createButton, createField, createSelect } from '../shared/toolkit/core.js';
import { createImageStream, imageKey } from './images-state.mjs';
import { layoutImages, visibleImages, workspaceImageMetrics } from './image-layout.mjs';
import { mountFileInspector } from './file-inspector.mjs';
import { createInspectionController } from './inspection-state.mjs';
import { createPromptSender } from './prompt-sender.mjs';
import { createWorkspaceIcon } from './icons.mjs';
import { createMediaViewer } from './media-viewer.mjs';
import { noun } from './plural.mjs';

function el(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
}

function formatBytes(value) {
    return value >= 1048576 ? `${(value / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(value / 1024))} KB`;
}

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
    let state, geometry = { positions: [], height: 0, maxHeight: 0 }, galleryWidth = 0, optionsRequest, recovering = false;
    const rendered = new Map();
    const itemIndexes = new Map();
    const filters = { root: 'all', q: '', subfolder: '', media: 'all', sort: 'newest', ...visit?.filters };
    scope.cleanup(() => {
        if (!embedded) lastVisit = state ? { filters: {...filters}, state, layout, density, selection, scrollY: window.scrollY } : null;
        cancelAnimationFrame(restoreFrame);
    });
    main.classList.add('images-main');
    scope.cleanup(() => main.classList.remove('images-main'));
    if (!embedded) {
        const intro = el('p', 'Browse your images, explore the details, keep your creative context.', 'intro');
        (main.querySelector('.workspace-heading-copy') || main).append(intro);
        scope.cleanup(() => intro.remove());
    }
    const filtersBar = el('div', undefined, 'image-filters');
    const search = el('input');
    search.value = filters.q;
    search.type = 'search'; search.placeholder = 'Find an image by filename or folder…'; search.maxLength = 256;
    const source = createSelect([['all', 'All sources']], '');
    const folder = createSelect([['', 'All folders']], '');
    const media = createSelect([['all', 'All media'], ['image', 'Images'], ['video', 'Videos']], '');
    const sort = createSelect([['newest', 'Newest first'], ['oldest', 'Oldest first'], ['name', 'Filename A–Z'], ['name-desc', 'Filename Z–A']], '');
    media.value = filters.media; sort.value = filters.sort;
    filtersBar.append(createField('Search filenames', search, 'image-search'), createField('Source', source, 'image-source'),
        createField('Folder', folder, 'image-folder'), createField('Media', media, 'image-media'), createField('Sort', sort, 'image-sort'));
    const optionsStatus = el('p', '', 'options-status muted');
    optionsStatus.hidden = true; optionsStatus.setAttribute('role', 'alert');
    if (!embedded) main.append(filtersBar, optionsStatus);
    const controls = el('div', undefined, 'image-view-controls');
    const toolbar = el('div', undefined, 'image-toolbar');
    const collectionHeading = el('h2', embedded ? 'Linked images' : 'All images');collectionHeading.tabIndex = -1;
    toolbar.append(collectionHeading);
    const count = el('p', 'Loading images…', 'muted');
    const layouts = el('div', undefined, 'image-layouts');
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
    const refresh = createButton('Refresh');
    scope.listen(refresh, 'click', async () => {
        if (refresh.disabled) return;
        refresh.disabled = true;
        optionsRequest?.abort(); optionsRequest = null;
        optionsStatus.hidden = true;
        try {
            await api.post('/prompt_manager/gallery/rescan', {}, {signal:scope.signal});
            if (scope.signal.aborted) return;
            if (await loadOptions() && !scope.signal.aborted) reset();
        } catch {
            if (scope.signal.aborted) return;
            optionsStatus.textContent = 'Images could not be refreshed. Try Refresh again.';
            optionsStatus.hidden = false;
        } finally {
            refresh.disabled = recovering;
        }
    });
    const currentGallery = el('a', 'Current gallery', 'workspace-link');
    currentGallery.href = resolveUrl('/prompt_manager/gallery');
    const thumbnails = createSelect([['comfortable', 'Comfortable'], ['large', 'Large'], ['compact', 'Compact']], '');
    thumbnails.value = density;
    scope.listen(thumbnails, 'change', () => { density = thumbnails.value; updateLayout(); });
    controls.append(layouts, createField('Thumbnail size', thumbnails, 'image-density'));
    if (!embedded) {
        const group = el('a', 'Group by positive prompt', 'workspace-link');
        group.href = '#/library'; group.prepend(createWorkspaceIcon('branch'));
        controls.append(group, refresh);
    }
    main.append(controls);
    toolbar.append(count);
    const body = el('div', undefined, 'image-workspace');
    const collection = el('section', undefined, 'image-collection');
    collection.setAttribute('aria-label', 'Images');
    const grid = el('div', undefined, 'image-grid');
    grid.setAttribute('role', 'list'); grid.setAttribute('aria-label', 'Image collection');
    const continuation = el('div', undefined, 'image-continuation');
    const message = el('p');
    const more = createButton('Load more images');
    const retry = createButton('Retry');
    const end = el('p', '', 'muted');
    continuation.append(message, more, retry, end);
    collection.append(toolbar, grid, continuation);
    if (!embedded) collection.append(currentGallery);
    const details = el('section', undefined, 'image-details panel');
    details.setAttribute('aria-label', 'Image details');
    details.append(el('h2', 'Image details'), el('p', 'Select an image to inspect it.'));
    body.append(collection, details);
    main.append(body);
    const mediaViewer = createMediaViewer({container:main,parentScope:scope,inspectionController:inspection,sender,
        onSelect:image => {if (selection !== imageKey(image)) inspect(image);else inspection.select(image);},
        onRefresh:refreshInspectionCollection,onClose:scheduleLoad,onRestoreFocus:image => {
            const index = state?.items.findIndex(entry => imageKey(entry) === imageKey(image));
            if (index >= 0) focusImage(index);else collectionHeading.focus({preventScroll:true});
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
    function focusImage(index, preview = false) {
        if (index < 0 || index >= state.items.length) return;
        const image = state.items[index], key = imageKey(image);
        let card = rendered.get(key);
        if (!card) {
            card = cardFor(image); rendered.set(key, card); positionCard(card, index);
            grid.insertBefore(card, [...grid.children].find(child => Number(child.dataset.index) > index) || null);
        }
        card.querySelector(preview ? '.image-card-footer button' : '.image-open').focus({ preventScroll: true });
        card.scrollIntoView({ block: 'nearest' });
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
    function renderState(next) {
        const changed = state?.items !== next.items;
        if (changed) {
            if (!next.items.length) itemIndexes.clear();
            for (let index = state?.items.length || 0; index < next.items.length; index++) itemIndexes.set(imageKey(next.items[index]), index);
        }
        state = next;
        count.textContent = embedded ? `${next.loaded.toLocaleString()} loaded · ${next.total.toLocaleString()} ${noun(next.total, 'linked image')}` : `${next.loaded.toLocaleString()} of ${next.total.toLocaleString()} ${noun(next.total, 'image')}`;
        if (changed) updateLayout();
        message.textContent = recovering ? 'Images paused until sources and folders are available.' : next.error ? 'Images could not be loaded. Retry or refresh the collection.' : next.loading ? 'Loading images…' : '';
        if (next.error) message.setAttribute('role', 'alert'); else message.setAttribute('role', 'status');
        retry.hidden = !next.error;
        more.hidden = !next.hasMore || !!next.error;
        more.setAttribute('aria-disabled', String(next.loading || recovering));
        end.textContent = !recovering && !next.loading && !next.error && !next.hasMore ? (next.loaded ? 'You’re all caught up.' : embedded ? 'No linked media available for this selection.' : 'No matching images or media. Try changing the filters.') : '';
        if (!selection && next.items.length) inspect(next.items[0]);
        scheduleLoad();
    }
    const stream = createImageStream({ api, fetchPage, onChange: renderState, restore: visit ? {filters, state:visit.state} : undefined });
    scope.cleanup(() => {
        stream.dispose();
        for (const video of body.querySelectorAll('video')) releaseVideo(video);
    });
    scope.listen(more, 'click', () => { if (!recovering) stream.loadMore(); });
    scope.listen(retry, 'click', () => { if (!recovering) stream.loadMore(); });
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
    function reset() {
        mediaViewer.stopSlideshow();
        main.querySelector('.image-preview[open]')?.close();
        if (!recovering) {
            inspection.clear();
            detailInspector?.dispose(); detailInspector = null;
            details.replaceChildren(el('h2', 'Image details'), el('p', 'Select an image to inspect it.'));
        }
        selection = null;
        for (const video of details.querySelectorAll('video')) releaseVideo(video);
        stream.reset(filters, {load:!recovering});
    }
    async function refreshInspectionCollection() {
        if (scope.signal.aborted) return;
        // Numeric roots may now name another source; retire the old stream first.
        recovering = true;
        filters.root = 'all';filters.subfolder = '';filters.q = search.value.trim();
        source.replaceChildren(new Option('All sources', 'all'));folder.replaceChildren(new Option('All folders', ''));
        for (const control of [search,source,folder,media,sort,refresh,more,retry]) control.disabled = true;
        optionsStatus.hidden = true;
        reset();
        const discovery = loadOptions(), request = optionsRequest;
        if (!await discovery || scope.signal.aborted || request !== optionsRequest) return;
        recovering = false;
        for (const control of [search,source,folder,media,sort,refresh,more,retry]) control.disabled = false;
        reset();
    }
    async function loadOptions() {
        optionsRequest?.abort();
        const request = new AbortController(); optionsRequest = request;
        try {
            const params = new URLSearchParams({mode:'workspace', include_ancestors:'true'});
            if (filters.root !== 'all') params.set('root', filters.root);
            const data = await api.get(`/prompt_manager/gallery/subfolders?${params}`, {signal:request.signal});
            if (scope.signal.aborted || request !== optionsRequest) return false;
            source.replaceChildren(new Option('All sources', 'all'), ...data.roots.map(root => new Option(root.label, String(root.index))));
            source.value = filters.root;
            folder.replaceChildren(new Option('All folders', ''), ...data.subfolders.map(path => new Option(path, path)));
            folder.value = filters.subfolder;
            optionsStatus.hidden = true;
            return true;
        } catch {
            if (scope.signal.aborted || request !== optionsRequest) return false;
            optionsStatus.textContent = recovering ? 'Sources and folders could not be loaded. Refresh image collection to retry.' : 'Sources and folders could not be loaded. Refresh to retry.';
            optionsStatus.hidden = false;
            return false;
        }
    }
    scope.cleanup(() => optionsRequest?.abort());
    scope.listen(search, 'input', scope.debounce(() => { if (!recovering) { filters.q = search.value.trim(); reset(); } }, 200));
    for (const [input, key] of [[source, 'root'], [folder, 'subfolder'], [media, 'media'], [sort, 'sort']]) {
        scope.listen(input, 'change', () => {
            if (recovering) return;
            filters[key] = input.value;
            if (key === 'root') { filters.subfolder = ''; folder.value = ''; loadOptions(); }
            reset();
        });
    }
    if (!embedded) loadOptions();
    if (visit) {
        renderState(stream.state);
        const selected = stream.state.items.find(image => imageKey(image) === selection);
        if (selected) inspect(selected);
        restoreFrame = requestAnimationFrame(() => {
            if (!scope.signal.aborted) window.scrollTo(0, visit.scrollY);
        });
        if (visit.state.loading || visit.state.error) stream.loadMore();
    } else reset();
    return {dispose};
}
