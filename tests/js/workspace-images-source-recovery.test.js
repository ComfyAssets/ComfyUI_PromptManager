// Run with: node --test tests/js/workspace-images-source-recovery.test.js
const test = require('node:test');
const assert = require('node:assert/strict');

const load = () => import('../../web/js/workspace/images-source-recovery.mjs');

const OLD = Object.freeze({ root_index: 0, root_scope: 'a'.repeat(64), path: 'sub/a.png', filename: 'a.png' });
const FRESH = Object.freeze({ root_index: 1, root_scope: 'b'.repeat(64), path: 'sub/a.png', filename: 'a.png', url: '/u' });

function fakeApi(answer) {
    const calls = [];
    return { calls, post: async (url, body, options) => { calls.push({ url, body, options }); return answer(body); } };
}
const flush = () => new Promise(resolve => setImmediate(resolve));

test('rescopeImage posts only the target and returns the fresh descriptor', async () => {
    const { rescopeImage, RESCOPE_URL } = await load();
    const api = fakeApi(() => ({ success: true, image: FRESH }));
    const signal = new AbortController().signal;
    const fresh = await rescopeImage(api, OLD, { signal });
    assert.deepEqual(fresh, FRESH);
    assert.ok(Object.isFrozen(fresh));
    assert.deepEqual(api.calls, [{ url: RESCOPE_URL, body: { target: { root_index: 0, root_scope: OLD.root_scope, path: 'sub/a.png' } }, options: { signal } }]);
});

test('rescopeImage answers null for a refusal, a malformed answer, another path or a bad target', async () => {
    const { rescopeImage } = await load();
    const refused = { post: async () => { throw Object.assign(new Error('gone'), { status: 409, code: 'source-changed' }); } };
    assert.equal(await rescopeImage(refused, OLD), null);
    const lost = { post: async () => { throw new TypeError('Failed to fetch'); } };
    await assert.rejects(rescopeImage(lost, OLD), TypeError, 'a lost answer is not final');
    for (const answer of [null, { success: false }, { success: true, image: { ...FRESH, root_scope: 'x' } },
        { success: true, image: { ...FRESH, path: 'other.png' } }, { success: true, image: { ...FRESH, root_index: -1 } }]) {
        assert.equal(await rescopeImage(fakeApi(() => answer), OLD), null);
    }
    const api = fakeApi(() => ({ success: true, image: FRESH }));
    for (const image of [null, { ...OLD, root_scope: 'short' }, { ...OLD, path: '' }, { ...OLD, root_index: 1.5 }]) {
        assert.equal(await rescopeImage(api, image), null);
    }
    assert.equal(api.calls.length, 0, 'a malformed target is never sent');
});

test('one rescope per held pair: callers share it, a tried pair answers its first result, reset forgets', async () => {
    const { createSourceRecovery } = await load();
    let answer = { success: true, image: FRESH };
    const api = fakeApi(() => answer);
    const applied = [];
    const recovery = createSourceRecovery({ api, generation: () => 7, apply: async (image, fresh, started) => { applied.push([image.path, fresh.root_index, started]); return fresh; } });
    const [first, second] = await Promise.all([recovery.recover(OLD), recovery.recover({ ...OLD })]);
    assert.deepEqual([first, second], [FRESH, FRESH]);
    assert.equal(api.calls.length, 1);
    assert.deepEqual(applied, [['sub/a.png', 1, 7]]);
    answer = { success: false };
    assert.deepEqual(await recovery.recover(OLD), FRESH, 'the pair is not asked again');
    recovery.reset();
    assert.equal(await recovery.recover(OLD), null, 'after a reset the pair is asked again');
    assert.equal(api.calls.length, 2);
    assert.equal(await recovery.recover({ ...OLD, root_scope: 'bad' }), null);
    assert.equal(api.calls.length, 2);
});

test('a refused pair never loops, an aborted page applies nothing and a failing apply is null', async () => {
    const { createSourceRecovery } = await load();
    const api = fakeApi(() => ({ success: true, image: FRESH }));
    const page = new AbortController();
    let applied = 0;
    const recovery = createSourceRecovery({ api, signal: page.signal, apply: async () => { applied += 1; return FRESH; } });
    page.abort();
    assert.equal(await recovery.recover(OLD), null);
    assert.equal(applied, 0);
    const failing = createSourceRecovery({ api, apply: async () => { throw new Error('page gone'); } });
    assert.equal(await failing.recover(OLD), null);
    assert.throws(() => createSourceRecovery({ api }), TypeError);
    // An api without post (an embedded gallery's page adapter) finds nothing.
    assert.equal(await createSourceRecovery({ api: {}, apply: async () => FRESH }).recover(OLD), null);
});

test('the memo forgets its oldest pairs past its limit', async () => {
    const { createSourceRecovery } = await load();
    const api = fakeApi(body => ({ success: true, image: { ...FRESH, path: body.target.path } }));
    const recovery = createSourceRecovery({ api, apply: async (_image, fresh) => fresh });
    for (let index = 0; index < 65; index++) await recovery.recover({ ...OLD, path: `f${index}.png` });
    assert.equal(api.calls.length, 65);
    await recovery.recover({ ...OLD, path: 'f64.png' });
    assert.equal(api.calls.length, 65, 'a recent pair is still remembered');
    await recovery.recover({ ...OLD, path: 'f0.png' });
    assert.equal(api.calls.length, 66, 'the oldest pair was forgotten');
});

function fakePage({ selected = true, root = 'all', current = true } = {}) {
    const log = [];
    const page = {
        filters: { root, subfolder: root === 'all' ? '' : 'sub' },
        current: started => { log.push(['current', started]); return current; },
        selected: image => selected && image.path === OLD.path,
        inspect: image => log.push(['inspect', image.root_index]),
        dropSelection: () => log.push(['drop']),
        stream: {
            refresh: async () => { log.push(['refresh']); throw new Error('read failed'); },
            reset: (filters, options) => log.push(['reset', { ...filters }, options]),
        },
        updateScope: () => log.push(['scope']),
        closeViewer: () => log.push(['close-viewer']),
        clearChecks: () => log.push(['clear-checks']),
        listsNow: () => true,
        rediscover: () => log.push(['rediscover']),
    };
    return { page, log };
}

test('a renumbered source keeps nothing by its old key: only the file stays selected', async () => {
    const { recoveredSource } = await load();
    const { page, log } = fakePage({ root: '0' });
    assert.equal(await recoveredSource(page)(OLD, FRESH, 3), FRESH);
    assert.deepEqual(log, [['current', 3], ['close-viewer'], ['clear-checks'], ['inspect', 1], ['scope'],
        ['reset', { root: '1', subfolder: 'sub' }, { load: true }], ['rediscover']]);
});

test('another Source filter falls back to All sources; an unselected file drops the old selection', async () => {
    const { recoveredSource } = await load();
    const { page, log } = fakePage({ root: '2', selected: false });
    await recoveredSource(page)(OLD, FRESH, 0);
    assert.deepEqual(log.find(entry => entry[0] === 'reset')[1], { root: 'all', subfolder: '' });
    assert.ok(log.some(entry => entry[0] === 'drop'));
    assert.ok(!log.some(entry => entry[0] === 'inspect'));
    const all = fakePage({ root: 'all' });
    await recoveredSource(all.page)(OLD, FRESH, 0);
    assert.deepEqual(all.log.find(entry => entry[0] === 'reset')[1], { root: 'all', subfolder: '' });
});

test('an unchanged numbering re-reads in place; a page that moved on is left alone', async () => {
    const { recoveredSource } = await load();
    const same = { ...FRESH, root_index: 0 };
    const { page, log } = fakePage();
    assert.equal(await recoveredSource(page)(OLD, same, 1), same);
    assert.deepEqual(log, [['current', 1], ['inspect', 0], ['refresh']], 'a failed re-read is not an error');
    const unselected = fakePage({ selected: false });
    await recoveredSource(unselected.page)(OLD, same, 1);
    assert.deepEqual(unselected.log, [['current', 1], ['refresh']]);
    const moved = fakePage({ current: false });
    assert.equal(await recoveredSource(moved.page)(OLD, FRESH, 1), FRESH, 'the dialog may still retarget');
    assert.deepEqual(moved.log, [['current', 1]]);
    await flush();
});

test('a scope that still names its root is not stale: nothing is applied', async () => {
    const { recoveredSource } = await load();
    const { page, log } = fakePage();
    assert.equal(await recoveredSource(page)(OLD, { ...OLD, url: '/u' }, 1), null);
    assert.deepEqual(log, []);
});

test('a lost rescope answer is not remembered: the pair is asked again', async () => {
    const { createSourceRecovery } = await load();
    let down = true;
    const api = { calls: 0, post: async () => { api.calls += 1; if (down) throw Object.assign(new Error('bad gateway'), { status: 502 }); return { success: true, image: FRESH }; } };
    const recovery = createSourceRecovery({ api, apply: async (_image, fresh) => fresh });
    assert.equal(await recovery.recover(OLD), null);
    down = false;
    assert.deepEqual(await recovery.recover(OLD), FRESH);
    assert.equal(api.calls, 2);
});

test('the same index under another scope (a changed key) re-reads in place and keeps the selection', async () => {
    const { recoveredSource } = await load();
    const rekeyed = { ...OLD, root_scope: 'c'.repeat(64) };
    const { page, log } = fakePage();
    page.stream.refresh = async () => { log.push(['refresh']); };
    assert.equal(await recoveredSource(page)(OLD, rekeyed, 2), rekeyed);
    assert.deepEqual(log, [['current', 2], ['inspect', 0], ['refresh']]);
    assert.ok(!log.some(entry => ['reset', 'drop', 'clear-checks', 'close-viewer'].includes(entry[0])));
});
