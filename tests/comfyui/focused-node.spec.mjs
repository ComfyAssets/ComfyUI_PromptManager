import { writeFile } from 'node:fs/promises';
import { test, expect } from '../../tools/vendor/node_modules/@playwright/test/index.mjs';
import { installHostHooks } from './host-fixture.mjs';

installHostHooks(test, ['PromptManager', 'PromptManagerText', 'PromptSearchList', 'PromptManagerFocused']);

async function addEditor(page, type) {
    await page.waitForFunction(type => Boolean(globalThis.LiteGraph?.registered_node_types?.[type]), type);
    return page.evaluate(async type => {
        const { app } = await import('/scripts/app.js');
        const node = LiteGraph.createNode(type);
        if (!node) throw new Error(`${type} must be registered by the real host`);
        app.graph.add(node);
        node.setSize([type === 'PromptManagerSplit' ? 1100 : 680, 1050]);
        app.canvas.ds.scale = .8;
        app.canvas.centerOnNode(node);
        return node.id;
    }, type);
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

async function showLibrary(page) {
    const button = page.locator('.pm-focused').getByRole('button', { name: 'Browse library', exact: true });
    if (await button.count()) await button.click();
}

async function browse(page, text) {
    const root = page.locator('.pm-focused');
    await showLibrary(page);
    await root.getByRole('button', { name: 'Recent', exact: true }).click();
    await root.locator('.pm-editor-results').getByRole('button').filter({ has: page.getByText(text, { exact: true }) }).click();
    return root;
}

for (const [label, type] of [['Focused', 'PromptManagerFocused'], ['Split', 'PromptManagerSplit']]) {
test.describe(label, () => {
test(`${label} definition preserves Classic encoding and exposes composition STRING sockets`, async ({ request }) => {
    const definitions = await (await request.get('/object_info')).json();
    const focused = definitions[type];
    expect(focused).toBeDefined();
    expect(focused.display_name).toBe(`Prompt Manager (${label})`);
    expect(focused.output).toEqual(definitions.PromptManager.output);
    expect(focused.input.required).toEqual(definitions.PromptManager.input.required);
    for (const name of ['prepend_text', 'append_text']) {
        expect(focused.input.optional[name][0]).toBe('STRING');
        expect(focused.input.optional[name][1]).toMatchObject({ forceInput: true, default: '' });
    }
});

test(`${label} preview, explicit load, composition, renderer changes and workflow reload preserve state`, async ({ page, request }, testInfo) => {
    const text = `Focused selected ${testInfo.project.name}`;
    await seed(request, text);
    const id = await addEditor(page, type);
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
    await page.screenshot({ path: testInfo.outputPath(`${label.toLowerCase()}-reloaded.png`) });
    await page.evaluate(async id => { const { app } = await import('/scripts/app.js'); app.graph.remove(app.graph.getNodeById(id)); }, id);
    await expect(root).toHaveCount(0);
});

test(`${label} search preview respects metadata opt-out`, async ({ page, request }, testInfo) => {
    const text = `Focused search ${testInfo.project.name}`;
    await seed(request, text, 'Incoming category', ['incoming-tag']);
    const id = await addEditor(page, type);
    const root = page.locator('.pm-focused');
    await root.getByText('Metadata · category / tags', { exact: true }).click();
    await root.getByRole('combobox', { name: 'Category', exact: true }).fill('Keep category');
    await root.getByRole('combobox', { name: 'Tags', exact: true }).fill('keep-tag');
    await showLibrary(page);
    await root.getByRole('searchbox', { name: 'Search saved prompts', exact: true }).fill(text);
    await root.getByRole('button', { name: 'Search', exact: true }).click();
    await root.locator('.pm-editor-results').getByRole('button').filter({ has: page.getByText(text, { exact: true }) }).click();
    await root.getByRole('switch', { name: 'Load metadata', exact: true }).uncheck();
    await root.getByRole('button', { name: 'Use prompt', exact: true }).click();
    await expect.poll(() => model(page, id)).toMatchObject({ text, category: 'Keep category', tags: 'keep-tag' });
});

test(`${label} connected STRING inputs remain linked through UI actions and reload`, async ({ page, request }, testInfo) => {
    const text = `Connected library ${testInfo.project.name}`;
    await seed(request, text);
    const id = await addEditor(page, type);
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
    await page.screenshot({ path: testInfo.outputPath(`${label.toLowerCase()}-disconnected.png`) });
});

test(`${label} follows actual ComfyUI light and dark palettes`, async ({ page }, testInfo) => {
    await addEditor(page, type);
    for (const palette of ['light', 'dark']) {
        await page.evaluate(async palette => {
            const { app } = await import('/scripts/app.js');
            await app.ui.settings.setSettingValue('Comfy.ColorPalette', palette);
        }, palette);
        const root = page.locator('.pm-focused');
        await expect(root).toHaveAttribute('data-ca-theme', palette);
        if (label === 'Split') {
            await expect(root.getByRole('button', { name: /^(Browse|Hide) library$/ })).toHaveCount(0);
            await expect(root.getByRole('button', { name: 'Search', exact: true })).toHaveCSS('background-color', 'rgb(8, 119, 187)');
        } else {
            await expect(root.getByRole('button', { name: 'Browse library', exact: true })).toHaveCSS('background-color', 'rgb(8, 119, 187)');
        }
        await page.screenshot({ path: testInfo.outputPath(`${label.toLowerCase()}-${palette}.png`) });
    }
});

test(`${label} Web UI button opens the workspace; legacy pages still load`, async ({ page }) => {
    await addEditor(page, type);
    const popupPromise = page.waitForEvent('popup');
    await page.locator('.pm-focused').getByRole('button', { name: 'Web UI ↗', exact: true }).click();
    const popup = await popupPromise;
    await popup.waitForLoadState('domcontentloaded');
    await expect(popup).toHaveURL(/\/prompt_manager\/workspace#\/library$/);
    await expect(popup.locator('#workspace-main h1')).toHaveText('Prompt library');
    await expect(page.locator('.pm-focused').getByRole('status').filter({ hasText: 'Web UI opened.' })).toHaveText('Web UI opened.');
    await popup.goto(new URL('/prompt_manager/gallery', page.url()).href);
    await expect(popup.locator('#addPromptBtn')).toBeVisible();
    await popup.close();
});

test(`${label} metadata completion uses saved categories and tags across renderers and reload`, async ({ page, request }, testInfo) => {
    const category = `Host completion category ${testInfo.project.name}`;
    const tag = `host-completion-${testInfo.project.name}`;
    await seed(request, `Saved autocomplete ${testInfo.project.name}`, category, [tag]);
    const id = await addEditor(page, type);
    const root = page.locator('.pm-focused');
    await root.getByRole('textbox', { name: 'Main prompt', exact: true }).fill('Autocomplete workflow');
    await root.getByText('Metadata · category / tags', { exact: true }).click();
    const categoryInput = root.getByRole('combobox', { name: 'Category', exact: true });
    const tagInput = root.getByRole('combobox', { name: 'Tags', exact: true });
    await categoryInput.fill(category.slice(0, -3));
    const categories = page.getByRole('listbox', { name: 'Saved categories', exact: true });
    await expect(categories.getByRole('option', { name: category, exact: true })).toBeVisible();
    await categoryInput.press('ArrowDown');
    await expect(categoryInput).toHaveAttribute('aria-activedescendant', /.+/);
    await categoryInput.press('Enter');
    await expect(categoryInput).toHaveValue(category);
    await expect(categoryInput).toHaveAttribute('aria-expanded', 'false');
    for (const palette of ['light', 'dark']) {
        await page.evaluate(async palette => {
            const { app } = await import('/scripts/app.js');
            await app.ui.settings.setSettingValue('Comfy.ColorPalette', palette);
        }, palette);
        await tagInput.fill(`keep, ${tag.slice(0, -3)}`);
        const tags = page.getByRole('listbox', { name: 'Saved tags', exact: true });
        const option = tags.getByRole('option', { name: tag, exact: true });
        await expect(option).toBeVisible();
        await expect(tags).toHaveAttribute('data-ca-theme', palette);
        const bounds = await tags.boundingBox();
        const viewport = page.viewportSize();
        expect(bounds.x).toBeGreaterThanOrEqual(0);
        expect(bounds.y).toBeGreaterThanOrEqual(0);
        expect(bounds.x + bounds.width).toBeLessThanOrEqual(viewport.width);
        expect(bounds.y + bounds.height).toBeLessThanOrEqual(viewport.height);
        await page.screenshot({ path: testInfo.outputPath(`${label.toLowerCase()}-metadata-${palette}.png`) });
        await option.click();
        await expect(tagInput).toHaveValue(`keep, ${tag}, `);
    }
    const expected = { text: 'Autocomplete workflow', category, tags: `keep, ${tag}, ` };
    await expect.poll(() => model(page, id)).toMatchObject(expected);
    for (const renderer of [!testInfo.project.metadata.initialRenderer, testInfo.project.metadata.initialRenderer]) {
        await page.evaluate(async renderer => {
            const { app } = await import('/scripts/app.js');
            await app.ui.settings.setSettingValue('Comfy.VueNodes.Enabled', renderer);
            app.canvas.centerOnNode(app.graph._nodes[0]);
        }, renderer);
        await expect.poll(() => page.evaluate(() => LiteGraph.vueNodesMode)).toBe(renderer);
        await expect(categoryInput).toHaveValue(category);
        await expect(tagInput).toHaveValue(expected.tags);
    }
    const queued = await page.evaluate(async id => {
        const { app } = await import('/scripts/app.js');
        const prompt = await app.graphToPrompt();
        await app.loadGraphData(app.graph.serialize());
        return prompt.output[String(id)].inputs;
    }, id);
    expect(queued).toMatchObject(expected);
    await expect(categoryInput).toHaveValue(category);
    await expect(tagInput).toHaveValue(expected.tags);
    await categoryInput.fill(category.slice(0, -3));
    await expect(categories).toBeVisible();
    await page.evaluate(async id => {
        const { app } = await import('/scripts/app.js');
        app.graph.remove(app.graph.getNodeById(id));
    }, id);
    await expect(page.getByRole('listbox', { name: 'Saved categories', includeHidden: true })).toHaveCount(0);
    await expect(page.getByRole('listbox', { name: 'Saved tags', includeHidden: true })).toHaveCount(0);
});

test(`${label} library autocomplete filters saved prompts without editing draft metadata`, async ({ page, request }, testInfo) => {
    const category = `Library category ${testInfo.project.name}`;
    const tag = `library-filter-${testInfo.project.name}`;
    const matchingText = `Matching library prompt ${testInfo.project.name}`;
    await seed(request, matchingText, category, [tag]);
    await seed(request, `Wrong category ${testInfo.project.name}`, `Other category ${testInfo.project.name}`, [tag]);
    await seed(request, `Wrong tag ${testInfo.project.name}`, category, [`other-filter-${testInfo.project.name}`]);
    const id = await addEditor(page, type);
    const root = page.locator('.pm-focused');
    await root.getByRole('textbox', { name: 'Main prompt', exact: true }).fill('Keep my draft');
    await root.getByText('Metadata · category / tags', { exact: true }).click();
    await root.getByRole('combobox', { name: 'Category', exact: true }).fill('Draft category');
    await root.getByRole('combobox', { name: 'Tags', exact: true }).fill('draft-tag');
    await showLibrary(page);
    const filterCategory = root.getByRole('combobox', { name: 'Filter category', exact: true });
    const filterTags = root.getByRole('combobox', { name: 'Filter tags', exact: true });
    await filterCategory.fill(category.slice(0, -3));
    await expect(page.getByRole('listbox', { name: 'Saved categories', exact: true }).getByRole('option', { name: category, exact: true })).toBeVisible();
    await filterCategory.press('ArrowDown');
    await filterCategory.press('Enter');
    await expect(filterCategory).toHaveValue(category);
    for (const palette of ['light', 'dark']) {
        await page.evaluate(async palette => {
            const { app } = await import('/scripts/app.js');
            await app.ui.settings.setSettingValue('Comfy.ColorPalette', palette);
        }, palette);
        await filterTags.fill(tag.slice(0, -3));
        const option = page.getByRole('listbox', { name: 'Saved tags', exact: true }).getByRole('option', { name: tag, exact: true });
        await expect(option).toBeVisible();
        await page.screenshot({ path: testInfo.outputPath(`${label.toLowerCase()}-library-filters-${palette}.png`) });
        await option.click();
        await expect(filterTags).toHaveValue(`${tag}, `);
    }
    const draft = { text: 'Keep my draft', category: 'Draft category', tags: 'draft-tag' };
    await root.getByRole('button', { name: 'Search', exact: true }).click();
    const results = root.locator('.pm-editor-results').getByRole('button');
    await expect(results).toHaveCount(1);
    await expect(results.first()).toContainText(matchingText);
    await results.first().click();
    await expect.poll(() => model(page, id)).toMatchObject(draft);
    for (const renderer of [!testInfo.project.metadata.initialRenderer, testInfo.project.metadata.initialRenderer]) {
        await page.evaluate(async renderer => {
            const { app } = await import('/scripts/app.js');
            await app.ui.settings.setSettingValue('Comfy.VueNodes.Enabled', renderer);
            app.canvas.centerOnNode(app.graph._nodes[0]);
        }, renderer);
        await expect.poll(() => page.evaluate(() => LiteGraph.vueNodesMode)).toBe(renderer);
        await expect(filterCategory).toHaveValue(category);
        await expect(filterTags).toHaveValue(`${tag}, `);
        await expect.poll(() => model(page, id)).toMatchObject(draft);
    }
    const queued = await page.evaluate(async id => {
        const { app } = await import('/scripts/app.js');
        const prompt = await app.graphToPrompt();
        await app.loadGraphData(app.graph.serialize());
        return prompt.output[String(id)].inputs;
    }, id);
    expect(queued).toMatchObject(draft);
    await expect(filterCategory).toHaveValue(category);
    await expect(filterTags).toHaveValue(`${tag}, `);
    await root.getByRole('button', { name: 'Search', exact: true }).click();
    await expect(results).toHaveCount(1);
    await expect(results.first()).toContainText(matchingText);
    await expect.poll(() => model(page, id)).toMatchObject(draft);
});

if (label === 'Split') {
    test('Split keeps its library visible beside the editor and stacks narrow nodes', async ({ page, request }, testInfo) => {
        for (let index = 0; index < 21; index++) {
            await seed(request, `Split layout recent ${testInfo.project.name} ${index}`);
        }
        const id = await addEditor(page, type);
        const root = page.locator('.pm-split');
        await page.evaluate(async id => {
            const { app } = await import('/scripts/app.js');
            const node = app.graph.getNodeById(id);
            node.setSize([900, 650]);
            app.canvas.centerOnNode(node);
        }, id);
        await expect(root).toHaveAttribute('aria-label', 'Split prompt editor');
        const savedProperties = await page.evaluate(async id => {
            const { app } = await import('/scripts/app.js');
            return app.graph.getNodeById(id).serialize().properties;
        }, id);
        expect(savedProperties).toHaveProperty('splitUI');
        expect(savedProperties).not.toHaveProperty('focusedUI');
        await expect(root.getByRole('button', { name: /^(Browse|Hide) library$/ })).toHaveCount(0);
        const editor = root.getByRole('textbox', { name: 'Main prompt', exact: true });
        const library = root.getByRole('region', { name: 'Prompt library', exact: true });
        await expect(library).toBeVisible();
        const captureGeometry = async phase => {
            const geometry = await page.evaluate(async id => {
                const { app } = await import('/scripts/app.js');
                const node = app.graph.getNodeById(id);
                const root = node._focusedView.element;
                const bounds = selector => root.querySelector(selector)?.getBoundingClientRect().toJSON();
                const ancestors = [];
                for (let element = root; element && element !== document.body; element = element.parentElement) {
                    const style = getComputedStyle(element);
                    ancestors.push({ tag: element.tagName, className: element.className, bounds: element.getBoundingClientRect().toJSON(), inlineStyle: element.style.cssText, computed: Object.fromEntries(['height', 'min-height', 'max-height', 'display', 'overflow', 'flex', 'contain', 'position'].map(name => [name, style.getPropertyValue(name)])) });
                }
                return { nodeSize: [...node.size], ancestors, root: root.getBoundingClientRect().toJSON(), library: bounds('.pm-editor-library'), results: bounds('.pm-editor-results'), use: bounds('.pm-editor-selection button'), footer: bounds(':scope > .pm-editor-status') };
            }, id);
            const path = testInfo.outputPath(`split-${phase}-geometry.json`);
            await writeFile(path, JSON.stringify(geometry, null, 2));
            await testInfo.attach(`split-${phase}-geometry.json`, { path, contentType: 'application/json' });
            return geometry;
        };
        const beforeRecent = await captureGeometry('before-recent');
        await root.getByRole('button', { name: 'Recent', exact: true }).click();
        const results = library.locator('.pm-editor-results').getByRole('button');
        await expect(results).toHaveCount(20);
        const promptText = results.first().locator('span').first();
        const selectedText = await promptText.innerText();
        await results.first().click();
        const use = root.getByRole('button', { name: 'Use prompt', exact: true });
        const afterSelection = await captureGeometry('after-selection');
        await page.screenshot({ path: testInfo.outputPath('split-default-selected.png') });
        expect.soft((await promptText.boundingBox())?.height || 0).toBeGreaterThan(10);
        expect.soft(afterSelection.root.height).toBeLessThanOrEqual(beforeRecent.root.height + 1);
        await use.click();
        await expect(editor).toHaveValue(selectedText);
        await expect.poll(async () => {
            const main = await editor.boundingBox();
            const browser = await library.boundingBox();
            return browser.x >= main.x + main.width;
        }).toBe(true);
        await page.screenshot({ path: testInfo.outputPath('split-wide.png') });
        await page.evaluate(async id => {
            const { app } = await import('/scripts/app.js');
            const node = app.graph.getNodeById(id);
            node.setSize([480, 1050]);
            app.canvas.centerOnNode(node);
        }, id);
        await expect.poll(async () => {
            const main = await editor.boundingBox();
            const browser = await library.boundingBox();
            return browser.y >= main.y + main.height;
        }).toBe(true);
        await expect(library).toBeVisible();
        await page.screenshot({ path: testInfo.outputPath('split-narrow.png') });
    });
}

});
}
