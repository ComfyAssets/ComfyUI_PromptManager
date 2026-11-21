// The vendored Viewer.js build on a synthetic page, without ComfyUI or Python: the browser check the Viewer.js update
// workflow runs before it opens its pull request. The workspace's own viewer (web/js/workspace/media-viewer.mjs) is
// proven against the real server by tests/browser/workspace-viewer-*.spec.mjs and the Images and history specs. The
// old pages' viewer tests went with those pages (#668).
import { test, expect } from '@playwright/test';
import { join } from 'node:path';
import { repositoryRoot as root } from '../vendor-viewerjs.mjs';

const IMAGE = '<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128"><rect width="128" height="128" fill="teal"/></svg>';
const PAGE = '<head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><ul id="images">'
    + [0, 1, 2].map(index => `<li><img src="http://viewer.test/original/${index}.svg" alt="Image ${index}" width="100" height="100"></li>`).join('')
    + '</ul></body>';

/** The index Viewer.js reports once it has shown an image. */
const viewedIndex = page => page.evaluate(() => window.viewed.at(-1));

test.beforeEach(async ({ page }) => {
    page.viewerErrors = [];
    page.on('pageerror', error => page.viewerErrors.push(error.message));
    await page.route('http://viewer.test/**', route => route.request().resourceType() === 'document'
        ? route.fulfill({ contentType: 'text/html', body: PAGE })
        : route.fulfill({ contentType: 'image/svg+xml', body: IMAGE }));
    await page.goto('http://viewer.test/');
    await page.addStyleTag({ path: join(root, 'web/lib/viewerjs/viewer.min.css') });
    await page.addScriptTag({ path: join(root, 'web/lib/viewerjs/viewer.min.js') });
    await page.evaluate(() => {
        window.viewed = [];
        const list = document.getElementById('images');
        list.addEventListener('viewed', event => window.viewed.push(event.detail.index));
        list.addEventListener('hidden', () => { window.hidden = true; });
        window.viewer = new window.Viewer(list, { transition: false });
    });
});

test.afterEach(async ({ page }) => {
    expect(page.viewerErrors).toEqual([]);
});

test('the vendored Viewer.js opens at the clicked image, moves on with the keyboard and leaves nothing behind', async ({ page }) => {
    await page.locator('#images img').nth(1).click();
    await expect.poll(() => viewedIndex(page)).toBe(1);
    await expect(page.locator('.viewer-container')).toHaveClass(/viewer-in/);
    await expect(page.locator('.viewer-canvas img')).toHaveAttribute('src', 'http://viewer.test/original/1.svg');
    await page.keyboard.press('ArrowRight');
    await expect.poll(() => viewedIndex(page)).toBe(2);
    await page.keyboard.press('Escape');
    await expect.poll(() => page.evaluate(() => window.hidden)).toBe(true);
    await page.evaluate(() => window.viewer.destroy());
    await expect(page.locator('.viewer-container')).toHaveCount(0);
    await expect(page.locator('body')).not.toHaveClass(/viewer-open/);
});

test('a touch swipe moves the vendored Viewer.js to the next image', async ({ page, isMobile }) => {
    test.skip(!isMobile, 'Touch interaction project');
    await page.evaluate(() => window.viewer.view(0));
    await expect.poll(() => viewedIndex(page)).toBe(0);
    const session = await page.context().newCDPSession(page);
    const { width, height } = page.viewportSize();
    await session.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: width * 0.9, y: height / 2 }] });
    for (let step = 1; step <= 8; step += 1) {
        await session.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: width * 0.9 - width * 0.8 * step / 8, y: height / 2 }] });
    }
    await session.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await expect.poll(() => viewedIndex(page)).toBe(1);
});
