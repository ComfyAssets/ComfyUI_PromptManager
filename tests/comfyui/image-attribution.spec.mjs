// #342 on the real pinned host: the image monitor links an output only to a prompt
// that provably produced it. An unknown node (PMHistoryCached) stands in for the
// maintainer's LLM; KSampler is the fixture's deterministic stand-in. #247: an output
// of a workflow without a PromptManager node gets its own prompt.
import { randomUUID } from 'node:crypto';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';
import { expect } from '../../tools/vendor/node_modules/@playwright/test/index.mjs';
import { test } from './execution-fixture.mjs';

const run = promisify(execFile);
const reader = fileURLToPath(new URL('./read_execution_history.py', import.meta.url));

async function readLinks(databasePath, filenames, texts = []) {
    const { stdout } = await run(process.env.PM_TEST_PYTHON || 'python',
        [reader, databasePath, '--links', ...filenames, '--texts', ...texts], { timeout: 5000, maxBuffer: 1024 * 1024 });
    return JSON.parse(stdout);
}

// kind: the positive node; text: a typed editor or 'llm' (linked from the unknown
// node); prepend: 'llm' links the unknown node into prepend_text.
function graph({ kind = 'PromptManagerPair', text = '', prepend = '', llm = '', seed = 1, prefix }) {
    const linked = value => value === 'llm' ? ['11', 0] : value;
    const nodes = {
        '10': { class_type: 'PMHistoryInputs', inputs: {} },
        '20': { class_type: kind, inputs: { text: linked(text), clip: ['10', 0], append_text: '',
            ...(prepend ? { prepend_text: linked(prepend) } : {}),
            ...(kind === 'PromptManagerPair' ? { negative_text: '' } : {}) } },
        '30': { class_type: 'CLIPTextEncode', inputs: { text: '', clip: ['10', 0] } },
        '40': { class_type: 'KSampler', inputs: { model: ['10', 1], latent_image: ['10', 2],
            positive: ['20', 0], negative: kind === 'PromptManagerPair' ? ['20', 1] : ['30', 0],
            seed, steps: 1, cfg: 1, sampler_name: 'euler', scheduler: 'normal', denoise: 1 } },
        '60': { class_type: 'PMHistoryImages', inputs: { value: 64, batch: 1, latent: ['40', 0] } },
        '70': { class_type: 'SaveImage', inputs: { images: ['60', 0], filename_prefix: prefix } },
    };
    if (llm) nodes['11'] = { class_type: 'PMHistoryCached', inputs: { value: llm } };
    return nodes;
}

async function saved(request, prompt, history = {}) {
    const id = randomUUID();
    const response = await request.post('/prompt', { data: { prompt, prompt_id: id } });
    expect(response.ok()).toBe(true);
    let item;
    await expect.poll(async () => {
        item = (await (await request.get(`/history/${id}`)).json())[id];
        return item?.status?.messages?.some(([kind]) => kind === 'execution_success') || false;
    }, { timeout: 15000 }).toBe(true);
    const [image] = item.outputs['70'].images;
    expect(image.type).toBe('output');
    history.item = item;
    return image.filename;
}

async function linkedTo(execution, filename, expected) {
    await expect.poll(async () => (await readLinks(execution.databasePath, [filename])).links[filename],
        { timeout: 20000, message: `${filename} is linked by the image monitor` }).toBe(expected);
}

async function monitoring(execution, enabled) {
    const response = await execution.request.post('/pm_execution_fixture/monitor', { data: { enabled } });
    expect(response.ok()).toBe(true);
    expect((await response.json()).monitoring).toBe(enabled);
}

async function cacheMode(execution, mode) {
    expect((await execution.request.post('/pm_execution_fixture/cache', { data: { mode } })).ok()).toBe(true);
}

test.beforeEach(async ({ execution }) => {
    expect((await execution.request.post('/pm_execution_fixture/metadata', { data: { disabled: false } })).ok()).toBe(true);
    await cacheMode(execution, 'CLASSIC');  // keeps the re-run's PromptManager node cached
    await monitoring(execution, true);
});
test.afterEach(async ({ execution }) => {
    await monitoring(execution, false);
    await cacheMode(execution, 'RAM_PRESSURE');
});

test('an output whose positive is only linked prepend text stays unlinked, never given an earlier prompt', async ({ execution }, testInfo) => {
    const tag = `${testInfo.project.name} ${randomUUID()}`;
    const earlier = `a woman holding a coffee cup ${tag}`;
    const prepended = `llm prepend landscape ${tag}`, llmText = `llm text landscape ${tag}`;

    // A typed prompt runs first: before #342 the next unidentifiable image took it.
    const first = await saved(execution.request, graph({ text: earlier, prefix: 'misslink/earlier' }));
    await linkedTo(execution, first, earlier);

    // The maintainer's workflow: empty editor, the generated text arrives in prepend.
    const orphan = await saved(execution.request,
        graph({ prepend: 'llm', llm: prepended, prefix: 'misslink/prepend' }));

    // An LLM feeding the editor itself is stored and links through its run key; the
    // monitor works in creation order, so the prepend image was processed first.
    const llmGraph = graph({ kind: 'PromptManager', text: 'llm', llm: llmText, prefix: 'misslink/text' });
    const proven = await saved(execution.request, llmGraph);
    await linkedTo(execution, proven, llmText);
    const state = await readLinks(execution.databasePath, [orphan], [prepended]);
    expect(state).toEqual({ links: { [orphan]: null }, stored: { [prepended]: false } });

    // Only the sampler seed changes: the PromptManager node is served from the cache
    // without running (the host says so), so no queue entry exists for this run and
    // only the snapshot's run match can link it.
    const rerunHistory = {};
    const rerun = await saved(execution.request, { ...llmGraph, '40': { ...llmGraph['40'],
        inputs: { ...llmGraph['40'].inputs, seed: 2 } } }, rerunHistory);
    const cached = rerunHistory.item.status.messages.find(([kind]) => kind === 'execution_cached');
    expect(cached?.[1].nodes).toEqual(expect.arrayContaining(['11', '20']));
    await linkedTo(execution, rerun, llmText);
});

test('an output saved without metadata links once its recorded run names a stored prompt', async ({ execution }, testInfo) => {
    const llmText = `bare output landscape ${testInfo.project.name} ${randomUUID()}`;
    const request = execution.request;
    expect((await request.post('/pm_execution_fixture/metadata', { data: { disabled: true } })).ok()).toBe(true);
    try {
        // No embedded graph: no run key can prove the tracker entry, so only the
        // run's recorded history can, and only once the run's outputs are sealed.
        // The gate after SaveImage holds the run open while the monitor looks.
        const prompt = graph({ kind: 'PromptManager', text: 'llm', llm: llmText, prefix: 'misslink/bare' });
        prompt['80'] = { class_type: 'PMHistoryImageOutput', inputs: { images: ['70', 0], mode: 'gate' } };
        expect((await request.post('/pm_execution_fixture/gate/hold')).ok()).toBe(true);
        const id = randomUUID();
        try {
            expect((await request.post('/prompt', { data: { prompt, prompt_id: id } })).ok()).toBe(true);
            let waiting = [];
            await expect.poll(async () => {
                waiting = (await (await request.get('/pm_execution_fixture/monitor')).json()).awaiting;
                return waiting.filter(name => name.startsWith('bare_')).length;
            }, { timeout: 20000, message: 'the monitor processed the bare output while its run was open' }).toBe(1);
            const [bare] = waiting.filter(name => name.startsWith('bare_'));
            expect((await readLinks(execution.databasePath, [bare])).links[bare]).toBeNull();
            expect((await request.post('/pm_execution_fixture/gate/release')).ok()).toBe(true);
            await linkedTo(execution, bare, llmText);
            expect((await (await request.get('/pm_execution_fixture/monitor')).json()).awaiting).not.toContain(bare);
        } finally {
            expect((await request.post('/pm_execution_fixture/gate/release')).ok()).toBe(true);
        }
    } finally {
        expect((await request.post('/pm_execution_fixture/metadata', { data: { disabled: false } })).ok()).toBe(true);
    }
});

// #247: no PromptManager node at all, only ComfyUI's own text encoders, sampler and
// saver (the fixture's inputs and decoder stand in for the model files).
function foreignGraph({ text, seed = 1, prefix }) {
    return {
        '10': { class_type: 'PMHistoryInputs', inputs: {} },
        '20': { class_type: 'CLIPTextEncode', inputs: { text, clip: ['10', 0] } },
        '30': { class_type: 'CLIPTextEncode', inputs: { text: '', clip: ['10', 0] } },
        '40': { class_type: 'KSampler', inputs: { model: ['10', 1], latent_image: ['10', 2], positive: ['20', 0],
            negative: ['30', 0], seed, steps: 1, cfg: 1, sampler_name: 'euler', scheduler: 'normal', denoise: 1 } },
        '60': { class_type: 'PMHistoryImages', inputs: { value: 64, batch: 1, latent: ['40', 0] } },
        '70': { class_type: 'SaveImage', inputs: { images: ['60', 0], filename_prefix: prefix } },
    };
}

test('an output of a workflow without a PromptManager node creates and links its prompt; a second run reuses it', async ({ execution }, testInfo) => {
    const text = `foreign lighthouse at dusk ${testInfo.project.name} ${randomUUID()}`;
    const first = await saved(execution.request, foreignGraph({ text, prefix: 'foreign/run' }));
    await linkedTo(execution, first, text);
    // Only the seed changes: a new file, the same text, so the same stored prompt.
    const second = await saved(execution.request, foreignGraph({ text, seed: 2, prefix: 'foreign/run' }));
    await linkedTo(execution, second, text);
    const state = await readLinks(execution.databasePath, [first, second], [text]);
    expect(state).toEqual({ links: { [first]: text, [second]: text }, stored: { [text]: true } });
    // Decided from the graph at settle: nothing waits for the run's recorded outputs.
    expect((await (await execution.request.get('/pm_execution_fixture/monitor')).json()).foreign).toEqual([]);
});
