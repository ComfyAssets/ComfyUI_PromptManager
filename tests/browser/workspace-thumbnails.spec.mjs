import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';

/**
 * #249: cards and strips load previews, never the full file. The approved mock has no repeated thumbnail prompt
 * (docs/concepts/workspace/README.md), so a preview that was not made yet is made by the server on first view:
 * the listing still names the `_thumb` URL. Only the media viewer's main image (and downloads) load the original.
 */
const images = '/prompt_manager/workspace#/images';
const library = '/prompt_manager/workspace#/library';
const cards = page => page.locator('article[data-image-key]');
const details = page => page.getByRole('complementary', { name: 'Selected image details', exact: true });
const SERVE = '/prompt_manager/images/serve/';

/** Every media file the page asks the server for, as `{path, preview}` (path relative to its root). */
function recordMedia(page) {
    const seen = [];
    page.on('request', request => {
        const url = new URL(request.url());
        if (!url.pathname.startsWith(SERVE)) return;
        const path = decodeURIComponent(url.pathname.slice(SERVE.length));
        seen.push({ path, preview: /^thumbnails\/.+_thumb\.[a-z0-9]+$/.test(path) });
    });
    return seen;
}
/** Every card image has loaded (or failed): what it asked for is all in `seen`. */
const settled = page => page.evaluate(() => Promise.all([...document.querySelectorAll('article img')].map(image => image.decode().catch(() => {}))));
const originals = seen => seen.filter(item => !item.preview).map(item => item.path);

test.beforeEach(async ({ page, browserName }) => {
    await startCoverage(page, browserName);
    page.thumbErrors = [];
    page.on('pageerror', error => page.thumbErrors.push(error.message));
});
test.afterEach(async ({ page, browserName }) => {
    await saveCoverage(page, browserName);
    expect(page.thumbErrors).toEqual([]);
});

test('a page of the Images grid requests previews, never originals, in every layout and size', async ({ page, request }) => {
    await expect(await request.post('/__test__/reset')).toBeOK();
    // Newest outputs with no preview yet: the live finding (844 such files listed with a null thumbnail).
    await expect(await request.post('/__test__/system/unthumbnailed', { data: { count: 6 } })).toBeOK();
    const listing = await (await request.get('/prompt_manager/images/output?limit=40')).json();
    const fresh = listing.images.filter(image => image.path.startsWith('unthumbnailed/'));
    expect(fresh).toHaveLength(6);
    for (const image of fresh) expect(image.thumbnail_url).toMatch(/\/thumbnails\/unthumbnailed\/plain_\d{3}_thumb\.png\?root=0/);
    const seen = recordMedia(page);
    await page.goto(images);
    await expect(cards(page)).toHaveCount(8);
    await settled(page);
    expect(originals(seen)).toEqual([]);
    expect(seen.filter(item => item.path.startsWith('thumbnails/unthumbnailed/'))).toHaveLength(6);
    // The server made them on that first view.
    await expect.poll(async () => (await (await request.get('/prompt_manager/images/thumbnails/status')).json()).pending).toBe(0);
    for (const layout of ['List', 'Masonry', 'Grid']) {
        await page.getByRole('button', { name: layout, exact: true }).click();
        await expect(page.getByRole('button', { name: layout, exact: true })).toHaveAttribute('aria-pressed', 'true');
        for (const density of ['large', 'compact', 'comfortable']) {
            await page.getByRole('combobox', { name: 'Thumbnail size', exact: true }).selectOption(density);
            await expect(cards(page)).toHaveCount(8);
            await settled(page);
        }
    }
    expect(originals(seen)).toEqual([]);
});

test('the inspector cover is the preview and only the media viewer loads the original', async ({ page, request }) => {
    await expect(await request.post('/__test__/reset')).toBeOK();
    await expect(await request.post('/__test__/system/unthumbnailed', { data: { count: 1 } })).toBeOK();
    const seen = recordMedia(page);
    await page.goto(images);
    const card = cards(page).filter({ hasText: 'plain_001.png' });
    await card.getByRole('button', { name: 'Inspect plain_001.png', exact: true }).click();
    const cover = details(page).locator('img.detail-cover');
    await expect(cover).toHaveAttribute('src', /\/thumbnails\/unthumbnailed\/plain_001_thumb\.png\?/);
    await settled(page);
    await cover.evaluate(image => image.decode().catch(() => {}));
    expect(originals(seen)).toEqual([]);
    await card.getByRole('button', { name: 'Open plain_001.png', exact: true }).click();
    await expect(page.locator('dialog#viewer-dialog')).toBeVisible();
    await expect.poll(() => originals(seen)).toEqual(['unthumbnailed/plain_001.png']);
    await page.getByRole('button', { name: 'Close image viewer', exact: true }).click();
});

test('a video card shows its preview frame instead of loading the video', async ({ page, request }) => {
    await expect(await request.post('/__test__/reset', { data: { include_video: true } })).toBeOK();
    const seen = recordMedia(page);
    await page.goto(images);
    const card = cards(page).filter({ hasText: 'clip.webm' });
    await expect(card.locator('.media-art > .image-open > img')).toHaveAttribute('src', /\/thumbnails\/clip_thumb\.jpg\?/);
    await expect(card.locator('video')).toHaveCount(0);
    await settled(page);
    expect(originals(seen)).toEqual([]);
});

test('Library cards and the shared gallery request previews only', async ({ page, request }) => {
    await expect(await request.post('/__test__/reset', { data: { native_library: true } })).toBeOK();
    const seen = recordMedia(page);
    await page.goto(library);
    const cat = page.locator('article[data-prompt-id]').filter({ has: page.getByText('Cat portrait', { exact: true }) });
    await expect(cat.locator('.card-art img')).not.toHaveCount(0);
    await settled(page);
    expect(seen.length).toBeGreaterThan(0);
    expect(originals(seen)).toEqual([]);
});
