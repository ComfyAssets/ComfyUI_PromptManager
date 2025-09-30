/**
 * Epic Stats Module - Creative Data Mining and Visualizations
 * Extracts insights from prompts, images, and generation patterns
 */

const EpicStats = (function() {
    'use strict';

    function createStub() {
        return {
            init: () => {},
            loadAllStats: async () => {},
            renderDashboard: () => {},
            exportStats: () => {},
            getStatsData: () => ({}),
        };
    }

    if (typeof window === 'undefined') {
        return createStub();
    }
    const pmActive = (window.location?.pathname || '').includes('/prompt_manager');
    if (!pmActive) {
        console.info('[PromptManager] stats-epic skipped outside PromptManager UI context');
        return createStub();
    }

    let statsData = {};
    let charts = {};
    const SETTINGS_STORAGE_KEY = 'promptManagerSettings';
    const IGNORED_WORDS_KEY = 'statsIgnoredWords';
    let statsIgnoredWords = [];
    let ignoredWordLookup = new Set();
    let lastIgnoreEvent = { key: null, timestamp: 0 };
    const loadingBanner = document.querySelector('[data-stats-progress]');
    const loadingTitle = loadingBanner?.querySelector('[data-stats-progress-text]');
    const loadingDetail = loadingBanner?.querySelector('[data-stats-progress-detail]');
    const loadingSpinner = loadingBanner?.querySelector('[data-stats-progress-spinner]');
    let loadingSlowTimer = null;

    // Probe multiple API prefixes so the stats page works behind ComfyUI proxies
    const API_BASE_CANDIDATES = ['/api/v1', '/api/prompt_manager'];
    let apiBase = null;

    const DEFAULT_PAGE_SIZE = 500;
    const MAX_PAGE_FETCHES = 50;

    const LOADING_COPY = {
        starting: {
            title: 'Preparing analytics…',
            detail: 'Crunching historical data for the first view. Large libraries may take a minute.',
        },
        remote: {
            title: 'Fetching aggregated snapshot…',
            detail: 'Processing analytics on the server.',
        },
        refresh: {
            title: 'Refreshing analytics…',
            detail: 'Recomputing totals using the latest prompts.',
        },
        fallback: {
            title: 'Crunching data locally…',
            detail: 'Server snapshot unavailable; building charts in the browser. This can take longer on big datasets.',
        },
        slow: {
            title: 'Still working…',
            detail: 'We are nearly there. First runs over large histories can take up to a few minutes.',
        },
        done: {
            title: 'Analytics ready',
            detail: '',
        },
        error: {
            title: 'Analytics failed',
            detail: 'Tap refresh to try again, or check the server logs for details.',
        },
    };

    function safeDivide(numerator, denominator) {
        if (!denominator) {
            return 0;
        }
        const result = numerator / denominator;
        return Number.isFinite(result) ? result : 0;
    }

    function safePercentage(value, total, precision = 1) {
        if (!total) {
            return (0).toFixed(precision);
        }
        const result = (value / total) * 100;
        return Number.isFinite(result) ? result.toFixed(precision) : (0).toFixed(precision);
    }

    function safeFixed(value, precision = 1) {
        if (!Number.isFinite(value)) {
            return (0).toFixed(precision);
        }
        return value.toFixed(precision);
    }

    async function fetchJson(path, options = {}) {
        const method = (options.method || 'GET').toUpperCase();
        let candidates;

        if (apiBase) {
            const remaining = API_BASE_CANDIDATES.filter((candidate) => candidate !== apiBase);
            candidates = method === 'GET' ? [apiBase, ...remaining] : [apiBase];
        } else {
            candidates = [...API_BASE_CANDIDATES];
        }

        let lastError = null;

        for (const candidate of candidates) {
            const url = `${candidate}${path}`;
            try {
                const response = await fetch(url, options);
                if (!response.ok) {
                    let errorMessage = `${response.status} ${response.statusText}`;
                    try {
                        const errorBody = await response.json();
                        const serverMessage = errorBody?.error || errorBody?.message;
                        if (serverMessage) {
                            errorMessage = `${response.status} ${serverMessage}`;
                        }
                    } catch (_parseError) {
                        try {
                            const text = await response.text();
                            if (text) {
                                errorMessage = `${response.status} ${text}`;
                            }
                        } catch (_textError) {
                            // Ignore parsing issues so we can fall back to the next candidate
                        }
                    }

                    lastError = new Error(errorMessage);
                    if (method !== 'GET') {
                        break;
                    }
                    continue;
                }

                const payload = await response.json();
                apiBase = candidate;
                return payload;
            } catch (error) {
                lastError = error;
            }
        }

        throw lastError || new Error(`Unable to reach PromptManager API (${path})`);
    }

    function buildQueryPath(path, params) {
        const [base, query = ''] = path.split('?');
        const search = new URLSearchParams(query);

        Object.entries(params).forEach(([key, value]) => {
            if (value === undefined || value === null) {
                search.delete(key);
            } else {
                search.set(key, String(value));
            }
        });

        const queryString = search.toString();
        return queryString ? `${base}?${queryString}` : base;
    }

    function setLoadingStage(stage, detailOverride) {
        if (!loadingBanner) {
            return;
        }

        if (loadingSlowTimer) {
            clearTimeout(loadingSlowTimer);
            loadingSlowTimer = null;
        }

        if (stage === 'done') {
            loadingBanner.classList.add('is-hidden');
            loadingBanner.classList.remove('has-error');
            return;
        }

        const copy = LOADING_COPY[stage] || LOADING_COPY.starting;
        const title = copy.title;
        const detail = detailOverride ?? copy.detail;

        loadingBanner.classList.remove('is-hidden');
        if (stage === 'error') {
            loadingBanner.classList.add('has-error');
        } else {
            loadingBanner.classList.remove('has-error');
        }

        if (loadingTitle) {
            loadingTitle.textContent = title;
        }
        if (loadingDetail) {
            loadingDetail.textContent = detail;
            loadingDetail.style.display = detail ? 'block' : 'none';
        }
        if (loadingSpinner) {
            loadingSpinner.style.display = stage === 'error' ? 'none' : 'inline-block';
        }

        if (['remote', 'refresh', 'fallback'].includes(stage)) {
            loadingSlowTimer = setTimeout(() => {
                if (loadingBanner.classList.contains('is-hidden')) {
                    return;
                }
                if (loadingTitle) {
                    loadingTitle.textContent = LOADING_COPY.slow.title;
                }
                if (loadingDetail) {
                    loadingDetail.textContent = LOADING_COPY.slow.detail;
                    loadingDetail.style.display = 'block';
                }
            }, 9000);
        }
    }

    async function fetchStatsOverview(options = {}) {
        const { force = false } = options;
        // Try to fetch epic stats first
        try {
            const epicPath = buildQueryPath('/stats/epic', force ? { force: '1' } : {});
            const epicPayload = await fetchJson(epicPath);

            if (epicPayload && epicPayload.success && epicPayload.data) {
                // Transform epic stats to match expected format
                const data = epicPayload.data;
                return {
                    totalPrompts: data.hero_stats.total_prompts || 0,
                    totalImages: data.hero_stats.total_images || 0,
                    totalRated: data.hero_stats.rated_count || 0,
                    avgRating: data.hero_stats.avg_rating || 0,
                    fiveStarCount: data.hero_stats.five_star_count || 0,
                    totalCollections: data.hero_stats.total_collections || 0,
                    imagesPerPrompt: data.hero_stats.images_per_prompt || 0,
                    generationStreak: data.hero_stats.generation_streak || 0,
                    generation_analytics: data.generation_analytics,
                    time_patterns: data.time_patterns,
                    model_usage: data.model_usage,
                    quality_metrics: data.quality_metrics,
                    rating_trends: data.rating_trends,
                    calculated_at: data.calculated_at,
                    generatedAt: data.calculated_at
                };
            }
        } catch (epicError) {
            console.log('Epic stats not available, trying overview endpoint', epicError);
        }

        // Fallback to original overview endpoint
        const path = buildQueryPath('/stats/overview', force ? { force: '1' } : {});
        const payload = await fetchJson(path);

        if (payload && payload.success && payload.data) {
            return payload.data;
        }

        const message = payload?.error || 'Aggregated statistics unavailable';
        throw new Error(message);
    }

    function applyStatsSnapshot(snapshot) {
        const baseSnapshot = snapshot && typeof snapshot === 'object' ? { ...snapshot } : {};
        const totals = baseSnapshot.totals || {};

        statsData = baseSnapshot;
        statsData.totalPrompts = statsData.totalPrompts ?? totals.prompts ?? 0;
        statsData.totalImages = statsData.totalImages ?? totals.images ?? 0;
        statsData.totalSessions = statsData.totalSessions ?? totals.sessions ?? 0;

        const promptPatterns = statsData.promptPatterns = statsData.promptPatterns || {};
        promptPatterns.topWords = promptPatterns.topWords || {};
        promptPatterns.ignoredWords = [...statsIgnoredWords];

        // Transform epic stats data to expected format
        if (snapshot.time_patterns && Array.isArray(snapshot.time_patterns)) {
            // Create hourlyActivity array from time_patterns
            const hourlyActivity = new Array(24).fill(0);
            snapshot.time_patterns.forEach(pattern => {
                if (pattern.hour >= 0 && pattern.hour < 24) {
                    hourlyActivity[pattern.hour] = pattern.generations || 0;
                }
            });

            // Calculate peak hours
            const hourlyMax = Math.max(...hourlyActivity);
            const peakHours = [];
            if (hourlyMax > 0) {
                hourlyActivity.forEach((count, hour) => {
                    if (count > hourlyMax * 0.5) { // Consider hours with >50% of max as peak
                        peakHours.push({
                            hour,
                            count,
                            percentage: safeFixed((count / hourlyMax) * 100, 1)
                        });
                    }
                });
            }

            statsData.timeAnalytics = statsData.timeAnalytics || {};
            statsData.timeAnalytics.hourlyActivity = hourlyActivity;
            statsData.timeAnalytics.peakHours = peakHours;
            statsData.timeAnalytics.currentStreak = snapshot.generationStreak || 0;
            statsData.timeAnalytics.longestStreak = snapshot.generationStreak || 0; // TODO: track separately
        }

        // Transform quality metrics
        if (snapshot.quality_metrics) {
            statsData.qualityMetrics = {
                ...snapshot.quality_metrics,
                innovationIndex: Math.min(100, Math.round((snapshot.quality_metrics.avg_quality_score || 0) * 20)) // Convert 0-5 to 0-100
            };
        }

        // Transform model usage for charts
        if (snapshot.model_usage && Array.isArray(snapshot.model_usage)) {
            statsData.modelUsage = snapshot.model_usage;
        }

        // Transform resolution data
        if (snapshot.quality_metrics && snapshot.quality_metrics.top_resolutions) {
            statsData.resolutionDistribution = snapshot.quality_metrics.top_resolutions;
        }

        if (!statsData.generatedAt) {
            statsData.generatedAt = new Date().toISOString();
        }
    }

    function dedupeItems(items, keySelector) {
        const seen = new Set();
        const result = [];

        items.forEach((item, index) => {
            const key = keySelector(item, index);
            if (key && seen.has(key)) {
                return;
            }
            if (key) {
                seen.add(key);
            }
            result.push(item);
        });

        return result;
    }

    async function fetchPaginatedCollection(path, extractKeys, options = {}) {
        const perPage = options.perPage ?? DEFAULT_PAGE_SIZE;
        const maxPages = options.maxPages ?? MAX_PAGE_FETCHES;
        const keySelector = options.keySelector || ((item) => item?.id || item?.uuid || item?.image_id || item?.hash || null);

        const results = [];
        const seen = new Set();
        let page = options.startPage ?? 1;

        while (page <= maxPages) {
            const requestPath = buildQueryPath(path, { per_page: perPage, page });
            let payload;

            try {
                payload = await fetchJson(requestPath);
            } catch (error) {
                console.error(`Failed to fetch ${requestPath}:`, error);
                break;
            }

            const items = extractArray(payload, extractKeys) || [];
            if (!items.length) {
                break;
            }

            items.forEach((item, index) => {
                const key = keySelector(item, index) || `${page}-${index}`;
                if (seen.has(key)) {
                    return;
                }
                seen.add(key);
                results.push(item);
            });

            const pagination = payload?.pagination
                || payload?.meta?.pagination
                || payload?.data?.pagination
                || null;

            if (pagination) {
                const current = Number(pagination.page ?? pagination.current_page ?? page);
                const totalPages = Number(pagination.total_pages ?? pagination.pages ?? 0);
                const nextPage = pagination.next_page ?? pagination.nextPage ?? null;

                if ((totalPages && current >= totalPages) || (!nextPage && current >= (totalPages || current))) {
                    break;
                }

                page = Number(nextPage) || current + 1;
            } else if (items.length < perPage) {
                break;
            } else {
                page += 1;
            }
        }

        return results;
    }

    function extractArray(payload, keys = []) {
        if (!payload) {
            return [];
        }

        if (Array.isArray(payload)) {
            return payload;
        }

        if (typeof payload !== 'object') {
            return [];
        }

        const searchKeys = keys.length ? keys : ['items'];

        for (const key of searchKeys) {
            const value = payload[key];
            if (Array.isArray(value)) {
                return value;
            }
        }

        if (payload.data) {
            return extractArray(payload.data, keys);
        }

        if (Array.isArray(payload.results)) {
            return payload.results;
        }

        return [];
    }

    function normalizeTags(value) {
        if (!value) {
            return [];
        }

        if (Array.isArray(value)) {
            return value
                .map((entry) => (entry == null ? '' : String(entry)))
                .map((entry) => entry.trim())
                .filter(Boolean);
        }

        if (typeof value === 'string') {
            const trimmed = value.trim();
            if (!trimmed) {
                return [];
            }

            if ((trimmed.startsWith('[') && trimmed.endsWith(']')) || (trimmed.startsWith('{') && trimmed.endsWith('}'))) {
                try {
                    const parsed = JSON.parse(trimmed);
                    return normalizeTags(parsed);
                } catch (_jsonError) {
                    // Fall back to comma splitting below when JSON parsing fails
                }
            }

            return trimmed
                .split(',')
                .map((entry) => entry.trim())
                .filter(Boolean);
        }

        if (typeof value === 'object') {
            if (Array.isArray(value.tags)) {
                return normalizeTags(value.tags);
            }
            if (Array.isArray(value.items)) {
                return normalizeTags(value.items);
            }
            return normalizeTags(Object.values(value));
        }

        return [String(value).trim()].filter(Boolean);
    }

    function normalizeWord(value) {
        if (value == null) {
            return '';
        }
        return String(value).trim().toLowerCase();
    }

    function loadSettingsObject() {
        try {
            const stored = localStorage.getItem(SETTINGS_STORAGE_KEY);
            if (!stored) {
                return {};
            }
            const parsed = JSON.parse(stored);
            return parsed && typeof parsed === 'object' ? parsed : {};
        } catch (error) {
            console.error('Failed to parse settings from localStorage:', error);
            return {};
        }
    }

    function setIgnoredWords(words) {
        const cleaned = [];
        const seen = new Set();

        words.forEach((entry) => {
            const original = typeof entry === 'string' ? entry.trim() : String(entry || '').trim();
            const normalized = normalizeWord(original);
            if (!normalized || seen.has(normalized)) {
                return;
            }
            seen.add(normalized);
            cleaned.push(original || normalized);
        });

        cleaned.sort((a, b) => a.localeCompare(b));
        statsIgnoredWords = cleaned;
        ignoredWordLookup = new Set(cleaned.map(normalizeWord));
    }

    function loadIgnoredWordsFromStorage() {
        const settings = loadSettingsObject();
        const stored = settings[IGNORED_WORDS_KEY];

        if (Array.isArray(stored)) {
            return stored;
        }

        if (typeof stored === 'string' && stored.trim()) {
            return stored
                .split(',')
                .map((entry) => entry.trim())
                .filter(Boolean);
        }

        return [];
    }

    function persistIgnoredWords() {
        try {
            const settings = loadSettingsObject();
            settings[IGNORED_WORDS_KEY] = [...statsIgnoredWords];
            localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(settings));
            emitIgnoredWordsUpdate();
        } catch (error) {
            console.error('Failed to persist ignored words:', error);
        }
    }

    function emitIgnoredWordsUpdate() {
        const payload = { words: [...statsIgnoredWords] };

        if (window.EventBus) {
            try {
                EventBus.emit('stats.ignoredWords.updated', payload);
            } catch (error) {
                console.error('Failed to emit stats.ignoredWords.updated via EventBus', error);
            }
        }

        window.dispatchEvent(new CustomEvent('stats.ignoredWords.updated', { detail: payload }));
    }

    function getNormalizedIgnoredWords(list) {
        return list
            .map((entry) => normalizeWord(entry))
            .filter(Boolean)
            .sort();
    }

    function arraysEqual(a, b) {
        if (a.length !== b.length) {
            return false;
        }
        for (let i = 0; i < a.length; i++) {
            if (a[i] !== b[i]) {
                return false;
            }
        }
        return true;
    }

    function filterIgnoredWords(frequencyMap) {
        if (!frequencyMap) {
            return {};
        }
        const entries = Object.entries(frequencyMap).filter(([word]) => !ignoredWordLookup.has(normalizeWord(word)));
        return Object.fromEntries(entries);
    }

    function initializeIgnoredWords() {
        setIgnoredWords(loadIgnoredWordsFromStorage());
        attachIgnoredWordListeners();
    }

    function attachIgnoredWordListeners() {
        if (attachIgnoredWordListeners._bound) {
            return;
        }

        window.addEventListener('wordcloud.ignore', (event) => {
            handleIgnoreWordRequest(event?.detail);
        });

        window.addEventListener('stats.ignoredWords.updated', (event) => {
            if (event?.detail) {
                syncIgnoredWords(event.detail.words);
            }
        });

        if (window.EventBus) {
            EventBus.on('stats.ignoredWords.updated', (payload) => {
                syncIgnoredWords(payload?.words);
            });
        }

        attachIgnoredWordListeners._bound = true;
    }

    function handleIgnoreWordRequest(detail) {
        const rawWord = detail?.word;
        const label = typeof rawWord === 'string' ? rawWord.trim() : String(rawWord || '').trim();
        const normalized = normalizeWord(label || rawWord);

        if (!normalized) {
            return;
        }

        const now = Date.now();
        if (lastIgnoreEvent.key === normalized && now - lastIgnoreEvent.timestamp < 200) {
            return;
        }
        lastIgnoreEvent = { key: normalized, timestamp: now };

        if (ignoredWordLookup.has(normalized)) {
            return;
        }

        const candidate = label || normalized;
        setIgnoredWords([...statsIgnoredWords, candidate]);
        persistIgnoredWords();
        if (window.NotificationService) {
            window.NotificationService.show(`Ignoring "${candidate}" in stats`, 'info');
        }
        renderWordCloud();
    }

    function syncIgnoredWords(words) {
        if (!Array.isArray(words)) {
            return;
        }

        const sanitized = words
            .map((entry) => (typeof entry === 'string' ? entry.trim() : String(entry || '').trim()))
            .filter(Boolean);

        const incoming = getNormalizedIgnoredWords(sanitized);
        const current = getNormalizedIgnoredWords(statsIgnoredWords);

        if (arraysEqual(incoming, current)) {
            return;
        }

        setIgnoredWords(sanitized);
        renderWordCloud();
    }

    /**
     * Initialize the epic stats module
     */
    async function init() {
        initializeIgnoredWords();
        setLoadingStage('starting');
        await loadAllStats();
        renderDashboard();
        setupEventListeners();
        startRealTimeUpdates();
    }

    /**
     * Load all statistics from the backend
     */
    async function loadAllStats(options = {}) {
        const { force = false, silent = false } = options;

        if (!silent) {
            setLoadingStage(force ? 'refresh' : 'remote');
        }

        try {
            const snapshot = await fetchStatsOverview({ force });
            applyStatsSnapshot(snapshot);
            if (!silent) {
                setLoadingStage('done');
            }
            return true;
        } catch (overviewError) {
            console.warn('Aggregated stats endpoint unavailable, using legacy generator.', overviewError);
            if (!silent) {
                setLoadingStage('fallback');
            }
        }

        try {
            await generateStatsFromData();
            if (!silent) {
                setLoadingStage('done');
            }
            return true;
        } catch (error) {
            console.error('Failed to load stats:', error);
            if (!silent) {
                setLoadingStage('error', error.message);
            }
            const container = document.getElementById('stats-dashboard');
            if (container) {
                container.innerHTML = `
                    <div style="text-align: center; padding: 50px; color: #ff6b6b;">
                        <h2>Failed to load statistics</h2>
                        <p>${error.message}</p>
                        <button onclick=\"location.reload()\" class=\"btn btn-primary\">Retry</button>
                    </div>
                `;
            }
            return false;
        }
    }

    /**
     * Generate statistics from raw data
     */
    async function generateStatsFromData() {
        // Use Promise.allSettled to handle failures gracefully
        const [promptsResult, imagesResult, trackingResult] = await Promise.allSettled([
            fetchPrompts(),
            fetchImages(),
            fetchTracking()
        ]);

        // Extract successful results with fallbacks
        const prompts = promptsResult.status === 'fulfilled' ? promptsResult.value : [];
        const images = imagesResult.status === 'fulfilled' ? imagesResult.value : [];
        const tracking = trackingResult.status === 'fulfilled' ? trackingResult.value : [];

        // Ensure arrays
        const safePrompts = Array.isArray(prompts) ? prompts : [];
        const safeImages = Array.isArray(images) ? images : [];
        const safeTracking = Array.isArray(tracking) ? tracking : [];

        const snapshot = {
            // Core Metrics
            totalPrompts: safePrompts.length,
            totalImages: safeImages.length,
            totalSessions: safeTracking.length > 0 ? new Set(safeTracking.map(t => t.session_id)).size : 0,

            // Time-based Analytics
            timeAnalytics: calculateTimeAnalytics(safeImages, safePrompts),

            // Creative Patterns
            promptPatterns: analyzePromptPatterns(safePrompts),

            // Generation Analytics
            generationMetrics: analyzeGenerationMetrics(safeImages),

            // User Behavior
            userBehavior: analyzeUserBehavior(safeTracking, safePrompts),

            // Model Performance
            modelPerformance: analyzeModelPerformance(safePrompts, safeImages),

            // Quality Metrics
            qualityMetrics: analyzeQualityMetrics(safePrompts, safeImages),

            // Workflow Analysis
            workflowAnalysis: analyzeWorkflows(safeImages, safeTracking),

            // Trends and Predictions
            trends: analyzeTrends(safePrompts, safeImages, safeTracking)
        };

        applyStatsSnapshot(snapshot);
    }

    /**
     * Calculate time-based analytics
     */
    function calculateTimeAnalytics(images, prompts) {
        // Ensure arrays
        if (!Array.isArray(images)) images = [];
        if (!Array.isArray(prompts)) prompts = [];
        const now = new Date();
        const stats = {
            // Productivity by hour of day
            hourlyActivity: new Array(24).fill(0),
            // Activity by day of week
            weekdayActivity: new Array(7).fill(0),
            // Monthly growth
            monthlyGrowth: {},
            // Generation speed over time
            generationSpeed: [],
            // Peak creative hours
            peakHours: [],
            // Longest streak
            longestStreak: 0,
            // Current streak
            currentStreak: 0
        };

        // Process images by time
        images.forEach(img => {
            const date = new Date(img.generation_time || img.created_at);
            stats.hourlyActivity[date.getHours()]++;
            stats.weekdayActivity[date.getDay()]++;

            const monthKey = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
            stats.monthlyGrowth[monthKey] = (stats.monthlyGrowth[monthKey] || 0) + 1;
        });

        // Find peak hours
        const hourlyMax = Math.max(...stats.hourlyActivity);
        if (hourlyMax > 0) {
            stats.peakHours = stats.hourlyActivity
                .map((count, hour) => ({
                    hour,
                    count,
                    percentage: safeFixed((count / hourlyMax) * 100, 1)
                }))
                .filter(h => h.count > hourlyMax * 0.7)
                .sort((a, b) => b.count - a.count);
        } else {
            stats.peakHours = [];
        }

        // Calculate streaks
        const dateSet = new Set(images.map(img => {
            const d = new Date(img.generation_time || img.created_at);
            return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
        }));

        let currentStreak = 0;
        let maxStreak = 0;
        const today = new Date();

        for (let i = 0; i < 365; i++) {
            const checkDate = new Date(today - i * 86400000);
            const key = `${checkDate.getFullYear()}-${checkDate.getMonth()}-${checkDate.getDate()}`;

            if (dateSet.has(key)) {
                currentStreak++;
                maxStreak = Math.max(maxStreak, currentStreak);
            } else if (i < 7) {
                // Allow up to 1 day gap for current streak
                continue;
            } else {
                break;
            }
        }

        stats.currentStreak = currentStreak;
        stats.longestStreak = maxStreak;

        return stats;
    }

    /**
     * Analyze prompt patterns and creativity
     */
    function analyzePromptPatterns(prompts) {
        const patterns = {
            // Most used words
            topWords: {},
            // Style combinations
            stylePatterns: {},
            // Negative prompt patterns
            negativePatterns: {},
            // Complexity scores
            complexityDistribution: { simple: 0, moderate: 0, complex: 0, extreme: 0 },
            // Average prompt length
            avgPromptLength: 0,
            // Unique vocabulary size
            vocabularySize: 0,
            // Most creative prompts (high uniqueness)
            mostCreative: [],
            // Prompt evolution (how prompts change over time)
            evolution: [],
            // Common themes
            themes: {}
        };

        // Check if prompts is an array
        if (!Array.isArray(prompts) || prompts.length === 0) {
            return patterns;
        }

        const allWords = new Set();
        const wordFreq = {};

        prompts.forEach(prompt => {
            // Tokenize and analyze
            const words = (prompt.positive_prompt || '').toLowerCase()
                .split(/[\s,;.]+/)
                .filter(w => w.length > 3);

            words.forEach(word => {
                allWords.add(word);
                wordFreq[word] = (wordFreq[word] || 0) + 1;
            });

            // Complexity analysis
            const complexity = calculateComplexity(prompt.positive_prompt);
            if (complexity < 50) patterns.complexityDistribution.simple++;
            else if (complexity < 100) patterns.complexityDistribution.moderate++;
            else if (complexity < 200) patterns.complexityDistribution.complex++;
            else patterns.complexityDistribution.extreme++;

            // Extract styles
            const styles = extractStyles(prompt.positive_prompt);
            styles.forEach(style => {
                patterns.stylePatterns[style] = (patterns.stylePatterns[style] || 0) + 1;
            });

            // Analyze negative prompts
            if (prompt.negative_prompt) {
                const negWords = prompt.negative_prompt.toLowerCase().split(/[\s,;.]+/);
                negWords.forEach(word => {
                    if (word.length > 2) {
                        patterns.negativePatterns[word] = (patterns.negativePatterns[word] || 0) + 1;
                    }
                });
            }
        });

        // Top words
        patterns.topWords = Object.entries(wordFreq)
            .sort((a, b) => b[1] - a[1])
            .slice(0, 50)
            .reduce((acc, [word, count]) => {
                acc[word] = count;
                return acc;
            }, {});

        patterns.vocabularySize = allWords.size;
        patterns.avgPromptLength = safeDivide(
            prompts.reduce((sum, p) => sum + (p.positive_prompt || '').length, 0),
            prompts.length
        );

        // Find most creative (unique) prompts
        patterns.mostCreative = prompts
            .map(p => ({
                ...p,
                uniqueness: calculateUniqueness(p.positive_prompt, prompts)
            }))
            .sort((a, b) => b.uniqueness - a.uniqueness)
            .slice(0, 10);

        patterns.ignoredWords = [...statsIgnoredWords];

        return patterns;
    }

    /**
     * Analyze generation metrics
     */
    function analyzeGenerationMetrics(images) {
        // Check if images is an array
        if (!Array.isArray(images)) {
            images = [];
        }
        const metrics = {
            // Resolution distribution
            resolutions: {},
            // File size analytics
            avgFileSize: 0,
            totalDiskUsage: 0,
            // Format preferences
            formats: {},
            // Aspect ratios
            aspectRatios: { portrait: 0, landscape: 0, square: 0, ultrawide: 0 },
            // Generation speed (if we have timing data)
            avgGenerationTime: 0,
            // Quality distribution (based on file size as proxy)
            qualityTiers: { low: 0, medium: 0, high: 0, ultra: 0 },
            // Media types
            mediaTypes: { image: 0, video: 0, gif: 0 },
            // Thumbnail stats
            thumbnailCoverage: 0
        };

        let totalSize = 0;
        let thumbnailCount = 0;

        images.forEach(img => {
            // Resolution analysis
            if (img.width && img.height) {
                const res = `${img.width}x${img.height}`;
                metrics.resolutions[res] = (metrics.resolutions[res] || 0) + 1;

                // Aspect ratio
                const ratio = img.width / img.height;
                if (ratio < 0.8) metrics.aspectRatios.portrait++;
                else if (ratio > 1.2 && ratio < 2) metrics.aspectRatios.landscape++;
                else if (ratio >= 0.8 && ratio <= 1.2) metrics.aspectRatios.square++;
                else if (ratio >= 2) metrics.aspectRatios.ultrawide++;
            }

            // File size
            if (img.file_size) {
                totalSize += img.file_size;
                const sizeMB = img.file_size / (1024 * 1024);
                if (sizeMB < 1) metrics.qualityTiers.low++;
                else if (sizeMB < 3) metrics.qualityTiers.medium++;
                else if (sizeMB < 10) metrics.qualityTiers.high++;
                else metrics.qualityTiers.ultra++;
            }

            // Format
            const format = img.format || 'unknown';
            metrics.formats[format] = (metrics.formats[format] || 0) + 1;

            // Media type
            const mediaType = img.media_type || 'image';
            metrics.mediaTypes[mediaType] = (metrics.mediaTypes[mediaType] || 0) + 1;

            // Thumbnails
            if (img.thumbnail_small_path || img.thumbnail_medium_path) {
                thumbnailCount++;
            }
        });

        metrics.avgFileSize = safeDivide(totalSize, images.length);
        metrics.totalDiskUsage = totalSize;
        metrics.thumbnailCoverage = safePercentage(thumbnailCount, images.length, 1);

        return metrics;
    }

    /**
     * Analyze user behavior patterns
     */
    function analyzeUserBehavior(tracking, prompts) {
        // Ensure arrays
        if (!Array.isArray(tracking)) tracking = [];
        if (!Array.isArray(prompts)) prompts = [];
        const behavior = {
            // Session patterns
            avgSessionLength: 0,
            sessionsPerDay: {},
            // Prompt refinement patterns
            refinementRate: 0,
            avgRevisionsPerPrompt: 0,
            // Category preferences
            categoryDistribution: {},
            // Rating distribution
            ratingDistribution: { 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 },
            // Tag cloud
            popularTags: {},
            // Workflow complexity
            workflowComplexity: { simple: 0, moderate: 0, complex: 0 },
            // Experimentation rate
            experimentationScore: 0
        };

        // Session analysis
        const sessions = {};
        tracking.forEach(t => {
            if (!sessions[t.session_id]) {
                sessions[t.session_id] = {
                    start: new Date(t.created_at),
                    end: new Date(t.created_at),
                    prompts: []
                };
            }
            sessions[t.session_id].end = new Date(t.created_at);
            sessions[t.session_id].prompts.push(t.prompt_text);
        });

        // Calculate session metrics
        const sessionLengths = Object.values(sessions).map(s =>
            (s.end - s.start) / 60000 // minutes
        );
        behavior.avgSessionLength = safeDivide(
            sessionLengths.reduce((a, b) => a + b, 0),
            sessionLengths.length
        );

        // Category and rating analysis
        prompts.forEach(p => {
            if (p.category) {
                behavior.categoryDistribution[p.category] =
                    (behavior.categoryDistribution[p.category] || 0) + 1;
            }

            if (p.rating) {
                behavior.ratingDistribution[p.rating]++;
            }

            normalizeTags(p.tags).forEach(tag => {
                behavior.popularTags[tag] = (behavior.popularTags[tag] || 0) + 1;
            });
        });

        // Calculate experimentation score based on prompt diversity
        const uniquePrompts = new Set(prompts.map(p => p.positive_prompt)).size;
        behavior.experimentationScore = safePercentage(uniquePrompts, prompts.length, 1);

        return behavior;
    }

    /**
     * Analyze model performance
     */
    function analyzeModelPerformance(prompts, images) {
        // Ensure arrays
        if (!Array.isArray(prompts)) prompts = [];
        if (!Array.isArray(images)) images = [];
        const performance = {
            // Model usage distribution
            modelUsage: {},
            // Sampler preferences
            samplerPreferences: {},
            // Success rate by model (based on ratings)
            modelSuccessRate: {},
            // Parameter combinations
            popularSettings: [],
            // Model switching patterns
            modelSwitchingRate: 0,
            // Optimal settings discovered
            optimalConfigs: []
        };

        // Extract model and sampler data
        prompts.forEach((p, idx) => {
            if (p.model_hash) {
                performance.modelUsage[p.model_hash] =
                    (performance.modelUsage[p.model_hash] || 0) + 1;

                // Success rate based on rating
                if (p.rating >= 4) {
                    if (!performance.modelSuccessRate[p.model_hash]) {
                        performance.modelSuccessRate[p.model_hash] = { success: 0, total: 0 };
                    }
                    performance.modelSuccessRate[p.model_hash].success++;
                    performance.modelSuccessRate[p.model_hash].total++;
                }
            }

            if (p.sampler_settings) {
                try {
                    const settings = JSON.parse(p.sampler_settings);
                    const key = `${settings.sampler || 'unknown'}_${settings.steps || 0}`;
                    performance.samplerPreferences[key] =
                        (performance.samplerPreferences[key] || 0) + 1;
                } catch (e) {}
            }
        });

        // Find optimal configurations (highest rated)
        performance.optimalConfigs = prompts
            .filter(p => p.rating === 5 && p.generation_params)
            .map(p => ({
                model: p.model_hash,
                params: p.generation_params,
                prompt_excerpt: (p.positive_prompt || '').substring(0, 50)
            }))
            .slice(0, 5);

        return performance;
    }

    /**
     * Analyze quality metrics
     */
    function analyzeQualityMetrics(prompts, images) {
        // Ensure arrays
        if (!Array.isArray(prompts)) prompts = [];
        if (!Array.isArray(images)) images = [];
        const quality = {
            // Rating trends over time
            ratingTrend: [],
            // Quality improvement rate
            improvementRate: 0,
            // Best performing categories
            topCategories: [],
            // Quality by time of day
            qualityByHour: new Array(24).fill(0),
            // Consistency score
            consistencyScore: 0,
            // Innovation index
            innovationIndex: 0
        };

        // Calculate rating trends
        const sortedPrompts = prompts
            .filter(p => p.created_at && p.rating)
            .sort((a, b) => new Date(a.created_at) - new Date(b.created_at));

        // Group by month
        const monthlyRatings = {};
        sortedPrompts.forEach(p => {
            const date = new Date(p.created_at);
            const key = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
            if (!monthlyRatings[key]) {
                monthlyRatings[key] = { sum: 0, count: 0 };
            }
            monthlyRatings[key].sum += p.rating;
            monthlyRatings[key].count++;
        });

        quality.ratingTrend = Object.entries(monthlyRatings)
            .map(([month, data]) => ({
                month,
                avgRating: safeFixed(safeDivide(data.sum, data.count), 2)
            }));

        // Calculate improvement rate
        if (quality.ratingTrend.length >= 2) {
            const first = parseFloat(quality.ratingTrend[0].avgRating);
            const last = parseFloat(quality.ratingTrend[quality.ratingTrend.length - 1].avgRating);
            if (first !== 0) {
                quality.improvementRate = safeFixed(((last - first) / first) * 100, 1);
            } else {
                quality.improvementRate = (0).toFixed(1);
            }
        }

        // Top categories by average rating
        const categoryRatings = {};
        prompts.forEach(p => {
            if (p.category && p.rating) {
                if (!categoryRatings[p.category]) {
                    categoryRatings[p.category] = { sum: 0, count: 0 };
                }
                categoryRatings[p.category].sum += p.rating;
                categoryRatings[p.category].count++;
            }
        });

        quality.topCategories = Object.entries(categoryRatings)
            .map(([cat, data]) => ({
                category: cat,
                avgRating: safeFixed(safeDivide(data.sum, data.count), 2),
                count: data.count
            }))
            .sort((a, b) => b.avgRating - a.avgRating)
            .slice(0, 10);

        // Calculate consistency (lower variance = higher consistency)
        const ratings = prompts.filter(p => p.rating).map(p => p.rating);
        if (ratings.length > 0) {
            const avgRating = safeDivide(ratings.reduce((a, b) => a + b, 0), ratings.length);
            const variance = safeDivide(
                ratings.reduce((sum, r) => sum + Math.pow(r - avgRating, 2), 0),
                ratings.length
            );
            quality.consistencyScore = safeFixed(Math.max(0, 100 - variance * 20), 1);
        } else {
            quality.consistencyScore = (0).toFixed(1);
        }

        // Innovation index based on prompt diversity
        const uniqueWords = new Set();
        prompts.forEach(p => {
            const words = (p.positive_prompt || '').toLowerCase().split(/[\s,;.]+/);
            words.forEach(w => uniqueWords.add(w));
        });
        quality.innovationIndex = safeFixed(Math.min(100, uniqueWords.size / 10), 1);

        return quality;
    }

    /**
     * Analyze workflows
     */
    function analyzeWorkflows(images, tracking) {
        // Ensure arrays
        if (!Array.isArray(images)) images = [];
        if (!Array.isArray(tracking)) tracking = [];
        const workflows = {
            // Node usage statistics
            nodeUsage: {},
            // Workflow complexity distribution
            complexityLevels: { simple: 0, moderate: 0, complex: 0, advanced: 0 },
            // Most common workflow patterns
            commonPatterns: [],
            // Evolution of workflow complexity
            complexityTrend: [],
            // Unique workflows
            uniqueWorkflowCount: 0
        };

        const workflowHashes = new Set();

        images.forEach(img => {
            if (img.workflow_data) {
                try {
                    const wf = JSON.parse(img.workflow_data);
                    workflowHashes.add(JSON.stringify(wf));

                    // Count nodes
                    const nodeCount = Object.keys(wf.nodes || {}).length;
                    if (nodeCount < 5) workflows.complexityLevels.simple++;
                    else if (nodeCount < 10) workflows.complexityLevels.moderate++;
                    else if (nodeCount < 20) workflows.complexityLevels.complex++;
                    else workflows.complexityLevels.advanced++;

                    // Node usage
                    if (wf.nodes) {
                        Object.values(wf.nodes).forEach(node => {
                            const type = node.class_type || node.type || 'unknown';
                            workflows.nodeUsage[type] = (workflows.nodeUsage[type] || 0) + 1;
                        });
                    }
                } catch (e) {}
            }
        });

        workflows.uniqueWorkflowCount = workflowHashes.size;

        return workflows;
    }

    /**
     * Analyze trends and make predictions
     */
    function analyzeTrends(prompts, images, tracking) {
        // Ensure arrays
        if (!Array.isArray(prompts)) prompts = [];
        if (!Array.isArray(images)) images = [];
        if (!Array.isArray(tracking)) tracking = [];
        const trends = {
            // Growth predictions
            projectedGrowth: {},
            // Trending styles
            trendingStyles: [],
            // Emerging patterns
            emergingPatterns: [],
            // Seasonal patterns
            seasonalTrends: {},
            // Predicted peak times
            predictedPeakTimes: [],
            // Usage velocity
            velocityScore: 0
        };

        // Calculate growth rate
        const imagesByMonth = {};
        images.forEach(img => {
            const date = new Date(img.generation_time || img.created_at);
            const key = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
            imagesByMonth[key] = (imagesByMonth[key] || 0) + 1;
        });

        // Simple linear projection
        const months = Object.keys(imagesByMonth).sort();
        if (months.length >= 3) {
            const recentMonths = months.slice(-3);
            const recentCounts = recentMonths.map(m => imagesByMonth[m]);
            const avgGrowth = (recentCounts[2] - recentCounts[0]) / 2;

            // Project next 3 months
            for (let i = 1; i <= 3; i++) {
                const lastCount = recentCounts[recentCounts.length - 1];
                trends.projectedGrowth[`Month +${i}`] = Math.round(lastCount + avgGrowth * i);
            }
        }

        // Calculate velocity score (acceleration of usage)
        const recentDays = 7;
        const recentImages = images.filter(img => {
            const date = new Date(img.generation_time || img.created_at);
            const daysDiff = (Date.now() - date) / (1000 * 60 * 60 * 24);
            return daysDiff <= recentDays;
        });
        trends.velocityScore = safeFixed(safeDivide(recentImages.length, recentDays) * 7, 1); // Weekly rate

        // Find trending styles (recent vs overall)
        const recentStyles = {};
        const overallStyles = {};

        prompts.forEach(p => {
            const styles = extractStyles(p.positive_prompt);
            const isRecent = (Date.now() - new Date(p.created_at)) < 7 * 24 * 60 * 60 * 1000;

            styles.forEach(style => {
                if (isRecent) {
                    recentStyles[style] = (recentStyles[style] || 0) + 1;
                }
                overallStyles[style] = (overallStyles[style] || 0) + 1;
            });
        });

        // Calculate trending (higher recent usage than average)
        trends.trendingStyles = Object.entries(recentStyles)
            .map(([style, recentCount]) => {
                const overallTotal = overallStyles[style] || 0;
                const overallAvg = safeDivide(overallTotal, 30); // Daily average
                const recentAvg = safeDivide(recentCount, 7);
                const ratio = overallAvg > 0 ? recentAvg / overallAvg : recentAvg;
                return {
                    style,
                    trendScore: safeFixed(ratio, 2),
                    recentCount,
                    rising: recentAvg > overallAvg
                };
            })
            .filter(s => s.rising)
            .sort((a, b) => b.trendScore - a.trendScore)
            .slice(0, 10);

        return trends;
    }

    // Helper functions
    function calculateComplexity(prompt) {
        if (!prompt) return 0;
        const factors = {
            length: prompt.length / 10,
            commas: (prompt.match(/,/g) || []).length * 2,
            parentheses: (prompt.match(/[()]/g) || []).length * 3,
            weights: (prompt.match(/:\d+(\.\d+)?/g) || []).length * 5
        };
        return Object.values(factors).reduce((a, b) => a + b, 0);
    }

    function calculateUniqueness(prompt, allPrompts) {
        if (!prompt) return 0;
        const words = new Set(prompt.toLowerCase().split(/[\s,;.]+/));
        let uniqueScore = 0;

        words.forEach(word => {
            let occurrences = 0;
            allPrompts.forEach(p => {
                if (p.positive_prompt && p.positive_prompt.toLowerCase().includes(word)) {
                    occurrences++;
                }
            });
            uniqueScore += 1 / occurrences;
        });

        return uniqueScore;
    }

    function extractStyles(prompt) {
        if (!prompt) return [];
        const styleKeywords = [
            'realistic', 'anime', 'cartoon', 'photorealistic', 'digital art',
            'oil painting', 'watercolor', 'sketch', 'concept art', 'fantasy',
            'sci-fi', 'cyberpunk', 'steampunk', 'gothic', 'minimalist',
            'abstract', 'surreal', 'impressionist', 'baroque', 'renaissance'
        ];

        const found = [];
        const lower = prompt.toLowerCase();
        styleKeywords.forEach(style => {
            if (lower.includes(style)) {
                found.push(style);
            }
        });

        return found;
    }

    // Data fetching functions
    async function fetchPrompts() {
        const basePath = '/prompts?sort_by=created_at&sort_desc=true';

        const prompts = await fetchPaginatedCollection(basePath, ['prompts', 'items'], {
            perPage: DEFAULT_PAGE_SIZE,
            keySelector: (prompt) => prompt?.id || prompt?.uuid || prompt?.prompt_id || prompt?.hash,
        });

        return prompts;
    }

    async function fetchImages() {
        const sources = [
            '/gallery/images',
            '/images',
        ];

        for (const endpoint of sources) {
            const images = await fetchPaginatedCollection(endpoint, ['images', 'items', 'generated_images'], {
                perPage: DEFAULT_PAGE_SIZE,
                keySelector: (image) => image?.id || image?.image_id || image?.uuid || image?.path,
            });

            if (images.length) {
                return images;
            }
        }

        return [];
    }

    async function fetchTracking() {
        // Tracking endpoint doesn't exist yet, return empty array
        return [];
    }

    /**
     * Render the stats dashboard
     */
    function renderDashboard() {
        const container = document.getElementById('stats-dashboard');
        if (!container) return;

        container.innerHTML = `
            <div class="stats-dashboard">
                <!-- Hero Stats -->
                <div class="stats-hero">
                    <div class="hero-stat">
                        <div class="stat-value" id="totalPromptsValue">${statsData.totalPrompts || 0}</div>
                        <div class="stat-label">Total Prompts</div>
                        <div class="stat-trend">+${statsData.trends?.velocityScore || 0}/week</div>
                    </div>
                    <div class="hero-stat">
                        <div class="stat-value" id="totalImagesValue">${statsData.totalImages || 0}</div>
                        <div class="stat-label">Images Generated</div>
                        <div class="stat-sparkline" id="imageSparkline"></div>
                    </div>
                    <div class="hero-stat">
                        <div class="stat-value" id="currentStreakValue">${statsData.timeAnalytics?.currentStreak || 0}</div>
                        <div class="stat-label">Day Streak 🔥</div>
                        <div class="stat-subtext">Best: ${statsData.timeAnalytics?.longestStreak || 0} days</div>
                    </div>
                    <div class="hero-stat">
                        <div class="stat-value" id="innovationScore">${statsData.qualityMetrics?.innovationIndex || 0}%</div>
                        <div class="stat-label">Innovation Index</div>
                        <div class="stat-progress">
                            <div class="progress-fill" style="width: ${statsData.qualityMetrics?.innovationIndex || 0}%"></div>
                        </div>
                    </div>
                </div>

                <!-- Time Analytics -->
                <div class="stats-section">
                    <h3 class="section-title">⏰ Peak Creative Hours</h3>
                    <div class="chart-container">
                        <canvas id="hourlyActivityChart"></canvas>
                    </div>
                    <div class="peak-hours-summary">
                        ${(statsData.timeAnalytics?.peakHours || []).map(h =>
                            `<span class="peak-hour">${h.hour}:00 (${h.percentage}%)</span>`
                        ).join('')}
                    </div>
                </div>

                <!-- Prompt Creativity -->
                <div class="stats-section">
                    <h3 class="section-title">🎨 Creative Patterns</h3>
                    <div class="stats-grid">
                        <div class="stat-card">
                            <h4>Vocabulary Size</h4>
                            <div class="big-number">${statsData.promptPatterns?.vocabularySize || 0}</div>
                            <div class="stat-detail">unique words used</div>
                        </div>
                        <div class="stat-card">
                            <h4>Experimentation Rate</h4>
                            <div class="big-number">${statsData.userBehavior?.experimentationScore || 0}%</div>
                            <div class="stat-detail">prompt diversity</div>
                        </div>
                        <div class="stat-card">
                            <h4>Complexity Distribution</h4>
                            <div class="complexity-bars">
                                ${Object.entries(statsData.promptPatterns?.complexityDistribution || {}).map(([level, count]) =>
                                    `<div class="complexity-bar">
                                        <span class="bar-label">${level}</span>
                                        <div class="bar-fill" style="width: ${count}px"></div>
                                        <span class="bar-value">${count}</span>
                                    </div>`
                                ).join('')}
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Word Cloud -->
                <div class="stats-section">
                    <h3 class="section-title">☁️ Your Creative Universe</h3>
                    <div class="word-cloud" id="wordCloud"></div>
                </div>

                <!-- Generation Analytics -->
                <div class="stats-section">
                    <h3 class="section-title">📊 Generation Analytics</h3>
                    <div class="stats-grid">
                        <div class="stat-card">
                            <h4>Resolution Distribution</h4>
                            <canvas id="resolutionChart"></canvas>
                        </div>
                        <div class="stat-card">
                            <h4>Aspect Ratios</h4>
                            <canvas id="aspectRatioChart"></canvas>
                        </div>
                        <div class="stat-card">
                            <h4>Quality Tiers</h4>
                            <div class="quality-distribution">
                                ${Object.entries(statsData.generationMetrics?.qualityTiers || {}).map(([tier, count]) =>
                                    `<div class="quality-tier">
                                        <span class="tier-label">${tier}</span>
                                        <div class="tier-bar">
                                            <div class="tier-fill tier-${tier}" style="width: ${count * 5}px"></div>
                                        </div>
                                        <span class="tier-count">${count}</span>
                                    </div>`
                                ).join('')}
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Trending Styles -->
                <div class="stats-section">
                    <h3 class="section-title">📈 Trending Now</h3>
                    <div class="trending-container">
                        ${(statsData.trends?.trendingStyles || []).map(style =>
                            `<div class="trending-item">
                                <span class="trend-arrow">↗</span>
                                <span class="trend-name">${style.style}</span>
                                <span class="trend-score">×${style.trendScore}</span>
                            </div>`
                        ).join('')}
                    </div>
                </div>

                <!-- Model Performance -->
                <div class="stats-section">
                    <h3 class="section-title">🤖 Model Performance</h3>
                    <div class="model-stats">
                        <canvas id="modelUsageChart"></canvas>
                    </div>
                    <div class="optimal-configs">
                        <h4>🏆 Optimal Configurations</h4>
                        ${(statsData.modelPerformance?.optimalConfigs || []).map(config =>
                            `<div class="config-item">
                                <span class="config-model">${config.model?.substring(0, 8)}...</span>
                                <span class="config-prompt">"${config.prompt_excerpt}..."</span>
                            </div>`
                        ).join('')}
                    </div>
                </div>

                <!-- Quality Metrics -->
                <div class="stats-section">
                    <h3 class="section-title">✨ Quality Journey</h3>
                    <div class="quality-metrics">
                        <div class="metric-card">
                            <h4>Consistency Score</h4>
                            <div class="circular-progress" data-value="${statsData.qualityMetrics?.consistencyScore || 0}">
                                <svg viewBox="0 0 100 100">
                                    <circle cx="50" cy="50" r="45" class="progress-bg"></circle>
                                    <circle cx="50" cy="50" r="45" class="progress-fill"
                                            style="stroke-dasharray: ${(statsData.qualityMetrics?.consistencyScore || 0) * 2.83} 283"></circle>
                                </svg>
                                <div class="progress-value">${statsData.qualityMetrics?.consistencyScore || 0}%</div>
                            </div>
                        </div>
                        <div class="metric-card">
                            <h4>Improvement Rate</h4>
                            <div class="improvement-indicator ${(statsData.qualityMetrics?.improvementRate || 0) >= 0 ? 'positive' : 'negative'}">
                                ${(statsData.qualityMetrics?.improvementRate || 0) >= 0 ? '↑' : '↓'}
                                ${Math.abs(statsData.qualityMetrics?.improvementRate || 0)}%
                            </div>
                        </div>
                        <div class="metric-card">
                            <h4>Rating Trend</h4>
                            <canvas id="ratingTrendChart"></canvas>
                        </div>
                    </div>
                </div>

                <!-- Workflow Complexity -->
                <div class="stats-section">
                    <h3 class="section-title">🔧 Workflow Evolution</h3>
                    <div class="workflow-stats">
                        <div class="stat-card">
                            <h4>Unique Workflows</h4>
                            <div class="big-number">${statsData.workflowAnalysis?.uniqueWorkflowCount || 0}</div>
                        </div>
                        <div class="stat-card">
                            <h4>Complexity Levels</h4>
                            <canvas id="workflowComplexityChart"></canvas>
                        </div>
                        <div class="stat-card">
                            <h4>Top Nodes Used</h4>
                            <div class="node-list">
                                ${Object.entries(statsData.workflowAnalysis?.nodeUsage || {})
                                    .sort((a, b) => b[1] - a[1])
                                    .slice(0, 5)
                                    .map(([node, count]) =>
                                        `<div class="node-item">
                                            <span class="node-name">${node}</span>
                                            <span class="node-count">${count}</span>
                                        </div>`
                                    ).join('')}
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Predictions -->
                <div class="stats-section">
                    <h3 class="section-title">🔮 Future Projections</h3>
                    <div class="predictions">
                        <div class="prediction-card">
                            <h4>Next 3 Months</h4>
                            ${Object.entries(statsData.trends?.projectedGrowth || {}).map(([month, count]) =>
                                `<div class="projection-item">
                                    <span class="projection-month">${month}</span>
                                    <span class="projection-value">${count} images</span>
                                </div>`
                            ).join('')}
                        </div>
                        <div class="prediction-card">
                            <h4>Velocity Score</h4>
                            <div class="velocity-meter">
                                <div class="velocity-value">${statsData.trends?.velocityScore || 0}</div>
                                <div class="velocity-label">images/week</div>
                                <div class="velocity-indicator" style="transform: rotate(${Math.min(180, (statsData.trends?.velocityScore || 0) * 2)}deg)"></div>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Fun Stats -->
                <div class="stats-section">
                    <h3 class="section-title">🎉 Fun Facts</h3>
                    <div class="fun-stats">
                        <div class="fun-fact">
                            <span class="fact-emoji">💾</span>
                            <span class="fact-text">Total disk usage: ${formatBytes(statsData.generationMetrics?.totalDiskUsage || 0)}</span>
                        </div>
                        <div class="fun-fact">
                            <span class="fact-emoji">📝</span>
                            <span class="fact-text">Average prompt length: ${Math.round(statsData.promptPatterns?.avgPromptLength || 0)} characters</span>
                        </div>
                        <div class="fun-fact">
                            <span class="fact-emoji">🏃</span>
                            <span class="fact-text">Longest session: ${Math.round(statsData.userBehavior?.avgSessionLength || 0)} minutes</span>
                        </div>
                        <div class="fun-fact">
                            <span class="fact-emoji">🖼️</span>
                            <span class="fact-text">Thumbnail coverage: ${statsData.generationMetrics?.thumbnailCoverage || 0}%</span>
                        </div>
                    </div>
                </div>
            </div>
        `;

        // Render charts after DOM is ready
        setTimeout(() => {
            renderCharts();
            renderWordCloud();
        }, 100);
    }

    /**
     * Render all charts
     */
    function renderCharts() {
        // Hourly activity chart
        const hourlyCtx = document.getElementById('hourlyActivityChart');
        if (hourlyCtx) {
            const hourlyData = statsData?.timeAnalytics?.hourlyActivity;
            renderHourlyChart(hourlyCtx, hourlyData);
        }

        // Resolution chart
        const resCtx = document.getElementById('resolutionChart');
        if (resCtx) {
            const resData = statsData?.generationMetrics?.resolutions;
            renderResolutionChart(resCtx, resData);
        }

        // Aspect ratio chart
        const aspectCtx = document.getElementById('aspectRatioChart');
        if (aspectCtx) {
            const aspectData = statsData?.generationMetrics?.aspectRatios;
            renderAspectRatioChart(aspectCtx, aspectData);
        }

        // Model usage chart
        const modelCtx = document.getElementById('modelUsageChart');
        if (modelCtx) {
            const modelData = statsData?.modelPerformance?.modelUsage;
            renderModelChart(modelCtx, modelData);
        }

        // Rating trend chart
        const ratingCtx = document.getElementById('ratingTrendChart');
        if (ratingCtx) {
            const ratingData = statsData?.qualityMetrics?.ratingTrend;
            renderRatingTrendChart(ratingCtx, ratingData);
        }

        // Workflow complexity chart
        const workflowCtx = document.getElementById('workflowComplexityChart');
        if (workflowCtx) {
            const workflowData = statsData?.workflowAnalysis?.complexityLevels;
            renderWorkflowChart(workflowCtx, workflowData);
        }
    }

    // Individual chart rendering functions
    function renderHourlyChart(ctx, data) {
        // Check if data is valid
        if (!data || !Array.isArray(data) || data.length === 0) {
            console.warn('No hourly activity data available');
            // Create empty 24-hour array
            data = new Array(24).fill(0);
        }

        // Simple bar chart using canvas
        const canvas = ctx.getContext('2d');
        const width = ctx.width = ctx.offsetWidth;
        const height = ctx.height = 150;
        const barWidth = width / 24;
        const maxValue = Math.max(...data);
        const denominator = maxValue > 0 ? maxValue : 1;

        canvas.clearRect(0, 0, width, height);
        canvas.fillStyle = '#2E7EE5';

        data.forEach((value, hour) => {
            const barHeight = (value / denominator) * (height - 20);
            canvas.fillRect(
                hour * barWidth + 2,
                height - barHeight - 20,
                barWidth - 4,
                barHeight
            );
        });

        // Draw hour labels
        canvas.fillStyle = '#888';
        canvas.font = '10px sans-serif';
        for (let i = 0; i < 24; i += 3) {
            canvas.fillText(i, i * barWidth, height - 5);
        }
    }

    function renderWordCloud(container) {
        const wordCloudEl = document.getElementById('wordCloud');
        if (!wordCloudEl) return;

        const frequencyMap = statsData?.promptPatterns?.topWords || {};
        const filteredMap = filterIgnoredWords(frequencyMap);

        if (statsData?.promptPatterns) {
            statsData.promptPatterns.ignoredWords = [...statsIgnoredWords];
        }

        // Use the advanced WordCloudManager if available
        if (window.WordCloudManager) {
            wordCloudEl.id = 'word-cloud-container';
            wordCloudEl.style.minHeight = '400px';
            wordCloudEl.style.position = 'relative';

            if (Object.keys(filteredMap).length > 0) {
                window.WordCloudManager.renderFromFrequencyMap(wordCloudEl, filteredMap, {
                    maxWords: 60,
                    minFontSize: 14,
                    maxFontSize: 42,
                });
            } else {
                window.WordCloudManager.renderFromFrequencyMap(wordCloudEl, filteredMap);
            }

            if (!renderWordCloud._listenersAttached && window.EventBus) {
                window.EventBus.on('wordcloud.selected', (data) => {
                    console.log('Word selected:', data.word);
                    // Could filter other stats by this word
                });
                renderWordCloud._listenersAttached = true;
            }
        } else if (filteredMap && Object.keys(filteredMap).length > 0) {
            // Fallback to simple visualization
            const words = Object.entries(filteredMap)
                .slice(0, 30)
                .map(([word, count], index) => {
                    const size = Math.max(12, Math.min(48, count * 2));
                    const opacity = 0.5 + (count / 100);
                    const colors = ['#2E7EE5', '#E5532E', '#2EE555', '#E5E52E', '#E52EE5'];
                    const color = colors[index % colors.length];

                    return `<span class="word-cloud-item"
                            style="font-size: ${size}px;
                                   opacity: ${opacity};
                                   color: ${color}">
                            ${word}
                        </span>`;
                }).join('');

            wordCloudEl.innerHTML = words;
        } else {
            wordCloudEl.innerHTML = '<p class="word-cloud-empty">Not enough prompt data yet.</p>';
        }
    }

    function formatBytes(bytes) {
        if (!bytes || bytes <= 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), sizes.length - 1);
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    }

    /**
     * Setup event listeners
     */
    function setupEventListeners() {
        // Refresh button
        const refreshBtn = document.getElementById('refreshStats');
        if (refreshBtn) {
            refreshBtn.addEventListener('click', async () => {
                refreshBtn.classList.add('loading');
                await loadAllStats({ force: true });
                renderDashboard();
                refreshBtn.classList.remove('loading');
            });
        }

        // Export stats
        const exportBtn = document.getElementById('exportStats');
        if (exportBtn) {
            exportBtn.addEventListener('click', exportStats);
        }
    }

    /**
     * Export stats as JSON
     */
    function exportStats() {
        const dataStr = JSON.stringify(statsData, null, 2);
        const blob = new Blob([dataStr], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `prompt-manager-stats-${new Date().toISOString().split('T')[0]}.json`;
        a.click();
        URL.revokeObjectURL(url);
    }

    /**
     * Start real-time updates
     */
    function startRealTimeUpdates() {
        // Update every 30 seconds
        setInterval(async () => {
            await loadAllStats({ silent: true });
            updateLiveStats();
        }, 30000);
    }

    /**
     * Update live stats without full re-render
     */
    function updateLiveStats() {
        // Update hero stats
        const totalPromptsEl = document.getElementById('totalPromptsValue');
        if (totalPromptsEl) {
            totalPromptsEl.textContent = statsData.totalPrompts || 0;
        }

        const totalImagesEl = document.getElementById('totalImagesValue');
        if (totalImagesEl) {
            totalImagesEl.textContent = statsData.totalImages || 0;
        }

        const streakEl = document.getElementById('currentStreakValue');
        if (streakEl) {
            streakEl.textContent = statsData.timeAnalytics?.currentStreak || 0;
        }

        const innovationEl = document.getElementById('innovationScore');
        if (innovationEl) {
            innovationEl.textContent = `${statsData.qualityMetrics?.innovationIndex || 0}%`;
        }
    }

    // Simple chart rendering functions for other charts
    function renderResolutionChart(ctx, data) {
        // Check if data is valid
        if (!data || typeof data !== 'object') {
            console.warn('No resolution data available');
            return;
        }

        // Implement pie chart for resolutions
        const canvas = ctx.getContext('2d');
        const width = ctx.width = ctx.offsetWidth;
        const height = ctx.height = 200;

        // Simple implementation - you could enhance this
        canvas.clearRect(0, 0, width, height);
        // ... pie chart implementation
    }

    function renderAspectRatioChart(ctx, data) {
        // Check if data is valid
        if (!data || typeof data !== 'object') {
            console.warn('No aspect ratio data available');
            return;
        }

        // Donut chart for aspect ratios
        const canvas = ctx.getContext('2d');
        const width = ctx.width = ctx.offsetWidth;
        const height = ctx.height = 200;

        canvas.clearRect(0, 0, width, height);
        // ... donut chart implementation
    }

    function renderModelChart(ctx, data) {
        // Check if data is valid
        if (!data || typeof data !== 'object') {
            console.warn('No model usage data available');
            return;
        }

        // Bar chart for model usage
        const canvas = ctx.getContext('2d');
        const width = ctx.width = ctx.offsetWidth;
        const height = ctx.height = 200;

        canvas.clearRect(0, 0, width, height);
        // ... bar chart implementation
    }

    function renderRatingTrendChart(ctx, data) {
        // Check if data is valid
        if (!data || (!Array.isArray(data) && typeof data !== 'object')) {
            console.warn('No rating trend data available');
            return;
        }

        // Line chart for rating trends
        const canvas = ctx.getContext('2d');
        const width = ctx.width = ctx.offsetWidth;
        const height = ctx.height = 150;

        canvas.clearRect(0, 0, width, height);
        // ... line chart implementation
    }

    function renderWorkflowChart(ctx, data) {
        // Check if data is valid
        if (!data || typeof data !== 'object') {
            console.warn('No workflow complexity data available');
            return;
        }

        // Horizontal bar chart for workflow complexity
        const canvas = ctx.getContext('2d');
        const width = ctx.width = ctx.offsetWidth;
        const height = ctx.height = 200;

        canvas.clearRect(0, 0, width, height);
        // ... horizontal bar chart implementation
    }

    // Public API
    return {
        init,
        loadAllStats,
        renderDashboard,
        exportStats,
        getStatsData: () => statsData
    };
})();

// Initialize when ready
document.addEventListener('DOMContentLoaded', () => {
    if (window.location.pathname.includes('stats')) {
        EpicStats.init();
    }
});

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = EpicStats;
}
if (typeof window !== 'undefined') {
    window.EpicStats = EpicStats;
}
