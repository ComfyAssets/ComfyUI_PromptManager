import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';
import { access } from 'node:fs/promises';
import { join } from 'node:path';
import assert from 'node:assert/strict';
import { test as base, expect } from '../../tools/vendor/node_modules/@playwright/test/index.mjs';
import { launchExecutionHost } from './execution-host.mjs';
import runtime from '../../tools/vendor/comfyui-runtime.json' with { type: 'json' };

export { launchExecutionHost };
const run = promisify(execFile);
const reader = fileURLToPath(new URL('./read_execution_history.py', import.meta.url));

export async function readExecutionRuns(databasePath, jobId, sessionId) {
    const args = [reader, databasePath, jobId];
    if (sessionId !== undefined) args.push('--session-id', sessionId);
    const { stdout } = await run(process.env.PM_TEST_PYTHON || 'python', args, { timeout: 5000, maxBuffer: 1024 * 1024 });
    return JSON.parse(stdout);
}

export const test = base.extend({
    execution: [async ({ playwright }, use, workerInfo) => {
        const execution = await launchExecutionHost({
            logPath: fileURLToPath(new URL(`../../tools/vendor/test-results/executions/${workerInfo.project.name}-host.log`, import.meta.url)),
        });
        let request;
        try {
            request = await playwright.request.newContext({ baseURL: 'http://127.0.0.1:8196/' });
            await expect.poll(async () => {
                if (execution.host.child.exitCode !== null) throw new Error('Owned execution host exited before readiness');
                try { return (await request.get('/system_stats')).ok(); } catch { return false; }
            }, { timeout: 90000 }).toBe(true);
            const info = (await (await request.get('/system_stats')).json()).system;
            expect(info.comfyui_version).toBe(runtime.serverVersion);
            expect(info.pytorch_version).toBe(`${runtime.torchVersion}+cpu`);
            expect(info.required_frontend_version).toBe(runtime.frontendVersion);
            expect((await request.get('/prompt_manager/stats')).ok()).toBe(true);
            await access(execution.databasePath);
            await assert.rejects(access(join(execution.host.stateDirectory, 'custom_nodes/promptmanager/prompts.db')), { code: 'ENOENT' });
            await use({ ...execution, request });
        } finally {
            try { if (request) await request.post('/pm_execution_fixture/gate/release', { timeout: 5000 }); }
            finally { try { await request?.dispose(); } finally { await execution.dispose(); } }
        }
    }, { scope: 'worker', timeout: 120000 }],
});
