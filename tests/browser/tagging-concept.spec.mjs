// The Tag image dialog and the card's Info and Tag group against the approved concept (#249 PR C, plan Task C4, Mock
// inventory M1 and M2). Production runs on the concept-shaped fixture with the concept image's own tags saved as its
// image tags and the injected fake model answering the concept's own sample (alpine: mountain 98%, observatory 95%,
// snow 88%, blue_hour 81%), so every remaining difference is a recorded deviation applied by mapConceptTagging
// (D29, D83, D84, D91) or a test failure. No model is ever downloaded: every test checks no download request was sent.
import { test, expect } from './fixtures.mjs';
import { seedConcept, openConcept, openProduction, saveImageTags, mutate, SIZES, THEMES } from './concept-inventory.mjs';
import { taggingInventory, cardActionsInventory, mapConceptTagging, diffTaggingInventory, withoutChipGeometry } from './tagging-canonical-comparison.mjs';

const MODULE = '/prompt_manager/js/workspace/image-tagging.mjs';
// The concept's first image (app.js images[0]) and its sample suggestions (generateTags, scene "alpine").
const CONCEPT_FILE = 'alpine_00001.png';
const CONCEPT_TAGS = ['cinematic', 'blue_hour', 'architecture'];
const ALPINE = [['mountain', 0.98], ['observatory', 0.95], ['snow', 0.88], ['blue_hour', 0.81]];
// The fake model's server default instructions (tests/browser/server.py TestModel.default_prompt), D84.
const INSTRUCTIONS = 'Describe the image';
const CONCEPT_MODEL = { gguf: 'ollama' };

const tagDialog = page => page.getByRole('dialog', { name: 'Tag image', exact: true });
const firstCard = (page, side) => page.locator(side === 'concept' ? '#image-grid > article.media-card' : '#workspace-main article[data-image-key]').first();

test.beforeEach(async ({ page, request }) => {
    const manifest = await seedConcept(request);
    expect(manifest).toBeTruthy();
    await expect(await request.post('/__test__/tagging/suggestions', { data: { scored: ALPINE } })).toBeOK();
    page.downloads = [];
    page.on('request', item => { if (/\/autotag\/download\//.test(item.url())) page.downloads.push(item.url()); });
    page.errors = [];
    page.on('pageerror', error => page.errors.push(error.message));
});
test.afterEach(async ({ page, request }) => {
    await leave(page);
    expect(page.downloads).toEqual([]);
    expect(page.errors).toEqual([]);
    await request.post('/__test__/tagging/suggestions', { data: { scored: null } });
});

async function conceptPath(request) {
    const listing = await (await request.get('/prompt_manager/images/output?mode=workspace&limit=40')).json();
    const image = listing.images.find(item => item.filename === CONCEPT_FILE);
    expect(image).toBeTruthy();
    return image.path;
}

/**
 * Close an open Tag dialog before opening the next page, so each comparison starts from a settled page. Leaving with
 * the dialog open is covered on its own (workspace-tagging-lifecycle.spec.mjs: the keepalive release on pagehide).
 */
async function leave(page) {
    if (await tagDialog(page).count()) {
        await page.keyboard.press('Escape');
        await expect(tagDialog(page)).toHaveCount(0);
    }
}
/** Open `side`'s Tag image dialog from its first card, settled (production: the capture preview and saved tags). */
async function openTagDialog(page, side, theme) {
    await leave(page);
    if (side === 'concept') await openConcept(page, 'images', theme);
    else await openProduction(page, 'images', theme);
    await firstCard(page, side).getByRole('button', { name: `Tag or auto-tag ${CONCEPT_FILE}`, exact: true }).click();
    await expect(tagDialog(page)).toBeVisible();
    await settled(page, side);
}
async function settled(page, side) {
    if (side === 'production') {
        await expect(tagDialog(page).locator('img.tag-image-preview')).toHaveAttribute('src', /\/image-annotations\/capture\//);
        await expect(tagDialog(page).locator('#manual-image-tags')).toHaveValue(CONCEPT_TAGS.join(', '));
    }
    await page.evaluate(async () => {
        await document.fonts.ready;
        await Promise.all([...document.querySelectorAll('#modal img')].map(image => image.decode().catch(() => {})));
    });
}
async function generateWith(page, side, model) {
    const select = tagDialog(page).getByRole('combobox', { name: 'Tagging model', exact: true });
    if (model) await select.selectOption(side === 'concept' ? CONCEPT_MODEL[model] ?? model : model);
    await tagDialog(page).getByRole('button', { name: 'Generate suggestions', exact: true }).click();
    await expect(tagDialog(page).locator('#suggestion-count')).not.toHaveText('Not generated');
}
// The pointer rests outside the dialog, so no control is measured in its hover state.
const inventory = async page => {
    await page.mouse.move(0, 0);
    return page.locator('dialog#modal').evaluate(taggingInventory);
};

/** The concept and production inventories of one dialog state, the concept mapped (D29, D83, D84, D91). */
async function compareState(page, { theme, act = async () => {} }) {
    await openTagDialog(page, 'concept', theme);
    const conceptOpened = await inventory(page);
    await act(page, 'concept');
    const concept = await inventory(page);
    await openTagDialog(page, 'production', theme);
    await act(page, 'production');
    const actual = await inventory(page);
    const numberField = conceptOpened.modelPanel.fields.find(field => field.control.id === 'image-general-threshold');
    const reference = mapConceptTagging(concept, { instructions: INSTRUCTIONS, chip: concept.heading.size, numberField });
    return { reference: withoutChipGeometry(reference), actual: withoutChipGeometry(actual) };
}

for (const [width, height] of SIZES) for (const theme of THEMES) {
    test(`concept and production Tag dialog match at ${width}x${height} ${theme}`, async ({ page, request }) => {
        test.setTimeout(90_000);
        await saveImageTags(request, await conceptPath(request), CONCEPT_TAGS);
        await page.setViewportSize({ width, height });
        // Opened, generated with WD14 (the concept's default), then generated with a language model.
        for (const [state, act] of [
            ['opened', async () => {}],
            ['generated WD14', (page, side) => generateWith(page, side)],
            ['generated GGUF', (page, side) => generateWith(page, side, 'gguf')],
        ]) {
            const { reference, actual } = await compareState(page, { theme, act });
            expect(diffTaggingInventory(reference, actual), `${state} at ${width}x${height} ${theme}`).toEqual([]);
        }
        // Pinned concept geometry (M2), so a mapping mistake on both sides cannot pass unseen.
        const { actual } = await compareState(page, { theme, act: (page, side) => generateWith(page, side) });
        const small = width <= 700;
        expect([actual.heading.preview.width, actual.heading.preview.height]).toEqual(small ? [86, 86] : [112, 100]);
        expect(actual.heading.columnGap).toBe('16px');
        expect([actual.modelPanel.paddingTop, actual.modelPanel.borderTopLeftRadius]).toEqual(['16px', '6px']);
        expect(actual.actions.position).toBe('sticky');
        expect(actual.suggestions.rows.map(row => [row.name, row.confidence, row.control.label])).toEqual(
            ALPINE.map(([name, confidence]) => [name, `${Math.round(confidence * 100)}%`, `Remove ${name}`]));
        const accent = await page.evaluate(() => {
            const probe = document.createElement('i');
            probe.style.cssText = 'border-top:1px solid var(--ca-accent);background:var(--soft-blue)';
            document.body.append(probe);
            const style = getComputedStyle(probe), tokens = [style.borderTopColor, style.backgroundColor];
            probe.remove();
            return tokens;
        });
        for (const row of actual.suggestions.rows) expect([row.borderTopColor, row.backgroundColor]).toEqual(accent);
        expect(actual.dialog.noHorizontalOverflow).toBe(true);
        expect(actual.noPageScroll).toBe(true);
    });
}

test('an empty result matches the concept below its threshold', async ({ page, request }) => {
    await saveImageTags(request, await conceptPath(request), CONCEPT_TAGS);
    await expect(await request.post('/__test__/tagging/suggestions', { data: { scored: [] } })).toBeOK();
    const { reference, actual } = await compareState(page, { theme: 'dark', act: async (page, side) => {
        // The concept filters its sample by the General confidence; above every sample nothing is shown.
        if (side === 'concept') await tagDialog(page).getByRole('spinbutton', { name: 'General confidence', exact: true }).fill('1');
        await generateWith(page, side);
        if (side === 'concept') await tagDialog(page).getByRole('spinbutton', { name: 'General confidence', exact: true }).fill('0.35');
    } });
    expect(actual.suggestions.copy).toBe('No suggestions met this threshold. Adjust it or enter tags manually.');
    expect(diffTaggingInventory(reference, actual)).toEqual([]);
});

test('review-scoped dialog shows the scope note', async ({ page, request }) => {
    const path = await conceptPath(request);
    await saveImageTags(request, path, CONCEPT_TAGS);
    await page.setViewportSize({ width: 1600, height: 1100 });
    await openConcept(page, 'review', 'dark');
    await page.locator('#main section.surface').getByRole('button', { name: 'Tag image', exact: true }).click();
    await expect(tagDialog(page)).toBeVisible();
    await settled(page, 'concept');
    const opened = await inventory(page);
    // Needs review ships with PR F; until then the review source opens the same dialog from the module.
    await leave(page);
    await openProduction(page, 'images', 'dark');
    await page.evaluate(async ({ module, path }) => {
        const { openImageTagging } = await import(module);
        const { createScope } = await import('/prompt_manager/js/shared/toolkit/core.js');
        const api = window.ApiClient.createApiClient();
        const listing = await api.get('/prompt_manager/images/output?mode=workspace&limit=40');
        openImageTagging({ api, scope: createScope(), image: listing.images.find(item => item.path === path), source: 'review' });
    }, { module: MODULE, path });
    await expect(tagDialog(page)).toBeVisible();
    await settled(page, 'production');
    const actual = await inventory(page);
    expect(actual.scopeNote.text).toBe('Tags belong to this image. Resolve its prompt association separately in Needs review.');
    expect(actual.order).toEqual(['tag-image-heading', 'tag-scope-note small', 'field', 'tag-model-panel', 'tag-suggestion-panel', 'dialog-actions']);
    const numberField = opened.modelPanel.fields.find(field => field.control.id === 'image-general-threshold');
    const reference = mapConceptTagging(opened, { instructions: INSTRUCTIONS, chip: opened.heading.size, numberField });
    expect(diffTaggingInventory(reference, actual)).toEqual([]);
});

for (const [width, height] of SIZES) test(`card action group matches M1 at ${width}x${height}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await openConcept(page, 'images', 'dark');
    const reference = await firstCard(page, 'concept').evaluate(cardActionsInventory);
    await openProduction(page, 'images', 'dark');
    const actual = await firstCard(page, 'production').evaluate(cardActionsInventory);
    expect(actual.buttons.map(button => button.label)).toEqual([`Inspect ${CONCEPT_FILE}`, `Tag or auto-tag ${CONCEPT_FILE}`]);
    expect(diffTaggingInventory(reference, actual)).toEqual([]);
});

test('the comparison notices a changed panel border or primary label', async ({ page, request }) => {
    await saveImageTags(request, await conceptPath(request), CONCEPT_TAGS);
    const { reference, actual } = await compareState(page, { theme: 'dark' });
    expect(diffTaggingInventory(reference, actual)).toEqual([]);
    const undo = await mutate(page, { css: '.tag-model-panel { border: 0 !important; }' });
    const borderless = withoutChipGeometry(await inventory(page));
    expect(diffTaggingInventory(reference, borderless).map(item => item.path)).toEqual(
        expect.arrayContaining(['modelPanel.borderTopWidth', 'modelPanel.borderTopStyle']));
    await undo();
    await tagDialog(page).locator('.dialog-actions .ca-primary').evaluate(node => { node.textContent = 'Save'; });
    const relabelled = withoutChipGeometry(await inventory(page));
    expect(diffTaggingInventory(reference, relabelled).map(item => item.path)).toEqual(['actions.buttons.1.text']);
});

// Paired captures for the PR (no screenshot baselines): every production state beside the concept's nearest one. The
// states the concept lacks (unavailable model, saving, conflict, file changed) pair with the concept's opened dialog.
test('paired Tag dialog captures', async ({ page, request }, testInfo) => {
    test.setTimeout(120_000);
    const path = await conceptPath(request);
    await saveImageTags(request, path, CONCEPT_TAGS);
    await page.setViewportSize({ width: 1600, height: 1100 });
    const shot = async name => {
        await page.evaluate(async () => { await document.fonts.ready; await Promise.all([...document.images].map(image => image.decode().catch(() => {}))); });
        const file = testInfo.outputPath(`${name}.png`);
        await page.screenshot({ path: file });
        await testInfo.attach(name, { path: file, contentType: 'image/png' });
    };
    const both = async (name, act) => {
        for (const side of ['concept', 'production']) {
            await openTagDialog(page, side, 'dark');
            await act(page, side);
            await shot(`${side}-${name}`);
        }
    };
    await both('opened', async () => {});
    await both('generated-wd14', (page, side) => generateWith(page, side));
    await both('generated-gguf', (page, side) => generateWith(page, side, 'gguf'));
    await expect(await request.post('/__test__/tagging/suggestions', { data: { scored: [] } })).toBeOK();
    await both('empty', async (page, side) => {
        if (side === 'concept') await tagDialog(page).getByRole('spinbutton', { name: 'General confidence', exact: true }).fill('1');
        await generateWith(page, side);
    });
    await expect(await request.post('/__test__/tagging/suggestions', { data: { scored: ALPINE } })).toBeOK();
    const save = () => tagDialog(page).locator('.dialog-actions .ca-primary').click();
    const status = () => tagDialog(page).locator('#tag-generation-status');

    // Unavailable model.
    await expect(await request.post('/__test__/tagging/models', { data: { unavailable: ['wd14-swinv2'] } })).toBeOK();
    await openTagDialog(page, 'production', 'dark');
    await generateWith(page, 'production').catch(() => {});
    await expect(status()).toHaveText('WD14 SwinV2 is not downloaded. Download it from the legacy Auto-tag page.');
    await shot('production-unavailable');
    await expect(await request.post('/__test__/tagging/models', { data: { unavailable: [] } })).toBeOK();

    // Saving: the write is held until the capture is taken.
    let release;
    const held = new Promise(resolve => { release = resolve; });
    const writes = url => url.pathname === '/prompt_manager/image-annotations/tags';
    await page.route(writes, async route => { await held; await route.continue(); });
    await openTagDialog(page, 'production', 'dark');
    const sent = page.waitForRequest(item => writes(new URL(item.url())));
    await save();
    await sent;
    await expect(tagDialog(page).locator('.dialog-actions .ca-primary')).toHaveAttribute('aria-disabled', 'true');
    await shot('production-saving');
    release();
    await expect(tagDialog(page)).toHaveCount(0);
    await page.unroute(writes);

    // Conflict: another writer saved this file's set after the dialog observed it.
    await openTagDialog(page, 'production', 'dark');
    await saveImageTags(request, path, ['changed_elsewhere']);
    await save();
    await expect(status()).not.toHaveText('');
    await shot('production-conflict');
    await page.keyboard.press('Escape');

    // File changed: the server's own envelope for bytes replaced after the preview (B1 `file-changed`).
    await page.route(writes, route => route.fulfill({ status: 409, contentType: 'application/json',
        body: JSON.stringify({ success: false, code: 'file-changed', error: 'The file changed since it was previewed. Reopen the Tag dialog.' }) }));
    await saveImageTags(request, path, CONCEPT_TAGS);
    await openTagDialog(page, 'production', 'dark');
    await save();
    await expect(status()).not.toHaveText('');
    await shot('production-file-changed');
    await page.unroute(writes);
    await page.keyboard.press('Escape');

    // Unconfirmed save (a 504): D116's Retry leads the actions, before Cancel and the disabled primary.
    await page.route(writes, route => route.fulfill({ status: 504, body: 'Gateway Timeout' }));
    await openTagDialog(page, 'production', 'dark');
    await save();
    await expect(status()).toHaveText('The save could not be confirmed. Retry sends the same tags again.');
    expect((await inventory(page)).actions.buttons.map(button => [button.text, button.primary, button.disabled])).toEqual(
        [['Retry', false, false], ['Cancel', false, false], ['Save manual tags', true, true]]);
    await shot('production-unknown-outcome');
    await page.unroute(writes);
    await page.keyboard.press('Escape');

    // Review scope: the concept's Needs review dialog and production's review-sourced dialog.
    await leave(page);
    await openConcept(page, 'review', 'dark');
    await page.locator('#main section.surface').getByRole('button', { name: 'Tag image', exact: true }).click();
    await expect(tagDialog(page)).toBeVisible();
    await shot('concept-review');
    await leave(page);
    await openProduction(page, 'images', 'dark');
    await page.evaluate(async ({ module, path }) => {
        const { openImageTagging } = await import(module);
        const { createScope } = await import('/prompt_manager/js/shared/toolkit/core.js');
        const api = window.ApiClient.createApiClient();
        const listing = await api.get('/prompt_manager/images/output?mode=workspace&limit=40');
        openImageTagging({ api, scope: createScope(), image: listing.images.find(item => item.path === path), source: 'review' });
    }, { module: MODULE, path });
    await expect(tagDialog(page).locator('.tag-scope-note')).toBeVisible();
    await settled(page, 'production');
    await shot('production-review');
    await leave(page);
});
