// Run with: node --test tests/js/workspace-image-tagging-state.test.js
const test = require('node:test');
const assert = require('node:assert/strict');
const { createApiClient } = require('../../web/js/api-client.js');

// Only an absent module is tolerated, so RED is an assertion failure, not a crash.
const source = import('../../web/js/workspace/image-tagging-state.mjs').catch(error => {
    if (error.code !== 'ERR_MODULE_NOT_FOUND') throw error;
    return {};
});
async function load() {
    const module = await source;
    assert.equal(typeof module.createImageTaggingController, 'function', 'image-tagging-state.mjs is not implemented');
    return module;
}

const EPOCH = '11111111-1111-4111-8111-111111111111';
const SCOPE = 'c'.repeat(64);
const SHA = 'd'.repeat(64);
const TOKEN = 'capture-token_1';
const TOKEN2 = 'capture-token_2';
const TARGET = Object.freeze({ root_index: 0, root_scope: SCOPE, path: 'dunes/dunes_00001.png' });
const OBSERVE = '/prompt_manager/image-annotations/observe';
const SUGGEST = '/prompt_manager/image-annotations/suggest';
const TAGS = '/prompt_manager/image-annotations/tags';
const MODELS = '/prompt_manager/autotag/models';
const CAPTURE = '/prompt_manager/image-annotations/capture/';
const DEFAULT_PROMPT = 'Generate only comma-separated Danbooru tags (lowercase_underscores, no spaces).';

const flush = () => new Promise(resolve => setImmediate(resolve));
async function settle(times = 8) { for (let i = 0; i < times; i++) await flush(); }
function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}
function response(status, body) {
    return { ok: status >= 200 && status < 300, status, statusText: `Status ${status}`, json: async () => body };
}
const fail = (status, code, error = `failure ${code}`, extra = {}) => response(status, { success: false, code, error, ...extra });

function captureInfo(overrides = {}) {
    return {
        token: TOKEN, expires_at: '2026-10-22T12:00:00+00:00', preview_url: CAPTURE + (overrides.token || TOKEN),
        sha256: SHA, size: 1234, width: 832, height: 1216, media_type: 'image',
        generate: { supported: true, reason: null }, ...overrides,
    };
}
function observed(overrides = {}, capture = {}) {
    return {
        success: true, library_epoch: EPOCH, target: { ...TARGET }, capture: captureInfo(capture),
        title: 'Desert pavilion', filename: 'dunes_00001.png', tags: ['Minimal', 'warm_light'], tag_revision: 3,
        ...overrides,
    };
}
const models = (overrides = {}) => ({
    success: true, models: { 'wd14-swinv2': { downloaded: true } }, default_prompt: DEFAULT_PROMPT,
    wd14_general_threshold: 0.35, wd14_character_threshold: 0.85, suggestion_limit: 10, ...overrides,
});
const SANDSTONE = [
    { name: 'minimal', confidence: 0.99 }, { name: 'editorial', confidence: 0.97 },
    { name: 'sandstone', confidence: 0.981 }, { name: 'Editorial', confidence: 0.5 },
];
const suggested = (rows = SANDSTONE, overrides = {}) => ({ success: true, model_type: 'wd14-swinv2', options: {}, suggestions: rows, ...overrides });
const receipt = (tags, overrides = {}) => ({ success: true, library_epoch: EPOCH, sha256: SHA, tags, tag_revision: 4, replayed: false, ...overrides });

/** Real createApiClient over a fetch whose answers come from routes[METHOD URL](call). */
function harness(routes = {}) {
    const calls = [];
    const fetch = (url, init = {}) => {
        const call = { url, method: init.method, init, body: init.body === undefined ? undefined : JSON.parse(init.body), aborted: false, signal: init.signal };
        calls.push(call);
        return new Promise((resolve, reject) => {
            init.signal?.addEventListener('abort', () => {
                call.aborted = true;
                reject(new DOMException('The operation was aborted.', 'AbortError'));
            });
            const key = `${call.method} ${url.startsWith(CAPTURE) ? CAPTURE : url}`;
            const route = routes[key];
            Promise.resolve().then(() => {
                if (!route) return response(200, { success: true });
                return route(call);
            }).then(resolve, reject);
        });
    };
    return {
        api: createApiClient(fetch), calls,
        of: (method, url) => calls.filter(call => call.method === method && (url === CAPTURE ? call.url.startsWith(CAPTURE) : call.url === url)),
    };
}
function defaultRoutes(extra = {}) {
    return {
        [`POST ${OBSERVE}`]: () => response(200, observed()),
        [`GET ${MODELS}`]: () => response(200, models()),
        [`POST ${SUGGEST}`]: () => response(200, suggested()),
        [`PUT ${TAGS}`]: call => response(200, receipt(call.body.tags)),
        ...extra,
    };
}
async function opened(routes, options = {}) {
    const { createImageTaggingController } = await load();
    const h = harness(defaultRoutes(routes));
    const c = createImageTaggingController({ api: h.api, target: TARGET, source: 'images', ...options });
    await c.open();
    await settle();
    return { c, h };
}

test('MODEL_OPTIONS lists legacy engines in mock order with D84 labels', async () => {
    const { MODEL_OPTIONS } = await load();
    assert.deepEqual(MODEL_OPTIONS.map(option => [option.id, option.label]), [
        ['wd14-swinv2', 'WD14 SwinV2 · ~400 MB'],
        ['wd14-vit', 'WD14 ViT · ~400 MB'],
        ['gguf', 'GGUF JoyCaption (Recommended) · ~4.5 GB'],
        ['hf', 'HuggingFace JoyCaption · ~16 GB'],
    ]);
    assert.ok(Object.isFrozen(MODEL_OPTIONS) && Object.isFrozen(MODEL_OPTIONS[0]));
});

test('dedupeTags trims, drops blanks and keeps the first spelling case-insensitively', async () => {
    const { dedupeTags, parseManualTags } = await load();
    const input = [' Minimal ', 'minimal', '', 'warm_light', 'WARM_LIGHT', 7, 'dune'];
    assert.deepEqual(dedupeTags(input), ['Minimal', 'warm_light', 'dune']);
    assert.deepEqual(input[0], ' Minimal ', 'input is not mutated');
    assert.deepEqual(dedupeTags(undefined), []);
    assert.deepEqual(parseManualTags(' a, ,B , b,c '), ['a', 'B', 'c']);
    assert.deepEqual(parseManualTags(null), []);
});

test('the controller rejects a malformed target, source or api', async () => {
    const { createImageTaggingController } = await load();
    const { api } = harness();
    assert.throws(() => createImageTaggingController({ api, target: { ...TARGET, root_index: true } }), TypeError);
    assert.throws(() => createImageTaggingController({ api, target: { ...TARGET, root_scope: 'x' } }), TypeError);
    assert.throws(() => createImageTaggingController({ api, target: { ...TARGET, path: '' } }), TypeError);
    assert.throws(() => createImageTaggingController({ api, target: TARGET, source: 'elsewhere' }), TypeError);
    assert.throws(() => createImageTaggingController({ api: {}, target: TARGET }), TypeError);
    assert.throws(() => createImageTaggingController(), TypeError);
});

test('opens with saved tags joined by comma-space', async () => {
    const { createImageTaggingController } = await load();
    const h = harness(defaultRoutes());
    const c = createImageTaggingController({ api: h.api, target: TARGET, source: 'images' });
    const seen = [];
    const unsubscribe = c.subscribe(state => seen.push(state.phase));
    assert.equal(c.state.phase, 'observing');
    const pending = c.open();
    assert.equal(c.state.phase, 'observing');
    await pending;
    await settle();
    assert.equal(c.state.phase, 'ready');
    assert.equal(c.state.manual, 'Minimal, warm_light');
    assert.deepEqual(c.state.savedTags, ['Minimal', 'warm_light']);
    assert.equal(c.state.image.filename, 'dunes_00001.png');
    assert.equal(c.state.image.title, 'Desert pavilion');
    assert.equal(c.state.image.dimensions, '832 × 1216');
    assert.equal(c.state.image.previewUrl, CAPTURE + TOKEN);
    assert.equal(c.state.model, 'wd14-swinv2');
    assert.equal(c.state.countText, 'Not generated');
    assert.equal(c.state.primaryLabel, 'Save manual tags');
    assert.equal(c.state.suggestionsText, 'Generate suggestions with the selected model before approving.');
    assert.equal(c.state.showScopeNote, false);
    assert.equal(c.state.canSave, true);
    assert.equal(c.state.canGenerate, true);
    assert.ok(Object.isFrozen(c.state));
    assert.deepEqual(h.of('POST', OBSERVE)[0].body, { target: { ...TARGET } });
    assert.ok(seen.includes('ready'));
    unsubscribe();
    c.setManual('x');
    assert.equal(seen.at(-1), 'ready', 'an unsubscribed listener is not called');
    assert.throws(() => c.subscribe('nope'), TypeError);
});

test('an image without known dimensions shows none and an unseen file starts empty', async () => {
    const { c } = await opened({
        [`POST ${OBSERVE}`]: () => response(200, observed({ tags: [], tag_revision: 0, title: null }, { width: null, height: null })),
    });
    assert.equal(c.state.image.dimensions, null);
    assert.equal(c.state.manual, '');
    assert.equal(c.state.image.title, null);
});

test('instructions default to the server prompt', async () => {
    const { c } = await opened({
        [`GET ${MODELS}`]: () => response(200, models({ suggestion_limit: 7, wd14_general_threshold: 0.5 })),
    });
    assert.equal(c.state.options.instructions, DEFAULT_PROMPT);
    assert.equal(c.state.options.suggestion_limit, 7);
    assert.equal(c.state.options.general_threshold, 0.5);
    assert.equal(c.state.options.character_threshold, 0.85);
    c.setModel('gguf');
    assert.equal(c.state.options.instructions, DEFAULT_PROMPT);
    c.setOption('instructions', 'only colours');
    c.setModel('hf');
    assert.equal(c.state.options.instructions, DEFAULT_PROMPT, 'a model change re-renders the defaults like the mock');
    assert.equal(c.state.options.suggestion_limit, 7, 'the suggestion count survives a model change');
});

// #328: one tagging configuration. The dialogs start on the model saved in System › Auto-tagging, unless a preset
// (a review session's choice) or the user already picked one; an unknown saved model keeps the first option.
test('dialog starts on the saved model unless a preset or the user chose one', async () => {
    const saved = { [`GET ${MODELS}`]: () => response(200, models({ model_type: 'gguf' })) };
    const { c } = await opened(saved);
    assert.equal(c.state.model, 'gguf');
    assert.equal(c.state.options.instructions, DEFAULT_PROMPT);

    const { c: preset } = await opened(saved, { preset: { model: 'wd14-vit', options: {} } });
    assert.equal(preset.state.model, 'wd14-vit');

    const { createImageTaggingController, createTaggingOptionsController } = await load();
    const h = harness(defaultRoutes(saved));
    const picked = createImageTaggingController({ api: h.api, target: TARGET, source: 'images' });
    picked.setModel('hf');
    await picked.open();
    await settle();
    assert.equal(picked.state.model, 'hf', 'the user picked a model before the read landed');

    // A pick made while the models read is still in flight wins over the saved model that lands after it.
    const gate = deferred();
    const late = harness(defaultRoutes({ [`GET ${MODELS}`]: () => gate.promise }));
    const racing = createImageTaggingController({ api: late.api, target: TARGET, source: 'images' });
    const opening = racing.open();
    await settle();
    assert.equal(racing.state.phase, 'observing');
    racing.setModel('wd14-vit');
    gate.resolve(response(200, models({ model_type: 'gguf' })));
    await opening;
    await settle();
    assert.equal(racing.state.model, 'wd14-vit');
    racing.dispose();

    const { c: unknown } = await opened({ [`GET ${MODELS}`]: () => response(200, models({ model_type: 'wd14-convnext' })) });
    assert.equal(unknown.state.model, 'wd14-swinv2');

    const options = createTaggingOptionsController({ api: harness(defaultRoutes(saved)).api });
    await options.open();
    assert.equal(options.state.model, 'gguf');
    const chosen = createTaggingOptionsController({ api: harness(defaultRoutes(saved)).api });
    chosen.setModel('wd14-vit');
    await chosen.open();
    assert.equal(chosen.state.model, 'wd14-vit');
    for (const controller of [c, preset, picked, unknown, options, chosen]) controller.dispose();
});

test('an unreadable or malformed models read falls back to the documented defaults', async () => {
    for (const route of [() => fail(500, null), () => response(200, models({ default_prompt: 4, suggestion_limit: 0, wd14_general_threshold: 2, wd14_character_threshold: 'x' }))]) {
        const { c } = await opened({ [`GET ${MODELS}`]: route });
        assert.equal(c.state.phase, 'ready');
        assert.deepEqual({ ...c.state.options }, { general_threshold: 0.35, character_threshold: 0.85, instructions: '', suggestion_limit: 10 });
    }
});

test('observe failures and malformed answers end in observe-error and retry observes again', async () => {
    const { createImageTaggingController } = await load();
    let answer = () => fail(409, 'source-changed', 'The source changed. Refresh the image collection before tagging this file.');
    const h = harness(defaultRoutes({ [`POST ${OBSERVE}`]: () => answer() }));
    const c = createImageTaggingController({ api: h.api, target: TARGET });
    await c.open();
    assert.equal(c.state.phase, 'observe-error');
    assert.equal(c.state.statusText, 'The source changed. Refresh the image collection before tagging this file.');
    assert.equal(c.state.canSave, false);
    assert.equal(c.state.canGenerate, false);
    assert.equal(c.state.canRetry, true);
    answer = () => response(200, observed({ tag_revision: -1 }));
    await c.retry();
    assert.equal(c.state.phase, 'observe-error');
    assert.equal(c.state.statusText, 'The image could not be opened for tagging.');
    assert.equal(h.of('DELETE', CAPTURE).length, 1, 'a malformed answer still releases its capture');
    answer = () => Promise.reject(new TypeError('Failed to fetch'));
    await c.retry();
    assert.equal(c.state.statusText, 'The image could not be opened for tagging.');
    answer = () => response(200, observed());
    await c.retry();
    await settle();
    assert.equal(c.state.phase, 'ready');
    assert.equal(h.of('POST', OBSERVE).length, 4);
});

test('model change clears suggestions count and status but keeps manual', async () => {
    const { c } = await opened();
    c.setManual('Minimal, dune');
    await c.generate();
    assert.equal(c.state.countText, '3 suggestions');
    c.setModel('wd14-vit');
    assert.deepEqual(c.state.suggestions, []);
    assert.equal(c.state.countText, 'Not generated');
    assert.equal(c.state.statusText, '');
    assert.equal(c.state.suggestionsText, 'Generate suggestions with the selected model before approving.');
    assert.equal(c.state.manual, 'Minimal, dune');
    assert.equal(c.state.model, 'wd14-vit');
    c.setModel('wd14-vit');
    assert.equal(c.state.model, 'wd14-vit');
    assert.throws(() => c.setModel('ollama'), TypeError);
    assert.throws(() => c.setOption('model', 1), TypeError);
});

test('generate sends the model options and stages scored chips', async () => {
    const { c, h } = await opened();
    c.setOption('general_threshold', '0.4');
    c.setOption('suggestion_limit', '3');
    assert.equal(await c.generate(), true);
    const sent = h.of('POST', SUGGEST)[0].body;
    assert.deepEqual(sent, {
        target: { ...TARGET }, capture_token: TOKEN, expected_epoch: EPOCH, model_type: 'wd14-swinv2',
        options: { general_threshold: 0.4, character_threshold: 0.85, suggestion_limit: 3 },
    });
    assert.deepEqual(c.state.suggestions.map(row => [row.name, row.label]), [['minimal', '99%'], ['editorial', '97%'], ['sandstone', '98%']]);
    assert.equal(c.state.statusText, 'Suggestions from WD14 SwinV2 · ~400 MB. Approve tags to save.');
    assert.equal(c.state.suggestionsText, '');
    c.setModel('gguf');
    await c.generate();
    assert.deepEqual(h.of('POST', SUGGEST)[1].body.options, { instructions: DEFAULT_PROMPT, suggestion_limit: 3 });
});

test('language models label chips Suggested and the count is capped at the requested limit', async () => {
    const rows = Array.from({ length: 12 }, (_, i) => ({ name: `tag${i}`, confidence: null }));
    const { c } = await opened({ [`POST ${SUGGEST}`]: () => response(200, suggested(rows, { model_type: 'gguf' })) });
    c.setModel('gguf');
    await c.generate();
    assert.equal(c.state.suggestions.length, 10);
    assert.ok(c.state.suggestions.every(row => row.label === 'Suggested' && row.confidence === null));
    assert.equal(c.state.countText, '10 suggestions');
});

test('an empty result shows the threshold copy and a malformed one is a generate error', async () => {
    let answer = () => response(200, suggested([]));
    const { c } = await opened({ [`POST ${SUGGEST}`]: () => answer() });
    await c.generate();
    assert.equal(c.state.suggestionsText, 'No suggestions met this threshold. Adjust it or enter tags manually.');
    assert.equal(c.state.countText, '0 suggestions');
    assert.equal(c.state.primaryLabel, 'Approve selected tags');
    answer = () => response(200, suggested([{ name: 4, confidence: 2 }]));
    await c.generate();
    assert.equal(c.state.phase, 'generate-error');
    assert.equal(c.state.statusText, 'Auto-tagging failed. Check server logs for details.');
    answer = () => response(200, suggested([{ name: 'one', confidence: 1 }]));
    assert.equal(await c.retry(), true, 'retry after a generate error generates again');
    assert.equal(c.state.countText, '1 suggestion');
    assert.equal(c.state.suggestions[0].label, '100%');
});

test('invalid options send nothing', async () => {
    const { c, h } = await opened();
    const { optionError } = await load();
    for (const [key, value] of [['general_threshold', '1.5'], ['general_threshold', ''], ['character_threshold', 'abc'], ['suggestion_limit', '0'], ['suggestion_limit', '2.5'], ['suggestion_limit', '101']]) {
        c.setOption('general_threshold', 0.35);
        c.setOption('character_threshold', 0.85);
        c.setOption('suggestion_limit', 10);
        c.setOption(key, value);
        assert.equal(await c.generate(), false, `${key}=${value}`);
        assert.equal(optionError(c.state.model, c.state.options).field, key);
    }
    c.setModel('gguf');
    c.setOption('instructions', '   ');
    assert.equal(await c.generate(), false);
    c.setOption('instructions', 'x'.repeat(4001));
    assert.equal(await c.generate(), false);
    assert.equal(optionError('gguf', c.state.options).field, 'instructions');
    assert.equal(h.of('POST', SUGGEST).length, 0);
    assert.equal(optionError('wd14-vit', { general_threshold: 0, character_threshold: 1, suggestion_limit: 100 }), null);
});

test('late generation for an old serial, closed or expired dialog is ignored', async () => {
    const pending = [];
    const { c, h } = await opened({ [`POST ${SUGGEST}`]: () => { const d = deferred(); pending.push(d); return d.promise; } });
    const first = c.generate();
    assert.equal(c.state.phase, 'generating');
    assert.equal(c.state.statusText, 'Generating suggestions…');
    assert.equal(c.state.canSave, false);
    c.setModel('wd14-vit');
    assert.equal(c.state.phase, 'ready');
    assert.equal(h.of('POST', SUGGEST)[0].aborted, true);
    await settle();
    pending[0].resolve(response(200, suggested()));
    assert.equal(await first, false);
    assert.equal(c.state.phase, 'ready', 'the superseded request neither stages chips nor reports its abort');
    assert.equal(c.state.statusText, '');
    assert.deepEqual(c.state.suggestions, []);
    assert.equal(c.state.countText, 'Not generated');

    const second = c.generate();
    c.cancel();
    await settle();
    pending[1].resolve(response(200, suggested()));
    assert.equal(await second, false);
    assert.deepEqual(c.state.suggestions, []);
    assert.equal(c.state.closed, true);
    assert.equal(await c.generate(), false, 'a closed dialog never generates');

    const expired = await opened({ [`POST ${SUGGEST}`]: () => fail(409, 'capture-expired', 'The preview expired. Open the Tag dialog again.') });
    await expired.c.generate();
    assert.equal(expired.c.state.phase, 'capture-expired');
    assert.equal(expired.c.state.canSave, false);
    assert.equal(expired.c.state.canGenerate, false);
});

test('a reopen while observing ignores the older observation and releases its capture', async () => {
    const { createImageTaggingController } = await load();
    const answers = [deferred(), deferred()];
    let n = 0;
    const h = harness(defaultRoutes({ [`POST ${OBSERVE}`]: () => answers[n++].promise }));
    const c = createImageTaggingController({ api: h.api, target: TARGET });
    const one = c.open();
    const two = c.open();
    answers[1].resolve(response(200, observed({}, { token: TOKEN2 })));
    await two;
    answers[0].resolve(response(200, observed()));
    await one;
    await settle();
    assert.equal(c.state.image.previewUrl, CAPTURE + TOKEN2);
    assert.deepEqual(h.of('DELETE', CAPTURE).map(call => call.url), [CAPTURE + TOKEN]);
    c.dispose();
    assert.deepEqual(h.of('DELETE', CAPTURE).map(call => call.url), [CAPTURE + TOKEN, CAPTURE + TOKEN2]);
});

test('primary label toggles Save manual tags / Approve selected tags', async () => {
    const { c } = await opened();
    assert.equal(c.state.primaryLabel, 'Save manual tags');
    await c.generate();
    assert.equal(c.state.primaryLabel, 'Approve selected tags'); c.setModel('wd14-vit'); assert.equal(c.state.primaryLabel, 'Save manual tags');
    assert.equal(c.state.countText, 'Not generated');
});

test('remove drops one chip and the last removal shows the all-removed copy', async () => {
    const { c } = await opened();
    await c.generate();
    c.remove('editorial');
    assert.deepEqual(c.state.suggestions.map(row => row.name), ['minimal', 'sandstone']);
    assert.equal(c.state.countText, '2 suggestions');
    c.remove('missing');
    assert.equal(c.state.suggestions.length, 2);
    c.remove('minimal');
    c.remove('sandstone');
    assert.deepEqual(c.state.suggestions, []);
    assert.equal(c.state.suggestionsText, 'All suggestions removed. Generate again or enter tags manually.');
    assert.equal(c.state.primaryLabel, 'Approve selected tags');
});

test('save sends manual then remaining chips deduplicated case-insensitively keeping the manual spelling', async () => {
    const { createImageTaggingController } = await load();
    const h = harness(defaultRoutes({
        [`POST ${SUGGEST}`]: () => response(200, suggested([
            { name: 'minimal', confidence: 0.99 }, { name: 'editorial', confidence: 0.97 },
            { name: 'sandstone', confidence: 0.96 }, { name: 'desert', confidence: 0.9 },
        ])),
    }));
    const c = createImageTaggingController({ api: h.api, target: TARGET, source: 'review' });
    await c.open();
    await settle();
    assert.equal(c.state.showScopeNote, true);
    await c.generate();
    c.remove('desert');
    assert.equal(c.state.primaryLabel, 'Approve selected tags');
    assert.equal(await c.save(), true);
    const sent = h.of('PUT', TAGS)[0].body;
    assert.deepEqual(sent.tags, ['Minimal', 'warm_light', 'editorial', 'sandstone']); // manual "Minimal", chip "minimal" dropped
    assert.deepEqual(sent, {
        target: { ...TARGET }, capture_token: TOKEN, sha256: SHA, expected_epoch: EPOCH,
        expected_tag_revision: 3, tags: sent.tags,
    });
    assert.equal(c.state.phase, 'saved');
    assert.deepEqual(c.state.savedTags, sent.tags);
    assert.equal(c.state.tagRevision, 4);
    assert.equal(c.state.toast, '4 tags saved for dunes_00001.png. Prompt association still awaits review.');
    assert.equal(c.state.canSave, false);
    assert.equal(await c.save(), false, 'a saved dialog does not save twice');
});

test('toast matches the mock with the review suffix', async () => {
    const { c } = await opened({ [`PUT ${TAGS}`]: () => response(200, receipt(['one'])) });
    c.setManual('one');
    await c.save();
    assert.equal(c.state.toast, '1 tags saved for dunes_00001.png.', 'the mock always says "tags"');
    const many = await opened({}, { source: 'library' });
    await many.c.save();
    assert.equal(many.c.state.toast, '2 tags saved for dunes_00001.png.');
});

test('empty manual and no chips sends the acknowledged empty set', async () => {
    const { c, h } = await opened({ [`PUT ${TAGS}`]: () => response(200, receipt([])) });
    c.setManual('  ,  ');
    await c.save();
    assert.deepEqual(h.of('PUT', TAGS)[0].body.tags, []);
    assert.equal(c.state.toast, '0 tags saved for dunes_00001.png.');
});

test('conflict keeps draft and exposes current saved tags', async () => {
    let answer = () => fail(409, 'tag-conflict', 'The tags of this image changed since the dialog opened; review them first.',
        { tags: ['night', 'rain'], tag_revision: 5 });
    const { c, h } = await opened({ [`PUT ${TAGS}`]: call => answer(call) });
    await c.generate();
    c.setManual('Minimal, dune');
    await c.save();
    assert.equal(c.state.phase, 'conflict');
    assert.deepEqual(c.state.currentTags, ['night', 'rain']);
    assert.equal(c.state.manual, 'night, rain, Minimal, dune');
    assert.equal(c.state.suggestions.length, 3);
    assert.equal(c.state.statusText, 'The tags of this image changed since the dialog opened; review them first. Saved now: night, rain.');
    assert.equal(c.state.canSave, true);
    answer = call => response(200, receipt(call.body.tags, { tag_revision: 6 }));
    await c.save();
    assert.equal(h.of('PUT', TAGS)[1].body.expected_tag_revision, 5, 'the second save is against the revision the user has seen');
    assert.equal(c.state.phase, 'saved');
    assert.equal(c.state.currentTags, null);
});

test('lost response keeps draft and retry resends the identical body once', async () => {
    const answers = [() => Promise.reject(new TypeError('Failed to fetch')), () => fail(504, null, 'Gateway Timeout'),
        () => response(200, { success: true, tags: 'broken' })];
    for (const lost of answers) {
        let answer = lost;
        const { c, h } = await opened({ [`PUT ${TAGS}`]: call => answer(call) });
        c.setManual('Minimal, dune');
        await c.save();
        assert.equal(c.state.phase, 'unknown-outcome');
        assert.equal(c.state.statusText, 'The save could not be confirmed. Retry sends the same tags again.');
        assert.equal(c.state.canRetry, true);
        assert.equal(c.state.canSave, false);
        assert.equal(c.state.manual, 'Minimal, dune');
        c.setManual('changed meanwhile');
        const gate = deferred();
        answer = call => gate.promise.then(() => response(200, receipt(call.body.tags, { replayed: true })));
        const retried = c.retry();
        assert.equal(await c.retry(), false, 'a retry in flight is not sent twice');
        gate.resolve();
        assert.equal(await retried, true);
        const puts = h.of('PUT', TAGS);
        assert.equal(puts.length, 2);
        assert.deepEqual(puts[1].body, puts[0].body);
        assert.equal(c.state.phase, 'saved');
        assert.equal(await c.retry(), false);
    }
});

test('write errors keep the draft and allow another save', async () => {
    const { c } = await opened({ [`PUT ${TAGS}`]: () => fail(400, 'tag-limit', 'An image can carry at most 100 tags.') });
    await c.save();
    assert.equal(c.state.phase, 'write-error');
    assert.equal(c.state.statusText, 'An image can carry at most 100 tags.');
    assert.equal(c.state.canSave, true);
    assert.equal(c.state.canRetry, false);
    const generic = await opened({ [`PUT ${TAGS}`]: () => fail(500, 'save-failed', '') });
    await generic.c.save();
    assert.equal(generic.c.state.phase, 'write-error');
    assert.equal(generic.c.state.statusText, 'The tags could not be saved.');
});

test('storage-changed and file-changed disable save until reopen', async () => {
    for (const [code, phase] of [['storage-changed', 'storage-changed'], ['file-changed', 'file-changed'], ['source-changed', 'file-changed'], ['capture-expired', 'capture-expired']]) {
        const message = `server says ${code}`;
        let answer = () => fail(409, code, message);
        const { c, h } = await opened({ [`PUT ${TAGS}`]: () => answer() });
        c.setManual('kept draft');
        await c.save();
        assert.equal(c.state.phase, phase);
        assert.equal(c.state.statusText, message);
        assert.equal(c.state.canSave, false);
        assert.equal(c.state.canGenerate, false);
        assert.equal(await c.save(), false);
        assert.equal(h.of('PUT', TAGS).length, 1);
        await c.open();
        await settle();
        assert.equal(c.state.phase, 'ready', 'reopen observes again');
        assert.equal(c.state.manual, 'Minimal, warm_light');
        assert.equal(h.of('DELETE', CAPTURE).length, 1, 'reopen releases the old capture');
    }
});

test('video disables generate with the still-image message', async () => {
    const reason = 'Auto-tagging is available for supported still images only.';
    const { c, h } = await opened({
        [`POST ${OBSERVE}`]: () => response(200, observed({ filename: 'clip.mp4' }, { media_type: 'video', generate: { supported: false, reason } })),
    });
    assert.equal(c.state.image.mediaType, 'video');
    assert.equal(c.state.canGenerate, false);
    assert.equal(c.state.canSave, true);
    assert.equal(c.state.statusText, reason);
    c.setModel('gguf');
    assert.equal(c.state.statusText, reason, 'the reason survives a model change');
    assert.equal(await c.generate(), false);
    assert.equal(h.of('POST', SUGGEST).length, 0);
});

test('model-unavailable and model-busy set the status line only', async () => {
    let answer = () => response(200, suggested());
    const { c } = await opened({ [`POST ${SUGGEST}`]: () => answer() });
    await c.generate();
    const before = c.state;
    for (const [code, message] of [
        ['model-unavailable', 'WD14 SwinV2 is not downloaded. Download it in System › Auto-tagging.'],
        ['model-busy', 'A tagging model is busy with another operation. Try again when it finishes.'],
        ['unsupported-media', 'Images larger than 4096 px per side or 16,777,216 px are not supported.'],
    ]) {
        answer = () => fail(409, code, message);
        assert.equal(await c.generate(), false);
        assert.equal(c.state.statusText, message);
        assert.deepEqual(c.state.suggestions, before.suggestions);
        assert.equal(c.state.countText, before.countText);
        assert.equal(c.state.primaryLabel, before.primaryLabel);
        assert.equal(c.state.manual, before.manual);
        assert.equal(c.state.canSave, true);
        assert.equal(c.state.canGenerate, true);
    }
    answer = () => Promise.reject(new TypeError('Failed to fetch'));
    await c.generate();
    assert.equal(c.state.statusText, 'Auto-tagging failed. Check server logs for details.');
    c.setModel('wd14-vit');
    assert.equal(c.state.phase, 'ready');
});

test('a storage restore during generate requires a reopen', async () => {
    const { c } = await opened({ [`POST ${SUGGEST}`]: () => fail(409, 'storage-changed', 'Prompt storage was restored; reopen the Tag dialog.') });
    c.setManual('kept');
    await c.generate();
    assert.equal(c.state.phase, 'storage-changed');
    assert.equal(c.state.canSave, false);
    assert.equal(c.state.canGenerate, false);
    await c.retry();
    await settle();
    assert.equal(c.state.phase, 'ready', 'Retry observes again (D116)');
    assert.equal(c.state.manual, 'kept');
});

test('dispose aborts requests and releases the capture once', async () => {
    const pending = deferred();
    const { c, h } = await opened({ [`POST ${SUGGEST}`]: () => pending.promise });
    const token = TOKEN;
    const releases = [];
    const generating = c.generate();
    let notified = 0;
    c.subscribe(() => { notified += 1; });
    c.dispose();
    c.dispose();
    c.cancel();
    for (const call of h.of('DELETE', CAPTURE)) releases.push(decodeURIComponent(call.url.slice(CAPTURE.length)));
    assert.deepEqual(releases, [token]);
    assert.equal(h.of('POST', SUGGEST)[0].aborted, true);
    pending.resolve(response(200, suggested()));
    assert.equal(await generating, false);
    assert.equal(notified, 0, 'a disposed controller never notifies');
    c.setManual('ignored');
    c.remove('x');
    assert.notEqual(c.state.manual, 'ignored');
    assert.equal(await c.save(), false);
    assert.equal(await c.open(), undefined);
    assert.equal(h.of('POST', OBSERVE).length, 1);
});

test('dispose while observing aborts the models read and releases a late capture', async () => {
    const { createImageTaggingController } = await load();
    const late = deferred();
    const h = harness(defaultRoutes({ [`POST ${OBSERVE}`]: () => late.promise, [`GET ${MODELS}`]: () => new Promise(() => {}) }));
    const c = createImageTaggingController({ api: h.api, target: TARGET });
    const opening = c.open();
    c.dispose();
    assert.equal(h.of('GET', MODELS)[0].aborted, true);
    late.resolve(response(200, observed()));
    await opening;
    await settle();
    assert.deepEqual(h.of('DELETE', CAPTURE).map(call => call.url), [CAPTURE + TOKEN]);
    assert.equal(c.state.phase, 'observing');
});

test('a failing capture release is not reported as a dialog error', async () => {
    const { c, h } = await opened({ [`DELETE ${CAPTURE}`]: () => Promise.reject(new TypeError('offline')) });
    c.dispose();
    await settle();
    assert.equal(h.of('DELETE', CAPTURE).length, 1);
    assert.equal(c.state.phase, 'ready');
});

test('the opening sentence is the concept on-screen copy and the chip hint shows only with chips (D83)', async () => {
    const { createImageTaggingController } = await load();
    const h = harness(defaultRoutes());
    const c = createImageTaggingController({ api: h.api, target: TARGET });
    assert.equal(c.state.suggestionsText, 'Generate suggestions with the selected model before approving.');
    assert.equal(c.state.chipHint, '');
    await c.open();
    await settle();
    assert.equal(c.state.suggestionsText, 'Generate suggestions with the selected model before approving.');
    assert.equal(c.state.chipHint, '');
    await c.generate();
    assert.equal(c.state.chipHint, 'Remove anything you do not want to add.');
    for (const row of [...c.state.suggestions]) c.remove(row.name);
    assert.equal(c.state.chipHint, '');
});

test('the option fields follow the server defaults: open and a model change bump the revision, typing does not', async () => {
    const { c, h } = await opened({
        [`GET ${MODELS}`]: () => response(200, models({ suggestion_limit: 7, wd14_general_threshold: 0.5, wd14_character_threshold: 0.9 })),
    });
    const opening = c.state.optionsRevision;
    assert.ok(opening > 0, 'the observation that applies the server defaults bumps the revision');
    assert.deepEqual({ ...c.state.options }, { general_threshold: 0.5, character_threshold: 0.9, instructions: DEFAULT_PROMPT, suggestion_limit: 7 });
    c.setOption('general_threshold', '0.6');
    assert.equal(c.state.optionsRevision, opening, 'typing never re-renders the fields');
    c.setModel('wd14-vit');
    assert.equal(c.state.optionsRevision, opening + 1);
    assert.equal(c.state.options.general_threshold, 0.5);
    await c.generate();
    assert.deepEqual(h.of('POST', SUGGEST)[0].body.options, { general_threshold: 0.5, character_threshold: 0.9, suggestion_limit: 7 });
});

test('a conflict shows the current set and merges it ahead of the draft, so a second Save never drops it unseen', async () => {
    let answer = () => fail(409, 'tag-conflict', 'The tags of this image changed since the dialog opened; review them first.',
        { tags: ['changed_elsewhere', 'Minimal'], tag_revision: 5 });
    const { c, h } = await opened({ [`PUT ${TAGS}`]: call => answer(call) });
    const draft = c.state.draftRevision;
    c.setManual('minimal, dune');
    await c.save();
    assert.equal(c.state.phase, 'conflict');
    assert.equal(c.state.statusText,
        'The tags of this image changed since the dialog opened; review them first. Saved now: changed_elsewhere, Minimal.');
    assert.equal(c.state.manual, 'changed_elsewhere, Minimal, dune', 'the current set leads, the draft follows, repeats dropped');
    assert.equal(c.state.draftRevision, draft + 1, 'the dialog rewrites its field');
    answer = call => response(200, receipt(call.body.tags, { tag_revision: 6 }));
    await c.save();
    const second = h.of('PUT', TAGS)[1].body;
    assert.deepEqual(second.tags, ['changed_elsewhere', 'Minimal', 'dune']);
    assert.equal(second.expected_tag_revision, 5);
    const empty = await opened({ [`PUT ${TAGS}`]: () => fail(409, 'tag-conflict', 'Changed.', { tags: [], tag_revision: 9 }) });
    empty.c.setManual('a');
    await empty.c.save();
    assert.equal(empty.c.state.statusText, 'Changed. Saved now: no tags.');
    assert.equal(empty.c.state.manual, 'a');
});

test('the capture release and the save survive leaving the page (keepalive)', async () => {
    const { c, h } = await opened();
    await c.save();
    assert.equal(h.of('PUT', TAGS)[0].init.keepalive, true);
    const other = await opened();
    other.c.dispose();
    const [release] = other.h.of('DELETE', CAPTURE);
    assert.equal(release.init.keepalive, true);
    assert.equal(h.of('POST', OBSERVE)[0].init.keepalive, undefined, 'reads are not kept alive');
});

test('closing during a save releases the capture only after the PUT settles and reports the outcome', async () => {
    const outcomes = [];
    const gate = deferred();
    const { c, h } = await opened({ [`PUT ${TAGS}`]: call => gate.promise.then(() => response(200, receipt(call.body.tags))) },
        { onLateSave: outcome => outcomes.push(outcome) });
    c.setManual('dune');
    const saving = c.save();
    c.dispose();
    await settle();
    assert.equal(h.of('DELETE', CAPTURE).length, 0, 'the capture stays while its save is in flight');
    gate.resolve();
    assert.equal(await saving, true);
    await settle();
    const order = h.calls.filter(call => call.method !== 'GET').map(call => call.method);
    assert.deepEqual(order, ['POST', 'PUT', 'DELETE']);
    assert.deepEqual(outcomes, [{ saved: true, filename: 'dunes_00001.png', tags: ['dune'], toast: '1 tags saved for dunes_00001.png.' }]);
    assert.equal(c.state.closed, true);

    for (const [route, message] of [
        [() => fail(409, 'tag-conflict', 'Changed elsewhere.', { tags: ['x'], tag_revision: 5 }), 'Changed elsewhere.'],
        [() => fail(400, 'tag-limit', 'Too many.'), 'Too many.'],
        [() => Promise.reject(new TypeError('Failed to fetch')), 'The tag save could not be confirmed. Open Tag image again to check the saved tags.'],
    ]) {
        const late = [];
        const held = deferred();
        const run = await opened({ [`PUT ${TAGS}`]: () => held.promise.then(route) }, { onLateSave: outcome => late.push(outcome) });
        const pending = run.c.save();
        run.c.cancel();
        held.resolve();
        assert.equal(await pending, false);
        await settle();
        assert.deepEqual(late, [{ saved: false, message }]);
        assert.equal(run.h.of('DELETE', CAPTURE).length, 1);
    }
    const quiet = await opened();
    quiet.c.dispose();
    assert.equal(quiet.h.of('DELETE', CAPTURE).length, 1, 'with no save in flight the capture is released at once');
    const { createImageTaggingController } = await load();
    assert.throws(() => createImageTaggingController({ api: h.api, target: TARGET, onLateSave: 'x' }), TypeError);
});

test('retry after a changed storage, file or expired capture observes again and keeps the draft', async () => {
    for (const code of ['storage-changed', 'file-changed', 'capture-expired']) {
        let revision = 3;
        const { c, h } = await opened({
            [`PUT ${TAGS}`]: () => fail(409, code, `server says ${code}`),
            [`POST ${OBSERVE}`]: () => response(200, observed({ tag_revision: revision, tags: revision === 3 ? ['Minimal', 'warm_light'] : ['night'] })),
        });
        c.setManual('kept draft');
        await c.save();
        assert.equal(c.state.canRetry, true);
        const draft = c.state.draftRevision;
        await c.retry();
        await settle();
        assert.equal(c.state.phase, 'ready');
        assert.equal(c.state.manual, 'kept draft', 'an unchanged tag set keeps the draft as typed');
        assert.equal(c.state.draftRevision, draft + 1);
        assert.equal(h.of('POST', OBSERVE).length, 2);
        revision = 4;
        await c.save();
        await c.retry();
        await settle();
        assert.equal(c.state.manual, 'night, kept draft', 'a set changed meanwhile is shown ahead of the draft');
        assert.equal(c.state.statusText, 'The tags of this image changed since the dialog opened. Saved now: night.');
        assert.equal(c.state.tagRevision, 4);
    }
    // A first observation that failed has no draft to keep: its retry fills the saved tags.
    const { createImageTaggingController } = await load();
    let first = true;
    const h = harness(defaultRoutes({ [`POST ${OBSERVE}`]: () => (first ? (first = false, fail(500, 'observe-failed')) : response(200, observed())) }));
    const c = createImageTaggingController({ api: h.api, target: TARGET });
    await c.open();
    await c.retry();
    await settle();
    assert.equal(c.state.manual, 'Minimal, warm_light');
});
