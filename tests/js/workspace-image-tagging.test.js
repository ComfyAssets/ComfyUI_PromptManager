import test from 'node:test';
import assert from 'node:assert/strict';

// Guarded: before the dialog module exists every test fails on an assertion, not on the import.
const loaded = await import('../../web/js/workspace/image-tagging.mjs').catch(() => null);
const module = () => { assert.ok(loaded, 'image-tagging.mjs is not implemented'); return loaded; };
const SCOPE = 'a'.repeat(64);

test('a listed file gives its tagging target, anything else none', () => {
    const { imageTarget } = module();
    assert.deepEqual(imageTarget({ root_index: 1, root_scope: SCOPE, path: 'a/b.png', filename: 'b.png' }),
        { root_index: 1, root_scope: SCOPE, path: 'a/b.png' });
    for (const image of [null, {}, { root_index: -1, root_scope: SCOPE, path: 'a.png' }, { root_index: 0, path: 'a.png' },
        { root_index: 0, root_scope: 'A'.repeat(64), path: 'a.png' }, { root_index: 0, root_scope: SCOPE, path: '' },
        { root_index: 1.5, root_scope: SCOPE, path: 'a.png' }]) {
        assert.equal(imageTarget(image), null);
    }
});

test('tag save listeners subscribe and unsubscribe once', () => {
    const { onImageTagsSaved, imageTagsListenerCount } = module();
    const before = imageTagsListenerCount();
    const off = onImageTagsSaved(() => {});
    assert.equal(imageTagsListenerCount(), before + 1);
    off(); off();
    assert.equal(imageTagsListenerCount(), before);
    assert.throws(() => onImageTagsSaved('nope'), TypeError);
});
