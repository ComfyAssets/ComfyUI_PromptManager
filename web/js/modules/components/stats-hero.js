/**
 * Stats Hero Component
 * Clean, modern stats display with automatic updates
 * @module StatsHero
 */
const StatsHero = (function() {
    'use strict';

    const config = {
        updateInterval: 30000, // 30 seconds
        animationDuration: 500,
        sparklinePoints: 20
    };

    let container = null;
    let updateTimer = null;
    let lastStats = {};

    /**
     * Initialize the stats hero component
     * @param {string} containerId - Container element ID
     * @param {Object} options - Configuration options
     */
    function init(containerId, options = {}) {
        Object.assign(config, options);
        container = document.getElementById(containerId);

        if (!container) {
            console.error('Stats hero container not found:', containerId);
            return;
        }

        render();
        startAutoUpdate();
    }

    /**
     * Render the stats hero component
     */
    function render() {
        // Grid layout with 4 stats side-by-side
        container.innerHTML = `
            <div class="stats-hero">
                <div class="hero-stat" data-stat="prompts">
                    <div class="stat-value loading" id="totalPromptsValue">--</div>
                    <div class="stat-label">Total Prompts</div>
                    <div class="stat-trend" id="promptsTrend"></div>
                </div>

                <div class="hero-stat" data-stat="images">
                    <div class="stat-value loading" id="totalImagesValue">--</div>
                    <div class="stat-label">Images Generated</div>
                    <div class="stat-sparkline" id="imageSparkline"></div>
                </div>

                <div class="hero-stat streak" data-stat="streak">
                    <div class="stat-value loading" id="currentStreakValue">--</div>
                    <div class="stat-label">Day Streak 🔥</div>
                    <div class="stat-subtext" id="streakSubtext"></div>
                </div>

                <div class="hero-stat" data-stat="innovation">
                    <div class="stat-value loading" id="innovationScore">--</div>
                    <div class="stat-label">Innovation Index</div>
                    <div class="stat-progress">
                        <div class="progress-fill" id="innovationProgress" style="width: 0%"></div>
                    </div>
                </div>
            </div>
        `;

        // Load initial stats
        updateStats();
    }

    /**
     * Update stats from the server
     */
    async function updateStats() {
        try {
            const response = await fetch('/api/v1/stats/overview');
            if (!response.ok) throw new Error('Failed to fetch stats');

            const data = await response.json();
            if (data.success) {
                displayStats(data.data);
            }
        } catch (error) {
            console.error('Error updating stats:', error);
        }
    }

    /**
     * Display stats in the UI
     */
    function displayStats(stats) {
        // Update prompts
        updateValue('totalPromptsValue', stats.totalPrompts, true);
        updateTrend('promptsTrend', stats.recentActivity?.prompts_24h || 0);

        // Update images
        updateValue('totalImagesValue', stats.totalImages, true);
        updateSparkline('imageSparkline', stats.imageHistory || []);

        // Calculate and update streak
        const streak = calculateStreak(stats);
        updateValue('currentStreakValue', streak.current);
        updateElement('streakSubtext', `Best: ${streak.best} days`);

        // Calculate and update innovation score
        const innovation = calculateInnovation(stats);
        updateValue('innovationScore', `${innovation}%`);
        updateProgressBar('innovationProgress', innovation);

        lastStats = stats;
    }

    /**
     * Update a single value with animation
     */
    function updateValue(elementId, value, format = false) {
        const element = document.getElementById(elementId);
        if (!element) return;

        // Remove loading state
        element.classList.remove('loading');

        // Format number if needed
        const displayValue = format && typeof value === 'number'
            ? value.toLocaleString()
            : value;

        // Animate if value changed
        if (element.textContent !== displayValue.toString()) {
            element.classList.add('updating');
            element.textContent = displayValue;
            setTimeout(() => element.classList.remove('updating'), config.animationDuration);
        }
    }

    /**
     * Update trend indicator
     */
    function updateTrend(elementId, value) {
        const element = document.getElementById(elementId);
        if (!element) return;

        if (value > 0) {
            element.className = 'stat-trend positive';
            element.textContent = `+${value} today`;
        } else if (value < 0) {
            element.className = 'stat-trend negative';
            element.textContent = `${value} today`;
        } else {
            element.className = 'stat-trend neutral';
            element.textContent = 'No change';
        }
    }

    /**
     * Update sparkline chart
     */
    function updateSparkline(elementId, data) {
        const element = document.getElementById(elementId);
        if (!element || !data.length) return;

        const width = element.offsetWidth || 200;
        const height = 40;
        const points = data.slice(-config.sparklinePoints);

        // Calculate min/max for scaling
        const max = Math.max(...points);
        const min = Math.min(...points);
        const range = max - min || 1;

        // Generate SVG points
        const svgPoints = points.map((value, index) => {
            const x = (index / (points.length - 1)) * width;
            const y = height - ((value - min) / range * height);
            return `${x},${y}`;
        }).join(' ');

        element.innerHTML = `
            <svg width="${width}" height="${height}" style="display: block;">
                <polyline points="${svgPoints}"
                          fill="none"
                          stroke="#0066cc"
                          stroke-width="2"
                          opacity="0.8"/>
            </svg>
        `;
    }

    /**
     * Update progress bar
     */
    function updateProgressBar(elementId, percentage) {
        const element = document.getElementById(elementId);
        if (!element) return;

        element.style.width = `${Math.min(100, Math.max(0, percentage))}%`;
    }

    /**
     * Update generic element
     */
    function updateElement(elementId, content) {
        const element = document.getElementById(elementId);
        if (element) element.textContent = content;
    }

    /**
     * Calculate current streak
     */
    function calculateStreak(stats) {
        // This would analyze daily activity to calculate streak
        // For now, return mock data
        const recent = stats.recentActivity || {};
        const hasActivity = recent.prompts_24h > 0 || recent.images_24h > 0;

        return {
            current: hasActivity ? 6 : 0,
            best: 6
        };
    }

    /**
     * Calculate innovation index
     */
    function calculateInnovation(stats) {
        // Innovation score based on:
        // - Unique categories used
        // - Tag diversity
        // - New prompts vs regenerations
        // For now, return a calculated percentage

        const categories = Object.keys(stats.categoryBreakdown || {}).length;
        const tags = Object.keys(stats.tagFrequency || {}).length;

        // Simple formula: more diversity = higher innovation
        const score = Math.min(100, (categories * 5) + (tags * 2));
        return Math.round(score);
    }

    /**
     * Start automatic updates
     */
    function startAutoUpdate() {
        stopAutoUpdate(); // Clear any existing timer

        if (config.updateInterval > 0) {
            updateTimer = setInterval(updateStats, config.updateInterval);
        }
    }

    /**
     * Stop automatic updates
     */
    function stopAutoUpdate() {
        if (updateTimer) {
            clearInterval(updateTimer);
            updateTimer = null;
        }
    }

    /**
     * Destroy the component
     */
    function destroy() {
        stopAutoUpdate();
        if (container) {
            container.innerHTML = '';
        }
        container = null;
        lastStats = {};
    }

    // Public API
    return {
        init,
        render,
        updateStats,
        startAutoUpdate,
        stopAutoUpdate,
        destroy
    };
})();

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = StatsHero;
}

// Attach to window for global access
if (typeof window !== 'undefined') {
    window.StatsHero = StatsHero;
}