        class PromptAdmin {
            constructor() {
                this.prompts = [];
                this.selectedPrompts = new Set();
                this.settings = {
                    resultTimeout: 5,
                    webuiDisplayMode: 'popup',
                };
                this.categories = [];
                this.tags = [];
                this.imageViewMode = 'fit'; // 'fit' or 'full'
                this.naturalImageSize = { width: 0, height: 0 };
                
                // Pagination state
                this.pagination = {
                    currentPage: 1,
                    limit: 50,
                    total: 0,
                    totalPages: 1
                };

                this.tagsPage = null;

                this.init();
            }

            init() {
                this.bindEvents();
                this.initRouter();
                this.loadInitialData();
            }

            initRouter() {
                window.addEventListener('hashchange', () => this.handleRoute());
                this.handleRoute();
            }

            handleRoute() {
                const hash = window.location.hash;
                if (hash === '#/tags' || hash.startsWith('#/tags/')) {
                    this.showTagsPage();
                } else {
                    this.showDashboard();
                }
            }

            showDashboard() {
                const dashboard = document.getElementById('dashboardView');
                const tagsPage = document.getElementById('tagsPageView');
                if (dashboard) dashboard.classList.remove('hidden');
                if (tagsPage) tagsPage.classList.add('hidden');
                if (this.tagsPage) {
                    this.tagsPage.destroy();
                    this.tagsPage = null;
                }
            }

            showTagsPage() {
                const dashboard = document.getElementById('dashboardView');
                const tagsPage = document.getElementById('tagsPageView');
                if (dashboard) dashboard.classList.add('hidden');
                if (tagsPage) tagsPage.classList.remove('hidden');

                // Lazy-initialize TagsPageManager
                if (!this.tagsPage && typeof TagsPageManager !== 'undefined') {
                    this.tagsPage = new TagsPageManager(this);
                } else if (this.tagsPage) {
                    this.tagsPage.loadTagsList();
                }
            }

            bindEvents() {
                // Search
                document.getElementById("searchBtn").addEventListener("click", () => this.search());
                document.getElementById("searchText").addEventListener("keyup", (e) => {
                    if (e.key === "Enter") this.search();
                });

                // Bulk actions
                document.getElementById("selectAll").addEventListener("change", (e) =>
                    this.toggleSelectAll(e.target.checked)
                );
                document.getElementById("bulkDeleteBtn").addEventListener("click", () => this.bulkDelete());
                document.getElementById("bulkTagBtn").addEventListener("click", () => this.showBulkTagModal());
                document.getElementById("bulkCategoryBtn").addEventListener("click", () => this.showBulkCategoryModal());
                document.getElementById("exportBtn").addEventListener("click", () => this.exportPrompts());
                document.getElementById("settingsBtn").addEventListener("click", () => this.showSettingsModal());
                document.getElementById("statsBtn").addEventListener("click", () => this.showStats());
                document.getElementById("diagnosticsBtn").addEventListener("click", () => this.showDiagnosticsModal());
                document.getElementById("maintenanceBtn").addEventListener("click", () => this.showMaintenanceModal());
                document.getElementById("backupBtn").addEventListener("click", () => this.backupDatabase());
                document.getElementById("restoreBtn").addEventListener("click", () => this.showRestoreModal());
                document.getElementById("scanBtn").addEventListener("click", () => this.showScanModal());
                document.getElementById("autoTagBtn").addEventListener("click", () => this.showAutoTagModal());
                document.getElementById("addPromptBtn").addEventListener("click", () => this.showAddPromptModal());
                document.getElementById("logsBtn").addEventListener("click", () => this.showLogsModal());
                document.getElementById("metadataBtn").addEventListener("click", () => this.openMetadataViewer());
                document.getElementById("galleryBtn").addEventListener("click", () => this.openGallery());

                // Pagination controls
                document.getElementById("limitSelector").addEventListener("change", (e) => this.changeLimit(parseInt(e.target.value)));
                document.getElementById("firstPageBtn").addEventListener("click", () => this.goToPage(1));
                document.getElementById("prevPageBtn").addEventListener("click", () => this.goToPage(this.pagination.currentPage - 1));
                document.getElementById("nextPageBtn").addEventListener("click", () => this.goToPage(this.pagination.currentPage + 1));
                document.getElementById("lastPageBtn").addEventListener("click", () => this.goToPage(this.pagination.totalPages));

                // Modals
                this.bindModalEvents();

                // Auto-search on filter changes
                ["searchCategory"].forEach((id) => {
                    document.getElementById(id).addEventListener("change", () => this.search());
                });
                
                // Sort dropdown change
                document.getElementById("sortBy").addEventListener("change", () => this.renderPrompts());
            }

            bindModalEvents() {
                // Settings modal
                document.getElementById("saveSettings").addEventListener("click", () => this.saveSettings());
                document.getElementById("cancelSettings").addEventListener("click", () => this.hideModal("settingsModal"));
                document.getElementById("refreshMonitoringStatus").addEventListener("click", () => this.updateMonitoringStatus());

                // Bulk tag modal
                document.getElementById("confirmBulkTag").addEventListener("click", () => this.confirmBulkTag());
                document.getElementById("cancelBulkTag").addEventListener("click", () => this.hideModal("bulkTagModal"));

                // Individual tag modal
                document.getElementById("confirmIndividualTag").addEventListener("click", () => this.confirmIndividualTag());
                document.getElementById("cancelIndividualTag").addEventListener("click", () => this.hideModal("individualTagModal"));

                // Bulk category modal
                document.getElementById("confirmBulkCategory").addEventListener("click", () => this.confirmBulkCategory());
                document.getElementById("cancelBulkCategory").addEventListener("click", () => this.hideModal("bulkCategoryModal"));

                // Restore modal
                document.getElementById("confirmRestore").addEventListener("click", () => this.confirmRestore());
                document.getElementById("cancelRestore").addEventListener("click", () => this.hideModal("restoreModal"));

                // Diagnostics modal
                document.getElementById("runDiagnosticsBtn").addEventListener("click", () => this.runDiagnostics());
                document.getElementById("testImageLinkBtn").addEventListener("click", () => this.testImageLink());

                // Maintenance modal
                document.getElementById("runMaintenanceBtn").addEventListener("click", () => this.runMaintenance());
                document.getElementById("selectAllMaintenanceBtn").addEventListener("click", () => this.selectAllMaintenance());
                document.getElementById("clearAllMaintenanceBtn").addEventListener("click", () => this.clearAllMaintenance());

                // Scan modal
                document.getElementById("startScan").addEventListener("click", () => this.startScan());
                document.getElementById("cancelScan").addEventListener("click", () => this.hideModal("scanModal"));
                document.getElementById("quickBackupBtn").addEventListener("click", () => this.quickBackup());

                // Logs modal
                document.getElementById("refreshLogsBtn").addEventListener("click", () => this.refreshLogs());
                document.getElementById("downloadLogsBtn").addEventListener("click", () => this.downloadLogs());
                document.getElementById("clearLogsBtn").addEventListener("click", () => this.clearLogs());
                document.getElementById("updateLogConfigBtn").addEventListener("click", () => this.updateLogConfig());

                // Auto Tag modals
                document.getElementById("startAutoTagBtn").addEventListener("click", () => this.startAutoTag());
                document.getElementById("startReviewBtn").addEventListener("click", () => this.startReview());
                document.getElementById("skipReviewBtn").addEventListener("click", () => this.skipReviewImage());
                document.getElementById("applyReviewBtn").addEventListener("click", () => this.applyReviewTags());
                document.getElementById("cancelAutoTagBtn").addEventListener("click", () => this.cancelAutoTag());
                document.getElementById("cancelDownloadBtn").addEventListener("click", () => this.cancelDownload());

                // Re-tag confirmation modal buttons
                document.getElementById("retagSkipBtn").addEventListener("click", () => this.handleRetagChoice('skip'));
                document.getElementById("retagSkipAllBtn").addEventListener("click", () => this.handleRetagChoice('skipAll'));
                document.getElementById("retagConfirmBtn").addEventListener("click", () => this.handleRetagChoice('retag'));

                // WD14 model selection toggle (prompt vs thresholds)
                document.querySelectorAll('input[name="autoTagModel"]').forEach(radio => {
                    radio.addEventListener('change', (e) => {
                        const isWd14 = e.target.value.startsWith('wd14');
                        document.getElementById('promptSection').classList.toggle('hidden', isWd14);
                        document.getElementById('wd14ThresholdSection').classList.toggle('hidden', !isWd14);
                    });
                });

                // WD14 threshold slider value display
                document.getElementById('wd14GeneralThreshold').addEventListener('input', (e) => {
                    document.getElementById('wd14GeneralThresholdValue').textContent = parseFloat(e.target.value).toFixed(2);
                });
                document.getElementById('wd14CharacterThreshold').addEventListener('input', (e) => {
                    document.getElementById('wd14CharacterThresholdValue').textContent = parseFloat(e.target.value).toFixed(2);
                });

                // Tags accordion toggle
                document.getElementById("reviewTagsToggle").addEventListener("click", () => this.toggleReviewTags());

                // Add Prompt modal
                document.getElementById("saveNewPromptBtn").addEventListener("click", () => this.saveNewPrompt());
                this.setupAddPromptModal();

                // Close modals on backdrop click
                document.querySelectorAll("[id$='Modal']").forEach((modal) => {
                    modal.addEventListener("click", (e) => {
                        if (e.target === modal) {
                            modal.classList.add("hidden");
                            modal.classList.remove("flex");
                            document.body.style.overflow = "";
                        }
                    });
                });
            }

            async loadInitialData() {
                try {
                    await Promise.all([
                        this.loadStatistics(),
                        this.loadCategories(),
                        this.loadTags(),
                        this.loadRecentPrompts(),
                        this.loadSettings(),
                    ]);
                } catch (error) {
                    this.showNotification("Failed to load initial data", "error");
                    console.error("Load error:", error);
                }
            }

            async loadSettings() {
                try {
                    const response = await fetch("/prompt_manager/settings");
                    if (response.ok) {
                        const data = await response.json();
                        if (data.success && data.settings) {
                            this.settings.resultTimeout = data.settings.result_timeout || 5;
                            this.settings.webuiDisplayMode = data.settings.webui_display_mode || 'popup';
                            this.settings.galleryRootPath = data.settings.gallery_root_path || '';
                            this.settings.monitoredDirectories = data.settings.monitored_directories || [];
                        }
                    }
                } catch (error) {
                    console.error("Settings load error:", error);
                }
            }

            async loadStatistics() {
                try {
                    const response = await fetch("/prompt_manager/stats");
                    if (response.ok) {
                        const data = await response.json();
                        console.log("Stats response:", data); // Debug log
                        
                        if (data.success) {
                            // Try different possible response structures
                            const stats = data.stats || data;
                            document.getElementById("totalPrompts").textContent = stats.total_prompts || data.total_prompts || 0;
                            document.getElementById("totalCategories").textContent = stats.total_categories || data.total_categories || 0;
                            document.getElementById("totalTags").textContent = stats.total_tags || data.total_tags || 0;
                            document.getElementById("avgRating").textContent = 
                                (stats.avg_rating || data.avg_rating) ? (stats.avg_rating || data.avg_rating).toFixed(1) : "N/A";
                        } else {
                            console.error("Stats API returned success=false:", data);
                        }
                    } else {
                        console.error("Stats API HTTP error:", response.status, response.statusText);
                    }
                } catch (error) {
                    console.error("Stats error:", error);
                    // Set some default values if API fails
                    document.getElementById("totalPrompts").textContent = this.prompts.length || 0;
                    document.getElementById("totalCategories").textContent = this.categories.length || 0;
                    document.getElementById("totalTags").textContent = this.tags.length || 0;
                    document.getElementById("avgRating").textContent = "N/A";
                }
            }

            async loadCategories() {
                try {
                    const response = await fetch("/prompt_manager/categories");
                    if (response.ok) {
                        const data = await response.json();
                        if (data.success) {
                            this.categories = data.categories;
                            this.populateCategoryDropdown();
                        }
                    }
                } catch (error) {
                    console.error("Categories error:", error);
                }
            }

            async loadTags() {
                try {
                    const response = await fetch("/prompt_manager/tags");
                    if (response.ok) {
                        const data = await response.json();
                        if (data.success) {
                            this.tags = data.tags;
                        }
                    }
                } catch (error) {
                    console.error("Tags error:", error);
                }
            }

            populateCategoryDropdown() {
                const select = document.getElementById("searchCategory");
                select.innerHTML = '<option value="">All Categories</option>';
                this.categories.forEach((category) => {
                    const option = document.createElement("option");
                    option.value = category;
                    option.textContent = category;
                    select.appendChild(option);
                });
            }

            async loadRecentPrompts(page = 1) {
                try {
                    this.pagination.currentPage = page;
                    const offset = (page - 1) * this.pagination.limit;
                    const response = await fetch(`/prompt_manager/recent?limit=${this.pagination.limit}&offset=${offset}&page=${page}`);
                    if (response.ok) {
                        const data = await response.json();
                        if (data.success) {
                            this.prompts = data.results;
                            this.pagination = {
                                ...this.pagination,
                                total: data.pagination.total,
                                totalPages: data.pagination.total_pages,
                                currentPage: data.pagination.page
                            };
                            this.renderPrompts();
                            this.updatePaginationControls();
                            // Don't update stats from local data as it's only a subset of prompts
                        }
                    }
                } catch (error) {
                    console.error("Recent prompts error:", error);
                } finally {
                    document.getElementById("loadingState").classList.add("hidden");
                    document.getElementById("resultsList").classList.remove("hidden");
                    document.getElementById("paginationControls").classList.remove("hidden");
                }
            }

            updateLocalStats() {
                // Only update local stats if we haven't loaded proper statistics yet
                // This prevents overwriting the correct total counts from the API
                const currentTotal = document.getElementById("totalPrompts").textContent;
                
                // Only update if the current total is still the default "-"
                if (currentTotal === "-") {
                    // Show local counts as fallback only when API hasn't loaded yet
                    document.getElementById("totalPrompts").textContent = this.prompts.length;
                    
                    const uniqueCategories = new Set(this.prompts.map(p => p.category).filter(Boolean));
                    document.getElementById("totalCategories").textContent = uniqueCategories.size;
                    
                    const allTags = this.prompts.flatMap(p => Array.isArray(p.tags) ? p.tags : []);
                    const uniqueTags = new Set(allTags.filter(Boolean));
                    document.getElementById("totalTags").textContent = uniqueTags.size;
                    
                    const ratings = this.prompts.map(p => p.rating).filter(r => r && r > 0);
                    if (ratings.length > 0) {
                        const avgRating = ratings.reduce((a, b) => a + b, 0) / ratings.length;
                        document.getElementById("avgRating").textContent = avgRating.toFixed(1);
                    }
                }
            }

            async search() {
                const searchText = document.getElementById("searchText").value;
                const category = document.getElementById("searchCategory").value;
                const tags = document.getElementById("searchTags").value;

                document.getElementById("loadingState").classList.remove("hidden");
                document.getElementById("resultsList").classList.add("hidden");

                try {
                    const params = new URLSearchParams();
                    if (searchText) params.append("text", searchText);
                    if (category) params.append("category", category);
                    if (tags) params.append("tags", tags);
                    params.append("limit", "100");

                    const response = await fetch(`/prompt_manager/search?${params}`);
                    if (response.ok) {
                        const data = await response.json();
                        if (data.success) {
                            this.prompts = data.results;
                            this.renderPrompts();
                            document.getElementById("resultsTitle").textContent = "Search Results";
                        }
                    }
                } catch (error) {
                    this.showNotification("Search failed", "error");
                    console.error("Search error:", error);
                } finally {
                    document.getElementById("loadingState").classList.add("hidden");
                    document.getElementById("resultsList").classList.remove("hidden");
                }
            }

            sortPrompts(prompts) {
                const sortBy = document.getElementById("sortBy")?.value || "created_desc";
                const [field, direction] = sortBy.split("_");
                
                return [...prompts].sort((a, b) => {
                    let aVal, bVal;
                    
                    switch (field) {
                        case "rating":
                            aVal = a.rating || 0;
                            bVal = b.rating || 0;
                            break;
                        case "created":
                            aVal = new Date(a.created_at);
                            bVal = new Date(b.created_at);
                            break;
                        case "text":
                            aVal = a.text.toLowerCase();
                            bVal = b.text.toLowerCase();
                            break;
                        default:
                            return 0;
                    }
                    
                    if (direction === "asc") {
                        return aVal > bVal ? 1 : aVal < bVal ? -1 : 0;
                    } else {
                        return aVal < bVal ? 1 : aVal > bVal ? -1 : 0;
                    }
                });
            }

            renderPrompts() {
                const container = document.getElementById("resultsList");
                const count = document.getElementById("resultsCount");

                count.textContent = `${this.prompts.length} prompts`;

                if (this.prompts.length === 0) {
                    container.innerHTML = `
                        <div class="text-center py-6">
                            <div class="w-16 h-16 bg-pm-surface rounded-full flex items-center justify-center mx-auto mb-4">
                                <span class="text-lg">📭</span>
                            </div>
                            <h3 class="text-sm font-semibold text-pm-secondary mb-2">No prompts found</h3>
                            <p class="text-pm-muted">Try adjusting your search criteria or create some prompts in ComfyUI</p>
                        </div>
                    `;
                    return;
                }

                // Sort prompts before rendering
                const sortedPrompts = this.sortPrompts(this.prompts);
                container.innerHTML = sortedPrompts.map((prompt) => this.renderPromptItem(prompt)).join("");
                this.selectedPrompts.clear();
                this.updateBulkActionButtons();

                // Add hover behavior to all star ratings
                sortedPrompts.forEach(prompt => {
                    this.addStarHoverBehavior(prompt.id);
                });

                // Delegated click handler for remove-tag buttons (avoids inline onclick XSS)
                container.querySelectorAll('.remove-tag-btn').forEach(btn => {
                    btn.addEventListener('click', (e) => {
                        const promptId = parseInt(e.target.dataset.promptId);
                        const tag = e.target.dataset.tag;
                        window.admin.removeTag(promptId, tag);
                    });
                });

                // Load film strips for each prompt (async, non-blocking)
                this.loadAllFilmStrips(sortedPrompts);
            }

            async loadAllFilmStrips(prompts) {
                for (const prompt of prompts) {
                    const container = document.getElementById(`filmStrip-${prompt.id}`);
                    if (!container) continue;

                    // Use pre-loaded images from API response if available
                    if (prompt.images && prompt.images.length > 0) {
                        container.innerHTML = this.createFilmStrip(prompt.images, prompt.id);  // trusted internal HTML
                    } else if (prompt.image_count > 0) {
                        // Images exist but weren't pre-loaded — fetch individually
                        this.loadFilmStripForPrompt(prompt.id);
                    }
                }
            }

            async loadFilmStripForPrompt(promptId) {
                const container = document.getElementById(`filmStrip-${promptId}`);
                if (!container) return;

                try {
                    const images = await this.loadFilmStripImages(promptId);
                    container.innerHTML = this.createFilmStrip(images, promptId);  // trusted internal HTML
                } catch (error) {
                    console.error(`Error loading filmstrip for prompt ${promptId}:`, error);
                    container.innerHTML = `<div class="film-strip-empty">Failed to load images</div>`;  // static HTML
                }
            }

            renderPromptItem(prompt) {
                const tags = Array.isArray(prompt.tags) ? prompt.tags : [];
                const category = prompt.category || "No category";
                const rating = prompt.rating || 0;
                const created = new Date(prompt.created_at).toLocaleDateString();

                return `
                    <div class="bg-pm-surface rounded-pm-md border border-pm hover:border-pm transition-all duration-200" data-id="${prompt.id}">
                        <div class="p-4">
                            <div class="flex items-start space-x-4">
                                <input type="checkbox" class="prompt-checkbox w-5 h-5 text-pm-accent bg-pm-input border-pm rounded focus:ring-pm-accent mt-1" data-id="${prompt.id}">

                                <div class="flex-1 min-w-0">
                                    <div class="bg-pm-surface rounded-pm-sm p-4 mb-4 relative group">
                                        <div class="prompt-text text-pm leading-relaxed whitespace-pre-wrap" data-id="${prompt.id}">${this.escapeHtml(prompt.text)}</div>
                                        <button class="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity duration-200 bg-pm-surface hover:bg-pm-hover text-pm-secondary hover:text-pm p-2 rounded-pm-sm text-sm" onclick="window.admin.copyPromptToClipboard(${prompt.id})">
                                            📋 Copy
                                        </button>
                                    </div>

                                    <div class="flex flex-wrap items-center gap-4 text-sm text-pm-secondary mb-3">
                                        <div class="flex items-center space-x-1">
                                            <span>📁</span>
                                            <span class="category-text" data-id="${prompt.id}">${this.escapeHtml(category)}</span>
                                        </div>
                                        <div class="flex items-center space-x-1">
                                            <span>📅</span>
                                            <span>${created}</span>
                                        </div>
                                        <div class="flex items-center space-x-1">
                                            <div class="rating flex space-x-1" data-id="${prompt.id}" data-rating="${rating}">
                                                ${this.renderStars(rating, prompt.id)}
                                            </div>
                                        </div>
                                    </div>
                                    
                                    <div class="tags-accordion" data-prompt-id="${prompt.id}">
                                        <div class="flex flex-wrap items-center gap-2">
                                            ${tags.slice(0, 10).map(tag => `
                                                <span class="inline-flex items-center space-x-1 bg-pm-accent-tint text-pm-accent px-3 py-1 rounded-full text-sm border border-pm-accent">
                                                    <span>${this.escapeHtml(tag)}</span>
                                                    <button class="remove-tag-btn text-pm-accent hover:text-pm-accent ml-1" data-prompt-id="${prompt.id}" data-tag="${this.escapeHtml(tag)}">&times;</button>
                                                </span>
                                            `).join("")}
                                            <button class="inline-flex items-center space-x-1 bg-pm-input hover:bg-pm-hover text-pm-secondary px-3 py-1 rounded-full text-sm transition-colors" onclick="window.admin.addTag(${prompt.id})">
                                                <span>+</span>
                                                <span>Add Tags</span>
                                            </button>
                                        </div>
                                        ${tags.length > 10 ? `
                                            <div class="tags-hidden hidden mt-2">
                                                <div class="flex flex-wrap items-center gap-2 max-h-[180px] overflow-y-auto p-2 bg-pm-surface rounded-pm-sm">
                                                    ${tags.slice(10).map(tag => `
                                                        <span class="inline-flex items-center space-x-1 bg-pm-accent-tint text-pm-accent px-3 py-1 rounded-full text-sm border border-pm-accent">
                                                            <span>${this.escapeHtml(tag)}</span>
                                                            <button class="remove-tag-btn text-pm-accent hover:text-pm-accent ml-1" data-prompt-id="${prompt.id}" data-tag="${this.escapeHtml(tag)}">&times;</button>
                                                        </span>
                                                    `).join("")}
                                                </div>
                                            </div>
                                            <button class="tags-toggle-btn mt-2 text-sm text-pm-accent hover:text-pm-accent transition-colors flex items-center gap-1" onclick="window.admin.toggleMainTags(${prompt.id})">
                                                <span class="toggle-icon">▼</span>
                                                <span class="toggle-text">Show ${tags.length - 10} more tags</span>
                                            </button>
                                        ` : ''}
                                    </div>

                                    <!-- Film Strip -->
                                    <div class="film-strip-container mt-3 pt-3 border-t border-pm-subtle" id="filmStrip-${prompt.id}">
                                        <div class="film-strip-empty">Loading images...</div>
                                    </div>
                                </div>

                                <div class="flex flex-col space-y-2">
                                    <button class="px-4 py-2 bg-pm-accent hover:bg-pm-accent-hover text-pm text-sm font-medium rounded-pm-sm transition-colors" onclick="window.admin.viewGallery(${prompt.id})">
                                        🖼️ Gallery
                                    </button>
                                    <button class="px-4 py-2 bg-pm-accent hover:bg-pm-accent-hover text-pm text-sm font-medium rounded-pm-sm transition-colors" onclick="window.admin.editPrompt(${prompt.id})">
                                        ✏️ Edit
                                    </button>
                                    <button class="px-4 py-2 bg-pm-error hover:bg-pm-error text-pm text-sm font-medium rounded-pm-sm transition-colors" onclick="window.admin.deletePrompt(${prompt.id})">
                                        🗑️ Delete
                                    </button>
                                </div>
                            </div>
                        </div>
                    </div>
                `;
            }

            renderStars(rating, promptId) {
                return Array.from({ length: 5 }, (_, i) => {
                    const starNum = i + 1;
                    const isActive = starNum <= rating;
                    const starIcon = isActive ? '⭐' : '☆'; // filled star vs outline star
                    const color = isActive ? 'var(--pm-warning)' : 'var(--pm-text-secondary)'; // yellow-400 : gray-400
                    return `<button class="star-btn" data-star="${starNum}" data-prompt-id="${promptId}" style="font-size: 1.125rem; color: ${color}; background: none; border: none; cursor: pointer; transition: all 0.2s; width: 1.125rem; height: 1.125rem; display: inline-flex; align-items: center; justify-content: center; padding: 0; line-height: 1;" onclick="window.admin.setRating(${promptId}, ${starNum})">${starIcon}</button>`;
                }).join("");
            }

            addStarHoverBehavior(promptId) {
                const starButtons = document.querySelectorAll(`[data-prompt-id="${promptId}"].star-btn`);
                const ratingElement = document.querySelector(`[data-id="${promptId}"][data-rating]`);
                const currentRating = parseInt(ratingElement?.dataset.rating || 0);

                starButtons.forEach((starBtn, index) => {
                    const starNum = index + 1;
                    
                    starBtn.addEventListener('mouseenter', () => {
                        // Light up all stars up to this one on hover
                        starButtons.forEach((btn, i) => {
                            if (i < starNum) {
                                btn.textContent = '⭐';
                                btn.style.color = 'var(--pm-warning)'; // bright yellow on hover
                            } else {
                                btn.textContent = '☆';
                                btn.style.color = 'var(--pm-text-secondary)'; // gray
                            }
                        });
                    });

                    starBtn.addEventListener('mouseleave', () => {
                        // Reset to current rating
                        starButtons.forEach((btn, i) => {
                            if (i < currentRating) {
                                btn.textContent = '⭐';
                                btn.style.color = 'var(--pm-warning)'; // yellow
                            } else {
                                btn.textContent = '☆';
                                btn.style.color = 'var(--pm-text-secondary)'; // gray
                            }
                        });
                    });
                });
            }

            escapeHtml(text) {
                const div = document.createElement("div");
                div.textContent = text;
                return div.innerHTML;
            }

            showNotification(message, type = "info") {
                const notification = document.createElement("div");
                
                // Check if ViewerJS is open and use higher z-index
                const viewerContainer = document.querySelector('.viewer-container');
                const isViewerOpen = viewerContainer && viewerContainer.style.display !== 'none';
                const zIndex = isViewerOpen ? 'z-[50000]' : 'z-50';
                
                notification.className = `fixed top-4 right-4 px-4 py-2 rounded-pm-sm shadow-pm text-sm ${zIndex} transition-all duration-300 transform translate-x-full`;

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

            showModal(modalId) {
                document.getElementById(modalId).classList.remove("hidden");
                document.getElementById(modalId).classList.add("flex");
                document.body.style.overflow = "hidden";
            }

            hideModal(modalId) {
                document.getElementById(modalId).classList.add("hidden");
                document.getElementById(modalId).classList.remove("flex");
                document.body.style.overflow = "";
            }

            showSettingsModal() {
                document.getElementById("resultTimeout").value = this.settings.resultTimeout;
                document.getElementById("webuiDisplayMode").value = this.settings.webuiDisplayMode;
                document.getElementById("galleryRootPath").value = this.settings.galleryRootPath || '';
                this.updateMonitoringStatus();
                this.showModal("settingsModal");
            }

            async updateMonitoringStatus() {
                const statusEl = document.getElementById("monitoringStatus");
                try {
                    const response = await fetch("/prompt_manager/settings");
                    if (response.ok) {
                        const data = await response.json();
                        const dirs = data.settings?.monitored_directories || [];
                        if (dirs.length > 0) {
                            statusEl.innerHTML = dirs.map(d => `<div class="truncate" title="${d}">✓ ${d}</div>`).join('');
                        } else {
                            statusEl.textContent = 'No directories being monitored (auto-detect on restart)';
                        }
                    }
                } catch (error) {
                    statusEl.textContent = 'Unable to fetch status';
                }
            }

            async saveSettings() {
                const timeout = parseInt(document.getElementById("resultTimeout").value);
                const displayMode = document.getElementById("webuiDisplayMode").value;
                const galleryPath = document.getElementById("galleryRootPath").value.trim();

                this.settings.resultTimeout = timeout;
                this.settings.webuiDisplayMode = displayMode;
                this.settings.galleryRootPath = galleryPath;

                try {
                    const response = await fetch("/prompt_manager/settings", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            result_timeout: timeout,
                            webui_display_mode: displayMode,
                            gallery_root_path: galleryPath
                        }),
                    });

                    if (response.ok) {
                        const data = await response.json();
                        if (data.restart_required) {
                            this.showNotification("Settings saved. Restart ComfyUI for gallery path changes to take effect.", "warning");
                        } else {
                            this.showNotification("Settings saved successfully", "success");
                        }
                        this.hideModal("settingsModal");
                    } else {
                        throw new Error("Failed to save settings");
                    }
                } catch (error) {
                    this.showNotification("Failed to save settings", "error");
                }
            }

            toggleSelectAll(checked) {
                document.querySelectorAll(".prompt-checkbox").forEach((checkbox) => {
                    checkbox.checked = checked;
                    if (checked) {
                        this.selectedPrompts.add(parseInt(checkbox.dataset.id));
                    } else {
                        this.selectedPrompts.clear();
                    }
                });
                this.updateBulkActionButtons();
            }

            updateBulkActionButtons() {
                const hasSelected = this.selectedPrompts.size > 0;
                document.getElementById("bulkDeleteBtn").disabled = !hasSelected;
                document.getElementById("bulkTagBtn").disabled = !hasSelected;
                document.getElementById("bulkCategoryBtn").disabled = !hasSelected;
            }

            async setRating(promptId, rating) {
                console.log(`Setting rating for prompt ${promptId} to ${rating}`);
                
                // Update UI immediately for better UX
                const ratingElement = document.querySelector(`[data-id="${promptId}"][data-rating]`);
                if (ratingElement) {
                    ratingElement.dataset.rating = rating;
                    ratingElement.innerHTML = this.renderStars(rating, promptId);
                    // Add hover behavior to new stars
                    this.addStarHoverBehavior(promptId);
                }

                try {
                    const response = await fetch(`/prompt_manager/prompts/${promptId}/rating`, {
                        method: "PUT",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ rating }),
                    });

                    if (response.ok) {
                        this.showNotification("Rating updated", "success");
                        
                        // Update the prompt data in memory
                        const promptIndex = this.prompts.findIndex(p => p.id === promptId);
                        if (promptIndex !== -1) {
                            this.prompts[promptIndex].rating = rating;
                        }
                        
                        // Refresh stats
                        setTimeout(() => {
                            this.loadStatistics();
                        }, 500);
                    } else {
                        // Revert UI change on API failure
                        const originalPrompt = this.prompts.find(p => p.id === promptId);
                        const originalRating = originalPrompt ? originalPrompt.rating : 0;
                        if (ratingElement) {
                            ratingElement.dataset.rating = originalRating;
                            ratingElement.innerHTML = this.renderStars(originalRating, promptId);
                        }
                        this.showNotification("Failed to update rating", "error");
                    }
                } catch (error) {
                    console.error("Rating update error:", error);
                    // Revert UI change on error
                    const originalPrompt = this.prompts.find(p => p.id === promptId);
                    const originalRating = originalPrompt ? originalPrompt.rating : 0;
                    if (ratingElement) {
                        ratingElement.dataset.rating = originalRating;
                        ratingElement.innerHTML = this.renderStars(originalRating, promptId);
                    }
                    this.showNotification("Failed to update rating", "error");
                }
            }

            addTag(promptId) {
                // Store the prompt ID for later use
                this.currentTagPromptId = promptId;
                // Clear the input and show the modal
                document.getElementById("individualTagInput").value = "";
                this.showModal("individualTagModal");
            }

            async confirmIndividualTag() {
                const tags = document.getElementById("individualTagInput").value.split(",")
                    .map((tag) => tag.trim()).filter((tag) => tag);

                if (tags.length === 0) return;

                try {
                    const response = await fetch("/prompt_manager/prompts/tags", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            prompt_id: this.currentTagPromptId,
                            tags: tags,
                        }),
                    });

                    if (response.ok) {
                        this.showNotification(`Tags added to prompt`, "success");
                        this.hideModal("individualTagModal");
                        // Refresh both stats and results
                        await this.loadStatistics();
                        this.search();
                    }
                } catch (error) {
                    this.showNotification("Failed to add tags", "error");
                }
            }

            async removeTag(promptId, tag) {
                try {
                    const response = await fetch(`/prompt_manager/prompts/${promptId}/tags`, {
                        method: "DELETE",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ tag }),
                    });

                    if (response.ok) {
                        this.showNotification("Tag removed", "success");
                        // Refresh both stats and results
                        await this.loadStatistics();
                        this.search();
                    }
                } catch (error) {
                    this.showNotification("Failed to remove tag", "error");
                }
            }

            async editPrompt(promptId) {
                const promptElement = document.querySelector(`[data-id="${promptId}"].prompt-text`);
                if (!promptElement) return;

                const originalText = promptElement.textContent;
                promptElement.contentEditable = true;
                promptElement.focus();
                promptElement.classList.add("bg-pm-surface", "border", "border-pm-accent", "rounded-pm-sm", "p-3");

                const saveEdit = async () => {
                    const newText = promptElement.textContent.trim();
                    if (newText !== originalText && newText) {
                        try {
                            const response = await fetch(`/prompt_manager/prompts/${promptId}`, {
                                method: "PUT",
                                headers: { "Content-Type": "application/json" },
                                body: JSON.stringify({ text: newText }),
                            });

                            if (response.ok) {
                                this.showNotification("Prompt updated", "success");
                                // Refresh the page to show updated content
                                setTimeout(() => {
                                    this.loadStatistics();
                                    setTimeout(() => window.location.reload(), 500);
                                }, 1000);
                            } else {
                                throw new Error("Update failed");
                            }
                        } catch (error) {
                            this.showNotification("Failed to update prompt", "error");
                            promptElement.textContent = originalText;
                        }
                    }
                    promptElement.contentEditable = false;
                    promptElement.classList.remove("bg-pm-surface", "border", "border-pm-accent", "rounded-pm-sm", "p-3");
                };

                promptElement.addEventListener("blur", saveEdit, { once: true });
                promptElement.addEventListener("keydown", (e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        saveEdit();
                    }
                    if (e.key === "Escape") {
                        promptElement.textContent = originalText;
                        promptElement.contentEditable = false;
                        promptElement.classList.remove("bg-pm-surface", "border", "border-pm-accent", "rounded-pm-sm", "p-3");
                    }
                });
            }

            async deletePrompt(promptId) {
                if (!confirm("Are you sure you want to delete this prompt?")) return;

                try {
                    const response = await fetch(`/prompt_manager/delete/${promptId}`, {
                        method: "DELETE",
                    });

                    if (response.ok) {
                        this.showNotification("Prompt deleted", "success");
                        // Refresh stats and reload page
                        setTimeout(() => {
                            this.loadStatistics();
                            setTimeout(() => window.location.reload(), 500);
                        }, 1000);
                    }
                } catch (error) {
                    this.showNotification("Failed to delete prompt", "error");
                }
            }

            showBulkTagModal() {
                this.showModal("bulkTagModal");
            }

            showBulkCategoryModal() {
                this.showModal("bulkCategoryModal");
            }

            async confirmBulkTag() {
                const tags = document.getElementById("bulkTagInput").value.split(",")
                    .map((tag) => tag.trim()).filter((tag) => tag);

                if (tags.length === 0) return;

                try {
                    const response = await fetch("/prompt_manager/bulk/tags", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            prompt_ids: Array.from(this.selectedPrompts),
                            tags,
                        }),
                    });

                    if (response.ok) {
                        this.showNotification(`Tags added to ${this.selectedPrompts.size} prompts`, "success");
                        this.hideModal("bulkTagModal");
                        // Refresh both stats and results
                        await this.loadStatistics();
                        this.search();
                    }
                } catch (error) {
                    this.showNotification("Failed to add tags", "error");
                }
            }

            async confirmBulkCategory() {
                const category = document.getElementById("bulkCategoryInput").value.trim();
                if (!category) return;

                try {
                    const response = await fetch("/prompt_manager/bulk/category", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            prompt_ids: Array.from(this.selectedPrompts),
                            category,
                        }),
                    });

                    if (response.ok) {
                        this.showNotification(`Category set for ${this.selectedPrompts.size} prompts`, "success");
                        this.hideModal("bulkCategoryModal");
                        // Refresh both stats and results
                        await this.loadStatistics();
                        this.search();
                    }
                } catch (error) {
                    this.showNotification("Failed to set category", "error");
                }
            }

            async bulkDelete() {
                if (!confirm(`Are you sure you want to delete ${this.selectedPrompts.size} prompts?`)) return;

                try {
                    const response = await fetch("/prompt_manager/bulk/delete", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            prompt_ids: Array.from(this.selectedPrompts),
                        }),
                    });

                    if (response.ok) {
                        this.showNotification(`${this.selectedPrompts.size} prompts deleted`, "success");
                        // Refresh stats and reload page
                        setTimeout(() => {
                            this.loadStatistics();
                            setTimeout(() => window.location.reload(), 500);
                        }, 1000);
                    }
                } catch (error) {
                    this.showNotification("Failed to delete prompts", "error");
                }
            }

            async exportPrompts() {
                try {
                    const response = await fetch("/prompt_manager/export");
                    if (response.ok) {
                        const blob = await response.blob();
                        const url = window.URL.createObjectURL(blob);
                        const a = document.createElement("a");
                        a.href = url;
                        a.download = `prompts-${new Date().toISOString().split("T")[0]}.json`;
                        document.body.appendChild(a);
                        a.click();
                        window.URL.revokeObjectURL(url);
                        document.body.removeChild(a);
                        this.showNotification("Prompts exported", "success");
                    }
                } catch (error) {
                    this.showNotification("Failed to export prompts", "error");
                }
            }

            async showStats() {
                await this.loadStatistics();
                this.showNotification("📊 Stats refreshed successfully!", "success");
            }

            // Gallery functionality
            async viewGallery(promptId) {
                this.showModal("galleryModal");
                
                // Show loading state
                document.getElementById("galleryLoading").classList.remove("hidden");
                document.getElementById("galleryContent").classList.add("hidden");
                document.getElementById("galleryEmpty").classList.add("hidden");
                
                try {
                    const response = await fetch(`/prompt_manager/prompts/${promptId}/images`);
                    if (response.ok) {
                        const data = await response.json();
                        if (data.success) {
                            this.renderGallery(data.images, promptId);
                        } else {
                            throw new Error(data.error || 'Failed to load images');
                        }
                    } else {
                        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                    }
                } catch (error) {
                    console.error('Gallery load error:', error);
                    this.showNotification('Failed to load gallery', 'error');
                    this.closeGallery();
                } finally {
                    document.getElementById("galleryLoading").classList.add("hidden");
                }
            }

            renderGallery(images, promptId) {
                const content = document.getElementById("galleryContent");
                const empty = document.getElementById("galleryEmpty");
                
                // Update title
                const prompt = this.prompts.find(p => p.id === promptId);
                const promptText = prompt ? prompt.text.substring(0, 50) + (prompt.text.length > 50 ? '...' : '') : 'Unknown Prompt';
                document.getElementById("galleryTitle").textContent = `Gallery: ${promptText}`;
                
                if (!images || images.length === 0) {
                    content.classList.add("hidden");
                    empty.classList.remove("hidden");
                    return;
                }
                
                content.classList.remove("hidden");
                empty.classList.add("hidden");
                
                content.innerHTML = images.map((image, index) => `
                    <div class="group cursor-pointer bg-pm-surface rounded-pm-md overflow-hidden border border-pm hover:border-pm transition-all duration-200">
                        <div class="aspect-square bg-pm-surface overflow-hidden relative">
                            <img src="/prompt_manager/images/${image.id}/file" 
                                 alt="Generated image ${index + 1}" 
                                 class="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
                                 data-original="/prompt_manager/images/${image.id}/file"
                                 data-caption="Generated: ${new Date(image.generation_time).toLocaleDateString()} ${new Date(image.generation_time).toLocaleTimeString()} | ${image.width && image.height ? `${image.width}×${image.height}` : 'Unknown size'}${image.file_size ? ` | ${this.formatFileSize(image.file_size)}` : ''}"
                                 onerror="this.parentElement.innerHTML='<div class=\\'flex items-center justify-center h-full text-pm-secondary\\'>⚠️ Image not found</div>'">
                            <div class="absolute inset-0 bg-black/0 group-hover:bg-black/20 transition-opacity duration-300 flex items-center justify-center pointer-events-none">
                                <svg class="w-8 h-8 text-pm opacity-0 group-hover:opacity-100 transition-opacity duration-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path>
                                </svg>
                            </div>
                        </div>
                        <div class="p-3">
                            <div class="text-xs text-pm-secondary mb-1">
                                ${new Date(image.generation_time).toLocaleDateString()} ${new Date(image.generation_time).toLocaleTimeString()}
                            </div>
                            <div class="text-xs text-pm-muted">
                                ${image.width && image.height ? `${image.width}×${image.height}` : 'Unknown size'}
                                ${image.file_size ? ` • ${this.formatFileSize(image.file_size)}` : ''}
                            </div>
                        </div>
                    </div>
                `).join('');
                
                // Store images for navigation
                this.currentGalleryImages = images;
                
                // Initialize ViewerJS for the gallery after DOM is updated
                setTimeout(() => {
                    if (this.galleryViewer) {
                        this.galleryViewer.destroy();
                    }
                    
                    this.galleryViewer = new Viewer(content, {
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
                        navbar: true,
                        title: true,
                        transition: true,
                        keyboard: true,
                        backdrop: 'static',
                        loading: true,
                        loop: true,
                        tooltip: true,
                        zoomRatio: 0.1,
                        minZoomRatio: 0.1,
                        maxZoomRatio: 4,
                        zoomOnTouch: true,
                        zoomOnWheel: true,
                        slideOnTouch: true,
                        toggleOnDblclick: false,
                        className: '',
                        shown: () => {
                            this.addMetadataSidebar();
                        },
                        viewed: (event) => {
                            this.loadMetadataForImage(event.detail.image);
                        }
                    });
                }, 100);
            }





            closeGallery() {
                this.hideModal("galleryModal");
            }




            formatFileSize(bytes) {
                if (!bytes) return 'Unknown';
                const units = ['B', 'KB', 'MB', 'GB'];
                let size = bytes;
                let unitIndex = 0;
                
                while (size >= 1024 && unitIndex < units.length - 1) {
                    size /= 1024;
                    unitIndex++;
                }
                
                return `${size.toFixed(1)} ${units[unitIndex]}`;
            }

            // Diagnostics functionality
            showDiagnosticsModal() {
                this.showModal("diagnosticsModal");
            }

            closeDiagnostics() {
                this.hideModal("diagnosticsModal");
            }

            // Maintenance functionality
            showMaintenanceModal() {
                this.showModal("maintenanceModal");
            }

            closeMaintenance() {
                this.hideModal("maintenanceModal");
            }

            selectAllMaintenance() {
                document.querySelectorAll('.maintenance-option').forEach(checkbox => {
                    checkbox.checked = true;
                });
            }

            clearAllMaintenance() {
                document.querySelectorAll('.maintenance-option').forEach(checkbox => {
                    checkbox.checked = false;
                });
            }

            async runMaintenance() {
                const resultsContainer = document.getElementById("maintenanceResults");
                const runButton = document.getElementById("runMaintenanceBtn");
                
                // Get selected operations
                const selectedOps = Array.from(document.querySelectorAll('.maintenance-option:checked'))
                    .map(checkbox => checkbox.id);
                
                if (selectedOps.length === 0) {
                    this.showNotification('Please select at least one maintenance operation', 'warning');
                    return;
                }
                
                // Show loading state
                runButton.disabled = true;
                runButton.innerHTML = '🔄 Running...';
                resultsContainer.innerHTML = `
                    <div class="text-center py-4">
                        <div class="w-12 h-12 border-4 border-teal-500/30 border-t-teal-500 rounded-full animate-spin mx-auto mb-4"></div>
                        <p class="text-pm-secondary">Running maintenance operations...</p>
                        <p class="text-sm text-pm-muted mt-2">Operations: ${selectedOps.map(op => op.replace(/_/g, ' ')).join(', ')}</p>
                    </div>
                `;
                
                try {
                    const response = await fetch('/prompt_manager/maintenance', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ operations: selectedOps })
                    });
                    
                    if (response.ok) {
                        const data = await response.json();
                        if (data.success) {
                            this.renderMaintenanceResults(data);
                            
                            // Show overall success notification
                            if (data.all_successful) {
                                this.showNotification('✅ All maintenance operations completed successfully!', 'success');
                            } else {
                                this.showNotification('⚠️ Some maintenance operations had issues', 'warning');
                            }
                            
                            // Refresh stats after maintenance
                            setTimeout(() => this.loadStatistics(), 1000);
                        } else {
                            throw new Error(data.error || 'Maintenance failed');
                        }
                    } else {
                        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                    }
                } catch (error) {
                    console.error('Maintenance error:', error);
                    resultsContainer.innerHTML = `
                        <div class="bg-pm-error-tint border border-pm-error rounded-pm-sm p-4">
                            <h4 class="text-pm-error font-medium mb-2">❌ Maintenance Failed</h4>
                            <p class="text-pm-secondary">${error.message}</p>
                        </div>
                    `;
                    this.showNotification('❌ Maintenance failed', 'error');
                } finally {
                    runButton.disabled = false;
                    runButton.innerHTML = '🔧 Run Maintenance';
                }
            }

            renderMaintenanceResults(data) {
                const resultsContainer = document.getElementById("maintenanceResults");
                
                let html = '<div class="space-y-4">';
                
                // Summary
                html += '<div class="bg-pm-surface rounded-pm-sm p-4">';
                html += '<h4 class="text-pm font-medium mb-3">📋 Maintenance Summary</h4>';
                html += '<div class="grid grid-cols-1 md:grid-cols-2 gap-3">';
                html += `
                    <div class="flex justify-between items-center p-2 bg-pm-input rounded">
                        <span class="text-pm-secondary">Operations Completed</span>
                        <span class="text-pm-success font-mono text-sm">${data.operations_completed}</span>
                    </div>
                `;
                html += `
                    <div class="flex justify-between items-center p-2 bg-pm-input rounded">
                        <span class="text-pm-secondary">Overall Success</span>
                        <span class="${data.all_successful ? 'text-pm-success' : 'text-pm-warning'} font-mono text-sm">
                            ${data.all_successful ? '✅ ALL PASSED' : '⚠️ SOME ISSUES'}
                        </span>
                    </div>
                `;
                html += '</div></div>';
                
                // Detailed results
                for (const [operation, result] of Object.entries(data.results)) {
                    const bgColor = result.success ? 'bg-pm-success-tint border-pm-success' : 'bg-pm-error-tint border-pm-error';
                    const statusIcon = result.success ? '✅' : '❌';
                    const statusText = result.success ? 'SUCCESS' : 'FAILED';
                    
                    html += `<div class="${bgColor} border rounded-pm-sm p-4">`;
                    html += `<h4 class="text-pm font-medium mb-2 flex items-center space-x-2">`;
                    html += `<span>${statusIcon}</span>`;
                    html += `<span class="capitalize">${operation.replace(/_/g, ' ')}</span>`;
                    html += `<span class="text-sm font-mono ${result.success ? 'text-pm-success' : 'text-pm-error'}">${statusText}</span>`;
                    html += `</h4>`;

                    if (result.message) {
                        html += `<p class="text-pm-secondary mb-2">${result.message}</p>`;
                    }

                    // Show specific details
                    if (result.removed_count !== undefined) {
                        html += `<div class="text-sm text-pm-secondary">Items removed: ${result.removed_count}</div>`;
                    }

                    if (result.duplicate_hashes !== undefined) {
                        html += `<div class="text-sm text-pm-secondary">Duplicate hash groups found: ${result.duplicate_hashes}</div>`;
                    }

                    if (result.issues_found !== undefined) {
                        html += `<div class="text-sm text-pm-secondary">Issues found: ${result.issues_found}</div>`;
                        if (result.issues && result.issues.length > 0) {
                            html += `<ul class="ml-4 list-disc text-xs text-pm-muted mt-1">`;
                            result.issues.forEach(issue => {
                                html += `<li>${issue}</li>`;
                            });
                            html += `</ul>`;
                        }
                    }

                    if (result.info) {
                        html += `<div class="text-sm text-pm-secondary mt-2">`;
                        html += `<p>Total prompts: ${result.info.total_prompts || 'N/A'}</p>`;
                        html += `<p>Database size: ${result.info.database_size_bytes ? this.formatFileSize(result.info.database_size_bytes) : 'N/A'}</p>`;
                        html += `</div>`;
                    }

                    if (result.error) {
                        html += `<div class="text-sm text-pm-error mt-2 font-mono bg-pm-error-tint p-2 rounded">`;
                        html += `Error: ${result.error}`;
                        html += `</div>`;
                    }
                    
                    html += '</div>';
                }
                
                html += '</div>';
                resultsContainer.innerHTML = html;
            }

            async runDiagnostics() {
                const content = document.getElementById("diagnosticsContent");
                content.innerHTML = `
                    <div class="text-center py-4">
                        <div class="w-12 h-12 border-4 border-orange-500/30 border-t-orange-500 rounded-full animate-spin mx-auto mb-4"></div>
                        <p class="text-pm-secondary">Running diagnostics...</p>
                    </div>
                `;

                try {
                    const response = await fetch('/prompt_manager/diagnostics');
                    if (response.ok) {
                        const data = await response.json();
                        if (data.success) {
                            this.renderDiagnostics(data.diagnostics);
                        } else {
                            throw new Error(data.error || 'Diagnostics failed');
                        }
                    } else {
                        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                    }
                } catch (error) {
                    console.error('Diagnostics error:', error);
                    content.innerHTML = `
                        <div class="bg-pm-error-tint border border-pm-error rounded-pm-sm p-4">
                            <h4 class="text-pm-error font-medium mb-2">❌ Diagnostics Failed</h4>
                            <p class="text-pm-secondary">${error.message}</p>
                        </div>
                    `;
                }
            }

            renderDiagnostics(diagnostics) {
                const content = document.getElementById("diagnosticsContent");
                
                let html = '<div class="space-y-4">';
                
                // Summary
                html += '<div class="bg-pm-surface rounded-pm-sm p-4">';
                html += '<h4 class="text-pm font-medium mb-3">📋 Diagnostic Summary</h4>';
                html += '<div class="grid grid-cols-1 md:grid-cols-2 gap-3">';

                for (const [category, result] of Object.entries(diagnostics)) {
                    const status = result.status === 'ok' ? '✅ PASS' : (result.status === 'warning' ? '⚠️ WARNING' : '❌ FAIL');
                    const statusColor = result.status === 'ok' ? 'text-pm-success' : (result.status === 'warning' ? 'text-pm-warning' : 'text-pm-error');

                    html += `
                        <div class="flex justify-between items-center p-2 bg-pm-input rounded">
                            <span class="text-pm-secondary capitalize">${this.escapeHtml(category)}</span>
                            <span class="${statusColor} font-mono text-sm">${status}</span>
                        </div>
                    `;
                }
                
                html += '</div></div>';
                
                // Detailed results
                for (const [category, result] of Object.entries(diagnostics)) {
                    const bgColor = result.status === 'ok' ? 'bg-pm-success-tint border-pm-success' :
                                   (result.status === 'warning' ? 'bg-pm-warning/20 border-pm-warning' : 'bg-pm-error-tint border-pm-error');

                    html += `<div class="${bgColor} border rounded-pm-sm p-4">`;
                    html += `<h4 class="text-pm font-medium mb-2 capitalize">${this.escapeHtml(category)}</h4>`;

                    if (result.message) {
                        html += `<p class="text-pm-secondary mb-2">${result.message}</p>`;
                    }

                    // Show specific details based on category
                    if (category === 'database' && result.status === 'ok') {
                        html += `<div class="text-sm text-pm-secondary">`;
                        html += `<p>Prompts: ${result.prompt_count || 0}</p>`;
                        html += `<p>Images table: ${result.has_images_table ? 'Yes' : 'No'}</p>`;
                        html += `</div>`;
                    }

                    if (category === 'images_table' && result.status === 'ok') {
                        html += `<div class="text-sm text-pm-secondary">`;
                        html += `<p>Images: ${result.image_count || 0}</p>`;
                        if (result.recent_images && result.recent_images.length > 0) {
                            html += `<p>Recent images:</p>`;
                            html += `<ul class="ml-4 list-disc">`;
                            result.recent_images.slice(0, 3).forEach(img => {
                                html += `<li>${img.filename} → Prompt ${img.prompt_id}</li>`;
                            });
                            html += `</ul>`;
                        }
                        html += `</div>`;
                    }

                    if (category === 'comfyui_output' && result.output_dirs) {
                        html += `<div class="text-sm text-pm-secondary">`;
                        html += `<p>Output directories found:</p>`;
                        html += `<ul class="ml-4 list-disc">`;
                        result.output_dirs.forEach(dir => {
                            html += `<li>${dir}</li>`;
                        });
                        html += `</ul>`;
                        html += `</div>`;
                    }

                    if (category === 'dependencies' && result.dependencies) {
                        html += `<div class="text-sm text-pm-secondary">`;
                        for (const [dep, available] of Object.entries(result.dependencies)) {
                            const status = available ? '✅' : '❌';
                            html += `<p>${status} ${dep}</p>`;
                        }
                        html += `</div>`;
                    }
                    
                    html += '</div>';
                }
                
                html += '</div>';
                content.innerHTML = html;
            }

            async testImageLink() {
                if (this.prompts.length === 0) {
                    this.showNotification('No prompts available for testing', 'warning');
                    return;
                }
                
                // Use the first prompt for testing
                const testPrompt = this.prompts[0];
                
                try {
                    const response = await fetch('/prompt_manager/diagnostics/test-link', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            prompt_id: testPrompt.id,
                            image_path: '/test/fake/image.png'
                        })
                    });
                    
                    if (response.ok) {
                        const data = await response.json();
                        if (data.success) {
                            this.showNotification('✅ Test image link created successfully!', 'success');
                            // Refresh the gallery to show the test image
                            setTimeout(() => this.search(), 1000);
                        } else {
                            throw new Error(data.result?.message || 'Test failed');
                        }
                    } else {
                        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                    }
                } catch (error) {
                    console.error('Test link error:', error);
                    this.showNotification(`❌ Test failed: ${error.message}`, 'error');
                }
            }

            // Database backup and restore functionality
            async backupDatabase() {
                try {
                    const response = await fetch('/prompt_manager/backup');
                    
                    if (response.ok) {
                        // Get the filename from the Content-Disposition header
                        const contentDisposition = response.headers.get('Content-Disposition');
                        let filename = 'prompts_backup.db';
                        
                        if (contentDisposition) {
                            const filenameMatch = contentDisposition.match(/filename="(.+)"/);
                            if (filenameMatch) {
                                filename = filenameMatch[1];
                            }
                        }
                        
                        // Create blob and download
                        const blob = await response.blob();
                        const url = window.URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        a.href = url;
                        a.download = filename;
                        document.body.appendChild(a);
                        a.click();
                        window.URL.revokeObjectURL(url);
                        document.body.removeChild(a);
                        
                        this.showNotification('💾 Database backup downloaded successfully!', 'success');
                    } else {
                        const errorData = await response.json();
                        throw new Error(errorData.error || 'Backup failed');
                    }
                } catch (error) {
                    console.error('Backup error:', error);
                    this.showNotification(`❌ Backup failed: ${error.message}`, 'error');
                }
            }

            showRestoreModal() {
                // Clear previous file selection
                document.getElementById('restoreFileInput').value = '';
                this.showModal('restoreModal');
            }

            async confirmRestore() {
                const fileInput = document.getElementById('restoreFileInput');
                const file = fileInput.files[0];
                
                if (!file) {
                    this.showNotification('Please select a database file to restore', 'warning');
                    return;
                }
                
                if (!file.name.endsWith('.db')) {
                    this.showNotification('Please select a valid .db file', 'warning');
                    return;
                }
                
                // Show confirmation
                const confirmed = confirm(
                    `Are you sure you want to restore from "${file.name}"?\n\n` +
                    'This will replace your current database. A backup will be created automatically.\n\n' +
                    'This action cannot be undone.'
                );
                
                if (!confirmed) {
                    return;
                }
                
                try {
                    // Disable the restore button and show loading
                    const confirmBtn = document.getElementById('confirmRestore');
                    confirmBtn.disabled = true;
                    confirmBtn.innerHTML = '🔄 Restoring...';
                    
                    // Create FormData for file upload
                    const formData = new FormData();
                    formData.append('database_file', file);
                    
                    const response = await fetch('/prompt_manager/restore', {
                        method: 'POST',
                        body: formData
                    });
                    
                    const data = await response.json();
                    
                    if (response.ok && data.success) {
                        this.showNotification(
                            `✅ Database restored successfully! Found ${data.prompt_count} prompts.`, 
                            'success'
                        );
                        this.hideModal('restoreModal');
                        
                        // Refresh the interface to show new data
                        setTimeout(() => {
                            window.location.reload();
                        }, 2000);
                    } else {
                        throw new Error(data.error || 'Restore failed');
                    }
                } catch (error) {
                    console.error('Restore error:', error);
                    this.showNotification(`❌ Restore failed: ${error.message}`, 'error');
                } finally {
                    // Re-enable the restore button
                    const confirmBtn = document.getElementById('confirmRestore');
                    confirmBtn.disabled = false;
                    confirmBtn.innerHTML = 'Restore Database';
                }
            }

            // Copy prompt to clipboard functionality
            async copyPromptToClipboard(promptId) {
                // Find the prompt text from the prompts array
                const prompt = this.prompts.find(p => p.id === promptId);
                if (!prompt) {
                    this.showNotification('❌ Prompt not found', 'error');
                    return;
                }
                
                const promptText = prompt.text;
                
                try {
                    // Use the modern Clipboard API if available
                    if (navigator.clipboard && window.isSecureContext) {
                        await navigator.clipboard.writeText(promptText);
                        this.showNotification('📋 Prompt copied to clipboard!', 'success');
                    } else {
                        // Fallback for older browsers or non-secure contexts
                        const textArea = document.createElement('textarea');
                        textArea.value = promptText;
                        textArea.style.position = 'fixed';
                        textArea.style.left = '-999999px';
                        textArea.style.top = '-999999px';
                        document.body.appendChild(textArea);
                        textArea.focus();
                        textArea.select();
                        
                        if (document.execCommand('copy')) {
                            this.showNotification('📋 Prompt copied to clipboard!', 'success');
                        } else {
                            throw new Error('Copy command failed');
                        }
                        
                        document.body.removeChild(textArea);
                    }
                } catch (error) {
                    console.error('Copy to clipboard failed:', error);
                    this.showNotification('❌ Failed to copy prompt to clipboard', 'error');
                    
                    // Show a fallback modal with the text for manual copying
                    this.showCopyFallbackModal(promptText);
                }
            }

            showCopyFallbackModal(text) {
                // Create a temporary modal for manual copy
                const modal = document.createElement('div');
                modal.className = 'fixed inset-0 bg-black/50 flex items-center justify-center z-50';
                modal.innerHTML = `
                    <div class="bg-pm-surface rounded-pm-md p-4 max-w-2xl w-full mx-4 border border-pm">
                        <h3 class="text-sm font-semibold text-pm mb-3">Copy Prompt Text</h3>
                        <p class="text-pm-secondary mb-4">Please manually copy the text below:</p>
                        <textarea readonly class="w-full h-32 px-4 py-3 bg-pm-surface border border-pm rounded-pm-sm text-pm resize-none" style="font-family: monospace;">${text}</textarea>
                        <div class="flex justify-end mt-4">
                            <button class="px-4 py-1.5 bg-pm-accent hover:bg-pm-accent-hover text-pm font-medium rounded-pm-sm transition-colors" onclick="this.closest('[class*=fixed]').remove()">
                                Close
                            </button>
                        </div>
                    </div>
                `;
                document.body.appendChild(modal);
                document.body.style.overflow = "hidden";
                
                // Auto-select the text in the textarea
                const textarea = modal.querySelector('textarea');
                textarea.focus();
                textarea.select();
                
                // Close modal when clicking outside
                modal.addEventListener('click', (e) => {
                    if (e.target === modal) {
                        modal.remove();
                        document.body.style.overflow = "";
                    }
                });
                
                // Also handle the close button
                modal.querySelector('button').addEventListener('click', () => {
                    document.body.style.overflow = "";
                });
            }

            // Scan functionality
            showScanModal() {
                // Reset progress display
                document.getElementById("scanProgress").classList.add("hidden");
                document.getElementById("startScan").disabled = false;
                document.getElementById("startScan").textContent = "Start Scan";
                this.showModal("scanModal");
            }

            async quickBackup() {
                try {
                    const response = await fetch("/prompt_manager/backup");
                    if (response.ok) {
                        const blob = await response.blob();
                        const url = window.URL.createObjectURL(blob);
                        const a = document.createElement("a");
                        a.href = url;
                        a.download = `prompts-backup-${new Date().toISOString().split("T")[0]}.db`;
                        document.body.appendChild(a);
                        a.click();
                        window.URL.revokeObjectURL(url);
                        document.body.removeChild(a);
                        this.showNotification("Database backup created successfully!", "success");
                    } else {
                        throw new Error("Backup failed");
                    }
                } catch (error) {
                    this.showNotification("Failed to create backup", "error");
                }
            }

            async startScan() {
                // Show progress section
                document.getElementById("scanProgress").classList.remove("hidden");
                document.getElementById("startScan").disabled = true;
                document.getElementById("startScan").textContent = "Scanning...";
                
                // Reset progress
                this.updateScanProgress(0, "Initializing scan...", 0, 0);
                
                try {
                    const response = await fetch("/prompt_manager/scan", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({})
                    });

                    if (!response.ok) {
                        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                    }

                    // Handle streaming response
                    const reader = response.body.getReader();
                    const decoder = new TextDecoder();

                    while (true) {
                        const { done, value } = await reader.read();
                        if (done) break;

                        const chunk = decoder.decode(value);
                        const lines = chunk.split('\n');
                        
                        for (const line of lines) {
                            if (line.trim().startsWith('data: ')) {
                                try {
                                    const data = JSON.parse(line.substring(6));
                                    if (data.type === 'progress') {
                                        this.updateScanProgress(
                                            data.progress,
                                            data.status,
                                            data.processed,
                                            data.found
                                        );
                                    } else if (data.type === 'complete') {
                                        this.completeScan(data.processed, data.found, data.added, data.linked);
                                        return;
                                    } else if (data.type === 'error') {
                                        throw new Error(data.message);
                                    }
                                } catch (e) {
                                    console.log('Non-JSON line:', line);
                                }
                            }
                        }
                    }
                } catch (error) {
                    this.showNotification(`Scan failed: ${error.message}`, "error");
                    document.getElementById("startScan").disabled = false;
                    document.getElementById("startScan").textContent = "Start Scan";
                }
            }

            updateScanProgress(progress, status, processed, found) {
                document.getElementById("scanProgressBar").style.width = `${progress}%`;
                document.getElementById("scanStatusText").textContent = status;
                document.getElementById("scanCount").textContent = `${processed} files processed`;
                document.getElementById("scanFound").textContent = `${found} prompts found`;
            }

            completeScan(processed, found, added, linked = 0) {
                this.updateScanProgress(100, "Scan completed!", processed, found);
                document.getElementById("startScan").disabled = false;
                document.getElementById("startScan").textContent = "Start New Scan";
                
                // Show detailed notification with all counts
                const linkedText = linked > 0 ? `, linked ${linked} images to existing prompts` : '';
                this.showNotification(
                    `Scan completed! Processed ${processed} files, found ${found} prompts, added ${added} new prompts to database${linkedText}.`,
                    "success"
                );
                
                // Auto-close modal after a short delay to let user see the completion message
                setTimeout(() => {
                    this.hideModal("scanModal");
                    
                    // Refresh the statistics immediately
                    this.loadStatistics();
                    
                    // Also reload the page after a short delay to ensure everything is fully updated
                    setTimeout(() => {
                        window.location.reload();
                    }, 1000);
                }, 2000);
            }

            // Logs functionality
            showLogsModal() {
                this.showModal("logsModal");
                this.loadLogStats();
                this.loadLogFiles();
                this.loadLogConfig();
            }

            closeLogs() {
                this.hideModal("logsModal");
            }

            async loadLogStats() {
                try {
                    const response = await fetch('/prompt_manager/logs/stats');
                    const data = await response.json();
                    
                    if (data.success) {
                        const stats = data.stats;
                        document.getElementById("logStatsTotal").textContent = stats.buffer_count || 0;
                        document.getElementById("logStatsErrors").textContent = stats.level_counts?.ERROR || 0;
                        document.getElementById("logStatsWarnings").textContent = stats.level_counts?.WARNING || 0;
                        document.getElementById("logStatsLevel").textContent = stats.current_level || "INFO";
                        document.getElementById("logStatsSize").textContent = this.formatBytes(stats.total_log_size || 0);
                    }
                } catch (error) {
                    console.error("Failed to load log stats:", error);
                }
            }

            async loadLogConfig() {
                try {
                    const response = await fetch('/prompt_manager/logs/config');
                    const data = await response.json();
                    
                    if (data.success) {
                        const config = data.config;
                        document.getElementById("setLogLevel").value = config.level || "INFO";
                        document.getElementById("consoleLogging").checked = config.console_logging !== false;
                    }
                } catch (error) {
                    console.error("Failed to load log config:", error);
                }
            }

            async loadLogFiles() {
                try {
                    const response = await fetch('/prompt_manager/logs/files');
                    const data = await response.json();
                    
                    if (data.success) {
                        const container = document.getElementById("logFilesList");
                        
                        if (data.files.length === 0) {
                            container.innerHTML = '<div class="col-span-full text-center text-pm-muted">No log files found</div>';
                            return;
                        }
                        
                        container.innerHTML = data.files.map(file => `
                            <div class="bg-pm-surface rounded-pm-sm p-3">
                                <div class="flex items-center justify-between mb-2">
                                    <span class="text-sm font-medium text-pm">${file.filename}</span>
                                    ${file.is_main ? '<span class="bg-pm-accent text-xs px-2 py-1 rounded">Active</span>' : ''}
                                </div>
                                <div class="text-xs text-pm-secondary mb-2">
                                    Size: ${this.formatBytes(file.size)} | Modified: ${new Date(file.modified).toLocaleString()}
                                </div>
                                <button onclick="window.admin.downloadLogFile('${file.filename}')"
                                        class="w-full px-3 py-1 bg-pm-success hover:bg-pm-success text-pm text-xs rounded transition-colors">
                                    📥 Download
                                </button>
                            </div>
                        `).join('');
                    }
                } catch (error) {
                    console.error("Failed to load log files:", error);
                }
            }

            async refreshLogs() {
                const level = document.getElementById("logLevel").value;
                const limit = parseInt(document.getElementById("logLimit").value);
                
                try {
                    const url = new URL('/prompt_manager/logs', window.location.origin);
                    if (level) url.searchParams.set('level', level);
                    url.searchParams.set('limit', limit.toString());
                    
                    const response = await fetch(url);
                    const data = await response.json();
                    
                    if (data.success) {
                        this.displayLogs(data.logs);
                        this.showNotification(`Loaded ${data.logs.length} log entries`, "success");
                    } else {
                        this.showNotification(`Failed to load logs: ${data.error}`, "error");
                    }
                } catch (error) {
                    console.error("Failed to refresh logs:", error);
                    this.showNotification("Failed to refresh logs", "error");
                }
            }

            displayLogs(logs) {
                const container = document.getElementById("logsContainer");
                
                if (logs.length === 0) {
                    container.innerHTML = '<div class="text-center py-4 text-pm-muted">No logs found</div>';
                    return;
                }

                container.innerHTML = logs.map(log => {
                    const levelColors = {
                        DEBUG: 'text-pm-secondary',
                        INFO: 'text-pm-accent',
                        WARNING: 'text-pm-warning',
                        ERROR: 'text-pm-error',
                        CRITICAL: 'text-pm-error'
                    };

                    const levelColor = levelColors[log.level] || 'text-pm-secondary';
                    const timestamp = new Date(log.timestamp).toLocaleString();

                    return `
                        <div class="border-l-2 border-pm pl-3 py-1 hover:bg-pm-surface transition-colors">
                            <div class="flex items-start space-x-2 text-sm">
                                <span class="text-pm-muted text-xs font-mono w-24 flex-shrink-0">${timestamp.split(' ')[1]}</span>
                                <span class="${levelColor} font-semibold w-16 flex-shrink-0">${log.level}</span>
                                <span class="text-pm-accent text-xs w-32 flex-shrink-0">${log.logger}</span>
                                <span class="text-pm-secondary flex-1">${log.message}</span>
                            </div>
                            <div class="text-xs text-pm-muted ml-44">
                                ${log.filename}:${log.lineno}
                            </div>
                        </div>
                    `;
                }).join('');
                
                // Auto-scroll to bottom
                container.scrollTop = container.scrollHeight;
            }

            async downloadLogs() {
                const level = document.getElementById("logLevel").value;
                const limit = parseInt(document.getElementById("logLimit").value);
                
                try {
                    const url = new URL('/prompt_manager/logs', window.location.origin);
                    if (level) url.searchParams.set('level', level);
                    url.searchParams.set('limit', limit.toString());
                    
                    const response = await fetch(url);
                    const data = await response.json();
                    
                    if (data.success) {
                        const logText = data.logs.map(log => 
                            `${log.timestamp} [${log.level}] ${log.logger}: ${log.message} (${log.filename}:${log.lineno})`
                        ).join('\n');
                        
                        const blob = new Blob([logText], { type: 'text/plain' });
                        const url2 = URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        a.href = url2;
                        a.download = `prompt_manager_logs_${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.txt`;
                        document.body.appendChild(a);
                        a.click();
                        document.body.removeChild(a);
                        URL.revokeObjectURL(url2);
                        
                        this.showNotification("Logs downloaded successfully", "success");
                    } else {
                        this.showNotification(`Failed to download logs: ${data.error}`, "error");
                    }
                } catch (error) {
                    console.error("Failed to download logs:", error);
                    this.showNotification("Failed to download logs", "error");
                }
            }

            async downloadLogFile(filename) {
                try {
                    const response = await fetch(`/prompt_manager/logs/download/${encodeURIComponent(filename)}`);
                    
                    if (response.ok) {
                        const blob = await response.blob();
                        const url = URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        a.href = url;
                        a.download = filename;
                        document.body.appendChild(a);
                        a.click();
                        document.body.removeChild(a);
                        URL.revokeObjectURL(url);
                        
                        this.showNotification(`Downloaded ${filename}`, "success");
                    } else {
                        this.showNotification(`Failed to download ${filename}`, "error");
                    }
                } catch (error) {
                    console.error("Failed to download log file:", error);
                    this.showNotification("Failed to download log file", "error");
                }
            }

            async clearLogs() {
                if (!confirm("Are you sure you want to clear all log files? This cannot be undone.")) {
                    return;
                }
                
                try {
                    const response = await fetch('/prompt_manager/logs/truncate', {
                        method: 'POST'
                    });
                    const data = await response.json();
                    
                    if (data.success) {
                        this.showNotification(data.message, "success");
                        this.refreshLogs();
                        this.loadLogStats();
                        this.loadLogFiles();
                    } else {
                        this.showNotification(`Failed to clear logs: ${data.error}`, "error");
                    }
                } catch (error) {
                    console.error("Failed to clear logs:", error);
                    this.showNotification("Failed to clear logs", "error");
                }
            }

            async updateLogConfig() {
                const level = document.getElementById("setLogLevel").value;
                const consoleLogging = document.getElementById("consoleLogging").checked;
                
                try {
                    const response = await fetch('/prompt_manager/logs/config', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({
                            level: level,
                            console_logging: consoleLogging
                        })
                    });
                    
                    const data = await response.json();
                    
                    if (data.success) {
                        this.showNotification("Log configuration updated", "success");
                        this.loadLogStats();
                    } else {
                        this.showNotification(`Failed to update config: ${data.error}`, "error");
                    }
                } catch (error) {
                    console.error("Failed to update log config:", error);
                    this.showNotification("Failed to update log configuration", "error");
                }
            }

            formatBytes(bytes) {
                if (bytes === 0) return '0 B';
                const k = 1024;
                const sizes = ['B', 'KB', 'MB', 'GB'];
                const i = Math.floor(Math.log(bytes) / Math.log(k));
                return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
            }

            // Pagination methods
            updatePaginationControls() {
                const { currentPage, totalPages, total, limit } = this.pagination;
                
                // Update pagination info
                document.getElementById("currentPage").textContent = currentPage;
                document.getElementById("totalPages").textContent = totalPages;
                document.getElementById("totalResults").textContent = total;
                
                // Calculate showing range
                const start = ((currentPage - 1) * limit) + 1;
                const end = Math.min(currentPage * limit, total);
                document.getElementById("showingStart").textContent = start;
                document.getElementById("showingEnd").textContent = end;
                
                // Update results count
                document.getElementById("resultsCount").textContent = `${this.prompts.length} of ${total}`;
                
                // Update button states
                document.getElementById("firstPageBtn").disabled = currentPage === 1;
                document.getElementById("prevPageBtn").disabled = currentPage === 1;
                document.getElementById("nextPageBtn").disabled = currentPage === totalPages;
                document.getElementById("lastPageBtn").disabled = currentPage === totalPages;
            }

            async goToPage(page) {
                if (page < 1 || page > this.pagination.totalPages || page === this.pagination.currentPage) {
                    return;
                }
                await this.loadRecentPrompts(page);
                // Scroll to top of results after page loads
                window.scrollTo({ top: 0, behavior: 'smooth' });
            }

            async changeLimit(newLimit) {
                this.pagination.limit = newLimit;
                this.pagination.currentPage = 1; // Reset to first page
                await this.loadRecentPrompts(1);
            }

            // Metadata functionality
            openMetadataViewer() {
                window.open('/prompt_manager/gallery.html', '_blank');
            }

            // Gallery functionality
            openGallery() {
                window.open('/prompt_manager/gallery', '_blank');
            }



            async parsePNGMetadata(arrayBuffer) {
                const dataView = new DataView(arrayBuffer);
                let offset = 8; // Skip PNG signature
                const metadata = {};
                let chunkCount = 0;

                console.log('Starting PNG metadata parsing...');

                while (offset < arrayBuffer.byteLength - 8) {
                    const length = dataView.getUint32(offset);
                    const type = new TextDecoder().decode(arrayBuffer.slice(offset + 4, offset + 8));
                    
                    chunkCount++;
                    console.log(`Chunk ${chunkCount}: type=${type}, length=${length}`);
                    
                    if (type === 'tEXt' || type === 'iTXt' || type === 'zTXt') {
                        const chunkData = arrayBuffer.slice(offset + 8, offset + 8 + length);
                        let text;
                        
                        if (type === 'tEXt') {
                            text = new TextDecoder().decode(chunkData);
                        } else if (type === 'iTXt') {
                            // iTXt format: keyword\0compression\0language\0translated_keyword\0text
                            const textData = new TextDecoder().decode(chunkData);
                            const parts = textData.split('\0');
                            console.log(`iTXt parts count: ${parts.length}, first part: ${parts[0]}`);
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
                            console.log(`Found metadata: ${key} = ${value.substring(0, 100)}...`);
                            metadata[key] = value;
                        }
                    }
                    
                    offset += 8 + length + 4; // Move to next chunk (8 = length + type, 4 = CRC)
                }

                console.log(`Parsed ${chunkCount} chunks, found ${Object.keys(metadata).length} metadata items`);
                return metadata;
            }

            extractComfyUIData(metadata) {
                // Look for ComfyUI workflow data in various possible fields
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
                            console.log(`Successfully parsed workflow field: ${field}`);
                            break;
                        } catch (e) {
                            console.log('Failed to parse workflow field:', field, e.message);
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
                            console.log(`Successfully parsed prompt field: ${field}`);
                            break;
                        } catch (e) {
                            console.log('Failed to parse prompt field:', field, e.message);
                            console.log('Raw data:', metadata[field].substring(0, 200) + '...');
                        }
                    }
                }

                return { workflow: workflowData, prompt: promptData };
            }

            updateMetadataPanel(comfyData, imageSrc) {
                const metadataContent = document.getElementById('metadataContent');
                if (!metadataContent) return;

                // Get the actual file path from current image data
                let filePath = imageSrc; // fallback to URL
                if (this.currentGalleryImages && this.currentImageIndex !== null && this.currentGalleryImages[this.currentImageIndex]) {
                    const currentImage = this.currentGalleryImages[this.currentImageIndex];
                    filePath = currentImage.image_path || currentImage.filename || imageSrc;
                }

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
                    console.log('Parsing prompt data...', Object.keys(comfyData.prompt).length, 'nodes');
                    const promptNodes = comfyData.prompt;
                    
                    for (const nodeId in promptNodes) {
                        const node = promptNodes[nodeId];
                        console.log(`Node ${nodeId}:`, node.class_type, node);
                        
                        // Checkpoint
                        if (node.class_type === 'CheckpointLoaderSimple' && node.inputs) {
                            checkpoint = node.inputs.ckpt_name || checkpoint;
                            console.log('Found checkpoint:', checkpoint);
                        }
                        
                        // Prompts - need to identify which is positive vs negative
                        if (node.class_type === 'PromptManager' && node.inputs && node.inputs.text) {
                            // PromptManager typically contains the positive prompt
                            const promptValue = typeof node.inputs.text === 'string' ? node.inputs.text : (Array.isArray(node.inputs.text) ? node.inputs.text[0] : String(node.inputs.text));
                            positivePrompt = promptValue;
                            console.log('Found PromptManager with text:', promptValue.substring(0, 100));
                        }
                        
                        if (node.class_type === 'CLIPTextEncode' && node.inputs && node.inputs.text) {
                            // Check if this looks like a negative prompt
                            const textValue = typeof node.inputs.text === 'string' ? node.inputs.text : (Array.isArray(node.inputs.text) ? node.inputs.text[0] : String(node.inputs.text));
                            console.log('Found CLIPTextEncode:', textValue.substring(0, 50));
                            const text = textValue.toLowerCase();
                            if (text.includes('bad anatomy') || text.includes('unfinished') || 
                                text.includes('censored') || text.includes('weird anatomy') ||
                                text.includes('negative') || text.includes('embedding:')) {
                                negativePrompt = textValue;
                                console.log('Found negative prompt:', textValue.substring(0, 100));
                            } else if (positivePrompt === 'No prompt found') {
                                // If we haven't found a positive prompt yet, this might be it
                                positivePrompt = textValue;
                                console.log('Found potential positive prompt:', textValue.substring(0, 100));
                            }
                        }
                        
                        // Sampling parameters
                        if (node.class_type === 'KSampler' && node.inputs) {
                            seed = node.inputs.seed || seed;
                            steps = node.inputs.steps || steps;
                            cfgScale = node.inputs.cfg || cfgScale;
                            sampler = node.inputs.sampler_name || sampler;
                            console.log('Found sampler params:', {seed, steps, cfgScale, sampler});
                        }
                    }
                } else {
                    console.log('No prompt data found, will try workflow fallback');
                }

                // Store the current metadata for copying
                this.currentMetadata = {
                    positivePrompt,
                    negativePrompt,
                    checkpoint,
                    steps,
                    cfgScale,
                    sampler,
                    seed,
                    workflow: comfyData.workflow,
                    prompt: comfyData.prompt
                };

                // Update the HTML
                metadataContent.innerHTML = `
                    <!-- File Path -->
                    <div>
                        <h2 class="text-sm font-medium text-pm-secondary mb-2">File Path</h2>
                        <div class="text-sm text-pm-accent hover:text-pm-accent cursor-pointer bg-pm-surface p-2 rounded break-all" onclick="window.admin.copyToClipboard('${filePath}')">
                            ${filePath}
                        </div>
                    </div>

                    <!-- Resources used -->
                    <div>
                        <h2 class="text-sm font-medium text-pm-secondary mb-2">Resources used</h2>
                        <div class="flex items-center justify-between">
                            <div>
                                <div class="text-pm-accent hover:text-pm-accent cursor-pointer">${checkpoint}</div>
                                <div class="text-xs text-pm-muted">ComfyUI Generated</div>
                            </div>
                            <span class="px-2 py-1 text-xs bg-pm-surface text-pm-secondary rounded">CHECKPOINT</span>
                        </div>
                    </div>

                    <!-- Prompt -->
                    <div>
                        <div class="flex items-center gap-2 mb-2">
                            <h2 class="text-sm font-medium text-pm-secondary">Prompt</h2>
                            <span class="px-2 py-1 text-xs bg-orange-600 text-orange-100 rounded">COMFYUI</span>
                            <button class="ml-auto text-pm-secondary hover:text-pm-secondary" onclick="window.admin.copyPrompt('positive')">
                                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"></path>
                                </svg>
                            </button>
                        </div>
                        <div class="text-sm text-pm-secondary bg-pm-surface p-3 rounded max-h-32 overflow-y-auto">
                            ${positivePrompt.substring(0, 200)}${positivePrompt.length > 200 ? '...' : ''}
                        </div>
                        ${positivePrompt.length > 200 ? '<button class="text-pm-accent hover:text-pm-accent text-sm mt-1" onclick="window.admin.showFullPrompt(\'positive\')">Show more</button>' : ''}
                    </div>

                    <!-- Negative prompt -->
                    <div>
                        <div class="flex items-center justify-between mb-2">
                            <h2 class="text-sm font-medium text-pm-secondary">Negative prompt</h2>
                            <button class="text-pm-secondary hover:text-pm-secondary" onclick="window.admin.copyPrompt('negative')">
                                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"></path>
                                </svg>
                            </button>
                        </div>
                        <div class="text-sm text-pm-secondary bg-pm-surface p-3 rounded max-h-32 overflow-y-auto">
                            ${negativePrompt.substring(0, 200)}${negativePrompt.length > 200 ? '...' : ''}
                        </div>
                        ${negativePrompt.length > 200 ? '<button class="text-pm-accent hover:text-pm-accent text-sm mt-1" onclick="window.admin.showFullPrompt(\'negative\')">Show more</button>' : ''}
                    </div>

                    <!-- Other metadata -->
                    <div>
                        <h2 class="text-sm font-medium text-pm-secondary mb-3">Other metadata</h2>
                        <div class="flex flex-wrap gap-2">
                            <span class="px-2 py-1 text-xs bg-pm-surface text-pm-secondary rounded">CFG SCALE: ${cfgScale}</span>
                            <span class="px-2 py-1 text-xs bg-pm-surface text-pm-secondary rounded">STEPS: ${steps}</span>
                            <span class="px-2 py-1 text-xs bg-pm-surface text-pm-secondary rounded">SAMPLER: ${sampler}</span>
                        </div>
                        <div class="mt-2">
                            <span class="px-2 py-1 text-xs bg-pm-surface text-pm-secondary rounded">SEED: ${seed}</span>
                        </div>
                    </div>

                    <!-- Raw Workflow Data -->
                    <div>
                        <h2 class="text-sm font-medium text-pm-secondary mb-2">ComfyUI Workflow</h2>
                        <div class="flex items-center gap-2">
                            <button class="text-pm-accent hover:text-pm-accent text-sm" onclick="window.admin.showWorkflowData()">View Raw Workflow JSON</button>
                            <button class="text-pm-accent hover:text-pm-accent" onclick="window.admin.downloadWorkflowJSON()" title="Download JSON">
                                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
                                </svg>
                            </button>
                        </div>
                    </div>
                `;
            }

            showMetadataError() {
                const metadataContent = document.getElementById('metadataContent');
                if (!metadataContent) return;

                metadataContent.innerHTML = `
                    <div class="text-center text-pm-error py-8">
                        <svg class="w-16 h-16 mx-auto mb-4 opacity-30" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                        </svg>
                        <p class="text-sm mb-2">Error Loading Metadata</p>
                        <p class="text-sm">Could not extract ComfyUI metadata from this image</p>
                    </div>
                `;
            }

            async copyPrompt(type) {
                if (!this.currentMetadata) {
                    this.showNotification('❌ No metadata available for copying', 'error');
                    return;
                }
                
                const text = type === 'positive' ? this.currentMetadata.positivePrompt : this.currentMetadata.negativePrompt;
                
                if (!text || text === 'No prompt found' || text === 'No negative prompt found') {
                    this.showNotification(`❌ No ${type} prompt available`, 'error');
                    return;
                }
                
                await this.copyToClipboard(text);
            }

            async tryFallbackMetadata(imageSrc) {
                console.log('Trying fallback metadata extraction for:', imageSrc);
                
                try {
                    // Try to get prompt from current gallery image data
                    let fallbackPrompt = 'No prompt found';
                    
                    if (this.currentGalleryImages && this.currentImageIndex !== null && this.currentGalleryImages[this.currentImageIndex]) {
                        const currentImage = this.currentGalleryImages[this.currentImageIndex];
                        console.log('Current image data:', currentImage);
                        
                        // If we have prompt_id, try to get the prompt from our local prompts array
                        if (currentImage.prompt_id && this.prompts) {
                            const prompt = this.prompts.find(p => p.id === currentImage.prompt_id);
                            if (prompt) {
                                fallbackPrompt = prompt.text;
                                console.log('Found prompt from database:', fallbackPrompt.substring(0, 100));
                            }
                        }
                    }
                    
                    // Set fallback metadata
                    this.currentMetadata = {
                        positivePrompt: fallbackPrompt,
                        negativePrompt: 'No negative prompt found',
                        checkpoint: 'Unknown',
                        steps: 'Unknown',
                        cfgScale: 'Unknown',
                        sampler: 'Unknown',
                        seed: 'Unknown',
                        workflow: null,
                        prompt: null
                    };
                    
                    // Update metadata panel with fallback data
                    this.updateMetadataPanel({}, imageSrc);
                    
                } catch (error) {
                    console.error('Fallback metadata extraction failed:', error);
                    this.showMetadataError();
                    
                    // Set empty metadata as last resort
                    this.currentMetadata = {
                        positivePrompt: 'No prompt found',
                        negativePrompt: 'No negative prompt found',
                        checkpoint: 'Unknown',
                        steps: 'Unknown',
                        cfgScale: 'Unknown',
                        sampler: 'Unknown',
                        seed: 'Unknown',
                        workflow: null,
                        prompt: null
                    };
                }
            }

            async copyAllMetadata() {
                if (!this.currentMetadata) return;
                
                const allData = `Checkpoint: ${this.currentMetadata.checkpoint || 'Unknown'}
Positive Prompt: ${this.currentMetadata.positivePrompt}
Negative Prompt: ${this.currentMetadata.negativePrompt}
Steps: ${this.currentMetadata.steps || 'Unknown'}
CFG Scale: ${this.currentMetadata.cfgScale || 'Unknown'}
Sampler: ${this.currentMetadata.sampler || 'Unknown'}
Seed: ${this.currentMetadata.seed || 'Unknown'}`;
                
                await this.copyToClipboard(allData);
            }

            showFullPrompt(type) {
                if (!this.currentMetadata) return;

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

            showWorkflowData() {
                if (!this.currentMetadata || !this.currentMetadata.workflow) return;
                
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

            downloadWorkflowJSON() {
                if (!this.currentMetadata || !this.currentMetadata.workflow) return;
                
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
                // Look for ComfyUI workflow data in various possible fields
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
                    
                    for (const nodeId in promptNodes) {
                        const node = promptNodes[nodeId];
                        
                        // Checkpoint
                        if (node.class_type === 'CheckpointLoaderSimple' && node.inputs) {
                            checkpoint = node.inputs.ckpt_name || checkpoint;
                        }
                        
                        // Prompts - need to identify which is positive vs negative
                        if (node.class_type === 'PromptManager' && node.inputs && node.inputs.text) {
                            // PromptManager typically contains the positive prompt
                            const promptValue = typeof node.inputs.text === 'string' ? node.inputs.text : (Array.isArray(node.inputs.text) ? node.inputs.text[0] : String(node.inputs.text));
                            positivePrompt = promptValue;
                        }
                        
                        if (node.class_type === 'CLIPTextEncode' && node.inputs && node.inputs.text) {
                            // Check if this looks like a negative prompt
                            const textValue = typeof node.inputs.text === 'string' ? node.inputs.text : (Array.isArray(node.inputs.text) ? node.inputs.text[0] : String(node.inputs.text));
                            const text = textValue.toLowerCase();
                            if (text.includes('bad anatomy') || text.includes('unfinished') || 
                                text.includes('censored') || text.includes('weird anatomy') ||
                                text.includes('negative') || text.includes('embedding:')) {
                                negativePrompt = textValue;
                            } else if (positivePrompt === 'No prompt found') {
                                // If we haven't found a positive prompt yet, this might be it
                                positivePrompt = textValue;
                            }
                        }
                        
                        // Sampling parameters
                        if (node.class_type === 'KSampler' && node.inputs) {
                            seed = node.inputs.seed || seed;
                            steps = node.inputs.steps || steps;
                            cfgScale = node.inputs.cfg || cfgScale;
                            sampler = node.inputs.sampler_name || sampler;
                        }
                    }
                }

                // Parse workflow data (this is what we actually have in your case)
                if (comfyData.workflow && comfyData.workflow.nodes) {
                    const nodes = comfyData.workflow.nodes;
                    console.log('DEBUG: Total nodes found:', nodes.length);
                    
                    // Debug: Log all nodes to understand the structure
                    nodes.forEach((node, index) => {
                        console.log(`Node ${index} (ID: ${node.id}):`, {
                            type: node.type,
                            title: node.title,
                            widgets_values: node.widgets_values
                        });
                    });
                    
                    // Look for checkpoint loader
                    const checkpointNode = nodes.find(node => 
                        node.type === 'CheckpointLoaderSimple' || 
                        node.type === 'CheckpointLoader'
                    );
                    if (checkpointNode && checkpointNode.widgets_values && checkpointNode.widgets_values[0]) {
                        checkpoint = checkpointNode.widgets_values[0];
                    }

                    // Look for prompts in workflow nodes
                    const textEncodeNodes = nodes.filter(node => node.type === 'CLIPTextEncode');
                    const promptManagerNodes = nodes.filter(node => node.type === 'PromptManager');
                    
                    // Check PromptManager first for positive prompt
                    if (promptManagerNodes.length > 0 && promptManagerNodes[0].widgets_values && promptManagerNodes[0].widgets_values[0]) {
                        positivePrompt = promptManagerNodes[0].widgets_values[0];
                    }
                    
                    // For negative prompt, look for CLIPTextEncode that contains negative keywords
                    for (const node of textEncodeNodes) {
                        if (node.widgets_values && node.widgets_values[0]) {
                            const text = node.widgets_values[0].toLowerCase();
                            if (text.includes('bad anatomy') || text.includes('unfinished') || 
                                text.includes('censored') || text.includes('weird anatomy') ||
                                text.includes('negative') || text.includes('embedding:')) {
                                negativePrompt = node.widgets_values[0];
                                break;
                            }
                        }
                    }

                    // Extract generation parameters from their source nodes
                    // Look for specific node types with better fallback logic
                    
                    // CFG Scale - prioritize Float nodes with title 'CFG', then other sources
                    console.log('DEBUG: Looking for CFG scale...');
                    
                    // First priority: Float node with title 'CFG'
                    const cfgFloatNode = nodes.find(node => node.type === 'Float' && node.title === 'CFG');
                    if (cfgFloatNode && cfgFloatNode.widgets_values && cfgFloatNode.widgets_values[0]) {
                        cfgScale = cfgFloatNode.widgets_values[0];
                        console.log(`DEBUG: Found CFG in Float node: ${cfgScale}`);
                    }
                    
                    // If not found, look for specific value of 7 (your target CFG)
                    if (cfgScale === 'Unknown') {
                        for (const node of nodes) {
                            if (node.widgets_values && Array.isArray(node.widgets_values)) {
                                for (let i = 0; i < node.widgets_values.length; i++) {
                                    const value = node.widgets_values[i];
                                    if (value === 7) {  // Target the specific value you mentioned
                                        cfgScale = value;
                                        console.log(`DEBUG: Found target CFG value 7 in node ${node.id} (${node.type}) at index ${i}`);
                                        break;
                                    }
                                }
                                if (cfgScale !== 'Unknown') break;
                            }
                        }
                    }
                    
                    // Fallback: any reasonable CFG value
                    if (cfgScale === 'Unknown') {
                        for (const node of nodes) {
                            if (node.widgets_values && Array.isArray(node.widgets_values)) {
                                for (let i = 0; i < node.widgets_values.length; i++) {
                                    const value = node.widgets_values[i];
                                    if (typeof value === 'number' && value > 1 && value <= 30) {
                                        console.log(`DEBUG: Found potential CFG ${value} in node ${node.id} (${node.type}) at index ${i}`);
                                        cfgScale = value;
                                        console.log(`DEBUG: Set CFG scale to ${value}`);
                                        break;
                                    }
                                }
                                if (cfgScale !== 'Unknown') break;
                            }
                        }
                    }
                    
                    // Steps - prioritize Int nodes with title 'Steps', then target value 30
                    console.log('DEBUG: Looking for steps...');
                    
                    // First priority: Int node with title 'Steps'
                    const stepsIntNode = nodes.find(node => node.type === 'Int' && node.title === 'Steps');
                    if (stepsIntNode && stepsIntNode.widgets_values && stepsIntNode.widgets_values[0]) {
                        steps = stepsIntNode.widgets_values[0];
                        console.log(`DEBUG: Found steps in Int node: ${steps}`);
                    }
                    
                    // If not found, look for specific value of 30 (your target steps)
                    if (steps === 'Unknown') {
                        for (const node of nodes) {
                            if (node.widgets_values && Array.isArray(node.widgets_values)) {
                                for (let i = 0; i < node.widgets_values.length; i++) {
                                    const value = node.widgets_values[i];
                                    if (value === 30) {  // Target the specific value you mentioned
                                        steps = value;
                                        console.log(`DEBUG: Found target steps value 30 in node ${node.id} (${node.type}) at index ${i}`);
                                        break;
                                    }
                                }
                                if (steps !== 'Unknown') break;
                            }
                        }
                    }
                    
                    // Fallback: reasonable step values, prefer 10-150 range
                    if (steps === 'Unknown') {
                        for (const node of nodes) {
                            if (node.widgets_values && Array.isArray(node.widgets_values)) {
                                for (let i = 0; i < node.widgets_values.length; i++) {
                                    const value = node.widgets_values[i];
                                    if (typeof value === 'number' && value >= 10 && value <= 150) {
                                        console.log(`DEBUG: Found good steps value ${value} in node ${node.id} (${node.type}) at index ${i}`);
                                        steps = value;
                                        console.log(`DEBUG: Set steps to ${value}`);
                                        break;
                                    }
                                }
                                if (steps !== 'Unknown') break;
                            }
                        }
                    }
                    
                    // Sampler - look for valid ComfyUI samplers
                    console.log('DEBUG: Looking for sampler...');
                    const validSamplers = [
                        'euler', 'euler_ancestral', 'heun', 'dpm_2', 'dpm_2_ancestral', 'lms', 
                        'dpm_fast', 'dpm_adaptive', 'dpmpp_2s_ancestral', 'dpmpp_sde', 'dpmpp_sde_gpu',
                        'dpmpp_2m', 'dpmpp_2m_sde', 'dpmpp_2m_sde_gpu', 'dpmpp_3m_sde', 'dpmpp_3m_sde_gpu',
                        'ddim', 'uni_pc', 'uni_pc_bh2'
                    ];
                    for (const node of nodes) {
                        if (node.widgets_values && Array.isArray(node.widgets_values)) {
                            for (let i = 0; i < node.widgets_values.length; i++) {
                                const value = node.widgets_values[i];
                                if (typeof value === 'string') {
                                    const samplerValue = value.toLowerCase();
                                    if (validSamplers.some(validSampler => samplerValue.includes(validSampler))) {
                                        console.log(`DEBUG: Found sampler ${value} in node ${node.id} (${node.type}) at index ${i}`);
                                        if (sampler === 'Unknown') {
                                            sampler = value;
                                            console.log(`DEBUG: Set sampler to ${value}`);
                                        }
                                    }
                                }
                            }
                        }
                    }
                    
                    // Seed - look for large numbers that could be seeds
                    console.log('DEBUG: Looking for seed...');
                    for (const node of nodes) {
                        if (node.widgets_values && Array.isArray(node.widgets_values)) {
                            for (let i = 0; i < node.widgets_values.length; i++) {
                                const value = node.widgets_values[i];
                                if (typeof value === 'number' && value > 100000) {
                                    console.log(`DEBUG: Found potential seed ${value} in node ${node.id} (${node.type}) at index ${i}`);
                                    if (seed === 'Unknown') {
                                        seed = value;
                                        console.log(`DEBUG: Set seed to ${value}`);
                                    }
                                }
                            }
                        }
                    }
                    
                    // If we still don't have sampling params, look at KSampler widgets_values directly
                    const ksamplerNode = nodes.find(node => 
                        node.type === 'KSampler' || node.type === 'KSamplerAdvanced'
                    );
                    if (ksamplerNode && ksamplerNode.widgets_values) {
                        const widgets = ksamplerNode.widgets_values;
                        // KSampler widgets_values order: [seed, control_mode, steps, cfg, sampler_name, scheduler, denoise, ...]
                        if (seed === 'Unknown' && widgets[0]) seed = widgets[0];
                        if (steps === 'Unknown' && widgets[2]) steps = widgets[2];
                        if (cfgScale === 'Unknown' && widgets[3]) cfgScale = widgets[3];
                        if (sampler === 'Unknown' && widgets[4]) sampler = widgets[4];
                    }
                }

                return {
                    positivePrompt,
                    negativePrompt,
                    checkpoint,
                    steps,
                    cfgScale,
                    sampler,
                    seed,
                    workflow: comfyData.workflow,
                    prompt: comfyData.prompt,
                    imagePath
                };
            }

            addMetadataSidebar() {
                const viewerContainer = document.querySelector('.viewer-container');
                if (!viewerContainer || document.getElementById('metadata-sidebar')) return;

                const sidebar = document.createElement('div');
                sidebar.id = 'metadata-sidebar';
                sidebar.className = 'metadata-sidebar';
                sidebar.innerHTML = `
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
                        <!-- Initial placeholder content -->
                        <div class="text-center text-pm-muted py-8">
                            <svg class="w-16 h-16 mx-auto mb-4 opacity-30" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
                            </svg>
                            <p class="text-sm mb-2">Loading Metadata...</p>
                            <p class="text-sm">Extracting ComfyUI workflow data</p>
                        </div>
                    </div>
                `;

                viewerContainer.appendChild(sidebar);
                this.attachMetadataEventListeners();
            }

            async loadMetadataForImage(imgElement) {
                const imageUrl = imgElement.getAttribute('data-original') || imgElement.src;
                if (!imageUrl) return;

                const metadataContent = document.getElementById('metadata-content');
                if (!metadataContent) return;

                // Show loading state
                metadataContent.innerHTML = `
                    <div class="metadata-loading">
                        <div class="text-center text-pm-muted py-8">
                            <svg class="w-16 h-16 mx-auto mb-4 opacity-30 animate-spin" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path>
                            </svg>
                            <p class="text-sm mb-2">Loading Metadata...</p>
                            <p class="text-sm">Extracting ComfyUI workflow data</p>
                        </div>
                    </div>
                `;

                try {
                    const metadata = await this.extractImageMetadata(imageUrl);
                    this.currentMetadata = metadata;
                    this.updateMetadataSidebar(metadata);
                } catch (error) {
                    console.error('Failed to load metadata:', error);
                    metadataContent.innerHTML = `
                        <div class="text-center text-pm-error py-8">
                            <p class="text-sm mb-2">Failed to Load Metadata</p>
                            <p class="text-sm">Could not extract ComfyUI workflow data</p>
                        </div>
                    `;
                }
            }

            updateMetadataSidebar(metadata) {
                const metadataContent = document.getElementById('metadata-content');
                if (!metadataContent || !metadata) return;

                metadataContent.innerHTML = `
                <!-- File Path -->
                <div>
                    <h2 class="text-sm font-medium text-pm-secondary mb-2">File Path</h2>
                    <div class="text-sm text-pm-accent hover:text-pm-accent cursor-pointer bg-pm-surface p-2 rounded break-all" data-copy-path="">
                        ${metadata.imagePath}
                    </div>
                </div>

                <!-- Resources used -->
                <div>
                    <h2 class="text-sm font-medium text-pm-secondary mb-2">Resources used</h2>
                    <div class="flex items-center justify-between">
                        <div>
                            <div class="text-pm-accent hover:text-pm-accent cursor-pointer">${metadata.checkpoint}</div>
                            <div class="text-xs text-pm-muted">ComfyUI Generated</div>
                        </div>
                        <span class="px-2 py-1 text-xs bg-pm-surface text-pm-secondary rounded">CHECKPOINT</span>
                    </div>
                </div>

                <!-- Prompt -->
                <div>
                    <div class="flex items-center gap-2 mb-2">
                        <h2 class="text-sm font-medium text-pm-secondary">Prompt</h2>
                        <span class="px-2 py-1 text-xs bg-orange-600 text-orange-100 rounded">COMFYUI</span>
                        <button class="ml-auto text-pm-secondary hover:text-pm-secondary" data-copy-type="positive">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"></path>
                            </svg>
                        </button>
                    </div>
                    <div class="text-sm text-pm-secondary bg-pm-surface p-3 rounded max-h-32 overflow-y-auto">
                        ${metadata.positivePrompt.substring(0, 200)}${metadata.positivePrompt.length > 200 ? '...' : ''}
                    </div>
                    ${metadata.positivePrompt.length > 200 ? '<button class="text-pm-accent hover:text-pm-accent text-sm mt-1" data-show-type="positive">Show more</button>' : ''}
                </div>

                <!-- Negative prompt -->
                <div>
                    <div class="flex items-center justify-between mb-2">
                        <h2 class="text-sm font-medium text-pm-secondary">Negative prompt</h2>
                        <button class="text-pm-secondary hover:text-pm-secondary" data-copy-type="negative">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"></path>
                            </svg>
                        </button>
                    </div>
                    <div class="text-sm text-pm-secondary bg-pm-surface p-3 rounded max-h-32 overflow-y-auto">
                        ${metadata.negativePrompt.substring(0, 200)}${metadata.negativePrompt.length > 200 ? '...' : ''}
                    </div>
                    ${metadata.negativePrompt.length > 200 ? '<button class="text-pm-accent hover:text-pm-accent text-sm mt-1" data-show-type="negative">Show more</button>' : ''}
                </div>

                <!-- Other metadata -->
                <div>
                    <h2 class="text-sm font-medium text-pm-secondary mb-3">Other metadata</h2>
                    <div class="flex flex-wrap gap-2">
                        <span class="px-2 py-1 text-xs bg-pm-surface text-pm-secondary rounded">CFG SCALE: ${metadata.cfgScale}</span>
                        <span class="px-2 py-1 text-xs bg-pm-surface text-pm-secondary rounded">STEPS: ${metadata.steps}</span>
                        <span class="px-2 py-1 text-xs bg-pm-surface text-pm-secondary rounded">SAMPLER: ${metadata.sampler}</span>
                    </div>
                    <div class="mt-2">
                        <span class="px-2 py-1 text-xs bg-pm-surface text-pm-secondary rounded">SEED: ${metadata.seed}</span>
                    </div>
                </div>

                <!-- Raw Workflow Data -->
                <div>
                    <h2 class="text-sm font-medium text-pm-secondary mb-2">ComfyUI Workflow</h2>
                    <div class="flex items-center gap-2">
                        <button class="text-pm-accent hover:text-pm-accent text-sm" data-action="show-workflow">View Raw Workflow JSON</button>
                        <button class="text-pm-accent hover:text-pm-accent" data-action="download-workflow" title="Download JSON">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
                            </svg>
                        </button>
                    </div>
                </div>
                `;
                
                // Re-attach event listeners after updating content
                this.attachMetadataEventListeners();
            }

            attachMetadataEventListeners() {
                const sidebar = document.getElementById('metadata-sidebar');
                if (!sidebar) return;

                // Copy buttons
                const copyBtns = sidebar.querySelectorAll('[data-copy-type]');
                copyBtns.forEach(btn => {
                    btn.addEventListener('click', (e) => {
                        const type = btn.getAttribute('data-copy-type');
                        if (this.currentMetadata) {
                            const text = type === 'positive' ? this.currentMetadata.positivePrompt : this.currentMetadata.negativePrompt;
                            this.copyToClipboard(text);
                        }
                    });
                });

                // File path copy
                const pathEl = sidebar.querySelector('[data-copy-path]');
                if (pathEl) {
                    pathEl.addEventListener('click', () => {
                        if (this.currentMetadata) {
                            this.copyToClipboard(this.currentMetadata.imagePath);
                        }
                    });
                }

                // Show more buttons
                const showMoreBtns = sidebar.querySelectorAll('[data-show-type]');
                showMoreBtns.forEach(btn => {
                    btn.addEventListener('click', (e) => {
                        const type = btn.getAttribute('data-show-type');
                        this.showFullPrompt(type);
                    });
                });

                // Workflow action buttons
                const actionBtns = sidebar.querySelectorAll('[data-action]');
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
                const copyAllBtn = sidebar.querySelector('.metadata-copy-all');
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
                cancelled: false,
                skipAllTagged: false,
                retagResolve: null,  // Promise resolver for retag confirmation
                tagsExpanded: false  // Track accordion state
            };

            // ==================== Add Prompt Modal ====================

            showAddPromptModal() {
                // Clear form
                document.getElementById('addPromptText').value = '';
                document.getElementById('addPromptCategory').value = '';
                document.getElementById('addPromptRatingValue').value = '';
                document.getElementById('addPromptNotes').value = '';
                document.getElementById('addPromptProtected').checked = true;

                // Clear tags
                this.addPromptTags = [];
                this.renderAddPromptTags();

                // Reset rating stars
                this.updateAddPromptRatingStars(0);

                // Populate categories datalist
                this.populateAddPromptCategories();

                this.showModal('addPromptModal');
            }

            setupAddPromptModal() {
                this.addPromptTags = [];

                // Rating stars
                const ratingContainer = document.getElementById('addPromptRating');
                const ratingStars = ratingContainer.querySelectorAll('.rating-star');
                ratingStars.forEach(star => {
                    star.addEventListener('click', () => {
                        const rating = parseInt(star.dataset.rating);
                        document.getElementById('addPromptRatingValue').value = rating;
                        this.updateAddPromptRatingStars(rating);
                    });
                });

                // Clear rating button
                document.getElementById('clearAddPromptRatingBtn').addEventListener('click', () => {
                    document.getElementById('addPromptRatingValue').value = '';
                    this.updateAddPromptRatingStars(0);
                });

                // Tag input
                const tagInput = document.getElementById('addPromptTagInput');
                tagInput.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter' || e.key === ',') {
                        e.preventDefault();
                        const tag = tagInput.value.trim().replace(/,/g, '');
                        if (tag && !this.addPromptTags.includes(tag)) {
                            this.addPromptTags.push(tag);
                            this.renderAddPromptTags();
                        }
                        tagInput.value = '';
                    }
                });

                // Tag suggestions
                tagInput.addEventListener('input', () => {
                    this.showAddPromptTagSuggestions(tagInput.value);
                });
            }

            updateAddPromptRatingStars(rating) {
                const stars = document.querySelectorAll('#addPromptRating .rating-star');
                stars.forEach((star, index) => {
                    if (index < rating) {
                        star.textContent = '★';
                        star.classList.add('text-pm-warning');
                        star.classList.remove('text-pm-muted');
                    } else {
                        star.textContent = '☆';
                        star.classList.remove('text-pm-warning');
                        star.classList.add('text-pm-muted');
                    }
                });
            }

            renderAddPromptTags() {
                const container = document.getElementById('addPromptTagsContainer');
                const input = document.getElementById('addPromptTagInput');

                // Remove existing tag chips
                container.querySelectorAll('.tag-chip').forEach(chip => chip.remove());

                // Add tag chips
                this.addPromptTags.forEach(tag => {
                    const chip = document.createElement('span');
                    chip.className = 'tag-chip inline-flex items-center px-2 py-1 bg-pm-accent text-pm text-xs rounded cursor-pointer hover:bg-pm-accent-hover';
                    chip.innerHTML = `${tag} <span class="ml-1">&times;</span>`;
                    chip.addEventListener('click', () => {
                        this.addPromptTags = this.addPromptTags.filter(t => t !== tag);
                        this.renderAddPromptTags();
                    });
                    container.insertBefore(chip, input);
                });
            }

            showAddPromptTagSuggestions(query) {
                const suggestionsContainer = document.getElementById('addPromptTagSuggestions');
                if (!query) {
                    suggestionsContainer.classList.add('hidden');
                    return;
                }

                const matchingTags = this.tags.filter(tag =>
                    tag.toLowerCase().includes(query.toLowerCase()) &&
                    !this.addPromptTags.includes(tag)
                ).slice(0, 10);

                if (matchingTags.length === 0) {
                    suggestionsContainer.classList.add('hidden');
                    return;
                }

                suggestionsContainer.innerHTML = matchingTags.map(tag => `
                    <div class="px-3 py-2 hover:bg-pm-hover cursor-pointer text-sm text-pm" data-tag="${tag}">
                        ${tag}
                    </div>
                `).join('');

                suggestionsContainer.querySelectorAll('[data-tag]').forEach(el => {
                    el.addEventListener('click', () => {
                        const tag = el.dataset.tag;
                        if (!this.addPromptTags.includes(tag)) {
                            this.addPromptTags.push(tag);
                            this.renderAddPromptTags();
                        }
                        document.getElementById('addPromptTagInput').value = '';
                        suggestionsContainer.classList.add('hidden');
                    });
                });

                suggestionsContainer.classList.remove('hidden');
            }

            populateAddPromptCategories() {
                const datalist = document.getElementById('addPromptCategoryList');
                datalist.innerHTML = this.categories.map(cat => `<option value="${cat}">`).join('');
            }

            async saveNewPrompt() {
                const text = document.getElementById('addPromptText').value.trim();
                const category = document.getElementById('addPromptCategory').value.trim();
                const rating = document.getElementById('addPromptRatingValue').value;
                const notes = document.getElementById('addPromptNotes').value.trim();
                const isProtected = document.getElementById('addPromptProtected').checked;

                if (!text) {
                    this.showNotification('Prompt text is required', 'error');
                    return;
                }

                try {
                    const response = await fetch('/prompt_manager/save', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            text: text,
                            category: category || null,
                            rating: rating ? parseInt(rating) : null,
                            tags: this.addPromptTags,
                            notes: notes || null,
                            is_protected: isProtected
                        })
                    });

                    const data = await response.json();

                    if (data.success) {
                        this.showNotification('Prompt added successfully!', 'success');
                        this.hideModal('addPromptModal');
                        // Refresh the prompts list
                        this.search();
                        // Reload categories and tags in case new ones were added
                        this.loadCategories();
                        this.loadTags();
                    } else {
                        this.showNotification(data.error || 'Failed to add prompt', 'error');
                    }
                } catch (error) {
                    console.error('Error adding prompt:', error);
                    this.showNotification('Failed to add prompt', 'error');
                }
            }

            // ==================== End Add Prompt Modal ====================

            // ==================== Film Strip Functions ====================

            getImageUrl(image) {
                // If the image already has a url property, use it
                if (image.url) return image.url;

                // Otherwise construct from image_path
                if (image.image_path) {
                    // Extract relative path from the full path
                    // The image_path might be absolute, we need to serve it via the API
                    const path = image.image_path;
                    // Try to get filename for serving
                    const filename = image.filename || path.split(/[/\\]/).pop();
                    // Use the image ID if available, otherwise use the path-based serve endpoint
                    if (image.id) {
                        return `/prompt_manager/images/${image.id}/file`;
                    }
                    // Fallback: serve by path (encode the path)
                    return `/prompt_manager/images/serve/${encodeURIComponent(path)}`;
                }
                return null;
            }

            getThumbnailUrl(image) {
                // If thumbnail_url already exists, use it
                if (image.thumbnail_url) return image.thumbnail_url;

                // Try to construct thumbnail URL from filename and relative_path
                // Thumbnails are stored as: thumbnails/{parent_dir}/{stem}_thumb{ext}
                // to avoid collisions with same-named files in different directories
                if (image.filename) {
                    const lastDot = image.filename.lastIndexOf('.');
                    if (lastDot > 0) {
                        const stem = image.filename.substring(0, lastDot);
                        const ext = image.filename.substring(lastDot);
                        
                        // Get parent directory from relative_path if available
                        let parentDir = '';
                        if (image.relative_path) {
                            const relPath = image.relative_path.replace(/\\/g, '/');
                            const lastSlash = relPath.lastIndexOf('/');
                            if (lastSlash > 0) {
                                parentDir = relPath.substring(0, lastSlash) + '/';
                            }
                        }
                        
                        // URL encode the path components (but preserve slashes)
                        const encodedParentDir = parentDir.split('/').map(p => encodeURIComponent(p)).join('/');
                        return `/prompt_manager/images/serve/thumbnails/${encodedParentDir}${encodeURIComponent(stem)}_thumb${ext}`;
                    }
                }

                // Fall back to full image URL
                return this.getImageUrl(image);
            }

            async loadFilmStripImages(promptId) {
                try {
                    const response = await fetch(`/prompt_manager/prompts/${promptId}/images`);
                    if (response.ok) {
                        const data = await response.json();
                        if (data.success && data.images) {
                            return data.images;
                        }
                    }
                    return [];
                } catch (error) {
                    console.error(`Error loading images for prompt ${promptId}:`, error);
                    return [];
                }
            }

            createFilmStrip(images, promptId, maxThumbnails = 5) {
                if (!images || images.length === 0) {
                    return `<div class="film-strip-empty">No images</div>`;
                }

                const displayImages = images.slice(0, maxThumbnails);
                const remaining = images.length - maxThumbnails;

                let html = `<div class="prompt-film-strip film-strip-size-medium" data-prompt-id="${promptId}">`;

                displayImages.forEach((image, index) => {
                    const imageUrl = this.getImageUrl(image);
                    const thumbnailUrl = this.getThumbnailUrl(image);

                    if (!thumbnailUrl) {
                        // Skip images with no valid URL
                        return;
                    }

                    html += `
                        <div class="film-strip-thumbnail"
                             data-index="${index}"
                             data-prompt-id="${promptId}"
                             onclick="window.admin.openFilmStripViewer(${promptId}, ${index})">
                            <img src="${thumbnailUrl}"
                                 alt="Image ${index + 1}"
                                 loading="lazy"
                                 onerror="this.src='${imageUrl}'; this.onerror=null;">
                        </div>
                    `;
                });

                if (remaining > 0) {
                    html += `
                        <div class="film-strip-thumbnail film-strip-thumbnail--more"
                             onclick="window.admin.viewGallery(${promptId})">
                            +${remaining}
                        </div>
                    `;
                }

                html += `</div>`;
                return html;
            }

            async openFilmStripViewer(promptId, startIndex = 0) {
                // Use the existing gallery function to open the viewer
                // Store images temporarily for the viewer
                const images = await this.loadFilmStripImages(promptId);
                if (images.length === 0) {
                    this.showNotification('No images found for this prompt', 'warning');
                    return;
                }

                // Store for viewer
                this.filmStripImages = images;
                this.filmStripCurrentPrompt = promptId;

                // Create a temporary container for ViewerJS
                const container = document.createElement('div');
                container.id = 'filmStripViewerContainer';
                container.style.display = 'none';

                images.forEach((image, index) => {
                    const imageUrl = this.getImageUrl(image);
                    if (!imageUrl) return;

                    const img = document.createElement('img');
                    img.src = imageUrl;
                    img.alt = image.filename || `Image ${index + 1}`;
                    img.dataset.caption = this.formatFilmStripCaption(image);
                    container.appendChild(img);
                });

                document.body.appendChild(container);

                // Initialize ViewerJS
                const viewer = new Viewer(container, {
                    inline: false,
                    navbar: true,
                    toolbar: {
                        zoomIn: 1,
                        zoomOut: 1,
                        oneToOne: 1,
                        reset: 1,
                        prev: 1,
                        play: false,
                        next: 1,
                        rotateLeft: 1,
                        rotateRight: 1,
                        flipHorizontal: 1,
                        flipVertical: 1
                    },
                    title: [1, (image, imageData) => image.alt || 'Image'],
                    hidden: () => {
                        viewer.destroy();
                        container.remove();
                    },
                    initialViewIndex: startIndex
                });

                viewer.show();
            }

            formatFilmStripCaption(image) {
                const parts = [];
                if (image.filename) parts.push(image.filename);
                if (image.width && image.height) parts.push(`${image.width}x${image.height}`);
                return parts.join(' | ') || 'Image';
            }

            async renderFilmStripForPrompt(promptId, container) {
                const images = await this.loadFilmStripImages(promptId);
                container.innerHTML = this.createFilmStrip(images, promptId);
            }

            // ==================== End Film Strip Functions ====================

            async showAutoTagModal() {
                this.showModal("autoTagModal");
                await this.checkAutoTagModels();
            }

            _setModelStatus(el, downloaded, modelKey) {
                el.textContent = '';
                if (downloaded) {
                    const span = document.createElement('span');
                    span.className = 'text-pm-success text-sm';
                    span.textContent = '\u2713 Downloaded';
                    el.appendChild(span);
                } else {
                    const btn = document.createElement('button');
                    btn.className = 'px-3 py-1 bg-pm-accent hover:bg-pm-accent-hover text-pm text-xs rounded transition-colors';
                    btn.textContent = 'Download';
                    btn.addEventListener('click', () => window.admin.downloadModel(modelKey));
                    el.appendChild(btn);
                }
            }

            async checkAutoTagModels() {
                const ggufStatus = document.getElementById("ggufModelStatus");
                const hfStatus = document.getElementById("hfModelStatus");
                const wd14SwinV2Status = document.getElementById("wd14SwinV2ModelStatus");
                const wd14VitStatus = document.getElementById("wd14VitModelStatus");

                const statusEls = [ggufStatus, hfStatus, wd14SwinV2Status, wd14VitStatus];
                statusEls.forEach(el => { el.textContent = 'Checking...'; el.className = 'flex items-center gap-1.5'; });

                try {
                    const response = await fetch('/prompt_manager/autotag/models');
                    const data = await response.json();

                    if (data.success) {
                        this._setModelStatus(ggufStatus, data.models.gguf.downloaded, 'gguf');
                        this._setModelStatus(hfStatus, data.models.hf.downloaded, 'hf');
                        this._setModelStatus(wd14SwinV2Status, data.models['wd14-swinv2']?.downloaded, 'wd14-swinv2');
                        this._setModelStatus(wd14VitStatus, data.models['wd14-vit']?.downloaded, 'wd14-vit');

                        // Update WD14 threshold defaults from server
                        if (data.wd14_general_threshold !== undefined) {
                            const genSlider = document.getElementById('wd14GeneralThreshold');
                            genSlider.value = data.wd14_general_threshold;
                            document.getElementById('wd14GeneralThresholdValue').textContent = parseFloat(data.wd14_general_threshold).toFixed(2);
                        }
                        if (data.wd14_character_threshold !== undefined) {
                            const charSlider = document.getElementById('wd14CharacterThreshold');
                            charSlider.value = data.wd14_character_threshold;
                            document.getElementById('wd14CharacterThresholdValue').textContent = parseFloat(data.wd14_character_threshold).toFixed(2);
                        }
                    } else {
                        statusEls.forEach(el => { el.textContent = 'Error'; });
                    }
                } catch (error) {
                    console.error('Error checking models:', error);
                    statusEls.forEach(el => { el.textContent = 'Error'; });
                }
            }

            async downloadModel(modelType) {
                const modelNames = {
                    'gguf': 'GGUF Model',
                    'hf': 'HuggingFace Model',
                    'wd14-swinv2': 'WD14 SwinV2',
                    'wd14-vit': 'WD14 ViT'
                };
                const modelName = modelNames[modelType] || modelType;
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

            async startAutoTag() {
                const modelType = document.querySelector('input[name="autoTagModel"]:checked').value;
                const prompt = document.getElementById('autoTagPrompt').value;
                const skipTagged = document.querySelector('input[name="autoTagMode"]:checked').value === 'skip';

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
                    formData.append('skip_tagged', skipTagged ? 'true' : 'false');

                    if (modelType.startsWith('wd14')) {
                        formData.append('general_threshold', document.getElementById('wd14GeneralThreshold').value);
                        formData.append('character_threshold', document.getElementById('wd14CharacterThreshold').value);
                    } else {
                        formData.append('prompt', prompt);
                    }

                    this.autoTagState.eventSource = new EventSource(`/prompt_manager/autotag/start?${formData.toString()}`);

                    this.autoTagState.eventSource.onmessage = (event) => {
                        const data = JSON.parse(event.data);

                        if (data.type === 'progress') {
                            document.getElementById('autoTagCurrentFile').textContent = data.status || 'Processing...';
                            document.getElementById('autoTagProgressPercent').textContent = `${data.progress || 0}%`;
                            document.getElementById('autoTagProgressBar').style.width = `${data.progress || 0}%`;
                            if (data.processed !== undefined) {
                                document.getElementById('autoTagProcessed').textContent = data.processed;
                                document.getElementById('autoTagApplied').textContent = data.tagged || 0;
                                document.getElementById('autoTagSkipped').textContent = data.skipped || 0;
                            }
                        } else if (data.type === 'complete') {
                            this.autoTagState.eventSource.close();
                            this.hideModal("autoTagProgressModal");
                            this.showNotification(`Auto tagging complete! Applied tags to ${data.tagged || 0} prompts.`, 'success');
                            this.search(); // Refresh the prompt list
                        } else if (data.type === 'error') {
                            this.autoTagState.eventSource.close();
                            this.hideModal("autoTagProgressModal");
                            this.showNotification(`Auto tag error: ${data.message}`, 'error');
                        } else if (data.type === 'cancelled') {
                            this.autoTagState.eventSource.close();
                            this.hideModal("autoTagProgressModal");
                            this.showNotification('Auto tagging cancelled', 'info');
                        }
                    };

                    this.autoTagState.eventSource.onerror = (error) => {
                        console.error('AutoTag SSE error:', error);
                        console.error('EventSource readyState:', this.autoTagState.eventSource.readyState);
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

                // Show loading modal while we fetch images and load model
                this.showModal("autoTagLoadingModal");
                document.getElementById('autoTagLoadingStatus').textContent = 'Fetching images from database...';

                // Get ALL images with linked prompts from the database
                try {
                    const scanResponse = await fetch('/prompt_manager/images/all');
                    const scanData = await scanResponse.json();

                    if (!scanData.success || !scanData.images || scanData.images.length === 0) {
                        this.hideModal("autoTagLoadingModal");
                        this.showNotification('No images with linked prompts found in database', 'warning');
                        return;
                    }

                    this.autoTagState.reviewImages = scanData.images;
                    this.autoTagState.reviewIndex = 0;
                    this.autoTagState.modelType = modelType;
                    this.autoTagState.prompt = document.getElementById('autoTagPrompt').value;
                    this.autoTagState.generalThreshold = parseFloat(document.getElementById('wd14GeneralThreshold').value);
                    this.autoTagState.characterThreshold = parseFloat(document.getElementById('wd14CharacterThreshold').value);
                    this.autoTagState.modelLoaded = false;
                    this.autoTagState.skipAllTagged = false;  // Reset skip flag for new session

                    document.getElementById('reviewTotalCount').textContent = scanData.images.length;

                    // Update loading status - model will load on first image
                    document.getElementById('autoTagLoadingStatus').textContent =
                        `Found ${scanData.images.length} images. Loading ${modelType.toUpperCase()} model...`;

                    // Load first image (this will load the model)
                    await this.loadNextReviewImage();

                    // Hide loading, show review modal
                    this.hideModal("autoTagLoadingModal");
                    this.showModal("autoTagReviewModal");
                } catch (error) {
                    console.error('Error starting review:', error);
                    this.hideModal("autoTagLoadingModal");
                    this.showNotification('Failed to start review mode', 'error');
                }
            }

            // Check if tags array has "real" tags (excluding auto-scanned and prepend:* metadata)
            getRealTags(tags) {
                if (!tags || !Array.isArray(tags)) return [];
                return tags.filter(tag => tag !== 'auto-scanned' && !tag.startsWith('prepend:'));
            }

            // Show the re-tag confirmation modal
            async showRetagConfirmation(image, imageUrl, realTags) {
                return new Promise((resolve) => {
                    this.autoTagState.retagResolve = resolve;

                    // Set up the modal content
                    document.getElementById('retagPreviewImage').src = imageUrl;
                    document.getElementById('retagImageName').textContent = image.image_path.split('/').pop();

                    // Display existing tags
                    const tagsContainer = document.getElementById('retagExistingTags');
                    tagsContainer.innerHTML = realTags.map(tag =>
                        `<span class="px-2 py-1 bg-pm-accent text-pm text-xs rounded">${this.escapeHtml(tag)}</span>`
                    ).join('');

                    // Show modal
                    this.showModal('retagConfirmModal');
                });
            }

            // Handle user's choice in re-tag modal
            handleRetagChoice(choice) {
                this.hideModal('retagConfirmModal');

                if (choice === 'skipAll') {
                    this.autoTagState.skipAllTagged = true;
                }

                if (this.autoTagState.retagResolve) {
                    this.autoTagState.retagResolve(choice);
                    this.autoTagState.retagResolve = null;
                }
            }

            async loadNextReviewImage() {
                if (this.autoTagState.reviewIndex >= this.autoTagState.reviewImages.length) {
                    this.hideModal("autoTagReviewModal");
                    this.showNotification('Review complete!', 'success');
                    this.search();
                    return;
                }

                const image = this.autoTagState.reviewImages[this.autoTagState.reviewIndex];
                document.getElementById('reviewCurrentIndex').textContent = this.autoTagState.reviewIndex + 1;

                // Build image URL from image_path (database field)
                const imagePath = image.image_path;
                const filename = imagePath.split('/').pop();
                // Use the serve endpoint with relative path
                const relPath = imagePath.includes('/output/') ?
                    imagePath.substring(imagePath.indexOf('/output/') + 8) : filename;
                const imageUrl = `/prompt_manager/images/serve/${relPath}`;

                // Check if image already has real tags (excluding auto-scanned)
                const realTags = this.getRealTags(image.prompt_tags);

                if (realTags.length > 0) {
                    // Skip if "Skip All Tagged" was selected
                    if (this.autoTagState.skipAllTagged) {
                        this.autoTagState.reviewIndex++;
                        return this.loadNextReviewImage();
                    }

                    // Show confirmation modal
                    const choice = await this.showRetagConfirmation(image, imageUrl, realTags);

                    if (choice === 'skip' || choice === 'skipAll') {
                        this.autoTagState.reviewIndex++;
                        return this.loadNextReviewImage();
                    }
                    // choice === 'retag' - continue with tagging
                }

                document.getElementById('reviewImage').src = imageUrl;
                document.getElementById('reviewImageName').textContent = filename || 'Unknown';
                document.getElementById('reviewTagsVisible').innerHTML = '<div class="text-pm-secondary">Generating tags...</div>';
                document.getElementById('reviewTagsHidden').innerHTML = '';
                document.getElementById('reviewTagsAccordion').classList.add('hidden');
                document.getElementById('reviewTagsToggle').classList.add('hidden');

                // Store prompt_id from database for applying tags later
                this.autoTagState.currentPromptId = image.prompt_id;
                this.autoTagState.tagsExpanded = false;  // Reset accordion for new image

                try {
                    const requestBody = {
                        image_path: image.image_path,
                        model_type: this.autoTagState.modelType,
                    };
                    if (this.autoTagState.modelType.startsWith('wd14')) {
                        requestBody.general_threshold = this.autoTagState.generalThreshold;
                        requestBody.character_threshold = this.autoTagState.characterThreshold;
                    } else {
                        requestBody.prompt = this.autoTagState.prompt;
                    }

                    const response = await fetch('/prompt_manager/autotag/single', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(requestBody)
                    });

                    const data = await response.json();

                    if (data.success) {
                        this.autoTagState.currentTags = data.tags;
                        // Use prompt_id from database, fallback to API response
                        this.autoTagState.currentPromptId = image.prompt_id || data.prompt_id;
                        this.renderReviewTags();
                    } else {
                        document.getElementById('reviewTagsVisible').innerHTML =
                            `<div class="text-pm-error">Error: ${data.error}</div>`;
                    }
                } catch (error) {
                    console.error('Error generating tags:', error);
                    document.getElementById('reviewTagsVisible').innerHTML =
                        '<div class="text-pm-error">Failed to generate tags</div>';
                }
            }

            renderReviewTags() {
                const visibleContainer = document.getElementById('reviewTagsVisible');
                const hiddenContainer = document.getElementById('reviewTagsHidden');
                const accordion = document.getElementById('reviewTagsAccordion');
                const toggleBtn = document.getElementById('reviewTagsToggle');
                const toggleIcon = document.getElementById('reviewTagsToggleIcon');
                const toggleText = document.getElementById('reviewTagsToggleText');
                const countSpan = document.getElementById('reviewTagsCount');

                if (this.autoTagState.currentTags.length === 0) {
                    visibleContainer.innerHTML = '<div class="text-pm-secondary">No tags generated</div>';
                    hiddenContainer.innerHTML = '';
                    accordion.classList.add('hidden');
                    toggleBtn.classList.add('hidden');
                    return;
                }

                // Show first ~10 tags in visible row, rest in accordion
                const visibleCount = Math.min(10, this.autoTagState.currentTags.length);
                const visibleTags = this.autoTagState.currentTags.slice(0, visibleCount);
                const hiddenTags = this.autoTagState.currentTags.slice(visibleCount);

                const createTagChip = (tag, index) => `
                    <span class="tag-chip">
                        ${this.escapeHtml(tag)}
                        <span class="tag-remove" onclick="window.admin.removeReviewTag(${index})">×</span>
                    </span>
                `;

                visibleContainer.innerHTML = visibleTags.map((tag, i) => createTagChip(tag, i)).join('');

                if (hiddenTags.length > 0) {
                    hiddenContainer.innerHTML = hiddenTags.map((tag, i) => createTagChip(tag, visibleCount + i)).join('');
                    toggleBtn.classList.remove('hidden');
                    countSpan.textContent = `(${hiddenTags.length} more)`;

                    // Reset accordion state
                    if (!this.autoTagState.tagsExpanded) {
                        accordion.classList.add('hidden');
                        toggleIcon.textContent = '▼';
                        toggleText.textContent = 'Show all tags';
                    }
                } else {
                    hiddenContainer.innerHTML = '';
                    accordion.classList.add('hidden');
                    toggleBtn.classList.add('hidden');
                }
            }

            toggleReviewTags() {
                const accordion = document.getElementById('reviewTagsAccordion');
                const toggleIcon = document.getElementById('reviewTagsToggleIcon');
                const toggleText = document.getElementById('reviewTagsToggleText');

                this.autoTagState.tagsExpanded = !this.autoTagState.tagsExpanded;

                if (this.autoTagState.tagsExpanded) {
                    accordion.classList.remove('hidden');
                    toggleIcon.textContent = '▲';
                    toggleText.textContent = 'Show less';
                } else {
                    accordion.classList.add('hidden');
                    toggleIcon.textContent = '▼';
                    toggleText.textContent = 'Show all tags';
                }
            }

            toggleMainTags(promptId) {
                const container = document.querySelector(`.tags-accordion[data-prompt-id="${promptId}"]`);
                if (!container) return;

                const hiddenSection = container.querySelector('.tags-hidden');
                const toggleBtn = container.querySelector('.tags-toggle-btn');
                if (!hiddenSection || !toggleBtn) return;

                const toggleIcon = toggleBtn.querySelector('.toggle-icon');
                const toggleText = toggleBtn.querySelector('.toggle-text');
                const isHidden = hiddenSection.classList.contains('hidden');

                if (isHidden) {
                    hiddenSection.classList.remove('hidden');
                    toggleIcon.textContent = '▲';
                    toggleText.textContent = 'Show less';
                } else {
                    hiddenSection.classList.add('hidden');
                    toggleIcon.textContent = '▼';
                    // Recalculate the count from the hidden tags
                    const hiddenTags = hiddenSection.querySelectorAll('span.inline-flex').length;
                    toggleText.textContent = `Show ${hiddenTags} more tags`;
                }
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

                const promptId = this.autoTagState.currentPromptId;

                if (!promptId) {
                    this.showNotification('No linked prompt found for this image', 'warning');
                    this.skipReviewImage();
                    return;
                }

                try {
                    const response = await fetch('/prompt_manager/autotag/apply', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            prompt_id: promptId,
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
                this.autoTagState.skipAllTagged = false;
                this.hideModal("autoTagReviewModal");
            }
        }

        // Initialize the admin interface
        const admin = new PromptAdmin();
        window.admin = admin;

        // Add event listeners for prompt selection
        document.addEventListener("change", function (e) {
            if (e.target.classList.contains("prompt-checkbox")) {
                const promptId = parseInt(e.target.dataset.id);
                if (e.target.checked) {
                    admin.selectedPrompts.add(promptId);
                } else {
                    admin.selectedPrompts.delete(promptId);
                }
                admin.updateBulkActionButtons();

                const allCheckboxes = document.querySelectorAll(".prompt-checkbox");
                const checkedCheckboxes = document.querySelectorAll(".prompt-checkbox:checked");
                const selectAllCheckbox = document.getElementById("selectAll");
                selectAllCheckbox.checked = allCheckboxes.length === checkedCheckboxes.length;
                selectAllCheckbox.indeterminate = checkedCheckboxes.length > 0 && checkedCheckboxes.length < allCheckboxes.length;
            }
        });

        // Keyboard shortcuts for modals
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') {
                // Close any open modals
                if (document.getElementById('galleryModal') && !document.getElementById('galleryModal').classList.contains('hidden')) {
                    admin.closeGallery();
                } else if (document.getElementById('imageViewerModal') && !document.getElementById('imageViewerModal').classList.contains('hidden')) {
                    admin.closeImageViewer();
                }
            } else if (document.getElementById('imageViewerModal') && !document.getElementById('imageViewerModal').classList.contains('hidden')) {
                // Handle arrow keys in image viewer
                if (e.key === 'ArrowLeft') {
                    e.preventDefault();
                    admin.previousImage();
                } else if (e.key === 'ArrowRight') {
                    e.preventDefault();
                    admin.nextImage();
                }
            }
        });

        // Window resize listener for responsive image sizing
        window.addEventListener('resize', function() {
            // Only apply resize adjustments if image viewer is open and in fit mode
            if (document.getElementById('imageViewerModal') && !document.getElementById('imageViewerModal').classList.contains('hidden') && admin.imageViewMode === 'fit') {
                admin.applyImageSizing();
            }
        });
