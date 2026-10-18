import { randomUUID } from 'node:crypto';
import { expect } from '../../tools/vendor/node_modules/@playwright/test/index.mjs';
import { test, readExecutionRuns } from './execution-fixture.mjs';

async function resetCache({ execution }) {
    expect((await execution.request.post('/pm_execution_fixture/cache', { data: { mode: 'RAM_PRESSURE' } })).ok()).toBe(true);
}

test.beforeEach(resetCache);
test.afterAll(resetCache);

function images(value, batch = 1) {
    return {
        '1': { class_type: 'PMHistoryImages', inputs: { value, batch } },
        '2': { class_type: 'SaveImage', inputs: { images: ['1', 0], filename_prefix: 'output-evidence/nested/image' } },
        '3': { class_type: 'PreviewImage', inputs: { images: ['1', 0] } },
    };
}

async function submit(execution, prompt, id = randomUUID()) {
    const response = await execution.request.post('/prompt', { data: { prompt, prompt_id: id } });
    expect(response.ok()).toBe(true);
    expect((await response.json()).prompt_id).toBe(id);
    return id;
}

async function persisted(execution, id, outcomes) {
    let result;
    await expect.poll(async () => {
        result = await readExecutionRuns(execution.databasePath, id);
        return { outcomes: result.rows.map(row => row.outcome),
            states: result.rows.map(row => result.outputs[row.run_id].state) };
    }, { timeout: 15000, message: 'Exact attempts and final output evidence reach SQLite' }).toEqual({
        outcomes, states: outcomes.map(() => 'complete'),
    });
    for (const row of result.rows) {
        const detailResponse = await execution.request.get(`/prompt_manager/history/runs/${row.run_id}`);
        expect(detailResponse.ok()).toBe(true);
        const detail = await detailResponse.json();
        expect(detail.run).toMatchObject({ run_id: row.run_id, outcome: row.outcome, queue_item_id: String(row.queue_item_id) });
        const response = await execution.request.get(`/prompt_manager/history/runs/${row.run_id}/outputs?epoch=${detail.history_epoch}`);
        expect(response.ok()).toBe(true);expect(response.headers()['cache-control']).toBe('no-store');
        const publicOutputs = await response.json(), captured = result.outputs[row.run_id];
        expect(publicOutputs.capture).toMatchObject({ state: captured.state, reason: captured.reason, source: captured.source });
        expect(publicOutputs.retained_occurrence_count).toBe(captured.references.length);
        expect(publicOutputs.items.map(item => ({ ...item.occurrence, ...item.reference })).map(({ run_id, ...reference }) => reference)).toEqual(
            captured.references.map(({ output_node_id, image_ordinal, folder_type, subfolder, filename, path_style, root_key }) =>
                ({ output_node_id, image_ordinal, folder_type, subfolder, filename, path_style, root_key })));
        for (const item of publicOutputs.items) {
            expect(item).toMatchObject({ file_identity: 'unverified', newly_generated: 'unknown', context_membership: 'unknown' });
            expect(item.current_candidate.evidence_class).toBe('locator_candidate');
        }
    }
    return result;
}

async function finalHistory(execution, id) {
    const response = await execution.request.get(`/history/${id}`);
    expect(response.ok()).toBe(true);
    const item = (await response.json())[id];
    expect(item.prompt[1]).toBe(id);
    return item;
}

function expectHistory(capture, history) {
    const reported = Object.entries(history.outputs).flatMap(([node, output]) =>
        (output.images || []).map((image, ordinal) => ({ output_node_id: node, image_ordinal: ordinal,
            folder_type: image.type, subfolder: image.subfolder, filename: image.filename })));
    reported.sort((a, b) => a.output_node_id.localeCompare(b.output_node_id) || a.image_ordinal - b.image_ordinal);
    expect(capture).toMatchObject({ state: 'complete', reason: reported.length ? 'reported' : 'none_reported',
        source: 'host_final_history', format_version: 1 });
    expect(capture.references.map(({ output_node_id, image_ordinal, folder_type, subfolder, filename }) =>
        ({ output_node_id, image_ordinal, folder_type, subfolder, filename }))).toEqual(reported);
    for (const reference of capture.references) {
        expect(reference).toMatchObject({ path_style: 'posix', file_identity: 'unverified',
            newly_generated: 'unknown', context_membership: 'unknown', transient: reference.folder_type === 'temp' });
        expect(reference.root_key).toMatch(/^[a-f0-9]{64}$/);
    }
}

async function gate(execution, action) {
    expect((await execution.request.post(`/pm_execution_fixture/gate/${action}`)).ok()).toBe(true);
}

async function entered(execution) {
    await expect.poll(async () => (await (await execution.request.get('/pm_execution_fixture/gate')).json()).entered,
        { timeout: 10000 }).toBe(true);
}

function downstream(value, mode) {
    const graph = images(value);
    delete graph['3'];
    graph['4'] = { class_type: 'PMHistoryImageOutput', inputs: { images: ['2', 0], mode } };
    return graph;
}

test('headless stock SaveImage and PreviewImage retain A,A,B batches under a reused public UUID', async ({ execution }) => {
    const id = randomUUID();
    const graph = images(31, 2);
    let result;
    for (const [index, value] of [31, 31, 73].entries()) {
        graph['1'].inputs.value = value;
        await submit(execution, graph, id);
        result = await persisted(execution, id, Array(index + 1).fill('succeeded'));
        const item = await finalHistory(execution, id);
        const row = result.rows[index];
        expectHistory(result.outputs[row.run_id], item);
        expect(result.outputs[row.run_id].references).toHaveLength(4);
        expect(result.captures[row.run_id].contexts).toEqual([]);
        expect(result.usage[row.run_id]).toEqual([]);
        if (index === 1) expect(item.status.messages.find(([kind]) => kind === 'execution_cached')[1].nodes).toEqual(expect.arrayContaining(['1', '2', '3']));
    }
    const attempts = result.rows;
    expect(new Set(attempts.map(row => row.run_id)).size).toBe(3);
    expect(new Set(attempts.map(row => row.queue_item_id)).size).toBe(3);
    const captures = attempts.map(row => result.outputs[row.run_id]);
    expect(captures.map(capture => capture.state)).toEqual(['complete', 'complete', 'complete']);
    expect(captures[1].references).toEqual(captures[0].references);
    expect(captures[2].references).not.toEqual(captures[0].references);
    expect(captures[0].references[0].context_membership).toBe('unknown');
    expect(captures[0].references.filter(reference => reference.transient)).toHaveLength(2);
});

test('stock image evidence survives disabled PNG metadata', async ({ execution }) => {
    const enabled = images(44);
    delete enabled['3'];
    const enabledId = await submit(execution, enabled);
    const enabledResult = await persisted(execution, enabledId, ['succeeded']);
    const enabledReference = enabledResult.outputs[enabledResult.rows[0].run_id].references[0];
    const withMetadata = await execution.request.get('/view', { params: {
        filename: enabledReference.filename, subfolder: enabledReference.subfolder, type: enabledReference.folder_type,
    } });
    expect(withMetadata.ok()).toBe(true);
    expect((await withMetadata.body()).includes(Buffer.from('tEXtprompt'))).toBe(true);
    expect((await execution.request.post('/pm_execution_fixture/metadata', { data: { disabled: true } })).ok()).toBe(true);
    try {
        const graph = images(45);
        delete graph['3'];
        const id = await submit(execution, graph);
        const result = await persisted(execution, id, ['succeeded']);
        const capture = result.outputs[result.rows[0].run_id];
        expectHistory(capture, await finalHistory(execution, id));
        expect(capture.references).toHaveLength(1);
        const { filename, subfolder, folder_type: type } = capture.references[0];
        const response = await execution.request.get('/view', { params: { filename, subfolder, type } });
        expect(response.ok()).toBe(true);
        const png = await response.body();
        expect(png.subarray(1, 4).toString()).toBe('PNG');
        expect(png.includes(Buffer.from('tEXtprompt'))).toBe(false);
    } finally {
        expect((await execution.request.post('/pm_execution_fixture/metadata', { data: { disabled: false } })).ok()).toBe(true);
    }
});

test('text-only output reports a complete empty image set', async ({ execution }) => {
    const id = await submit(execution, { '1': { class_type: 'PMHistoryCached', inputs: { value: 'text has no image locator' } } });
    const result = await persisted(execution, id, ['succeeded']);
    const capture = result.outputs[result.rows[0].run_id];
    expectHistory(capture, await finalHistory(execution, id));
    expect(capture.references).toEqual([]);
    expect(capture.reason).toBe('none_reported');
});

test('batched outputs preserve PM usage while context membership stays unknown', async ({ execution }) => {
    const graph = images(57, 2);
    graph['10'] = { class_type: 'PMHistoryInputs', inputs: {} };
    graph['20'] = { class_type: 'PromptManagerPair', inputs: { clip: ['10', 0], text: `output usage ${randomUUID()}`,
        negative_text: `negative ${randomUUID()}`, prepend_text: '', append_text: '' } };
    graph['30'] = { class_type: 'KSampler', inputs: { model: ['10', 1], latent_image: ['10', 2],
        positive: ['20', 0], negative: ['20', 1], seed: 1, steps: 1, cfg: 1, sampler_name: 'euler', scheduler: 'normal', denoise: 1 } };
    graph['1'].inputs.latent = ['30', 0];
    const id = await submit(execution, graph);
    const first = await persisted(execution, id, ['succeeded']);
    const runId = first.rows[0].run_id;
    expectHistory(first.outputs[runId], await finalHistory(execution, id));
    expect(first.outputs[runId].references).toHaveLength(4);
    expect(first.captures[runId].contexts).toHaveLength(1);
    expect(first.usage[runId]).toEqual([{ role: 'positive', run_count: 1 }, { role: 'negative', run_count: 0 }, { role: 'pair', run_count: 1 }]);
    await submit(execution, graph, id);
    const second = await persisted(execution, id, ['succeeded', 'succeeded']);
    expect(second.outputs[second.rows[1].run_id].references).toEqual(first.outputs[runId].references);
    expect(second.usage[runId]).toEqual([{ role: 'positive', run_count: 2 }, { role: 'negative', run_count: 0 }, { role: 'pair', run_count: 2 }]);
});

test('failed work after stock SaveImage preserves exactly the final reported partial-run outputs', async ({ execution }) => {
    const id = await submit(execution, downstream(81, 'failure'));
    const result = await persisted(execution, id, ['failed']);
    const item = await finalHistory(execution, id);
    expect(item.status.messages.some(([kind]) => kind === 'execution_error')).toBe(true);
    expectHistory(result.outputs[result.rows[0].run_id], item);
    expect(result.outputs[result.rows[0].run_id].references).toHaveLength(1);
});

test('interruption after stock SaveImage preserves exactly the final reported outputs', async ({ execution }) => {
    await gate(execution, 'hold');
    try {
        const id = await submit(execution, downstream(93, 'gate'));
        await entered(execution);
        expect((await execution.request.post('/interrupt', { data: {} })).ok()).toBe(true);
        const result = await persisted(execution, id, ['interrupted']);
        const item = await finalHistory(execution, id);
        expect(item.status.messages.some(([kind]) => kind === 'execution_interrupted')).toBe(true);
        expectHistory(result.outputs[result.rows[0].run_id], item);
        expect(result.outputs[result.rows[0].run_id].references).toHaveLength(1);
    } finally { await gate(execution, 'release'); }
});

test('completed image before pending gated work is not finalized early', async ({ execution }) => {
    await gate(execution, 'hold');
    try {
        const id = await submit(execution, downstream(107, 'gate'));
        await entered(execution);
        let pending;
        await expect.poll(async () => {
            pending = await readExecutionRuns(execution.databasePath, id);
            return pending.rows.map(row => row.outcome);
        }, { timeout: 10000 }).toEqual(['observed']);
        expect(pending.outputs[pending.rows[0].run_id]).toMatchObject({ state: 'not_captured', references: [] });
        expect((await (await execution.request.get(`/history/${id}`)).json())[id]).toBeUndefined();
        await gate(execution, 'release');
        const result = await persisted(execution, id, ['succeeded']);
        expectHistory(result.outputs[result.rows[0].run_id], await finalHistory(execution, id));
        expect(result.outputs[result.rows[0].run_id].references).toHaveLength(1);
    } finally { await gate(execution, 'release'); }
});

test('stock output capture survives both renderer switches and workflow reload', async ({ execution, page, context }, testInfo) => {
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
        return !!app.graph && !!globalThis.LiteGraph?.registered_node_types?.PMHistoryImages;
    });
    await expect.poll(() => page.evaluate(() => LiteGraph.vueNodesMode)).toBe(testInfo.project.metadata.initialRenderer);
    await page.evaluate(async () => {
        const { app } = await import('/scripts/app.js');
        app.graph.clear();
        const source = LiteGraph.createNode('PMHistoryImages');
        const saved = LiteGraph.createNode('SaveImage');
        const preview = LiteGraph.createNode('PreviewImage');
        for (const node of [source, saved, preview]) app.graph.add(node);
        source.widgets.find(widget => widget.name === 'value').value = 119;
        source.widgets.find(widget => widget.name === 'batch').value = 1;
        saved.widgets.find(widget => widget.name === 'filename_prefix').value = 'renderer-evidence/image';
        source.connect(0, saved, 0);
        source.connect(0, preview, 0);
    });
    const captures = [];
    for (const renderer of [testInfo.project.metadata.initialRenderer, !testInfo.project.metadata.initialRenderer, testInfo.project.metadata.initialRenderer]) {
        await page.evaluate(async renderer => {
            const { app } = await import('/scripts/app.js');
            await app.ui.settings.setSettingValue('Comfy.VueNodes.Enabled', renderer);
            await app.loadGraphData(app.graph.serialize());
        }, renderer);
        await expect.poll(() => page.evaluate(() => LiteGraph.vueNodesMode)).toBe(renderer);
        const graph = await page.evaluate(async () => (await (await import('/scripts/app.js')).app.graphToPrompt()).output);
        expect(Object.values(graph).map(node => node.class_type).sort()).toEqual(['PMHistoryImages', 'PreviewImage', 'SaveImage']);
        const id = await submit(execution, graph);
        const result = await persisted(execution, id, ['succeeded']);
        const capture = result.outputs[result.rows[0].run_id];
        expectHistory(capture, await finalHistory(execution, id));
        expect(capture.references).toHaveLength(2);
        captures.push(capture);
    }
    expect(captures[1].references).toEqual(captures[0].references);
    expect(captures[2].references).toEqual(captures[0].references);
    expect(errors).toEqual([]);
});
