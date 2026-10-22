import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:net';
import { fileURLToPath } from 'node:url';
import { freePort } from '../../tools/vendor/free-port.mjs';
import { startBrowserServer, createServerSupervisor } from '../../tools/vendor/start-browser-server.mjs';
import { workflowWorkers } from '../../tools/vendor/test-workers.mjs';

const fakeServer = fileURLToPath(new URL('./fake-browser-server.mjs', import.meta.url));
const fake = (...extra) => ({ command: process.execPath, args: port => [fakeServer, '--port', String(port), ...extra] });

function listen(port = 0) {
    return new Promise((resolve, reject) => {
        const server = createServer();
        server.once('error', reject);
        server.listen(port, '127.0.0.1', () => resolve(server));
    });
}

test('free port allocation never reuses a bound port', async t => {
    const held = await listen();
    t.after(() => held.close());
    const heldPort = held.address().port;
    const seen = [];
    for (let index = 0; index < 20; index += 1) {
        const port = await freePort();
        assert.ok(Number.isInteger(port) && port > 0 && port < 65536, `port ${port} is a TCP port`);
        assert.notEqual(port, heldPort, 'a port another process holds is never handed out');
        const probe = await listen(port);
        seen.push(port);
        await new Promise(resolve => probe.close(resolve));
    }
    assert.ok(new Set(seen).size > 1, 'the OS hands out more than one port');
});

test('startBrowserServer serves the readiness page on the given port and stops cleanly', async () => {
    const port = await freePort();
    const server = await startBrowserServer({ port, ...fake() });
    assert.equal(server.port, port);
    assert.equal(server.url, `http://127.0.0.1:${port}`);
    assert.equal(await (await fetch(`${server.url}/prompt_manager/test`)).text(), 'ready');
    await server.stop();
    await assert.rejects(fetch(`${server.url}/prompt_manager/test`));
});

test('startBrowserServer reports a startup failure with the server\'s stderr tail', async () => {
    const port = await freePort();
    await assert.rejects(startBrowserServer({ port, ...fake('--fail') }), error => {
        assert.match(error.message, /exited with code 4 before it was ready/);
        assert.match(error.message, /last fake stderr line/);
        return true;
    });
});

test('startBrowserServer gives up on a server that never becomes ready', async () => {
    const port = await freePort();
    const silent = { command: process.execPath, args: () => ['-e', 'setInterval(() => {}, 1000)'] };
    await assert.rejects(startBrowserServer({ port, ...silent, timeoutMs: 500 }), /not ready .* within 500 ms/);
});

test('worker server restarts after an unexpected exit', async t => {
    const supervisor = createServerSupervisor({ start: async () => startBrowserServer({ port: await freePort(), ...fake() }) });
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

test('supervisor retries a port that was taken between reservation and bind', async t => {
    const ports = [];
    let attempts = 0;
    const supervisor = createServerSupervisor({
        start: async () => {
            attempts += 1;
            if (attempts === 1) throw new Error('OSError: [Errno 98] error while attempting to bind: address already in use');
            const port = await freePort();
            ports.push(port);
            return startBrowserServer({ port, ...fake() });
        },
    });
    t.after(() => supervisor.stop());
    const server = await supervisor.ensure();
    assert.equal(attempts, 2);
    assert.equal(server.port, ports[0]);
});

test('supervisor does not retry a startup failure that is not a port collision', async () => {
    let attempts = 0;
    const supervisor = createServerSupervisor({ start: async () => { attempts += 1; throw new Error('ImportError: no module'); } });
    await assert.rejects(supervisor.ensure(), /ImportError/);
    assert.equal(attempts, 1);
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
