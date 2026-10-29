import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';
import { seedConcept } from './concept-inventory.mjs';
import { readCoverageTable, parseControl, CONTROL_EXEMPTIONS } from '../../tools/vendor/legacy-coverage.mjs';

// System › Overview (#249 System S2 Task 6) on the concept-shaped real fixture: the metrics, rows, Statistics dialog and
// Health report read the server, never sample values (D93, D95, D96). The hooks are workspace_system_fixture.py's.
const workspace = '/prompt_manager/workspace';
const OVERVIEW_URL = '/prompt_manager/system/overview';
const THUMBNAILS_URL = '/prompt_manager/images/thumbnails/status';
const number = value => value.toLocaleString('en-US');

test.beforeEach(async ({ page, request, browserName }) => {
    await startCoverage(page, browserName);
    page.manifest = await seedConcept(request);
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
    // The Backup status row is its own read of /backup/status (S9): compare once it answered.
    await expect(layoutOf(page).locator('.operation').filter({ has: page.locator('h3', { hasText: 'Backup status' }) })
        .locator('p')).not.toHaveText('Checking backups…');
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
    // The fixture records no ambiguous file: the Needs review queue is empty.
    expect(overview.needs_review).toEqual({ pending: 0 });
    const layout = await openOverview(page);
    expect(await layout.locator('.summary-metrics > .metric').evaluateAll(items => items.map(item =>
        [item.querySelector('strong').textContent, item.querySelector('span').textContent]))).toEqual([
        [number(overview.library.positive_prompts), 'Positive prompts'],
        [number(overview.library.negative_variants), 'Negative variants'],
        [number(overview.images.indexed), 'Images'],
        ['Not checked', 'Database integrity'],
    ]);
    expect(await rowsOf(layout, 'Library health')).toEqual([
        ['0 ambiguous image links', 'Review metadata before assigning a positive / negative pair.', 'Review metadata'],
        ['Backup status', 'No backup recorded.', 'View backups'],
        // D129: the fixture's database is configured explicitly, so it reads as config.json's choice and does not move.
        ['Library location', 'browser.db · The database path set in config.json.', 'Details'],
        ['No missing thumbnails', 'Generate only missing previews when necessary.', 'Review thumbnails'],
    ]);
    expect(await rowsOf(layout, 'Workspace preferences')).toEqual([
        ['One display configuration', 'Grid and density settings are shared across views.', 'Appearance'],
        ['2 output sources', 'Monitoring and enrichment share one source list.', 'Manage sources'],
        ['Statistics', 'Runs by category, with library totals.', 'Explore'],
    ]);
    expect(writes).toEqual([]);
});

// D129 (#249 S6b): where the library lives. The hook makes the fixture's database read as a node-folder library; its
// Move library is a stand-in that touches no file (workspace_system_fixture.py).
const banner = page => page.locator('#library-banner');
const LIBRARY_URL = '/prompt_manager/system/library';

test('a node-folder library is offered a move above every page; Keep it here stops the offer', async ({ page, request }) => {
    await expect(await request.post('/__test__/system/library', { data: { state: 'node_folder' } })).toBeOK();
    await page.goto(`${workspace}#/images`);
    await expect(banner(page).locator('.notice h2')).toHaveText('Your library is inside the node folder');
    await expect(banner(page).locator('.notice p')).toHaveText(/^Your library is stored inside the node folder, where an update or reinstall can delete it\./);
    await expect(banner(page).locator('.notice .row-actions button')).toHaveText(['Keep it here', 'Move library']);
    await expect(banner(page).getByRole('button', { name: 'Move library', exact: true })).toHaveClass(/ca-primary/);
    await page.goto(`${workspace}#/library`);
    await expect(banner(page).locator('.notice')).toBeVisible();
    await banner(page).getByRole('button', { name: 'Keep it here', exact: true }).click();
    await expect(banner(page)).toBeHidden();
    const { location } = await serverJson(request, LIBRARY_URL);
    expect([location.keep, location.offer, location.movable]).toEqual([true, false, true]);
    await page.reload();
    const layout = await openOverview(page);
    await expect(banner(page)).toBeHidden();
    const row = layout.locator('.operation').filter({ hasText: 'Library location' });
    await expect(row.locator('p')).toHaveText('browser.db · The node folder, where an update or reinstall can delete it. '
        + 'You chose to keep it here.');
    // The row stays: the move is still one press away.
    await expect(row.locator('.row-actions button')).toHaveText('Move library');
});

test('Move library is reviewed, runs as an Activity job and ends the offer', async ({ page, request }) => {
    await expect(await request.post('/__test__/system/library', { data: { state: 'node_folder' } })).toBeOK();
    const layout = await openOverview(page);
    const row = layout.locator('.operation').filter({ hasText: 'Library location' });
    await expect(row.locator('p')).toHaveText('browser.db · The node folder, where an update or reinstall can delete it.');
    const move = banner(page).getByRole('button', { name: 'Move library', exact: true });
    await move.click();
    const dialog = page.locator('dialog#modal[open]');
    await expect(dialog.locator('#modal-title')).toHaveText('Move library');
    expect(await dialog.locator('dl.meta-grid > div').evaluateAll(items => items.map(item =>
        [item.querySelector('dt').textContent, item.querySelector('dd').textContent]))).toEqual([
        ['From', 'browser.db'], ['To', 'user/__promptmanager/prompts.db'], ['Size', expect.stringMatching(/ (KB|MB)$/)],
        ['Moves with it', 'config.json and the database backups'], ['Old files', 'Renamed *.moved-to-…, never deleted'],
    ]);
    await expect(dialog.locator('.dialog-actions button')).toHaveText(['Cancel', 'Move library']);
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(page.locator('dialog#modal[open]')).toHaveCount(0);
    await expect(move).toBeFocused();
    await row.getByRole('button', { name: 'Move library', exact: true }).click();
    await dialog.getByRole('button', { name: 'Move library', exact: true }).click();
    await expect(page).toHaveURL(/#\/activity\/[^/]+$/);
    await expect(page.locator('#workspace-main')).toContainText(
        /Moved the library to user\/__promptmanager: \d+ prompts? and 0 images verified\. The old files were renamed browser\.db\.moved-to-__promptmanager; nothing was deleted\./);
    // The offer ends with the job; the Overview says where the library lives now.
    await expect(banner(page)).toBeHidden();
    const after = await openOverview(page);
    await expect(after.locator('.operation').filter({ hasText: 'Library location' }).locator('p'))
        .toHaveText('browser.db · ComfyUI\'s user folder, which updates and reinstalls of the node leave alone.');
});

test('a move that fails says so and keeps the offer; a refused start says why', async ({ page, request }) => {
    await expect(await request.post('/__test__/system/library', { data: { state: 'failing' } })).toBeOK();
    await page.goto(`${workspace}#/images`);
    await banner(page).getByRole('button', { name: 'Move library', exact: true }).click();
    await page.locator('dialog#modal[open]').getByRole('button', { name: 'Move library', exact: true }).click();
    await expect(page).toHaveURL(/#\/activity\/[^/]+$/);
    await expect(page.locator('#workspace-main')).toContainText(
        'The copy did not pass verification. Nothing was moved. The library is still in the node folder, unchanged.');
    await expect(banner(page).locator('.notice')).toBeVisible();
    await expect(await request.post('/__test__/system/fail', { data: { path: `${LIBRARY_URL}/move` } })).toBeOK();
    await banner(page).getByRole('button', { name: 'Move library', exact: true }).click();
    await page.locator('dialog#modal[open]').getByRole('button', { name: 'Move library', exact: true }).click();
    await expect(page.locator('#workspace-toast')).toHaveText('Move library could not start. Try again.');
});

test('a library that does not move shows its details', async ({ page }) => {
    const layout = await openOverview(page);
    await expect(banner(page)).toBeHidden();
    const details = layout.locator('.operation').filter({ hasText: 'Library location' }).getByRole('button', { name: 'Details', exact: true });
    await details.click();
    const dialog = page.locator('dialog#modal[open]');
    await expect(dialog.locator('#modal-title')).toHaveText('Library location');
    expect(await dialog.locator('dl.meta-grid > div').evaluateAll(items => items.map(item =>
        [item.querySelector('dt').textContent, item.querySelector('dd').textContent.replace(/[\d.,]+ (KB|MB)$/, 'N')]))).toEqual([
        ['Database', 'browser.db'], ['Why here', 'The database path set in config.json'],
        ['Beside it', 'config.json, the database backups and the logs'], ['Size', 'N'],
    ]);
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();
    await expect(details).toBeFocused();
});

test('Run health checks opens Health report and updates metric 4', async ({ page, request }) => {
    await page.goto(`${workspace}#/system/maintenance`);
    const layout = layoutOf(page);
    await expect(layout.locator(':scope > .notice h2')).toHaveText('Check first. Review changes. Then apply.');
    // Maintenance is native (S7): its two surfaces follow the notice.
    await expect(layout.locator(':scope > section.surface > h2')).toHaveText(['Database', 'Images & cache']);
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
    // Review missing links reviews a fresh Missing-file links plan (S7); nothing is cleaned up.
    await dialog.getByRole('button', { name: 'Review missing links', exact: true }).click();
    await expect(dialog.locator('#modal-title')).toHaveText('Review maintenance plan');
    await expect(dialog.locator('#modal-body > h3')).toHaveText('Missing-file links');
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
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
});

test('Overview Review thumbnails opens the same dialog', async ({ page, request }) => {
    await expect(await request.post('/__test__/system/unthumbnailed', { data: { count: 2 } })).toBeOK();
    const plan = await serverJson(request, '/prompt_manager/images/thumbnails/plan?mode=missing');
    const starts = [];
    page.on('request', item => { if (item.method() === 'POST' && item.url().endsWith('/prompt_manager/jobs/thumbnails')) starts.push(item.postDataJSON()); });
    const layout = await openOverview(page);
    const row = layout.locator('.operation').filter({ has: page.getByRole('button', { name: 'Review thumbnails', exact: true }) });
    await expect(row.locator('h3')).toHaveText('2 missing thumbnails');
    await row.getByRole('button', { name: 'Review thumbnails', exact: true }).click();
    const review = page.locator('dialog#modal[open]');
    await expect(review.locator('#modal-title')).toHaveText('Thumbnail maintenance');
    await expect(page).toHaveURL(`${workspace}#/system/overview`);
    await expect(review.locator('.field > label')).toHaveText(['Scope']);
    await expect(review.locator('#modal-body > p.small').first()).toHaveText(`2 previews to generate · ${number(plan.fresh)} already up to date.`);
    // Escape closes it and focus returns to the row's button.
    await page.keyboard.press('Escape');
    await expect(review).toHaveCount(0);
    await expect(row.getByRole('button', { name: 'Review thumbnails', exact: true })).toBeFocused();
    await row.getByRole('button', { name: 'Review thumbnails', exact: true }).click();
    await review.getByRole('button', { name: 'Start', exact: true }).click();
    await expect(page).toHaveURL(/#\/activity\/[0-9a-f]{32}$/);
    expect(starts).toEqual([{ mode: 'missing' }]);
    const id = page.url().split('/').pop();
    await expect.poll(async () => (await serverJson(request, `/prompt_manager/jobs/${id}`)).job.state).toBe('finished');
});

test('the thumbnail row counts outdated previews and offers Convert outdated (#317)', async ({ page, request }) => {
    await expect(await request.post('/__test__/system/legacy-previews', { data: { count: 2 } })).toBeOK();
    const layout = await openOverview(page);
    const row = layout.locator('.operation').filter({ has: page.getByRole('button', { name: 'Review thumbnails', exact: true }) });
    await expect(row.locator('h3')).toHaveText('No missing thumbnails · 2 outdated');
    await expect(row.locator('.row-actions > button')).toHaveText(['Review thumbnails', 'Convert outdated']);
    await row.getByRole('button', { name: 'Convert outdated', exact: true }).click();
    const review = page.locator('dialog#modal[open]');
    await expect(review.locator('#modal-title')).toHaveText('Thumbnail maintenance');
    // It opens on the Convert outdated scope; nothing starts until Start.
    await expect(review.locator('#thumb-mode')).toHaveValue('convert');
    await expect(review.locator('#modal-body > p.small').first()).toHaveText('2 previews in an older format to convert.');
    expect((await serverJson(request, '/prompt_manager/jobs')).jobs).toEqual([]);
    await page.keyboard.press('Escape');
    await expect(row.getByRole('button', { name: 'Convert outdated', exact: true })).toBeFocused();
});

test('Appearance › Thumbnails saves the server thumbnail settings and never converts by itself (#317)', async ({ page, request }) => {
    await expect(await request.post('/__test__/system/jxl-encoder', { data: { available: false } })).toBeOK();
    await expect(await request.post('/__test__/system/legacy-previews', { data: { count: 1 } })).toBeOK();
    await page.goto(`${workspace}#/system/appearance`);
    const surface = page.locator('#workspace-main .settings-layout > section.surface').nth(1);
    await expect(surface.locator(':scope > h2')).toHaveText('Thumbnails');
    await expect(surface.locator('.setting-row label')).toHaveText(['Thumbnail format', 'Thumbnail quality', 'Thumbnail size limit']);
    const format = surface.locator('#thumbnail-format');
    await expect(format).toBeEnabled();
    await expect(format).toHaveValue('webp');
    await expect(surface.locator('#thumbnail-quality')).toHaveValue('80');
    await expect(surface.locator('#thumbnail-edge')).toHaveValue('512');
    // D130: JPEG XL is offered only with an encoder; without one the row says why, in the server's words.
    await expect(format.locator('option[value="jxl"]')).toBeDisabled();
    await expect(surface.locator('.setting-row').first().locator('p.small').first()).toContainText('No JPEG XL encoder found.');
    await format.selectOption('jpeg');
    await surface.locator('#thumbnail-quality').fill('65');
    await surface.locator('#thumbnail-quality').dispatchEvent('change');
    await page.getByRole('button', { name: 'Save preferences', exact: true }).click();
    await expect(page.locator('#settings-status')).toHaveText('Saved to the server configuration.');
    const { settings } = await serverJson(request, '/prompt_manager/settings');
    expect([settings.thumbnail_format, settings.thumbnail_quality, settings.thumbnail_edge]).toEqual(['jpeg', 65, 512]);
    // Changing the format converts nothing: the row says so and the old preview is still listed.
    await expect(surface.locator('.operation p')).toHaveText('Format saved. Existing previews keep their old format until you convert them.');
    expect((await serverJson(request, '/prompt_manager/jobs')).jobs).toEqual([]);
    expect((await serverJson(request, '/prompt_manager/images/thumbnails/status')).outdated).toBeGreaterThan(0);
    // With an encoder JPEG XL can be chosen.
    await expect(await request.post('/__test__/system/jxl-encoder', { data: { available: true } })).toBeOK();
    await page.reload();
    await expect(page.locator('#workspace-main #thumbnail-format option[value="jxl"]')).toBeEnabled();
    await expect(page.locator('#workspace-main #thumbnail-format')).toHaveValue('jpeg');
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

test('ambiguous row reads the pending count and Review metadata opens Needs review', async ({ page }) => {
    const layout = await openOverview(page);
    const row = layout.locator('.operation').filter({ hasText: 'ambiguous image links' });
    await expect(row.locator('h3')).toHaveText('0 ambiguous image links');
    await expect(row.locator('p')).toHaveText('Review metadata before assigning a positive / negative pair.');
    const review = row.getByRole('button', { name: 'Review metadata', exact: true });
    await expect(review).not.toHaveAttribute('aria-disabled');
    await review.click();
    // The concept's go-review: the Needs review page.
    await expect(page).toHaveURL(`${workspace}#/review`);
    await expect(page.getByRole('heading', { name: 'Metadata review', exact: true })).toBeVisible();
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
    // D153 (#447): no rating column and no rated-images total.
    await expect(tables.first().locator('thead th')).toHaveText(['Category', 'Runs']);
    const cells = table => table.locator('tbody tr').evaluateAll(rows => rows.map(row => [...row.cells].map(cell => cell.textContent)));
    expect(await cells(tables.first())).toEqual(statistics.by_category.map(row => [row.category ?? 'Uncategorized', number(row.runs)]));
    expect(await cells(tables.nth(1))).toEqual([['Prompts', number(statistics.totals.total_prompts)],
        ['Tags', number(statistics.totals.total_tags)], ['Images', number(statistics.totals.total_images)]]);
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
    // Diagnostics & logs is native (S12): its body is its two surfaces.
    await nav.getByRole('button', { name: 'Diagnostics & logs', exact: true }).click();
    await expect(layout.locator(':scope > section.surface')).toHaveCount(2);
    await expect(await request.post('/__test__/release')).toBeOK();
    await expect.poll(held).toBe(0);
    // Both late answers reached a section that had left: Diagnostics keeps its own body.
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await expect(layout.locator('.summary-metrics')).toHaveCount(0);
    expect(await layout.evaluate(node => [...node.children].map(child => `${child.localName}.${child.className}`)))
        .toEqual(['section.surface', 'section.surface', 'div.save-row']);
    // Back on Overview, one fresh read paints one set of metrics.
    await nav.getByRole('button', { name: 'Overview', exact: true }).click();
    await expect(layout.locator('.summary-metrics')).toHaveCount(1);
    await expect(layout.locator('.two-col')).toHaveCount(1);
});

// System › Appearance (#249 System S3 Task 8): the browser display defaults (`gallerySettings`, preferences.mjs) and the
// two host fields behind the one Save row, and the consumers that read them on their next visit.
const SETTINGS_URL = '/prompt_manager/settings';
const INSPECT_PATH = '/prompt_manager/images/inspect/';
const statusOf = page => page.locator('#workspace-main #settings-status');
const saveOf = page => page.locator('#workspace-main .save-row > button');
const storedSettings = page => page.evaluate(() => JSON.parse(localStorage.getItem('gallerySettings') || 'null'));
const imageCards = page => page.locator('#workspace-main article.media-card');
const panel = page => page.getByRole('complementary', { name: 'Selected image details', exact: true });

async function openAppearance(page) {
    await page.goto(`${workspace}#/system/appearance`);
    await expect(page.locator('#workspace-main .settings-layout > section.surface > h2').first()).toHaveText('Browsing & display');
    // The host rows are enabled once the server settings answered.
    await expect(page.locator('#timeout')).toBeEnabled();
}

async function saveAppearance(page, text) {
    await expect(saveOf(page)).not.toHaveAttribute('aria-disabled', 'true');
    await saveOf(page).click();
    await expect(statusOf(page)).toHaveText(text);
}

/** Set toggles (by row id) through their Toolkit switches. */
async function toggle(page, values) {
    for (const [id, checked] of Object.entries(values)) await page.locator(`#${id}`).setChecked(checked, { force: true });
}

/**
 * A request barrier: one request issued and answered after everything the page has asked for so far, so a read the
 * last action started has been seen (Playwright reports a page's requests in order).
 */
async function requestBarrier(page) {
    expect(await page.evaluate(url => fetch(url, { cache: 'no-store' }).then(response => response.ok), SETTINGS_URL)).toBe(true);
}

async function openImages(page) {
    await page.goto(`${workspace}#/images`);
    await expect(imageCards(page).first()).toBeVisible();
}

test('defaults apply to a new visit but not to the current view or a saved view', async ({ page, request }) => {
    const created = await request.post('/prompt_manager/saved-views', { data: { page: 'images', name: 'Everything',
        state: { q: '', category: '', sort: 'newest', source: 'all', folder: '', media: 'all', tags: [], favorites: false, collection: '' } } });
    expect(created.status()).toBe(201);
    const view = (await created.json()).view;
    try {
        await openImages(page);
        // The current view: the toolbar sets List.
        await page.getByRole('group', { name: 'Image layout' }).getByRole('button', { name: 'List', exact: true }).click();
        await expect(page.locator('.image-grid')).toHaveAttribute('data-layout', 'list');
        await openAppearance(page);
        // #451: a fresh browser's Default layout is Masonry.
        await expect(page.locator('#default-view')).toHaveValue('masonry');
        await page.locator('#default-view').selectOption('grid');
        await page.locator('#density').selectOption('compact');
        await expect(statusOf(page)).toHaveText('Unsaved changes.');
        await saveAppearance(page, 'Saved in this browser.');
        const stored = await storedSettings(page);
        expect([stored.workspaceLayout, stored.workspaceDensity]).toEqual(['grid', 'compact']);
        // Back in Images (the page session's last visit): the toolbar's List is the current view's own and stays; the
        // size the toolbar never changed follows the newer default (#451).
        await page.locator('#primary-nav a[data-route="images"]').click();
        await expect(page.locator('.image-grid')).toHaveAttribute('data-layout', 'list');
        await expect(page.locator('.image-grid')).toHaveAttribute('data-density', 'compact');
        // The saved view opens as before.
        await page.goto(`${workspace}#/saved/${view.id}`);
        await expect(imageCards(page).first()).toBeVisible();
        await expect(page.locator('.image-grid')).toHaveAttribute('data-layout', 'grid');
        await expect(page.locator('.image-grid')).toHaveAttribute('data-density', 'comfortable');
        // A new visit starts from the defaults.
        await openImages(page);
        await page.reload();
        await expect(imageCards(page).first()).toBeVisible();
        await expect(page.locator('.image-grid')).toHaveAttribute('data-layout', 'grid');
        await expect(page.locator('.image-grid')).toHaveAttribute('data-density', 'compact');
        await expect(page.getByRole('group', { name: 'Image layout' }).getByRole('button', { name: 'Grid', exact: true }))
            .toHaveAttribute('aria-pressed', 'true');
    } finally {
        await request.delete(`/prompt_manager/saved-views/${view.id}`);
    }
});

/** Choose a Default layout in System › Appearance and save it. */
async function saveDefaultLayout(page, value) {
    await openAppearance(page);
    await page.locator('#default-view').selectOption(value);
    await saveAppearance(page, 'Saved in this browser.');
}

const imageLayoutOf = page => page.locator('#workspace-main .image-grid');
const layoutButton = (page, name) => page.getByRole('group', { name: 'Image layout' }).getByRole('button', { name, exact: true });

test('a fresh browser opens Images in Masonry (#451)', async ({ page }) => {
    await openImages(page);
    await expect(imageLayoutOf(page)).toHaveAttribute('data-layout', 'masonry');
    await expect(layoutButton(page, 'Masonry')).toHaveAttribute('aria-pressed', 'true');
    // Nothing was stored for it: the default is the gallery settings' own.
    expect(await page.evaluate(() => localStorage.getItem('gallerySettings'))).toBeNull();
    // A stored explicit choice is kept.
    await page.evaluate(() => localStorage.setItem('gallerySettings', JSON.stringify({ workspaceLayout: 'grid' })));
    await page.reload();
    await expect(imageCards(page).first()).toBeVisible();
    await expect(imageLayoutOf(page)).toHaveAttribute('data-layout', 'grid');
});

test('a Default layout saved later reaches the next Images visit in this tab (#451)', async ({ page }) => {
    // The visit starts from Grid, then System saves Masonry without a reload.
    await page.addInitScript(() => { if (!localStorage.getItem('gallerySettings')) localStorage.setItem('gallerySettings', '{"workspaceLayout":"grid"}'); });
    await openImages(page);
    await expect(imageLayoutOf(page)).toHaveAttribute('data-layout', 'grid');
    await page.locator('#system-nav a[data-route="system"]').click();
    await expect(page.locator('#workspace-main .settings-layout')).toBeVisible();
    await page.locator('#workspace-main .section-nav').getByRole('button', { name: 'Appearance', exact: true }).click();
    await expect(page.locator('#timeout')).toBeEnabled();
    await page.locator('#default-view').selectOption('masonry');
    await saveAppearance(page, 'Saved in this browser.');
    await page.locator('#primary-nav a[data-route="images"]').click();
    await expect(imageCards(page).first()).toBeVisible();
    await expect(imageLayoutOf(page)).toHaveAttribute('data-layout', 'masonry');
    await expect(layoutButton(page, 'Masonry')).toHaveAttribute('aria-pressed', 'true');
});

test('a Default layout saved in another tab reaches this tab\'s next Images visit (#451)', async ({ page, context }) => {
    await page.addInitScript(() => { if (!localStorage.getItem('gallerySettings')) localStorage.setItem('gallerySettings', '{"workspaceLayout":"grid"}'); });
    await openImages(page);
    await expect(imageLayoutOf(page)).toHaveAttribute('data-layout', 'grid');
    const other = await context.newPage();
    try {
        await saveDefaultLayout(other, 'list');
    } finally {
        await other.close();
    }
    await page.locator('#primary-nav a[data-route="library"]').click();
    await expect(page.locator('#workspace-main .prompt-card').first()).toBeVisible();
    await page.locator('#primary-nav a[data-route="images"]').click();
    await expect(imageCards(page).first()).toBeVisible();
    await expect(imageLayoutOf(page)).toHaveAttribute('data-layout', 'list');
    // A toolbar choice afterwards is this view's own again: a return keeps it.
    await layoutButton(page, 'Masonry').click();
    await expect(imageLayoutOf(page)).toHaveAttribute('data-layout', 'masonry');
    await page.locator('#primary-nav a[data-route="library"]').click();
    await expect(page.locator('#workspace-main .prompt-card').first()).toBeVisible();
    await page.locator('#primary-nav a[data-route="images"]').click();
    await expect(imageCards(page).first()).toBeVisible();
    await expect(imageLayoutOf(page)).toHaveAttribute('data-layout', 'masonry');
});

// #451 D157: a cover crop keeps a portrait image's top; Masonry shows the whole image and keeps the centre.
test('cover crops keep the top of a portrait image; Masonry stays centred (#451)', async ({ page }) => {
    const position = locator => locator.evaluate(node => getComputedStyle(node).objectPosition);
    const portrait = page.locator('#workspace-main article.media-card .image-open img[width="1024"][height="1536"]').first();
    await openImages(page);
    await layoutButton(page, 'Grid').click();
    await expect(imageLayoutOf(page)).toHaveAttribute('data-layout', 'grid');
    await expect(portrait).toBeVisible();
    expect(await position(portrait)).toBe('50% 20%');
    await layoutButton(page, 'List').click();
    await expect(imageLayoutOf(page)).toHaveAttribute('data-layout', 'list');
    expect(await position(portrait)).toBe('50% 20%');
    // The side panel's cover (also History's media inspector).
    await portrait.locator('xpath=ancestor::article[1]').locator('.image-card-actions button').first().click();
    await expect(panel(page).locator('.detail-cover')).toBeVisible();
    expect(await position(panel(page).locator('.detail-cover'))).toBe('50% 20%');
    await layoutButton(page, 'Masonry').click();
    await expect(imageLayoutOf(page)).toHaveAttribute('data-layout', 'masonry');
    expect(await position(portrait)).toBe('50% 50%');
    // The Library's card art and a prompt history's shared gallery.
    await page.goto(`${workspace}#/library`);
    const art = page.locator('#workspace-main .prompt-card .card-art img').first();
    await expect(art).toBeVisible();
    expect(await position(art)).toBe('50% 20%');
    await page.goto(`${workspace}#/library/${page.manifest.prompts[0].positive_id}`);
    await expect(imageCards(page).first()).toBeVisible();
    expect(await position(imageCards(page).first().locator('.image-open img'))).toBe('50% 20%');
});

test('the defaults reach the Images page only; embedded galleries keep their own presentation', async ({ page }) => {
    await openAppearance(page);
    await page.locator('#default-view').selectOption('list');
    await page.locator('#density').selectOption('compact');
    await saveAppearance(page, 'Saved in this browser.');
    // The prompt history's shared gallery has no toolbar to change them, so it opens as it always did.
    await page.goto(`${workspace}#/library/${page.manifest.prompts[0].positive_id}`);
    await expect(imageCards(page).first()).toBeVisible();
    await expect(page.locator('#workspace-main .image-grid')).toHaveAttribute('data-layout', 'grid');
    await expect(page.locator('#workspace-main .image-grid')).toHaveAttribute('data-density', 'comfortable');
    // The Images page starts from them.
    await page.locator('#primary-nav a[data-route="images"]').click();
    await expect(imageCards(page).first()).toBeVisible();
    await expect(page.locator('.image-grid')).toHaveAttribute('data-layout', 'list');
    await expect(page.locator('.image-grid')).toHaveAttribute('data-density', 'compact');
});

test('metadata off shows an explicit load and loads once on request', async ({ page }) => {
    await openAppearance(page);
    await toggle(page, { 'metadata-auto': false });
    await saveAppearance(page, 'Saved in this browser.');
    const reads = [];
    page.on('request', item => { if (new URL(item.url()).pathname.startsWith(INSPECT_PATH)) reads.push(item.url()); });
    await openImages(page);
    const first = imageCards(page).first();
    await first.locator('.image-card-actions button').first().click();
    await expect(panel(page).getByRole('status')).toHaveText('Metadata loads when you ask for it.');
    const load = panel(page).getByRole('button', { name: 'Load metadata', exact: true });
    await expect(load).toBeVisible();
    expect(reads).toEqual([]);
    await load.click();
    await expect(panel(page).locator('.panel-heading h2')).toBeVisible();
    expect(reads).toHaveLength(1);
    // The same file again reads nothing more (the cache is on).
    await first.locator('.image-card-actions button').first().click();
    await expect(panel(page).locator('.panel-heading h2')).toBeVisible();
    await requestBarrier(page);
    expect(reads).toHaveLength(1);
    // The viewer's details use the same inspector: another file waits there too, and loads on request.
    await imageCards(page).nth(1).locator('.image-open').click();
    const viewerDetails = page.locator('#viewer-details');
    await expect(viewerDetails.getByRole('status')).toHaveText('Metadata loads when you ask for it.');
    expect(reads).toHaveLength(1);
    await viewerDetails.getByRole('button', { name: 'Load metadata', exact: true }).click();
    await expect(viewerDetails.locator('.panel-heading h2')).toBeVisible();
    expect(reads).toHaveLength(2);
});

test('cache off rereads the current selection; on reuses the one current record', async ({ page }) => {
    const reads = [];
    page.on('request', item => { if (new URL(item.url()).pathname.startsWith(INSPECT_PATH)) reads.push(item.url()); });
    for (const cache of [true, false]) {
        if (!cache) {
            await openAppearance(page);
            await toggle(page, { 'metadata-cache': false });
            await saveAppearance(page, 'Saved in this browser.');
            await page.reload();
        }
        await openImages(page);
        const info = imageCards(page).first().locator('.image-card-actions button').first();
        await info.click();
        await expect(panel(page).locator('.panel-heading h2')).toBeVisible();
        await expect.poll(() => reads.length).toBeGreaterThan(0);
        await requestBarrier(page);
        reads.length = 0;
        // Inspect the selected file twice more.
        for (const _ of [1, 2]) {
            const answered = cache ? null : page.waitForResponse(response => new URL(response.url()).pathname.startsWith(INSPECT_PATH));
            await info.click();
            if (answered) await answered;
            await expect(panel(page).locator('.panel-heading h2')).toBeVisible();
        }
        await requestBarrier(page);
        expect(reads, `cache ${cache}`).toHaveLength(cache ? 0 : 2);
    }
});

test('paths hidden only changes display', async ({ page }) => {
    const rows = async () => page.locator('#viewer-details details').filter({ hasText: 'File & provenance' }).locator('dt')
        .evaluateAll(terms => terms.map(term => term.textContent));
    const openViewer = async () => {
        await openImages(page);
        await imageCards(page).first().locator('.image-open').click();
        const provenance = page.locator('#viewer-details details').filter({ hasText: 'File & provenance' });
        await provenance.locator('summary').click();
        await expect(provenance.locator('dl')).toBeVisible();
        return provenance;
    };
    let provenance = await openViewer();
    expect(await rows()).toContain('Path');
    const before = { show: await provenance.getByRole('button', { name: /^Show .* in folder$/ }).count(),
        download: await provenance.getByRole('link', { name: 'Download original', exact: true }).getAttribute('href') };
    await page.keyboard.press('Escape');
    await openAppearance(page);
    await toggle(page, { paths: false });
    await saveAppearance(page, 'Saved in this browser.');
    provenance = await openViewer();
    const shown = await rows();
    expect(shown).not.toContain('Path');
    expect(shown).toEqual(expect.arrayContaining(['Source', 'Size']));
    // Everything else is as it was: the folder action and the original download stay.
    expect(await provenance.getByRole('button', { name: /^Show .* in folder$/ }).count()).toBe(before.show);
    await expect(provenance.getByRole('link', { name: 'Download original', exact: true })).toHaveAttribute('href', before.download);
});

test('video defaults reach the viewer', async ({ page }) => {
    await openAppearance(page);
    await toggle(page, { 'video-auto': true, 'video-muted': false, 'video-loop': false });
    await saveAppearance(page, 'Saved in this browser.');
    expect(await page.evaluate(async () => (await import('/prompt_manager/js/workspace/media-viewer.mjs')).readViewerPreferences()))
        .toEqual({ videoAutoplay: true, videoMute: false, videoLoop: false });
    const stored = await storedSettings(page);
    expect([stored.videoAutoplay, stored.videoMute, stored.videoLoop]).toEqual([true, false, false]);
});

test('host fields round-trip through /settings with revision', async ({ page, request }) => {
    const posts = [];
    page.on('request', item => { if (item.method() === 'POST' && new URL(item.url()).pathname === SETTINGS_URL) posts.push(item.postDataJSON()); });
    const before = await serverJson(request, SETTINGS_URL);
    await openAppearance(page);
    await expect(page.locator('#open-mode')).toHaveValue(before.settings.webui_display_mode);
    await expect(page.locator('#timeout')).toHaveValue(String(before.settings.result_timeout));
    // Only whole seconds from 0 to 300: anything else is highlighted and never sent.
    for (const bad of ['301', '-1', '2.5']) {
        await page.locator('#timeout').fill(bad);
        await page.locator('#timeout').press('Tab');
        await expect(page.locator('#timeout')).toHaveAttribute('aria-invalid', 'true');
        await expect(statusOf(page)).toHaveText('Some changes were not saved. Review the highlighted settings.');
        await expect(saveOf(page)).toHaveAttribute('aria-disabled', 'true');
    }
    await page.locator('#timeout').fill('0');
    await page.locator('#timeout').press('Tab');
    await expect(page.locator('#timeout')).not.toHaveAttribute('aria-invalid');
    await page.locator('#open-mode').selectOption('popup');
    await saveAppearance(page, 'Saved to the server configuration.');
    expect(posts).toEqual([{ webui_display_mode: 'popup', result_timeout: 0, expected_revision: before.revision }]);
    const after = await serverJson(request, SETTINGS_URL);
    expect([after.settings.webui_display_mode, after.settings.result_timeout]).toEqual(['popup', 0]);
    expect(after.revision).not.toBe(before.revision);
    // No browser key was written for a host field.
    expect(await storedSettings(page)).toBeNull();
});

test('server settings that cannot be read say so in their rows', async ({ page }) => {
    await page.route(url => url.pathname === SETTINGS_URL, route => (route.request().method() === 'GET'
        ? route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ success: false, error: 'Injected failure' }) })
        : route.continue()));
    await page.goto(`${workspace}#/system/appearance`);
    // #317: the Thumbnails rows are server settings too.
    for (const id of ['open-mode', 'timeout', 'thumbnail-format', 'thumbnail-quality', 'thumbnail-edge']) {
        const row = page.locator('#workspace-main .setting-row').filter({ has: page.locator(`#${id}`) });
        await expect(row.locator('p.small').first()).toHaveText('Server settings could not be read.');
        await expect(page.locator(`#${id}`)).toBeDisabled();
    }
    // The browser rows are this browser's, and work as before.
    await expect(page.locator('#default-view')).toBeEnabled();
    await expect(page.locator('#workspace-main .setting-row p.small', { hasText: 'Server settings could not be read.' })).toHaveCount(5);
    await expect(page.locator('#workspace-main section.surface').first().locator('.setting-row p.small')).toHaveCount(2);
});

test('stale revision shows the conflict status', async ({ page, request }) => {
    await openAppearance(page);
    // Another page saves after this one read the settings.
    const current = await serverJson(request, SETTINGS_URL);
    await expect(await request.post(SETTINGS_URL, { data: { result_timeout: 42, expected_revision: current.revision } })).toBeOK();
    await page.locator('#open-mode').selectOption('popup');
    await saveAppearance(page, 'Settings changed elsewhere. Reload this section to see them.');
    await expect(page.locator('#open-mode')).toHaveAttribute('aria-invalid', 'true');
    // The draft is kept and the section now shows the other page's value.
    await expect(page.locator('#open-mode')).toHaveValue('popup');
    await expect(page.locator('#timeout')).toHaveValue('42');
    expect((await serverJson(request, SETTINGS_URL)).settings.webui_display_mode).toBe('newtab');
    // With the reloaded revision, Save succeeds.
    await saveAppearance(page, 'Saved to the server configuration.');
    expect((await serverJson(request, SETTINGS_URL)).settings.webui_display_mode).toBe('popup');
});

test('reload keeps saved values; a new tab reads them', async ({ page, context }) => {
    await openAppearance(page);
    await page.locator('#image-quality').selectOption('high');
    await toggle(page, { 'card-details': false });
    await page.locator('#open-mode').selectOption('popup');
    await saveAppearance(page, 'Saved in this browser. Saved to the server configuration.');
    await page.reload();
    await expect(page.locator('#timeout')).toBeEnabled();
    await expect(page.locator('#image-quality')).toHaveValue('high');
    await expect(page.locator('#card-details')).not.toBeChecked();
    await expect(page.locator('#open-mode')).toHaveValue('popup');
    const other = await context.newPage();
    await openAppearance(other);
    await expect(other.locator('#image-quality')).toHaveValue('high');
    await expect(other.locator('#card-details')).not.toBeChecked();
    // A change saved in one tab reaches the other's clean controls; a draft it overtakes is flagged and kept.
    await other.locator('#density').selectOption('large');
    await page.locator('#density').selectOption('compact');
    await saveAppearance(page, 'Saved in this browser.');
    await expect(other.locator('#density')).toHaveValue('large');
    await expect(other.locator('#settings-status')).toHaveText('Settings changed elsewhere. Reload this section to see them.');
    await expect(other.locator('#density')).toHaveAttribute('aria-invalid', 'true');
    await page.locator('#image-quality').selectOption('low');
    await saveAppearance(page, 'Saved in this browser.');
    await expect(other.locator('#image-quality')).toHaveValue('low');
    await other.close();
});

test('a change another tab saved while System was closed is a conflict on return, never saved over silently', async ({ page, context }) => {
    await openAppearance(page);
    await page.locator('#density').selectOption('compact');
    await expect(statusOf(page)).toHaveText('Unsaved changes.');
    // Leave System for Images: the draft stays for the page session, but System no longer listens.
    await page.locator('#primary-nav a[data-route="images"]').click();
    await expect(imageCards(page).first()).toBeVisible();
    const other = await context.newPage();
    await openAppearance(other);
    await other.locator('#density').selectOption('large');
    await saveAppearance(other, 'Saved in this browser.');
    await other.close();
    // Back in System: the draft is kept and flagged, and nothing was written over the other tab's value.
    await page.evaluate(() => { location.hash = '#/system/appearance'; });
    await expect(page.locator('#workspace-main .settings-layout > section.surface > h2').first()).toHaveText('Browsing & display');
    await expect(statusOf(page)).toHaveText('Settings changed elsewhere. Reload this section to see them.');
    await expect(page.locator('#density')).toHaveAttribute('aria-invalid', 'true');
    await expect(page.locator('#density')).toHaveValue('compact');
    expect((await storedSettings(page)).workspaceDensity).toBe('large');
    // Saving after the conflict was shown is an explicit choice: it wins.
    await saveAppearance(page, 'Saved in this browser.');
    expect((await storedSettings(page)).workspaceDensity).toBe('compact');
    await expect(page.locator('#density')).not.toHaveAttribute('aria-invalid');
});

test('a browser that refuses storage is saved for this tab only, never "Saved"', async ({ page }) => {
    await openAppearance(page);
    await page.evaluate(() => {
        Storage.prototype.setItem = function setItem() { throw new DOMException('Quota exceeded', 'QuotaExceededError'); };
    });
    await page.locator('#default-view').selectOption('list');
    await saveAppearance(page, 'Saved for this tab only; browser storage is unavailable.');
    expect(await storedSettings(page)).toBeNull();
    // This tab's next Images visit still uses it.
    await page.locator('#primary-nav a[data-route="images"]').click();
    await expect(imageCards(page).first()).toBeVisible();
    await expect(page.locator('.image-grid')).toHaveAttribute('data-layout', 'list');
});

test('added rows reach their consumers', async ({ page }) => {
    await openImages(page);
    await expect(imageCards(page).first().locator('.media-info h3')).toBeVisible();
    await openAppearance(page);
    await toggle(page, { 'card-details': false, 'viewer-details': true });
    await saveAppearance(page, 'Saved in this browser.');
    await page.reload();
    await openImages(page);
    // Show image details on cards off: no title or file line; Info and Tag stay.
    const card = imageCards(page).first();
    await expect(card.locator('.media-info h3')).toBeHidden();
    await expect(card.locator('.image-card-actions button')).toHaveCount(2);
    // Collapse viewer details by default: the viewer opens with its details hidden behind one toggle.
    await card.locator('.image-open').click();
    const details = page.locator('#viewer-details');
    await expect(page.locator('#viewer-dialog')).toBeVisible();
    await expect(details).toBeHidden();
    const show = page.locator('#viewer-toolbar').getByRole('button', { name: 'Show details', exact: true });
    await expect(show).toHaveAttribute('aria-expanded', 'false');
    await show.click();
    await expect(details).toBeVisible();
    await expect(page.locator('#viewer-toolbar').getByRole('button', { name: 'Hide details', exact: true })).toHaveAttribute('aria-expanded', 'true');
});

test('reset restores defaults without touching other keys', async ({ page }) => {
    await page.goto(`${workspace}#/system/appearance`);
    await page.evaluate(() => localStorage.setItem('gallerySettings', JSON.stringify({ gridColumns: 5, defaultLimit: 200,
        thumbnailPromptDismissed: 'kept', workspaceLayout: 'list', videoMute: false, showImageInfo: false })));
    await page.reload();
    await expect(page.locator('#timeout')).toBeEnabled();
    await expect(page.locator('#default-view')).toHaveValue('list');
    const reset = page.locator('#workspace-main .operation').filter({ hasText: 'Reset display defaults' }).getByRole('button', { name: 'Reset', exact: true });
    await reset.click();
    // #451 D157: the default layout is Masonry.
    await expect(page.locator('#default-view')).toHaveValue('masonry');
    await expect(page.locator('#video-muted')).toBeChecked();
    await expect(statusOf(page)).toHaveText('Unsaved changes.');
    await saveAppearance(page, 'Saved in this browser.');
    const stored = await storedSettings(page);
    expect(stored).toEqual({ gridColumns: 5, defaultLimit: 200, thumbnailPromptDismissed: 'kept', workspaceLayout: 'masonry',
        videoMute: true, showImageInfo: true });
});

// TESTING.md "Legacy System capability coverage" (#249 System S12, #329): each `covered` row's native control is on its
// System section, found by role and accessible name (a `term` by its text, a disclosure by its summary). Collapsed
// `details` are opened first, so a control inside one counts once it is shown. tests/vendor/legacy-coverage.test.mjs
// checks the rest of each row.
test('every covered legacy System capability has its native control', async ({ page, request }) => {
    // Download is offered for a model that is not on disk ("Download again" otherwise): the selected default's file is
    // missing here.
    await expect(await request.post('/__test__/tagging/models', { data: { unavailable: ['wd14-swinv2'] } })).toBeOK();
    try {
        await findControls(page);
    } finally {
        await request.post('/__test__/tagging/reset', { data: {} });
    }
});

async function findControls(page) {
    const covered = readCoverageTable().filter(row => row.Status === 'covered')
        .map(row => ({ capability: row.Capability, ...parseControl(row['Native control']) }));
    // A covered row without a control is one of the recorded exemptions, never silently skipped.
    expect(covered.filter(row => !row.section).map(row => row.capability)).toEqual(Object.keys(CONTROL_EXEMPTIONS));
    const controls = covered.filter(row => row.section);
    expect(controls.length).toBeGreaterThan(40);
    const sections = [...new Set(controls.map(row => row.section))];
    for (const section of sections) {
        await page.goto(`${workspace}#/system/${section}`);
        const main = page.locator('#workspace-main');
        await expect(layoutOf(page).locator(':scope > *').first()).toBeVisible();
        for (const details of await main.locator('details:not([open]) > summary').all()) await details.click();
        for (const { capability, role, name, row } of controls.filter(item => item.section === section)) {
            // A name several rows share is looked up inside the row the table names.
            const within = row ? main.locator('.operation').filter({ has: page.locator(`h3:text-is("${row}")`) }) : main;
            const exact = new RegExp(`^${name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`);
            // A `dt` (term) and a `details` disclosure (group) have no name from content: the term's text, the summary's.
            const control = role === 'term' ? within.getByRole('term').filter({ hasText: exact })
                : role === 'group' ? within.locator('details').filter({ has: page.locator(':scope > summary', { hasText: exact }) })
                    .or(within.getByRole('group', { name, exact: true }))
                    : within.getByRole(role, { name, exact: true });
            await expect(control.first(), `${capability}: ${section} · ${role} · "${name}"${row ? ` in "${row}"` : ''}`).toBeVisible();
        }
    }
}
