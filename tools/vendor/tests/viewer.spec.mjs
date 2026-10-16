import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { repositoryRoot as root } from '../vendor-viewerjs.mjs';

const source = file => readFileSync(join(root, file), 'utf8');

async function waitForImage(page, index) {
    await expect.poll(() => page.evaluate(() => window.gallery.viewer?.viewed)).toBe(true);
    await expect.poll(() => page.evaluate(() => window.gallery.lastMetadata)).toContain(`/original/${index}.svg`);
}

async function loadGallery(page) {
    await page.addScriptTag({ content: source('web/js/gallery.js') + '\nwindow.TestGallery = GalleryManager;' });
    await page.evaluate(() => {
        const manager = window.gallery = Object.create(TestGallery.prototype);
        Object.assign(manager, {
            viewer: null, viewMode: 'grid',
            images: Array.from({ length: 2000 }, (_, i) => ({ filename: `${i}.svg`,
                url: `http://viewer.test/original/${i}.svg`, thumbnail_url: `http://viewer.test/thumb/${i}.svg`,
                relative_path: `${i}.svg`, size: 100, modified_time: 1 })),
            getSettings: () => ({ showImageInfo: true }),
            loadImageMetadata(url, content) { this.lastMetadata = url; content.textContent = url; },
        });
        manager.renderGallery();
    });
}

test.beforeEach(async ({ page }) => {
    page.viewerErrors = [];
    page.on('pageerror', error => page.viewerErrors.push(error.message));
    await page.route('http://viewer.test/**', route => route.request().resourceType() === 'document'
        ? route.fulfill({ contentType: 'text/html', body: '<head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><button id="outside">Outside</button><div id="galleryGrid"></div><div id="galleryList"></div><div id="loadingState"></div><div id="errorState"></div><div id="galleryContent"></div><div id="galleryEmpty"></div><h1 id="galleryTitle"></h1></body>' })
        : route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128"><rect width="128" height="128" fill="teal"/></svg>' }));
    await page.goto('http://viewer.test/');
    await page.addStyleTag({ path: join(root, 'web/lib/tailwind/comfyui-theme.css') });
    await page.addStyleTag({ path: join(root, 'web/lib/tailwind/styles.css') });
    await page.addStyleTag({ path: join(root, 'web/lib/viewerjs/viewer.min.css') });
    await page.addStyleTag({ content: '.hidden{display:none} #galleryGrid{display:grid;grid-template-columns:repeat(8,1fr)} #galleryGrid img{width:100px;height:100px}' });
    for (const file of ['web/lib/viewerjs/viewer.min.js', 'web/js/html-escape.js', 'web/js/image-helpers.js', 'web/js/api-client.js']) {
        await page.addScriptTag({ path: join(root, file) });
    }
});

test.afterEach(async ({ page }) => {
    expect(page.viewerErrors).toEqual([]);
});

test('gallery retains cards and navigates across bounded windows with correct metadata', async ({ page }) => {
    await loadGallery(page);
    await page.locator('[data-image-index="1500"]').click();
    await waitForImage(page, 1500);
    expect(await page.evaluate(() => gallery.viewer.length)).toBeLessThanOrEqual(100);
    expect(await page.evaluate(() => {
        const first = document.querySelector('.image-item');
        gallery.images.push({ ...gallery.images[0], filename: 'appended.svg' });
        gallery.renderGallery({ from: 2000 });
        return first === document.querySelector('.image-item');
    })).toBe(true);
    await page.evaluate(() => gallery.viewer.view(99));
    await waitForImage(page, 1549);
    await page.keyboard.press('ArrowRight');
    await waitForImage(page, 1550);
    await page.keyboard.press('ArrowLeft');
    await waitForImage(page, 1549);
    await page.keyboard.press('Escape');
    await expect.poll(() => page.evaluate(() => gallery.viewer.isShown)).toBe(false);
    await expect(page.locator('#metadata-sidebar')).toHaveCount(0);
    await page.locator('#outside').focus();
    await expect(page.locator('#outside')).toBeFocused();
});

test('gallery slideshow advances without preloading the collection and cleans up on close', async ({ page }) => {
    await loadGallery(page);
    await page.evaluate(() => gallery.openViewer(1500));
    await waitForImage(page, 1500);
    await page.clock.install();
    await page.locator('[data-viewer-action="play"]').click();
    expect(await page.evaluate(() => gallery.slideshowPlaying)).toBe(true);
    await expect(page.locator('.viewer-player img')).toHaveCount(0);
    await page.clock.fastForward(5000);
    await waitForImage(page, 1501);
    await page.locator('[data-viewer-action="play"]').click();
    expect(await page.evaluate(() => gallery.slideshowPlaying)).toBe(false);
    await page.keyboard.press('Escape');
    await expect.poll(() => page.evaluate(() => gallery.viewer.isShown)).toBe(false);
    await page.evaluate(() => gallery.destroyViewer());
    await expect(page.locator('.viewer-container')).toHaveCount(0);
});

test('admin gallery and filmstrip use the real viewer events and destroy temporary containers', async ({ page }) => {
    // Exercise the production class without starting the ComfyUI dashboard bootstrap.
    const adminSource = source('web/js/admin.js');
    await page.addScriptTag({ content: adminSource.slice(0, adminSource.indexOf('        function startAdmin()')) + '\nwindow.TestAdmin = PromptAdmin;' });
    await page.evaluate(async () => {
        const admin = window.admin = Object.create(TestAdmin.prototype);
        const images = Array.from({ length: 3 }, (_, i) => ({ filename: `${i}.svg`, url: `http://viewer.test/original/${i}.svg`, size: 100, created_at: 1 }));
        Object.assign(admin, {
            prompts: [{ id: 1, text: 'test' }],
            getImageUrl: image => image.url,
            loadFilmStripImages: async () => images,
            addMetadataSidebar() {},
            loadMetadataForImage(image) { this.lastMetadata = image.src; },
        });
        await admin.openFilmStripViewer(1, 1);
    });
    await expect.poll(() => page.evaluate(() => admin.lastMetadata)).toContain('/1.svg');
    await page.keyboard.press('ArrowRight');
    await expect.poll(() => page.evaluate(() => admin.lastMetadata)).toContain('/2.svg');
    await page.keyboard.press('Escape');
    await expect(page.locator('#filmStripViewerContainer')).toHaveCount(0);
    await expect(page.locator('.viewer-container')).toHaveCount(0);
    await page.evaluate(() => admin.renderGallery([{ filename: 'a.svg', url: 'http://viewer.test/original/0.svg', size: 100, created_at: 1 }], 1));
    await expect.poll(() => page.evaluate(() => Boolean(admin.galleryViewer))).toBe(true);
    await page.evaluate(() => admin.galleryViewer.show());
    await expect.poll(() => page.evaluate(() => admin.galleryViewer.viewed)).toBe(true);
    await page.keyboard.press('Escape');
    await expect.poll(() => page.evaluate(() => admin.galleryViewer.isShown)).toBe(false);
    await page.evaluate(() => admin.galleryViewer.destroy());
});

test('tag viewer opens at the selected image and cleans up on Escape', async ({ page }) => {
    await page.addScriptTag({ path: join(root, 'web/js/tags-page.js') });
    await page.evaluate(async () => {
        const tags = Object.create(TagsPageManager.prototype);
        tags.admin = { getImageUrl: image => image.url };
        await tags.openImageViewer({ images: [0, 1, 2].map(i => ({ filename: `${i}.svg`, url: `http://viewer.test/original/${i}.svg` })), image_count: 3 }, 1);
    });
    await expect(page.locator('.viewer-canvas img')).toHaveAttribute('src', /\/1.svg$/);
    await page.keyboard.press('ArrowRight');
    await expect(page.locator('.viewer-canvas img')).toHaveAttribute('src', /\/2.svg$/);
    await page.keyboard.press('Escape');
    await expect(page.locator('.viewer-container')).toHaveCount(0);
});

test('touch swipes navigate the gallery and restore short drags', async ({ page, isMobile }) => {
    test.skip(!isMobile, 'Touch interaction project');
    await loadGallery(page);
    await page.evaluate(() => gallery.openViewer(1500));
    await waitForImage(page, 1500);
    await page.locator('.metadata-collapse-btn').click();
    await expect(page.locator('.metadata-sidebar')).toHaveClass(/collapsed/);
    const session = await page.context().newCDPSession(page);
    const width = await page.evaluate(() => gallery.viewer.viewerData.width);
    const swipe = async distance => {
        await session.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: width * 0.9, y: 220 }] });
        for (let step = 1; step <= 8; step++) {
            await session.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: width * 0.9 - distance * step / 8, y: 220 }] });
        }
        await session.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    };
    await swipe(width * 0.8);
    await waitForImage(page, 1501);
    const initialX = await page.evaluate(() => gallery.viewer.imageData.x);
    await swipe(width * 0.1);
    await waitForImage(page, 1501);
    await expect.poll(() => page.evaluate(() => gallery.viewer.imageData.x)).toBe(initialX);
});

test('touch pinch zoom and rotation remain usable', async ({ page, isMobile }) => {
    test.skip(!isMobile, 'Touch interaction project');
    await loadGallery(page);
    await page.evaluate(() => gallery.openViewer(1500));
    await waitForImage(page, 1500);
    await page.locator('.metadata-collapse-btn').click();
    await expect(page.locator('.metadata-sidebar')).toHaveClass(/collapsed/);
    const session = await page.context().newCDPSession(page);
    const points = (radius, angle = 0) => [-1, 1].map((sign, id) => ({
        id, x: 400 + sign * radius * Math.cos(angle), y: 220 + sign * radius * Math.sin(angle),
    }));
    await session.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: points(30) });
    for (let step = 1; step <= 6; step++) {
        await session.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: points(30 + step * 8) });
    }
    await session.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await expect.poll(() => page.evaluate(() => gallery.viewer.imageData.ratio > 1.2)).toBe(true);
    await session.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: points(70) });
    for (let step = 1; step <= 8; step++) {
        await session.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: points(70, Math.PI / 2 * step / 8) });
    }
    await session.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await expect.poll(() => page.evaluate(() => Math.abs(gallery.viewer.imageData.rotate))).toBe(90);
});
