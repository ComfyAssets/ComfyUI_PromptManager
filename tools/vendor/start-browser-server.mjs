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
const START_ATTEMPTS = 3;
const PORT_COLLISION = /address already in use|errno 98\b|errno 48\b|10048|EADDRINUSE/i;

const pythonServer = port => ['-m', 'tests.browser.server', '--port', String(port)];

function describeExit({ code, signal }) {
    return signal ? `signal ${signal}` : `code ${code}`;
}

// Start one disposable tests/browser/server.py on `port` and wait until it
// serves the readiness page. Resolves to {port, url, exited, exitStatus,
// stderrTail(), stop()}; rejects with the server's stderr tail when it exits
// or stays unready.
export async function startBrowserServer({
    port,
    command = process.env.PM_TEST_PYTHON || 'python',
    args = pythonServer,
    cwd = repositoryRoot,
    timeoutMs = STARTUP_TIMEOUT_MS,
} = {}) {
    if (!Number.isInteger(port) || port <= 0) throw new Error(`startBrowserServer needs a port, got ${port}`);
    const url = `http://127.0.0.1:${port}`;
    const child = spawn(command, args(port), { cwd, stdio: ['ignore', 'ignore', 'pipe'] });
    let stderr = '';
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', chunk => { stderr = (stderr + chunk).slice(-STDERR_TAIL_BYTES); });
    const killOnExit = () => child.kill('SIGKILL');
    process.once('exit', killOnExit);
    const server = {
        port,
        url,
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
        return new Error(`test server on port ${port} ${reason}${tail ? `\n--- server stderr tail ---\n${tail}` : ''}`);
    };
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        if (server.exitStatus !== null) {
            throw await failure(`exited with ${describeExit(server.exitStatus)} before it was ready`);
        }
        try {
            const response = await fetch(`${url}${READY_PATH}`, { signal: AbortSignal.timeout(PROBE_TIMEOUT_MS) });
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
// server, or starts a fresh one when the last one died; crash(server) turns an
// unexpected exit into an error carrying the stderr tail; a startup that lost
// its port to another process between reservation and bind is retried.
export function createServerSupervisor({ start, attempts = START_ATTEMPTS }) {
    let current = null;
    return {
        async ensure() {
            if (current && current.exitStatus === null) return current;
            current = null;
            for (let attempt = 1; ; attempt += 1) {
                try {
                    current = await start();
                    return current;
                } catch (error) {
                    if (attempt >= attempts || !PORT_COLLISION.test(error.message)) throw error;
                }
            }
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
