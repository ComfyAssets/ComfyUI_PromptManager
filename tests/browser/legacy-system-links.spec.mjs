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

async function openPageOptions(page) {
    await page.locator('#pageOptionsBtn').click();
    await expect(page.locator('#settingsModal')).toBeVisible();
}

/** The auto-tag modal with its model rows read from /autotag/models. */
async function openAutoTag(page) {
    const read = page.waitForResponse(item => item.url().endsWith('/prompt_manager/autotag/models'));
    await page.locator('#autoTagBtn').click();
    await read;
    await expect(page.locator('#autoTagModal')).toBeVisible();
}

const notDownloaded = (page, request) => post(request, '/__test__/tagging/models', { unavailable: ['gguf'] });
const modelLoaded = (page, request) => post(request, '/__test__/tagging/loaded', { model: 'wd14-vit' });

// One row per System link of the design's §2 map: the page, the control's label, how to find it, the section it opens
// and what a test must arrange before the control shows.
const LINKS = Object.freeze([
    { page: 'admin', label: 'Settings', locate: page => page.locator('#settingsBtn'), section: 'overview' },
    { page: 'admin', label: 'Maintenance', locate: page => page.locator('#maintenanceBtn'), section: 'maintenance' },
    { page: 'admin', label: 'Backup DB', locate: page => page.locator('#backupBtn'), section: 'backups' },
    { page: 'admin', label: 'Restore DB', locate: page => page.locator('#restoreBtn'), section: 'backups' },
    { page: 'gallery', label: 'Settings', locate: page => page.locator('#settingsBtn'), section: 'appearance' },
    { page: 'admin', label: 'Manage in System (model row)', arrange: notDownloaded, reveal: openAutoTag,
        locate: page => page.locator('#ggufModelStatus a'), section: 'tagging' },
    { page: 'gallery', label: 'Manage in System (model row)', arrange: notDownloaded, reveal: openAutoTag,
        locate: page => page.locator('#ggufModelStatus a'), section: 'tagging' },
    { page: 'gallery', label: 'Manage in System (Unload Model)', arrange: modelLoaded, reveal: openAutoTag,
        locate: page => page.locator('#unloadModelBtn'), section: 'tagging' },
    { page: 'gallery', label: 'Page options Maintenance', reveal: openPageOptions,
        locate: page => page.locator('#pageOptionsMaintenanceLink'), section: 'maintenance' },
    { page: 'gallery', label: 'Page options Sources & scans', reveal: openPageOptions,
        locate: page => page.locator('#pageOptionsSourcesLink'), section: 'sources' },
    { page: 'admin', label: 'Diagnostics', locate: page => page.locator('#diagnosticsBtn'), section: 'diagnostics' },
    { page: 'admin', label: 'View Logs', locate: page => page.locator('#logsBtn'), section: 'diagnostics' },
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

// Each row opens its section in a new tab and sends no request from the legacy page (the design's `a System link sends
// no request from the legacy page`, checked per row so one slow page never times out the whole table).
for (const link of LINKS) {
    test(`legacy ${link.page} ${link.label} opens System ${link.section}`, async ({ page, context, request }) => {
        // The gallery's thumbnail startup check (K9) reads its status on a timer of its own; it is off here.
        await page.addInitScript(() => {
            localStorage.setItem('gallerySettings', JSON.stringify({ checkThumbnailsAtStartup: false }));
        });
        const control = await openLink(page, request, link);
        const legacyUrl = page.url();
        await expect(control).toHaveAttribute('href', `workspace#/system/${link.section}`);
        await expect(control).toHaveAttribute('target', '_blank');
        await expect(control).toHaveAttribute('rel', 'noopener');
        // The page's own start-up reads (list, film strips, settings) finish first; then the click must add none.
        // Lazy thumbnails may still load whenever the layout changes; they are not the click's.
        await page.waitForLoadState('networkidle');
        const sent = [];
        const record = item => { if (item.resourceType() !== 'image') sent.push(`${item.method()} ${item.url()}`); };
        page.on('request', record);
        const opened = context.waitForEvent('page');
        await control.click();
        const system = await opened;
        await expect(system).toHaveURL(new RegExp(`/prompt_manager/workspace#/system/${link.section}$`));
        page.off('request', record);
        expect(sent, 'a System link sends no request from the legacy page').toEqual([]);
        // System's page heading is "System"; the open section is the pressed button of its section switcher.
        const main = system.locator('#workspace-main');
        await expect(main.getByRole('heading', { level: 1, name: 'System', exact: true })).toBeVisible();
        await expect(main.getByRole('group', { name: 'System sections', exact: true })
            .getByRole('button', { name: LABELS[link.section], exact: true })).toHaveAttribute('aria-pressed', 'true');
        expect(page.url()).toBe(legacyUrl);
        await system.close();
    });
}

test('kept admin flows still work', async ({ page, context }) => {
    await page.goto(PAGES.admin);
    const texts = page.locator('#resultsList .prompt-text');
    await expect(texts.first()).toBeVisible();

    // K1: sorting reorders the list.
    for (const [value, order] of [['text_asc', 1], ['text_desc', -1]]) {
        const listed = page.waitForResponse(item => /\/prompt_manager\/(recent|search)\?/.test(item.url()) && item.url().includes(`sort=${value}`));
        await page.locator('#sortBy').selectOption(value);
        await listed;
        await expect.poll(async () => {
            const shown = await texts.allTextContents();
            // The server sorts case-insensitively (SQLite NOCASE), not by locale.
            const key = text => text.toLowerCase();
            const sorted = [...shown].sort((a, b) => order * (key(a) < key(b) ? -1 : key(a) > key(b) ? 1 : 0));
            return shown.length > 1 && shown.join('\n') === sorted.join('\n');
        }).toBe(true);
    }
    // K3: Add Prompt saves a prompt the list then shows.
    await page.locator('#addPromptBtn').click();
    await page.locator('#addPromptText').fill('Kept flow lighthouse');
    const saved = page.waitForResponse(item => item.request().method() === 'POST' && item.url().endsWith('/prompt_manager/save'));
    await page.locator('#saveNewPromptBtn').click();
    expect((await saved).ok()).toBe(true);
    await expect(page.locator('#addPromptModal')).toBeHidden();
    await expect(texts.filter({ hasText: 'Kept flow lighthouse' })).toBeVisible();

    // K1: a search narrows the list, and a selection enables the bulk buttons.
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

test('Page options keeps the page-only settings admin', async ({ page, request }) => {
    const before = (await (await request.get('/prompt_manager/settings')).json()).settings;
    await page.goto(PAGES.admin);
    await expect(page.locator('#resultsList .prompt-text').first()).toBeVisible();
    await page.locator('#pageOptionsBtn').click();
    const dialog = page.locator('#settingsModal');
    await expect(dialog).toBeVisible();
    await expect(dialog.locator('h3')).toHaveText('Page options');
    // Exactly the page-only setting (G5): the list's Infinite Scrolling default.
    expect(await dialog.locator('input, select, textarea').evaluateAll(fields => fields.map(field => field.id)))
        .toEqual(['infiniteScrollDefault']);
    await expect(page.locator('#infiniteScrollDefault')).not.toBeChecked();
    await page.locator('#infiniteScrollDefault').check();
    const saved = page.waitForRequest(item => item.method() === 'POST' && item.url().endsWith('/prompt_manager/settings'));
    await page.locator('#saveSettings').click();
    expect((await saved).postDataJSON()).toEqual({ infinite_scroll: true });
    await expect(dialog).toBeHidden();
    // The list header follows the saved default at once; nothing else changed.
    await expect(page.locator('#infiniteScrollSession')).toBeChecked();
    const after = (await (await request.get('/prompt_manager/settings')).json()).settings;
    expect(after).toEqual({ ...before, infinite_scroll: true });
    await page.reload();
    // The dialog shows the saved default once the page has read it (the header follows it first).
    await expect(page.locator('#infiniteScrollSession')).toBeChecked();
    await page.locator('#pageOptionsBtn').click();
    await expect(page.locator('#infiniteScrollDefault')).toBeChecked();
});

test('Page options keeps the page-only settings gallery', async ({ page }) => {
    // Native Appearance keys and the startup prompt's own keys are in the shared object before the page opens.
    const stored = { imageQuality: 'high', showImageInfo: false, videoMute: false,
        autoLoadMetadata: false, workspaceLayout: 'masonry', thumbnailPromptDismissed: 'sig', thumbnailsGenerated: true,
        checkThumbnailsAtStartup: false, defaultViewMode: 'list' };
    await page.addInitScript(value => {
        if (!sessionStorage.getItem('seeded')) {
            sessionStorage.setItem('seeded', '1');
            localStorage.setItem('gallerySettings', value);
        }
    }, JSON.stringify(stored));
    const listed = page.waitForResponse(item => item.url().includes('/prompt_manager/images/output?'));
    await page.goto(PAGES.gallery);
    await listed;
    // With no Page options control, a stored default view still opens the page in it.
    await expect(page.locator('#listViewBtn')).toHaveClass(/bg-pm-accent/);
    await openPageOptions(page);
    const dialog = page.locator('#settingsModal');
    await expect(dialog.locator('h3')).toHaveText('Page options');
    // Exactly the page-only settings (G5); the default view is System › Appearance's Default layout.
    expect(await dialog.locator('input, select, textarea').evaluateAll(fields => fields.map(field => field.id))).toEqual([
        'checkThumbnailsAtStartup', 'lazyLoadingToggle', 'defaultLimitSelect', 'gridColumnsSelect',
        'infiniteScrollToggle', 'debugModeToggle', 'apiTimeoutInput']);
    await expect(dialog.getByText('Default View Mode')).toHaveCount(0);
    await page.locator('#defaultLimitSelect').selectOption('50');
    await page.locator('#debugModeToggle').check();
    await page.locator('#saveSettingsBtn').click();
    await expect(dialog).toBeHidden();
    const read = () => page.evaluate(() => JSON.parse(localStorage.getItem('gallerySettings')));
    let saved = await read();
    expect(saved).toMatchObject({ ...stored, defaultLimit: 50, debugMode: true });

    // Reset puts back only the page options' defaults; the Appearance, startup-prompt and default-view keys stay.
    await openPageOptions(page);
    page.once('dialog', dialog => dialog.accept());
    await page.locator('#resetSettingsBtn').click();
    await expect(page.getByText('Page options reset to defaults')).toBeVisible();
    await expect(page.locator('#defaultLimitSelect')).toHaveValue('100');
    saved = await read();
    expect(saved).toMatchObject({ defaultViewMode: 'list', imageQuality: 'high', showImageInfo: false, videoMute: false,
        autoLoadMetadata: false, workspaceLayout: 'masonry', thumbnailPromptDismissed: 'sig', thumbnailsGenerated: true,
        defaultLimit: 100, debugMode: false, checkThumbnailsAtStartup: true, lazyLoading: true, gridColumns: 8,
        infiniteScroll: false, apiTimeout: 30 });
});

test('the legacy auto-tag modal stays open after Manage in System', async ({ page, context, request }) => {
    for (const legacy of ['admin', 'gallery']) {
        await post(request, '/__test__/tagging/models', { unavailable: ['gguf'] });
        await page.goto(PAGES[legacy]);
        await openAutoTag(page);
        const link = page.locator('#ggufModelStatus').getByRole('link', { name: 'Manage in System', exact: true });
        await expect(link).toHaveAttribute('title', 'Opens System › Auto-tagging in a new tab');
        // A downloaded model keeps its status text.
        await expect(page.locator('#wd14VitModelStatus')).toHaveText('\u2713 Downloaded');
        const opened = context.waitForEvent('page');
        await link.click();
        const system = await opened;
        await expect(system).toHaveURL(/#\/system\/tagging$/);
        await expect(page.locator('#autoTagModal')).toBeVisible();
        await system.close();
        // Downloaded in System meanwhile: reopening the modal reads the models again (CUJ 4).
        await post(request, '/__test__/tagging/models', { unavailable: [] });
        await page.goto(PAGES[legacy]);
        await openAutoTag(page);
        await expect(page.locator('#ggufModelStatus')).toHaveText('\u2713 Downloaded');
    }
});
