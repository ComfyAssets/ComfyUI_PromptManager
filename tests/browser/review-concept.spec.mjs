// Needs review against the approved concept (#249 PR F, plan Task F3, Mock inventory M4: app.js renderReview
// a749–760, style.css s407–415, s796–814 and their breakpoints, screenshot needs-review-tagging.png). Production runs
// on the review fixture's concept-shaped queue (workspace_review_fixture.py `concept: true`: the concept's first three
// image names and sizes, and the concept's first prompt on the open item), so every remaining difference is a recorded
// deviation applied by mapConceptReview (D3, D29, D119) or a test failure. Paired captures of every state are attached
// as artifacts; no screenshot baseline is added.
import { test, expect } from './fixtures.mjs';
import { openConcept, openProduction, inventoryOf, mutate, saveImageTags, diffInventory, SIZES, THEMES } from './concept-inventory.mjs';
import { reviewInventory, mapConceptReview, taggingInventory, mapConceptTagging, diffTaggingInventory } from './tagging-canonical-comparison.mjs';

const CONCEPT_FILE = 'alpine_00001.png';
const CONCEPT_TAGS = ['cinematic', 'blue_hour', 'architecture'];
const SURFACE = ['h2', 'p.small', 'div.row-actions.spaced-15', 'div.field', 'section.positive.prompt-section',
    'section.negative.prompt-section', 'div.dialog-actions'];
// The fake model's server default instructions (tests/browser/server.py TestModel.default_prompt), D84.
const INSTRUCTIONS = 'Describe the image';

const panel = page => page.locator('#workspace-main section.review-panel');
const tagDialog = page => page.getByRole('dialog', { name: 'Tag image', exact: true });

async function seed(request, data = {}) {
    await expect(await request.post('/__test__/review/reset', { data: { items: 3, concept: true, ...data } })).toBeOK();
}
/** Production's Needs review page with its first item's sampler paths loaded. */
async function openReview(page, theme) {
    await openProduction(page, 'review', theme);
    await expect(panel(page).getByRole('combobox', { name: 'Sampler context', exact: true })).toBeEnabled();
    await settle(page);
    // D179, D181: the two buttons the concept lacks are pinned here, then left out of the comparison.
    await expect(panel(page).getByRole('button', { name: 'Leave unresolved', exact: true })).toBeVisible();
    await expect(panel(page).getByRole('button', { name: 'Link to a prompt…', exact: true })).toBeVisible();
    await page.addStyleTag({ content: '.review-panel .review-leave-unresolved, .review-panel .review-link-open { display: none !important; }' });
}
async function settle(page) {
    await page.mouse.move(0, 0);
    await page.evaluate(async () => {
        await document.fonts.ready;
        await Promise.all([...document.images].map(image => image.decode().catch(() => {})));
    });
}
const conceptInventory = page => page.evaluate(reviewInventory, '#main');
const productionInventory = page => page.evaluate(reviewInventory, '#workspace-main');
const node = (tree, prefix) => tree.tag?.startsWith(prefix) ? tree : tree.children?.map(child => node(child, prefix)).find(Boolean) ?? null;
/** Computed values of CSS tokens, read through a probe element in the open page. */
const tokens = (page, declarations) => page.evaluate(text => {
    const probe = document.createElement('i');
    probe.style.cssText = text;
    document.body.append(probe);
    const style = getComputedStyle(probe), values = [style.borderTopColor, style.backgroundColor];
    probe.remove();
    return values;
}, declarations);

async function compareAt(page, request, width, height, theme) {
    await seed(request);
    await page.setViewportSize({ width, height });
    await openConcept(page, 'review', theme);
    await settle(page);
    const concept = await conceptInventory(page);
    await openReview(page, theme);
    const actual = await productionInventory(page);
    return { concept, actual, reference: mapConceptReview(concept, actual) };
}

test.beforeEach(async ({ page }) => {
    page.errors = [];
    page.on('pageerror', error => page.errors.push(error.message));
});
test.afterEach(async ({ page, request }) => {
    expect(page.errors).toEqual([]);
    await request.post('/__test__/jobs/restart');
});

for (const [width, height] of SIZES) for (const theme of THEMES) {
    test(`concept and production Metadata review match at ${width}x${height} ${theme}`, async ({ page, request }) => {
        const { actual, reference } = await compareAt(page, request, width, height, theme);
        expect(diffInventory(reference, actual), `${width}x${height} ${theme}`).toEqual([]);
        // Pinned concept values (M4), so a mapping mistake on both sides cannot pass unseen.
        const main = actual.main;
        expect(main.children.map(child => child.tag)).toEqual(['div.heading', 'div.notice', 'div.gallery-layout']);
        const notice = node(main, 'div.notice');
        const [accent, soft] = await tokens(page, 'border-top:1px solid var(--ca-accent);background:var(--soft-blue)');
        expect([notice.borderTopColor, notice.borderLeftColor, notice.backgroundColor]).toEqual([accent, accent, soft]);
        expect([notice.borderTopWidth, notice.borderTopLeftRadius, notice.paddingTop, notice.paddingLeft, notice.marginBottom])
            .toEqual(['1px', '6px', '15px', '18px', '22px']);
        expect(notice.flexDirection).toBe(width <= 700 ? 'column' : 'row');
        expect(notice.children.map(child => child.children.map(item => [item.tag, item.text])))
            .toEqual([[['h2', '3 images need a decision'], ['p', 'Multiple sampler paths were found. The existing image links were preserved. Choose the correct context or leave them unresolved.']]]);
        const layout = node(main, 'div.gallery-layout');
        expect(layout.gridTemplateColumns).toBe(width <= 700 ? `${layout.width}px` : `${layout.width - 22 - 310}px 310px`);
        const surface = node(main, 'section.review-panel');
        expect(surface.children.map(child => child.tag)).toEqual(SURFACE);
        expect(surface.children[0].text).toBe('Resolve the association');
        expect(surface.children[1].text).toBe(CONCEPT_FILE);
        const actions = surface.children.at(-1);
        expect(actions.children.map(button => [button.text, button.primary])).toEqual([['Leave for later', false], ['Apply association', true]]);
        expect(node(surface, 'select').options.map(option => option[0])).not.toContain('Leave unresolved'); // D179
        expect(node(main, 'div.image-grid').children.map(card => node(card, 'h3').text))
            .toEqual(['alpine_00001.png', 'dunes_00001.png', 'forest_00001.png']);
        expect(actual.noPageScroll).toBe(true);
    });
}

test('the gallery layout follows the concept at every breakpoint', async ({ page, request }) => {
    await seed(request);
    // 310px beside the queue, 275px and a 15px gap at ≤1250px, 280px at ≤950px, one column at ≤700px.
    for (const [width, columns, gap] of [[1600, '310px', '22px'], [1200, '275px', '15px'], [900, '280px', '15px'], [390, null, '15px']]) {
        await page.setViewportSize({ width, height: 1000 });
        await openConcept(page, 'review', 'dark');
        const concept = node((await conceptInventory(page)).main, 'div.gallery-layout');
        await openReview(page, 'dark');
        const actual = node((await productionInventory(page)).main, 'div.gallery-layout');
        expect([actual.gridTemplateColumns, actual.columnGap], `${width}px`).toEqual([concept.gridTemplateColumns, concept.columnGap]);
        expect([actual.gridTemplateColumns.split(' ').at(-1), actual.columnGap], `${width}px`).toEqual([columns ?? `${actual.width}px`, gap]);
        expect(actual.gridTemplateColumns.split(' ').length, `${width}px`).toBe(width <= 700 ? 1 : 2);
    }
});

test('SAVED VIEWS rows match the concept with Needs review after Favorites', async ({ page, request }) => {
    await seed(request);
    await page.setViewportSize({ width: 1600, height: 1100 });
    await openConcept(page, 'review', 'dark');
    const concept = (await inventoryOf(page, '#main')).chrome.navs.find(nav => nav.id === 'saved-nav');
    await openReview(page, 'dark');
    const actual = (await inventoryOf(page, '#workspace-main')).chrome.navs.find(nav => nav.id === 'saved-nav');
    // Sample data: the concept stars three images; this queue's fixture stars none. Needs review counts the queue (3).
    const reference = { ...concept, rows: concept.rows.map(row => row.text === 'Favorites' ? { ...row, count: { ...row.count, text: '0' } } : row) };
    expect(diffInventory(reference, actual)).toEqual([]);
    expect(actual.rows.map(row => [row.text, row.count?.text, row.current])).toEqual([['Favorites', '0', null], ['Needs review', '3', 'page']]);
    // The count's auto start margin follows the row's own text; every other count token is shared.
    const { text: favoriteCount, marginInlineStart: favoriteMargin, ...favoriteStyle } = actual.rows[0].count;
    const { text: reviewCount, marginInlineStart: reviewMargin, ...reviewStyle } = actual.rows[1].count;
    expect(reviewStyle).toEqual(favoriteStyle);
    expect(actual.rows[1].icon.d).toBe('M4 3h16v18H4zM8 7h8M8 12h8M8 17h5');
});

test('review Tag dialog matches the needs-review-tagging reference', async ({ page, request }) => {
    await seed(request);
    await saveImageTags(request, `review/${CONCEPT_FILE}`, CONCEPT_TAGS);
    await page.setViewportSize({ width: 1600, height: 1100 });
    await openConcept(page, 'review', 'dark');
    await page.locator('#main section.surface').getByRole('button', { name: 'Tag image', exact: true }).click();
    await expect(tagDialog(page)).toBeVisible();
    await settle(page);
    const opened = await page.locator('dialog#modal').evaluate(taggingInventory);
    await page.keyboard.press('Escape');
    await openReview(page, 'dark');
    await panel(page).getByRole('button', { name: 'Tag image', exact: true }).click();
    await expect(tagDialog(page).locator('img.tag-image-preview')).toHaveAttribute('src', /\/image-annotations\/capture\//);
    await expect(tagDialog(page).locator('#manual-image-tags')).toHaveValue(CONCEPT_TAGS.join(', '));
    await settle(page);
    const actual = await page.locator('dialog#modal').evaluate(taggingInventory);
    const numberField = opened.modelPanel.fields.find(field => field.control.id === 'image-general-threshold');
    const mapped = mapConceptTagging(opened, { instructions: INSTRUCTIONS, chip: opened.heading.size, numberField });
    // D119: an undecided queue item names no prompt, so the heading and the preview's alt are the file name.
    const reference = { ...mapped, heading: { ...mapped.heading, h3: { ...mapped.heading.h3, text: CONCEPT_FILE },
        preview: { ...mapped.heading.preview, alt: CONCEPT_FILE } } };
    expect(actual.scopeNote.text).toBe('Tags belong to this image. Resolve its prompt association separately in Needs review.');
    expect(diffTaggingInventory(reference, actual)).toEqual([]);
    await page.keyboard.press('Escape');
    await expect(tagDialog(page)).toHaveCount(0);
});

test('the comparison notices a dropped notice border and reordered actions', async ({ page, request }) => {
    const { actual, reference } = await compareAt(page, request, 1600, 1100, 'dark');
    expect(diffInventory(reference, actual)).toEqual([]);
    const undoBorder = await mutate(page, { css: '#workspace-main .notice { border: 0 !important; }' });
    const borderless = await productionInventory(page);
    expect(diffInventory(reference, borderless).map(item => item.path)).toEqual(expect.arrayContaining([
        'main.children.1.borderTopWidth', 'main.children.1.borderTopStyle']));
    await undoBorder();
    await panel(page).locator('.dialog-actions').evaluate(row => row.prepend(row.lastElementChild));
    const reordered = await productionInventory(page);
    expect(diffInventory(reference, reordered).map(item => item.path)).toEqual(expect.arrayContaining([
        'main.children.2.children.1.children.6.children.0.text', 'main.children.2.children.1.children.6.children.0.primary']));
    await panel(page).locator('.dialog-actions').evaluate(row => row.append(row.firstElementChild));
    expect(diffInventory(reference, await productionInventory(page))).toEqual([]);
});

// Paired captures for the PR (no baselines): the populated queue beside the concept in both themes and widths, then
// the states the concept lacks (conflict, limited item, unavailable file, empty queue) as production captures.
test('paired Needs review captures', async ({ page, request }, testInfo) => {
    test.setTimeout(120_000);
    const shot = async name => {
        await settle(page);
        const file = testInfo.outputPath(`${name}.png`);
        await page.screenshot({ path: file, fullPage: true });
        await testInfo.attach(name, { path: file, contentType: 'image/png' });
    };
    await seed(request);
    for (const [width, height] of SIZES) for (const theme of THEMES) {
        await page.setViewportSize({ width, height });
        await openConcept(page, 'review', theme);
        await shot(`concept-review-${width}-${theme}`);
        await openReview(page, theme);
        await shot(`production-review-${width}-${theme}`);
    }
    await page.setViewportSize({ width: 1600, height: 1100 });
    await seed(request, { items: 0, conflict: true, limited: true, unavailable: true });
    await openReview(page, 'dark');
    await panel(page).getByRole('combobox', { name: 'Sampler context', exact: true }).selectOption({ index: 0 });
    const decided = page.waitForResponse(item => /\/decision$/.test(item.url()));
    await panel(page).getByRole('button', { name: 'Apply association', exact: true }).click();
    expect((await decided).status()).toBe(409);
    await expect(panel(page).locator('.warning-note')).toBeVisible();
    await shot('production-review-conflict');
    for (const [index, name] of [[1, 'limited'], [2, 'unavailable']]) {
        await page.locator('#workspace-main .review-queue article').nth(index).getByRole('button', { name: /^Inspect / }).click();
        await expect(panel(page).locator('.warning-note')).toBeVisible();
        await shot(`production-review-${name}`);
    }
    await seed(request, { items: 0 });
    await openProduction(page, 'review', 'dark');
    await expect(page.locator('#workspace-main .notice h2')).toHaveText('No images need a decision');
    await shot('production-review-empty');
});
