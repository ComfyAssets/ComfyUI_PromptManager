/**
 * Maintenance reviews (#249 System S7 Task 19): the concept's "Review maintenance plan" (`maintenancePlan`, app.js
 * 953–958) and "Review duplicate image files" (`duplicates`, 1457–1461) on the S6 backend.
 *
 * - D100 plan dialog: Review plan reads `POST /prompt_manager/maintenance/preview` (read-only) and shows the server's
 *   plan in the shared review (`openReviewDialog`): the operation's `h3`, the concept's `dl.meta-grid` Scope /
 *   Protected / Backup / Media on disk with the plan's own values, a `details` list of the records it names, the
 *   `p.small` line saying until when the review holds, the D55 backup box ("Back up now") and Cancel / "Apply reviewed
 *   operation" (the concept's "Simulate reviewed operation"). Cancel writes nothing. A plan with nothing to do (a scope
 *   of 0) shows its rows, "There is nothing to apply; nothing was changed." and only Close.
 * - Apply sends the plan's token (`POST /maintenance/apply`): the dialog closes, the job is followed in the Activity
 *   store, which toasts that it started, and its view opens. A refused apply says why: `busy` names the running job
 *   (D57), `plan-used` links the job that applied it, an expired or replaced review is reviewed again at once, and an
 *   apply whose answer was lost says "Outcome not confirmed" and points to Activity (the job may be running).
 * - D101 duplicates dialog: a finished duplicate search's groups, ten at a time (`GET /maintenance/duplicates/<job>`),
 *   one `.compare-grid` per group with a `.compare-item` per copy (its preview and a Keep radio; the oldest copy is
 *   kept unless another is chosen). Each choice names its copy by `{group_id, root_index, path}`, so equal paths in two
 *   sources are never confused. The `p.small` says what removing a copy does, History keeping the removed paths in its
 *   run records. "Review duplicate cleanup" reviews the groups shown so far as a `duplicate_files` plan.
 *
 * Every request goes through the page's api client; every listener sits on the section's `scope`.
 */
import { createButton } from '../shared/toolkit/core.js';
import { closeWorkspaceDialog, openContentDialog, openReviewDialog } from './workspace-dialogs.mjs';
import { busyToast, downloadDatabaseBackup } from './activity-scan.mjs';
import { showWorkspaceToast } from './toast.mjs';
import { navigateTo } from './state.mjs';
import { noun } from './plural.mjs';

export const PREVIEW_URL = '/prompt_manager/maintenance/preview';
export const APPLY_URL = '/prompt_manager/maintenance/apply';
export const GROUPS_URL = '/prompt_manager/maintenance/duplicates';
export const GROUP_PAGE = 10;
export const PLAN_TITLE = 'Review maintenance plan';
export const DUPLICATES_TITLE = 'Review duplicate image files';
export const APPLY_LABEL = 'Apply reviewed operation';
export const NOTHING_TO_APPLY = 'There is nothing to apply; nothing was changed.';
export const REVIEW_FAILED = 'The plan could not be reviewed. Nothing was changed.';
export const APPLY_FAILED = 'The operation could not start. Nothing was changed.';
export const GROUPS_FAILED = 'The duplicate groups could not be read. Nothing was changed.';
export const NOT_CONFIRMED = ['Outcome not confirmed. ', { text: 'Check Activity', href: '#/activity' },
    ' before applying it again.'];
export const DUPLICATES_NOTE = 'Removing a copy deletes its file from disk; its database links move to the kept copy. ' +
    'History keeps the removed paths in its run records. Nothing is deleted until you review and apply the cleanup.';
export const BACKUP_COPY = Object.freeze({ heading: 'Back up the database first (recommended)',
    text: 'Downloads a copy of the prompts database before this operation changes any records.' });
/** The concept's operation names, which head the plan dialog (and title the server's job). */
export const OPERATION_TITLES = Object.freeze({
    duplicate_prompts: 'Duplicate prompt text',
    optimize: 'Optimize database',
    unused_components: 'Unused prompt components',
    missing_links: 'Missing-file links',
    duplicate_files: 'Duplicate image files',
    // Reviewed and applied through the API and seen in Activity; Maintenance has no row for it (the concept has none).
    workflow_storage: 'Store each workflow once',
});
// The review is gone or no longer matches the database: review it again.
const REVIEW_AGAIN = new Set(['plan-expired', 'storage-changed']);
// Refusals whose server message is the whole truth.
const SERVER_SAYS = new Set(['insufficient-space', 'nothing-to-do', 'too-large', 'invalid-request', 'not-found',
    'job-running', 'no-groups', 'apply-failed', 'sources-changed']);

const number = value => value.toLocaleString('en-US');

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

/** The Backup value: what the apply will do before it changes anything. */
export function backupText(backup) {
    if (backup?.policy === 'off') return 'No safety backup (the policy is off)';
    const where = backup?.destination_label ? ` (${backup.destination_label})` : '';
    if (backup?.policy === 'required') return `Always created and verified first${where}`;
    return `Create and verify before changes${where}`;
}

/** The Protected value: the server's reasons, or that nothing is held back. */
export function protectedText(held) {
    const reasons = Array.isArray(held?.reasons) ? held.reasons.filter(reason => typeof reason === 'string' && reason) : [];
    if (reasons.length) return reasons.join(' · ');
    const count = Number.isSafeInteger(held?.count) ? held.count : 0;
    return count ? `${number(count)} ${noun(count, 'record')} kept` : 'Nothing is protected';
}

/** D100: the concept's four rows with the plan's values. */
export function planGrid(plan) {
    return [
        ['Scope', plan.scope?.label || `${number(plan.scope?.count ?? 0)} ${noun(plan.scope?.count ?? 0, 'record')}`],
        ['Protected', protectedText(plan.protected)],
        ['Backup', backupText(plan.backup)],
        ['Media on disk', plan.media?.label || 'No files are removed'],
    ];
}

const megabytes = bytes => `${(bytes / 2 ** 20).toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} MB`;

/** D100: the `p.small` line: until when the review holds, and a disk without room for the apply's writes. */
export function planNote(plan, { now = Date.now() } = {}) {
    const parts = [];
    const expires = new Date(plan.expires_at);
    if (!Number.isNaN(expires.getTime())) {
        const minutes = Math.max(1, Math.round((expires.getTime() - now) / 60000));
        parts.push(`Nothing changes until you apply. This review holds for ${number(minutes)} ${noun(minutes, 'minute')}.`);
    } else {
        parts.push('Nothing changes until you apply.');
    }
    const disk = plan.disk;
    if (disk && disk.sufficient === false && Number.isFinite(disk.required_bytes) && Number.isFinite(disk.free_bytes)) {
        parts.push(`Not enough free disk space: about ${megabytes(disk.required_bytes)} is needed and ${megabytes(disk.free_bytes)} is free.`);
    }
    return parts.join(' ');
}

/** Whether the plan changes anything: a scope of 0 records has nothing to apply (an unknown count is not assumed empty). */
export function hasWork(plan) {
    return !(Number.isSafeInteger(plan?.scope?.count) && plan.scope.count === 0);
}

/** The records a plan names (at most 50), as `label · detail` lines. */
export function detailLines(plan) {
    return (Array.isArray(plan.details) ? plan.details : [])
        .filter(row => row && typeof row.label === 'string')
        .map(row => (row.detail ? `${row.label} · ${row.detail}` : row.label));
}

/**
 * The toast for a refused or failed apply; `review` when the plan must be reviewed again, `lost` when the outcome is
 * not known. Only our server's own refusal is definitive (a known code, or a 4xx whose body says success:false): a
 * dropped connection, a proxy's 5xx or a 2xx whose body could not be read may follow a started job.
 */
export function applyRefusal(error) {
    const status = error?.status;
    const known = error?.code && (error.code === 'busy' || error.code === 'plan-used' || REVIEW_AGAIN.has(error.code)
        || SERVER_SAYS.has(error.code));
    const refused = Number.isInteger(status) && status >= 400 && status < 500 && error.refused === true;
    if (!known && !refused) return { toast: NOT_CONFIRMED, lost: true };
    if (error.code === 'busy') return { toast: busyToast(error.running) };
    if (error.code === 'plan-used') {
        const job = error.job;
        if (!job) return { toast: 'This plan was already applied.' };
        return { toast: ['This plan was already applied: ', { text: job.title ?? 'its job', href: `#/activity/${job.id}` }, '.'] };
    }
    if (REVIEW_AGAIN.has(error.code)) return { toast: error.message, review: true };
    if (SERVER_SAYS.has(error.code) && error.message) return { toast: error.message };
    return { toast: APPLY_FAILED };
}

/** The toast for a review that could not be read. */
export function reviewRefusal(error) {
    if ((REVIEW_AGAIN.has(error?.code) || SERVER_SAYS.has(error?.code)) && error.message) return error.message;
    return REVIEW_FAILED;
}

/** Hold `trigger` aria-disabled with `busyText` while `work()` runs; a second press is ignored. */
async function holding(trigger, busyText, work) {
    if (trigger?.getAttribute('aria-disabled') === 'true') return;
    const text = trigger?.textContent;
    trigger?.setAttribute('aria-disabled', 'true');
    if (trigger && busyText) trigger.textContent = busyText;
    try {
        await work();
    } finally {
        trigger?.removeAttribute('aria-disabled');
        if (trigger && busyText) trigger.textContent = text;
    }
}

/**
 * Apply a reviewed plan's `token`; the job opens in Activity. `reviewAgain()` reviews the operation once more when the
 * server says the review expired or the database was replaced.
 */
export async function applyPlan({ api, store, scope, token, reviewAgain, notify = showWorkspaceToast, navigate = navigateTo }) {
    let result;
    try {
        result = await api.post(APPLY_URL, { token });
    } catch (error) {
        if (scope.signal.aborted) return;
        const refusal = applyRefusal(error);
        notify(refusal.toast);
        if (refusal.lost) store.load();
        if (refusal.review) reviewAgain?.();
        return;
    }
    if (scope.signal.aborted) return;
    let job;
    try {
        job = store.track(result?.job);
    } catch {
        notify(NOT_CONFIRMED);
        store.load();
        return;
    }
    notify(`${job.title} started.`);
    navigate(`#/activity/${job.id}`);
}

/** Show the reviewed `plan` for `operation`; Apply applies exactly this token. */
export function showPlan(plan, { api, store, scope, trigger = null, reviewAgain, notify = showWorkspaceToast,
    navigate = navigateTo, backup = () => downloadDatabaseBackup() }) {
    const options = [];
    const lines = detailLines(plan);
    if (lines.length) {
        const list = el('details');
        const count = plan.scope?.count ?? lines.length;
        list.append(el('summary', lines.length < count ? `Listed records (${number(lines.length)} of ${number(count)})`
            : `Listed records (${number(lines.length)})`));
        const body = el('pre', lines.join('\n'));
        body.tabIndex = 0;
        list.append(body);
        options.push(list);
    }
    const heading = OPERATION_TITLES[plan.operation] ?? plan.operation;
    if (!hasWork(plan)) {
        // Nothing to apply: the review's own rows and a Close, never an Apply that would start an empty job.
        const grid = el('dl', undefined, 'meta-grid');
        for (const [term, value] of planGrid(plan)) {
            const entry = el('div');
            entry.append(el('dt', term), el('dd', value));
            grid.append(entry);
        }
        const close = createButton('Close');
        close.addEventListener('click', () => closeWorkspaceDialog());
        const actions = el('div', undefined, 'dialog-actions');
        actions.append(close);
        openContentDialog({ scope, trigger, title: PLAN_TITLE,
            nodes: [el('h3', heading), grid, ...options, el('p', NOTHING_TO_APPLY, 'small'), actions] });
        return;
    }
    openReviewDialog({ scope, trigger, title: PLAN_TITLE, heading,
        grid: planGrid(plan), options, sentence: planNote(plan), backup: { copy: BACKUP_COPY, onBackup: backup },
        cancelLabel: 'Cancel', primary: { label: APPLY_LABEL },
        onStart: () => applyPlan({ api, store, scope, token: plan.token, reviewAgain, notify, navigate }) });
}

/**
 * Review `operation` (`POST /maintenance/preview`) and show its plan. `options` are the preview's (duplicate_files:
 * `{job_id, keep}`). `hold` (default `trigger`, which gets focus back) is the pressed button: it reads "Reviewing…"
 * and ignores presses until the review answered, so a double press sends one preview. Resolves once the plan is shown
 * or the refusal toasted.
 */
export async function reviewPlan({ api, store, scope, operation, options = {}, trigger = null, hold = trigger,
    notify = showWorkspaceToast, navigate = navigateTo }) {
    const again = () => reviewPlan({ api, store, scope, operation, options, trigger, notify, navigate });
    await holding(hold, 'Reviewing…', async () => {
        let plan;
        try {
            plan = await api.post(PREVIEW_URL, { operation, options }, { signal: scope.signal });
        } catch (error) {
            if (!scope.signal.aborted) notify(reviewRefusal(error));
            return;
        }
        if (scope.signal.aborted) return;
        showPlan(plan, { api, store, scope, trigger, notify, navigate, reviewAgain: again });
    });
}

// ── duplicate image files ───────────────────────────────────────────────────

/** The keep entries for the chosen copies: `{group_id, root_index, path}`, never a bare path (equal paths in two sources). */
export function keepEntries(chosen) {
    return [...chosen].map(([groupId, file]) => ({ group_id: groupId, root_index: file.root_index, path: file.path }));
}

/** "Groups 1–10 of 24". */
export function pageText(offset, shown, total) {
    if (!total) return 'No duplicate files were found.';
    return `${noun(total, 'Group')} ${number(offset + 1)}–${number(offset + shown)} of ${number(total)}`;
}

const kilobytes = bytes => (bytes >= 2 ** 20 ? megabytes(bytes) : `${number(Math.max(1, Math.round(bytes / 1024)))} KB`);

/** A group's line: "3 identical files · 120 KB each". */
export function groupText(group) {
    const count = Number.isSafeInteger(group.count) ? group.count : group.files.length;
    return `${number(count)} identical ${noun(count, 'file')} · ${kilobytes(group.size ?? 0)} each`;
}

/** A copy's Keep label: its source and path; a file gone since the search says so. */
export function copyLabel(file) {
    const where = file.root ? `${file.root}/${file.path}` : file.path;
    return file.missing ? `Keep ${where} (no longer on disk)` : `Keep ${where}`;
}

function preview(file) {
    const url = file.thumbnail_url ?? (file.media_type === 'video' ? null : file.url);
    if (!url) return el('div', file.media_type === 'video' ? 'Video' : 'No preview', 'empty-state');
    const image = el('img', undefined, 'sample-fluid');
    image.src = window.ApiClient.resolveUrl(url);
    image.alt = file.filename ?? file.path;
    image.loading = 'lazy';
    return image;
}

function copyItem(group, file, index, chosen, scope) {
    const item = el('div', undefined, 'compare-item');
    const label = el('label');
    const radio = el('input');
    radio.type = 'radio';
    radio.name = `keep-${group.group_id}`;
    radio.value = String(index);
    radio.disabled = file.missing === true;
    const current = chosen.get(group.group_id);
    radio.checked = current === file;
    scope.listen(radio, 'change', () => { if (radio.checked) chosen.set(group.group_id, file); });
    label.append(radio, document.createTextNode(` ${copyLabel(file)}`));
    item.append(preview(file), label);
    return item;
}

/**
 * Open "Review duplicate image files" for the finished search `jobId`, at its first page. Choices are kept across pages;
 * the groups shown so far go into the cleanup review.
 */
export async function openDuplicatesReview({ api, store, scope, jobId, trigger = null, notify = showWorkspaceToast,
    navigate = navigateTo }) {
    const chosen = new Map();
    const status = el('p', '', 'small');
    status.setAttribute('role', 'status');
    const groupsNode = el('div', undefined, 'duplicate-groups');
    const previous = createButton('Previous groups');
    const next = createButton('Next groups');
    const pager = el('div', undefined, 'row-actions');
    pager.append(previous, next);
    const cancel = createButton('Cancel');
    // The concept's `duplicates` dialog: a plain button; the plan it opens has the primary.
    const review = createButton('Review duplicate cleanup');
    const actions = el('div', undefined, 'dialog-actions');
    actions.append(cancel, review);
    let offset = 0;
    let total = 0;

    async function load(at) {
        const query = new URLSearchParams({ offset: String(at), limit: String(GROUP_PAGE) });
        const page = await api.get(`${GROUPS_URL}/${encodeURIComponent(jobId)}?${query}`, { signal: scope.signal, cache: 'no-store' });
        const groups = Array.isArray(page?.groups) ? page.groups : [];
        offset = at;
        total = Number.isSafeInteger(page?.total) ? page.total : groups.length;
        const nodes = [];
        for (const group of groups) {
            const files = Array.isArray(group.files) ? group.files : [];
            if (!chosen.has(group.group_id)) {
                const keep = files.find(file => !file.missing);
                if (keep) chosen.set(group.group_id, keep);
            } else {
                // A page read again: the same copy, by its identity, stays chosen.
                const was = chosen.get(group.group_id);
                const same = files.find(file => file.root_index === was.root_index && file.path === was.path);
                if (same) chosen.set(group.group_id, same);
            }
            const grid = el('div', undefined, 'compare-grid');
            grid.append(...files.map((file, index) => copyItem(group, file, index, chosen, scope)));
            nodes.push(el('p', groupText({ ...group, files }), 'small'), grid);
        }
        groupsNode.replaceChildren(...nodes);
        status.textContent = pageText(offset, groups.length, total);
        previous.disabled = offset === 0;
        next.disabled = offset + groups.length >= total;
        review.disabled = chosen.size === 0;
    }

    try {
        await load(0);
    } catch (error) {
        if (!scope.signal.aborted) notify(reviewRefusal(error) === REVIEW_FAILED ? GROUPS_FAILED : error.message);
        return;
    }
    if (scope.signal.aborted) return;
    const turn = async at => {
        try {
            await load(at);
        } catch (error) {
            if (!scope.signal.aborted) notify(GROUPS_FAILED);
        }
    };
    scope.listen(previous, 'click', () => turn(Math.max(0, offset - GROUP_PAGE)));
    scope.listen(next, 'click', () => turn(offset + GROUP_PAGE));
    scope.listen(cancel, 'click', () => closeWorkspaceDialog());
    scope.listen(review, 'click', () => {
        if (review.disabled) return;
        reviewPlan({ api, store, scope, operation: 'duplicate_files', trigger, hold: review,
            options: { job_id: jobId, keep: keepEntries(chosen) }, notify, navigate });
    });
    const nodes = [status, groupsNode];
    if (total > GROUP_PAGE) nodes.push(pager);
    nodes.push(el('p', DUPLICATES_NOTE, 'small'), actions);
    openContentDialog({ scope, title: DUPLICATES_TITLE, trigger, nodes });
}
