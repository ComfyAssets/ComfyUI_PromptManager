/**
 * System › Integrations (#249 System S10 Task 27): the concept's `renderSystem` "integrations" body (app.js 682–684).
 *
 * - "ComfyUI": Default send destination, Host compatibility and Return to canvas after send. D106 (Q8): the send
 *   bridge always asks for the window and node, and the nodes always follow ComfyUI's own renderer, so only "Ask each
 *   time" and "Follow host renderer" can be chosen; the other options and the return toggle are disabled, with the
 *   reason under the row, and nothing is stored for them. Host compatibility also says which renderer the open
 *   ComfyUI windows use, as the send bridge reports it (`renderer` on each Prompt Manager node it lists).
 * - "LoRA & resources": "LoraManager integration" (enabled, a host draft) says where LoraManager was detected;
 *   D106 adds the read-only "LoraManager path" row, as on the legacy page. "Automatically include trigger words"
 *   (a host draft) says whether the trigger words are loaded. The three are separate facts (`/lora/status`).
 *   D114: the CivitAI key field is never filled in; typing drafts a new key, a blank field keeps the saved one, and
 *   "Clear saved key" (shown while a key is saved) drafts its removal; the line under it says "A key is saved." or "No key
 *   saved." All of them are
 *   saved by the one Save row (`/lora/enable` with the settings revision, system.mjs).
 * - "LoRA reference data" → "Review import" (D107; the concept's button only simulated a job): the server
 *   reviews the import (`POST /lora/import/preview`, read-only) and the shared review dialog shows what it replaces,
 *   what saved pairs block, what it downloads and the backup box; Start runs the "LoRA reference import" Activity job.
 */
import { createButton } from '../shared/toolkit/core.js';
import { createBridgeClient } from '../shared/toolkit/prompt-bridge.js';
import { settingRow, operationRow } from './system-controls.mjs';
import { HOST_UNAVAILABLE } from './system-appearance.mjs';
import { closeWorkspaceDialog, openContentDialog, openReviewDialog } from './workspace-dialogs.mjs';
import { BACKUP_COPY, applyPlan, backupText, planNote, protectedText, reviewRefusal } from './maintenance-review.mjs';
import { downloadDatabaseBackup } from './activity-scan.mjs';
import { showWorkspaceToast } from './toast.mjs';
import { navigateTo } from './state.mjs';
import { noun } from './plural.mjs';

export const LORA_STATUS_URL = '/prompt_manager/lora/status';
export const LORA_ENABLE_URL = '/prompt_manager/lora/enable';
export const IMPORT_PREVIEW_URL = '/prompt_manager/lora/import/preview';
export const IMPORT_APPLY_URL = '/prompt_manager/lora/import/apply';
export const IMPORT_TITLE = 'Review LoRA import';
export const IMPORT_START = 'Start import';
/** The host draft keys this section saves through `/lora/enable`; every other host key goes to `/settings`. */
export const LORA_KEYS = Object.freeze(['lora_enabled', 'lora_trigger_words_enabled', 'civitai_api_key', 'clear_civitai_api_key']);

/** D106: why the options the bridge cannot honour are disabled. */
export const REASONS = Object.freeze({
    send: 'Sending always asks for the ComfyUI window and node, so “Ask each time” is the only option.',
    renderer: 'Prompt Manager nodes always follow ComfyUI’s own renderer setting.',
    returnCanvas: 'Not available: sending applies the text in ComfyUI and keeps this page open.',
});
export const RENDERER_COPY = Object.freeze({
    looking: 'Looking for open ComfyUI windows…',
    none: 'No ComfyUI window is open in this browser, so its renderer is not known.',
    noNodes: 'ComfyUI is open; add a Prompt Manager node to see which renderer it uses.',
    unavailable: 'The renderer could not be checked in this browser.',
});
const RENDERER_NAMES = Object.freeze({ classic: 'Classic', nodes2: 'Nodes 2.0' });
export const KEY_COPY = Object.freeze({ saved: 'A key is saved.', none: 'No key saved.',
    clearing: 'The saved key is removed when you save.', replacing: 'The new key replaces the saved one when you save.' });
export const NOTHING_TO_IMPORT = 'There is nothing to import; nothing was changed.';
export const BLOCKED_NOTE = 'Delete or change the saved pairs that use these prompts, then review the import again. Nothing was changed.';
export const CACHE_NOTE = 'Example images downloaded before a failure stay in the image cache.';

const number = value => value.toLocaleString('en-US');

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

/** The host values a `/lora/status` answer gives: drafted ones (enabled, trigger words, clear) and read-only facts. */
export function loraValues(status) {
    return {
        lora_enabled: status?.enabled === true,
        lora_trigger_words_enabled: status?.trigger_words_enabled === true,
        clear_civitai_api_key: false,
        lora_path: typeof status?.path === 'string' ? status.path : '',
        lora_detected: status?.detected === true,
        lora_detected_path: typeof status?.detected_path === 'string' ? status.detected_path : '',
        lora_detected_usable: status?.detected_usable !== false,
        lora_has_key: status?.has_civitai_api_key === true,
        lora_cache_loaded: status?.trigger_cache_loaded === true,
    };
}

/** Host draft changes split by route: `{lora, settings}`, each null when it has none. */
export function splitHostChanges(changes) {
    const lora = {};
    const settings = {};
    for (const [key, value] of Object.entries(changes ?? {})) (LORA_KEYS.includes(key) ? lora : settings)[key] = value;
    return { lora: Object.keys(lora).length ? lora : null, settings: Object.keys(settings).length ? settings : null };
}

/**
 * The `/lora/enable` body for the drafted LoRA `changes` over the `saved` host values: the whole LoRA state (the route
 * saves all of it), the saved path, and the key only when one was typed (a blank or absent key keeps the saved one).
 */
export function loraPayload(changes, saved) {
    const value = key => (Object.hasOwn(changes, key) ? changes[key] : saved?.[key]);
    const body = { enabled: value('lora_enabled') === true, trigger_words_enabled: value('lora_trigger_words_enabled') === true,
        path: saved?.lora_path || '' };
    if (changes.clear_civitai_api_key === true) body.clear_civitai_api_key = true;
    else if (typeof changes.civitai_api_key === 'string' && changes.civitai_api_key.trim()) body.civitai_api_key = changes.civitai_api_key.trim();
    return body;
}

/**
 * Write host `changes` in their two parts (S10 review I3): the LoRA keys through `postLora` (`/lora/enable`), then the
 * other keys through `postSettings` (`/settings`), each with the revision the previous write left. Resolves with the
 * last answer when everything saved. When only one part saved, rejects with the other part's error carrying
 * `partial: {saved: [keys], revision}`, so the store keeps what saved and adopts the new revision; a
 * conflict on the first write (another page changed config.json) stops before the second.
 */
export async function writeHostInParts({ changes, saved, expected_revision, postLora, postSettings }) {
    const { lora, settings } = splitHostChanges(changes);
    let revision = expected_revision;
    let result = null;
    const written = [];
    let refused = null;
    if (lora) {
        try {
            result = await postLora({ ...loraPayload(lora, saved), expected_revision: revision });
            if (typeof result?.revision === 'string') revision = result.revision;
            written.push(...Object.keys(lora));
        } catch (error) {
            if (!settings || error?.code === 'settings-changed') throw error;
            refused = error;
        }
    }
    if (settings) {
        try {
            result = await postSettings({ ...settings, expected_revision: revision });
            if (typeof result?.revision === 'string') revision = result.revision;
            written.push(...Object.keys(settings));
        } catch (error) {
            if (!written.length) throw error;
            throw Object.assign(error, { partial: { saved: written, revision } });
        }
    }
    if (refused) {
        throw Object.assign(refused, { partial: { saved: written, revision } });
    }
    return result;
}

/** The renderer line for discovered bridge hosts (D106). */
export function rendererText(hosts) {
    if (!Array.isArray(hosts) || hosts.length === 0) return RENDERER_COPY.none;
    const seen = new Set();
    for (const host of hosts) for (const target of host?.targets ?? []) if (RENDERER_NAMES[target?.renderer]) seen.add(target.renderer);
    if (!seen.size) return RENDERER_COPY.noNodes;
    const names = ['classic', 'nodes2'].filter(name => seen.has(name)).map(name => RENDERER_NAMES[name]);
    return `Detected: ${names.join(' and ')} (${number(hosts.length)} ComfyUI ${noun(hosts.length, 'window')}).`;
}

/** The LoraManager row's line: where it was detected, or that it was not. */
export function detectionText(values) {
    if (values.lora_detected === undefined) return null;
    if (!values.lora_detected) return 'Not detected in ComfyUI’s custom_nodes folders.';
    if (values.lora_detected_usable === false) {
        // A linked install outside custom_nodes is found but cannot be enabled (S10 review N5).
        return `Found at ${values.lora_detected_path || 'a linked folder'}, but outside ComfyUI’s custom_nodes folders, so it can’t be enabled.`;
    }
    return values.lora_detected_path ? `Detected: ${values.lora_detected_path}.` : 'Detected.';
}

/** The trigger-words row's line (the cache, separate from the setting). */
export function cacheText(values) {
    if (values.lora_cache_loaded === undefined) return null;
    return values.lora_cache_loaded ? 'Trigger words are loaded.' : 'Trigger words are not loaded.';
}

/** D114: the key line for the saved state and the drafts. */
export function keyText(values, changes) {
    if (changes?.clear_civitai_api_key === true) return KEY_COPY.clearing;
    if (typeof changes?.civitai_api_key === 'string' && changes.civitai_api_key.trim()) return KEY_COPY.replacing;
    if (values.lora_has_key === undefined) return null;
    return values.lora_has_key ? KEY_COPY.saved : KEY_COPY.none;
}

/** D107: the import review's rows from the server's plan. */
export function importGrid(plan) {
    const downloads = Number.isSafeInteger(plan.planned_downloads) ? plan.planned_downloads : 0;
    const network = downloads
        ? `Downloads up to ${number(downloads)} example ${noun(downloads, 'image')} from CivitAI${plan.network?.api_key ? ' with your saved key' : ''}`
        : 'No downloads';
    const found = Number.isSafeInteger(plan.lora_files) ? plan.lora_files : 0;
    const readable = Number.isSafeInteger(plan.with_metadata) ? plan.with_metadata : 0;
    return [
        ['Scope', plan.scope?.label || `${number(readable)} ${noun(readable, 'LoRA')}`],
        ['Found', `${number(found)} metadata ${noun(found, 'file')} · ${number(readable)} readable`],
        ['Protected', protectedText(plan.protected)],
        ['Network', network],
        ['Backup', backupText(plan.backup)],
        ['Media on disk', plan.media?.label || 'No files are removed'],
    ];
}

function namesList(plan) {
    const names = (Array.isArray(plan.details) ? plan.details : []).map(row => row?.label).filter(label => typeof label === 'string');
    if (!names.length) return [];
    const list = el('details');
    const count = Number.isSafeInteger(plan.with_metadata) ? plan.with_metadata : names.length;
    list.append(el('summary', names.length < count ? `LoRAs found (${number(names.length)} of ${number(count)})` : `LoRAs found (${number(names.length)})`));
    const body = el('pre', names.join('\n'));
    body.tabIndex = 0;
    list.append(body);
    return [list];
}

function closeOnly({ scope, trigger, plan, note }) {
    const grid = el('dl', undefined, 'meta-grid');
    for (const [term, value] of importGrid(plan)) {
        const entry = el('div');
        entry.append(el('dt', term), el('dd', value));
        grid.append(entry);
    }
    const close = createButton('Close');
    close.addEventListener('click', () => closeWorkspaceDialog());
    const actions = el('div', undefined, 'dialog-actions');
    actions.append(close);
    openContentDialog({ scope, trigger, title: IMPORT_TITLE, nodes: [el('h3', 'LoRA reference data'), grid, ...namesList(plan),
        el('p', note, 'small'), actions] });
}

/** Show the reviewed import; Start applies exactly this token (D107). */
export function showImportPlan(plan, { api, store, scope, trigger = null, reviewAgain, notify = showWorkspaceToast,
    navigate = navigateTo, backup = () => downloadDatabaseBackup() }) {
    if (!plan.token) {
        closeOnly({ scope, trigger, plan, note: plan.blocked_by_pairs ? BLOCKED_NOTE : NOTHING_TO_IMPORT });
        return;
    }
    if (!plan.with_metadata && !plan.current_imports) {
        closeOnly({ scope, trigger, plan, note: NOTHING_TO_IMPORT });
        return;
    }
    const removesAll = !plan.with_metadata;
    const sentence = [planNote(plan), removesAll ? 'No LoRA metadata was found: this removes every imported LoRA prompt.' : '', CACHE_NOTE]
        .filter(Boolean).join(' ');
    openReviewDialog({ scope, trigger, title: IMPORT_TITLE, heading: 'LoRA reference data', grid: importGrid(plan),
        options: namesList(plan), sentence, backup: { copy: BACKUP_COPY, onBackup: backup }, cancelLabel: 'Cancel',
        primary: { label: IMPORT_START, danger: removesAll },
        onStart: () => applyPlan({ api, store, scope, token: plan.token, reviewAgain, notify, navigate, url: IMPORT_APPLY_URL }) });
}

/** Review the import (read-only) and show its plan; the pressed button reads "Reviewing…" until it answered. */
export async function reviewImport({ api, store, scope, trigger = null, notify = showWorkspaceToast, navigate = navigateTo }) {
    if (trigger?.getAttribute('aria-disabled') === 'true') return;
    const again = () => reviewImport({ api, store, scope, trigger, notify, navigate });
    const label = trigger?.textContent;
    trigger?.setAttribute('aria-disabled', 'true');
    if (trigger) trigger.textContent = 'Reviewing…';
    try {
        let plan;
        try {
            plan = await api.post(IMPORT_PREVIEW_URL, {}, { signal: scope.signal });
        } catch (error) {
            if (!scope.signal.aborted) notify(reviewRefusal(error));
            return;
        }
        if (!scope.signal.aborted) showImportPlan(plan, { api, store, scope, trigger, notify, navigate, reviewAgain: again });
    } finally {
        trigger?.removeAttribute('aria-disabled');
        if (trigger) trigger.textContent = label;
    }
}

/** The row's control (`data-setting`), wherever the Toolkit switch nests it. */
const controlOf = (row, id) => row.querySelector(`[data-setting="${id}"]`);

/** A `p.small` status line under a row's description, created on first use. */
function noteOf(row) {
    let note = row.children[0].querySelector(':scope > p.small');
    if (!note) {
        note = el('p', undefined, 'small');
        row.children[0].append(note);
    }
    return note;
}

function setNote(row, text) {
    if (text) noteOf(row).textContent = text;
    else row.children[0].querySelector(':scope > p.small')?.remove();
}

/** A select row whose options after the first are disabled (D106). */
function onlyFirst(spec) {
    const row = settingRow({ ...spec, control: { type: 'select', options: spec.options } });
    for (const option of controlOf(row, spec.id).options) option.disabled = option.index > 0;
    return row;
}

/** The "ComfyUI" surface (D106) and its renderer detection over the send bridge. */
function comfySurface({ scope, clientFactory }) {
    const surface = el('section', undefined, 'surface');
    surface.append(el('h2', 'ComfyUI'), el('p', 'One send contract across cards, prompt history and every metadata view.'));
    const send = onlyFirst({ id: 'send-destination', label: 'Default send destination',
        description: 'A destination picker previews both roles before applying.',
        options: ['Ask each time', 'Selected Prompt Manager node', 'Create a Prompt Manager (+/-) node'] });
    const renderer = onlyFirst({ id: 'renderer', label: 'Host compatibility',
        description: 'Use the shared compatibility layer for Classic and Nodes 2.0.',
        options: ['Follow host renderer', 'Classic', 'Nodes 2.0'] });
    const returnRow = settingRow({ id: 'return-canvas', label: 'Return to canvas after send',
        description: 'Apply text without queuing generation.', control: { type: 'toggle', checked: false } });
    controlOf(returnRow, 'return-canvas').disabled = true;
    setNote(send, REASONS.send);
    setNote(returnRow, REASONS.returnCanvas);
    const status = el('p', RENDERER_COPY.looking, 'small');
    status.setAttribute('role', 'status');
    status.id = 'renderer-status';
    renderer.children[0].append(el('p', REASONS.renderer, 'small'), status);
    surface.append(send, renderer, returnRow);

    let client = null;
    // Overlapping checks (two quick visibility changes) paint only the newest answer (S10 review N2).
    let latest = 0;
    const detect = async () => {
        const check = ++latest;
        let text;
        try {
            client ??= clientFactory();
            text = rendererText(await client.discover({ waitMs: 300 }));
        } catch {
            text = RENDERER_COPY.unavailable;
        }
        if (check === latest && !scope.signal.aborted) status.textContent = text;
    };
    scope.cleanup(() => client?.dispose());
    // Another tab may open or switch ComfyUI meanwhile: look again when this page is shown again.
    scope.listen(document, 'visibilitychange', () => { if (document.visibilityState === 'visible') detect(); });
    detect();
    return surface;
}

function bridgeFactory() {
    const url = new URL(window.ApiClient.resolveUrl('/prompt_manager/'), window.location.href);
    return () => createBridgeClient({ namespace: url.pathname.slice(0, -'prompt_manager/'.length), origin: window.location.origin });
}

/**
 * Mount Integrations in `container` (System's `.settings-layout`); returns the section's two surfaces. `drafts` is the
 * System draft store (its host domain carries the LoRA values, system.mjs), `activity` the page's Activity store.
 */
export function mountIntegrations(container, { api, activity, scope, drafts, clientFactory = bridgeFactory(),
    notify = showWorkspaceToast, navigate = navigateTo }) {
    const comfy = comfySurface({ scope, clientFactory });
    const surface = el('section', undefined, 'surface');
    surface.append(el('h2', 'LoRA & resources'), el('p', 'Preserves LoraManager detection, data import and positive-only trigger injection.'));
    const enable = settingRow({ id: 'lora-enable', label: 'LoraManager integration', description: 'Checking for LoraManager…',
        control: { type: 'toggle', checked: false }, scope, onChange: value => drafts.set('host', 'lora_enabled', value) });
    const pathRow = settingRow({ id: 'lora-path', label: 'LoraManager path',
        description: 'Found automatically in ComfyUI’s custom_nodes folders; read-only, as on the existing settings page.',
        control: { type: 'text' } });
    const pathInput = controlOf(pathRow, 'lora-path');
    pathInput.readOnly = true;
    pathInput.placeholder = 'Not detected';
    // Wide enough for the usual install path within the concept's 230px control limit.
    pathInput.size = 34;
    const triggers = settingRow({ id: 'lora-triggers', label: 'Automatically include trigger words',
        description: 'Trigger words apply only to positive text; connected node inputs remain authoritative.',
        control: { type: 'toggle', checked: false }, scope, onChange: value => drafts.set('host', 'lora_trigger_words_enabled', value) });
    const key = settingRow({ id: 'civitai-key', label: 'CivitAI API key', description: 'Credentials stay in the server configuration.',
        control: { type: 'password' } });
    const keyInput = controlOf(key, 'civitai-key');
    // Never a login the browser offers to save or fill (S10 review N3).
    keyInput.autocomplete = 'new-password';
    keyInput.spellcheck = false;
    keyInput.placeholder = 'Optional';
    // D114: the saved-key line is announced (a polite live region) and describes the field (S10 review M2).
    const keyNote = el('p', '', 'small');
    keyNote.id = 'civitai-key-status';
    keyNote.setAttribute('role', 'status');
    key.children[0].append(keyNote);
    keyInput.setAttribute('aria-describedby', keyNote.id);
    const clear = createButton('Clear saved key');
    clear.dataset.setting = 'clear_civitai_api_key';
    clear.dataset.key = 'clear_civitai_api_key';
    const keyActions = el('div', undefined, 'row-actions');
    keyActions.append(clear);
    key.children[0].append(keyActions);
    scope.listen(keyInput, 'input', () => {
        // D114: a blank field keeps the saved key: it is no draft at all.
        drafts.set('host', 'civitai_api_key', keyInput.value.trim() ? keyInput.value : undefined);
        if (keyInput.value.trim()) drafts.set('host', 'clear_civitai_api_key', false);
    });
    scope.listen(clear, 'click', () => {
        keyInput.value = '';
        // The button leaves with the saved key: keep the keyboard on the field it belongs to (S10 review M2).
        keyInput.focus();
        drafts.set('host', 'civitai_api_key', undefined);
        drafts.set('host', 'clear_civitai_api_key', true);
    });
    const importRow = operationRow({ scope, title: 'LoRA reference data', description: 'Review and import resource metadata.',
        label: 'Review import', onClick: trigger => reviewImport({ api, store: activity, scope, trigger, notify, navigate }) });
    surface.append(enable, pathRow, triggers, key, importRow);

    const controls = [[enable, 'lora-enable', 'lora_enabled'], [triggers, 'lora-triggers', 'lora_trigger_words_enabled']]
        .map(([row, id, name]) => ({ row, name, input: controlOf(row, id) }));
    for (const control of controls) control.input.dataset.key = control.name;
    keyInput.dataset.key = 'civitai_api_key';

    // The saved LoRA state as last painted: a save (or another tab's change read back) that changes it reads
    // `/lora/status` again, so detection, the key line and the trigger cache follow what the server now holds.
    let shown;
    const repaint = () => {
        const values = drafts.saved('host');
        const changes = drafts.changes('host');
        const unread = values.lora_enabled === undefined;
        const failed = drafts.hostState?.() === 'failed' || (drafts.hostState?.() === 'loaded' && unread);
        for (const { input, name } of controls) {
            const value = drafts.get('host', name);
            input.disabled = value === undefined;
            if (value !== undefined) input.checked = value === true;
        }
        enable.children[0].children[1].textContent = failed && unread ? HOST_UNAVAILABLE
            : detectionText(values) ?? 'Checking for LoraManager…';
        pathInput.value = values.lora_path || values.lora_detected_path || '';
        setNote(triggers, cacheText(values));
        keyInput.disabled = unread;
        keyNote.textContent = keyText(values, changes) ?? '';
        // D114: "Clear saved key" is offered only while a saved key would stay.
        if (values.lora_has_key === true && changes.clear_civitai_api_key !== true) key.children[0].append(keyActions);
        else {
            if (keyActions.contains(document.activeElement)) keyInput.focus();
            keyActions.remove();
        }
        const state = JSON.stringify([values.lora_enabled, values.lora_trigger_words_enabled, values.civitai_api_key,
            values.clear_civitai_api_key]);
        const saved = values.civitai_api_key !== undefined || values.clear_civitai_api_key === true;
        if (!unread && shown !== undefined && (state !== shown || saved)) {
            if (saved) keyInput.value = '';
            shown = undefined;
            drafts.reloadHost();
            return;
        }
        if (!unread) shown = state;
    };
    scope.cleanup(drafts.subscribe(repaint));
    // A typed key is held only while its field shows it: leaving the section drops it, since the field comes back
    // empty and a hidden key draft could not be seen or discarded (S10 review N4).
    scope.cleanup(() => drafts.set('host', 'civitai_api_key', undefined));
    repaint();
    return [comfy, surface];
}
