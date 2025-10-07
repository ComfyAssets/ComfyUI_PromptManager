/**
 * Thumbnail Rebuild Modal V2
 *
 * Unified modal for comprehensive thumbnail scanning and rebuilding.
 * Features:
 * - 4-state workflow: SCANNING → OPTIONS → PROCESSING → SUMMARY
 * - Comprehensive validation (DB + disk)
 * - Auto-fix for broken links and orphaned files
 * - Real-time progress tracking
 * - Professional UX with no alerts
 */

const ThumbnailRebuildModalV2 = (function() {
    'use strict';

    const STATES = {
        SCANNING: 'scanning',
        OPTIONS: 'options',
        PROCESSING: 'processing',
        SUMMARY: 'summary'
    };

    class Modal {
        constructor() {
            this.state = STATES.SCANNING;
            this.scanResults = null;
            this.taskId = null;
            this.pollingInterval = null;
            this.modal = null;
            this.startTime = null;
            this.summaryStats = null;
            this.selectedSizes = ['small', 'medium', 'large', 'xlarge']; // Default sizes

            this.init();
        }

        init() {
            this.createModal();
            this.attachEventListeners();
        }



        createModal() {
            const modalHTML = `
                <div id="thumbnailRebuildModalV2" class="modal-overlay thumbnail-rebuild-v2" style="display: none;">
                    <div class="modal modal-large">
                        <div class="modal-header">
                            <h2 class="modal-title">
                                <i class="fas fa-sync-alt"></i>
                                <span id="modalTitle">Rebuild Thumbnails</span>
                            </h2>
                            <button class="modal-close" data-action="close">
                                <i class="fas fa-times"></i>
                            </button>
                        </div>

                        <div class="modal-body" id="modalBody">
                            <!-- Dynamic content based on state -->
                        </div>

                        <div class="modal-footer" id="modalFooter">
                            <!-- Dynamic buttons based on state -->
                        </div>
                    </div>
                </div>
            `;

            document.body.insertAdjacentHTML('beforeend', modalHTML);
            this.modal = document.getElementById('thumbnailRebuildModalV2');
        }

        attachEventListeners() {
            // Close button
            this.modal.querySelector('.modal-close').addEventListener('click', () => {
                if (!this.isProcessing()) {
                    this.close();
                }
            });

            // Close on overlay click (only if not processing)
            this.modal.addEventListener('click', (e) => {
                if (e.target === this.modal && !this.isProcessing()) {
                    this.close();
                }
            });
        }

        async open(sizes = ['small', 'medium', 'large', 'xlarge']) {
            this.selectedSizes = sizes;
            this.modal.style.display = 'flex';
            this.setState(STATES.SCANNING);
            await this.startScan();
        }

        // Alias for backwards compatibility
        async show(sizes) {
            return this.open(sizes);
        }

        setState(newState) {
            this.state = newState;
            this.render();
        }

        isProcessing() {
            return this.state === STATES.SCANNING || this.state === STATES.PROCESSING;
        }

        render() {
            const body = this.modal.querySelector('#modalBody');
            const footer = this.modal.querySelector('#modalFooter');

            switch(this.state) {
                case STATES.SCANNING:
                    body.innerHTML = this.renderScanning();
                    footer.innerHTML = '<button class="btn btn-secondary" data-action="cancel">Cancel</button>';
                    break;

                case STATES.OPTIONS:
                    body.innerHTML = this.renderOptions();
                    footer.innerHTML = `
                        <button class="btn btn-secondary" data-action="cancel">Cancel</button>
                        <button class="btn btn-primary" data-action="start-rebuild">
                            <i class="fas fa-sync-alt"></i> Start Rebuild
                        </button>
                    `;
                    break;

                case STATES.PROCESSING:
                    body.innerHTML = this.renderProcessing();
                    footer.innerHTML = '<button class="btn btn-secondary" data-action="cancel">Cancel</button>';
                    this.startTime = Date.now();
                    this.updateElapsedTime();
                    break;

                case STATES.SUMMARY:
                    body.innerHTML = this.renderSummary();
                    footer.innerHTML = '<button class="btn btn-success" data-action="close">Done</button>';
                    break;
            }

            this.attachStateEventListeners();
        }

        attachStateEventListeners() {
            // Cancel button
            const cancelBtn = this.modal.querySelector('[data-action="cancel"]');
            if (cancelBtn) {
                cancelBtn.addEventListener('click', () => this.cancel());
            }

            // Close button
            const closeBtn = this.modal.querySelector('[data-action="close"]');
            if (closeBtn) {
                closeBtn.addEventListener('click', () => this.close());
            }

            // Start rebuild button
            const startBtn = this.modal.querySelector('[data-action="start-rebuild"]');
            if (startBtn) {
                startBtn.addEventListener('click', () => this.startRebuild());
            }

            // Strategy radio buttons
            const strategyRadios = this.modal.querySelectorAll('input[name="strategy"]');
            strategyRadios.forEach(radio => {
                radio.addEventListener('change', () => this.onStrategyChange());
            });

            // Review orphans button
            const reviewBtn = this.modal.querySelector('[data-action="review-orphans"]');
            if (reviewBtn) {
                reviewBtn.addEventListener('click', () => this.showOrphansDialog());
            }

            // View errors button
            const errorsBtn = this.modal.querySelector('[data-action="view-errors"]');
            if (errorsBtn) {
                errorsBtn.addEventListener('click', () => this.showErrorsDialog());
            }
        }

        renderScanning() {
            return `
                <div class="scan-view">
                    <div class="scan-header">
                        <i class="fas fa-search fa-3x text-primary"></i>
                        <h3>Scanning Thumbnail Library</h3>
                        <p class="text-muted">Validating thumbnails across database and disk...</p>
                    </div>

                    <div class="scan-phases">
                        <div class="phase-item" id="phase1">
                            <div class="phase-header">
                                <span class="phase-icon"><i class="fas fa-database"></i></span>
                                <span class="phase-title">Phase 1: Database Validation</span>
                                <span class="phase-status" id="phase1Status">Starting...</span>
                            </div>
                            <p class="phase-message" id="phase1Message"></p>
                            <div class="progress-bar">
                                <div class="progress-fill" id="phase1Progress" style="width: 0%;"></div>
                            </div>
                        </div>

                        <div class="phase-item" id="phase2">
                            <div class="phase-header">
                                <span class="phase-icon"><i class="fas fa-hdd"></i></span>
                                <span class="phase-title">Phase 2: Disk File Scan</span>
                                <span class="phase-status" id="phase2Status">Waiting...</span>
                            </div>
                            <p class="phase-message" id="phase2Message"></p>
                            <div class="progress-bar">
                                <div class="progress-fill" id="phase2Progress" style="width: 0%;"></div>
                            </div>
                        </div>

                        <div class="phase-item" id="phase3">
                            <div class="phase-header">
                                <span class="phase-icon"><i class="fas fa-link"></i></span>
                                <span class="phase-title">Phase 3: Match Orphaned Files</span>
                                <span class="phase-status" id="phase3Status">Waiting...</span>
                            </div>
                            <p class="phase-message" id="phase3Message"></p>
                            <div class="progress-bar">
                                <div class="progress-fill" id="phase3Progress" style="width: 0%;"></div>
                            </div>
                        </div>
                    </div>

                    <p class="scan-note">
                        <i class="fas fa-info-circle"></i>
                        This may take a few minutes for large libraries...
                    </p>
                </div>
            `;
        }

        renderOptions() {
            if (!this.scanResults) {
                return '<p>No scan results available</p>';
            }

            const cats = this.scanResults.categories;
            const totalOps = (cats.broken_links || 0) + (cats.linkable_orphans || 0) + (cats.missing || 0);
            const trueOrphans = this.scanResults.true_orphans || {};

            return `
                <div class="options-view">
                    <h3>📊 Scan Results</h3>

                    <div class="results-grid">
                        <div class="result-card valid">
                            <i class="fas fa-check-circle"></i>
                            <span class="result-value">${(cats.valid || 0).toLocaleString()}</span>
                            <span class="result-label">Valid Thumbnails</span>
                        </div>

                        <div class="result-card broken">
                            <i class="fas fa-unlink"></i>
                            <span class="result-value">${(cats.broken_links || 0).toLocaleString()}</span>
                            <span class="result-label">Broken Links</span>
                            <span class="result-action">Auto-fix</span>
                        </div>

                        <div class="result-card orphaned">
                            <i class="fas fa-link"></i>
                            <span class="result-value">${(cats.linkable_orphans || 0).toLocaleString()}</span>
                            <span class="result-label">Orphaned (Linkable)</span>
                            <span class="result-action">Auto-fix</span>
                        </div>

                        <div class="result-card missing">
                            <i class="fas fa-plus-circle"></i>
                            <span class="result-value">${(cats.missing || 0).toLocaleString()}</span>
                            <span class="result-label">Missing</span>
                            <span class="result-action">Generate</span>
                        </div>

                        ${(trueOrphans.count || 0) > 0 ? `
                        <div class="result-card orphans">
                            <i class="fas fa-trash-alt"></i>
                            <span class="result-value">${trueOrphans.count}</span>
                            <span class="result-label">True Orphans</span>
                            <span class="result-meta">${this.formatBytes(trueOrphans.size_bytes || 0)}</span>
                            <button class="btn-link" data-action="review-orphans">Review</button>
                        </div>
                        ` : ''}
                    </div>

                    <div class="rebuild-strategy">
                        <h4>🎯 Rebuild Strategy</h4>

                        <label class="radio-option">
                            <input type="radio" name="strategy" value="auto" checked>
                            <div class="option-content">
                                <strong>Auto-Fix Everything (Recommended)</strong>
                                <p>Fix broken links, link orphans, and generate missing thumbnails automatically</p>
                            </div>
                        </label>

                        <label class="radio-option">
                            <input type="radio" name="strategy" value="custom">
                            <div class="option-content">
                                <strong>Custom Selection</strong>
                                <p>Choose which operations to perform</p>
                            </div>
                        </label>

                        <div id="customOptions" style="display: none;">
                            <label class="checkbox-option">
                                <input type="checkbox" name="fix_broken" checked>
                                <span>Fix broken links (${cats.broken_links || 0})</span>
                            </label>
                            <label class="checkbox-option">
                                <input type="checkbox" name="link_orphans" checked>
                                <span>Link orphaned files (${cats.linkable_orphans || 0})</span>
                            </label>
                            <label class="checkbox-option">
                                <input type="checkbox" name="generate_missing" checked>
                                <span>Generate missing thumbnails (${cats.missing || 0})</span>
                            </label>
                        </div>
                    </div>

                    <div class="size-selection">
                        <h4>Thumbnail Sizes</h4>
                        <div class="size-checkboxes">
                            <label><input type="checkbox" name="size" value="small" ${this.selectedSizes.includes('small') ? 'checked' : ''}> Small (150px)</label>
                            <label><input type="checkbox" name="size" value="medium" ${this.selectedSizes.includes('medium') ? 'checked' : ''}> Medium (300px)</label>
                            <label><input type="checkbox" name="size" value="large" ${this.selectedSizes.includes('large') ? 'checked' : ''}> Large (600px)</label>
                            <label><input type="checkbox" name="size" value="xlarge" ${this.selectedSizes.includes('xlarge') ? 'checked' : ''}> X-Large (1200px)</label>
                        </div>
                    </div>

                    <div class="operation-summary">
                        <p><strong>Total Operations:</strong> <span id="totalOps">${totalOps.toLocaleString()}</span></p>
                        <p><strong>Estimated Time:</strong> <span id="estimatedTime">${this.formatTime(this.scanResults.estimated_time_seconds || 0)}</span></p>
                    </div>
                </div>
            `;
        }

        renderProcessing() {
            return `
                <div class="processing-view">
                    <h3>🔄 Rebuilding Thumbnails</h3>

                    <div class="operation-progress">
                        <div class="op-item" id="opFixing">
                            <span class="op-label">Fixing broken links</span>
                            <span class="op-status" id="opFixingStatus">Waiting...</span>
                        </div>
                        <div class="op-item" id="opLinking">
                            <span class="op-label">Linking orphaned files</span>
                            <span class="op-status" id="opLinkingStatus">Waiting...</span>
                        </div>
                        <div class="op-item" id="opGenerating">
                            <span class="op-label">Generating missing</span>
                            <span class="op-status" id="opGeneratingStatus">Waiting...</span>
                        </div>
                    </div>

                    <div class="overall-progress">
                        <h4>Overall Progress</h4>
                        <div class="progress-bar large">
                            <div class="progress-fill" id="overallProgress" style="width: 0%;"></div>
                            <span class="progress-text" id="overallPercent">0%</span>
                        </div>

                        <div class="current-file">
                            <i class="fas fa-file-image"></i>
                            <span id="currentFile">Initializing...</span>
                        </div>

                        <div class="time-stats">
                            <span>Elapsed: <strong id="elapsed">0:00</strong></span>
                            <span>Remaining: <strong id="remaining">calculating...</strong></span>
                        </div>
                    </div>

                    <div class="stats-summary">
                        <div class="stat-item">
                            <span class="stat-label">Fixed:</span>
                            <span class="stat-value" id="statFixed">0</span>
                        </div>
                        <div class="stat-item">
                            <span class="stat-label">Linked:</span>
                            <span class="stat-value" id="statLinked">0</span>
                        </div>
                        <div class="stat-item">
                            <span class="stat-label">Generated:</span>
                            <span class="stat-value" id="statGenerated">0</span>
                        </div>
                        <div class="stat-item">
                            <span class="stat-label">Failed:</span>
                            <span class="stat-value" id="statFailed">0</span>
                        </div>
                    </div>
                </div>
            `;
        }

        renderSummary() {
            const stats = this.summaryStats || {};
            const hasFailures = (stats.failed || 0) > 0;

            return `
                <div class="summary-view">
                    <div class="summary-icon ${hasFailures ? 'warning' : 'success'}">
                        <i class="fas ${hasFailures ? 'fa-exclamation-triangle' : 'fa-check-circle'} fa-4x"></i>
                    </div>

                    <h3>${hasFailures ? 'Rebuild Completed with Warnings' : 'Rebuild Complete!'}</h3>
                    <p class="summary-message">Your thumbnail library has been synchronized.</p>

                    <div class="summary-stats">
                        ${stats.fixed_links ? `<p>✓ Fixed ${stats.fixed_links.toLocaleString()} broken links</p>` : ''}
                        ${stats.linked_orphans ? `<p>✓ Linked ${stats.linked_orphans.toLocaleString()} orphaned files</p>` : ''}
                        ${stats.generated ? `<p>✓ Generated ${stats.generated.toLocaleString()} new thumbnails</p>` : ''}
                        ${stats.failed ? `<p>⚠️  ${stats.failed} files failed</p>` : ''}
                    </div>

                    <p class="summary-total">
                        Total processed: <strong>${(stats.completed || 0).toLocaleString()}</strong> operations
                        in <strong>${this.formatTime(stats.duration_seconds || 0)}</strong>
                    </p>

                    ${hasFailures && stats.errors && stats.errors.length > 0 ? `
                        <button class="btn btn-link" data-action="view-errors">
                            <i class="fas fa-list"></i> View Failed Items (${stats.errors.length})
                        </button>
                    ` : ''}
                </div>
            `;
        }

        async startScan() {
            try {
                const response = await fetch('/api/v1/thumbnails/comprehensive-scan', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        sizes: this.selectedSizes || ['small', 'medium', 'large', 'xlarge'],
                        sample_limit: 6
                    })
                });

                if (!response.ok) throw new Error('Scan failed');

                const result = await response.json();
                this.taskId = result.task_id;

                // Poll for progress
                await this.pollScanProgress();

            } catch (error) {
                console.error('Scan error:', error);
                this.showError('Failed to scan thumbnail library: ' + error.message);
            }
        }

        async pollScanProgress() {
            this.pollingInterval = setInterval(async () => {
                try {
                    const response = await fetch(`/api/v1/thumbnails/status/${this.taskId}`);
                    if (!response.ok) return;

                    const data = await response.json();

                    // Update phase progress
                    if (data.progress) {
                        this.updateScanProgress(data.progress);
                    }

                    // Check completion
                    if (data.status === 'completed') {
                        clearInterval(this.pollingInterval);
                        this.scanResults = data.result;
                        this.setState(STATES.OPTIONS);
                    } else if (data.status === 'failed') {
                        clearInterval(this.pollingInterval);
                        this.showError('Scan failed: ' + (data.error || 'Unknown error'));
                    }

                } catch (error) {
                    console.error('Progress poll error:', error);
                }
            }, 500);
        }

        updateScanProgress(progress) {
            const phase = progress.phase;
            const percentage = progress.percentage || 0;
            const message = progress.message || '';

            if (phase === 'database_validation') {
                const el = document.getElementById('phase1Progress');
                const status = document.getElementById('phase1Status');
                const msgEl = document.getElementById('phase1Message');
                if (el) el.style.width = `${percentage}%`;
                if (status) status.textContent = `${progress.current} / ${progress.total}`;
                if (msgEl && message) msgEl.textContent = message;
            } else if (phase === 'disk_scan') {
                const el = document.getElementById('phase2Progress');
                const status = document.getElementById('phase2Status');
                const msgEl = document.getElementById('phase2Message');
                if (el) el.style.width = `${percentage}%`;
                if (status) status.textContent = `${progress.current} / ${progress.total}`;
                if (msgEl && message) msgEl.textContent = message;
            } else if (phase === 'orphan_matching') {
                const el = document.getElementById('phase3Progress');
                const status = document.getElementById('phase3Status');
                const msgEl = document.getElementById('phase3Message');
                if (el) el.style.width = `${percentage}%`;
                if (status) status.textContent = `${progress.current} / ${progress.total}`;
                if (msgEl && message) msgEl.textContent = message;
            }
        }

        async startRebuild() {
            // Get selected strategy
            const strategy = document.querySelector('input[name="strategy"]:checked').value;

            // Get operations
            let operations;
            if (strategy === 'auto') {
                operations = {
                    fix_broken_links: true,
                    link_orphans: true,
                    generate_missing: true,
                    delete_true_orphans: false
                };
            } else {
                operations = {
                    fix_broken_links: document.querySelector('input[name="fix_broken"]').checked,
                    link_orphans: document.querySelector('input[name="link_orphans"]').checked,
                    generate_missing: document.querySelector('input[name="generate_missing"]').checked,
                    delete_true_orphans: false
                };
            }

            // Get selected sizes
            const sizeCheckboxes = document.querySelectorAll('input[name="size"]:checked');
            const sizes = Array.from(sizeCheckboxes).map(cb => cb.value);

            if (sizes.length === 0) {
                this.showError('Please select at least one thumbnail size');
                return;
            }

            try {
                const response = await fetch('/api/v1/thumbnails/rebuild-unified', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        operations: operations,
                        sizes: sizes,
                        scan_results: this.scanResults.breakdown
                    })
                });

                if (!response.ok) throw new Error('Rebuild failed');

                const result = await response.json();
                this.taskId = result.task_id;

                // Switch to processing state
                this.setState(STATES.PROCESSING);

                // Poll for progress
                await this.pollRebuildProgress();

            } catch (error) {
                console.error('Rebuild error:', error);
                this.showError('Failed to start rebuild: ' + error.message);
            }
        }

        async pollRebuildProgress() {
            this.pollingInterval = setInterval(async () => {
                try {
                    const response = await fetch(`/api/v1/thumbnails/status/${this.taskId}`);
                    if (!response.ok) return;

                    const data = await response.json();

                    // Update progress
                    if (data.progress) {
                        this.updateRebuildProgress(data.progress);
                    }

                    // Check completion
                    if (data.status === 'completed' || data.status === 'cancelled') {
                        clearInterval(this.pollingInterval);
                        this.summaryStats = data.result?.stats || {};
                        this.summaryStats.completed = data.result?.completed || 0;
                        this.summaryStats.duration_seconds = data.result?.duration_seconds || 0;
                        this.setState(STATES.SUMMARY);
                    } else if (data.status === 'failed') {
                        clearInterval(this.pollingInterval);
                        this.showError('Rebuild failed: ' + (data.error || 'Unknown error'));
                    }

                } catch (error) {
                    console.error('Progress poll error:', error);
                }
            }, 500);
        }

        updateRebuildProgress(progress) {
            const operation = progress.operation || '';
            const percentage = progress.percentage || 0;
            const stats = progress.stats || {};

            // Update operation status
            if (operation === 'fixing_broken_links') {
                const el = document.getElementById('opFixingStatus');
                if (el) el.textContent = `${stats.fixed_links || 0} fixed`;
            } else if (operation === 'linking_orphans') {
                const el = document.getElementById('opLinkingStatus');
                if (el) el.textContent = `${stats.linked_orphans || 0} linked`;
            } else if (operation === 'generating_missing') {
                const el = document.getElementById('opGeneratingStatus');
                if (el) el.textContent = `${stats.generated || 0} generated`;
            }

            // Update overall progress
            const bar = document.getElementById('overallProgress');
            const percent = document.getElementById('overallPercent');
            if (bar) bar.style.width = `${percentage}%`;
            if (percent) percent.textContent = `${Math.round(percentage)}%`;

            // Update current file
            const currentFile = document.getElementById('currentFile');
            if (currentFile && progress.current_file) {
                currentFile.textContent = progress.current_file;
            }

            // Update stats
            const statFixed = document.getElementById('statFixed');
            const statLinked = document.getElementById('statLinked');
            const statGenerated = document.getElementById('statGenerated');
            const statFailed = document.getElementById('statFailed');

            if (statFixed) statFixed.textContent = stats.fixed_links || 0;
            if (statLinked) statLinked.textContent = stats.linked_orphans || 0;
            if (statGenerated) statGenerated.textContent = stats.generated || 0;
            if (statFailed) statFailed.textContent = stats.failed || 0;
        }

        updateElapsedTime() {
            if (this.state !== STATES.PROCESSING || !this.startTime) return;

            const elapsed = Date.now() - this.startTime;
            const minutes = Math.floor(elapsed / 60000);
            const seconds = Math.floor((elapsed % 60000) / 1000);

            const elapsedEl = document.getElementById('elapsed');
            if (elapsedEl) {
                elapsedEl.textContent = `${minutes}:${seconds.toString().padStart(2, '0')}`;
            }

            setTimeout(() => this.updateElapsedTime(), 1000);
        }

        onStrategyChange() {
            const strategy = document.querySelector('input[name="strategy"]:checked').value;
            const customOptions = document.getElementById('customOptions');

            if (customOptions) {
                customOptions.style.display = strategy === 'custom' ? 'block' : 'none';
            }
        }

        showOrphansDialog() {
            const orphans = this.scanResults?.true_orphans || {};

            if (!orphans.count) return;

            const dialog = document.createElement('div');
            dialog.className = 'modal-overlay orphans-dialog';
            dialog.innerHTML = `
                <div class="modal modal-medium">
                    <div class="modal-header">
                        <h2>🗑️ True Orphans - No Parent Found</h2>
                        <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>
                    <div class="modal-body">
                        <p>These thumbnails have no matching source image (parent was likely deleted).</p>
                        <p><strong>${orphans.count} files • ${this.formatBytes(orphans.size_bytes || 0)}</strong></p>

                        <h4>Sample files:</h4>
                        <ul class="orphan-list">
                            ${(orphans.sample_files || []).slice(0, 10).map(file => `
                                <li>${file.path} (${this.formatBytes(file.file_size)})</li>
                            `).join('')}
                            ${orphans.count > 10 ? `<li>...and ${orphans.count - 10} more</li>` : ''}
                        </ul>

                        <p class="text-warning">
                            <i class="fas fa-exclamation-triangle"></i>
                            These files can be safely deleted to free up space.
                        </p>
                    </div>
                    <div class="modal-footer">
                        <button class="btn btn-secondary" onclick="this.closest('.modal-overlay').remove()">Close</button>
                    </div>
                </div>
            `;

            document.body.appendChild(dialog);
        }

        showErrorsDialog() {
            const errors = this.summaryStats?.errors || [];

            if (!errors.length) return;

            const dialog = document.createElement('div');
            dialog.className = 'modal-overlay errors-dialog';
            dialog.innerHTML = `
                <div class="modal modal-medium">
                    <div class="modal-header">
                        <h2>⚠️ Failed Items</h2>
                        <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>
                    <div class="modal-body">
                        <p><strong>${errors.length} items failed during rebuild:</strong></p>

                        <ul class="error-list">
                            ${errors.map(err => `
                                <li>
                                    <strong>${err.operation || 'Unknown'}:</strong>
                                    ${err.path || err.image_id || 'Unknown file'}<br>
                                    <span class="error-message">${err.error}</span>
                                </li>
                            `).join('')}
                        </ul>
                    </div>
                    <div class="modal-footer">
                        <button class="btn btn-secondary" onclick="this.closest('.modal-overlay').remove()">Close</button>
                    </div>
                </div>
            `;

            document.body.appendChild(dialog);
        }

        async cancel() {
            if (!this.taskId) {
                this.close();
                return;
            }

            if (this.isProcessing()) {
                // Try to cancel the task
                try {
                    await fetch('/api/v1/thumbnails/cancel', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ task_id: this.taskId })
                    });
                } catch (error) {
                    console.error('Cancel error:', error);
                }
            }

            this.close();
        }

        showError(message) {
            // Use toast if available
            if (window.ToastManager) {
                window.ToastManager.showError(message);
            } else {
                console.error('[ThumbnailRebuildV2]', message);
            }
        }

        formatBytes(bytes) {
            if (!bytes || bytes < 1024) return bytes + ' B';
            if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
            if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
            return (bytes / (1024 * 1024 * 1024)).toFixed(1) + ' GB';
        }

        formatTime(seconds) {
            if (!seconds) return '0s';
            const mins = Math.floor(seconds / 60);
            const secs = Math.floor(seconds % 60);
            if (mins === 0) return `${secs}s`;
            return `${mins}m ${secs}s`;
        }

        close() {
            if (this.pollingInterval) {
                clearInterval(this.pollingInterval);
                this.pollingInterval = null;
            }

            this.modal.style.display = 'none';
            this.state = STATES.SCANNING;
            this.scanResults = null;
            this.taskId = null;
            this.startTime = null;
            this.summaryStats = null;
        }
    }

    return Modal;
})();

// Initialize globally
if (typeof window !== 'undefined') {
    window.ThumbnailRebuildModalV2 = ThumbnailRebuildModalV2;
}
