// System › Backup & export (#249 System S9 Task 24): the concept's copy (app.js 691–693), the status line from
// GET /backup/status (D103), the restore review's rows from the S8 preview (D105), its upload and refusals, the export
// list, the restore job's Activity copy and the reload after a finished restore.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { ApiError } = require('../../web/js/api-client.js');

const backups = import('../../web/js/workspace/system-backups.mjs');
const kinds = import('../../web/js/workspace/activity-kinds.mjs');
const scan = import('../../web/js/workspace/activity-scan.mjs');
const dialogs = import('../../web/js/workspace/workspace-dialogs.mjs');

const CONCEPT = fs.readFileSync(path.join(__dirname, '../../docs/concepts/workspace/app.js'), 'utf8');
// The module's code without its comments (which name the concept's prototype labels they replace).
const SOURCE = fs.readFileSync(path.join(__dirname, '../../web/js/workspace/system-backups.mjs'), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

const conceptSection = () => {
    const start = CONCEPT.indexOf('case "backups":');
    return CONCEPT.slice(start, CONCEPT.indexOf('break;', start));
};

test('the section keeps the concept copy: surfaces, descriptions and the two kept labels', () => {
    const body = conceptSection();
    for (const text of ['Database backup', 'A complete recovery snapshot, including prompt pairs, metadata and image links. Media files are separate.',
        'Review restore…', 'Portable export', 'Export reusable recipes and tags as versioned JSON. This is distinct from a complete database backup.',
        'Import / inspect file', 'Recovery policy', 'Proposed', 'Create a safety backup before maintenance', 'Scheduled database backups',
        'Manual only', 'Daily', 'Weekly']) {
        assert.ok(body.includes(text), `concept lacks ${text}`);
        assert.ok(SOURCE.includes(text), `production lacks ${text}`);
    }
    // D103/D104: prototype copy is never shipped.
    // D104 (M2): the toggle's concept line ("Preview a verified backup…") misstates the policy and is replaced too.
    for (const sample of ['Simulate backup', 'Sample backup', 'Export sample JSON', 'Production feature proposal', 'A restore would be',
        'Preview a verified backup before applying operations that change records.']) {
        assert.ok(body.includes(sample));
        assert.ok(!SOURCE.includes(sample), `production ships ${sample}`);
    }
});

test('status line shows only server-reported backups (D103)', async () => {
    const { backupStatusText, NO_BACKUP, STATUS_LOADING, STATUS_FAILED, whenText } = await backups;
    assert.equal(backupStatusText(undefined), STATUS_LOADING);
    assert.equal(backupStatusText(null), STATUS_FAILED);
    assert.equal(backupStatusText({ success: true, safety_backups: [], last_download: null }), NO_BACKUP);
    assert.equal(NO_BACKUP, 'No backup recorded.');
    // A time the server did not send in a readable form names nothing.
    assert.equal(backupStatusText({ safety_backups: [{ name: 'x', modified_at: 'soon' }], last_download: { at: 7, verified: true } }),
        NO_BACKUP);
    const at = '2026-10-25T09:30:00+00:00';
    assert.equal(backupStatusText({ safety_backups: [], last_download: { at, bytes: 10, verified: true } }),
        `Downloaded backup · ${whenText(at)} · verified.`);
    assert.equal(backupStatusText({ safety_backups: [], last_download: { at, bytes: 10, verified: false } }),
        `Downloaded backup · ${whenText(at)} · not verified.`);
    const safety = [{ name: 'prompts.db.backup_2', bytes: 1, modified_at: at }, { name: 'prompts.db.backup_1', bytes: 1, modified_at: at }];
    assert.equal(backupStatusText({ safety_backups: safety, last_download: null }), `Safety backup · ${whenText(at)} · 2 on disk.`);
    assert.equal(backupStatusText({ safety_backups: safety.slice(0, 1), last_download: { at, verified: true } }),
        `Downloaded backup · ${whenText(at)} · verified. Safety backup · ${whenText(at)} · 1 on disk.`);
    assert.equal(whenText(undefined), null);
});

const PREVIEW = Object.freeze({
    success: true, token: 'r'.repeat(43), expires_at: '2026-10-25T12:15:00+00:00',
    summary: { prompts: 1234, positives: 900, pairs: 3, archived_pairs: 0, image_links: 56, runs: 1, saved_views: 2, collections: 1 },
    schema: { migrated: true, library_state: true }, integrity: 'ok', documents: { repaired: 0, cleared: 0, images: 0 },
    live: { prompts: 1, positives: 1, pairs: 0, archived_pairs: 0, image_links: 0, runs: 0, saved_views: 0, collections: 0 }, size: 4096,
    safety_backup: { destination_label: 'prompts.db.backup_<date and time>' },
});

test('Review restore shows the real summary in the concept rows (D105)', async () => {
    const { restoreGrid, restoreNote, countsText, integrityText, snapshotText, whenText } = await backups;
    const file = { name: 'prompts_backup_20261025_093000.db', size: 3 * 2 ** 20, lastModified: Date.parse('2026-10-25T09:30:00Z') };
    assert.deepEqual(restoreGrid(PREVIEW, file), [
        ['Snapshot', `prompts_backup_20261025_093000.db · 3.0 MB · ${whenText('2026-10-25T09:30:00.000Z')}`],
        ['Integrity', 'Verified'],
        ['Migration', 'Upgraded to the current schema'],
        ['Safety backup', 'Created before replacing (prompts.db.backup_<date and time>)'],
        ['In the backup', '1,234 prompts · 56 image links · 3 saved pairs · 1 run'],
        ['In the library now', '1 prompt · 0 image links · 0 saved pairs · 0 runs'],
    ]);
    assert.equal(restoreGrid({ ...PREVIEW, schema: { migrated: false } }, file)[2][1], 'Current schema');
    assert.equal(restoreGrid({ ...PREVIEW, safety_backup: null }, file)[3][1], 'Created before replacing');
    assert.equal(countsText({}), 'Not reported');
    assert.equal(snapshotText({ name: 'a.db' }), 'a.db');
    assert.equal(integrityText({ integrity: 'ok', documents: { repaired: 2, cleared: 1, images: 1 } }),
        'Verified · repaired 2 damaged workflow documents · could not repair 1 workflow document: 1 image shows no workflow');
    assert.equal(integrityText({ integrity: 'ok', documents: { repaired: 1, cleared: 2, images: 3 } }),
        'Verified · repaired 1 damaged workflow document · could not repair 2 workflow documents: 3 images show no workflow');
    assert.equal(integrityText({}), 'Not verified');
    const now = Date.parse('2026-10-25T12:00:00+00:00');
    assert.equal(restoreNote(PREVIEW, { now }), 'Restore replaces the whole library with this backup and runs alone in Activity. ' +
        'Nothing changes until you restore. This review holds for 15 minutes.');
    assert.equal(restoreNote({ expires_at: 'x' }, { now }), 'Restore replaces the whole library with this backup and runs alone ' +
        'in Activity. Nothing changes until you restore.');
});

function response(status, body, headers = {}) {
    return { ok: status >= 200 && status < 300, status, json: async () => {
        if (body === undefined) throw new SyntaxError('not JSON');
        return body;
    }, headers: { get: name => headers[name] ?? null }, blob: async () => new Blob(['x']) };
}

test('invalid file shows the validator message; a refusal names its job; a lost answer stages nothing', async () => {
    const { uploadRestore, reviewRefusal, REVIEW_FAILED, PREVIEW_URL } = await backups;
    const file = new File(['not a database'], 'bad.db');
    let seen;
    const refuse = async (url, init) => { seen = [url, init.method, init.cache]; return response(400,
        { success: false, code: 'invalid-backup', error: 'Not a SQLite database file' }); };
    const invalid = await uploadRestore(file, { fetchUrl: refuse, ApiError }).catch(error => error);
    assert.deepEqual(seen, [PREVIEW_URL, 'POST', 'no-store']);
    assert.equal(reviewRefusal(invalid), "This file can't be restored: Not a SQLite database file.");
    const busy = await uploadRestore(file, { ApiError, fetchUrl: async () => response(409,
        { success: false, code: 'busy', error: 'Busy', running: { id: 'j1', title: 'Metadata scan' } }) }).catch(error => error);
    assert.deepEqual(reviewRefusal(busy), ['Another job is running: ', { text: 'Metadata scan', href: '#/activity/j1' }, '.']);
    const full = await uploadRestore(file, { ApiError, fetchUrl: async () => response(429,
        { success: false, code: 'too-large', error: 'Two restores are already being reviewed. Discard one first.' }) })
        .catch(error => error);
    // I1: the server's "Discard one first" names nothing this page can do; the toast says what the user can.
    assert.equal(reviewRefusal(full), (await backups).REVIEWS_FULL);
    const proxy = await uploadRestore(file, { ApiError, fetchUrl: async () => response(502, undefined) }).catch(error => error);
    assert.equal(reviewRefusal(proxy), REVIEW_FAILED);
    const odd = await uploadRestore(file, { ApiError, fetchUrl: async () => response(200, { success: true }) }).catch(error => error);
    assert.equal(reviewRefusal(odd), REVIEW_FAILED);
    assert.equal(reviewRefusal(new Error('offline')), REVIEW_FAILED);
    assert.deepEqual(await uploadRestore(file, { ApiError, fetchUrl: async () => response(200, PREVIEW) }), PREVIEW);
});

// #583: WebKit reads a FormData's file part by reference, possibly after a garbage collection; the page holds the form
// until the upload is answered, then lets it go, whether the server staged it, refused it or the request failed.
test('a review upload keeps its form referenced until the upload is answered', async () => {
    const { uploadRestore, uploadsInFlight } = await backups;
    const file = new File(['bytes'], 'b.db');
    for (const answer of [async () => response(200, PREVIEW), async () => response(400, { success: false, error: 'x' }),
        async () => { throw new TypeError('offline'); }]) {
        let held;
        const upload = uploadRestore(file, { ApiError, fetchUrl: async (url, init) => {
            held = uploadsInFlight.has(init.body) && init.body.get('database_file').size === file.size;
            return answer();
        } }).catch(() => {});
        await upload;
        assert.equal(held, true);
        assert.equal(uploadsInFlight.size, 0);
    }
});

test('Cancel discards the stage through the api client and never throws', async () => {
    const { discardRestore, PREVIEW_URL } = await backups;
    const calls = [];
    await discardRestore('a/b', { fetchUrl: async (url, init) => { calls.push([url, init.method]); return response(200, {}); } });
    await discardRestore('gone', { fetchUrl: async () => { throw new TypeError('offline'); } });
    assert.deepEqual(calls, [[`${PREVIEW_URL}/a%2Fb`, 'DELETE']]);
});

test('Export JSON downloads /library/export; secondary buttons download /export and /pairs/export (D103)', async () => {
    const { EXPORTS } = await backups;
    assert.deepEqual(EXPORTS.map(([label, url]) => [label, url]), [
        ['Export JSON', '/prompt_manager/library/export'],
        ['Export all prompts JSON', '/prompt_manager/export'],
        ['Export saved pairs JSON', '/prompt_manager/pairs/export'],
    ]);
});

test('a failed export toasts and saves nothing', async () => {
    const { downloadExport, EXPORTS, EXPORT_FAILED } = await backups;
    const toasts = [];
    assert.equal(await downloadExport(EXPORTS[1], { fetchUrl: async () => response(500, {}), notify: text => toasts.push(text) }), false);
    const aborted = AbortSignal.abort();
    assert.equal(await downloadExport(EXPORTS[1], { fetchUrl: async () => { throw new Error('x'); }, notify: text => toasts.push(text),
        signal: aborted }), false);
    assert.deepEqual(toasts, [EXPORT_FAILED]);
});

test('Restore starts the job: Activity names it and ends with the server line', async () => {
    const { finishToast, outcomeLine, pillParts, canCancel, startToast } = await kinds;
    const job = { id: 'j', kind: 'restore', title: 'Database restore', state: 'running', cancellable: false, progress: 0, total: 0,
        counters: { errors: 0 }, status_line: 'Restoring the database' };
    assert.equal(startToast(job), 'Database restore started.');
    assert.equal(pillParts(job).label, 'Restoring the database');
    assert.equal(canCancel(job), false);
    assert.equal(outcomeLine(job), '');
    const done = { ...job, state: 'finished', status_line: 'Restored 3 prompts, 1 image link, 0 saved pairs and 0 runs' };
    assert.equal(finishToast(done), 'Database restore finished · Restored 3 prompts, 1 image link, 0 saved pairs and 0 runs');
    assert.equal(outcomeLine(done), done.status_line);
    const failed = { ...job, state: 'failed', status_line: 'The library changed since the review; nothing was changed. Review the restore again.' };
    assert.equal(finishToast(failed), `Database restore failed · ${failed.status_line}`);
    assert.equal(finishToast({ ...job, state: 'failed', status_line: '' }), 'Database restore failed.');
});

test('views reload once after a restore this page saw finish; a failed restore changes nothing', async () => {
    const { onRestoreFinished } = await backups;
    let list = [];
    const listeners = new Set();
    const activity = { jobs: () => list, subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); } };
    const emit = next => { list = next; for (const listener of listeners) listener(); };
    let reloads = 0;
    const stop = onRestoreFinished(activity, () => { reloads += 1; });
    emit([{ id: 'old', kind: 'restore', state: 'finished' }]);
    assert.equal(reloads, 0, 'a restore that ended before this page saw it run');
    emit([{ id: 'a', kind: 'restore', state: 'running' }, { id: 's', kind: 'scan', state: 'running' }]);
    emit([{ id: 'a', kind: 'restore', state: 'finished' }, { id: 's', kind: 'scan', state: 'finished' }]);
    emit([{ id: 'a', kind: 'restore', state: 'finished' }]);
    assert.equal(reloads, 1);
    emit([{ id: 'b', kind: 'restore', state: 'running' }]);
    emit([{ id: 'b', kind: 'restore', state: 'failed' }]);
    assert.equal(reloads, 1);
    stop();
    assert.equal(listeners.size, 0);
});

// #415: a fake document whose open dialogs the test sets, and a MutationObserver stand-in it triggers.
function dialogPage() {
    const page = { open: 0, observers: new Set() };
    page.doc = { documentElement: {}, querySelector: selector => (selector === 'dialog[open]' && page.open > 0 ? {} : null) };
    page.Observer = class {
        constructor(callback) { this.callback = callback; }
        observe(target, options) { this.target = target; this.options = options; page.observers.add(this); }
        disconnect() { page.observers.delete(this); }
    };
    page.mutate = open => { page.open = open; for (const observer of [...page.observers]) observer.callback([]); };
    return page;
}

test('the restore reload runs at once when no dialog is open', async () => {
    const { createRestoreReload } = await backups;
    const page = dialogPage();
    let reloads = 0;
    const reload = createRestoreReload(() => { reloads += 1; }, { doc: page.doc, Observer: page.Observer });
    reload.request();
    assert.equal(reloads, 1);
    assert.equal(page.observers.size, 0, 'nothing waits');
    reload.request();
    assert.equal(reloads, 2);
});

test('the restore reload waits for every open dialog to close, then runs once (#415)', async () => {
    const { createRestoreReload } = await backups;
    const page = dialogPage();
    page.open = 1;
    let reloads = 0;
    const reload = createRestoreReload(() => { reloads += 1; }, { doc: page.doc, Observer: page.Observer });
    reload.request();
    reload.request();
    assert.equal(reloads, 0, 'an open dialog holds the reload');
    assert.equal(page.observers.size, 1, 'one observer for repeated requests');
    const [observer] = page.observers;
    assert.equal(observer.target, page.doc.documentElement);
    assert.deepEqual(observer.options, { subtree: true, childList: true, attributes: true, attributeFilter: ['open'] });
    page.mutate(1);
    assert.equal(reloads, 0, 'a dialog swapped for another keeps it waiting');
    page.mutate(0);
    assert.equal(reloads, 1);
    assert.equal(page.observers.size, 0, 'the observer stops once it ran');
    page.mutate(0);
    assert.equal(reloads, 1);
    page.open = 1;
    reload.request();
    page.mutate(0);
    assert.equal(reloads, 2, 'a later restore waits again');
});

test('disposing drops a waiting restore reload', async () => {
    const { createRestoreReload } = await backups;
    const page = dialogPage();
    page.open = 1;
    let reloads = 0;
    const reload = createRestoreReload(() => { reloads += 1; }, { doc: page.doc, Observer: page.Observer });
    reload.request();
    reload.dispose();
    assert.equal(page.observers.size, 0);
    page.mutate(0);
    assert.equal(reloads, 0);
    reload.dispose();
});

test('a downloaded backup the server could not verify says so', async () => {
    const { downloadDatabaseBackup, BACKUP_DONE, backupUnverified } = await scan;
    const toasts = [];
    const body = globalThis.document;
    globalThis.document = { createElement: () => ({ click() {}, remove() {} }), body: { append() {} } };
    try {
        await downloadDatabaseBackup({ notify: text => toasts.push(text),
            fetchUrl: async () => response(200, {}, { 'X-PromptManager-Verify': 'ok' }) });
        await downloadDatabaseBackup({ notify: text => toasts.push(text),
            fetchUrl: async () => response(200, {}, { 'X-PromptManager-Verify': 'Database does not contain a pair table' }) });
    } finally {
        globalThis.document = body;
    }
    assert.deepEqual(toasts, [BACKUP_DONE, backupUnverified('Database does not contain a pair table')]);
    assert.equal(toasts[1], 'Database backup downloaded, but it did not pass verification: Database does not contain a pair table');
});

test('the Import dialog shares the rescan copy with Images (D15)', async () => {
    const { rescanFinished, RESCAN_STARTED, RESCAN_FAILED } = await dialogs;
    assert.equal(RESCAN_STARTED, 'Rescanning output folders…');
    assert.equal(RESCAN_FAILED, 'Rescan could not finish. Try again.');
    assert.equal(rescanFinished({ total: 1, roots: 2 }), 'Rescan finished. 1 file in 2 output folders.');
    assert.equal(rescanFinished({ total: 1200, roots: 1 }), 'Rescan finished. 1,200 files in 1 output folder.');
    assert.equal(rescanFinished(null), 'Rescan finished.');
});

test('applyPlan follows a started job even after its section left, and reports the outcome (S9 N5, I1)', async () => {
    const { applyPlan } = await import('../../web/js/workspace/maintenance-review.mjs');
    const job = { id: 'a'.repeat(32), kind: 'restore', title: 'Database restore', state: 'running' };
    const tracked = [];
    const store = { track: raw => { tracked.push(raw.id); return raw; }, load() {} };
    const left = new AbortController();
    left.abort();
    const toasts = [], routes = [];
    const outcome = await applyPlan({ api: { post: async () => ({ success: true, job }) }, store, scope: { signal: left.signal },
        token: 't', notify: text => toasts.push(text), navigate: hash => routes.push(hash) });
    // The page's store follows it (a finished restore must reload the views); the left section neither toasts nor opens it.
    assert.deepEqual(tracked, [job.id]);
    assert.deepEqual([toasts, routes], [[], []]);
    assert.deepEqual(outcome, { job });
    const busy = new ApiError(409, 'Busy', 'busy', null, { success: false, code: 'busy', running: { id: 'j1', title: 'Scan' } });
    const live = { signal: new AbortController().signal };
    const refused = await applyPlan({ api: { post: async () => { throw busy; } }, store, scope: live, token: 't',
        notify: text => toasts.push(text), navigate: hash => routes.push(hash) });
    assert.deepEqual(refused, { refused: busy });
    assert.deepEqual(toasts, [['Another job is running: ', { text: 'Scan', href: '#/activity/j1' }, '.']]);
});

test('after an apply: follow a started job, reopen a busy refusal, release the token otherwise (I1)', async () => {
    const { afterApply } = await backups;
    const refusal = code => ({ refused: new ApiError(409, 'x', code, null, { success: false, code }) });
    assert.equal(afterApply({ job: { id: 'j' } }), 'follow');
    assert.equal(afterApply(refusal('busy')), 'reopen');
    for (const outcome of [refusal('plan-used'), refusal('plan-expired'), refusal('plan-changed'), refusal('storage-changed'),
        { refused: new TypeError('offline') }, { lost: true }, undefined]) {
        assert.equal(afterApply(outcome), 'release');
    }
});

test('a restore job that hands its review back reopens it once in the page that started it; any other end forgets it (I1)', async () => {
    const { rememberRestore, onRestoreHandedBack } = await backups;
    let list = [];
    const listeners = new Set();
    const activity = { jobs: () => list, subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); } };
    const emit = next => { list = next; for (const listener of listeners) listener(); };
    const reopened = [];
    const stop = onRestoreHandedBack(activity, review => reopened.push(review.token));
    rememberRestore('kept', { token: 'k' });
    rememberRestore('done', { token: 'd' });
    rememberRestore('gone', { token: 'g' });
    emit([{ id: 'kept', kind: 'restore', state: 'running', scope: {} }]);
    emit([{ id: 'kept', kind: 'restore', state: 'failed', scope: { failure: 'busy', review_kept: true } },
        { id: 'done', kind: 'restore', state: 'finished', scope: {} },
        { id: 'gone', kind: 'restore', state: 'failed', scope: { failure: 'busy' } }]);
    emit(list);
    assert.deepEqual(reopened, ['k']);
    stop();
    assert.equal(listeners.size, 0);
});

test('small files show KB; a full review store says what to do (N3, I1)', async () => {
    const { snapshotText, reviewRefusal, EXPORTS } = await backups;
    assert.equal(snapshotText({ name: 'a.db', size: 40 * 1024 }), 'a.db · 40 KB');
    assert.equal(snapshotText({ name: 'a.db', size: 10 }), 'a.db · 1 KB');
    assert.equal(snapshotText({ name: 'a.db', size: 3 * 2 ** 20 }), 'a.db · 3.0 MB');
    const full = new ApiError(429, 'Two restores are already being reviewed. Discard one first.', 'too-large', null,
        { success: false, code: 'too-large' });
    assert.equal(reviewRefusal(full), 'Two restores are already being reviewed in another window. Discard one there, ' +
        'or try again when it expires (15 minutes after it was reviewed).');
    // N1: /export is the legacy list of every prompt.
    assert.deepEqual(EXPORTS[1].slice(0, 2), ['Export all prompts JSON', '/prompt_manager/export']);
});
