// #222: what "clear the cache" may remove from this browser. Only entries Prompt Manager itself writes as a cache count,
// from an inventory of the real writers (never a guessed prefix): preferences (the workspace theme, the update notice,
// the display defaults) and other tools' storage and caches on the shared ComfyUI origin always stay.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const CacheOwnership = require('../../web/js/cache-ownership.js');

const WEB = path.join(__dirname, '../../web');

function memoryStorage(entries) {
    const map = new Map(Object.entries(entries));
    return {
        map,
        get length() { return map.size; },
        key: index => [...map.keys()][index] ?? null,
        getItem: key => (map.has(key) ? map.get(key) : null),
        setItem: (key, value) => map.set(key, String(value)),
        removeItem: key => map.delete(key),
    };
}

function memoryCaches(names, { failDelete = new Set() } = {}) {
    const set = new Set(names);
    return {
        set,
        keys: async () => [...set],
        delete: async name => {
            if (failDelete.has(name)) throw new Error('denied');
            return set.delete(name);
        },
    };
}

const PREFERENCES = { gallerySettings: '{"viewMode":"grid"}', pm_workspace_theme: 'light', pm_update_notice_dismissed: '3.3.0' };
const OWNED = Object.freeze({ storageKeys: ['pm-owned-test-entry'], cacheNames: ['pm-owned-test-cache'] });

test('keeps pm_workspace_theme, pm_update_notice_dismissed and gallerySettings', async () => {
    const storage = memoryStorage({ ...PREFERENCES, 'pm-owned-test-entry': 'x', pm_cached: '1', promptManager_x: '1' });
    const result = await CacheOwnership.clearOwned({ storage, cacheStorage: null, owned: OWNED });
    assert.deepEqual(result.removed, ['pm-owned-test-entry']);
    for (const [key, value] of Object.entries(PREFERENCES)) assert.equal(storage.getItem(key), value, key);
    // The old prefix rule's matches are not Prompt Manager caches either: nothing writes them.
    assert.equal(storage.getItem('pm_cached'), '1');
    assert.equal(storage.getItem('promptManager_x'), '1');
    for (const key of Object.keys(PREFERENCES)) assert.ok(CacheOwnership.PREFERENCE_KEYS.includes(key), key);
});

test("keeps other tools' CacheStorage named gallery/thumbnail", async () => {
    const caches = memoryCaches(['other-gallery', 'thumbnail-cache', 'my-gallery-thumbnails', 'pm-owned-test-cache']);
    const result = await CacheOwnership.clearOwned({ storage: memoryStorage({}), cacheStorage: caches, owned: OWNED });
    assert.deepEqual([...caches.set].sort(), ['my-gallery-thumbnails', 'other-gallery', 'thumbnail-cache']);
    assert.deepEqual(result.removed, ['pm-owned-test-cache']);
});

test('removes only inventoried owned keys', async () => {
    // The shipped inventory: no Prompt Manager writer stores a cache entry in this browser.
    assert.deepEqual(CacheOwnership.OWNED, { storageKeys: [], cacheNames: [] });
    assert.ok(Object.isFrozen(CacheOwnership.OWNED.storageKeys) && Object.isFrozen(CacheOwnership.OWNED.cacheNames));
    const storage = memoryStorage({ ...PREFERENCES, gallery_cache_1: 'a', metadata_cache_2: 'b', other: 'c' });
    const caches = memoryCaches(['gallery', 'thumbnail']);
    const result = await CacheOwnership.clearOwned({ storage, cacheStorage: caches });
    assert.deepEqual(result, { removed: [], failed: [], denied: false, partial: false });
    assert.equal(storage.map.size, 6);
    assert.equal(caches.set.size, 2);
    assert.equal(CacheOwnership.clearMessage(result), CacheOwnership.NOTHING_CACHED);
});

/**
 * Every browser-storage write in one file's text: `{keys}` (setItem keys, as written) and `problems` (writes no key
 * can be read from: a bracket or property assignment on a Storage, a computed setItem key, an IndexedDB or
 * CacheStorage open or write). The scan is textual, so it errs on the side of reporting.
 */
function storageWrites(text) {
    const keys = [];
    const problems = [];
    for (const match of text.matchAll(/\.setItem\(\s*([^,)]+)/g)) {
        const key = match[1].trim();
        const literal = /^(?:'([^']+)'|"([^"]+)")$/.exec(key);
        if (literal) keys.push(literal[1] ?? literal[2]);
        else if (/^[A-Z_]+_KEY$/.test(key)) keys.push(`$${key}`);
        else problems.push(`a computed setItem key: ${key}`);
    }
    if (/\b\w*[sS]torage\s*\[[^\]]+\]\s*=(?!=)/.test(text)) problems.push('a bracket assignment on a Storage');
    if (/\b(?:local|session)Storage\s*\.\s*(?!getItem|setItem|removeItem|key\b|length\b|clear\b)\w+\s*=(?!=)/.test(text)) {
        problems.push('a property assignment on a Storage');
    }
    if (/\bcaches\s*\.\s*open\s*\(/.test(text)) problems.push('opens a CacheStorage cache');
    if (/\bindexedDB\s*\.\s*open\s*\(/.test(text)) problems.push('opens IndexedDB');
    return { keys, problems };
}

test('the storage scan reports every way of writing browser storage (S7 review M5)', () => {
    assert.deepEqual(storageWrites("localStorage.setItem('gallerySettings', x); storage.setItem(NOTICE_KEY, v);").keys,
        ['gallerySettings', '$NOTICE_KEY']);
    for (const text of ["localStorage['pm_cache'] = 1;", 'sessionStorage[key] = value;', 'storage[name] = x;',
        'localStorage.pm_cache = 1;', 'sessionStorage.someKey = "v";', 'localStorage.setItem(key, 1);',
        "caches.open('pm').then(cache => cache.put(url, response));", "indexedDB.open('pm-cache', 1);"]) {
        assert.equal(storageWrites(text).problems.length, 1, text);
    }
    for (const text of ["if (localStorage['pm'] === x) {}", 'const value = localStorage.getItem(KEY);',
        'localStorage.removeItem(KEY);', 'const n = localStorage.length;']) {
        assert.deepEqual(storageWrites(text), { keys: [], problems: [] }, text);
    }
});

test('the inventory covers every browser storage writer in web/', () => {
    // Every key a Prompt Manager page writes is a preference or an inventoried cache entry; a new writer must be
    // classified here before a cache clear may remove it. Skipped: web/lib (third-party Viewer.js and Tailwind
    // output) and web/js/shared/toolkit (the pinned ComfyAssets Toolkit build, never hand-edited here and verified by
    // `npm run --prefix tools/vendor check`). Neither writes browser storage today; the Toolkit's own repository owns
    // its storage, and a new pin is reviewed as its own change.
    const files = [];
    const walk = dir => {
        for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
            const full = path.join(dir, entry.name);
            if (entry.isDirectory()) { if (!['lib', 'toolkit'].includes(entry.name)) walk(full); }
            else if (/\.(m?js|html)$/.test(entry.name)) files.push(full);
        }
    };
    walk(WEB);
    const written = new Set();
    const constants = {};
    for (const file of files) {
        const text = fs.readFileSync(file, 'utf8');
        for (const match of text.matchAll(/(?:const|export const)\s+([A-Z_]+_KEY)\s*=\s*'([^']+)'/g)) constants[match[1]] = match[2];
        const { keys, problems } = storageWrites(text);
        assert.deepEqual(problems, [], path.relative(WEB, file));
        for (const key of keys) written.add(key);
    }
    const keys = [...written].map(key => (key.startsWith('$') ? constants[key.slice(1)] : key));
    assert.ok(keys.length >= 3, 'the writers were found');
    for (const key of keys) {
        assert.ok(key, 'every written key resolves');
        assert.ok(CacheOwnership.PREFERENCE_KEYS.includes(key) || CacheOwnership.OWNED.storageKeys.includes(key), key);
    }
});

test('storage denial reports partial', async () => {
    const denied = { get length() { throw new Error('SecurityError'); }, key() { throw new Error('SecurityError'); },
        getItem() { throw new Error('SecurityError'); }, removeItem() { throw new Error('SecurityError'); } };
    const result = await CacheOwnership.clearOwned({ storage: denied, cacheStorage: null, owned: OWNED });
    assert.equal(result.denied, true);
    assert.equal(result.partial, true);
    assert.equal(CacheOwnership.clearMessage(result), CacheOwnership.STORAGE_DENIED);

    const caches = memoryCaches(['pm-owned-test-cache'], { failDelete: new Set(['pm-owned-test-cache']) });
    const storage = memoryStorage({ 'pm-owned-test-entry': 'x' });
    const partial = await CacheOwnership.clearOwned({ storage, cacheStorage: caches, owned: OWNED });
    assert.deepEqual(partial.removed, ['pm-owned-test-entry']);
    assert.deepEqual(partial.failed, ['pm-owned-test-cache']);
    assert.equal(partial.partial, true);
    assert.equal(CacheOwnership.clearMessage(partial), 'Cleared 1 of 2 cached entries; this browser refused the rest.');

    const keysDenied = { keys: async () => { throw new Error('denied'); } };
    const unreadable = await CacheOwnership.clearOwned({ storage: memoryStorage({}), cacheStorage: keysDenied, owned: OWNED });
    assert.equal(unreadable.denied, true);
});

test('server thumbnail clear stays a separate explicit call', async () => {
    const calls = [];
    const original = globalThis.fetch;
    globalThis.fetch = async (...args) => { calls.push(args); return { ok: true }; };
    try {
        const storage = memoryStorage({ 'pm-owned-test-entry': 'x' });
        const result = await CacheOwnership.clearOwned({ storage, cacheStorage: memoryCaches([]), owned: OWNED });
        assert.deepEqual(result.removed, ['pm-owned-test-entry']);
        assert.equal(CacheOwnership.clearMessage(result), 'Cleared 1 cached entry from this browser.');
    } finally {
        globalThis.fetch = original;
    }
    assert.deepEqual(calls, []);
    assert.equal(/fetch|clear-thumbnails|ApiClient/.test(fs.readFileSync(path.join(WEB, 'js/cache-ownership.js'), 'utf8')), false);
});

test('ownedEntries lists what a clear would remove, reading nothing it cannot', async () => {
    const storage = memoryStorage({ ...PREFERENCES, 'pm-owned-test-entry': 'x' });
    const caches = memoryCaches(['pm-owned-test-cache', 'gallery']);
    assert.deepEqual(await CacheOwnership.ownedEntries({ storage, cacheStorage: caches, owned: OWNED }),
        { keys: ['pm-owned-test-entry'], caches: ['pm-owned-test-cache'], denied: false });
    assert.deepEqual(await CacheOwnership.ownedEntries({ storage, cacheStorage: caches }),
        { keys: [], caches: [], denied: false });
    assert.deepEqual(await CacheOwnership.ownedEntries({ storage: null, cacheStorage: undefined, owned: OWNED }),
        { keys: [], caches: [], denied: false });
    assert.equal(CacheOwnership.clearMessage({ removed: ['a', 'b'], failed: [], denied: false, partial: false }),
        'Cleared 2 cached entries from this browser.');
});
