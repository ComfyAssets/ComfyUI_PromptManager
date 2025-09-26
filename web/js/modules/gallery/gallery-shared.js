/**
 * GalleryShared Module
 * Reusable helpers for rendering gallery layouts and integrating ViewerJS
 * Used by the main gallery page and embedded prompt galleries.
 */
(function() {
  'use strict';

  if (typeof window === 'undefined') {
    return;
  }

  if (window.GalleryShared) {
    return;
  }

  const containerState = new WeakMap();
  let autoIdCounter = 0;

  function ensureContainerId(container) {
    if (!container.id) {
      container.id = `gallery-shared-${Date.now()}-${autoIdCounter++}`;
    }
    return container.id;
  }

  function getState(container) {
    let state = containerState.get(container);
    if (!state) {
      state = {
        masonry: null,
        integrationId: null,
      };
      containerState.set(container, state);
    }
    return state;
  }

  function escapeHtml(text) {
    if (text === undefined || text === null) {
      return '';
    }
    const div = document.createElement('div');
    div.textContent = String(text);
    return div.innerHTML;
  }

  function formatFileSize(bytes) {
    if (!Number.isFinite(bytes) || bytes <= 0) {
      return 'Unknown';
    }
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), sizes.length - 1);
    const value = bytes / Math.pow(1024, i);
    return `${value.toFixed(i === 0 ? 0 : 1)} ${sizes[i]}`;
  }

  function formatTimeAgo(dateString) {
    if (!dateString) {
      return 'Unknown';
    }

    const date = new Date(dateString);
    if (Number.isNaN(date.getTime())) {
      return 'Unknown';
    }

    const now = new Date();
    const diff = now.getTime() - date.getTime();

    const seconds = Math.floor(diff / 1000);
    const minutes = Math.floor(seconds / 60);
    const hours = Math.floor(minutes / 60);
    const days = Math.floor(hours / 24);

    if (days > 7) {
      return date.toLocaleDateString();
    }
    if (days > 0) {
      return `${days} day${days > 1 ? 's' : ''} ago`;
    }
    if (hours > 0) {
      return `${hours} hour${hours > 1 ? 's' : ''} ago`;
    }
    if (minutes > 0) {
      return `${minutes} minute${minutes > 1 ? 's' : ''} ago`;
    }
    return 'Just now';
  }

  function parseJson(value) {
    if (!value) {
      return null;
    }
    if (typeof value === 'object') {
      return value;
    }
    try {
      return JSON.parse(value);
    } catch (error) {
      console.warn('GalleryShared: failed to parse metadata', error);
      return null;
    }
  }

  function getModelDisplayName(modelPath) {
    if (!modelPath) {
      return 'Unknown';
    }
    const parts = String(modelPath).split(/[\\/]/);
    const filename = parts[parts.length - 1];
    return filename.replace(/\.(ckpt|safetensors|pt)$/i, '').replace(/_/g, ' ');
  }

  function extractModelName(item) {
    if (!item) {
      return 'Unknown';
    }

    const metadataSources = [item.metadata, item.workflow, item.parameters, item.workflow_data]
      .filter((source) => source !== undefined && source !== null);

    for (const source of metadataSources) {
      const data = parseJson(source);
      if (!data) {
        continue;
      }

      const nodes = Array.isArray(data) ? data : Object.values(data);
      for (const node of nodes) {
        if (!node || typeof node !== 'object') {
          continue;
        }

        const inputs = node.inputs || {};
        const classType = node.class_type || node.type;
        if ((classType === 'CheckpointLoaderSimple' || classType === 'CheckpointLoader') && inputs.ckpt_name) {
          return getModelDisplayName(inputs.ckpt_name);
        }
      }
    }

    return 'Unknown';
  }

  function computeDimensions(item) {
    if (item.dimensions) {
      return item.dimensions;
    }
    const width = item.width || item.metadata?.width;
    const height = item.height || item.metadata?.height;
    if (width && height) {
      return `${width}x${height}`;
    }
    return 'Unknown';
  }

  function computeSize(item) {
    if (item.size) {
      return item.size;
    }
    if (Number.isFinite(item.file_size)) {
      return formatFileSize(item.file_size);
    }
    return 'Unknown';
  }

  function applyViewModeToContainer(container, mode) {
    if (!container) {
      return 'grid';
    }

    const normalized = ['grid', 'masonry', 'list'].includes(mode) ? mode : 'grid';
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

  function initializeMasonryLayout(container) {
    if (typeof Masonry === 'undefined') {
      return null;
    }

    if (!container.querySelector('.masonry-sizer')) {
      const sizer = document.createElement('div');
      sizer.className = 'masonry-sizer';
      container.prepend(sizer);
    }

    const instance = new Masonry(container, {
      itemSelector: '.gallery-item',
      columnWidth: '.masonry-sizer',
      percentPosition: true,
      gutter: 24,
    });

    container.querySelectorAll('img').forEach((img) => {
      if (img.complete) {
        instance.layout();
      } else {
        img.addEventListener('load', () => {
          instance.layout();
        }, { once: true });
      }
    });

    return instance;
  }

  function attachMediaOverlay(imageWrapper, mediaType) {
    if (!imageWrapper) {
      return;
    }

    const overlay = document.createElement('div');
    overlay.className = `media-overlay media-overlay-${mediaType}`;
    overlay.innerHTML = mediaType === 'video'
      ? '<i class="fa-solid fa-circle-play" aria-hidden="true"></i>'
      : '<i class="fa-solid fa-wave-square" aria-hidden="true"></i>';
    imageWrapper.appendChild(overlay);
  }

  function createGalleryItem(item, viewMode = 'grid', options = {}) {
    const includeActions = options.includeActions !== false;
    const element = document.createElement('div');
    element.className = 'gallery-item';
    if (viewMode === 'list') {
      element.classList.add('list-item');
    }
    if (options.active) {
      element.classList.add('is-active');
    }

    const identifier = item.id || item.image_id || item.filename || `item-${autoIdCounter++}`;
    element.dataset.itemId = identifier;

    const modelName = extractModelName(item);
    const placeholderImage = '/prompt_manager/images/placeholder.png';
    const audioPlaceholder = '/prompt_manager/images/wave-form.svg';

    const mediaType = item.media_type || item.type || 'image';
    let thumbnailUrl = item.thumbnail_url || item.thumbnail || item.preview || item.src || placeholderImage;
    if (mediaType === 'audio') {
      thumbnailUrl = audioPlaceholder;
    }
    const fullMediaUrl = item.image_url || item.url || item.path || item.fullSrc || thumbnailUrl;

    const title = item.filename || item.file_name || item.name || 'Untitled';
    const dimensions = computeDimensions(item);
    const size = computeSize(item);
    const timeAgo = formatTimeAgo(item.generation_time || item.created_at || item.timestamp);

    element.innerHTML = `
      <div class="gallery-image${options.blur ? ' blur-sensitive is-blurred' : ''}">
        <img src="${thumbnailUrl}"
             data-full-src="${fullMediaUrl}"
             alt="${escapeHtml(title)}"
             loading="lazy"
             data-placeholder="${placeholderImage}" />
      </div>
      <div class="gallery-info">
        <div class="gallery-title">${escapeHtml(title)}</div>
        <div class="gallery-meta">
          <span class="chip">${escapeHtml(modelName)}</span>
          <span class="chip">${escapeHtml(dimensions)}</span>
          <span class="chip">${escapeHtml(size)}</span>
          ${mediaType !== 'image' ? `<span class="chip chip-media">${escapeHtml(mediaType)}</span>` : ''}
        </div>
        <div class="gallery-timestamp">${escapeHtml(timeAgo)}</div>
        ${includeActions ? `
        <div class="gallery-actions">
          <button class="btn btn-ghost" title="View Details" data-action="gallery-view" data-id="${escapeHtml(identifier)}">
            <i class="fa-solid fa-info-circle"></i>
          </button>
          <button class="btn btn-ghost" title="Copy Prompt" data-action="gallery-copy" data-id="${escapeHtml(identifier)}">
            <i class="fa-solid fa-copy"></i>
          </button>
          <button class="btn btn-ghost" title="Delete" data-action="gallery-delete" data-id="${escapeHtml(identifier)}">
            <i class="fa-solid fa-trash"></i>
          </button>
        </div>
        ` : ''}
      </div>
    `;

    if (viewMode === 'list') {
      const imageEl = element.querySelector('.gallery-image');
      if (imageEl) {
        imageEl.classList.add('list-image');
      }
    }

    const img = element.querySelector('.gallery-image img');
    if (img) {
      img.dataset.fullSrc = fullMediaUrl;
      img.dataset.mediaType = mediaType;

      img.addEventListener('error', () => {
        if (img.dataset.fallbackTried === '1') {
          img.src = img.dataset.placeholder || placeholderImage;
          return;
        }
        img.dataset.fallbackTried = '1';
        img.src = fullMediaUrl || thumbnailUrl || placeholderImage;
      });

      if (typeof options.onImageClick === 'function') {
        img.addEventListener('click', (event) => {
          options.onImageClick(event, element, item);
        });
      }
    }

    if (mediaType === 'video') {
      attachMediaOverlay(element.querySelector('.gallery-image'), 'video');
    } else if (mediaType === 'audio') {
      attachMediaOverlay(element.querySelector('.gallery-image'), 'audio');
    }

    return element;
  }

  function integrateViewer(container, selectors, state, viewerConfig) {
    if (!window.ViewerIntegration || typeof window.ViewerIntegration.initGallery !== 'function') {
      return null;
    }

    if (state.integrationId && typeof window.ViewerIntegration.destroy === 'function') {
      window.ViewerIntegration.destroy(state.integrationId);
      state.integrationId = null;
    }

    const containerId = ensureContainerId(container);
    const resolvedSelectors = Object.assign({
      gallery: `#${containerId}`,
      galleryImage: `#${containerId} .gallery-image img`,
    }, selectors || {});

    const integrationId = window.ViewerIntegration.initGallery({
      selectors: resolvedSelectors,
      viewer: viewerConfig?.viewer,
      filmstrip: viewerConfig?.filmstrip,
      metadata: viewerConfig?.metadata,
    });

    state.integrationId = integrationId;
    return integrationId;
  }

  function renderItems(container, items, options = {}) {
    if (!container) {
      return { viewMode: 'grid', integrationId: null, elements: [] };
    }

    const state = getState(container);
    const viewMode = applyViewModeToContainer(container, options.viewMode || container.dataset.viewMode || 'grid');

    container.innerHTML = '';
    const elements = [];
    const includeActions = options.includeActions !== false;

    items.forEach((item, index) => {
      const element = createGalleryItem(item, viewMode, {
        includeActions,
        active: options.activeIndex === index,
        blur: typeof options.getBlur === 'function' ? options.getBlur(item, index) : false,
        onImageClick: options.onImageClick,
      });
      element.dataset.galleryIndex = index;
      container.appendChild(element);
      elements.push(element);
    });

    if (viewMode === 'masonry') {
      state.masonry = initializeMasonryLayout(container);
    } else if (state.masonry && typeof state.masonry.destroy === 'function') {
      state.masonry.destroy();
      state.masonry = null;
    }

    let integrationId = null;
    if (options.enableViewer !== false) {
      integrationId = integrateViewer(container, options.selectors, state, options.viewerConfig);
    }

    if (!integrationId) {
      elements.forEach((element) => {
        const img = element.querySelector('.gallery-image img');
        if (!img) {
          return;
        }
        if (img.dataset.clickBound === '1') {
          return;
        }
        img.dataset.clickBound = '1';
        img.addEventListener('click', () => {
          const fullSrc = img.getAttribute('data-full-src') || img.src;
          if (fullSrc) {
            window.open(fullSrc, '_blank', 'noopener');
          }
        });
      });
    }

    if (typeof options.afterRender === 'function') {
      options.afterRender(elements, items);
    }

    return { viewMode, integrationId, elements };
  }

  function highlightIndex(container, index) {
    if (!container) {
      return;
    }
    const items = container.querySelectorAll('.gallery-item');
    items.forEach((element, idx) => {
      element.classList.toggle('is-active', idx === index);
    });
  }

  function teardown(container) {
    if (!container) {
      return;
    }

    const state = containerState.get(container);
    if (!state) {
      return;
    }

    if (state.masonry && typeof state.masonry.destroy === 'function') {
      state.masonry.destroy();
    }

    if (state.integrationId && window.ViewerIntegration && typeof window.ViewerIntegration.destroy === 'function') {
      window.ViewerIntegration.destroy(state.integrationId);
    }

    containerState.delete(container);
  }

  window.GalleryShared = {
    renderItems,
    createGalleryItem,
    applyViewModeToContainer,
    initializeMasonryLayout,
    integrateViewer,
    highlightIndex,
    teardown,
    escapeHtml,
    formatTimeAgo,
    formatFileSize,
    ensureContainerId,
  };
})();

