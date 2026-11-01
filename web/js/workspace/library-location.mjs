/**
 * Where the library lives (#249 System S6b, D129): the workspace banner, System › Overview's "Library location" row,
 * the Move library review and its start.
 *
 * - The server says where the database is and why (`GET /prompt_manager/system/library` and the Overview's
 *   `location`): `default` (ComfyUI's user folder), `environment` (PROMPT_MANAGER_DATA_DIR), `config`
 *   (config.json's database path) or `node_folder`, where an update or reinstall can delete it.
 * - D129: while a node-folder library is not kept there, the shell shows the concept's `.notice` (`h2` + `p` +
 *   `.row-actions`) above every page: "Keep it here" saves the choice on the server (the banner stops; the Overview row
 *   stays), "Move library" opens the review. The concept has no such notice; its markup and classes are the Sources
 *   notice's (app.js 680).
 * - The review is the shared record-changing review (`openReviewDialog`): From, To, Size, what moves with it and what
 *   happens to the old files. Move library starts the server's exclusive "Move library" job, tracks it in Activity and
 *   opens its view; a refusal (another job running, no room, a library already there) says why and nothing starts.
 * - When a Move library job ends, the banner and the Overview read the location again.
 */
import { createButton } from '../shared/toolkit/core.js';
import { busyToast } from './activity-scan.mjs';
import { closeWorkspaceDialog, openContentDialog, openReviewDialog } from './workspace-dialogs.mjs';
import { navigateTo } from './state.mjs';
import { showWorkspaceToast } from './toast.mjs';

export const LIBRARY_URL = '/prompt_manager/system/library';
export const MOVE_URL = `${LIBRARY_URL}/move`;
export const KEEP_URL = `${LIBRARY_URL}/keep`;
export const MOVE_KIND = 'library_move';

export const BANNER_TITLE = 'Your library is inside the node folder';
const BANNER_LEAD = 'Your library is stored inside the node folder, where an update or reinstall can delete it.';
/** Where Move library puts it, by the server's `target_source`. */
export const TARGET_TEXT = Object.freeze({
    default: 'ComfyUI\'s user folder',
    environment: 'the folder PROMPT_MANAGER_DATA_DIR names',
});
/** The notice's sentence: it names the actual target. */
export function bannerText(location) {
    const target = TARGET_TEXT[location?.target_source] ?? TARGET_TEXT.default;
    return `${BANNER_LEAD} Move it to ${target}: it is copied and verified first, and nothing is deleted.`;
}
export const BANNER_TEXT = bannerText({ target_source: 'default' });
export const KEEP_LABEL = 'Keep it here';
export const MOVE_LABEL = 'Move library';
export const ROW_TITLE = 'Library location';
export const START_FAILED = 'Move library could not start. Try again.';
export const KEEP_FAILED = 'Your choice could not be saved. Try again.';

/** Why the library is where it is, by `source`. */
export const SOURCE_TEXT = Object.freeze({
    default: 'ComfyUI\'s user folder, which updates and reinstalls of the node leave alone',
    environment: 'The folder PROMPT_MANAGER_DATA_DIR names',
    config: 'The database path set in config.json',
    node_folder: 'The node folder, where an update or reinstall can delete it',
});

/** A `problem` code as a sentence. */
export const PROBLEM_TEXT = Object.freeze({
    'data-dir-not-absolute': 'PROMPT_MANAGER_DATA_DIR is not an absolute folder, so it was ignored.',
    'data-dir-served': 'PROMPT_MANAGER_DATA_DIR names a folder whose files are served over the web, so it was ignored.',
    'data-dir-empty': 'PROMPT_MANAGER_DATA_DIR has no library yet; Move library puts this one there.',
    'move-unfinished': 'An earlier move did not finish; this library is still the one in use.',
    'inside-node-folder': 'It is inside the node folder, where an update or reinstall deletes it, config.json and its '
        + 'CivitAI key included. Set database.default_path or PROMPT_MANAGER_DATA_DIR to a folder outside it.',
});

const number = value => Number(value).toLocaleString('en-US');

/** "1.6 GB" or "120.0 MB" or "512 KB". */
export function sizeText(bytes) {
    if (!Number.isFinite(bytes) || bytes < 0) return 'Unknown';
    if (bytes >= 2 ** 30) return `${(bytes / 2 ** 30).toLocaleString('en-US', { maximumFractionDigits: 1, minimumFractionDigits: 1 })} GB`;
    if (bytes >= 2 ** 20) return `${(bytes / 2 ** 20).toLocaleString('en-US', { maximumFractionDigits: 1, minimumFractionDigits: 1 })} MB`;
    return `${number(Math.ceil(bytes / 1024))} KB`;
}

/** Whether `location` is a well-formed server answer. */
export function isLocation(location) {
    return Boolean(location) && typeof location === 'object' && typeof location.path === 'string'
        && Object.hasOwn(SOURCE_TEXT, location.source) && typeof location.movable === 'boolean';
}

/** The Overview row's description: the path, why it is there, the user's choice and any problem. */
export function locationDescription(location) {
    const parts = [`${location.path} · ${SOURCE_TEXT[location.source]}.`];
    if (location.movable && location.keep) parts.push('You chose to keep it here.');
    const problem = PROBLEM_TEXT[location.problem];
    if (problem) parts.push(problem);
    return parts.join(' ');
}

/** The review's `[term, value]` rows. */
export function reviewGrid(location) {
    return [
        ['From', location.path],
        ['To', `${location.target}/prompts.db`],
        ['Size', sizeText(location.size_bytes)],
        ['Moves with it', location.config_moves === false
            ? 'The database backups (config.json stays where PROMPT_MANAGER_CONFIG_PATH names it)'
            : 'config.json and the database backups'],
        ['Old files', 'Renamed *.moved-to-…, never deleted'],
        // #320 D201: previews kept in one folder do not move with the library (D8A).
        ...(typeof location.thumbnails_folder === 'string' && location.thumbnails_folder
            ? [['Thumbnails', `Stay in ${location.thumbnails_folder}. Move them in System › Sources & scans.`]] : []),
    ];
}

export const REVIEW_SENTENCE = 'The copy is verified before Prompt Manager switches to it. No other job can run until it '
    + 'finishes, and closing this page does not stop it.';

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

/**
 * Start the Move library job, follow it in Activity and open its view. A busy server toasts the D57 link to the running
 * job; any other refusal says the server's reason. Nothing is said once `scope` ended.
 */
export async function startLibraryMove({ api, activity, scope, notify = showWorkspaceToast, navigate = navigateTo }) {
    try {
        const { job } = await api.post(MOVE_URL, {}, { signal: scope.signal });
        if (scope.signal.aborted) return null;
        const tracked = activity.track(job);
        navigate(`#/activity/${tracked.id}`);
        return tracked;
    } catch (error) {
        if (scope.signal.aborted) return null;
        if (error?.status === 409 && error?.code === 'busy') {
            await activity.load();
            if (!scope.signal.aborted) notify(busyToast(activity.running()[0] ?? null));
            return null;
        }
        notify(typeof error?.code === 'string' && error.message ? error.message : START_FAILED);
        return null;
    }
}

/** The Move library review over `location`; Move library starts the job. */
export function openMoveReview({ api, activity, scope, location, trigger = null, notify = showWorkspaceToast,
    navigate = navigateTo }) {
    return openReviewDialog({ scope, trigger, title: 'Move library', grid: reviewGrid(location), sentence: REVIEW_SENTENCE,
        cancelLabel: 'Cancel', primary: { label: MOVE_LABEL },
        onStart: () => { startLibraryMove({ api, activity, scope, notify, navigate }); } });
}

/** Save "Keep it here" (`keep` true) or take it back; resolves the new location, or null when it was not saved. */
export async function keepLibrary({ api, scope, keep = true, notify = showWorkspaceToast }) {
    try {
        const { location } = await api.post(KEEP_URL, { keep }, { signal: scope.signal });
        return isLocation(location) ? location : null;
    } catch (error) {
        if (!scope.signal.aborted) notify(KEEP_FAILED);
        return null;
    }
}

/** The details of a library that does not move: where it is, why, and what lives beside it. */
export function openLocationDetails({ scope, location, trigger = null }) {
    const list = el('dl', undefined, 'meta-grid');
    for (const [term, value] of [['Database', location.path], ['Why here', SOURCE_TEXT[location.source]],
        ['Beside it', 'config.json, the database backups and the logs'], ['Size', sizeText(location.size_bytes)]]) {
        const entry = el('div');
        entry.append(el('dt', term), el('dd', value));
        list.append(entry);
    }
    const close = createButton('Close');
    scope.listen(close, 'click', () => closeWorkspaceDialog());
    const actions = el('div', undefined, 'dialog-actions');
    actions.append(close);
    const nodes = [list];
    const problem = PROBLEM_TEXT[location.problem];
    if (problem) nodes.push(el('p', problem, 'small'));
    nodes.push(actions);
    openContentDialog({ scope, title: ROW_TITLE, trigger, nodes });
}

/** D129: the Overview's "Library location" `.operation` row; Move library when it can move, else Details. */
export function locationRow(location, { api, activity, scope, notify = showWorkspaceToast, navigate = navigateTo }) {
    const row = el('div', undefined, 'operation');
    const text = el('div');
    text.append(el('h3', ROW_TITLE), el('p', locationDescription(location)));
    const actions = el('div', undefined, 'row-actions');
    const action = createButton(location.movable ? MOVE_LABEL : 'Details');
    scope.listen(action, 'click', () => {
        if (location.movable) openMoveReview({ api, activity, scope, location, trigger: action, notify, navigate });
        else openLocationDetails({ scope, location, trigger: action });
    });
    actions.append(action);
    row.append(text, actions);
    return row;
}

/** D129: the concept's `.notice` for a node-folder library: Keep it here and Move library. */
export function libraryNotice({ scope, location = null, onKeep, onMove }) {
    const notice = el('div', undefined, 'notice');
    const text = el('div');
    text.append(el('h2', BANNER_TITLE), el('p', bannerText(location)));
    const keep = createButton(KEEP_LABEL);
    const move = createButton(MOVE_LABEL, 'ca-primary');
    scope.listen(keep, 'click', () => onKeep(keep));
    scope.listen(move, 'click', () => onMove(move));
    const actions = el('div', undefined, 'row-actions');
    actions.append(keep, move);
    notice.append(text, actions);
    return notice;
}

/** Calls `onEnded()` whenever a Move library job this page sees stops running. Returns the unsubscribe. */
export function onMoveEnded(activity, onEnded) {
    const running = new Set();
    return activity.subscribe(() => {
        let ended = false;
        for (const job of activity.jobs()) {
            if (job.kind !== MOVE_KIND) continue;
            if (job.state === 'running') running.add(job.id);
            else if (running.delete(job.id)) ended = true;
        }
        if (ended) onEnded();
    });
}

/**
 * Mount the banner in `container` (hidden until the server offers the move). It reads the location once, again when a
 * Move library job ends, and hides for good this page once the user keeps the library where it is.
 */
export function mountLibraryBanner(container, { api, activity, scope, notify = showWorkspaceToast, navigate = navigateTo,
    latestOnly = window.ApiClient.latestOnly, STALE = window.ApiClient.STALE }) {
    const read = latestOnly({ lifetime: scope.signal });
    const show = location => {
        container.replaceChildren();
        container.hidden = !(isLocation(location) && location.offer === true);
        if (container.hidden) return;
        container.append(libraryNotice({ scope, location,
            onKeep: async button => {
                button.setAttribute('aria-disabled', 'true');
                const saved = await keepLibrary({ api, scope, notify });
                if (scope.signal.aborted) return;
                button.removeAttribute('aria-disabled');
                if (saved) show(saved);
            },
            onMove: button => openMoveReview({ api, activity, scope, location, trigger: button, notify, navigate }) }));
    };
    async function load() {
        try {
            const answer = await read(signal => api.get(LIBRARY_URL, { signal, cache: 'no-store' }));
            if (answer === STALE || scope.signal.aborted) return;
            show(answer.location);
        } catch {
            // The banner is advisory: an unreadable location shows nothing (the Overview row reports it).
            if (!scope.signal.aborted) show(null);
        }
    }
    container.hidden = true;
    scope.cleanup(onMoveEnded(activity, load));
    load();
    return { reload: load };
}
