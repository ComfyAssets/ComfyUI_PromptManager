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
