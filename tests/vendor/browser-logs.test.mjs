import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { attachBrowserLogs, BROWSER_EXIT_PREFIX, createBrowserLogRecorder, isAbnormalExit, isTargetGone, parseExitLine,
    playwrightDebug } from '../../tools/vendor/browser-logs.mjs';

const debug = playwrightDebug();

/** Runs `body` with DEBUG set to `namespaces` and restores the caller's setting afterwards. */
function withDebug(namespaces, body) {
    const saved = debug.disable();
    const savedLog = debug.log;
    if (namespaces) debug.enable(namespaces);
    try {
        return body();
    } finally {
        debug.log = savedLog;
        debug.disable();
        if (saved) debug.enable(saved);
    }
}

const outputDir = mkdtempSync(join(tmpdir(), 'pm-browser-logs-'));
test.after(() => rmSync(outputDir, { recursive: true, force: true }));

function testInfo({ status = 'failed', errors = [] } = {}) {
    const attachments = [];
    const dir = mkdtempSync(join(outputDir, 'test-'));
    return { status, expectedStatus: 'passed', errors, attachments, outputPath: name => join(dir, name),
        attach: async (name, options) => { attachments.push({ name, ...options }); } };
}

test('parses the exit line Playwright logs for a browser process', () => {
    assert.deepEqual(parseExitLine('[pid=7124] <process did exit: exitCode=null, signal=SIGKILL> +21ms'),
        { pid: 7124, exitCode: null, signal: 'SIGKILL' });
    assert.deepEqual(parseExitLine('[pid=9] <process did exit: exitCode=0, signal=null>'), { pid: 9, exitCode: 0, signal: null });
    assert.deepEqual(parseExitLine('[pid=9] <process did exit: exitCode=11, signal=null>'), { pid: 9, exitCode: 11, signal: null });
    assert.equal(parseExitLine('[pid=9][err] JavaScript warning'), null);
    assert.equal(isAbnormalExit({ pid: 9, exitCode: 0, signal: null }), false);
    assert.equal(isAbnormalExit({ pid: 9, exitCode: null, signal: 'SIGKILL' }), true);
    assert.equal(isAbnormalExit({ pid: 9, exitCode: 139, signal: null }), true);
});

test('recognises the errors of a page, context or browser that went away', () => {
    for (const message of ['Error: browser.newContext: Target page, context or browser has been closed',
        'Protocol error (Page.navigate): Target closed', 'page.goto: Page crashed', 'Target crashed']) {
        assert.equal(isTargetGone(message), true, message);
    }
    for (const message of ['expect(locator).toBeVisible() failed', undefined, 'Test timeout of 30000ms exceeded.']) {
        assert.equal(isTargetGone(message), false, String(message));
    }
});

test('records the browser channel into a bounded tail without printing it', () => withDebug('', () => {
    const printed = [];
    debug.log = (...args) => printed.push(args.join(' '));
    const written = [];
    const recorder = createBrowserLogRecorder({ debug, limit: 3, write: text => written.push(text) });
    const browser = debug('pw:browser');
    browser('<launched> pid=5');
    for (const n of [1, 2, 3, 4]) browser(`[pid=5][err] line ${n}`);
    assert.deepEqual(printed, [], 'the browser channel stays quiet when DEBUG did not ask for it');
    assert.match(recorder.tail(), /line 2[\s\S]*line 3[\s\S]*line 4/);
    assert.doesNotMatch(recorder.tail(), /line 1/);
    assert.deepEqual(written, []);
    assert.deepEqual(recorder.launches(), [5], 'launches outlive the bounded tail');
    browser('[pid=5] <process did exit: exitCode=null, signal=SIGKILL>');
    assert.deepEqual(recorder.exits(), [{ pid: 5, exitCode: null, signal: 'SIGKILL' }]);
    assert.deepEqual(written, [`${BROWSER_EXIT_PREFIX}browser pid=5 exited: exitCode=null, signal=SIGKILL\n`]);
    browser('[pid=6] <process did exit: exitCode=0, signal=null>');
    assert.equal(written.length, 1, 'a clean exit prints nothing');
    recorder.uninstall();
    assert.equal(debug.enabled('pw:browser'), false, 'uninstall restores the caller\'s DEBUG');
}));

test('keeps printing the channels the user enabled, the browser one included', () => withDebug('pw:api,pw:browser', () => {
    const printed = [];
    debug.log = function (...args) { printed.push(`${this.namespace} ${args.join(' ')}`); };
    const recorder = createBrowserLogRecorder({ debug, write: () => {} });
    debug('pw:api')('call');
    debug('pw:browser')('[pid=1][out] hello');
    debug('pw:protocol')('not enabled');
    assert.equal(printed.length, 2);
    assert.match(printed[0], /^pw:api /);
    assert.match(printed[1], /^pw:browser /);
    assert.doesNotMatch(recorder.tail(), /call/);
    recorder.uninstall();
    assert.equal(debug.enabled('pw:api'), true);
    assert.equal(debug.enabled('pw:browser'), true);
}));

test('attaches the tail and the exit only to a test whose target went away', async () => withDebug('', async () => {
    debug.log = () => {};
    const recorder = createBrowserLogRecorder({ debug, write: () => {} });
    const browser = debug('pw:browser');
    browser('\u001b[30m[pid=8][err] Crash Annotation GraphicsCriticalError\u001b[0m');
    browser('[pid=8] <process did exit: exitCode=null, signal=SIGKILL>');

    const passed = testInfo({ status: 'passed' });
    assert.equal(await attachBrowserLogs(passed, recorder), false);
    const assertion = testInfo({ errors: [{ message: 'expect(locator).toBeVisible() failed' }] });
    assert.equal(await attachBrowserLogs(assertion, recorder), false);
    assert.deepEqual(assertion.attachments, []);

    const closed = testInfo({ errors: [{ message: 'Error: browser.newContext: Target page, context or browser has been closed' }] });
    assert.equal(await attachBrowserLogs(closed, recorder), true);
    assert.equal(closed.attachments.length, 1);
    const [{ name, path, contentType }] = closed.attachments;
    assert.equal(name, 'browser-stderr');
    assert.equal(contentType, 'text/plain');
    const body = readFileSync(path, 'utf8');
    assert.match(body, /^browser pid=8 exited: exitCode=null, signal=SIGKILL\n/);
    assert.match(body, /\[pid=8\]\[err\] Crash Annotation GraphicsCriticalError/);
    assert.doesNotMatch(body, /\u001b/, 'the attached log has no terminal colours');
    recorder.uninstall();
}));

test('says so when the worker logged no abnormal exit', async () => withDebug('', async () => {
    debug.log = () => {};
    const recorder = createBrowserLogRecorder({ debug, write: () => {} });
    const closed = testInfo({ errors: [{ message: 'x', stack: 'Error: Target closed\n    at y' }] });
    assert.equal(await attachBrowserLogs(closed, recorder), true);
    assert.match(readFileSync(closed.attachments[0].path, 'utf8'), /^no abnormal browser exit was logged in this worker\n/);
    recorder.uninstall();
}));
