// The shared concept toolbar and dialogs (#249 C1): concept app.js toolbar() a347–360, "Refine this view"
// a1348–1364, "Import & inspect" a1483–1491 (D15) and the "Copy prompt" fallback a1223–1241.
const test = require('node:test');
const assert = require('node:assert/strict');

const toolbarModule = import('../../web/js/workspace/toolbar.mjs');
const dialogsModule = import('../../web/js/workspace/workspace-dialogs.mjs');

function optionsCollection(list) {
    const collection = { length: list.length, item: index => list[index] ?? null, [Symbol.iterator]: () => list[Symbol.iterator]() };
    list.forEach((option, index) => { collection[index] = option; });
    return Object.freeze(collection);
}

/** A minimal DOM: enough element behavior for markup, events, focus and dialogs. */
function fakeDOM() {
    const previous = { document: global.document };
    let active = null;
    class Element extends EventTarget {
        constructor(tag) {
            super();
            this.tagName = tag.toUpperCase();
            this.children = [];
            this.attributes = {};
            this.parentNode = null;
            this.className = '';
            this.id = '';
            this.value = '';
            this.disabled = false;
            this.open = false;
            this.selected = false;
            this.selectionStart = 0;
            this.selectionEnd = 0;
            this._text = '';
        }
        get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
        set textContent(text) { this.children = []; this._text = String(text); }
        get isConnected() { return this === body || Boolean(this.parentNode?.isConnected); }
        // Like a browser's HTMLOptionsCollection: indexed and iterable, but not an Array (no .some/.map).
        get options() { return optionsCollection(this.children.filter(child => child.tagName === 'OPTION')); }
        append(...nodes) {
            for (const node of nodes) {
                node.parentNode?.children.splice(node.parentNode.children.indexOf(node), 1);
                node.parentNode = this;
                this.children.push(node);
            }
        }
        replaceChildren(...nodes) {
            for (const child of this.children) child.parentNode = null;
            this.children = [];
            this._text = '';
            this.append(...nodes);
        }
        remove() {
            if (!this.parentNode) return;
            this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1);
            this.parentNode = null;
        }
        setAttribute(name, value) { this.attributes[name] = String(value); }
        getAttribute(name) { return Object.hasOwn(this.attributes, name) ? this.attributes[name] : null; }
        removeAttribute(name) { delete this.attributes[name]; }
        focus() { active = this; }
        select() { this.selectionStart = 0; this.selectionEnd = this.value.length; }
        showModal() { this.open = true; }
        close() {
            if (!this.open) return;
            this.open = false;
            // Browsers queue the close event as a later task.
            setTimeout(() => this.dispatchEvent(new Event('close')), 0);
        }
        click() { this.dispatchEvent(new Event('click', { cancelable: true })); }
    }
    const body = new Element('body');
    global.document = {
        body,
        get activeElement() { return active ?? body; },
        createElement: tag => new Element(tag),
        createElementNS: (namespace, tag) => new Element(tag),
        createTextNode: text => Object.assign(new Element('#text'), { _text: String(text) }),
    };
    const all = (root, predicate, found = []) => {
        if (predicate(root)) found.push(root);
        for (const child of root.children) all(child, predicate, found);
        return found;
    };
    return {
        body, Element,
        all: predicate => all(body, predicate),
        find: (root, predicate) => all(root, predicate)[0],
        restore() { Object.assign(global, previous); },
    };
}

const byId = (dom, id) => dom.all(node => node.id === id)[0];
const tags = node => node.children.map(child => child.tagName.toLowerCase());
const optionPairs = select => [...select.options].map(option => [option.value, option.textContent]);
const modal = dom => byId(dom, 'modal');
const tick = () => new Promise(resolve => setTimeout(resolve, 5));
const coreModule = import('../../web/js/shared/toolkit/core.js');
const button = (root, text) => {
    const found = [];
    const walk = node => {
        if (node.tagName === 'BUTTON' && node.textContent === text) found.push(node);
        node.children.forEach(walk);
    };
    walk(root);
    return found[0];
};

test('toolbar emits concept markup and order', async () => {
    const { mountToolbar } = await toolbarModule;
    const { createScope } = await import('../../web/js/shared/toolkit/core.js');
    const dom = fakeDOM();
    const scope = createScope();
    try {
        const container = new dom.Element('div');
        dom.body.append(container);
        const changes = [];
        const filters = [];
        const saves = [];
        const toolbar = mountToolbar(container, {
            scope, categories: ['Portraits', 'Landscapes'],
            sorts: [['newest', 'Recently added'], ['name', 'Name A–Z'], ['rating', 'Highest rated']],
            value: { q: 'castle', category: 'Landscapes', sort: 'name' },
            onChange: value => changes.push(value), onFilters: trigger => filters.push(trigger),
            onSaveView: trigger => saves.push(trigger),
        });
        const root = container.children[0];
        assert.equal(toolbar.element, root);
        assert.equal(root.tagName, 'SEARCH');
        assert.equal(root.className, 'toolbar');
        assert.deepEqual(tags(root), ['label', 'select', 'select', 'button', 'button']);

        const [label, category, sort, saveButton, filtersButton] = root.children;
        assert.equal(label.className, 'search-field');
        assert.equal(label.htmlFor, 'search');
        assert.deepEqual(tags(label), ['svg', 'input']);
        assert.equal(label.children[0].getAttribute('class'), 'icon');
        assert.equal(label.children[0].getAttribute('aria-hidden'), 'true');
        const input = label.children[1];
        assert.equal(input.id, 'search');
        assert.equal(input.type, 'search');
        assert.equal(input.placeholder, 'Search prompts, tags, filenames…');
        // The listing API rejects search text over 500 characters, as the legacy search capped it.
        assert.equal(input.maxLength, 500);
        assert.equal(input.getAttribute('aria-label'), 'Search workspace');
        assert.equal(input.value, 'castle');

        assert.equal(category.id, 'category-filter');
        assert.equal(category.getAttribute('aria-label'), 'Filter by category');
        assert.deepEqual(optionPairs(category), [['', 'All categories'], ['Portraits', 'Portraits'], ['Landscapes', 'Landscapes']]);
        assert.equal(category.value, 'Landscapes');
        assert.equal(sort.id, 'sort');
        assert.equal(sort.getAttribute('aria-label'), 'Sort results');
        assert.equal(sort.value, 'name');

        // The concept's Save view, between sort and Filters (a359), reports its own button as the trigger.
        assert.equal(button(root, 'Save view'), saveButton);
        assert.equal(saveButton.type, 'button');
        saveButton.click();
        assert.deepEqual(saves, [saveButton]);
        assert.deepEqual(filters, []);
        assert.equal(filtersButton.textContent, 'Filters');
        assert.equal(filtersButton.type, 'button');
        filtersButton.click();
        assert.deepEqual(filters, [filtersButton]);

        category.value = '';
        category.dispatchEvent(new Event('change'));
        sort.value = 'rating';
        sort.dispatchEvent(new Event('change'));
        assert.deepEqual(changes, [
            { q: 'castle', category: '', sort: 'name' },
            { q: 'castle', category: '', sort: 'rating' },
        ]);

        // Categories refresh without dropping a still-listed selection.
        toolbar.setCategories(['Archive']);
        assert.deepEqual(optionPairs(category), [['', 'All categories'], ['Archive', 'Archive']]);
        toolbar.setValue({ q: 'moon', category: 'Archive', sort: 'newest' });
        assert.deepEqual([input.value, category.value, sort.value], ['moon', 'Archive', 'newest']);
        assert.deepEqual(toolbar.value(), { q: 'moon', category: 'Archive', sort: 'newest' });
        // A still-listed selection survives a category refresh.
        toolbar.setCategories(['Portraits', 'Archive']);
        assert.equal(category.value, 'Archive');
        assert.deepEqual(optionPairs(category), [['', 'All categories'], ['Portraits', 'Portraits'], ['Archive', 'Archive']]);
        // A selection that is no longer listed drops to All categories.
        toolbar.setCategories(['Portraits']);
        assert.equal(category.value, '');
        // An unknown sort falls back to the first option instead of an empty select.
        toolbar.setValue({ sort: 'bogus' });
        assert.equal(sort.value, 'newest');
        assert.throws(() => mountToolbar(container, { categories: [], sorts: [['newest', 'Recently added']],
            onChange() {}, onFilters() {}, onSaveView() {} }), TypeError);
        // Every toolbar page offers Save view, so the callback is required.
        assert.throws(() => mountToolbar(container, { scope, categories: [], sorts: [['newest', 'Recently added']],
            onChange() {}, onFilters() {} }), TypeError);
    } finally {
        scope.dispose();
        dom.restore();
    }
});

test('toolbar search uses a debounced onChange that stops with its scope', async () => {
    const { mountToolbar, SEARCH_DELAY } = await toolbarModule;
    const { createScope } = await import('../../web/js/shared/toolkit/core.js');
    const dom = fakeDOM();
    const scope = createScope();
    try {
        const container = new dom.Element('div');
        const changes = [];
        mountToolbar(container, { scope, categories: [], sorts: [['newest', 'Recently added']],
            value: { q: '', category: '', sort: 'newest' }, onChange: value => changes.push(value), onFilters() {}, onSaveView() {} });
        const input = container.children[0].children[0].children[1];
        assert.equal(input.id, 'search');
        for (const text of ['c', 'ca', 'cat']) {
            input.value = text;
            input.dispatchEvent(new Event('input'));
        }
        assert.deepEqual(changes, []);
        await new Promise(resolve => setTimeout(resolve, SEARCH_DELAY + 30));
        assert.deepEqual(changes, [{ q: 'cat', category: '', sort: 'newest' }]);
        input.value = 'dog';
        input.dispatchEvent(new Event('input'));
        scope.dispose();
        await new Promise(resolve => setTimeout(resolve, SEARCH_DELAY + 30));
        assert.equal(changes.length, 1);
        assert.equal(container.children.length, 0);
    } finally {
        scope.dispose();
        dom.restore();
    }
});

test('sort lists concept options first', async () => {
    const { mountToolbar, orderSorts } = await toolbarModule;
    const { createScope } = await import('../../web/js/shared/toolkit/core.js');
    // D14: the concept's three, in the concept's order, then the page's appended options in their own order.
    const given = [['newest', 'Recently added'], ['oldest', 'Oldest first'], ['name-desc', 'Filename Z–A'],
        ['rating', 'Highest rated'], ['name', 'Name A–Z']];
    assert.deepEqual(orderSorts(given), [['newest', 'Recently added'], ['name', 'Name A–Z'], ['rating', 'Highest rated'],
        ['oldest', 'Oldest first'], ['name-desc', 'Filename Z–A']]);
    // A page without a concept option keeps the ones it has, concept first.
    assert.deepEqual(orderSorts([['oldest', 'Oldest first'], ['newest', 'Recently added']]),
        [['newest', 'Recently added'], ['oldest', 'Oldest first']]);
    // Matching is by the concept's exact labels (en dash): a variant label is an appended option.
    assert.deepEqual(orderSorts([['name', 'Name A-Z'], ['newest', 'Recently added']]),
        [['newest', 'Recently added'], ['name', 'Name A-Z']]);
    assert.throws(() => orderSorts([]), TypeError);
    assert.throws(() => orderSorts([['newest']]), TypeError);

    const dom = fakeDOM();
    const scope = createScope();
    try {
        const container = new dom.Element('div');
        mountToolbar(container, { scope, categories: [], sorts: given, value: { q: '', category: '', sort: 'oldest' },
            onChange() {}, onFilters() {}, onSaveView() {} });
        const sort = container.children[0].children[2];
        assert.deepEqual(optionPairs(sort).map(([, label]) => label),
            ['Recently added', 'Name A–Z', 'Highest rated', 'Oldest first', 'Filename Z–A']);
        assert.equal(sort.value, 'oldest');
    } finally {
        scope.dispose();
        dom.restore();
    }
});

const REFINE_FIELDS = [
    { key: 'source', options: [['all', 'All sources'], ['0', 'output'], ['1', 'archive']] },
    { key: 'folder', options: [['', 'All output folders'], ['portraits', 'portraits']] },
    { key: 'media', options: [['all', 'All media'], ['image', 'Images'], ['video', 'Videos']] },
    { key: 'tags' },
];

test('refine dialog round-trips values and clear resets them', async () => {
    const { openRefineDialog, closeWorkspaceDialog } = await dialogsModule;
    const dom = fakeDOM();
    const scope = (await coreModule).createScope();
    try {
        const trigger = new dom.Element('button');
        dom.body.append(trigger);
        const applied = [];
        const cleared = [];
        const values = { source: '1', folder: 'portraits', media: 'video', tags: 'cinematic' };
        openRefineDialog({ scope, fields: REFINE_FIELDS, values, trigger,
            onApply: value => applied.push(value), onClear: value => cleared.push(value) });
        const dialog = modal(dom);
        assert.equal(dialog.open, true);
        assert.equal(byId(dom, 'modal-title').textContent, 'Refine this view');
        const body = byId(dom, 'modal-body');
        const fields = body.children.filter(child => child.className === 'field');
        assert.deepEqual(fields.map(field => field.children[0].textContent), ['Source', 'Folder', 'Media', 'Tags']);
        assert.deepEqual(fields.map(field => field.children[0].htmlFor), ['filter-source', 'filter-folder', 'filter-media', 'filter-tags']);
        assert.deepEqual(fields.map(field => field.children[1].id), ['filter-source', 'filter-folder', 'filter-media', 'filter-tags']);
        const [source, folder, media, tagsInput] = fields.map(field => field.children[1]);
        assert.deepEqual(optionPairs(source), REFINE_FIELDS[0].options);
        assert.deepEqual([source.value, folder.value, media.value, tagsInput.value], ['1', 'portraits', 'video', 'cinematic']);
        assert.equal(tagsInput.placeholder, 'e.g. cinematic');
        // D14: no mock-status note, because every field is a real filter.
        assert.deepEqual(tags(body), ['div', 'div', 'div', 'div', 'div']);
        assert.equal(body.children.some(child => child.tagName === 'P'), false);
        const actions = body.children[4];
        assert.equal(actions.className, 'dialog-actions');
        assert.deepEqual(actions.children.map(child => [child.textContent, child.className]),
            [['Clear filters', ''], ['Apply filters', 'ca-primary']]);

        // Apply returns the values as edited and leaves the caller's object alone.
        media.value = 'image';
        tagsInput.value = '  moody ';
        button(body, 'Apply filters').click();
        assert.deepEqual(applied, [{ source: '1', folder: 'portraits', media: 'image', tags: 'moody' }]);
        assert.equal(values.media, 'video');
        assert.equal(dialog.open, false);
        assert.equal(trigger.isConnected && document.activeElement, trigger);

        // Reopening with the applied values shows them again (a round trip).
        openRefineDialog({ scope, fields: REFINE_FIELDS, values: applied[0], trigger, onApply: value => applied.push(value), onClear() {} });
        button(byId(dom, 'modal-body'), 'Apply filters').click();
        assert.deepEqual(applied[1], applied[0]);

        // Clear resets every field to its first option (Tags to empty) and reports that.
        openRefineDialog({ scope, fields: REFINE_FIELDS, values: applied[0], trigger, onApply: value => applied.push(value),
            onClear: value => cleared.push(value) });
        const reopened = byId(dom, 'modal-body');
        button(reopened, 'Clear filters').click();
        // The concept's Clear also clears the toolbar category; the page applies `category`.
        assert.deepEqual(cleared, [{ source: 'all', folder: '', media: 'all', tags: '', category: '' }]);
        assert.equal(applied.length, 2);
        assert.equal(modal(dom).open, false);

        // A page offers only its own fields; the modal is the one shared shell.
        openRefineDialog({ scope, fields: [{ key: 'tags' }], values: {}, onApply: value => applied.push(value), onClear() {} });
        const only = byId(dom, 'modal-body').children.filter(child => child.className === 'field');
        assert.deepEqual(only.map(field => field.children[0].textContent), ['Tags']);
        button(byId(dom, 'modal-body'), 'Apply filters').click();
        assert.deepEqual(applied[2], { tags: '' });
        assert.equal(dom.all(node => node.id === 'modal').length, 1);
        assert.throws(() => openRefineDialog({ scope, fields: [{ key: 'evidence' }], values: {}, onApply() {}, onClear() {} }), TypeError);
    } finally {
        closeWorkspaceDialog();
        scope.dispose();
        dom.restore();
    }
});

test('Refine lists options discovered after it opened and keeps the wanted values', async () => {
    const { openRefineDialog, openImportDialog, closeWorkspaceDialog } = await dialogsModule;
    const dom = fakeDOM();
    const scope = (await coreModule).createScope();
    try {
        let bySource = {};
        const optionsFor = source => [['', 'All output folders'], ...(bySource[source] ?? []).map(path => [path, path])];
        const fields = [{ key: 'source', options: [['all', 'All sources']] }, { key: 'folder', options: optionsFor('all'), optionsFor },
            REFINE_FIELDS[2]];
        const applied = [];
        const refine = openRefineDialog({ scope, fields, values: { source: '1', folder: 'temp-only', media: 'video' },
            onApply: value => applied.push(value), onClear() {} });
        const [source, folder, media] = byId(dom, 'modal-body').children.filter(child => child.className === 'field')
            .map(field => field.children[1]);
        // Opened before discovery: only the cleared values.
        assert.deepEqual(optionPairs(source), [['all', 'All sources']]);
        assert.equal(source.value, 'all');
        assert.equal(folder.value, '');
        bySource = { all: ['portraits', 'temp-only'], 0: ['portraits'], 1: ['temp-only'] };
        refine.setOptions('source', [['all', 'All sources'], ['0', 'output'], ['1', 'archive']]);
        // The values it opened with, now offered, and the chosen Source's folders.
        assert.deepEqual(optionPairs(source).map(([value]) => value), ['all', '0', '1']);
        assert.equal(source.value, '1');
        assert.deepEqual(optionPairs(folder), [['', 'All output folders'], ['temp-only', 'temp-only']]);
        assert.equal(folder.value, 'temp-only');
        // A choice made since wins over the opening value; tags and unknown keys are ignored.
        source.value = '0';
        source.dispatchEvent(new Event('change'));
        refine.setOptions('source', [['all', 'All sources'], ['0', 'output'], ['1', 'archive']]);
        refine.setOptions('tags', [['x', 'x']]);
        assert.equal(source.value, '0');
        assert.deepEqual(optionPairs(folder).map(([value]) => value), ['', 'portraits']);
        assert.equal(media.value, 'video');
        button(byId(dom, 'modal-body'), 'Apply filters').click();
        assert.deepEqual(applied, [{ source: '0', folder: '', media: 'video' }]);
        // Closed: a late discovery leaves the shell alone.
        refine.setOptions('source', [['all', 'All sources']]);
        assert.equal(source.options.length, 3);
        // Replaced by another dialog: the same.
        const again = openRefineDialog({ scope, fields, values: {}, onApply() {}, onClear() {} });
        openImportDialog({ scope, onInspect() {}, onRescan() {} });
        again.setOptions('source', [['all', 'All sources'], ['9', 'late']]);
        assert.equal(byId(dom, 'modal-title').textContent, 'Import & inspect');
    } finally {
        closeWorkspaceDialog();
        scope.dispose();
        dom.restore();
    }
});

test('the Folder list follows the chosen Source', async () => {
    const { openRefineDialog, closeWorkspaceDialog } = await dialogsModule;
    const dom = fakeDOM();
    const scope = (await coreModule).createScope();
    try {
        const bySource = { all: ['portraits', 'temp-only'], 0: ['portraits'], 1: ['temp-only'] };
        const optionsFor = source => [['', 'All output folders'], ...(bySource[source] ?? []).map(path => [path, path])];
        const fields = [REFINE_FIELDS[0], { key: 'folder', options: optionsFor('all'), optionsFor }, REFINE_FIELDS[2]];
        const applied = [];
        openRefineDialog({ scope, fields, values: { source: '1', folder: 'temp-only', media: 'all' },
            onApply: value => applied.push(value), onClear() {} });
        const [source, folder] = byId(dom, 'modal-body').children.filter(child => child.className === 'field')
            .map(field => field.children[1]);
        // Opened on a chosen Source: only its folders.
        assert.deepEqual(optionPairs(folder), [['', 'All output folders'], ['temp-only', 'temp-only']]);
        assert.equal(folder.value, 'temp-only');
        // Another Source drops a folder it does not have.
        source.value = '0';
        source.dispatchEvent(new Event('change'));
        assert.deepEqual(optionPairs(folder), [['', 'All output folders'], ['portraits', 'portraits']]);
        assert.equal(folder.value, '');
        folder.value = 'portraits';
        // A folder the next Source also has stays chosen.
        source.value = 'all';
        source.dispatchEvent(new Event('change'));
        assert.deepEqual(optionPairs(folder).map(([value]) => value), ['', 'portraits', 'temp-only']);
        assert.equal(folder.value, 'portraits');
        button(byId(dom, 'modal-body'), 'Apply filters').click();
        assert.deepEqual(applied, [{ source: 'all', folder: 'portraits', media: 'all' }]);
    } finally {
        closeWorkspaceDialog();
        scope.dispose();
        dom.restore();
    }
});

test('a running rescan marks Rescan busy and ignores a second press', async () => {
    const { openImportDialog, closeWorkspaceDialog } = await dialogsModule;
    const dom = fakeDOM();
    const scope = (await coreModule).createScope();
    try {
        const calls = [];
        const handle = openImportDialog({ scope, rescanBusy: true, onInspect: () => calls.push('inspect'),
            onRescan: () => calls.push('rescan') });
        const body = byId(dom, 'modal-body');
        const busy = button(body, 'Rescanning…');
        assert.ok(busy, 'the busy Rescan says it is running');
        assert.equal(busy.getAttribute('aria-disabled'), 'true');
        busy.click();
        assert.deepEqual(calls, []);
        assert.equal(modal(dom).open, true, 'an ignored press keeps the dialog open');
        handle.setRescanBusy(false);
        assert.equal(busy.textContent, 'Rescan');
        assert.equal(busy.getAttribute('aria-disabled'), null);
        busy.click();
        assert.deepEqual(calls, ['rescan']);
        assert.equal(modal(dom).open, false);
    } finally {
        closeWorkspaceDialog();
        scope.dispose();
        dom.restore();
    }
});

test('import dialog lists only real operations', async () => {
    const { openImportDialog, closeWorkspaceDialog } = await dialogsModule;
    const dom = fakeDOM();
    const scope = (await coreModule).createScope();
    try {
        const calls = [];
        openImportDialog({ scope, onInspect: () => calls.push('inspect'), onRescan: () => calls.push('rescan') });
        assert.equal(byId(dom, 'modal-title').textContent, 'Import & inspect');
        const body = byId(dom, 'modal-body');
        assert.deepEqual(tags(body), ['p', 'div']);
        assert.equal(body.children[0].className, 'small');
        const list = body.children[1];
        assert.equal(list.className, 'operation-list');
        const rows = list.children.map(row => {
            assert.equal(row.className, 'operation');
            const [text, actions] = row.children;
            assert.deepEqual(tags(text), ['h3', 'p']);
            assert.equal(actions.className, 'row-actions');
            return [text.children[0].textContent, text.children[1].textContent, actions.children[0].textContent];
        });
        // D15: no "Import portable recipes" until import exists; Rescan finds added or removed files.
        assert.deepEqual(rows, [
            ['Inspect a local image', 'Read embedded metadata before deciding to add it.', 'Open inspector'],
            ['Scan an output folder', 'Find files added or removed in the output folders.', 'Rescan'],
        ]);
        assert.doesNotMatch(body.textContent, /recipe|portable|sample/i);

        button(body, 'Rescan').click();
        assert.deepEqual(calls, ['rescan']);
        assert.equal(modal(dom).open, false);
        openImportDialog({ scope, onInspect: () => calls.push('inspect'), onRescan: () => calls.push('rescan') });
        button(byId(dom, 'modal-body'), 'Open inspector').click();
        assert.deepEqual(calls, ['rescan', 'inspect']);
        assert.equal(modal(dom).open, false);
    } finally {
        closeWorkspaceDialog();
        scope.dispose();
        dom.restore();
    }
});

test('copy fallback selects the text', async () => {
    const { openCopyFallback, closeWorkspaceDialog } = await dialogsModule;
    const dom = fakeDOM();
    const scope = (await coreModule).createScope();
    try {
        const text = '<b>a castle</b>, "moonlight" & fog';
        openCopyFallback(text, { scope });
        assert.equal(byId(dom, 'modal-title').textContent, 'Copy prompt');
        const body = byId(dom, 'modal-body');
        assert.deepEqual(tags(body), ['div', 'p']);
        const [field, note] = body.children;
        assert.equal(field.className, 'field');
        const [label, area] = field.children;
        assert.equal(label.textContent, 'Select and copy this text');
        assert.equal(label.htmlFor, 'copy-text');
        assert.equal(area.tagName, 'TEXTAREA');
        assert.equal(area.id, 'copy-text');
        assert.equal(area.readOnly, true);
        assert.equal(area.value, text);
        assert.deepEqual([area.selectionStart, area.selectionEnd], [0, text.length]);
        assert.equal(document.activeElement, area);
        assert.equal(note.className, 'small');
        assert.equal(note.textContent, 'Automatic clipboard access requires a secure browser context.');
        assert.throws(() => openCopyFallback(42, { scope }), TypeError);
        // LI-4 (D117): the settings summary names its own dialog; the select-to-copy body is the same.
        openCopyFallback('Seed: 7', { scope, title: 'Copy settings summary' });
        assert.equal(byId(dom, 'modal-title').textContent, 'Copy settings summary');
        assert.equal(byId(dom, 'copy-text').value, 'Seed: 7');
    } finally {
        closeWorkspaceDialog();
        scope.dispose();
        dom.restore();
    }
});

// The concept's Save view dialog (a1330–1346 via simpleInput a1138): an empty name falls back to "Saved view".
test('an input dialog with a fallback applies it for an empty value', async () => {
    const { openInputDialog, closeWorkspaceDialog } = await dialogsModule;
    const dom = fakeDOM();
    const scope = (await coreModule).createScope();
    try {
        const applied = [];
        const open = value => openInputDialog({ scope, title: 'Save this view', label: 'View name', action: 'Save',
            value, maxLength: 100, fallback: 'Saved view', onApply: name => applied.push(name) });
        open('My workspace view');
        assert.equal(byId(dom, 'modal-title').textContent, 'Save this view');
        const body = byId(dom, 'modal-body');
        const [field, actions] = body.children;
        const [label, input] = field.children;
        assert.equal(label.textContent, 'View name');
        assert.equal(input.id, 'action-input');
        assert.equal(input.value, 'My workspace view');
        assert.equal(input.maxLength, 100);
        assert.deepEqual(actions.children.map(node => node.textContent), ['Cancel', 'Save']);
        input.value = '   ';
        actions.children[1].click();
        assert.deepEqual(applied, ['Saved view']);
        open('');
        byId(dom, 'action-input').value = '  Castles  ';
        byId(dom, 'modal-body').children[1].children[1].click();
        assert.deepEqual(applied, ['Saved view', 'Castles']);
        assert.throws(() => openInputDialog({ scope, title: 'T', label: 'L', action: 'A', fallback: '', onApply() {} }), TypeError);
    } finally {
        closeWorkspaceDialog();
        scope.dispose();
        dom.restore();
    }
});

test('dialogs restore focus to the trigger on Escape', async () => {
    const { openRefineDialog, openImportDialog, openCopyFallback, closeWorkspaceDialog } = await dialogsModule;
    const dom = fakeDOM();
    const scope = (await coreModule).createScope();
    try {
        const opens = [
            trigger => openRefineDialog({ scope, fields: [{ key: 'tags' }], values: {}, trigger, onApply() {}, onClear() {} }),
            trigger => openImportDialog({ scope, trigger, onInspect() {}, onRescan() {} }),
            trigger => openCopyFallback('text', { trigger, scope }),
        ];
        for (const open of opens) {
            const trigger = new dom.Element('button');
            dom.body.append(trigger);
            open(trigger);
            const dialog = modal(dom);
            assert.equal(dialog.open, true);
            assert.equal(dialog.getAttribute('aria-labelledby'), 'modal-title');
            const header = dialog.children[0];
            assert.equal(header.className, 'dialog-header');
            assert.deepEqual(tags(header), ['h2', 'button']);
            const close = header.children[1];
            assert.equal(close.id, 'modal-close');
            assert.equal(close.textContent, '×');
            assert.equal(close.getAttribute('aria-label'), 'Close dialog');
            assert.notEqual(document.activeElement, trigger);
            // Escape fires `cancel` on a modal dialog.
            const escape = new Event('cancel', { cancelable: true });
            dialog.dispatchEvent(escape);
            assert.equal(dialog.open, false);
            assert.equal(document.activeElement, trigger);
        }

        // × closes and restores focus too; a removed trigger leaves focus alone.
        const trigger = new dom.Element('button');
        dom.body.append(trigger);
        openImportDialog({ scope, trigger, onInspect() {}, onRescan() {} });
        byId(dom, 'modal-close').click();
        assert.equal(modal(dom).open, false);
        assert.equal(document.activeElement, trigger);
        openImportDialog({ scope, trigger, onInspect() {}, onRescan() {} });
        trigger.remove();
        const close = byId(dom, 'modal-close');
        close.click();
        assert.notEqual(document.activeElement, trigger);

        // Opening over an open dialog swaps its content and keeps the first trigger; dispose removes the shell.
        const first = new dom.Element('button');
        dom.body.append(first);
        openImportDialog({ scope, trigger: first, onInspect() {}, onRescan() {} });
        openCopyFallback('swap', { scope });
        assert.equal(byId(dom, 'modal-title').textContent, 'Copy prompt');
        modal(dom).dispatchEvent(new Event('cancel', { cancelable: true }));
        assert.equal(document.activeElement, first);
        openImportDialog({ scope, trigger: first, onInspect() {}, onRescan() {} });
        closeWorkspaceDialog();
        assert.equal(modal(dom), undefined);
    } finally {
        closeWorkspaceDialog();
        scope.dispose();
        dom.restore();
    }
});

test('an action may open the next dialog without the late close event closing it', async () => {
    const { openImportDialog, openCopyFallback, closeWorkspaceDialog } = await dialogsModule;
    const dom = fakeDOM();
    const scope = (await coreModule).createScope();
    try {
        const first = new dom.Element('button');
        const second = new dom.Element('button');
        dom.body.append(first, second);
        openImportDialog({ scope, trigger: first, onInspect() {},
            onRescan: () => openCopyFallback('next', { scope, trigger: second }) });
        button(byId(dom, 'modal-body'), 'Rescan').click();
        await tick();
        assert.equal(modal(dom).open, true);
        assert.equal(byId(dom, 'modal-title').textContent, 'Copy prompt');
        assert.equal(document.activeElement, byId(dom, 'copy-text'));
        // Its own Escape still restores focus to its own trigger.
        modal(dom).dispatchEvent(new Event('cancel', { cancelable: true }));
        await tick();
        assert.equal(document.activeElement, second);
        // Replacing the shell ignores the old shell's late close event too.
        openImportDialog({ scope, trigger: first, onInspect() {}, onRescan() {} });
        closeWorkspaceDialog();
        openImportDialog({ scope, trigger: second, onInspect() {}, onRescan() {} });
        await tick();
        assert.equal(modal(dom).open, true);
        assert.equal(byId(dom, 'modal-title').textContent, 'Import & inspect');
    } finally {
        closeWorkspaceDialog();
        scope.dispose();
        dom.restore();
    }
});

test('a content dialog shows page nodes and releases them once on close or replacement', async () => {
    const { openContentDialog, openCopyFallback, closeWorkspaceDialog } = await dialogsModule;
    const dom = fakeDOM();
    const scope = (await coreModule).createScope();
    try {
        const trigger = new dom.Element('button');
        dom.body.append(trigger);
        let closed = 0;
        const grid = new dom.Element('div');
        openContentDialog({ scope, title: 'All images for this positive', nodes: [grid], trigger, onClose: () => closed++ });
        assert.equal(byId(dom, 'modal-title').textContent, 'All images for this positive');
        assert.deepEqual(byId(dom, 'modal-body').children, [grid]);
        assert.equal(modal(dom).open, true);
        modal(dom).dispatchEvent(new Event('cancel', { cancelable: true }));
        assert.equal(closed, 1);
        assert.equal(document.activeElement, trigger);
        await tick();
        assert.equal(closed, 1);
        // Another dialog replacing the body releases it too, exactly once.
        openContentDialog({ scope, title: 'Repeat run', nodes: [new dom.Element('p')], trigger, onClose: () => closed++ });
        openCopyFallback('swap', { scope });
        assert.equal(closed, 2);
        closeWorkspaceDialog();
        assert.equal(closed, 2);
        // Disposing the page scope closes it.
        openContentDialog({ scope, title: 'Repeat run', nodes: [], onClose: () => closed++ });
        scope.dispose();
        assert.equal(closed, 3);
        assert.throws(() => openContentDialog({ title: 'x', nodes: [] }), /page scope/);
        const other = (await coreModule).createScope();
        assert.throws(() => openContentDialog({ scope: other, title: '', nodes: [] }), TypeError);
        assert.throws(() => openContentDialog({ scope: other, title: 'x', nodes: [null] }), TypeError);
        assert.throws(() => openContentDialog({ scope: other, title: 'x', nodes: [], onClose: 1 }), TypeError);
        other.dispose();
    } finally {
        closeWorkspaceDialog();
        scope.dispose();
        dom.restore();
    }
});

test('dialogs need the page scope and close with it', async () => {
    const { openRefineDialog, openImportDialog, openCopyFallback, closeWorkspaceDialog } = await dialogsModule;
    const { createScope } = await coreModule;
    const dom = fakeDOM();
    try {
        assert.throws(() => openImportDialog({ onInspect() {}, onRescan() {} }), /page scope/);
        assert.throws(() => openRefineDialog({ fields: [], onApply() {}, onClear() {} }), /page scope/);
        assert.throws(() => openCopyFallback('text'), /page scope/);
        const scope = createScope();
        assert.throws(() => openImportDialog({ scope, onInspect() {} }), TypeError);
        assert.throws(() => openRefineDialog({ scope, fields: [], onApply() {} }), TypeError);
        assert.equal(modal(dom), undefined);

        // Disposing the page scope closes and removes the shell, however often the page opened it.
        const trigger = new dom.Element('button');
        dom.body.append(trigger);
        openImportDialog({ scope, trigger, onInspect() {}, onRescan() {} });
        openCopyFallback('again', { scope });
        scope.dispose();
        assert.equal(modal(dom), undefined);
        assert.equal(document.activeElement, trigger);

        // An old page's scope never closes the next page's dialog.
        const older = createScope();
        const newer = createScope();
        openImportDialog({ scope: older, onInspect() {}, onRescan() {} });
        openCopyFallback('next page', { scope: newer });
        older.dispose();
        assert.equal(modal(dom).open, true);
        newer.dispose();
        assert.equal(modal(dom), undefined);
    } finally {
        closeWorkspaceDialog();
        dom.restore();
    }
});

test('every route change closes the shared workspace dialog', async () => {
    const { readFile } = require('node:fs/promises');
    const path = require('node:path');
    const source = await readFile(path.join(__dirname, '../../web/js/workspace/index.mjs'), 'utf8');
    assert.match(source, /import \{[^}]*\bcloseWorkspaceDialog\b[^}]*\} from '\.\/workspace-dialogs\.mjs';/);
    const render = source.slice(source.indexOf('function render(route) {'));
    const body = render.slice(0, render.indexOf('viewScope = createScope();'));
    assert.match(body, /closeWorkspaceDialog\(\);/, 'render closes the dialog before the next page mounts');
});

// D44: the concept has no indicator, so a filter set in "Refine this view" (or by Browse) was invisible on the page.
test('the Filters button shows the active refinement count', async () => {
    const { mountToolbar } = await toolbarModule;
    const { createScope } = await import('../../web/js/shared/toolkit/core.js');
    const dom = fakeDOM();
    const scope = createScope();
    try {
        const container = new dom.Element('div');
        const toolbar = mountToolbar(container, { scope, categories: [], sorts: [['newest', 'Recently added']],
            activeFilters: 2, onChange() {}, onFilters() {}, onSaveView() {} });
        const filtersButton = container.children[0].children[4];
        // The visible text is the accessible name, so the count is spoken too.
        assert.equal(filtersButton.textContent, 'Filters · 2');
        toolbar.setActiveFilters(1);
        assert.equal(filtersButton.textContent, 'Filters · 1');
        toolbar.setActiveFilters(0);
        assert.equal(filtersButton.textContent, 'Filters');
        for (const bad of [-1, 1.5, '1', null, Number.NaN]) assert.throws(() => toolbar.setActiveFilters(bad), TypeError);
        assert.equal(filtersButton.textContent, 'Filters');
        // An unchanged count leaves the label's text node alone: every listing reload reports the count, and WebKit
        // drops a click whose mousedown hit a text node that was replaced before mouseup.
        let writes = 0;
        const proto = Object.getPrototypeOf(filtersButton);
        const descriptor = Object.getOwnPropertyDescriptor(proto, 'textContent');
        Object.defineProperty(filtersButton, 'textContent', {
            get() { return descriptor.get.call(this); },
            set(text) { writes += 1; descriptor.set.call(this, text); },
            configurable: true,
        });
        toolbar.setActiveFilters(0);
        assert.equal(writes, 0);
        toolbar.setActiveFilters(3);
        assert.deepEqual([writes, filtersButton.textContent], [1, 'Filters · 3']);
        assert.throws(() => mountToolbar(new dom.Element('div'), { scope, categories: [], sorts: [['newest', 'Recently added']],
            activeFilters: -1, onChange() {}, onFilters() {}, onSaveView() {} }), TypeError);
    } finally {
        scope.dispose();
        dom.restore();
    }
});

// #249 System S2 Task 5: the scan review's dialog and backup box (D55) generalised for every record-changing review.
const scanModule = import('../../web/js/workspace/activity-scan.mjs');
const bodyShape = dom => byId(dom, 'modal-body').children.map(node => [node.tagName.toLowerCase(), node.className].filter(Boolean).join('.'));

test('backupNotice is the concept .notice with its copy and one Back up now action', async () => {
    const { backupNotice, SCAN_BACKUP_COPY } = await dialogsModule;
    const dom = fakeDOM();
    try {
        let calls = 0;
        const notice = backupNotice({ copy: SCAN_BACKUP_COPY, onBackup: () => { calls++; } });
        assert.equal(notice.className, 'notice');
        assert.deepEqual(tags(notice), ['div', 'div']);
        assert.deepEqual(tags(notice.children[0]), ['h3', 'p']);
        assert.equal(notice.children[0].children[0].textContent, 'Back up the database first (recommended)');
        assert.equal(notice.children[0].children[1].textContent,
            'Downloads a copy of the prompts database before the scan changes any records.');
        assert.equal(notice.children[1].className, 'row-actions');
        const action = button(notice, 'Back up now');
        action.click();
        assert.equal(calls, 1);
        assert.throws(() => backupNotice({ copy: { heading: '', text: 'x' }, onBackup() {} }), TypeError);
        assert.throws(() => backupNotice({ copy: SCAN_BACKUP_COPY }), TypeError);
    } finally {
        dom.restore();
    }
});

test('openReviewDialog lays out heading, grid, options, sentence, backup box and actions', async () => {
    const { openReviewDialog, closeWorkspaceDialog } = await dialogsModule;
    const { createScope } = await coreModule;
    const dom = fakeDOM();
    const scope = createScope();
    try {
        const trigger = new dom.Element('button');
        dom.body.append(trigger);
        const option = new dom.Element('div');
        option.className = 'setting-row';
        const started = [];
        const review = openReviewDialog({ scope, title: 'Review maintenance plan', heading: 'Missing-file links',
            grid: [['Scope', '2 stale image links'], ['Media on disk', 'No source files removed']], options: [option],
            sentence: 'Cancelling leaves all data untouched.', backup: { copy: { heading: 'Back up first', text: 'A copy.' }, onBackup() {} },
            cancelLabel: 'Cancel', primary: { label: 'Apply reviewed operation' }, onStart: () => started.push('start'), trigger });
        assert.equal(byId(dom, 'modal-title').textContent, 'Review maintenance plan');
        assert.deepEqual(bodyShape(dom), ['h3', 'dl.meta-grid', 'div.setting-row', 'p.small', 'div.notice', 'div.dialog-actions']);
        const grid = byId(dom, 'modal-body').children[1];
        assert.deepEqual(grid.children.map(entry => [tags(entry), entry.children[0].textContent, entry.children[1].textContent]), [
            [['dt', 'dd'], 'Scope', '2 stale image links'], [['dt', 'dd'], 'Media on disk', 'No source files removed']]);
        review.setValue('Scope', '3 stale image links');
        assert.equal(grid.children[0].children[1].textContent, '3 stale image links');
        // #355: a row's hover title (the LoRA review's settings file); an unknown term changes nothing.
        review.setTitle('Media on disk', '~/settings.json');
        review.setTitle('Absent', 'x');
        review.setValue('Absent', 'x');
        assert.equal(grid.children[1].children[1].title, '~/settings.json');
        const actions = byId(dom, 'modal-body').children.at(-1);
        assert.deepEqual(actions.children.map(node => [node.textContent, node.className]),
            [['Cancel', ''], ['Apply reviewed operation', 'ca-primary']]);
        actions.children[1].click();
        assert.deepEqual(started, ['start']);
        assert.equal(modal(dom).open, false);
        assert.equal(document.activeElement, trigger);

        // A danger primary (the concept's Restore) has no sentence, heading, options or backup box when none is given.
        openReviewDialog({ scope, title: 'Restore preview', grid: [['Integrity', 'Verified']], backup: null,
            cancelLabel: 'Cancel', primary: { label: 'Restore', danger: true }, onStart() {} });
        assert.deepEqual(bodyShape(dom), ['dl.meta-grid', 'div.dialog-actions']);
        assert.equal(button(byId(dom, 'modal-body'), 'Restore').className, 'danger');
        button(byId(dom, 'modal-body'), 'Cancel').click();
        assert.equal(modal(dom).open, false);
        assert.throws(() => openReviewDialog({ scope, title: 'x', grid: [['a']], cancelLabel: 'Cancel', primary: { label: 'Go' },
            onStart() {} }), TypeError);
        assert.throws(() => openReviewDialog({ scope, title: 'x', grid: [], cancelLabel: 'Cancel', primary: {}, onStart() {} }), TypeError);
    } finally {
        scope.dispose();
        closeWorkspaceDialog();
        dom.restore();
    }
});

test('review dialog backup box keeps the dialog open and toasts the outcome', async () => {
    const { openReviewDialog, closeWorkspaceDialog, SCAN_BACKUP_COPY } = await dialogsModule;
    const { downloadDatabaseBackup, BACKUP_DONE, BACKUP_FAILED } = await scanModule;
    const { createScope } = await coreModule;
    const dom = fakeDOM();
    const scope = createScope();
    const created = URL.createObjectURL;
    const revoked = URL.revokeObjectURL;
    URL.createObjectURL = () => 'blob:backup';
    URL.revokeObjectURL = () => {};
    try {
        const toasts = [];
        let answer;
        const fetchUrl = () => new Promise(resolve => { answer = resolve; });
        openReviewDialog({ scope, title: 'Review metadata scan', grid: [['Sources', '2 output folders']],
            backup: { copy: SCAN_BACKUP_COPY, onBackup: () => downloadDatabaseBackup({ fetchUrl, notify: text => toasts.push(text) }) },
            cancelLabel: 'Cancel', primary: { label: 'Start scan' }, onStart() { throw new Error('not started'); } });
        const action = button(byId(dom, 'modal-body'), 'Back up now');
        action.click();
        assert.equal(action.textContent, 'Backing up…');
        assert.equal(action.getAttribute('aria-disabled'), 'true');
        // A second press while the copy downloads is ignored.
        action.click();
        answer({ ok: true, blob: async () => new Blob(['db']), headers: { get: name => (name === 'Content-Disposition' ? 'attachment; filename="prompts_backup_1.db"' : null) } });
        await tick();
        assert.deepEqual(toasts, [BACKUP_DONE]);
        assert.equal(modal(dom).open, true);
        assert.equal(action.textContent, 'Back up now');
        assert.equal(action.getAttribute('aria-disabled'), null);
        action.click();
        answer({ ok: false, status: 500 });
        await tick();
        assert.deepEqual(toasts, [BACKUP_DONE, BACKUP_FAILED]);
        assert.equal(modal(dom).open, true);
    } finally {
        URL.createObjectURL = created;
        URL.revokeObjectURL = revoked;
        scope.dispose();
        closeWorkspaceDialog();
        dom.restore();
    }
});

test('the scan review is a thin openReviewDialog caller with its scope switch', async () => {
    const { openScanReviewDialog, closeWorkspaceDialog } = await dialogsModule;
    const { createScope } = await coreModule;
    const dom = fakeDOM();
    const scope = createScope();
    try {
        const starts = [];
        openScanReviewDialog({ scope, sources: 2, onBackup() {}, onStart: value => starts.push(value) });
        assert.equal(byId(dom, 'modal-title').textContent, 'Review metadata scan');
        assert.deepEqual(bodyShape(dom), ['dl.meta-grid', 'div.setting-row', 'p.small', 'div.notice', 'div.dialog-actions']);
        const grid = byId(dom, 'modal-body').children[0];
        assert.deepEqual(grid.children.map(entry => entry.textContent), ['Sources2 output folders', 'ScopeNew and unresolved files']);
        const allFiles = byId(dom, 'scan-all-files');
        allFiles.checked = true;
        allFiles.dispatchEvent(new Event('change'));
        assert.equal(grid.children[1].children[1].textContent, 'All files');
        button(byId(dom, 'modal-body'), 'Start scan').click();
        assert.deepEqual(starts, [{ scope: 'all' }]);
    } finally {
        scope.dispose();
        closeWorkspaceDialog();
        dom.restore();
    }
});

test('a review dialog reports one dismissal for Cancel, ×, Escape, a replacing dialog and the page leaving, never after its primary (S9 M1)', async () => {
    const { openReviewDialog, openContentDialog, closeWorkspaceDialog } = await dialogsModule;
    const { createScope } = await coreModule;
    const dom = fakeDOM();
    const scope = createScope();
    const dismissed = [];
    const open = (name, primary = 'Restore') => openReviewDialog({ scope, title: 'Restore preview', grid: [['Integrity', 'Verified']],
        cancelLabel: 'Cancel', primary: { label: primary, danger: true }, onStart() {}, onDismiss: () => dismissed.push(name) });
    try {
        open('cancel');
        button(byId(dom, 'modal-body'), 'Cancel').click();
        open('close');
        byId(dom, 'modal-close').click();
        open('escape');
        modal(dom).dispatchEvent(new Event('cancel', { cancelable: true }));
        open('replaced');
        openContentDialog({ scope, title: 'Other', nodes: [] });
        closeWorkspaceDialog();
        open('started');
        button(byId(dom, 'modal-body'), 'Restore').click();
        await tick();
        open('left');
        scope.dispose();
        await tick();
        assert.deepEqual(dismissed, ['cancel', 'close', 'escape', 'replaced', 'left']);
        assert.throws(() => openReviewDialog({ scope: createScope(), title: 'x', grid: [['a', 'b']], cancelLabel: 'Cancel',
            primary: { label: 'Go' }, onStart() {}, onDismiss: 'no' }), TypeError);
    } finally {
        scope.dispose();
        closeWorkspaceDialog();
        dom.restore();
    }
});
