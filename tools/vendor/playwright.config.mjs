import { defineConfig } from '@playwright/test';
import { reporters } from './test-workers.mjs';

export default defineConfig({
    testDir: './tests',
    outputDir: './test-results/viewer',
    testMatch: '*.spec.mjs',
    fullyParallel: true,
    workers: 2,
    forbidOnly: Boolean(process.env.CI),
    retries: process.env.CI ? 1 : 0,
    reporter: reporters({ name: 'viewer' }),
    use: { browserName: 'chromium', headless: true, trace: 'retain-on-failure' },
    projects: [
        { name: 'desktop', use: { viewport: { width: 1280, height: 800 } } },
        { name: 'touch', use: { viewport: { width: 800, height: 700 }, hasTouch: true, isMobile: true } },
    ],
});
