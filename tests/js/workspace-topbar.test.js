import test from 'node:test';
import assert from 'node:assert/strict';
import { canvasRoot, canvasHref, bindCanvasLink } from '../../web/js/workspace/topbar.mjs';

// The shared mount-aware resolver (#164), as api-client.js implements it.
const resolverAt = pathname => url => {
    if (!url.startsWith('/prompt_manager/')) return url;
    const match = pathname.match(/^(.*)\/prompt_manager(?:\/|$)/);
    return match && match[1] ? match[1] + url : url;
};

function link() {
    const node = new EventTarget();
    node.href = '';
    return node;
}
function click(node, init = {}) {
    const event = new Event('click', { cancelable: true });
    Object.assign(event, { button: 0, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false, ...init });
    node.dispatchEvent(event);
    return event;
}

test('the canvas root is the ComfyUI root under the page mount', () => {
    assert.equal(canvasRoot(resolverAt('/prompt_manager/workspace')), '/');
    assert.equal(canvasRoot(resolverAt('/proxy/comfy/prompt_manager/workspace')), '/proxy/comfy/');
    assert.throws(() => canvasRoot(() => 'https://elsewhere.example/'), /canvas/i);
});

test('the canvas root follows a non-standard proxy mount', () => {
    assert.equal(canvasRoot(resolverAt('/ComfyBackendDirect/prompt_manager/workspace')), '/ComfyBackendDirect/');
    assert.equal(canvasHref(resolverAt('/ComfyBackendDirect/prompt_manager/workspace'), '/ComfyBackendDirect/prompt_manager/workspace'),
        '/ComfyBackendDirect/');
});

// A4 review: a resolver failure must never stop the shell from mounting, and the fallback stays on the page's mount.
test('a resolver failure falls back to the page mount and never to another origin', () => {
    const warn = console.warn;
    const warnings = [];
    console.warn = (...args) => warnings.push(args);
    try {
        const broken = () => { throw new Error('resolver unavailable'); };
        assert.equal(canvasHref(broken, '/ComfyBackendDirect/prompt_manager/workspace'), '/ComfyBackendDirect/');
        assert.equal(canvasHref(broken, '/ComfyBackendDirect/prompt_manager/workspace/'), '/ComfyBackendDirect/');
        assert.equal(canvasHref(broken, '/prompt_manager/workspace'), '/');
        assert.equal(canvasHref(() => 'https://elsewhere.example/prompt_manager/', '/a/prompt_manager/workspace'), '/a/');
        assert.equal(canvasHref(broken, '//elsewhere.example/prompt_manager/workspace'), '/');
        assert.equal(canvasHref(broken, '/workspace'), '/');
        assert.equal(canvasHref(broken, undefined), '/');
        assert.equal(canvasHref(undefined, '/a/prompt_manager/workspace'), '/a/');
        assert.equal(warnings.length, 8);
    } finally {
        console.warn = warn;
    }
});

test('ComfyUI link follows its href unless a resolved pair is selected', () => {
    const node = link();
    const opened = [];
    let pair = null;
    const sender = { open: (roles, trigger) => opened.push([roles, trigger]) };
    const unbind = bindCanvasLink(node, { getSelectedPair: () => pair, sender, href: '/proxy/' });
    assert.equal(node.href, '/proxy/');
    assert.equal(click(node).defaultPrevented, false);
    pair = { positive: 'a castle', negative: '' };
    const event = click(node);
    assert.equal(event.defaultPrevented, true);
    assert.deepEqual(opened, [[{ positive: 'a castle', negative: '' }, node]]);
    // A modified or secondary click keeps the browser's link behavior.
    for (const init of [{ metaKey: true }, { ctrlKey: true }, { shiftKey: true }, { altKey: true }, { button: 1 }]) {
        assert.equal(click(node, init).defaultPrevented, false);
    }
    // One role alone is not a pair.
    pair = { positive: 'a castle' };
    assert.equal(click(node).defaultPrevented, false);
    assert.equal(opened.length, 1);
    unbind();
    pair = { positive: 'a castle', negative: '' };
    assert.equal(click(node).defaultPrevented, false);
    assert.equal(opened.length, 1);
});

test('ComfyUI link without a pair source is a plain link', () => {
    const node = link();
    const unbind = bindCanvasLink(node, { href: '/' });
    assert.equal(click(node).defaultPrevented, false);
    unbind();
});
