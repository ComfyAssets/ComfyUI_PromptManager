const test = require('node:test');
const assert = require('node:assert/strict');
const source = import('../../web/js/workspace/plural.mjs');

test('countOf uses the singular noun only for exactly one', async () => {
    const { countOf, noun } = await source;
    assert.equal(countOf(1, 'linked image'), '1 linked image');
    assert.equal(countOf(0, 'linked image'), '0 linked images');
    assert.equal(countOf(2, 'saved negative variant'), '2 saved negative variants');
    assert.equal(countOf(1, 'distinct recorded run'), '1 distinct recorded run');
    assert.equal(countOf(149, 'distinct recorded run'), '149 distinct recorded runs');
    assert.equal(noun(1, 'image'), 'image');
    assert.equal(noun(202, 'image'), 'images');
    assert.equal(noun(2, 'child', 'children'), 'children');
});
