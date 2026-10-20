import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from '../../tools/vendor/node_modules/@playwright/test/index.mjs';

const workspace = '/prompt_manager/workspace#/images';
const cards = page => page.locator('article[data-image-key]');
// Grid pages only: the sidebar's Images count is its own `limit=1` read of the same listing.
const imagePages = url => url.pathname === '/prompt_manager/images/output' && url.search !== '' && url.searchParams.get('limit') !== '1';
const details = page => page.getByRole('region', { name: 'Image details', exact: true });
// The concept toolbar, filter row and caption (#249 C2); Source, Folder, Media and Tags live in "Refine this view".
const search = page => page.getByRole('searchbox', { name: 'Search workspace', exact: true });
const sortBy = page => page.getByRole('combobox', { name: 'Sort results', exact: true });
const caption = page => page.locator('.section-caption');
const refineDialog = page => page.getByRole('dialog', { name: 'Refine this view', exact: true });
const importDialog = page => page.getByRole('dialog', { name: 'Import & inspect', exact: true });
async function refine(page, values) {
    await page.getByRole('button', { name: 'Filters', exact: true }).click();
    const dialog = refineDialog(page);
    for (const [label, value] of Object.entries(values)) {
        const control = dialog.getByLabel(label, { exact: true });
        if (label === 'Tags') await control.fill(value);
        else await control.selectOption(value);
    }
    await dialog.getByRole('button', { name: 'Apply filters', exact: true }).click();
    await expect(dialog).toBeHidden();
}
async function rescan(page) {
    await page.getByRole('button', { name: 'Import', exact: true }).click();
    await importDialog(page).getByRole('button', { name: 'Rescan', exact: true }).click();
}

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

test('native Images keeps colliding filenames distinct and inspects the selected source', async ({ page, request }, testInfo) => {
    await page.setViewportSize({width:1600,height:1100});
    async function capture(name) {
        await page.evaluate(async()=>{await document.fonts.ready;await Promise.all([...document.images].map(image=>image.decode().catch(()=>{})));});
        const path=testInfo.outputPath(`${name}.png`);await page.screenshot({path});
        await testInfo.attach(name,{path,contentType:'image/png'});
    }
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
    await capture('after-images-source-collision-input-1600');
    await cards(page).first().getByRole('button', { name: 'Open same.png', exact: true }).click();
    await expect(details(page)).toContainText(/output/i);
    await capture('after-images-source-collision-output-1600');
    await page.setViewportSize({width:390,height:844});
    for (const [index,source] of [[1,'input'],[0,'output']]) {
        await cards(page).nth(index).getByRole('button',{name:'Open same.png',exact:true}).click();
        await expect(details(page)).toContainText(new RegExp(source,'i'));
        await details(page).scrollIntoViewIfNeeded();
        await capture(`after-images-source-collision-${source}-390`);
    }
    expect((await (await request.get('/prompt_manager/stats')).json()).stats).toEqual(snapshot.stats);
});

test('workspace defaults to the native Images collection without a hash route', async ({ page }) => {
    await page.goto('/prompt_manager/workspace');
    await expect(page.locator('#workspace-main h1')).toHaveText('Images');
    await expect(cards(page)).toHaveCount(2);
    await expect(page.getByRole('navigation', { name: 'Workspace', exact: true })
        .getByRole('link', { name: /^Images(?:\s*[\d,]+)?$/ })).toHaveAttribute('aria-current', 'page');
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
    await page.route(imagePages, async route => {
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

for (const embedded of [false, true]) {
    for (const close of ['Close', 'Escape', 'parent abort']) {
        test(`viewer closure resumes native continuation after a short pending page: ${embedded ? 'embedded' : 'Images'} ${close}`, async ({ page, request }, testInfo) => {
            await expect(await request.post('/__test__/reset', { data: { extra_images: 80 } })).toBeOK();
            const data = await (await request.get('/prompt_manager/images/output?limit=200')).json();
            await page.setViewportSize({ width: 1600, height: 1100 });
            await page.route('**/prompt_manager/workspace', async route => {
                const response = await route.fetch();
                const html = (await response.text()).replace('<script type="module" src="js/workspace/index.mjs"></script>', '');
                await route.fulfill({ response, body: html });
            });
            await page.goto('/prompt_manager/workspace');
            await page.evaluate(async ({ images, embedded }) => {
                const { mountImages } = await import('/prompt_manager/js/workspace/images.mjs');
                const { createScope } = await import('/prompt_manager/js/shared/toolkit/core.js');
                window.ApiClient = { resolveUrl: value => value };
                const fixture = window.continuationFixture = { reads: [], scope: createScope() };
                const api = { get: async (url, options) => (await fetch(url, options)).json() };
                const fetchPage = ({ parameters }) => {
                    fixture.reads.push(parameters.get('cursor'));
                    if (fixture.reads.length === 1) return { images: images.slice(0, 40), total: 80, has_more: true, next_cursor: 'next-0' };
                    if (fixture.reads.length === 2) return new Promise(resolve => {
                        fixture.finishPage = () => resolve({ images: [], total: 80, has_more: true, next_cursor: 'next-1' });
                    });
                    return { images: images.slice(40, 80), total: 80, has_more: false, next_cursor: null };
                };
                mountImages(document.querySelector('#workspace-main'), api, fixture.scope, { embedded, fetchPage });
            }, { images: data.images, embedded });
            await expect(cards(page)).toHaveCount(40);
            expect(await page.evaluate(() => window.continuationFixture.reads)).toEqual([null]);
            await page.locator('.infinite-continuation').scrollIntoViewIfNeeded();
            await expect.poll(() => page.evaluate(() => window.continuationFixture.reads)).toEqual([null, 'next-0']);
            const key = await cards(page).last().getAttribute('data-image-key');
            const preview = page.locator(`article[data-image-key="${key}"]`).getByRole('button', { name: /^Preview / });
            await preview.click();
            const viewer = page.getByRole('dialog', { name: 'Image preview', exact: true });
            await expect(viewer.locator('.viewer-canvas img')).toBeVisible();
            await page.evaluate(() => window.continuationFixture.finishPage());
            await expect(page.getByRole('button', { name: 'Load more images', exact: true })).toHaveAttribute('aria-disabled', 'false');
            await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
            expect(await page.evaluate(() => ({ reads: window.continuationFixture.reads, nativeObserver: typeof IntersectionObserver, visible: document.querySelector('.infinite-continuation').getBoundingClientRect().top <= innerHeight })))
                .toEqual({ reads: [null, 'next-0'], nativeObserver: 'function', visible: true });
            if (close === 'parent abort') await page.evaluate(() => window.continuationFixture.scope.dispose());
            else if (close === 'Close') await viewer.getByRole('button', { name: 'Close preview', exact: true }).click();
            else await page.keyboard.press('Escape');
            await expect(viewer).toHaveCount(0);
            if (close !== 'parent abort') {
                await expect(preview).toBeFocused();
                await expect.poll(() => page.evaluate(() => window.continuationFixture.reads)).toEqual([null, 'next-0', 'next-1']);
                await expect(caption(page)).toContainText(embedded ? '80 loaded' : '80 of 80');
            }
            await page.evaluate(() => window.continuationFixture.scope.dispose());
            await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
            const reads = await page.evaluate(() => window.continuationFixture.reads);
            expect(reads).toEqual(close === 'parent abort' ? [null, 'next-0'] : [null, 'next-0', 'next-1']);
            await testInfo.attach('native-continuation-reads', { body: JSON.stringify({ embedded, close, reads }), contentType: 'application/json' });
        });
    }
}

test('source, folder and media filters operate on real backend records', async ({ page, request }) => {
    await expect(await request.post('/__test__/reset', { data: { extra_images: 3 } })).toBeOK();
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(5);
    await refine(page, { Source: '1' });
    await expect(cards(page)).toHaveCount(1);
    await expect(cards(page).first()).toHaveAttribute('data-image-key', '1:shared/same.png');
    await refine(page, { Source: 'all', Folder: 'bulk' });
    await expect(cards(page)).toHaveCount(3);
    await expect(cards(page).first()).toHaveAttribute('data-image-key', /^0:bulk\//);
    await refine(page, { Media: 'video' });
    await expect(cards(page)).toHaveCount(0);
    await expect(page.locator('#workspace-main')).toContainText(/no (matching )?(images|media)/i);
    await refine(page, { Media: 'image' });
    await expect(cards(page)).toHaveCount(3);
    await refine(page, { Folder: '' });
    await expect(cards(page)).toHaveCount(5);
});

test('filename search finds records beyond the initial batch and sort orders them', async ({ page, request }) => {
    await expect(await request.post('/__test__/reset', { data: { extra_images: 100 } })).toBeOK();
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(40);
    await search(page).fill('same.png');
    await expect(cards(page)).toHaveCount(2);
    await search(page).fill('0099');
    await expect(cards(page)).toHaveCount(1);
    await expect(cards(page)).toHaveAttribute('data-image-key', '0:bulk/0099.png');
    await search(page).fill('same.png');
    await expect(cards(page)).toHaveCount(2);
    await sortBy(page).selectOption('oldest');
    await expect(cards(page).first()).toHaveAttribute('data-image-key', '1:shared/same.png');
    await sortBy(page).selectOption('newest');
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
    const box = search(page);
    await refine(page, { Source: '0' });
    await box.fill('00');
    await expect(caption(page)).toContainText('40 of 100 images');
    await page.getByRole('button', { name: 'List', exact: true }).click();
    await page.getByRole('button', { name: 'Load more images', exact: true }).click();
    await expect(caption(page)).toContainText('80 of 100 images');
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
    await expect(box).toHaveValue('00');
    await expect(page.locator('.filter-row span.chip')).toHaveText(/ · All folders$/);
    await expect(page.getByRole('button', { name: 'List', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await expect(caption(page)).toContainText('80 of 100 images');
    await expect(cards(page)).toHaveCount(80);
    await expect(details(page).getByRole('heading', { level: 2 })).toHaveText(selectedTitle);
    await expect.poll(async () => Math.abs((await page.evaluate(() => window.scrollY)) - previousScroll)).toBeLessThan(120);
    // Checked last: closing the dialog focuses Filters, which scrolls the toolbar into view.
    await page.getByRole('button', { name: 'Filters', exact: true }).click();
    await expect(refineDialog(page).getByLabel('Source', { exact: true })).toHaveValue('0');
    await page.keyboard.press('Escape');
    await expect(refineDialog(page)).toBeHidden();
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
    // The concept (app.js loadMoreImages) focuses the first appended image after a Load more button press.
    await expect(page.locator('article[aria-posinset="41"] .image-open')).toBeFocused();
    await more.focus();
    await page.keyboard.press('Enter');
    await expect(cards(page)).toHaveCount(102);
    expect(new Set(await cards(page).evaluateAll(elements => elements.map(element => element.dataset.imageKey))).size).toBe(102);
    await expect(more).toBeHidden();
    await expect(page.locator('article[aria-posinset="81"] .image-open')).toBeFocused();
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
        await expect(caption(page)).toContainText(`${loaded} of 202 images`);
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
        await expect(caption(page)).toContainText(`${loaded} of 202 images`);
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
    await refine(page, { Media: 'video' });
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

test('collection viewer ignores delayed media callbacks and keeps inspection retries independent',async({page})=>{
    await page.goto(workspace);await expect(cards(page)).toHaveCount(2);
    await page.evaluate(()=>{const Native=window.Viewer;window.liveViewers=[];window.Viewer=class extends Native {constructor(...args){super(...args);window.liveViewers.push(this);}};});
    let release,held;const waiting=new Promise(resolve=>{held=resolve;});const pending=new Promise(resolve=>{release=resolve;});
    await page.route('**/images/serve/shared/same.png?*',async route=>{
        const url=new URL(route.request().url());
        if(url.searchParams.get('root')==='0'&&url.searchParams.has('_pm_viewer')) {held();await pending;}
        await route.continue().catch(()=>{});
    });
    await cards(page).first().getByRole('button',{name:'Preview same.png',exact:true}).click();await waiting;
    const preview=page.getByRole('dialog',{name:'Image preview',exact:true});
    await expect(preview.getByRole('button',{name:'Zoom in',exact:true})).toHaveAttribute('aria-disabled','true');
    await page.route('**/images/inspect/shared/same.png?*',route=>route.fulfill({status:503,json:{success:false,error:'Unavailable'}}),{times:1});
    await preview.getByRole('button',{name:'Next media',exact:true}).click();
    await expect(preview).toContainText('Could not read prompts from this file.');await expect(preview.getByRole('button',{name:'Zoom in',exact:true})).toHaveAttribute('aria-disabled','false');
    await expect(preview.getByRole('group',{name:'Media controls',exact:true}).getByRole('link',{name:'Download original',exact:true})).toHaveAttribute('href',/root=1&root_scope=/);
    await preview.getByRole('button',{name:'Rotate image 90 degrees',exact:true}).click();
    await page.evaluate(()=>{window.latestCanvas=document.querySelector('.viewer-canvas img');window.liveViewers[0].options.viewed();});release();
    expect(await page.evaluate(()=>window.liveViewers.at(-1).imageData.rotate)).toBe(90);
    await preview.getByRole('button',{name:'Retry prompt details',exact:true}).click();
    await expect(preview).toContainText('same.png');await expect(preview.getByRole('button',{name:'Retry prompt details',exact:true})).toHaveCount(0);
    expect(await page.evaluate(()=>window.latestCanvas===document.querySelector('.viewer-canvas img'))).toBe(true);
    await page.evaluate(()=>{location.hash='/overview';});await expect(page.locator('.viewer-container,.image-preview')).toHaveCount(0);
});

test('collection viewer describes video transforms and releases native playback on navigation',async({page,request})=>{
    await expect(await request.post('/__test__/reset',{data:{include_video:true}})).toBeOK();await page.goto(workspace);
    await page.locator('article[data-image-key="0:clip.webm"] .image-card-footer button').click();
    const preview=page.getByRole('dialog',{name:'Image preview',exact:true}),video=preview.locator('video');
    await expect(video).toHaveJSProperty('controls',true);await expect(video).toHaveJSProperty('playsInline',true);
    const zoom=preview.getByRole('button',{name:'Zoom in',exact:true});await expect(zoom).toHaveAttribute('aria-disabled','true');await expect(zoom).toHaveAccessibleDescription('Image transforms are unavailable for video.');
    await video.focus();const position=await preview.locator('.image-preview-position').textContent();await page.keyboard.press('ArrowRight');await expect(preview.locator('.image-preview-position')).toHaveText(position);
    await video.evaluate(async node=>{window.previousClip=node;node.muted=true;await node.play();});
    await preview.getByRole('button',{name:'Next media',exact:true}).click();await expect(preview.locator('video')).toHaveCount(0);await expect(zoom).toHaveAttribute('aria-disabled','false');
    expect(await page.evaluate(()=>window.previousClip.paused&&!window.previousClip.isConnected&&!window.previousClip.hasAttribute('src'))).toBe(true);
    await preview.getByRole('button',{name:'Close preview',exact:true}).click();await expect(page.locator('.image-preview')).toHaveCount(0);
});

test('failed image requests offer retry while stale results cannot replace a newer route', async ({ page }) => {
    await page.route(imagePages, route => route.fulfill({
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
    await page.route(imagePages, async route => {
        const response = await route.fetch();
        arrived();
        await pending;
        await route.fulfill({ response }).catch(() => {});
    });
    await search(page).fill('nothing');
    await received;
    await page.getByRole('navigation', { name: 'Tools and system', exact: true })
        .getByRole('link', { name: 'Overview', exact: true }).click();
    release();
    await expect(page.locator('#workspace-main h1')).toHaveText('Overview');
    await expect(cards(page)).toHaveCount(0);
    await expect(details(page)).toHaveCount(0);
});

test('source and folder discovery failure stays visible and Import Rescan restores usable filters', async ({ page }) => {
    await page.route('**/prompt_manager/gallery/subfolders?*', route => route.fulfill({
        status: 503, json: { success: false, error: 'Sources unavailable' },
    }), { times: 1 });
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(2);
    await expect(page.getByRole('alert')).toHaveText('Sources and folders could not be loaded. Rescan from Import to retry.');
    await rescan(page);
    await expect(page.getByRole('alert')).toHaveCount(0);
    await refine(page, { Source: '1' });
    await expect(cards(page)).toHaveCount(1);
    await expect(cards(page)).toHaveAttribute('data-image-key', '1:shared/same.png');
    await refine(page, { Folder: 'shared' });
    await expect(cards(page)).toHaveCount(1);
});

async function staleSourceRecovery(page, request, roots) {
    await expect(await request.post('/__test__/reset', { data: { extra_images: 80 } })).toBeOK();
    await page.setViewportSize({ width: 1600, height: 1100 });
    await page.goto(workspace);
    const source = refineDialog(page).getByLabel('Source', { exact: true });
    const folder = refineDialog(page).getByLabel('Folder', { exact: true });
    const filters = page.getByRole('button', { name: 'Filters', exact: true });
    await filters.click();await expect(folder.locator('option[value="bulk"]')).toHaveCount(1);await page.keyboard.press('Escape');
    await refine(page, { Source: '0', Folder: 'bulk' });
    const configured = page.waitForResponse(response => {
        const url = new URL(response.url());return url.pathname.endsWith('/images/output') && url.searchParams.get('root') === '0' && url.searchParams.get('subfolder') === 'bulk' && url.searchParams.get('q') === 'png' && url.searchParams.get('media') === 'image' && url.searchParams.get('sort') === 'name';
    });
    await search(page).fill('png');
    await refine(page, { Media: 'image' });await sortBy(page).selectOption('name');
    const initial = await (await configured).json();await expect(caption(page)).toContainText('40 of 80');
    let oldPage;
    await page.route(imagePages, async route => {
        if (new URL(route.request().url()).searchParams.has('cursor') && !oldPage) {
            const response = await route.fetch();oldPage = { route, data: await response.json() };
        } else await route.continue();
    });
    await page.getByRole('button', { name: 'Load more images', exact: true }).scrollIntoViewIfNeeded();await expect.poll(() => !!oldPage).toBe(true);
    await expect(await request.post('/prompt_manager/settings', { data: { gallery_root_paths: roots } })).toBeOK();
    const selected = initial.images.at(-1), key = `${selected.root_index}:${selected.path}`;
    const stale = page.waitForResponse(response => response.url().includes('/images/inspect/') && response.status() === 409);
    await page.locator(`article[data-image-key="${key}"] .image-open`).click();expect((await stale).status()).toBe(409);
    await expect(details(page)).toContainText('The source changed. Refresh the image collection before inspecting this file.');
    await page.locator(`article[data-image-key="${key}"] .image-card-footer button`).click();
    const viewer = page.getByRole('dialog', { name: 'Image preview', exact: true });await expect(viewer).toContainText('The source changed.');
    const pages = [], inspections = [], options = [], mutations = [];
    page.on('request', request => {
        const url = new URL(request.url());
        if (url.pathname.endsWith('/images/output')) pages.push(url);
        if (url.pathname.includes('/images/inspect/')) inspections.push(url);
        if (request.method() !== 'GET') mutations.push(url.pathname);
    });
    await page.route('**/prompt_manager/gallery/subfolders?*', route => { options.push(route); });
    await viewer.getByRole('button', { name: 'Refresh image collection', exact: true }).click();
    await expect(viewer).toHaveCount(0);await expect.poll(() => options.length).toBe(1);
    await oldPage.route.fulfill({ json: oldPage.data });
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    return { source, folder, filters, pages, inspections, options, mutations };
}

for (const [name, roots] of [['reordered', ['input', 'output']], ['removed', ['input']]]) {
    test(`source-change recovery clears a ${name} root before discovery and safe retry`, async ({ page, request }, testInfo) => {
        const recovery = await staleSourceRecovery(page, request, roots);
        try {
            expect(recovery.pages.map(url => url.search)).toEqual([]);expect(recovery.inspections).toEqual([]);
            await expect(page.locator('.filter-row span.chip')).toHaveText('All output folders');
            for (const name of ['Filter by category', 'Sort results']) await expect(page.getByRole('combobox', { name, exact: true })).toBeDisabled();
            for (const name of ['Filters', 'Import']) await expect(page.getByRole('button', { name, exact: true })).toBeDisabled();
            await expect(search(page)).toBeDisabled();
            await expect(page.getByRole('button', { name: 'Load more images', exact: true })).toBeDisabled();
            await page.getByRole('button', { name: 'Load more images', exact: true }).evaluate(node => node.click());await recovery.filters.evaluate(node => node.click());await sortBy(page).dispatchEvent('change');
            await expect(refineDialog(page)).toBeHidden();
            await recovery.options[0].fulfill({ status: 503, json: { success: false, error: 'Discovery unavailable' } });
            await expect(page.getByRole('alert').filter({ hasText: 'Sources and folders could not be loaded.' })).toBeVisible();
            expect(recovery.pages).toEqual([]);expect(recovery.inspections).toEqual([]);await expect(cards(page)).toHaveCount(0);
            const retry = details(page).getByRole('button', { name: 'Refresh image collection', exact: true });await expect(retry).toBeEnabled();
            if (testInfo.project.name === 'chromium' && name === 'reordered') await page.screenshot({ path: testInfo.outputPath('safe-source-discovery-failure.png') });
            await retry.click();await expect.poll(() => recovery.options.length).toBe(2);
            expect(new URL(recovery.options[1].request().url()).searchParams.has('root')).toBe(false);
            const response = await recovery.options[1].fetch();await recovery.options[1].fulfill({ response });
            await expect.poll(() => recovery.pages.length).toBe(1);
            const query = recovery.pages[0].searchParams;expect(query.has('root')).toBe(false);expect(query.has('subfolder')).toBe(false);
            expect(query.get('q')).toBe('png');expect(query.get('media')).toBe('image');expect(query.get('sort')).toBe('name');
            await expect(recovery.filters).toBeEnabled();await recovery.filters.click();await expect(recovery.source).toHaveValue('all');
            await expect(recovery.source.locator('option')).toHaveText(['All sources', ...roots]);await page.keyboard.press('Escape');
            await expect.poll(() => recovery.inspections.length).toBe(1);expect(recovery.inspections[0].searchParams.get('root')).toBe(name === 'reordered' ? '1' : '0');
            expect(recovery.mutations).toEqual([]);
            if (testInfo.project.name === 'chromium' && name === 'reordered') await page.screenshot({ path: testInfo.outputPath('safe-source-recovery-success.png') });
        } finally { await expect(await request.post('/prompt_manager/settings', { data: { gallery_root_paths: ['output', 'input'] } })).toBeOK(); }
    });
}

test('source-change recovery ignores superseded discovery and route disposal', async ({ page, request }) => {
    const recovery = await staleSourceRecovery(page, request, ['input', 'output']);
    try {
        expect(recovery.pages).toEqual([]);await details(page).getByRole('button', { name: 'Refresh image collection', exact: true }).click();await expect.poll(() => recovery.options.length).toBe(2);
        await recovery.options[0].fulfill({ json: { roots: [{ index: 0, label: 'Obsolete source label' }], subfolders: ['obsolete'] } });
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        expect(recovery.pages).toEqual([]);await expect(page.locator('.filter-row span.chip')).toHaveText('All output folders');await expect(recovery.filters).toBeDisabled();
        await page.getByRole('navigation', { name: 'Tools and system', exact: true }).getByRole('link', { name: 'Overview', exact: true }).click();
        await recovery.options[1].fulfill({ json: { roots: [{ index: 0, label: 'Late source label' }], subfolders: ['late'] } });
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        await expect(page.locator('#workspace-main h1')).toHaveText('Overview');expect(recovery.pages).toEqual([]);expect(recovery.inspections).toEqual([]);expect(recovery.mutations).toEqual([]);
    } finally { await expect(await request.post('/prompt_manager/settings', { data: { gallery_root_paths: ['output', 'input'] } })).toBeOK(); }
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
    await expect(search(page)).toBeVisible();
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
    await page.route(imagePages, async route => {
        if (delayed) return route.continue();
        delayed = true;
        const response = await route.fetch();
        arrived();
        await pending;
        await route.fulfill({ response }).catch(() => {});
        complete();
    });
    const box = search(page);
    await box.fill('same.png');
    await received;
    await box.fill('0001');
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
        await page.getByRole('combobox', { name: 'Theme', exact: true }).selectOption(theme);
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
    await rescan(page);
    await expect.poll(() => page.evaluate(() => !window.__oldInspectorVideo.isConnected)).toBe(true);
    expect(await page.evaluate(() => window.__oldInspectorVideo.getAttribute('src'))).toBeNull();
});

test('Import Rescan rescans cached files and preserves the collection when rescan fails', async ({ page, request }) => {
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(2);
    await expect(await request.post('/__test__/add-nested-image')).toBeOK();
    await page.route('**/prompt_manager/gallery/rescan', route => route.fulfill({
        status: 503, json: { success: false, error: 'Rescan unavailable' },
    }), { times: 1 });
    await rescan(page);
    await expect(page.getByRole('alert')).toContainText('could not be rescanned');
    await expect(cards(page)).toHaveCount(2);
    await rescan(page);
    await expect(cards(page)).toHaveCount(3);
    await expect(cards(page).filter({ hasText: 'new-output.png' })).toHaveCount(1);
    await expect(page.getByRole('alert')).toBeHidden();
});

test('folder discovery offers ancestors that filter nested images', async ({ page, request }) => {
    await expect(await request.post('/__test__/add-nested-image')).toBeOK();
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(3);
    await page.getByRole('button', { name: 'Filters', exact: true }).click();
    await expect(refineDialog(page).getByLabel('Folder', { exact: true }).locator('option[value="project"]')).toHaveCount(1);
    // The concept's Folder default.
    await expect(refineDialog(page).getByLabel('Folder', { exact: true }).locator('option').first()).toHaveText('All output folders');
    await page.keyboard.press('Escape');
    await refine(page, { Folder: 'project' });
    await expect(cards(page)).toHaveCount(1);
    await expect(cards(page)).toContainText('new-output.png');
});

test('leaving during Rescan cancels it without replacing the next workspace', async ({ page }) => {
    let release;
    const pending = new Promise(resolve => { release = resolve; });
    await page.route('**/prompt_manager/gallery/rescan', async route => {
        await pending;
        await route.fulfill({ json: { success: true } }).catch(() => {});
    });
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(2);
    const requested = page.waitForRequest('**/prompt_manager/gallery/rescan');
    await rescan(page);
    await requested;
    await page.getByRole('navigation', { name: 'Tools and system', exact: true })
        .getByRole('link', { name: 'Overview', exact: true }).click();
    await expect(page.locator('#workspace-main h1')).toHaveText('Overview');
    release();
    await expect(page.getByRole('heading', { name: 'Continue creating', exact: true })).toBeVisible();
    await expect(page.getByText('Output folders could not be rescanned. Try Rescan again.', { exact: true })).toHaveCount(0);
});


test('Images uses approved geometry while keeping real controls', async ({page,request}) => {
    await request.post('/__test__/reset',{data:{workspace_visual:true}});
    await page.setViewportSize({width:1600,height:1100});
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(24);
    await expect(page.locator('#workspace-main > .heading p.muted')).toHaveText('Your generations, their prompts, and the next idea.');
    await expect(details(page)).toHaveCSS('width','310px');
    await expect(page.locator('.gallery-layout')).toHaveCSS('column-gap','22px');
    for (const label of ['Filter by category','Sort results','Thumbnail size']) await expect(page.getByRole('combobox',{name:label,exact:true})).toBeVisible();
    await expect(search(page)).toHaveAttribute('placeholder','Search prompts, tags, filenames…');
    await expect(page.getByRole('button',{name:'Group by positive prompt →',exact:true})).toBeVisible();
    const geometry = await cards(page).evaluateAll(nodes=>nodes.slice(0,4).map(node=>{
        const box=node.getBoundingClientRect(),media=node.querySelector('.image-open').getBoundingClientRect();
        return {x:box.x,y:box.y,right:box.right,bottom:box.bottom,width:box.width,mediaWidth:media.width,mediaHeight:media.height};
    }));
    expect(geometry[1].x-geometry[0].right).toBeCloseTo(13,0);
    expect(geometry[3].y-geometry[0].bottom).toBeCloseTo(13,0);
    expect(geometry[3].x).toBeCloseTo(geometry[0].x,0);
    expect(geometry[0].mediaWidth/geometry[0].mediaHeight).toBeCloseTo(4/3,2);
    expect((await caption(page).boundingBox()).y).toBeLessThan(geometry[0].y);
    await expect(cards(page).first()).toHaveCSS('outline-style','solid');
    const cover=details(page).getByRole('button',{name:'Open full preview',exact:true});
    await expect(cover).toHaveCSS('height','150px');
    await expect(cover.locator('img')).toBeVisible();await expect(cover.locator('img')).toHaveCSS('height','150px');
    expect(await cover.evaluate(node=>node===node.parentElement.firstElementChild&&node.tagName==='BUTTON')).toBe(true);
    await page.setViewportSize({width:390,height:844});
    await expect.poll(()=>cards(page).nth(1).evaluate(node=>node.offsetTop)).toBe(0);
    await expect.poll(()=>cards(page).nth(2).evaluate(node=>node.offsetTop)).toBeGreaterThan(0);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth-document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
});

test('density changes and resize preserve identity focus and selection', async ({page,request}) => {
    await request.post('/__test__/reset',{data:{workspace_visual:true}});
    await page.setViewportSize({width:1600,height:1100});await page.goto(workspace);
    await expect(cards(page)).toHaveCount(24);
    const first=cards(page).first();await first.evaluate(node=>{window.originalImageCard=node;});
    await first.getByRole('button',{name:/^Open /}).click();
    const key=await first.getAttribute('data-image-key');
    let imageReads=0;page.on('request',request=>{if(request.url().includes('/images/output?')) imageReads++;});
    for (const layout of ['Grid','List','Masonry']) for (const [density,columns] of [['comfortable',3],['large',2],['compact',4]]) {
        await page.getByRole('button',{name:layout,exact:true}).click();
        await page.getByRole('combobox',{name:'Thumbnail size',exact:true}).selectOption(density);
        await first.getByRole('button',{name:/^Open /}).focus();await page.keyboard.press('ArrowDown');
        await expect(cards(page).nth(layout==='List'?1:columns).getByRole('button',{name:/^Open /})).toBeFocused();
        await expect(first).toHaveClass(/selected/);
        expect(await first.evaluate(node=>node===window.originalImageCard)).toBe(true);
    }
    expect(imageReads).toBe(0);
    await page.evaluate(()=>{location.hash='#/overview';});await expect(page.locator('h1')).toHaveText('Overview');
    await page.evaluate(()=>{location.hash='#/images';});await expect(page.getByRole('combobox',{name:'Thumbnail size',exact:true})).toHaveValue('compact');
    await expect(page.locator(`article[data-image-key="${key}"]`)).toHaveClass(/selected/);
    await page.getByRole('button',{name:'Grid',exact:true}).click();
    for (const [width,columns] of [[951,3],[950,3],[700,2],[390,2]]) {
        await page.setViewportSize({width,height:844});
        await expect.poll(()=>cards(page).nth(columns).evaluate(node=>node.offsetLeft)).toBe(0);
        await first.getByRole('button',{name:/^Open /}).focus();await page.keyboard.press('ArrowDown');
        await expect(cards(page).nth(columns).getByRole('button',{name:/^Open /})).toBeFocused();
        expect(await page.evaluate(()=>document.documentElement.scrollWidth-document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
    }
});

test('images toolbar and filter row match the concept controls', async ({ page, request }) => {
    // Old → new: Search filenames → toolbar search; Source/Folder/Media → Filters; Refresh → Import → Rescan (D15).
    await expect(await request.post('/prompt_manager/save', { data: { text: 'Dog landscape', category: 'Landscapes', tags: ['dogs'], rating: 5 } })).toBeOK();
    const { roots } = await (await request.get('/prompt_manager/gallery/subfolders?mode=workspace')).json();
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(2);
    const main = page.locator('#workspace-main');
    await expect(main.locator(':scope > .heading p.muted')).toHaveText('Your generations, their prompts, and the next idea.');
    await expect(main.locator(':scope > .heading .heading-actions > button')).toHaveText(['Import', 'New prompt']);
    await expect(main.locator(':scope > .heading .heading-actions > button').nth(1)).toHaveClass(/ca-primary/);
    await expect(page.getByRole('searchbox', { name: 'Search filenames' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Refresh', exact: true })).toHaveCount(0);
    await expect(page.getByRole('link', { name: 'Current gallery' })).toHaveCount(0);
    await expect(main.locator('h2', { hasText: /^All images$/ })).toHaveCount(0);
    await expect(main.locator('search.toolbar > *')).toHaveCount(4);
    await expect(sortBy(page).locator('option')).toHaveText(['Recently added', 'Name A–Z', 'Highest rated', 'Oldest first', 'Name Z–A']);
    const row = main.locator('.filter-row');
    await expect(row.locator(':scope > *')).toHaveCount(5);
    await expect(row.locator(':scope > .segmented[role="group"][aria-label="Image layout"] > button')).toHaveText(['Grid', 'List', 'Masonry']);
    await expect(row.locator(':scope > label.small[for="image-size"]')).toHaveText('Thumbnail size');
    await expect(row.locator(':scope > select#image-size option')).toHaveText(['Comfortable', 'Large', 'Compact']);
    await expect(row.locator(':scope > span.chip')).toHaveText('All output folders');
    await expect(row.locator(':scope > span.chip svg.icon')).toHaveCount(1);
    await expect(row.locator(':scope > button.chip')).toHaveText('Group by positive prompt →');

    await search(page).fill('cat');
    await expect(cards(page)).toHaveCount(1);
    await expect(cards(page)).toHaveAttribute('data-image-key', '0:shared/same.png');
    await search(page).fill('dogs');
    await expect(cards(page)).toHaveCount(1);
    await expect(cards(page)).toHaveAttribute('data-image-key', '1:shared/same.png');
    await search(page).fill('');
    await expect(cards(page)).toHaveCount(2);
    await expect(cards(page).first()).toHaveAttribute('data-image-key', '0:shared/same.png');
    await sortBy(page).selectOption('rating');
    await expect(cards(page).first()).toHaveAttribute('data-image-key', '1:shared/same.png');
    await sortBy(page).selectOption('newest');
    await page.getByRole('combobox', { name: 'Filter by category', exact: true }).selectOption('Landscapes');
    await expect(cards(page)).toHaveCount(1);
    await expect(cards(page)).toHaveAttribute('data-image-key', '1:shared/same.png');
    await page.getByRole('combobox', { name: 'Filter by category', exact: true }).selectOption('');
    await expect(cards(page)).toHaveCount(2);

    await refine(page, { Source: '1', Folder: 'shared', Media: 'image' });
    await expect(cards(page)).toHaveCount(1);
    await expect(cards(page)).toHaveAttribute('data-image-key', '1:shared/same.png');
    await expect(row.locator(':scope > span.chip')).toHaveText(`${roots[1].label} · shared`);
    await refine(page, { Media: 'video' });
    await expect(cards(page)).toHaveCount(0);
    await refine(page, { Source: 'all', Folder: '', Media: 'all', Tags: 'cats' });
    await expect(cards(page)).toHaveCount(1);
    await expect(cards(page)).toHaveAttribute('data-image-key', '0:shared/same.png');
    await expect(row.locator(':scope > span.chip')).toHaveText('All output folders');
    await page.getByRole('combobox', { name: 'Filter by category', exact: true }).selectOption('Landscapes');
    await expect(cards(page)).toHaveCount(0);
    await page.getByRole('button', { name: 'Filters', exact: true }).click();
    await expect(refineDialog(page).getByLabel('Tags', { exact: true })).toHaveValue('cats');
    await refineDialog(page).getByRole('button', { name: 'Clear filters', exact: true }).click();
    await expect(page.getByRole('combobox', { name: 'Filter by category', exact: true })).toHaveValue('');
    await expect(cards(page)).toHaveCount(2);

    const rescans = [];
    page.on('request', request => { if (request.url().includes('/prompt_manager/gallery/rescan')) rescans.push(request.method()); });
    await expect(await request.post('/__test__/add-nested-image')).toBeOK();
    await rescan(page);
    await expect(cards(page)).toHaveCount(3);
    await expect(caption(page)).toContainText('3 of 3 images');
    expect(rescans).toEqual(['POST']);
    await page.getByRole('button', { name: 'Import', exact: true }).click();
    await importDialog(page).getByRole('button', { name: 'Open inspector', exact: true }).click();
    await expect(page).toHaveURL(/#\/inspect$/);
    await page.goBack();
    await expect(page.locator('#workspace-main h1')).toHaveText('Images');
    await row.locator(':scope > button.chip').click();
    await expect(page).toHaveURL(/#\/library$/);
});

test('browser Back closes the open Images dialog and leaves the next page usable', async ({ page }) => {
    await page.goto('/prompt_manager/workspace#/overview');
    await expect(page.locator('#workspace-main h1')).toHaveText('Overview');
    for (const [button, dialog] of [['Filters', refineDialog], ['Import', importDialog]]) {
        await page.evaluate(() => { location.hash = '/images'; });
        await expect(cards(page)).toHaveCount(2);
        await page.getByRole('button', { name: button, exact: true }).click();
        await expect(dialog(page)).toBeVisible();
        await page.goBack();
        await expect(page.locator('#workspace-main h1')).toHaveText('Overview');
        await expect(page.locator('dialog#modal[open]')).toHaveCount(0);
        await page.getByRole('navigation', { name: 'Workspace', exact: true }).getByRole('link', { name: /^Images(?:\s*[\d,]+)?$/ }).click();
        await expect(cards(page)).toHaveCount(2);
        await search(page).fill('cat');
        await expect(cards(page)).toHaveCount(1);
        await search(page).fill('');
        await expect(cards(page)).toHaveCount(2);
        await page.evaluate(() => { location.hash = '/overview'; });
        await expect(page.locator('#workspace-main h1')).toHaveText('Overview');
    }
});

test('count line shows images and positive prompts', async ({ page, request }) => {
    await page.addInitScript(() => { window.IntersectionObserver = undefined; });
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(2);
    await expect(caption(page).locator('#result-count')).toHaveText('2 of 2 images · 2 positive prompts');
    await expect(caption(page).locator(':scope > span')).toHaveCount(2);
    await expect(caption(page).locator(':scope > span').nth(1)).toHaveText(/^Today · [A-Z][a-z]+ \d{1,2}$/);
    await sortBy(page).selectOption('name');
    await expect(cards(page).first()).toHaveAttribute('data-image-key', /same\.png$/);
    await expect(caption(page).locator(':scope > span').nth(1)).toBeHidden();
    await search(page).fill('cat');
    await expect(caption(page).locator('#result-count')).toHaveText('1 of 1 image · 1 positive prompt');

    await expect(await request.post('/__test__/reset', { data: { extra_images: 100 } })).toBeOK();
    // A plain cursor page leaves positive_count out; the first page's count stays.
    const cursorPages = [];
    await page.route(url => imagePages(url) && url.searchParams.has('cursor'), async route => {
        const response = await route.fetch();
        const json = await response.json();
        cursorPages.push('positive_count' in json);
        await route.fulfill({ response, json });
    });
    // A fresh document: a same-page visit would restore the earlier search.
    await page.goto('about:blank');
    await page.goto(workspace);
    await expect(caption(page).locator('#result-count')).toHaveText('40 of 102 images · 2 positive prompts');
    await page.getByRole('button', { name: 'Load more images', exact: true }).click();
    await expect(caption(page).locator('#result-count')).toHaveText('80 of 102 images · 2 positive prompts');
    expect(cursorPages).toEqual([false]);

    // A failed count read is null: the count line keeps only the images.
    await page.unrouteAll({ behavior: 'wait' });
    await page.route(imagePages, async route => {
        const response = await route.fetch();
        await route.fulfill({ response, json: { ...(await response.json()), positive_count: null } });
    });
    await page.goto('about:blank');
    await page.goto(workspace);
    await expect(caption(page).locator('#result-count')).toHaveText('40 of 102 images');
});

test('Load more toast reports the new total', async ({ page, request }) => {
    await page.addInitScript(() => { window.IntersectionObserver = undefined; });
    await expect(await request.post('/__test__/reset', { data: { extra_images: 100 } })).toBeOK();
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(40);
    await expect(page.locator('.infinite-continuation p.small')).toHaveText('40 of 102 images · more load as you scroll');
    await expect(page.locator('#workspace-toast')).toHaveText('');
    await page.getByRole('button', { name: 'Load more images', exact: true }).click();
    await expect(page.locator('#workspace-toast')).toHaveText('40 more images loaded. 80 of 102.');
    await page.getByRole('button', { name: 'Load more images', exact: true }).click();
    await expect(page.locator('#workspace-toast')).toHaveText('22 more images loaded. 102 of 102.');
    await expect(page.locator('.infinite-continuation p.small')).toHaveText('You’re all caught up · 102 images');
    await search(page).fill('no-such-image');
    await expect(cards(page)).toHaveCount(0);
    await expect(page.locator('.infinite-continuation p.small')).toHaveText('No matching images · 0 images');
});

test('Load more focus never falls to the page when the last page hides the button', async ({ page, request }) => {
    await page.addInitScript(() => { window.IntersectionObserver = undefined; });
    await expect(await request.post('/__test__/reset', { data: { extra_images: 100 } })).toBeOK();
    // The last page repeats already listed files: nothing new to focus, and Load more disappears.
    await page.route(url => imagePages(url) && url.searchParams.has('cursor'), async route => {
        const response = await route.fetch();
        const json = await response.json();
        await route.fulfill({ response, json: { ...json, images: [], has_more: false, next_cursor: null } });
    });
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(40);
    const more = page.getByRole('button', { name: 'Load more images', exact: true });
    await more.focus();
    await page.keyboard.press('Enter');
    await expect(more).toBeHidden();
    await expect(page.locator('article[aria-posinset="40"] .image-open')).toBeFocused();
    expect(await page.evaluate(() => document.activeElement !== document.body)).toBe(true);
});

test('a search with no match shows the concept empty state', async ({ page }) => {
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(2);
    const empty = page.locator('.gallery-body > .empty');
    await expect(empty).toBeHidden();
    await search(page).fill('no-such-image');
    await expect(cards(page)).toHaveCount(0);
    await expect(empty).toBeVisible();
    await expect(empty).toHaveText('No matching images. Try another search or category.');
    await search(page).fill('');
    await expect(cards(page)).toHaveCount(2);
    await expect(empty).toBeHidden();
});

test('a Load more that finishes after focus moved on leaves focus where the user put it', async ({ page, request }) => {
    await page.addInitScript(() => { window.IntersectionObserver = undefined; });
    await expect(await request.post('/__test__/reset', { data: { extra_images: 100 } })).toBeOK();
    let release, arrived;
    const held = new Promise(resolve => { release = resolve; });
    const started = new Promise(resolve => { arrived = resolve; });
    await page.route(url => imagePages(url) && url.searchParams.has('cursor'), async route => { arrived(); await held; await route.continue(); }, { times: 1 });
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(40);
    await page.getByRole('button', { name: 'Load more images', exact: true }).click();
    await started;
    await search(page).focus();
    release();
    await expect(cards(page)).toHaveCount(80);
    await expect(search(page)).toBeFocused();
});
