import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';
import { seedConceptAt, openProduction } from './concept-inventory.mjs';
import { startAndAwaitJob } from './job-end.mjs';

// #480 (D159): Images › Delete…: the review asks every time (nothing preselected), "Remove from the library only"
// hides the file and keeps it on disk, "Also move the files to the trash" moves it (the server's fake trash here).
test.use({ locale: 'en-US', timezoneId: 'UTC' });

const CARDS = '#workspace-main article[data-image-key]';
const bar = page => page.locator('#workspace-main #bulkbar');
const review = page => page.getByRole('dialog', { name: /^Delete \d+ images?$/ });
const toast = page => page.locator('#workspace-toast');

test.beforeEach(async ({ page, request, browserName }) => {
    await startCoverage(page, browserName);
    await seedConceptAt(page, request);
    page.deleteErrors = [];
    page.on('pageerror', error => page.deleteErrors.push(error.message));
    page.on('dialog', dialog => { page.deleteErrors.push(`browser ${dialog.type()}`); dialog.dismiss().catch(() => {}); });
});

test.afterEach(async ({ page, browserName }) => {
    await saveCoverage(page, browserName);
    expect(page.deleteErrors).toEqual([]);
});

async function openImages(page) {
    await page.setViewportSize({ width: 1600, height: 1100 });
    await openProduction(page, 'images', 'dark');
    await expect(page.locator(CARDS).first()).toBeVisible();
    return page.locator(CARDS).count();
}

async function check(page, index) {
    const card = page.locator(CARDS).nth(index);
    const key = await card.getAttribute('data-image-key');
    await card.locator('.image-overlays input[type="checkbox"]').check();
    return key;
}

async function deleteChecked(page, mode) {
    await bar(page).getByRole('button', { name: 'Delete…', exact: true }).click();
    const dialog = review(page);
    await expect(dialog).toBeVisible();
    const primary = dialog.locator('.dialog-actions button.danger');
    // Ask every time: no mode is preselected and the primary waits for one.
    await expect(dialog.getByRole('radio')).toHaveCount(2);
    for (const radio of await dialog.getByRole('radio').all()) await expect(radio).not.toBeChecked();
    await expect(primary).toHaveAttribute('aria-disabled', 'true');
    await expect(dialog.locator('dl.meta-grid')).toBeHidden();
    const label = mode === 'trash' ? 'Also move the files to the trash' : 'Remove from the library only';
    await dialog.getByRole('radio', { name: new RegExp(`^${label}`) }).check();
    await expect(dialog.locator('dl.meta-grid')).toBeVisible();
    await expect(primary).not.toHaveAttribute('aria-disabled', 'true');
    return { dialog, primary };
}

for (const mode of ['library', 'trash']) {
    test(`${mode}: the deleted image leaves Images and stays gone after a reload`, async ({ page }) => {
        const before = await openImages(page);
        const key = await check(page, 0);
        const { dialog, primary } = await deleteChecked(page, mode);
        await expect(dialog.locator('dt', { hasText: 'Media on disk' })).toBeVisible();
        await expect(primary).toHaveText(mode === 'trash' ? 'Move 1 image to the trash' : 'Remove 1 image from the library');
        await startAndAwaitJob(page, () => primary.click());
        await expect(toast(page)).toContainText(mode === 'trash' ? 'Moved 1 image to the trash.' : 'Removed 1 image from the library.');
        await expect(page.locator(`${CARDS}[data-image-key="${key}"]`)).toHaveCount(0);
        await expect(page.locator(CARDS)).toHaveCount(before - 1);
        await expect(bar(page)).toBeEmpty();
        await page.reload();
        await expect(page.locator(CARDS).first()).toBeVisible();
        await expect(page.locator(`${CARDS}[data-image-key="${key}"]`)).toHaveCount(0);
    });
}

// #501: the sidebar's Images count (read once at mount) follows a finished delete without a reload.
test('a finished delete re-reads the sidebar Images count', async ({ page }) => {
    await openImages(page);
    const badge = page.locator('#primary-nav a[data-route="images"] .nav-count');
    await expect(badge).toHaveText(/^\d[\d,]*$/);
    const before = Number((await badge.textContent()).replace(/,/g, ''));
    await check(page, 0);
    const { primary } = await deleteChecked(page, 'library');
    await startAndAwaitJob(page, () => primary.click());
    await expect(toast(page)).toContainText('Removed 1 image from the library.');
    await expect(badge).toHaveText((before - 1).toLocaleString('en-US'));
});

// #533: the toast says "started" for as long as the job's end has not reached the page, then says how it ended.
test('a slow job keeps its started toast until the end arrives', async ({ page }) => {
    await openImages(page);
    await check(page, 0);
    let release;
    const held = new Promise(resolve => { release = resolve; });
    await page.route('**/prompt_manager/jobs/*/events', async route => { await held; await route.continue(); });
    const { primary } = await deleteChecked(page, 'library');
    await primary.click();
    await expect(toast(page)).toHaveText('Delete images (remove from the library) started.');
    release();
    await expect(toast(page)).toContainText('Removed 1 image from the library.');
});

test('Cancel changes nothing', async ({ page }) => {
    const before = await openImages(page);
    await check(page, 0);
    const { dialog } = await deleteChecked(page, 'library');
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(dialog).toBeHidden();
    await expect(page.locator(CARDS)).toHaveCount(before);
});

test('switching modes redraws the rows from one review', async ({ page }) => {
    await openImages(page);
    await check(page, 0);
    let previews = 0;
    page.on('request', request => { if (request.url().endsWith('/images/delete/preview')) previews += 1; });
    const { dialog, primary } = await deleteChecked(page, 'library');
    await expect(dialog.locator('dd', { hasText: 'Files stay on disk, hidden from Images.' })).toBeVisible();
    await dialog.getByRole('radio', { name: /^Also move the files to the trash/ }).check();
    await expect(dialog.locator('dd', { hasText: 'Nothing is deleted permanently' })).toBeVisible();
    await expect(primary).toHaveText('Move 1 image to the trash');
    expect(previews).toBe(1);
});

test('Delete… in the inspector reviews that one image', async ({ page }) => {
    const before = await openImages(page);
    const card = page.locator(CARDS).first();
    await card.getByRole('button', { name: /^Inspect / }).click();
    const button = page.locator('#workspace-main .file-inspector button.danger', { hasText: 'Delete…' });
    await expect(button).toBeVisible();
    await button.click();
    const dialog = review(page);
    await expect(dialog).toBeVisible();
    await dialog.getByRole('radio', { name: /^Remove from the library only/ }).check();
    await dialog.locator('.dialog-actions button.danger').click();
    await expect(page.locator(CARDS)).toHaveCount(before - 1);
});

test('Delete… in the viewer closes it after the delete', async ({ page }) => {
    const before = await openImages(page);
    await page.locator(CARDS).first().getByRole('button', { name: /^Open / }).click();
    const viewer = page.locator('dialog#viewer-dialog[open]');
    await expect(viewer).toBeVisible();
    await viewer.getByRole('button', { name: 'Delete…', exact: true }).click();
    const dialog = review(page);
    await expect(dialog).toBeVisible();
    await dialog.getByRole('radio', { name: /^Remove from the library only/ }).check();
    await dialog.locator('.dialog-actions button.danger').click();
    await expect(viewer).toBeHidden();
    await expect(page.locator(CARDS)).toHaveCount(before - 1);
});

// The review never starts a delete without an explicit choice (live test 2026-10-29): every activation of the
// aria-disabled primary is ignored, the dialog has no form to submit, and a chosen primary starts once.
test('without a chosen mode nothing can start a delete', async ({ page }) => {
    const before = await openImages(page);
    await check(page, 0);
    const applies = [];
    page.on('request', request => { if (request.url().includes('/maintenance/apply')) applies.push(request.postDataJSON()); });
    await bar(page).getByRole('button', { name: 'Delete…', exact: true }).click();
    const dialog = review(page);
    await expect(dialog).toBeVisible();
    const primary = dialog.locator('.dialog-actions button.danger');
    await expect(primary).toHaveAttribute('aria-disabled', 'true');
    await expect(primary).toHaveText('Choose what happens to the files');
    // Visibly dimmed while no mode is chosen.
    await expect(primary).toHaveCSS('opacity', '0.55');
    await expect(primary).toHaveCSS('cursor', 'not-allowed');
    await primary.click({ force: true });
    await primary.dblclick({ force: true });
    await primary.focus();
    await page.keyboard.press('Enter');
    await page.keyboard.press('Space');
    await dialog.locator('#modal-title').click();
    await page.keyboard.press('Enter');
    expect(await dialog.locator('form').count()).toBe(0);
    await page.evaluate(() => document.querySelector('dialog#modal[open]')?.dispatchEvent(new Event('submit', { bubbles: true })));
    await expect(dialog).toBeVisible();
    expect(applies).toEqual([]);
    for (const radio of await dialog.getByRole('radio').all()) await expect(radio).not.toBeChecked();
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(page.locator(CARDS)).toHaveCount(before);
    expect(applies).toEqual([]);
});

test('a chosen mode starts exactly one delete, with that mode, however often it is pressed', async ({ page }) => {
    const before = await openImages(page);
    await check(page, 0);
    const applies = [];
    page.on('request', request => { if (request.url().includes('/maintenance/apply')) applies.push(request.postDataJSON()); });
    const { primary } = await deleteChecked(page, 'library');
    await startAndAwaitJob(page, () => primary.dblclick());
    await expect(toast(page)).toContainText('Removed 1 image from the library.');
    await expect(page.locator(CARDS)).toHaveCount(before - 1);
    expect(applies.length).toBe(1);
    expect(applies[0].mode).toBe('library');
    expect(Object.keys(applies[0]).sort()).toEqual(['mode', 'token']);
});
