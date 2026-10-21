const test = require('node:test');
const assert = require('node:assert/strict');
const source = import('../../web/js/workspace/state.mjs');

test('workspace routes resolve only supported deep links', async () => {
    const { resolveRoute } = await source;
    for (const route of ['overview', 'images', 'library', 'tags', 'inspect', 'system', 'activity']) {
        assert.equal(resolveRoute(`#/${route}`).id, route);
    }
    for (const hash of ['', '#', '#/unknown', '#/images/other', '#/<script>', '#/activity/abc', `#/activity/${'A'.repeat(32)}`]) {
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
    for (const id of ['images', 'library', 'tags', 'overview', 'activity']) assert.equal(isHandoff(resolveRoute(`#/${id}`)), false, id);
    for (const id of ['inspect', 'system']) assert.equal(isHandoff(resolveRoute(`#/${id}`)), true, id);
});

test('a job of the Activity page is its own deep link', async () => {
    const { resolveRoute, routeTitle, isHandoff } = await source;
    const id = '0123456789abcdef'.repeat(2);
    const route = resolveRoute(`#/activity/${id}`);
    assert.deepEqual([route.id, route.jobId, route.hash, routeTitle(route), isHandoff(route)],
        ['activity', id, `#/activity/${id}`, 'Activity', false]);
    assert.equal(resolveRoute('#/activity').jobId, undefined);
    assert.equal(route.href, undefined, 'Activity has no legacy card on Overview');
    // The router renders again when only the job changes, and not for the same job.
    const { mountRouter } = await source;
    const other = 'f'.repeat(32);
    const win = new EventTarget();
    win.location = { hash: `#/activity/${id}` };
    win.history = { state: null, replaceState(state, unused, hash) { win.location.hash = hash; } };
    const seen = [];
    const dispose = mountRouter(win, next => seen.push([next.id, next.jobId]));
    win.dispatchEvent(new Event('hashchange'));
    win.location.hash = `#/activity/${other}`; win.dispatchEvent(new Event('hashchange'));
    win.location.hash = '#/activity'; win.dispatchEvent(new Event('hashchange'));
    assert.deepEqual(seen, [['activity', id], ['activity', other], ['activity', undefined]]);
    dispose();
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
    const counts = summarizeNavCounts({images: {total: 24}, favorites: {total: 3}, library: {total: 0}, tags: {total: 7}});
    assert.ok(counts instanceof Map);
    assert.deepEqual([...counts], [['images', 24], ['favorites', 3], ['library', 0], ['tags', 7]]);
    assert.equal(counts.has('inspect'), false, 'Inspect a file has no count');
    for (const invalid of [undefined, null, {}, {total: -1}, {total: 1.5}, {total: '3'}, {total: NaN},
        {total: Infinity}, {total: Number.MAX_SAFE_INTEGER + 1}, {total: null}, [], 'x']) {
        const result = summarizeNavCounts({images: invalid, favorites: invalid, library: {total: 2}, tags: invalid});
        assert.equal(result.get('images'), null, JSON.stringify(invalid));
        assert.equal(result.get('favorites'), null);
        assert.equal(result.get('library'), 2);
        assert.equal(result.get('tags'), null);
    }
    assert.deepEqual([...summarizeNavCounts()], [['images', null], ['favorites', null], ['library', null], ['tags', null]]);
});

test('nav counts settle independently so a slow images read never delays the others', async () => {
    const { loadNavCounts } = await source;
    let releaseImages;
    const images = new Promise(resolve => { releaseImages = resolve; });
    const api = {get: async url => {
        if (url.includes('favorites=1')) return {success: true, total: 2};
        if (url.startsWith('/prompt_manager/images')) return images;
        if (url.startsWith('/prompt_manager/library')) return {success: true, total: 3};
        throw new Error('Unavailable');
    }};
    const settled = [];
    const done = loadNavCounts(api, new AbortController().signal, (id, count) => settled.push([id, count]));
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.deepEqual(settled.sort(), [['favorites', 2], ['library', 3], ['tags', null]]);
    releaseImages({success: true, total: 40});
    const counts = await done;
    assert.deepEqual(settled.at(-1), ['images', 40]);
    assert.deepEqual([...counts], [['images', 40], ['favorites', 2], ['library', 3], ['tags', null]]);
});

test('nav counts issue four bounded requests and drop only the failed count', async () => {
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
    assert.deepEqual([...counts], [['images', 12], ['favorites', 12], ['library', null], ['tags', 5]]);
    const urls = calls.map(([url]) => new URL(url, 'http://host'));
    assert.deepEqual(urls.map(url => url.pathname),
        ['/prompt_manager/images/output', '/prompt_manager/images/output', '/prompt_manager/library', '/prompt_manager/vocabulary/tags']);
    for (const url of urls) assert.equal(url.searchParams.get('limit'), '1');
    assert.equal(urls[0].searchParams.get('mode'), 'workspace');
    assert.equal(urls[0].searchParams.has('favorites'), false);
    // D51: the Favorites count is the Favorites page's own listing.
    assert.equal(urls[1].searchParams.get('mode'), 'workspace');
    assert.equal(urls[1].searchParams.get('favorites'), '1');
    assert.ok(calls.every(([, used]) => used === signal));
});

test('a favorite change re-reads only the Favorites count', async () => {
    const { loadNavCounts, FAVORITE_WRITE_COUNTS } = await source;
    assert.deepEqual([...FAVORITE_WRITE_COUNTS], ['favorites']);
    assert.ok(Object.isFrozen(FAVORITE_WRITE_COUNTS));
    const calls = [];
    const api = {get: async url => { calls.push(url); return {success: true, total: 0}; }};
    const counts = await loadNavCounts(api, new AbortController().signal, () => {}, FAVORITE_WRITE_COUNTS);
    assert.deepEqual(calls, ['/prompt_manager/images/output?mode=workspace&favorites=1&limit=1']);
    // A real zero is a count, unlike an outstanding read.
    assert.deepEqual([...counts], [['favorites', 0]]);
});

test('the Favorites route is native and resolves by its hash', async () => {
    const { resolveRoute, isHandoff, routeTitle, routes } = await source;
    const route = resolveRoute('#/favorites');
    assert.equal(route.id, 'favorites');
    assert.equal(route.label, 'Favorites');
    assert.equal(routeTitle(route), 'Favorites');
    assert.equal(isHandoff(route), false);
    // No href: Overview lists no legacy card for it.
    assert.equal(routes.find(item => item.id === 'favorites').href, undefined);
});

test('a Library or vocabulary write re-reads only the Library and Tags counts', async () => {
    const { loadNavCounts, LIBRARY_WRITE_COUNTS } = await source;
    assert.deepEqual([...LIBRARY_WRITE_COUNTS], ['library', 'tags']);
    assert.ok(Object.isFrozen(LIBRARY_WRITE_COUNTS));
    const calls = [];
    const api = {get: async url => {
        calls.push(new URL(url, 'http://host').pathname);
        return {success: true, total: url.startsWith('/prompt_manager/library') ? 4 : 9};
    }};
    const settled = [];
    const counts = await loadNavCounts(api, new AbortController().signal, (id, count) => settled.push([id, count]), LIBRARY_WRITE_COUNTS);
    assert.deepEqual(calls, ['/prompt_manager/library', '/prompt_manager/vocabulary/tags']);
    assert.deepEqual(settled.sort(), [['library', 4], ['tags', 9]]);
    assert.deepEqual([...counts], [['library', 4], ['tags', 9]]);
});

test('navigating to the current route re-renders it, as the concept go() does', async () => {
    const {mountRouter,navigateTo}=await source;
    const win=new EventTarget();win.location={hash:'#/library/12'};
    win.history={state:null,replaceState(state,unused,hash){win.location.hash=hash;}};
    const seen=[];const dispose=mountRouter(win,route=>seen.push([route.id,route.positiveId]));
    navigateTo('#/library/12',win);
    assert.deepEqual(seen,[['library',12],['library',12]]);
    // A different route only sets the hash; its hashchange renders it once.
    navigateTo('#/library/13',win);assert.equal(win.location.hash,'#/library/13');assert.equal(seen.length,2);
    win.dispatchEvent(new Event('hashchange'));assert.deepEqual(seen.at(-1),['library',13]);
    dispose();navigateTo('#/library/13',win);assert.equal(seen.length,3);
});

// Saved views (#249 D16): `#/saved/<id>` opens one persisted view; the shell resolves it to its page.
test('saved-view links resolve to their id and re-render only when the id changes', async () => {
    const {resolveRoute,mountRouter,isHandoff,routes}=await source;
    const route=resolveRoute('#/saved/7');
    assert.deepEqual([route.id,route.savedId,route.hash],['saved',7,'#/saved/7']);
    assert.equal(isHandoff(route),false);
    assert.equal(routes.some(item=>item.id==='saved'),false,'saved views are not an Overview card or a WORKSPACE link');
    for(const hash of ['#/saved','#/saved/0','#/saved/-2','#/saved/1.5','#/saved/9007199254740992','#/saved/3/x','#/saved/abc']) {
        assert.equal(resolveRoute(hash).id,'images',hash);
    }
    const win=new EventTarget();win.location={hash:'#/saved/3'};
    win.history={state:null,replaceState(state,unused,hash){win.location.hash=hash;}};
    const seen=[];const dispose=mountRouter(win,next=>seen.push([next.id,next.savedId]));
    assert.equal(win.location.hash,'#/saved/3');
    win.dispatchEvent(new Event('hashchange'));
    win.location.hash='#/saved/4';win.dispatchEvent(new Event('hashchange'));
    win.location.hash='#/images';win.dispatchEvent(new Event('hashchange'));
    assert.deepEqual(seen,[['saved',3],['saved',4],['images',undefined]]);
    dispose();
});
