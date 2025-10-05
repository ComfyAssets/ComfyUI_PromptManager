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
        this.modal = null;
        this.isGenerating = false;
        this.currentProgress = null;
        this.missingCount = 0;
        this.selectedSizes = ['small', 'medium', 'large'];
        this.progressUnsubscribe = null;
        this.currentTaskId = null;

        this.init();
    }

    init() {
        console.log('ThumbnailModal: Initializing...');
        this.createModal();
        this.attachEventListeners();

        // Check for scan results from index.html stored in sessionStorage
        const scanResult = sessionStorage.getItem('thumbnailScanResult');
        console.log('ThumbnailModal: Checking sessionStorage for scan result:', scanResult);

        if (scanResult) {
            try {
                const result = JSON.parse(scanResult);

                // Clear the scan result so it doesn't show again on refresh
                sessionStorage.removeItem('thumbnailScanResult');

                // Show modal if there are ANY missing thumbnails
                if (result.missing_count > 0) {
                    console.log('ThumbnailModal: Found', result.missing_count, 'missing thumbnails from scan');
                    this.missingCount = result.missing_count;
                    this.showMissingThumbnailsPrompt();
                } else {
                    console.log('ThumbnailModal: No missing thumbnails found');
                }
            } catch (error) {
                console.error('Failed to parse thumbnail scan result:', error);
            }
        }
    }

    createModal() {
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
                                        <label class="checkbox-label">
                                            <input type="checkbox" name="thumbSize" value="small" checked>
                                            <span>Small (150x150)</span>
                                        </label>
                                        <label class="checkbox-label">
                                            <input type="checkbox" name="thumbSize" value="medium" checked>
                                            <span>Medium (300x300)</span>
                                        </label>
                                        <label class="checkbox-label">
                                            <input type="checkbox" name="thumbSize" value="large" checked>
                                            <span>Large (600x600)</span>
                                        </label>
                                        <label class="checkbox-label">
                                            <input type="checkbox" name="thumbSize" value="xlarge">
                                            <span>Extra Large (1200x1200)</span>
                                        </label>
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
                    sizes: ['small', 'medium', 'large']
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
        this.modal.style.display = 'flex';

        // Hide scan view, show detected view
        this.modal.querySelector('#thumbnailScanView').style.display = 'none';
        this.modal.querySelector('#thumbnailDetectedView').style.display = 'block';

        // Update count
        this.modal.querySelector('#missingCount').textContent = this.missingCount;

        // Show appropriate buttons
        this.modal.querySelector('#btnCancel').style.display = 'none';
        this.modal.querySelector('#btnGenerate').style.display = 'inline-block';
        this.modal.querySelector('#btnSkip').style.display = 'inline-block';

        // Calculate estimate
        this.updateEstimate();
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
        this.isGenerating = true;

        // Update UI
        this.modal.querySelector('#thumbnailDetectedView').style.display = 'none';
        this.modal.querySelector('#thumbnailProgressView').style.display = 'block';
        this.modal.querySelector('#btnGenerate').style.display = 'none';
        this.modal.querySelector('#btnSkip').style.display = 'none';
        this.modal.querySelector('#btnCancel').style.display = 'inline-block';
        this.modal.querySelector('#btnCancel').disabled = false;

        // Initialize progress
        this.modal.querySelector('#progressTotal').textContent = this.missingCount;

        try {
            const response = await fetch('/api/v1/thumbnails/generate', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    sizes: this.selectedSizes
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
            console.warn('EventBus unavailable; thumbnail progress updates disabled');
            return;
        }

        if (this.progressUnsubscribe) {
            this.progressUnsubscribe();
            this.progressUnsubscribe = null;
        }

        this.progressUnsubscribe = EventBus.on('sse:progress', (event) => {
            if (!event || event.operation !== 'thumbnails') {
                return;
            }

            if (this.currentTaskId && event.task_id && event.task_id !== this.currentTaskId) {
                return;
            }

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
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            console.log('ThumbnailModal: DOMContentLoaded - Creating instance');
            window.thumbnailModal = new ThumbnailModal();
        });
    } else {
        console.log('ThumbnailModal: DOM already loaded - Creating instance');
        window.thumbnailModal = new ThumbnailModal();
    }
} else {
    console.info('ThumbnailModal: Skipping initialization outside PromptManager UI context');
}
