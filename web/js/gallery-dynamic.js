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
        await loadModelOptions();
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
                items_per_page: 20,
                sort_order: currentFilters.sortOrder || 'date_desc',
                view_mode: currentFilters.viewMode || 'grid'
            });

            // Add filters if present
            if (currentFilters.search) params.append('search', currentFilters.search);
            if (currentFilters.tags) params.append('tags', currentFilters.tags);
            if (currentFilters.dateFrom) params.append('date_from', currentFilters.dateFrom);
            if (currentFilters.dateTo) params.append('date_to', currentFilters.dateTo);
            if (currentFilters.model) params.append('model', currentFilters.model);

            // Fetch data from API
            const response = await fetch(`/api/promptmanager/gallery?${params}`);

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const result = await response.json();

            if (result.success && result.data) {
                currentPage = result.data.pagination.page;
                totalPages = result.data.pagination.total_pages;

                renderGalleryItems(result.data.items);
                updatePagination(result.data.pagination);
                hideEmptyState();
            } else if (result.data && result.data.items.length === 0) {
                showEmptyState();
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

        // Clear existing mock items
        container.innerHTML = '';

        if (items.length === 0) {
            showEmptyState();
            return;
        }

        // Create gallery items
        items.forEach(item => {
            const galleryItem = createGalleryItem(item);
            container.appendChild(galleryItem);
        });

        // Re-initialize ViewerJS if available
        if (window.ViewerIntegration) {
            window.ViewerIntegration.refresh();
        }
    }

    /**
     * Create a gallery item element
     */
    function createGalleryItem(item) {
        const div = document.createElement('div');
        div.className = 'gallery-item';
        div.dataset.itemId = item.id;

        // Get first image or placeholder
        const primaryImage = item.images && item.images[0] ? item.images[0] : {
            src: '/prompt_manager/images/placeholder.png',
            thumb: '/prompt_manager/images/placeholder-thumb.png',
            alt: 'No image'
        };

        // Build HTML
        div.innerHTML = `
            <div class="gallery-image">
                <img src="${primaryImage.thumb || primaryImage.src}"
                     alt="${escapeHtml(primaryImage.alt || item.metadata.prompt || '')}"
                     loading="lazy" />
            </div>
            <div class="gallery-info">
                <div class="gallery-title">${escapeHtml(item.display.title)}</div>
                <div class="gallery-meta">
                    ${item.metadata.model ? `<span class="chip chip-primary">${escapeHtml(getModelName(item.metadata.model))}</span>` : ''}
                    ${item.images && item.images[0] ? `<span class="chip">${getImageDimensions(item.images[0])}</span>` : ''}
                </div>
                <div class="gallery-timestamp">
                    ${formatTimeAgo(item.metadata.created_at)}
                </div>
                <div class="prompt-film-strip">
                    <div class="film-strip-thumbnails">
                        ${renderThumbnails(item.images)}
                    </div>
                    <div class="film-strip-actions">
                        <button class="film-strip-btn" title="View Gallery" onclick="viewGalleryItem('${item.id}')">
                            <i class="fa-solid fa-image"></i>
                        </button>
                        <button class="film-strip-btn" title="Edit" onclick="editGalleryItem('${item.id}')">
                            <i class="fa-solid fa-pen-to-square"></i>
                        </button>
                        <button class="film-strip-btn" title="Delete" onclick="deleteGalleryItem('${item.id}')">
                            <i class="fa-solid fa-trash-can"></i>
                        </button>
                    </div>
                </div>
            </div>
        `;

        // Add click handler for image
        const img = div.querySelector('.gallery-image img');
        if (img && item.images && item.images.length > 0) {
            img.style.cursor = 'pointer';
            img.addEventListener('click', () => {
                if (window.ViewerIntegration) {
                    window.ViewerIntegration.show(item.images, 0, item.metadata);
                }
            });
        }

        return div;
    }

    /**
     * Render thumbnail strip
     */
    function renderThumbnails(images) {
        if (!images || images.length === 0) {
            return '<div class="film-strip-thumbnail">0</div>';
        }

        return images.slice(0, 3).map((img, index) => {
            if (img.thumb) {
                return `<div class="film-strip-thumbnail">
                    <img src="${img.thumb}" alt="${index + 1}" />
                </div>`;
            }
            return `<div class="film-strip-thumbnail">${index + 1}</div>`;
        }).join('');
    }

    /**
     * Update pagination controls
     */
    function updatePagination(pagination) {
        // Remove existing pagination or create new
        let paginationEl = document.querySelector('.gallery-pagination');
        if (!paginationEl) {
            paginationEl = document.createElement('div');
            paginationEl.className = 'gallery-pagination';
            document.querySelector('.content-section').appendChild(paginationEl);
        }

        paginationEl.innerHTML = `
            <button class="btn btn-secondary" ${pagination.page <= 1 ? 'disabled' : ''}
                    onclick="loadGalleryPage(${pagination.page - 1})">
                Previous
            </button>
            <span class="pagination-info">
                Page ${pagination.page} of ${pagination.total_pages}
                (${pagination.total_items} items)
            </span>
            <button class="btn btn-secondary" ${pagination.page >= pagination.total_pages ? 'disabled' : ''}
                    onclick="loadGalleryPage(${pagination.page + 1})">
                Next
            </button>
        `;
    }

    /**
     * Load available models for filter dropdown
     */
    async function loadModelOptions() {
        try {
            const response = await fetch('/api/promptmanager/models');
            if (!response.ok) return;

            const result = await response.json();
            if (!result.success || !result.models) return;

            const select = document.querySelector('select[name="model"]');
            if (!select) return;

            // Clear existing options except "All Models"
            select.innerHTML = '<option value="">All Models</option>';

            // Add model options
            result.models.forEach(model => {
                const option = document.createElement('option');
                option.value = model.name;
                option.textContent = `${model.display_name} (${model.count})`;
                select.appendChild(option);
            });

        } catch (error) {
            console.warn('Failed to load model options:', error);
        }
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

                switch(value) {
                    case 'today':
                        currentFilters.dateFrom = new Date(now.setHours(0,0,0,0)).toISOString();
                        currentFilters.dateTo = new Date().toISOString();
                        break;
                    case 'week':
                        const weekAgo = new Date(now.setDate(now.getDate() - 7));
                        currentFilters.dateFrom = weekAgo.toISOString();
                        currentFilters.dateTo = new Date().toISOString();
                        break;
                    case 'month':
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
     * Utility: Get model display name
     */
    function getModelName(modelPath) {
        if (!modelPath) return 'Unknown';
        const parts = modelPath.split(/[\/\\]/);
        const filename = parts[parts.length - 1];
        return filename.replace(/\.(ckpt|safetensors|pt)$/i, '');
    }

    /**
     * Utility: Get image dimensions
     */
    function getImageDimensions(image) {
        // Try to extract from metadata or use placeholder
        if (image.width && image.height) {
            return `${image.width}x${image.height}`;
        }
        return '512x512'; // Default
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

    // Global functions for button actions
    window.viewGalleryItem = function(itemId) {
        console.log('View item:', itemId);
        // This will be handled by ViewerJS integration
    };

    window.editGalleryItem = function(itemId) {
        console.log('Edit item:', itemId);
        // TODO: Implement edit functionality
    };

    window.deleteGalleryItem = async function(itemId) {
        if (!confirm('Are you sure you want to delete this item?')) return;

        try {
            const response = await fetch(`/api/promptmanager/gallery/${itemId}`, {
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

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initDynamicGallery);
    } else {
        initDynamicGallery();
    }

})();