// System › Appearance › Thumbnails (#317): the server's thumbnail format, quality and size limit as host rows of the
// one Save row, JPEG XL offered only with an encoder (D130), a note where this browser cannot show JPEG XL, and the
// explicit Convert outdated operation (never automatic).
const test = require('node:test');
const assert = require('node:assert/strict');

const thumbnailsModule = import('../../web/js/workspace/system-thumbnails.mjs');
const stateModule = import('../../web/js/workspace/system-state.mjs');
const coreModule = import('../../web/js/shared/toolkit/core.js');

/** A minimal DOM whose elements count their live listeners (a listener's signal aborts it, as in a browser). */
function fakeDOM() {
    const previous = global.document;
    const elements = [];
    class Element {
        constructor(tag) {
            this.tagName = tag.toUpperCase();
            this.children = [];
            this.attributes = {};
            this.listeners = [];
            this.dataset = {};
            this.className = '';
            this.id = '';
            this.value = '';
            this._text = '';
            elements.push(this);
        }
        get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
        set textContent(text) { this.children = []; this._text = String(text); }
        append(...nodes) {
            for (const node of nodes) {
                const child = typeof node === 'string' ? Object.assign(new Element('#text'), { _text: node }) : node;
                child.parentNode = this;
                this.children.push(child);
            }
        }
        remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(child => child !== this); this.parentNode = null; }
        replaceChildren(...nodes) { this.children = []; this._text = ''; this.append(...nodes); }
        setAttribute(name, value) { this.attributes[name] = String(value); }
        getAttribute(name) { return Object.hasOwn(this.attributes, name) ? this.attributes[name] : null; }
        removeAttribute(name) { delete this.attributes[name]; }
        addEventListener(type, callback, options = {}) {
            if (options.signal?.aborted) return;
            const entry = { type, callback };
            this.listeners.push(entry);
            options.signal?.addEventListener('abort', () => { this.listeners = this.listeners.filter(item => item !== entry); });
        }
        removeEventListener(type, callback) { this.listeners = this.listeners.filter(item => item.type !== type || item.callback !== callback); }
        dispatch(type) { for (const { callback } of this.listeners.filter(item => item.type === type)) callback({ type, target: this }); }
        click() { this.dispatch('click'); }
    }
    global.document = {
        createElement: tag => new Element(tag),
        createTextNode: text => Object.assign(new Element('#text'), { _text: String(text) }),
    };
    const walk = (node, found = []) => { found.push(node); node.children.forEach(child => walk(child, found)); return found; };
    return {
        listenerCount: () => elements.reduce((sum, element) => sum + element.listeners.length, 0),
        find: (root, predicate) => walk(root).find(predicate),
        tags: node => node.children.map(child => child.tagName.toLowerCase()),
        restore() { global.document = previous; },
    };
}

const HOST = { thumbnail_format: 'webp', thumbnail_quality: 80, thumbnail_edge: 512,
    thumbnail_encoders: { webp: { available: true }, jpeg: { available: true }, jxl: { available: true, encoder: 'cjxl', reason: null } } };
const NO_JXL = { webp: { available: true }, jpeg: { available: true },
    jxl: { available: false, encoder: null, reason: 'No JPEG XL encoder found. Install pillow-jxl-plugin.' } };

async function mounted({ host = HOST, browserJxl = true, writeHost = async () => ({ success: true, persisted: true, revision: 'sha256:two' }) } = {}) {
    const { mountThumbnailSettings } = await thumbnailsModule;
    const { createSystemDrafts } = await stateModule;
    const { createScope } = await coreModule;
    const dom = fakeDOM();
    let hostValues = null;
    const drafts = createSystemDrafts({
        readBrowser: () => ({}), readRuntime: () => ({}),
        readHost: async () => ({ values: hostValues, revision: 'sha256:one' }),
        writeBrowser: async () => ({ persisted: true }), writeHost, writeRuntime: async () => ({}),
    });
    const scope = createScope();
    const reviews = [];
    let answer;
    const support = () => new Promise(resolve => { answer = resolve; });
    const surface = mountThumbnailSettings(null, { scope, drafts, api: {}, activity: {}, support,
        openReview: options => reviews.push(options) });
    const byId = id => dom.find(surface, node => node.dataset?.setting === id);
    const notes = () => dom.find(surface, node => node.dataset?.setting === 'thumbnail-format').parentNode.children[0].children.slice(2)
        .map(node => node.textContent);
    return { dom, drafts, scope, surface, byId, reviews, notes, browser: () => answer(browserJxl),
        loadHost: async () => { hostValues = host; await drafts.load('host'); } };
}

test('a Thumbnails surface of three host rows and the Convert outdated operation', async () => {
    const { dom, surface, scope } = await mounted();
    try {
        assert.equal(surface.className, 'surface');
        assert.equal(surface.children[0].textContent, 'Thumbnails');
        const rows = surface.children.slice(2);
        assert.deepEqual(rows.map(row => row.children[0].children[0].textContent),
            ['Thumbnail format', 'Thumbnail quality', 'Thumbnail size limit', 'Convert outdated thumbnails']);
        assert.deepEqual(rows.map(row => row.className), ['setting-row', 'setting-row', 'setting-row', 'operation']);
    } finally { scope.dispose(); assert.equal(dom.listenerCount(), 0); dom.restore(); }
});

test('the rows edit the server settings once they were read', async () => {
    const { FORMAT_OPTIONS } = await thumbnailsModule;
    assert.deepEqual(FORMAT_OPTIONS, [['webp', 'WebP'], ['jxl', 'JPEG XL'], ['jpeg', 'JPEG']]);
    const { dom, drafts, scope, byId, loadHost } = await mounted();
    try {
        for (const id of ['thumbnail-format', 'thumbnail-quality', 'thumbnail-edge']) assert.equal(byId(id).disabled, true, id);
        await loadHost();
        assert.equal(byId('thumbnail-format').value, 'webp');
        assert.equal(byId('thumbnail-quality').value, '80');
        assert.equal(byId('thumbnail-edge').value, '512');
        assert.equal(byId('thumbnail-format').disabled, false);
        byId('thumbnail-format').value = 'jpeg'; byId('thumbnail-format').dispatch('change');
        byId('thumbnail-quality').value = '60'; byId('thumbnail-quality').dispatch('change');
        byId('thumbnail-edge').value = '300'; byId('thumbnail-edge').dispatch('change');
        assert.deepEqual(drafts.changes('host'), { thumbnail_format: 'jpeg', thumbnail_quality: 60, thumbnail_edge: 300 });
    } finally { scope.dispose(); dom.restore(); }
});

test('quality takes 1–100 and the size limit 128–2048 whole numbers only', async () => {
    const { wholeNumber, QUALITY_LIMIT, EDGE_LIMIT } = await thumbnailsModule;
    assert.deepEqual(['1', ' 100 ', '0', '101', '80.5', '', 'x'].map(text => wholeNumber(text, QUALITY_LIMIT)), [1, 100, null, null, null, null, null]);
    assert.deepEqual(['128', '2048', '127', '2049', '1e3'].map(text => wholeNumber(text, EDGE_LIMIT)), [128, 2048, null, null, null]);
    const { dom, drafts, scope, byId, loadHost } = await mounted();
    try {
        await loadHost();
        byId('thumbnail-quality').value = '0'; byId('thumbnail-quality').dispatch('change');
        byId('thumbnail-edge').value = '4096'; byId('thumbnail-edge').dispatch('change');
        assert.deepEqual(drafts.changes('host'), {});
        assert.deepEqual([...drafts.failed('host')].sort(), ['thumbnail_edge', 'thumbnail_quality']);
    } finally { scope.dispose(); dom.restore(); }
});

test('JPEG XL is disabled with the server reason when no encoder is installed (D130)', async () => {
    const { dom, scope, byId, loadHost, notes } = await mounted({ host: { ...HOST, thumbnail_encoders: NO_JXL } });
    try {
        await loadHost();
        const jxl = byId('thumbnail-format').children.find(option => option.value === 'jxl');
        assert.equal(jxl.disabled, true);
        assert.deepEqual(notes(), [NO_JXL.jxl.reason]);
    } finally { scope.dispose(); dom.restore(); }
    const ready = await mounted();
    try {
        await ready.loadHost();
        assert.equal(ready.byId('thumbnail-format').children.find(option => option.value === 'jxl').disabled, false);
        assert.deepEqual(ready.notes(), []);
    } finally { ready.scope.dispose(); ready.dom.restore(); }
});

test('a browser that cannot show JPEG XL says the originals are shown instead', async () => {
    const { BROWSER_NO_JXL } = await thumbnailsModule;
    const { dom, scope, loadHost, notes, browser } = await mounted({ browserJxl: false });
    try {
        await loadHost();
        browser();
        await new Promise(resolve => setImmediate(resolve));
        assert.deepEqual(notes(), [BROWSER_NO_JXL]);
    } finally { scope.dispose(); dom.restore(); }
});

test('Convert outdated opens Thumbnail maintenance on its convert scope; a format save says previews keep theirs', async () => {
    const { FORMAT_SAVED, CONVERT_DESCRIPTION } = await thumbnailsModule;
    const { dom, drafts, scope, surface, byId, reviews, loadHost } = await mounted();
    try {
        const operation = surface.children.at(-1);
        assert.equal(operation.children[0].children[1].textContent, CONVERT_DESCRIPTION);
        dom.find(operation, node => node.tagName === 'BUTTON').click();
        assert.equal(reviews.length, 1);
        assert.equal(reviews[0].mode, 'convert');
        await loadHost();
        byId('thumbnail-quality').value = '70'; byId('thumbnail-quality').dispatch('change');
        await drafts.save();
        assert.equal(operation.children[0].children[1].textContent, CONVERT_DESCRIPTION, 'quality alone converts nothing');
        byId('thumbnail-format').value = 'jpeg'; byId('thumbnail-format').dispatch('change');
        await drafts.save();
        assert.equal(operation.children[0].children[1].textContent, FORMAT_SAVED);
        assert.equal(reviews.length, 1, 'nothing starts by itself');
    } finally { scope.dispose(); assert.equal(dom.listenerCount(), 0); dom.restore(); }
});
