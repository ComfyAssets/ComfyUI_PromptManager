import { spawn } from 'node:child_process';
const child = spawn(process.env.PM_TEST_PYTHON || 'python',
    ['-m', 'tests.browser.server', ...process.argv.slice(2)], { stdio: 'inherit' });
child.on('error', error => { console.error(error.message); process.exitCode = 1; });
child.on('exit', code => { process.exitCode = code ?? 1; });
for (const signal of ['SIGTERM', 'SIGINT']) {
    process.once(signal, () => child.kill(signal));
}
