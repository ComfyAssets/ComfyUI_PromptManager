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

test('tags is a native route titled Tags; only unbuilt routes hand off to legacy tools', async () => {
    const { resolveRoute, routeTitle, isHandoff } = await source;
    const tags = resolveRoute('#/tags');
    assert.equal(tags.label, 'Tags & categories');
    assert.equal(routeTitle(tags), 'Tags');
    assert.equal(tags.href, '/prompt_manager/admin#/tags', 'Overview keeps the existing tag manager card');
    for (const id of ['images', 'library', 'inspect', 'overview', 'system']) {
        const route = resolveRoute(`#/${id}`);
        assert.equal(routeTitle(route), route.label);
    }
    assert.equal(routeTitle(resolveRoute('#/library/7')), 'Prompt library');
    for (const id of ['images', 'library', 'tags', 'overview']) assert.equal(isHandoff(resolveRoute(`#/${id}`)), false, id);
    for (const id of ['inspect', 'system']) assert.equal(isHandoff(resolveRoute(`#/${id}`)), true, id);
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

test('nav counts reject negative, non-integer and missing totals', async () => {
    const { summarizeNavCounts } = await source;
    const counts = summarizeNavCounts({images: {total: 24}, library: {total: 0}, tags: {total: 7}});
    assert.ok(counts instanceof Map);
    assert.deepEqual([...counts], [['images', 24], ['library', 0], ['tags', 7]]);
    assert.equal(counts.has('inspect'), false, 'Inspect a file has no count');
    for (const invalid of [undefined, null, {}, {total: -1}, {total: 1.5}, {total: '3'}, {total: NaN},
        {total: Infinity}, {total: Number.MAX_SAFE_INTEGER + 1}, {total: null}, [], 'x']) {
        const result = summarizeNavCounts({images: invalid, library: {total: 2}, tags: invalid});
        assert.equal(result.get('images'), null, JSON.stringify(invalid));
        assert.equal(result.get('library'), 2);
        assert.equal(result.get('tags'), null);
    }
    assert.deepEqual([...summarizeNavCounts()], [['images', null], ['library', null], ['tags', null]]);
});

test('nav counts issue three bounded requests and drop only the failed count', async () => {
    const { loadNavCounts } = await source;
    const calls = [];
    const signal = new AbortController().signal;
    const api = {get: async (url, options) => {
        calls.push([url, options.signal]);
        if (url.startsWith('/prompt_manager/library')) throw new Error('Unavailable');
        if (url.startsWith('/prompt_manager/vocabulary/tags')) return {success: true, total: 5};
        return {success: true, total: 12};
    }};
    const counts = await loadNavCounts(api, signal);
    assert.deepEqual([...counts], [['images', 12], ['library', null], ['tags', 5]]);
    const urls = calls.map(([url]) => new URL(url, 'http://host'));
    assert.deepEqual(urls.map(url => url.pathname),
        ['/prompt_manager/images/output', '/prompt_manager/library', '/prompt_manager/vocabulary/tags']);
    for (const url of urls) assert.equal(url.searchParams.get('limit'), '1');
    assert.equal(urls[0].searchParams.get('mode'), 'workspace');
    assert.ok(calls.every(([, used]) => used === signal));
});
