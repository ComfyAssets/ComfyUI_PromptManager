// The shell's shared toast: plain messages clear after 6.5 s; a message with a link stays 20 s (D57), and any toast
// stays while the pointer or keyboard focus rests on it.
const test = require('node:test');
const assert = require('node:assert/strict');

const source = import('../../web/js/workspace/toast.mjs');

class FakeToast extends EventTarget {
    constructor() { super(); this.held = false; this.nodes = []; this.text = ''; }
    get textContent() { return this.text; }
    set textContent(value) { this.text = value; this.nodes = []; }
    replaceChildren(...nodes) { this.nodes = nodes; this.text = nodes.map(node => node.textContent).join(''); }
    matches(selector) { assert.equal(selector, ':hover, :focus-within'); return this.held; }
}

function install() {
    const toast = new FakeToast();
    const previous = global.document;
    global.document = {
        getElementById: id => id === 'workspace-toast' ? toast : null,
        createTextNode: text => ({ textContent: text }),
        createElement: tag => ({ tag, href: '', textContent: '' }),
    };
    return { toast, restore: () => { global.document = previous; } };
}

test('a plain toast clears after 6.5 s and a linked one after 20 s', async t => {
    const { showWorkspaceToast, TOAST_MS, LINK_TOAST_MS } = await source;
    t.mock.timers.enable({ apis: ['setTimeout'] });
    const { toast, restore } = install();
    try {
        showWorkspaceToast('Scan started.');
        assert.equal(toast.textContent, 'Scan started.');
        t.mock.timers.tick(TOAST_MS);
        assert.equal(toast.textContent, '');

        showWorkspaceToast(['Another job is running: ', { text: 'Scan', href: '#/activity/x' }, '.']);
        assert.equal(toast.textContent, 'Another job is running: Scan.');
        assert.deepEqual(toast.nodes[1], { tag: 'a', href: '#/activity/x', textContent: 'Scan' });
        t.mock.timers.tick(TOAST_MS);
        assert.equal(toast.textContent, 'Another job is running: Scan.');
        t.mock.timers.tick(LINK_TOAST_MS - TOAST_MS);
        assert.equal(toast.textContent, '');
    } finally {
        restore();
    }
});

test('a toast under the pointer or focus waits until they leave', async t => {
    const { showWorkspaceToast, TOAST_MS } = await source;
    t.mock.timers.enable({ apis: ['setTimeout'] });
    const { toast, restore } = install();
    try {
        showWorkspaceToast('Prompt copied.');
        toast.held = true;
        t.mock.timers.tick(TOAST_MS);
        assert.equal(toast.textContent, 'Prompt copied.', 'held: it stays');
        // Focus moving inside the toast keeps it; leaving it starts a fresh 6.5 s.
        toast.dispatchEvent(new Event('focusout'));
        t.mock.timers.tick(0);
        toast.dispatchEvent(new Event('pointerleave'));
        assert.equal(toast.textContent, 'Prompt copied.');
        toast.held = false;
        toast.dispatchEvent(new Event('focusout'));
        t.mock.timers.tick(0);
        t.mock.timers.tick(TOAST_MS - 1);
        assert.equal(toast.textContent, 'Prompt copied.');
        t.mock.timers.tick(1);
        assert.equal(toast.textContent, '');

        // A newer toast replaces a held one and stops waiting for the old one's pointer.
        showWorkspaceToast('First.');
        toast.held = true;
        t.mock.timers.tick(TOAST_MS);
        showWorkspaceToast('Second.');
        toast.held = false;
        toast.dispatchEvent(new Event('pointerleave'));
        t.mock.timers.tick(TOAST_MS - 1);
        assert.equal(toast.textContent, 'Second.');
        t.mock.timers.tick(1);
        assert.equal(toast.textContent, '');
    } finally {
        restore();
    }
});

test('a page without the toast shows nothing and does not fail', async () => {
    const { showWorkspaceToast } = await source;
    const previous = global.document;
    global.document = { getElementById: () => null };
    try {
        assert.doesNotThrow(() => showWorkspaceToast('Nothing.'));
    } finally {
        global.document = previous;
    }
});
