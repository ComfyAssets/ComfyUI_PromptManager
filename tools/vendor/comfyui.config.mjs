import { defineConfig } from './node_modules/@playwright/test/index.mjs';
import { fileURLToPath } from 'node:url';
import { HOST_READY_MARKER } from './comfyui-host.mjs';

if (!process.env.PM_COMFY_ROOT) throw new Error('Set PM_COMFY_ROOT to a pinned disposable ComfyUI checkout');
const host = new URL(process.env.PM_COMFY_URL || 'http://127.0.0.1:8189/');
if (host.protocol !== 'http:' || host.hostname !== '127.0.0.1' || !host.port) throw new Error('A managed host needs an HTTP 127.0.0.1 URL with an explicit port');
const servers = [{
    command: 'node tools/vendor/comfyui-host.mjs',
    cwd: fileURLToPath(new URL('../../', import.meta.url)),
    // No URL wait: any server on the port would satisfy it, including another run's host that
    // then stops mid-test. The launcher refuses a used port and prints this line only once its
    // own ComfyUI answers; the group reaches the tests as PM_COMFY_HOST_BASE.
    wait: { stdout: new RegExp(`^${HOST_READY_MARKER} base-directory=(?<pm_comfy_host_base>.+?)\r?$`, 'm') },
    timeout: 120000, reuseExistingServer: false,
    env: { PM_COMFY_PORT: host.port },
    gracefulShutdown: { signal: 'SIGTERM', timeout: 10000 },
}];

export default defineConfig({
    testDir: '../../tests/comfyui',
    testIgnore: ['execution-history.spec.mjs', 'execution-outputs.spec.mjs', 'execution-text-link.spec.mjs', 'library-location.spec.mjs'],
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
