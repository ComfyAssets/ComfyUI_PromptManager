import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';
import { seedConcept } from './concept-inventory.mjs';

// System › Diagnostics & logs (#249 System S12, #329) on the concept-shaped fixture, with the logger writing into this
// server's own root (workspace_diagnostics_fixture.py): the meta-grid's live values, Run checks, the report, Inspect and
// the test link (D110); the log viewer, statistics, downloads and runtime drafts (D109); and the reviewed rotated-log
// cleanup (D100) and Clear all logs, which delete only inside that temporary log folder.
const workspace = '/prompt_manager/workspace';
const REPORT_URL = '/prompt_manager/system/diagnostics';
const LOGS_URL = '/prompt_manager/logs';
const CONFIG_URL = '/prompt_manager/logs/config';
const PREVIEW_URL = '/prompt_manager/maintenance/preview';
const APPLY_URL = '/prompt_manager/maintenance/apply';
const TEST_LINK_URL = '/prompt_manager/diagnostics/test-link';
const CHANGED = 'The log files changed since the review; nothing was deleted. Review again.';
const KEY = 'sk-s12-browser-SECRET-0123456789';
const LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'];

test.beforeEach(async ({ page, request, browserName }) => {
    await startCoverage(page, browserName);
    await seedConcept(request);
    await expect(await request.post('/__test__/system/reset')).toBeOK();
    await expect(await request.post('/__test__/integrations/reset')).toBeOK();
    await expect(await request.post('/__test__/jobs/restart')).toBeOK();
    await expect(await request.post('/__test__/logs/reset')).toBeOK();
    page.systemErrors = [];
    page.on('pageerror', error => page.systemErrors.push(error.message));
});

test.afterEach(async ({ page, request, browserName }) => {
    await saveCoverage(page, browserName);
    await request.post('/__test__/release');
    await request.post('/__test__/jobs/restart');
    await request.post('/__test__/logs/reset');
    await request.post('/__test__/integrations/reset');
    await request.post('/__test__/system/reset');
    expect(page.systemErrors).toEqual([]);
});

const toast = page => page.locator('#workspace-toast');
const layoutOf = page => page.locator('#workspace-main .settings-layout');
const dialog = page => page.locator('dialog#modal[open]');
const surfaceOf = (page, title) => layoutOf(page).locator(':scope > section.surface').filter({
    has: page.getByRole('heading', { level: 2, name: title, exact: true }) });
const cell = (page, term) => surfaceOf(page, 'Diagnostics').locator('.meta-grid > div').filter({
    has: page.locator(`dt:text-is("${term}")`) }).locator('dd');
const lines = page => page.locator('#log-lines > code');
const operation = (page, title) => layoutOf(page).locator('.operation').filter({ has: page.locator(`h3:text-is("${title}")`) });
const settingOf = (page, label) => layoutOf(page).locator('.setting-row').filter({ has: page.locator(`label:text-is("${label}")`) });
const status = page => page.locator('#settings-status');
const save = page => page.getByRole('button', { name: 'Save preferences', exact: true });

async function serverJson(request, url) {
    const response = await request.get(url);
    await expect(response).toBeOK();
    return response.json();
}

async function seedLogs(request, data = {}) {
    await expect(await request.post('/__test__/logs/seed', { data })).toBeOK();
}

const names = async request => (await serverJson(request, '/__test__/logs/state')).entries.map(entry => entry.name);

async function openDiagnostics(page) {
    if (page.url().endsWith('#/system/diagnostics')) await page.reload();
    else await page.goto(`${workspace}#/system/diagnostics`);
    await expect(layoutOf(page).locator(':scope > section.surface')).toHaveCount(2);
    await expect(lines(page)).not.toHaveText('Loading…');
    await expect(cell(page, 'Database')).not.toHaveText('Loading…');
    // The renderer line settles first: its text can move the rows below.
    await expect(cell(page, 'Host renderers')).not.toHaveText('Loading…');
    await expect(page.locator('#log-stats')).not.toHaveText('Loading…');
    return layoutOf(page);
}

async function openFiles(page) {
    const details = surfaceOf(page, 'Logs').locator('details');
    if (!(await details.evaluate(node => node.open))) await details.locator('summary').click();
    await expect(details.locator(':scope > p.small').first()).not.toHaveText('Loading…');
    return details;
}

/** The levels of the viewer's lines, in order. */
const shownLevels = async page => (await lines(page).textContent()).split('\n').filter(Boolean)
    .map(line => line.split(/ +/)[1]);

async function ended(request, id) {
    await expect.poll(async () => (await serverJson(request, `/prompt_manager/jobs/${id}`)).job.state).not.toBe('running');
    return (await serverJson(request, `/prompt_manager/jobs/${id}`)).job;
}

/** A second page of this origin that serves a fake ComfyUI window on the send bridge. */
async function fakeComfyWindow(context, renderer) {
    const host = await context.newPage();
    await host.goto(`${workspace}#/overview`);
    await host.evaluate(async drawn => {
        const { createBridgeHost } = await import('/prompt_manager/js/shared/toolkit/prompt-bridge.js');
        window.fakeHost = createBridgeHost({ namespace: '/', origin: location.origin, label: 'PromptManager — ComfyUI',
            adapter: { listTargets: () => [{ id: '7', label: 'Prompt Manager', type: 'PromptManager', roles: ['positive'],
                renderer: drawn }], preview() { throw new Error('unused'); }, apply() { throw new Error('unused'); } } });
    }, renderer);
    return host;
}

async function readBlob(download) {
    const chunks = await (await download.createReadStream()).toArray();
    return Buffer.concat(chunks).toString('utf8');
}

// ── Task 4: the Diagnostics surface ─────────────────────────────────────────────────────────────────────────────────

test('meta-grid shows real database and source status, the pinned Viewer.js version and the host renderers',
    async ({ page, request, context }) => {
        const mutations = [];
        page.on('request', item => { if (!['GET', 'HEAD', 'OPTIONS'].includes(item.method())) mutations.push(item.url()); });
        await openDiagnostics(page);
        const report = await serverJson(request, REPORT_URL);
        const surface = surfaceOf(page, 'Diagnostics');
        await expect(surface.locator(':scope > .panel-heading > h2')).toHaveText('Diagnostics');
        await expect(surface.locator(':scope > .panel-heading > button')).toHaveText('Run checks');
        await expect(surface.locator('.meta-grid dt')).toHaveText(['Database', 'Sources', 'Viewer.js', 'Host renderers']);
        const images = report.checks.database.image_count;
        await expect(cell(page, 'Database')).toHaveText(new RegExp(`^Readable · [\\d.]+ (bytes|KB|MB|GB) · ${images.toLocaleString('en-US')} images?$`));
        // The size is the database's real size (`database_size_bytes`), never a missing key read as 0.
        expect(report.database_info.database_size_bytes).toBeGreaterThan(0);
        await expect(cell(page, 'Database')).not.toContainText('· 0 bytes ·');
        const { configured, watching } = report.sources;
        await expect(cell(page, 'Sources')).toHaveText(configured
            ? `${configured} source${configured === 1 ? '' : 's'} · ${watching} watching`
            : `ComfyUI output folder · ${watching ? 'watching' : 'manual scan'}`);
        await expect(cell(page, 'Viewer.js')).toHaveText('1.15.2 · pinned vendor');
        await expect(cell(page, 'Host renderers')).toHaveText('No ComfyUI window is open in this browser, so its renderer is not known.');
        // A ComfyUI window with a Classic node: the line Integrations shows (D106).
        const host = await fakeComfyWindow(context, 'classic');
        await openDiagnostics(page);
        await expect(cell(page, 'Host renderers')).toHaveText('Detected: Classic (1 ComfyUI window).');
        await host.close();
        // A refused read names the running job (D57's naming).
        await page.route(`**${REPORT_URL}`, route => route.fulfill({ status: 409, contentType: 'application/json',
            body: JSON.stringify({ success: false, code: 'busy', error: 'busy', running: { id: 'a'.repeat(32), title: 'Restore' } }) }));
        await openDiagnostics(page);
        await expect(cell(page, 'Database')).toHaveText('Busy: Restore is running.');
        await page.unroute(`**${REPORT_URL}`);
        await expect(await request.post('/__test__/system/fail', { data: { path: REPORT_URL, times: 1 } })).toBeOK();
        await openDiagnostics(page);
        await expect(cell(page, 'Sources')).toHaveText('Could not be read.');
        expect(mutations).toEqual([]);
    });

test('Run checks opens the shared Health report', async ({ page, request }) => {
    const before = await serverJson(request, '/__test__/system/digest');
    await openDiagnostics(page);
    const run = surfaceOf(page, 'Diagnostics').getByRole('button', { name: 'Run checks', exact: true });
    await run.click();
    await expect(dialog(page).locator('#modal-title')).toHaveText('Health report');
    await expect(run).toHaveText('Run checks');
    await dialog(page).getByRole('button', { name: 'Close', exact: true }).click();
    await expect(dialog(page)).toHaveCount(0);
    await expect(run).toBeFocused();
    // The same report Overview reads: Overview's metric names it.
    expect((await serverJson(request, '/prompt_manager/system/health')).report).toBeTruthy();
    expect((await serverJson(request, '/__test__/system/digest')).rows).toEqual(before.rows);
});

test('report download goes through the api client and holds no secret or absolute path', async ({ page, request }) => {
    const { revision } = await serverJson(request, '/prompt_manager/settings');
    await expect(await request.post('/prompt_manager/lora/enable', { data: { enabled: false, trigger_words_enabled: false, path: '',
        civitai_api_key: KEY, expected_revision: revision } })).toBeOK();
    await openDiagnostics(page);
    const reads = [];
    page.on('request', item => { if (item.url().endsWith(REPORT_URL)) reads.push(item.url()); });
    const download = page.waitForEvent('download');
    await surfaceOf(page, 'Diagnostics').getByRole('button', { name: 'Download diagnostic report', exact: true }).click();
    const file = await download;
    expect(file.suggestedFilename()).toMatch(/^prompt-manager-diagnostics-\d{8}-\d{4}\.json$/);
    await expect(toast(page)).toHaveText('Diagnostic report downloaded.');
    expect(reads).toHaveLength(1);
    const text = await readBlob(file);
    const report = JSON.parse(text);
    expect(report.host_renderers).toBe('No ComfyUI window is open in this browser, so its renderer is not known.');
    expect(report.config.integrations.has_civitai_api_key).toBe(true);
    expect(report.redacted).toBe(0);
    expect(report.viewerjs).toBe('1.15.2');
    expect(Object.keys(report.checks)).toEqual(['database', 'dependencies', 'comfyui_output', 'image_monitor']);
    expect(text).not.toContain(KEY);
    expect(text).not.toContain('pm-browser-');
    const strings = [];
    const walk = value => {
        if (typeof value === 'string') strings.push(value);
        else if (value && typeof value === 'object') Object.values(value).forEach(walk);
    };
    walk(report);
    expect(strings.filter(value => /^(\/|[A-Za-z]:[\\/]|\\\\)/.test(value))).toEqual([]);
    // A refused report names the job and saves nothing.
    await page.route(`**${REPORT_URL}`, route => route.fulfill({ status: 409, contentType: 'application/json',
        body: JSON.stringify({ success: false, code: 'busy', error: 'busy', running: { id: 'a'.repeat(32), title: 'Restore' } }) }));
    let saved = false;
    page.on('download', () => { saved = true; });
    await surfaceOf(page, 'Diagnostics').getByRole('button', { name: 'Download diagnostic report', exact: true }).click();
    await expect(toast(page)).toHaveText('Busy: Restore is running. Try again when it ends.');
    await page.unroute(`**${REPORT_URL}`);
    await page.route(`**${REPORT_URL}`, route => route.fulfill({ status: 500, contentType: 'application/json',
        body: JSON.stringify({ success: false, error: 'The diagnostic report could not be built.' }) }));
    await surfaceOf(page, 'Diagnostics').getByRole('button', { name: 'Download diagnostic report', exact: true }).click();
    await expect(toast(page)).toHaveText('The diagnostic report could not be downloaded. Nothing was saved to this device.');
    expect(saved).toBe(false);
});

test('Inspect opens the Inspect a file page and writes nothing', async ({ page, request }) => {
    const before = await serverJson(request, '/__test__/system/digest');
    const mutations = [];
    page.on('request', item => { if (!['GET', 'HEAD', 'OPTIONS'].includes(item.method())) mutations.push(item.url()); });
    await openDiagnostics(page);
    const row = operation(page, 'Image-link diagnostics');
    await expect(row.locator('p')).toHaveText('Check a file-to-prompt association without changing it.');
    await row.getByRole('button', { name: 'Inspect', exact: true }).click();
    await expect(page).toHaveURL(`${workspace}#/inspect`);
    await expect(page.locator('#workspace-main h1')).toHaveText('Inspect a file');
    expect(mutations).toEqual([]);
    expect(await serverJson(request, '/__test__/system/digest')).toEqual(before);
});

test('Create test link confirms first and names the prompt', async ({ page, request }) => {
    const [prompt] = (await serverJson(request, '/prompt_manager/recent?limit=1')).results;
    const posts = [];
    page.on('request', item => { if (item.url().endsWith(TEST_LINK_URL)) posts.push(item.postDataJSON()); });
    await openDiagnostics(page);
    const row = operation(page, 'Test image link');
    await expect(row.locator('p')).toHaveText('Write a placeholder image record to check that linking works.');
    const create = row.getByRole('button', { name: 'Create test link…', exact: true });
    await expect(create).not.toHaveAttribute('aria-disabled', 'true');
    await create.click();
    await expect(dialog(page).locator('#modal-title')).toHaveText('Create test link');
    await expect(dialog(page).locator('#modal-body > p')).toHaveText(new RegExp('^Writes a placeholder image record '
        + `\\(prompt_manager_test_link\\.png\\) linked to prompt #${prompt.id} “.+”\\. Maintenance › Missing-file links removes it later\\.$`));
    await dialog(page).getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(dialog(page)).toHaveCount(0);
    expect(posts).toEqual([]);
    await create.click();
    await dialog(page).getByRole('button', { name: 'Create', exact: true }).click();
    await expect(toast(page)).toHaveText(/^Test link created \(image record \d+\)\.$/);
    expect(posts).toEqual([{ prompt_id: prompt.id }]);
    // A failed write says nothing was changed.
    await page.route(`**${TEST_LINK_URL}`, route => route.fulfill({ status: 500, contentType: 'application/json',
        body: JSON.stringify({ success: false, error: 'Test link failed' }) }));
    await create.click();
    await dialog(page).getByRole('button', { name: 'Create', exact: true }).click();
    await expect(toast(page)).toHaveText('The test link could not be created. Nothing was changed.');
    // With no prompt to link, the button is unavailable and says why.
    await page.route('**/prompt_manager/recent?limit=1', route => route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ success: true, results: [] }) }));
    await openDiagnostics(page);
    await expect(operation(page, 'Test image link').locator('p'))
        .toHaveText('Write a placeholder image record to check that linking works. Add a prompt first.');
    await expect(operation(page, 'Test image link').getByRole('button')).toHaveAttribute('aria-disabled', 'true');
});

// ── Task 5: the log viewer, statistics and runtime settings ─────────────────────────────────────────────────────────

test('level filter is minimum-level over all six choices', async ({ page, request }) => {
    await seedLogs(request);
    const urls = [];
    page.on('request', item => { if (new URL(item.url()).pathname === LOGS_URL) urls.push(new URL(item.url()).search); });
    await openDiagnostics(page);
    const level = page.locator('#log-level');
    await expect(level.locator('option')).toHaveText(['All levels', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']);
    await expect(page.locator('label[for="log-level"]')).toHaveText('Level');
    // All levels: the concept's four lines first, newest first, in its format.
    await expect(lines(page)).toContainText('09:42:18 INFO  Scan completed: 12 enriched, 0 duplicate links\n'
        + '09:42:18 WARNING 3 images have ambiguous prompt metadata\n09:39:04 INFO  Database backup verified\n'
        + '09:38:52 INFO  Source monitor started: output');
    for (const line of (await lines(page).textContent()).split('\n')) expect(line).toMatch(/^\d\d:\d\d:\d\d [A-Z]+ +\S/);
    expect(urls.at(-1)).toBe('?limit=100');
    for (const minimum of LEVELS) {
        // Wait for this level's answer: the previous level's lines also hold the CRITICAL line, so seeing it proved
        // nothing, and under load the filtered answer painted after the check (#501 merge queue, firefox).
        const answered = page.waitForResponse(item => new URL(item.url()).pathname === LOGS_URL
            && new URL(item.url()).search === `?limit=100&level=${minimum}`);
        await level.selectOption(minimum);
        await answered;
        await expect.poll(() => urls.at(-1)).toBe(`?limit=100&level=${minimum}`);
        await expect(lines(page)).toContainText('CRITICAL The database could not be opened');
        await expect.poll(async () => {
            const shown = await shownLevels(page);
            return shown.every(name => LEVELS.indexOf(name) >= LEVELS.indexOf(minimum)) || `${minimum}: ${shown}`;
        }).toBe(true);
        if (minimum === 'DEBUG') await expect(lines(page)).toContainText('DEBUG Thumbnail cache lookup');
        if (minimum === 'WARNING') {
            await expect(lines(page)).not.toContainText('Database backup verified');
            await expect(lines(page)).toContainText('ERROR A source folder could not be read');
        }
    }
    await level.selectOption('');
    await expect.poll(() => urls.at(-1)).toBe('?limit=100');
});

test('Lines accepts 1 to 500 and sends nothing when invalid', async ({ page, request }) => {
    await seedLogs(request, { filler: 30 });
    const urls = [];
    page.on('request', item => { if (new URL(item.url()).pathname === LOGS_URL) urls.push(new URL(item.url()).search); });
    await openDiagnostics(page);
    const limit = page.locator('#log-limit');
    await expect(page.locator('label[for="log-limit"]')).toHaveText('Lines');
    await expect(limit).toHaveValue('100');
    expect(await limit.evaluate(node => [node.type, node.min, node.max, node.title]))
        .toEqual(['number', '1', '500', 'Lines to show: a whole number from 1 to 500.']);
    await limit.fill('20');
    await limit.dispatchEvent('change');
    await expect.poll(() => urls.at(-1)).toBe('?limit=20');
    await expect.poll(async () => (await lines(page).textContent()).split('\n').length).toBe(20);
    const sent = urls.length;
    for (const value of ['0', '501', '2.5', '']) {
        await limit.fill(value);
        await limit.dispatchEvent('change');
        await expect(limit).toHaveAttribute('aria-invalid', 'true');
    }
    // A Level change while Lines is invalid sends nothing either.
    await page.locator('#log-level').selectOption('WARNING');
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    expect(urls.length).toBe(sent);
    await limit.fill('500');
    await limit.dispatchEvent('change');
    await expect(limit).not.toHaveAttribute('aria-invalid');
    await expect.poll(() => urls.at(-1)).toBe('?limit=500&level=WARNING');
});

test('logs refresh handles empty, error and stale answers', async ({ page, request }) => {
    await seedLogs(request);
    await openDiagnostics(page);
    const level = page.locator('#log-level');
    const refresh = surfaceOf(page, 'Logs').getByRole('button', { name: 'Refresh', exact: true });
    // Empty: nothing at this level once the buffer is cleared.
    await expect(await request.post('/__test__/logs/reset')).toBeOK();
    await level.selectOption('CRITICAL');
    await expect(lines(page)).toHaveText('No log entries at this level.');
    // Error.
    await page.route(`**${LOGS_URL}?*`, route => route.fulfill({ status: 500, contentType: 'application/json',
        body: JSON.stringify({ success: false, error: 'boom', logs: [] }) }));
    await refresh.click();
    await expect(lines(page)).toHaveText('The log could not be read.');
    await page.unroute(`**${LOGS_URL}?*`);
    // Stale: an older answer that arrives after a newer request never paints.
    await seedLogs(request);
    await expect(await request.post('/__test__/hold', { data: { path: LOGS_URL } })).toBeOK();
    await level.selectOption('DEBUG');
    await expect.poll(async () => (await serverJson(request, '/__test__/hold')).held[LOGS_URL] ?? 0).toBe(1);
    await level.selectOption('ERROR');
    await expect.poll(async () => (await serverJson(request, '/__test__/hold')).held[LOGS_URL] ?? 0).toBe(2);
    await expect(await request.post('/__test__/release')).toBeOK();
    await expect(lines(page)).toContainText('CRITICAL The database could not be opened');
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    expect((await shownLevels(page)).every(name => ['ERROR', 'CRITICAL'].includes(name))).toBe(true);
});

test('log statistics show entries, errors, warnings, files and size', async ({ page, request }) => {
    await seedLogs(request, { rotated: [2048, 1024], other: true, link: true, active: 512 });
    await openDiagnostics(page);
    const stats = page.getByRole('status', { name: 'Log statistics', exact: true });
    await expect(stats).toHaveAttribute('id', 'log-stats');
    const text = await stats.textContent();
    const match = /^([\d,]+) entr(?:y|ies) in memory · (\d+) errors? · (\d+) warnings? · (\d+) files? · (.+) on disk$/.exec(text);
    expect(match, text).not.toBeNull();
    const [, entries, errors, warnings, files, size] = match;
    expect(Number(entries.replace(/,/g, ''))).toBeGreaterThanOrEqual(7);
    expect(Number(errors)).toBeGreaterThanOrEqual(2);
    expect(Number(warnings)).toBeGreaterThanOrEqual(1);
    // The active log, two rotated copies and the .bak; the link is not counted (Rule R).
    expect(Number(files)).toBe(4);
    expect(size).toMatch(/^[\d.]+ (bytes|KB|MB)$/);
});

test('Download log saves the active log', async ({ page, request }) => {
    await seedLogs(request, { active: 300 });
    await openDiagnostics(page);
    const download = page.waitForEvent('download');
    await surfaceOf(page, 'Logs').getByRole('button', { name: 'Download log', exact: true }).click();
    const file = await download;
    expect(file.suggestedFilename()).toBe('prompt_manager.log');
    expect((await readBlob(file)).length).toBeGreaterThanOrEqual(300);
    await page.route('**/prompt_manager/logs/download/**', route => route.fulfill({ status: 404, contentType: 'application/json',
        body: JSON.stringify({ success: false, error: 'Log file not found' }) }));
    await surfaceOf(page, 'Logs').getByRole('button', { name: 'Download log', exact: true }).click();
    await expect(toast(page)).toHaveText('The log could not be downloaded.');
});

test('runtime level (including CRITICAL) and console logging save and say they apply until the server restarts',
    async ({ page, request }) => {
        const posts = [];
        page.on('request', item => { if (item.method() === 'POST' && item.url().endsWith(CONFIG_URL)) posts.push(item.postDataJSON()); });
        await openDiagnostics(page);
        const level = settingOf(page, 'Runtime log level');
        await expect(level.locator('p')).toHaveText('How much the server writes to the log. Applied until the server restarts.');
        await expect(level.locator('option')).toHaveText(['INFO', 'WARNING', 'DEBUG', 'ERROR', 'CRITICAL']);
        await expect(level.locator('select')).toHaveValue('INFO');
        const consoleRow = settingOf(page, 'Console logging');
        await expect(consoleRow.locator('p')).toHaveText('Print Prompt Manager’s warnings and errors in the ComfyUI console '
            + '(everything at DEBUG). Applied until the server restarts.');
        const toggle = consoleRow.locator('input[type="checkbox"]');
        await expect(toggle).toBeChecked();
        await level.locator('select').selectOption('CRITICAL');
        await toggle.uncheck();
        await expect(status(page)).toHaveText('Unsaved changes.');
        await save(page).click();
        await expect(status(page)).toHaveText('Applied until the server restarts.');
        expect(posts).toEqual([{ level: 'CRITICAL', console_logging: false }]);
        const { config } = await serverJson(request, CONFIG_URL);
        expect([config.level, config.console_logging]).toEqual(['CRITICAL', false]);
        // Read again on a fresh page: the server's values.
        await openDiagnostics(page);
        await expect(settingOf(page, 'Runtime log level').locator('select')).toHaveValue('CRITICAL');
        await expect(settingOf(page, 'Console logging').locator('input[type="checkbox"]')).not.toBeChecked();
        // Only the changed key is sent.
        await settingOf(page, 'Runtime log level').locator('select').selectOption('DEBUG');
        await save(page).click();
        await expect(status(page)).toHaveText('Applied until the server restarts.');
        expect(posts.at(-1)).toEqual({ level: 'DEBUG' });
    });

test('a refused runtime value is marked invalid', async ({ page }) => {
    await page.route(`**${CONFIG_URL}`, route => (route.request().method() === 'POST'
        ? route.fulfill({ status: 400, contentType: 'application/json', body: JSON.stringify({ success: false, error: 'Invalid log level' }) })
        : route.continue()));
    await openDiagnostics(page);
    const select = settingOf(page, 'Runtime log level').locator('select');
    await select.selectOption('WARNING');
    await save(page).click();
    await expect(status(page)).toHaveText('Some changes were not saved. Review the highlighted settings.');
    await expect(select).toHaveAttribute('aria-invalid', 'true');
    await expect(settingOf(page, 'Console logging').locator('input[type="checkbox"]')).not.toHaveAttribute('aria-invalid');
    // The draft stays: an edit clears the mark.
    await expect(select).toHaveValue('WARNING');
    await select.selectOption('ERROR');
    await expect(select).not.toHaveAttribute('aria-invalid');
});

test('runtime values that cannot be read stay disabled and say so', async ({ page }) => {
    await page.route(`**${CONFIG_URL}`, route => route.fulfill({ status: 500, contentType: 'application/json',
        body: JSON.stringify({ success: false, error: 'boom' }) }));
    await openDiagnostics(page);
    for (const label of ['Runtime log level', 'Console logging']) {
        const row = settingOf(page, label);
        await expect(row.locator('p.small')).toHaveText('The current value could not be read.');
        await expect(row.locator('select, input[type="checkbox"]')).toBeDisabled();
    }
});

// ── Task 6: log files, the reviewed cleanup and Clear all logs ─────────────────────────────────────────────────────

const LOG_FOLDER = { rotated: [3000, 2048, 1024], other: true, link: true, active: 100 };
const LINKS = process.platform === 'win32' ? 0 : 1;

test('Review log cleanup lists only rotated copies and keeps the rest', async ({ page, request }) => {
    await seedLogs(request, LOG_FOLDER);
    const before = await names(request);
    const previews = [];
    page.on('request', item => { if (item.url().endsWith(PREVIEW_URL)) previews.push(item.postDataJSON()); });
    await openDiagnostics(page);
    const details = await openFiles(page);
    await expect(details.locator(':scope > p.small').first()).toHaveText(/^prompt_manager\.log · [\d.]+ (bytes|KB) · rotated copies: 3$/);
    await details.getByRole('button', { name: 'Review log cleanup', exact: true }).click();
    await expect(dialog(page).locator('#modal-title')).toHaveText('Review maintenance plan');
    await expect(dialog(page).locator('#modal-body > h3')).toHaveText('Rotated log copies');
    expect(previews).toEqual([{ operation: 'rotated_logs', options: {} }]);
    const grid = await dialog(page).locator('dl.meta-grid > div').evaluateAll(items => items.map(item =>
        [item.querySelector('dt').textContent, item.querySelector('dd').textContent]));
    expect(grid.map(([term]) => term)).toEqual(['Scope', 'Protected', 'Backup', 'Media on disk']);
    expect(grid[0][1]).toMatch(/^3 rotated log copies · 5\.9 KB$/);
    expect(grid[1][1]).toBe(['Kept: the active log', ...(LINKS ? ['Kept: 1 link'] : []), 'Kept: 1 other file'].join(' · '));
    expect(grid[2][1]).toBe('Not needed: no library record changes');
    expect(grid[3][1]).toBe('Deletes only the listed log files. No image, video or library record changes.');
    await expect(dialog(page).locator('details > summary')).toHaveText('Listed files (3)');
    await expect(dialog(page).locator('details pre')).toHaveText(/^prompt_manager\.log\.1 · 2\.9 KB · .+\nprompt_manager\.log\.2 · 2\.0 KB · .+\nprompt_manager\.log\.3 · 1\.0 KB · .+$/);
    // No D55 backup box: nothing in the library changes.
    await expect(dialog(page).locator('.notice')).toHaveCount(0);
    await expect(dialog(page).getByRole('button', { name: 'Back up now' })).toHaveCount(0);
    await dialog(page).getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(dialog(page)).toHaveCount(0);
    expect(await names(request)).toEqual(before);
});

test('Apply deletes exactly the listed files and Activity lists them', async ({ page, request }) => {
    await seedLogs(request, LOG_FOLDER);
    await openDiagnostics(page);
    const details = await openFiles(page);
    await details.getByRole('button', { name: 'Review log cleanup', exact: true }).click();
    await dialog(page).getByRole('button', { name: 'Apply reviewed operation', exact: true }).click();
    await expect(dialog(page)).toHaveCount(0);
    await expect(page).toHaveURL(/#\/activity\/[0-9a-f]{32}$/);
    const job = await ended(request, page.url().split('/').pop());
    expect([job.kind, job.state, job.title, job.counters.changed]).toEqual(['maintenance', 'finished', 'Rotated log copies', 3]);
    await expect(toast(page)).toHaveText(/Rotated log copies (started|finished · 3 changed)/);
    const main = page.locator('#workspace-main');
    await expect(main.locator('h1')).toHaveText('Rotated log copies');
    for (const name of ['prompt_manager.log.1', 'prompt_manager.log.2', 'prompt_manager.log.3']) await expect(main).toContainText(name);
    // Only those three are gone: the active log, the user's .bak and the link are kept.
    expect(await names(request)).toEqual(['prompt_manager.log', ...(LINKS ? ['prompt_manager.log.4'] : []), 'prompt_manager.log.bak']);
});

test('a rollover after the review deletes nothing and Activity offers Review again', async ({ page, request }) => {
    await seedLogs(request, LOG_FOLDER);
    await openDiagnostics(page);
    const details = await openFiles(page);
    await details.getByRole('button', { name: 'Review log cleanup', exact: true }).click();
    await expect(dialog(page).locator('#modal-body > h3')).toHaveText('Rotated log copies');
    await expect(await request.post('/__test__/logs/rotate')).toBeOK();
    const rolled = await serverJson(request, '/__test__/logs/state');
    await dialog(page).getByRole('button', { name: 'Apply reviewed operation', exact: true }).click();
    await expect(page).toHaveURL(/#\/activity\/[0-9a-f]{32}$/);
    const job = await ended(request, page.url().split('/').pop());
    expect([job.state, job.scope.failure]).toEqual(['failed', 'plan-changed']);
    await expect(page.locator('#workspace-main')).toContainText(CHANGED);
    expect((await serverJson(request, '/__test__/logs/state')).entries.map(entry => entry.name))
        .toEqual(rolled.entries.map(entry => entry.name));
    const previews = [];
    page.on('request', item => { if (item.url().endsWith(PREVIEW_URL)) previews.push(item.postDataJSON()); });
    await page.locator('#workspace-main').getByRole('button', { name: 'Review again', exact: true }).click();
    await expect(dialog(page).locator('#modal-body > h3')).toHaveText('Rotated log copies');
    expect(previews).toEqual([{ operation: 'rotated_logs', options: {} }]);
});

test('Clear all logs states both counts, confirms first and reads everything again', async ({ page, request }) => {
    await seedLogs(request, LOG_FOLDER);
    const truncates = [];
    page.on('request', item => { if (item.url().endsWith('/prompt_manager/logs/truncate')) truncates.push(item.postDataJSON()); });
    await openDiagnostics(page);
    await expect(lines(page)).toContainText('Database backup verified');
    const details = await openFiles(page);
    const clearRow = operation(page, 'Clear all logs');
    await expect(clearRow.locator('p')).toHaveText('Empty the active log and delete its rotated copies.');
    const clear = clearRow.getByRole('button', { name: 'Clear all logs…', exact: true });
    await expect(clear).toHaveClass(/\bdanger\b/);
    await clear.click();
    await expect(dialog(page).locator('#modal-title')).toHaveText('Clear all logs');
    await expect(dialog(page).locator('#modal-body > p')).toHaveText('Empties prompt_manager.log, deletes 3 rotated copies (5.9 KB) '
        + 'and clears the lines shown here. The other files in the log folder are kept. This cannot be undone.');
    await dialog(page).getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(dialog(page)).toHaveCount(0);
    expect(truncates).toEqual([]);
    await clear.click();
    await dialog(page).getByRole('button', { name: 'Clear', exact: true }).click();
    await expect(toast(page)).toHaveText('Logs cleared · 3 rotated copies deleted.');
    expect(truncates).toHaveLength(1);
    // #389: Clear sends back exactly the files the dialog was built from, each with its identity string.
    expect(truncates[0].expected.map(row => row.name).sort()).toEqual(['prompt_manager.log', 'prompt_manager.log.1',
        'prompt_manager.log.2', 'prompt_manager.log.3']);
    for (const row of truncates[0].expected) expect(row.identity).toMatch(/^\d+:\d+:\d+$/);
    // Read again: the viewer, the file line and the list.
    await expect(lines(page)).not.toContainText('Database backup verified');
    await expect(details.locator(':scope > p.small').first()).toHaveText(/rotated copies: 0$/);
    const kept = ['prompt_manager.log', 'prompt_manager.log.bak', ...(LINKS ? ['prompt_manager.log.4'] : [])];
    // The server lists newest first: compare as a set.
    await expect.poll(async () => (await details.locator('.operation-list h3').allTextContents()).sort()).toEqual([...kept].sort());
    expect(await names(request)).toEqual(['prompt_manager.log', ...(LINKS ? ['prompt_manager.log.4'] : []), 'prompt_manager.log.bak']);
});

test('Clear all logs reads the files again before it states what it deletes', async ({ page, request }) => {
    await seedLogs(request, { rotated: [1024], active: 100 });
    await openDiagnostics(page);
    const details = await openFiles(page);
    await expect(details.locator(':scope > p.small').first()).toHaveText(/rotated copies: 1$/);
    // The log rolls over while the page stays open: Clear names what it deletes now, not the count shown.
    await seedLogs(request, { records: false, rotated: [1024, 1024, 1024] });
    await operation(page, 'Clear all logs').getByRole('button', { name: 'Clear all logs…', exact: true }).click();
    await expect(dialog(page).locator('#modal-body > p')).toHaveText(/^Empties prompt_manager\.log, deletes 3 rotated copies \(3\.0 KB\) /);
    await expect(details.locator(':scope > p.small').first()).toHaveText(/rotated copies: 3$/);
    await dialog(page).getByRole('button', { name: 'Cancel', exact: true }).click();
    // A list that cannot be read gives the general sentence, never an old count.
    await page.route('**/prompt_manager/logs/files', route => route.fulfill({ status: 500, contentType: 'application/json',
        body: JSON.stringify({ success: false, error: 'boom', files: [] }) }));
    await operation(page, 'Clear all logs').getByRole('button', { name: 'Clear all logs…', exact: true }).click();
    await expect(dialog(page).locator('#modal-body > p')).toHaveText(/^Empties prompt_manager\.log, deletes its rotated copies and clears/);
    await dialog(page).getByRole('button', { name: 'Cancel', exact: true }).click();
    expect(await names(request)).toEqual(['prompt_manager.log', 'prompt_manager.log.1', 'prompt_manager.log.2', 'prompt_manager.log.3']);
});

test('a rollover while Clear all logs is open deletes nothing and says why (#389)', async ({ page, request }) => {
    await seedLogs(request, { rotated: [1024, 1024], active: 100 });
    await openDiagnostics(page);
    await openFiles(page);
    await operation(page, 'Clear all logs').getByRole('button', { name: 'Clear all logs…', exact: true }).click();
    await expect(dialog(page).locator('#modal-body > p')).toHaveText(/^Empties prompt_manager\.log, deletes 2 rotated copies /);
    // The log rolls over after the confirmation was built: the copies it listed are not the ones there now.
    await expect(await request.post('/__test__/logs/rotate')).toBeOK();
    const rolled = await names(request);
    const answer = page.waitForResponse(response => response.url().endsWith('/prompt_manager/logs/truncate'));
    await dialog(page).getByRole('button', { name: 'Clear', exact: true }).click();
    expect((await answer).status()).toBe(409);
    await expect(toast(page)).toHaveText('The log files changed since you confirmed; nothing was cleared. '
        + 'Read the list again and clear again.');
    expect(await names(request)).toEqual(rolled);
});

test('each listed file downloads by its returned name and a link has no Download', async ({ page, request }) => {
    await seedLogs(request, LOG_FOLDER);
    await openDiagnostics(page);
    const details = await openFiles(page);
    await expect(details.locator(':scope > p.small').nth(1))
        .toHaveText('Rotates at 10 MB · keeps 5 rotated copies · file logging on · memory buffer 1,000 entries');
    const list = details.locator('.operation-list');
    expect((await list.locator('h3').allTextContents()).sort()).toEqual(['prompt_manager.log', 'prompt_manager.log.1',
        'prompt_manager.log.2', 'prompt_manager.log.3', ...(LINKS ? ['prompt_manager.log.4'] : []), 'prompt_manager.log.bak'].sort());
    const item = name => list.locator('.operation').filter({ has: page.locator(`h3:text-is("${name}")`) });
    await expect(item('prompt_manager.log.1').locator('p')).toHaveText(/^2\.9 KB · \d{4}-\d\d-\d\d \d\d:\d\d · Rotated copy$/);
    await expect(item('prompt_manager.log.bak').locator('p')).toHaveText(/^0 bytes · .+ · Other file$/);
    if (LINKS) {
        await expect(item('prompt_manager.log.4').locator('p')).toHaveText(/ · Link, not downloaded$/);
        await expect(item('prompt_manager.log.4').getByRole('button')).toHaveCount(0);
    }
    for (const name of ['prompt_manager.log.1', 'prompt_manager.log.bak']) {
        const download = page.waitForEvent('download');
        await item(name).getByRole('button', { name: `Download ${name}`, exact: true }).click();
        const file = await download;
        expect(file.suggestedFilename()).toBe(name);
        if (name === 'prompt_manager.log.1') expect((await readBlob(file)).length).toBe(3000);
    }
});
