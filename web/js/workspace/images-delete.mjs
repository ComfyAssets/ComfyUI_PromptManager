/**
 * Images › Delete… (#480, D159): the review dialog for the checked images (or "all N matching", or the one image of
 * the inspector or viewer) and the apply.
 *
 * - The concept's selection bar has "Remove links…" (`danger`) opening its `maintenancePlan` review (`dl.meta-grid`
 *   Scope / Protected / Backup / Media on disk, Cancel and a primary). Production names it "Delete…" and asks each
 *   time (maintainer ruling): a radio group "What should happen to the files?" with nothing checked, "Remove from the
 *   library only" (records go, the file stays on disk, hidden from Images) or "Also move the files to the trash".
 * - One preview (`POST /images/delete/preview`) answers both modes; choosing a mode only redraws the rows. The primary
 *   (`danger`) is enabled once a mode with images to act on is chosen. Apply sends `{token, mode}` to the reviewed-plan
 *   apply; the job is followed in the Activity store and `onDeleted(job)` runs when it ends.
 * - Nothing here deletes a file: the server never unlinks one (it renames into the system trash or leaves it).
 */
import { createButton } from '../shared/toolkit/core.js';
import { closeWorkspaceDialog, openCloseOnlyReview, openContentDialog } from './workspace-dialogs.mjs';
import { followJobEnd } from './images-tagging-bulk.mjs';
import { applyRefusal, APPLY_URL } from './maintenance-review.mjs';
import { showWorkspaceToast } from './toast.mjs';
import { countOf, noun } from './plural.mjs';

export const PREVIEW_URL = '/prompt_manager/images/delete/preview';
export const MAX_DELETE = 500;
export const TOO_MANY = 'Select at most 500 images.';
export const QUESTION = 'What should happen to the files?';
export const MODES = Object.freeze([
    Object.freeze({value: 'library', label: 'Remove from the library only', hint: 'Files stay on disk, hidden from Images.'}),
    Object.freeze({value: 'trash', label: 'Also move the files to the trash',
        hint: 'You can restore them from the trash until it is emptied.'}),
]);
export const NONE_DELETABLE = 'None of the selected images can be deleted.';
export const REVIEW_FAILED = 'The review failed. Nothing was changed.';
export const SOURCE_CHANGED = 'The sources changed. Refresh the images and try again.';
export const STORAGE_CHANGED = 'Prompt storage was restored. Reopen Delete.';
export const USE_LIBRARY_ONLY = 'To remove these files from the library anyway, choose “Remove from the library only”.';
export const NO_TRASH_HERE = 'Moving to the trash is not available for these files; each one lists why.';
const SENTENCES = Object.freeze({
    libraryBackup: 'Removed images stay hidden unless the file is modified or replaced. Their tags, category, collections '
        + 'and favorite can only be recovered by restoring the safety backup, which also undoes every later change.',
    libraryNoBackup: 'Removed images stay hidden unless the file is modified or replaced. Their tags, category, '
        + 'collections and favorite cannot be recovered.',
    trash: 'A file restored from the trash shows again without its tags, category, collections or favorite; a scan '
        + 'links it to its prompt again.',
});
export { SENTENCES };

const RECORD_WORDS = [
    ['image_tags', 'with image tags', 'with image tags'],
    ['auto_tagged', 'auto-tagged', 'auto-tagged'],
    ['categories', 'category', 'categories'],
    ['collections', 'collection membership', 'collection memberships'],
    ['favorites', 'favorite', 'favorites'],
    ['review_items', 'Needs review item', 'Needs review items'],
];

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

function number(value) {
    return Number(value ?? 0).toLocaleString('en-US');
}

/** The images a mode acts on. */
export function modeCount(plan, mode) {
    return Number(plan?.[mode]?.count ?? 0);
}

/** "Name, name, name and K more" for up to three refused rows, grouped by their sentence. */
export function refusedValue(summary) {
    const count = Number(summary?.count ?? 0);
    if (!count) return null;
    const rows = Array.isArray(summary.rows) ? summary.rows : [];
    const names = rows.slice(0, 3).map(row => String(row.path).split('/').at(-1));
    const more = count - names.length;
    const reasons = [...new Set(rows.map(row => row.detail).filter(Boolean))];
    return `${number(count)}: ${names.join(', ')}${more > 0 ? ` and ${number(more)} more` : ''}`
        + (reasons.length ? `. ${reasons.join(' ')}` : '');
}

/** The `dl.meta-grid` rows for `mode` (design §9.3). */
export function deleteRows(plan, mode) {
    const rows = [['Scope', countOf(modeCount(plan, mode), 'image')]];
    const refused = {count: 0, rows: []};
    for (const part of mode === 'trash' ? [plan.refused, plan.trash_refused] : [plan.refused]) {
        refused.count += Number(part?.count ?? 0);
        refused.rows.push(...(part?.rows ?? []));
    }
    const notChanged = refusedValue(refused);
    if (notChanged) rows.push(['Not changed', notChanged]);
    const records = plan.records ?? {};
    const links = Number(records.prompt_links ?? 0);
    if (links) {
        const orphans = plan.orphaned_prompts ?? {count: 0, titles: []};
        let text = `${countOf(links, 'link')}.`;
        if (orphans.count > 0) {
            const titles = (orphans.titles ?? []).filter(Boolean);
            const more = orphans.count - titles.length;
            text += ` ${countOf(orphans.count, 'prompt')} ${orphans.count === 1 ? 'loses its' : 'lose their'} last image`
                + (titles.length ? `: ${titles.join(', ')}${more > 0 ? ` and ${number(more)} more` : ''}` : '')
                + `. The ${noun(orphans.count, 'prompt')} ${orphans.count === 1 ? 'stays' : 'stay'} on the Library page.`;
        }
        rows.push(['Prompt links', text]);
    }
    const details = RECORD_WORDS.filter(([key]) => Number(records[key] ?? 0) > 0)
        .map(([key, one, many]) => `${number(records[key])} ${Number(records[key]) === 1 ? one : many}`);
    if (details.length) rows.push(['Image details', details.join(', ')]);
    rows.push(['Backup', backupOn(plan) ? 'Create and verify before changes (the database, not the files)'
        : 'Off: no safety backup (System › Backup & export › Recovery policy)']);
    rows.push(['Media on disk', mode === 'trash'
        ? 'Files move to the trash. Nothing is deleted permanently. Disk space is freed when you empty the trash.'
        : 'Files stay on disk, hidden from Images.']);
    return rows;
}

function backupOn(plan) {
    return plan?.backup?.policy !== 'off';
}

/** The `p.small` sentences for `mode`. */
export function deleteSentences(plan, mode) {
    if (mode === 'library') return [backupOn(plan) ? SENTENCES.libraryBackup : SENTENCES.libraryNoBackup];
    const sentences = [SENTENCES.trash];
    const trashRefused = Number(plan?.trash_refused?.count ?? 0);
    if (trashRefused && trashRefused >= modeCount(plan, 'library')) sentences.push(NO_TRASH_HERE);
    if (trashRefused) sentences.push(USE_LIBRARY_ONLY);
    return sentences;
}

/** The primary's label: "Remove N images from the library" or "Move N images to the trash". */
export function primaryLabel(mode, count) {
    return mode === 'trash' ? `Move ${countOf(count, 'image')} to the trash` : `Remove ${countOf(count, 'image')} from the library`;
}

/** Why the review cannot start at all, or null. */
export function closeOnlyReason(plan) {
    if (modeCount(plan, 'library') === 0 && modeCount(plan, 'trash') === 0) return NONE_DELETABLE;
    if (plan?.disk && plan.disk.sufficient === false) {
        return `There is not enough free space for the safety backup (needs ${number(plan.disk.required_bytes)} bytes, `
            + `has ${number(plan.disk.free_bytes)}).`;
    }
    return null;
}

/** The toast for a review that could not be read. */
export function reviewFailure(error) {
    if (error?.code === 'source-changed') return SOURCE_CHANGED;
    if (error?.code === 'storage-changed') return STORAGE_CHANGED;
    if (error?.code === 'too-many-images') return TOO_MANY;
    if (error?.code === 'busy' && error.message) return error.message;
    return REVIEW_FAILED;
}

/** The toast when the job ends (design §9.4). */
export function outcomeToast(job) {
    const counters = job?.counters ?? {};
    const mode = job?.scope?.mode;
    if (job?.scope?.failure === 'backup-failed') return 'The safety backup failed; nothing was changed.';
    if (job?.scope?.failure === 'sources-changed') return 'The sources changed; nothing was changed.';
    const done = Number(mode === 'trash' ? counters.trashed : counters.removed) || 0;
    const total = Number(job?.total ?? 0);
    if (job?.scope?.failure === 'records-failed') {
        return `Stopped after ${number(done)} of ${countOf(total, 'image')}; the rest were not changed. See Activity.`;
    }
    const head = mode === 'trash' ? `Moved ${countOf(done, 'image')} to the trash.` : `Removed ${countOf(done, 'image')} from the library.`;
    const unchanged = Number(counters.not_changed ?? 0);
    return unchanged ? `${head} ${number(unchanged)} ${unchanged === 1 ? 'was' : 'were'} not changed (see Activity).` : head;
}

function modeGroup(onChange) {
    const group = el('div', undefined, 'field');
    group.setAttribute('role', 'radiogroup');
    const caption = el('span', QUESTION);
    caption.id = 'image-delete-mode-label';
    group.setAttribute('aria-labelledby', caption.id);
    group.append(caption);
    for (const choice of MODES) {
        const label = el('label');
        const input = el('input');
        Object.assign(input, {type: 'radio', name: 'image-delete-mode', value: choice.value, checked: false});
        input.addEventListener('change', () => { if (input.checked) onChange(choice.value); });
        label.append(input, ` ${choice.label}`, el('span', ` ${choice.hint}`, 'small muted'));
        group.append(label);
    }
    return group;
}

/** The review for an answered preview `plan`; `onApply(mode)` runs on the primary. */
export function showDeleteReview(plan, {scope, count, trigger = null, onApply}) {
    const title = `Delete ${countOf(count, 'image')}`;
    const reason = closeOnlyReason(plan);
    if (reason) {
        openCloseOnlyReview({scope, title, grid: deleteRows(plan, 'library'), sentence: reason, trigger});
        return null;
    }
    let mode = null, started = false;
    const list = el('dl', undefined, 'meta-grid');
    list.hidden = true;
    const notes = el('div', undefined, 'image-delete-notes');
    const cancel = createButton('Cancel');
    const start = createButton('Choose what happens to the files', 'danger image-delete-start');
    start.setAttribute('aria-disabled', 'true');
    function render() {
        list.replaceChildren();
        notes.replaceChildren();
        if (!mode) return;
        for (const [term, value] of deleteRows(plan, mode)) {
            const entry = el('div');
            entry.append(el('dt', term), el('dd', value));
            list.append(entry);
        }
        list.hidden = false;
        for (const sentence of deleteSentences(plan, mode)) notes.append(el('p', sentence, 'small'));
        const acting = modeCount(plan, mode);
        start.textContent = primaryLabel(mode, acting);
        start.setAttribute('aria-disabled', String(acting === 0));
    }
    const group = modeGroup(value => { mode = value; render(); });
    cancel.addEventListener('click', () => closeWorkspaceDialog());
    // Nothing starts without an explicit mode: an aria-disabled primary ignores every activation (click, Enter, Space),
    // and a chosen one starts once however often it is pressed. The dialog has no form, so Enter elsewhere submits nothing.
    start.addEventListener('click', () => {
        if (started || !MODES.some(choice => choice.value === mode) || start.getAttribute('aria-disabled') === 'true') return;
        started = true;
        start.setAttribute('aria-disabled', 'true');
        const chosen = mode;
        closeWorkspaceDialog();
        onApply(chosen);
    });
    const actions = el('div', undefined, 'dialog-actions');
    actions.append(cancel, start);
    openContentDialog({scope, title, nodes: [group, list, notes, actions], trigger});
    return {group, list, start};
}

/** Apply `plan` in `mode`; the started job, or null (a toast said why). */
export async function applyDelete({api, store, plan, mode, notify = showWorkspaceToast}) {
    if (!MODES.some(choice => choice.value === mode)) throw new TypeError('A delete needs an explicit mode');
    let result;
    try {
        result = await api.post(APPLY_URL, {token: plan.token, mode});
    } catch (error) {
        const refusal = applyRefusal(error);
        notify(refusal.toast);
        if (refusal.lost) store?.load?.();
        return null;
    }
    try {
        const job = store ? store.track(result?.job) : result?.job;
        notify(`${job.title} started.`);
        return job;
    } catch {
        store?.load?.();
        return null;
    }
}

/**
 * The whole flow for `images` (the checked ones) or `filter` ("all N matching", `count` of them): the preview, the
 * review and the apply. `trigger` reads "Reviewing…" and is aria-disabled until the review opens; a second press is
 * ignored. `onDeleted(job)` runs when the started job ends.
 */
export async function openDeleteImages({api, scope, store, images = [], filter = null, count = images.length, trigger = null,
    onDeleted = () => {}, notify = showWorkspaceToast}) {
    if (count > MAX_DELETE) { notify(TOO_MANY); return; }
    if (!filter && !images.length) return;
    if (trigger?.getAttribute('aria-disabled') === 'true') return;
    const label = trigger?.textContent;
    trigger?.setAttribute('aria-disabled', 'true');
    if (trigger) trigger.textContent = 'Reviewing…';
    let plan;
    try {
        const body = filter ? {filter} : {items: images.map(({root_index, root_scope, path}) => ({root_index, root_scope, path}))};
        plan = await api.post(PREVIEW_URL, body, {signal: scope.signal});
    } catch (error) {
        if (!scope.signal.aborted) notify(reviewFailure(error));
        return;
    } finally {
        if (trigger) { trigger.textContent = label; trigger.removeAttribute('aria-disabled'); }
    }
    if (scope.signal.aborted) return;
    showDeleteReview(plan, {scope, count, trigger, onApply: async mode => {
        const job = await applyDelete({api, store, plan, mode, notify});
        if (!job) return;
        if (store) followJobEnd({store, scope, id: job.id, onEnd: ended => { notify(outcomeToast(ended)); onDeleted(ended); }});
        else onDeleted(job);
    }});
}
