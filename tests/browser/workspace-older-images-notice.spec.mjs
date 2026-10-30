// The one-time older-images notice in the workspace shell (#527, D166). Its dismissal is the legacy admin page's key,
// so dismissing it in any place dismisses it everywhere.
import { test, expect } from './fixtures.mjs';

const WORKSPACE = '/prompt_manager/workspace';
const ADMIN = '/prompt_manager/admin';
const NOTICE_KEY = 'pm_update_notice_dismissed';

const shellNotice = page => page.locator('#older-images-notice > .notice');
const sourcesNotice = page => page.locator('#workspace-main .settings-layout > .notice');
const dialog = page => page.locator('dialog#modal[open]');
const adminModal = page => page.locator('#updateNoticeModal');
const stored = page => page.evaluate(key => localStorage.getItem(key), NOTICE_KEY);

async function openRoute(page, route) {
    await page.goto(`${WORKSPACE}#/${route}`);
    await expect(page.locator('#workspace-main h1').first()).toBeVisible();
}

// Every spec on this worker's server shares its job list.
test.beforeEach(async ({ request }) => {
    await expect(await request.post('/__test__/jobs/restart')).toBeOK();
});

test.describe('with the notice not dismissed', () => {
    test.use({ olderImagesNotice: true });

    for (const route of ['library', 'images', 'tags', 'activity', 'system/overview']) {
        test(`shows above the ${route} page with Later and Review scan`, async ({ page }) => {
            await openRoute(page, route);
            await expect(shellNotice(page).locator('h2')).toHaveText('Bring older images into your prompt history');
            await expect(shellNotice(page).locator('.row-actions > button')).toHaveText(['Later', 'Review scan']);
            await expect(page.locator('#older-images-notice')).toBeVisible();
        });
    }

    test('Later dismisses it for good without a write or a scan', async ({ page, request }) => {
        const writes = [];
        page.on('request', item => { if (!['GET', 'HEAD', 'OPTIONS'].includes(item.method())) writes.push(item.url()); });
        await openRoute(page, 'library');
        await shellNotice(page).getByRole('button', { name: 'Later', exact: true }).click();
        await expect(shellNotice(page)).toHaveCount(0);
        await expect(page.locator('#older-images-notice')).toBeHidden();
        await expect(page.locator('#workspace-main')).toBeFocused();
        expect(await stored(page)).toBe('3.2.0');
        await expect(dialog(page)).toHaveCount(0);
        await page.reload();
        await expect(page.locator('#workspace-main h1').first()).toBeVisible();
        await expect(shellNotice(page)).toHaveCount(0);
        expect(writes).toEqual([]);
        expect((await (await request.get('/prompt_manager/jobs')).json()).jobs).toEqual([]);
    });

    test('Review scan dismisses it and opens the review of every source', async ({ page }) => {
        await openRoute(page, 'library');
        await shellNotice(page).getByRole('button', { name: 'Review scan', exact: true }).click();
        await expect(dialog(page).locator('#modal-title')).toHaveText('Review metadata scan');
        await expect(dialog(page).locator('dl.meta-grid > div > dd')).toHaveText(['2 output folders', 'New and unresolved files']);
        await expect(shellNotice(page)).toHaveCount(0);
        expect(await stored(page)).toBe('3.2.0');
        await dialog(page).getByRole('button', { name: 'Cancel', exact: true }).click();
        await expect(page.locator('#workspace-main')).toBeFocused();
    });

    test('Review scan keeps the notice and says so when the sources cannot be read', async ({ page }) => {
        await openRoute(page, 'library');
        await page.route('**/prompt_manager/settings', route => route.fulfill({ status: 500, json: { success: false } }));
        await shellNotice(page).getByRole('button', { name: 'Review scan', exact: true }).click();
        await expect(page.locator('#workspace-toast')).toHaveText('Could not read the image sources. Try again.');
        await expect(shellNotice(page)).toHaveCount(1);
        await expect(shellNotice(page).getByRole('button', { name: 'Review scan', exact: true })).not.toHaveAttribute('aria-disabled', 'true');
        await expect(dialog(page)).toHaveCount(0);
        expect(await stored(page)).toBeNull();
    });

    test('System > Sources shows one notice, its own, and dismissing it there clears the shell', async ({ page }) => {
        await openRoute(page, 'system/overview');
        await expect(shellNotice(page)).toHaveCount(1);
        await page.goto(`${WORKSPACE}#/system/sources`);
        await expect(page.locator('#workspace-main .settings-layout > .notice:visible')).toHaveCount(1);
        await expect(shellNotice(page)).toHaveCount(0);
        await sourcesNotice(page).getByRole('button', { name: 'Later', exact: true }).click();
        await expect(sourcesNotice(page)).toHaveCount(0);
        await page.goto(`${WORKSPACE}#/library`);
        await expect(page.locator('#workspace-main h1').first()).toBeVisible();
        await expect(shellNotice(page)).toHaveCount(0);
    });

    test('dismissing in the shell clears the copy System has already mounted', async ({ page }) => {
        await openRoute(page, 'system/sources');
        await expect(sourcesNotice(page)).toHaveCount(1);
        await page.goto(`${WORKSPACE}#/system/overview`);
        await shellNotice(page).getByRole('button', { name: 'Later', exact: true }).click();
        await page.goto(`${WORKSPACE}#/system/sources`);
        await expect(page.locator('#workspace-main #source-list')).toBeVisible();
        await expect(sourcesNotice(page)).toHaveCount(0);
    });

    test('dismissing in another tab clears it here', async ({ page, context }) => {
        await openRoute(page, 'library');
        await expect(shellNotice(page)).toHaveCount(1);
        const other = await context.newPage();
        await openRoute(other, 'images');
        await shellNotice(other).getByRole('button', { name: 'Later', exact: true }).click();
        await expect(shellNotice(page)).toHaveCount(0);
        await other.close();
    });

    test('the admin modal still works: Later without the checkbox keeps the notice everywhere', async ({ page }) => {
        await page.goto(ADMIN);
        await expect(adminModal(page)).toBeVisible();
        await page.locator('#updateNoticeDismiss').click();
        await expect(adminModal(page)).toBeHidden();
        expect(await stored(page)).toBeNull();
        await openRoute(page, 'library');
        await expect(shellNotice(page)).toHaveCount(1);
    });

    test('dismissing on the admin page dismisses it in the workspace', async ({ page }) => {
        await page.goto(ADMIN);
        await page.locator('#dismissUpdateNotice').check();
        await page.locator('#updateNoticeDismiss').click();
        await expect(adminModal(page)).toBeHidden();
        await openRoute(page, 'library');
        await expect(shellNotice(page)).toHaveCount(0);
    });

    test('dismissing in the workspace dismisses it on the admin page', async ({ page }) => {
        await openRoute(page, 'library');
        await shellNotice(page).getByRole('button', { name: 'Later', exact: true }).click();
        await page.goto(ADMIN);
        await page.waitForLoadState('load');
        await expect(adminModal(page)).toBeHidden();
    });

    test('the admin Scan still dismisses it and opens the scan modal', async ({ page }) => {
        await page.goto(ADMIN);
        await page.locator('#updateNoticeScan').click();
        await expect(adminModal(page)).toBeHidden();
        expect(await stored(page)).toBe('3.2.0');
    });
});

test('specs start with the notice dismissed, on every page', async ({ page, browser, baseURL }) => {
    await openRoute(page, 'library');
    await expect(shellNotice(page)).toHaveCount(0);
    const context = await browser.newContext({ baseURL });
    const other = await context.newPage();
    await other.goto(ADMIN);
    await other.waitForLoadState('load');
    await expect(adminModal(other)).toBeHidden();
    await context.close();
});
