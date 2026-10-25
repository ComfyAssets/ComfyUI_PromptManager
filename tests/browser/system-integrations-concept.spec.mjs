import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';
import { SIZES, THEMES, seedConcept, openConcept, openProduction, diffInventory, mutate } from './concept-inventory.mjs';

// #249 System S10 Task 28: System › Integrations (app.js renderSystem 682–684) and the LoRA import review against the
// immutable concept, on the concept-shaped fixture with a fake LoraManager install. Each concept measurement is mapped
// only by the recorded deviations (D106, D107, D114), each commented where it applies; after that the difference must
// be empty, and a mutated label, option, row or token must be noticed. The concept starts its sample import without a
// review, so the import review is compared with the concept's own record-changing review, `maintenancePlan`
// (953–958), whose shell, grid, note and actions it shares.
test.use({ locale: 'en-US', timezoneId: 'UTC' });

test.beforeEach(async ({ page, request, browserName }) => {
    await startCoverage(page, browserName);
    await seedConcept(request);
    await expect(await request.post('/__test__/system/reset')).toBeOK();
    await expect(await request.post('/__test__/integrations/reset')).toBeOK();
    await expect(await request.post('/__test__/jobs/restart')).toBeOK();
    page.conceptErrors = [];
    page.on('pageerror', error => page.conceptErrors.push(error.message));
});

test.afterEach(async ({ page, request, browserName }) => {
    await saveCoverage(page, browserName);
    await request.post('/__test__/jobs/restart');
    await request.post('/__test__/integrations/reset');
    await request.post('/__test__/system/reset');
    expect(page.conceptErrors).toEqual([]);
});

const PREVIEW_URL = '/prompt_manager/lora/import/preview';

/** Runs in the page: Integrations' surfaces under `mainSelector`, every row's text and control, geometry and tokens. */
function measureIntegrations(mainSelector) {
    const layout = document.querySelector(`${mainSelector} .settings-layout`);
    const css = (node, ...keys) => node ? Object.fromEntries(keys.map(key => [key, getComputedStyle(node)[key]])) : null;
    const name = node => [node.localName, ...[...node.classList].sort()].join('.');
    const text = node => node?.textContent.trim() ?? null;
    const control = row => {
        const select = row.querySelector(':scope > select');
        if (select) return { kind: 'select', id: select.id, options: [...select.options].map(option => [option.textContent, option.disabled]),
            selected: select.selectedOptions[0]?.textContent ?? null, disabled: select.disabled };
        const input = row.querySelector(':scope > input');
        if (input) return { kind: `input.${input.type}`, id: input.id, value: input.value, readOnly: input.readOnly };
        const toggle = row.querySelector(':scope > * input[type="checkbox"]');
        return toggle ? { kind: 'switch', id: toggle.id, wrapper: name(toggle.parentElement), checked: toggle.checked,
            disabled: toggle.disabled, ...css(toggle.parentElement, 'width', 'height') } : null;
    };
    const surfaces = [...layout.querySelectorAll(':scope > section.surface')];
    const firstRow = surfaces[0].querySelector('.setting-row');
    const note = layout.querySelector('.setting-row p.small');
    return {
        layout: [...layout.children].map(name),
        surfaces: surfaces.map(surface => ({ tree: [...surface.children].map(name), heading: text(surface.querySelector(':scope > h2')),
            intro: text(surface.querySelector(':scope > p')),
            rows: [...surface.querySelectorAll(':scope > .setting-row')].map(row => ({ tree: [...row.children].map(name),
                text: [...row.firstElementChild.children].map(node => [name(node), text(node), node.localName === 'label' ? node.htmlFor : null]),
                control: control(row) })),
            operations: [...surface.querySelectorAll(':scope > .operation')].map(row => ({ tree: [...row.children].map(name),
                title: text(row.querySelector('h3')), description: text(row.querySelector('p')),
                actions: [...row.querySelectorAll('.row-actions > button')].map(item => [text(item), item.className]) })) })),
        surface: css(surfaces[1], 'paddingTop', 'paddingLeft', 'backgroundColor', 'borderTopWidth', 'borderTopColor', 'borderTopLeftRadius',
            'marginTop'),
        row: css(firstRow, 'display', 'alignItems', 'justifyContent', 'columnGap', 'paddingTop', 'paddingBottom', 'borderTopWidth',
            'borderTopColor', 'marginTop'),
        label: css(firstRow.querySelector('label'), 'fontSize', 'fontWeight', 'color'),
        description: css(firstRow.querySelector('p'), 'fontSize', 'color', 'marginTop', 'maxWidth'),
        select: css(firstRow.querySelector('select'), 'fontSize', 'minWidth', 'maxWidth', 'backgroundColor', 'color', 'borderTopColor'),
        password: css(layout.querySelector('.setting-row input[type="password"]'), 'fontSize', 'minWidth', 'maxWidth', 'backgroundColor',
            'color', 'borderTopColor'),
        // The concept's `.small` text (its Save row status); production's row notes use the same class.
        small: css(layout.querySelector('#settings-status'), 'fontSize', 'color'),
        note: note && css(note, 'fontSize', 'color'),
        // Whether each select is as wide as the browser lays out a copy holding only its chosen option, i.e. it shows
        // that option whole (the browser's own select sizing, arrow included).
        selectsFit: [...layout.querySelectorAll('.setting-row > select')].map(select => {
            const copy = select.cloneNode(false);
            copy.removeAttribute('id');
            copy.append(new Option(select.selectedOptions[0]?.textContent ?? ''));
            Object.assign(copy.style, { position: 'absolute', visibility: 'hidden', width: 'max-content', minWidth: '0',
                maxWidth: 'none', flex: 'none' });
            select.parentElement.append(copy);
            const needed = copy.getBoundingClientRect().width;
            copy.remove();
            return select.getBoundingClientRect().width + 0.5 >= needed;
        }),
        pageScroll: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    };
}

const SEND_REASON = 'Sending always asks for the ComfyUI window and node, so “Ask each time” is the only option.';
const RENDERER_REASON = 'Prompt Manager nodes always follow ComfyUI’s own renderer setting.';
const RENDERER_NONE = 'No ComfyUI window is open in this browser, so its renderer is not known.';
const RETURN_REASON = 'Not available: sending applies the text in ComfyUI and keeps this page open.';
const PATH_DESCRIPTION = 'Found automatically in ComfyUI’s custom_nodes folders; read-only, as on the existing settings page.';

/**
 * The concept's Integrations with the recorded deviations applied, for the fixture's LoraManager (detected, disabled,
 * no key, trigger words off and not loaded).
 */
function mapIntegrationsConcept(reference) {
    const [comfy, lora] = reference.surfaces;
    const small = text => ['p.small', text, null];
    const firstOnly = row => ({ ...row, control: { ...row.control, options: row.control.options.map(([label], index) => [label, index > 0]) } });
    const [send, renderer, back] = comfy.rows;
    const [enable, triggers, key] = lora.rows;
    return {
        ...reference,
        surfaces: [
            { ...comfy, rows: [
                // D106: only "Ask each time" can be chosen, and the row says why.
                { ...firstOnly(send), text: [...send.text, small(SEND_REASON)] },
                // D106: only "Follow host renderer" can be chosen; the reason, then the detected renderer.
                { ...firstOnly(renderer), text: [...renderer.text, small(RENDERER_REASON), small(RENDERER_NONE)] },
                // D106: Return to canvas is not available: off, disabled and saying why.
                { ...back, text: [...back.text, small(RETURN_REASON)], control: { ...back.control, checked: false, disabled: true } },
            ] },
            { ...lora,
                // D106: the LoraManager path row (legacy, read-only) follows the integration toggle.
                tree: [lora.tree[0], lora.tree[1], 'div.setting-row', 'div.setting-row', 'div.setting-row', 'div.setting-row', 'div.operation'],
                rows: [
                    // The real detection replaces the concept's sample line; the integration is off in the fixture.
                    { ...enable, text: [enable.text[0], ['p', 'Detected: custom_nodes/ComfyUI-Lora-Manager.', null]],
                        control: { ...enable.control, checked: false } },
                    { tree: ['div', 'input'],
                        text: [['label', 'LoraManager path', 'lora-path'], ['p', PATH_DESCRIPTION, null]],
                        control: { kind: 'input.text', id: 'lora-path', value: 'custom_nodes/ComfyUI-Lora-Manager', readOnly: true } },
                    // The trigger cache is a separate fact from the setting: its own line.
                    { ...triggers, text: [...triggers.text, small('Trigger words are not loaded.')], control: { ...triggers.control, checked: false } },
                    // D114: no prototype warning; the saved-key line under it.
                    // (No key is saved in the fixture, so "Clear saved key" is not offered.)
                    { ...key, text: [key.text[0], ['p', 'Credentials stay in the server configuration.', null], small('No key saved.')],
                        control: { ...key.control, value: '' } },
                ],
                // D107: the import is reviewed first ("Review import"; the concept's button only simulated a job).
                operations: lora.operations.map(operation => ({ ...operation, actions: [['Review import', '']] })) },
        ],
        // D106: both selects show their whole chosen option at every width (at 390 the concept cuts "Follow host
        // renderer"; production lets the two selects grow to 160px and never shrink for the reason lines).
        selectsFit: reference.selectsFit.map(() => true),
        select: reference.select.maxWidth === '120px' ? { ...reference.select, maxWidth: '160px' } : reference.select,
        // The D106/D114 row notes are `p.small` inside the row text, so they read as its description (`.setting-row p`).
        note: { fontSize: reference.description.fontSize, color: reference.description.color },
    };
}

async function conceptIntegrations(page, theme) {
    await openConcept(page, 'system', theme);
    await page.locator('#main .section-nav').getByRole('button', { name: 'Integrations', exact: true }).click();
    await expect(page.locator('#main .settings-layout > section.surface')).toHaveCount(2);
    await page.mouse.move(0, 0);
    return page.evaluate(measureIntegrations, '#main');
}

async function productionIntegrations(page, theme) {
    await openProduction(page, 'system/integrations', theme);
    // Compare once the LoRA state and the renderer check answered.
    await expect(page.locator('#workspace-main #lora-path')).toHaveValue('custom_nodes/ComfyUI-Lora-Manager');
    await expect(page.locator('#workspace-main #renderer-status')).toHaveText(RENDERER_NONE);
    await page.mouse.move(0, 0);
    return page.evaluate(measureIntegrations, '#workspace-main');
}

const fakeLoraManager = async (request, data = {}) =>
    expect(await request.post('/__test__/integrations/lora-manager', { data })).toBeOK();

for (const theme of THEMES) for (const [width, height] of SIZES) {
    test(`integrations matches the concept ${theme} ${width}x${height}`, async ({ page, request }, testInfo) => {
        await page.setViewportSize({ width, height });
        await fakeLoraManager(request);
        const concept = await conceptIntegrations(page, theme);
        await page.screenshot({ path: testInfo.outputPath(`concept-integrations-${theme}-${width}.png`), fullPage: true });
        const production = await productionIntegrations(page, theme);
        await page.screenshot({ path: testInfo.outputPath(`production-integrations-${theme}-${width}.png`), fullPage: true });
        expect(diffInventory(mapIntegrationsConcept(concept), production)).toEqual([]);
        expect(production.pageScroll).toBe(false);
    });
}

// D106 below the concept's smallest size: on a 320px phone both selects still show their whole option, without page
// scroll (S10 re-review N9).
for (const theme of THEMES) test(`integrations selects fit at 320 wide ${theme}`, async ({ page, request }) => {
    await page.setViewportSize({ width: 320, height: 700 });
    await fakeLoraManager(request);
    const production = await productionIntegrations(page, theme);
    expect(production.selectsFit).toEqual([true, true]);
    expect(production.pageScroll).toBe(false);
});

test('integrations comparison detects mutations', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    await fakeLoraManager(request);
    const concept = await conceptIntegrations(page, 'dark');
    const expected = mapIntegrationsConcept(concept);
    const production = await productionIntegrations(page, 'dark');
    expect(diffInventory(expected, production)).toEqual([]);
    // Unmapped, the section differs only where D106, D107 and D114 are recorded.
    expect(diffInventory(concept, production).map(item => item.path.split('.').slice(0, 4).join('.'))
        .filter((path, index, all) => all.indexOf(path) === index).sort()).toEqual([
        'note', 'note.color', 'note.fontSize', 'surfaces.0.rows.0', 'surfaces.0.rows.1', 'surfaces.0.rows.2', 'surfaces.1.operations.0',
        'surfaces.1.rows.0', 'surfaces.1.rows.1', 'surfaces.1.rows.2', 'surfaces.1.rows.3', 'surfaces.1.rows.length',
        'surfaces.1.tree.5', 'surfaces.1.tree.6', 'surfaces.1.tree.length'].sort());
    const measure = () => page.evaluate(measureIntegrations, '#workspace-main');
    const paths = async () => diffInventory(expected, await measure()).map(item => item.path);
    // A removed row.
    const undoRow = await mutate(page, { remove: ['#workspace-main section.surface:nth-of-type(2) > .setting-row:nth-of-type(3)'] });
    expect(await paths()).toEqual(expect.arrayContaining(['surfaces.1.rows.length', 'surfaces.1.tree.length']));
    await undoRow();
    // A changed label and a changed option.
    await page.evaluate(() => { document.querySelector('#workspace-main label[for="renderer"]').textContent = 'Renderer'; });
    expect(await paths()).toEqual(['surfaces.0.rows.1.text.0.1']);
    await page.evaluate(() => { document.querySelector('#workspace-main label[for="renderer"]').textContent = 'Host compatibility'; });
    await page.evaluate(() => { document.querySelector('#workspace-main #send-destination option').textContent = 'Always ask'; });
    expect(await paths()).toEqual(expect.arrayContaining(['surfaces.0.rows.0.control.options.0.0']));
    await page.evaluate(() => { document.querySelector('#workspace-main #send-destination option').textContent = 'Ask each time'; });
    // An unsupported option that became selectable.
    await page.evaluate(() => { document.querySelectorAll('#workspace-main #renderer option')[1].disabled = false; });
    expect(await paths()).toEqual(['surfaces.0.rows.1.control.options.1.1']);
    await page.evaluate(() => { document.querySelectorAll('#workspace-main #renderer option')[1].disabled = true; });
    // A select squeezed until its chosen option is cut (S10 review M3).
    const undoSqueeze = await mutate(page, { css: '#workspace-main #renderer { max-width: 70px !important; }' });
    expect(await paths()).toEqual(['selectsFit.1']);
    await undoSqueeze();
    // A swapped token.
    const undoToken = await mutate(page, { css: '#workspace-main .settings-layout { --line: rgb(255, 0, 0); }' });
    expect(await paths()).toEqual(expect.arrayContaining(['row.borderTopColor']));
    await undoToken();
    expect(diffInventory(expected, await measure())).toEqual([]);
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
        details: body.querySelector(':scope > details') && { summary: text(body.querySelector(':scope > details > summary')),
            tree: [...body.querySelector(':scope > details').children].map(name) },
        backup: body.querySelector(':scope > .notice') && { tree: [...body.querySelector(':scope > .notice').children].map(name),
            text: [...body.querySelectorAll(':scope > .notice > div:first-child > *')].map(node => [node.localName, text(node)]),
            buttons: [...body.querySelectorAll(':scope > .notice button')].map(text) },
        note: { text: text(note), ...css(note, 'fontSize', 'color', 'marginTop') },
        actions: { tree: [...actions.children].map(name), ...css(actions, 'display', 'justifyContent', 'columnGap', 'marginTop'),
            buttons: [...actions.children].map(item => ({ text: text(item), className: item.className,
                ...css(item, 'backgroundColor', 'color', 'fontSize', 'paddingTop', 'paddingLeft', 'borderTopColor') })) },
    };
}

/**
 * D107: the server's import plan in the concept's review: "Review LoRA import" with "LoRA reference data" as its `h3`,
 * the concept's four rows with the plan's values plus Found and Network, the LoRAs it found (`details`), the hold
 * line with the cache sentence in the concept's `p.small`, the D55 backup box, and Cancel / "Start import".
 */
function mapImportConcept(reference, plan) {
    const [cancel, start] = reference.actions.buttons;
    return {
        ...reference,
        title: 'Review LoRA import',
        tree: ['h3', 'dl.meta-grid', 'details', 'p.small', 'div.notice', 'div.dialog-actions'],
        heading: { ...reference.heading, text: 'LoRA reference data' },
        grid: { ...reference.grid, rows: [['Scope', plan.scope.label], ['Found', '2 metadata files · 2 readable'],
            ['Protected', 'Nothing is protected'], ['Network', 'Downloads up to 2 example images from CivitAI'],
            ['Backup', `Create and verify before changes (${plan.backup.destination_label})`],
            ['Media on disk', 'No files are removed; image links are replaced']] },
        details: { summary: 'LoRAs found (2)', tree: ['summary', 'pre'] },
        backup: { tree: ['div', 'div.row-actions'], text: [['h3', 'Back up the database first (recommended)'],
            ['p', 'Downloads a copy of the prompts database before this operation changes any records.']], buttons: ['Back up now'] },
        note: { ...reference.note, text: 'Nothing changes until you apply. This review holds for 15 minutes. '
            + 'Example images downloaded before a failure stay in the image cache.' },
        actions: { ...reference.actions, buttons: [cancel, { ...start, text: 'Start import' }] },
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

async function productionReview(page, request, theme) {
    await openProduction(page, 'system/integrations', theme);
    await expect(page.locator('#workspace-main #lora-path')).toHaveValue('custom_nodes/ComfyUI-Lora-Manager');
    // The renderer line above settles first: its new text moves the rows below (a click must not land mid-shift).
    await expect(page.locator('#workspace-main #renderer-status')).toHaveText(RENDERER_NONE);
    // The import reviews what is enabled: save the integration on first.
    await page.locator('#workspace-main #lora-enable').check();
    await page.getByRole('button', { name: 'Save preferences', exact: true }).click();
    await expect(page.locator('#settings-status')).toHaveText('Saved to the server configuration.');
    const answer = page.waitForResponse(response => response.url().endsWith(PREVIEW_URL));
    await page.locator('#workspace-main').getByRole('button', { name: 'Review import', exact: true }).click();
    const plan = await (await answer).json();
    await expect(page.locator('dialog#modal[open] #modal-title')).toHaveText('Review LoRA import');
    await page.mouse.move(0, 0);
    return { plan, production: await page.evaluate(measureReview) };
}

for (const theme of THEMES) for (const [width, height] of SIZES) {
    test(`LoRA import review matches the concept ${theme} ${width}x${height}`, async ({ page, request }, testInfo) => {
        await page.setViewportSize({ width, height });
        await fakeLoraManager(request, { loras: 2, civitai: true });
        const concept = await conceptReview(page, theme);
        expect(concept.tree).toEqual(['p.demo-note', 'h3', 'dl.meta-grid', 'p.small', 'div.dialog-actions']);
        await page.screenshot({ path: testInfo.outputPath(`concept-plan-review-${theme}-${width}.png`) });
        const { plan, production } = await productionReview(page, request, theme);
        await page.screenshot({ path: testInfo.outputPath(`production-lora-review-${theme}-${width}.png`) });
        expect(diffInventory(mapImportConcept(concept, plan), production)).toEqual([]);
    });
}

test('LoRA import review comparison detects mutations', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    await fakeLoraManager(request, { loras: 2, civitai: true });
    const concept = await conceptReview(page, 'dark');
    const { plan } = await productionReview(page, request, 'dark');
    const expected = mapImportConcept(concept, plan);
    const paths = async () => diffInventory(expected, await page.evaluate(measureReview)).map(item => item.path);
    expect(await paths()).toEqual([]);
    await page.evaluate(() => { document.querySelectorAll('dialog#modal[open] dl.meta-grid dt')[3].textContent = 'Downloads'; });
    expect(await paths()).toEqual(['grid.rows.3.0']);
    await page.evaluate(() => { document.querySelectorAll('dialog#modal[open] dl.meta-grid dt')[3].textContent = 'Network'; });
    const undoGrid = await mutate(page, { css: 'dialog#modal dl.meta-grid { column-gap: 3px; }' });
    expect(await paths()).toEqual(expect.arrayContaining(['grid.columnGap']));
    await undoGrid();
    const undoBox = await mutate(page, { remove: ['dialog#modal[open] #modal-body > .notice'] });
    expect(await paths()).toEqual(expect.arrayContaining(['backup', 'tree.length']));
    await undoBox();
    expect(await paths()).toEqual([]);
});
