import { defineConfig, devices } from '@playwright/test';
import { reporters, workflowWorkers } from './test-workers.mjs';

// No global webServer: tests/browser/fixtures.mjs starts one server per worker
// on an OS-chosen port. Files run in parallel; tests inside a file keep their order.
export default defineConfig({
    testDir: '../../tests/browser',
    outputDir: './test-results/workflows',
    testMatch: '*.spec.mjs',
    workers: workflowWorkers(),
    fullyParallel: false,
    forbidOnly: Boolean(process.env.CI),
    retries: 0,
    reporter: reporters({ name: 'workflows' }),
    use: { trace: 'retain-on-failure', screenshot: 'only-on-failure' },
    projects: [
        { name: 'chromium', outputDir: './test-results/workflows/chromium', use: { browserName: 'chromium' } },
        { name: 'firefox', outputDir: './test-results/workflows/firefox', use: { browserName: 'firefox' } },
        { name: 'webkit', outputDir: './test-results/workflows/webkit', use: { browserName: 'webkit' } },
        { name: 'touch', outputDir: './test-results/workflows/touch', use: { ...devices['Pixel 7'], browserName: 'chromium' } },
    ],
});
