import test from 'node:test';
import assert from 'node:assert/strict';

const { libraryTitle } = await import('../../web/js/workspace/library.mjs').catch(() => ({}));

test('library title counts UTF-16 units and never splits a surrogate pair', () => {
    assert.equal(typeof libraryTitle, 'function', 'libraryTitle is exported');
    assert.equal(libraryTitle('  short line  \nsecond'), 'short line');
    assert.equal(libraryTitle('a'.repeat(80)), 'a'.repeat(80));
    assert.equal(libraryTitle('a'.repeat(81)), `${'a'.repeat(80)}…`);
    // 80 emoji are 160 UTF-16 units: 40 fit.
    assert.equal(libraryTitle('😀'.repeat(80)), `${'😀'.repeat(40)}…`);
    // 79 units then an emoji: the emoji would straddle the limit, so it is dropped whole.
    assert.equal(libraryTitle(`${'a'.repeat(79)}😀b`), `${'a'.repeat(79)}…`);
});
