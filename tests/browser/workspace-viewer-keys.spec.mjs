// #532 (TESTING.md D170): ← and → move through the viewer's files wherever focus is, except where the arrows belong to
// what has focus; ⌘ beside the view switch lists the shortcuts in an overlay that closes on its own.
import { test, expect } from './fixtures.mjs';
import { seedConcept, openProduction } from './concept-inventory.mjs';

test.use({ locale: 'en-US', timezoneId: 'UTC', reducedMotion: 'reduce' });

const CARDS = '.gallery-body > .image-grid > .media-card';
const viewer = page => page.locator('dialog#viewer-dialog[open]');
const position = page => viewer(page).locator('.media-position');
const keysButton = page => viewer(page).locator('.dialog-header > button#viewer-keys');
const overlay = page => page.getByRole('dialog', { name: 'Keyboard shortcuts', exact: true });

test.beforeEach(async ({ request }, testInfo) => { testInfo.manifest = await seedConcept(request); });

async function openImages(page, width = 1600) {
    await page.setViewportSize({ width, height: 1100 });
    await openProduction(page, 'images', 'dark');
    await page.locator(CARDS).first().locator('.image-open').click();
    await expect(viewer(page).locator('#viewer-details .panel-heading')).toBeVisible();
    await expect(position(page)).toContainText('1 of ');
}
async function movesBothWays(page) {
    await page.keyboard.press('ArrowRight');
    await expect(position(page)).toContainText('2 of ');
    await page.keyboard.press('ArrowLeft');
    await expect(position(page)).toContainText('1 of ');
}
async function staysPut(page) {
    const before = await position(page).textContent();
    await page.keyboard.press('ArrowRight');
    await page.evaluate(() => new Promise(requestAnimationFrame));
    await expect(position(page)).toHaveText(before);
}

test('the arrows move through the files as soon as the viewer opens, with focus on ✕', async ({ page }) => {
    await openImages(page);
    await expect(viewer(page).locator('#viewer-close')).toBeFocused();
    await movesBothWays(page);
});

test('the arrows keep working from every header control and when focus falls to the page', async ({ page }) => {
    await openImages(page);
    await viewer(page).locator('#viewer-mode').click();
    await movesBothWays(page);
    await viewer(page).locator('#viewer-mode').click();
    // Header text is not focusable: the click leaves focus outside any control.
    await viewer(page).locator('.dialog-header p').click();
    await movesBothWays(page);
    await page.evaluate(() => document.activeElement?.blur());
    await movesBothWays(page);
});

test('the arrows move from a details button but stay with focused text and with modifier keys', async ({ page }) => {
    await openImages(page);
    await viewer(page).locator('#viewer-details button').first().focus();
    await movesBothWays(page);
    await viewer(page).locator('#viewer-details p.prompt-text').first().focus();
    await staysPut(page);
    await viewer(page).locator('#viewer-close').focus();
    const before = await position(page).textContent();
    await page.keyboard.press('Shift+ArrowRight');
    await page.evaluate(() => new Promise(requestAnimationFrame));
    await expect(position(page)).toHaveText(before);
});

test('⌘ lists the shortcuts; Esc closes only the overlay and the arrows wait for it', async ({ page }) => {
    await openImages(page);
    await expect(keysButton(page)).toHaveAccessibleName('Keyboard shortcuts');
    await expect(keysButton(page)).toHaveAttribute('title', 'Keyboard shortcuts');
    await expect(keysButton(page).locator('svg.icon')).toHaveCount(1);
    await expect(page.locator('#viewer-keys-dialog')).toHaveCount(0);
    await keysButton(page).click();
    await expect(overlay(page)).toBeVisible();
    expect(await overlay(page).locator('dt').allTextContents()).toEqual(['←', '→', 'Esc']);
    expect(await overlay(page).locator('dd').allTextContents()).toEqual(['Previous file', 'Next file', 'Close the viewer']);
    // A small dialog sized to its content, its title inset from the edge.
    const box = await overlay(page).evaluate(node => {
        const frame = node.getBoundingClientRect(), title = node.querySelector('h2').getBoundingClientRect();
        return { height: frame.height, inset: title.left - frame.left };
    });
    expect(box.height).toBeLessThan(400);
    expect(box.inset).toBeGreaterThan(8);
    await staysPut(page);
    await page.keyboard.press('Escape');
    await expect(overlay(page)).toHaveCount(0);
    await expect(viewer(page)).toBeVisible();
    await expect(keysButton(page)).toBeFocused();
    await movesBothWays(page);
    await page.keyboard.press('Escape');
    await expect(viewer(page)).toHaveCount(0);
});

test('the overlay × closes only the overlay; closing the viewer never leaves one behind', async ({ page }) => {
    await openImages(page);
    await keysButton(page).click();
    await overlay(page).getByRole('button', { name: 'Close keyboard shortcuts', exact: true }).click();
    await expect(overlay(page)).toHaveCount(0);
    await expect(viewer(page)).toBeVisible();
    await expect(keysButton(page)).toBeFocused();
    await keysButton(page).click();
    await expect(overlay(page)).toBeVisible();
    await page.evaluate(() => { location.hash = '/overview'; });
    await expect(page.locator('#viewer-dialog,#viewer-keys-dialog')).toHaveCount(0);
});

test('opening the shortcuts stops the slideshow', async ({ page }) => {
    await openImages(page);
    const play = viewer(page).locator('#viewer-toolbar').getByRole('button', { name: /slideshow$/ });
    await play.click();
    await expect(play).toHaveAttribute('aria-label', 'Pause slideshow');
    await keysButton(page).click();
    await expect(overlay(page)).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(play).toHaveAttribute('aria-label', 'Play slideshow');
});

test('the shortcuts button hides at phone width, like the view switch', async ({ page }) => {
    await openImages(page, 600);
    await expect(keysButton(page)).toBeHidden();
    await movesBothWays(page);
});
