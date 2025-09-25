/**
 * Status Indicator Component
 * Shows connection status for SSE and other real-time services
 * @module StatusIndicator
 */
const StatusIndicator = (function() {
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
        console.info('[PromptManager] status indicator skipped outside PromptManager UI context');
        return createStub();
    }

    // Configuration
    const config = {
        position: 'bottom-right', // top-left, top-right, bottom-left, bottom-right
        autoHide: true,
        autoHideDelay: 5000,
        animations: true,
        showDetails: true
    };

    // State
    let state = {
        connections: new Map(), // service -> status
        isVisible: false,
        autoHideTimer: null
    };

    // Status types
    const STATUS = {
        CONNECTED: 'connected',
        CONNECTING: 'connecting',
        DISCONNECTED: 'disconnected',
        ERROR: 'error',
        RECONNECTING: 'reconnecting'
    };

    // Status configurations
    const statusConfig = {
        [STATUS.CONNECTED]: {
            icon: '✓',
            color: '#00cc66',
            text: 'Connected',
            pulse: false
        },
        [STATUS.CONNECTING]: {
            icon: '◐',
            color: '#ffaa00',
            text: 'Connecting...',
            pulse: true
        },
        [STATUS.DISCONNECTED]: {
            icon: '✕',
            color: '#cc0000',
            text: 'Disconnected',
            pulse: false
        },
        [STATUS.ERROR]: {
            icon: '⚠',
            color: '#cc0000',
            text: 'Error',
            pulse: true
        },
        [STATUS.RECONNECTING]: {
            icon: '↻',
            color: '#ffaa00',
            text: 'Reconnecting...',
            pulse: false  // Disabled pulse since we use spin animation
        }
    };

    /**
     * Initialize the status indicator
     */
    function init(options = {}) {
        Object.assign(config, options);
        createDOM();
        attachEventListeners();
        subscribeToEvents();
        return this;
    }

    /**
     * Create the DOM structure
     */
    function createDOM() {
        // Remove existing indicator if present
        const existing = document.getElementById('status-indicator');
        if (existing) {
            existing.remove();
        }

        // Create container
        const container = document.createElement('div');
        container.id = 'status-indicator';
        container.className = `status-indicator ${config.position}`;
        container.innerHTML = `
            <div class="status-indicator-content">
                <div class="status-indicator-header">
                    <span class="status-indicator-title">Connection Status</span>
                    <button class="status-indicator-close" aria-label="Close">×</button>
                </div>
                <div class="status-indicator-body">
                    <div class="status-indicator-services"></div>
                </div>
                <div class="status-indicator-footer">
                    <button class="status-indicator-reconnect-all">Reconnect All</button>
                </div>
            </div>
        `;

        // Add styles if not already present
        if (!document.getElementById('status-indicator-styles')) {
            const styles = document.createElement('style');
            styles.id = 'status-indicator-styles';
            styles.textContent = getStyles();
            document.head.appendChild(styles);
        }

        // Add to DOM
        document.body.appendChild(container);
    }

    /**
     * Get CSS styles for the indicator
     */
    function getStyles() {
        return `
            .status-indicator {
                position: fixed;
                z-index: 10000;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                font-size: 14px;
                transition: all 0.3s ease;
                pointer-events: none;
            }

            .status-indicator.visible {
                pointer-events: auto;
            }

            .status-indicator.top-left {
                top: 20px;
                left: 20px;
            }

            .status-indicator.top-right {
                top: 20px;
                right: 20px;
            }

            .status-indicator.bottom-left {
                bottom: 20px;
                left: 20px;
            }

            .status-indicator.bottom-right {
                bottom: 20px;
                right: 20px;
            }

            .status-indicator-content {
                background: #1a1a1a;
                border: 1px solid #333;
                border-radius: 8px;
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5);
                min-width: 280px;
                max-width: 350px;
                opacity: 0;
                transform: translateY(10px);
                transition: all 0.3s ease;
            }

            .status-indicator.visible .status-indicator-content {
                opacity: 1;
                transform: translateY(0);
            }

            .status-indicator-header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                padding: 12px 16px;
                border-bottom: 1px solid #333;
            }

            .status-indicator-title {
                color: #ffffff;
                font-weight: 600;
            }

            .status-indicator-close {
                background: transparent;
                border: none;
                color: #888;
                font-size: 20px;
                cursor: pointer;
                padding: 0;
                width: 24px;
                height: 24px;
                display: flex;
                align-items: center;
                justify-content: center;
                transition: color 0.2s;
            }

            .status-indicator-close:hover {
                color: #fff;
            }

            .status-indicator-body {
                padding: 12px 16px;
            }

            .status-indicator-services {
                display: flex;
                flex-direction: column;
                gap: 8px;
            }

            .status-service {
                display: flex;
                align-items: center;
                gap: 12px;
                padding: 8px;
                background: #0a0a0a;
                border-radius: 4px;
                transition: background 0.2s;
            }

            .status-service:hover {
                background: #141414;
            }

            .status-service-icon {
                width: 24px;
                height: 24px;
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 16px;
                border-radius: 50%;
                background: currentColor;
                position: relative;
                overflow: hidden;  /* Contain the spinning icon */
            }

            .status-service-icon.pulse::after {
                content: '';
                position: absolute;
                top: 0;
                left: 0;
                right: 0;
                bottom: 0;
                border-radius: 50%;
                border: 2px solid currentColor;
                animation: pulse 1.5s ease-out infinite;
            }

            @keyframes pulse {
                0% {
                    transform: scale(1);
                    opacity: 1;
                }
                100% {
                    transform: scale(1.5);
                    opacity: 0;
                }
            }

            .status-service-icon span {
                display: block;
                position: absolute;
                top: 50%;
                left: 50%;
                transform: translate(-50%, -50%);
                line-height: 1;
                z-index: 1;
            }

            .status-service-icon span.spin {
                animation: spinCentered 1s linear infinite;
            }

            @keyframes spinCentered {
                from { transform: translate(-50%, -50%) rotate(0deg); }
                to { transform: translate(-50%, -50%) rotate(360deg); }
            }

            .status-service-info {
                flex: 1;
                display: flex;
                flex-direction: column;
            }

            .status-service-name {
                color: #ffffff;
                font-weight: 500;
            }

            .status-service-status {
                color: #888;
                font-size: 12px;
            }

            .status-service-action {
                background: transparent;
                border: 1px solid #333;
                color: #888;
                padding: 4px 8px;
                border-radius: 4px;
                font-size: 12px;
                cursor: pointer;
                transition: all 0.2s;
            }

            .status-service-action:hover {
                border-color: #0066cc;
                color: #0066cc;
            }

            .status-indicator-footer {
                padding: 12px 16px;
                border-top: 1px solid #333;
                display: none;
            }

            .status-indicator.has-errors .status-indicator-footer {
                display: block;
            }

            .status-indicator-reconnect-all {
                width: 100%;
                padding: 8px;
                background: #0066cc;
                color: white;
                border: none;
                border-radius: 4px;
                cursor: pointer;
                font-weight: 500;
                transition: background 0.2s;
            }

            .status-indicator-reconnect-all:hover {
                background: #0052a3;
            }

            /* Minimized state */
            .status-indicator.minimized .status-indicator-content {
                min-width: auto;
            }

            .status-indicator.minimized .status-indicator-header,
            .status-indicator.minimized .status-indicator-body,
            .status-indicator.minimized .status-indicator-footer {
                display: none;
            }

            .status-indicator-mini {
                width: 40px;
                height: 40px;
                background: #1a1a1a;
                border: 1px solid #333;
                border-radius: 50%;
                display: flex;
                align-items: center;
                justify-content: center;
                cursor: pointer;
                font-size: 20px;
            }
        `;
    }

    /**
     * Attach event listeners
     */
    function attachEventListeners() {
        const indicator = document.getElementById('status-indicator');
        if (!indicator) return;

        // Close button
        const closeBtn = indicator.querySelector('.status-indicator-close');
        if (closeBtn) {
            closeBtn.addEventListener('click', hide);
        }

        // Reconnect all button
        const reconnectBtn = indicator.querySelector('.status-indicator-reconnect-all');
        if (reconnectBtn) {
            reconnectBtn.addEventListener('click', reconnectAll);
        }
    }

    /**
     * Subscribe to global events
     */
    function subscribeToEvents() {
        // Listen for SSE events
        if (window.EventBus) {
            EventBus.on('sse:connected', handleConnected);
            EventBus.on('sse:disconnected', handleDisconnected);
            EventBus.on('sse:error', handleError);
            EventBus.on('sse:reconnecting', handleReconnecting);
        }

        // Listen for realtime update events
        if (window.RealtimeUpdates) {
            EventBus.on('realtime:stream:started', (data) => {
                updateService(data.stream, STATUS.CONNECTED);
            });
            EventBus.on('realtime:stream:stopped', (data) => {
                updateService(data.stream, STATUS.DISCONNECTED);
            });
            EventBus.on('realtime:stream:error', (data) => {
                updateService(data.stream, STATUS.ERROR, data.error);
            });
        }
    }

    /**
     * Update service status
     */
    function updateService(serviceName, status, details = null) {
        state.connections.set(serviceName, { status, details, timestamp: Date.now() });
        render();

        // Show indicator on status change
        if (status !== STATUS.CONNECTED) {
            show();
        } else if (config.autoHide && allConnected()) {
            scheduleAutoHide();
        }
    }

    /**
     * Render the current state
     */
    function render() {
        const container = document.querySelector('.status-indicator-services');
        if (!container) return;

        container.innerHTML = '';

        // Add each service
        state.connections.forEach((info, serviceName) => {
            const serviceEl = createServiceElement(serviceName, info);
            container.appendChild(serviceEl);
        });

        // Update footer visibility
        const indicator = document.getElementById('status-indicator');
        const hasErrors = Array.from(state.connections.values())
            .some(info => info.status === STATUS.ERROR || info.status === STATUS.DISCONNECTED);

        if (hasErrors) {
            indicator.classList.add('has-errors');
        } else {
            indicator.classList.remove('has-errors');
        }
    }

    /**
     * Create service element
     */
    function createServiceElement(serviceName, info) {
        const div = document.createElement('div');
        div.className = 'status-service';

        const config = statusConfig[info.status] || statusConfig[STATUS.DISCONNECTED];
        const isReconnectable = info.status === STATUS.ERROR || info.status === STATUS.DISCONNECTED;

        div.innerHTML = `
            <div class="status-service-icon ${config.pulse ? 'pulse' : ''}"
                 style="color: ${config.color}">
                <span class="${info.status === STATUS.RECONNECTING ? 'spin' : ''}" style="color: #1a1a1a">${config.icon}</span>
            </div>
            <div class="status-service-info">
                <div class="status-service-name">${formatServiceName(serviceName)}</div>
                <div class="status-service-status">${config.text}${info.details ? ` - ${info.details}` : ''}</div>
            </div>
            ${isReconnectable ? `<button class="status-service-action" data-service="${serviceName}">Reconnect</button>` : ''}
        `;

        // Add reconnect handler
        const reconnectBtn = div.querySelector('.status-service-action');
        if (reconnectBtn) {
            reconnectBtn.addEventListener('click', () => reconnectService(serviceName));
        }

        return div;
    }

    /**
     * Format service name for display
     */
    function formatServiceName(name) {
        return name.charAt(0).toUpperCase() + name.slice(1).replace(/-/g, ' ');
    }

    /**
     * Show the indicator
     */
    function show() {
        const indicator = document.getElementById('status-indicator');
        if (!indicator) return;

        indicator.classList.add('visible');
        state.isVisible = true;

        // Cancel any pending auto-hide
        if (state.autoHideTimer) {
            clearTimeout(state.autoHideTimer);
            state.autoHideTimer = null;
        }
    }

    /**
     * Hide the indicator
     */
    function hide() {
        const indicator = document.getElementById('status-indicator');
        if (!indicator) return;

        indicator.classList.remove('visible');
        state.isVisible = false;
    }

    /**
     * Schedule auto-hide
     */
    function scheduleAutoHide() {
        if (!config.autoHide) return;

        if (state.autoHideTimer) {
            clearTimeout(state.autoHideTimer);
        }

        state.autoHideTimer = setTimeout(() => {
            if (allConnected()) {
                hide();
            }
        }, config.autoHideDelay);
    }

    /**
     * Check if all services are connected
     */
    function allConnected() {
        return Array.from(state.connections.values())
            .every(info => info.status === STATUS.CONNECTED);
    }

    /**
     * Reconnect a specific service
     */
    function reconnectService(serviceName) {
        updateService(serviceName, STATUS.RECONNECTING);

        // Emit reconnect event
        if (window.EventBus) {
            EventBus.emit('status:reconnect', { service: serviceName });
        }

        // Trigger actual reconnection
        if (window.RealtimeUpdates && window.RealtimeUpdates.startStream) {
            window.RealtimeUpdates.startStream(serviceName);
        }
    }

    /**
     * Reconnect all services
     */
    function reconnectAll() {
        state.connections.forEach((info, serviceName) => {
            if (info.status === STATUS.ERROR || info.status === STATUS.DISCONNECTED) {
                reconnectService(serviceName);
            }
        });
    }

    /**
     * Handle connected event
     */
    function handleConnected(data) {
        updateService(data.service || 'main', STATUS.CONNECTED);
    }

    /**
     * Handle disconnected event
     */
    function handleDisconnected(data) {
        updateService(data.service || 'main', STATUS.DISCONNECTED);
    }

    /**
     * Handle error event
     */
    function handleError(data) {
        updateService(data.service || 'main', STATUS.ERROR, data.message);
    }

    /**
     * Handle reconnecting event
     */
    function handleReconnecting(data) {
        updateService(data.service || 'main', STATUS.RECONNECTING);
    }

    /**
     * Get current status
     */
    function getStatus() {
        return {
            services: Object.fromEntries(state.connections),
            isVisible: state.isVisible,
            allConnected: allConnected()
        };
    }

    /**
     * Destroy the indicator
     */
    function destroy() {
        // Unsubscribe from events
        if (window.EventBus) {
            EventBus.off('sse:connected', handleConnected);
            EventBus.off('sse:disconnected', handleDisconnected);
            EventBus.off('sse:error', handleError);
            EventBus.off('sse:reconnecting', handleReconnecting);
        }

        // Clear timers
        if (state.autoHideTimer) {
            clearTimeout(state.autoHideTimer);
        }

        // Remove DOM
        const indicator = document.getElementById('status-indicator');
        if (indicator) {
            indicator.remove();
        }

        // Clear state
        state.connections.clear();
    }

    // Public API
    return {
        init,
        show,
        hide,
        updateService,
        getStatus,
        destroy,
        STATUS
    };
})();

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = StatusIndicator;
}

// Attach to window for global access
if (typeof window !== 'undefined') {
    window.StatusIndicator = StatusIndicator;
}
