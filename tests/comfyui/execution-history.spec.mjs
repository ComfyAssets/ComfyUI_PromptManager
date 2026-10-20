import { randomUUID } from 'node:crypto';
import { expect } from '../../tools/vendor/node_modules/@playwright/test/index.mjs';
import { test, readExecutionRuns } from './execution-fixture.mjs';
import { installHostHooks } from './host-fixture.mjs';

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

function recipeGraph({ text = 'context positive', negative = '', kind = 'PromptManagerPair', prepend = '', append = '', dynamic = false } = {}) {
    const graph = {
        '10': { class_type: 'PMHistoryInputs', inputs: {} },
        '20': { class_type: kind, inputs: { text: dynamic ? ['11', 0] : text,
            prepend_text: prepend, append_text: append, ...(kind === 'PromptManagerText' ? {} : { clip: ['10', 0] }),
            ...(kind === 'PromptManagerPair' ? { negative_text: negative } : {}) } },
        '30': { class_type: 'CLIPTextEncode', inputs: { text: negative, clip: ['10', 0] } },
        '40': { class_type: 'KSampler', inputs: { model: ['10', 1], latent_image: ['10', 2],
            positive: ['20', 0], negative: kind === 'PromptManagerPair' ? ['20', 1] : ['30', 0],
            seed: 1, steps: 1, cfg: 1, sampler_name: 'euler', scheduler: 'normal', denoise: 1 } },
        '50': { class_type: 'PMHistoryOutput', inputs: { value: ['40', 0] } },
    };
    if (kind === 'PromptManagerText') {
        graph['31'] = { class_type: 'CLIPTextEncode', inputs: { text: ['20', 0], clip: ['10', 0] } };
        graph['40'].inputs.positive = ['31', 0];
    }
    if (dynamic) graph['11'] = { class_type: 'PMHistoryCached', inputs: { value: text } };
    return graph;
}

async function capture(execution, id, outcome = 'succeeded') {
    const [run] = await rows(execution, id, [outcome]);
    let result;
    await expect.poll(async () => {
        result = await readExecutionRuns(execution.databasePath, id);
        return result.captures[run.run_id].state;
    }, { timeout: 10000, message: 'Background writer persisted context finalization' }).not.toBe('not_captured');
    const response = await execution.request.get(`/prompt_manager/history/runs/${run.run_id}`);
    expect(response.ok()).toBe(true);expect(response.headers()['cache-control']).toBe('no-store');
    const detail = await response.json();
    const fields = contexts => contexts.map(({ anchor_id, context_id, positive, negative, evidence }) => ({ anchor_id, context_id, positive, negative, evidence }));
    expect(fields(detail.contexts)).toEqual(fields(result.captures[run.run_id].contexts));
    expect(detail.run).toMatchObject({ run_id: run.run_id, outcome, queue_item_id: String(run.queue_item_id) });
    return result.captures[run.run_id];
}

test.describe('public chronological history with the actual acknowledged host bridge', () => {
    test.beforeEach(async ({ execution }) => { expect(execution.databasePath).toBeTruthy(); });
    installHostHooks(test, ['PromptManagerPair']);

    test('three actual attempts expose two contexts and send exact frozen roles through both renderer switches and reload', async ({ execution, page, context }, testInfo) => {
        test.setTimeout(120000);
        const positive = `public chronology ${testInfo.project.name} ${randomUUID()}\n猫`, attempts = [];
        for (const negative of ['negative A', 'negative A', 'negative B']) {
            const graph = recipeGraph({ text: positive, negative, prepend: ' pre ', append: ' post ' });
            graph['60'] = { class_type: 'PMHistoryImages', inputs: { value: 128, batch: 2, latent: ['40', 0] } };
            graph['70'] = { class_type: 'SaveImage', inputs: { images: ['60', 0], filename_prefix: 'chronology/public' } };
            graph['71'] = { class_type: 'PreviewImage', inputs: { images: ['60', 0] } };
            const id = await submit(execution.request, graph);
            await history(execution.request, id);const frozen = await capture(execution, id);
            const persisted = await readExecutionRuns(execution.databasePath, id);attempts.push({ row: persisted.rows[0], context: frozen.contexts[0] });
        }
        const positiveId = attempts[0].context.positive_prompt_id;
        const response = await execution.request.get(`/prompt_manager/library/${positiveId}/history`);expect(response.ok()).toBe(true);const window = await response.json();
        expect(window.recorded_run_count).toBe(3);expect(window.items.map(item => item.run_id)).toEqual(attempts.map(item => item.row.run_id));
        expect(window.context_summaries).toHaveLength(2);expect(new Set(attempts.map(item => item.context.positive.raw))).toEqual(new Set([positive]));
        expect(attempts.map(item => item.context.evidence.positive.effective.source)).toEqual(['runtime_return', 'host_cache_output', 'runtime_return']);
        const workspace = await context.newPage();const errors = [];workspace.on('pageerror', error => errors.push(error.message));
        try {
            const destination = await page.evaluate(async () => {const { app } = await import('/scripts/app.js');const node = LiteGraph.createNode('PromptManagerPair');app.graph.add(node);return node.id;});
            for (const renderer of [testInfo.project.metadata.initialRenderer, !testInfo.project.metadata.initialRenderer, testInfo.project.metadata.initialRenderer]) {
                await page.evaluate(async renderer => {const { app } = await import('/scripts/app.js');await app.ui.settings.setSettingValue('Comfy.VueNodes.Enabled', renderer);await app.loadGraphData(app.graph.serialize());}, renderer);
                await expect.poll(() => page.evaluate(() => LiteGraph.vueNodesMode)).toBe(renderer);
                await workspace.goto(`/prompt_manager/workspace#/library/${positiveId}`);
                const recorded = workspace.getByRole('region', { name: 'Recorded executions', exact: true });await expect(recorded.locator('[data-context-branch]')).toHaveCount(2);
                await recorded.locator(`button[data-run-id="${attempts[0].row.run_id}"]`).click();
                const run = workspace.getByRole('dialog', { name: 'Recorded run', exact: true });await expect(run).toBeVisible();
                await expect(run.locator('[data-receipt]')).toHaveCount(4);
                await run.getByRole('button', { name: 'View current file', exact: true }).first().click();
                const viewer = workspace.getByRole('dialog', { name: 'Image preview', exact: true });await expect(viewer).toBeVisible();
                await expect(viewer.locator('.viewer-canvas img')).toBeVisible();
                await workspace.keyboard.press('Escape');await expect(run).toBeVisible();
                const frozen = attempts[0].context;
                for (const mode of ['Raw', 'Effective']) {
                    await run.getByRole('button', { name: mode, exact: true }).click();
                    for (const [role, value] of [['Positive', frozen.positive[mode.toLowerCase()]], ['Negative', frozen.negative[mode.toLowerCase()]]]) expect(await run.getByRole('region', { name: 'Frozen run prompt texts', exact: true }).getByRole('region', { name: `${role} prompt`, exact: true }).locator('pre').evaluate(node => node.textContent)).toBe(value);
                    await page.evaluate(async id => {const { app } = await import('/scripts/app.js');const node = app.graph.getNodeById(id);node.widgets.find(w => w.name === 'text').value = 'Before send';window.chronologyEffects = { queue: 0, load: 0 };window.chronologyOriginals = {};for (const [method, key] of [['queuePrompt', 'queue'], ['loadGraphData', 'load']]) {const original = app[method];window.chronologyOriginals[method] = original;app[method] = function(...args) {window.chronologyEffects[key]++;return original.apply(this, args);};}}, destination);
                    try {
                        await run.getByRole('region', { name: 'Frozen run prompt texts', exact: true }).getByRole('button', { name: 'Send prompt pair', exact: true }).click();const sender = workspace.getByRole('dialog', { name: 'Send to ComfyUI', exact: true });
                        const hosts = sender.getByRole('combobox', { name: 'ComfyUI window', exact: true });await expect(hosts.locator('option')).toHaveCount(2);await hosts.selectOption({ index: 1 });
                        await sender.getByRole('combobox', { name: 'Destination node', exact: true }).selectOption({ index: 1 });await sender.getByRole('button', { name: 'Preview changes', exact: true }).click();
                        await sender.getByRole('button', { name: 'Apply to ComfyUI', exact: true }).click();await expect(sender).toContainText('Sent to ComfyUI.');
                        const applied = await page.evaluate(async id => {const { app } = await import('/scripts/app.js');const node = app.graph.getNodeById(id);return { positive: node.widgets.find(w => w.name === 'text').value, negative: node.widgets.find(w => w.name === 'negative_text').value, effects: window.chronologyEffects };}, destination);
                        expect(applied).toEqual({ positive: frozen.positive[mode.toLowerCase()], negative: frozen.negative[mode.toLowerCase()], effects: { queue: 0, load: 0 } });
                        await workspace.keyboard.press('Escape');await expect(run).toBeVisible();
                    } finally {await page.evaluate(async () => {const { app } = await import('/scripts/app.js');Object.assign(app, window.chronologyOriginals);});}
                }
                await workspace.keyboard.press('Escape');await expect(recorded).toBeVisible();
            }
            const versions = await (await execution.request.get('/system_stats')).json();await testInfo.attach('chronology-runtime.json', { body: JSON.stringify(versions.system, null, 2), contentType: 'application/json' });
            expect(errors).toEqual([]);
        } finally {await workspace.close();}
    });
});

test('a cached dynamic unknown positive stays readable by run UUID and outside the known raw Library scope', async ({ execution }) => {
    const text = `unknown positive ${randomUUID()}`, graph = recipeGraph({ text, dynamic: true });
    const first = await submit(execution.request, graph);await history(execution.request, first);const known = (await capture(execution, first)).contexts[0];
    const second = await submit(execution.request, graph);await history(execution.request, second);const unknown = (await capture(execution, second)).contexts[0];
    expect(unknown.positive.raw).toBeNull();expect(unknown.positive.effective).toBe(known.positive.effective);
    const result = await readExecutionRuns(execution.databasePath, second), runId = result.rows[0].run_id;
    const detail = await execution.request.get(`/prompt_manager/history/runs/${runId}`);expect(detail.ok()).toBe(true);expect((await detail.json()).contexts[0].positive.raw).toBeNull();
    const response = await execution.request.get(`/prompt_manager/library/${known.positive_prompt_id}/history`);expect(response.ok()).toBe(true);const window = await response.json();
    expect(window.recorded_run_count).toBe(1);expect(window.items.map(item => item.run_id)).not.toContain(runId);
});

test('actual PM A,A,B jobs persist two combinations and one identical positive snapshot', async ({ execution }) => {
    const captures = [];
    const positiveSnapshotIds = [];
    for (const negative of ['negative A', 'negative A', 'negative B']) {
        const id = await submit(execution.request, recipeGraph({ negative }));
        await history(execution.request, id);
        captures.push(await capture(execution, id));
        const persisted = await readExecutionRuns(execution.databasePath, id);
        positiveSnapshotIds.push(persisted.snapshotIds[persisted.rows[0].run_id][0].positive_snapshot_id);
    }
    expect(new Set(positiveSnapshotIds).size).toBe(1);
    expect(captures.map(value => value.state)).toEqual(['complete', 'complete', 'complete']);
    const contexts = captures.map(value => value.contexts[0]);
    expect(new Set(contexts.map(value => value.context_id)).size).toBe(2);
    expect(new Set(contexts.map(value => JSON.stringify(value.positive))).size).toBe(1);
    expect(contexts[0].evidence.positive.effective.source).toBe('runtime_return');
    expect(contexts[1].evidence.positive.effective.source).toBe('host_cache_output');
    expect(contexts[1].evidence.positive.raw.source).toBe('graph_literal');
    expect(contexts[1].evidence.role_provenance).toBe('inferred');
    expect(contexts[2].negative.raw).toBe('negative B');
});

test('fresh registered PM variants preserve exact raw whitespace and known empty negative', async ({ execution }) => {
    for (const kind of ['PromptManager', 'PromptManagerFocused', 'PromptManagerSplit', 'PromptManagerPair', 'PromptManagerText']) {
        const graph = recipeGraph({ kind, text: `  ${kind} e\u0301\n`, prepend: ' pre \t', append: '\n post ' });
        const id = await submit(execution.request, graph);
        await history(execution.request, id);
        const value = (await capture(execution, id)).contexts[0];
        expect(value.positive).toEqual({ raw: `  ${kind} e\u0301\n`, prepend: ' pre \t', append: '\n post ', effective: `pre   ${kind} e\u0301\n post` });
        expect(value.negative.raw).toBe('');
        expect(value.negative.effective).toBe('');
        expect(value.evidence.positive.raw).toMatchObject({ provenance: 'recorded', source: 'runtime_return' });
    }
});

test('fully cached text uses old effective output after external trigger configuration changes', async ({ execution }) => {
    const request = execution.request;
    const configure = suffix => request.post('/pm_execution_fixture/injection', { data: { suffix } });
    try {
        expect((await configure(' trigger-A')).ok()).toBe(true);
        const graph = recipeGraph({ text: 'cached trigger recipe', negative: 'trigger negative' });
        const first = await submit(request, graph);
        await history(request, first);
        const original = (await capture(execution, first)).contexts[0];
        expect(original.positive.effective).toBe('cached trigger recipe trigger-A');
        expect((await configure(' trigger-B')).ok()).toBe(true);
        const second = await submit(request, graph);
        const item = await history(request, second);
        expect(item.status.messages.find(([kind]) => kind === 'execution_cached')[1].nodes).toEqual(expect.arrayContaining(['20', '40', '50']));
        const repeated = (await capture(execution, second)).contexts[0];
        expect(repeated.context_id).toBe(original.context_id);
        expect(repeated.positive.effective).toBe(original.positive.effective);
        expect(repeated.evidence.positive.effective.source).toBe('host_cache_output');
    } finally { expect((await configure('')).ok()).toBe(true); }
});

test('crossed Pair and Text negative roles remain independent declared contexts', async ({ execution }) => {
    const graph = recipeGraph({ text: 'pair one positive', negative: 'pair one negative' });
    graph['21'] = { class_type: 'PromptManagerPair', inputs: { text: 'pair two positive', negative_text: 'pair two negative', clip: ['10', 0] } };
    graph['22'] = { class_type: 'PromptManagerText', inputs: { text: 'text negative role' } };
    graph['32'] = { class_type: 'CLIPTextEncode', inputs: { text: ['22', 0], clip: ['10', 0] } };
    graph['40'].inputs.negative = ['21', 1];
    graph['41'] = structuredClone(graph['40']);
    graph['41'].inputs.positive = ['21', 0];
    graph['41'].inputs.negative = ['32', 0];
    graph['51'] = { class_type: 'PMHistoryOutput', inputs: { value: ['41', 0] } };
    const id = await submit(execution.request, graph);
    await history(execution.request, id);
    const result = await capture(execution, id);
    expect(result.contexts.map(value => [value.anchor_id, value.positive.raw, value.negative.raw])).toEqual([
        ['40', 'pair one positive', 'pair two negative'], ['41', 'pair two positive', 'text negative role'],
    ]);
    for (const value of result.contexts) expect(value.evidence.negative.effective.source).toBe('runtime_return');
});

test('successful source capture survives downstream failure and real interruption', async ({ execution }) => {
    const request = execution.request;
    for (const mode of ['failure', 'gate']) {
        const graph = recipeGraph({ text: `source before ${mode}` });
        graph['50'].inputs.mode = mode;
        if (mode === 'gate') await control(request, 'hold');
        try {
            const id = await submit(request, graph);
            if (mode === 'gate') {
                await entered(request);
                expect((await request.post('/interrupt', { data: {} })).ok()).toBe(true);
            }
            await history(request, id, mode === 'gate' ? 'execution_interrupted' : 'execution_error');
            const result = await capture(execution, id, mode === 'gate' ? 'interrupted' : 'failed');
            expect(result.contexts[0].positive.effective).toBe(`source before ${mode}`);
            expect(result.contexts[0].evidence.positive.effective.source).toBe('runtime_return');
        } finally { await control(request, 'release'); }
    }
});

test('cached dynamic raw stays unknown while literal affixes and effective cache text survive', async ({ execution }) => {
    const graph = recipeGraph({ dynamic: true, text: 'runtime dynamic recipe', prepend: ' pre ', append: ' post ' });
    const first = await submit(execution.request, graph);
    await history(execution.request, first);
    expect((await capture(execution, first)).contexts[0].positive.raw).toBe('runtime dynamic recipe');
    const second = await submit(execution.request, graph);
    await history(execution.request, second);
    const value = (await capture(execution, second)).contexts[0];
    expect(value.positive).toEqual({ raw: null, prepend: ' pre ', append: ' post ', effective: 'pre runtime dynamic recipe post' });
    expect(value.evidence.positive.raw.provenance).toBe('unknown');
    expect(value.evidence.positive.effective.source).toBe('host_cache_output');
});

test('unsupported conditioning transforms and duplicate list invocations preserve uncertainty', async ({ execution }) => {
    const transformed = recipeGraph({ text: 'unsupported transformed recipe' });
    transformed['35'] = { class_type: 'PMHistoryTransform', inputs: { conditioning: ['20', 0] } };
    transformed['40'].inputs.positive = ['35', 0];
    const unsupported = await submit(execution.request, transformed);
    await history(execution.request, unsupported);
    const unknown = await capture(execution, unsupported);
    expect(unknown.state).toBe('partial');
    expect(unknown.contexts[0].positive.effective).toBeNull();
    const graph = recipeGraph({ text: 'duplicate runtime list' });
    graph['11'] = { class_type: 'PMHistoryList', inputs: { value: 'duplicate runtime list' } };
    graph['20'].inputs.text = ['11', 0];
    const id = await submit(execution.request, graph);
    await history(execution.request, id);
    const result = await capture(execution, id);
    expect(result.state).toBe('ambiguous');
    expect(result.reason).toBe('multiple_samples');
    expect(result.contexts[0].positive.effective).toBeNull();
});

test('real executor Classic LRU RAM and disabled caches preserve honest evidence and usage', async ({ execution }) => {
    const request = execution.request;
    for (const mode of ['CLASSIC', 'LRU', 'RAM_PRESSURE', 'NONE']) {
        expect((await request.post('/pm_execution_fixture/cache', { data: { mode } })).ok()).toBe(true);
        const graph = recipeGraph({ text: `cache mode ${mode}`, negative: `cache mode negative ${mode}` });
        const results = [];
        for (let index = 0; index < 2; index++) {
            const id = await submit(request, graph);
            await history(request, id);
            results.push(await capture(execution, id));
            const persisted = await readExecutionRuns(execution.databasePath, id);
            const [run] = persisted.rows;
            expect(persisted.usage[run.run_id]).toEqual([
                { role: 'positive', run_count: index + 1 },
                { role: 'negative', run_count: 0 },
                { role: 'pair', run_count: index + 1 },
            ]);
        }
        expect(results[0].state).toBe('complete');
        expect(results[1].contexts[0].context_id).toBe(results[0].contexts[0].context_id);
        expect(results[1].contexts[0].evidence.positive.effective.source).toBe(mode === 'NONE' ? 'runtime_return' : 'host_cache_output');
    }
});
