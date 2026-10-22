import { spawn } from 'node:child_process';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { repositoryRoot } from './vendor-viewerjs.mjs';

const READY_PATH = '/prompt_manager/test';
const STARTUP_TIMEOUT_MS = 60_000;
const STOP_TIMEOUT_MS = 5_000;
const POLL_INTERVAL_MS = 100;
const PROBE_TIMEOUT_MS = 1_000;
const STDERR_TAIL_BYTES = 8_192;
const HOST = '127.0.0.1';
// tests/browser/server.py prints this once it holds its listening socket.
const PORT_ANNOUNCEMENT = /^PM_TEST_SERVER_PORT=(\d+)\s*$/m;

const pythonServer = port => ['-m', 'tests.browser.server', '--port', String(port)];

function describeExit({ code, signal }) {
    return signal ? `signal ${signal}` : `code ${code}`;
}

// Start one disposable tests/browser/server.py and wait until it serves the
// readiness page. `port` 0 (the default) lets the server bind an OS-chosen port
// itself and announce it on stdout, so nothing can take the port between
// choosing and binding. Resolves to {port, url, exited, exitStatus,
// stderrTail(), stop()}; rejects with the server's stderr tail when it exits
// or stays unready.
export async function startBrowserServer({
    port = 0,
    command = process.env.PM_TEST_PYTHON || 'python',
    args = pythonServer,
    cwd = repositoryRoot,
    timeoutMs = STARTUP_TIMEOUT_MS,
} = {}) {
    if (!Number.isInteger(port) || port < 0 || port > 65535) {
        throw new Error(`startBrowserServer needs a port from 0 to 65535, got ${port}`);
    }
    const child = spawn(command, args(port), { cwd, stdio: ['ignore', 'pipe', 'pipe'] });
    let stderr = '';
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', chunk => { stderr = (stderr + chunk).slice(-STDERR_TAIL_BYTES); });
    let stdout = '';
    let announced = null;
    child.stdout.setEncoding('utf8');
    // Keep draining stdout for the server's whole life so it never blocks on a full pipe.
    child.stdout.on('data', chunk => {
        if (announced !== null) return;
        stdout = (stdout + chunk).slice(-STDERR_TAIL_BYTES);
        const match = PORT_ANNOUNCEMENT.exec(stdout);
        if (match) announced = Number(match[1]);
    });
    const killOnExit = () => child.kill('SIGKILL');
    process.once('exit', killOnExit);
    const server = {
        port: port || null,
        url: port ? `http://${HOST}:${port}` : null,
        exitStatus: null,
        stopping: false,
        stderrTail: () => stderr.trimEnd(),
        exited: new Promise(done => {
            child.once('error', error => {
                stderr += `\n${error.message}`;
                server.exitStatus = { code: null, signal: null, error };
                done(server.exitStatus);
            });
            child.once('exit', (code, signal) => {
                server.exitStatus = { code, signal };
                done(server.exitStatus);
            });
        }),
        async stop() {
            server.stopping = true;
            if (server.exitStatus === null) {
                child.kill('SIGTERM');
                const stopped = await Promise.race([server.exited, delay(STOP_TIMEOUT_MS).then(() => false)]);
                if (!stopped) {
                    child.kill('SIGKILL');
                    await server.exited;
                }
            }
            process.removeListener('exit', killOnExit);
        },
    };
    server.exited.then(() => process.removeListener('exit', killOnExit));
    const failure = async reason => {
        await server.stop();
        const tail = server.stderrTail();
        return new Error(`test server on port ${server.port ?? '(not yet announced)'} ${reason}`
            + (tail ? `\n--- server stderr tail ---\n${tail}` : ''));
    };
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        if (server.exitStatus !== null) {
            throw await failure(`exited with ${describeExit(server.exitStatus)} before it was ready`);
        }
        if (announced !== null && server.url === null) {
            server.port = announced;
            server.url = `http://${HOST}:${announced}`;
        }
        if (announced !== null && announced !== server.port) {
            throw await failure(`announced port ${announced} instead`);
        }
        if (server.url === null) {
            await delay(POLL_INTERVAL_MS);
            continue;
        }
        try {
            const response = await fetch(`${server.url}${READY_PATH}`, { signal: AbortSignal.timeout(PROBE_TIMEOUT_MS) });
            await response.arrayBuffer();
            if (response.ok) return server;
        } catch {
            // Not listening yet.
        }
        await delay(POLL_INTERVAL_MS);
    }
    throw await failure(`was not ready at ${READY_PATH} within ${timeoutMs} ms`);
}

// Keep one live server for a Playwright worker. ensure() returns the running
// server, or starts a fresh one when the last one died (a failed start is
// reported, not retried); crash(server) turns an unexpected exit into an error
// carrying the stderr tail.
export function createServerSupervisor({ start }) {
    let current = null;
    return {
        async ensure() {
            if (current && current.exitStatus === null) return current;
            current = null;
            current = await start();
            return current;
        },
        crash(server) {
            if (!server || server.stopping || server.exitStatus === null) return null;
            const tail = server.stderrTail();
            return new Error(`test server on port ${server.port} exited unexpectedly (${describeExit(server.exitStatus)})`
                + (tail ? `\n--- server stderr tail ---\n${tail}` : ''));
        },
        async stop() {
            const server = current;
            current = null;
            await server?.stop();
        },
    };
}

// CLI: `node start-browser-server.mjs --port N` runs one server in the foreground.
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
    const child = spawn(process.env.PM_TEST_PYTHON || 'python',
        ['-m', 'tests.browser.server', ...process.argv.slice(2)], { stdio: 'inherit' });
    child.on('error', error => { console.error(error.message); process.exitCode = 1; });
    child.on('exit', code => { process.exitCode = code ?? 1; });
    for (const signal of ['SIGTERM', 'SIGINT']) {
        process.once(signal, () => child.kill(signal));
    }
}
