import { defineConfig, devices } from '@playwright/test';
import { globalTimeoutMs, reporters, workflowWorkers } from './test-workers.mjs';

// CI default; each browser job in viewerjs-check.yml sets PM_TEST_GLOBAL_TIMEOUT_MINUTES below its
// test step's timeout-minutes (tests/vendor/ci-workflow.test.mjs checks the order).
const CI_GLOBAL_TIMEOUT_MINUTES = 17;

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
    // Each test keeps Playwright's 30 s default; the whole run is bounded under CI (#425).
    globalTimeout: globalTimeoutMs({ ciMinutes: CI_GLOBAL_TIMEOUT_MINUTES, variable: 'PM_TEST_GLOBAL_TIMEOUT_MINUTES' }),
    reporter: reporters({ name: 'workflows' }),
    use: { trace: 'retain-on-failure', screenshot: 'only-on-failure' },
    projects: [
        { name: 'chromium', outputDir: './test-results/workflows/chromium', use: { browserName: 'chromium' } },
        { name: 'firefox', outputDir: './test-results/workflows/firefox', use: { browserName: 'firefox' } },
        { name: 'webkit', outputDir: './test-results/workflows/webkit', use: { browserName: 'webkit' } },
        { name: 'touch', outputDir: './test-results/workflows/touch', use: { ...devices['Pixel 7'], browserName: 'chromium' } },
    ],
});
