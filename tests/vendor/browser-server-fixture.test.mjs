import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:net';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { startBrowserServer, createServerSupervisor, attachServerStderr, forwardPrefixedLines, SLOW_RESET_PREFIX } from '../../tools/vendor/start-browser-server.mjs';
import { globalTimeoutMs, reporters, workflowWorkers } from '../../tools/vendor/test-workers.mjs';
import RunningTestsReporter from '../../tools/vendor/running-tests-reporter.mjs';

const fakeServer = fileURLToPath(new URL('./fake-browser-server.mjs', import.meta.url));
const fake = (...extra) => ({ command: process.execPath, args: port => [fakeServer, '--port', String(port), ...extra] });
const REAL_SERVER_TIMEOUT_MS = 90_000;
const launcher = fileURLToPath(new URL('../../tools/vendor/start-browser-server.mjs', import.meta.url));

/** Polls `check` until it is truthy, failing with `what` after `ms`. */
async function waitFor(check, what, ms = 5_000) {
    const deadline = Date.now() + ms;
    while (!check()) {
        if (Date.now() > deadline) throw new Error(`timed out waiting for ${what}`);
        await delay(20);
    }
}

async function writeStderr(server, text) {
    await (await fetch(`${server.url}/__stderr?text=${encodeURIComponent(text)}`)).text();
    await waitFor(() => server.stderrTail().endsWith(text.trimEnd()), `stderr ${JSON.stringify(text)}`);
}

/** A process that exited counts as gone even while a non-reaping PID 1 (CI's container) leaves it a zombie. */
function alive(pid) {
    try { process.kill(pid, 0); } catch { return false; }
    try { return readFileSync(`/proc/${pid}/stat`, 'utf8').replace(/^.*\) /s, '')[0] !== 'Z'; } catch { return true; }
}

/** Starts a server from a separate Node worker process, SIGKILLs that worker and returns the server's pid. */
async function serverOfKilledWorker(options) {
    const script = `import { startBrowserServer } from ${JSON.stringify(pathToFileURL(launcher).href)};
        const server = await startBrowserServer(${options});
        console.log('SERVER_PID=' + server.pid);
        setInterval(() => {}, 1000);`;
    const worker = spawn(process.execPath, ['--input-type=module', '-e', script], { stdio: ['ignore', 'pipe', 'inherit'] });
    let out = '';
    worker.stdout.setEncoding('utf8');
    worker.stdout.on('data', chunk => { out += chunk; });
    await waitFor(() => /SERVER_PID=\d+\n/.test(out) || worker.exitCode !== null, 'the worker to start its server', REAL_SERVER_TIMEOUT_MS);
    const pid = Number(/SERVER_PID=(\d+)/.exec(out)?.[1]);
    assert.ok(pid > 0, `the worker started a server (output: ${out})`);
    assert.ok(alive(pid), 'the server runs while its worker lives');
    worker.kill('SIGKILL');
    await once(worker, 'exit');
    return pid;
}

function listen(port = 0) {
    return new Promise((resolve, reject) => {
        const server = createServer();
        server.once('error', reject);
        server.listen(port, '127.0.0.1', () => resolve(server));
    });
}

async function releasedPort() {
    const probe = await listen();
    const { port } = probe.address();
    await new Promise(resolve => probe.close(resolve));
    return port;
}

test('free port allocation never reuses a bound port', async t => {
    const held = await listen();
    t.after(() => held.close());
    const heldPort = held.address().port;
    const servers = await Promise.all(Array.from({ length: 6 }, () => startBrowserServer(fake())));
    t.after(() => Promise.all(servers.map(server => server.stop())));
    const ports = servers.map(server => server.port);
    for (const server of servers) {
        assert.ok(Number.isInteger(server.port) && server.port > 0 && server.port < 65536, `port ${server.port} is a TCP port`);
        assert.equal(server.url, `http://127.0.0.1:${server.port}`);
        assert.equal(await (await fetch(`${server.url}/prompt_manager/test`)).text(), 'ready');
    }
    assert.ok(!ports.includes(heldPort), 'a port another process holds is never handed out');
    assert.equal(new Set(ports).size, ports.length, 'concurrent servers never share a port');
});

test('startBrowserServer asks the server to bind port 0 and reads the port it announces', async () => {
    const asked = [];
    const server = await startBrowserServer({ command: process.execPath, args: port => { asked.push(port); return fake().args(port); } });
    try {
        assert.deepEqual(asked, [0], 'no port is reserved and released before the server binds');
        assert.ok(server.port > 0);
    } finally {
        await server.stop();
    }
});

test('startBrowserServer serves the readiness page on a requested port and stops cleanly', async () => {
    const port = await releasedPort();
    const server = await startBrowserServer({ port, ...fake() });
    assert.equal(server.port, port);
    assert.equal(server.url, `http://127.0.0.1:${port}`);
    assert.equal(await (await fetch(`${server.url}/prompt_manager/test`)).text(), 'ready');
    await server.stop();
    await assert.rejects(fetch(`${server.url}/prompt_manager/test`));
});

test('startBrowserServer reports a startup failure with the server\'s stderr tail', async () => {
    await assert.rejects(startBrowserServer(fake('--fail')), error => {
        assert.match(error.message, /exited with code 4 before it was ready/);
        assert.match(error.message, /last fake stderr line/);
        return true;
    });
});

test('startBrowserServer gives up on a server that never announces a port or becomes ready', async () => {
    const silent = { command: process.execPath, args: () => ['-e', 'setInterval(() => {}, 1000)'] };
    await assert.rejects(startBrowserServer({ ...silent, timeoutMs: 500 }), /not ready .* within 500 ms/);
});

test('worker server restarts after an unexpected exit', async t => {
    const supervisor = createServerSupervisor({ start: () => startBrowserServer(fake()) });
    t.after(() => supervisor.stop());
    const first = await supervisor.ensure();
    assert.equal(supervisor.crash(first), null, 'a running server reports no crash');
    await fetch(`${first.url}/__exit`);
    await first.exited;
    const crash = supervisor.crash(first);
    assert.match(crash.message, /test server on port \d+ exited unexpectedly \(code 3\)/);
    assert.match(crash.message, /crashed on purpose/);
    const second = await supervisor.ensure();
    assert.notEqual(second, first, 'the next test gets a fresh server');
    assert.equal(await (await fetch(`${second.url}/prompt_manager/test`)).text(), 'ready');
    assert.equal(await supervisor.ensure(), second, 'a healthy server is reused');
});

test('supervisor reports a startup failure once and tries again on the next ensure', async t => {
    let attempts = 0;
    const supervisor = createServerSupervisor({
        start: async () => {
            attempts += 1;
            if (attempts === 1) throw new Error('ImportError: no module');
            return startBrowserServer(fake());
        },
    });
    t.after(() => supervisor.stop());
    await assert.rejects(supervisor.ensure(), /ImportError/);
    assert.equal(attempts, 1, 'a failed startup is not retried behind the caller\'s back');
    const server = await supervisor.ensure();
    assert.equal(attempts, 2);
    assert.equal(await (await fetch(`${server.url}/prompt_manager/test`)).text(), 'ready');
});

test('the port announcement counts only once its line is complete', async t => {
    const server = await startBrowserServer({ ...fake('--split-announce'), timeoutMs: 5_000 });
    t.after(() => server.stop());
    assert.equal(await (await fetch(`${server.url}/prompt_manager/test`)).text(), 'ready');
    assert.equal(server.url, `http://127.0.0.1:${server.port}`);
});

test('server stderr since a mark holds only what came after it, bounded to the newest output', async t => {
    const server = await startBrowserServer({ ...fake(), stderrLogChars: 64 });
    t.after(() => server.stop());
    await writeStderr(server, 'earlier test\n');
    const mark = server.stderrMark();
    assert.equal(server.stderrSince(mark), '');
    await writeStderr(server, 'Traceback: this test\n');
    assert.equal(server.stderrSince(mark), 'Traceback: this test\n');
    const flood = `${'x'.repeat(80)}newest\n`;
    await writeStderr(server, flood);
    const kept = server.stderrSince(mark);
    assert.match(kept, /^\[earlier server stderr dropped\]\n/);
    assert.ok(kept.endsWith('newest\n'), 'the newest output is kept');
    assert.ok(kept.length <= 64 + '[earlier server stderr dropped]\n'.length, `bounded, got ${kept.length} chars`);
});

test('a failed test gets the server stderr written during it attached; a passing one does not', async t => {
    const server = await startBrowserServer(fake());
    t.after(() => server.stop());
    const attached = [];
    const info = status => ({ status, expectedStatus: 'passed', attach: async (name, options) => { attached.push({ name, ...options }); } });
    await writeStderr(server, 'before the test\n');
    const mark = server.stderrMark();
    await attachServerStderr(info('failed'), server, mark);
    assert.deepEqual(attached, [], 'nothing to attach when the server wrote nothing');
    await writeStderr(server, 'Traceback (most recent call last): boom\n');
    await attachServerStderr(info('passed'), server, mark);
    assert.deepEqual(attached, [], 'a passing test attaches nothing');
    await attachServerStderr(info('timedOut'), server, mark);
    assert.equal(attached.length, 1);
    assert.equal(attached[0].name, 'server-stderr');
    assert.equal(attached[0].contentType, 'text/plain');
    assert.equal(String(attached[0].body), 'Traceback (most recent call last): boom\n');
});

test('only complete slow-reset report lines are forwarded, however the chunks split them', () => {
    const written = [];
    const forward = forwardPrefixedLines(SLOW_RESET_PREFIX, text => written.push(text));
    forward(`aiohttp access line\n${SLOW_RESET_PREFIX}POST /__test__/reset still`);
    assert.deepEqual(written, [], 'a report line waits for its newline');
    forward(` running after 2.000s\nplain stderr\n${SLOW_RESET_PREFIX}Thread 0x1 [asyncio_0]\n${SLOW_RESET_PREFIX.slice(0, 5)}`);
    forward(`${SLOW_RESET_PREFIX.slice(5)}  File "server.py", line 1\nnot a report ${SLOW_RESET_PREFIX}\n`);
    assert.deepEqual(written, [
        `${SLOW_RESET_PREFIX}POST /__test__/reset still running after 2.000s\n${SLOW_RESET_PREFIX}Thread 0x1 [asyncio_0]\n`,
        `${SLOW_RESET_PREFIX}  File "server.py", line 1\n`,
    ]);
});

test('a slow-reset report on server stderr reaches the worker stderr; other stderr does not', async t => {
    const reported = [];
    const server = await startBrowserServer({ ...fake(), report: text => reported.push(text) });
    t.after(() => server.stop());
    await writeStderr(server, 'Traceback: ordinary stderr\n');
    await writeStderr(server, `${SLOW_RESET_PREFIX}POST /__test__/reset answered 200 after 16.064s\n`);
    assert.deepEqual(reported, [`${SLOW_RESET_PREFIX}POST /__test__/reset answered 200 after 16.064s\n`]);
    assert.ok(server.stderrTail().includes('Traceback: ordinary stderr'), 'the stderr tail still keeps everything');
});

test('a server exits with its worker even when the worker is SIGKILLed', async () => {
    const pid = await serverOfKilledWorker(`{ command: process.execPath, args: port => [${JSON.stringify(fakeServer)}, '--port', String(port), '--exit-on-stdin-eof'] }`);
    await waitFor(() => !alive(pid), `server ${pid} to exit after its worker died`);
});

test('tests/browser/server.py exits with a SIGKILLed worker', { timeout: 2 * REAL_SERVER_TIMEOUT_MS }, async () => {
    const pid = await serverOfKilledWorker(`{ timeoutMs: ${REAL_SERVER_TIMEOUT_MS} }`);
    await waitFor(() => !alive(pid), `server.py ${pid} to exit after its worker died`, 15_000);
});

test('tests/browser/server.py binds an OS-chosen port for --port 0 and still honours --port N', { timeout: 2 * REAL_SERVER_TIMEOUT_MS }, async t => {
    const held = await listen();
    t.after(() => held.close());
    const chosen = await startBrowserServer({ timeoutMs: REAL_SERVER_TIMEOUT_MS });
    try {
        assert.notEqual(chosen.port, held.address().port);
        assert.equal((await fetch(`${chosen.url}/prompt_manager/test`)).status, 200);
    } finally {
        await chosen.stop();
    }
    const port = await releasedPort();
    const fixed = await startBrowserServer({ port, timeoutMs: REAL_SERVER_TIMEOUT_MS });
    try {
        assert.equal(fixed.port, port);
        assert.equal((await fetch(`http://127.0.0.1:${port}/prompt_manager/test`)).status, 200);
    } finally {
        await fixed.stop();
    }
});

test('worker count follows PM_TEST_WORKERS, then one in CI, then a quarter of the CPUs up to eight', () => {
    assert.equal(workflowWorkers({ env: { PM_TEST_WORKERS: '3', CI: '1' }, cpus: 32 }), 3);
    assert.equal(workflowWorkers({ env: { CI: '1' }, cpus: 32 }), 1);
    assert.equal(workflowWorkers({ env: {}, cpus: 32 }), 8);
    assert.equal(workflowWorkers({ env: {}, cpus: 12 }), 3);
    assert.equal(workflowWorkers({ env: {}, cpus: 2 }), 1);
    assert.throws(() => workflowWorkers({ env: { PM_TEST_WORKERS: 'many' }, cpus: 8 }), /PM_TEST_WORKERS must be a positive integer/);
    assert.throws(() => workflowWorkers({ env: { PM_TEST_WORKERS: '0' }, cpus: 8 }), /PM_TEST_WORKERS must be a positive integer/);
});

test('the Playwright run limit applies under CI, follows its override and is off locally', () => {
    const variable = 'PM_TEST_GLOBAL_TIMEOUT_MINUTES';
    assert.equal(globalTimeoutMs({ env: { CI: '1' }, ciMinutes: 17, variable }), 17 * 60_000);
    assert.equal(globalTimeoutMs({ env: {}, ciMinutes: 17, variable }), 0, 'local runs keep no limit');
    assert.equal(globalTimeoutMs({ env: { CI: '1', [variable]: '15' }, ciMinutes: 17, variable }), 15 * 60_000);
    assert.equal(globalTimeoutMs({ env: { [variable]: '0.5' }, ciMinutes: 17, variable }), 30_000);
    assert.equal(globalTimeoutMs({ env: { CI: '1', [variable]: '1' }, ciMinutes: 3 }), 3 * 60_000,
        'a config without a variable ignores the override');
    for (const bad of ['0', '-1', 'soon', '']) {
        assert.throws(() => globalTimeoutMs({ env: { [variable]: bad }, ciMinutes: 17, variable }), /positive number/);
    }
});

test('CI adds the running-tests reporter and a blob report under test-results/blob/, which the trace upload covers; local runs keep list', () => {
    assert.equal(reporters({ env: {}, name: 'workflows' }), 'list');
    assert.deepEqual(reporters({ env: { CI: '1' }, name: 'workflows' }),
        [['list'], ['./running-tests-reporter.mjs'], ['blob', { outputDir: './test-results/blob/workflows' }]]);
});

test('the running-tests reporter names only the tests that never ended, with how long they ran', () => {
    let clock = 1_000;
    const output = [];
    const reporter = new RunningTestsReporter({ now: () => clock, write: text => output.push(text), cwd: '/repo' });
    const spec = (id, title, line) => ({ id, title, location: { file: '/repo/tests/browser/a.spec.mjs', line },
        parent: { project: () => ({ name: 'firefox' }) } });
    const done = spec('1', 'finishes', 10);
    const stuck = spec('2', 'hangs on a fetch', 20);
    reporter.onTestBegin(done, { retry: 0 });
    reporter.onTestBegin(stuck, { retry: 0 });
    clock += 500;
    reporter.onTestEnd(done, { retry: 0 });
    clock += 41_500;
    reporter.onEnd({ status: 'timedout' });
    assert.equal(output.join(''), '\nStill running when the run ended (timedout): 1\n'
        + '  [firefox] › tests/browser/a.spec.mjs:20 › hangs on a fetch (running 42.0s)\n');
    output.length = 0;
    reporter.onTestEnd(stuck, { retry: 0 });
    reporter.onEnd({ status: 'passed' });
    assert.deepEqual(output, [], 'a run where every test ended prints nothing');
});
