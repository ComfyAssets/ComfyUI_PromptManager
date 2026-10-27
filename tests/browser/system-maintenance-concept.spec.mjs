import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';
import { SIZES, THEMES, seedConcept, openConcept, openProduction, diffInventory, mutate, measureReview, mapPlanConcept }
    from './concept-inventory.mjs';

// #249 System S7 Task 20: System › Maintenance (app.js renderSystem 688–690), its plan dialog (`maintenancePlan`,
// 953–958) for each operation and its duplicates dialog (`duplicates`, 1457–1461) against the immutable concept, on
// the concept-shaped real fixture. Each concept measurement is mapped only by the recorded deviations (D100, D101,
// D102; the health report is D95, compared in system-concept.spec), each commented where it applies; after that the
// difference must be empty, and a mutated label, row or token must be noticed.
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

const PREVIEW_URL = '/prompt_manager/maintenance/preview';

/** Runs in the page: Maintenance's notice and surfaces under `mainSelector`, with the concept's geometry and tokens. */
function measureMaintenance(mainSelector) {
    const layout = document.querySelector(`${mainSelector} .settings-layout`);
    const css = (node, ...keys) => node ? Object.fromEntries(keys.map(key => [key, getComputedStyle(node)[key]])) : null;
    const name = node => [node.localName, ...[...node.classList].sort()].join('.');
    const text = node => node?.textContent.trim() ?? null;
    const button = node => ({ text: text(node), className: node.className,
        ...css(node, 'backgroundColor', 'color', 'fontSize', 'paddingTop', 'paddingLeft', 'borderTopColor', 'opacity', 'cursor') });
    const box = node => {
        const rect = node.getBoundingClientRect(), base = layout.getBoundingClientRect();
        return { left: Math.round(rect.left - base.left), width: Math.round(rect.width) };
    };
    const notice = layout.querySelector(':scope > .notice');
    const surfaces = [...layout.querySelectorAll(':scope > section.surface')];
    const operation = surfaces[0].querySelector('.operation');
    return {
        layout: [...layout.children].map(name),
        notice: { tree: [...notice.children].map(name), text: [...notice.querySelectorAll(':scope > div > *')].map(node => [node.localName, text(node)]),
            run: button(notice.querySelector(':scope > button')),
            ...css(notice, 'display', 'alignItems', 'justifyContent', 'columnGap', 'borderTopColor', 'backgroundColor', 'paddingTop', 'marginBottom') },
        surfaces: surfaces.map(surface => ({ tree: [...surface.children].map(name), heading: text(surface.querySelector(':scope > h2')),
            box: box(surface),
            rows: [...surface.querySelectorAll(':scope > .operation')].map(row => ({ tree: [...row.children].map(name),
                title: text(row.querySelector('h3')), description: text(row.querySelector('p')),
                actions: [...row.querySelectorAll('.row-actions > button')].map(button) })) })),
        surface: css(surfaces[0], 'paddingTop', 'paddingLeft', 'backgroundColor', 'borderTopWidth', 'borderTopColor', 'borderTopLeftRadius', 'marginTop'),
        h2: css(surfaces[0].querySelector('h2'), 'fontSize', 'fontWeight', 'color', 'marginBottom'),
        operation: css(operation, 'display', 'alignItems', 'justifyContent', 'columnGap', 'paddingTop', 'paddingBottom',
            'borderTopWidth', 'borderTopColor'),
        h3: css(operation.querySelector('h3'), 'fontSize', 'fontWeight', 'color'),
        p: css(operation.querySelector('p'), 'fontSize', 'color', 'marginTop'),
        actions: css(operation.querySelector('.row-actions'), 'display', 'columnGap', 'flexShrink'),
        pageScroll: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    };
}

/** D102: the Metadata cache row says why its review is unavailable and its button is aria-disabled (D93's dimmed style). */
function mapMaintenanceConcept(reference) {
    return {
        ...reference,
        surfaces: reference.surfaces.map(surface => ({ ...surface, rows: surface.rows.map(row => row.title !== 'Metadata cache' ? row : {
            ...row, description: `${row.description} Nothing is cached in this browser.`,
            actions: row.actions.map(action => ({ ...action, opacity: '0.55', cursor: 'not-allowed' })) }) })),
    };
}

async function conceptMaintenance(page, theme) {
    await openConcept(page, 'system', theme);
    await page.locator('#main .section-nav').getByRole('button', { name: 'Maintenance', exact: true }).click();
    await expect(page.locator('#main .settings-layout > section.surface')).toHaveCount(2);
    await page.mouse.move(0, 0);
    return page.evaluate(measureMaintenance, '#main');
}

async function productionMaintenance(page, theme) {
    await openProduction(page, 'system/maintenance', theme);
    await expect(page.locator('#workspace-main .settings-layout > section.surface')).toHaveCount(2);
    // The Metadata cache row reads this browser's storage once: compare after it answered.
    await expect(page.locator('#workspace-main .operation').filter({ hasText: 'Metadata cache' }).locator('p'))
        .toHaveText(/Nothing is cached in this browser\.$/);
    await page.mouse.move(0, 0);
    return page.evaluate(measureMaintenance, '#workspace-main');
}

for (const theme of THEMES) for (const [width, height] of SIZES) {
    test(`maintenance matches the concept ${theme} ${width}x${height}`, async ({ page }, testInfo) => {
        await page.setViewportSize({ width, height });
        const concept = await conceptMaintenance(page, theme);
        await page.screenshot({ path: testInfo.outputPath(`concept-maintenance-${theme}-${width}.png`), fullPage: true });
        const production = await productionMaintenance(page, theme);
        await page.screenshot({ path: testInfo.outputPath(`production-maintenance-${theme}-${width}.png`), fullPage: true });
        expect(diffInventory(mapMaintenanceConcept(concept), production)).toEqual([]);
        expect(production.pageScroll).toBe(false);
    });
}

// The concept's plan names (`data-plan`) and production's operation, row and button for each.
const PLANS = Object.freeze([
    ['duplicate prompts', 'duplicate_prompts', 'Duplicate prompt text'],
    ['database optimization', 'optimize', 'Optimize database'],
    ['unused prompt components', 'unused_components', 'Unused prompt components'],
    ['missing-file links', 'missing_links', 'Missing-file links'],
]);

async function conceptPlan(page, theme, name) {
    await conceptMaintenance(page, theme);
    await page.locator(`#main [data-plan="${name}"]`).click();
    await expect(page.locator('dialog#modal[open] #modal-title')).toHaveText('Review maintenance plan');
    await page.mouse.move(0, 0);
    return page.evaluate(measureReview);
}

/** Production's plan dialog for the row `title`, with the server's answer its review read. */
async function productionPlan(page, title) {
    const answer = page.waitForResponse(response => response.url().endsWith(PREVIEW_URL));
    await page.locator('#workspace-main .operation').filter({ has: page.locator('h3', { hasText: title }) })
        .getByRole('button', { name: 'Review plan', exact: true }).click();
    const plan = await (await answer).json();
    await expect(page.locator('dialog#modal[open] #modal-body > h3')).toHaveText(title);
    await page.mouse.move(0, 0);
    return { plan, production: await page.evaluate(measureReview) };
}

for (const theme of THEMES) for (const [width, height] of SIZES) {
    test(`plan dialogs match the concept ${theme} ${width}x${height}`, async ({ page, request }, testInfo) => {
        await page.setViewportSize({ width, height });
        // Something to merge, and a missing-file link, so no plan is empty.
        await expect(await request.post('/__test__/system/duplicate-prompts')).toBeOK();
        await expect(await request.post('/__test__/system/missing-file')).toBeOK();
        const concepts = [];
        for (const [name] of PLANS) {
            concepts.push(await conceptPlan(page, theme, name));
            await page.screenshot({ path: testInfo.outputPath(`concept-plan-${name.replace(/\W+/g, '-')}-${theme}-${width}.png`) });
        }
        expect(concepts[0].tree).toEqual(['p.demo-note', 'h3', 'dl.meta-grid', 'p.small', 'div.dialog-actions']);
        await productionMaintenance(page, theme);
        for (const [index, [, operation, title]] of PLANS.entries()) {
            const { plan, production } = await productionPlan(page, title);
            expect(plan.operation).toBe(operation);
            await page.screenshot({ path: testInfo.outputPath(`production-plan-${operation}-${theme}-${width}.png`) });
            expect(diffInventory(mapPlanConcept(concepts[index], title, plan), production)).toEqual([]);
            await page.keyboard.press('Escape');
            await expect(page.locator('dialog#modal[open]')).toHaveCount(0);
        }
    });
}

/**
 * D101: the real group in the concept's markup: the status line (the concept's sample note's place), a group's
 * `p.small` and `.compare-grid` in one wrapper, a "Keep <source>/<path>" radio per copy, the oldest checked, and the
 * production note in the concept's `p.small`.
 */
function mapDuplicatesConcept(reference) {
    const items = reference.compare.items.map((item, index) => ({ ...item,
        label: `Keep output/s7-dupes/group_00_${index ? 'b' : 'a'}.png` }));
    return {
        ...reference,
        tree: ['p.small', 'div.duplicate-groups', 'p.small', 'div.dialog-actions'],
        compare: { ...reference.compare, items },
        note: { ...reference.note, text: 'Removing a copy deletes its file from disk; its database links move to the kept copy. '
            + 'History keeps the removed paths in its run records. Nothing is deleted until you review and apply the cleanup.' },
    };
}

async function conceptDuplicates(page, theme) {
    await conceptMaintenance(page, theme);
    await page.locator('#main').getByRole('button', { name: 'Find duplicates', exact: true }).click();
    await expect(page.locator('dialog#modal[open] #modal-title')).toHaveText('Review duplicate image files');
    await page.mouse.move(0, 0);
    return page.evaluate(measureReview);
}

async function productionDuplicates(page, theme) {
    await productionMaintenance(page, theme);
    await page.locator('#workspace-main').getByRole('button', { name: 'Find duplicates', exact: true }).click();
    await page.locator('#workspace-main').getByRole('button', { name: 'Review groups', exact: true }).click();
    await expect(page.locator('dialog#modal[open] .compare-item img')).toHaveCount(2);
    await page.locator('dialog#modal[open] .compare-item img').evaluateAll(images => Promise.all(images.map(image => image.decode())));
    await page.mouse.move(0, 0);
    return page.evaluate(measureReview);
}

for (const theme of THEMES) for (const [width, height] of SIZES) {
    test(`duplicates dialog matches the concept ${theme} ${width}x${height}`, async ({ page, request }, testInfo) => {
        await page.setViewportSize({ width, height });
        await expect(await request.post('/__test__/system/duplicate-files', { data: { groups: 1, trio: false } })).toBeOK();
        const concept = await conceptDuplicates(page, theme);
        expect(concept.tree).toEqual(['p.demo-note', 'div.compare-grid', 'p.small', 'div.dialog-actions']);
        await page.screenshot({ path: testInfo.outputPath(`concept-duplicates-${theme}-${width}.png`) });
        const production = await productionDuplicates(page, theme);
        await page.screenshot({ path: testInfo.outputPath(`production-duplicates-${theme}-${width}.png`) });
        expect(diffInventory(mapDuplicatesConcept(concept), production)).toEqual([]);
    });
}

test('maintenance comparison detects mutations', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    const concept = await conceptMaintenance(page, 'dark');
    const expected = mapMaintenanceConcept(concept);
    const production = await productionMaintenance(page, 'dark');
    expect(diffInventory(expected, production)).toEqual([]);
    // Unmapped, the section differs only on the D102 row.
    expect(diffInventory(concept, production).map(item => item.path).sort()).toEqual([
        'surfaces.1.rows.3.actions.0.cursor', 'surfaces.1.rows.3.actions.0.opacity', 'surfaces.1.rows.3.description']);
    const measure = () => page.evaluate(measureMaintenance, '#workspace-main');
    const paths = async () => diffInventory(expected, await measure()).map(item => item.path);
    // A removed row, a changed label, a lost danger mark and a swapped token.
    const undoRow = await mutate(page, { remove: ['#workspace-main section.surface:nth-of-type(1) > .operation:nth-of-type(2)'] });
    expect(await paths()).toEqual(expect.arrayContaining(['surfaces.0.rows.length', 'surfaces.0.tree.length']));
    await undoRow();
    await page.evaluate(() => { document.querySelector('#workspace-main section.surface h3').textContent = 'Integrity checks'; });
    expect(await paths()).toEqual(['surfaces.0.rows.0.title']);
    await page.evaluate(() => { document.querySelector('#workspace-main section.surface h3').textContent = 'Integrity & relationship checks'; });
    await page.evaluate(() => { document.querySelector('#workspace-main button.danger').classList.remove('danger'); });
    expect(await paths()).toEqual(expect.arrayContaining(['surfaces.0.rows.3.actions.0.className']));
    await page.evaluate(() => { document.querySelectorAll('#workspace-main section.surface')[0].querySelectorAll('.row-actions > button')[3].classList.add('danger'); });
    const undoToken = await mutate(page, { css: '#workspace-main .settings-layout { --line: rgb(255, 0, 0); }' });
    expect(await paths()).toEqual(expect.arrayContaining(['operation.borderTopColor']));
    await undoToken();
    expect(diffInventory(expected, await measure())).toEqual([]);

    // The plan dialog: a changed grid term and the duplicates dialog's grid token are noticed.
    await expect(await request.post('/__test__/system/duplicate-prompts')).toBeOK();
    const conceptPlanDialog = await conceptPlan(page, 'dark', 'duplicate prompts');
    await productionMaintenance(page, 'dark');
    const { plan } = await productionPlan(page, 'Duplicate prompt text');
    const expectedPlan = mapPlanConcept(conceptPlanDialog, 'Duplicate prompt text', plan);
    const planPaths = async () => diffInventory(expectedPlan, await page.evaluate(measureReview)).map(item => item.path);
    expect(await planPaths()).toEqual([]);
    await page.evaluate(() => { document.querySelector('dialog#modal[open] dl.meta-grid dt').textContent = 'Impact'; });
    expect(await planPaths()).toEqual(['grid.rows.0.0']);
    await page.evaluate(() => { document.querySelector('dialog#modal[open] dl.meta-grid dt').textContent = 'Scope'; });
    const undoGrid = await mutate(page, { css: 'dialog#modal dl.meta-grid { column-gap: 3px; }' });
    expect(await planPaths()).toEqual(expect.arrayContaining(['grid.columnGap']));
    await undoGrid();
    expect(await planPaths()).toEqual([]);
});

test('duplicates comparison detects mutations', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    await expect(await request.post('/__test__/system/duplicate-files', { data: { groups: 1, trio: false } })).toBeOK();
    const expected = mapDuplicatesConcept(await conceptDuplicates(page, 'dark'));
    await productionDuplicates(page, 'dark');
    const paths = async () => diffInventory(expected, await page.evaluate(measureReview)).map(item => item.path);
    expect(await paths()).toEqual([]);
    const undoToken = await mutate(page, { css: 'dialog#modal .compare-item { background: rgb(255, 0, 0); }' });
    expect(await paths()).toEqual(['compare.item.backgroundColor']);
    await undoToken();
    await page.evaluate(() => { document.querySelector('dialog#modal[open] .dialog-actions button:last-child').classList.add('ca-primary'); });
    expect(await paths()).toEqual(expect.arrayContaining(['actions.buttons.1.className']));
    await page.evaluate(() => { document.querySelector('dialog#modal[open] .dialog-actions button:last-child').classList.remove('ca-primary'); });
    const undoItem = await mutate(page, { remove: ['dialog#modal[open] .compare-item:last-child'] });
    expect(await paths()).toEqual(expect.arrayContaining(['compare.items.length']));
    await undoItem();
    expect(await paths()).toEqual([]);
});
