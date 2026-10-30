import test from 'node:test';
import assert from 'node:assert/strict';

// #498 (D162): Images › Show removed images › Return to library: the words, the request and the toasts.
const loaded = await import('../../web/js/workspace/images-return.mjs').catch(() => null);
const module = () => { assert.ok(loaded, 'images-return.mjs is not implemented'); return loaded; };
const pageScope = () => {
    const controller = new AbortController();
    return { signal: controller.signal, cleanup: () => {}, abort: () => controller.abort() };
};
const image = (path, root_index = 0) => ({ path, root_index, root_scope: 's0', filename: path });

test('the toggle, the caption and the empty view', () => {
    const { toggleLabel, removedCaption, removedEmpty, RETURN_LABEL } = module();
    assert.equal(toggleLabel(null), 'Show removed images');
    assert.equal(toggleLabel(1234), 'Show removed images (1,234)');
    assert.equal(removedCaption(1), '1 removed image · files on disk, hidden from Images');
    assert.equal(removedCaption(3), '3 removed images · files on disk, hidden from Images');
    assert.equal(removedEmpty(false), 'No removed images.');
    assert.equal(removedEmpty(true), 'No removed images match.');
    assert.equal(RETURN_LABEL, 'Return to library');
});

test('the finished toast says what came back and what did not', () => {
    const { finishedToast, startToast } = module();
    assert.equal(startToast(2), 'Returning 2 images to the library…');
    assert.equal(finishedToast({ state: 'finished', counters: { added: 1 } }, 2),
        'Returned 2 images to the library; 1 image linked to its prompt. Tags, categories, collections and favorites do not come back.');
    assert.equal(finishedToast({ state: 'finished', counters: { added: 3 } }, 3),
        'Returned 3 images to the library; 3 images linked to their prompts. Tags, categories, collections and favorites do not come back.');
    assert.equal(finishedToast({ state: 'finished', counters: {} }, 1),
        'Returned 1 image to the library. Tags, categories, collections and favorites do not come back.');
    assert.match(finishedToast({ state: 'failed', status_line: 'Disk error' }, 1), /^Returning images stopped: Disk error\.$/);
    assert.match(finishedToast({ state: 'cancelled' }, 1), /cancelled/);
});

test('refusals name the cause', () => {
    const { returnFailure, NONE_REMOVED, TOO_MANY, SOURCE_CHANGED, RETURN_FAILED } = module();
    assert.equal(returnFailure({ code: 'no-images' }), NONE_REMOVED);
    assert.equal(returnFailure({ code: 'too-many-images' }), TOO_MANY);
    assert.equal(returnFailure({ code: 'source-changed' }), SOURCE_CHANGED);
    assert.equal(returnFailure({ code: 'busy', message: 'Scan for metadata is running.' }), 'Scan for metadata is running.');
    assert.equal(returnFailure(new Error('x')), RETURN_FAILED);
});

test('over 500 refuses with the toast before any request', async () => {
    const { returnImages, TOO_MANY } = module();
    const toasts = [], posts = [];
    const api = { post: async (...args) => { posts.push(args); return {}; } };
    await returnImages({ api, scope: pageScope(), filter: { q: '' }, count: 501, notify: text => toasts.push(text) });
    assert.deepEqual(toasts, [TOO_MANY]);
    assert.deepEqual(posts, []);
});

test('items post their identities; a filter posts the removed view; the job is tracked and followed', async () => {
    const { returnImages, RETURN_URL } = module();
    const toasts = [], posts = [], ended = [];
    let listener = null;
    const job = { id: 'j1', title: 'Return images to the library', state: 'running', counters: {} };
    const store = {
        track: started => started,
        find: () => job,
        subscribe: fn => { listener = fn; return () => { listener = null; }; },
    };
    const api = { post: async (url, body) => { posts.push([url, body]); return { job, returned: 2, not_removed: 0 }; } };
    const started = await returnImages({ api, scope: pageScope(), store, images: [image('a.png'), image('b.png', 1)],
        notify: text => toasts.push(text), onReturned: done => ended.push(done) });
    assert.equal(started, job);
    assert.deepEqual(posts[0], [RETURN_URL, { items: [
        { root_index: 0, root_scope: 's0', path: 'a.png' }, { root_index: 1, root_scope: 's0', path: 'b.png' }] }]);
    assert.deepEqual(toasts, ['Returning 2 images to the library…']);
    job.state = 'finished'; job.counters = { added: 2 };
    listener();
    assert.deepEqual(ended, [job]);
    assert.match(toasts[1], /^Returned 2 images to the library; 2 images linked/);
    await returnImages({ api, scope: pageScope(), filter: { q: 'x' }, count: 2, notify: () => {} });
    assert.deepEqual(posts[1][1], { filter: { q: 'x', removed: '1' } });
});

test('a refused return toasts, releases the trigger and changes nothing', async () => {
    const { returnImages, NONE_REMOVED } = module();
    const toasts = [];
    const attributes = new Map();
    const trigger = { getAttribute: name => attributes.get(name) ?? null, setAttribute: (name, value) => attributes.set(name, value),
        removeAttribute: name => attributes.delete(name) };
    const api = { post: async () => { throw Object.assign(new Error('no'), { code: 'no-images' }); } };
    const result = await returnImages({ api, scope: pageScope(), images: [image('a.png')], trigger, notify: text => toasts.push(text) });
    assert.equal(result, null);
    assert.deepEqual(toasts, [NONE_REMOVED]);
    assert.equal(trigger.getAttribute('aria-disabled'), null);
});
