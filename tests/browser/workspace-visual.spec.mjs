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
async function attachEvidence(page, testInfo, name, fullPage = true) {
    await page.evaluate(async () => {
        await document.fonts.ready;
        for (const image of document.images) image.loading = 'eager';
        await Promise.all([...document.images].map(image => image.decode().catch(() => {})));
    });
    const screenshotPath = testInfo.outputPath(`${name}.png`);
    await page.screenshot({ path: screenshotPath, fullPage });
    await testInfo.attach(name, { path: screenshotPath, contentType: 'image/png' });
    const geometry = await page.evaluate(() => Object.fromEntries(['.sidebar', '.topbar', '#workspace-main', '.workspace-heading', 'h1', '.image-workspace', '.image-grid', '.image-card', '.image-details', '.prompt-block-heading', '.library-toolbar', '.library-grid', '.library-card', '.library-card-art', '.library-card-content'].map(selector => {
        const node = document.querySelector(selector);
        if (!node) return [selector, null];
        const box = node.getBoundingClientRect(), style = getComputedStyle(node);
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
                await expect(page.locator('.workspace-heading-copy > .intro')).toHaveCSS('line-height', '18px');
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
        return scroller.scrollHeight <= scroller.clientHeight + 1 && node.scrollHeight <= node.clientHeight + 1;
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

test('Library visual evidence keeps natural cards and returns from real history in both themes',async({browser,browserName,request},testInfo)=>{
    await request.post('/__test__/reset',{data:{workspace_visual:true}});
    for(const [width,height] of [[1600,1100],[390,844]]) for(const theme of ['dark','light']) {
        const context=await browser.newContext({viewport:{width,height},locale:'en-US',timezoneId:'UTC',reducedMotion:'reduce'});
        const page=await context.newPage();const errors=[];page.on('pageerror',error=>errors.push(error.message));await startCoverage(page,browserName);
        try {
            await page.goto(`${workspace}#/library`);await expect(page.locator('article[data-prompt-id]')).toHaveCount(6);
            await page.getByRole('combobox',{name:'Appearance',exact:true}).selectOption(theme);
            await attachEvidence(page,testInfo,`after-library-${width}-${theme}`,false);
            await noOverflow(page);
            await page.getByRole('link',{name:'Explore history',exact:true}).first().click();
            await expect(page.getByRole('region',{name:'Prompt history',exact:true})).toBeVisible();
            await expect(page.locator('.library-history > div > .intro')).toHaveCount(1);
            await page.getByRole('link',{name:'Back to library',exact:true}).click();
            await expect(page.locator('article[data-prompt-id]')).toHaveCount(6);
            await expect(page.locator('.workspace-heading-copy > .intro')).toHaveCount(1);
            expect(errors).toEqual([]);
        } finally {await saveCoverage(page,browserName);await context.close();}
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


async function orderedMedia(page) {
    const boxes=await page.locator('article[data-image-key]').evaluateAll(nodes=>nodes.map(node=>{
        const box=node.getBoundingClientRect();return {x:box.x,y:box.y,right:box.right,bottom:box.bottom};
    }));
    for (let index=0;index<boxes.length;index++) for (const other of boxes.slice(index+1)) {
        const box=boxes[index];
        expect(box.right<=other.x+1||other.right<=box.x+1||box.bottom<=other.y+1||other.bottom<=box.y+1).toBe(true);
    }
    await noOverflow(page);
}

test('Images visual layouts retain real delayed media long filenames and loaded scroll',async({page,request},testInfo)=>{
    await request.post('/__test__/reset',{data:{workspace_visual:true}});
    let release, mediaRequests=0;const held=new Promise(resolve=>{release=resolve;});
    await page.route('**/images/serve/**',async route=>{mediaRequests++;await held;await route.continue().catch(()=>{});});
    try {
        await page.setViewportSize({width:1600,height:1100});await page.goto(`${workspace}#/images`,{waitUntil:'domcontentloaded'});
        await expect(page.locator('article[data-image-key]')).toHaveCount(24);
        await expect.poll(()=>mediaRequests).toBeGreaterThan(0);
        expect(await page.locator('.image-card img').first().evaluate(image=>image.complete)).toBe(false);
        await expect(page.locator('.image-card').first()).toHaveCSS('border-radius','7px');
        await orderedMedia(page);
    } finally {release();}
    for (const [width,height] of [[1600,1100],[390,844]]) {
        await page.setViewportSize({width,height});
        for (const theme of ['dark','light']) {
            await page.getByRole('combobox',{name:'Appearance',exact:true}).selectOption(theme);
            await page.locator('.image-card .image-open').first().click();
            await expect(page.locator('.image-details h2')).toHaveText('study-01.png');
            for (const layout of ['Grid','List','Masonry']) {
                await page.getByRole('button',{name:layout,exact:true}).click();await page.evaluate(()=>scrollTo(0,0));
                if(layout==='List') await expect(page.locator('.image-card').first()).toHaveCSS('height','97px');
                await orderedMedia(page);
                await attachEvidence(page,testInfo,`after-images-${layout.toLowerCase()}-${width}-${theme}`,false);
            }
            await page.locator('article[data-image-key]').last().scrollIntoViewIfNeeded();
            const longCard=page.locator('article[data-image-key]').filter({hasText:'a-very-long-geometric-composition-'});
            await expect(longCard.locator('strong')).toHaveText(/layered-context-.*\.png/);
            await longCard.locator('.image-open').click();
            await expect(page.locator('.image-details h2')).toHaveText(await longCard.locator('strong').textContent());
            await orderedMedia(page);
            await attachEvidence(page,testInfo,`after-images-loaded-scroll-${width}-${theme}`,false);
        }
    }
    await page.evaluate(()=>{location.hash='#/overview';});
    await expect(page.locator('.image-card,.image-details,.viewer-container,dialog[open]')).toHaveCount(0);
});

test('embedded history gallery keeps the history heading and disposes its layouts',async({page,request},testInfo)=>{
    await request.post('/__test__/reset',{data:{workspace_visual:true}});
    for (const width of [1600,390]) {
        await page.setViewportSize({width,height:width===1600?1100:844});await page.goto(`${workspace}#/library`);
        await page.getByRole('link',{name:'Explore history',exact:true}).first().click();
        await expect(page.getByRole('region',{name:'Prompt history',exact:true})).toBeVisible();
        await expect(page.locator('h1')).not.toHaveText('Prompt library');
        const title=await page.locator('h1').textContent();expect(title).not.toBe('Images');
        await expect(page.locator('article[data-image-key]').first()).toBeVisible();
        for (const layout of ['List','Masonry','Grid']) {
            await page.getByRole('button',{name:layout,exact:true}).click();await orderedMedia(page);
            await expect(page.locator('h1')).toHaveText(title);
            await expect(page.locator('.workspace-heading-copy > .intro')).toHaveCount(0);
        }
        await attachEvidence(page,testInfo,`after-images-embedded-history-${width}`,false);
        await page.getByRole('link',{name:'Back to library',exact:true}).click();
        await expect(page.locator('.image-card,.image-details,dialog[open]')).toHaveCount(0);
    }
});
