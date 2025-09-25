/**
 * Unified Gallery Module - Integrates all gallery components with ViewerJS
 * Provides a single interface for all gallery views while respecting settings
 */

const UnifiedGallery = (function() {
    'use strict';

    // Private state
    let currentPage = 1;
    let currentFilter = null;
    let viewerInstance = null;
    let galleryContainer = null;
    let settings = {};

    /**
     * Initialize the unified gallery
     */
    function init(container, options = {}) {
        galleryContainer = container;

        // Load settings from localStorage and merge with options
        settings = {
            ...loadSettings(),
            ...options
        };

        // Initialize ViewerJS integration
        initializeViewer();

        // Setup event listeners
        setupEventListeners();

        // Load initial gallery data
        loadGalleryPage(1);

        return {
            refresh: () => loadGalleryPage(currentPage),
            setFilter: (filter) => applyFilter(filter),
            setViewMode: (mode) => changeViewMode(mode),
            exportSelection: () => exportSelectedItems()
        };
    }

    /**
     * Load settings from localStorage and API
     */
    function loadSettings() {
        const localSettings = JSON.parse(localStorage.getItem('promptManagerSettings') || '{}');

        return {
            itemsPerPage: localSettings.itemsPerPage || 20,
            viewMode: localSettings.viewMode || 'grid',
            sortOrder: localSettings.sortOrder || 'date_desc',
            viewer: {
                theme: localSettings.viewerTheme || 'dark',
                toolbar: localSettings.viewerToolbar !== false,
                navbar: localSettings.viewerNavbar !== false,
                title: localSettings.viewerTitle !== false,
                keyboard: localSettings.viewerKeyboard !== false,
                backdrop: localSettings.viewerBackdrop !== false,
                fullscreen: localSettings.viewerFullscreen !== false,
                ...localSettings.viewer
            },
            filmstrip: {
                enabled: localSettings.filmstripEnabled !== false,
                position: localSettings.filmstripPosition || 'bottom',
                thumbnailSize: localSettings.filmstripThumbnailSize || 'medium',
                ...localSettings.filmstrip
            },
            metadata: {
                enabled: localSettings.metadataEnabled !== false,
                position: localSettings.metadataPosition || 'right',
                fields: localSettings.metadataFields || ['prompt', 'model', 'settings'],
                ...localSettings.metadata
            }
        };
    }

    /**
     * Save settings to localStorage
     */
    function saveSettings() {
        localStorage.setItem('promptManagerSettings', JSON.stringify(settings));

        // Also sync to backend
        fetch('/api/promptmanager/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        }).catch(err => console.warn('Failed to sync settings:', err));
    }

    /**
     * Initialize ViewerJS with our modules
     */
    function initializeViewer() {
        if (!window.ViewerIntegration) {
            console.error('ViewerIntegration not loaded');
            return;
        }

        // Initialize ViewerIntegration with our settings
        viewerInstance = ViewerIntegration.init(galleryContainer, {
            viewer: settings.viewer,
            filmstrip: settings.filmstrip,
            metadata: settings.metadata,
            onReady: () => console.log('Viewer ready'),
            onView: (detail) => trackImageView(detail),
            onHidden: () => updateGalleryState()
        });
    }

    /**
     * Load a page of gallery items
     */
    async function loadGalleryPage(page) {
        try {
            // Show loading state
            showLoading();

            // Build query params
            const params = new URLSearchParams({
                page: page,
                items_per_page: settings.itemsPerPage,
                sort_order: settings.sortOrder,
                view_mode: settings.viewMode
            });

            if (currentFilter) {
                if (currentFilter.tags) params.append('tags', currentFilter.tags.join(','));
                if (currentFilter.search) params.append('search', currentFilter.search);
                if (currentFilter.dateFrom) params.append('date_from', currentFilter.dateFrom);
                if (currentFilter.dateTo) params.append('date_to', currentFilter.dateTo);
            }

            // Fetch gallery data
            const response = await fetch(`/api/promptmanager/gallery?${params}`);
            const data = await response.json();

            // Update current page
            currentPage = page;

            // Render gallery
            renderGallery(data);

            // Update pagination
            updatePagination(data.pagination);

            // Hide loading state
            hideLoading();

        } catch (error) {
            console.error('Failed to load gallery:', error);
            showError('Failed to load gallery items');
        }
    }

    /**
     * Render gallery items based on view mode
     */
    function renderGallery(data) {
        if (!galleryContainer) return;

        // Clear existing content
        const galleryGrid = galleryContainer.querySelector('.gallery-grid') ||
                           document.createElement('div');
        galleryGrid.className = `gallery-grid gallery-${settings.viewMode}`;
        galleryGrid.innerHTML = '';

        // Render based on view mode
        if (settings.viewMode === 'grid') {
            renderGridView(galleryGrid, data.items);
        } else if (settings.viewMode === 'list') {
            renderListView(galleryGrid, data.items);
        } else if (settings.viewMode === 'masonry') {
            renderMasonryView(galleryGrid, data.items);
        }

        // Replace or append gallery grid
        const existingGrid = galleryContainer.querySelector('.gallery-grid');
        if (existingGrid) {
            existingGrid.replaceWith(galleryGrid);
        } else {
            galleryContainer.appendChild(galleryGrid);
        }

        // Setup viewer for all images
        setupImageViewers(data.items);
    }

    /**
     * Render grid view
     */
    function renderGridView(container, items) {
        items.forEach(item => {
            const card = createGalleryCard(item);
            container.appendChild(card);
        });
    }

    /**
     * Create a gallery card element
     */
    function createGalleryCard(item) {
        const card = document.createElement('div');
        card.className = 'gallery-card';
        card.dataset.itemId = item.id;

        // Main image
        const imageContainer = document.createElement('div');
        imageContainer.className = 'gallery-card-image';

        const img = document.createElement('img');
        img.src = item.images[0].thumb || item.images[0].src;
        img.alt = item.metadata.prompt || '';
        img.loading = 'lazy';
        img.dataset.galleryItem = JSON.stringify(item);

        imageContainer.appendChild(img);

        // Overlay with badges
        if (item.display.badges?.length > 0) {
            const overlay = document.createElement('div');
            overlay.className = 'gallery-card-overlay';

            item.display.badges.forEach(badge => {
                const badgeEl = document.createElement('span');
                badgeEl.className = `badge ${badge.class}`;
                badgeEl.textContent = badge.text;
                overlay.appendChild(badgeEl);
            });

            imageContainer.appendChild(overlay);
        }

        // Info section
        const info = document.createElement('div');
        info.className = 'gallery-card-info';

        const title = document.createElement('h4');
        title.className = 'gallery-card-title';
        title.textContent = item.display.title;
        info.appendChild(title);

        if (item.display.subtitle) {
            const subtitle = document.createElement('p');
            subtitle.className = 'gallery-card-subtitle';
            subtitle.textContent = item.display.subtitle;
            info.appendChild(subtitle);
        }

        // Actions
        const actions = document.createElement('div');
        actions.className = 'gallery-card-actions';

        const viewBtn = document.createElement('button');
        viewBtn.className = 'btn btn-sm btn-view';
        viewBtn.innerHTML = '<i class="fas fa-eye"></i>';
        viewBtn.title = 'View';
        viewBtn.onclick = () => viewItem(item);

        const selectBtn = document.createElement('button');
        selectBtn.className = 'btn btn-sm btn-select';
        selectBtn.innerHTML = '<i class="fas fa-check"></i>';
        selectBtn.title = 'Select';
        selectBtn.onclick = () => toggleSelection(item.id);

        actions.appendChild(viewBtn);
        actions.appendChild(selectBtn);
        info.appendChild(actions);

        card.appendChild(imageContainer);
        card.appendChild(info);

        return card;
    }

    /**
     * Setup ViewerJS for gallery images
     */
    function setupImageViewers(items) {
        if (!viewerInstance) return;

        // Prepare image list for viewer
        const images = [];
        items.forEach(item => {
            item.images.forEach(img => {
                images.push({
                    src: img.src,
                    alt: img.alt || item.metadata.prompt,
                    title: img.title || item.display.title,
                    metadata: item.metadata
                });
            });
        });

        // Update viewer with new image list
        viewerInstance.update(images);

        // Add click handlers to all gallery images
        galleryContainer.querySelectorAll('.gallery-card-image img').forEach((img, index) => {
            img.style.cursor = 'pointer';
            img.onclick = () => viewerInstance.show(index);
        });
    }

    /**
     * View an item in the viewer
     */
    function viewItem(item) {
        if (!viewerInstance) return;

        // Find the index of the first image of this item
        const allImages = galleryContainer.querySelectorAll('.gallery-card-image img');
        let index = -1;

        allImages.forEach((img, i) => {
            const data = JSON.parse(img.dataset.galleryItem || '{}');
            if (data.id === item.id && index === -1) {
                index = i;
            }
        });

        if (index >= 0) {
            viewerInstance.show(index);
        }
    }

    /**
     * Toggle item selection
     */
    function toggleSelection(itemId) {
        const card = galleryContainer.querySelector(`.gallery-card[data-item-id="${itemId}"]`);
        if (card) {
            card.classList.toggle('selected');
            updateSelectionCount();
        }
    }

    /**
     * Update pagination controls
     */
    function updatePagination(pagination) {
        let paginationContainer = galleryContainer.querySelector('.gallery-pagination');

        if (!paginationContainer) {
            paginationContainer = document.createElement('div');
            paginationContainer.className = 'gallery-pagination';
            galleryContainer.appendChild(paginationContainer);
        }

        paginationContainer.innerHTML = '';

        // Previous button
        const prevBtn = document.createElement('button');
        prevBtn.className = 'btn btn-pagination';
        prevBtn.textContent = 'Previous';
        prevBtn.disabled = pagination.page <= 1;
        prevBtn.onclick = () => loadGalleryPage(pagination.page - 1);
        paginationContainer.appendChild(prevBtn);

        // Page info
        const pageInfo = document.createElement('span');
        pageInfo.className = 'pagination-info';
        pageInfo.textContent = `Page ${pagination.page} of ${pagination.total_pages} (${pagination.total_items} items)`;
        paginationContainer.appendChild(pageInfo);

        // Next button
        const nextBtn = document.createElement('button');
        nextBtn.className = 'btn btn-pagination';
        nextBtn.textContent = 'Next';
        nextBtn.disabled = pagination.page >= pagination.total_pages;
        nextBtn.onclick = () => loadGalleryPage(pagination.page + 1);
        paginationContainer.appendChild(nextBtn);
    }

    /**
     * Apply filter to gallery
     */
    function applyFilter(filter) {
        currentFilter = filter;
        currentPage = 1;
        loadGalleryPage(1);
    }

    /**
     * Change view mode
     */
    function changeViewMode(mode) {
        if (settings.viewMode !== mode) {
            settings.viewMode = mode;
            saveSettings();
            loadGalleryPage(currentPage);
        }
    }

    /**
     * Setup event listeners
     */
    function setupEventListeners() {
        // Listen for settings changes
        window.addEventListener('settingsUpdated', (e) => {
            if (e.detail) {
                settings = { ...settings, ...e.detail };
                saveSettings();

                // Update viewer if settings changed
                if (e.detail.viewer || e.detail.filmstrip || e.detail.metadata) {
                    viewerInstance?.updateConfig({
                        viewer: settings.viewer,
                        filmstrip: settings.filmstrip,
                        metadata: settings.metadata
                    });
                }

                // Reload gallery if view settings changed
                if (e.detail.viewMode || e.detail.itemsPerPage) {
                    loadGalleryPage(currentPage);
                }
            }
        });

        // Listen for refresh requests
        window.addEventListener('refreshGallery', () => {
            loadGalleryPage(currentPage);
        });
    }

    /**
     * Export selected items
     */
    async function exportSelectedItems() {
        const selectedCards = galleryContainer.querySelectorAll('.gallery-card.selected');
        const itemIds = Array.from(selectedCards).map(card => card.dataset.itemId);

        if (itemIds.length === 0) {
            showNotification('No items selected', 'warning');
            return;
        }

        try {
            const response = await fetch('/api/promptmanager/gallery/export', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ item_ids: itemIds, format: 'json' })
            });

            if (response.ok) {
                const result = await response.json();
                showNotification(`Exported ${itemIds.length} items`, 'success');
                // Trigger download
                window.location.href = result.download_url;
            }
        } catch (error) {
            console.error('Export failed:', error);
            showNotification('Export failed', 'error');
        }
    }

    /**
     * Show loading state
     */
    function showLoading() {
        let loader = galleryContainer.querySelector('.gallery-loader');
        if (!loader) {
            loader = document.createElement('div');
            loader.className = 'gallery-loader';
            loader.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Loading...';
            galleryContainer.appendChild(loader);
        }
        loader.style.display = 'block';
    }

    /**
     * Hide loading state
     */
    function hideLoading() {
        const loader = galleryContainer.querySelector('.gallery-loader');
        if (loader) {
            loader.style.display = 'none';
        }
    }

    /**
     * Show error message
     */
    function showError(message) {
        hideLoading();
        const errorEl = document.createElement('div');
        errorEl.className = 'gallery-error';
        errorEl.textContent = message;
        galleryContainer.appendChild(errorEl);
    }

    /**
     * Show notification
     */
    function showNotification(message, type = 'info') {
        // This would integrate with your notification system
        console.log(`[${type}] ${message}`);
    }

    /**
     * Track image view for analytics
     */
    function trackImageView(detail) {
        // Track view in database
        fetch('/api/promptmanager/gallery/track-view', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                item_id: detail.metadata?.prompt_id,
                timestamp: new Date().toISOString()
            })
        }).catch(err => console.warn('Failed to track view:', err));
    }

    /**
     * Update gallery state after viewer closes
     */
    function updateGalleryState() {
        // Could refresh thumbnails, update view counts, etc.
    }

    /**
     * Update selection count display
     */
    function updateSelectionCount() {
        const selected = galleryContainer.querySelectorAll('.gallery-card.selected').length;

        let counter = galleryContainer.querySelector('.selection-counter');
        if (!counter && selected > 0) {
            counter = document.createElement('div');
            counter.className = 'selection-counter';
            galleryContainer.insertBefore(counter, galleryContainer.firstChild);
        }

        if (counter) {
            if (selected > 0) {
                counter.textContent = `${selected} items selected`;
                counter.style.display = 'block';
            } else {
                counter.style.display = 'none';
            }
        }
    }

    // Public API
    return {
        init: init,
        loadPage: loadGalleryPage,
        setFilter: applyFilter,
        setViewMode: changeViewMode,
        exportSelection: exportSelectedItems,
        refresh: () => loadGalleryPage(currentPage)
    };

})();

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = UnifiedGallery;
}