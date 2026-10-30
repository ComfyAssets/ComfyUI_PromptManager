// #513: a worker records its browser's own log, so a browser that dies between two tests leaves its exit and stderr
// behind instead of only "Target page, context or browser has been closed" on the next test.
import { test, expect } from './fixtures.mjs';
import { isTargetGone } from '../../tools/vendor/browser-logs.mjs';

test('the worker records the launch of the browser its tests use', async ({ browser, browserLogs }) => {
    expect(browser.isConnected()).toBe(true);
    expect(browserLogs.launches().length).toBeGreaterThan(0);
});

test('a killed browser leaves its signal and stderr in the worker log', async ({ playwright, browserName, browserLogs }) => {
    const before = browserLogs.launches();
    const victim = await playwright[browserName].launch();
    const pid = browserLogs.launches().find(item => !before.includes(item));
    expect(pid, 'the new browser\'s launch was recorded').toBeGreaterThan(0);
    const disconnected = new Promise(resolve => victim.once('disconnected', resolve));
    // Playwright starts a browser in a process group of its own; WebKit's launched pid is a wrapper script.
    process.kill(-pid, 'SIGKILL');
    await disconnected;
    const error = await victim.newContext().then(() => null, item => item);
    expect(error, 'a dead browser refuses a new context').not.toBeNull();
    expect(isTargetGone(error.message)).toBe(true);
    // The pipe can close before the launcher has logged the exit.
    await expect.poll(() => browserLogs.exits()).toContainEqual({ pid, exitCode: null, signal: 'SIGKILL' });
    expect(browserLogs.tail()).toContain(`[pid=${pid}] <process did exit: exitCode=null, signal=SIGKILL>`);
});
