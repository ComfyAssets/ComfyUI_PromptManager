/**
 * Gallery Page JavaScript
 * Handles all gallery page functionality with proper separation of concerns
 */

(function() {
  'use strict';

  if (typeof window === 'undefined') {
    return;
  }
  const pmActive = (window.location?.pathname || '').includes('/prompt_manager');
  if (!pmActive) {
    console.info('[PromptManager] gallery-page skipped outside PromptManager UI context');
    return;
  }

  // State management
  let currentView = 'grid';
  let lightboxImage = null;

  /**
   * Initialize gallery page
   */
  function init() {
    attachEventListeners();
    setupGalleryItems();
    setupLightbox();
    console.log('Gallery page initialized');
  }

  /**
   * Attach event listeners to elements
   */
  function attachEventListeners() {
    // Upload button
    const uploadBtn = document.querySelector('[data-action="upload-image"]');
    if (uploadBtn) {
      uploadBtn.addEventListener('click', uploadImage);
    }

    // View mode buttons
    document.querySelectorAll('.view-mode-btn').forEach(btn => {
      btn.addEventListener('click', function() {
        const mode = this.dataset.mode || this.textContent.toLowerCase();
        setViewMode(mode);
      });
    });

    // Keyboard shortcuts
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && lightboxImage) {
        closeLightbox();
      }
      if (lightboxImage) {
        if (e.key === 'ArrowLeft') {
          navigateLightbox('prev');
        } else if (e.key === 'ArrowRight') {
          navigateLightbox('next');
        }
      }
    });
  }

  /**
   * Setup gallery items
   */
  function setupGalleryItems() {
    // Disabled - ViewerJS integration handles image viewing now
    // document.querySelectorAll('.gallery-item').forEach((item, index) => {
    //   item.addEventListener('click', function() {
    //     const imageSrc = this.dataset.image ||
    //                     this.querySelector('img')?.src ||
    //                     `sample${index + 1}.jpg`;
    //     openLightbox(imageSrc);
    //   });
    // });
  }

  /**
   * Setup lightbox
   */
  function setupLightbox() {
    const lightbox = document.getElementById('lightbox');
    if (lightbox) {
      // Close on backdrop click
      lightbox.addEventListener('click', (e) => {
        if (e.target === lightbox) {
          closeLightbox();
        }
      });
    }

    // Lightbox content should not close on click
    const lightboxContent = document.querySelector('.lightbox-content');
    if (lightboxContent) {
      lightboxContent.addEventListener('click', (e) => {
        e.stopPropagation();
      });
    }

    // Close button
    const closeBtn = document.querySelector('.lightbox-close');
    if (closeBtn) {
      closeBtn.addEventListener('click', closeLightbox);
    }

    // Navigation buttons
    const prevBtn = document.querySelector('.lightbox-prev');
    if (prevBtn) {
      prevBtn.addEventListener('click', () => navigateLightbox('prev'));
    }

    const nextBtn = document.querySelector('.lightbox-next');
    if (nextBtn) {
      nextBtn.addEventListener('click', () => navigateLightbox('next'));
    }
  }

  /**
   * Upload image
   */
  function uploadImage() {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/*';
    input.multiple = true;

    input.onchange = async (e) => {
      const files = Array.from(e.target.files);
      if (files.length === 0) return;

      try {
        // TODO: Implement actual upload logic
        console.log('Uploading files:', files);

        if (window.NotificationService) {
          window.NotificationService.show(
            `Uploading ${files.length} image${files.length > 1 ? 's' : ''}...`,
            'info'
          );
        }

        // Simulate upload delay
        setTimeout(() => {
          if (window.NotificationService) {
            window.NotificationService.show('Images uploaded successfully', 'success');
          }
        }, 1500);
      } catch (error) {
        console.error('Upload failed:', error);
        if (window.NotificationService) {
          window.NotificationService.show('Failed to upload images', 'error');
        }
      }
    };

    input.click();
  }

  /**
   * Set view mode
   */
  function setViewMode(mode) {
    currentView = mode;

    // Update button states
    document.querySelectorAll('.view-mode-btn').forEach(btn => {
      const btnMode = btn.dataset.mode || btn.textContent.toLowerCase();
      btn.classList.toggle('active', btnMode === mode);
    });

    // Update gallery container
    const gallery = document.querySelector('.gallery-container');
    if (gallery) {
      const viewClasses = ['grid-view', 'masonry-view', 'list-view'];
      viewClasses.forEach(cls => gallery.classList.remove(cls));
      gallery.classList.add('gallery-grid');
      gallery.classList.add(`${mode}-view`);
      gallery.dataset.viewMode = mode;
    }

    // Store preference in PromptManager settings (not standalone key)
    try {
      const settings = JSON.parse(localStorage.getItem('promptManagerSettings') || '{}');
      settings.galleryDefaultView = mode;
      localStorage.setItem('promptManagerSettings', JSON.stringify(settings));
    } catch (e) {
      console.warn('Failed to save gallery view preference:', e);
    }

    console.log('View mode changed to:', mode);
  }

  /**
   * Open lightbox
   */
  function openLightbox(imageSrc) {
    const lightbox = document.getElementById('lightbox');
    const lightboxImg = document.querySelector('.lightbox-img');

    if (!lightbox || !lightboxImg) return;

    lightboxImage = imageSrc;
    lightboxImg.src = imageSrc;
    lightbox.classList.add('active');
    document.body.style.overflow = 'hidden';

    // Update metadata
    updateLightboxMetadata(imageSrc);
  }

  /**
   * Close lightbox
   */
  function closeLightbox(event) {
    // If event is provided and not from close button/backdrop, ignore
    if (event && event.target &&
        !event.target.classList.contains('lightbox') &&
        !event.target.classList.contains('lightbox-close')) {
      return;
    }

    const lightbox = document.getElementById('lightbox');
    if (lightbox) {
      lightbox.classList.remove('active');
      document.body.style.overflow = '';
      lightboxImage = null;
    }
  }

  /**
   * Navigate lightbox images
   */
  function navigateLightbox(direction) {
    const items = document.querySelectorAll('.gallery-item');
    if (items.length === 0) return;

    // Find current index
    let currentIndex = -1;
    items.forEach((item, index) => {
      const imgSrc = item.dataset.image ||
                    item.querySelector('img')?.src ||
                    `sample${index + 1}.jpg`;
      if (imgSrc === lightboxImage) {
        currentIndex = index;
      }
    });

    if (currentIndex === -1) return;

    // Calculate new index
    let newIndex;
    if (direction === 'prev') {
      newIndex = currentIndex > 0 ? currentIndex - 1 : items.length - 1;
    } else {
      newIndex = currentIndex < items.length - 1 ? currentIndex + 1 : 0;
    }

    // Get new image
    const newItem = items[newIndex];
    const newImage = newItem.dataset.image ||
                    newItem.querySelector('img')?.src ||
                    `sample${newIndex + 1}.jpg`;

    // Update lightbox
    openLightbox(newImage);
  }

  /**
   * Update lightbox metadata
   */
  function updateLightboxMetadata(imageSrc) {
    // Extract filename
    const filename = imageSrc.split('/').pop();

    // Update metadata display
    const metadataElement = document.querySelector('.lightbox-metadata');
    if (metadataElement) {
      // TODO: Fetch actual metadata
      metadataElement.innerHTML = `
        <div><strong>Filename:</strong> ${filename}</div>
        <div><strong>Size:</strong> 1920x1080</div>
        <div><strong>Date:</strong> ${new Date().toLocaleDateString()}</div>
      `;
    }
  }

  /**
   * Load saved preferences
   */
  function loadPreferences() {
    // Read from PromptManager settings (not standalone key)
    try {
      const settings = JSON.parse(localStorage.getItem('promptManagerSettings') || '{}');
      const savedView = settings.galleryDefaultView;
      if (savedView) {
        setViewMode(savedView);
      }
    } catch (e) {
      console.warn('Failed to load gallery view preference:', e);
    }
  }

  // Make functions available globally for any legacy code
  window.uploadImage = uploadImage;
  window.setViewMode = setViewMode;
  window.openLightbox = openLightbox;
  window.closeLightbox = closeLightbox;

  // Initialize when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      init();
      loadPreferences();
    });
  } else {
    init();
    loadPreferences();
  }
})();
