/**
 * Dashboard Page JavaScript
 * Handles all dashboard page functionality with proper separation of concerns
 */

(function() {
  'use strict';

  if (typeof window === 'undefined') {
    return;
  }
  const pmActive = (window.location?.pathname || '').includes('/prompt_manager');
  if (!pmActive) {
    console.info('[PromptManager] dashboard-page skipped outside PromptManager UI context');
    return;
  }

  // State management
  let currentModel = 'flux';
  let selectedPrompts = new Set();
  let apiBase = null;
  let allPrompts = [];
  let currentPromptList = [];
  let searchTerm = '';
  let searchTermLower = '';
  let editingPromptId = null;
  let searchDebounceTimer = null;
  let currentSortOrder = 'newest'; // Default sort order
  let currentTagFilter = []; // Array of selected tag names
  let currentModelFilter = 'all';
  let currentCategoryFilter = 'all';

  const API_BASE_CANDIDATES = ['/api/v1', '/api/prompt_manager'];
  const DEFAULT_EMPTY_MESSAGE = 'Start by creating a new prompt or importing existing ones';
  const DEFAULT_FILM_STRIP_SIZE = 'medium';
  const FILM_STRIP_IMAGE_LIMIT_BY_SIZE = { small: 4, medium: 6, large: 8 };
  const PROMPT_GALLERY_IMAGE_LIMIT = 24;
  const DEFAULT_BLUR_TAGS = ['nsfw'];

  const paginationState = {
    page: 1,
    limit: getItemsPerPageSetting(),
    total: 0,
    totalPages: 1,
  };

  let filmStripSize = getFilmStripSizeSetting();
  let blurConfig = getBlurSettings();
  const selectionControls = {
    selectAllButton: null,
    selectAllCheckbox: null,
    deleteButton: null,
    addTagsButton: null,
    addCollectionsButton: null,
    exportButton: null,
    countLabel: null,
  };
  let addTagsDraft = [];
  const promptGalleryState = {
    promptId: null,
    images: [],
    integrationId: null,
    activeIndex: 0,
  };
  let promptGalleryIntegrationId = null;
  let viewerIntegrationReady = false;

  /**
   * Initialize dashboard page
   */
  async function init() {
    attachEventListeners();
    setupModalHandlers();
    registerSettingsListener();
    paginationState.limit = getItemsPerPageSetting();
    filmStripSize = getFilmStripSizeSetting();
    switchModel(currentModel);

    // Note: Dashboard now uses ViewerManager directly instead of ViewerIntegration
    // This avoids container conflicts and provides more reliable modal display

    if (window.MigrationService) {
      window.MigrationService.init().catch((error) => {
        console.error('Migration init failed:', error);
      });
    } else {
      console.warn('MigrationService not available on dashboard load');
    }

    // Check for missing thumbnails and prompt user if needed
    checkForMissingThumbnails();

    try {
      await loadDashboardData();
    } catch (error) {
      console.error('Failed to initialise dashboard data:', error);
    }
  }

  /**
   * Check for missing thumbnails and store result for modal
   */
  async function checkForMissingThumbnails() {
    try {
      // Clear any stale sessionStorage data before starting new scan
      sessionStorage.removeItem('thumbnailScanResult');
      console.log('[ThumbnailScan] Cleared stale sessionStorage before scan');

      // Fetch user's enabled thumbnail sizes from settings
      let enabledSizes = ['small', 'medium']; // Default fallback (most common sizes)
      try {
        const settingsResponse = await fetch('/api/v1/settings/thumbnails');
        if (settingsResponse.ok) {
          const settings = await settingsResponse.json();
          if (settings.enabled_sizes && settings.enabled_sizes.length > 0) {
            enabledSizes = settings.enabled_sizes;
            console.log('[ThumbnailScan] Using configured sizes:', enabledSizes);
          } else {
            console.warn('[ThumbnailScan] No enabled_sizes in settings, using default:', enabledSizes);
          }
        } else {
          console.warn('[ThumbnailScan] Failed to fetch settings (HTTP', settingsResponse.status, '), using default:', enabledSizes);
        }
      } catch (settingsError) {
        console.warn('[ThumbnailScan] Error fetching thumbnail settings, using defaults:', settingsError);
      }

      const response = await fetch('/api/v1/thumbnails/scan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sizes: enabledSizes }),
      });

      if (!response.ok) {
        console.warn('Thumbnail scan failed:', response.status);
        return;
      }

      const result = await response.json();
      console.log('Thumbnail scan result:', result);

      // Debug: Show which images are missing thumbnails
      if (result.missing_images && result.missing_images.length > 0) {
        console.log('Missing thumbnail images:', result.missing_images);
      }

      // Changed threshold from 10 to 0 to show prompt for ANY missing thumbnails
      if (result.missing_count > 0) {
        console.log(`Found ${result.missing_count} images without thumbnails`);
        // Store in sessionStorage for the thumbnail modal to pick up
        // Use total_operations from scan result directly
        const scanData = {
          missing_count: result.missing_count,
          total_operations: result.total_operations,
          scanned_at: new Date().toISOString(),
        };
        sessionStorage.setItem('thumbnailScanResult', JSON.stringify(scanData));

        // Trigger modal directly if it exists and is initialized
        if (window.thumbnailModal && window.thumbnailModal.initialized) {
          console.log('[ThumbnailScan] Triggering modal directly with scan result');
          window.thumbnailModal.missingCount = result.missing_count;
          window.thumbnailModal.showMissingThumbnailsPrompt();
        } else {
          console.log('[ThumbnailScan] Modal not ready yet, relying on sessionStorage auto-detection');
        }
      } else {
        console.log('All images have thumbnails');
        // sessionStorage already cleared at the start
      }
    } catch (error) {
      console.warn('Failed to scan for thumbnails:', error);
      // Don't block dashboard loading for thumbnail issues
    }
  }

  /**
   * Attach event listeners to elements
   */
  function attachEventListeners() {
    // Mobile menu toggle
    const mobileMenuToggle = document.querySelector('.mobile-menu-toggle');
    if (mobileMenuToggle) {
      mobileMenuToggle.addEventListener('click', toggleMobileMenu);
    }

    // Add prompt buttons
    document.querySelectorAll('[data-action="add-prompt"]').forEach(btn => {
      btn.addEventListener('click', showAddPromptModal);
    });

    // Import/Export buttons
    document.querySelectorAll('[data-action="import-prompts"]').forEach((btn) => {
      btn.addEventListener('click', importPrompts);
    });

    document.querySelectorAll('[data-action="export-prompts"]').forEach((btn) => {
      btn.addEventListener('click', exportPrompts);
    });

    selectionControls.selectAllButton = document.querySelector('[data-action="select-all"]');
    selectionControls.selectAllCheckbox = document.querySelector('.select-all-checkbox');
    selectionControls.deleteButton = document.querySelector('[data-action="delete-selected"]');
    selectionControls.addTagsButton = document.querySelector('[data-action="add-tags"]');
    selectionControls.addCollectionsButton = document.querySelector('[data-action="add-collections"]');
    selectionControls.exportButton = document.querySelector('[data-action="export-selected"]');
    selectionControls.countLabel = document.getElementById('selectionCount');

    selectionControls.selectAllCheckbox?.addEventListener('change', handleSelectAllToggle);
    selectionControls.deleteButton?.addEventListener('click', openDeleteSelectedModal);
    selectionControls.addTagsButton?.addEventListener('click', openAddTagsModal);
    selectionControls.exportButton?.addEventListener('click', exportSelectedPrompts);

    document.querySelectorAll('[data-action="close-edit-modal"]').forEach((btn) => {
      btn.addEventListener('click', hideEditPromptModal);
    });

    document.querySelectorAll('[data-action="cancel-edit-modal"]').forEach((btn) => {
      btn.addEventListener('click', hideEditPromptModal);
    });

    document.querySelector('[data-action="close-delete-modal"]')?.addEventListener('click', closeDeleteSelectedModal);
    document.querySelector('[data-action="cancel-delete-modal"]')?.addEventListener('click', closeDeleteSelectedModal);
    document.querySelector('[data-action="confirm-delete-selected"]')?.addEventListener('click', confirmDeleteSelected);

    document.querySelector('[data-action="close-add-tags-modal"]')?.addEventListener('click', closeAddTagsModal);
    document.querySelector('[data-action="cancel-add-tags-modal"]')?.addEventListener('click', closeAddTagsModal);
    document.querySelector('[data-action="add-tag-token"]')?.addEventListener('click', () => {
      const input = document.getElementById('addTagInput');
      if (!input) return;
      handleAddTagInput(input.value);
      input.value = '';
      input.focus();
    });
    document.querySelector('[data-action="apply-add-tags"]')?.addEventListener('click', applyAddTagsToSelection);

    document.querySelectorAll('[data-action="save-edit-prompt"]').forEach((btn) => {
      btn.addEventListener('click', (event) => {
        event.preventDefault();
        saveEditPrompt();
      });
    });

    const searchInput = document.getElementById('searchInput');
    if (searchInput) {
      searchInput.addEventListener('input', (event) => {
        handleSearchInput(event.target.value);
      });
    }

    // Filter bar event listeners
    const sortBySelect = document.getElementById('sortBySelect');
    if (sortBySelect) {
      sortBySelect.addEventListener('change', (event) => {
        currentSortOrder = event.target.value;
        loadPrompts(1); // Reset to page 1 when filter changes
      });
    }

    const modelFilterSelect = document.getElementById('modelFilterSelect');
    if (modelFilterSelect) {
      modelFilterSelect.addEventListener('change', (event) => {
        currentModelFilter = event.target.value;
        loadPrompts(1); // Reset to page 1 when filter changes
      });
    }

    const categoryFilterSelect = document.getElementById('categoryFilterSelect');
    if (categoryFilterSelect) {
      categoryFilterSelect.addEventListener('change', (event) => {
        currentCategoryFilter = event.target.value;
        loadPrompts(1); // Reset to page 1 when filter changes
      });
    }

    // Initialize multi-tag filter component
    if (window.MultiTagFilter) {
      window.MultiTagFilter.init('multiTagFilterContainer', {
        apiEndpoint: '/api/v1/tags',
        placeholder: 'Search tags...',
        externalPillsContainer: 'activeFiltersPills',
      }).then((success) => {
        if (success) {
          // Listen for tag selection changes and apply filters immediately
          window.MultiTagFilter.onChange((data) => {
            currentTagFilter = data.tags;
            loadPrompts(1); // Reset to page 1 when filter changes
          });
        } else {
          console.error('Failed to initialize MultiTagFilter');
        }
      }).catch((error) => {
        console.error('Error initializing MultiTagFilter:', error);
      });
    }

    const addTagInput = document.getElementById('addTagInput');
    addTagInput?.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        handleAddTagInput(event.target.value);
        event.target.value = '';
      }
    });

    addTagInput?.addEventListener('input', (event) => {
      const value = event.target.value;
      if (value.includes(',')) {
        const parts = value.split(',');
        const lastPart = parts.pop();
        parts.forEach((part) => handleAddTagInput(part));
        event.target.value = lastPart ?? '';
      }
      renderTagSuggestions(event.target.value);
    });

    const deleteSelectedModal = document.getElementById('deleteSelectedModal');
    deleteSelectedModal?.addEventListener('click', (event) => {
      if (event.target === deleteSelectedModal) {
        closeDeleteSelectedModal();
      }
    });

    const addTagsModal = document.getElementById('addTagsModal');
    addTagsModal?.addEventListener('click', (event) => {
      if (event.target === addTagsModal) {
        closeAddTagsModal();
      }
    });

    document.getElementById('addTagsList')?.addEventListener('click', (event) => {
      const button = event.target.closest('button[data-tag]');
      if (!button) return;
      removeAddTag(button.dataset.tag);
    });

    document.getElementById('tagSuggestions')?.addEventListener('click', (event) => {
      const chip = event.target.closest('.tag-suggestion-chip[data-tag]');
      if (!chip) return;
      handleAddTagInput(chip.dataset.tag);
      const input = document.getElementById('addTagInput');
      if (input) {
        input.value = '';
        input.focus();
      }
      renderTagSuggestions('');
    });

    updateSelectionUI();

    // Create modal close/cancel
    document.querySelector('#createPromptModal .modal-close')?.addEventListener('click', hideAddPromptModal);

    // Modal cancel button
    document.querySelectorAll('[data-action="cancel-modal"]').forEach((button) => {
      button.addEventListener('click', hideAddPromptModal);
    });

    // Modal submit button
    document.querySelectorAll('[data-action="create-prompt"]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        createPrompt(event);
      });
    });

    const promptForm = document.getElementById('promptForm');
    if (promptForm) {
      promptForm.addEventListener('submit', createPrompt);
    }

    const editPromptForm = document.getElementById('editPromptForm');
    if (editPromptForm) {
      editPromptForm.addEventListener('submit', (event) => {
        event.preventDefault();
        saveEditPrompt();
      });
    }

    // Model tabs
    document.querySelectorAll('.model-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        const model = tab.dataset.model;
        if (model) {
          switchModel(model);
        }
      });
    });
  }

  /**
   * Setup prompt card interactions
   */
  function setupPromptCards() {
    // Copy to clipboard for prompt content
    document.querySelectorAll('.prompt-content').forEach(element => {
      if (element.dataset.clipboardBound === 'true') {
        return;
      }
      element.addEventListener('click', function() {
        const type = this.classList.contains('prompt-positive') ? 'positive' : 'negative';
        copyToClipboard(this, type);
      });
      element.dataset.clipboardBound = 'true';
    });
  }

  /**
   * Setup modal handlers
   */
  function setupModalHandlers() {
    // Close modal on backdrop click
    const createPromptModal = document.getElementById('createPromptModal');
    if (createPromptModal) {
      createPromptModal.addEventListener('click', (event) => {
        if (event.target === createPromptModal) {
          hideAddPromptModal();
        }
      });

      const modalContainer = createPromptModal.querySelector('.modal-container');
      modalContainer?.addEventListener('click', (event) => event.stopPropagation());
    }

    const editPromptModal = document.getElementById('editPromptModal');
    if (editPromptModal) {
      editPromptModal.addEventListener('click', (event) => {
        if (event.target === editPromptModal) {
          hideEditPromptModal();
        }
      });

      const editModalContainer = editPromptModal.querySelector('.modal-container');
      editModalContainer?.addEventListener('click', (event) => event.stopPropagation());
    }

    const galleryOverlay = document.getElementById('promptGalleryOverlay');
    if (galleryOverlay) {
      galleryOverlay.addEventListener('click', (event) => {
        if (event.target === galleryOverlay) {
          hidePromptGallery();
        }
      });

      galleryOverlay.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
          event.preventDefault();
          hidePromptGallery();
        }
      });
    }

    const galleryCloseButton = document.querySelector('[data-action="close-gallery"]');
    if (galleryCloseButton) {
      galleryCloseButton.addEventListener('click', hidePromptGallery);
    }

    document.addEventListener('keydown', (event) => {
      if (event.key === '/' && !event.metaKey && !event.ctrlKey && !event.altKey) {
        const target = event.target;
        const isTypingTarget = target && (
          target instanceof HTMLInputElement ||
          target instanceof HTMLTextAreaElement ||
          target instanceof HTMLSelectElement ||
          target?.isContentEditable
        );
        if (!isTypingTarget && !isCreatePromptModalOpen() && !isEditPromptModalOpen() && !isDeleteModalOpen() && !isAddTagsModalOpen() && !isGalleryOpen()) {
          event.preventDefault();
          focusSearchInput();
          return;
        }
      }

      if (event.key === 'Escape') {
        if (isGalleryOpen()) {
          hidePromptGallery();
        }
        if (isCreatePromptModalOpen()) {
          hideAddPromptModal();
          return;
        }
        if (isEditPromptModalOpen()) {
          hideEditPromptModal();
          return;
        }
        if (isDeleteModalOpen()) {
          closeDeleteSelectedModal();
          return;
        }
        if (isAddTagsModalOpen()) {
          closeAddTagsModal();
        }
      }
    });
  }

  async function fetchJson(path, options = {}) {
    const method = (options.method || 'GET').toUpperCase();

    let candidates;
    if (apiBase) {
      candidates = [apiBase];
      if (method === 'GET') {
        candidates = [apiBase, ...API_BASE_CANDIDATES.filter(candidate => candidate !== apiBase)];
      }
    } else if (method === 'GET') {
      candidates = [...API_BASE_CANDIDATES];
    } else {
      candidates = [API_BASE_CANDIDATES[0]];
    }

    let lastError = null;
    for (const candidate of candidates) {
      const url = `${candidate}${path}`;
      try {
        const response = await fetch(url, options);
        if (!response.ok) {
          let errorMessage = `${response.status} ${response.statusText}`;
          try {
            const errorBody = await response.json();
            const serverMessage = errorBody?.error || errorBody?.message;
            if (serverMessage) {
              errorMessage = `${response.status} ${serverMessage}`;
            }
          } catch (parseError) {
            try {
              const text = await response.text();
              if (text) {
                errorMessage = `${response.status} ${text}`;
              }
            } catch (textError) {
              // ignore parsing issues
            }
          }
          lastError = new Error(errorMessage);
          if (method !== 'GET') {
            break;
          }
          continue;
        }
        const payload = await response.json();
        apiBase = candidate;
        return payload;
      } catch (error) {
        lastError = error;
      }
    }
    throw lastError || new Error(`Unable to reach PromptManager API (${path})`);
  }

  async function loadDashboardData() {
    setLoading(true);
    try {
      await Promise.all([
        loadStats(),
        loadPrompts(1),
        loadCategories(),
      ]);
    } catch (error) {
      console.error('Failed to load dashboard data:', error);
      showEmptyState(true, error);
      if (window.NotificationService) {
        window.NotificationService.show('Failed to load dashboard data', 'error');
      }
      throw error;
    } finally {
      setLoading(false);
    }
  }

  async function loadCategories() {
    try {
      const payload = await fetchJson('/prompts/categories');
      const categoriesData = Array.isArray(payload?.data) ? payload.data : [];

      // Populate filter dropdown
      const categorySelect = document.getElementById('categoryFilterSelect');
      if (categorySelect && categoriesData.length > 0) {
        const currentValue = categorySelect.value;
        categorySelect.innerHTML = '<option value="all">All Categories</option>';
        categoriesData.forEach(item => {
          // Handle both string format and object format {category: "name", count: 123}
          const categoryName = typeof item === 'string' ? item : (item.category || item.name);
          if (categoryName && categoryName !== '') {
            const option = document.createElement('option');
            option.value = categoryName;
            option.textContent = categoryName;
            categorySelect.appendChild(option);
          }
        });
        categorySelect.value = currentValue;
      }

      // Populate datalist for autocomplete in create/edit modals
      const categoryDatalist = document.getElementById('categoryDatalist');
      if (categoryDatalist) {
        categoryDatalist.innerHTML = '';
        categoriesData.forEach(item => {
          const categoryName = typeof item === 'string' ? item : (item.category || item.name);
          if (categoryName && categoryName !== '') {
            const option = document.createElement('option');
            option.value = categoryName;
            categoryDatalist.appendChild(option);
          }
        });
      }
    } catch (error) {
      console.warn('Failed to load categories:', error);
      // Non-critical error, don't throw
    }
  }

  async function loadStats() {
    try {
      // Try to fetch epic stats first
      const epicPayload = await fetchJson('/stats/epic');
      if (epicPayload?.success && epicPayload?.data) {
        const epicStats = epicPayload.data;
        // Transform epic stats to match dashboard format
        const stats = {
          prompt_count: epicStats.hero_stats?.total_prompts || 0,
          rated_count: epicStats.hero_stats?.rated_count || 0,
          avg_rating: epicStats.hero_stats?.avg_rating || 0,
          tag_count: 0, // Will be fetched from system stats
          total_images: epicStats.hero_stats?.total_images || 0,
          image_count: epicStats.hero_stats?.total_images || 0,
          top_categories: [], // Will be fetched from system stats
          // Add epic-specific data
          five_star_count: epicStats.hero_stats?.five_star_count || 0,
          generation_streak: epicStats.hero_stats?.generation_streak || 0,
          images_per_prompt: epicStats.hero_stats?.images_per_prompt || 0,
          total_collections: epicStats.hero_stats?.total_collections || 0,
          generation_analytics: epicStats.generation_analytics,
          quality_metrics: epicStats.quality_metrics,
        };

        // Also fetch system stats for missing data (tags, categories)
        try {
          const sysPayload = await fetchJson('/system/stats');
          const sysStats = sysPayload?.data ?? sysPayload ?? {};
          stats.tag_count = sysStats.tag_count || 0;
          stats.top_categories = sysStats.top_categories || [];
        } catch (sysError) {
          console.log('System stats not available, using defaults', sysError);
        }

        renderStats(stats);
        renderHeroStats(epicStats);
        return stats;
      }
    } catch (epicError) {
      console.log('Epic stats not available, falling back to system stats', epicError);
    }

    // Fallback to system stats
    const statsPayload = await fetchJson('/system/stats');
    const stats = statsPayload?.data ?? statsPayload ?? {};
    renderStats(stats);
    return stats;
  }

  async function loadPrompts(page = paginationState.page) {
    const requestedPage = Math.max(1, Number(page) || 1);
    const limit = Math.max(1, getItemsPerPageSetting());
    paginationState.limit = limit;
    selectedPrompts.clear();
    filmStripSize = getFilmStripSizeSetting();
    blurConfig = getBlurSettings();
    setPromptListLoading(true);

    const params = new URLSearchParams({
      page: String(requestedPage),
      limit: String(limit),
      include_images: '1',
      image_limit: String(getFilmStripImageLimit()),
    });

    // Add search parameter if present
    if (searchTerm) {
      params.append('search', searchTerm);
    }

    // Add category filter if not 'all'
    if (currentCategoryFilter && currentCategoryFilter !== 'all') {
      params.append('category', currentCategoryFilter);
    }

    // Add multi-tag AND filter
    if (Array.isArray(currentTagFilter) && currentTagFilter.length > 0) {
      // Send tags as comma-separated list for AND filtering
      params.append('tags', currentTagFilter.join(','));
    }

    // Add sort order parameters (v1 API uses order_by for field and order_dir for direction)
    const sortConfig = {
      'newest': { field: 'id', dir: 'desc' },
      'oldest': { field: 'id', dir: 'asc' },
      'most-used': { field: 'usage_count', dir: 'desc' },
      'highest-rated': { field: 'rating', dir: 'desc' }
    };
    const config = sortConfig[currentSortOrder] || sortConfig['newest'];
    params.append('order_by', config.field);
    params.append('order_dir', config.dir);

    try {
      const promptsPayload = await fetchJson(`/prompts?${params.toString()}`);
      console.log('[loadPrompts] Raw response:', promptsPayload);

      const prompts = Array.isArray(promptsPayload?.data)
        ? promptsPayload.data
        : Array.isArray(promptsPayload)
          ? promptsPayload
          : [];

      console.log('[loadPrompts] Extracted prompts:', prompts);
      console.log('[loadPrompts] Number of prompts:', prompts.length);

      const pagination = promptsPayload?.pagination ?? {};
      paginationState.page = Number(pagination.page) || requestedPage;
      paginationState.limit = Number(pagination.limit) || limit;
      paginationState.total = Number(pagination.total) || prompts.length;
      paginationState.totalPages = Number(pagination.total_pages)
        || Math.max(1, Math.ceil(paginationState.total / paginationState.limit));

      allPrompts = prompts;
      currentPromptList = prompts; // No more client-side filtering
      console.log('[loadPrompts] Rendering prompts:', currentPromptList.length);
      renderPromptList(currentPromptList);
      renderPagination();
      return prompts;
    } catch (error) {
      console.error('Failed to load prompts:', error);
      showEmptyState(true, error);
      throw error;
    } finally {
      setPromptListLoading(false);
    }
  }

  function renderStats(stats) {
    const promptCount = stats.prompt_count ?? 0;
    const ratedCount = stats.rated_count ?? 0;
    const avgRating = stats.avg_rating ?? 0;
    const tagCount = stats.tag_count ?? 0;
    const totalImages = stats.total_images ?? stats.image_count ?? 0;
    const topCategory = Array.isArray(stats.top_categories) && stats.top_categories.length
      ? stats.top_categories[0]
      : null;

    setTextContent('statTotalPrompts', formatNumber(promptCount));
    setTextContent('statAverageRating', `Avg. rating: ${avgRating.toFixed(2)}`);

    setTextContent('statRatedPrompts', formatNumber(ratedCount));
    const ratedShare = promptCount ? `${((ratedCount / promptCount) * 100).toFixed(1)}%` : '0%';
    setTextContent('statRatedShare', `Rated share: ${ratedShare}`);

    setTextContent('statUniqueTags', formatNumber(tagCount));
    if (topCategory) {
      setTextContent('statTopCategory', `Top category: ${topCategory.name} (${formatNumber(topCategory.count)})`);
    } else {
      setTextContent('statTopCategory', 'Top category: —');
    }

    setTextContent('statLinkedImages', formatNumber(totalImages));
    const imagesPerPrompt = promptCount ? totalImages / promptCount : 0;
    setTextContent('statImagesPerPrompt', `Images per prompt: ${imagesPerPrompt.toFixed(2)}`);
  }

  function renderHeroStats(epicStats) {
    // Update the hero stats section if it exists
    const heroSection = document.querySelector('.hero-stats');
    if (!heroSection || !epicStats?.hero_stats) return;

    // Find and update hero stat elements
    const heroElements = {
      'Total Images': epicStats.hero_stats.total_images || 0,
      'Total Prompts': epicStats.hero_stats.total_prompts || 0,
      'Rated Prompts': epicStats.hero_stats.rated_count || 0,
      'Average Rating': (epicStats.hero_stats.avg_rating || 0).toFixed(2),
      '5-Star Images': epicStats.hero_stats.five_star_count || 0,
      'Collections': epicStats.hero_stats.total_collections || 0,
      'Generation Streak': `${epicStats.hero_stats.generation_streak || 0} days`,
      'Avg Images/Prompt': (epicStats.hero_stats.images_per_prompt || 0).toFixed(2),
    };

    // Update hero stat cards
    const statCards = heroSection.querySelectorAll('.stat-card');
    statCards.forEach(card => {
      const label = card.querySelector('.stat-label')?.textContent;
      const valueElement = card.querySelector('.stat-value');
      if (label && valueElement && heroElements[label] !== undefined) {
        valueElement.textContent = formatNumber(heroElements[label]);
      }
    });

    // If Generation Analytics section exists, update it
    const genAnalytics = epicStats.generation_analytics;
    if (genAnalytics) {
      setTextContent('genTotalGenerations', formatNumber(genAnalytics.total_generations));
      setTextContent('genUniquePrompts', formatNumber(genAnalytics.unique_prompts));
      setTextContent('genAvgPerDay', formatNumber(genAnalytics.avg_per_day));
      setTextContent('genPeakDay', `${genAnalytics.peak_day || 'N/A'}: ${formatNumber(genAnalytics.peak_day_count)} images`);
      setTextContent('genAvgTime', `${(genAnalytics.avg_generation_time || 0).toFixed(2)}s`);
    }

    // If Quality Metrics section exists, update it
    const qualityMetrics = epicStats.quality_metrics;
    if (qualityMetrics) {
      setTextContent('qualAvgScore', (qualityMetrics.avg_quality_score || 0).toFixed(2));
      setTextContent('qualHighCount', formatNumber(qualityMetrics.high_quality_count));
      setTextContent('qualLowCount', formatNumber(qualityMetrics.low_quality_count));
      setTextContent('qualTrend', qualityMetrics.quality_trend || 'stable');
    }
  }

  function handleSearchInput(rawValue) {
    const value = (rawValue ?? '').toString();
    searchTerm = value.trim();
    searchTermLower = searchTerm.toLowerCase();

    // Clear existing timer
    if (searchDebounceTimer) {
      clearTimeout(searchDebounceTimer);
    }

    // Debounce search - wait 300ms after user stops typing
    searchDebounceTimer = setTimeout(async () => {
      // Reset to page 1 when searching
      paginationState.page = 1;

      // Load prompts with search term
      await loadPrompts(1);
    }, 300);
  }

  function getVisiblePrompts() {
    if (!searchTermLower) {
      return [...allPrompts];
    }

    return allPrompts.filter((prompt) => {
      const haystacks = [];
      if (prompt.prompt) haystacks.push(prompt.prompt);
      if (prompt.positive_prompt) haystacks.push(prompt.positive_prompt);
      if (prompt.negative_prompt) haystacks.push(prompt.negative_prompt);
      if (prompt.category) haystacks.push(prompt.category);
      if (prompt.notes) haystacks.push(prompt.notes);
      const normalizedTags = normalizePromptTags(prompt);
      if (normalizedTags.length) {
        haystacks.push(normalizedTags.join(' '));
      }
      return haystacks.some((text) =>
        typeof text === 'string' && text.toLowerCase().includes(searchTermLower)
      );
    });
  }

  function getSelectedPromptIds() {
    return Array.from(selectedPrompts);
  }

  function findPromptById(id) {
    return currentPromptList.find((prompt) => Number(prompt.id) === id);
  }

  function handlePromptSelectionChange(id, shouldSelect) {
    if (shouldSelect) {
      selectedPrompts.add(id);
    } else {
      selectedPrompts.delete(id);
    }
    updateSelectionUI();
  }

  function handleSelectAllToggle(event) {
    const shouldSelect = event.target.checked;
    if (shouldSelect) {
      currentPromptList.forEach((prompt) => {
        selectedPrompts.add(Number(prompt.id));
      });
    } else {
      currentPromptList.forEach((prompt) => {
        selectedPrompts.delete(Number(prompt.id));
      });
    }
    renderPromptList(currentPromptList);
  }

  function clearSelection() {
    if (selectedPrompts.size === 0) {
      return;
    }
    selectedPrompts.clear();
    renderPromptList(currentPromptList);
  }

  function updateSelectionUI() {
    const ids = getSelectedPromptIds();
    const count = ids.length;
    const totalVisible = currentPromptList.length;

    if (selectionControls.countLabel) {
      selectionControls.countLabel.textContent = count
        ? `${count} selected`
        : 'No prompts selected';
    }

    const selectAllCheckbox = selectionControls.selectAllCheckbox;
    if (selectAllCheckbox) {
      selectAllCheckbox.disabled = totalVisible === 0;
      if (!totalVisible) {
        selectAllCheckbox.checked = false;
        selectAllCheckbox.indeterminate = false;
      } else if (count === 0) {
        selectAllCheckbox.checked = false;
        selectAllCheckbox.indeterminate = false;
      } else if (count === totalVisible) {
        selectAllCheckbox.checked = true;
        selectAllCheckbox.indeterminate = false;
      } else {
        selectAllCheckbox.checked = false;
        selectAllCheckbox.indeterminate = true;
      }
    }

    if (selectionControls.selectAllButton) {
      selectionControls.selectAllButton.classList.toggle('is-disabled', totalVisible === 0);
    }

    const disable = count === 0;
    [selectionControls.deleteButton, selectionControls.addTagsButton, selectionControls.exportButton].forEach((button) => {
      if (!button) return;
      button.disabled = disable;
      button.classList.toggle('is-disabled', disable);
    });

    if (selectionControls.addCollectionsButton) {
      selectionControls.addCollectionsButton.disabled = true;
      selectionControls.addCollectionsButton.classList.add('is-disabled');
    }
  }

  function renderPromptList(prompts) {
    const list = document.getElementById('promptList');
    const emptyPlaceholder = document.getElementById('promptListEmpty');

    if (!list) {
      return;
    }

    currentPromptList = Array.isArray(prompts) ? prompts : [];
    const visibleIds = new Set(currentPromptList.map((prompt) => Number(prompt.id)));
    selectedPrompts = new Set(getSelectedPromptIds().filter((id) => visibleIds.has(id)));
    list.innerHTML = '';

    if (!currentPromptList || currentPromptList.length === 0) {
      if (emptyPlaceholder) {
        emptyPlaceholder.hidden = Boolean(searchTerm);
      }
      if (searchTerm) {
        showEmptyState(true, null, `No prompts match “${searchTerm}”.`);
      } else {
        showEmptyState(true);
      }
      updateSelectionUI();
      return;
    }

    showEmptyState(false);
    if (emptyPlaceholder) {
      emptyPlaceholder.hidden = true;
    }

    currentPromptList.forEach(prompt => {
      list.appendChild(createPromptItem(prompt));
    });

    setupPromptCards();
    updateSelectionUI();
  }

  function createPromptItem(prompt) {
    const item = document.createElement('div');
    item.className = 'prompt-item';
    const promptId = Number(prompt.id ?? 0);
    item.dataset.promptId = String(promptId);
    const blurPrompt = shouldBlurPrompt(prompt);

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.className = 'prompt-checkbox';
    const isSelected = selectedPrompts.has(promptId);
    checkbox.checked = isSelected;
    item.classList.toggle('is-selected', isSelected);
    checkbox.addEventListener('change', (event) => {
      handlePromptSelectionChange(promptId, event.target.checked);
      item.classList.toggle('is-selected', event.target.checked);
    });
    item.appendChild(checkbox);

    const main = document.createElement('div');
    main.className = 'prompt-main-content';
    item.appendChild(main);

    const header = document.createElement('div');
    header.className = 'prompt-header';
    main.appendChild(header);

    const headerInner = document.createElement('div');
    header.appendChild(headerInner);

    const meta = document.createElement('div');
    meta.className = 'prompt-meta';
    headerInner.appendChild(meta);

    const categoryChip = document.createElement('span');
    categoryChip.className = 'chip';
    categoryChip.innerHTML = highlightWithCurrentSearch(prompt.category || 'Uncategorized');
    meta.appendChild(categoryChip);

    if (Array.isArray(prompt.tags) && prompt.tags.length) {
      prompt.tags.slice(0, 2).forEach(tag => {
        const chip = document.createElement('span');
        chip.className = 'chip';
        chip.innerHTML = highlightWithCurrentSearch(tag);
        meta.appendChild(chip);
      });
    }

    const idChip = document.createElement('span');
    idChip.className = 'chip';
    const idLabel = `#${prompt.id ?? '—'}`;
    idChip.innerHTML = highlightWithCurrentSearch(idLabel);
    meta.appendChild(idChip);

    const positiveBlock = document.createElement('div');
    positiveBlock.className = 'prompt-content prompt-positive';
    const positiveLabel = document.createElement('div');
    positiveLabel.className = 'label prompt-label';
    positiveLabel.textContent = '✅ Positive Prompt';
    positiveBlock.appendChild(positiveLabel);

    const copyIcon = document.createElement('span');
    copyIcon.className = 'copy-icon';
    copyIcon.innerHTML = '<i class="fa-solid fa-clipboard-list" aria-hidden="true"></i> Copy';
    // Always copy the full text, not the displayed text
    const positiveFull = prompt.prompt || prompt.positive_prompt || '';
    copyIcon.addEventListener('click', (e) => {
      e.stopPropagation();
      copyToClipboard(positiveFull);
    });
    positiveBlock.appendChild(copyIcon);

    // Add hover send button for positive prompt
    const positiveSendIcon = document.createElement('span');
    positiveSendIcon.className = 'prompt-send-icon';
    positiveSendIcon.innerHTML = '<i class="fa-solid fa-paper-plane"></i> Send';
    positiveSendIcon.addEventListener('click', (e) => {
      e.stopPropagation();
      if (window.ComfyUISender) {
        window.ComfyUISender.sendToComfyUI(prompt, {
          shiftPressed: e?.shiftKey || false,
          type: 'positive'
        });
      } else {
        console.error('ComfyUISender not loaded');
        window.showToast?.('ComfyUI Sender service not available', 'error');
      }
    });
    positiveBlock.appendChild(positiveSendIcon);

    const positiveTextContainer = document.createElement('div');
    positiveTextContainer.className = 'prompt-text-container';

    const positiveText = document.createElement('p');
    positiveText.className = 'prompt-content-text';
    const needsTruncation = positiveFull.length > 220;
    let isExpanded = false;

    // Initially show truncated text if needed
    positiveText.innerHTML = needsTruncation ? formatPromptText(positiveFull) : highlightWithCurrentSearch(positiveFull);
    positiveText.title = positiveFull;
    positiveTextContainer.appendChild(positiveText);

    // Add show more/less button if text is long
    if (needsTruncation) {
      const toggleButton = document.createElement('button');
      toggleButton.className = 'prompt-toggle-btn';
      toggleButton.innerHTML = '<i class="fa-solid fa-chevron-down"></i> Show more';
      toggleButton.addEventListener('click', (e) => {
        e.stopPropagation();
        isExpanded = !isExpanded;
        if (isExpanded) {
          positiveText.innerHTML = highlightWithCurrentSearch(positiveFull);
          toggleButton.innerHTML = '<i class="fa-solid fa-chevron-up"></i> Show less';
        } else {
          positiveText.innerHTML = formatPromptText(positiveFull);
          toggleButton.innerHTML = '<i class="fa-solid fa-chevron-down"></i> Show more';
        }
      });
      positiveTextContainer.appendChild(toggleButton);
    }

    positiveBlock.appendChild(positiveTextContainer);
    main.appendChild(positiveBlock);

    if (prompt.negative_prompt) {
      const negativeBlock = document.createElement('div');
      negativeBlock.className = 'prompt-content prompt-negative';
      const negativeLabel = document.createElement('div');
      negativeLabel.className = 'label prompt-label';
      negativeLabel.textContent = '⛔ Negative Prompt';
      negativeBlock.appendChild(negativeLabel);

      const negativeCopy = document.createElement('span');
      negativeCopy.className = 'copy-icon';
      negativeCopy.innerHTML = '<i class="fa-solid fa-clipboard-list" aria-hidden="true"></i> Copy';
      // Always copy the full negative text
      negativeCopy.addEventListener('click', (e) => {
        e.stopPropagation();
        copyToClipboard(prompt.negative_prompt);
      });
      negativeBlock.appendChild(negativeCopy);

      // Add hover send button for negative prompt
      const negativeSendIcon = document.createElement('span');
      negativeSendIcon.className = 'prompt-send-icon';
      negativeSendIcon.innerHTML = '<i class="fa-solid fa-paper-plane"></i> Send';
      negativeSendIcon.addEventListener('click', (e) => {
        e.stopPropagation();
        if (window.ComfyUISender) {
          window.ComfyUISender.sendToComfyUI(prompt, {
            shiftPressed: e?.shiftKey || false,
            type: 'negative'
          });
        } else {
          console.error('ComfyUISender not loaded');
          window.showToast?.('ComfyUI Sender service not available', 'error');
        }
      });
      negativeBlock.appendChild(negativeSendIcon);

      const negativeTextContainer = document.createElement('div');
      negativeTextContainer.className = 'prompt-text-container';

      const negativeText = document.createElement('p');
      negativeText.className = 'prompt-content-text';
      const negativeNeedsTruncation = prompt.negative_prompt.length > 220;
      let negativeIsExpanded = false;

      // Initially show truncated text if needed
      negativeText.innerHTML = negativeNeedsTruncation ? formatPromptText(prompt.negative_prompt) : highlightWithCurrentSearch(prompt.negative_prompt);
      negativeText.title = prompt.negative_prompt;
      negativeTextContainer.appendChild(negativeText);

      // Add show more/less button if text is long
      if (negativeNeedsTruncation) {
        const negativeToggleButton = document.createElement('button');
        negativeToggleButton.className = 'prompt-toggle-btn';
        negativeToggleButton.innerHTML = '<i class="fa-solid fa-chevron-down"></i> Show more';
        negativeToggleButton.addEventListener('click', (e) => {
          e.stopPropagation();
          negativeIsExpanded = !negativeIsExpanded;
          if (negativeIsExpanded) {
            negativeText.innerHTML = highlightWithCurrentSearch(prompt.negative_prompt);
            negativeToggleButton.innerHTML = '<i class="fa-solid fa-chevron-up"></i> Show less';
          } else {
            negativeText.innerHTML = formatPromptText(prompt.negative_prompt);
            negativeToggleButton.innerHTML = '<i class="fa-solid fa-chevron-down"></i> Show more';
          }
        });
        negativeTextContainer.appendChild(negativeToggleButton);
      }

      negativeBlock.appendChild(negativeTextContainer);
      main.appendChild(negativeBlock);
    }

    const filmStrip = createFilmStrip(prompt, blurPrompt);
    if (filmStrip) {
      main.appendChild(filmStrip);
    }

    const actions = document.createElement('div');
    actions.className = 'prompt-side-actions';

    const galleryButton = createSideActionButton({
      label: 'Gallery',
      iconClass: 'fa-solid fa-images',
      onClick: () => openPromptImages(prompt),
    });
    actions.appendChild(galleryButton);

    const sendButton = createSideActionButton({
      label: 'Send to ComfyUI',
      iconClass: 'fa-solid fa-paper-plane',
      onClick: (e) => {
        if (window.ComfyUISender) {
          window.ComfyUISender.sendToComfyUI(prompt, {
            shiftPressed: e?.shiftKey || false,
            type: 'both'  // Main button sends both positive and negative
          });
        } else {
          console.error('ComfyUISender not loaded');
          window.showToast?.('ComfyUI Sender service not available', 'error');
        }
      },
      variant: 'send',
    });
    actions.appendChild(sendButton);

    const editButton = createSideActionButton({
      label: 'Edit',
      iconClass: 'fa-solid fa-pen',
      onClick: () => startEditPrompt(prompt),
    });
    actions.appendChild(editButton);

    const deleteButton = createSideActionButton({
      label: 'Delete',
      iconClass: 'fa-solid fa-trash',
      onClick: () => confirmDeletePrompt(prompt),
      variant: 'danger',
    });
    actions.appendChild(deleteButton);

    item.appendChild(actions);
    return item;
  }

  function createSideActionButton({ label, iconClass, onClick, variant = 'default' }) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'prompt-side-btn';
    if (variant !== 'default') {
      button.classList.add(`prompt-side-btn--${variant}`);
    }
    button.title = label;
    button.setAttribute('aria-label', label);
    button.innerHTML = `<i class="${iconClass}" aria-hidden="true"></i>`;
    button.addEventListener('click', (event) => {
      event.stopPropagation();
      try {
        onClick?.(event);
      } catch (error) {
        console.error(`${label} action failed`, error);
      }
    });
    return button;
  }

  function createFilmStrip(prompt, shouldBlur) {
    const images = Array.isArray(prompt.images) ? prompt.images : [];
    const totalImages = prompt.image_count ?? images.length;

    const filmStrip = document.createElement('div');
    filmStrip.className = `prompt-film-strip film-strip-size-${filmStripSize}`;

    const thumbnails = document.createElement('div');
    thumbnails.className = 'film-strip-thumbnails';
    filmStrip.appendChild(thumbnails);

    images.slice(0, getFilmStripImageLimit()).forEach((image, index) => {
      const thumb = createFilmStripThumbnail(prompt, image, index, shouldBlur);
      if (thumb) {
        thumbnails.appendChild(thumb);
      }
    });

    const remaining = Math.max(0, totalImages - Math.min(images.length, getFilmStripImageLimit()));
    if (remaining > 0) {
      const more = document.createElement('div');
      more.className = 'film-strip-thumbnail film-strip-thumbnail--more';
      more.textContent = `+${remaining}`;
      more.title = 'Open gallery';
      more.addEventListener('click', () => openPromptImages(prompt));
      thumbnails.appendChild(more);
    }

    return filmStrip;
  }

  function createFilmStripThumbnail(prompt, image, index = 0, shouldBlur = false) {
    if (!image) return null;

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'film-strip-thumbnail';
    const baseTitle = image.file_name || 'Generated image';
    button.title = shouldBlur ? `${baseTitle} (hover to reveal)` : baseTitle;
    button.dataset.imageIndex = String(index);

    const url = image.thumbnail_url || image.url;
    if (url) {
      button.style.backgroundImage = `url(${encodeURI(url)})`;
    }

    if (shouldBlur) {
      button.classList.add('blur-sensitive', 'is-blurred');
    }

    button.addEventListener('click', () => openPromptImageViewer(prompt, index));
    return button;
  }

  function openImageInNewTab(image) {
    const url = image?.url || image?.fullSrc || image?.thumbnail_url;
    if (!url) {
      return;
    }
    window.open(url, '_blank', 'noopener');
  }

  async function openPromptImageViewer(prompt, startIndex = 0) {
    if (!prompt || !prompt.id) {
      return;
    }

    try {
      const promptImages = await ensurePromptImages(prompt, PROMPT_GALLERY_IMAGE_LIMIT);
      if (!Array.isArray(promptImages) || promptImages.length === 0) {
        console.warn('No images found for prompt', prompt.id);
        return;
      }

      const preferences = getGalleryViewerPreferences();
      if (!ensureViewerIntegrationReady(preferences)) {
        openImageInNewTab(promptImages[startIndex] || promptImages[0]);
        return;
      }

      const viewerItems = promptImages
        .map((image, index) => mapPromptImageToViewerEntry(image, index, prompt))
        .filter(Boolean);

      if (!viewerItems.length) {
        openImageInNewTab(promptImages[startIndex] || promptImages[0]);
        return;
      }

      const safeIndex = Math.min(Math.max(startIndex, 0), viewerItems.length - 1);

      const integrationId = window.ViewerIntegration.openImageSet(viewerItems, {
        startIndex: safeIndex,
        viewer: preferences.viewer,
        filmstrip: preferences.filmstrip,
        metadata: preferences.metadata,
        context: {
          type: 'prompt-filmstrip',
          promptId: prompt.id,
        },
      });

      if (!integrationId) {
        openImageInNewTab(promptImages[safeIndex]);
      }
    } catch (error) {
      console.error('Failed to open prompt image viewer:', error);
      const promptImages = promptGalleryState.images.length ? promptGalleryState.images : null;
      const fallbackImage = promptImages?.[startIndex] || promptImages?.[0];
      if (fallbackImage) {
        openImageInNewTab(fallbackImage);
      }
    }
  }

  async function openPromptImages(prompt, startIndex = 0) {
    if (!prompt || !prompt.id) {
      return;
    }

    showPromptGalleryOverlay(prompt);
    updateGalleryStatus('Loading images…');

    try {
      const promptImages = await ensurePromptImages(prompt, PROMPT_GALLERY_IMAGE_LIMIT);
      if (!Array.isArray(promptImages) || promptImages.length === 0) {
        updateGalleryStatus('No images linked to this prompt yet.');
        return;
      }

      promptGalleryState.promptId = prompt.id;
      promptGalleryState.images = promptImages;
      promptGalleryState.activeIndex = Math.min(Math.max(startIndex, 0), promptImages.length - 1);

      renderPromptGallery(prompt, promptImages, promptGalleryState.activeIndex);
    } catch (error) {
      console.error('Failed to load images for prompt gallery:', error);
      updateGalleryStatus('Failed to load images.');
    }
  }

  async function startEditPrompt(prompt) {
    if (!prompt || !prompt.id) {
      return;
    }

    editingPromptId = Number(prompt.id);
    showEditPromptModal();
    setEditModalLoading(true, 'load');

    try {
      const response = await fetchJson(`/prompts/${editingPromptId}`);
      const promptData = response?.data ?? response ?? prompt;
      populateEditForm(promptData);
      setEditModalLoading(false);
      focusEditPositivePrompt();
    } catch (error) {
      console.error('Failed to load prompt for editing', error);
      if (window.NotificationService) {
        window.NotificationService.show('Failed to load prompt for editing', 'error');
      }
      hideEditPromptModal();
    }
  }

  async function confirmDeletePrompt(prompt) {
    if (!prompt || !prompt.id) {
      return;
    }

    const shouldDelete = window.confirm(`Delete prompt #${prompt.id}? This action cannot be undone.`);
    if (!shouldDelete) {
      return;
    }

    try {
      const response = await fetchJson(`/prompts/${prompt.id}`, { method: 'DELETE' });
      if (!response?.success) {
        throw new Error(response?.error || 'Delete failed');
      }

      if (window.NotificationService) {
        window.NotificationService.show('Prompt deleted successfully', 'success');
      }

      const nextTotal = Math.max(0, paginationState.total - 1);
      const maxPage = Math.max(1, Math.ceil(nextTotal / paginationState.limit));
      const targetPage = Math.min(paginationState.page, maxPage);
      await loadPrompts(targetPage);
    } catch (error) {
      console.error('Failed to delete prompt', error);
      if (window.NotificationService) {
        window.NotificationService.show('Failed to delete prompt', 'error');
      }
    }
  }

  async function ensurePromptImages(prompt, limit = PROMPT_GALLERY_IMAGE_LIMIT) {
    const existing = Array.isArray(prompt.images) ? prompt.images : [];
    const expected = typeof prompt.image_count === 'number' ? prompt.image_count : existing.length;

    if (existing.length && (existing.length >= expected || existing.length >= limit)) {
      return existing.slice(0, limit);
    }

    const payload = await fetchJson(`/prompts/${prompt.id}/images?limit=${limit}`);
    const fetched = Array.isArray(payload?.data) ? payload.data : [];
    prompt.images = fetched;
    if (typeof prompt.image_count !== 'number') {
      prompt.image_count = fetched.length;
    }
    return fetched.slice(0, limit);
  }

  function ensureViewerIntegrationReady(preferences) {
    if (viewerIntegrationReady) {
      return true;
    }
    if (!window.ViewerIntegration || typeof window.ViewerIntegration.init !== 'function') {
      console.warn('ViewerIntegration not available');
      return false;
    }
    try {
      window.ViewerIntegration.init({
        viewer: preferences?.viewer,
        filmstrip: preferences?.filmstrip,
        metadata: preferences?.metadata,
      });
      viewerIntegrationReady = true;
    } catch (error) {
      console.warn('ViewerIntegration init failed', error);
      viewerIntegrationReady = false;
    }
    return viewerIntegrationReady;
  }

  function getGalleryElements() {
    return {
      overlay: document.getElementById('promptGalleryOverlay'),
      title: document.getElementById('promptGalleryTitle'),
      gallery: document.getElementById('promptGalleryGallery'),
      status: document.getElementById('promptGalleryStatus'),
    };
  }

  function showPromptGalleryOverlay(prompt) {
    const { overlay, title, gallery } = getGalleryElements();
    if (!overlay) {
      return;
    }

    overlay.hidden = false;
    overlay.classList.add('is-visible');
    updateBodyScrollLock();

    if (gallery) {
      gallery.innerHTML = '';
    }

    if (overlay.getAttribute('tabindex') === null) {
      overlay.setAttribute('tabindex', '-1');
    }
    overlay.focus({ preventScroll: true });

    if (title) {
      const promptLabel = prompt?.prompt || prompt?.positive_prompt || '';
      const truncated = promptLabel ? truncate(promptLabel, 80) : '';
      title.textContent = truncated ? `Prompt #${prompt.id}: ${truncated}` : `Prompt #${prompt.id}`;
    }
  }

  function normalizePromptImageForGallery(image, index = 0) {
    const shared = window.GalleryShared;
    const sizeLabel = shared?.formatFileSize ? shared.formatFileSize(image.file_size) : computeSizeLabel(image.file_size);

    return Object.assign({}, image, {
      id: image.id ?? image.image_id ?? `prompt-image-${index}`,
      filename: image.file_name || image.filename || image.name || `Image ${index + 1}`,
      thumbnail_url: image.thumbnail_url || image.thumbnail || image.thumbnail_medium_url || image.preview || image.src || image.url || image.full_url || '',
      image_url: image.url || image.full_url || image.path || image.image_url || image.src || '',
      dimensions: image.dimensions || buildDimensionsLabel(image.width, image.height),
      size: image.size || sizeLabel,
      generation_time: image.generation_time || image.created_at || image.timestamp,
    });
  }

  function buildDimensionsLabel(width, height) {
    if (width && height) {
      return `${width}x${height}`;
    }
    return 'Unknown';
  }

  function computeSizeLabel(bytes) {
    if (!Number.isFinite(bytes)) {
      return 'Unknown';
    }
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), sizes.length - 1);
    return `${(bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1)} ${sizes[i]}`;
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
        console.debug('Failed to parse metadata JSON', error);
      }
    }
    return null;
  }

  function mapPromptImageToViewerEntry(image, index = 0, prompt = null) {
    if (!image) {
      return null;
    }

    const thumb = image.thumbnail_url || image.thumbnail || image.thumbnail_medium_url || image.preview || image.src || image.url || image.full_url || '';
    const full = image.url || image.full_url || image.path || image.image_url || image.src || thumb;
    const imageId = image.id
      ?? image.image_id
      ?? image.generated_image_id
      ?? image.original_image_id
      ?? extractImageIdFromUrl(full)
      ?? extractImageIdFromUrl(thumb);

    const metadata = parseMaybeJson(image.metadata)
      || parseMaybeJson(image.meta)
      || parseMaybeJson(image.prompt_metadata);

    if (!thumb && !full) {
      return null;
    }

    const label = image.file_name || image.filename || image.name || `Image ${index + 1}`;

    return {
      src: thumb || full,
      thumbnail: thumb || full,
      fullSrc: full || thumb,
      alt: image.alt || label,
      title: label,
      id: imageId,
      metadata,
      original: image,
      promptId: prompt?.id ?? null,
      index,
    };
  }

  function applyPromptBlur(elements, shouldBlur) {
    elements.forEach((element) => {
      const wrapper = element.querySelector('.gallery-image');
      if (!wrapper) {
        return;
      }
      if (shouldBlur) {
        wrapper.classList.add('blur-sensitive', 'is-blurred');
      } else {
        wrapper.classList.remove('blur-sensitive', 'is-blurred');
      }
    });
  }

  function highlightPromptGalleryIndex(index) {
    const { gallery } = getGalleryElements();
    if (!gallery || !window.GalleryShared?.highlightIndex) {
      return;
    }

    window.GalleryShared.highlightIndex(gallery, index);

    const activeItem = gallery.querySelector('.gallery-item.is-active');
    if (activeItem && typeof activeItem.scrollIntoView === 'function') {
      activeItem.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'nearest' });
    }
  }

  function renderPromptGallery(prompt, promptImages, focusIndex = 0) {
    const { gallery } = getGalleryElements();
    if (!gallery) {
      return;
    }

    const shared = window.GalleryShared;
    if (!shared || typeof shared.renderItems !== 'function') {
      gallery.innerHTML = '<p class="text-muted">Gallery unavailable.</p>';
      updateGalleryStatus('');
      return;
    }

    const preferences = getGalleryViewerPreferences();
    ensureViewerIntegrationReady(preferences);

    const normalizedItems = promptImages.map((image, index) => normalizePromptImageForGallery(image, index));

    const { integrationId, elements } = shared.renderItems(gallery, normalizedItems, {
      viewMode: gallery.dataset.viewMode || 'grid',
      includeActions: false,
      selectors: {
        gallery: '#promptGalleryGallery',
        galleryImage: '#promptGalleryGallery .gallery-image img',
      },
      activeIndex: focusIndex,
      enableViewer: true,
      viewerConfig: preferences,
      afterRender(renderedElements) {
        applyPromptBlur(renderedElements, shouldBlurPrompt(prompt));
      }
    });

    promptGalleryIntegrationId = integrationId;
    promptGalleryState.integrationId = integrationId;

    updateGalleryStatus('');
    highlightPromptGalleryIndex(focusIndex);

    if (integrationId && window.ViewerIntegration && typeof window.ViewerIntegration.showImage === 'function') {
      window.requestAnimationFrame(() => {
        window.ViewerIntegration.showImage(focusIndex, integrationId);
      });
    }
  }

  function updateGalleryStatus(message) {
    const { status } = getGalleryElements();
    if (!status) {
      return;
    }

    if (message) {
      status.textContent = message;
      status.hidden = false;
    } else {
      status.textContent = '';
      status.hidden = true;
    }
  }

  function hidePromptGallery() {
    const { overlay } = getGalleryElements();
    if (!overlay) {
      return;
    }

    overlay.classList.remove('is-visible');
    overlay.hidden = true;
    updateBodyScrollLock();

    if (gallery && window.GalleryShared?.teardown) {
      window.GalleryShared.teardown(gallery);
    }

    promptGalleryIntegrationId = null;
    promptGalleryState.promptId = null;
    promptGalleryState.images = [];
    promptGalleryState.integrationId = null;
    promptGalleryState.activeIndex = 0;
  }

  function isGalleryOpen() {
    const { overlay } = getGalleryElements();
    return Boolean(overlay && !overlay.hidden);
  }

  function isCreatePromptModalOpen() {
    const modal = document.getElementById('createPromptModal');
    return Boolean(modal && modal.classList.contains('active'));
  }

  function isEditPromptModalOpen() {
    const modal = document.getElementById('editPromptModal');
    return Boolean(modal && modal.classList.contains('active'));
  }

  function isDeleteModalOpen() {
    const modal = document.getElementById('deleteSelectedModal');
    return Boolean(modal && !modal.hasAttribute('hidden'));
  }

  function isAddTagsModalOpen() {
    const modal = document.getElementById('addTagsModal');
    return Boolean(modal && !modal.hasAttribute('hidden'));
  }

  function updateBodyScrollLock() {
    const createModal = document.getElementById('createPromptModal');
    const editModal = document.getElementById('editPromptModal');
    const galleryOverlay = document.getElementById('promptGalleryOverlay');
    const deleteModal = document.getElementById('deleteSelectedModal');
    const addTagsModal = document.getElementById('addTagsModal');
    const shouldLock = (createModal && createModal.classList.contains('active'))
      || (editModal && editModal.classList.contains('active'))
      || (galleryOverlay && galleryOverlay.classList.contains('is-visible'))
      || (deleteModal && deleteModal.classList.contains('active'))
      || (addTagsModal && addTagsModal.classList.contains('active'));

    document.body.style.overflow = shouldLock ? 'hidden' : '';
  }

  function focusSearchInput() {
    const input = document.getElementById('searchInput');
    if (input) {
      input.focus({ preventScroll: false });
      input.select();
    }
  }

  function setPromptListLoading(isLoading) {
    const list = document.getElementById('promptList');
    if (list) {
      list.classList.toggle('prompt-list--loading', Boolean(isLoading));
    }
  }

  function renderPagination() {
    const container = document.getElementById('promptPagination');
    if (!container) {
      return;
    }

    const { total, limit, page, totalPages } = paginationState;
    if (searchTermLower) {
      container.hidden = false;
      container.innerHTML = '';
      const info = document.createElement('div');
      info.className = 'pagination-info pagination-info--search';
      const countLabel = currentPromptList.length === 1 ? '1 result' : `${currentPromptList.length} results`;
      info.innerHTML = `${countLabel} for “${escapeHtml(searchTerm)}”`;
      container.appendChild(info);
      return;
    }

    if (!total || totalPages <= 1) {
      container.innerHTML = '';
      container.hidden = true;
      return;
    }

    container.hidden = false;
    container.innerHTML = '';

    const info = document.createElement('div');
    info.className = 'pagination-info';
    const start = (page - 1) * limit + 1;
    const end = Math.min(total, start + limit - 1);
    info.textContent = `Showing ${start}-${end} of ${total}`;
    container.appendChild(info);

    const controls = document.createElement('div');
    controls.className = 'pagination-controls';

    controls.appendChild(createPaginationButton('Previous', page <= 1, () => changePage(page - 1), { ariaLabel: 'Go to previous page' }));

    const pages = buildPageList(page, totalPages);
    let lastPage = null;
    pages.forEach((pageNumber) => {
      if (lastPage && pageNumber - lastPage > 1) {
        controls.appendChild(createPaginationEllipsis());
      }
      controls.appendChild(
        createPaginationButton(String(pageNumber), false, () => changePage(pageNumber), {
          isActive: pageNumber === page,
          ariaLabel: `Go to page ${pageNumber}`,
        }),
      );
      lastPage = pageNumber;
    });

    controls.appendChild(createPaginationButton('Next', page >= totalPages, () => changePage(page + 1), { ariaLabel: 'Go to next page' }));

    container.appendChild(controls);
  }

  function createPaginationButton(label, disabled, onClick, { isActive = false, ariaLabel } = {}) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'pagination-button';
    if (isActive) {
      button.classList.add('active');
    }
    if (ariaLabel) {
      button.setAttribute('aria-label', ariaLabel);
    }

    button.textContent = label;
    button.disabled = Boolean(disabled);

    if (!disabled) {
      button.addEventListener('click', () => {
        onClick?.();
      });
    }

    return button;
  }

  function createPaginationEllipsis() {
    const span = document.createElement('span');
    span.className = 'pagination-ellipsis';
    span.textContent = '…';
    return span;
  }

  function buildPageList(current, total) {
    const pages = new Set([1, total, current, current - 1, current + 1, current - 2, current + 2]);
    return Array.from(pages)
      .filter((page) => Number.isInteger(page) && page >= 1 && page <= total)
      .sort((a, b) => a - b);
  }

  function scrollToTop() {
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

  function changePage(targetPage) {
    const page = Math.max(1, Math.min(targetPage, paginationState.totalPages));
    if (page === paginationState.page) {
      return;
    }

    // Scroll to top when changing pages
    scrollToTop();

    loadPrompts(page).catch((error) => {
      console.error('Failed to change page', error);
    });
  }

  function registerSettingsListener() {
    window.addEventListener('storage', handleSettingsStorageEvent);
  }

  function handleSettingsStorageEvent(event) {
    if (event.key !== 'promptManagerSettings') {
      return;
    }

    const newLimit = getItemsPerPageSetting();
    const limitChanged = newLimit !== paginationState.limit;
    paginationState.limit = newLimit;
    filmStripSize = getFilmStripSizeSetting();
    blurConfig = getBlurSettings();

    if (limitChanged) {
      changePage(1);
    } else if (currentPromptList.length || searchTermLower) {
      renderPromptList(getVisiblePrompts());
      renderPagination();
    }
  }

  function getStoredSettings() {
    try {
      const raw = localStorage.getItem('promptManagerSettings');
      if (!raw) {
        return {};
      }
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === 'object' ? parsed : {};
    } catch (error) {
      console.error('Failed to parse stored settings', error);
      return {};
    }
  }

  function getItemsPerPageSetting() {
    const settings = getStoredSettings();
    const raw = settings.itemsPerPage ?? settings.items_per_page;
    const parsed = Number.parseInt(raw, 10);
    if (Number.isFinite(parsed) && parsed > 0) {
      return Math.min(Math.max(parsed, 5), 200);
    }
    return 50;
  }

  function getGalleryViewerPreferences() {
    const settings = getStoredSettings();

    const viewer = Object.assign({
      theme: settings.viewerTheme || 'dark',
      toolbar: settings.viewerToolbar !== false,
      navbar: settings.viewerNavbar !== false,
      title: settings.viewerTitle !== false,
      keyboard: settings.viewerKeyboard !== false,
      fullscreen: settings.viewerFullscreen !== false
    }, settings.viewer || {});

    const filmstrip = Object.assign({
      enabled: settings.filmstripEnabled !== false,
      position: settings.filmstripPosition || 'bottom',
      autoHide: settings.filmstripAutoHide === true,
    }, settings.filmstrip || {});

    if (settings.filmstripThumbnailWidth != null) {
      filmstrip.thumbnailWidth = Number(settings.filmstripThumbnailWidth);
    }

    if (settings.filmstripThumbnailHeight != null) {
      filmstrip.thumbnailHeight = Number(settings.filmstripThumbnailHeight);
    }

    const metadata = Object.assign({
      enabled: settings.metadataEnabled !== false,
      position: settings.metadataPosition || 'right',
      autoShow: settings.metadataAutoShow !== false,
      collapsible: settings.metadataCollapsible !== false
    }, settings.metadata || {});

    return { viewer, filmstrip, metadata };
  }

  function getFilmStripSizeSetting() {
    const settings = getStoredSettings();
    const value = String(settings.filmStripSize || '').toLowerCase();
    return ['small', 'medium', 'large'].includes(value) ? value : DEFAULT_FILM_STRIP_SIZE;
  }

  function getFilmStripImageLimit(size = filmStripSize) {
    return FILM_STRIP_IMAGE_LIMIT_BY_SIZE[size] || FILM_STRIP_IMAGE_LIMIT_BY_SIZE[DEFAULT_FILM_STRIP_SIZE];
  }

  function getBlurSettings() {
    const settings = getStoredSettings();
    const enabled = settings.blurNSFW !== false;
    const tagsSetting = settings.blurNSFWTags ?? DEFAULT_BLUR_TAGS.join(',');
    let tags = parseBlurTagsSetting(tagsSetting);
    if (!tags.length) {
      tags = [...DEFAULT_BLUR_TAGS];
    } else {
      tags = Array.from(new Set(tags));
    }
    return {
      enabled,
      tags,
    };
  }

  function parseBlurTagsSetting(value) {
    if (Array.isArray(value)) {
      return value.map((tag) => String(tag).trim().toLowerCase()).filter(Boolean);
    }
    if (!value) {
      return [];
    }
    return String(value)
      .split(',')
      .map((tag) => tag.trim().toLowerCase())
      .filter(Boolean);
  }

  function normalizePromptTags(prompt) {
    const raw = prompt?.tags;
    if (!raw) {
      return [];
    }

    if (Array.isArray(raw)) {
      return raw.map((tag) => String(tag).trim().toLowerCase()).filter(Boolean);
    }

    if (typeof raw === 'string') {
      try {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed)) {
          return parsed.map((tag) => String(tag).trim().toLowerCase()).filter(Boolean);
        }
      } catch (error) {
        // fall through to comma split
      }
      return raw
        .split(',')
        .map((tag) => tag.trim().toLowerCase())
        .filter(Boolean);
    }

    return [];
  }

  function parseTagsValue(value) {
    if (!value) {
      return [];
    }
    if (Array.isArray(value)) {
      return value.map((tag) => String(tag).trim().toLowerCase()).filter(Boolean);
    }
    if (typeof value === 'string') {
      try {
        const parsed = JSON.parse(value);
        if (Array.isArray(parsed)) {
          return parsed.map((tag) => String(tag).trim().toLowerCase()).filter(Boolean);
        }
      } catch (error) {
        // fall through to comma split
      }
      return value
        .split(',')
        .map((tag) => tag.trim().toLowerCase())
        .filter(Boolean);
    }
    return [];
  }

  function shouldBlurPrompt(prompt) {
    if (!blurConfig.enabled) {
      return false;
    }

    const promptTags = normalizePromptTags(prompt);
    if (!promptTags.length) {
      return false;
    }

    const tagSet = new Set(promptTags);
    return blurConfig.tags.some((tag) => tagSet.has(tag));
  }

  function showEmptyState(visible, error, messageOverride) {
    const emptyState = document.getElementById('emptyState');
    if (!emptyState) return;

    const message = emptyState.querySelector('p.empty-text');

    if (visible) {
      emptyState.classList.remove('empty-state-hidden');
      if (message) {
        if (messageOverride) {
          message.textContent = messageOverride;
        } else if (error) {
          message.textContent = `Failed to load prompts: ${error.message || error}`;
        } else {
          message.textContent = DEFAULT_EMPTY_MESSAGE;
        }
      }
    } else {
      emptyState.classList.add('empty-state-hidden');
      if (message) {
        message.textContent = DEFAULT_EMPTY_MESSAGE;
      }
    }
  }

  function setLoading(isLoading) {
    const overlay = document.getElementById('loadingOverlay');
    if (!overlay) return;
    overlay.classList.toggle('active', Boolean(isLoading));
  }

  function setTextContent(id, text) {
    const el = document.getElementById(id);
    if (el) {
      el.textContent = text;
    }
  }

  function formatNumber(value) {
    return Number(value || 0).toLocaleString();
  }

  function truncate(text, maxLength = 220) {
    if (!text) return '';
    if (text.length <= maxLength) return text;
    return `${text.slice(0, maxLength - 1)}…`;
  }

  function formatPromptText(text) {
    const source = typeof text === 'string' ? text : '';
    const truncated = truncate(source);
    return highlightWithCurrentSearch(truncated);
  }

  function highlightWithCurrentSearch(text) {
    if (!text) {
      return '';
    }

    if (!searchTermLower) {
      return escapeHtml(text);
    }

    const lowerText = text.toLowerCase();
    if (!lowerText.includes(searchTermLower)) {
      return escapeHtml(text);
    }

    const termLength = searchTerm.length || searchTermLower.length;
    if (!termLength) {
      return escapeHtml(text);
    }

    let result = '';
    let lastIndex = 0;
    let searchIndex = lowerText.indexOf(searchTermLower);

    while (searchIndex !== -1) {
      const matchEnd = searchIndex + termLength;
      result += escapeHtml(text.slice(lastIndex, searchIndex));
      result += `<mark class="prompt-highlight">${escapeHtml(text.slice(searchIndex, matchEnd))}</mark>`;
      lastIndex = matchEnd;
      searchIndex = lowerText.indexOf(searchTermLower, lastIndex);
    }

    result += escapeHtml(text.slice(lastIndex));
    return result;
  }

  function escapeHtml(text) {
    const safeText = typeof text === 'string' ? text : String(text ?? '');
    return safeText.replace(/[&<>"']/g, (char) => {
      switch (char) {
        case '&':
          return '&amp;';
        case '<':
          return '&lt;';
        case '>':
          return '&gt;';
        case '"':
          return '&quot;';
        case '\'':
          return '&#39;';
        default:
          return char;
      }
    });
  }

  function setEditModalLoading(isLoading, context = 'load') {
    const form = document.getElementById('editPromptForm');
    if (!form) {
      return;
    }

    const interactiveFields = form.querySelectorAll('input, textarea, select');
    interactiveFields.forEach((element) => {
      element.disabled = Boolean(isLoading);
    });

    const saveButton = form.querySelector('[data-action="save-edit-prompt"]');
    if (saveButton) {
      if (!saveButton.dataset.originalText) {
        saveButton.dataset.originalText = saveButton.textContent || 'Save Changes';
      }
      if (isLoading) {
        saveButton.textContent = context === 'save' ? 'Saving…' : 'Loading…';
        saveButton.disabled = true;
      } else {
        saveButton.textContent = saveButton.dataset.originalText;
        saveButton.disabled = false;
      }
    }
  }

  function populateEditForm(data) {
    const form = document.getElementById('editPromptForm');
    if (!form) {
      return;
    }

    const categoryInput = form.querySelector('#editPromptCategory');
    if (categoryInput) {
      categoryInput.value = data?.category ?? '';
    }

    const tagsInput = form.querySelector('#editPromptTags');
    if (tagsInput) {
      tagsInput.value = tagsArrayToInput(data?.tags);
    }

    const ratingInput = form.querySelector('#editPromptRating');
    if (ratingInput) {
      const ratingValue = data?.rating;
      ratingInput.value = ratingValue === null || ratingValue === undefined ? '' : ratingValue;
    }

    const notesField = form.querySelector('#editPromptNotes');
    if (notesField) {
      notesField.value = data?.notes ?? '';
    }

    const positiveField = form.querySelector('#editPositivePrompt');
    if (positiveField) {
      positiveField.value = data?.prompt || data?.positive_prompt || '';
    }

    const negativeField = form.querySelector('#editNegativePrompt');
    if (negativeField) {
      negativeField.value = data?.negative_prompt || '';
    }
  }

  function tagsArrayToInput(tags) {
    if (!tags) {
      return '';
    }
    if (Array.isArray(tags)) {
      return tags.join(', ');
    }
    if (typeof tags === 'string') {
      return tags;
    }
    return '';
  }

  function focusEditPositivePrompt() {
    window.requestAnimationFrame(() => {
      const field = document.getElementById('editPositivePrompt');
      if (field) {
        const length = field.value.length;
        field.focus();
        field.setSelectionRange(length, length);
      }
    });
  }
  /**
   * Toggle mobile menu
   */
  function toggleMobileMenu() {
    const sidebar = document.querySelector('.sidebar');
    if (sidebar) {
      sidebar.classList.toggle('mobile-active');
    }
  }

  /**
   * Show add prompt modal
   */
  function showAddPromptModal() {
    const modal = document.getElementById('createPromptModal');
    if (!modal) {
      return;
    }

    modal.classList.add('active');
    modal.removeAttribute('hidden');

    const form = document.getElementById('promptForm');
    form?.reset();
    currentModel = 'flux';
    switchModel(currentModel);

    const positivePromptField = form?.querySelector('#positivePrompt');
    if (positivePromptField) {
      window.requestAnimationFrame(() => positivePromptField.focus());
    }

    updateBodyScrollLock();
  }

  /**
   * Hide add prompt modal
   */
  function hideAddPromptModal() {
    const modal = document.getElementById('createPromptModal');
    if (!modal) {
      return;
    }

    modal.classList.remove('active');
    modal.setAttribute('hidden', 'hidden');

    updateBodyScrollLock();
  }

  function showEditPromptModal() {
    const modal = document.getElementById('editPromptModal');
    if (!modal) {
      return;
    }

    modal.classList.add('active');
    modal.removeAttribute('hidden');
    updateBodyScrollLock();
  }

  function hideEditPromptModal() {
    const modal = document.getElementById('editPromptModal');
    if (!modal) {
      return;
    }

    modal.classList.remove('active');
    modal.setAttribute('hidden', 'hidden');
    const form = document.getElementById('editPromptForm');
    form?.reset();
    editingPromptId = null;
    setEditModalLoading(false);
    updateBodyScrollLock();
  }

  /**
   * Switch between model types
   */
  function switchModel(model) {
    currentModel = model;

    // Update tab states
    document.querySelectorAll('.model-tab').forEach(tab => {
      tab.classList.toggle('active', tab.dataset.model === model);
    });

    // Toggle negative prompt visibility based on model selection
    const negativeGroup = document.querySelector('.negative-prompt-group');
    if (negativeGroup) {
      const shouldHide = model !== 'sd';
      negativeGroup.toggleAttribute('hidden', shouldHide);
      negativeGroup.classList.toggle('hidden', shouldHide);
    }
  }

  function collectTagSuggestions() {
    const counts = new Map();
    allPrompts.forEach((prompt) => {
      normalizePromptTags(prompt).forEach((tag) => {
        counts.set(tag, (counts.get(tag) || 0) + 1);
      });
    });

    return Array.from(counts.entries())
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .map(([tag]) => tag);
  }

  function openDeleteSelectedModal() {
    const ids = getSelectedPromptIds();
    if (!ids.length) {
      return;
    }

    const summary = document.getElementById('deleteSummary');
    if (summary) {
      summary.textContent = ids.map((id) => `#${id}`).join(', ');
    }

    const modal = document.getElementById('deleteSelectedModal');
    if (modal) {
      modal.removeAttribute('hidden');
      modal.classList.add('active');
      updateBodyScrollLock();
    }
  }

  function closeDeleteSelectedModal() {
    const modal = document.getElementById('deleteSelectedModal');
    if (modal) {
      modal.classList.remove('active');
      modal.setAttribute('hidden', 'hidden');
      updateBodyScrollLock();
    }
  }

  function setDeleteModalLoading(isLoading) {
    const confirm = document.querySelector('[data-action="confirm-delete-selected"]');
    if (confirm) {
      if (!confirm.dataset.originalText) {
        confirm.dataset.originalText = confirm.textContent || 'Delete';
      }
      confirm.disabled = Boolean(isLoading);
      confirm.textContent = isLoading ? 'Deleting…' : confirm.dataset.originalText;
    }
  }

  async function confirmDeleteSelected() {
    const ids = getSelectedPromptIds();
    if (!ids.length) {
      closeDeleteSelectedModal();
      return;
    }

    try {
      setDeleteModalLoading(true);
      const response = await fetchJson('/prompts/bulk', {
        method: 'DELETE',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ ids }),
      });

      if (response?.errors?.length) {
        throw new Error(response.errors.map((err) => `#${err.id}: ${err.error}`).join(', '));
      }

      if (window.NotificationService) {
        window.NotificationService.show(`Deleted ${ids.length} prompt${ids.length === 1 ? '' : 's'}`, 'success');
      }

      closeDeleteSelectedModal();
      clearSelection();
      await loadPrompts(paginationState.page);
    } catch (error) {
      console.error('Failed to delete prompts', error);
      if (window.NotificationService) {
        window.NotificationService.show(`Failed to delete prompts: ${error.message || error}`, 'error');
      }
    } finally {
      setDeleteModalLoading(false);
    }
  }

  function openAddTagsModal() {
    const ids = getSelectedPromptIds();
    if (!ids.length) {
      return;
    }

    addTagsDraft = [];
    renderAddTagsList();
    renderTagSuggestions('');

    const modal = document.getElementById('addTagsModal');
    if (modal) {
      modal.removeAttribute('hidden');
      modal.classList.add('active');
      updateBodyScrollLock();
    }

    const input = document.getElementById('addTagInput');
    if (input) {
      input.value = '';
      setTimeout(() => input.focus(), 50);
    }
  }

  function closeAddTagsModal() {
    const modal = document.getElementById('addTagsModal');
    if (modal) {
      modal.classList.remove('active');
      modal.setAttribute('hidden', 'hidden');
      updateBodyScrollLock();
    }
    addTagsDraft = [];
    renderAddTagsList();
    renderTagSuggestions('');
  }

  function renderAddTagsList() {
    const list = document.getElementById('addTagsList');
    if (!list) return;

    list.innerHTML = '';

    if (!addTagsDraft.length) {
      const empty = document.createElement('span');
      empty.className = 'blur-tag-empty';
      empty.textContent = 'No tags yet';
      list.appendChild(empty);
      return;
    }

    addTagsDraft.forEach((tag) => {
      const chip = document.createElement('span');
      chip.className = 'blur-tag-chip';
      chip.textContent = tag;

      const removeBtn = document.createElement('button');
      removeBtn.type = 'button';
      removeBtn.dataset.tag = tag;
      removeBtn.setAttribute('aria-label', `Remove ${tag}`);
      removeBtn.innerHTML = '&times;';

      chip.appendChild(removeBtn);
      list.appendChild(chip);
    });
  }

  function addTagToDraft(tag) {
    const normalized = (tag || '').trim().toLowerCase();
    if (!normalized) {
      return;
    }

    if (!addTagsDraft.includes(normalized)) {
      addTagsDraft.push(normalized);
      addTagsDraft = addTagsDraft.sort();
      renderAddTagsList();
    }
  }

  function removeAddTag(tag) {
    addTagsDraft = addTagsDraft.filter((entry) => entry !== tag);
    renderAddTagsList();
    renderTagSuggestions(document.getElementById('addTagInput')?.value || '');
  }

  function handleAddTagInput(rawValue) {
    const normalized = (rawValue || '').trim();
    if (!normalized) {
      return;
    }

    normalized
      .split(/[\s,]+/)
      .map((token) => token.trim().toLowerCase())
      .filter(Boolean)
      .forEach(addTagToDraft);

    renderTagSuggestions(document.getElementById('addTagInput')?.value || '');
  }

  function renderTagSuggestions(filterValue) {
    const container = document.getElementById('tagSuggestions');
    if (!container) return;

    container.innerHTML = '';

    const suggestions = collectTagSuggestions();
    const filter = (filterValue || '').trim().toLowerCase();

    const filtered = suggestions
      .filter((tag) => !addTagsDraft.includes(tag) && (!filter || tag.includes(filter)))
      .slice(0, 20);

    if (!filtered.length) {
      const empty = document.createElement('span');
      empty.className = 'blur-tag-empty';
      empty.textContent = filter ? 'No matches' : 'No tag suggestions available';
      container.appendChild(empty);
      return;
    }

    filtered.forEach((tag) => {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'tag-suggestion-chip';
      chip.dataset.tag = tag;
      chip.textContent = tag;
      container.appendChild(chip);
    });
  }

  function setAddTagsModalLoading(isLoading) {
    const applyButton = document.querySelector('[data-action="apply-add-tags"]');
    if (applyButton) {
      if (!applyButton.dataset.originalText) {
        applyButton.dataset.originalText = applyButton.textContent || 'Apply Tags';
      }
      applyButton.disabled = Boolean(isLoading);
      applyButton.textContent = isLoading ? 'Applying…' : applyButton.dataset.originalText;
    }
  }

  async function applyAddTagsToSelection() {
    if (!addTagsDraft.length) {
      if (window.NotificationService) {
        window.NotificationService.show('Add at least one tag before applying.', 'warning');
      }
      return;
    }

    const ids = getSelectedPromptIds();
    if (!ids.length) {
      closeAddTagsModal();
      return;
    }

    try {
      setAddTagsModalLoading(true);
      await Promise.all(ids.map(async (id) => {
        const prompt = findPromptById(id);
        if (!prompt) {
          return;
        }
        const existing = normalizePromptTags(prompt);
        const merged = Array.from(new Set([...existing, ...addTagsDraft]));
        const payload = { tags: merged };
        await fetchJson(`/prompts/${id}`, {
          method: 'PUT',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify(payload),
        });
      }));

      if (window.NotificationService) {
        window.NotificationService.show(`Added ${addTagsDraft.length} tag${addTagsDraft.length === 1 ? '' : 's'} to ${ids.length} prompt${ids.length === 1 ? '' : 's'}`, 'success');
      }

      closeAddTagsModal();
      await loadPrompts(paginationState.page);
      clearSelection();
    } catch (error) {
      console.error('Failed to add tags to prompts', error);
      if (window.NotificationService) {
        window.NotificationService.show(`Failed to add tags: ${error.message || error}`, 'error');
      }
    } finally {
      setAddTagsModalLoading(false);
    }
  }

  function exportSelectedPrompts() {
    const ids = getSelectedPromptIds();
    if (!ids.length) {
      return;
    }

    const promptsToExport = currentPromptList
      .filter((prompt) => ids.includes(Number(prompt.id)))
      .map((prompt) => ({
        id: prompt.id,
        prompt: prompt.prompt || prompt.positive_prompt || '',
        negative_prompt: prompt.negative_prompt || '',
        category: prompt.category || 'uncategorized',
        tags: normalizePromptTags(prompt),
        notes: prompt.notes || '',
        created_at: prompt.created_at,
        updated_at: prompt.updated_at,
      }));

    if (!promptsToExport.length) {
      if (window.NotificationService) {
        window.NotificationService.show('No prompts available to export on this page.', 'warning');
      }
      return;
    }

    const payload = {
      exported_at: new Date().toISOString(),
      count: promptsToExport.length,
      prompts: promptsToExport,
    };

    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `prompts_export_${Date.now()}.json`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);

    if (window.NotificationService) {
      window.NotificationService.show(`Exported ${promptsToExport.length} prompt${promptsToExport.length === 1 ? '' : 's'}`, 'success');
    }
  }

  function normalizeImportedPrompt(raw) {
    const promptText = raw?.prompt ?? raw?.positive_prompt ?? raw?.text ?? '';
    const negativeText = raw?.negative_prompt ?? raw?.negative ?? '';
    const category = raw?.category ?? 'uncategorized';
    const notes = raw?.notes ?? '';
    const rating = typeof raw?.rating === 'number' ? raw.rating : null;
    const tags = parseTagsValue(raw?.tags);

    return {
      prompt: promptText,
      positive_prompt: promptText,
      negative_prompt: negativeText,
      category,
      notes,
      tags,
      ...(rating !== null ? { rating } : {}),
    };
  }

  /**
   * Copy text to clipboard
   */
  async function copyToClipboard(textOrElement, type) {
    // Handle both text and element inputs
    let text;
    let label;

    if (typeof textOrElement === 'string') {
      // Direct text copy
      text = textOrElement;
    } else {
      // Element-based copy (legacy behavior)
      text = textOrElement.querySelector('.prompt-content-text')?.textContent || '';
      label = textOrElement.querySelector('.prompt-label');
    }

    try {
      await navigator.clipboard.writeText(text);

      // Show success feedback for element-based copy
      if (label) {
        const originalText = label.textContent;
        label.textContent = '✓ Copied!';

        setTimeout(() => {
          label.textContent = originalText;
        }, 2000);
      }

      // Show notification if available
      if (window.NotificationService) {
        const promptType = type === 'positive' ? 'Positive' : type === 'negative' ? 'Negative' : '';
        const message = promptType ? `${promptType} prompt copied!` : 'Copied to clipboard!';
        window.NotificationService.show(message, 'success');
      }
    } catch (error) {
      console.error('Failed to copy:', error);
      if (window.NotificationService) {
        window.NotificationService.show('Failed to copy to clipboard', 'error');
      }
    }
  }

  /**
   * Import prompts from file
   */
  function importPrompts() {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json';
    input.onchange = async (e) => {
      const file = e.target.files[0];
      if (file) {
        try {
          const text = await file.text();
          const payload = JSON.parse(text);

          let prompts = [];
          if (Array.isArray(payload?.prompts)) {
            prompts = payload.prompts;
          } else if (Array.isArray(payload)) {
            prompts = payload;
          }

          if (!prompts.length) {
            if (window.NotificationService) {
              window.NotificationService.show('No prompts found in file', 'warning');
            }
            return;
          }

          const normalized = prompts
            .map((prompt) => normalizeImportedPrompt(prompt))
            .filter((prompt) => prompt.positive_prompt);

          if (!normalized.length) {
            if (window.NotificationService) {
              window.NotificationService.show('No valid prompts to import', 'warning');
            }
            return;
          }

          const response = await fetchJson('/prompts/bulk', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
            },
            body: JSON.stringify({ prompts: normalized }),
          });

          const errors = Array.isArray(response?.errors) ? response.errors : [];
          const createdCount = Array.isArray(response?.created) ? response.created.length : 0;

          if (window.NotificationService) {
            if (createdCount > 0) {
              window.NotificationService.show(`Imported ${createdCount} prompt${createdCount === 1 ? '' : 's'}`, 'success');
            }
            if (errors.length) {
              const duplicateCount = errors.filter((err) => String(err.error).toLowerCase().includes('unique')).length;
              const failedCount = errors.length - duplicateCount;
              let message = '';
              if (duplicateCount) {
                message += `${duplicateCount} duplicate${duplicateCount === 1 ? '' : 's'} skipped. `;
              }
              if (failedCount) {
                message += `${failedCount} failed.`;
              }
              window.NotificationService.show(message.trim() || 'Some prompts could not be imported.', 'warning');
            }
          }

          await loadPrompts(1);
        } catch (error) {
          console.error('Import failed:', error);
          if (window.NotificationService) {
            window.NotificationService.show(`Failed to import prompts: ${error.message || error}`, 'error');
          }
        }
      }
    };
    input.click();
  }

  /**
   * Export prompts to file
   */
  function exportPrompts() {
    // TODO: Gather prompts from the page or API
    const prompts = {
      version: '1.0',
      prompts: [],
      exportDate: new Date().toISOString()
    };

    const blob = new Blob([JSON.stringify(prompts, null, 2)], {
      type: 'application/json'
    });

    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `prompts_${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);

    if (window.NotificationService) {
      window.NotificationService.show('Prompts exported successfully', 'success');
    }
  }

  /**
   * Create a new prompt
   */
  async function createPrompt(event) {
    event?.preventDefault();
    console.log('[createPrompt] Function called');

    const form = document.querySelector('.prompt-form');
    if (!form) {
      console.error('[createPrompt] Form not found');
      return;
    }

    // Gather form data
    const positivePrompt = form.querySelector('#positivePrompt')?.value?.trim() || '';
    const negativePrompt = form.querySelector('#negativePrompt')?.value?.trim() || '';
    const rawTags = form.querySelector('#promptTags')?.value || '';
    const category = form.querySelector('#promptCategory')?.value?.trim() || '';

    console.log('[createPrompt] Form data:', { positivePrompt, negativePrompt, rawTags, category });

    // Generate title from positive prompt (first 50 chars or up to first comma/period)
    let title = positivePrompt;
    const punctIndex = Math.min(
      title.indexOf(',') > 0 ? title.indexOf(',') : 50,
      title.indexOf('.') > 0 ? title.indexOf('.') : 50,
      50
    );
    title = title.substring(0, punctIndex).trim();
    if (!title) {
      title = 'Untitled Prompt';
    }

    // Validate
    if (!positivePrompt) {
      console.warn('[createPrompt] Validation failed: no positive prompt');
      if (window.NotificationService) {
        window.NotificationService.show('Positive prompt text is required', 'warning');
      }
      return;
    }

    const tagList = rawTags
      .split(',')
      .map((tag) => tag.trim())
      .filter(Boolean);

    const payload = {
      title,
      positivePrompt,
      negativePrompt,
      tags: tagList,
      model: currentModel,
    };

    // Add category if provided
    if (category) {
      payload.category = category;
    }

    console.log('[createPrompt] Payload to send:', payload);
    console.log('[createPrompt] API base candidates:', API_BASE_CANDIDATES);
    console.log('[createPrompt] Current apiBase:', apiBase);

    try {
      const result = await fetchJson('/prompts', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      });

      console.log('[createPrompt] Success! Response:', result);
      console.log('[createPrompt] Response data:', result.data);
      console.log('[createPrompt] Created prompt ID:', result.data?.id);

      hideAddPromptModal();
      form.reset();

      console.log('[createPrompt] Reloading prompts, stats, and categories...');
      const reloadResults = await Promise.allSettled([
        loadPrompts(1),
        loadStats(),
        loadCategories(),
      ]);
      console.log('[createPrompt] Reload results:', reloadResults);

      if (window.NotificationService) {
        window.NotificationService.show('Prompt created successfully', 'success');
      }
    } catch (error) {
      console.error('[createPrompt] Failed to create prompt:', error);
      console.error('[createPrompt] Error details:', error.message, error.stack);
      if (window.NotificationService) {
        window.NotificationService.show('Failed to create prompt: ' + error.message, 'error');
      }
    }
  }

  async function saveEditPrompt() {
    const form = document.getElementById('editPromptForm');
    if (!form || editingPromptId === null) {
      return;
    }

    const positiveField = form.querySelector('#editPositivePrompt');
    const negativeField = form.querySelector('#editNegativePrompt');
    const categoryField = form.querySelector('#editPromptCategory');
    const tagsField = form.querySelector('#editPromptTags');
    const ratingField = form.querySelector('#editPromptRating');
    const notesField = form.querySelector('#editPromptNotes');

    const positivePrompt = positiveField?.value?.trim() || '';
    if (!positivePrompt) {
      if (window.NotificationService) {
        window.NotificationService.show('Positive prompt text is required', 'warning');
      }
      positiveField?.focus();
      return;
    }

    const negativePrompt = negativeField?.value?.trim() || '';
    const category = categoryField?.value?.trim() || 'uncategorized';
    const tagsValue = tagsField?.value || '';
    const ratingValue = ratingField?.value;
    const notes = notesField?.value?.trim() || '';

    const tagsArray = parseTagsInput(tagsValue);
    const tagsSerialized = tagsArray.length ? JSON.stringify(tagsArray) : '[]';

    let rating = null;
    if (ratingValue !== '' && ratingValue !== null && ratingValue !== undefined) {
      const parsedRating = Number.parseInt(ratingValue, 10);
      if (!Number.isNaN(parsedRating)) {
        rating = Math.max(0, Math.min(5, parsedRating));
      }
    }

    const payload = {
      prompt: positivePrompt,
      positive_prompt: positivePrompt,
      negative_prompt: negativePrompt,
      category,
      notes,
      tags: tagsArray,
    };

    if (rating !== null) {
      payload.rating = rating;
    }

    try {
      setEditModalLoading(true, 'save');
      const response = await fetchJson(`/prompts/${editingPromptId}`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      });

      const updatedPrompt = response?.data ?? response;

      if (window.NotificationService) {
        window.NotificationService.show('Prompt updated successfully', 'success');
      }

      hideEditPromptModal();
      await Promise.all([
        loadPrompts(paginationState.page),
        loadCategories(),
      ]);

      if (updatedPrompt && searchTermLower) {
        // Ensure highlights refresh with the current search term
        handleSearchInput(searchTerm);
      }
    } catch (error) {
      console.error('Failed to update prompt', error);
      if (window.NotificationService) {
        window.NotificationService.show('Failed to update prompt', 'error');
      }
    } finally {
      setEditModalLoading(false);
    }
  }

  // Make functions available globally for any legacy code
  window.toggleMobileMenu = toggleMobileMenu;
  window.showAddPromptModal = showAddPromptModal;
  window.hideAddPromptModal = hideAddPromptModal;
  window.switchModel = switchModel;
  window.copyToClipboard = copyToClipboard;
  window.importPrompts = importPrompts;
  window.exportPrompts = exportPrompts;
  window.createPrompt = createPrompt;

  // Initialize when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
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