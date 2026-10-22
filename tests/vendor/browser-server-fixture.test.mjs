import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:net';
import { fileURLToPath } from 'node:url';
import { startBrowserServer, createServerSupervisor } from '../../tools/vendor/start-browser-server.mjs';
import { workflowWorkers } from '../../tools/vendor/test-workers.mjs';

const fakeServer = fileURLToPath(new URL('./fake-browser-server.mjs', import.meta.url));
const fake = (...extra) => ({ command: process.execPath, args: port => [fakeServer, '--port', String(port), ...extra] });
const REAL_SERVER_TIMEOUT_MS = 90_000;

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
