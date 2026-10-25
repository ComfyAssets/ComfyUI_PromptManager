import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';
import { seedConcept } from './concept-inventory.mjs';

// System › Maintenance (#249 System S7 Task 19) on the concept-shaped real fixture and the S6 backend: the concept's
// rows, the read-only health check, the D100 plan review applied as an Activity job, the D101 duplicate search and
// review, the S5 thumbnail dialog and the D102 metadata cache row. The hooks are workspace_system_fixture.py's.
const workspace = '/prompt_manager/workspace';
const PREVIEW_URL = '/prompt_manager/maintenance/preview';
const APPLY_URL = '/prompt_manager/maintenance/apply';
const CHANGED = 'The library changed since the review; nothing was changed. Review the plan again.';

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
const row = (page, title) => layoutOf(page).locator('.operation').filter({ has: page.locator('h3', { hasText: title }) });

async function serverJson(request, url) {
    const response = await request.get(url);
    await expect(response).toBeOK();
    return response.json();
}

async function openMaintenance(page) {
    await page.goto(`${workspace}#/system/maintenance`);
    await expect(layoutOf(page).locator(':scope > section.surface')).toHaveCount(2);
    return layoutOf(page);
}

/** The open plan dialog's `[dt, dd]` rows. */
const gridOf = page => dialog(page).locator('dl.meta-grid > div').evaluateAll(items => items.map(item =>
    [item.querySelector('dt').textContent, item.querySelector('dd').textContent]));

/** Wait until the job `id` ended on the server; answers its summary. */
async function ended(request, id) {
    await expect.poll(async () => (await serverJson(request, `/prompt_manager/jobs/${id}`)).job.state).not.toBe('running');
    return (await serverJson(request, `/prompt_manager/jobs/${id}`)).job;
}

test('the section shows the concept rows; Metadata cache follows Q5 (D102)', async ({ page }) => {
    const layout = await openMaintenance(page);
    await expect(layout.locator(':scope > .notice h2')).toHaveText('Check first. Review changes. Then apply.');
    await expect(layout.locator(':scope > section.handoff')).toHaveCount(0);
    expect(await layout.locator(':scope > section.surface').evaluateAll(surfaces => surfaces.map(surface => [
        surface.querySelector('h2').textContent,
        [...surface.querySelectorAll('.operation')].map(item => [item.querySelector('h3').textContent,
            item.querySelector('.row-actions > button').textContent, item.querySelector('.row-actions > button').className]),
    ]))).toEqual([
        ['Database', [['Integrity & relationship checks', 'Check', ''], ['Duplicate prompt text', 'Review plan', ''],
            ['Optimize database', 'Review plan', ''], ['Unused prompt components', 'Review plan', 'danger']]],
        ['Images & cache', [['Missing-file links', 'Review plan', 'danger'], ['Duplicate image files', 'Find duplicates', ''],
            ['Thumbnail cache', 'Review options', ''], ['Metadata cache', 'Review plan', '']]],
    ]);
    // Nothing of Prompt Manager's is cached in a browser (#222): the row says so and its review is unavailable.
    const cache = row(page, 'Metadata cache');
    await expect(cache.locator('p')).toHaveText('Clear only Prompt Manager metadata cached by this browser. Nothing is cached in this browser.');
    const review = cache.getByRole('button', { name: 'Review plan', exact: true });
    await expect(review).toHaveAttribute('aria-disabled', 'true');
    // An aria-disabled button stays focusable and clickable; its press opens nothing.
    await review.dispatchEvent('click');
    await expect(review).toHaveAttribute('aria-disabled', 'true');
    await expect(dialog(page)).toHaveCount(0);
});

test('Metadata cache reviews and clears only owned browser entries when there are some (D102)', async ({ page }) => {
    // The shipped inventory is empty (#222); this test names one owned entry through the module's inventory seam.
    await page.addInitScript(() => {
        localStorage.setItem('pm-owned-test-entry', 'cached');
        localStorage.setItem('pm_workspace_theme', 'dark');
        let wrapped;
        Object.defineProperty(window, 'CacheOwnership', { configurable: true, get: () => wrapped, set(api) {
            const owned = { storageKeys: ['pm-owned-test-entry'], cacheNames: [] };
            wrapped = { ...api, ownedEntries: options => api.ownedEntries({ ...options, owned }),
                clearOwned: options => api.clearOwned({ ...options, owned }) };
        } });
    });
    await openMaintenance(page);
    const cache = row(page, 'Metadata cache');
    const review = cache.getByRole('button', { name: 'Review plan', exact: true });
    await expect(review).not.toHaveAttribute('aria-disabled', 'true');
    await expect(cache.locator('p')).toHaveText('Clear only Prompt Manager metadata cached by this browser.');
    await review.click();
    await expect(dialog(page).locator('#modal-body > h3')).toHaveText('Metadata cache');
    expect(await gridOf(page)).toEqual([['Scope', '1 cached entry in this browser'],
        ['Protected', 'Preferences and other tools’ data in this browser'], ['Backup', 'Not needed: the database is not changed'],
        ['Media on disk', 'No files are removed']]);
    await dialog(page).getByRole('button', { name: 'Clear browser cache', exact: true }).click();
    await expect(toast(page)).toHaveText('Cleared 1 cached entry from this browser.');
    await expect(review).toHaveAttribute('aria-disabled', 'true');
    expect(await page.evaluate(() => [localStorage.getItem('pm-owned-test-entry'), localStorage.getItem('pm_workspace_theme')]))
        .toEqual([null, 'dark']);
});

test('Run health checks and Check open the Health report; Review missing links starts a fresh preview, never a cleanup',
    async ({ page, request }) => {
        await expect(await request.post('/__test__/system/missing-file')).toBeOK();
        const before = await serverJson(request, '/__test__/system/digest');
        const previews = [];
        page.on('request', item => { if (item.url().endsWith(PREVIEW_URL)) previews.push(item.postDataJSON()); });
        await openMaintenance(page);
        const check = row(page, 'Integrity & relationship checks').getByRole('button', { name: 'Check', exact: true });
        await check.click();
        await expect(dialog(page).locator('#modal-title')).toHaveText('Health report');
        await expect(dialog(page).locator('.operation').filter({ hasText: 'Missing files · output' }).locator('.chip'))
            .toHaveText('1 link needs review');
        await expect(check).toHaveText('Check');
        await dialog(page).getByRole('button', { name: 'Review missing links', exact: true }).click();
        await expect(dialog(page).locator('#modal-title')).toHaveText('Review maintenance plan');
        await expect(dialog(page).locator('#modal-body > h3')).toHaveText('Missing-file links');
        expect(previews).toEqual([{ operation: 'missing_links', options: {} }]);
        expect((await gridOf(page))[0]).toEqual(['Scope', '1 link to a missing file']);
        await dialog(page).getByRole('button', { name: 'Cancel', exact: true }).click();
        await expect(dialog(page)).toHaveCount(0);
        await expect(check).toBeFocused();
        // The notice's Run health checks opens the same report.
        await layoutOf(page).getByRole('button', { name: 'Run health checks', exact: true }).click();
        await expect(dialog(page).locator('#modal-title')).toHaveText('Health report');
        expect(await serverJson(request, '/__test__/system/digest')).toEqual(before);
    });

test("plan dialog shows the server's scope, protected, backup policy and media lines with the backup box (D100); Cancel writes nothing",
    async ({ page, request }) => {
        await expect(await request.post('/__test__/system/duplicate-prompts')).toBeOK();
        const before = await serverJson(request, '/__test__/system/digest');
        const answers = [];
        page.on('response', response => { if (response.url().endsWith(PREVIEW_URL)) answers.push(response.json()); });
        await openMaintenance(page);
        const reviewButton = row(page, 'Duplicate prompt text').getByRole('button', { name: 'Review plan', exact: true });
        await reviewButton.click();
        const plan = dialog(page);
        await expect(plan.locator('#modal-title')).toHaveText('Review maintenance plan');
        const answer = await answers[0];
        expect(answer.scope.count).toBe(1);
        await expect(plan.locator('#modal-body > h3')).toHaveText('Duplicate prompt text');
        expect(await gridOf(page)).toEqual([
            ['Scope', answer.scope.label],
            ['Protected', answer.protected.reasons.length ? answer.protected.reasons.join(' · ') : 'Nothing is protected'],
            ['Backup', `Create and verify before changes (${answer.backup.destination_label})`],
            ['Media on disk', answer.media.label],
        ]);
        expect(await plan.locator('#modal-body').evaluate(body => [...body.children].map(node =>
            [node.localName, ...node.classList].join('.')))).toEqual(['h3', 'dl.meta-grid', 'details', 'p.small', 'div.notice',
            'div.dialog-actions']);
        await expect(plan.locator('details > summary')).toHaveText('Listed records (1)');
        await expect(plan.locator('#modal-body > p.small')).toHaveText(/^Nothing changes until you apply\. This review holds for 1[45] minutes\.$/);
        await expect(plan.locator('.notice h3')).toHaveText('Back up the database first (recommended)');
        await expect(plan.locator('.dialog-actions button')).toHaveText(['Cancel', 'Apply reviewed operation']);
        await plan.getByRole('button', { name: 'Cancel', exact: true }).click();
        await expect(dialog(page)).toHaveCount(0);
        await expect(reviewButton).toBeFocused();
        // Optimize and Unused prompt components are reviewed the same way.
        for (const [title, scope] of [['Optimize database', 'The whole database'], ['Unused prompt components', null]]) {
            await row(page, title).getByRole('button', { name: 'Review plan', exact: true }).click();
            await expect(dialog(page).locator('#modal-body > h3')).toHaveText(title);
            const value = (await answers.at(-1)).scope.label;
            expect((await gridOf(page))[0]).toEqual(['Scope', scope ?? value]);
            await page.keyboard.press('Escape');
            await expect(dialog(page)).toHaveCount(0);
        }
        expect(await serverJson(request, '/__test__/system/digest')).toEqual(before);
    });

test('Apply reviewed operation closes, toasts, opens the job in Activity', async ({ page, request }) => {
    await expect(await request.post('/__test__/system/duplicate-prompts')).toBeOK();
    const applies = [];
    page.on('request', item => { if (item.url().endsWith(APPLY_URL)) applies.push(item.postDataJSON()); });
    await openMaintenance(page);
    await row(page, 'Duplicate prompt text').getByRole('button', { name: 'Review plan', exact: true }).click();
    await dialog(page).getByRole('button', { name: 'Apply reviewed operation', exact: true }).click();
    await expect(dialog(page)).toHaveCount(0);
    await expect(page).toHaveURL(/#\/activity\/[0-9a-f]{32}$/);
    await expect(toast(page)).toHaveText(/Duplicate prompt text (started|finished · 1 changed)/);
    expect(applies).toHaveLength(1);
    expect(Object.keys(applies[0])).toEqual(['token']);
    const job = await ended(request, page.url().split('/').pop());
    expect([job.kind, job.state, job.counters.changed]).toEqual(['maintenance', 'finished', 1]);
    const main = page.locator('#workspace-main');
    await expect(main.locator('h1')).toHaveText('Duplicate prompt text');
    await expect(main.locator('.stat-row .stat dt')).toHaveText(['Changed', 'Protected', 'Errors']);
    await expect(main.locator('.stat-row .stat dd').first()).toHaveText('1');
    // Not cancellable: no Cancel, a report like every job.
    await expect(main.getByRole('button', { name: 'Cancel job', exact: true })).toHaveCount(0);
    await expect(main.getByRole('button', { name: 'Download report', exact: true })).toBeVisible();
});

test('plan-changed asks for a new review', async ({ page, request }) => {
    await expect(await request.post('/__test__/system/duplicate-prompts')).toBeOK();
    await openMaintenance(page);
    await row(page, 'Duplicate prompt text').getByRole('button', { name: 'Review plan', exact: true }).click();
    await expect(dialog(page).locator('#modal-body > h3')).toHaveText('Duplicate prompt text');
    // The library changes after the review: the apply's recheck refuses it and nothing is changed.
    await expect(await request.post('/__test__/system/duplicate-prompts', { data: { again: true } })).toBeOK();
    const before = await serverJson(request, '/__test__/system/digest');
    await dialog(page).getByRole('button', { name: 'Apply reviewed operation', exact: true }).click();
    await expect(page).toHaveURL(/#\/activity\/[0-9a-f]{32}$/);
    await expect(toast(page)).toHaveText(`Duplicate prompt text failed · ${CHANGED}`);
    await expect(page.locator('#workspace-main')).toContainText(CHANGED);
    expect((await serverJson(request, '/__test__/system/digest')).rows).toEqual(before.rows);
    // S7 review M2: the failed job offers Review again, which reviews the same operation afresh.
    const previews = [];
    page.on('request', item => { if (item.url().endsWith(PREVIEW_URL)) previews.push(item.postDataJSON()); });
    await page.locator('#workspace-main').getByRole('button', { name: 'Review again', exact: true }).click();
    await expect(dialog(page).locator('#modal-title')).toHaveText('Review maintenance plan');
    await expect(dialog(page).locator('#modal-body > h3')).toHaveText('Duplicate prompt text');
    expect(previews).toEqual([{ operation: 'duplicate_prompts', options: {} }]);
});

test('a review button pressed twice sends one preview (S7 review M4)', async ({ page, request }) => {
    await expect(await request.post('/__test__/system/missing-file')).toBeOK();
    await expect(await request.post('/__test__/system/duplicate-files')).toBeOK();
    const previews = [];
    page.on('request', item => { if (item.url().endsWith(PREVIEW_URL)) previews.push(item.postDataJSON().operation); });
    await openMaintenance(page);
    await row(page, 'Integrity & relationship checks').getByRole('button', { name: 'Check', exact: true }).click();
    // Hold the preview at the server so the second press lands while it is read.
    await expect(await request.post('/__test__/hold', { data: { path: PREVIEW_URL } })).toBeOK();
    // The pressed button reads "Reviewing…" while held, so it is found by its place, not its name.
    const missing = dialog(page).locator('.dialog-actions > button').last();
    await expect(missing).toHaveText('Review missing links');
    await missing.click();
    await expect(missing).toHaveAttribute('aria-disabled', 'true');
    await expect(missing).toHaveText('Reviewing…');
    await missing.dispatchEvent('click');
    await expect.poll(async () => (await serverJson(request, '/__test__/hold')).held[PREVIEW_URL]).toBe(1);
    await expect(await request.post('/__test__/release')).toBeOK();
    await expect(dialog(page).locator('#modal-body > h3')).toHaveText('Missing-file links');
    expect(previews).toEqual(['missing_links']);
    await page.keyboard.press('Escape');

    await row(page, 'Duplicate image files').getByRole('button', { name: 'Find duplicates', exact: true }).click();
    await page.locator('#workspace-main').getByRole('button', { name: 'Review groups', exact: true }).click();
    await expect(await request.post('/__test__/hold', { data: { path: PREVIEW_URL } })).toBeOK();
    const cleanup = dialog(page).locator('.dialog-actions > button').last();
    await expect(cleanup).toHaveText('Review duplicate cleanup');
    await cleanup.click();
    await expect(cleanup).toHaveAttribute('aria-disabled', 'true');
    await cleanup.dispatchEvent('click');
    await expect.poll(async () => (await serverJson(request, '/__test__/hold')).held[PREVIEW_URL]).toBe(1);
    await expect(await request.post('/__test__/release')).toBeOK();
    await expect(dialog(page).locator('#modal-body > h3')).toHaveText('Duplicate image files');
    expect(previews).toEqual(['missing_links', 'duplicate_files']);
});

test('plan-used links the job that applied it', async ({ page, request }) => {
    await expect(await request.post('/__test__/system/duplicate-prompts')).toBeOK();
    // A double-submitted apply: the first request applies the plan, the second one's answer reaches the page.
    await page.route(`**${APPLY_URL}`, async route => {
        await route.fetch();
        await route.fulfill({ response: await route.fetch() });
    });
    await openMaintenance(page);
    await row(page, 'Duplicate prompt text').getByRole('button', { name: 'Review plan', exact: true }).click();
    await dialog(page).getByRole('button', { name: 'Apply reviewed operation', exact: true }).click();
    await expect(toast(page)).toHaveText('This plan was already applied: Duplicate prompt text.');
    const link = toast(page).getByRole('link', { name: 'Duplicate prompt text', exact: true });
    const { jobs } = await serverJson(request, '/prompt_manager/jobs');
    const applied = jobs.find(job => job.kind === 'maintenance');
    await expect(link).toHaveAttribute('href', `#/activity/${applied.id}`);
    await expect(page).toHaveURL(`${workspace}#/system/maintenance`);
});

/**
 * Record every toast the page shows (`[text, [link hrefs]]`): a job's finish toast may replace an earlier one before a
 * test reads it.
 */
async function recordToasts(page) {
    await page.addInitScript(() => {
        window.toastLog = [];
        const record = node => window.toastLog.push([node.textContent, [...node.querySelectorAll('a')].map(a => a.getAttribute('href'))]);
        new MutationObserver(() => {
            const node = document.getElementById('workspace-toast');
            const last = window.toastLog.at(-1);
            if (node && node.textContent && last?.[0] !== node.textContent) record(node);
        }).observe(document, { subtree: true, childList: true, characterData: true });
    });
    return () => page.evaluate(() => window.toastLog);
}

const NOT_CONFIRMED = ['Outcome not confirmed. Check Activity before applying it again.', ['#/activity']];

/** The maintenance jobs the server lists (the apply ran even though its answer was lost). */
async function maintenanceJobs(request) {
    return (await serverJson(request, '/prompt_manager/jobs')).jobs.filter(job => job.kind === 'maintenance');
}

test('lost apply response says Outcome not confirmed and points to Activity', async ({ page, request }) => {
    await expect(await request.post('/__test__/system/duplicate-prompts')).toBeOK();
    // The server applies the plan; its answer never reaches the page.
    const reads = [];
    await page.route(`**${APPLY_URL}`, async route => {
        await route.fetch();
        page.on('request', item => { if (item.url().endsWith('/prompt_manager/jobs')) reads.push(item.url()); });
        await route.abort('failed');
    });
    const toasts = await recordToasts(page);
    await openMaintenance(page);
    await row(page, 'Duplicate prompt text').getByRole('button', { name: 'Review plan', exact: true }).click();
    await dialog(page).getByRole('button', { name: 'Apply reviewed operation', exact: true }).click();
    await expect.poll(toasts).toContainEqual(NOT_CONFIRMED);
    await expect(page).toHaveURL(`${workspace}#/system/maintenance`);
    // The Activity list is read again at once, and the job the lost answer started is in it.
    await expect.poll(() => reads.length).toBeGreaterThan(0);
    expect((await maintenanceJobs(request)).map(job => job.title)).toEqual(['Duplicate prompt text']);
    await page.evaluate(() => { location.hash = '#/activity'; });
    await expect(page.locator('#workspace-main')).toContainText('Duplicate prompt text');
});

test('a proxy error or an unreadable answer is not a refusal: Outcome not confirmed (S7 review I1)', async ({ page, request }) => {
    await expect(await request.post('/__test__/system/duplicate-prompts')).toBeOK();
    const reads = [];
    // A reverse proxy times out after the server started the job.
    await page.route(`**${APPLY_URL}`, async route => {
        await route.fetch();
        page.on('request', item => { if (item.url().endsWith('/prompt_manager/jobs')) reads.push(item.url()); });
        await route.fulfill({ status: 504, contentType: 'text/html', body: '<html><body>Gateway Timeout</body></html>' });
    });
    const toasts = await recordToasts(page);
    await openMaintenance(page);
    await row(page, 'Duplicate prompt text').getByRole('button', { name: 'Review plan', exact: true }).click();
    await dialog(page).getByRole('button', { name: 'Apply reviewed operation', exact: true }).click();
    await expect.poll(toasts).toContainEqual(NOT_CONFIRMED);
    await expect.poll(() => reads.length).toBeGreaterThan(0);
    expect(await maintenanceJobs(request)).toHaveLength(1);
});

test('a refused apply names the running job', async ({ page, request }) => {
    await expect(await request.post('/__test__/system/duplicate-prompts')).toBeOK();
    await openMaintenance(page);
    await row(page, 'Duplicate prompt text').getByRole('button', { name: 'Review plan', exact: true }).click();
    await expect(dialog(page).locator('#modal-body > h3')).toHaveText('Duplicate prompt text');
    const hold = await (await request.post('/__test__/jobs/hold', { data: { title: 'Database restore (admin page)' } })).json();
    await dialog(page).getByRole('button', { name: 'Apply reviewed operation', exact: true }).click();
    await expect(toast(page)).toHaveText('Another job is running: Database restore (admin page).');
    await expect(toast(page).getByRole('link')).toHaveAttribute('href', `#/activity/${hold.job.id}`);
    await expect(page).toHaveURL(`${workspace}#/system/maintenance`);
});

test('Find duplicates starts a job; its view links Review groups; Keep radios build a duplicate_files plan (D101)',
    async ({ page, request }) => {
        await expect(await request.post('/__test__/system/duplicate-files')).toBeOK();
        const previews = [];
        page.on('request', item => { if (item.url().endsWith(PREVIEW_URL)) previews.push(item.postDataJSON()); });
        await openMaintenance(page);
        await row(page, 'Duplicate image files').getByRole('button', { name: 'Find duplicates', exact: true }).click();
        await expect(page).toHaveURL(/#\/activity\/[0-9a-f]{32}$/);
        const jobId = page.url().split('/').pop();
        const main = page.locator('#workspace-main');
        await expect(main.locator('h1')).toHaveText('Find duplicate image files');
        const reviewGroups = main.getByRole('button', { name: 'Review groups', exact: true });
        await expect(reviewGroups).toBeVisible();
        await expect(main.locator('.stat-row .stat dt')).toHaveText(['Groups found']);
        await reviewGroups.click();
        const review = dialog(page);
        await expect(review.locator('#modal-title')).toHaveText('Review duplicate image files');
        const group = review.locator('.compare-grid').filter({ hasText: 's7-dupes/copy.png' });
        await expect(group.locator('.compare-item img')).toHaveCount(3);
        const labels = (await group.locator('.compare-item label').allTextContents()).map(text => text.trim());
        expect([...labels].sort()).toEqual(['Keep input/s7-dupes/same.png', 'Keep output/s7-dupes/copy.png',
            'Keep output/s7-dupes/same.png']);
        await expect(review.locator('#modal-body > p.small').last())
            .toHaveText(/History keeps the removed paths in its run records\./);
        // Keep the input copy: the same relative path as the output copy, told apart by its source index.
        await group.locator('.compare-item').filter({ hasText: 'input/s7-dupes/same.png' }).getByRole('radio').check();
        await review.getByRole('button', { name: 'Review duplicate cleanup', exact: true }).click();
        await expect(dialog(page).locator('#modal-title')).toHaveText('Review maintenance plan');
        await expect(dialog(page).locator('#modal-body > h3')).toHaveText('Duplicate image files');
        expect(previews).toHaveLength(1);
        expect(previews[0].operation).toBe('duplicate_files');
        expect(previews[0].options.job_id).toBe(jobId);
        expect(previews[0].options.keep).toContainEqual(expect.objectContaining({ root_index: 1, path: 's7-dupes/same.png' }));
        for (const entry of previews[0].options.keep) expect(Object.keys(entry).sort()).toEqual(['group_id', 'path', 'root_index']);
        expect((await gridOf(page))[3][1]).toMatch(/^Deletes 2 files \(.+\) from disk; the kept copies stay\./);
        await dialog(page).getByRole('button', { name: 'Apply reviewed operation', exact: true }).click();
        await expect(page).not.toHaveURL(new RegExp(jobId));
        await expect(page).toHaveURL(/#\/activity\/[0-9a-f]{32}$/);
        const cleanup = await ended(request, page.url().split('/').pop());
        expect([cleanup.kind, cleanup.state, cleanup.counters.removed]).toEqual(['duplicate_cleanup', 'finished', 2]);
        await expect(main.locator('.stat-row .stat dt')).toHaveText(['Removed', 'Kept', 'Errors']);
        expect((await request.get('/prompt_manager/images/serve/s7-dupes/same.png?root=1')).status()).toBe(200);
        expect((await request.get('/prompt_manager/images/serve/s7-dupes/same.png?root=0')).status()).toBe(404);
        expect((await request.get('/prompt_manager/images/serve/s7-dupes/copy.png?root=0')).status()).toBe(404);
    });

test('the duplicates review pages ten groups at a time and keeps each choice (D101)', async ({ page, request }) => {
    await expect(await request.post('/__test__/system/duplicate-files', { data: { groups: 11 } })).toBeOK();
    const groupReads = [];
    page.on('request', item => { if (item.url().includes('/maintenance/duplicates/')) groupReads.push(new URL(item.url()).search); });
    const previews = [];
    page.on('request', item => { if (item.url().endsWith(PREVIEW_URL)) previews.push(item.postDataJSON()); });
    await openMaintenance(page);
    await row(page, 'Duplicate image files').getByRole('button', { name: 'Find duplicates', exact: true }).click();
    const main = page.locator('#workspace-main');
    await main.getByRole('button', { name: 'Review groups', exact: true }).click();
    const review = dialog(page);
    const status = review.locator('#modal-body > p.small[role=status]');
    await expect(status).toHaveText('Groups 1–10 of 12');
    await expect(review.locator('.compare-grid')).toHaveCount(10);
    await expect(review.getByRole('button', { name: 'Previous groups', exact: true })).toBeDisabled();
    // The three-copy group sorts first (the most bytes to free): keep its input copy.
    const first = review.locator('.compare-grid').first();
    await first.locator('.compare-item').filter({ hasText: 'input/s7-dupes/same.png' }).getByRole('radio').check();
    await review.getByRole('button', { name: 'Next groups', exact: true }).click();
    await expect(status).toHaveText('Groups 11–12 of 12');
    await expect(review.locator('.compare-grid')).toHaveCount(2);
    await expect(review.getByRole('button', { name: 'Next groups', exact: true })).toBeDisabled();
    await review.getByRole('button', { name: 'Previous groups', exact: true }).click();
    await expect(status).toHaveText('Groups 1–10 of 12');
    await expect(first.locator('.compare-item').filter({ hasText: 'input/s7-dupes/same.png' }).getByRole('radio')).toBeChecked();
    expect(groupReads).toEqual(['?offset=0&limit=10', '?offset=10&limit=10', '?offset=0&limit=10']);
    await review.getByRole('button', { name: 'Review duplicate cleanup', exact: true }).click();
    await expect(dialog(page).locator('#modal-body > h3')).toHaveText('Duplicate image files');
    // Every group shown so far is reviewed, each with its chosen copy.
    expect(previews[0].options.keep).toHaveLength(12);
    expect(previews[0].options.keep).toContainEqual(expect.objectContaining({ root_index: 1, path: 's7-dupes/same.png' }));
    await dialog(page).getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(dialog(page)).toHaveCount(0);
});

test('Thumbnail cache opens the S5 dialog', async ({ page }) => {
    await openMaintenance(page);
    const options = row(page, 'Thumbnail cache').getByRole('button', { name: 'Review options', exact: true });
    await options.click();
    await expect(dialog(page).locator('#modal-title')).toHaveText('Thumbnail maintenance');
    await expect(dialog(page).locator('.field > label')).toHaveText(['Scope']);
    await page.keyboard.press('Escape');
    await expect(options).toBeFocused();
});
