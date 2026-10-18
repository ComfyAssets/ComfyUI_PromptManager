import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { readFileSync } from 'node:fs';

const root = fileURLToPath(new URL('../../', import.meta.url));
test('real-ComfyUI suite registers Classic and Nodes 2.0 startup projects', () => {
    const output = execFileSync(process.execPath, ['tools/vendor/node_modules/@playwright/test/cli.js',
        'test', '--config', 'tools/vendor/comfyui.config.mjs', '--list'], {
        cwd: root, encoding: 'utf8', env: { ...process.env,
            PM_COMFY_ROOT: '/unused-pinned-checkout', PM_COMFY_URL: 'http://127.0.0.1:8199/' },
    });
    assert.match(output, /\[classic-start\]/);
    assert.match(output, /\[nodes2-start\]/);
});

test('execution script discovers only the new matrix and preserves all 78 stock cases', () => {
    const manifest = JSON.parse(readFileSync(new URL('../../tools/vendor/package.json', import.meta.url), 'utf8'));
    assert.equal(manifest.scripts['test:executions'], 'playwright test --config playwright.execution.config.mjs');
    const list = config => execFileSync(process.execPath, ['tools/vendor/node_modules/@playwright/test/cli.js',
        'test', '--config', config, '--list'], { cwd: root, encoding: 'utf8',
        env: { ...process.env, PM_COMFY_ROOT: '/unused-pinned-checkout' } });
    const stock = list('tools/vendor/comfyui.config.mjs');
    assert.match(stock, /Total: 78 tests in 7 files/);
    assert.doesNotMatch(stock, /execution-history/);
    assert.doesNotMatch(stock, /execution-outputs/);
    const executions = list('tools/vendor/playwright.execution.config.mjs');
    assert.match(executions, /\[classic-start\]/);
    assert.match(executions, /\[nodes2-start\]/);
    assert.match(executions, /Total: 42 tests in 2 files/);
    for (const project of ['classic-start', 'nodes2-start']) {
        const cases = executions.split('\n').filter(line => line.includes(`[${project}]`));
        assert.equal(cases.filter(line => line.includes('execution-history.spec.mjs')).length, 13);
        assert.equal(cases.filter(line => line.includes('execution-outputs.spec.mjs')).length, 8);
    }
    assert.doesNotMatch(executions, /prompt-manager.spec/);
});

test('execution config refuses live-host URLs and binds a managed loopback port', () => {
    for (const url of ['http://127.0.0.1:8188/', 'https://127.0.0.1:8196/', 'http://localhost:8196/', 'http://example.com:8196/']) {
        assert.throws(() => execFileSync(process.execPath, ['--input-type=module', '-e',
            "await import('./tools/vendor/playwright.execution.config.mjs')"], { cwd: root, stdio: 'pipe',
            env: { ...process.env, PM_COMFY_ROOT: '/unused', PM_EXECUTION_URL: url } }), /loopback|8196/);
    }
});

test('CI retains stock, execution and pack suites in order with uploaded execution reports', () => {
    const workflow = readFileSync(new URL('../../.github/workflows/test.yml', import.meta.url), 'utf8');
    const commands = [...workflow.matchAll(/^        run: npm run --prefix tools\/vendor (test:[a-z]+)$/gm)].map(match => match[1]);
    assert.deepEqual(commands, ['test:comfyui', 'test:executions', 'test:compat']);
    assert.match(workflow, /path: tools\/vendor\/test-results\//);
});
