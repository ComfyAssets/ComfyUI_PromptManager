import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';
import { SIZES, THEMES, seedConcept, openConcept, openProduction, diffInventory, mutate } from './concept-inventory.mjs';

// #328 System S11 Task 8: System › Auto-tagging (app.js renderSystem 685–686) and the Tag output images review against
// the immutable concept, on the concept-shaped fixture with the fake tagging model (every engine downloaded, none
// loaded). Each concept measurement is mapped only by the recorded deviations (D132, D133, D134), each commented where
// it applies; after that the difference must be empty, and a mutated label, option, order, class, select width or
// token must be noticed. The concept has no batch review, so the Tag output images review is compared with the
// concept's own record-changing review, `maintenancePlan` (953–958), whose shell, grid, note and actions it shares.
test.use({ locale: 'en-US', timezoneId: 'UTC' });

test.beforeEach(async ({ page, request, browserName }) => {
    await startCoverage(page, browserName);
    // The tagging reset restores the fake model (nothing loaded, every engine on disk) and the saved tagging defaults;
    // the concept fixture then replaces its rows and files.
    await expect(await request.post('/__test__/tagging/reset', { data: {} })).toBeOK();
    await seedConcept(request);
    await expect(await request.post('/__test__/system/reset')).toBeOK();
    await expect(await request.post('/__test__/jobs/restart')).toBeOK();
    page.conceptErrors = [];
    page.on('pageerror', error => page.conceptErrors.push(error.message));
});

test.afterEach(async ({ page, request, browserName }) => {
    await saveCoverage(page, browserName);
    await request.post('/__test__/jobs/restart');
    await request.post('/__test__/system/reset');
    await request.post('/__test__/tagging/reset', { data: {} });
    expect(page.conceptErrors).toEqual([]);
});

const PREVIEW_URL = '/prompt_manager/autotag/batch/preview';
const MODEL_OPTIONS = ['WD14 SwinV2 · ~400 MB', 'WD14 ViT · ~400 MB', 'GGUF JoyCaption · ~4.5 GB', 'HuggingFace JoyCaption · ~16 GB'];
const SUGGESTIONS = 'From 1 to 100: the most suggestions Generate shows for one image.';
const BATCH = 'Tag each output image from its own pixels with the model above. Prompts are not changed. Review the scope first.';

/** Runs in the page: Auto-tagging's surface under `mainSelector`: rows, field, actions, operation, geometry and tokens. */
function measureTagging(mainSelector) {
    const layout = document.querySelector(`${mainSelector} .settings-layout`);
    const css = (node, ...keys) => node ? Object.fromEntries(keys.map(key => [key, getComputedStyle(node)[key]])) : null;
    const name = node => [node.localName, ...[...node.classList].sort()].join('.');
    const text = node => node?.textContent.trim() ?? null;
    const control = row => {
        const select = row.querySelector(':scope > select');
        if (select) return { kind: 'select', id: select.id, options: [...select.options].map(option => [option.textContent, option.disabled]),
            selected: select.selectedOptions[0]?.textContent ?? null, disabled: select.disabled };
        const input = row.querySelector(':scope > input');
        return input ? { kind: `input.${input.type}`, id: input.id, value: input.value, disabled: input.disabled } : null;
    };
    const surface = layout.querySelector(':scope > section.surface');
    const firstRow = surface.querySelector('.setting-row');
    const field = surface.querySelector(':scope > .field');
    const actions = surface.querySelector(':scope > .row-actions');
    return {
        layout: [...layout.children].map(name),
        tree: [...surface.children].map(name),
        heading: text(surface.querySelector(':scope > h2')),
        intro: text(surface.querySelector(':scope > p')),
        // Per row: whether it may wrap, and whether its control sits under the text (a wrapped row) or beside it.
        rows: [...surface.querySelectorAll(':scope > .setting-row')].map(row => ({ tree: [...row.children].map(name),
            text: [...row.firstElementChild.children].map(node => [name(node), text(node), node.localName === 'label' ? node.htmlFor : null]),
            control: control(row), flexWrap: getComputedStyle(row).flexWrap,
            controlBelow: row.lastElementChild.getBoundingClientRect().top >= row.firstElementChild.getBoundingClientRect().bottom - 0.5 })),
        field: { tree: [...field.children].map(name), label: [text(field.querySelector('label')), field.querySelector('label').htmlFor],
            textarea: { id: field.querySelector('textarea').id, value: field.querySelector('textarea').value,
                ...css(field.querySelector('textarea'), 'fontSize', 'minHeight', 'backgroundColor', 'color', 'borderTopColor') },
            ...css(field, 'display', 'rowGap', 'fontSize', 'marginTop', 'marginBottom') },
        actions: { buttons: [...actions.children].map(item => [text(item), item.className]),
            ...css(actions, 'display', 'flexWrap', 'columnGap', 'justifyContent'),
            primary: css(actions.querySelector('.ca-primary'), 'backgroundColor', 'color', 'fontSize', 'paddingTop', 'paddingLeft'),
            plain: css(actions.querySelector('button:not(.ca-primary)'), 'backgroundColor', 'color', 'fontSize', 'borderTopColor') },
        operations: [...surface.querySelectorAll(':scope > .operation')].map(row => ({ tree: [...row.children].map(name),
            title: text(row.querySelector('h3')), description: text(row.querySelector('p')),
            actions: [...row.querySelectorAll('.row-actions > button')].map(item => [text(item), item.className]) })),
        surface: css(surface, 'paddingTop', 'paddingLeft', 'backgroundColor', 'borderTopWidth', 'borderTopColor', 'borderTopLeftRadius'),
        row: css(firstRow, 'display', 'alignItems', 'justifyContent', 'columnGap', 'paddingTop', 'paddingBottom', 'borderTopWidth',
            'borderTopColor', 'marginTop'),
        label: css(firstRow.querySelector('label'), 'fontSize', 'fontWeight', 'color'),
        description: css(firstRow.querySelector('p'), 'fontSize', 'color', 'marginTop', 'maxWidth'),
        select: css(firstRow.querySelector('select'), 'fontSize', 'minWidth', 'backgroundColor', 'color', 'borderTopColor'),
        number: css(surface.querySelector('.setting-row input[type="number"]'), 'fontSize', 'minWidth', 'maxWidth', 'backgroundColor',
            'color', 'borderTopColor'),
        // Whether each select is as wide as the browser lays out a copy holding only its chosen option, i.e. it shows
        // that option whole (the browser's own select sizing, arrow included).
        selectsFit: [...surface.querySelectorAll('.setting-row > select')].map(select => {
            const copy = select.cloneNode(false);
            copy.removeAttribute('id');
            copy.append(new Option(select.selectedOptions[0]?.textContent ?? ''));
            // Important inline values: the phone cap is an !important rule (concept.css), which a plain inline style loses to.
            for (const [key, value] of [['position', 'absolute'], ['visibility', 'hidden'], ['width', 'max-content'], ['min-width', '0'],
                ['max-width', 'none'], ['flex', 'none']]) copy.style.setProperty(key, value, 'important');
            select.parentElement.append(copy);
            const needed = copy.getBoundingClientRect().width;
            copy.remove();
            return select.getBoundingClientRect().width + 0.5 >= needed;
        }),
        pageScroll: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    };
}

/**
 * The concept's Auto-tagging with the recorded deviations applied, for the fixture (all engines on disk, none loaded).
 * `phone` is a viewport at the concept's 700px breakpoint or below.
 */
function mapTaggingConcept(reference, { template, phone }) {
    const [model, existing, general, character] = reference.rows;
    const [preview] = reference.actions.buttons;
    return {
        ...reference,
        // D133 adds a row after the thresholds; D134 adds the operation after the actions.
        tree: [...reference.tree.slice(0, 6), 'div.setting-row', ...reference.tree.slice(6), 'div.operation'],
        rows: [
            // D132 (OQ5, OQ6): the four real engines with their download size, and the chosen one's live status.
            { ...model, text: [model.text[0], ['p', 'Downloaded. Not loaded.', null]],
                control: { ...model.control, options: MODEL_OPTIONS.map(label => [label, false]), selected: MODEL_OPTIONS[0] },
                // D132: at phone width the row wraps, so the engine select sits whole under its text.
                ...(phone ? { flexWrap: 'wrap', controlBelow: true } : {}) },
            existing, general, character,
            // D133: the persisted suggestion count, on the concept's number row.
            { ...general, text: [['label', 'Suggestions per image', 'tag-suggestions'], ['p', SUGGESTIONS, null]],
                control: { ...general.control, id: 'tag-suggestions', value: '10' } },
        ],
        // The saved template (the server's default until one is saved) replaces the concept's sample text.
        field: { ...reference.field, textarea: { ...reference.field.textarea, value: template } },
        // D132: Download (for an engine already on disk, "Download again") and Unload replace the two simulated jobs.
        actions: { ...reference.actions, buttons: [preview, ['Download again', ''], ['Unload', '']] },
        // D134: the prompt-tag batch, reviewed first.
        operations: [{ tree: ['div', 'div.row-actions'], title: 'Tag output images', description: BATCH, actions: [['Review', '']] }],
        // D132 (#398): every select shows its whole chosen option at every width (at 390 the concept's 120px cap cuts
        // them; production lets each grow past it).
        selectsFit: reference.selectsFit.map(() => true),
    };
}

async function conceptTagging(page, theme) {
    await openConcept(page, 'system', theme);
    await page.locator('#main .section-nav').getByRole('button', { name: 'Auto-tagging', exact: true }).click();
    await expect(page.locator('#main #tag-template')).toBeVisible();
    await page.mouse.move(0, 0);
    return page.evaluate(measureTagging, '#main');
}

async function productionTagging(page, theme) {
    await openProduction(page, 'system/tagging', theme);
    // Compare once the settings and the model status answered.
    await expect(page.locator('#workspace-main #tag-model')).toBeEnabled();
    await expect(page.locator('#workspace-main .setting-row').first().locator('p')).toHaveText('Downloaded. Not loaded.');
    await page.mouse.move(0, 0);
    return page.evaluate(measureTagging, '#workspace-main');
}

async function savedTemplate(request, page) {
    const response = await request.get('/prompt_manager/settings');
    await expect(response).toBeOK();
    return { template: (await response.json()).settings.autotag_template, phone: page.viewportSize().width <= 700 };
}

for (const theme of THEMES) for (const [width, height] of SIZES) {
    test(`auto-tagging matches the concept ${theme} ${width}x${height}`, async ({ page, request }, testInfo) => {
        await page.setViewportSize({ width, height });
        const concept = await conceptTagging(page, theme);
        await page.screenshot({ path: testInfo.outputPath(`concept-tagging-${theme}-${width}.png`), fullPage: true });
        const production = await productionTagging(page, theme);
        await page.screenshot({ path: testInfo.outputPath(`production-tagging-${theme}-${width}.png`), fullPage: true });
        expect(diffInventory(mapTaggingConcept(concept, await savedTemplate(request, page)), production)).toEqual([]);
        expect(production.pageScroll).toBe(false);
    });
}

// D132 (#398) at phone width and below the concept's smallest size: every option of every select, once chosen, shows
// whole, with no page scroll.
for (const theme of THEMES) for (const width of [390, 320]) {
    test(`auto-tagging selects fit every option at ${width} wide ${theme}`, async ({ page }, testInfo) => {
        await page.setViewportSize({ width, height: 844 });
        await productionTagging(page, theme);
        await page.locator('#workspace-main #tag-existing').selectOption('replace_after_review');
        await page.locator('#workspace-main section.surface').screenshot({ path: testInfo.outputPath(`production-tagging-selects-${theme}-${width}.png`) });
        const selects = page.locator('#workspace-main section.surface > .setting-row > select');
        await expect(selects).toHaveCount(2);
        for (let index = 0; index < 2; index += 1) {
            const select = selects.nth(index);
            const values = await select.evaluate(node => [...node.options].map(option => option.value));
            for (const value of values) {
                await select.selectOption(value);
                const measured = await page.evaluate(measureTagging, '#workspace-main');
                expect(measured.selectsFit, `${await select.getAttribute('id')}=${value}`).toEqual([true, true]);
                expect(measured.pageScroll, value).toBe(false);
            }
        }
    });
}

test('auto-tagging comparison detects mutations', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    const concept = await conceptTagging(page, 'dark');
    const saved = await savedTemplate(request, page);
    const expected = mapTaggingConcept(concept, saved);
    const production = await productionTagging(page, 'dark');
    expect(diffInventory(expected, production)).toEqual([]);
    // Unmapped, the section differs only where D132, D133 and D134 are recorded (and the saved template's text).
    expect(diffInventory(concept, production).map(item => item.path.split('.').slice(0, 3).join('.'))
        .filter((path, index, all) => all.indexOf(path) === index).sort()).toEqual([
        'actions.buttons.1', 'actions.buttons.2', 'field.textarea.value', 'operations.0.actions', 'operations.0.description',
        'operations.0.title', 'operations.0.tree', 'operations.length', 'rows.0.control', 'rows.0.text', 'rows.4.control', 'rows.4.controlBelow',
        'rows.4.flexWrap', 'rows.4.text', 'rows.4.tree', 'rows.length', 'tree.6', 'tree.7', 'tree.8', 'tree.9', 'tree.length'].sort());
    const measure = () => page.evaluate(measureTagging, '#workspace-main');
    const paths = async () => diffInventory(expected, await measure()).map(item => item.path);
    // A removed row.
    const undoRow = await mutate(page, { remove: ['#workspace-main section.surface > .setting-row:nth-of-type(2)'] });
    expect(await paths()).toEqual(expect.arrayContaining(['rows.length', 'tree.length']));
    await undoRow();
    // A renamed label.
    await page.evaluate(() => { document.querySelector('#workspace-main label[for="tag-existing"]').textContent = 'Existing'; });
    expect(await paths()).toEqual(['rows.1.text.0.1']);
    await page.evaluate(() => { document.querySelector('#workspace-main label[for="tag-existing"]').textContent = 'Existing tags'; });
    // A reordered option.
    await page.evaluate(() => { const select = document.querySelector('#workspace-main #tag-existing'); select.append(select.options[0]); });
    expect(await paths()).toEqual(expect.arrayContaining(['rows.1.control.options.0.0']));
    await page.evaluate(() => { const select = document.querySelector('#workspace-main #tag-existing'); select.prepend(select.options[2]); });
    // ca-primary moved to another action.
    await page.evaluate(() => {
        const [first, second] = document.querySelectorAll('#workspace-main section.surface > .row-actions > button');
        first.classList.remove('ca-primary');
        second.classList.add('ca-primary');
    });
    expect(await paths()).toEqual(expect.arrayContaining(['actions.buttons.0.1', 'actions.buttons.1.1']));
    await page.evaluate(() => {
        const [first, second] = document.querySelectorAll('#workspace-main section.surface > .row-actions > button');
        second.classList.remove('ca-primary');
        first.classList.add('ca-primary');
    });
    // A select squeezed until its chosen option is cut.
    const undoSqueeze = await mutate(page, { css: '#workspace-main #tag-model { max-width: 60px !important; min-width: 0 !important; }' });
    expect(await paths()).toEqual(expect.arrayContaining(['selectsFit.0']));
    await undoSqueeze();
    const undoSqueezeExisting = await mutate(page, { css: '#workspace-main #tag-existing { max-width: 60px !important; min-width: 0 !important; }' });
    expect(await paths()).toEqual(expect.arrayContaining(['selectsFit.1']));
    await undoSqueezeExisting();
    // A row that wraps where the concept's does not.
    const undoWrap = await mutate(page, { css: '#workspace-main .setting-row:has(> #tag-existing) { flex-wrap: wrap; }' });
    expect(await paths()).toEqual(['rows.1.flexWrap']);
    await undoWrap();
    // A swapped token.
    const undoToken = await mutate(page, { css: '#workspace-main .settings-layout { --line: rgb(255, 0, 0); }' });
    expect(await paths()).toEqual(expect.arrayContaining(['row.borderTopColor']));
    await undoToken();
    expect(diffInventory(expected, await measure())).toEqual([]);
});

// D132 at phone width: only the engine row wraps; Existing tags grows beside its text instead (#398).
test('auto-tagging at 390 wraps only the engine row', async ({ page, request }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const concept = await conceptTagging(page, 'dark');
    const production = await productionTagging(page, 'dark');
    expect(diffInventory(mapTaggingConcept(concept, await savedTemplate(request, page)), production)).toEqual([]);
    expect(production.rows.map(row => row.flexWrap)).toEqual(['wrap', 'nowrap', 'nowrap', 'nowrap', 'nowrap']);
    expect(production.selectsFit).toEqual([true, true]);
});

/** Runs in the page: the open review dialog: its title, body and the concept's tokens. */
function measureReview() {
    const dialog = document.querySelector('dialog#modal[open]');
    const css = (node, ...keys) => node ? Object.fromEntries(keys.map(key => [key, getComputedStyle(node)[key]])) : null;
    const name = node => [node.localName, ...[...node.classList].sort()].join('.');
    const text = node => node?.textContent.trim() ?? null;
    const body = dialog.querySelector('#modal-body');
    const grid = body.querySelector(':scope > dl.meta-grid');
    const note = [...body.querySelectorAll(':scope > p.small')].at(-1);
    const actions = body.querySelector(':scope > .dialog-actions');
    return {
        title: text(dialog.querySelector('#modal-title')),
        tree: [...body.children].map(name),
        heading: { text: text(body.querySelector(':scope > h3')),
            ...css(body.querySelector(':scope > h3'), 'fontSize', 'fontWeight', 'color', 'marginBottom') },
        grid: { rows: [...grid.children].map(row => [text(row.querySelector('dt')), text(row.querySelector('dd'))]),
            ...css(grid, 'display', 'gridTemplateColumns', 'columnGap', 'rowGap'),
            dt: css(grid.querySelector('dt'), 'fontSize', 'color', 'textTransform'), dd: css(grid.querySelector('dd'), 'fontSize', 'color') },
        backup: body.querySelector(':scope > .notice') && { tree: [...body.querySelector(':scope > .notice').children].map(name),
            text: [...body.querySelectorAll(':scope > .notice > div:first-child > *')].map(node => [node.localName, text(node)]),
            buttons: [...body.querySelectorAll(':scope > .notice button')].map(text) },
        note: { text: text(note), ...css(note, 'fontSize', 'color', 'marginTop') },
        actions: { tree: [...actions.children].map(name), ...css(actions, 'display', 'justifyContent', 'columnGap', 'marginTop'),
            buttons: [...actions.children].map(item => ({ text: text(item), className: item.className,
                ...css(item, 'backgroundColor', 'color', 'fontSize', 'paddingTop', 'paddingLeft', 'borderTopColor') })) },
    };
}

const number = value => value.toLocaleString('en-US');
/** DEC11 (#379): the Time row the preview's estimate reads as (WD14 on the test server's processor). */
function timeRow(plan) {
    const minutes = Math.max(1, Math.ceil(plan.estimate_seconds / 60));
    const text = minutes < 90 ? `about ${minutes} min`
        : `about ${Math.floor(minutes / 60)} h${minutes % 60 ? ` ${minutes % 60} min` : ''}`;
    return `${text} on a processor; a graphics card can be faster.`;
}

/**
 * D134 (#379): the server's batch preview in the concept's review: "Review tag output images" with "Tag output images"
 * as its `h3`, the model, policy, image counts, time estimate and settings in the concept's `dl.meta-grid` (Backup in
 * the concept's wording), the estimate sentence in the concept's `p.small`, the D55 backup box and Cancel / "Start
 * tagging".
 */
function mapBatchConcept(reference, plan) {
    const [cancel, start] = reference.actions.buttons;
    return {
        ...reference,
        title: 'Review tag output images',
        tree: ['h3', 'dl.meta-grid', 'p.small', 'div.notice', 'div.dialog-actions'],
        heading: { ...reference.heading, text: 'Tag output images' },
        grid: { ...reference.grid, rows: [['Model', 'WD14 SwinV2 · ~400 MB'], ['Existing tags', 'Merge with existing'],
            ['Images', number(plan.images)], ['Already auto-tagged', number(plan.already_tagged)],
            ['Images to tag', `about ${number(plan.to_tag)}`], ['Time', timeRow(plan)],
            ['Settings', `General 0.35 · Character 0.85 · up to ${number(plan.suggestion_limit)} tags per image`],
            ['Backup', 'Create and verify before changes']] },
        backup: { tree: ['div', 'div.row-actions'], text: [['h3', 'Back up the database first (recommended)'],
            ['p', 'Downloads a copy of the prompts database before this operation changes any records.']], buttons: ['Back up now'] },
        note: { ...reference.note, text: 'Nothing is written until you start. The number to tag is an estimate: images changed '
            + 'since they were tagged are tagged again, and missing files are skipped. Activity reports what was tagged.' },
        actions: { ...reference.actions, buttons: [cancel, { ...start, text: 'Start tagging' }] },
    };
}

async function conceptReview(page, theme) {
    await openConcept(page, 'system', theme);
    await page.locator('#main .section-nav').getByRole('button', { name: 'Maintenance', exact: true }).click();
    await page.locator('#main [data-plan="duplicate prompts"]').click();
    await expect(page.locator('dialog#modal[open] #modal-title')).toHaveText('Review maintenance plan');
    await page.mouse.move(0, 0);
    return page.evaluate(measureReview);
}

async function productionReview(page, theme) {
    await productionTagging(page, theme);
    // Merge reviews every listed still image (#379): a batch with work, the full review the concept's shell is compared
    // with.
    await page.locator('#workspace-main #tag-existing').selectOption('merge');
    const answer = page.waitForResponse(response => response.url().endsWith(PREVIEW_URL));
    await page.locator('#workspace-main .operation').getByRole('button', { name: 'Review', exact: true }).click();
    const plan = await (await answer).json();
    await expect(page.locator('dialog#modal[open] #modal-title')).toHaveText('Review tag output images');
    await page.mouse.move(0, 0);
    return { plan, production: await page.evaluate(measureReview) };
}

for (const theme of THEMES) for (const [width, height] of SIZES) {
    test(`tag output images review matches the concept ${theme} ${width}x${height}`, async ({ page }, testInfo) => {
        await page.setViewportSize({ width, height });
        const concept = await conceptReview(page, theme);
        expect(concept.tree).toEqual(['p.demo-note', 'h3', 'dl.meta-grid', 'p.small', 'div.dialog-actions']);
        await page.screenshot({ path: testInfo.outputPath(`concept-plan-review-${theme}-${width}.png`) });
        const { plan, production } = await productionReview(page, theme);
        // Under Merge every listed still image is tagged again: the batch has work to review.
        expect(plan.to_tag).toBeGreaterThan(0);
        expect(plan.estimate_device).toBe('cpu');
        await page.screenshot({ path: testInfo.outputPath(`production-tagging-review-${theme}-${width}.png`) });
        expect(diffInventory(mapBatchConcept(concept, plan), production)).toEqual([]);
    });
}

/**
 * D134 (CUJ 5): Replace after review never runs as a batch: the same review shell with the counts, the reason as its
 * `p.small`, no backup box and only Cancel (the shared close-only review Integrations also uses).
 */
function mapCancelOnlyConcept(reference, plan) {
    const [cancel] = reference.actions.buttons;
    const mapped = mapBatchConcept(reference, plan);
    return {
        ...mapped,
        tree: ['h3', 'dl.meta-grid', 'p.small', 'div.dialog-actions'],
        grid: { ...mapped.grid, rows: mapped.grid.rows.map(([term, value]) => [term, term === 'Existing tags' ? 'Replace after review' : value]) },
        backup: null,
        note: { ...reference.note, text: 'Replacing tags needs a review of each image. Use Preview tagging review, or choose Skip or '
            + 'Merge for a batch run.' },
        actions: { ...reference.actions, tree: [reference.actions.tree[0]], buttons: [cancel] },
    };
}

test('the Cancel-only review matches the concept review shell', async ({ page }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    const concept = await conceptReview(page, 'dark');
    await productionTagging(page, 'dark');
    await page.locator('#workspace-main #tag-existing').selectOption('replace_after_review');
    const answer = page.waitForResponse(response => response.url().endsWith(PREVIEW_URL));
    await page.locator('#workspace-main .operation').getByRole('button', { name: 'Review', exact: true }).click();
    const plan = await (await answer).json();
    expect(plan.review_required).toBe(true);
    await expect(page.locator('dialog#modal[open] #modal-title')).toHaveText('Review tag output images');
    await page.mouse.move(0, 0);
    expect(diffInventory(mapCancelOnlyConcept(concept, plan), await page.evaluate(measureReview))).toEqual([]);
});

test('tag output images review comparison detects mutations', async ({ page }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    const concept = await conceptReview(page, 'dark');
    const { plan } = await productionReview(page, 'dark');
    const expected = mapBatchConcept(concept, plan);
    const paths = async () => diffInventory(expected, await page.evaluate(measureReview)).map(item => item.path);
    expect(await paths()).toEqual([]);
    // Every label of the grid is compared (#379's rows included): a changed one is named by its row.
    for (const [index, [term]] of expected.grid.rows.entries()) {
        await page.evaluate(([at, label]) => {
            document.querySelectorAll('dialog#modal[open] dl.meta-grid dt')[at].textContent = label;
        }, [index, `${term}!`]);
        expect(await paths()).toEqual([`grid.rows.${index}.0`]);
        await page.evaluate(([at, label]) => {
            document.querySelectorAll('dialog#modal[open] dl.meta-grid dt')[at].textContent = label;
        }, [index, term]);
    }
    expect(await paths()).toEqual([]);
    const undoGrid = await mutate(page, { css: 'dialog#modal dl.meta-grid { column-gap: 3px; }' });
    expect(await paths()).toEqual(expect.arrayContaining(['grid.columnGap']));
    await undoGrid();
    const undoBox = await mutate(page, { remove: ['dialog#modal[open] #modal-body > .notice'] });
    expect(await paths()).toEqual(expect.arrayContaining(['backup', 'tree.length']));
    await undoBox();
    expect(await paths()).toEqual([]);
});
