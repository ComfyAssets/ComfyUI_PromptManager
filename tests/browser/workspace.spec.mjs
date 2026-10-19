import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from '../../tools/vendor/node_modules/@playwright/test/index.mjs';

const workspace = '/prompt_manager/workspace';
const statistic = (page, label) => page.locator('#workspace-main dt')
    .filter({ hasText: new RegExp(`^${label}$`) }).locator('..').locator('dd');
const routes = [
    ['overview', 'Overview', null],
    ['images', 'Images', '/prompt_manager/gallery'],
    ['library', 'Prompt library', '/prompt_manager/admin'],
    ['tags', 'Tags & categories', null],
    ['inspect', 'Inspect a file', '/prompt_manager/gallery.html'],
    ['system', 'System', '/prompt_manager/admin'],
];

async function countAppearanceWrites(page) {
    await page.evaluate(() => {
        window.__appearanceChanges = 0;
        const original = Storage.prototype.setItem;
        Storage.prototype.setItem = function (...args) {
            if (args[0] === 'pm_workspace_theme') window.__appearanceChanges++;
            return original.apply(this, args);
        };
    });
}

test.beforeEach(async ({ page, request, browserName }) => {
    await startCoverage(page, browserName);
    await expect(await request.post('/__test__/reset')).toBeOK();
    page.workspaceErrors = [];
    page.on('pageerror', error => page.workspaceErrors.push(error.message));
    page.on('response', response => {
        if (response.status() >= 400 && /\/prompt_manager\/(js|lib)\//.test(response.url())) {
            page.workspaceErrors.push(`${response.status()} ${response.url()}`);
        }
    });
});

test.afterEach(async ({ page, browserName }) => {
    await saveCoverage(page, browserName);
    expect(page.workspaceErrors).toEqual([]);
});

test('workspace starts with real persisted statistics without mutating data', async ({ page, request }) => {
    const before = await (await request.get('/prompt_manager/stats')).json();
    const mutations = [];
    page.on('request', request => {
        if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) mutations.push(request.url());
    });
    await page.goto(`${workspace}#/overview`);
    await expect(page).toHaveTitle(/Prompt Manager/);
    await expect(page.getByRole('navigation', { name: 'Workspace', exact: true })).toBeVisible();
    const main = page.locator('#workspace-main');
    await expect(main.getByRole('heading', { level: 1, name: 'Overview', exact: true })).toBeVisible();
    for (const [label, key] of [['Prompts', 'total_prompts'], ['Images', 'total_images'], ['Tags', 'total_tags']]) {
        await expect(statistic(page, label)).toHaveText(String(before.stats[key]));
    }
    expect(before.stats.total_prompts).toBe(2);
    expect((await (await request.get('/prompt_manager/stats')).json()).stats).toEqual(before.stats);
    expect(mutations).toEqual([]);
    await expect(page.locator('iframe')).toHaveCount(0);
});

test('all shell destinations deep-link, reload and expose working existing applications', async ({ page, request }) => {
    for (const [route, label, legacy] of routes) {
        await page.goto(`${workspace}#/${route}`);
        const main = page.locator('#workspace-main');
        await expect(main.getByRole('heading', { level: 1, name: label, exact: true })).toBeVisible();
        await expect(page.getByRole('navigation', { name: 'Workspace', exact: true })
            .getByRole('link', { name: label, exact: true })).toHaveAttribute('aria-current', 'page');
        if (route === 'tags') await expect(main.locator('.handoff')).toHaveCount(0);
        if (legacy) {
            await expect(main.locator(`a[href="${legacy}"]`)).toBeVisible();
            await expect(await request.get(legacy.split('#')[0])).toBeOK();
        }
        await page.reload();
        await expect(main.getByRole('heading', { level: 1, name: label, exact: true })).toBeVisible();
    }
});

test('keyboard navigation, browser history and unknown routes remain usable', async ({ page }) => {
    await page.goto(`${workspace}#/overview`);
    const navigation = page.getByRole('navigation', { name: 'Workspace', exact: true });
    const images = navigation.getByRole('link', { name: 'Images', exact: true });
    await images.focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('#workspace-main h1')).toHaveText('Images');
    await navigation.getByRole('link', { name: 'Prompt library', exact: true }).click();
    await expect(page.locator('#workspace-main h1')).toHaveText('Prompt library');
    await page.goBack();
    await expect(page.locator('#workspace-main h1')).toHaveText('Images');
    await page.goForward();
    await expect(page.locator('#workspace-main h1')).toHaveText('Prompt library');
    await page.goto(`${workspace}#/not-a-page`);
    await expect(page.locator('#workspace-main h1')).toHaveText('Images');
    await expect(navigation.getByRole('link', { name: 'Images', exact: true }))
        .toHaveAttribute('aria-current', 'page');
});

test('skip to content focuses the current view without changing its route', async ({ page }) => {
    await page.goto(`${workspace}#/library`);
    await expect(page.locator('#workspace-main h1')).toHaveText('Prompt library');
    await page.keyboard.press('Tab');
    await expect(page.getByRole('link', { name: 'Skip to content', exact: true })).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(`${workspace}#/library`);
    await expect(page.locator('#workspace-main')).toBeFocused();
    await expect(page.locator('#workspace-main h1')).toHaveText('Prompt library');
});

test('returning to overview refreshes persisted counts without duplicating navigation', async ({ page, request }) => {
    await page.goto(`${workspace}#/overview`);
    const prompts = statistic(page, 'Prompts');
    await expect(prompts).toHaveText('2');
    const navigation = page.getByRole('navigation', { name: 'Workspace', exact: true });
    await navigation.getByRole('link', { name: 'Images', exact: true }).click();
    await expect(await request.post('/prompt_manager/save', { data: { text: 'A new persisted workspace prompt' } })).toBeOK();
    await navigation.getByRole('link', { name: 'Overview', exact: true }).click();
    await expect(prompts).toHaveText('3');
    await expect(navigation).toHaveCount(1);
    await expect(navigation.getByRole('link')).toHaveCount(routes.length);
});

test('appearance follows ComfyUI and persists an explicit light or dark preference', async ({ page }) => {
    await page.route('**/api/settings/Comfy.ColorPalette', route => route.fulfill({ json: 'light' }));
    await page.goto(`${workspace}#/overview`);
    const theme = page.locator('[data-ca-theme]').first();
    await expect(theme).toHaveAttribute('data-ca-theme', 'light');
    const appearance = page.getByRole('combobox', { name: 'Appearance', exact: true });
    await appearance.selectOption({ label: 'Dark' });
    await expect(theme).toHaveAttribute('data-ca-theme', 'dark');
    await page.reload();
    await expect(theme).toHaveAttribute('data-ca-theme', 'dark');
    await expect(appearance).toHaveValue('dark');
    await appearance.selectOption({ label: 'Light' });
    await expect(theme).toHaveAttribute('data-ca-theme', 'light');
    await appearance.selectOption({ label: 'Follow ComfyUI' });
    await expect(theme).toHaveAttribute('data-ca-theme', 'light');
});

test('unavailable host palette falls back to system appearance and tracks system changes', async ({ page }) => {
    await page.route('**/api/settings/Comfy.ColorPalette', route => route.fulfill({ status: 503, json: {} }));
    await page.emulateMedia({ colorScheme: 'light' });
    await page.goto(`${workspace}#/overview`);
    const theme = page.locator('[data-ca-theme]').first();
    await expect(theme).toHaveAttribute('data-ca-theme', 'light');
    await page.emulateMedia({ colorScheme: 'dark' });
    await expect(theme).toHaveAttribute('data-ca-theme', 'dark');
});

test('overview reports loading, offers retry after failure and renders real empty statistics', async ({ page }) => {
    let release;
    const pending = new Promise(resolve => { release = resolve; });
    await page.route('**/prompt_manager/stats', async route => {
        await pending;
        await route.fulfill({ status: 503, json: { success: false, error: 'Unavailable' } });
    });
    await page.goto(`${workspace}#/overview`);
    await expect(page.locator('#workspace-main').getByRole('status')).toBeVisible();
    release();
    await expect(page.locator('#workspace-main').getByRole('alert')).toBeVisible();
    await page.unroute('**/prompt_manager/stats');
    await page.route('**/prompt_manager/stats', route => route.fulfill({
        json: { success: true, stats: { total_prompts: 0, total_images: 0, total_tags: 0 } },
    }));
    await page.getByRole('button', { name: 'Retry', exact: true }).click();
    await expect(page.locator('#workspace-main').getByRole('alert')).toHaveCount(0);
    for (const label of ['Prompts', 'Images', 'Tags']) {
        await expect(statistic(page, label)).toHaveText('0');
    }
});

test('small screens keep navigation and appearance accessible without horizontal page overflow', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto(`${workspace}#/overview`);
    const navigation = page.getByRole('navigation', { name: 'Workspace', exact: true });
    for (const [, label] of routes) {
        await navigation.getByRole('link', { name: label, exact: true }).click();
        await expect(page.locator('#workspace-main h1')).toHaveText(label);
    }
    await expect(page.getByRole('combobox', { name: 'Appearance', exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('leaving a loading overview prevents its delayed result from replacing the new view', async ({ page }) => {
    let release;
    let completed;
    const pending = new Promise(resolve => { release = resolve; });
    const finished = new Promise(resolve => { completed = resolve; });
    await page.route('**/prompt_manager/stats', async route => {
        await pending;
        await route.fulfill({ json: {
            success: true, stats: { total_prompts: 999, total_images: 888, total_tags: 777 },
        } }).catch(() => {});
        completed();
    });
    await page.goto(`${workspace}#/overview`);
    await expect(page.locator('#workspace-main').getByRole('status')).toBeVisible();
    await page.getByRole('navigation', { name: 'Workspace', exact: true })
        .getByRole('link', { name: 'Images', exact: true }).click();
    await expect(page.locator('#workspace-main h1')).toHaveText('Images');
    release();
    await finished;
    await expect(page.locator('#workspace-main h1')).toHaveText('Images');
    await expect(page.locator('#workspace-main')).not.toContainText('999');
    await expect(page.locator('.workspace-nav-primary')).toHaveCount(1);
    await expect(page.locator('.workspace-nav-tools')).toHaveCount(1);
    await expect(page.getByRole('combobox', { name: 'Appearance', exact: true })).toHaveCount(1);
    await expect(page.locator('#workspace-main a[href="/prompt_manager/gallery"]')).toBeVisible();
    await countAppearanceWrites(page);
    await page.getByRole('combobox', { name: 'Appearance', exact: true }).selectOption('light');
    expect(await page.evaluate(() => window.__appearanceChanges)).toBe(1);
});

test('cached-page restoration remounts one working shell and refreshes its data', async ({ page }) => {
    let reads = 0;
    page.on('request', request => {
        if (new URL(request.url()).pathname === '/prompt_manager/stats') reads++;
    });
    await page.goto(`${workspace}#/overview`);
    await expect(statistic(page, 'Prompts')).toHaveText('2');
    for (let restore = 0; restore < 2; restore++) {
        await page.evaluate(() => {
            window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true }));
            window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }));
        });
        await expect(statistic(page, 'Prompts')).toHaveText('2');
    }
    expect(reads).toBe(3);
    const navigation = page.getByRole('navigation', { name: 'Workspace', exact: true });
    await expect(navigation.getByRole('link')).toHaveCount(routes.length);
    await expect(page.locator('.workspace-nav-primary')).toHaveCount(1);
    await expect(page.locator('.workspace-nav-tools')).toHaveCount(1);
    await expect(page.getByRole('combobox', { name: 'Appearance', exact: true })).toHaveCount(1);
    await countAppearanceWrites(page);
    await navigation.getByRole('link', { name: 'Images', exact: true }).click();
    await expect(page.locator('#workspace-main h1')).toHaveText('Images');
    await page.getByRole('combobox', { name: 'Appearance' }).selectOption('light');
    await expect(page.locator('body')).toHaveAttribute('data-ca-theme', 'light');
    expect(await page.evaluate(() => window.__appearanceChanges)).toBe(1);
});
