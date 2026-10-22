// The Tag image dialog, the card Tag button and the Tags page hooks (#249 PR C, plan Task C2, Mock inventory M1, M2, M5)
// against the real routes and the injected fake model (tests/browser/workspace_tagging_fixture.py). No model is ever
// downloaded or loaded: the fake answers, and every test checks that no download request was sent.
import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';
import { seedConcept, openConcept, openProduction, saveImageTags } from './concept-inventory.mjs';

const images = '/prompt_manager/workspace#/images';
const MODULE = '/prompt_manager/js/workspace/image-tagging.mjs';
const MODEL_LABELS = ['WD14 SwinV2 · ~400 MB', 'WD14 ViT · ~400 MB', 'GGUF JoyCaption (Recommended) · ~4.5 GB', 'HuggingFace JoyCaption · ~16 GB'];
const INITIAL_COPY = 'Choose a model, then generate tags. Remove anything you do not want to add.';
const MODEL_CHANGED = 'Generate suggestions with the selected model before approving.';
const ALL_REMOVED = 'All suggestions removed. Generate again or enter tags manually.';
const card = (page, path) => page.locator(`article[data-image-key="0:${path}"]`);
const tagButton = (page, path) => card(page, path).getByRole('button', { name: `Tag or auto-tag ${path.split('/').pop()}`, exact: true });
const dialog = page => page.getByRole('dialog', { name: 'Tag image', exact: true });
const manual = page => dialog(page).getByRole('textbox', { name: 'Image tags · edit manually', exact: true });
const model = page => dialog(page).getByRole('combobox', { name: 'Tagging model', exact: true });
const generate = page => dialog(page).getByRole('button', { name: 'Generate suggestions', exact: true });
const chips = page => dialog(page).locator('#tag-suggestions .chip.tag-suggestion');
const status = page => dialog(page).locator('#tag-generation-status');
const toast = page => page.locator('#workspace-toast');
const nav = (page, label) => page.locator('aside.sidebar nav').getByRole('link', { name: new RegExp(`^${label}(?:\\s*[\\d,]+)?$`) });
const FIRST = 'tagging/dunes_00001.png', SECOND = 'tagging/dunes_00002.png', COPY = 'copies/dunes_00001.png';
const VIDEO = 'tagging/clip.webm', UNLINKED = 'loose/unlinked.png';

let manifest;
test.beforeEach(async ({ page, request, browserName }) => {
    await startCoverage(page, browserName);
    const response = await request.post('/__test__/tagging/reset', { data: {} });
    await expect(response).toBeOK();
    manifest = await response.json();
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

/** Images, then the Tag dialog of `path`, settled on its observation (the manual field holds the saved tags). */
async function openTag(page, path = FIRST, { goto = true } = {}) {
    if (goto) await page.goto(images);
    const observed = page.waitForResponse(item => item.url().endsWith('/image-annotations/observe'));
    await tagButton(page, path).click();
    await expect(dialog(page)).toBeVisible();
    expect((await observed).status()).toBe(200);
    await expect(dialog(page).locator('img.tag-image-preview, video.tag-image-preview')).toHaveAttribute('src', /\/prompt_manager\/image-annotations\/capture\//);
}
async function generated(page) {
    const answer = page.waitForResponse(item => item.url().endsWith('/image-annotations/suggest'));
    await generate(page).click();
    return answer;
}
/** Read the saved tags of `path` through the real observe route, then release that capture. */
async function savedTags(request, path) {
    const target = { root_index: 0, root_scope: await scopeOf(request), path };
    const observed = await (await request.post('/prompt_manager/image-annotations/observe', { data: { target } })).json();
    expect(observed.success).toBe(true);
    await request.delete(`/prompt_manager/image-annotations/capture/${observed.capture.token}`);
    return observed.tags;
}
async function scopeOf(request) {
    const listing = await (await request.get('/prompt_manager/images/output?mode=workspace&limit=40')).json();
    return listing.images[0].root_scope;
}

test('tag dialog control inventory and order match M2', async ({ page }) => {
    await openTag(page);
    await expect(page.locator('dialog#modal')).toHaveClass(/(^| )tagging-modal( |$)/);
    await expect(dialog(page).locator('#modal-title')).toHaveText('Tag image');
    const body = dialog(page).locator('#modal-body');
    expect(await body.evaluate(node => [...node.children].map(child => child.className))).toEqual(
        ['tag-image-heading', 'field', 'tag-model-panel', 'tag-suggestion-panel', 'dialog-actions']);
    const heading = body.locator('.tag-image-heading');
    await expect(heading.locator('h3')).toHaveText(manifest.title);
    await expect(heading.locator('p.small')).toHaveText('dunes_00001.png');
    await expect(heading.locator('span.chip')).toHaveText('96 × 64');
    await expect(heading.locator('img.tag-image-preview')).toHaveAttribute('alt', manifest.title);
    await expect(body.locator('.tag-scope-note')).toHaveCount(0);
    await expect(manual(page)).toHaveAttribute('id', 'manual-image-tags');
    await expect(manual(page)).toHaveValue('');
    await expect(manual(page)).toHaveAccessibleDescription('Comma-separated. Generated suggestions are added only after you approve them.');
    const panel = body.locator('.tag-model-panel');
    await expect(panel.locator('h3')).toHaveText('Auto-tag this image');
    await expect(model(page).locator('option')).toHaveText(MODEL_LABELS);
    expect(await model(page).locator('option').evaluateAll(nodes => nodes.map(node => node.value))).toEqual(['wd14-swinv2', 'wd14-vit', 'gguf', 'hf']);
    await expect(model(page)).toHaveValue('wd14-swinv2');
    const options = panel.locator('#image-tag-model-options');
    const general = options.getByRole('spinbutton', { name: 'General confidence', exact: true });
    const character = options.getByRole('spinbutton', { name: 'Character confidence', exact: true });
    const limit = options.getByRole('spinbutton', { name: 'Suggestions', exact: true });
    await expect(options.locator('.two-col .field')).toHaveCount(2);
    await expect(general).toHaveValue('0.35');
    await expect(character).toHaveValue('0.85');
    await expect(limit).toHaveValue('10');
    for (const [input, attributes] of [[general, ['0', '1', '0.05']], [character, ['0', '1', '0.05']], [limit, ['1', '100', '1']]]) {
        expect(await input.evaluate(node => [node.min, node.max, node.step])).toEqual(attributes);
    }
    await expect(generate(page)).toHaveClass(/ca-primary/);
    await expect(generate(page).locator('svg.icon')).toHaveCount(1);
    const suggestions = body.locator('section.tag-suggestion-panel');
    await expect(suggestions).toHaveAttribute('aria-labelledby', 'tag-suggestions-title');
    await expect(suggestions.locator('h3#tag-suggestions-title')).toHaveText('Review suggestions');
    await expect(suggestions.locator('#suggestion-count')).toHaveText('Not generated');
    await expect(suggestions.locator('#tag-suggestions > p.small')).toHaveText(INITIAL_COPY);
    await expect(status(page)).toHaveAttribute('role', 'status');
    await expect(status(page)).toHaveText('');
    await expect(body.locator('.dialog-actions button')).toHaveText(['Cancel', 'Save manual tags']);
    await expect(body.locator('.dialog-actions button').last()).toHaveClass(/ca-primary/);
    await model(page).selectOption('gguf');
    await expect(options.getByRole('textbox', { name: 'Tagging instructions', exact: true })).toHaveValue('Describe the image');
    await expect(options.getByRole('spinbutton', { name: 'Suggestions', exact: true })).toHaveValue('10');
    await expect(options.locator('.two-col')).toHaveCount(0);
});

test('generate shows real fixture percentages for WD14 and Suggested for GGUF, at most ten chips', async ({ page }) => {
    await openTag(page);
    const answer = await generated(page);
    expect(answer.request().postDataJSON()).toMatchObject({ model_type: 'wd14-swinv2', options: { general_threshold: 0.35, character_threshold: 0.85, suggestion_limit: 10 } });
    await expect(chips(page)).toHaveCount(10);
    await expect(chips(page).first().locator(':scope > span').first()).toHaveText('sandstone');
    await expect(chips(page).locator('.tag-confidence').first()).toHaveText('98%');
    await expect(chips(page).nth(1).locator('.tag-confidence')).toHaveText('97%');
    await expect(chips(page).first().getByRole('button', { name: 'Remove sandstone', exact: true })).toBeVisible();
    await expect(dialog(page).locator('#suggestion-count')).toHaveText('10 suggestions');
    await expect(status(page)).toHaveText('Suggestions from WD14 SwinV2 · ~400 MB. Approve tags to save.');
    await expect(dialog(page).locator('.dialog-actions .ca-primary')).toHaveText('Approve selected tags');
    for (const id of ['gguf', 'hf']) {
        await model(page).selectOption(id);
        await expect(chips(page)).toHaveCount(0);
        await generated(page);
        await expect(chips(page)).toHaveCount(10);
        await expect(chips(page).locator('.tag-confidence')).toHaveText(Array(10).fill('Suggested'));
        await expect(status(page)).toHaveText(`Suggestions from ${MODEL_LABELS[id === 'gguf' ? 2 : 3]}. Approve tags to save.`);
    }
    await dialog(page).getByRole('spinbutton', { name: 'Suggestions', exact: true }).fill('3');
    const fewer = await generated(page);
    expect(fewer.request().postDataJSON().options).toEqual({ instructions: 'Describe the image', suggestion_limit: 3 });
    await expect(chips(page)).toHaveCount(3);
    await expect(dialog(page).locator('#suggestion-count')).toHaveText('3 suggestions');
});

test('removing chips and editing manually then approving persists across reload', async ({ page, request }) => {
    await openTag(page);
    await manual(page).fill('Minimal, sky_blue');
    await dialog(page).getByRole('spinbutton', { name: 'Suggestions', exact: true }).fill('3');
    await generated(page);
    await expect(chips(page)).toHaveCount(3);
    const remove = dialog(page).getByRole('button', { name: 'Remove desert', exact: true });
    await remove.click();
    await expect(chips(page)).toHaveCount(2);
    // Focus moves to the next chip's ×.
    await expect(dialog(page).getByRole('button', { name: 'Remove arch', exact: true })).toBeFocused();
    const saved = page.waitForResponse(item => item.url().endsWith('/image-annotations/tags') && item.request().method() === 'PUT');
    await dialog(page).getByRole('button', { name: 'Approve selected tags', exact: true }).click();
    expect((await saved).request().postDataJSON().tags).toEqual(['Minimal', 'sky_blue', 'sandstone', 'arch']);
    await expect(dialog(page)).toHaveCount(0);
    await expect(toast(page)).toHaveText('4 tags saved for dunes_00001.png.');
    await expect(tagButton(page, FIRST)).toBeFocused();
    expect(await savedTags(request, FIRST)).toEqual(['Minimal', 'sky_blue', 'sandstone', 'arch']);
    await page.reload();
    await openTag(page, FIRST, { goto: false });
    await expect(manual(page)).toHaveValue('Minimal, sky_blue, sandstone, arch');
    // Removing every chip shows the all-removed copy; the manual set still saves.
    await dialog(page).getByRole('spinbutton', { name: 'Suggestions', exact: true }).fill('2');
    await generated(page);
    for (const name of ['sandstone', 'desert']) await dialog(page).getByRole('button', { name: `Remove ${name}`, exact: true }).click();
    await expect(dialog(page).locator('#tag-suggestions > p.small')).toHaveText(ALL_REMOVED);
    await expect(generate(page)).toBeFocused();
    await dialog(page).getByRole('button', { name: 'Approve selected tags', exact: true }).click();
    await expect(toast(page)).toHaveText('4 tags saved for dunes_00001.png.');
});

test('cancel escape and close write nothing', async ({ page, request }) => {
    const writes = [];
    page.on('request', item => { if (item.url().endsWith('/image-annotations/tags')) writes.push(item.method()); });
    await page.goto(images);
    for (const dismiss of [
        () => dialog(page).getByRole('button', { name: 'Cancel', exact: true }).click(),
        () => page.keyboard.press('Escape'),
        () => dialog(page).getByRole('button', { name: 'Close dialog', exact: true }).click(),
    ]) {
        const released = page.waitForResponse(item => item.url().includes('/image-annotations/capture/') && item.request().method() === 'DELETE');
        await openTag(page, FIRST, { goto: false });
        await manual(page).fill('never_saved');
        await dismiss();
        await expect(dialog(page)).toHaveCount(0);
        expect((await released).ok()).toBe(true);
        await expect(tagButton(page, FIRST)).toBeFocused();
        await expect(page.locator('dialog#modal.tagging-modal')).toHaveCount(0);
    }
    expect(writes).toEqual([]);
    expect(await savedTags(request, FIRST)).toEqual([]);
});

test('model change discards suggestions and relabels primary', async ({ page }) => {
    await openTag(page);
    await manual(page).fill('kept');
    await generated(page);
    await expect(dialog(page).locator('.dialog-actions .ca-primary')).toHaveText('Approve selected tags');
    await dialog(page).getByRole('spinbutton', { name: 'General confidence', exact: true }).fill('0.5');
    await model(page).selectOption('wd14-vit');
    await expect(chips(page)).toHaveCount(0);
    await expect(dialog(page).locator('#tag-suggestions > p.small')).toHaveText(MODEL_CHANGED);
    await expect(dialog(page).locator('#suggestion-count')).toHaveText('Not generated');
    await expect(status(page)).toHaveText('');
    await expect(dialog(page).locator('.dialog-actions .ca-primary')).toHaveText('Save manual tags');
    await expect(manual(page)).toHaveValue('kept');
    // Like the concept, the new model's fields start from the defaults.
    await expect(dialog(page).getByRole('spinbutton', { name: 'General confidence', exact: true })).toHaveValue('0.35');
});

test('invalid threshold reports validity without request', async ({ page }) => {
    const sent = [];
    page.on('request', item => { if (item.url().endsWith('/image-annotations/suggest')) sent.push(item.url()); });
    await openTag(page);
    const general = dialog(page).getByRole('spinbutton', { name: 'General confidence', exact: true });
    await general.fill('1.5');
    await generate(page).click();
    await expect(general).toBeFocused();
    expect(await general.evaluate(node => node.validity.rangeOverflow)).toBe(true);
    await general.fill('');
    await generate(page).click();
    await expect.poll(() => general.evaluate(node => node.validationMessage)).toBe('Enter a confidence from 0 to 1.');
    await general.fill('0.35');
    expect(await general.evaluate(node => node.validity.valid)).toBe(true);
    await model(page).selectOption('gguf');
    const instructions = dialog(page).getByRole('textbox', { name: 'Tagging instructions', exact: true });
    await instructions.fill('   ');
    await generate(page).click();
    await expect.poll(() => instructions.evaluate(node => node.validationMessage)).toBe('Enter tagging instructions of at most 4000 characters.');
    await expect(status(page)).toHaveText('');
    expect(sent).toEqual([]);
});

test('unavailable model shows inline status and never downloads', async ({ page, request }) => {
    await expect(await request.post('/__test__/tagging/models', { data: { unavailable: ['wd14-swinv2'] } })).toBeOK();
    await openTag(page);
    expect((await generated(page)).status()).toBe(409);
    await expect(status(page)).toHaveText('WD14 SwinV2 is not downloaded. Download it from the legacy Auto-tag page.');
    await expect(chips(page)).toHaveCount(0);
    await expect(dialog(page).locator('#suggestion-count')).toHaveText('Not generated');
    // Retry (ruling T5) leads the actions while the controller allows it, and generates again.
    await expect(dialog(page).locator('.dialog-actions button')).toHaveText(['Retry', 'Cancel', 'Save manual tags']);
    await expect(await request.post('/__test__/tagging/models', { data: { unavailable: [] } })).toBeOK();
    const again = page.waitForResponse(item => item.url().endsWith('/image-annotations/suggest'));
    await dialog(page).getByRole('button', { name: 'Retry', exact: true }).click();
    expect((await again).status()).toBe(200);
    await expect(chips(page)).toHaveCount(10);
    await expect(dialog(page).locator('.dialog-actions button')).toHaveText(['Cancel', 'Approve selected tags']);
    await expect(dialog(page).locator('.dialog-actions .ca-primary')).toBeFocused();
});

test('video tag is manual only', async ({ page, request }) => {
    await openTag(page, VIDEO);
    const preview = dialog(page).locator('video.tag-image-preview');
    await expect(preview).toHaveAttribute('preload', 'metadata');
    expect(await preview.evaluate(node => node.muted)).toBe(true);
    await expect(dialog(page).locator('.tag-image-heading span.chip')).toHaveCount(0);
    await expect(generate(page)).toHaveAttribute('aria-disabled', 'true');
    await expect(status(page)).toHaveText('Auto-tagging is available for supported still images only.');
    let suggested = false;
    page.on('request', item => { if (item.url().endsWith('/image-annotations/suggest')) suggested = true; });
    // Pressing the aria-disabled Generate sends nothing.
    await generate(page).focus();
    await page.keyboard.press('Enter');
    await manual(page).fill('clip_tag');
    await dialog(page).getByRole('button', { name: 'Save manual tags', exact: true }).click();
    await expect(toast(page)).toHaveText('1 tags saved for clip.webm.');
    expect(suggested).toBe(false);
    expect(await savedTags(request, VIDEO)).toEqual(['clip_tag']);
});

test('sticky footer reachable at 390x844 while content scrolls', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openTag(page);
    await model(page).selectOption('gguf');
    await generated(page);
    await expect(chips(page)).toHaveCount(10);
    const shell = page.locator('dialog#modal');
    const actions = dialog(page).locator('.dialog-actions');
    expect(await actions.evaluate(node => getComputedStyle(node).position)).toBe('sticky');
    expect(await shell.evaluate(node => node.scrollHeight > node.clientHeight)).toBe(true);
    for (const top of [0, 10_000]) {
        await shell.evaluate((node, value) => { node.scrollTop = value; }, top);
        const box = await actions.boundingBox(), frame = await shell.boundingBox();
        expect(box.y + box.height).toBeLessThanOrEqual(frame.y + frame.height + 1);
        expect(box.y).toBeGreaterThanOrEqual(frame.y);
        await expect(dialog(page).getByRole('button', { name: 'Approve selected tags', exact: true })).toBeInViewport();
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});

test('focus starts on close and returns to the Tag trigger', async ({ page }) => {
    await openTag(page, SECOND);
    await expect(dialog(page).getByRole('button', { name: 'Close dialog', exact: true })).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(dialog(page)).toHaveCount(0);
    await expect(tagButton(page, SECOND)).toBeFocused();
});

test('every Images and Library gallery card has Info then Tag with mock labels', async ({ page }) => {
    const expectCards = async locator => {
        const labels = await locator.evaluateAll(nodes => nodes.map(node => {
            const buttons = [...node.querySelectorAll('.media-info > .image-card-actions > button')];
            return { file: node.querySelector('.image-open').getAttribute('aria-label').replace(/^Open /, ''),
                buttons: buttons.map(button => [button.className, button.getAttribute('aria-label'), button.querySelector('svg.icon') !== null]) };
        }));
        expect(labels.length).toBeGreaterThan(0);
        for (const { file, buttons } of labels) {
            expect(buttons).toEqual([['icon-btn', `Inspect ${file}`, true], ['icon-btn', `Tag or auto-tag ${file}`, true]]);
        }
        return labels.map(({ file }) => file);
    };
    await page.goto(images);
    await expect(page.locator('article[data-image-key]')).toHaveCount(manifest.files.length);
    expect(await expectCards(page.locator('article[data-image-key]'))).toEqual(manifest.files.map(file => file.filename));
    await page.goto(`/prompt_manager/workspace#/library/${manifest.positive_id}`);
    const gallery = page.locator('.library-history .history-gallery article[data-image-key]');
    await expect(gallery.first()).toBeVisible();
    await expectCards(gallery);
    const observed = page.waitForResponse(item => item.url().endsWith('/image-annotations/observe'));
    await gallery.first().getByRole('button', { name: /^Tag or auto-tag / }).click();
    await expect(dialog(page)).toBeVisible();
    expect((await observed).status()).toBe(200);
    await dialog(page).getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(gallery.first().getByRole('button', { name: /^Tag or auto-tag / })).toBeFocused();
});

test('Tag opens the dialog without selecting the card', async ({ page }) => {
    const panel = page.getByRole('complementary', { name: 'Selected image details', exact: true });
    await page.goto(images);
    await expect(card(page, FIRST)).toHaveClass(/\bselected\b/);
    const before = await panel.innerText();
    await openTag(page, SECOND, { goto: false });
    await page.keyboard.press('Escape');
    await expect(card(page, SECOND)).not.toHaveClass(/\bselected\b/);
    await expect(card(page, FIRST)).toHaveClass(/\bselected\b/);
    expect(await panel.innerText()).toBe(before);
});

test('tagging one of two cards sharing a prompt leaves the other card, the prompt and the pair unchanged', async ({ page, request }) => {
    const before = await (await request.get('/__test__/tagging/snapshot')).json();
    expect(await savedTags(request, SECOND)).toEqual([]);
    expect(await savedTags(request, COPY)).toEqual([]);
    const pairBefore = await (await request.get(`/prompt_manager/library/${manifest.positive_id}`)).json();
    await openTag(page, FIRST);
    await manual(page).fill('only_this_file');
    await dialog(page).getByRole('button', { name: 'Save manual tags', exact: true }).click();
    await expect(toast(page)).toHaveText('1 tags saved for dunes_00001.png.');
    expect(await savedTags(request, FIRST)).toEqual(['only_this_file']);
    expect(await savedTags(request, SECOND)).toEqual([]);
    expect(await savedTags(request, COPY)).toEqual([]);
    expect(await (await request.get('/__test__/tagging/snapshot')).json()).toEqual(before);
    const pairAfter = await (await request.get(`/prompt_manager/library/${manifest.positive_id}`)).json();
    expect(pairAfter.success).toBe(true);
    expect({ ...pairAfter, library_epoch: null }).toEqual({ ...pairBefore, library_epoch: null });
});

test('Tags page Auto-tag images opens the dialog for the selected image and is disabled without one', async ({ page }) => {
    await page.goto('/prompt_manager/workspace#/tags');
    const autoTag = page.locator('#workspace-main .heading-actions').getByRole('button', { name: 'Auto-tag images', exact: true });
    await expect(page.locator('#workspace-main .heading-actions button')).toHaveText(['+ Create tag', 'Auto-tag images']);
    await expect(autoTag).toHaveClass(/ca-primary/);
    await expect(autoTag).toHaveAttribute('aria-disabled', 'true');
    // An aria-disabled control still takes a press; it only explains.
    await autoTag.focus();
    await page.keyboard.press('Enter');
    await expect(toast(page)).toHaveText('Select an image in Images first.');
    await expect(dialog(page)).toHaveCount(0);
    await nav(page, 'Images').click();
    await card(page, SECOND).getByRole('button', { name: 'Inspect dunes_00002.png', exact: true }).click();
    await expect(card(page, SECOND)).toHaveClass(/selected/);
    await nav(page, 'Tags & categories').click();
    await expect(autoTag).not.toHaveAttribute('aria-disabled', 'true');
    const observed = page.waitForResponse(item => item.url().endsWith('/image-annotations/observe'));
    await autoTag.click();
    expect((await observed).request().postDataJSON().target.path).toBe(SECOND);
    await expect(dialog(page).locator('.tag-image-heading p.small')).toHaveText('dunes_00002.png');
    await page.keyboard.press('Escape');
    await expect(autoTag).toBeFocused();
});

test('Images column shows image_count and updates after save', async ({ page }) => {
    await page.goto(images);
    await card(page, FIRST).getByRole('button', { name: 'Inspect dunes_00001.png', exact: true }).click();
    await nav(page, 'Tags & categories').click();
    const row = name => page.locator('#workspace-main table tbody tr').filter({ has: page.locator('td:first-child').getByText(name, { exact: true }) });
    await expect(row('minimal').locator('td:nth-child(3)')).toHaveText('0');
    await expect(row('editorial').locator('td:nth-child(3)')).toHaveText('0');
    await page.locator('#workspace-main .heading-actions').getByRole('button', { name: 'Auto-tag images', exact: true }).click();
    await expect(manual(page)).toBeVisible();
    await expect(dialog(page).locator('img.tag-image-preview')).toHaveAttribute('src', /capture/);
    await manual(page).fill('minimal, editorial');
    const reread = page.waitForResponse(item => item.url().includes('/prompt_manager/vocabulary/tags'));
    await dialog(page).getByRole('button', { name: 'Save manual tags', exact: true }).click();
    await reread;
    await expect(row('minimal').locator('td:nth-child(3)')).toHaveText('1');
    await expect(row('editorial').locator('td:nth-child(3)')).toHaveText('1');
    await expect(row('warm_light').locator('td:nth-child(3)')).toHaveText('0');
});

test('virtualized cards release listeners on unmount', async ({ page, request }) => {
    const extra = 140;
    await expect(await request.post('/__test__/tagging/reset', { data: { extra_images: extra } })).toBeOK();
    const count = () => page.evaluate(async url => (await import(url)).imageTagsListenerCount(), MODULE);
    await page.goto(images);
    await expect(card(page, FIRST)).toBeVisible();
    const mounted = await count();
    // Leaving and returning to Images several times keeps one subscription per mounted page.
    for (let round = 0; round < 3; round += 1) {
        await nav(page, 'Tags & categories').click();
        await expect(page.locator('#workspace-main h1')).toHaveText('Tags & categories');
        await nav(page, 'Images').click();
        await expect(card(page, FIRST)).toBeVisible();
    }
    expect(await count()).toBe(mounted);
    // Cards scrolled out are unmounted; the delegated Tag still works on a card rendered again.
    const continuation = page.locator('#gallery-continuation');
    await expect(async () => {
        await continuation.scrollIntoViewIfNeeded();
        await expect(continuation).toContainText('You’re all caught up', { timeout: 1000 });
    }).toPass();
    await continuation.scrollIntoViewIfNeeded();
    await expect(card(page, FIRST)).toHaveCount(0);
    expect(await count()).toBe(mounted);
    await page.evaluate(() => window.scrollTo(0, 0));
    await expect(card(page, FIRST)).toBeVisible();
    await openTag(page, FIRST, { goto: false });
    await page.keyboard.press('Escape');
    await expect(tagButton(page, FIRST)).toBeFocused();
    expect(await count()).toBe(mounted);
});

// Paired captures for review (the exact concept comparison is plan Task C4): the concept's dialog and production's,
// before and after Generate, on the concept-shaped fixture. Only that both open and generate is asserted here.
for (const [width, height] of [[1600, 1100], [390, 844]]) test(`paired Tag dialog captures ${width}x${height}`, async ({ page, request }, testInfo) => {
    await seedConcept(request);
    await page.setViewportSize({ width, height });
    const shot = async name => {
        await page.evaluate(async () => { await document.fonts.ready; await Promise.all([...document.images].map(image => image.decode().catch(() => {}))); });
        const path = testInfo.outputPath(`${name}.png`);
        await page.screenshot({ path });
        await testInfo.attach(name, { path, contentType: 'image/png' });
    };
    for (const side of ['concept', 'production']) {
        if (side === 'concept') await openConcept(page, 'images', 'dark');
        else await openProduction(page, 'images', 'dark');
        const first = page.locator(side === 'concept' ? '#image-grid > article.media-card' : 'article[data-image-key]').first();
        await first.getByRole('button', { name: /^Tag or auto-tag / }).click();
        const shell = page.getByRole('dialog', { name: 'Tag image', exact: true });
        await expect(shell).toBeVisible();
        if (side === 'production') await expect(shell.locator('img.tag-image-preview')).toHaveAttribute('src', /capture/);
        await shot(`${side}-tag-dialog-${width}`);
        await shell.getByRole('button', { name: 'Generate suggestions', exact: true }).click();
        await expect(shell.locator('#suggestion-count')).not.toHaveText('Not generated');
        await shot(`${side}-tag-dialog-generated-${width}`);
    }
});

// Task C3 (D88): the side panel and the viewer details draw the file's own current image tags first, then D21's linked
// tags not already shown (case-insensitive). Panel image tags are Tags filter buttons; the viewer's chips are plain.
const panel = page => page.getByRole('complementary', { name: 'Selected image details', exact: true });
const tagRow = page => panel(page).locator('.detail-inner > .tags').last();
const inspected = (page, path) => page.waitForResponse(item => new URL(item.url()).pathname === `/prompt_manager/images/inspect/${path}`);
const infoButton = (page, path) => card(page, path).getByRole('button', { name: `Inspect ${path.split('/').pop()}`, exact: true });

test('inspector shows saved image tags after Save without reselecting', async ({ page }) => {
    await page.goto(images);
    await expect(card(page, FIRST)).toHaveClass(/\bselected\b/);
    // D21/D39: the linked recipe's own tag, plain.
    await expect(tagRow(page).locator('.chip')).toHaveText(manifest.pair_tags);
    await expect(tagRow(page).locator('button.chip')).toHaveCount(0);
    await openTag(page, FIRST, { goto: false });
    await manual(page).fill('dunes_tag, Editorial');
    const reread = inspected(page, FIRST);
    await dialog(page).getByRole('button', { name: 'Save manual tags', exact: true }).click();
    expect((await (await reread).json()).image_tags).toEqual({ state: 'current', tags: ['dunes_tag', 'Editorial'] });
    // The image tags lead as filter buttons; the recipe's "editorial" is already shown as "Editorial".
    await expect(tagRow(page).locator('.chip')).toHaveText(['dunes_tag', 'Editorial']);
    await expect(tagRow(page).locator('button.chip')).toHaveText(['dunes_tag', 'Editorial']);
    await expect(card(page, FIRST)).toHaveClass(/\bselected\b/);
    await expect(panel(page).locator('.panel-heading h2')).toHaveText(manifest.title);
});

test('clicking an image tag chip filters Images to the tagged unlinked file', async ({ page, request }) => {
    expect(await saveImageTags(request, UNLINKED, ['loose_tag'])).toEqual(['loose_tag']);
    await page.goto(images);
    const read = inspected(page, UNLINKED);
    await infoButton(page, UNLINKED).click();
    await read;
    await expect(panel(page).locator('.panel-heading h2')).toHaveText('unlinked.png');
    await expect(tagRow(page).locator('.chip')).toHaveText(['loose_tag']);
    const listing = page.waitForRequest(item => item.url().includes('/images/output?') && new URL(item.url()).searchParams.get('tags') === 'loose_tag');
    await tagRow(page).getByRole('button', { name: 'loose_tag', exact: true }).click();
    await listing;
    await expect(page.locator('.gallery-body article[data-image-key]')).toHaveCount(1);
    await expect(card(page, UNLINKED)).toBeVisible();
    await expect(page.getByRole('button', { name: 'Filters · 1', exact: true })).toBeVisible();
});

test('viewer details show the same chips', async ({ page, request }) => {
    await saveImageTags(request, FIRST, ['dunes_tag', 'Editorial', 'sunlit']);
    await page.goto(images);
    await expect(tagRow(page).locator('button.chip')).toHaveText(['dunes_tag', 'Editorial', 'sunlit']);
    await card(page, FIRST).locator('.image-open').click();
    const details = page.locator('#viewer-dialog[open] #viewer-details');
    await expect(details.locator('.panel-heading h2')).toHaveText(manifest.title);
    const row = details.locator('.tags').last();
    await expect(row.locator('.chip')).toHaveText(['dunes_tag', 'Editorial', 'sunlit']);
    await expect(row.locator('span.chip')).toHaveCount(3);
    await expect(row.locator('button')).toHaveCount(0);
    // A file without image tags: the viewer shows only D21's linked tags.
    await page.getByRole('button', { name: 'Next media', exact: true }).click();
    await expect(page.locator('#viewer-dialog[open] #viewer-title')).toHaveText(`${manifest.title} · dunes_00002.png`);
    await expect(details.locator('.tags').last().locator('.chip')).toHaveText(manifest.pair_tags);
});

test('search finds an image by its image tag', async ({ page, request }) => {
    await saveImageTags(request, UNLINKED, ['Loose_Tag']);
    await page.goto(images);
    await expect(page.locator('.gallery-body article[data-image-key]')).toHaveCount(manifest.files.length);
    const listing = page.waitForRequest(item => item.url().includes('/images/output?') && new URL(item.url()).searchParams.get('q') === 'loose_t');
    await page.getByRole('searchbox', { name: 'Search workspace', exact: true }).fill('loose_t');
    await listing;
    await expect(page.locator('.gallery-body article[data-image-key]')).toHaveCount(1);
    await expect(card(page, UNLINKED)).toBeVisible();
});

// Task C4 rulings: a save re-reads the selected file's details without a loading flash, and under a Tags filter or a
// search the loaded listing is re-read in place (scroll and selection kept while the file still matches).
const cards = page => page.locator('.gallery-body article[data-image-key]');
const listingOf = (page, check) => page.waitForResponse(item => item.url().includes('/images/output?') && check(new URL(item.url()).searchParams));

test('the post-save re-read keeps the panel content until the new read lands', async ({ page }) => {
    await page.goto(images);
    await expect(card(page, FIRST)).toHaveClass(/\bselected\b/);
    await expect(tagRow(page).locator('.chip')).toHaveText(manifest.pair_tags);
    await page.evaluate(() => {
        const node = document.querySelector('aside[aria-label="Selected image details"]');
        window.__loadingSeen = false;
        window.__loadingWatch = new MutationObserver(() => { if (node.textContent.includes('Loading file details')) window.__loadingSeen = true; });
        window.__loadingWatch.observe(node, { childList: true, subtree: true, characterData: true });
    });
    let release;
    const held = new Promise(resolve => { release = resolve; });
    const matcher = url => url.pathname === `/prompt_manager/images/inspect/${FIRST}`;
    await page.route(matcher, async route => { await held; await route.continue(); });
    await openTag(page, FIRST, { goto: false });
    await manual(page).fill('held_tag');
    const reread = page.waitForRequest(item => new URL(item.url()).pathname === `/prompt_manager/images/inspect/${FIRST}`);
    await dialog(page).getByRole('button', { name: 'Save manual tags', exact: true }).click();
    await reread;
    await expect(toast(page)).toHaveText('1 tags saved for dunes_00001.png.');
    // While the re-read is held, the panel still shows the file's details.
    await expect(panel(page).locator('.panel-heading h2')).toHaveText(manifest.title);
    await expect(tagRow(page).locator('.chip')).toHaveText(manifest.pair_tags);
    expect(await page.evaluate(() => window.__loadingSeen)).toBe(false);
    const landed = inspected(page, FIRST);
    release();
    await landed;
    await expect(tagRow(page).locator('.chip')).toHaveText(['held_tag', ...manifest.pair_tags]);
    expect(await page.evaluate(() => { window.__loadingWatch.disconnect(); return window.__loadingSeen; })).toBe(false);
    await page.unroute(matcher);
});

test('a save under a Tags filter re-reads the listing: the selection stays while it matches and follows when it leaves', async ({ page, request }) => {
    await saveImageTags(request, UNLINKED, ['shared']);
    await saveImageTags(request, SECOND, ['shared']);
    await page.goto(images);
    const read = inspected(page, SECOND);
    await infoButton(page, SECOND).click();
    await read;
    const filtered = listingOf(page, query => query.get('tags') === 'shared');
    await tagRow(page).getByRole('button', { name: 'shared', exact: true }).click();
    await filtered;
    await expect(cards(page)).toHaveCount(2);
    // The newest match (SECOND) is selected once the filter applies.
    await expect(card(page, SECOND)).toHaveClass(/\bselected\b/);
    // Tagging the other match keeps both cards and SECOND selected; the listing is re-read.
    await openTag(page, UNLINKED, { goto: false });
    await manual(page).fill('shared, extra');
    let reread = listingOf(page, query => query.get('tags') === 'shared' && !query.has('cursor'));
    await dialog(page).getByRole('button', { name: 'Save manual tags', exact: true }).click();
    await reread;
    await expect(cards(page)).toHaveCount(2);
    await expect(card(page, SECOND)).toHaveClass(/\bselected\b/);
    await expect(tagButton(page, UNLINKED)).toBeFocused();
    // SECOND loses the filter tag: it leaves the result and the panel follows the remaining match.
    await openTag(page, SECOND, { goto: false });
    await manual(page).fill('');
    reread = listingOf(page, query => query.get('tags') === 'shared' && !query.has('cursor'));
    const next = inspected(page, UNLINKED);
    await dialog(page).getByRole('button', { name: 'Save manual tags', exact: true }).click();
    await reread;
    await next;
    await expect(cards(page)).toHaveCount(1);
    await expect(card(page, SECOND)).toHaveCount(0);
    await expect(card(page, UNLINKED)).toHaveClass(/\bselected\b/);
    await expect(panel(page).locator('.panel-heading h2')).toHaveText('unlinked.png');
    await expect(page.getByText('1 of 1 image', { exact: false }).first()).toBeVisible();
});

test('a save under a search keeps the scroll position and the loaded range', async ({ page, request }) => {
    await expect(await request.post('/__test__/tagging/reset', { data: { extra_images: 140 } })).toBeOK();
    await page.goto(images);
    const searched = listingOf(page, query => query.get('q') === 'bulk');
    await page.getByRole('searchbox', { name: 'Search workspace', exact: true }).fill('bulk');
    await searched;
    const continuation = page.locator('#gallery-continuation');
    // Load at least two pages (the search lists 40 a page), then rest a third of the way down.
    const loadedCount = async () => {
        const text = await continuation.textContent();
        return /caught up/.test(text) ? 140 : Number(/^(\d+) of 140/.exec(text)?.[1] ?? 0);
    };
    await expect.poll(async () => { await continuation.scrollIntoViewIfNeeded(); return loadedCount(); }).toBeGreaterThanOrEqual(80);
    await page.evaluate(() => window.scrollTo(0, Math.round(document.documentElement.scrollHeight / 3)));
    await expect(continuation).not.toBeInViewport();
    const loaded = await continuation.textContent();
    await expect.poll(() => continuation.textContent()).toBe(loaded);
    const y = await page.evaluate(() => window.scrollY);
    expect(y).toBeGreaterThan(0);
    const visible = await cards(page).evaluateAll(nodes => nodes.find(node => { const box = node.getBoundingClientRect(); return box.top > 80 && box.bottom < innerHeight; })?.dataset.imageKey);
    expect(visible).toBeTruthy();
    const path = visible.slice(2);
    await openTag(page, path, { goto: false });
    await manual(page).fill('kept_in_place');
    const reread = listingOf(page, query => query.get('q') === 'bulk' && !query.has('cursor'));
    await dialog(page).getByRole('button', { name: 'Save manual tags', exact: true }).click();
    await reread;
    await expect(tagButton(page, path)).toBeFocused();
    await expect(continuation).toHaveText(loaded);
    expect(Math.abs(await page.evaluate(() => window.scrollY) - y)).toBeLessThanOrEqual(1);
});
