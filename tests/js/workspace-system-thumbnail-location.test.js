// Thumbnail location (#320, D196–D201): the row's state and status line, the move review's rows, the unavailable
// notice's text, the Activity views of a move and a prune, the prune scope's plan line and the Move library row.
const test = require('node:test');
const assert = require('node:assert/strict');

const locationModule = import('../../web/js/workspace/system-thumbnail-location.mjs');
const kindsModule = import('../../web/js/workspace/activity-kinds.mjs');
const reviewModule = import('../../web/js/workspace/thumbnail-review.mjs');
const libraryModule = import('../../web/js/workspace/library-location.mjs');

const SOURCE = Object.freeze({ mode: 'source', path: null, default_path: '/data/thumbnails', available: true,
    problem: null, cleanup_pending: 0 });
const FOLDER = Object.freeze({ mode: 'folder', path: 'E:\\Thumbnails', default_path: '/data/thumbnails',
    available: true, problem: null, cleanup_pending: 0 });

const PLAN = Object.freeze({
    from: { mode: 'source', path: null, available: true },
    to: { mode: 'folder', path: 'E:\\Thumbnails', exists: true, will_create: false, default: false },
    totals: { sources: 3, files: 8512, bytes: 1.2 * 1024 ** 3, orphans: 41, temps: 2, links: 1, left_in_place: 1,
        too_long: 32, unlisted_sources: 0, unlisted_files: 0 },
    too_long_listed: ['Archive 2025 · a_thumb.webp', 'Archive 2025 · b_thumb.webp'],
    left_listed: ['Archive 2025 · notes.txt'],
    unlisted: [],
    free_bytes: 1.6 * 1024 ** 4,
    confirm_required: true,
    token: 't',
});

test('the status line says where previews are now', async () => {
    const { statusText } = await locationModule;
    assert.equal(statusText(SOURCE), 'Now: inside each source.');
    assert.equal(statusText(FOLDER), 'Now: one folder, E:\\Thumbnails.');
    assert.equal(statusText({ ...FOLDER, path: '/data/thumbnails' }),
        'Now: one folder, /data/thumbnails. Using the default folder next to your library.');
    assert.equal(statusText({ ...FOLDER, available: false, problem: 'missing' }),
        'Now: one folder, E:\\Thumbnails · unavailable. Originals are shown.');
    assert.match(statusText({ ...SOURCE, cleanup_pending: 1 }), /Old thumbnails in 1 place wait to be removed/);
    assert.equal(statusText(undefined), '');
    assert.match(statusText(SOURCE, { mode: 'folder', path: '/data/thumbnails' }),
        /^Now: inside each source\. The suggested folder is next to your library/);
    assert.equal(statusText(SOURCE, { mode: 'folder', path: '/other' }), 'Now: inside each source.');
});

test('Review move… is offered only for another location', async () => {
    const { choiceChanged } = await locationModule;
    assert.equal(choiceChanged(SOURCE, { mode: 'source' }), false);
    assert.equal(choiceChanged(SOURCE, { mode: 'folder', path: ' ' }), false);
    assert.equal(choiceChanged(SOURCE, { mode: 'folder', path: '/data/thumbnails' }), true);
    assert.equal(choiceChanged(FOLDER, { mode: 'folder', path: 'E:\\Thumbnails' }), false);
    assert.equal(choiceChanged(FOLDER, { mode: 'folder', path: 'F:\\Thumbs' }), true);
    assert.equal(choiceChanged(FOLDER, { mode: 'source' }), true);
    assert.equal(choiceChanged({ ...SOURCE, problem: 'invalid' }, { mode: 'source' }), true);
    assert.equal(choiceChanged(undefined, { mode: 'source' }), false);
});

test('the review lists what moves, what is removed and what stays', async () => {
    const { reviewRows } = await locationModule;
    const rows = Object.fromEntries(reviewRows(PLAN));
    assert.equal(rows.From, 'Inside each source (3 sources)');
    assert.equal(rows.To, 'E:\\Thumbnails (empty folder)');
    assert.equal(rows.Thumbnails, '8,512 in 3 sources · 1.2 GB');
    assert.equal(rows['Free space'], '1638.4 GB free');
    assert.match(rows['Too long for Windows'][0], /^32 thumbnails cannot be stored there/);
    assert.deepEqual(rows['Too long for Windows'][1], [...PLAN.too_long_listed, 'and 30 more']);
    assert.deepEqual(rows['Removed after the move'][1],
        ['41 thumbnails whose original is gone', '2 unfinished temporary files']);
    assert.equal(rows['Left in place'][0], '1 file that is not a thumbnail:');
    assert.equal(rows['Links skipped'], '1 link inside a thumbnails folder is not followed');
});

test('the review of a move back names the folder and the removed sources', async () => {
    const { reviewRows } = await locationModule;
    const back = reviewRows({ ...PLAN, from: { mode: 'folder', path: '/data/thumbnails' },
        to: { mode: 'source', path: null }, unlisted: [{ key: 'old-1c22e0a4', files: 240 }],
        totals: { sources: 1, files: 1, bytes: 10 }, free_bytes: undefined });
    const rows = Object.fromEntries(back);
    assert.equal(rows.From, '/data/thumbnails');
    assert.equal(rows.To, 'Inside each source (1 source)');
    assert.equal(rows['Free space'], undefined);
    assert.match(rows['Removed after the move'][0], /The folder itself is removed once empty/);
    assert.deepEqual(rows['Removed after the move'][1],
        ['1 source no longer listed: 240 thumbnails are removed; they are made again if it returns']);
    const fresh = Object.fromEntries(reviewRows({ ...PLAN, to: { mode: 'folder', path: '/d', default: true, will_create: true } }));
    assert.equal(fresh.To, '/d (new, next to your library)');
});

test('the notice says why the folder is unavailable', async () => {
    const { noticeText } = await locationModule;
    assert.match(noticeText({ ...FOLDER, problem: 'missing' }), /^E:\\Thumbnails is missing\. Originals are shown/);
    assert.match(noticeText({ ...FOLDER, problem: 'unreachable' }), /can’t be reached/);
    assert.match(noticeText({ ...FOLDER, problem: 'marker-mismatch' }), /holds another thumbnail folder/);
    assert.match(noticeText({ ...FOLDER, problem: 'invalid' }), /config\.json can’t be used/);
    assert.match(noticeText({ ...FOLDER, problem: null }), /is being checked/);
});

test('sizes are written as the server writes them', async () => {
    const { sizeText } = await locationModule;
    assert.equal(sizeText(512), '512 B');
    assert.equal(sizeText(85.2 * 1024 * 1024), '85.2 MB');
});

test('a move and a prune show their own tiles in Activity', async () => {
    const { viewOf, movePhase, finishToast } = await kindsModule;
    const move = { kind: 'thumbnails', state: 'running', cancellable: true, scope: { mode: 'move', from: 'source' },
        counters: { moved: 3, skipped: 0, left: 1, errors: 2 }, status_line: '' };
    assert.deepEqual(viewOf(move).tiles.map(([group, label]) => label), ['Moved', 'Already there', 'Left in place', 'Errors']);
    assert.equal(viewOf(move).cancelLabel, 'Cancel move');
    assert.match(movePhase(move), /^Step 1 of 3: copying\. Thumbnails are still read from inside each source/);
    assert.match(movePhase({ ...move, cancellable: false }), /can no longer be cancelled/);
    assert.match(movePhase({ ...move, scope: { mode: 'move', phase: 'remove' } }), /Removing the old copies/);
    assert.match(movePhase({ ...move, state: 'finished' }), /1 file that is not a thumbnail was left in place/);
    assert.match(movePhase({ ...move, state: 'cancelled' }), /^Nothing changed/);
    const done = { ...move, state: 'finished', status_line: 'Thumbnails moved to E:\\T: 3 moved · old copies removed.' };
    assert.equal(finishToast(done), done.status_line);
    const prune = { kind: 'thumbnails', state: 'running', cancellable: true, scope: { mode: 'prune' },
        counters: { removed: 1, left: 0, errors: 0 } };
    assert.deepEqual(viewOf(prune).tiles.map(([, label]) => label), ['Removed', 'Left in place', 'Errors']);
    const generate = { kind: 'thumbnails', state: 'running', cancellable: true, scope: { mode: 'missing' },
        counters: { generated: 0, skipped: 0, errors: 0 } };
    assert.deepEqual(viewOf(generate).tiles.map(([, label]) => label), ['Generated', 'Skipped', 'Errors']);
});

test('the prune scope reads its plan line from the server', async () => {
    const { planText, PLAN_FAILED, PRUNE_OPTION } = await reviewModule;
    assert.equal(PRUNE_OPTION[1], 'Remove thumbnails of removed sources');
    assert.equal(planText({ mode: 'prune', line: '2 sources no longer listed: 1,240 thumbnails (85.2 MB) are removed.' }),
        '2 sources no longer listed: 1,240 thumbnails (85.2 MB) are removed.');
    assert.equal(planText({ mode: 'prune' }), PLAN_FAILED);
});

test('Move library says where thumbnails kept in one folder stay', async () => {
    const { reviewGrid } = await libraryModule;
    const base = { path: 'p', target: 't', size_bytes: 0, config_moves: true };
    assert.equal(reviewGrid(base).some(([term]) => term === 'Thumbnails'), false);
    const rows = Object.fromEntries(reviewGrid({ ...base, thumbnails_folder: '/data/thumbnails' }));
    assert.equal(rows.Thumbnails, 'Stay in /data/thumbnails. Move them in System › Sources & scans.');
});
