// #343: a link drives a PromptManager node's text (and the Pair's negative) on the real pinned host.
// The workflow is built by the real frontend through the nodes' sockets and queued as the frontend serializes it;
// only inference is replaced (fixtures/ledger_nodes.py). The linked text must be saved, counted, recorded in the
// execution history, and the run's image must link to it.
import { execFile } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { promisify } from 'node:util';
import { expect } from '../../tools/vendor/node_modules/@playwright/test/index.mjs';
import { test, readExecutionRuns } from './execution-fixture.mjs';
import { installHostHooks } from './host-fixture.mjs';

// The worker's owned host must be up before the shared host hooks open the page.
test.beforeEach(async ({ execution }) => { expect(execution.databasePath).toBeTruthy(); });
installHostHooks(test, ['PromptManager', 'PromptManagerText', 'PromptManagerFocused', 'PromptManagerPair', 'PMHistoryInputs']);

// `primitive`: a core string node the queue-time resolver follows. `legacy`: the frontend's virtual Primitive node,
// whose value is queued as literal text. `preview`: PrimitiveString -> PreviewAny, the shape of the maintainer's
// TextGenerate -> PreviewAny chain, whose text is only known when the node runs.
function build(page, { kind, positive, negative, prefix, saveToLibrary }) {
    return page.evaluate(async ({ kind, positive, negative, prefix, saveToLibrary }) => {
        const { app } = await import('/scripts/app.js');
        app.graph.clear();
        const add = (type, x, y) => { const node = LiteGraph.createNode(type); app.graph.add(node); node.pos = [x, y]; return node; };
        const slot = (node, name) => { const index = node.inputs.findIndex(input => input.name === name); if (index < 0) throw new Error(`${node.type} has no ${name} socket`); return index; };
        const connect = (from, output, to, name) => { if (!from.connect(output, to, slot(to, name))) throw new Error(`${from.type} could not link to ${to.type}.${name}`); };
        const inputs = add('PMHistoryInputs', 0, 0);
        const prompt = add(kind, 700, 0);
        const set = (name, value) => { const widget = prompt.widgets.find(item => item.name === name); widget.value = value; if (widget.inputEl) widget.inputEl.value = value; };
        set('text', positive.typed);
        if (negative) set('negative_text', negative.typed);
        if (saveToLibrary !== undefined) prompt.widgets.find(item => item.name === 'save_to_library').value = saveToLibrary;
        const drive = (name, side, y) => {
            if (!side.via) return;
            if (side.via === 'legacy') {
                // The frontend's own Primitive: a virtual node whose value is applied when the workflow is queued.
                const primitive = add('PrimitiveNode', 0, y);
                connect(primitive, 0, prompt, name);
                primitive.widgets[0].value = side.text;
                return;
            }
            const text = add('PrimitiveString', 0, y);
            text.widgets.find(widget => widget.name === 'value').value = side.text;
            let source = text;
            if (side.via === 'preview') { source = add('PreviewAny', 350, y); connect(text, 0, source, 'source'); }
            connect(source, 0, prompt, name);
        };
        drive('text', positive, 300);
        if (negative) drive('negative_text', negative, 500);
        if (kind !== 'PromptManagerText') connect(inputs, 0, prompt, 'clip');
        const sampler = add('KSampler', 1400, 0);
        connect(inputs, 1, sampler, 'model');
        connect(inputs, 2, sampler, 'latent_image');
        if (kind === 'PromptManagerText') {
            const encode = add('CLIPTextEncode', 1100, 0);
            encode.convertWidgetToInput?.(encode.widgets.find(widget => widget.name === 'text'));
            connect(inputs, 0, encode, 'clip');
            connect(prompt, 0, encode, 'text');
            connect(encode, 0, sampler, 'positive');
        } else connect(prompt, 0, sampler, 'positive');
        if (kind === 'PromptManagerPair') connect(prompt, 1, sampler, 'negative');
        else {
            const empty = add('CLIPTextEncode', 1100, 300);
            connect(inputs, 0, empty, 'clip');
            connect(empty, 0, sampler, 'negative');
        }
        const images = add('PMHistoryImages', 1800, 0);
        images.widgets.find(widget => widget.name === 'value').value = 99;
        connect(sampler, 0, images, 'latent');
        const save = add('SaveImage', 2100, 0);
        save.widgets.find(widget => widget.name === 'filename_prefix').value = prefix;
        connect(images, 0, save, 'images');
        // The Classic nodes install their own text serializer shortly after creation; queue after it, as a user does.
        await prompt._pmTextSerializerReady;
        const { output, workflow } = await app.graphToPrompt();
        return { output, workflow, promptNode: String(prompt.id) };
    }, { kind, positive, negative, prefix, saveToLibrary });
}

async function run(execution, page, testCase) {
    const prefix = `textlink/${randomUUID()}`;
    const { output, workflow, promptNode } = await build(page, { ...testCase, prefix });
    const id = randomUUID();
    const response = await execution.request.post('/prompt', { data: { prompt: output, prompt_id: id, extra_data: { extra_pnginfo: { workflow } } } });
    expect(response.ok(), await response.text()).toBe(true);
    let item;
    await expect.poll(async () => {
        item = (await (await execution.request.get(`/history/${id}`)).json())[id];
        return item?.status?.status_str ?? null;
    }, { timeout: 20000 }).toBe('success');
    let result;
    await expect.poll(async () => {
        result = await readExecutionRuns(execution.databasePath, id);
        const run = result.rows[0];
        return run ? result.captures[run.run_id].state : null;
    }, { timeout: 10000, message: 'the run and its prompt context reach SQLite' }).toBe('complete');
    const run = result.rows[0];
    const files = Object.values(item.outputs).flatMap(value => value.images || []).map(image => image.filename);
    return { output, promptNode, context: result.captures[run.run_id].contexts[0], usage: result.usage[run.run_id], files };
}

async function scan(execution) {
    const response = await execution.request.post('/prompt_manager/scan', { timeout: 60000 });
    expect(response.ok()).toBe(true);
    const events = (await response.text()).split('\n').filter(line => line.startsWith('data: ')).map(line => JSON.parse(line.slice(6)));
    expect(events.at(-1)?.type, JSON.stringify(events.at(-1))).toBe('complete');
}

// filename -> linked prompt id (null when the image is not linked), read-only from the owned host's library.
const LINKS = `import json, sqlite3, sys, pathlib
db = sqlite3.connect(pathlib.Path(sys.argv[1]).resolve().as_uri() + "?mode=ro", uri=True)
links = {name: None for name in sys.argv[2:]}
for name, prompt in db.execute("SELECT filename, prompt_id FROM generated_images WHERE filename IN (%s)" % ",".join("?" * (len(sys.argv) - 2)), sys.argv[2:]):
    links[name] = prompt
print(json.dumps(links))`;
async function linkedPrompts(execution, files) {
    const { stdout } = await promisify(execFile)(process.env.PM_TEST_PYTHON || 'python', ['-c', LINKS, execution.databasePath, ...files], { timeout: 5000 });
    return JSON.parse(stdout);
}

// Text a link resolves from the queued graph (a primitive) links its images through their embedded metadata.
// Text only known at run time (PreviewAny) cannot be read from the image: its attribution from the recorded run is
// #342's. Here it must never be linked to another prompt.
async function expectImagesLinked(execution, evidence, viaRuntime) {
    await scan(execution);
    const links = await linkedPrompts(execution, evidence.files);
    expect(evidence.files.length).toBeGreaterThan(0);
    for (const [file, prompt] of Object.entries(links)) {
        if (viaRuntime) expect([null, evidence.context.positive_prompt_id], file).toContain(prompt);
        else expect(prompt, file).toBe(evidence.context.positive_prompt_id);
    }
}

const unique = label => `${label} ${randomUUID()}`;

const sources = { primitive: 'PrimitiveString', legacy: 'a legacy Primitive', preview: 'PreviewAny' };
for (const kind of ['PromptManager', 'PromptManagerText', 'PromptManagerFocused', 'PromptManagerSplit']) {
    for (const via of Object.keys(sources)) {
        test(`${kind} saves, counts, records and links images to text linked from ${sources[via]}`, async ({ execution, page }) => {
            test.setTimeout(90000);
            const text = unique(`Linked ${kind} ${via}`);
            const evidence = await run(execution, page, { kind, positive: { typed: 'Typed under the link', text, via } });
            const source = evidence.output[evidence.promptNode].inputs.text;
            // A real node is queued as a link; the virtual Primitive as its own value, never the hidden typed text.
            if (via === 'legacy') expect(source).toBe(text);
            else expect(Array.isArray(source), 'the frontend queued the link, not the typed text').toBe(true);
            expect(evidence.context.positive.raw).toBe(text);
            expect(evidence.context.positive.effective).toBe(text);
            expect(evidence.usage).toEqual([{ role: 'positive', run_count: 1 }]);
            const saved = await (await execution.request.get(`/prompt_manager/library/${evidence.context.positive_prompt_id}`)).json();
            expect(saved.positive.text).toBe(text);
            await expectImagesLinked(execution, evidence, via === 'preview');
        });
    }
}

// A legacy Primitive's wildcard is drawn once when the workflow is queued: the run saves and counts what was queued.
for (const kind of ['PromptManager', 'PromptManagerFocused']) {
    test(`${kind} saves and counts the text a wildcard legacy Primitive queued (#343 re-review R1)`, async ({ execution, page }) => {
        test.setTimeout(90000);
        const stem = unique(`Wildcard ${kind}`);
        const evidence = await run(execution, page, { kind, positive: { typed: 'Typed under the link', text: `${stem} {alpha|beta}`, via: 'legacy' } });
        const queuedText = evidence.output[evidence.promptNode].inputs.text;
        // The Classic node keeps its widget binding: the host applies the Primitive, and its own text serializer
        // queues the text as written, as on main. The Focused editor queues one choice.
        if (kind === 'PromptManager') expect(queuedText).toBe(`${stem} {alpha|beta}`);
        else expect([`${stem} alpha`, `${stem} beta`]).toContain(queuedText);
        expect(evidence.context.positive.raw).toBe(queuedText);
        expect(evidence.usage).toEqual([{ role: 'positive', run_count: 1 }]);
        const saved = await (await execution.request.get(`/prompt_manager/library/${evidence.context.positive_prompt_id}`)).json();
        expect(saved.positive.text).toBe(queuedText);
    });
}

for (const [label, linked, negativeVia] of [['positive', ['text'], 'primitive'], ['negative', ['negative_text'], 'primitive'],
    ['both', ['text', 'negative_text'], 'primitive'], ['neither', [], 'primitive'], ['negative from a legacy Primitive', ['negative_text'], 'legacy']]) {
    test(`PromptManagerPair with ${label} linked saves, counts and records the pair and links its images`, async ({ execution, page }) => {
        test.setTimeout(90000);
        const sides = {
            text: { typed: unique('Typed positive'), text: unique('Linked positive'), via: linked.includes('text') ? 'preview' : null },
            negative_text: { typed: unique('Typed negative'), text: unique('Linked negative'), via: linked.includes('negative_text') ? negativeVia : null },
        };
        const evidence = await run(execution, page, { kind: 'PromptManagerPair', positive: sides.text, negative: sides.negative_text });
        const expected = name => sides[name].via ? sides[name].text : sides[name].typed;
        for (const name of ['text', 'negative_text']) {
            const queuedValue = evidence.output[evidence.promptNode].inputs[name];
            if (sides[name].via === 'legacy') expect(queuedValue, name).toBe(sides[name].text);
            else expect(Array.isArray(queuedValue), name).toBe(linked.includes(name));
        }
        expect(evidence.context.positive.raw).toBe(expected('text'));
        expect(evidence.context.negative.raw).toBe(expected('negative_text'));
        expect(evidence.usage).toEqual([{ role: 'positive', run_count: 1 }, { role: 'negative', run_count: 0 }, { role: 'pair', run_count: 1 }]);
        const pair = await (await execution.request.get(`/prompt_manager/pairs/${evidence.context.pair_id}`)).json();
        expect(pair.pair).toMatchObject({ positive_text: expected('text'), negative_text: expected('negative_text') });
        await expectImagesLinked(execution, evidence, sides.text.via === 'preview');
    });
}

// #90: with Save to library off the run still produces its image, but PromptManager saves, counts and links nothing,
// and the run's history records no prompt text for it.
for (const kind of ['PromptManager', 'PromptManagerText', 'PromptManagerFocused', 'PromptManagerSplit', 'PromptManagerPair']) {
    test(`${kind} with Save to library off saves, counts and links nothing (#90)`, async ({ execution, page }) => {
        test.setTimeout(90000);
        const text = unique(`Not saved ${kind}`);
        const negative = kind === 'PromptManagerPair' ? { typed: unique('Not saved negative') } : undefined;
        const prefix = `notsaved/${randomUUID()}`;
        const { output, workflow, promptNode } = await build(page, { kind, positive: { typed: text }, negative, prefix, saveToLibrary: false });
        expect(output[promptNode].inputs.save_to_library).toBe(false);
        const id = randomUUID();
        const response = await execution.request.post('/prompt', { data: { prompt: output, prompt_id: id, extra_data: { extra_pnginfo: { workflow } } } });
        expect(response.ok(), await response.text()).toBe(true);
        let item;
        await expect.poll(async () => {
            item = (await (await execution.request.get(`/history/${id}`)).json())[id];
            return item?.status?.status_str ?? null;
        }, { timeout: 20000 }).toBe('success');
        let result;
        await expect.poll(async () => {
            result = await readExecutionRuns(execution.databasePath, id);
            const run = result.rows[0];
            return Boolean(run && result.captures[run.run_id]);
        }, { timeout: 10000, message: 'the run reaches SQLite' }).toBe(true);
        const run = result.rows[0];
        for (const context of result.captures[run.run_id].contexts || []) {
            expect(context.positive_prompt_id ?? null).toBeNull();
            expect(context.pair_id ?? null).toBeNull();
        }
        expect((result.usage[run.run_id] || []).every(entry => entry.run_count === 0)).toBe(true);
        const files = Object.values(item.outputs).flatMap(value => value.images || []).map(image => image.filename);
        expect(files.length).toBeGreaterThan(0);
        await scan(execution);
        expect(Object.values(await linkedPrompts(execution, files))).toEqual(files.map(() => null));
        for (const query of [text, negative?.typed].filter(Boolean)) {
            const found = await (await execution.request.get(`/prompt_manager/search?text=${encodeURIComponent(query)}`)).json();
            expect(found.results ?? [], query).toEqual([]);
        }
    });
}
