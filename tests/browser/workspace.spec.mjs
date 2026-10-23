import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';

const workspace = '/prompt_manager/workspace';
const statistic = (page, label) => page.locator('#workspace-main dt')
    .filter({ hasText: new RegExp(`^${label}$`) }).locator('..').locator('dd');
// Concept sidebar: WORKSPACE links carry a real `.nav-count` once loaded, so their accessible name may end in it.
const escapeName = label => label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const navName = label => new RegExp(`^${escapeName(label)}(?:\\s*[\\d,]+)?$`);
const sidebarLink = (page, label) => page.locator('aside.sidebar nav').getByRole('link', { name: navName(label) });
const sidebarLinks = page => page.locator('aside.sidebar nav a');
const routes = [
    ['overview', 'Overview', null],
    // D29: Images no longer links the legacy gallery; Overview's tool cards still do.
    ['images', 'Images', null],
    // #249 E3: the shelf no longer links the legacy Library; Overview's tool card still does.
    ['library', 'Prompt library', null],
    ['tags', 'Tags & categories', null],
    // #249 local Inspect: the native Inspect a file page; Overview's tool card still links the legacy metadata page.
    ['inspect', 'Inspect a file', null],
    // #249 System S2: native System; its undelivered sections hand off per section (D94, asserted below).
    ['system', 'System', null],
    // #249 D51: SAVED VIEWS' Favorites, Images filtered to favorites.
    ['favorites', 'Favorites', null],
    // #249 Activity-1: the concept's Activity page in the tools-and-system group.
    ['activity', 'Activity', null],
];

async function countThemeWrites(page) {
    await page.evaluate(() => {
        window.__themeChanges = 0;
        const original = Storage.prototype.setItem;
        Storage.prototype.setItem = function (...args) {
            if (args[0] === 'pm_workspace_theme') window.__themeChanges++;
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
    // A route handler still running at teardown must not fail the test it outlived.
    await page.unrouteAll({ behavior: 'ignoreErrors' });
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
        await expect(sidebarLink(page, label)).toHaveAttribute('aria-current', 'page');
        if (route === 'tags' || route === 'inspect') await expect(main.locator('.handoff')).toHaveCount(0);
        if (route === 'inspect') await expect(main.locator('#local-file')).toBeAttached();
        if (legacy) {
            await expect(main.locator(`a[href="${legacy}"]`)).toBeVisible();
            await expect(await request.get(legacy.split('#')[0])).toBeOK();
        }
        await page.reload();
        await expect(main.getByRole('heading', { level: 1, name: label, exact: true })).toBeVisible();
    }
    // D29 and #249 E3: the legacy gallery and Library are reached from Overview's tool cards, not from their pages.
    await page.goto(`${workspace}#/overview`);
    const library = page.locator('#workspace-main').getByRole('link', { name: 'Open existing prompt library', exact: true });
    await expect(library).toHaveAttribute('href', '/prompt_manager/admin');
    await expect(await request.get('/prompt_manager/admin')).toBeOK();
    // The legacy metadata page stays reachable from Overview beside the native Inspect page.
    const metadata = page.locator('#workspace-main').getByRole('link', { name: 'Open existing metadata inspector', exact: true });
    await expect(metadata).toHaveAttribute('href', '/prompt_manager/gallery.html');
    await expect(await request.get('/prompt_manager/gallery.html')).toBeOK();
    const gallery = page.locator('#workspace-main').getByRole('link', { name: 'Open existing image gallery', exact: true });
    await expect(gallery).toHaveAttribute('href', '/prompt_manager/gallery');
    await expect(await request.get('/prompt_manager/gallery')).toBeOK();
    await gallery.click();
    await expect(page).toHaveURL(/\/prompt_manager\/gallery$/);
});

test('keyboard navigation, browser history and unknown routes remain usable', async ({ page }) => {
    await page.goto(`${workspace}#/overview`);
    const navigation = page.getByRole('navigation', { name: 'Workspace', exact: true });
    const images = navigation.getByRole('link', { name: navName('Images') });
    await images.focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('#workspace-main h1')).toHaveText('Images');
    await navigation.getByRole('link', { name: navName('Prompt library') }).click();
    await expect(page.locator('#workspace-main h1')).toHaveText('Prompt library');
    await page.goBack();
    await expect(page.locator('#workspace-main h1')).toHaveText('Images');
    await page.goForward();
    await expect(page.locator('#workspace-main h1')).toHaveText('Prompt library');
    await page.goto(`${workspace}#/not-a-page`);
    await expect(page.locator('#workspace-main h1')).toHaveText('Images');
    await expect(navigation.getByRole('link', { name: navName('Images') }))
        .toHaveAttribute('aria-current', 'page');
});

test('skip to content focuses the current view without changing its route', async ({ page }) => {
    await page.goto(`${workspace}#/library`);
    await expect(page.locator('#workspace-main h1')).toHaveText('Prompt library');
    await page.keyboard.press('Tab');
    await expect(page.getByRole('link', { name: 'Skip to workspace', exact: true })).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(`${workspace}#/library`);
    await expect(page.locator('#workspace-main')).toBeFocused();
    await expect(page.locator('#workspace-main h1')).toHaveText('Prompt library');
});

// The concept focuses its page title on every hashchange navigation (docs/concepts/workspace/app.js 1168), so a screen
// reader announces "<page>, heading level 1"; the first render leaves focus alone.
test('a sidebar route change focuses the page title, as the concept does', async ({ page }) => {
    await page.goto(`${workspace}#/library`);
    const title = page.locator('#page-title');
    await expect(title).toHaveText('Prompt library');
    await expect(title).not.toBeFocused();
    await sidebarLink(page, 'Images').click();
    await expect(title).toHaveText('Images');
    await expect(title).toBeFocused();
    await expect(page.locator('#workspace-main')).not.toBeFocused();
    await sidebarLink(page, 'Tags & categories').click();
    await expect(title).toHaveText('Tags & categories');
    await expect(title).toBeFocused();
    await page.goBack();
    await expect(title).toHaveText('Images');
    await expect(title).toBeFocused();
});

test('returning to overview refreshes persisted counts without duplicating navigation', async ({ page, request }) => {
    await page.goto(`${workspace}#/overview`);
    const prompts = statistic(page, 'Prompts');
    await expect(prompts).toHaveText('2');
    const navigation = page.getByRole('navigation', { name: 'Workspace', exact: true });
    await sidebarLink(page, 'Images').click();
    await expect(await request.post('/prompt_manager/save', { data: { text: 'A new persisted workspace prompt' } })).toBeOK();
    await sidebarLink(page, 'Overview').click();
    await expect(prompts).toHaveText('3');
    await expect(navigation).toHaveCount(1);
    await expect(sidebarLinks(page)).toHaveCount(routes.length);
});

test('theme System follows ComfyUI and persists an explicit light or dark preference', async ({ page }) => {
    await page.route('**/api/settings/Comfy.ColorPalette', route => route.fulfill({ json: 'light' }));
    await page.goto(`${workspace}#/overview`);
    const theme = page.locator('[data-ca-theme]').first();
    await expect(theme).toHaveAttribute('data-ca-theme', 'light');
    const themeSelect = page.getByRole('combobox', { name: 'Theme', exact: true });
    await expect(themeSelect).toHaveValue('system');
    await themeSelect.selectOption({ label: 'Dark' });
    await expect(theme).toHaveAttribute('data-ca-theme', 'dark');
    await page.reload();
    await expect(theme).toHaveAttribute('data-ca-theme', 'dark');
    await expect(themeSelect).toHaveValue('dark');
    await themeSelect.selectOption({ label: 'Light' });
    await expect(theme).toHaveAttribute('data-ca-theme', 'light');
    await themeSelect.selectOption({ label: 'System' });
    await expect(theme).toHaveAttribute('data-ca-theme', 'light');
});

// Task A4 (D4, D29): the concept topbar (index.html 45-63, style.css 225-277) without the prototype badge; Activity
// (D52, D53) shows its button, and its pill only while a job runs.
test('topbar matches the concept actions', async ({ page }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    await page.goto(`${workspace}#/images`);
    const topbar = page.locator('.workspace > header.topbar');
    await expect(topbar).toHaveCount(1);
    const shape = await topbar.evaluate(node => ({
        children: [...node.children].map(child => `${child.localName}.${child.className}`),
        crumbs: [...node.querySelector('.breadcrumbs').childNodes].filter(item => item.nodeType === 1 || item.textContent.trim())
            .map(item => item.nodeType === 1 ? `${item.localName}${item.id ? `#${item.id}` : ''}:${item.textContent}` : item.textContent.trim()),
        actions: [...node.querySelector('.top-actions').children].map(child => `${child.localName}.${child.className}`),
        label: node.querySelector('.theme-label').firstChild.textContent.trim(),
        labelFor: node.querySelector('.theme-label').htmlFor,
        options: [...node.querySelectorAll('#theme option')].map(option => [option.value, option.textContent]),
        arrow: [...node.querySelectorAll('.canvas-link span')].map(span => [span.textContent, span.getAttribute('aria-hidden')]),
        tokens: {
            height: getComputedStyle(node).height, padding: getComputedStyle(node).padding,
            crumbs: getComputedStyle(node.querySelector('.breadcrumbs')).fontSize,
            current: getComputedStyle(node.querySelector('#workspace-breadcrumb')).color === getComputedStyle(document.body).color,
            label: getComputedStyle(node.querySelector('.theme-label')).fontSize,
            select: getComputedStyle(node.querySelector('#theme')).fontSize,
            link: getComputedStyle(node.querySelector('.canvas-link')).fontSize,
            gap: getComputedStyle(node.querySelector('.top-actions')).columnGap,
        },
    }));
    expect(shape).toEqual({
        children: ['div.breadcrumbs', 'div.top-actions'],
        crumbs: ['ComfyAssets', 'span:/', 'span#workspace-breadcrumb:Images'],
        actions: ['a.activity-pill', 'button.', 'label.theme-label', 'a.canvas-link'],
        label: 'Theme', labelFor: 'theme',
        options: [['dark', 'Dark'], ['light', 'Light'], ['system', 'System']],
        arrow: [['↗', 'true']],
        tokens: { height: '68px', padding: '0px 30px', crumbs: '12px', current: true, label: '11px', select: '11px', link: '12px', gap: '12px' },
    });
    const theme = page.getByRole('combobox', { name: 'Theme', exact: true });
    await expect(theme).toHaveValue('system');
    const link = page.getByRole('link', { name: 'ComfyUI', exact: true });
    await expect(link).toHaveText('ComfyUI ↗');
    // D6: the ComfyUI canvas root under the page's mount (resolveUrl), never a hard-coded origin.
    await expect(link).toHaveAttribute('href', '/');
    expect(await link.evaluate(node => node.href === `${location.origin}/`)).toBe(true);
    await expect(page.getByRole('combobox', { name: 'Appearance' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Activity', exact: true })).toHaveCount(1);
    await expect(page.locator('#activity-pill')).toBeHidden();
    await expect(page.locator('.concept-badge')).toHaveCount(0);
    await page.locator('nav [data-route="library"]').click();
    await expect(page.locator('#workspace-breadcrumb')).toHaveText('Prompt library');
    await expect(topbar.locator('.top-actions > *')).toHaveCount(4);
});

// Concept style.css 1375-1391: at <=700px the breadcrumbs hide, the actions spread across the bar and the Theme
// label text collapses while the select keeps its accessible name.
test('mobile topbar hides breadcrumbs and spreads actions', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`${workspace}#/images`);
    const theme = page.getByRole('combobox', { name: 'Theme', exact: true });
    await expect(theme).toBeVisible();
    await expect(page.locator('.breadcrumbs')).toBeHidden();
    const layout = await page.locator('.topbar').evaluate(node => {
        const bar = node.getBoundingClientRect(), actions = node.querySelector('.top-actions');
        const box = element => element.getBoundingClientRect();
        return {
            padding: getComputedStyle(node).paddingInline,
            actions: { width: box(actions).width, justify: getComputedStyle(actions).justifyContent, gap: getComputedStyle(actions).columnGap },
            expected: bar.width - 28,
            label: getComputedStyle(node.querySelector('.theme-label')).fontSize,
            select: getComputedStyle(node.querySelector('#theme')).fontSize,
            activityLeft: box(node.querySelector('#activity-open')).left - bar.left,
            actionsGap: [box(node.querySelector('#activity-open')).right, box(node.querySelector('.theme-label')).left,
                box(node.querySelector('.theme-label')).right, box(node.querySelector('.canvas-link')).left],
            linkRight: bar.right - box(node.querySelector('.canvas-link')).right,
        };
    });
    expect(layout).toMatchObject({ padding: '14px', actions: { justify: 'space-between', gap: '8px' }, label: '0px', select: '11px' });
    expect(Math.abs(layout.actions.width - layout.expected)).toBeLessThanOrEqual(1);
    // The concept's three items spread with space-between: Activity first, Theme between equal gaps, ComfyUI last.
    expect(Math.abs(layout.activityLeft - 14)).toBeLessThanOrEqual(1);
    const [activityRight, themeLeft, themeRight, linkLeft] = layout.actionsGap;
    expect(Math.abs((themeLeft - activityRight) - (linkLeft - themeRight))).toBeLessThanOrEqual(1);
    expect(Math.abs(layout.linkRight - 14)).toBeLessThanOrEqual(1);
    await expect(page.getByRole('link', { name: 'ComfyUI', exact: true })).toBeVisible();
    await theme.selectOption('light');
    await expect(page.locator('body')).toHaveAttribute('data-ca-theme', 'light');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('unavailable host palette falls back to the system theme and tracks system changes', async ({ page }) => {
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

test('small screens keep navigation and the theme accessible without horizontal page overflow', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto(`${workspace}#/overview`);
    for (const [, label] of routes) {
        await sidebarLink(page, label).click();
        await expect(page.locator('#workspace-main h1')).toHaveText(label);
    }
    await expect(page.getByRole('combobox', { name: 'Theme', exact: true })).toBeVisible();
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
    await sidebarLink(page, 'Images').click();
    await expect(page.locator('#workspace-main h1')).toHaveText('Images');
    release();
    await finished;
    await expect(page.locator('#workspace-main h1')).toHaveText('Images');
    await expect(page.locator('#workspace-main')).not.toContainText('999');
    await expect(page.locator('#primary-nav')).toHaveCount(1);
    await expect(page.locator('#system-nav')).toHaveCount(1);
    await expect(page.getByRole('combobox', { name: 'Theme', exact: true })).toHaveCount(1);
    await expect(page.locator('#workspace-main .section-caption')).toBeVisible();
    await countThemeWrites(page);
    await page.getByRole('combobox', { name: 'Theme', exact: true }).selectOption('light');
    expect(await page.evaluate(() => window.__themeChanges)).toBe(1);
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
    await expect(sidebarLinks(page)).toHaveCount(routes.length);
    await expect(page.locator('#primary-nav')).toHaveCount(1);
    await expect(page.locator('#system-nav')).toHaveCount(1);
    await expect(page.getByRole('combobox', { name: 'Theme', exact: true })).toHaveCount(1);
    await countThemeWrites(page);
    await sidebarLink(page, 'Images').click();
    await expect(page.locator('#workspace-main h1')).toHaveText('Images');
    await page.getByRole('combobox', { name: 'Theme' }).selectOption('light');
    await expect(page.locator('body')).toHaveAttribute('data-ca-theme', 'light');
    expect(await page.evaluate(() => window.__themeChanges)).toBe(1);
});

// Task A2: the Overview and handoff panels use the concept's `.surface` (style.css 709-722) and `.small` from the
// concept base stylesheet, not the retired shell sheet (18px h2, 11px panel radius).
const surfaceStyle = node => {
    const style = getComputedStyle(node);
    const heading = getComputedStyle(node.querySelector(':scope > h2'));
    const intro = node.querySelector(':scope > p');
    return {
        padding: [style.paddingTop, style.paddingRight, style.paddingBottom, style.paddingLeft],
        radius: style.borderTopLeftRadius, border: `${style.borderTopWidth} ${style.borderTopStyle}`,
        background: style.backgroundColor,
        h2: { fontSize: heading.fontSize, fontWeight: heading.fontWeight, marginTop: heading.marginTop, marginBottom: heading.marginBottom },
        intro: intro && { fontSize: getComputedStyle(intro).fontSize, marginTop: getComputedStyle(intro).marginTop },
    };
};
const SURFACE = {
    padding: ['22px', '22px', '22px', '22px'], radius: '7px', border: '1px solid',
    h2: { fontSize: '17px', fontWeight: '600', marginTop: '0px', marginBottom: '0px' },
    intro: { fontSize: '12px', marginTop: '7px' },
};

for (const width of [1600, 390]) test(`overview and handoff render with concept surfaces ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`${workspace}#/overview`);
    const main = page.locator('#workspace-main');
    await expect(statistic(page, 'Prompts')).toHaveText('2');
    const tools = main.locator('section.surface').filter({ has: page.getByRole('heading', { level: 2, name: 'Continue creating', exact: true }) });
    await expect(tools).toHaveCount(1);
    const surface = await tools.evaluate(surfaceStyle);
    expect(surface).toMatchObject(SURFACE);
    expect(surface.background).not.toBe('rgba(0, 0, 0, 0)');
    await expect(main.locator('.panel')).toHaveCount(0);
    const card = tools.locator('.tool-card').first();
    await expect(card.locator('p.small')).toHaveCount(1);
    expect(await card.locator('p.small').evaluate(node => getComputedStyle(node).fontSize)).toBe('12px');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

    await sidebarLink(page, 'System').click();
    const handoff = main.locator('section.handoff');
    await expect(handoff).toHaveClass(/\bsurface\b/);
    expect(await handoff.evaluate(surfaceStyle)).toMatchObject(SURFACE);
    await expect(handoff.locator('a[href="/prompt_manager/admin"]')).toBeVisible();
    await expect(main.locator('.panel')).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

// #249 System S2 Task 5: the concept's renderSystem shell (app.js 698–706) at `#/system/<section>`, every section a D94
// hand-off until its PR, and the Save row's truthful status (D92).
const SYSTEM_SECTION_LABELS = ['Overview', 'Appearance', 'Sources & scans', 'Integrations', 'Auto-tagging', 'Maintenance',
    'Backup & export', 'Diagnostics & logs'];
const SYSTEM_HASHES = ['overview', 'appearance', 'sources', 'integrations', 'tagging', 'maintenance', 'backups', 'diagnostics'];
const SYSTEM_HANDOFFS = { appearance: ['/prompt_manager/gallery', '/prompt_manager/admin'], sources: ['/prompt_manager/admin', '/prompt_manager/gallery'],
    maintenance: ['/prompt_manager/admin', '/prompt_manager/gallery'] };

test('System is native: the concept shell, section navigation, hand-offs and the Save row', async ({ page }) => {
    const mutations = [];
    page.on('request', item => { if (!['GET', 'HEAD', 'OPTIONS'].includes(item.method())) mutations.push(item.url()); });
    await page.goto(`${workspace}#/system`);
    const main = page.locator('#workspace-main');
    await expect(main.locator('h1')).toHaveText('System');
    await expect(main.locator('.heading p.muted')).toHaveText('Configuration, maintenance and recovery—with browsing kept in the workspace.');
    // D92: no "Sample configuration" chip.
    await expect(main.locator('.heading .chip')).toHaveCount(0);
    expect(await main.evaluate(node => [...node.children].map(child => `${child.localName}.${child.className}`)))
        .toEqual(['div.heading', 'div.section-nav', 'div.settings-layout']);
    const nav = main.getByRole('group', { name: 'System sections', exact: true });
    await expect(nav.getByRole('button')).toHaveText(SYSTEM_SECTION_LABELS);
    await expect(nav.getByRole('button', { name: 'Overview', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await expect(nav.locator('[aria-pressed="true"]')).toHaveCount(1);
    await expect(page.locator('aside.sidebar a[href="#/system"]')).toHaveAttribute('aria-current', 'page');
    const layout = main.locator('.settings-layout');
    await expect(layout.locator(':scope > .save-row > span.small#settings-status')).toHaveText('No unsaved changes.');
    const save = layout.locator(':scope > .save-row > button.ca-primary');
    await expect(save).toHaveText('Save preferences');
    await expect(save).toHaveAttribute('aria-disabled', 'true');
    // Playwright waits on an aria-disabled control, so the ignored press is forced.
    await save.click({ force: true });
    await expect(layout.locator('#settings-status')).toHaveText('No unsaved changes.');

    for (const [index, label] of SYSTEM_SECTION_LABELS.entries()) {
        const hash = SYSTEM_HASHES[index];
        const button = nav.getByRole('button', { name: label, exact: true });
        await button.click();
        await expect(page).toHaveURL(`${workspace}#/system/${hash}`);
        await expect(button).toHaveAttribute('aria-pressed', 'true');
        await expect(nav.locator('[aria-pressed="true"]')).toHaveCount(1);
        // The section changes in place: focus stays on the pressed section, not the page title.
        await expect(button).toBeFocused();
        expect(await layout.evaluate(node => [...node.children].map(child => `${child.localName}.${child.className}`)))
            .toEqual(['section.surface handoff', 'div.save-row']);
        const handoff = layout.locator('section.handoff');
        await expect(handoff.locator('h2')).toHaveText('Your current tools are ready');
        expect(await handoff.locator('a.workspace-link').evaluateAll(links => links.map(link => link.getAttribute('href'))))
            .toEqual(SYSTEM_HANDOFFS[hash] ?? ['/prompt_manager/admin']);
        await expect(layout.locator('#settings-status')).toHaveText('No unsaved changes.');
        await expect(page.locator('aside.sidebar a[href="#/system"]')).toHaveAttribute('aria-current', 'page');
    }
    // Deep links, reload and history keep the section; an unknown section is Overview under its own hash.
    await page.reload();
    await expect(nav.getByRole('button', { name: 'Diagnostics & logs', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await page.goBack();
    await expect(page).toHaveURL(`${workspace}#/system/backups`);
    await expect(nav.getByRole('button', { name: 'Backup & export', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await page.goto(`${workspace}#/system/general`);
    await expect(page).toHaveURL(`${workspace}#/system/overview`);
    await expect(nav.getByRole('button', { name: 'Overview', exact: true })).toHaveAttribute('aria-pressed', 'true');
    // The sidebar's System returns to the Overview section.
    await nav.getByRole('button', { name: 'Integrations', exact: true }).click();
    await sidebarLink(page, 'System').click();
    await expect(page).toHaveURL(`${workspace}#/system`);
    await expect(nav.getByRole('button', { name: 'Overview', exact: true })).toHaveAttribute('aria-pressed', 'true');
    // Leaving System and coming back renders it once, with a fresh section.
    await sidebarLink(page, 'Images').click();
    await expect(main.locator('h1')).toHaveText('Images');
    await expect(main.locator('.section-nav')).toHaveCount(0);
    await page.goBack();
    await expect(main.locator('.section-nav')).toHaveCount(1);
    await expect(main.locator('.settings-layout')).toHaveCount(1);
    expect(mutations).toEqual([]);
});

test('Overview opens native System from its System card (Q12)', async ({ page }) => {
    await page.goto(`${workspace}#/overview`);
    const card = page.locator('#workspace-main .tool-card').filter({ has: page.getByRole('heading', { name: 'System', exact: true }) });
    await expect(card.locator('p.small')).toHaveText('Configuration, maintenance and recovery—with browsing kept in the workspace.');
    const link = card.getByRole('link', { name: 'Open System', exact: true });
    await expect(link).toHaveAttribute('href', '#/system/overview');
    await link.click();
    await expect(page.locator('#workspace-main h1')).toHaveText('System');
    await expect(page.locator('#workspace-main .section-nav [aria-pressed="true"]')).toHaveText('Overview');
});

// Task A3: the concept sidebar (index.html h16-41, app.js nav()) rendered from real data (D27, D28).
const NAV_TOTALS = [
    ['images', 'Images', '/prompt_manager/images/output?mode=workspace&limit=1'],
    ['library', 'Prompt library', '/prompt_manager/library?limit=1'],
    ['tags', 'Tags & categories', '/prompt_manager/vocabulary/tags?limit=1'],
];
async function serverTotals(request) {
    const totals = {};
    for (const [id, , url] of NAV_TOTALS) {
        const response = await request.get(url);
        await expect(response).toBeOK();
        totals[id] = (await response.json()).total;
        expect(Number.isSafeInteger(totals[id]) && totals[id] >= 0).toBe(true);
    }
    return totals;
}
const navCount = (page, route) => page.locator(`#primary-nav a[data-route="${route}"] .nav-count`);
const childShape = locator => locator.evaluate(node => [...node.children]
    .map(child => `${child.localName}${child.id ? `#${child.id}` : ''}${[...child.classList].map(name => `.${name}`).join('')}`));

test('sidebar matches the concept groups and shows real counts', async ({ page, request }) => {
    const totals = await serverTotals(request);
    expect(totals.library).toBe(2);
    await page.setViewportSize({ width: 1600, height: 1100 });
    await page.goto(`${workspace}#/images`);
    await expect(page.locator('body > .app-shell > aside.sidebar')).toHaveCount(1);
    await expect(page.locator('body > .app-shell > .workspace > #workspace-main')).toHaveCount(1);
    await expect(page.locator('.workspace-shell, .workspace-body, .sidebar-footer, .connection-dot, .workspace-icon')).toHaveCount(0);
    const sidebar = page.locator('aside.sidebar');
    expect(await childShape(sidebar)).toEqual(['a.brand', 'div.sidebar-label', 'nav#primary-nav', 'div.sidebar-label',
        'nav#saved-nav', 'div.sidebar-bottom']);
    await expect(sidebar.locator('.brand')).toHaveAttribute('href', '#/images');
    // D49: SAVED VIEWS without the concept's PROPOSED badge; D28: still no COLLECTIONS.
    await expect(sidebar.locator('.sidebar-label')).toHaveText(['WORKSPACE', 'SAVED VIEWS']);
    await expect(page.locator('nav#saved-nav')).toHaveAttribute('aria-label', 'Saved views');
    await expect(page.locator('#collections-nav, .new-label')).toHaveCount(0);
    const primary = page.locator('nav#primary-nav');
    await expect(primary).toHaveAttribute('aria-label', 'Workspace');
    for (const [id] of NAV_TOTALS) await expect(navCount(page, id)).toHaveText(totals[id].toLocaleString('en-US'));
    const links = await primary.evaluate(node => [...node.children].map(link => ({ tag: link.localName,
        href: link.getAttribute('href'), icon: link.querySelector('svg')?.getAttribute('class'),
        nodes: [...link.childNodes].map(child => child.nodeType === Node.TEXT_NODE ? `text:${child.textContent}`
            : `${child.localName}${[...child.classList].map(name => `.${name}`).join('')}`) })));
    expect(links).toEqual([
        { tag: 'a', href: '#/images', nodes: ['svg.icon', 'text:Images', 'span.nav-count'], icon: 'icon' },
        { tag: 'a', href: '#/library', nodes: ['svg.icon', 'text:Prompt library', 'span.nav-count'], icon: 'icon' },
        { tag: 'a', href: '#/tags', nodes: ['svg.icon', 'text:Tags & categories', 'span.nav-count'], icon: 'icon' },
        { tag: 'a', href: '#/inspect', nodes: ['svg.icon', 'text:Inspect a file'], icon: 'icon' },
    ]);
    await expect(primary.locator('a[href="#/images"]')).toHaveAttribute('aria-current', 'page');
    await expect(primary.locator('a[aria-current]')).toHaveCount(1);
    await expect(page.locator('#primary-nav a[data-route="inspect"] .nav-count')).toHaveCount(0);
    // Concept tokens: 38px rows, 12px muted labels, 10px muted counts pushed to the end.
    const style = await primary.locator('a[href="#/library"]').evaluate(link => {
        const own = getComputedStyle(link), count = link.querySelector('.nav-count');
        return { minHeight: own.minHeight, fontSize: own.fontSize, gap: own.columnGap, count: getComputedStyle(count).fontSize,
            end: Math.round(link.getBoundingClientRect().right - count.getBoundingClientRect().right) };
    });
    expect(style).toEqual({ minHeight: '38px', fontSize: '12px', gap: '11px', count: '10px', end: 12 });
    // At phone width the concept wraps the links and hides counts and the connection (concept 950px block).
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(navCount(page, 'library')).toBeHidden();
    await expect(page.locator('.sidebar-label, .connection')).toHaveCount(3);
    for (const hidden of await page.locator('.sidebar-label, .connection').all()) await expect(hidden).toBeHidden();
    await expect(primary.locator('a[href="#/inspect"]')).toBeVisible();
});

test('nav counts stay hidden until loaded and when their request fails', async ({ page, request }) => {
    const totals = await serverTotals(request);
    let release;
    const pending = new Promise(resolve => { release = resolve; });
    await page.route('**/prompt_manager/library?limit=1', async route => {
        await pending;
        await route.fulfill({ status: 503, json: { success: false, error: 'Unavailable' } });
    });
    await page.route('**/prompt_manager/vocabulary/tags?limit=1', route => route.fulfill({ json: { success: true, total: -4 } }));
    await page.goto(`${workspace}#/overview`);
    await expect(page.locator('#workspace-main h1')).toHaveText('Overview');
    // Nothing renders for a read that is outstanding: no "0" placeholder. The settled Images count does not wait for it.
    await expect(navCount(page, 'images')).toHaveText(String(totals.images));
    await expect(navCount(page, 'library')).toHaveCount(0);
    await expect(navCount(page, 'tags')).toHaveCount(0);
    release();
    await expect(navCount(page, 'images')).toHaveText(String(totals.images));
    await expect(navCount(page, 'library')).toHaveCount(0);
    await expect(navCount(page, 'tags')).toHaveCount(0);
    await expect(page.locator('#primary-nav .nav-count')).toHaveCount(1);
});

// A3 review: the Images listing scans output folders, so its slow count must not hold back Library and Tags.
test('a slow Images count does not delay the Library and Tags badges', async ({ page, request }) => {
    const totals = await serverTotals(request);
    let release;
    const pending = new Promise(resolve => { release = resolve; });
    await page.route(url => url.pathname.endsWith('/prompt_manager/images/output') && url.searchParams.get('limit') === '1', async route => {
        await pending;
        await route.continue().catch(() => {});
    });
    await page.goto(`${workspace}#/overview`);
    await expect(navCount(page, 'library')).toHaveText(String(totals.library));
    await expect(navCount(page, 'tags')).toHaveText(String(totals.tags));
    await expect(navCount(page, 'images')).toHaveCount(0);
    release();
    await expect(navCount(page, 'images')).toHaveText(String(totals.images));
});

test('sidebar bottom holds Activity, System and Overview with a neutral connection', async ({ page }) => {
    await page.goto(`${workspace}#/system`);
    const bottom = page.locator('aside.sidebar > .sidebar-bottom');
    expect(await childShape(bottom)).toEqual(['nav#system-nav', 'div.connection']);
    const system = page.locator('nav#system-nav');
    await expect(system).toHaveAttribute('aria-label', 'Tools and system');
    expect(await system.locator(':scope > a').evaluateAll(nodes => nodes.map(node => [node.getAttribute('href'), node.textContent]))).toEqual([
        ['#/activity', 'Activity'], ['#/system', 'System'], ['#/overview', 'Overview'],
    ]);
    await expect(system.locator('.nav-count')).toHaveCount(0);
    await expect(system.locator('a[href="#/system"]')).toHaveAttribute('aria-current', 'page');
    await expect(system.locator('svg.icon')).toHaveCount(3);
    const connection = bottom.locator('.connection');
    expect(await childShape(connection)).toEqual(['i', 'div']);
    await expect(connection.locator(':scope > div')).toHaveText('ComfyUI workspaceYour library, on your server');
    await expect(connection.locator('small')).toHaveText('Your library, on your server');
    // D28: no fake connection state. The dot is the muted text colour, not the concept's demo green.
    const dot = await connection.evaluate(node => ({ dot: getComputedStyle(node.querySelector('i')).backgroundColor,
        muted: getComputedStyle(node.querySelector('small')).color }));
    expect(dot.dot).toBe(dot.muted);
    await page.locator('nav#system-nav').getByRole('link', { name: 'Overview', exact: true }).click();
    await expect(page.locator('#workspace-main h1')).toHaveText('Overview');
    await expect(system.locator('a[href="#/overview"]')).toHaveAttribute('aria-current', 'page');
    await expect(page.locator('aside.sidebar a[aria-current]')).toHaveCount(1);
});

test('counts refresh after a confirmed editor save', async ({ page, request }) => {
    const before = await serverTotals(request);
    await page.goto(`${workspace}#/library`);
    await expect(navCount(page, 'library')).toHaveText(String(before.library));
    await expect(navCount(page, 'tags')).toHaveText(String(before.tags));
    const reads = [];
    page.on('request', item => {
        const url = new URL(item.url());
        if (url.searchParams.get('limit') === '1') reads.push(url.pathname);
    });
    await page.getByRole('button', { name: '+ New prompt', exact: true }).click();
    const dialog = page.locator('dialog.library-editor');
    await expect(dialog.getByRole('button', { name: 'Save', exact: true })).toBeEnabled();
    await dialog.getByLabel('Title', { exact: true }).fill('Harbor at dawn');
    await dialog.getByLabel('Positive prompt', { exact: true }).fill('A quiet harbor at dawn for the sidebar count');
    await dialog.getByLabel('Tags, separated by commas', { exact: true }).fill('sidebar_count_tag');
    await dialog.getByRole('button', { name: 'Save', exact: true }).click();
    await expect(dialog).toHaveCount(0);
    const after = await serverTotals(request);
    expect(after.library).toBe(before.library + 1);
    expect(after.tags).toBeGreaterThan(before.tags);
    await expect(navCount(page, 'library')).toHaveText(String(after.library));
    await expect(navCount(page, 'tags')).toHaveText(String(after.tags));
    expect(reads.filter(path => path === '/prompt_manager/library').length).toBeGreaterThanOrEqual(1);
    // A Library write cannot change the Images count, so the Images listing is not read again.
    expect(reads.filter(path => path === '/prompt_manager/images/output')).toEqual([]);
});

test('a superseded count read never lands and a failed refresh removes the stale count', async ({ page, request }) => {
    const totals = await serverTotals(request);
    await page.goto(`${workspace}#/overview`);
    await expect(navCount(page, 'library')).toHaveText(String(totals.library));
    const held = [];
    let reads = 0;
    await page.route('**/prompt_manager/library?limit=1', route => {
        if (reads++ === 0) { held.push(route); return undefined; }
        return route.fulfill({ status: 503, json: { success: false, error: 'Unavailable' } });
    });
    const invalidate = () => page.evaluate(async () => {
        const { invalidateLibraryAfterWrite } = await import('/prompt_manager/js/workspace/library.mjs');
        invalidateLibraryAfterWrite({ kind: 'test', receipt: {} });
    });
    await invalidate();
    await expect.poll(() => held.length).toBe(1);
    await invalidate();
    await expect(navCount(page, 'library')).toHaveCount(0);
    await expect(navCount(page, 'images')).toHaveText(String(totals.images));
    await expect(navCount(page, 'tags')).toHaveText(String(totals.tags));
    await held[0].fulfill({ json: { success: true, total: 999 } }).catch(() => {});
    await page.waitForTimeout(100);
    await expect(navCount(page, 'library')).toHaveCount(0);
    await page.unroute('**/prompt_manager/library?limit=1');
    // Leaving the page with a read in flight aborts it; the restored shell reads the counts again.
    let release;
    const pending = new Promise(resolve => { release = resolve; });
    await page.route('**/prompt_manager/library?limit=1', async route => { await pending; await route.continue().catch(() => {}); }, { times: 1 });
    await invalidate();
    await page.evaluate(() => {
        window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true }));
        window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }));
    });
    release();
    await expect(navCount(page, 'library')).toHaveText(String(totals.library));
    await expect(page.locator('#primary-nav a')).toHaveCount(4);
    await expect(page.locator('#primary-nav .nav-count')).toHaveCount(3);
});

// Task A5: the concept heading() (app.js 303-305) with its typography (style.css 73-103, 283-300; <=700 1408-1416) on
// every native route, and the production footer copy in the concept `.app-footer` (D29).
const headingShape = node => {
    const style = element => getComputedStyle(element);
    const shape = element => `${element.localName}${element.id ? `#${element.id}` : ''}${[...element.classList].map(name => `.${name}`).join('')}`;
    const [copy, actions] = node.children;
    const [h1, description] = copy.children;
    const main = node.parentElement.getBoundingClientRect();
    return {
        children: [...node.children].map(shape), copy: [...copy.children].map(shape), tabIndex: h1.tabIndex,
        direction: style(node).flexDirection, marginBottom: style(node).marginBottom,
        h1: { fontSize: style(h1).fontSize, lineHeight: Math.round(parseFloat(style(h1).lineHeight) / parseFloat(style(h1).fontSize) * 100) / 100, fontWeight: style(h1).fontWeight, letterSpacing: style(h1).letterSpacing, marginTop: style(h1).marginTop },
        description: { text: description.textContent.length > 0, fontSize: style(description).fontSize, marginTop: style(description).marginTop, muted: style(description).color === style(document.querySelector('.connection small')).color },
        actions: { gap: style(actions).columnGap, left: Math.round(actions.getBoundingClientRect().left), fullWidth: Math.round(actions.getBoundingClientRect().width) === Math.round(node.getBoundingClientRect().width) },
        mainLeft: Math.round(main.left + parseFloat(getComputedStyle(node.parentElement).paddingLeft)),
        pageTitles: document.querySelectorAll('#page-title').length,
        retired: document.querySelectorAll('.workspace-heading, .workspace-heading-copy, #workspace-main .intro').length,
    };
};

test('every route renders the concept heading', async ({ page, request }) => {
    const library = await (await request.get('/prompt_manager/library?limit=1')).json();
    const positiveId = library.items[0].id;
    const pages = [['overview', 'Overview'], ['images', 'Images'], ['library', 'Prompt library'], ['tags', 'Tags & categories'], [`library/${positiveId}`, null]];
    for (const width of [1600, 390]) {
        await page.setViewportSize({ width, height: 900 });
        for (const [hash, title] of pages) {
            await page.goto(`${workspace}#/${hash}`);
            const heading = page.locator('#workspace-main > .heading');
            await expect(heading).toHaveCount(1);
            if (title) await expect(heading.locator('h1')).toHaveText(title);
            else await expect(heading.getByRole('button', { name: '← Prompt library', exact: true })).toBeVisible();
            await expect(heading.locator('p.muted')).not.toHaveText('');
            const shape = await heading.evaluate(headingShape);
            expect(shape, hash).toMatchObject({
                children: ['div', 'div.heading-actions'], copy: ['h1#page-title', 'p.muted'], tabIndex: -1, marginBottom: '22px',
                h1: width === 1600 ? { fontSize: '28px', lineHeight: 1.3, fontWeight: '620', letterSpacing: '-0.7px', marginTop: '0px' }
                    : { fontSize: '25px', lineHeight: 1.3, fontWeight: '620', letterSpacing: '-0.7px', marginTop: '0px' },
                description: { text: true, fontSize: '12px', marginTop: '6px', muted: true },
                actions: { gap: '8px' }, pageTitles: 1, retired: 0,
            });
            if (width === 1600) {
                expect(shape.direction, hash).toBe('row');
            } else {
                // The concept stacks the heading and gives the actions the full row, starting at main's 14px gutter.
                expect(shape.direction, hash).toBe('column');
                expect(shape.actions, hash).toMatchObject({ left: 14, fullWidth: true });
                expect(shape.mainLeft, hash).toBe(14);
            }
        }
    }
});

test('the footer is the concept app footer with production copy', async ({ page }) => {
    for (const [width, padding, direction] of [[1600, '22px 30px', 'row'], [390, '20px 14px', 'column']]) {
        await page.setViewportSize({ width, height: 900 });
        await page.goto(`${workspace}#/overview`);
        const footer = page.locator('.workspace > footer.app-footer');
        await expect(footer).toHaveCount(1);
        await expect(page.locator('.page-footer')).toHaveCount(0);
        expect(await footer.evaluate(node => ({
            children: [...node.children].map(child => child.localName), text: node.textContent,
            padding: getComputedStyle(node).padding, fontSize: getComputedStyle(node).fontSize, direction: getComputedStyle(node).flexDirection,
            muted: getComputedStyle(node).color === getComputedStyle(document.querySelector('.connection small')).color,
            afterMain: node.previousElementSibling.id,
        }))).toEqual({ children: ['span'], text: 'ComfyAssets · Local tools. Your library.', padding, fontSize: '10px', direction, muted: true, afterMain: 'workspace-main' });
    }
});

// #164: the maintainer serves ComfyUI behind a proxy prefix. Everything, the ComfyUI link included, stays on that mount.
const PROXY_MOUNT = '/ComfyBackendDirect';
async function serveUnderProxyMount(page) {
    const escaped = [];
    page.on('request', item => {
        const { pathname } = new URL(item.url());
        if (pathname.startsWith('/prompt_manager/')) escaped.push(pathname);
    });
    await page.route(url => url.pathname.startsWith(`${PROXY_MOUNT}/`), async route => {
        const url = new URL(route.request().url());
        url.pathname = url.pathname.slice(PROXY_MOUNT.length);
        // A request still in flight when the test ends (a thumbnail the last route started) meets a page that is
        // closing: its fetched response is disposed under it. Only that teardown error is ignored.
        try {
            const response = await route.fetch({ url: url.href });
            await route.fulfill({ response });
        } catch (error) {
            if (!/has been disposed|has been closed|Target page, context or browser has been closed/.test(String(error?.message))) throw error;
        }
    });
    return escaped;
}

test('the ComfyUI link points to the root of a non-standard proxy mount', async ({ page }) => {
    const escaped = await serveUnderProxyMount(page);
    for (const route of ['overview', 'library', 'tags']) {
        await page.goto(`${PROXY_MOUNT}${workspace}#/${route}`);
        await expect(page.locator('#workspace-main > .heading h1')).toBeVisible();
        const link = page.getByRole('link', { name: 'ComfyUI', exact: true });
        await expect(link).toHaveAttribute('href', `${PROXY_MOUNT}/`);
        expect(await link.evaluate(node => node.href)).toBe(new URL(`${PROXY_MOUNT}/`, page.url()).href);
    }
    await expect(navCount(page, 'library')).toHaveCount(1);
    expect(escaped).toEqual([]);
});

test('a resolver failure for the ComfyUI link never stops the shell from mounting', async ({ page }) => {
    await serveUnderProxyMount(page);
    // Wrap the shared client as it loads so only the topbar's canvas-root lookup throws (the sender reads the same root).
    // Every engine's stack names the calling module's URL; each injected failure is counted so a renamed module or a
    // changed call site fails this test on the count instead of silently testing nothing.
    await page.addInitScript(() => {
        let client;
        window.__resolverFailures = { injected: 0 };
        Object.defineProperty(window, 'ApiClient', { configurable: true, get: () => client, set(value) {
            client = { ...value, resolveUrl: (url, ...rest) => {
                if (url === '/prompt_manager/' && /\/workspace\/topbar\.mjs\b/.test(String(new Error().stack))) {
                    window.__resolverFailures.injected += 1;
                    throw new Error('resolver unavailable');
                }
                return value.resolveUrl(url, ...rest);
            } };
        } });
    });
    const warnings = [];
    page.on('console', message => { if (message.type() === 'warning') warnings.push(message.text()); });
    await page.goto(`${PROXY_MOUNT}${workspace}#/overview`);
    await expect(page.locator('#workspace-main h1')).toHaveText('Overview');
    await expect(sidebarLinks(page)).toHaveCount(routes.length);
    expect(await page.evaluate(() => window.__resolverFailures.injected), 'the topbar lookup was the one that failed').toBe(1);
    expect(warnings.filter(text => text.includes('the ComfyUI link falls back to the page mount'))).toHaveLength(1);
    await expect(page.getByRole('link', { name: 'ComfyUI', exact: true })).toHaveAttribute('href', `${PROXY_MOUNT}/`);
    await sidebarLink(page, 'Prompt library').click();
    await expect(page.locator('#workspace-main h1')).toHaveText('Prompt library');
});
