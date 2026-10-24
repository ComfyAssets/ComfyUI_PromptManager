// System › thumbnail review (#249 System S5 Task 14): the concept's "Thumbnail maintenance" dialog (app.js 1433–1437,
// `thumbnail-review`) on the thumbnail job (S5 Task 12). Pure parts: the scopes the job takes (Convert outdated, #317),
// the D131 settings line that replaced its quality and size fields, the D99 plan line and the start outcome.
const test = require('node:test');
const assert = require('node:assert/strict');

const reviewModule = import('../../web/js/workspace/thumbnail-review.mjs');

test('the scopes map to the job modes; Convert outdated is the explicit third (#317)', async () => {
    const { SCOPE_OPTIONS } = await reviewModule;
    assert.deepEqual(SCOPE_OPTIONS, [['missing', 'Missing thumbnails only'], ['rebuild', 'Rebuild all (explicit opt-in)'],
        ['convert', 'Convert outdated (removes older formats)']]);
});

test('D131: the dialog names the server thumbnail settings instead of its own quality and size fields', async () => {
    const { settingsText, SETTINGS_UNKNOWN } = await reviewModule;
    assert.equal(settingsText({ format: 'webp', quality: 80, size: 512 }),
        'Previews are written as WebP · quality 80 · 512 px. Change this in System › Appearance.');
    assert.equal(settingsText({ format: 'jxl', quality: 90, size: 1024 }),
        'Previews are written as JPEG XL · quality 90 · 1,024 px. Change this in System › Appearance.');
    assert.equal(settingsText({ format: 'jpeg', quality: 5, size: 128 }),
        'Previews are written as JPEG · quality 5 · 128 px. Change this in System › Appearance.');
    for (const plan of [null, {}, { format: 'png', quality: 80, size: 512 }, { format: 'webp', quality: '80', size: 512 }]) {
        assert.equal(settingsText(plan), SETTINGS_UNKNOWN);
    }
});

test('Convert outdated says how many previews in an older format it converts, past the per-batch cap too (#317)', async () => {
    const { planText } = await reviewModule;
    assert.equal(planText({ mode: 'convert', targets: 17500, fresh: 2000, outdated: 17500, remaining: 7500 }),
        '25,000 previews in an older format to convert.');
    assert.equal(planText({ mode: 'convert', targets: 1, fresh: 0, outdated: 1, remaining: 0 }), '1 preview in an older format to convert.');
    assert.equal(planText({ mode: 'convert', targets: 0, fresh: 9, outdated: 0, remaining: 0 }), 'No previews in an older format.');
});

test('the plan line counts what a missing-only run generates and what is already up to date (D99)', async () => {
    const { planText } = await reviewModule;
    assert.equal(planText({ mode: 'missing', targets: 12, fresh: 30, remaining: 0 }), '12 previews to generate · 30 already up to date.');
    assert.equal(planText({ mode: 'missing', targets: 1, fresh: 1, remaining: 0 }), '1 preview to generate · 1 already up to date.');
    assert.equal(planText({ mode: 'missing', targets: 0, fresh: 0, remaining: 0 }), '0 previews to generate · 0 already up to date.');
    assert.equal(planText({ mode: 'missing', targets: 10000, fresh: 5, remaining: 2500 }),
        '12,500 previews to generate · 5 already up to date.');
});

test('Rebuild all says every preview is regenerated (D99)', async () => {
    const { planText } = await reviewModule;
    assert.equal(planText({ mode: 'rebuild', targets: 42, fresh: 30, remaining: 0 }), 'Every preview is regenerated: 42 files.');
    assert.equal(planText({ mode: 'rebuild', targets: 1, fresh: 1, remaining: 0 }), 'Every preview is regenerated: 1 file.');
    assert.equal(planText({ mode: 'rebuild', targets: 10000, fresh: 9000, remaining: 3 }),
        'Every preview is regenerated: 10,003 files.');
});

test('an answer without counts is a failed plan, never zeros', async () => {
    const { planText, PLAN_FAILED } = await reviewModule;
    assert.equal(planText(null), PLAN_FAILED);
    assert.equal(planText({ mode: 'missing', targets: 2 }), PLAN_FAILED);
    assert.equal(planText({ mode: 'missing', targets: -1, fresh: 0, remaining: 0 }), PLAN_FAILED);
    assert.equal(planText({ mode: 'other', targets: 1, fresh: 0, remaining: 0 }), PLAN_FAILED);
});

const scopeOf = () => ({ signal: new AbortController().signal });

test('Start starts the thumbnail job with the chosen options and opens it in Activity', async () => {
    const { startThumbnails } = await reviewModule;
    const calls = [];
    const navigated = [];
    const store = { start: async (kind, body) => { calls.push([kind, body]); return { job: { id: 'a'.repeat(32) } }; } };
    await startThumbnails({ store, scope: scopeOf(), options: { mode: 'convert' },
        navigate: hash => navigated.push(hash), notify: () => assert.fail('no toast of its own') });
    assert.deepEqual(calls, [['thumbnails', { mode: 'convert' }]]);
    assert.deepEqual(navigated, [`#/activity/${'a'.repeat(32)}`]);
});

test('a refused start shows the busy link toast and stays', async () => {
    const { startThumbnails } = await reviewModule;
    const toasts = [];
    const store = { start: async () => ({ busy: { id: 'b'.repeat(32), title: 'Scan for metadata' } }) };
    await startThumbnails({ store, scope: scopeOf(), options: { mode: 'missing' },
        navigate: () => assert.fail('stays'), notify: message => toasts.push(message) });
    assert.deepEqual(toasts, [['Another job is running: ', { text: 'Scan for metadata', href: `#/activity/${'b'.repeat(32)}` }, '.']]);
});

test('a failed start says so; a start after the section left says nothing', async () => {
    const { startThumbnails, START_FAILED } = await reviewModule;
    const toasts = [];
    const store = { start: async () => { throw new Error('offline'); } };
    await startThumbnails({ store, scope: scopeOf(), options: { mode: 'missing' },
        navigate: () => assert.fail('stays'), notify: message => toasts.push(message) });
    assert.deepEqual(toasts, [START_FAILED]);
    const gone = new AbortController();
    gone.abort();
    await startThumbnails({ store, scope: { signal: gone.signal }, options: { mode: 'missing' },
        navigate: () => assert.fail('stays'), notify: () => assert.fail('silent') });
});
