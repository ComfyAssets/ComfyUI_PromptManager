/**
 * PromptManager Gallery Component
 * 
 * Professional image gallery with infinite scroll, lazy loading, and advanced filtering.
 * Showcases the super clean, modern dark themed aesthetic.
 */

class Gallery {
  constructor(container, options = {}) {
    this.container = typeof container === 'string' ? document.getElementById(container) : container;
    this.options = {
      itemsPerPage: 24,
      loadThreshold: 500, // Load more when 500px from bottom
      imageSize: 'medium', // small, medium, large
      showMetadata: true,
      enableSelection: true,
      enableFiltering: true,
      enableSorting: true,
      ...options
    };

    // State
    this.items = [];
    this.filteredItems = [];
    this.selectedItems = new Set();
    this.currentPage = 0;
    this.isLoading = false;
    this.hasMoreItems = true;
    this.filters = {
      search: '',
      dateRange: null,
      rating: null,
      category: null
    };
    this.sortBy = 'created_at';
    this.sortOrder = 'desc';

    // Initialize
    this.init();
  }

  /**
   * Initialize gallery
   */
  init() {
    this.createStructure();
    this.bindEvents();
    this.loadInitialItems();
  }

  /**
   * Create gallery HTML structure
   */
  createStructure() {
    this.container.className = 'gallery-container';
    this.container.innerHTML = `
      <div class="gallery-header">
        <div class="gallery-toolbar">
          <div class="gallery-search">
            <input type="text" 
                   class="search-input" 
                   placeholder="Search prompts and metadata..." 
                   data-action="search">
            <button class="search-btn" data-action="search">
              <svg class="icon"><use href="#icon-search"></use></svg>
            </button>
          </div>
          
          <div class="gallery-filters">
            <select class="filter-select" data-filter="rating">
              <option value="">All Ratings</option>
              <option value="5">★★★★★</option>
              <option value="4">★★★★☆</option>
              <option value="3">★★★☆☆</option>
              <option value="2">★★☆☆☆</option>
              <option value="1">★☆☆☆☆</option>
            </select>
            
            <select class="filter-select" data-filter="category">
              <option value="">All Categories</option>
              <option value="portrait">Portrait</option>
              <option value="landscape">Landscape</option>
              <option value="abstract">Abstract</option>
              <option value="architecture">Architecture</option>
              <option value="nature">Nature</option>
            </select>
            
            <input type="date" class="filter-date" data-filter="dateFrom" placeholder="From Date">
            <input type="date" class="filter-date" data-filter="dateTo" placeholder="To Date">
            
            <button class="filter-clear" data-action="clearFilters">Clear Filters</button>
          </div>
          
          <div class="gallery-controls">
            <div class="view-size-controls">
              <button class="size-btn ${this.options.imageSize === 'small' ? 'active' : ''}" 
                      data-size="small" title="Small thumbnails">
                <svg class="icon"><use href="#icon-grid-small"></use></svg>
              </button>
              <button class="size-btn ${this.options.imageSize === 'medium' ? 'active' : ''}" 
                      data-size="medium" title="Medium thumbnails">
                <svg class="icon"><use href="#icon-grid-medium"></use></svg>
              </button>
              <button class="size-btn ${this.options.imageSize === 'large' ? 'active' : ''}" 
                      data-size="large" title="Large thumbnails">
                <svg class="icon"><use href="#icon-grid-large"></use></svg>
              </button>
            </div>
            
            <select class="sort-select" data-sort="sortBy">
              <option value="created_at">Date Created</option>
              <option value="updated_at">Date Modified</option>
              <option value="name">Name</option>
              <option value="rating">Rating</option>
              <option value="file_size">File Size</option>
            </select>
            
            <button class="sort-order-btn" data-action="toggleSortOrder" title="Sort order">
              <svg class="icon"><use href="#icon-sort-${this.sortOrder}"></use></svg>
            </button>
          </div>
          
          <div class="selection-controls" style="display: none;">
            <span class="selection-count">0 selected</span>
            <button class="bulk-action-btn" data-action="bulkDelete">Delete Selected</button>
            <button class="bulk-action-btn" data-action="bulkExport">Export Selected</button>
            <button class="selection-clear-btn" data-action="clearSelection">Clear Selection</button>
          </div>
        </div>
      </div>

      <div class="gallery-content">
        <div class="gallery-grid ${this.options.imageSize}">
          <!-- Gallery items will be inserted here -->
        </div>
        
        <div class="gallery-loading" style="display: none;">
          <div class="loading-spinner"></div>
          <span class="loading-text">Loading images...</span>
        </div>
        
        <div class="gallery-empty" style="display: none;">
          <div class="empty-icon">
            <svg class="icon"><use href="#icon-image"></use></svg>
          </div>
          <h3>No Images Found</h3>
          <p>Try adjusting your search criteria or generate some images first.</p>
        </div>
      </div>

      <div class="gallery-stats">
        <span class="stats-total">0 images</span>
        <span class="stats-selected" style="display: none;">0 selected</span>
        <span class="stats-storage">0 MB total</span>
      </div>
    `;

    // Cache DOM elements
    this.elements = {
      header: this.container.querySelector('.gallery-header'),
      grid: this.container.querySelector('.gallery-grid'),
      loading: this.container.querySelector('.gallery-loading'),
      empty: this.container.querySelector('.gallery-empty'),
      searchInput: this.container.querySelector('.search-input'),
      filterSelects: this.container.querySelectorAll('.filter-select'),
      filterDates: this.container.querySelectorAll('.filter-date'),
      sortSelect: this.container.querySelector('.sort-select'),
      sortOrderBtn: this.container.querySelector('.sort-order-btn'),
      sizeButtons: this.container.querySelectorAll('.size-btn'),
      selectionControls: this.container.querySelector('.selection-controls'),
      selectionCount: this.container.querySelector('.selection-count'),
      statsTotal: this.container.querySelector('.stats-total'),
      statsSelected: this.container.querySelector('.stats-selected'),
      statsStorage: this.container.querySelector('.stats-storage')
    };
  }

  /**
   * Bind event listeners
   */
  bindEvents() {
    // Infinite scroll
    window.addEventListener('scroll', this.throttle(() => {
      this.checkInfiniteScroll();
    }, 100));

    // Search
    this.elements.searchInput.addEventListener('input', this.debounce((e) => {
      this.filters.search = e.target.value;
      this.applyFilters();
    }, 300));

    // Filters
    this.elements.filterSelects.forEach(select => {
      select.addEventListener('change', (e) => {
        const filterType = e.target.dataset.filter;
        this.filters[filterType] = e.target.value || null;
        this.applyFilters();
      });
    });

    this.elements.filterDates.forEach(input => {
      input.addEventListener('change', (e) => {
        const filterType = e.target.dataset.filter;
        this.filters[filterType] = e.target.value || null;
        this.applyFilters();
      });
    });

    // Sorting
    this.elements.sortSelect.addEventListener('change', (e) => {
      this.sortBy = e.target.value;
      this.applySorting();
    });

    this.elements.sortOrderBtn.addEventListener('click', () => {
      this.sortOrder = this.sortOrder === 'asc' ? 'desc' : 'asc';
      this.updateSortOrderIcon();
      this.applySorting();
    });

    // View size controls
    this.elements.sizeButtons.forEach(btn => {
      btn.addEventListener('click', (e) => {
        const size = e.currentTarget.dataset.size;
        this.setImageSize(size);
      });
    });

    // Gallery grid events (event delegation)
    this.elements.grid.addEventListener('click', (e) => {
      this.handleGridClick(e);
    });

    this.elements.grid.addEventListener('dblclick', (e) => {
      this.handleGridDoubleClick(e);
    });

    // Bulk actions
    this.container.addEventListener('click', (e) => {
      const action = e.target.dataset.action;
      if (action) {
        e.preventDefault();
        this.handleAction(action, e);
      }
    });

    // Keyboard shortcuts
    document.addEventListener('keydown', (e) => {
      if (this.container.contains(document.activeElement) || this.hasSelection()) {
        this.handleKeyboard(e);
      }
    });
  }

  /**
   * Load initial items
   */
  async loadInitialItems() {
    this.currentPage = 0;
    this.hasMoreItems = true;
    this.items = [];
    this.filteredItems = [];
    this.clearGrid();
    await this.loadMoreItems();
  }

  /**
   * Load more items from API
   */
  async loadMoreItems() {
    if (this.isLoading || !this.hasMoreItems) return;

    this.isLoading = true;
    this.showLoading();

    try {
      const response = await api.getImages({
        page: this.currentPage,
        limit: this.options.itemsPerPage,
        sort_by: this.sortBy,
        sort_order: this.sortOrder,
        ...this.filters
      });

      const newItems = response.data || [];
      
      if (newItems.length === 0) {
        this.hasMoreItems = false;
      } else {
        this.items.push(...newItems);
        this.filteredItems = [...this.items];
        this.renderNewItems(newItems);
        this.currentPage++;
      }

      this.updateStats();
      this.hideLoading();

    } catch (error) {
      console.error('Failed to load gallery items:', error);
      EventHelpers.error(error, 'Gallery loading');
    } finally {
      this.isLoading = false;
    }
  }

  /**
   * Render new items to grid
   */
  renderNewItems(items) {
    const fragment = document.createDocumentFragment();

    items.forEach(item => {
      const element = this.createGalleryItem(item);
      fragment.appendChild(element);
    });

    this.elements.grid.appendChild(fragment);
    this.updateEmptyState();

    // Lazy load images
    this.lazyLoadImages();
  }

  /**
   * Create gallery item element
   */
  createGalleryItem(item) {
    const element = document.createElement('div');
    element.className = 'gallery-item';
    element.dataset.id = item.id;
    element.dataset.path = item.file_path || item.image_path;

    // Determine media type from item data or file extension
    const mediaType = this.getMediaType(item);
    element.dataset.mediaType = mediaType;

    const isSelected = this.selectedItems.has(item.id);
    if (isSelected) {
      element.classList.add('selected');
    }

    // Create appropriate media element based on type
    let mediaContent = '';
    if (mediaType === 'video') {
      mediaContent = `
        <video class="lazy-media"
               data-src="${item.file_path || item.image_path}"
               poster="${item.thumbnail_url || ''}"
               muted
               loop>
        </video>
        <div class="media-type-indicator">
          <svg class="icon"><use href="#icon-video"></use></svg>
        </div>`;
    } else if (mediaType === 'audio') {
      mediaContent = `
        <div class="audio-placeholder">
          <svg class="icon icon-large"><use href="#icon-audio"></use></svg>
          <span class="audio-filename">${item.filename || 'Audio File'}</span>
        </div>
        <div class="media-type-indicator">
          <svg class="icon"><use href="#icon-audio"></use></svg>
        </div>`;
    } else {
      // Default image handling
      mediaContent = `
        <img data-src="${item.thumbnail_url || item.file_path || item.image_path}"
             alt="${item.prompt_text?.slice(0, 100) || 'Generated media'}"
             class="lazy-image"
             loading="lazy">`;
    }

    element.innerHTML = `
      <div class="item-container">
        <div class="item-image">
          ${mediaContent}
          <div class="image-overlay">
            <div class="overlay-controls">
              <button class="overlay-btn" data-action="preview" title="Preview">
                <svg class="icon"><use href="#icon-eye"></use></svg>
              </button>
              <button class="overlay-btn" data-action="metadata" title="View metadata">
                <svg class="icon"><use href="#icon-info"></use></svg>
              </button>
              <button class="overlay-btn" data-action="download" title="Download">
                <svg class="icon"><use href="#icon-download"></use></svg>
              </button>
            </div>
          </div>
        </div>
        
        ${this.options.showMetadata ? `
          <div class="item-metadata">
            <div class="item-title" title="${item.prompt_text || 'Untitled'}">
              ${this.truncateText(item.prompt_text || 'Untitled', 50)}
            </div>
            <div class="item-details">
              <span class="item-date">${this.formatDate(item.created_at)}</span>
              <span class="item-size">${this.formatFileSize(item.file_size)}</span>
              ${item.rating ? `<span class="item-rating">${this.formatRating(item.rating)}</span>` : ''}
            </div>
          </div>
        ` : ''}
        
        ${this.options.enableSelection ? `
          <div class="item-selection">
            <input type="checkbox" 
                   class="selection-checkbox" 
                   ${isSelected ? 'checked' : ''}
                   data-action="select">
          </div>
        ` : ''}
      </div>
    `;

    return element;
  }

  /**
   * Lazy load images using Intersection Observer
   */
  lazyLoadImages() {
    if (!this.imageObserver) {
      this.imageObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
          if (entry.isIntersecting) {
            const element = entry.target;
            const src = element.dataset.src;

            if (src) {
              // Handle video elements
              if (element.tagName === 'VIDEO') {
                element.src = src;
                element.classList.add('loading');

                element.onloadeddata = () => {
                  element.classList.remove('loading');
                  element.classList.add('loaded');
                  // Auto-play on hover
                  element.parentElement.addEventListener('mouseenter', () => element.play());
                  element.parentElement.addEventListener('mouseleave', () => {
                    element.pause();
                    element.currentTime = 0;
                  });
                };

                element.onerror = () => {
                  element.classList.remove('loading');
                  element.classList.add('error');
                };
              }
              // Handle image elements
              else {
                element.src = src;
                element.classList.add('loading');

                element.onload = () => {
                  element.classList.remove('loading');
                  element.classList.add('loaded');
                };

                element.onerror = () => {
                  element.classList.remove('loading');
                  element.classList.add('error');
                  element.src = '/web/images/placeholder.png';
                };
              }

              this.imageObserver.unobserve(element);
            }
          }
        });
      }, {
        rootMargin: '50px'
      });
    }

    // Observe all lazy images and videos
    const lazyMedia = this.elements.grid.querySelectorAll('.lazy-image:not(.loaded):not(.loading), .lazy-media:not(.loaded):not(.loading)');
    lazyMedia.forEach(media => this.imageObserver.observe(media));
  }

  /**
   * Check if should load more items (infinite scroll)
   */
  checkInfiniteScroll() {
    if (this.isLoading || !this.hasMoreItems) return;

    const scrollTop = window.pageYOffset;
    const windowHeight = window.innerHeight;
    const documentHeight = document.documentElement.scrollHeight;

    if (scrollTop + windowHeight >= documentHeight - this.options.loadThreshold) {
      this.loadMoreItems();
    }
  }

  /**
   * Apply filters to items
   */
  async applyFilters() {
    this.currentPage = 0;
    this.hasMoreItems = true;
    this.clearGrid();
    await this.loadMoreItems();
  }

  /**
   * Apply sorting to items
   */
  async applySorting() {
    this.currentPage = 0;
    this.hasMoreItems = true;
    this.clearGrid();
    await this.loadMoreItems();
  }

  /**
   * Set image size
   */
  setImageSize(size) {
    this.options.imageSize = size;
    
    // Update grid class
    this.elements.grid.className = `gallery-grid ${size}`;
    
    // Update button states
    this.elements.sizeButtons.forEach(btn => {
      btn.classList.toggle('active', btn.dataset.size === size);
    });

    // Force re-layout
    this.lazyLoadImages();
  }

  /**
   * Handle grid clicks
   */
  handleGridClick(e) {
    const item = e.target.closest('.gallery-item');
    if (!item) return;

    const action = e.target.dataset.action;
    const itemId = item.dataset.id;

    switch (action) {
      case 'select':
        this.toggleSelection(itemId, item);
        break;
      case 'preview':
        this.previewItem(itemId);
        break;
      case 'metadata':
        this.showMetadata(itemId);
        break;
      case 'download':
        this.downloadItem(itemId);
        break;
      default:
        // Default click behavior
        if (this.options.enableSelection && (e.ctrlKey || e.metaKey)) {
          this.toggleSelection(itemId, item);
        } else {
          this.previewItem(itemId);
        }
    }
  }

  /**
   * Handle grid double clicks
   */
  handleGridDoubleClick(e) {
    const item = e.target.closest('.gallery-item');
    if (!item) return;

    const itemId = item.dataset.id;
    this.openItemEditor(itemId);
  }

  /**
   * Handle keyboard shortcuts
   */
  handleKeyboard(e) {
    switch (e.code) {
      case 'KeyA':
        if (e.ctrlKey || e.metaKey) {
          e.preventDefault();
          this.selectAll();
        }
        break;
      case 'Escape':
        this.clearSelection();
        break;
      case 'Delete':
        if (this.hasSelection()) {
          this.bulkDelete();
        }
        break;
      case 'KeyF':
        if (e.ctrlKey || e.metaKey) {
          e.preventDefault();
          this.elements.searchInput.focus();
        }
        break;
    }
  }

  /**
   * Toggle item selection
   */
  toggleSelection(itemId, element) {
    if (this.selectedItems.has(itemId)) {
      this.selectedItems.delete(itemId);
      element.classList.remove('selected');
      element.querySelector('.selection-checkbox').checked = false;
    } else {
      this.selectedItems.add(itemId);
      element.classList.add('selected');
      element.querySelector('.selection-checkbox').checked = true;
    }

    this.updateSelectionUI();
  }

  /**
   * Select all items
   */
  selectAll() {
    this.elements.grid.querySelectorAll('.gallery-item').forEach(element => {
      const itemId = element.dataset.id;
      this.selectedItems.add(itemId);
      element.classList.add('selected');
      const checkbox = element.querySelector('.selection-checkbox');
      if (checkbox) checkbox.checked = true;
    });

    this.updateSelectionUI();
  }

  /**
   * Clear selection
   */
  clearSelection() {
    this.selectedItems.clear();
    
    this.elements.grid.querySelectorAll('.gallery-item.selected').forEach(element => {
      element.classList.remove('selected');
      const checkbox = element.querySelector('.selection-checkbox');
      if (checkbox) checkbox.checked = false;
    });

    this.updateSelectionUI();
  }

  /**
   * Update selection UI
   */
  updateSelectionUI() {
    const count = this.selectedItems.size;
    const hasSelection = count > 0;

    // Update selection controls visibility
    this.elements.selectionControls.style.display = hasSelection ? 'flex' : 'none';
    this.elements.selectionCount.textContent = `${count} selected`;
    this.elements.statsSelected.textContent = `${count} selected`;
    this.elements.statsSelected.style.display = hasSelection ? 'inline' : 'none';

    // Emit selection change event
    events.emit(AppEvents.SELECTION_CHANGE, {
      selectedItems: Array.from(this.selectedItems),
      count
    });
  }

  /**
   * Update statistics
   */
  updateStats() {
    const totalCount = this.items.length;
    const totalSize = this.items.reduce((sum, item) => sum + (item.file_size || 0), 0);

    this.elements.statsTotal.textContent = `${totalCount} ${totalCount === 1 ? 'image' : 'images'}`;
    this.elements.statsStorage.textContent = this.formatFileSize(totalSize);
  }

  /**
   * Update empty state
   */
  updateEmptyState() {
    const isEmpty = this.elements.grid.children.length === 0;
    this.elements.empty.style.display = isEmpty ? 'flex' : 'none';
    this.elements.grid.style.display = isEmpty ? 'none' : 'grid';
  }

  /**
   * Show loading state
   */
  showLoading() {
    this.elements.loading.style.display = 'flex';
  }

  /**
   * Hide loading state
   */
  hideLoading() {
    this.elements.loading.style.display = 'none';
  }

  /**
   * Clear grid
   */
  clearGrid() {
    this.elements.grid.innerHTML = '';
    this.updateEmptyState();
  }

  /**
   * Update sort order icon
   */
  updateSortOrderIcon() {
    const useElement = this.elements.sortOrderBtn.querySelector('use');
    useElement.setAttribute('href', `#icon-sort-${this.sortOrder}`);
  }

  /**
   * Check if has selection
   */
  hasSelection() {
    return this.selectedItems.size > 0;
  }

  /**
   * Handle various actions
   */
  async handleAction(action, event) {
    switch (action) {
      case 'search':
        this.applyFilters();
        break;
      case 'clearFilters':
        this.clearFilters();
        break;
      case 'toggleSortOrder':
        // Handled in bindEvents
        break;
      case 'bulkDelete':
        await this.bulkDelete();
        break;
      case 'bulkExport':
        await this.bulkExport();
        break;
      case 'clearSelection':
        this.clearSelection();
        break;
    }
  }

  /**
   * Clear all filters
   */
  clearFilters() {
    this.filters = {
      search: '',
      dateRange: null,
      rating: null,
      category: null
    };

    // Reset form elements
    this.elements.searchInput.value = '';
    this.elements.filterSelects.forEach(select => select.value = '');
    this.elements.filterDates.forEach(input => input.value = '');

    this.applyFilters();
  }

  /**
   * Preview item
   */
  previewItem(itemId) {
    const item = this.items.find(i => i.id === itemId);
    if (!item) return;

    events.emit(AppEvents.IMAGE_SELECT, item);
  }

  /**
   * Show metadata for item
   */
  showMetadata(itemId) {
    const item = this.items.find(i => i.id === itemId);
    if (!item) return;

    events.emit('metadata:show', item);
  }

  /**
   * Download item
   */
  async downloadItem(itemId) {
    try {
      const item = this.items.find(i => i.id === itemId);
      if (!item) return;

      const response = await api.get(`/images/${itemId}/download`);
      
      // Create download link
      const link = document.createElement('a');
      link.href = response.url;
      link.download = item.filename || `image_${itemId}.png`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);

    } catch (error) {
      EventHelpers.error(error, 'Download failed');
    }
  }

  /**
   * Open item editor
   */
  openItemEditor(itemId) {
    const item = this.items.find(i => i.id === itemId);
    if (!item) return;

    events.emit('editor:open', item);
  }

  /**
   * Bulk delete selected items
   */
  async bulkDelete() {
    if (!this.hasSelection()) return;

    const confirmed = confirm(`Delete ${this.selectedItems.size} selected images?`);
    if (!confirmed) return;

    try {
      await api.bulkDeleteImages(Array.from(this.selectedItems));
      
      // Remove items from DOM and state
      this.selectedItems.forEach(itemId => {
        const element = this.elements.grid.querySelector(`[data-id="${itemId}"]`);
        if (element) element.remove();
        
        const itemIndex = this.items.findIndex(i => i.id === itemId);
        if (itemIndex > -1) this.items.splice(itemIndex, 1);
      });

      this.selectedItems.clear();
      this.updateSelectionUI();
      this.updateStats();
      this.updateEmptyState();

      EventHelpers.notify(`Deleted ${this.selectedItems.size} images`, 'success');

    } catch (error) {
      EventHelpers.error(error, 'Bulk delete failed');
    }
  }

  /**
   * Bulk export selected items
   */
  async bulkExport() {
    if (!this.hasSelection()) return;

    try {
      const response = await api.post('/images/bulk-export', {
        image_ids: Array.from(this.selectedItems)
      });

      // Download export file
      const link = document.createElement('a');
      link.href = response.download_url;
      link.download = 'exported_images.zip';
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);

      EventHelpers.notify(`Exported ${this.selectedItems.size} images`, 'success');

    } catch (error) {
      EventHelpers.error(error, 'Bulk export failed');
    }
  }

  // Utility methods
  truncateText(text, maxLength) {
    return text.length > maxLength ? text.slice(0, maxLength) + '...' : text;
  }

  formatDate(dateString) {
    return new Date(dateString).toLocaleDateString();
  }

  formatFileSize(bytes) {
    if (!bytes) return '0 B';
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${sizes[i]}`;
  }

  formatRating(rating) {
    return '★'.repeat(rating) + '☆'.repeat(5 - rating);
  }

  getMediaType(item) {
    // Check if media_type is provided in the item data
    if (item.media_type) {
      return item.media_type;
    }

    // Fall back to file extension detection
    const path = item.file_path || item.image_path || item.filename || '';
    const extension = path.split('.').pop()?.toLowerCase();

    // Video formats
    if (['mp4', 'avi', 'mov', 'webm', 'mkv'].includes(extension)) {
      return 'video';
    }

    // Audio formats
    if (['wav', 'mp3', 'ogg', 'flac', 'aac', 'm4a'].includes(extension)) {
      return 'audio';
    }

    // Default to image
    return 'image';
  }

  throttle(func, delay) {
    let timeoutId;
    let lastExecTime = 0;
    return (...args) => {
      const currentTime = Date.now();
      if (currentTime - lastExecTime > delay) {
        func(...args);
        lastExecTime = currentTime;
      } else {
        clearTimeout(timeoutId);
        timeoutId = setTimeout(() => {
          func(...args);
          lastExecTime = Date.now();
        }, delay - (currentTime - lastExecTime));
      }
    };
  }

  debounce(func, delay) {
    let timeoutId;
    return (...args) => {
      clearTimeout(timeoutId);
      timeoutId = setTimeout(() => func(...args), delay);
    };
  }

  /**
   * Refresh gallery
   */
  async refresh() {
    await this.loadInitialItems();
  }

  /**
   * Destroy gallery and cleanup
   */
  destroy() {
    if (this.imageObserver) {
      this.imageObserver.disconnect();
    }
    
    window.removeEventListener('scroll', this.checkInfiniteScroll);
    this.container.innerHTML = '';
  }
}

// Export gallery class
window.Gallery = Gallery;
export default Gallery;