// Shared `test` for the page workflows: every Playwright worker owns one
// tests/browser/server.py process on an OS-chosen port, with its own temp root
// and database. `baseURL` (and so `page`, `request` and `browser.newContext`)
// points at that worker's server. A server that dies fails the running test
// with its stderr tail, and the next test in the worker gets a fresh server.
import { test as base, expect } from '../../tools/vendor/node_modules/@playwright/test/index.mjs';
import { freePort } from '../../tools/vendor/free-port.mjs';
import { createServerSupervisor, startBrowserServer } from '../../tools/vendor/start-browser-server.mjs';

const SERVER_FIXTURE_TIMEOUT_MS = 90_000;

export const test = base.extend({
    testServer: [async ({}, use) => {
        const supervisor = createServerSupervisor({ start: async () => startBrowserServer({ port: await freePort() }) });
        try {
            await use(supervisor);
        } finally {
            await supervisor.stop();
        }
    }, { scope: 'worker', timeout: SERVER_FIXTURE_TIMEOUT_MS }],
    workerServer: [async ({ testServer }, use) => {
        const server = await testServer.ensure();
        await use(server);
        const crash = testServer.crash(server);
        if (crash) throw crash;
    }, { auto: true, timeout: SERVER_FIXTURE_TIMEOUT_MS }],
    baseURL: async ({ workerServer }, use) => use(workerServer.url),
});

export { expect };
