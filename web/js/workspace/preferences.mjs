/**
 * Browser display defaults (#249 System S3 Task 7). The workspace keeps them in the legacy gallery's own
 * `gallerySettings` object (Q3, "One display configuration"), so the gallery, the workspace and the viewer read one
 * configuration: the new `workspaceLayout` and `workspaceDensity` beside the legacy keys they share (metadata, paths,
 * card details, viewer details, image quality and the video defaults). The legacy paging keys (`defaultLimit`,
 * `gridColumns`, `infiniteScroll`, …) belong to the legacy gallery and are never read here (README continuous-browsing
 * ruling).
 *
 * - `readPreferences(getStorage, normalize)` → `{values, persisted}`: the workspace keys, normalized by `gallery-settings.js`;
 *   `persisted` is false when this browser refused its storage.
 * - `writePreferences(dirty, getStorage, {baseline})` → `{persisted, conflicts}`: re-reads the stored object and writes
 *   back only the dirty keys, every other key byte-for-byte as stored. A write the browser refused (denied storage, a
 *   full quota) leaves the stored bytes as they were and keeps the change for this tab only, so every reader in this
 *   tab still sees it. `conflicts` lists the dirty keys another page changed since `baseline` was read; the explicit
 *   save still wins.
 * - `subscribePreferences(win, onChange, getStorage)` → unsubscribe: `onChange(readPreferences())` after another page
 *   changed the settings (a `storage` event).
 * - `reconcilePreferences({baseline, drafts, next})` → `{conflicts}`: which drafts such a change overtook.
 *
 * Every storage access is in try/catch: a private window, blocked site data or a malformed value never breaks a page.
 */
export const PREFERENCES_KEY = 'gallerySettings';
export const WORKSPACE_PREFERENCE_KEYS = Object.freeze(['workspaceLayout', 'workspaceDensity', 'autoLoadMetadata', 'cacheMetadata',
    'imageQuality', 'videoAutoplay', 'videoMute', 'videoLoop', 'showFilePaths', 'showImageInfo', 'sidebarCollapsedByDefault']);

// The changes this tab could not store, overlaid on every read until storage accepts them.
const tabOnly = new Map();
const defaultStorage = () => globalThis.localStorage;
const normalizer = () => globalThis.GallerySettings.normalizeGallerySettings;
const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);

/** Forget what this tab kept without storage (tests, and nothing else). */
export function resetTabPreferences() {
    tabOnly.clear();
}

const pick = settings => Object.fromEntries(WORKSPACE_PREFERENCE_KEYS.map(key => [key, settings[key]]));
/** The gallery's defaults for the workspace keys. */
export function preferenceDefaults() {
    return pick(normalizer()(null));
}

/** The stored object (`{}` when absent or malformed), or null when storage is unavailable. */
function storedObject(storage) {
    let text;
    try { text = storage.getItem(PREFERENCES_KEY); } catch { return null; }
    try {
        const parsed = text ? JSON.parse(text) : null;
        return isObject(parsed) ? parsed : {};
    } catch {
        return {};
    }
}

function storageOf(getStorage) {
    try { return getStorage() ?? null; } catch { return null; }
}

/** `normalize` is gallery-settings.js's `normalizeGallerySettings` (the page's `window.GallerySettings` by default). */
export function readPreferences(getStorage = defaultStorage, normalize = normalizer()) {
    const storage = storageOf(getStorage);
    const stored = storage ? storedObject(storage) : null;
    const values = pick(normalize({ ...(stored ?? {}), ...Object.fromEntries(tabOnly) }));
    return { values, persisted: stored !== null };
}

/** A change is only a workspace key with a value the gallery's normalizer keeps as given. */
function validate(dirty) {
    if (!isObject(dirty)) throw new TypeError('Preferences need an object of changes');
    for (const [key, value] of Object.entries(dirty)) {
        if (!WORKSPACE_PREFERENCE_KEYS.includes(key)) throw new TypeError(`Not a workspace display preference: ${key}`);
        if (normalizer()({ [key]: value })[key] !== value) throw new TypeError(`Invalid value for ${key}`);
    }
}

export function writePreferences(dirty, getStorage = defaultStorage, { baseline = null } = {}) {
    validate(dirty);
    const keep = () => { for (const [key, value] of Object.entries(dirty)) tabOnly.set(key, value); };
    const storage = storageOf(getStorage);
    const stored = storage ? storedObject(storage) : null;
    if (stored === null) { keep(); return { persisted: false, conflicts: [] }; }
    const current = pick(normalizer()(stored));
    const conflicts = baseline ? Object.keys(dirty).filter(key => Object.hasOwn(baseline, key) && current[key] !== baseline[key]) : [];
    try {
        storage.setItem(PREFERENCES_KEY, JSON.stringify({ ...stored, ...dirty }));
    } catch {
        keep();
        return { persisted: false, conflicts };
    }
    for (const key of Object.keys(dirty)) tabOnly.delete(key);
    return { persisted: true, conflicts };
}

/** The current values for a display consumer, or null on a page without gallery-settings.js (a unit test). */
export function displayPreferences(getStorage = defaultStorage) {
    try { return readPreferences(getStorage).values; } catch { return null; }
}

export function subscribePreferences(win, onChange, getStorage = defaultStorage) {
    const listener = event => {
        if (event.key !== null && event.key !== PREFERENCES_KEY) return;
        if (event.storageArea && event.storageArea !== storageOf(getStorage)) return;
        onChange(readPreferences(getStorage));
    };
    win.addEventListener('storage', listener);
    return () => win.removeEventListener('storage', listener);
}

export function reconcilePreferences({ baseline, drafts, next }) {
    return { conflicts: Object.keys(drafts).filter(key => next[key] !== baseline[key] && next[key] !== drafts[key]) };
}
