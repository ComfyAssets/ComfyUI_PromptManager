import test from 'node:test';
import assert from 'node:assert/strict';
import { canvasRoot, bindCanvasLink } from '../../web/js/workspace/topbar.mjs';

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
