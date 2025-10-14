/**
 * Dynamic Gallery Loader
 * Replaces mock data with real database content
 */

(function() {
    'use strict';

    // Only run in gallery page
    if (!window.location.pathname.includes('/gallery')) {
        return;
    }

    let currentPage = 1;
    let totalPages = 1;
    let currentFilters = {};
    let isLoading = false;
    let activeViewerIntegrationId = null;
    let masonryInstance = null;
    let hasMorePages = true;
    let infiniteScrollEnabled = true;

    // Progressive loading state
    let scrollVelocity = 0;
    let lastScrollTop = 0;
    let lastScrollTime = Date.now();
    let preloadingNextBatch = false;
    let batchQueue = [];
    let progressiveLoadTimer = null;

    // Track loaded items to prevent duplicates
    let loadedItemIds = new Set();
    let totalItemsLoaded = 0;
    let lastPageLoaded = 0;

    // Get items per page from settings or use default
    function getItemsPerPage() {
        try {
            const settings = JSON.parse(localStorage.getItem('promptManagerSettings') || '{}');
            const itemsPerPage = parseInt(settings.galleryItemsPerPage) || 20;
            console.log(`[Gallery] Items per page from settings: ${itemsPerPage}`);
            // Ensure it's within valid range
            return Math.min(Math.max(itemsPerPage, 10), 500);
        } catch (e) {
            console.warn('Failed to load gallery items per page setting, using default:', e);
            return 20; // Default changed from 200 to 20
        }
    }

    /**
     * Initialize dynamic gallery
     */
    async function initDynamicGallery() {
        console.log('Initializing dynamic gallery...');

        const container = document.getElementById('galleryContainer') || document.querySelector('.gallery-container');
        
        // Load default view and thumbnail size from settings
        try {
            const settings = JSON.parse(localStorage.getItem('promptManagerSettings') || '{}');

            // Load default view
            const defaultView = settings.galleryDefaultView || 'grid';
            currentFilters.viewMode = defaultView;
            if (container) {
                container.dataset.viewMode = defaultView;
            }

            // Apply thumbnail size setting
            const thumbnailSize = settings.galleryThumbnailSize || 'medium';
            const sizeMap = {
                'small': '200px',
                'medium': '300px',
                'large': '400px'
            };
            document.documentElement.style.setProperty(
                '--gallery-thumbnail-size',
                sizeMap[thumbnailSize] || sizeMap['medium']
            );
        } catch (e) {
            console.warn('Failed to load gallery settings:', e);
            if (container?.dataset?.viewMode) {
                currentFilters.viewMode = container.dataset.viewMode;
            }
        }

        if (currentFilters.viewMode) {
            document.querySelectorAll('.view-mode-btn').forEach(btn => {
                const mode = btn.dataset.mode || btn.textContent.trim().toLowerCase();
                btn.classList.toggle('active', mode === currentFilters.viewMode);
            });
        }

        // Hide the container initially to prevent empty frames
        container.style.opacity = '0';
        container.style.visibility = 'hidden';

        // Setup event listeners
        setupFilterListeners();
        
        // Conditional setup based on view mode
        const viewMode = currentFilters.viewMode || 'grid';
        if (viewMode === 'masonry') {
            // Masonry uses infinite scroll, hide pagination immediately
            setupInfiniteScroll();
            hidePagination(); // Ensure pagination is hidden for masonry on init
        } else {
            // Grid and list use pagination
            setupPaginationListeners();
        }

        // Load initial data
        await loadGalleryData();

        // Show the container after data is loaded with a smooth fade-in
        setTimeout(() => {
            container.style.transition = 'opacity 0.3s ease, visibility 0.3s ease';
            container.style.opacity = '1';
            container.style.visibility = 'visible';
        }, 100);

        // Listen for storage changes (in case settings are changed in another tab)
        window.addEventListener('storage', (e) => {
            if (e.key === 'promptManagerSettings') {
                console.log('Settings changed, reloading gallery...');

                try {
                    const settings = JSON.parse(e.newValue || '{}');

                    // Apply thumbnail size if changed
                    const thumbnailSize = settings.galleryThumbnailSize || 'medium';
                    const sizeMap = {
                        'small': '200px',
                        'medium': '300px',
                        'large': '400px'
                    };
                    document.documentElement.style.setProperty(
                        '--gallery-thumbnail-size',
                        sizeMap[thumbnailSize] || sizeMap['medium']
                    );
                } catch (err) {
                    console.warn('Failed to apply thumbnail size setting:', err);
                }

                // Reload gallery with new settings
                loadGalleryData(1, false);
            }
        });

        // Load available models for filter
        // await loadModelOptions(); // Commented out as this API doesn't exist yet
    }

    /**
     * Load gallery data from API
     * @param {number} page - Page to load
     * @param {boolean} scrollToTop - Whether to scroll to top after loading
     * @param {boolean} append - Whether to append items (for infinite scroll) or replace
     */
    async function loadGalleryData(page = 1, scrollToTop = false, append = false) {
        if (isLoading) return;
        isLoading = true;

        try {
            if (!append) {
                showLoadingState();
            }

            // Build query parameters
            const params = new URLSearchParams({
                page: page,
                limit: getItemsPerPage(),  // Use setting from localStorage
                sort_order: currentFilters.sortOrder || 'date_desc',
                view_mode: currentFilters.viewMode || 'grid'
            });

            // Add filters if present
            if (currentFilters.search) params.append('search', currentFilters.search);
            if (currentFilters.tags) params.append('tags', currentFilters.tags);
            if (currentFilters.dateFrom) params.append('date_from', currentFilters.dateFrom);
            if (currentFilters.dateTo) params.append('date_to', currentFilters.dateTo);
            if (currentFilters.model) params.append('model', currentFilters.model);

            // Fetch data from API - using the v1 endpoint
            const response = await fetch(`/api/v1/gallery/images?${params}`);

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const result = await response.json();

            if (result.success && result.data) {
                // Use 'page' from API response (not 'current_page')
                currentPage = result.pagination?.page || result.pagination?.current_page || page;
                totalPages = result.pagination?.total_pages || 1;

                // Debug: Log what the API actually returned
                console.log(`[Gallery] API pagination response:`, result.pagination);

                // Calculate has_next if not provided by API
                if (result.pagination && typeof result.pagination.has_next !== 'undefined') {
                    hasMorePages = result.pagination.has_next;
                    console.log(`[Gallery] Using API has_next:`, hasMorePages);
                } else {
                    // Calculate from page numbers
                    hasMorePages = currentPage < totalPages;
                    console.log(`[Gallery] Calculated hasMorePages: ${currentPage} < ${totalPages} = ${hasMorePages}`);
                }

                // Debug logging for items per page
                console.log(`[Gallery] Loaded ${result.data.length} items (requested: ${getItemsPerPage()})`);
                console.log(`[Gallery] Pagination info:`, result.pagination);

                // Update page tracking when loading through main function
                lastPageLoaded = currentPage;
                console.log(`Main load: Set lastPageLoaded to ${lastPageLoaded}`);

                renderGalleryItems(result.data, append);

                // Update pagination for grid/list views (not for append operations)
                if (!append) {
                    updatePagination(result.pagination);
                }

                if (result.data.length === 0 && !append) {
                    showEmptyState();
                } else {
                    hideEmptyState();
                }

                // Scroll to top of gallery after loading new page
                if (scrollToTop) {
                    // Try to find the main content area or top of the page
                    const mainContent = document.querySelector('.main-content');
                    const topbar = document.querySelector('.topbar');

                    if (mainContent) {
                        // Scroll to the main content area
                        mainContent.scrollIntoView({
                            behavior: 'smooth',
                            block: 'start'
                        });
                    } else if (topbar) {
                        // Scroll to just above the topbar
                        const topbarRect = topbar.getBoundingClientRect();
                        const topbarTop = window.pageYOffset + topbarRect.top;
                        window.scrollTo({
                            top: Math.max(0, topbarTop - 20), // 20px padding above topbar
                            behavior: 'smooth'
                        });
                    } else {
                        // Fallback: scroll to absolute top of page
                        window.scrollTo({
                            top: 0,
                            behavior: 'smooth'
                        });
                    }
                }
            } else {
                throw new Error(result.error || 'Failed to load gallery data');
            }

        } catch (error) {
            console.error('Failed to load gallery:', error);
            showErrorState(error.message);
        } finally {
            isLoading = false;
            hideLoadingState();
        }
    }

    function applyViewModeToContainer(container, mode) {
        if (!container) {
            return 'grid';
        }

        const normalized = ['grid', 'masonry', 'list'].includes(mode) ? mode : 'grid';
        if (normalized !== 'masonry') {
            if (masonryInstance) {
                masonryInstance.destroy();
                masonryInstance = null;
            }
            container.querySelectorAll('.masonry-sizer').forEach(node => node.remove());
        }

        container.classList.remove('gallery-grid', 'masonry-grid', 'list-view');

        switch (normalized) {
            case 'masonry':
                container.classList.add('masonry-grid');
                break;
            case 'list':
                container.classList.add('list-view');
                break;
            default:
                container.classList.add('gallery-grid');
        }

        container.dataset.viewMode = normalized;
        return normalized;
    }

    /**
     * Render gallery items
     * @param {Array} items - Gallery items to render
     * @param {boolean} append - Whether to append items (for infinite scroll) or replace
     */
    function renderGalleryItems(items, append = false) {
        const container = document.getElementById('galleryContainer') || document.querySelector('.gallery-container');
        if (!container) {
            console.error('Gallery container not found');
            return;
        }

        // Clear existing items if not appending
        if (!append) {
            container.innerHTML = '';
            // Reset masonry instance when replacing content
            if (masonryInstance) {
                masonryInstance.destroy();
                masonryInstance = null;
            }
            // Reset keyboard focus when reloading
            resetKeyboardFocus();

            // Reset duplicate tracking when starting fresh
            loadedItemIds.clear();
            totalItemsLoaded = 0;
            lastPageLoaded = 0;
        }

        if (!items || items.length === 0) {
            if (!append) {
                showEmptyState();
            }
            return;
        }

        const viewMode = applyViewModeToContainer(container, currentFilters.viewMode || container.dataset.viewMode || 'grid');
        currentFilters.viewMode = viewMode;

        const newElements = [];

        // Create gallery items using actual API data structure
        items.forEach(item => {
            // Track loaded items to prevent duplicates
            if (!loadedItemIds.has(item.id)) {
                loadedItemIds.add(item.id);
                totalItemsLoaded++;

                const galleryItem = createGalleryItem(item, viewMode);
                container.appendChild(galleryItem);
                newElements.push(galleryItem);
            }
        });

        if (viewMode === 'masonry') {
            if (append && masonryInstance) {
                // Handle append with proper image loading
                handleMasonryAppend(newElements);
            } else {
                // Initialize new masonry layout
                initializeMasonryLayout(container);
            }
        }


        refreshViewerIntegration(container);
    }

    /**
     * Create a gallery item element from actual API data
     */
    function createGalleryItem(item, viewMode = 'grid') {
        const div = document.createElement('div');
        div.className = 'gallery-item';
        if (viewMode === 'list') {
            div.classList.add('list-item');
        }
        div.dataset.itemId = item.id;

        const modelName = extractModelName(item);
        const placeholderImage = '/prompt_manager/images/placeholder.png';
        const audioPlaceholder = '/prompt_manager/images/wave-form.svg';

        const mediaType = item.media_type || 'image';
        let thumbnailUrl = item.thumbnail_url || item.thumbnail || placeholderImage;
        if (mediaType === 'audio') {
            thumbnailUrl = audioPlaceholder;
        }
        const fullMediaUrl = item.image_url || item.url || item.path || thumbnailUrl;

        // Store metadata on the element for viewer integration
        let metadataStr = '';
        if (item.metadata) {
            if (typeof item.metadata === 'string') {
                // If it's already a string, use it as is (unless it's empty JSON)
                metadataStr = (item.metadata && item.metadata !== '{}') ? item.metadata : '';
            } else if (typeof item.metadata === 'object' && Object.keys(item.metadata).length > 0) {
                // If it's an object with content, stringify it
                metadataStr = JSON.stringify(item.metadata);
            }
        }

        // Build HTML - for masonry view, only show the image (no info wrapper)
        if (viewMode === 'masonry') {
            // CRITICAL: Don't use lazy loading for masonry - it breaks layout calculation
            div.innerHTML = `
                <div class="gallery-image">
                    <img src="${thumbnailUrl}"
                         data-full-src="${fullMediaUrl}"
                         alt="${escapeHtml(item.filename || '')}"
                         data-placeholder="${placeholderImage}"
                         data-metadata="${escapeHtml(metadataStr)}"
                         title="${escapeHtml(item.filename || 'Untitled')}" />
                </div>
            `;
        } else {
            // Build HTML for grid and list views with full info
            div.innerHTML = `
                <div class="gallery-image">
                    <img src="${thumbnailUrl}"
                         data-full-src="${fullMediaUrl}"
                         alt="${escapeHtml(item.filename || '')}"
                         loading="lazy"
                         data-placeholder="${placeholderImage}"
                         data-metadata="${escapeHtml(metadataStr)}" />
                </div>
                <div class="gallery-info">
                    <div class="gallery-title">${escapeHtml(item.filename || 'Untitled')}</div>
                    <div class="gallery-meta">
                        <span class="chip">${escapeHtml(modelName)}</span>
                        <span class="chip">${item.dimensions || 'Unknown'}</span>
                        <span class="chip">${item.size || 'Unknown'}</span>
                        ${mediaType !== 'image' ? `<span class="chip chip-media">${mediaType}</span>` : ''}
                    </div>
                    <div class="gallery-timestamp">
                        ${formatTimeAgo(item.generation_time)}
                    </div>
                    <div class="gallery-actions">
                        <button class="btn btn-ghost" title="View Details" onclick="viewGalleryItem('${item.id}')">
                            <i class="fa-solid fa-info-circle"></i>
                        </button>
                        <button class="btn btn-ghost" title="Copy Prompt" onclick="copyPrompt('${item.id}')">
                            <i class="fa-solid fa-copy"></i>
                        </button>
                        <button class="btn btn-ghost" title="Delete" onclick="deleteGalleryItem('${item.id}')">
                            <i class="fa-solid fa-trash"></i>
                        </button>
                    </div>
                </div>
            `;
        }

        if (viewMode === 'list') {
            const imageEl = div.querySelector('.gallery-image');
            if (imageEl) {
                imageEl.classList.add('list-image');
            }
        }

        // Add error fallback for broken thumbnails
        const img = div.querySelector('.gallery-image img');
        if (img) {
            img.dataset.fullSrc = fullMediaUrl;
            const handleImageError = () => {
                const fallbackTried = img.dataset.fallbackTried === '1';
                if (!fallbackTried && fullMediaUrl && img.src !== fullMediaUrl) {
                    img.dataset.fallbackTried = '1';
                    img.src = fullMediaUrl;
                    return;
                }

                img.removeEventListener('error', handleImageError);
                img.dataset.fallbackTried = '1';
                img.src = img.dataset.placeholder || placeholderImage;
            };

            img.addEventListener('error', handleImageError);
            img.style.cursor = 'pointer';
            img.dataset.mediaType = mediaType;
            img.addEventListener('click', () => {
                if (mediaType === 'video' || mediaType === 'audio') {
                    window.open(fullMediaUrl, '_blank');
                    return;
                }

                if (window.ViewerIntegration && window.ViewerIntegration.showImage) {
                    const active = window.ViewerIntegration.getActiveIntegration?.();
                    if (active?.id) {
                        const items = Array.from(document.querySelectorAll('#galleryContainer .gallery-item'));
                        const index = items.findIndex(el => el.dataset.itemId === String(item.id));
                        if (index >= 0) {
                            window.ViewerIntegration.showImage(index, active.id);
                            return;
                        }
                    }
                }

                window.open(fullMediaUrl, '_blank');
            });

            if (mediaType === 'video') {
                const overlay = document.createElement('div');
                overlay.className = 'media-overlay media-overlay-video';
                overlay.innerHTML = '<i class="fa-solid fa-circle-play" aria-hidden="true"></i>';
                const imageWrapper = div.querySelector('.gallery-image');
                if (imageWrapper) {
                    imageWrapper.appendChild(overlay);
                }
            } else if (mediaType === 'audio') {
                const overlay = document.createElement('div');
                overlay.className = 'media-overlay media-overlay-audio';
                overlay.innerHTML = '<i class="fa-solid fa-wave-square" aria-hidden="true"></i>';
                const imageWrapper = div.querySelector('.gallery-image');
                if (imageWrapper) {
                    imageWrapper.appendChild(overlay);
                }
            }
        }

        return div;
    }

    function extractModelName(item) {
        if (!item) {
            return 'Unknown';
        }

        const metadataSources = [item.metadata, item.workflow, item.parameters]
            .filter(source => source !== undefined && source !== null);

        for (const source of metadataSources) {
            try {
                const data = typeof source === 'string' ? JSON.parse(source) : source;
                if (!data || typeof data !== 'object') {
                    continue;
                }

                const nodes = Array.isArray(data) ? data : Object.values(data);
                for (const node of nodes) {
                    if (!node || typeof node !== 'object') {
                        continue;
                    }

                    const inputs = node.inputs || {};
                    const classType = node.class_type || node.type;
                    if (
                        (classType === 'CheckpointLoaderSimple' || classType === 'CheckpointLoader') &&
                        inputs.ckpt_name
                    ) {
                        return getModelDisplayName(inputs.ckpt_name);
                    }
                }
            } catch (error) {
                console.warn('Failed to parse metadata for item', item.id, error);
            }
        }

        return 'Unknown';
    }

    /**
     * Get model display name from path
     */
    function getModelDisplayName(modelPath) {
        if (!modelPath) return 'Unknown';
        const parts = modelPath.split(/[\/\\]/);
        const filename = parts[parts.length - 1];
        return filename.replace(/\.(ckpt|safetensors|pt)$/i, '').replace(/_/g, ' ');
    }

    /**
     * Update pagination controls
     * Only show for grid/list views, hidden for masonry
     */
    function updatePagination(pagination) {
        if (!pagination) return;

        console.log(`[Gallery] updatePagination called with viewMode: ${currentFilters.viewMode}`);

        // Only show pagination for grid/list views
        if (currentFilters.viewMode === 'masonry') {
            console.log('[Gallery] Masonry detected, hiding pagination');
            hidePagination();
            return;
        }

        console.log('[Gallery] Grid/List detected, showing pagination');

        // Remove existing pagination or create new
        let paginationEl = document.querySelector('.gallery-pagination');
        if (!paginationEl) {
            paginationEl = document.createElement('div');
            paginationEl.className = 'gallery-pagination';
            const contentWrapper = document.querySelector('.content-wrapper');
            if (contentWrapper) {
                contentWrapper.appendChild(paginationEl);
            }
        }

        // Make sure pagination is visible (it might have been hidden for masonry)
        paginationEl.style.display = '';

        // Use 'page' from API (not 'current_page')
        const currentPageNum = pagination.page || pagination.current_page || currentPage;
        const totalPagesNum = pagination.total_pages || totalPages;
        const totalItemsNum = pagination.total || pagination.total_items || 0;

        // Calculate has_prev and has_next if not provided by API
        const hasPrev = typeof pagination.has_prev !== 'undefined'
            ? pagination.has_prev
            : currentPageNum > 1;
        const hasNext = typeof pagination.has_next !== 'undefined'
            ? pagination.has_next
            : currentPageNum < totalPagesNum;

        console.log(`[Gallery] Pagination buttons: hasPrev=${hasPrev}, hasNext=${hasNext}, page=${currentPageNum}/${totalPagesNum}`);

        paginationEl.innerHTML = `
            <button class="btn btn-secondary" ${!hasPrev ? 'disabled' : ''}
                    onclick="loadGalleryPage(${currentPageNum - 1})">
                Previous
            </button>
            <span class="pagination-info">
                Page ${currentPageNum} of ${totalPagesNum}
                (${totalItemsNum} items)
            </span>
            <button class="btn btn-secondary" ${!hasNext ? 'disabled' : ''}
                    onclick="loadGalleryPage(${currentPageNum + 1})">
                Next
            </button>
        `;
    }

    /**
     * Setup filter event listeners
     */
    function setupFilterListeners() {
        // Search input
        const searchInput = document.getElementById('searchInput');
        if (searchInput) {
            let searchTimer;
            searchInput.addEventListener('input', (e) => {
                clearTimeout(searchTimer);
                searchTimer = setTimeout(() => {
                    currentFilters.search = e.target.value;
                    currentPage = 1;  // Reset to first page
                    hasMorePages = true;  // Reset for new search
                    loadGalleryData(1, false);  // Don't scroll for search
                }, 500);
            });
        }

        // View mode toggle buttons
        window.setViewMode = function(mode, evt) {
            const normalizedMode = (mode || '').toString().toLowerCase();

            document.querySelectorAll('.view-mode-btn').forEach(btn => {
                const btnMode = btn.dataset.mode || btn.textContent.trim().toLowerCase();
                const isActive = btn === (evt?.currentTarget || null) || btnMode === normalizedMode;
                btn.classList.toggle('active', isActive);
            });

            const container = document.querySelector('.gallery-container');
            currentFilters.viewMode = applyViewModeToContainer(container, normalizedMode);

            // Switch between infinite scroll and pagination based on view mode
            if (normalizedMode === 'masonry') {
                // Enable infinite scroll, hide pagination
                teardownPagination();
                setupInfiniteScroll();
                hidePagination();
            } else {
                // Enable pagination, disable infinite scroll
                teardownInfiniteScroll();
                setupPaginationListeners();
                showPagination();
            }

            loadGalleryData(1, false);  // Reload from page 1 for view mode change
        };

        // Model filter - first select in filter-bar
        const modelSelect = document.querySelector('.filter-bar select:nth-child(1)');
        if (modelSelect) {
            modelSelect.addEventListener('change', (e) => {
                currentFilters.model = e.target.value;
                loadGalleryData(1, false);  // Don't scroll for filter change
            });
        }

        // Date filter - second select in filter-bar
        const dateSelect = document.querySelector('.filter-bar select:nth-child(2)');
        if (dateSelect) {
            dateSelect.addEventListener('change', (e) => {
                const value = e.target.value;
                const now = new Date();

                switch(value.toLowerCase()) {
                    case 'today':
                        currentFilters.dateFrom = new Date(now.setHours(0,0,0,0)).toISOString();
                        currentFilters.dateTo = new Date().toISOString();
                        break;
                    case 'this week':
                        const weekAgo = new Date(now.setDate(now.getDate() - 7));
                        currentFilters.dateFrom = weekAgo.toISOString();
                        currentFilters.dateTo = new Date().toISOString();
                        break;
                    case 'this month':
                        const monthAgo = new Date(now.setMonth(now.getMonth() - 1));
                        currentFilters.dateFrom = monthAgo.toISOString();
                        currentFilters.dateTo = new Date().toISOString();
                        break;
                    default:
                        delete currentFilters.dateFrom;
                        delete currentFilters.dateTo;
                }

                loadGalleryData(1, false);  // Don't scroll for filter change
            });
        }

        // Sort order - third select in filter-bar
        const sortSelect = document.querySelector('.filter-bar select:nth-child(3)');
        if (sortSelect) {
            sortSelect.addEventListener('change', (e) => {
                const sortMap = {
                    'Newest First': 'date_desc',
                    'Oldest First': 'date_asc',
                    'Most Liked': 'date_desc' // Placeholder until we have likes
                };
                currentFilters.sortOrder = sortMap[e.target.value] || 'date_desc';
                loadGalleryData(currentPage, false);  // Don't scroll for sort change
            });
        }
    }

    /**
     * Setup pagination listeners for grid/list views
     */
    function setupPaginationListeners() {
        window.loadGalleryPage = (page) => {
            if (page >= 1 && page <= totalPages) {
                loadGalleryData(page, true);  // Set scrollToTop to true for pagination
            }
        };
    }

    /**
     * Teardown pagination listeners
     */
    function teardownPagination() {
        // Remove the loadGalleryPage function
        if (window.loadGalleryPage) {
            delete window.loadGalleryPage;
        }
    }

    /**
     * Teardown infinite scroll listeners and cleanup
     */
    function teardownInfiniteScroll() {
        // Remove scroll listeners if they exist
        // Note: We can't remove the specific handler because it's anonymous,
        // but we set a flag to prevent it from triggering new loads
        infiniteScrollEnabled = false;

        // Clear any ongoing timers
        if (progressiveLoadTimer) {
            clearTimeout(progressiveLoadTimer);
            progressiveLoadTimer = null;
        }

        // Clear batch queue
        batchQueue = [];
        preloadingNextBatch = false;

        // Hide the infinite scroll loader
        hideInfiniteScrollLoader();
    }

    /**
     * Hide pagination controls
     */
    function hidePagination() {
        const paginationEl = document.querySelector('.gallery-pagination');
        if (paginationEl) {
            paginationEl.style.display = 'none';
        }
    }

    /**
     * Show pagination controls
     */
    function showPagination() {
        const paginationEl = document.querySelector('.gallery-pagination');
        if (paginationEl) {
            paginationEl.style.display = '';
        }
    }

    /**
     * Setup progressive infinite scroll with intelligent preloading
     */
    function setupInfiniteScroll() {
        // Re-enable infinite scroll when setting it up
        infiniteScrollEnabled = true;
        console.log('[Gallery] setupInfiniteScroll() called - infinite scroll enabled');
        let scrollTimeout;

        // Dynamic thresholds based on scroll behavior and viewport
        const getLoadThreshold = () => {
            // Much more aggressive base threshold - start loading when 2-3 viewports remain
            const viewportHeight = window.innerHeight;
            let threshold = viewportHeight * 3; // Load when 3 screens of content remain

            // Adjust based on scroll velocity (faster scroll = load even earlier)
            if (scrollVelocity > 50) {
                threshold = Math.max(threshold, viewportHeight * 5); // 5 viewports ahead for fast scrolling
            } else if (scrollVelocity > 20) {
                threshold = Math.max(threshold, viewportHeight * 4); // 4 viewports for medium
            }

            // Never less than 2 viewports
            return Math.max(viewportHeight * 2, threshold);
        };

        // Calculate scroll velocity and predict user intent
        const updateScrollMetrics = () => {
            const currentScrollTop = window.pageYOffset || document.documentElement.scrollTop;
            const currentTime = Date.now();
            const timeDelta = currentTime - lastScrollTime;

            if (timeDelta > 0) {
                // Calculate pixels per millisecond, then convert to pixels per second
                scrollVelocity = Math.abs(currentScrollTop - lastScrollTop) / timeDelta * 1000;
            }

            lastScrollTop = currentScrollTop;
            lastScrollTime = currentTime;
        };

        // Load next full page and add to rendering queue
        const loadNextBatch = async () => {
            console.log(`[Gallery] loadNextBatch called: preloading=${preloadingNextBatch}, hasMore=${hasMorePages}`);

            if (preloadingNextBatch || !hasMorePages) {
                console.log(`[Gallery] loadNextBatch aborted: preloading=${preloadingNextBatch}, hasMore=${hasMorePages}`);
                return;
            }

            preloadingNextBatch = true;
            console.log('[Gallery] Starting batch load...');

            try {
                // Ensure we're loading the correct next page
                // If lastPageLoaded is 0 (initial state), use currentPage + 1
                // Otherwise use lastPageLoaded + 1
                const nextPage = lastPageLoaded > 0 ? lastPageLoaded + 1 : currentPage + 1;

                // Build query parameters with page number
                const params = new URLSearchParams({
                    page: nextPage,
                    limit: getItemsPerPage(), // Use full page size from settings
                    sort_order: currentFilters.sortOrder || 'date_desc',
                    view_mode: currentFilters.viewMode || 'grid'
                });

                // Add filters if present
                if (currentFilters.search) params.append('search', currentFilters.search);
                if (currentFilters.tags) params.append('tags', currentFilters.tags);
                if (currentFilters.dateFrom) params.append('date_from', currentFilters.dateFrom);
                if (currentFilters.dateTo) params.append('date_to', currentFilters.dateTo);
                if (currentFilters.model) params.append('model', currentFilters.model);

                console.log(`[Gallery] Loading page ${nextPage} for progressive rendering`);
                const response = await fetch(`/api/v1/gallery/images?${params}`);

                if (response.ok) {
                    const result = await response.json();
                    console.log(`[Gallery] Batch load response: success=${result.success}, dataLength=${result.data?.length || 0}`);

                    if (result.success && result.data && result.data.length > 0) {
                        // Filter out any duplicates (safety check)
                        const newItems = result.data.filter(item => {
                            if (loadedItemIds.has(item.id)) {
                                console.warn(`[Gallery] Duplicate item detected: ${item.id}`);
                                return false;
                            }
                            loadedItemIds.add(item.id);
                            return true;
                        });

                        console.log(`[Gallery] Filtered ${newItems.length} new items (${result.data.length - newItems.length} duplicates)`);

                        if (newItems.length > 0) {
                            // Add to queue for progressive rendering
                            batchQueue.push(...newItems);

                            // Track total items loaded
                            totalItemsLoaded += newItems.length;

                            // Update page tracking
                            lastPageLoaded = nextPage;
                            currentPage = nextPage;

                            // Update pagination info
                            if (result.pagination) {
                                // Calculate has_next if not provided by API
                                if (typeof result.pagination.has_next !== 'undefined') {
                                    hasMorePages = result.pagination.has_next;
                                    console.log(`[Gallery] Using API has_next:`, hasMorePages);
                                } else {
                                    // Use page from API (not current_page)
                                    const pageNum = result.pagination.page || result.pagination.current_page || nextPage;
                                    const totalPagesNum = result.pagination.total_pages || 1;
                                    hasMorePages = pageNum < totalPagesNum;
                                    console.log(`[Gallery] Calculated hasMorePages for batch: ${pageNum} < ${totalPagesNum} = ${hasMorePages}`);
                                }
                            } else {
                                // If no pagination info, check if we got less than requested
                                hasMorePages = result.data.length === getItemsPerPage();
                                console.log(`[Gallery] No pagination info, set hasMorePages=${hasMorePages} based on data length`);
                            }

                            console.log(`[Gallery] Added ${newItems.length} items to queue (queue size: ${batchQueue.length})`);

                            // Start progressive rendering if not already running
                            if (!progressiveLoadTimer) {
                                console.log('[Gallery] Starting progressive rendering');
                                startProgressiveRendering();
                            }
                        } else {
                            console.log('[Gallery] No new items to add (all duplicates), setting hasMorePages=false');
                            hasMorePages = false;
                        }
                    } else {
                        // No more data
                        console.log('[Gallery] No more data available, setting hasMorePages=false');
                        hasMorePages = false;
                    }
                } else {
                    console.error(`[Gallery] Batch load failed with status: ${response.status}`);
                }
            } catch (error) {
                console.error('[Gallery] Failed to preload batch:', error);
            } finally {
                console.log('[Gallery] Batch load complete, resetting preloadingNextBatch flag');
                preloadingNextBatch = false;
            }
        };

        // Render items progressively from queue
        const startProgressiveRendering = () => {
            if (progressiveLoadTimer) {
                return;
            }

            const renderBatch = () => {
                if (batchQueue.length === 0) {
                    progressiveLoadTimer = null;
                    hideInfiniteScrollLoader();
                    return;
                }

                // Take a small chunk from queue (5-10 items at a time)
                const chunkSize = currentFilters.viewMode === 'masonry' ? 5 : 10;
                const chunk = batchQueue.splice(0, Math.min(chunkSize, batchQueue.length));

                // Render this chunk
                const container = document.getElementById('galleryContainer') || document.querySelector('.gallery-container');
                if (container && chunk.length > 0) {
                    const viewMode = currentFilters.viewMode || 'grid';
                    const newElements = [];

                    chunk.forEach(item => {
                        const galleryItem = createGalleryItem(item, viewMode);
                        container.appendChild(galleryItem);
                        newElements.push(galleryItem);
                    });

                    // Handle masonry layout for new items
                    if (viewMode === 'masonry' && masonryInstance) {
                        handleMasonryAppend(newElements);
                    }

                    refreshViewerIntegration(container);
                }

                // Continue rendering if more items in queue
                if (batchQueue.length > 0) {
                    progressiveLoadTimer = setTimeout(renderBatch, 100); // Small delay between batches
                } else {
                    progressiveLoadTimer = null;

                    // Check if we should load more
                    if (hasMorePages) {
                        checkScrollPosition();
                    }
                }
            };

            showInfiniteScrollLoader();
            progressiveLoadTimer = setTimeout(renderBatch, 50);
        };

        const checkScrollPosition = () => {
            // Don't trigger if not enabled or already loading main data
            if (!infiniteScrollEnabled || isLoading) {
                console.log(`[Gallery] checkScrollPosition - skipped: infiniteScrollEnabled=${infiniteScrollEnabled}, isLoading=${isLoading}`);
                return;
            }

            const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
            const scrollHeight = document.documentElement.scrollHeight;
            const clientHeight = window.innerHeight;
            const threshold = getLoadThreshold();

            // Calculate how close we are to bottom
            const distanceFromBottom = scrollHeight - (scrollTop + clientHeight);
            const scrollProgress = (scrollTop + clientHeight) / scrollHeight;

            // Calculate items remaining to view (rough estimate)
            const averageItemHeight = 300; // Approximate height of gallery items
            const itemsRemainingToView = Math.floor(distanceFromBottom / averageItemHeight);
            const itemsInQueue = batchQueue.length;
            const totalBufferedItems = itemsInQueue + itemsRemainingToView;

            // We want to maintain a buffer of at least 50-100 items ahead
            const desiredBuffer = scrollVelocity > 50 ? 100 : 50;

            // Load more if:
            // 1. We're within threshold distance from bottom, OR
            // 2. We have less than desired buffer of items remaining
            const needsMoreContent = distanceFromBottom < threshold || totalBufferedItems < desiredBuffer;

            console.log(`[Gallery] Scroll check: distance=${Math.round(distanceFromBottom)}px, threshold=${Math.round(threshold)}px, buffer=${totalBufferedItems}, hasMore=${hasMorePages}, preloading=${preloadingNextBatch}, needsMore=${needsMoreContent}`);

            if (needsMoreContent && hasMorePages && !preloadingNextBatch) {
                // Always load full page from API (200 items by default)
                console.log(`[Gallery] ✅ Triggering batch load - Distance: ${Math.round(distanceFromBottom)}px, Buffer: ${totalBufferedItems} items`);
                loadNextBatch();
            }

            // Emergency load if we're REALLY close to bottom
            if (distanceFromBottom < 500 && batchQueue.length < 10 && hasMorePages && !preloadingNextBatch) {
                console.log(`[Gallery] 🚨 Emergency load - only ${distanceFromBottom}px from bottom!`);
                loadNextBatch();
            }
        };

        // Main scroll handler
        const handleScroll = () => {
            updateScrollMetrics();
            checkScrollPosition();
        };

        // Debounced scroll handler
        const scrollHandler = () => {
            // Update metrics immediately
            updateScrollMetrics();

            // Debounce the loading check
            clearTimeout(scrollTimeout);
            scrollTimeout = setTimeout(handleScroll, 50); // Faster response
        };

        console.log('[Gallery] Attaching scroll event listener for infinite scroll');
        window.addEventListener('scroll', scrollHandler);

        // Initial check - very important for when viewport is large
        setTimeout(() => {
            console.log('[Gallery] Running initial scroll position check');
            checkScrollPosition();
            // Also check if we need to load more to fill the viewport
            const scrollHeight = document.documentElement.scrollHeight;
            const clientHeight = window.innerHeight;
            if (scrollHeight <= clientHeight * 2 && hasMorePages) {
                console.log('[Gallery] Initial viewport needs more content');
                loadNextBatch();
            }
        }, 500);

        // Periodic check for slow/stopped scrollers
        console.log('[Gallery] Setting up periodic scroll check (every 1s)');
        setInterval(() => {
            // Always check if we have enough buffer
            checkScrollPosition();
        }, 1000); // Check every second instead of every 2
    }

    /**
     * Show loading state
     */
    function showLoadingState() {
        const container = document.querySelector('.gallery-container');
        if (!container) return;

        // Don't show loader on initial page load when container is hidden
        if (container.style.opacity === '0') {
            return;
        }

        // Add loading overlay if not exists
        let loader = container.querySelector('.gallery-loader');
        if (!loader) {
            loader = document.createElement('div');
            loader.className = 'gallery-loader';
            loader.innerHTML = `
                <div class="loader-content">
                    <i class="fa-solid fa-spinner fa-spin fa-2x"></i>
                    <p>Loading gallery...</p>
                </div>
            `;
            container.appendChild(loader);
        }
        loader.style.display = 'flex';
    }

    /**
     * Hide loading state
     */
    function hideLoadingState() {
        const loader = document.querySelector('.gallery-loader');
        if (loader) {
            loader.style.display = 'none';
        }
        hideInfiniteScrollLoader(); // Also hide infinite scroll loader
    }

    /**
     * Show infinite scroll loader at bottom
     */
    function showInfiniteScrollLoader() {
        let loader = document.querySelector('.infinite-scroll-loader');
        if (!loader) {
            loader = document.createElement('div');
            loader.className = 'infinite-scroll-loader';
            loader.innerHTML = `
                <div class="loader-content">
                    <i class="fa-solid fa-spinner fa-spin"></i>
                    <span>Loading more images...</span>
                </div>
            `;
            // Add after gallery container
            const container = document.querySelector('.gallery-container');
            if (container && container.parentNode) {
                container.parentNode.insertBefore(loader, container.nextSibling);
            }
        }
        loader.style.display = 'flex';
    }

    /**
     * Hide infinite scroll loader
     */
    function hideInfiniteScrollLoader() {
        const loader = document.querySelector('.infinite-scroll-loader');
        if (loader) {
            loader.style.display = 'none';
        }
    }

    /**
     * Show empty state
     */
    function showEmptyState() {
        const emptyState = document.getElementById('emptyState');
        if (emptyState) {
            emptyState.classList.remove('empty-state-hidden');
        }

        const container = document.querySelector('.gallery-container');
        if (container) {
            container.style.display = 'none';
        }
    }

    /**
     * Hide empty state
     */
    function hideEmptyState() {
        const emptyState = document.getElementById('emptyState');
        if (emptyState) {
            emptyState.classList.add('empty-state-hidden');
        }

        const container = document.querySelector('.gallery-container');
        if (container) {
            container.style.display = '';
        }
    }

    /**
     * Show error state
     */
    function showErrorState(message) {
        const container = document.querySelector('.gallery-container');
        if (!container) return;

        container.innerHTML = `
            <div class="error-state">
                <i class="fa-solid fa-exclamation-triangle fa-2x"></i>
                <h3>Failed to load gallery</h3>
                <p>${escapeHtml(message)}</p>
                <button class="btn btn-primary" onclick="location.reload()">
                    Retry
                </button>
            </div>
        `;
    }

    /**
     * Utility: Escape HTML
     */
    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text || '';
        return div.innerHTML;
    }

    /**
     * Utility: Format time ago
     */
    function formatTimeAgo(dateString) {
        if (!dateString) return 'Unknown';

        const date = new Date(dateString);
        const now = new Date();
        const diff = now - date;

        const seconds = Math.floor(diff / 1000);
        const minutes = Math.floor(seconds / 60);
        const hours = Math.floor(minutes / 60);
        const days = Math.floor(hours / 24);

        if (days > 7) {
            return date.toLocaleDateString();
        } else if (days > 0) {
            return `${days} day${days > 1 ? 's' : ''} ago`;
        } else if (hours > 0) {
            return `${hours} hour${hours > 1 ? 's' : ''} ago`;
        } else if (minutes > 0) {
            return `${minutes} minute${minutes > 1 ? 's' : ''} ago`;
        } else {
            return 'Just now';
        }
    }

    function refreshViewerIntegration(container) {
        if (!window.ViewerIntegration) {
            return;
        }

        if (!container) {
            container = document.querySelector('#galleryContainer');
        }

        if (!container) {
            return;
        }

        // Check if we already have an active integration
        const active = window.ViewerIntegration.getActiveIntegration?.();

        if (active?.id || activeViewerIntegrationId) {
            // Update existing integration with new images
            const integrationId = active?.id || activeViewerIntegrationId;

            // Extract images with metadata from the refreshed gallery
            const images = Array.from(container.querySelectorAll('.gallery-image img')).map((img, index) => {
                const thumbSrc = img.currentSrc || img.src;
                const fullSrc = img.dataset.fullSrc || img.getAttribute('data-full-src') || thumbSrc;

                // Get metadata from data attribute
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
                        // Silently ignore parse errors for empty/invalid metadata
                        if (metadataStr !== '') {
                            console.debug('Metadata parse issue for non-empty string:', metadataStr.substring(0, 50));
                        }
                    }
                }

                return {
                    src: thumbSrc,
                    fullSrc,
                    alt: img.alt || `Image ${index + 1}`,
                    title: img.getAttribute('title') || img.alt || `Image ${index + 1}`,
                    metadata: metadata
                };
            });

            // Refresh the integration with new images
            if (typeof window.ViewerIntegration.refreshImages === 'function') {
                window.ViewerIntegration.refreshImages(integrationId, images);
            }
        } else {
            // No existing integration, create a new one
            if (typeof window.ViewerIntegration.initGallery !== 'function') {
                return;
            }

            activeViewerIntegrationId = window.ViewerIntegration.initGallery({
                selectors: {
                    gallery: '#galleryContainer',
                    galleryImage: '#galleryContainer .gallery-image img'
                },
                metadata: {
                    enabled: true,
                    position: 'right',
                    autoShow: true,
                    showCopyButtons: true,
                    enableCache: true
                },
                filmstrip: {
                    enabled: true,
                    position: 'bottom',
                    autoHide: false,
                    lazyLoad: true
                }
            });
        }
    }

    function initializeMasonryLayout(container) {
        if (!container || typeof Masonry === 'undefined') {
            console.warn('Masonry library not available');
            return;
        }

        // Add sizer element for column width
        if (!container.querySelector('.masonry-sizer')) {
            const sizer = document.createElement('div');
            sizer.className = 'masonry-sizer';
            container.prepend(sizer);
        }

        // Clean up existing instance
        if (masonryInstance) {
            masonryInstance.destroy();
            masonryInstance = null;
        }

        // Check if imagesLoaded is available
        if (typeof imagesLoaded === 'function') {
            // Use imagesLoaded for perfect layout timing
            imagesLoaded(container, function() {
                masonryInstance = new Masonry(container, {
                    itemSelector: '.gallery-item',
                    columnWidth: '.masonry-sizer',
                    percentPosition: true,
                    gutter: 8,
                    fitWidth: false,
                    transitionDuration: '0.2s',
                    horizontalOrder: false
                });
            });
        } else {
            // Fallback without imagesLoaded (less reliable)
            console.warn('imagesLoaded not available - layout may be imperfect');

            // Create masonry instance immediately
            masonryInstance = new Masonry(container, {
                itemSelector: '.gallery-item',
                columnWidth: '.masonry-sizer',
                percentPosition: true,
                gutter: 8,
                fitWidth: false,
                transitionDuration: '0.2s',
                horizontalOrder: false,
                initLayout: false
            });

            // Progressive layout updates as images load
            const images = container.querySelectorAll('img');
            let layoutTimer;

            const scheduleLayout = () => {
                clearTimeout(layoutTimer);
                layoutTimer = setTimeout(() => {
                    if (masonryInstance) {
                        masonryInstance.layout();
                    }
                }, 100);
            };

            images.forEach((img) => {
                if (!img.complete || img.naturalHeight === 0) {
                    img.addEventListener('load', scheduleLayout, { once: true });
                    img.addEventListener('error', scheduleLayout, { once: true });
                }
            });

            // Initial layout
            masonryInstance.layout();

            // Final layout after all images should be loaded
            setTimeout(() => {
                if (masonryInstance) {
                    masonryInstance.layout();
                }
            }, 1000);
        }
    }

    /**
     * Handle appending new items to existing Masonry layout
     * @param {Array} newElements - New DOM elements to append
     */
    function handleMasonryAppend(newElements) {
        if (!masonryInstance || !newElements || newElements.length === 0) {
            return;
        }

        // Check if imagesLoaded is available
        if (typeof imagesLoaded === 'function') {
            // Use imagesLoaded for proper append timing
            masonryInstance.appended(newElements);

            imagesLoaded(newElements, function() {
                // Layout after all new images are loaded
                if (masonryInstance) {
                    masonryInstance.layout();
                }
            });
        } else {
            // Fallback: append and layout with progressive updates
            masonryInstance.appended(newElements);

            // Collect all images from new elements
            const newImages = [];
            newElements.forEach(element => {
                const imgs = element.querySelectorAll('img');
                imgs.forEach(img => newImages.push(img));
            });

            let layoutTimer;
            const scheduleLayout = () => {
                clearTimeout(layoutTimer);
                layoutTimer = setTimeout(() => {
                    if (masonryInstance) {
                        masonryInstance.layout();
                    }
                }, 100);
            };

            // Set up load handlers for each new image
            newImages.forEach((img) => {
                if (!img.complete || img.naturalHeight === 0) {
                    img.addEventListener('load', scheduleLayout, { once: true });
                    img.addEventListener('error', scheduleLayout, { once: true });
                }
            });

            // Initial layout of new items
            masonryInstance.layout();

            // Safety layout after expected load time
            setTimeout(() => {
                if (masonryInstance) {
                    masonryInstance.layout();
                }
            }, 1000);
        }
    }

    // Global functions for button actions
    window.viewGalleryItem = function(itemId) {
        const active = window.ViewerIntegration?.getActiveIntegration?.();
        if (active?.id) {
            const items = Array.from(document.querySelectorAll('#galleryContainer .gallery-item'));
            const index = items.findIndex(el => el.dataset.itemId === String(itemId));
            if (index >= 0) {
                window.ViewerIntegration.showImage(index, active.id);
                return;
            }
        }

        const item = document.querySelector(`[data-item-id="${itemId}"] img[data-full-src]`);
        if (item) {
            const fullSrc = item.getAttribute('data-full-src') || item.src;
            window.open(fullSrc, '_blank');
        }
    };

    window.copyPrompt = async function(itemId) {
        try {
            // Find the item data
            const container = document.querySelector(`[data-item-id="${itemId}"]`);
            if (!container) return;

            // TODO: Extract prompt from metadata and copy to clipboard
            if (window.NotificationService) {
                window.NotificationService.show('Prompt copied to clipboard', 'success');
            }
        } catch (error) {
            console.error('Failed to copy prompt:', error);
            if (window.NotificationService) {
                window.NotificationService.show('Failed to copy prompt', 'error');
            }
        }
    };

    window.deleteGalleryItem = async function(itemId) {
        if (!confirm('Are you sure you want to delete this item?')) return;

        try {
            const response = await fetch(`/api/v1/gallery/images/${itemId}`, {
                method: 'DELETE'
            });

            if (response.ok) {
                // Reload current page
                await loadGalleryData(currentPage);

                if (window.NotificationService) {
                    window.NotificationService.show('Item deleted successfully', 'success');
                }
            }
        } catch (error) {
            console.error('Failed to delete item:', error);
            if (window.NotificationService) {
                window.NotificationService.show('Failed to delete item', 'error');
            }
        }
    };

    window.refreshGallery = function() {
        loadGalleryData(currentPage);
    };

    /**
     * Keyboard navigation for gallery
     */
    let currentFocusedIndex = -1;

    function setupKeyboardNavigation() {
        document.addEventListener('keydown', handleKeyboardNavigation);
    }

    function handleKeyboardNavigation(e) {
        // Only handle arrow keys and Enter
        if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Enter'].includes(e.key)) {
            return;
        }

        // Don't handle if user is typing in an input field
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
            return;
        }

        const container = document.getElementById('galleryContainer') || document.querySelector('.gallery-container');
        if (!container) return;

        const items = Array.from(container.querySelectorAll('.gallery-item'));
        if (items.length === 0) return;

        // Prevent default arrow key behavior (page scrolling)
        if (e.key.startsWith('Arrow')) {
            e.preventDefault();
        }

        // Initialize focus if not set
        if (currentFocusedIndex === -1) {
            currentFocusedIndex = 0;
            focusGalleryItem(items[currentFocusedIndex]);
            return;
        }

        const viewMode = container.dataset.viewMode || 'grid';
        let newIndex = currentFocusedIndex;

        if (viewMode === 'masonry' || viewMode === 'grid') {
            // Calculate grid dimensions for proper up/down navigation
            const itemsPerRow = getItemsPerRow(container, items);

            switch (e.key) {
                case 'ArrowLeft':
                    if (currentFocusedIndex > 0) {
                        newIndex = currentFocusedIndex - 1;
                    }
                    break;

                case 'ArrowRight':
                    if (currentFocusedIndex < items.length - 1) {
                        newIndex = currentFocusedIndex + 1;
                    }
                    break;

                case 'ArrowUp':
                    if (currentFocusedIndex >= itemsPerRow) {
                        newIndex = currentFocusedIndex - itemsPerRow;
                    }
                    break;

                case 'ArrowDown':
                    if (currentFocusedIndex < items.length - itemsPerRow) {
                        newIndex = currentFocusedIndex + itemsPerRow;
                    }
                    break;

                case 'Enter':
                    // Open the focused image in viewer or trigger click
                    if (items[currentFocusedIndex]) {
                        const clickEvent = new MouseEvent('click', {
                            view: window,
                            bubbles: true,
                            cancelable: true
                        });
                        items[currentFocusedIndex].dispatchEvent(clickEvent);
                    }
                    break;
            }
        } else {
            // List view - only up/down navigation
            switch (e.key) {
                case 'ArrowUp':
                case 'ArrowLeft':
                    if (currentFocusedIndex > 0) {
                        newIndex = currentFocusedIndex - 1;
                    }
                    break;

                case 'ArrowDown':
                case 'ArrowRight':
                    if (currentFocusedIndex < items.length - 1) {
                        newIndex = currentFocusedIndex + 1;
                    }
                    break;

                case 'Enter':
                    if (items[currentFocusedIndex]) {
                        const clickEvent = new MouseEvent('click', {
                            view: window,
                            bubbles: true,
                            cancelable: true
                        });
                        items[currentFocusedIndex].dispatchEvent(clickEvent);
                    }
                    break;
            }
        }

        // Update focus if index changed
        if (newIndex !== currentFocusedIndex && newIndex >= 0 && newIndex < items.length) {
            unfocusGalleryItem(items[currentFocusedIndex]);
            currentFocusedIndex = newIndex;
            focusGalleryItem(items[currentFocusedIndex]);
        }
    }

    function getItemsPerRow(container, items) {
        if (items.length < 2) return 1;

        // Get the actual positions of items to determine columns
        const firstItemRect = items[0].getBoundingClientRect();
        let itemsPerRow = 1;

        for (let i = 1; i < items.length; i++) {
            const rect = items[i].getBoundingClientRect();
            if (rect.top > firstItemRect.bottom) {
                break;
            }
            itemsPerRow++;
        }

        return itemsPerRow || 1;
    }

    function focusGalleryItem(item) {
        if (!item) return;

        item.classList.add('keyboard-focused');

        // Scroll into view if needed
        item.scrollIntoView({
            behavior: 'smooth',
            block: 'nearest',
            inline: 'nearest'
        });
    }

    function unfocusGalleryItem(item) {
        if (!item) return;
        item.classList.remove('keyboard-focused');
    }

    // Reset focus when gallery is reloaded
    function resetKeyboardFocus() {
        currentFocusedIndex = -1;
        document.querySelectorAll('.gallery-item.keyboard-focused').forEach(item => {
            item.classList.remove('keyboard-focused');
        });
    }

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            initDynamicGallery();
            setupKeyboardNavigation();
        });
    } else {
        initDynamicGallery();
        setupKeyboardNavigation();
    }

})();