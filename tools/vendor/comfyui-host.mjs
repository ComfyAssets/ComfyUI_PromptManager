import { writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import { join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { launchComfyHost as launchSharedHost } from '@comfyassets/toolkit/testing/host';
import runtime from './comfyui-runtime.json' with { type: 'json' };

export { launchSharedHost };

const repositoryRoot = fileURLToPath(new URL('../../', import.meta.url));
// comfyui.config.mjs waits for this line; its named group becomes PM_COMFY_HOST_BASE in the test workers.
export const HOST_READY_MARKER = 'PM_COMFY_HOST_READY';
const READY_POLL_MS = 250;

export async function preparePromptManagerPlugin({ installed, stateDirectory }) {
    const configPath = join(installed, 'config.json');
    await writeFile(configPath, JSON.stringify({
        database: { default_path: join(stateDirectory, 'prompts.db') },
        gallery: { monitoring: { enabled: false, directories: [join(stateDirectory, 'output')] } },
        web_ui: { result_timeout: 0 },
    }), { mode: 0o600 });
    return { PROMPT_MANAGER_CONFIG_PATH: configPath, PROMPT_MANAGER_EXTRA_GALLERY_ROOTS: '' };
}

export function launchComfyHost({ checkout, repository = repositoryRoot,
    python = process.env.PM_TEST_PYTHON || 'python', port = 8189, pins = runtime,
    logPath = join(repositoryRoot, 'tools/vendor/test-results/comfyui-host.log'), forwardOutput = true }) {
    return launchSharedHost({ checkout, repository, python, port, pins, logPath, forwardOutput,
        pluginName: 'promptmanager',
        preparePlugin: preparePromptManagerPlugin,
    });
}

export async function assertPortFree(port) {
    await new Promise((resolve, reject) => {
        const probe = createServer();
        probe.once('error', error => reject(new Error(`ComfyUI host port ${port} is already in use, often by another `
            + 'real-host run. A managed host is never shared: stop that run or set PM_COMFY_URL to a free '
            + 'http://127.0.0.1:PORT/ address.', { cause: error })));
        probe.listen({ host: '127.0.0.1', port, exclusive: true }, () => probe.close(error => error ? reject(error) : resolve()));
    });
}

export function hostBaseDirectory(stats) {
    const argv = stats?.system?.argv;
    if (!Array.isArray(argv)) return null;
    const index = argv.indexOf('--base-directory');
    return index >= 0 ? argv[index + 1] ?? null : null;
}

// Ready means this launcher's own ComfyUI answers: /system_stats reports its base directory.
// Any other answer on the port comes from a host this run did not start, so the run must not use it.
export async function waitForOwnHost(host, port, { fetchImpl = fetch } = {}) {
    let exited = null;
    host.finished.then(result => { exited = result; });
    const url = `http://127.0.0.1:${port}/system_stats`;
    for (;;) {
        let stats = null;
        try { stats = await (await fetchImpl(url)).json(); }
        catch { stats = null; }
        if (stats) {
            const base = hostBaseDirectory(stats);
            if (base === host.stateDirectory) return;
            throw new Error(`Port ${port} is answered by a ComfyUI host this run did not start `
                + `(base directory ${base ?? 'unknown'}); another run is using the port.`);
        }
        if (exited) {
            throw new Error(`ComfyUI host exited before it answered on port ${port} `
                + `(exit=${exited.code}, signal=${exited.signal || 'none'}).`);
        }
        await new Promise(resolve => setTimeout(resolve, READY_POLL_MS));
    }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
    try {
        const port = Number(process.env.PM_COMFY_PORT || 8189);
        await assertPortFree(port);
        const host = await launchComfyHost({ checkout: process.env.PM_COMFY_ROOT, port });
        const stop = () => { host.stop().catch(error => { console.error(error.message); process.exitCode = 1; }); };
        process.once('SIGINT', stop);
        process.once('SIGTERM', stop);
        let refused = false;
        try {
            await waitForOwnHost(host, port);
            console.log(`${HOST_READY_MARKER} base-directory=${host.stateDirectory}`);
        } catch (error) {
            refused = true;
            console.error(error.message);
            await host.stop();
        }
        const result = await host.finished;
        process.removeListener('SIGINT', stop);
        process.removeListener('SIGTERM', stop);
        if (result.code) console.error(`ComfyUI host exited with status ${result.code}; see tools/vendor/test-results/comfyui-host.log`);
        process.exitCode = refused ? 1 : result.code;
    } catch (error) { console.error(error.message); process.exitCode = 1; }
}
