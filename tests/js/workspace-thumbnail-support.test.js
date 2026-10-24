// #317: JPEG XL thumbnails in browsers that cannot decode them. The workspace decodes a tiny embedded JPEG XL once;
// where it fails, cards and the inspector cover use the original instead, and any thumbnail <img> that fails to load
// falls back to the original. A thumbnail is only a placeholder: the original is what every other view uses.
import test from 'node:test';
import assert from 'node:assert/strict';

const support = await import('../../web/js/workspace/thumbnail-support.mjs');

/** A fake Image whose decode answers `ok` once its src is set. */
function fakeImage(ok, log) {
    return () => {
        const image = {};
        Object.defineProperty(image, 'src', {
            set(value) { log.push(value); queueMicrotask(() => (ok ? image.onload?.() : image.onerror?.())); },
        });
        return image;
    };
}

test('a .jxl thumbnail URL is recognised by its path, never its query', () => {
    assert.equal(support.isJxlUrl('/prompt_manager/images/serve/thumbnails/a_thumb.jxl?v=1&root=0'), true);
    assert.equal(support.isJxlUrl('/prompt_manager/images/serve/thumbnails/a_thumb.JXL'), true);
    assert.equal(support.isJxlUrl('/prompt_manager/images/serve/thumbnails/a_thumb.webp?x=.jxl'), false);
    assert.equal(support.isJxlUrl('/x/a.jxl#frag'), true);
    for (const value of [null, undefined, '', 42]) assert.equal(support.isJxlUrl(value), false);
});

test('decode support is probed once with the embedded image and remembered', async () => {
    support.resetJxlSupport();
    const log = [];
    assert.equal(support.jxlSupport(), undefined, 'unknown before the probe');
    const first = support.detectJxlSupport({ createImage: fakeImage(true, log) });
    const again = support.detectJxlSupport({ createImage: fakeImage(false, log) });
    assert.equal(await first, true);
    assert.equal(await again, true);
    assert.equal(log.length, 1, 'one probe');
    assert.match(log[0], /^data:image\/jxl;base64,\/woA/);
    assert.equal(support.jxlSupport(), true);
    support.resetJxlSupport();
    assert.equal(await support.detectJxlSupport({ createImage: fakeImage(false, log) }), false);
    assert.equal(support.jxlSupport(), false);
    support.resetJxlSupport();
    assert.equal(await support.detectJxlSupport({ createImage: () => { throw new Error('no Image'); } }), false);
    support.resetJxlSupport();
});

test('cards use the original for a JPEG XL thumbnail this browser cannot show', () => {
    const image = { url: '/serve/a.png?root=0', thumbnail_url: '/serve/thumbnails/a_thumb.jxl?root=0' };
    const webp = { ...image, thumbnail_url: '/serve/thumbnails/a_thumb.webp?root=0' };
    assert.equal(support.thumbnailSource(image, false), image.url);
    assert.equal(support.thumbnailSource(image, true), image.thumbnail_url);
    assert.equal(support.thumbnailSource(image, undefined), image.thumbnail_url, 'unknown: try it, the error falls back');
    assert.equal(support.thumbnailSource(webp, false), webp.thumbnail_url);
    assert.equal(support.thumbnailSource({ url: image.url }, false), image.url);
    assert.equal(support.thumbnailSource({ url: image.url, thumbnail_url: null }, true), image.url);
});

test('a thumbnail that fails to load falls back to the original once', () => {
    const listeners = {};
    const node = { src: '/serve/thumbnails/a_thumb.jxl',
        addEventListener(type, handler, options) { listeners[type] = { handler, options }; } };
    support.fallBackToOriginal(node, '/serve/a.png');
    assert.equal(listeners.error.options.once, true);
    listeners.error.handler();
    assert.equal(node.src, '/serve/a.png');
    const original = { src: '/serve/a.png', addEventListener: () => assert.fail('no listener for the original itself') };
    support.fallBackToOriginal(original, '/serve/a.png');
    const listen = [];
    const scoped = { src: '/serve/thumbnails/b_thumb.webp', isConnected: true };
    support.fallBackToOriginal(scoped, '/serve/b.png', (target, type, handler, options) => listen.push([target, type, handler, options]));
    assert.equal(listen[0][1], 'error');
    listen[0][2]();
    assert.equal(scoped.src, '/serve/b.png');
});
