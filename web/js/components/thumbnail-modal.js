/**
 * Thumbnail Generation Modal Component
 *
 * Provides UI for batch thumbnail generation with progress tracking
 * Integrates with the enhanced thumbnail service for missing thumbnail detection
 * and batch generation with real-time progress updates
 */

function shouldActivatePromptManagerUI() {
    if (typeof window === 'undefined') {
        return false;
    }
    const path = window.location?.pathname || '';
    return path.includes('/prompt_manager');
}

class ThumbnailModal {
    constructor() {
        // Singleton pattern - return existing instance if one exists
        if (ThumbnailModal.instance) {
            console.log('[ThumbnailModal] Singleton: Returning existing instance');
            return ThumbnailModal.instance;
        }

        console.log('[ThumbnailModal] Singleton: Creating new instance');
        this.modal = null;
        this.isGenerating = false;
        this.currentProgress = null;
        this.missingCount = 0;
        this.selectedSizes = ['small', 'medium']; // Will be updated from settings
        this.enabledSizes = ['small', 'medium']; // User's enabled sizes from settings
        this.progressUnsubscribe = null;
        this.currentTaskId = null;
        this.initialized = false;

        // Store the instance
        ThumbnailModal.instance = this;

        this.init();
    }

    async init() {
        // Guard against multiple initializations
        if (this.initialized) {
            console.log('[ThumbnailModal] Already initialized, skipping init()');
            return;
        }

        console.log('[ThumbnailModal] init() called');

        // Fetch user's enabled sizes from settings
        await this.fetchEnabledSizes();

        console.log('[ThumbnailModal] Creating modal DOM elements...');
        this.createModal();
        console.log('[ThumbnailModal] Modal created:', this.modal ? 'SUCCESS' : 'FAILED');

        console.log('[ThumbnailModal] Attaching event listeners...');
        this.attachEventListeners();
        console.log('[ThumbnailModal] Event listeners attached');

        // Check for scan results from index.html stored in sessionStorage
        console.log('[ThumbnailModal] Checking sessionStorage...');
        const scanResult = sessionStorage.getItem('thumbnailScanResult');
        console.log('[ThumbnailModal] Raw sessionStorage value:', scanResult);

        if (scanResult) {
            console.log('[ThumbnailModal] Scan result found in sessionStorage');
            try {
                const result = JSON.parse(scanResult);
                console.log('[ThumbnailModal] Parsed scan result:', result);
                console.log('[ThumbnailModal] Missing count:', result.missing_count);
                console.log('[ThumbnailModal] Scanned at:', result.scanned_at);

                // Clear the scan result so it doesn't show again on refresh
                sessionStorage.removeItem('thumbnailScanResult');
                console.log('[ThumbnailModal] Cleared sessionStorage key');

                // Show modal if there are ANY missing thumbnails
                if (result.missing_count > 0) {
                    console.log('[ThumbnailModal] Found', result.missing_count, 'missing thumbnails from scan');
                    console.log('[ThumbnailModal] Calling showMissingThumbnailsPrompt()...');
                    this.missingCount = result.missing_count;
                    this.showMissingThumbnailsPrompt();
                } else {
                    console.log('[ThumbnailModal] No missing thumbnails detected (missing_count is 0 or falsy)');
                }
            } catch (error) {
                console.error('[ThumbnailModal] Failed to parse thumbnail scan result:', error);
                console.error('[ThumbnailModal] Error stack:', error.stack);
            }
        } else {
            console.log('[ThumbnailModal] No scan result found in sessionStorage');
            console.log('[ThumbnailModal] All sessionStorage keys:', Object.keys(sessionStorage));
        }

        this.initialized = true;
        console.log('[ThumbnailModal] Initialization complete');
    }

    async fetchEnabledSizes() {
        try {
            console.log('[ThumbnailModal] Fetching enabled sizes from settings...');
            const response = await fetch('/api/v1/settings/thumbnails');
            if (response.ok) {
                const settings = await response.json();
                console.log('[ThumbnailModal] Raw API response:', settings);
                console.log('[ThumbnailModal] enabled_sizes from API:', settings.enabled_sizes);
                if (settings.enabled_sizes && settings.enabled_sizes.length > 0) {
                    this.enabledSizes = settings.enabled_sizes;
                    this.selectedSizes = settings.enabled_sizes;
                    console.log('[ThumbnailModal] Loaded enabled sizes:', this.enabledSizes);
                } else {
                    console.log('[ThumbnailModal] No enabled sizes in settings, using defaults:', this.enabledSizes);
                }
            } else {
                console.warn('[ThumbnailModal] Failed to fetch settings, using defaults');
            }
        } catch (error) {
            console.error('[ThumbnailModal] Error fetching enabled sizes:', error);
            // Keep defaults
        }
    }

    buildSizeCheckboxes() {
        const sizeLabels = {
            'small': 'Small (150x150)',
            'medium': 'Medium (300x300)',
            'large': 'Large (600x600)',
            'xlarge': 'Extra Large (1200x1200)'
        };

        return this.enabledSizes.map(size => `
            <label class="checkbox-label">
                <input type="checkbox" name="thumbSize" value="${size}" checked>
                <span>${sizeLabels[size] || size}</span>
            </label>
        `).join('');
    }

    createModal() {
        // Check if modal already exists in DOM
        const existingModal = document.getElementById('thumbnailModal');
        if (existingModal) {
            console.log('[ThumbnailModal] Modal already exists in DOM, reusing it');
            this.modal = existingModal;
            return;
        }

        const modalHTML = `
            <div id="thumbnailModal" class="modal" style="display: none;">
                <div class="modal-backdrop"></div>
                <div class="modal-content">
                    <div class="modal-header">
                        <h2 class="modal-title">
                            <i class="fas fa-images"></i>
                            Thumbnail Generation
                        </h2>
                        <button class="modal-close" data-action="close">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>

                    <div class="modal-body">
                        <!-- Initial scan view -->
                        <div id="thumbnailScanView" class="view-section">
                            <div class="scan-info">
                                <div class="scan-icon">
                                    <i class="fas fa-search fa-3x"></i>
                                </div>
                                <h3>Scanning for Missing Thumbnails</h3>
                                <p class="scan-status">Checking your image library...</p>
                                <div class="scan-progress">
                                    <div class="progress-bar">
                                        <div class="progress-fill" style="width: 0%;"></div>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <!-- Missing thumbnails detected view -->
                        <div id="thumbnailDetectedView" class="view-section" style="display: none;">
                            <div class="detection-info">
                                <div class="detection-icon">
                                    <i class="fas fa-exclamation-triangle fa-3x text-warning"></i>
                                </div>
                                <h3>Missing Thumbnails Detected</h3>
                                <p class="detection-message">
                                    Found <span id="missingCount">0</span> images without thumbnails.
                                </p>

                                <div class="thumbnail-options">
                                    <h4>Select Thumbnail Sizes to Generate:</h4>
                                    <div class="size-checkboxes">
                                        ${this.buildSizeCheckboxes()}
                                    </div>
                                </div>

                                <div class="generation-estimate">
                                    <p class="estimate-text">
                                        <i class="fas fa-clock"></i>
                                        Estimated time: <span id="estimatedTime">calculating...</span>
                                    </p>
                                </div>
                            </div>
                        </div>

                        <!-- Generation progress view -->
                        <div id="thumbnailProgressView" class="view-section" style="display: none;">
                            <div class="generation-progress">
                                <h3>Generating Thumbnails</h3>

                                <div class="progress-stats">
                                    <div class="stat-item">
                                        <span class="stat-label">Progress:</span>
                                        <span class="stat-value" id="progressPercentage">0%</span>
                                    </div>
                                    <div class="stat-item">
                                        <span class="stat-label">Completed:</span>
                                        <span class="stat-value">
                                            <span id="progressCompleted">0</span> / <span id="progressTotal">0</span>
                                        </span>
                                    </div>
                                    <div class="stat-item">
                                        <span class="stat-label">Time Remaining:</span>
                                        <span class="stat-value" id="timeRemaining">calculating...</span>
                                    </div>
                                </div>

                                <div class="progress-bar large">
                                    <div class="progress-fill" id="generationProgress" style="width: 0%;">
                                        <span class="progress-text">0%</span>
                                    </div>
                                </div>

                                <div class="current-file">
                                    <i class="fas fa-file-image"></i>
                                    <span id="currentFile">Starting...</span>
                                </div>

                                <div class="progress-details">
                                    <div class="detail-item success">
                                        <i class="fas fa-check-circle"></i>
                                        <span>Completed: <span id="detailCompleted">0</span></span>
                                    </div>
                                    <div class="detail-item error">
                                        <i class="fas fa-times-circle"></i>
                                        <span>Failed: <span id="detailFailed">0</span></span>
                                    </div>
                                    <div class="detail-item skip">
                                        <i class="fas fa-forward"></i>
                                        <span>Skipped: <span id="detailSkipped">0</span></span>
                                    </div>
                                </div>

                                <!-- Error list -->
                                <div id="errorList" class="error-list" style="display: none;">
                                    <h4>Errors:</h4>
                                    <div class="error-items"></div>
                                </div>
                            </div>
                        </div>

                        <!-- Completion view -->
                        <div id="thumbnailCompleteView" class="view-section" style="display: none;">
                            <div class="completion-info">
                                <div class="completion-icon">
                                    <i class="fas fa-check-circle fa-3x text-success"></i>
                                </div>
                                <h3>Thumbnail Generation Complete</h3>

                                <div class="completion-stats">
                                    <div class="stat-row">
                                        <span class="stat-label">Total Processed:</span>
                                        <span class="stat-value" id="completedTotal">0</span>
                                    </div>
                                    <div class="stat-row">
                                        <span class="stat-label">Successfully Generated:</span>
                                        <span class="stat-value text-success" id="completedSuccess">0</span>
                                    </div>
                                    <div class="stat-row">
                                        <span class="stat-label">Failed:</span>
                                        <span class="stat-value text-danger" id="completedFailed">0</span>
                                    </div>
                                    <div class="stat-row">
                                        <span class="stat-label">Time Taken:</span>
                                        <span class="stat-value" id="completedTime">0s</span>
                                    </div>
                                </div>

                                <div class="completion-message">
                                    <p>Thumbnails have been generated and cached for faster loading.</p>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="modal-footer">
                        <button class="btn btn-secondary" data-action="cancel" id="btnCancel">
                            Cancel
                        </button>
                        <button class="btn btn-primary" data-action="generate" id="btnGenerate" style="display: none;">
                            <i class="fas fa-play"></i>
                            Generate Thumbnails
                        </button>
                        <button class="btn btn-secondary" data-action="skip" id="btnSkip" style="display: none;">
                            Skip for Now
                        </button>
                        <button class="btn btn-success" data-action="done" id="btnDone" style="display: none;">
                            Done
                        </button>
                    </div>
                </div>
            </div>
        `;

        // Add modal to body
        document.body.insertAdjacentHTML('beforeend', modalHTML);
        this.modal = document.getElementById('thumbnailModal');
    }

    attachEventListeners() {
        // Guard against attaching listeners multiple times
        if (this.modal.dataset.listenersAttached === 'true') {
            console.log('[ThumbnailModal] Event listeners already attached, skipping');
            return;
        }

        // Modal actions
        this.modal.addEventListener('click', (e) => {
            const action = e.target.closest('[data-action]')?.dataset.action;

            switch(action) {
                case 'close':
                case 'skip':
                    this.close();
                    break;
                case 'generate':
                    this.startGeneration();
                    break;
                case 'cancel':
                    this.cancelGeneration();
                    break;
                case 'done':
                    this.close();
                    window.location.reload();
                    break;
            }
        });

        // Backdrop click
        this.modal.querySelector('.modal-backdrop').addEventListener('click', () => {
            if (!this.isGenerating) {
                this.close();
            }
        });

        // Size checkbox changes
        this.modal.addEventListener('change', (e) => {
            if (e.target.name === 'thumbSize') {
                this.updateSelectedSizes();
                this.updateEstimate();
            }
        });

        // Mark listeners as attached
        this.modal.dataset.listenersAttached = 'true';
        console.log('[ThumbnailModal] Event listeners attached successfully');
    }

    async checkMissingThumbnails() {
        console.log('ThumbnailModal: Checking for missing thumbnails...');
        try {
            const response = await fetch('/api/v1/thumbnails/scan', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    sizes: this.enabledSizes
                })
            });

            console.log('ThumbnailModal: Scan response status:', response.status);

            if (!response.ok) {
                const errorText = await response.text();
                console.error('Failed to scan for missing thumbnails:', response.status, errorText);
                return;
            }

            const data = await response.json();
            console.log('ThumbnailModal: Scan results:', data);
            this.missingCount = data.missing_count;

            // Show modal if more than 10 missing thumbnails
            if (this.missingCount > 10) {
                this.showMissingThumbnailsPrompt();
            }
        } catch (error) {
            console.error('Error checking thumbnails:', error);
        }
    }

    showMissingThumbnailsPrompt() {
        console.log('[ThumbnailModal] showMissingThumbnailsPrompt() called');
        console.log('[ThumbnailModal] Missing count:', this.missingCount);
        console.log('[ThumbnailModal] Modal element:', this.modal ? 'EXISTS' : 'MISSING');

        if (!this.modal) {
            console.error('[ThumbnailModal] Cannot show prompt - modal element is null!');
            return;
        }

        console.log('[ThumbnailModal] Setting modal display to flex...');
        this.modal.style.display = 'flex';
        console.log('[ThumbnailModal] Modal display style:', this.modal.style.display);

        // Hide scan view, show detected view
        console.log('[ThumbnailModal] Updating view visibility...');
        const scanView = this.modal.querySelector('#thumbnailScanView');
        const detectedView = this.modal.querySelector('#thumbnailDetectedView');

        console.log('[ThumbnailModal] scanView element:', scanView ? 'EXISTS' : 'MISSING');
        console.log('[ThumbnailModal] detectedView element:', detectedView ? 'EXISTS' : 'MISSING');

        if (scanView) scanView.style.display = 'none';
        if (detectedView) detectedView.style.display = 'block';

        // Update count
        const missingCountEl = this.modal.querySelector('#missingCount');
        console.log('[ThumbnailModal] missingCount element:', missingCountEl ? 'EXISTS' : 'MISSING');
        if (missingCountEl) {
            missingCountEl.textContent = this.missingCount;
            console.log('[ThumbnailModal] Updated missing count display to:', this.missingCount);
        }

        // Show appropriate buttons
        console.log('[ThumbnailModal] Updating button visibility...');
        const btnCancel = this.modal.querySelector('#btnCancel');
        const btnGenerate = this.modal.querySelector('#btnGenerate');
        const btnSkip = this.modal.querySelector('#btnSkip');

        if (btnCancel) btnCancel.style.display = 'none';
        if (btnGenerate) btnGenerate.style.display = 'inline-block';
        if (btnSkip) btnSkip.style.display = 'inline-block';

        console.log('[ThumbnailModal] Button states updated');

        // Calculate estimate
        console.log('[ThumbnailModal] Calculating time estimate...');
        this.updateEstimate();

        console.log('[ThumbnailModal] Modal should now be visible');
    }

    updateSelectedSizes() {
        const checkboxes = this.modal.querySelectorAll('input[name="thumbSize"]:checked');
        this.selectedSizes = Array.from(checkboxes).map(cb => cb.value);
    }

    updateEstimate() {
        const imagesPerSecond = 2; // Rough estimate
        const totalOperations = this.missingCount * this.selectedSizes.length;
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

    async startGeneration() {
        console.log('[ThumbnailModal] startGeneration() called');
        this.isGenerating = true;

        // Update UI
        console.log('[ThumbnailModal] Switching to progress view...');
        this.modal.querySelector('#thumbnailDetectedView').style.display = 'none';
        this.modal.querySelector('#thumbnailProgressView').style.display = 'block';
        this.modal.querySelector('#btnGenerate').style.display = 'none';
        this.modal.querySelector('#btnSkip').style.display = 'none';
        this.modal.querySelector('#btnCancel').style.display = 'inline-block';
        this.modal.querySelector('#btnCancel').disabled = false;

        // Initialize progress
        this.modal.querySelector('#progressTotal').textContent = this.missingCount;

        try {
            const response = await fetch('/api/v1/thumbnails/rebuild', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    sizes: this.selectedSizes,
                    skip_existing: true
                })
            });

            if (!response.ok) {
                throw new Error('Failed to start thumbnail generation');
            }

            const payload = await response.json();
            this.currentTaskId = payload?.task_id || null;
            this.subscribeToProgress();

        } catch (error) {
            console.error('Generation error:', error);
            this.showError('Failed to start thumbnail generation');
            this.isGenerating = false;
        }
    }

    async cancelGeneration() {
        if (!this.currentTaskId) {
            this.close();
            return;
        }

        const cancelButton = this.modal.querySelector('#btnCancel');
        cancelButton.disabled = true;

        try {
            const response = await fetch('/api/v1/thumbnails/cancel', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ task_id: this.currentTaskId })
            });

            if (!response.ok) {
                throw new Error(`Cancel failed with status ${response.status}`);
            }

            this.modal.querySelector('#currentFile').textContent = 'Cancelling…';
        } catch (error) {
            console.error('Cancel error:', error);
            cancelButton.disabled = false;
            this.showError('Failed to cancel thumbnail generation');
        }
    }

    subscribeToProgress() {
        if (!window.EventBus) {
            console.warn('[ThumbnailModal] EventBus unavailable; thumbnail progress updates disabled');
            return;
        }

        console.log('[ThumbnailModal] Subscribing to progress events...');
        console.log('[ThumbnailModal] Current task ID:', this.currentTaskId);

        if (this.progressUnsubscribe) {
            console.log('[ThumbnailModal] Cleaning up previous subscription');
            this.progressUnsubscribe();
            this.progressUnsubscribe = null;
        }

        this.progressUnsubscribe = EventBus.on('sse:progress', (event) => {
            console.log('[ThumbnailModal] Received SSE progress event:', event);

            if (!event) {
                console.log('[ThumbnailModal] Event is null or undefined, ignoring');
                return;
            }

            console.log('[ThumbnailModal] Event operation:', event.operation);
            if (event.operation !== 'thumbnail_generation') {
                console.log('[ThumbnailModal] Not a thumbnail operation, ignoring');
                return;
            }

            console.log('[ThumbnailModal] Thumbnail event received!');
            console.log('[ThumbnailModal] Event task_id:', event.task_id);
            console.log('[ThumbnailModal] Current task_id:', this.currentTaskId);

            if (this.currentTaskId && event.task_id && event.task_id !== this.currentTaskId) {
                console.log('[ThumbnailModal] Task ID mismatch, ignoring');
                return;
            }

            console.log('[ThumbnailModal] Updating progress with event data');
            this.updateProgress(event);

            const hasResult = Boolean(event.result);
            const isComplete = Number(event.progress || 0) >= 100;

            if (hasResult || isComplete) {
                const summary = hasResult
                    ? event.result
                    : { stats: event.stats || this.currentProgress };

                this.showCompletion(summary || {});

                if (this.progressUnsubscribe) {
                    this.progressUnsubscribe();
                    this.progressUnsubscribe = null;
                }
            }
        });
    }

    updateProgress(progress) {
        const stats = progress.stats || progress;
        this.currentProgress = stats;

        // Update percentage
        const percentage = Math.round(stats.percentage || progress.progress || 0);
        this.modal.querySelector('#progressPercentage').textContent = `${percentage}%`;
        this.modal.querySelector('#generationProgress').style.width = `${percentage}%`;
        this.modal.querySelector('.progress-text').textContent = `${percentage}%`;

        const totalOperations = stats.total
            || (typeof this.missingCount === 'number' && this.missingCount > 0
                ? this.missingCount * Math.max(1, this.selectedSizes.length)
                : 0);
        if (totalOperations) {
            this.modal.querySelector('#progressTotal').textContent = totalOperations;
        }

        // Update counts
        this.modal.querySelector('#progressCompleted').textContent = stats.completed;
        this.modal.querySelector('#detailCompleted').textContent = stats.completed;
        this.modal.querySelector('#detailFailed').textContent = stats.failed;
        this.modal.querySelector('#detailSkipped').textContent = stats.skipped;

        // Update current file
        if (stats.current_file || progress.message) {
            this.modal.querySelector('#currentFile').textContent = stats.current_file || progress.message;
        }

        // Update time remaining
        if (stats.estimated_time_remaining) {
            const minutes = Math.floor(stats.estimated_time_remaining / 60);
            const seconds = stats.estimated_time_remaining % 60;
            let timeStr = '';
            if (minutes > 0) {
                timeStr += `${minutes}m `;
            }
            timeStr += `${seconds}s`;
            this.modal.querySelector('#timeRemaining').textContent = timeStr;
        }

        // Show errors if any
        if (stats.errors && stats.errors.length > 0) {
            const errorList = this.modal.querySelector('#errorList');
            const errorItems = errorList.querySelector('.error-items');

            errorItems.innerHTML = stats.errors.map(err => `
                <div class="error-item">
                    <i class="fas fa-exclamation-circle"></i>
                    <span>${err.file}: ${err.error}</span>
                </div>
            `).join('');

            errorList.style.display = 'block';
        }
    }

    showCompletion(summary) {
        this.isGenerating = false;

        // Clear sessionStorage when generation completes
        sessionStorage.removeItem('thumbnailScanResult');
        console.log('ThumbnailModal: Cleared sessionStorage after generation completion');

        // Update UI
        this.modal.querySelector('#thumbnailProgressView').style.display = 'none';
        this.modal.querySelector('#thumbnailCompleteView').style.display = 'block';

        const completed = Number(summary.completed ?? summary.stats?.completed ?? 0);
        const failed = Number(summary.failed ?? summary.stats?.failed ?? 0);
        const skipped = Number(summary.skipped ?? summary.stats?.skipped ?? 0);
        const processed = Number(
            summary.processed
            ?? summary.stats?.processed
            ?? completed + failed + skipped
        );
        const total = Number(summary.total ?? summary.stats?.total ?? processed);
        const durationSeconds = Math.max(
            0,
            Math.round(
                Number(summary.duration_seconds ?? summary.duration ?? 0)
            )
        );
        const isCancelled = Boolean(summary.cancelled || summary.status === 'cancelled');

        this.modal.querySelector('#completedTotal').textContent = processed;
        this.modal.querySelector('#completedSuccess').textContent = completed;
        this.modal.querySelector('#completedFailed').textContent = failed;

        const minutes = Math.floor(durationSeconds / 60);
        const seconds = durationSeconds % 60;
        let timeStr = '';
        if (minutes > 0) {
            timeStr += `${minutes}m `;
        }
        timeStr += `${seconds}s`;
        this.modal.querySelector('#completedTime').textContent = timeStr;

        const titleEl = this.modal.querySelector('#thumbnailCompleteView .completion-info h3');
        if (titleEl) {
            titleEl.textContent = isCancelled ? 'Thumbnail Generation Cancelled' : 'Thumbnail Generation Complete';
        }

        const messageEl = this.modal.querySelector('#thumbnailCompleteView .completion-message p');
        if (messageEl) {
            messageEl.textContent = isCancelled
                ? 'Cancellation acknowledged. Any thumbnails generated before the request are still available.'
                : 'Thumbnails have been generated and cached for faster loading.';
        }

        // Show done button
        this.modal.querySelector('#btnCancel').style.display = 'none';
        this.modal.querySelector('#btnDone').style.display = 'inline-block';
    }

    showError(message) {
        // Show error notification
        if (window.NotificationService) {
            window.NotificationService.show(message, 'error');
        } else {
            alert(message);
        }
    }

    close() {
        if (this.progressUnsubscribe) {
            this.progressUnsubscribe();
            this.progressUnsubscribe = null;
        }

        this.modal.style.display = 'none';
        this.isGenerating = false;

        // Clear sessionStorage to prevent showing stale scan results
        sessionStorage.removeItem('thumbnailScanResult');
        console.log('ThumbnailModal: Cleared sessionStorage on close');

        // Reset views
        this.modal.querySelector('#thumbnailScanView').style.display = 'block';
        this.modal.querySelector('#thumbnailDetectedView').style.display = 'none';
        this.modal.querySelector('#thumbnailProgressView').style.display = 'none';
        this.modal.querySelector('#thumbnailCompleteView').style.display = 'none';
    }

    // Public method to trigger manual generation
    showManualGeneration() {
        this.modal.style.display = 'flex';

        // Start with scanning
        this.modal.querySelector('#thumbnailScanView').style.display = 'block';
        this.modal.querySelector('.scan-status').textContent = 'Scanning your image library...';

        // Perform scan
        this.checkMissingThumbnails();
    }
}

// Attach class to window for global access
if (typeof window !== 'undefined') {
    window.ThumbnailModal = ThumbnailModal;
}

if (shouldActivatePromptManagerUI()) {
    console.log('[ThumbnailModal] PromptManager UI context detected');
    console.log('[ThumbnailModal] Current path:', window.location?.pathname);
    console.log('[ThumbnailModal] Document readyState:', document.readyState);
    console.log('[ThumbnailModal] SessionStorage at load time:', sessionStorage.getItem('thumbnailScanResult'));

    if (document.readyState === 'loading') {
        console.log('[ThumbnailModal] Waiting for DOMContentLoaded...');
        document.addEventListener('DOMContentLoaded', () => {
            console.log('[ThumbnailModal] DOMContentLoaded fired - Creating instance');
            console.log('[ThumbnailModal] SessionStorage at DOMContentLoaded:', sessionStorage.getItem('thumbnailScanResult'));
            window.thumbnailModal = new ThumbnailModal();
        });
    } else {
        console.log('[ThumbnailModal] DOM already loaded - Creating instance immediately');
        console.log('[ThumbnailModal] SessionStorage before init:', sessionStorage.getItem('thumbnailScanResult'));
        window.thumbnailModal = new ThumbnailModal();
    }
} else {
    console.info('[ThumbnailModal] Skipping initialization outside PromptManager UI context');
    console.log('[ThumbnailModal] Current path:', window.location?.pathname);
}
