import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';
import { SIZES, THEMES, seedConcept, openConcept, openProduction, diffInventory, mutate } from './concept-inventory.mjs';

// #249 System S2 Task 6: System's shell and its Overview section (app.js renderSystem 658–706), the Statistics dialog
// ("Usage statistics", a1600–1604) and the health report (a1439–1456) against the immutable concept, on the
// concept-shaped real fixture. Each concept measurement is mapped only by the recorded deviations (D92–D96, D121,
// D122, D123), each commented where it applies; after that the difference must be empty, and a mutation of a
// row, a label or a token must be noticed.
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
    await request.post('/__test__/system/reset');
    expect(page.conceptErrors).toEqual([]);
});

/** Runs in the page; self-contained. System's heading, section nav, Overview body and Save row under `mainSelector`. */
function measureSystem(mainSelector) {
    const main = document.querySelector(mainSelector);
    const css = (node, ...keys) => node ? Object.fromEntries(keys.map(key => [key, getComputedStyle(node)[key]])) : null;
    const name = node => [node.localName, ...[...node.classList].sort()].join('.');
    const box = (node, origin = main) => {
        const rect = node.getBoundingClientRect(), base = origin.getBoundingClientRect();
        return { left: Math.round(rect.left - base.left), width: Math.round(rect.width) };
    };
    // A surface's top: from `.two-col` beside its neighbour, else from the bottom of the surface above it. This pins the
    // concept's `.surface + .surface` 18px offset, which concept.css applies inside `.two-col` too (copied, not ruled).
    const top = node => {
        const rect = node.getBoundingClientRect(), previous = node.previousElementSibling?.getBoundingClientRect();
        if (previous && rect.top >= previous.bottom) return Math.round(rect.top - previous.bottom);
        return Math.round(rect.top - node.parentElement.getBoundingClientRect().top);
    };
    const text = node => node?.textContent.trim() ?? null;
    const button = node => ({ text: text(node), className: node.className,
        ...css(node, 'backgroundColor', 'color', 'fontSize', 'paddingTop', 'paddingLeft', 'borderTopWidth', 'borderTopColor',
            'borderTopLeftRadius', 'opacity', 'cursor') });
    const heading = main.querySelector(':scope > .heading');
    const nav = main.querySelector(':scope > .section-nav');
    const layout = main.querySelector(':scope > .settings-layout');
    const metrics = layout.querySelector(':scope > .summary-metrics');
    const columns = layout.querySelector(':scope > .two-col');
    const save = layout.querySelector(':scope > .save-row');
    const firstMetric = metrics.querySelector('.metric');
    const firstSurface = columns.querySelector('.surface');
    const firstOperation = firstSurface.querySelector('.operation');
    return {
        tree: [...main.children].map(name),
        heading: {
            title: text(heading.querySelector('h1')), description: text(heading.querySelector('p.muted')),
            actions: [...(heading.querySelector('.heading-actions')?.children ?? [])].map(name),
        },
        nav: {
            role: nav.getAttribute('role'), label: nav.getAttribute('aria-label'),
            ...css(nav, 'display', 'flexWrap', 'columnGap', 'rowGap', 'paddingBottom', 'marginBottom', 'borderBottomWidth', 'borderBottomColor'),
            box: box(nav),
            buttons: [...nav.children].map(item => ({ pressed: item.getAttribute('aria-pressed'), ...button(item) })),
        },
        layout: { tree: [...layout.children].map(name), ...css(layout, 'maxWidth'), box: box(layout) },
        metrics: {
            ...css(metrics, 'display', 'gridTemplateColumns', 'columnGap', 'rowGap', 'marginBottom'), box: box(metrics),
            items: [...metrics.children].map(item => ({ tree: [...item.children].map(name), value: text(item.querySelector('strong')),
                label: text(item.querySelector('span')), box: box(item) })),
            metric: css(firstMetric, 'paddingTop', 'paddingLeft', 'borderTopWidth', 'borderTopColor', 'borderTopLeftRadius', 'backgroundColor'),
            strong: css(firstMetric.querySelector('strong'), 'display', 'fontSize', 'fontWeight', 'color'),
            span: css(firstMetric.querySelector('span'), 'fontSize', 'color', 'marginTop'),
        },
        columns: {
            ...css(columns, 'display', 'gridTemplateColumns', 'columnGap', 'rowGap'), box: box(columns),
            surfaces: [...columns.children].map(surface => ({ tree: [...surface.children].map(name), box: box(surface), top: top(surface),
                heading: text(surface.querySelector(':scope > h2')),
                rows: [...surface.querySelectorAll(':scope > .operation')].map(row => ({ tree: [...row.children].map(name),
                    title: text(row.querySelector('h3')), description: text(row.querySelector('p')),
                    action: button(row.querySelector('.row-actions > button')) })) })),
            surface: css(firstSurface, 'paddingTop', 'paddingLeft', 'backgroundColor', 'borderTopWidth', 'borderTopColor', 'borderTopLeftRadius'),
            h2: css(firstSurface.querySelector('h2'), 'fontSize', 'fontWeight', 'color', 'marginBottom'),
            operation: css(firstOperation, 'display', 'alignItems', 'justifyContent', 'columnGap', 'paddingTop', 'paddingBottom',
                'borderTopWidth', 'borderTopColor'),
            h3: css(firstOperation.querySelector('h3'), 'fontSize', 'fontWeight', 'color'),
            p: css(firstOperation.querySelector('p'), 'fontSize', 'color', 'marginTop'),
            actions: css(firstOperation.querySelector('.row-actions'), 'display', 'columnGap', 'flexShrink'),
        },
        save: {
            tree: [...save.children].map(name), ...css(save, 'display', 'alignItems', 'justifyContent', 'columnGap', 'marginTop', 'paddingTop'),
            status: { text: text(save.querySelector('#settings-status')), ...css(save.querySelector('#settings-status'), 'fontSize', 'color') },
            button: button(save.querySelector('button')),
        },
        pageScroll: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    };
}

/** Runs in the page: the open dialog's title, body sequence, tables, operation rows and actions. */
function measureDialog() {
    const dialog = document.querySelector('dialog#modal[open]');
    const css = (node, ...keys) => node ? Object.fromEntries(keys.map(key => [key, getComputedStyle(node)[key]])) : null;
    const name = node => [node.localName, ...[...node.classList].sort()].join('.');
    const body = dialog.querySelector('#modal-body');
    const firstCell = selector => body.querySelector(selector);
    const list = body.querySelector(':scope > .operation-list');
    const actions = body.querySelector(':scope > .dialog-actions');
    return {
        title: dialog.querySelector('#modal-title').textContent,
        body: [...body.children].map(name),
        tables: [...body.querySelectorAll(':scope > table')].map(table => ({
            caption: table.caption?.textContent ?? null,
            head: [...table.tHead.rows[0].cells].map(cell => [cell.localName, cell.scope, cell.textContent]),
            rows: [...table.tBodies[0].rows].map(row => [...row.cells].map(cell => cell.textContent)),
        })),
        tableStyle: firstCell(':scope > table') && {
            table: css(firstCell(':scope > table'), 'width', 'borderCollapse', 'fontSize', 'marginTop'),
            caption: css(firstCell(':scope > table caption'), 'textAlign', 'fontSize', 'fontWeight', 'color', 'paddingBottom'),
            th: css(firstCell(':scope > table thead th'), 'textAlign', 'fontSize', 'fontWeight', 'color', 'paddingTop', 'paddingLeft',
                'borderBottomWidth', 'borderBottomColor'),
            td: css(firstCell(':scope > table tbody td'), 'textAlign', 'fontSize', 'color', 'paddingTop', 'paddingLeft',
                'borderBottomWidth', 'borderBottomColor'),
        },
        operations: list && {
            ...css(list, 'display', 'rowGap', 'marginTop'),
            rows: [...list.children].map(row => ({ tree: [...row.children].map(name), title: row.querySelector('h3').textContent,
                chip: row.querySelector('.chip').textContent })),
            operation: css(list.querySelector('.operation'), 'display', 'alignItems', 'justifyContent', 'paddingTop', 'borderTopWidth'),
            h3: css(list.querySelector('.operation h3'), 'fontSize', 'fontWeight', 'color'),
            chip: css(list.querySelector('.operation .chip'), 'fontSize', 'color', 'backgroundColor', 'borderTopLeftRadius', 'paddingLeft'),
        },
        actions: actions && {
            ...css(actions, 'display', 'justifyContent', 'columnGap', 'marginTop'),
            buttons: [...actions.children].map(item => ({ text: item.textContent, className: item.className,
                ...css(item, 'backgroundColor', 'color', 'fontSize', 'paddingTop', 'paddingLeft') })),
        },
    };
}

/** Runs in the page: a `.notice` (Maintenance's "Check first…") with its copy. */
function measureNotice(mainSelector) {
    const notice = document.querySelector(`${mainSelector} .settings-layout > .notice`);
    const css = (node, ...keys) => Object.fromEntries(keys.map(key => [key, getComputedStyle(node)[key]]));
    const name = node => [node.localName, ...[...node.classList].sort()].join('.');
    const run = notice.querySelector(':scope > button');
    return {
        tree: [...notice.children].map(name), text: [...notice.querySelectorAll(':scope > div > *')].map(node => [node.localName, node.textContent]),
        frame: css(notice, 'display', 'alignItems', 'justifyContent', 'columnGap', 'borderTopWidth', 'borderTopColor', 'backgroundColor',
            'borderTopLeftRadius', 'paddingTop', 'paddingLeft', 'marginBottom'),
        h2: css(notice.querySelector('h2'), 'fontSize', 'fontWeight', 'color'),
        p: css(notice.querySelector('p'), 'fontSize', 'color', 'marginTop'),
        run: { text: run.textContent, className: run.className, ...css(run, 'backgroundColor', 'color', 'fontSize', 'paddingTop', 'paddingLeft') },
    };
}

async function serverJson(request, url) {
    const response = await request.get(url);
    await expect(response).toBeOK();
    return response.json();
}

const number = value => value.toLocaleString('en-US');
const ratingText = value => value === null ? 'Not rated' : `${number(value)} / 5`;

/** The concept's shell and Overview with the recorded deviations applied, given the server's real overview. */
function mapOverviewConcept(reference, overview) {
    const relabel = { 'Database integrity': 'Not checked', 'Positive prompts': number(overview.library.positive_prompts),
        'Negative variants': number(overview.library.negative_variants), Images: number(overview.images.indexed) };
    const rows = {
        // D93: the real pending count; Review metadata opens Needs review (the concept's go-review).
        '3 ambiguous image links': row => ({ ...row,
            title: `${number(overview.needs_review.pending)} ambiguous image link${overview.needs_review.pending === 1 ? '' : 's'}` }),
        // D93: no backup has been recorded (verified backups arrive with S8).
        'Backup status': row => ({ ...row, description: 'No retained backup record.' }),
        // D93: there is no paging, so the shared settings are grid and density.
        'One display configuration': row => ({ ...row, description: 'Grid and density settings are shared across views.' }),
        // D93: the real sources, not samples.
        '2 sample output sources': row => ({ ...row, title: `${number(overview.sources.effective)} output sources` }),
        // D96: the dialog shows runs and ratings by category and library totals, not prompt, tag or date usage.
        Statistics: row => ({ ...row, description: 'Runs and ratings by category, with library totals.' }),
    };
    return {
        ...reference,
        // D92: no "Sample configuration" chip.
        heading: { ...reference.heading, actions: [] },
        // D122: the concept's literal label is its prototype action name ("system-tab sections").
        nav: { ...reference.nav, label: 'System sections' },
        metrics: { ...reference.metrics, items: reference.metrics.items.map(item => ({ ...item, value: relabel[item.label] })) },
        columns: { ...reference.columns, surfaces: reference.columns.surfaces.map(surface => ({ ...surface,
            rows: surface.rows.map(row => (rows[row.title] ?? (value => value))(row)) })) },
        save: { ...reference.save,
            // D92: truthful status copy, and "Save preferences" is aria-disabled (dimmed) while nothing is unsaved.
            status: { ...reference.save.status, text: 'No unsaved changes.' },
            button: { ...reference.save.button, opacity: '0.55', cursor: 'not-allowed' } },
    };
}

async function conceptOverview(page, theme) {
    await openConcept(page, 'system', theme);
    await expect(page.locator('#main .summary-metrics')).toBeVisible();
    await page.mouse.move(0, 0);
    return page.evaluate(measureSystem, '#main');
}

async function productionOverview(page, theme) {
    await openProduction(page, 'system/overview', theme);
    await expect(page.locator('#workspace-main .summary-metrics')).toBeVisible();
    // The thumbnail row is its own read (I2): compare once it answered.
    await expect(page.locator('#workspace-main .operation h3').filter({ hasText: 'thumbnails' })).toHaveText('No missing thumbnails');
    await page.mouse.move(0, 0);
    return page.evaluate(measureSystem, '#workspace-main');
}

for (const theme of THEMES) for (const [width, height] of SIZES) {
    test(`system shell and overview match the concept ${theme} ${width}x${height}`, async ({ page, request }, testInfo) => {
        await page.setViewportSize({ width, height });
        const overview = await serverJson(request, '/prompt_manager/system/overview');
        // The fixture's thumbnails all exist, so the concept's "No missing thumbnails" is also the real title.
        expect((await serverJson(request, '/prompt_manager/images/thumbnails/status')).pending).toBe(0);
        const concept = await conceptOverview(page, theme);
        expect(concept.heading.actions).toEqual(['span.chip']);
        // Paired captures are artifacts only; there are no PNG baselines.
        await page.screenshot({ path: testInfo.outputPath(`concept-system-${theme}-${width}.png`), fullPage: true });
        const production = await productionOverview(page, theme);
        await page.screenshot({ path: testInfo.outputPath(`production-system-${theme}-${width}.png`), fullPage: true });
        expect(diffInventory(mapOverviewConcept(concept, overview), production)).toEqual([]);
        expect(production.pageScroll).toBe(false);
    });
}

// The recorded differences, by path: D93 the review row (its real count), the backup row, the
// preferences row and the sources row, and metric 4; D96 the statistics row; D92 the chip, the status and the dimmed
// Save; D122 the nav label. (The fixture's first three metrics are the concept's own counts.)
const UNMAPPED_PATHS = [
    'columns.surfaces.0.rows.0.title', 'columns.surfaces.0.rows.1.description',
    'columns.surfaces.1.rows.0.description', 'columns.surfaces.1.rows.1.title', 'columns.surfaces.1.rows.2.description',
    'heading.actions.0', 'heading.actions.length', 'metrics.items.3.value', 'nav.label',
    'save.button.cursor', 'save.button.opacity', 'save.status.text',
];

test('system comparison detects mutations', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    const overview = await serverJson(request, '/prompt_manager/system/overview');
    const concept = await conceptOverview(page, 'dark');
    const expected = mapOverviewConcept(concept, overview);
    const production = await productionOverview(page, 'dark');
    expect(diffInventory(expected, production)).toEqual([]);
    // Unmapped, the concept differs exactly where a deviation is recorded, and nowhere else.
    expect(diffInventory(concept, production).map(item => item.path).sort()).toEqual(UNMAPPED_PATHS);
    const measure = () => page.evaluate(measureSystem, '#workspace-main');
    const paths = async () => diffInventory(expected, await measure()).map(item => item.path);

    // A removed row.
    const undoRow = await mutate(page, { remove: ['#workspace-main .two-col > .surface:first-child > .operation:nth-of-type(2)'] });
    expect(await paths()).toEqual(expect.arrayContaining(['columns.surfaces.0.rows.length', 'columns.surfaces.0.tree.length']));
    await undoRow();
    // A changed label.
    await page.evaluate(() => { document.querySelector('#workspace-main .metric span').textContent = 'Positive prompt'; });
    expect(await paths()).toEqual(['metrics.items.0.label']);
    await page.evaluate(() => { document.querySelector('#workspace-main .metric span').textContent = 'Positive prompts'; });
    // A swapped token.
    const undoToken = await mutate(page, { css: '#workspace-main .settings-layout { --line: rgb(255, 0, 0); }' });
    expect(await paths()).toEqual(expect.arrayContaining(['metrics.metric.borderTopColor', 'columns.surface.borderTopColor']));
    await undoToken();
    expect(diffInventory(expected, await measure())).toEqual([]);
});

/** D96: the concept's table, its rows the real ones; then "Library totals" in the same table markup. */
function mapStatisticsConcept(reference, statistics) {
    const [runs] = reference.tables;
    return {
        ...reference,
        // No sample note (prototype copy); a second table follows the first.
        body: ['table', 'table'],
        tables: [
            { ...runs, rows: statistics.by_category.map(row => [row.category ?? 'Uncategorized', number(row.runs), ratingText(row.average_rating)]) },
            { caption: 'Library totals', head: [['th', 'col', 'Total'], ['th', 'col', 'Count']],
                rows: [['Prompts', number(statistics.totals.total_prompts)], ['Tags', number(statistics.totals.total_tags)],
                    ['Images', number(statistics.totals.total_images)], ['Rated images', number(statistics.rated_images)]] },
        ],
    };
}

// The fixture's image sources and each check's real chip on it: nothing is damaged, Needs review does not exist yet (Q6).
const FIXTURE_SOURCES = ['output', 'input'];
const FIXTURE_CHIPS = Object.freeze({ 'Integrity check': 'Passed', 'Foreign keys & pair references': 'Passed',
    'Canonical hashes': 'Passed', 'Pair uniqueness': 'Passed', 'Missing files': 'Passed', 'Ambiguous metadata': 'Not available yet',
    Consistency: 'Passed' });
const chipOf = title => {
    if (!(title in FIXTURE_CHIPS)) throw new Error(`No expected chip for ${title}`);
    return FIXTURE_CHIPS[title];
};

/**
 * D95: the concept's health report from its own five rows, labels verbatim, titled "Health report". Only the D95 rows
 * are inserted where they are recorded (Pair uniqueness after Canonical hashes, Consistency last); Missing files has a
 * row per image source when there are several; each chip is the fixture's expected result.
 */
function mapHealthConcept(reference, sources = FIXTURE_SOURCES) {
    const [first] = reference.operations.rows;
    const added = title => ({ ...first, title, chip: chipOf(title) });
    const rows = reference.operations.rows.flatMap(row => {
        const mapped = row.title === 'Missing files' && sources.length > 1
            ? sources.map(source => ({ ...row, title: `Missing files · ${source}`, chip: chipOf(row.title) }))
            : [{ ...row, chip: chipOf(row.title) }];
        return row.title === 'Canonical hashes' ? [...mapped, added('Pair uniqueness')] : mapped;
    });
    return {
        ...reference,
        title: 'Health report',
        // No sample note (prototype copy); D95 adds the checked line after the list.
        body: ['div.operation-list', 'p.small', 'div.dialog-actions'],
        operations: { ...reference.operations, rows: [...rows, added('Consistency')] },
    };
}

// Unmapped, the health report differs exactly where D95 is recorded: the title, the body sequence (no sample note, the
// checked line), the rows after Canonical hashes (Pair uniqueness inserted, Missing files per source) and the chips of
// the concept's sample findings.
const UNMAPPED_HEALTH_PATHS = [
    'body.0', 'body.1',
    ...[3, 4].flatMap(row => [`operations.rows.${row}.chip`, `operations.rows.${row}.title`]),
    // The concept's five rows end at row 4; rows 5–7 exist only with the D95 rows and the second source.
    ...[5, 6, 7].flatMap(row => ['chip', 'title', 'tree.0', 'tree.1', 'tree.length'].map(key => `operations.rows.${row}.${key}`)),
    'operations.rows.length', 'title',
];

async function conceptDialogs(page, theme, testInfo) {
    await openConcept(page, 'system', theme);
    await page.locator('#main').getByRole('button', { name: 'Explore', exact: true }).click();
    await expect(page.locator('dialog#modal[open] #modal-title')).toHaveText('Usage statistics');
    await page.mouse.move(0, 0);
    const statistics = await page.evaluate(measureDialog);
    await page.screenshot({ path: testInfo.outputPath(`concept-statistics-${theme}.png`) });
    await page.keyboard.press('Escape');
    await page.locator('#main .section-nav').getByRole('button', { name: 'Maintenance', exact: true }).click();
    const notice = await page.evaluate(measureNotice, '#main');
    await page.locator('#main .notice').getByRole('button', { name: 'Run health checks', exact: true }).click();
    await expect(page.locator('dialog#modal[open] #modal-title')).toHaveText('Sample health report');
    await page.mouse.move(0, 0);
    await page.screenshot({ path: testInfo.outputPath(`concept-health-${theme}.png`) });
    return { statistics, notice, health: await page.evaluate(measureDialog) };
}

async function productionDialogs(page, theme, testInfo) {
    await openProduction(page, 'system/overview', theme);
    await page.locator('#workspace-main').getByRole('button', { name: 'Explore', exact: true }).click();
    await expect(page.locator('dialog#modal[open] #modal-title')).toHaveText('Usage statistics');
    await page.mouse.move(0, 0);
    const statistics = await page.evaluate(measureDialog);
    await page.screenshot({ path: testInfo.outputPath(`production-statistics-${theme}.png`) });
    await page.keyboard.press('Escape');
    await page.locator('#workspace-main .section-nav').getByRole('button', { name: 'Maintenance', exact: true }).click();
    await expect(page.locator('#workspace-main .notice')).toBeVisible();
    const notice = await page.evaluate(measureNotice, '#workspace-main');
    await page.locator('#workspace-main .notice').getByRole('button', { name: 'Run health checks', exact: true }).click();
    await expect(page.locator('dialog#modal[open] #modal-title')).toHaveText('Health report');
    await page.mouse.move(0, 0);
    await page.screenshot({ path: testInfo.outputPath(`production-health-${theme}.png`) });
    return { statistics, notice, health: await page.evaluate(measureDialog) };
}

for (const theme of THEMES) test(`statistics and health dialogs match the concept ${theme}`, async ({ page, request }, testInfo) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    const concept = await conceptDialogs(page, theme, testInfo);
    expect(concept.statistics.body).toEqual(['p.demo-note', 'table']);
    expect(concept.health.body).toEqual(['p.demo-note', 'div.operation-list', 'div.dialog-actions']);
    const production = await productionDialogs(page, theme, testInfo);
    const statistics = await serverJson(request, '/prompt_manager/system/statistics');
    const { report } = await serverJson(request, '/prompt_manager/system/health');
    // The fixture the expected chips describe: its two sources, and the concept's checks plus the D95 ones.
    const missing = report.checks.find(check => check.id === 'missing_files');
    expect(missing.sources.map(source => source.name)).toEqual(FIXTURE_SOURCES);
    expect(diffInventory(mapStatisticsConcept(concept.statistics, statistics), production.statistics)).toEqual([]);
    expect(diffInventory(mapHealthConcept(concept.health), production.health)).toEqual([]);
    // D123: Maintenance's notice is the concept's own, delivered ahead of the rest of the section.
    expect(diffInventory(concept.notice, production.notice)).toEqual([]);

    // Unmapped, the dialogs differ only where D95 and D96 are recorded.
    expect(diffInventory(concept.health, production.health).map(item => item.path).sort()).toEqual(UNMAPPED_HEALTH_PATHS);
    expect(diffInventory(concept.statistics, production.statistics).map(item => item.path.split('.')[0])
        .filter((part, index, all) => all.indexOf(part) === index).sort()).toEqual(['body', 'tables']);

    // Mutation check: a changed chip token in the health report is noticed, then undone to an exact match again.
    const undo = await mutate(page, { css: '#modal .operation .chip { background: rgb(255, 0, 0); }' });
    expect(diffInventory(mapHealthConcept(concept.health), await page.evaluate(measureDialog)).map(item => item.path))
        .toEqual(['operations.chip.backgroundColor']);
    await undo();
    expect(diffInventory(mapHealthConcept(concept.health), await page.evaluate(measureDialog))).toEqual([]);
    // A renamed check (database/health.py's label) is noticed against the concept's own label.
    await page.evaluate(() => { document.querySelector('#modal .operation h3').textContent = 'Integrity checks'; });
    expect(diffInventory(mapHealthConcept(concept.health), await page.evaluate(measureDialog)).map(item => item.path))
        .toEqual(['operations.rows.0.title']);
    await page.evaluate(() => { document.querySelector('#modal .operation h3').textContent = 'Integrity check'; });
    // And a removed "Library totals" table and a changed caption in the statistics dialog.
    await page.keyboard.press('Escape');
    await page.locator('#workspace-main .section-nav').getByRole('button', { name: 'Overview', exact: true }).click();
    await page.locator('#workspace-main').getByRole('button', { name: 'Explore', exact: true }).click();
    await expect(page.locator('dialog#modal[open] #modal-title')).toHaveText('Usage statistics');
    const expectedStatistics = mapStatisticsConcept(concept.statistics, statistics);
    const undoTable = await mutate(page, { remove: ['#modal-body > table:nth-of-type(2)'] });
    expect(diffInventory(expectedStatistics, await page.evaluate(measureDialog)).map(item => item.path))
        .toEqual(expect.arrayContaining(['body.length', 'tables.length']));
    await undoTable();
    await page.evaluate(() => { document.querySelector('#modal-body caption').textContent = 'Runs per category'; });
    expect(diffInventory(expectedStatistics, await page.evaluate(measureDialog)).map(item => item.path)).toEqual(['tables.0.caption']);
});
