import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';
import { seedConcept } from './concept-inventory.mjs';

// #320 Thumbnail location (D196–D201, the approved 320 mock) on the concept-shaped real fixture: the row in System ›
// Sources & scans reviews and runs Move thumbnails both ways, the review refuses a folder in place, and an unplugged
// thumbnail folder shows the notice whose Recreate folder brings it back. The hooks are thumbnail_location_fixture.py's.
const workspace = '/prompt_manager/workspace';
const NOTICE_KEY = 'pm_update_notice_dismissed';

test.beforeEach(async ({ page, request, browserName }) => {
    await startCoverage(page, browserName);
    await seedConcept(request);
    await expect(await request.post('/__test__/system/reset')).toBeOK();
    await expect(await request.post('/__test__/thumbnail-location', { data: { mode: 'source' } })).toBeOK();
    page.locationErrors = [];
    page.on('pageerror', error => page.locationErrors.push(error.message));
    await page.addInitScript(key => { if (location.protocol.startsWith('http')) localStorage.setItem(key, '3.2.0'); }, NOTICE_KEY);
});

test.afterEach(async ({ page, request, browserName }) => {
    await saveCoverage(page, browserName);
    await request.post('/__test__/thumbnail-location', { data: { mode: 'source' } });
    expect(page.locationErrors).toEqual([]);
});

const block = page => page.locator('#location-block');
const dialog = page => page.locator('dialog#modal[open]');

async function files(request) {
    const response = await request.get('/__test__/thumbnail-files');
    await expect(response).toBeOK();
    return response.json();
}

async function openSources(page) {
    await page.goto(`${workspace}#/system/sources`);
    await expect(block(page).locator('#location-status')).not.toHaveText('');
}

/** Review the row's choice and start the move; resolves once its job view says it finished. */
async function moveFromRow(page) {
    const planned = page.waitForResponse(response => response.url().endsWith('/images/thumbnails/plan')
        && response.request().method() === 'POST');
    await block(page).getByRole('button', { name: 'Review move…' }).click();
    expect((await planned).status()).toBe(200);
    await expect(dialog(page).locator('h2')).toHaveText('Move thumbnails');
    await dialog(page).getByRole('button', { name: 'Move thumbnails' }).click();
    await expect(page.locator('#workspace-main h1')).toHaveText('Move thumbnails');
    await expect(page.locator('#workspace-main')).toContainText('Thumbnails moved to');
}

test('the row moves thumbnails into one folder and back', async ({ page, request }) => {
    await openSources(page);
    const row = block(page);
    await expect(row.getByRole('radio', { name: /Inside each source/ })).toBeChecked();
    await expect(row.locator('#location-status')).toHaveText('Now: inside each source.');
    await expect(row.getByRole('button', { name: 'Review move…' })).toHaveAttribute('aria-disabled', 'true');
    await expect(row.locator('#location-path')).toBeDisabled();

    await row.getByRole('radio', { name: /One folder/ }).check();
    await expect(row.locator('#location-path')).toBeEnabled();
    await expect(row.locator('#location-path')).toHaveValue(/default-thumbs$/);
    await expect(row.locator('#location-status')).toHaveText(/^Now: inside each source\. The suggested folder is next to your library/);
    await expect(row.getByRole('button', { name: 'Review move…' })).toHaveAttribute('aria-disabled', 'false');
    await moveFromRow(page);
    await expect(page.locator('#job-phase')).toHaveText(/^Thumbnails are now read from the new location/);
    const moved = await files(request);
    expect(moved.folder.length).toBeGreaterThan(0);
    expect(moved.folder.every(name => /^[a-z0-9_-]+-[0-9a-f]{8}\//.test(name))).toBe(true);
    expect(moved.source_thumbnails).toBe(false);

    // Images shows thumbnails served from the folder.
    const served = page.waitForResponse(response => response.url().includes('/images/serve/thumbnails/')
        && response.status() === 200);
    await page.goto(`${workspace}#/images`);
    await served;

    await openSources(page);
    await expect(block(page).locator('#location-status')).toHaveText(/^Now: one folder, .*default-thumbs\. Using the default folder/);
    await block(page).getByRole('radio', { name: /Inside each source/ }).check();
    await moveFromRow(page);
    const back = await files(request);
    expect(back.source_thumbnails).toBe(true);
    expect(back.folder_exists).toBe(false);
});

test('a folder the server refuses is explained under the field', async ({ page }) => {
    await openSources(page);
    const row = block(page);
    await row.getByRole('radio', { name: /One folder/ }).check();
    await row.locator('#location-path').fill('thumbnails');
    await row.getByRole('button', { name: 'Review move…' }).click();
    await expect(row.locator('#location-problem')).toHaveText(
        'Enter a full folder path, such as D:\\Thumbnails or /mnt/fast/thumbnails.');
    await expect(row.locator('.location-path')).toHaveAttribute('aria-invalid', 'true');
    await expect(dialog(page)).toHaveCount(0);
    await row.locator('#location-path').fill('/somewhere/else');
    await expect(row.locator('#location-problem')).toHaveText('');
});

test('an unplugged folder shows the notice and Recreate folder brings it back', async ({ page, request }) => {
    await expect(await request.post('/__test__/thumbnail-location', { data: { mode: 'folder' } })).toBeOK();
    const removed = await request.post('/__test__/thumbnail-folder/remove');
    expect((await removed.json()).available).toBe(false);
    await openSources(page);
    const notice = page.locator('#unavailable-notice');
    await expect(notice.locator('h2')).toHaveText('The thumbnail folder is unavailable');
    await expect(notice).toContainText('is missing. Originals are shown and no thumbnails are written until it is back.');
    await expect(block(page).locator('#location-status')).toHaveText(/· unavailable\. Originals are shown\.$/);
    await notice.getByRole('button', { name: 'Recreate folder' }).click();
    await expect(dialog(page).locator('h2')).toHaveText('Recreate the thumbnail folder?');
    const recreated = page.waitForResponse(response => response.url().endsWith('/location/recreate'));
    await dialog(page).getByRole('button', { name: 'Recreate folder' }).click();
    expect((await recreated).status()).toBe(200);
    await expect(notice).toHaveCount(0);
    await expect(block(page).locator('#location-status')).toHaveText(/^Now: one folder, .*thumbs\.$/);
});

test('a folder of your own needs the confirmation before it moves', async ({ page, request }) => {
    const custom = (await files(request)).custom_path;
    await openSources(page);
    const row = block(page);
    await row.getByRole('radio', { name: /One folder/ }).check();
    await row.locator('#location-path').fill(custom);
    await row.getByRole('button', { name: 'Review move…' }).click();
    const review = dialog(page);
    await expect(review.locator('dt')).toContainText(['From', 'To', 'Thumbnails', 'Free space', 'Removed after the move']);
    await expect(review.locator('dd').nth(1)).toHaveText(`${custom} (new folder)`);
    const move = review.getByRole('button', { name: 'Move thumbnails' });
    await expect(move).toHaveAttribute('aria-disabled', 'true');
    await review.getByRole('checkbox', { name: /may create its own folders/ }).check();
    await expect(move).toHaveAttribute('aria-disabled', 'false');
    await move.click();
    await expect(page.locator('#workspace-main')).toContainText('Thumbnails moved to');
    expect((await files(request)).folder.length).toBeGreaterThan(0);
});

test('another folder’s marker offers Use this folder', async ({ page, request }) => {
    await expect(await request.post('/__test__/thumbnail-location', { data: { mode: 'folder' } })).toBeOK();
    await expect(await request.post('/__test__/thumbnail-folder/foreign')).toBeOK();
    await openSources(page);
    const notice = page.locator('#unavailable-notice');
    await expect(notice).toContainText('holds another thumbnail folder');
    await notice.getByRole('button', { name: 'Use this folder' }).click();
    await expect(notice).toHaveCount(0);
    await expect(block(page).locator('#location-status')).toHaveText(/^Now: one folder, .*thumbs\.$/);
});
