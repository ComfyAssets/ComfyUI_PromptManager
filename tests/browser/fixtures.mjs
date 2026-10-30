// Shared `test` for the page workflows: every Playwright worker owns one
// tests/browser/server.py process on a port the OS chose for it, with its own temp root
// and database. `baseURL` (and so `page`, `request` and `browser.newContext`)
// points at that worker's server. A server that dies fails the running test
// with its stderr tail, and the next test in the worker gets a fresh server.
// A test that fails gets the stderr its server wrote during it attached as
// `server-stderr`. The worker also records its browser's own log (#513): a
// browser that exits abnormally prints one `pm-browser-exit|` line, and a test
// that fails on a closed or crashed target gets that log attached as
// `browser-stderr`.
// The one-time older-images notice (#527) shows above every workspace page and as
// a modal on the admin page until dismissed, so every context starts with it
// dismissed. A spec about the notice sets `test.use({ olderImagesNotice: true })`.
import { test as base, expect } from '../../tools/vendor/node_modules/@playwright/test/index.mjs';
import { attachServerStderr, createServerSupervisor, startBrowserServer } from '../../tools/vendor/start-browser-server.mjs';
import { attachBrowserLogs, createBrowserLogRecorder } from '../../tools/vendor/browser-logs.mjs';

const SERVER_FIXTURE_TIMEOUT_MS = 90_000;

export const test = base.extend({
    browserLogs: [async ({}, use) => {
        const recorder = createBrowserLogRecorder();
        try {
            await use(recorder);
        } finally {
            recorder.uninstall();
        }
    }, { scope: 'worker', auto: true }],
    browserLogsOnFailure: [async ({ browserLogs }, use, testInfo) => {
        await use();
        await attachBrowserLogs(testInfo, browserLogs);
    }, { auto: true }],
    testServer: [async ({}, use) => {
        const supervisor = createServerSupervisor({ start: () => startBrowserServer() });
        try {
            await use(supervisor);
        } finally {
            await supervisor.stop();
        }
    }, { scope: 'worker', timeout: SERVER_FIXTURE_TIMEOUT_MS }],
    workerServer: [async ({ testServer }, use, testInfo) => {
        const server = await testServer.ensure();
        const mark = server.stderrMark();
        await use(server);
        await attachServerStderr(testInfo, server, mark);
        const crash = testServer.crash(server);
        if (crash) throw crash;
    }, { auto: true, timeout: SERVER_FIXTURE_TIMEOUT_MS }],
    baseURL: async ({ workerServer }, use) => use(workerServer.url),
    olderImagesNotice: [false, { option: true }],
    storageState: async ({ baseURL, olderImagesNotice }, use) => use(olderImagesNotice ? undefined : {
        cookies: [],
        origins: [{ origin: new URL(baseURL).origin, localStorage: [{ name: 'pm_update_notice_dismissed', value: '3.2.0' }] }],
    }),
});

export { expect };
