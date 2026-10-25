// #343: on the pinned real host, a STRING link dragged with the mouse drives each PromptManager node's text (and the
// Pair's negative) in Classic and Nodes 2.0, across renderer switches, a workflow reload, disconnect and removal.
import { readFile } from 'node:fs/promises';
import { test, expect } from '../../tools/vendor/node_modules/@playwright/test/index.mjs';
import { installHostHooks } from './host-fixture.mjs';

installHostHooks(test, ['PromptManager', 'PromptManagerText', 'PromptManagerFocused', 'PromptManagerSplit', 'PromptManagerPair']);

const editors = { PromptManagerFocused: 'Main prompt', PromptManagerSplit: 'Main prompt', PromptManagerPair: 'Positive prompt' };
const note = label => `${label} comes from a link. Your typed text is kept for when you disconnect it.`;

/** Viewport position of a node's slot: the slot dot in Nodes 2.0, the canvas slot position in Classic. */
function slotPosition(page, nodeId, kind, name) {
    return page.evaluate(async ({ nodeId, kind, name }) => {
        const { app } = await import('/scripts/app.js');
        const node = app.graph.getNodeById(nodeId);
        const slots = kind === 'out' ? node.outputs : node.inputs;
        const index = typeof name === 'number' ? name : slots.findIndex(slot => slot.name === name);
        if (LiteGraph.vueNodesMode) {
            const dot = document.querySelector(`[data-slot-key="${nodeId}-${kind}-${index}"]`);
            if (!dot) return null;
            const box = dot.getBoundingClientRect();
            return [box.x + box.width / 2, box.y + box.height / 2];
        }
        const position = app.canvas.ds.convertOffsetToCanvas(kind === 'out' ? node.getOutputPos(index) : node.getInputPos(index));
        const box = app.canvas.canvas.getBoundingClientRect();
        return [box.x + position[0], box.y + position[1]];
    }, { nodeId, kind, name });
}

async function drag(page, from, to) {
    await page.mouse.move(...from);
    await page.mouse.down();
    await page.mouse.move((from[0] + to[0]) / 2, (from[1] + to[1]) / 2, { steps: 5 });
    await page.mouse.move(...to, { steps: 10 });
    await page.mouse.up();
}

const center = box => [box.x + box.width / 2, box.y + box.height / 2];
const linkedInputs = (page, id) => page.evaluate(async id => {
    const { app } = await import('/scripts/app.js');
    return app.graph.getNodeById(id).inputs.filter(input => input.link != null).map(input => input.name);
}, id);
const queued = (page, id) => page.evaluate(async id => (await (await import('/scripts/app.js')).app.graphToPrompt()).output[String(id)].inputs, id);

async function setup(page, type) {
    return page.evaluate(async type => {
        const { app } = await import('/scripts/app.js');
        const node = LiteGraph.createNode(type);
        app.graph.add(node);
        node.pos = [500, 80];
        node.setSize([type === 'PromptManagerPair' ? 1100 : 650, 760]);
        const sources = ['text', 'negative_text'].map((name, index) => {
            const source = LiteGraph.createNode('PrimitiveString');
            app.graph.add(source);
            source.pos = [40, 80 + index * 160];
            source.widgets.find(widget => widget.name === 'value').value = `Linked ${name} ${type}`;
            return source.id;
        });
        app.canvas.ds.scale = .8;
        app.canvas.ds.offset = [40, 40];
        app.canvas.setDirty(true, true);
        return { id: node.id, sources };
    }, type);
}

/** The prompt box a user drops the link on: the editor's textarea, or the core text box of the Classic nodes. */
async function promptBox(page, type, id) {
    if (editors[type]) return page.getByRole('textbox', { name: editors[type], exact: true });
    // The Classic nodes keep the core text widget: Nodes 2.0 draws it as its own textarea, Classic as widget.inputEl.
    if (await page.evaluate(() => LiteGraph.vueNodesMode)) return page.locator(`[data-node-id="${id}"] textarea`).first();
    await page.evaluate(async id => {
        const { app } = await import('/scripts/app.js');
        app.graph.getNodeById(id).widgets.find(widget => widget.name === 'text').inputEl.dataset.pmTestPromptBox = String(id);
    }, id);
    return page.locator(`textarea[data-pm-test-prompt-box="${id}"]`);
}

/** What the node shows while its text is linked. */
function linkedNote(page, type) {
    return editors[type] ? page.getByText(note(editors[type]), { exact: true }) : page.locator('.pm-text-link-note').getByText(note('Text'), { exact: true });
}

for (const type of ['PromptManagerFocused', 'PromptManagerSplit', 'PromptManagerPair', 'PromptManager', 'PromptManagerText']) {
    test(`${type}: a STRING link dropped on the prompt box drives text and the typed text returns on disconnect`, async ({ page }, testInfo) => {
        test.setTimeout(90000);
        const { id, sources } = await setup(page, type);
        const typed = `Typed ${type} ${testInfo.project.name}`;
        const box = await promptBox(page, type, id);
        await box.fill(typed);
        await expect(linkedNote(page, type)).toBeHidden();
        await page.screenshot({ path: testInfo.outputPath(`${type}-unlinked.png`) });
        // The drop lands on text, never on prepend_text (#342).
        await drag(page, await slotPosition(page, sources[0], 'out', 0), center(await box.boundingBox()));
        await expect.poll(() => linkedInputs(page, id)).toEqual(['text']);
        const links = { text: [String(sources[0]), 0] };
        if (type === 'PromptManagerPair') {
            await page.getByRole('textbox', { name: 'Negative prompt', exact: true }).fill(`Typed negative ${testInfo.project.name}`);
            await drag(page, await slotPosition(page, sources[1], 'out', 0), await slotPosition(page, id, 'in', 'negative_text'));
            await expect.poll(() => linkedInputs(page, id)).toEqual(['text', 'negative_text']);
            links.negative_text = [String(sources[1]), 0];
            await expect(page.getByText(note('Negative prompt'), { exact: true })).toBeVisible();
        }
        await expect(linkedNote(page, type)).toBeVisible();
        expect(await queued(page, id)).toMatchObject(links);
        await page.screenshot({ path: testInfo.outputPath(`${type}-linked.png`) });
        for (const renderer of [!testInfo.project.metadata.initialRenderer, testInfo.project.metadata.initialRenderer]) {
            await page.evaluate(async renderer => {
                const { app } = await import('/scripts/app.js');
                await app.ui.settings.setSettingValue('Comfy.VueNodes.Enabled', renderer);
            }, renderer);
            await expect.poll(() => page.evaluate(() => LiteGraph.vueNodesMode)).toBe(renderer);
            await expect(linkedNote(page, type)).toBeVisible();
            expect(await queued(page, id)).toMatchObject(links);
        }
        const saved = await page.evaluate(async id => {
            const { app } = await import('/scripts/app.js');
            const workflow = app.graph.serialize();
            await app.loadGraphData(workflow);
            return workflow.nodes.find(node => String(node.id) === String(id)).widgets_values;
        }, id);
        expect(saved[0]).toBe(typed);
        await expect(linkedNote(page, type)).toBeVisible();
        expect(await queued(page, id)).toMatchObject(links);
        const local = await page.evaluate(async id => {
            const { app } = await import('/scripts/app.js');
            const node = app.graph.getNodeById(id);
            for (const input of [...node.inputs]) if (input.link != null) node.disconnectInput(node.inputs.indexOf(input));
            return (await app.graphToPrompt()).output[String(id)].inputs;
        }, id);
        expect(local.text).toBe(typed);
        if (type === 'PromptManagerPair') expect(local.negative_text).toBe(`Typed negative ${testInfo.project.name}`);
        await expect(linkedNote(page, type)).toBeHidden();
        const restored = await promptBox(page, type, id);
        await expect(restored).toBeVisible();
        await expect(restored).toHaveValue(typed);
        await page.screenshot({ path: testInfo.outputPath(`${type}-disconnected.png`) });
        await page.evaluate(async id => { const { app } = await import('/scripts/app.js'); app.graph.remove(app.graph.getNodeById(id)); }, id);
        await expect(page.locator('.pm-focused, .pm-classic, .pm-text-link-note')).toHaveCount(0);
    });
}

test('workflows saved before #343 load unchanged and their linked text keeps working', async ({ page }) => {
    const workflow = JSON.parse(await readFile(new URL('./fixtures/workflow-before-343.json', import.meta.url), 'utf8'));
    const result = await page.evaluate(async workflow => {
        const { app } = await import('/scripts/app.js');
        await app.loadGraphData(structuredClone(workflow));
        const output = (await app.graphToPrompt()).output;
        const nodes = Object.fromEntries(app.graph._nodes.map(node => [node.id, node]));
        const reloaded = app.graph.serialize();
        return {
            values: Object.fromEntries(reloaded.nodes.map(node => [node.id, node.widgets_values])),
            links: Object.fromEntries(Object.entries(output).map(([id, node]) => [id, { text: node.inputs.text, negative_text: node.inputs.negative_text }])),
            widgetBound: Object.fromEntries(Object.values(nodes).filter(node => node.type.startsWith('PromptManager')).map(node => [node.id, node.inputs.filter(input => ['text', 'negative_text'].includes(input.name) && input.widget).map(input => input.name)])),
        };
    }, workflow);
    for (const node of workflow.nodes) {
        // Every saved widget value comes back as it was saved, except the typed text main lost under a link (null).
        if (node.widgets_values?.[0] !== null) expect(result.values[node.id], `${node.type} ${node.id}`).toEqual(node.widgets_values);
    }
    expect(result.links).toMatchObject({
        1: { text: 'Saved PromptManager text' }, 2: { text: 'Saved PromptManagerText text' }, 3: { text: 'Saved PromptManagerFocused text' },
        4: { text: 'Saved PromptManagerSplit text' }, 5: { text: 'Saved PromptManagerPair text', negative_text: 'Saved pair negative' },
        6: { text: ['7', 0] }, 8: { text: ['9', 0], negative_text: ['10', 0] }, 11: { text: ['12', 0] },
    });
    // The editor nodes' text inputs are sockets now; the Classic nodes keep their core widget sockets.
    expect(result.widgetBound).toEqual({ 1: ['text'], 2: ['text'], 3: [], 4: [], 5: [], 6: [], 8: [], 11: ['text'] });
    // The linked sockets are drawn where a user can see and re-drag them, in either renderer.
    for (const [node, name] of [[6, 'text'], [8, 'text'], [8, 'negative_text']]) {
        await page.evaluate(async node => { const { app } = await import('/scripts/app.js'); app.canvas.centerOnNode(app.graph.getNodeById(node)); app.canvas.setDirty(true, true); }, node);
        await expect.poll(async () => {
            const position = await slotPosition(page, node, 'in', name);
            return Boolean(position && position[0] > 0 && position[1] > 0 && position[0] < 1600 && position[1] < 1100);
        }, { message: `${node}.${name} socket on screen` }).toBe(true);
    }
    const unplugged = await page.evaluate(async () => {
        const { app } = await import('/scripts/app.js');
        for (const id of [6, 8]) { const node = app.graph.getNodeById(id); for (const input of [...node.inputs]) if (input.link != null) node.disconnectInput(node.inputs.indexOf(input)); }
        const output = (await app.graphToPrompt()).output;
        return { focused: output['6'].inputs.text, positive: output['8'].inputs.text, negative: output['8'].inputs.negative_text };
    });
    expect(unplugged).toEqual({ focused: 'Typed under focused link', positive: 'Typed pair positive', negative: 'Typed pair negative' });
});
