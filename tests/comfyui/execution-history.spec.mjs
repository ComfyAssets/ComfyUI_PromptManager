import { randomUUID } from 'node:crypto';
import { expect } from '../../tools/vendor/node_modules/@playwright/test/index.mjs';
import { test, readExecutionRuns } from './execution-fixture.mjs';

const sensitive = ['PM_SECRET_PROMPT_209', 'PM_SECRET_TOKEN_209', 'PM_SECRET_TRACE_209'];
const cached = value => ({ '1': { class_type: 'PMHistoryCached', inputs: { value } } });
const gate = value => ({ '1': { class_type: 'PMHistoryGate', inputs: { value } } });

async function submit(request, prompt, promptId = randomUUID()) {
    const response = await request.post('/prompt', { data: { prompt, prompt_id: promptId,
        extra_data: { token: sensitive[1], extra_pnginfo: { private: sensitive[2] } } } });
    expect(response.ok()).toBe(true);
    expect((await response.json()).prompt_id).toBe(promptId);
    return promptId;
}

async function history(request, jobId, terminal = 'execution_success') {
    let item;
    await expect.poll(async () => {
        const response = await request.get(`/history/${jobId}`);
        expect(response.ok()).toBe(true);
        item = (await response.json())[jobId];
        return item?.status?.messages?.some(([kind]) => kind === terminal) || false;
    }, { timeout: 15000 }).toBe(true);
    expect(item.prompt[1]).toBe(jobId);
    return item;
}

async function rows(execution, jobId, outcomes) {
    let result;
    await expect.poll(async () => {
        result = await readExecutionRuns(execution.databasePath, jobId);
        return { schemaAvailable: result.schemaAvailable, outcomes: result.rows.map(row => row.outcome) };
    }, { timeout: 10000, message: `Persisted exact-job outcomes for ${jobId}` }).toEqual({ schemaAvailable: true, outcomes });
    for (const sentinel of sensitive) expect(JSON.stringify(result.rows)).not.toContain(sentinel);
    for (const row of result.rows) {
        expect(row.observed_dequeue_at).toBeGreaterThan(0);
        expect(row.observed_callback_at).toBeGreaterThanOrEqual(row.observed_dequeue_at);
        expect(row.executor_started_at).toBeGreaterThan(0);
        expect(row.terminal_event_at).toBeGreaterThanOrEqual(row.executor_started_at);
    }
    return result.rows;
}

async function control(request, action) {
    expect((await request.post(`/pm_execution_fixture/gate/${action}`)).ok()).toBe(true);
}

async function entered(request) {
    await expect.poll(async () => (await (await request.get('/pm_execution_fixture/gate')).json()).entered,
        { timeout: 10000 }).toBe(true);
}

test('headless actual queue execution persists without a browser or WebSocket client', async ({ execution }) => {
    const id = await submit(execution.request, cached(`headless ${sensitive[0]}`));
    const item = await history(execution.request, id);
    expect(item.status.status_str).toBe('success');
    expect((await rows(execution, id, ['succeeded']))[0].outcome).toBe('succeeded');
});

test('cached reruns after failure survive renderer switches both ways and workflow reload', async ({ execution, page, context }, testInfo) => {
    const request = execution.request;
    await context.route('**/*', async route => {
        const path = new URL(route.request().url()).pathname.replace(/^\/api\//, '/');
        if (path === '/settings' && route.request().method() === 'GET') {
            const response = await route.fetch();
            return route.fulfill({ json: { ...await response.json(),
                'Comfy.VueNodes.Enabled': testInfo.project.metadata.initialRenderer,
                'Comfy.TutorialCompleted': true, 'Comfy.Workflow.Persist': false,
                'Comfy.Notification.ShowVersionUpdates': false } });
        }
        return route.continue();
    });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('status', { name: 'Loading ComfyUI' })).toBeHidden({ timeout: 30000 });
    await page.waitForFunction(async () => {
        const { app } = await import('/scripts/app.js');
        return !!app.graph && !!globalThis.LiteGraph?.registered_node_types?.PMHistoryCached;
    });
    await expect.poll(() => page.evaluate(() => LiteGraph.vueNodesMode)).toBe(testInfo.project.metadata.initialRenderer);
    await page.evaluate(async value => {
        const { app } = await import('/scripts/app.js');
        app.graph.clear();
        const node = LiteGraph.createNode('PMHistoryCached');
        app.graph.add(node);
        node.widgets.find(widget => widget.name === 'value').value = value;
    }, `cache ${sensitive[0]}`);
    const graph = await page.evaluate(async () => (await (await import('/scripts/app.js')).app.graphToPrompt()).output);
    expect(Object.values(graph).map(node => node.class_type)).toEqual(['PMHistoryCached']);
    const cachedNode = Object.keys(graph)[0];
    await history(request, await submit(request, graph));
    const failureGraph = { ...graph, '999': { class_type: 'PMHistoryFailure', inputs: { value: sensitive[2] } } };
    const failed = await submit(request, failureGraph);
    const failureHistory = await history(request, failed, 'execution_error');
    expect(failureHistory.status.messages.find(([kind]) => kind === 'execution_error')[1].exception_message).toContain(sensitive[2]);
    const failedRows = await rows(execution, failed, ['failed']);
    expect(failedRows[0]).toMatchObject({ outcome: 'failed', node_id: '999', node_type: 'PMHistoryFailure', exception_type: 'ValueError' });
    for (const renderer of [!testInfo.project.metadata.initialRenderer, testInfo.project.metadata.initialRenderer]) {
        await page.evaluate(async renderer => {
            const { app } = await import('/scripts/app.js');
            await app.ui.settings.setSettingValue('Comfy.VueNodes.Enabled', renderer);
            await app.loadGraphData(app.graph.serialize());
        }, renderer);
        await expect.poll(() => page.evaluate(() => LiteGraph.vueNodesMode)).toBe(renderer);
        const reloaded = await page.evaluate(async () => (await (await import('/scripts/app.js')).app.graphToPrompt()).output);
        const id = await submit(request, reloaded);
        const item = await history(request, id);
        expect(item.status.messages.find(([kind]) => kind === 'execution_cached')[1].nodes).toContain(cachedNode);
        expect((await rows(execution, id, ['succeeded']))[0].outcome).toBe('succeeded');
    }
    expect(errors).toEqual([]);
});

test('reusing a public UUID creates two distinct persisted attempts', async ({ execution }) => {
    const id = randomUUID();
    await history(execution.request, await submit(execution.request, cached('first reused UUID'), id));
    await rows(execution, id, ['succeeded']);
    const graph = cached('second reused UUID');
    await submit(execution.request, graph, id);
    await expect.poll(async () => {
        const response = await execution.request.get(`/history/${id}`);
        return (await response.json())[id]?.prompt[2]?.['1']?.inputs?.value;
    }, { timeout: 15000 }).toBe('second reused UUID');
    const attempts = await rows(execution, id, ['succeeded', 'succeeded']);
    expect(attempts).toHaveLength(2);
    expect(new Set(attempts.map(row => row.run_id)).size).toBe(2);
    expect(new Set(attempts.map(row => row.queue_item_id)).size).toBe(2);
    expect(new Set(attempts.map(row => row.session_id)).size).toBe(1);
});

test('real worker interruption persists an interrupted outcome', async ({ execution }) => {
    const request = execution.request;
    await control(request, 'hold');
    try {
        const id = await submit(request, gate(`interrupt ${sensitive[0]}`));
        await entered(request);
        expect((await request.post('/interrupt', { data: {} })).ok()).toBe(true);
        await history(request, id, 'execution_interrupted');
        expect((await rows(execution, id, ['interrupted']))[0].outcome).toBe('interrupted');
    } finally { await control(request, 'release'); }
});

test('rejected and removed-before-dequeue jobs create no persisted attempts', async ({ execution }) => {
    const request = execution.request;
    const rejected = randomUUID();
    const response = await request.post('/prompt', { data: { prompt_id: rejected,
        prompt: { '1': { class_type: 'PMHistoryMissing', inputs: {} } } } });
    expect(response.status()).toBe(400);
    await control(request, 'hold');
    try {
        const blocking = await submit(request, gate('hold worker until queued item is deleted'));
        await entered(request);
        const removed = await submit(request, cached('must never dequeue'));
        const queued = (await (await request.get('/queue')).json()).queue_pending;
        expect(queued.some(item => item[1] === removed)).toBe(true);
        expect((await request.post('/queue', { data: { delete: [removed] } })).ok()).toBe(true);
        expect((await (await request.get('/queue')).json()).queue_pending.some(item => item[1] === removed)).toBe(false);
        await control(request, 'release');
        await history(request, blocking);
        await rows(execution, blocking, ['succeeded']);
        for (const id of [rejected, removed]) {
            expect((await (await request.get(`/history/${id}`)).json())[id]).toBeUndefined();
            expect((await rows(execution, id, [])).length).toBe(0);
        }
    } finally { await control(request, 'release'); }
});
