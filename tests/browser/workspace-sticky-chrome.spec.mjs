// #531 (D186-D189): the selection bars stay in view, a Back to top chip follows the page, and the side panels stay with
// you. Beyond the concept; the concept itself is unchanged.
import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';
import { startAndAwaitJob } from './job-end.mjs';

const IMAGES = '/prompt_manager/workspace#/images';
const LIBRARY = '/prompt_manager/workspace#/library';
const REVIEW = '/prompt_manager/workspace#/review';
const PHONE = { width: 390, height: 844 };
const imageCards = page => page.locator('#image-grid article[data-image-key]');
const promptCards = page => page.locator('article[data-prompt-id]');
const imageBar = page => page.locator('#bulkbar > .bulkbar');
const libraryBar = page => page.locator('#library-bulkbar > .bulkbar');
const chip = page => page.getByRole('button', { name: 'Back to top', exact: true });
const toast = page => page.locator('#workspace-toast');
const reviewPanel = page => page.getByRole('region', { name: 'Resolve the association', exact: true });
const reviewCards = page => page.locator('.review-queue article.media-card');

const box = locator => locator.evaluate(node => {
    const { top, bottom, left, right } = node.getBoundingClientRect();
    return { top, bottom, left, right };
});
const overlaps = (a, b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
/** Scroll the window to `y` and wait for the frame that applied it. */
async function scrollToY(page, y) {
    await page.evaluate(target => new Promise(resolve => {
        scrollTo({ top: target, behavior: 'instant' });
        requestAnimationFrame(() => requestAnimationFrame(resolve));
    }), y);
}
/** The first card wholly below `below` px, by its index in the list. */
async function cardBelow(cards, below) {
    const count = await cards.count();
    for (let index = 0; index < count; index++) {
        const card = cards.nth(index);
        if ((await box(card)).top <= below) continue;
        // By its key, not its position: the mounted window moves as the page does.
        const key = await card.evaluate(node => ['imageKey', 'promptId', 'reviewId'].map(name => [name, node.dataset[name]]).find(([, value]) => value));
        const attribute = key[0].replace(/[A-Z]/g, letter => `-${letter.toLowerCase()}`);
        return cards.page().locator(`article[data-${attribute}=${JSON.stringify(key[1])}]`);
    }
    throw new Error(`no card below ${below}`);
}
async function openImages(page, request, extra = 100) {
    await expect(await request.post('/__test__/reset', { data: { extra_images: extra } })).toBeOK();
    await page.goto(IMAGES);
    await expect(imageCards(page).first()).toBeVisible();
}
async function openLibrary(page, request, extra = 40) {
    await expect(await request.post('/__test__/reset', { data: { native_library: true, extra_prompts: extra } })).toBeOK();
    await page.goto(LIBRARY);
    await expect(promptCards(page).first()).toBeVisible();
}
async function openReview(page, request, items = 30) {
    await expect(await request.post('/__test__/review/reset', { data: { items } })).toBeOK();
    await page.goto(REVIEW);
    await expect(reviewPanel(page).getByRole('combobox', { name: 'Sampler context', exact: true })).toBeEnabled();
}
const inViewport = (rect, page) => rect.top >= 0 && rect.bottom <= page.viewportSize().height;

test.beforeEach(async ({ page, browserName }) => { await startCoverage(page, browserName); });
test.afterEach(async ({ page, browserName }) => { await saveCoverage(page, browserName); });

for (const viewport of [null, PHONE]) {
    const label = viewport ? ' at 390px' : '';
    test(`Images: selecting far down keeps the bar in view and the card in place${label}`, async ({ page, request }) => {
        if (viewport) await page.setViewportSize(viewport);
        await openImages(page, request);
        await scrollToY(page, 1500);
        const card = await cardBelow(imageCards(page), 300);
        const before = (await box(card)).top;
        await card.locator('.image-overlays input[type="checkbox"]').check();
        await expect(imageBar(page).locator('strong')).toHaveText('1 selected');
        expect(inViewport(await box(imageBar(page)), page)).toBe(true);
        expect(Math.abs((await box(card)).top - before)).toBeLessThanOrEqual(1);
        // Keep scrolling and selecting: the bar stays; Clear returns the card to its place too.
        await scrollToY(page, 1900);
        expect(inViewport(await box(imageBar(page)), page)).toBe(true);
        const later = await cardBelow(imageCards(page), 300);
        await later.locator('.image-overlays input[type="checkbox"]').check();
        await expect(imageBar(page).locator('strong')).toHaveText('2 selected');
        const held = (await box(later)).top;
        await imageBar(page).getByRole('button', { name: 'Clear', exact: true }).click();
        await expect(imageBar(page)).toHaveCount(0);
        expect(Math.abs((await box(later)).top - held)).toBeLessThanOrEqual(1);
    });

    test(`Library: selecting far down keeps the bar in view and the card in place${label}`, async ({ page, request }) => {
        if (viewport) await page.setViewportSize(viewport);
        await openLibrary(page, request);
        await scrollToY(page, 2000);
        await expect(promptCards(page).first()).toBeAttached();
        const card = await cardBelow(promptCards(page), 250);
        const before = (await box(card)).top;
        await card.locator('.image-overlays input[type="checkbox"]').check();
        await expect(libraryBar(page).locator('strong')).toHaveText('1 selected');
        expect(inViewport(await box(libraryBar(page)), page)).toBe(true);
        expect(Math.abs((await box(card)).top - before)).toBeLessThanOrEqual(1);
        expect(await card.isVisible()).toBe(true);
    });
}

test('the Images panel clears the stuck bar and a focused card is not left under it', async ({ page, request }) => {
    await page.setViewportSize({ width: 1400, height: 900 });
    await openImages(page, request);
    await scrollToY(page, 1500);
    await (await cardBelow(imageCards(page), 300)).locator('.image-overlays input[type="checkbox"]').check();
    await expect(imageBar(page)).toBeVisible();
    await expect.poll(async () => (await box(page.locator('#image-inspector'))).top).toBeGreaterThanOrEqual((await box(imageBar(page))).bottom);
    expect(overlaps(await box(page.locator('#image-inspector')), await box(imageBar(page)))).toBe(false);
    // A control scrolled to by focus lands below the bar; the bar's own controls do not move the page.
    const y = await page.evaluate(() => scrollY);
    await imageBar(page).getByRole('button', { name: 'Clear', exact: true }).focus();
    expect(await page.evaluate(() => scrollY)).toBe(y);
    const above = imageCards(page).first().locator('.image-overlays input[type="checkbox"]');
    await above.focus();
    expect((await box(above)).top).toBeGreaterThanOrEqual((await box(imageBar(page))).bottom);
});

test('Back to top shows off the top of a page, goes to the top and hands focus to the page', async ({ page, request }) => {
    await openImages(page, request);
    await expect(chip(page)).toBeHidden();
    await scrollToY(page, 1500);
    await expect(chip(page)).toBeVisible();
    const loaded = await page.evaluate(() => document.querySelector('#result-count').textContent);
    await chip(page).click();
    await expect.poll(() => page.evaluate(() => scrollY)).toBe(0);
    await expect(chip(page)).toBeHidden();
    await expect(page.locator('#workspace-main')).toBeFocused();
    expect(await page.evaluate(() => document.querySelector('#result-count').textContent)).toBe(loaded);
    await expect(imageCards(page).first()).toBeInViewport();
});

test('Back to top on the virtualized Library lands on the first card and stays there', async ({ page, request }) => {
    await openLibrary(page, request, 200);
    // Past 120 loaded prompts the shelf mounts only a window of them (each pass reaches the next page's trigger).
    await expect.poll(async () => {
        await page.evaluate(() => scrollTo({ top: document.documentElement.scrollHeight, behavior: 'instant' }));
        return page.locator('article[data-index="0"]').count();
    }, { timeout: 30000 }).toBe(0);
    await expect(chip(page)).toBeVisible();
    await chip(page).click();
    await expect.poll(() => page.evaluate(() => scrollY)).toBe(0);
    await expect.poll(async () => Number(await promptCards(page).first().getAttribute('data-index'))).toBe(0);
    await expect(promptCards(page).first()).toBeInViewport();
    expect(await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve(scrollY)))))).toBe(0);
});

test('Back to top jumps, under reduced motion and otherwise', async ({ page, request }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await openImages(page, request, 20);
    await scrollToY(page, 600);
    await expect(chip(page)).toBeVisible();
    const after = await chip(page).evaluate(node => { node.click(); return scrollY; });
    expect(after).toBe(0);
});

for (const viewport of [{ width: 1280, height: 720 }, PHONE]) {
    test(`Back to top moves clear of a toast and of the bar at ${viewport.width}px`, async ({ page, request }) => {
        await page.setViewportSize(viewport);
        await openImages(page, request);
        await scrollToY(page, 1500);
        await expect(chip(page)).toBeVisible();
        await (await cardBelow(imageCards(page), 200)).locator('.image-overlays input[type="checkbox"]').check();
        await expect(imageBar(page)).toBeVisible();
        expect(overlaps(await box(chip(page)), await box(imageBar(page)))).toBe(false);
        const alone = await box(chip(page));
        await page.evaluate(() => {
            document.getElementById('workspace-toast').textContent = 'A much longer message than most, one that keeps going, so that it wraps to more than two lines on the narrowest screen a phone will give this page to show it on, and then some more words again.';
        });
        await expect(toast(page)).not.toBeEmpty();
        await expect.poll(async () => overlaps(await box(chip(page)), await box(toast(page)))).toBe(false);
        expect((await box(chip(page))).bottom).toBeLessThanOrEqual((await box(toast(page))).top);
        await expect(chip(page)).toBeInViewport();
        await page.evaluate(() => { document.getElementById('workspace-toast').textContent = ''; });
        await expect.poll(async () => Math.round((await box(chip(page))).bottom)).toBe(Math.round(alone.bottom));
    });
}

test('a stuck bar at 390px is one row that keeps every action reachable', async ({ page, request }) => {
    await page.setViewportSize(PHONE);
    await openImages(page, request);
    await scrollToY(page, 1500);
    await (await cardBelow(imageCards(page), 200)).locator('.image-overlays input[type="checkbox"]').check();
    await expect(imageBar(page)).toBeVisible();
    expect((await box(imageBar(page))).bottom - (await box(imageBar(page))).top).toBeLessThan(80);
    const clear = imageBar(page).getByRole('button', { name: 'Clear', exact: true });
    await clear.scrollIntoViewIfNeeded();
    await expect(clear).toBeInViewport();
    await clear.click();
    await expect(imageBar(page)).toHaveCount(0);
});

test('the Needs review panel stays with you, and its long content scrolls inside it', async ({ page, request }) => {
    await page.setViewportSize({ width: 1400, height: 500 });
    await openReview(page, request);
    await scrollToY(page, 700);
    await expect(reviewPanel(page)).toBeInViewport();
    const rect = await box(reviewPanel(page));
    expect(rect.top).toBeGreaterThanOrEqual(0);
    expect(rect.bottom).toBeLessThanOrEqual(500);
    const card = await cardBelow(reviewCards(page), 100);
    await card.getByRole('button', { name: /^Inspect / }).click();
    const name = await card.locator('.media-info').first().textContent();
    expect(name).toBeTruthy();
    await expect(reviewPanel(page)).toBeInViewport();
    expect((await box(reviewPanel(page))).top).toBe(rect.top);
});

test('at 390px Info on a lower review card brings the panel into view', async ({ page, request }) => {
    await page.setViewportSize(PHONE);
    await openReview(page, request);
    await expect(reviewPanel(page)).not.toBeInViewport();
    const card = reviewCards(page).nth(3);
    await card.scrollIntoViewIfNeeded();
    await card.getByRole('button', { name: /^Inspect / }).click();
    await expect(reviewPanel(page)).toBeInViewport();
});

// D189: at one column the Images panel follows the whole list: Info goes there, and a batch doesn't push it away.
test('at 390px Info on an image brings its panel into view and loading waits for the next move', async ({ page, request }) => {
    await page.setViewportSize(PHONE);
    await openImages(page, request);
    expect(await page.evaluate(() => scrollY)).toBe(0);
    await expect(page.locator('#image-inspector')).not.toBeInViewport();
    let reads = 0;
    page.on('request', item => { if (new URL(item.url()).pathname.endsWith('/images/recent')) reads++; });
    const card = imageCards(page).nth(3);
    await card.scrollIntoViewIfNeeded();
    await card.getByRole('button', { name: /^Inspect / }).click();
    await expect(page.locator('#image-inspector')).toBeInViewport();
    await expect(page.locator('#viewer-dialog')).toBeHidden();
    const held = (await box(page.locator('#image-inspector'))).top;
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    expect((await box(page.locator('#image-inspector'))).top).toBe(held);
    expect(reads).toBe(0);
    // The next move releases it: loading resumes.
    const next = page.waitForRequest(item => new URL(item.url()).pathname.includes('/images'));
    await page.mouse.wheel(0, 40);
    await next;
});

// Review's review-state load more is a button, so Library's anchor stays honest through a compensated bar.
test('a Library selection does not jump after a pending height correction', async ({ page, request }) => {
    await page.setViewportSize({ width: 1000, height: 800 });
    await openLibrary(page, request);
    await scrollToY(page, 3000);
    const card = await cardBelow(promptCards(page), 250);
    await card.locator('.image-overlays input[type="checkbox"]').check();
    await expect(libraryBar(page)).toBeVisible();
    const before = (await box(card)).top;
    await page.setViewportSize({ width: 1300, height: 800 });
    await expect(card).toBeAttached();
    await page.setViewportSize({ width: 1000, height: 800 });
    await expect.poll(async () => Math.abs((await box(card)).top - before)).toBeLessThanOrEqual(1);
});

// #531: after a bulk action on a bar, the page shows the refreshed list from the top.
async function selectFarDownImages(page, request) {
    await openImages(page, request);
    await scrollToY(page, 1500);
    await (await cardBelow(imageCards(page), 200)).locator('.image-overlays input[type="checkbox"]').check();
    await expect(imageBar(page)).toBeVisible();
}
const atTop = page => expect.poll(() => page.evaluate(() => scrollY)).toBe(0);
const applyDialog = async (page, title, value) => {
    const dialog = page.getByRole('dialog', { name: title, exact: true });
    await dialog.locator('#action-input').fill(value);
    await dialog.getByRole('button', { name: /^(Apply|Add)/ }).click();
    await expect(dialog).toHaveCount(0);
};
test('Images: Set category returns to the top of the refreshed list', async ({ page, request }) => {
    await selectFarDownImages(page, request);
    const read = page.waitForRequest(item => item.url().includes('/prompt_manager/images/'));
    await imageBar(page).getByRole('button', { name: 'Set category', exact: true }).click();
    await applyDialog(page, 'Categorize selected images', 'Studio');
    await read;
    await atTop(page);
    await expect(imageBar(page)).toHaveCount(0);
    await expect(imageCards(page).first()).toBeInViewport();
});
test('Images: Add tags returns to the top of the refreshed list when its job ends', async ({ page, request }) => {
    await selectFarDownImages(page, request);
    await imageBar(page).getByRole('button', { name: 'Add tags', exact: true }).click();
    await applyDialog(page, 'Tag selected images', 'sticky-tag');
    await atTop(page);
    await expect(imageBar(page)).toBeVisible();
});
test('Library: Set category and Add tags return to the top of the reloaded shelf', async ({ page, request }) => {
    await openLibrary(page, request);
    for (const [button, title, value] of [['Set category', 'Categorize selected prompts', 'Studio'], ['Add tags', 'Tag selected prompts', 'sticky']]) {
        await scrollToY(page, 2000);
        await (await cardBelow(promptCards(page), 250)).locator('.image-overlays input[type="checkbox"]').check();
        await libraryBar(page).getByRole('button', { name: button, exact: true }).click();
        await applyDialog(page, title, value);
        await atTop(page);
        await expect(libraryBar(page)).toHaveCount(0);
        await expect(promptCards(page).first()).toBeInViewport();
    }
});
test('Images: Delete… returns to the top of the reloaded list', async ({ page, request }) => {
    await selectFarDownImages(page, request);
    await imageBar(page).getByRole('button', { name: 'Delete…', exact: true }).click();
    const dialog = page.locator('dialog[open]');
    await dialog.getByRole('radio', { name: /^Remove from the library only/ }).check();
    await startAndAwaitJob(page, () => dialog.locator('.dialog-actions button.danger').click());
    await expect(toast(page)).toContainText('Removed 1 image from the library.');
    await atTop(page);
    await expect(page.locator('#bulkbar')).toBeEmpty();
});
test('Library: Delete… returns to the top of the reloaded shelf', async ({ page, request }) => {
    await openLibrary(page, request);
    await scrollToY(page, 2000);
    await (await cardBelow(promptCards(page), 250)).locator('.image-overlays input[type="checkbox"]').check();
    await libraryBar(page).getByRole('button', { name: 'Delete…', exact: true }).click();
    await page.locator('dialog[open] .dialog-actions button.danger').click();
    await expect(page.locator('dialog[open]')).toHaveCount(0);
    await atTop(page);
    await expect(page.locator('#library-bulkbar')).toBeEmpty();
});

