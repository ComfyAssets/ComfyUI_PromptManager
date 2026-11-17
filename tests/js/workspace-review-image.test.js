// #646: Needs review hands the viewer and inspector the same identity the Images listing does, the POSIX mark
// for a name holding a backslash included.
import test from 'node:test';
import assert from 'node:assert/strict';
import { imageOf } from '../../web/js/workspace/review.mjs';
import { inspectPath } from '../../web/js/workspace/inspection-state.mjs';

const scope = 'a'.repeat(64);
const item = target => ({ filename: 'a.png', target, url: '/u', thumbnail_url: null, is_video: false });

test('a review item keeps the POSIX mark of a name holding a backslash', () => {
    const path = 'm.safetensors\\2026-11-17/a.png';
    const image = imageOf(item({ root_index: 0, root_scope: scope, path, path_style: 'posix' }));
    assert.equal(image.path_style, 'posix');
    assert.equal(inspectPath(image), `/prompt_manager/images/inspect/m.safetensors%5C2026-11-17/a.png?root=0&root_scope=${scope}`);
});

test('a review item without the mark, or with another value, carries none', () => {
    for (const path_style of [undefined, 'windows', 'POSIX']) {
        const image = imageOf(item({ root_index: 0, root_scope: scope, path: 'a\\b.png', path_style }));
        assert.equal(Object.hasOwn(image, 'path_style'), false);
        assert.throws(() => inspectPath(image), /Invalid inspection identity/);
    }
    assert.equal(Object.hasOwn(imageOf(item({ root_index: 0, root_scope: scope, path: 'p/a.png' })), 'path_style'), false);
});
