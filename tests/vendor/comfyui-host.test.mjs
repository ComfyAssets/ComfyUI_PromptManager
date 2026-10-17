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
    execFileSync(python, ['-c', `import sqlite3,sys
c=sqlite3.connect(sys.argv[1])
c.execute('CREATE TABLE execution_runs (run_id TEXT, job_id TEXT, session_id TEXT, outcome TEXT)')
c.executemany('INSERT INTO execution_runs VALUES (?,?,?,?)', [('first',sys.argv[2],sys.argv[3],'succeeded'),('other','30000000-0000-4000-8000-000000000001',sys.argv[3],'failed')])
c.commit()`, database, job, session]);
    const before = await readFile(database);
    assert.deepEqual(await readExecutionRuns(database, job, session), { schemaAvailable: true, rows: [
        { run_id: 'first', job_id: job, session_id: session, outcome: 'succeeded' },
    ] });
    assert.deepEqual((await readExecutionRuns(database, job, '40000000-0000-4000-8000-000000000001')).rows, []);
    assert.deepEqual(await readFile(database), before);
    await assert.rejects(readExecutionRuns(database, "' OR 1=1 --"));
    execFileSync(python, ['-c', `import sqlite3,sys
c=sqlite3.connect(sys.argv[1])
c.executemany('INSERT INTO execution_runs VALUES (?,?,?,?)', [(str(i),sys.argv[2],sys.argv[3],'succeeded') for i in range(256)])
c.commit()`, database, job, session]);
    await assert.rejects(readExecutionRuns(database, job), /bound|256/);
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
