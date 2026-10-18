const test = require('node:test');
const assert = require('node:assert/strict');
const source = import('../../web/js/workspace/library-layout.mjs');

test('natural Library rows use the tallest measured card and estimate only missing heights', async () => {
    const {layoutLibraryRows} = await source;
    const heights = Object.freeze([300, 470, 600, 250, null]);
    const layout = layoutLibraryRows(heights, 2);
    assert.deepEqual(layout.rows, [
        {first: 0, last: 1, top: 0, height: 470},
        {first: 2, last: 3, top: 488, height: 600},
        {first: 4, last: 4, top: 1106, height: 468},
    ]);
    assert.equal(layout.height, 1574);
    assert.deepEqual(layoutLibraryRows([300, 470], 1), {
        rows: [{first: 0, last: 0, top: 0, height: 300}, {first: 1, last: 1, top: 318, height: 470}], height: 788,
    });
    assert.deepEqual(layoutLibraryRows([], 2), {rows: [], height: 0});
});

test('row measurements validate geometry and invalid heights reserve the configured estimate', async () => {
    const {layoutLibraryRows} = await source;
    assert.deepEqual(layoutLibraryRows([0, -1, NaN, Infinity, undefined, '300'], 2, {estimate: 448, gap: 0}).rows.map(row => row.height), [448, 448, 448]);
    for (const columns of [0, -1, 1.5, Infinity, NaN, '2']) assert.throws(() => layoutLibraryRows([], columns), TypeError);
    for (const estimate of [0, -1, NaN, Infinity]) assert.throws(() => layoutLibraryRows([], 2, {estimate}), TypeError);
    for (const gap of [-1, NaN, Infinity]) assert.throws(() => layoutLibraryRows([], 2, {gap}), TypeError);
    const before = layoutLibraryRows([300, 470, 600], 2);
    const appended = layoutLibraryRows([300, 470, 600, 250, 310], 2);
    assert.deepEqual(appended.rows[0], before.rows[0]);
    assert.deepEqual(appended.rows[1], {first: 2, last: 3, top: 488, height: 600});
    assert.equal(appended.height, 1416);
});

test('measured windows add two overscan rows and pin distant focus in ascending DOM order', async () => {
    const {layoutLibraryRows, visibleLibraryRows} = await source;
    const {rows} = layoutLibraryRows(Array(40).fill(100), 2, {gap: 10});
    for (const row of rows) Object.freeze(row);
    Object.freeze(rows);
    assert.deepEqual(visibleLibraryRows(rows, 550, 100, 0), [0, 3, 4, 5, 6, 7]);
    assert.deepEqual(visibleLibraryRows(rows, 550, 100, 39), [3, 4, 5, 6, 7, 19]);
    assert.deepEqual(visibleLibraryRows(rows, 550, 100, 40), [3, 4, 5, 6, 7]);
    assert.deepEqual(visibleLibraryRows(rows, 550, 100, -1), [3, 4, 5, 6, 7]);
    assert.deepEqual(visibleLibraryRows([], 0, 900), []);
    assert.deepEqual(visibleLibraryRows(rows, NaN, Infinity), [0, 1, 2]);
});

test('measured-window lookup binary searches a 50000-row shelf without walking it', async () => {
    const {visibleLibraryRows} = await source;
    let reads = 0;
    const rows = new Proxy({length: 50000}, {get(target, key) {
        if (/^\d+$/.test(String(key))) { reads++; const index = Number(key); return {first: index * 2, last: index * 2 + 1, top: index * 118, height: 100}; }
        return Reflect.get(target, key);
    }});
    assert.deepEqual(visibleLibraryRows(rows, 1180000, 100, 0), [0, 9998, 9999, 10000, 10001, 10002]);
    assert.ok(reads < 500, `${reads} numeric row reads`);
});
