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

    function getIntegrationByViewer(viewerInstance) {
        if (!viewerInstance) {
            return null;
        }

        for (const integration of integrations.values()) {
            if (!integration.viewerId) {
                continue;
            }

            const viewer = window.ViewerManager?.getViewer?.(integration.viewerId);
            if (viewer === viewerInstance) {
                return integration;
            }
        }

        return null;
    }

    function hideMetadataPanelForViewer(viewerInstance) {
        if (!viewerInstance || !window.MetadataManager || typeof window.MetadataManager.hidePanel !== 'function') {
            return;
        }

        const integration = getIntegrationByViewer(viewerInstance) || (activeIntegration ? integrations.get(activeIntegration) : null);
        if (integration?.metadataPanelId) {
            window.MetadataManager.hidePanel(integration.metadataPanelId);
        }
    }

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

    function parseMaybeJson(value) {
        if (!value) {
            return null;
        }
        if (typeof value === 'object') {
            return value;
        }
        if (typeof value === 'string') {
            try {
                return JSON.parse(value);
            } catch (error) {
                console.debug('ViewerIntegration: failed to parse metadata JSON', error);
            }
        }
        return null;
    }

    function extractImageIdFromUrl(url) {
        if (!url || typeof url !== 'string') {
            return null;
        }

        const match = url.match(/(?:generated-images|gallery\/images)\/(\d+)/);
        if (match && match[1]) {
            const parsed = Number.parseInt(match[1], 10);
            return Number.isFinite(parsed) ? parsed : null;
        }

        return null;
    }

    function resolveImageId(image) {
        if (!image || typeof image !== 'object') {
            return null;
        }

        const direct = image.id
            ?? image.image_id
            ?? image.generated_image_id
            ?? image.original_image_id
            ?? image.generatedId
            ?? image.imageId;

        if (direct !== undefined && direct !== null && direct !== '') {
            const numeric = Number(direct);
            return Number.isFinite(numeric) ? numeric : direct;
        }

        const candidates = [
            image.url,
            image.fullSrc,
            image.src,
            image.image_url,
            image.thumbnail_url,
            image.path
        ];

        for (const candidate of candidates) {
            const parsed = extractImageIdFromUrl(candidate);
            if (parsed !== null) {
                return parsed;
            }
        }

        return null;
    }

    function normalizeImageEntry(image, index = 0) {
        if (typeof image === 'string') {
            return {
                src: image,
                fullSrc: image,
                alt: `Image ${index + 1}`,
                title: `Image ${index + 1}`,
                metadata: null
            };
        }

        if (!image || typeof image !== 'object') {
            return {
                src: '',
                fullSrc: '',
                alt: '',
                title: '',
                metadata: null
            };
        }

        const thumb = image.src || image.thumb || image.thumbnail || image.thumbnail_url || image.preview_url || image.preview || image.url || '';
        const full = image.fullSrc || image.url || image.image_url || image.path || image.full_url || thumb;
        const descriptiveText = image.title || image.alt || image.display_title || image.filename || image.file_name || image.name || `Image ${index + 1}`;
        const parsedMetadata = parseMaybeJson(image.metadata)
            || parseMaybeJson(image.meta)
            || parseMaybeJson(image.prompt_metadata);

        // Don't cache empty/invalid metadata - let the viewer extract it from the image file
        const hasValidMetadata = parsedMetadata && Object.keys(parsedMetadata).length > 0 &&
                                 parsedMetadata !== '{}' &&
                                 (parsedMetadata.positive_prompt || parsedMetadata.prompt || parsedMetadata.summary);

        console.debug('[ViewerIntegration] Normalized image metadata', {
            imageId: resolveImageId(image),
            hadParsedMetadata: !!parsedMetadata,
            parsedMetadataKeys: parsedMetadata ? Object.keys(parsedMetadata) : [],
            hasValidMetadata,
            willCache: hasValidMetadata
        });

        return {
            src: thumb,
            fullSrc: full,
            alt: image.alt || descriptiveText,
            title: descriptiveText,
            id: resolveImageId(image),
            metadata: hasValidMetadata ? parsedMetadata : null,
            original: image
        };
    }

    function attachFilmstripToViewer(integration) {
        if (!integration || !integration.filmstripId) {
            return;
        }

        const viewerContainer = document.querySelector('.viewer-container');
        if (viewerContainer) {
            FilmstripManager.attach(integration.filmstripId, viewerContainer);
        }
    }

    function disposeIntegration(integrationId, options = {}) {
        const integration = integrations.get(integrationId);
        if (!integration) return;

        if (integration.viewerId) {
            ViewerManager.destroy(integration.viewerId);
        }

        if (integration.filmstripId) {
            FilmstripManager.destroy(integration.filmstripId);
        }

        if (integration.metadataPanelId) {
            MetadataManager.hidePanel(integration.metadataPanelId);
            MetadataManager.destroyPanel(integration.metadataPanelId);
        }

        if (options.removeContainer && integration.container && integration.container.parentNode) {
            integration.container.parentNode.removeChild(integration.container);
        }

        integrations.delete(integrationId);

        if (activeIntegration === integrationId) {
            activeIntegration = null;
        }
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
        integration.images = images.map(img => {
            const thumbSrc = img.currentSrc || img.src;
            const fullSrc = img.dataset.fullSrc || img.getAttribute('data-full-src') || thumbSrc;

            // Get metadata from data attribute (already HTML-encoded)
            let metadata = null;
            const metadataStr = img.dataset.metadata || img.getAttribute('data-metadata');

            if (metadataStr && metadataStr !== '' && metadataStr !== '{}') {
                try {
                    // Unescape HTML entities
                    const parser = new DOMParser();
                    const txt = parser.parseFromString(metadataStr, 'text/html');
                    const unescaped = txt.documentElement.textContent;

                    // Only parse if we have actual content
                    if (unescaped && unescaped !== '' && unescaped !== '{}') {
                        metadata = JSON.parse(unescaped);
                    }
                } catch (e) {
                    // Only warn for non-empty metadata strings
                    if (metadataStr !== '') {
                        console.debug('Metadata parse issue:', metadataStr.substring(0, 50));
                    }
                }
            }

            return {
                src: thumbSrc,
                fullSrc,
                alt: img.alt || '',
                title: img.getAttribute('title') || img.alt || '',
                metadata: metadata
            };
        });

        // Create viewer instance
        integration.viewerId = ViewerManager.create(container, {
            filter(image) {
                return image.tagName === 'IMG';
            },
            url(image) {
                return image.dataset?.fullSrc || image.getAttribute?.('data-full-src') || image.src;
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
        integration.images = images.map(img => {
            const thumbSrc = img.currentSrc || img.src;
            const fullSrc = img.dataset.fullSrc || img.getAttribute('data-full-src') || thumbSrc;
            return {
                src: thumbSrc,
                fullSrc,
                alt: img.alt || '',
                title: img.getAttribute('title') || img.alt || ''
            };
        });

        // Create viewer instance with full configuration
        integration.viewerId = ViewerManager.create(recentImagesContainer, {
            filter(image) {
                return image.tagName === 'IMG';
            },
            url(image) {
                return image.dataset?.fullSrc || image.getAttribute?.('data-full-src') || image.src;
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

    function loadMetadataForImage(integration, index) {
        if (!integration?.metadataPanelId) {
            console.warn('[ViewerIntegration] No metadata panel ID for integration', integration?.id);
            return;
        }

        const image = integration.images[index];
        if (!image) {
            console.warn('[ViewerIntegration] No image at index', index);
            return;
        }

        // Display cached metadata immediately when available.
        if (image.metadata && Object.keys(image.metadata).length > 0) {
            MetadataManager.display(integration.metadataPanelId, image.metadata);
            return;
        }

        // Avoid duplicate fetches while a request is in flight.
        if (image.metadataLoading) {
            console.debug('[ViewerIntegration] Metadata already loading for image', index);
            return;
        }

        image.metadataLoading = true;
        const source = image.fullSrc || image.src;
        const imageId = image.id ?? resolveImageId(image.original) ?? resolveImageId(image) ?? extractImageIdFromUrl(source);

        console.debug('[ViewerIntegration] Loading metadata', {
            integrationId: integration.id,
            imageIndex: index,
            imageId,
            source,
            image: image
        });

        (async () => {
            try {
                let metadata = null;

                if (imageId && typeof MetadataManager.getByImageId === 'function') {
                    console.debug('[ViewerIntegration] Attempting to get metadata by image ID:', imageId);
                    metadata = await MetadataManager.getByImageId(imageId);
                    if (metadata) {
                        console.debug('[ViewerIntegration] Successfully retrieved metadata by ID', {
                            imageId,
                            hasSummary: Boolean(metadata?.summary)
                        });
                    }
                }

                if (!metadata && source) {
                    console.debug('[ViewerIntegration] Falling back to extract from source:', source);
                    metadata = await MetadataManager.extract(source);
                    if (metadata) {
                        console.debug('[ViewerIntegration] Successfully extracted metadata from source', {
                            source,
                            hasSummary: Boolean(metadata?.summary)
                        });
                    }
                }

                if (metadata) {
                    console.debug('[ViewerIntegration] Displaying metadata in panel', {
                        panelId: integration.metadataPanelId,
                        metadata
                    });
                    image.metadata = metadata;
                    MetadataManager.display(integration.metadataPanelId, metadata);
                } else {
                    console.warn('[ViewerIntegration] No metadata found for image', {
                        imageId,
                        source,
                        index
                    });
                }
            } catch (error) {
                console.error('[ViewerIntegration] Metadata retrieval failed:', error);
            } finally {
                delete image.metadataLoading;
            }
        })();
    }

    function handleImageViewed(integrationId, index) {
        const integration = integrations.get(integrationId);
        if (!integration) return;

        // Update filmstrip if present (pass true to indicate call is from viewer)
        if (integration.filmstripId) {
            FilmstripManager.setActive(integration.filmstripId, index, true);
        }

        // Load and display metadata if enabled
        loadMetadataForImage(integration, index);

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

        // Don't extract metadata from files - API is the single source of truth
        // Metadata should only come from the API, not from client-side extraction

        // Clean up object URL after a delay
        setTimeout(() => {
            URL.revokeObjectURL(url);
        }, 60000);
    }

    function showImage(integrationId, index) {
        const integration = integrations.get(integrationId);
        if (!integration || !integration.viewerId) {
            console.warn('[ViewerIntegration] Cannot show image - no integration or viewer');
            return;
        }

        ViewerManager.show(integration.viewerId, index);

        // Sync with filmstrip (pass true to indicate call is from viewer to prevent loop)
        if (integration.filmstripId) {
            FilmstripManager.setActive(integration.filmstripId, index, true);
        }

        // Ensure metadata panel reflects the currently viewed image
        loadMetadataForImage(integration, index);
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
    if (typeof document !== 'undefined') {
        document.addEventListener('viewer:hidden', (event) => {
            hideMetadataPanelForViewer(event?.detail?.viewer);
        });
    }

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

            integration.images = images.map((image, index) => normalizeImageEntry(image, index));

            // Update viewer
            if (integration.viewerId) {
                ViewerManager.update(integration.viewerId);
            }

            // Update filmstrip
            if (integration.filmstripId) {
                FilmstripManager.populate(integration.filmstripId, integration.images);
            }

            return true;
        },

        /**
         * Open a standalone viewer instance with a provided image set
         * @param {Array} images - Array of image descriptors or URLs
         * @param {Object} options - Viewer configuration overrides
         * @returns {string|null} Integration ID when successful
         */
        openImageSet: function(images, options = {}) {

            if (!Array.isArray(images) || images.length === 0) {
                console.warn('ViewerIntegration.openImageSet: No images supplied');
                return null;
            }

            if (!initialized && !initializeModules()) {
                console.error('ViewerIntegration: Required modules unavailable');
                return null;
            }

            const normalizedImages = images
                .map((image, index) => normalizeImageEntry(image, index))
                .filter((image) => Boolean(image.src || image.fullSrc));

            if (normalizedImages.length === 0) {
                console.warn('ViewerIntegration.openImageSet: Normalized images missing sources');
                return null;
            }

            const viewerOverrides = Object.assign({}, options.viewer || {});
            const filmstripOverrides = options.filmstrip === false
                ? { enabled: false }
                : Object.assign({}, globalConfig.filmstrip, options.filmstrip || {});
            const metadataOverrides = options.metadata === false
                ? { enabled: false }
                : Object.assign({}, globalConfig.metadata, options.metadata || {});

            const integrationConfig = {
                viewer: Object.assign({}, globalConfig.viewer, viewerOverrides),
                filmstrip: filmstripOverrides,
                metadata: metadataOverrides,
                destroyOnHide: options.destroyOnHide !== false,
                startIndex: Number.isInteger(options.startIndex) ? options.startIndex : 0,
                context: options.context || null
            };

            const integrationId = createIntegration('adhoc', integrationConfig);

            const integration = integrations.get(integrationId);
            if (!integration) {
                return null;
            }

            const container = document.createElement('div');
            container.className = options.containerClass || 'viewer-adhoc-container';
            container.style.display = 'none';

            normalizedImages.forEach((image) => {
                const imgElement = document.createElement('img');
                imgElement.src = image.src || image.fullSrc;
                if (image.fullSrc) {
                    imgElement.setAttribute('data-full-src', image.fullSrc);
                }
                if (image.alt) {
                    imgElement.alt = image.alt;
                }
                if (image.title) {
                    imgElement.title = image.title;
                }
                container.appendChild(imgElement);
            });

            document.body.appendChild(container);

            integration.container = container;
            integration.images = normalizedImages;
            integration.context = options.context || null;
            integration.initialized = true;

            // Ensure viewer config overrides are applied
            if (integration.config.viewer) {
                ViewerManager.updateConfig(integration.config.viewer);
                if (integration.config.viewer.theme) {
                    ViewerManager.setTheme(integration.config.viewer.theme);
                }
            }

            const viewerOptions = Object.assign({}, options.viewerOptions || {});

            viewerOptions.filter = viewerOptions.filter || function(node) {
                return node.tagName === 'IMG';
            };

            viewerOptions.url = viewerOptions.url || function(node) {
                return node.dataset?.fullSrc || node.getAttribute?.('data-full-src') || node.src;
            };

            const onShown = typeof options.onShown === 'function' ? options.onShown : null;
            const onHidden = typeof options.onHidden === 'function' ? options.onHidden : null;
            const onViewed = typeof options.onViewed === 'function' ? options.onViewed : null;

            viewerOptions.viewed = function(event) {
                handleImageViewed(integrationId, event.detail.index);
                if (onViewed) {
                    onViewed({ integrationId, event });
                }
            };

            viewerOptions.shown = function(event) {
                attachFilmstripToViewer(integration);

                if (integration.metadataPanelId && integration.config.metadata?.autoShow !== false) {
                    MetadataManager.showPanel(integration.metadataPanelId);
                }

                if (onShown) {
                    onShown({ integrationId, event });
                }
            };

            viewerOptions.hidden = function(event) {
                if (onHidden) {
                    onHidden({ integrationId, event });
                }

                if (integration.config.destroyOnHide !== false) {
                    disposeIntegration(integrationId, { removeContainer: true });
                }
            };

            integration.viewerId = ViewerManager.create(container, viewerOptions);

            if (integration.config.filmstrip?.enabled) {
                integration.filmstripId = FilmstripManager.create(
                    integration.viewerId,
                    integration.config.filmstrip
                );
                FilmstripManager.populate(integration.filmstripId, normalizedImages);
            }

            if (integration.config.metadata?.enabled) {
                const panelConfig = Object.assign({
                    position: integration.config.metadata.position || 'right',
                    collapsible: integration.config.metadata.collapsible !== false,
                    autoShow: integration.config.metadata.autoShow !== false
                }, integration.config.metadata.panel || {});

                integration.metadataPanelId = MetadataManager.createPanel(panelConfig);

                MetadataManager.attachPanel(
                    integration.metadataPanelId,
                    integration.config.metadata.container || document.body
                );
            }

            activeIntegration = integrationId;

            const startIndex = Math.min(
                Math.max(0, integration.config.startIndex || 0),
                normalizedImages.length - 1
            );

            showImage(integrationId, startIndex);

            return integrationId;
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

            // Return pre-loaded metadata from the image object
            // API is the single source of truth for metadata
            return currentImage.metadata || null;
        },

        /**
         * Destroy integration
         * @param {string} integrationId - Integration ID
         */
        destroy: function(integrationId) {
            if (!integrations.has(integrationId)) return false;

            disposeIntegration(integrationId);
            return true;
        },

        /**
         * Destroy all integrations
         */
        destroyAll: function() {
            Array.from(integrations.keys()).forEach((id) => {
                disposeIntegration(id);
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
                    animation: viewer-spin 1s linear infinite;
                }

                @keyframes viewer-spin {
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
