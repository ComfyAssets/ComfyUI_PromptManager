// System › Appearance (#249 System S3 Task 8): the concept's "Browsing & display" rows (app.js 676–678) on the drafts
// of the one Save row, their stored values (Q3 mappings), the host timeout's 0–300 whole seconds and the D97 rows.
const test = require('node:test');
const assert = require('node:assert/strict');
const gallerySettings = require('../../web/js/gallery-settings.js');

const appearanceModule = import('../../web/js/workspace/system-appearance.mjs');
const stateModule = import('../../web/js/workspace/system-state.mjs');
const coreModule = import('../../web/js/shared/toolkit/core.js');
globalThis.GallerySettings = gallerySettings;

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

const BROWSER = { workspaceLayout: 'grid', workspaceDensity: 'comfortable', autoLoadMetadata: true, cacheMetadata: true,
    imageQuality: 'medium', videoAutoplay: false, videoMute: true, videoLoop: true, showFilePaths: true, showImageInfo: true,
    sidebarCollapsedByDefault: false };

async function mounted({ host = { webui_display_mode: 'newtab', result_timeout: 5 }, browser = BROWSER } = {}) {
    const { mountAppearance } = await appearanceModule;
    const { createSystemDrafts } = await stateModule;
    const { createScope } = await coreModule;
    const dom = fakeDOM();
    let hostValues = null;
    const drafts = createSystemDrafts({
        readBrowser: () => ({ ...browser }), readRuntime: () => ({}),
        readHost: async () => ({ values: hostValues, revision: 'sha256:one' }),
        writeBrowser: async () => ({ persisted: true }), writeHost: async () => ({ success: true, persisted: true }), writeRuntime: async () => ({}),
    });
    await drafts.load('browser');
    const scope = createScope();
    const [surface] = mountAppearance(null, { scope, drafts });
    const byId = id => dom.find(surface, node => node.dataset?.setting === id);
    return { dom, drafts, scope, surface, byId, loadHost: async () => { hostValues = host; await drafts.load('host'); } };
}

test('the surface is the concept section with the concept rows in order, then the D97 rows', async () => {
    const { APPEARANCE_ROWS } = await appearanceModule;
    const { dom, surface, scope } = await mounted();
    try {
        assert.equal(surface.className, 'surface');
        assert.equal(surface.children[0].textContent, 'Browsing & display');
        assert.equal(surface.children[1].textContent, 'One set of defaults; toolbar changes apply to the current view.');
        const rows = surface.children.slice(2);
        const titles = rows.map(row => row.children[0].children[0].textContent);
        assert.deepEqual(titles, ['Default layout', 'Thumbnail size', 'Continuous browsing', 'Load metadata on selection',
            'Cache metadata in this browser', 'Image loading quality', 'Autoplay videos', 'Mute videos by default', 'Loop videos',
            'Show file paths', 'Open from ComfyUI', 'Node search auto-hide (seconds)', 'Show image details on cards',
            'Collapse viewer details by default', 'Reset display defaults']);
        const continuous = rows[2];
        assert.deepEqual(dom.tags(continuous.children[0]), ['h3', 'p']);
        assert.equal(continuous.children[1].className, 'chip blue');
        assert.equal(continuous.children[1].textContent, 'Infinite scroll');
        assert.equal(rows.at(-1).className, 'operation');
        assert.equal(APPEARANCE_ROWS.filter(Array.isArray).length, 13);
    } finally { scope.dispose(); assert.equal(dom.listenerCount(), 0); dom.restore(); }
});

test('added rows reach their consumers: each control edits its stored key with the Q3 values', async () => {
    const { dom, drafts, scope, byId, loadHost } = await mounted();
    try {
        // Host rows stay disabled until the server settings answered.
        assert.equal(byId('open-mode').disabled, true);
        assert.equal(byId('timeout').disabled, true);
        await loadHost();
        assert.equal(byId('open-mode').disabled, false);
        assert.equal(byId('timeout').value, '5');
        assert.deepEqual(byId('image-quality').children.map(option => [option.value, option.textContent]),
            [['medium', 'Optimized'], ['high', 'Original'], ['low', 'Fast']]);
        assert.deepEqual(byId('open-mode').children.map(option => [option.value, option.textContent]),
            [['newtab', 'New tab'], ['popup', 'Popup window']]);
        const edits = [['default-view', 'masonry'], ['density', 'large'], ['image-quality', 'low'], ['open-mode', 'popup']];
        for (const [id, value] of edits) { byId(id).value = value; byId(id).dispatch('change'); }
        for (const id of ['metadata-auto', 'metadata-cache', 'video-muted', 'video-loop', 'paths', 'card-details']) {
            byId(id).checked = false; byId(id).dispatch('change');
        }
        for (const id of ['video-auto', 'viewer-details']) { byId(id).checked = true; byId(id).dispatch('change'); }
        assert.deepEqual(drafts.changes('browser'), { workspaceLayout: 'masonry', workspaceDensity: 'large', imageQuality: 'low',
            autoLoadMetadata: false, cacheMetadata: false, videoMute: false, videoLoop: false, showFilePaths: false, showImageInfo: false,
            videoAutoplay: true, sidebarCollapsedByDefault: true });
        assert.deepEqual(drafts.changes('host'), { webui_display_mode: 'popup' });
    } finally { scope.dispose(); dom.restore(); }
});

test('the auto-hide timeout takes whole seconds from 0 to 300 only', async () => {
    const { resultTimeout } = await appearanceModule;
    assert.deepEqual(['0', '30', ' 300 ', '301', '-1', '2.5', '', 'abc', '1e2'].map(resultTimeout), [0, 30, 300, null, null, null, null, null, null]);
    const { dom, drafts, scope, byId, loadHost } = await mounted();
    try {
        await loadHost();
        const timeout = byId('timeout');
        timeout.value = '45'; timeout.dispatch('change');
        assert.deepEqual(drafts.changes('host'), { result_timeout: 45 });
        timeout.value = '301'; timeout.dispatch('change');
        assert.deepEqual(drafts.changes('host'), {});
        assert.deepEqual([...drafts.failed('host')], ['result_timeout']);
        timeout.value = '0'; timeout.dispatch('change');
        assert.deepEqual(drafts.changes('host'), { result_timeout: 0 });
        assert.deepEqual([...drafts.failed('host')], []);
    } finally { scope.dispose(); dom.restore(); }
});

test('reset restores defaults without touching other keys', async () => {
    const { DISPLAY_KEYS } = await appearanceModule;
    const changed = { ...BROWSER, workspaceLayout: 'list', videoMute: false, showImageInfo: false, sidebarCollapsedByDefault: true };
    const { dom, drafts, scope, surface, loadHost } = await mounted({ browser: changed });
    try {
        await loadHost();
        drafts.set('host', 'result_timeout', 9);
        const reset = dom.find(surface.children.at(-1), node => node.tagName === 'BUTTON');
        assert.equal(reset.textContent, 'Reset');
        reset.click();
        assert.deepEqual(drafts.changes('browser'), { workspaceLayout: 'grid', videoMute: true, showImageInfo: true, sidebarCollapsedByDefault: false });
        // The host fields and every key outside the display defaults are left as they are.
        assert.deepEqual(drafts.changes('host'), { result_timeout: 9 });
        assert.deepEqual([...DISPLAY_KEYS].sort(), Object.keys(BROWSER).sort());
    } finally { scope.dispose(); dom.restore(); }
});
