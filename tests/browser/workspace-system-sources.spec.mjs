import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';
import { seedConcept } from './concept-inventory.mjs';

// System › Sources & scans (#249 System S4 Task 11) on the concept-shaped real fixture, whose two sources are this
// server's `output` and `input` folders: the source list, monitoring and worker rows read GET /settings (S4 Task 9),
// Add/Remove edit the host draft behind the one Save row, and every scan starts the session job through the shared
// "Review metadata scan" (D55), scoped to one source from its row (S4 Task 10). The hooks are workspace_system_fixture.py's.
const workspace = '/prompt_manager/workspace';
const SETTINGS_URL = '/prompt_manager/settings';
const NOTICE_KEY = 'pm_update_notice_dismissed';
const OUTSIDE_REASON = 'Gallery root must be inside a ComfyUI directory (output, input, temp, user) or one listed in PROMPT_MANAGER_EXTRA_GALLERY_ROOTS';

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
    await request.post('/__test__/jobs/restart');
    await request.post('/__test__/system/reset');
    expect(page.systemErrors).toEqual([]);
});

const toast = page => page.locator('#workspace-toast');
const layoutOf = page => page.locator('#workspace-main .settings-layout');
const sourceList = page => layoutOf(page).locator('#source-list');
const statusOf = page => layoutOf(page).locator('#settings-status');
const saveButton = page => layoutOf(page).getByRole('button', { name: 'Save preferences', exact: true });
const dialog = page => page.locator('dialog#modal[open]');

async function serverJson(request, url) {
    const response = await request.get(url);
    await expect(response).toBeOK();
    return response.json();
}

/** Open Sources with the update notice dismissed (or shown, `notice: true`) once the source list has loaded. */
async function openSources(page, { notice = false } = {}) {
    if (!notice) await page.addInitScript(key => { if (location.protocol.startsWith('http')) localStorage.setItem(key, '3.2.0'); }, NOTICE_KEY);
    await page.goto(`${workspace}#/system/sources`);
    await expect(sourceList(page).locator('.operation').first()).toBeVisible();
    await expect(layoutOf(page).locator('#workers')).toBeEnabled();
    return layoutOf(page);
}

/** The source list's rows: `[h3, p, [buttons]]`, then its note line when there is one. */
function rowsOf(page) {
    return sourceList(page).evaluate(list => [...list.children].map(row => row.matches('.operation')
        ? [row.querySelector('h3').textContent, row.querySelector('p').textContent,
            [...row.querySelectorAll('.row-actions > button')].map(button => button.textContent)]
        : [row.localName, row.className, row.textContent]));
}

const sourceRow = (page, title) => sourceList(page).locator('.operation').filter({ has: page.getByRole('heading', { name: title, exact: true }) });

test('notice Later persists the 3.2.0 dismissal (pm_update_notice_dismissed) without scanning', async ({ page, request }) => {
    const writes = [];
    page.on('request', item => { if (!['GET', 'HEAD', 'OPTIONS'].includes(item.method())) writes.push(item.url()); });
    const layout = await openSources(page, { notice: true });
    const notice = layout.locator(':scope > .notice');
    await expect(notice.locator('h2')).toHaveText('Bring older images into your prompt history');
    await expect(notice.locator('p')).toHaveText('The schema upgrades automatically. An optional metadata scan links existing positives and negatives without regenerating all thumbnails.');
    await expect(notice.locator('.row-actions > button')).toHaveText(['Later', 'Review scan']);
    await notice.getByRole('button', { name: 'Later', exact: true }).click();
    await expect(layout.locator(':scope > .notice')).toHaveCount(0);
    expect(await page.evaluate(key => localStorage.getItem(key), NOTICE_KEY)).toBe('3.2.0');
    await expect(dialog(page)).toHaveCount(0);
    // The dismissal is the legacy admin page's own, so neither page shows the notice again.
    await page.reload();
    await expect(sourceList(page).locator('.operation').first()).toBeVisible();
    await expect(layout.locator(':scope > .notice')).toHaveCount(0);
    expect(writes).toEqual([]);
    expect((await serverJson(request, '/prompt_manager/jobs')).jobs).toEqual([]);
});

test('Review scan opens the shared Review metadata scan with all sources', async ({ page, request }) => {
    const layout = await openSources(page, { notice: true });
    // The notice's Review scan dismisses it, as the legacy page's does, and reviews every source.
    await layout.locator(':scope > .notice').getByRole('button', { name: 'Review scan', exact: true }).click();
    await expect(dialog(page).locator('#modal-title')).toHaveText('Review metadata scan');
    await expect(dialog(page).locator('dl.meta-grid > div > dd')).toHaveText(['2 output folders', 'New and unresolved files']);
    expect(await page.evaluate(key => localStorage.getItem(key), NOTICE_KEY)).toBe('3.2.0');
    await dialog(page).getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(layout.locator(':scope > .notice')).toHaveCount(0);
    // Enrich existing metadata is the same review.
    const enrich = layout.locator('.operation').filter({ hasText: 'Enrich existing metadata' });
    await expect(enrich.locator('p')).toHaveText('Resolve both prompts, deduplicate recipes, preserve uncertain links.');
    await enrich.getByRole('button', { name: 'Review scan', exact: true }).click();
    await expect(dialog(page).locator('dl.meta-grid > div > dd')).toHaveText(['2 output folders', 'New and unresolved files']);
    await dialog(page).getByRole('button', { name: 'Cancel', exact: true }).click();
    expect((await serverJson(request, '/prompt_manager/jobs')).jobs).toEqual([]);
});

test('Scan changes opens it with that one source ("1 output folder") and starts a scoped job that opens in Activity', async ({ page, request }) => {
    await expect(await request.post('/__test__/system/monitor', { data: { watching: ['input'] } })).toBeOK();
    const bodies = [];
    page.on('request', item => { if (item.method() === 'POST' && item.url().endsWith('/prompt_manager/jobs/scan')) bodies.push(item.postDataJSON()); });
    await openSources(page);
    const input = sourceRow(page, 'ComfyUI input');
    await expect(input.locator('p')).toHaveText('input · watching');
    await input.getByRole('button', { name: 'Scan changes', exact: true }).click();
    await expect(dialog(page).locator('#modal-title')).toHaveText('Review metadata scan');
    await expect(dialog(page).locator('dl.meta-grid > div > dd')).toHaveText(['1 output folder', 'New and unresolved files']);
    await dialog(page).getByRole('button', { name: 'Start scan', exact: true }).click();
    await expect(page).toHaveURL(/#\/activity\/[0-9a-f]{32}$/);
    // The toast says it started (or, for a scan this quick, that it already finished); Activity shows its progress.
    await expect(toast(page)).toHaveText(/^Scan (started\.|finished · )/);
    expect(bodies).toEqual([{ scope: 'new', roots: [1], paths: ['input'] }]);
    const id = page.url().split('/').pop();
    // The job walks only that source (the count is recorded once the walk has listed it).
    await expect.poll(async () => (await serverJson(request, `/prompt_manager/jobs/${id}`)).job.scope)
        .toEqual({ files: 'new', roots: [1], output_folders: 1 });
    await expect(page.locator('#workspace-main h1')).toHaveText('Scan for metadata');
});

test('Add source validates on the server, then marks the host draft dirty; Save persists', async ({ page, request }) => {
    // A folder inside ComfyUI's output folder that is not a source yet.
    await expect(await request.post('/__test__/system/unthumbnailed', { data: { count: 1 } })).toBeOK();
    const layout = await openSources(page);
    expect(await rowsOf(page)).toEqual([
        ['ComfyUI output', 'output · manual scan', ['Scan folder', 'Remove']],
        ['ComfyUI input', 'input · manual scan', ['Scan folder', 'Remove']],
    ]);
    await expect(layout.locator('.panel-heading > h2')).toHaveText('Image sources');
    const add = layout.locator('.panel-heading').getByRole('button', { name: '+ Add source', exact: true });
    await add.click();
    await expect(dialog(page).locator('#modal-title')).toHaveText('Add image source');
    await expect(dialog(page).locator('label[for="action-input"]')).toHaveText('Folder path');
    await expect(dialog(page).locator('#action-input')).toHaveValue('');
    await expect(dialog(page).locator('.dialog-actions > button')).toHaveText(['Cancel', 'Add']);
    await dialog(page).locator('#action-input').fill('output/unthumbnailed');
    const validated = page.waitForResponse(response => response.url().endsWith('/prompt_manager/settings/validate-source'));
    await dialog(page).getByRole('button', { name: 'Add', exact: true }).click();
    expect(await (await validated).json()).toEqual({ success: true, valid: true, label: 'unthumbnailed', path: 'output/unthumbnailed', code: null, reason: null });
    await expect(dialog(page)).toHaveCount(0);
    await expect(add).toBeFocused();
    // Validation wrote nothing: the draft is dirty and the server still has two sources.
    expect(await rowsOf(page)).toEqual([
        ['ComfyUI output', 'output · manual scan', ['Scan folder', 'Remove']],
        ['ComfyUI input', 'input · manual scan', ['Scan folder', 'Remove']],
        ['unthumbnailed', 'output/unthumbnailed · not saved yet', ['Remove']],
    ]);
    await expect(statusOf(page)).toHaveText('Unsaved changes.');
    expect((await serverJson(request, SETTINGS_URL)).sources.effective).toHaveLength(2);
    const saved = page.waitForRequest(item => item.method() === 'POST' && item.url().endsWith(SETTINGS_URL));
    await saveButton(page).click();
    const body = (await saved).postDataJSON();
    expect(body.gallery_root_paths).toEqual(['output', 'input', 'output/unthumbnailed']);
    expect(typeof body.expected_revision).toBe('string');
    await expect(statusOf(page)).toHaveText('Saved to the server configuration.');
    // The list is the server's again: three effective sources, each scannable.
    await expect(sourceRow(page, 'unthumbnailed').locator('p')).toHaveText('output/unthumbnailed · manual scan');
    await expect(sourceRow(page, 'unthumbnailed').getByRole('button', { name: 'Scan folder', exact: true })).toBeVisible();
    expect((await serverJson(request, SETTINGS_URL)).sources.effective.map(source => source.path)).toEqual(['output', 'input', 'output/unthumbnailed']);
});

test('invalid source shows the server reason', async ({ page, request }) => {
    const layout = await openSources(page);
    await layout.getByRole('button', { name: '+ Add source', exact: true }).click();
    const field = dialog(page).locator('#action-input');
    for (const [path, reason, submit] of [
        ['output/not-there', 'Gallery root does not exist', 'click'],
        // Enter in the field adds too (M6).
        ['/etc', OUTSIDE_REASON, 'Enter'],
    ]) {
        await field.fill(path);
        if (submit === 'Enter') await field.press('Enter');
        else await dialog(page).getByRole('button', { name: 'Add', exact: true }).click();
        await expect(dialog(page).locator('p.small[role="alert"]')).toHaveText(reason);
        await expect(field).toHaveAttribute('aria-invalid', 'true');
        await expect(field).toHaveValue(path);
    }
    // A folder already listed (the server's answer names it) is not added twice.
    await field.fill('input');
    await dialog(page).getByRole('button', { name: 'Add', exact: true }).click();
    await expect(dialog(page).locator('p.small[role="alert"]')).toHaveText('This folder is already a source.');
    // A check that cannot finish says so and adds nothing.
    await page.route('**/prompt_manager/settings/validate-source', route => route.fulfill({ status: 500,
        json: { success: false, code: null, error: 'Failed to check the source' } }));
    await field.fill('output/not-checked');
    await dialog(page).getByRole('button', { name: 'Add', exact: true }).click();
    await expect(dialog(page).locator('p.small[role="alert"]')).toHaveText('The folder could not be checked. Try again.');
    await page.unroute('**/prompt_manager/settings/validate-source');
    // Typing again clears the reason.
    await field.fill('output/unthumbnailed');
    await expect(dialog(page).locator('p.small[role="alert"]')).toHaveCount(0);
    await expect(field).not.toHaveAttribute('aria-invalid', 'true');
    await dialog(page).getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(statusOf(page)).toHaveText('No unsaved changes.');
    expect((await serverJson(request, SETTINGS_URL)).sources.configured).toHaveLength(2);
});

test('Remove marks the draft; removing the last entry states auto-detect', async ({ page, request }) => {
    await openSources(page);
    await sourceRow(page, 'ComfyUI input').getByRole('button', { name: 'Remove', exact: true }).click();
    expect(await rowsOf(page)).toEqual([['ComfyUI output', 'output · manual scan', ['Scan folder', 'Remove']]]);
    await expect(statusOf(page)).toHaveText('Unsaved changes.');
    await sourceRow(page, 'ComfyUI output').getByRole('button', { name: 'Remove', exact: true }).click();
    expect(await rowsOf(page)).toEqual([['p', 'small', 'No source is configured. After saving, ComfyUI’s output folder is used.']]);
    await saveButton(page).click();
    await expect(statusOf(page)).toHaveText('Saved to the server configuration.');
    // Saved: ComfyUI's output folder is detected and listed without Remove.
    await expect.poll(rowsOf.bind(null, page)).toEqual([
        ['ComfyUI output', 'output · manual scan', ['Scan folder']],
        ['p', 'small', 'No source is configured; ComfyUI’s output folder is used.'],
    ]);
    const { sources } = await serverJson(request, SETTINGS_URL);
    expect([sources.configured, sources.auto_detected]).toEqual([[], true]);
});

test('a configured source that does not exist says why and can be removed', async ({ page, request }) => {
    await expect(await request.post('/__test__/system/missing-source')).toBeOK();
    await openSources(page);
    // The fixture's unmounted drive is not inside a ComfyUI folder, so the server says that (it never probes outside).
    expect((await rowsOf(page)).at(-1)).toEqual(['unmounted-drive', `unmounted-drive · ${OUTSIDE_REASON}`, ['Remove']]);
    await sourceRow(page, 'unmounted-drive').getByRole('button', { name: 'Remove', exact: true }).click();
    await expect(sourceList(page).locator('.operation')).toHaveCount(2);
    await saveButton(page).click();
    await expect(statusOf(page)).toHaveText('Saved to the server configuration.');
    expect((await serverJson(request, SETTINGS_URL)).sources.configured.map(source => source.path)).toEqual(['output', 'input']);
});

test('watching only with a live observer; restart pending otherwise', async ({ page, request }) => {
    // No image monitor in this process: every source is scanned by hand.
    await openSources(page);
    await expect(sourceList(page).locator('.operation p')).toHaveText(['output · manual scan', 'input · manual scan']);
    // A live observer that scheduled output: output is watched; input was added since it started.
    await expect(await request.post('/__test__/system/monitor', { data: { watching: ['output'] } })).toBeOK();
    await page.reload();
    await expect(sourceList(page).locator('.operation p')).toHaveText(['output · watching', 'input · restart pending']);
    await expect(sourceList(page).locator('.operation .row-actions > button:first-child')).toHaveText(['Scan changes', 'Scan folder']);
    // An observer that died watches nothing.
    await expect(await request.post('/__test__/system/monitor', { data: { watching: ['output', 'input'], alive: false } })).toBeOK();
    await page.reload();
    await expect(sourceList(page).locator('.operation p')).toHaveText(['output · restart pending', 'input · restart pending']);
});

test('Watch for new outputs saves monitoring_enabled; a restart applies it', async ({ page, request }) => {
    await expect(await request.post('/__test__/system/monitor', { data: { watching: ['output', 'input'] } })).toBeOK();
    const layout = await openSources(page);
    const monitoring = layout.getByRole('switch', { name: 'Watch for new outputs', exact: true });
    await expect(monitoring).toBeChecked();
    await expect(layout.locator('.setting-row').filter({ has: page.locator('#monitoring') }).locator('p'))
        .toHaveText('New files enter the same library. Existing scans do not count as new runs.');
    await monitoring.uncheck();
    await expect(statusOf(page)).toHaveText('Unsaved changes.');
    const saved = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith(SETTINGS_URL));
    await saveButton(page).click();
    const answer = await (await saved).json();
    expect(answer.restart_required_fields).toEqual(['monitoring_enabled']);
    // D128: saved for the next start, and the status says a restart applies it.
    await expect(statusOf(page)).toHaveText('Saved to the server configuration. Restart ComfyUI to apply: Watch for new outputs.');
    // The running observer keeps watching until ComfyUI restarts, and the list says so.
    const { settings, monitor } = await serverJson(request, SETTINGS_URL);
    expect([settings.monitoring_enabled, monitor.observer_alive]).toEqual([false, true]);
    await expect(sourceList(page).locator('.operation p')).toHaveText(['output · watching until restart', 'input · watching until restart']);
    await expect(sourceList(page).locator('.operation .row-actions > button:first-child')).toHaveText(['Scan changes', 'Scan changes']);
    // Switched back on before the restart: the observer still runs, so nothing waits for one.
    await monitoring.check();
    await saveButton(page).click();
    await expect(statusOf(page)).toHaveText('Saved to the server configuration.');
    await expect(sourceList(page).locator('.operation p')).toHaveText(['output · watching', 'input · watching']);
});

test('worker range max is cpu_count', async ({ page, request }) => {
    const { settings } = await serverJson(request, SETTINGS_URL);
    const layout = await openSources(page);
    const workers = layout.locator('#workers');
    await expect(layout.locator('label[for="workers"]')).toHaveText('Worker threads');
    await expect(workers).toHaveAttribute('type', 'range');
    await expect(workers).toHaveAttribute('min', '1');
    await expect(workers).toHaveAttribute('max', String(settings.cpu_count));
    await expect(workers).toHaveValue(String(settings.worker_threads));
    const next = settings.worker_threads === 1 ? settings.cpu_count : 1;
    await workers.fill(String(next));
    await expect(statusOf(page)).toHaveText(next === settings.worker_threads ? 'No unsaved changes.' : 'Unsaved changes.');
    test.skip(next === settings.worker_threads, 'One core: the range has a single value');
    const saved = page.waitForRequest(item => item.method() === 'POST' && item.url().endsWith(SETTINGS_URL));
    await saveButton(page).click();
    expect((await saved).postDataJSON().worker_threads).toBe(next);
    await expect(statusOf(page)).toHaveText('Saved to the server configuration.');
    expect((await serverJson(request, SETTINGS_URL)).settings.worker_threads).toBe(next);
});

test('Refresh files calls /gallery/rescan and toasts its counts', async ({ page }) => {
    const layout = await openSources(page);
    const refresh = layout.locator('.operation').filter({ has: page.getByRole('heading', { name: 'Refresh files', exact: true }) });
    await expect(refresh.locator('p')).toHaveText('Read every source again to find files added or removed outside ComfyUI.');
    const answered = page.waitForResponse(response => response.url().endsWith('/prompt_manager/gallery/rescan'));
    await refresh.getByRole('button', { name: 'Refresh', exact: true }).click();
    const { total, roots } = await (await answered).json();
    expect(roots).toBe(2);
    await expect(toast(page)).toHaveText(`Files refreshed. ${total.toLocaleString('en-US')} files in 2 output folders.`);
    // A failed refresh says so.
    await page.route('**/prompt_manager/gallery/rescan', route => route.fulfill({ status: 500, json: { success: false, error: 'busy disk' } }));
    await refresh.getByRole('button', { name: 'Refresh', exact: true }).click();
    await expect(toast(page)).toHaveText('Files could not be refreshed. Try again.');
});

const THUMBNAIL_PLAN_URL = '/prompt_manager/images/thumbnails/plan';
const THUMBNAIL_JOB_URL = '/prompt_manager/jobs/thumbnails';
const planCount = value => value.toLocaleString('en-US');

/** Open "Thumbnail maintenance" from Sources' Thumbnail generation → Review options. */
async function openThumbnailReview(page) {
    const layout = await openSources(page);
    const thumbnails = layout.locator('.operation').filter({ has: page.getByRole('heading', { name: 'Thumbnail generation', exact: true }) });
    await expect(thumbnails.locator('p')).toHaveText('Generate missing previews; rebuilding all is a separate explicit option.');
    await thumbnails.getByRole('button', { name: 'Review options', exact: true }).click();
    await expect(dialog(page).locator('#modal-title')).toHaveText('Thumbnail maintenance');
    return dialog(page);
}

/** The thumbnail requests this page sends: plan reads (their query) and job starts (their body). */
function thumbnailRequests(page) {
    const seen = { plans: [], starts: [] };
    page.on('request', item => {
        const url = new URL(item.url());
        if (url.pathname === THUMBNAIL_PLAN_URL) seen.plans.push(Object.fromEntries(url.searchParams));
        if (item.method() === 'POST' && url.pathname === THUMBNAIL_JOB_URL) seen.starts.push(item.postDataJSON());
    });
    return seen;
}

const missingLine = plan => `${planCount(plan.targets)} ${plan.targets === 1 ? 'preview' : 'previews'} to generate · ` +
    `${planCount(plan.fresh)} already up to date.`;

test('Review options shows the plan counts for the chosen scope/quality/size before Start', async ({ page, request }) => {
    await expect(await request.post('/__test__/system/unthumbnailed', { data: { count: 3 } })).toBeOK();
    // Image loading quality (Appearance, Q3) preselects the dialog's quality.
    await page.addInitScript(() => { if (location.protocol.startsWith('http')) localStorage.setItem('gallerySettings', JSON.stringify({ imageQuality: 'high' })); });
    const seen = thumbnailRequests(page);
    const plan = await serverJson(request, `${THUMBNAIL_PLAN_URL}?mode=missing&size=512&quality=high`);
    expect(plan.targets).toBe(3);
    const review = await openThumbnailReview(page);
    await expect(review.locator('#modal-body > *')).toHaveCount(5);
    await expect(review.locator('.field > label')).toHaveText(['Scope', 'Quality', 'Preview size limit']);
    await expect(review.locator('#thumb-mode option')).toHaveText(['Missing thumbnails only', 'Rebuild all (explicit opt-in)']);
    await expect(review.locator('#thumb-quality option')).toHaveText(['Medium · optimized', 'High · original', 'Low · fastest']);
    await expect(review.locator('#thumb-mode')).toHaveValue('missing');
    await expect(review.locator('#thumb-quality')).toHaveValue('high');
    await expect(review.locator('#thumb-limit')).toHaveValue('512');
    await expect(review.locator('#thumb-limit')).toHaveAttribute('min', '128');
    await expect(review.locator('#thumb-limit')).toHaveAttribute('max', '2048');
    const line = review.locator('#modal-body > p.small');
    await expect(line).toHaveAttribute('role', 'status');
    await expect(line).toHaveText(missingLine(plan));
    expect(seen.plans).toEqual([{ mode: 'missing', size: '512', quality: 'high' }]);
    await expect(review.locator('.dialog-actions > button')).toHaveText(['Cancel', 'Start']);
    // Reviewing starts nothing; Cancel closes.
    await review.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(dialog(page)).toHaveCount(0);
    expect(seen.starts).toEqual([]);
    expect((await serverJson(request, '/prompt_manager/jobs')).jobs).toEqual([]);
});

test('size outside 128–2048 reports validity and requests nothing', async ({ page, request }) => {
    const seen = thumbnailRequests(page);
    const review = await openThumbnailReview(page);
    const line = review.locator('#modal-body > p.small');
    await expect(line).toHaveText(/ already up to date\.$/);
    const size = review.locator('#thumb-limit');
    for (const value of ['4096', '64', '']) {
        await size.fill(value);
        await expect(size).toHaveAttribute('aria-invalid', 'true');
        await expect(line).toHaveText('Enter a size from 128 to 2048 pixels.');
        expect(await size.evaluate(input => input.validity.valid)).toBe(false);
        // Start keeps the dialog open and sends nothing; neither does a scope change.
        await review.getByRole('button', { name: 'Start', exact: true }).click();
        await expect(dialog(page).locator('#modal-title')).toHaveText('Thumbnail maintenance');
        await review.locator('#thumb-mode').selectOption('rebuild');
        await expect(line).toHaveText('Enter a size from 128 to 2048 pixels.');
        await review.locator('#thumb-mode').selectOption('missing');
    }
    expect(seen.starts).toEqual([]);
    expect(seen.plans).toEqual([{ mode: 'missing', size: '512', quality: 'medium' }]);
    await size.fill('1024');
    await expect(size).not.toHaveAttribute('aria-invalid');
    await expect(line).toHaveText(/ already up to date\.$/);
    expect((await serverJson(request, '/prompt_manager/jobs')).jobs).toEqual([]);
});

test('Start closes the dialog, toasts, and opens the job in Activity', async ({ page, request }) => {
    await expect(await request.post('/__test__/system/unthumbnailed', { data: { count: 2 } })).toBeOK();
    const seen = thumbnailRequests(page);
    const review = await openThumbnailReview(page);
    await review.locator('#thumb-quality').selectOption('low');
    await review.locator('#thumb-limit').fill('256');
    await review.getByRole('button', { name: 'Start', exact: true }).click();
    await expect(dialog(page)).toHaveCount(0);
    await expect(page).toHaveURL(/#\/activity\/[0-9a-f]{32}$/);
    // The toast says it started (or, for a run this quick, that it already finished); Activity shows its progress.
    await expect(toast(page)).toHaveText(/^(Thumbnail generation started\.|Thumbnails finished · 2 generated · 0 errors)$/);
    expect(seen.starts).toEqual([{ mode: 'missing', size: 256, quality: 'low' }]);
    const id = page.url().split('/').pop();
    const { job } = await serverJson(request, `/prompt_manager/jobs/${id}`);
    expect(job).toMatchObject({ kind: 'thumbnails', title: 'Thumbnail generation', scope: { mode: 'missing', size: 256, quality: 'low' } });
    await expect(page.locator('#workspace-main h1')).toHaveText('Thumbnail generation');
    await expect.poll(async () => (await serverJson(request, `/prompt_manager/jobs/${id}`)).job.state).toBe('finished');
});

test('Rebuild all is explicit and the plan line says every preview is regenerated', async ({ page, request }) => {
    const seen = thumbnailRequests(page);
    const rebuild = await serverJson(request, `${THUMBNAIL_PLAN_URL}?mode=rebuild&size=512&quality=medium`);
    expect(rebuild.targets).toBeGreaterThan(0);
    const review = await openThumbnailReview(page);
    // Missing thumbnails only is the default; rebuilding is the explicit second option.
    await expect(review.locator('#thumb-mode')).toHaveValue('missing');
    const line = review.locator('#modal-body > p.small');
    await expect(line).toHaveText(/ already up to date\.$/);
    await review.locator('#thumb-mode').selectOption('rebuild');
    await expect(line).toHaveText(`Every preview is regenerated: ${planCount(rebuild.targets)} files.`);
    expect(seen.plans).toEqual([{ mode: 'missing', size: '512', quality: 'medium' }, { mode: 'rebuild', size: '512', quality: 'medium' }]);
    // Back and forth reads each scope once.
    await review.locator('#thumb-mode').selectOption('missing');
    await expect(line).toHaveText(/ already up to date\.$/);
    await review.locator('#thumb-mode').selectOption('rebuild');
    await expect(line).toHaveText(`Every preview is regenerated: ${planCount(rebuild.targets)} files.`);
    expect(seen.plans).toHaveLength(2);
    await review.getByRole('button', { name: 'Start', exact: true }).click();
    await expect(page).toHaveURL(/#\/activity\/[0-9a-f]{32}$/);
    expect(seen.starts).toEqual([{ mode: 'rebuild', size: 512, quality: 'medium' }]);
    const id = page.url().split('/').pop();
    await expect.poll(async () => (await serverJson(request, `/prompt_manager/jobs/${id}`)).job.state).toBe('finished');
});

test('a plan that cannot be read says so; Start still starts', async ({ page }) => {
    await page.route(`**${THUMBNAIL_PLAN_URL}?*`, route => route.fulfill({ status: 500, json: { success: false, error: 'disk' } }));
    const review = await openThumbnailReview(page);
    await expect(review.locator('#modal-body > p.small')).toHaveText('The previews could not be counted.');
    await expect(review.getByRole('button', { name: 'Start', exact: true })).not.toHaveAttribute('aria-disabled');
    await review.getByRole('button', { name: 'Cancel', exact: true }).click();
});

test('a refused start shows the busy link toast', async ({ page, request }) => {
    const running = await (await request.post('/__test__/jobs/start', { data: { title: 'Scan for metadata' } })).json();
    const review = await openThumbnailReview(page);
    await review.getByRole('button', { name: 'Start', exact: true }).click();
    await expect(dialog(page)).toHaveCount(0);
    await expect(toast(page)).toHaveText('Another job is running: Scan for metadata.');
    await expect(toast(page).getByRole('link', { name: 'Scan for metadata', exact: true })).toHaveAttribute('href', `#/activity/${running.job.id}`);
    await expect(page).toHaveURL(`${workspace}#/system/sources`);
    expect((await serverJson(request, '/prompt_manager/jobs')).jobs.map(job => job.kind)).toEqual(['scan']);
    await request.post(`/__test__/jobs/${running.job.id}/finish`, { data: { state: 'finished' } });
});

test('a save refused by a running scan shows the busy link toast', async ({ page, request }) => {
    const running = await (await request.post('/__test__/jobs/start', { data: { title: 'Scan for metadata' } })).json();
    await openSources(page);
    await sourceRow(page, 'ComfyUI input').getByRole('button', { name: 'Remove', exact: true }).click();
    await saveButton(page).click();
    await expect(toast(page)).toHaveText('Another job is running: Scan for metadata.');
    await expect(toast(page).getByRole('link', { name: 'Scan for metadata', exact: true })).toHaveAttribute('href', `#/activity/${running.job.id}`);
    await expect(statusOf(page)).toHaveText('Some changes were not saved. Review the highlighted settings.');
    // The draft is kept for the next Save, and the server still has both sources.
    await expect(sourceList(page).locator('.operation')).toHaveCount(1);
    expect((await serverJson(request, SETTINGS_URL)).sources.configured).toHaveLength(2);
    // An answer that does not name its job (an older server): the toast names the running one from the job list.
    await page.route(`**${SETTINGS_URL}`, route => (route.request().method() === 'POST'
        ? route.fulfill({ status: 409, json: { success: false, code: 'busy', error: 'Scan for metadata already running' } })
        : route.continue()));
    await page.locator('#workspace-toast').evaluate(node => { node.textContent = ''; });
    await saveButton(page).click();
    await expect(toast(page)).toHaveText('Another job is running: Scan for metadata.');
    await expect(toast(page).getByRole('link', { name: 'Scan for metadata', exact: true })).toHaveAttribute('href', `#/activity/${running.job.id}`);
    await page.unroute(`**${SETTINGS_URL}`);
    await request.post(`/__test__/jobs/${running.job.id}/finish`, { data: { state: 'finished' } });
});

test('a source list that cannot be read says so and retries', async ({ page }) => {
    await page.addInitScript(key => { if (location.protocol.startsWith('http')) localStorage.setItem(key, '3.2.0'); }, NOTICE_KEY);
    let failing = true;
    await page.route(`**${SETTINGS_URL}`, route => (failing && route.request().method() === 'GET'
        ? route.fulfill({ status: 500, json: { success: false, error: 'Injected failure' } }) : route.continue()));
    await page.goto(`${workspace}#/system/sources`);
    await expect(sourceList(page).getByRole('alert')).toHaveText('Sources could not be loaded. Your saved data has not changed.');
    await expect(sourceList(page).locator('.operation')).toHaveCount(0);
    // The server settings rows say so too, and stay disabled.
    await expect(layoutOf(page).locator('#workers')).toBeDisabled();
    failing = false;
    await sourceList(page).getByRole('button', { name: 'Retry', exact: true }).click();
    await expect(sourceList(page).locator('.operation')).toHaveCount(2);
});

test('a scan from a list that changed since it was read starts nothing, says why and reads the list again', async ({ page, request }) => {
    await openSources(page);
    // The legacy page (or another tab) removes ComfyUI output: the page's "input" is index 1, now past the end.
    await expect(await request.post(SETTINGS_URL, { data: { gallery_root_paths: ['input'] } })).toBeOK();
    const refused = page.waitForResponse(response => response.url().endsWith('/prompt_manager/jobs/scan'));
    await sourceRow(page, 'ComfyUI input').getByRole('button', { name: 'Scan folder', exact: true }).click();
    await dialog(page).getByRole('button', { name: 'Start scan', exact: true }).click();
    const answer = await refused;
    expect([answer.status(), (await answer.json()).code]).toEqual([409, 'sources-changed']);
    await expect(toast(page)).toHaveText('The sources changed since they were loaded. Reload them.');
    // No scan started (the other save ran as its own source-change hold).
    expect((await serverJson(request, '/prompt_manager/jobs')).jobs.map(job => job.kind)).toEqual(['source_change']);
    // Read again: the one source is index 0 now, and its scan starts.
    await expect.poll(rowsOf.bind(null, page)).toEqual([['ComfyUI input', 'input · manual scan', ['Scan folder', 'Remove']]]);
    await expect(statusOf(page)).toHaveText('No unsaved changes.');
    const started = page.waitForRequest(item => item.method() === 'POST' && item.url().endsWith('/prompt_manager/jobs/scan'));
    await sourceRow(page, 'ComfyUI input').getByRole('button', { name: 'Scan folder', exact: true }).click();
    await dialog(page).getByRole('button', { name: 'Start scan', exact: true }).click();
    expect((await started).postDataJSON()).toEqual({ scope: 'new', roots: [0], paths: ['input'] });
    await expect(page).toHaveURL(/#\/activity\/[0-9a-f]{32}$/);
});

test('a save refused for one unusable source names it and marks its row', async ({ page, request }) => {
    await expect(await request.post('/__test__/system/missing-source', { data: { inside: true } })).toBeOK();
    await openSources(page);
    await expect(sourceRow(page, 'unplugged').locator('p')).toHaveText('output/unplugged · manual scan');
    // The drive is unplugged after the page read the list; removing another source resends the whole list.
    await expect(await request.post('/__test__/system/unplug')).toBeOK();
    await sourceRow(page, 'ComfyUI input').getByRole('button', { name: 'Remove', exact: true }).click();
    const saved = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith(SETTINGS_URL));
    await saveButton(page).click();
    const answer = await saved;
    expect(answer.status()).toBe(400);
    expect(await answer.json()).toMatchObject({ code: 'invalid-source', source: 'output/unplugged', index: 1 });
    await expect(statusOf(page)).toHaveText("output/unplugged can't be used: Gallery root does not exist. Remove it or reconnect it, then save.");
    await expect(sourceRow(page, 'unplugged')).toHaveAttribute('aria-invalid', 'true');
    await expect(sourceList(page).locator('.operation[aria-invalid="true"]')).toHaveCount(1);
    expect((await serverJson(request, SETTINGS_URL)).sources.configured).toHaveLength(3);
    // Removed, the list saves.
    await sourceRow(page, 'unplugged').getByRole('button', { name: 'Remove', exact: true }).click();
    await expect(sourceList(page).locator('.operation[aria-invalid="true"]')).toHaveCount(0);
    await saveButton(page).click();
    await expect(statusOf(page)).toHaveText('Saved to the server configuration.');
    expect((await serverJson(request, SETTINGS_URL)).sources.configured.map(source => source.path)).toEqual(['output']);
});
