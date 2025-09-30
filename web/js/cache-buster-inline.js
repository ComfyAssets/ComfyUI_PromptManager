/**
 * Inline Cache Busting Script
 * Add this as the FIRST script in <head> to bust cache on all subsequent resources.
 *
 * Usage in HTML:
 * <script src="/prompt_manager/js/cache-buster-inline.js"></script>
 */
(function() {
    'use strict';

    // Cache version - timestamp-based for dev, env var for prod
    window.PROMPTMANAGER_CACHE_VERSION = window.PROMPTMANAGER_VERSION || Date.now();

    // Helper to append version to URLs
    window.bustCache = function(url) {
        if (!url || url.startsWith('data:') || url.startsWith('blob:') || url.startsWith('http')) {
            return url;
        }
        const sep = url.includes('?') ? '&' : '?';
        return url + sep + 'v=' + window.PROMPTMANAGER_CACHE_VERSION;
    };

    console.log('[PromptManager] Cache busting enabled, version:', window.PROMPTMANAGER_CACHE_VERSION);
})();
