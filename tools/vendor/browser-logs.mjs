// What a browser printed before it died (#513). Playwright writes a browser's stderr, its launch and its exit
// (`<process did exit: exitCode=…, signal=…>`) only to its `pw:browser` debug channel, which is off unless DEBUG is
// set, so a browser that dies between two tests leaves the next test only "Target page, context or browser has been
// closed". A worker records that channel into a bounded tail, prints one line when a browser exits abnormally and
// attaches the tail to a test that fails on a closed or crashed target. Nothing is retried or hidden.
import { createRequire } from 'node:module';
import { writeFile } from 'node:fs/promises';
import { format, stripVTControlCharacters } from 'node:util';

const BROWSER_NAMESPACE = 'pw:browser';
export const BROWSER_LOG_LIMIT = 300;
export const BROWSER_EXIT_PREFIX = 'pm-browser-exit| ';
const LAUNCH_LINE = /<launched> pid=(\d+)/;
const EXIT_LINE = /\[pid=(\d+)\] <process did exit: exitCode=([^,]+), signal=([^>]+)>/;
// The client's TargetClosedError, a protocol error of a closed or crashed target, and a crashed page.
const TARGET_GONE = /Target page, context or browser has been closed|Target (?:closed|crashed)|Browser closed|browser has disconnected|Page crashed/i;

/** The `debug` module Playwright logs through (the instance this worker's tests launch browsers with). */
export function playwrightDebug() {
    return createRequire(import.meta.url)('playwright-core/lib/utilsBundle').debug;
}

/** The pid, exit code and signal of a `process did exit` line, or null. */
export function parseExitLine(line) {
    const match = EXIT_LINE.exec(line);
    if (!match) return null;
    const value = text => (text === 'null' ? null : text);
    const code = value(match[2]);
    return { pid: Number(match[1]), exitCode: code === null ? null : Number(code), signal: value(match[3]) };
}

export const isAbnormalExit = exit => exit !== null && (exit.exitCode !== 0 || exit.signal !== null);

/** True for an error message that says the page, context or browser under the test went away. */
export const isTargetGone = message => TARGET_GONE.test(String(message ?? ''));

/**
 * Records `pw:browser` into a tail of at most `limit` lines. Other debug channels keep printing as before; the
 * browser channel prints only when the user's own DEBUG asked for it. `write` gets one line per abnormal exit.
 */
export function createBrowserLogRecorder({ debug = playwrightDebug(), limit = BROWSER_LOG_LIMIT,
    write = text => process.stderr.write(text) } = {}) {
    const lines = [];
    const launches = [];
    const exits = [];
    const userWantsBrowser = debug.enabled(BROWSER_NAMESPACE);
    const previous = debug.disable();
    const original = debug.log;
    debug.log = function record(...args) {
        if (this?.namespace !== BROWSER_NAMESPACE || userWantsBrowser) {
            (original ?? (() => {})).apply(this, args);
        }
        if (this?.namespace !== BROWSER_NAMESPACE) return;
        const line = stripVTControlCharacters(format(...args));
        lines.push(line);
        if (lines.length > limit) lines.splice(0, lines.length - limit);
        const launched = LAUNCH_LINE.exec(line);
        if (launched) launches.push(Number(launched[1]));
        const exit = parseExitLine(line);
        if (!exit) return;
        exits.push(exit);
        if (isAbnormalExit(exit)) {
            write(`${BROWSER_EXIT_PREFIX}browser pid=${exit.pid} exited: exitCode=${exit.exitCode}, signal=${exit.signal}\n`);
        }
    };
    debug.enable([previous, BROWSER_NAMESPACE].filter(Boolean).join(','));
    return {
        tail: () => lines.join('\n'),
        launches: () => [...launches],
        exits: () => [...exits],
        uninstall() {
            debug.log = original;
            debug.disable();
            if (previous) debug.enable(previous);
        },
    };
}

/** Attaches the browser log tail to a test that failed because its page, context or browser went away. */
export async function attachBrowserLogs(testInfo, recorder) {
    if (testInfo.status === testInfo.expectedStatus) return false;
    if (!testInfo.errors.some(error => isTargetGone(error.message) || isTargetGone(error.stack))) return false;
    const exits = recorder.exits().filter(isAbnormalExit)
        .map(exit => `browser pid=${exit.pid} exited: exitCode=${exit.exitCode}, signal=${exit.signal}`);
    const summary = exits.length ? exits.join('\n') : 'no abnormal browser exit was logged in this worker';
    // A file, so the log travels with the uploaded test-results (the list reporter shortens a long body).
    const path = testInfo.outputPath('browser-stderr.txt');
    await writeFile(path, `${summary}\n--- last ${BROWSER_LOG_LIMIT} pw:browser lines ---\n${recorder.tail()}\n`);
    await testInfo.attach('browser-stderr', { path, contentType: 'text/plain' });
    return true;
}
