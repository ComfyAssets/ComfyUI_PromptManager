import { test, expect } from '../../tools/vendor/node_modules/@playwright/test/index.mjs';
import { installHostHooks } from './host-fixture.mjs';

installHostHooks(test);

test('workspace serves real library data beside either ComfyUI renderer', async ({ page, context, request }, testInfo) => {
    const saved = await request.post('/prompt_manager/save', { data: {
        text: `Workspace host smoke ${testInfo.project.name}`, tags: ['workspace-host'],
    } });
    expect(saved.ok()).toBe(true);
    const stats = (await (await request.get('/prompt_manager/stats')).json()).stats;
    const workspace = await context.newPage();
    const errors = [];
    workspace.on('pageerror', error => errors.push(error.message));
    try {
        await workspace.goto('/prompt_manager/workspace#/overview');
        await expect(workspace.locator('#workspace-main h1')).toHaveText('Overview');
        await expect(workspace.locator('dt').filter({ hasText: /^Prompts$/ }).locator('+ dd'))
            .toHaveText(stats.total_prompts.toLocaleString());
        await workspace.getByRole('combobox', { name: 'Appearance' }).selectOption('light');
        await expect(workspace.locator('body')).toHaveAttribute('data-ca-theme', 'light');
        const imageResponse = workspace.waitForResponse(response => {
            const url = new URL(response.url());
            return url.pathname === '/prompt_manager/images/output' && url.searchParams.get('mode') === 'workspace';
        });
        await workspace.getByRole('navigation', { name: 'Workspace', exact: true })
            .getByRole('link', { name: /^Images(?:\s*[\d,]+)?$/ }).click();
        await expect(workspace.locator('#workspace-main h1')).toHaveText('Images');
        const images = await imageResponse;
        expect(images.ok()).toBe(true);
        const collection = await images.json();
        expect(collection.success).toBe(true);
        await expect(workspace.getByRole('list', { name: 'Image collection', exact: true })).toBeAttached();
        if (collection.images.length) {
            await expect(workspace.locator('article[data-image-key]').first()).toBeVisible();
        } else {
            await expect(workspace.getByText('No matching images or media. Try changing the filters.', { exact: true })).toBeVisible();
        }
        await expect(workspace.locator('#workspace-main a[href="/prompt_manager/gallery"]')).toHaveAttribute('href', '/prompt_manager/gallery');
        await workspace.reload();
        await expect(workspace.locator('#workspace-main h1')).toHaveText('Images');
        await expect(workspace.locator('body')).toHaveAttribute('data-ca-theme', 'light');
        await workspace.screenshot({ path: testInfo.outputPath('workspace-host.png') });
        expect(errors).toEqual([]);
        expect(await page.evaluate(() => LiteGraph.vueNodesMode)).toBe(testInfo.project.metadata.initialRenderer);
    } finally {
        await workspace.close();
    }
});
