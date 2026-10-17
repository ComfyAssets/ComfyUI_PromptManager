import { writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { launchComfyHost as launchSharedHost } from '@comfyassets/toolkit/testing/host';
import runtime from './comfyui-runtime.json' with { type: 'json' };

export { launchSharedHost };

const repositoryRoot = fileURLToPath(new URL('../../', import.meta.url));

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

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
    try {
        const host = await launchComfyHost({ checkout: process.env.PM_COMFY_ROOT,
            port: Number(process.env.PM_COMFY_PORT || 8189) });
        const stop = () => { host.stop().catch(error => { console.error(error.message); process.exitCode = 1; }); };
        process.once('SIGINT', stop);
        process.once('SIGTERM', stop);
        const result = await host.finished;
        process.removeListener('SIGINT', stop);
        process.removeListener('SIGTERM', stop);
        if (result.code) console.error(`ComfyUI host exited with status ${result.code}; see tools/vendor/test-results/comfyui-host.log`);
        process.exitCode = result.code;
    } catch (error) { console.error(error.message); process.exitCode = 1; }
}
