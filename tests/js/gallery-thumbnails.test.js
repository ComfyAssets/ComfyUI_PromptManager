const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fixture(status, settings = {}) {
    const timers = [];
    const prompts = [];
    const requests = [];
    const buttons = {};
    const storage = new Map();
    const document = {
        addEventListener() {},
        createElement: () => ({ addEventListener() {} }),
        body: { appendChild() {}, removeChild() {} },
        getElementById: id => buttons[id] ||= { addEventListener(type, handler) { this[type] = handler; } },
    };
    const context = vm.createContext({ document, window: {}, console: { debug() {}, warn() {} },
        setTimeout: callback => timers.push(callback),
        localStorage: { setItem: (key, value) => storage.set(key, value) },
        ApiClient: { fetchUrl: async () => ({ json: async () => ({ success: true,
            total: 10000, images: Array.from({ length: 50 }, () => ({ thumbnail_url: null })),
        }) }) },
    });
    const Manager = vm.runInContext(fs.readFileSync(path.join(__dirname, '../../web/js/gallery.js'), 'utf8') + '\nGalleryManager;', context, { filename: path.join(__dirname, '../../web/js/gallery.js') });
    const manager = Object.create(Manager.prototype);
    Object.assign(manager, { getSettings: () => ({ checkThumbnailsAtStartup: true, ...settings }),
        api: { get: async url => { requests.push(url); return status; } },
    });
    const originalPrompt = manager.showThumbnailGenerationPrompt.bind(manager);
    manager.showThumbnailGenerationPrompt = (...args) => prompts.push(args);
    async function check() {
        await manager.checkThumbnailsAtStartup();
        await timers.shift()();
    }
    return { manager, check, prompts, requests, settings, buttons, storage, originalPrompt };
}

test('startup uses exact collection counts instead of extrapolating the newest 50 images', async () => {
    const f = fixture({ success: true, total_images: 10000, pending: 50, ready: 9950, collection_id: 'a' });
    await f.check();
    assert.deepEqual(f.requests, ['/prompt_manager/images/thumbnails/status']);
    assert.equal(f.prompts.length, 0);
});

test('startup prompt reports the actual number needing generation', async () => {
    const f = fixture({ success: true, total_images: 10000, pending: 1200, collection_id: 'a' });
    await f.check();
    assert.equal(f.prompts.length, 1);
    assert.deepEqual(f.prompts[0].slice(0, 2), [10000, 1200]);
});

test('a complete collection never prompts again', async () => {
    const f = fixture({ success: true, total_images: 10000, pending: 0, collection_id: 'a' });
    await f.check();
    await f.check();
    assert.equal(f.prompts.length, 0);
});

test('Not Now persists the collection state and suppresses the unchanged prompt', async () => {
    const status = { success: true, total_images: 100, pending: 30, collection_id: 'a' };
    const f = fixture(status);
    await f.check();
    f.originalPrompt(...f.prompts[0]);
    f.buttons.thumbnailPromptNo.click();
    Object.assign(f.settings, JSON.parse(f.storage.get('gallerySettings')));
    f.prompts.length = 0;
    await f.check();
    assert.equal(f.prompts.length, 0);
    status.pending = 40;
    await f.check();
    assert.equal(f.prompts.length, 1);
});

// WebKit fails a fetch a leaving page starts ("Fetch API cannot load … due to access control checks.") and logs it
// as a page error: the delayed startup check starts no read once the page has begun to leave (as admin.js, #290).
test('the startup check starts no read once the page is leaving', async () => {
    const f = fixture({ success: true, total_images: 100, pending: 30, collection_id: 'a' });
    f.manager.pageLifetime = new AbortController();
    f.manager.pageLifetime.abort();
    await f.check();
    assert.equal(f.requests.length, 0);
    assert.equal(f.prompts.length, 0);
});

test('the startup check passes the page lifetime to its read', async () => {
    const f = fixture({ success: true, total_images: 100, pending: 30, collection_id: 'a' });
    const signals = [];
    f.manager.pageLifetime = new AbortController();
    const get = f.manager.api.get;
    f.manager.api.get = async (url, options) => { signals.push(options?.signal); return get(url); };
    await f.check();
    assert.deepEqual(signals, [f.manager.pageLifetime.signal]);
    assert.equal(f.prompts.length, 1);
});

test('disabled checks do not request collection status', async () => {
    const f = fixture({}, { checkThumbnailsAtStartup: false });
    await f.check();
    assert.equal(f.requests.length, 0);
    assert.equal(f.prompts.length, 0);
});

test('saving gallery settings retains the remembered thumbnail dismissal', () => {
    const f = fixture({}, { thumbnailPromptDismissed: 'remembered-state' });
    f.manager.showNotification = () => {};
    f.manager.hideSettings = () => {};
    f.manager.applySettings = () => {};
    f.manager.saveSettings();
    assert.equal(JSON.parse(f.storage.get('gallerySettings')).thumbnailPromptDismissed, 'remembered-state');
});
