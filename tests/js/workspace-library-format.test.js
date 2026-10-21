const test = require('node:test');
const assert = require('node:assert/strict');
const source = import('../../web/js/workspace/library-layout.mjs');

/** Runs `body` with the process time zone set to `zone`, restoring the previous one. */
async function inZone(zone, body) {
    const previous = process.env.TZ;
    process.env.TZ = zone;
    try { await body(); } finally {
        if (previous === undefined) delete process.env.TZ; else process.env.TZ = previous;
    }
}

test('used label boundaries in UTC and local time', async () => {
    const { usedLabel } = await source;
    await inZone('UTC', () => {
        const now = new Date('2026-10-20T12:00:00Z');
        assert.equal(usedLabel('2026-10-20T00:00:00Z', now), 'Used today');
        assert.equal(usedLabel('2026-10-20T11:59:59Z', now), 'Used today');
        assert.equal(usedLabel('2026-10-19T23:59:59Z', now), 'Used yesterday');
        assert.equal(usedLabel('2026-10-19T00:00:00Z', now), 'Used yesterday');
        assert.equal(usedLabel('2026-10-18T23:59:59Z', now), 'Used 2 days ago');
        assert.equal(usedLabel('2026-10-14T00:00:00Z', now), 'Used 6 days ago');
        assert.equal(usedLabel('2026-10-13T23:59:59Z', now), 'Used Oct 13');
        assert.equal(usedLabel('2026-01-02T08:00:00Z', now), 'Used Jan 2');
        // D23: a use in another calendar year names its year, so last year's Oct 21 never reads as this year's.
        assert.equal(usedLabel('2025-10-21T08:00:00Z', now), 'Used Oct 21, 2025');
        assert.equal(usedLabel('2025-12-31T23:59:59Z', now), 'Used Dec 31, 2025');
        // A clock ahead of this browser is not a future use: it reads as today.
        assert.equal(usedLabel('2026-10-21T09:00:00Z', now), 'Used today');
    });
    // The day boundary is the viewer's local midnight, not UTC's (PDT is UTC-7 in October).
    await inZone('America/Los_Angeles', () => {
        const now = new Date('2026-10-20T07:30:00Z');
        assert.equal(usedLabel('2026-10-20T07:10:00Z', now), 'Used today');
        assert.equal(usedLabel('2026-10-20T06:30:00Z', now), 'Used yesterday');
        assert.equal(usedLabel('2026-10-14T07:00:00Z', now), 'Used 6 days ago');
        assert.equal(usedLabel('2026-10-14T06:59:59Z', now), 'Used Oct 13');
    });
    // Across the November DST change a calendar day is still one day.
    await inZone('America/New_York', () => {
        const now = new Date('2026-11-02T05:30:00Z');
        assert.equal(usedLabel('2026-11-01T04:30:00Z', now), 'Used yesterday');
        assert.equal(usedLabel('2026-10-31T05:30:00Z', now), 'Used 2 days ago');
    });
    for (const missing of [null, undefined, '', 'not a date', 42]) assert.equal(usedLabel(missing, new Date()), 'Not used yet');
});

test('rating label omits unrated', async () => {
    const { ratingLabel } = await source;
    assert.deepEqual(ratingLabel(5), { stars: '★★★★★', hidden: 'Rated 5 out of 5' });
    assert.deepEqual(ratingLabel(1), { stars: '★', hidden: 'Rated 1 out of 5' });
    for (const unrated of [null, undefined, 0, 6, -1, 2.5, '3', true]) assert.deepEqual(ratingLabel(unrated), { stars: '', hidden: '' });
});
