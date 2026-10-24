// Per-kind Activity descriptors (#249 System S5 Task 13, D111): each job kind's pill verb, toasts, line, tiles,
// outcomes, openable rows and labels, from one table (web/js/workspace/activity-kinds.mjs).
const test = require('node:test');
const assert = require('node:assert/strict');

const source = import('../../web/js/workspace/activity-kinds.mjs');
const store = import('../../web/js/workspace/activity-store.mjs');

const ID = 'a'.repeat(32);

function job(overrides = {}) {
    return { id: ID, kind: 'scan', title: 'Scan for metadata', state: 'running', started_at: 1, finished_at: null,
        progress: 42, status_line: '', processed: 8240, total: 19622,
        counters: { added: 1, known: 0, review: 0, no_metadata: 0, errors: 1 }, scope: {}, listed: {}, log_length: 0,
        cancellable: true, ...overrides };
}

test('scan descriptors are unchanged', async () => {
    const { kindOf } = await source;
    const scan = kindOf('scan');
    assert.equal(scan.pillVerb, 'Scanning');
    assert.equal(scan.startToast(job()), 'Scan started.');
    assert.deepEqual(scan.tiles, [['added', 'Added to Library', 'added'], ['known', 'Already known', ''],
        ['review', 'Needs review', 'warn'], ['no_metadata', 'No usable metadata', ''], ['errors', 'Errors', 'bad']]);
    assert.deepEqual(scan.outcomes, [['added', 'Added to Library'], ['review', 'Needs review'], ['errors', 'Errors'],
        ['no_metadata', 'No usable metadata'], ['known', 'Already known']]);
    assert.deepEqual([...scan.openable].sort(), ['added', 'known']);
    assert.deepEqual([scan.log, scan.cancelLabel], ['Scan log', 'Cancel scan']);
    assert.equal(scan.finishToast(job({ state: 'finished' })), 'Scan finished · 1 added · 1 error');
    assert.equal(scan.finishToast(job({ state: 'cancelled' })), 'Scan cancelled · 1 added');
    assert.equal(scan.finishToast(job({ state: 'failed' })), 'Scan failed · 1 added · 1 error');
    assert.equal(scan.line(job()), '1 added · 1 error');
    assert.equal(scan.kept(job()), '1 added');
    assert.equal(scan.scope(job({ scope: { output_folders: 2 } })), '2 output folders');
    // The store keeps answering with the same table.
    const { jobView, finishToast, startToast, pillParts, outcomeLine } = await store;
    assert.equal(jobView('scan'), scan);
    assert.equal(finishToast(job({ state: 'finished' })), 'Scan finished · 1 added · 1 error');
    assert.equal(startToast(job()), 'Scan started.');
    assert.equal(pillParts(job()).text, 'Scanning · 42% · 8,240 / 19,622');
    assert.equal(outcomeLine(job()), '1 added · 1 error');
});

test('thumbnail job shows its own tiles and toasts', async () => {
    const { kindOf, viewOf } = await source;
    const { finishToast, startToast, pillParts, outcomeLine } = await store;
    const thumbs = job({ kind: 'thumbnails', title: 'Thumbnail generation', processed: 4, total: 10, progress: 40,
        counters: { generated: 1420, skipped: 3, errors: 1 }, scope: { mode: 'missing', size: 512, quality: 'medium',
            output_folders: 2 } });
    const view = kindOf('thumbnails');
    assert.equal(viewOf(thumbs), view);
    assert.deepEqual(view.tiles, [['generated', 'Generated', 'added'], ['skipped', 'Skipped', ''], ['errors', 'Errors', 'bad']]);
    assert.deepEqual(view.outcomes, [['generated', 'Generated'], ['errors', 'Errors'], ['skipped', 'Skipped']]);
    assert.deepEqual([...view.openable].sort(), ['generated', 'skipped']);
    assert.deepEqual([view.log, view.cancelLabel], ['Job log', 'Cancel job']);
    assert.equal(pillParts(thumbs).text, 'Generating thumbnails · 40% · 4 / 10');
    assert.equal(startToast(thumbs), 'Thumbnail generation started.');
    assert.equal(finishToast({ ...thumbs, state: 'finished' }), 'Thumbnails finished · 1,420 generated · 1 error');
    assert.equal(finishToast({ ...thumbs, state: 'failed' }), 'Thumbnails failed · 1,420 generated · 1 error');
    assert.equal(finishToast({ ...thumbs, state: 'cancelled' }), 'Thumbnails cancelled · 1,420 generated');
    assert.equal(finishToast({ ...thumbs, counters: { generated: 0, skipped: 0, errors: 2 }, state: 'finished' }),
        'Thumbnails finished · 0 generated · 2 errors');
    assert.equal(outcomeLine(thumbs), '1,420 generated · 1 error');
    assert.equal(view.kept(thumbs), '1,420 generated');
    assert.equal(view.scope(thumbs), '2 output folders');
});

test('non-cancellable jobs show no Cancel job', async () => {
    const { canCancel } = await source;
    assert.equal(canCancel(job()), true);
    assert.equal(canCancel(job({ cancellable: false })), false);
    for (const state of ['finished', 'cancelled', 'failed', 'lost']) assert.equal(canCancel(job({ state })), false);
    // The health check is a read job the server cannot stop.
    assert.equal(canCancel(job({ kind: 'health', title: 'Health check', cancellable: false, counters: {} })), false);
});

test('indeterminate progress has no value attribute', async () => {
    const { indeterminate, showProgress } = await source;
    const { pillParts } = await store;
    function node() {
        const attributes = new Map();
        return { attributes, textContent: '', set value(v) { attributes.set('value', String(v)); },
            get value() { return Number(attributes.get('value') ?? 0); },
            setAttribute(key, v) { attributes.set(key, String(v)); }, removeAttribute(key) { attributes.delete(key); } };
    }
    // Running with no total yet: the server has not said how much there is, so there is no percentage.
    const health = job({ kind: 'health', title: 'Health check', progress: 0, processed: 0, total: 0, counters: {},
        cancellable: false, status_line: 'Checking the database and image links' });
    assert.equal(indeterminate(health), true);
    const bar = node();
    bar.value = 37;
    showProgress(bar, health);
    assert.equal(bar.attributes.has('value'), false);
    assert.equal(bar.textContent, '');
    assert.equal(pillParts(health).text, 'Health check');
    assert.deepEqual(pillParts(health), { label: 'Health check', files: '', text: 'Health check' });
    // A known total is determinate, and so is any ended job.
    const scan = job();
    assert.equal(indeterminate(scan), false);
    showProgress(bar, scan);
    assert.equal(bar.attributes.get('value'), '42');
    assert.equal(bar.textContent, '42%');
    assert.equal(indeterminate({ ...health, state: 'finished', progress: 100 }), false);
    // A scan before its file count is known is indeterminate too, never "0%".
    assert.equal(pillParts(job({ total: 0, processed: 0, progress: 0 })).text, 'Scanning');
});

test('held legacy jobs read "(admin page)" with no outcomes', async () => {
    const { viewOf } = await source;
    const { jobDetail } = await import('../../web/js/workspace/activity.mjs');
    const { finishToast, outcomeLine } = await store;
    // A hold has no outcome groups (counters {}), whatever its kind: thumbnails held by the gallery's clear included.
    for (const [kind, title] of [['restore', 'Database restore (admin page)'],
        ['thumbnails', 'Thumbnail clear (gallery page)'], ['maintenance', 'Database maintenance (admin page)']]) {
        const held = job({ kind, title, counters: {}, cancellable: false, progress: 0, processed: 0, total: 0 });
        const view = viewOf(held);
        assert.deepEqual([view.tiles, view.outcomes, [...view.openable]], [[], [], []], kind);
        assert.equal(outcomeLine(held), '');
        assert.match(jobDetail(held), /^Started \d\d:\d\d · closing this view does not cancel it$/);
        const ended = { ...held, state: 'finished', finished_at: 2 };
        assert.match(jobDetail(ended), /^Finished \d\d:\d\d$/);
        assert.equal(finishToast(ended), `${title} finished.`);
        assert.ok(title.endsWith('page)'));
    }
});

test('unknown and generic kinds show no tiles of another kind', async () => {
    const { kindOf, viewOf } = await source;
    for (const kind of ['maintenance', '', 'constructor', '__proto__', 'health']) {
        const view = kindOf(kind);
        assert.deepEqual([view.tiles, view.outcomes, [...view.openable]], [[], [], []], kind);
        assert.deepEqual([view.log, view.cancelLabel], ['Job log', 'Cancel job']);
        assert.equal(view.pillVerb, null);
        assert.equal(view.line(job({ kind })), '');
        assert.equal(view.kept(job({ kind })), '');
    }
    const backup = job({ kind: 'backup', title: 'Backup' });
    assert.equal(kindOf('backup').startToast(backup), 'Backup started.');
    assert.equal(kindOf('backup').finishToast({ ...backup, state: 'finished' }), 'Backup finished.');
    assert.equal(kindOf('backup').finishToast({ ...backup, state: 'cancelled' }), 'Backup was cancelled.');
    assert.equal(kindOf('backup').finishToast({ ...backup, state: 'failed' }), 'Backup failed.');
    // A kind's tiles are only the groups its job counts: no zero tile for a group the server never reported.
    const partial = viewOf(job({ counters: { added: 3, errors: 0 } }));
    assert.deepEqual(partial.tiles, [['added', 'Added to Library', 'added'], ['errors', 'Errors', 'bad']]);
    assert.deepEqual(partial.outcomes, [['added', 'Added to Library'], ['errors', 'Errors']]);
    assert.deepEqual([...partial.openable], ['added']);
    assert.equal(partial.log, 'Scan log');
});
