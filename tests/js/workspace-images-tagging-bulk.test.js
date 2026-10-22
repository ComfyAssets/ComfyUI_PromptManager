import test from 'node:test';
import assert from 'node:assert/strict';

// Guarded: before the module exists every test fails on an assertion, not on the import.
const loaded = await import('../../web/js/workspace/images-tagging-bulk.mjs').catch(() => null);
const module = () => { assert.ok(loaded, 'images-tagging-bulk.mjs is not implemented'); return loaded; };
const state = await import('../../web/js/workspace/image-tagging-state.mjs');
const { startToast, parseJob } = await import('../../web/js/workspace/activity-store.mjs');
const SCOPE = 'b'.repeat(64);
const JOB_ID = 'c'.repeat(32);
const image = (path, extra = {}) => ({ root_index: 0, root_scope: SCOPE, path, filename: path.split('/').pop(), ...extra });
const pageScope = () => {
    const controller = new AbortController();
    const cleanups = [];
    return { signal: controller.signal, cleanup: fn => cleanups.push(fn), abort: () => controller.abort(), cleanups };
};

test('targets keep listing order and identity', () => {
    const { bulkTargets } = module();
    const listing = [image('b/2.png', { width: 9 }), image('a/1.png'), { ...image('x.png'), root_scope: 'nope' }, image('c/3.webm')];
    assert.deepEqual(bulkTargets(listing), [
        { root_index: 0, root_scope: SCOPE, path: 'b/2.png' },
        { root_index: 0, root_scope: SCOPE, path: 'a/1.png' },
        { root_index: 0, root_scope: SCOPE, path: 'c/3.webm' },
    ]);
    assert.deepEqual(bulkTargets([]), []);
});

test('over 500 selected refuses with the toast', () => {
    const { bulkTargets, openAddTags, openAutoTag, MAX_BULK_IMAGES, TOO_MANY } = module();
    assert.equal(MAX_BULK_IMAGES, 500);
    assert.equal(TOO_MANY, 'Select at most 500 images.');
    const many = Array.from({ length: 501 }, (_, index) => image(`bulk/${index}.png`));
    assert.equal(bulkTargets(many), null);
    assert.equal(bulkTargets(many.slice(0, 500)).length, 500);
    const toasts = [];
    const store = { start: () => assert.fail('no job starts') };
    // Refused before any dialog opens (no DOM here).
    openAddTags({ scope: pageScope(), store, images: many, notify: text => toasts.push(text) });
    openAutoTag({ api: {}, scope: pageScope(), store, images: many, notify: text => toasts.push(text) });
    assert.deepEqual(toasts, [TOO_MANY, TOO_MANY]);
});

test('add parses comma tags with the same dedupe as the dialog', () => {
    const { parseBulkTags, ADD_DIALOG } = module();
    assert.deepEqual(ADD_DIALOG, { title: 'Tag selected images', label: 'Tags, separated by commas', action: 'Apply' });
    assert.deepEqual(parseBulkTags(' Sunset, sunset ,, blue_hour,SUNSET, Blue_Hour '), ['Sunset', 'blue_hour']);
    assert.deepEqual(parseBulkTags(' , ,'), []);
    assert.equal(parseBulkTags, state.parseManualTags);
});

test('busy start keeps the selection and names the running job', async () => {
    const { startTagJob } = module();
    const toasts = [];
    const posted = [];
    const running = { id: JOB_ID, title: 'Scan for metadata' };
    const store = { start: async (kind, body) => { posted.push([kind, body]); return { busy: running, message: 'busy' }; } };
    const body = { mode: 'add', targets: [{ root_index: 0, root_scope: SCOPE, path: 'a.png' }], tags: ['x'] };
    assert.equal(await startTagJob({ store, scope: pageScope(), body, notify: text => toasts.push(text) }), null);
    assert.deepEqual(posted, [['image-tags', body]]);
    assert.deepEqual(toasts, [['Another job is running: ', { text: 'Scan for metadata', href: `#/activity/${JOB_ID}` }, '.']]);
});

test('a started job is returned without a toast of its own; refusals toast their sentence', async () => {
    const { startTagJob } = module();
    const toasts = [];
    const notify = text => toasts.push(text);
    const job = { id: JOB_ID, kind: 'image-tags' };
    assert.equal(await startTagJob({ store: { start: async () => ({ job }) }, scope: pageScope(), body: { mode: 'add' }, notify }), job);
    const refused = Object.assign(new Error('The tagging model is busy. Try again when it finishes.'), { name: 'ApiError', status: 409, code: 'model-busy' });
    assert.equal(await startTagJob({ store: { start: async () => { throw refused; } }, scope: pageScope(), body: { mode: 'auto' }, notify }), null);
    assert.equal(await startTagJob({ store: { start: async () => { throw new TypeError('down'); } }, scope: pageScope(), body: { mode: 'auto' }, notify }), null);
    assert.equal(await startTagJob({ store: { start: async () => { throw new TypeError('down'); } }, scope: pageScope(), body: { mode: 'add' }, notify }), null);
    assert.deepEqual(toasts, ['The tagging model is busy. Try again when it finishes.', 'Auto-tagging could not start. Try again.',
        'Tagging could not start. Try again.']);
    // A page left meanwhile says nothing.
    const left = pageScope();
    left.abort();
    assert.equal(await startTagJob({ store: { start: async () => { throw refused; } }, scope: left, body: { mode: 'auto' }, notify }), null);
    assert.equal(toasts.length, 3);
});

test('the start toast names the tagging mode', () => {
    const job = mode => parseJob({ id: JOB_ID, state: 'running', kind: 'image-tags', title: 'Add tags to images', scope: { mode, images: 2 } });
    assert.equal(startToast(job('add')), 'Tagging started.');
    assert.equal(startToast(job('auto')), 'Auto-tagging started.');
    assert.equal(startToast(job('other')), 'Add tags to images started.');
    assert.equal(startToast(parseJob({ id: JOB_ID, state: 'running', kind: 'scan', title: 'Scan for metadata' })), 'Scan started.');
});

test('a followed job reports its end once', () => {
    const { followJobEnd } = module();
    let jobs = [{ id: JOB_ID, state: 'running', counters: {} }];
    const listeners = new Set();
    const store = { find: id => jobs.find(job => job.id === id), subscribe: fn => { listeners.add(fn); return () => listeners.delete(fn); } };
    const ended = [];
    const scope = pageScope();
    followJobEnd({ store, scope, id: JOB_ID, onEnd: job => ended.push(job.state) });
    assert.equal(listeners.size, 1);
    for (const fn of [...listeners]) fn(store);
    assert.deepEqual(ended, []);
    jobs = [{ id: JOB_ID, state: 'finished', counters: { tagged: 1 } }];
    for (const fn of [...listeners]) fn(store);
    for (const fn of [...listeners]) fn(store);
    assert.deepEqual(ended, ['finished']);
    assert.equal(listeners.size, 0);
    assert.equal(scope.cleanups.length, 1);
});

test('a model that is not downloaded is named with the server sentence', () => {
    const { modelUnavailable } = state;
    assert.equal(modelUnavailable(null, 'wd14-vit'), '');
    assert.equal(modelUnavailable({ 'wd14-vit': { downloaded: true } }, 'wd14-vit'), '');
    assert.equal(modelUnavailable({ 'wd14-vit': { downloaded: false } }, 'wd14-vit'), 'WD14 ViT is not downloaded. Download it from the legacy Auto-tag page.');
    assert.equal(modelUnavailable({ gguf: { downloaded: false, name: 'GGUF (Recommended)' } }, 'gguf'),
        'GGUF (Recommended) is not downloaded. Download it from the legacy Auto-tag page.');
    assert.throws(() => modelUnavailable({}, 'nope'), TypeError);
});

test('the bulk options start from the server defaults and wait for them before Start', async () => {
    const { createTaggingOptionsController } = state;
    let answer;
    const api = { get: () => new Promise(resolve => { answer = resolve; }) };
    const options = createTaggingOptionsController({ api });
    const seen = [];
    options.subscribe(value => seen.push(value.optionsRevision));
    assert.equal(options.state.canStart, false);
    assert.equal(options.state.canGenerate, false);
    const opening = options.open();
    answer({ wd14_general_threshold: 0.5, wd14_character_threshold: 0.9, default_prompt: 'Say it', suggestion_limit: 7,
        models: { 'wd14-swinv2': { downloaded: true }, 'wd14-vit': { downloaded: false }, gguf: { downloaded: true } } });
    await opening;
    assert.equal(options.state.canStart, true);
    assert.deepEqual(options.request(), { model_type: 'wd14-swinv2', options: { general_threshold: 0.5, character_threshold: 0.9, suggestion_limit: 7 } });
    options.setOption('suggestion_limit', '3');
    options.setModel('wd14-vit');
    assert.equal(options.state.canStart, false);
    assert.match(options.state.unavailable, /^WD14 ViT is not downloaded\./);
    options.setModel('gguf');
    assert.deepEqual(options.request(), { model_type: 'gguf', options: { instructions: 'Say it', suggestion_limit: 3 } });
    options.setOption('instructions', '  ');
    assert.equal(options.request(), null);
    assert.deepEqual(seen, [1, 1, 2, 3, 3]);
    assert.throws(() => options.setModel('nope'), TypeError);
    assert.throws(() => options.setOption('nope', 1), TypeError);
    options.dispose();
    options.setOption('suggestion_limit', '4');
    assert.equal(options.state.options.suggestion_limit, '3');
});

test('the bulk options keep the documented defaults when the read fails', async () => {
    const { createTaggingOptionsController } = state;
    const options = createTaggingOptionsController({ api: { get: async () => { throw new Error('offline'); } } });
    await options.open();
    await options.open();
    assert.equal(options.state.canStart, true);
    assert.equal(options.state.unavailable, '');
    assert.deepEqual(options.request(), { model_type: 'wd14-swinv2', options: { general_threshold: 0.35, character_threshold: 0.85, suggestion_limit: 10 } });
    assert.throws(() => createTaggingOptionsController({}), TypeError);
});

test('a preset starts the Tag image dialog on the chosen model and options', async () => {
    const { createImageTaggingController } = state;
    const target = { root_index: 0, root_scope: SCOPE, path: 'a.png' };
    const api = {
        get: async () => ({ wd14_general_threshold: 0.35, wd14_character_threshold: 0.85, default_prompt: 'Describe', suggestion_limit: 10 }),
        post: async () => ({ success: true, library_epoch: '12345678-1234-1234-1234-123456789abc', filename: 'a.png', title: null, tags: [], tag_revision: 0,
            capture: { token: 'tok', sha256: 'd'.repeat(64), preview_url: '/prompt_manager/image-annotations/capture/tok', width: 4, height: 4,
                media_type: 'image', generate: { supported: true, reason: null } } }),
        put: async () => ({}), del: async () => ({}),
    };
    const controller = createImageTaggingController({ api, target, source: 'bulk', preset: { model: 'gguf', options: { instructions: 'Name it', suggestion_limit: 3 } } });
    assert.equal(controller.state.model, 'gguf');
    await controller.open();
    assert.deepEqual([controller.state.model, controller.state.options.instructions, controller.state.options.suggestion_limit], ['gguf', 'Name it', 3]);
    controller.dispose();
    assert.throws(() => createImageTaggingController({ api, target, preset: { model: 'nope', options: {} } }), TypeError);
    assert.throws(() => createImageTaggingController({ api, target, preset: { model: 'gguf', options: { instructions: '' } } }), TypeError);
    assert.throws(() => createImageTaggingController({ api, target, preset: { model: 'gguf', options: { nope: 1 } } }), TypeError);
});
