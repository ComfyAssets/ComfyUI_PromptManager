import { execFileSync } from 'node:child_process';
import { copyFile, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { createServer } from 'node:net';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { launchSharedHost as launchComfyHost, preparePromptManagerPlugin } from '../../tools/vendor/comfyui-host.mjs';
import runtime from '../../tools/vendor/comfyui-runtime.json' with { type: 'json' };

const repositoryRoot = fileURLToPath(new URL('../../', import.meta.url));

export async function launchExecutionHost({ checkout = process.env.PM_COMFY_ROOT,
    repository = process.env.PM_EXECUTION_REPOSITORY || repositoryRoot,
    python = process.env.PM_TEST_PYTHON || 'python', port = 8196, pins = runtime,
    logPath = join(repositoryRoot, 'tools/vendor/test-results/executions/host.log'), forwardOutput = true } = {}) {
    if (port !== 8196) throw new Error('Execution tests reserve managed loopback port 8196');
    await new Promise((resolve, reject) => {
        const probe = createServer();
        probe.once('error', error => reject(new Error('Execution loopback port 8196 is already in use', { cause: error })));
        probe.listen({ host: '127.0.0.1', port, exclusive: true }, () => probe.close(error => error ? reject(error) : resolve()));
    });
    const fixtureDirectory = await mkdtemp(join(tmpdir(), 'pm-execution-fixture-'));
    let host;
    try {
        await copyFile(new URL('./fixtures/ledger_nodes.py', import.meta.url), join(fixtureDirectory, '__init__.py'));
        execFileSync('git', ['init', '--quiet', fixtureDirectory]);
        execFileSync('git', ['-C', fixtureDirectory, 'add', '__init__.py']);
        host = await launchComfyHost({ checkout, repository, python, port, pins, logPath, forwardOutput,
            pluginName: 'promptmanager', preparePlugin: preparePromptManagerPlugin,
            additionalPlugins: [{ pluginName: 'pm_execution_fixture', repository: fixtureDirectory }],
        });
    } catch (error) {
        await rm(fixtureDirectory, { recursive: true, force: true });
        throw error;
    }
    host.finished.finally(() => rm(fixtureDirectory, { recursive: true, force: true })).catch(() => {});
    return { host, fixtureDirectory, databasePath: join(host.stateDirectory, 'prompts.db'), async dispose() {
        try { await host.stop(); }
        finally { await rm(fixtureDirectory, { recursive: true, force: true }); }
    } };
}
