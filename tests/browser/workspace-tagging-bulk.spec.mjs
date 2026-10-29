// The Images selection bar's Add tags and Auto-tag selected (#249 PR D, plan Task D2, Mock inventory M3; D85, D90,
// D91) against the real `image-tags` job route and the injected fake model (tests/browser/workspace_tagging_fixture.py).
// No model is ever downloaded or loaded for real: the fake answers, and every test checks no download request was sent.
import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';

const images = '/prompt_manager/workspace#/images';
const FIRST = 'tagging/dunes_00001.png', SECOND = 'tagging/dunes_00002.png', UNLINKED = 'loose/unlinked.png';
const card = (page, path) => page.locator(`article[data-image-key="0:${path}"]`);
const bar = page => page.locator('#workspace-main > #bulkbar > .bulkbar');
const barButton = (page, name) => bar(page).getByRole('button', { name, exact: true });
const modal = page => page.locator('dialog#modal[open]');
const title = page => modal(page).locator('#modal-title');
const toast = page => page.locator('#workspace-toast');
const toasts = page => page.evaluate(() => window.__toasts);
const chips = page => modal(page).locator('#tag-suggestions .chip.tag-suggestion');
const actionButtons = page => modal(page).locator('.dialog-actions button');
const panel = page => page.getByRole('complementary', { name: 'Selected image details', exact: true });
const imageTags = page => panel(page).getByRole('region', { name: 'Image tags', exact: true }).locator('.tags');
const NOT_DOWNLOADED = 'WD14 SwinV2 is not downloaded. Download it in System › Auto-tagging.';

test.beforeEach(async ({ page, request, browserName }) => {
    await startCoverage(page, browserName);
    // Every toast text in order: a fast job's finish toast may replace its start toast before an assertion reads it.
    await page.addInitScript(() => {
        window.__toasts = [];
        new MutationObserver(() => {
            const text = document.getElementById('workspace-toast')?.textContent ?? '';
            if (text && window.__toasts.at(-1) !== text) window.__toasts.push(text);
        }).observe(document, { subtree: true, childList: true, characterData: true });
    });
    await expect(await request.post('/__test__/tagging/reset', { data: {} })).toBeOK();
    page.taggingErrors = [];
    page.on('pageerror', error => page.taggingErrors.push(error.message));
    page.downloads = [];
    page.on('request', item => { if (/\/autotag\/download\//.test(item.url())) page.downloads.push(item.url()); });
});
test.afterEach(async ({ page, browserName }) => {
    await saveCoverage(page, browserName);
    expect(page.taggingErrors).toEqual([]);
    expect(page.downloads).toEqual([]);
});

/** Images with `paths` checked, in that order. */
async function select(page, ...paths) {
    await page.goto(images);
    for (const path of paths) await card(page, path).getByRole('checkbox').check();
    await expect(bar(page).locator('strong')).toHaveText(`${paths.length} selected`);
}
async function scopeOf(request) {
    const listing = await (await request.get('/prompt_manager/images/output?mode=workspace&limit=40')).json();
    return listing.images[0].root_scope;
}
/** The saved tags of `path` through the real observe route, then that capture's release. */
async function savedTags(request, path) {
    const target = { root_index: 0, root_scope: await scopeOf(request), path };
    const observed = await (await request.post('/prompt_manager/image-annotations/observe', { data: { target } })).json();
    expect(observed.success).toBe(true);
    await request.delete(`/prompt_manager/image-annotations/capture/${observed.capture.token}`);
    return observed.tags;
}
/** Wait until the session's job `id` has ended; returns its summary. */
async function ended(request, id) {
    let job;
    await expect.poll(async () => {
        const { jobs } = await (await request.get('/prompt_manager/jobs')).json();
        job = jobs.find(item => item.id === id);
        return job?.state;
    }).toBe('finished');
    return job;
}
const jobStart = page => page.waitForRequest(item => item.method() === 'POST' && new URL(item.url()).pathname === '/prompt_manager/jobs/image-tags');

test('selection bar shows Add tags then Auto-tag selected before Set category', async ({ page }) => {
    await select(page, FIRST);
    await expect(bar(page).locator(':scope > *')).toHaveText(['1 selected', 'Add tags', 'Auto-tag selected', 'Set category', 'Add to collection', 'Export metadata', 'Clear']);
});

test('Add tags runs a job and the cards show the tags after it finishes', async ({ page, request }) => {
    await select(page, SECOND, FIRST);
    await expect(card(page, FIRST)).toHaveClass(/\bselected\b/);
    await barButton(page, 'Add tags').click();
    await expect(title(page)).toHaveText('Tag selected images');
    const field = modal(page).getByRole('textbox', { name: 'Tags, separated by commas', exact: true });
    await expect(field).toHaveValue('');
    await expect(actionButtons(page)).toHaveText(['Cancel', 'Apply']);
    let posted = 0;
    page.on('request', item => { if (new URL(item.url()).pathname === '/prompt_manager/jobs/image-tags') posted += 1; });
    // A field that parses to no names keeps the dialog and its text, and reports why on the field (D118).
    await field.fill(' , ,');
    await actionButtons(page).last().click();
    await expect.poll(() => field.evaluate(node => node.validationMessage)).toBe('Enter at least one tag.');
    await expect(title(page)).toHaveText('Tag selected images');
    await expect(field).toHaveValue(' , ,');
    await field.fill('sunset, Sunset , blue_hour,,');
    expect(await field.evaluate(node => node.validity.valid)).toBe(true);
    const started = jobStart(page);
    const answer = page.waitForResponse(item => new URL(item.url()).pathname === '/prompt_manager/jobs/image-tags');
    await actionButtons(page).last().click();
    const scope = await scopeOf(request);
    // Listing order (the newer FIRST before SECOND), the reveal identity, the dialog's dedupe.
    expect((await started).postDataJSON()).toEqual({ mode: 'add', tags: ['sunset', 'blue_hour'], targets: [
        { root_index: 0, root_scope: scope, path: FIRST }, { root_index: 0, root_scope: scope, path: SECOND }] });
    const { job } = await (await answer).json();
    // The refused Apply sent nothing: only the valid one was posted.
    expect(posted).toBe(1);
    await expect.poll(() => toasts(page)).toContain('Tagging started.');
    expect(await toasts(page)).not.toContain('Enter at least one tag.');
    await expect(modal(page)).toHaveCount(0);
    await expect(bar(page).locator('strong')).toHaveText('2 selected');
    expect((await ended(request, job.id)).counters).toEqual({ tagged: 2, unchanged: 0, skipped: 0, errors: 0 });
    // The finished job's files are read again: the selected card's panel shows its new image tags.
    await expect(imageTags(page).locator('button.chip')).toHaveText(['sunset', 'blue_hour']);
    expect(await savedTags(request, SECOND)).toEqual(['sunset', 'blue_hour']);
    expect(await savedTags(request, UNLINKED)).toEqual([]);
});

test('Auto-tag selected with Apply automatically runs a job with the chosen model, thresholds and suggestion count', async ({ page, request }) => {
    await select(page, FIRST, UNLINKED);
    await barButton(page, 'Auto-tag selected').click();
    await expect(title(page)).toHaveText('Auto-tag selected images');
    await expect(modal(page)).toHaveClass(/(^| )tagging-modal( |$)/);
    const body = modal(page).locator('#modal-body');
    expect(await body.evaluate(node => [...node.children].map(child => child.className))).toEqual(['tag-model-panel', 'field', 'small', 'dialog-actions']);
    await expect(body.locator('.tag-model-panel h3')).toHaveText('Auto-tag 2 images');
    // No preview, manual field, suggestions or Generate: only the model panel's fields.
    await expect(body.getByRole('button', { name: 'Generate suggestions', exact: true })).toHaveCount(0);
    await expect(body.locator('#manual-image-tags, .tag-suggestion-panel, .tag-image-preview')).toHaveCount(0);
    const group = body.getByRole('radiogroup', { name: 'How to apply', exact: true });
    await expect(group.getByRole('radio', { name: 'Apply automatically', exact: true })).toBeChecked();
    await expect(group.getByRole('radio', { name: 'Review each image', exact: true })).not.toBeChecked();
    await expect(actionButtons(page)).toHaveText(['Cancel', 'Start auto-tagging']);
    await body.getByRole('combobox', { name: 'Tagging model', exact: true }).selectOption('wd14-vit');
    await body.getByRole('spinbutton', { name: 'General confidence', exact: true }).fill('0.5');
    await body.getByRole('spinbutton', { name: 'Character confidence', exact: true }).fill('0.9');
    const limit = body.getByRole('spinbutton', { name: 'Suggestions', exact: true });
    // An invalid count reports its validity and sends nothing.
    await limit.fill('0');
    let posted = 0;
    page.on('request', item => { if (new URL(item.url()).pathname === '/prompt_manager/jobs/image-tags') posted += 1; });
    await actionButtons(page).last().click();
    expect(await limit.evaluate(node => node.validity.valid)).toBe(false);
    await expect(title(page)).toHaveText('Auto-tag selected images');
    await limit.fill('2');
    const started = jobStart(page);
    const answer = page.waitForResponse(item => new URL(item.url()).pathname === '/prompt_manager/jobs/image-tags');
    await actionButtons(page).last().click();
    const scope = await scopeOf(request);
    expect((await started).postDataJSON()).toEqual({ mode: 'auto', model_type: 'wd14-vit',
        options: { general_threshold: 0.5, character_threshold: 0.9, suggestion_limit: 2 },
        targets: [{ root_index: 0, root_scope: scope, path: FIRST }, { root_index: 0, root_scope: scope, path: UNLINKED }] });
    const { job } = await (await answer).json();
    // The invalid press sent nothing: only this Start was posted.
    expect(posted).toBe(1);
    await expect.poll(() => toasts(page)).toContain('Auto-tagging started.');
    await expect(modal(page)).toHaveCount(0);
    await expect(bar(page).locator('strong')).toHaveText('2 selected');
    expect((await ended(request, job.id)).counters).toEqual({ tagged: 2, unchanged: 0, skipped: 0, errors: 0 });
    expect(await savedTags(request, FIRST)).toEqual(['sandstone', 'desert']);
    expect(await savedTags(request, UNLINKED)).toEqual(['sandstone', 'desert']);
    expect(await savedTags(request, SECOND)).toEqual([]);
});

test('a model that is not downloaded keeps Start disabled and says why', async ({ page, request }) => {
    await expect(await request.post('/__test__/tagging/models', { data: { unavailable: ['wd14-swinv2'] } })).toBeOK();
    await select(page, FIRST);
    await barButton(page, 'Auto-tag selected').click();
    const status = modal(page).locator('#tag-generation-status');
    await expect(status).toHaveAttribute('role', 'status');
    await expect(status).toHaveText(NOT_DOWNLOADED);
    const start = actionButtons(page).last();
    await expect(start).toHaveAttribute('aria-disabled', 'true');
    let posted = 0;
    page.on('request', item => { if (new URL(item.url()).pathname === '/prompt_manager/jobs/image-tags') posted += 1; });
    // aria-disabled: a press does nothing.
    await start.dispatchEvent('click');
    await expect(title(page)).toHaveText('Auto-tag selected images');
    await modal(page).getByRole('combobox', { name: 'Tagging model', exact: true }).selectOption('wd14-vit');
    await expect(status).toHaveText('');
    await expect(start).not.toHaveAttribute('aria-disabled', 'true');
    await modal(page).getByRole('combobox', { name: 'Tagging model', exact: true }).selectOption('wd14-swinv2');
    await expect(start).toHaveAttribute('aria-disabled', 'true');
    await actionButtons(page).first().click();
    await expect(modal(page)).toHaveCount(0);
    await expect(barButton(page, 'Auto-tag selected')).toBeFocused();
    expect(posted).toBe(0);
});

const NEEDS_LLAMA = 'GGUF JoyCaption needs llama-cpp-python. Install it into ComfyUI\'s Python: pip install llama-cpp-python. '
    + 'For a CUDA build: CMAKE_ARGS="-DGGML_CUDA=on" pip install llama-cpp-python';

test('a model missing its backend package keeps Start disabled and names the install command (#424)', async ({ page, request }) => {
    await expect(await request.post('/__test__/tagging/models', { data: { unavailable: [],
        missing_packages: { gguf: ['llama-cpp-python'] } } })).toBeOK();
    await select(page, FIRST);
    await barButton(page, 'Auto-tag selected').click();
    const status = modal(page).locator('#tag-generation-status');
    const start = actionButtons(page).last();
    await modal(page).getByRole('combobox', { name: 'Tagging model', exact: true }).selectOption('gguf');
    await expect(status).toHaveText(NEEDS_LLAMA);
    await expect(start).toHaveAttribute('aria-disabled', 'true');
    await modal(page).getByRole('combobox', { name: 'Tagging model', exact: true }).selectOption('wd14-vit');
    await expect(status).toHaveText('');
    await expect(start).not.toHaveAttribute('aria-disabled', 'true');
    await actionButtons(page).first().click();
    await expect(modal(page)).toHaveCount(0);
});

test('Suggestions field prefills from the setting and caps chips at the entered count', async ({ page, request }) => {
    await expect(await request.post('/__test__/tagging/defaults', { data: { general: 0.5, character: 0.9, limit: 7 } })).toBeOK();
    await select(page, FIRST);
    await barButton(page, 'Auto-tag selected').click();
    const limit = modal(page).getByRole('spinbutton', { name: 'Suggestions', exact: true });
    await expect(limit).toHaveValue('7');
    await expect(modal(page).getByRole('spinbutton', { name: 'General confidence', exact: true })).toHaveValue('0.5');
    await expect(modal(page).getByRole('spinbutton', { name: 'Character confidence', exact: true })).toHaveValue('0.9');
    await modal(page).getByRole('radio', { name: 'Review each image', exact: true }).check();
    await expect(actionButtons(page)).toHaveText(['Cancel', 'Start review']);
    await limit.fill('3');
    const suggest = page.waitForRequest(item => item.url().endsWith('/image-annotations/suggest'));
    await actionButtons(page).last().click();
    await expect(title(page)).toHaveText('Tag image · Image 1 of 1');
    expect((await suggest).postDataJSON().options).toEqual({ general_threshold: 0.5, character_threshold: 0.9, suggestion_limit: 3 });
    await expect(chips(page)).toHaveCount(3);
    await expect(modal(page).getByRole('spinbutton', { name: 'Suggestions', exact: true })).toHaveValue('3');
    await expect(actionButtons(page)).toHaveText(['Stop reviewing', 'Skip', 'Approve and finish']);
    await page.keyboard.press('Escape');
    await expect(modal(page)).toHaveCount(0);
    await expect(toast(page)).toHaveText('0 of 1 images tagged.');
    expect(await savedTags(request, FIRST)).toEqual([]);
});

test('Review each image walks the selection one dialog at a time and only approved images gain tags', async ({ page, request }) => {
    await select(page, FIRST, SECOND, UNLINKED);
    // The second image's first Generate finds the model busy (a legacy batch): the dialog waits for Retry or Skip.
    let suggests = 0;
    await page.route('**/prompt_manager/image-annotations/suggest', async route => {
        suggests += 1;
        if (suggests === 2) {
            await route.fulfill({ status: 409, json: { success: false, code: 'model-busy', error: 'The tagging model is busy. Try again when it finishes.' } });
            return;
        }
        await route.continue();
    });
    const puts = [];
    page.on('request', item => { if (item.method() === 'PUT' && item.url().endsWith('/image-annotations/tags')) puts.push(item.postDataJSON()); });
    await barButton(page, 'Auto-tag selected').click();
    await modal(page).getByRole('radio', { name: 'Review each image', exact: true }).check();
    await modal(page).getByRole('spinbutton', { name: 'Suggestions', exact: true }).fill('2');
    await actionButtons(page).last().click();
    await expect(title(page)).toHaveText('Tag image · Image 1 of 3');
    await expect(modal(page).getByRole('combobox', { name: 'Tagging model', exact: true })).toHaveValue('wd14-swinv2');
    await expect(chips(page)).toHaveCount(2);
    await expect(actionButtons(page)).toHaveText(['Stop reviewing', 'Skip', 'Approve and next']);
    await expect(actionButtons(page).last()).toHaveClass(/ca-primary/);
    // Approve saves the dialog's manual field plus its remaining chips.
    await modal(page).getByRole('textbox', { name: 'Image tags · edit manually', exact: true }).fill('mine');
    await modal(page).getByRole('button', { name: 'Remove desert', exact: true }).click();
    await actionButtons(page).last().click();
    await expect(title(page)).toHaveText('Tag image · Image 2 of 3');
    await expect(modal(page).locator('#tag-generation-status')).toHaveText('The tagging model is busy. Try again when it finishes.');
    // No suggestions on screen: the primary would save the manual field only (D90).
    await expect(actionButtons(page)).toHaveText(['Retry', 'Stop reviewing', 'Skip', 'Save and next']);
    await actionButtons(page).nth(2).click();
    await expect(title(page)).toHaveText('Tag image · Image 3 of 3');
    await expect(chips(page)).toHaveCount(2);
    await expect(actionButtons(page)).toHaveText(['Stop reviewing', 'Skip', 'Approve and finish']);
    await actionButtons(page).last().click();
    await expect(modal(page)).toHaveCount(0);
    await expect(toast(page)).toHaveText('2 of 3 images tagged.');
    await expect(barButton(page, 'Auto-tag selected')).toBeFocused();
    await expect(bar(page).locator('strong')).toHaveText('3 selected');
    expect(puts.map(body => [body.target.path, body.tags])).toEqual([[FIRST, ['mine', 'sandstone']], [UNLINKED, ['sandstone', 'desert']]]);
    expect(await savedTags(request, FIRST)).toEqual(['mine', 'sandstone']);
    expect(await savedTags(request, SECOND)).toEqual([]);
    expect(await savedTags(request, UNLINKED)).toEqual(['sandstone', 'desert']);
    // The reviewed file's panel shows its saved tags without reselecting.
    await expect(imageTags(page).locator('button.chip')).toHaveText(['mine', 'sandstone']);
    await expect.poll(async () => (await (await request.get('/__test__/tagging/captures')).json()).captures).toBe(0);
});

test('Review holds Stop reviewing, Escape and × while a save is in flight, and names a manual-only save', async ({ page, request }) => {
    await select(page, FIRST, SECOND);
    let release;
    const held = new Promise(resolve => { release = resolve; });
    await page.route('**/prompt_manager/image-annotations/tags', async route => {
        if (route.request().method() === 'PUT') await held;
        await route.continue();
    });
    await barButton(page, 'Auto-tag selected').click();
    await modal(page).getByRole('radio', { name: 'Review each image', exact: true }).check();
    await actionButtons(page).last().click();
    await expect(title(page)).toHaveText('Tag image · Image 1 of 2');
    await expect(chips(page)).not.toHaveCount(0);
    await expect(actionButtons(page)).toHaveText(['Stop reviewing', 'Skip', 'Approve and next']);
    // D90: a model change clears the suggestions, so the primary saves the manual field only and says so.
    await modal(page).getByRole('combobox', { name: 'Tagging model', exact: true }).selectOption('wd14-vit');
    await expect(chips(page)).toHaveCount(0);
    await expect(actionButtons(page)).toHaveText(['Stop reviewing', 'Skip', 'Save and next']);
    await modal(page).getByRole('textbox', { name: 'Image tags · edit manually', exact: true }).fill('mine');
    const put = page.waitForRequest(item => item.method() === 'PUT' && item.url().endsWith('/image-annotations/tags'));
    await actionButtons(page).last().click();
    await put;
    const stop = actionButtons(page).first();
    await expect(stop).toHaveAttribute('aria-disabled', 'true');
    await expect(actionButtons(page).nth(1)).toHaveAttribute('aria-disabled', 'true');
    // None of these ends the session while the save is in flight; the count then includes it.
    await page.keyboard.press('Escape');
    await modal(page).locator('#modal-close').click();
    await stop.dispatchEvent('click'); // aria-disabled: Playwright's click would wait for it
    release();
    await expect(title(page)).toHaveText('Tag image · Image 2 of 2');
    await expect(chips(page)).not.toHaveCount(0);
    await expect(actionButtons(page)).toHaveText(['Stop reviewing', 'Skip', 'Approve and finish']);
    await expect(actionButtons(page).first()).not.toHaveAttribute('aria-disabled', 'true');
    await actionButtons(page).first().click();
    await expect(modal(page)).toHaveCount(0);
    await expect(toast(page)).toHaveText('1 of 2 images tagged.');
    expect(await savedTags(request, FIRST)).toEqual(['mine']);
    expect(await savedTags(request, SECOND)).toEqual([]);
});

test('a running scan makes the start report busy', async ({ page, request }) => {
    const scan = await (await request.post('/__test__/jobs/start', { data: { kind: 'scan', title: 'Scan for metadata' } })).json();
    try {
        await select(page, FIRST, SECOND);
        await barButton(page, 'Add tags').click();
        await modal(page).getByRole('textbox', { name: 'Tags, separated by commas', exact: true }).fill('sunset');
        await actionButtons(page).last().click();
        await expect(toast(page)).toHaveText('Another job is running: Scan for metadata.');
        await expect(toast(page).getByRole('link', { name: 'Scan for metadata', exact: true })).toHaveAttribute('href', `#/activity/${scan.job.id}`);
        await expect(bar(page).locator('strong')).toHaveText('2 selected');
        // Auto-tag is refused the same way.
        await barButton(page, 'Auto-tag selected').click();
        await expect(actionButtons(page).last()).not.toHaveAttribute('aria-disabled', 'true');
        await actionButtons(page).last().click();
        await expect(toast(page)).toHaveText('Another job is running: Scan for metadata.');
        await expect(bar(page).locator('strong')).toHaveText('2 selected');
        expect(await savedTags(request, FIRST)).toEqual([]);
    } finally {
        await request.post(`/__test__/jobs/${scan.job.id}/finish`, { data: { state: 'finished' } });
    }
});
