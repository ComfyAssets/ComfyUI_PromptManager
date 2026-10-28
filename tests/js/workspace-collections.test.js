// Collections (#331 D136–D140): the client store, the route hash, the names, every user-facing string and the
// mapping of the routes' error codes.
const test = require('node:test');
const assert = require('node:assert/strict');

const source = import('../../web/js/workspace/collections.mjs');

const FILE = Object.freeze({ root_index: 0, root_scope: 'a'.repeat(64), path: 'grids/one.png' });

function apiError(status, code) {
    return Object.assign(new Error(code || `HTTP ${status}`), { status, code: code || null });
}

function fakeApi() {
    const calls = [];
    const pending = [];
    const api = {
        calls,
        get(url, options) {
            calls.push(['GET', url, options?.cache]);
            if (url.includes('/membership?')) return api.membershipResult ?? Promise.resolve({ success: true, collection_ids: [2] });
            return new Promise((resolve, reject) => pending.push({ resolve, reject }));
        },
        post(url, body) {
            calls.push(['POST', url, body]);
            return api.postResult ?? Promise.resolve({ success: true, collection: { id: 5, name: 'Grids' }, created: true,
                added: body.items.length, already: 0, not_found: 0 });
        },
        patch(url, body) {
            calls.push(['PATCH', url, body]);
            return api.patchResult ?? Promise.resolve({ success: true, collection: { id: 2, name: body.name } });
        },
        del(url, options) {
            calls.push(['DELETE', url, options?.body]);
            return api.deleteResult ?? Promise.resolve(options?.body
                ? { success: true, collection: { id: options.body.collection_id, name: 'Portraits' }, removed: options.body.items.length }
                : { success: true, deleted: true });
        },
    };
    return { api, pending };
}

const LISTED = { success: true, collections: [
    { id: 4, name: 'Experiments', count: 8, missing: 0 },
    { id: 2, name: 'Landscapes', count: 12, missing: 1 },
] };

/** A store whose first read answered with `data`; the test's own timer seam replaces the 300 ms count timer. */
async function readyStore(data = LISTED) {
    const { createCollections } = await source;
    const { api, pending } = fakeApi();
    const timers = [];
    const controller = new AbortController();
    const store = createCollections(api, controller.signal, {
        setTimer: (run, delay) => { timers.push({ run, delay, cleared: false }); return timers.length; },
        clearTimer: id => { if (timers[id - 1]) timers[id - 1].cleared = true; },
    });
    store.load();
    pending.shift().resolve(data);
    await store.settled();
    return { store, api, pending, timers, controller };
}

test('the route hash, the name rule and the limits', async () => {
    const { collectionHash, normalizeCollectionName, MAX_COLLECTION_NAME_LENGTH, MAX_COLLECTION_ITEMS,
        COLLECTION_COUNT_DELAY_MS } = await source;
    assert.equal(collectionHash(12), '#/collection/12');
    assert.throws(() => collectionHash(0), TypeError);
    assert.throws(() => collectionHash('3'), TypeError);
    assert.equal(MAX_COLLECTION_NAME_LENGTH, 100);
    assert.equal(MAX_COLLECTION_ITEMS, 500);
    assert.equal(COLLECTION_COUNT_DELAY_MS, 300);
    // Trimmed, each run of whitespace one space, 1 to 100 characters, no control characters (design §1).
    assert.equal(normalizeCollectionName('  LoRa   testing   grids '), 'LoRa testing grids');
    assert.equal(normalizeCollectionName('x'.repeat(100)), 'x'.repeat(100));
    for (const bad of ['', '   ', 'x'.repeat(101), 'bad\u0007name', 7, null, undefined]) {
        assert.equal(normalizeCollectionName(bad), null, String(bad));
    }
    // A tab or newline is whitespace, collapsed rather than refused.
    assert.equal(normalizeCollectionName('a\tb\nc'), 'a b c');
});

test('the listing keeps usable rows in the server order, with counts only when they are counts', async () => {
    const { parseCollections, parseCollection } = await source;
    assert.deepEqual(parseCollections({ success: true, collections: [
        { id: 3, name: 'b', count: 2, missing: 0 },
        { id: 0, name: 'bad id' },
        { id: 1, name: '  ', count: 1, missing: 0 },
        { id: 7, name: 'A', count: null, missing: null },
        { id: 9, name: 'c', count: -1, missing: 1.5 },
    ] }), [
        { id: 3, name: 'b', count: 2, missing: 0 },
        { id: 7, name: 'A', count: null, missing: null },
        { id: 9, name: 'c', count: null, missing: null },
    ]);
    assert.equal(parseCollection(null), null);
    assert.throws(() => parseCollections({ success: true }), TypeError);
});

test('every user-facing string of the design (§7.4, §8)', async () => {
    const c = await source;
    assert.equal(c.addedMessage({ collection: { name: 'X' }, added: 1, already: 0, notFound: 0 }), 'Added 1 image to “X”.');
    assert.equal(c.addedMessage({ collection: { name: 'X' }, added: 3, already: 0, notFound: 0 }), 'Added 3 images to “X”.');
    assert.equal(c.addedMessage({ collection: { name: 'X' }, added: 1, already: 1, notFound: 0 }),
        'Added 1 image to “X”. 1 was already in it.');
    assert.equal(c.addedMessage({ collection: { name: 'X' }, added: 2, already: 3, notFound: 1 }),
        'Added 2 images to “X”. 3 were already in it. 1 could not be found.');
    assert.equal(c.addedMessage({ collection: { name: 'X' }, added: 1, already: 0, notFound: 2 }),
        'Added 1 image to “X”. 2 could not be found.');
    assert.equal(c.addedMessage({ collection: { name: 'X' }, added: 0, already: 4, notFound: 0 }),
        'All selected images were already in “X”.');
    assert.equal(c.FILES_NOT_FOUND_TOAST, 'None of the selected images could be found. Refresh the images and try again.');
    assert.equal(c.SOURCE_CHANGED_TOAST, 'The sources changed. Refresh the images and try again.');
    assert.equal(c.ADD_FAILED_TOAST, 'The images could not be added. Try again.');
    assert.equal(c.removedMessage('X', 1), 'Removed 1 image from “X”.');
    assert.equal(c.removedMessage('X', 4), 'Removed 4 images from “X”.');
    assert.equal(c.REMOVE_FAILED_TOAST, 'The images could not be removed. Try again.');
    assert.equal(c.COLLECTION_GONE_TOAST, 'That collection no longer exists.');
    assert.deepEqual([c.COLLECTION_NOUNS.title, c.COLLECTION_NOUNS.label, c.COLLECTION_NOUNS.deleteTitle],
        ['Edit collection', 'Collection name', 'Delete collection']);
    assert.equal(c.COLLECTION_NOUNS.renamed, 'Collection renamed.');
    assert.equal(c.RENAMED_TOAST, 'Collection renamed.');
    assert.equal(c.nameTakenMessage('LoRa'), 'A collection named “LoRa” already exists.');
    assert.equal(c.RENAME_FAILED_TOAST, 'The collection could not be renamed. Try again.');
    // The stored count: listed members plus missing ones.
    assert.equal(c.deleteQuestion({ name: 'X', count: 1, missing: 0 }),
        'Delete “X”? Its 1 image stays where it is; only the collection is removed.');
    assert.equal(c.deleteQuestion({ name: 'X', count: 3, missing: 1 }),
        'Delete “X”? Its 4 images stay where they are; only the collection is removed.');
    assert.equal(c.deleteQuestion({ name: 'X', count: null, missing: null }),
        'Delete “X”? Its images stay where they are; only the collection is removed.');
    assert.equal(c.COLLECTION_NOUNS.deleteText({ name: 'X', count: 2, missing: 0 }), c.deleteQuestion({ name: 'X', count: 2, missing: 0 }));
    assert.equal(c.DELETED_TOAST, 'Collection deleted.');
    assert.equal(c.DELETE_FAILED_TOAST, 'The collection could not be deleted. Try again.');
    assert.equal(c.DELETED_PAGE_TEXT, 'This collection no longer exists.');
    assert.equal(c.OPEN_IMAGES_LINK, 'Open Images');
    assert.equal(c.VIEW_COLLECTION_DELETED, 'The collection this view uses was deleted.');
    assert.equal(c.LIST_FAILED_TEXT, 'Collections could not be loaded. Your saved data has not changed.');
    assert.equal(c.missingNote(1), '1 image in this collection is not in your current sources.');
    assert.equal(c.missingNote(5), '5 images in this collection are not in your current sources.');
    for (const none of [0, null, undefined, -1, 1.5]) assert.equal(c.missingNote(none), '');
    assert.equal(c.TOO_MANY_TOAST, 'Select at most 500 images.');
    assert.deepEqual([c.ADD_DIALOG_TITLE, c.ADD_DIALOG_LABEL, c.ADD_DIALOG_ACTION],
        ['Add selected images to collection', 'Collection name', 'Add']);
    assert.equal(c.COLLECTIONS_UPDATED_TOAST, 'Collections updated.');
    assert.equal(c.UPDATE_FAILED_TEXT, 'The collections could not be updated. Try again.');
    assert.equal(c.MEMBERSHIP_LOADING_TEXT, 'Loading collections…');
    assert.equal(c.MEMBERSHIP_FAILED_TEXT, 'Collections could not be loaded.');
    assert.equal(c.NO_COLLECTIONS_TEXT, 'No collections yet. Name one below.');
});

test('a write failure maps the routes\' error codes to their strings', async () => {
    const { writeFailure, renameFailure, isCollectionGone, COLLECTION_GONE_TOAST, SOURCE_CHANGED_TOAST,
        FILES_NOT_FOUND_TOAST, ADD_FAILED_TOAST, RENAME_FAILED_TOAST } = await source;
    assert.equal(writeFailure(apiError(404, 'collection-not-found'), ADD_FAILED_TOAST), COLLECTION_GONE_TOAST);
    assert.equal(writeFailure(apiError(409, 'source-changed'), ADD_FAILED_TOAST), SOURCE_CHANGED_TOAST);
    assert.equal(writeFailure(apiError(404, 'files-not-found'), ADD_FAILED_TOAST), FILES_NOT_FOUND_TOAST);
    for (const other of [apiError(500), apiError(400), apiError(404), new TypeError('x'), null]) {
        assert.equal(writeFailure(other, ADD_FAILED_TOAST), ADD_FAILED_TOAST);
    }
    assert.equal(isCollectionGone(apiError(404, 'collection-not-found')), true);
    assert.equal(isCollectionGone(apiError(404)), false);
    // The Edit dialog: a taken name stays in the dialog; a gone collection closes it; anything else keeps it open.
    assert.deepEqual(renameFailure('LoRa')(apiError(409, 'name-taken')), { field: 'A collection named “LoRa” already exists.' });
    assert.deepEqual(renameFailure('LoRa')(apiError(404, 'collection-not-found')), { toast: COLLECTION_GONE_TOAST, close: true });
    assert.deepEqual(renameFailure('LoRa')(apiError(500)), { toast: RENAME_FAILED_TOAST });
});

test('the store reports loads, keeps the last list after a failed read, and a newer read supersedes', async () => {
    const { createCollections, COLLECTIONS_URL } = await source;
    const { api, pending } = fakeApi();
    const store = createCollections(api);
    assert.equal(store.status, 'loading');
    const seen = [];
    store.subscribe(next => seen.push([next.status, next.collections.map(item => item.id)]));
    store.load();
    const settled = store.settled();
    pending[0].resolve(LISTED);
    assert.equal((await settled).status, 'ready');
    assert.deepEqual(api.calls[0], ['GET', COLLECTIONS_URL, 'no-store']);
    assert.deepEqual(store.find(2), { id: 2, name: 'Landscapes', count: 12, missing: 1 });
    assert.equal(store.find(3), null);
    assert.deepEqual(store.collections.map(item => item.name), ['Experiments', 'Landscapes']);

    store.load();
    pending[1].reject(new Error('offline'));
    await store.settled();
    assert.equal(store.status, 'error');
    assert.deepEqual(store.collections.map(item => item.id), [4, 2]);

    store.load();
    store.load();
    pending[3].resolve({ success: true, collections: [{ id: 8, name: 'New', count: 0, missing: 0 }] });
    pending[2].resolve(LISTED);
    await store.settled();
    assert.deepEqual(store.collections.map(item => item.id), [8]);
    assert.deepEqual(seen.at(-1), ['ready', [8]]);
    // A malformed listing is a failed read.
    store.load();
    pending[4].resolve({ success: true });
    await store.settled();
    assert.equal(store.status, 'error');
});

test('adding by name or id sends only the file identity and re-reads the counts 300 ms after the last change', async () => {
    const { store, api, pending, timers } = await readyStore();
    const { COLLECTION_ITEMS_URL } = await source;
    const result = await store.add({ name: '  Grids  ' }, [{ ...FILE, title: 'ignored', favorite: true }]);
    assert.deepEqual(api.calls.at(-1), ['POST', COLLECTION_ITEMS_URL, { name: 'Grids', items: [FILE] }]);
    assert.deepEqual(result, { collection: { id: 5, name: 'Grids' }, created: true, added: 1, already: 0, notFound: 0 });
    // The new collection is listed at once (its count arrives with the re-read), in name order.
    assert.deepEqual(store.collections.map(item => [item.id, item.count]), [[4, 8], [5, null], [2, 12]]);
    api.postResult = Promise.resolve({ success: true, collection: { id: 2, name: 'Landscapes' }, created: false, added: 0,
        already: 1, not_found: 1 });
    const again = await store.add({ collectionId: 2 }, [FILE, { ...FILE, path: 'b.png' }]);
    assert.deepEqual(api.calls.at(-1)[2], { collection_id: 2, items: [FILE, { ...FILE, path: 'b.png' }] });
    assert.deepEqual(again, { collection: { id: 2, name: 'Landscapes' }, created: false, added: 0, already: 1, notFound: 1 });
    // One trailing re-read for the run of writes.
    assert.deepEqual(timers.map(timer => [timer.delay, timer.cleared]), [[300, true], [300, false]]);
    const reads = api.calls.filter(call => call[0] === 'GET').length;
    timers.at(-1).run();
    assert.equal(api.calls.filter(call => call[0] === 'GET').length, reads + 1);
    pending.shift().resolve(LISTED);
    await store.settled();
});

test('a write is refused before sending when its target or files are not valid', async () => {
    const { store, api } = await readyStore();
    const sent = api.calls.length;
    const many = Array.from({ length: 501 }, (_, index) => ({ ...FILE, path: `${index}.png` }));
    for (const [target, items] of [
        [{ name: '' }, [FILE]], [{ name: 'x'.repeat(101) }, [FILE]], [{ collectionId: 0 }, [FILE]], [{}, [FILE]],
        [{ name: 'a', collectionId: 2 }, [FILE]], [{ collectionId: 2 }, []], [{ collectionId: 2 }, many],
        [{ collectionId: 2 }, [{ ...FILE, root_index: -1 }]], [{ collectionId: 2 }, [{ ...FILE, path: '' }]],
        [{ collectionId: 2 }, [{ ...FILE, root_scope: 3 }]], [{ collectionId: 2 }, 'a.png'],
    ]) {
        await assert.rejects(store.add(target, items), TypeError);
    }
    await assert.rejects(store.remove(0, [FILE]), TypeError);
    await assert.rejects(store.remove(2, []), TypeError);
    await assert.rejects(store.rename(2, '   '), TypeError);
    await assert.rejects(store.delete(-1), TypeError);
    await assert.rejects(store.membership({ root_index: 'x', root_scope: 'a', path: 'p' }), TypeError);
    assert.equal(api.calls.length, sent);
});

test('a write to a collection that is gone re-reads the list at once and rejects with the error', async () => {
    const { store, api, pending } = await readyStore();
    const { isCollectionGone } = await source;
    api.postResult = Promise.reject(apiError(404, 'collection-not-found'));
    const reads = api.calls.filter(call => call[0] === 'GET').length;
    await assert.rejects(store.add({ collectionId: 2 }, [FILE]), error => isCollectionGone(error));
    assert.equal(api.calls.filter(call => call[0] === 'GET').length, reads + 1);
    pending.shift().resolve({ success: true, collections: [LISTED.collections[0]] });
    await store.settled();
    assert.equal(store.find(2), null);
    // Another failure re-reads nothing.
    api.deleteResult = Promise.reject(apiError(500));
    await assert.rejects(store.remove(4, [FILE]), error => error.status === 500);
    assert.equal(api.calls.filter(call => call[0] === 'GET').length, reads + 1);
});

test('remove sends the collection and files; membership reads one file\'s collections', async () => {
    const { store, api, timers } = await readyStore();
    const { COLLECTION_ITEMS_URL, COLLECTION_MEMBERSHIP_URL } = await source;
    assert.deepEqual(await store.remove(2, [FILE]), { collection: { id: 2, name: 'Portraits' }, removed: 1 });
    assert.deepEqual(api.calls.at(-1), ['DELETE', COLLECTION_ITEMS_URL, { collection_id: 2, items: [FILE] }]);
    assert.equal(timers.length, 1);
    assert.deepEqual(await store.membership(FILE), [2]);
    const [method, url] = api.calls.at(-1);
    assert.equal(method, 'GET');
    const query = new URL(url, 'http://local');
    assert.equal(query.pathname, COLLECTION_MEMBERSHIP_URL);
    assert.deepEqual(Object.fromEntries(query.searchParams), { root_index: '0', root_scope: FILE.root_scope, path: FILE.path });
    api.membershipResult = Promise.resolve({ success: true, collection_ids: [1, 'x'] });
    await assert.rejects(store.membership(FILE), TypeError);
});

test('rename and delete change the listed row at once and re-read the counts', async () => {
    const { store, api, timers } = await readyStore();
    const { COLLECTIONS_URL } = await source;
    const renamed = await store.rename(2, '  A  grids ');
    assert.deepEqual(api.calls.at(-1), ['PATCH', `${COLLECTIONS_URL}/2`, { name: 'A grids' }]);
    assert.deepEqual(renamed, { id: 2, name: 'A grids' });
    // The row keeps its counts and moves to its new place in name order.
    assert.deepEqual(store.collections.map(item => [item.id, item.name, item.count]), [[2, 'A grids', 12], [4, 'Experiments', 8]]);
    assert.equal(await store.delete(4), 4);
    assert.deepEqual(api.calls.at(-1), ['DELETE', `${COLLECTIONS_URL}/4`, undefined]);
    assert.deepEqual(store.collections.map(item => item.id), [2]);
    // Already gone (another tab): the outcome the user asked for.
    api.deleteResult = Promise.reject(apiError(404, 'collection-not-found'));
    assert.equal(await store.delete(2), 2);
    assert.deepEqual(store.collections, []);
    api.patchResult = Promise.reject(apiError(409, 'name-taken'));
    await assert.rejects(store.rename(9, 'Taken'), error => error.code === 'name-taken');
    assert.equal(timers.filter(timer => !timer.cleared).length, 1);
});

test('a disposed store stops its count timer, drops a read in flight and reads no more', async () => {
    const { store, timers, controller, api, pending } = await readyStore();
    await store.rename(2, 'Kept');
    const seen = [];
    store.subscribe(next => seen.push(next));
    store.load();
    controller.abort();
    assert.equal(timers.at(-1).cleared, true);
    pending.shift().resolve(LISTED);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(seen.filter(next => next.status === 'ready').length, 0);
    const sent = api.calls.length;
    await store.load();
    assert.equal(api.calls.length, sent);
});
