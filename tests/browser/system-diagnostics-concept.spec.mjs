import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';
import { SIZES, THEMES, seedConcept, openConcept, openProduction, diffInventory, mutate, measureReview, mapPlanConcept }
    from './concept-inventory.mjs';

// #249 System S12 Task 7 (#329): System › Diagnostics & logs (app.js renderSystem 694–695) and its log cleanup review
// (the concept's `maintenancePlan("rotated logs")`, 953–958) against the immutable concept, on the concept-shaped
// fixture with the logger writing into this server's own root and seeded with the concept's sample (four lines, a
// 1.4 MB active log, three rotated copies, console logging off). Each concept measurement is mapped only by the recorded
// deviations (D109, D110, and D100 for the review), each commented where it applies; after that the difference must be
// empty, and a removed term, a renamed button, a reordered option, a primary Refresh, a squeezed select or a swapped
// token must be noticed.
test.use({ locale: 'en-US', timezoneId: 'UTC' });

test.beforeEach(async ({ page, request, browserName }) => {
    await startCoverage(page, browserName);
    await seedConcept(request);
    await expect(await request.post('/__test__/system/reset')).toBeOK();
    await expect(await request.post('/__test__/jobs/restart')).toBeOK();
    await expect(await request.post('/__test__/logs/reset')).toBeOK();
    await expect(await request.post('/__test__/logs/seed', { data: { mode: 'concept' } })).toBeOK();
    page.conceptErrors = [];
    page.on('pageerror', error => page.conceptErrors.push(error.message));
});

test.afterEach(async ({ page, request, browserName }) => {
    await saveCoverage(page, browserName);
    await request.post('/__test__/jobs/restart');
    await request.post('/__test__/logs/reset');
    await request.post('/__test__/system/reset');
    expect(page.conceptErrors).toEqual([]);
});

const REPORT_URL = '/prompt_manager/system/diagnostics';
const PREVIEW_URL = '/prompt_manager/maintenance/preview';
const LINE_FORMAT = /^\d\d:\d\d:\d\d [A-Z]+ +\S/;

/** Runs in the page: Diagnostics & logs under `mainSelector`: every surface's text, controls, geometry and tokens. */
function measureDiagnostics([mainSelector, lineFormat]) {
    const format = new RegExp(lineFormat);
    const layout = document.querySelector(`${mainSelector} .settings-layout`);
    const css = (node, ...keys) => node ? Object.fromEntries(keys.map(key => [key, getComputedStyle(node)[key]])) : null;
    const tokens = (node, ...names) => Object.fromEntries(names.map(key => [key, getComputedStyle(node).getPropertyValue(key).trim()]));
    const name = node => [node.localName, ...[...node.classList].sort()].join('.');
    const text = node => node?.textContent.trim() ?? null;
    const button = node => ({ text: text(node), className: node.className });
    const control = row => {
        const select = row.querySelector(':scope > select');
        if (select) return { kind: 'select', id: select.id, options: [...select.options].map(option => option.textContent),
            selected: select.selectedOptions[0]?.textContent ?? null, disabled: select.disabled };
        const toggle = row.querySelector(':scope > * input[type="checkbox"]');
        return toggle ? { kind: 'switch', id: toggle.id, wrapper: name(toggle.parentElement), checked: toggle.checked,
            disabled: toggle.disabled } : null;
    };
    const operations = parent => [...parent.querySelectorAll(':scope > .operation')].map(row => ({ tree: [...row.children].map(name),
        title: text(row.querySelector('h3')), description: text(row.querySelector('p')),
        actions: [...row.querySelectorAll('.row-actions > button')].map(button) }));
    const toolbarItem = node => ({ name: name(node), text: node.localName === 'select' || node.localName === 'input' ? null : text(node),
        for: node.localName === 'label' ? node.htmlFor : null, id: node.id || null,
        options: node.localName === 'select' ? [...node.options].map(option => option.textContent) : null,
        input: node.localName === 'input' ? [node.type, node.min, node.max, node.value] : null,
        className: node.localName === 'button' ? node.className : null });
    const surfaces = [...layout.querySelectorAll(':scope > section.surface')];
    const pre = layout.querySelector('pre');
    const lines = (pre?.textContent ?? '').split('\n').filter(Boolean);
    const details = layout.querySelector('details');
    const firstRow = layout.querySelector('.setting-row');
    const toolbar = layout.querySelector('.toolbar');
    return {
        layout: [...layout.children].map(name),
        surfaces: surfaces.map(surface => {
            const heading = surface.querySelector(':scope > .panel-heading');
            const grid = surface.querySelector(':scope > .meta-grid');
            return {
                tree: [...surface.children].map(name),
                heading: text(surface.querySelector(':scope > h2, :scope > .panel-heading > h2')),
                panelHeading: heading && [...heading.children].map(node => [name(node), text(node)]),
                grid: grid && [...grid.children].map(entry => [name(entry), text(entry.querySelector('dt')), text(entry.querySelector('dd'))]),
                buttons: [...surface.querySelectorAll(':scope > button')].map(button),
                toolbar: surface.querySelector(':scope > .toolbar') && [...surface.querySelector(':scope > .toolbar').children].map(toolbarItem),
                pre: surface.querySelector(':scope > pre') && { tabIndex: pre.tabIndex, tree: [...pre.children].map(name), lines,
                    format: lines.every(line => format.test(line)) },
                notes: [...surface.querySelectorAll(':scope > p')].map(node => [name(node), text(node), node.getAttribute('role'),
                    node.getAttribute('aria-label')]),
                rows: [...surface.querySelectorAll(':scope > .setting-row')].map(row => ({ tree: [...row.children].map(name),
                    text: [...row.firstElementChild.children].map(node => [name(node), text(node), node.localName === 'label' ? node.htmlFor : null]),
                    control: control(row) })),
                details: surface.querySelector(':scope > details') && {
                    summary: text(details.querySelector(':scope > summary')), tree: [...details.children].map(name),
                    notes: [...details.querySelectorAll(':scope > p')].map(node => [name(node), text(node)]),
                    buttons: [...details.querySelectorAll(':scope > button')].map(button),
                    files: [...details.querySelectorAll(':scope > .operation-list > .operation')].map(row => ({
                        title: text(row.querySelector('h3')), detail: text(row.querySelector('p')), actions: [...row.querySelectorAll('.row-actions > button')].map(item =>
                            [text(item), item.getAttribute('aria-label')]) })),
                    operations: operations(details) },
                operations: operations(surface),
            };
        }),
        tokens: tokens(surfaces[0], '--line', '--panel', '--accent'),
        surface: css(surfaces[1], 'paddingTop', 'paddingLeft', 'backgroundColor', 'borderTopWidth', 'borderTopColor', 'borderTopLeftRadius',
            'marginTop'),
        grid: css(layout.querySelector('.meta-grid'), 'display', 'gridTemplateColumns', 'columnGap', 'rowGap'),
        toolbar: css(toolbar, 'display', 'flexWrap', 'alignItems', 'columnGap', 'rowGap', 'marginBottom'),
        pre: css(pre, 'fontFamily', 'fontSize', 'backgroundColor', 'borderTopColor', 'borderTopLeftRadius', 'paddingTop', 'overflowX'),
        row: css(firstRow, 'display', 'alignItems', 'justifyContent', 'columnGap', 'paddingTop', 'paddingBottom', 'borderTopWidth',
            'borderTopColor'),
        label: css(firstRow.querySelector('label'), 'fontSize', 'fontWeight', 'color'),
        description: css(firstRow.querySelector('p'), 'fontSize', 'color', 'marginTop'),
        select: css(firstRow.querySelector('select'), 'fontSize', 'minWidth', 'maxWidth', 'backgroundColor', 'color', 'borderTopColor'),
        summary: css(details.querySelector('summary'), 'fontSize', 'fontWeight', 'color', 'cursor'),
        small: css(details.querySelector(':scope > p.small'), 'fontSize', 'color'),
        // Whether each select is as wide as the browser lays out a copy holding only its chosen option, i.e. it shows
        // that option whole (the browser's own select sizing, arrow included).
        selectsFit: [...layout.querySelectorAll('select')].map(select => {
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

const plural = (count, one, many = `${one}s`) => `${count.toLocaleString('en-US')} ${count === 1 ? one : many}`;
/** The server's size label (1 KB = 1024 bytes), as the section prints it. */
function sizeText(bytes) {
    if (bytes < 1024) return plural(bytes, 'byte');
    if (bytes < 2 ** 20) return `${(bytes / 2 ** 10).toFixed(1)} KB`;
    if (bytes < 2 ** 30) return `${(bytes / 2 ** 20).toFixed(1)} MB`;
    return `${(bytes / 2 ** 30).toFixed(1)} GB`;
}

const KINDS = Object.freeze({ active: 'Active log', rotated: 'Rotated copy', other: 'Other file' });
const RENDERER_NONE = 'No ComfyUI window is open in this browser, so its renderer is not known.';
const COPY = Object.freeze({
    testLink: 'Write a placeholder image record to check that linking works.',
    runtimeLevel: 'How much the server writes to the log. Applied until the server restarts.',
    console: 'Print Prompt Manager’s warnings and errors in the ComfyUI console (everything at DEBUG). Applied until the server restarts.',
    clear: 'Empty the active log and delete its rotated copies.',
    retention: 'Rotates at 10 MB · keeps 5 rotated copies · file logging on · memory buffer 1,000 entries',
});

/**
 * The concept's Diagnostics & logs with the recorded deviations applied, for the server's `report` and the seeded
 * `files` (`/logs/files`). The concept's own sample values the fixture reproduces stay as they are: the four log lines,
 * the Viewer.js line, "prompt_manager.log · 1.4 MB · rotated copies: 3", INFO and console logging off.
 */
function mapDiagnosticsConcept(reference, { report, files, stats }) {
    const [diagnostics, logs] = reference.surfaces;
    const [levelLabel, level, refresh, download] = logs.toolbar;
    const [runtime, consoleRow] = logs.rows;
    const database = report.checks.database;
    const { configured, watching, unavailable } = report.sources;
    const sources = configured === 0 ? `ComfyUI output folder · ${watching ? 'watching' : 'manual scan'}`
        : unavailable > 0 ? `${plural(configured, 'source')} · ${unavailable} not available`
            : `${plural(configured, 'source')} · ${watching} watching`;
    const errors = (stats.level_counts.ERROR ?? 0) + (stats.level_counts.CRITICAL ?? 0);
    return {
        ...reference,
        surfaces: [
            { ...diagnostics,
                // D110: the Test image link operation follows the concept's Image-link diagnostics.
                tree: [...diagnostics.tree, 'div.operation'],
                // D110: live values. Database and Sources from `GET /system/diagnostics`, Host renderers is D106's
                // renderer line (no ComfyUI window in this browser); the pinned Viewer.js line is the concept's own.
                grid: diagnostics.grid.map(([entry, term, value]) => [entry, term, {
                    Database: `Readable · ${sizeText(report.database_info.database_size_bytes)} · ${plural(database.image_count, 'image')}`,
                    Sources: sources,
                    'Host renderers': RENDERER_NONE }[term] ?? value]),
                // D110: the buttons drop the concept's "sample".
                buttons: diagnostics.buttons.map(item => ({ ...item, text: item.text.replace('Download sample diagnostic report',
                    'Download diagnostic report') })),
                operations: [
                    // D110: "Inspect" opens the read-only Inspect a file page.
                    ...diagnostics.operations.map(row => ({ ...row, actions: row.actions.map(item => ({ ...item, text: 'Inspect' })) })),
                    // D110: the legacy synthetic link (S12-11), enabled: the fixture has a most recent prompt.
                    { ...diagnostics.operations[0], title: 'Test image link', description: COPY.testLink,
                        actions: [{ ...diagnostics.operations[0].actions[0], text: 'Create test link…' }] },
                ] },
            { ...logs,
                // D109: the statistics line follows the viewer.
                tree: [logs.tree[0], logs.tree[1], logs.tree[2], 'p.small', ...logs.tree.slice(3)],
                toolbar: [
                    levelLabel,
                    // D109: all six minimum levels (the concept has three).
                    { ...level, options: ['All levels', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] },
                    // D109: the added Lines control, 1–500, default 100.
                    { name: 'label.small', text: 'Lines', for: 'log-limit', id: null, options: null, input: null, className: null },
                    { name: 'input', text: null, for: null, id: 'log-limit', options: null, input: ['number', '1', '500', '100'],
                        className: null },
                    refresh,
                    // D109: the active log, not a sample.
                    { ...download, text: 'Download log' },
                ],
                // D109: "Log statistics" (errors are ERROR + CRITICAL).
                notes: [['p.small', `${plural(stats.buffer_count, 'entry', 'entries')} in memory · ${plural(errors, 'error')} · `
                    + `${plural(stats.level_counts.WARNING ?? 0, 'warning')} · ${plural(stats.log_files_count, 'file')} · `
                    + `${sizeText(stats.total_log_size)} on disk`, 'status', 'Log statistics']],
                rows: [
                    // D109: CRITICAL after the concept's levels; the value lasts until the server restarts.
                    { ...runtime, text: [runtime.text[0], ['p', COPY.runtimeLevel, null]],
                        control: { ...runtime.control, options: [...runtime.control.options, 'CRITICAL'] } },
                    // D109: what the console setting does and that it lasts until the server restarts (off, as the
                    // concept's sample, by the fixture's seed).
                    { ...consoleRow, text: [consoleRow.text[0], ['p', COPY.console, null]] },
                ],
                details: { ...logs.details,
                    // D109: the retention line, the files and Clear all logs follow the concept's line and review.
                    tree: [...logs.details.tree, 'p.small', 'div.operation-list', 'div.operation'],
                    notes: [...logs.details.notes, ['p.small', COPY.retention]],
                    files: files.map(row => ({ title: row.filename, detail: `${sizeText(row.size)} · ${row.modified.slice(0, 16)
                        .replace('T', ' ')} · ${KINDS[row.kind]}`, actions: [['Download', `Download ${row.filename}`]] })),
                    operations: [{ tree: ['div', 'div.row-actions'], title: 'Clear all logs', description: COPY.clear,
                        actions: [{ text: 'Clear all logs…', className: 'danger' }] }] } },
        ],
    };
}

async function conceptDiagnostics(page, theme) {
    await openConcept(page, 'system', theme);
    await page.locator('#main .section-nav').getByRole('button', { name: 'Diagnostics & logs', exact: true }).click();
    await expect(page.locator('#main .settings-layout > section.surface')).toHaveCount(2);
    await page.mouse.move(0, 0);
    return page.evaluate(measureDiagnostics, ['#main', LINE_FORMAT.source]);
}

/** Production's section once every read answered, with the server's report, files and statistics it shows. */
async function productionDiagnostics(page, request, theme) {
    await openProduction(page, 'system/diagnostics', theme);
    const main = page.locator('#workspace-main');
    await expect(main.locator('.settings-layout > section.surface')).toHaveCount(2);
    await expect(main.locator('.meta-grid dd')).not.toContainText(['Loading…']);
    await expect(main.locator('.meta-grid dd').last()).toHaveText(RENDERER_NONE);
    await expect(main.locator('#log-lines')).not.toHaveText('Loading…');
    await expect(main.locator('#log-stats')).not.toHaveText('Loading…');
    await expect(main.locator('details > p.small')).not.toContainText(['Loading…']);
    await expect(main.locator('#runtime-level')).toBeEnabled();
    await expect(main.locator('.operation').filter({ hasText: 'Create test link…' }).locator('button')).not.toHaveAttribute('aria-disabled', 'true');
    await page.mouse.move(0, 0);
    const production = await page.evaluate(measureDiagnostics, ['#workspace-main', LINE_FORMAT.source]);
    const read = async url => (await request.get(url)).json();
    const report = await read(REPORT_URL);
    const { files } = await read('/prompt_manager/logs/files');
    const { stats } = await read('/prompt_manager/logs/stats');
    return { production, report, files, stats };
}

for (const theme of THEMES) for (const [width, height] of SIZES) {
    test(`diagnostics and logs match the concept ${theme} ${width}x${height}`, async ({ page, request }, testInfo) => {
        await page.setViewportSize({ width, height });
        const concept = await conceptDiagnostics(page, theme);
        await page.screenshot({ path: testInfo.outputPath(`concept-diagnostics-${theme}-${width}.png`), fullPage: true });
        const { production, ...live } = await productionDiagnostics(page, request, theme);
        await page.screenshot({ path: testInfo.outputPath(`production-diagnostics-${theme}-${width}.png`), fullPage: true });
        expect(diffInventory(mapDiagnosticsConcept(concept, live), production)).toEqual([]);
        expect(production.surfaces[1].pre.format).toBe(true);
        expect(production.pageScroll).toBe(false);
    });
}

// Below the concept's smallest size: on a 320px phone the Level and Runtime log level selects still show their whole
// option, without page scroll.
for (const theme of THEMES) test(`diagnostics selects fit at 320 wide ${theme}`, async ({ page, request }) => {
    await page.setViewportSize({ width: 320, height: 700 });
    const { production } = await productionDiagnostics(page, request, theme);
    expect(production.selectsFit).toEqual([true, true]);
    expect(production.pageScroll).toBe(false);
});

/**
 * D100 with D109's rule for this plan: the server's `rotated_logs` plan in the concept's dialog, "Rotated log copies"
 * as its `h3`, the plan's grid values (its Backup row says none is needed), "Listed files (N)", the hold line, no D55
 * backup box (nothing in the library changes) and "Apply reviewed operation".
 */
function mapCleanupConcept(reference, plan) {
    const mapped = mapPlanConcept(reference, 'Rotated log copies', plan);
    return {
        ...mapped,
        // D109: no backup box for a plan that changes no library record.
        tree: mapped.tree.filter(item => item !== 'div.notice'),
        grid: { ...mapped.grid, rows: mapped.grid.rows.map(([term, value]) =>
            [term, term === 'Backup' ? 'Not needed: no library record changes' : value]) },
        backup: null,
        // D109: the plan lists files, not records.
        details: { ...mapped.details, summary: `Listed files (${plan.details.length})` },
    };
}

async function conceptCleanup(page, theme) {
    await conceptDiagnostics(page, theme);
    await page.locator('#main details > summary').click();
    await page.locator('#main [data-plan="rotated logs"]').click();
    await expect(page.locator('dialog#modal[open] #modal-title')).toHaveText('Review maintenance plan');
    await page.mouse.move(0, 0);
    return page.evaluate(measureReview);
}

async function productionCleanup(page, request, theme) {
    await productionDiagnostics(page, request, theme);
    await page.locator('#workspace-main details > summary').click();
    const answer = page.waitForResponse(response => response.url().endsWith(PREVIEW_URL));
    await page.locator('#workspace-main').getByRole('button', { name: 'Review log cleanup', exact: true }).click();
    const plan = await (await answer).json();
    await expect(page.locator('dialog#modal[open] #modal-body > h3')).toHaveText('Rotated log copies');
    await page.mouse.move(0, 0);
    return { plan, production: await page.evaluate(measureReview) };
}

for (const theme of THEMES) for (const [width, height] of SIZES) {
    test(`log cleanup review matches the concept ${theme} ${width}x${height}`, async ({ page, request }, testInfo) => {
        await page.setViewportSize({ width, height });
        const concept = await conceptCleanup(page, theme);
        expect(concept.heading.text).toBe('rotated logs');
        expect(concept.tree).toEqual(['p.demo-note', 'h3', 'dl.meta-grid', 'p.small', 'div.dialog-actions']);
        await page.screenshot({ path: testInfo.outputPath(`concept-log-cleanup-${theme}-${width}.png`) });
        const { plan, production } = await productionCleanup(page, request, theme);
        expect(plan.operation).toBe('rotated_logs');
        await page.screenshot({ path: testInfo.outputPath(`production-log-cleanup-${theme}-${width}.png`) });
        expect(diffInventory(mapCleanupConcept(concept, plan), production)).toEqual([]);
    });
}

// Every path where production differs from the unmapped concept: D110's live values (Viewer.js is the concept's own),
// renamed buttons and added operation, and D109's toolbar, statistics line, runtime rows and log files.
const DEVIATED_PATHS = [
    'surfaces.0.buttons.0', 'surfaces.0.grid.0', 'surfaces.0.grid.1', 'surfaces.0.grid.3', 'surfaces.0.operations.0',
    'surfaces.0.operations.1', 'surfaces.0.operations.length', 'surfaces.0.tree.4', 'surfaces.0.tree.length',
    'surfaces.1.details.files', 'surfaces.1.details.notes', 'surfaces.1.details.operations', 'surfaces.1.details.tree',
    'surfaces.1.notes.0', 'surfaces.1.notes.length', 'surfaces.1.rows.0', 'surfaces.1.rows.1', 'surfaces.1.toolbar.1',
    'surfaces.1.toolbar.2', 'surfaces.1.toolbar.3', 'surfaces.1.toolbar.4', 'surfaces.1.toolbar.5', 'surfaces.1.toolbar.length',
    'surfaces.1.tree.3', 'surfaces.1.tree.5', 'surfaces.1.tree.6', 'surfaces.1.tree.length'].sort();

test('diagnostics comparison detects mutations', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    const concept = await conceptDiagnostics(page, 'dark');
    const { production, ...live } = await productionDiagnostics(page, request, 'dark');
    const expected = mapDiagnosticsConcept(concept, live);
    expect(diffInventory(expected, production)).toEqual([]);
    // Unmapped, the section differs only where D109 and D110 are recorded.
    expect(diffInventory(concept, production).map(item => item.path.split('.').slice(0, 4).join('.'))
        .filter((path, index, all) => all.indexOf(path) === index).sort()).toEqual(DEVIATED_PATHS);
    const measure = () => page.evaluate(measureDiagnostics, ['#workspace-main', LINE_FORMAT.source]);
    const paths = async () => diffInventory(expected, await measure()).map(item => item.path);
    // A removed term.
    const undoTerm = await mutate(page, { remove: ['#workspace-main .meta-grid > div:nth-child(2)'] });
    expect(await paths()).toEqual(expect.arrayContaining(['surfaces.0.grid.length']));
    await undoTerm();
    // A renamed button.
    await page.evaluate(() => { document.querySelector('#workspace-main section.surface > button').textContent = 'Download report'; });
    expect(await paths()).toEqual(['surfaces.0.buttons.0.text']);
    await page.evaluate(() => { document.querySelector('#workspace-main section.surface > button').textContent = 'Download diagnostic report'; });
    // A reordered level option.
    await page.evaluate(() => { const select = document.querySelector('#workspace-main #log-level'); select.append(select.options[1]); });
    expect(await paths()).toEqual(expect.arrayContaining(['surfaces.1.toolbar.1.options.1']));
    await page.evaluate(() => { const select = document.querySelector('#workspace-main #log-level'); select.insertBefore(select.options[5], select.options[1]); });
    // A primary Refresh.
    await page.evaluate(() => { document.querySelectorAll('#workspace-main .toolbar > button')[0].classList.add('ca-primary'); });
    expect(await paths()).toEqual(expect.arrayContaining(['surfaces.1.toolbar.4.className']));
    await page.evaluate(() => { document.querySelectorAll('#workspace-main .toolbar > button')[0].classList.remove('ca-primary'); });
    // A select squeezed until its chosen option is cut.
    const undoSqueeze = await mutate(page, { css: '#workspace-main #runtime-level { max-width: 30px !important; min-width: 0 !important; }' });
    expect(await paths()).toEqual(expect.arrayContaining(['selectsFit.1']));
    await undoSqueeze();
    // A swapped token.
    const undoToken = await mutate(page, { css: '#workspace-main .settings-layout { --line: rgb(255, 0, 0); }' });
    expect(await paths()).toEqual(expect.arrayContaining(['tokens.--line', 'row.borderTopColor']));
    await undoToken();
    expect(diffInventory(expected, await measure())).toEqual([]);

    // The log cleanup review: a changed grid term, a backup box that came back and a grid token are noticed.
    const conceptReview = await conceptCleanup(page, 'dark');
    const { plan } = await productionCleanup(page, request, 'dark');
    const expectedReview = mapCleanupConcept(conceptReview, plan);
    const reviewPaths = async () => diffInventory(expectedReview, await page.evaluate(measureReview)).map(item => item.path);
    expect(await reviewPaths()).toEqual([]);
    await page.evaluate(() => { document.querySelectorAll('dialog#modal[open] dl.meta-grid dt')[2].textContent = 'Safety'; });
    expect(await reviewPaths()).toEqual(['grid.rows.2.0']);
    await page.evaluate(() => { document.querySelectorAll('dialog#modal[open] dl.meta-grid dt')[2].textContent = 'Backup'; });
    const undoGrid = await mutate(page, { css: 'dialog#modal dl.meta-grid { column-gap: 3px; }' });
    expect(await reviewPaths()).toEqual(expect.arrayContaining(['grid.columnGap']));
    await undoGrid();
    expect(await reviewPaths()).toEqual([]);
});

