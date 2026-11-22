/**
 * Review tag cleanup (#618, D210): a finished tag search's findings, ticked one by one or a whole rule at a time, then
 * reviewed as the `tag_cleanup` D100 plan (maintenance-review.mjs `reviewPlan`).
 *
 * The dialog has no rule table of its own: it draws whatever rules `GET /prompt_manager/maintenance/tag_cleanup/<job>`
 * returns (each `{id, title, explanation, action_label, total, findings:[{tag_id, name, detail}]}`), so a later rule
 * needs no change here. Top to bottom: the `role="status"` line ("N tags selected"); per rule with findings a checkbox
 * "<title> (<total>)" that ticks or clears every listed tag of the rule (indeterminate while only some are), its
 * explanation and what applying does, and a closed "Show tags" `details` whose rows are built when it first opens (a
 * search may list thousands); when the server's cap cut the list, a line says to search again; the note that nothing is
 * deleted until the cleanup is reviewed and applied; Cancel and "Review cleanup" (disabled while nothing is ticked).
 * Nothing is ticked when it opens. Tag names are set as text and wrap anywhere (a comma tag can be hundreds of
 * characters long).
 */
import { createButton } from '../shared/toolkit/core.js';
import { closeWorkspaceDialog, openContentDialog } from './workspace-dialogs.mjs';
import { REVIEW_FAILED, reviewPlan, reviewRefusal } from './maintenance-review.mjs';
import { showWorkspaceToast } from './toast.mjs';
import { navigateTo } from './state.mjs';
import { noun } from './plural.mjs';

export const FINDINGS_URL = '/prompt_manager/maintenance/tag_cleanup';
export const TAG_CLEANUP_OPERATION = 'tag_cleanup';
export const REVIEW_TITLE = 'Review tag cleanup';
export const REVIEW_LABEL = 'Review cleanup';
export const SHOW_TAGS = 'Show tags';
export const CLEANUP_NOTE = 'Nothing is deleted until you review and apply the cleanup. Prompts, pairs and images are kept; '
    + 'only the tag is removed from them.';
export const CAP_NOTE = 'Only the first 2,000 are listed. Clean these up, then search again.';
export const FINDINGS_FAILED = 'The tags to clean up could not be read. Nothing was changed.';

const number = value => value.toLocaleString('en-US');

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

/** A finished tag search that found tags to review (Activity's Review tags). */
export function hasTagFindings(job) {
    return job?.kind === 'tag_cleanup' && job.state === 'finished' && (job.counters?.found ?? 0) > 0;
}

/** The rules the server returned that have findings to draw, in its order. */
export function shownRules(rules) {
    return (Array.isArray(rules) ? rules : [])
        .filter(rule => rule && typeof rule.id === 'string' && Array.isArray(rule.findings) && rule.findings.length > 0);
}

/** The selection: per rule, the tag ids it listed (in order) and the set of ticked ones (none at first). */
export function createSelection(rules) {
    const listed = new Map();
    const ticked = new Map();
    for (const rule of shownRules(rules)) {
        listed.set(rule.id, rule.findings.map(finding => finding.tag_id));
        ticked.set(rule.id, new Set());
    }
    return { listed, ticked };
}

export function selectedCount(selection) {
    let count = 0;
    for (const set of selection.ticked.values()) count += set.size;
    return count;
}

/** "N tags selected". */
export const statusText = count => `${number(count)} ${noun(count, 'tag')} selected`;

/** `all`, `some` or `none` of the rule's listed tags ticked. */
export function ruleState(selection, ruleId) {
    const size = selection.ticked.get(ruleId)?.size ?? 0;
    if (!size) return 'none';
    return size === selection.listed.get(ruleId).length ? 'all' : 'some';
}

export const isTicked = (selection, ruleId, tagId) => selection.ticked.get(ruleId)?.has(tagId) === true;

/** Tick every listed tag of the rule, or clear them all. */
export function toggleRule(selection, ruleId, on) {
    const set = selection.ticked.get(ruleId);
    if (!set) return;
    set.clear();
    if (on) for (const tagId of selection.listed.get(ruleId)) set.add(tagId);
}

/** Tick or clear one tag the rule listed (any other id is ignored). */
export function toggleTag(selection, ruleId, tagId, on) {
    const set = selection.ticked.get(ruleId);
    if (!set || !selection.listed.get(ruleId).includes(tagId)) return;
    if (on) set.add(tagId);
    else set.delete(tagId);
}

/** The plan's `select`: each rule with ticks once, in the server's order, with its ticked ids in listed order. */
export function selectPayload(selection) {
    const select = [];
    for (const [rule, ids] of selection.listed) {
        const set = selection.ticked.get(rule);
        const tagIds = ids.filter(id => set.has(id));
        if (tagIds.length) select.push({ rule, tag_ids: tagIds });
    }
    return select;
}

/** "Tags containing a comma (232)". */
export const ruleLabel = rule => `${rule.title} (${number(Number.isSafeInteger(rule.total) ? rule.total : rule.findings.length)})`;
/** "a, b · 3 prompts · 1 pair". */
export const findingLabel = finding => (finding.detail ? `${finding.name} · ${finding.detail}` : finding.name);
/** The rule's explanation and what applying it does, as one line. */
export const ruleText = rule => [rule.explanation, rule.action_label].filter(Boolean).join(' ');
/** The cap line when the server listed fewer tags than it found, else null. */
export const capNote = rule => (Number.isSafeInteger(rule.total) && rule.total > rule.findings.length ? CAP_NOTE : null);

function checkboxRow(text, className = 'review-confirm') {
    const label = el('label', undefined, className);
    const box = el('input');
    box.type = 'checkbox';
    label.append(box, el('span', text));
    return { label, box };
}

/** One rule's block: its checkbox, its line, its lazily built tag rows and the cap line. */
function ruleBlock(rule, selection, scope, changed) {
    const block = el('div', undefined, 'tag-cleanup-rule');
    block.dataset.rule = rule.id;
    const head = checkboxRow(ruleLabel(rule));
    const rows = new Map();
    const list = el('details');
    list.append(el('summary', SHOW_TAGS));
    const paint = () => {
        const state = ruleState(selection, rule.id);
        head.box.checked = state === 'all';
        head.box.indeterminate = state === 'some';
        for (const [tagId, box] of rows) box.checked = isTicked(selection, rule.id, tagId);
    };
    scope.listen(head.box, 'change', () => {
        toggleRule(selection, rule.id, head.box.checked);
        paint();
        changed();
    });
    scope.listen(list, 'toggle', () => {
        if (!list.open || rows.size) return;
        const body = el('div', undefined, 'tag-cleanup-tags');
        for (const finding of rule.findings) {
            const row = checkboxRow(findingLabel(finding));
            row.box.checked = isTicked(selection, rule.id, finding.tag_id);
            rows.set(finding.tag_id, row.box);
            scope.listen(row.box, 'change', () => {
                toggleTag(selection, rule.id, finding.tag_id, row.box.checked);
                paint();
                changed();
            });
            body.append(row.label);
        }
        list.append(body);
    });
    block.append(head.label, el('p', ruleText(rule), 'small'), list);
    const cap = capNote(rule);
    if (cap) block.append(el('p', cap, 'small'));
    return block;
}

/** Open "Review tag cleanup" for the finished tag search `jobId`. */
export async function openTagCleanupReview({ api, store, scope, jobId, trigger = null, notify = showWorkspaceToast,
    navigate = navigateTo }) {
    let body;
    try {
        body = await api.get(`${FINDINGS_URL}/${encodeURIComponent(jobId)}`, { signal: scope.signal, cache: 'no-store' });
    } catch (error) {
        if (!scope.signal.aborted) notify(reviewRefusal(error) === REVIEW_FAILED ? FINDINGS_FAILED : error.message);
        return;
    }
    if (scope.signal.aborted) return;
    const rules = shownRules(body?.rules);
    const selection = createSelection(rules);
    const status = el('p', statusText(0), 'small');
    status.setAttribute('role', 'status');
    const cancel = createButton('Cancel');
    const review = createButton(REVIEW_LABEL);
    review.disabled = true;
    const actions = el('div', undefined, 'dialog-actions');
    actions.append(cancel, review);
    const changed = () => {
        const count = selectedCount(selection);
        status.textContent = statusText(count);
        review.disabled = count === 0;
    };
    scope.listen(cancel, 'click', () => closeWorkspaceDialog());
    scope.listen(review, 'click', () => {
        if (review.disabled) return;
        reviewPlan({ api, store, scope, operation: TAG_CLEANUP_OPERATION, trigger, hold: review,
            options: { job_id: jobId, select: selectPayload(selection) }, notify, navigate });
    });
    const blocks = rules.map(rule => ruleBlock(rule, selection, scope, changed));
    openContentDialog({ scope, title: REVIEW_TITLE, trigger,
        nodes: [status, ...blocks, el('p', CLEANUP_NOTE, 'small'), actions] });
}
