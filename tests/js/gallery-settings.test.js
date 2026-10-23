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

test("a legacy gallery Save keeps the workspace keys and every other stored key", () => {
    const fs = require("node:fs");
    const path = require("node:path");
    const vm = require("node:vm");
    const settings = require("../../web/js/gallery-settings.js");
    const storage = new Map([["gallerySettings", JSON.stringify({ workspaceLayout: "masonry", workspaceDensity: "compact", someOtherTool: 1 })]]);
    const controls = {
        lazyLoadingToggle: { checked: true }, imageQualitySelect: { value: "high" }, defaultViewModeSelect: { value: "list" },
        defaultLimitSelect: { value: "200" }, gridColumnsSelect: { value: "6" }, showImageInfoToggle: { checked: false },
        autoLoadMetadataToggle: { checked: true }, cacheMetadataToggle: { checked: true }, showFilePathsToggle: { checked: false },
        sidebarCollapsedByDefaultToggle: { checked: true }, infiniteScrollToggle: { checked: false }, debugModeToggle: { checked: false },
        apiTimeoutInput: { value: "30" }, checkThumbnailsAtStartup: { checked: true }, videoAutoplayToggle: { checked: true },
        videoMuteToggle: { checked: false }, videoLoopToggle: { checked: true },
    };
    const context = vm.createContext({
        document: { addEventListener() {}, getElementById: id => controls[id] },
        window: {}, console: { debug() {}, warn() {}, error() {} }, GallerySettings: settings,
        localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) },
    });
    const file = path.join(__dirname, "../../web/js/gallery.js");
    const Manager = vm.runInContext(`${fs.readFileSync(file, "utf8")}\nGalleryManager;`, context, { filename: file });
    const manager = Object.create(Manager.prototype);
    Object.assign(manager, { showNotification() {}, hideSettings() {}, applySettings() {} });
    manager.saveSettings();
    const saved = JSON.parse(storage.get("gallerySettings"));
    assert.equal(saved.workspaceLayout, "masonry");
    assert.equal(saved.workspaceDensity, "compact");
    assert.equal(saved.someOtherTool, 1);
    assert.equal(saved.defaultViewMode, "list");
    assert.equal(saved.showImageInfo, false);
    assert.equal(saved.videoMute, false);
});
