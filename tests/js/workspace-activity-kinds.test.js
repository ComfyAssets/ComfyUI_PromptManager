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
    for (const kind of ['backup', '', 'constructor', '__proto__', 'health']) {
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

// System S7 (carry-over from S6): the maintenance jobs read as themselves in Activity, with their own counters, a
// report like every job, and Cancel only when the server can stop them.
test('a maintenance apply shows its title, changed / protected / errors and no Cancel', async () => {
    const { kindOf, viewOf, canCancel } = await source;
    const { finishToast, startToast, pillParts, outcomeLine } = await store;
    const apply = job({ kind: 'maintenance', title: 'Duplicate prompt text', progress: 0, processed: 0, total: 0,
        counters: { changed: 3, protected: 1, errors: 0 }, scope: { operation: 'duplicate_prompts' }, cancellable: false });
    const view = kindOf('maintenance');
    assert.equal(viewOf(apply), view);
    assert.deepEqual(view.tiles, [['changed', 'Changed', 'added'], ['protected', 'Protected', ''], ['errors', 'Errors', 'bad']]);
    assert.deepEqual(view.outcomes, [['changed', 'Changed'], ['errors', 'Errors'], ['protected', 'Protected']]);
    assert.deepEqual([...view.openable], []);
    assert.deepEqual([view.log, view.cancelLabel], ['Maintenance log', 'Cancel job']);
    assert.equal(canCancel(apply), false);
    // Indeterminate while it runs: the title alone, never a made-up percentage.
    assert.equal(pillParts(apply).text, 'Duplicate prompt text');
    assert.equal(startToast(apply), 'Duplicate prompt text started.');
    assert.equal(outcomeLine(apply), '3 changed · 0 errors');
    assert.equal(finishToast({ ...apply, state: 'finished' }), 'Duplicate prompt text finished · 3 changed');
    // A failed apply says why, so plan-changed asks for a new review.
    const changed = 'The library changed since the review; nothing was changed. Review the plan again.';
    assert.equal(finishToast({ ...apply, state: 'failed', status_line: changed, counters: { changed: 0, protected: 1, errors: 1 } }),
        `Duplicate prompt text failed · ${changed}`);
    assert.equal(finishToast({ ...apply, state: 'failed', status_line: '' }), 'Duplicate prompt text failed · 0 errors');
    // Store each workflow once is a cancellable maintenance job: Cancel shows while it runs.
    const migration = { ...apply, title: 'Store each workflow once', cancellable: true };
    assert.equal(canCancel(migration), true);
    assert.equal(finishToast({ ...migration, state: 'cancelled' }), 'Store each workflow once cancelled · 3 changed');
});

test('a duplicate search counts groups, opens their first copy and cancels as a search', async () => {
    const { kindOf, viewOf, canCancel } = await source;
    const { finishToast, startToast, pillParts, outcomeLine } = await store;
    const find = job({ kind: 'duplicates', title: 'Find duplicate image files', processed: 5, total: 10, progress: 50,
        counters: { groups_found: 0 }, scope: { output_folders: 2 } });
    const view = kindOf('duplicates');
    assert.equal(viewOf(find), view);
    assert.deepEqual(view.tiles, [['groups_found', 'Groups found', '']]);
    assert.deepEqual(view.outcomes, [['groups_found', 'Groups found']]);
    assert.deepEqual([...view.openable], ['groups_found']);
    assert.deepEqual([view.log, view.cancelLabel], ['Search log', 'Cancel search']);
    assert.equal(canCancel(find), true);
    assert.equal(pillParts(find).text, 'Finding duplicates · 50% · 5 / 10');
    assert.equal(startToast(find), 'Duplicate search started.');
    assert.equal(view.scope(find), '2 output folders');
    const found = { ...find, state: 'finished', counters: { groups_found: 12 } };
    assert.equal(outcomeLine(found), '12 groups found');
    assert.equal(finishToast(found), 'Duplicate search finished · 12 groups found');
    assert.equal(finishToast({ ...find, state: 'cancelled' }), 'Duplicate search cancelled · 0 groups found');
    assert.equal(finishToast({ ...found, counters: { groups_found: 1 } }), 'Duplicate search finished · 1 group found');
});

test('a duplicate cleanup counts removed, kept and errors and cannot be cancelled', async () => {
    const { kindOf, viewOf, canCancel } = await source;
    const { finishToast, pillParts, outcomeLine } = await store;
    const cleanup = job({ kind: 'duplicate_cleanup', title: 'Remove duplicate image files', processed: 1, total: 4,
        progress: 25, counters: { removed: 1, kept: 0, errors: 0 }, cancellable: false });
    const view = kindOf('duplicate_cleanup');
    assert.equal(viewOf(cleanup), view);
    assert.deepEqual(view.tiles, [['removed', 'Removed', 'added'], ['kept', 'Kept', ''], ['errors', 'Errors', 'bad']]);
    assert.deepEqual(view.outcomes, [['removed', 'Removed'], ['errors', 'Errors'], ['kept', 'Kept']]);
    assert.deepEqual([...view.openable], ['kept']);
    assert.deepEqual([view.log, view.cancelLabel], ['Cleanup log', 'Cancel job']);
    assert.equal(canCancel(cleanup), false);
    assert.equal(pillParts(cleanup).text, 'Removing duplicate files · 25% · 1 / 4');
    assert.equal(outcomeLine(cleanup), '1 removed · 0 errors');
    assert.equal(finishToast({ ...cleanup, state: 'finished', counters: { removed: 3, kept: 1, errors: 1 } }),
        'Duplicate cleanup finished · 3 removed · 1 error');
});

test('a failed duplicate cleanup says the server reason (S7 review M3)', async () => {
    const { finishToast } = await store;
    const reason = 'The sources changed since the search. Search for duplicates again.';
    const failed = job({ kind: 'duplicate_cleanup', title: 'Remove duplicate image files', state: 'failed', status_line: reason,
        counters: { removed: 0, kept: 0, errors: 1 }, cancellable: false });
    assert.equal(finishToast(failed), `Duplicate cleanup failed · ${reason}`);
    assert.equal(finishToast({ ...failed, status_line: '' }), 'Duplicate cleanup failed · 0 removed · 1 error');
});
