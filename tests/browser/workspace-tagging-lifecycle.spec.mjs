// The Tag image dialog's capture lifetime when the page or the dialog goes away mid-flight (#249 PR C final review I4,
// I5), run on all four projects: leaving the page with the dialog open releases the capture (a `keepalive` DELETE sent
// on `pagehide`, which also closes the dialog), and closing the dialog while its save is in flight keeps the capture
// until the save settles, then still reports the save. The fixture is tests/browser/workspace_tagging_fixture.py.
import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';

const images = '/prompt_manager/workspace#/images';
const FIRST = 'tagging/dunes_00001.png';
const TAGS_URL = '**/prompt_manager/image-annotations/tags';
const dialog = page => page.getByRole('dialog', { name: 'Tag image', exact: true });
const manual = page => dialog(page).getByRole('textbox', { name: 'Image tags · edit manually', exact: true });
const tagButton = page => page.locator(`article[data-image-key="0:${FIRST}"]`).getByRole('button', { name: 'Tag or auto-tag dunes_00001.png', exact: true });
const openCaptures = async request => (await (await request.get('/__test__/tagging/captures')).json()).captures;

test.beforeEach(async ({ page, request, browserName }) => {
    await startCoverage(page, browserName);
    await expect(await request.post('/__test__/tagging/reset', { data: {} })).toBeOK();
    page.problems = [];
    page.on('pageerror', error => page.problems.push(`pageerror: ${error.message}`));
    page.on('console', message => { if (message.type() === 'error') page.problems.push(`console: ${message.text()}`); });
});
test.afterEach(async ({ page, browserName }) => {
    await saveCoverage(page, browserName);
    expect(page.problems).toEqual([]);
});

async function openTag(page) {
    await page.goto(images);
    const observed = page.waitForResponse(item => item.url().endsWith('/image-annotations/observe'));
    await tagButton(page).click();
    await expect(dialog(page)).toBeVisible();
    expect((await observed).status()).toBe(200);
    await expect(manual(page)).toBeEditable();
}

test('leaving the page with the dialog open releases its capture without a console error (I4)', async ({ page, request }) => {
    await openTag(page);
    expect(await openCaptures(request)).toBe(1);
    // Another document: the old one's pagehide closes the dialog and sends the release with keepalive.
    await page.goto('/prompt_manager/workspace?left=1#/tags');
    await expect(page.locator('#workspace-main h1')).toHaveText('Tags & categories');
    await expect.poll(() => openCaptures(request)).toBe(0);
    await expect(dialog(page)).toHaveCount(0);
});

test('closing the dialog during a save releases the capture after the save and still reports it (I5)', async ({ page, request }) => {
    await openTag(page);
    await manual(page).fill('saved_after_close');
    const order = [];
    page.on('response', item => { if (item.url().endsWith('/image-annotations/tags')) order.push(`PUT ${item.status()}`); });
    page.on('request', item => { if (item.url().includes('/image-annotations/capture/') && item.method() === 'DELETE') order.push('DELETE'); });
    let release;
    const held = new Promise(resolve => { release = resolve; });
    const sent = new Promise(resolve => {
        page.route(TAGS_URL, async route => { resolve(); await held; await route.continue(); });
    });
    await dialog(page).getByRole('button', { name: 'Save manual tags', exact: true }).click();
    await sent;
    await page.keyboard.press('Escape');
    await expect(dialog(page)).toHaveCount(0);
    expect(await openCaptures(request)).toBe(1);
    expect(order).toEqual([]);
    release();
    await expect(page.locator('#workspace-toast')).toHaveText('1 tags saved for dunes_00001.png.');
    await expect.poll(() => order).toEqual(['PUT 200', 'DELETE']);
    await expect.poll(() => openCaptures(request)).toBe(0);
    // The page followed the late save: the selected file's details show the new image tag.
    await expect(page.getByRole('complementary', { name: 'Selected image details', exact: true }).getByText('saved_after_close', { exact: true })).toBeVisible();
    await page.unroute(TAGS_URL);
});
