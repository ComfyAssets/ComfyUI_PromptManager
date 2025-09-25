/**
 * Footer Status Component
 * Compact SSE status indicators for footer placement
 * @module FooterStatus
 */
const FooterStatus = (function() {
    'use strict';

    function isPromptManagerContext() {
        if (typeof window === 'undefined') {
            return false;
        }
        const path = window.location?.pathname || '';
        return path.includes('/prompt_manager');
    }

    // Configuration
    const config = {
        showText: true,
        showCount: true,
        enableTooltips: true,
        refreshInterval: 30000 // 30 seconds
    };

    // State
    let state = {
        connections: new Map(),
        lastUpdate: null
    };

    /**
     * Initialize footer status
     */
    function init(options = {}) {
        if (!isPromptManagerContext()) {
            return this;
        }
        Object.assign(config, options);
        createFooterStatus();
        subscribeToEvents();
        return this;
    }

    /**
     * Create footer status elements
     */
    function createFooterStatus() {
        // Add styles
        if (!document.getElementById('footer-status-styles')) {
            const styles = document.createElement('style');
            styles.id = 'footer-status-styles';
            styles.textContent = getFooterStyles();
            document.head.appendChild(styles);
        }

        // Find existing footer and modify it
        const footer = document.querySelector('.main-footer');
        if (!footer) {
            console.warn('[FooterStatus] .main-footer not found; skipping footer status initialisation');
            return;
        }

        // Find or create footer content
        let footerContent = footer.querySelector('.footer-content');
        if (!footerContent) {
            footerContent = document.createElement('div');
            footerContent.className = 'footer-content';
            footer.appendChild(footerContent);
        }

        // Add status section to existing footer content
        const statusSection = document.createElement('div');
        statusSection.className = 'footer-status';
        statusSection.innerHTML = `
            <div class="footer-status-indicators">
                <div class="footer-status-item" data-service="overall">
                    <span class="footer-status-icon">○</span>
                    <span class="footer-status-text">Connecting...</span>
                </div>
            </div>
            <div class="footer-status-info">
                <span class="footer-status-count">0 services</span>
                <span class="footer-status-timestamp">--:--</span>
            </div>
        `;

        // Insert status section as the first child (before text and links)
        footerContent.insertBefore(statusSection, footerContent.firstChild);
    }

    /**
     * Get CSS styles for footer status
     */
    function getFooterStyles() {
        return `
            /* Update existing footer to include status */
            .main-footer .footer-content {
                display: flex;
                justify-content: space-between;
                align-items: center;
                gap: 16px;
                flex-wrap: wrap;
            }

            .footer-status {
                display: flex;
                align-items: center;
                gap: 12px;
                order: -1; /* Show first in flex layout */
            }

            .footer-status-indicators {
                display: flex;
                align-items: center;
                gap: 8px;
            }

            .footer-status-item {
                display: flex;
                align-items: center;
                gap: 6px;
                padding: 2px 6px;
                border-radius: 3px;
                transition: background 0.2s;
                cursor: pointer;
            }

            .footer-status-item:hover {
                background: #1a1a1a;
            }

            .footer-status-icon {
                width: 8px;
                height: 8px;
                border-radius: 50%;
                display: inline-block;
                position: relative;
            }

            .footer-status-icon.connected {
                background: #00cc66;
            }

            .footer-status-icon.connecting {
                background: #ffaa00;
                animation: pulse 1.5s ease-out infinite;
            }

            .footer-status-icon.disconnected {
                background: #cc0000;
            }

            .footer-status-icon.error {
                background: #cc0000;
                animation: pulse 1.5s ease-out infinite;
            }

            .footer-status-icon.reconnecting {
                background: #ffaa00;
                animation: spin 1s linear infinite;
            }

            @keyframes pulse {
                0% { opacity: 1; transform: scale(1); }
                50% { opacity: 0.7; transform: scale(1.2); }
                100% { opacity: 1; transform: scale(1); }
            }

            @keyframes spin {
                from { transform: rotate(0deg); }
                to { transform: rotate(360deg); }
            }

            .footer-status-text {
                color: #888;
                font-size: 11px;
                white-space: nowrap;
            }

            .footer-status-text.connected {
                color: #00cc66;
            }

            .footer-status-text.error {
                color: #cc0000;
            }

            .footer-status-info {
                display: flex;
                align-items: center;
                gap: 12px;
                color: #666;
                font-size: 11px;
            }

            .footer-status-count {
                color: #888;
            }

            .footer-status-timestamp {
                color: #666;
                font-family: monospace;
            }

            /* Compact mode for smaller screens */
            @media (max-width: 768px) {
                .footer-status-text {
                    display: none;
                }

                .footer-status-indicators {
                    gap: 8px;
                }

                .footer-status-info {
                    gap: 8px;
                }
            }

            /* Tooltip styles */
            .footer-status-item[title] {
                position: relative;
            }

            /* Responsive layout for footer */
            @media (max-width: 768px) {
                .main-footer .footer-content {
                    flex-direction: column;
                    align-items: flex-start;
                    gap: 8px;
                }

                .footer-status {
                    order: 0;
                }
            }
        `;
    }

    /**
     * Subscribe to SSE events
     */
    function subscribeToEvents() {
        if (window.EventBus) {
            EventBus.on('sse:connected', handleConnected);
            EventBus.on('sse:disconnected', handleDisconnected);
            EventBus.on('sse:error', handleError);
            EventBus.on('sse:reconnecting', handleReconnecting);
        }
    }

    /**
     * Update service status
     */
    function updateService(serviceName, status, details = null) {
        state.connections.set(serviceName, { status, details, timestamp: Date.now() });
        state.lastUpdate = Date.now();
        render();
    }

    /**
     * Render footer status
     */
    function render() {
        const indicators = document.querySelector('.footer-status-indicators');
        const countEl = document.querySelector('.footer-status-count');
        const timestampEl = document.querySelector('.footer-status-timestamp');

        if (!indicators) return;

        // Determine overall status
        const statuses = Array.from(state.connections.values());
        const overallStatus = getOverallStatus(statuses);

        // Update main indicator
        indicators.innerHTML = `
            <div class="footer-status-item" data-service="overall" title="Overall connection status">
                <span class="footer-status-icon ${overallStatus}"></span>
                <span class="footer-status-text ${overallStatus}">${getStatusText(overallStatus)}</span>
            </div>
        `;

        // Add individual service indicators if needed
        if (state.connections.size > 1) {
            state.connections.forEach((info, serviceName) => {
                const item = document.createElement('div');
                item.className = 'footer-status-item';
                item.setAttribute('data-service', serviceName);
                item.setAttribute('title', `${formatServiceName(serviceName)}: ${getStatusText(info.status)}`);
                item.innerHTML = `
                    <span class="footer-status-icon ${info.status}"></span>
                    <span class="footer-status-text">${formatServiceName(serviceName)}</span>
                `;
                indicators.appendChild(item);
            });
        }

        // Update count and timestamp
        if (countEl) {
            countEl.textContent = `${state.connections.size} service${state.connections.size !== 1 ? 's' : ''}`;
        }

        if (timestampEl && state.lastUpdate) {
            const time = new Date(state.lastUpdate);
            timestampEl.textContent = `${time.getHours().toString().padStart(2, '0')}:${time.getMinutes().toString().padStart(2, '0')}`;
        }
    }

    /**
     * Get overall status from individual statuses
     */
    function getOverallStatus(statuses) {
        if (statuses.length === 0) return 'disconnected';

        if (statuses.some(s => s.status === 'error')) return 'error';
        if (statuses.some(s => s.status === 'reconnecting')) return 'reconnecting';
        if (statuses.some(s => s.status === 'connecting')) return 'connecting';
        if (statuses.some(s => s.status === 'disconnected')) return 'disconnected';
        if (statuses.every(s => s.status === 'connected')) return 'connected';

        return 'disconnected';
    }

    /**
     * Get status text
     */
    function getStatusText(status) {
        const texts = {
            connected: 'Connected',
            connecting: 'Connecting...',
            disconnected: 'Disconnected',
            error: 'Error',
            reconnecting: 'Reconnecting...'
        };
        return texts[status] || 'Unknown';
    }

    /**
     * Format service name
     */
    function formatServiceName(name) {
        return name.charAt(0).toUpperCase() + name.slice(1).replace(/-/g, ' ');
    }

    /**
     * Event handlers
     */
    function handleConnected(data) {
        updateService(data.service || 'main', 'connected');
    }

    function handleDisconnected(data) {
        updateService(data.service || 'main', 'disconnected');
    }

    function handleError(data) {
        updateService(data.service || 'main', 'error', data.message);
    }

    function handleReconnecting(data) {
        updateService(data.service || 'main', 'reconnecting');
    }

    /**
     * Get current status
     */
    function getStatus() {
        return {
            services: Object.fromEntries(state.connections),
            lastUpdate: state.lastUpdate
        };
    }

    /**
     * Destroy the component
     */
    function destroy() {
        // Unsubscribe from events
        if (window.EventBus) {
            EventBus.off('sse:connected', handleConnected);
            EventBus.off('sse:disconnected', handleDisconnected);
            EventBus.off('sse:error', handleError);
            EventBus.off('sse:reconnecting', handleReconnecting);
        }

        // Remove status section from existing footer
        const statusSection = document.querySelector('.footer-status');
        if (statusSection) {
            statusSection.remove();
        }

        // Clear state
        state.connections.clear();
    }

    // Public API
    return {
        init,
        updateService,
        getStatus,
        destroy
    };
})();

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = FooterStatus;
}

// Attach to window for global access
if (typeof window !== 'undefined') {
    window.FooterStatus = FooterStatus;
}
