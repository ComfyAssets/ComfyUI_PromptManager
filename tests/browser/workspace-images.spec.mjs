import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';
import { seedConcept, openConcept, openProduction } from './concept-inventory.mjs';

const workspace = '/prompt_manager/workspace#/images';
const cards = page => page.locator('article[data-image-key]');
// Grid pages only: the sidebar's Images count is its own `limit=1` read of the same listing.
const imagePages = url => url.pathname === '/prompt_manager/images/output' && url.search !== '' && url.searchParams.get('limit') !== '1';
const details = page => page.getByRole('complementary', { name: 'Selected image details', exact: true });
// The concept panel names no source (D20); its cover is the selected file's own rooted URL, root 0 output, 1 input.
const SOURCE_ROOTS = { output: 0, input: 1 };
const expectPanelSource = (page, source) => expect(details(page).locator('img.detail-cover')).toHaveAttribute('src', new RegExp(`[?&]root=${SOURCE_ROOTS[source]}(&|$)`));
// The concept toolbar, filter row and caption (#249 C2); Source, Folder, Media and Tags live in "Refine this view".
const search = page => page.getByRole('searchbox', { name: 'Search workspace', exact: true });
const sortBy = page => page.getByRole('combobox', { name: 'Sort results', exact: true });
const caption = page => page.locator('.section-caption');
const refineDialog = page => page.getByRole('dialog', { name: 'Refine this view', exact: true });
const importDialog = page => page.getByRole('dialog', { name: 'Import & inspect', exact: true });
// D44: the Filters button reads "Filters · N" while N Refine filters are active.
const filtersButton = page => page.locator('#workspace-main search.toolbar').getByRole('button', { name: /^Filters(?: · \d+)?$/ });
async function refine(page, values) {
    await filtersButton(page).click();
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
    await cards(page).nth(1).getByRole('button', { name: 'Inspect same.png', exact: true }).click();
    await expect(details(page)).toContainText('same.png');
    await expectPanelSource(page, 'input');
    await capture('after-images-source-collision-input-1600');
    await cards(page).first().getByRole('button', { name: 'Inspect same.png', exact: true }).click();
    await expectPanelSource(page, 'output');
    await capture('after-images-source-collision-output-1600');
    await page.setViewportSize({width:390,height:844});
    for (const [index,source] of [[1,'input'],[0,'output']]) {
        await cards(page).nth(index).getByRole('button',{name:'Inspect same.png',exact:true}).click();
        await expectPanelSource(page, source);
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

test('image selection is shown on the selected card', async ({ page }) => {
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(2);
    // D19: Info selects into the panel; as in the concept, the card carries `.selected` and Info has no pressed state.
    const selected = cards(page).last().getByRole('button', { name: 'Inspect same.png', exact: true });
    await selected.click();
    await expect(cards(page).last()).toHaveClass(/\bselected\b/);
    await expect(cards(page).first()).not.toHaveClass(/\bselected\b/);
    await expect(cards(page).locator('.image-card-actions [aria-pressed]')).toHaveCount(0);
});

test('the side panel cover is the concept plain image, never a preview control', async ({ page }) => {
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(2);
    await cards(page).last().getByRole('button', { name: 'Inspect same.png', exact: true }).click();
    await expectPanelSource(page, 'input');
    // D19/D20: the card image opens the viewer; the panel cover is decoration (`alt=""`) and not focusable.
    await expect(details(page).getByRole('button', { name: 'Open full preview', exact: true })).toHaveCount(0);
    await expect(details(page).locator('img.detail-cover')).toHaveAttribute('alt', '');
    await details(page).locator('img.detail-cover').click();
    await expect(page.locator('dialog[open]')).toHaveCount(0);
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
            const preview = page.locator(`article[data-image-key="${key}"]`).getByRole('button', { name: /^Open / });
            await preview.click();
            const viewer = page.locator('dialog#viewer-dialog');
            await expect(viewer.locator('.viewer-canvas img')).toBeVisible();
            await page.evaluate(() => window.continuationFixture.finishPage());
            await expect(page.getByRole('button', { name: 'Load more images', exact: true })).toHaveAttribute('aria-disabled', 'false');
            await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
            expect(await page.evaluate(() => ({ reads: window.continuationFixture.reads, nativeObserver: typeof IntersectionObserver, visible: document.querySelector('.infinite-continuation').getBoundingClientRect().top <= innerHeight })))
                .toEqual({ reads: [null, 'next-0'], nativeObserver: 'function', visible: true });
            if (close === 'parent abort') await page.evaluate(() => window.continuationFixture.scope.dispose());
            else if (close === 'Close') await viewer.getByRole('button', { name: 'Close image viewer', exact: true }).click();
            else await page.keyboard.press('Escape');
            await expect(viewer).toHaveCount(0);
            if (close !== 'parent abort') {
                await expect(preview).toBeFocused();
                await expect.poll(() => page.evaluate(() => window.continuationFixture.reads)).toEqual([null, 'next-0', 'next-1']);
                // An embedded collection has no caption (D47); its loaded cards show the continuation instead.
                if (embedded) { await expect(cards(page)).toHaveCount(80); await expect(caption(page)).toHaveCount(0); }
                else await expect(caption(page)).toContainText('80 of 80');
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
    await cards(page).nth(1).getByRole('button', { name: 'Inspect same.png', exact: true }).click();
    for (const layout of ['List', 'Masonry', 'Grid']) {
        await page.getByRole('button', { name: layout, exact: true }).click();
        await expect(page.getByRole('button', { name: layout, exact: true })).toHaveAttribute('aria-pressed', 'true');
        await expect(cards(page)).toHaveCount(2);
        await expectPanelSource(page, 'input');
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
    await cards(page).first().getByRole('button', { name: /^Inspect / }).click();
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
    await expect(page.locator('.filter-row span.chip')).toHaveText(/ · All output folders$/);
    await expect(page.getByRole('button', { name: 'List', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await expect(caption(page)).toContainText('80 of 100 images');
    await expect(cards(page)).toHaveCount(80);
    await expect(details(page).getByRole('heading', { level: 2 })).toHaveText(selectedTitle);
    await expect.poll(async () => Math.abs((await page.evaluate(() => window.scrollY)) - previousScroll)).toBeLessThan(120);
    // Checked last: closing the dialog focuses Filters, which scrolls the toolbar into view. The restored Source
    // still counts (D44).
    await page.getByRole('button', { name: 'Filters · 1', exact: true }).click();
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
    await cards(page).first().getByRole('button', { name: /^Inspect / }).click();
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
    await first.getByRole('button', { name: /^Inspect / }).click();
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
        .getByRole('button', { name: /^Open / }).click();
    await expect(page.locator('dialog[open] .viewer-canvas img')).toBeVisible();
    await page.getByRole('button', { name: 'Close image viewer', exact: true }).click();
});

test('virtual gallery keyboard navigation reaches loaded endpoints and announces collection positions', async ({ page, request }) => {
    await page.addInitScript(() => { window.IntersectionObserver = undefined; });
    await expect(await request.post('/__test__/reset', { data: { extra_images: 200 } })).toBeOK();
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(40);
    const firstKey = await cards(page).first().getAttribute('data-image-key');
    const firstName = await cards(page).first().getByRole('button', { name: /^Open / }).getAttribute('aria-label');
    await cards(page).first().getByRole('button', { name: /^Inspect / }).click();
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
    await cards(page).last().getByRole('button', { name: 'Open same.png', exact: true }).click();
    await expect(page.locator('dialog[open] .viewer-canvas img')).toBeVisible();
    await expect(page.locator('dialog[open] .viewer-canvas img')).toHaveAttribute('src', /root=1/);
    await page.getByRole('button', { name: 'Close image viewer', exact: true }).click();
    await expect(page.locator('dialog[open]')).toHaveCount(0);
    await cards(page).first().getByRole('button', { name: 'Open same.png', exact: true }).click();
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
    // Legacy video art inside the concept card; D11 names the video's own extension.
    await expect(card.locator('.media-art > .image-open > video')).toHaveCount(1);
    await expect(card.locator('.media-info p')).toHaveText(/(?:^|· )WEBM$/);
    expect(await card.locator('video').evaluate(node => node.getBoundingClientRect().height)).toBeGreaterThan(20);
    await card.getByRole('button', { name: 'Open clip.webm', exact: true }).click();
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
    await page.getByRole('button', { name: 'Close image viewer', exact: true }).click();
    await expect(page.locator('dialog video')).toHaveCount(0);
    expect(await page.evaluate(() => window.__previewVideo.paused && !window.__previewVideo.isConnected)).toBe(true);
    await card.getByRole('button', { name: 'Open clip.webm', exact: true }).click();
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
    await cards(page).first().getByRole('button',{name:'Open same.png',exact:true}).click();await waiting;
    const preview=page.locator('dialog#viewer-dialog');
    await expect(preview.getByRole('button',{name:'Zoom in',exact:true})).toHaveAttribute('aria-disabled','true');
    await page.route('**/images/inspect/shared/same.png?*',route=>route.fulfill({status:503,json:{success:false,error:'Unavailable'}}),{times:1});
    await preview.getByRole('button',{name:'Next media',exact:true}).click();
    await expect(preview).toContainText('Could not read prompts from this file.');await expect(preview.getByRole('button',{name:'Zoom in',exact:true})).toHaveAttribute('aria-disabled','false');
    await expect(preview.locator('#viewer-toolbar').getByRole('link',{name:'Download original',exact:true})).toHaveAttribute('href',/root=1&root_scope=/);
    await preview.getByRole('button',{name:'Rotate image 90 degrees',exact:true}).click();
    await page.evaluate(()=>{window.latestCanvas=document.querySelector('.viewer-canvas img');window.liveViewers[0].options.viewed();});release();
    expect(await page.evaluate(()=>window.liveViewers.at(-1).imageData.rotate)).toBe(90);
    await preview.getByRole('button',{name:'Retry prompt details',exact:true}).click();
    await expect(preview).toContainText('same.png');await expect(preview.getByRole('button',{name:'Retry prompt details',exact:true})).toHaveCount(0);
    expect(await page.evaluate(()=>window.latestCanvas===document.querySelector('.viewer-canvas img'))).toBe(true);
    await page.evaluate(()=>{location.hash='/overview';});await expect(page.locator('.viewer-container,#viewer-dialog')).toHaveCount(0);
});

test('collection viewer describes video transforms and releases native playback on navigation',async({page,request})=>{
    await expect(await request.post('/__test__/reset',{data:{include_video:true}})).toBeOK();await page.goto(workspace);
    await page.locator('article[data-image-key="0:clip.webm"] .image-open').click();
    const preview=page.locator('dialog#viewer-dialog'),video=preview.locator('video');
    await expect(video).toHaveJSProperty('controls',true);await expect(video).toHaveJSProperty('playsInline',true);
    const zoom=preview.getByRole('button',{name:'Zoom in',exact:true});await expect(zoom).toHaveAttribute('aria-disabled','true');await expect(zoom).toHaveAccessibleDescription('Image transforms are unavailable for video.');
    await video.focus();const position=await preview.locator('.media-position').textContent();await page.keyboard.press('ArrowRight');await expect(preview.locator('.media-position')).toHaveText(position);
    await video.evaluate(async node=>{window.previousClip=node;node.muted=true;await node.play();});
    await preview.getByRole('button',{name:'Next media',exact:true}).click();await expect(preview.locator('video')).toHaveCount(0);await expect(zoom).toHaveAttribute('aria-disabled','false');
    expect(await page.evaluate(()=>window.previousClip.paused&&!window.previousClip.isConnected&&!window.previousClip.hasAttribute('src'))).toBe(true);
    await preview.getByRole('button',{name:'Close image viewer',exact:true}).click();await expect(page.locator('#viewer-dialog')).toHaveCount(0);
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
    const filters = filtersButton(page);
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
    await page.locator(`article[data-image-key="${key}"] .image-card-actions button:first-child`).click();expect((await stale).status()).toBe(409);
    await expect(details(page)).toContainText('The source changed. Refresh the image collection before inspecting this file.');
    await page.locator(`article[data-image-key="${key}"] .image-open`).click();
    const viewer = page.locator('dialog#viewer-dialog');await expect(viewer).toContainText('The source changed.');
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
            // D44: recovery clears Source and Folder; the Media filter stays and still counts.
            await expect(recovery.filters).toHaveText('Filters · 1');await expect(recovery.filters).toBeDisabled();
            await expect(page.getByRole('button', { name: 'Import', exact: true })).toBeDisabled();
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
        await cards(page).last().getByRole('button', { name: 'Inspect same.png', exact: true }).click();
        await expectPanelSource(page, 'input');
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
    await card.getByRole('button', { name: 'Inspect clip.webm', exact: true }).click();
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
    await expect(toast(page)).toHaveText('Rescan could not finish. Try again.');
    await expect(page.getByRole('alert')).toHaveCount(0);
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
    await expect(toast(page)).not.toHaveText(/^Rescan (finished|could not finish)/);
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
    // The concept's `.image-open img { aspect-ratio: 1.33 }`.
    expect(geometry[0].mediaWidth/geometry[0].mediaHeight).toBeCloseTo(1.33,2);
    expect((await caption(page).boundingBox()).y).toBeLessThan(geometry[0].y);
    await expect(cards(page).first()).toHaveCSS('box-shadow',/ 0px 0px 0px 1px$/);
    const cover=details(page).locator('img.detail-cover');
    await expect(cover).toBeVisible();await expect(cover).toHaveCSS('height','150px');
    expect(await cover.evaluate(node=>node===node.parentElement.firstElementChild&&node.tagName==='IMG')).toBe(true);
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
    await first.getByRole('button',{name:/^Inspect /}).click();
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
    // app.js toolbar() a347–360: search, category, sort, Save view, Filters.
    await expect(main.locator('search.toolbar > *')).toHaveCount(5);
    await expect(main.locator('search.toolbar > button')).toHaveText(['Save view', 'Filters']);
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

    await expect(filtersButton(page)).toHaveText('Filters');
    await refine(page, { Source: '1', Folder: 'shared', Media: 'image' });
    await expect(cards(page)).toHaveCount(1);
    await expect(cards(page)).toHaveAttribute('data-image-key', '1:shared/same.png');
    await expect(row.locator(':scope > span.chip')).toHaveText(`${roots[1].label} · shared`);
    // D44: Source, Folder and Media each count once; the toolbar category is not a Refine filter.
    await expect(filtersButton(page)).toHaveText('Filters · 3');
    await refine(page, { Media: 'video' });
    await expect(cards(page)).toHaveCount(0);
    await refine(page, { Source: 'all', Folder: '', Media: 'all', Tags: 'cats' });
    await expect(cards(page)).toHaveCount(1);
    await expect(cards(page)).toHaveAttribute('data-image-key', '0:shared/same.png');
    await expect(row.locator(':scope > span.chip')).toHaveText('All output folders');
    await page.getByRole('combobox', { name: 'Filter by category', exact: true }).selectOption('Landscapes');
    await expect(cards(page)).toHaveCount(0);
    await expect(filtersButton(page)).toHaveText('Filters · 1');
    await page.getByRole('button', { name: 'Filters · 1', exact: true }).click();
    await expect(refineDialog(page).getByLabel('Tags', { exact: true })).toHaveValue('cats');
    await refineDialog(page).getByRole('button', { name: 'Clear filters', exact: true }).click();
    await expect(page.getByRole('combobox', { name: 'Filter by category', exact: true })).toHaveValue('');
    await expect(cards(page)).toHaveCount(2);
    await expect(page.getByRole('button', { name: 'Filters', exact: true })).toBeFocused();

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

// #249 C3: the concept media card (a396–399), its activation (D19) and the concept grid breakpoints (D3).
const CARD_PATHS = () => {
    // Tag and class paths below the card; icon internals are one `svg.icon`.
    const walk = (node, prefix, out) => {
        for (const child of node.children) {
            const name = child.tagName.toLowerCase();
            const classes = [...child.classList].filter(value => value !== 'selected').sort();
            const path = `${prefix} > ${name}${classes.map(value => `.${value}`).join('')}`;
            out.push(path);
            if (name !== 'svg') walk(child, path, out);
        }
        return out;
    };
    return walk;
};
async function cardInventory(page, concept) {
    return page.locator(concept ? '#image-grid > article.media-card' : 'article[data-image-key]').evaluateAll((nodes, {walkSource, concept}) => {
        const walk = new Function(`return (${walkSource})()`)();
        return nodes.map(card => {
            const copy = card.cloneNode(true);
            // Info then Tag (#249 C2 retired D9). The D8 checkbox and the D7 star are compared.
            const open = card.querySelector('.image-open'), img = open.querySelector('img');
            const star = card.querySelector('.image-overlays > button.star-button');
            const box = card.querySelector('.image-overlays > label > input');
            return { filename: open.getAttribute('aria-label').replace(/^Open /, ''),
                // The concept's alt is the card title plus its demo-only " · sample illustration N" (m5).
                alt: img.alt.replace(/ · sample illustration \d+$/, ''),
                size: [img.getAttribute('width'), img.getAttribute('height')],
                title: card.querySelector('.media-info h3').textContent, subtitle: card.querySelector('.media-info p').textContent,
                ratio: card.style.getPropertyValue('--media-ratio').replace(/\s/g, ''),
                actions: [...card.querySelectorAll('.image-card-actions > button')].map(button => button.getAttribute('aria-label')),
                star: star && { text: star.textContent, pressed: star.getAttribute('aria-pressed'), label: star.getAttribute('aria-label') },
                select: box && { type: box.type, checked: box.checked, label: box.getAttribute('aria-label') },
                paths: walk(copy, `article.${[...card.classList].filter(value => value !== 'selected').sort().join('.')}`, []) };
        });
    }, { walkSource: CARD_PATHS.toString(), concept });
}

test('card matches the concept structure', async ({ page, request }) => {
    await seedConcept(request);
    await page.setViewportSize({ width: 1600, height: 1100 });
    await openConcept(page, 'images', 'dark');
    const concept = new Map((await cardInventory(page, true)).map(card => [card.filename, card]));
    expect(concept.size).toBeGreaterThan(5);
    await openProduction(page, 'images', 'dark');
    await expect(cards(page)).toHaveCount(24);
    const production = await cardInventory(page, false);
    const listed = (await (await request.get('/prompt_manager/images/output?limit=40&mode=workspace')).json()).images;
    let compared = 0;
    for (const card of production) {
        const reference = concept.get(card.filename);
        if (!reference) continue;
        compared++;
        // D10 title (the linked positive's, also for pair links), D11 "W × H · PNG · Seed N", the concept's
        // --media-ratio, image alt and size attributes, Inspect label, the D7 star (the fixture stars the concept's) and markup.
        expect(card, card.filename).toEqual(reference);
    }
    expect(compared).toBeGreaterThan(5);
    expect(production.filter(card => card.star?.pressed === 'true').length).toBeGreaterThan(0);
    // D10/D20: a pair-linked file's inspector heading is the positive's title, as the concept panel's.
    const pairLinked = 'alpine_00001.png';
    expect(concept.get(pairLinked)).toBeTruthy();
    await page.getByRole('button', { name: `Inspect ${pairLinked}`, exact: true }).click();
    await expect(details(page).getByRole('heading', { level: 2 })).toHaveText(concept.get(pairLinked).title);
    await openConcept(page, 'images', 'dark');
    await page.locator(`#image-grid [aria-label="Inspect ${pairLinked}"]`).click();
    await expect(page.locator('#image-inspector h2')).toHaveText(concept.get(pairLinked).title);
    await openProduction(page, 'images', 'dark');
    await expect(cards(page)).toHaveCount(24);
    const first = cards(page).first();
    // The h3 keeps the full path; no byte size and no "Preview <file>" button remain on the card.
    for (const image of listed.slice(0, 6)) {
        const card = page.locator(`article[data-image-key="${image.root_index}:${image.path}"]`);
        await expect(card.locator('.media-info h3')).toHaveAttribute('title', image.path);
    }
    await expect(page.locator('article[data-image-key]').getByRole('button', { name: /^Preview / })).toHaveCount(0);
    expect(await cards(page).evaluateAll(nodes => nodes.filter(node => /\d\s?(?:B|KB|MB|GB)\b/.test(node.textContent)).length)).toBe(0);
    await expect(first.locator('.image-card-footer, small, strong')).toHaveCount(0);

    // D10: anything but a confirmed link shows the filename; D11: the seed is the string as sent, unknown parts omitted.
    await page.route(imagePages, async route => {
        const response = await route.fetch(), json = await response.json();
        const [a, b, c, ...rest] = json.images;
        await route.fulfill({ response, json: { ...json, images: [
            { ...a, linked: { ...a.linked, status: 'ambiguous' }, seed: '9007199254740993' },
            { ...b, width: null, height: null, seed: null },
            { ...c, linked: { status: 'unavailable', positive_id: null, title: null, title_source: null, category: null, rating: null } },
            ...rest] } });
    });
    await page.goto('about:blank'); await page.goto(workspace);
    await expect(cards(page)).toHaveCount(24);
    const [a, b, c] = listed;
    await expect(cards(page).nth(0).locator('.media-info h3')).toHaveText(a.filename);
    await expect(cards(page).nth(0).locator('.media-info p')).toHaveText(`${a.width} × ${a.height} · PNG · Seed 9007199254740993`);
    await expect(cards(page).nth(1).locator('.media-info h3')).toHaveText(b.linked.title);
    await expect(cards(page).nth(1).locator('.media-info p')).toHaveText('PNG');
    await expect(cards(page).nth(2).locator('.media-info h3')).toHaveText(c.filename);
});

test('image click opens the viewer and Info selects the panel', async ({ page }) => {
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(2);
    // D19: the first load still selects the first image into the panel.
    await expect(cards(page).first()).toHaveClass(/selected/);
    await expectPanelSource(page, 'output');
    const viewer = page.locator('dialog#viewer-dialog');
    const image = cards(page).nth(1).getByRole('button', { name: 'Open same.png', exact: true });
    await image.click();
    await expect(viewer.locator('.viewer-canvas img')).toBeVisible();
    await expect(viewer.locator('.viewer-canvas img')).toHaveAttribute('src', /root=1/);
    await page.keyboard.press('Escape');
    await expect(viewer).toHaveCount(0);
    await expect(image).toBeFocused();
    for (const key of ['Enter', ' ']) {
        await image.focus(); await page.keyboard.press(key);
        await expect(viewer.locator('.viewer-canvas img')).toBeVisible();
        await page.keyboard.press('Escape');
        await expect(viewer).toHaveCount(0);
        await expect(image).toBeFocused();
    }
    const info = cards(page).nth(1).getByRole('button', { name: 'Inspect same.png', exact: true });
    await expect(info.locator('svg.icon')).toHaveCount(1);
    await info.click();
    await expect(viewer).toHaveCount(0);
    await expect(cards(page).nth(1)).toHaveClass(/selected/);
    await expect(cards(page).first()).not.toHaveClass(/selected/);
    await expect(info).not.toHaveAttribute('aria-pressed');
    await expectPanelSource(page, 'input');
    await image.focus(); await page.keyboard.press('ArrowLeft');
    await expect(cards(page).first().getByRole('button', { name: 'Open same.png', exact: true })).toBeFocused();
    await expectPanelSource(page, 'input');
});

test('grid columns follow concept breakpoints', async ({ page, request }) => {
    await seedConcept(request);
    const expected = { comfortable: { 1600: 3, 1800: 4, 1200: 2, 390: 2 }, large: { 1600: 2, 1800: 2, 1200: 2, 390: 2 },
        compact: { 1600: 4, 1800: 5, 1200: 3, 390: 2 } };
    const rowLength = selector => page.locator(selector).evaluateAll(nodes => {
        const top = nodes[0].getBoundingClientRect().top;
        return nodes.filter(node => Math.abs(node.getBoundingClientRect().top - top) < 1).length;
    });
    for (const [url, selector] of [[null, '#image-grid > article.media-card'], [workspace, 'article[data-image-key]']]) {
        for (const width of [1600, 1800, 1200, 390]) {
            await page.setViewportSize({ width, height: 1100 });
            if (url) { await page.goto('about:blank'); await page.goto(url); } else await openConcept(page, 'images', 'dark');
            await expect.poll(() => page.locator(selector).count()).toBeGreaterThan(5);
            for (const density of ['comfortable', 'large', 'compact']) {
                await page.locator('#image-size').selectOption(density);
                await expect.poll(() => rowLength(selector), `${url ? 'production' : 'concept'} ${density} ${width}`).toBe(expected[density][width]);
                if (url) {
                    // The columns come from concept.css: the grid's own tracks, not a production breakpoint table.
                    expect(await page.locator('.image-grid').evaluate(node => getComputedStyle(node).gridTemplateColumns.split(/\s+/).length))
                        .toBe(expected[density][width]);
                    await cards(page).first().locator('.image-open').focus(); await page.keyboard.press('ArrowDown');
                    await expect(cards(page).nth(expected[density][width]).locator('.image-open')).toBeFocused();
                }
            }
        }
    }
});

// `cssZoom` follows the suite's zoom convention (media queries keep the 1600px breakpoint); the 800px variant is a real
// 200% browser zoom: half the CSS viewport at device scale 2, so the concept's narrower breakpoint applies too (m4).
async function zoomedGallery(page, request, { cssZoom }) {
    await page.addInitScript(() => { window.IntersectionObserver = undefined; });
    await expect(await request.post('/__test__/reset', { data: { extra_images: 200 } })).toBeOK();
    if (cssZoom) await page.setViewportSize({ width: 1600, height: 1100 });
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(40);
    if (cssZoom) await page.evaluate(() => { document.documentElement.style.zoom = '2'; });
    const more = page.getByRole('button', { name: 'Load more images', exact: true });
    for (const loaded of [80, 120, 160, 200, 202]) {
        await more.click();
        await expect(caption(page)).toContainText(`${loaded} of 202 images`);
    }
    await expect.poll(() => cards(page).count()).toBeLessThan(120);
    // Every mounted card keeps the concept structure inside its reserved box.
    expect(await cards(page).evaluateAll(nodes => nodes.filter(node => {
        const box = node.getBoundingClientRect(), info = node.querySelector('.media-info').getBoundingClientRect();
        return info.bottom > box.bottom + 1 || info.right > box.right + 1 || !node.querySelector('.media-art > .image-open');
    }).length)).toBe(0);
    await page.evaluate(() => window.scrollTo(0, 0));
    const first = cards(page).first();
    await first.locator('.image-open').focus();
    await page.keyboard.press('End');
    await expect(page.locator('article[aria-posinset="202"] .image-open')).toBeFocused();
    await page.keyboard.press('Home');
    await expect(page.locator('article[aria-posinset="1"] .image-open')).toBeFocused();
    const columns = await cards(page).evaluateAll(nodes => {
        const top = nodes[0].getBoundingClientRect().top;
        return nodes.filter(node => Math.abs(node.getBoundingClientRect().top - top) < 1).length;
    });
    await page.keyboard.press('ArrowDown');
    await expect(page.locator(`article[aria-posinset="${columns + 1}"] .image-open`)).toBeFocused();
    // The concept's card order: image, the D8 checkbox, the D7 star, then Inspect.
    await page.keyboard.press('Tab');
    await expect(page.locator(`article[aria-posinset="${columns + 1}"] .image-overlays input[type="checkbox"]`)).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(page.locator(`article[aria-posinset="${columns + 1}"] .image-overlays .star-button`)).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(page.locator(`article[aria-posinset="${columns + 1}"] .image-card-actions button:first-child`)).toBeFocused();
    await page.evaluate(() => {
        const card = document.querySelector('article[aria-posinset="1"]');
        const grid = document.querySelector('.image-grid');
        window.scrollTo(0, (grid.getBoundingClientRect().top + window.scrollY) + card.offsetHeight * 30);
    });
    await expect.poll(() => cards(page).count()).toBeLessThan(120);
    const previousScroll = await page.evaluate(() => window.scrollY);
    expect(previousScroll).toBeGreaterThan(1000);
    await page.evaluate(() => { window.location.hash = '/overview'; });
    await expect(page.locator('#workspace-main h1')).toHaveText('Overview');
    await page.evaluate(() => { window.location.hash = '/images'; });
    await expect(caption(page)).toContainText('202 of 202 images');
    await expect.poll(async () => Math.abs((await page.evaluate(() => window.scrollY)) - previousScroll)).toBeLessThan(240);
    await expect.poll(() => cards(page).count()).toBeLessThan(120);
    if (cssZoom) await page.evaluate(() => { document.documentElement.style.zoom = ''; });
}

test('bounded DOM, keyboard and scroll restoration survive at 200% zoom', async ({ page, request }) => {
    await zoomedGallery(page, request, { cssZoom: true });
});

test.describe('at an 800px viewport and device scale 2', () => {
    test.use({ viewport: { width: 800, height: 550 }, deviceScaleFactor: 2 });
    test('bounded DOM, keyboard and scroll restoration survive at real 200% zoom', async ({ page, request }) => {
        await zoomedGallery(page, request, { cssZoom: false });
        // The concept's narrow breakpoint, read from the grid's own tracks.
        expect(await page.locator('.image-grid').evaluate(node => getComputedStyle(node).gridTemplateColumns.split(/\s+/).length)).toBe(2);
    });
});

test('a debounced search that lands after a sort change does not reload the same page', async ({ page }) => {
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(2);
    const fetches = await recordFetches(page);
    // The sort change applies the typed query at once; the search debounce then reports the same values.
    await search(page).fill('same');
    await sortBy(page).selectOption('name');
    // HTML timers of equal delay run in the order they were set, so a timer of the toolbar's own debounce delay set
    // now runs after the debounced report.
    await page.evaluate(async () => {
        const { SEARCH_DELAY } = await import(new URL('js/workspace/toolbar.mjs', document.baseURI).href);
        await new Promise(resolve => setTimeout(resolve, SEARCH_DELAY));
    });
    const reads = (await fetches()).filter(item => imagePages(new URL(`http://host${item.path}${item.search}`)));
    expect(reads.filter(item => new URLSearchParams(item.search).get('q') === 'same')).toHaveLength(1);
});

// #249 D18: the concept's hover zoom (1.03 over 0.2s); reduced motion makes it instant, and a touch device (hover: none)
// has no sticky zoom after a tap.
for (const motion of ['no-preference', 'reduce']) {
    test(`card image zooms on hover per concept (${motion})`, async ({ page }) => {
        await page.emulateMedia({ reducedMotion: motion });
        await page.goto(workspace);
        await expect(cards(page)).toHaveCount(2);
        const card = cards(page).first();
        const img = card.locator('.image-open img');
        const touch = await page.evaluate(() => matchMedia('(hover: none)').matches);
        expect(await img.evaluate(n => getComputedStyle(n).transform)).toBe('none');
        await card.locator('.image-open').hover();
        // Touch: the emulated hover must not leave a zoom behind.
        await expect.poll(() => img.evaluate(n => getComputedStyle(n).transform)).toBe(touch ? 'none' : 'matrix(1.03, 0, 0, 1.03, 0, 0)');
        expect(await img.evaluate(n => getComputedStyle(n).transitionDuration)).toBe(motion === 'reduce' ? '0s' : '0.2s');
    });
}

// D7 = A: the concept's star (app.js mediaCard, actions.favorite) against the PR B favorites store.
const favoriteRoute = '**/prompt_manager/images/favorite';
const star = (card, filename) => card.getByRole('button', { name: `Favorite ${filename}`, exact: true });
const toast = page => page.locator('#workspace-toast');
const listedFavorites = async request => Object.fromEntries((await (await request.get('/prompt_manager/images/output?limit=40&mode=workspace')).json())
    .images.map(image => [`${image.root_index}:${image.path}`, image.favorite]));
async function expectStar(button, favorite) {
    await expect(button).toHaveText(favorite ? '★' : '☆');
    await expect(button).toHaveAttribute('aria-pressed', String(favorite));
}

test('favorite persists across reload', async ({ page, request }) => {
    await expect(await request.post('/__test__/reset', { data: { include_video: true } })).toBeOK();
    const sent = [];
    page.on('request', item => {
        if (new URL(item.url()).pathname === '/prompt_manager/images/favorite') {
            sent.push({ method: item.method(), type: item.headers()['content-type'], body: item.postDataJSON() });
        }
    });
    await page.goto(workspace);
    const input = page.locator('article[data-image-key="1:shared/same.png"]');
    const video = page.locator('article[data-image-key="0:clip.webm"]');
    await expect(input).toBeVisible(); await expect(video).toBeVisible();
    // The star sits in the concept's overlay on image and video cards alike.
    await expect(input.locator('.media-art > .image-overlays > button.star-button')).toHaveCount(1);
    await expect(video.locator('.media-art > .image-overlays > button.star-button')).toHaveCount(1);
    await expectStar(star(input, 'same.png'), false);
    await expectStar(star(video, 'clip.webm'), false);
    const selected = await page.locator('article.selected').getAttribute('data-image-key');
    expect(selected).not.toBe('1:shared/same.png');

    await star(input, 'same.png').click();
    await expectStar(star(input, 'same.png'), true);
    await expect(toast(page)).toHaveText('Added to favorites.');
    // The star neither opens the viewer nor inspects the card.
    await expect(page.locator('dialog#viewer-dialog')).toHaveCount(0);
    await expect(page.locator('article.selected')).toHaveAttribute('data-image-key', selected);
    await expect(star(input, 'same.png')).toBeFocused();

    // Keyboard: Enter stars the video, Space clears it again.
    await star(video, 'clip.webm').focus();
    await page.keyboard.press('Enter');
    await expectStar(star(video, 'clip.webm'), true);
    await expect(toast(page)).toHaveText('Added to favorites.');
    // One request per file at a time: a press while the star is still busy is ignored, so wait for it to settle.
    await expect(star(video, 'clip.webm')).not.toHaveAttribute('aria-disabled', 'true');
    await page.keyboard.press(' ');
    await expectStar(star(video, 'clip.webm'), false);
    await expect(toast(page)).toHaveText('Removed from favorites.');
    await expect(page.locator('dialog#viewer-dialog')).toHaveCount(0);
    await expect.poll(() => sent.length).toBe(3);
    expect(sent).toEqual([
        { method: 'PUT', type: 'application/json', body: { root_index: 1, path: 'shared/same.png' } },
        { method: 'PUT', type: 'application/json', body: { root_index: 0, path: 'clip.webm' } },
        { method: 'DELETE', type: 'application/json', body: { root_index: 0, path: 'clip.webm' } },
    ]);
    // Favorites are keyed by source: the same file name in the other root stays unstarred.
    await expect.poll(() => listedFavorites(request)).toMatchObject({ '1:shared/same.png': true, '0:shared/same.png': false, '0:clip.webm': false });

    await page.reload();
    await expectStar(star(input, 'same.png'), true);
    await expectStar(star(page.locator('article[data-image-key="0:shared/same.png"]'), 'same.png'), false);
    await expectStar(star(video, 'clip.webm'), false);
    await star(input, 'same.png').click();
    await expectStar(star(input, 'same.png'), false);
    await expect(toast(page)).toHaveText('Removed from favorites.');
    await expect.poll(() => listedFavorites(request)).toMatchObject({ '1:shared/same.png': false });
    await page.reload();
    await expectStar(star(input, 'same.png'), false);

    // Leaving Images and coming back restores the browsing state with the current star.
    await star(input, 'same.png').click();
    await expect(toast(page)).toHaveText('Added to favorites.');
    await page.evaluate(() => { window.location.hash = '/overview'; });
    await expect(page.locator('#workspace-main h1')).toHaveText('Overview');
    await page.evaluate(() => { window.location.hash = '/images'; });
    await expectStar(star(input, 'same.png'), true);
});

test('favorite failure keeps the previous state and announces it', async ({ page, request }) => {
    await page.goto(workspace);
    const card = page.locator('article[data-image-key="0:shared/same.png"]');
    const button = star(card, 'same.png');
    await expectStar(button, false);
    const held = [];
    await page.route(favoriteRoute, route => { held.push(route); });
    const fetches = await recordFetches(page);
    const favoriteFetches = async () => (await fetches()).filter(item => item.path === '/prompt_manager/images/favorite').length;
    // Optimistic while in flight; a second press during the request sends nothing more.
    await button.click();
    await expectStar(button, true);
    // The pending star says it is busy, so the ignored second press is not a silent no-op.
    await expect(button).toHaveAttribute('aria-disabled', 'true');
    // Playwright waits for an aria-disabled button to enable; a user can still press it, so force the press.
    await button.click({ force: true });
    expect(await favoriteFetches()).toBe(1);
    await expect.poll(() => held.length).toBe(1);
    await held[0].fulfill({ status: 500, json: { success: false, error: 'Favorite update failed' } });
    await expectStar(button, false);
    await expect(button).not.toHaveAttribute('aria-disabled', 'true');
    await expect(toast(page)).toHaveText('Favorite could not be added. Try again.');
    expect((await listedFavorites(request))['0:shared/same.png']).toBe(false);

    // Removal failure keeps the star; a network failure is announced the same way.
    await page.unroute(favoriteRoute);
    await button.click();
    await expectStar(button, true);
    await expect(toast(page)).toHaveText('Added to favorites.');
    await page.route(favoriteRoute, route => route.abort('failed'));
    await button.click();
    await expectStar(button, true);
    await expect(toast(page)).toHaveText('Favorite could not be removed. Try again.');
    await page.unroute(favoriteRoute);
    await page.reload();
    await expectStar(star(card, 'same.png'), true);
    expect((await listedFavorites(request))['0:shared/same.png']).toBe(true);

    // A failure that lands after leaving Images still restores the star the user comes back to, and is announced.
    const late = [];
    await page.route(favoriteRoute, route => { late.push(route); });
    await star(card, 'same.png').click();
    await expectStar(star(card, 'same.png'), false);
    await expect.poll(() => late.length).toBe(1);
    await page.evaluate(() => { window.location.hash = '/overview'; });
    await expect(page.locator('#workspace-main h1')).toHaveText('Overview');
    await late[0].fulfill({ status: 500, json: { success: false, error: 'Favorite update failed' } });
    await expect(toast(page)).toHaveText('Favorite could not be removed. Try again.');
    await page.evaluate(() => { window.location.hash = '/images'; });
    await expectStar(star(card, 'same.png'), true);
    await page.unroute(favoriteRoute);

    // One request per file at a time, across leaving and returning: the request outlives the page that sent it, the
    // returned page still waits for it, and its answer reaches the card the user is looking at.
    const across = [];
    await page.route(favoriteRoute, route => { across.push(route); });
    await star(card, 'same.png').click();
    await expectStar(star(card, 'same.png'), false);
    await expect.poll(() => across.length).toBe(1);
    await page.evaluate(() => { window.location.hash = '/overview'; });
    await expect(page.locator('#workspace-main h1')).toHaveText('Overview');
    await page.evaluate(() => { window.location.hash = '/images'; });
    await expectStar(star(card, 'same.png'), false);
    await expect(star(card, 'same.png')).toHaveAttribute('aria-disabled', 'true');
    // The page was reloaded above: record afresh, from the ignored press on.
    const afterReturn = await recordFetches(page);
    await star(card, 'same.png').click({ force: true });
    expect((await afterReturn()).filter(item => item.path === '/prompt_manager/images/favorite')).toEqual([]);
    expect(across.length).toBe(1);
    await expectStar(star(card, 'same.png'), false);
    await across[0].fulfill({ status: 500, json: { success: false, error: 'Favorite update failed' } });
    await expectStar(star(card, 'same.png'), true);
    await expect(star(card, 'same.png')).not.toHaveAttribute('aria-disabled', 'true');
    await expect(toast(page)).toHaveText('Favorite could not be removed. Try again.');
    // The rolled-back star is also what the next return shows, and the server never changed.
    await page.evaluate(() => { window.location.hash = '/overview'; });
    await expect(page.locator('#workspace-main h1')).toHaveText('Overview');
    await page.evaluate(() => { window.location.hash = '/images'; });
    await expectStar(star(card, 'same.png'), true);
    expect((await listedFavorites(request))['0:shared/same.png']).toBe(true);
});

// #249 final review I1: the old Refresh button showed that it ran; Import → Rescan says so too, and when it ends.
test('Import Rescan shows that it runs, ignores a second press and announces the outcome', async ({ page, request }) => {
    const held = [];
    await page.route('**/prompt_manager/gallery/rescan', route => { held.push(route); });
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(2);
    await expect(await request.post('/__test__/add-nested-image')).toBeOK();
    await rescan(page);
    await expect.poll(() => held.length).toBe(1);
    await expect(toast(page)).toHaveText('Rescanning output folders…');
    // Reopened while the scan runs, Rescan says it is running; a press on it is ignored and keeps the dialog open.
    await page.getByRole('button', { name: 'Import', exact: true }).click();
    const running = importDialog(page).getByRole('button', { name: 'Rescanning…', exact: true });
    await expect(running).toHaveAttribute('aria-disabled', 'true');
    await running.click({ force: true });
    await expect(importDialog(page)).toBeVisible();
    expect(held.length).toBe(1);
    await expect(cards(page)).toHaveCount(2);
    // The scan's own answer: every file in both fixture roots (output and input).
    await held[0].continue();
    await expect(toast(page)).toHaveText('Rescan finished. 3 files in 2 output folders.');
    const idle = importDialog(page).getByRole('button', { name: 'Rescan', exact: true });
    await expect(idle).not.toHaveAttribute('aria-disabled', 'true');
    await page.keyboard.press('Escape');
    await expect(cards(page)).toHaveCount(3);
    await expect(caption(page)).toContainText('3 of 3 images');
});

// #249 final review I2: a polite live region re-announces replaced text, so an unchanged status is never rewritten.
test('a star press leaves the continuation status, count and caption date untouched', async ({ page }) => {
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(2);
    await expect(page.locator('#gallery-continuation p')).toHaveText(/caught up/);
    await expect(caption(page).locator('span').nth(1)).toBeVisible();
    await page.evaluate(() => {
        window.__statusMutations = [];
        const observer = new MutationObserver(records => {
            window.__statusMutations.push(...records.map(record => `${record.type}:${record.target.nodeName}`));
        });
        for (const node of [document.querySelector('#gallery-continuation p'), document.querySelector('#result-count'),
            document.querySelector('.section-caption > span:nth-child(2)')]) {
            observer.observe(node, { childList: true, characterData: true, subtree: true, attributes: true });
        }
    });
    const card = page.locator('article[data-image-key="0:shared/same.png"]');
    await star(card, 'same.png').click();
    await expectStar(star(card, 'same.png'), true);
    await expect(toast(page)).toHaveText('Added to favorites.');
    expect(await page.evaluate(() => window.__statusMutations)).toEqual([]);
});

// Opened before the source and folder discovery answered (a slow server), Refine lists them once it does.
test('Refine opened before discovery settles lists the discovered sources and folders', async ({ page }) => {
    let release, started;
    const held = new Promise(resolve => { release = resolve; }), hit = new Promise(resolve => { started = resolve; });
    await page.route('**/prompt_manager/gallery/subfolders?*', async route => { started(); await held; await route.continue(); }, { times: 1 });
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(2);
    await hit;
    await filtersButton(page).click();
    const source = refineDialog(page).getByLabel('Source', { exact: true });
    const folder = refineDialog(page).getByLabel('Folder', { exact: true });
    const values = select => select.locator('option').evaluateAll(options => options.map(option => option.value));
    expect(await values(source)).toEqual(['all']);
    expect(await values(folder)).toEqual(['']);
    release();
    await expect.poll(() => values(source)).toEqual(['all', '0', '1']);
    expect(await values(folder)).toEqual(['', 'shared']);
    await expect(source).toHaveValue('all');
    await source.selectOption('1');
    await refineDialog(page).getByRole('button', { name: 'Apply filters', exact: true }).click();
    await expect(cards(page)).toHaveCount(1);
    await expect(cards(page)).toHaveAttribute('data-image-key', '1:shared/same.png');
});

// #249 final review m1: as in the old field row, a Source offers only its own folders.
test('a search reload keeps the Filters label node, so a click landing during the reload is not lost', async ({ page, request }) => {
    // WebKit drops a click whose mousedown hit a text node that was replaced before mouseup; the reload reports the
    // (unchanged) active count, so the label must keep its node.
    await expect(await request.post('/__test__/reset', { data: { extra_images: 80 } })).toBeOK();
    await page.goto(workspace);
    const button = filtersButton(page);
    await button.click();await expect(refineDialog(page).getByLabel('Folder', { exact: true }).locator('option[value="bulk"]')).toHaveCount(1);await page.keyboard.press('Escape');
    await refine(page, { Source: '0', Folder: 'bulk' });
    await expect(button).toHaveText('Filters · 2');
    const label = await button.evaluateHandle(node => node.firstChild);
    const reloaded = page.waitForResponse(response => {
        const url = new URL(response.url());return imagePages(url) && url.searchParams.get('q') === 'png';
    });
    await search(page).fill('png');
    await reloaded;
    await expect(button).toHaveText('Filters · 2');
    expect(await button.evaluate((node, text) => node.firstChild === text && text.isConnected, label)).toBe(true);
});

test('Refine offers only the chosen source folders', async ({ page, request }) => {
    await expect(await request.post('/__test__/add-nested-image')).toBeOK();
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(3);
    await page.getByRole('button', { name: 'Filters', exact: true }).click();
    const source = refineDialog(page).getByLabel('Source', { exact: true });
    const folder = refineDialog(page).getByLabel('Folder', { exact: true });
    const values = () => folder.locator('option').evaluateAll(options => options.map(option => option.value));
    expect(await values()).toEqual(['', 'project', 'project/day', 'shared']);
    await folder.selectOption('project');
    await source.selectOption('1');
    expect(await values()).toEqual(['', 'shared']);
    await expect(folder).toHaveValue('');
    await source.selectOption('0');
    expect(await values()).toEqual(['', 'project', 'project/day', 'shared']);
    await folder.selectOption('shared');
    await source.selectOption('1');
    await expect(folder).toHaveValue('shared');
    await refineDialog(page).getByRole('button', { name: 'Apply filters', exact: true }).click();
    await expect(cards(page)).toHaveCount(1);
    await expect(cards(page)).toHaveAttribute('data-image-key', '1:shared/same.png');
    // Reopened on that Source, the dialog again lists only its folders. Source and Folder count (D44).
    await page.getByRole('button', { name: 'Filters · 2', exact: true }).click();
    expect(await values()).toEqual(['', 'shared']);
});

/**
 * Record every fetch the page's code calls from now on, as `{method, path, search}`. The API client calls fetch in the
 * same task as the action that needs it, so after a press the record already holds any request the press started.
 */
async function recordFetches(page) {
    await page.evaluate(() => {
        window.__fetches = [];
        const original = window.fetch;
        window.fetch = (resource, init = {}) => {
            const url = new URL(String(resource instanceof Request ? resource.url : resource), window.location.href);
            window.__fetches.push({ method: (init.method || 'GET').toUpperCase(), path: url.pathname, search: url.search });
            return original.call(window, resource, init);
        };
    });
    return () => page.evaluate(() => window.__fetches);
}
/** The listing page reads among them (the sidebar count's `limit=1` read is not one). */
async function countImageFetches(page) {
    const fetches = await recordFetches(page);
    return async () => (await fetches()).filter(item => imagePages(new URL(`http://host${item.path}${item.search}`))).length;
}

// #249 final review m2: like the toolbar, an unchanged refinement reloads nothing, so the loaded cards (and with them
// the reader's place) stay as they were.
test('Apply or Clear without a change keeps the loaded page', async ({ page, request }) => {
    await expect(await request.post('/__test__/reset', { data: { extra_images: 80 } })).toBeOK();
    await page.setViewportSize({ width: 1200, height: 700 });
    await page.goto(workspace);
    await expect(caption(page)).toContainText(/of 82 images/);
    const fetches = await countImageFetches(page);
    await cards(page).first().evaluate(card => { card.dataset.kept = 'yes'; });
    const before = await caption(page).textContent();
    for (const action of ['Apply filters', 'Clear filters']) {
        await page.getByRole('button', { name: 'Filters', exact: true }).click();
        await refineDialog(page).getByRole('button', { name: action, exact: true }).click();
        await expect(refineDialog(page)).toBeHidden();
        expect(await fetches()).toBe(0);
        await expect(cards(page).first()).toHaveAttribute('data-kept', 'yes');
        await expect(caption(page)).toHaveText(before);
    }
    // A real change still applies.
    await refine(page, { Media: 'image' });
    await expect.poll(fetches).toBe(1);
});

// #249 final review m6: discovery and categories failing together on mount give one alert, not two.
test('unreachable sources and categories announce one error', async ({ page }) => {
    await page.route('**/prompt_manager/gallery/subfolders?*', route => route.abort('failed'));
    await page.route('**/prompt_manager/library/options*', route => route.abort('failed'));
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(2);
    await expect(page.getByRole('alert')).toHaveText('Sources, folders and categories could not be loaded. Rescan from Import to retry.');
    await expect(page.locator('.options-status')).toHaveCount(1);
    await page.unroute('**/prompt_manager/gallery/subfolders?*');
    await page.unroute('**/prompt_manager/library/options*');
    await rescan(page);
    await expect(page.getByRole('alert')).toHaveCount(0);
});

// PR D-b Task 3 (D42): the shared toast keeps the concept's fixed bottom placement, so a discovery alert that a rescan
// leaves visible must not stay under it. The window ends just below the alert, inside the toast's band, as on a short
// window or a phone with its browser bars shown.
const boxOf = locator => locator.evaluate(node => {
    const { top, right, bottom, left } = node.getBoundingClientRect();
    return { top, right, bottom, left };
});
const overlaps = (a, b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
for (const width of [1600, 390]) {
    test(`Rescan toast does not cover the discovery alert it leaves at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 1100 });
        await page.route('**/prompt_manager/library/options*', route => route.abort('failed'));
        await page.goto(workspace);
        await expect(cards(page)).toHaveCount(2);
        const alert = page.getByRole('alert');
        const failed = 'Categories could not be loaded. Rescan from Import to retry.';
        await expect(alert).toHaveText(failed);
        const toastInset = width > 700 ? 22 : 12;
        await page.setViewportSize({ width, height: Math.ceil((await boxOf(alert)).bottom) + toastInset + 16 });
        await rescan(page);
        await expect(toast(page)).toHaveText(/^Rescan finished\. /);
        await expect(alert).toHaveText(failed);
        await expect.poll(async () => overlaps(await boxOf(alert), await boxOf(toast(page)))).toBe(false);
        const [shown, placed] = [await boxOf(alert), await boxOf(toast(page))];
        expect(shown.top).toBeGreaterThanOrEqual(0);
        expect(shown.bottom).toBeLessThanOrEqual(placed.top);
        // The concept's toast stays where the concept puts it: centred, its inset from the bottom.
        const viewport = page.viewportSize();
        expect(Math.round(viewport.height - placed.bottom)).toBe(toastInset);
        expect(Math.abs((placed.left + placed.right) / 2 - viewport.width / 2)).toBeLessThan(1);
    });
}

// Final review m3: D42 scrolls only when the alert meets the toast's band, never back up to one above the window.
test('Rescan does not scroll back to a discovery alert above the window', async ({ page }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    await page.route('**/prompt_manager/library/options*', route => route.abort('failed'));
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(2);
    const alert = page.getByRole('alert');
    await expect(alert).toHaveText('Categories could not be loaded. Rescan from Import to retry.');
    await page.setViewportSize({ width: 1600, height: 260 });
    // Import is at the top; the page is scrolled down to the last card while its dialog is open.
    await page.getByRole('button', { name: 'Import', exact: true }).click();
    await cards(page).last().evaluate(node => node.scrollIntoView({ block: 'end' }));
    expect((await boxOf(alert)).bottom).toBeLessThan(0);
    // The reload itself may move the page; what D42 must not do is scroll the alert back into view.
    await alert.evaluate(node => {
        window.alertScrolls = 0;
        node.scrollIntoView = (...args) => { window.alertScrolls += 1; Element.prototype.scrollIntoView.apply(node, args); };
    });
    await importDialog(page).getByRole('button', { name: 'Rescan', exact: true }).click();
    await expect(toast(page)).toHaveText(/^Rescan finished\. /);
    await expect(alert).toBeAttached();
    expect(await page.evaluate(() => window.alertScrolls)).toBe(0);
});

// #249 final review m8: D36's alphabetical categories hold whatever order the server sends.
test('toolbar categories are alphabetical regardless of server order', async ({ page }) => {
    await page.route('**/prompt_manager/library/options*', route => route.fulfill({
        json: { success: true, categories: ['zeta', 'Alpha', 'beta'] },
    }));
    await page.goto(workspace);
    const category = page.getByRole('combobox', { name: 'Filter by category', exact: true });
    await expect(category.locator('option')).toHaveText(['All categories', 'Alpha', 'beta', 'zeta']);
});

// #249 D8 follow-up (image selection, Task 1): the concept's card checkbox and selection bar (app.js 398, 426-434).
const bulkbar = page => page.locator('#workspace-main > #bulkbar');
const checkbox = (card, filename) => card.getByRole('checkbox', { name: `Select ${filename}`, exact: true });
const barButtons = page => bulkbar(page).locator('.bulkbar > button');

test('card checkboxes select images into the concept bar and Clear empties it', async ({ page }) => {
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(2);
    // The concept's place: an empty bar between the filter row and the gallery layout.
    await expect(bulkbar(page)).toBeEmpty();
    expect(await bulkbar(page).evaluate(bar => [bar.previousElementSibling.className, bar.nextElementSibling.classList.contains('gallery-layout')]))
        .toEqual(['filter-row', true]);
    // Each overlay starts with the checkbox label, then the star.
    expect(await page.locator('.image-overlays').evaluateAll(rows => rows.map(row => [...row.children].map(child =>
        `${child.tagName.toLowerCase()}${child.querySelector(':scope > input') ? `>${child.firstElementChild.type}` : ''}`))))
        .toEqual([['label>checkbox', 'button'], ['label>checkbox', 'button']]);
    const [first, second] = [cards(page).first(), cards(page).last()];
    const selectedBefore = await cards(page).evaluateAll(nodes => nodes.map(node => node.classList.contains('selected')));
    await checkbox(first, 'same.png').check();
    await expect(bulkbar(page).locator('.bulkbar > strong')).toHaveText('1 selected');
    await expect(barButtons(page)).toHaveText(['Set category', 'Export metadata', 'Clear']);
    // Toggling neither opens the viewer, nor inspects, nor moves focus.
    await expect(page.locator('dialog[open]')).toHaveCount(0);
    await expect(checkbox(first, 'same.png')).toBeFocused();
    expect(await cards(page).evaluateAll(nodes => nodes.map(node => node.classList.contains('selected')))).toEqual(selectedBefore);
    // The count updates in place: the same bar and count nodes.
    await bulkbar(page).locator('.bulkbar > strong').evaluate(node => { node.dataset.kept = 'yes'; });
    await checkbox(second, 'same.png').check();
    await expect(bulkbar(page).locator('.bulkbar > strong')).toHaveText('2 selected');
    await expect(bulkbar(page).locator('.bulkbar > strong')).toHaveAttribute('data-kept', 'yes');
    // Set category opens its dialog; Cancel keeps the selection.
    await bulkbar(page).getByRole('button', { name: 'Set category', exact: true }).click();
    await page.getByRole('dialog', { name: 'Categorize selected images', exact: true }).getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(page.locator('dialog[open]')).toHaveCount(0);
    await expect(bulkbar(page).locator('.bulkbar > strong')).toHaveText('2 selected');
    await checkbox(second, 'same.png').uncheck();
    await expect(bulkbar(page).locator('.bulkbar > strong')).toHaveText('1 selected');
    await checkbox(second, 'same.png').check();
    await bulkbar(page).getByRole('button', { name: 'Clear', exact: true }).click();
    await expect(bulkbar(page)).toBeEmpty();
    // The pressed control hid with its bar: focus moves to the page heading, not to the page.
    await expect(page.locator('#workspace-main h1')).toBeFocused();
    await expect(checkbox(first, 'same.png')).not.toBeChecked();
    await expect(checkbox(second, 'same.png')).not.toBeChecked();
});

test('the card checkbox keeps its own Space and Tab', async ({ page }) => {
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(2);
    const first = cards(page).first();
    await first.getByRole('button', { name: 'Open same.png', exact: true }).focus();
    await page.keyboard.press('Tab');
    await expect(checkbox(first, 'same.png')).toBeFocused();
    await page.keyboard.press('Space');
    await expect(checkbox(first, 'same.png')).toBeChecked();
    await expect(bulkbar(page).locator('.bulkbar > strong')).toHaveText('1 selected');
    await expect(page.locator('dialog[open]')).toHaveCount(0);
    await page.keyboard.press('Tab');
    await expect(star(first, 'same.png')).toBeFocused();
    await page.keyboard.press('Shift+Tab');
    await expect(checkbox(first, 'same.png')).toBeFocused();
    await page.keyboard.press('Shift+Tab');
    await expect(first.getByRole('button', { name: 'Open same.png', exact: true })).toBeFocused();
    // An arrow key on the checkbox still moves between cards, as from the card's other controls.
    await checkbox(first, 'same.png').focus();
    await page.keyboard.press('ArrowRight');
    await expect(cards(page).last().getByRole('button', { name: 'Open same.png', exact: true })).toBeFocused();
    await expect(checkbox(first, 'same.png')).toBeChecked();
});

test('the selection survives scrolling, re-mounting and appended batches, and a search clears it', async ({ page, request }) => {
    await page.addInitScript(() => { window.IntersectionObserver = undefined; });
    await expect(await request.post('/__test__/reset', { data: { extra_images: 200 } })).toBeOK();
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(40);
    const firstKey = await cards(page).first().getAttribute('data-image-key');
    await cards(page).first().getByRole('checkbox', { name: /^Select / }).check();
    await expect(bulkbar(page).locator('.bulkbar > strong')).toHaveText('1 selected');
    const more = page.getByRole('button', { name: 'Load more images', exact: true });
    for (const loaded of [80, 120, 160, 200, 202]) {
        await more.click();
        await expect(caption(page)).toContainText(`${loaded} of 202 images`);
        await expect(bulkbar(page).locator('.bulkbar > strong')).toHaveText('1 selected');
    }
    // A card from a later batch joins the kept selection.
    const late = page.locator('article[data-image-key][data-index="200"]');
    await late.getByRole('checkbox', { name: /^Select / }).check();
    await expect(bulkbar(page).locator('.bulkbar > strong')).toHaveText('2 selected');
    // Bounded rendering unmounted the first card; scrolled back, it is mounted again with its box checked.
    await expect(page.locator(`article[data-image-key="${firstKey}"]`)).toHaveCount(0);
    await page.evaluate(() => window.scrollTo(0, 0));
    const again = page.locator(`article[data-image-key="${firstKey}"]`);
    await expect(again).toBeVisible();
    await expect(again.getByRole('checkbox', { name: /^Select / })).toBeChecked();
    await expect(cards(page).nth(1).getByRole('checkbox', { name: /^Select / })).not.toBeChecked();
    // Returning to Images keeps the restored collection and its selection.
    await page.evaluate(() => { window.location.hash = '/overview'; });
    await expect(page.locator('#workspace-main h1')).toHaveText('Overview');
    await page.evaluate(() => { window.location.hash = '/images'; });
    await expect(page.locator('#workspace-main h1')).toHaveText('Images');
    await expect(bulkbar(page).locator('.bulkbar > strong')).toHaveText('2 selected');
    // A search replaces the collection, and with it the selection.
    await search(page).fill('0001');
    await expect(caption(page)).not.toContainText('202 images');
    await expect(bulkbar(page)).toBeEmpty();
    await search(page).fill('');
    await expect(caption(page)).toContainText('of 202 images');
    await expect(bulkbar(page)).toBeEmpty();
    await expect(page.locator(`article[data-image-key="${firstKey}"]`).getByRole('checkbox', { name: /^Select / })).not.toBeChecked();
    // A sort change replaces the collection too.
    await cards(page).first().getByRole('checkbox', { name: /^Select / }).check();
    await expect(bulkbar(page).locator('.bulkbar > strong')).toHaveText('1 selected');
    await sortBy(page).selectOption('oldest');
    await expect(bulkbar(page)).toBeEmpty();
    await expect(cards(page).getByRole('checkbox', { checked: true })).toHaveCount(0);
});

test('the card checkbox does not open the viewer', async ({ page }) => {
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(2);
    const first = cards(page).first();
    const selectedBefore = await cards(page).evaluateAll(nodes => nodes.map(node => node.classList.contains('selected')));
    const inspectorBefore = await details(page).innerText();
    // A press on the label around the box toggles it, as does a press on the box itself.
    await first.locator('.image-overlays > label').click({ position: { x: 1, y: 1 } });
    await expect(checkbox(first, 'same.png')).toBeChecked();
    await expect(bulkbar(page).locator('.bulkbar > strong')).toHaveText('1 selected');
    await checkbox(first, 'same.png').click();
    await expect(checkbox(first, 'same.png')).not.toBeChecked();
    await expect(page.locator('dialog[open]')).toHaveCount(0);
    await expect(page.locator('#viewer-dialog[open]')).toHaveCount(0);
    // Neither press selected the card or inspected it.
    expect(await cards(page).evaluateAll(nodes => nodes.map(node => node.classList.contains('selected')))).toEqual(selectedBefore);
    expect(await details(page).innerText()).toBe(inspectorBefore);
});

// #249 D8 follow-up (image selection, Task 2): Export metadata downloads one bundle of the inspector's envelopes.
const exportButton = page => bulkbar(page).getByRole('button', { name: 'Export metadata', exact: true });
const inspectRoute = '**/prompt_manager/images/inspect/**';
const atRoot = (route, root) => new URL(route.request().url()).searchParams.get('root') === root;
async function checkBoth(page) {
    await expect(cards(page)).toHaveCount(2);
    await checkbox(cards(page).first(), 'same.png').check();
    await checkbox(cards(page).last(), 'same.png').check();
    await expect(bulkbar(page).locator('.bulkbar > strong')).toHaveText('2 selected');
}
async function bundleOf(download) {
    const fs = await import('node:fs/promises');
    return JSON.parse(await fs.readFile(await download.path(), 'utf8'));
}

test('Export metadata downloads one bundle of the checked images in listing order and keeps the selection', async ({ page }) => {
    // Record the Blob URLs the page creates and revokes, to prove the download's URL is released.
    await page.addInitScript(() => {
        const blobs = window.__blobs = { created: [], revoked: [] };
        const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
        URL.createObjectURL = value => { const url = create(value); blobs.created.push(url); return url; };
        URL.revokeObjectURL = url => { blobs.revoked.push(url); revoke(url); };
    });
    await page.goto(workspace);
    await checkBoth(page);
    const keys = await cards(page).evaluateAll(nodes => nodes.map(node => node.dataset.imageKey));
    // Hold both reads: the button is busy and a second press starts nothing more.
    const held = [], reads = [];
    await page.route(inspectRoute, route => { reads.push(route.request().url()); held.push(route); });
    const downloads = [];
    page.on('download', download => downloads.push(download));
    await exportButton(page).click();
    await expect.poll(() => held.length).toBe(2);
    await expect(exportButton(page)).toHaveAttribute('aria-disabled', 'true');
    // Playwright waits on an aria-disabled control, so the second press is a dispatched click.
    await exportButton(page).dispatchEvent('click');
    await page.evaluate(() => new Promise(requestAnimationFrame));
    const saving = page.waitForEvent('download');
    for (const route of held) await route.continue();
    const download = await saving;
    expect(download.suggestedFilename()).toBe('prompt-manager-images-metadata.json');
    const bundle = await bundleOf(download);
    expect([bundle.format, bundle.version]).toEqual(['prompt-manager-images-metadata', 1]);
    expect(Number.isNaN(Date.parse(bundle.exported_at))).toBe(false);
    expect(bundle.images.map(image => image.identity.key)).toEqual(keys);
    expect(bundle.images.map(image => image.identity.path.split('/').at(-1))).toEqual(['same.png', 'same.png']);
    expect(bundle.images.every(image => image.inspection_version === 1 && image.metadata.prompt_graph === undefined)).toBe(true);
    await expect(toast(page)).toHaveText('Exported metadata for 2 images.');
    await expect(exportButton(page)).not.toHaveAttribute('aria-disabled', 'true');
    expect(reads).toHaveLength(2);
    expect(downloads).toHaveLength(1);
    // The bundle's one Blob URL is revoked once the download started.
    await expect.poll(() => page.evaluate(() => window.__blobs)).toEqual({ created: [expect.stringMatching(/^blob:/)], revoked: [expect.stringMatching(/^blob:/)] });
    const blobs = await page.evaluate(() => window.__blobs);
    expect(blobs.revoked).toEqual(blobs.created);
    // The selection is kept after an export.
    await expect(bulkbar(page).locator('.bulkbar > strong')).toHaveText('2 selected');
    await expect(cards(page).getByRole('checkbox', { checked: true })).toHaveCount(2);
});

test('Export metadata reports images that could not be read and exports the rest', async ({ page }) => {
    await page.goto(workspace);
    await checkBoth(page);
    await page.route(inspectRoute, route => atRoot(route, '1') ?
        route.fulfill({ status: 503, json: { success: false, error: 'Unavailable' } }) : route.continue());
    const [download] = await Promise.all([page.waitForEvent('download'), exportButton(page).click()]);
    const bundle = await bundleOf(download);
    expect(bundle.images.map(image => image.identity.key)).toEqual(['0:shared/same.png']);
    await expect(toast(page)).toHaveText('Exported metadata for 1 of 2 images. 1 could not be read.');
    await expect(bulkbar(page).locator('.bulkbar > strong')).toHaveText('2 selected');
});

test('Export metadata downloads nothing when no image can be read', async ({ page }) => {
    await page.goto(workspace);
    await checkBoth(page);
    await page.route(inspectRoute, route => route.fulfill({ status: 503, json: { success: false, error: 'Unavailable' } }));
    const downloads = [];
    page.on('download', download => downloads.push(download));
    await exportButton(page).click();
    await expect(toast(page)).toHaveText('Metadata could not be read. Try again.');
    await expect(exportButton(page)).not.toHaveAttribute('aria-disabled', 'true');
    expect(downloads).toHaveLength(0);
    await expect(bulkbar(page).locator('.bulkbar > strong')).toHaveText('2 selected');
});

// #249 D8 follow-up (image selection, Task 3): Set category sets the linked positives' category (app.js simpleInput).
const categoryButton = page => bulkbar(page).getByRole('button', { name: 'Set category', exact: true });
const categoryDialog = page => page.getByRole('dialog', { name: 'Categorize selected images', exact: true });
const categoryRoute = '**/prompt_manager/bulk/category';
/** Unlink the input root's card by deleting its prompt; returns the output card's positive id. */
async function unlinkInputCard(request) {
    const { images } = await (await request.get('/prompt_manager/images/output?mode=workspace')).json();
    const byKey = Object.fromEntries(images.map(image => [`${image.root_index}:${image.path}`, image]));
    const [output, input] = [byKey['0:shared/same.png'], byKey['1:shared/same.png']];
    expect([output.linked.status, input.linked.status]).toEqual(['linked', 'linked']);
    await expect(await request.delete(`/prompt_manager/delete/${input.linked.positive_id}`)).toBeOK();
    return output.linked.positive_id;
}

test('Set category applies the category to the linked positives and the category filter then matches', async ({ page, request }) => {
    const outputPositive = await unlinkInputCard(request);
    await page.goto(workspace);
    await checkBoth(page);
    const opener = categoryButton(page);
    await opener.click();
    const dialog = categoryDialog(page);
    await expect(dialog).toBeVisible();
    // The concept's simpleInput: one field and Cancel / Apply (primary).
    await expect(dialog.locator('#modal-body > .field > label[for="action-input"]')).toHaveText('Category');
    await expect(dialog.locator('#modal-body > .field > input#action-input')).toHaveValue('');
    await expect(dialog.locator('#modal-body > .dialog-actions > button')).toHaveText(['Cancel', 'Apply']);
    await expect(dialog.getByRole('button', { name: 'Apply', exact: true })).toHaveClass(/ca-primary/);
    await expect(dialog.getByLabel('Category', { exact: true })).toBeFocused();
    // An empty value applies nothing and keeps the dialog open.
    const posts = [];
    page.on('request', item => { if (item.method() === 'POST' && item.url().endsWith('/prompt_manager/bulk/category')) posts.push(item.postDataJSON()); });
    await dialog.getByRole('button', { name: 'Apply', exact: true }).click();
    await expect(dialog).toBeVisible();
    await dialog.getByLabel('Category', { exact: true }).fill('  Landscapes ');
    const listings = [];
    page.on('request', item => { if (imagePages(new URL(item.url()))) listings.push(item.url()); });
    await dialog.getByRole('button', { name: 'Apply', exact: true }).click();
    await expect(dialog).toBeHidden();
    await expect(toast(page)).toHaveText('Category set on 1 prompt. 1 selected image had no linked prompt and was skipped.');
    expect(posts).toEqual([{ prompt_ids: [outputPositive], category: 'Landscapes' }]);
    // The listing and the category options reload; the reload clears the selection.
    await expect.poll(() => listings.length).toBeGreaterThan(0);
    await expect(bulkbar(page)).toBeEmpty();
    await expect(page.locator('#workspace-main h1')).toBeFocused();
    await expect(cards(page).getByRole('checkbox', { checked: true })).toHaveCount(0);
    const filter = page.getByRole('combobox', { name: 'Filter by category', exact: true });
    await expect(filter.locator('option')).toContainText(['Landscapes']);
    await filter.selectOption('Landscapes');
    await expect(cards(page)).toHaveCount(1);
    await expect(cards(page)).toHaveAttribute('data-image-key', '0:shared/same.png');
});

test('Set category without a linked image posts nothing and opens no dialog', async ({ page, request }) => {
    await unlinkInputCard(request);
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(2);
    let posted = 0;
    await page.route(categoryRoute, route => { posted += 1; return route.continue(); });
    await checkbox(cards(page).last(), 'same.png').check();
    await categoryButton(page).click();
    await expect(toast(page)).toHaveText("None of the selected images is linked to a saved prompt, so there is no prompt category to set.");
    await expect(page.locator('dialog[open]')).toHaveCount(0);
    expect(posted).toBe(0);
    await expect(bulkbar(page).locator('.bulkbar > strong')).toHaveText('1 selected');
});

test('Set category reports a failure, keeps the selection and ignores a second press while it runs', async ({ page }) => {
    await page.goto(workspace);
    await checkBoth(page);
    const held = [];
    await page.route(categoryRoute, route => { held.push(route); });
    await categoryButton(page).click();
    await categoryDialog(page).getByLabel('Category', { exact: true }).fill('Landscapes');
    await categoryDialog(page).getByRole('button', { name: 'Apply', exact: true }).click();
    await expect.poll(() => held.length).toBe(1);
    await expect(categoryButton(page)).toHaveAttribute('aria-disabled', 'true');
    // Playwright waits on an aria-disabled control, so the second press is a dispatched click.
    await categoryButton(page).dispatchEvent('click');
    await expect(page.locator('dialog[open]')).toHaveCount(0);
    await held[0].fulfill({ status: 500, json: { success: false, error: 'boom' } });
    await expect(toast(page)).toHaveText('Category could not be set. Try again.');
    await expect(categoryButton(page)).not.toHaveAttribute('aria-disabled', 'true');
    expect(held).toHaveLength(1);
    await expect(bulkbar(page).locator('.bulkbar > strong')).toHaveText('2 selected');
    await expect(cards(page).getByRole('checkbox', { checked: true })).toHaveCount(2);
});

test('leaving Images during an export downloads nothing and shows no toast', async ({ page }) => {
    await page.goto(workspace);
    await checkBoth(page);
    const held = [];
    await page.route(inspectRoute, route => { held.push(route); });
    const downloads = [];
    page.on('download', download => downloads.push(download));
    await exportButton(page).click();
    await expect.poll(() => held.length).toBe(2);
    await page.evaluate(() => { window.location.hash = '/overview'; });
    await expect(page.locator('#workspace-main h1')).toHaveText('Overview');
    // The reads were aborted with the page; answering them now changes nothing.
    for (const route of held) await route.continue().catch(() => {});
    await page.evaluate(() => new Promise(resolve => setTimeout(resolve, 150)));
    expect(downloads).toHaveLength(0);
    await expect(toast(page)).not.toContainText('etadata');
});

test('leaving Images during Set category finishes the write, and the return reloads the collection', async ({ page }) => {
    await page.goto(workspace);
    await checkBoth(page);
    const held = [];
    await page.route(categoryRoute, route => { held.push(route); });
    await categoryButton(page).click();
    await categoryDialog(page).getByLabel('Category', { exact: true }).fill('Landscapes');
    await categoryDialog(page).getByRole('button', { name: 'Apply', exact: true }).click();
    await expect.poll(() => held.length).toBe(1);
    await page.evaluate(() => { window.location.hash = '/overview'; });
    await expect(page.locator('#workspace-main h1')).toHaveText('Overview');
    // The write is not aborted with the page: the server still receives and answers it.
    const answered = page.waitForResponse(response => response.url().endsWith('/prompt_manager/bulk/category'));
    await held[0].continue();
    expect((await answered).ok()).toBe(true);
    await page.evaluate(() => new Promise(resolve => setTimeout(resolve, 150)));
    await expect(toast(page)).not.toContainText('Category');
    // The saved visit predates the write, so the return loads the collection afresh: no stale selection or categories.
    await page.evaluate(() => { window.location.hash = '/images'; });
    await expect(page.locator('#workspace-main h1')).toHaveText('Images');
    await expect(cards(page)).toHaveCount(2);
    await expect(bulkbar(page)).toBeEmpty();
    await expect(cards(page).getByRole('checkbox', { checked: true })).toHaveCount(0);
    await expect(page.getByRole('combobox', { name: 'Filter by category', exact: true }).locator('option')).toContainText(['Landscapes']);
});

test('Set category limits the field to the server length, shows the server count, and a rejection keeps the selection', async ({ page }) => {
    await page.goto(workspace);
    await checkBoth(page);
    const answers = [
        { status: 400, json: { success: false, error: 'Category cannot exceed 100 characters' } },
        { status: 200, json: { success: true, updated_count: 0 } },
    ];
    await page.route(categoryRoute, route => route.fulfill(answers.shift()));
    for (const expected of ['Category could not be set. Try again.', 'Category set on 0 prompts.']) {
        await categoryButton(page).click();
        const field = categoryDialog(page).getByLabel('Category', { exact: true });
        await expect(field).toHaveAttribute('maxlength', '100');
        await field.fill('Landscapes');
        await categoryDialog(page).getByRole('button', { name: 'Apply', exact: true }).click();
        await expect(toast(page)).toHaveText(expected);
        if (answers.length) await expect(bulkbar(page).locator('.bulkbar > strong')).toHaveText('2 selected');
    }
    // The success reload clears the selection.
    await expect(bulkbar(page)).toBeEmpty();
    expect(answers).toHaveLength(0);
});

// Activity-1 Task 3 (D55): Import → "Scan for metadata" → Review scan opens the concept's "Review metadata scan".
const scanReview = page => page.getByRole('dialog', { name: 'Review metadata scan', exact: true });
async function openScanReview(page) {
    await page.getByRole('button', { name: 'Import', exact: true }).click();
    const row = importDialog(page).locator('.operation').filter({ has: page.getByRole('heading', { name: 'Scan for metadata', exact: true }) });
    await row.getByRole('button', { name: 'Review scan', exact: true }).click();
    await expect(scanReview(page)).toBeVisible();
}

test('scan for metadata opens the review dialog from Import', async ({ page, request }) => {
    await expect(await request.post('/__test__/jobs/restart')).toBeOK();
    await page.setViewportSize({ width: 1600, height: 1100 });
    await page.goto(workspace);
    await expect(cards(page)).toHaveCount(2);
    // Rescan stays the quick refresh; the metadata scan is its own row under it.
    await page.getByRole('button', { name: 'Import', exact: true }).click();
    await expect(importDialog(page).locator('.operation h3')).toHaveText(['Inspect a local image', 'Scan an output folder', 'Scan for metadata']);
    await expect(importDialog(page).locator('.operation').nth(2).locator('p')).toHaveText('Discover and enrich existing files using the configured sources.');
    await page.keyboard.press('Escape');
    await openScanReview(page);
    const dialog = scanReview(page);
    await expect(dialog.locator('dl.meta-grid > div > dt')).toHaveText(['Sources', 'Scope']);
    await expect(dialog.locator('dl.meta-grid > div > dd')).toHaveText(['2 output folders', 'New and unresolved files']);
    await expect(dialog.locator('#modal-body > p.small')).toHaveText('Enrich positive / negative associations, reuse canonical text and report unchanged files. Do not inflate run counts or recreate existing thumbnails.');
    await expect(dialog.locator('.notice h3')).toHaveText('Back up the database first (recommended)');
    await expect(dialog.locator('.dialog-actions button')).toHaveText(['Cancel', 'Start scan']);
    await expect(dialog.getByRole('button', { name: 'Start scan', exact: true })).toHaveClass(/ca-primary/);

    // Back up now downloads the existing backup, toasts, and keeps the dialog open for the scan.
    const download = page.waitForEvent('download');
    await dialog.getByRole('button', { name: 'Back up now', exact: true }).click();
    expect((await download).suggestedFilename()).toMatch(/^prompts_backup_\d{8}_\d{6}\.db$/);
    await expect(toast(page)).toHaveText('Database backup downloaded.');
    await expect(dialog).toBeVisible();
    // A failed backup says so and downloads nothing.
    await page.route('**/prompt_manager/backup', route => route.fulfill({ status: 500, json: { success: false, error: 'disk full' } }));
    await dialog.getByRole('button', { name: 'Back up now', exact: true }).click();
    await expect(toast(page)).toHaveText('The database could not be backed up. Try again.');

    // Cancel closes it and starts nothing.
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(scanReview(page)).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Import', exact: true })).toBeFocused();
    const jobs = await (await request.get('/prompt_manager/jobs')).json();
    expect(jobs.jobs).toEqual([]);
});
