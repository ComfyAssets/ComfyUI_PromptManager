// The Tag image dialog, the card Tag button and the Tags page hooks (#249 PR C, plan Task C2, Mock inventory M1, M2, M5)
// against the real routes and the injected fake model (tests/browser/workspace_tagging_fixture.py). No model is ever
// downloaded or loaded: the fake answers, and every test checks that no download request was sent.
import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';
import { saveImageTags } from './concept-inventory.mjs';
import { taggingInventory } from './tagging-canonical-comparison.mjs';

const images = '/prompt_manager/workspace#/images';
const MODULE = '/prompt_manager/js/workspace/image-tagging.mjs';
const MODEL_LABELS = ['WD14 SwinV2 · ~400 MB', 'WD14 ViT · ~400 MB', 'GGUF JoyCaption (Recommended) · ~4.5 GB', 'HuggingFace JoyCaption · ~16 GB'];
// The concept's on-screen opening sentence (its renderTagModelOptions replaces the markup's at once, D83).
const MODEL_CHANGED = 'Generate suggestions with the selected model before approving.';
const CHIP_HINT = 'Remove anything you do not want to add.';
const ALL_REMOVED = 'All suggestions removed. Generate again or enter tags manually.';
const card = (page, path) => page.locator(`article[data-image-key="0:${path}"]`);
const tagButton = (page, path) => card(page, path).getByRole('button', { name: `Tag or auto-tag ${path.split('/').pop()}`, exact: true });
const dialog = page => page.getByRole('dialog', { name: 'Tag image', exact: true });
const manual = page => dialog(page).getByRole('textbox', { name: 'Image tags · edit manually', exact: true });
const model = page => dialog(page).getByRole('combobox', { name: 'Tagging model', exact: true });
const generate = page => dialog(page).getByRole('button', { name: 'Generate suggestions', exact: true });
const chips = page => dialog(page).locator('#tag-suggestions .chip.tag-suggestion');
const status = page => dialog(page).locator('#tag-generation-status');
// D147: the reason a state blocks Save, on its own row in the sticky actions.
const notice = page => dialog(page).locator('.dialog-actions > .tag-action-notice');
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
    await expect(suggestions.locator('#tag-suggestions > p.small')).toHaveText(MODEL_CHANGED);
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
    // D83: ruling T4's instruction leads the chips, only while chips exist.
    await expect(dialog(page).locator('#tag-suggestions > *')).toHaveCount(2);
    await expect(dialog(page).locator('#tag-suggestions > p.small')).toHaveText(CHIP_HINT);
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
    await expect(status(page)).toHaveText('WD14 SwinV2 is not downloaded. Download it in System › Auto-tagging.');
    await expect(chips(page)).toHaveCount(0);
    await expect(dialog(page).locator('#suggestion-count')).toHaveText('Not generated');
    // D116: Retry leads the actions while the controller allows it, and generates again.
    await expect(dialog(page).locator('.dialog-actions button')).toHaveText(['Retry', 'Cancel', 'Save manual tags']);
    await expect(await request.post('/__test__/tagging/models', { data: { unavailable: [] } })).toBeOK();
    const again = page.waitForResponse(item => item.url().endsWith('/image-annotations/suggest'));
    await dialog(page).getByRole('button', { name: 'Retry', exact: true }).click();
    expect((await again).status()).toBe(200);
    await expect(chips(page)).toHaveCount(10);
    await expect(dialog(page).locator('.dialog-actions button')).toHaveText(['Cancel', 'Approve selected tags']);
    await expect(dialog(page).locator('.dialog-actions .ca-primary')).toBeFocused();
});

test('a missing backend package is the server\'s install sentence, no traceback page (#424)', async ({ page, request }) => {
    await expect(await request.post('/__test__/tagging/models', { data: { unavailable: [],
        missing_packages: { 'wd14-swinv2': ['onnxruntime'] } } })).toBeOK();
    await openTag(page);
    const answer = await generated(page);
    expect(answer.status()).toBe(409);
    expect(await answer.json()).toMatchObject({ success: false, code: 'model-unavailable' });
    await expect(status(page)).toHaveText('WD14 SwinV2 needs onnxruntime. Install it into ComfyUI\'s Python: '
        + 'pip install onnxruntime. Use onnxruntime-gpu for CUDA.');
    await expect(chips(page)).toHaveCount(0);
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
    // Capture the panel once it shows the first card's details, not while it is still reading them.
    await expect(panel.getByRole('heading', { level: 2 })).toBeVisible();
    await expect(panel).not.toContainText('Loading file details');
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

// Task C3 (D88), #449 (D154): the side panel and the viewer details draw the file's own current image tags in their own
// "Image tags" section, then D21's linked tags in a "Prompt tags" or "Recipe tags" section after it. Panel image tags are
// Tags filter buttons; the viewer's chips are plain.
const panel = page => page.getByRole('complementary', { name: 'Selected image details', exact: true });
const imageTags = page => panel(page).getByRole('region', { name: 'Image tags', exact: true }).locator('.tags');
const linkedTags = page => panel(page).locator('.tag-section.linked-tags > .tags');
const inspected = (page, path) => page.waitForResponse(item => new URL(item.url()).pathname === `/prompt_manager/images/inspect/${path}`);
const infoButton = (page, path) => card(page, path).getByRole('button', { name: `Inspect ${path.split('/').pop()}`, exact: true });

test('inspector shows saved image tags after Save without reselecting', async ({ page }) => {
    await page.goto(images);
    await expect(card(page, FIRST)).toHaveClass(/\bselected\b/);
    // D21/D39: the linked recipe's own tag, plain, and no image tags yet.
    await expect(linkedTags(page).locator('.chip')).toHaveText(manifest.pair_tags);
    await expect(linkedTags(page).locator('button.chip')).toHaveCount(0);
    await expect(panel(page).locator('.tag-section.image-tags')).toHaveCount(0);
    await openTag(page, FIRST, { goto: false });
    await manual(page).fill('dunes_tag, Editorial');
    const reread = inspected(page, FIRST);
    await dialog(page).getByRole('button', { name: 'Save manual tags', exact: true }).click();
    expect((await (await reread).json()).image_tags).toEqual({ state: 'current', tags: ['dunes_tag', 'Editorial'] });
    // The image tags lead as filter buttons in their own section; the recipe keeps its "editorial" in its own (D154).
    await expect(imageTags(page).locator('.chip')).toHaveText(['dunes_tag', 'Editorial']);
    await expect(imageTags(page).locator('button.chip')).toHaveText(['dunes_tag', 'Editorial']);
    await expect(panel(page).locator('.tag-section.image-tags .role-label')).toHaveText('Image tags · 2');
    await expect(linkedTags(page).locator('.chip')).toHaveText(manifest.pair_tags);
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
    await expect(imageTags(page).locator('.chip')).toHaveText(['loose_tag']);
    await expect(panel(page).locator('.tag-section.linked-tags')).toHaveCount(0);
    const listing = page.waitForRequest(item => item.url().includes('/images/output?') && new URL(item.url()).searchParams.get('tags') === 'loose_tag');
    await imageTags(page).getByRole('button', { name: 'loose_tag', exact: true }).click();
    await listing;
    await expect(page.locator('.gallery-body article[data-image-key]')).toHaveCount(1);
    await expect(card(page, UNLINKED)).toBeVisible();
    await expect(page.getByRole('button', { name: 'Filters · 1', exact: true })).toBeVisible();
});

test('viewer details show the same chips', async ({ page, request }) => {
    await saveImageTags(request, FIRST, ['dunes_tag', 'Editorial', 'sunlit']);
    await page.goto(images);
    await expect(imageTags(page).locator('button.chip')).toHaveText(['dunes_tag', 'Editorial', 'sunlit']);
    await card(page, FIRST).locator('.image-open').click();
    const details = page.locator('#viewer-dialog[open] #viewer-details');
    await expect(details.locator('.panel-heading h2')).toHaveText(manifest.title);
    const row = details.getByRole('region', { name: 'Image tags', exact: true }).locator('.tags');
    await expect(row.locator('.chip')).toHaveText(['dunes_tag', 'Editorial', 'sunlit']);
    await expect(row.locator('span.chip')).toHaveCount(3);
    await expect(row.locator('button')).toHaveCount(0);
    await expect(details.locator('.tag-section.linked-tags .chip')).toHaveText(manifest.pair_tags);
    // A file without image tags: the viewer shows only D21's linked tags.
    await page.getByRole('button', { name: 'Next media', exact: true }).click();
    await expect(page.locator('#viewer-dialog[open] #viewer-title')).toHaveText(`${manifest.title} · dunes_00002.png`);
    await expect(details.locator('.tag-section.linked-tags .chip')).toHaveText(manifest.pair_tags);
    await expect(details.locator('.tag-section.image-tags')).toHaveCount(0);
});

// #449 (D154): the image tags are a labelled region of their own after the negative prompt, framed like a prompt section,
// so they never read as part of it; the same in the side panel and the viewer details.
async function tagSectionPlacement(root) {
    return root.evaluate(node => {
        const negative = node.querySelector('section.prompt-section.negative'), tags = node.querySelector('section.tag-section.image-tags');
        const style = getComputedStyle(tags), label = getComputedStyle(tags.querySelector('.prompt-heading .role-label'));
        return {
            after: Boolean(negative.compareDocumentPosition(tags) & Node.DOCUMENT_POSITION_FOLLOWING), inside: negative.contains(tags),
            below: tags.getBoundingClientRect().top >= negative.getBoundingClientRect().bottom,
            border: `${style.borderTopWidth} ${style.borderTopStyle}`, paddingTop: style.paddingTop, marginTop: style.marginTop,
            label: {transform: label.textTransform, size: label.fontSize, sameColorAsNegative: label.color === getComputedStyle(negative.querySelector('.role-label')).color},
        };
    });
}
const PLACED = {after: true, inside: false, below: true, border: '1px solid', paddingTop: '15px', marginTop: '15px',
    label: {transform: 'uppercase', size: '10px', sameColorAsNegative: false}};

test('D154: image tags sit in their own labelled section after the negative prompt, in the panel and the viewer', async ({ page, request }) => {
    await saveImageTags(request, FIRST, ['dunes_tag', 'sunlit']);
    await page.goto(images);
    const section = panel(page).getByRole('region', { name: 'Image tags', exact: true });
    await expect(section.locator('.role-label')).toHaveText('Image tags · 2');
    await expect(section.locator('.tags > button.chip')).toHaveText(['dunes_tag', 'sunlit']);
    expect(await tagSectionPlacement(panel(page))).toEqual(PLACED);
    await card(page, FIRST).locator('.image-open').click();
    const details = page.locator('#viewer-dialog[open] #viewer-details');
    await expect(details.locator('.panel-heading h2')).toHaveText(manifest.title);
    const viewerSection = details.getByRole('region', { name: 'Image tags', exact: true });
    await expect(viewerSection.locator('.role-label')).toHaveText('Image tags · 2');
    await expect(viewerSection.locator('.tags > span.chip')).toHaveText(['dunes_tag', 'sunlit']);
    expect(await tagSectionPlacement(details)).toEqual(PLACED);
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

// #379 CUJ 8: the Tags filter finds images by their own image tags through the tag index; no prompt carries them, and
// selecting a match shows its prompt. A linked prompt's tag still combines with the image tags.
test('the Tags filter finds images by image tags only, and selecting one shows its prompt (#379)', async ({ page, request }) => {
    await saveImageTags(request, FIRST, ['beach', '1girl', 'waves']);
    await saveImageTags(request, SECOND, ['beach', 'waves']);
    await saveImageTags(request, UNLINKED, ['beach', '1girl', 'waves', 'gulls']);
    await page.goto(images);
    await expect(page.locator('.gallery-body article[data-image-key]')).toHaveCount(manifest.files.length);
    const filters = page.locator('#workspace-main search.toolbar').getByRole('button', { name: /^Filters(?: · \d+)?$/ });
    const refine = page.getByRole('dialog', { name: 'Refine this view', exact: true });
    const applyTags = async value => {
        await filters.click();
        await refine.getByLabel('Tags', { exact: true }).fill(value);
        const listing = page.waitForRequest(item => item.url().includes('/images/output?')
            && new URL(item.url()).searchParams.get('tags') !== null);
        await refine.getByRole('button', { name: 'Apply filters', exact: true }).click();
        await listing;
    };
    await applyTags('beach, 1girl, waves');
    // Every listed image whose image tags hold all three; SECOND lacks 1girl.
    await expect(page.locator('.gallery-body article[data-image-key]')).toHaveCount(2);
    await expect(card(page, FIRST)).toBeVisible();
    await expect(card(page, UNLINKED)).toBeVisible();
    await expect(card(page, SECOND)).toHaveCount(0);
    // The newest match is selected; the inspector shows its linked prompt.
    await infoButton(page, FIRST).click();
    await expect(card(page, FIRST)).toHaveClass(/\bselected\b/);
    await expect(panel(page).locator('.panel-heading h2')).toHaveText(manifest.title);
    // The linked positive's own tag combines: SECOND has beach and waves itself and minimal through its prompt.
    await applyTags(`beach, waves, ${manifest.prompt_tags[0]}`);
    await expect(page.locator('.gallery-body article[data-image-key]')).toHaveCount(2);
    await expect(card(page, FIRST)).toBeVisible();
    await expect(card(page, SECOND)).toBeVisible();
});

// Task C4 rulings: a save re-reads the selected file's details without a loading flash, and under a Tags filter or a
// search the loaded listing is re-read in place (scroll and selection kept while the file still matches).
const cards = page => page.locator('.gallery-body article[data-image-key]');
const listingOf = (page, check) => page.waitForResponse(item => item.url().includes('/images/output?') && check(new URL(item.url()).searchParams));

test('the post-save re-read keeps the panel content until the new read lands', async ({ page }) => {
    await page.goto(images);
    await expect(card(page, FIRST)).toHaveClass(/\bselected\b/);
    await expect(linkedTags(page).locator('.chip')).toHaveText(manifest.pair_tags);
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
    await expect(linkedTags(page).locator('.chip')).toHaveText(manifest.pair_tags);
    expect(await page.evaluate(() => window.__loadingSeen)).toBe(false);
    const landed = inspected(page, FIRST);
    release();
    await landed;
    await expect(imageTags(page).locator('.chip')).toHaveText(['held_tag']);
    await expect(linkedTags(page).locator('.chip')).toHaveText(manifest.pair_tags);
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
    await imageTags(page).getByRole('button', { name: 'shared', exact: true }).click();
    await filtered;
    await expect(cards(page)).toHaveCount(2);
    // The newest match (SECOND) is selected once the filter applies.
    await expect(card(page, SECOND)).toHaveClass(/\bselected\b/);
    // Both matches checked: the selection bar keeps only what the refreshed result still lists (I7).
    for (const path of [SECOND, UNLINKED]) await card(page, path).getByRole('checkbox', { name: `Select ${path.split('/').pop()}`, exact: true }).check();
    const bulkbar = page.locator('#bulkbar .bulkbar');
    await expect(bulkbar.locator('strong')).toHaveText('2 selected');
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
    await expect(bulkbar.locator('strong')).toHaveText('1 selected');
    await expect(card(page, UNLINKED).getByRole('checkbox')).toBeChecked();
    // Export metadata acts on exactly the one file the bar counts.
    const exported = [];
    page.on('request', item => { if (item.url().includes('/prompt_manager/images/inspect/')) exported.push(new URL(item.url()).pathname); });
    const download = page.waitForEvent('download');
    await bulkbar.getByRole('button', { name: 'Export metadata', exact: true }).click();
    await download;
    expect(exported).toEqual([`/prompt_manager/images/inspect/${UNLINKED}`]);
});

test('a save under a search keeps the scroll position and the loaded range', async ({ page, request }) => {
    await expect(await request.post('/__test__/tagging/reset', { data: { extra_images: 140 } })).toBeOK();
    // Listing pages still in flight: one that lands after the scroll below would grow the loaded range.
    const inFlight = new Set();
    page.on('request', item => { if (item.url().includes('/images/output?')) inFlight.add(item); });
    for (const done of ['requestfinished', 'requestfailed']) page.on(done, item => inFlight.delete(item));
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
    // A load the continuation scheduled for the next frame has started by now; wait for every page to land.
    await page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))));
    await expect.poll(() => inFlight.size).toBe(0);
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

// Final review round (I1, I2, I9, M5, M6).
test('the option fields show the server defaults that Generate sends (I1)', async ({ page, request }) => {
    await expect(await request.post('/__test__/tagging/defaults', { data: { general: 0.5, character: 0.9, limit: 7 } })).toBeOK();
    await openTag(page);
    const field = name => dialog(page).getByRole('spinbutton', { name, exact: true });
    await expect(field('General confidence')).toHaveValue('0.5');
    await expect(field('Character confidence')).toHaveValue('0.9');
    await expect(field('Suggestions')).toHaveValue('7');
    const answer = await generated(page);
    expect(answer.request().postDataJSON().options).toEqual({ general_threshold: 0.5, character_threshold: 0.9, suggestion_limit: 7 });
    await expect(chips(page)).toHaveCount(7);
    // A model change re-renders the new model's fields from the server defaults, and keeps the count (D91).
    await field('Suggestions').fill('4');
    await model(page).selectOption('wd14-vit');
    await expect(field('General confidence')).toHaveValue('0.5');
    await expect(field('Suggestions')).toHaveValue('4');
});

test('a tag conflict shows the other writer\'s tags before Save, and a second Save keeps them (I2)', async ({ page, request }) => {
    await openTag(page);
    await manual(page).fill('mine');
    // Another writer (a second tab, the legacy page) saves while the dialog is open.
    expect(await saveImageTags(request, FIRST, ['changed_elsewhere'])).toEqual(['changed_elsewhere']);
    const conflict = page.waitForResponse(item => item.url().endsWith('/image-annotations/tags') && item.request().method() === 'PUT');
    await dialog(page).getByRole('button', { name: 'Save manual tags', exact: true }).click();
    expect((await conflict).status()).toBe(409);
    await expect(status(page)).toContainText('Saved now: changed_elsewhere.');
    await expect(manual(page)).toHaveValue('changed_elsewhere, mine');
    await expect(dialog(page)).toBeVisible();
    const second = page.waitForResponse(item => item.url().endsWith('/image-annotations/tags') && item.request().method() === 'PUT');
    await dialog(page).getByRole('button', { name: 'Save manual tags', exact: true }).click();
    expect((await second).request().postDataJSON().tags).toEqual(['changed_elsewhere', 'mine']);
    await expect(toast(page)).toHaveText('2 tags saved for dunes_00001.png.');
    expect(await savedTags(request, FIRST)).toEqual(['changed_elsewhere', 'mine']);
});

test('D116: Retry leads the actions after an unconfirmed save and resends the same tags', async ({ page, request }) => {
    await openTag(page);
    await manual(page).fill('retried');
    let lost = true;
    await page.route('**/prompt_manager/image-annotations/tags', route => (lost ? (lost = false, route.fulfill({ status: 504, body: 'Gateway Timeout' })) : route.continue()));
    await dialog(page).getByRole('button', { name: 'Save manual tags', exact: true }).click();
    await expect(notice(page)).toHaveText('The save could not be confirmed. Retry sends the same tags again.');
    await expect(status(page)).toHaveText('');
    const inventory = await page.locator('dialog#modal').evaluate(taggingInventory);
    expect(inventory.actions.buttons.map(button => [button.text, button.primary, button.disabled])).toEqual(
        [['Retry', false, false], ['Cancel', false, false], ['Save manual tags', true, true]]);
    expect(inventory.actions.buttons[0].minHeight).toBe(inventory.actions.buttons[1].minHeight);
    const resent = page.waitForResponse(item => item.url().endsWith('/image-annotations/tags') && item.request().method() === 'PUT');
    await dialog(page).getByRole('button', { name: 'Retry', exact: true }).click();
    expect((await resent).request().postDataJSON().tags).toEqual(['retried']);
    await expect(toast(page)).toHaveText('1 tags saved for dunes_00001.png.');
    expect(await savedTags(request, FIRST)).toEqual(['retried']);
    await page.unroute('**/prompt_manager/image-annotations/tags');
});

test('D116: Retry after an expired capture observes again and keeps the typed draft (M5)', async ({ page, request }) => {
    await openTag(page);
    await manual(page).fill('kept_draft');
    // The store forgets every capture (as its idle sweep would), so Save answers capture-expired.
    await expect(await request.post('/__test__/tagging/suggestions', { data: { scored: null } })).toBeOK();
    await dialog(page).getByRole('button', { name: 'Save manual tags', exact: true }).click();
    await expect(dialog(page).locator('.dialog-actions .ca-primary')).toHaveAttribute('aria-disabled', 'true');
    const observed = page.waitForResponse(item => item.url().endsWith('/image-annotations/observe'));
    await dialog(page).getByRole('button', { name: 'Retry', exact: true }).click();
    expect((await observed).status()).toBe(200);
    await expect(dialog(page).locator('.dialog-actions button')).toHaveText(['Cancel', 'Save manual tags']);
    await expect(manual(page)).toHaveValue('kept_draft');
    await dialog(page).getByRole('button', { name: 'Save manual tags', exact: true }).click();
    await expect(toast(page)).toHaveText('1 tags saved for dunes_00001.png.');
    expect(await savedTags(request, FIRST)).toEqual(['kept_draft']);
});

test('a tag save from Tags reloads a filtered Images visit instead of restoring it (M6)', async ({ page, request }) => {
    await saveImageTags(request, SECOND, ['shared']);
    await saveImageTags(request, UNLINKED, ['shared']);
    await page.goto(images);
    await infoButton(page, SECOND).click();
    const filtered = listingOf(page, query => query.get('tags') === 'shared');
    await imageTags(page).getByRole('button', { name: 'shared', exact: true }).click();
    await filtered;
    await expect(cards(page)).toHaveCount(2);
    await expect(card(page, SECOND)).toHaveClass(/\bselected\b/);
    // Tags › Auto-tag images tags the selected SECOND without the filter tag.
    await nav(page, 'Tags & categories').click();
    await page.locator('#workspace-main .heading-actions').getByRole('button', { name: 'Auto-tag images', exact: true }).click();
    await expect(manual(page)).toHaveValue('shared');
    await manual(page).fill('other');
    await dialog(page).getByRole('button', { name: 'Save manual tags', exact: true }).click();
    await expect(toast(page)).toHaveText('1 tags saved for dunes_00002.png.');
    const reloaded = listingOf(page, query => query.get('tags') === 'shared' && !query.has('cursor'));
    await nav(page, 'Images').click();
    await reloaded;
    await expect(cards(page)).toHaveCount(1);
    await expect(card(page, UNLINKED)).toBeVisible();
    await expect(page.getByRole('button', { name: 'Filters · 1', exact: true })).toBeVisible();
});

test('a failed listing re-read after a save says so and keeps the cards (M2)', async ({ page, request }) => {
    await saveImageTags(request, SECOND, ['shared']);
    await saveImageTags(request, UNLINKED, ['shared']);
    await page.goto(images);
    await infoButton(page, SECOND).click();
    const filtered = listingOf(page, query => query.get('tags') === 'shared');
    await imageTags(page).getByRole('button', { name: 'shared', exact: true }).click();
    await filtered;
    await expect(cards(page)).toHaveCount(2);
    await openTag(page, SECOND, { goto: false });
    await manual(page).fill('');
    const failing = url => url.pathname === '/prompt_manager/images/output' && url.searchParams.get('tags') === 'shared';
    await page.route(failing, route => route.fulfill({ status: 500, contentType: 'application/json', body: '{"success":false,"error":"down"}' }));
    const reread = page.waitForResponse(item => failing(new URL(item.url())));
    await dialog(page).getByRole('button', { name: 'Save manual tags', exact: true }).click();
    expect((await reread).status()).toBe(500);
    await expect(toast(page)).toHaveText('Images could not be refreshed after the tag save. Refresh image collection to retry.');
    await expect(cards(page)).toHaveCount(2);
    await expect(page.locator('#gallery-continuation [aria-disabled="true"]')).toHaveCount(0);
    await page.unroute(failing);
});

// #431: the saved Skipped tags hide matching suggestions before the limit (WD14 fills it from lower scores), and the
// chip hint says how many were not suggested.
test('suggestions omit skipped tags and say how many were hidden', async ({ page, request }) => {
    await expect(await request.post('/__test__/tagging/defaults', { data: { general: 0.35, character: 0.85, limit: 4,
        skipped: ['desert', 'rock'] } })).toBeOK();
    await openTag(page);
    const answer = await (await generated(page)).json();
    expect(answer.skipped).toBe(2);
    await expect(chips(page).locator(':scope > span:first-child')).toHaveText(['sandstone', 'arch', 'sky', 'dune']);
    await expect(dialog(page).locator('#tag-suggestions > p.small')).toHaveText(`${CHIP_HINT} 2 skipped tags were not suggested.`);
});

// D204 (#431): the Saved instructions select above Tagging instructions: the engine default, a pick, a one-off edit
// ("Edited for this run" and the muted note), an engine switch resets, WD14 hides both; Generate sends the text shown.
test('the saved instructions picker fills, edits one-off and resets per engine (D204)', async ({ page, request }) => {
    const settings = await (await request.get('/prompt_manager/settings')).json();
    const builtin = settings.settings.autotag_builtin_instructions;
    await openTag(page);
    const options = dialog(page).locator('#image-tag-model-options');
    const pick = options.getByRole('combobox', { name: 'Saved instructions', exact: true });
    const text = options.getByRole('textbox', { name: 'Tagging instructions', exact: true });
    const note = options.locator('.instructions-edited');
    await expect(pick).toHaveCount(0);
    await model(page).selectOption('gguf');
    await expect(pick.locator('option')).toHaveText(['Built-in', 'Describe the image · default']);
    await expect(pick).toHaveAttribute('id', 'image-tag-instructions-pick');
    await expect(text).toHaveValue('Describe the image');
    await expect(note).toBeHidden();
    await pick.selectOption('builtin');
    await expect(text).toHaveValue(builtin);
    await text.fill('Only the sky.');
    await expect(pick.locator('option:checked')).toHaveText('Edited for this run');
    await expect(pick.locator('option[value="edited"]')).toBeDisabled();
    await expect(note).toHaveText('Edits apply to this run only. Manage saved instructions in System › Auto-tagging.');
    await expect(text).toBeFocused();
    // Generate sends the edited text; the saved entries are unchanged.
    const sent = page.waitForRequest(item => item.url().endsWith('/image-annotations/suggest'));
    await generate(page).click();
    expect((await sent).postDataJSON().options.instructions).toBe('Only the sky.');
    // Choosing an entry replaces the edit; switching engines starts from the new engine's default.
    await pick.selectOption('builtin');
    await expect(text).toHaveValue(builtin);
    await expect(note).toBeHidden();
    await text.fill('Edited again.');
    await model(page).selectOption('hf');
    await expect(text).toHaveValue('Describe the image');
    await expect(pick).toHaveValue('d35c71be0a1e');
    await model(page).selectOption('wd14-vit');
    await expect(pick).toHaveCount(0);
    await expect(text).toHaveCount(0);
    expect((await (await request.get('/prompt_manager/settings')).json()).settings.autotag_instructions)
        .toEqual(settings.settings.autotag_instructions);
});
