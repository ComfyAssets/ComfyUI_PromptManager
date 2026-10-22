import { writeFile } from 'node:fs/promises';
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { captureCanonicalState, historyGeometry, compareCanonicalHistory, nativeDialogState, exerciseCurrentPairSend, assertRecordedHistory } from './history-canonical-comparison.mjs';
import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';

test.use({ locale: 'en-US', timezoneId: 'UTC', reducedMotion: 'reduce' });
const workspace = '/prompt_manager/workspace';
const primary = ['images', 'library', 'tags', 'inspect'];
const tools = ['activity', 'system', 'overview'];

async function noOverflow(page) {
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
}
async function groupedNavigation(page) {
    for (const [group, routes] of [['primary', primary], ['system', tools]]) {
        const region = page.locator(`#${group}-nav`);
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
    const geometry = await page.evaluate(() => Object.fromEntries(['.sidebar', '.topbar', '#workspace-main', '.heading', 'h1', '.image-workspace', '.image-grid', '.media-card', '.detail-panel', '.prompt-heading', '.library-grid', '.prompt-card', '.prompt-card .card-art', '.prompt-card .card-content'].map(selector => {
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

test('canonical history assets are the exact immutable approved source', async ({ request }) => {
    const assets = [
        ['/__test__/canonical/workspace', 'docs/concepts/workspace/index.html', 'd84712c7284bb232919a1ed9b154575c8ac574f63b2404a4ac4bc06cbf323c03'],
        ['/workspace/app.js', 'docs/concepts/workspace/app.js', 'e33e7652da12a68cd3fe31e94d77d680aa439b6434ceee94f21cc4ce8883263e'],
        ['/workspace/style.css', 'docs/concepts/workspace/style.css', '2c479ca1a2ddbbd14a550eae27dd731c326ccdb91fe9ddc87e2b81cd5298fce8'],
    ];
    for (const [url, source, sha256] of assets) {
        const response = await request.get(url);await expect(response).toBeOK();
        const bytes = await response.body();
        expect(bytes).toEqual(await readFile(new URL(`../../${source}`, import.meta.url)));
        expect(createHash('sha256').update(bytes).digest('hex')).toBe(sha256);
        expect(response.headers()['cache-control']).toBe('no-store');
        expect(response.headers()['x-content-type-options']).toBe('nosniff');
    }
    for (const url of ['/workspace/missing.js', '/__test__/canonical/server.py', '/toolkit/missing.js', '/vendor/missing.js']) {
        expect((await request.get(url)).status()).toBe(404);
    }
});

for (const [width, height] of [[1600, 1100], [390, 844]]) for (const theme of ['dark', 'light']) {
    test(`canonical chronological history ${width} ${theme}`, async ({ page, request }, testInfo) => {
        await page.setViewportSize({ width, height });
        await page.goto('/__test__/canonical/workspace#prompt/alpine');
        await page.getByRole('combobox', { name: 'Theme', exact: true }).selectOption(theme);
        await expect(page.locator('.timeline')).toBeVisible();
        const referenceDot = page.getByRole('button', { name: 'Select negative Quality baseline', exact: true });
        await referenceDot.focus();await page.keyboard.press('Enter');
        await expect(page.getByRole('button', { name: 'Select negative Quality baseline', exact: true })).toHaveAttribute('aria-pressed', 'true');
        const reference = await historyGeometry(page, false);
        for (const [name, selector] of [['saved', '.surface'], ['branches', '.timeline-panel'], ['gallery', '.history-gallery']]) {
            await captureCanonicalState(page, testInfo, `concept-${name}-${width}-${theme}`, selector);
        }
        await page.getByRole('button', { name: 'Show repeat run for Quality baseline', exact: true }).click();
        await expect(page.getByRole('dialog', { name: 'Repeat run', exact: true })).toBeVisible();
        expect(await page.locator('#modal').evaluate(node => node.matches(':modal'))).toBe(true);
        reference.runDialog = await nativeDialogState(page.locator('#modal'));
        await captureCanonicalState(page, testInfo, `concept-run-${width}-${theme}`, '#modal');
        await page.keyboard.press('Escape');await expect(page.locator('#modal')).not.toBeVisible();
        await page.locator('.history-gallery .image-open').first().click();
        await expect(page.locator('#viewer-dialog')).toBeVisible();
        await expect(page.locator('#viewer-dialog')).toHaveAccessibleName('Alpine observatory · alpine_00001.png');
        await captureCanonicalState(page, testInfo, `concept-viewer-${width}-${theme}`, '#viewer-dialog');
        await page.keyboard.press('Escape');

        const reset = await request.post('/__test__/reset', { data: { chronological_history: true } });await expect(reset).toBeOK();const fixture = await reset.json();
        await page.goto(`${workspace}#/library/${fixture.canonical_id}`);
        await page.getByRole('combobox', { name: 'Theme', exact: true }).selectOption(theme);
        const details = await (await request.get(`/prompt_manager/library/${fixture.canonical_id}`)).json();
        const pairTitle = details.variants.find(variant => variant.id === fixture.canonical_pair).title;
        const choice = page.getByRole('button', { name: `Select negative ${pairTitle}`, exact: true });
        await choice.focus();await page.keyboard.press('Enter');await expect(choice).toHaveAttribute('aria-pressed', 'true');
        const actual = await historyGeometry(page, true);
        const roles = page.getByRole('region', { name: 'Saved prompt texts', exact: true });
        const positive = roles.getByRole('region', { name: 'Positive prompt', exact: true });
        expect(await positive.evaluate(node => node.nextElementSibling?.getAttribute('aria-label'))).toBe('Negative prompt');
        const title = await page.locator('#workspace-main > .heading h1').textContent();
        for (const name of ['Copy positive prompt', 'Send positive prompt to ComfyUI', 'Copy negative prompt', 'Send negative prompt to ComfyUI']) await expect(roles.getByRole('button', { name: `${name}: ${title}`, exact: true })).toBeVisible();
        // As in the concept, the selected pair's send sits in the history heading, not among the saved texts.
        await expect(roles.getByRole('button', { name: 'Send prompt pair', exact: true })).toHaveCount(0);
        await expect(page.locator('#workspace-main > .heading button.send')).toBeEnabled();
        await expect(page.locator('.library-history .history-gallery article[data-image-key]')).toHaveCount(2);
        await page.locator('.library-history .history-gallery .image-card-actions button').first().click();
        await expect(page.getByRole('complementary', { name: 'Selected image details', exact: true })).toContainText('canonical-alt.png');
        await page.locator('.library-history .history-gallery .media-card.selected .image-open').click();
        await expect(page.locator('dialog#viewer-dialog')).toBeVisible();await page.keyboard.press('Escape');
        for (const [name, selector] of [['saved', '.library-history article.surface'], ['branches', '.library-history .timeline-panel'], ['gallery', '.library-history .history-gallery'], ['chronology', '.recorded-history']]) {
            await captureCanonicalState(page, testInfo, `production-${name}-${width}-${theme}`, selector);
        }
        const run = page.locator(`button[data-run-id="${fixture.canonical_run}"][data-anchor-id="10"]`);
        await run.focus();await page.keyboard.press('Enter');
        const runDialog = page.getByRole('dialog', { name: 'Recorded run', exact: true });
        await expect(runDialog.locator('[data-receipt]')).toHaveCount(2);
        expect(await runDialog.evaluate(node => node.matches(':modal') && node.contains(document.activeElement))).toBe(true);
        actual.runDialog = await nativeDialogState(runDialog);
        for (const name of ['Raw', 'Effective', 'Close recorded run', 'Send prompt pair']) await expect(runDialog.getByRole('button', { name, exact: true })).toBeVisible();
        await captureCanonicalState(page, testInfo, `production-run-${width}-${theme}`, '.recorded-run-dialog');
        const view = runDialog.getByRole('button', { name: 'View current file', exact: true }).first();await view.click();
        const viewer = page.locator('dialog#viewer-dialog');await expect(viewer).toBeVisible();
        await expect(viewer.getByRole('region', { name: 'Positive prompt', exact: true })).toContainText(fixture.canonical_positive);
        await expect(viewer.getByRole('region', { name: 'Negative prompt', exact: true })).toContainText(fixture.canonical_negative);
        for (const name of ['Copy positive prompt', 'Send positive prompt to ComfyUI', 'Copy negative prompt', 'Send negative prompt to ComfyUI']) await expect(viewer.getByRole('button', { name: new RegExp(`^${name}: `) })).toBeVisible();
        await expect(viewer.getByRole('button', { name: /^Send current file prompt pair to ComfyUI: |^Send prompt pair to ComfyUI: / })).toBeVisible();
        await expect(viewer).toContainText('42001');await expect(viewer).toContainText('720');await expect(viewer).toContainText('480');
        const currentPositive = viewer.getByRole('region', { name: 'Positive prompt', exact: true });
        await currentPositive.getByRole('button', { name: /^Copy positive prompt: / }).click();
        // The concept's copy outcome: the shared toast, or its "Copy prompt" dialog when the clipboard is refused.
        const copyFallback = page.getByRole('dialog', { name: 'Copy prompt', exact: true });
        await expect(page.locator('#workspace-toast').filter({ hasText: 'Prompt copied.' }).or(copyFallback)).toHaveCount(1);
        if (await copyFallback.isVisible()) { await page.keyboard.press('Escape'); await expect(copyFallback).toBeHidden(); }
        await exerciseCurrentPairSend(page, { positive: fixture.canonical_positive, negative: fixture.canonical_negative });
        await captureCanonicalState(page, testInfo, `production-viewer-${width}-${theme}`, 'dialog#viewer-dialog');
        await page.keyboard.press('Escape');await expect(view).toBeFocused();await page.keyboard.press('Escape');await expect(run).toBeFocused();
        await compareCanonicalHistory(reference, actual, testInfo);

        const history = await request.get(`/prompt_manager/library/${fixture.positive_id}/history`);await expect(history).toBeOK();
        const window = await history.json();expect(window.items.map(item => item.run_id)).toEqual(fixture.aab);
        expect(window.context_summaries).toHaveLength(2);
        const contextIds = [...new Set(window.items.flatMap(item => item.matching_anchors.map(anchor => anchor.context_id)))];
        expect(contextIds).toHaveLength(2);
        await page.goto(`${workspace}#/library/${fixture.positive_id}`);
        await expect(page.locator('.history-run-marker')).toHaveCount(3);
        await page.locator('.history-timeline').evaluate(node => { node.scrollLeft = 0;node.scrollTop = 0; });
        const anchor = page.locator('.history-context-anchor').first();await anchor.focus();await expect(anchor).toBeFocused();
        const multiple = await historyGeometry(page, true);
        expect(multiple.recordedBranch.map(branch => branch.contextId)).toEqual(contextIds);
        expect(multiple.recordedMarker.map(marker => marker.runId)).toEqual(fixture.aab);
        await captureCanonicalState(page, testInfo, `production-chronology-multiple-${width}-${theme}`, '.recorded-history');
        const geometryPath = testInfo.outputPath('canonical-recorded-geometry.json');
        await writeFile(geometryPath, JSON.stringify({ reference, actual: multiple, expected: { contextIds, runIds: fixture.aab, branchCount: 2, anchorCount: 2, repeatCount: 1 }, differences: 'Two real frozen contexts / three runs versus three illustrative saved variants; chronological columns retain 64px run spacing, with the same role colors, curves and 57px row rhythm.' }, null, 2));
        await testInfo.attach('canonical-recorded-geometry', { path: geometryPath, contentType: 'application/json' });
        assertRecordedHistory(reference, multiple, { branchCount: 2, anchorCount: 2, repeatCount: 1 });
    });
}

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
            await expect(page.locator('#workspace-main > .heading > div > h1#page-title')).toBeVisible();
            await groupedNavigation(page);
            await expect(page.locator(route === 'images' ? 'article[data-image-key]' : 'article[data-prompt-id]').first()).toBeVisible();
            await expect(page.locator('.brand')).toHaveAttribute('href', '#/images');
            await expect(page.locator('#workspace-main > .eyebrow')).toHaveCount(0);
            for (const [theme, active] of [['dark', 'rgb(20, 54, 77)'], ['light', 'rgb(228, 241, 251)']]) {
                await page.getByRole('combobox', { name: 'Theme', exact: true }).selectOption(theme);
                await expect(page.locator('nav [aria-current="page"]')).toHaveCSS('background-color', active);
                await expect(page.locator('h1')).toHaveCSS('font-weight', '620');
                await expect(page.locator('h1')).toHaveCSS('letter-spacing', '-0.7px');
                await expect(page.locator('#workspace-main > .heading')).toHaveCSS('margin-block-end', '22px');
                await expect(page.locator('h1')).toHaveCSS('font-size', width === 1600 ? '28px' : '25px');
                await expect(page.locator('#workspace-main > .heading p.muted')).toHaveCSS('line-height', '18px');
                await expect(page.locator('#workspace-main')).toHaveCSS('padding-inline-start', width === 1600 ? '30px' : '14px');
                if (width === 1600) {
                    await expect(page.locator('.sidebar')).toHaveCSS('width', '218px');
                    await expect(page.locator('.topbar')).toHaveCSS('height', '68px');
                } else {
                    await expect(page.locator('.sidebar')).toHaveCSS('position', 'static');
                    expect(await page.locator('.app-shell').evaluate(node => getComputedStyle(node).gridTemplateColumns.split(' ').length)).toBe(1);
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
    await page.getByRole('combobox', { name: 'Theme', exact: true }).focus();
    await expect(page.getByRole('combobox', { name: 'Theme', exact: true })).toBeFocused();
    const text = page.locator('.prompt-text').filter({ hasText: 'A study in very long creative context' });
    await text.scrollIntoViewIfNeeded();
    expect((await text.textContent()).length).toBeGreaterThan(1500);
    await text.focus(); await expect(text).toBeFocused();
    // #249 D23: the card clamps the prompt to two lines; the card content itself never scrolls or overflows.
    expect(await text.evaluate(node => {
        const content = node.closest('.card-content');
        return content.scrollHeight <= content.clientHeight + 1 && content.scrollWidth <= content.clientWidth + 1 && node.scrollHeight > node.clientHeight + 1;
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

test('Theme label and control fit the narrow shell at 200 percent zoom', async ({ page, request }, testInfo) => {
    await request.post('/__test__/reset', { data: { workspace_visual: true } });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`${workspace}#/images`);
    await expect(page.locator('article[data-image-key]')).toHaveCount(24);
    await page.evaluate(() => { document.documentElement.style.zoom = '2'; });
    const preference = page.getByRole('combobox', { name: 'Theme', exact: true });
    const bounds = await preference.evaluate(select => {
        const field = select.closest('label');
        return { intendedWrapper: field.classList.contains('theme-label') && field.parentElement.classList.contains('top-actions'),
            boxes: [field, select].map(node => {
                const box = node.getBoundingClientRect(); return { left: box.left, right: box.right, width: box.width };
            }), width: document.documentElement.clientWidth };
    });
    for (const box of bounds.boxes) {
        expect(box.width).toBeGreaterThan(0);
        expect(box.left).toBeGreaterThanOrEqual(-1);
        expect(box.right).toBeLessThanOrEqual(bounds.width + 1);
    }
    expect(bounds.intendedWrapper).toBe(true);
    for (const theme of ['system', 'light', 'dark']) {
        await preference.selectOption(theme); await expect(preference).toHaveValue(theme);
    }
    await preference.focus(); await expect(preference).toBeFocused();
    await noOverflow(page);
    await attachEvidence(page, testInfo, 'zoom-shell-390', false);
});

async function containedImageContent(page) {
    return page.locator('article[data-image-key]').evaluateAll(nodes => {
        const failures = [];
        for (const card of nodes) {
            const outer = card.getBoundingClientRect();
            for (const selector of ['.media-info', '.media-info h3', '.media-info p', '.image-card-actions button']) {
                const node = card.querySelector(selector), box = node.getBoundingClientRect();
                if (box.width <= 0 || box.height <= 0 || box.left < outer.left - 1 || box.right > outer.right + 1 ||
                    box.top < outer.top - 1 || box.bottom > outer.bottom + 1 ||
                    (selector === '.media-info p' && node.scrollHeight > node.clientHeight + 1)) {
                    failures.push({ key: card.dataset.imageKey, selector,
                        card: { left: outer.left, right: outer.right, top: outer.top, bottom: outer.bottom },
                        content: { left: box.left, right: box.right, top: box.top, bottom: box.bottom } });
                }
            }
            const action = card.querySelector('.image-card-actions button').getBoundingClientRect();
            const range = document.createRange(); range.selectNodeContents(card.querySelector('.media-info p'));
            for (const text of range.getClientRects()) {
                if (Math.min(text.right, action.right) - Math.max(text.left, action.left) > .5 &&
                    Math.min(text.bottom, action.bottom) - Math.max(text.top, action.top) > .5) {
                    failures.push({ key: card.dataset.imageKey, selector: 'facts/action overlap',
                        text: { left: text.left, right: text.right, top: text.top, bottom: text.bottom },
                        action: { left: action.left, right: action.right, top: action.top, bottom: action.bottom } });
                }
            }
        }
        return failures;
    });
}

for (const [width, height] of [[390, 844], [1600, 1100]]) for (const layout of ['Grid', 'List', 'Masonry']) {
    test(`${layout} contains real card text and keyboard actions at ${width}px with 200 percent zoom`, async ({ page, request }, testInfo) => {
        await request.post('/__test__/reset', { data: { workspace_visual: true } });
        await page.setViewportSize({ width, height }); await page.goto(`${workspace}#/images`);
        const cards = page.locator('article[data-image-key]');
        await expect(cards).toHaveCount(24);
        await page.getByRole('button', { name: layout, exact: true }).click();
        const firstKey = await cards.first().getAttribute('data-image-key');
        for (const zoom of [1, 2]) {
            await page.evaluate(zoom => { document.documentElement.style.zoom = String(zoom); }, zoom);
            for (const density of ['comfortable', 'large', 'compact']) {
                await page.getByRole('combobox', { name: 'Thumbnail size', exact: true }).selectOption(density);
                await expect.poll(() => containedImageContent(page)).toEqual([]);
                await orderedMedia(page);
                const columns = await cards.evaluateAll(nodes => {
                    const top = nodes[0].getBoundingClientRect().top;
                    return nodes.filter(node => Math.abs(node.getBoundingClientRect().top - top) < 1).length;
                });
                if (zoom === 1) expect(columns).toBe(layout === 'List' ? 1 : width === 390 || density === 'large' ? 2 : density === 'compact' ? 4 : 3);
                else expect(columns).toBe(layout === 'List' || width === 390 ? 1 : density === 'large' ? 2 : density === 'compact' ? 4 : 3);
                if (zoom === 1 && layout === 'List') await expect(cards.first()).toHaveCSS('height', '97px');
                await cards.first().locator('.image-open').focus(); await page.keyboard.press('ArrowDown');
                await expect(cards.nth(columns).locator('.image-open')).toBeFocused();
                await page.keyboard.press('ArrowUp'); await expect(cards.first().locator('.image-open')).toBeFocused();
                expect(await cards.first().getAttribute('data-image-key')).toBe(firstKey);
            }
            const longCard = cards.filter({ has: page.locator('[aria-label^="Open a-very-long-geometric-composition-"]') });
            const filename = (await longCard.locator('.image-open').getAttribute('aria-label')).replace(/^Open /, '');
            expect(filename).toMatch(/layered-context-.*\.png/);
            // D19: Info selects into the panel; the image itself opens the viewer.
            await longCard.locator('.image-card-actions button').focus(); await page.keyboard.press('Enter');
            await expect(page.locator('.detail-panel .panel-heading h2')).toHaveText(filename);
            // D19: the card image, not the panel, opens the viewer.
            const fullPreview = longCard.locator('.image-open');
            await fullPreview.focus(); await expect(fullPreview).toBeFocused(); await page.keyboard.press('Enter');
            await expect(page.locator('dialog#viewer-dialog')).toBeVisible();
            await expect(page.locator('#viewer-title')).toHaveText(filename);
            await page.keyboard.press('Escape');
            // The concept's card order (app.js mediaCard): image, the D8 checkbox, the D7 star, then Inspect. List hides
            // the overlays as the concept does (`.list-grid .image-overlays`), so there the image is followed by Inspect.
            const box = longCard.getByRole('checkbox', { name: `Select ${filename}`, exact: true });
            const star = longCard.getByRole('button', { name: `Favorite ${filename}`, exact: true });
            const overlays = layout === 'List' ? [] : [box, star];
            if (layout === 'List') { await expect(box).toBeHidden(); await expect(star).toBeHidden(); }
            await longCard.locator('.image-open').focus();
            for (const control of overlays) { await page.keyboard.press('Tab'); await expect(control).toBeFocused(); }
            await page.keyboard.press('Tab');
            await expect(longCard.getByRole('button', { name: `Inspect ${filename}`, exact: true })).toBeFocused();
            for (const control of [...overlays].reverse()) { await page.keyboard.press('Shift+Tab'); await expect(control).toBeFocused(); }
            await page.keyboard.press('Shift+Tab');
            await expect(longCard.locator('.image-open')).toBeFocused();
            await page.keyboard.press('Enter'); await expect(page.locator('dialog[open]')).toHaveCount(1);
            await page.keyboard.press('Escape');
            await expect.poll(() => containedImageContent(page)).toEqual([]);
            await orderedMedia(page);
            await page.evaluate(() => scrollTo(0, 0));
            await attachEvidence(page, testInfo, `zoom-${layout.toLowerCase()}-${width}-${zoom}`, false);
        }
    });
}

test('zoomed virtual Images keeps the visible real media mounted during native scrolling', async ({ page, request }) => {
    await page.addInitScript(() => { window.IntersectionObserver = undefined; });
    await request.post('/__test__/reset', { data: { extra_images: 200 } });
    await page.setViewportSize({ width: 390, height: 844 }); await page.goto(`${workspace}#/images`);
    const cards = page.locator('article[data-image-key]');
    await expect(cards).toHaveCount(40);
    await page.evaluate(() => { document.documentElement.style.zoom = '2'; });
    await expect.poll(() => cards.nth(1).evaluate(node => node.offsetLeft)).toBe(0);
    const stride = await cards.first().evaluate(node => node.getBoundingClientRect().height + 26);
    const more = page.getByRole('button', { name: 'Load more images', exact: true });
    for (const loaded of [80, 120, 160, 200, 202]) {
        await more.click(); await expect(page.locator('.section-caption')).toContainText(`${loaded} of 202 images`);
    }
    await expect.poll(() => cards.count()).toBeLessThan(120);
    await page.evaluate(stride => {
        const grid = document.querySelector('.image-grid');
        scrollTo(0, grid.getBoundingClientRect().top + scrollY + stride * 100 - 120);
    }, stride);
    await expect(page.locator('article[data-index="100"]')).toBeVisible();
    await expect(page.locator('article[data-index="101"]')).toHaveCount(1);
    const visible = page.locator('article[data-index="100"]');
    await visible.locator('.image-open').focus(); await page.keyboard.press('ArrowDown');
    await expect(page.locator('article[data-index="101"] .image-open')).toBeFocused();
    await page.keyboard.press('ArrowUp'); await expect(visible.locator('.image-open')).toBeFocused();
    await expect(visible).toBeInViewport();
    await expect.poll(() => containedImageContent(page)).toEqual([]);
    await orderedMedia(page);
    await expect.poll(() => cards.count()).toBeLessThan(120);
});

test('supplemental extreme zoom grows real footers preserves the visible anchor and resets reservations', async ({ page, request }) => {
    await request.post('/__test__/reset', { data: { workspace_visual: true } });
    await page.setViewportSize({ width: 390, height: 844 }); await page.goto(`${workspace}#/images`);
    const cards = page.locator('article[data-image-key]'), grid = page.locator('.image-grid');
    await expect(cards).toHaveCount(24);
    await page.evaluate(() => { document.documentElement.style.zoom = '2'; });
    await expect.poll(() => cards.nth(1).evaluate(node => node.offsetLeft)).toBe(0);
    const selected = cards.nth(16), key = await selected.getAttribute('data-image-key');
    await selected.locator('.image-card-actions button').click(); await selected.locator('.image-open').focus();
    const filename = (await selected.locator('.image-open').getAttribute('aria-label')).replace(/^Open /, '');
    await selected.evaluate(node => scrollBy(0, node.getBoundingClientRect().top - 120));
    await page.evaluate(() => {
        const grid = document.querySelector('.image-grid'), footers = [...grid.querySelectorAll('.media-info')];
        // Production measures each footer's fractional height (`getBoundingClientRect().height / scale`, #249 C7), so the
        // probe records the visible anchor at the first measurement after the zoom, before the grid relays out.
        const nativeRect = Element.prototype.getBoundingClientRect;
        const restore = () => { for (const node of footers) delete node.getBoundingClientRect; };
        window.footerMeasurementProbe = { before: null, restore };
        for (const footer of footers) Object.defineProperty(footer, 'getBoundingClientRect', { configurable: true, writable: true, value() {
            const rect = nativeRect.call(this);
            if (document.documentElement.style.zoom === '4' && grid.style.getPropertyValue('--image-footer-height') === '54px' &&
                Math.abs(Number.parseFloat(this.closest('.media-card').style.width) - Number.parseFloat(getComputedStyle(grid).width)) < .01) {
                const top = visualViewport?.offsetTop || 0, bottom = top + (visualViewport?.height || innerHeight);
                const first = [...grid.children].find(node => { const box = node.getBoundingClientRect(); return box.bottom > top && box.top < bottom; });
                window.footerMeasurementProbe.before = first ? { index: Number(first.dataset.index), key: first.dataset.imageKey,
                    top: first.getBoundingClientRect().top - top } : null;
                restore();
            }
            return rect;
        } });
        document.documentElement.style.zoom = '4';
        const box = grid.getBoundingClientRect();
        // The approved initial reservation is 54px under the concept's 1.33 media and a 13px row gap.
        const stride = (box.width / 4 - 2) / 1.33 + 54 + 2 + 13;
        // Place row eight inside the 52px scaled gap so the preceding row is not visible.
        const index = 8;
        scrollTo(0, box.top + scrollY + index * stride * 4 - 16);
    });
    try {
        await expect.poll(() => page.evaluate(() => window.footerMeasurementProbe.before?.index)).toBe(8);
        const anchor = await page.evaluate(() => window.footerMeasurementProbe.before);
        await expect.poll(() => grid.evaluate(node => Number.parseFloat(node.style.getPropertyValue('--image-footer-height')))).toBeGreaterThan(54);
        await expect.poll(() => containedImageContent(page)).toEqual([]);
        const visibleAnchor = page.locator(`article[data-index="${anchor.index}"]`);
        const anchorTop = await visibleAnchor.evaluate(node => node.getBoundingClientRect().top - (visualViewport?.offsetTop || 0));
        expect(Math.abs(anchorTop - anchor.top)).toBeLessThanOrEqual(2);
        await expect(page.locator(`article[data-image-key="${key}"]`)).toHaveClass(/selected/);
        await expect(selected.locator('.image-open')).toBeFocused();
        await expect(page.locator('.detail-panel .panel-heading h2')).toHaveText(filename);
        const mutations = await grid.evaluate(async node => {
            let changes = 0;
            const observer = new MutationObserver(records => { changes += records.length; });
            observer.observe(node, { subtree: true, attributes: true, attributeFilter: ['style'] });
            for (let frame = 0; frame < 6; frame++) await new Promise(requestAnimationFrame);
            observer.disconnect(); return changes;
        });
        expect(mutations).toBe(0);
        await page.getByRole('button', { name: 'List', exact: true }).click();
        await expect.poll(() => containedImageContent(page)).toEqual([]);
        expect(await cards.first().evaluate(node => node.offsetHeight)).toBeGreaterThan(97);
        await page.getByRole('searchbox', { name: 'Search workspace', exact: true }).fill('no-real-media-match');
        await expect(cards).toHaveCount(0);
        expect(await grid.evaluate(node => Number.parseFloat(node.style.getPropertyValue('--image-footer-height')))).toBe(54);
        await page.getByRole('searchbox', { name: 'Search workspace', exact: true }).fill('');
        await expect(cards).toHaveCount(24);
        await expect.poll(() => grid.evaluate(node => Number.parseFloat(node.style.getPropertyValue('--image-footer-height')))).toBeGreaterThan(54);
        const extremeFooter = await grid.evaluate(node => Number.parseFloat(node.style.getPropertyValue('--image-footer-height')));
        await page.evaluate(() => { document.documentElement.style.zoom = '2'; });
        await expect.poll(() => grid.evaluate(node => Number.parseFloat(node.style.getPropertyValue('--image-footer-height')))).toBeLessThan(extremeFooter);
        await expect(cards.first()).toHaveCSS('height', '97px');
        await expect.poll(() => containedImageContent(page)).toEqual([]);
        await page.getByRole('button', { name: 'Grid', exact: true }).click();
        await expect.poll(() => grid.evaluate(node => Number.parseFloat(node.style.getPropertyValue('--image-footer-height')))).toBe(54);
        await orderedMedia(page);
    } finally { await page.evaluate(() => {
        window.footerMeasurementProbe?.restore(); delete window.footerMeasurementProbe;
    }); }
});

test('supplemental wrapped real footer reservation survives append without moving the visible card', async ({ page, request }) => {
    await page.addInitScript(() => { window.IntersectionObserver = undefined; });
    await request.post('/__test__/reset', { data: { extra_images: 200 } });
    await page.setViewportSize({ width: 390, height: 844 }); await page.goto(`${workspace}#/images`);
    const cards = page.locator('article[data-image-key]'), grid = page.locator('.image-grid');
    await expect(cards).toHaveCount(40);
    await cards.first().locator('.image-card-actions button').click();
    const selectedKey = await cards.first().getAttribute('data-image-key');
    await page.evaluate(() => { document.documentElement.style.zoom = '4'; });
    await expect.poll(() => grid.evaluate(node => Number.parseFloat(node.style.getPropertyValue('--image-footer-height')))).toBeGreaterThan(54);
    let release, pending = false;
    const held = new Promise(resolve => { release = resolve; });
    await page.route('**/images/output?**', async route => {
        if (new URL(route.request().url()).searchParams.has('cursor')) { pending = true; await held; }
        await route.continue();
    });
    try {
        const more = page.getByRole('button', { name: 'Load more images', exact: true });
        // At 4x zoom the concept continuation (copy + Load more) fills the viewport, so keep the last cards in view and
        // press Load more from the keyboard without scrolling to it.
        await cards.last().scrollIntoViewIfNeeded();
        await more.evaluate(node => node.focus({ preventScroll: true })); await page.keyboard.press('Enter');
        await expect.poll(() => pending).toBe(true);
        const anchor = await cards.evaluateAll(nodes => {
            const top = visualViewport?.offsetTop || 0, bottom = top + (visualViewport?.height || innerHeight);
            const node = nodes.find(node => { const box = node.getBoundingClientRect(); return box.bottom > top && box.top < bottom; });
            return node ? { key: node.dataset.imageKey, top: node.getBoundingClientRect().top - top } : null;
        });
        expect(anchor).not.toBeNull();
        release(); await expect(page.locator('.section-caption')).toContainText('80 of 202 images');
        await expect(cards).toHaveCount(80);
        await expect.poll(async () => Math.abs(await page.locator(`article[data-image-key="${anchor.key}"]`).evaluate(node => node.getBoundingClientRect().top - (visualViewport?.offsetTop || 0)) - anchor.top)).toBeLessThanOrEqual(1);
        await expect(page.locator(`article[data-image-key="${selectedKey}"]`)).toHaveClass(/selected/);
        // The concept focuses the first appended image after a Load more button press.
        await expect(page.locator('article[aria-posinset="41"] .image-open')).toBeFocused();
        await expect.poll(() => containedImageContent(page)).toEqual([]);
    } finally { release(); }
});

test('Library visual evidence keeps natural cards and returns from real history in both themes',async({browser,browserName,request},testInfo)=>{
    await request.post('/__test__/reset',{data:{workspace_visual:true}});
    for(const [width,height] of [[1600,1100],[390,844]]) for(const theme of ['dark','light']) {
        const context=await browser.newContext({viewport:{width,height},locale:'en-US',timezoneId:'UTC',reducedMotion:'reduce'});
        const page=await context.newPage();const errors=[];page.on('pageerror',error=>errors.push(error.message));await startCoverage(page,browserName);
        try {
            await page.goto(`${workspace}#/library`);await expect(page.locator('article[data-prompt-id]')).toHaveCount(6);
            await page.getByRole('combobox',{name:'Theme',exact:true}).selectOption(theme);
            await attachEvidence(page,testInfo,`after-library-${width}-${theme}`,false);
            await noOverflow(page);
            await page.getByRole('button',{name:'Explore history',exact:true}).first().click();
            await expect(page.getByRole('region',{name:'Prompt history',exact:true})).toBeVisible();
            await expect(page.locator('#workspace-main > .heading p.muted')).toHaveText('Prompt history · one positive, one shared gallery');
            await page.getByRole('button',{name:'← Prompt library',exact:true}).click();
            await expect(page.locator('article[data-prompt-id]')).toHaveCount(6);
            await expect(page.locator('#workspace-main > .heading p.muted')).toHaveText('One positive. Reusable negatives. Every experiment stays connected.');
            expect(errors).toEqual([]);
        } finally {await saveCoverage(page,browserName);await context.close();}
    }
});


test('approved icons are decorative safe SVGs and reject unknown vocabulary', async ({ page }) => {
    await page.goto(`${workspace}#/images`);
    const icons = await page.evaluate(async () => {
        const { createWorkspaceIcon } = await import('/prompt_manager/js/workspace/icons.mjs');
        const names = ['images', 'library', 'tags', 'upload', 'overview', 'activity', 'settings', 'grid', 'list', 'masonry', 'search', 'info', 'copy', 'branch', 'star', 'tagImage', 'folder', 'history', 'review'];
        const otherDocument = document.implementation.createHTMLDocument();
        const rendered = names.map(name => {
            const icon = createWorkspaceIcon(name, otherDocument);
            return { name, svg: icon instanceof SVGSVGElement, className: icon.getAttribute('class'), owner: icon.ownerDocument === otherDocument,
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
        expect(icon).toMatchObject({ svg: true, className: 'icon', owner: true, viewBox: '0 0 24 24', hidden: 'true', focusable: 'false', fill: 'none', stroke: 'currentColor', width: '1.65', children: 1 });
        expect(icon.path.length).toBeGreaterThan(10);
    }
    // D28 ruling: Overview's own outline home glyph; the concept's `activity` pulse is the Activity row's.
    expect(icons.rendered.find(icon => icon.name === 'activity').path).toBe('M3 12h4l3-8 4 16 3-8h4');
    expect(icons.rendered.find(icon => icon.name === 'overview').path).toBe('M3 11 12 4l9 7M5 10v10h14V10M10 20v-6h4v6');
    expect(new Set(icons.rendered.map(icon => icon.path)).size).toBe(icons.rendered.length);
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
        expect(await page.locator('.media-card img').first().evaluate(image=>image.complete)).toBe(false);
        await expect(page.locator('.media-card').first()).toHaveCSS('border-radius','7px');
        await orderedMedia(page);
    } finally {release();}
    for (const [width,height] of [[1600,1100],[390,844]]) {
        await page.setViewportSize({width,height});
        for (const theme of ['dark','light']) {
            await page.getByRole('combobox',{name:'Theme',exact:true}).selectOption(theme);
            await page.locator('.media-card .image-card-actions button').first().click();
            await expect(page.locator('.detail-panel .panel-heading h2')).toHaveText('study-01.png');
            for (const layout of ['Grid','List','Masonry']) {
                await page.getByRole('button',{name:layout,exact:true}).click();await page.evaluate(()=>scrollTo(0,0));
                if(layout==='List') await expect(page.locator('.media-card').first()).toHaveCSS('height','97px');
                await orderedMedia(page);
                await attachEvidence(page,testInfo,`after-images-${layout.toLowerCase()}-${width}-${theme}`,false);
            }
            await page.locator('article[data-image-key]').last().scrollIntoViewIfNeeded();
            const longCard=page.locator('article[data-image-key]').filter({has:page.locator('[aria-label^="Open a-very-long-geometric-composition-"]')});
            const longName=(await longCard.locator('.image-open').getAttribute('aria-label')).replace(/^Open /,'');
            expect(longName).toMatch(/layered-context-.*\.png/);
            await longCard.locator('.image-card-actions button').click();
            await expect(page.locator('.detail-panel .panel-heading h2')).toHaveText(longName);
            await orderedMedia(page);
            await attachEvidence(page,testInfo,`after-images-loaded-scroll-${width}-${theme}`,false);
        }
    }
    await page.evaluate(()=>{location.hash='#/overview';});
    await expect(page.locator('.media-card,.detail-panel,.viewer-container,dialog[open]')).toHaveCount(0);
});

test('embedded history gallery keeps the history heading and disposes its layouts',async({page,request},testInfo)=>{
    await request.post('/__test__/reset',{data:{workspace_visual:true}});
    for (const width of [1600,390]) {
        await page.setViewportSize({width,height:width===1600?1100:844});await page.goto(`${workspace}#/library`);
        await page.getByRole('button',{name:'Explore history',exact:true}).first().click();
        await expect(page.getByRole('region',{name:'Prompt history',exact:true})).toBeVisible();
        await expect(page.locator('h1')).not.toHaveText('Prompt library');
        const title=await page.locator('h1').textContent();expect(title).not.toBe('Images');
        // History opens on its first negative variant (concept navigate: state.variant = 0); the fixture's images are linked
        // to the positive only, so the layout contract uses the All images dialog's embedded gallery (#249 D26).
        await page.getByRole('button',{name:'All images for this positive',exact:true}).click();
        const listing=page.getByRole('dialog',{name:'All images for this positive',exact:true});
        await expect(listing.locator('article[data-image-key]').first()).toBeVisible();
        for (const layout of ['List','Masonry','Grid']) {
            await listing.getByRole('button',{name:layout,exact:true}).click();await orderedMedia(page);
            await expect(page.locator('h1')).toHaveText(title);
            await expect(page.locator('#workspace-main > .heading p.muted')).toHaveText('Prompt history · one positive, one shared gallery');
        }
        await attachEvidence(page,testInfo,`after-images-embedded-history-${width}`,false);
        await page.keyboard.press('Escape');await expect(listing).toHaveCount(0);
        await page.getByRole('button',{name:'← Prompt library',exact:true}).click();
        await expect(page.locator('.media-card,.detail-panel,dialog[open]')).toHaveCount(0);
    }
});
