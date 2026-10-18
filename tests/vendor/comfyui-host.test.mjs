import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtemp, writeFile, mkdir, readFile, realpath, rm, access, readdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { once } from 'node:events';
import { createServer } from 'node:net';
import { launchComfyHost } from '../../tools/vendor/comfyui-host.mjs';

async function fixture(t, program) {
    const directory = await mkdtemp(join(tmpdir(), 'pm-host-test-'));
    t.after(() => rm(directory, { recursive: true, force: true }));
    const checkout = join(directory, 'host');
    const repository = join(directory, 'plugin');
    await mkdir(checkout);
    await mkdir(repository);
    await writeFile(join(repository, '__init__.py'), '');
    execFileSync('git', ['init', '--quiet', repository]);
    execFileSync('git', ['-C', repository, 'add', '__init__.py']);
    await writeFile(join(repository, 'config.json'), 'private-user-config');
    await writeFile(join(repository, 'prompts.db'), 'private-user-database');
    await writeFile(join(checkout, 'main.py'), program);
    const git = args => execFileSync('git', ['-C', checkout, ...args], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
    git(['init', '--quiet']);
    git(['add', 'main.py']);
    git(['-c', 'user.name=PromptManager Tests', '-c', 'user.email=tests@example.invalid', 'commit', '--quiet', '-m', 'fixture']);
    return { checkout, repository, python: process.execPath, port: 8199,
        pins: { revision: git(['rev-parse', 'HEAD']) }, logPath: join(directory, 'host.log'), forwardOutput: false };
}

const idle = `console.log('ready'); setInterval(() => {}, 1000);`;

test('PromptManager adapter isolates database, configuration and monitored directories', async t => {
    const options = await fixture(t, idle);
    const host = await launchComfyHost(options);
    t.after(() => host.stop());
    await once(host.child.stdout, 'data');
    const args = host.child.spawnargs;
    for (const flag of ['--cpu', '--offline', '--disable-all-custom-nodes']) assert.ok(args.includes(flag));
    assert.equal(args[args.indexOf('--base-directory') + 1], host.stateDirectory);
    assert.equal(args[args.indexOf('--listen') + 1], '127.0.0.1');
    assert.equal(args[args.indexOf('--whitelist-custom-nodes') + 1], 'promptmanager');
    const installed = join(host.stateDirectory, 'custom_nodes/promptmanager');
    assert.notEqual(await realpath(installed), await realpath(options.repository));
    assert.equal(await readFile(join(installed, '__init__.py'), 'utf8'), '');
    await assert.rejects(access(join(installed, 'prompts.db')), { code: 'ENOENT' });
    assert.equal(JSON.parse(await readFile(join(installed, 'config.json'), 'utf8')).database.default_path, join(host.stateDirectory, 'prompts.db'));
    await writeFile(join(installed, 'prompts.db'), 'test data');
    assert.equal(await readFile(join(options.repository, 'prompts.db'), 'utf8'), 'private-user-database');
    assert.equal(await readFile(join(options.repository, 'config.json'), 'utf8'), 'private-user-config');
    const config = JSON.parse(await readFile(join(installed, 'config.json'), 'utf8'));
    assert.equal(config.gallery.monitoring.enabled, false);
    assert.deepEqual(config.gallery.monitoring.directories, [join(host.stateDirectory, 'output')]);
    assert.equal(config.web_ui.result_timeout, 0);
    await access(join(host.stateDirectory, 'user'));
    await host.stop();
    await assert.rejects(access(host.stateDirectory), { code: 'ENOENT' });
    await access(join(options.checkout, 'main.py'));
    await access(join(options.repository, '__init__.py'));
});


test('execution host adds only the tracked fixture pack and removes all owned state', async t => {
    const { launchExecutionHost } = await import('../comfyui/execution-host.mjs');
    const options = await fixture(t, idle);
    const execution = await launchExecutionHost({ ...options, port: 8196 });
    t.after(() => execution.dispose());
    await once(execution.host.child.stdout, 'data');
    const args = execution.host.child.spawnargs;
    assert.deepEqual(args.slice(args.indexOf('--whitelist-custom-nodes') + 1), ['promptmanager', 'pm_execution_fixture']);
    assert.equal(execution.databasePath, join(execution.host.stateDirectory, 'prompts.db'));
    const installed = join(execution.host.stateDirectory, 'custom_nodes/pm_execution_fixture');
    assert.match(await readFile(join(installed, '__init__.py'), 'utf8'), /PMHistoryCached/);
    assert.deepEqual(await readdir(installed), ['__init__.py']);
    const source = execution.fixtureDirectory;
    assert.notEqual(await realpath(source), await realpath(options.repository));
    await execution.dispose();
    await assert.rejects(access(execution.host.stateDirectory), { code: 'ENOENT' });
    await assert.rejects(access(source), { code: 'ENOENT' });
    await access(join(options.repository, '__init__.py'));
});

test('read-only ledger helper distinguishes absent schema from missing, corrupt and oversized data', async t => {
    const { readExecutionRuns } = await import('../comfyui/execution-fixture.mjs');
    const directory = await mkdtemp(join(tmpdir(), 'pm-ledger-read-'));
    t.after(() => rm(directory, { recursive: true, force: true }));
    const database = join(directory, 'prompts.db');
    const python = process.env.PM_TEST_PYTHON || 'python';
    const job = '10000000-0000-4000-8000-000000000001';
    const session = '20000000-0000-4000-8000-000000000001';
    execFileSync(python, ['-c', 'import sqlite3,sys; sqlite3.connect(sys.argv[1]).close()', database]);
    assert.deepEqual(await readExecutionRuns(database, job), { schemaAvailable: false, rows: [] });
    const runId = '50000000-0000-4000-8000-000000000001';
    const otherSession = '40000000-0000-4000-8000-000000000001';
    const epoch = execFileSync(python, ['-c', `import sys
from database.operations import PromptDatabase
from utils.execution_history_events import RunStart, make_finish
from utils.execution_contexts import ContextBundle, PromptSnapshot, RecipeContext, TextEvidence
from types import SimpleNamespace

db = PromptDatabase(sys.argv[1])
try:
    epoch = db.get_execution_epoch()
    c = db.model.get_connection()
    with c:
        c.executemany("INSERT INTO prompts(id,text,run_count) VALUES(?,?,?)",
                      [(7, ' positive ', 19), (8, 'negative', 23)])
        c.execute("INSERT INTO prompt_pairs(id,positive_prompt_id,negative_prompt_id,run_count) VALUES(2,7,8,31)")
    for index, (job, session) in enumerate([
        (sys.argv[2], sys.argv[3]),
        ('30000000-0000-4000-8000-000000000001', sys.argv[3]),
        (sys.argv[2], sys.argv[4]),
    ], 1):
        run_id = '50000000-0000-4000-8000-%012d' % index
        db.write_execution_start(RunStart(run_id, session, index, job, epoch, 100))
        db.write_execution_finish(make_finish(session, index, epoch,
            SimpleNamespace(messages=[('execution_success', {'prompt_id': job})]), 200))
    def snapshot(raw):
        def known(value):
            return TextEvidence(value, 'recorded', 'runtime_return', None)
        return PromptSnapshot(known(raw), known(raw), known(''), known(''))
    db.write_execution_contexts(sys.argv[5], epoch, ContextBundle(
        (RecipeContext('4', snapshot(' positive '), snapshot('negative'), 'inferred'),),
        'complete', 'captured'))
    print(epoch)
finally:
    db.close_all()
`, database, job, session, otherSession, runId], { cwd: new URL('../../', import.meta.url), encoding: 'utf8' }).trim();
    const before = await readFile(database);
    const result = await readExecutionRuns(database, job, session);
    assert.equal(result.schemaAvailable, true);
    assert.deepEqual(result.rows, [{
        run_id: runId, job_id: job, session_id: session, queue_item_id: 1,
        storage_epoch: epoch, record_version: 1, observed_dequeue_at: 100,
        observed_callback_at: 200, executor_started_at: null, terminal_event_at: null,
        outcome: 'succeeded', reason: 'terminal_success', diagnostics_omitted: 0,
        node_id: null, node_type: null, exception_type: null,
    }]);
    assert.deepEqual(Object.keys(result.captures), [runId]);
    const capture = result.captures[runId];
    assert.equal(capture.contexts.length, 1);
    const contextId = capture.contexts[0].context_id;
    assert.match(contextId, /^[a-f0-9]{64}$/);
    const recorded = { provenance: 'recorded', source: 'runtime_return', reason: null };
    const evidence = { raw: recorded, effective: recorded, prepend: recorded, append: recorded };
    assert.deepEqual(capture, {
        state: 'complete', reason: 'captured', format_version: 1, contexts: [{
            anchor_id: '4', context_id: contextId,
            positive: { raw: ' positive ', effective: ' positive ', prepend: '', append: '' },
            negative: { raw: 'negative', effective: 'negative', prepend: '', append: '' },
            evidence: { format_version: 1, role_provenance: 'inferred', positive: evidence, negative: evidence },
            positive_prompt_id: 7, negative_prompt_id: 8, pair_id: 2, reference_epoch: epoch,
        }],
    });
    assert.deepEqual(result.usage, { [runId]: [
        { role: 'positive', run_count: 19 }, { role: 'negative', run_count: 23 }, { role: 'pair', run_count: 31 },
    ] });
    assert.deepEqual(Object.keys(result.snapshotIds), [runId]);
    assert.equal(result.snapshotIds[runId].length, 1);
    assert.deepEqual(Object.keys(result.snapshotIds[runId][0]).sort(), ['negative_snapshot_id', 'positive_snapshot_id']);
    const { positive_snapshot_id: positiveId, negative_snapshot_id: negativeId } = result.snapshotIds[runId][0];
    assert.equal(positiveId, 'd2d716108f66314e127fe87bb81327426d2a148e341a8ca1f7cf6a0b0df2ce8c');
    assert.equal(negativeId, '07db4923fea7f8d00d1a94a902e0b85137471178b5e7bb67ad664ef3370770ed');
    const unfiltered = await readExecutionRuns(database, job);
    assert.deepEqual(unfiltered.rows.map(row => row.session_id), [session, otherSession]);
    const uncapturedId = '50000000-0000-4000-8000-000000000003';
    assert.deepEqual(unfiltered.captures[uncapturedId], {
        state: 'not_captured', reason: 'missing', format_version: null, contexts: [],
    });
    assert.deepEqual(unfiltered.usage[uncapturedId], []);
    assert.deepEqual(unfiltered.snapshotIds[uncapturedId], []);
    assert.deepEqual(await readExecutionRuns(database, job, '60000000-0000-4000-8000-000000000001'), {
        schemaAvailable: true, rows: [], captures: {}, usage: {}, snapshotIds: {},
    });
    assert.deepEqual(await readFile(database), before);
    await assert.rejects(readExecutionRuns(database, "' OR 1=1 --"));
    await assert.rejects(readExecutionRuns(database, job, "' OR 1=1 --"));
    const addRuns = (start, stop) => execFileSync(python, ['-c', `import sys
from database.operations import PromptDatabase
from utils.execution_history_events import RunStart

db = PromptDatabase(sys.argv[1])
try:
    epoch = db.get_execution_epoch()
    for index in range(int(sys.argv[4]), int(sys.argv[5])):
        run_id = '70000000-0000-4000-8000-%012d' % index
        db.write_execution_start(RunStart(run_id, sys.argv[3], index, sys.argv[2], epoch, 100))
finally:
    db.close_all()
`, database, job, session, String(start), String(stop)], { cwd: new URL('../../', import.meta.url) });
    addRuns(4, 258);
    assert.equal((await readExecutionRuns(database, job)).rows.length, 256);
    addRuns(258, 259);
    await assert.rejects(readExecutionRuns(database, job), /bound|256/);
    execFileSync(python, ['-c', `import sqlite3,sys
with sqlite3.connect(sys.argv[1]) as c:
    c.execute('DROP TABLE execution_context_capture')
`, database]);
    await assert.rejects(readExecutionRuns(database, job, otherSession), /no such table: execution_context_capture/);
    await assert.rejects(readExecutionRuns(join(directory, 'missing.db'), job));
    await writeFile(database, 'corrupt database');
    await assert.rejects(readExecutionRuns(database, job));
});


test('execution launcher refuses an occupied port without touching an existing server', async t => {
    const { launchExecutionHost } = await import('../comfyui/execution-host.mjs');
    const options = await fixture(t, idle);
    const server = createServer();
    await new Promise((resolve, reject) => { server.once('error', reject); server.listen(8196, '127.0.0.1', resolve); });
    let execution;
    try {
        await assert.rejects(async () => { execution = await launchExecutionHost({ ...options, port: 8196 }); }, /already in use/);
        assert.equal(server.listening, true);
    } finally {
        await execution?.dispose();
        await new Promise(resolve => server.close(resolve));
    }
});
