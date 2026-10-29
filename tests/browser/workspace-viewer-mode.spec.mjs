// #450 (TESTING.md D156): every viewer has a full-screen toggle beside ✕. Full screen fills the browser window edge to
// edge, Centered is the concept's framed dialog; the button names the view it switches to. System › Appearance ›
// Viewer › "Open viewer in" chooses how the viewer opens; the button changes the current view only.
import { test, expect } from './fixtures.mjs';
import { seedConcept, openProduction } from './concept-inventory.mjs';
import { parityPng } from './local-inspect-files.mjs';

test.use({ locale: 'en-US', timezoneId: 'UTC', reducedMotion: 'reduce' });

const CARDS = '.gallery-body > .image-grid > .media-card';
const viewer = page => page.locator('dialog#viewer-dialog[open]');
const modeButton = page => viewer(page).locator('.dialog-header > button#viewer-mode');
const details = page => viewer(page).locator('#viewer-details');

test.beforeEach(async ({ request }, testInfo) => { testInfo.manifest = await seedConcept(request); });

async function openImages(page, { width = 1600, height = 1100, theme = 'dark' } = {}) {
    await page.setViewportSize({ width, height });
    await openProduction(page, 'images', theme);
    await page.locator(CARDS).first().locator('.image-open').click();
    await expect(viewer(page).locator('#viewer-details .panel-heading')).toBeVisible();
}

/** The dialog's box and edges against the window. */
const frame = page => viewer(page).evaluate(node => {
    const box = node.getBoundingClientRect(), style = getComputedStyle(node);
    return { left: Math.round(box.left), top: Math.round(box.top), right: Math.round(innerWidth - box.right),
        bottom: Math.round(document.documentElement.clientHeight - box.bottom), radius: style.borderTopLeftRadius,
        border: style.borderTopWidth, fills: Math.abs(box.width - document.documentElement.clientWidth) <= 1 };
});

test('the button beside ✕ switches between Centered and Full screen and names the view it switches to', async ({ page }) => {
    await openImages(page);
    const header = await viewer(page).locator('.dialog-header').evaluate(node => [...node.children].map(child => child.id || child.localName));
    expect(header).toEqual(['div', 'viewer-mode', 'viewer-close']);
    await expect(modeButton(page)).toHaveAccessibleName('Full screen view');
    await expect(modeButton(page)).toHaveAttribute('title', 'Full screen view');
    await expect(modeButton(page).locator('svg.icon')).toHaveCount(1);
    const centered = await frame(page);
    expect(centered.left).toBeGreaterThan(0);
    expect(centered.fills).toBe(false);
    // Keyboard: the button is reachable and keeps focus after a switch.
    await viewer(page).locator('#viewer-close').focus();
    await page.keyboard.press('Shift+Tab');
    await expect(modeButton(page)).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(viewer(page)).toHaveClass(/\bviewer-fullscreen\b/);
    await expect(modeButton(page)).toBeFocused();
    await expect(modeButton(page)).toHaveAccessibleName('Centered view');
    expect(await frame(page)).toEqual({ left: 0, top: 0, right: 0, bottom: 0, radius: '0px', border: '0px', fills: true });
    // The details stay toggleable in full screen.
    const toggle = viewer(page).getByRole('button', { name: 'Hide details', exact: true });
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await toggle.click();
    await expect(details(page)).toBeHidden();
    await viewer(page).getByRole('button', { name: 'Show details', exact: true }).click();
    await expect(details(page)).toBeVisible();
    // Back to Centered: today's dialog, with no toggle while the collapse setting is off.
    await modeButton(page).click();
    await expect(viewer(page)).not.toHaveClass(/\bviewer-fullscreen\b/);
    expect(await frame(page)).toEqual(centered);
    await expect(viewer(page).getByRole('button', { name: /^(Show|Hide) details$/ })).toHaveCount(0);
    // Navigation keeps the current view; Esc closes from full screen; the next open is Centered again.
    await modeButton(page).click();
    await viewer(page).getByRole('button', { name: 'Next media', exact: true }).click();
    await expect(viewer(page)).toHaveClass(/\bviewer-fullscreen\b/);
    await viewer(page).getByRole('button', { name: 'Hide details', exact: true }).focus();
    await page.keyboard.press('Escape');
    await expect(viewer(page)).toHaveCount(0);
    await page.locator(CARDS).first().locator('.image-open').click();
    await expect(viewer(page)).not.toHaveClass(/\bviewer-fullscreen\b/);
});

test('switching back to Centered keeps the collapse setting\'s toggle and hidden details (D97)', async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('gallerySettings', JSON.stringify({ sidebarCollapsedByDefault: true })));
    await openProduction(page, 'images', 'dark');
    await page.locator(CARDS).first().locator('.image-open').click();
    await expect(viewer(page)).toBeVisible();
    await expect(details(page)).toBeHidden();
    await modeButton(page).click();
    await expect(viewer(page).getByRole('button', { name: 'Show details', exact: true })).toBeVisible();
    await modeButton(page).click();
    await expect(details(page)).toBeHidden();
    await expect(viewer(page).getByRole('button', { name: 'Show details', exact: true })).toHaveAttribute('aria-expanded', 'false');
});

test('System › Appearance › Viewer chooses how the viewer opens; the button changes only the current view', async ({ page }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    await openProduction(page, 'system/appearance', 'dark');
    const surface = page.locator('#workspace-main section.surface').filter({ has: page.locator('h2', { hasText: /^Viewer$/ }) });
    await expect(surface.locator('.setting-row > div > :is(label, h3)')).toHaveText(['Open viewer in', 'Collapse viewer details by default']);
    const select = surface.getByRole('combobox', { name: 'Open viewer in', exact: true });
    await expect(select).toHaveValue('centered');
    await select.selectOption('fullscreen');
    await page.getByRole('button', { name: 'Save preferences', exact: true }).click();
    await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem('gallerySettings') || '{}').viewerOpenMode)).toBe('fullscreen');
    await page.evaluate(() => { location.hash = '#/images'; });
    await page.locator(CARDS).first().locator('.image-open').click();
    await expect(viewer(page)).toHaveClass(/\bviewer-fullscreen\b/);
    await expect(modeButton(page)).toHaveAccessibleName('Centered view');
    await modeButton(page).click();
    await expect(viewer(page)).not.toHaveClass(/\bviewer-fullscreen\b/);
    expect(await page.evaluate(() => JSON.parse(localStorage.getItem('gallerySettings')).viewerOpenMode)).toBe('fullscreen');
});

test('Inspect opens full screen with its details when that is the default (D155)', async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('gallerySettings', JSON.stringify({ viewerOpenMode: 'fullscreen', sidebarCollapsedByDefault: true })));
    await page.setViewportSize({ width: 1600, height: 1100 });
    await openProduction(page, 'inspect', 'dark');
    await page.locator('#local-file').setInputFiles(parityPng('f02.png'));
    await expect(viewer(page)).toHaveClass(/\bviewer-fullscreen\b/);
    await expect(details(page)).toBeVisible();
    await expect(viewer(page).getByRole('button', { name: 'Hide details', exact: true })).toBeVisible();
});

test('the Library gallery viewer has the button too', async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    await openProduction(page, `library/${testInfo.manifest.prompts[0].positive_id}`, 'dark');
    await page.locator('.media-card .image-open').first().click();
    await expect(modeButton(page)).toHaveAccessibleName('Full screen view');
});

test('at 390px the viewer is already full-bleed and the button hides', async ({ page }) => {
    await openImages(page, { width: 390, height: 844 });
    await expect(viewer(page).locator('#viewer-mode')).toBeHidden();
    await expect(viewer(page).locator('#viewer-close')).toBeVisible();
});

test('full screen holds at 200% zoom and coarse pointers get a 40px target', async ({ page, browserName }) => {
    await openImages(page);
    await modeButton(page).click();
    await page.evaluate(() => { document.documentElement.style.zoom = '2'; });
    const zoomed = await frame(page);
    expect(zoomed).toMatchObject({ left: 0, top: 0, right: 0, bottom: 0 });
    await page.evaluate(() => { document.documentElement.style.zoom = '1'; });
    if (await page.evaluate(() => matchMedia('(pointer: coarse)').matches)) {
        const size = await modeButton(page).evaluate(node => [node.getBoundingClientRect().width, node.getBoundingClientRect().height]);
        expect(Math.min(...size)).toBeGreaterThanOrEqual(40);
    }
    test.info().annotations.push({ type: 'engine', description: browserName });
});
