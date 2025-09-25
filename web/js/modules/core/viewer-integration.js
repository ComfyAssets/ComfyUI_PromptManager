/**
 * ViewerJS Integration Module
 * Orchestrates ViewerManager, FilmstripManager, and MetadataManager
 * @module ViewerIntegration
 */
const ViewerIntegration = (function() {
    'use strict';

    function createStub() {
        const target = {};
        const stub = new Proxy(target, {
            get: (obj, prop) => {
                if (prop in obj) {
                    return obj[prop];
                }
                return (...args) => {
                    if (prop === 'initGallery' || prop === 'initMetadata' || prop === 'initCollections' || prop === 'initDashboard') {
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
        console.info('[PromptManager] viewer integration skipped outside PromptManager UI context');
        return createStub();
    }

    // Private variables
    const integrations = new Map();
    let initialized = false;
    let globalConfig = {
        viewer: {
            theme: 'dark',
            toolbar: true,
            navbar: true,
            title: true,
            fullscreen: true,
            keyboard: true
        },
        filmstrip: {
            enabled: true,
            position: 'bottom',
            autoHide: false,
            lazyLoad: true,
            thumbnailWidth: 120,
            thumbnailHeight: 80
        },
        metadata: {
            enabled: true,
            position: 'right',
            autoShow: true,
            showCopyButtons: true,
            enableCache: true
        },
        selectors: {
            gallery: '.gallery-grid',
            galleryImage: '.gallery-item .gallery-image img',
            metadataContainer: '.metadata-display',
            collectionGrid: '.collection-grid',
            dashboardRecent: '.recent-images'
        }
    };

    // Integration state
    let activeIntegration = null;

    // Private methods
    function initializeModules() {
        if (!window.ViewerManager || !window.FilmstripManager || !window.MetadataManager) {
            console.error('ViewerIntegration: Required modules not loaded');
            return false;
        }

        // Initialize managers with global config
        ViewerManager.init(globalConfig.viewer);
        FilmstripManager.init(globalConfig.filmstrip);
        MetadataManager.init(globalConfig.metadata);

        // Set theme
        ViewerManager.setTheme(globalConfig.viewer.theme);

        initialized = true;
        return true;
    }

    function createIntegration(pageType, config = {}) {
        const integrationId = `integration_${pageType}_${Date.now()}`;
        const mergedConfig = Object.assign({}, globalConfig, config);

        const integration = {
            id: integrationId,
            pageType: pageType,
            config: mergedConfig,
            viewerId: null,
            filmstripId: null,
            metadataPanelId: null,
            container: null,
            images: [],
            initialized: false
        };

        integrations.set(integrationId, integration);
        return integrationId;
    }

    function setupGalleryIntegration(integrationId) {
        const integration = integrations.get(integrationId);
        if (!integration) return false;

        const container = document.querySelector(integration.config.selectors.gallery);
        if (!container) {
            console.error('Gallery container not found');
            return false;
        }

        integration.container = container;

        // Find all images in gallery
        const images = Array.from(container.querySelectorAll(integration.config.selectors.galleryImage || 'img'));
        integration.images = images.map(img => ({
            src: img.src,
            alt: img.alt || '',
            title: img.getAttribute('title') || img.alt || ''
        }));

        // Create viewer instance
        integration.viewerId = ViewerManager.create(container, {
            filter(image) {
                return image.tagName === 'IMG';
            },
            url(image) {
                return image.src;
            },
            viewed(e) {
                const index = e.detail.index;
                handleImageViewed(integrationId, index);
            }
        });

        // Create filmstrip if enabled
        if (integration.config.filmstrip.enabled) {
            integration.filmstripId = FilmstripManager.create(
                integration.viewerId,
                integration.config.filmstrip
            );

            // Populate filmstrip with gallery images
            FilmstripManager.populate(integration.filmstripId, integration.images);

            // Attach to viewer container
            const viewerContainer = document.querySelector('.viewer-container');
            if (viewerContainer) {
                FilmstripManager.attach(integration.filmstripId, viewerContainer);
            }
        }

        // Create metadata panel if enabled
        if (integration.config.metadata.enabled) {
            integration.metadataPanelId = MetadataManager.createPanel(integration.config.metadata.panel);
            MetadataManager.attachPanel(integration.metadataPanelId, document.body);
        }

        // Add click handlers to gallery items (not just images)
        const galleryItems = container.querySelectorAll('.gallery-item');
        galleryItems.forEach((item, index) => {
            item.style.cursor = 'pointer';
            item.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                showImage(integrationId, index);
            });
        });

        integration.initialized = true;
        return true;
    }

    function setupMetadataPageIntegration(integrationId) {
        const integration = integrations.get(integrationId);
        if (!integration) return false;

        // Find drag-drop area and preview area
        const dropZone = document.querySelector('.drag-drop-area');
        const previewArea = document.querySelector('.image-preview-area');

        if (!dropZone || !previewArea) {
            console.error('Metadata page elements not found');
            return false;
        }

        // Create viewer for preview
        integration.viewerId = ViewerManager.create(previewArea, {
            inline: true,
            toolbar: false,
            navbar: false,
            title: false,
            transition: false
        });

        // Create metadata panel
        integration.metadataPanelId = MetadataManager.createPanel({
            position: 'bottom',
            collapsible: false,
            showExportButton: true,
            autoShow: true
        });

        // Attach panel to metadata display area
        const metadataDisplay = document.querySelector('.metadata-display');
        if (metadataDisplay) {
            MetadataManager.attachPanel(integration.metadataPanelId, metadataDisplay);
        }

        // Handle file drops
        dropZone.addEventListener('drop', async (e) => {
            e.preventDefault();
            const files = Array.from(e.dataTransfer.files);
            const imageFile = files.find(f => f.type.startsWith('image/'));

            if (imageFile) {
                await handleImageUpload(integrationId, imageFile);
            }
        });

        dropZone.addEventListener('dragover', (e) => {
            e.preventDefault();
            dropZone.classList.add('drag-over');
        });

        dropZone.addEventListener('dragleave', () => {
            dropZone.classList.remove('drag-over');
        });

        integration.initialized = true;
        return true;
    }

    function setupCollectionsIntegration(integrationId) {
        const integration = integrations.get(integrationId);
        if (!integration) return false;

        const container = document.querySelector(integration.config.selectors.collectionGrid);
        if (!container) {
            console.error('Collections container not found');
            return false;
        }

        integration.container = container;

        // Similar to gallery but with collection-specific features
        const collections = container.querySelectorAll('.collection-item');

        collections.forEach(collection => {
            const images = Array.from(collection.querySelectorAll('img'));
            if (images.length === 0) return;

            // Create a viewer for each collection
            const collectionViewer = ViewerManager.create(collection, {
                filter(image) {
                    return image.tagName === 'IMG';
                },
                title: true,
                navbar: true
            });

            // Store viewer reference
            collection.setAttribute('data-viewer-id', collectionViewer);

            // Add click handler
            collection.addEventListener('click', (e) => {
                if (e.target.tagName === 'IMG') {
                    e.preventDefault();
                    ViewerManager.show(collectionViewer);
                }
            });
        });

        integration.initialized = true;
        return true;
    }

    function setupDashboardIntegration(integrationId) {
        const integration = integrations.get(integrationId);
        if (!integration) return false;

        const recentImagesContainer = document.querySelector(integration.config.selectors.dashboardRecent);
        if (!recentImagesContainer) {
            console.error('Dashboard recent images container not found');
            return false;
        }

        integration.container = recentImagesContainer;

        // Find all images in dashboard gallery
        const images = Array.from(recentImagesContainer.querySelectorAll('img'));
        integration.images = images.map(img => ({
            src: img.src,
            alt: img.alt || '',
            title: img.getAttribute('title') || img.alt || ''
        }));

        // Create viewer instance with full configuration
        integration.viewerId = ViewerManager.create(recentImagesContainer, {
            filter(image) {
                return image.tagName === 'IMG';
            },
            url(image) {
                return image.src;
            },
            button: false,
            navbar: true,
            toolbar: {
                zoomIn: true,
                zoomOut: true,
                reset: true,
                close: true
            },
            transition: false,
            backdrop: 'static',
            viewed(e) {
                const index = e.detail.index;
                handleImageViewed(integrationId, index);
            }
        });

        // Create filmstrip if enabled
        if (integration.config.filmstrip.enabled) {
            integration.filmstripId = FilmstripManager.create(
                integration.viewerId,
                integration.config.filmstrip
            );

            // Populate filmstrip with dashboard images
            FilmstripManager.populate(integration.filmstripId, integration.images);

            // Attach to viewer container
            const viewerContainer = document.querySelector('.viewer-container');
            if (viewerContainer) {
                FilmstripManager.attach(integration.filmstripId, viewerContainer);
            }
        }

        // Create metadata panel if enabled
        if (integration.config.metadata.enabled) {
            integration.metadataPanelId = MetadataManager.createPanel(integration.config.metadata.panel);
            MetadataManager.attachPanel(integration.metadataPanelId, document.body);
        }

        // Add click handlers to images
        images.forEach((img, index) => {
            img.style.cursor = 'pointer';

            // Quick preview on hover (optional)
            let hoverTimer;
            img.addEventListener('mouseenter', () => {
                hoverTimer = setTimeout(() => {
                    // Show a tooltip with basic info
                    showQuickInfo(img);
                }, 500);
            });

            img.addEventListener('mouseleave', () => {
                clearTimeout(hoverTimer);
                hideQuickInfo();
            });

            // Full view on click
            img.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                showImage(integrationId, index);
            });
        });

        integration.initialized = true;
        return true;
    }

    function handleImageViewed(integrationId, index) {
        const integration = integrations.get(integrationId);
        if (!integration) return;

        // Update filmstrip if present
        if (integration.filmstripId) {
            FilmstripManager.setActive(integration.filmstripId, index);
        }

        // Load and display metadata if enabled
        if (integration.metadataPanelId && integration.images[index]) {
            const image = integration.images[index];
            MetadataManager.extractAndDisplay(integration.metadataPanelId, image.src);
        }

        // Emit custom event
        const event = new CustomEvent('viewer:imageViewed', {
            detail: {
                integrationId: integrationId,
                index: index,
                image: integration.images[index]
            }
        });
        document.dispatchEvent(event);
    }

    async function handleImageUpload(integrationId, file) {
        const integration = integrations.get(integrationId);
        if (!integration) return;

        // Create object URL for preview
        const url = URL.createObjectURL(file);

        // Update viewer with new image
        const previewArea = document.querySelector('.image-preview-area');
        if (previewArea) {
            previewArea.innerHTML = `<img src="${url}" alt="${file.name}" />`;
            ViewerManager.update(integration.viewerId);
        }

        // Extract and display metadata
        if (integration.metadataPanelId) {
            const metadata = await MetadataManager.extract(file);
            MetadataManager.display(integration.metadataPanelId, metadata);
        }

        // Clean up object URL after a delay
        setTimeout(() => {
            URL.revokeObjectURL(url);
        }, 60000);
    }

    function showImage(integrationId, index) {
        const integration = integrations.get(integrationId);
        if (!integration || !integration.viewerId) return;

        ViewerManager.show(integration.viewerId, index);

        // Sync with filmstrip
        if (integration.filmstripId) {
            FilmstripManager.setActive(integration.filmstripId, index);
        }

        // Load metadata
        if (integration.metadataPanelId && integration.images[index]) {
            MetadataManager.extractAndDisplay(integration.metadataPanelId, integration.images[index].src);
        }
    }

    function showQuickInfo(img) {
        // Create or update tooltip
        let tooltip = document.getElementById('quick-info-tooltip');
        if (!tooltip) {
            tooltip = document.createElement('div');
            tooltip.id = 'quick-info-tooltip';
            tooltip.className = 'quick-info-tooltip';
            document.body.appendChild(tooltip);
        }

        const rect = img.getBoundingClientRect();
        tooltip.innerHTML = `
            <div class="quick-info-content">
                <strong>${img.alt || 'Image'}</strong>
                <br>Click to view full size
            </div>
        `;

        tooltip.style.left = `${rect.left}px`;
        tooltip.style.top = `${rect.bottom + 5}px`;
        tooltip.style.display = 'block';
    }

    function hideQuickInfo() {
        const tooltip = document.getElementById('quick-info-tooltip');
        if (tooltip) {
            tooltip.style.display = 'none';
        }
    }

    // Public API
    return {
        /**
         * Initialize the integration system
         * @param {Object} config - Global configuration
         */
        init: function(config = {}) {
            if (initialized) {
                console.warn('ViewerIntegration already initialized');
                return this;
            }

            Object.assign(globalConfig, config);

            if (!initializeModules()) {
                console.error('Failed to initialize viewer modules');
                return this;
            }

            // Inject integration styles
            this.injectStyles();

            // Auto-detect current page and initialize
            this.autoInitialize();

            return this;
        },

        /**
         * Auto-detect and initialize based on current page
         */
        autoInitialize: function() {
            const path = window.location.pathname;

            if (path.includes('gallery')) {
                this.initGallery();
            } else if (path.includes('metadata')) {
                this.initMetadata();
            } else if (path.includes('collections')) {
                this.initCollections();
            } else if (path.includes('dashboard')) {
                this.initDashboard();
            }
        },

        /**
         * Initialize gallery page integration
         * @param {Object} config - Page-specific configuration
         */
        initGallery: function(config = {}) {
            const integrationId = createIntegration('gallery', config);

            if (setupGalleryIntegration(integrationId)) {
                activeIntegration = integrationId;
                console.log('Gallery integration initialized');
            }

            return integrationId;
        },

        /**
         * Initialize metadata page integration
         * @param {Object} config - Page-specific configuration
         */
        initMetadata: function(config = {}) {
            const integrationId = createIntegration('metadata', config);

            if (setupMetadataPageIntegration(integrationId)) {
                activeIntegration = integrationId;
                console.log('Metadata page integration initialized');
            }

            return integrationId;
        },

        /**
         * Initialize collections page integration
         * @param {Object} config - Page-specific configuration
         */
        initCollections: function(config = {}) {
            const integrationId = createIntegration('collections', config);

            if (setupCollectionsIntegration(integrationId)) {
                activeIntegration = integrationId;
                console.log('Collections integration initialized');
            }

            return integrationId;
        },

        /**
         * Initialize dashboard integration
         * @param {Object} config - Page-specific configuration
         */
        initDashboard: function(config = {}) {
            const integrationId = createIntegration('dashboard', config);

            if (setupDashboardIntegration(integrationId)) {
                activeIntegration = integrationId;
                console.log('Dashboard integration initialized');
            }

            return integrationId;
        },

        /**
         * Get integration by ID
         * @param {string} integrationId - Integration ID
         */
        getIntegration: function(integrationId) {
            return integrations.get(integrationId);
        },

        /**
         * Get active integration
         */
        getActiveIntegration: function() {
            return activeIntegration ? integrations.get(activeIntegration) : null;
        },

        /**
         * Update integration configuration
         * @param {string} integrationId - Integration ID
         * @param {Object} config - New configuration
         */
        updateConfig: function(integrationId, config) {
            const integration = integrations.get(integrationId);
            if (!integration) return false;

            Object.assign(integration.config, config);

            // Apply updates to managers
            if (integration.viewerId) {
                ViewerManager.updateConfig(config.viewer || {});
            }

            return true;
        },

        /**
         * Refresh integration with new images
         * @param {string} integrationId - Integration ID
         * @param {Array} images - New images array
         */
        refreshImages: function(integrationId, images) {
            const integration = integrations.get(integrationId);
            if (!integration) return false;

            integration.images = images;

            // Update viewer
            if (integration.viewerId) {
                ViewerManager.update(integration.viewerId);
            }

            // Update filmstrip
            if (integration.filmstripId) {
                FilmstripManager.populate(integration.filmstripId, images);
            }

            return true;
        },

        /**
         * Show image in viewer
         * @param {number} index - Image index
         * @param {string} integrationId - Optional integration ID
         */
        showImage: function(index, integrationId = null) {
            const id = integrationId || activeIntegration;
            if (id) {
                showImage(id, index);
            }
        },

        /**
         * Extract metadata for current image
         * @param {string} integrationId - Optional integration ID
         */
        extractCurrentMetadata: async function(integrationId = null) {
            const id = integrationId || activeIntegration;
            const integration = integrations.get(id);

            if (!integration || !integration.viewerId) return null;

            const viewer = ViewerManager.getViewer(integration.viewerId);
            if (!viewer) return null;

            const currentIndex = viewer.index;
            const currentImage = integration.images[currentIndex];

            if (!currentImage) return null;

            return await MetadataManager.extract(currentImage.src);
        },

        /**
         * Destroy integration
         * @param {string} integrationId - Integration ID
         */
        destroy: function(integrationId) {
            const integration = integrations.get(integrationId);
            if (!integration) return false;

            // Clean up viewer
            if (integration.viewerId) {
                ViewerManager.destroy(integration.viewerId);
            }

            // Clean up filmstrip
            if (integration.filmstripId) {
                FilmstripManager.destroy(integration.filmstripId);
            }

            // Clean up metadata panel
            if (integration.metadataPanelId) {
                MetadataManager.destroyPanel(integration.metadataPanelId);
            }

            // Remove from integrations
            integrations.delete(integrationId);

            if (activeIntegration === integrationId) {
                activeIntegration = null;
            }

            return true;
        },

        /**
         * Destroy all integrations
         */
        destroyAll: function() {
            integrations.forEach((_, id) => {
                this.destroy(id);
            });
        },

        /**
         * Inject required styles
         */
        injectStyles: function() {
            if (document.getElementById('viewer-integration-styles')) return;

            const styles = document.createElement('style');
            styles.id = 'viewer-integration-styles';
            styles.textContent = `
                /* Integration styles */
                .gallery-item {
                    cursor: pointer;
                    transition: transform 0.2s;
                }

                .gallery-item:hover {
                    transform: scale(1.02);
                    box-shadow: 0 4px 12px rgba(79, 195, 247, 0.3);
                }

                .drag-drop-area.drag-over {
                    background: rgba(79, 195, 247, 0.1);
                    border-color: #4fc3f7;
                }

                .quick-info-tooltip {
                    position: absolute;
                    background: rgba(0, 0, 0, 0.9);
                    color: #fff;
                    padding: 8px 12px;
                    border-radius: 4px;
                    font-size: 12px;
                    z-index: 9999;
                    pointer-events: none;
                    display: none;
                }

                .quick-info-content {
                    text-align: center;
                }

                /* Viewer container positioning */
                .viewer-container .filmstrip-container {
                    position: absolute;
                }

                .viewer-container .metadata-panel {
                    position: absolute;
                }

                /* Loading states */
                .viewer-loading {
                    position: relative;
                }

                .viewer-loading::before {
                    content: '';
                    position: absolute;
                    top: 50%;
                    left: 50%;
                    transform: translate(-50%, -50%);
                    width: 40px;
                    height: 40px;
                    border: 4px solid rgba(255, 255, 255, 0.3);
                    border-top-color: #4fc3f7;
                    border-radius: 50%;
                    animation: spin 1s linear infinite;
                }

                @keyframes spin {
                    to { transform: translate(-50%, -50%) rotate(360deg); }
                }

                /* Responsive adjustments */
                @media (max-width: 768px) {
                    .metadata-panel-right {
                        width: 100% !important;
                        height: 50vh;
                    }

                    .filmstrip-container {
                        height: 60px !important;
                    }
                }
            `;
            document.head.appendChild(styles);
        }
    };
})();

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = ViewerIntegration;
}

// Attach to window for global access
if (typeof window !== 'undefined') {
    window.ViewerIntegration = ViewerIntegration;
}
