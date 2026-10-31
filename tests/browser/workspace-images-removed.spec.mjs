import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';
import { seedConceptAt, openProduction } from './concept-inventory.mjs';
import { RETURN_URL, startAndAwaitJob } from './job-end.mjs';

// #498 (D162): Images › Show removed images (N) lists the files "Remove from the library only" hid; Return to library
// (bar or inspector) clears their marks and scans them again. Nothing is deleted or moved.
test.use({ locale: 'en-US', timezoneId: 'UTC' });

const CARDS = '#workspace-main article[data-image-key]';
const bar = page => page.locator('#workspace-main #bulkbar');
const toast = page => page.locator('#workspace-toast');
const toggle = page => page.locator('.heading-actions').getByRole('button', { name: /^Show removed images/ });
const imagesBadge = page => page.locator('#primary-nav a[data-route="images"] .nav-count');

test.beforeEach(async ({ page, request, browserName }) => {
    await startCoverage(page, browserName);
    await seedConceptAt(page, request);
    page.removedErrors = [];
    page.on('pageerror', error => page.removedErrors.push(error.message));
    page.on('dialog', dialog => { page.removedErrors.push(`browser ${dialog.type()}`); dialog.dismiss().catch(() => {}); });
});

test.afterEach(async ({ page, browserName }) => {
    await saveCoverage(page, browserName);
    expect(page.removedErrors).toEqual([]);
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

/** Remove the first `count` cards from the library (library mode), then wait for the cards to leave. */
async function removeFromLibrary(page, count) {
    const keys = [];
    for (let index = 0; index < count; index += 1) keys.push(await check(page, index));
    await bar(page).getByRole('button', { name: 'Delete…', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: /^Delete \d+ images?$/ });
    await dialog.getByRole('radio', { name: /^Remove from the library only/ }).check();
    await startAndAwaitJob(page, () => dialog.locator('.dialog-actions button.danger').click());
    await expect(toast(page)).toContainText(`Removed ${count} image${count === 1 ? '' : 's'} from the library.`);
    for (const key of keys) await expect(page.locator(`${CARDS}[data-image-key="${key}"]`)).toHaveCount(0);
    return keys;
}

test('without removed images there is no toggle', async ({ page }) => {
    await openImages(page);
    await expect(page.locator('#result-count')).toContainText(/images?/);
    await expect(toggle(page)).toHaveCount(0);
});

test('Show removed images lists the removed files; Return to library brings them back', async ({ page }) => {
    const before = await openImages(page);
    const keys = await removeFromLibrary(page, 2);
    await expect(toggle(page)).toHaveText('Show removed images (2)');
    await expect(toggle(page)).toHaveAttribute('aria-pressed', 'false');
    await toggle(page).click();
    await expect(toggle(page)).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator(CARDS)).toHaveCount(2);
    await expect(page.locator('#result-count')).toHaveText('2 removed images · files on disk, hidden from Images');
    for (const key of keys) {
        const card = page.locator(`${CARDS}[data-image-key="${key}"]`);
        await expect(card.locator('.chip', { hasText: 'Removed' })).toBeVisible();
        // A removed file has no records: no star and no Tag action, which would write and show it again.
        await expect(card.locator('.star-button')).toHaveCount(0);
        await expect(card.locator('.image-card-actions button')).toHaveCount(1);
    }
    await check(page, 0);
    await check(page, 1);
    const actions = bar(page).locator('button');
    await expect(actions).toHaveText(['Return to library', 'Clear']);
    await startAndAwaitJob(page, () => bar(page).getByRole('button', { name: 'Return to library' }).click(), RETURN_URL);
    await expect(toast(page)).toContainText('Returned 2 images to the library');
    await expect(toast(page)).toContainText('Tags, categories, collections and favorites do not come back.');
    // The view emptied: it turns off and Images lists every file again.
    await expect(toggle(page)).toHaveCount(0);
    await expect(page.locator(CARDS)).toHaveCount(before);
    for (const key of keys) await expect(page.locator(`${CARDS}[data-image-key="${key}"]`)).toHaveCount(1);
    await expect(bar(page)).toBeEmpty();
});

test('Return to library from the inspector', async ({ page }) => {
    await openImages(page);
    const [key] = await removeFromLibrary(page, 1);
    await toggle(page).click();
    const card = page.locator(`${CARDS}[data-image-key="${key}"]`);
    await card.getByRole('button', { name: /^Inspect / }).click();
    const panel = page.locator('#image-inspector');
    await expect(panel.getByRole('button', { name: 'Return to library' })).toBeVisible();
    await expect(panel.getByRole('button', { name: 'Delete…' })).toHaveCount(0);
    await expect(panel.locator('button[data-collections]')).toHaveCount(0);
    await startAndAwaitJob(page, () => panel.getByRole('button', { name: 'Return to library' }).click(), RETURN_URL);
    await expect(toast(page)).toContainText('Returned 1 image to the library');
    await expect(toggle(page)).toHaveCount(0);
    await expect(page.locator(`${CARDS}[data-image-key="${key}"]`)).toHaveCount(1);
});

test('the sidebar Images count follows a remove and a return', async ({ page }) => {
    await openImages(page);
    await expect(imagesBadge(page)).toHaveText(/^\d[\d,]*$/);
    const start = Number((await imagesBadge(page).textContent()).replace(/,/g, ''));
    await removeFromLibrary(page, 1);
    await expect(imagesBadge(page)).toHaveText((start - 1).toLocaleString('en-US'));
    await toggle(page).click();
    await check(page, 0);
    await startAndAwaitJob(page, () => bar(page).getByRole('button', { name: 'Return to library' }).click(), RETURN_URL);
    await expect(toast(page)).toContainText('Returned 1 image to the library');
    await expect(imagesBadge(page)).toHaveText(start.toLocaleString('en-US'));
});

test('the removed view is never saved and ends when the page is left', async ({ page }) => {
    await openImages(page);
    await removeFromLibrary(page, 1);
    await toggle(page).click();
    await expect(toggle(page)).toHaveAttribute('aria-pressed', 'true');
    await page.locator('#primary-nav a[data-route="library"]').click();
    await expect(page).toHaveURL(/#\/library/);
    await page.locator('#primary-nav a[data-route="images"]').click();
    await expect(page.locator(CARDS).first()).toBeVisible();
    await expect(toggle(page)).toHaveAttribute('aria-pressed', 'false');
    await expect(page.locator('#result-count')).not.toContainText('removed');
});

test('choosing a category in the removed view ends the view and lists the category', async ({ page }) => {
    await openImages(page);
    await removeFromLibrary(page, 1);
    await toggle(page).click();
    await expect(toggle(page)).toHaveAttribute('aria-pressed', 'true');
    await page.getByRole('combobox', { name: 'Filter by category', exact: true }).selectOption('Landscapes');
    // A removed file has no category: the view ends instead of refusing the listing.
    await expect(toggle(page)).toHaveAttribute('aria-pressed', 'false');
    await expect(page.locator(CARDS).first()).toBeVisible();
    await expect(page.locator('#result-count')).not.toContainText('removed');
});
