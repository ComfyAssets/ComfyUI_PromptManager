/**
 * System › Sources & scans (#249 System S4 Task 11): the concept's `renderSystem` "sources" body (app.js 679–681).
 *
 * - The optional `.notice` "Bring older images into your prompt history" shows until this browser dismissed the 3.2.0
 *   update notice (`pm_update_notice_dismissed`, the legacy admin page's own key). Later dismisses it without scanning;
 *   Review scan dismisses it, as the legacy page's Scan does, and reviews every source.
 * - "Image sources" lists the server's sources (GET /settings, S4 Task 9) in `#source-list`, one `.operation` each:
 *   its label, "<path> · watching | watching until restart | manual scan | restart pending" and "Scan changes"
 *   (watched) or "Scan folder", which opens the shared "Review metadata scan" (D55) for that one source ("1 output
 *   folder") and starts the session job over it (`roots` with its public path, S4 Task 10). The job opens in Activity;
 *   the toast only says it started. When the sources changed since they were read, nothing starts: the toast says so
 *   and the list is read again.
 * - "+ Add source" asks the server whether a folder can be a source (`validate-source`, which writes nothing), then adds
 *   it to the host draft of `gallery_root_paths`; Remove takes one out. Both wait for the one Save row.
 * - "Watch for new outputs" (`monitoring_enabled`) and "Worker threads" (`worker_threads`, a range up to the server's
 *   `cpu_count`, D113) are host drafts too. "Enrich existing metadata" reviews a scan of every source.
 * - D147 (#247): "Save prompts from other workflows" (`save_foreign_runs`), a host draft below "Watch for new outputs",
 *   applied by the server at once (the monitor reads it for every file); disabled, saying why, while monitoring is off,
 *   unless the running monitor still watches until a restart (D128 `stopping`): then it can still be set.
 * - "Thumbnail generation → Review options" opens "Thumbnail maintenance" (thumbnail-review.mjs, S5 Task 14), which
 *   starts the thumbnail Activity job.
 *
 * D98: per-source Remove; an added source reads "not saved yet" until Save; the line under the list says when
 * ComfyUI's output folder is used instead of configured sources or after an unsaved removal of the last one; with no
 * source configured, ComfyUI's output folder is the row "ComfyUI/output · watching · default" (`in_comfyui`: its public
 * path is relative to the ComfyUI base) and its scan keeps the server's public path; "restart pending" for a source the running monitor does not watch yet; the
 * "Refresh files" operation (`/gallery/rescan`) closes the surface. D127: the add dialog's field is "Folder path",
 * starts empty and says the server's reason in place; its action is "Add", and Enter adds too.
 * D128: "watching until restart" for a source the running monitor still watches after monitoring was switched off;
 * "same folder as another source" for a configured source the effective list already holds under another spelling; a
 * save refused for one source (`invalid-source`) marks that row and the status says the server's reason.
 */
import { createButton } from '../shared/toolkit/core.js';
import { closeWorkspaceDialog, field, openContentDialog } from './workspace-dialogs.mjs';
import { openMetadataScan } from './activity-scan.mjs';
import { openThumbnailReview } from './thumbnail-review.mjs';
import { settingRow, operationRow } from './system-controls.mjs';
import { HOST_UNAVAILABLE } from './system-appearance.mjs';
import { showWorkspaceToast } from './toast.mjs';
import { noun } from './plural.mjs';

const SETTINGS_URL = '/prompt_manager/settings';
const VALIDATE_URL = '/prompt_manager/settings/validate-source';
const RESCAN_URL = '/prompt_manager/gallery/rescan';
const ROOTS_KEY = 'gallery_root_paths';

export const NOTICE_KEY = 'pm_update_notice_dismissed';
export const NOTICE_VERSION = '3.2.0';
export const WATCH_COPY = Object.freeze({ watching: 'watching', stopping: 'watching until restart', manual: 'manual scan',
    restart_pending: 'restart pending' });
// The states whose observer runs now: their scan only has to catch up on changes.
const WATCHED = new Set(['watching', 'stopping']);
const NOT_SAVED = 'not saved yet';
// An auto-detected source (ComfyUI's output folder) says it is the default on its own row.
const DEFAULT_SOURCE = 'default';
const COMFYUI_PREFIX = 'ComfyUI/';
export const DUPLICATE_SOURCE = 'same folder as another source';
/** D98: the line under the source list when it does not say everything by itself. */
export const NOTES = Object.freeze({
    removedAll: 'No source is configured. After saving, ComfyUI’s output folder is used.',
    fallback: 'No configured source is available; ComfyUI’s output folder is used.',
    none: 'No source is available.',
});
export const LOADING_TEXT = 'Loading sources…';
export const LOAD_FAILED_TEXT = 'Sources could not be loaded. Your saved data has not changed.';
export const DUPLICATE_TEXT = 'This folder is already a source.';
export const VALIDATE_FAILED_TEXT = 'The folder could not be checked. Try again.';
export const REFRESH_FAILED_TEXT = 'Files could not be refreshed. Try again.';
/** D147 (#247): the row the concept does not draw, below "Watch for new outputs". */
export const FOREIGN_RUNS = Object.freeze({ id: 'foreign-runs', key: 'save_foreign_runs', label: 'Save prompts from other workflows',
    description: 'New outputs of workflows without a PromptManager node add their prompt to the library when it is certain.',
    off: 'Applies while Watch for new outputs is on.' });

/**
 * D147: the row for the drafted `save_foreign_runs` (`value`) and `monitoring_enabled` (`monitoring`): disabled until
 * read, and while monitoring is off, when `note` says why (the value is kept and saved as it is). `stopping`: a source
 * is still watched until the restart (D128), so the value can still be set; the note stays.
 */
export function foreignRunsState({ value, monitoring, stopping = false }) {
    if (value === undefined) return { disabled: true, checked: null, note: null };
    const off = monitoring === false;
    return { disabled: off && !stopping, checked: value === true, note: off ? FOREIGN_RUNS.off : null };
}

// Labels of the sources added in this page session (validate-source answers them), for the rows not saved yet.
const addedLabels = new Map();

const defaultStorage = () => window.localStorage;

/** Whether this browser dismissed the 3.2.0 notice; storage that cannot be read shows it. */
export function noticeDismissed(getStorage = defaultStorage) {
    try {
        return getStorage().getItem(NOTICE_KEY) === NOTICE_VERSION;
    } catch {
        return false;
    }
}

/** Dismiss the notice for this browser; false when storage refused (it stays dismissed for this view). */
export function dismissNotice(getStorage = defaultStorage) {
    try {
        getStorage().setItem(NOTICE_KEY, NOTICE_VERSION);
        return true;
    } catch {
        return false;
    }
}

const sameRoots = (a, b) => Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((path, index) => path === b[index]);

/** The roots to draft: the saved list itself when they are equal, so the draft is clean again. */
export function draftRoots(next, saved) {
    return sameRoots(next, saved) ? saved : [...next];
}

const folderName = path => path.split('/').filter(Boolean).at(-1) ?? path;

function effectiveRow(source, remove) {
    const watched = WATCHED.has(source.watch);
    const auto = remove === null;
    // The default row reads from ComfyUI's own folder ("ComfyUI/output") when its public path is relative to the base.
    const shown = auto && source.in_comfyui === true ? `${COMFYUI_PREFIX}${source.path}` : source.path;
    const parts = [shown, WATCH_COPY[source.watch] ?? WATCH_COPY.manual, ...(auto ? [DEFAULT_SOURCE] : [])];
    return { key: remove ?? `auto:${source.root_index}`, title: source.label, description: parts.join(' · '),
        scan: { label: watched ? 'Scan changes' : 'Scan folder', root: source.root_index, path: source.path }, remove };
}

/**
 * The source list for `sources` (GET /settings' `sources`), the saved `gallery_root_paths` and the drafted ones
 * (`current`): `{rows: [{key, title, description, scan: {label, root, path} | null, remove: path | null}], note}`.
 * A valid configured source missing from the effective list is another spelling of a folder already listed.
 */
export function sourceRows({ sources, saved, current, labels = addedLabels }) {
    const configured = sources.configured ?? [];
    const effective = sources.effective ?? [];
    const savedRoots = saved ?? configured.map(source => source.path);
    const roots = current ?? savedRoots;
    const dirty = !sameRoots(roots, savedRoots);
    const rows = [];
    for (const path of new Set(roots)) {
        const entry = configured.find(source => source.path === path);
        const live = entry?.valid ? effective.find(source => source.path === path) : undefined;
        if (live) rows.push(effectiveRow(live, path));
        else if (entry) {
            const why = entry.valid ? DUPLICATE_SOURCE : entry.reason ?? WATCH_COPY.manual;
            rows.push({ key: path, title: entry.label, description: `${path} · ${why}`, scan: null, remove: path });
        } else rows.push({ key: path, title: labels.get(path) ?? folderName(path), description: `${path} · ${NOT_SAVED}`, scan: null, remove: path });
    }
    if (dirty) return { rows, note: roots.length ? null : NOTES.removedAll };
    if (sources.auto_detected) {
        rows.push(...effective.map(source => effectiveRow(source, null)));
        return { rows, note: configured.length ? NOTES.fallback : null };
    }
    return { rows, note: rows.length ? null : NOTES.none };
}

/** The Refresh files toast for the rescan's `{total, roots}`. */
export function refreshedText(result) {
    const { total, roots } = result ?? {};
    if (![total, roots].every(value => Number.isSafeInteger(value) && value >= 0)) return 'Files refreshed.';
    return `Files refreshed. ${total.toLocaleString('en-US')} ${noun(total, 'file')} in ${roots.toLocaleString('en-US')} output ${noun(roots, 'folder')}.`;
}

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

/** The concept's `.notice` with Later and Review scan. */
function noticeNode({ scope, onLater, onReview }) {
    const notice = el('div', undefined, 'notice');
    const text = el('div');
    text.append(el('h2', 'Bring older images into your prompt history'),
        el('p', 'The schema upgrades automatically. An optional metadata scan links existing positives and negatives without regenerating all thumbnails.'));
    const actions = el('div', undefined, 'row-actions');
    const later = createButton('Later');
    const review = createButton('Review scan', 'ca-primary');
    scope.listen(later, 'click', onLater);
    scope.listen(review, 'click', onReview);
    actions.append(later, review);
    notice.append(text, actions);
    return notice;
}

/**
 * "Add image source" (the concept's `simpleInput`, D127): the server checks the folder; a refused one keeps the
 * dialog open with the server's reason under the field. Enter in the field adds, as the Add button does.
 */
function openAddSource({ api, scope, trigger, listed, onAdd }) {
    const input = el('input');
    const reason = el('p', undefined, 'small');
    reason.setAttribute('role', 'alert');
    const cancel = createButton('Cancel');
    const add = createButton('Add', 'ca-primary');
    let open = true;
    const refuse = text => {
        reason.textContent = text;
        if (!reason.isConnected) actions.before(reason);
        input.setAttribute('aria-invalid', 'true');
        input.focus();
    };
    scope.listen(input, 'input', () => {
        reason.remove();
        input.removeAttribute('aria-invalid');
    });
    scope.listen(input, 'keydown', event => {
        if (event.key !== 'Enter' || event.isComposing) return;
        event.preventDefault();
        submit();
    });
    scope.listen(cancel, 'click', () => closeWorkspaceDialog());
    scope.listen(add, 'click', () => submit());
    async function submit() {
        const path = input.value.trim();
        if (!path) { input.focus(); return; }
        if (add.getAttribute('aria-disabled') === 'true') return;
        add.setAttribute('aria-disabled', 'true');
        try {
            const result = await api.post(VALIDATE_URL, { path }, { signal: scope.signal });
            if (!open) return;
            if (!result.valid) refuse(result.reason ?? VALIDATE_FAILED_TEXT);
            else if (listed().includes(result.path)) refuse(DUPLICATE_TEXT);
            else {
                closeWorkspaceDialog();
                onAdd(result);
            }
        } catch {
            if (open) refuse(VALIDATE_FAILED_TEXT);
        } finally {
            add.removeAttribute('aria-disabled');
        }
    }
    const actions = el('div', undefined, 'dialog-actions');
    actions.append(cancel, add);
    openContentDialog({ scope, title: 'Add image source', trigger, nodes: [field('Folder path', input, 'action-input'), actions],
        onClose: () => { open = false; } });
    input.focus();
}

/** The row's control (`data-setting`), wherever the Toolkit switch nests it. */
const controlOf = (row, id) => row.querySelector(`[data-setting="${id}"]`);

/**
 * Mount Sources in `container` (System's `.settings-layout`); returns the section's nodes. `activity` is the page's
 * Activity store (scans start through it), `drafts` the System drafts.
 */
export function mountSources(container, { api, activity, scope, drafts, notify = showWorkspaceToast, storage = defaultStorage,
    latestOnly = window.ApiClient.latestOnly, STALE = window.ApiClient.STALE }) {
    const statusRead = latestOnly({ lifetime: scope.signal });
    // undefined while read, null when the read failed, else GET /settings' `sources`.
    let sources;
    const nodes = [];
    const scanAll = trigger => openMetadataScan({ scope, store: activity, trigger, sources: sources?.effective?.length ?? 0 });

    if (!noticeDismissed(storage)) {
        const notice = noticeNode({ scope,
            onLater: () => { dismissNotice(storage); notice.remove(); },
            onReview: () => {
                dismissNotice(storage);
                // The pressed button leaves with the notice, so focus returns to the list's Add source.
                notice.remove();
                scanAll(addButton);
            } });
        nodes.push(notice);
    }

    const surface = el('section', undefined, 'surface');
    const heading = el('div', undefined, 'panel-heading');
    const addButton = createButton('+ Add source');
    heading.append(el('h2', 'Image sources'), addButton);
    const list = el('div');
    list.id = 'source-list';
    surface.append(heading, el('p', 'The same folders power browsing, monitoring and metadata enrichment.'), list);

    const roots = () => drafts.get('host', ROOTS_KEY) ?? sources?.configured?.map(source => source.path) ?? [];
    const setRoots = next => drafts.set('host', ROOTS_KEY, draftRoots(next, drafts.saved('host')[ROOTS_KEY]));
    scope.listen(addButton, 'click', () => openAddSource({ api, scope, trigger: addButton, listed: roots,
        onAdd: ({ path, label }) => {
            addedLabels.set(path, label);
            setRoots([...roots(), path]);
        } }));

    const monitoring = settingRow({ id: 'monitoring', label: 'Watch for new outputs',
        description: 'New files enter the same library. Existing scans do not count as new runs.', control: { type: 'toggle' },
        scope, onChange: value => drafts.set('host', 'monitoring_enabled', value) });
    const foreign = settingRow({ id: FOREIGN_RUNS.id, label: FOREIGN_RUNS.label, description: FOREIGN_RUNS.description,
        control: { type: 'toggle' }, scope, onChange: value => drafts.set('host', FOREIGN_RUNS.key, value) });
    const workers = settingRow({ id: 'workers', label: 'Worker threads',
        description: 'Balance background scans and thumbnail work against generation.', control: { type: 'range', value: 4 },
        scope, onChange: value => drafts.set('host', 'worker_threads', value) });
    const hostRows = [[monitoring, 'monitoring', 'monitoring_enabled'], [foreign, FOREIGN_RUNS.id, FOREIGN_RUNS.key],
        [workers, 'workers', 'worker_threads']]
        .map(([row, id, key]) => ({ input: controlOf(row, id), key, text: row.children[0], note: null }));
    for (const row of hostRows) row.input.dataset.key = row.key;
    surface.append(monitoring, foreign, workers,
        operationRow({ scope, title: 'Enrich existing metadata', description: 'Resolve both prompts, deduplicate recipes, preserve uncertain links.',
            label: 'Review scan', onClick: scanAll }),
        operationRow({ scope, title: 'Thumbnail generation', description: 'Generate missing previews; rebuilding all is a separate explicit option.',
            label: 'Review options', onClick: trigger => openThumbnailReview({ api, store: activity, scope, trigger }) }),
        // D98: the legacy gallery's Refresh, which rereads every source's file list.
        operationRow({ scope, title: 'Refresh files', description: 'Read every source again to find files added or removed outside ComfyUI.',
            label: 'Refresh', onClick: refresh }));
    nodes.push(surface);

    async function refresh(button) {
        if (button.getAttribute('aria-disabled') === 'true') return;
        button.setAttribute('aria-disabled', 'true');
        try {
            const result = await api.post(RESCAN_URL, {}, { signal: scope.signal });
            if (!scope.signal.aborted) notify(refreshedText(result));
        } catch {
            if (!scope.signal.aborted) notify(REFRESH_FAILED_TEXT);
        } finally {
            button.removeAttribute('aria-disabled');
        }
    }

    function sourceNode(row) {
        const actions = [];
        if (row.scan) {
            const scan = createButton(row.scan.label);
            scope.listen(scan, 'click', () => openMetadataScan({ scope, store: activity, trigger: scan, sources: 1,
                roots: [row.scan.root], paths: [row.scan.path], onStale: reread }));
            actions.push(scan);
        }
        if (row.remove !== null) {
            // D98: a plain button; nothing is removed until Save.
            const remove = createButton('Remove');
            scope.listen(remove, 'click', () => setRoots(roots().filter(path => path !== row.remove)));
            actions.push(remove);
        }
        const node = el('div', undefined, 'operation');
        // D128: the source the last save was refused for; the status says why.
        if (row.remove !== null && row.remove === drafts.invalidSource?.()) node.setAttribute('aria-invalid', 'true');
        const text = el('div');
        text.append(el('h3', row.title), el('p', row.description));
        const group = el('div', undefined, 'row-actions');
        group.append(...actions);
        node.append(text, group);
        return node;
    }

    function paintList() {
        if (sources === undefined) {
            const loading = el('p', LOADING_TEXT, 'small');
            loading.setAttribute('role', 'status');
            list.replaceChildren(loading);
            return;
        }
        if (sources === null) {
            const alert = el('p', LOAD_FAILED_TEXT);
            alert.setAttribute('role', 'alert');
            const retry = createButton('Retry');
            scope.listen(retry, 'click', () => {
                load();
                if (drafts.hostState?.() === 'failed') drafts.reloadHost();
            });
            list.replaceChildren(alert, retry);
            return;
        }
        const { rows, note } = sourceRows({ sources, saved: drafts.saved('host')[ROOTS_KEY], current: drafts.get('host', ROOTS_KEY) });
        list.replaceChildren(...rows.map(sourceNode), ...(note ? [el('p', note, 'small')] : []));
    }

    function paintControls() {
        const unavailable = drafts.hostState?.() === 'failed';
        for (const row of hostRows) {
            const value = drafts.get('host', row.key);
            row.input.disabled = value === undefined;
            if (row.key === FOREIGN_RUNS.key) {
                paintForeign(row, value, unavailable);
                continue;
            }
            if (row.key === 'worker_threads') {
                // D113: the range ends at the server's core count (the concept's fixed 16).
                const cpus = drafts.get('host', 'cpu_count');
                if (Number.isSafeInteger(cpus) && cpus >= 1) row.input.max = String(cpus);
                if (value !== undefined) row.input.value = String(value);
            } else if (value !== undefined) row.input.checked = value === true;
            if (unavailable && value === undefined) {
                if (!row.note) { row.note = el('p', HOST_UNAVAILABLE, 'small'); row.text.append(row.note); }
            } else if (row.note) { row.note.remove(); row.note = null; }
        }
    }

    // D147: disabled while monitoring is drafted off, with the reason under the description.
    function paintForeign(row, value, unavailable) {
        const stopping = sources?.effective?.some(source => source.watch === 'stopping') === true;
        const state = foreignRunsState({ value, monitoring: drafts.get('host', 'monitoring_enabled'), stopping });
        row.input.disabled = state.disabled;
        if (state.checked !== null) row.input.checked = state.checked;
        const note = unavailable && value === undefined ? HOST_UNAVAILABLE : state.note;
        if (note && row.note?.textContent !== note) {
            row.note?.remove();
            row.note = el('p', note, 'small');
            row.note.id = `${FOREIGN_RUNS.id}-note`;
            row.text.append(row.note);
        } else if (!note && row.note) { row.note.remove(); row.note = null; }
        // The note is the switch's description, so a screen reader says why it is disabled.
        if (row.note) row.input.setAttribute('aria-describedby', row.note.id);
        else row.input.removeAttribute('aria-describedby');
    }

    // The sources changed since this page read them (a refused per-source scan): read them and the saved values again.
    function reread() {
        load();
        drafts.reloadHost?.();
    }

    async function load() {
        sources = undefined;
        paintList();
        let answer;
        try {
            answer = await statusRead(signal => api.get(SETTINGS_URL, { signal, cache: 'no-store' }));
        } catch {
            answer = null;
        }
        if (answer === STALE || scope.signal.aborted) return;
        sources = answer?.sources ?? null;
        paintList();
        paintControls();
    }

    // A save (or a reread after a conflict) changed the saved sources or monitoring: read their state again. The first
    // host read on mount is not a change.
    const savedState = () => {
        const saved = drafts.saved('host');
        return saved[ROOTS_KEY] === undefined ? undefined : JSON.stringify([saved[ROOTS_KEY], saved.monitoring_enabled]);
    };
    let state = savedState();
    scope.cleanup(drafts.subscribe(() => {
        const previous = state;
        state = savedState();
        paintControls();
        if (previous !== undefined && state !== previous) load();
        else if (sources) paintList();
    }));
    paintControls();
    load();
    return nodes;
}
