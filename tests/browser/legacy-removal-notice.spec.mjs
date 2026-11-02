// #615: each old page says it will be removed and links to where its job lives now. The page keeps working, and the
// notice takes its own room: nothing is pushed off a page whose layout fills the window.
import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';

const PAGES = Object.freeze([
    { path: '/prompt_manager/admin', section: 'library', heading: 'Prompt library' },
    { path: '/prompt_manager/gallery', section: 'images', heading: 'Images' },
    { path: '/prompt_manager/gallery.html', section: 'inspect', heading: 'Inspect a file' },
]);

test.beforeEach(async ({ page, request, browserName }) => {
    await startCoverage(page, browserName);
    await expect(await request.post('/__test__/reset', {})).toBeOK();
    await page.addInitScript(() => {
        localStorage.setItem('pm_update_notice_dismissed', '3.2.0');
        localStorage.setItem('gallerySettings', JSON.stringify({ checkThumbnailsAtStartup: false }));
    });
});

test.afterEach(async ({ page, browserName }) => {
    await saveCoverage(page, browserName);
});

for (const { path, section, heading } of PAGES) {
    test(`${path} says it will be removed and links to ${section}`, async ({ page }) => {
        await page.goto(path);
        const notice = page.locator('#legacy-removal-notice');
        await expect(notice).toBeVisible();
        await expect(notice).toContainText('This page will be removed in a future release.');
        await notice.getByRole('link', { name: 'Open the workspace' }).click();
        await expect(page).toHaveURL(new RegExp(`/prompt_manager/workspace#/${section}$`));
        await expect(page.locator('#workspace-main h1')).toHaveText(heading);
    });
}

test('the notice leaves the metadata page filling the window', async ({ page }) => {
    await page.goto('/prompt_manager/gallery.html');
    await expect(page.locator('#legacy-removal-notice')).toBeVisible();
    const { bottom, height, scroll } = await page.locator('#image-area').evaluate(area => ({
        bottom: area.getBoundingClientRect().bottom,
        height: window.innerHeight,
        scroll: document.documentElement.scrollHeight,
    }));
    expect(bottom).toBeLessThanOrEqual(height);
    expect(scroll).toBeLessThanOrEqual(height);
});
