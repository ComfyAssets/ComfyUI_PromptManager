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

// #249 System S5: the stream's work is now an Activity job that outlives the page's connection, so the gallery's
// Cancel asks the server to stop the job. Since #330 the startup dialog is the gallery's only thumbnail stream (System
// › Sources & scans generates the rest). The stream's first frame, `job {id, started}`, names the job; the gallery
// cancels by that id, only a job this stream started, and a Cancel before the id waits for it (review I1).
const JOB = 'b'.repeat(32);

function streamFixture({ postFails = false } = {}) {
    const posts = [];
    const warnings = [];
    const gets = [];
    const listeners = {};
    const source = { closed: 0, readyState: 1, CLOSED: 2, url: null,
        addEventListener(type, handler) { (listeners[type] ||= []).push(handler); },
        close() { this.closed += 1; } };
    const element = () => ({ classList: { add() {}, remove() {} }, style: {}, textContent: '', handlers: {},
        addEventListener(type, handler) { this.handlers[type] = handler; },
        removeEventListener(type, handler) { if (this.handlers[type] === handler) delete this.handlers[type]; },
        click() { this.handlers.click?.(); } });
    const elements = {};
    const document = { addEventListener() {}, getElementById: id => elements[id] ||= element(),
        createElement: () => element(), body: { appendChild() {}, removeChild() {} } };
    const context = vm.createContext({ document, window: {},
        console: { debug() {}, warn: (...args) => warnings.push(args), error() {} },
        setTimeout() {}, localStorage: { setItem() {} },
        SseStream: { connect: (url, options) => { source.url = url; source.options = options; return source; } } });
    const file = path.join(__dirname, '../../web/js/gallery.js');
    const Manager = vm.runInContext(fs.readFileSync(file, 'utf8') + '\nGalleryManager;', context, { filename: file });
    const manager = Object.create(Manager.prototype);
    Object.assign(manager, { getSettings: () => ({}),
        api: { get: async url => { gets.push(url); return { success: true, jobs: [] }; },
            post: async (url, body) => {
                posts.push([url, body]);
                if (postFails) throw new Error('offline');
                return { success: true };
            } } });
    const emit = (type, data) => (listeners[type] || []).forEach(handler => handler({ data: JSON.stringify(data) }));
    // The dialog's Cancel runs the handler and from then on reads as cancelled, as the startup dialog does.
    let cancelled = false;
    const ui = { addStatusMessage() {}, progressText: element(), progressPercent: element(), progressBar: element(),
        progressDetails: element(), progressETA: element(), isCancelled: () => cancelled,
        onCancel: handler => { ui.cancel = () => { cancelled = true; handler(); }; } };
    const result = manager.generateThumbnailsWithProgressInModal(ui);
    const outcome = result.then(value => ({ value }), error => ({ error: error.message }));
    const cancel = () => ui.cancel();
    const settle = () => new Promise(resolve => setImmediate(resolve));
    return { emit, source, posts, gets, warnings, outcome, cancel, settle };
}

const cancelUrl = id => `/prompt_manager/jobs/${id}/cancel`;
const CANCELLED = 'Thumbnail generation cancelled; previews already written are kept';

test('Cancel before the job id arrives cancels that job once it does', async () => {
    const f = streamFixture();
    f.cancel();
    await f.settle();
    assert.deepEqual(f.posts, []);
    assert.equal(f.source.closed, 0); // kept open until the stream names its job
    assert.deepEqual(await f.outcome, { error: CANCELLED });
    // The job frame is the stream's first (the dialog closes the stream on any later frame once cancelled).
    f.emit('job', { id: JOB, started: true });
    await f.settle();
    assert.equal(JSON.stringify(f.posts), JSON.stringify([[cancelUrl(JOB), {}]]));
    assert.equal(f.source.closed, 1);
    f.emit('job', { id: 'c'.repeat(32), started: true }); // a stray second frame changes nothing
    await f.settle();
    assert.equal(f.posts.length, 1);
    assert.deepEqual(f.gets, []); // never looks a job up by kind
});

test('Cancel after the id cancels it at once, and a double Cancel posts once', async () => {
    const f = streamFixture();
    f.emit('job', { id: JOB, started: true });
    f.cancel();
    f.cancel();
    await f.settle();
    assert.equal(JSON.stringify(f.posts), JSON.stringify([[cancelUrl(JOB), {}]]));
    assert.equal(f.source.closed, 1);
    assert.deepEqual(await f.outcome, { error: CANCELLED });
    assert.deepEqual(f.gets, []);
});

test("Cancel leaves another tab's or System's job running and only stops following it", async () => {
    const f = streamFixture();
    f.emit('job', { id: JOB, started: false });
    f.cancel();
    await f.settle();
    assert.deepEqual(f.posts, []);
    assert.deepEqual(f.gets, []);
    assert.equal(f.source.closed, 1);
    assert.match((await f.outcome).error, /keeps running/);
});

test('a Cancel the stream never names a job for posts nothing and closes when it ends', async () => {
    const f = streamFixture();
    f.cancel();
    f.emit('error', { error: 'Scan for metadata already running' });
    await f.settle();
    assert.deepEqual(f.posts, []);
    assert.equal(f.source.closed, 1);
});

test('a cancel post that fails is reported, not thrown', async () => {
    const f = streamFixture({ postFails: true });
    f.emit('job', { id: JOB, started: true });
    f.cancel();
    await f.settle();
    assert.equal(f.posts.length, 1);
    assert.equal(f.warnings.length, 1);
    assert.deepEqual(await f.outcome, { error: CANCELLED });
});

test('the startup dialog cancels its own job by id too', async () => {
    const f = streamFixture();
    f.cancel();
    await f.settle();
    assert.deepEqual(f.posts, []);
    f.emit('job', { id: JOB, started: true });
    await f.settle();
    assert.equal(JSON.stringify(f.posts), JSON.stringify([[cancelUrl(JOB), {}]]));
    assert.equal(f.source.closed, 1);
    assert.match((await f.outcome).error, /cancelled/);
});
