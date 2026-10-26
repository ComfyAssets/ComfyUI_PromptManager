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

test('execution script discovers only the new matrix and preserves all 78 original stock cases plus the native editor and renderer host cases', () => {
    const manifest = JSON.parse(readFileSync(new URL('../../tools/vendor/package.json', import.meta.url), 'utf8'));
    assert.equal(manifest.scripts['test:executions'], 'playwright test --config playwright.execution.config.mjs');
    const list = config => execFileSync(process.execPath, ['tools/vendor/node_modules/@playwright/test/cli.js',
        'test', '--config', config, '--list'], { cwd: root, encoding: 'utf8',
        env: { ...process.env, PM_COMFY_ROOT: '/unused-pinned-checkout' } });
    const stock = list('tools/vendor/comfyui.config.mjs');
    // + local Inspect host case (inspect-sender.spec.mjs, 2 projects), + the S10 renderer case (prompt-bridge.spec.mjs, 2 projects)
    // + #343 linked text: five node types, PromptSearchList, the pre-#343 workflows, legacy Primitive, moved link,
    //   paste/clone/undo, wildcard Primitive, subgraph Primitive (text-link.spec.mjs, 2 projects)
    assert.match(stock, /Total: 110 tests in 9 files/);
    assert.doesNotMatch(stock, /execution-history/);
    assert.doesNotMatch(stock, /execution-outputs/);
    assert.doesNotMatch(stock, /library-location/);  // it starts its own fresh host (#249 S6b)
    assert.doesNotMatch(stock, /image-attribution/);  // needs the owned fixture pack (#342)
    const executions = list('tools/vendor/playwright.execution.config.mjs');
    assert.match(executions, /\[classic-start\]/);
    assert.match(executions, /\[nodes2-start\]/);
    // + the fresh-host library location case, + #342 image attribution, + #343 linked text
    assert.match(executions, /Total: 90 tests in 5 files/);
    for (const project of ['classic-start', 'nodes2-start']) {
        const cases = executions.split('\n').filter(line => line.includes(`[${project}]`));
        assert.equal(cases.filter(line => line.includes('execution-history.spec.mjs')).length, 15);
        assert.equal(cases.filter(line => line.includes('execution-outputs.spec.mjs')).length, 8);
        assert.equal(cases.filter(line => line.includes('library-location.spec.mjs')).length, 1);
        assert.equal(cases.filter(line => line.includes('image-attribution.spec.mjs')).length, 2);
        assert.equal(cases.filter(line => line.includes('execution-text-link.spec.mjs')).length, 19);
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

test('real-host config waits for its own launcher, never for whichever server holds the port', async t => {
    const previous = process.env.PM_COMFY_ROOT;
    process.env.PM_COMFY_ROOT = '/unused-pinned-checkout';
    t.after(() => { if (previous === undefined) delete process.env.PM_COMFY_ROOT; else process.env.PM_COMFY_ROOT = previous; });
    const { default: config } = await import('../../tools/vendor/comfyui.config.mjs');
    const [server] = config.webServer;
    assert.equal(server.url, undefined);
    assert.equal(server.port, undefined);
    assert.equal(server.reuseExistingServer, false);
    const ready = server.wait.stdout.exec('Starting server\nPM_COMFY_HOST_READY base-directory=/tmp/comfyassets-host-a b\r\nlater');
    assert.equal(ready.groups.pm_comfy_host_base, '/tmp/comfyassets-host-a b');
    assert.equal(server.wait.stdout.exec('To see the GUI go to: http://127.0.0.1:8189'), null);
});
