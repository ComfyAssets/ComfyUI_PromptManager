import { defineConfig } from './node_modules/@playwright/test/index.mjs';

if (!process.env.PM_COMFY_ROOT) throw new Error('Set PM_COMFY_ROOT to a pinned disposable ComfyUI checkout');
const host = new URL(process.env.PM_EXECUTION_URL || 'http://127.0.0.1:8196/');
if (host.href !== 'http://127.0.0.1:8196/') throw new Error('Execution tests require managed loopback http://127.0.0.1:8196/');

export default defineConfig({
    testDir: '../../tests/comfyui',
    testMatch: 'execution-history.spec.mjs',
    outputDir: './test-results/executions/cases',
    timeout: 60000,
    workers: 1,
    forbidOnly: Boolean(process.env.CI),
    retries: 0,
    use: { baseURL: host.href, browserName: 'chromium', viewport: { width: 1600, height: 1100 },
        trace: 'retain-on-failure', screenshot: 'only-on-failure' },
    projects: [
        { name: 'classic-start', metadata: { initialRenderer: false } },
        { name: 'nodes2-start', metadata: { initialRenderer: true } },
    ],
});
