// System › thumbnail review (#249 System S5 Task 14): the concept's "Thumbnail maintenance" dialog (app.js 1433–1437,
// `thumbnail-review`) on the thumbnail job (S5 Task 12). Pure parts: the option values the job takes, the preview size
// check, the Image loading quality preselection (Q3), the D99 plan line and the start outcome.
const test = require('node:test');
const assert = require('node:assert/strict');

const reviewModule = import('../../web/js/workspace/thumbnail-review.mjs');

test('the concept options map to the job values in the concept order', async () => {
    const { SCOPE_OPTIONS, QUALITY_OPTIONS, SIZE_LIMIT } = await reviewModule;
    assert.deepEqual(SCOPE_OPTIONS, [['missing', 'Missing thumbnails only'], ['rebuild', 'Rebuild all (explicit opt-in)']]);
    assert.deepEqual(QUALITY_OPTIONS, [['medium', 'Medium · optimized'], ['high', 'High · original'], ['low', 'Low · fastest']]);
    assert.deepEqual(SIZE_LIMIT, { min: 128, max: 2048, value: 512 });
});

test('size outside 128–2048 reports validity: only whole pixels in range are a size', async () => {
    const { previewSize } = await reviewModule;
    assert.equal(previewSize('512'), 512);
    assert.equal(previewSize('128'), 128);
    assert.equal(previewSize('2048'), 2048);
    for (const text of ['', ' ', '127', '2049', '4096', '512.5', '-512', 'abc', '1e3', '0x200']) {
        assert.equal(previewSize(text), null, text);
    }
});

test('Image loading quality preselects the dialog quality (Q3); anything else is Medium', async () => {
    const { preselectedQuality } = await reviewModule;
    assert.equal(preselectedQuality('low'), 'low');
    assert.equal(preselectedQuality('high'), 'high');
    assert.equal(preselectedQuality('medium'), 'medium');
    assert.equal(preselectedQuality(undefined), 'medium');
    assert.equal(preselectedQuality('original'), 'medium');
});

test('the plan line counts what a missing-only run generates and what is already up to date (D99)', async () => {
    const { planText } = await reviewModule;
    assert.equal(planText({ mode: 'missing', targets: 12, fresh: 30, remaining: 0 }), '12 previews to generate · 30 already up to date.');
    assert.equal(planText({ mode: 'missing', targets: 1, fresh: 1, remaining: 0 }), '1 preview to generate · 1 already up to date.');
    assert.equal(planText({ mode: 'missing', targets: 0, fresh: 0, remaining: 0 }), '0 previews to generate · 0 already up to date.');
    assert.equal(planText({ mode: 'missing', targets: 10000, fresh: 5, remaining: 2500 }),
        '10,000 previews to generate · 5 already up to date · 2,500 more on the next run.');
});

test('Rebuild all says every preview is regenerated (D99)', async () => {
    const { planText } = await reviewModule;
    assert.equal(planText({ mode: 'rebuild', targets: 42, fresh: 30, remaining: 0 }), 'Every preview is regenerated: 42 files.');
    assert.equal(planText({ mode: 'rebuild', targets: 1, fresh: 1, remaining: 0 }), 'Every preview is regenerated: 1 file.');
    assert.equal(planText({ mode: 'rebuild', targets: 10000, fresh: 9000, remaining: 3 }),
        'Every preview is regenerated: 10,000 files · 3 more on the next run.');
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
    await startThumbnails({ store, scope: scopeOf(), options: { mode: 'rebuild', size: 1024, quality: 'high' },
        navigate: hash => navigated.push(hash), notify: () => assert.fail('no toast of its own') });
    assert.deepEqual(calls, [['thumbnails', { mode: 'rebuild', size: 1024, quality: 'high' }]]);
    assert.deepEqual(navigated, [`#/activity/${'a'.repeat(32)}`]);
});

test('a refused start shows the busy link toast and stays', async () => {
    const { startThumbnails } = await reviewModule;
    const toasts = [];
    const store = { start: async () => ({ busy: { id: 'b'.repeat(32), title: 'Scan for metadata' } }) };
    await startThumbnails({ store, scope: scopeOf(), options: { mode: 'missing', size: 512, quality: 'medium' },
        navigate: () => assert.fail('stays'), notify: message => toasts.push(message) });
    assert.deepEqual(toasts, [['Another job is running: ', { text: 'Scan for metadata', href: `#/activity/${'b'.repeat(32)}` }, '.']]);
});

test('a failed start says so; a start after the section left says nothing', async () => {
    const { startThumbnails, START_FAILED } = await reviewModule;
    const toasts = [];
    const store = { start: async () => { throw new Error('offline'); } };
    await startThumbnails({ store, scope: scopeOf(), options: { mode: 'missing', size: 512, quality: 'medium' },
        navigate: () => assert.fail('stays'), notify: message => toasts.push(message) });
    assert.deepEqual(toasts, [START_FAILED]);
    const gone = new AbortController();
    gone.abort();
    await startThumbnails({ store, scope: { signal: gone.signal }, options: { mode: 'missing', size: 512, quality: 'medium' },
        navigate: () => assert.fail('stays'), notify: () => assert.fail('silent') });
});
