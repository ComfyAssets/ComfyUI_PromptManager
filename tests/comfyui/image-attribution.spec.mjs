// #342 on the real pinned host: the image monitor links an output only to a prompt
// that provably produced it. An unknown node (PMHistoryCached) stands in for the
// maintainer's LLM; KSampler is the fixture's deterministic stand-in.
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
