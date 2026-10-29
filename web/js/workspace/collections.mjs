/**
 * Collections (#331): named, hand-picked groups of images, persisted through `/prompt_manager/collections*`.
 *
 * - D136: the sidebar COLLECTIONS group (the concept's `#collections-nav`, index.html 28–31, app.js 336–339) lists
 *   each collection as `#/collection/<id>` with its listed-member count, without the concept's "PROPOSED" badge.
 * - D137: a collection page is Images filtered to the collection; its Edit renames or deletes it through the saved
 *   views' Edit dialog (D51) with collection nouns.
 * - This module holds the client store (`createCollections`), the route hash, the name rule and every user-facing
 *   string of the design (§7.4, §8), so the selection bar, the inspector and the shell say the same things.
 */
import { noun } from './plural.mjs';
import { openEditDialog } from './saved-views.mjs';

export const COLLECTIONS_URL = '/prompt_manager/collections';
export const COLLECTION_ITEMS_URL = `${COLLECTIONS_URL}/items`;
export const COLLECTION_MEMBERSHIP_URL = `${COLLECTIONS_URL}/membership`;
export const MAX_COLLECTION_NAME_LENGTH = 100;
// The routes' cap on one add or remove (the bulk tagging cap, MAX_BULK_IMAGES).
export const MAX_COLLECTION_ITEMS = 500;
// Counts are re-read once after a run of confirmed changes settles, as the Favorites count (#249 M1).
export const COLLECTION_COUNT_DELAY_MS = 300;

// Strings (design §7.4 and §8).
export const ADD_DIALOG_TITLE = 'Add selected images to collection';
export const ADD_DIALOG_LABEL = 'Collection name';
export const ADD_DIALOG_ACTION = 'Add';
export const TOO_MANY_TOAST = `Select at most ${MAX_COLLECTION_ITEMS} images.`;
export const FILES_NOT_FOUND_TOAST = 'None of the selected images could be found. Refresh the images and try again.';
export const SOURCE_CHANGED_TOAST = 'The sources changed. Refresh the images and try again.';
export const ADD_FAILED_TOAST = 'The images could not be added. Try again.';
export const REMOVE_FAILED_TOAST = 'The images could not be removed. Try again.';
export const COLLECTION_GONE_TOAST = 'That collection no longer exists.';
export const RENAMED_TOAST = 'Collection renamed.';
export const RENAME_FAILED_TOAST = 'The collection could not be renamed. Try again.';
export const DELETED_TOAST = 'Collection deleted.';
export const DELETE_FAILED_TOAST = 'The collection could not be deleted. Try again.';
export const DELETED_PAGE_TEXT = 'This collection no longer exists.';
export const OPEN_IMAGES_LINK = 'Open Images';
export const VIEW_COLLECTION_DELETED = 'The collection this view uses was deleted.';
export const LIST_FAILED_TEXT = 'Collections could not be loaded. Your saved data has not changed.';
export const COLLECTIONS_UPDATED_TOAST = 'Collections updated.';
export const UPDATE_FAILED_TEXT = 'The collections could not be updated. Try again.';
export const MEMBERSHIP_LOADING_TEXT = 'Loading collections…';
export const MEMBERSHIP_FAILED_TEXT = 'Collections could not be loaded.';
export const NO_COLLECTIONS_TEXT = 'No collections yet. Name one below.';

const CONTROL = /[\u0000-\u001f\u007f]/;
const isCount = value => Number.isSafeInteger(value) && value >= 0;
const isId = value => Number.isSafeInteger(value) && value >= 1 && value <= 999_999_999;

/** The route of one collection. */
export function collectionHash(id) {
    if (!isId(id)) throw new TypeError('Invalid collection id');
    return `#/collection/${id}`;
}

/**
 * A name as the database stores it (design §1): trimmed, each run of whitespace one space, 1 to 100 characters
 * without control characters; null otherwise. Tabs and newlines are whitespace, so they collapse.
 */
export function normalizeCollectionName(value) {
    if (typeof value !== 'string') return null;
    const name = value.trim().replace(/\s+/g, ' ');
    return name.length >= 1 && name.length <= MAX_COLLECTION_NAME_LENGTH && !CONTROL.test(name) ? name : null;
}

/** One listed collection, or null when the row is not usable. `count` and `missing` are null unless counts. */
export function parseCollection(row) {
    if (!isId(row?.id) || typeof row.name !== 'string' || !row.name.trim()) return null;
    const counted = isCount(row.count) && isCount(row.missing);
    return { id: row.id, name: row.name, count: counted ? row.count : null, missing: counted ? row.missing : null };
}

/** The listing's collections in the server's order (name, then id), skipping unusable rows. */
export function parseCollections(data) {
    if (!Array.isArray(data?.collections)) throw new TypeError('Invalid collections');
    return data.collections.map(parseCollection).filter(Boolean);
}

/** The sidebar's order while a re-read is pending: an approximation of the server's name key, then id. */
export const collectionNameKey = name => name.normalize('NFC').toLowerCase().normalize('NFC');
const nameKey = collectionNameKey;
const ordered = list => [...list].sort((a, b) => {
    const left = nameKey(a.name), right = nameKey(b.name);
    return left < right ? -1 : left > right ? 1 : a.id - b.id;
});

/** "Added N images to “X”." with what was already in it and what could not be found. */
export function addedMessage({ collection, added, already, notFound }) {
    if (added === 0 && notFound === 0) return `All selected images were already in “${collection.name}”.`;
    let text = `Added ${added} ${noun(added, 'image')} to “${collection.name}”.`;
    if (already > 0) text += already === 1 ? ' 1 was already in it.' : ` ${already} were already in it.`;
    if (notFound > 0) text += ` ${notFound} could not be found.`;
    return text;
}

export function removedMessage(name, removed) {
    return `Removed ${removed} ${noun(removed, 'image')} from “${name}”.`;
}

export function nameTakenMessage(name) {
    return `A collection named “${name}” already exists.`;
}

/** The Delete confirmation, with the stored count (listed plus missing) when the counts are known. */
export function deleteQuestion({ name, count, missing }) {
    if (!isCount(count) || !isCount(missing)) {
        return `Delete “${name}”? Its images stay where they are; only the collection is removed.`;
    }
    const stored = count + missing;
    return stored === 1
        ? `Delete “${name}”? Its 1 image stays where it is; only the collection is removed.`
        : `Delete “${name}”? Its ${stored} images stay where they are; only the collection is removed.`;
}

/** The collection page's note about members its sources do not list now (MC3); empty when there are none. */
export function missingNote(missing) {
    if (!isCount(missing) || missing === 0) return '';
    return missing === 1
        ? '1 image in this collection is not in your current sources.'
        : `${missing} images in this collection are not in your current sources.`;
}

export const isCollectionGone = error => error?.code === 'collection-not-found';

/** A failed add or remove's toast: the routes' error codes, else `fallback`. */
export function writeFailure(error, fallback) {
    if (isCollectionGone(error)) return COLLECTION_GONE_TOAST;
    if (error?.code === 'source-changed') return SOURCE_CHANGED_TOAST;
    if (error?.code === 'files-not-found') return FILES_NOT_FOUND_TOAST;
    return fallback;
}

/** The Edit dialog's mapping of a failed rename of `name` (openEditDialog `renameError`). */
export function renameFailure(name) {
    return error => {
        if (error?.code === 'name-taken') return { field: nameTakenMessage(name) };
        if (isCollectionGone(error)) return { toast: COLLECTION_GONE_TOAST, close: true };
        return { toast: RENAME_FAILED_TOAST };
    };
}

/** The collection nouns of the shared Edit dialog (D51, D137). */
export const COLLECTION_NOUNS = Object.freeze({
    title: 'Edit collection', label: 'Collection name', deleteTitle: 'Delete collection', deleteText: deleteQuestion,
    renamed: RENAMED_TOAST, renameFailed: RENAME_FAILED_TOAST, deleted: DELETED_TOAST, deleteFailed: DELETE_FAILED_TOAST,
});

/**
 * D137: the open collection's Edit. A taken name stays in the dialog under the field; a failed rename keeps it open;
 * a collection deleted meanwhile closes it and runs `onGone()` after the toast. `onRename(name)` sends the normalized
 * name; a delete's 404 counts as deleted (the store's `delete`).
 */
export function openEditCollectionDialog({ scope, trigger, collection, onRename, onDelete, onGone = () => {} }) {
    let attempted = '';
    const mapping = error => {
        const outcome = renameFailure(attempted)(error);
        if (outcome.close) queueMicrotask(onGone);
        return outcome;
    };
    openEditDialog({ scope, trigger, item: collection, nouns: COLLECTION_NOUNS, onDelete, renameError: mapping,
        onRename: name => {
            attempted = normalizeCollectionName(name) ?? name;
            return onRename(attempted);
        } });
}

function fileTargets(items) {
    if (!Array.isArray(items) || items.length < 1 || items.length > MAX_COLLECTION_ITEMS) {
        throw new TypeError('A collection change needs 1 to 500 files');
    }
    return items.map(item => {
        if (!Number.isSafeInteger(item?.root_index) || item.root_index < 0 || typeof item.root_scope !== 'string' ||
            !item.root_scope || typeof item.path !== 'string' || !item.path) {
            throw new TypeError('Invalid collection file');
        }
        return { root_index: item.root_index, root_scope: item.root_scope, path: item.path };
    });
}

function requireId(id) {
    if (!isId(id)) throw new TypeError('Invalid collection id');
    return id;
}

function requireName(name) {
    const normalized = normalizeCollectionName(name);
    if (normalized === null) throw new TypeError('Invalid collection name');
    return normalized;
}

/** The `{id, name}` a write answered with. */
function answered(data) {
    const collection = parseCollection(data?.collection);
    if (!collection) throw new TypeError('Invalid collection');
    return { id: collection.id, name: collection.name };
}

function countOf(value) {
    if (!isCount(value)) throw new TypeError('Invalid collection count');
    return value;
}

/**
 * The shell's collection list. `load()` re-reads it (a newer read supersedes an older one), `subscribe(fn)` reports
 * `{collections, status}` on every change, `settled()` resolves once no read is in flight; `status` is 'loading',
 * 'ready' or 'error' and a failed read keeps the last list. `add`, `remove`, `rename` and `delete` change the listed
 * rows at once and re-read the counts once, 300 ms after the last confirmed change; a write to a collection that is
 * gone (404 `collection-not-found`) re-reads the list at once and rejects. `timers` is a test seam.
 */
export function createCollections(api, signal, { setTimer = setTimeout, clearTimer = clearTimeout } = {}) {
    let collections = [], status = 'loading', request = null, timer = 0;
    const listeners = new Set();
    let waiting = [];
    const emit = () => {
        const snapshot = { collections, status };
        for (const listener of listeners) listener(snapshot);
        if (status !== 'loading') { const done = waiting; waiting = []; for (const resolve of done) resolve(snapshot); }
    };
    const stopTimer = () => { if (timer) clearTimer(timer); timer = 0; };
    signal?.addEventListener('abort', () => { stopTimer(); request?.abort(); }, { once: true });
    function load() {
        if (signal?.aborted) return Promise.resolve();
        request?.abort();
        const controller = new AbortController();
        request = controller;
        status = 'loading';
        emit();
        return api.get(COLLECTIONS_URL, { signal: controller.signal, cache: 'no-store' }).then(data => {
            if (request !== controller) return;
            collections = parseCollections(data);
            status = 'ready';
        }).catch(() => {
            if (request !== controller) return;
            status = 'error';
        }).finally(() => {
            if (request !== controller || signal?.aborted) return;
            request = null;
            emit();
        });
    }
    function changed(next) {
        collections = ordered(next);
        emit();
        stopTimer();
        timer = setTimer(() => { timer = 0; load(); }, COLLECTION_COUNT_DELAY_MS);
    }
    /** Send one write; a collection found gone meanwhile re-reads the list before the error reaches the caller. */
    async function send(write) {
        try {
            return await write();
        } catch (error) {
            if (isCollectionGone(error)) load();
            throw error;
        }
    }
    async function add(target, items) {
        const byId = target?.collectionId !== undefined, byName = target?.name !== undefined;
        if (byId === byName) throw new TypeError('A collection is named by its id or a name');
        const body = byId ? { collection_id: requireId(target.collectionId) } : { name: requireName(target.name) };
        body.items = fileTargets(items);
        const data = await send(() => api.post(COLLECTION_ITEMS_URL, body, { signal }));
        const result = { collection: answered(data), created: data.created === true, added: countOf(data.added),
            already: countOf(data.already), notFound: countOf(data.not_found) };
        const { id, name } = result.collection;
        changed(collections.some(item => item.id === id)
            ? collections.map(item => item.id === id ? { ...item, name } : item)
            : [...collections, { id, name, count: null, missing: null }]);
        return result;
    }
    async function remove(id, items) {
        const body = { collection_id: requireId(id), items: fileTargets(items) };
        const data = await send(() => api.del(COLLECTION_ITEMS_URL, { signal, body }));
        const result = { collection: answered(data), removed: countOf(data.removed) };
        changed(collections);
        return result;
    }
    async function membership(identity) {
        const [file] = fileTargets([identity]);
        const query = new URLSearchParams({ root_index: String(file.root_index), root_scope: file.root_scope, path: file.path });
        const data = await api.get(`${COLLECTION_MEMBERSHIP_URL}?${query}`, { signal, cache: 'no-store' });
        const ids = data?.collection_ids;
        if (!Array.isArray(ids) || !ids.every(isId)) throw new TypeError('Invalid collection membership');
        return [...ids];
    }
    async function rename(id, name) {
        const url = `${COLLECTIONS_URL}/${requireId(id)}`;
        const body = { name: requireName(name) };
        const renamed = answered(await send(() => api.patch(url, body, { signal })));
        changed(collections.map(item => item.id === renamed.id ? { ...item, name: renamed.name } : item));
        return renamed;
    }
    async function deleteCollection(id) {
        const url = `${COLLECTIONS_URL}/${requireId(id)}`;
        try {
            await api.del(url, { signal });
        } catch (error) {
            // Already gone (another tab deleted it): the outcome the user asked for.
            if (error?.status !== 404) throw error;
        }
        changed(collections.filter(item => item.id !== id));
        return id;
    }
    return {
        load, add, remove, membership, rename, delete: deleteCollection,
        get collections() { return collections; },
        get status() { return status; },
        find: id => collections.find(item => item.id === id) ?? null,
        settled: () => status === 'loading' ? new Promise(resolve => waiting.push(resolve)) : Promise.resolve({ collections, status }),
        subscribe(listener) {
            listeners.add(listener);
            return () => listeners.delete(listener);
        },
    };
}
