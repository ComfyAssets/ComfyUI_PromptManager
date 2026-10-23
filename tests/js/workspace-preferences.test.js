// Browser display defaults (#249 System S3 Task 7): the workspace reads and writes the legacy gallery's own
// `gallerySettings` object (Q3, "One display configuration"), writes only the keys it changed, never consumes the
// legacy paging keys, and says when this browser refused to keep a change.
import test from 'node:test';
import assert from 'node:assert/strict';
import gallerySettings from '../../web/js/gallery-settings.js';

const module = await import('../../web/js/workspace/preferences.mjs').catch(error => {
    if (error.code !== 'ERR_MODULE_NOT_FOUND') throw error;
    return {};
});
globalThis.GallerySettings = gallerySettings;
const KEY = 'gallerySettings';

/** A localStorage stand-in holding `text` under the settings key; `setItem` can be made to throw. */
function storage(text = null, { quota = false } = {}) {
    const writes = [];
    const store = {
        text,
        writes,
        getItem(key) { assert.equal(key, KEY); return store.text; },
        setItem(key, value) {
            assert.equal(key, KEY);
            writes.push(value);
            if (quota) { const error = new Error('Quota exceeded'); error.name = 'QuotaExceededError'; throw error; }
            store.text = value;
        },
        removeItem() { assert.fail('Preferences never remove the shared settings'); },
    };
    return store;
}
const denied = () => { const error = new Error('Denied'); error.name = 'SecurityError'; throw error; };

test('normalizes new workspace keys and keeps legacy keys', () => {
    assert.equal(typeof module.readPreferences, 'function', 'preferences.mjs exports readPreferences');
    module.resetTabPreferences();
    const legacy = { defaultViewMode: 'list', gridColumns: 5, defaultLimit: 200, thumbnailPromptDismissed: 'sig', extra: 1 };
    const store = storage(JSON.stringify({ ...legacy, workspaceLayout: 'masonry', workspaceDensity: 'compact', showFilePaths: false }));
    const { values, persisted } = module.readPreferences(() => store);
    assert.equal(persisted, true);
    assert.equal(values.workspaceLayout, 'masonry');
    assert.equal(values.workspaceDensity, 'compact');
    assert.equal(values.showFilePaths, false);
    // Invalid workspace values fall back to the defaults, as every legacy key does.
    store.text = JSON.stringify({ workspaceLayout: 'mosaic', workspaceDensity: 7, videoMute: 'no' });
    const fallback = module.readPreferences(() => store).values;
    assert.equal(fallback.workspaceLayout, 'grid');
    assert.equal(fallback.workspaceDensity, 'comfortable');
    assert.equal(fallback.videoMute, true);
    // The legacy normalizer knows the new keys and still keeps unknown ones.
    const normalized = gallerySettings.normalizeGallerySettings({ ...legacy, workspaceLayout: 'list' });
    assert.equal(normalized.workspaceLayout, 'list');
    assert.equal(normalized.workspaceDensity, 'comfortable');
    assert.equal(normalized.extra, 1);
    assert.equal(normalized.thumbnailPromptDismissed, 'sig');
    assert.deepEqual(gallerySettings.WORKSPACE_LAYOUTS, ['grid', 'list', 'masonry']);
    assert.deepEqual(gallerySettings.WORKSPACE_DENSITIES, ['comfortable', 'large', 'compact']);
});

test('write touches only dirty keys', () => {
    module.resetTabPreferences();
    const stored = { defaultViewMode: 'list', gridColumns: 5, thumbnailsGenerated: true, extra: { kept: true }, videoMute: false };
    const store = storage(JSON.stringify(stored));
    const result = module.writePreferences({ workspaceLayout: 'masonry', showImageInfo: false }, () => store);
    assert.deepEqual(result, { persisted: true, conflicts: [] });
    assert.equal(store.writes.length, 1);
    // Every other key, legacy or unknown, is written back exactly as it was stored: no defaults are filled in.
    assert.deepEqual(JSON.parse(store.text), { ...stored, workspaceLayout: 'masonry', showImageInfo: false });
    // It re-reads the stored object, so a key another page wrote after this one read is kept.
    store.text = JSON.stringify({ ...JSON.parse(store.text), gridColumns: 9 });
    module.writePreferences({ videoLoop: false }, () => store);
    assert.equal(JSON.parse(store.text).gridColumns, 9);
    assert.equal(JSON.parse(store.text).workspaceLayout, 'masonry');
    // An empty or malformed stored value starts from an empty object, never the defaults.
    for (const text of [null, '{broken', '[]', '42']) {
        const fresh = storage(text);
        assert.equal(module.writePreferences({ cacheMetadata: false }, () => fresh).persisted, true);
        assert.deepEqual(JSON.parse(fresh.text), { cacheMetadata: false });
    }
    // Only workspace keys with valid values are accepted; nothing is written for a bad change.
    for (const bad of [{ gridColumns: 4 }, { defaultLimit: 50 }, { workspaceLayout: 'mosaic' }, { videoMute: 'yes' }, { imageQuality: 'ultra' }]) {
        const untouched = storage(JSON.stringify(stored));
        assert.throws(() => module.writePreferences(bad, () => untouched), TypeError);
        assert.equal(untouched.writes.length, 0);
    }
    // A key changed elsewhere since this tab read it (its baseline) is reported, and the explicit save still wins.
    const elsewhere = storage(JSON.stringify({ videoLoop: false }));
    const outcome = module.writePreferences({ videoLoop: true, videoMute: false }, () => elsewhere, { baseline: { videoLoop: true, videoMute: true } });
    assert.deepEqual(outcome, { persisted: true, conflicts: ['videoLoop'] });
    assert.equal(JSON.parse(elsewhere.text).videoLoop, true);
});

test('denied storage reports tab-only', () => {
    module.resetTabPreferences();
    const read = module.readPreferences(denied);
    assert.equal(read.persisted, false);
    assert.equal(read.values.workspaceLayout, 'grid');
    const result = module.writePreferences({ workspaceLayout: 'list', videoAutoplay: true }, denied);
    assert.deepEqual(result, { persisted: false, conflicts: [] });
    // The change is kept for this tab: every reader in it (a new Images visit, the viewer) sees it.
    const tab = module.readPreferences(denied);
    assert.equal(tab.persisted, false);
    assert.equal(tab.values.workspaceLayout, 'list');
    assert.equal(tab.values.videoAutoplay, true);
    // A getItem that throws is denied storage too.
    const throwing = { getItem: denied, setItem: denied };
    assert.equal(module.readPreferences(() => throwing).values.workspaceLayout, 'list');
    assert.equal(module.writePreferences({ workspaceDensity: 'large' }, () => throwing).persisted, false);
    // Once storage accepts a write, the tab copy of that key gives way to the stored value.
    const working = storage(JSON.stringify({ workspaceLayout: 'masonry' }));
    assert.equal(module.writePreferences({ workspaceLayout: 'masonry' }, () => working).persisted, true);
    assert.equal(module.readPreferences(() => working).values.workspaceLayout, 'masonry');
    module.resetTabPreferences();
    assert.equal(module.readPreferences(denied).values.videoAutoplay, false);
});

test('quota error keeps previous bytes', () => {
    module.resetTabPreferences();
    const before = JSON.stringify({ gridColumns: 6, workspaceDensity: 'large' });
    const store = storage(before, { quota: true });
    const result = module.writePreferences({ workspaceDensity: 'compact' }, () => store);
    assert.deepEqual(result, { persisted: false, conflicts: [] });
    assert.equal(store.writes.length, 1);
    assert.equal(store.text, before);
    // This tab keeps the change it could not store.
    assert.equal(module.readPreferences(() => store).values.workspaceDensity, 'compact');
    module.resetTabPreferences();
    assert.equal(module.readPreferences(() => store).values.workspaceDensity, 'large');
});

/** A window stand-in that records its storage listeners. */
function fakeWindow() {
    const listeners = new Set();
    return {
        listeners,
        addEventListener(type, listener) { assert.equal(type, 'storage'); listeners.add(listener); },
        removeEventListener(type, listener) { assert.equal(type, 'storage'); listeners.delete(listener); },
        fire(event) { for (const listener of [...listeners]) listener(event); },
    };
}

test('storage event updates clean values and flags dirty conflicts', () => {
    module.resetTabPreferences();
    const store = storage(JSON.stringify({ workspaceLayout: 'list', videoMute: false }));
    const win = fakeWindow();
    const seen = [];
    const unsubscribe = module.subscribePreferences(win, next => seen.push(next), () => store);
    assert.equal(win.listeners.size, 1);
    // Another key, or another storage area's event, is not ours.
    win.fire({ key: 'pm_update_notice_dismissed', storageArea: store });
    win.fire({ key: KEY, storageArea: { other: true } });
    assert.equal(seen.length, 0);
    win.fire({ key: KEY, storageArea: store });
    win.fire({ key: null, storageArea: store }); // localStorage.clear() elsewhere
    assert.equal(seen.length, 2);
    assert.equal(seen[0].values.workspaceLayout, 'list');
    unsubscribe();
    assert.equal(win.listeners.size, 0);
    // The reconciliation of the System drafts: clean values follow the other page; a key this tab has a draft for and
    // the other page changed is a conflict, and the draft stays.
    const baseline = { workspaceLayout: 'grid', videoMute: true, videoLoop: true };
    const next = { workspaceLayout: 'list', videoMute: true, videoLoop: false };
    assert.deepEqual(module.reconcilePreferences({ baseline, drafts: { videoMute: false, videoLoop: false }, next }), { conflicts: [] });
    assert.deepEqual(module.reconcilePreferences({ baseline, drafts: { workspaceLayout: 'masonry' }, next }), { conflicts: ['workspaceLayout'] });
    assert.deepEqual(module.reconcilePreferences({ baseline, drafts: { workspaceLayout: 'list' }, next }), { conflicts: [] });
});

test('legacy paging keys are never consumed by the workspace', () => {
    module.resetTabPreferences();
    const store = storage(JSON.stringify({ defaultLimit: 500, gridColumns: 12, infiniteScroll: true, lazyLoading: false,
        defaultViewMode: 'list', debugMode: true, apiTimeout: 9, thumbnailsGenerated: true, checkThumbnailsAtStartup: false }));
    const { values } = module.readPreferences(() => store);
    assert.deepEqual(Object.keys(values).sort(), [...module.WORKSPACE_PREFERENCE_KEYS].sort());
    for (const key of ['defaultLimit', 'gridColumns', 'infiniteScroll', 'lazyLoading', 'defaultViewMode', 'debugMode', 'apiTimeout',
        'thumbnailsGenerated', 'checkThumbnailsAtStartup', 'thumbnailPromptDismissed']) assert.equal(Object.hasOwn(values, key), false, key);
    // The display defaults are the gallery's own defaults for these keys.
    const defaults = module.preferenceDefaults();
    for (const key of module.WORKSPACE_PREFERENCE_KEYS) assert.equal(defaults[key], gallerySettings.DEFAULT_SETTINGS[key], key);
});
