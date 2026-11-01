// System › Auto-tagging "Skipped tags" (#431, D202): the add input, the chips, the client checks and the preview line.
const test = require('node:test');
const assert = require('node:assert/strict');

const skippedModule = import('../../web/js/workspace/system-skipped-tags.mjs');
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

const tick = () => new Promise(resolve => setTimeout(resolve, 5));

async function mounted({ saved = ['blurry'], answer, host = true } = {}) {
    const { skippedTagsBlock } = await skippedModule;
    const { createSystemDrafts } = await stateModule;
    const { createScope } = await coreModule;
    const dom = fakeDOM();
    const drafts = createSystemDrafts({
        readBrowser: () => ({}), readRuntime: () => ({}),
        readHost: async () => ({ values: host ? { autotag_skipped_tags: saved } : {}, revision: 'sha256:one' }),
        writeBrowser: async () => ({}), writeHost: async () => ({ success: true, persisted: true }), writeRuntime: async () => ({}),
    });
    const scope = createScope();
    const posts = [];
    const api = {
        post: (url, body, options) => {
            posts.push({ url, body, signal: options.signal });
            if (answer) return answer(body, options);
            return Promise.resolve({ success: true, count: body.entries.length, sample: body.entries, sample_limit: 12,
                per_entry: body.entries.map((entry, index) => ({ entry, count: index })), sources: { library: 3, wd14: ['WD14 SwinV2'] } });
        },
    };
    const block = skippedTagsBlock({ api, scope, drafts, delay: 1 });
    scope.cleanup(drafts.subscribe(block.repaint));
    block.repaint();
    await drafts.reloadHost();
    const byId = id => dom.find(block.node, node => node.id === id);
    const chips = () => dom.find(block.node, node => node.className === 'tags skip-chips');
    const preview = () => dom.find(block.node, node => node.className === 'small skip-preview');
    const add = async text => { byId('tag-skipped-input').value = text; await byId('tag-skipped-add').dispatch('click'); };
    return { dom, drafts, scope, block, byId, chips, preview, posts, add };
}

test('words split like the server: NFC, lower case, space, _ and - only', async () => {
    const { entryWords } = await skippedModule;
    assert.deepEqual(entryWords('Half-closed_eyes'), ['half', 'closed', 'eyes']);
    assert.deepEqual(entryWords('saber_(fate)'), ['saber', '(fate)']);
    assert.deepEqual(entryWords(' _ - '), []);
    assert.deepEqual(entryWords('CAFÉ'), ['café']);
});

test('the client checks say why an entry is not added', async () => {
    const { entryProblem, SKIPPED_COPY } = await skippedModule;
    assert.equal(entryProblem('  sky ', ['blurry']), '');
    assert.equal(entryProblem('   ', []), SKIPPED_COPY.empty);
    assert.equal(entryProblem('_--', []), 'Enter a word.');
    assert.equal(entryProblem('a, b', []), 'No commas or line breaks.');
    assert.equal(entryProblem('a\nb', []), 'No commas or line breaks.');
    assert.equal(entryProblem('x'.repeat(51), []), 'At most 50 characters.');
    assert.equal(entryProblem('🏜'.repeat(50), []), '');
    assert.equal(entryProblem('Blurry-Background', ['blurry background']), 'Already in the list.');
    assert.equal(entryProblem('sky', Array.from({ length: 200 }, (_, i) => `w${i}`)), 'The list is full (200).');
});

test('the preview line states the count, the sample and the sources', async () => {
    const { previewText, chipCount } = await skippedModule;
    assert.deepEqual(previewText({ count: 15, sample: ['blurry', 'signature'], sources: { wd14: ['WD14 SwinV2'] } }), {
        lead: 'Hides 15 tags from suggestions', rest: ': blurry, signature and 13 more. Tags already on images are not changed.' });
    assert.deepEqual(previewText({ count: 1, sample: ['blurry'], sources: { wd14: [] } }), {
        lead: 'Hides 1 tag from suggestions',
        rest: ': blurry. Checked against library tags only; no WD14 label list is downloaded. Tags already on images are not changed.' });
    assert.deepEqual(previewText({ count: 0, sample: [], sources: { wd14: ['WD14 ViT'] } }), {
        lead: 'Hides 0 tags from suggestions', rest: '. Tags already on images are not changed.' });
    assert.deepEqual(previewText(null).lead, 'Hides 0 tags from suggestions');
    assert.deepEqual(chipCount(3), { title: 'Hides 3 tags', text: '3' });
    assert.deepEqual(chipCount(1), { title: 'Hides 1 tag', text: '1' });
    assert.deepEqual(chipCount(0), { title: 'Hides no known tag', text: 'none' });
    assert.equal(chipCount(undefined), null);
});

test('the block shows the saved list, previews it and carries its setting keys', async () => {
    const view = await mounted({ saved: ['blurry', 'signature'] });
    try {
        assert.equal(view.block.node.dataset.setting, 'tag-skipped');
        assert.equal(view.block.node.dataset.key, 'autotag_skipped_tags');
        assert.equal(view.preview().textContent, 'Checking…');
        await tick();
        assert.deepEqual(view.posts.map(post => [post.url, post.body]),
            [['/prompt_manager/autotag/skipped-tags/preview', { entries: ['blurry', 'signature'] }]]);
        assert.equal(view.preview().textContent, 'Hides 2 tags from suggestions: blurry, signature. Tags already on images are not changed.');
        const chips = view.chips().children;
        assert.deepEqual(chips.map(chip => [chip.className, chip.title, chip.textContent]), [
            ['chip tag-suggestion none', 'Hides no known tag', 'blurrynone×'],
            ['chip tag-suggestion', 'Hides 1 tag', 'signature1×']]);
        assert.equal(chips[1].children[2].getAttribute('aria-label'), 'Remove signature');
    } finally { view.scope.dispose(); view.dom.restore(); }
});

test('add and remove draft the list; a list set back is clean; Enter adds', async () => {
    const view = await mounted();
    try {
        await view.add('  closed eyes ');
        assert.deepEqual(view.drafts.get('host', 'autotag_skipped_tags'), ['blurry', 'closed eyes']);
        assert.equal(view.byId('tag-skipped-input').value, '');
        assert.equal(view.drafts.dirty(), true);
        view.byId('tag-skipped-input').value = 'sky';
        let prevented = false;
        await view.byId('tag-skipped-input').dispatch('keydown', { key: 'Enter', preventDefault: () => { prevented = true; } });
        assert.equal(prevented, true);
        await view.byId('tag-skipped-input').dispatch('keydown', { key: 'a' });
        assert.deepEqual(view.drafts.get('host', 'autotag_skipped_tags'), ['blurry', 'closed eyes', 'sky']);
        // Remove the two added chips: the list equals the saved one and nothing is unsaved (sameDraft).
        const removeButton = index => view.chips().children[index].children.at(-1);
        removeButton(2).focus();
        await removeButton(2).dispatch('click');
        assert.equal(global.document.activeElement, removeButton(1), 'focus moves to the neighbour');
        await removeButton(1).dispatch('click');
        assert.deepEqual(view.drafts.get('host', 'autotag_skipped_tags'), ['blurry']);
        assert.equal(view.drafts.dirty(), false);
        await tick();
        // Only the last list of a quick series is asked for.
        assert.deepEqual(view.posts.map(post => post.body.entries), [['blurry']]);
    } finally { view.scope.dispose(); view.dom.restore(); }
});

test('a refused entry is not added and the line under the input says why', async () => {
    const view = await mounted();
    try {
        const problem = () => view.byId('tag-skipped-problem').textContent;
        await view.add('Blurry');
        assert.equal(problem(), 'Already in the list.');
        assert.equal(view.byId('tag-skipped-input').getAttribute('aria-invalid'), 'true');
        assert.equal(view.byId('tag-skipped-input').value, 'Blurry');
        await view.byId('tag-skipped-input').dispatch('input');
        assert.equal(problem(), '');
        assert.equal(view.byId('tag-skipped-input').getAttribute('aria-invalid'), null);
        await view.add('a,b');
        assert.equal(problem(), 'No commas or line breaks.');
        await view.add(' ');
        assert.equal(problem(), 'Enter a word.');
        assert.deepEqual(view.drafts.get('host', 'autotag_skipped_tags'), ['blurry']);
    } finally { view.scope.dispose(); view.dom.restore(); }
});

test('a full list disables Add and says so', async () => {
    const view = await mounted({ saved: Array.from({ length: 200 }, (_, i) => `w${i}`) });
    try {
        assert.equal(view.byId('tag-skipped-add').disabled, true);
        assert.equal(view.byId('tag-skipped-problem').textContent, 'The list is full (200).');
        await view.chips().children[0].children.at(-1).dispatch('click');
        assert.equal(view.byId('tag-skipped-add').disabled, false);
        assert.equal(view.byId('tag-skipped-problem').textContent, '');
    } finally { view.scope.dispose(); view.dom.restore(); }
});

test('an empty list says no tags are skipped without a request', async () => {
    const view = await mounted({ saved: [] });
    try {
        await tick();
        assert.equal(view.preview().textContent, 'No tags are skipped.');
        assert.deepEqual(view.posts, []);
    } finally { view.scope.dispose(); view.dom.restore(); }
});

test('a failed preview says so; a newer list aborts the older request', async () => {
    let calls = 0;
    const view = await mounted({ answer: (body, options) => {
        calls += 1;
        if (calls === 1) return new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(new Error('aborted'))));
        return Promise.reject(Object.assign(new Error('busy'), { code: 'busy' }));
    } });
    try {
        await tick();
        assert.equal(view.posts.length, 1);
        await view.add('sky');
        assert.equal(view.posts[0].signal.aborted, true);
        await tick();
        assert.equal(view.preview().textContent, 'The preview could not be read.');
        // The list stays editable.
        assert.equal(view.byId('tag-skipped-input').disabled, false);
    } finally { view.scope.dispose(); view.dom.restore(); }
});

test('before the settings are read the controls are disabled', async () => {
    const view = await mounted({ host: false });
    try {
        assert.equal(view.byId('tag-skipped-input').disabled, true);
        assert.equal(view.byId('tag-skipped-add').disabled, true);
        await view.add('sky');
        assert.equal(view.drafts.get('host', 'autotag_skipped_tags'), undefined);
    } finally { view.scope.dispose(); view.dom.restore(); }
});

test('leaving the section aborts a request in flight and the pending timer', async () => {
    const view = await mounted({ answer: (_body, options) => new Promise((_r, reject) => options.signal.addEventListener('abort', () => reject(new Error('x')))) });
    await tick();
    assert.equal(view.posts.length, 1);
    view.scope.dispose();
    assert.equal(view.posts[0].signal.aborted, true);
    view.dom.restore();
});
