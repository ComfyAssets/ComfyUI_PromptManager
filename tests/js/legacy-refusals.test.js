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

test('admin settings save shows why config.json refused it', async () => {
    const message = 'config.json cannot be read, so nothing was saved. Fix or remove the file, then save again.';
    const { page, notifications } = load('admin.js', 'PromptAdmin',
        async () => jsonResponse(409, { success: false, persisted: false, code: 'config-unreadable', error: message }));
    page.settings = {};
    page.setInfiniteScroll = () => assert.fail('nothing changes after a refusal');

    await page.saveSettings();

    assert.deepEqual(notifications, [['error', `Failed to save settings: ${message}`]]);
});

test('admin settings save keeps its plain message when the reply has no reason', async () => {
    const { page, notifications } = load('admin.js', 'PromptAdmin', async () => ({ ok: false, status: 500,
        json: async () => { throw new SyntaxError('not json'); } }));
    page.settings = {};

    await page.saveSettings();

    assert.deepEqual(notifications, [['error', 'Failed to save settings']]);
});

// #330 S13-9: the admin page's reduced Page options save sends only the list's scroll default, so a stale legacy form
// can no longer overwrite scan folders or other settings edited in System.
test('admin Page options save posts only infinite_scroll', async () => {
    const sent = [];
    const { page, dom, notifications } = load('admin.js', 'PromptAdmin', async (url, init) => {
        sent.push([url, init.method, JSON.parse(init.body)]);
        return jsonResponse(200, { success: true, restart_required: true });
    });
    page.settings = { infiniteScroll: false };
    const scrolled = [];
    page.setInfiniteScroll = (on, options) => scrolled.push([on, { ...options }]);
    dom.getElementById('infiniteScrollDefault').checked = true;

    await page.saveSettings();

    assert.deepEqual(sent, [['/prompt_manager/settings', 'POST', { infinite_scroll: true }]]);
    assert.deepEqual(scrolled, [[true, { session: false }]]);
    assert.equal(page.settings.infiniteScroll, true);
    assert.deepEqual(notifications, [['success', 'Page options saved'], ['hide', 'settingsModal']]);
});

