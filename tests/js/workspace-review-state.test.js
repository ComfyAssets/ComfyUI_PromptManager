// Run with: node --test tests/js/workspace-review-state.test.js
const test = require('node:test');
const assert = require('node:assert/strict');
const { createApiClient } = require('../../web/js/api-client.js');

// Only an absent module is tolerated, so RED is an assertion failure, not a crash.
const source = import('../../web/js/workspace/review-state.mjs').catch(error => {
    if (error.code !== 'ERR_MODULE_NOT_FOUND') throw error;
    return {};
});
async function load() {
    const module = await source;
    assert.equal(typeof module.createReviewController, 'function', 'review-state.mjs is not implemented');
    return module;
}

const EPOCH = '11111111-1111-4111-8111-111111111111';
const SCOPE = 'c'.repeat(64);
const SHA = 'd'.repeat(64);
const PRINT = 'e'.repeat(64);
const HASH_A = 'a'.repeat(64);
const HASH_B = 'b'.repeat(64);
const HASH_C = 'f'.repeat(64);
const ITEMS = '/prompt_manager/review/items';
const OBSERVE = '/prompt_manager/image-annotations/observe';
const CAPTURE = '/prompt_manager/image-annotations/capture/';

const flush = () => new Promise(resolve => setImmediate(resolve));
async function settle(times = 8) { for (let i = 0; i < times; i++) await flush(); }
function response(status, body) {
    return { ok: status >= 200 && status < 300, status, statusText: `Status ${status}`, json: async () => body };
}
const fail = (status, code, error = `failure ${code}`, extra = {}) => response(status, { success: false, code, error, ...extra });

function item(id, overrides = {}) {
    const path = `dunes/${id}.png`;
    return {
        id, state: 'pending', revision: 0, created_at: `2026-10-22T10:00:0${id.length}+00:00`, filename: `${id}.png`,
        target: { root_index: 0, root_scope: SCOPE, path }, unavailable: false, candidate_count: 3, limited: false,
        url: `/prompt_manager/images/serve/${path}?root=0&root_scope=${SCOPE}`, is_video: false, ...overrides,
    };
}
const candidate = (hash, sampler, label, positive, negative, state = 'resolved') => ({
    hash, sampler_id: sampler, state, reason: '', label,
    positive: positive === null ? null : { text: positive }, negative: negative === null ? null : { text: negative },
});
function detailOf(entry, overrides = {}) {
    return {
        success: true, library_epoch: EPOCH, ...entry, total: 2, sha256: SHA, dependency_fingerprint: PRINT,
        candidates: [
            candidate(HASH_C, '9', 'No positive / Soft negative', null, 'blur', 'unresolved'),
            candidate(HASH_A, '12', 'Dunes / Soft negative', 'golden dunes', 'blur'),
            candidate(HASH_B, '28', 'Dunes / Harsh negative', 'golden dunes', 'noise'),
        ],
        ...overrides,
    };
}
const page = (items, overrides = {}) => ({ success: true, library_epoch: EPOCH, total: items.length, items, next_cursor: null, ...overrides });
const observed = (token = 'capture-1') => ({
    success: true, library_epoch: EPOCH, target: item('one').target, filename: 'one.png', title: 'one.png', tags: [], tag_revision: 0,
    capture: { token, sha256: SHA, preview_url: CAPTURE + token, size: 10, width: 8, height: 8, media_type: 'image',
        expires_at: '2026-10-22T12:00:00+00:00', generate: { supported: true, reason: null } },
});

/** Real createApiClient over a fetch whose answers come from routes['METHOD path'](call). */
function harness(routes = {}) {
    const calls = [];
    const fetch = (url, init = {}) => {
        const call = { url, method: init.method, body: init.body === undefined ? undefined : JSON.parse(init.body) };
        calls.push(call);
        const path = url.startsWith(CAPTURE) ? CAPTURE : url.split('?')[0];
        const route = routes[`${call.method} ${path}`];
        return new Promise((resolve, reject) => {
            init.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
            Promise.resolve().then(() => (route ? route(call) : response(200, { success: true }))).then(resolve, reject);
        });
    };
    return {
        api: createApiClient(fetch), calls,
        of: (method, prefix) => calls.filter(call => call.method === method && call.url.startsWith(prefix)),
    };
}
const QUEUE = [item('one'), item('two')];
function routes(extra = {}) {
    return {
        [`GET ${ITEMS}`]: () => response(200, page(QUEUE)),
        [`GET ${ITEMS}/one`]: () => response(200, detailOf(QUEUE[0])),
        [`GET ${ITEMS}/two`]: () => response(200, detailOf(QUEUE[1], { candidates: [], limited: true, candidate_count: 0 })),
        [`POST ${OBSERVE}`]: () => response(200, observed()),
        [`POST ${ITEMS}/one/decision`]: call => response(200, { success: true, library_epoch: EPOCH, total: 1,
            item: { ...QUEUE[0], state: call.body.choice === 'unresolved' ? 'unresolved' : 'selected', revision: 1 } }),
        ...extra,
    };
}
async function loaded(extra) {
    const { createReviewController } = await load();
    const h = harness(routes(extra));
    const c = createReviewController({ api: h.api });
    await c.load();
    await settle();
    return { c, h };
}

test('notice titles for 0, 1 and 3', async () => {
    const { noticeTitle } = await load();
    assert.equal(noticeTitle(0), 'No images need a decision');
    assert.equal(noticeTitle(1), '1 image needs a decision');
    assert.equal(noticeTitle(3), '3 images need a decision');
    assert.equal(noticeTitle(1234), '1,234 images need a decision');
});

test('loads pending items and the exact count', async () => {
    const { c, h } = await loaded();
    assert.equal(c.state.status, 'ready');
    assert.equal(c.state.total, 2);
    assert.deepEqual(c.state.items.map(entry => entry.id), ['one', 'two']);
    const [list] = h.of('GET', ITEMS);
    assert.equal(list.url, `${ITEMS}?limit=40`);
    // The first item is selected, as the concept selects its first queue image.
    assert.equal(c.state.selectedId, 'one');
    assert.ok(Object.isFrozen(c.state));
});

test('selecting a card loads detail and option labels, first workable preselected, unusable disabled', async () => {
    const { c } = await loaded();
    assert.equal(c.state.detailStatus, 'ready');
    assert.deepEqual(c.state.options.map(option => [option.label, option.disabled]), [
        ['Sampler 9 · No positive / Soft negative', true],
        ['Sampler 12 · Dunes / Soft negative', false],
        ['Sampler 28 · Dunes / Harsh negative', false],
        ['Leave unresolved', false],
    ]);
    assert.equal(c.state.options.at(-1).value, 'unresolved');
    assert.equal(c.state.choice, HASH_A);
    assert.deepEqual(c.state.prompts, { positive: { status: 'resolved', text: 'golden dunes' }, negative: { status: 'resolved', text: 'blur' } });
    c.choose(HASH_C);
    assert.equal(c.state.choice, HASH_A, 'a disabled option cannot be chosen');
    c.choose(HASH_B);
    assert.equal(c.state.prompts.negative.text, 'noise');
    c.choose('unresolved');
    assert.equal(c.state.prompts.positive.status, 'ambiguous');
    await c.select('two');
    await settle();
    assert.equal(c.state.selectedId, 'two');
    assert.deepEqual(c.state.options.map(option => option.label), ['Leave unresolved']);
    assert.equal(c.state.choice, 'unresolved');
    assert.match(c.state.warning, /too many sampler paths/i);
});

test('apply sends the selected candidate hash or unresolved with a fresh capture', async () => {
    const { c, h } = await loaded();
    c.choose(HASH_B);
    const done = await c.apply();
    assert.equal(done, true);
    const [observe] = h.of('POST', OBSERVE);
    assert.deepEqual(observe.body, { target: QUEUE[0].target });
    const [decision] = h.of('POST', `${ITEMS}/one/decision`);
    assert.deepEqual(decision.body, { expected_epoch: EPOCH, expected_revision: 0, capture_token: 'capture-1', sha256: SHA,
        dependency_fingerprint: PRINT, choice: HASH_B });
    assert.equal(c.state.phase, 'done');
    assert.equal(c.state.total, 1);
    assert.equal(c.state.toast, 'Association accepted.');
    // The server releases a used capture itself.
    assert.equal(h.of('DELETE', CAPTURE).length, 0);

    const again = await loaded();
    again.c.choose('unresolved');
    assert.equal(await again.c.apply(), true);
    const [unresolved] = again.h.of('POST', `${ITEMS}/one/decision`);
    assert.equal(unresolved.body.choice, 'unresolved');
    assert.equal(unresolved.body.capture_token, 'capture-1');
    assert.equal(again.c.state.toast, 'Left unresolved.');
});

test('an unavailable file can only be left unresolved, without a capture', async () => {
    const gone = item('one', { target: null, unavailable: true, url: null });
    const { c, h } = await loaded({
        [`GET ${ITEMS}`]: () => response(200, page([gone])),
        [`GET ${ITEMS}/one`]: () => response(200, detailOf(gone)),
    });
    assert.ok(c.state.options.slice(0, -1).every(option => option.disabled));
    assert.equal(c.state.choice, 'unresolved');
    assert.match(c.state.warning, /not under a current image source/i);
    assert.equal(await c.apply(), true);
    assert.equal(h.of('POST', OBSERVE).length, 0);
    const [decision] = h.of('POST', `${ITEMS}/one/decision`);
    assert.equal(decision.body.capture_token, null);
    assert.equal(decision.body.sha256, SHA);
});

test('association conflict keeps the selection and shows the inline warning', async () => {
    const message = 'This image is already linked to another prompt or negative, so this sampler path cannot be attached safely.';
    const { c, h } = await loaded({ [`POST ${ITEMS}/one/decision`]: () => fail(409, 'association-conflict', message) });
    assert.equal(await c.apply(), false);
    assert.equal(c.state.selectedId, 'one');
    assert.equal(c.state.choice, HASH_A);
    assert.equal(c.state.phase, 'idle');
    assert.equal(c.state.warning, message);
    // The refused decision's capture is released.
    assert.equal(h.of('DELETE', CAPTURE).length, 1);
    assert.equal(h.of('GET', `${ITEMS}?`).length, 1, 'no reload');
});

test('storage-changed requires reload', async () => {
    let lists = 0;
    const { c, h } = await loaded({
        [`GET ${ITEMS}`]: () => { lists += 1; return response(200, page(QUEUE)); },
        [`POST ${ITEMS}/one/decision`]: () => fail(409, 'storage-changed', 'Prompt storage was restored; reload the review queue.'),
    });
    assert.equal(await c.apply(), false);
    await settle();
    assert.equal(lists, 2, 'the queue is read again under the new epoch');
    assert.equal(c.state.warning, 'Prompt storage was restored; reload the review queue.');
    assert.equal(c.state.canRetry, false);
    assert.equal(h.of('DELETE', CAPTURE).length, 1);
});

test('lost response keeps the selection and retry resends once', async () => {
    let attempts = 0;
    const { c, h } = await loaded({
        [`POST ${ITEMS}/one/decision`]: call => {
            attempts += 1;
            if (attempts === 1) return Promise.reject(new TypeError('Failed to fetch'));
            return response(200, { success: true, library_epoch: EPOCH, total: 1, item: { ...QUEUE[0], state: 'selected', revision: 1 } });
        },
    });
    assert.equal(await c.apply(), false);
    assert.equal(c.state.selectedId, 'one');
    assert.equal(c.state.canRetry, true);
    assert.match(c.state.warning, /may not have been saved/i);
    assert.equal(h.of('DELETE', CAPTURE).length, 0, 'the capture is kept for the retry');
    const [first] = h.of('POST', `${ITEMS}/one/decision`);
    assert.equal(await c.retry(), true);
    const sent = h.of('POST', `${ITEMS}/one/decision`);
    assert.equal(sent.length, 2);
    assert.deepEqual(sent[1].body, first.body, 'the identical decision is resent');
    assert.equal(h.of('POST', OBSERVE).length, 1, 'no second capture');
    assert.equal(await c.retry(), false, 'nothing left to resend');
    assert.equal(h.of('POST', `${ITEMS}/one/decision`).length, 2);
});

test('a retry answered by review-conflict reloads the queue', async () => {
    let attempts = 0, lists = 0;
    const { c } = await loaded({
        [`GET ${ITEMS}`]: () => { lists += 1; return response(200, page(lists === 1 ? QUEUE : [QUEUE[1]])); },
        [`POST ${ITEMS}/one/decision`]: () => {
            attempts += 1;
            if (attempts === 1) return Promise.reject(new TypeError('Failed to fetch'));
            return fail(409, 'review-conflict', 'This item was decided or changed since it was opened. Reload the queue.');
        },
    });
    await c.apply();
    assert.equal(await c.retry(), false);
    await settle();
    assert.equal(lists, 2);
    assert.equal(c.state.selectedId, 'two');
});

test('a review-conflict on a still pending item reads its detail again', async () => {
    let details = 0, decisions = 0;
    const { c, h } = await loaded({
        [`GET ${ITEMS}/one`]: () => { details += 1; return response(200, detailOf({ ...QUEUE[0], revision: details - 1 })); },
        [`POST ${ITEMS}/one/decision`]: () => {
            decisions += 1;
            return decisions === 1 ? fail(409, 'review-conflict', 'This item was decided or changed since it was opened. Reload the queue.')
                : response(200, { success: true, library_epoch: EPOCH, total: 1, item: { ...QUEUE[0], state: 'selected', revision: 2 } });
        },
    });
    assert.equal(await c.apply(), false);
    await settle();
    assert.equal(details, 2, 'the item is read again');
    assert.equal(c.state.detail.revision, 1);
    assert.equal(c.state.warning, 'This item was decided or changed since it was opened. Reload the queue.');
    assert.equal(await c.apply(), true);
    assert.equal(h.of('POST', `${ITEMS}/one/decision`).at(-1).body.expected_revision, 1);
});

test('leave for later writes nothing', async () => {
    const { c, h } = await loaded();
    c.leave();
    assert.equal(c.state.phase, 'done');
    assert.equal(c.state.toast, null);
    assert.equal(h.of('POST', '/').length, 0);
    assert.equal(h.of('DELETE', '/').length, 0);
});

test('load more pages with the cursor and keeps the selection', async () => {
    const more = [item('three')];
    const { c, h } = await loaded({
        [`GET ${ITEMS}`]: call => response(200, call.url.includes('cursor=')
            ? page(more, { total: 3 }) : page(QUEUE, { total: 3, next_cursor: 'abc_-1' })),
    });
    assert.equal(c.state.nextCursor, 'abc_-1');
    await c.loadMore();
    assert.equal(h.of('GET', ITEMS).at(-1).url, `${ITEMS}?limit=40&cursor=abc_-1`);
    assert.deepEqual(c.state.items.map(entry => entry.id), ['one', 'two', 'three']);
    assert.equal(c.state.nextCursor, null);
    assert.equal(c.state.selectedId, 'one');
});

test('a failed queue read is an error state and load retries', async () => {
    let lists = 0;
    const { c } = await loaded({ [`GET ${ITEMS}`]: () => { lists += 1; return lists === 1 ? fail(500, 'review-failed') : response(200, page(QUEUE)); } });
    assert.equal(c.state.status, 'error');
    await c.load();
    await settle();
    assert.equal(c.state.status, 'ready');
});

test('tag save toast carries the review suffix and the item stays pending', async () => {
    const { c, h } = await loaded({
        ['GET /prompt_manager/autotag/models']: () => response(200, { success: true, models: {}, default_prompt: 'x',
            wd14_general_threshold: 0.35, wd14_character_threshold: 0.85, suggestion_limit: 10 }),
        ['PUT /prompt_manager/image-annotations/tags']: call => response(200, { success: true, library_epoch: EPOCH, sha256: SHA,
            tags: call.body.tags, tag_revision: 1, replayed: false }),
    });
    const { createImageTaggingController } = await import('../../web/js/workspace/image-tagging-state.mjs');
    const before = h.calls.filter(call => call.url.startsWith(ITEMS)).length;
    // The Tag dialog opened from Needs review is the shared controller with the review source.
    const tagging = createImageTaggingController({ api: h.api, target: QUEUE[0].target, source: 'review' });
    assert.equal(tagging.state.showScopeNote, true);
    await tagging.open();
    await settle();
    tagging.setManual('dunes');
    await tagging.save();
    await settle();
    assert.equal(tagging.state.toast, '1 tags saved for one.png. Prompt association still awaits review.');
    tagging.dispose();
    assert.equal(h.calls.filter(call => call.url.startsWith(ITEMS)).length, before, 'a tag save neither decides nor re-reads the queue');
    assert.deepEqual(c.state.items.map(entry => [entry.id, entry.state]), [['one', 'pending'], ['two', 'pending']]);
    assert.equal(c.state.phase, 'idle');
});

test('dispose releases a kept capture and stops reads', async () => {
    const { c, h } = await loaded({ [`POST ${ITEMS}/one/decision`]: () => Promise.reject(new TypeError('Failed to fetch')) });
    await c.apply();
    c.dispose();
    await settle();
    const released = h.of('DELETE', CAPTURE);
    assert.equal(released.length, 1);
    assert.equal(released[0].url, CAPTURE + 'capture-1');
    assert.equal(await c.retry(), false);
});

// F2: an open page re-reads its queue when the server says it changed (the review change stream).
function refreshOf(c) {
    assert.equal(typeof c.refresh, 'function', 'refresh is not implemented');
    return c.refresh();
}
test('refresh re-reads the queue quietly and keeps the selection and choice', async () => {
    let queue = QUEUE;
    const { c, h } = await loaded({ [`GET ${ITEMS}`]: () => response(200, page(queue)) });
    c.choose(HASH_B);
    const statuses = [];
    c.subscribe(state => statuses.push(state.status));
    queue = [...QUEUE, item('three')];
    await refreshOf(c);
    await settle();
    assert.deepEqual(c.state.items.map(entry => entry.id), ['one', 'two', 'three']);
    assert.equal(c.state.total, 3);
    assert.equal(c.state.selectedId, 'one');
    assert.equal(c.state.choice, HASH_B);
    assert.equal(c.state.warning, '');
    assert.ok(statuses.every(status => status === 'ready'), `no loading state: ${statuses}`);
    assert.equal(h.of('GET', `${ITEMS}/one`).length, 1, 'the open item is not read again');
});

test('refreshes asked for during a refresh collapse into one more read', async () => {
    let release;
    const held = new Promise(resolve => { release = resolve; });
    let lists = 0;
    const { c, h } = await loaded({ [`GET ${ITEMS}`]: async () => {
        lists += 1;
        if (lists === 2) await held;
        return response(200, page(QUEUE));
    } });
    const first = refreshOf(c);
    await settle();
    refreshOf(c);
    refreshOf(c);
    release();
    await first;
    await settle();
    assert.equal(h.of('GET', `${ITEMS}?`).length, 3, 'the load, the refresh and one collapsed re-read');
    assert.equal(c.state.status, 'ready');
});

test('a refresh after the open item was decided elsewhere opens the next one and says so', async () => {
    let queue = QUEUE;
    const { c, h } = await loaded({
        [`GET ${ITEMS}`]: () => response(200, page(queue)),
        [`GET ${ITEMS}/three`]: () => response(200, detailOf(item('three'))),
    });
    queue = [item('three')];
    await refreshOf(c);
    await settle();
    assert.equal(c.state.selectedId, 'three');
    assert.equal(c.state.detailStatus, 'ready');
    assert.equal(c.state.warning, 'one.png was decided elsewhere, so the next image is open.');
    assert.equal(h.of('GET', `${ITEMS}/three`).length, 1);
});

test('a refresh to an empty queue clears the selection', async () => {
    let queue = QUEUE;
    const { c } = await loaded({ [`GET ${ITEMS}`]: () => response(200, page(queue)) });
    queue = [];
    await refreshOf(c);
    await settle();
    assert.equal(c.state.total, 0);
    assert.deepEqual(c.state.items, []);
    assert.equal(c.state.selectedId, null);
    assert.equal(c.state.status, 'ready');
});

test('a failed refresh keeps the queue and says so', async () => {
    let lists = 0;
    const { c } = await loaded({ [`GET ${ITEMS}`]: () => { lists += 1; return lists === 1 ? response(200, page(QUEUE)) : fail(500, 'review-failed'); } });
    await refreshOf(c);
    await settle();
    assert.equal(c.state.status, 'ready');
    assert.deepEqual(c.state.items.map(entry => entry.id), ['one', 'two']);
    assert.equal(c.state.warning, 'The review queue could not be read again. Reload the page to see the latest images.');
});

test('dispose during a decision releases its capture', async () => {
    const { c, h } = await loaded({ [`POST ${ITEMS}/one/decision`]: () => new Promise(() => {}) });
    const applying = c.apply();
    await settle();
    assert.equal(c.state.phase, 'applying');
    c.dispose();
    assert.equal(await applying, false);
    await settle();
    assert.deepEqual(h.of('DELETE', CAPTURE).map(call => call.url), [CAPTURE + 'capture-1']);
    await refreshOf(c);
    assert.equal(h.of('GET', `${ITEMS}?`).length, 1, 'a disposed controller reads nothing');
});

test('a change heard during the first read re-reads once after it, and a failed first read stays an error', async () => {
    const { createReviewController } = await load();
    let release;
    let lists = 0;
    const h = harness(routes({ [`GET ${ITEMS}`]: async () => {
        lists += 1;
        if (lists === 1) await new Promise(resolve => { release = resolve; });
        return response(200, page(QUEUE));
    } }));
    const c = createReviewController({ api: h.api });
    const first = c.load();
    await settle();
    refreshOf(c);
    assert.equal(lists, 1, 'the first read is not interrupted');
    release();
    await first;
    await settle();
    assert.equal(lists, 2, 'one re-read after the first read');
    assert.equal(c.state.status, 'ready');

    const failing = harness(routes({ [`GET ${ITEMS}`]: () => fail(500, 'review-failed') }));
    const broken = createReviewController({ api: failing.api });
    const reading = broken.load();
    refreshOf(broken);
    await reading;
    await settle();
    assert.equal(broken.state.status, 'error');
    assert.equal(failing.of('GET', `${ITEMS}?`).length, 1, 'Retry is the way back, not a re-read');
});

// F3 rulings: a live re-read keeps the loaded depth, and the stream's `ready` reads nothing when the first read is current.

/** A keyset pager over `source()`: pages of 40 with an index cursor, each answering `version()`. */
function pager(source, version = () => 0, { onPage } = {}) {
    return call => {
        const query = new URL(call.url, 'http://x').searchParams;
        const cursor = query.get('cursor');
        const all = source();
        const from = cursor ? Number(cursor.slice(1)) : 0;
        const answer = onPage?.(cursor);
        if (answer) return answer;
        const items = all.slice(from, from + 40);
        const next = from + 40 < all.length ? `c${from + 40}` : null;
        return response(200, page(items, { total: all.length, next_cursor: next, review_revision: version() }));
    };
}
const many = (count, prefix = 'i') => Array.from({ length: count }, (_, index) => item(`${prefix}${String(index).padStart(3, '0')}`));
function syncOf(c, version) {
    assert.equal(typeof c.sync, 'function', 'sync is not implemented');
    return c.sync(version);
}
const version = revision => ({ library_epoch: EPOCH, review_revision: revision });

test('a refresh keeps the loaded depth: it re-reads as many items as were loaded', async () => {
    let queue = many(100);
    const { c, h } = await loaded({ [`GET ${ITEMS}`]: pager(() => queue) });
    await c.loadMore();
    assert.equal(c.state.items.length, 80);
    const before = h.of('GET', `${ITEMS}?`).length;
    queue = [...queue, item('late')];
    await refreshOf(c);
    await settle();
    assert.equal(c.state.items.length, 80, 'the Load more page is kept');
    assert.deepEqual(c.state.items.map(entry => entry.id), queue.slice(0, 80).map(entry => entry.id));
    assert.equal(c.state.total, 101);
    assert.equal(c.state.nextCursor, 'c80', 'Load more continues after the kept depth');
    assert.deepEqual(h.of('GET', `${ITEMS}?`).slice(before).map(call => call.url),
        [`${ITEMS}?limit=40`, `${ITEMS}?limit=40&cursor=c40`]);
    assert.equal(c.state.selectedId, 'i000');
});

test('a refresh after decisions elsewhere fills the loaded depth from what is left', async () => {
    let queue = many(85);
    const { c } = await loaded({ [`GET ${ITEMS}`]: pager(() => queue) });
    await c.loadMore();
    await c.loadMore();
    assert.equal(c.state.items.length, 85);
    queue = queue.filter((_, index) => index % 10 !== 5);
    await refreshOf(c);
    await settle();
    assert.equal(c.state.items.length, queue.length);
    assert.equal(c.state.nextCursor, null);
});

test('a deep refresh whose next page meets a restore reads again from the first page', async () => {
    let restored = false;
    let queue = many(60);
    const { c, h } = await loaded({ [`GET ${ITEMS}`]: pager(() => queue, () => 0, {
        onPage: cursor => {
            if (cursor && restored) { restored = false; return fail(409, 'storage-changed', 'The library was restored.'); }
            return null;
        },
    }) });
    await c.loadMore();
    const before = h.of('GET', `${ITEMS}?`).length;
    restored = true;
    queue = many(60, 'r');
    await refreshOf(c);
    await settle();
    assert.deepEqual(h.of('GET', `${ITEMS}?`).slice(before).map(call => call.url),
        [`${ITEMS}?limit=40`, `${ITEMS}?limit=40&cursor=c40`, `${ITEMS}?limit=40`, `${ITEMS}?limit=40&cursor=c40`]);
    assert.equal(c.state.items.length, 60);
    assert.equal(c.state.items[0].id, 'r000');
    assert.notEqual(c.state.warning, 'The review queue could not be read again. Reload the page to see the latest images.',
        'a restore met mid-read is not a failed re-read');
    assert.equal(c.state.status, 'ready');
});

test('ready naming the version the first read answered reads nothing more', async () => {
    const { c, h } = await loaded({ [`GET ${ITEMS}`]: pager(() => QUEUE, () => 4) });
    await syncOf(c, version(4));
    await settle();
    assert.equal(h.of('GET', `${ITEMS}?`).length, 1, 'one list read per visit');
    assert.equal(h.of('GET', `${ITEMS}/one`).length, 1);
});

test('ready naming a newer version, another epoch or no version re-reads once', async () => {
    const { c, h } = await loaded({ [`GET ${ITEMS}`]: pager(() => QUEUE, () => 4) });
    await syncOf(c, version(5));
    await settle();
    assert.equal(h.of('GET', `${ITEMS}?`).length, 2);
    await syncOf(c, { library_epoch: '22222222-2222-4222-8222-222222222222', review_revision: 4 });
    await settle();
    assert.equal(h.of('GET', `${ITEMS}?`).length, 3);
    await syncOf(c, {});
    await settle();
    assert.equal(h.of('GET', `${ITEMS}?`).length, 4, 'an unknown version is read, as before versions existed');
});

test('ready heard during the first read re-reads only when that read is older', async () => {
    const { createReviewController } = await load();
    for (const [readRevision, readyRevision, reads] of [[4, 4, 1], [5, 4, 1], [4, 5, 2]]) {
        let release;
        let lists = 0;
        const answer = pager(() => QUEUE, () => (lists === 1 ? readRevision : readyRevision));
        const h = harness(routes({ [`GET ${ITEMS}`]: async call => {
            lists += 1;
            if (lists === 1) await new Promise(resolve => { release = resolve; });
            return answer(call);
        } }));
        const c = createReviewController({ api: h.api });
        const first = c.load();
        await settle();
        syncOf(c, version(readyRevision));
        release();
        await first;
        await settle();
        assert.equal(lists, reads, `first read r${readRevision}, ready r${readyRevision}`);
        c.dispose();
    }
});

test('a change heard during the first read still re-reads after a current ready', async () => {
    const { createReviewController } = await load();
    let release;
    let lists = 0;
    const h = harness(routes({ [`GET ${ITEMS}`]: async call => {
        lists += 1;
        if (lists === 1) await new Promise(resolve => { release = resolve; });
        return pager(() => QUEUE, () => 4)(call);
    } }));
    const c = createReviewController({ api: h.api });
    const first = c.load();
    await settle();
    syncOf(c, version(4));
    refreshOf(c);
    release();
    await first;
    await settle();
    assert.equal(lists, 2);
});
