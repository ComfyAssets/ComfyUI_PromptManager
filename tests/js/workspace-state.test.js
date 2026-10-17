const test = require('node:test');
const assert = require('node:assert/strict');
const source = import('../../web/js/workspace/state.mjs');

test('workspace routes resolve only supported deep links', async () => {
    const { resolveRoute } = await source;
    for (const route of ['overview', 'images', 'library', 'tags', 'inspect', 'system']) {
        assert.equal(resolveRoute(`#/${route}`).id, route);
    }
    for (const hash of ['', '#', '#/unknown', '#/images/other', '#/<script>']) {
        assert.equal(resolveRoute(hash).id, 'images');
    }
});

test('statistics preserve zero and reject incomplete or invalid counts', async () => {
    const { summarizeStats } = await source;
    assert.deepEqual(summarizeStats({total_prompts:0, total_images:12, total_tags:3}), [
        ['Prompts', 0], ['Images', 12], ['Tags', 3],
    ]);
    for (const stats of [undefined, null, {}, {total_prompts:0, total_images:-1, total_tags:0},
        {total_prompts:'2', total_images:0, total_tags:0},
        {total_prompts:Infinity, total_images:0, total_tags:0},
        {total_prompts:0, total_images:1.5, total_tags:0}]) {
        assert.throws(() => summarizeStats(stats), /statistics/i);
    }
});

test('router normalizes unknown routes, avoids duplicate renders, and disposes its listener', async () => {
    const { mountRouter } = await source;
    const win = new EventTarget();
    win.location = {hash:'#/missing'};
    const replacements = [];
    win.history = {state: {keep:1}, replaceState(state, unused, hash) {
        replacements.push([state, hash]); win.location.hash = hash;
    }};
    const seen = [];
    const dispose = mountRouter(win, route => seen.push(route.id));
    assert.deepEqual(seen, ['images']);
    assert.deepEqual(replacements, [[{keep:1}, '#/images']]);
    win.location.hash = '#/library';
    win.dispatchEvent(new Event('hashchange'));
    win.dispatchEvent(new Event('hashchange'));
    assert.deepEqual(seen, ['images', 'library']);
    dispose();
    dispose();
    win.location.hash = '#/system';
    win.dispatchEvent(new Event('hashchange'));
    assert.deepEqual(seen, ['images', 'library']);
});

test('positive library deep links preserve the selected identity without duplicate route renders', async () => {
    const {resolveRoute,mountRouter}=await source;
    assert.equal(resolveRoute('#/library/12').positiveId,12);
    for(const hash of ['#/library/0','#/library/-1','#/library/1.5','#/library/9007199254740992','#/library/1/extra']) assert.equal(resolveRoute(hash).id,'images');
    const win=new EventTarget();win.location={hash:'#/library/12'};
    win.history={state:null,replaceState(state,unused,hash){win.location.hash=hash;}};
    const seen=[];const dispose=mountRouter(win,route=>seen.push([route.id,route.positiveId]));
    assert.equal(win.location.hash,'#/library/12');
    win.dispatchEvent(new Event('hashchange'));assert.equal(seen.length,1);
    win.location.hash='#/library/13';win.dispatchEvent(new Event('hashchange'));
    win.location.hash='#/library';win.dispatchEvent(new Event('hashchange'));
    assert.deepEqual(seen,[['library',12],['library',13],['library',undefined]]);dispose();
});
