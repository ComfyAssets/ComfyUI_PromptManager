        class GalleryManager {
            constructor() {
                this.images = [];
                this.currentPage = 1;
                this.limit = 100;
                this.total = 0;
                this.viewMode = 'grid'; // 'grid' or 'list'
                this.viewer = null;
                
                this.initializeEventListeners();
                this.loadImages();
                
                // Check thumbnails at startup if enabled
                this.checkThumbnailsAtStartup();
            }

            initializeEventListeners() {
                document.getElementById('refreshBtn').addEventListener('click', () => this.loadImages());
                document.getElementById('limitSelector').addEventListener('change', (e) => this.changeLimit(parseInt(e.target.value)));
                document.getElementById('gridViewBtn').addEventListener('click', () => this.setViewMode('grid'));
                document.getElementById('listViewBtn').addEventListener('click', () => this.setViewMode('list'));
                document.getElementById('settingsBtn').addEventListener('click', () => this.showSettings());
                document.getElementById('autoTagBtn').addEventListener('click', () => this.showAutoTagModal());
                document.getElementById('addPromptBtn').addEventListener('click', () => this.showAddPromptModal());
                document.getElementById('retryBtn').addEventListener('click', () => this.loadImages());
                document.getElementById('prevPageBtn').addEventListener('click', () => this.previousPage());
                document.getElementById('nextPageBtn').addEventListener('click', () => this.nextPage());
                document.getElementById('pageInput').addEventListener('change', (e) => this.goToPage(parseInt(e.target.value)));
                document.getElementById('pageInput').addEventListener('keypress', (e) => {
                    if (e.key === 'Enter') {
                        this.goToPage(parseInt(e.target.value));
                    }
                });
                
                // Settings modal event listeners
                document.getElementById('closeSettingsBtn').addEventListener('click', () => this.hideSettings());
                document.getElementById('saveSettingsBtn').addEventListener('click', () => this.saveSettings());
                document.getElementById('resetSettingsBtn').addEventListener('click', () => this.resetSettings());
                document.getElementById('generateThumbnailsBtn').addEventListener('click', () => this.generateThumbnails());
                document.getElementById('clearCacheBtn').addEventListener('click', () => this.clearCache());
                document.getElementById('rescanFolderBtn').addEventListener('click', () => this.rescanFolder());
                document.getElementById('scanDuplicatesBtn').addEventListener('click', () => this.scanDuplicates());
                
                // Duplicates modal event listeners
                document.getElementById('closeDuplicatesBtn').addEventListener('click', () => this.hideDuplicatesModal());
                document.getElementById('selectAllBtn').addEventListener('click', () => this.selectAllDuplicates());
                document.getElementById('removeDuplicatesBtn').addEventListener('click', () => this.removeDuplicates());
                
                // Close modal on outside click
                document.getElementById('settingsModal').addEventListener('click', (e) => {
                    if (e.target.id === 'settingsModal') {
                        this.hideSettings();
                    }
                });
                
                document.getElementById('duplicatesModal').addEventListener('click', (e) => {
                    if (e.target.id === 'duplicatesModal') {
                        this.hideDuplicatesModal();
                    }
                });

                // Auto Tag modal event listeners
                document.getElementById('startAutoTagBtn').addEventListener('click', () => this.startAutoTag());
                document.getElementById('startReviewBtn').addEventListener('click', () => this.startReview());
                document.getElementById('skipReviewBtn').addEventListener('click', () => this.skipReviewImage());
                document.getElementById('applyReviewBtn').addEventListener('click', () => this.applyReviewTags());
                document.getElementById('cancelAutoTagBtn').addEventListener('click', () => this.cancelAutoTag());
                document.getElementById('cancelDownloadBtn').addEventListener('click', () => this.cancelDownload());
                document.getElementById('unloadModelBtn').addEventListener('click', () => this.unloadModel());

                // Add Prompt modal event listeners
                document.getElementById('saveNewPromptBtn').addEventListener('click', () => this.saveNewPrompt());
                document.getElementById('addPromptTagInput').addEventListener('input', (e) => this.handleTagInput(e));
                document.getElementById('addPromptTagInput').addEventListener('keydown', (e) => this.handleTagKeydown(e));
                document.getElementById('clearRatingBtn').addEventListener('click', () => this.clearRating());

                // Rating stars click handlers
                document.querySelectorAll('#addPromptRating .rating-star').forEach(star => {
                    star.addEventListener('click', (e) => this.setRating(parseInt(e.target.dataset.rating)));
                });

                // Close AutoTag modals on outside click
                ['autoTagModal', 'autoTagReviewModal', 'autoTagProgressModal', 'autoTagDownloadModal', 'addPromptModal'].forEach(modalId => {
                    document.getElementById(modalId).addEventListener('click', (e) => {
                        if (e.target.id === modalId) {
                            this.hideModal(modalId);
                        }
                    });
                });
            }

            async loadImages() {
                this.showLoading();
                
                try {
                    const offset = (this.currentPage - 1) * this.limit;
                    const response = await fetch(`/prompt_manager/images/output?limit=${this.limit}&offset=${offset}`);
                    
                    if (!response.ok) {
                        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                    }
                    
                    const data = await response.json();
                    
                    if (data.success) {
                        this.images = data.images;
                        this.total = data.total;
                        this.updateStats();
                        this.renderGallery();
                        this.updatePagination();
                        document.getElementById('loadingStatus').textContent = 'Loaded';
                    } else {
                        throw new Error(data.error || 'Failed to load images');
                    }
                    
                } catch (error) {
                    console.error('Error loading images:', error);
                    this.showError(error.message);
                }
            }

            showLoading() {
                document.getElementById('loadingState').classList.remove('hidden');
                document.getElementById('errorState').classList.add('hidden');
                document.getElementById('galleryGrid').classList.add('hidden');
                document.getElementById('galleryList').classList.add('hidden');
                document.getElementById('paginationControls').classList.add('hidden');
                document.getElementById('loadingStatus').textContent = 'Loading...';
            }

            showError(message) {
                document.getElementById('loadingState').classList.add('hidden');
                document.getElementById('errorState').classList.remove('hidden');
                document.getElementById('galleryGrid').classList.add('hidden');
                document.getElementById('galleryList').classList.add('hidden');
                document.getElementById('paginationControls').classList.add('hidden');
                document.getElementById('errorMessage').textContent = message;
                document.getElementById('loadingStatus').textContent = 'Error';
            }

            updateStats() {
                const start = (this.currentPage - 1) * this.limit + 1;
                const end = Math.min(this.currentPage * this.limit, this.total);
                
                document.getElementById('showingStart').textContent = this.images.length > 0 ? start : 0;
                document.getElementById('showingEnd').textContent = end;
                document.getElementById('totalImages').textContent = this.total;
            }

            setViewMode(mode) {
                this.viewMode = mode;
                
                // Update button states
                const gridBtn = document.getElementById('gridViewBtn');
                const listBtn = document.getElementById('listViewBtn');
                
                if (mode === 'grid') {
                    gridBtn.classList.remove('bg-pm-input');
                    gridBtn.classList.add('bg-pm-accent');
                    listBtn.classList.remove('bg-pm-accent');
                    listBtn.classList.add('bg-pm-input');
                } else {
                    listBtn.classList.remove('bg-pm-input');
                    listBtn.classList.add('bg-pm-accent');
                    gridBtn.classList.remove('bg-pm-accent');
                    gridBtn.classList.add('bg-pm-input');
                }
                
                this.renderGallery();
            }

            renderGallery() {
                if (this.images.length === 0) {
                    this.showError('No images found in the output folder');
                    return;
                }

                document.getElementById('loadingState').classList.add('hidden');
                document.getElementById('errorState').classList.add('hidden');

                if (this.viewMode === 'grid') {
                    this.renderGridView();
                } else {
                    this.renderListView();
                }

                // Initialize ViewerJS after rendering
                setTimeout(() => this.initializeViewer(), 100);
                
                // Add simple fallback click handlers
                this.addFallbackClickHandlers();
            }

            renderGridView() {
                const grid = document.getElementById('galleryGrid');
                const list = document.getElementById('galleryList');
                
                list.classList.add('hidden');
                grid.classList.remove('hidden');

                grid.innerHTML = this.images.map((image, index) => {
                    // Use thumbnail for display if available, fallback to original
                    const displayUrl = image.thumbnail_url || image.url;
                    const hasThumb = !!image.thumbnail_url;
                    const isVideo = image.is_video || false;
                    const mediaType = image.media_type || 'image';
                    
                    return `
                    <div class="image-item bg-pm-surface rounded-pm-md overflow-hidden border border-pm hover:border-pm cursor-pointer group">
                        <div class="aspect-square bg-pm-primary overflow-hidden relative">
                            <img src="${displayUrl}"
                                 alt="${this.escapeHtml(image.filename)}"
                                 class="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
                                 loading="lazy"
                                 data-original="${image.url}"
                                 data-thumbnail="${image.thumbnail_url || ''}"
                                 data-caption="${this.escapeHtml(this.formatImageCaption(image))}"
                                 data-media-type="${mediaType}"
                                 data-is-video="${isVideo}"
                                 onerror="this.style.display='none'; this.nextElementSibling.style.display='flex'">
                            <!-- Fallback for failed thumbnails -->
                            <div class="hidden absolute inset-0 bg-pm-surface text-pm-secondary flex items-center justify-center">
                                <div class="text-center">
                                    <div class="text-lg mb-1">${isVideo ? '🎬' : '🖼️'}</div>
                                    <div class="text-xs">Failed to load</div>
                                </div>
                            </div>
                            <div class="absolute inset-0 bg-black/0 group-hover:bg-black/20 transition-opacity duration-300 flex items-center justify-center">
                                ${isVideo ? `
                                    <div class="w-12 h-12 bg-black/60 rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity duration-300">
                                        <svg class="w-6 h-6 text-pm ml-1" fill="currentColor" viewBox="0 0 24 24">
                                            <path d="M8 5v14l11-7z"/>
                                        </svg>
                                    </div>
                                ` : `
                                    <svg class="w-8 h-8 text-pm opacity-0 group-hover:opacity-100 transition-opacity duration-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path>
                                    </svg>
                                `}
                            </div>
                            ${hasThumb ? '<div class="absolute top-2 right-2 w-3 h-3 bg-pm-success rounded-full" title="Thumbnail available"></div>' : ''}
                            ${isVideo ? '<div class="absolute top-2 left-2 px-2 py-1 bg-pm-error text-pm text-xs rounded" title="Video file">VIDEO</div>' : ''}
                        </div>
                        <div class="p-2">
                            <div class="text-xs text-pm-secondary truncate" title="${this.escapeHtml(image.filename)}">${this.escapeHtml(image.filename)}</div>
                            <div class="text-xs text-pm-muted">${this.formatFileSize(image.size)}${hasThumb ? ' • Fast' : ''}${isVideo ? ' • Video' : ''}</div>
                        </div>
                    </div>
                `;
                }).join('');
            }

            renderListView() {
                const grid = document.getElementById('galleryGrid');
                const list = document.getElementById('galleryList');
                
                grid.classList.add('hidden');
                list.classList.remove('hidden');

                list.innerHTML = this.images.map((image, index) => {
                    // Use thumbnail for display if available, fallback to original
                    const displayUrl = image.thumbnail_url || image.url;
                    const hasThumb = !!image.thumbnail_url;
                    const isVideo = image.is_video || false;
                    const mediaType = image.media_type || 'image';
                    
                    return `
                    <div class="image-item bg-pm-surface rounded-pm-md border border-pm hover:border-pm cursor-pointer group flex items-center p-4">
                        <div class="w-16 h-16 bg-pm-primary rounded-pm-md overflow-hidden flex-shrink-0 mr-4 relative">
                            <img src="${displayUrl}"
                                 alt="${this.escapeHtml(image.filename)}"
                                 class="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
                                 loading="lazy"
                                 data-original="${image.url}"
                                 data-thumbnail="${image.thumbnail_url || ''}"
                                 data-caption="${this.escapeHtml(this.formatImageCaption(image))}"
                                 data-media-type="${mediaType}"
                                 data-is-video="${isVideo}"
                                 onerror="this.style.display='none'">
                            ${hasThumb ? '<div class="absolute top-1 right-1 w-2 h-2 bg-pm-success rounded-full" title="Thumbnail available"></div>' : ''}
                            ${isVideo ? '<div class="absolute top-1 left-1 w-4 h-3 bg-pm-error text-pm text-xs flex items-center justify-center rounded" title="Video">▶</div>' : ''}
                        </div>
                        <div class="flex-1 min-w-0">
                            <div class="text-sm font-medium text-pm truncate">${this.escapeHtml(image.filename)}${isVideo ? ' 🎬' : ''}</div>
                            <div class="text-xs text-pm-secondary">${this.formatFileSize(image.size)} • ${new Date(image.modified_time * 1000).toLocaleDateString()}${hasThumb ? ' • Fast' : ''}${isVideo ? ' • Video' : ''}</div>
                            <div class="text-xs text-pm-muted truncate">${image.relative_path}</div>
                        </div>
                        <div class="flex-shrink-0 ml-4">
                            ${isVideo ? `
                                <svg class="w-5 h-5 text-pm-error group-hover:text-pm-error transition-colors" fill="currentColor" viewBox="0 0 24 24">
                                    <path d="M8 5v14l11-7z"/>
                                </svg>
                            ` : `
                                <svg class="w-5 h-5 text-pm-secondary group-hover:text-pm transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path>
                                </svg>
                            `}
                        </div>
                    </div>
                `;
                }).join('');
            }

            initializeViewer() {
                // Destroy existing viewer if it exists
                if (this.viewer) {
                    this.viewer.destroy();
                }

                const container = this.viewMode === 'grid' ? document.getElementById('galleryGrid') : document.getElementById('galleryList');
                
                // Find all images in the container
                const images = container.querySelectorAll('img[data-original]');
                
                if (images.length === 0) {
                    console.warn('No images found for viewer initialization');
                    return;
                }

                this.viewer = new Viewer(container, {
                    inline: false,
                    button: true,
                    navbar: true,
                    title: true,
                    // IMPORTANT: Use original images for viewing, not thumbnails
                    url: 'data-original',  // Tell ViewerJS to use data-original attribute
                    toolbar: {
                        zoomIn: 1,
                        zoomOut: 1,
                        oneToOne: 1,
                        reset: 1,
                        prev: 1,
                        play: {
                            show: 1,
                            size: 'large',
                        },
                        next: 1,
                        rotateLeft: 1,
                        rotateRight: 1,
                        flipHorizontal: 1,
                        flipVertical: 1,
                    },
                    className: '',
                    title: [1, (image, imageData) => `${imageData.alt} (${this.images.length} images) - Original Image`],
                    viewed: (event) => {
                        console.log('ViewerJS opened original image for metadata');
                        // Add metadata sidebar when ViewerJS opens
                        // Use the original image URL from data-original attribute
                        const originalImg = event.detail.originalImage;
                        setTimeout(() => this.addMetadataSidebar(originalImg), 100);
                    },
                    show: function() {
                        console.log('Viewer shown - displaying original image');
                    },
                    shown: function() {
                        console.log('ViewerJS initialization complete - ready for metadata');
                    },
                    hide: function() {
                        // Remove sidebar when viewer closes
                        const sidebar = document.getElementById('metadata-sidebar');
                        if (sidebar) {
                            sidebar.remove();
                        }
                    }
                });

                // Don't add click handlers here - let ViewerJS handle them
            }

            addMetadataSidebar(originalImage) {
                const viewerContainer = document.querySelector('.viewer-container');
                if (!viewerContainer || document.getElementById('metadata-sidebar')) return;

                const sidebar = document.createElement('div');
                sidebar.id = 'metadata-sidebar';
                sidebar.className = 'metadata-sidebar';
                sidebar.innerHTML = `
                    <!-- Collapse Button -->
                    <div class="metadata-collapse-btn" onclick="this.closest('.metadata-sidebar').classList.toggle('collapsed')">
                        <svg class="w-4 h-4 text-pm-secondary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"></path>
                        </svg>
                    </div>

                    <!-- Header -->
                    <div class="flex items-center justify-between p-4 border-b border-pm">
                        <div class="flex items-center gap-2">
                            <svg class="w-5 h-5 text-pm-secondary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
                            </svg>
                            <h1 class="text-sm font-semibold text-pm">Generation data</h1>
                        </div>
                        <button class="text-xs text-pm-accent hover:text-pm-accent transition-colors metadata-copy-all">
                            📋 COPY ALL
                        </button>
                    </div>

                    <!-- Scrollable Content -->
                    <div id="metadata-content" class="flex-1 overflow-y-auto p-4 space-y-3">
                        <!-- Loading state -->
                        <div class="text-center text-pm-muted py-8">
                            <div class="w-8 h-8 border-2 border-pm-accent-muted border-t-pm-accent rounded-full animate-spin mx-auto mb-2"></div>
                            <p class="text-sm">Loading metadata...</p>
                        </div>
                    </div>
                `;

                document.body.appendChild(sidebar);

                // Load metadata for the current image (ALWAYS use original for metadata)
                const originalImageUrl = originalImage.dataset.original || originalImage.src;
                console.log('Loading metadata from original image:', originalImageUrl);
                this.loadImageMetadata(originalImageUrl, sidebar.querySelector('#metadata-content'));

                // Listen for ViewerJS view changes to update metadata
                this.setupViewerMetadataUpdates(sidebar);
            }

            setupViewerMetadataUpdates(sidebar) {
                // Listen for ViewerJS navigation events
                const viewerContainer = document.querySelector('.viewer-container');
                if (!viewerContainer) return;

                // Override ViewerJS navigation to update metadata
                const observer = new MutationObserver((mutations) => {
                    mutations.forEach((mutation) => {
                        if (mutation.type === 'attributes' && mutation.attributeName === 'src') {
                            const img = mutation.target;
                            if (img.tagName === 'IMG' && img.closest('.viewer-canvas')) {
                                // Find the corresponding original image
                                const imageUrl = img.src;
                                this.loadImageMetadata(imageUrl, sidebar.querySelector('#metadata-content'));
                            }
                        }
                    });
                });

                const canvas = viewerContainer.querySelector('.viewer-canvas');
                if (canvas) {
                    observer.observe(canvas, {
                        childList: true,
                        subtree: true,
                        attributes: true,
                        attributeFilter: ['src']
                    });
                }
            }

            addFallbackClickHandlers() {
                // Add simple click handlers that work even if ViewerJS fails
                const container = this.viewMode === 'grid' ? document.getElementById('galleryGrid') : document.getElementById('galleryList');
                const imageItems = container.querySelectorAll('.image-item');
                
                imageItems.forEach((item, index) => {
                    item.addEventListener('click', (e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        
                        const img = item.querySelector('img[data-original]');
                        if (img) {
                            // ALWAYS use the original media URL for viewing
                            const originalMediaUrl = img.dataset.original;
                            const caption = img.dataset.caption;
                            const isVideo = img.dataset.isVideo === 'true';
                            const mediaType = img.dataset.mediaType || 'image';
                            
                            console.log('Opening original media for viewing:', originalMediaUrl, 'Type:', mediaType);
                            
                            if (isVideo) {
                                // For videos, use our custom video modal
                                this.showVideoModal(originalMediaUrl, caption, index);
                            } else {
                                // For images, try ViewerJS first, fallback to simple modal
                                if (this.viewer) {
                                    try {
                                        this.viewer.view(index);
                                    } catch (e) {
                                        console.warn('ViewerJS failed, using fallback:', e);
                                        this.showSimpleImageModal(originalMediaUrl, caption);
                                    }
                                } else {
                                    this.showSimpleImageModal(originalMediaUrl, caption);
                                }
                            }
                        }
                    });
                });
            }

            async showSimpleImageModal(imageUrl, caption) {
                // Create a modal that matches the screenshot design
                const modal = document.createElement('div');
                modal.className = 'fixed inset-0 bg-black z-50 flex';
                modal.innerHTML = `
                    <!-- Image area with navigation -->
                    <div class="flex-1 flex items-center justify-center relative">
                        <!-- Navigation arrows -->
                        <button class="absolute left-4 top-1/2 transform -translate-y-1/2 w-10 h-10 bg-black/50 hover:bg-black/70 rounded-full flex items-center justify-center text-pm text-xl z-10" id="prevImageBtn">
                            ‹
                        </button>
                        <button class="absolute right-4 top-1/2 transform -translate-y-1/2 w-10 h-10 bg-black/50 hover:bg-black/70 rounded-full flex items-center justify-center text-pm text-xl z-10" id="nextImageBtn">
                            ›
                        </button>
                        
                        <!-- Close button -->
                        <button class="absolute top-4 right-4 w-10 h-10 bg-black/50 hover:bg-black/70 rounded-full flex items-center justify-center text-pm text-xl z-10" onclick="this.parentElement.parentElement.remove()">
                            ×
                        </button>
                        
                        <!-- Main image -->
                        <img src="${imageUrl}" alt="${caption}" class="max-w-full max-h-full object-contain" id="modalImage">
                    </div>
                    
                    <!-- Metadata sidebar (matching the screenshot) -->
                    <div class="metadata-sidebar">
                        <!-- Collapse Button -->
                        <div class="metadata-collapse-btn" onclick="this.closest('.metadata-sidebar').classList.toggle('collapsed')">
                            <svg class="w-4 h-4 text-pm-secondary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"></path>
                            </svg>
                        </div>

                        <!-- Header -->
                        <div class="flex items-center justify-between p-4 border-b border-pm">
                            <div class="flex items-center gap-2">
                                <svg class="w-5 h-5 text-pm-secondary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
                                </svg>
                                <h1 class="text-sm font-semibold text-pm">Generation data</h1>
                            </div>
                            <button class="text-xs text-pm-accent hover:text-pm-accent transition-colors metadata-copy-all">
                                📋 COPY ALL
                            </button>
                        </div>

                        <!-- Scrollable Content -->
                        <div id="metadata-content" class="flex-1 overflow-y-auto p-4 space-y-3">
                            <!-- Loading state -->
                            <div class="text-center text-pm-muted py-8">
                                <div class="w-8 h-8 border-2 border-pm-accent-muted border-t-pm-accent rounded-full animate-spin mx-auto mb-2"></div>
                                <p class="text-sm">Loading metadata...</p>
                            </div>
                        </div>
                    </div>
                `;
                
                // Close on click outside image area
                modal.addEventListener('click', (e) => {
                    if (e.target === modal || e.target.id === 'modalImage') {
                        modal.remove();
                    }
                });
                
                // Close on Escape key
                const escapeHandler = (e) => {
                    if (e.key === 'Escape') {
                        modal.remove();
                        document.removeEventListener('keydown', escapeHandler);
                    }
                };
                document.addEventListener('keydown', escapeHandler);
                
                document.body.appendChild(modal);
                
                // Add navigation functionality
                this.setupImageNavigation(modal, imageUrl);
                
                // Load and display metadata
                await this.loadImageMetadata(imageUrl, modal.querySelector('#metadata-content'));
            }

            async showVideoModal(videoUrl, caption, videoIndex) {
                const settings = this.getSettings();
                
                // Create a modal for video viewing
                const modal = document.createElement('div');
                modal.className = 'fixed inset-0 bg-black z-50 flex';
                modal.innerHTML = `
                    <!-- Video area with navigation -->
                    <div class="flex-1 flex items-center justify-center relative">
                        <!-- Navigation arrows -->
                        <button class="absolute left-4 top-1/2 transform -translate-y-1/2 w-10 h-10 bg-black/50 hover:bg-black/70 rounded-full flex items-center justify-center text-pm text-xl z-10" id="prevVideoBtn">
                            ‹
                        </button>
                        <button class="absolute right-4 top-1/2 transform -translate-y-1/2 w-10 h-10 bg-black/50 hover:bg-black/70 rounded-full flex items-center justify-center text-pm text-xl z-10" id="nextVideoBtn">
                            ›
                        </button>
                        
                        <!-- Close button -->
                        <button class="absolute top-4 right-4 w-10 h-10 bg-black/50 hover:bg-black/70 rounded-full flex items-center justify-center text-pm text-xl z-10" onclick="this.parentElement.parentElement.remove()">
                            ×
                        </button>
                        
                        <!-- Video controls -->
                        <div class="absolute bottom-4 right-4 flex space-x-2 z-10">
                            <button id="videoMuteBtn" class="px-3 py-2 bg-black/50 hover:bg-black/70 rounded text-pm text-sm">
                                🔊
                            </button>
                            <button id="videoLoopBtn" class="px-3 py-2 bg-black/50 hover:bg-black/70 rounded text-pm text-sm">
                                🔁
                            </button>
                        </div>
                        
                        <!-- Main video -->
                        <video id="modalVideo" 
                               src="${videoUrl}" 
                               class="max-w-full max-h-full object-contain"
                               controls
                               ${settings.videoAutoplay ? 'autoplay' : ''}
                               ${settings.videoMute ? 'muted' : ''}
                               ${settings.videoLoop ? 'loop' : ''}>
                            Your browser does not support the video tag.
                        </video>
                    </div>
                    
                    <!-- Metadata sidebar (videos have limited metadata) -->
                    <div class="metadata-sidebar">
                        <!-- Collapse Button -->
                        <div class="metadata-collapse-btn" onclick="this.closest('.metadata-sidebar').classList.toggle('collapsed')">
                            <svg class="w-4 h-4 text-pm-secondary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"></path>
                            </svg>
                        </div>

                        <!-- Header -->
                        <div class="flex items-center justify-between p-4 border-b border-pm">
                            <div class="flex items-center gap-2">
                                <svg class="w-5 h-5 text-pm-error" fill="currentColor" viewBox="0 0 24 24">
                                    <path d="M8 5v14l11-7z"/>
                                </svg>
                                <h1 class="text-sm font-semibold text-pm">Video Info</h1>
                            </div>
                            <button class="text-xs text-pm-accent hover:text-pm-accent transition-colors" onclick="navigator.clipboard.writeText('${videoUrl}').then(() => this.textContent = '✓ Copied!'); setTimeout(() => this.textContent = '📋 COPY URL', 1000)">
                                📋 COPY URL
                            </button>
                        </div>
                        
                        <!-- Video info content -->
                        <div id="video-info-content" class="flex-1 overflow-y-auto p-4 space-y-3">
                            <!-- Video details will be loaded here -->
                        </div>
                    </div>
                `;
                
                // Close on click outside video area
                modal.addEventListener('click', (e) => {
                    if (e.target === modal) {
                        modal.remove();
                    }
                });
                
                // Close on Escape key
                const escapeHandler = (e) => {
                    if (e.key === 'Escape') {
                        modal.remove();
                        document.removeEventListener('keydown', escapeHandler);
                    }
                };
                document.addEventListener('keydown', escapeHandler);
                
                document.body.appendChild(modal);
                
                // Set up video controls
                this.setupVideoControls(modal, settings);
                
                // Add navigation functionality
                this.setupVideoNavigation(modal, videoUrl, videoIndex);
                
                // Load and display video info
                await this.loadVideoInfo(videoUrl, modal.querySelector('#video-info-content'));
            }

            setupVideoControls(modal, settings) {
                const video = modal.querySelector('#modalVideo');
                const muteBtn = modal.querySelector('#videoMuteBtn');
                const loopBtn = modal.querySelector('#videoLoopBtn');
                
                // Mute/unmute toggle
                muteBtn.addEventListener('click', () => {
                    video.muted = !video.muted;
                    muteBtn.textContent = video.muted ? '🔇' : '🔊';
                    
                    // Save setting
                    const newSettings = this.getSettings();
                    newSettings.videoMute = video.muted;
                    this.saveSettings(newSettings);
                });
                
                // Loop toggle
                loopBtn.addEventListener('click', () => {
                    video.loop = !video.loop;
                    loopBtn.style.opacity = video.loop ? '1' : '0.5';
                    
                    // Save setting
                    const newSettings = this.getSettings();
                    newSettings.videoLoop = video.loop;
                    this.saveSettings(newSettings);
                });
                
                // Set initial states
                muteBtn.textContent = video.muted ? '🔇' : '🔊';
                loopBtn.style.opacity = video.loop ? '1' : '0.5';
            }

            setupVideoNavigation(modal, currentVideoUrl, currentIndex) {
                const prevBtn = modal.querySelector('#prevVideoBtn');
                const nextBtn = modal.querySelector('#nextVideoBtn');
                
                // Get all video items from current gallery
                const container = this.viewMode === 'grid' ? document.getElementById('galleryGrid') : document.getElementById('galleryList');
                const videoItems = Array.from(container.querySelectorAll('.image-item img[data-is-video="true"]'));
                
                if (videoItems.length <= 1) {
                    prevBtn.style.display = 'none';
                    nextBtn.style.display = 'none';
                    return;
                }
                
                const currentVideoIndex = videoItems.findIndex(img => img.dataset.original === currentVideoUrl);
                
                prevBtn.addEventListener('click', () => {
                    const prevIndex = currentVideoIndex > 0 ? currentVideoIndex - 1 : videoItems.length - 1;
                    const prevVideo = videoItems[prevIndex];
                    if (prevVideo) {
                        modal.remove();
                        this.showVideoModal(prevVideo.dataset.original, prevVideo.dataset.caption, prevIndex);
                    }
                });
                
                nextBtn.addEventListener('click', () => {
                    const nextIndex = currentVideoIndex < videoItems.length - 1 ? currentVideoIndex + 1 : 0;
                    const nextVideo = videoItems[nextIndex];
                    if (nextVideo) {
                        modal.remove();
                        this.showVideoModal(nextVideo.dataset.original, nextVideo.dataset.caption, nextIndex);
                    }
                });
                
                // Add keyboard navigation
                const keyHandler = (e) => {
                    if (e.key === 'ArrowLeft') {
                        e.preventDefault();
                        prevBtn.click();
                    } else if (e.key === 'ArrowRight') {
                        e.preventDefault();
                        nextBtn.click();
                    }
                };
                
                document.addEventListener('keydown', keyHandler);
                
                // Clean up listener when modal is removed
                const observer = new MutationObserver((mutations) => {
                    mutations.forEach((mutation) => {
                        if (mutation.type === 'childList') {
                            mutation.removedNodes.forEach((node) => {
                                if (node === modal) {
                                    document.removeEventListener('keydown', keyHandler);
                                    observer.disconnect();
                                }
                            });
                        }
                    });
                });
                
                observer.observe(document.body, { childList: true });
            }

            async loadVideoInfo(videoUrl, container) {
                try {
                    // Extract basic video information
                    const fileName = videoUrl.split('/').pop();
                    const fileExt = fileName.split('.').pop().toUpperCase();
                    
                    container.innerHTML = `
                        <!-- File Info -->
                        <div class="space-y-2">
                            <h2 class="text-sm font-medium text-pm-secondary">File Info</h2>
                            <div class="text-xs text-pm-secondary font-mono bg-pm-surface p-2 rounded break-all cursor-pointer hover:bg-pm-hover"
                                 onclick="navigator.clipboard.writeText('${videoUrl}').then(() => this.textContent = 'Copied!'); setTimeout(() => this.textContent = '${fileName}', 1000)"
                                 title="Click to copy path">
                                ${fileName}
                            </div>
                            <div class="text-xs text-pm-muted">
                                Type: ${fileExt} Video
                            </div>
                        </div>

                        <!-- Video Properties -->
                        <div class="space-y-2" id="video-properties">
                            <h2 class="text-sm font-medium text-pm-secondary">Video Properties</h2>
                            <div class="text-xs text-pm-secondary">
                                Loading video information...
                            </div>
                        </div>

                        <!-- Video Settings -->
                        <div class="space-y-3">
                            <h2 class="text-sm font-medium text-pm-secondary">Playback Settings</h2>
                            <div class="space-y-2 text-xs">
                                <div class="flex justify-between items-center">
                                    <span class="text-pm-secondary">Autoplay:</span>
                                    <span class="text-pm">${this.getSettings().videoAutoplay ? 'Enabled' : 'Disabled'}</span>
                                </div>
                                <div class="flex justify-between items-center">
                                    <span class="text-pm-secondary">Muted:</span>
                                    <span class="text-pm">${this.getSettings().videoMute ? 'Yes' : 'No'}</span>
                                </div>
                                <div class="flex justify-between items-center">
                                    <span class="text-pm-secondary">Loop:</span>
                                    <span class="text-pm">${this.getSettings().videoLoop ? 'Enabled' : 'Disabled'}</span>
                                </div>
                            </div>
                        </div>

                        <!-- Note about metadata -->
                        <div class="bg-pm-accent-tint border border-pm-accent rounded-pm-md p-3">
                            <h4 class="text-pm-accent font-medium mb-1 text-sm">ℹ️ Video Note</h4>
                            <p class="text-pm-secondary text-xs">
                                Videos don't contain the same metadata as PNG images. ComfyUI workflow data is only embedded in PNG outputs.
                            </p>
                        </div>
                    `;
                    
                    // Try to get video metadata from the video element
                    const video = document.querySelector('#modalVideo');
                    if (video) {
                        video.addEventListener('loadedmetadata', () => {
                            const propertiesDiv = container.querySelector('#video-properties');
                            if (propertiesDiv) {
                                const duration = video.duration;
                                const width = video.videoWidth;
                                const height = video.videoHeight;
                                
                                propertiesDiv.innerHTML = `
                                    <h2 class="text-sm font-medium text-pm-secondary">Video Properties</h2>
                                    <div class="space-y-1 text-xs">
                                        <div class="flex justify-between">
                                            <span class="text-pm-secondary">Duration:</span>
                                            <span class="text-pm">${this.formatDuration(duration)}</span>
                                        </div>
                                        <div class="flex justify-between">
                                            <span class="text-pm-secondary">Resolution:</span>
                                            <span class="text-pm">${width} × ${height}</span>
                                        </div>
                                        <div class="flex justify-between">
                                            <span class="text-pm-secondary">Aspect Ratio:</span>
                                            <span class="text-pm">${(width/height).toFixed(2)}:1</span>
                                        </div>
                                    </div>
                                `;
                            }
                        });
                    }
                    
                } catch (error) {
                    console.error('Error loading video info:', error);
                    container.innerHTML = `
                        <div class="bg-pm-error-tint border border-pm-error rounded-pm-md p-4">
                            <h4 class="text-pm-error font-medium mb-2">❌ Video Info Error</h4>
                            <p class="text-pm-secondary text-sm">${error.message}</p>
                        </div>
                    `;
                }
            }

            formatDuration(seconds) {
                if (isNaN(seconds) || seconds === 0) return '0:00';
                
                const hours = Math.floor(seconds / 3600);
                const minutes = Math.floor((seconds % 3600) / 60);
                const secs = Math.floor(seconds % 60);
                
                if (hours > 0) {
                    return `${hours}:${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
                } else {
                    return `${minutes}:${secs.toString().padStart(2, '0')}`;
                }
            }

            async loadImageMetadata(imageUrl, container) {
                try {
                    // First try to get linked prompt from database
                    let databasePrompt = null;
                    try {
                        // Extract relative path from the URL for the API call
                        const urlParts = imageUrl.split('/prompt_manager/images/serve/');
                        if (urlParts.length > 1) {
                            const relativePath = urlParts[1];
                            const response = await fetch(`/prompt_manager/images/prompt/${encodeURIComponent(relativePath)}`);
                            if (response.ok) {
                                const data = await response.json();
                                if (data.success && data.prompt) {
                                    databasePrompt = data.prompt;
                                }
                            }
                        }
                    } catch (dbError) {
                        // No database prompt found, will fall back to metadata extraction
                    }
                    
                    // If we have a database prompt, use it; otherwise extract from metadata
                    let metadata;
                    if (databasePrompt) {
                        // Create metadata object from database prompt
                        metadata = {
                            positivePrompt: databasePrompt.text,
                            negativePrompt: 'No negative prompt found', // Database doesn't separate positive/negative
                            checkpoint: 'Unknown',
                            steps: 'Unknown',
                            cfgScale: 'Unknown',
                            sampler: 'Unknown',
                            seed: 'Unknown',
                            workflow: databasePrompt.workflow_data,
                            imagePath: imageUrl,
                            source: 'database', // Mark this as coming from database
                            promptId: databasePrompt.prompt_id,
                            category: databasePrompt.category,
                            tags: databasePrompt.tags,
                            rating: databasePrompt.rating,
                            notes: databasePrompt.notes
                        };
                        
                        // Still try to extract technical parameters from PNG metadata if available
                        try {
                            const pngMetadata = await this.extractImageMetadata(imageUrl);
                            if (pngMetadata) {
                                metadata.checkpoint = pngMetadata.checkpoint;
                                metadata.steps = pngMetadata.steps;
                                metadata.cfgScale = pngMetadata.cfgScale;
                                metadata.sampler = pngMetadata.sampler;
                                metadata.seed = pngMetadata.seed;
                                // Use negative prompt from PNG if available
                                if (pngMetadata.negativePrompt && pngMetadata.negativePrompt !== 'No negative prompt found') {
                                    metadata.negativePrompt = pngMetadata.negativePrompt;
                                }
                            }
                        } catch (pngError) {
                            // Could not extract PNG metadata, using database-only data
                        }
                    } else {
                        // Fall back to PNG metadata extraction
                        metadata = await this.extractImageMetadata(imageUrl);
                        if (metadata) {
                            metadata.source = 'png'; // Mark this as coming from PNG
                        }
                    }
                    
                    this.displayMetadata(metadata, container);
                } catch (error) {
                    console.error('Error loading metadata:', error);
                    container.innerHTML = `
                        <div class="bg-pm-error-tint border border-pm-error rounded-pm-md p-4">
                            <h4 class="text-pm-error font-medium mb-2">❌ Metadata Error</h4>
                            <p class="text-pm-secondary text-sm">${error.message}</p>
                        </div>
                    `;
                }
            }

            displayMetadata(metadata, container) {
                if (!metadata) {
                    container.innerHTML = `
                        <div class="text-center text-pm-muted py-8">
                            <svg class="w-16 h-16 mx-auto mb-4 opacity-30" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
                            </svg>
                            <p class="text-sm">No metadata found in this image</p>
                        </div>
                    `;
                    return;
                }

                const html = `
                    <!-- File Path -->
                    <div class="space-y-2">
                        <h2 class="text-sm font-medium text-pm-secondary">File Path</h2>
                        <div class="text-xs text-pm-secondary font-mono bg-pm-surface p-2 rounded break-all cursor-pointer hover:bg-pm-hover" data-copy-path title="Click to copy">
                            ${metadata.imagePath || 'Unknown'}
                        </div>
                    </div>

                    <!-- Resources used -->
                    <div class="space-y-3">
                        <h2 class="text-sm font-medium text-pm-secondary">Resources used</h2>
                        <div class="space-y-2 text-xs">
                            <div class="flex justify-between">
                                <span class="text-pm-secondary">Model:</span>
                                <span class="text-pm">${metadata.checkpoint || 'Unknown'}</span>
                            </div>
                        </div>
                    </div>

                    <!-- Prompt Section -->
                    <div class="space-y-3">
                        <div class="flex items-center justify-between">
                            <h2 class="text-sm font-medium text-pm-secondary">Prompt</h2>
                            <button class="text-xs px-2 py-1 bg-pm-warning hover:bg-pm-warning text-pm rounded transition-colors" data-copy-type="positive">
                                COPY PROMPT
                            </button>
                        </div>
                        <div class="text-xs text-pm bg-pm-surface p-3 rounded max-h-32 overflow-y-auto break-words leading-relaxed">
                            ${this.formatPromptText(metadata.positivePrompt)}
                            <div class="mt-2 pt-2 border-t border-pm">
                                <button class="text-pm-accent hover:text-pm-accent underline text-xs" data-show-type="positive">
                                    Show more
                                </button>
                            </div>
                        </div>
                    </div>

                    <!-- Negative Prompt -->
                    <div class="space-y-3">
                        <h2 class="text-sm font-medium text-pm-secondary">Negative prompt</h2>
                        <div class="text-xs text-pm bg-pm-surface p-3 rounded max-h-32 overflow-y-auto break-words leading-relaxed">
                            ${this.formatPromptText(metadata.negativePrompt)}
                            <div class="mt-2 pt-2 border-t border-pm">
                                <button class="text-pm-accent hover:text-pm-accent underline text-xs" data-show-type="negative">
                                    Show more
                                </button>
                            </div>
                        </div>
                    </div>

                    <!-- Other metadata -->
                    <div class="space-y-3">
                        <h2 class="text-sm font-medium text-pm-secondary">Other metadata</h2>
                        <div class="space-y-2 text-xs">
                            <div class="flex justify-between">
                                <span class="text-pm-secondary">CFG SCALE:</span>
                                <span class="text-pm px-2 py-1 bg-pm-surface rounded text-xs">${metadata.cfgScale || 'Unknown'}</span>
                            </div>
                            <div class="flex justify-between">
                                <span class="text-pm-secondary">STEPS:</span>
                                <span class="text-pm px-2 py-1 bg-pm-surface rounded text-xs">${metadata.steps || 'Unknown'}</span>
                            </div>
                            <div class="flex justify-between">
                                <span class="text-pm-secondary">SAMPLER:</span>
                                <span class="text-pm px-2 py-1 bg-pm-surface rounded text-xs">${metadata.sampler || 'Unknown'}</span>
                            </div>
                            <div class="flex justify-between">
                                <span class="text-pm-secondary">SEED:</span>
                                <span class="text-pm font-mono px-2 py-1 bg-pm-surface rounded text-xs">${metadata.seed || 'Unknown'}</span>
                            </div>
                        </div>
                    </div>

                    <!-- ComfyUI Workflow -->
                    ${metadata.workflow ? `
                        <div class="space-y-3">
                            <h2 class="text-sm font-medium text-pm-secondary">ComfyUI Workflow</h2>
                            <div class="space-y-2">
                                <button class="text-xs text-pm-accent hover:text-pm-accent underline flex items-center gap-1" data-action="show-workflow">
                                    <span>View Raw Workflow JSON</span>
                                    <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"></path>
                                    </svg>
                                </button>
                                <button class="text-xs text-pm-accent hover:text-pm-accent underline flex items-center gap-1" data-action="download-workflow">
                                    <span>Download Workflow</span>
                                    <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
                                    </svg>
                                </button>
                            </div>
                        </div>
                    ` : ''}
                `;

                container.innerHTML = html;
                
                // Store metadata for actions
                this.currentMetadata = metadata;
                
                // Add click handlers
                this.setupMetadataActions(container);
            }

            setupImageNavigation(modal, currentImageUrl) {
                // Find current image index
                this.currentImageIndex = this.images.findIndex(img => img.url === currentImageUrl);
                
                const prevBtn = modal.querySelector('#prevImageBtn');
                const nextBtn = modal.querySelector('#nextImageBtn');
                const modalImage = modal.querySelector('#modalImage');
                
                const updateImage = async (newIndex) => {
                    if (newIndex >= 0 && newIndex < this.images.length) {
                        this.currentImageIndex = newIndex;
                        const newImage = this.images[newIndex];
                        // ALWAYS use original URL for viewing and metadata
                        modalImage.src = newImage.url;  // This is always the original URL
                        modalImage.alt = this.formatImageCaption(newImage);
                        
                        // Update metadata using original image
                        const metadataContainer = modal.querySelector('#metadata-content');
                        metadataContainer.innerHTML = `
                            <div class="text-center text-pm-muted py-8">
                                <div class="w-8 h-8 border-2 border-pm-accent-muted border-t-pm-accent rounded-full animate-spin mx-auto mb-2"></div>
                                <p class="text-sm">Loading metadata from original image...</p>
                            </div>
                        `;
                        // Load metadata from original image (which has embedded workflow data)
                        await this.loadImageMetadata(newImage.url, metadataContainer);
                    }
                    
                    // Update button states
                    prevBtn.style.opacity = newIndex > 0 ? '1' : '0.5';
                    nextBtn.style.opacity = newIndex < this.images.length - 1 ? '1' : '0.5';
                };
                
                prevBtn.addEventListener('click', () => {
                    if (this.currentImageIndex > 0) {
                        updateImage(this.currentImageIndex - 1);
                    }
                });
                
                nextBtn.addEventListener('click', () => {
                    if (this.currentImageIndex < this.images.length - 1) {
                        updateImage(this.currentImageIndex + 1);
                    }
                });
                
                // Keyboard navigation
                const keyHandler = (e) => {
                    if (e.key === 'ArrowLeft' && this.currentImageIndex > 0) {
                        updateImage(this.currentImageIndex - 1);
                    } else if (e.key === 'ArrowRight' && this.currentImageIndex < this.images.length - 1) {
                        updateImage(this.currentImageIndex + 1);
                    }
                };
                document.addEventListener('keydown', keyHandler);
                
                // Clean up on modal close
                modal.addEventListener('remove', () => {
                    document.removeEventListener('keydown', keyHandler);
                });
                
                // Initial button state
                updateImage(this.currentImageIndex);
            }

            setupMetadataActions(container) {
                // Copy prompt buttons
                const copyButtons = container.querySelectorAll('[data-copy-type]');
                copyButtons.forEach(btn => {
                    btn.addEventListener('click', (e) => {
                        const type = btn.getAttribute('data-copy-type');
                        if (this.currentMetadata) {
                            const text = type === 'positive' ? this.currentMetadata.positivePrompt : this.currentMetadata.negativePrompt;
                            this.copyToClipboard(text);
                        }
                    });
                });

                // File path copy
                const pathEl = container.querySelector('[data-copy-path]');
                if (pathEl) {
                    pathEl.addEventListener('click', () => {
                        if (this.currentMetadata) {
                            this.copyToClipboard(this.currentMetadata.imagePath);
                        }
                    });
                }

                // Show more buttons
                const showMoreBtns = container.querySelectorAll('[data-show-type]');
                showMoreBtns.forEach(btn => {
                    btn.addEventListener('click', (e) => {
                        const type = btn.getAttribute('data-show-type');
                        this.showFullPrompt(type);
                    });
                });

                // Workflow action buttons
                const actionBtns = container.querySelectorAll('[data-action]');
                actionBtns.forEach(btn => {
                    btn.addEventListener('click', (e) => {
                        const action = btn.getAttribute('data-action');
                        if (action === 'show-workflow') {
                            this.showWorkflowData();
                        } else if (action === 'download-workflow') {
                            this.downloadWorkflowJSON();
                        }
                    });
                });

                // Copy all button
                const copyAllBtn = container.closest('.metadata-sidebar').querySelector('.metadata-copy-all');
                if (copyAllBtn) {
                    copyAllBtn.addEventListener('click', () => {
                        this.copyAllMetadata();
                    });
                }
            }

            showFullPrompt(type) {
                if (this.currentMetadata) {
                    const prompt = type === 'positive' ? this.currentMetadata.positivePrompt : this.currentMetadata.negativePrompt;
                    const safeType = this.escapeHtml(type.charAt(0).toUpperCase() + type.slice(1));
                    const newWindow = window.open('', '_blank');
                    const doc = newWindow.document;
                    doc.open();
                    doc.write('<!DOCTYPE html><html><head><title>' + safeType + ' Prompt</title></head><body></body></html>');
                    doc.close();
                    doc.body.style.cssText = 'background:#111;color:#fff;font-family:monospace;padding:20px;';
                    const h2 = doc.createElement('h2');
                    h2.textContent = type.charAt(0).toUpperCase() + type.slice(1) + ' Prompt';
                    doc.body.appendChild(h2);
                    const pre = doc.createElement('pre');
                    pre.style.cssText = 'background:#222;padding:15px;border-radius:5px;white-space:pre-wrap;line-height:1.5;';
                    pre.textContent = prompt;
                    doc.body.appendChild(pre);
                    const btn = doc.createElement('button');
                    btn.textContent = 'Copy to Clipboard';
                    btn.style.cssText = 'margin-top:20px;padding:10px 20px;background:#444;color:#fff;border:none;border-radius:5px;cursor:pointer;';
                    btn.addEventListener('click', () => { navigator.clipboard.writeText(pre.textContent).then(() => alert('Copied!')); });
                    doc.body.appendChild(btn);
                }
            }

            showWorkflowData() {
                if (this.currentMetadata && this.currentMetadata.workflow) {
                    const newWindow = window.open('', '_blank');
                    newWindow.document.write(`
                        <html>
                            <head><title>ComfyUI Workflow Data</title></head>
                            <body style="background: #111; color: #fff; font-family: monospace; padding: 20px;">
                                <h2>ComfyUI Workflow JSON</h2>
                                <pre style="background: #222; padding: 15px; border-radius: 5px; overflow: auto;">${JSON.stringify(this.currentMetadata.workflow, null, 2)}</pre>
                            </body>
                        </html>
                    `);
                }
            }

            async copyToClipboard(text) {
                try {
                    await navigator.clipboard.writeText(text);
                    this.showNotification('Copied to clipboard!', 'success');
                } catch (err) {
                    console.error('Copy failed:', err);
                    this.showNotification('Copy failed', 'error');
                }
            }

            copyAllMetadata() {
                if (!this.currentMetadata) return;
                
                const allData = `Checkpoint: ${this.currentMetadata.checkpoint || 'Unknown'}
Positive Prompt: ${this.currentMetadata.positivePrompt}
Negative Prompt: ${this.currentMetadata.negativePrompt}
Steps: ${this.currentMetadata.steps || 'Unknown'}
CFG Scale: ${this.currentMetadata.cfgScale || 'Unknown'}
Sampler: ${this.currentMetadata.sampler || 'Unknown'}
Seed: ${this.currentMetadata.seed || 'Unknown'}`;
                
                this.copyToClipboard(allData);
            }

            downloadWorkflowJSON() {
                if (this.currentMetadata && this.currentMetadata.workflow) {
                    const dataStr = JSON.stringify(this.currentMetadata.workflow, null, 2);
                    const dataBlob = new Blob([dataStr], {type: 'application/json'});
                    const url = URL.createObjectURL(dataBlob);
                    const link = document.createElement('a');
                    link.href = url;
                    link.download = 'comfyui_workflow.json';
                    document.body.appendChild(link);
                    link.click();
                    document.body.removeChild(link);
                    URL.revokeObjectURL(url);
                    this.showNotification('Workflow downloaded!', 'success');
                }
            }

            formatPromptText(text) {
                if (!text || text === 'No prompt found' || text === 'No negative prompt found') {
                    return `<span class="text-pm-muted italic">${text || 'No prompt found'}</span>`;
                }
                return this.escapeHtml(text);
            }

            escapeHtml(text) {
                const div = document.createElement('div');
                div.textContent = text;
                return div.innerHTML;
            }

            unescapeHtml(text) {
                const div = document.createElement('div');
                div.innerHTML = text;
                return div.textContent;
            }

            showNotification(message, type = 'info') {
                const notification = document.createElement('div');
                notification.className = `fixed top-4 right-4 px-4 py-2 rounded-pm-md shadow-pm text-sm z-[20000] transition-all duration-300 transform translate-x-full`;

                const colors = {
                    success: "bg-pm-success text-pm",
                    error: "bg-pm-error text-pm",
                    warning: "bg-pm-warning text-pm",
                    info: "bg-pm-accent text-pm"
                };

                notification.className += ` ${colors[type] || colors.info}`;
                notification.textContent = message;

                document.body.appendChild(notification);

                setTimeout(() => notification.classList.remove("translate-x-full"), 100);
                setTimeout(() => {
                    notification.classList.add("translate-x-full");
                    setTimeout(() => {
                        if (notification.parentNode) {
                            document.body.removeChild(notification);
                        }
                    }, 300);
                }, 3000);
            }

            formatImageCaption(image) {
                const date = new Date(image.modified_time * 1000);
                return `${image.filename} | ${this.formatFileSize(image.size)} | ${date.toLocaleString()}`;
            }

            async extractImageMetadata(imageUrl) {
                try {
                    // Fetch the image as an array buffer
                    const response = await fetch(imageUrl);
                    const arrayBuffer = await response.arrayBuffer();
                    
                    // Parse PNG metadata
                    const metadata = await this.parsePNGMetadata(arrayBuffer);
                    const comfyData = this.extractComfyUIData(metadata);
                    
                    return this.parseWorkflowData(comfyData, imageUrl);
                } catch (error) {
                    console.error('Error extracting metadata:', error);
                    return null;
                }
            }

            async parsePNGMetadata(arrayBuffer) {
                const dataView = new DataView(arrayBuffer);
                let offset = 8; // Skip PNG signature
                const metadata = {};

                while (offset < arrayBuffer.byteLength - 8) {
                    const length = dataView.getUint32(offset);
                    const type = new TextDecoder().decode(arrayBuffer.slice(offset + 4, offset + 8));
                    
                    if (type === 'tEXt' || type === 'iTXt' || type === 'zTXt') {
                        const chunkData = arrayBuffer.slice(offset + 8, offset + 8 + length);
                        let text;
                        
                        if (type === 'tEXt') {
                            text = new TextDecoder().decode(chunkData);
                        } else if (type === 'iTXt') {
                            // iTXt format: keyword\0compression\0language\0translated_keyword\0text
                            const textData = new TextDecoder().decode(chunkData);
                            const parts = textData.split('\0');
                            if (parts.length >= 5) {
                                metadata[parts[0]] = parts[4];
                            }
                            text = textData;
                        } else if (type === 'zTXt') {
                            // zTXt is compressed - basic parsing (might need proper decompression)
                            text = new TextDecoder().decode(chunkData);
                        }
                        
                        // Parse the text chunk for key-value pairs
                        const nullIndex = text.indexOf('\0');
                        if (nullIndex !== -1) {
                            const key = text.substring(0, nullIndex);
                            const value = text.substring(nullIndex + 1);
                            metadata[key] = value;
                        }
                    }
                    
                    offset += 8 + length + 4; // Move to next chunk (8 = length + type, 4 = CRC)
                }

                return metadata;
            }

            extractComfyUIData(metadata) {
                let workflowData = null;
                let promptData = null;

                // Common ComfyUI metadata field names
                const workflowFields = ['workflow', 'Workflow', 'comfy', 'ComfyUI'];
                const promptFields = ['prompt', 'Prompt', 'parameters', 'Parameters'];

                for (const field of workflowFields) {
                    if (metadata[field]) {
                        try {
                            // Clean NaN values from JSON string before parsing
                            let cleanedJson = metadata[field];
                            cleanedJson = cleanedJson.replace(/:\s*NaN\b/g, ': null');
                            cleanedJson = cleanedJson.replace(/\bNaN\b/g, 'null');
                            
                            workflowData = JSON.parse(cleanedJson);
                            break;
                        } catch (e) {
                            console.log('Failed to parse workflow field:', field);
                        }
                    }
                }

                for (const field of promptFields) {
                    if (metadata[field]) {
                        try {
                            // Clean NaN values from JSON string before parsing
                            let cleanedJson = metadata[field];
                            cleanedJson = cleanedJson.replace(/:\s*NaN\b/g, ': null');
                            cleanedJson = cleanedJson.replace(/\bNaN\b/g, 'null');
                            
                            promptData = JSON.parse(cleanedJson);
                            break;
                        } catch (e) {
                            console.log('Failed to parse prompt field:', field);
                        }
                    }
                }

                return { workflow: workflowData, prompt: promptData };
            }

            parseWorkflowData(comfyData, imagePath) {
                // Extract information from ComfyUI workflow
                let checkpoint = 'Unknown';
                let positivePrompt = 'No prompt found';
                let negativePrompt = 'No negative prompt found';
                let steps = 'Unknown';
                let cfgScale = 'Unknown';
                let sampler = 'Unknown';
                let seed = 'Unknown';

                // Parse prompt data first (more reliable for actual generation parameters)
                if (comfyData.prompt) {
                    const promptNodes = comfyData.prompt;
                    
                    // Track all text nodes and try to identify positive/negative
                    const textNodes = [];
                    
                    for (const nodeId in promptNodes) {
                        const node = promptNodes[nodeId];
                        
                        // Checkpoint - check multiple types
                        if ((node.class_type === 'CheckpointLoaderSimple' || 
                             node.class_type === 'UNETLoader' ||
                             node.class_type === 'DualCLIPLoader') && node.inputs) {
                            checkpoint = node.inputs.ckpt_name || node.inputs.unet_name || checkpoint;
                        }
                        
                        // Collect all text nodes for analysis
                        if (node.inputs && node.inputs.text) {
                            textNodes.push({
                                nodeId: nodeId,
                                classType: node.class_type,
                                text: node.inputs.text,
                                inputs: node.inputs
                            });
                        }
                        
                        // Special handling for PromptManager nodes
                        if (node.class_type === 'PromptManager' && node.inputs) {
                            // PromptManager always contains the positive prompt
                            if (node.inputs.text) {
                                positivePrompt = node.inputs.text;
                            }
                            // Also check for loaded prompt
                            if (node.inputs.selected_prompt) {
                                positivePrompt = node.inputs.selected_prompt;
                            }
                        }
                        
                        // PromptManagerText node
                        if (node.class_type === 'PromptManagerText' && node.inputs) {
                            if (node.inputs.text) {
                                positivePrompt = node.inputs.text;
                            }
                            if (node.inputs.selected_prompt) {
                                positivePrompt = node.inputs.selected_prompt;
                            }
                        }
                        
                        // Sampling parameters - check multiple sampler types
                        if ((node.class_type === 'KSampler' || 
                             node.class_type === 'KSamplerAdvanced' ||
                             node.class_type === 'SamplerCustom' ||
                             node.class_type === 'SamplerCustomAdvanced') && node.inputs) {
                            steps = node.inputs.steps || steps;
                            cfgScale = node.inputs.cfg || cfgScale;
                            sampler = node.inputs.sampler_name || sampler;
                            seed = node.inputs.seed || node.inputs.noise_seed || seed;
                        }
                        
                        // New ComfyUI node types for modern workflows
                        if (node.class_type === 'CFGGuider' && node.inputs && node.inputs.cfg) {
                            cfgScale = node.inputs.cfg;
                        }
                        
                        if (node.class_type === 'BasicScheduler' && node.inputs && node.inputs.steps) {
                            steps = node.inputs.steps;
                        }
                        
                        if (node.class_type === 'KSamplerSelect' && node.inputs && node.inputs.sampler_name) {
                            sampler = node.inputs.sampler_name;
                        }
                        
                        if ((node.class_type === 'RandomNoise' || node.class_type === 'SeedHistory') && node.inputs) {
                            if (node.inputs.noise_seed) {
                                // RandomNoise nodes might reference other nodes, resolve if needed
                                seed = Array.isArray(node.inputs.noise_seed) ? node.inputs.noise_seed[0] : node.inputs.noise_seed;
                            } else if (node.inputs.seed) {
                                seed = node.inputs.seed;
                            }
                        }
                    }
                    
                    // Process collected text nodes to identify positive/negative prompts
                    // if we haven't found them from PromptManager
                    for (const textNode of textNodes) {
                        const text = textNode.text;
                        // Ensure text is a string before calling toLowerCase
                        if (!text || typeof text !== 'string') {
                            continue;
                        }
                        const textLower = text.toLowerCase();
                        
                        // Skip if this is the PromptManager text we already have
                        if (textNode.classType === 'PromptManager' || textNode.classType === 'PromptManagerText') {
                            continue;
                        }
                        
                        // Identify negative prompts by common patterns
                        if (textNode.classType === 'CLIPTextEncode') {
                            if (textLower.includes('bad anatomy') || textLower.includes('unfinished') || 
                                textLower.includes('censored') || textLower.includes('weird anatomy') ||
                                textLower.includes('negative') || textLower.includes('embedding:') ||
                                textLower.includes('worst quality') || textLower.includes('low quality')) {
                                negativePrompt = text;
                            } else if (positivePrompt === 'No prompt found') {
                                // If we haven't found a positive prompt yet, this might be it
                                positivePrompt = text;
                            }
                        }
                    }
                }

                // Also try to parse from workflow data if we didn't find everything in prompt
                if (comfyData.workflow && comfyData.workflow.nodes) {
                    for (const node of comfyData.workflow.nodes) {
                        // Checkpoint loaders
                        if ((node.type === 'CheckpointLoaderSimple' || 
                             node.type === 'UNETLoader' ||
                             node.type === 'DualCLIPLoader') && node.widgets_values) {
                            checkpoint = node.widgets_values[0] || checkpoint;
                        }
                        
                        // PromptManager nodes in workflow
                        if (node.type === 'PromptManager' && node.widgets_values) {
                            // The first widget value is usually the text
                            if (node.widgets_values[0] && positivePrompt === 'No prompt found') {
                                positivePrompt = node.widgets_values[0];
                            }
                        }
                        
                        if (node.type === 'PromptManagerText' && node.widgets_values) {
                            if (node.widgets_values[0] && positivePrompt === 'No prompt found') {
                                positivePrompt = node.widgets_values[0];
                            }
                        }
                        
                        // CLIPTextEncode nodes
                        if (node.type === 'CLIPTextEncode' && node.widgets_values && node.widgets_values[0]) {
                            const text = node.widgets_values[0];
                            // Ensure text is a string before calling toLowerCase
                            if (text && typeof text === 'string') {
                                const textLower = text.toLowerCase();
                                
                                if (textLower.includes('bad anatomy') || textLower.includes('unfinished') || 
                                    textLower.includes('censored') || textLower.includes('negative') ||
                                    textLower.includes('worst quality') || textLower.includes('low quality')) {
                                    negativePrompt = text;
                                } else if (positivePrompt === 'No prompt found') {
                                    positivePrompt = text;
                                }
                            }
                        }
                        
                        // Samplers
                        if ((node.type === 'KSampler' || node.type === 'KSamplerAdvanced' ||
                             node.type === 'SamplerCustom' || node.type === 'SamplerCustomAdvanced') && node.widgets_values) {
                            if (node.widgets_values.length >= 4) {
                                seed = node.widgets_values[0] || seed;
                                steps = node.widgets_values[1] || steps;
                                cfgScale = node.widgets_values[2] || cfgScale;
                                sampler = node.widgets_values[3] || sampler;
                            }
                        }
                    }
                }

                return {
                    checkpoint,
                    positivePrompt,
                    negativePrompt,
                    steps,
                    cfgScale,
                    sampler,
                    seed,
                    workflow: comfyData.workflow,
                    imagePath
                };
            }

            // Settings functionality
            showSettings() {
                this.loadSettingsFromStorage();
                document.getElementById('settingsModal').classList.remove('hidden');
                document.getElementById('settingsModal').classList.add('flex');
                document.body.style.overflow = 'hidden';
            }

            hideSettings() {
                document.getElementById('settingsModal').classList.add('hidden');
                document.getElementById('settingsModal').classList.remove('flex');
                document.body.style.overflow = '';
            }

            loadSettingsFromStorage() {
                const settings = this.getSettings();
                
                // Load checkbox settings
                document.getElementById('lazyLoadingToggle').checked = settings.lazyLoading;
                document.getElementById('autoLoadMetadataToggle').checked = settings.autoLoadMetadata;
                document.getElementById('cacheMetadataToggle').checked = settings.cacheMetadata;
                document.getElementById('showFilePathsToggle').checked = settings.showFilePaths;
                document.getElementById('showImageInfoToggle').checked = settings.showImageInfo;
                document.getElementById('debugModeToggle').checked = settings.debugMode;
                document.getElementById('checkThumbnailsAtStartup').checked = settings.checkThumbnailsAtStartup;
                
                // Load video settings
                document.getElementById('videoAutoplayToggle').checked = settings.videoAutoplay;
                document.getElementById('videoMuteToggle').checked = settings.videoMute;
                document.getElementById('videoLoopToggle').checked = settings.videoLoop;
                
                // Load select settings
                document.getElementById('imageQualitySelect').value = settings.imageQuality;
                document.getElementById('defaultViewModeSelect').value = settings.defaultViewMode;
                document.getElementById('defaultLimitSelect').value = settings.defaultLimit;
                document.getElementById('gridColumnsSelect').value = settings.gridColumns;
                
                // Load input settings
                document.getElementById('apiTimeoutInput').value = settings.apiTimeout;
                
                // Update thumbnail status
                document.getElementById('thumbnailStatus').textContent = settings.thumbnailsGenerated ? 'Generated' : 'Not generated';
            }

            getSettings() {
                const defaultSettings = {
                    lazyLoading: true,
                    imageQuality: 'medium',
                    defaultViewMode: 'grid',
                    defaultLimit: 100,
                    gridColumns: 8,
                    showImageInfo: true,
                    autoLoadMetadata: true,
                    cacheMetadata: true,
                    showFilePaths: true,
                    debugMode: false,
                    apiTimeout: 30,
                    thumbnailsGenerated: false,
                    checkThumbnailsAtStartup: true,
                    // Video settings
                    videoAutoplay: false,
                    videoMute: true,
                    videoLoop: true
                };

                try {
                    const stored = localStorage.getItem('gallerySettings');
                    if (stored) {
                        return { ...defaultSettings, ...JSON.parse(stored) };
                    }
                } catch (e) {
                    console.warn('Failed to load settings from localStorage:', e);
                }

                return defaultSettings;
            }

            saveSettings() {
                const settings = {
                    lazyLoading: document.getElementById('lazyLoadingToggle').checked,
                    imageQuality: document.getElementById('imageQualitySelect').value,
                    defaultViewMode: document.getElementById('defaultViewModeSelect').value,
                    defaultLimit: parseInt(document.getElementById('defaultLimitSelect').value),
                    gridColumns: parseInt(document.getElementById('gridColumnsSelect').value),
                    showImageInfo: document.getElementById('showImageInfoToggle').checked,
                    autoLoadMetadata: document.getElementById('autoLoadMetadataToggle').checked,
                    cacheMetadata: document.getElementById('cacheMetadataToggle').checked,
                    showFilePaths: document.getElementById('showFilePathsToggle').checked,
                    debugMode: document.getElementById('debugModeToggle').checked,
                    apiTimeout: parseInt(document.getElementById('apiTimeoutInput').value),
                    thumbnailsGenerated: this.getSettings().thumbnailsGenerated, // Preserve this
                    checkThumbnailsAtStartup: document.getElementById('checkThumbnailsAtStartup').checked,
                    // Video settings
                    videoAutoplay: document.getElementById('videoAutoplayToggle').checked,
                    videoMute: document.getElementById('videoMuteToggle').checked,
                    videoLoop: document.getElementById('videoLoopToggle').checked
                };

                try {
                    localStorage.setItem('gallerySettings', JSON.stringify(settings));
                    this.showNotification('Settings saved successfully!', 'success');
                    this.hideSettings();
                    
                    // Apply some settings immediately
                    this.applySettings(settings);
                } catch (e) {
                    console.error('Failed to save settings:', e);
                    this.showNotification('Failed to save settings', 'error');
                }
            }

            resetSettings() {
                if (confirm('Reset all settings to defaults? This cannot be undone.')) {
                    localStorage.removeItem('gallerySettings');
                    this.loadSettingsFromStorage();
                    this.showNotification('Settings reset to defaults', 'success');
                }
            }

            applySettings(settings) {
                // Apply grid columns
                this.updateGridColumns(settings.gridColumns);
                
                // Apply default view mode if different
                if (this.viewMode !== settings.defaultViewMode) {
                    this.setViewMode(settings.defaultViewMode);
                }
                
                // Apply default limit if different
                if (this.limit !== settings.defaultLimit) {
                    this.limit = settings.defaultLimit;
                    document.getElementById('limitSelector').value = settings.defaultLimit;
                }
            }

            updateGridColumns(columns) {
                const gridContainer = document.getElementById('galleryGrid');
                if (gridContainer) {
                    // Remove existing column classes
                    gridContainer.className = gridContainer.className.replace(/xl:grid-cols-\d+/g, '');
                    // Add new column class
                    gridContainer.classList.add(`xl:grid-cols-${columns}`);
                }
            }

            async generateThumbnails() {
                const btn = document.getElementById('generateThumbnailsBtn');
                const status = document.getElementById('thumbnailStatus');
                const progressContainer = document.getElementById('thumbnailProgress');
                const progressBar = document.getElementById('progressBar');
                const progressText = document.getElementById('progressText');
                const progressPercent = document.getElementById('progressPercent');
                const progressDetails = document.getElementById('progressDetails');
                const progressETA = document.getElementById('progressETA');
                
                btn.disabled = true;
                btn.textContent = 'Generating...';
                status.textContent = 'Initializing thumbnail generation...';
                progressContainer.classList.remove('hidden');
                
                // Initialize progress display
                progressBar.style.width = '0%';
                progressPercent.textContent = 'Starting...';
                progressText.textContent = 'Connecting to server...';
                progressDetails.textContent = 'Preparing to scan output directory...';
                progressETA.textContent = '';
                
                try {
                    // Start thumbnail generation with progress updates
                    const result = await this.generateThumbnailsWithProgress({
                        quality: document.getElementById('imageQualitySelect').value
                    });
                    
                    // Update settings on success
                    const settings = this.getSettings();
                    settings.thumbnailsGenerated = true;
                    localStorage.setItem('gallerySettings', JSON.stringify(settings));
                    
                    status.textContent = 'Generation completed';
                    progressText.textContent = 'Completed!';
                    progressDetails.textContent = `Generated ${result.count} new, skipped ${result.skipped || 0} existing (${result.total_images} total) in ${result.elapsed_time}s`;
                    
                    this.showNotification('Thumbnails generated successfully!', 'success');
                    
                    // Hide progress after a delay
                    setTimeout(() => {
                        progressContainer.classList.add('hidden');
                    }, 3000);
                    
                } catch (error) {
                    console.error('Thumbnail generation error:', error);
                    console.error('Error details:', error.stack);
                    status.textContent = 'Generation failed';
                    progressText.textContent = 'Failed';
                    progressDetails.textContent = error.message;
                    progressBar.style.width = '0%';
                    progressBar.classList.add('bg-pm-error');
                    progressPercent.textContent = 'Error';

                    this.showNotification('Failed to generate thumbnails: ' + error.message, 'error');

                    // Hide progress after a delay
                    setTimeout(() => {
                        progressContainer.classList.add('hidden');
                        progressBar.classList.remove('bg-pm-error');
                    }, 5000);
                } finally {
                    btn.disabled = false;
                    btn.textContent = 'Generate';
                }
            }

            async generateThumbnailsWithProgress(options) {
                const progressBar = document.getElementById('progressBar');
                const progressText = document.getElementById('progressText');
                const progressPercent = document.getElementById('progressPercent');
                const progressDetails = document.getElementById('progressDetails');
                const progressETA = document.getElementById('progressETA');
                const cancelBtn = document.getElementById('cancelThumbnailsBtn');
                
                return new Promise((resolve, reject) => {
                    // Show cancel button
                    cancelBtn.classList.remove('hidden');
                    
                    console.log(`Starting thumbnail generation with quality: ${options.quality}`);
                    
                    // Set up Server-Sent Events
                    const eventSource = new EventSource(`/prompt_manager/images/generate-thumbnails/progress?quality=${options.quality}`);
                    
                    // Log connection
                    console.log('EventSource connected for thumbnail generation progress');
                    
                    let cancelled = false;
                    let resultData = null;
                    
                    // Handle cancellation
                    const cancelHandler = () => {
                        cancelled = true;
                        eventSource.close();
                        cancelBtn.classList.add('hidden');
                        reject(new Error('Thumbnail generation cancelled by user'));
                    };
                    cancelBtn.addEventListener('click', cancelHandler);
                    
                    // Handle scanning/status events
                    eventSource.addEventListener('status', (event) => {
                        if (cancelled) return;
                        const data = JSON.parse(event.data);
                        progressText.textContent = data.message || 'Processing...';
                        if (data.phase === 'scanning') {
                            progressDetails.textContent = 'Scanning output folder for images and videos...';
                            progressBar.style.width = '0%';
                            progressPercent.textContent = 'Scanning...';
                        } else {
                            progressDetails.textContent = 'Preparing thumbnail generation...';
                        }
                        
                        // Log status updates
                        if (this.getSettings().debugMode) {
                            console.log(`Thumbnail generation status: ${data.phase} - ${data.message}`);
                        }
                    });
                    
                    // Handle start event
                    eventSource.addEventListener('start', (event) => {
                        if (cancelled) return;
                        const data = JSON.parse(event.data);
                        // Show detailed file type breakdown if available
                        const fileBreakdown = (data.image_count !== undefined && data.video_count !== undefined) 
                            ? ` (${data.image_count} images, ${data.video_count} videos)`
                            : '';
                        progressText.textContent = `Starting to process ${data.total_images} media files${fileBreakdown}...`;
                        progressDetails.textContent = data.message || `Found ${data.total_images} images and videos to process`;
                        progressBar.style.width = '0%';
                        progressPercent.textContent = '0%';
                        progressETA.textContent = 'Calculating...';
                        
                        // Log start event
                        if (this.getSettings().debugMode) {
                            console.log(`Thumbnail generation started: ${data.total_images} files to process`);
                        }
                    });
                    
                    // Handle progress updates
                    eventSource.addEventListener('progress', (event) => {
                        if (cancelled) return;
                        const data = JSON.parse(event.data);
                        
                        // Update progress bar
                        progressBar.style.width = `${data.percentage}%`;
                        progressPercent.textContent = `${data.percentage}%`;
                        
                        // Update text with current file being processed and action
                        const fileInfo = data.current_file ? ` - ${data.current_file}` : '';
                        const actionText = data.action === 'skipped' ? ' [Skipping]' : data.action === 'generating' ? ' [Generating]' : '';
                        progressText.textContent = `Processing ${data.file_type || 'file'}... (${data.processed}/${data.total_images})${fileInfo}${actionText}`;
                        
                        // More detailed progress information
                        const elapsedText = data.elapsed ? ` | Time: ${Math.floor(data.elapsed)}s` : '';
                        progressDetails.textContent = `Generated: ${data.generated}, Skipped: ${data.skipped} | Rate: ${data.rate} img/s${elapsedText}`;
                        
                        // Update ETA
                        if (data.eta > 0 && data.eta < 3600) {
                            const etaText = data.eta > 60 ? `${Math.floor(data.eta / 60)}m ${Math.floor(data.eta % 60)}s` : `${Math.floor(data.eta)}s`;
                            progressETA.textContent = `ETA: ${etaText}`;
                        } else {
                            progressETA.textContent = '';
                        }
                        
                        // Log to console for debugging if debug mode is enabled
                        if (this.getSettings().debugMode) {
                            console.log(`Thumbnail generation progress: ${data.processed}/${data.total_images} - ${data.current_file}`);
                        }
                    });
                    
                    // Handle completion
                    eventSource.addEventListener('complete', (event) => {
                        if (cancelled) return;
                        resultData = JSON.parse(event.data);
                        
                        // Update final progress
                        progressBar.style.width = '100%';
                        progressPercent.textContent = '100%';
                        progressText.textContent = 'Completed!';
                        
                        // Show detailed completion message
                        const errorCount = resultData.error_count || (resultData.errors ? resultData.errors.length : 0);
                        const errors = errorCount > 0 ? ` (${errorCount} errors)` : '';
                        const rateText = resultData.processing_rate ? ` at ${resultData.processing_rate} img/s` : '';
                        progressDetails.textContent = `Generated: ${resultData.count} new, Skipped: ${resultData.skipped} existing | Total: ${resultData.total_images} files | Time: ${resultData.elapsed_time}s${rateText}${errors}`;
                        
                        // Show first error if any occurred
                        if (resultData.errors && resultData.errors.length > 0 && this.getSettings().debugMode) {
                            console.error('First thumbnail error:', resultData.errors[0]);
                        }
                        progressETA.textContent = 'Done';
                        
                        // Log completion details
                        if (this.getSettings().debugMode) {
                            console.log('Thumbnail generation completed:', resultData);
                            if (resultData.errors && resultData.errors.length > 0) {
                                console.warn('Thumbnail generation errors:', resultData.errors);
                            }
                        }
                        
                        // Clean up
                        eventSource.close();
                        cancelBtn.removeEventListener('click', cancelHandler);
                        cancelBtn.classList.add('hidden');
                        
                        resolve(resultData);
                    });
                    
                    // Handle errors
                    eventSource.addEventListener('error', (event) => {
                        if (cancelled) return;
                        const data = JSON.parse(event.data);
                        
                        // Clean up
                        eventSource.close();
                        cancelBtn.removeEventListener('click', cancelHandler);
                        cancelBtn.classList.add('hidden');
                        
                        reject(new Error(data.error || 'Thumbnail generation failed'));
                    });
                    
                    // Handle EventSource errors
                    eventSource.onerror = (error) => {
                        if (cancelled) return;
                        console.error('EventSource error:', error);
                        console.error('EventSource readyState:', eventSource.readyState);
                        
                        // Check if connection is closing normally
                        if (eventSource.readyState === EventSource.CLOSED) {
                            console.log('EventSource connection closed');
                        } else {
                            console.error('EventSource connection failed');
                        }
                        
                        // Clean up
                        eventSource.close();
                        cancelBtn.removeEventListener('click', cancelHandler);
                        cancelBtn.classList.add('hidden');
                        
                        // Only reject if we don't have result data
                        if (!resultData) {
                            reject(new Error('Connection to server lost during thumbnail generation'));
                        }
                    };
                });
            }


            async clearCache() {
                if (confirm('Clear PromptManager cache and thumbnails? This will NOT affect your original images.')) {
                    try {
                        // Clear only PromptManager-related browser caches (never touch user images)
                        if ('caches' in window) {
                            const cacheNames = await caches.keys();
                            // Only clear caches that are specifically ours
                            const ourCaches = cacheNames.filter(name => 
                                name.includes('prompt-manager') || 
                                name.includes('gallery') ||
                                name.includes('thumbnail')
                            );
                            await Promise.all(ourCaches.map(name => caches.delete(name)));
                        }
                        
                        // Clear only our localStorage entries (never touch user data)
                        Object.keys(localStorage).forEach(key => {
                            if (key.startsWith('gallery_cache_') || 
                                key.startsWith('metadata_cache_') ||
                                key.startsWith('promptManager_') ||
                                key.startsWith('pm_')) {
                                localStorage.removeItem(key);
                            }
                        });
                        
                        // Call backend to clear server-side thumbnails (only our generated thumbnails)
                        try {
                            const response = await fetch('/prompt_manager/images/clear-thumbnails', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' }
                            });
                            
                            if (response.ok) {
                                const data = await response.json();
                                console.log('Server thumbnails cleared:', data);
                            }
                        } catch (e) {
                            console.warn('Could not clear server thumbnails:', e);
                            // Not a critical error - continue with local cleanup
                        }
                        
                        // Update thumbnail status
                        const settings = this.getSettings();
                        settings.thumbnailsGenerated = false;
                        localStorage.setItem('gallerySettings', JSON.stringify(settings));
                        document.getElementById('thumbnailStatus').textContent = 'Not generated';
                        
                        this.showNotification('PromptManager cache cleared successfully! Original images untouched.', 'success');
                    } catch (error) {
                        console.error('Clear cache error:', error);
                        this.showNotification('Failed to clear cache', 'error');
                    }
                }
            }

            async rescanFolder() {
                const btn = document.getElementById('rescanFolderBtn');
                btn.disabled = true;
                btn.textContent = 'Scanning...';
                
                try {
                    // Force reload images
                    await this.loadImages();
                    this.showNotification('Folder rescanned successfully!', 'success');
                } catch (error) {
                    console.error('Rescan error:', error);
                    this.showNotification('Failed to rescan folder', 'error');
                } finally {
                    btn.disabled = false;
                    btn.textContent = 'Rescan';
                }
            }

            async scanDuplicates() {
                this.showDuplicatesModal();
                
                const btn = document.getElementById('scanDuplicatesBtn');
                btn.disabled = true;
                btn.textContent = 'Scanning...';
                
                try {
                    const response = await fetch('/prompt_manager/scan_duplicates');
                    const data = await response.json();
                    
                    if (data.success) {
                        this.displayDuplicates(data.duplicates || []);
                        this.showNotification(`Found ${data.duplicates?.length || 0} duplicate image groups`, 'info');
                    } else {
                        throw new Error(data.error || 'Failed to scan for duplicate images');
                    }
                } catch (error) {
                    console.error('Duplicate scan error:', error);
                    this.showNotification('Failed to scan for duplicate images', 'error');
                    this.hideDuplicatesModal();
                } finally {
                    btn.disabled = false;
                    btn.textContent = 'Scan';
                }
            }

            showDuplicatesModal() {
                document.getElementById('duplicatesModal').classList.remove('hidden');
                document.getElementById('duplicatesModal').classList.add('flex');
                document.body.style.overflow = 'hidden';
                
                // Reset modal state
                document.getElementById('duplicatesScanStatus').classList.remove('hidden');
                document.getElementById('duplicatesContent').classList.add('hidden');
                document.getElementById('duplicatesFooter').classList.add('hidden');
            }

            hideDuplicatesModal() {
                document.getElementById('duplicatesModal').classList.add('hidden');
                document.getElementById('duplicatesModal').classList.remove('flex');
                document.body.style.overflow = '';
            }

            displayDuplicates(duplicates) {
                const scanStatus = document.getElementById('duplicatesScanStatus');
                const content = document.getElementById('duplicatesContent');
                const footer = document.getElementById('duplicatesFooter');
                const list = document.getElementById('duplicatesList');
                const count = document.getElementById('duplicatesCount');
                
                scanStatus.classList.add('hidden');
                
                if (duplicates.length === 0) {
                    list.innerHTML = '<div class="text-center py-4 text-pm-secondary">No duplicate images found! 🎉</div>';
                    content.classList.remove('hidden');
                    return;
                }
                
                count.textContent = duplicates.length;
                list.innerHTML = '';
                
                duplicates.forEach((group, groupIndex) => {
                    const groupEl = document.createElement('div');
                    groupEl.className = 'bg-pm-surface rounded-pm-md p-4 border border-pm';
                    
                    groupEl.innerHTML = `
                        <div class="mb-3 text-sm font-medium text-pm-secondary">
                            Duplicate Group ${groupIndex + 1} (${group.images.length} identical ${group.images[0].media_type}s)
                        </div>
                        <div class="mb-3 p-3 bg-pm-primary rounded text-sm text-pm-secondary">
                            <strong>Content Hash:</strong> ${group.hash.substring(0, 16)}...
                        </div>
                        <div class="grid grid-cols-1 lg:grid-cols-2 gap-3">
                            ${group.images.map((image, imageIndex) => `
                                <div class="flex items-start p-3 bg-pm-primary rounded border border-pm ${imageIndex === 0 ? 'border-pm-success' : ''}">
                                    <div class="flex-shrink-0 mr-3">
                                        ${image.media_type === 'video' ? `
                                            <div class="w-20 h-20 bg-pm-input rounded flex items-center justify-center relative">
                                                ${image.thumbnail_url ? `
                                                    <img src="${image.thumbnail_url}" alt="Video thumbnail" class="w-full h-full object-cover rounded">
                                                ` : `
                                                    <svg class="w-8 h-8 text-pm-secondary" fill="currentColor" viewBox="0 0 20 20">
                                                        <path d="M2 6a2 2 0 012-2h6l2 2h6a2 2 0 012 2v6a2 2 0 01-2 2H4a2 2 0 01-2-2V6zM14.553 7.106A1 1 0 0014 8v4a1 1 0 00.553.894l2 1A1 1 0 0018 13V7a1 1 0 00-1.447-.894l-2 1z"/>
                                                    </svg>
                                                `}
                                                <div class="absolute bottom-0 right-0 bg-black/75 text-pm text-xs px-1 rounded">VIDEO</div>
                                            </div>
                                        ` : `
                                            <img src="${image.thumbnail_url || image.url}" alt="${this.escapeHtml(image.filename)}"
                                                 class="w-20 h-20 object-cover rounded cursor-pointer hover:opacity-80"
                                                 onclick="this.parentElement.parentElement.querySelector('.image-preview').click()">
                                        `}
                                    </div>
                                    <div class="flex-1 min-w-0">
                                        <div class="text-sm text-pm-secondary mb-2">
                                            <div class="font-medium truncate" title="${this.escapeHtml(image.filename)}">${this.escapeHtml(image.filename)}</div>
                                            <div class="text-xs text-pm-secondary mt-1">
                                                <span class="font-medium">Size:</span> ${this.formatFileSize(image.size)} •
                                                <span class="font-medium">Modified:</span> ${new Date(image.modified_time * 1000).toLocaleDateString()}
                                            </div>
                                            <div class="text-xs text-pm-muted mt-1 truncate" title="${image.relative_path}">
                                                ${image.relative_path}
                                            </div>
                                        </div>
                                        <div class="flex items-center justify-between">
                                            <div class="flex items-center space-x-2">
                                                <button class="image-preview text-xs bg-pm-accent hover:bg-pm-accent-hover text-pm px-2 py-1 rounded"
                                                        onclick="window.open('${image.url}', '_blank')">
                                                    View Full
                                                </button>
                                            </div>
                                            <div class="flex items-center">
                                                ${imageIndex === 0 ? `
                                                    <span class="px-3 py-1 bg-pm-success text-pm text-xs rounded">KEEP (Oldest)</span>
                                                ` : `
                                                    <label class="flex items-center">
                                                        <input type="checkbox" class="duplicate-checkbox" data-group="${groupIndex}" data-image-path="${image.path}"
                                                               class="w-4 h-4 text-pm-error bg-pm-input border-pm rounded focus:ring-pm-error">
                                                        <span class="ml-2 text-sm text-pm-error">Delete</span>
                                                    </label>
                                                `}
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            `).join('')}
                        </div>
                    `;
                    
                    list.appendChild(groupEl);
                });
                
                content.classList.remove('hidden');
                footer.classList.remove('hidden');
            }

            selectAllDuplicates() {
                const checkboxes = document.querySelectorAll('.duplicate-checkbox');
                const selectBtn = document.getElementById('selectAllBtn');
                
                const allChecked = Array.from(checkboxes).every(cb => cb.checked);
                
                checkboxes.forEach(cb => {
                    cb.checked = !allChecked;
                });
                
                selectBtn.textContent = allChecked ? 'Select All to Delete' : 'Deselect All';
            }

            async removeDuplicates() {
                const checkboxes = document.querySelectorAll('.duplicate-checkbox:checked');
                
                if (checkboxes.length === 0) {
                    this.showNotification('No duplicate images selected for removal', 'warning');
                    return;
                }
                
                const confirmation = confirm(`Are you sure you want to permanently delete ${checkboxes.length} duplicate image files? This action cannot be undone!`);
                if (!confirmation) return;
                
                const btn = document.getElementById('removeDuplicatesBtn');
                btn.disabled = true;
                btn.textContent = 'Deleting...';
                
                try {
                    const imagePaths = Array.from(checkboxes).map(cb => cb.dataset.imagePath);
                    
                    const response = await fetch('/prompt_manager/delete_duplicate_images', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({ image_paths: imagePaths })
                    });
                    
                    const data = await response.json();
                    
                    if (data.success) {
                        this.showNotification(`Successfully deleted ${imagePaths.length} duplicate image files`, 'success');
                        this.hideDuplicatesModal();
                        this.loadImages(); // Refresh the gallery
                    } else {
                        throw new Error(data.error || 'Failed to delete duplicate images');
                    }
                } catch (error) {
                    console.error('Remove duplicates error:', error);
                    this.showNotification('Failed to delete duplicate images', 'error');
                } finally {
                    btn.disabled = false;
                    btn.textContent = 'Remove Selected';
                }
            }

            formatFileSize(bytes) {
                if (bytes === 0) return '0 Bytes';
                const k = 1024;
                const sizes = ['Bytes', 'KB', 'MB', 'GB'];
                const i = Math.floor(Math.log(bytes) / Math.log(k));
                return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
            }

            changeLimit(newLimit) {
                this.limit = newLimit;
                this.currentPage = 1;
                this.loadImages();
            }

            updatePagination() {
                const totalPages = Math.ceil(this.total / this.limit);
                const hasPrev = this.currentPage > 1;
                const hasNext = this.currentPage < totalPages;

                document.getElementById('prevPageBtn').disabled = !hasPrev;
                document.getElementById('nextPageBtn').disabled = !hasNext;
                document.getElementById('pageInfo').textContent = `of ${totalPages}`;
                
                const pageInput = document.getElementById('pageInput');
                pageInput.value = this.currentPage;
                pageInput.max = totalPages;

                if (totalPages > 1) {
                    document.getElementById('paginationControls').classList.remove('hidden');
                } else {
                    document.getElementById('paginationControls').classList.add('hidden');
                }
            }

            previousPage() {
                if (this.currentPage > 1) {
                    this.currentPage--;
                    this.loadImages();
                }
            }

            nextPage() {
                const totalPages = Math.ceil(this.total / this.limit);
                if (this.currentPage < totalPages) {
                    this.currentPage++;
                    this.loadImages();
                }
            }

            goToPage(page) {
                const totalPages = Math.ceil(this.total / this.limit);
                if (isNaN(page) || page < 1 || page > totalPages) {
                    // Reset to current page if invalid input
                    document.getElementById('pageInput').value = this.currentPage;
                    return;
                }
                
                if (page !== this.currentPage) {
                    this.currentPage = page;
                    this.loadImages();
                }
            }

            async checkThumbnailsAtStartup() {
                // Wait a moment to ensure page is fully loaded
                setTimeout(async () => {
                    const settings = this.getSettings();
                    
                    // Only check if the feature is enabled
                    if (!settings.checkThumbnailsAtStartup) {
                        console.debug('Thumbnail startup check disabled in settings');
                        return;
                    }
                    
                    try {
                        // Check a sample of images to see if any are missing thumbnails
                        const response = await fetch('/prompt_manager/images/output?limit=50&offset=0');
                        const data = await response.json();
                        
                        console.debug('Checking images for missing thumbnails:', data);
                        
                        if (data.success && data.images && data.images.length > 0) {
                            // Count images missing thumbnails
                            let missingThumbnails = 0;
                            for (const image of data.images) {
                                if (!image.thumbnail_url) {
                                    missingThumbnails++;
                                }
                            }
                            
                            console.debug(`Found ${missingThumbnails} images without thumbnails in sample of ${data.images.length}`);
                            
                            // If more than 10% of sampled images are missing thumbnails, show prompt
                            const missingPercentage = (missingThumbnails / data.images.length) * 100;
                            if (missingThumbnails > 0 && missingPercentage > 10) {
                                // Estimate total missing based on sample
                                const estimatedMissing = Math.round((missingThumbnails / data.images.length) * data.total);
                                console.debug(`Estimated ${estimatedMissing} total images missing thumbnails (${missingPercentage.toFixed(1)}% of sample)`);
                                this.showThumbnailGenerationPrompt(data.total, estimatedMissing);
                            } else if (missingThumbnails > 0) {
                                console.debug(`Only ${missingPercentage.toFixed(1)}% of images missing thumbnails, not showing prompt`);
                            } else {
                                console.debug('All sampled images have thumbnails');
                            }
                        } else {
                            console.debug('No images found or failed to fetch images');
                        }
                    } catch (error) {
                        console.debug('Could not check for missing thumbnails at startup:', error);
                    }
                }, 1000); // Wait 1 second for page to settle
            }

            showThumbnailGenerationPrompt(imageCount, missingCount = imageCount) {
                const modal = document.createElement('div');
                modal.className = 'fixed inset-0 bg-black/50 flex items-center justify-center z-50';
                modal.innerHTML = `
                    <div class="bg-pm-surface rounded-pm-md max-w-2xl w-full mx-4 border border-pm">
                        <div class="p-4">
                            <h3 class="text-sm font-semibold text-pm mb-3 flex items-center">
                                <span class="mr-2">🚀</span>
                                Generate Thumbnails?
                            </h3>
                            <p class="text-pm-secondary mb-4">
                                Found ${missingCount} images without thumbnails (${imageCount} total images). Would you like to generate thumbnails for faster loading?
                            </p>
                            <p class="text-sm text-pm-muted mb-3">
                                💡 Thumbnails speed up gallery loading significantly. You can change this setting in Gallery Settings.
                            </p>
                            <div class="flex space-x-3">
                                <button id="thumbnailPromptNo" class="flex-1 px-4 py-2 bg-pm-input hover:bg-pm-hover text-pm rounded-pm-md transition-colors">
                                    Not Now
                                </button>
                                <button id="thumbnailPromptYes" class="flex-1 px-4 py-2 bg-pm-accent hover:bg-pm-accent-hover text-pm rounded-pm-md transition-colors">
                                    Generate
                                </button>
                            </div>
                        </div>
                    </div>
                `;
                
                document.body.appendChild(modal);
                
                // Handle button clicks
                document.getElementById('thumbnailPromptNo').addEventListener('click', () => {
                    document.body.removeChild(modal);
                });
                
                document.getElementById('thumbnailPromptYes').addEventListener('click', () => {
                    // Don't remove modal yet - transform it to show progress
                    this.startThumbnailGenerationInModal(modal, imageCount, missingCount);
                });
                
                // Close on outside click
                modal.addEventListener('click', (e) => {
                    if (e.target === modal) {
                        document.body.removeChild(modal);
                    }
                });
            }

            async startThumbnailGenerationInModal(modal, imageCount, missingCount) {
                // Transform the modal to show progress
                const modalContent = modal.querySelector('.bg-pm-surface');
                modalContent.innerHTML = `
                    <div class="p-4">
                        <h3 class="text-sm font-semibold text-pm mb-3 flex items-center">
                            <span class="mr-2">🚀</span>
                            Generating Thumbnails
                        </h3>
                        <p class="text-pm-secondary mb-4">
                            Processing ${missingCount} images out of ${imageCount} total...
                        </p>

                        <!-- Progress Section -->
                        <div class="mb-3">
                            <div class="flex items-center justify-between text-sm text-pm-secondary mb-2">
                                <span id="modalProgressText">Initializing...</span>
                                <span id="modalProgressPercent">0%</span>
                            </div>
                            <div class="w-full bg-pm-input rounded-full h-3">
                                <div id="modalProgressBar" class="bg-pm-accent h-3 rounded-full transition-all duration-300" style="width: 0%"></div>
                            </div>
                            <div class="flex items-center justify-between text-xs text-pm-muted mt-2">
                                <span id="modalProgressDetails">Starting thumbnail generation...</span>
                                <span id="modalProgressETA" class="text-pm-muted"></span>
                            </div>
                        </div>

                        <!-- Status Messages -->
                        <div id="modalStatusMessages" class="text-sm text-pm-secondary mb-4 max-h-40 overflow-y-auto">
                            <div class="text-pm-accent">• Starting thumbnail generation...</div>
                        </div>

                        <!-- Buttons -->
                        <div class="flex space-x-3">
                            <button id="modalCancelBtn" class="flex-1 px-4 py-2 bg-pm-error hover:bg-pm-error text-pm rounded-pm-md transition-colors">
                                Cancel
                            </button>
                            <button id="modalCloseBtn" class="flex-1 px-4 py-2 bg-pm-input hover:bg-pm-hover text-pm rounded-pm-md transition-colors hidden">
                                Close
                            </button>
                        </div>
                    </div>
                `;
                
                // Setup progress tracking
                let isCancelled = false;
                const progressText = modal.querySelector('#modalProgressText');
                const progressPercent = modal.querySelector('#modalProgressPercent');
                const progressBar = modal.querySelector('#modalProgressBar');
                const progressDetails = modal.querySelector('#modalProgressDetails');
                const progressETA = modal.querySelector('#modalProgressETA');
                const statusMessages = modal.querySelector('#modalStatusMessages');
                const cancelBtn = modal.querySelector('#modalCancelBtn');
                const closeBtn = modal.querySelector('#modalCloseBtn');
                
                // Add status message helper
                const addStatusMessage = (message, type = 'info') => {
                    const colors = {
                        'info': 'text-pm-secondary',
                        'success': 'text-pm-success',
                        'error': 'text-pm-error',
                        'warning': 'text-pm-warning'
                    };
                    const msgEl = document.createElement('div');
                    msgEl.className = colors[type] || colors.info;
                    msgEl.textContent = `• ${message}`;
                    statusMessages.appendChild(msgEl);
                    statusMessages.scrollTop = statusMessages.scrollHeight;
                };
                
                // Handle cancel
                cancelBtn.addEventListener('click', () => {
                    isCancelled = true;
                    addStatusMessage('Cancellation requested...', 'warning');
                    cancelBtn.disabled = true;
                    cancelBtn.textContent = 'Cancelling...';
                });
                
                // Handle close (only appears when done)
                closeBtn.addEventListener('click', () => {
                    document.body.removeChild(modal);
                    // Refresh the gallery to show new thumbnails
                    this.loadImages();
                });
                
                // Disable outside click while processing
                modal.removeEventListener('click', modal.clickHandler);
                
                try {
                    addStatusMessage('Connecting to thumbnail generation service...');
                    
                    // Start thumbnail generation with progress monitoring
                    await this.generateThumbnailsWithProgressInModal({
                        progressText,
                        progressPercent,
                        progressBar,
                        progressDetails,
                        progressETA,
                        addStatusMessage,
                        isCancelled: () => isCancelled
                    });
                    
                    // Show completion
                    if (!isCancelled) {
                        progressText.textContent = 'Completed!';
                        progressPercent.textContent = '100%';
                        progressBar.style.width = '100%';
                        addStatusMessage('Thumbnail generation completed successfully!', 'success');
                        
                        // Update settings
                        const settings = this.getSettings();
                        settings.thumbnailsGenerated = true;
                        localStorage.setItem('gallerySettings', JSON.stringify(settings));
                        
                        // Show notification
                        this.showNotification('Thumbnails generated successfully!', 'success');
                    }
                    
                } catch (error) {
                    console.error('Thumbnail generation failed:', error);
                    addStatusMessage(`Generation failed: ${error.message}`, 'error');
                    progressBar.classList.add('bg-pm-error');
                    progressBar.classList.remove('bg-pm-accent');
                } finally {
                    // Show close button and hide cancel
                    cancelBtn.classList.add('hidden');
                    closeBtn.classList.remove('hidden');
                    
                    // Re-enable outside click to close
                    setTimeout(() => {
                        modal.addEventListener('click', (e) => {
                            if (e.target === modal) {
                                document.body.removeChild(modal);
                                // Refresh the gallery to show new thumbnails
                                this.loadImages();
                            }
                        });
                    }, 100);
                }
            }            async generateThumbnailsWithProgressInModal(uiElements) {
                const { progressText, progressPercent, progressBar, progressDetails, progressETA, addStatusMessage, isCancelled } = uiElements;
                
                return new Promise((resolve, reject) => {
                    addStatusMessage('Starting thumbnail generation...');
                    
                    // Get quality setting
                    const quality = this.getSettings().imageQuality || 'medium';
                    
                    console.log(`Starting modal thumbnail generation with quality: ${quality}`);
                    
                    // Set up Server-Sent Events (same as the settings modal)
                    const eventSource = new EventSource(`/prompt_manager/images/generate-thumbnails/progress?quality=${quality}`);
                    
                    let resultData = null;
                    
                    // Handle scanning/status events
                    eventSource.addEventListener('status', (event) => {
                        if (isCancelled()) {
                            eventSource.close();
                            return;
                        }
                        const data = JSON.parse(event.data);
                        
                        if (data.phase === 'scanning') {
                            progressText.textContent = 'Scanning for images...';
                            progressDetails.textContent = data.message || 'Scanning output folder for images and videos...';
                            addStatusMessage(data.message || 'Scanning output folder...', 'info');
                        } else if (data.phase === 'processing' && data.message.includes('Error')) {
                            addStatusMessage(data.message, 'error');
                        } else {
                            progressDetails.textContent = data.message || 'Preparing...';
                        }
                    });
                    
                    // Handle start event
                    eventSource.addEventListener('start', (event) => {
                        if (isCancelled()) {
                            eventSource.close();
                            return;
                        }
                        const data = JSON.parse(event.data);
                        
                        // Show detailed file type breakdown
                        const fileBreakdown = (data.image_count !== undefined && data.video_count !== undefined) 
                            ? ` (${data.image_count} images, ${data.video_count} videos)`
                            : '';
                        
                        progressText.textContent = `Processing ${data.total_images} files${fileBreakdown}...`;
                        progressDetails.textContent = data.message || `Found ${data.total_images} media files to process`;
                        progressBar.style.width = '0%';
                        progressPercent.textContent = '0%';
                        progressETA.textContent = 'Calculating...';
                        
                        addStatusMessage(`Found ${data.total_images} media files to process${fileBreakdown}`, 'info');
                    });
                    
                    // Handle progress updates
                    eventSource.addEventListener('progress', (event) => {
                        if (isCancelled()) {
                            eventSource.close();
                            return;
                        }
                        const data = JSON.parse(event.data);
                        
                        // Update progress bar
                        progressBar.style.width = `${data.percentage}%`;
                        progressPercent.textContent = `${data.percentage}%`;
                        
                        // Update text with current file and action
                        const actionText = data.action === 'skipped' ? ' [Skipped]' : data.action === 'generating' ? ' [Generating]' : '';
                        // Show full path but truncate if too long
                        const fullPath = data.current_file || '';
                        const displayPath = fullPath.length > 60 ? '...' + fullPath.slice(-57) : fullPath;
                        progressText.textContent = `Processing ${data.file_type || 'file'} ${data.processed}/${data.total_images}: ${displayPath}${actionText}`;
                        
                        // Update details
                        const elapsedText = data.elapsed ? ` | Time: ${Math.floor(data.elapsed)}s` : '';
                        progressDetails.textContent = `Generated: ${data.generated}, Skipped: ${data.skipped} | Rate: ${data.rate} img/s${elapsedText}`;
                        
                        // Update ETA
                        if (data.eta > 0 && data.eta < 3600) {
                            const etaText = data.eta > 60 ? `${Math.floor(data.eta / 60)}m ${Math.floor(data.eta % 60)}s` : `${Math.floor(data.eta)}s`;
                            progressETA.textContent = `ETA: ${etaText}`;
                        } else {
                            progressETA.textContent = '';
                        }
                        
                        // Add status message periodically
                        if (data.processed % 50 === 0 || data.processed === data.total_images) {
                            addStatusMessage(`Processed ${data.processed}/${data.total_images} files (${data.generated} generated, ${data.skipped} skipped)`, 'info');
                        }
                    });
                    
                    // Handle completion
                    eventSource.addEventListener('complete', (event) => {
                        if (isCancelled()) {
                            eventSource.close();
                            return;
                        }
                        resultData = JSON.parse(event.data);
                        
                        // Update final progress
                        progressBar.style.width = '100%';
                        progressPercent.textContent = '100%';
                        progressText.textContent = 'Completed!';
                        
                        // Show detailed completion message
                        const errorCount = resultData.error_count || (resultData.errors ? resultData.errors.length : 0);
                        const errors = errorCount > 0 ? ` (${errorCount} errors)` : '';
                        const rateText = resultData.processing_rate ? ` at ${resultData.processing_rate} img/s` : '';
                        progressDetails.textContent = `Generated: ${resultData.count} new, Skipped: ${resultData.skipped} | Total: ${resultData.total_images} files | Time: ${resultData.elapsed_time}s${rateText}${errors}`;
                        progressETA.textContent = 'Done';
                        
                        // Add completion message
                        if (errorCount > 0) {
                            addStatusMessage(`Completed with ${errorCount} errors: Generated ${resultData.count} new thumbnails, skipped ${resultData.skipped} existing`, 'warning');
                            if (resultData.errors && resultData.errors.length > 0) {
                                // Show first few errors
                                resultData.errors.slice(0, 3).forEach(err => {
                                    addStatusMessage(err, 'error');
                                });
                            }
                        } else {
                            addStatusMessage(`Successfully generated ${resultData.count} new thumbnails, skipped ${resultData.skipped} existing`, 'success');
                        }
                        
                        console.log('Modal thumbnail generation completed:', resultData);
                        
                        // Clean up
                        eventSource.close();
                        resolve(resultData);
                    });
                    
                    // Handle errors
                    eventSource.addEventListener('error', (event) => {
                        if (isCancelled()) {
                            eventSource.close();
                            return;
                        }
                        
                        let errorMsg = 'Thumbnail generation failed';
                        try {
                            const data = JSON.parse(event.data);
                            errorMsg = data.error || errorMsg;
                        } catch (e) {
                            // If not JSON, it's a connection error
                            console.error('EventSource error in modal:', e);
                        }
                        
                        addStatusMessage(errorMsg, 'error');
                        progressBar.classList.add('bg-pm-error');
                        progressBar.classList.remove('bg-pm-accent');

                        // Clean up
                        eventSource.close();
                        reject(new Error(errorMsg));
                    });
                    
                    // Handle EventSource connection errors
                    eventSource.onerror = (error) => {
                        if (isCancelled()) {
                            eventSource.close();
                            return;
                        }
                        
                        console.error('EventSource connection error in modal:', error);
                        
                        // Check if connection is closing normally (after complete event)
                        if (eventSource.readyState === EventSource.CLOSED && resultData) {
                            console.log('EventSource connection closed normally after completion');
                            return;
                        }
                        
                        addStatusMessage('Connection to server lost', 'error');
                        progressBar.classList.add('bg-pm-error');
                        progressBar.classList.remove('bg-pm-accent');

                        // Clean up
                        eventSource.close();

                        if (!resultData) {
                            reject(new Error('Connection to server lost during thumbnail generation'));
                        }
                    };
                });
            }

            // ==========================================
            // Add Prompt Feature Methods
            // ==========================================

            // State for Add Prompt feature
            addPromptState = {
                allTags: [],
                allCategories: [],
                selectedTags: [],
                selectedRating: null
            };

            async showAddPromptModal() {
                // Reset form
                this.resetAddPromptForm();

                // Load tags and categories for autocomplete
                await this.loadTagsAndCategories();

                this.showModal('addPromptModal');
                document.getElementById('addPromptText').focus();
            }

            hideAddPromptModal() {
                this.hideModal('addPromptModal');
                this.hideSuggestions();
            }

            resetAddPromptForm() {
                document.getElementById('addPromptText').value = '';
                document.getElementById('addPromptCategory').value = '';
                document.getElementById('addPromptNotes').value = '';
                document.getElementById('addPromptTagInput').value = '';
                document.getElementById('addPromptProtected').checked = true;
                document.getElementById('addPromptRatingValue').value = '';
                this.addPromptState.selectedTags = [];
                this.addPromptState.selectedRating = null;
                this.renderSelectedTags();
                this.updateRatingStars(0);
            }

            async loadTagsAndCategories() {
                try {
                    // Load tags
                    const tagsResponse = await fetch('/prompt_manager/tags');
                    const tagsData = await tagsResponse.json();
                    if (tagsData.success) {
                        // Filter out the __protected__ tag from suggestions
                        this.addPromptState.allTags = tagsData.tags.filter(tag => tag !== '__protected__');
                    }

                    // Load categories
                    const categoriesResponse = await fetch('/prompt_manager/categories');
                    const categoriesData = await categoriesResponse.json();
                    if (categoriesData.success) {
                        this.addPromptState.allCategories = categoriesData.categories;
                        this.populateCategoryDatalist();
                    }
                } catch (error) {
                    console.error('Error loading tags/categories:', error);
                }
            }

            populateCategoryDatalist() {
                const datalist = document.getElementById('categoryList');
                datalist.innerHTML = this.addPromptState.allCategories
                    .map(cat => `<option value="${this.escapeHtml(cat)}">`)
                    .join('');
            }

            handleTagInput(e) {
                const query = e.target.value.toLowerCase().trim();

                if (query.length < 1) {
                    this.hideSuggestions();
                    return;
                }

                // Filter tags that match query and aren't already selected
                const suggestions = this.addPromptState.allTags
                    .filter(tag =>
                        tag.toLowerCase().includes(query) &&
                        !this.addPromptState.selectedTags.includes(tag)
                    )
                    .slice(0, 10);

                if (suggestions.length > 0) {
                    this.showSuggestions(suggestions);
                } else {
                    this.hideSuggestions();
                }
            }

            handleTagKeydown(e) {
                if (e.key === 'Enter' || e.key === ',') {
                    e.preventDefault();
                    const input = e.target;
                    const value = input.value.replace(',', '').trim();

                    if (value && !this.addPromptState.selectedTags.includes(value)) {
                        this.addTag(value);
                        input.value = '';
                        this.hideSuggestions();
                    }
                } else if (e.key === 'Backspace' && e.target.value === '') {
                    // Remove last tag on backspace with empty input
                    if (this.addPromptState.selectedTags.length > 0) {
                        this.removeTag(this.addPromptState.selectedTags.length - 1);
                    }
                } else if (e.key === 'Escape') {
                    this.hideSuggestions();
                } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                    this.navigateSuggestions(e.key === 'ArrowDown' ? 1 : -1);
                    e.preventDefault();
                }
            }

            showSuggestions(suggestions) {
                const container = document.getElementById('tagSuggestions');
                container.innerHTML = suggestions
                    .map((tag, index) => `
                        <div class="px-3 py-2 hover:bg-pm-hover cursor-pointer text-pm text-sm suggestion-item"
                             data-index="${index}"
                             onclick="window.gallery.selectSuggestion('${this.escapeHtml(tag)}')">
                            ${this.escapeHtml(tag)}
                        </div>
                    `)
                    .join('');
                container.classList.remove('hidden');
            }

            hideSuggestions() {
                document.getElementById('tagSuggestions').classList.add('hidden');
            }

            navigateSuggestions(direction) {
                const container = document.getElementById('tagSuggestions');
                const items = container.querySelectorAll('.suggestion-item');
                if (items.length === 0) return;

                const current = container.querySelector('.bg-pm-hover');
                let nextIndex = 0;

                if (current) {
                    current.classList.remove('bg-pm-hover');
                    nextIndex = parseInt(current.dataset.index) + direction;
                    if (nextIndex < 0) nextIndex = items.length - 1;
                    if (nextIndex >= items.length) nextIndex = 0;
                } else if (direction === -1) {
                    nextIndex = items.length - 1;
                }

                items[nextIndex].classList.add('bg-pm-hover');
            }

            selectSuggestion(tag) {
                this.addTag(tag);
                document.getElementById('addPromptTagInput').value = '';
                this.hideSuggestions();
                document.getElementById('addPromptTagInput').focus();
            }

            addTag(tag) {
                if (!this.addPromptState.selectedTags.includes(tag)) {
                    this.addPromptState.selectedTags.push(tag);
                    this.renderSelectedTags();
                }
            }

            removeTag(index) {
                this.addPromptState.selectedTags.splice(index, 1);
                this.renderSelectedTags();
            }

            renderSelectedTags() {
                const container = document.getElementById('addPromptTagsContainer');
                const input = document.getElementById('addPromptTagInput');

                // Remove existing tag chips (keep only the input)
                container.querySelectorAll('.tag-chip').forEach(el => el.remove());

                // Add tag chips before the input
                this.addPromptState.selectedTags.forEach((tag, index) => {
                    const chip = document.createElement('span');
                    chip.className = 'tag-chip';
                    chip.innerHTML = `
                        ${this.escapeHtml(tag)}
                        <span class="tag-remove" onclick="window.gallery.removeTag(${index})">×</span>
                    `;
                    container.insertBefore(chip, input);
                });
            }

            setRating(rating) {
                this.addPromptState.selectedRating = rating;
                document.getElementById('addPromptRatingValue').value = rating;
                this.updateRatingStars(rating);
            }

            clearRating() {
                this.addPromptState.selectedRating = null;
                document.getElementById('addPromptRatingValue').value = '';
                this.updateRatingStars(0);
            }

            updateRatingStars(rating) {
                const stars = document.querySelectorAll('#addPromptRating .rating-star');
                stars.forEach((star, index) => {
                    if (index < rating) {
                        star.textContent = '★';
                        star.classList.remove('text-pm-muted');
                        star.classList.add('text-pm-warning');
                    } else {
                        star.textContent = '☆';
                        star.classList.remove('text-pm-warning');
                        star.classList.add('text-pm-muted');
                    }
                });
            }

            async saveNewPrompt() {
                const text = document.getElementById('addPromptText').value.trim();
                const category = document.getElementById('addPromptCategory').value.trim();
                const notes = document.getElementById('addPromptNotes').value.trim();
                const isProtected = document.getElementById('addPromptProtected').checked;
                const rating = this.addPromptState.selectedRating;

                // Validation
                if (!text) {
                    this.showNotification('Prompt text is required', 'error');
                    document.getElementById('addPromptText').focus();
                    return;
                }

                // Build tags array - add __protected__ if checkbox is checked
                let tags = [...this.addPromptState.selectedTags];
                if (isProtected && !tags.includes('__protected__')) {
                    tags.push('__protected__');
                }

                // Disable button during save
                const saveBtn = document.getElementById('saveNewPromptBtn');
                saveBtn.disabled = true;
                saveBtn.textContent = 'Saving...';

                try {
                    const response = await fetch('/prompt_manager/save', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            text: text,
                            category: category || null,
                            tags: tags,
                            rating: rating,
                            notes: notes || null
                        })
                    });

                    const data = await response.json();

                    if (data.success) {
                        const message = data.is_duplicate
                            ? 'Prompt already exists (updated metadata)'
                            : 'Prompt added successfully!';
                        this.showNotification(message, 'success');
                        this.hideAddPromptModal();
                    } else {
                        this.showNotification(`Failed to save prompt: ${data.error}`, 'error');
                    }
                } catch (error) {
                    console.error('Error saving prompt:', error);
                    this.showNotification('Failed to save prompt', 'error');
                } finally {
                    saveBtn.disabled = false;
                    saveBtn.textContent = '➕ Add Prompt';
                }
            }

            // ==========================================
            // Auto Tag Feature Methods
            // ==========================================

            // State for AutoTag feature
            autoTagState = {
                eventSource: null,
                downloadEventSource: null,
                reviewImages: [],
                reviewIndex: 0,
                currentTags: [],
                cancelled: false
            };

            showModal(modalId) {
                const modal = document.getElementById(modalId);
                modal.classList.remove('hidden');
                modal.classList.add('flex');
                document.body.style.overflow = 'hidden';
            }

            hideModal(modalId) {
                const modal = document.getElementById(modalId);
                modal.classList.add('hidden');
                modal.classList.remove('flex');
                document.body.style.overflow = '';
            }

            async showAutoTagModal() {
                this.showModal("autoTagModal");
                await this.checkAutoTagModels();
            }

            async checkAutoTagModels() {
                const ggufStatus = document.getElementById("ggufModelStatus");
                const hfStatus = document.getElementById("hfModelStatus");
                const modelLoadedStatus = document.getElementById("modelLoadedStatus");
                const loadedModelType = document.getElementById("loadedModelType");
                const unloadModelBtn = document.getElementById("unloadModelBtn");

                ggufStatus.innerHTML = '<span class="text-pm-secondary text-sm">Checking...</span>';
                hfStatus.innerHTML = '<span class="text-pm-secondary text-sm">Checking...</span>';

                try {
                    const response = await fetch('/prompt_manager/autotag/models');
                    const data = await response.json();

                    if (data.success) {
                        // Update GGUF status
                        if (data.models.gguf.downloaded) {
                            ggufStatus.innerHTML = '<span class="text-pm-success text-sm">✓ Downloaded</span>';
                        } else {
                            ggufStatus.innerHTML = `<button onclick="window.gallery.downloadModel('gguf')" class="px-3 py-1 bg-pm-accent hover:bg-pm-accent-hover text-pm text-xs rounded transition-colors">Download</button>`;
                        }

                        // Update HF status
                        if (data.models.hf.downloaded) {
                            hfStatus.innerHTML = '<span class="text-pm-success text-sm">✓ Downloaded</span>';
                        } else {
                            hfStatus.innerHTML = `<button onclick="window.gallery.downloadModel('hf')" class="px-3 py-1 bg-pm-accent hover:bg-pm-accent-hover text-pm text-xs rounded transition-colors">Download</button>`;
                        }

                        // Update model loaded status
                        if (data.model_loaded && data.loaded_model_type) {
                            modelLoadedStatus.classList.remove('hidden');
                            loadedModelType.textContent = data.loaded_model_type.toUpperCase();
                            unloadModelBtn.classList.remove('hidden');
                        } else {
                            modelLoadedStatus.classList.add('hidden');
                            unloadModelBtn.classList.add('hidden');
                        }
                    } else {
                        ggufStatus.innerHTML = '<span class="text-pm-error text-sm">Error</span>';
                        hfStatus.innerHTML = '<span class="text-pm-error text-sm">Error</span>';
                    }
                } catch (error) {
                    console.error('Error checking models:', error);
                    ggufStatus.innerHTML = '<span class="text-pm-error text-sm">Error</span>';
                    hfStatus.innerHTML = '<span class="text-pm-error text-sm">Error</span>';
                }
            }

            async downloadModel(modelType) {
                const modelName = modelType === 'gguf' ? 'GGUF Model' : 'HuggingFace Model';
                document.getElementById('downloadModelName').textContent = modelName;
                document.getElementById('downloadStatus').textContent = 'Preparing...';
                document.getElementById('downloadProgressPercent').textContent = '0%';
                document.getElementById('downloadProgressBar').style.width = '0%';

                this.hideModal("autoTagModal");
                this.showModal("autoTagDownloadModal");

                try {
                    this.autoTagState.downloadEventSource = new EventSource(`/prompt_manager/autotag/download/${modelType}`);

                    this.autoTagState.downloadEventSource.onmessage = (event) => {
                        const data = JSON.parse(event.data);

                        if (data.status === 'downloading') {
                            const percent = Math.round(data.progress * 100);
                            document.getElementById('downloadStatus').textContent = data.message || 'Downloading...';
                            document.getElementById('downloadProgressPercent').textContent = `${percent}%`;
                            document.getElementById('downloadProgressBar').style.width = `${percent}%`;
                        } else if (data.status === 'complete') {
                            this.autoTagState.downloadEventSource.close();
                            this.hideModal("autoTagDownloadModal");
                            this.showModal("autoTagModal");
                            this.checkAutoTagModels();
                            this.showNotification('Model downloaded successfully!', 'success');
                        } else if (data.status === 'error') {
                            this.autoTagState.downloadEventSource.close();
                            this.hideModal("autoTagDownloadModal");
                            this.showModal("autoTagModal");
                            this.showNotification(`Download failed: ${data.message}`, 'error');
                        }
                    };

                    this.autoTagState.downloadEventSource.onerror = () => {
                        this.autoTagState.downloadEventSource.close();
                        this.hideModal("autoTagDownloadModal");
                        this.showModal("autoTagModal");
                        this.showNotification('Download connection lost', 'error');
                    };
                } catch (error) {
                    console.error('Download error:', error);
                    this.hideModal("autoTagDownloadModal");
                    this.showModal("autoTagModal");
                    this.showNotification('Failed to start download', 'error');
                }
            }

            cancelDownload() {
                if (this.autoTagState.downloadEventSource) {
                    this.autoTagState.downloadEventSource.close();
                    this.autoTagState.downloadEventSource = null;
                }
                this.hideModal("autoTagDownloadModal");
                this.showModal("autoTagModal");
            }

            async unloadModel() {
                const unloadBtn = document.getElementById("unloadModelBtn");
                unloadBtn.disabled = true;
                unloadBtn.textContent = 'Unloading...';

                try {
                    const response = await fetch('/prompt_manager/autotag/unload', {
                        method: 'POST'
                    });
                    const data = await response.json();

                    if (data.success) {
                        this.showNotification(data.message, 'success');
                        await this.checkAutoTagModels(); // Refresh status
                    } else {
                        this.showNotification(`Failed to unload model: ${data.error}`, 'error');
                    }
                } catch (error) {
                    console.error('Error unloading model:', error);
                    this.showNotification('Failed to unload model', 'error');
                } finally {
                    unloadBtn.disabled = false;
                    unloadBtn.textContent = 'Unload Model';
                }
            }

            async startAutoTag() {
                const modelType = document.querySelector('input[name="autoTagModel"]:checked').value;
                const prompt = document.getElementById('autoTagPrompt').value;
                const keepInMemory = document.getElementById('keepModelInMemory').checked;

                // Check if model is downloaded
                const response = await fetch('/prompt_manager/autotag/models');
                const data = await response.json();

                if (data.success && !data.models[modelType].downloaded) {
                    this.showNotification('Please download the selected model first', 'warning');
                    return;
                }

                this.hideModal("autoTagModal");
                this.showModal("autoTagProgressModal");

                // Reset progress display
                document.getElementById('autoTagCurrentFile').textContent = 'Loading model...';
                document.getElementById('autoTagProgressPercent').textContent = '0%';
                document.getElementById('autoTagProgressBar').style.width = '0%';
                document.getElementById('autoTagProcessed').textContent = '0';
                document.getElementById('autoTagApplied').textContent = '0';
                document.getElementById('autoTagSkipped').textContent = '0';

                this.autoTagState.cancelled = false;

                try {
                    const formData = new URLSearchParams();
                    formData.append('model_type', modelType);
                    formData.append('prompt', prompt);
                    formData.append('keep_in_memory', keepInMemory);

                    this.autoTagState.eventSource = new EventSource(`/prompt_manager/autotag/start?${formData.toString()}`);

                    this.autoTagState.eventSource.onmessage = (event) => {
                        const data = JSON.parse(event.data);

                        if (data.status === 'loading_model') {
                            document.getElementById('autoTagCurrentFile').textContent = 'Loading model...';
                        } else if (data.status === 'processing') {
                            const percent = Math.round((data.processed / data.total) * 100);
                            document.getElementById('autoTagCurrentFile').textContent = data.current_file || 'Processing...';
                            document.getElementById('autoTagProgressPercent').textContent = `${percent}%`;
                            document.getElementById('autoTagProgressBar').style.width = `${percent}%`;
                            document.getElementById('autoTagProcessed').textContent = data.processed;
                            document.getElementById('autoTagApplied').textContent = data.tags_applied;
                            document.getElementById('autoTagSkipped').textContent = data.skipped;
                        } else if (data.status === 'complete') {
                            this.autoTagState.eventSource.close();
                            this.hideModal("autoTagProgressModal");
                            this.showNotification(`Auto tagging complete! Applied tags to ${data.tags_applied} prompts.`, 'success');
                            this.loadImages(); // Refresh the gallery
                        } else if (data.status === 'error') {
                            this.autoTagState.eventSource.close();
                            this.hideModal("autoTagProgressModal");
                            this.showNotification(`Auto tag error: ${data.message}`, 'error');
                        } else if (data.status === 'cancelled') {
                            this.autoTagState.eventSource.close();
                            this.hideModal("autoTagProgressModal");
                            this.showNotification('Auto tagging cancelled', 'info');
                        }
                    };

                    this.autoTagState.eventSource.onerror = () => {
                        this.autoTagState.eventSource.close();
                        this.hideModal("autoTagProgressModal");
                        this.showNotification('Connection lost during auto tagging', 'error');
                    };
                } catch (error) {
                    console.error('Auto tag error:', error);
                    this.hideModal("autoTagProgressModal");
                    this.showNotification('Failed to start auto tagging', 'error');
                }
            }

            cancelAutoTag() {
                this.autoTagState.cancelled = true;
                if (this.autoTagState.eventSource) {
                    this.autoTagState.eventSource.close();
                    this.autoTagState.eventSource = null;
                }
                this.hideModal("autoTagProgressModal");
                this.showNotification('Auto tagging cancelled', 'info');
            }

            async startReview() {
                const modelType = document.querySelector('input[name="autoTagModel"]:checked').value;

                // Check if model is downloaded
                const response = await fetch('/prompt_manager/autotag/models');
                const data = await response.json();

                if (data.success && !data.models[modelType].downloaded) {
                    this.showNotification('Please download the selected model first', 'warning');
                    return;
                }

                this.hideModal("autoTagModal");

                // Use current gallery images for review
                if (!this.images || this.images.length === 0) {
                    this.showNotification('No images found in gallery', 'warning');
                    return;
                }

                this.autoTagState.reviewImages = this.images;
                this.autoTagState.reviewIndex = 0;
                this.autoTagState.modelType = modelType;
                this.autoTagState.prompt = document.getElementById('autoTagPrompt').value;

                document.getElementById('reviewTotalCount').textContent = this.images.length;

                this.showModal("autoTagReviewModal");
                await this.loadNextReviewImage();
            }

            async loadNextReviewImage() {
                if (this.autoTagState.reviewIndex >= this.autoTagState.reviewImages.length) {
                    this.hideModal("autoTagReviewModal");
                    this.showNotification('Review complete!', 'success');
                    this.loadImages();
                    return;
                }

                const image = this.autoTagState.reviewImages[this.autoTagState.reviewIndex];
                document.getElementById('reviewCurrentIndex').textContent = this.autoTagState.reviewIndex + 1;
                document.getElementById('reviewImage').src = image.thumbnail_url || image.url;
                document.getElementById('reviewImageName').textContent = image.filename || 'Unknown';
                document.getElementById('reviewTagsContainer').innerHTML = '<div class="text-pm-secondary">Generating tags...</div>';

                try {
                    const response = await fetch('/prompt_manager/autotag/single', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            image_path: image.path,
                            model_type: this.autoTagState.modelType,
                            prompt: this.autoTagState.prompt
                        })
                    });

                    const data = await response.json();

                    if (data.success) {
                        this.autoTagState.currentTags = data.tags;
                        this.renderReviewTags();
                    } else {
                        document.getElementById('reviewTagsContainer').innerHTML =
                            `<div class="text-pm-error">Error: ${data.error}</div>`;
                    }
                } catch (error) {
                    console.error('Error generating tags:', error);
                    document.getElementById('reviewTagsContainer').innerHTML =
                        '<div class="text-pm-error">Failed to generate tags</div>';
                }
            }

            renderReviewTags() {
                const container = document.getElementById('reviewTagsContainer');
                if (this.autoTagState.currentTags.length === 0) {
                    container.innerHTML = '<div class="text-pm-secondary">No tags generated</div>';
                    return;
                }

                container.innerHTML = this.autoTagState.currentTags.map((tag, index) => `
                    <span class="tag-chip">
                        ${this.escapeHtml(tag)}
                        <span class="tag-remove" onclick="window.gallery.removeReviewTag(${index})">×</span>
                    </span>
                `).join('');
            }

            escapeHtml(text) {
                const div = document.createElement('div');
                div.textContent = text;
                return div.innerHTML;
            }

            removeReviewTag(index) {
                this.autoTagState.currentTags.splice(index, 1);
                this.renderReviewTags();
            }

            skipReviewImage() {
                this.autoTagState.reviewIndex++;
                this.loadNextReviewImage();
            }

            async applyReviewTags() {
                if (this.autoTagState.currentTags.length === 0) {
                    this.skipReviewImage();
                    return;
                }

                const image = this.autoTagState.reviewImages[this.autoTagState.reviewIndex];

                try {
                    const response = await fetch('/prompt_manager/autotag/apply', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            image_path: image.path,
                            tags: this.autoTagState.currentTags
                        })
                    });

                    const data = await response.json();

                    if (data.success) {
                        this.showNotification(`Applied ${this.autoTagState.currentTags.length} tags`, 'success');
                    } else {
                        this.showNotification(data.error || 'Failed to apply tags', 'warning');
                    }
                } catch (error) {
                    console.error('Error applying tags:', error);
                    this.showNotification('Failed to apply tags', 'error');
                }

                this.autoTagState.reviewIndex++;
                this.loadNextReviewImage();
            }

            cancelReview() {
                this.autoTagState.reviewImages = [];
                this.autoTagState.reviewIndex = 0;
                this.autoTagState.currentTags = [];
                this.hideModal("autoTagReviewModal");
            }

            showNotification(message, type = 'info') {
                // Simple notification - could be enhanced with a toast library
                const colors = {
                    success: 'bg-pm-success',
                    error: 'bg-pm-error',
                    warning: 'bg-pm-warning',
                    info: 'bg-pm-accent'
                };

                const notification = document.createElement('div');
                notification.className = `fixed bottom-4 right-4 ${colors[type]} text-pm px-4 py-2 rounded-pm-md shadow-pm text-sm z-50 animate-fade-in`;
                notification.textContent = message;
                document.body.appendChild(notification);

                setTimeout(() => {
                    notification.remove();
                }, 3000);
            }
        }

        // Initialize gallery when page loads
        document.addEventListener('DOMContentLoaded', () => {
            window.gallery = new GalleryManager();
        });

        // Global helper for modal close buttons
        function hideModal(modalId) {
            const modal = document.getElementById(modalId);
            modal.classList.add('hidden');
            modal.classList.remove('flex');
            document.body.style.overflow = '';
        }
