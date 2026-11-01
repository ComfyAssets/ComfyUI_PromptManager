// System › Auto-tagging "Saved instructions" (#431, D203): Built-in read-only, New, Duplicate, Delete (a deleted default
// resets to Built-in), the caps, the editor drafts and the per-engine default selects.
const test = require('node:test');
const assert = require('node:assert/strict');

const instructionsModule = import('../../web/js/workspace/system-instructions.mjs');
const stateModule = import('../../web/js/workspace/system-state.mjs');
const coreModule = import('../../web/js/shared/toolkit/core.js');

/** A minimal DOM: elements with children, attributes, listeners (aborted by their signal) and focus. */
function fakeDOM() {
    const previous = global.document;
    class Element {
        constructor(tag) {
            this.tagName = tag.toUpperCase();
            this.children = [];
            this.attributes = {};
            this.listeners = [];
            this.dataset = {};
            this.className = '';
            this.id = '';
            this.value = '';
            this.title = '';
            this.disabled = false;
            this._text = '';
        }
        get localName() { return this.tagName.toLowerCase(); }
        get textContent() { return this._text + this.children.map(child => (typeof child === 'string' ? child : child.textContent)).join(''); }
        set textContent(text) { this.children = []; this._text = String(text); }
        append(...nodes) { for (const node of nodes) { if (typeof node !== 'string') node.parentNode = this; this.children.push(node); } }
        replaceChildren(...nodes) { this.children = []; this._text = ''; this.append(...nodes); }
        querySelectorAll(selector) {
            assert.equal(selector, 'button');
            const found = [];
            const walk = node => { for (const child of node.children) if (typeof child !== 'string') { if (child.tagName === 'BUTTON') found.push(child); walk(child); } };
            walk(this);
            return found;
        }
        focus() { global.document.activeElement = this; }
        setAttribute(name, value) { this.attributes[name] = String(value); }
        getAttribute(name) { return Object.hasOwn(this.attributes, name) ? this.attributes[name] : null; }
        removeAttribute(name) { delete this.attributes[name]; }
        addEventListener(type, callback, options = {}) {
            if (options.signal?.aborted) return;
            const entry = { type, callback };
            this.listeners.push(entry);
            options.signal?.addEventListener('abort', () => { this.listeners = this.listeners.filter(item => item !== entry); });
        }
        dispatch(type, event = {}) {
            return Promise.all(this.listeners.filter(item => item.type === type).map(({ callback }) => callback({ type, target: this, ...event })));
        }
    }
    global.document = { createElement: tag => new Element(tag), activeElement: null };
    const walk = (node, found = []) => { found.push(node); node.children.forEach(child => typeof child !== 'string' && walk(child, found)); return found; };
    return { find: (root, predicate) => walk(root).find(predicate), restore() { global.document = previous; } };
}


const PHOTO = { id: '0123456789ab', name: 'Custom template', text: 'Photo tags.' };
const ANIME = { id: '0123456789ac', name: 'Anime', text: 'Anime tags.' };
const HOST = { autotag_builtin_instructions: 'Built-in text.', autotag_instructions: [PHOTO, ANIME],
    autotag_default_instructions: { gguf: PHOTO.id, hf: PHOTO.id } };

async function mounted({ host = HOST } = {}) {
    const { savedInstructionsBlock } = await instructionsModule;
    const { createSystemDrafts } = await stateModule;
    const { createScope } = await coreModule;
    const dom = fakeDOM();
    const drafts = createSystemDrafts({
        readBrowser: () => ({}), readRuntime: () => ({}), readHost: async () => ({ values: host, revision: 'sha256:one' }),
        writeBrowser: async () => ({}), writeHost: async () => ({ success: true, persisted: true }), writeRuntime: async () => ({}),
    });
    const scope = createScope();
    let counter = 0;
    const random = bytes => bytes.map(() => (counter += 1));
    const block = savedInstructionsBlock({ scope, drafts, random });
    const root = global.document.createElement('div');
    root.append(...block.nodes);
    scope.cleanup(drafts.subscribe(block.repaint));
    block.repaint();
    await drafts.reloadHost();
    const byId = id => dom.find(root, node => node.id === id);
    const options = () => byId('instruction-list').children.map(option => [option.children[0].textContent,
        option.children[1]?.children.map(badge => badge.textContent) ?? [], option.getAttribute('aria-selected')]);
    const click = id => byId(id).dispatch('click');
    const pick = name => byId('instruction-list').children.find(option => option.children[0].textContent === name).dispatch('click');
    return { dom, drafts, scope, block, byId, options, click, pick, root };
}

test('helpers: Built-in first, defaults, free names, ids and the server sentences', async () => {
    const { allEntries, defaultEntry, freeName, newId, listProblem, entryNote } = await instructionsModule;
    assert.deepEqual(allEntries(HOST).map(entry => entry.name), ['Built-in', 'Custom template', 'Anime']);
    assert.equal(allEntries(HOST)[0].text, 'Built-in text.');
    assert.equal(defaultEntry(HOST, 'hf').text, 'Photo tags.');
    assert.equal(defaultEntry({ ...HOST, autotag_default_instructions: { gguf: 'gone', hf: 'builtin' } }, 'gguf').name, 'Built-in');
    assert.equal(freeName('New instructions', allEntries(HOST)), 'New instructions');
    assert.equal(freeName('anime', allEntries(HOST)), 'anime (2)');
    assert.equal(freeName('Anime', [...allEntries(HOST), { name: 'Anime (2)' }]), 'Anime (3)');
    let calls = 0;
    const id = newId([{ id: '000000000000' }], bytes => { calls += 1; return bytes.fill(calls === 1 ? 0 : 171); });
    assert.equal(id, 'abababababab');
    assert.match(newId([]), /^[0-9a-f]{12}$/);
    const list = entries => listProblem({ autotag_instructions: entries });
    assert.equal(list([PHOTO]), null);
    assert.equal(list([{ ...PHOTO, name: ' ' }]), 'Saved instructions: entry 1 needs a name.');
    assert.equal(list([PHOTO, { ...ANIME, name: 'x'.repeat(61) }]), 'Saved instructions: entry 2 name must be at most 60 characters.');
    assert.equal(list([{ ...PHOTO, name: 'a\nb' }]), 'Saved instructions: entry 1 name must not contain a line break.');
    assert.equal(list([{ ...PHOTO, name: 'built-in' }]), 'Saved instructions: the name “Built-in” is reserved.');
    assert.equal(list([PHOTO, { ...ANIME, name: ' custom TEMPLATE ' }]), 'Saved instructions: entry 2 name “custom TEMPLATE” is already used.');
    assert.equal(list([{ ...PHOTO, text: ' ' }]), 'Saved instructions: entry 1 text must be non-empty text.');
    assert.equal(list([{ ...PHOTO, text: 'x'.repeat(4001) }]), 'Saved instructions: entry 1 text must be at most 4000 characters.');
    const long = { ...PHOTO, text: 'x'.repeat(4001) };
    assert.equal(entryNote(long, [long]), 'The text must be at most 4,000 characters.');
});

test('the list shows Built-in read-only first, the saved entries and their default chips', async () => {
    const view = await mounted();
    try {
        assert.deepEqual(view.options(), [['Built-in', ['Read-only'], 'true'],
            ['Custom template', ['Default for GGUF', 'Default for HuggingFace'], 'false'], ['Anime', [], 'false']]);
        assert.equal(view.byId('instructions-count').textContent, '2 of 20 saved · Built‑in is not counted.');
        assert.equal(view.byId('instruction-name').value, 'Built-in');
        assert.equal(view.byId('instruction-name').readOnly, true);
        assert.equal(view.byId('instruction-text').value, 'Built-in text.');
        assert.equal(view.byId('instruction-text').readOnly, true);
        assert.equal(view.byId('instruction-note').textContent, 'Built-in instructions can’t be changed. Duplicate them to make your own.');
        assert.equal(view.byId('instruction-delete').disabled, true);
        assert.equal(view.byId('instruction-list').getAttribute('role'), 'listbox');
        assert.equal(view.block.nodes[0].dataset.key, 'autotag_instructions');
        assert.deepEqual(view.byId('instruction-default-gguf').children.map(option => option.textContent), ['Built-in', 'Custom template', 'Anime']);
        assert.equal(view.byId('instruction-default-gguf').value, PHOTO.id);
        assert.equal(view.byId('instruction-default-hf').dataset.key, 'autotag_default_instructions');
    } finally { view.scope.dispose(); view.dom.restore(); }
});

test('editing a saved entry drafts the list; its counts and note follow', async () => {
    const view = await mounted();
    try {
        await view.pick('Anime');
        assert.equal(view.byId('instruction-name').readOnly, false);
        assert.equal(view.byId('instruction-delete').disabled, false);
        view.byId('instruction-name').value = 'Anime tags';
        await view.byId('instruction-name').dispatch('input');
        view.byId('instruction-text').value = 'Tags, anime.';
        await view.byId('instruction-text').dispatch('input');
        assert.deepEqual(view.drafts.get('host', 'autotag_instructions'), [PHOTO, { ...ANIME, name: 'Anime tags', text: 'Tags, anime.' }]);
        assert.equal(view.byId('instruction-name').parentNode.children[0].children[1].textContent, '10 of 60');
        assert.equal(view.byId('instruction-text').parentNode.children[0].children[1].textContent, '12 of 4,000 characters');
        view.byId('instruction-name').value = 'custom template';
        await view.byId('instruction-name').dispatch('input');
        assert.equal(view.byId('instruction-note').textContent, 'The name “custom template” is already used.');
        // Built-in is never edited.
        await view.pick('Built-in');
        view.byId('instruction-text').value = 'changed';
        await view.byId('instruction-text').dispatch('input');
        assert.equal(view.drafts.get('host', 'autotag_instructions')[0], PHOTO);
    } finally { view.scope.dispose(); view.dom.restore(); }
});

test('New and Duplicate append an entry with a fresh id and a free name; the caps disable them', async () => {
    const view = await mounted();
    try {
        await view.click('instruction-new');
        const added = view.drafts.get('host', 'autotag_instructions').at(-1);
        assert.match(added.id, /^[0-9a-f]{12}$/);
        assert.deepEqual([added.name, added.text], ['New instructions', '']);
        assert.equal(view.options().at(-1)[2], 'true');
        assert.equal(view.byId('instruction-note').textContent, 'The text must not be empty.');
        await view.pick('Built-in');
        await view.click('instruction-duplicate');
        assert.deepEqual(view.drafts.get('host', 'autotag_instructions').at(-1).name, 'Built-in copy');
        assert.equal(view.drafts.get('host', 'autotag_instructions').at(-1).text, 'Built-in text.');
        const full = await mounted({ host: { ...HOST, autotag_instructions: Array.from({ length: 20 },
            (_, i) => ({ id: i.toString(16).padStart(12, '0'), name: `n${i}`, text: 't' })) } });
        try {
            assert.equal(full.byId('instruction-new').disabled, true);
            assert.equal(full.byId('instruction-duplicate').disabled, true);
            assert.equal(full.byId('instruction-cap').hidden, false);
            await full.click('instruction-new');
            assert.equal(full.drafts.get('host', 'autotag_instructions').length, 20);
        } finally { full.scope.dispose(); }
    } finally { view.scope.dispose(); view.dom.restore(); }
});

test('deleting a default resets that engine to Built-in and says so; a select change clears the note', async () => {
    const view = await mounted();
    try {
        await view.pick('Custom template');
        await view.click('instruction-delete');
        assert.deepEqual(view.drafts.get('host', 'autotag_instructions'), [ANIME]);
        assert.deepEqual(view.drafts.get('host', 'autotag_default_instructions'), { gguf: 'builtin', hf: 'builtin' });
        assert.equal(view.byId('instruction-reset').textContent, 'GGUF and HuggingFace now use Built-in.');
        assert.equal(view.options()[1][2], 'true', 'the next entry is selected');
        view.byId('instruction-default-hf').value = ANIME.id;
        await view.byId('instruction-default-hf').dispatch('change');
        assert.deepEqual(view.drafts.get('host', 'autotag_default_instructions'), { gguf: 'builtin', hf: ANIME.id });
        assert.equal(view.byId('instruction-reset').textContent, '');
        await view.click('instruction-delete');
        assert.equal(view.byId('instruction-reset').textContent, 'HuggingFace now uses Built-in.');
        assert.deepEqual(view.options().map(([name]) => name), ['Built-in']);
    } finally { view.scope.dispose(); view.dom.restore(); }
});

test('arrow keys move the selection; before the read everything is disabled', async () => {
    const view = await mounted();
    try {
        const list = view.byId('instruction-list');
        await list.dispatch('keydown', { key: 'ArrowDown' });
        assert.equal(view.byId('instruction-name').value, 'Custom template');
        await list.dispatch('keydown', { key: 'End' });
        assert.equal(view.byId('instruction-name').value, 'Anime');
        await list.dispatch('keydown', { key: 'ArrowDown' });
        assert.equal(view.byId('instruction-name').value, 'Anime');
        await list.dispatch('keydown', { key: 'Home' });
        assert.equal(view.byId('instruction-name').value, 'Built-in');
        await list.dispatch('keydown', { key: 'a' });
        assert.equal(list.getAttribute('aria-activedescendant'), 'instruction-option-builtin');
    } finally { view.scope.dispose(); view.dom.restore(); }
    const unread = await mounted({ host: {} });
    try {
        assert.equal(unread.byId('instruction-new').disabled, true);
        assert.equal(unread.byId('instruction-default-gguf').disabled, true);
    } finally { unread.scope.dispose(); unread.dom.restore(); }
});
