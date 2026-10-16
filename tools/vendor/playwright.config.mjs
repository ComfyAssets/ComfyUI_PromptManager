import { defineConfig } from '@playwright/test';

export default defineConfig({
    testDir: './tests',
    testMatch: '*.spec.mjs',
    fullyParallel: true,
    workers: 2,
    forbidOnly: Boolean(process.env.CI),
    retries: process.env.CI ? 1 : 0,
    reporter: 'list',
    use: { browserName: 'chromium', headless: true, trace: 'retain-on-failure' },
    projects: [
        { name: 'desktop', use: { viewport: { width: 1280, height: 800 } } },
        { name: 'touch', use: { viewport: { width: 800, height: 700 }, hasTouch: true, isMobile: true } },
    ],
});
