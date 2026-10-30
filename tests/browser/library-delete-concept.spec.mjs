import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';
import { SIZES, THEMES, seedConcept, openConcept, openProduction, diffInventory, mutate } from './concept-inventory.mjs';

// #443 D152: the Library's Delete… review against the concept's own reviewed archive: the recipe editor's "Archive…"
// (app.js 976), which opens `maintenancePlan("archive <title>")` (953–958, 1309–1310). The concept's shell, grid tokens,
// `p.small` and actions are kept; its title, demo note, rows, note text and `ca-primary` start are replaced by D152's,
// and the start takes the concept's `danger` class, measured on the editor's own "Archive…". The bar's Delete… is
// compared with the concept Images bar's danger action, "Remove links…" (app.js 432).
test.use({ locale: 'en-US', timezoneId: 'UTC' });

test.beforeEach(async ({ page, request, browserName }) => {
    await startCoverage(page, browserName);
    await seedConcept(request);
    page.conceptErrors = [];
    page.on('pageerror', error => page.conceptErrors.push(error.message));
});
test.afterEach(async ({ page, browserName }) => {
    await saveCoverage(page, browserName);
    expect(page.conceptErrors).toEqual([]);
});

/** Runs in the page: the open review dialog's title, body tree, grid, notes and actions with the concept's tokens. */
function measureReview() {
    const dialog = document.querySelector('dialog#modal[open]');
    const css = (node, ...keys) => node ? Object.fromEntries(keys.map(key => [key, getComputedStyle(node)[key]])) : null;
    const name = node => [node.localName, ...[...node.classList].sort()].join('.');
    const text = node => node?.textContent.trim() ?? null;
    const body = dialog.querySelector('#modal-body');
    const grid = body.querySelector(':scope > dl.meta-grid');
    const notes = [...body.querySelectorAll(':scope > p.small')];
    const actions = body.querySelector(':scope > .dialog-actions');
    return {
        title: text(dialog.querySelector('#modal-title')),
        tree: [...body.children].map(name).filter(item => item !== 'p.demo-note' && item !== 'h3'),
        grid: { rows: [...grid.children].map(row => [text(row.querySelector('dt')), text(row.querySelector('dd'))]),
            ...css(grid, 'display', 'gridTemplateColumns', 'columnGap', 'rowGap'),
            dt: css(grid.querySelector('dt'), 'fontSize', 'color', 'textTransform'), dd: css(grid.querySelector('dd'), 'fontSize', 'color') },
        notes: notes.map(note => ({ text: text(note), ...css(note, 'fontSize', 'color', 'marginTop') })),
        actions: { tree: [...actions.children].map(name), ...css(actions, 'display', 'justifyContent', 'columnGap', 'marginTop'),
            buttons: [...actions.children].map(item => ({ text: text(item), className: item.className,
                ...css(item, 'backgroundColor', 'color', 'fontSize', 'paddingTop', 'paddingLeft', 'borderTopColor') })) },
    };
}

/** Runs in the page: a button's class and tokens (the concept's danger button, or production's). */
function measureButton(selector) {
    const node = document.querySelector(selector);
    const style = getComputedStyle(node);
    return { className: node.className, ...Object.fromEntries(['backgroundColor', 'color', 'fontSize', 'fontWeight', 'paddingTop',
        'paddingLeft', 'borderTopColor', 'borderTopLeftRadius', 'height'].map(key => [key, key === 'height'
        ? `${Math.round(node.getBoundingClientRect().height * 100) / 100}px` : style[key]])) };
}

async function conceptReview(page, theme) {
    await openConcept(page, 'prompt/alpine', theme);
    await page.locator('#main .heading-actions').getByRole('button', { name: 'Edit metadata', exact: true }).click();
    const archive = page.locator('dialog#modal[open] .dialog-actions button', { hasText: 'Archive…' });
    await expect(archive).toBeVisible();await page.mouse.move(0, 0);
    const danger = await page.evaluate(measureButton, 'dialog#modal[open] .dialog-actions button.danger');
    await archive.click();
    await expect(page.locator('dialog#modal[open] #modal-title')).toHaveText('Review maintenance plan');
    await page.mouse.move(0, 0);
    return { danger, review: await page.evaluate(measureReview) };
}

async function seedMixed(request) {
    // One unpaired prompt beside the concept's paired ones, so the review deletes one and archives one.
    await expect(await request.post('/prompt_manager/save', { data: { text: 'Unpaired study', category: 'Studies' } })).toBeOK();
    const items = (await (await request.get('/prompt_manager/library?limit=100')).json()).items;
    return { unpaired: items.find(item => item.text === 'Unpaired study'), paired: items.find(item => item.variant_count > 0) };
}

async function productionReview(page, request, theme) {
    const { unpaired, paired } = await seedMixed(request);
    await openProduction(page, 'library', theme);
    for (const item of [unpaired, paired]) await page.locator(`article[data-prompt-id="${item.id}"] .image-overlays input`).check();
    await page.locator('#library-bulkbar .bulkbar').getByRole('button', { name: 'Delete…', exact: true }).click();
    await expect(page.locator('dialog#modal[open] #modal-title')).toHaveText('Delete or archive selected prompts');
    await page.mouse.move(0, 0);
    return page.evaluate(measureReview);
}

/** D152's changes to the concept's review; everything else must equal the concept. */
function mapDeleteConcept({ danger, review }) {
    const [cancel] = review.actions.buttons;
    const note = review.notes.at(-1);
    return {
        ...review,
        title: 'Delete or archive selected prompts',
        // #482: the paired prompt is deletable, so the review asks Archive or Delete permanently (a D90 radio row).
        tree: ['dl.meta-grid', 'div.field', 'p.small', 'p.small', 'div.dialog-actions'],
        grid: { ...review.grid, rows: [['Delete permanently', '1 prompt not in a saved pair'],
            ['Archive', '1 prompt in a saved pair: hidden, restorable'],
            ['Image links removed', '0 images will no longer show these prompts (files stay on disk)'],
            ['Backup', 'Create and verify before deleting']] },
        notes: [
            { ...note, text: 'Prompts in a saved pair, as its positive or its negative, are archived instead of deleted, so the '
                + 'pair and its runs stay intact. To delete them later or bring them back, press Show archived prompts, select them '
                + 'and press Delete… or Restore.' },
            // A second paragraph takes the concept's own `p + p` margin (style.css 93-95).
            { ...note, marginTop: '10px', text: "Deleting removes each prompt's text, title, category, tags, rating, notes, run count, last use and "
                + 'image links. Deleted prompts cannot be brought back one by one: the only way back is to restore the safety '
                + 'backup taken first (System › Backup & export), which returns the whole library to that moment. It is kept '
                + 'among the last 10 safety backups. Image files, favorites, collections and recorded runs stay. A later scan, a '
                + 'new image or a node run with the same text adds it again as a new prompt.' },
        ],
        actions: { ...review.actions, tree: [review.actions.tree[0], 'button.danger'], buttons: [cancel, { text: 'Delete 1 and archive 1 prompt', className: danger.className,
            backgroundColor: danger.backgroundColor, color: danger.color, fontSize: danger.fontSize, paddingTop: danger.paddingTop,
            paddingLeft: danger.paddingLeft, borderTopColor: danger.borderTopColor }] },
    };
}

for (const theme of THEMES) for (const [width, height] of SIZES) {
    test(`the Delete… review matches the concept's reviewed archive ${theme} ${width}x${height}`, async ({ page, request }, testInfo) => {
        await page.setViewportSize({ width, height });
        const concept = await conceptReview(page, theme);
        expect(concept.review.tree).toEqual(['dl.meta-grid', 'p.small', 'div.dialog-actions']);
        await page.screenshot({ path: testInfo.outputPath(`concept-archive-review-${theme}-${width}.png`) });
        const production = await productionReview(page, request, theme);
        await page.screenshot({ path: testInfo.outputPath(`production-delete-review-${theme}-${width}.png`) });
        expect(diffInventory(mapDeleteConcept(concept), production)).toEqual([]);
    });
}

test('the Delete… review comparison detects mutations', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    const concept = await conceptReview(page, 'dark');
    await productionReview(page, request, 'dark');
    const expected = mapDeleteConcept(concept);
    const paths = async () => diffInventory(expected, await page.evaluate(measureReview)).map(item => item.path);
    expect(await paths()).toEqual([]);
    await mutate(page, { css: '#modal-body .dialog-actions .danger { padding-left: 2px }' });
    expect(await paths()).toContain('actions.buttons.1.paddingLeft');
});

test('the bar\'s Delete… matches the concept Images bar\'s danger action', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    await openConcept(page, 'images', 'dark');
    await page.locator('#image-grid .media-card').first().locator('input[data-select]').check();
    await expect(page.locator('#bulkbar .bulkbar')).toBeVisible();await page.mouse.move(0, 0);
    const reference = await page.evaluate(measureButton, '#bulkbar .bulkbar button.danger');
    const { paired } = await seedMixed(request);
    await openProduction(page, 'library', 'dark');
    await page.locator(`article[data-prompt-id="${paired.id}"] .image-overlays input`).check();
    await expect(page.locator('#library-bulkbar .bulkbar')).toBeVisible();await page.mouse.move(0, 0);
    expect(await page.evaluate(measureButton, '#library-bulkbar .bulkbar button.danger')).toEqual(reference);
});
