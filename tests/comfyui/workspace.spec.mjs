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
        await workspace.goto('/prompt_manager/workspace');
        await expect(workspace.locator('#workspace-main h1')).toHaveText('Overview');
        await expect(workspace.locator('dt').filter({ hasText: /^Prompts$/ }).locator('+ dd'))
            .toHaveText(stats.total_prompts.toLocaleString());
        await workspace.getByRole('combobox', { name: 'Appearance' }).selectOption('light');
        await expect(workspace.locator('body')).toHaveAttribute('data-ca-theme', 'light');
        await workspace.getByRole('navigation', { name: 'Workspace', exact: true })
            .getByRole('link', { name: 'Images', exact: true }).click();
        await expect(workspace.locator('#workspace-main h1')).toHaveText('Images');
        await expect(workspace.locator('#workspace-main a')).toHaveAttribute('href', '/prompt_manager/gallery');
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
