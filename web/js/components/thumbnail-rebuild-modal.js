/**
 * Thumbnail Rebuild Modal Component
 *
 * Provides UI for rebuilding all thumbnails with overwrite confirmation
 * Shows existing thumbnails and allows user to skip or overwrite
 */

class ThumbnailRebuildModal {
    constructor() {
        this.modal = null;
        this.isGenerating = false;
        this.existingThumbnails = [];
        this.overwriteMode = 'ask'; // 'ask', 'skip', 'overwrite'
        this.selectedSizes = ['small', 'medium', 'large'];
        this.currentTaskId = null;

        this.init();
    }

    init() {
        this.createModal();
        this.attachEventListeners();
    }

    createModal() {
        const modalHTML = `
            <div id="thumbnailRebuildModal" class="modal-overlay thumbnail-rebuild-modal" style="display: none;">
                <div class="modal">
                    <div class="modal-header">
                        <h2 class="modal-title">
                            <i class="fas fa-sync-alt"></i>
                            Rebuild All Thumbnails
                        </h2>
                        <button class="modal-close" data-action="close">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>

                    <div class="modal-body">
                        <!-- Scanning view -->
                        <div id="rebuildScanView" class="view-section">
                            <div class="scan-info">
                                <div class="scan-icon">
                                    <i class="fas fa-search fa-3x"></i>
                                </div>
                                <h3>Scanning Image Library</h3>
                                <p class="scan-status">Checking for existing thumbnails...</p>
                                <div class="scan-progress">
                                    <div class="progress-bar">
                                        <div class="progress-fill" style="width: 0%;"></div>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <!-- Confirmation view -->
                        <div id="rebuildConfirmView" class="view-section" style="display: none;">
                            <div class="rebuild-info">
                                <h3>Thumbnail Rebuild Confirmation</h3>
                                <div class="stats-grid">
                                    <div class="stat-card">
                                        <i class="fas fa-images"></i>
                                        <span class="stat-value" id="totalImages">0</span>
                                        <span class="stat-label">Total Images</span>
                                    </div>
                                    <div class="stat-card">
                                        <i class="fas fa-check-circle text-success"></i>
                                        <span class="stat-value" id="existingCount">0</span>
                                        <span class="stat-label">Existing Thumbnails</span>
                                    </div>
                                    <div class="stat-card">
                                        <i class="fas fa-plus-circle text-warning"></i>
                                        <span class="stat-value" id="missingCount">0</span>
                                        <span class="stat-label">Missing Thumbnails</span>
                                    </div>
                                </div>

                                <!-- Existing thumbnails preview -->
                                <div id="existingPreview" class="existing-preview" style="display: none;">
                                    <h4>Sample of Existing Thumbnails (will be replaced)</h4>
                                    <div class="thumbnail-grid" id="thumbnailGrid">
                                        <!-- Thumbnails will be inserted here -->
                                    </div>
                                </div>

                                <!-- Options -->
                                <div class="rebuild-options">
                                    <h4>Rebuild Options</h4>
                                    <div class="option-group">
                                        <label class="option-item">
                                            <input type="radio" name="overwriteMode" value="skip" checked>
                                            <span>Skip existing thumbnails (only generate missing)</span>
                                        </label>
                                        <label class="option-item">
                                            <input type="radio" name="overwriteMode" value="overwrite">
                                            <span>Overwrite all thumbnails (regenerate everything)</span>
                                        </label>
                                    </div>

                                    <div class="size-selection">
                                        <h4>Thumbnail Sizes</h4>
                                        <label class="checkbox-item">
                                            <input type="checkbox" name="thumbSize" value="small" checked>
                                            <span>Small (150px)</span>
                                        </label>
                                        <label class="checkbox-item">
                                            <input type="checkbox" name="thumbSize" value="medium" checked>
                                            <span>Medium (300px)</span>
                                        </label>
                                        <label class="checkbox-item">
                                            <input type="checkbox" name="thumbSize" value="large" checked>
                                            <span>Large (600px)</span>
                                        </label>
                                    </div>
                                </div>

                                <div class="estimate-info">
                                    <i class="fas fa-clock"></i>
                                    Estimated time: <span id="estimatedTime">calculating...</span>
                                </div>
                            </div>
                        </div>

                        <!-- Progress view -->
                        <div id="rebuildProgressView" class="view-section" style="display: none;">
                            <div class="progress-info">
                                <h3>Generating Thumbnails</h3>
                                <div class="progress-stats">
                                    <div class="progress-stat">
                                        <span class="stat-label">Progress:</span>
                                        <span class="stat-value">
                                            <span id="progressCurrent">0</span> / <span id="progressTotal">0</span>
                                        </span>
                                    </div>
                                    <div class="progress-stat">
                                        <span class="stat-label">Skipped:</span>
                                        <span class="stat-value" id="progressSkipped">0</span>
                                    </div>
                                    <div class="progress-stat">
                                        <span class="stat-label">Failed:</span>
                                        <span class="stat-value" id="progressFailed">0</span>
                                    </div>
                                </div>

                                <div class="progress-bar large">
                                    <div class="progress-fill" id="progressBar" style="width: 0%;"></div>
                                    <span class="progress-text" id="progressPercent">0%</span>
                                </div>

                                <div class="current-file">
                                    <i class="fas fa-file-image"></i>
                                    <span id="currentFile">Initializing...</span>
                                </div>

                                <div class="time-info">
                                    <span>Elapsed: <span id="elapsedTime">0:00</span></span>
                                    <span>Remaining: <span id="remainingTime">calculating...</span></span>
                                </div>
                            </div>
                        </div>

                        <!-- Complete view -->
                        <div id="rebuildCompleteView" class="view-section" style="display: none;">
                            <div class="complete-info">
                                <div class="complete-icon success">
                                    <i class="fas fa-check-circle fa-3x"></i>
                                </div>
                                <h3>Thumbnail Rebuild Complete</h3>
                                <div class="complete-stats">
                                    <p>Total Processed: <strong id="completeTotal">0</strong></p>
                                    <p>Successfully Generated: <strong id="completeSuccess">0</strong></p>
                                    <p>Skipped: <strong id="completeSkipped">0</strong></p>
                                    <p>Failed: <strong id="completeFailed">0</strong></p>
                                </div>
                                <p class="complete-message">
                                    Thumbnails have been rebuilt and cached for faster loading.
                                </p>
                            </div>
                        </div>
                    </div>

                    <div class="modal-footer">
                        <button class="btn btn-secondary" id="btnCancel" data-action="cancel">Cancel</button>
                        <button class="btn btn-primary" id="btnProceed" data-action="proceed" style="display: none;">
                            <i class="fas fa-sync-alt"></i>
                            Start Rebuild
                        </button>
                        <button class="btn btn-success" id="btnDone" data-action="close" style="display: none;">Done</button>
                    </div>
                </div>
                </div>
            </div>
        `;

        // Add modal to body
        document.body.insertAdjacentHTML('beforeend', modalHTML);
        this.modal = document.getElementById('thumbnailRebuildModal');
    }

    attachEventListeners() {
        // Close button
        this.modal.querySelector('.modal-close').addEventListener('click', () => this.close());

        // Action buttons
        this.modal.querySelector('#btnCancel').addEventListener('click', () => {
            if (this.isGenerating) {
                this.cancelGeneration();
            } else {
                this.close();
            }
        });

        this.modal.querySelector('#btnProceed').addEventListener('click', () => {
            this.startRebuild();
        });

        this.modal.querySelector('#btnDone').addEventListener('click', () => {
            this.close();
        });

        // Options
        this.modal.querySelectorAll('input[name="overwriteMode"]').forEach(radio => {
            radio.addEventListener('change', () => {
                this.overwriteMode = radio.value;
                this.updateEstimate();
            });
        });

        this.modal.querySelectorAll('input[name="thumbSize"]').forEach(checkbox => {
            checkbox.addEventListener('change', () => {
                this.updateSelectedSizes();
                this.updateEstimate();
            });
        });

        // Close on overlay click (outside modal)
        this.modal.addEventListener('click', (e) => {
            if (e.target === this.modal && !this.isGenerating) {
                this.close();
            }
        });
    }

    async show() {
        console.log('[ThumbnailRebuildModal] show() called');
        console.log('[ThumbnailRebuildModal] this.modal:', this.modal);

        if (!this.modal) {
            console.error('[ThumbnailRebuildModal] ERROR: this.modal is null!');
            return;
        }

        console.log('[ThumbnailRebuildModal] Setting display to flex');
        this.modal.style.display = 'flex';

        console.log('[ThumbnailRebuildModal] Calling scanImages()');
        await this.scanImages();
    }

    async scanImages() {
        // Show scan view
        this.modal.querySelector('#rebuildScanView').style.display = 'block';
        this.modal.querySelector('#rebuildConfirmView').style.display = 'none';

        try {
            // Scan ALL images to check which have thumbnails
            const response = await fetch('/api/v1/thumbnails/scan/all', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    sizes: this.selectedSizes,
                    sample_limit: 6
                })
            });

            if (!response.ok) {
                throw new Error('Failed to scan images');
            }

            const data = await response.json();

            // Process scan results
            this.processScanResults(data);

        } catch (error) {
            console.error('Scan error:', error);
            this.showError('Failed to scan image library');
        }
    }

    processScanResults(data) {
        const totalImages = data.total_images || 0;
        const existingCount = data.existing_count || 0;
        const missingCount = data.missing_count || 0;
        this.existingThumbnails = data.sample_thumbnails || [];

        // Update stats
        this.modal.querySelector('#totalImages').textContent = totalImages;
        this.modal.querySelector('#existingCount').textContent = existingCount;
        this.modal.querySelector('#missingCount').textContent = missingCount;

        // Show sample thumbnails if available
        if (this.existingThumbnails.length > 0) {
            this.showThumbnailPreview();
        }

        // Show confirmation view
        this.modal.querySelector('#rebuildScanView').style.display = 'none';
        this.modal.querySelector('#rebuildConfirmView').style.display = 'block';
        this.modal.querySelector('#btnProceed').style.display = 'inline-block';

        // Update estimate
        this.updateEstimate();
    }

    showThumbnailPreview() {
        const preview = this.modal.querySelector('#existingPreview');
        const grid = this.modal.querySelector('#thumbnailGrid');

        // Clear existing
        grid.innerHTML = '';

        // Show max 6 sample thumbnails
        const samples = this.existingThumbnails.slice(0, 6);
        samples.forEach(thumb => {
            const item = document.createElement('div');
            item.className = 'thumbnail-item';
            item.innerHTML = `
                <img src="${thumb.url}" alt="Existing thumbnail" loading="lazy">
                <div class="thumbnail-label">${thumb.name || 'Thumbnail'}</div>
            `;
            grid.appendChild(item);
        });

        preview.style.display = 'block';
    }

    updateSelectedSizes() {
        const checkboxes = this.modal.querySelectorAll('input[name="thumbSize"]:checked');
        this.selectedSizes = Array.from(checkboxes).map(cb => cb.value);
    }

    updateEstimate() {
        const totalImages = parseInt(this.modal.querySelector('#totalImages').textContent) || 0;
        const existingCount = parseInt(this.modal.querySelector('#existingCount').textContent) || 0;

        let imagesToProcess = totalImages;
        if (this.overwriteMode === 'skip') {
            imagesToProcess = totalImages - existingCount;
        }

        const imagesPerSecond = 2;
        const totalOperations = imagesToProcess * this.selectedSizes.length;
        const estimatedSeconds = Math.ceil(totalOperations / imagesPerSecond);

        const minutes = Math.floor(estimatedSeconds / 60);
        const seconds = estimatedSeconds % 60;

        let timeStr = '';
        if (minutes > 0) {
            timeStr += `${minutes} minute${minutes > 1 ? 's' : ''} `;
        }
        timeStr += `${seconds} second${seconds !== 1 ? 's' : ''}`;

        this.modal.querySelector('#estimatedTime').textContent = timeStr;
    }

    async startRebuild() {
        this.isGenerating = true;

        // Update UI
        this.modal.querySelector('#rebuildConfirmView').style.display = 'none';
        this.modal.querySelector('#rebuildProgressView').style.display = 'block';
        this.modal.querySelector('#btnProceed').style.display = 'none';
        this.modal.querySelector('#btnCancel').textContent = 'Cancel';

        // Start timer
        this.startTime = Date.now();
        this.updateTimer();

        try {
            // Use the rebuild endpoint with skip_existing parameter
            const skipExisting = this.overwriteMode === 'skip';

            const response = await fetch('/api/v1/thumbnails/rebuild', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    sizes: this.selectedSizes,
                    skip_existing: skipExisting
                })
            });

            if (!response.ok) {
                throw new Error('Failed to start rebuild');
            }

            const result = await response.json();
            this.currentTaskId = result.task_id;

            // Subscribe to progress updates
            this.subscribeToProgress();

        } catch (error) {
            console.error('Rebuild error:', error);
            this.showError('Failed to start thumbnail rebuild');
            this.isGenerating = false;
        }
    }

    updateTimer() {
        if (!this.isGenerating) return;

        const elapsed = Date.now() - this.startTime;
        const minutes = Math.floor(elapsed / 60000);
        const seconds = Math.floor((elapsed % 60000) / 1000);

        this.modal.querySelector('#elapsedTime').textContent =
            `${minutes}:${seconds.toString().padStart(2, '0')}`;

        setTimeout(() => this.updateTimer(), 1000);
    }

    subscribeToProgress() {
        // Poll for progress updates
        this.progressInterval = setInterval(async () => {
            if (!this.currentTaskId || !this.isGenerating) {
                clearInterval(this.progressInterval);
                return;
            }

            try {
                const response = await fetch(`/api/v1/thumbnails/status/${this.currentTaskId}`);
                if (!response.ok) {
                    throw new Error('Failed to get progress');
                }

                const data = await response.json();
                this.updateProgress(data);

                // Check if completed
                if (data.status === 'completed' || data.status === 'failed' || data.status === 'cancelled') {
                    clearInterval(this.progressInterval);
                    if (data.status === 'completed') {
                        this.showComplete(data.result || data.progress);
                    } else if (data.status === 'failed') {
                        this.showError(data.error || 'Generation failed');
                    } else {
                        this.showError('Generation was cancelled');
                    }
                }
            } catch (error) {
                console.error('Progress check error:', error);
            }
        }, 500); // Poll every 500ms
    }

    updateProgress(data) {
        const progress = data.progress || {};

        // Update progress bar
        const percentage = progress.percentage || 0;
        const progressBar = this.modal.querySelector('#progressBar');
        const progressPercent = this.modal.querySelector('#progressPercent');

        if (progressBar) progressBar.style.width = `${percentage}%`;
        if (progressPercent) progressPercent.textContent = `${Math.round(percentage)}%`;

        // Update stats
        const currentEl = this.modal.querySelector('#progressCurrent');
        const totalEl = this.modal.querySelector('#progressTotal');
        const skippedEl = this.modal.querySelector('#progressSkipped');
        const failedEl = this.modal.querySelector('#progressFailed');

        if (currentEl) currentEl.textContent = progress.completed || 0;
        if (totalEl) totalEl.textContent = progress.total || 0;
        if (skippedEl) skippedEl.textContent = progress.skipped || 0;
        if (failedEl) failedEl.textContent = progress.failed || 0;

        // Update current file
        const currentFileEl = this.modal.querySelector('#currentFile');
        if (currentFileEl && progress.current_file) {
            currentFileEl.textContent = progress.current_file;
        }

        // Update remaining time
        const remainingEl = this.modal.querySelector('#remainingTime');
        if (remainingEl && progress.estimated_time_remaining) {
            const minutes = Math.floor(progress.estimated_time_remaining / 60);
            const seconds = progress.estimated_time_remaining % 60;
            remainingEl.textContent = minutes > 0
                ? `${minutes}m ${seconds}s`
                : `${seconds}s`;
        }
    }

    async cancelGeneration() {
        if (!this.currentTaskId) return;

        try {
            const response = await fetch('/api/v1/thumbnails/cancel', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ task_id: this.currentTaskId })
            });

            if (response.ok) {
                this.isGenerating = false;
                clearInterval(this.progressInterval);
                this.showError('Generation cancelled by user');
            }
        } catch (error) {
            console.error('Cancel error:', error);
        }
    }

    showComplete(stats) {
        this.isGenerating = false;

        // Update complete view
        this.modal.querySelector('#completeTotal').textContent = stats.total || 0;
        this.modal.querySelector('#completeSuccess').textContent = stats.completed || 0;
        this.modal.querySelector('#completeSkipped').textContent = stats.skipped || 0;
        this.modal.querySelector('#completeFailed').textContent = stats.failed || 0;

        // Show complete view
        this.modal.querySelector('#rebuildProgressView').style.display = 'none';
        this.modal.querySelector('#rebuildCompleteView').style.display = 'block';
        this.modal.querySelector('#btnCancel').style.display = 'none';
        this.modal.querySelector('#btnDone').style.display = 'inline-block';
    }

    showError(message) {
        // Show error toast or alert
        if (window.ToastManager) {
            window.ToastManager.showError(message);
        } else {
            alert(message);
        }
    }

    close() {
        this.modal.style.display = 'none';
        this.isGenerating = false;

        // Reset state
        this.overwriteMode = 'skip';
        this.existingThumbnails = [];

        // Reset UI
        this.modal.querySelector('#rebuildScanView').style.display = 'block';
        this.modal.querySelector('#rebuildConfirmView').style.display = 'none';
        this.modal.querySelector('#rebuildProgressView').style.display = 'none';
        this.modal.querySelector('#rebuildCompleteView').style.display = 'none';
    }
}

// Initialize on page load if in prompt manager
if (typeof window !== 'undefined' && window.location.pathname.includes('/prompt_manager')) {
    window.ThumbnailRebuildModal = ThumbnailRebuildModal;
}