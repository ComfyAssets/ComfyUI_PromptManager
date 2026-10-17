import { createScope, createButton, createField, createSelect } from '../shared/toolkit/core.js';
import { createImageStream, imageKey } from './images-state.mjs';
import { layoutImages, visibleImages } from './image-layout.mjs';
import { mountPromptInspector } from './prompt-inspector.mjs';
import { createPromptSender } from './prompt-sender.mjs';

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
    let detailInspector, previewInspector;
    scope.cleanup(() => { detailInspector?.dispose(); previewInspector?.dispose(); });
    const visit = embedded ? null : lastVisit;
    let layout = visit?.layout || 'grid', selection = visit?.selection || null, frame = 0, loadFrame = 0, restoreFrame = 0, observer, viewer;
    let state, geometry = { positions: [], height: 0, maxHeight: 0 }, optionsRequest;
    const rendered = new Map();
    const itemIndexes = new Map();
    const filters = { root: 'all', q: '', subfolder: '', media: 'all', sort: 'newest', ...visit?.filters };
    scope.cleanup(() => {
        if (!embedded) lastVisit = state ? { filters: {...filters}, state, layout, selection, scrollY: window.scrollY } : null;
        cancelAnimationFrame(restoreFrame);
    });
    main.classList.add('images-main');
    scope.cleanup(() => main.classList.remove('images-main'));
    if (!embedded) main.append(el('p', 'Browse your images, explore the details, keep your creative context.', 'intro'));
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
    const toolbar = el('div', undefined, 'image-toolbar');
    const count = el('p', 'Loading images…', 'muted');
    const layouts = el('div', undefined, 'image-layouts');
    layouts.setAttribute('role', 'group'); layouts.setAttribute('aria-label', 'Image layout');
    for (const mode of ['grid', 'list', 'masonry']) {
        const button = createButton(mode[0].toUpperCase() + mode.slice(1));
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
            await loadOptions();
            if (!scope.signal.aborted) reset();
        } catch {
            if (scope.signal.aborted) return;
            optionsStatus.textContent = 'Images could not be refreshed. Try Refresh again.';
            optionsStatus.hidden = false;
        } finally {
            refresh.disabled = false;
        }
    });
    const currentGallery = el('a', 'Current gallery', 'workspace-link');
    currentGallery.href = resolveUrl('/prompt_manager/gallery');
    toolbar.append(count, layouts);
    if (!embedded) toolbar.append(refresh, currentGallery);
    main.append(toolbar);
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
    collection.append(grid, continuation);
    const details = el('section', undefined, 'image-details panel');
    details.setAttribute('aria-label', 'Image details');
    details.append(el('h2', 'Image details'), el('p', 'Select an image to inspect it.'));
    body.append(collection, details);
    main.append(body);
    const dialog = el('dialog', undefined, 'image-preview');
    dialog.setAttribute('aria-label', 'Image preview');
    const dialogHeader = el('header');
    const previewTitle = el('h2');
    const close = createButton('Close preview');
    dialogHeader.append(previewTitle, close);
    const stage = el('div', undefined, 'image-preview-stage');
    const previewBody = el('div', undefined, 'image-preview-body');
    const previewDetails = el('aside', undefined, 'image-preview-details');
    previewDetails.setAttribute('aria-label', 'Preview prompt details');
    previewBody.append(stage, previewDetails);
    dialog.append(dialogHeader, previewBody);
    main.append(dialog);
    scope.listen(close, 'click', () => dialog.close());
    scope.listen(dialog, 'close', () => { destroyPreview(); scheduleLoad(); });
    function destroyPreview() {
        previewInspector?.dispose(); previewInspector = null;
        previewDetails.replaceChildren();
        viewer?.destroy(); viewer = null;
        for (const video of stage.querySelectorAll('video')) releaseVideo(video);
        stage.replaceChildren();
    }
    scope.cleanup(() => { dialog.close(); destroyPreview(); dialog.remove(); });

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
        const thumbnail = mediaElement(image);
        const metadata = el('dl', undefined, 'image-facts');
        const fields = [['Source', image.root_dir], ['Path', image.path], ['Size', formatBytes(image.size)],
            ['Dimensions', image.width && image.height ? `${image.width} × ${image.height}` : 'Not available'],
            ['Modified', new Date(image.modified_time * 1000).toLocaleString()]];
        for (const [label, value] of fields) {
            const row = el('div'); row.append(el('dt', label), el('dd', value)); metadata.append(row);
        }
        const preview = createButton('Open full preview', 'ca-primary');
        // The view delegates these controls so changing selection retains no old handlers.
        preview.dataset.previewKey = selection;
        const download = el('a', 'Download original', 'workspace-link');
        download.href = resolveUrl(image.url); download.download = image.filename;
        const prompts = el('div');
        const legacy = el('p', 'Open Current gallery for tagging.', 'muted');
        legacy.hidden = embedded;
        details.replaceChildren(el('p', 'SELECTED IMAGE', 'eyebrow'), el('h2', image.filename), thumbnail, preview, download, prompts, metadata, legacy);
        detailInspector = mountPromptInspector(prompts, {api, image, sender, parentScope:scope});
        for (const [key, card] of rendered) {
            card.classList.toggle('selected', key === selection);
            card.querySelector('.image-open').setAttribute('aria-pressed', String(key === selection));
        }
    }
    scope.listen(details, 'click', event => {
        const key = event.target.closest('[data-preview-key]')?.dataset.previewKey;
        if (key) previewImage(state.items.find(image => imageKey(image) === key));
    });
    function previewImage(image) {
        if (!image) return;
        if (selection !== imageKey(image)) inspect(image);
        destroyPreview();
        previewTitle.textContent = image.filename;
        previewInspector = mountPromptInspector(previewDetails, {api, image, sender, parentScope:scope});
        if (!dialog.open) dialog.showModal();
        if (image.is_video) {
            const video = el('video'); video.controls = true; video.src = resolveUrl(image.url); video.preload = 'metadata';
            stage.append(video);
        } else if (window.Viewer) {
            const original = el('img'); original.src = resolveUrl(image.url); original.alt = image.filename;
            original.hidden = true; stage.append(original);
            viewer = new window.Viewer(original, { inline: true, container: stage, navbar: false, title: false,
                transition: false, keyboard: false, viewed() { viewer?.resize(); } });
        } else {
            stage.append(el('p', 'The image viewer could not load. Refresh the page to try again.'));
        }
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
        const sub = el('small', `${formatBytes(image.size)} · ${image.media_type === 'video' ? 'Video' : 'Image'}`);
        const preview = createButton('↗', 'ca-icon-button');
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
        const top = Math.max(0, -grid.getBoundingClientRect().top);
        const active = document.activeElement?.closest('article[data-image-key]')?.dataset.imageKey;
        const focused = active ? itemIndexes.get(active) ?? -1 : -1;
        const indexes = state.items.length <= 120 ? state.items.map((_, index) => index) :
            visibleImages(geometry.positions, top, window.innerHeight, focused, geometry.maxHeight);
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
        const columns = layout === 'list' ? 1 : Math.max(1, Math.floor((grid.clientWidth + 16) / 256));
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
    function updateLayout() {
        if (!state || scope.signal.aborted) return;
        grid.dataset.layout = layout;
        cancelAnimationFrame(frame); frame = 0;
        geometry = layoutImages(state.items, grid.clientWidth, layout);
        grid.style.height = `${geometry.height}px`;
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
    scope.cleanup(() => { resize.disconnect(); observer?.disconnect(); cancelAnimationFrame(resizeFrame); cancelAnimationFrame(frame); cancelAnimationFrame(loadFrame); });
    function renderState(next) {
        const changed = state?.items !== next.items;
        if (changed) {
            if (!next.items.length) itemIndexes.clear();
            for (let index = state?.items.length || 0; index < next.items.length; index++) itemIndexes.set(imageKey(next.items[index]), index);
        }
        state = next;
        count.textContent = embedded ? `${next.loaded.toLocaleString()} loaded · ${next.total.toLocaleString()} linked images` : `${next.loaded.toLocaleString()} of ${next.total.toLocaleString()} images`;
        if (changed) updateLayout();
        message.textContent = next.error ? 'Images could not be loaded. Retry or refresh the collection.' : next.loading ? 'Loading images…' : '';
        if (next.error) message.setAttribute('role', 'alert'); else message.setAttribute('role', 'status');
        retry.hidden = !next.error;
        more.hidden = !next.hasMore || !!next.error;
        more.setAttribute('aria-disabled', String(next.loading));
        end.textContent = !next.loading && !next.error && !next.hasMore ? (next.loaded ? 'You’re all caught up.' : embedded ? 'No linked media available for this selection.' : 'No matching images or media. Try changing the filters.') : '';
        if (!selection && next.items.length) inspect(next.items[0]);
        scheduleLoad();
    }
    const stream = createImageStream({ api, fetchPage, onChange: renderState, restore: visit ? {filters, state:visit.state} : undefined });
    scope.cleanup(() => {
        stream.dispose();
        for (const video of body.querySelectorAll('video')) releaseVideo(video);
    });
    scope.listen(more, 'click', () => stream.loadMore());
    scope.listen(retry, 'click', () => stream.loadMore());
    function maybeLoad() {
        if (!observer || scope.signal.aborted || !state?.hasMore || state.loading || state.error || dialog.open) return;
        const bounds = continuation.getBoundingClientRect();
        if (bounds.top <= window.innerHeight + 250 && bounds.bottom >= 0) stream.loadMore();
    }
    function scheduleLoad() {
        if (!loadFrame) loadFrame = requestAnimationFrame(() => { loadFrame = 0; maybeLoad(); });
    }
    if (typeof IntersectionObserver === 'function') {
        observer = new IntersectionObserver(entries => {
            if (entries.some(entry => entry.isIntersecting)) maybeLoad();
        }, { rootMargin: '250px' });
        observer.observe(continuation);
    }
    function reset() {
        detailInspector?.dispose(); detailInspector = null;
        selection = null;
        for (const video of details.querySelectorAll('video')) releaseVideo(video);
        details.replaceChildren(el('h2', 'Image details'), el('p', 'Select an image to inspect it.'));
        stream.reset(filters);
    }
    async function loadOptions() {
        optionsRequest?.abort();
        const request = new AbortController(); optionsRequest = request;
        try {
            const params = new URLSearchParams({mode:'workspace', include_ancestors:'true'});
            if (filters.root !== 'all') params.set('root', filters.root);
            const data = await api.get(`/prompt_manager/gallery/subfolders?${params}`, {signal:request.signal});
            if (scope.signal.aborted || request !== optionsRequest) return;
            source.replaceChildren(new Option('All sources', 'all'), ...data.roots.map(root => new Option(root.label, String(root.index))));
            source.value = filters.root;
            folder.replaceChildren(new Option('All folders', ''), ...data.subfolders.map(path => new Option(path, path)));
            folder.value = filters.subfolder;
            optionsStatus.hidden = true;
        } catch {
            if (scope.signal.aborted || request !== optionsRequest) return;
            optionsStatus.textContent = 'Sources and folders could not be loaded. Refresh to retry.';
            optionsStatus.hidden = false;
        }
    }
    scope.cleanup(() => optionsRequest?.abort());
    scope.listen(search, 'input', scope.debounce(() => { filters.q = search.value.trim(); reset(); }, 200));
    for (const [input, key] of [[source, 'root'], [folder, 'subfolder'], [media, 'media'], [sort, 'sort']]) {
        scope.listen(input, 'change', () => {
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
