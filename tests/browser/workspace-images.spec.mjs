import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from '../../tools/vendor/node_modules/@playwright/test/index.mjs';

const workspace = '/prompt_manager/workspace#/images';
const cards = page => page.locator('article[data-image-key]');
const details = page => page.getByRole('region', { name: 'Image details', exact: true });

test.beforeEach(async ({ page, request, browserName }) => {
    await startCoverage(page, browserName);
    await expect(await request.post('/__test__/reset')).toBeOK();
    page.imageErrors = [];
    page.on('pageerror', error => page.imageErrors.push(error.message));
});

test.afterEach(async ({ page, browserName }) => {
    await saveCoverage(page, browserName);
    expect(page.imageErrors).toEqual([]);
});

test('native Images keeps colliding filenames distinct and inspects the selected source', async ({ page, request }) => {
    const snapshot = await (await request.get('/prompt_manager/stats')).json();
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(2);
    expect(await cards(page).evaluateAll(elements => elements.map(element => element.dataset.imageKey)))
        .toEqual(['0:shared/same.png', '1:shared/same.png']);
    const originals = await cards(page).locator('img').evaluateAll(elements => elements.map(element => element.src));
    expect(new Set(originals).size).toBe(2);
    await cards(page).nth(1).getByRole('button', { name: 'Open same.png', exact: true }).click();
    await expect(details(page)).toContainText('same.png');
    await expect(details(page)).toContainText('shared/same.png');
    await expect(details(page)).toContainText(/input/i);
    await cards(page).first().getByRole('button', { name: 'Open same.png', exact: true }).click();
    await expect(details(page)).toContainText(/output/i);
    expect((await (await request.get('/prompt_manager/stats')).json()).stats).toEqual(snapshot.stats);
});

test('workspace defaults to the native Images collection without a hash route', async ({ page }) => {
    await page.goto('/prompt_manager/workspace');
    await expect(page.locator('#workspace-main h1')).toHaveText('Images');
    await expect(cards(page)).toHaveCount(2);
    await expect(page.getByRole('navigation', { name: 'Workspace', exact: true })
        .getByRole('link', { name: 'Images', exact: true })).toHaveAttribute('aria-current', 'page');
});

test('image selection is announced on the selected image button', async ({ page }) => {
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(2);
    const selected = cards(page).last().getByRole('button', { name: 'Open same.png', exact: true });
    await selected.click();
    await expect(selected).toHaveAttribute('aria-pressed', 'true');
    await expect(cards(page).first().getByRole('button', { name: 'Open same.png', exact: true }))
        .toHaveAttribute('aria-pressed', 'false');
});

test('closing details preview restores its trigger focus', async ({ page }) => {
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(2);
    const selected = cards(page).last().getByRole('button', { name: 'Open same.png', exact: true });
    await selected.click();
    const preview = details(page).getByRole('button', { name: 'Open full preview', exact: true });
    await preview.click();
    await expect(page.locator('dialog[open] .viewer-canvas img')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('dialog[open]')).toHaveCount(0);
    await expect(preview).toBeFocused();
});

test('a short visible batch automatically continues until the sentinel is filled or exhausted', async ({ page, request }) => {
    const real = await (await request.get('/prompt_manager/images/output?limit=40')).json();
    let reads = 0;
    await page.route('**/prompt_manager/images/output?*', async route => {
        const current = reads++;
        await route.fulfill({ json: {
            success: true, images: [real.images[current]], total: 2,
            has_more: current === 0, next_cursor: current === 0 ? 'next-short-batch' : null,
        } });
    });
    await page.setViewportSize({ width: 1440, height: 1200 });
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(2);
    expect(reads).toBe(2);
    await expect(page.getByRole('button', { name: 'Load more images', exact: true })).toBeHidden();
});

test('source, folder and media filters operate on real backend records', async ({ page, request }) => {
    await expect(await request.post('/__test__/reset', { data: { extra_images: 3 } })).toBeOK();
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(5);
    await page.getByRole('combobox', { name: 'Source', exact: true }).selectOption('1');
    await expect(cards(page)).toHaveCount(1);
    await expect(cards(page).first()).toHaveAttribute('data-image-key', '1:shared/same.png');
    await page.getByRole('combobox', { name: 'Source', exact: true }).selectOption('all');
    await page.getByRole('combobox', { name: 'Folder', exact: true }).selectOption('bulk');
    await expect(cards(page)).toHaveCount(3);
    await expect(cards(page).first()).toHaveAttribute('data-image-key', /^0:bulk\//);
    await page.getByRole('combobox', { name: 'Media', exact: true }).selectOption('video');
    await expect(cards(page)).toHaveCount(0);
    await expect(page.locator('#workspace-main')).toContainText(/no (matching )?(images|media)/i);
    await page.getByRole('combobox', { name: 'Media', exact: true }).selectOption('image');
    await expect(cards(page)).toHaveCount(3);
    await page.getByRole('combobox', { name: 'Folder', exact: true }).selectOption('');
    await expect(cards(page)).toHaveCount(5);
});

test('filename search finds records beyond the initial batch and sort orders them', async ({ page, request }) => {
    await expect(await request.post('/__test__/reset', { data: { extra_images: 100 } })).toBeOK();
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(40);
    await page.getByRole('searchbox', { name: 'Search filenames', exact: true }).fill('same.png');
    await expect(cards(page)).toHaveCount(2);
    await page.getByRole('searchbox', { name: 'Search filenames', exact: true }).fill('0099');
    await expect(cards(page)).toHaveCount(1);
    await expect(cards(page)).toHaveAttribute('data-image-key', '0:bulk/0099.png');
    await page.getByRole('searchbox', { name: 'Search filenames', exact: true }).fill('same.png');
    await expect(cards(page)).toHaveCount(2);
    await page.getByRole('combobox', { name: 'Sort', exact: true }).selectOption('oldest');
    await expect(cards(page).first()).toHaveAttribute('data-image-key', '1:shared/same.png');
    await page.getByRole('combobox', { name: 'Sort', exact: true }).selectOption('newest');
    await expect(cards(page).first()).toHaveAttribute('data-image-key', '0:shared/same.png');
});

test('layout changes retain selection and use one collection in Grid, List and Masonry', async ({ page }) => {
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(2);
    await cards(page).nth(1).getByRole('button', { name: 'Open same.png', exact: true }).click();
    for (const layout of ['List', 'Masonry', 'Grid']) {
        await page.getByRole('button', { name: layout, exact: true }).click();
        await expect(page.getByRole('button', { name: layout, exact: true })).toHaveAttribute('aria-pressed', 'true');
        await expect(cards(page)).toHaveCount(2);
        await expect(details(page)).toContainText(/input/i);
        const boxes = await cards(page).evaluateAll(elements => elements.map(element => {
            const box = element.getBoundingClientRect();
            return { x: box.x, y: box.y, right: box.right, bottom: box.bottom };
        }));
        expect(boxes[0].right <= boxes[1].x || boxes[1].right <= boxes[0].x ||
            boxes[0].bottom <= boxes[1].y || boxes[1].bottom <= boxes[0].y).toBe(true);
    }
});

test('returning to Images restores the filtered stream, layout, selection and scroll context', async ({ page, request }) => {
    await page.addInitScript(() => { window.IntersectionObserver = undefined; });
    await expect(await request.post('/__test__/reset', { data: { extra_images: 100 } })).toBeOK();
    await page.goto(workspace);
    const search = page.getByRole('searchbox', { name: 'Search filenames', exact: true });
    await page.getByRole('combobox', { name: 'Source', exact: true }).selectOption('0');
    await search.fill('00');
    await expect(page.locator('.image-toolbar')).toContainText('40 of 100 images');
    await page.getByRole('button', { name: 'List', exact: true }).click();
    await page.getByRole('button', { name: 'Load more images', exact: true }).click();
    await expect(page.locator('.image-toolbar')).toContainText('80 of 100 images');
    await cards(page).first().getByRole('button', { name: /^Open / }).click();
    const selectedTitle = await details(page).getByRole('heading', { level: 2 }).textContent();
    await cards(page).nth(30).scrollIntoViewIfNeeded();
    const previousScroll = await page.evaluate(() => window.scrollY);
    expect(previousScroll).toBeGreaterThan(500);
    // Hash navigation preserves the scroll position being tested; clicking the fixed
    // navigation can otherwise scroll a non-sticky mobile sidebar into view first.
    await page.evaluate(() => { window.location.hash = '/overview'; });
    await expect(page.locator('#workspace-main h1')).toHaveText('Overview');
    await page.evaluate(() => { window.location.hash = '/images'; });
    await expect(page.locator('#workspace-main h1')).toHaveText('Images');
    await expect(search).toHaveValue('00');
    await expect(page.getByRole('combobox', { name: 'Source', exact: true })).toHaveValue('0');
    await expect(page.getByRole('button', { name: 'List', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('.image-toolbar')).toContainText('80 of 100 images');
    await expect(cards(page)).toHaveCount(80);
    await expect(details(page).getByRole('heading', { level: 2 })).toHaveText(selectedTitle);
    await expect.poll(async () => Math.abs((await page.evaluate(() => window.scrollY)) - previousScroll)).toBeLessThan(120);
});

test('continuous loading appends without replacing cards or losing selected details', async ({ page, request }) => {
    // Exercise the keyboard fallback independently from IntersectionObserver scrolling.
    await page.addInitScript(() => { window.IntersectionObserver = undefined; });
    await expect(await request.post('/__test__/reset', { data: { extra_images: 100 } })).toBeOK();
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(40);
    await cards(page).first().evaluate(element => { element.dataset.preserved = 'yes'; });
    await cards(page).first().getByRole('button', { name: /^Open / }).click();
    const selected = await cards(page).first().getAttribute('data-image-key');
    const more = page.getByRole('button', { name: 'Load more images', exact: true });
    await more.focus();
    await page.keyboard.press('Enter');
    await expect(cards(page)).toHaveCount(80);
    await expect(cards(page).first()).toHaveAttribute('data-preserved', 'yes');
    await expect(cards(page).first()).toHaveAttribute('data-image-key', selected);
    await expect(details(page)).toBeVisible();
    await expect(more).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(cards(page)).toHaveCount(102);
    expect(new Set(await cards(page).evaluateAll(elements => elements.map(element => element.dataset.imageKey))).size).toBe(102);
    await expect(more).toBeHidden();
    await expect(page.getByRole('button', { name: /next page|previous page/i })).toHaveCount(0);
});

test('scrolling to the sentinel automatically appends the next backend batch', async ({ page, request }) => {
    await expect(await request.post('/__test__/reset', { data: { extra_images: 100 } })).toBeOK();
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(40);
    const first = cards(page).first();
    await first.evaluate(element => { element.dataset.preserved = 'scroll'; });
    await page.getByRole('button', { name: 'Load more images', exact: true }).scrollIntoViewIfNeeded();
    await expect(cards(page)).toHaveCount(80);
    await expect(first).toHaveAttribute('data-preserved', 'scroll');
});

test('large continuous collections bound the mounted cards and restore earlier images on backscroll', async ({ page, request }) => {
    await page.addInitScript(() => { window.IntersectionObserver = undefined; });
    await expect(await request.post('/__test__/reset', { data: { extra_images: 200 } })).toBeOK();
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(40);
    const first = cards(page).first();
    const firstKey = await first.getAttribute('data-image-key');
    await first.getByRole('button', { name: /^Open / }).click();
    const selectedTitle = await details(page).getByRole('heading', { level: 2 }).textContent();
    const more = page.getByRole('button', { name: 'Load more images', exact: true });
    for (const loaded of [80, 120, 160, 200, 202]) {
        await more.click();
        await expect(page.locator('.image-toolbar')).toContainText(`${loaded} of 202 images`);
    }
    await expect(more).toBeHidden();
    await expect.poll(() => cards(page).count()).toBeLessThan(120);
    await expect(details(page).getByRole('heading', { level: 2 })).toHaveText(selectedTitle);
    expect(new Set(await cards(page).evaluateAll(elements => elements.map(element => element.dataset.imageKey))).size)
        .toBe(await cards(page).count());
    await page.evaluate(() => window.scrollTo(0, 0));
    await expect(page.locator(`article[data-image-key="${firstKey}"]`)).toBeVisible();
    await expect.poll(() => cards(page).count()).toBeLessThan(120);
    await expect(details(page).getByRole('heading', { level: 2 })).toHaveText(selectedTitle);
    await page.locator(`article[data-image-key="${firstKey}"]`)
        .getByRole('button', { name: /^Preview / }).click();
    await expect(page.locator('dialog[open] .viewer-canvas img')).toBeVisible();
    await page.getByRole('button', { name: 'Close preview', exact: true }).click();
});

test('virtual gallery keyboard navigation reaches loaded endpoints and announces collection positions', async ({ page, request }) => {
    await page.addInitScript(() => { window.IntersectionObserver = undefined; });
    await expect(await request.post('/__test__/reset', { data: { extra_images: 200 } })).toBeOK();
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(40);
    const firstKey = await cards(page).first().getAttribute('data-image-key');
    const firstName = await cards(page).first().getByRole('button', { name: /^Open / }).getAttribute('aria-label');
    await cards(page).first().getByRole('button', { name: /^Open / }).click();
    const selectedTitle = await details(page).getByRole('heading', { level: 2 }).textContent();
    const more = page.getByRole('button', { name: 'Load more images', exact: true });
    for (const loaded of [80, 120, 160, 200, 202]) {
        await more.click();
        await expect(page.locator('.image-toolbar')).toContainText(`${loaded} of 202 images`);
    }
    await page.evaluate(() => window.scrollTo(0, 0));
    const first = page.locator(`article[data-image-key="${firstKey}"]`);
    await expect(first).toBeVisible();
    await first.getByRole('button', { name: firstName, exact: true }).focus();
    await page.keyboard.press('End');
    await expect(page.locator('article[data-image-key="1:shared/same.png"]')
        .getByRole('button', { name: 'Open same.png', exact: true })).toBeFocused();
    await expect(page.locator('article[data-image-key="1:shared/same.png"]')).toHaveAttribute('aria-posinset', '202');
    await expect(page.locator('article[data-image-key="1:shared/same.png"]')).toHaveAttribute('aria-setsize', '202');
    await expect(page.getByRole('list', { name: 'Image collection', exact: true })).toBeVisible();
    await expect(page.locator('article[data-image-key="1:shared/same.png"]')).toHaveAttribute('role', 'listitem');
    await page.keyboard.press('Home');
    await expect(first.getByRole('button', { name: firstName, exact: true })).toBeFocused();
    await expect(first).toHaveAttribute('aria-posinset', '1');
    await page.keyboard.press('ArrowRight');
    await expect(page.locator('article[aria-posinset="2"]').getByRole('button', { name: /^Open / })).toBeFocused();
    await expect(details(page).getByRole('heading', { level: 2 })).toHaveText(selectedTitle);
    await expect.poll(() => cards(page).count()).toBeLessThan(120);
});

test('preview uses actual Viewer.js and cleans up when closing or leaving Images', async ({ page }) => {
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(2);
    await cards(page).last().getByRole('button', { name: 'Preview same.png', exact: true }).click();
    await expect(page.locator('dialog[open] .viewer-canvas img')).toBeVisible();
    await expect(page.locator('dialog[open] .viewer-canvas img')).toHaveAttribute('src', /root=1/);
    await page.getByRole('button', { name: 'Close preview', exact: true }).click();
    await expect(page.locator('dialog[open]')).toHaveCount(0);
    await cards(page).first().getByRole('button', { name: 'Preview same.png', exact: true }).click();
    await expect(page.locator('dialog[open] .viewer-canvas img')).toBeVisible();
    await page.evaluate(() => { window.location.hash = '/overview'; });
    await expect(page.locator('#workspace-main h1')).toHaveText('Overview');
    await expect(page.locator('.viewer-container')).toHaveCount(0);
    await expect(page.locator('dialog[open]')).toHaveCount(0);
});

test('real video media has a usable card and native playback cleans up on close and navigation', async ({ page, request }) => {
    await expect(await request.post('/__test__/reset', { data: { include_video: true } })).toBeOK();
    const media = await (await request.get('/prompt_manager/images/output?limit=40')).json();
    expect(media.images.find(image => image.filename === 'clip.webm').thumbnail_url).toBeNull();
    await page.goto(workspace);
    await page.getByRole('combobox', { name: 'Media', exact: true }).selectOption('video');
    await expect(cards(page)).toHaveCount(1);
    const card = cards(page).first();
    await expect(card).toHaveAttribute('data-image-key', '0:clip.webm');
    await expect(card.locator('img')).toHaveCount(0);
    await expect(card.getByRole('button', { name: 'Open clip.webm', exact: true })).toBeVisible();
    await card.getByRole('button', { name: 'Preview clip.webm', exact: true }).click();
    const video = page.locator('dialog[open] video');
    await expect(video).toBeVisible();
    await expect(video).toHaveJSProperty('controls', true);
    await expect.poll(() => video.evaluate(element => element.readyState)).toBeGreaterThanOrEqual(1);
    const playing = await video.evaluate(async element => {
        window.__previewVideo = element;
        element.muted = true;
        await element.play();
        return !element.paused;
    });
    expect(playing).toBe(true);
    await page.getByRole('button', { name: 'Close preview', exact: true }).click();
    await expect(page.locator('dialog video')).toHaveCount(0);
    expect(await page.evaluate(() => window.__previewVideo.paused && !window.__previewVideo.isConnected)).toBe(true);
    await card.getByRole('button', { name: 'Preview clip.webm', exact: true }).click();
    await expect(video).toBeVisible();
    await video.evaluate(async element => {
        window.__previewVideo = element;
        element.muted = true;
        await element.play();
    });
    await page.evaluate(() => { window.location.hash = '/overview'; });
    await expect(page.locator('#workspace-main h1')).toHaveText('Overview');
    await expect(page.locator('video')).toHaveCount(0);
    expect(await page.evaluate(() => window.__previewVideo.paused && !window.__previewVideo.isConnected)).toBe(true);
});

test('failed image requests offer retry while stale results cannot replace a newer route', async ({ page }) => {
    await page.route('**/prompt_manager/images/output?*', route => route.fulfill({
        status: 503, json: { success: false, error: 'Images unavailable' },
    }), { times: 1 });
    await page.goto(workspace);
    await expect(page.locator('#workspace-main').getByRole('alert')).toBeVisible();
    await page.getByRole('button', { name: 'Retry', exact: true }).click();
    await expect(cards(page)).toHaveCount(2);
    let release;
    const pending = new Promise(resolve => { release = resolve; });
    let arrived;
    const received = new Promise(resolve => { arrived = resolve; });
    await page.route('**/prompt_manager/images/output?*', async route => {
        const response = await route.fetch();
        arrived();
        await pending;
        await route.fulfill({ response }).catch(() => {});
    });
    await page.getByRole('searchbox', { name: 'Search filenames', exact: true }).fill('nothing');
    await received;
    await page.getByRole('navigation', { name: 'Workspace', exact: true })
        .getByRole('link', { name: 'Overview', exact: true }).click();
    release();
    await expect(page.locator('#workspace-main h1')).toHaveText('Overview');
    await expect(cards(page)).toHaveCount(0);
    await expect(details(page)).toHaveCount(0);
});

test('source and folder discovery failure stays visible and Refresh restores usable filters', async ({ page }) => {
    await page.route('**/prompt_manager/gallery/subfolders?*', route => route.fulfill({
        status: 503, json: { success: false, error: 'Sources unavailable' },
    }), { times: 1 });
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(2);
    await expect(page.getByRole('alert')).toHaveText('Sources and folders could not be loaded. Refresh to retry.');
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    await expect(page.getByRole('alert')).toHaveCount(0);
    await page.getByRole('combobox', { name: 'Source', exact: true }).selectOption('1');
    await expect(cards(page)).toHaveCount(1);
    await expect(cards(page)).toHaveAttribute('data-image-key', '1:shared/same.png');
    await page.getByRole('combobox', { name: 'Folder', exact: true }).selectOption('shared');
    await expect(cards(page)).toHaveCount(1);
});

test('mobile Images keeps filters, layouts and selected details within the viewport', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(2);
    for (const layout of ['List', 'Masonry', 'Grid']) {
        await page.getByRole('button', { name: layout, exact: true }).click();
        await cards(page).last().getByRole('button', { name: 'Open same.png', exact: true }).click();
        await expect(details(page)).toContainText(/input/i);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    }
    await expect(page.getByRole('searchbox', { name: 'Search filenames', exact: true })).toBeVisible();
});

test('a delayed search cannot replace results for a newer filename query', async ({ page, request }) => {
    await expect(await request.post('/__test__/reset', { data: { extra_images: 3 } })).toBeOK();
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(5);
    let release;
    let arrived;
    let complete;
    const pending = new Promise(resolve => { release = resolve; });
    const received = new Promise(resolve => { arrived = resolve; });
    const completed = new Promise(resolve => { complete = resolve; });
    let delayed = false;
    await page.route('**/prompt_manager/images/output?*', async route => {
        if (delayed) return route.continue();
        delayed = true;
        const response = await route.fetch();
        arrived();
        await pending;
        await route.fulfill({ response }).catch(() => {});
        complete();
    });
    const search = page.getByRole('searchbox', { name: 'Search filenames', exact: true });
    await search.fill('same.png');
    await received;
    await search.fill('0001');
    await expect(cards(page)).toHaveCount(1);
    await expect(cards(page)).toHaveAttribute('data-image-key', '0:bulk/0001.png');
    release();
    await completed;
    await expect(cards(page)).toHaveCount(1);
    await expect(cards(page)).toHaveAttribute('data-image-key', '0:bulk/0001.png');
});

test('selected layout stays blue with readable text on hover in either theme', async ({ page }) => {
    await page.goto(workspace);
    const layout = page.getByRole('group', { name: 'Image layout' }).getByRole('button', { name: 'Masonry', exact: true });
    for (const theme of ['light', 'dark']) {
        await page.getByRole('combobox', { name: 'Appearance', exact: true }).selectOption(theme);
        await layout.click();
        await layout.hover();
        await expect(layout).toHaveCSS('background-color', 'rgb(8, 119, 187)');
        await expect(layout).toHaveCSS('color', 'rgb(255, 255, 255)');
    }
});

test('refresh releases the previous inspector video source', async ({ page, request }) => {
    await expect(await request.post('/__test__/reset', { data: { include_video: true } })).toBeOK();
    await page.goto(workspace);
    const card = cards(page).filter({ hasText: 'clip.webm' });
    await card.getByRole('button', { name: 'Open clip.webm', exact: true }).click();
    await expect(details(page).locator('video')).toBeVisible();
    await details(page).locator('video').evaluate(element => { window.__oldInspectorVideo = element; });
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    await expect.poll(() => page.evaluate(() => !window.__oldInspectorVideo.isConnected)).toBe(true);
    expect(await page.evaluate(() => window.__oldInspectorVideo.getAttribute('src'))).toBeNull();
});

test('Refresh rescans cached files and preserves the collection when rescan fails', async ({ page, request }) => {
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(2);
    await expect(await request.post('/__test__/add-nested-image')).toBeOK();
    await page.route('**/prompt_manager/gallery/rescan', route => route.fulfill({
        status: 503, json: { success: false, error: 'Rescan unavailable' },
    }), { times: 1 });
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    await expect(page.getByRole('alert')).toContainText('could not be refreshed');
    await expect(cards(page)).toHaveCount(2);
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    await expect(cards(page)).toHaveCount(3);
    await expect(cards(page).filter({ hasText: 'new-output.png' })).toHaveCount(1);
    await expect(page.getByRole('alert')).toBeHidden();
});

test('folder discovery offers ancestors that filter nested images', async ({ page, request }) => {
    await expect(await request.post('/__test__/add-nested-image')).toBeOK();
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(3);
    const folder = page.getByRole('combobox', { name: 'Folder', exact: true });
    await expect(folder.locator('option[value="project"]')).toHaveCount(1);
    await folder.selectOption('project');
    await expect(cards(page)).toHaveCount(1);
    await expect(cards(page)).toContainText('new-output.png');
});

test('leaving during Refresh cancels it without replacing the next workspace', async ({ page }) => {
    let release;
    const pending = new Promise(resolve => { release = resolve; });
    await page.route('**/prompt_manager/gallery/rescan', async route => {
        await pending;
        await route.fulfill({ json: { success: true } }).catch(() => {});
    });
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(2);
    const requested = page.waitForRequest('**/prompt_manager/gallery/rescan');
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    await requested;
    await page.getByRole('navigation', { name: 'Workspace', exact: true })
        .getByRole('link', { name: 'Overview', exact: true }).click();
    await expect(page.locator('#workspace-main h1')).toHaveText('Overview');
    release();
    await expect(page.getByRole('heading', { name: 'Continue creating', exact: true })).toBeVisible();
    await expect(page.getByText('Images could not be refreshed. Try Refresh again.', { exact: true })).toHaveCount(0);
});
