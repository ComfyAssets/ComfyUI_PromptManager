// #328: the legacy admin and gallery auto-tag tools keep working beside System › Auto-tagging. The model download is
// the `model_download` Activity job; the pages' stream attaches to it. No model is ever downloaded: the fixture's
// fake download waits until the test releases it (`/__test__/tagging/download`).
import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';

const TITLE = 'Tagging model download · WD14 ViT';

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

async function startDownload(page, request) {
    await page.locator('#autoTagBtn').click();
    const status = page.locator('#wd14VitModelStatus');
    await status.getByRole('button', { name: 'Download', exact: true }).click();
    const modal = page.locator('#autoTagDownloadModal');
    await expect(modal).toBeVisible();
    await expect(page.locator('#downloadStatus')).toHaveText('Downloading…');
    await expect(page.locator('#downloadProgressBar')).toHaveClass(/animate-pulse/);
    await expect(modal).toContainText('The download continues in Activity.');
    await expect(page.locator('#cancelDownloadBtn')).toHaveText('Close');
    const running = (await downloads(request)).filter(job => job.state === 'running');
    expect(running.map(job => [job.title, job.cancellable])).toEqual([[TITLE, false]]);
    return { modal, status };
}

test('admin download modal closes on complete and Activity lists the job without Cancel', async ({ page, request }) => {
    await page.goto('/prompt_manager/admin');
    const { modal, status } = await startDownload(page, request);

    await page.goto('/prompt_manager/workspace#/activity');
    const item = page.locator('#workspace-main section.surface > .activity-item')
        .filter({ has: page.getByRole('link', { name: new RegExp(`${TITLE}$`) }) });
    await expect(item.locator('.chip')).toHaveText('Running');
    await expect(item.locator('progress')).not.toHaveAttribute('value');
    await expect(item.getByRole('button', { name: 'Cancel job', exact: true })).toHaveCount(0);

    // Back on the admin page a second download of the same model attaches to the running job.
    await page.goto('/prompt_manager/admin');
    await startDownload(page, request);
    await post(request, '/__test__/tagging/download', { mode: 'succeed' });
    await expect(modal).toBeHidden();
    await expect(status).toContainText('Downloaded');
    expect((await downloads(request)).map(job => [job.title, job.state])).toEqual([[TITLE, 'finished']]);
});

test('gallery download modal closes on complete', async ({ page, request }) => {
    await page.goto('/prompt_manager/gallery');
    const { modal, status } = await startDownload(page, request);
    await post(request, '/__test__/tagging/download', { mode: 'succeed' });
    await expect(modal).toBeHidden();
    await expect(page.locator('#autoTagModal')).toBeVisible();
    await expect(status).toContainText('Downloaded');
    expect((await downloads(request)).map(job => job.state)).toEqual(['finished']);
});

test('a failed download closes the modal and says so', async ({ page, request }) => {
    await page.goto('/prompt_manager/gallery');
    const { modal, status } = await startDownload(page, request);
    await post(request, '/__test__/tagging/download', { mode: 'fail' });
    await expect(modal).toBeHidden();
    await expect(page.getByText('Download failed: Download failed')).toBeVisible();
    await expect(status.getByRole('button', { name: 'Download', exact: true })).toBeVisible();
    expect((await downloads(request)).map(job => job.state)).toEqual(['failed']);
});

// (wd14-vit is not downloaded in these tests: beforeEach marks it so the download tests can offer it.)
// The gallery's batch reader keyed on `data.status` values the server never sends, so its modal never moved or
// closed (#328 Problem 5). It now reads `type` frames as the admin page does.
async function watchProgress(page) {
    await page.evaluate(() => {
        window.progressSeen = [];
        const node = document.getElementById('autoTagProgressPercent');
        new MutationObserver(() => window.progressSeen.push(node.textContent))
            .observe(node, { childList: true, characterData: true, subtree: true });
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
    await expect(page.getByText(/^Auto tagging complete! Applied tags to \d+ prompts\.$/)).toBeVisible();
}

test('gallery batch modal counts progress and closes on complete', async ({ page, request }) => {
    await page.goto('/prompt_manager/gallery');
    await runGalleryBatch(page);
    const seen = await page.evaluate(() => window.progressSeen);
    // The fixture's prompts are already tagged, so the batch skips them; its progress frames still move the bar.
    expect(seen).toContain('95%');
    expect((await downloads(request)).length).toBe(0);
});

test('admin review opens the first image', async ({ page }) => {
    await page.goto('/prompt_manager/admin');
    await page.locator('#autoTagBtn').click();
    await page.locator('input[name="autoTagModel"][value="wd14-swinv2"]').check();
    await page.locator('#startReviewBtn').click();
    // The first image's prompt is already tagged: confirm a new pass, then the review opens on that image.
    await page.locator('#retagConfirmBtn').click();
    await expect(page.locator('#autoTagReviewModal')).toBeVisible();
    await expect(page.locator('#reviewImageName')).not.toHaveText(/^(image_name\.png|Unknown|)$/);
    await expect(page.getByText('Failed to start review mode')).toHaveCount(0);
});

test('gallery unload works and is refused while the gate is held', async ({ page, request }) => {
    await page.goto('/prompt_manager/gallery');
    await runGalleryBatch(page); // keeps the model in memory
    await page.locator('#autoTagBtn').click();
    const unload = page.locator('#unloadModelBtn');
    await expect(unload).toBeVisible();

    // A held download owns the model gate: Unload is refused with the server's error.
    const held = await request.post('/prompt_manager/jobs/model-download', { data: { model_type: 'gguf' } });
    expect(held.status()).toBe(202);
    await unload.click();
    await expect(page.getByText(/^Failed to unload model: A tagging model is busy/)).toBeVisible();

    await post(request, '/__test__/tagging/download', { mode: 'succeed' });
    await expect.poll(async () => (await downloads(request)).map(job => job.state)).toEqual(['finished']);
    await unload.click();
    await expect(page.getByText('WD14-SWINV2 model unloaded successfully')).toBeVisible();
});
