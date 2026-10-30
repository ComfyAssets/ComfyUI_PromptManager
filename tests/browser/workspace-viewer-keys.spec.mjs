// #532 (TESTING.md D170): ← and → move through the viewer's files wherever focus is, except where the arrows belong to
// what has focus; ⌘ beside the view switch lists the shortcuts in an overlay that closes on its own.
import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';
import { seedConcept, openProduction } from './concept-inventory.mjs';

test.use({ locale: 'en-US', timezoneId: 'UTC', reducedMotion: 'reduce' });

const CARDS = '.gallery-body > .image-grid > .media-card';
const viewer = page => page.locator('dialog#viewer-dialog[open]');
const position = page => viewer(page).locator('.media-position');
const keysButton = page => viewer(page).locator('.dialog-header > button#viewer-keys');
const ALL_KEYS = ['←', '→', 'Space', '+', '-', '0', 'R', 'H', 'F', 'I', '?', 'Esc', 'D', 'Delete'];
const overlay = page => page.getByRole('dialog', { name: 'Keyboard shortcuts', exact: true });

test.beforeEach(async ({ page, request, browserName }, testInfo) => {
    await startCoverage(page, browserName);
    testInfo.manifest = await seedConcept(request);
});
test.afterEach(async ({ page, browserName }) => { await saveCoverage(page, browserName); });

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
    expect(await overlay(page).locator('dt').allTextContents()).toEqual(ALL_KEYS);
    // A small dialog sized to its content, its title inset from the edge.
    const box = await overlay(page).evaluate(node => {
        const frame = node.getBoundingClientRect(), title = node.querySelector('h2').getBoundingClientRect();
        return { height: frame.height, inset: title.left - frame.left };
    });
    expect(box.height).toBeLessThan(700);
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

// ── #532: the toolbar's actions on single keys. Each key clicks the toolbar's own control, so the two cannot disagree.
const stage = page => viewer(page).locator('#viewer-stage');
const image = page => viewer(page).locator('.viewer-canvas img');
const control = (page, name) => viewer(page).getByRole('button', { name, exact: true });
const transform = page => image(page).evaluate(node => node.style.transform + '|' + node.style.width);
const playing = page => viewer(page).locator('#viewer-toolbar').getByRole('button', { name: /slideshow$/ });
const review = page => page.locator('dialog#modal[open]');
/** A key event a browser would keep for itself, or that repeats, sent straight to what has focus. */
const synth = (page, key, init = {}) => page.evaluate(([key, init]) => {
    (document.activeElement || document.body).dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init }));
}, [key, init]);
async function ready(page) {
    await expect(control(page, 'Zoom in')).toHaveAttribute('aria-disabled', 'false');
    await stage(page).focus();
}
/** Nothing the keys can change has changed. */
const snapshot = async page => ({ position: await position(page).textContent(), transform: await transform(page),
    play: await playing(page).getAttribute('aria-label'), classes: await viewer(page).getAttribute('class'),
    details: await viewer(page).locator('#viewer-details').isVisible(), modals: await page.locator('dialog[open]').count() });

test('+ - 0 zoom and fit, R rotates, H flips', async ({ page }) => {
    await openImages(page);await ready(page);
    const fit = await transform(page);
    await page.keyboard.press('+');
    await expect.poll(() => transform(page)).not.toBe(fit);
    const zoomed = await transform(page);
    await page.keyboard.press('-');
    await expect.poll(() => transform(page)).not.toBe(zoomed);
    await page.keyboard.press('+');await page.keyboard.press('0');
    await expect.poll(() => transform(page)).toBe(fit);
    await page.keyboard.press('r');
    await expect.poll(() => transform(page)).toContain('rotate(90deg)');
    await page.keyboard.press('Shift+R');
    await expect.poll(() => transform(page)).toContain('rotate(180deg)');
    await page.keyboard.press('h');
    await expect.poll(() => transform(page)).toContain('scaleX(-1)');
});

test('Space plays and pauses on the stage, and activates a focused button instead', async ({ page }) => {
    await openImages(page);await ready(page);
    await page.keyboard.press('Space');
    await expect(playing(page)).toHaveAttribute('aria-label', 'Pause slideshow');
    await page.keyboard.press('Space');
    await expect(playing(page)).toHaveAttribute('aria-label', 'Play slideshow');
    await control(page, 'Next media').focus();
    await page.keyboard.press('Space');
    await expect(position(page)).toContainText('2 of ');
    await expect(playing(page)).toHaveAttribute('aria-label', 'Play slideshow');
});

test('F switches the view, I shows and hides the details the toggle offers', async ({ page }) => {
    await openImages(page);await ready(page);
    const details = viewer(page).locator('#viewer-details');
    // Centered without the collapse setting: the toolbar has no toggle, so I does nothing.
    await page.keyboard.press('i');
    await page.evaluate(() => new Promise(requestAnimationFrame));
    await expect(details).toBeVisible();
    await page.keyboard.press('f');
    await expect(viewer(page)).toHaveClass(/\bviewer-fullscreen\b/);
    await page.keyboard.press('i');
    await expect(details).toBeHidden();
    await page.keyboard.press('i');
    await expect(details).toBeVisible();
    await page.keyboard.press('f');
    await expect(viewer(page)).not.toHaveClass(/\bviewer-fullscreen\b/);
});

test('F does nothing where the view switch is hidden; ? still opens the shortcuts, which fit at 390 px', async ({ page }) => {
    await openImages(page, 390);await ready(page);
    await page.keyboard.press('f');
    await page.evaluate(() => new Promise(requestAnimationFrame));
    await expect(viewer(page)).not.toHaveClass(/\bviewer-fullscreen\b/);
    await page.keyboard.press('?');
    await expect(overlay(page)).toBeVisible();
    const fit = await overlay(page).evaluate(node => {
        const box = node.getBoundingClientRect();
        return { inside: box.left >= 0 && box.right <= innerWidth && box.top >= 0 && box.bottom <= innerHeight,
            noSideScroll: node.scrollWidth <= node.clientWidth };
    });
    expect(fit).toEqual({ inside: true, noSideScroll: true });
    expect(await overlay(page).locator('dt').allTextContents()).toEqual(ALL_KEYS);
    await overlay(page).locator('dt').last().scrollIntoViewIfNeeded();
    await expect(overlay(page).locator('dt').last()).toBeInViewport();
});

test('D downloads the original', async ({ page }) => {
    await openImages(page);await ready(page);
    const [download] = await Promise.all([page.waitForEvent('download'), page.keyboard.press('d')]);
    expect(download.suggestedFilename()).toMatch(/\.(png|jpe?g|webp)$/);
});

test('Delete opens the review and never deletes; every viewer key waits while the review is open', async ({ page }) => {
    const writes = [];
    page.on('request', request => { if (request.method() !== 'GET' && !request.url().includes('/__test__/')) writes.push(request.url()); });
    await openImages(page);await ready(page);
    const before = await snapshot(page);
    const downloads = [];page.on('download', download => downloads.push(download));
    writes.length = 0;
    await page.keyboard.press('Delete');
    await expect(review(page)).toBeVisible();
    // The review reads its preview; repeating the key must not ask again.
    await page.waitForLoadState('networkidle');
    const opened = writes.length;
    for (const key of ['Delete', 'r', 'f', 'i', 'd', '?']) await page.keyboard.press(key);
    // With focus fallen to the page, too.
    await page.evaluate(() => document.activeElement?.blur());
    for (const key of ['Delete', 'ArrowRight', 'r', ' ']) await page.keyboard.press(key === ' ' ? 'Space' : key);
    await page.waitForLoadState('networkidle');
    expect(writes.length).toBe(opened);
    // Inside the review the arrows and Space are its own: they may choose a mode, which only reads a new preview.
    await review(page).getByRole('radio').first().focus();
    for (const key of ['ArrowRight', 'Space']) await page.keyboard.press(key);
    await expect(overlay(page)).toHaveCount(0);
    await page.keyboard.press('Escape');
    await expect(review(page)).toHaveCount(0);
    await expect(viewer(page)).toBeVisible();
    expect(writes.filter(url => /delete|trash/i.test(url) && !url.endsWith('/delete/preview'))).toEqual([]);
    expect(await snapshot(page)).toEqual(before);
    expect(downloads).toEqual([]);
});

test('the keys do nothing with Ctrl, Cmd or Alt held, on a repeat, or while the overlay is open', async ({ page }) => {
    await openImages(page);await ready(page);
    const before = await snapshot(page);
    const downloads = [];page.on('download', download => downloads.push(download));
    for (const modifier of ['ctrlKey', 'metaKey', 'altKey'])
        for (const key of ['+', '-', '0', 'r', 'h', ' ', 'f', 'd', 'Delete', '?']) await synth(page, key, { [modifier]: true });
    for (const key of [' ', 'r', 'h', 'f', 'd', 'Delete', '?']) await synth(page, key, { repeat: true });
    await page.evaluate(() => new Promise(requestAnimationFrame));
    expect(await snapshot(page)).toEqual(before);
    expect(downloads).toEqual([]);
    await page.keyboard.press('?');
    await expect(overlay(page)).toBeVisible();
    for (const key of ['r', 'h', 'f', 'i', '0', 'Delete']) await page.keyboard.press(key);
    await page.keyboard.press('Escape');
    await expect(overlay(page)).toHaveCount(0);
    expect(await snapshot(page)).toEqual(before);
    await expect(viewer(page)).toBeVisible();
});

test('the keys stay out of a form field, raw metadata and a disclosure', async ({ page }) => {
    await openImages(page);await ready(page);
    const before = await snapshot(page);
    await viewer(page).locator('#viewer-details').evaluate(node => {
        const field = document.createElement('input');field.id = 'probe';node.append(field);
    });
    await viewer(page).locator('#probe').focus();
    await page.keyboard.type('rhfid0+-? ');
    await expect(viewer(page).locator('#probe')).toHaveValue('rhfid0+-? ');
    await viewer(page).locator('#probe').evaluate(node => node.remove());
    const summary = viewer(page).getByText('Workflow & raw metadata', { exact: true });
    await summary.focus();
    for (const key of ['r', 'f', '0']) await page.keyboard.press(key);
    await page.evaluate(() => new Promise(requestAnimationFrame));
    expect(await snapshot(page)).toEqual(before);
});

test('on a video the image keys and Space do nothing, as the toolbar disables them; a focused video keeps its keys', async ({ page, request }) => {
    await expect(await request.post('/__test__/reset', { data: { include_video: true } })).toBeOK();
    await page.setViewportSize({ width: 1600, height: 1100 });
    await page.goto('/prompt_manager/workspace#/images');
    await page.locator('article[data-image-key="0:clip.webm"] .image-open').click();
    const video = viewer(page).locator('video');
    await expect(video).toHaveJSProperty('controls', true);
    await expect(control(page, 'Zoom in')).toHaveAttribute('aria-disabled', 'true');
    const errors = [];page.on('pageerror', error => errors.push(error.message));
    await stage(page).focus();
    for (const key of ['+', '-', '0', 'r', 'h', 'Space']) await page.keyboard.press(key);
    await expect(playing(page)).toHaveAttribute('aria-label', 'Play slideshow');
    await expect(viewer(page).locator('.viewer-canvas')).toHaveCount(0);
    expect(errors).toEqual([]);
    await video.focus();
    await page.keyboard.press('f');
    await page.evaluate(() => new Promise(requestAnimationFrame));
    await expect(viewer(page)).not.toHaveClass(/\bviewer-fullscreen\b/);
});
