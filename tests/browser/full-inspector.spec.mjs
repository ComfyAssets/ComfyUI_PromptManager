import { test, expect } from '../../tools/vendor/node_modules/@playwright/test/index.mjs';

test('inspection facts survive actual API to browser transport', async ({ page, request }) => {
    await expect(await request.post('/__test__/reset', { data: { full_inspector: true } })).toBeOK();
    await page.goto('/prompt_manager/workspace');
    const result = await page.evaluate(async () => {
        if (typeof window.ComfyMetadata?.extractGenerationFacts !== 'function') return { missingFactsExport: true };
        const { inspectPath, toInspectionRecord } = await import('/prompt_manager/js/workspace/inspection-state.mjs');
        const { createApiClient } = window.ApiClient;
        const api = createApiClient();
        const collection = await api.get('/prompt_manager/images/output?mode=workspace&subfolder=inspector&limit=40');
        const records = {};
        for (const name of ['complete', 'ambiguous', 'workflow-only', 'control-heavy']) {
            const image = collection.images.find(entry => entry.root_index === 0 && entry.path === `inspector/${name}.png`);
            const envelope = await api.get(inspectPath(image));
            records[name] = toInspectionRecord(envelope, image, window.ComfyMetadata.extractGenerationFacts);
        }
        return records;
    });
    expect(result.missingFactsExport).toBeUndefined();
    const complete = result.complete;
    expect(Object.values(complete.generation).map(fact => fact.value)).toEqual(['9007199254740993', '28', '6.5', 'dpmpp_2m', 'karras', 'fixture-model.safetensors']);
    expect(Object.values(complete.generation).every(fact => fact.source === 'api-graph' && fact.evidence === 'configuration')).toBe(true);
    expect(Object.values(result.ambiguous.generation).every(fact => fact.status === 'ambiguous' && fact.value === null)).toBe(true);
    expect(result['workflow-only'].generation.seed).toMatchObject({ status: 'known', value: '9007199254740993', source: 'workflow', evidence: 'configuration' });
    expect(result['workflow-only'].prompts.positive.source).toBe('saved-association');
    expect(Object.values(result['control-heavy'].generation).every(fact => fact.status === 'unresolved' && fact.value === null && fact.reason === 'projection-limited')).toBe(true);
    await expect(await request.post('/__test__/reset')).toBeOK();
});

test('inspection wire preserves exact rooted envelope', async ({ request }) => {
    for (const data of [
        { full_inspector: 'true' }, { full_inspector: 1 },
        { full_inspector: true, workspace_visual: true },
        { full_inspector: true, prompt_inspector: true },
        { full_inspector: true, native_library: true },
        { full_inspector: true, extra_prompts: 1 },
        { full_inspector: true, extra_variants: 1 },
        { full_inspector: true, extra_images: true },
        { full_inspector: true, include_video: 'true' },
    ]) expect((await request.post('/__test__/reset', { data })).status()).toBe(400);
    await expect(await request.post('/__test__/reset', { data: { full_inspector: true, include_video: true, extra_images: 2 } })).toBeOK();
    const collection = await (await request.get('/prompt_manager/images/output?mode=workspace&subfolder=inspector&limit=40')).json();
    expect(collection.total).toBe(11);
    const complete = collection.images.find(image => image.root_index === 0 && image.path === 'inspector/complete.png');
    const collision = collection.images.find(image => image.root_index === 1 && image.path === complete.path);
    expect(complete.root_scope).toMatch(/^[0-9a-f]{64}$/);
    expect(collision.root_scope).not.toBe(complete.root_scope);
    const inspect = async entry => {
        const response = await request.get(`/prompt_manager/images/inspect/${entry.path.split('/').map(encodeURIComponent).join('/')}?root=${entry.root_index}&root_scope=${entry.root_scope}`);
        await expect(response).toBeOK();
        expect(response.headers()['cache-control']).toBe('no-store');
        expect((await response.body()).length).toBeLessThanOrEqual(8 * 1024 * 1024);
        return response.json();
    };
    const result = await inspect(complete);
    expect(result.inspection_version).toBe(1);
    expect(result.image).toEqual({ root_index: 0, path: complete.path, filename: 'complete.png' });
    expect(result.root_scope).toBe(complete.root_scope);
    expect(result.prompts.positive.text).toContain('Inspector portrait 猫 😀');
    expect(result.prompts.negative.text).toBe('blurred edges, watermark');
    expect(result.metadata.prompt_graph.value['3'].inputs.seed).toBe('9007199254740993');
    expect(result.metadata.prompt_graph.value['3'].inputs.cfg).toBe('6.5');
    expect(result.metadata.record_formats).toEqual({ prompt: 'original-text', workflow: 'original-text' });
    expect(result.file).toMatchObject({ observation: 'current-file', format: 'PNG', width: 720, height: 480 });
    expect(result.library).toMatchObject({ status: 'linked', metadata_scope: 'pair', title: 'Inspector recipe', category: 'Inspection', tags: ['fixture', 'geometric'] });
    expect((await inspect(collision)).prompts.positive.text).toBe('Input collision');
    const gallery = await (await request.get(`/prompt_manager/library/${result.library.positive_id}/images?pair_id=${result.library.pair_id}`)).json();
    expect(gallery.images[0].root_scope).toBe(complete.root_scope);
    const library = await (await request.get('/prompt_manager/library')).json();
    expect(library.items.find(item => item.id === result.library.positive_id).previews.some(entry => entry.root_scope === complete.root_scope)).toBe(true);
    const workflow = await inspect(collection.images.find(image => image.path === 'inspector/workflow-only.png'));
    expect(workflow.prompts.positive.source).toBe('saved-association');
    const limited = await inspect(collection.images.find(image => image.path === 'inspector/control-heavy.png'));
    expect(limited.metadata).toMatchObject({ status: 'limited', omitted_reason: 'response-size-limit', raw: {}, parameters: null });
    expect(collection.images.some(image => image.path.endsWith('clip.webm'))).toBe(true);
    await expect(await request.post('/__test__/reset')).toBeOK();
});
