import { writeFile } from 'node:fs/promises';
import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from '../../tools/vendor/node_modules/@playwright/test/index.mjs';

test.use({ locale: 'en-US', timezoneId: 'UTC', reducedMotion: 'reduce' });
const workspace = '/prompt_manager/workspace';
const primary = ['images', 'library', 'tags', 'inspect'];
const tools = ['overview', 'system'];

async function noOverflow(page) {
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
}
async function groupedNavigation(page) {
    for (const [group, routes] of [['primary', primary], ['tools', tools]]) {
        const region = page.locator(`.workspace-nav-${group}`);
        await expect(region).toHaveCount(1);
        expect(await region.locator('[data-route]').evaluateAll(nodes => nodes.map(node => node.dataset.route))).toEqual(routes);
    }
}
async function attachEvidence(page, testInfo, name) {
    await page.evaluate(async () => {
        await document.fonts.ready;
        for (const image of document.images) image.loading = 'eager';
        await Promise.all([...document.images].map(image => image.decode().catch(() => {})));
    });
    const screenshotPath = testInfo.outputPath(`${name}.png`);
    await page.screenshot({ path: screenshotPath, fullPage: true });
    await testInfo.attach(name, { path: screenshotPath, contentType: 'image/png' });
    const geometry = await page.evaluate(() => Object.fromEntries(['.sidebar', '.topbar', '#workspace-main', '.workspace-heading', 'h1'].map(selector => {
        const node = document.querySelector(selector), box = node.getBoundingClientRect(), style = getComputedStyle(node);
        return [selector, { x: box.x, y: box.y, width: box.width, height: box.height, padding: style.padding, fontSize: style.fontSize }];
    })));
    const geometryPath = testInfo.outputPath(`${name}-geometry.json`);
    await writeFile(geometryPath, JSON.stringify(geometry, null, 2));
    await testInfo.attach(`${name}-geometry`, { path: geometryPath, contentType: 'application/json' });
}

test.beforeEach(async ({ page, request, browserName }) => {
    await startCoverage(page, browserName);
    await expect(await request.post('/__test__/reset')).toBeOK();
});
test.afterEach(async ({ page, browserName }) => { await saveCoverage(page, browserName); });

test('visual fixture exposes real stable media and library records', async ({ request }) => {
    expect((await request.post('/__test__/reset', { data: { workspace_visual: 'true' } })).status()).toBe(400);
    for (const option of ['extra_images', 'include_video', 'prompt_inspector', 'native_library', 'extra_prompts', 'extra_variants']) {
        expect((await request.post('/__test__/reset', { data: { workspace_visual: true, [option]: option.startsWith('extra') ? 1 : true } })).status()).toBe(400);
    }
    await expect(await request.post('/__test__/reset', { data: { workspace_visual: true } })).toBeOK();
    const images = await (await request.get('/prompt_manager/images/output?limit=40')).json();
    expect(images.total).toBe(24);
    expect(new Set(images.images.map(image => image.root_index))).toEqual(new Set([0, 1]));
    const image = images.images[0];
    await expect(await request.get(image.url)).toBeOK();
    const thumbnail = await request.get(image.thumbnail_url);
    await expect(thumbnail).toBeOK();
    expect(thumbnail.headers()['content-type']).toBe('image/png');
    const inspection = await (await request.get(`/prompt_manager/images/inspect/${encodeURIComponent(image.path)}?root=${image.root_index}`)).json();
    expect(inspection.success).toBe(true);
    expect(inspection.image.path).toBe(image.path);
    expect(inspection.prompts.positive.text).toContain('Amber geometry');
    expect(inspection.prompts.negative.text).toBe('blurred edges, watermark');
    expect(new Set(images.images.map(image => image.modified_time)).size).toBe(24);
    const library = await (await request.get('/prompt_manager/library')).json();
    expect(library.total).toBe(6);
    expect(library.items.reduce((sum, item) => sum + item.variant_count, 0)).toBe(10);
    const recipes = await Promise.all(library.items.map(async item => (await request.get(`/prompt_manager/library/${item.id}`)).json()));
    expect(recipes.flatMap(recipe => recipe.variants).some(variant => variant.negative_text === '')).toBe(true);
    const options = await (await request.get('/prompt_manager/library/options')).json();
    expect(options.success).toBe(true);
    await request.post('/__test__/reset', { data: { workspace_visual: true } });
    const repeated = await (await request.get('/prompt_manager/images/output?limit=40')).json();
    const identity = items => items.map(({root_index, path, modified_time}) => ({root_index, path, modified_time}));
    expect(identity(repeated.images)).toEqual(identity(images.images));
    await expect(await request.post('/__test__/reset')).toBeOK();
    expect((await (await request.get('/prompt_manager/stats')).json()).stats.total_prompts).toBe(2);
});

test('shell matches approved stable geometry in both themes', async ({ page, request }, testInfo) => {
    await request.post('/__test__/reset', { data: { workspace_visual: true } });
    for (const [width, height] of [[1600, 1100], [390, 844]]) {
        await page.setViewportSize({ width, height });
        for (const route of ['images', 'library']) {
            await page.goto(`${workspace}#/${route}`);
            if (width === 1600) await expect(page.locator('.sidebar')).toHaveCSS('width', '218px');
            await expect(page.locator('.workspace-heading > .workspace-heading-copy > h1')).toBeVisible();
            await groupedNavigation(page);
            await expect(page.locator(route === 'images' ? 'article[data-image-key]' : 'article[data-prompt-id]').first()).toBeVisible();
            await expect(page.locator('.brand')).toHaveAttribute('href', '#/images');
            await expect(page.locator('#workspace-main > .eyebrow')).toHaveCount(0);
            for (const [theme, active] of [['dark', 'rgb(20, 54, 77)'], ['light', 'rgb(228, 241, 251)']]) {
                await page.getByRole('combobox', { name: 'Appearance', exact: true }).selectOption(theme);
                await expect(page.locator('nav [aria-current="page"]')).toHaveCSS('background-color', active);
                await expect(page.locator('h1')).toHaveCSS('font-weight', '620');
                await expect(page.locator('h1')).toHaveCSS('letter-spacing', '-0.7px');
                await expect(page.locator('.workspace-heading')).toHaveCSS('margin-block-end', '22px');
                await expect(page.locator('h1')).toHaveCSS('font-size', width === 1600 ? '28px' : '25px');
                await expect(page.locator('#workspace-main')).toHaveCSS('padding-inline-start', width === 1600 ? '30px' : '14px');
                if (width === 1600) {
                    await expect(page.locator('.sidebar')).toHaveCSS('width', '218px');
                    await expect(page.locator('.topbar')).toHaveCSS('height', '68px');
                } else {
                    await expect(page.locator('.sidebar')).toHaveCSS('position', 'static');
                    expect(await page.locator('.workspace-shell').evaluate(node => getComputedStyle(node).gridTemplateColumns.split(' ').length)).toBe(1);
                }
                await noOverflow(page);
                await attachEvidence(page, testInfo, `${route}-${width}-${theme}`);
            }
        }
    }
});

test('long content and zoom keep grouped navigation reachable', async ({ page, request }) => {
    await request.post('/__test__/reset', { data: { workspace_visual: true } });
    await page.setViewportSize({ width: 1600, height: 1100 });
    await page.goto(`${workspace}#/library`);
    await expect(page.locator('article[data-prompt-id]')).toHaveCount(6);
    await page.evaluate(() => { document.documentElement.style.zoom = '2'; });
    await groupedNavigation(page);
    for (const route of [...primary, ...tools]) {
        const link = page.locator(`nav [data-route="${route}"]`);
        await link.focus(); await expect(link).toBeFocused();
    }
    await page.getByRole('combobox', { name: 'Appearance', exact: true }).focus();
    await expect(page.getByRole('combobox', { name: 'Appearance', exact: true })).toBeFocused();
    const text = page.locator('.prompt-text').filter({ hasText: 'A study in very long creative context' });
    await text.scrollIntoViewIfNeeded();
    expect((await text.textContent()).length).toBeGreaterThan(1500);
    await text.focus(); await expect(text).toBeFocused();
    expect(await text.evaluate(node => {
        const scroller = node.closest('.library-card-prompts');
        scroller.scrollTop = scroller.scrollHeight;
        return scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 1;
    })).toBe(true);
    await noOverflow(page);
    await page.evaluate(() => { document.documentElement.style.zoom = ''; });
    for (const width of [1251, 1250, 951, 950, 780, 700]) {
        await page.setViewportSize({ width, height: 1100 });
        await expect(page.locator('.sidebar')).toHaveCSS('position', width > 950 ? 'sticky' : 'static');
        if (width > 950) await expect(page.locator('.sidebar')).toHaveCSS('width', width > 1250 ? '218px' : '185px');
        await expect(page.locator('#workspace-main')).toHaveCSS('padding-inline-start', width > 1250 ? '30px' : width > 700 ? '24px' : '14px');
        await noOverflow(page);
    }
});


test('approved icons are decorative safe SVGs and reject unknown vocabulary', async ({ page }) => {
    await page.goto(`${workspace}#/images`);
    const icons = await page.evaluate(async () => {
        const { createWorkspaceIcon } = await import('/prompt_manager/js/workspace/icons.mjs');
        const names = ['images', 'library', 'tags', 'upload', 'overview', 'settings', 'grid', 'list', 'masonry', 'search', 'info', 'copy', 'branch'];
        const otherDocument = document.implementation.createHTMLDocument();
        const rendered = names.map(name => {
            const icon = createWorkspaceIcon(name, otherDocument);
            return { name, svg: icon instanceof SVGSVGElement, owner: icon.ownerDocument === otherDocument,
                viewBox: icon.getAttribute('viewBox'), hidden: icon.getAttribute('aria-hidden'), focusable: icon.getAttribute('focusable'),
                fill: icon.getAttribute('fill'), stroke: icon.getAttribute('stroke'), width: icon.getAttribute('stroke-width'),
                path: icon.querySelector('path').getAttribute('d'), children: icon.childElementCount };
        });
        const unknown = ['missing', '__proto__', '', null, 1].map(name => {
            try { createWorkspaceIcon(name); return false; } catch (error) { return error instanceof TypeError; }
        });
        return { rendered, unknown };
    });
    expect(icons.unknown).toEqual([true, true, true, true, true]);
    for (const icon of icons.rendered) {
        expect(icon).toMatchObject({ svg: true, owner: true, viewBox: '0 0 24 24', hidden: 'true', focusable: 'false', fill: 'none', stroke: 'currentColor', width: '1.65', children: 1 });
        expect(icon.path.length).toBeGreaterThan(10);
    }
    expect(icons.rendered.find(icon => icon.name === 'overview').path).toBe('M3 12h4l3-8 4 16 3-8h4');
});
