// #249 S6b: a fresh install keeps its library in ComfyUI's system user folder. A pinned CPU ComfyUI starts with Prompt
// Manager and no configuration at all (no config.json, no PROMPT_MANAGER_* paths): the library must be created in
// user/__promptmanager, nothing may be written into the node folder, and ComfyUI's /userdata API must not serve it.
import { execFileSync } from 'node:child_process';
import { access, readdir } from 'node:fs/promises';
import { createServer } from 'node:net';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test, expect } from '../../tools/vendor/node_modules/@playwright/test/index.mjs';
import { launchSharedHost } from '../../tools/vendor/comfyui-host.mjs';
import runtime from '../../tools/vendor/comfyui-runtime.json' with { type: 'json' };

const repositoryRoot = fileURLToPath(new URL('../../', import.meta.url));

/** An unused loopback port chosen by the OS (this host shares no port with the other suites). */
function freePort() {
    return new Promise((resolve, reject) => {
        const probe = createServer();
        probe.once('error', reject);
        probe.listen({ host: '127.0.0.1', port: 0, exclusive: true }, () => {
            const { port } = probe.address();
            probe.close(error => error ? reject(error) : resolve(port));
        });
    });
}

test('a fresh host keeps the library in user/__promptmanager and serves none of it', async ({ playwright }, testInfo) => {
    test.skip(testInfo.project.name !== 'classic-start', 'one fresh host is enough; the renderer does not matter here');
    test.setTimeout(180000);
    const port = await freePort();
    const host = await launchSharedHost({ checkout: process.env.PM_COMFY_ROOT, repository: repositoryRoot,
        python: process.env.PM_TEST_PYTHON || 'python', port, pins: runtime, forwardOutput: false,
        logPath: join(repositoryRoot, 'tools/vendor/test-results/executions/fresh-host.log'), pluginName: 'promptmanager',
        // A fresh install: nothing written, and no inherited path variable may choose the folder.
        preparePlugin: async () => ({ PROMPT_MANAGER_CONFIG_PATH: '', PROMPT_MANAGER_DATA_DIR: '',
            PROMPT_MANAGER_EXTRA_GALLERY_ROOTS: '' }) });
    const request = await playwright.request.newContext({ baseURL: `http://127.0.0.1:${port}/` });
    try {
        await expect.poll(async () => {
            if (host.child.exitCode !== null) throw new Error('The fresh host exited before it answered');
            try { return (await request.get('/system_stats')).ok(); } catch { return false; }
        }, { timeout: 120000 }).toBe(true);
        expect((await (await request.get('/system_stats')).json()).system.comfyui_version).toBe(runtime.serverVersion);
        await expect(await request.get('/prompt_manager/stats')).toBeOK();

        const dataFolder = join(host.stateDirectory, 'user', '__promptmanager');
        await access(join(dataFolder, 'prompts.db'));
        await access(join(dataFolder, 'logs'));
        const { location } = await (await request.get('/prompt_manager/system/library')).json();
        expect(location).toMatchObject({ path: 'user/__promptmanager/prompts.db', source: 'default', movable: false,
            offer: false, problem: null });

        // Nothing but byte code was added to the node folder: no database, config.json, logs or caches.
        const installed = join(host.stateDirectory, 'custom_nodes', 'promptmanager');
        const tracked = new Set(execFileSync('git', ['-C', repositoryRoot, 'ls-files', '-z'], { encoding: 'utf8' })
            .split('\0').filter(Boolean).map(file => file.split('/')[0]));
        const added = (await readdir(installed)).filter(name => !tracked.has(name) && name !== '__pycache__');
        expect(added).toEqual([]);

        // ComfyUI's /userdata serves public users only: the system user's folder is out of reach.
        for (const path of ['/userdata/..%2F__promptmanager%2Fprompts.db', '/api/userdata/..%2F__promptmanager%2Fprompts.db',
            '/userdata/%2E%2E%2F__promptmanager%2Fconfig.json', '/userdata/prompts.db']) {
            const response = await request.get(path);
            expect(response.status(), path).not.toBe(200);
        }
        const listing = await request.get('/userdata?dir=..%2F__promptmanager&recurse=true');
        expect(listing.status()).not.toBe(200);
        const asSystemUser = await request.get('/userdata/prompts.db', { headers: { 'comfy-user': '__promptmanager' } });
        expect(asSystemUser.status()).not.toBe(200);
    } finally {
        try { await request.dispose(); } finally { await host.stop(); }
    }
});
