import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';
import { seedConcept } from './concept-inventory.mjs';

// System › Overview (#249 System S2 Task 6) on the concept-shaped real fixture: the metrics, rows, Statistics dialog and
// Health report read the server, never sample values (D93, D95, D96). The hooks are workspace_system_fixture.py's.
const workspace = '/prompt_manager/workspace';
const OVERVIEW_URL = '/prompt_manager/system/overview';
const THUMBNAILS_URL = '/prompt_manager/images/thumbnails/status';
const number = value => value.toLocaleString('en-US');

test.beforeEach(async ({ page, request, browserName }) => {
    await startCoverage(page, browserName);
    await seedConcept(request);
    await expect(await request.post('/__test__/system/reset')).toBeOK();
    await expect(await request.post('/__test__/jobs/restart')).toBeOK();
    page.systemErrors = [];
    page.on('pageerror', error => page.systemErrors.push(error.message));
});

test.afterEach(async ({ page, request, browserName }) => {
    await saveCoverage(page, browserName);
    await request.post('/__test__/release');
    await request.post('/__test__/system/reset');
    expect(page.systemErrors).toEqual([]);
});

async function serverJson(request, url) {
    const response = await request.get(url);
    await expect(response).toBeOK();
    return response.json();
}

const layoutOf = page => page.locator('#workspace-main .settings-layout');

async function openOverview(page) {
    await page.goto(`${workspace}#/system/overview`);
    await expect(layoutOf(page).locator(':scope > .summary-metrics')).toBeVisible();
    return layoutOf(page);
}

/** The rows of one Overview surface: `[h3, p, button]`. */
function rowsOf(layout, heading) {
    return layout.locator('.two-col > section.surface').filter({ has: layout.page().getByRole('heading', { name: heading, exact: true }) })
        .locator('.operation').evaluateAll(rows => rows.map(row => [row.querySelector('h3').textContent,
            row.querySelector('p').textContent, row.querySelector('.row-actions button').textContent]));
}

test('overview shows real counts and Not checked', async ({ page, request }) => {
    const writes = [];
    page.on('request', item => { if (!['GET', 'HEAD', 'OPTIONS'].includes(item.method())) writes.push(item.url()); });
    const overview = await serverJson(request, OVERVIEW_URL);
    const thumbnails = await serverJson(request, THUMBNAILS_URL);
    // The concept fixture: six positives, their saved negatives and 24 linked images, all with thumbnails.
    expect([overview.library.positive_prompts, overview.images.indexed, thumbnails.pending]).toEqual([6, 24, 0]);
    // I2: the Overview never walks the sources for thumbnails; the row reads the status route itself.
    expect(overview).not.toHaveProperty('thumbnails');
    const layout = await openOverview(page);
    expect(await layout.locator('.summary-metrics > .metric').evaluateAll(items => items.map(item =>
        [item.querySelector('strong').textContent, item.querySelector('span').textContent]))).toEqual([
        [number(overview.library.positive_prompts), 'Positive prompts'],
        [number(overview.library.negative_variants), 'Negative variants'],
        [number(overview.images.indexed), 'Images'],
        ['Not checked', 'Database integrity'],
    ]);
    expect(await rowsOf(layout, 'Library health')).toEqual([
        ['Ambiguous image links', 'Not available yet.', 'Review metadata'],
        ['Backup status', 'No retained backup record.', 'View backups'],
        ['No missing thumbnails', 'Generate only missing previews when necessary.', 'Review thumbnails'],
    ]);
    expect(await rowsOf(layout, 'Workspace preferences')).toEqual([
        ['One display configuration', 'Grid and density settings are shared across views.', 'Appearance'],
        ['2 output sources', 'Monitoring and enrichment share one source list.', 'Manage sources'],
        ['Statistics', 'Runs and ratings by category, with library totals.', 'Explore'],
    ]);
    expect(writes).toEqual([]);
});

test('Run health checks opens Health report and updates metric 4', async ({ page, request }) => {
    await page.goto(`${workspace}#/system/maintenance`);
    const layout = layoutOf(page);
    await expect(layout.locator(':scope > .notice h2')).toHaveText('Check first. Review changes. Then apply.');
    // The rest of Maintenance is still the existing tools' (D94).
    await expect(layout.locator(':scope > section.handoff h2')).toHaveText('Your current tools are ready');
    const run = layout.getByRole('button', { name: 'Run health checks', exact: true });
    await run.click();
    const dialog = page.locator('dialog#modal[open]');
    await expect(dialog.locator('#modal-title')).toHaveText('Health report');
    const { report } = await serverJson(request, '/prompt_manager/system/health');
    expect(report.status).toBe('incomplete');
    expect(await dialog.locator('.operation-list > .operation').evaluateAll(rows => rows.map(row =>
        [row.querySelector('h3').textContent, row.querySelector('span.chip').textContent]))).toEqual([
        ['Integrity check', 'Passed'], ['Foreign keys & pair references', 'Passed'], ['Canonical hashes', 'Passed'],
        ['Pair uniqueness', 'Passed'], ['Missing files · output', 'Passed'], ['Missing files · input', 'Passed'],
        ['Ambiguous metadata', 'Not available yet'], ['Consistency', 'Passed'],
    ]);
    await expect(dialog.locator('#modal-body > p.small')).toHaveText(/^Checked .+\. Some checks could not run, so this report is incomplete\.$/);
    await expect(dialog.locator('.dialog-actions button')).toHaveText(['Close', 'Review missing links']);
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();
    await expect(page.locator('dialog#modal[open]')).toHaveCount(0);
    await expect(run).toBeFocused();
    await page.locator('#workspace-main .section-nav').getByRole('button', { name: 'Overview', exact: true }).click();
    await expect(layout.locator('.metric').nth(3).locator('strong')).toHaveText('Incomplete');

    // A missing file and an unmounted source: the report counts the readable sources and names the other one.
    await expect(await request.post('/__test__/system/missing-file')).toBeOK();
    await expect(await request.post('/__test__/system/missing-source')).toBeOK();
    await page.locator('#workspace-main .section-nav').getByRole('button', { name: 'Maintenance', exact: true }).click();
    await layout.getByRole('button', { name: 'Run health checks', exact: true }).click();
    await expect(dialog.locator('.operation').filter({ hasText: 'Missing files' }).locator('.chip'))
        .toHaveText(['1 link needs review', 'Passed', 'Not available']);
    await expect(dialog.locator('.operation h3').filter({ hasText: 'Missing files' }))
        .toHaveText(['Missing files · output', 'Missing files · input', 'Missing files · unmounted-drive']);
    // Review missing links goes to Maintenance, where the cleanup's existing tool is linked.
    await dialog.getByRole('button', { name: 'Review missing links', exact: true }).click();
    await expect(page.locator('dialog#modal[open]')).toHaveCount(0);
    await expect(page).toHaveURL(`${workspace}#/system/maintenance`);
    await page.locator('#workspace-main .section-nav').getByRole('button', { name: 'Overview', exact: true }).click();
    await expect(layout.locator('.metric').nth(3).locator('strong')).toHaveText('1 issue');
});

test('a health check that fails says so and keeps the last result', async ({ page, request }) => {
    await expect(await request.post('/__test__/system/fail', { data: { path: '/prompt_manager/system/health' } })).toBeOK();
    await page.goto(`${workspace}#/system/maintenance`);
    const run = layoutOf(page).getByRole('button', { name: 'Run health checks', exact: true });
    await run.click();
    await expect(page.locator('#workspace-toast')).toHaveText('The health check could not finish. Nothing was changed.');
    await expect(page.locator('dialog#modal[open]')).toHaveCount(0);
    await expect(run).not.toHaveAttribute('aria-disabled', 'true');
    await openOverview(page);
    await expect(layoutOf(page).locator('.metric').nth(3).locator('strong')).toHaveText('Not checked');
});

test('thumbnail row reflects the status endpoint', async ({ page, request }) => {
    await expect(await request.post('/__test__/system/unthumbnailed', { data: { count: 3 } })).toBeOK();
    const status = await serverJson(request, '/prompt_manager/images/thumbnails/status');
    expect(status.pending).toBe(3);
    const layout = await openOverview(page);
    const row = layout.locator('.operation').filter({ has: page.getByRole('button', { name: 'Review thumbnails', exact: true }) });
    await expect(row.locator('h3')).toHaveText('3 missing thumbnails');
    // Until the thumbnail review dialog (S5), Review thumbnails opens Sources.
    await row.getByRole('button', { name: 'Review thumbnails', exact: true }).click();
    await expect(page).toHaveURL(`${workspace}#/system/sources`);
});

test('the thumbnail row paints when its own status read answers', async ({ page, request }) => {
    const held = async () => (await serverJson(request, '/__test__/hold')).held[THUMBNAILS_URL] ?? 0;
    await expect(await request.post('/__test__/hold', { data: { path: THUMBNAILS_URL } })).toBeOK();
    const layout = await openOverview(page);
    const row = layout.locator('.operation').filter({ has: page.getByRole('button', { name: 'Review thumbnails', exact: true }) });
    // The other values do not wait for the walk of every source.
    await expect(row.locator('h3')).toHaveText('Checking thumbnails…');
    await expect(layout.locator('.summary-metrics > .metric')).toHaveCount(4);
    await expect.poll(held).toBe(1);
    await expect(await request.post('/__test__/release')).toBeOK();
    await expect(row.locator('h3')).toHaveText('No missing thumbnails');
});

test('a failed thumbnail read is unavailable, never zero', async ({ page }) => {
    await page.route(`**${THUMBNAILS_URL}`, route => route.fulfill({ status: 500, contentType: 'application/json',
        body: JSON.stringify({ success: false, error: 'Injected failure' }) }));
    const layout = await openOverview(page);
    const row = layout.locator('.operation').filter({ has: page.getByRole('button', { name: 'Review thumbnails', exact: true }) });
    await expect(row.locator('h3')).toHaveText('Thumbnail status unavailable');
    await expect(layout.getByRole('alert')).toHaveCount(0);
    await expect(layout.locator('.metric').nth(2).locator('strong')).toHaveText('24');
});

test('ambiguous row reads Not available yet (Q6)', async ({ page }) => {
    const layout = await openOverview(page);
    const row = layout.locator('.operation').filter({ hasText: 'Ambiguous image links' });
    await expect(row.locator('p')).toHaveText('Not available yet.');
    const review = row.getByRole('button', { name: 'Review metadata', exact: true });
    await expect(review).toHaveAttribute('aria-disabled', 'true');
    await review.click({ force: true });
    await expect(page).toHaveURL(`${workspace}#/system/overview`);
});

test('View backups / Appearance / Manage sources switch sections', async ({ page }) => {
    for (const [label, section, pressed] of [['View backups', 'backups', 'Backup & export'], ['Appearance', 'appearance', 'Appearance'],
        ['Manage sources', 'sources', 'Sources & scans']]) {
        const layout = await openOverview(page);
        await layout.getByRole('button', { name: label, exact: true }).click();
        await expect(page).toHaveURL(`${workspace}#/system/${section}`);
        await expect(page.locator('#workspace-main .section-nav [aria-pressed="true"]')).toHaveText(pressed);
        await expect(layout.locator(':scope > .summary-metrics')).toHaveCount(0);
    }
});

test('statistics dialog lists runs by category and library totals', async ({ page, request }) => {
    const statistics = await serverJson(request, '/prompt_manager/system/statistics');
    expect(statistics.by_category.length).toBeGreaterThan(1);
    const layout = await openOverview(page);
    const explore = layout.getByRole('button', { name: 'Explore', exact: true });
    await explore.click();
    const dialog = page.locator('dialog#modal[open]');
    await expect(dialog.locator('#modal-title')).toHaveText('Usage statistics');
    const tables = dialog.locator('#modal-body > table');
    await expect(tables.locator('caption')).toHaveText(['Runs by category', 'Library totals']);
    await expect(tables.first().locator('thead th')).toHaveText(['Category', 'Runs', 'Avg. rating']);
    const cells = table => table.locator('tbody tr').evaluateAll(rows => rows.map(row => [...row.cells].map(cell => cell.textContent)));
    expect(await cells(tables.first())).toEqual(statistics.by_category.map(row => [row.category ?? 'Uncategorized',
        number(row.runs), row.average_rating === null ? 'Not rated' : `${number(row.average_rating)} / 5`]));
    expect(await cells(tables.nth(1))).toEqual([['Prompts', number(statistics.totals.total_prompts)],
        ['Tags', number(statistics.totals.total_tags)], ['Images', number(statistics.totals.total_images)],
        ['Rated images', number(statistics.rated_images)]]);
    await page.keyboard.press('Escape');
    await expect(explore).toBeFocused();
    // A statistics read that fails says so; the Overview stays.
    await expect(await request.post('/__test__/system/fail', { data: { path: '/prompt_manager/system/statistics' } })).toBeOK();
    await explore.click();
    await expect(page.locator('#workspace-toast')).toHaveText('Statistics could not be loaded. Your saved data has not changed.');
    await expect(page.locator('dialog#modal[open]')).toHaveCount(0);
});

test('load failure shows Retry and no zeros', async ({ page, request }) => {
    await expect(await request.post('/__test__/system/fail', { data: { path: OVERVIEW_URL } })).toBeOK();
    await page.goto(`${workspace}#/system/overview`);
    const layout = layoutOf(page);
    await expect(layout.getByRole('alert')).toHaveText('The overview could not be loaded. Your saved data has not changed.');
    await expect(layout.locator('.metric')).toHaveCount(0);
    await expect(layout).not.toContainText('0');
    await layout.getByRole('button', { name: 'Retry', exact: true }).click();
    await expect(layout.locator('.summary-metrics > .metric')).toHaveCount(4);
    await expect(layout.getByRole('alert')).toHaveCount(0);
});

test('out-of-order overview responses never repaint a newer section', async ({ page, request }) => {
    const held = async () => (await serverJson(request, '/__test__/hold')).held[OVERVIEW_URL] ?? 0;
    await expect(await request.post('/__test__/hold', { data: { path: OVERVIEW_URL } })).toBeOK();
    await page.goto(`${workspace}#/system/overview`);
    const layout = layoutOf(page);
    await expect(layout.getByRole('status')).toHaveText('Loading the overview…');
    await expect.poll(held).toBe(1);
    // Leave for Appearance, come back (a second read), then leave again before either answers.
    const nav = page.locator('#workspace-main .section-nav');
    await nav.getByRole('button', { name: 'Appearance', exact: true }).click();
    await nav.getByRole('button', { name: 'Overview', exact: true }).click();
    await expect.poll(held).toBe(2);
    await nav.getByRole('button', { name: 'Sources & scans', exact: true }).click();
    await expect(await request.post('/__test__/release')).toBeOK();
    await expect.poll(held).toBe(0);
    // Both late answers reached a section that had left: Sources keeps its own body.
    await expect(layout.locator(':scope > section.handoff')).toBeVisible();
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await expect(layout.locator('.summary-metrics')).toHaveCount(0);
    expect(await layout.evaluate(node => [...node.children].map(child => child.className))).toEqual(['surface handoff', 'save-row']);
    // Back on Overview, one fresh read paints one set of metrics.
    await nav.getByRole('button', { name: 'Overview', exact: true }).click();
    await expect(layout.locator('.summary-metrics')).toHaveCount(1);
    await expect(layout.locator('.two-col')).toHaveCount(1);
});
