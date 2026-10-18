const test = require('node:test');
const assert = require('node:assert/strict');
const source = import('../../web/js/workspace/history-state.mjs');

test('chronological columns keep the visible runs, three-column overscan and distant focus in ascending order', async () => {
    const {visibleHistoryColumns} = await source;
    assert.equal(typeof visibleHistoryColumns, 'function', 'history must expose a bounded column window');
    assert.deepEqual(visibleHistoryColumns(1001, 640, 128, 999), [7,8,9,10,11,12,13,14,999]);
    assert.deepEqual(visibleHistoryColumns(1001, 0, 64), [0,1,2,3]);
    assert.deepEqual(visibleHistoryColumns(1001, 64000, 390, 0), [0,997,998,999,1000]);
    assert.deepEqual(visibleHistoryColumns(3, 100, 1), [0,1,2]);
    assert.deepEqual(visibleHistoryColumns(0, 0, 500), []);
});

test('column geometry handles fractional edges, invalid scroll and focus without scanning all loaded runs', async () => {
    const {visibleHistoryColumns} = await source;
    assert.equal(typeof visibleHistoryColumns, 'function');
    assert.deepEqual(visibleHistoryColumns(50000, 640.5, 64, 0), [0,7,8,9,10,11,12,13,14]);
    assert.deepEqual(visibleHistoryColumns(121, NaN, Infinity), [0,1,2,3]);
    assert.equal(visibleHistoryColumns(120, 900, 1).length, 120);
    assert.ok(visibleHistoryColumns(10000000, 40000, 390).length <= 14);
    for(const count of [-1, 1.1, NaN, '4']) assert.throws(()=>visibleHistoryColumns(count,0,100), TypeError);
});
