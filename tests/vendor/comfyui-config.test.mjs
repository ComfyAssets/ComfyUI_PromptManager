import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

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
