/**
 * Progress Modal Component
 * Shows operation progress in a modal that requires user dismissal
 * @module ProgressModal
 */
const ProgressModal = (function() {
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
        console.info('[PromptManager] progress modal skipped outside PromptManager UI context');
        return createStub();
    }

    // Configuration
    const config = {
        modalId: 'progress-modal',
        autoClose: false  // Require manual dismissal
    };

    // State
    let state = {
        isOpen: false,
        isRunning: false,
        operationName: '',
        startTime: null,
        logs: []
    };

    // Private methods
    function createModalHTML() {
        return `
            <div id="${config.modalId}" class="modal progress-modal" style="display: none;">
                <div class="modal-backdrop"></div>
                <div class="modal-content">
                    <div class="modal-header">
                        <h2 class="modal-title">
                            <span class="spinner" id="progress-spinner" style="display: none;"></span>
                            <span id="progress-title">Operation Progress</span>
                        </h2>
                    </div>

                    <div class="modal-body">
                        <!-- Operation Info -->
                        <div class="operation-info">
                            <div class="operation-name" id="operation-name"></div>
                            <div class="operation-time">
                                <span id="elapsed-time"></span>
                            </div>
                        </div>

                        <!-- Progress Bar -->
                        <div class="progress-container">
                            <div class="progress-header">
                                <span class="progress-label" id="progress-label">Initializing...</span>
                                <span class="progress-percent" id="progress-percent">0%</span>
                            </div>
                            <div class="progress-bar-wrapper">
                                <div class="progress-bar" id="progress-bar" style="width: 0%"></div>
                            </div>
                        </div>

                        <!-- Log Output -->
                        <div class="progress-log-container">
                            <div class="log-header">
                                <span>Operation Log</span>
                                <button class="btn-clear-log" id="clear-log">Clear</button>
                            </div>
                            <div class="progress-log" id="progress-log"></div>
                        </div>

                        <!-- Result Summary (shown when complete) -->
                        <div class="result-summary" id="result-summary" style="display: none;">
                            <div class="result-icon" id="result-icon"></div>
                            <div class="result-message" id="result-message"></div>
                            <div class="result-details" id="result-details"></div>
                        </div>
                    </div>

                    <div class="modal-footer">
                        <button class="btn btn-secondary" id="progress-cancel" style="display: none;">Cancel</button>
                        <button class="btn btn-primary" id="progress-close" disabled>Close</button>
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

        // Close button
        modal.querySelector('#progress-close').addEventListener('click', () => {
            ProgressModal.close();
        });

        // Clear log button
        modal.querySelector('#clear-log').addEventListener('click', clearLog);

        // Cancel button (if operation supports cancellation)
        modal.querySelector('#progress-cancel').addEventListener('click', () => {
            if (state.onCancel) {
                state.onCancel();
            }
        });
    }

    function updateElapsedTime() {
        if (!state.startTime || !state.isRunning) return;

        const elapsed = Date.now() - state.startTime;
        const seconds = Math.floor(elapsed / 1000);
        const minutes = Math.floor(seconds / 60);
        const displaySeconds = seconds % 60;

        const timeStr = minutes > 0
            ? `${minutes}m ${displaySeconds}s`
            : `${displaySeconds}s`;

        const elapsedEl = document.getElementById('elapsed-time');
        if (elapsedEl) {
            elapsedEl.textContent = `Elapsed: ${timeStr}`;
        }
    }

    function clearLog() {
        const log = document.getElementById('progress-log');
        if (log) {
            log.innerHTML = '';
            state.logs = [];
        }
    }

    function formatTimestamp() {
        return new Date().toLocaleTimeString('en-US', {
            hour12: false,
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit'
        });
    }

    // Public API
    return {
        init,

        /**
         * Start a new operation
         * @param {string} operationName - Name of the operation
         * @param {Object} options - Options including onCancel callback
         */
        start: function(operationName, options = {}) {
            state.isOpen = true;
            state.isRunning = true;
            state.operationName = operationName;
            state.startTime = Date.now();
            state.logs = [];
            state.onCancel = options.onCancel;

            const modal = document.getElementById(config.modalId);
            if (!modal) {
                init();
            }

            // Show modal
            document.getElementById(config.modalId).style.display = 'flex';

            // Update UI
            document.getElementById('operation-name').textContent = operationName;
            document.getElementById('progress-title').textContent = operationName;
            document.getElementById('progress-spinner').style.display = 'inline-block';
            document.getElementById('progress-label').textContent = 'Initializing...';
            document.getElementById('progress-percent').textContent = '0%';
            document.getElementById('progress-bar').style.width = '0%';

            // Hide result summary
            document.getElementById('result-summary').style.display = 'none';

            // Clear previous logs
            clearLog();

            // Disable close button during operation
            document.getElementById('progress-close').disabled = true;

            // Show cancel button if cancellable
            document.getElementById('progress-cancel').style.display =
                options.onCancel ? 'inline-block' : 'none';

            // Start elapsed time updates
            state.elapsedTimer = setInterval(updateElapsedTime, 100);

            // Add start log
            this.log(`Operation started: ${operationName}`, 'info');
        },

        /**
         * Update progress
         * @param {number} percent - Progress percentage (0-100)
         * @param {string} message - Status message
         */
        updateProgress: function(percent, message) {
            const progressBar = document.getElementById('progress-bar');
            const progressPercent = document.getElementById('progress-percent');
            const progressLabel = document.getElementById('progress-label');

            if (progressBar) progressBar.style.width = `${percent}%`;
            if (progressPercent) progressPercent.textContent = `${Math.round(percent)}%`;
            if (progressLabel && message) progressLabel.textContent = message;

            if (message) {
                this.log(message, percent === 100 ? 'success' : 'info');
            }
        },

        /**
         * Add a log entry
         * @param {string} message - Log message
         * @param {string} type - Log type (info, success, warning, error)
         */
        log: function(message, type = 'info') {
            const log = document.getElementById('progress-log');
            if (!log) return;

            const entry = document.createElement('div');
            entry.className = `log-entry log-${type}`;

            const timestamp = document.createElement('span');
            timestamp.className = 'log-timestamp';
            timestamp.textContent = `[${formatTimestamp()}]`;

            const content = document.createElement('span');
            content.className = 'log-content';
            content.textContent = message;

            entry.appendChild(timestamp);
            entry.appendChild(content);
            log.appendChild(entry);

            // Auto-scroll to bottom
            log.scrollTop = log.scrollHeight;

            // Store in state
            state.logs.push({ message, type, timestamp: Date.now() });
        },

        /**
         * Complete the operation
         * @param {boolean} success - Whether operation succeeded
         * @param {string} message - Completion message
         * @param {Object} details - Additional details
         */
        complete: function(success, message, details = {}) {
            state.isRunning = false;

            // Clear elapsed timer
            if (state.elapsedTimer) {
                clearInterval(state.elapsedTimer);
                state.elapsedTimer = null;
            }

            // Update final elapsed time
            updateElapsedTime();

            // Update progress to 100%
            this.updateProgress(100, success ? 'Complete!' : 'Failed');

            // Hide spinner
            document.getElementById('progress-spinner').style.display = 'none';

            // Show result summary
            const resultSummary = document.getElementById('result-summary');
            const resultIcon = document.getElementById('result-icon');
            const resultMessage = document.getElementById('result-message');
            const resultDetails = document.getElementById('result-details');

            if (resultSummary) {
                resultSummary.style.display = 'block';
                resultSummary.className = `result-summary ${success ? 'success' : 'error'}`;
            }

            if (resultIcon) {
                resultIcon.textContent = success ? '✅' : '❌';
            }

            if (resultMessage) {
                resultMessage.textContent = message;
            }

            if (resultDetails && details) {
                const detailsHtml = Object.entries(details)
                    .map(([key, value]) => `<div><strong>${key}:</strong> ${value}</div>`)
                    .join('');
                resultDetails.innerHTML = detailsHtml;
            }

            // Add completion log
            this.log(message, success ? 'success' : 'error');

            // Enable close button
            document.getElementById('progress-close').disabled = false;

            // Hide cancel button
            document.getElementById('progress-cancel').style.display = 'none';

            // Focus close button for easy dismissal
            document.getElementById('progress-close').focus();
        },

        /**
         * Close the modal
         */
        close: function() {
            state.isOpen = false;
            state.isRunning = false;

            // Clear timer
            if (state.elapsedTimer) {
                clearInterval(state.elapsedTimer);
                state.elapsedTimer = null;
            }

            document.getElementById(config.modalId).style.display = 'none';
        },

        /**
         * Check if modal is open
         */
        isOpen: function() {
            return state.isOpen;
        },

        /**
         * Check if operation is running
         */
        isRunning: function() {
            return state.isRunning;
        }
    };
})();

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = ProgressModal;
}

// Attach to window for global access
if (typeof window !== 'undefined') {
    window.ProgressModal = ProgressModal;
}
