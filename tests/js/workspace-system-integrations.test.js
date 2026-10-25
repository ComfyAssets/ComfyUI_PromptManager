// System › Integrations (#249 System S10 Task 27): the host values and saves behind the LoRA rows (D114), the D106
// renderer and detection lines, and the D107 import review built from the server's plan.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const integrations = import('../../web/js/workspace/system-integrations.mjs');
const kinds = import('../../web/js/workspace/activity-kinds.mjs');

const CONCEPT = fs.readFileSync(path.join(__dirname, '../../docs/concepts/workspace/app.js'), 'utf8');
const SOURCE = fs.readFileSync(path.join(__dirname, '../../web/js/workspace/system-integrations.mjs'), 'utf8');

test('the section keeps the concept rows, labels and options in order', () => {
    const start = CONCEPT.indexOf('case "integrations":');
    const body = CONCEPT.slice(start, CONCEPT.indexOf('break;', start));
    const rows = [...body.matchAll(/setting\("([^"]+)", "([^"]+)", "([^"]+)"/g)].map(([, id, label]) => [id, label]);
    assert.deepEqual(rows.map(([id]) => id), ['send-destination', 'renderer', 'return-canvas', 'lora-enable', 'lora-triggers',
        'civitai-key']);
    for (const [id, label] of rows) {
        assert.ok(SOURCE.includes(`id: '${id}', label: '${label}'`), `${id} keeps the concept label "${label}"`);
    }
    for (const option of ['Ask each time', 'Selected Prompt Manager node', 'Create a Prompt Manager (+/-) node',
        'Follow host renderer', 'Classic', 'Nodes 2.0']) assert.ok(body.includes(option) && SOURCE.includes(`'${option}'`), option);
    for (const surface of ['ComfyUI', 'LoRA & resources']) assert.ok(SOURCE.includes(`'h2', '${surface}'`), surface);
    assert.ok(body.includes('"LoRA reference data", "Review and import resource metadata."'));
    // Prototype copy is never shipped (Global constraints): the sample detection line and the mock warning.
    assert.ok(!SOURCE.includes('Sample detection'));
    assert.ok(!SOURCE.includes('Do not enter a real key'));
    assert.ok(!SOURCE.includes('Simulate import'));
});

test('LoRA status becomes host values: drafted flags and read-only facts kept apart', async () => {
    const { loraValues } = await integrations;
    assert.deepEqual(loraValues({ enabled: true, trigger_words_enabled: false, path: 'custom_nodes/ComfyUI-Lora-Manager',
        has_civitai_api_key: true, detected: true, detected_path: 'custom_nodes/ComfyUI-Lora-Manager', trigger_cache_loaded: true }), {
        lora_enabled: true, lora_trigger_words_enabled: false, clear_civitai_api_key: false,
        lora_path: 'custom_nodes/ComfyUI-Lora-Manager', lora_detected: true, lora_detected_path: 'custom_nodes/ComfyUI-Lora-Manager',
        lora_detected_usable: true, lora_has_key: true, lora_cache_loaded: true,
    });
    const empty = loraValues({});
    assert.equal(empty.lora_enabled, false);
    assert.equal(empty.lora_path, '');
    assert.equal(empty.lora_has_key, false);
    // The key itself is never part of what the page holds from the server.
    assert.ok(!Object.keys(loraValues({ civitai_api_key: 'secret' })).includes('civitai_api_key'));
});

test('host changes split between /lora/enable and /settings', async () => {
    const { splitHostChanges } = await integrations;
    assert.deepEqual(splitHostChanges({ lora_enabled: true, result_timeout: 5 }),
        { lora: { lora_enabled: true }, settings: { result_timeout: 5 } });
    assert.deepEqual(splitHostChanges({ result_timeout: 5 }), { lora: null, settings: { result_timeout: 5 } });
    assert.deepEqual(splitHostChanges({ civitai_api_key: 'k', clear_civitai_api_key: false }),
        { lora: { civitai_api_key: 'k', clear_civitai_api_key: false }, settings: null });
    assert.deepEqual(splitHostChanges(undefined), { lora: null, settings: null });
});

test('key never prefilled; blank keeps; Clear saved key clears; saved through Save preferences (D114)', async () => {
    const { loraPayload, keyText, KEY_COPY } = await integrations;
    const saved = { lora_enabled: true, lora_trigger_words_enabled: false, lora_path: 'custom_nodes/LM', lora_has_key: true };
    // The whole LoRA state goes with every save; the saved path is kept; no key unless one was typed.
    assert.deepEqual(loraPayload({ lora_trigger_words_enabled: true }, saved),
        { enabled: true, trigger_words_enabled: true, path: 'custom_nodes/LM' });
    assert.deepEqual(loraPayload({ civitai_api_key: '  ' }, saved), { enabled: true, trigger_words_enabled: false, path: 'custom_nodes/LM' });
    assert.deepEqual(loraPayload({ civitai_api_key: ' new-key ' }, saved),
        { enabled: true, trigger_words_enabled: false, path: 'custom_nodes/LM', civitai_api_key: 'new-key' });
    assert.deepEqual(loraPayload({ clear_civitai_api_key: true, civitai_api_key: 'ignored' }, saved),
        { enabled: true, trigger_words_enabled: false, path: 'custom_nodes/LM', clear_civitai_api_key: true });
    assert.deepEqual(loraPayload({ lora_enabled: true }, {}), { enabled: true, trigger_words_enabled: false, path: '' });
    assert.equal(keyText({ lora_has_key: true }, {}), KEY_COPY.saved);
    assert.equal(keyText({ lora_has_key: false }, {}), KEY_COPY.none);
    assert.equal(keyText({ lora_has_key: true }, { clear_civitai_api_key: true }), KEY_COPY.clearing);
    assert.equal(keyText({ lora_has_key: false }, { civitai_api_key: 'k' }), KEY_COPY.replacing);
    assert.equal(keyText({}, {}), null);
    assert.deepEqual(KEY_COPY, { saved: 'A key is saved.', none: 'No key saved.', clearing: 'The saved key is removed when you save.',
        replacing: 'The new key replaces the saved one when you save.' });
});

test('LoRA rows show detected / enabled / cache-loaded separately', async () => {
    const { detectionText, cacheText } = await integrations;
    assert.equal(detectionText({ lora_detected: true, lora_detected_path: 'custom_nodes/ComfyUI-Lora-Manager' }),
        'Detected: custom_nodes/ComfyUI-Lora-Manager.');
    assert.equal(detectionText({ lora_detected: true, lora_detected_path: '' }), 'Detected.');
    assert.equal(detectionText({ lora_detected: false }), 'Not detected in ComfyUI’s custom_nodes folders.');
    assert.equal(detectionText({}), null);
    // S10 review N5: a linked install outside custom_nodes is found but can't be enabled.
    assert.equal(detectionText({ lora_detected: true, lora_detected_path: 'LoRA-Manager', lora_detected_usable: false }),
        'Found at LoRA-Manager, but outside ComfyUI’s custom_nodes folders, so it can’t be enabled.');
    assert.equal((await integrations).loraValues({ detected: true, detected_usable: false }).lora_detected_usable, false);
    assert.equal(cacheText({ lora_cache_loaded: true }), 'Trigger words are loaded.');
    assert.equal(cacheText({ lora_cache_loaded: false }), 'Trigger words are not loaded.');
    assert.equal(cacheText({}), null);
});

test('host compatibility shows the detected renderer as status (D106)', async () => {
    const { rendererText, RENDERER_COPY } = await integrations;
    const host = (...renderers) => ({ hostId: 'h', label: 'PromptManager — ComfyUI', targets: renderers.map((renderer, id) =>
        ({ id: String(id), roles: ['positive'], renderer })) });
    assert.equal(rendererText([]), RENDERER_COPY.none);
    assert.equal(rendererText(null), RENDERER_COPY.none);
    assert.equal(rendererText([host()]), RENDERER_COPY.noNodes);
    assert.equal(rendererText([host('vue')]), RENDERER_COPY.noNodes);
    assert.equal(rendererText([host('classic')]), 'Detected: Classic (1 ComfyUI window).');
    assert.equal(rendererText([host('nodes2', 'nodes2')]), 'Detected: Nodes 2.0 (1 ComfyUI window).');
    assert.equal(rendererText([host('nodes2'), host('classic')]), 'Detected: Classic and Nodes 2.0 (2 ComfyUI windows).');
});

test('the import review shows replacement, blocked items and network effects (D107)', async () => {
    const { importGrid } = await integrations;
    const plan = { lora_files: 3, with_metadata: 2, current_imports: 5, blocked_by_pairs: 0, planned_downloads: 4,
        scope: { count: 2, label: 'Replace 5 imported prompts with 2 LoRAs' }, protected: { count: 0, reasons: [] },
        backup: { policy: 'on', destination_label: 'prompts.db.backup_<date and time>' }, network: { planned_downloads: 4, api_key: true },
        media: { deletes_files: false, label: 'No files are removed; image links are replaced' } };
    assert.deepEqual(importGrid(plan), [
        ['Scope', 'Replace 5 imported prompts with 2 LoRAs'],
        ['Found', '3 metadata files · 2 readable'],
        ['Protected', 'Nothing is protected'],
        ['Network', 'Downloads up to 4 example images from CivitAI with your saved key'],
        ['Backup', 'Create and verify before changes (prompts.db.backup_<date and time>)'],
        ['Media on disk', 'No files are removed; image links are replaced'],
    ]);
    const blocked = importGrid({ ...plan, planned_downloads: 1, network: { api_key: false }, protected: { count: 2,
        reasons: ['2 imported prompts used by saved pairs'] } });
    assert.deepEqual(blocked[2], ['Protected', '2 imported prompts used by saved pairs']);
    assert.deepEqual(blocked[3], ['Network', 'Downloads up to 1 example image from CivitAI']);
    assert.deepEqual(importGrid({})[0], ['Scope', '0 LoRAs']);
    assert.deepEqual(importGrid({})[3], ['Network', 'No downloads']);
    assert.deepEqual(importGrid({})[5], ['Media on disk', 'No files are removed']);
});

test('a LoRA import job reads in Activity: tiles, outcomes and the kept collection on failure', async () => {
    const { kindOf, finishToast, outcomeLine, viewOf } = await kinds;
    const view = kindOf('lora_import');
    assert.equal(view.pillVerb, 'Importing LoRAs');
    assert.deepEqual(view.tiles.map(([group]) => group), ['imported', 'linked', 'skipped', 'downloaded', 'download_errors', 'errors']);
    const counters = { imported: 3, linked: 1, skipped: 0, downloaded: 2, download_errors: 0, errors: 0 };
    const job = { kind: 'lora_import', title: 'LoRA reference import', state: 'finished', cancellable: false, counters };
    assert.equal(outcomeLine(job), '3 imported · 0 errors');
    assert.equal(finishToast(job), 'LoRA import finished · 3 imported · 0 errors');
    assert.equal(finishToast({ ...job, state: 'cancelled' }), 'LoRA import cancelled · 3 imported');
    const failed = { ...job, state: 'failed', status_line: 'The import failed; the previous LoRA imports were kept.' };
    assert.equal(finishToast(failed), 'LoRA import failed · The import failed; the previous LoRA imports were kept.');
    assert.equal(finishToast({ ...failed, status_line: '' }), 'LoRA import failed · 3 imported · 0 errors');
    assert.equal(viewOf(job).tiles.length, 6);
    // The legacy page's held import (no counters) reads as the generic job.
    assert.equal(viewOf({ kind: 'lora_import', title: 'LoRA import (admin page)', cancellable: false, counters: {} }).tiles.length, 0);
});

test('a failed import the files or saved pairs overtook is reviewed again in Integrations', async () => {
    const { reviewAgainOf } = await import('../../web/js/workspace/activity-job-view.mjs');
    const failed = failure => ({ kind: 'lora_import', state: 'failed', scope: { operation: 'lora_import', failure } });
    for (const failure of ['plan-changed', 'storage-changed', 'plan-expired', 'blocked-by-pairs']) {
        assert.deepEqual(reviewAgainOf(failed(failure)), { section: '#/system/integrations' }, failure);
    }
    assert.equal(reviewAgainOf(failed('import-failed')), null);
    assert.equal(reviewAgainOf({ ...failed('plan-changed'), state: 'cancelled' }), null);
});

test('a host save in two parts reports exactly which part saved (S10 review I3)', async () => {
    const { writeHostInParts } = await integrations;
    const saved = { lora_enabled: true, lora_trigger_words_enabled: false, lora_path: 'custom_nodes/LM' };
    const calls = [];
    const ok = revision => async body => { calls.push(body); return { success: true, persisted: true, revision }; };
    const refuse = (code, message) => async body => { calls.push(body); throw Object.assign(new Error(message), { code }); };
    const run = (changes, postLora, postSettings) => writeHostInParts({ changes, saved, expected_revision: 'r0', postLora, postSettings });

    // Both parts: the second carries the revision the first returned.
    assert.equal((await run({ lora_trigger_words_enabled: true, result_timeout: 5 }, ok('r1'), ok('r2'))).revision, 'r2');
    assert.deepEqual(calls.map(body => body.expected_revision), ['r0', 'r1']);
    // LoRA saved, settings refused: the LoRA keys and the new revision ride on the settings error.
    calls.length = 0;
    const later = await run({ lora_trigger_words_enabled: true, gallery_root_paths: ['x'] }, ok('r1'),
        refuse('invalid-source', 'x cannot be used')).catch(error => error);
    assert.equal(later.code, 'invalid-source');
    assert.deepEqual(later.partial, { saved: ['lora_trigger_words_enabled'], revision: 'r1' });
    // LoRA refused (not detected), settings still sent with the unchanged revision and saved.
    calls.length = 0;
    const first = await run({ lora_enabled: true, result_timeout: 5 }, refuse(null, 'LoraManager not found'), ok('r2'))
        .catch(error => error);
    assert.equal(first.message, 'LoraManager not found');
    assert.deepEqual(first.partial, { saved: ['result_timeout'], revision: 'r2' });
    assert.deepEqual(calls.map(body => body.expected_revision), ['r0', 'r0']);
    // A conflict on the first write stops there; a lone part fails as itself.
    calls.length = 0;
    const conflict = await run({ lora_enabled: false, result_timeout: 5 }, refuse('settings-changed', 'changed'), ok('r2'))
        .catch(error => error);
    assert.equal(conflict.code, 'settings-changed');
    assert.equal(conflict.partial, undefined);
    assert.equal(calls.length, 1);
    const alone = await run({ result_timeout: 5 }, ok('r1'), refuse('busy', 'scan running')).catch(error => error);
    assert.equal(alone.code, 'busy');
    assert.equal(alone.partial, undefined);
    const both = await run({ lora_enabled: true, result_timeout: 5 }, refuse(null, 'a'), refuse(null, 'b')).catch(error => error);
    assert.equal(both.message, 'b');
    assert.equal(both.partial, undefined);
});
