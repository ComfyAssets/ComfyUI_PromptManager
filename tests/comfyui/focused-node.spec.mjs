import { test, expect } from '../../tools/vendor/node_modules/@playwright/test/index.mjs';
import { installHostHooks } from './host-fixture.mjs';

installHostHooks(test, ['PromptManager', 'PromptManagerText', 'PromptSearchList', 'PromptManagerFocused']);

async function addFocused(page) {
    return page.evaluate(async () => {
        const { app } = await import('/scripts/app.js');
        const node = LiteGraph.createNode('PromptManagerFocused');
        if (!node) throw new Error('PromptManagerFocused must be registered by the real host');
        app.graph.add(node);
        node.setSize([680, 1050]);
        app.canvas.ds.scale = .8;
        app.canvas.centerOnNode(node);
        return node.id;
    });
}

async function model(page, id) {
    return page.evaluate(async id => {
        const { app } = await import('/scripts/app.js');
        return Object.fromEntries(app.graph.getNodeById(id).widgets.map(widget => [widget.name, widget.value]));
    }, id);
}

async function seed(request, text, category = 'Focused host tests', tags = ['focused-host']) {
    const response = await request.post('/prompt_manager/save', { data: { text, category, tags } });
    expect(response.ok()).toBe(true);
    expect((await response.json()).success).toBe(true);
}

async function browse(page, text) {
    const root = page.locator('.pm-focused');
    await root.getByRole('button', { name: 'Browse library', exact: true }).click();
    await root.getByRole('button', { name: 'Recent', exact: true }).click();
    await root.locator('.pm-editor-results').getByRole('button').filter({ has: page.getByText(text, { exact: true }) }).click();
    return root;
}

test('Focused definition preserves Classic encoding and exposes composition STRING sockets', async ({ request }) => {
    const definitions = await (await request.get('/object_info')).json();
    const focused = definitions.PromptManagerFocused;
    expect(focused).toBeDefined();
    expect(focused.display_name).toBe('Prompt Manager (Focused)');
    expect(focused.output).toEqual(definitions.PromptManager.output);
    expect(focused.input.required).toEqual(definitions.PromptManager.input.required);
    for (const name of ['prepend_text', 'append_text']) {
        expect(focused.input.optional[name][0]).toBe('STRING');
        expect(focused.input.optional[name][1]).toMatchObject({ forceInput: true, default: '' });
    }
});

test('Focused preview, explicit load, composition, renderer changes and workflow reload preserve state', async ({ page, request }, testInfo) => {
    const text = `Focused selected ${testInfo.project.name}`;
    await seed(request, text);
    const id = await addFocused(page);
    const root = page.locator('.pm-focused');
    const editor = root.getByRole('textbox', { name: 'Main prompt', exact: true });
    await expect(root).toBeVisible();
    expect(await editor.evaluate(async (element, id) => {
        const { app } = await import('/scripts/app.js');
        return app.graph.getNodeById(id).widgets.find(widget => widget.name === 'text').inputEl === element;
    }, id)).toBe(true);
    await editor.fill('Original editing text');
    await browse(page, text);
    await expect(editor).toHaveValue('Original editing text');
    expect((await model(page, id)).text).toBe('Original editing text');
    await root.getByRole('button', { name: 'Use prompt', exact: true }).click();
    await expect(editor).toHaveValue(text);
    await expect.poll(() => model(page, id)).toMatchObject({ text, category: 'Focused host tests', tags: 'focused-host' });
    await root.getByText('Composition · prepend / append', { exact: true }).click();
    await root.getByRole('textbox', { name: 'Prepend', exact: true }).fill('LoRA trigger');
    await root.getByRole('textbox', { name: 'Append', exact: true }).fill('high detail');
    await root.getByText('Composed text preview', { exact: true }).click();
    await expect(root).toContainText(`LoRA trigger ${text} high detail`);
    for (const renderer of [!testInfo.project.metadata.initialRenderer, testInfo.project.metadata.initialRenderer]) {
        await page.evaluate(async renderer => {
            const { app } = await import('/scripts/app.js');
            await app.ui.settings.setSettingValue('Comfy.VueNodes.Enabled', renderer);
            app.canvas.centerOnNode(app.graph._nodes[0]);
        }, renderer);
        await expect.poll(() => page.evaluate(() => LiteGraph.vueNodesMode)).toBe(renderer);
        await expect(root).toHaveCount(1);
        await expect(editor).toHaveValue(text);
    }
    const queued = await page.evaluate(async id => {
        const { app } = await import('/scripts/app.js');
        const prompt = await app.graphToPrompt();
        await app.loadGraphData(app.graph.serialize());
        return prompt.output[String(id)].inputs;
    }, id);
    expect(queued).toMatchObject({ text, category: 'Focused host tests', tags: 'focused-host', prepend_text: 'LoRA trigger', append_text: 'high detail' });
    await expect(editor).toHaveValue(text);
    await expect.poll(() => model(page, id)).toMatchObject({ text, prepend_text: 'LoRA trigger', append_text: 'high detail' });
    await page.screenshot({ path: testInfo.outputPath('focused-reloaded.png') });
    await page.evaluate(async id => { const { app } = await import('/scripts/app.js'); app.graph.remove(app.graph.getNodeById(id)); }, id);
    await expect(root).toHaveCount(0);
});

test('Focused search preview respects metadata opt-out', async ({ page, request }, testInfo) => {
    const text = `Focused search ${testInfo.project.name}`;
    await seed(request, text, 'Incoming category', ['incoming-tag']);
    const id = await addFocused(page);
    const root = page.locator('.pm-focused');
    await root.getByText('Metadata · category / tags', { exact: true }).click();
    await root.getByRole('textbox', { name: 'Category', exact: true }).fill('Keep category');
    await root.getByRole('textbox', { name: 'Tags', exact: true }).fill('keep-tag');
    await root.getByRole('button', { name: 'Browse library', exact: true }).click();
    await root.getByRole('searchbox', { name: 'Search saved prompts', exact: true }).fill(text);
    await root.getByRole('button', { name: 'Search', exact: true }).click();
    await root.locator('.pm-editor-results').getByRole('button').filter({ has: page.getByText(text, { exact: true }) }).click();
    await root.getByRole('switch', { name: 'Load metadata', exact: true }).uncheck();
    await root.getByRole('button', { name: 'Use prompt', exact: true }).click();
    await expect.poll(() => model(page, id)).toMatchObject({ text, category: 'Keep category', tags: 'keep-tag' });
});

test('Focused connected STRING inputs remain linked through UI actions and reload', async ({ page, request }, testInfo) => {
    const text = `Connected library ${testInfo.project.name}`;
    await seed(request, text);
    const id = await addFocused(page);
    const root = page.locator('.pm-focused');
    await root.getByRole('textbox', { name: 'Main prompt', exact: true }).fill('Local main');
    await root.getByText('Composition · prepend / append', { exact: true }).click();
    await root.getByRole('textbox', { name: 'Prepend', exact: true }).fill('Local prepend');
    await root.getByRole('textbox', { name: 'Append', exact: true }).fill('Local append');
    await root.getByText('Composition · prepend / append', { exact: true }).click();
    const links = await page.evaluate(async id => {
        const { app } = await import('/scripts/app.js');
        const { convertToInput } = await import('/extensions/core/widgetInputs.js');
        const target = app.graph.getNodeById(id);
        convertToInput(target, target.widgets.find(widget => widget.name === 'text'));
        const result = {};
        for (const name of ['text', 'prepend_text', 'append_text']) {
            const source = LiteGraph.createNode('PrimitiveString');
            app.graph.add(source);
            source.pos = [target.pos[0] - 360, target.pos[1] + Object.keys(result).length * 150];
            source.widgets.find(widget => widget.name === 'value').value = `Connected ${name}`;
            const slot = target.inputs.findIndex(input => input.name === name);
            if (slot < 0) throw new Error(`Missing ${name} input socket`);
            source.connect(0, target, slot);
            result[name] = [String(source.id), 0];
        }
        app.canvas.centerOnNode(target);
        return result;
    }, id);
    await browse(page, text);
    await expect(root.getByRole('textbox', { name: 'Main prompt', exact: true })).toBeHidden();
    await expect(root.getByRole('button', { name: 'Use prompt', exact: true })).toBeDisabled();
    await root.getByText('Composition · prepend / append', { exact: true }).click();
    for (const name of ['Prepend', 'Append']) await expect(root.getByRole('textbox', { name, exact: true })).toBeHidden();
    const guardedValues = await model(page, id);
    await root.locator('textarea, input').evaluateAll(inputs => {
        for (const input of inputs) {
            if (!input.hidden && !input.closest('[hidden]')) continue;
            input.value = 'Attempt to replace upstream text';
            input.dispatchEvent(new Event('input', { bubbles: true }));
        }
    });
    expect(await model(page, id)).toMatchObject(guardedValues);
    for (let reload = 0; reload < 2; reload++) {
        const inputs = await page.evaluate(async ({ id, reload }) => {
            const { app } = await import('/scripts/app.js');
            if (reload) await app.loadGraphData(app.graph.serialize());
            return (await app.graphToPrompt()).output[String(id)].inputs;
        }, { id, reload });
        expect(inputs).toMatchObject(links);
    }
    await expect(root.getByRole('button', { name: 'Use prompt', exact: true, includeHidden: true })).toBeDisabled();
    const disconnected = await page.evaluate(async id => {
        const { app } = await import('/scripts/app.js');
        const node = app.graph.getNodeById(id);
        for (const name of ['text', 'prepend_text', 'append_text']) node.disconnectInput(node.inputs.findIndex(input => input.name === name));
        return (await app.graphToPrompt()).output[String(id)].inputs;
    }, id);
    expect(disconnected).toMatchObject({ text: 'Local main', prepend_text: 'Local prepend', append_text: 'Local append' });
    for (const [name, value] of [['Main prompt', 'Local main'], ['Prepend', 'Local prepend'], ['Append', 'Local append']]) {
        const input = page.getByRole('textbox', { name, exact: true });
        await expect(input).toHaveCount(1);
        await expect(input).toBeEditable();
        await expect(input).toHaveValue(value);
    }
    await page.screenshot({ path: testInfo.outputPath('focused-disconnected.png') });
});

test('Focused follows actual ComfyUI light and dark palettes', async ({ page }, testInfo) => {
    await addFocused(page);
    for (const palette of ['light', 'dark']) {
        await page.evaluate(async palette => {
            const { app } = await import('/scripts/app.js');
            await app.ui.settings.setSettingValue('Comfy.ColorPalette', palette);
        }, palette);
        const root = page.locator('.pm-focused');
        await expect(root).toHaveAttribute('data-ca-theme', palette);
        await expect(root.getByRole('button', { name: 'Browse library', exact: true })).toHaveCSS('background-color', 'rgb(8, 119, 187)');
        await page.screenshot({ path: testInfo.outputPath(`focused-${palette}.png`) });
    }
});

test('Focused Web UI button opens actual standalone pages', async ({ page }) => {
    await addFocused(page);
    const popupPromise = page.waitForEvent('popup');
    await page.locator('.pm-focused').getByRole('button', { name: 'Web UI ↗', exact: true }).click();
    const popup = await popupPromise;
    await popup.waitForLoadState('domcontentloaded');
    await expect(popup).toHaveURL(/\/prompt_manager\/(web|admin)/);
    await expect(popup.locator('body')).toContainText('Prompt');
    await expect(page.locator('.pm-focused').getByRole('status')).toHaveText('Web UI opened.');
    await popup.goto(new URL('/prompt_manager/gallery', page.url()).href);
    await expect(popup.locator('#addPromptBtn')).toBeVisible();
    await popup.close();
});
