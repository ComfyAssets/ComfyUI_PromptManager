/**
 * Saved views (#249 D16, maintainer ruling 2026-10-21): a named toolbar value plus its "Refine this view" filters, for
 * Images or the Prompt library, persisted through `/prompt_manager/saved-views`.
 *
 * - The toolbar's Save view opens the concept's dialog (app.js a1330–1346 through `simpleInput` a1138): "Save this
 *   view", "View name", `Search: <q>` or "My workspace view", Cancel / Save; an empty name is "Saved view".
 * - The sidebar SAVED VIEWS group lists each view (concept `nav()` a331–335) as `#/saved/<id>` (a1158–1162).
 * - A view's state always holds every key of its page (the API requires them all); a value its page no longer
 *   accepts is dropped to that key's default, so an opened view applies what it can.
 * - D51: an open view's heading Edit (maintainer ruling, beyond the concept): "Edit saved view" with "View name",
 *   Delete… (danger), Cancel and Save; Delete… asks inside the same dialog, never through `window.confirm`.
 */
import { createButton } from '../shared/toolkit/core.js';
import { openInputDialog, openContentDialog, closeWorkspaceDialog, field } from './workspace-dialogs.mjs';
import { showWorkspaceToast } from './toast.mjs';

export const SAVED_VIEWS_URL = '/prompt_manager/saved-views';
export const SAVED_TOAST = 'Saved view added to the sidebar.';
export const SAVE_FAILED_TOAST = 'The view could not be saved. Try again.';
export const RENAMED_TOAST = 'Saved view renamed.';
export const DELETED_TOAST = 'Saved view deleted.';
export const RENAME_FAILED_TOAST = 'The view could not be renamed. Try again.';
export const DELETE_FAILED_TOAST = 'The view could not be deleted. Try again.';
export const TOO_LARGE_TOAST = 'The view is too large to save. Shorten its search, folder or tags and try again.';
// The API's cap on a view's encoded state (database/saved_views.py MAX_STATE_BYTES), in UTF-8 bytes of compact JSON.
export const MAX_STATE_BYTES = 4096;
const MAX_NAME_LENGTH = 100;
const MAX_TAGS = 20;
const MAX_TAG_LENGTH = 50;
const CONTROL = /[\u0000-\u001f\u007f]/;

// The API's whitelist (database/saved_views.py _STATE_FIELDS), with each key's default.
const text = (limit, fallback = '') => ({ fallback, accepts: value => typeof value === 'string' && value.length <= limit && !CONTROL.test(value) });
const choice = (choices, fallback) => ({ fallback, accepts: value => choices.includes(value) });
const tags = { fallback: [], accepts: value => Array.isArray(value) && value.length <= MAX_TAGS && value.every(tag =>
    typeof tag === 'string' && tag.trim() && tag.length <= MAX_TAG_LENGTH && !tag.includes(',') && !CONTROL.test(tag)) };
const FIELDS = Object.freeze({
    images: Object.freeze({
        q: text(500), category: text(100), sort: choice(['newest', 'oldest', 'name', 'name-desc', 'rating'], 'newest'),
        source: { fallback: 'all', accepts: value => typeof value === 'string' && /^(all|0|[1-9][0-9]{0,8})$/.test(value) },
        folder: text(1024), media: choice(['all', 'image', 'video'], 'all'), tags,
        // A view saved on Favorites (#249 D51) reopens Images filtered to favorites.
        favorites: { fallback: false, accepts: value => typeof value === 'boolean' },
        // #331 D140: a view saved on a collection page reopens that collection ('' or 1 to 9 digits, no leading zero).
        collection: { fallback: '', accepts: value => typeof value === 'string' && /^(|[1-9][0-9]{0,8})$/.test(value) },
    }),
    library: Object.freeze({
        q: text(500), category: text(100), sort: choice(['newest', 'oldest', 'name', 'rating'], 'newest'), tags,
    }),
});

export const SAVED_VIEW_PAGES = Object.freeze(Object.keys(FIELDS));

/** The page's full state: every key, each saved value its page accepts, the default otherwise. */
export function normalizeViewState(page, state) {
    const fields = FIELDS[page];
    if (!fields) throw new TypeError(`Unknown saved-view page: ${page}`);
    const source = state && typeof state === 'object' && !Array.isArray(state) ? state : {};
    const normalized = Object.fromEntries(Object.entries(fields).map(([key, field]) => {
        const value = field.accepts(source[key]) ? source[key] : field.fallback;
        return [key, Array.isArray(value) ? [...value] : value];
    }));
    // Favorites and a collection are never both a view's filter: the collection wins, as the listing does (D140).
    if (normalized.favorites === true && normalized.collection) normalized.favorites = false;
    return normalized;
}

/** True when the state's compact JSON fits the API's 4 KB cap (the server's encoding: no spaces, UTF-8). */
export function stateFits(state) {
    return new TextEncoder().encode(JSON.stringify(state)).length <= MAX_STATE_BYTES;
}

/** The error `create()` rejects with when the state is over the cap; nothing is sent. */
export class SavedViewTooLarge extends RangeError {
    constructor() { super('Saved view state is over 4 KB'); this.name = 'SavedViewTooLarge'; }
}

/** The concept's default name (a1336): `Search: <q>` while there is a search, else "My workspace view". */
export function defaultViewName(q) {
    const search = typeof q === 'string' ? q.trim() : '';
    return (search ? `Search: ${search}` : 'My workspace view').slice(0, MAX_NAME_LENGTH);
}

/** One listed view, or null when the response row is not a usable view. */
export function parseSavedView(row) {
    if (!Number.isSafeInteger(row?.id) || row.id < 1 || !SAVED_VIEW_PAGES.includes(row.page)) return null;
    if (typeof row.name !== 'string' || !row.name.trim()) return null;
    return { id: row.id, page: row.page, name: row.name, state: normalizeViewState(row.page, row.state) };
}

/** The listing's views in the server's order, skipping any row that is not a usable view. */
export function parseSavedViews(data) {
    if (!Array.isArray(data?.views)) throw new TypeError('Invalid saved views');
    return data.views.map(parseSavedView).filter(Boolean);
}

/** The route of one view. */
export const savedViewHash = id => `#/saved/${id}`;

/** Images filters (images.mjs `filters`) from a saved state, and the state of the current filters. */
export function imagesFilters(state) {
    const view = normalizeViewState('images', state);
    return { q: view.q, category: view.category, sort: view.sort, root: view.source, subfolder: view.folder, media: view.media,
        tags: view.tags, favorites: view.favorites, collection: view.collection };
}
export function imagesViewState(filters) {
    return normalizeViewState('images', { q: filters.q, category: filters.category, sort: filters.sort, source: filters.root,
        folder: filters.subfolder, media: filters.media, tags: filters.tags, favorites: filters.favorites === true,
        collection: filters.collection == null ? '' : String(filters.collection) });
}

/** Library shelf filters (library.mjs `filters`) from a saved state, and the state of the current filters. */
export function libraryFilters(state) {
    const view = normalizeViewState('library', state);
    return { text: view.q, category: view.category, sort: view.sort, tags: view.tags };
}
export function libraryViewState(filters) {
    return normalizeViewState('library', { q: filters.text, category: filters.category, sort: filters.sort, tags: filters.tags });
}

/**
 * The shell's list of saved views: `load()` re-reads it (a newer read supersedes an older one), `create()` saves one
 * and re-reads the list, `rename(id, name)` and `remove(id)` change one and re-read it (a failure re-reads it too), `subscribe(fn)` reports `{views, status}` on every change, and `settled()` resolves once no
 * read is in flight. `status` is 'loading', 'ready' or 'error'; a failed read keeps the last listed views.
 */
export function createSavedViews(api, signal) {
    let views = [], status = 'loading', request = null;
    const listeners = new Set();
    let waiting = [];
    const emit = () => {
        const snapshot = { views, status };
        for (const listener of listeners) listener(snapshot);
        if (status !== 'loading') { const done = waiting; waiting = []; for (const resolve of done) resolve(snapshot); }
    };
    function load() {
        request?.abort();
        const controller = new AbortController();
        request = controller;
        const abort = () => controller.abort();
        signal?.addEventListener('abort', abort, { once: true });
        status = 'loading';
        emit();
        return api.get(SAVED_VIEWS_URL, { signal: controller.signal, cache: 'no-store' }).then(data => {
            if (request !== controller) return;
            views = parseSavedViews(data);
            status = 'ready';
        }).catch(() => {
            if (request !== controller) return;
            status = 'error';
        }).finally(() => {
            signal?.removeEventListener('abort', abort);
            if (request !== controller || signal?.aborted) return;
            request = null;
            emit();
        });
    }
    async function create(page, name, state) {
        const full = normalizeViewState(page, state);
        if (!stateFits(full)) throw new SavedViewTooLarge();
        const data = await api.post(SAVED_VIEWS_URL, { page, name, state: full }, { signal });
        const view = parseSavedView(data?.view);
        if (!view) throw new TypeError('Invalid saved view');
        await load();
        return view;
    }
    /** Run one change of a listed view, then re-read the list whatever its outcome (another tab may have changed it). */
    async function change(send) {
        try { return await send(); } finally { await load(); }
    }
    const viewUrl = id => {
        if (!Number.isSafeInteger(id) || id < 1) throw new TypeError('Invalid saved view id');
        return `${SAVED_VIEWS_URL}/${id}`;
    };
    function rename(id, name) {
        const url = viewUrl(id);
        return change(async () => {
            const view = parseSavedView((await api.patch(url, { name }, { signal }))?.view);
            if (!view) throw new TypeError('Invalid saved view');
            return view;
        });
    }
    function remove(id) {
        const url = viewUrl(id);
        return change(async () => {
            try {
                await api.del(url, { signal });
            } catch (error) {
                // Already gone (another tab deleted it): the outcome the user asked for.
                if (error?.status !== 404) throw error;
            }
            return id;
        });
    }
    return {
        load, create, rename, remove,
        get views() { return views; },
        get status() { return status; },
        find: id => views.find(view => view.id === id) ?? null,
        settled: () => status === 'loading' ? new Promise(resolve => waiting.push(resolve)) : Promise.resolve({ views, status }),
        subscribe(listener) {
            listeners.add(listener);
            return () => listeners.delete(listener);
        },
    };
}

/**
 * The concept's Save view (a1330–1346): the name dialog, then the save. `onSave(name)` returns the save's promise;
 * the toast reports either outcome.
 */
export function openSaveViewDialog({ scope, trigger, q, onSave }) {
    openInputDialog({ scope, trigger, title: 'Save this view', label: 'View name', action: 'Save', value: defaultViewName(q),
        maxLength: MAX_NAME_LENGTH, fallback: 'Saved view', onApply: async name => {
            try {
                await onSave(name);
                showWorkspaceToast(SAVED_TOAST);
            } catch (error) {
                if (!scope.signal.aborted) showWorkspaceToast(error instanceof SavedViewTooLarge ? TOO_LARGE_TOAST : SAVE_FAILED_TOAST);
            }
        } });
}

/**
 * D51: the open view's Edit dialog. "View name" (Save keeps the dialog open on an empty name; an unchanged one closes it), then Delete… (danger), Cancel
 * and Save. Delete… swaps the same dialog to its confirmation, Cancel / Delete (its Cancel closes the dialog), so nothing is removed by one press and
 * no browser `confirm()` appears. `onRename(name)` and `onDelete()` return the store's promise; the toast reports either
 * outcome, and the shell navigates after a delete.
 */
export function openEditViewDialog({ scope, trigger, view, onRename, onDelete }) {
    const input = document.createElement('input');
    input.value = view.name;
    input.maxLength = MAX_NAME_LENGTH;
    const remove = createButton('Delete…', 'danger');
    const cancel = createButton('Cancel');
    const save = createButton('Save', 'ca-primary');
    const actions = document.createElement('div');
    actions.className = 'dialog-actions';
    actions.append(remove, cancel, save);
    cancel.addEventListener('click', () => closeWorkspaceDialog());
    save.addEventListener('click', async () => {
        const name = input.value.trim();
        if (!name) { input.focus(); return; }
        closeWorkspaceDialog();
        if (name === view.name) return;
        try {
            await onRename(name);
            showWorkspaceToast(RENAMED_TOAST);
        } catch {
            if (!scope.signal.aborted) showWorkspaceToast(RENAME_FAILED_TOAST);
        }
    });
    remove.addEventListener('click', () => confirmDelete({ scope, trigger, view, onDelete }));
    openContentDialog({ scope, trigger, title: 'Edit saved view', nodes: [field('View name', input, 'action-input'), actions] });
    input.focus();
    input.select();
}

function confirmDelete({ scope, trigger, view, onDelete }) {
    const text = document.createElement('p');
    text.textContent = `Delete “${view.name}”? Only the saved view is removed; its images and prompts stay.`;
    const actions = document.createElement('div');
    actions.className = 'dialog-actions';
    const cancel = createButton('Cancel');
    const confirmButton = createButton('Delete', 'danger');
    actions.append(cancel, confirmButton);
    cancel.addEventListener('click', () => closeWorkspaceDialog());
    confirmButton.addEventListener('click', async () => {
        closeWorkspaceDialog();
        try {
            await onDelete();
            showWorkspaceToast(DELETED_TOAST);
        } catch {
            if (!scope.signal.aborted) showWorkspaceToast(DELETE_FAILED_TOAST);
        }
    });
    openContentDialog({ scope, trigger, title: 'Delete saved view', nodes: [text, actions] });
    cancel.focus();
}
