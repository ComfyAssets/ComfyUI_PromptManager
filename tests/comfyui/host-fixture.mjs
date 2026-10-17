import { expect } from '../../tools/vendor/node_modules/@playwright/test/index.mjs';
import runtime from '../../tools/vendor/comfyui-runtime.json' with { type: 'json' };

export function installHostHooks(test, expectedTypes = ['PromptManager', 'PromptManagerText', 'PromptSearchList']) {
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
        await page.waitForFunction(async expectedTypes => {
            const { app } = await import('/scripts/app.js');
            return !!app.graph && expectedTypes.every(name => globalThis.LiteGraph?.registered_node_types?.[name]);
        }, expectedTypes);
        await expect.poll(() => page.evaluate(() => LiteGraph.vueNodesMode)).toBe(testInfo.project.metadata.initialRenderer);
        await page.evaluate(async () => { const { app } = await import('/scripts/app.js'); app.graph.clear(); });
    });

    test.afterEach(async ({ page }) => {
        expect(page.hostErrors).toEqual([]);
    });

}
