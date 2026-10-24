// #317: a card's media is the listing's thumbnail (WebP, JPEG XL or JPEG), never something the browser cannot show:
// a JPEG XL thumbnail in a browser without JPEG XL support loads the original, and a thumbnail that fails to load falls
// back to the original. A video whose preview fails still swaps back to its own element (#249).
import test from 'node:test';
import assert from 'node:assert/strict';
import { mountedDom } from './helpers/mounted-dom.mjs';

const support = await import('../../web/js/workspace/thumbnail-support.mjs');
const { cardMedia } = await import('../../web/js/workspace/image-card.mjs');

function withDom(run) {
    const dom = mountedDom();
    dom.win.ApiClient = { resolveUrl: url => `http://host${url}` };
    try { return run(dom); } finally { support.resetJxlSupport(); dom.restore(); }
}

const image = (thumbnail, extra = {}) => ({ root_index: 0, path: 'a.png', filename: 'a.png', url: '/serve/a.png?root=0',
    thumbnail_url: thumbnail, ...extra });

test('a WebP thumbnail is the card media and falls back to the original when it fails', () => withDom(() => {
    const node = cardMedia(image('/serve/thumbnails/a_thumb.webp?root=0'));
    assert.equal(node.tagName, 'img');
    assert.equal(node.src, 'http://host/serve/thumbnails/a_thumb.webp?root=0');
    node.fire('error');
    assert.equal(node.src, 'http://host/serve/a.png?root=0');
}));

test('a JPEG XL thumbnail loads the original where this browser cannot show JPEG XL', async () => {
    support.resetJxlSupport();
    await support.detectJxlSupport({ createImage: () => ({ set src(_) { queueMicrotask(() => this.onerror()); } }) });
    const dom = mountedDom();
    dom.win.ApiClient = { resolveUrl: url => `http://host${url}` };
    try {
        assert.equal(support.jxlSupport(), false);
        const node = cardMedia(image('/serve/thumbnails/a_thumb.jxl?root=0'));
        assert.equal(node.src, 'http://host/serve/a.png?root=0');
    } finally { support.resetJxlSupport(); dom.restore(); }
});

test('a JPEG XL thumbnail is used where this browser shows JPEG XL', async () => {
    await support.detectJxlSupport({ createImage: () => ({ set src(_) { queueMicrotask(() => this.onload()); } }) });
    const dom = mountedDom();
    dom.win.ApiClient = { resolveUrl: url => `http://host${url}` };
    try {
        const node = cardMedia(image('/serve/thumbnails/a_thumb.jxl?root=0'));
        assert.equal(node.src, 'http://host/serve/thumbnails/a_thumb.jxl?root=0');
    } finally { support.resetJxlSupport(); dom.restore(); }
});

test('a video whose preview fails still swaps back to its own element', () => withDom(dom => {
    const holder = new dom.Node('div');
    const node = cardMedia(image('/serve/thumbnails/c_thumb.webp?root=0', { path: 'c.mp4', filename: 'c.mp4', url: '/serve/c.mp4?root=0', is_video: true }));
    holder.append(node);
    node.replaceWith = replacement => { holder.children = [replacement]; };
    node.fire('error');
    assert.equal(holder.children[0].tagName, 'video');
    assert.equal(holder.children[0].src, 'http://host/serve/c.mp4?root=0');
}));

test('a video with a JPEG XL thumbnail keeps its own element where JPEG XL cannot be shown (review M2)', async () => {
    support.resetJxlSupport();
    await support.detectJxlSupport({ createImage: () => ({ set src(_) { queueMicrotask(() => this.onerror()); } }) });
    const dom = mountedDom();
    dom.win.ApiClient = { resolveUrl: url => `http://host${url}` };
    try {
        const node = cardMedia(image('/serve/thumbnails/c_thumb.jxl?root=0',
            { path: 'c.mp4', filename: 'c.mp4', url: '/serve/c.mp4?root=0', is_video: true }));
        assert.equal(node.tagName, 'video');
        assert.equal(node.src, 'http://host/serve/c.mp4?root=0');
    } finally { support.resetJxlSupport(); dom.restore(); }
});
