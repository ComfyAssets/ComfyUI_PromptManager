import { defineConfig } from './node_modules/@playwright/test/index.mjs';
import { fileURLToPath } from 'node:url';

if (!process.env.PM_COMFY_ROOT) throw new Error('Set PM_COMFY_ROOT to a pinned disposable ComfyUI checkout');
const host = new URL(process.env.PM_COMFY_URL || 'http://127.0.0.1:8189/');
if (host.protocol !== 'http:' || host.hostname !== '127.0.0.1' || !host.port) throw new Error('A managed host needs an HTTP 127.0.0.1 URL with an explicit port');
const servers = [{
    command: 'node tools/vendor/comfyui-host.mjs',
    cwd: fileURLToPath(new URL('../../', import.meta.url)),
    url: new URL('system_stats', host).href, timeout: 120000, reuseExistingServer: false,
    env: { PM_COMFY_PORT: host.port },
    gracefulShutdown: { signal: 'SIGTERM', timeout: 10000 },
}];

export default defineConfig({
    testDir: '../../tests/comfyui',
    testIgnore: 'execution-history.spec.mjs',
    outputDir: './test-results/comfyui',
    timeout: 60000,
    workers: 1,
    forbidOnly: Boolean(process.env.CI),
    retries: 0,
    use: { baseURL: host.href, browserName: 'chromium', viewport: { width: 1600, height: 1100 }, trace: 'retain-on-failure', screenshot: 'only-on-failure' },
    webServer: servers,
    projects: [
        { name: 'classic-start', metadata: { initialRenderer: false } },
        { name: 'nodes2-start', metadata: { initialRenderer: true } },
    ],
});
