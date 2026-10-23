// System controls (#249 System S2 Task 5): the concept's `setting()` (app.js 624–634, its toggles hydrated into Toolkit
// switches by `hydrateSwitches` 708–717), `operation()` (645–654) and `sectionNav()` (306–308) as DOM builders whose
// listeners live on the caller's scope.
const test = require('node:test');
const assert = require('node:assert/strict');

const controlsModule = import('../../web/js/workspace/system-controls.mjs');
const coreModule = import('../../web/js/shared/toolkit/core.js');

/** A minimal DOM whose elements count their live listeners (a listener's signal aborts it, as in a browser). */
function fakeDOM() {
    const previous = global.document;
    const elements = [];
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
            this._text = '';
            elements.push(this);
        }
        get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
        set textContent(text) { this.children = []; this._text = String(text); }
        append(...nodes) {
            for (const node of nodes) {
                const child = typeof node === 'string' ? Object.assign(new Element('#text'), { _text: node }) : node;
                child.parentNode = this;
                this.children.push(child);
            }
        }
        replaceChildren(...nodes) { this.children = []; this._text = ''; this.append(...nodes); }
        setAttribute(name, value) { this.attributes[name] = String(value); }
        getAttribute(name) { return Object.hasOwn(this.attributes, name) ? this.attributes[name] : null; }
        removeAttribute(name) { delete this.attributes[name]; }
        addEventListener(type, callback, options = {}) {
            if (options.signal?.aborted) return;
            const entry = { type, callback };
            this.listeners.push(entry);
            options.signal?.addEventListener('abort', () => { this.listeners = this.listeners.filter(item => item !== entry); });
        }
        removeEventListener(type, callback) { this.listeners = this.listeners.filter(item => item.type !== type || item.callback !== callback); }
        dispatch(type) { for (const { callback } of this.listeners.filter(item => item.type === type)) callback({ type, target: this }); }
        click() { this.dispatch('click'); }
    }
    global.document = {
        createElement: tag => new Element(tag),
        createTextNode: text => Object.assign(new Element('#text'), { _text: String(text) }),
    };
    const walk = (node, found = []) => { found.push(node); node.children.forEach(child => walk(child, found)); return found; };
    return {
        listenerCount: () => elements.reduce((sum, element) => sum + element.listeners.length, 0),
        find: (root, predicate) => walk(root).find(predicate),
        tags: node => node.children.map(child => child.tagName.toLowerCase()),
        restore() { global.document = previous; },
    };
}

test('settingRow is the concept row: div > label[for] + p, then the control', async () => {
    const { settingRow } = await controlsModule;
    const { createScope } = await coreModule;
    const dom = fakeDOM();
    const scope = createScope();
    try {
        const changes = [];
        const row = settingRow({ id: 'timeout', label: 'Node search auto-hide (seconds)',
            description: 'Node search behavior only. Use 0 to keep results visible.',
            control: { type: 'number', value: 30, min: 0, max: 300 }, scope, onChange: value => changes.push(value) });
        assert.equal(row.className, 'setting-row');
        assert.deepEqual(dom.tags(row), ['div', 'input']);
        const [text, input] = row.children;
        assert.deepEqual(dom.tags(text), ['label', 'p']);
        assert.equal(text.children[0].textContent, 'Node search auto-hide (seconds)');
        assert.equal(text.children[0].htmlFor, 'timeout');
        assert.equal(text.children[1].textContent, 'Node search behavior only. Use 0 to keep results visible.');
        assert.deepEqual([input.id, input.type, input.value, input.min, input.max], ['timeout', 'number', '30', '0', '300']);
        assert.equal(input.dataset.setting, 'timeout');
        input.value = '45';
        input.dispatch('change');
        assert.deepEqual(changes, [45]);

        const select = settingRow({ id: 'open-mode', label: 'Open from ComfyUI', description: 'Keep the node pack’s popup / new-tab preference.',
            control: { type: 'select', options: [['newtab', 'New tab'], ['popup', 'Popup window']], value: 'popup' }, scope,
            onChange: value => changes.push(value) }).children[1];
        assert.equal(select.tagName, 'SELECT');
        assert.deepEqual(select.children.map(option => [option.value, option.textContent]), [['newtab', 'New tab'], ['popup', 'Popup window']]);
        assert.equal(select.value, 'popup');
        select.value = 'newtab';
        select.dispatch('change');
        assert.deepEqual(changes, [45, 'newtab']);

        // The concept's range is 1–16 unless the row says otherwise (D113: the CPU count).
        const range = settingRow({ id: 'workers', label: 'Worker threads', description: 'Balance background scans.',
            control: { type: 'range', value: 4 } }).children[1];
        assert.deepEqual([range.type, range.min, range.max, range.value], ['range', '1', '16', '4']);
        const password = settingRow({ id: 'civitai-key', label: 'CivitAI API key', description: 'Credentials stay on the server.',
            control: { type: 'password', value: '' } }).children[1];
        assert.equal(password.type, 'password');

        // A ready-made control is placed as given and takes the row id.
        const custom = document.createElement('textarea');
        const customRow = settingRow({ id: 'custom', label: 'Custom', description: 'Any control.', control: custom });
        assert.equal(customRow.children[1], custom);
        assert.equal(custom.id, 'custom');
        assert.throws(() => settingRow({ id: '', label: 'x', description: 'y', control: { type: 'number' } }), TypeError);
        assert.throws(() => settingRow({ id: 'x', label: 'x', description: 'y', control: { type: 'colour' } }), TypeError);
    } finally {
        scope.dispose();
        dom.restore();
    }
});

test('a toggle row holds the Toolkit switch, labelled by the row label', async () => {
    const { settingRow } = await controlsModule;
    const { createScope } = await coreModule;
    const dom = fakeDOM();
    const scope = createScope();
    try {
        const changes = [];
        const row = settingRow({ id: 'video-auto', label: 'Autoplay videos', description: 'Videos stay within the same gallery.',
            control: { type: 'toggle', checked: false }, scope, onChange: value => changes.push(value) });
        const field = row.children[1];
        // createSwitch('', '', id): `label.ca-switch` > input[type=checkbox][role=switch] + an empty label text.
        assert.equal(field.tagName, 'LABEL');
        assert.equal(field.className, 'ca-switch');
        const input = field.children[0];
        assert.deepEqual([input.type, input.role, input.id, input.checked], ['checkbox', 'switch', 'video-auto', false]);
        assert.equal(row.children[0].children[0].htmlFor, input.id);
        assert.equal(input.dataset.setting, 'video-auto');
        input.checked = true;
        input.dispatch('change');
        assert.deepEqual(changes, [true]);
        // The concept's toggles are on unless configured off (`val !== false`).
        const on = settingRow({ id: 'video-loop', label: 'Loop videos', description: 'Repeat a clip.', control: { type: 'toggle' } });
        assert.equal(on.children[1].children[0].checked, true);
    } finally {
        scope.dispose();
        dom.restore();
    }
});

test('operationRow is the concept operation with a danger class only when flagged', async () => {
    const { operationRow } = await controlsModule;
    const { createScope } = await coreModule;
    const dom = fakeDOM();
    const scope = createScope();
    try {
        const clicks = [];
        const row = operationRow({ title: 'Missing-file links',
            description: 'Remove stale database links only. Files on disk are never deleted by this operation.',
            label: 'Review plan', danger: true, scope, onClick: button => clicks.push(button) });
        assert.equal(row.className, 'operation');
        assert.deepEqual(dom.tags(row), ['div', 'div']);
        assert.deepEqual(dom.tags(row.children[0]), ['h3', 'p']);
        assert.equal(row.children[0].children[0].textContent, 'Missing-file links');
        assert.equal(row.children[1].className, 'row-actions');
        const button = row.children[1].children[0];
        assert.deepEqual([button.tagName, button.type, button.textContent, button.className], ['BUTTON', 'button', 'Review plan', 'danger']);
        button.click();
        assert.deepEqual(clicks, [button]);
        const plain = operationRow({ title: 'Statistics', description: 'Usage by prompt, category, tag and date.', label: 'Explore',
            scope, onClick() {} });
        assert.equal(plain.children[1].children[0].className, '');
        assert.throws(() => operationRow({ title: 'x', description: 'y', label: 'z', onClick() {} }), TypeError);
    } finally {
        scope.dispose();
        dom.restore();
    }
});

test('sectionNav marks the active section and reports the chosen one', async () => {
    const { sectionNav } = await controlsModule;
    const { createScope } = await coreModule;
    const dom = fakeDOM();
    const scope = createScope();
    try {
        const chosen = [];
        const nav = sectionNav({ label: 'System sections', sections: [['overview', 'Overview'], ['appearance', 'Appearance']],
            active: 'appearance', scope, onSelect: id => chosen.push(id) });
        assert.equal(nav.className, 'section-nav');
        assert.equal(nav.getAttribute('role'), 'group');
        assert.equal(nav.getAttribute('aria-label'), 'System sections');
        assert.deepEqual(nav.children.map(button => [button.textContent, button.getAttribute('aria-pressed'), button.dataset.value]),
            [['Overview', 'false', 'overview'], ['Appearance', 'true', 'appearance']]);
        nav.children[0].click();
        assert.deepEqual(chosen, ['overview']);
    } finally {
        scope.dispose();
        dom.restore();
    }
});

test('disposing the scope leaves no listener behind', async () => {
    const { settingRow, operationRow, sectionNav } = await controlsModule;
    const { createScope } = await coreModule;
    const dom = fakeDOM();
    const scope = createScope();
    try {
        settingRow({ id: 'a', label: 'A', description: 'a', control: { type: 'toggle' }, scope, onChange() {} });
        settingRow({ id: 'b', label: 'B', description: 'b', control: { type: 'select', options: ['Grid', 'List'] }, scope, onChange() {} });
        operationRow({ title: 'C', description: 'c', label: 'Go', scope, onClick() {} });
        sectionNav({ label: 'System sections', sections: [['overview', 'Overview']], active: 'overview', scope, onSelect() {} });
        assert.equal(dom.listenerCount() > 0, true);
        scope.dispose();
        assert.equal(dom.listenerCount(), 0);
    } finally {
        scope.dispose();
        dom.restore();
    }
});
