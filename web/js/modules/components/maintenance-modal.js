/**
 * Maintenance Modal Component
 * Handles database maintenance operations
 * @module MaintenanceModal
 */
const MaintenanceModal = (function() {
    'use strict';

    function createStub() {
        const target = {};
        const stub = new Proxy(target, {
            get: (obj, prop) => {
                if (prop in obj) {
                    return obj[prop];
                }
                return (...args) => stub;
            },
            set: (obj, prop, value) => {
                obj[prop] = value;
                return true;
            },
        });
        return stub;
    }

    if (typeof window === 'undefined') {
        return createStub();
    }
    const pmActive = (window.location?.pathname || '').includes('/prompt_manager');
    if (!pmActive) {
        console.info('[PromptManager] maintenance modal skipped outside PromptManager UI context');
        return createStub();
    }

    // Configuration
    const config = {
        modalId: 'maintenance-modal',
        statsRefreshInterval: 5000  // Refresh stats every 5 seconds when modal is open
    };

    // State
    let state = {
        isOpen: false,
        isRunning: false,
        selectedOperations: new Set(),
        statsTimer: null,
        currentStats: null
    };

    // Private methods
    function showToast(message, type = 'info') {
        // Use the project's toast system
        if (window.showToast) {
            window.showToast(message, type);
        } else if (window.notificationService && window.notificationService.show) {
            window.notificationService.show(message, type);
        } else {
            console.log(`[${type.toUpperCase()}] ${message}`);
        }
    }

    function createModalHTML() {
        return `
            <div id="${config.modalId}" class="modal maintenance-modal" style="display: none;">
                <div class="modal-backdrop"></div>
                <div class="modal-content">
                    <div class="modal-header">
                        <h2 class="modal-title">
                            <span>🔧</span>
                            Database Maintenance
                        </h2>
                        <button class="modal-close" aria-label="Close">×</button>
                    </div>

                    <div class="modal-body">
                        <!-- Statistics -->
                        <div class="maintenance-stats">
                            <h3><span>📊</span> Statistics</h3>
                            <div class="stats-grid">
                                <div class="stat-item">
                                    <div class="stat-value" id="stat-prompts">-</div>
                                    <div class="stat-label">Prompts</div>
                                </div>
                                <div class="stat-item">
                                    <div class="stat-value" id="stat-images">-</div>
                                    <div class="stat-label">Images</div>
                                </div>
                                <div class="stat-item warning" id="stat-duplicates-container">
                                    <div class="stat-value" id="stat-duplicates">-</div>
                                    <div class="stat-label">Duplicate Links</div>
                                </div>
                                <div class="stat-item error" id="stat-orphaned-container">
                                    <div class="stat-value" id="stat-orphaned">-</div>
                                    <div class="stat-label">Orphaned Images</div>
                                </div>
                                <div class="stat-item error" id="stat-missing-container">
                                    <div class="stat-value" id="stat-missing">-</div>
                                    <div class="stat-label">Missing Files</div>
                                </div>
                                <div class="stat-item">
                                    <div class="stat-value" id="stat-size">-</div>
                                    <div class="stat-label">Database Size</div>
                                </div>
                            </div>
                        </div>

                        <!-- Quick Actions -->
                        <div class="quick-actions">
                            <h3><span>⚡</span> Quick Actions</h3>
                            <div class="actions-grid">
                                <button class="action-button" data-action="deduplicate">
                                    <span class="action-icon">🔄</span>
                                    Remove Duplicates
                                </button>
                                <button class="action-button" data-action="clean-orphans">
                                    <span class="action-icon">🧹</span>
                                    Clean Orphans
                                </button>
                                <button class="action-button" data-action="validate-paths">
                                    <span class="action-icon">✔️</span>
                                    Validate Paths
                                </button>
                                <button class="action-button" data-action="fix-broken-links">
                                    <span class="action-icon">🔗</span>
                                    Fix Broken Links
                                </button>
                                <button class="action-button" data-action="optimize">
                                    <span class="action-icon">⚙️</span>
                                    Optimize Database
                                </button>
                                <button class="action-button" data-action="backup">
                                    <span class="action-icon">💾</span>
                                    Create Backup
                                </button>
                                <button class="action-button danger" data-action="remove-missing">
                                    <span class="action-icon">❌</span>
                                    Remove Missing Files
                                </button>
                            </div>
                        </div>

                        <!-- Advanced Operations -->
                        <div class="advanced-operations">
                            <h3><span>🔬</span> Advanced Operations</h3>
                            <div class="operation-list">
                                <div class="operation-item">
                                    <input type="checkbox" id="op-integrity" data-operation="check-integrity">
                                    <label for="op-integrity" class="operation-label">
                                        Full Database Integrity Check
                                        <div class="operation-description">Verify database structure and foreign key constraints</div>
                                    </label>
                                </div>
                                <div class="operation-item">
                                    <input type="checkbox" id="op-reindex" data-operation="reindex">
                                    <label for="op-reindex" class="operation-label">
                                        Rebuild All Indexes
                                        <div class="operation-description">Recreate database indexes for better performance</div>
                                    </label>
                                </div>
                                <div class="operation-item">
                                    <input type="checkbox" id="op-export" data-operation="export">
                                    <label for="op-export" class="operation-label">
                                        Export Database Backup
                                        <div class="operation-description">Create a complete backup with timestamp</div>
                                    </label>
                                </div>
                            </div>
                            <button class="run-selected-button" id="run-selected">
                                <span>▶</span>
                                Run Selected Operations
                            </button>
                        </div>

                        <!-- Progress -->
                        <div class="maintenance-progress" id="maintenance-progress">
                            <div class="progress-header">
                                <span class="progress-label">Running maintenance operations...</span>
                                <span class="progress-status" id="progress-status">0%</span>
                            </div>
                            <div class="progress-bar-container">
                                <div class="progress-bar" id="progress-bar" style="width: 0%"></div>
                            </div>
                            <div class="progress-log" id="progress-log"></div>
                        </div>
                    </div>

                    <div class="modal-footer">
                        <div class="footer-info">
                            <div class="database-size">
                                <span>📁</span>
                                <span id="footer-db-size">-</span>
                            </div>
                            <div class="last-updated">
                                Last updated: <span id="footer-last-updated">-</span>
                            </div>
                        </div>
                        <button class="btn btn-secondary" id="close-maintenance">Close</button>
                    </div>
                </div>
            </div>
        `;
    }

    function init() {
        // Add modal to DOM if not already present
        if (!document.getElementById(config.modalId)) {
            const modalDiv = document.createElement('div');
            modalDiv.innerHTML = createModalHTML();
            document.body.appendChild(modalDiv.firstElementChild);
        }

        attachEventListeners();
        return this;
    }

    function attachEventListeners() {
        const modal = document.getElementById(config.modalId);
        if (!modal) return;

        // Close button handlers
        modal.querySelector('.modal-close').addEventListener('click', close);
        modal.querySelector('#close-maintenance').addEventListener('click', close);
        modal.querySelector('.modal-backdrop').addEventListener('click', close);

        // Quick action buttons
        modal.querySelectorAll('.action-button').forEach(button => {
            button.addEventListener('click', handleQuickAction);
        });

        // Advanced operation checkboxes
        modal.querySelectorAll('.operation-item input').forEach(checkbox => {
            checkbox.addEventListener('change', handleOperationToggle);
        });

        // Run selected button
        modal.querySelector('#run-selected').addEventListener('click', runSelectedOperations);
    }

    function handleQuickAction(event) {
        const action = event.currentTarget.dataset.action;
        if (state.isRunning) {
            showToast('Another operation is in progress', 'warning');
            return;
        }

        executeAction(action);
    }

    function handleOperationToggle(event) {
        const operation = event.target.dataset.operation;
        if (event.target.checked) {
            state.selectedOperations.add(operation);
        } else {
            state.selectedOperations.delete(operation);
        }

        // Enable/disable run button
        const runButton = document.getElementById('run-selected');
        runButton.disabled = state.selectedOperations.size === 0;
    }

    async function executeAction(action) {
        state.isRunning = true;

        // Initialize progress modal if needed
        if (!window.ProgressModal) {
            console.error('ProgressModal not loaded');
            showToast('Progress modal not available', 'error');
            return;
        }

        // Start progress modal
        const actionLabels = {
            'deduplicate': 'Remove Duplicate Links',
            'clean-orphans': 'Clean Orphaned Images',
            'validate-paths': 'Validate File Paths',
            'fix-broken-links': 'Fix Broken Image Links',
            'optimize': 'Optimize Database',
            'backup': 'Create Database Backup',
            'remove-missing': 'Remove Missing Files'
        };

        const actionName = actionLabels[action] || action;
        ProgressModal.start(actionName);

        try {
            ProgressModal.updateProgress(10, 'Connecting to server...');

            const response = await fetch(`/api/prompt_manager/maintenance/${action}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            });

            ProgressModal.updateProgress(50, 'Processing...');

            const result = await response.json();

            if (result.success) {
                // Parse result for details
                const details = {};
                if (result.count !== undefined) {
                    details['Items Processed'] = result.count;
                }
                if (result.size !== undefined) {
                    details['Size'] = result.size;
                }
                if (result.time !== undefined) {
                    details['Time Taken'] = result.time;
                }

                ProgressModal.complete(true, result.message, details);
                showToast(result.message, 'success');

                // Refresh stats after operation
                await loadStatistics();
            } else {
                ProgressModal.complete(false, result.error || 'Operation failed');
                showToast(result.error || 'Operation failed', 'error');
            }
        } catch (error) {
            ProgressModal.complete(false, `Error: ${error.message}`);
            showToast(`Error: ${error.message}`, 'error');
        } finally {
            state.isRunning = false;
        }
    }

    async function runSelectedOperations() {
        if (state.selectedOperations.size === 0) return;
        if (state.isRunning) {
            showToast('Another operation is in progress', 'warning');
            return;
        }

        state.isRunning = true;

        // Initialize progress modal if needed
        if (!window.ProgressModal) {
            console.error('ProgressModal not loaded');
            showToast('Progress modal not available', 'error');
            state.isRunning = false;
            return;
        }

        const operations = Array.from(state.selectedOperations);
        const operationLabels = {
            'check-integrity': 'Full Database Integrity Check',
            'reindex': 'Rebuild All Indexes',
            'export': 'Export Database Backup'
        };

        // Start progress modal for batch operations
        ProgressModal.start(`Running ${operations.length} Operations`);

        let completed = 0;
        let failed = 0;

        for (const operation of operations) {
            const operationName = operationLabels[operation] || operation;
            const progress = (completed / operations.length) * 100;

            ProgressModal.updateProgress(progress, `Running: ${operationName}`);
            ProgressModal.log(`Starting ${operationName}...`, 'info');

            try {
                const response = await fetch(`/api/prompt_manager/maintenance/${operation}`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    }
                });

                const result = await response.json();

                if (result.success) {
                    completed++;
                    ProgressModal.log(`✅ ${operationName} completed successfully`, 'success');
                } else {
                    failed++;
                    ProgressModal.log(`❌ ${operationName} failed: ${result.error}`, 'error');
                }
            } catch (error) {
                failed++;
                ProgressModal.log(`❌ ${operationName} error: ${error.message}`, 'error');
            }
        }

        // Complete with summary
        const success = failed === 0;
        const message = success
            ? `All ${operations.length} operations completed successfully!`
            : `Completed ${completed} of ${operations.length} operations (${failed} failed)`;

        const details = {
            'Total Operations': operations.length,
            'Successful': completed,
            'Failed': failed
        };

        ProgressModal.complete(success, message, details);
        showToast(message, success ? 'success' : 'warning');

        state.isRunning = false;

        // Clear selections
        document.querySelectorAll('.operation-item input').forEach(checkbox => {
            checkbox.checked = false;
        });
        state.selectedOperations.clear();

        // Refresh stats
        await loadStatistics();
    }

    async function loadStatistics() {
        try {
            const response = await fetch('/api/prompt_manager/maintenance/stats');
            const data = await response.json();

            if (data.success) {
                state.currentStats = data.stats;
                updateStatisticsDisplay(data.stats);
            }
        } catch (error) {
            console.error('Failed to load maintenance statistics:', error);
        }
    }

    function updateStatisticsDisplay(stats) {
        // Update stat values
        document.getElementById('stat-prompts').textContent = stats.prompts.toLocaleString();
        document.getElementById('stat-images').textContent = stats.images.toLocaleString();
        document.getElementById('stat-duplicates').textContent = stats.duplicates.toLocaleString();
        document.getElementById('stat-orphaned').textContent = stats.orphaned.toLocaleString();
        document.getElementById('stat-missing').textContent = stats.missing_files.toLocaleString();

        // Format database size
        const sizeMB = (stats.database_size / (1024 * 1024)).toFixed(2);
        document.getElementById('stat-size').textContent = `${sizeMB} MB`;
        document.getElementById('footer-db-size').textContent = `${sizeMB} MB`;

        // Update container classes based on values
        const duplicatesContainer = document.getElementById('stat-duplicates-container');
        duplicatesContainer.className = stats.duplicates > 0 ? 'stat-item warning' : 'stat-item success';

        const orphanedContainer = document.getElementById('stat-orphaned-container');
        orphanedContainer.className = stats.orphaned > 0 ? 'stat-item error' : 'stat-item success';

        const missingContainer = document.getElementById('stat-missing-container');
        missingContainer.className = stats.missing_files > 0 ? 'stat-item error' : 'stat-item success';

        // Update last updated time
        const now = new Date().toLocaleTimeString();
        document.getElementById('footer-last-updated').textContent = now;
    }

    function showProgress() {
        const progressDiv = document.getElementById('maintenance-progress');
        progressDiv.classList.add('active');

        // Clear previous log entries
        document.getElementById('progress-log').innerHTML = '';
    }

    function hideProgress() {
        const progressDiv = document.getElementById('maintenance-progress');
        progressDiv.classList.remove('active');
    }

    function updateProgress(percent, message) {
        document.getElementById('progress-bar').style.width = `${percent}%`;
        document.getElementById('progress-status').textContent = `${Math.round(percent)}%`;

        if (message) {
            addLogEntry(message);
        }
    }

    function addLogEntry(message, type = 'info') {
        const log = document.getElementById('progress-log');
        const entry = document.createElement('div');
        entry.className = `progress-log-entry ${type}`;
        entry.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
        log.appendChild(entry);
        log.scrollTop = log.scrollHeight;
    }

    // Public API
    return {
        init,

        open: async function() {
            const modal = document.getElementById(config.modalId);
            if (!modal) {
                init();
            }

            state.isOpen = true;
            document.getElementById(config.modalId).style.display = 'flex';

            // Load statistics
            await loadStatistics();

            // Start periodic refresh
            state.statsTimer = setInterval(loadStatistics, config.statsRefreshInterval);
        },

        close: function() {
            state.isOpen = false;
            document.getElementById(config.modalId).style.display = 'none';

            // Stop periodic refresh
            if (state.statsTimer) {
                clearInterval(state.statsTimer);
                state.statsTimer = null;
            }
        },

        isOpen: function() {
            return state.isOpen;
        },

        refreshStats: function() {
            return loadStatistics();
        }
    };

    // Alias close function
    function close() {
        MaintenanceModal.close();
    }
})();

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = MaintenanceModal;
}

// Attach to window for global access
if (typeof window !== 'undefined') {
    window.MaintenanceModal = MaintenanceModal;
}
