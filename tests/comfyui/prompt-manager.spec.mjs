import { test, expect } from '../../tools/vendor/node_modules/@playwright/test/index.mjs';
import runtime from '../../tools/vendor/comfyui-runtime.json' with { type: 'json' };

test.beforeEach(async ({ page, context, request }, testInfo) => {
    const response = await request.get('/system_stats');
    expect(response.ok()).toBe(true);
    const info = (await response.json()).system;
    expect(info.comfyui_version).toBe(runtime.serverVersion);
    expect(info.pytorch_version).toBe(`${runtime.torchVersion}+cpu`);
    expect(info.required_frontend_version).toBe(runtime.frontendVersion);
    expect(info.comfy_package_versions.find(item => item.name === 'comfyui-frontend-package')?.installed).toBe(runtime.frontendVersion);
    await testInfo.attach('host-versions.json', { body: JSON.stringify(info, null, 2), contentType: 'application/json' });
    page.hostErrors = [];
    page.on('pageerror', error => page.hostErrors.push(error.message));
    await context.route('**/*', async route => {
        const request = route.request();
        const path = new URL(request.url()).pathname.replace(/^\/api\//, '/');
        if (path === '/settings' && request.method() === 'GET') {
            const response = await route.fetch();
            return route.fulfill({ json: { ...await response.json(),
                'Comfy.VueNodes.Enabled': testInfo.project.metadata.initialRenderer,
                'Comfy.TutorialCompleted': true, 'Comfy.Workflow.Persist': false,
                'Comfy.Notification.ShowVersionUpdates': false } });
        }
        if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method()) && !path.startsWith('/prompt_manager/')) return route.fulfill({ json: {} });
        return route.continue();
    });
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('status', { name: 'Loading ComfyUI' })).toBeHidden({ timeout: 30000 });
    await page.waitForFunction(async () => {
        const { app } = await import('/scripts/app.js');
        return !!app.graph && ['PromptManager', 'PromptManagerText', 'PromptSearchList'].every(name => globalThis.LiteGraph?.registered_node_types?.[name]);
    });
    await expect.poll(() => page.evaluate(() => LiteGraph.vueNodesMode)).toBe(testInfo.project.metadata.initialRenderer);
    await page.evaluate(async () => { const { app } = await import('/scripts/app.js'); app.graph.clear(); });
});

test.afterEach(async ({ page }) => {
    expect(page.hostErrors).toEqual([]);
});

for (const type of ['PromptManager', 'PromptManagerText']) {
    test(`${type} selects real database prompts and preserves text across renderers and reload`, async ({ page, request }, testInfo) => {
        const text = `Host test ${type} ${testInfo.project.name}`;
        const saved = await request.post('/prompt_manager/save', { data: { text, category: 'Host tests', tags: ['host-test'] } });
        expect(saved.ok()).toBe(true);
        expect((await saved.json()).success).toBe(true);
        const id = await page.evaluate(async type => {
            const { app } = await import('/scripts/app.js');
            const node = LiteGraph.createNode(type);
            app.graph.add(node);
            node.setSize([650, 800]);
            app.canvas.centerOnNode(node);
            return node.id;
        }, type);
        const recent = page.getByRole('button', { name: '📋 Recent', exact: true });
        await expect(recent).toBeVisible();
        if (type === 'PromptManager') {
            await expect(page.locator('.pm-classic')).toBeVisible();
            await expect(page.getByRole('button', { name: '🔍 Search', exact: true })).toHaveCSS('background-color', 'rgb(8, 119, 187)');
        }
        await recent.click();
        await page.getByText(text, { exact: true }).click();
        const state = () => page.evaluate(async id => {
            const { app } = await import('/scripts/app.js');
            const node = app.graph.getNodeById(id);
            return Object.fromEntries(node.widgets.filter(widget => ['text', 'category', 'tags'].includes(widget.name)).map(widget => [widget.name, widget.value]));
        }, id);
        await expect.poll(state).toMatchObject({ text, category: 'Host tests', tags: 'host-test' });
        for (const renderer of [!testInfo.project.metadata.initialRenderer, testInfo.project.metadata.initialRenderer]) {
            await page.evaluate(async renderer => {
                const { app } = await import('/scripts/app.js');
                await app.ui.settings.setSettingValue('Comfy.VueNodes.Enabled', renderer);
                app.canvas.ds.scale = .8;
                app.canvas.centerOnNode(app.graph._nodes[0]);
            }, renderer);
            await expect.poll(() => page.evaluate(() => LiteGraph.vueNodesMode)).toBe(renderer);
            await expect(recent).toHaveCount(1);
            await expect(recent).toBeVisible();
            expect(await state()).toMatchObject({ text });
        }
        const output = await page.evaluate(async id => {
            const { app } = await import('/scripts/app.js');
            const prompt = await app.graphToPrompt();
            const workflow = app.graph.serialize();
            await app.loadGraphData(workflow);
            return prompt.output[String(id)].inputs;
        }, id);
        expect(output.text).toBe(text);
        await expect.poll(state).toMatchObject({ text, category: 'Host tests', tags: 'host-test' });
        await expect(recent).toHaveCount(1);
        await page.screenshot({ path: testInfo.outputPath(`${type}-reloaded.png`) });
        const removedStyle = await page.evaluate(async id => {
            const { app } = await import('/scripts/app.js');
            const node = app.graph.getNodeById(id);
            const style = node._styleSheet;
            app.graph.remove(node);
            return !style.isConnected;
        }, id);
        expect(removedStyle).toBe(true);
        await expect(recent).toHaveCount(0);
    });
}

test('all node schemas and extension entrypoints load from the real host', async ({ page, request }) => {
    const definitions = await (await request.get('/object_info')).json();
    expect(definitions.PromptManager.display_name).toBe('Prompt Manager (Classic)');
    for (const name of ['PromptManager', 'PromptManagerText', 'PromptSearchList']) expect(definitions[name]?.name).toBe(name);
    const extensions = await (await request.get('/extensions')).json();
    expect(extensions.filter(path => path.startsWith('/extensions/promptmanager/'))).toEqual(['/extensions/promptmanager/prompt_manager.js']);
    const result = await page.evaluate(async () => {
        const { app } = await import('/scripts/app.js');
        const node = LiteGraph.createNode('PromptSearchList');
        app.graph.add(node);
        const workflow = app.graph.serialize();
        await app.loadGraphData(workflow);
        return { type: app.graph._nodes[0].type, extension: app.extensions.some(item => item.name === 'PromptManager.UI') };
    });
    expect(result).toEqual({ type: 'PromptSearchList', extension: true });
});

test('node Web UI button opens actual standalone pages', async ({ page }) => {
    await page.evaluate(async () => {
        const { app } = await import('/scripts/app.js');
        const node = LiteGraph.createNode('PromptManager');
        app.graph.add(node);
        app.canvas.centerOnNode(node);
    });
    const popupPromise = page.waitForEvent('popup');
    await page.getByRole('button', { name: '🌐 Web UI', exact: true }).click();
    const popup = await popupPromise;
    await popup.waitForLoadState('domcontentloaded');
    await expect(popup).toHaveURL(/\/prompt_manager\/(web|admin)/);
    await expect(popup.locator('body')).toContainText('Prompt');
    await popup.goto(new URL('/prompt_manager/gallery', page.url()).href);
    await expect(popup.locator('#addPromptBtn')).toBeVisible();
    await popup.close();
});

test('Classic controls follow actual ComfyUI light and dark palettes', async ({ page }, testInfo) => {
    await page.evaluate(async () => {
        const { app } = await import('/scripts/app.js');
        const node = LiteGraph.createNode('PromptManager');
        app.graph.add(node);
        node.setSize([650, 800]);
        app.canvas.centerOnNode(node);
    });
    for (const palette of ['light', 'dark']) {
        await page.evaluate(async palette => {
            const { app } = await import('/scripts/app.js');
            await app.ui.settings.setSettingValue('Comfy.ColorPalette', palette);
        }, palette);
        await expect(page.locator('.pm-classic')).toHaveAttribute('data-ca-theme', palette);
        await expect(page.getByRole('button', { name: '🔍 Search', exact: true })).toHaveCSS('background-color', 'rgb(8, 119, 187)');
        await page.screenshot({ path: testInfo.outputPath(`classic-${palette}.png`) });
    }
});
