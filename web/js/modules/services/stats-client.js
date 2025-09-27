/**
 * Optimized Stats Client
 * Fetches pre-calculated stats from server without heavy client-side processing.
 * @module StatsClient
 */
const StatsClient = (function() {
    'use strict';

    // Configuration
    const config = {
        baseUrl: '/api/stats',
        cacheTime: 60000, // 1 minute client cache
        retryAttempts: 3,
        retryDelay: 1000
    };

    // Local cache
    let cache = {
        overview: null,
        timestamp: 0
    };

    // State
    let loading = false;
    let listeners = [];

    /**
     * Fetch stats overview from server
     * @param {boolean} force - Force refresh, bypass cache
     * @returns {Promise<Object>} Stats data
     */
    async function getOverview(force = false) {
        // Check client-side cache first
        if (!force && cache.overview && Date.now() - cache.timestamp < config.cacheTime) {
            return cache.overview;
        }

        // Check if already loading
        if (loading) {
            return new Promise((resolve) => {
                listeners.push(() => resolve(cache.overview));
            });
        }

        loading = true;

        try {
            const response = await fetchWithRetry(`${config.baseUrl}/overview${force ? '?force=true' : ''}`);

            if (!response.ok) {
                throw new Error(`Failed to fetch stats: ${response.statusText}`);
            }

            const result = await response.json();

            if (result.success) {
                cache.overview = result.data;
                cache.timestamp = Date.now();

                // Notify listeners
                listeners.forEach(cb => cb());
                listeners = [];

                return result.data;
            } else {
                throw new Error(result.error || 'Unknown error');
            }
        } catch (error) {
            console.error('Error fetching stats:', error);
            throw error;
        } finally {
            loading = false;
        }
    }

    /**
     * Get stats for specific category
     * @param {string} category - Category name
     * @returns {Promise<Object>} Category stats
     */
    async function getCategoryStats(category) {
        const response = await fetchWithRetry(`${config.baseUrl}/category/${encodeURIComponent(category)}`);

        if (!response.ok) {
            throw new Error(`Failed to fetch category stats: ${response.statusText}`);
        }

        const result = await response.json();

        if (result.success) {
            return result.data;
        } else {
            throw new Error(result.error || 'Unknown error');
        }
    }

    /**
     * Get stats for specific tag
     * @param {string} tag - Tag name
     * @returns {Promise<Object>} Tag stats
     */
    async function getTagStats(tag) {
        const response = await fetchWithRetry(`${config.baseUrl}/tag/${encodeURIComponent(tag)}`);

        if (!response.ok) {
            throw new Error(`Failed to fetch tag stats: ${response.statusText}`);
        }

        const result = await response.json();

        if (result.success) {
            return result.data;
        } else {
            throw new Error(result.error || 'Unknown error');
        }
    }

    /**
     * Get recent activity (paginated)
     * @param {number} page - Page number
     * @param {number} size - Page size
     * @returns {Promise<Object>} Recent activity data
     */
    async function getRecentActivity(page = 0, size = 50) {
        const params = new URLSearchParams({ page, size });
        const response = await fetchWithRetry(`${config.baseUrl}/recent?${params}`);

        if (!response.ok) {
            throw new Error(`Failed to fetch recent activity: ${response.statusText}`);
        }

        const result = await response.json();

        if (result.success) {
            return result.data;
        } else {
            throw new Error(result.error || 'Unknown error');
        }
    }

    /**
     * Fetch with retry logic
     * @private
     */
    async function fetchWithRetry(url, options = {}, attempt = 1) {
        try {
            return await fetch(url, options);
        } catch (error) {
            if (attempt < config.retryAttempts) {
                await new Promise(resolve => setTimeout(resolve, config.retryDelay * attempt));
                return fetchWithRetry(url, options, attempt + 1);
            }
            throw error;
        }
    }

    /**
     * Render stats overview in DOM
     * @param {string} containerId - Container element ID
     */
    async function renderOverview(containerId) {
        const container = document.getElementById(containerId);
        if (!container) {
            console.error('Container not found:', containerId);
            return;
        }

        // Show loading state
        container.innerHTML = '<div class="loading">Loading statistics...</div>';

        try {
            const stats = await getOverview();

            // Render stats - lightweight client-side rendering only
            container.innerHTML = `
                <div class="stats-overview">
                    <div class="stats-grid">
                        <div class="stat-card">
                            <h3>Total Prompts</h3>
                            <div class="stat-value">${stats.totalPrompts.toLocaleString()}</div>
                        </div>
                        <div class="stat-card">
                            <h3>Total Images</h3>
                            <div class="stat-value">${stats.totalImages.toLocaleString()}</div>
                        </div>
                        <div class="stat-card">
                            <h3>Sessions</h3>
                            <div class="stat-value">${stats.totalSessions.toLocaleString()}</div>
                        </div>
                        <div class="stat-card">
                            <h3>Recent Activity</h3>
                            <div class="stat-value">${stats.recent_activity?.last_30_days || 0}</div>
                            <div class="stat-label">Last 30 days</div>
                        </div>
                    </div>

                    <div class="stats-categories">
                        <h3>Top Categories</h3>
                        <ul class="category-list">
                            ${Object.entries(stats.category_breakdown || {})
                                .slice(0, 10)
                                .map(([cat, count]) => `
                                    <li>
                                        <span class="category-name">${cat}</span>
                                        <span class="category-count">${count}</span>
                                    </li>
                                `).join('')}
                        </ul>
                    </div>

                    <div class="stats-updated">
                        Last updated: ${new Date(stats.last_updated).toLocaleString()}
                    </div>
                </div>
            `;
        } catch (error) {
            container.innerHTML = `
                <div class="error">
                    <p>Failed to load statistics</p>
                    <button onclick="StatsClient.refresh('${containerId}')">Retry</button>
                </div>
            `;
        }
    }

    /**
     * Force refresh stats
     * @param {string} containerId - Container element ID
     */
    async function refresh(containerId) {
        cache.overview = null;
        cache.timestamp = 0;
        await renderOverview(containerId);
    }

    /**
     * Initialize stats client
     * @param {Object} options - Configuration options
     */
    function init(options = {}) {
        Object.assign(config, options);

        // Auto-refresh every 5 minutes if configured
        if (options.autoRefresh) {
            setInterval(() => {
                getOverview(true).catch(console.error);
            }, 300000);
        }
    }

    // Public API
    return {
        init,
        getOverview,
        getCategoryStats,
        getTagStats,
        getRecentActivity,
        renderOverview,
        refresh
    };
})();

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = StatsClient;
}

// Attach to window for global access
if (typeof window !== 'undefined') {
    window.StatsClient = StatsClient;
}