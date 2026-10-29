// Saved views (#249 D16): the state whitelist, the concept's default name, the listing parser and the shell's store.
const test = require('node:test');
const assert = require('node:assert/strict');

const source = import('../../web/js/workspace/saved-views.mjs');

const IMAGE_DEFAULTS = { q: '', category: '', sort: 'newest', source: 'all', folder: '', media: 'all', tags: [], favorites: false, collection: '' };
const LIBRARY_DEFAULTS = { q: '', category: '', sort: 'newest', tags: [], tag_mode: 'all', folder: '' };

test('a saved state always holds every page key and keeps only values its page accepts', async () => {
    const { normalizeViewState, SAVED_VIEW_PAGES } = await source;
    assert.deepEqual(SAVED_VIEW_PAGES, ['images', 'library']);
    assert.deepEqual(normalizeViewState('images', {}), IMAGE_DEFAULTS);
    assert.deepEqual(normalizeViewState('library', null), LIBRARY_DEFAULTS);
    const full = { q: 'castle', category: 'Portraits', sort: 'name-desc', source: '2', folder: 'a/b', media: 'video', tags: ['moody', 'fog'], favorites: true, collection: '' };
    assert.deepEqual(normalizeViewState('images', full), full);
    // #331 D140: a collection id is '' or 1 to 9 digits without a leading zero; an old view without the key reads ''.
    for (const collection of ['1', '12', '999999999']) {
        assert.equal(normalizeViewState('images', { collection }).collection, collection);
    }
    for (const collection of ['0', 'x', 12, '012', '1234567890', null]) {
        assert.equal(normalizeViewState('images', { collection }).collection, '');
    }
    // Favorites and a collection are never both a view's filter: the collection wins, as the listing's URL does.
    assert.deepEqual(normalizeViewState('images', { ...full, collection: '4' }), { ...full, favorites: false, collection: '4' });
    // D51: favorites is a boolean; anything else is the whole listing.
    for (const favorites of ['true', 1, null]) assert.equal(normalizeViewState('images', { ...full, favorites }).favorites, false);
    // Each rejected value falls back on its own; the rest of the view still applies.
    assert.deepEqual(normalizeViewState('images', { ...full, sort: 'bogus', source: '-1', media: 'audio', tags: ['a,b'], extra: 1 }),
        { ...full, sort: 'newest', source: 'all', media: 'all', tags: [] });
    assert.deepEqual(normalizeViewState('images', { ...full, q: 'x'.repeat(501), folder: 7, tags: Array(21).fill('t') }),
        { ...full, q: '', folder: '', tags: [] });
    assert.deepEqual(normalizeViewState('images', { ...full, category: 'bad\u0007', tags: [' '] }), { ...full, category: '', tags: [] });
    // Library has no source or media, and no Name Z–A; its folder (#444) is the Library's own.
    assert.deepEqual(normalizeViewState('library', { ...full, sort: 'name-desc' }), { q: 'castle', category: 'Portraits', sort: 'newest', tags: ['moody', 'fog'], tag_mode: 'all', folder: 'a/b' });
    assert.deepEqual(normalizeViewState('library', { sort: 'oldest' }), { ...LIBRARY_DEFAULTS, sort: 'oldest' });
    // The returned tags are a copy.
    const tags = ['one'];
    const state = normalizeViewState('library', { tags });
    state.tags.push('two');
    assert.deepEqual(tags, ['one']);
    assert.throws(() => normalizeViewState('tags', {}), TypeError);
});

test('the default name is the concept search name, else My workspace view, within the name limit', async () => {
    const { defaultViewName } = await source;
    assert.equal(defaultViewName('neon'), 'Search: neon');
    assert.equal(defaultViewName('  neon city '), 'Search: neon city');
    assert.equal(defaultViewName(''), 'My workspace view');
    assert.equal(defaultViewName('   '), 'My workspace view');
    assert.equal(defaultViewName(undefined), 'My workspace view');
    assert.equal(defaultViewName('x'.repeat(500)).length, 100);
});

test('the listing keeps usable views in order and normalizes their state', async () => {
    const { parseSavedViews, parseSavedView, savedViewHash } = await source;
    const views = parseSavedViews({ success: true, views: [
        { id: 3, page: 'library', name: 'Neon', state: { q: 'neon', category: '', sort: 'name', tags: [] } },
        { id: 0, page: 'images', name: 'Bad id', state: {} },
        { id: 4, page: 'tags', name: 'Bad page', state: {} },
        { id: 5, page: 'images', name: '  ', state: {} },
        { id: 1, page: 'images', name: 'Videos', state: { media: 'video', source: '9' } },
    ] });
    assert.deepEqual(views, [
        { id: 3, page: 'library', name: 'Neon', state: { q: 'neon', category: '', sort: 'name', tags: [], tag_mode: 'all', folder: '' } },
        { id: 1, page: 'images', name: 'Videos', state: { ...IMAGE_DEFAULTS, media: 'video', source: '9' } },
    ]);
    assert.equal(parseSavedView(null), null);
    assert.throws(() => parseSavedViews({ success: true }), TypeError);
    assert.equal(savedViewHash(12), '#/saved/12');
});

test('page filters and saved states convert both ways', async () => {
    const { imagesFilters, imagesViewState, libraryFilters, libraryViewState } = await source;
    const filters = { q: 'castle', category: 'Portraits', sort: 'rating', root: '1', subfolder: 'x', media: 'image', tags: ['fog'], favorites: true, collection: '' };
    const state = imagesViewState(filters);
    assert.deepEqual(state, { q: 'castle', category: 'Portraits', sort: 'rating', source: '1', folder: 'x', media: 'image', tags: ['fog'], favorites: true, collection: '' });
    assert.deepEqual(imagesFilters(state), filters);
    assert.deepEqual(imagesFilters({}), { q: '', category: '', sort: 'newest', root: 'all', subfolder: '', media: 'all', tags: [], favorites: false, collection: '' });
    // #331 D140: the open collection round-trips; a missing one is ''.
    const inCollection = { ...filters, favorites: false, collection: '7' };
    assert.equal(imagesViewState(inCollection).collection, '7');
    assert.deepEqual(imagesFilters(imagesViewState(inCollection)), inCollection);
    assert.equal(imagesViewState({ ...filters, collection: undefined }).collection, '');
    assert.equal(imagesViewState({ ...filters, collection: 7 }).collection, '7');
    assert.equal(imagesViewState({ ...filters, favorites: undefined }).favorites, false);
    const shelf = { text: 'neon', category: 'Cities', sort: 'oldest', tags: ['night'], tagMode: 'any', folder: '2026' };
    assert.deepEqual(libraryViewState(shelf), { q: 'neon', category: 'Cities', sort: 'oldest', tags: ['night'], tag_mode: 'any', folder: '2026' });
    assert.deepEqual(libraryFilters(libraryViewState(shelf)), shelf);
});

function fakeApi() {
    const calls = [];
    const pending = [];
    const api = {
        calls,
        get(url, options) {
            calls.push(['GET', url, options.cache]);
            return new Promise((resolve, reject) => pending.push({ resolve, reject, signal: options.signal }));
        },
        post(url, body) {
            calls.push(['POST', url, body]);
            return Promise.resolve({ success: true, view: { id: 9, ...body } });
        },
        patch(url, body) {
            calls.push(['PATCH', url, body]);
            return api.patchResult ?? Promise.resolve({ success: true, view: { id: 9, page: 'images', name: body.name, state: {} } });
        },
        del(url) {
            calls.push(['DELETE', url]);
            return api.deleteResult ?? Promise.resolve({ success: true, id: 9 });
        },
    };
    return { api, pending };
}

const flush = () => new Promise(resolve => setImmediate(resolve));

test('the store reports loads, keeps the last views after a failed read, and a newer read supersedes', async () => {
    const { createSavedViews, SAVED_VIEWS_URL } = await source;
    const { api, pending } = fakeApi();
    const controller = new AbortController();
    const store = createSavedViews(api, controller.signal);
    const seen = [];
    store.subscribe(next => seen.push([next.status, next.views.map(view => view.id)]));
    store.load();
    const settled = store.settled();
    pending[0].resolve({ success: true, views: [{ id: 2, page: 'images', name: 'A', state: {} }] });
    assert.equal((await settled).status, 'ready');
    assert.deepEqual(store.find(2).state, IMAGE_DEFAULTS);
    assert.equal(store.find(3), null);
    assert.deepEqual(api.calls[0], ['GET', SAVED_VIEWS_URL, 'no-store']);

    store.load();
    pending[1].reject(new Error('offline'));
    await flush();
    assert.equal(store.status, 'error');
    assert.deepEqual(store.views.map(view => view.id), [2]);

    // A second read aborts the first; only the newer one lands.
    store.load();
    store.load();
    assert.equal(pending[2].signal.aborted, true);
    pending[2].resolve({ success: true, views: [] });
    pending[3].resolve({ success: true, views: [{ id: 5, page: 'library', name: 'B', state: {} }] });
    await flush();
    assert.deepEqual(store.views.map(view => view.id), [5]);
    assert.deepEqual(seen, [['loading', []], ['ready', [2]], ['loading', [2]], ['error', [2]], ['loading', [2]], ['loading', [2]], ['ready', [5]]]);
    controller.abort();
});

test('creating a view posts the full state and re-reads the list', async () => {
    const { createSavedViews, SAVED_VIEWS_URL } = await source;
    const { api, pending } = fakeApi();
    const store = createSavedViews(api);
    const created = store.create('library', 'Neon', { q: 'neon' });
    await flush();
    assert.deepEqual(api.calls[0], ['POST', SAVED_VIEWS_URL, { page: 'library', name: 'Neon', state: { ...LIBRARY_DEFAULTS, q: 'neon' } }]);
    pending[0].resolve({ success: true, views: [{ id: 9, page: 'library', name: 'Neon', state: { q: 'neon' } }] });
    assert.deepEqual(await created, { id: 9, page: 'library', name: 'Neon', state: { ...LIBRARY_DEFAULTS, q: 'neon' } });
    assert.equal(store.find(9).name, 'Neon');
    const broken = createSavedViews({ ...api, post: () => Promise.resolve({ success: true, view: null }) });
    await assert.rejects(broken.create('images', 'X', {}), TypeError);
});

test('renaming and deleting a view re-read the list, whatever the outcome', async () => {
    const { createSavedViews, SAVED_VIEWS_URL } = await source;
    const { api, pending } = fakeApi();
    const store = createSavedViews(api);
    const renamed = store.rename(9, 'Dusk');
    await flush();
    assert.deepEqual(api.calls[0], ['PATCH', `${SAVED_VIEWS_URL}/9`, { name: 'Dusk' }]);
    assert.deepEqual(api.calls[1], ['GET', SAVED_VIEWS_URL, 'no-store']);
    pending[0].resolve({ success: true, views: [{ id: 9, page: 'images', name: 'Dusk', state: {} }] });
    assert.deepEqual(await renamed, { id: 9, page: 'images', name: 'Dusk', state: IMAGE_DEFAULTS });
    assert.equal(store.find(9).name, 'Dusk');

    const removed = store.remove(9);
    await flush();
    assert.deepEqual(api.calls[2], ['DELETE', `${SAVED_VIEWS_URL}/9`]);
    pending[1].resolve({ success: true, views: [] });
    assert.equal(await removed, 9);
    assert.equal(store.find(9), null);

    // A view another tab already deleted is the outcome asked for; any other failure rejects, after a re-read.
    api.deleteResult = Promise.reject(Object.assign(new Error('Not found'), { status: 404 }));
    const gone = store.remove(4);
    await flush();
    pending[2].resolve({ success: true, views: [] });
    assert.equal(await gone, 4);
    api.deleteResult = Promise.reject(Object.assign(new Error('Locked'), { status: 500 }));
    const failed = store.remove(4);
    await flush();
    pending[3].resolve({ success: true, views: [] });
    await assert.rejects(failed, /Locked/);
    api.patchResult = Promise.resolve({ success: true, view: null });
    const invalid = store.rename(4, 'X');
    await flush();
    pending[4].resolve({ success: true, views: [] });
    await assert.rejects(invalid, TypeError);
    assert.equal(pending.length, 5);
    assert.throws(() => store.rename(0, 'X'), TypeError);
    assert.throws(() => store.remove('9'), TypeError);
});

test('a state over the API 4 KB cap is refused before anything is sent (#249 M5)', async () => {
    const { createSavedViews, stateFits, SavedViewTooLarge, MAX_STATE_BYTES } = await source;
    assert.equal(MAX_STATE_BYTES, 4096);
    assert.equal(stateFits(IMAGE_DEFAULTS), true);
    // Each value is within its own limit; their multibyte UTF-8 together passes 4 KB.
    const big = { ...IMAGE_DEFAULTS, q: 'é'.repeat(500), folder: 'é'.repeat(1024), tags: Array.from({ length: 20 }, () => 'é'.repeat(50)) };
    assert.equal(stateFits(big), false);
    const { api } = fakeApi();
    const store = createSavedViews(api);
    await assert.rejects(store.create('images', 'Big', big), error => error instanceof SavedViewTooLarge && error instanceof RangeError);
    assert.deepEqual(api.calls, []);
});

test('a Library view keeps its tag match and folder; an old view reads the defaults (#444)', async () => {
    const { normalizeViewState, libraryFilters } = await source;
    assert.deepEqual(normalizeViewState('library', { tag_mode: 'any', folder: '2026/Aug' }), { ...LIBRARY_DEFAULTS, tag_mode: 'any', folder: '2026/Aug' });
    assert.deepEqual(normalizeViewState('library', { tag_mode: 'or', folder: 'x'.repeat(1025) }), LIBRARY_DEFAULTS);
    assert.deepEqual(libraryFilters({ q: 'a' }), { text: 'a', category: '', sort: 'newest', tags: [], tagMode: 'all', folder: '' });
});

test('a Library view keeps untagged, and untagged never travels with tags (#444)', async () => {
    const { normalizeViewState, libraryFilters, libraryViewState } = await source;
    assert.equal(normalizeViewState('library', { tag_mode: 'untagged' }).tag_mode, 'untagged');
    assert.deepEqual(normalizeViewState('library', { tags: ['a'], tag_mode: 'untagged' }), { ...LIBRARY_DEFAULTS, tags: ['a'] });
    const shelf = { text: '', category: '', sort: 'newest', tags: [], tagMode: 'untagged', folder: '' };
    assert.equal(libraryViewState(shelf).tag_mode, 'untagged');
    assert.deepEqual(libraryFilters(libraryViewState(shelf)), shelf);
});

test('a shelf without the #444 filters saves their defaults', async () => {
    const { libraryViewState } = await source;
    assert.deepEqual(libraryViewState({ text: '', category: '', sort: 'newest', tags: [] }), LIBRARY_DEFAULTS);
});
