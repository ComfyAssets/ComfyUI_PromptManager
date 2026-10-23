/**
 * Thumbnail review (#249 System S5 Task 14): the concept's "Thumbnail maintenance" dialog (`thumbnail-review`, app.js
 * 1433–1437), opened by Sources' "Thumbnail generation → Review options" and Overview's "Review thumbnails". It starts
 * the `thumbnails` Activity job (S5 Task 12) with the chosen options.
 *
 * - Scope: "Missing thumbnails only" (`missing`, the default) or "Rebuild all (explicit opt-in)" (`rebuild`).
 * - Quality: "Medium · optimized" / "High · original" / "Low · fastest" (`medium` / `high` / `low`), preselected from
 *   Appearance's Image loading quality (`gallerySettings.imageQuality`, the same three values; Q3).
 * - Preview size limit: the longest edge, a whole number from 128 to 2048 (concept default 512). A size outside that
 *   marks the field `aria-invalid`, says so in the plan line and sends nothing; Start reports the field's validity.
 * - D99: a `p.small[role=status]` line before the actions says what the server's plan counts for the chosen scope
 *   (`GET /images/thumbnails/plan`, read-only): "N previews to generate · M already up to date." or, for a rebuild,
 *   "Every preview is regenerated: N files.", plus "· R more on the next run" past the per-run cap. Each scope is read
 *   once per dialog (the size and quality change no count). The primary is "Start" (the concept's "Simulate thumbnail
 *   job"): it closes the dialog and starts the job through the Activity store, which toasts that it started; the job
 *   opens in Activity, where its progress lives. A start refused because another job holds the slot toasts the D57
 *   busy link toast and stays.
 */
import { createButton } from '../shared/toolkit/core.js';
import { closeWorkspaceDialog, field, openContentDialog } from './workspace-dialogs.mjs';
import { busyToast } from './activity-scan.mjs';
import { readPreferences } from './preferences.mjs';
import { showWorkspaceToast } from './toast.mjs';
import { navigateTo } from './state.mjs';
import { noun } from './plural.mjs';

export const THUMBNAIL_PLAN_URL = '/prompt_manager/images/thumbnails/plan';
export const THUMBNAILS_KIND = 'thumbnails';
export const SCOPE_OPTIONS = Object.freeze([['missing', 'Missing thumbnails only'], ['rebuild', 'Rebuild all (explicit opt-in)']]);
export const QUALITY_OPTIONS = Object.freeze([['medium', 'Medium · optimized'], ['high', 'High · original'], ['low', 'Low · fastest']]);
export const SIZE_LIMIT = Object.freeze({ min: 128, max: 2048, value: 512 });
export const PLAN_LOADING = 'Counting previews…';
export const PLAN_FAILED = 'The previews could not be counted.';
export const SIZE_INVALID = `Enter a size from ${SIZE_LIMIT.min} to ${SIZE_LIMIT.max} pixels.`;
export const START_FAILED = 'Thumbnail generation could not start. Try again.';

const QUALITIES = new Set(QUALITY_OPTIONS.map(([value]) => value));
const count = value => value.toLocaleString('en-US');
const isCount = value => Number.isSafeInteger(value) && value >= 0;

/** The preview size for the field's text: a whole number of pixels in range, else null. */
export function previewSize(text) {
    if (typeof text !== 'string' || !/^\d{1,5}$/.test(text)) return null;
    const size = Number(text);
    return size >= SIZE_LIMIT.min && size <= SIZE_LIMIT.max ? size : null;
}

/** Q3: Image loading quality preselects the dialog's quality; anything it does not know is Medium. */
export function preselectedQuality(imageQuality) {
    return QUALITIES.has(imageQuality) ? imageQuality : 'medium';
}

/** D99: the plan line for the server's `{mode, targets, fresh, remaining}`; an answer without counts failed. */
export function planText(plan) {
    if (!plan || !['missing', 'rebuild'].includes(plan.mode) || ![plan.targets, plan.fresh, plan.remaining].every(isCount)) {
        return PLAN_FAILED;
    }
    const more = plan.remaining ? ` · ${count(plan.remaining)} more on the next run` : '';
    if (plan.mode === 'rebuild') return `Every preview is regenerated: ${count(plan.targets)} ${noun(plan.targets, 'file')}${more}.`;
    return `${count(plan.targets)} ${noun(plan.targets, 'preview')} to generate · ${count(plan.fresh)} already up to date${more}.`;
}

/**
 * Start the thumbnail job with `options` (`{mode, size, quality}`) and open its view; a refused or failed start says
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

const defaultQuality = () => {
    try {
        return readPreferences().values.imageQuality;
    } catch {
        return undefined;
    }
};

/**
 * Open "Thumbnail maintenance" on the section's `scope`. `store` is the page's Activity store; `trigger` gets focus
 * back when the dialog closes.
 */
export function openThumbnailReview({ api, store, scope, trigger = null, quality = defaultQuality(), notify = showWorkspaceToast,
    navigate = navigateTo }) {
    const mode = select(SCOPE_OPTIONS, 'missing');
    const encoder = select(QUALITY_OPTIONS, preselectedQuality(quality));
    const size = el('input');
    size.type = 'number';
    size.value = String(SIZE_LIMIT.value);
    size.min = String(SIZE_LIMIT.min);
    size.max = String(SIZE_LIMIT.max);
    const line = el('p', PLAN_LOADING, 'small');
    line.setAttribute('role', 'status');
    const cancel = createButton('Cancel');
    const start = createButton('Start', 'ca-primary');
    const actions = el('div', undefined, 'dialog-actions');
    actions.append(cancel, start);

    // The plan per scope: a promise of its answer (null when it failed); the size and quality change no count.
    const plans = new Map();
    let open = true;
    const chosenSize = () => previewSize(size.value);
    const validate = () => {
        const valid = chosenSize() !== null;
        size.setCustomValidity(valid ? '' : SIZE_INVALID);
        if (valid) size.removeAttribute('aria-invalid');
        else size.setAttribute('aria-invalid', 'true');
        return valid;
    };
    const readPlan = chosen => {
        const query = new URLSearchParams({ mode: chosen, size: String(chosenSize()), quality: encoder.value });
        return api.get(`${THUMBNAIL_PLAN_URL}?${query}`, { signal: scope.signal, cache: 'no-store' }).catch(() => null);
    };
    async function paint() {
        if (!validate()) { line.textContent = SIZE_INVALID; return; }
        const chosen = mode.value;
        if (!plans.has(chosen)) plans.set(chosen, readPlan(chosen));
        const pending = plans.get(chosen);
        line.textContent = PLAN_LOADING;
        const plan = await pending;
        // A later choice painted its own line meanwhile, or the dialog closed.
        if (!open || scope.signal.aborted || mode.value !== chosen || !validate()) return;
        line.textContent = planText(plan);
    }

    scope.listen(mode, 'change', paint);
    scope.listen(size, 'input', paint);
    scope.listen(cancel, 'click', () => closeWorkspaceDialog());
    scope.listen(start, 'click', () => {
        if (!validate()) {
            size.reportValidity();
            size.focus();
            return;
        }
        const options = { mode: mode.value, size: chosenSize(), quality: encoder.value };
        closeWorkspaceDialog();
        startThumbnails({ store, scope, options, notify, navigate });
    });
    openContentDialog({ scope, title: 'Thumbnail maintenance', trigger,
        nodes: [field('Scope', mode, 'thumb-mode'), field('Quality', encoder, 'thumb-quality'),
            field('Preview size limit', size, 'thumb-limit'), line, actions],
        onClose: () => { open = false; } });
    paint();
}
