import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';
import { seedConcept } from './concept-inventory.mjs';

// System › Backup & export (#249 System S9 Task 24) on the S8 backend and the concept-shaped fixture: Back up and its
// status line (D103), the restore review (D105) and its job, the exports, Import / inspect and the recovery policy
// (D104). A restore here replaces this worker's own temp database; every test starts from a fresh concept fixture.
const workspace = '/prompt_manager/workspace';
const STATUS_URL = '/prompt_manager/backup/status';
const PREVIEW_URL = '/prompt_manager/restore/preview';
const APPLY_URL = '/prompt_manager/restore/apply';
const WHEN = { dateStyle: 'medium', timeStyle: 'short' };
const SAFETY_DESCRIPTION = 'Keep a verified copy of the database on disk before maintenance, cleanups and LoRA imports change ' +
    'records. A restore always keeps one.';

test.use({ locale: 'en-US', timezoneId: 'UTC' });

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
const dialog = page => page.locator('dialog#modal[open]');
const statusLine = page => layoutOf(page).locator('#backup-status');
const settingsStatus = page => page.locator('#settings-status');
const save = page => page.getByRole('button', { name: 'Save preferences', exact: true });
const buttonOf = (page, name) => layoutOf(page).getByRole('button', { name, exact: true });
const settingOf = (page, label) => layoutOf(page).locator('.setting-row').filter({ has: page.locator(`label:text-is("${label}")`) });

async function serverJson(request, url) {
    const response = await request.get(url);
    await expect(response).toBeOK();
    return response.json();
}

async function openBackups(page) {
    // A fresh page each time: the drafts live for the page session (Q10), and a hash-only goto would not reload.
    if (page.url().endsWith('#/system/backups')) await page.reload();
    else await page.goto(`${workspace}#/system/backups`);
    await expect(layoutOf(page).locator(':scope > .two-col > section.surface')).toHaveCount(2);
    await expect(statusLine(page)).not.toHaveText('Checking backups…');
    // The policy row is painted once the server settings were read.
    await expect(settingOf(page, 'Create a safety backup before maintenance').locator('input')).toBeEnabled();
    return layoutOf(page);
}

/** The status line the page should show for the server's current status (its own wording, in this locale). */
async function expectedStatus(page, request) {
    const status = await serverJson(request, STATUS_URL);
    return page.evaluate(([answer, options]) => {
        const when = value => new Date(value).toLocaleString(undefined, options);
        const parts = [];
        if (answer.last_download) parts.push(`Downloaded backup · ${when(answer.last_download.at)} · ${answer.last_download.verified ? 'verified' : 'not verified'}.`);
        if (answer.safety_backups.length) parts.push(`Safety backup · ${when(answer.safety_backups[0].modified_at)} · ${answer.safety_backups.length} on disk.`);
        return parts.join(' ') || 'No backup recorded.';
    }, [status, WHEN]);
}

/** A verified copy of the library as it is now (this records the session's last download, as Back up does). */
async function backupBytes(request) {
    const response = await request.get('/prompt_manager/backup');
    await expect(response).toBeOK();
    expect(response.headers()['x-promptmanager-verify']).toBe('ok');
    return response.body();
}

/** Pick `buffer` as the backup file through Review restore…'s file chooser; resolves with the preview's answer. */
async function chooseBackup(page, buffer, name = 'prompts_backup_20261025_093000.db') {
    const chooser = page.waitForEvent('filechooser');
    const answer = page.waitForResponse(response => response.url().endsWith(PREVIEW_URL) && response.request().method() === 'POST');
    await buttonOf(page, 'Review restore…').click();
    await (await chooser).setFiles({ name, mimeType: 'application/octet-stream', buffer });
    return answer;
}

const gridOf = page => dialog(page).locator('dl.meta-grid > div').evaluateAll(items => items.map(item =>
    [item.querySelector('dt').textContent, item.querySelector('dd').textContent]));

const libraryTotal = async request => (await serverJson(request, '/prompt_manager/library?limit=1')).total;

test('Back up downloads through the api client and revokes the object URL (D103)', async ({ page, request }) => {
    await page.addInitScript(() => {
        window.objectUrls = { created: [], revoked: [] };
        const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
        URL.createObjectURL = blob => { const url = create(blob); window.objectUrls.created.push(url); return url; };
        URL.revokeObjectURL = url => { window.objectUrls.revoked.push(url); revoke(url); };
    });
    await openBackups(page);
    await expect(statusLine(page)).toHaveText('No backup recorded.');
    const download = page.waitForEvent('download');
    const answer = page.waitForResponse(response => new URL(response.url()).pathname === '/prompt_manager/backup');
    await buttonOf(page, 'Back up').click();
    expect((await answer).headers()['x-promptmanager-verify']).toBe('ok');
    expect((await download).suggestedFilename()).toMatch(/^prompts_backup_\d{8}_\d{6}\.db$/);
    await expect(toast(page)).toHaveText('Database backup downloaded.');
    // The status line is read again: this session's verified download, as the server records it.
    await expect(statusLine(page)).toHaveText(/^Downloaded backup · .+ · verified\.$/);
    await expect(statusLine(page)).toHaveText(await expectedStatus(page, request));
    await expect(buttonOf(page, 'Back up')).not.toHaveAttribute('aria-disabled', 'true');
    const urls = await page.evaluate(() => window.objectUrls.created);
    expect(urls).toHaveLength(1);
    await expect.poll(() => page.evaluate(() => window.objectUrls.revoked)).toEqual(urls);
});

test('status line shows only server-reported backups (D103)', async ({ page, request }) => {
    await openBackups(page);
    await expect(statusLine(page)).toHaveText('No backup recorded.');
    await expect(await request.post('/__test__/system/safety-backup')).toBeOK();
    await openBackups(page);
    await expect(statusLine(page)).toHaveText(/^Safety backup · .+ · 1 on disk\.$/);
    await expect(statusLine(page)).toHaveText(await expectedStatus(page, request));
    // A status the server could not report is never shown as "no backup".
    await page.route(`**${STATUS_URL}`, route => route.fulfill({ status: 500, json: { success: false, error: 'Failed' } }));
    await page.reload();
    await expect(statusLine(page)).toHaveText('The backup status could not be read.');
});

test('Overview backup row uses the same status (D93)', async ({ page, request }) => {
    await expect(await request.post('/__test__/system/safety-backup')).toBeOK();
    await backupBytes(request);
    await page.goto(`${workspace}#/system/overview`);
    const row = layoutOf(page).locator('.operation').filter({ has: page.locator('h3', { hasText: 'Backup status' }) });
    const expected = await expectedStatus(page, request);
    expect(expected).toMatch(/^Downloaded backup · .+ · verified\. Safety backup · .+ · 1 on disk\.$/);
    await expect(row.locator('p')).toHaveText(expected);
    await row.getByRole('button', { name: 'View backups', exact: true }).click();
    await expect(page).toHaveURL(`${workspace}#/system/backups`);
    await expect(statusLine(page)).toHaveText(expected);
});

test('Review restore stages the chosen file and shows the real summary with the backup box (D105)', async ({ page, request }) => {
    const bytes = await backupBytes(request);
    await expect(await request.post('/prompt_manager/save', { data: { text: 'S9 prompt saved after the backup' } })).toBeOK();
    const before = await serverJson(request, '/__test__/system/digest');
    await openBackups(page);
    const answer = await chooseBackup(page, bytes);
    const preview = await answer.json();
    expect(preview.integrity).toBe('ok');
    await expect(dialog(page).locator('#modal-title')).toHaveText('Restore preview');
    const counts = summary => `${summary.prompts.toLocaleString('en-US')} ${summary.prompts === 1 ? 'prompt' : 'prompts'} · ` +
        `${summary.image_links} ${summary.image_links === 1 ? 'image link' : 'image links'} · ` +
        `${summary.pairs} ${summary.pairs === 1 ? 'saved pair' : 'saved pairs'} · ${summary.runs} ${summary.runs === 1 ? 'run' : 'runs'}`;
    const rows = await gridOf(page);
    expect(rows.map(([term]) => term)).toEqual(['Snapshot', 'Integrity', 'Migration', 'Safety backup', 'In the backup', 'In the library now']);
    expect(rows[0][1]).toMatch(/^prompts_backup_20261025_093000.db · (\d+ KB|\d+\.\d MB) · .+$/);
    expect(rows.slice(1)).toEqual([['Integrity', 'Verified'], ['Migration', 'Current schema'],
        ['Safety backup', `Created before replacing (${preview.safety_backup.destination_label})`],
        ['In the backup', counts(preview.summary)], ['In the library now', counts(preview.live)]]);
    // The prompt saved after the backup is the difference the review shows.
    expect(preview.live.prompts).toBe(preview.summary.prompts + 1);
    await expect(dialog(page).locator('#modal-body > p.small')).toHaveText('Restore replaces the whole library with this backup ' +
        'and runs alone in Activity. Nothing changes until you restore. This review holds for 15 minutes.');
    await expect(dialog(page).locator('.notice h3')).toHaveText('Back up the database first (recommended)');
    await expect(dialog(page).locator('.notice p')).toHaveText('Downloads a copy of the prompts database before the restore replaces it.');
    const actions = dialog(page).locator('.dialog-actions > button');
    await expect(actions).toHaveText(['Cancel', 'Restore']);
    await expect(actions.nth(1)).toHaveClass('danger');
    // The backup box downloads a copy and keeps the review open.
    const download = page.waitForEvent('download');
    await dialog(page).getByRole('button', { name: 'Back up now', exact: true }).click();
    await download;
    await expect(toast(page)).toHaveText('Database backup downloaded.');
    await expect(dialog(page).locator('#modal-title')).toHaveText('Restore preview');
    // A review writes nothing: the rows and the media are as they were.
    expect(await serverJson(request, '/__test__/system/digest')).toEqual(before);
    expect((await serverJson(request, '/__test__/system/backups')).stages).toBe(1);
});

test('Cancel discards the stage; Escape too', async ({ page, request }) => {
    const bytes = await backupBytes(request);
    const before = await serverJson(request, '/__test__/system/digest');
    await openBackups(page);
    await chooseBackup(page, bytes);
    await expect(dialog(page).locator('#modal-title')).toHaveText('Restore preview');
    expect((await serverJson(request, '/__test__/system/backups')).stages).toBe(1);
    let discarded = page.waitForResponse(response => response.url().includes(`${PREVIEW_URL}/`) && response.request().method() === 'DELETE');
    await dialog(page).getByRole('button', { name: 'Cancel', exact: true }).click();
    expect((await discarded).status()).toBe(200);
    await expect(dialog(page)).toHaveCount(0);
    await expect(buttonOf(page, 'Review restore…')).toBeFocused();
    expect((await serverJson(request, '/__test__/system/backups')).stages).toBe(0);
    await chooseBackup(page, bytes);
    await expect(dialog(page).locator('#modal-title')).toHaveText('Restore preview');
    discarded = page.waitForResponse(response => response.url().includes(`${PREVIEW_URL}/`) && response.request().method() === 'DELETE');
    await page.keyboard.press('Escape');
    expect((await discarded).status()).toBe(200);
    expect((await serverJson(request, '/__test__/system/backups')).stages).toBe(0);
    expect(await serverJson(request, '/__test__/system/digest')).toEqual(before);
});

test('Restore starts the job, opens Activity; views reload after it finishes', async ({ page, request }) => {
    const bytes = await backupBytes(request);
    const restoredTotal = await libraryTotal(request);
    await expect(await request.post('/prompt_manager/save', { data: { text: 'S9 prompt the restore removes' } })).toBeOK();
    expect(await libraryTotal(request)).toBe(restoredTotal + 1);
    await openBackups(page);
    const libraryCount = page.locator('aside.sidebar a[data-route="library"] .nav-count');
    await expect(libraryCount).toHaveText((restoredTotal + 1).toLocaleString('en-US'));
    await chooseBackup(page, bytes);
    const applied = page.waitForResponse(response => response.url().endsWith(APPLY_URL));
    await dialog(page).getByRole('button', { name: 'Restore', exact: true }).click();
    const { job } = await (await applied).json();
    expect([job.kind, job.title, job.cancellable]).toEqual(['restore', 'Database restore', false]);
    await expect(page).toHaveURL(`${workspace}#/activity/${job.id}`);
    await expect(toast(page)).toHaveText(/^Database restore finished · Restored \d+ prompts?, /);
    // The library is the backup's again, and the page says so without a reload: the sidebar counts were read again.
    expect(await libraryTotal(request)).toBe(restoredTotal);
    await expect(libraryCount).toHaveText(restoredTotal.toLocaleString('en-US'));
    expect((await serverJson(request, '/__test__/system/backups')).safety).toHaveLength(1);
    // The Activity job view stayed, and Backup & export now names the safety backup the restore kept.
    await expect(page).toHaveURL(`${workspace}#/activity/${job.id}`);
    await page.goto(`${workspace}#/system/backups`);
    await expect(statusLine(page)).toHaveText(/Safety backup · .+ · 1 on disk\.$/);
});

test('a finished restore shows the open page again from the restored data', async ({ page, request }) => {
    const bytes = await backupBytes(request);
    const restoredTotal = await libraryTotal(request);
    await expect(await request.post('/prompt_manager/save', { data: { text: 'S9 prompt the restore removes' } })).toBeOK();
    await openBackups(page);
    await chooseBackup(page, bytes);
    const preview = page.locator('dialog#modal[open] #modal-title');
    await expect(preview).toHaveText('Restore preview');
    // The restore is held at its start, so the reader is on the Library (showing the prompt) when it finishes.
    await expect(await request.post('/__test__/hold', { data: { path: APPLY_URL } })).toBeOK();
    const applied = page.waitForResponse(response => response.url().endsWith(APPLY_URL));
    await dialog(page).getByRole('button', { name: 'Restore', exact: true }).click();
    await expect.poll(async () => (await serverJson(request, '/__test__/hold')).held[APPLY_URL]).toBe(1);
    await page.evaluate(() => { window.location.hash = '#/library'; });
    const card = page.locator('#workspace-main article.prompt-card').filter({ hasText: 'S9 prompt the restore removes' });
    await expect(card).toHaveCount(1);
    await expect(await request.post('/__test__/release')).toBeOK();
    await applied;
    await expect(toast(page)).toHaveText(/^Database restore finished · /);
    // The shelf was read again from the restored library, on the same route.
    await expect(card).toHaveCount(0);
    await expect(page).toHaveURL(`${workspace}#/library`);
    expect(await libraryTotal(request)).toBe(restoredTotal);
});

test('invalid file shows the validator message; nothing is staged', async ({ page, request }) => {
    const before = await serverJson(request, '/__test__/system/digest');
    await openBackups(page);
    const answer = await chooseBackup(page, Buffer.from('this is not a database'), 'notes.db');
    expect(answer.status()).toBe(400);
    const body = await answer.json();
    expect(body.code).toBe('invalid-backup');
    await expect(toast(page)).toHaveText(`This file can't be restored: ${body.error.replace(/\.?$/, '.')}`);
    await expect(dialog(page)).toHaveCount(0);
    await expect(buttonOf(page, 'Review restore…')).not.toHaveAttribute('aria-disabled', 'true');
    expect((await serverJson(request, '/__test__/system/backups')).stages).toBe(0);
    expect(await serverJson(request, '/__test__/system/digest')).toEqual(before);
});

test('Export JSON downloads /library/export; secondary buttons download all prompts (/export) and /pairs/export (D103)', async ({ page }) => {
    const layout = await openBackups(page);
    const portable = layout.locator('section.surface').filter({ has: page.locator('h2', { hasText: 'Portable export' }) });
    expect(await portable.locator('.row-actions > button').allTextContents())
        .toEqual(['Export JSON', 'Import / inspect file', 'Export all prompts JSON', 'Export saved pairs JSON']);
    for (const [label, url, name, kind] of [
        ['Export JSON', '/prompt_manager/library/export', /^prompt-manager-library-v1\.json$/, 'prompt-manager-library'],
        ['Export all prompts JSON', '/prompt_manager/export', /^prompt_manager_\d{8}_\d{6}\.json$/, null],
        ['Export saved pairs JSON', '/prompt_manager/pairs/export', /^prompt-pairs\.json$/, 'prompt-manager-pairs']]) {
        const download = page.waitForEvent('download');
        const answer = page.waitForResponse(response => new URL(response.url()).pathname === url);
        await portable.getByRole('button', { name: label, exact: true }).click();
        expect((await answer).status()).toBe(200);
        const file = await download;
        expect(file.suggestedFilename()).toMatch(name);
        const parsed = JSON.parse(await (await file.createReadStream()).toArray().then(chunks => Buffer.concat(chunks).toString('utf8')));
        if (kind) expect(parsed.kind).toBe(kind);
        else expect(Array.isArray(parsed.prompts)).toBe(true);
    }
    // A failed export says so and saves nothing.
    await page.route('**/prompt_manager/pairs/export', route => route.fulfill({ status: 500, json: { success: false } }));
    await portable.getByRole('button', { name: 'Export saved pairs JSON', exact: true }).click();
    await expect(toast(page)).toHaveText('The export could not be downloaded. Nothing was saved to this device.');
});

test('Import / inspect lists Inspect, Rescan and the shared scan review (D15, D55)', async ({ page, request }) => {
    await openBackups(page);
    const open = async () => {
        await buttonOf(page, 'Import / inspect file').click();
        await expect(dialog(page).locator('#modal-title')).toHaveText('Import & inspect');
    };
    await open();
    const rows = dialog(page).locator('.operation-list > .operation');
    expect(await rows.locator('h3').allTextContents()).toEqual(['Inspect a local image', 'Scan an output folder', 'Scan for metadata']);
    expect(await rows.locator('button').allTextContents()).toEqual(['Open inspector', 'Rescan', 'Review scan']);
    // Rescan reads the output folders again and says what it found; while it runs, a reopened dialog says so.
    await expect(await request.post('/__test__/hold', { data: { path: '/prompt_manager/gallery/rescan' } })).toBeOK();
    const rescan = page.waitForResponse(response => response.url().endsWith('/prompt_manager/gallery/rescan'));
    await rows.getByRole('button', { name: 'Rescan', exact: true }).click();
    await expect(dialog(page)).toHaveCount(0);
    await expect(toast(page)).toHaveText('Rescanning output folders…');
    await open();
    await expect(rows.nth(1).locator('button')).toHaveText('Rescanning…');
    await expect(rows.nth(1).locator('button')).toHaveAttribute('aria-disabled', 'true');
    await expect(await request.post('/__test__/release')).toBeOK();
    expect((await rescan).status()).toBe(200);
    await expect(toast(page)).toHaveText(/^Rescan finished\. \d+ files? in \d+ output folders?\.$/);
    await expect(rows.nth(1).locator('button')).toHaveText('Rescan');
    // Scan for metadata opens the shared review over the server's output folders; Cancel starts nothing.
    await dialog(page).getByRole('button', { name: 'Review scan', exact: true }).click();
    await expect(dialog(page).locator('#modal-title')).toHaveText('Review metadata scan');
    await expect(dialog(page).locator('dl.meta-grid dd').first()).toHaveText(/^\d+ output folders?$/);
    await dialog(page).getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(dialog(page)).toHaveCount(0);
    // Open inspector goes to Inspect a file.
    await open();
    await dialog(page).getByRole('button', { name: 'Open inspector', exact: true }).click();
    await expect(page).toHaveURL(`${workspace}#/inspect`);
    await expect(dialog(page)).toHaveCount(0);
});

test('safety backup toggle saves through Save preferences with revision (D104)', async ({ page, request }) => {
    const before = await serverJson(request, '/prompt_manager/settings');
    expect(before.settings.safety_backup).toBe(true);
    await openBackups(page);
    const row = settingOf(page, 'Create a safety backup before maintenance');
    // D104: what the policy really does (the concept's "Preview a verified backup…" line does not say it).
    await expect(row.locator('p').first()).toHaveText(SAFETY_DESCRIPTION);
    const toggle = row.locator('input');
    await expect(toggle).toBeChecked();
    await toggle.uncheck();
    await expect(settingsStatus(page)).toHaveText('Unsaved changes.');
    const saved = page.waitForRequest(item => item.url().endsWith('/prompt_manager/settings') && item.method() === 'POST');
    await save(page).click();
    const body = (await saved).postDataJSON();
    expect(body).toEqual({ safety_backup: false, expected_revision: before.revision });
    await expect(settingsStatus(page)).toHaveText('Saved to the server configuration.');
    expect((await serverJson(request, '/prompt_manager/settings')).settings.safety_backup).toBe(false);
    // A maintenance review now says so.
    const plan = await request.post('/prompt_manager/maintenance/preview', { data: { operation: 'optimize', options: {} } });
    expect((await plan.json()).backup.policy).toBe('off');
    await openBackups(page);
    await expect(toggle).not.toBeChecked();
    // A configuration changed elsewhere is a conflict, never a blind write.
    await toggle.check();
    await expect(await request.post('/prompt_manager/settings', { data: { result_timeout: 7,
        expected_revision: (await serverJson(request, '/prompt_manager/settings')).revision } })).toBeOK();
    await save(page).click();
    await expect(settingsStatus(page)).toHaveText('Settings changed elsewhere. Reload this section to see them.');
    expect((await serverJson(request, '/prompt_manager/settings')).settings.safety_backup).toBe(false);
});

test('schedule offers Manual only; Daily/Weekly disabled with reason (D104)', async ({ page }) => {
    await openBackups(page);
    const schedule = settingOf(page, 'Scheduled database backups');
    expect(await schedule.locator('option').evaluateAll(options => options.map(option => [option.textContent, option.disabled])))
        .toEqual([['Manual only', false], ['Daily', true], ['Weekly', true]]);
    await expect(schedule.locator('select')).toHaveValue('Manual only');
    await expect(schedule.locator('p')).toHaveText('Daily and Weekly are not available yet: back up with Back up whenever you choose.');
    const policy = layoutOf(page).locator(':scope > section.surface');
    await expect(policy.locator('h2')).toHaveText('Recovery policy Proposed');
    await expect(policy.locator('h2 > span.chip.blue')).toHaveText('Proposed');
    await expect(policy.locator('p.warning-note')).toHaveText('Restores are staged, migrated and validated before replacing live data.');
    // Nothing on the schedule is ever drafted.
    await expect(settingsStatus(page)).toHaveText('No unsaved changes.');
});

test('a failed settings read leaves the policy disabled with the reason', async ({ page }) => {
    await page.route('**/prompt_manager/settings', route => (route.request().method() === 'GET'
        ? route.fulfill({ status: 500, json: { success: false, error: 'Failed' } }) : route.continue()));
    await page.goto(`${workspace}#/system/backups`);
    const row = settingOf(page, 'Create a safety backup before maintenance');
    await expect(row.locator('p.small')).toHaveText('Server settings could not be read.');
    await expect(row.locator('input')).toBeDisabled();
});

test('the legacy admin Backup and Restore keep working', async ({ page, request }) => {
    const bytes = await backupBytes(request);
    const restored = await request.post('/prompt_manager/restore', { multipart: { database_file: {
        name: 'prompts_backup.db', mimeType: 'application/octet-stream', buffer: bytes } } });
    await expect(restored).toBeOK();
    expect((await restored.json()).success).toBe(true);
    await page.goto('/prompt_manager/admin');
    await expect(page.locator('#restoreFileInput')).toHaveCount(1);
});

/** Start a fake running job (a metadata scan another tab started), which an exclusive restore must wait for. */
async function runningScan(request) {
    const response = await request.post('/__test__/jobs/start', { data: { kind: 'scan', title: 'Metadata scan', total: 4 } });
    await expect(response).toBeOK();
    return (await response.json()).job;
}

const stagesOf = async request => (await serverJson(request, '/__test__/system/backups')).stages;
const deleted = page => page.waitForResponse(response => response.url().includes(`${PREVIEW_URL}/`) &&
    response.request().method() === 'DELETE');

test('a refused restore reopens its review: Restore retries it, Discard releases it (I1)', async ({ page, request }) => {
    const bytes = await backupBytes(request);
    await openBackups(page);
    await chooseBackup(page, bytes);
    await expect(dialog(page).locator('#modal-title')).toHaveText('Restore preview');
    const scan = await runningScan(request);
    const refused = page.waitForResponse(response => response.url().endsWith(APPLY_URL));
    await dialog(page).getByRole('button', { name: 'Restore', exact: true }).click();
    expect((await refused).status()).toBe(409);
    await expect(toast(page)).toHaveText('Another job is running: Metadata scan.');
    // The same review is open again, its stage still held: Restore retries, Discard releases it.
    await expect(dialog(page).locator('#modal-title')).toHaveText('Restore preview');
    await expect(dialog(page).locator('.dialog-actions > button')).toHaveText(['Discard', 'Restore']);
    expect(await stagesOf(request)).toBe(1);
    await expect(await request.post(`/__test__/jobs/${scan.id}/finish`, { data: { state: 'finished' } })).toBeOK();
    const applied = page.waitForResponse(response => response.url().endsWith(APPLY_URL));
    await dialog(page).getByRole('button', { name: 'Restore', exact: true }).click();
    const { job } = await (await applied).json();
    await expect(page).toHaveURL(`${workspace}#/activity/${job.id}`);
    await expect(toast(page)).toHaveText(/^Database restore finished · /);
    expect(await stagesOf(request)).toBe(0);
});

test('two refused restores in a row, then a third Review restore… still opens the preview (I1)', async ({ page, request }) => {
    const bytes = await backupBytes(request);
    await openBackups(page);
    await runningScan(request);
    for (const leave of ['discard', 'escape']) {
        await chooseBackup(page, bytes);
        await expect(dialog(page).locator('#modal-title')).toHaveText('Restore preview');
        const refused = page.waitForResponse(response => response.url().endsWith(APPLY_URL));
        await dialog(page).getByRole('button', { name: 'Restore', exact: true }).click();
        expect((await refused).status()).toBe(409);
        await expect(dialog(page).locator('.dialog-actions > button')).toHaveText(['Discard', 'Restore']);
        const released = deleted(page);
        if (leave === 'discard') await dialog(page).getByRole('button', { name: 'Discard', exact: true }).click();
        else await page.keyboard.press('Escape');
        expect((await released).status()).toBe(200);
        await expect(dialog(page)).toHaveCount(0);
        expect(await stagesOf(request)).toBe(0);
    }
    const answer = await chooseBackup(page, bytes);
    expect((await answer).status()).toBe(200);
    await expect(dialog(page).locator('#modal-title')).toHaveText('Restore preview');
    expect(await stagesOf(request)).toBe(1);
});

test('× and leaving the section or the page discard the stage (M1)', async ({ page, request }) => {
    const bytes = await backupBytes(request);
    await openBackups(page);
    const leaves = [
        ['×', () => page.locator('#modal-close').click()],
        // The modal covers the page, so the section changes as a link or the Back button changes it: by its hash.
        ['section', () => page.evaluate(() => { window.location.hash = '#/system/maintenance'; })],
        ['route', () => page.evaluate(() => { window.location.hash = '#/images'; })],
    ];
    for (const [name, leave] of leaves) {
        if (!page.url().endsWith('#/system/backups')) {
            await page.goto(`${workspace}#/system/backups`);
            await expect(statusLine(page)).not.toHaveText('Checking backups…');
        }
        await chooseBackup(page, bytes);
        await expect(dialog(page).locator('#modal-title')).toHaveText('Restore preview');
        expect(await stagesOf(request), name).toBe(1);
        // A keepalive DELETE (it may outlive the page) is asserted by its request and the server's stage count.
        const released = page.waitForRequest(item => item.url().includes(`${PREVIEW_URL}/`) && item.method() === 'DELETE');
        await leave();
        await released;
        await expect(dialog(page)).toHaveCount(0);
        await expect.poll(() => stagesOf(request), { message: name }).toBe(0);
    }
});

test('a safety backup file (no .db extension) can be picked and reviewed (M3)', async ({ page, request }) => {
    await expect(await request.post('/__test__/system/safety-backup')).toBeOK();
    const bytes = await backupBytes(request);
    await openBackups(page);
    // The picker lists every file; the server decides what is a database.
    await expect(page.locator('#restore-file')).not.toHaveAttribute('accept', /./);
    const answer = await chooseBackup(page, bytes, 'prompts.db.backup_20261025_093000_000000');
    expect((await answer).status()).toBe(200);
    await expect(dialog(page).locator('#modal-title')).toHaveText('Restore preview');
    // The copy's size depends on what earlier tests on this server wrote: KB or MB.
    expect((await gridOf(page))[0][1]).toMatch(/^prompts\.db\.backup_20261025_093000_000000 · [\d,.]+ (KB|MB) · .+$/);
});
