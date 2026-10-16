const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const { escapeHtml } = require('../../web/js/html-escape.js');

function fixture(count = 2000) {
    const elements = new Map();
    function element() {
        return {
            dataset: {}, children: [], listeners: {}, classList: { add() {}, remove() {} },
            set innerHTML(html) { this.html = html; this.children = [{ html }]; },
            get innerHTML() { return this.html || ''; },
            insertAdjacentHTML(position, html) { this.children.push({ html }); },
            addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); },
            querySelectorAll() { return []; },
            remove() {},
        };
    }
    const document = {
        addEventListener() {},
        getElementById(id) { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); },
        createElement: element,
    };
    const instances = [];
    const timers = new Map();
    let timerId = 0;
    class Viewer {
        constructor(holder, options) {
            this.element = holder; this.options = { fullscreen: true, ...options };
            this.index = options.initialViewIndex; this.isShown = true;
            this.length = (holder.innerHTML.match(/<img /g) || []).length;
            instances.push(this);
        }
        requestFullscreen() { this.fullscreenRequested = true; }
        exitFullscreen() { this.fullscreenExited = true; }
        show() { this.shown = true; }
        view(index) { this.index = index; }
        clearEnforceFocus() { this.focusCleared = true; }
        destroy() { this.destroyed = true; }
    }
    const context = vm.createContext({ document, window: {}, Viewer, escapeHtml,
        console, setTimeout(callback) { timers.set(++timerId, callback); return timerId; },
        clearTimeout(id) { timers.delete(id); },
        ApiClient: { resolveUrl: value => value, STALE: Symbol() },
        ListState: { nextPageState: (state, next) => ({ ...state, ...next }) },
    });
    const source = fs.readFileSync(path.join(__dirname, '../../web/js/gallery.js'), 'utf8');
    const Manager = vm.runInContext(source + '\nGalleryManager;', context, { filename: path.join(__dirname, '../../web/js/gallery.js') });
    const manager = Object.create(Manager.prototype);
    Object.assign(manager, { images: Array.from({ length: count }, (_, i) => ({
        filename: `${i}.png`, url: `/original/${i}.png`, thumbnail_url: `/thumb/${i}.jpg`,
        relative_path: `${i}.png`, size: 100, modified_time: 1,
    })), viewMode: 'grid', viewer: null, currentPage: 1, limit: 100, total: count,
    getSettings: () => ({ showImageInfo: true }), });
    return { manager, document, instances, timers };
}

for (const mode of ['grid', 'list']) {
    test(`${mode} append retains existing cards and adds only the new page`, () => {
        const { manager, document, timers } = fixture(100);
        manager.viewMode = mode;
        manager.renderGallery();
        const container = document.getElementById(mode === 'grid' ? 'galleryGrid' : 'galleryList');
        assert.equal(timers.size, 0);
        const first = container.children[0];
        manager.images.push({ ...manager.images[0], filename: 'new.png' });
        manager.renderGallery({ from: 100 });
        assert.equal(container.children[0], first);
        assert.equal(container.children.length, 2);
        assert.match(container.children[1].html, /new.png/);
        assert.doesNotMatch(container.children[1].html, /alt="0.png"/);
        assert.match(container.children[1].html, /data-image-index="100"/);
    });
}

test('loadImages passes the previous length for append, and zero for replacement', async () => {
    const { manager } = fixture(200);
    const offsets = [];
    Object.assign(manager, { imagesRequest: async () => ({ total: 500, images: [manager.images[0]] }),
        renderGallery: options => offsets.push(options.from), updateStats() {},
        updatePagination() {}, maybeLoadMore() {}, showLoading() {}, });
    await manager.loadImages(3, { append: true });
    await manager.loadImages(1);
    assert.deepEqual(offsets, [200, 0]);
    assert.equal(manager.images.length, 1);
});

test('viewer opening is bounded and selects the clicked original', () => {
    const { manager, instances } = fixture();
    manager.openViewer(1500);
    const viewer = instances[0];
    assert.ok(viewer.element.innerHTML.match(/<img /g).length <= 100);
    assert.match(viewer.element.innerHTML, /data-original="\/original\/1500.png"/);
    const urls = [...viewer.element.innerHTML.matchAll(/data-original="([^"]+)"/g)].map(m => m[1]);
    assert.equal(urls[viewer.options.initialViewIndex], '/original/1500.png');
    assert.equal(viewer.shown, true);
});

test('previous/next cross window edges and wrap only at gallery edges', () => {
    const { manager, instances } = fixture();
    manager.openViewer(1500);
    const first = manager.viewer;
    first.index = 99;
    const lastUrl = [...first.element.innerHTML.matchAll(/data-original="\/original\/(\d+).png"/g)].at(-1);
    first.next(true);
    const next = manager.viewer;
    const urls = [...next.element.innerHTML.matchAll(/data-original="\/original\/(\d+).png"/g)];
    assert.equal(Number(urls[next.index][1]), Number(lastUrl[1]) + 1);
    manager.openViewer(0);
    manager.viewer.prev(true);
    const wrapped = manager.viewer;
    const wrappedUrls = [...wrapped.element.innerHTML.matchAll(/data-original="\/original\/(\d+).png"/g)];
    assert.equal(Number(wrappedUrls[wrapped.index][1]), 1999);
    assert.ok(instances.length >= 3);
});

test('delegated card clicks bind once, open appended indices, and preserve video dispatch', () => {
    const { manager, document } = fixture();
    const opened = [];
    manager.openViewer = index => opened.push(index);
    manager.showVideoModal = (...args) => opened.push(args);
    manager.addFallbackClickHandlers();
    manager.addFallbackClickHandlers();
    const grid = document.getElementById('galleryGrid');
    assert.equal(grid.listeners.click.length, 1);
    const item = { dataset: { imageIndex: '1500' }, querySelector: () => ({ dataset: {
        original: '/original/1500.png', caption: 'caption', isVideo: 'false',
    } }) };
    const click = () => grid.listeners.click[0]({ target: { closest: () => item }, preventDefault() {}, stopPropagation() {} });
    click();
    item.querySelector = () => ({ dataset: { original: '/video.mp4', caption: 'video', isVideo: 'true' } });
    click();
    assert.deepEqual(opened, [1500, ['/video.mp4', 'video', 1500]]);
});

test('navigation inside the window reuses the viewer and honors non-looping edges', () => {
    const { manager, instances } = fixture();
    manager.openViewer(1500);
    const viewer = manager.viewer;
    viewer.next(true);
    assert.equal(manager.viewer, viewer);
    assert.equal(viewer.index, 51);
    viewer.prev(true);
    assert.equal(viewer.index, 50);
    manager.openViewer(0);
    const edge = manager.viewer;
    edge.prev(false);
    assert.equal(manager.viewer, edge);
    assert.equal(edge.index, 0);
    assert.equal(instances.length, 2);
});

test('append leaves an open viewer intact; a full render destroys it', () => {
    const { manager } = fixture();
    manager.openViewer(1500);
    const viewer = manager.viewer;
    manager.renderGallery({ from: 1900 });
    assert.equal(manager.viewer, viewer);
    manager.renderGallery();
    assert.equal(viewer.destroyed, true);
    assert.equal(manager.viewer, null);
});

test('each viewed image updates sidebar metadata without waiting for DOM mutations', () => {
    const { manager, document } = fixture();
    const urls = [];
    document.querySelector = () => ({});
    const sidebar = document.getElementById('metadata-sidebar');
    sidebar.querySelector = () => ({});
    manager.loadImageMetadata = url => urls.push(url);
    manager.openViewer(1500);
    manager.viewer.options.viewed({ detail: { originalImage: { dataset: { original: '/original/1500.png' } } } });
    manager.viewer.options.viewed({ detail: { originalImage: { dataset: { original: '/original/1501.png' } } } });
    assert.deepEqual(urls, ['/original/1500.png', '/original/1501.png']);
});

test('slideshow advances across windows without preloading every original and stops on hide', () => {
    const { manager, timers } = fixture();
    manager.openViewer(1500);
    let prevented = false;
    manager.viewer.options.play({ preventDefault() { prevented = true; } });
    assert.equal(prevented, true);
    assert.equal(manager.viewer.fullscreenRequested, true);
    assert.equal(timers.size, 1);
    manager.viewer.index = 99;
    const tick = timers.values().next().value;
    timers.clear();
    tick();
    const viewer = manager.viewer;
    const urls = [...viewer.element.innerHTML.matchAll(/data-original="([^"]+)"/g)];
    assert.equal(urls[viewer.index][1], '/original/1550.png');
    assert.equal(timers.size, 1);
    viewer.options.hide();
    assert.equal(viewer.fullscreenExited, true);
    assert.equal(timers.size, 0);
});

test('destroying a viewer removes its focus trap and slideshow timer', () => {
    const { manager, timers } = fixture();
    manager.openViewer(1500);
    const viewer = manager.viewer;
    viewer.options.play({ preventDefault() {} });
    manager.destroyViewer();
    assert.equal(viewer.focusCleared, true);
    assert.equal(viewer.destroyed, true);
    assert.equal(timers.size, 0);
});
