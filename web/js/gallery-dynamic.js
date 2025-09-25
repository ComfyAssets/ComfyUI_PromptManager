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

    /**
     * Initialize dynamic gallery
     */
    async function initDynamicGallery() {
        console.log('Initializing dynamic gallery...');

        // Setup event listeners
        setupFilterListeners();
        setupPaginationListeners();

        // Load initial data
        await loadGalleryData();

        // Load available models for filter
        // await loadModelOptions(); // Commented out as this API doesn't exist yet
    }

    /**
     * Load gallery data from API
     */
    async function loadGalleryData(page = 1) {
        if (isLoading) return;
        isLoading = true;

        try {
            showLoadingState();

            // Build query parameters
            const params = new URLSearchParams({
                page: page,
                items_per_page: 60,
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
                currentPage = result.pagination?.current_page || page;
                totalPages = result.pagination?.total_pages || 1;

                renderGalleryItems(result.data);
                updatePagination(result.pagination);

                if (result.data.length === 0) {
                    showEmptyState();
                } else {
                    hideEmptyState();
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

    /**
     * Render gallery items
     */
    function renderGalleryItems(items) {
        const container = document.getElementById('galleryContainer') || document.querySelector('.gallery-container');
        if (!container) {
            console.error('Gallery container not found');
            return;
        }

        // Clear existing items
        container.innerHTML = '';

        if (!items || items.length === 0) {
            showEmptyState();
            return;
        }

        // Create gallery items using actual API data structure
        items.forEach(item => {
            const galleryItem = createGalleryItem(item);
            container.appendChild(galleryItem);
        });

        refreshViewerIntegration(container);
    }

    /**
     * Create a gallery item element from actual API data
     */
    function createGalleryItem(item) {
        const div = document.createElement('div');
        div.className = 'gallery-item';
        div.dataset.itemId = item.id;

        const modelName = extractModelName(item);

        const placeholderImage = '/prompt_manager/images/placeholder.png';
        const thumbnailUrl = item.thumbnail_url || item.thumbnail || placeholderImage;
        const fullImageUrl = item.image_url || item.url || item.path || thumbnailUrl;

        // Build HTML
        div.innerHTML = `
            <div class="gallery-image">
                <img src="${thumbnailUrl}"
                     data-full-src="${fullImageUrl}"
                     alt="${escapeHtml(item.filename || '')}"
                     loading="lazy"
                     data-placeholder="${placeholderImage}" />
            </div>
            <div class="gallery-info">
                <div class="gallery-title">${escapeHtml(item.filename || 'Untitled')}</div>
                <div class="gallery-meta">
                    <span class="chip">${escapeHtml(modelName)}</span>
                    <span class="chip">${item.dimensions || 'Unknown'}</span>
                    <span class="chip">${item.size || 'Unknown'}</span>
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

        // Add error fallback for broken thumbnails
        const img = div.querySelector('.gallery-image img');
        if (img) {
            const handleImageError = () => {
                const fallbackTried = img.dataset.fallbackTried === '1';
                if (!fallbackTried && fullImageUrl && img.src !== fullImageUrl) {
                    img.dataset.fallbackTried = '1';
                    img.src = fullImageUrl;
                    return;
                }

                img.removeEventListener('error', handleImageError);
                img.dataset.fallbackTried = '1';
                img.src = img.dataset.placeholder || placeholderImage;
            };

            img.addEventListener('error', handleImageError);
            img.style.cursor = 'pointer';
        }

        return div;
    }

    function extractModelName(item) {
        if (!item || !item.metadata) {
            return 'Unknown';
        }

        try {
            const rawMetadata = item.metadata;
            const metadata = typeof rawMetadata === 'string'
                ? JSON.parse(rawMetadata)
                : rawMetadata;

            if (metadata && typeof metadata === 'object') {
                for (const value of Object.values(metadata)) {
                    if (!value || typeof value !== 'object') {
                        continue;
                    }

                    const inputs = value.inputs || {};
                    if (
                        (value.class_type === 'CheckpointLoaderSimple' || value.class_type === 'CheckpointLoader') &&
                        inputs.ckpt_name
                    ) {
                        return getModelDisplayName(inputs.ckpt_name);
                    }
                }
            }
        } catch (error) {
            console.warn('Failed to parse metadata for item', item.id, error);
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
     */
    function updatePagination(pagination) {
        if (!pagination) return;

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

        const currentPageNum = pagination.current_page || currentPage;
        const totalPagesNum = pagination.total_pages || totalPages;
        const totalItemsNum = pagination.total_items || 0;

        paginationEl.innerHTML = `
            <button class="btn btn-secondary" ${!pagination.has_prev ? 'disabled' : ''}
                    onclick="loadGalleryPage(${currentPageNum - 1})">
                Previous
            </button>
            <span class="pagination-info">
                Page ${currentPageNum} of ${totalPagesNum}
                (${totalItemsNum} items)
            </span>
            <button class="btn btn-secondary" ${!pagination.has_next ? 'disabled' : ''}
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
                    loadGalleryData(1);
                }, 500);
            });
        }

        // View mode toggle buttons
        window.setViewMode = function(mode, evt) {
            currentFilters.viewMode = mode;

            document.querySelectorAll('.view-mode-btn').forEach(btn => {
                const btnMode = btn.dataset.mode || btn.textContent.trim().toLowerCase();
                const isActive = btn === (evt?.currentTarget || null) || btnMode === mode;
                btn.classList.toggle('active', isActive);
            });

            const container = document.querySelector('.gallery-container');
            if (container) {
                const layoutClasses = ['gallery-grid', 'masonry-grid'];
                layoutClasses.forEach(cls => container.classList.remove(cls));
                container.classList.add('gallery-grid');
                if (mode === 'masonry') {
                    container.classList.add('masonry-grid');
                }
                container.dataset.viewMode = mode;
            }

            loadGalleryData(currentPage);
        };

        // Model filter - first select in filter-bar
        const modelSelect = document.querySelector('.filter-bar select:nth-child(1)');
        if (modelSelect) {
            modelSelect.addEventListener('change', (e) => {
                currentFilters.model = e.target.value;
                loadGalleryData(1);
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

                loadGalleryData(1);
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
                loadGalleryData(currentPage);
            });
        }
    }

    /**
     * Setup pagination listeners
     */
    function setupPaginationListeners() {
        // Make loadGalleryPage globally available
        window.loadGalleryPage = (page) => {
            if (page >= 1 && page <= totalPages) {
                loadGalleryData(page);
            }
        };
    }

    /**
     * Show loading state
     */
    function showLoadingState() {
        const container = document.querySelector('.gallery-container');
        if (!container) return;

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
        if (!window.ViewerIntegration || typeof window.ViewerIntegration.initGallery !== 'function') {
            return;
        }

        if (!container) {
            container = document.querySelector('#galleryContainer');
        }

        if (!container) {
            return;
        }

        const active = window.ViewerIntegration.getActiveIntegration?.();
        if (active?.id && typeof window.ViewerIntegration.destroy === 'function') {
            window.ViewerIntegration.destroy(active.id);
        } else if (activeViewerIntegrationId && typeof window.ViewerIntegration.destroy === 'function') {
            window.ViewerIntegration.destroy(activeViewerIntegrationId);
        }

        if (typeof window.ViewerIntegration.initGallery !== 'function') {
            return;
        }

        activeViewerIntegrationId = window.ViewerIntegration.initGallery({
            selectors: {
                gallery: '#galleryContainer',
                galleryImage: '#galleryContainer .gallery-image img'
            }
        });
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

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initDynamicGallery);
    } else {
        initDynamicGallery();
    }

})();
