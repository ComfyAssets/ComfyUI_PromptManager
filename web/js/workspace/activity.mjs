/**
 * Activity (#249 Activity-1): the concept's Activity page (app.js renderActivity 732–738), its top-bar button
 * (`button#activity-open` with `span.count#activity-count`, index.html 51–52) and its sidebar row (app.js ~341), fed
 * by the session job store (activity-store.mjs).
 *
 * - D52: both counts show the running jobs only and are hidden at 0; the heading has no "Unified activity · proposed"
 *   chip (prototype copy).
 * - D53: while a job runs, the progress pill (`a.activity-pill`, the approved mockup's) sits before the button on every
 *   page: "Scanning · 42% · 8,240 / 19,622" and a mini bar, linking `#/activity/<id>`.
 * - D54: each `.activity-item` is a real job. Its chip names the state (green "Completed", or blue "Running",
 *   "Cancelled", "Failed", "Ended"), its line says when and what, and a running job shows its `<progress>` and
 *   "Cancel job" (no "Simulate completion").
 */
import { createButton } from '../shared/toolkit/core.js';
import { navigateTo } from './state.mjs';
import { pageHeading } from './heading.mjs';
import { showWorkspaceToast } from './toast.mjs';
import { countOf } from './plural.mjs';
import { pillParts, outcomeLine, addedOf, LOST_DETAIL } from './activity-store.mjs';

export const ACTIVITY_DESCRIPTION = 'Scans, tagging, downloads and maintenance share one history.';
export const EMPTY_ACTIVITY = 'No jobs have run since ComfyUI started. Scans and other long operations appear here.';
export const LOAD_FAILED = 'Activity could not be read. It is tried again shortly.';
export const CANCEL_FAILED_TOAST = 'The job could not be cancelled. Try again.';
const CHIPS = Object.freeze({
    running: ['blue', 'Running'], finished: ['green', 'Completed'], cancelled: ['blue', 'Cancelled'],
    failed: ['blue', 'Failed'], lost: ['blue', 'Ended'],
});

function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

/** "09:41" in the browser's local time; an unknown time is empty. */
export function clockTime(seconds) {
    if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return '';
    const date = new Date(seconds * 1000);
    return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}

const join = parts => parts.filter(Boolean).join(' · ');

/** What a job covers: "2 output folders" for a scan. */
function scopeLine(job) {
    const folders = job.scope?.output_folders;
    return Number.isSafeInteger(folders) && folders >= 0 ? countOf(folders, 'output folder') : '';
}

/** The item's line under its title, by state. */
export function jobDetail(job) {
    const at = clockTime(job.finished_at);
    if (job.state === 'running') {
        return join([`Started ${clockTime(job.started_at)}`, scopeLine(job), 'closing this view does not cancel it']);
    }
    if (job.state === 'lost') return LOST_DETAIL;
    if (job.state === 'cancelled') {
        return join([`Cancelled ${at} at a file boundary`, job.kind === 'scan' ? addedOf(job) : '',
            'completed work is kept']);
    }
    if (job.state === 'failed') return join([`Stopped on an error at ${at}`, outcomeLine(job)]);
    return join([`Finished ${at}`, outcomeLine(job)]);
}

/** The shell's Activity indicators: the top-bar count and pill, and the sidebar row's count. */
export function mountActivityTopbar({ store, scope, doc = document }) {
    const button = doc.getElementById('activity-open');
    const count = doc.getElementById('activity-count');
    const pill = doc.getElementById('activity-pill');
    scope.listen(button, 'click', () => navigateTo('#/activity'));
    const label = element('span', '', 'activity-pill-label');
    const files = element('span', '', 'activity-pill-files');
    const bar = element('span', undefined, 'activity-pill-bar');
    const fill = element('i');
    bar.setAttribute('aria-hidden', 'true');
    bar.append(fill);
    pill.replaceChildren(label, files, bar);
    const render = () => {
        const running = store.running();
        count.textContent = String(running.length);
        count.hidden = running.length === 0;
        const row = doc.querySelector('#system-nav a[data-route="activity"]');
        row?.querySelector('.nav-count')?.remove();
        if (row && running.length) row.append(element('span', running.length.toLocaleString(), 'nav-count'));
        const [job] = running;
        pill.hidden = !job;
        if (!job) {
            pill.removeAttribute('href');
            pill.removeAttribute('aria-label');
            return;
        }
        const parts = pillParts(job);
        label.textContent = parts.label;
        files.textContent = parts.files;
        fill.style.width = `${job.progress}%`;
        pill.href = `#/activity/${job.id}`;
        pill.setAttribute('aria-label', parts.text);
    };
    scope.cleanup(store.subscribe(render));
    render();
}

function itemFor(job, scope) {
    const item = element('div', undefined, 'activity-item');
    item.dataset.jobId = job.id;
    const heading = element('div', undefined, 'panel-heading');
    const title = element('h3');
    const chip = element('span');
    heading.append(title, chip);
    const detail = element('p');
    const progress = element('progress');
    progress.max = 100;
    const actions = element('div', undefined, 'row-actions');
    const cancel = createButton('Cancel job');
    actions.append(cancel);
    item.append(heading, detail, progress, actions);
    scope.listen(cancel, 'click', async () => {
        if (cancel.getAttribute('aria-disabled') === 'true') return;
        cancel.setAttribute('aria-disabled', 'true');
        try {
            await item.cancelJob();
        } catch (error) {
            if (scope.signal.aborted) return;
            cancel.removeAttribute('aria-disabled');
            showWorkspaceToast(CANCEL_FAILED_TOAST);
        }
    });
    item.update = next => {
        const [tone, state] = CHIPS[next.state];
        title.textContent = `${next.state === 'finished' ? '✓' : '○'} ${next.title}`;
        chip.className = `chip ${tone}`;
        chip.textContent = state;
        detail.textContent = jobDetail(next);
        const running = next.state === 'running';
        if (running) {
            progress.value = next.progress;
            progress.textContent = `${next.progress}%`;
            progress.setAttribute('aria-label', `${next.title} progress`);
            if (!progress.isConnected) item.insertBefore(progress, actions.isConnected ? actions : null);
            if (!actions.isConnected) item.append(actions);
        } else {
            progress.remove();
            actions.remove();
        }
    };
    return item;
}

/** The Activity page: the concept's "Recent work" list of this session's jobs. `jobId` marks one job's item. */
export function mountActivityPage(main, { store, scope, jobId = null }) {
    pageHeading(main).description.textContent = ACTIVITY_DESCRIPTION;
    const section = element('section', undefined, 'surface');
    const status = element('p', undefined, 'empty-state');
    section.append(element('h2', 'Recent work'));
    main.append(section);
    const items = new Map();
    let revealed = false;
    const render = () => {
        const jobs = store.jobs();
        for (const [id, item] of items) if (!jobs.some(job => job.id === id)) { item.remove(); items.delete(id); }
        jobs.forEach((job, index) => {
            let item = items.get(job.id);
            if (!item) {
                item = itemFor(job, scope);
                item.cancelJob = () => store.cancel(job.id);
                items.set(job.id, item);
            }
            item.update(job);
            if (job.id === jobId) item.setAttribute('aria-current', 'true');
            // Only a node out of place moves, so a focused Cancel job keeps its focus across updates.
            const at = section.children[index + 1];
            if (at !== item) section.insertBefore(item, at ?? null);
        });
        const failed = store.status === 'error' && !jobs.length;
        status.textContent = failed ? LOAD_FAILED : EMPTY_ACTIVITY;
        if (failed) status.setAttribute('role', 'alert');
        else status.removeAttribute('role');
        if (jobs.length || store.status === 'idle') status.remove();
        else if (!status.isConnected) section.append(status);
        const marked = jobId && items.get(jobId);
        if (marked && !revealed) {
            revealed = true;
            marked.scrollIntoView({ block: 'nearest' });
        }
    };
    scope.cleanup(store.subscribe(render));
    render();
    // Opening Activity reads the list at once instead of waiting for the next periodic read.
    store.load();
}
