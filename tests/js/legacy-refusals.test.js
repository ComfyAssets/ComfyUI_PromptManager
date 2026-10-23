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
    const storage = new Map([['pm_cached', '1'], ['userData', 'keep']]);
    const localStorage = {
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
        Object: { ...Object, keys: target => (target === localStorage ? [...storage.keys()] : Object.keys(target)) },
        TextDecoder, TextEncoder, JSON,
        ApiClient: { fetchUrl },
        ...extra,
    });
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

test('gallery cache clear still says so when the server clears', async () => {
    const { page, dom, notifications, storage } = load('gallery.js', 'GalleryManager',
        async () => jsonResponse(200, { success: true, cleared_count: 3 }));
    page.getSettings = () => ({ thumbnailsGenerated: true });

    await page.clearCache();

    assert.deepEqual(notifications, [['success', 'PromptManager cache cleared successfully! Original images untouched.']]);
    assert.equal(dom.nodes.thumbnailStatus.textContent, 'Not generated');
    assert.equal(JSON.parse(storage.get('gallerySettings')).thumbnailsGenerated, false);
    assert.equal(storage.get('userData'), 'keep');
    assert.equal(storage.has('pm_cached'), false);
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
