// The Images selection bar's collection actions (#331 D138, design §7): Add to collection after Set category, Remove
// from collection beside it while a collection filter is active, the 500 cap and every toast of §7.4.
import test from 'node:test';
import assert from 'node:assert/strict';

// Guarded: before the module exists every test fails on an assertion, not on the import.
const loaded = await import('../../web/js/workspace/images-collections.mjs').catch(() => null);
const module = () => { assert.ok(loaded, 'images-collections.mjs is not implemented'); return loaded; };
const { mountSelectionBar } = await import('../../web/js/workspace/images-selection.mjs');
const { createScope } = await import('../../web/js/shared/toolkit/core.js');
const strings = await import('../../web/js/workspace/collections.mjs');

const SCOPE = 'b'.repeat(64);
const image = (path, extra = {}) => ({ root_index: 0, root_scope: SCOPE, path, filename: path.split('/').pop(), ...extra });
const target = path => ({ root_index: 0, root_scope: SCOPE, path });
const images = count => Array.from({ length: count }, (_, index) => image(`set/${index}.png`));
const keyOf = item => `${item.root_index}:${item.path}`;
const flush = () => new Promise(resolve => setTimeout(resolve, 0));
const coded = (code, status = 400) => Object.assign(new Error(code), { name: 'ApiError', code, status });

/** A minimal DOM for the selection bar: elements with order, attributes, listeners, focus and a toast. */
function fakeDOM() {
    const previous = globalThis.document;
    let active = null;
    class Element extends EventTarget {
        constructor(tag) {
            super();
            this.tagName = tag.toUpperCase();
            this.children = [];
            this.parentNode = null;
            this.attributes = {};
            this.dataset = {};
            this.className = '';
            this.id = '';
            this._text = '';
        }
        get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
        set textContent(text) { this.children = []; this._text = String(text); }
        get nextElementSibling() {
            const siblings = this.parentNode?.children ?? [];
            return siblings[siblings.indexOf(this) + 1] ?? null;
        }
        append(...nodes) {
            for (const node of nodes) { node.remove(); node.parentNode = this; this.children.push(node); }
        }
        after(node) {
            node.remove();
            node.parentNode = this.parentNode;
            this.parentNode.children.splice(this.parentNode.children.indexOf(this) + 1, 0, node);
        }
        remove() {
            if (!this.parentNode) return;
            this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1);
            this.parentNode = null;
        }
        setAttribute(name, value) { this.attributes[name] = String(value); }
        getAttribute(name) { return Object.hasOwn(this.attributes, name) ? this.attributes[name] : null; }
        removeAttribute(name) { delete this.attributes[name]; }
        querySelector() { return this.children.find(child => child.tagName === 'BUTTON' && child.getAttribute('aria-disabled') !== 'true') ?? null; }
        querySelectorAll() { return []; }
        focus() { active = this; }
        click() { this.dispatchEvent(new Event('click')); }
    }
    const toast = new Element('div');
    const body = new Element('body');
    globalThis.document = {
        body,
        get activeElement() { return active ?? body; },
        createElement: tag => new Element(tag),
        createTextNode: text => Object.assign(new Element('#text'), { _text: String(text) }),
        getElementById: id => (id === 'workspace-toast' ? toast : null),
    };
    return { Element, body, toast, restore() { globalThis.document = previous; } };
}

/** The bar over `items` with `checked` keys; `options` adds the collection props. */
function mountBar(dom, items, checkedKeys, options = {}) {
    const scope = createScope();
    const grid = new dom.Element('div');
    const focusFallback = new dom.Element('h1');
    const bar = mountSelectionBar({ scope, api: {}, grid, getItems: () => items, focusFallback, onCategorySet() {},
        activity: options.activity ?? null, onTagged() {}, getFilter: () => ({}),
        getListing: () => ({ total: options.total ?? items.length, hasMore: false, positiveCount: null }),
        checked: Object.freeze(new Set(checkedKeys)), ...options.props });
    return { scope, bar, labels: () => bar.element.children[0]?.children.map(child => child.textContent) ?? [],
        button: text => bar.element.children[0].children.find(child => child.textContent === text) };
}

function store(overrides = {}) {
    const calls = [];
    return {
        calls,
        collections: [{ id: 3, name: 'Experiments', count: 8, missing: 0 }, { id: 7, name: 'Landscapes', count: 12, missing: 1 }],
        add: async (where, items) => { calls.push(['add', where, items]); return overrides.add?.(where, items); },
        remove: async (id, items) => { calls.push(['remove', id, items]); return overrides.remove?.(id, items); },
    };
}

test('selection bar lists Add to collection after Set category', () => {
    const dom = fakeDOM();
    try {
        const items = images(2);
        const collections = store();
        const plain = mountBar(dom, items, [keyOf(items[0])], { activity: {}, props: { collections } });
        assert.deepEqual(plain.labels(), ['1 selected', 'Add tags', 'Auto-tag selected', 'Set category', 'Add to collection',
            'Export metadata', 'Clear']);
        plain.scope.dispose();
        // §7.1: Remove from collection beside Add, only while a collection filter is active.
        const filtered = mountBar(dom, items, [keyOf(items[0])], { activity: {}, props: { collections, collection: '7' } });
        assert.deepEqual(filtered.labels(), ['1 selected', 'Add tags', 'Auto-tag selected', 'Set category', 'Add to collection',
            'Remove from collection', 'Export metadata', 'Clear']);
        filtered.scope.dispose();
        // Without the client store (a page that has none) neither shows.
        const none = mountBar(dom, items, [keyOf(items[0])]);
        assert.deepEqual(none.labels(), ['1 selected', 'Set category', 'Export metadata', 'Clear']);
        none.scope.dispose();
    } finally { dom.restore(); }
});

test('more than 500 checked toasts and opens nothing', async () => {
    const dom = fakeDOM();
    try {
        const items = images(501);
        const collections = store();
        const view = mountBar(dom, items, items.map(keyOf), { props: { collections, collection: '7' } });
        view.button('Add to collection').click();
        assert.equal(dom.toast.textContent, 'Select at most 500 images.');
        assert.equal(dom.body.children.length, 0, 'no dialog opened');
        dom.toast.textContent = '';
        view.button('Remove from collection').click();
        await flush();
        assert.equal(dom.toast.textContent, 'Select at most 500 images.');
        assert.deepEqual(collections.calls, []);
        view.scope.dispose();
    } finally { dom.restore(); }
});

test('Remove from collection removes the checked files and reloads the listing', async () => {
    const dom = fakeDOM();
    try {
        const items = images(3);
        let changed = 0;
        const collections = store({ remove: () => ({ collection: { id: 7, name: 'Landscapes' }, removed: 2 }) });
        const view = mountBar(dom, items, [keyOf(items[0]), keyOf(items[2])], { props: { collections, collection: '7',
            onCollectionsChanged: () => { changed++; } } });
        const remove = view.button('Remove from collection');
        remove.click();
        // A second press while the first is in flight is ignored.
        assert.equal(remove.getAttribute('aria-disabled'), 'true');
        remove.click();
        await flush();
        assert.deepEqual(collections.calls, [['remove', 7, [target('set/0.png'), target('set/2.png')]]]);
        assert.equal(dom.toast.textContent, 'Removed 2 images from “Landscapes”.');
        assert.equal(changed, 1);
        assert.equal(remove.getAttribute('aria-disabled'), null);
        view.scope.dispose();
    } finally { dom.restore(); }
});

test('the collection-target rule keeps the all-matching selection to its loaded images', () => {
    const { collectionTargets } = module();
    assert.deepEqual(collectionTargets(images(2)), { targets: [target('set/0.png'), target('set/1.png')] });
    assert.deepEqual(collectionTargets(images(501)), { refusal: strings.TOO_MANY_TOAST });
    // All N matching: only when every match is loaded and checked, and at most 500.
    assert.deepEqual(collectionTargets(images(2), { allMatching: true, total: 2 }), { targets: [target('set/0.png'), target('set/1.png')] });
    assert.deepEqual(collectionTargets(images(2), { allMatching: true, total: 3 }), { refusal: strings.TOO_MANY_TOAST });
    assert.deepEqual(collectionTargets(images(501), { allMatching: true, total: 501 }), { refusal: strings.TOO_MANY_TOAST });
    // A file without a listing identity cannot be named; none left is "could not be found".
    assert.deepEqual(collectionTargets([{ ...image('x.png'), root_scope: 'nope' }]), { refusal: strings.FILES_NOT_FOUND_TOAST });
});

const pageScope = () => {
    const controller = new AbortController();
    return { signal: controller.signal, cleanup() {}, abort: () => controller.abort() };
};

test('Add to collection opens the concept dialog with the existing names as suggestions', () => {
    const { openAddToCollection } = module();
    let dialog = null;
    const collections = store();
    openAddToCollection({ scope: pageScope(), collections, images: images(1), trigger: 'trigger', notify: assert.fail,
        openInput: args => { dialog = args; } });
    assert.equal(dialog.title, 'Add selected images to collection');
    assert.equal(dialog.label, 'Collection name');
    assert.equal(dialog.action, 'Add');
    assert.equal(dialog.maxLength, 100);
    assert.equal(dialog.trigger, 'trigger');
    assert.deepEqual(dialog.suggestions, ['Experiments', 'Landscapes']);
    assert.equal(dialog.value ?? '', '');
});

/** Apply `name` in the Add dialog over `count` images; resolves with the toasts and the store's calls. */
async function add(name, answer, { count = 1, extra = {} } = {}) {
    const { openAddToCollection } = module();
    const toasts = [];
    let dialog = null, sourceChanged = 0;
    const collections = store({ add: answer });
    openAddToCollection({ scope: pageScope(), collections, images: images(count), notify: text => toasts.push(text),
        openInput: args => { dialog = args; }, onSourceChanged: () => { sourceChanged++; }, ...extra });
    await dialog.onApply(name);
    return { toasts, calls: collections.calls, sourceChanged };
}
const answer = (added, already, notFound, created = false) => () => ({ collection: { id: 9, name: 'LoRa testing' }, created,
    added, already, notFound });

test('Add sends the normalized name and the checked files', async () => {
    const { toasts, calls } = await add('  LoRa   testing ', answer(3, 0, 0, true), { count: 3 });
    assert.deepEqual(calls, [['add', { name: 'LoRa testing' }, [target('set/0.png'), target('set/1.png'), target('set/2.png')]]]);
    assert.deepEqual(toasts, ['Added 3 images to “LoRa testing”.']);
});

test('Add toasts every outcome of §7.4', async () => {
    const cases = [
        [answer(1, 0, 0), 'Added 1 image to “LoRa testing”.'],
        [answer(1, 1, 0), 'Added 1 image to “LoRa testing”. 1 was already in it.'],
        [answer(2, 3, 0), 'Added 2 images to “LoRa testing”. 3 were already in it.'],
        [answer(1, 0, 1), 'Added 1 image to “LoRa testing”. 1 could not be found.'],
        [answer(1, 2, 4), 'Added 1 image to “LoRa testing”. 2 were already in it. 4 could not be found.'],
        [answer(0, 2, 0), 'All selected images were already in “LoRa testing”.'],
        [answer(0, 1, 1), 'Added 0 images to “LoRa testing”. 1 was already in it. 1 could not be found.'],
        [() => { throw coded('files-not-found', 404); }, 'None of the selected images could be found. Refresh the images and try again.'],
        [() => { throw coded('collection-not-found', 404); }, 'That collection no longer exists.'],
        [() => { throw new TypeError('offline'); }, 'The images could not be added. Try again.'],
    ];
    for (const [reply, text] of cases) assert.deepEqual((await add('LoRa testing', reply)).toasts, [text], text);
});

test('a stale source on Add says so and reloads the listing', async () => {
    const { toasts, sourceChanged } = await add('Grids', () => { throw coded('source-changed', 409); });
    assert.deepEqual(toasts, ['The sources changed. Refresh the images and try again.']);
    assert.equal(sourceChanged, 1);
});

test('Add with a name the store cannot take sends nothing and says it failed', async () => {
    const { toasts, calls } = await add('bad\u0001name', answer(1, 0, 0));
    assert.deepEqual(calls, []);
    assert.deepEqual(toasts, ['The images could not be added. Try again.']);
});

test('Add after the page left shows no toast', async () => {
    const { openAddToCollection } = module();
    const scope = pageScope();
    const toasts = [];
    let dialog = null;
    openAddToCollection({ scope, collections: store({ add: answer(1, 0, 0) }), images: images(1), notify: text => toasts.push(text),
        openInput: args => { dialog = args; } });
    scope.abort();
    await dialog.onApply('Grids');
    assert.deepEqual(toasts, []);
});

test('Add refuses an over-cap or all-matching selection before any dialog', () => {
    const { openAddToCollection } = module();
    const toasts = [];
    const open = extra => openAddToCollection({ scope: pageScope(), collections: store(), notify: text => toasts.push(text),
        openInput: () => assert.fail('no dialog opens'), ...extra });
    open({ images: images(501) });
    open({ images: images(2), allMatching: true, total: 40 });
    assert.deepEqual(toasts, [strings.TOO_MANY_TOAST, strings.TOO_MANY_TOAST]);
});

/** Remove `count` images from collection 7 with `reply`; resolves with the toasts and callbacks. */
async function remove(reply, count = 1) {
    const { removeFromCollection } = module();
    const toasts = [];
    let changed = 0, sourceChanged = 0;
    const collections = store({ remove: reply });
    await removeFromCollection({ scope: pageScope(), collections, collectionId: 7, images: images(count),
        notify: text => toasts.push(text), onChanged: () => { changed++; }, onSourceChanged: () => { sourceChanged++; } });
    return { toasts, changed, sourceChanged, calls: collections.calls };
}

test('Remove toasts every outcome of §7.4', async () => {
    assert.deepEqual(await remove(() => ({ collection: { id: 7, name: 'Landscapes' }, removed: 1 })),
        { toasts: ['Removed 1 image from “Landscapes”.'], changed: 1, sourceChanged: 0, calls: [['remove', 7, [target('set/0.png')]]] });
    assert.deepEqual((await remove(() => ({ collection: { id: 7, name: 'Landscapes' }, removed: 3 }), 3)).toasts,
        ['Removed 3 images from “Landscapes”.']);
    // A failure keeps the listing; a deleted collection reloads it (the page then says the collection is gone).
    const failed = await remove(() => { throw new TypeError('offline'); });
    assert.deepEqual([failed.toasts, failed.changed], [['The images could not be removed. Try again.'], 0]);
    const gone = await remove(() => { throw coded('collection-not-found', 404); });
    assert.deepEqual([gone.toasts, gone.changed], [['That collection no longer exists.'], 1]);
    const stale = await remove(() => { throw coded('source-changed', 409); });
    assert.deepEqual([stale.toasts, stale.changed, stale.sourceChanged], [['The sources changed. Refresh the images and try again.'], 0, 1]);
});
