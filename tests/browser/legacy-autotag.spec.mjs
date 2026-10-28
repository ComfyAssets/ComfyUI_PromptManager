// #328: the legacy admin and gallery auto-tag batch and review keep working beside System › Auto-tagging. Since #330
// the pages download and unload models only through System (legacy-system-links.spec.mjs); a batch never downloads
// one. The fixture's fake download (`/__test__/tagging/download`) is held so nothing is ever fetched.
import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';

async function post(request, url, data) {
    await expect(await request.post(url, data === undefined ? {} : { data })).toBeOK();
}

async function downloads(request) {
    const response = await request.get('/prompt_manager/jobs');
    await expect(response).toBeOK();
    return (await response.json()).jobs.filter(job => job.kind === 'model_download');
}

test.beforeEach(async ({ page, request, browserName }) => {
    await startCoverage(page, browserName);
    // The 3.2.0 update notice would cover the admin page's toolbar.
    await page.addInitScript(() => { localStorage.setItem('pm_update_notice_dismissed', '3.2.0'); });
    await post(request, '/__test__/jobs/restart');
    await post(request, '/__test__/tagging/reset');
    await post(request, '/__test__/tagging/models', { unavailable: ['wd14-vit'] });
    await post(request, '/__test__/tagging/download', { mode: 'hold' });
});

test.afterEach(async ({ page, request, browserName }) => {
    await saveCoverage(page, browserName);
    // Releases a download a failed test left held, and puts the raising default back.
    await post(request, '/__test__/tagging/reset');
    await post(request, '/__test__/jobs/restart');
});

// (wd14-vit is not downloaded in these tests: beforeEach marks it so the download tests can offer it.)
// The gallery's batch reader keyed on `data.status` values the server never sends, so its modal never moved or
// closed (#328 Problem 5). It now reads `type` frames as the admin page does.
async function watchProgress(page) {
    await page.evaluate(() => {
        window.progressSeen = [];
        const node = document.getElementById('autoTagProgressPercent');
        // Each change from its own record: frames that land in one task share one
        // callback, and the node's text by then is only the last of them.
        new MutationObserver(records => {
            for (const record of records) {
                if (record.type === 'characterData') window.progressSeen.push(record.target.data);
                for (const added of record.addedNodes) window.progressSeen.push(added.textContent);
            }
        }).observe(node, { childList: true, characterData: true, subtree: true });
    });
}

async function runGalleryBatch(page) {
    await page.locator('#autoTagBtn').click();
    await page.locator('input[name="autoTagModel"][value="wd14-swinv2"]').check();
    await watchProgress(page);
    const done = page.waitForResponse(response => response.url().includes('/prompt_manager/autotag/start'));
    await page.locator('#startAutoTagBtn').click();
    await done;
    await expect(page.locator('#autoTagProgressModal')).toBeHidden();
    // #379: the batch counts images, never prompts.
    await expect(page.getByText(/^Auto tagging complete! Tagged \d+ images?\.$/)).toBeVisible();
}

const CLOSE_NOTE = 'Closing this window stops tagging. Tags already added are kept. Prompt Manager\'s Activity page shows the '
    + 'time left.';

async function serverJobs(request) {
    const response = await request.get('/prompt_manager/jobs');
    await expect(response).toBeOK();
    return (await response.json()).jobs;
}

async function runNativeBatch(request) {
    const started = await request.post('/prompt_manager/jobs/autotag', { data: { model_type: 'wd14-swinv2', existing: 'skip',
        options: { general_threshold: 0.35, character_threshold: 0.85 } } });
    expect(started.status()).toBe(202);
    const { job } = await started.json();
    await expect.poll(async () => (await serverJobs(request)).find(item => item.id === job.id)?.state).toBe('finished');
}

test('gallery batch modal counts progress and closes on complete', async ({ page, request }) => {
    await page.goto('/prompt_manager/gallery');
    await runGalleryBatch(page);
    const seen = await page.evaluate(() => window.progressSeen);
    // The phase frames (model 5%, listing 8%) then the per-image frames (from 10%) move the bar.
    expect(seen).toContain('8%');
    expect(seen.some(value => parseInt(value, 10) >= 10)).toBe(true);
    await expect(page.locator('#autoTagCloseNote')).toHaveText(CLOSE_NOTE);
    // The batch ran as the autotag job of the legacy page.
    expect((await serverJobs(request)).filter(job => job.kind === 'autotag').map(job => [job.title, job.state]))
        .toEqual([['Tag output images (legacy page)', 'finished']]);
    expect((await downloads(request)).length).toBe(0);
});

test('admin batch copy names images, not prompts (#379)', async ({ page }) => {
    await page.goto('/prompt_manager/admin');
    await page.locator('#autoTagBtn').click();
    await expect(page.locator('input[name="autoTagMode"][value="skip"] + span')).toHaveText('Skip images already auto-tagged');
    await expect(page.getByText('"auto-scanned" is not counted as a real tag')).toHaveCount(0);
    await expect(page.locator('#autoTagCloseNote')).toHaveText(CLOSE_NOTE);
    await expect(page.locator('#retagConfirmModal h3')).toHaveText('Image Already Auto-tagged');
    await expect(page.locator('#retagConfirmModal')).toContainText('A tagging model already tagged this image. Generate new tags?');
});

test('admin review opens the first image', async ({ page }) => {
    await page.goto('/prompt_manager/admin');
    await page.locator('#autoTagBtn').click();
    await page.locator('input[name="autoTagModel"][value="wd14-swinv2"]').check();
    await page.locator('#startReviewBtn').click();
    // #379: no image is auto-tagged yet (prompt tags no longer count), so the review opens on the first image.
    await expect(page.locator('#retagConfirmModal')).toBeHidden();
    await expect(page.locator('#autoTagReviewModal')).toBeVisible();
    await expect(page.locator('#reviewImageName')).not.toHaveText(/^(image_name\.png|Unknown|)$/);
    await expect(page.getByText('Failed to start review mode')).toHaveCount(0);
});

/** The admin review lists every row, videos too: Skip any image that is not one until the confirmation shows. */
async function untilAutoTagged(page, confirm) {
    // Each image's skip check answers before its confirmation or its error shows.
    const failed = page.locator('#reviewTagsVisible .text-pm-error');
    for (let tries = 0; tries < 5; tries++) {
        await expect.poll(async () => (await confirm.isVisible()) || (await failed.count()) > 0).toBe(true);
        if (await confirm.isVisible()) return;
        const answered = page.waitForResponse(item => item.url().endsWith('/prompt_manager/autotag/single'));
        await page.locator('#skipReviewBtn').click();
        await answered;
    }
    await expect(confirm).toBeVisible();
}

// #379: the admin review asks before re-tagging an image a model already tagged, and Apply & Next writes that image's
// tags (its bytes, never its prompt).
test('admin review: an auto-tagged image asks first; Re-tag then Apply & Next tags the image', async ({ page, request }) => {
    await runNativeBatch(request);
    await page.goto('/prompt_manager/admin');
    await page.locator('#autoTagBtn').click();
    await page.locator('input[name="autoTagModel"][value="wd14-swinv2"]').check();
    await page.locator('#startReviewBtn').click();
    const confirm = page.locator('#retagConfirmModal');
    await untilAutoTagged(page, confirm);
    await expect(confirm.locator('#retagExistingTags span').first()).toHaveText('sandstone');
    await page.locator('#retagConfirmBtn').click();
    await expect(page.locator('#autoTagReviewModal')).toBeVisible();
    await expect(page.locator('#reviewTagsVisible .tag-chip').first()).toContainText('sandstone');
    const applied = page.waitForRequest(item => item.url().endsWith('/prompt_manager/autotag/apply'));
    const answer = page.waitForResponse(item => item.url().endsWith('/prompt_manager/autotag/apply'));
    await page.locator('#applyReviewBtn').click();
    const body = (await applied).postDataJSON();
    expect(body.prompt_id).toBeUndefined();
    expect(body.sha256).toMatch(/^[0-9a-f]{64}$/);
    expect((await answer).status()).toBe(200);
});

test('admin review: Skip All Tagged passes every auto-tagged image', async ({ page, request }) => {
    await runNativeBatch(request);
    await page.goto('/prompt_manager/admin');
    await page.locator('#autoTagBtn').click();
    await page.locator('input[name="autoTagModel"][value="wd14-swinv2"]').check();
    await page.locator('#startReviewBtn').click();
    const confirm = page.locator('#retagConfirmModal');
    await untilAutoTagged(page, confirm);
    await page.locator('#retagSkipAllBtn').click();
    await expect(page.locator('#retagConfirmModal')).toBeHidden();
    await expect(page.getByText('Review complete!')).toBeVisible();
});

test('gallery review: Apply & Next tags the image by its path and bytes', async ({ page }) => {
    await page.goto('/prompt_manager/gallery');
    await page.locator('#autoTagBtn').click();
    await page.locator('input[name="autoTagModel"][value="wd14-swinv2"]').check();
    const suggested = page.waitForRequest(item => item.url().endsWith('/prompt_manager/autotag/single'));
    await page.locator('#startReviewBtn').click();
    expect((await suggested).postDataJSON().existing).toBe('merge');
    await expect(page.locator('#reviewTagsContainer .tag-chip').first()).toContainText('sandstone');
    const applied = page.waitForRequest(item => item.url().endsWith('/prompt_manager/autotag/apply'));
    const answer = page.waitForResponse(item => item.url().endsWith('/prompt_manager/autotag/apply'));
    await page.locator('#applyReviewBtn').click();
    const body = (await applied).postDataJSON();
    expect(Object.keys(body).sort()).toEqual(['path', 'root_index', 'sha256', 'tags'].filter(key => key in body).sort());
    expect(body.prompt_id).toBeUndefined();
    expect((await answer).status()).toBe(200);
});
