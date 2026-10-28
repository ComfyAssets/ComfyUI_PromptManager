// Run with: node --test tests/js/
const test = require("node:test");
const assert = require("node:assert/strict");
const {
    DEFAULT_SETTINGS,
    normalizeGallerySettings,
    gridColumnsStyle,
    LIMIT_OPTIONS,
} = require("../../web/js/gallery-settings.js");

test("defaults are a complete, sane settings object", () => {
    const s = normalizeGallerySettings(null);
    assert.deepEqual(s, DEFAULT_SETTINGS);
    assert.equal(s.defaultViewMode, "grid");
    assert.equal(s.defaultLimit, 100);
    assert.equal(s.gridColumns, 8);
    assert.equal(s.showImageInfo, true);
    assert.equal(s.sidebarCollapsedByDefault, false);
});

test("stored values override defaults and unknown keys are kept", () => {
    const s = normalizeGallerySettings({ defaultViewMode: "list", defaultLimit: 500, gridColumns: 10, showImageInfo: false, videoLoop: false, extra: 1 });
    assert.equal(s.defaultViewMode, "list");
    assert.equal(s.defaultLimit, 500);
    assert.equal(s.gridColumns, 10);
    assert.equal(s.showImageInfo, false);
    assert.equal(s.videoLoop, false);
    assert.equal(s.extra, 1);
});

test("invalid stored values fall back to defaults instead of breaking the page", () => {
    const s = normalizeGallerySettings({ defaultViewMode: "mosaic", defaultLimit: 7, gridColumns: "lots", showImageInfo: "no" });
    assert.equal(s.defaultViewMode, "grid");
    assert.equal(s.defaultLimit, 100);
    assert.equal(s.gridColumns, 8);
    assert.equal(s.showImageInfo, true);
});

test("numeric strings from <select> values are accepted and columns are clamped", () => {
    assert.equal(normalizeGallerySettings({ defaultLimit: "200" }).defaultLimit, 200);
    assert.equal(normalizeGallerySettings({ gridColumns: "10" }).gridColumns, 10);
    assert.equal(normalizeGallerySettings({ gridColumns: 40 }).gridColumns, 12);
    assert.equal(normalizeGallerySettings({ gridColumns: 1 }).gridColumns, 2);
    assert.deepEqual(LIMIT_OPTIONS, [50, 100, 200, 500]);
});

test("normalize never mutates its input", () => {
    const raw = { gridColumns: 4 };
    normalizeGallerySettings(raw);
    assert.deepEqual(raw, { gridColumns: 4 });
});

test("gridColumnsStyle produces an explicit grid template", () => {
    assert.equal(gridColumnsStyle(10), "repeat(10, minmax(0, 1fr))");
    assert.equal(gridColumnsStyle("6"), "repeat(6, minmax(0, 1fr))");
    assert.equal(gridColumnsStyle("x"), "repeat(8, minmax(0, 1fr))");
});

test("infinite scrolling is off by default and must be a boolean", () => {
    assert.equal(DEFAULT_SETTINGS.infiniteScroll, false);
    assert.equal(normalizeGallerySettings({ infiniteScroll: true }).infiniteScroll, true);
    assert.equal(normalizeGallerySettings({ infiniteScroll: "yes" }).infiniteScroll, false);
});

test("the workspace keys have their own defaults and validation", () => {
    const { WORKSPACE_LAYOUTS, WORKSPACE_DENSITIES } = require("../../web/js/gallery-settings.js");
    assert.equal(DEFAULT_SETTINGS.workspaceLayout, "grid");
    assert.equal(DEFAULT_SETTINGS.workspaceDensity, "comfortable");
    assert.deepEqual(WORKSPACE_LAYOUTS, ["grid", "list", "masonry"]);
    assert.deepEqual(WORKSPACE_DENSITIES, ["comfortable", "large", "compact"]);
    assert.equal(normalizeGallerySettings({ workspaceLayout: "masonry" }).workspaceLayout, "masonry");
    assert.equal(normalizeGallerySettings({ workspaceLayout: "mosaic" }).workspaceLayout, "grid");
    assert.equal(normalizeGallerySettings({ workspaceDensity: "large" }).workspaceDensity, "large");
    assert.equal(normalizeGallerySettings({ workspaceDensity: 3 }).workspaceDensity, "comfortable");
    // The legacy gallery's own view mode is separate: it has no masonry.
    assert.equal(normalizeGallerySettings({ defaultViewMode: "masonry" }).defaultViewMode, "grid");
});

// #330 S13: the legacy gallery's Page options edit only the page-only keys (and its default view, see PAGE_OPTION_KEYS); System › Appearance owns the rest, so a Save
// or a Reset from the legacy page keeps every other stored key.
function legacyGallery(stored, controls, extra = {}) {
    const fs = require("node:fs");
    const path = require("node:path");
    const vm = require("node:vm");
    const settings = require("../../web/js/gallery-settings.js");
    const storage = new Map([["gallerySettings", JSON.stringify(stored)]]);
    const context = vm.createContext({
        document: { addEventListener() {}, getElementById: id => controls[id] },
        window: {}, console: { debug() {}, warn() {}, error() {} }, GallerySettings: settings,
        localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value),
            removeItem: key => storage.delete(key) },
        ...extra,
    });
    const file = path.join(__dirname, "../../web/js/gallery.js");
    const Manager = vm.runInContext(`${fs.readFileSync(file, "utf8")}\nGalleryManager;`, context, { filename: file });
    const manager = Object.create(Manager.prototype);
    const calls = [];
    Object.assign(manager, { showNotification: (message, type) => calls.push([type, message]), hideSettings() {},
        applySettings: applied => calls.push(["apply", applied.lazyLoading]) });
    return { manager, storage, calls, saved: () => JSON.parse(storage.get("gallerySettings")) };
}

const PAGE_OPTION_CONTROLS = () => ({
    checkThumbnailsAtStartup: { checked: false }, lazyLoadingToggle: { checked: false }, defaultViewModeSelect: { value: "list" },
    defaultLimitSelect: { value: "200" },
    gridColumnsSelect: { value: "6" }, infiniteScrollToggle: { checked: true }, debugModeToggle: { checked: true },
    apiTimeoutInput: { value: "45" },
});

test("a legacy gallery Save keeps the workspace keys and every other stored key", () => {
    const { manager, saved } = legacyGallery({ workspaceLayout: "masonry", workspaceDensity: "compact", someOtherTool: 1,
        imageQuality: "high", showImageInfo: false, videoMute: false, thumbnailPromptDismissed: "sig" }, PAGE_OPTION_CONTROLS());
    manager.saveSettings();
    const result = saved();
    assert.equal(result.workspaceLayout, "masonry");
    assert.equal(result.workspaceDensity, "compact");
    assert.equal(result.someOtherTool, 1);
    assert.equal(result.imageQuality, "high");
    assert.equal(result.showImageInfo, false);
    assert.equal(result.videoMute, false);
    assert.equal(result.thumbnailPromptDismissed, "sig");
    // The page-only keys come from the dialog (the default view stays a page option: System's layout is the workspace's).
    assert.equal(result.defaultViewMode, "list");
    assert.equal(result.lazyLoading, false);
    assert.equal(result.debugMode, true);
    assert.equal(result.checkThumbnailsAtStartup, false);
    assert.equal(result.defaultLimit, 200);
    assert.equal(result.gridColumns, 6);
    assert.equal(result.infiniteScroll, true);
    assert.equal(result.apiTimeout, 45);
});

test("a legacy gallery Reset restores only the page options and keeps every other stored key", () => {
    const controls = PAGE_OPTION_CONTROLS();
    const { manager, saved, calls } = legacyGallery({ workspaceLayout: "masonry", imageQuality: "high", videoMute: false,
        thumbnailPromptDismissed: "sig", thumbnailsGenerated: true, lazyLoading: false, debugMode: true, defaultLimit: 200,
        gridColumns: 6, infiniteScroll: true, apiTimeout: 45, checkThumbnailsAtStartup: false, defaultViewMode: "list" }, controls,
    { confirm: message => (calls.push(["confirm", message]), true) });
    manager.resetSettings();
    const result = saved();
    const { DEFAULT_SETTINGS } = require("../../web/js/gallery-settings.js");
    for (const key of ["checkThumbnailsAtStartup", "lazyLoading", "defaultViewMode", "defaultLimit", "gridColumns",
        "infiniteScroll", "debugMode", "apiTimeout"]) {
        assert.equal(result[key], DEFAULT_SETTINGS[key], key);
    }
    assert.equal(result.workspaceLayout, "masonry");
    assert.equal(result.imageQuality, "high");
    assert.equal(result.videoMute, false);
    assert.equal(result.thumbnailPromptDismissed, "sig");
    assert.equal(result.thumbnailsGenerated, true);
    // The dialog shows the defaults again and the page applies them.
    assert.equal(controls.lazyLoadingToggle.checked, DEFAULT_SETTINGS.lazyLoading);
    assert.equal(controls.apiTimeoutInput.value, DEFAULT_SETTINGS.apiTimeout);
    assert.deepEqual(calls, [["confirm", "Reset these page options to their defaults?"], ["apply", DEFAULT_SETTINGS.lazyLoading],
        ["success", "Page options reset to defaults"]]);
});

test("a viewer mute or loop click remembers only that key and never reads the Page options dialog", () => {
    const { manager, saved, calls } = legacyGallery({ defaultLimit: 200, lazyLoading: false, videoMute: true }, {});
    manager.storeSetting("videoMute", false);
    const result = saved();
    assert.equal(result.videoMute, false);
    assert.equal(result.defaultLimit, 200);
    assert.equal(result.lazyLoading, false);
    assert.deepEqual(calls, []);
});
