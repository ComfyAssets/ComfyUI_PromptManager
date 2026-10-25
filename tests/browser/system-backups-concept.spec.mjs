import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';
import { SIZES, THEMES, seedConcept, openConcept, openProduction, diffInventory, mutate } from './concept-inventory.mjs';

// #249 System S9 Task 25: System › Backup & export (app.js renderSystem 691–693), its "Restore preview" (`restore`,
// 1472–1476) and its "Import & inspect" (`import`, 1483–1487) against the immutable concept, on the concept-shaped
// fixture. Each concept measurement is mapped only by the recorded deviations (D103, D104, D105; the Import dialog's
// D15 and D55), each commented where it applies; after that the difference must be empty, and a mutated label, row,
// danger mark or token must be noticed.
test.use({ locale: 'en-US', timezoneId: 'UTC' });

test.beforeEach(async ({ page, request, browserName }) => {
    await startCoverage(page, browserName);
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
    expect(page.conceptErrors).toEqual([]);
});

const PREVIEW_URL = '/prompt_manager/restore/preview';

/** Runs in the page: the section's surfaces under `mainSelector`, their text, controls, geometry and tokens. */
function measureBackups(mainSelector) {
    const layout = document.querySelector(`${mainSelector} .settings-layout`);
    const css = (node, ...keys) => node ? Object.fromEntries(keys.map(key => [key, getComputedStyle(node)[key]])) : null;
    const name = node => [node.localName, ...[...node.classList].sort()].join('.');
    const text = node => node?.textContent.trim() ?? null;
    const button = node => ({ text: text(node), className: node.className,
        ...css(node, 'backgroundColor', 'color', 'fontSize', 'paddingTop', 'paddingLeft', 'borderTopColor') });
    const base = layout.getBoundingClientRect();
    const box = node => {
        const rect = node.getBoundingClientRect();
        return { left: Math.round(rect.left - base.left), top: Math.round(rect.top - base.top), width: Math.round(rect.width) };
    };
    const control = row => {
        const select = row.querySelector(':scope > select');
        if (select) return { kind: 'select', id: select.id, options: [...select.options].map(option => [option.textContent, option.disabled]),
            selected: select.selectedOptions[0]?.textContent ?? null, disabled: select.disabled };
        const toggle = row.querySelector(':scope > * input[type="checkbox"]');
        return toggle ? { kind: 'switch', id: toggle.id, wrapper: name(toggle.parentElement), checked: toggle.checked,
            disabled: toggle.disabled, ...css(toggle.parentElement, 'width', 'height') } : null;
    };
    const columns = layout.querySelector(':scope > .two-col');
    const surfaces = [...columns.querySelectorAll(':scope > section.surface')];
    const policy = layout.querySelector(':scope > section.surface');
    const chip = policy.querySelector(':scope > h2 > .chip');
    const row = policy.querySelector('.setting-row');
    const status = surfaces[0].querySelector(':scope > p.small');
    const warning = policy.querySelector(':scope > .warning-note');
    return {
        layout: [...layout.children].map(name),
        columns: { ...css(columns, 'display', 'gridTemplateColumns', 'columnGap'), box: box(columns) },
        surfaces: surfaces.map(surface => ({ tree: [...surface.children].map(name), heading: text(surface.querySelector(':scope > h2')),
            intro: text(surface.querySelector(':scope > p:not(.small)')), box: box(surface),
            actions: [...surface.querySelectorAll(':scope > .row-actions')].map(group => ({ tree: [...group.children].map(name),
                ...css(group, 'display', 'columnGap', 'flexWrap', 'marginTop'),
                buttons: [...group.children].map(button) })) })),
        status: { text: text(status), ...css(status, 'fontSize', 'color', 'marginTop') },
        surface: css(surfaces[0], 'paddingTop', 'paddingLeft', 'backgroundColor', 'borderTopWidth', 'borderTopColor', 'borderTopLeftRadius'),
        h2: css(surfaces[0].querySelector('h2'), 'fontSize', 'fontWeight', 'color', 'marginBottom'),
        intro: css(surfaces[0].querySelector(':scope > p'), 'fontSize', 'color', 'lineHeight'),
        // Its place below the columns (the concept's `.surface + .surface` 18px), whatever the columns' height.
        policy: { tree: [...policy.children].map(name), heading: text(policy.querySelector(':scope > h2')),
            box: { left: box(policy).left, width: box(policy).width,
                gap: Math.round(policy.getBoundingClientRect().top - columns.getBoundingClientRect().bottom) },
            chip: { text: text(chip), className: chip.className, ...css(chip, 'color', 'fontSize', 'borderTopColor', 'backgroundColor') },
            rows: [...policy.querySelectorAll(':scope > .setting-row')].map(item => ({ tree: [...item.children].map(name),
                text: [...item.firstElementChild.children].map(node => [name(node), text(node), node.localName === 'label' ? node.htmlFor : null]),
                control: control(item) })),
            warning: { text: text(warning), ...css(warning, 'fontSize', 'color', 'marginTop', 'marginBottom') } },
        row: css(row, 'display', 'alignItems', 'justifyContent', 'columnGap', 'paddingTop', 'paddingBottom', 'borderTopWidth', 'borderTopColor',
            'marginTop'),
        label: css(row.querySelector('label'), 'fontSize', 'fontWeight', 'color'),
        description: css(row.querySelector('p'), 'fontSize', 'color', 'marginTop', 'maxWidth'),
        select: css(policy.querySelector('.setting-row > select'), 'fontSize', 'minWidth', 'maxWidth', 'backgroundColor', 'color',
            'borderTopColor'),
        pageScroll: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    };
}

const SCHEDULE_REASON = 'Daily and Weekly are not available yet: back up with Back up whenever you choose.';
const SAFETY_DESCRIPTION = 'Keep a verified copy of the database on disk before maintenance, cleanups and LoRA imports change ' +
    'records. A restore always keeps one.';

/**
 * D103: "Back up" (the concept's "Simulate backup"), the status line from /backup/status, "Export JSON" (its "Export
 * sample JSON") and the two secondary exports in a second `.row-actions.spaced` with the concept's plain buttons.
 * D104: the toggle's line says what the policy does, the schedule's reason replaces the prototype's proposal line,
 * Daily and Weekly are disabled, and the warning note is truthful. The fixture has no backup yet.
 */
function mapBackupsConcept(reference) {
    const [database, portable] = reference.surfaces;
    const [backup, restore] = database.actions[0].buttons;
    const [exportButton, importButton] = portable.actions[0].buttons;
    const secondary = ['Export all prompts JSON', 'Export saved pairs JSON'].map(label => ({ ...exportButton, text: label }));
    return {
        ...reference,
        surfaces: [
            { ...database, actions: [{ ...database.actions[0], buttons: [{ ...backup, text: 'Back up' }, restore] }] },
            { ...portable, tree: [...portable.tree, 'div.row-actions.spaced'],
                actions: [{ ...portable.actions[0], buttons: [{ ...exportButton, text: 'Export JSON' }, importButton] },
                    { ...portable.actions[0], tree: ['button', 'button'], buttons: secondary }] },
        ],
        status: { ...reference.status, text: 'No backup recorded.' },
        policy: { ...reference.policy,
            rows: reference.policy.rows.map(item => (item.control.kind !== 'select'
                // D104 (M2): what the safety-backup policy really does.
                ? { ...item, text: item.text.map(([node, value, target]) => [node, node === 'p' ? SAFETY_DESCRIPTION : value, target]) }
                : { ...item,
                    text: item.text.map(([node, value, target]) => [node, node === 'p' ? SCHEDULE_REASON : value, target]),
                    control: { ...item.control, options: item.control.options.map(([label]) => [label, label !== 'Manual only']) } })),
            warning: { ...reference.policy.warning, text: 'Restores are staged, migrated and validated before replacing live data.' } },
    };
}

async function conceptBackups(page, theme) {
    await openConcept(page, 'system', theme);
    await page.locator('#main .section-nav').getByRole('button', { name: 'Backup & export', exact: true }).click();
    await expect(page.locator('#main .settings-layout > .two-col > section.surface')).toHaveCount(2);
    await page.mouse.move(0, 0);
    return page.evaluate(measureBackups, '#main');
}

async function productionBackups(page, theme, status = 'No backup recorded.') {
    await openProduction(page, 'system/backups', theme);
    await expect(page.locator('#workspace-main .settings-layout > .two-col > section.surface')).toHaveCount(2);
    // The status line and the policy row are their own reads: compare once both answered.
    await expect(page.locator('#workspace-main #backup-status')).toHaveText(status);
    await expect(page.locator('#workspace-main #backup-before')).toBeEnabled();
    await page.mouse.move(0, 0);
    return page.evaluate(measureBackups, '#workspace-main');
}

for (const theme of THEMES) for (const [width, height] of SIZES) {
    test(`backup & export matches the concept ${theme} ${width}x${height}`, async ({ page }, testInfo) => {
        await page.setViewportSize({ width, height });
        const concept = await conceptBackups(page, theme);
        await page.screenshot({ path: testInfo.outputPath(`concept-backups-${theme}-${width}.png`), fullPage: true });
        const production = await productionBackups(page, theme);
        await page.screenshot({ path: testInfo.outputPath(`production-backups-${theme}-${width}.png`), fullPage: true });
        expect(diffInventory(mapBackupsConcept(concept), production)).toEqual([]);
        expect(production.pageScroll).toBe(false);
    });
}

/** Runs in the page: the open dialog's title, body tree, grid, note, backup box and actions with the concept's tokens. */
function measureDialog() {
    const dialog = document.querySelector('dialog#modal[open]');
    const css = (node, ...keys) => node ? Object.fromEntries(keys.map(key => [key, getComputedStyle(node)[key]])) : null;
    const name = node => [node.localName, ...[...node.classList].sort()].join('.');
    const text = node => node?.textContent.trim() ?? null;
    const body = dialog.querySelector('#modal-body');
    const grid = body.querySelector(':scope > dl.meta-grid');
    const note = body.querySelector(':scope > p.small');
    const actions = body.querySelector(':scope > .dialog-actions');
    const list = body.querySelector(':scope > .operation-list');
    return {
        title: text(dialog.querySelector('#modal-title')),
        tree: [...body.children].map(name),
        grid: grid && { rows: [...grid.children].map(row => [text(row.querySelector('dt')), text(row.querySelector('dd'))]),
            ...css(grid, 'display', 'gridTemplateColumns', 'columnGap', 'rowGap'),
            dt: css(grid.querySelector('dt'), 'fontSize', 'color', 'textTransform'), dd: css(grid.querySelector('dd'), 'fontSize', 'color') },
        note: note && { text: text(note), ...css(note, 'fontSize', 'color') },
        backup: body.querySelector(':scope > .notice') && { tree: [...body.querySelector(':scope > .notice').children].map(name),
            text: [...body.querySelectorAll(':scope > .notice > div:first-child > *')].map(node => [node.localName, text(node)]),
            buttons: [...body.querySelectorAll(':scope > .notice button')].map(text) },
        list: list && { ...css(list, 'display', 'rowGap'),
            rows: [...list.children].map(row => ({ tree: [...row.children].map(name), title: text(row.querySelector('h3')),
                description: text(row.querySelector('p')), buttons: [...row.querySelectorAll('button')].map(text),
                ...css(row, 'display', 'alignItems', 'paddingTop', 'borderTopColor') })) },
        actions: actions && { tree: [...actions.children].map(name), ...css(actions, 'display', 'justifyContent', 'columnGap', 'marginTop'),
            buttons: [...actions.children].map(item => ({ text: text(item), className: item.className,
                ...css(item, 'backgroundColor', 'color', 'fontSize', 'paddingTop', 'paddingLeft', 'borderTopColor') })) },
    };
}

const RESTORE_BACKUP_BOX = Object.freeze({ tree: ['div', 'div.row-actions'], text: [['h3', 'Back up the database first (recommended)'],
    ['p', 'Downloads a copy of the prompts database before the restore replaces it.']], buttons: ['Back up now'] });

/**
 * D105: the server's review in the concept's "Restore preview": no sample note; the four rows with the review's values,
 * then "In the backup" and "In the library now"; the hold line in the concept's dialog `p.small` (its Import dialog's
 * intro, `small`); the D55 backup box before the actions; "Restore" (the concept's "Simulate restore", still danger).
 */
function mapRestoreConcept(reference, small, rows) {
    const [cancel, restore] = reference.actions.buttons;
    return {
        ...reference,
        tree: ['dl.meta-grid', 'p.small', 'div.notice', 'div.dialog-actions'],
        grid: { ...reference.grid, rows },
        note: { ...small, text: 'Restore replaces the whole library with this backup and runs alone in Activity. ' +
            'Nothing changes until you restore. This review holds for 15 minutes.' },
        backup: RESTORE_BACKUP_BOX,
        actions: { ...reference.actions, buttons: [cancel, { ...restore, text: 'Restore' }] },
    };
}

/** D15 and D55, as on Images: Rescan for "Scan an output folder", then "Scan for metadata"; no portable recipe import. */
function mapImportConcept(reference) {
    return { ...reference,
        note: { ...reference.note, text: 'Image inspection and output scanning have different effects. Choose the workflow you want.' },
        list: { ...reference.list, rows: reference.list.rows.filter(row => row.title !== 'Import portable recipes').flatMap(row =>
            row.title === 'Scan an output folder'
                ? [{ ...row, description: 'Find files added or removed in the output folders.', buttons: ['Rescan'] },
                    { ...row, title: 'Scan for metadata' }] : [row]) } };
}

async function conceptDialog(page, theme, action, title) {
    await conceptBackups(page, theme);
    await page.locator(`#main .settings-layout [data-action="${action}"]`).click();
    await expect(page.locator('dialog#modal[open] #modal-title')).toHaveText(title);
    await page.mouse.move(0, 0);
    return page.evaluate(measureDialog);
}

/** Production's restore review of a fresh backup of this server's library; the review's own answer too. */
async function productionRestore(page, request, theme) {
    const copy = await request.get('/prompt_manager/backup');
    await expect(copy).toBeOK();
    // That copy is this session's download now.
    await productionBackups(page, theme, /^Downloaded backup · .+ · verified\.$/);
    const chooser = page.waitForEvent('filechooser');
    const answer = page.waitForResponse(response => response.url().endsWith(PREVIEW_URL));
    await page.locator('#workspace-main').getByRole('button', { name: 'Review restore…', exact: true }).click();
    await (await chooser).setFiles({ name: 'prompts_backup_20261025_093000.db', mimeType: 'application/octet-stream',
        buffer: await copy.body() });
    const preview = await (await answer).json();
    await expect(page.locator('dialog#modal[open] #modal-title')).toHaveText('Restore preview');
    await page.mouse.move(0, 0);
    return { preview, production: await page.evaluate(measureDialog) };
}

const counts = summary => `${summary.prompts} ${summary.prompts === 1 ? 'prompt' : 'prompts'} · ${summary.image_links} ` +
    `${summary.image_links === 1 ? 'image link' : 'image links'} · ${summary.pairs} ${summary.pairs === 1 ? 'saved pair' : 'saved pairs'}` +
    ` · ${summary.runs} ${summary.runs === 1 ? 'run' : 'runs'}`;

function restoreRows(preview, snapshot) {
    return [['Snapshot', snapshot], ['Integrity', 'Verified'], ['Migration', 'Current schema'],
        ['Safety backup', `Created before replacing (${preview.safety_backup.destination_label})`],
        ['In the backup', counts(preview.summary)], ['In the library now', counts(preview.live)]];
}

for (const theme of THEMES) for (const [width, height] of SIZES) {
    test(`restore preview and import dialogs match the concept ${theme} ${width}x${height}`, async ({ page, request }, testInfo) => {
        await page.setViewportSize({ width, height });
        // The concept's own dialog `p.small` (its Import dialog's intro) is the reference for the review's note.
        const conceptImport = await conceptDialog(page, theme, 'import', 'Import & inspect');
        await page.screenshot({ path: testInfo.outputPath(`concept-import-${theme}-${width}.png`) });
        const conceptRestore = await conceptDialog(page, theme, 'restore', 'Restore preview');
        expect(conceptRestore.tree).toEqual(['p.demo-note', 'dl.meta-grid', 'div.dialog-actions']);
        await page.screenshot({ path: testInfo.outputPath(`concept-restore-${theme}-${width}.png`) });
        const { preview, production } = await productionRestore(page, request, theme);
        await page.screenshot({ path: testInfo.outputPath(`production-restore-${theme}-${width}.png`) });
        const snapshot = production.grid.rows[0][1];
        expect(snapshot).toMatch(/^prompts_backup_20261025_093000.db · (\d+ KB|\d+\.\d MB) · .+$/);
        const { text: _intro, ...small } = conceptImport.note;
        expect(diffInventory(mapRestoreConcept(conceptRestore, small, restoreRows(preview, snapshot)), production)).toEqual([]);
        await page.keyboard.press('Escape');
        await expect(page.locator('dialog#modal[open]')).toHaveCount(0);
        await page.locator('#workspace-main').getByRole('button', { name: 'Import / inspect file', exact: true }).click();
        await expect(page.locator('dialog#modal[open] #modal-title')).toHaveText('Import & inspect');
        await page.mouse.move(0, 0);
        await page.screenshot({ path: testInfo.outputPath(`production-import-${theme}-${width}.png`) });
        expect(diffInventory(mapImportConcept(conceptImport), await page.evaluate(measureDialog))).toEqual([]);
    });
}

test('backup & export comparison detects mutations', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    const concept = await conceptBackups(page, 'dark');
    const expected = mapBackupsConcept(concept);
    await productionBackups(page, 'dark');
    const measure = () => page.evaluate(measureBackups, '#workspace-main');
    const paths = async () => diffInventory(expected, await measure()).map(item => item.path);
    expect(await paths()).toEqual([]);
    // Unmapped, the section differs exactly on the D103 and D104 paths (the added row's own paths, then the rest).
    const unmapped = diffInventory(concept, await measure()).map(item => item.path).sort();
    const added = 'surfaces.1.actions.1.';
    expect(unmapped.some(path => path.startsWith(added))).toBe(true);
    expect(unmapped.filter(path => !path.startsWith(added))).toEqual([
        'policy.rows.0.text.1.1', 'policy.rows.1.control.options.1.1', 'policy.rows.1.control.options.2.1', 'policy.rows.1.text.1.1',
        'policy.warning.text',
        'status.text', 'surfaces.0.actions.0.buttons.0.text', 'surfaces.1.actions.0.buttons.0.text', 'surfaces.1.actions.length',
        'surfaces.1.tree.3', 'surfaces.1.tree.length']);
    // A renamed label, a removed row, a lost danger mark and a swapped token are noticed.
    await page.evaluate(() => { document.querySelector('#workspace-main .setting-row label').textContent = 'Safety backup'; });
    expect(await paths()).toEqual(['policy.rows.0.text.0.1']);
    await page.evaluate(() => { document.querySelector('#workspace-main .setting-row label').textContent = 'Create a safety backup before maintenance'; });
    const undoRow = await mutate(page, { remove: ['#workspace-main .settings-layout > section.surface > .setting-row:last-of-type'] });
    expect(await paths()).toEqual(expect.arrayContaining(['policy.rows.length', 'policy.tree.length']));
    await undoRow();
    await page.evaluate(() => { document.querySelector('#workspace-main button.danger').classList.remove('danger'); });
    expect(await paths()).toEqual(expect.arrayContaining(['surfaces.0.actions.0.buttons.1.className']));
    await page.evaluate(() => { document.querySelector('#workspace-main .two-col .row-actions > button:nth-child(2)').classList.add('danger'); });
    const undoToken = await mutate(page, { css: '#workspace-main .settings-layout { --line: rgb(255, 0, 0); }' });
    expect(await paths()).toEqual(expect.arrayContaining(['row.borderTopColor']));
    await undoToken();
    const undoChip = await mutate(page, { css: '#workspace-main .chip.blue { color: rgb(255, 0, 0); }' });
    expect(await paths()).toEqual(['policy.chip.color']);
    await undoChip();
    expect(await paths()).toEqual([]);

    // The restore review: a renamed term, a lost danger mark and the grid's token are noticed.
    const conceptImport = await conceptDialog(page, 'dark', 'import', 'Import & inspect');
    const conceptRestore = await conceptDialog(page, 'dark', 'restore', 'Restore preview');
    const { preview, production } = await productionRestore(page, request, 'dark');
    const { text: _intro, ...small } = conceptImport.note;
    const expectedRestore = mapRestoreConcept(conceptRestore, small, restoreRows(preview, production.grid.rows[0][1]));
    const restorePaths = async () => diffInventory(expectedRestore, await page.evaluate(measureDialog)).map(item => item.path);
    expect(await restorePaths()).toEqual([]);
    await page.evaluate(() => { document.querySelector('dialog#modal[open] dl.meta-grid dt').textContent = 'Backup'; });
    expect(await restorePaths()).toEqual(['grid.rows.0.0']);
    await page.evaluate(() => { document.querySelector('dialog#modal[open] dl.meta-grid dt').textContent = 'Snapshot'; });
    await page.evaluate(() => { document.querySelector('dialog#modal[open] .dialog-actions .danger').classList.remove('danger'); });
    expect(await restorePaths()).toEqual(expect.arrayContaining(['actions.buttons.1.className']));
    await page.evaluate(() => { document.querySelector('dialog#modal[open] .dialog-actions button:last-child').classList.add('danger'); });
    const undoGrid = await mutate(page, { css: 'dialog#modal dl.meta-grid { column-gap: 3px; }' });
    expect(await restorePaths()).toEqual(expect.arrayContaining(['grid.columnGap']));
    await undoGrid();
    expect(await restorePaths()).toEqual([]);
});
