/**
 * ViewerManager Module
 * Centralized manager for ViewerJS instances across the application
 * @module ViewerManager
 */
const ViewerManager = (function() {
    'use strict';

    function createStub() {
        const target = {};
        const stub = new Proxy(target, {
            get: (obj, prop) => {
                if (prop in obj) {
                    return obj[prop];
                }
                return (...args) => {
                    if (prop === 'create' || prop === 'init') {
                        return null;
                    }
                    return stub;
                };
            },
            set: (obj, prop, value) => {
                obj[prop] = value;
                return true;
            },
        });
        return stub;
    }

    if (typeof window === 'undefined') {
        return createStub();
    }
    const pmActive = (window.location?.pathname || '').includes('/prompt_manager');
    if (!pmActive) {
        console.info('[PromptManager] viewer manager skipped outside PromptManager UI context');
        return createStub();
    }

    // Private variables
    const instances = new Map();
    let globalConfig = {
        // Default ViewerJS configuration
        inline: false,
        button: true,
        navbar: true,
        title: true,
        toolbar: {
            zoomIn: true,
            zoomOut: true,
            oneToOne: true,
            reset: true,
            prev: true,
            play: false,
            next: true,
            rotateLeft: true,
            rotateRight: true,
            flipHorizontal: true,
            flipVertical: true,
        },
        tooltip: true,
        movable: true,
        zoomable: true,
        rotatable: true,
        scalable: true,
        transition: true,
        fullscreen: true,
        keyboard: true,
        // Custom theme support
        className: 'promptmanager-viewer',
        // Use data-full-src for full resolution images instead of thumbnails
        url: function(image) {
            // Try data-full-src first (set by gallery), fall back to src
            return image.getAttribute('data-full-src') || image.src;
        },
        // Performance optimizations
        loading: true,
        loop: true,
        minZoomRatio: 0.1,
        maxZoomRatio: 10,
        zoomRatio: 0.1,
        minWidth: 200,
        minHeight: 100,
        // Event callbacks
        ready: null,
        shown: null,
        hidden: null,
        viewed: null,
        move: null,
        moved: null,
        rotate: null,
        rotated: null,
        scale: null,
        scaled: null,
        zoom: null,
        zoomed: null,
        play: null,
        stop: null
    };

    // Keyboard shortcut mappings
    const keyboardShortcuts = {
        'Escape': 'hide',
        'Space': 'toggle',
        'Enter': 'fullscreen',
        'ArrowLeft': 'prev',
        'ArrowRight': 'next',
        'ArrowUp': 'zoomIn',
        'ArrowDown': 'zoomOut',
        '0': 'reset',
        '1': 'oneToOne',
        'r': 'rotateRight',
        'R': 'rotateLeft',
        'h': 'flipHorizontal',
        'v': 'flipVertical',
        'f': 'fullscreen',
        '+': 'zoomIn',
        '-': 'zoomOut',
        '?': 'showHelp'
    };

    // Active viewer tracking
    let activeViewer = null;
    let keyboardHandlersAttached = false;
    let helpOverlay = null;

    // Private methods
    function validateConfig(config) {
        if (!config || typeof config !== 'object') {
            throw new Error('ViewerManager: Invalid configuration object');
        }
        return true;
    }

    function createInstance(container, config = {}) {
        if (!container) {
            throw new Error('ViewerManager: Container element is required');
        }

        // Ensure ViewerJS library is loaded
        if (typeof Viewer === 'undefined') {
            throw new Error('ViewerManager: ViewerJS library not loaded. Please include viewer.js');
        }

        // Merge configurations
        const instanceConfig = Object.assign({}, globalConfig, config);

        // Add custom event handlers
        const originalCallbacks = {};
        ['ready', 'shown', 'hidden', 'viewed'].forEach(event => {
            originalCallbacks[event] = instanceConfig[event];
            instanceConfig[event] = function(e) {
                handleViewerEvent(event, e, this);
                if (originalCallbacks[event]) {
                    originalCallbacks[event].call(this, e);
                }
            };
        });

        // Create ViewerJS instance
        const viewer = new Viewer(container, instanceConfig);

        // Store instance
        const id = generateInstanceId();
        instances.set(id, {
            viewer: viewer,
            container: container,
            config: instanceConfig,
            metadata: {},
            created: new Date()
        });

        return id;
    }

    function generateInstanceId() {
        return `viewer_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
    }

    function handleViewerEvent(event, e, viewer) {
        // Track active viewer
        if (event === 'shown') {
            // Validate viewer instance before tracking
            if (viewer && typeof viewer.show === 'function') {
                activeViewer = viewer;
                attachKeyboardHandlers();
            }
        } else if (event === 'hidden') {
            if (activeViewer === viewer) {
                activeViewer = null;
                detachKeyboardHandlers();
            }
        }

        // Emit custom events for integration
        const customEvent = new CustomEvent(`viewer:${event}`, {
            detail: { viewer, event: e }
        });
        document.dispatchEvent(customEvent);
    }

    function attachKeyboardHandlers() {
        if (keyboardHandlersAttached) return;
        if (!activeViewer) return;
        
        document.addEventListener('keydown', handleKeyPress);
        keyboardHandlersAttached = true;
    }

    function detachKeyboardHandlers() {
        if (!keyboardHandlersAttached) return;
        document.removeEventListener('keydown', handleKeyPress);
        keyboardHandlersAttached = false;
    }

    function handleKeyPress(e) {
        // Don't interfere with form inputs
        if (e.target.matches('input, textarea, select')) return;

        const action = keyboardShortcuts[e.key] || keyboardShortcuts[e.code];
        if (action && activeViewer) {
            e.preventDefault();
            executeAction(activeViewer, action);
        }
    }

    function executeAction(viewer, action) {
        // activeViewer is already validated, no need for additional checks

        switch(action) {
            case 'hide':
                viewer.hide();
                break;
            case 'toggle':
                viewer.toggle();
                break;
            case 'fullscreen':
                viewer.full();
                break;
            case 'prev':
                if (viewer.prev) {
                    viewer.prev(true);  // Pass true for loop parameter
                } else {
                    console.warn('Viewer.prev method not available');
                }
                break;
            case 'next':
                if (viewer.next) {
                    viewer.next(true);  // Pass true for loop parameter
                } else {
                    console.warn('Viewer.next method not available');
                }
                break;
            case 'zoomIn':
                viewer.zoom(0.1);
                break;
            case 'zoomOut':
                viewer.zoom(-0.1);
                break;
            case 'reset':
                viewer.reset();
                break;
            case 'oneToOne':
                viewer.zoomTo(1);
                break;
            case 'rotateRight':
                viewer.rotate(90);
                break;
            case 'rotateLeft':
                viewer.rotate(-90);
                break;
            case 'flipHorizontal':
                viewer.scaleX(-viewer.imageData.scaleX || -1);
                break;
            case 'flipVertical':
                viewer.scaleY(-viewer.imageData.scaleY || -1);
                break;
            case 'showHelp':
                showHelpOverlay();
                break;
        }
    }

    function showHelpOverlay() {
        if (helpOverlay) {
            helpOverlay.style.display = 'block';
            return;
        }

        helpOverlay = document.createElement('div');
        helpOverlay.className = 'viewer-help-overlay';
        helpOverlay.innerHTML = `
            <div class="viewer-help-content">
                <h3>Keyboard Shortcuts</h3>
                <button class="viewer-help-close">&times;</button>
                <dl>
                    <dt>Esc</dt><dd>Close viewer</dd>
                    <dt>Space</dt><dd>Toggle view</dd>
                    <dt>Enter / F</dt><dd>Fullscreen</dd>
                    <dt>←/→</dt><dd>Previous/Next image</dd>
                    <dt>↑/↓</dt><dd>Zoom in/out</dd>
                    <dt>+/-</dt><dd>Zoom in/out</dd>
                    <dt>0</dt><dd>Reset view</dd>
                    <dt>1</dt><dd>Original size</dd>
                    <dt>R/Shift+R</dt><dd>Rotate right/left</dd>
                    <dt>H</dt><dd>Flip horizontal</dd>
                    <dt>V</dt><dd>Flip vertical</dd>
                    <dt>?</dt><dd>Show this help</dd>
                </dl>
            </div>
        `;

        document.body.appendChild(helpOverlay);

        // Close handler
        helpOverlay.querySelector('.viewer-help-close').addEventListener('click', () => {
            helpOverlay.style.display = 'none';
        });

        helpOverlay.addEventListener('click', (e) => {
            if (e.target === helpOverlay) {
                helpOverlay.style.display = 'none';
            }
        });
    }

    function destroyInstance(id) {
        const instance = instances.get(id);
        if (!instance) return false;

        // Clean up viewer
        instance.viewer.destroy();

        // Clean up event listeners
        if (activeViewer === instance.viewer) {
            activeViewer = null;
            detachKeyboardHandlers();
        }

        // Remove from instances
        instances.delete(id);
        return true;
    }

    // Public API
    return {
        /**
         * Initialize ViewerManager with global configuration
         * @param {Object} config - Global ViewerJS configuration
         */
        init: function(config = {}) {
            if (config && validateConfig(config)) {
                Object.assign(globalConfig, config);
            }

            // Inject required CSS if not present
            if (!document.querySelector('link[href*="viewer.css"]')) {
                const link = document.createElement('link');
                link.rel = 'stylesheet';
                link.href = '/prompt_manager/vendor/viewerjs/dist/viewer.css';
                document.head.appendChild(link);
            }

            // Add custom styles for theme support
            this.injectCustomStyles();

            return this;
        },

        /**
         * Create a new viewer instance
         * @param {HTMLElement|string} container - Container element or selector
         * @param {Object} config - Instance-specific configuration
         * @returns {string} Instance ID
         */
        create: function(container, config = {}) {
            if (typeof container === 'string') {
                container = document.querySelector(container);
            }

            if (!container) {
                throw new Error('ViewerManager: Invalid container');
            }

            return createInstance(container, config);
        },

        /**
         * Get viewer instance by ID
         * @param {string} id - Instance ID
         * @returns {Object|null} Viewer instance data
         */
        get: function(id) {
            return instances.get(id) || null;
        },

        /**
         * Get the raw ViewerJS instance
         * @param {string} id - Instance ID
         * @returns {Viewer|null} ViewerJS instance
         */
        getViewer: function(id) {
            const instance = instances.get(id);
            return instance ? instance.viewer : null;
        },

        /**
         * Update viewer images
         * @param {string} id - Instance ID
         * @param {Array} images - New images array
         */
        update: function(id, images) {
            const instance = instances.get(id);
            if (!instance) return false;

            instance.viewer.update();
            return true;
        },

        /**
         * Show viewer
         * @param {string} id - Instance ID
         * @param {number} index - Image index to show
         */
        show: function(id, index = 0) {
            const instance = instances.get(id);
            if (!instance) return false;

            instance.viewer.show();
            if (index > 0) {
                instance.viewer.view(index);
            }
            return true;
        },

        /**
         * Hide viewer
         * @param {string} id - Instance ID
         */
        hide: function(id) {
            const instance = instances.get(id);
            if (!instance) return false;

            instance.viewer.hide();
            return true;
        },

        /**
         * Destroy viewer instance
         * @param {string} id - Instance ID
         */
        destroy: function(id) {
            return destroyInstance(id);
        },

        /**
         * Destroy all viewer instances
         */
        destroyAll: function() {
            instances.forEach((instance, id) => {
                destroyInstance(id);
            });
        },

        /**
         * Get all instance IDs
         * @returns {Array} Array of instance IDs
         */
        getAllIds: function() {
            return Array.from(instances.keys());
        },

        /**
         * Set metadata for instance
         * @param {string} id - Instance ID
         * @param {Object} metadata - Metadata object
         */
        setMetadata: function(id, metadata) {
            const instance = instances.get(id);
            if (!instance) return false;

            instance.metadata = Object.assign(instance.metadata, metadata);
            return true;
        },

        /**
         * Get metadata for instance
         * @param {string} id - Instance ID
         * @returns {Object|null} Metadata object
         */
        getMetadata: function(id) {
            const instance = instances.get(id);
            return instance ? instance.metadata : null;
        },

        /**
         * Apply theme to viewers
         * @param {string} theme - Theme name ('dark', 'light', 'auto')
         */
        setTheme: function(theme) {
            document.documentElement.setAttribute('data-viewer-theme', theme);
        },

        /**
         * Get current theme
         * @returns {string} Current theme
         */
        getTheme: function() {
            return document.documentElement.getAttribute('data-viewer-theme') || 'auto';
        },

        /**
         * Update global configuration
         * @param {Object} config - New configuration options
         */
        updateConfig: function(config) {
            if (validateConfig(config)) {
                Object.assign(globalConfig, config);
            }
        },

        /**
         * Get global configuration
         * @returns {Object} Global configuration
         */
        getConfig: function() {
            return Object.assign({}, globalConfig);
        },

        /**
         * Show keyboard shortcuts help
         */
        showHelp: function() {
            showHelpOverlay();
        },

        /**
         * Inject custom CSS styles
         */
        injectCustomStyles: function() {
            if (document.getElementById('viewer-manager-styles')) return;

            const styles = document.createElement('style');
            styles.id = 'viewer-manager-styles';
            styles.textContent = `
                /* Dark theme for ViewerJS */
                [data-viewer-theme="dark"] .viewer-backdrop {
                    background-color: rgba(0, 0, 0, 0.95);
                }

                [data-viewer-theme="dark"] .viewer-navbar {
                    background-color: rgba(0, 0, 0, 0.8);
                }

                [data-viewer-theme="dark"] .viewer-title {
                    color: #ffffff;
                }

                [data-viewer-theme="dark"] .viewer-toolbar > li {
                    background-color: rgba(0, 0, 0, 0.8);
                    color: #ffffff;
                }

                [data-viewer-theme="dark"] .viewer-toolbar > li:hover {
                    background-color: rgba(255, 255, 255, 0.1);
                }

                /* Help overlay styles */
                .viewer-help-overlay {
                    display: none;
                    position: fixed;
                    top: 0;
                    left: 0;
                    right: 0;
                    bottom: 0;
                    background-color: rgba(0, 0, 0, 0.8);
                    z-index: 9999;
                    animation: fadeIn 0.3s;
                }

                .viewer-help-content {
                    position: absolute;
                    top: 50%;
                    left: 50%;
                    transform: translate(-50%, -50%);
                    background-color: #1a1a1a;
                    border: 1px solid #333;
                    border-radius: 8px;
                    padding: 30px;
                    max-width: 500px;
                    max-height: 80vh;
                    overflow-y: auto;
                    color: #ffffff;
                }

                .viewer-help-content h3 {
                    margin-top: 0;
                    margin-bottom: 20px;
                    font-size: 24px;
                    color: #ffffff;
                }

                .viewer-help-close {
                    position: absolute;
                    top: 10px;
                    right: 10px;
                    background: transparent;
                    border: none;
                    color: #ffffff;
                    font-size: 24px;
                    cursor: pointer;
                    padding: 5px 10px;
                }

                .viewer-help-close:hover {
                    color: #ff6b6b;
                }

                .viewer-help-content dl {
                    display: grid;
                    grid-template-columns: 120px 1fr;
                    gap: 10px 20px;
                    margin: 0;
                }

                .viewer-help-content dt {
                    font-family: 'Courier New', monospace;
                    background-color: #333;
                    padding: 5px 10px;
                    border-radius: 4px;
                    text-align: center;
                    color: #4fc3f7;
                }

                .viewer-help-content dd {
                    margin: 0;
                    padding: 5px 0;
                    color: #e0e0e0;
                }

                @keyframes fadeIn {
                    from { opacity: 0; }
                    to { opacity: 1; }
                }

                /* Custom viewer container styles */
                .promptmanager-viewer {
                    font-family: system-ui, -apple-system, sans-serif;
                }

                /* Loading indicator */
                .viewer-loading::after {
                    content: 'Loading...';
                    position: absolute;
                    top: 50%;
                    left: 50%;
                    transform: translate(-50%, -50%);
                    color: #ffffff;
                    font-size: 18px;
                    z-index: 1;
                }

                /* Performance optimizations */
                .viewer-move {
                    cursor: move;
                }

                .viewer-fade {
                    opacity: 0;
                }

                .viewer-in {
                    opacity: 1;
                }

                .viewer-transition {
                    transition: all 0.3s;
                }
            `;
            document.head.appendChild(styles);
        },

        /**
         * Get statistics about current instances
         * @returns {Object} Statistics object
         */
        getStats: function() {
            return {
                totalInstances: instances.size,
                activeViewer: activeViewer !== null,
                instanceIds: Array.from(instances.keys()),
                created: Array.from(instances.values()).map(i => i.created)
            };
        },

        /**
         * Register custom keyboard shortcut
         * @param {string} key - Key or key combination
         * @param {string} action - Action to perform
         */
        registerShortcut: function(key, action) {
            keyboardShortcuts[key] = action;
        },

        /**
         * Unregister keyboard shortcut
         * @param {string} key - Key to unregister
         */
        unregisterShortcut: function(key) {
            delete keyboardShortcuts[key];
        },

        /**
         * Get all keyboard shortcuts
         * @returns {Object} Keyboard shortcuts map
         */
        getShortcuts: function() {
            return Object.assign({}, keyboardShortcuts);
        }
    };
})();

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = ViewerManager;
}

// Attach to window for global access
if (typeof window !== 'undefined') {
    window.ViewerManager = ViewerManager;
}
