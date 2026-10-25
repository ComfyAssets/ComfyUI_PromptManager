// The legacy admin and gallery pages show a server refusal's reason (#220, PR #289 review I1 and M8): a job that
// runs in the workspace refuses their record-changing requests, and a corrupt config.json refuses a settings save.
// Each page must name the reason, not report a lost connection, a bare HTTP status or a success.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function elements() {
    const nodes = {};
    return {
        nodes,
        getElementById: id => nodes[id] ||= { id, textContent: '', innerHTML: '', disabled: false, style: {},
            value: '', checked: false, addEventListener() {}, querySelectorAll: () => [] },
    };
}

function jsonResponse(status, body) {
    return { ok: status >= 200 && status < 300, status, statusText: status === 409 ? 'Conflict' : 'OK',
        json: async () => body };
}

function sseResponse(frames) {
    const bytes = new TextEncoder().encode(frames.map(frame => `data: ${JSON.stringify(frame)}\n\n`).join(''));
    let sent = false;
    return { ok: true, status: 200, body: { getReader: () => ({
        read: async () => (sent ? { done: true } : (sent = true, { done: false, value: bytes })),
    }) } };
}

function load(file, className, fetchUrl, extra = {}, dom = elements()) {
    const timers = [];
    const notifications = [];
    const storage = new Map([['pm_cached', '1'], ['userData', 'keep'], ['pm_workspace_theme', 'light'],
        ['pm_update_notice_dismissed', '3.3.0']]);
    const localStorage = {
        get length() { return storage.size; },
        key: index => [...storage.keys()][index] ?? null,
        getItem: key => storage.get(key) ?? null,
        setItem: (key, value) => storage.set(key, String(value)),
        removeItem: key => storage.delete(key),
    };
    const context = vm.createContext({
        document: { readyState: 'loading', addEventListener() {}, getElementById: dom.getElementById,
            querySelectorAll: () => [] },
        window: {}, console: { error() {}, warn() {}, debug() {} },
        setTimeout: callback => timers.push(callback),
        confirm: () => true,
        localStorage,
        TextDecoder, TextEncoder, JSON,
        ApiClient: { fetchUrl },
        ...extra,
    });
    // gallery.html loads cache-ownership.js before gallery.js (#222).
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../web/js/cache-ownership.js'), 'utf8'), context);
    const source = fs.readFileSync(path.join(__dirname, `../../web/js/${file}`), 'utf8');
    const Page = vm.runInContext(`${source}\n${className};`, context, { filename: path.join(__dirname, `../../web/js/${file}`) });
    const page = Object.create(Page.prototype);
    page.showNotification = (message, type) => notifications.push([type, message]);
    page.showModal = id => notifications.push(['show', id]);
    page.hideModal = id => notifications.push(['hide', id]);
    return { page, dom, timers, notifications, storage };
}

const BUSY = 'Scan for metadata already running';

test('gallery cache clear reports a refused server clear and never claims success', async () => {
    const calls = [];
    const { page, dom, notifications, storage } = load('gallery.js', 'GalleryManager', async url => {
        calls.push(url);
        return jsonResponse(409, { success: false, error: BUSY });
    });
    page.getSettings = () => ({ thumbnailsGenerated: true });
    dom.getElementById('thumbnailStatus').textContent = 'Generated';

    await page.clearCache();

    assert.deepEqual(calls, ['/prompt_manager/images/clear-thumbnails']);
    assert.deepEqual(notifications, [['error', `Thumbnails were not cleared: ${BUSY}`]]);
    assert.equal(dom.nodes.thumbnailStatus.textContent, 'Generated');
    assert.equal(storage.get('gallerySettings'), undefined); // thumbnailsGenerated is not reset
});

test('gallery cache clear says what it cleared and keeps preferences and other data (#222)', async () => {
    const calls = [];
    const { page, dom, notifications, storage } = load('gallery.js', 'GalleryManager', async url => {
        calls.push(url);
        return jsonResponse(200, { success: true, cleared_count: 3 });
    });
    page.getSettings = () => ({ thumbnailsGenerated: true });

    await page.clearCache();

    // The server's thumbnails are cleared by their own explicit call; nothing Prompt Manager owns is cached in this
    // browser, so no browser entry is removed and the toast says exactly that.
    assert.deepEqual(calls, ['/prompt_manager/images/clear-thumbnails']);
    assert.deepEqual(notifications, [['success',
        "Thumbnails cleared; original images untouched. Nothing of Prompt Manager's is cached in this browser."]]);
    assert.equal(dom.nodes.thumbnailStatus.textContent, 'Not generated');
    assert.equal(JSON.parse(storage.get('gallerySettings')).thumbnailsGenerated, false);
    assert.equal(storage.get('userData'), 'keep');
    assert.equal(storage.get('pm_cached'), '1');
    assert.equal(storage.get('pm_workspace_theme'), 'light');
    assert.equal(storage.get('pm_update_notice_dismissed'), '3.3.0');
});

test("gallery cache clear never deletes another tool's gallery or thumbnail caches (#222)", async () => {
    const names = new Set(['comfy-gallery', 'thumbnail-cache']);
    const caches = { keys: async () => [...names], delete: async name => names.delete(name) };
    const { page, notifications } = load('gallery.js', 'GalleryManager',
        async () => jsonResponse(200, { success: true }), { caches, window: { caches } });
    page.getSettings = () => ({});

    await page.clearCache();

    assert.deepEqual([...names], ['comfy-gallery', 'thumbnail-cache']);
    assert.equal(notifications.at(-1)[0], 'success');
});

test('gallery cache clear reports a lost server clear instead of success', async () => {
    const { page, dom, notifications, storage } = load('gallery.js', 'GalleryManager', async () => {
        throw new TypeError('Failed to fetch');
    });
    page.getSettings = () => ({ thumbnailsGenerated: true });
    dom.getElementById('thumbnailStatus').textContent = 'Generated';

    await page.clearCache();

    assert.deepEqual(notifications, [['error', 'Thumbnails were not cleared: the server could not be reached.']]);
    assert.equal(dom.nodes.thumbnailStatus.textContent, 'Generated');
    assert.equal(storage.get('gallerySettings'), undefined);
});

test('gallery cache clear says so when browser storage is denied', async () => {
    const denied = { get length() { throw new Error('SecurityError'); }, key() { throw new Error('SecurityError'); },
        getItem() { throw new Error('SecurityError'); }, setItem() { throw new Error('SecurityError'); },
        removeItem() { throw new Error('SecurityError'); } };
    const { page, notifications } = load('gallery.js', 'GalleryManager',
        async () => jsonResponse(200, { success: true }), { localStorage: denied });
    page.getSettings = () => ({});

    await page.clearCache();

    assert.deepEqual(notifications, [['warning',
        'Thumbnails cleared; original images untouched. This browser refused access to its storage, so nothing there was cleared.']]);
});

test('admin LoRA import shows a refusal frame and closes its modal', async () => {
    const { page, dom, timers, notifications } = load('admin.js', 'PromptAdmin',
        async () => sseResponse([{ type: 'error', status: BUSY, progress: 0, message: BUSY }]));
    page.saveLoraSettings = async () => true;
    page.loadStatistics = () => assert.fail('a refused import must not reload statistics');

    await page.runLoraImport();

    assert.equal(dom.nodes.loraImportStatus.textContent, `Error: ${BUSY}`);
    assert.deepEqual(notifications.filter(([type]) => type === 'error'), [['error', `LoRA import failed: ${BUSY}`]]);
    assert.equal(timers.length, 1);
    timers[0]();
    assert.deepEqual(notifications.at(-1), ['hide', 'loraImportModal']);
});

test('admin settings save shows why config.json refused it', async () => {
    const message = 'config.json cannot be read, so nothing was saved. Fix or remove the file, then save again.';
    const { page, notifications } = load('admin.js', 'PromptAdmin',
        async () => jsonResponse(409, { success: false, persisted: false, code: 'config-unreadable', error: message }));
    page.settings = {};
    page._collectScanPaths = () => [];
    page.saveLoraSettings = async () => assert.fail('nothing else is saved after a refusal');

    await page.saveSettings();

    assert.deepEqual(notifications, [['error', `Failed to save settings: ${message}`]]);
});

test('admin settings save keeps its plain message when the reply has no reason', async () => {
    const { page, notifications } = load('admin.js', 'PromptAdmin', async () => ({ ok: false, status: 500,
        json: async () => { throw new SyntaxError('not json'); } }));
    page.settings = {};
    page._collectScanPaths = () => [];

    await page.saveSettings();

    assert.deepEqual(notifications, [['error', 'Failed to save settings']]);
});

test('admin maintenance names the job that refused it', async () => {
    const { page, dom, notifications } = load('admin.js', 'PromptAdmin',
        async () => jsonResponse(409, { success: false, error: BUSY }), {
            escapeHtml: text => String(text),
            document: { readyState: 'loading', addEventListener() {}, getElementById: id => dom.getElementById(id),
                querySelectorAll: selector => (selector === '.maintenance-option:checked' ? [{ id: 'vacuum' }] : []) },
        });

    await page.runMaintenance();

    const results = dom.nodes.maintenanceResults.innerHTML;
    assert.match(results, /Maintenance Failed/);
    assert.match(results, new RegExp(BUSY));
    assert.doesNotMatch(results, /HTTP 409/);
    assert.deepEqual(notifications, [['error', '❌ Maintenance failed']]);
    assert.equal(dom.nodes.runMaintenanceBtn.disabled, false);
});
