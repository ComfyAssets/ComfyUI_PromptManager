const test = require('node:test');
const assert = require('node:assert/strict');
const source = import('../../web/js/workspace/images-multiselect.mjs');

const KEYS = Object.freeze(['0:a', '0:b', '0:c', '0:d', '1:e']);

test('a range runs from the anchor to the target in display order, either way', async () => {
    const { rangeKeys } = await source;
    assert.deepEqual(rangeKeys(KEYS, '0:b', '0:d'), ['0:b', '0:c', '0:d']);
    assert.deepEqual(rangeKeys(KEYS, '1:e', '0:c'), ['0:c', '0:d', '1:e']);
    assert.deepEqual(rangeKeys(KEYS, '0:c', '0:c'), ['0:c']);
});

test('a range without a listed anchor is the target alone, and an unlisted target is empty', async () => {
    const { rangeKeys } = await source;
    assert.deepEqual(rangeKeys(KEYS, null, '0:c'), ['0:c']);
    assert.deepEqual(rangeKeys(KEYS, '9:gone', '0:c'), ['0:c']);
    assert.deepEqual(rangeKeys(KEYS, '0:a', '9:gone'), []);
});

test('applying keys adds or removes them and keeps the same frozen set when nothing changes', async () => {
    const { applyKeys } = await source;
    const start = Object.freeze(new Set(['0:a']));
    const added = applyKeys(start, ['0:b', '0:c'], true);
    assert.deepEqual([...added], ['0:a', '0:b', '0:c']);
    assert.ok(Object.isFrozen(added));
    assert.deepEqual([...start], ['0:a']);
    const removed = applyKeys(added, ['0:a', '0:b'], false);
    assert.deepEqual([...removed], ['0:c']);
    assert.equal(applyKeys(removed, ['0:c'], true), removed);
    assert.equal(applyKeys(removed, ['0:a'], false), removed);
});

test('every listed key is checked only when each one is in the set', async () => {
    const { everyChecked } = await source;
    assert.equal(everyChecked(new Set(KEYS), KEYS), true);
    assert.equal(everyChecked(new Set(KEYS.slice(1)), KEYS), false);
    assert.equal(everyChecked(new Set(['x', ...KEYS.slice(1)]), KEYS), false);
    assert.equal(everyChecked(new Set(), []), false);
});

test('the select-all-matching offer needs every loaded image checked and more to load', async () => {
    const { offersAllMatching } = await source;
    const all = new Set(KEYS);
    assert.equal(offersAllMatching(all, KEYS, { total: 9, hasMore: true }), true);
    assert.equal(offersAllMatching(all, KEYS, { total: 5, hasMore: false }), false);
    assert.equal(offersAllMatching(new Set(KEYS.slice(1)), KEYS, { total: 9, hasMore: true }), false);
    assert.equal(offersAllMatching(new Set(), [], { total: 9, hasMore: true }), false);
});

test('the counts read "N selected" and "All N matching selected", grouped', async () => {
    const { matchingLabel, matchingOffer } = await source;
    assert.equal(matchingLabel(12345), 'All 12,345 matching selected');
    assert.equal(matchingOffer(12345), 'Select all 12,345 matching');
});

function fakeTimers() {
    const timers = new Map();
    let next = 1;
    return {
        set: (callback, delay) => { timers.set(next, { callback, delay }); return next++; },
        clear: id => timers.delete(id),
        fire: () => { for (const [id, { callback }] of [...timers]) { timers.delete(id); callback(); } },
        pending: () => timers.size,
        delays: () => [...timers.values()].map(timer => timer.delay),
    };
}

test('a held press fires once after the delay and swallows the click that follows', async () => {
    const { createLongPress, LONG_PRESS_MS } = await source;
    const timers = fakeTimers(), fired = [];
    const press = createLongPress({ onLongPress: target => fired.push(target), setTimer: timers.set, clearTimer: timers.clear });
    press.down(10, 10, 'card-a');
    assert.deepEqual(timers.delays(), [LONG_PRESS_MS]);
    assert.equal(LONG_PRESS_MS, 500);
    press.move(14, 13);
    timers.fire();
    assert.deepEqual(fired, ['card-a']);
    press.up();
    assert.equal(press.consumeClick(), true);
    assert.equal(press.consumeClick(), false);
});

test('moving past the slop, lifting early or cancelling never fires', async () => {
    const { createLongPress, LONG_PRESS_SLOP } = await source;
    assert.equal(LONG_PRESS_SLOP, 10);
    const timers = fakeTimers(), fired = [];
    const press = createLongPress({ onLongPress: target => fired.push(target), setTimer: timers.set, clearTimer: timers.clear });
    press.down(0, 0, 'a'); press.move(0, 11);
    assert.equal(timers.pending(), 0);
    press.down(0, 0, 'b'); press.up();
    assert.equal(timers.pending(), 0);
    press.down(0, 0, 'c');
    assert.equal(press.pending(), true);
    press.cancel();
    assert.equal(press.pending(), false);
    assert.equal(timers.pending(), 0);
    press.move(0, 0); press.up(); press.cancel();
    assert.deepEqual(fired, []);
    assert.equal(press.consumeClick(), false);
    // A new press drops a long press whose click never came.
    press.down(0, 0, 'd'); timers.fire();
    press.down(0, 0, 'e');
    assert.equal(press.consumeClick(), false);
    timers.fire();
    assert.deepEqual(fired, ['d', 'e']);
    // A cancel (a mouse press on a hybrid device) forgets a fired press whose click never came.
    press.cancel();
    assert.equal(press.consumeClick(), false);
});
