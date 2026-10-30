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

/** The concept's shell and Overview with the recorded deviations applied, given the server's real overview. */
function mapOverviewConcept(reference, overview) {
    const relabel = { 'Database integrity': 'Not checked', 'Positive prompts': number(overview.library.positive_prompts),
        'Negative variants': number(overview.library.negative_variants), Images: number(overview.images.indexed) };
    const rows = {
        // D93: the real pending count; Review metadata opens Needs review (the concept's go-review).
        '3 ambiguous image links': row => ({ ...row,
            title: `${number(overview.needs_review.pending)} ambiguous image link${overview.needs_review.pending === 1 ? '' : 's'}` }),
        // D93 (S9): what GET /backup/status reports; the fixture has no backup yet.
        'Backup status': row => ({ ...row, description: 'No backup recorded.' }),
        // D93: there is no paging, so the shared settings are grid and density.
        'One display configuration': row => ({ ...row, description: 'Grid and density settings are shared across views.' }),
        // D93: the real sources, not samples.
        '2 sample output sources': row => ({ ...row, title: `${number(overview.sources.effective)} output sources` }),
        // D96: the dialog shows runs by category and library totals, not prompt, tag or date usage (D153: no ratings).
        Statistics: row => ({ ...row, description: 'Runs by category, with library totals.' }),
    };
    const surfaces = reference.columns.surfaces.map(surface => ({ ...surface,
        rows: surface.rows.map(row => (rows[row.title] ?? (value => value))(row)) }));
    // D129: "Library location" after Backup status, in the backup row's own markup; the fixture's database is configured
    // explicitly (config.json), so it does not move and its action is Details.
    const [health] = surfaces;
    const backup = health.rows.find(row => row.title === 'Backup status');
    const location = { ...backup, title: 'Library location',
        description: `${overview.location.path} · The database path set in config.json.`,
        action: { ...backup.action, text: 'Details' } };
    surfaces[0] = { ...health, tree: [...health.tree, 'div.operation'],
        rows: [...health.rows.slice(0, 2), location, ...health.rows.slice(2)] };
    return {
        ...reference,
        // D92: no "Sample configuration" chip.
        heading: { ...reference.heading, actions: [] },
        // D122: the concept's literal label is its prototype action name ("system-tab sections").
        nav: { ...reference.nav, label: 'System sections' },
        metrics: { ...reference.metrics, items: reference.metrics.items.map(item => ({ ...item, value: relabel[item.label] })) },
        columns: { ...reference.columns, surfaces },
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
    // So is the backup row (S9).
    await expect(page.locator('#workspace-main .operation').filter({ has: page.locator('h3', { hasText: 'Backup status' }) })
        .locator('p')).toHaveText('No backup recorded.');
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
// Save; D122 the nav label; D129 the inserted "Library location" row, which shifts the thumbnail row to the fourth place
// (row 2 takes its title, text and action; row 3 and the surface's fifth child are new). (The fixture's first three
// metrics are the concept's own counts.)
const D129_ROW_ACTION = ['backgroundColor', 'borderTopColor', 'borderTopLeftRadius', 'borderTopWidth', 'className', 'color',
    'cursor', 'fontSize', 'opacity', 'paddingLeft', 'paddingTop', 'text'].map(key => `columns.surfaces.0.rows.3.action.${key}`);
const UNMAPPED_PATHS = [
    'columns.surfaces.0.rows.0.title', 'columns.surfaces.0.rows.1.description',
    'columns.surfaces.0.rows.2.action.text', 'columns.surfaces.0.rows.2.description', 'columns.surfaces.0.rows.2.title',
    ...D129_ROW_ACTION, 'columns.surfaces.0.rows.3.description', 'columns.surfaces.0.rows.3.title',
    'columns.surfaces.0.rows.3.tree.0', 'columns.surfaces.0.rows.3.tree.1', 'columns.surfaces.0.rows.3.tree.length',
    'columns.surfaces.0.rows.length', 'columns.surfaces.0.tree.4', 'columns.surfaces.0.tree.length',
    'columns.surfaces.1.rows.0.description', 'columns.surfaces.1.rows.1.title', 'columns.surfaces.1.rows.2.description',
    'heading.actions.0', 'heading.actions.length', 'metrics.items.3.value', 'nav.label',
    'save.button.cursor', 'save.button.opacity', 'save.status.text',
].sort();

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

/**
 * D96: the concept's table, its rows the real ones; then "Library totals" in the same table markup. D153 (#447): the
 * concept's "Avg. rating" column and a "Rated images" total are not drawn.
 */
function mapStatisticsConcept(reference, statistics) {
    const [runs] = reference.tables;
    return {
        ...reference,
        // No sample note (prototype copy); a second table follows the first.
        body: ['table', 'table'],
        tables: [
            { ...runs, head: runs.head.filter(([, , text]) => text !== 'Avg. rating'),
                rows: statistics.by_category.map(row => [row.category ?? 'Uncategorized', number(row.runs)]) },
            { caption: 'Library totals', head: [['th', 'col', 'Total'], ['th', 'col', 'Count']],
                rows: [['Prompts', number(statistics.totals.total_prompts)], ['Tags', number(statistics.totals.total_tags)],
                    ['Images', number(statistics.totals.total_images)]] },
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

/** Runs in the page: Appearance's surface, every row's text and control, and the concept's row geometry and tokens. */
function measureAppearance(mainSelector) {
    const layout = document.querySelector(`${mainSelector} .settings-layout`);
    const css = (node, ...keys) => node ? Object.fromEntries(keys.map(key => [key, getComputedStyle(node)[key]])) : null;
    const name = node => [node.localName, ...[...node.classList].sort()].join('.');
    const text = node => node?.textContent.trim() ?? null;
    const surface = layout.querySelector(':scope > section.surface');
    const control = row => {
        const select = row.querySelector(':scope > select');
        if (select) return { kind: 'select', id: select.id, options: [...select.options].map(option => option.textContent),
            selected: select.selectedOptions[0]?.textContent ?? null };
        const input = row.querySelector(':scope > input');
        if (input) return { kind: `input.${input.type}`, id: input.id, value: input.value };
        const toggle = row.querySelector(':scope > * input[type="checkbox"]');
        if (toggle) return { kind: 'switch', id: toggle.id, wrapper: name(toggle.parentElement), checked: toggle.checked,
            ...css(toggle.parentElement, 'width', 'height') };
        const chip = row.querySelector(':scope > .chip');
        return chip ? { kind: name(chip), text: text(chip), ...css(chip, 'color', 'backgroundColor', 'fontSize') } : null;
    };
    const rows = [...surface.querySelectorAll(':scope > .setting-row')];
    const operations = [...surface.querySelectorAll(':scope > .operation')];
    const firstRow = rows[0];
    return {
        layout: [...layout.children].map(name),
        surface: { tree: [...surface.children].map(name), heading: text(surface.querySelector(':scope > h2')),
            intro: text(surface.querySelector(':scope > p')),
            ...css(surface, 'paddingTop', 'paddingLeft', 'backgroundColor', 'borderTopWidth', 'borderTopColor', 'borderTopLeftRadius') },
        rows: rows.map(row => ({ tree: [...row.children].map(name), text: [...row.firstElementChild.children].map(node => [node.localName, text(node),
            node.localName === 'label' ? node.htmlFor : null]), control: control(row) })),
        operations: operations.map(row => ({ tree: [...row.children].map(name), title: text(row.querySelector('h3')),
            description: text(row.querySelector('p')), action: text(row.querySelector('.row-actions > button')) })),
        row: css(firstRow, 'display', 'alignItems', 'justifyContent', 'columnGap', 'paddingTop', 'paddingBottom', 'borderTopWidth',
            'borderTopColor', 'marginTop'),
        label: css(firstRow.querySelector('label'), 'fontSize', 'fontWeight', 'color'),
        description: css(firstRow.querySelector('p'), 'fontSize', 'color', 'marginTop', 'maxWidth'),
        select: css(firstRow.querySelector('select'), 'fontSize', 'minWidth', 'maxWidth', 'backgroundColor', 'color', 'borderTopColor'),
        heading3: css(surface.querySelector('.setting-row h3'), 'fontSize', 'fontWeight', 'color'),
        number: css(surface.querySelector('.setting-row input[type="number"]'), 'fontSize', 'minWidth', 'maxWidth', 'backgroundColor'),
        pageScroll: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    };
}

// D97: the rows Appearance adds for the legacy gallery settings, after the concept's own, as production renders them.
// D156 (#450) moved "Collapse viewer details by default" to the Viewer surface (workspace-viewer-mode.spec.mjs).
const ADDED_APPEARANCE_ROWS = [
    ['card-details', 'Show image details on cards', 'Show each card’s title and file details under the preview.', true],
];

/** The concept's Appearance with the recorded deviations applied, given the server's real settings. */
function mapAppearanceConcept(reference, settings) {
    const toggleRow = reference.rows.find(row => row.control?.kind === 'switch');
    const added = ADDED_APPEARANCE_ROWS.map(([id, label, description, checked]) => ({
        tree: toggleRow.tree, text: [['label', label, id], ['p', description, null]],
        control: { ...toggleRow.control, id, checked } }));
    return {
        ...reference,
        // D156: the Viewer surface, then (#317) the server's thumbnail settings in their own surface ("Thumbnails",
        // README: retained in Appearance).
        layout: [reference.layout[0], 'section.surface', 'section.surface', ...reference.layout.slice(1)],
        // D97: one added toggle (D156 moved the other) and the "Reset display defaults" operation close the surface.
        surface: { ...reference.surface, tree: [...reference.surface.tree, 'div.setting-row', 'div.operation'] },
        rows: [...reference.rows.map(row => row.control?.id === 'timeout'
            // The node search timeout is the server's real `result_timeout`, not the concept's sample 30.
            ? { ...row, control: { ...row.control, value: String(settings.result_timeout) } } : row), ...added],
        operations: [{ tree: ['div', 'div.row-actions'], title: 'Reset display defaults',
            description: 'Restore this browser’s display defaults, then save. Other settings are kept.', action: 'Reset' }],
    };
}

async function conceptAppearance(page, theme) {
    await openConcept(page, 'system', theme);
    await page.locator('#main .section-nav').getByRole('button', { name: 'Appearance', exact: true }).click();
    await expect(page.locator('#main .settings-layout > section.surface > h2')).toHaveText('Browsing & display');
    await page.mouse.move(0, 0);
    return page.evaluate(measureAppearance, '#main');
}

async function productionAppearance(page, theme) {
    await openProduction(page, 'system/appearance', theme);
    await expect(page.locator('#workspace-main #timeout')).toBeEnabled();
    await page.mouse.move(0, 0);
    return page.evaluate(measureAppearance, '#workspace-main');
}

for (const theme of THEMES) for (const [width, height] of SIZES) {
    test(`appearance matches the concept ${theme} ${width}x${height}`, async ({ page, request }, testInfo) => {
        await page.setViewportSize({ width, height });
        const { settings } = await serverJson(request, '/prompt_manager/settings');
        const concept = await conceptAppearance(page, theme);
        await page.screenshot({ path: testInfo.outputPath(`concept-appearance-${theme}-${width}.png`), fullPage: true });
        const production = await productionAppearance(page, theme);
        await page.screenshot({ path: testInfo.outputPath(`production-appearance-${theme}-${width}.png`), fullPage: true });
        expect(diffInventory(mapAppearanceConcept(concept, settings), production)).toEqual([]);
        expect(production.pageScroll).toBe(false);
    });
}

test('appearance comparison detects mutations', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    const { settings } = await serverJson(request, '/prompt_manager/settings');
    const concept = await conceptAppearance(page, 'dark');
    const expected = mapAppearanceConcept(concept, settings);
    const production = await productionAppearance(page, 'dark');
    expect(diffInventory(expected, production)).toEqual([]);
    // Unmapped, the concept differs only where D97 and the real timeout are recorded.
    expect(diffInventory(concept, production).map(item => item.path.split('.').slice(0, 2).join('.'))
        .filter((path, index, all) => all.indexOf(path) === index).sort())
        .toEqual(['layout.1', 'layout.2', 'layout.3', 'layout.length', 'operations.0', 'operations.length', 'rows.11', 'rows.12',
            'rows.length', 'surface.tree'].sort());
    const measure = () => page.evaluate(measureAppearance, '#workspace-main');
    const paths = async () => diffInventory(expected, await measure()).map(item => item.path);
    // A removed row.
    const undoRow = await mutate(page, { remove: ['#workspace-main .setting-row:nth-of-type(3)'] });
    expect(await paths()).toEqual(expect.arrayContaining(['rows.length', 'surface.tree.length']));
    await undoRow();
    // A changed label and a changed option.
    await page.evaluate(() => { document.querySelector('#workspace-main label[for="density"]').textContent = 'Thumbnail sizes'; });
    expect(await paths()).toEqual(['rows.1.text.0.1']);
    await page.evaluate(() => { document.querySelector('#workspace-main label[for="density"]').textContent = 'Thumbnail size'; });
    await page.evaluate(() => { document.querySelector('#workspace-main #image-quality option').textContent = 'Medium'; });
    expect(await paths()).toEqual(expect.arrayContaining(['rows.5.control.options.0']));
    await page.evaluate(() => { document.querySelector('#workspace-main #image-quality option').textContent = 'Optimized'; });
    // A swapped token.
    const undoToken = await mutate(page, { css: '#workspace-main .settings-layout { --line: rgb(255, 0, 0); }' });
    expect(await paths()).toEqual(expect.arrayContaining(['row.borderTopColor']));
    await undoToken();
    expect(diffInventory(expected, await measure())).toEqual([]);
});

/**
 * Runs in the page: Sources & scans (app.js 679–681): the optional notice, then the "Image sources" surface with its
 * panel heading, the source list, the two setting rows and the operations, with the concept's geometry and tokens.
 */
function measureSources(mainSelector) {
    const layout = document.querySelector(`${mainSelector} .settings-layout`);
    const css = (node, ...keys) => node ? Object.fromEntries(keys.map(key => [key, getComputedStyle(node)[key]])) : null;
    const name = node => [node.localName, ...[...node.classList].sort()].join('.');
    const text = node => node?.textContent.trim() ?? null;
    const button = node => ({ text: text(node), className: node.className,
        ...css(node, 'backgroundColor', 'color', 'fontSize', 'paddingTop', 'paddingLeft', 'borderTopColor') });
    const operation = row => ({ tree: [...row.children].map(name), title: text(row.querySelector('h3')),
        description: text(row.querySelector('p')), actions: [...row.querySelectorAll('.row-actions > button')].map(button) });
    const notice = layout.querySelector(':scope > .notice');
    const surface = layout.querySelector(':scope > section.surface');
    const heading = surface.querySelector(':scope > .panel-heading');
    const list = surface.querySelector(':scope > #source-list');
    const control = row => {
        const toggle = row.querySelector(':scope > * input[type="checkbox"]');
        if (toggle) return { kind: 'switch', id: toggle.id, wrapper: name(toggle.parentElement), checked: toggle.checked,
            ...css(toggle.parentElement, 'width', 'height') };
        const input = row.querySelector(':scope > input');
        return { kind: `input.${input.type}`, id: input.id, min: input.min, max: input.max, value: input.value,
            ...css(input, 'width', 'accentColor') };
    };
    const rows = [...surface.querySelectorAll(':scope > .setting-row')];
    return {
        layout: [...layout.children].map(name),
        notice: notice && { tree: [...notice.children].map(name),
            text: [...notice.querySelectorAll(':scope > div > *')].map(node => [node.localName, text(node)]),
            actions: [...notice.querySelectorAll(':scope > .row-actions > button')].map(button),
            ...css(notice, 'display', 'alignItems', 'columnGap', 'borderTopColor', 'backgroundColor', 'paddingTop', 'marginBottom') },
        surface: { tree: [...surface.children].map(name), intro: text(surface.querySelector(':scope > p')),
            ...css(surface, 'paddingTop', 'paddingLeft', 'backgroundColor', 'borderTopColor', 'borderTopLeftRadius') },
        heading: { tree: [...heading.children].map(name), title: text(heading.querySelector('h2')),
            action: button(heading.querySelector('button')), ...css(heading, 'display', 'alignItems', 'justifyContent', 'marginBottom'),
            h2: css(heading.querySelector('h2'), 'fontSize', 'fontWeight', 'color') },
        sources: [...list.children].map(operation),
        rows: rows.map(row => ({ tree: [...row.children].map(name), text: [...row.firstElementChild.children].map(node => [node.localName, text(node),
            node.localName === 'label' ? node.htmlFor : null]), control: control(row) })),
        operations: [...surface.querySelectorAll(':scope > .operation')].map(operation),
        operation: css(list.querySelector('.operation'), 'display', 'alignItems', 'justifyContent', 'columnGap', 'paddingTop',
            'paddingBottom', 'borderTopWidth', 'borderTopColor'),
        h3: css(list.querySelector('.operation h3'), 'fontSize', 'fontWeight', 'color'),
        p: css(list.querySelector('.operation p'), 'fontSize', 'color', 'marginTop'),
        row: css(rows[0], 'display', 'alignItems', 'justifyContent', 'columnGap', 'paddingTop', 'paddingBottom', 'borderTopColor'),
        pageScroll: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    };
}

// D128: "watching until restart" after monitoring was switched off while the observer still runs.
const WATCH_TEXT = { watching: 'watching', stopping: 'watching until restart', manual: 'manual scan', restart_pending: 'restart pending' };
const WATCHED = new Set(['watching', 'stopping']);
// D98: the operation Sources adds after the concept's own, as production renders it.
const REFRESH_FILES = { title: 'Refresh files', description: 'Read every source again to find files added or removed outside ComfyUI.',
    label: 'Refresh' };

// D158 (#247): the row production adds directly below "Watch for new outputs", in that row's markup.
const FOREIGN_RUNS = { id: 'foreign-runs', label: 'Save prompts from other workflows',
    description: 'New outputs of workflows without a PromptManager node add their prompt to the library when it is certain.' };

/** D158: the concept's "Watch for new outputs" row as the inserted "Save prompts from other workflows" row. */
function foreignRunsRow(monitoringRow, settings) {
    return { ...monitoringRow, text: [['label', FOREIGN_RUNS.label, FOREIGN_RUNS.id], ['p', FOREIGN_RUNS.description, null]],
        control: { ...monitoringRow.control, id: FOREIGN_RUNS.id, checked: settings.save_foreign_runs } };
}

/** The concept's Sources with the recorded deviations applied, given the server's real settings and sources. */
function mapSourcesConcept(reference, { settings, sources }) {
    const [sample] = reference.sources;
    const scan = sample.actions[0];
    const rowAt = reference.surface.tree.indexOf('div.setting-row');
    return {
        ...reference,
        // The real sources replace the two samples (without the "sample path" note). D98: each configured one has Remove,
        // a plain button after its scan.
        sources: sources.effective.map(source => ({ ...sample, title: source.label,
            description: `${source.path} · ${WATCH_TEXT[source.watch]}`,
            actions: [{ ...scan, text: WATCHED.has(source.watch) ? 'Scan changes' : 'Scan folder' }, { ...scan, text: 'Remove' }] })),
        rows: reference.rows.flatMap(row => {
            if (row.control.id === 'monitoring') return [{ ...row, control: { ...row.control, checked: settings.monitoring_enabled } },
                foreignRunsRow(row, settings)];
            // D113: the range ends at the server's core count (the concept's fixed 16) and shows the real worker count.
            return [{ ...row, control: { ...row.control, max: String(settings.cpu_count), value: String(settings.worker_threads) } }];
        }),
        // D158: the inserted row after the first setting row; D98: "Refresh files" closes the surface.
        surface: { ...reference.surface, tree: [...reference.surface.tree.slice(0, rowAt + 1), 'div.setting-row',
            ...reference.surface.tree.slice(rowAt + 1), 'div.operation'] },
        operations: [...reference.operations, { ...reference.operations[0], title: REFRESH_FILES.title,
            description: REFRESH_FILES.description, actions: [{ ...reference.operations[0].actions[0], text: REFRESH_FILES.label }] }],
    };
}

async function conceptSources(page, theme, { notice }) {
    await openConcept(page, 'system', theme);
    await page.locator('#main .section-nav').getByRole('button', { name: 'Sources & scans', exact: true }).click();
    await expect(page.locator('#main .panel-heading > h2')).toHaveText('Image sources');
    if (!notice) await page.locator('#main .notice').getByRole('button', { name: 'Later', exact: true }).click();
    await expect(page.locator('#main .settings-layout > .notice')).toHaveCount(notice ? 1 : 0);
    await page.mouse.move(0, 0);
    return page.evaluate(measureSources, '#main');
}

async function productionSources(page, theme, { notice }) {
    // about:blank has no storage; the workspace page reads the dismissal.
    if (!notice) await page.addInitScript(() => { if (location.protocol.startsWith('http')) localStorage.setItem('pm_update_notice_dismissed', '3.2.0'); });
    await openProduction(page, 'system/sources', theme);
    await expect(page.locator('#workspace-main #source-list .operation')).toHaveCount(2);
    await expect(page.locator('#workspace-main #workers')).toBeEnabled();
    await expect(page.locator('#workspace-main .settings-layout > .notice')).toHaveCount(notice ? 1 : 0);
    await page.mouse.move(0, 0);
    return page.evaluate(measureSources, '#workspace-main');
}

/** The fixture's sources as the concept shows its samples: the first one watched, the second not. */
async function watchedSources(request) {
    await expect(await request.post('/__test__/system/monitor', { data: { watching: ['output'] } })).toBeOK();
    return serverJson(request, '/prompt_manager/settings');
}

for (const theme of THEMES) for (const [width, height] of SIZES) for (const notice of [true, false]) {
    const variant = notice ? 'with' : 'without';
    test.describe(() => {
    test.use({ olderImagesNotice: notice });
    test(`sources matches the concept ${variant} the notice ${theme} ${width}x${height}`, async ({ page, request }, testInfo) => {
        await page.setViewportSize({ width, height });
        const server = await watchedSources(request);
        const concept = await conceptSources(page, theme, { notice });
        await page.screenshot({ path: testInfo.outputPath(`concept-sources-${variant}-notice-${theme}-${width}.png`), fullPage: true });
        const production = await productionSources(page, theme, { notice });
        await page.screenshot({ path: testInfo.outputPath(`production-sources-${variant}-notice-${theme}-${width}.png`), fullPage: true });
        expect(diffInventory(mapSourcesConcept(concept, server), production)).toEqual([]);
        expect(production.pageScroll).toBe(false);
    });
    });
}

test.describe(() => {
test.use({ olderImagesNotice: true });
test('sources comparison detects mutations', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    const server = await watchedSources(request);
    const concept = await conceptSources(page, 'dark', { notice: true });
    const expected = mapSourcesConcept(concept, server);
    const production = await productionSources(page, 'dark', { notice: true });
    expect(diffInventory(expected, production)).toEqual([]);
    // Unmapped, the concept differs only in the real sources, the D98 operation, the D158 row (which moves the D113
    // range to the third row) and the D113 range.
    expect(diffInventory(concept, production).map(item => item.path.split('.').slice(0, 2).join('.'))
        .filter((path, index, all) => all.indexOf(path) === index).sort())
        .toEqual(['operations.2', 'operations.length', 'rows.1', 'rows.2', 'rows.length', 'sources.0', 'sources.1',
            'surface.tree'].sort());
    const measure = () => page.evaluate(measureSources, '#workspace-main');
    const paths = async () => diffInventory(expected, await measure()).map(item => item.path);
    // A removed notice and a removed source row.
    const undoNotice = await mutate(page, { remove: ['#workspace-main .settings-layout > .notice'] });
    expect(await paths()).toEqual(expect.arrayContaining(['layout.length', 'notice.tree.length']));
    await undoNotice();
    const undoRow = await mutate(page, { remove: ['#workspace-main #source-list > .operation:nth-child(2)'] });
    expect(await paths()).toEqual(expect.arrayContaining(['sources.length']));
    await undoRow();
    // A changed label and a changed button.
    await page.evaluate(() => { document.querySelector('#workspace-main label[for="monitoring"]').textContent = 'Watch for outputs'; });
    expect(await paths()).toEqual(['rows.0.text.0.1']);
    await page.evaluate(() => { document.querySelector('#workspace-main label[for="monitoring"]').textContent = 'Watch for new outputs'; });
    // D158: the inserted row is compared too: a missing one and a changed label are caught.
    const undoForeign = await mutate(page, { remove: ['#workspace-main .setting-row:has(#foreign-runs)'] });
    expect(await paths()).toEqual(expect.arrayContaining(['rows.length', 'surface.tree.length']));
    await undoForeign();
    await page.evaluate(() => { document.querySelector('#workspace-main label[for="foreign-runs"]').textContent = 'Save other prompts'; });
    expect(await paths()).toEqual(['rows.1.text.0.1']);
    await page.evaluate(() => { document.querySelector('#workspace-main label[for="foreign-runs"]').textContent = 'Save prompts from other workflows'; });
    await page.evaluate(() => { document.querySelector('#workspace-main .panel-heading button').textContent = 'Add source'; });
    expect(await paths()).toEqual(['heading.action.text']);
    await page.evaluate(() => { document.querySelector('#workspace-main .panel-heading button').textContent = '+ Add source'; });
    // A swapped token.
    const undoToken = await mutate(page, { css: '#workspace-main .settings-layout { --line: rgb(255, 0, 0); }' });
    expect(await paths()).toEqual(expect.arrayContaining(['operation.borderTopColor']));
    await undoToken();
    expect(diffInventory(expected, await measure())).toEqual([]);
});
});

/**
 * Runs in the page: the open "Add image source" dialog (the concept's `simpleInput`, app.js 1138 and 1497): its title,
 * the body's tree, the field's label and input, and the actions, with the concept's geometry and tokens.
 */
function measureAddSource() {
    const dialog = document.querySelector('dialog#modal[open]');
    const css = (node, ...keys) => node ? Object.fromEntries(keys.map(key => [key, getComputedStyle(node)[key]])) : null;
    const name = node => [node.localName, ...[...node.classList].sort()].join('.');
    const text = node => node?.textContent.trim() ?? null;
    const body = dialog.querySelector('#modal-body');
    const field = body.querySelector(':scope > .field');
    const label = field?.querySelector(':scope > label');
    const input = field?.querySelector(':scope > input');
    const actions = body.querySelector(':scope > .dialog-actions');
    const rect = dialog.getBoundingClientRect();
    return {
        title: text(dialog.querySelector('#modal-title')),
        tree: [...body.children].map(name),
        field: field && { tree: [...field.children].map(name), ...css(field, 'display', 'flexDirection', 'rowGap', 'marginBottom') },
        label: label && { text: text(label), for: label.htmlFor, ...css(label, 'fontSize', 'fontWeight', 'color') },
        input: input && { id: input.id, type: input.type, value: input.value,
            ...css(input, 'height', 'paddingLeft', 'fontSize', 'color', 'backgroundColor', 'borderTopColor', 'borderTopLeftRadius') },
        actions: { tree: [...actions.children].map(name), ...css(actions, 'display', 'justifyContent', 'columnGap', 'marginTop'),
            buttons: [...actions.children].map(button => ({ text: text(button), className: button.className,
                ...css(button, 'backgroundColor', 'color', 'fontSize', 'paddingTop', 'paddingLeft', 'borderTopColor') })) },
        width: Math.round(rect.width),
    };
}

/** D127: the field is "Folder path", starts empty (no sample folder) and its action is "Add". */
function mapAddSourceConcept(reference) {
    const [cancel, apply] = reference.actions.buttons;
    return { ...reference, label: { ...reference.label, text: 'Folder path' }, input: { ...reference.input, value: '' },
        actions: { ...reference.actions, buttons: [cancel, { ...apply, text: 'Add' }] } };
}

async function conceptAddSource(page, theme) {
    await conceptSources(page, theme, { notice: false });
    await page.locator('#main .panel-heading').getByRole('button', { name: '+ Add source', exact: true }).click();
    await expect(page.locator('dialog#modal[open] #modal-title')).toHaveText('Add image source');
    await page.mouse.move(0, 0);
    return page.evaluate(measureAddSource);
}

async function productionAddSource(page, theme) {
    await productionSources(page, theme, { notice: false });
    await page.locator('#workspace-main .panel-heading').getByRole('button', { name: '+ Add source', exact: true }).click();
    await expect(page.locator('dialog#modal[open] #modal-title')).toHaveText('Add image source');
    await page.mouse.move(0, 0);
    return page.evaluate(measureAddSource);
}

for (const theme of THEMES) for (const [width, height] of SIZES) {
    test(`add source dialog matches the concept ${theme} ${width}x${height}`, async ({ page, request }, testInfo) => {
        await page.setViewportSize({ width, height });
        await watchedSources(request);
        const concept = await conceptAddSource(page, theme);
        await page.screenshot({ path: testInfo.outputPath(`concept-add-source-${theme}-${width}.png`) });
        const production = await productionAddSource(page, theme);
        await page.screenshot({ path: testInfo.outputPath(`production-add-source-${theme}-${width}.png`) });
        expect(diffInventory(mapAddSourceConcept(concept), production)).toEqual([]);
    });
}

test('add source comparison detects mutations', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    await watchedSources(request);
    const concept = await conceptAddSource(page, 'dark');
    const expected = mapAddSourceConcept(concept);
    const production = await productionAddSource(page, 'dark');
    expect(diffInventory(expected, production)).toEqual([]);
    // Unmapped, the concept differs only where D127 is recorded.
    expect(diffInventory(concept, production).map(item => item.path).sort())
        .toEqual(['actions.buttons.1.text', 'input.value', 'label.text']);
    const paths = async () => diffInventory(expected, await page.evaluate(measureAddSource)).map(item => item.path);
    // A changed label, a swapped action order and a changed style.
    await page.evaluate(() => { document.querySelector('dialog#modal[open] label[for="action-input"]').textContent = 'Folder'; });
    expect(await paths()).toEqual(['label.text']);
    await page.evaluate(() => { document.querySelector('dialog#modal[open] label[for="action-input"]').textContent = 'Folder path'; });
    await page.evaluate(() => { const actions = document.querySelector('dialog#modal[open] .dialog-actions'); actions.prepend(actions.lastElementChild); });
    expect(await paths()).toEqual(expect.arrayContaining(['actions.buttons.0.text', 'actions.buttons.1.text']));
    await page.evaluate(() => { const actions = document.querySelector('dialog#modal[open] .dialog-actions'); actions.prepend(actions.lastElementChild); });
    const undoStyle = await mutate(page, { css: 'dialog#modal #action-input { border-color: rgb(255, 0, 0); } dialog#modal .dialog-actions { gap: 31px; }' });
    expect(await paths()).toEqual(expect.arrayContaining(['input.borderTopColor', 'actions.columnGap']));
    await undoStyle();
    expect(diffInventory(expected, await page.evaluate(measureAddSource))).toEqual([]);
});

/**
 * Runs in the page: the open "Thumbnail maintenance" dialog (the concept's `thumbnail-review`, app.js 1433–1437): its
 * title, the body's tree, each `.field` (label, control, options, selection, limits), the `p.small` line and the
 * actions, with the concept's geometry and tokens. The concept has no `p.small`, so a probe one stands in for its style.
 */
function measureThumbnailReview() {
    const dialog = document.querySelector('dialog#modal[open]');
    const css = (node, ...keys) => node ? Object.fromEntries(keys.map(key => [key, getComputedStyle(node)[key]])) : null;
    const name = node => [node.localName, ...[...node.classList].sort()].join('.');
    const text = node => node?.textContent.trim() ?? null;
    const body = dialog.querySelector('#modal-body');
    const fields = [...body.querySelectorAll(':scope > .field')].map(field => {
        const label = field.querySelector(':scope > label');
        const control = field.querySelector(':scope > select, :scope > input');
        return {
            tree: [...field.children].map(name),
            ...css(field, 'display', 'flexDirection', 'rowGap', 'marginBottom'),
            label: { text: text(label), for: label.htmlFor, ...css(label, 'fontSize', 'fontWeight', 'color') },
            control: { id: control.id, tag: control.localName, type: control.type,
                options: control.options ? [...control.options].map(option => option.textContent) : null,
                selected: control.options ? control.selectedIndex : null,
                value: control.localName === 'input' ? control.value : null,
                min: control.getAttribute('min'), max: control.getAttribute('max'),
                ...css(control, 'height', 'paddingLeft', 'fontSize', 'color', 'backgroundColor', 'borderTopColor', 'borderTopLeftRadius') },
        };
    });
    let line = body.querySelector(':scope > p.small');
    const probe = !line;
    if (probe) { line = document.createElement('p'); line.className = 'small'; body.append(line); }
    const plan = { text: probe ? null : text(line), role: probe ? null : line.getAttribute('role'),
        ...css(line, 'fontSize', 'color', 'marginTop', 'marginBottom') };
    if (probe) line.remove();
    const actions = body.querySelector(':scope > .dialog-actions');
    const rect = dialog.getBoundingClientRect();
    return {
        title: text(dialog.querySelector('#modal-title')),
        tree: [...body.children].map(name),
        fields,
        plan,
        actions: { tree: [...actions.children].map(name), ...css(actions, 'display', 'justifyContent', 'columnGap', 'marginTop'),
            buttons: [...actions.children].map(button => ({ text: text(button), className: button.className,
                ...css(button, 'backgroundColor', 'color', 'fontSize', 'paddingTop', 'paddingLeft', 'borderTopColor') })) },
        width: Math.round(rect.width),
    };
}

/**
 * D99: the planned counts line (`p.small[role=status]`) before the actions, from the server's plan for the chosen
 * options, and the primary "Start" (the concept's "Simulate thumbnail job"). D131 (#317): the Quality and Preview size
 * limit fields give way to a second `p.small` naming the server's thumbnail settings, and Scope gains "Convert
 * outdated (removes older formats)".
 */
function mapThumbnailConcept(reference, plan) {
    const [cancel, start] = reference.actions.buttons;
    const [scope] = reference.fields;
    const tree = ['div.field', 'p.small', 'p.small', 'div.dialog-actions'];
    const fields = [{ ...scope, control: { ...scope.control, options: [...scope.control.options, 'Convert outdated (removes older formats)'] } }];
    return { ...reference, tree, fields,
        plan: { ...reference.plan, role: 'status',
            text: `${number(plan.targets)} ${plan.targets === 1 ? 'preview' : 'previews'} to generate · ${number(plan.fresh)} already up to date.` },
        actions: { ...reference.actions, buttons: [cancel, { ...start, text: 'Start' }] } };
}

async function conceptThumbnailReview(page, theme) {
    await conceptSources(page, theme, { notice: false });
    const row = page.locator('#main .operation').filter({ has: page.getByRole('heading', { name: 'Thumbnail generation', exact: true }) });
    await row.getByRole('button', { name: 'Review options', exact: true }).click();
    await expect(page.locator('dialog#modal[open] #modal-title')).toHaveText('Thumbnail maintenance');
    await page.mouse.move(0, 0);
    return page.evaluate(measureThumbnailReview);
}

/** Production's dialog from Sources' Review options or Overview's Review thumbnails, once its plan line answered. */
async function productionThumbnailReview(page, theme, entry) {
    if (entry === 'sources') {
        await productionSources(page, theme, { notice: false });
        const row = page.locator('#workspace-main .operation').filter({ has: page.getByRole('heading', { name: 'Thumbnail generation', exact: true }) });
        await row.getByRole('button', { name: 'Review options', exact: true }).click();
    } else {
        await openProduction(page, 'system/overview', theme);
        await page.locator('#workspace-main').getByRole('button', { name: 'Review thumbnails', exact: true }).click();
    }
    await expect(page.locator('dialog#modal[open] #modal-title')).toHaveText('Thumbnail maintenance');
    await expect(page.locator('dialog#modal[open] #modal-body > p.small').first()).toHaveText(/ already up to date\.$/);
    await page.mouse.move(0, 0);
    return page.evaluate(measureThumbnailReview);
}

const thumbnailPlan = request => serverJson(request, '/prompt_manager/images/thumbnails/plan?mode=missing');

for (const entry of ['sources', 'overview']) for (const theme of THEMES) for (const [width, height] of SIZES) {
    test(`thumbnail dialog matches the concept from ${entry} ${theme} ${width}x${height}`, async ({ page, request }, testInfo) => {
        await page.setViewportSize({ width, height });
        await watchedSources(request);
        const plan = await thumbnailPlan(request);
        const concept = await conceptThumbnailReview(page, theme);
        await page.screenshot({ path: testInfo.outputPath(`concept-thumbnail-review-${theme}-${width}.png`) });
        const production = await productionThumbnailReview(page, theme, entry);
        await page.screenshot({ path: testInfo.outputPath(`production-thumbnail-review-${entry}-${theme}-${width}.png`) });
        expect(diffInventory(mapThumbnailConcept(concept, plan), production)).toEqual([]);
    });
}

test('thumbnail dialog comparison detects mutations', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    await watchedSources(request);
    const plan = await thumbnailPlan(request);
    const concept = await conceptThumbnailReview(page, 'dark');
    const expected = mapThumbnailConcept(concept, plan);
    const production = await productionThumbnailReview(page, 'dark', 'sources');
    expect(diffInventory(expected, production)).toEqual([]);
    // Unmapped, the concept differs only where D99 and D131 are recorded.
    expect(diffInventory(concept, production).map(item => item.path.split('.').slice(0, 2).join('.'))
        .filter((path, index, all) => all.indexOf(path) === index).sort())
        .toEqual(['actions.buttons', 'fields.0', 'fields.1', 'fields.2', 'fields.length', 'plan.role', 'plan.text', 'tree.1', 'tree.2']
            .sort());
    const paths = async () => diffInventory(expected, await page.evaluate(measureThumbnailReview)).map(item => item.path);
    // A changed option label, a swapped option order, a swapped action order and a changed style.
    await page.evaluate(() => { document.querySelector('#thumb-mode option').textContent = 'Missing only'; });
    expect(await paths()).toEqual(['fields.0.control.options.0']);
    await page.evaluate(() => { document.querySelector('#thumb-mode option').textContent = 'Missing thumbnails only'; });
    await page.evaluate(() => { const select = document.querySelector('#thumb-mode'); select.prepend(select.lastElementChild); });
    expect(await paths()).toEqual(expect.arrayContaining(['fields.0.control.options.0', 'fields.0.control.options.1']));
    await page.evaluate(() => { const select = document.querySelector('#thumb-mode'); select.append(select.firstElementChild); select.selectedIndex = 0; });
    await page.evaluate(() => { const actions = document.querySelector('dialog#modal[open] .dialog-actions'); actions.prepend(actions.lastElementChild); });
    expect(await paths()).toEqual(expect.arrayContaining(['actions.buttons.0.text', 'actions.buttons.1.text']));
    await page.evaluate(() => { const actions = document.querySelector('dialog#modal[open] .dialog-actions'); actions.prepend(actions.lastElementChild); });
    const undoStyle = await mutate(page, { css: 'dialog#modal #thumb-mode { border-color: rgb(255, 0, 0); } dialog#modal #modal-body > p.small { color: rgb(255, 0, 0); }' });
    expect(await paths()).toEqual(expect.arrayContaining(['fields.0.control.borderTopColor', 'plan.color']));
    await undoStyle();
    expect(diffInventory(expected, await page.evaluate(measureThumbnailReview))).toEqual([]);
});

/**
 * Runs in the page: one `.notice` (the concept's markup: `div > h2 + p`, then `.row-actions` buttons) with its tokens and
 * geometry, and its left edge against `edgeSelector` (the content it lines up with).
 */
function measureNoticeOf([selector, edgeSelector]) {
    const notice = document.querySelector(selector);
    const css = (node, ...keys) => node ? Object.fromEntries(keys.map(key => [key, getComputedStyle(node)[key]])) : null;
    const name = node => [node.localName, ...[...node.classList].sort()].join('.');
    const text = node => node?.textContent.trim() ?? null;
    const button = node => ({ text: text(node), className: node.className,
        ...css(node, 'backgroundColor', 'color', 'fontSize', 'paddingTop', 'paddingLeft', 'borderTopColor', 'borderTopLeftRadius') });
    const edge = document.querySelector(edgeSelector);
    return {
        tree: [...notice.children].map(name),
        text: [...notice.querySelectorAll(':scope > div:not(.row-actions) > *')].map(node => [node.localName, text(node)]),
        actions: [...notice.querySelectorAll(':scope > .row-actions > button')].map(button),
        frame: css(notice, 'display', 'alignItems', 'justifyContent', 'columnGap', 'borderTopWidth', 'borderTopColor',
            'borderTopLeftRadius', 'backgroundColor', 'paddingTop', 'paddingLeft', 'marginBottom', 'flexDirection'),
        h2: css(notice.querySelector('h2'), 'fontSize', 'fontWeight', 'color'),
        p: css(notice.querySelector('p'), 'fontSize', 'color', 'marginTop'),
        rowActions: css(notice.querySelector(':scope > .row-actions'), 'display', 'columnGap', 'flexShrink'),
        alignedWith: Math.round(notice.getBoundingClientRect().left - edge.getBoundingClientRect().left),
        pageScroll: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    };
}

/** D129: the concept's Sources notice as the library notice: its own copy and actions, no bottom margin (main's padding). */
function mapLibraryNotice(reference) {
    return {
        ...reference,
        text: [['h2', 'Your library is inside the node folder'], ['p', 'Your library is stored inside the node folder, where an '
            + 'update or reinstall can delete it. Move it to ComfyUI\'s user folder: it is copied and verified first, and nothing '
            + 'is deleted.']],
        actions: reference.actions.map((action, index) => ({ ...action, text: ['Keep it here', 'Move library'][index] })),
        frame: { ...reference.frame, marginBottom: '0px' },
    };
}

async function conceptNotice(page, theme) {
    await openConcept(page, 'system', theme);
    await page.locator('#main .section-nav').getByRole('button', { name: 'Sources & scans', exact: true }).click();
    await expect(page.locator('#main .settings-layout > .notice')).toBeVisible();
    await page.mouse.move(0, 0);
    return page.evaluate(measureNoticeOf, ['#main .settings-layout > .notice', '#main .heading']);
}

async function productionNotice(page, theme) {
    await openProduction(page, 'system/overview', theme);
    await expect(page.locator('#library-banner > .notice')).toBeVisible();
    await expect(page.locator('#workspace-main .summary-metrics')).toBeVisible();
    await page.mouse.move(0, 0);
    return page.evaluate(measureNoticeOf, ['#library-banner > .notice', '#workspace-main .heading']);
}

for (const theme of THEMES) for (const [width, height] of SIZES) {
    test(`library notice matches the concept notice ${theme} ${width}x${height}`, async ({ page, request }, testInfo) => {
        await page.setViewportSize({ width, height });
        await expect(await request.post('/__test__/system/library', { data: { state: 'node_folder' } })).toBeOK();
        const concept = await conceptNotice(page, theme);
        const production = await productionNotice(page, theme);
        await page.screenshot({ path: testInfo.outputPath(`production-library-notice-${theme}-${width}.png`), fullPage: true });
        expect(diffInventory(mapLibraryNotice(concept), production)).toEqual([]);
        expect(production.alignedWith).toBe(0);
        expect(production.pageScroll).toBe(false);
    });
}

test('library notice comparison detects mutations', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    await expect(await request.post('/__test__/system/library', { data: { state: 'node_folder' } })).toBeOK();
    const expected = mapLibraryNotice(await conceptNotice(page, 'dark'));
    await productionNotice(page, 'dark');
    const paths = async () => diffInventory(expected,
        await page.evaluate(measureNoticeOf, ['#library-banner > .notice', '#workspace-main .heading'])).map(item => item.path);
    expect(await paths()).toEqual([]);
    const undoToken = await mutate(page, { css: '#library-banner { --soft-blue: rgb(255, 0, 0); }' });
    expect(await paths()).toEqual(['frame.backgroundColor']);
    await undoToken();
    await page.evaluate(() => { document.querySelector('#library-banner .notice h2').textContent = 'Library'; });
    expect(await paths()).toEqual(['text.0.1']);
});
