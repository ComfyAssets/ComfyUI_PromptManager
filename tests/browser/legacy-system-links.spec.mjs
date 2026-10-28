// System S13 (#330): the legacy admin and gallery pages open native System for their System tools. Each replaced
// control is a plain link (`workspace#/system/<id>`, a new tab), and the legacy flows S13 keeps still work.
import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';

const PAGES = Object.freeze({ admin: '/prompt_manager/admin', gallery: '/prompt_manager/gallery' });
const LABELS = Object.freeze({
    overview: 'Overview',
    appearance: 'Appearance',
    sources: 'Sources & scans',
    integrations: 'Integrations',
    tagging: 'Auto-tagging',
    maintenance: 'Maintenance',
    backups: 'Backup & export',
    diagnostics: 'Diagnostics & logs',
});

// One row per System link of the design's §2 map: the page, the control's label, how to find it, the section it opens
// and what a test must arrange before the control shows.
const LINKS = Object.freeze([
]);

async function post(request, url, data) {
    await expect(await request.post(url, data === undefined ? {} : { data })).toBeOK();
}

test.beforeEach(async ({ page, request, browserName }) => {
    await startCoverage(page, browserName);
    await post(request, '/__test__/reset');
    await post(request, '/__test__/system/reset');
    await post(request, '/__test__/tagging/reset');
    // The 3.2.0 update notice would cover the admin page's toolbar.
    await page.addInitScript(() => { localStorage.setItem('pm_update_notice_dismissed', '3.2.0'); });
    page.legacyErrors = [];
    page.on('pageerror', error => page.legacyErrors.push(error.message));
});

test.afterEach(async ({ page, request, browserName }) => {
    await saveCoverage(page, browserName);
    await post(request, '/__test__/tagging/reset');
    await post(request, '/__test__/system/reset');
    expect(page.legacyErrors).toEqual([]);
});

/** Opens the legacy page with the link showing; returns the link. */
async function openLink(page, request, link) {
    if (link.arrange) await link.arrange(page, request);
    await page.goto(PAGES[link.page]);
    if (link.reveal) await link.reveal(page);
    const control = link.locate(page);
    await expect(control).toBeVisible();
    return control;
}

for (const link of LINKS) {
    test(`legacy ${link.page} ${link.label} opens System ${link.section}`, async ({ page, context, request }) => {
        const control = await openLink(page, request, link);
        const legacyUrl = page.url();
        await expect(control).toHaveAttribute('href', `workspace#/system/${link.section}`);
        await expect(control).toHaveAttribute('target', '_blank');
        await expect(control).toHaveAttribute('rel', 'noopener');
        const opened = context.waitForEvent('page');
        await control.click();
        const system = await opened;
        await expect(system).toHaveURL(new RegExp(`/prompt_manager/workspace#/system/${link.section}$`));
        await expect(system.locator('#workspace-main').getByRole('heading', { level: 1, name: LABELS[link.section], exact: true }))
            .toBeVisible();
        expect(page.url()).toBe(legacyUrl);
        await system.close();
    });
}

test('a System link sends no request from the legacy page', async ({ page, context, request }) => {
    for (const link of LINKS) {
        const control = await openLink(page, request, link);
        const sent = [];
        const record = item => sent.push(`${item.method()} ${item.url()}`);
        page.on('request', record);
        const opened = context.waitForEvent('page');
        await control.click();
        const system = await opened;
        await system.waitForLoadState('domcontentloaded');
        page.off('request', record);
        expect(sent, `${link.page} ${link.label}`).toEqual([]);
        await system.close();
    }
});

test('kept admin flows still work', async ({ page, context }) => {
    await page.goto(PAGES.admin);
    const texts = page.locator('#resultsList .prompt-text');
    await expect(texts.first()).toBeVisible();

    // K1: sorting reorders the list, a search narrows it, and a selection enables the bulk buttons.
    for (const [value, order] of [['text_asc', 1], ['text_desc', -1]]) {
        const listed = page.waitForResponse(item => /\/prompt_manager\/(recent|search)\?/.test(item.url()) && item.url().includes(`sort=${value}`));
        await page.locator('#sortBy').selectOption(value);
        await listed;
        await expect.poll(async () => {
            const shown = await texts.allTextContents();
            const sorted = [...shown].sort((a, b) => order * a.localeCompare(b));
            return shown.length > 1 && shown.join('\n') === sorted.join('\n');
        }).toBe(true);
    }
    const all = await texts.allTextContents();
    const word = all[0].trim().split(/\s+/)[0];
    const searched = page.waitForResponse(item => item.url().includes('/prompt_manager/search?') && item.url().includes('text='));
    await page.locator('#searchText').fill(word);
    await page.locator('#searchBtn').click();
    await searched;
    await expect.poll(async () => {
        const shown = await texts.allTextContents();
        return shown.length > 0 && shown.length < all.length && shown.every(text => text.toLowerCase().includes(word.toLowerCase()));
    }).toBe(true);
    await expect(page.locator('#bulkDeleteBtn')).toBeDisabled();
    await page.locator('#resultsList .prompt-checkbox').first().check();
    for (const id of ['#bulkDeleteBtn', '#bulkTagBtn', '#bulkCategoryBtn']) await expect(page.locator(id)).toBeEnabled();

    // K3: Add Prompt saves a prompt the list then shows.
    await page.locator('#addPromptBtn').click();
    await page.locator('#addPromptText').fill('Kept flow lighthouse');
    const saved = page.waitForResponse(item => item.request().method() === 'POST' && item.url().endsWith('/prompt_manager/save'));
    await page.locator('#saveNewPromptBtn').click();
    expect((await saved).ok()).toBe(true);
    await expect(page.locator('#addPromptModal')).toBeHidden();

    // K4: Stats reads the statistics again; Export downloads the prompts.
    const stats = page.waitForResponse(item => item.url().endsWith('/prompt_manager/stats') && item.ok());
    await page.locator('#statsBtn').click();
    await stats;
    await expect(page.getByText('Stats refreshed successfully!')).toBeVisible();
    const download = page.waitForEvent('download');
    await page.locator('#exportBtn').click();
    expect((await download).suggestedFilename()).toMatch(/^prompts-\d{4}-\d{2}-\d{2}\.json$/);

    // K5: Scan Images opens its dialog (with the quick backup) without starting a scan.
    await page.locator('#scanBtn').click();
    await expect(page.locator('#scanModal')).toBeVisible();
    await expect(page.locator('#quickBackupBtn')).toBeVisible();
    await page.locator('#cancelScan').click();
    await expect(page.locator('#scanModal')).toBeHidden();

    // K7: Metadata and Gallery open their pages in a new tab.
    for (const [id, path] of [['#metadataBtn', '/prompt_manager/gallery.html'], ['#galleryBtn', '/prompt_manager/gallery']]) {
        const opened = context.waitForEvent('page');
        await page.locator(id).click();
        const tab = await opened;
        await tab.waitForLoadState('domcontentloaded');
        expect(new URL(tab.url()).pathname).toBe(path);
        await tab.close();
    }
});
