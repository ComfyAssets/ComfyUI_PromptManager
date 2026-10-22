// Each Playwright worker owns its server: `request`, `page` and manual
// `browser.newContext()` all reach it through `baseURL`, and another server
// process never sees its database.
import { test, expect } from './fixtures.mjs';
import { startBrowserServer } from '../../tools/vendor/start-browser-server.mjs';

const search = text => `/prompt_manager/search?text=${encodeURIComponent(text)}`;

test('every fixture reaches this worker\'s own server and another server keeps its own database', async ({ request, page, browser, baseURL, workerServer }, testInfo) => {
    expect(baseURL).toBe(workerServer.url);
    await expect(await request.post('/__test__/reset')).toBeOK();
    const marker = `worker ${testInfo.workerIndex} isolation ${testInfo.project.name} ${testInfo.repeatEachIndex}`;
    await expect(await request.post('/prompt_manager/save', { data: { text: marker } })).toBeOK();
    expect((await (await request.get(search(marker))).json()).count).toBe(1);

    const viaPage = await page.request.get(search(marker));
    expect(viaPage.url().startsWith(`${baseURL}/`)).toBe(true);
    expect((await viaPage.json()).count).toBe(1);
    const context = await browser.newContext();
    try {
        const response = await (await context.newPage()).goto(search(marker));
        expect(response.url().startsWith(`${baseURL}/`)).toBe(true);
        expect((await response.json()).count).toBe(1);
    } finally {
        await context.close();
    }

    const other = await startBrowserServer();
    try {
        expect(other.url).not.toBe(baseURL);
        expect((await (await fetch(`${other.url}${search(marker)}`)).json()).count).toBe(0);
    } finally {
        await other.stop();
    }
});
