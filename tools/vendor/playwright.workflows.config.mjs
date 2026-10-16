import { defineConfig, devices } from '@playwright/test';
import { repositoryRoot } from './vendor-viewerjs.mjs';
const port = Number(process.env.PM_TEST_PORT || 8765);
export default defineConfig({
    testDir: '../../tests/browser',
    outputDir: './test-results/workflows',
    testMatch: '*.spec.mjs',
    workers: 1,
    fullyParallel: false,
    forbidOnly: Boolean(process.env.CI),
    retries: 0,
    reporter: 'list',
    use: { baseURL: `http://127.0.0.1:${port}`, trace: 'retain-on-failure', screenshot: 'only-on-failure' },
    webServer: {
        command: `node tools/vendor/start-browser-server.mjs --port ${port}`,
        cwd: repositoryRoot,
        url: `http://127.0.0.1:${port}/prompt_manager/test`,
        reuseExistingServer: false,
        gracefulShutdown: { signal: 'SIGTERM', timeout: 5000 },
    },
    projects: [
        { name: 'chromium', use: { browserName: 'chromium' } },
        { name: 'firefox', use: { browserName: 'firefox' } },
        { name: 'webkit', use: { browserName: 'webkit' } },
        { name: 'touch', use: { ...devices['Pixel 7'], browserName: 'chromium' } },
    ],
});
