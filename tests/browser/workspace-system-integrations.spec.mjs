import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';
import { seedConcept } from './concept-inventory.mjs';

// System › Integrations (#249 System S10 Task 27) on the concept-shaped fixture with a fake LoraManager install
// (workspace_integrations_fixture.py): the D106 ComfyUI rows and renderer status, the LoRA rows and the D114 key, saved
// through the one Save row, and the D107 import review run as an Activity job. No test reaches the network: CivitAI
// downloads are answered by the fixture's stub opener.
const workspace = '/prompt_manager/workspace';
const PREVIEW_URL = '/prompt_manager/lora/import/preview';
const APPLY_URL = '/prompt_manager/lora/import/apply';
const ENABLE_URL = '/prompt_manager/lora/enable';
const KEPT = 'The import failed; the previous LoRA imports were kept. Images already downloaded stay in the image cache.';

test.beforeEach(async ({ page, request, browserName }) => {
    await startCoverage(page, browserName);
    await seedConcept(request);
    await expect(await request.post('/__test__/system/reset')).toBeOK();
    await expect(await request.post('/__test__/integrations/reset')).toBeOK();
    await expect(await request.post('/__test__/jobs/restart')).toBeOK();
    page.systemErrors = [];
    page.on('pageerror', error => page.systemErrors.push(error.message));
});

test.afterEach(async ({ page, request, browserName }) => {
    await saveCoverage(page, browserName);
    await request.post('/__test__/integrations/gate', { data: { hold: false } });
    await request.post('/__test__/release');
    await request.post('/__test__/jobs/restart');
    await request.post('/__test__/integrations/reset');
    await request.post('/__test__/system/reset');
    expect(page.systemErrors).toEqual([]);
});

const toast = page => page.locator('#workspace-toast');
const layoutOf = page => page.locator('#workspace-main .settings-layout');
const dialog = page => page.locator('dialog#modal[open]');
const status = page => page.locator('#settings-status');
const save = page => page.getByRole('button', { name: 'Save preferences', exact: true });
const settingOf = (page, label) => layoutOf(page).locator('.setting-row').filter({ has: page.locator(`label:text-is("${label}")`) });

async function serverJson(request, url) {
    const response = await request.get(url);
    await expect(response).toBeOK();
    return response.json();
}

async function lora(request, data = {}) {
    await expect(await request.post('/__test__/integrations/lora-manager', { data })).toBeOK();
}

async function openIntegrations(page) {
    // A fresh page each time: the drafts live for the page session (Q10), and a hash-only goto would not reload.
    if (page.url().endsWith('#/system/integrations')) await page.reload();
    else await page.goto(`${workspace}#/system/integrations`);
    await expect(layoutOf(page).locator(':scope > section.surface')).toHaveCount(2);
    // The LoRA rows are painted once the server's LoRA state was read.
    await expect(settingOf(page, 'LoraManager integration').locator('input')).toBeEnabled();
    // The renderer line settles first: its new text moves the rows below, so no click lands mid-shift.
    await expect(settingOf(page, 'Host compatibility').getByRole('status')).not.toHaveText('Looking for open ComfyUI windows…');
    return layoutOf(page);
}

/** Enable the integration (and optionally its trigger words) through the Save row. */
async function enable(page, { triggers = false } = {}) {
    await settingOf(page, 'LoraManager integration').locator('input').check();
    if (triggers) await settingOf(page, 'Automatically include trigger words').locator('input').check();
    await save(page).click();
    await expect(status(page)).toHaveText('Saved to the server configuration.');
}

/** The open review's `[dt, dd]` rows. */
const gridOf = page => dialog(page).locator('dl.meta-grid > div').evaluateAll(items => items.map(item =>
    [item.querySelector('dt').textContent, item.querySelector('dd').textContent]));

async function ended(request, id) {
    await expect.poll(async () => (await serverJson(request, `/prompt_manager/jobs/${id}`)).job.state).not.toBe('running');
    return (await serverJson(request, `/prompt_manager/jobs/${id}`)).job;
}

/** A second page of this origin that serves a fake ComfyUI window on the send bridge. */
async function fakeComfyWindow(context, renderer) {
    const host = await context.newPage();
    await host.goto(`${workspace}#/overview`);
    await host.evaluate(async drawn => {
        const { createBridgeHost } = await import('/prompt_manager/js/shared/toolkit/prompt-bridge.js');
        window.fakeHost = createBridgeHost({ namespace: '/', origin: location.origin, label: 'PromptManager — ComfyUI',
            adapter: { listTargets: () => (drawn ? [{ id: '7', label: 'Prompt Manager', type: 'PromptManager', roles: ['positive'],
                renderer: drawn }] : []), preview() { throw new Error('unused'); }, apply() { throw new Error('unused'); } } });
    }, renderer);
    return host;
}

test('send destination offers Ask each time; unsupported options disabled with the reason (D106)', async ({ page }) => {
    const layout = await openIntegrations(page);
    expect(await layout.locator(':scope > section.surface > h2').allTextContents()).toEqual(['ComfyUI', 'LoRA & resources']);
    const send = settingOf(page, 'Default send destination');
    expect(await send.locator('option').evaluateAll(options => options.map(option => [option.textContent, option.disabled])))
        .toEqual([['Ask each time', false], ['Selected Prompt Manager node', true], ['Create a Prompt Manager (+/-) node', true]]);
    await expect(send.locator('select')).toHaveValue('Ask each time');
    await expect(send.locator('p.small')).toHaveText(
        'Sending always asks for the ComfyUI window and node, so “Ask each time” is the only option.');
    const renderer = settingOf(page, 'Host compatibility');
    expect(await renderer.locator('option').evaluateAll(options => options.map(option => [option.textContent, option.disabled])))
        .toEqual([['Follow host renderer', false], ['Classic', true], ['Nodes 2.0', true]]);
    await expect(renderer.locator('p.small').first()).toHaveText('Prompt Manager nodes always follow ComfyUI’s own renderer setting.');
    const back = settingOf(page, 'Return to canvas after send');
    await expect(back.locator('input')).toBeDisabled();
    await expect(back.locator('input')).not.toBeChecked();
    await expect(back.locator('p.small')).toHaveText('Not available: sending applies the text in ComfyUI and keeps this page open.');
    // Nothing on this surface is ever drafted: the Save row stays clean.
    await expect(status(page)).toHaveText('No unsaved changes.');
    await expect(save(page)).toHaveAttribute('aria-disabled', 'true');
});

test('host compatibility shows the detected renderer as status (D106)', async ({ page, context }) => {
    await openIntegrations(page);
    const line = settingOf(page, 'Host compatibility').getByRole('status');
    await expect(line).toHaveText('No ComfyUI window is open in this browser, so its renderer is not known.');
    const host = await fakeComfyWindow(context, 'nodes2');
    try {
        await page.reload();
        await expect(line).toHaveText('Detected: Nodes 2.0 (1 ComfyUI window).');
        await host.evaluate(() => window.fakeHost.dispose());
        await host.evaluate(async () => {
            const { createBridgeHost } = await import('/prompt_manager/js/shared/toolkit/prompt-bridge.js');
            window.fakeHost = createBridgeHost({ namespace: '/', origin: location.origin, label: 'PromptManager — ComfyUI',
                adapter: { listTargets: () => [], preview() {}, apply() {} } });
        });
        await page.reload();
        await expect(line).toHaveText('ComfyUI is open; add a Prompt Manager node to see which renderer it uses.');
    } finally {
        await host.close();
    }
});

test('LoRA rows show detected / enabled / cache-loaded separately', async ({ page, request }) => {
    await openIntegrations(page);
    const integration = settingOf(page, 'LoraManager integration');
    const triggers = settingOf(page, 'Automatically include trigger words');
    await expect(integration.locator('p').first()).toHaveText('Not detected in ComfyUI’s custom_nodes folders.');
    await expect(integration.locator('input')).not.toBeChecked();
    await expect(triggers.locator('p.small')).toHaveText('Trigger words are not loaded.');
    // Enabling what is not installed is refused by the server: the toggle stays highlighted, nothing was saved.
    await integration.locator('input').check();
    await save(page).click();
    await expect(status(page)).toHaveText('Some changes were not saved. Review the highlighted settings.');
    await expect(integration.locator('input')).toHaveAttribute('aria-invalid', 'true');
    expect((await serverJson(request, '/__test__/integrations/state')).enabled).toBe(false);

    await lora(request);
    await openIntegrations(page);
    await expect(integration.locator('p').first()).toHaveText('Detected: custom_nodes/ComfyUI-Lora-Manager.');
    await expect(integration.locator('input')).not.toBeChecked();
    await enable(page, { triggers: true });
    await expect(integration.locator('input')).toBeChecked();
    await expect(triggers.locator('p.small')).toHaveText('Trigger words are loaded.');
    expect(await serverJson(request, '/__test__/integrations/state')).toMatchObject({ enabled: true, trigger_words: true });
    // A reload reads the saved state back.
    await openIntegrations(page);
    await expect(integration.locator('input')).toBeChecked();
    await expect(triggers.locator('input')).toBeChecked();
    await expect(status(page)).toHaveText('No unsaved changes.');
});

test('LoraManager path row shows the detected path read-only, as legacy (D106)', async ({ page, request }) => {
    await openIntegrations(page);
    const row = settingOf(page, 'LoraManager path');
    const input = row.locator('input');
    await expect(input).toHaveAttribute('readonly', '');
    await expect(input).toHaveValue('');
    await expect(input).toHaveAttribute('placeholder', 'Not detected');
    await lora(request);
    await openIntegrations(page);
    await expect(input).toHaveValue('custom_nodes/ComfyUI-Lora-Manager');
    await input.focus();
    await page.keyboard.type('elsewhere');
    await expect(input).toHaveValue('custom_nodes/ComfyUI-Lora-Manager');
    await expect(status(page)).toHaveText('No unsaved changes.');
});

test('key never prefilled; blank keeps; Clear saved key clears; saved through Save preferences (D114)', async ({ page, request }) => {
    const enables = [];
    page.on('request', item => { if (item.url().endsWith(ENABLE_URL)) enables.push(item.postDataJSON()); });
    await lora(request);
    await openIntegrations(page);
    const key = settingOf(page, 'CivitAI API key');
    const field = key.locator('input[type="password"]');
    const clear = key.getByRole('button', { name: 'Clear saved key', exact: true });
    await expect(field).toHaveValue('');
    await expect(key.locator('p.small')).toHaveText('No key saved.');
    await expect(clear).toHaveCount(0);
    // Typing a key drafts it; Save sends it once through /lora/enable with the settings revision.
    await field.fill('sk-test-key');
    await expect(key.locator('p.small')).toHaveText('The new key replaces the saved one when you save.');
    await expect(status(page)).toHaveText('Unsaved changes.');
    await save(page).click();
    await expect(status(page)).toHaveText('Saved to the server configuration.');
    expect(enables).toHaveLength(1);
    expect(enables[0]).toMatchObject({ civitai_api_key: 'sk-test-key', enabled: false });
    expect(enables[0].expected_revision).toMatch(/^(sha256:|absent)/);
    await expect(field).toHaveValue('');
    await expect(key.locator('p.small')).toHaveText('A key is saved.');
    expect((await serverJson(request, '/__test__/integrations/state')).key_saved).toBe(true);
    // Never prefilled, even after a reload; the page never receives the key.
    const statusBodies = [];
    page.on('response', async response => {
        if (response.url().endsWith('/prompt_manager/lora/status')) statusBodies.push(await response.text());
    });
    await openIntegrations(page);
    await expect(field).toHaveValue('');
    await expect(key.locator('p.small')).toHaveText('A key is saved.');
    expect(statusBodies.join('')).not.toContain('sk-test-key');
    // A blank field keeps the saved key: nothing to save.
    await field.fill('   ');
    await expect(status(page)).toHaveText('No unsaved changes.');
    await field.fill('');
    // Clear saved key drafts the removal; Save removes it.
    await clear.click();
    await expect(key.locator('p.small')).toHaveText('The saved key is removed when you save.');
    await expect(clear).toHaveCount(0);
    await save(page).click();
    await expect(status(page)).toHaveText('Saved to the server configuration.');
    expect(enables.at(-1)).toMatchObject({ clear_civitai_api_key: true });
    expect(enables.at(-1)).not.toHaveProperty('civitai_api_key');
    await expect(key.locator('p.small')).toHaveText('No key saved.');
    expect((await serverJson(request, '/__test__/integrations/state')).key_saved).toBe(false);
});

test('a LoRA save over settings changed elsewhere is a conflict, never a blind write', async ({ page, request }) => {
    await lora(request);
    await openIntegrations(page);
    await settingOf(page, 'Automatically include trigger words').locator('input').check();
    // Another page saves the settings meanwhile: the revision this page read is stale.
    await expect(await request.post('/prompt_manager/settings', { data: { result_timeout: 42 } })).toBeOK();
    await save(page).click();
    await expect(status(page)).toHaveText('Settings changed elsewhere. Reload this section to see them.');
    expect((await serverJson(request, '/__test__/integrations/state')).trigger_words).toBe(false);
    // The draft stays; the next Save carries the reread revision and succeeds.
    await save(page).click();
    await expect(status(page)).toHaveText('Saved to the server configuration.');
    expect((await serverJson(request, '/__test__/integrations/state')).trigger_words).toBe(true);
});

test('Review import shows replacement, blocked items and network effects with the backup box; Start opens the job (D107)',
    async ({ page, request }) => {
        await lora(request, { loras: 3, civitai: true, previous: 2 });
        const previews = [];
        const applies = [];
        page.on('request', item => {
            if (item.url().endsWith(PREVIEW_URL)) previews.push(item.postDataJSON());
            if (item.url().endsWith(APPLY_URL)) applies.push(item.postDataJSON());
        });
        await openIntegrations(page);
        await enable(page);
        const before = await serverJson(request, '/__test__/integrations/state');
        const review = layoutOf(page).locator('.operation').filter({ hasText: 'LoRA reference data' })
            .getByRole('button', { name: 'Review import', exact: true });
        await review.click();
        await expect(dialog(page).locator('#modal-title')).toHaveText('Review LoRA import');
        await expect(dialog(page).locator('#modal-body > h3')).toHaveText('LoRA reference data');
        expect(previews).toEqual([{}]);
        expect(await gridOf(page)).toEqual([
            ['Scope', 'Replace 2 imported prompts with 3 LoRAs'],
            ['Found', '3 metadata files · 3 readable'],
            ['Protected', 'Nothing is protected'],
            ['Network', 'Downloads up to 3 example images from CivitAI'],
            ['Backup', 'Create and verify before changes (browser.db.backup_<date and time>)'],
            ['Media on disk', 'No files are removed; image links are replaced'],
        ]);
        await expect(dialog(page).locator('details > summary')).toHaveText('LoRAs found (3)');
        await expect(dialog(page).locator('.notice h3')).toHaveText('Back up the database first (recommended)');
        await expect(dialog(page).locator('p.small').last()).toContainText('stay in the image cache');
        // The review wrote and downloaded nothing; Cancel neither.
        await dialog(page).getByRole('button', { name: 'Cancel', exact: true }).click();
        await expect(dialog(page)).toHaveCount(0);
        await expect(review).toBeFocused();
        expect(await serverJson(request, '/__test__/integrations/state')).toEqual(before);

        await review.click();
        await dialog(page).getByRole('button', { name: 'Start import', exact: true }).click();
        await expect(dialog(page)).toHaveCount(0);
        await expect(page).toHaveURL(/#\/activity\/[0-9a-f]{32}$/);
        await expect(toast(page)).toHaveText(/LoRA reference import started\.|LoRA import finished · 3 imported · 0 errors/);
        expect(applies).toHaveLength(1);
        expect(Object.keys(applies[0])).toEqual(['token']);
        const job = await ended(request, page.url().split('/').pop());
        expect([job.kind, job.title, job.state]).toEqual(['lora_import', 'LoRA reference import', 'finished']);
        expect(job.counters).toMatchObject({ imported: 3, downloaded: 3, download_errors: 0, errors: 0 });
        const main = page.locator('#workspace-main');
        await expect(main.locator('h1')).toHaveText('LoRA reference import');
        await expect(main.locator('.stat-row .stat dt')).toHaveText(['Imported', 'Linked to existing', 'Skipped', 'Images downloaded',
            'Download errors', 'Errors']);
        await expect(main.getByRole('button', { name: 'Cancel import', exact: true })).toHaveCount(0);
        const after = await serverJson(request, '/__test__/integrations/state');
        expect(after.imports).toEqual(['style_01 example prompt', 'style_02 example prompt', 'style_03 example prompt']);
        expect(after.downloads).toBe(3);
    });

test('saved pairs block the import: the review explains it and offers only Close', async ({ page, request }) => {
    await lora(request, { previous: 1 });
    await expect(await request.post('/__test__/integrations/pair')).toBeOK();
    await openIntegrations(page);
    await enable(page);
    await layoutOf(page).getByRole('button', { name: 'Review import', exact: true }).click();
    expect((await gridOf(page))[2]).toEqual(['Protected', '1 imported prompt used by saved pairs']);
    await expect(dialog(page).locator('p.small')).toHaveText(
        'Delete or change the saved pairs that use these prompts, then review the import again. Nothing was changed.');
    expect(await dialog(page).locator('.dialog-actions button').allTextContents()).toEqual(['Close']);
    await dialog(page).getByRole('button', { name: 'Close', exact: true }).click();
    await expect(dialog(page)).toHaveCount(0);
});

test('a failed import keeps old imports and its job says so', async ({ page, request }) => {
    await lora(request, { loras: 2, previous: 2 });
    await openIntegrations(page);
    await enable(page);
    const before = await serverJson(request, '/__test__/integrations/state');
    await expect(await request.post('/__test__/integrations/fail-import')).toBeOK();
    await layoutOf(page).getByRole('button', { name: 'Review import', exact: true }).click();
    await dialog(page).getByRole('button', { name: 'Start import', exact: true }).click();
    await expect(page).toHaveURL(/#\/activity\/[0-9a-f]{32}$/);
    await expect(toast(page)).toHaveText(`LoRA import failed · ${KEPT}`);
    const job = await ended(request, page.url().split('/').pop());
    expect([job.state, job.scope.failure, job.counters.errors]).toEqual(['failed', 'import-failed', 1]);
    await expect(page.locator('#workspace-main')).toContainText(KEPT);
    expect((await serverJson(request, '/__test__/integrations/state')).imports).toEqual(before.imports);
    expect(before.imports).toEqual(['previous lora import 1', 'previous lora import 2']);
});

test('the import can be cancelled while it downloads; then nothing is written', async ({ page, request }) => {
    await lora(request, { loras: 2, civitai: true, previous: 1 });
    await openIntegrations(page);
    await enable(page);
    const before = await serverJson(request, '/__test__/integrations/state');
    await expect(await request.post('/__test__/integrations/gate', { data: { hold: true } })).toBeOK();
    await layoutOf(page).getByRole('button', { name: 'Review import', exact: true }).click();
    await dialog(page).getByRole('button', { name: 'Start import', exact: true }).click();
    await expect(page).toHaveURL(/#\/activity\/[0-9a-f]{32}$/);
    const id = page.url().split('/').pop();
    await expect.poll(async () => (await serverJson(request, '/__test__/integrations/state')).downloads).toBeGreaterThan(0);
    const cancel = page.locator('#workspace-main').getByRole('button', { name: 'Cancel import', exact: true });
    await expect(cancel).toBeVisible();
    await cancel.click();
    await expect(await request.post('/__test__/integrations/gate', { data: { hold: false } })).toBeOK();
    const job = await ended(request, id);
    expect(job.state).toBe('cancelled');
    expect((await serverJson(request, '/__test__/integrations/state')).imports).toEqual(before.imports);
    await expect(cancel).toHaveCount(0);
});
