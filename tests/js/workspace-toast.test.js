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

function install(dialogs = []) {
    const toast = new FakeToast();
    const previous = global.document;
    global.document = {
        querySelectorAll: selector => { assert.equal(selector, 'dialog'); return dialogs; },
        getElementById: id => id === 'workspace-toast' ? toast : null,
        createTextNode: text => ({ textContent: text }),
        createElement: tag => ({ tag, href: '', textContent: '' }),
    };
    return { toast, restore: () => { global.document = previous; } };
}

// First: the module remembers a toast it has shown.
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


class FakeHost extends EventTarget {
    constructor(modal = false, localName = 'dialog') { super(); this.modal = modal; this.localName = localName; this.nodes = []; }
    matches(selector) { assert.equal(selector, ':modal'); return this.modal; }
    append(node) { node.parentNode?.nodes.splice(node.parentNode.nodes.indexOf(node), 1); node.parentNode = this; this.nodes.push(node); }
}

test('a toast shows inside the topmost open modal and goes home when it closes (#533)', async () => {
    const { showWorkspaceToast } = await source;
    const closed = new FakeHost(false), lower = new FakeHost(true), top = new FakeHost(true);
    const dialogs = [closed, lower, top];
    const { toast, restore } = install(dialogs);
    const home = new FakeHost(false, 'body');home.append(toast);
    try {
        showWorkspaceToast('Opened the folder on this computer.');
        assert.equal(toast.parentNode, top);
        // The upper modal closes: the toast stays visible in the one under it.
        top.modal = false;top.dispatchEvent(new Event('close'));
        assert.equal(toast.parentNode, lower);
        lower.modal = false;lower.dispatchEvent(new Event('close'));
        assert.equal(toast.parentNode, home);
        showWorkspaceToast('Next.');
        assert.equal(toast.parentNode, home);
    } finally {
        restore();
    }
});

test('a toast removed along with its modal still shows the next message (#533)', async () => {
    const { showWorkspaceToast } = await source;
    const modal = new FakeHost(true);
    const { toast, restore } = install([modal]);
    const home = new FakeHost(false, 'body');home.append(toast);
    try {
        showWorkspaceToast('Inside.');
        assert.equal(toast.parentNode, modal);
        modal.modal = false;
        const byId = document.getElementById;
        document.getElementById = () => null; // the dialog left the page with the toast in it
        showWorkspaceToast('After.');
        assert.equal(toast.parentNode, home);
        assert.equal(toast.textContent, 'After.');
        document.getElementById = byId;
    } finally {
        restore();
    }
});
