/**
 * System › Auto-tagging (#328, System S11 Tasks 6–7): the concept's `renderSystem` "tagging" body (app.js 685–686).
 *
 * One `section.surface` "Auto-tagging" with the concept's rows, in its order, built with system-controls.mjs:
 * - "Tagging model" (`#tag-model`, host key `autotag_model_type`). D132: the four real engines in the concept's order,
 *   each `<label> · <size_label>` from `GET /prompt_manager/autotag/models` (read once per mount; it never loads or
 *   downloads a model), and the row's description is the chosen model's live status instead of the concept's design
 *   note. While the read is pending the select is disabled and says "Loading model status…".
 * - "Existing tags", "General-tag confidence", "Character-tag confidence": the concept's labels and descriptions
 *   verbatim (they state the shipped defaults); host keys `autotag_existing`, `autotag_general_threshold`,
 *   `autotag_character_threshold`. D133 adds "Suggestions per image" (`autotag_suggestion_limit`, 1–100) after them.
 * - D202 (#431): the "Skipped tags" block after it (system-skipped-tags.mjs, host key `autotag_skipped_tags`).
 * - The `.field` "LLM tag template" (`#tag-template`, `autotag_template`).
 * - `.row-actions`: "Preview tagging review" (`ca-primary`) opens the Tag image dialog for the image selected in Images
 *   with the section's current model and values, aria-disabled without one (the D115 sentence) or with an invalid
 *   value (the toast names it). D132: the concept's two simulated job buttons are "Download" (or
 *   "Download again" for a model on disk), which starts the `model_download` Activity job and opens it, and "Unload",
 *   a direct `POST /autotag/unload` (enabled while a model is loaded; a busy model's refusal names the operation).
 * - D134: the `.operation` "Tag output images" › "Review" previews the per-image batch (#379,
 *   `POST /autotag/batch/preview`, read-only: image counts and a time estimate) and shows it in the shared review dialog
 *   with the D55 backup box; "Start tagging" runs the cancellable `autotag` Activity job, which tags each output image
 *   from its own pixels and never a prompt. A review that cannot start (Replace after review, a short disk, no image,
 *   nothing to tag) offers only Cancel and says why.
 *
 * Every value is a host draft saved by the one Save row (system.mjs); every listener sits on the section's scope.
 */
import { createButton } from '../shared/toolkit/core.js';
import { settingRow, operationRow } from './system-controls.mjs';
import { HOST_UNAVAILABLE } from './system-appearance.mjs';
import { openCloseOnlyReview, openReviewDialog } from './workspace-dialogs.mjs';
import { BACKUP_COPY, backupText, holding, reviewRefusal } from './maintenance-review.mjs';
import { busyToast, downloadDatabaseBackup } from './activity-scan.mjs';
import { MODEL_OPTIONS, installCommand, missingPackages, needsPackages, packageList } from './image-tagging-state.mjs';
import { openImageTagging } from './image-tagging.mjs';
import { selectedImage } from './images.mjs';
import { NO_SELECTION } from './vocabulary.mjs';
import { showWorkspaceToast } from './toast.mjs';
import { navigateTo } from './state.mjs';
import { skippedTagsBlock } from './system-skipped-tags.mjs';

export const MODELS_URL = '/prompt_manager/autotag/models';
export const UNLOAD_URL = '/prompt_manager/autotag/unload';
export const BATCH_PREVIEW_URL = '/prompt_manager/autotag/batch/preview';
export const DOWNLOAD_KIND = 'model-download';
export const BATCH_KIND = 'autotag';
export const REVIEW_TITLE = 'Review tag output images';
export const BATCH_TITLE = 'Tag output images';
export const START_LABEL = 'Start tagging';
export const INTRO = 'One tagging configuration for images and prompt-library actions. Every result can be reviewed before applying.';
export const BATCH_DESCRIPTION = 'Tag each output image from its own pixels with the model above. Prompts are not changed. Review the scope first.';
export const SUGGESTIONS_DESCRIPTION = 'From 1 to 100: the most suggestions Generate shows for one image.';

/** D132: the engines in the concept's order, with the labels and sizes the server states when it cannot be read. */
export const MODEL_LABELS = Object.freeze({
    'wd14-swinv2': Object.freeze({ label: 'WD14 SwinV2', size: '~400 MB' }),
    'wd14-vit': Object.freeze({ label: 'WD14 ViT', size: '~400 MB' }),
    gguf: Object.freeze({ label: 'GGUF JoyCaption', size: '~4.5 GB' }),
    hf: Object.freeze({ label: 'HuggingFace JoyCaption', size: '~16 GB' }),
});
export const MODEL_IDS = Object.freeze(MODEL_OPTIONS.map(option => option.id));
const CLASSIFIERS = new Set(MODEL_OPTIONS.filter(option => option.classifier).map(option => option.id));

export const EXISTING_OPTIONS = Object.freeze([['skip', 'Skip already tagged'], ['merge', 'Merge with existing'],
    ['replace_after_review', 'Replace after review']]);

/** The host draft key of each control (`id` → key). */
export const SETTING_KEYS = Object.freeze({
    'tag-model': 'autotag_model_type',
    'tag-existing': 'autotag_existing',
    'threshold-general': 'autotag_general_threshold',
    'threshold-character': 'autotag_character_threshold',
    'tag-suggestions': 'autotag_suggestion_limit',
    'tag-template': 'autotag_template',
});
const NUMERIC = new Set(['autotag_general_threshold', 'autotag_character_threshold', 'autotag_suggestion_limit']);
const MAX_TEMPLATE_CHARS = 4000;

export const STATUS_COPY = Object.freeze({
    loading: 'Loading model status…',
    failed: 'Model status could not be read.',
    downloaded: 'Downloaded. Not loaded.',
    loaded: 'Downloaded and loaded.',
    partial: 'Partly downloaded. Download again to finish.',
    missing: size => `Not downloaded. Download size ${size}.`,
    needs: (names, command, note) => `Downloaded. Needs ${names}: ${command}.${typeof note === 'string' && note.trim() ? ` ${note}` : ''}`,
});

/** The messages that name a setting the section cannot use (the server's own wording). */
export const SETTING_PROBLEMS = Object.freeze({
    autotag_general_threshold: 'General-tag confidence must be between 0 and 1.',
    autotag_character_threshold: 'Character-tag confidence must be between 0 and 1.',
    // #369: every page shows the thresholds in steps of 0.05, so the server saves only values on that grid.
    autotag_general_threshold_step: 'General-tag confidence must be a multiple of 0.05.',
    autotag_character_threshold_step: 'Character-tag confidence must be a multiple of 0.05.',
    autotag_suggestion_limit: 'Suggestions per image must be a whole number from 1 to 100.',
    // The server's two template sentences (`validate_template`), blank first.
    autotag_template: 'LLM tag template must be non-empty text.',
    autotag_template_length: `LLM tag template must be at most ${MAX_TEMPLATE_CHARS} characters.`,
});
/** Characters as Python's `len` counts them (code points), not UTF-16 units. */
export const characters = text => [...String(text ?? '')].length;

export const REPLACE_NOTE = 'Replacing tags needs a review of each image. Use Preview tagging review, or choose Skip or Merge for a batch run.';
export const NOTHING_TO_TAG = 'Every output image is already auto-tagged.';
export const NO_IMAGES = 'The output folders contain no images to tag.';
/** The Time row's tails (#379): where WD14 runs, and the language models, which have no estimate. */
export const TIME_ON_CPU = ' on a processor; a graphics card can be faster.';
export const TIME_ON_GPU = ' on the graphics card.';
export const TIME_UNKNOWN = 'Language-model engines take longer per image. Activity shows the time left once tagging starts.';
export const STATUS_UNREAD = 'The model status is still being read. Try again in a moment.';
export const SETTINGS_UNREAD = 'The settings are still being read. Try again in a moment.';
export const UNLOAD_FAILED = 'The tagging model could not be unloaded. Try again.';
export const UNKNOWN_MODEL = 'The saved tagging model is not one of these engines. Choose one and save preferences.';
export const DISK_SHORT_UNKNOWN = 'Not enough free disk space for the safety backup. Nothing is written until there is room.';
export const modelBusy = operation => `The tagging model is busy: ${operation || 'Another tagging operation'}. Try again when it finishes.`;
export const notDownloaded = label => `${label} is not downloaded. Download it first.`;
const START_SAYS = new Set(['model-unavailable', 'review-required', 'insufficient-space']);

const number = value => (Number.isSafeInteger(value) ? value : 0).toLocaleString('en-US');
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

/** The engine's display name: the server's label, else the documented one. */
export function modelLabel(id, entry) {
    return typeof entry?.label === 'string' && entry.label ? entry.label : MODEL_LABELS[id]?.label ?? id;
}

/** The download size the server states, else the documented one. */
export function modelSize(id, entry) {
    return typeof entry?.size_label === 'string' && entry.size_label ? entry.size_label : MODEL_LABELS[id]?.size ?? '';
}

/** D132 (OQ5): `<label> · <size_label>` per engine, in the concept's order. */
export function modelOptions(models) {
    return MODEL_IDS.map(id => [id, `${modelLabel(id, models?.[id])} · ${modelSize(id, models?.[id])}`]);
}

/**
 * D132: the Tagging model description for `status` (`{phase: loading|loaded|failed, models}`) and the chosen `model`.
 * GGUF with only one of its two files on disk is partly downloaded. A backend package the server cannot import (#424)
 * turns "Downloaded" into "Downloaded. Needs <pkg>: pip install <pkg>." and adds "Also needs <pkg>." otherwise.
 */
export function modelDescription(status, model) {
    if (status?.phase === 'loading') return STATUS_COPY.loading;
    const entry = status?.models?.[model];
    if (status?.phase !== 'loaded' || !object(entry)) return STATUS_COPY.failed;
    // #424: a backend package that cannot be imported; a model on disk without it never reads as ready.
    const needs = missingPackages(entry).length ? packageList(entry) : '';
    if (entry.downloaded === true) {
        if (needs) return STATUS_COPY.needs(needs, installCommand(entry), entry.install_note);
        return entry.loaded === true ? STATUS_COPY.loaded : STATUS_COPY.downloaded;
    }
    const files = partlyDownloaded(model, entry) ? STATUS_COPY.partial : STATUS_COPY.missing(modelSize(model, entry));
    return needs ? `${files} Also needs ${needs}.` : files;
}

/** GGUF with only one of its two files on disk (a download that did not finish). */
export const partlyDownloaded = (model, entry) => model === 'gguf' && object(entry) && entry.downloaded !== true
    && entry.model_exists !== entry.mmproj_exists;

/** D132 (OQ10): "Download again" for an engine on disk or partly on disk, else "Download". */
export const downloadLabel = (model, entry) => (entry?.downloaded === true || partlyDownloaded(model, entry) ? 'Download again' : 'Download');

/** The `/autotag/models` answer as section state: the per-engine entries and the loaded engine, if any. */
export function readStatus(value) {
    if (!object(value) || !object(value.models)) return { phase: 'failed', models: null, loadedType: null };
    const loadedType = value.model_loaded === true && MODEL_IDS.includes(value.loaded_model_type) ? value.loaded_model_type : null;
    return { phase: 'loaded', models: value.models, loadedType };
}

/** A number control's draft: a number when its text is one, else the trimmed text (the server then names the setting). */
export function coerceNumber(text) {
    const value = String(text ?? '').trim();
    if (value === '') return value;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : value;
}

/** The draft value a control's text stands for. */
export function draftValue(key, text) {
    return NUMERIC.has(key) ? coerceNumber(text) : String(text ?? '');
}

const unit = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1;
// A multiple of 0.05 within the server's tolerance (`validate_saved_threshold`): (0.1 + 0.2) * 20 is 6.000000000000001.
const onStep = value => Math.abs(value * 20 - Math.round(value * 20)) <= 1e-12;
// A threshold on the grid, handed on exactly as the server stores it (anything else is left for draftProblem to refuse).
const snapped = value => (unit(value) && onStep(value) ? Math.round(value * 20) / 20 : value);

/**
 * The first setting the chosen model would use that the section cannot send, as `{key, message}`, else null: WD14
 * reads both thresholds (0 to 1 in steps of 0.05, #369), the language models the template; Generate also reads the
 * suggestion count (`limit`).
 */
export function draftProblem(values, { limit = true } = {}) {
    const model = values.autotag_model_type;
    const keys = CLASSIFIERS.has(model) ? ['autotag_general_threshold', 'autotag_character_threshold'] : ['autotag_template'];
    if (limit) keys.push('autotag_suggestion_limit');
    for (const key of keys) {
        const value = values[key];
        if (key === 'autotag_template') {
            if (typeof value !== 'string' || value.trim() === '') return { key, message: SETTING_PROBLEMS.autotag_template };
            if (characters(value) > MAX_TEMPLATE_CHARS) return { key, message: SETTING_PROBLEMS.autotag_template_length };
            continue;
        }
        const valid = key === 'autotag_suggestion_limit' ? Number.isInteger(value) && value >= 1 && value <= 100 : unit(value);
        if (!valid) return { key, message: SETTING_PROBLEMS[key] };
        if (key !== 'autotag_suggestion_limit' && !onStep(value)) return { key, message: SETTING_PROBLEMS[`${key}_step`] };
    }
    return null;
}

/** The Tag image dialog's preset (`{model, options}`) from the drafts: only the chosen model's own options. */
export function taggingPreset(values) {
    const model = values.autotag_model_type;
    const options = CLASSIFIERS.has(model)
        ? { general_threshold: snapped(values.autotag_general_threshold),
            character_threshold: snapped(values.autotag_character_threshold) }
        : { instructions: values.autotag_template };
    return { model, options: { ...options, suggestion_limit: values.autotag_suggestion_limit } };
}

/** The `POST /jobs/autotag` body for the drafts (WD14: both thresholds; the language models: the template). */
export function batchBody(values) {
    const model = values.autotag_model_type;
    const options = CLASSIFIERS.has(model)
        ? { general_threshold: snapped(values.autotag_general_threshold),
            character_threshold: snapped(values.autotag_character_threshold) }
        : { instructions: values.autotag_template };
    return { model_type: model, existing: values.autotag_existing, options };
}

/** The toast for a start the server refused or that failed (`what` names the action). */
export function startRefusal(error, what) {
    if (error?.code === 'model-busy') return modelBusy(error.operation);
    if (START_SAYS.has(error?.code) && error.message) return error.message;
    return `${what} could not start. Try again.`;
}

/**
 * Start a job of route `kind` (`model-download`, `autotag`) through the Activity store, which toasts the start; open
 * its view. A slot held by another job names it (D57); any other refusal is toasted by its code. Resolves with the
 * job, or null when none started.
 */
export async function startJob({ activity, scope, kind, body, what, notify = showWorkspaceToast, navigate = navigateTo }) {
    let result;
    try {
        result = await activity.start(kind, body);
    } catch (error) {
        if (!scope.signal.aborted) notify(startRefusal(error, what));
        return null;
    }
    if (scope.signal.aborted) return result.job ?? null;
    if (result.busy !== undefined) {
        notify(busyToast(result.busy));
        return null;
    }
    navigate(`#/activity/${result.job.id}`);
    return result.job;
}

/** The existing-tags policy's label. */
export const existingLabel = value => EXISTING_OPTIONS.find(([id]) => id === value)?.[1] ?? String(value ?? '');

/**
 * DEC11 (#379): a whole number of minutes as the review's Time text: "about m min" under 90, then "about H h M min",
 * or "about H h" on the hour.
 */
export function durationText(minutes) {
    const m = Math.max(1, Math.ceil(Number.isFinite(minutes) ? minutes : 1));
    if (m < 90) return `about ${m} min`;
    const hours = Math.floor(m / 60);
    const rest = m % 60;
    return rest === 0 ? `about ${hours} h` : `about ${hours} h ${rest} min`;
}

/** The review's Time row: the preview's `estimate_seconds` where WD14 runs (`estimate_device`), else the sentence. */
export function timeText(plan) {
    const seconds = plan?.estimate_seconds;
    if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) return TIME_UNKNOWN;
    return durationText(Math.ceil(seconds / 60)) + (plan.estimate_device === 'gpu' ? TIME_ON_GPU : TIME_ON_CPU);
}

/** D134: the review's rows from the server's preview and the drafts it was made with (image counts, #379). */
export function batchGrid(plan, values) {
    const id = plan.model?.type ?? values.autotag_model_type;
    let settings = CLASSIFIERS.has(id)
        ? `General ${values.autotag_general_threshold} · Character ${values.autotag_character_threshold}`
        : `Template of ${number(characters(values.autotag_template))} characters`;
    // The saved count the run uses (the preview's), not an unsaved draft.
    if (Number.isSafeInteger(plan.suggestion_limit)) settings += ` · up to ${number(plan.suggestion_limit)} tags per image`;
    return [
        ['Model', `${modelLabel(id, plan.model)} · ${modelSize(id, plan.model)}`],
        ['Existing tags', existingLabel(plan.existing ?? values.autotag_existing)],
        ['Images', number(plan.images)],
        ['Already auto-tagged', number(plan.already_tagged)],
        ['Images to tag', `about ${number(plan.to_tag)}`],
        ['Time', timeText(plan)],
        ['Settings', settings],
        ['Backup', backupText(plan.backup)],
    ];
}

const megabytes = bytes => `${(bytes / 2 ** 20).toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} MB`;

/**
 * A disk without room for the batch's safety backup, in the server's insufficient-space wording
 * (`short_space_message`); byte counts that are not numbers are not guessed.
 */
export function diskShortNote(disk) {
    const required = disk?.required_bytes;
    const free = disk?.free_bytes;
    if (!Number.isFinite(required) || !Number.isFinite(free) || required < 0 || free < 0) return DISK_SHORT_UNKNOWN;
    return `Not enough free disk space: the safety backup needs about ${megabytes(required)} and ${megabytes(free)} is free. `
        + 'Nothing is written until there is room.';
}

/** Why the reviewed batch cannot start (the Cancel-only review's sentence), or null. */
export function batchCloseNote(plan) {
    if (plan.review_required === true) return REPLACE_NOTE;
    if (plan.model?.downloaded === false) return notDownloaded(modelLabel(plan.model.type, plan.model));
    if (missingPackages(plan.model).length) return needsPackages(modelLabel(plan.model.type, plan.model), plan.model);
    if (plan.disk?.sufficient === false) return diskShortNote(plan.disk);
    if (!Number.isSafeInteger(plan.images) || plan.images === 0) return NO_IMAGES;
    if (plan.to_tag === 0) return NOTHING_TO_TAG;
    return null;
}

export const BATCH_SENTENCE = 'Nothing is written until you start. The number to tag is an estimate: images changed since '
    + 'they were tagged are tagged again, and missing files are skipped. Activity reports what was tagged.';

/** The Cancel-only review: the counts and why the batch cannot start (CUJ 5), in the shared close-only shell. */
export function openBatchCloseOnly({ scope, trigger, grid, note }) {
    openCloseOnlyReview({ scope, trigger, title: REVIEW_TITLE, heading: BATCH_TITLE, grid, sentence: note, closeLabel: 'Cancel' });
}

/** D134: show the previewed batch; Start tagging runs it as the `autotag` job. */
export function showBatchPlan(plan, { values, activity, scope, trigger = null, notify = showWorkspaceToast, navigate = navigateTo,
    backup = () => downloadDatabaseBackup(), openReview = openReviewDialog, openCloseOnly = openBatchCloseOnly }) {
    const grid = batchGrid(plan, values);
    const note = batchCloseNote(plan);
    if (note) {
        openCloseOnly({ scope, trigger, grid, note });
        return;
    }
    const body = batchBody(values);
    openReview({ scope, trigger, title: REVIEW_TITLE, heading: BATCH_TITLE, grid, sentence: BATCH_SENTENCE,
        backup: plan.backup?.policy === 'off' ? null : { copy: BACKUP_COPY, onBackup: backup }, cancelLabel: 'Cancel',
        primary: { label: START_LABEL },
        onStart: () => startJob({ activity, scope, kind: BATCH_KIND, body, what: BATCH_TITLE, notify, navigate }) });
}

/** Preview the batch for the drafts (read-only) and show it; the pressed button reads "Reviewing…" until it answered. */
export async function reviewBatch({ api, activity, scope, values, trigger = null, notify = showWorkspaceToast, navigate = navigateTo,
    show = showBatchPlan }) {
    await holding(trigger, 'Reviewing…', async () => {
        let plan;
        try {
            plan = await api.post(BATCH_PREVIEW_URL, { model_type: values.autotag_model_type, existing: values.autotag_existing },
                { signal: scope.signal });
        } catch (error) {
            if (scope.signal.aborted) return;
            notify(error?.code === 'busy' ? busyToast(error.running) : reviewRefusal(error));
            return;
        }
        if (!scope.signal.aborted) show(plan, { values, activity, scope, trigger, notify, navigate });
    });
}

/** Mark `button` aria-disabled (with its reason as a hover title) or enabled. */
function setAvailable(button, reason) {
    if (reason) {
        button.setAttribute('aria-disabled', 'true');
        button.dataset.reason = reason;
    } else {
        button.removeAttribute('aria-disabled');
        delete button.dataset.reason;
    }
}

/**
 * Mount Auto-tagging in `container` (System's `.settings-layout`); returns its surface. `drafts` is the System draft
 * store, `activity` the page's Activity store. `selected()` is the image selected in Images (null when none) and
 * `openTagging` opens the Tag image dialog and `show` the previewed batch; all three are injectable for tests.
 */
export function mountTagging(container, { api, activity, scope, drafts, notify = showWorkspaceToast, navigate = navigateTo,
    selected = selectedImage, openTagging = openImageTagging, show = showBatchPlan }) {
    const surface = el('section', undefined, 'surface');
    surface.append(el('h2', 'Auto-tagging'), el('p', INTRO));
    const controls = [];
    const add = (row, id) => {
        const input = row.children[1];
        input.dataset.key = SETTING_KEYS[id];
        controls.push({ id, key: SETTING_KEYS[id], input, text: row.children[0] });
        surface.append(row);
        return row;
    };
    const modelRow = add(settingRow({ id: 'tag-model', label: 'Tagging model', description: STATUS_COPY.loading,
        control: { type: 'select', options: modelOptions(null) } }), 'tag-model');
    add(settingRow({ id: 'tag-existing', label: 'Existing tags', description: 'Skipping remains the default; replace and merge are explicit.',
        control: { type: 'select', options: EXISTING_OPTIONS } }), 'tag-existing');
    add(settingRow({ id: 'threshold-general', label: 'General-tag confidence', description: 'Current WD14 default: 0.35.',
        control: { type: 'number', min: 0, max: 1, step: 0.05 } }), 'threshold-general');
    add(settingRow({ id: 'threshold-character', label: 'Character-tag confidence', description: 'Current WD14 default: 0.85.',
        control: { type: 'number', min: 0, max: 1, step: 0.05 } }), 'threshold-character');
    // D133: the persisted suggestion count, which the concept does not show.
    add(settingRow({ id: 'tag-suggestions', label: 'Suggestions per image', description: SUGGESTIONS_DESCRIPTION,
        control: { type: 'number', min: 1, max: 100, step: 1 } }), 'tag-suggestions');
    // D202 (#431): the words whose suggested tags are hidden on every suggestion path.
    const skipped = skippedTagsBlock({ api, scope, drafts });
    surface.append(skipped.node);

    const field = el('div', undefined, 'field');
    const templateLabel = el('label', 'LLM tag template');
    templateLabel.htmlFor = 'tag-template';
    const template = el('textarea');
    template.id = 'tag-template';
    template.dataset.setting = 'tag-template';
    template.dataset.key = SETTING_KEYS['tag-template'];
    field.append(templateLabel, template);
    surface.append(field);
    controls.push({ id: 'tag-template', key: SETTING_KEYS['tag-template'], input: template, text: null });

    for (const { key, input } of controls) {
        // Text controls draft while typing (the Save row follows at once); selects and numbers on change.
        const event = input.localName === 'textarea' ? 'input' : 'change';
        scope.listen(input, event, () => drafts.set('host', key, draftValue(key, input.value)));
    }

    const actions = el('div', undefined, 'row-actions');
    const preview = createButton('Preview tagging review', 'ca-primary');
    const download = createButton('Download');
    const unload = createButton('Unload');
    actions.append(preview, download, unload);
    surface.append(actions);
    const batch = operationRow({ scope, title: BATCH_TITLE, description: BATCH_DESCRIPTION, label: 'Review',
        onClick: trigger => {
            if (trigger.getAttribute('aria-disabled') === 'true') { notify(trigger.dataset.reason); return; }
            reviewBatch({ api, activity, scope, values: current(), trigger, notify, navigate, show })
                .catch(error => { if (!scope.signal.aborted) notify(reviewRefusal(error)); })
                .finally(() => { if (!scope.signal.aborted) repaint(); });
        } });
    surface.append(batch);
    const review = batch.children[1].children[0];

    let status = { phase: 'loading', models: null, loadedType: null };
    let starting = false;
    // The "could not be read" line under each row, while the server settings could not be read.
    const notes = new Map();
    const image = selected();
    const current = () => Object.fromEntries(Object.values(SETTING_KEYS).map(key => [key, drafts.get('host', key)]));
    const chosen = () => drafts.get('host', 'autotag_model_type');
    const known = () => MODEL_IDS.includes(chosen());

    const repaint = () => {
        skipped.repaint();
        const values = current();
        const unread = values.autotag_model_type === undefined;
        const failed = drafts.hostState?.() === 'failed' && unread;
        for (const { key, input, text } of controls) {
            const value = values[key];
            input.disabled = value === undefined || (key === 'autotag_model_type' && status.phase === 'loading');
            // A focused field keeps what the user is typing; a select always shows its value.
            if (value !== undefined && (document.activeElement !== input || input.localName === 'select')) input.value = String(value);
            if (!text) continue;
            notes.get(key)?.remove();
            notes.delete(key);
            if (failed) {
                const note = el('p', HOST_UNAVAILABLE, 'small');
                notes.set(key, note);
                text.append(note);
            }
        }
        const labels = modelOptions(status.models);
        for (const [index, option] of [...modelRow.children[1].options].entries()) option.textContent = labels[index][1];
        modelRow.children[0].children[1].textContent = unread && status.phase !== 'loading' ? STATUS_COPY.failed
            : modelDescription(status, values.autotag_model_type);

        const unknown = { message: failed ? HOST_UNAVAILABLE : unread ? SETTINGS_UNREAD : UNKNOWN_MODEL };
        const problem = known() ? draftProblem(values) : unknown;
        setAvailable(preview, !image ? NO_SELECTION : problem?.message);
        const entry = status.models?.[values.autotag_model_type];
        // A start in flight keeps Download as it is (aria-disabled), whatever a draft change repaints.
        if (!starting) {
            download.textContent = downloadLabel(values.autotag_model_type, entry);
            if (status.phase === 'loading') setAvailable(download, STATUS_UNREAD);
            else if (!known()) setAvailable(download, unknown.message);
            else if (status.phase !== 'loaded') setAvailable(download, STATUS_COPY.failed);
            else setAvailable(download, null);
        }
        // Unload is offered only while a model is loaded.
        unload.disabled = !(status.phase === 'loaded' && status.loadedType);
        if (review.textContent === 'Reviewing…') return;
        const batchProblem = known() ? draftProblem(values, { limit: false }) : unknown;
        const label = modelLabel(values.autotag_model_type, entry);
        setAvailable(review, entry?.downloaded === false ? notDownloaded(label)
            : needsPackages(label, entry) || batchProblem?.message);
    };

    let reads = 0;
    const readModels = async () => {
        const read = ++reads;
        let next;
        try {
            next = readStatus(await api.get(MODELS_URL, { signal: scope.signal, cache: 'no-store' }));
        } catch {
            next = { phase: 'failed', models: null, loadedType: null };
        }
        if (read !== reads || scope.signal.aborted) return;
        status = next;
        repaint();
    };

    scope.listen(preview, 'click', () => {
        if (preview.getAttribute('aria-disabled') === 'true') { notify(preview.dataset.reason); return; }
        openTagging({ api, scope, image, source: 'tags', trigger: preview, preset: taggingPreset(current()) });
    });
    scope.listen(download, 'click', async () => {
        if (download.getAttribute('aria-disabled') === 'true') { notify(download.dataset.reason); return; }
        const model = chosen();
        starting = true;
        try {
            await holding(download, null, () => startJob({ activity, scope, kind: DOWNLOAD_KIND, body: { model_type: model },
                what: `Tagging model download · ${modelLabel(model, status.models?.[model])}`, notify, navigate }));
        } finally {
            starting = false;
        }
        if (!scope.signal.aborted) repaint();
    });
    scope.listen(unload, 'click', async () => {
        const model = status.loadedType;
        unload.disabled = true;
        try {
            const result = await api.post(UNLOAD_URL, {}, { signal: scope.signal });
            if (scope.signal.aborted) return;
            // Only an answer that unloaded something says so; "No model was loaded" just reads the status again.
            if (result?.model_loaded === false) notify(`${modelLabel(model, status.models?.[model])} unloaded.`);
        } catch (error) {
            if (scope.signal.aborted) return;
            notify(error?.code === 'model-busy' ? modelBusy(error.operation) : UNLOAD_FAILED);
        }
        await readModels();
    });

    scope.cleanup(drafts.subscribe(repaint));
    repaint();
    readModels();
    return [surface];
}
