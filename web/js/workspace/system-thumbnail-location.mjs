/**
 * Thumbnail location (#320, beyond the concept: D196–D199, approved static mock `320-mock`): where previews are kept.
 *
 * - D196 `locationBlock`: a `.setting-block` in System › Sources & scans after "Thumbnail generation": the radios
 *   "Inside each source" / "One folder", a "Folder path" field (enabled with One folder, prefilled with the folder in
 *   use or the server's `default_path`), the status line and "Review move…" (enabled when the choice differs from the
 *   location in use). Not a host draft: the Save row never saves it; the Move thumbnails job is the only writer (D4B).
 *   A refusal of the review shows in place under the field (`aria-invalid`), in the server's words.
 * - D197 `openMoveReview`: the shared review (Move library's shape) over the server's plan
 *   (`POST /images/thumbnails/plan`): From, To, Thumbnails, Free space, Too long for Windows, Removed after the move,
 *   Left in place, Links skipped; a folder that is not the default asks for the confirmation checkbox first. "Move
 *   thumbnails" starts the job (`POST /jobs/thumbnails {mode: "move", token, confirmed}`) and opens it in Activity.
 * - D199 `locationNotice`: while the thumbnail folder is unavailable, a `.notice` on Overview and Sources & scans
 *   says why, with "Recreate folder" (a missing folder, after a confirmation) or "Use this folder" (another folder's
 *   marker), and "Change location".
 *
 * The location comes from GET /settings' `thumbnail_location` (`{mode, path, default_path, available, problem,
 * cleanup_pending}`), read with the host settings.
 */
import { createButton } from '../shared/toolkit/core.js';
import { closeWorkspaceDialog, openContentDialog, openReviewDialog } from './workspace-dialogs.mjs';
import { busyToast } from './activity-scan.mjs';
import { showWorkspaceToast } from './toast.mjs';
import { navigateTo } from './state.mjs';
import { noun } from './plural.mjs';

export const PLAN_URL = '/prompt_manager/images/thumbnails/plan';
export const RECREATE_URL = '/prompt_manager/images/thumbnails/location/recreate';
export const ADOPT_URL = '/prompt_manager/images/thumbnails/location/adopt';
export const LOCATION_HINT = 'Where previews are kept. Originals are never moved. Avoid cloud-synced folders.';
export const SENTENCE = 'Every thumbnail is copied and checked before Prompt Manager switches. Scans, deletes and '
    + 'tagging wait until it finishes; closing this page does not stop it. Originals are never touched.';
export const REVIEW_FAILED = 'The move could not be reviewed. Try again.';
export const START_FAILED = 'Move thumbnails could not start. Try again.';
export const NOTICE_TITLE = 'The thumbnail folder is unavailable';
const BACK = 'Originals are shown and no thumbnails are written until it is back.';
const CHECK = 'If its drive is unplugged, connect it; Prompt Manager checks again every few seconds.';

const number = value => Number(value || 0).toLocaleString('en-US');
const isFolder = location => location?.mode === 'folder';

/** "1.2 GB" (binary units, one decimal, as the server writes them). */
export function sizeText(bytes) {
    let size = Number(bytes) || 0;
    for (const unit of ['B', 'KB', 'MB', 'GB']) {
        if (size < 1024 || unit === 'GB') return unit === 'B' ? `${size} B` : `${size.toFixed(1)} ${unit}`;
        size /= 1024;
    }
    return `${size.toFixed(1)} GB`;
}

export const SUGGESTED = 'The suggested folder is next to your library; Review move… shows what happens before anything '
    + 'is copied.';

/** The status line under the row; `choice` (`{mode, path}`) adds the suggestion while the default folder is chosen. */
export function statusText(location, choice = null) {
    if (!location) return '';
    if (!isFolder(location) && choice?.mode === 'folder' && location.default_path
        && choice.path?.trim() === location.default_path) {
        return `Now: inside each source. ${SUGGESTED}`;
    }
    let text = isFolder(location) ? `Now: one folder, ${location.path}` : 'Now: inside each source';
    if (isFolder(location) && location.available === false) return `${text} · unavailable. Originals are shown.`;
    text += '.';
    if (isFolder(location) && location.path && location.path === location.default_path) {
        text += ' Using the default folder next to your library.';
    }
    if (location.cleanup_pending > 0) {
        text += ` Old thumbnails in ${number(location.cleanup_pending)} ${noun(location.cleanup_pending, 'place')} wait to `
            + 'be removed until it is connected.';
    }
    return text;
}

/** Whether the row's choice (`{mode, path}`) differs from the location in use (Review move… enabled). */
export function choiceChanged(location, choice) {
    if (!location) return false;
    if (choice.mode !== 'folder') return isFolder(location) || Boolean(location.problem);
    const path = (choice.path || '').trim();
    return Boolean(path) && (!isFolder(location) || path !== location.path);
}

/** The D197 review rows (`[term, value]`, a value may be `[lead, items]`) for the server's plan. */
export function reviewRows(plan) {
    const totals = plan.totals ?? {};
    const sources = totals.sources ?? plan.sources?.length ?? 0;
    const inside = `Inside each source (${number(sources)} ${noun(sources, 'source')})`;
    const from = plan.from?.mode === 'folder' ? plan.from.path : inside;
    let to = inside;
    if (plan.to?.mode === 'folder') {
        const state = plan.to.default ? (plan.to.will_create ? 'new, next to your library' : 'next to your library')
            : (plan.to.will_create ? 'new folder' : 'empty folder');
        to = `${plan.to.path} (${state})`;
    }
    const rows = [['From', from], ['To', to],
        ['Thumbnails', `${number(totals.files)} in ${number(sources)} ${noun(sources, 'source')} · ${sizeText(totals.bytes)}`]];
    if (Number.isFinite(plan.free_bytes)) rows.push(['Free space', `${sizeText(plan.free_bytes)} free`]);
    if (totals.too_long > 0) {
        const more = totals.too_long - (plan.too_long_listed?.length ?? 0);
        rows.push(['Too long for Windows', [`${number(totals.too_long)} ${noun(totals.too_long, 'thumbnail')} cannot be `
            + 'stored there. They show the original instead; choose a shorter folder to keep them.',
        [...(plan.too_long_listed ?? []), ...(more > 0 ? [`and ${number(more)} more`] : [])]]]);
    }
    const removed = [];
    if (totals.orphans > 0) removed.push(`${number(totals.orphans)} ${noun(totals.orphans, 'thumbnail')} whose original is gone`);
    if (totals.temps > 0) removed.push(`${number(totals.temps)} unfinished temporary ${noun(totals.temps, 'file')}`);
    for (const source of plan.unlisted ?? []) {
        removed.push(`1 source no longer listed: ${number(source.files)} ${noun(source.files, 'thumbnail')} are removed; `
            + 'they are made again if it returns');
    }
    const oldCopies = plan.from?.mode === 'folder'
        ? 'The thumbnail folders of these sources inside it. The folder itself is removed once empty if Prompt Manager created it.'
        : 'The old copies in each source’s thumbnails folder';
    rows.push(['Removed after the move', [removed.length ? `${oldCopies}, and:` : `${oldCopies}.`, removed]]);
    if (totals.left_in_place > 0) {
        rows.push(['Left in place', [`${number(totals.left_in_place)} ${noun(totals.left_in_place, 'file')} that `
            + `${totals.left_in_place === 1 ? 'is' : 'are'} not ${totals.left_in_place === 1 ? 'a thumbnail' : 'thumbnails'}:`,
        plan.left_listed ?? []]]);
    }
    if (totals.links > 0) {
        rows.push(['Links skipped', `${number(totals.links)} ${noun(totals.links, 'link')} inside a thumbnails folder `
            + `${totals.links === 1 ? 'is' : 'are'} not followed`]);
    }
    return rows;
}

/** The notice text for an unavailable `location` (D199). */
export function noticeText(location) {
    const where = location?.path || 'The thumbnail folder';
    switch (location?.problem) {
    case 'missing': return `${where} is missing. ${BACK} ${CHECK}`;
    case 'unreachable': return `${where} can’t be reached. ${BACK} ${CHECK}`;
    case 'marker-mismatch': return `${where} holds another thumbnail folder. ${BACK} Use it if it is yours, or change `
        + 'the location.';
    case 'invalid': return `The thumbnail location in config.json can’t be used. ${BACK} Change the location.`;
    default: return `${where} is being checked. ${BACK}`;
    }
}

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

function radio(value, label, hint) {
    const node = el('label');
    const input = el('input');
    input.type = 'radio';
    input.name = 'thumb-location';
    input.value = value;
    input.id = `loc-${value}`;
    node.append(input, ` ${label} `, el('span', hint, 'small'));
    return { node, input };
}

/** Start the move with the plan's `token`; opens it in Activity, or says why not. */
export async function startMove({ store, scope, token, confirmed, notify = showWorkspaceToast, navigate = navigateTo,
    onStale = null }) {
    try {
        const result = await store.start('thumbnails', { mode: 'move', token, confirmed });
        if (scope.signal.aborted) return null;
        if (result.busy !== undefined) {
            notify(busyToast(result.busy));
            return null;
        }
        navigate(`#/activity/${result.job.id}`);
        return result.job;
    } catch (error) {
        if (scope.signal.aborted) return null;
        // The sources or the location changed since the review: review again with what is there now.
        if (error?.code === 'plan-stale' && onStale) {
            onStale();
            return null;
        }
        notify(typeof error?.code === 'string' && error.message ? error.message : START_FAILED);
        return null;
    }
}

/** Open the D197 review over `plan`; a plan the server refused for space keeps Move thumbnails unavailable. */
export function openMoveReview({ scope, store, plan, refusal = null, trigger = null, notify, navigate, onStale = null }) {
    const options = [];
    let box = null;
    if (plan.confirm_required && !refusal) {
        const label = el('label', undefined, 'review-confirm');
        box = el('input');
        box.type = 'checkbox';
        box.id = 'review-confirmed';
        label.append(box, el('span', `Prompt Manager may create its own folders and a .promptmanager-thumbnails.json file in ${plan.to.path}.`));
        options.push(label);
    }
    if (refusal) options.push(el('p', refusal, 'small review-refusal'));
    const review = openReviewDialog({ scope, trigger, title: 'Move thumbnails', grid: reviewRows(plan), options,
        sentence: refusal ? null : SENTENCE, cancelLabel: 'Cancel', primary: { label: 'Move thumbnails' },
        onStart: () => { startMove({ store, scope, token: plan.token, confirmed: Boolean(box?.checked), notify, navigate,
            onStale }); } });
    const locked = () => Boolean(refusal) || (box !== null && !box.checked);
    review.setPrimary({ label: 'Move thumbnails', disabled: locked() });
    if (box) scope.listen(box, 'change', () => review.setPrimary({ label: 'Move thumbnails', disabled: locked() }));
    return review;
}

/**
 * The D196 row; `location()` gives the server's `thumbnail_location` (undefined while read). Returns `{node, paint,
 * focus}`; `paint()` follows a new location.
 */
export function locationBlock({ api, store, scope, location, notify = showWorkspaceToast, navigate = navigateTo }) {
    const block = el('div', undefined, 'setting-block');
    block.id = 'location-block';
    const text = el('div', undefined, 'setting-block-text');
    const label = el('span', 'Thumbnail location', 'setting-block-label');
    label.id = 'location-label';
    text.append(label, el('p', LOCATION_HINT));
    const choice = el('div', undefined, 'location-choice');
    choice.setAttribute('role', 'radiogroup');
    choice.setAttribute('aria-labelledby', 'location-label');
    const source = radio('source', 'Inside each source', '<source>/thumbnails');
    const folder = radio('folder', 'One folder', 'one subfolder per source');
    choice.append(source.node, folder.node);
    const pathField = el('div', undefined, 'location-path field');
    const pathLabel = el('label', 'Folder path');
    pathLabel.htmlFor = 'location-path';
    const input = el('input');
    input.id = 'location-path';
    input.autocomplete = 'off';
    input.spellcheck = false;
    const problem = el('p', '', 'small location-problem');
    problem.id = 'location-problem';
    problem.setAttribute('role', 'alert');
    pathField.append(pathLabel, input, problem);
    const status = el('p', '', 'small location-status');
    status.id = 'location-status';
    status.setAttribute('role', 'status');
    const actions = el('div', undefined, 'row-actions');
    const review = createButton('Review move…');
    review.id = 'review-move';
    actions.append(review);
    block.append(text, choice, pathField, status, actions);

    let shown;
    const setProblem = message => {
        problem.textContent = message || '';
        pathField.setAttribute('aria-invalid', String(Boolean(message)));
        // The field itself carries the state, so a screen reader announces it with the reason.
        if (message) {
            input.setAttribute('aria-invalid', 'true');
            input.setAttribute('aria-describedby', 'location-problem');
        } else {
            input.removeAttribute('aria-invalid');
            input.removeAttribute('aria-describedby');
        }
    };
    const current = () => ({ mode: folder.input.checked ? 'folder' : 'source', path: input.value });
    const repaintButton = () => {
        const now = location();
        input.disabled = !folder.input.checked || !now;
        source.input.disabled = folder.input.disabled = !now;
        const changed = choiceChanged(now, current());
        status.textContent = now ? statusText(now, current()) : '';
        review.setAttribute('aria-disabled', String(!changed));
        review.classList.toggle('ca-primary', changed);
    };
    const paint = () => {
        const now = location();
        if (now && now !== shown) {
            shown = now;
            (isFolder(now) ? folder : source).input.checked = true;
            input.value = (isFolder(now) ? now.path : now.default_path) || '';
            setProblem(null);
        }
        repaintButton();
    };
    for (const control of [source.input, folder.input]) {
        scope.listen(control, 'change', () => {
            if (folder.input.checked && !input.value) input.value = location()?.default_path || '';
            setProblem(null);
            repaintButton();
        });
    }
    scope.listen(input, 'input', () => { setProblem(null); repaintButton(); });
    scope.listen(review, 'click', async () => {
        if (review.getAttribute('aria-disabled') === 'true') return;
        const chosen = current();
        const body = { mode: 'move', location: chosen.mode === 'folder' ? { mode: 'folder', path: chosen.path.trim() }
            : { mode: 'source' } };
        review.setAttribute('aria-disabled', 'true');
        try {
            const plan = await api.post(PLAN_URL, body, { signal: scope.signal });
            if (!scope.signal.aborted) {
                openMoveReview({ scope, store, plan, trigger: review, notify, navigate, onStale: () => review.click() });
            }
        } catch (error) {
            if (scope.signal.aborted) return;
            if (error?.status === 507 && error?.plan) {
                openMoveReview({ scope, store, plan: error.plan, refusal: error.plan.insufficient?.text || error.message,
                    trigger: review });
            } else if (error?.status === 409 && error?.code === 'busy') {
                notify(busyToast(error.running ?? null));
            } else if (typeof error?.code === 'string' && error.code.startsWith('location-') && error.message) {
                setProblem(error.message);
                input.focus();
            } else {
                notify(typeof error?.message === 'string' && error?.code ? error.message : REVIEW_FAILED);
            }
        } finally {
            repaintButton();
        }
    });
    paint();
    return {
        node: block,
        paint,
        focus() {
            folder.input.checked = true;
            repaintButton();
            input.focus();
            block.scrollIntoView?.({ block: 'center' });
        },
    };
}

/** Open the Recreate confirmation for `location`; resolves once it closed. */
export function openRecreate({ api, scope, location, trigger = null, onDone, notify = showWorkspaceToast }) {
    const grid = el('dl', undefined, 'meta-grid');
    for (const [term, value] of [['Folder', location.path], ['Made', 'An empty folder with a new .promptmanager-thumbnails.json'],
        ['Thumbnails', 'Made again as you browse, or with Thumbnail maintenance']]) {
        const entry = el('div');
        entry.append(el('dt', term), el('dd', value));
        grid.append(entry);
    }
    const why = el('p', '', 'small location-problem');
    const cancel = createButton('Cancel');
    const go = createButton('Recreate folder', 'ca-primary');
    const actions = el('div', undefined, 'dialog-actions');
    actions.append(cancel, go);
    scope.listen(cancel, 'click', () => closeWorkspaceDialog());
    scope.listen(go, 'click', async () => {
        try {
            const result = await api.post(RECREATE_URL, {}, { signal: scope.signal });
            if (scope.signal.aborted) return;
            closeWorkspaceDialog();
            onDone?.(result.location);
        } catch (error) {
            if (scope.signal.aborted) return;
            why.textContent = error?.message || 'The folder could not be recreated.';
            if (!error?.message) notify(why.textContent);
        }
    });
    openContentDialog({ scope, title: 'Recreate the thumbnail folder?', trigger, nodes: [grid,
        el('p', 'If the folder is on a drive that is not connected, connect it instead: recreating would put thumbnails on '
            + 'another disk.', 'small'), why, actions] });
}

/**
 * The D199 notice for `location` (null while it is available): Recreate folder for a missing folder, Use this folder
 * for another folder's marker, and Change location (`onChange`).
 */
export function locationNotice({ api, scope, location, onChange, onDone, notify = showWorkspaceToast }) {
    if (!isFolder(location) || location.available !== false) return null;
    const notice = el('div', undefined, 'notice');
    notice.id = 'unavailable-notice';
    const text = el('div');
    text.append(el('h2', NOTICE_TITLE), el('p', noticeText(location)));
    const actions = el('div', undefined, 'row-actions');
    if (location.problem === 'missing') {
        const recreate = createButton('Recreate folder');
        scope.listen(recreate, 'click', () => openRecreate({ api, scope, location, trigger: recreate, onDone, notify }));
        actions.append(recreate);
    } else if (location.problem === 'marker-mismatch') {
        const adopt = createButton('Use this folder');
        scope.listen(adopt, 'click', async () => {
            try {
                const result = await api.post(ADOPT_URL, {}, { signal: scope.signal });
                if (!scope.signal.aborted) onDone?.(result.location);
            } catch (error) {
                if (!scope.signal.aborted) notify(error?.message || 'The folder could not be used.');
            }
        });
        actions.append(adopt);
    }
    const change = createButton('Change location', 'ca-primary');
    scope.listen(change, 'click', () => onChange?.());
    actions.append(change);
    notice.append(text, actions);
    return notice;
}

/**
 * Where a section shows the D199 notice: a comment `anchor` (no element, so the concept comparisons see nothing while the
 * folder is available) after which `paint()` keeps the notice for `location()`.
 */
export function noticeSlot({ api, scope, location, onChange, onDone, notify = showWorkspaceToast }) {
    const anchor = document.createComment('thumbnail-location-notice');
    let shownFor;
    let notice = null;
    const paint = () => {
        const now = location();
        // Not mounted yet: the next paint (a microtask, or the next host read) places it.
        if (!anchor.parentNode) {
            queueMicrotask(() => { if (anchor.parentNode && !scope.signal.aborted) paint(); });
            return;
        }
        if (now === shownFor) return;
        shownFor = now;
        notice?.remove();
        notice = locationNotice({ api, scope, location: now, onChange, onDone, notify });
        if (notice) anchor.after(notice);
    };
    return { anchor, paint };
}
