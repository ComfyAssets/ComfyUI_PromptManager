/**
 * What "clear the cache" may remove from this browser (#222).
 *
 * ComfyUI serves every custom node from one origin, so a browser's localStorage and CacheStorage are shared with other
 * tools. A cache clear removes only the entries Prompt Manager itself writes as a cache, listed by name in `OWNED` from
 * an inventory of the real writers, never by a guessed prefix. The inventory (2026-10-24, enforced by
 * tests/js/cache-ownership.test.js): every key a Prompt Manager page writes is a preference (`PREFERENCE_KEYS`: the
 * display defaults, the workspace theme, the dismissed update notice), and no page opens a CacheStorage cache or
 * IndexedDB. So `OWNED` is empty: nothing of Prompt Manager's is cached in this browser, and a clear says so instead of
 * claiming it removed something.
 *
 * Server thumbnails are not browser data: clearing them is the caller's own, separate request. This module never makes
 * a request.
 *
 * Loadable in the browser (window.CacheOwnership) and in Node for tests.
 */
(function (root) {
    "use strict";

    const PREFERENCE_KEYS = Object.freeze(["gallerySettings", "pm_workspace_theme", "pm_update_notice_dismissed"]);
    const OWNED = Object.freeze({ storageKeys: Object.freeze([]), cacheNames: Object.freeze([]) });
    const NOTHING_CACHED = "Nothing of Prompt Manager's is cached in this browser.";
    const STORAGE_DENIED = "This browser refused access to its storage, so nothing there was cleared.";

    // What a storage getter that threw (the browser refused storage) resolves to.
    const REFUSED = Object.freeze({});

    /** `storage`, or what its getter returns; `REFUSED` when the getter throws. */
    function resolveStorage(storage) {
        if (typeof storage !== "function") return storage;
        try {
            return storage() ?? null;
        } catch {
            return REFUSED;
        }
    }

    /** The keys `storage` holds, or null when the browser refuses to say. */
    function storageKeys(storage) {
        if (storage === REFUSED) return null;
        if (!storage) return [];
        try {
            const keys = [];
            for (let index = 0; index < storage.length; index += 1) keys.push(storage.key(index));
            return keys;
        } catch {
            return null;
        }
    }

    async function cacheNames(cacheStorage) {
        if (!cacheStorage || typeof cacheStorage.keys !== "function") return [];
        try {
            return await cacheStorage.keys();
        } catch {
            return null;
        }
    }

    /**
     * The owned entries present now: `{keys, caches, denied}`; `denied` when the browser refused to list its storage or
     * caches.
     */
    async function ownedEntries({ storage, cacheStorage, owned = OWNED } = {}) {
        const present = storageKeys(resolveStorage(storage));
        const names = await cacheNames(cacheStorage);
        return {
            keys: (present ?? []).filter(key => owned.storageKeys.includes(key)),
            caches: (names ?? []).filter(name => owned.cacheNames.includes(name)),
            denied: present === null || names === null,
        };
    }

    /**
     * Remove the owned entries present now. `storage` is a Storage or a getter for one (`() => localStorage`, which a
     * browser refusing storage makes throw). Returns `{removed, failed, denied, partial}`: what was removed and what the
     * browser refused to remove, by name; `partial` when anything was refused, including listing the storage.
     */
    async function clearOwned({ storage, cacheStorage, owned = OWNED } = {}) {
        storage = resolveStorage(storage);
        const { keys, caches, denied } = await ownedEntries({ storage, cacheStorage, owned });
        const removed = [];
        const failed = [];
        for (const key of keys) {
            try {
                storage.removeItem(key);
                removed.push(key);
            } catch {
                failed.push(key);
            }
        }
        for (const name of caches) {
            try {
                await cacheStorage.delete(name);
                removed.push(name);
            } catch {
                failed.push(name);
            }
        }
        return { removed, failed, denied, partial: denied || failed.length > 0 };
    }

    const entries = count => `${count.toLocaleString("en-US")} cached ${count === 1 ? "entry" : "entries"}`;

    /** What a clear's result says, truthfully. */
    function clearMessage(result) {
        const total = result.removed.length + result.failed.length;
        if (result.failed.length) {
            return `Cleared ${result.removed.length.toLocaleString("en-US")} of ${entries(total)}; this browser refused the rest.`;
        }
        if (result.denied) return STORAGE_DENIED;
        if (!total) return NOTHING_CACHED;
        return `Cleared ${entries(total)} from this browser.`;
    }

    const api = { PREFERENCE_KEYS, OWNED, NOTHING_CACHED, STORAGE_DENIED, ownedEntries, clearOwned, clearMessage };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    else root.CacheOwnership = api;
})(typeof window !== "undefined" ? window : globalThis);
