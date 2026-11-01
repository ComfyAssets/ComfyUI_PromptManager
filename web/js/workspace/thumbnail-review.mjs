/**
 * Thumbnail review (#249 System S5 Task 14): the concept's "Thumbnail maintenance" dialog (`thumbnail-review`, app.js
 * 1433–1437), opened by Sources' "Thumbnail generation → Review options", Overview's "Review thumbnails" and
 * Appearance's "Convert outdated" (#317). It starts the `thumbnails` Activity job (S5 Task 12) with the chosen scope.
 *
 * - Scope: "Missing thumbnails only" (`missing`, the default), "Rebuild all (explicit opt-in)" (`rebuild`) or, #317,
 *   "Convert outdated (removes older formats)" (`convert`): previews in another format than the configured one are
 *   written again from their originals and the old files removed. Nothing converts unless chosen here.
 * - D131: the concept's Quality and Preview size limit fields are gone: previews follow the server's thumbnail
 *   settings (System › Appearance, #317), so a `p.small` line after the plan line names them from the plan's answer
 *   ("Previews are written as WebP · quality 80 · 512 px. …").
 * - D99: a `p.small[role=status]` line before the actions says what the server's plan counts for the chosen scope
 *   (`GET /images/thumbnails/plan?mode=`, read-only): "N previews to generate · M already up to date.", for a rebuild
 *   "Every preview is regenerated: N files.", for a conversion "N previews in an older format to convert.". N counts
 *   the files past the per-batch cap too: the job plans again after each batch (#317). Each scope is read once per dialog. The primary is "Start" (the
 *   concept's "Simulate thumbnail job"): it closes the dialog and starts the job through the Activity store, which
 *   toasts that it started; the job opens in Activity, where its progress lives. A start refused because another job
 *   holds the slot toasts the D57 busy link toast and stays.
 */
import { createButton } from '../shared/toolkit/core.js';
import { closeWorkspaceDialog, field, openContentDialog } from './workspace-dialogs.mjs';
import { busyToast } from './activity-scan.mjs';
import { showWorkspaceToast } from './toast.mjs';
import { navigateTo } from './state.mjs';
import { noun } from './plural.mjs';
import { sizeText } from './system-thumbnail-location.mjs';

export const THUMBNAIL_PLAN_URL = '/prompt_manager/images/thumbnails/plan';
export const THUMBNAILS_KIND = 'thumbnails';
export const SCOPE_OPTIONS = Object.freeze([['missing', 'Missing thumbnails only'], ['rebuild', 'Rebuild all (explicit opt-in)'],
    ['convert', 'Convert outdated (removes older formats)']]);
export const PLAN_LOADING = 'Counting previews…';
export const PLAN_FAILED = 'The previews could not be counted.';
export const START_FAILED = 'Thumbnail generation could not start. Try again.';
export const SETTINGS_UNKNOWN = 'Previews follow the thumbnail settings in System › Appearance.';
const FORMAT_LABELS = Object.freeze({ webp: 'WebP', jxl: 'JPEG XL', jpeg: 'JPEG' });
// #320 D200: offered only while previews are kept in one folder (31A).
export const PRUNE_OPTION = Object.freeze(['prune', 'Remove thumbnails of removed sources']);
const MODES = new Set([...SCOPE_OPTIONS.map(([value]) => value), PRUNE_OPTION[0]]);

const count = value => value.toLocaleString('en-US');
const isCount = value => Number.isSafeInteger(value) && value >= 0;

/** D131: the server's thumbnail settings from the plan's `{format, quality, size}`. */
export function settingsText(plan) {
    const label = FORMAT_LABELS[plan?.format];
    if (!label || !isCount(plan.quality) || !isCount(plan.size)) return SETTINGS_UNKNOWN;
    return `Previews are written as ${label} · quality ${plan.quality} · ${count(plan.size)} px. Change this in System › Appearance.`;
}

/** D99: the plan line for the server's `{mode, targets, fresh, remaining}`; an answer without counts failed. */
export function planText(plan) {
    if (plan?.mode === 'prune') return typeof plan.line === 'string' && plan.line ? plan.line : PLAN_FAILED;
    if (!plan || !MODES.has(plan.mode) || ![plan.targets, plan.fresh, plan.remaining].every(isCount)) {
        return PLAN_FAILED;
    }
    // #317: the cap bounds one batch, not the job, which plans again until nothing is left: the line counts it all.
    const work = plan.targets + plan.remaining;
    if (plan.mode === 'rebuild') return `Every preview is regenerated: ${count(work)} ${noun(work, 'file')}.`;
    if (plan.mode === 'convert') {
        if (!work) return 'No previews in an older format.';
        return `${count(work)} ${noun(work, 'preview')} in an older format to convert.`;
    }
    return `${count(work)} ${noun(work, 'preview')} to generate · ${count(plan.fresh)} already up to date.`;
}

/**
 * Start the thumbnail job with `options` (`{mode}`) and open its view; a refused or failed start says
 * why and stays. Nothing is said once the section that started it is gone.
 */
export async function startThumbnails({ store, scope, options, notify = showWorkspaceToast, navigate = navigateTo }) {
    try {
        const result = await store.start(THUMBNAILS_KIND, options);
        if (scope.signal.aborted) return;
        if (result.busy !== undefined) {
            notify(busyToast(result.busy));
            return;
        }
        navigate(`#/activity/${result.job.id}`);
    } catch {
        if (!scope.signal.aborted) notify(START_FAILED);
    }
}

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

function select(options, value) {
    const node = el('select');
    node.append(...options.map(([optionValue, label]) => {
        const option = el('option', label);
        option.value = optionValue;
        return option;
    }));
    node.value = value;
    return node;
}

/**
 * Open "Thumbnail maintenance" on the section's `scope`, on scope `mode` (`missing` unless given). `store` is the page's
 * Activity store; `trigger` gets focus back when the dialog closes.
 */
export function openThumbnailReview({ api, store, scope, trigger = null, mode: initial = 'missing', notify = showWorkspaceToast,
    navigate = navigateTo, location = null }) {
    const folderMode = location?.mode === 'folder';
    const options = folderMode ? [...SCOPE_OPTIONS, PRUNE_OPTION] : SCOPE_OPTIONS;
    const allowed = new Set(options.map(([value]) => value));
    const mode = select(options, allowed.has(initial) ? initial : 'missing');
    const line = el('p', PLAN_LOADING, 'small');
    line.setAttribute('role', 'status');
    const settings = el('p', SETTINGS_UNKNOWN, 'small');
    // D200: what the prune removes, under the plan line.
    const pruneDetail = el('p', '', 'small');
    const pruneGrid = el('dl', undefined, 'meta-grid');
    pruneDetail.hidden = pruneGrid.hidden = true;
    const cancel = createButton('Cancel');
    const start = createButton('Start', 'ca-primary');
    const actions = el('div', undefined, 'dialog-actions');
    actions.append(cancel, start);

    // The plan per scope: a promise of its answer (null when it failed).
    const plans = new Map();
    let open = true;
    const readPlan = chosen => api.get(`${THUMBNAIL_PLAN_URL}?${new URLSearchParams({ mode: chosen })}`,
        { signal: scope.signal, cache: 'no-store' }).catch(() => null);
    async function paint() {
        const chosen = mode.value;
        if (!plans.has(chosen)) plans.set(chosen, readPlan(chosen));
        const pending = plans.get(chosen);
        line.textContent = PLAN_LOADING;
        const plan = await pending;
        // A later choice painted its own line meanwhile, or the dialog closed.
        if (!open || scope.signal.aborted || mode.value !== chosen) return;
        line.textContent = planText(plan);
        const prune = chosen === 'prune';
        settings.hidden = prune;
        pruneDetail.hidden = pruneGrid.hidden = !prune || !plan?.sources?.length;
        if (prune) {
            pruneDetail.textContent = `Only Prompt Manager’s thumbnails in their folders inside ${plan?.folder ?? 'the thumbnail '
                + 'folder'} are removed. If a source comes back, its thumbnails are made again as you browse.`;
            pruneGrid.replaceChildren(...(plan?.sources ?? []).map(source => {
                const entry = el('div');
                entry.append(el('dt', source.key), el('dd', `${count(source.files)} ${noun(source.files, 'thumbnail')} · `
                    + `${sizeText(source.bytes)}`));
                return entry;
            }));
        } else {
            settings.textContent = settingsText(plan);
        }
    }

    scope.listen(mode, 'change', paint);
    scope.listen(cancel, 'click', () => closeWorkspaceDialog());
    scope.listen(start, 'click', () => {
        const options = { mode: mode.value };
        closeWorkspaceDialog();
        startThumbnails({ store, scope, options, notify, navigate });
    });
    openContentDialog({ scope, title: 'Thumbnail maintenance', trigger,
        nodes: [field('Scope', mode, 'thumb-mode'), line, settings, ...(folderMode ? [pruneDetail, pruneGrid] : []), actions],
        onClose: () => { open = false; } });
    paint();
}
