// System › Maintenance (#249 System S7 Task 19): the concept's rows (app.js 688–690), the D100 plan review built from
// the server's Plan, the D101 duplicates review's keep entries and copy, and the D102 metadata cache row.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const maintenance = import('../../web/js/workspace/system-maintenance.mjs');
const review = import('../../web/js/workspace/maintenance-review.mjs');
const jobView = import('../../web/js/workspace/activity-job-view.mjs');

const CONCEPT = fs.readFileSync(path.join(__dirname, '../../docs/concepts/workspace/app.js'), 'utf8');

test('the section lists the concept rows in its order, labels and danger marks', async () => {
    const { MAINTENANCE_SURFACES } = await maintenance;
    const start = CONCEPT.indexOf('case "maintenance":');
    const body = CONCEPT.slice(start, CONCEPT.indexOf('break;', start));
    const concept = [...body.matchAll(/operation\("([^"]+)", "([^"]+)", "[^"]+", "([^"]+)"(?:, '[^']*'|, "")?(, true)?\)/g)]
        .map(([, title, description, label, danger]) => [title, description, label, Boolean(danger)]);
    const surfaces = [...body.matchAll(/<section class="surface"><h2>([^<]+)<\/h2>/g)].map(match => match[1]);
    assert.deepEqual(MAINTENANCE_SURFACES.map(([heading]) => heading), surfaces.map(text => text.replace('&amp;', '&')));
    const production = MAINTENANCE_SURFACES.flatMap(([, rows]) => rows.map(([title, description, label, , danger = false]) =>
        [title, description, label, danger]));
    // D192 (#550, beyond the concept): "Pair category and tags" follows "Unused prompt components" in Database.
    const expected = concept.map(([title, ...rest]) => [title.replace('&amp;', '&'), ...rest]);
    expected.splice(expected.findIndex(([title]) => title === 'Unused prompt components') + 1, 0, PAIR_ROW);
    assert.deepEqual(production, expected);
    assert.equal(production.length, 9);
});

const PAIR_ROW = Object.freeze(['Pair category and tags', 'Fill an empty category or tags on prompts older Pair node runs ' +
    'saved, from their saved pairs. Only empty fields of never-edited prompts.', 'Review plan', false]);

test('D192: the Pair category and tags row reviews the pair_metadata plan under its title (#550)', async () => {
    const { MAINTENANCE_SURFACES } = await maintenance;
    const { OPERATION_TITLES } = await review;
    const [, database] = MAINTENANCE_SURFACES[0];
    const row = database.find(([title]) => title === PAIR_ROW[0]);
    assert.deepEqual(row, [PAIR_ROW[0], PAIR_ROW[1], PAIR_ROW[2], 'pair_metadata']);
    assert.equal(OPERATION_TITLES.pair_metadata, 'Pair category and tags');
});

const PLAN = Object.freeze({
    success: true, token: 't'.repeat(43), operation: 'duplicate_prompts', expires_at: '2026-10-24T12:15:00+00:00',
    scope: { count: 3, label: '3 duplicate prompts in 2 groups' },
    protected: { count: 1, reasons: ['1 group belongs to a saved pair'] },
    backup: { policy: 'on', destination_label: 'prompts.db.backup_<date and time>', expected_bytes: 4096 },
    media: { deletes_files: false, label: 'No files are deleted' },
    disk: { free_bytes: 2 ** 30, required_bytes: 2 ** 24, sufficient: true },
    details: [{ label: 'a cat', detail: '2 copies merged into the oldest' }, { label: 'a dog', detail: '' }],
});

test("plan dialog shows the server's scope, protected, backup policy and media lines (D100)", async () => {
    const { planGrid, planNote, detailLines, backupText, protectedText, OPERATION_TITLES } = await review;
    assert.deepEqual(planGrid(PLAN), [
        ['Scope', '3 duplicate prompts in 2 groups'],
        ['Protected', '1 group belongs to a saved pair'],
        ['Backup', 'Create and verify before changes (prompts.db.backup_<date and time>)'],
        ['Media on disk', 'No files are deleted'],
    ]);
    assert.equal(backupText({ policy: 'off', destination_label: 'x' }), 'No safety backup (the policy is off)');
    assert.equal(backupText({ policy: 'required', destination_label: 'prompts.db.pre-workflow-storage_<date and time>' }),
        'Always created and verified first (prompts.db.pre-workflow-storage_<date and time>)');
    assert.equal(backupText(undefined), 'Create and verify before changes');
    assert.equal(protectedText({ count: 0, reasons: [] }), 'Nothing is protected');
    assert.equal(protectedText({ count: 2, reasons: [] }), '2 records kept');
    assert.equal(protectedText({ count: 3, reasons: ['1 saved pair', '2 titled parents'] }), '1 saved pair · 2 titled parents');
    assert.deepEqual(planGrid({ scope: { count: 1 } })[0], ['Scope', '1 record']);
    assert.deepEqual(planGrid({})[3], ['Media on disk', 'No files are removed']);
    const now = Date.parse('2026-10-24T12:00:00+00:00');
    assert.equal(planNote(PLAN, { now }), 'Nothing changes until you apply. This review holds for 15 minutes.');
    assert.equal(planNote({ ...PLAN, expires_at: 'soon' }, { now }), 'Nothing changes until you apply.');
    assert.equal(planNote({ ...PLAN, expires_at: '2026-10-24T12:00:20+00:00', disk: { free_bytes: 2 ** 20, required_bytes: 3 * 2 ** 20,
        sufficient: false } }, { now }), 'Nothing changes until you apply. This review holds for 1 minute. ' +
        'Not enough free disk space: about 3.0 MB is needed and 1.0 MB is free.');
    assert.deepEqual(detailLines(PLAN), ['a cat · 2 copies merged into the oldest', 'a dog']);
    assert.deepEqual(detailLines({ details: [null, { label: 7 }] }), []);
    assert.equal(OPERATION_TITLES.missing_links, 'Missing-file links');
    // S7 review M8: the workflow migration's plan reads its own title (its job is started outside Maintenance).
    assert.equal(OPERATION_TITLES.workflow_storage, 'Store each workflow once');
});

test('a refused apply names its reason; a lost answer is not confirmed', async () => {
    const { applyRefusal, reviewRefusal, NOT_CONFIRMED, APPLY_FAILED, REVIEW_FAILED } = await review;
    const error = (status, code, message, extra = {}) => Object.assign(new Error(message), { status, code, ...extra });
    assert.deepEqual(applyRefusal(new TypeError('Failed to fetch')), { toast: NOT_CONFIRMED, lost: true });
    assert.deepEqual(NOT_CONFIRMED, ['Outcome not confirmed. ', { text: 'Check Activity', href: '#/activity' },
        ' before applying it again.']);
    const busy = applyRefusal(error(409, 'busy', 'busy', { running: { id: 'a'.repeat(32), title: 'Scan for metadata' } }));
    assert.deepEqual(busy.toast, ['Another job is running: ', { text: 'Scan for metadata', href: `#/activity/${'a'.repeat(32)}` }, '.']);
    assert.deepEqual(applyRefusal(error(409, 'plan-used', 'used', { job: { id: 'b'.repeat(32), title: 'Optimize database' } })).toast,
        ['This plan was already applied: ', { text: 'Optimize database', href: `#/activity/${'b'.repeat(32)}` }, '.']);
    assert.deepEqual(applyRefusal(error(409, 'plan-used', 'used', { job: { id: 'b'.repeat(32), title: null } })).toast[1].text, 'its job');
    assert.deepEqual(applyRefusal(error(409, 'plan-used', 'used', { job: null })), { toast: 'This plan was already applied.' });
    assert.deepEqual(applyRefusal(error(410, 'plan-expired', 'This review expired. Review the plan again.')),
        { toast: 'This review expired. Review the plan again.', review: true });
    assert.deepEqual(applyRefusal(error(409, 'storage-changed', 'Replaced.')), { toast: 'Replaced.', review: true });
    assert.deepEqual(applyRefusal(error(507, 'insufficient-space', 'Not enough free disk space.')), { toast: 'Not enough free disk space.' });
    // S7 review I1: only our server's own refusal is definitive. A proxy's 5xx, a 2xx whose body could not be read,
    // or any answer without the server's refusal may have followed a started job: not confirmed, read Activity again.
    assert.deepEqual(applyRefusal(error(504, null, 'Gateway Timeout')), { toast: NOT_CONFIRMED, lost: true });
    assert.deepEqual(applyRefusal(error(500, null, 'Internal Server Error')), { toast: NOT_CONFIRMED, lost: true });
    assert.deepEqual(applyRefusal(error(202, null, 'Invalid JSON response')), { toast: NOT_CONFIRMED, lost: true });
    assert.deepEqual(applyRefusal(error(502, null, 'Bad Gateway', { refused: false })), { toast: NOT_CONFIRMED, lost: true });
    assert.deepEqual(applyRefusal(error(400, null, 'token must be the plan token', { refused: true })), { toast: APPLY_FAILED });
    assert.deepEqual(applyRefusal(error(500, 'apply-failed', 'Maintenance failed; nothing was changed.', { refused: true })),
        { toast: 'Maintenance failed; nothing was changed.' });
    assert.equal(reviewRefusal(error(413, 'too-large', 'This plan is too large to review at once.')),
        'This plan is too large to review at once.');
    assert.equal(reviewRefusal(error(500, null, 'boom')), REVIEW_FAILED);
    // S7 re-review N1: a duplicate review whose sources changed since the search keeps the server's "search again".
    const changed = 'The sources changed since the search. Search for duplicates again.';
    assert.equal(reviewRefusal(error(409, 'sources-changed', changed)), changed);
    assert.equal(reviewRefusal(new TypeError('x')), REVIEW_FAILED);
});

test('keep entries name each copy by group, source index and path, never a bare path', async () => {
    const { keepEntries } = await review;
    // Two sources hold a file at the same relative path: only the source index tells them apart.
    const output = { path: 'same.png', root: 'output', root_index: 0 };
    const input = { path: 'same.png', root: 'input', root_index: 1 };
    const chosen = new Map([['hash-a', input], ['hash-b', output]]);
    assert.deepEqual(keepEntries(chosen), [
        { group_id: 'hash-a', root_index: 1, path: 'same.png' },
        { group_id: 'hash-b', root_index: 0, path: 'same.png' },
    ]);
});

test('the duplicates dialog says what removing a copy does, History included', async () => {
    const { DUPLICATES_NOTE, pageText, groupText, copyLabel } = await review;
    assert.match(DUPLICATES_NOTE, /History keeps the removed paths in its run records\./);
    assert.match(DUPLICATES_NOTE, /deletes its file from disk/);
    assert.equal(pageText(0, 10, 24), 'Groups 1–10 of 24');
    assert.equal(pageText(20, 4, 24), 'Groups 21–24 of 24');
    assert.equal(pageText(0, 0, 0), 'No duplicate files were found.');
    assert.equal(groupText({ count: 3, size: 120 * 1024, files: [] }), '3 identical files · 120 KB each');
    assert.equal(groupText({ size: 3 * 2 ** 20, files: [{}, {}] }), '2 identical files · 3.0 MB each');
    assert.equal(groupText({ count: 2, size: 10, files: [] }), '2 identical files · 1 KB each');
    assert.equal(copyLabel({ root: 'output', path: 'a/b.png' }), 'Keep output/a/b.png');
    assert.equal(copyLabel({ root: '', path: 'b.png', missing: true }), 'Keep b.png (no longer on disk)');
});

test('Metadata cache follows Q5: disabled with its reason while nothing is owned (D102)', async () => {
    const { metadataCacheDescription, METADATA_CACHE_DESCRIPTION, NOTHING_CACHED_REASON } = await maintenance;
    assert.equal(METADATA_CACHE_DESCRIPTION, 'Clear only Prompt Manager metadata cached by this browser.');
    assert.equal(metadataCacheDescription({ keys: [], caches: [] }), `${METADATA_CACHE_DESCRIPTION} ${NOTHING_CACHED_REASON}`);
    assert.equal(metadataCacheDescription({ keys: ['k'], caches: [] }), METADATA_CACHE_DESCRIPTION);
    // S7 review M6: a browser that refused the listing is not claimed empty.
    assert.equal(metadataCacheDescription({ keys: [], caches: [], denied: true }),
        `${METADATA_CACHE_DESCRIPTION} This browser did not allow checking its storage.`);
});

test('only a finished duplicate search with groups offers Review groups', async () => {
    const { hasDuplicateGroups } = await jobView;
    const job = (state, groups, kind = 'duplicates') => ({ kind, state, counters: { groups_found: groups } });
    assert.equal(hasDuplicateGroups(job('finished', 2)), true);
    assert.equal(hasDuplicateGroups(job('finished', 0)), false);
    assert.equal(hasDuplicateGroups(job('running', 2)), false);
    assert.equal(hasDuplicateGroups(job('finished', 2, 'scan')), false);
    assert.equal(hasDuplicateGroups({ kind: 'duplicates', state: 'finished', counters: {} }), false);
});

test('a failed apply the library overtook offers Review again, back to its review (S7 review M2)', async () => {
    const { reviewAgainOf } = await jobView;
    const failed = (kind, failure, operation) => ({ kind, state: 'failed', scope: { operation, failure } });
    assert.deepEqual(reviewAgainOf(failed('maintenance', 'plan-changed', 'duplicate_prompts')),
        { operation: 'duplicate_prompts' });
    assert.deepEqual(reviewAgainOf(failed('maintenance', 'storage-changed', 'optimize')), { operation: 'optimize' });
    // A duplicate cleanup reviewed against a search the sources overtook: search again in Maintenance.
    assert.deepEqual(reviewAgainOf(failed('duplicate_cleanup', 'sources-changed', 'duplicate_files')),
        { section: '#/system/maintenance' });
    assert.equal(reviewAgainOf(failed('maintenance', 'backup-failed', 'optimize')), null);
    assert.equal(reviewAgainOf(failed('maintenance', 'plan-changed', 'not_an_operation')), null);
    assert.equal(reviewAgainOf({ ...failed('maintenance', 'plan-changed', 'optimize'), state: 'finished' }), null);
    assert.equal(reviewAgainOf(failed('scan', 'plan-changed', 'optimize')), null);
});

test('a plan with nothing to do offers no Apply (S7 live test)', async () => {
    const { hasWork, NOTHING_TO_APPLY } = await review;
    assert.equal(hasWork(PLAN), true);
    assert.equal(hasWork({ ...PLAN, scope: { count: 0, label: 'No duplicate prompt text' } }), false);
    assert.equal(hasWork({ ...PLAN, operation: 'optimize', scope: { count: 1, label: 'The whole database' } }), true);
    // A plan whose count is unknown is not assumed empty: the server decides at apply.
    assert.equal(hasWork({ ...PLAN, scope: {} }), true);
    assert.equal(NOTHING_TO_APPLY, 'There is nothing to apply; nothing was changed.');
});

// S12 (#329): the rotated-log cleanup plan changes no library record: its Backup reads "Not needed", the review shows no
// D55 backup box, and its details are files.
const LOG_PLAN = { operation: 'rotated_logs', token: 't', expires_at: '2026-10-26T23:15:00',
    scope: { count: 3, label: '3 rotated log copies · 28.4 MB' },
    protected: { count: 3, reasons: ['Kept: the active log', 'Kept: 1 link', 'Kept: 1 other file'] },
    backup: { policy: 'none', destination_label: null, expected_bytes: null },
    media: { deletes_files: true, label: 'Deletes only the listed log files. No image, video or library record changes.' },
    details: [{ label: 'prompt_manager.log.1', detail: '10.0 MB · 2026-10-25 14:02' }] };

test('a plan without a database change reads Not needed and shows no backup box', async () => {
    const { planGrid, backupText, backupBox, BACKUP_COPY, OPERATION_TITLES } = await review;
    assert.equal(backupText({ policy: 'none' }), 'Not needed: no library record changes');
    assert.deepEqual(planGrid(LOG_PLAN), [
        ['Scope', '3 rotated log copies · 28.4 MB'],
        ['Protected', 'Kept: the active log · Kept: 1 link · Kept: 1 other file'],
        ['Backup', 'Not needed: no library record changes'],
        ['Media on disk', 'Deletes only the listed log files. No image, video or library record changes.'],
    ]);
    const onBackup = () => {};
    assert.equal(backupBox(LOG_PLAN, onBackup), null);
    // Every database plan keeps its D55 box.
    assert.deepEqual(backupBox({ ...LOG_PLAN, operation: 'optimize', backup: { policy: 'required' } }, onBackup), { copy: BACKUP_COPY, onBackup });
    assert.deepEqual(backupBox({ operation: 'missing_links' }, onBackup), { copy: BACKUP_COPY, onBackup });
    // Its title heads the dialog and lets Activity offer Review again.
    assert.equal(OPERATION_TITLES.rotated_logs, 'Rotated log copies');
});

test('rotated_logs details read Listed files', async () => {
    const { detailsSummary, detailLines } = await review;
    assert.equal(detailsSummary(LOG_PLAN, 3), 'Listed files (3)');
    assert.equal(detailsSummary({ ...LOG_PLAN, scope: { count: 60 } }, 50), 'Listed files (50 of 60)');
    assert.equal(detailsSummary({ operation: 'missing_links', scope: { count: 2 } }, 2), 'Listed records (2)');
    assert.deepEqual(detailLines(LOG_PLAN), ['prompt_manager.log.1 · 10.0 MB · 2026-10-25 14:02']);
});

test('a rotated-log cleanup the log overtook offers Review again (S12)', async () => {
    const { reviewAgainOf } = await jobView;
    assert.deepEqual(reviewAgainOf({ kind: 'maintenance', state: 'failed', scope: { operation: 'rotated_logs', failure: 'plan-changed' } }),
        { operation: 'rotated_logs' });
});

// #550 live test: a records operation's rows have no path, only a detail ("Prompt 12: category …").
const RECORD_JOB = Object.freeze({
    id: 'a'.repeat(32), kind: 'maintenance', title: 'Pair category and tags', state: 'finished',
    admission: 'records', cancellable: false, progress: 100, processed: 0, total: 0, status_line: 'Filled 2 prompts',
    scope: { operation: 'pair_metadata' }, counters: { changed: 2, protected: 0, errors: 0 },
    started_at: 1, finished_at: 2,
});

test('#550: path-less record rows are kept with their detail; file rows are unchanged', async () => {
    const { parseDetail, filterRows } = await jobView;
    const parsed = parseDetail({ ...RECORD_JOB, outcomes: { changed: [
        { path: '', root: '', root_index: null, detail: 'Prompt 2325: category “asian”; tags asian' },
        { path: '', root: '', root_index: null, detail: '' },
        { path: 'a/b.png', root: 'output', root_index: 0, detail: 'Link removed' },
    ] }, log: [] });
    assert.deepEqual(parsed.outcomes.changed, [
        { path: '', root: '', root_index: null, detail: 'Prompt 2325: category “asian”; tags asian', record: true },
        { path: 'a/b.png', root: 'output', root_index: 0, detail: 'Link removed', record: false },
    ]);
    // The filter reads a record's detail and a file's path.
    assert.deepEqual(filterRows(parsed.outcomes.changed, 'asian').map(row => row.detail),
        ['Prompt 2325: category “asian”; tags asian']);
    assert.deepEqual(filterRows(parsed.outcomes.changed, 'a/b').map(row => row.path), ['a/b.png']);
});

test('#550: a records operation says records, not files, for an empty group and a filter', async () => {
    const { groupCopy, EMPTY_GROUP, NO_MATCH } = await jobView;
    assert.deepEqual(groupCopy(RECORD_JOB), { empty: 'No records in this group.', noMatch: 'No listed record matches this filter.',
        placeholder: 'Filter records…' });
    for (const operation of ['duplicate_prompts', 'unused_components']) {
        assert.equal(groupCopy({ ...RECORD_JOB, scope: { operation } }).empty, 'No records in this group.');
    }
    const files = { empty: EMPTY_GROUP, noMatch: NO_MATCH, placeholder: 'Filter by file path…' };
    assert.deepEqual(groupCopy({ ...RECORD_JOB, scope: { operation: 'missing_links' } }), files);
    assert.deepEqual(groupCopy({ ...RECORD_JOB, kind: 'scan', scope: {} }), files);
});
