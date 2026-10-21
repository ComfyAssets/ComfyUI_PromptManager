// Saved views (#249 D16): the state whitelist, the concept's default name, the listing parser and the shell's store.
const test = require('node:test');
const assert = require('node:assert/strict');

const source = import('../../web/js/workspace/saved-views.mjs');

const IMAGE_DEFAULTS = { q: '', category: '', sort: 'newest', source: 'all', folder: '', media: 'all', tags: [] };
const LIBRARY_DEFAULTS = { q: '', category: '', sort: 'newest', tags: [] };

test('a saved state always holds every page key and keeps only values its page accepts', async () => {
    const { normalizeViewState, SAVED_VIEW_PAGES } = await source;
    assert.deepEqual(SAVED_VIEW_PAGES, ['images', 'library']);
    assert.deepEqual(normalizeViewState('images', {}), IMAGE_DEFAULTS);
    assert.deepEqual(normalizeViewState('library', null), LIBRARY_DEFAULTS);
    const full = { q: 'castle', category: 'Portraits', sort: 'name-desc', source: '2', folder: 'a/b', media: 'video', tags: ['moody', 'fog'] };
    assert.deepEqual(normalizeViewState('images', full), full);
    // Each rejected value falls back on its own; the rest of the view still applies.
    assert.deepEqual(normalizeViewState('images', { ...full, sort: 'bogus', source: '-1', media: 'audio', tags: ['a,b'], extra: 1 }),
        { ...full, sort: 'newest', source: 'all', media: 'all', tags: [] });
    assert.deepEqual(normalizeViewState('images', { ...full, q: 'x'.repeat(501), folder: 7, tags: Array(21).fill('t') }),
        { ...full, q: '', folder: '', tags: [] });
    assert.deepEqual(normalizeViewState('images', { ...full, category: 'bad\u0007', tags: [' '] }), { ...full, category: '', tags: [] });
    // Library has no source, folder or media, and no Name Z–A.
    assert.deepEqual(normalizeViewState('library', { ...full, sort: 'name-desc' }), { q: 'castle', category: 'Portraits', sort: 'newest', tags: ['moody', 'fog'] });
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
        { id: 3, page: 'library', name: 'Neon', state: { q: 'neon', category: '', sort: 'name', tags: [] } },
        { id: 1, page: 'images', name: 'Videos', state: { ...IMAGE_DEFAULTS, media: 'video', source: '9' } },
    ]);
    assert.equal(parseSavedView(null), null);
    assert.throws(() => parseSavedViews({ success: true }), TypeError);
    assert.equal(savedViewHash(12), '#/saved/12');
});

test('page filters and saved states convert both ways', async () => {
    const { imagesFilters, imagesViewState, libraryFilters, libraryViewState } = await source;
    const filters = { q: 'castle', category: 'Portraits', sort: 'rating', root: '1', subfolder: 'x', media: 'image', tags: ['fog'] };
    const state = imagesViewState(filters);
    assert.deepEqual(state, { q: 'castle', category: 'Portraits', sort: 'rating', source: '1', folder: 'x', media: 'image', tags: ['fog'] });
    assert.deepEqual(imagesFilters(state), filters);
    assert.deepEqual(imagesFilters({}), { q: '', category: '', sort: 'newest', root: 'all', subfolder: '', media: 'all', tags: [] });
    const shelf = { text: 'neon', category: 'Cities', sort: 'oldest', tags: ['night'] };
    assert.deepEqual(libraryViewState(shelf), { q: 'neon', category: 'Cities', sort: 'oldest', tags: ['night'] });
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
