import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from '../../tools/vendor/node_modules/@playwright/test/index.mjs';
import { THEMES, seedConcept, openConcept, openProduction, diffInventory, mutate } from './concept-inventory.mjs';

// Activity-1 Task 3. The review dialog is compared with the concept's own `scan-review` ("Review metadata scan",
// app.js 1428–1432) and its backup box with the concept's System `.notice`; the differences are D55. The job view
// has no concept counterpart: it is compared with the approved mockup `activity-scan.html` (a local, ignored file),
// whose structure and CSS values are restated below (D56). Each comparison is shown to notice a mutation.
const workspace = '/prompt_manager/workspace';

test.beforeEach(async ({ page, request, browserName }) => {
    await startCoverage(page, browserName);
    await expect(await request.post('/__test__/jobs/restart')).toBeOK();
    page.conceptErrors = [];
    page.on('pageerror', error => page.conceptErrors.push(error.message));
});

test.afterEach(async ({ page, request, browserName }) => {
    await saveCoverage(page, browserName);
    expect(page.conceptErrors).toEqual([]);
    await request.post('/__test__/jobs/restart');
});

/** The open review dialog: its body's element sequence, the meta grid, the sentence, the notice and the actions. */
function measureReview() {
    const dialog = document.querySelector('dialog#modal[open]');
    const css = (node, ...keys) => node ? Object.fromEntries(keys.map(key => [key, getComputedStyle(node)[key]])) : null;
    const name = node => [node.localName, ...[...node.classList].sort()].join('.');
    const grid = dialog.querySelector('#modal-body > .meta-grid');
    const sentence = dialog.querySelector('#modal-body > p.small');
    const actions = dialog.querySelector('#modal-body > .dialog-actions');
    return {
        title: dialog.querySelector('#modal-title').textContent,
        body: [...dialog.querySelector('#modal-body').children].map(name),
        grid: {
            tag: grid.localName,
            ...css(grid, 'display', 'gridTemplateColumns', 'columnGap', 'rowGap', 'marginTop', 'marginBottom', 'fontSize'),
            entries: [...grid.children].map(entry => ({
                tag: entry.localName, dt: entry.querySelector('dt').textContent, dd: entry.querySelector('dd').textContent,
                dtStyle: css(entry.querySelector('dt'), 'color', 'fontSize'),
                ddStyle: css(entry.querySelector('dd'), 'marginTop', 'marginLeft', 'fontSize', 'color'),
            })),
        },
        sentence: { text: sentence.textContent, ...css(sentence, 'fontSize', 'color', 'lineHeight', 'marginTop') },
        actions: {
            ...css(actions, 'display', 'justifyContent', 'columnGap', 'marginTop'),
            buttons: [...actions.children].map(button => ({ text: button.textContent, className: button.className,
                ...css(button, 'backgroundColor', 'color', 'fontSize', 'paddingTop', 'paddingLeft', 'borderTopLeftRadius') })),
        },
    };
}

/** A `.notice` box: its frame and its text and actions columns, without the copy. */
function measureNotice(selector) {
    const notice = document.querySelector(selector);
    const css = (node, ...keys) => node ? Object.fromEntries(keys.map(key => [key, getComputedStyle(node)[key]])) : null;
    return {
        frame: css(notice, 'display', 'alignItems', 'justifyContent', 'columnGap', 'borderTopWidth', 'borderTopStyle',
            'borderTopColor', 'backgroundColor', 'borderTopLeftRadius', 'paddingTop', 'paddingLeft'),
        columns: [...notice.children].map(node => node.localName + (node.className ? `.${node.className}` : '')),
        text: css(notice.querySelector(':scope > div > p'), 'fontSize', 'color', 'marginTop'),
        actions: css(notice.querySelector(':scope > .row-actions'), 'display', 'flexShrink'),
    };
}

/** D55: the concept's dialog with the production differences applied. */
function mapReviewConcept(reference) {
    return {
        ...reference,
        // No sample-plan note (prototype copy); the backup box sits before the actions.
        body: [...reference.body.filter(item => item !== 'p.demo-note').slice(0, -1), 'div.notice', reference.body.at(-1)]
            .map(item => item === 'div.meta-grid' ? 'dl.meta-grid' : item),
        // A `dl`, as in the concept's maintenance plan; real sources and the fixed Activity-1 scope.
        grid: { ...reference.grid, tag: 'dl', entries: reference.grid.entries.map(entry => ({ ...entry,
            dd: { Sources: '2 output folders', Scope: 'All files' }[entry.dt] })) },
        actions: { ...reference.actions, buttons: reference.actions.buttons.map(button => button.text === 'Simulate scan'
            ? { ...button, text: 'Start scan' } : button) },
    };
}

async function conceptReview(page, theme) {
    await openConcept(page, 'images', theme);
    await page.locator('#main .heading-actions').getByRole('button', { name: 'Import', exact: true }).click();
    await page.locator('dialog#modal[open] .operation').filter({ hasText: 'Scan an output folder' })
        .getByRole('button', { name: 'Review scan', exact: true }).click();
    await expect(page.locator('dialog#modal[open] #modal-title')).toHaveText('Review metadata scan');
    await page.mouse.move(0, 0);
    const review = await page.evaluate(measureReview);
    await page.keyboard.press('Escape');
    // The concept's System → Sources notice ("Bring older images into your prompt history").
    await page.goto(`/__test__/canonical/workspace#system`);
    await page.getByRole('combobox', { name: 'Theme', exact: true }).selectOption(theme);
    await page.locator('#main').getByRole('button', { name: 'Manage sources', exact: true }).click();
    await expect(page.locator('#main .notice')).toBeVisible();
    return { review, notice: await page.evaluate(measureNotice, '#main .notice') };
}

async function productionReview(page, theme) {
    await openProduction(page, 'images', theme);
    await page.locator('#workspace-main .heading-actions').getByRole('button', { name: 'Import', exact: true }).click();
    await page.locator('dialog#modal[open] .operation').filter({ hasText: 'Scan for metadata' })
        .getByRole('button', { name: 'Review scan', exact: true }).click();
    await expect(page.locator('dialog#modal[open] #modal-title')).toHaveText('Review metadata scan');
    // The swapped dialog can open under the pointer that pressed Review scan; measure it at rest.
    await page.mouse.move(0, 0);
    return { review: await page.evaluate(measureReview), notice: await page.evaluate(measureNotice, 'dialog#modal[open] .notice') };
}

for (const theme of THEMES) test(`review dialog matches the concept scan-review ${theme}`, async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    await seedConcept(request);
    const concept = await conceptReview(page, theme);
    expect(concept.review.body).toEqual(['p.demo-note', 'div.meta-grid', 'p.small', 'div.dialog-actions']);
    const production = await productionReview(page, theme);
    expect(diffInventory(mapReviewConcept(concept.review), production.review)).toEqual([]);
    expect(diffInventory(concept.notice, production.notice)).toEqual([]);
    await expect(page.locator('dialog#modal[open] .notice h3')).toHaveText('Back up the database first (recommended)');
    await expect(page.locator('dialog#modal[open] .notice .row-actions button')).toHaveText(['Back up now']);

    // Mutation check: one CSS change and one removed element are both noticed, then undone to an exact match again.
    const undoCss = await mutate(page, { css: '#modal .meta-grid { gap: 3px; } #modal .notice { padding: 2px; }' });
    const cssMutated = await page.evaluate(measureReview);
    const noticeMutated = await page.evaluate(measureNotice, 'dialog#modal[open] .notice');
    expect(diffInventory(mapReviewConcept(concept.review), cssMutated).map(item => item.path)).toEqual(expect.arrayContaining(['grid.columnGap', 'grid.rowGap']));
    expect(diffInventory(concept.notice, noticeMutated).map(item => item.path)).toEqual(['frame.paddingTop', 'frame.paddingLeft']);
    await undoCss();
    const undoDom = await mutate(page, { remove: ['#modal-body > p.small'] });
    const domMutated = await page.evaluate(() => [...document.querySelectorAll('#modal-body > *')].map(node => node.localName));
    expect(domMutated).toEqual(['dl', 'div', 'div']);
    await undoDom();
    expect(diffInventory(mapReviewConcept(concept.review), await page.evaluate(measureReview))).toEqual([]);
});

// The approved mockup's job view, restated: structure first, then the values of its CSS rules.
const MOCKUP_TREE = ['div.heading', 'section.surface', 'section.surface', 'details.term'];
const MOCKUP_PROGRESS = ['div.panel-heading(h2,span.small)', 'progress', 'dl.stat-row(div.added.stat,div.stat,div.stat.warn,div.stat,div.bad.stat)'];
const MOCKUP_RESULTS = ['div.panel-heading(h2,label.search-field)', 'div.outcomes(div.outcome-list,div.outcome-files)'];
const MOCKUP_STYLES = {
    statRow: { display: 'grid', columnGap: '10px', columns: 5, marginTop: '16px', marginBottom: '16px' },
    stat: { borderTopWidth: '1px', borderTopLeftRadius: '7px', paddingTop: '12px', paddingLeft: '14px' },
    dt: { fontSize: '11px' },
    dd: { fontSize: '20px', fontWeight: '600', marginTop: '6px', marginLeft: '0px' },
    outcomes: { display: 'grid', columnGap: '16px', first: '220px', marginTop: '6px' },
    outcomeButton: { display: 'flex', justifyContent: 'space-between', marginBottom: '6px', textAlign: 'left' },
    fileRow: { display: 'grid', columnGap: '12px', paddingTop: '9px', paddingLeft: '4px', borderBottomWidth: '1px', fontSize: '12px' },
    path: { monospace: true, overflowWrap: 'anywhere' },
    search: { maxWidth: '320px' },
    term: { marginTop: '16px', borderTopWidth: '1px', borderTopLeftRadius: '7px', overflow: 'hidden' },
    summary: { paddingTop: '11px', paddingLeft: '14px', fontSize: '12px', cursor: 'pointer' },
    pre: { marginTop: '0px', paddingTop: '12px', paddingLeft: '14px', maxHeight: '180px', fontSize: '11px', lineHeight: '18.7px', overflow: 'auto' },
};

function measureJobView() {
    const main = document.querySelector('#workspace-main');
    const css = (node, ...keys) => Object.fromEntries(keys.map(key => [key, getComputedStyle(node)[key]]));
    const name = node => [node.localName, ...[...node.classList].sort()].join('.');
    // The rows and sections the mockup nests are listed with their children.
    const shape = node => node.matches('.panel-heading, .outcomes, dl.stat-row')
        ? `${name(node)}(${[...node.children].map(name).join(',')})` : name(node);
    const [progress, results] = main.querySelectorAll(':scope > section.surface');
    const statRow = progress.querySelector('dl.stat-row');
    const stat = statRow.querySelector('.stat');
    const outcomes = results.querySelector('.outcomes');
    const button = outcomes.querySelector('.outcome-list > button');
    const row = outcomes.querySelector('.file-row');
    const term = main.querySelector(':scope > details.term');
    const pre = term.querySelector('pre');
    const columns = getComputedStyle(statRow).gridTemplateColumns.split(' ');
    return {
        tree: [...main.children].map(name),
        progress: [...progress.children].map(shape),
        results: [...results.children].map(shape),
        h2: [...main.querySelectorAll(':scope > section.surface > .panel-heading > h2')].map(node => node.textContent),
        summary: term.querySelector('summary').textContent,
        styles: {
            statRow: { ...css(statRow, 'display', 'columnGap', 'marginTop', 'marginBottom'), columns: columns.length },
            stat: css(stat, 'borderTopWidth', 'borderTopLeftRadius', 'paddingTop', 'paddingLeft'),
            dt: css(stat.querySelector('dt'), 'fontSize'),
            dd: css(stat.querySelector('dd'), 'fontSize', 'fontWeight', 'marginTop', 'marginLeft'),
            outcomes: { ...css(outcomes, 'display', 'columnGap', 'marginTop'), first: getComputedStyle(outcomes).gridTemplateColumns.split(' ')[0] },
            outcomeButton: css(button, 'display', 'justifyContent', 'marginBottom', 'textAlign'),
            fileRow: css(row, 'display', 'columnGap', 'paddingTop', 'paddingLeft', 'borderBottomWidth', 'fontSize'),
            path: { monospace: /monospace/.test(getComputedStyle(row.querySelector('.path')).fontFamily),
                overflowWrap: getComputedStyle(row.querySelector('.path')).overflowWrap },
            search: css(results.querySelector('.search-field'), 'maxWidth'),
            term: css(term, 'marginTop', 'borderTopWidth', 'borderTopLeftRadius', 'overflow'),
            summary: css(term.querySelector('summary'), 'paddingTop', 'paddingLeft', 'fontSize', 'cursor'),
            // WebKit reports the 1.7 line height of 11px as 18.700001px; compare it to the hundredth of a pixel.
            pre: { ...css(pre, 'marginTop', 'paddingTop', 'paddingLeft', 'maxHeight', 'fontSize', 'overflow'),
                lineHeight: `${Math.round(parseFloat(getComputedStyle(pre).lineHeight) * 100) / 100}px` },
        },
        // Mockup colours: the pressed outcome takes the accent border on the soft blue, the tiles their tone.
        pressed: (() => {
            const probe = document.createElement('span');
            main.append(probe);
            const tone = name => { probe.style.color = `var(${name})`; return getComputedStyle(probe).color; };
            const values = { accent: tone('--ca-accent'), soft: tone('--soft-blue'), green: tone('--green'), red: tone('--red'), warning: tone('--warning') };
            probe.remove();
            const on = outcomes.querySelector('.outcome-list > button[aria-pressed="true"]');
            const dd = selector => getComputedStyle(statRow.querySelector(`${selector} dd`)).color;
            return { border: getComputedStyle(on).borderTopColor === values.accent, background: getComputedStyle(on).backgroundColor === values.soft,
                added: dd('.stat.added') === values.green, bad: dd('.stat.bad') === values.red, warn: dd('.stat.warn') === values.warning };
        })(),
    };
}

const EXPECTED_JOB_VIEW = {
    tree: MOCKUP_TREE, progress: MOCKUP_PROGRESS, results: MOCKUP_RESULTS, h2: ['Progress', 'Results'],
    summary: '● Scan log — raw detail, click to show', styles: MOCKUP_STYLES,
    pressed: { border: true, background: true, added: true, bad: true, warn: true },
};

async function openRunningJob(page, request, theme, viewport = { width: 1600, height: 1100 }) {
    await page.setViewportSize(viewport);
    const started = await request.post('/__test__/jobs/start', { data: { total: 19622 } });
    await expect(started).toBeOK();
    const { job } = await started.json();
    await expect(await request.post(`/__test__/jobs/${job.id}/step`, { data: { processed: 8240,
        counters: { known: 6511, review: 37, no_metadata: 254, errors: 18 },
        records: [{ group: 'added', path: 'CCC/mabes9sans-dataset-v1/IMG_GEN_00369_.png', detail: 'Added to the Library as “Mabes portrait”' }] } })).toBeOK();
    await openProduction(page, `activity/${job.id}`, theme);
    await expect(page.locator('#workspace-main .file-row')).toHaveCount(1);
    return job;
}

for (const theme of THEMES) test(`job view follows the approved mockup ${theme}`, async ({ page, request }) => {
    await seedConcept(request);
    await openRunningJob(page, request, theme);
    expect(await page.evaluate(measureJobView)).toEqual(EXPECTED_JOB_VIEW);

    // Mutation check: a CSS change (tile gap) and a removed element (the outcome list) are both noticed.
    const undoCss = await mutate(page, { css: '#workspace-main .stat-row { gap: 3px; }' });
    expect(diffInventory(EXPECTED_JOB_VIEW, await page.evaluate(measureJobView)).map(item => item.path)).toEqual(['styles.statRow.columnGap']);
    await undoCss();
    const undoDom = await mutate(page, { remove: ['#workspace-main details.term'] });
    expect(await page.evaluate(() => [...document.querySelector('#workspace-main').children].map(node => node.localName)))
        .toEqual(['div', 'section', 'section']);
    await undoDom();
    expect(await page.evaluate(measureJobView)).toEqual(EXPECTED_JOB_VIEW);
});

test('job view fits a phone without page scroll', async ({ page, request }) => {
    await seedConcept(request);
    await openRunningJob(page, request, 'dark', { width: 390, height: 844 });
    const layout = await page.evaluate(() => {
        const main = document.querySelector('#workspace-main');
        const columns = node => getComputedStyle(node).gridTemplateColumns.split(' ').length;
        return {
            scroll: document.documentElement.scrollWidth <= document.documentElement.clientWidth,
            tiles: columns(main.querySelector('dl.stat-row')),
            outcomes: columns(main.querySelector('.outcomes')),
            inside: [...main.querySelectorAll('.stat, .outcome-list button, .file-row, .search-field')]
                .every(node => node.getBoundingClientRect().right <= main.getBoundingClientRect().right + 0.5),
        };
    });
    expect(layout).toEqual({ scroll: true, tiles: 2, outcomes: 1, inside: true });
});
