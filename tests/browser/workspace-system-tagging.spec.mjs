import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';

// System › Auto-tagging (#328, System S11 Tasks 6–7) on the tagging fixture (workspace_tagging_fixture.py) with the
// injected fake model: no model file exists and nothing is downloaded or run for real. The fake download waits on the
// fixture (`/__test__/tagging/download {mode}`), and every reset releases it. Every test asserts that no legacy
// download stream (`/autotag/download/`) was requested: the section downloads only through the Activity job.
const workspace = '/prompt_manager/workspace';
const MODELS_URL = '/prompt_manager/autotag/models';
const PREVIEW_URL = '/prompt_manager/autotag/batch/preview';
const SETTINGS_URL = '/prompt_manager/settings';
const MODEL_LABELS = ['WD14 SwinV2 · ~400 MB', 'WD14 ViT · ~400 MB', 'GGUF JoyCaption · ~4.5 GB', 'HuggingFace JoyCaption · ~16 GB'];
const FIRST = 'tagging/dunes_00001.png';
const SECOND = 'tagging/dunes_00002.png';

test.beforeEach(async ({ page, request, browserName }) => {
    await startCoverage(page, browserName);
    await expect(await request.post('/__test__/tagging/reset', { data: {} })).toBeOK();
    await expect(await request.post('/__test__/system/reset')).toBeOK();
    await expect(await request.post('/__test__/jobs/restart')).toBeOK();
    page.systemErrors = [];
    page.on('pageerror', error => page.systemErrors.push(error.message));
    page.downloads = [];
    page.modelCalls = [];
    page.on('request', item => {
        const { pathname } = new URL(item.url());
        if (pathname.includes('/autotag/download/')) page.downloads.push(pathname);
        if (pathname.startsWith('/prompt_manager/autotag/') || pathname.startsWith('/prompt_manager/image-annotations/suggest')
            || pathname.startsWith('/prompt_manager/jobs/model-download')) page.modelCalls.push(`${item.method()} ${pathname}`);
    });
});

test.afterEach(async ({ page, request, browserName }) => {
    await saveCoverage(page, browserName);
    // The reset releases a held fake download, so no runner outlives the test.
    await request.post('/__test__/tagging/reset', { data: {} });
    await request.post('/__test__/jobs/restart');
    await request.post('/__test__/system/reset');
    expect(page.systemErrors).toEqual([]);
    expect(page.downloads).toEqual([]);
});

const toast = page => page.locator('#workspace-toast');
const layoutOf = page => page.locator('#workspace-main .settings-layout');
const dialog = page => page.locator('dialog#modal[open]');
const status = page => page.locator('#settings-status');
const save = page => page.getByRole('button', { name: 'Save preferences', exact: true });
const surface = page => layoutOf(page).locator(':scope > section.surface');
const settingOf = (page, label) => layoutOf(page).locator('.setting-row').filter({ has: page.locator(`label:text-is("${label}")`) });
const modelRow = page => settingOf(page, 'Tagging model');
const actionOf = (page, name) => surface(page).locator(':scope > .row-actions').getByRole('button', { name, exact: true });
const reviewButton = page => surface(page).locator('.operation').getByRole('button', { name: 'Review', exact: true });
const card = (page, path) => page.locator(`article[data-image-key="0:${path}"]`);
const tagDialog = page => page.getByRole('dialog', { name: 'Tag image', exact: true });
const gridOf = page => dialog(page).locator('dl.meta-grid > div').evaluateAll(items => items.map(item =>
    [item.querySelector('dt').textContent, item.querySelector('dd').textContent]));

async function serverJson(request, url) {
    const response = await request.get(url);
    await expect(response).toBeOK();
    return response.json();
}

async function fakeDownload(request, mode) {
    await expect(await request.post('/__test__/tagging/download', { data: { mode } })).toBeOK();
}

async function unavailable(request, models) {
    await expect(await request.post('/__test__/tagging/models', { data: { unavailable: models } })).toBeOK();
}

async function ended(request, id) {
    await expect.poll(async () => (await serverJson(request, `/prompt_manager/jobs/${id}`)).job.state).not.toBe('running');
    return (await serverJson(request, `/prompt_manager/jobs/${id}`)).job;
}

/** System › Auto-tagging on a fresh page (the drafts live for the page session, Q10), settled on the model status. */
async function openTagging(page) {
    if (page.url().endsWith('#/system/tagging')) await page.reload();
    else await page.goto(`${workspace}#/system/tagging`);
    await expect(surface(page)).toHaveCount(1);
    await expect(page.locator('#tag-model')).toBeEnabled();
    await expect(modelRow(page).locator('p').first()).not.toHaveText('Loading model status…');
}

/** Select `path` in Images (the concept's `state.selectedImage`), then open System › Auto-tagging from the sidebar. */
async function selectThenOpen(page, path) {
    await page.goto(`${workspace}#/images`);
    await card(page, path).getByRole('button', { name: `Inspect ${path.split('/').pop()}`, exact: true }).click();
    await expect(card(page, path)).toHaveClass(/\bselected\b/);
    await page.locator('aside.sidebar a[href="#/system"]').click();
    await page.locator('#workspace-main .section-nav').getByRole('button', { name: 'Auto-tagging', exact: true }).click();
    await expect(page.locator('#tag-model')).toBeEnabled();
    await expect(modelRow(page).locator('p').first()).not.toHaveText('Loading model status…');
}

test('model options are the four real engines with size and the description shows live status', async ({ page, request }) => {
    await unavailable(request, ['wd14-vit', 'hf']);
    await openTagging(page);
    expect(await surface(page).locator(':scope > h2').textContent()).toBe('Auto-tagging');
    expect(await page.locator('#tag-model option').allTextContents()).toEqual(MODEL_LABELS);
    await expect(page.locator('#tag-model')).toHaveValue('wd14-swinv2');
    const line = modelRow(page).locator('p').first();
    await expect(line).toHaveText('Downloaded. Not loaded.');
    await expect(actionOf(page, 'Download again')).toBeVisible();
    // Choosing another engine drafts it and tells its own status; nothing is downloaded or loaded.
    await page.locator('#tag-model').selectOption('wd14-vit');
    await expect(line).toHaveText('Not downloaded. Download size ~400 MB.');
    await expect(actionOf(page, 'Download')).toBeVisible();
    await page.locator('#tag-model').selectOption('hf');
    await expect(line).toHaveText('Not downloaded. Download size ~16 GB.');
    await expect(status(page)).toHaveText('Unsaved changes.');
    // The other rows keep the concept's copy and the saved values (D133 adds Suggestions per image).
    expect(await surface(page).locator(':scope > .setting-row label').allTextContents()).toEqual(['Tagging model', 'Existing tags',
        'General-tag confidence', 'Character-tag confidence', 'Suggestions per image']);
    await expect(page.locator('#tag-existing')).toHaveValue('skip');
    await expect(page.locator('#threshold-general')).toHaveValue('0.35');
    await expect(page.locator('#threshold-character')).toHaveValue('0.85');
    await expect(page.locator('#tag-suggestions')).toHaveValue('10');
    const settings = await serverJson(request, SETTINGS_URL);
    await expect(page.locator('#tag-template')).toHaveValue(settings.settings.autotag_template);
    // Unload is offered only while a model is loaded (none is).
    await expect(actionOf(page, 'Unload')).toBeDisabled();
});

test('entering the section makes no model call besides status', async ({ page }) => {
    await openTagging(page);
    await page.locator('#workspace-main .section-nav').getByRole('button', { name: 'Integrations', exact: true }).click();
    await page.locator('#workspace-main .section-nav').getByRole('button', { name: 'Auto-tagging', exact: true }).click();
    await expect(page.locator('#tag-model')).toBeEnabled();
    await expect(modelRow(page).locator('p').first()).toHaveText('Downloaded. Not loaded.');
    // One status read per mount (two mounts), and nothing else that could load, run or download a model.
    expect(page.modelCalls).toEqual([`GET ${MODELS_URL}`, `GET ${MODELS_URL}`]);
    const models = await (await page.request.get(MODELS_URL)).json();
    expect(models.model_loaded).toBe(false);
});

test('defaults save through Save preferences and the Tag image dialog uses them', async ({ page, request }) => {
    await openTagging(page);
    await page.locator('#tag-existing').selectOption('merge');
    await page.locator('#threshold-general').fill('0.4');
    await page.locator('#threshold-general').press('Tab');
    await page.locator('#tag-suggestions').fill('15');
    await page.locator('#tag-suggestions').press('Tab');
    const posted = page.waitForRequest(item => item.method() === 'POST' && new URL(item.url()).pathname === SETTINGS_URL);
    await save(page).click();
    const body = (await posted).postDataJSON();
    expect(body).toMatchObject({ autotag_existing: 'merge', autotag_general_threshold: 0.4, autotag_suggestion_limit: 15 });
    expect(typeof body.expected_revision).toBe('string');
    await expect(status(page)).toHaveText('Saved to the server configuration.');
    const saved = (await serverJson(request, SETTINGS_URL)).settings;
    expect([saved.autotag_existing, saved.autotag_general_threshold, saved.autotag_character_threshold, saved.autotag_suggestion_limit])
        .toEqual(['merge', 0.4, 0.85, 15]);
    const models = await serverJson(request, MODELS_URL);
    expect([models.existing, models.wd14_general_threshold, models.suggestion_limit]).toEqual(['merge', 0.4, 15]);
    // The Tag image dialog in a fresh page starts from them.
    await page.goto(`${workspace}#/images`);
    await card(page, FIRST).getByRole('button', { name: `Tag or auto-tag ${FIRST.split('/').pop()}`, exact: true }).click();
    await expect(tagDialog(page)).toBeVisible();
    await expect(tagDialog(page).locator('#image-general-threshold')).toHaveValue('0.4');
    await expect(tagDialog(page).locator('#image-character-threshold')).toHaveValue('0.85');
    await expect(tagDialog(page).locator('#image-suggestion-limit')).toHaveValue('15');
});

test('a refused value shows the server\'s message', async ({ page, request }) => {
    const before = (await serverJson(request, SETTINGS_URL)).settings;
    // An image is selected, so Preview tagging review is held back only by the invalid value below.
    await selectThenOpen(page, FIRST);
    await page.locator('#threshold-general').fill('1.5');
    await page.locator('#threshold-general').press('Tab');
    await save(page).click();
    await expect(status(page)).toHaveText('General-tag confidence must be between 0 and 1.');
    await expect(page.locator('#threshold-general')).toHaveAttribute('aria-invalid', 'true');
    // Nothing was written; the draft is kept for the next Save.
    expect((await serverJson(request, SETTINGS_URL)).settings.autotag_general_threshold).toBe(before.autotag_general_threshold);
    await expect(page.locator('#threshold-general')).toHaveValue('1.5');
    // While the value is invalid, Preview tagging review is aria-disabled and explains instead of opening (Playwright
    // waits on an aria-disabled control, so it is pressed from the keyboard, as a user can).
    await expect(actionOf(page, 'Preview tagging review')).toHaveAttribute('aria-disabled', 'true');
    await actionOf(page, 'Preview tagging review').focus();
    await page.keyboard.press('Enter');
    await expect(toast(page)).toHaveText('General-tag confidence must be between 0 and 1.');
    await expect(tagDialog(page)).toHaveCount(0);
    await page.locator('#threshold-general').fill('0.5');
    await page.locator('#threshold-general').press('Tab');
    await expect(page.locator('#threshold-general')).not.toHaveAttribute('aria-invalid', 'true');
    await expect(status(page)).toHaveText('Unsaved changes.');
    await expect(actionOf(page, 'Preview tagging review')).not.toHaveAttribute('aria-disabled', 'true');
});

test('a value off the 0.05 step is refused and never reaches the Tag image dialog (#369)', async ({ page, request }) => {
    const before = (await serverJson(request, SETTINGS_URL)).settings;
    const STEP = 'General-tag confidence must be a multiple of 0.05.';
    await selectThenOpen(page, FIRST);
    await page.locator('#threshold-general').fill('0.37');
    await page.locator('#threshold-general').press('Tab');
    // Preview tagging review explains instead of opening a dialog whose Generate the browser would block.
    await expect(actionOf(page, 'Preview tagging review')).toHaveAttribute('aria-disabled', 'true');
    await actionOf(page, 'Preview tagging review').focus();
    await page.keyboard.press('Enter');
    await expect(toast(page)).toHaveText(STEP);
    await expect(tagDialog(page)).toHaveCount(0);
    // The server refuses the save with the same sentence and writes nothing.
    await save(page).click();
    await expect(status(page)).toHaveText(STEP);
    expect((await serverJson(request, SETTINGS_URL)).settings.autotag_general_threshold).toBe(before.autotag_general_threshold);
    // A value on the step opens the dialog, and its Generate passes the browser's own check and sends.
    await page.locator('#threshold-general').fill('0.4');
    await page.locator('#threshold-general').press('Tab');
    await expect(actionOf(page, 'Preview tagging review')).not.toHaveAttribute('aria-disabled', 'true');
    await actionOf(page, 'Preview tagging review').click();
    await expect(tagDialog(page).locator('#image-general-threshold')).toHaveValue('0.4');
    expect(await tagDialog(page).locator('#image-general-threshold').evaluate(input => input.checkValidity())).toBe(true);
    const suggested = page.waitForResponse(item => item.url().endsWith('/image-annotations/suggest'));
    await tagDialog(page).getByRole('button', { name: 'Generate suggestions', exact: true }).click();
    const answer = await suggested;
    expect(answer.request().postDataJSON().options.general_threshold).toBe(0.4);
});

test('Download starts the job and Activity shows it without Cancel', async ({ page, request }) => {
    await unavailable(request, ['wd14-vit']);
    await fakeDownload(request, 'hold');
    await openTagging(page);
    await page.locator('#tag-model').selectOption('wd14-vit');
    const started = page.waitForResponse(item => new URL(item.url()).pathname === '/prompt_manager/jobs/model-download');
    await actionOf(page, 'Download').click();
    const answer = await started;
    expect(answer.status()).toBe(202);
    expect(answer.request().postDataJSON()).toEqual({ model_type: 'wd14-vit' });
    const { job } = await answer.json();
    expect(job.title).toBe('Tagging model download · WD14 ViT');
    await expect(toast(page)).toHaveText('Tagging model download · WD14 ViT started.');
    await expect(page).toHaveURL(`${workspace}#/activity/${job.id}`);
    const main = page.locator('#workspace-main');
    await expect(main.locator('h1')).toHaveText('Tagging model download · WD14 ViT');
    await expect(main.getByRole('button', { name: /^Cancel/ })).toHaveCount(0);
    // Indeterminate: the server knows no byte progress.
    await expect(main.locator('progress').first()).toBeVisible();
    expect(await main.locator('progress').first().evaluate(node => node.hasAttribute('value'))).toBe(false);
    await fakeDownload(request, 'succeed');
    expect((await ended(request, job.id)).state).toBe('finished');
    await expect(toast(page)).toHaveText('Tagging model download · WD14 ViT finished.');
    // Back in the section, the engine reads as on disk (the fake marks it downloaded on success).
    await page.goto(`${workspace}#/system/tagging`);
    await expect(page.locator('#tag-model')).toBeEnabled();
    await page.locator('#tag-model').selectOption('wd14-vit');
    await expect(modelRow(page).locator('p').first()).toHaveText('Downloaded. Not loaded.');
    expect(page.modelCalls.filter(call => call.startsWith('POST'))).toEqual(['POST /prompt_manager/jobs/model-download']);
});

test('Unload is enabled only while a model is loaded, and a held gate\'s refusal names the operation', async ({ page, request }) => {
    await openTagging(page);
    await expect(actionOf(page, 'Unload')).toBeDisabled();
    // Load WD14 SwinV2 the way a user does: Generate suggestions in the Tag image dialog.
    await page.goto(`${workspace}#/images`);
    await card(page, FIRST).getByRole('button', { name: `Tag or auto-tag ${FIRST.split('/').pop()}`, exact: true }).click();
    await expect(tagDialog(page)).toBeVisible();
    const suggested = page.waitForResponse(item => item.url().endsWith('/image-annotations/suggest'));
    await tagDialog(page).getByRole('button', { name: 'Generate suggestions', exact: true }).click();
    expect((await suggested).status()).toBe(200);
    await tagDialog(page).getByRole('button', { name: 'Cancel', exact: true }).click();
    await openTagging(page);
    await expect(modelRow(page).locator('p').first()).toHaveText('Downloaded and loaded.');
    await expect(actionOf(page, 'Unload')).toBeEnabled();
    // A download holds the model gate: Unload is refused and the toast names it; nothing is unloaded.
    await unavailable(request, ['wd14-vit']);
    await fakeDownload(request, 'hold');
    const held = await request.post('/prompt_manager/jobs/model-download', { data: { model_type: 'wd14-vit' } });
    expect(held.status()).toBe(202);
    const { job } = await held.json();
    const refused = page.waitForResponse(item => item.url().endsWith('/autotag/unload'));
    await actionOf(page, 'Unload').click();
    const refusal = await refused;
    expect(refusal.status()).toBe(409);
    expect(await refusal.json()).toMatchObject({ code: 'model-busy', operation: 'Tagging model download · WD14 ViT' });
    await expect(toast(page)).toHaveText('The tagging model is busy: Tagging model download · WD14 ViT. Try again when it finishes.');
    expect((await serverJson(request, MODELS_URL)).loaded_model_type).toBe('wd14-swinv2');
    await expect(actionOf(page, 'Unload')).toBeEnabled();
    // Once the download ended, Unload works, says so and the row follows.
    await fakeDownload(request, 'succeed');
    await ended(request, job.id);
    await actionOf(page, 'Unload').click();
    await expect(toast(page)).toHaveText('WD14 SwinV2 unloaded.');
    await expect(modelRow(page).locator('p').first()).toHaveText('Downloaded. Not loaded.');
    await expect(actionOf(page, 'Unload')).toBeDisabled();
});

test('Preview tagging review opens the Tag image dialog for the selected image', async ({ page }) => {
    await selectThenOpen(page, SECOND);
    // The section's current (unsaved) values are what the dialog starts from.
    await page.locator('#threshold-character').fill('0.6');
    await page.locator('#threshold-character').press('Tab');
    await page.locator('#tag-model').selectOption('wd14-vit');
    const preview = actionOf(page, 'Preview tagging review');
    await expect(preview).toHaveClass(/ca-primary/);
    await expect(preview).not.toHaveAttribute('aria-disabled', 'true');
    const observed = page.waitForResponse(item => item.url().endsWith('/image-annotations/observe'));
    await preview.click();
    expect((await observed).status()).toBe(200);
    await expect(tagDialog(page)).toBeVisible();
    await expect(tagDialog(page).locator('.tag-image-heading p.small')).toHaveText('dunes_00002.png');
    await expect(tagDialog(page).getByRole('combobox', { name: 'Tagging model', exact: true })).toHaveValue('wd14-vit');
    await expect(tagDialog(page).locator('#image-character-threshold')).toHaveValue('0.6');
    await expect(tagDialog(page).locator('#image-general-threshold')).toHaveValue('0.35');
});

test('Preview tagging review without a selection toasts the D115 sentence', async ({ page }) => {
    await openTagging(page);
    const preview = actionOf(page, 'Preview tagging review');
    await expect(preview).toHaveAttribute('aria-disabled', 'true');
    // An aria-disabled control still takes a press; it only explains.
    await preview.focus();
    await page.keyboard.press('Enter');
    await expect(toast(page)).toHaveText('Select an image in Images first.');
    await expect(tagDialog(page)).toHaveCount(0);
});

test('Tag output images reviews the images, tags each one and never a prompt (#379)', async ({ page, request }) => {
    const before = await serverJson(request, '/__test__/tagging/snapshot');
    await openTagging(page);
    // The saved policy is Skip: no image is auto-tagged yet, so every listed still image is to tag.
    const previewed = page.waitForResponse(item => new URL(item.url()).pathname === PREVIEW_URL);
    await reviewButton(page).click();
    const answer = await previewed;
    expect(answer.request().postDataJSON()).toEqual({ model_type: 'wd14-swinv2', existing: 'skip' });
    const plan = await answer.json();
    // Four still images (two linked, a copy, an unlinked one); the video is not a target.
    expect([plan.images, plan.already_tagged, plan.to_tag]).toEqual([4, 0, 4]);
    expect(plan.prompts).toBeUndefined();
    await expect(dialog(page).locator('#modal-title')).toHaveText('Review tag output images');
    await expect(dialog(page).locator('#modal-body > h3')).toHaveText('Tag output images');
    expect(await gridOf(page)).toEqual([['Model', 'WD14 SwinV2 · ~400 MB'], ['Existing tags', 'Skip already tagged'],
        ['Images', '4'], ['Already auto-tagged', '0'], ['Images to tag', 'about 4'],
        ['Time', 'about 1 min on a processor; a graphics card can be faster.'],
        ['Settings', `General 0.35 · Character 0.85 · up to ${plan.suggestion_limit} tags per image`],
        ['Backup', 'Create and verify before changes']]);
    await expect(dialog(page).locator('#modal-body > p.small')).toHaveText('Nothing is written until you start. The number to '
        + 'tag is an estimate: images changed since they were tagged are tagged again, and missing files are skipped. '
        + 'Activity reports what was tagged.');
    await expect(dialog(page).locator('#modal-body > .notice h3')).toHaveText('Back up the database first (recommended)');
    // The review wrote nothing.
    expect(await serverJson(request, '/__test__/tagging/snapshot')).toEqual(before);
    await expect(dialog(page).locator('.dialog-actions button')).toHaveText(['Cancel', 'Start tagging']);
    const started = page.waitForResponse(item => new URL(item.url()).pathname === '/prompt_manager/jobs/autotag');
    await dialog(page).getByRole('button', { name: 'Start tagging', exact: true }).click();
    const start = await started;
    expect(start.request().postDataJSON()).toEqual({ model_type: 'wd14-swinv2', existing: 'skip',
        options: { general_threshold: 0.35, character_threshold: 0.85 } });
    const { job } = await start.json();
    await expect(page).toHaveURL(`${workspace}#/activity/${job.id}`);
    await expect(page.locator('#workspace-main h1')).toHaveText('Tag output images');
    const finished = await ended(request, job.id);
    expect(finished.state).toBe('finished');
    expect(finished.status_line).toBe('Tagged 4 images');
    // Each image got its own tags; no prompt, pair or link row changed.
    expect(await serverJson(request, '/__test__/tagging/snapshot')).toEqual(before);

    // Under Skip every image is now auto-tagged: nothing to start.
    await openTagging(page);
    await reviewButton(page).click();
    await expect(dialog(page).locator('#modal-body > p.small')).toHaveText('Every output image is already auto-tagged.');
    expect((await gridOf(page)).slice(2, 5)).toEqual([['Images', '4'], ['Already auto-tagged', '4'], ['Images to tag', 'about 0']]);
    await expect(dialog(page).locator('.dialog-actions button')).toHaveText(['Cancel']);
});

test('Replace after review shows only Cancel with the reason', async ({ page, request }) => {
    const before = await serverJson(request, '/__test__/tagging/snapshot');
    await openTagging(page);
    await page.locator('#tag-existing').selectOption('replace_after_review');
    await reviewButton(page).click();
    await expect(dialog(page).locator('#modal-title')).toHaveText('Review tag output images');
    expect((await gridOf(page)).map(([term]) => term)).toEqual(['Model', 'Existing tags', 'Images', 'Already auto-tagged',
        'Images to tag', 'Time', 'Settings', 'Backup']);
    await expect(dialog(page).locator('#modal-body > p.small')).toHaveText('Replacing tags needs a review of each image. '
        + 'Use Preview tagging review, or choose Skip or Merge for a batch run.');
    await expect(dialog(page).locator('.dialog-actions button')).toHaveText(['Cancel']);
    await dialog(page).getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(dialog(page)).toHaveCount(0);
    expect(await serverJson(request, '/__test__/tagging/snapshot')).toEqual(before);
    expect((await serverJson(request, '/prompt_manager/jobs')).jobs.filter(item => item.kind === 'autotag')).toEqual([]);
});

test('a model that is not downloaded cannot be reviewed for a batch; the button says why', async ({ page, request }) => {
    await unavailable(request, ['gguf']);
    await openTagging(page);
    await page.locator('#tag-model').selectOption('gguf');
    await expect(reviewButton(page)).toHaveAttribute('aria-disabled', 'true');
    // Playwright waits on an aria-disabled control: press it from the keyboard, as a user can.
    await reviewButton(page).focus();
    await page.keyboard.press('Enter');
    await expect(toast(page)).toHaveText('GGUF JoyCaption is not downloaded. Download it first.');
    await expect(dialog(page)).toHaveCount(0);
    expect(page.modelCalls.filter(call => call.includes('/batch/preview'))).toEqual([]);
});
