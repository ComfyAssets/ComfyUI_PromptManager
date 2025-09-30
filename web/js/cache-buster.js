/**
 * Cache Busting Utility
 * Automatically appends version parameter to script and stylesheet URLs
 * to prevent browser caching issues during development.
 */
(function() {
    'use strict';

    // Get cache version from environment or use timestamp
    const CACHE_VERSION = window.PROMPTMANAGER_VERSION || Date.now();

    // Flag to enable/disable cache busting (disable in production)
    const ENABLE_CACHE_BUSTING = window.PROMPTMANAGER_CACHE_BUST !== false;

    if (!ENABLE_CACHE_BUSTING) {
        console.log('[Cache Buster] Disabled');
        return;
    }

    /**
     * Add version parameter to URL
     */
    function addVersionParam(url) {
        if (!url || url.startsWith('data:') || url.startsWith('blob:')) {
            return url;
        }

        const separator = url.includes('?') ? '&' : '?';
        return `${url}${separator}v=${CACHE_VERSION}`;
    }

    /**
     * Update script src attributes
     */
    function bustScriptCache() {
        const scripts = document.querySelectorAll('script[src]');
        let count = 0;

        scripts.forEach(script => {
            const originalSrc = script.getAttribute('src');

            // Skip external CDN scripts and already versioned
            if (originalSrc.startsWith('http') || originalSrc.includes('?v=')) {
                return;
            }

            // Only bust local scripts from /prompt_manager/
            if (originalSrc.includes('/prompt_manager/')) {
                const newSrc = addVersionParam(originalSrc);
                if (newSrc !== originalSrc) {
                    // Create new script element to force reload
                    const newScript = document.createElement('script');
                    newScript.src = newSrc;
                    newScript.type = script.type || 'text/javascript';

                    // Copy other attributes
                    Array.from(script.attributes).forEach(attr => {
                        if (attr.name !== 'src') {
                            newScript.setAttribute(attr.name, attr.value);
                        }
                    });

                    // Replace old script with new one
                    script.parentNode.replaceChild(newScript, script);
                    count++;
                }
            }
        });

        return count;
    }

    /**
     * Update stylesheet href attributes
     */
    function bustStylesheetCache() {
        const links = document.querySelectorAll('link[rel="stylesheet"]');
        let count = 0;

        links.forEach(link => {
            const originalHref = link.getAttribute('href');

            // Skip external CDN stylesheets and already versioned
            if (originalHref.startsWith('http') || originalHref.includes('?v=')) {
                return;
            }

            // Only bust local stylesheets from /prompt_manager/
            if (originalHref.includes('/prompt_manager/')) {
                const newHref = addVersionParam(originalHref);
                if (newHref !== originalHref) {
                    link.href = newHref;
                    count++;
                }
            }
        });

        return count;
    }

    /**
     * Run cache busting on DOM ready
     */
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    function init() {
        const scriptCount = bustScriptCache();
        const styleCount = bustStylesheetCache();

        console.log(`[Cache Buster] Applied v=${CACHE_VERSION} to ${scriptCount} scripts, ${styleCount} stylesheets`);
    }

    // Export for manual use
    window.CacheBuster = {
        version: CACHE_VERSION,
        enabled: ENABLE_CACHE_BUSTING,
        addVersionParam: addVersionParam,
        bust: init
    };
})();
