// System › Overview (#249 System S2 Task 6): the concept's Overview copy on real values (D93), the Health report rows
// (D95), the Statistics tables (D96) and the health check run that waits for its read job.
const test = require('node:test');
const assert = require('node:assert/strict');

const overviewModule = import('../../web/js/workspace/system-overview.mjs');

const OVERVIEW = Object.freeze({
    library: { positive_prompts: 1234, negative_variants: 7 },
    images: { indexed: 24, rated: 20 },
    health: null,
    backup: null,
    needs_review: { pending: null },
    sources: { configured: 2, effective: 2, auto_detected: false },
    thumbnails: { missing: 0 },
});

test('metrics are the server counts and Not checked before any health check', async () => {
    const { metricValues } = await overviewModule;
    assert.deepEqual(metricValues(OVERVIEW), [
        [(1234).toLocaleString(), 'Positive prompts'], ['7', 'Negative variants'], ['24', 'Images'],
        ['Not checked', 'Database integrity'],
    ]);
});

test('metric 4 is the last health result', async () => {
    const { integrityText } = await overviewModule;
    assert.equal(integrityText(null), 'Not checked');
    assert.equal(integrityText({ status: 'passed', issues: 0 }), 'Passed');
    assert.equal(integrityText({ status: 'issues', issues: 1 }), '1 issue');
    assert.equal(integrityText({ status: 'issues', issues: 3 }), '3 issues');
    assert.equal(integrityText({ status: 'incomplete', issues: 0 }), 'Incomplete');
    // Issues found beside a check that could not run (Needs review, Q6) are still named.
    assert.equal(integrityText({ status: 'incomplete', issues: 2 }), '2 issues');
});

test('library health rows never invent a review count, a backup or thumbnails', async () => {
    const { ambiguousTitle, backupText, thumbnailTitle } = await overviewModule;
    assert.equal(ambiguousTitle({ pending: null }), 'Ambiguous image links');
    assert.equal(ambiguousTitle(undefined), 'Ambiguous image links');
    assert.equal(ambiguousTitle({ pending: 1 }), '1 ambiguous image link');
    assert.equal(ambiguousTitle({ pending: 3 }), '3 ambiguous image links');
    assert.equal(backupText(null), 'No retained backup record.');
    assert.equal(backupText({ verified_at: 'not a time' }), 'No retained backup record.');
    assert.match(backupText({ verified_at: '2026-10-22T09:30:00Z' }), /^Last verified backup .+\.$/);
    assert.equal(thumbnailTitle({ missing: 0 }), 'No missing thumbnails');
    assert.equal(thumbnailTitle({ missing: 1 }), '1 missing thumbnail');
    assert.equal(thumbnailTitle({ missing: 12 }), '12 missing thumbnails');
    assert.equal(thumbnailTitle({ missing: null }), 'Thumbnail status unavailable');
});

test('the sources row tells configured, available and detected sources apart', async () => {
    const { sourcesCopy } = await overviewModule;
    const shared = 'Monitoring and enrichment share one source list.';
    assert.deepEqual(sourcesCopy({ configured: 2, effective: 2, auto_detected: false }), ['2 output sources', shared]);
    assert.deepEqual(sourcesCopy({ configured: 2, effective: 1, auto_detected: false }),
        ['1 output source', `1 of 2 configured sources available. ${shared}`]);
    assert.deepEqual(sourcesCopy({ configured: 0, effective: 1, auto_detected: true }),
        ['1 output source', `None configured is available; ComfyUI's output folder is used. ${shared}`]);
});

const REPORT = Object.freeze({
    checked_at: '2026-10-23T08:00:00Z',
    status: 'issues',
    checks: [
        { id: 'integrity', label: 'Integrity check', status: 'passed', count: 0 },
        { id: 'foreign_keys', label: 'Foreign keys & pair references', status: 'issues', count: 2 },
        { id: 'canonical_hashes', label: 'Canonical hashes', status: 'issues', count: 1 },
        { id: 'pair_uniqueness', label: 'Pair uniqueness', status: 'failed', count: null },
        { id: 'missing_files', label: 'Missing files', status: 'issues', count: 2,
            sources: [{ name: 'output', status: 'checked', count: 2 }] },
        { id: 'ambiguous_metadata', label: 'Ambiguous metadata', status: 'unavailable', count: null },
        { id: 'consistency', label: 'Consistency', status: 'issues', count: null },
    ],
});

test('health rows give each check a chip from its real status, in the server order', async () => {
    const { healthRows } = await overviewModule;
    assert.deepEqual(healthRows(REPORT), [
        ['Integrity check', 'Passed'],
        ['Foreign keys & pair references', '2 broken references'],
        ['Canonical hashes', '1 hash does not match its text'],
        ['Pair uniqueness', 'Could not check'],
        ['Missing files', '2 links need review'],
        ['Ambiguous metadata', 'Not available yet'],
        ['Consistency', 'Issues found'],
    ]);
});

test('missing files is reported per image source when there are several', async () => {
    const { healthRows } = await overviewModule;
    const report = { ...REPORT, checks: [{ id: 'missing_files', label: 'Missing files', status: 'issues', count: 1, partial: true,
        sources: [{ name: 'output', status: 'checked', count: 1 }, { name: 'input', status: 'checked', count: 0 },
            { name: 'archive', status: 'unavailable', count: null }] }] };
    assert.deepEqual(healthRows(report), [
        ['Missing files · output', '1 link needs review'],
        ['Missing files · input', 'Passed'],
        ['Missing files · archive', 'Not available'],
    ]);
    const unavailable = { ...REPORT, checks: [{ id: 'missing_files', label: 'Missing files', status: 'unavailable', count: null, sources: [] }] };
    assert.deepEqual(healthRows(unavailable), [['Missing files', 'Not available']]);
});

test('the report summary says when it was checked and whether it is complete', async () => {
    const { healthSummary } = await overviewModule;
    const when = new Date(REPORT.checked_at).toLocaleString();
    assert.equal(healthSummary({ ...REPORT, status: 'passed' }), `Checked ${when}. Every check passed.`);
    assert.equal(healthSummary({ ...REPORT, status: 'incomplete' }), `Checked ${when}. Some checks could not run, so this report is incomplete.`);
    assert.equal(healthSummary({ ...REPORT, checked_at: 'x' }), 'Nothing was changed; review the checks with issues.');
});

test('statistics rows are the real runs and ratings, then the library totals', async () => {
    const { categoryRows, totalsRows } = await overviewModule;
    const statistics = {
        by_category: [{ category: 'Landscapes', runs: 1200, average_rating: 4.5 },
            { category: null, runs: 3, average_rating: null }],
        totals: { total_prompts: 12, total_tags: 15, total_images: 24 },
        rated_images: 20,
    };
    assert.deepEqual(categoryRows(statistics), [['Landscapes', (1200).toLocaleString(), '4.5 / 5'], ['Uncategorized', '3', 'Not rated']]);
    assert.deepEqual(totalsRows(statistics), [['Prompts', '12'], ['Tags', '15'], ['Images', '24'], ['Rated images', '20']]);
});

/** A fake api answering each `get`/`post` from a queue per URL, recording the calls. */
function fakeApi(answers) {
    const calls = [];
    const answer = (method, url) => {
        calls.push(`${method} ${url}`);
        const queue = answers[`${method} ${url}`];
        if (!queue?.length) throw new Error(`unexpected ${method} ${url}`);
        const next = queue.shift();
        return next instanceof Error ? Promise.reject(next) : Promise.resolve(next);
    };
    return { calls, get: url => answer('GET', url), post: url => answer('POST', url) };
}

const immediate = { setTimeout: callback => { queueMicrotask(callback); return 0; }, clearTimeout() {} };
const JOB = '0123456789abcdef0123456789abcdef';

test('a health check waits for its read job, then reads the report', async () => {
    const { runHealthCheck } = await overviewModule;
    const api = fakeApi({
        'POST /prompt_manager/system/health': [{ job: { id: JOB } }],
        [`GET /prompt_manager/jobs/${JOB}`]: [{ job: { state: 'running' } }, { job: { state: 'finished' } }],
        'GET /prompt_manager/system/health': [{ report: REPORT }],
    });
    assert.equal(await runHealthCheck(api, { timers: immediate }), REPORT);
    assert.deepEqual(api.calls, ['POST /prompt_manager/system/health', `GET /prompt_manager/jobs/${JOB}`,
        `GET /prompt_manager/jobs/${JOB}`, 'GET /prompt_manager/system/health']);
});

test('a check already running elsewhere is waited for, not refused', async () => {
    const { runHealthCheck } = await overviewModule;
    const busy = Object.assign(new Error('busy'), { status: 409, code: 'busy' });
    const api = fakeApi({
        'POST /prompt_manager/system/health': [busy],
        'GET /prompt_manager/jobs': [{ jobs: [{ id: 'f'.repeat(32), kind: 'scan', state: 'running' }, { id: JOB, kind: 'health', state: 'running' }] }],
        [`GET /prompt_manager/jobs/${JOB}`]: [{ job: { state: 'finished' } }],
        'GET /prompt_manager/system/health': [{ report: REPORT }],
    });
    assert.equal(await runHealthCheck(api, { timers: immediate }), REPORT);
    assert.ok(api.calls.includes(`GET /prompt_manager/jobs/${JOB}`));
});

test('a failed, lost or reportless check rejects; other errors pass through', async () => {
    const { runHealthCheck, waitForJob, HEALTH_FAILED_TEXT } = await overviewModule;
    const failed = fakeApi({ 'POST /prompt_manager/system/health': [{ job: { id: JOB } }],
        [`GET /prompt_manager/jobs/${JOB}`]: [{ job: { state: 'failed' } }] });
    await assert.rejects(runHealthCheck(failed, { timers: immediate }), { message: HEALTH_FAILED_TEXT });
    const lost = fakeApi({ [`GET /prompt_manager/jobs/${JOB}`]: [Object.assign(new Error('gone'), { status: 404 })] });
    assert.equal(await waitForJob(lost, JOB, { timers: immediate }), 'lost');
    const empty = fakeApi({ 'POST /prompt_manager/system/health': [{ job: { id: JOB } }],
        [`GET /prompt_manager/jobs/${JOB}`]: [{ job: { state: 'finished' } }], 'GET /prompt_manager/system/health': [{ report: null }] });
    await assert.rejects(runHealthCheck(empty, { timers: immediate }), { message: HEALTH_FAILED_TEXT });
    const broken = fakeApi({ 'POST /prompt_manager/system/health': [Object.assign(new Error('server'), { status: 500 })] });
    await assert.rejects(runHealthCheck(broken, { timers: immediate }), { message: 'server' });
});

test('waiting stops when its signal aborts', async () => {
    const { waitForJob } = await overviewModule;
    const controller = new AbortController();
    const api = fakeApi({ [`GET /prompt_manager/jobs/${JOB}`]: [{ job: { state: 'running' } }] });
    const never = { setTimeout: () => 1, clearTimeout() {} };
    const waiting = waitForJob(api, JOB, { signal: controller.signal, timers: never });
    await new Promise(resolve => setImmediate(resolve));
    controller.abort(new Error('left'));
    await assert.rejects(waiting, { message: 'left' });
});
