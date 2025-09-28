/**
 * FilmstripManager Module
 * Manages thumbnail filmstrip navigation for ViewerJS integration
 * @module FilmstripManager
 */
const FilmstripManager = (function() {
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
        console.info('[PromptManager] filmstrip manager skipped outside PromptManager UI context');
        return createStub();
    }

    // Private variables
    const instances = new Map();
    const defaultConfig = {
        position: 'bottom', // 'bottom', 'top', 'left', 'right'
        thumbnailWidth: 120,
        thumbnailHeight: 80,
        spacing: 10,
        scrollSpeed: 300,
        lazyLoad: true,
        lazyLoadOffset: 2, // Load 2 thumbnails ahead
        showIndicator: true,
        autoHide: false,
        autoHideDelay: 3000,
        animationDuration: 300,
        maxVisible: 8, // Maximum visible thumbnails
        centerActive: true, // Center the active thumbnail
        enableKeyboard: true,
        enableWheel: true,
        enableTouch: true,
        // Performance settings
        useIntersectionObserver: true,
        throttleScroll: 100,
        thumbnailQuality: 0.7
    };

    // State tracking
    let activeFilmstrip = null;
    let scrollThrottleTimer = null;

    // Private methods
    function createFilmstrip(viewerId, config = {}) {
        const mergedConfig = Object.assign({}, defaultConfig, config);

        // Create filmstrip container
        const container = document.createElement('div');
        container.className = `filmstrip-container filmstrip-${mergedConfig.position}`;
        container.setAttribute('data-viewer-id', viewerId);

        // Create inner wrapper for thumbnails
        const wrapper = document.createElement('div');
        wrapper.className = 'filmstrip-wrapper';

        // Create thumbnail track
        const track = document.createElement('div');
        track.className = 'filmstrip-track';

        wrapper.appendChild(track);
        container.appendChild(wrapper);

        // Add navigation buttons
        if (mergedConfig.position === 'bottom' || mergedConfig.position === 'top') {
            const prevBtn = createNavButton('prev', '◀');
            const nextBtn = createNavButton('next', '▶');
            container.appendChild(prevBtn);
            container.appendChild(nextBtn);
        }

        // Add position indicator
        if (mergedConfig.showIndicator) {
            const indicator = document.createElement('div');
            indicator.className = 'filmstrip-indicator';
            indicator.textContent = '1 / 1';
            container.appendChild(indicator);
        }

        const filmstripId = generateFilmstripId();

        // Store instance
        instances.set(filmstripId, {
            id: filmstripId,
            viewerId: viewerId,
            container: container,
            wrapper: wrapper,
            track: track,
            config: mergedConfig,
            thumbnails: [],
            activeIndex: 0,
            loadedIndexes: new Set(),
            scrollPosition: 0,
            isVisible: true,
            autoHideTimer: null,
            intersectionObserver: null
        });

        // Set up intersection observer for lazy loading
        if (mergedConfig.lazyLoad && mergedConfig.useIntersectionObserver) {
            setupIntersectionObserver(filmstripId);
        }

        // Attach event handlers
        attachEventHandlers(filmstripId);

        return filmstripId;
    }

    function generateFilmstripId() {
        return `filmstrip_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
    }

    function createNavButton(type, symbol) {
        const button = document.createElement('button');
        button.className = `filmstrip-nav filmstrip-nav-${type}`;
        button.setAttribute('aria-label', type === 'prev' ? 'Previous' : 'Next');
        button.innerHTML = symbol;
        return button;
    }

    function setupIntersectionObserver(filmstripId) {
        const instance = instances.get(filmstripId);
        if (!instance) return;

        const thumbnailWidth = Number(instance.config.thumbnailWidth) || 0;
        const lazyLoadOffset = Number(instance.config.lazyLoadOffset) || 0;
        const horizontalMargin = thumbnailWidth * lazyLoadOffset;

        const options = {
            root: instance.wrapper,
            rootMargin: `0px ${horizontalMargin}px 0px ${horizontalMargin}px`,
            threshold: 0.01
        };

        instance.intersectionObserver = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    const thumbnail = entry.target;
                    const index = parseInt(thumbnail.getAttribute('data-index'));
                    loadThumbnail(filmstripId, index);
                }
            });
        }, options);
    }

    function attachEventHandlers(filmstripId) {
        const instance = instances.get(filmstripId);
        if (!instance) return;

        const container = instance.container;

        // Navigation button handlers
        const prevBtn = container.querySelector('.filmstrip-nav-prev');
        const nextBtn = container.querySelector('.filmstrip-nav-next');

        if (prevBtn) {
            prevBtn.addEventListener('click', () => navigateFilmstrip(filmstripId, -1));
        }

        if (nextBtn) {
            nextBtn.addEventListener('click', () => navigateFilmstrip(filmstripId, 1));
        }

        // Scroll handler for horizontal scrolling
        if (instance.config.enableWheel) {
            instance.wrapper.addEventListener('wheel', (e) => {
                if (e.deltaY !== 0) {
                    e.preventDefault();
                    scrollFilmstrip(filmstripId, e.deltaY > 0 ? 1 : -1);
                }
            });
        }

        // Touch support
        if (instance.config.enableTouch) {
            setupTouchHandlers(filmstripId);
        }

        // Keyboard support
        if (instance.config.enableKeyboard) {
            setupKeyboardHandlers(filmstripId);
        }

        // Auto-hide functionality
        if (instance.config.autoHide) {
            setupAutoHide(filmstripId);
        }
    }

    function setupTouchHandlers(filmstripId) {
        const instance = instances.get(filmstripId);
        if (!instance) return;

        let touchStartX = 0;
        let touchStartScrollLeft = 0;

        instance.wrapper.addEventListener('touchstart', (e) => {
            touchStartX = e.touches[0].clientX;
            touchStartScrollLeft = instance.wrapper.scrollLeft;
        });

        instance.wrapper.addEventListener('touchmove', (e) => {
            const touchX = e.touches[0].clientX;
            const diff = touchStartX - touchX;
            instance.wrapper.scrollLeft = touchStartScrollLeft + diff;
        });
    }

    function setupKeyboardHandlers(filmstripId) {
        const instance = instances.get(filmstripId);
        if (!instance) return;

        // Only handle keyboard events when filmstrip is active
        instance.container.addEventListener('mouseenter', () => {
            activeFilmstrip = filmstripId;
        });

        instance.container.addEventListener('mouseleave', () => {
            if (activeFilmstrip === filmstripId) {
                activeFilmstrip = null;
            }
        });
    }

    function setupAutoHide(filmstripId) {
        const instance = instances.get(filmstripId);
        if (!instance) return;

        const resetAutoHide = () => {
            clearTimeout(instance.autoHideTimer);
            showFilmstrip(filmstripId);

            instance.autoHideTimer = setTimeout(() => {
                hideFilmstrip(filmstripId);
            }, instance.config.autoHideDelay);
        };

        instance.container.addEventListener('mouseenter', () => {
            clearTimeout(instance.autoHideTimer);
            showFilmstrip(filmstripId);
        });

        instance.container.addEventListener('mouseleave', resetAutoHide);

        // Initial auto-hide
        resetAutoHide();
    }

    function populateThumbnails(filmstripId, images) {
        const instance = instances.get(filmstripId);
        if (!instance) return;

        // Clear existing thumbnails
        instance.track.innerHTML = '';
        instance.thumbnails = [];
        instance.loadedIndexes.clear();

        // Create thumbnail elements
        images.forEach((image, index) => {
            const thumbnail = createThumbnail(image, index, instance.config);
            instance.track.appendChild(thumbnail);
            instance.thumbnails.push(thumbnail);

            // Set up intersection observer
            if (instance.intersectionObserver) {
                instance.intersectionObserver.observe(thumbnail);
            }

            // Attach click handler
            thumbnail.addEventListener('click', () => {
                selectThumbnail(filmstripId, index);
            });
        });

        // Load initial visible thumbnails
        if (!instance.config.lazyLoad) {
            instance.thumbnails.forEach((_, index) => {
                loadThumbnail(filmstripId, index);
            });
        } else {
            // Load only visible thumbnails
            loadVisibleThumbnails(filmstripId);
        }

        // Update indicator
        updateIndicator(filmstripId);
    }

    function createThumbnail(image, index, config) {
        const thumbnail = document.createElement('div');
        thumbnail.className = 'filmstrip-thumbnail';
        thumbnail.setAttribute('data-index', index);
        thumbnail.style.width = `${config.thumbnailWidth}px`;
        thumbnail.style.height = `${config.thumbnailHeight}px`;
        thumbnail.style.marginRight = `${config.spacing}px`;

        // Add loading placeholder
        const placeholder = document.createElement('div');
        placeholder.className = 'filmstrip-thumbnail-placeholder';
        thumbnail.appendChild(placeholder);

        // Store image source for lazy loading
        thumbnail.setAttribute('data-src', typeof image === 'string' ? image : image.src);
        thumbnail.setAttribute('data-title', typeof image === 'string' ? '' : (image.alt || image.title || ''));

        return thumbnail;
    }

    function loadThumbnail(filmstripId, index) {
        const instance = instances.get(filmstripId);
        if (!instance || instance.loadedIndexes.has(index)) return;

        const thumbnail = instance.thumbnails[index];
        if (!thumbnail) return;

        const src = thumbnail.getAttribute('data-src');
        const title = thumbnail.getAttribute('data-title');

        const img = new Image();
        img.onload = function() {
            // Remove placeholder
            const placeholder = thumbnail.querySelector('.filmstrip-thumbnail-placeholder');
            if (placeholder) {
                placeholder.remove();
            }

            // Add image
            img.className = 'filmstrip-thumbnail-image';
            thumbnail.appendChild(img);

            // Add title if exists
            if (title) {
                img.setAttribute('title', title);
            }

            // Mark as loaded
            instance.loadedIndexes.add(index);
            thumbnail.classList.add('loaded');
        };

        img.onerror = function() {
            thumbnail.classList.add('error');
        };

        // Apply quality setting for performance
        if (instance.config.thumbnailQuality < 1) {
            // You could add image optimization here if needed
        }

        img.src = src;
    }

    function loadVisibleThumbnails(filmstripId) {
        const instance = instances.get(filmstripId);
        if (!instance) return;

        const wrapperRect = instance.wrapper.getBoundingClientRect();

        instance.thumbnails.forEach((thumbnail, index) => {
            const rect = thumbnail.getBoundingClientRect();

            // Check if thumbnail is in or near viewport
            const isVisible = rect.left < wrapperRect.right + (instance.config.thumbnailWidth * instance.config.lazyLoadOffset) &&
                              rect.right > wrapperRect.left - (instance.config.thumbnailWidth * instance.config.lazyLoadOffset);

            if (isVisible) {
                loadThumbnail(filmstripId, index);
            }
        });
    }

    function selectThumbnail(filmstripId, index, fromViewer = false) {
        const instance = instances.get(filmstripId);
        if (!instance) return;

        // Prevent recursion - if already at this index, just update UI
        if (instance.activeIndex === index) {
            // Still update UI in case it's out of sync
            instance.thumbnails.forEach((thumb, i) => {
                thumb.classList.toggle('active', i === index);
            });
            updateIndicator(filmstripId);
            return;
        }

        // Update active state
        instance.thumbnails.forEach((thumb, i) => {
            thumb.classList.toggle('active', i === index);
        });

        instance.activeIndex = index;

        // Center active thumbnail if configured
        if (instance.config.centerActive) {
            centerThumbnail(filmstripId, index);
        }

        // Update indicator
        updateIndicator(filmstripId);

        // Emit event for viewer integration
        const event = new CustomEvent('filmstrip:select', {
            detail: {
                filmstripId: filmstripId,
                viewerId: instance.viewerId,
                index: index
            }
        });
        document.dispatchEvent(event);

        // Only trigger viewer update if not called from viewer (to prevent loop)
        if (!fromViewer) {
            // Try to use ViewerIntegration first (preferred for metadata support)
            if (window.ViewerIntegration && window.ViewerIntegration.showImage) {
                // Try to find the active integration
                const active = window.ViewerIntegration.getActiveIntegration?.();
                if (active?.id) {
                    window.ViewerIntegration.showImage(index, active.id);
                    return;
                }
            }

            // Fallback to direct ViewerManager (no metadata panel)
            if (window.ViewerManager) {
                const viewer = ViewerManager.getViewer(instance.viewerId);
                if (viewer) {
                    viewer.view(index);
                }
            }
        }
    }

    function centerThumbnail(filmstripId, index) {
        const instance = instances.get(filmstripId);
        if (!instance) return;

        const thumbnail = instance.thumbnails[index];
        if (!thumbnail) return;

        const wrapperWidth = instance.wrapper.offsetWidth;
        const thumbnailOffset = thumbnail.offsetLeft;
        const thumbnailWidth = instance.config.thumbnailWidth + instance.config.spacing;

        const scrollPosition = thumbnailOffset - (wrapperWidth / 2) + (thumbnailWidth / 2);

        instance.wrapper.scrollTo({
            left: scrollPosition,
            behavior: 'smooth'
        });
    }

    function scrollFilmstrip(filmstripId, direction) {
        const instance = instances.get(filmstripId);
        if (!instance) return;

        const scrollAmount = (instance.config.thumbnailWidth + instance.config.spacing) * 3;
        const currentScroll = instance.wrapper.scrollLeft;
        const newScroll = currentScroll + (scrollAmount * direction);

        instance.wrapper.scrollTo({
            left: newScroll,
            behavior: 'smooth'
        });

        // Throttled lazy load check
        if (scrollThrottleTimer) {
            clearTimeout(scrollThrottleTimer);
        }

        scrollThrottleTimer = setTimeout(() => {
            loadVisibleThumbnails(filmstripId);
        }, instance.config.throttleScroll);
    }

    function navigateFilmstrip(filmstripId, direction) {
        const instance = instances.get(filmstripId);
        if (!instance) return;

        const newIndex = Math.max(0, Math.min(instance.thumbnails.length - 1, instance.activeIndex + direction));
        selectThumbnail(filmstripId, newIndex);
    }

    function updateIndicator(filmstripId) {
        const instance = instances.get(filmstripId);
        if (!instance || !instance.config.showIndicator) return;

        const indicator = instance.container.querySelector('.filmstrip-indicator');
        if (indicator) {
            indicator.textContent = `${instance.activeIndex + 1} / ${instance.thumbnails.length}`;
        }
    }

    function showFilmstrip(filmstripId) {
        const instance = instances.get(filmstripId);
        if (!instance || instance.isVisible) return;

        instance.container.classList.remove('hidden');
        instance.isVisible = true;
    }

    function hideFilmstrip(filmstripId) {
        const instance = instances.get(filmstripId);
        if (!instance || !instance.isVisible) return;

        instance.container.classList.add('hidden');
        instance.isVisible = false;
    }

    function destroyFilmstrip(filmstripId) {
        const instance = instances.get(filmstripId);
        if (!instance) return;

        // Clean up intersection observer
        if (instance.intersectionObserver) {
            instance.intersectionObserver.disconnect();
        }

        // Clear timers
        clearTimeout(instance.autoHideTimer);

        // Remove DOM element
        if (instance.container.parentNode) {
            instance.container.parentNode.removeChild(instance.container);
        }

        // Remove from instances
        instances.delete(filmstripId);
    }

    // Public API
    return {
        /**
         * Initialize FilmstripManager
         * @param {Object} config - Global configuration
         */
        init: function(config = {}) {
            Object.assign(defaultConfig, config);

            // Inject required CSS
            this.injectStyles();

            // Set up global keyboard handler
            document.addEventListener('keydown', (e) => {
                if (!activeFilmstrip) return;

                const instance = instances.get(activeFilmstrip);
                if (!instance || !instance.config.enableKeyboard) return;

                switch(e.key) {
                    case 'Home':
                        e.preventDefault();
                        selectThumbnail(activeFilmstrip, 0);
                        break;
                    case 'End':
                        e.preventDefault();
                        selectThumbnail(activeFilmstrip, instance.thumbnails.length - 1);
                        break;
                    case 'PageUp':
                        e.preventDefault();
                        navigateFilmstrip(activeFilmstrip, -instance.config.maxVisible);
                        break;
                    case 'PageDown':
                        e.preventDefault();
                        navigateFilmstrip(activeFilmstrip, instance.config.maxVisible);
                        break;
                }
            });

            return this;
        },

        /**
         * Create a new filmstrip instance
         * @param {string} viewerId - Associated ViewerManager instance ID
         * @param {Object} config - Configuration options
         * @returns {string} Filmstrip ID
         */
        create: function(viewerId, config = {}) {
            return createFilmstrip(viewerId, config);
        },

        /**
         * Attach filmstrip to a container
         * @param {string} filmstripId - Filmstrip ID
         * @param {HTMLElement|string} container - Container element or selector
         */
        attach: function(filmstripId, container) {
            const instance = instances.get(filmstripId);
            if (!instance) return false;

            if (typeof container === 'string') {
                container = document.querySelector(container);
            }

            if (!container) return false;

            container.appendChild(instance.container);
            return true;
        },

        /**
         * Populate filmstrip with images
         * @param {string} filmstripId - Filmstrip ID
         * @param {Array} images - Array of image sources or image objects
         */
        populate: function(filmstripId, images) {
            populateThumbnails(filmstripId, images);
        },

        /**
         * Update active thumbnail
         * @param {string} filmstripId - Filmstrip ID
         * @param {number} index - Image index
         * @param {boolean} fromViewer - Whether this is called from viewer (prevents loop)
         */
        setActive: function(filmstripId, index, fromViewer = false) {
            selectThumbnail(filmstripId, index, fromViewer);
        },

        /**
         * Get active index
         * @param {string} filmstripId - Filmstrip ID
         * @returns {number} Active index
         */
        getActive: function(filmstripId) {
            const instance = instances.get(filmstripId);
            return instance ? instance.activeIndex : -1;
        },

        /**
         * Navigate filmstrip
         * @param {string} filmstripId - Filmstrip ID
         * @param {string} direction - 'prev' or 'next'
         */
        navigate: function(filmstripId, direction) {
            navigateFilmstrip(filmstripId, direction === 'prev' ? -1 : 1);
        },

        /**
         * Show filmstrip
         * @param {string} filmstripId - Filmstrip ID
         */
        show: function(filmstripId) {
            showFilmstrip(filmstripId);
        },

        /**
         * Hide filmstrip
         * @param {string} filmstripId - Filmstrip ID
         */
        hide: function(filmstripId) {
            hideFilmstrip(filmstripId);
        },

        /**
         * Toggle filmstrip visibility
         * @param {string} filmstripId - Filmstrip ID
         */
        toggle: function(filmstripId) {
            const instance = instances.get(filmstripId);
            if (!instance) return;

            if (instance.isVisible) {
                hideFilmstrip(filmstripId);
            } else {
                showFilmstrip(filmstripId);
            }
        },

        /**
         * Update configuration
         * @param {string} filmstripId - Filmstrip ID
         * @param {Object} config - New configuration
         */
        updateConfig: function(filmstripId, config) {
            const instance = instances.get(filmstripId);
            if (!instance) return false;

            Object.assign(instance.config, config);

            // Apply changes that can be updated dynamically
            if (config.position) {
                instance.container.className = `filmstrip-container filmstrip-${config.position}`;
            }

            return true;
        },

        /**
         * Destroy filmstrip instance
         * @param {string} filmstripId - Filmstrip ID
         */
        destroy: function(filmstripId) {
            destroyFilmstrip(filmstripId);
        },

        /**
         * Destroy all filmstrip instances
         */
        destroyAll: function() {
            instances.forEach((_, id) => {
                destroyFilmstrip(id);
            });
        },

        /**
         * Get all filmstrip IDs
         * @returns {Array} Array of filmstrip IDs
         */
        getAllIds: function() {
            return Array.from(instances.keys());
        },

        /**
         * Inject required CSS styles
         */
        injectStyles: function() {
            if (document.getElementById('filmstrip-manager-styles')) return;

            const styles = document.createElement('style');
            styles.id = 'filmstrip-manager-styles';
            styles.textContent = `
                /* Filmstrip container styles */
                .filmstrip-container {
                    position: fixed;
                    background: rgba(0, 0, 0, 0.9);
                    z-index: 9000;
                    display: flex;
                    align-items: center;
                    transition: opacity 0.3s, transform 0.3s;
                }

                .filmstrip-container.hidden {
                    opacity: 0;
                    pointer-events: none;
                }

                /* Position variations */
                .filmstrip-bottom {
                    bottom: 0;
                    left: 0;
                    right: 0;
                    height: 120px;
                    flex-direction: row;
                }

                .filmstrip-top {
                    top: 0;
                    left: 0;
                    right: 0;
                    height: 120px;
                    flex-direction: row;
                }

                .filmstrip-left {
                    top: 0;
                    bottom: 0;
                    left: 0;
                    width: 150px;
                    flex-direction: column;
                }

                .filmstrip-right {
                    top: 0;
                    bottom: 0;
                    right: 0;
                    width: 150px;
                    flex-direction: column;
                }

                /* Wrapper and track */
                .filmstrip-wrapper {
                    flex: 1;
                    overflow-x: auto;
                    overflow-y: hidden;
                    padding: 10px;
                    scrollbar-width: thin;
                    scrollbar-color: #666 #333;
                }

                .filmstrip-wrapper::-webkit-scrollbar {
                    height: 8px;
                }

                .filmstrip-wrapper::-webkit-scrollbar-track {
                    background: #333;
                }

                .filmstrip-wrapper::-webkit-scrollbar-thumb {
                    background: #666;
                    border-radius: 4px;
                }

                .filmstrip-wrapper::-webkit-scrollbar-thumb:hover {
                    background: #888;
                }

                .filmstrip-track {
                    display: flex;
                    gap: 10px;
                    align-items: center;
                }

                /* Thumbnails */
                .filmstrip-thumbnail {
                    flex-shrink: 0;
                    position: relative;
                    cursor: pointer;
                    border: 2px solid transparent;
                    border-radius: 4px;
                    overflow: hidden;
                    transition: all 0.2s;
                    background: #222;
                }

                .filmstrip-thumbnail:hover {
                    border-color: #666;
                    transform: scale(1.05);
                }

                .filmstrip-thumbnail.active {
                    border-color: #4fc3f7;
                    box-shadow: 0 0 10px rgba(79, 195, 247, 0.5);
                }

                .filmstrip-thumbnail.error {
                    background: #300;
                }

                .filmstrip-thumbnail-placeholder {
                    position: absolute;
                    top: 50%;
                    left: 50%;
                    transform: translate(-50%, -50%);
                    width: 30px;
                    height: 30px;
                    border: 3px solid #444;
                    border-top-color: #888;
                    border-radius: 50%;
                    animation: spin 1s linear infinite;
                }

                .filmstrip-thumbnail-image {
                    width: 100%;
                    height: 100%;
                    object-fit: cover;
                }

                /* Navigation buttons */
                .filmstrip-nav {
                    background: rgba(0, 0, 0, 0.8);
                    border: 1px solid #444;
                    color: #fff;
                    font-size: 20px;
                    padding: 10px 15px;
                    cursor: pointer;
                    transition: all 0.2s;
                    z-index: 1;
                }

                .filmstrip-nav:hover {
                    background: rgba(255, 255, 255, 0.1);
                    border-color: #666;
                }

                .filmstrip-nav:active {
                    transform: scale(0.95);
                }

                .filmstrip-nav:disabled {
                    opacity: 0.3;
                    cursor: not-allowed;
                }

                /* Indicator */
                .filmstrip-indicator {
                    position: absolute;
                    top: 10px;
                    right: 10px;
                    background: rgba(0, 0, 0, 0.8);
                    color: #fff;
                    padding: 5px 10px;
                    border-radius: 4px;
                    font-size: 12px;
                    font-family: system-ui, -apple-system, sans-serif;
                    pointer-events: none;
                }

                /* Animations */
                @keyframes spin {
                    to { transform: translate(-50%, -50%) rotate(360deg); }
                }

                /* Responsive adjustments */
                @media (max-width: 768px) {
                    .filmstrip-bottom,
                    .filmstrip-top {
                        height: 80px;
                    }

                    .filmstrip-thumbnail {
                        width: 80px !important;
                        height: 60px !important;
                    }
                }

                /* Touch device optimizations */
                @media (hover: none) {
                    .filmstrip-wrapper {
                        -webkit-overflow-scrolling: touch;
                    }
                }
            `;
            document.head.appendChild(styles);
        }
    };
})();

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = FilmstripManager;
}

// Attach to window for global access
if (typeof window !== 'undefined') {
    window.FilmstripManager = FilmstripManager;
}
