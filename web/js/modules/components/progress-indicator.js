/**
 * Progress Indicator Component
 * Shows progress for long-running operations with various styles
 * @module ProgressIndicator
 */
const ProgressIndicator = (function() {
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
        console.info('[PromptManager] progress indicator skipped outside PromptManager UI context');
        return createStub();
    }

    // Configuration
    const config = {
        position: 'top-center', // top-center, bottom-center, overlay
        showPercentage: true,
        showTimeEstimate: true,
        showCancelButton: true,
        animations: true,
        stackable: true,
        maxStack: 3
    };

    // State
    let state = {
        operations: new Map(), // id -> operation data
        activeOperations: [],
        nextId: 1
    };

    // Progress bar styles
    const STYLES = {
        DEFAULT: 'default',
        SUCCESS: 'success',
        WARNING: 'warning',
        ERROR: 'error',
        INFO: 'info'
    };

    /**
     * Initialize the progress indicator
     */
    function init(options = {}) {
        Object.assign(config, options);
        createDOM();
        subscribeToEvents();
        return this;
    }

    /**
     * Create the DOM structure
     */
    function createDOM() {
        // Remove existing container if present
        const existing = document.getElementById('progress-indicator-container');
        if (existing) {
            existing.remove();
        }

        // Create container
        const container = document.createElement('div');
        container.id = 'progress-indicator-container';
        container.className = `progress-indicator-container ${config.position}`;

        // Add styles if not already present
        if (!document.getElementById('progress-indicator-styles')) {
            const styles = document.createElement('style');
            styles.id = 'progress-indicator-styles';
            styles.textContent = getStyles();
            document.head.appendChild(styles);
        }

        // Add to DOM
        document.body.appendChild(container);
    }

    /**
     * Get CSS styles for the progress indicator
     */
    function getStyles() {
        return `
            .progress-indicator-container {
                position: fixed;
                z-index: 9999;
                pointer-events: none;
                display: flex;
                flex-direction: column;
                gap: 10px;
            }

            .progress-indicator-container.top-center {
                top: 20px;
                left: 50%;
                transform: translateX(-50%);
                align-items: center;
            }

            .progress-indicator-container.bottom-center {
                bottom: 20px;
                left: 50%;
                transform: translateX(-50%);
                align-items: center;
            }

            .progress-indicator-container.overlay {
                top: 50%;
                left: 50%;
                transform: translate(-50%, -50%);
                align-items: center;
            }

            .progress-indicator {
                background: #1a1a1a;
                border: 1px solid #333;
                border-radius: 8px;
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5);
                padding: 16px;
                min-width: 320px;
                max-width: 480px;
                pointer-events: auto;
                opacity: 0;
                transform: translateY(-20px);
                animation: slideIn 0.3s ease forwards;
            }

            @keyframes slideIn {
                to {
                    opacity: 1;
                    transform: translateY(0);
                }
            }

            .progress-indicator.removing {
                animation: slideOut 0.3s ease forwards;
            }

            @keyframes slideOut {
                to {
                    opacity: 0;
                    transform: translateY(-20px);
                }
            }

            .progress-header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 12px;
            }

            .progress-title {
                color: #ffffff;
                font-weight: 600;
                font-size: 14px;
                flex: 1;
            }

            .progress-cancel {
                background: transparent;
                border: 1px solid #666;
                color: #999;
                padding: 4px 8px;
                border-radius: 4px;
                font-size: 12px;
                cursor: pointer;
                transition: all 0.2s;
            }

            .progress-cancel:hover {
                border-color: #cc0000;
                color: #cc0000;
            }

            .progress-description {
                color: #888;
                font-size: 13px;
                margin-bottom: 12px;
            }

            .progress-bar-container {
                background: #0a0a0a;
                border-radius: 4px;
                height: 8px;
                overflow: hidden;
                position: relative;
            }

            .progress-bar {
                height: 100%;
                background: linear-gradient(90deg, #0066cc, #0088ff);
                transition: width 0.3s ease;
                position: relative;
                overflow: hidden;
            }

            .progress-bar::after {
                content: '';
                position: absolute;
                top: 0;
                left: 0;
                bottom: 0;
                right: 0;
                background: linear-gradient(
                    90deg,
                    transparent,
                    rgba(255, 255, 255, 0.2),
                    transparent
                );
                animation: shimmer 2s infinite;
            }

            @keyframes shimmer {
                0% { transform: translateX(-100%); }
                100% { transform: translateX(100%); }
            }

            .progress-bar.success {
                background: linear-gradient(90deg, #00cc66, #00ff88);
            }

            .progress-bar.warning {
                background: linear-gradient(90deg, #ffaa00, #ffcc00);
            }

            .progress-bar.error {
                background: linear-gradient(90deg, #cc0000, #ff0033);
            }

            .progress-bar.info {
                background: linear-gradient(90deg, #00aaff, #00ccff);
            }

            .progress-bar.indeterminate {
                width: 100% !important;
                background: linear-gradient(90deg, #333, #666, #333);
                background-size: 200% 100%;
                animation: indeterminate 1.5s linear infinite;
            }

            @keyframes indeterminate {
                0% { background-position: 200% 0; }
                100% { background-position: -200% 0; }
            }

            .progress-info {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-top: 8px;
                font-size: 12px;
                color: #888;
            }

            .progress-percentage {
                font-weight: 600;
                color: #0066cc;
            }

            .progress-time {
                display: flex;
                gap: 12px;
            }

            .progress-elapsed {
                display: flex;
                align-items: center;
                gap: 4px;
            }

            .progress-remaining {
                display: flex;
                align-items: center;
                gap: 4px;
            }

            .progress-details {
                margin-top: 8px;
                padding-top: 8px;
                border-top: 1px solid #333;
                font-size: 12px;
                color: #888;
            }

            .progress-step {
                display: flex;
                align-items: center;
                gap: 8px;
                margin-top: 4px;
            }

            .progress-step-icon {
                width: 16px;
                height: 16px;
                border-radius: 50%;
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 10px;
                background: #333;
            }

            .progress-step-icon.completed {
                background: #00cc66;
                color: #fff;
            }

            .progress-step-icon.active {
                background: #0066cc;
                color: #fff;
                animation: pulse 1.5s ease-out infinite;
            }

            @keyframes pulse {
                0% {
                    box-shadow: 0 0 0 0 rgba(0, 102, 204, 0.7);
                }
                70% {
                    box-shadow: 0 0 0 10px rgba(0, 102, 204, 0);
                }
                100% {
                    box-shadow: 0 0 0 0 rgba(0, 102, 204, 0);
                }
            }

            .progress-step-text {
                flex: 1;
            }

            /* Overlay backdrop */
            .progress-backdrop {
                position: fixed;
                top: 0;
                left: 0;
                right: 0;
                bottom: 0;
                background: rgba(0, 0, 0, 0.7);
                z-index: 9998;
                opacity: 0;
                animation: fadeIn 0.3s ease forwards;
            }

            @keyframes fadeIn {
                to { opacity: 1; }
            }

            .progress-backdrop.removing {
                animation: fadeOut 0.3s ease forwards;
            }

            @keyframes fadeOut {
                to { opacity: 0; }
            }

            /* Compact mode for stacked progress */
            .progress-indicator.compact {
                min-width: 280px;
                padding: 12px;
            }

            .progress-indicator.compact .progress-description,
            .progress-indicator.compact .progress-details {
                display: none;
            }

            .progress-indicator.compact .progress-bar-container {
                height: 4px;
            }
        `;
    }

    /**
     * Subscribe to global events
     */
    function subscribeToEvents() {
        if (window.EventBus) {
            EventBus.on('progress:start', handleProgressStart);
            EventBus.on('progress:update', handleProgressUpdate);
            EventBus.on('progress:complete', handleProgressComplete);
            EventBus.on('progress:error', handleProgressError);
            EventBus.on('progress:cancel', handleProgressCancel);
        }

        // Listen for SSE progress events
        if (window.SSEClient) {
            EventBus.on('sse:progress', handleSSEProgress);
        }
    }

    /**
     * Start a new progress operation
     */
    function start(options) {
        const id = options.id || `progress-${state.nextId++}`;

        const operation = {
            id,
            title: options.title || 'Processing...',
            description: options.description || '',
            progress: 0,
            style: options.style || STYLES.DEFAULT,
            indeterminate: options.indeterminate || false,
            cancelable: options.cancelable !== false && config.showCancelButton,
            startTime: Date.now(),
            steps: options.steps || [],
            currentStep: 0,
            data: options.data || {},
            onCancel: options.onCancel
        };

        state.operations.set(id, operation);
        state.activeOperations.push(id);

        // Create backdrop for overlay mode
        if (config.position === 'overlay' && state.activeOperations.length === 1) {
            createBackdrop();
        }

        render();
        return id;
    }

    /**
     * Update progress
     */
    function update(id, progress, options = {}) {
        const operation = state.operations.get(id);
        if (!operation) return;

        operation.progress = Math.min(100, Math.max(0, progress));

        if (options.description) {
            operation.description = options.description;
        }

        if (options.currentStep !== undefined) {
            operation.currentStep = options.currentStep;
        }

        if (options.style) {
            operation.style = options.style;
        }

        updateProgressBar(id);
    }

    /**
     * Complete a progress operation
     */
    function complete(id, options = {}) {
        const operation = state.operations.get(id);
        if (!operation) return;

        operation.progress = 100;
        operation.style = options.style || STYLES.SUCCESS;

        updateProgressBar(id);

        // Remove after a delay
        setTimeout(() => remove(id), options.delay || 1000);
    }

    /**
     * Mark operation as errored
     */
    function error(id, message, options = {}) {
        const operation = state.operations.get(id);
        if (!operation) return;

        operation.style = STYLES.ERROR;
        operation.description = message || 'An error occurred';

        updateProgressBar(id);

        // Remove after a longer delay for errors
        setTimeout(() => remove(id), options.delay || 3000);
    }

    /**
     * Cancel an operation
     */
    function cancel(id) {
        const operation = state.operations.get(id);
        if (!operation) return;

        // Call cancel callback if provided
        if (operation.onCancel) {
            operation.onCancel();
        }

        // Emit cancel event
        if (window.EventBus) {
            EventBus.emit('progress:cancelled', { id, operation });
        }

        remove(id);
    }

    /**
     * Remove a progress indicator
     */
    function remove(id) {
        const element = document.getElementById(`progress-${id}`);
        if (element) {
            element.classList.add('removing');
            setTimeout(() => {
                element.remove();
                state.operations.delete(id);
                state.activeOperations = state.activeOperations.filter(opId => opId !== id);

                // Remove backdrop if no more operations
                if (config.position === 'overlay' && state.activeOperations.length === 0) {
                    removeBackdrop();
                }
            }, 300);
        }
    }

    /**
     * Render all active operations
     */
    function render() {
        const container = document.getElementById('progress-indicator-container');
        if (!container) return;

        // Limit stack size
        const toRender = config.stackable
            ? state.activeOperations.slice(-config.maxStack)
            : state.activeOperations.slice(-1);

        toRender.forEach((id, index) => {
            const operation = state.operations.get(id);
            if (!operation) return;

            let element = document.getElementById(`progress-${id}`);
            if (!element) {
                element = createProgressElement(operation);
                container.appendChild(element);
            }

            // Update compact mode for stacked items
            if (config.stackable && toRender.length > 1 && index < toRender.length - 1) {
                element.classList.add('compact');
            } else {
                element.classList.remove('compact');
            }
        });
    }

    /**
     * Create progress element
     */
    function createProgressElement(operation) {
        const div = document.createElement('div');
        div.id = `progress-${operation.id}`;
        div.className = 'progress-indicator';

        const elapsed = Date.now() - operation.startTime;
        const remaining = operation.progress > 0
            ? (elapsed / operation.progress) * (100 - operation.progress)
            : 0;

        div.innerHTML = `
            <div class="progress-header">
                <div class="progress-title">${operation.title}</div>
                ${operation.cancelable ? `<button class="progress-cancel" data-id="${operation.id}">Cancel</button>` : ''}
            </div>
            ${operation.description ? `<div class="progress-description">${operation.description}</div>` : ''}
            <div class="progress-bar-container">
                <div class="progress-bar ${operation.style} ${operation.indeterminate ? 'indeterminate' : ''}"
                     style="width: ${operation.indeterminate ? '100' : operation.progress}%"></div>
            </div>
            <div class="progress-info">
                ${config.showPercentage && !operation.indeterminate ? `<span class="progress-percentage">${Math.round(operation.progress)}%</span>` : '<span></span>'}
                ${config.showTimeEstimate ? `
                    <div class="progress-time">
                        <span class="progress-elapsed">⏱ ${formatTime(elapsed)}</span>
                        ${!operation.indeterminate && operation.progress > 0 && operation.progress < 100 ?
                            `<span class="progress-remaining">⏳ ~${formatTime(remaining)}</span>` : ''}
                    </div>
                ` : ''}
            </div>
            ${operation.steps && operation.steps.length > 0 ? renderSteps(operation) : ''}
        `;

        // Add cancel handler
        const cancelBtn = div.querySelector('.progress-cancel');
        if (cancelBtn) {
            cancelBtn.addEventListener('click', () => cancel(operation.id));
        }

        return div;
    }

    /**
     * Render steps
     */
    function renderSteps(operation) {
        const stepsHtml = operation.steps.map((step, index) => {
            let iconClass = '';
            let icon = '○';

            if (index < operation.currentStep) {
                iconClass = 'completed';
                icon = '✓';
            } else if (index === operation.currentStep) {
                iconClass = 'active';
                icon = '●';
            }

            return `
                <div class="progress-step">
                    <div class="progress-step-icon ${iconClass}">${icon}</div>
                    <div class="progress-step-text">${step}</div>
                </div>
            `;
        }).join('');

        return `<div class="progress-details">${stepsHtml}</div>`;
    }

    /**
     * Update progress bar
     */
    function updateProgressBar(id) {
        const operation = state.operations.get(id);
        if (!operation) return;

        const element = document.getElementById(`progress-${id}`);
        if (!element) return;

        const bar = element.querySelector('.progress-bar');
        if (bar) {
            bar.className = `progress-bar ${operation.style} ${operation.indeterminate ? 'indeterminate' : ''}`;
            if (!operation.indeterminate) {
                bar.style.width = `${operation.progress}%`;
            }
        }

        const percentage = element.querySelector('.progress-percentage');
        if (percentage && !operation.indeterminate) {
            percentage.textContent = `${Math.round(operation.progress)}%`;
        }

        const description = element.querySelector('.progress-description');
        if (description) {
            description.textContent = operation.description;
        }

        // Update time estimates
        if (config.showTimeEstimate) {
            const elapsed = Date.now() - operation.startTime;
            const elapsedEl = element.querySelector('.progress-elapsed');
            if (elapsedEl) {
                elapsedEl.innerHTML = `⏱ ${formatTime(elapsed)}`;
            }

            if (operation.progress > 0 && operation.progress < 100) {
                const remaining = (elapsed / operation.progress) * (100 - operation.progress);
                const remainingEl = element.querySelector('.progress-remaining');
                if (remainingEl) {
                    remainingEl.innerHTML = `⏳ ~${formatTime(remaining)}`;
                }
            }
        }

        // Update steps if present
        if (operation.steps && operation.steps.length > 0) {
            const details = element.querySelector('.progress-details');
            if (details) {
                details.innerHTML = renderSteps(operation).replace('<div class="progress-details">', '').replace('</div>', '');
            }
        }
    }

    /**
     * Format time in human-readable format
     */
    function formatTime(ms) {
        if (ms < 1000) return 'Just started';

        const seconds = Math.floor(ms / 1000);
        if (seconds < 60) return `${seconds}s`;

        const minutes = Math.floor(seconds / 60);
        const remainingSeconds = seconds % 60;
        if (minutes < 60) return `${minutes}m ${remainingSeconds}s`;

        const hours = Math.floor(minutes / 60);
        const remainingMinutes = minutes % 60;
        return `${hours}h ${remainingMinutes}m`;
    }

    /**
     * Create backdrop for overlay mode
     */
    function createBackdrop() {
        const existing = document.getElementById('progress-backdrop');
        if (existing) return;

        const backdrop = document.createElement('div');
        backdrop.id = 'progress-backdrop';
        backdrop.className = 'progress-backdrop';
        document.body.appendChild(backdrop);
    }

    /**
     * Remove backdrop
     */
    function removeBackdrop() {
        const backdrop = document.getElementById('progress-backdrop');
        if (backdrop) {
            backdrop.classList.add('removing');
            setTimeout(() => backdrop.remove(), 300);
        }
    }

    // Event handlers
    function handleProgressStart(data) {
        start(data);
    }

    function handleProgressUpdate(data) {
        update(data.id, data.progress, data);
    }

    function handleProgressComplete(data) {
        complete(data.id, data);
    }

    function handleProgressError(data) {
        error(data.id, data.message, data);
    }

    function handleProgressCancel(data) {
        cancel(data.id);
    }

    function handleSSEProgress(data) {
        if (data.operation) {
            const id = `sse-${data.operation}`;

            if (data.progress === 0 || !state.operations.has(id)) {
                start({
                    id,
                    title: data.operation,
                    description: data.message || '',
                    progress: data.progress,
                    indeterminate: data.progress === undefined
                });
            } else if (data.progress === 100 || data.type === 'complete') {
                complete(id);
            } else if (data.type === 'error') {
                error(id, data.message);
            } else {
                update(id, data.progress, { description: data.message });
            }
        }
    }

    /**
     * Clear all progress indicators
     */
    function clearAll() {
        state.activeOperations.forEach(id => remove(id));
    }

    /**
     * Destroy the progress indicator system
     */
    function destroy() {
        clearAll();

        // Unsubscribe from events
        if (window.EventBus) {
            EventBus.off('progress:start', handleProgressStart);
            EventBus.off('progress:update', handleProgressUpdate);
            EventBus.off('progress:complete', handleProgressComplete);
            EventBus.off('progress:error', handleProgressError);
            EventBus.off('progress:cancel', handleProgressCancel);
        }

        // Remove DOM elements
        const container = document.getElementById('progress-indicator-container');
        if (container) container.remove();

        const backdrop = document.getElementById('progress-backdrop');
        if (backdrop) backdrop.remove();

        // Clear state
        state.operations.clear();
        state.activeOperations = [];
    }

    // Public API
    return {
        init,
        start,
        update,
        complete,
        error,
        cancel,
        remove,
        clearAll,
        destroy,
        STYLES
    };
})();

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = ProgressIndicator;
}

// Attach to window for global access
if (typeof window !== 'undefined') {
    window.ProgressIndicator = ProgressIndicator;
}
