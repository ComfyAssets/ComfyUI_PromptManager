import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtemp, writeFile, mkdir, readFile, realpath, rm, access } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { once } from 'node:events';
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

