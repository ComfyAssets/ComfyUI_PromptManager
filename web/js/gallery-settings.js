/**
 * Gallery display settings: defaults, validation of what localStorage holds,
 * and the grid template for a chosen column count.
 *
 * Settings are per browser (localStorage key "gallerySettings"). Anything
 * stored can be stale or hand-edited, so every value is checked before use.
 *
 * Loadable in the browser (window.GallerySettings) and in Node for tests.
 */
(function (root) {
    "use strict";

    const VIEW_MODES = ["grid", "list"];
    const LIMIT_OPTIONS = [50, 100, 200, 500];
    const MIN_COLUMNS = 2;
    const MAX_COLUMNS = 12;

    const DEFAULT_SETTINGS = Object.freeze({
        lazyLoading: true,
        imageQuality: "medium",
        defaultViewMode: "grid",
        defaultLimit: 100,
        gridColumns: 8,
        showImageInfo: true,
        autoLoadMetadata: true,
        cacheMetadata: true,
        showFilePaths: true,
        sidebarCollapsedByDefault: false,
        infiniteScroll: false,
        debugMode: false,
        apiTimeout: 30,
        thumbnailsGenerated: false,
        checkThumbnailsAtStartup: true,
        videoAutoplay: false,
        videoMute: true,
        videoLoop: true,
    });

    function toInt(value) {
        const n = typeof value === "string" ? Number(value.trim()) : value;
        return typeof n === "number" && Number.isInteger(n) ? n : null;
    }

    function bool(value, fallback) {
        return typeof value === "boolean" ? value : fallback;
    }

    /** Merge stored settings over the defaults, replacing anything invalid. */
    function normalizeGallerySettings(raw) {
        const stored = raw && typeof raw === "object" ? raw : {};
        const merged = { ...DEFAULT_SETTINGS, ...stored };
        const limit = toInt(merged.defaultLimit);
        const columns = toInt(merged.gridColumns);
        const timeout = toInt(merged.apiTimeout);
        return {
            ...merged,
            defaultViewMode: VIEW_MODES.includes(merged.defaultViewMode) ? merged.defaultViewMode : DEFAULT_SETTINGS.defaultViewMode,
            defaultLimit: limit !== null && LIMIT_OPTIONS.includes(limit) ? limit : DEFAULT_SETTINGS.defaultLimit,
            gridColumns: columns === null ? DEFAULT_SETTINGS.gridColumns : Math.min(MAX_COLUMNS, Math.max(MIN_COLUMNS, columns)),
            apiTimeout: timeout !== null && timeout > 0 ? timeout : DEFAULT_SETTINGS.apiTimeout,
            imageQuality: ["low", "medium", "high"].includes(merged.imageQuality) ? merged.imageQuality : DEFAULT_SETTINGS.imageQuality,
            lazyLoading: bool(merged.lazyLoading, DEFAULT_SETTINGS.lazyLoading),
            showImageInfo: bool(merged.showImageInfo, DEFAULT_SETTINGS.showImageInfo),
            autoLoadMetadata: bool(merged.autoLoadMetadata, DEFAULT_SETTINGS.autoLoadMetadata),
            cacheMetadata: bool(merged.cacheMetadata, DEFAULT_SETTINGS.cacheMetadata),
            showFilePaths: bool(merged.showFilePaths, DEFAULT_SETTINGS.showFilePaths),
            sidebarCollapsedByDefault: bool(merged.sidebarCollapsedByDefault, DEFAULT_SETTINGS.sidebarCollapsedByDefault),
            infiniteScroll: bool(merged.infiniteScroll, DEFAULT_SETTINGS.infiniteScroll),
            debugMode: bool(merged.debugMode, DEFAULT_SETTINGS.debugMode),
            thumbnailsGenerated: bool(merged.thumbnailsGenerated, DEFAULT_SETTINGS.thumbnailsGenerated),
            checkThumbnailsAtStartup: bool(merged.checkThumbnailsAtStartup, DEFAULT_SETTINGS.checkThumbnailsAtStartup),
            videoAutoplay: bool(merged.videoAutoplay, DEFAULT_SETTINGS.videoAutoplay),
            videoMute: bool(merged.videoMute, DEFAULT_SETTINGS.videoMute),
            videoLoop: bool(merged.videoLoop, DEFAULT_SETTINGS.videoLoop),
        };
    }

    /** CSS grid-template-columns for the desktop grid. */
    function gridColumnsStyle(columns) {
        const n = normalizeGallerySettings({ gridColumns: columns }).gridColumns;
        return `repeat(${n}, minmax(0, 1fr))`;
    }

    const api = { DEFAULT_SETTINGS, LIMIT_OPTIONS, VIEW_MODES, normalizeGallerySettings, gridColumnsStyle };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    else root.GallerySettings = api;
})(typeof window !== "undefined" ? window : globalThis);
