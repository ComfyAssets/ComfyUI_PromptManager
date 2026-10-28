import { defineConfig } from '@playwright/test';
import { globalTimeoutMs, reporters } from './test-workers.mjs';

// The viewer suite takes under a minute in CI and runs before the coverage workflows in the same step.
const CI_GLOBAL_TIMEOUT_MINUTES = 3;

export default defineConfig({
    testDir: './tests',
    outputDir: './test-results/viewer',
    testMatch: '*.spec.mjs',
    fullyParallel: true,
    workers: 2,
    forbidOnly: Boolean(process.env.CI),
    retries: process.env.CI ? 1 : 0,
    globalTimeout: globalTimeoutMs({ ciMinutes: CI_GLOBAL_TIMEOUT_MINUTES }),
    reporter: reporters({ name: 'viewer' }),
    use: { browserName: 'chromium', headless: true, trace: 'retain-on-failure' },
    projects: [
        { name: 'desktop', use: { viewport: { width: 1280, height: 800 } } },
        { name: 'touch', use: { viewport: { width: 800, height: 700 }, hasTouch: true, isMobile: true } },
    ],
});
