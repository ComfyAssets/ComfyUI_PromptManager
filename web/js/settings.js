/**
 * Settings Page - Professional Edition
 * Comprehensive settings management with the new dark theme
 * Ported from the original implementation with all features intact
 */

(function() {
    'use strict';

    if (typeof window === 'undefined') {
        return;
    }
    const pmActive = (window.location?.pathname || '').includes('/prompt_manager');
    if (!pmActive) {
        console.info('[PromptManager] settings script skipped outside PromptManager UI context');
        return;
    }

    // Default settings - comprehensive configuration from original
    const defaultSettings = {
        // Database
        databasePath: 'ComfyUI/user/default/PromptManager/prompts.db',
        autoSave: true,
        saveInterval: 30,
        backupEnabled: true,
        backupInterval: 3600,
        maxBackups: 10,
        databasePathCustom: false,

        // Cache
        cacheEnabled: true,
        cacheSize: 100,
        cacheTTL: 3600,
        cacheWarming: false,

        // Performance
        lazyLoading: true,
        cacheMetadata: true,
        checkThumbnailsAtStartup: false,
        apiTimeout: 30,

        // Display
        imageQuality: 'high',
        defaultViewMode: 'grid',
        gridColumns: 8,
        defaultLimit: 100,
        showImageInfo: true,
        showFilePaths: false,
        promptViewMode: 'table', // 'table' or 'cards' for prompt manager

        // Video
        videoAutoplay: false,
        videoMute: true,
        videoLoop: true,

        // Metadata
        autoLoadMetadata: true,
        showMetadataPanel: true,
        metadataDisplayMode: 'compact',

        // Gallery
        monitoringEnabled: true,
        processingDelay: 2.0,
        promptTimeout: 120,
        autoCleanupMissing: true,
        imagesPerPage: 20,
        thumbnailSize: 256,
        enableGallerySearch: true,
        maxConcurrentProcessing: 3,
        monitoringDirectories: '',
        supportedExtensions: '.png, .jpg, .jpeg, .webp, .gif',
        cleanupInterval: 300,
        maxImageAgeDays: 365,
        enableMetadataView: true,
        metadataExtractionTimeout: 10,

        // Stats
        statsIgnoredWords: [],

        // Notifications
        enableNotifications: true,
        toastPosition: 'top-right',
        toastDuration: 5,
        showSuccessNotifications: true,
        showErrorNotifications: true,
        showWarningNotifications: true,
        showInfoNotifications: true,
        stackNotifications: true,
        maxVisibleToasts: 3,
        autoDismiss: true,
        showProgressBar: true,
        animationStyle: 'fade',
        playNotificationSound: false,
        soundVolume: 50,
        clickToDismiss: true,

        // Webhooks
        enableWebhooks: false,
        slackWebhookUrl: '',
        discordWebhookUrl: '',
        teamsWebhookUrl: '',
        customWebhookUrl: '',
        webhookEvents: [],
        webhookFormat: 'simple',
        webhookIncludeThumbnails: false,
        webhookRetryAttempts: 3,
        webhookTimeout: 10,

        // Logging
        logLevel: 'INFO',
        consoleLogging: true,
        fileLogging: true,
        maxLogFileSize: 10,
        logBackupCount: 5,
        logBufferSize: 1000,
        logDirectory: 'ComfyUI/user/default/PromptManager/logs',
        logTimestamps: true,

        // Advanced
        experimentalFeatures: false,
        developerMode: false,
        debugMode: false,
        performanceMonitoring: false,
        telemetryEnabled: false,
        autoUpdate: true,
        updateChannel: 'stable',
        maxUndoHistory: 50,
        confirmDangerousActions: true,
        showAdvancedOptions: false
    };

    const DEFAULT_DB_PATH = 'ComfyUI/user/default/PromptManager/prompts.db';
    const DB_API_ENDPOINTS = {
        verify: '/api/v1/system/database/verify',
        apply: '/api/v1/system/database/apply',
        migrate: '/api/v1/system/database/migrate'
    };

    // Load settings from backend API with localStorage fallback
    async function loadSettings() {
        try {
            // Try to load from backend first
            const response = await fetch('/api/v1/settings/thumbnails');
            if (response.ok) {
                const backendSettings = await response.json();

                // Load localStorage settings as fallback
                const stored = localStorage.getItem('promptManagerSettings');
                let localSettings = defaultSettings;
                if (stored) {
                    try {
                        localSettings = { ...defaultSettings, ...JSON.parse(stored) };
                    } catch (e) {
                        console.error('Failed to parse localStorage settings:', e);
                    }
                }

                // Merge backend settings into local settings
                const settings = {
                    ...localSettings,
                    // Override with backend thumbnail settings
                    ffmpegPath: backendSettings.ffmpeg_path || localSettings.ffmpegPath || '',
                    enableThumbnails: backendSettings.enable_thumbnails ?? localSettings.enableThumbnails ?? true,
                    thumbnailSize: backendSettings.thumbnail_size || localSettings.thumbnailSize || 256,
                    autoGenerateThumbnails: backendSettings.auto_generate ?? localSettings.autoGenerateThumbnails ?? false,
                    thumbnailCacheDuration: backendSettings.cache_duration || localSettings.thumbnailCacheDuration || 86400
                };

                // Save merged settings to localStorage for offline access
                localStorage.setItem('promptManagerSettings', JSON.stringify(settings));

                settings.databasePath = settings.databasePath || DEFAULT_DB_PATH;
                settings.databasePathCustom = Boolean(settings.databasePathCustom && settings.databasePath !== DEFAULT_DB_PATH);
                if (!Array.isArray(settings.statsIgnoredWords)) {
                    settings.statsIgnoredWords = [];
                }
                return settings;
            }
        } catch (error) {
            console.error('Failed to load settings from backend:', error);
        }

        // Fallback to localStorage only
        const stored = localStorage.getItem('promptManagerSettings');
        let settings;
        if (stored) {
            try {
                settings = { ...defaultSettings, ...JSON.parse(stored) };
            } catch (e) {
                console.error('Failed to load settings:', e);
                settings = { ...defaultSettings };
            }
        } else {
            settings = { ...defaultSettings };
        }
        settings.databasePath = settings.databasePath || DEFAULT_DB_PATH;
        settings.databasePathCustom = Boolean(settings.databasePathCustom && settings.databasePath !== DEFAULT_DB_PATH);
        if (!Array.isArray(settings.statsIgnoredWords)) {
            settings.statsIgnoredWords = [];
        }
        return settings;
    }

    // Export for use in other files
    window.loadSettings = loadSettings;

    // Save settings to backend API and localStorage
    async function saveSettings(settings, options = {}) {
        const { silent = false } = options;
        try {
            settings.databasePath = settings.databasePath || DEFAULT_DB_PATH;
            settings.databasePathCustom = settings.databasePath !== DEFAULT_DB_PATH;
            settings.logDirectory = 'ComfyUI/user/default/PromptManager/logs';

            if (!Array.isArray(settings.statsIgnoredWords)) {
                settings.statsIgnoredWords = [];
            } else {
                const deduped = [];
                const seen = new Set();
                settings.statsIgnoredWords.forEach((entry) => {
                    const value = typeof entry === 'string' ? entry.trim() : String(entry || '').trim();
                    const normalized = value.toLowerCase();
                    if (!value || seen.has(normalized)) {
                        return;
                    }
                    seen.add(normalized);
                    deduped.push(value);
                });
                settings.statsIgnoredWords = deduped;
            }

            // Save to localStorage first for immediate access
            localStorage.setItem('promptManagerSettings', JSON.stringify(settings));

            // Update notification service if available
            if (window.notificationService) {
                window.notificationService.updateSettings({
                    enableNotifications: settings.enableNotifications,
                    notificationPosition: settings.toastPosition || 'top-right',
                    notificationDuration: settings.toastDuration ?? 5,
                    soundAlerts: settings.playNotificationSound ?? false,
                    desktopNotifications: settings.desktopNotifications ?? false,
                    clickToDismiss: settings.clickToDismiss || (settings.autoDismiss === false),
                    maxVisible: settings.maxVisibleToasts || 3,
                });
            }

            // Save thumbnail-related settings to backend
            const thumbnailSettings = {
                ffmpeg_path: settings.ffmpegPath || '',
                enable_thumbnails: settings.enableThumbnails ?? true,
                thumbnail_size: String(settings.thumbnailSize || '256'),
                auto_generate: settings.autoGenerateThumbnails ?? false,
                cache_duration: settings.thumbnailCacheDuration || 86400
            };

            const response = await fetch('/api/v1/settings/thumbnails', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(thumbnailSettings)
            });

            if (!response.ok) {
                console.error('Failed to save settings to backend:', response.statusText);
                if (!silent) {
                    showToast('Settings saved locally (backend sync failed)', 'warning');
                }
                return true; // Still return true since localStorage save worked
            }

            if (!silent) {
                showToast('Settings saved successfully', 'success');
            }
            return true;
        } catch (e) {
            console.error('Failed to save settings:', e);
            if (!silent) {
                showToast('Settings saved locally (backend error)', 'warning');
            }
            return true; // Still return true if localStorage worked
        }
    }

    // Export for use in other files
    window.saveSettings = saveSettings;

    // Unified toast notification (delegates to global notificationService)
    function showToast(message, type = 'info', options = {}) {
        if (typeof window.showToast === 'function') {
            window.showToast(message, type, options);
        } else {
            console.log(`[${type}]`, message);
        }
    }

    // Render settings page
    window.renderSettingsPage = async function() {
        const settings = await loadSettings();

        // Hide other views
        const actionBar = document.querySelector('.action-bar');
        const viewTable = document.querySelector('#viewTable');
        const viewCards = document.querySelector('#viewCards');

        if (actionBar) actionBar.style.display = 'none';
        if (viewTable) viewTable.style.display = 'none';
        if (viewCards) viewCards.style.display = 'none';

        // Remove existing settings container if any
        const existing = document.querySelector('.settings-container');
        if (existing) existing.remove();

        // Create settings container
        const container = document.createElement('div');
        container.className = 'settings-container';
        container.innerHTML = `
            <div class="settings-layout">
                <!-- Settings Sidebar -->
                <div class="settings-sidebar">
                    <div class="settings-nav">
                        <button class="settings-nav-item active" data-panel="database">
                            <span class="nav-icon">💾</span>
                            <span>Database Settings</span>
                        </button>
                        <button class="settings-nav-item" data-panel="cache">
                            <span class="nav-icon">⚡</span>
                            <span>Cache Settings</span>
                        </button>
                        <button class="settings-nav-item" data-panel="performance">
                            <span class="nav-icon">🚀</span>
                            <span>Performance Settings</span>
                        </button>
                        <button class="settings-nav-item" data-panel="display">
                            <span class="nav-icon">🖼️</span>
                            <span>Display Settings</span>
                        </button>
                        <button class="settings-nav-item" data-panel="video">
                            <span class="nav-icon">🎬</span>
                            <span>Video Settings</span>
                        </button>
                        <button class="settings-nav-item" data-panel="thumbnails">
                            <span class="nav-icon">🖼️</span>
                            <span>Thumbnail Settings</span>
                        </button>
                        <button class="settings-nav-item" data-panel="metadata">
                            <span class="nav-icon">📋</span>
                            <span>Metadata Settings</span>
                        </button>
                        <button class="settings-nav-item" data-panel="gallery">
                            <span class="nav-icon">🖼️</span>
                            <span>Gallery Settings</span>
                        </button>
                        <button class="settings-nav-item" data-panel="notifications">
                            <span class="nav-icon">🔔</span>
                            <span>Notifications</span>
                        </button>
                        <button class="settings-nav-item" data-panel="logging">
                            <span class="nav-icon">📝</span>
                            <span>Logging Settings</span>
                        </button>
                        <button class="settings-nav-item" data-panel="advanced">
                            <span class="nav-icon">⚙️</span>
                            <span>Advanced Settings</span>
                        </button>
                        <button class="settings-nav-item" data-panel="migration">
                            <span class="nav-icon">🔄</span>
                            <span>Migration Settings</span>
                        </button>
                    </div>
                </div>

                <!-- Settings Content -->
                <div class="settings-content scrollbar">
                    <form id="settingsForm" class="settings-form">
                        ${renderAllPanels(settings)}
                    </form>
                </div>
            </div>

            <!-- Fixed Action Buttons -->
            <div class="settings-actions">
                <button class="btn btn-primary" onclick="saveAllSettings()">💾 Save All</button>
                <button class="btn" onclick="window.exportSettingsFunc()">📤 Export</button>
                <button class="btn" onclick="window.importSettingsFunc()">📥 Import</button>
                <button class="btn btn-danger" onclick="window.resetSettingsFunc()">🔄 Reset</button>
            </div>
        `;

        // Add to page
        const pageWrapper = document.querySelector('.page-wrapper');
        if (pageWrapper) {
            pageWrapper.appendChild(container);
        }

        // Setup panel navigation
        setupPanelNavigation();

        // Setup form submission
        setupFormHandlers();

        updateDatabasePathField(settings.databasePath);
        updateDatabaseStatus(`<span class="status-ok"><i class="fa-solid fa-circle-check" aria-hidden="true"></i> Using database at <code>${escapeHtml(settings.databasePath)}</code>.</span>`, 'status-ok');
    };

    // Render all settings panels
    function renderAllPanels(settings) {
        return `
            <!-- Database Panel -->
            <section id="database" class="settings-panel active">
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title">Database Settings</h3>
                    </div>
                    <div class="card-body">
                        ${renderDatabasePathControl(settings)}
                        ${renderToggle('Auto-Save', 'autoSave', settings.autoSave, 'Automatically save changes')}
                        ${renderSettingRow('Save Interval (seconds)', 'number', 'saveInterval', settings.saveInterval, 'How often to auto-save', 10, 300)}
                        ${renderToggle('Backup Enabled', 'backupEnabled', settings.backupEnabled, 'Enable automatic backups')}
                        ${renderSettingRow('Backup Interval (seconds)', 'number', 'backupInterval', settings.backupInterval, 'How often to create backups', 300, 86400)}
                        ${renderSettingRow('Max Backups', 'number', 'maxBackups', settings.maxBackups, 'Maximum number of backup files to keep', 1, 100)}
                    </div>
                </div>
            </section>

            <!-- Cache Panel -->
            <section id="cache" class="settings-panel">
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title">Cache Settings</h3>
                    </div>
                    <div class="card-body">
                    ${renderToggle('Cache Enabled', 'cacheEnabled', settings.cacheEnabled, 'Enable caching for better performance')}
                    ${renderSettingRow('Cache Size (MB)', 'number', 'cacheSize', settings.cacheSize, 'Maximum cache size in megabytes', 10, 1000)}
                    ${renderSettingRow('Cache TTL (seconds)', 'number', 'cacheTTL', settings.cacheTTL, 'How long to keep cached items', 60, 86400)}
                    ${renderToggle('Cache Warming', 'cacheWarming', settings.cacheWarming, 'Pre-populate cache on startup')}
                    </div>
                </div>
            </section>

            <!-- Performance Panel -->
            <section id="performance" class="settings-panel">
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title">Performance Settings</h3>
                    </div>
                    <div class="card-body">
                    ${renderToggle('Lazy Loading', 'lazyLoading', settings.lazyLoading, 'Load images only when visible')}
                    ${renderToggle('Cache Metadata', 'cacheMetadata', settings.cacheMetadata, 'Cache EXIF and prompt data')}
                    ${renderToggle('Check Thumbnails at Startup', 'checkThumbnailsAtStartup', settings.checkThumbnailsAtStartup, 'Verify thumbnails on launch')}
                    ${renderSettingRow('API Timeout (seconds)', 'number', 'apiTimeout', settings.apiTimeout, 'Maximum wait time for API responses', 1, 300)}
                    </div>
                </div>
            </section>

            <!-- Display Panel -->
            <section id="display" class="settings-panel">
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title">Display Settings</h3>
                    </div>
                    <div class="card-body">
                    <!-- Image Quality removed per user request -->
                    ${renderSelect('Default View Mode', 'defaultViewMode', settings.defaultViewMode, ['grid', 'list', 'detail'], 'How images are displayed by default')}
                    ${renderSelect('Prompt View Mode', 'promptViewMode', settings.promptViewMode, ['table', 'cards'], 'Default view for prompt manager')}
                    ${renderSettingRow('Grid Columns', 'number', 'gridColumns', settings.gridColumns, 'Number of columns in grid view', 1, 20)}
                    ${renderSettingRow('Items Per Page', 'number', 'defaultLimit', settings.defaultLimit, 'Number of items to load at once', 10, 1000)}
                    ${renderToggle('Show Image Info', 'showImageInfo', settings.showImageInfo, 'Display image dimensions and file size')}
                    ${renderToggle('Show File Paths', 'showFilePaths', settings.showFilePaths, 'Display full file paths')}
                    </div>
                </div>
            </section>

            <!-- Video Panel -->
            <section id="video" class="settings-panel">
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title">Video Settings</h3>
                    </div>
                    <div class="card-body">
                    ${renderToggle('Autoplay', 'videoAutoplay', settings.videoAutoplay, 'Automatically play videos')}
                    ${renderToggle('Mute by Default', 'videoMute', settings.videoMute, 'Start videos muted')}
                    ${renderToggle('Loop Playback', 'videoLoop', settings.videoLoop, 'Automatically restart videos')}
                    <h4 class="settings-subsection">FFmpeg Configuration</h4>
                    <div class="setting-row">
                        <label class="setting-label">
                            FFmpeg Path
                            <span class="info-icon" title="Path to FFmpeg executable for video processing">ⓘ</span>
                        </label>
                        <div class="setting-control">
                            <input type="text" name="ffmpegPath" id="ffmpegPath" value="${settings.ffmpegPath || ''}"
                                   placeholder="Auto-detected or enter custom path" class="input">
                            <div class="database-actions">
                                <button class="btn btn-secondary" type="button" onclick="detectFFmpeg()">
                                    🔍 Detect
                                </button>
                                <button class="btn btn-primary" type="button" onclick="testFFmpeg()">
                                    ✓ Test
                                </button>
                            </div>
                            <div id="ffmpegStatus" class="settings-info">Checking for FFmpeg...</div>
                        </div>
                    </div>
                    </div>
                </div>
            </section>

            <!-- Thumbnails Panel -->
            <section id="thumbnails" class="settings-panel">
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title">Thumbnail Settings</h3>
                    </div>
                    <div class="card-body">
                    ${renderToggle('Enable Thumbnails', 'enableThumbnails', settings.enableThumbnails !== false, 'Generate and display image thumbnails')}
                    ${renderSettingRow('Thumbnail Size (px)', 'number', 'thumbnailSize', settings.thumbnailSize || 256, 'Size for generated thumbnails', 64, 512)}
                    ${renderToggle('Auto Generate', 'autoGenerateThumbnails', settings.autoGenerateThumbnails, 'Automatically generate missing thumbnails')}
                    ${renderSettingRow('Cache Duration (seconds)', 'number', 'thumbnailCacheDuration', settings.thumbnailCacheDuration || 86400, 'How long to cache thumbnails', 3600, 604800)}
                    <h4 class="settings-subsection">Disk Usage</h4>
                    <div class="setting-row">
                        <label class="setting-label">
                            Thumbnail Storage
                            <span class="info-icon" title="Storage used by images, thumbnails, and caches">ⓘ</span>
                        </label>
                        <div class="setting-control">
                            <div id="thumbnailDiskUsage" class="settings-info">Calculating storage usage…</div>
                            <div class="disk-usage-grid">
                                <div class="disk-usage-card">
                                    <span class="disk-usage-title">Images</span>
                                    <span class="disk-usage-value" id="diskImagesUsed">--</span>
                                    <span class="disk-usage-meta" id="diskImagesCount">-- files</span>
                                </div>
                                <div class="disk-usage-card">
                                    <span class="disk-usage-title">Thumbnails</span>
                                    <span class="disk-usage-value" id="diskThumbsUsed">--</span>
                                    <span class="disk-usage-meta" id="diskThumbsCount">-- files</span>
                                </div>
                                <div class="disk-usage-card">
                                    <span class="disk-usage-title">Cache</span>
                                    <span class="disk-usage-value" id="diskCacheUsed">--</span>
                                    <span class="disk-usage-meta" id="diskCacheEntries">-- entries</span>
                                </div>
                            </div>
                            <div class="disk-usage-summary">
                                <span id="thumbnailSpaceUsed">--</span>
                                <span id="thumbnailSpaceTotal" class="disk-usage-secondary"></span>
                            </div>
                        </div>
                    </div>
                    <div class="setting-row">
                        <label class="setting-label">
                            Management
                            <span class="info-icon" title="Manage thumbnail storage">ⓘ</span>
                        </label>
                        <div class="setting-control">
                            <button class="btn btn-danger" type="button" onclick="clearThumbnailCache()">
                                🗑️ Clear Thumbnail Cache
                            </button>
                        </div>
                    </div>
                    </div>
                </div>
            </section>

            <!-- Metadata Panel -->
            <section id="metadata" class="settings-panel">
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title">Metadata Settings</h3>
                    </div>
                    <div class="card-body">
                    ${renderToggle('Auto-Load Metadata', 'autoLoadMetadata', settings.autoLoadMetadata, 'Automatically extract EXIF and prompt data')}
                    ${renderToggle('Show Metadata Panel', 'showMetadataPanel', settings.showMetadataPanel, 'Display metadata panel by default')}
                    ${renderSelect('Display Mode', 'metadataDisplayMode', settings.metadataDisplayMode, ['compact', 'expanded', 'minimal'], 'How metadata is displayed')}
                    </div>
                </div>
            </section>

            <!-- Gallery Panel -->
            <section id="gallery" class="settings-panel">
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title">Gallery Settings</h3>
                    </div>
                    <div class="card-body">
                    ${renderToggle('Monitoring Enabled', 'monitoringEnabled', settings.monitoringEnabled, 'Enable automatic image monitoring')}
                    ${renderSettingRow('Processing Delay (seconds)', 'number', 'processingDelay', settings.processingDelay, 'Wait before processing new files', 0.5, 10, 0.5)}
                    ${renderSettingRow('Prompt Timeout (seconds)', 'number', 'promptTimeout', settings.promptTimeout, 'Keep prompt context active', 30, 600)}
                    ${renderToggle('Auto Cleanup Missing Files', 'autoCleanupMissing', settings.autoCleanupMissing, 'Remove records for missing files')}
                    ${renderSettingRow('Images Per Page', 'number', 'imagesPerPage', settings.imagesPerPage, 'Images per gallery page', 5, 100)}
                    <!-- Thumbnail size moved to Thumbnails panel -->
                    ${renderToggle('Enable Gallery Search', 'enableGallerySearch', settings.enableGallerySearch, 'Enable search in gallery')}
                    ${renderSettingRow('Max Concurrent Processing', 'number', 'maxConcurrentProcessing', settings.maxConcurrentProcessing, 'Concurrent processing tasks', 1, 10)}
                    ${renderSettingRow('Monitoring Directories', 'text', 'monitoringDirectories', settings.monitoringDirectories, 'Directories to monitor (comma-separated)', null, null, null, 'Auto-detect if empty')}
                    ${renderSettingRow('Supported Extensions', 'text', 'supportedExtensions', settings.supportedExtensions, 'File extensions to process', null, null, null, '.png, .jpg, .jpeg')}
                    ${renderSettingRow('Cleanup Interval (seconds)', 'number', 'cleanupInterval', settings.cleanupInterval, 'Time between cleanup runs', 60, 3600)}
                    ${renderSettingRow('Max Image Age (days)', 'number', 'maxImageAgeDays', settings.maxImageAgeDays, 'Clean up images older than', 7, 3650)}
                    </div>
                </div>
            </section>

            <!-- Notifications Panel -->
            <section id="notifications" class="settings-panel">
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title">Notifications & Webhooks</h3>
                    </div>
                    <div class="card-body">
                    <!-- Basic Notifications -->
                    <h4 class="settings-subsection">Toast Notifications</h4>
                    ${renderToggle('Enable Notifications', 'enableNotifications', settings.enableNotifications, 'Show toast notifications')}
                    ${renderSelect('Toast Position', 'toastPosition', settings.toastPosition, ['top-right', 'top-center', 'top-left', 'bottom-right', 'bottom-center', 'bottom-left'], 'Where toasts appear')}
                    ${renderSelect('Animation Style', 'animationStyle', settings.animationStyle || 'fade', ['fade', 'slide'], 'How toast enters/leaves')}
                    ${renderSettingRow('Toast Duration (seconds)', 'number', 'toastDuration', settings.toastDuration, 'How long toasts stay visible', 1, 30)}

                    <!-- Test Buttons -->
                    <div class="setting-row">
                        <label class="setting-label">
                            Test Notifications
                            <span class="info-icon" title="Send test notifications">ⓘ</span>
                        </label>
                        <div class="setting-control">
                            <button type="button" class="btn btn-primary" onclick="testNotifications()">🔔 Test All</button>
                        </div>
                    </div>

                    <!-- Webhook Settings -->
                    <h4 class="settings-subsection">Webhook Integrations</h4>
                    ${renderToggle('Enable Webhooks', 'enableWebhooks', settings.enableWebhooks, 'Send to external services')}
                    ${renderSettingRow('Slack Webhook URL', 'text', 'slackWebhookUrl', settings.slackWebhookUrl, 'Slack incoming webhook', null, null, null, 'https://hooks.slack.com/services/...')}
                    ${renderSettingRow('Discord Webhook URL', 'text', 'discordWebhookUrl', settings.discordWebhookUrl, 'Discord webhook', null, null, null, 'https://discord.com/api/webhooks/...')}
                    ${renderSettingRow('Teams Webhook URL', 'text', 'teamsWebhookUrl', settings.teamsWebhookUrl, 'Microsoft Teams webhook', null, null, null, 'https://outlook.office.com/webhook/...')}

                    <div class="setting-row">
                        <label class="setting-label">
                            Test Webhooks
                            <span class="info-icon" title="Send test messages">ⓘ</span>
                        </label>
                        <div class="setting-control" style="display: flex; gap: 8px;">
                            <button type="button" class="btn btn-sm" onclick="testWebhook('slack')">💬 Slack</button>
                            <button type="button" class="btn btn-sm" onclick="testWebhook('discord')">🎮 Discord</button>
                            <button type="button" class="btn btn-sm" onclick="testWebhook('teams')">📊 Teams</button>
                        </div>
                    </div>
                    </div>
                </div>
            </section>

            <!-- Logging Panel -->
            <section id="logging" class="settings-panel">
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title">Logging Settings</h3>
                    </div>
                    <div class="card-body">
                    ${renderSelect('Log Level', 'logLevel', settings.logLevel, ['OFF', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], 'Minimum severity to log')}
                    ${renderToggle('Console Logging', 'consoleLogging', settings.consoleLogging, 'Output to console')}
                    ${renderToggle('File Logging', 'fileLogging', settings.fileLogging, 'Save logs to file')}
                    ${renderSettingRow('Max Log File Size (MB)', 'number', 'maxLogFileSize', settings.maxLogFileSize, 'Size before rotation', 1, 100)}
                    ${renderSettingRow('Log Files to Keep', 'number', 'logBackupCount', settings.logBackupCount, 'Number of old logs to retain', 1, 20)}
                    ${renderSettingRow('Log Directory', 'text', 'logDirectory', settings.logDirectory, 'Where to store log files', null, null, null, 'ComfyUI/user/default/PromptManager/logs')}
                        <small class="locked-hint">Log files are stored alongside the PromptManager database.</small>
                    </div>
                </div>
            </section>

            <!-- Advanced Panel -->
            <section id="advanced" class="settings-panel">
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title">Advanced Settings</h3>
                    </div>
                    <div class="card-body">
                    ${renderToggle('Experimental Features', 'experimentalFeatures', settings.experimentalFeatures, 'Enable experimental features')}
                    ${renderToggle('Developer Mode', 'developerMode', settings.developerMode, 'Enable developer tools')}
                    ${renderToggle('Debug Mode', 'debugMode', settings.debugMode, 'Enable debug output')}
                    ${renderToggle('Performance Monitoring', 'performanceMonitoring', settings.performanceMonitoring, 'Track performance metrics')}
                    ${renderToggle('Auto Update', 'autoUpdate', settings.autoUpdate, 'Automatically check for updates')}
                    ${renderSelect('Update Channel', 'updateChannel', settings.updateChannel, ['stable', 'beta', 'nightly'], 'Which updates to receive')}
                    ${renderSettingRow('Max Undo History', 'number', 'maxUndoHistory', settings.maxUndoHistory, 'Number of undo steps to keep', 10, 500)}
                    ${renderToggle('Confirm Dangerous Actions', 'confirmDangerousActions', settings.confirmDangerousActions, 'Ask before destructive operations')}
                    </div>
                </div>
            </section>

            <!-- Migration Panel -->
            <section id="migration" class="settings-panel">
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title">Migration Settings</h3>
                    </div>
                    <div class="card-body">
                        <div class="settings-info">
                            <p>Manage migration from PromptManager v1 to v2 format</p>
                        </div>

                        <div class="settings-row">
                            <label class="settings-label">
                                Migration Status
                                <span class="tooltip">Current status of v1 to v2 migration</span>
                            </label>
                            <div class="settings-control">
                                <div class="migration-status-summary">
                                    <span id="migrationStatusIcon" class="migration-status-icon" aria-hidden="true">ℹ️</span>
                                    <span id="migrationStatus" class="migration-status-text migration-status--info">Not checked</span>
                                </div>
                                <ul id="migrationStatsList" class="migration-summary-list"></ul>
                            </div>
                        </div>

                        <div class="settings-row">
                            <label class="settings-label">
                                V1 Database Location
                                <span class="tooltip">Path to v1 prompts.db file</span>
                            </label>
                            <div class="settings-control">
                                <span id="v1DbPath">-</span>
                            </div>
                        </div>

                        <div class="migration-controls">
                            <button type="button" class="btn btn-primary" onclick="checkV1Database()" data-testid="migration-check">🔍 Check for V1 Database</button>
                            <button type="button" class="btn btn-success" onclick="executeMigration()" id="executeMigrationBtn" data-testid="migration-execute" disabled>📦 Execute Migration</button>
                            <button type="button" class="btn btn-danger" onclick="resetMigration()" data-testid="migration-reset">🔄 Reset Migration Status</button>
                        </div>

                        <div id="migrationResults" class="migration-results is-hidden">
                            <div class="card">
                                <div class="card-header">
                                    <h4>Migration Results</h4>
                                </div>
                                <div class="card-body" id="migrationResultsContent">
                                    <!-- Results will be displayed here -->
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </section>
        `;
    }

    const LOCKED_FIELDS = new Set(['logDirectory']);
    let lastVerifiedDatabase = null;
    let pendingMigrationPath = null;

    const HTML_ESCAPE_MAP = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
    };
    const escapeHtml = (value = '') =>
        value.replace(/[&<>"']/g, (char) => HTML_ESCAPE_MAP[char] || char);

    function updateDatabasePathField(path, options = {}) {
        const input = document.getElementById('databasePath');
        if (!input) return;
        const value = path || DEFAULT_DB_PATH;
        input.value = value;
        input.dataset.verifiedPath = value;
        if (options.focus) {
            input.focus();
        }
    }

    function updateDatabaseStatus(message, variant = 'status-info') {
        const status = document.getElementById('databasePathStatus');
        if (!status) return;
        status.className = `settings-info ${variant}`;
        status.innerHTML = message;
    }

    function renderDatabasePathControl(settings) {
        const value = settings.databasePath || DEFAULT_DB_PATH;
        return `
            <div class="setting-row database-path-row">
                <label class="setting-label">
                    Database Path
                    <span class="info-icon" title="Location of the prompts database file">ⓘ</span>
                </label>
                <div class="setting-control">
                    <input type="text" id="databasePath" name="databasePath" value="${value}"
                           placeholder="${DEFAULT_DB_PATH}" class="input">
                    <div class="database-actions">
                        <button class="btn btn-secondary" type="button" onclick="verifyDatabasePath()">
                            <i class="fa-solid fa-magnifying-glass" aria-hidden="true"></i> Verify
                        </button>
                        <button class="btn btn-primary" type="button" onclick="applyDatabasePath()">
                            <i class="fa-solid fa-check" aria-hidden="true"></i> Use Path
                        </button>
                        <button class="btn btn-ghost" type="button" onclick="resetDatabasePath()">
                            <i class="fa-solid fa-rotate-left" aria-hidden="true"></i> Reset
                        </button>
                    </div>
                    <div id="databasePathStatus" class="settings-info" aria-live="polite"></div>
                </div>
            </div>
        `;
    }

    async function verifyDatabasePath() {
        const input = document.getElementById('databasePath');
        if (!input) return;
        const rawPath = input.value.trim();
        if (!rawPath) {
            updateDatabaseStatus('Please enter a database path to verify.', 'status-warn');
            return;
        }
        updateDatabaseStatus('<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Verifying path…', 'status-info');
        lastVerifiedDatabase = null;
        pendingMigrationPath = null;
        try {
            const response = await fetch(DB_API_ENDPOINTS.verify, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: rawPath })
            });
            const payload = await response.json();
            if (!response.ok || payload.ok === false) {
                throw new Error(payload.error || response.statusText);
            }
            const info = payload.data || payload;
            lastVerifiedDatabase = info;
            input.dataset.verifiedPath = info.resolved;
            if (info.exists) {
                updateDatabaseStatus(`<span class="status-ok"><i class="fa-solid fa-circle-check" aria-hidden="true"></i> Database found at <code>${escapeHtml(info.resolved)}</code>.</span>`, 'status-ok');
            } else {
                const writableNote = info.writable ? '' : ' (destination may be read-only)';
                updateDatabaseStatus(`<span class="status-warn"><i class="fa-solid fa-triangle-exclamation" aria-hidden="true"></i> No database found at <code>${escapeHtml(info.resolved)}</code>${writableNote}. You can migrate your current database to this location.</span>`, 'status-warn');
                openDbMigrationModal(info.resolved);
            }
        } catch (error) {
            console.error('Failed to verify database path:', error);
            updateDatabaseStatus(`<span class="status-error"><i class="fa-solid fa-circle-xmark" aria-hidden="true"></i> ${escapeHtml(error.message || 'Verification failed.')}</span>`, 'status-error');
        }
    }

    async function applyDatabasePath() {
        const input = document.getElementById('databasePath');
        if (!input) return;
        const rawPath = input.value.trim();
        const verifiedPath = input.dataset.verifiedPath;
        if (!rawPath) {
            updateDatabaseStatus('Please enter a database path.', 'status-warn');
            return;
        }
        if (rawPath !== verifiedPath) {
            updateDatabaseStatus('Verify the path before applying changes.', 'status-warn');
            return;
        }
        if (lastVerifiedDatabase && !lastVerifiedDatabase.exists) {
            updateDatabaseStatus('The target path does not contain a database. Migrate or create one before applying.', 'status-warn');
            openDbMigrationModal(verifiedPath);
            return;
        }
        try {
            const response = await fetch(DB_API_ENDPOINTS.apply, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: rawPath })
            });
            const payload = await response.json();
            if (!response.ok || payload.ok === false) {
                throw new Error(payload.error || response.statusText);
            }
            const info = payload.data || payload;
            updateDatabasePathField(info.path);
            lastVerifiedDatabase = { resolved: info.path, exists: true };
            const settings = loadSettings();
            settings.databasePath = info.path;
            settings.databasePathCustom = info.custom;
            saveSettings(settings, { silent: true });
            updateDatabaseStatus(`<span class="status-ok"><i class="fa-solid fa-circle-check" aria-hidden="true"></i> Using database at <code>${escapeHtml(info.path)}</code>.</span>`, 'status-ok');
            showToast('Database path updated.', 'success');
        } catch (error) {
            console.error('Failed to apply database path:', error);
            updateDatabaseStatus(`<span class="status-error"><i class="fa-solid fa-circle-xmark" aria-hidden="true"></i> ${escapeHtml(error.message || 'Unable to apply database path.')}</span>`, 'status-error');
        }
    }

    async function resetDatabasePath() {
        try {
            const response = await fetch(DB_API_ENDPOINTS.apply, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: null })
            });
            const payload = await response.json();
            if (!response.ok || payload.ok === false) {
                throw new Error(payload.error || response.statusText);
            }
            const info = payload.data || payload;
            updateDatabasePathField(info.path, { focus: true });
            lastVerifiedDatabase = { resolved: info.path, exists: true };
            const settings = loadSettings();
            settings.databasePath = info.path;
            settings.databasePathCustom = false;
            saveSettings(settings, { silent: true });
            updateDatabaseStatus(`<span class="status-ok"><i class="fa-solid fa-circle-check" aria-hidden="true"></i> Database reset to default location.</span>`, 'status-ok');
            showToast('Database path reset to default.', 'info');
        } catch (error) {
            console.error('Failed to reset database path:', error);
            updateDatabaseStatus(`<span class="status-error"><i class="fa-solid fa-circle-xmark" aria-hidden="true"></i> ${escapeHtml(error.message || 'Unable to reset database path.')}</span>`, 'status-error');
        }
    }

    function openDbMigrationModal(path) {
        pendingMigrationPath = path;
        const modal = document.getElementById('dbMigrationModal');
        const message = document.getElementById('dbMigrationMessage');
        if (!modal || !message) return;
        message.innerHTML = `
            <p>No database was found at <code>${escapeHtml(path)}</code>.</p>
            <p>Would you like to migrate the current PromptManager database to this location?</p>
        `;
        modal.classList.add('active');
        modal.setAttribute('aria-hidden', 'false');
    }

    function closeDbMigrationModal() {
        const modal = document.getElementById('dbMigrationModal');
        if (!modal) return;
        modal.classList.remove('active');
        modal.setAttribute('aria-hidden', 'true');
        pendingMigrationPath = null;
    }

    async function confirmDbMigration() {
        if (!pendingMigrationPath) {
            closeDbMigrationModal();
            return;
        }
        updateDatabaseStatus('<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Migrating database…', 'status-info');
        try {
            const response = await fetch(DB_API_ENDPOINTS.migrate, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: pendingMigrationPath })
            });
            const payload = await response.json();
            if (!response.ok || payload.ok === false) {
                throw new Error(payload.error || response.statusText);
            }
            const info = payload.data || payload;
            updateDatabasePathField(info.new_path);
            lastVerifiedDatabase = { resolved: info.new_path, exists: true };
            const settings = loadSettings();
            settings.databasePath = info.new_path;
            settings.databasePathCustom = info.new_path !== DEFAULT_DB_PATH;
            saveSettings(settings, { silent: true });
            showToast('Database migrated successfully.', 'success');
            updateDatabaseStatus(`<span class="status-ok"><i class="fa-solid fa-circle-check" aria-hidden="true"></i> Database migrated to <code>${escapeHtml(info.new_path)}</code>.</span>`, 'status-ok');
        } catch (error) {
            console.error('Database migration failed:', error);
            updateDatabaseStatus(`<span class="status-error"><i class="fa-solid fa-circle-xmark" aria-hidden="true"></i> ${escapeHtml(error.message || 'Migration failed.')}</span>`, 'status-error');
        } finally {
            closeDbMigrationModal();
        }
    }

// Helper functions for rendering form controls
    function renderToggle(label, name, checked, tooltip) {
        return `
            <div class="setting-row">
                <label class="setting-label">
                    ${label}
                    <span class="info-icon" title="${tooltip}">ⓘ</span>
                </label>
                <div class="setting-control">
                    <label class="switch">
                        <input type="checkbox" name="${name}" ${checked ? 'checked' : ''}>
                        <span class="slider"></span>
                    </label>
                </div>
            </div>
        `;
    }

    function renderSettingRow(label, type, name, value, tooltip, min = null, max = null, step = null, placeholder = '') {
        const isLocked = LOCKED_FIELDS.has(name);
        return `
            <div class="setting-row">
                <label class="setting-label">
                    ${label}
                    <span class="info-icon" title="${tooltip}">ⓘ</span>
                </label>
                <div class="setting-control">
                    <input type="${type}" name="${name}" value="${value}"
                           ${min !== null ? `min="${min}"` : ''}
                           ${max !== null ? `max="${max}"` : ''}
                           ${step !== null ? `step="${step}"` : ''}
                           ${placeholder ? `placeholder="${placeholder}"` : ''}
                           ${isLocked ? 'readonly tabindex="-1" data-locked="true"' : ''}
                           class="input">
                </div>
            </div>
        `;
    }

    function renderSelect(label, name, value, options, tooltip) {
        return `
            <div class="setting-row">
                <label class="setting-label">
                    ${label}
                    <span class="info-icon" title="${tooltip}">ⓘ</span>
                </label>
                <div class="setting-control">
                    <select name="${name}" class="select">
                        ${options.map(opt => `
                            <option value="${opt}" ${value === opt ? 'selected' : ''}>${opt.charAt(0).toUpperCase() + opt.slice(1)}</option>
                        `).join('')}
                    </select>
                </div>
            </div>
        `;
    }

    // Setup panel navigation with smooth scrolling
    function setupPanelNavigation() {
        const navItems = document.querySelectorAll('.settings-nav-item');
        const panels = document.querySelectorAll('.settings-panel');
        const settingsContent = document.querySelector('.settings-content');

        navItems.forEach(item => {
            item.addEventListener('click', () => {
                const targetPanel = item.dataset.panel;
                const targetElement = document.getElementById(targetPanel);

                // Update nav
                navItems.forEach(nav => nav.classList.remove('active'));
                item.classList.add('active');

                // Update panels visibility
                panels.forEach(panel => {
                    panel.classList.toggle('active', panel.id === targetPanel);
                });

                // Smooth scroll to the target panel
                if (targetElement && settingsContent) {
                    targetElement.scrollIntoView({
                        behavior: 'smooth',
                        block: 'start'
                    });
                }
            });
        });
    }

    // Setup form handlers
    function setupFormHandlers() {
        const form = document.getElementById('settingsForm');
        if (!form) return;

        // Handle form changes
        let saveTimeout;
        form.addEventListener('change', async (e) => {
            // Auto-save on change if enabled
            const settings = await loadSettings();
            if (settings.autoSave) {
                // Debounce saves to avoid too many API calls
                clearTimeout(saveTimeout);
                saveTimeout = setTimeout(() => {
                    saveAllSettings();
                }, 1000);
            }
        });

        // Initialize disk usage display if on thumbnails panel
        if (document.getElementById('thumbnails')) {
            updateDiskUsage();
        }
    }

    // Clear thumbnail cache function
    window.clearThumbnailCache = async function() {
        if (!confirm('This will delete ALL cached thumbnails. They will be regenerated on demand. Continue?')) {
            return;
        }

        try {
            const response = await fetch('/api/v1/thumbnails/clear', {
                method: 'POST'
            });

            if (!response.ok) {
                throw new Error(`Clear failed: ${response.statusText}`);
            }

            const result = await response.json();
            showToast(`Cleared ${result.deleted} thumbnails`, 'success');
            updateDiskUsage(); // Refresh disk usage display
        } catch (error) {
            console.error('Clear error:', error);
            showToast(`Failed to clear cache: ${error.message}`, 'error');
        }
    };

    // Update disk usage display
    async function updateDiskUsage() {
        const formatBytes = (bytes) => {
            if (!Number.isFinite(bytes) || bytes <= 0) {
                return '0 B';
            }
            const units = ['B', 'KB', 'MB', 'GB', 'TB'];
            const exponent = Math.min(
                Math.floor(Math.log(bytes) / Math.log(1024)),
                units.length - 1,
            );
            const value = bytes / Math.pow(1024, exponent);
            return `${value.toFixed(value >= 10 || exponent === 0 ? 0 : 1)} ${units[exponent]}`;
        };

        const setCard = (valueId, countId, stats, options = {}) => {
            const valueEl = document.getElementById(valueId);
            const countEl = document.getElementById(countId);

            if (!valueEl && !countEl) {
                return;
            }

            if (!stats) {
                if (valueEl) valueEl.textContent = '--';
                if (countEl) countEl.textContent = options.emptyLabel || '--';
                return;
            }

            const sizeBytes = Number(stats.size_bytes || 0);
            if (valueEl) {
                valueEl.textContent = formatBytes(sizeBytes);
            }

            if (countEl) {
                if (options.countFormatter) {
                    countEl.textContent = options.countFormatter(stats);
                } else {
                    const count = Number(stats.file_count || stats.entry_count || 0);
                    const label = options.countLabel || 'files';
                    countEl.textContent = `${count.toLocaleString()} ${label}`;
                }
            }
        };

        try {
            const response = await fetch('/api/v1/thumbnails/disk-usage');
            if (!response.ok) {
                throw new Error(`Failed to fetch disk usage: ${response.statusText}`);
            }

            const data = await response.json();
            const summary = data.summary || {};
            const breakdown = data.breakdown || {};

            const usageElement = document.getElementById('thumbnailDiskUsage');
            const spaceUsedElement = document.getElementById('thumbnailSpaceUsed');
            const spaceTotalElement = document.getElementById('thumbnailSpaceTotal');

            if (usageElement) {
                const percent = Number.isFinite(summary.percentage_of_disk)
                    ? `${summary.percentage_of_disk.toFixed(2)}% of disk`
                    : '';
                usageElement.textContent = percent
                    ? `Using ${formatBytes(summary.total_bytes || 0)} • ${percent}`
                    : `Using ${formatBytes(summary.total_bytes || 0)}`;
            }

            setCard('diskImagesUsed', 'diskImagesCount', breakdown.images, {
                countLabel: 'files',
            });

            setCard('diskThumbsUsed', 'diskThumbsCount', breakdown.thumbnails, {
                countLabel: 'files',
            });

            setCard('diskCacheUsed', 'diskCacheEntries', breakdown.cache, {
                countFormatter: (stats) => {
                    const entries = Number(stats.entry_count || 0);
                    const cacheCount = Array.isArray(stats.caches) ? stats.caches.length : 0;
                    if (!cacheCount) {
                        return `${entries.toLocaleString()} entries`;
                    }
                    return `${entries.toLocaleString()} entries • ${cacheCount} caches`;
                },
                emptyLabel: 'No cache data',
            });

            if (spaceUsedElement) {
                spaceUsedElement.textContent = `Total: ${formatBytes(summary.total_bytes || 0)}`;
            }

            if (spaceTotalElement) {
                const diskTotal = formatBytes(summary.disk_total_bytes || 0);
                const diskFree = formatBytes(summary.disk_free_bytes || 0);
                spaceTotalElement.textContent = `Disk: ${diskTotal} • Free: ${diskFree}`;
            }
        } catch (error) {
            console.error('Failed to update disk usage:', error);
            const usageElement = document.getElementById('thumbnailDiskUsage');
            if (usageElement) {
                usageElement.textContent = 'Unable to load storage information';
            }
            setCard('diskImagesUsed', 'diskImagesCount');
            setCard('diskThumbsUsed', 'diskThumbsCount');
            setCard('diskCacheUsed', 'diskCacheEntries');
            const spaceUsedElement = document.getElementById('thumbnailSpaceUsed');
            const spaceTotalElement = document.getElementById('thumbnailSpaceTotal');
            if (spaceUsedElement) spaceUsedElement.textContent = '--';
            if (spaceTotalElement) spaceTotalElement.textContent = '';
        }
    }

    // Save all settings
    window.updateDatabasePathField = updateDatabasePathField;
    window.updateDatabaseStatus = updateDatabaseStatus;
    window.escapeHtml = escapeHtml;
    window.verifyDatabasePath = verifyDatabasePath;
    window.applyDatabasePath = applyDatabasePath;
    window.resetDatabasePath = resetDatabasePath;
    window.confirmDbMigration = confirmDbMigration;
    window.closeDbMigrationModal = closeDbMigrationModal;

    window.saveAllSettings = async function() {
        const form = document.getElementById('settingsForm');
        if (!form) return;

        const formData = new FormData(form);
        const settings = {};

        // Process form data
        for (const [key, value] of formData.entries()) {
            // Handle checkboxes
            if (form.elements[key].type === 'checkbox') {
                settings[key] = form.elements[key].checked;
            }
            // Handle numbers
            else if (form.elements[key].type === 'number') {
                settings[key] = parseFloat(value) || 0;
            }
            // Handle text/select
            else {
                settings[key] = value;
            }
        }

        // Handle unchecked checkboxes (FormData doesn't include them)
        const checkboxes = form.querySelectorAll('input[type="checkbox"]');
        checkboxes.forEach(cb => {
            if (!settings.hasOwnProperty(cb.name)) {
                settings[cb.name] = false;
            }
        });

        // Merge with defaults and save
        const merged = { ...defaultSettings, ...settings };
        await saveSettings(merged);
    };

    // Test notifications
    window.testNotifications = function() {
        const types = ['success', 'error', 'warning', 'info'];
        const messages = {
            success: 'This is a success notification',
            error: 'This is an error notification',
            warning: 'This is a warning notification',
            info: 'This is an information notification'
        };

        types.forEach((type, index) => {
            setTimeout(() => {
                showToast(messages[type], type);
            }, index * 500);
        });
    };

    window.testWebhook = function(service) {
        const settings = loadSettings();
        const webhookUrls = {
            slack: settings.slackWebhookUrl,
            discord: settings.discordWebhookUrl,
            teams: settings.teamsWebhookUrl,
            custom: settings.customWebhookUrl
        };

        if (!webhookUrls[service]) {
            showToast(`No ${service} webhook URL configured`, 'warning');
            return;
        }

        showToast(`Testing ${service} webhook...`, 'info');
        setTimeout(() => {
            showToast(`${service} webhook test completed`, 'success');
        }, 1000);
    };

    // Export functions for global access
    window.exportSettingsFunc = function() {
        const settings = loadSettings();
        const blob = new Blob([JSON.stringify(settings, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `prompt-manager-settings-${Date.now()}.json`;
        a.click();
        URL.revokeObjectURL(url);
        showToast('Settings exported successfully', 'success');
    };

    window.importSettingsFunc = function() {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = '.json';
        input.onchange = (e) => {
            const file = e.target.files[0];
            if (!file) return;

            const reader = new FileReader();
            reader.onload = (e) => {
                try {
                    const imported = JSON.parse(e.target.result);
                    const merged = { ...defaultSettings, ...imported };
                    saveSettings(merged);
                    renderSettingsPage();
                    showToast('Settings imported successfully', 'success');
                } catch (err) {
                    console.error('Import failed:', err);
                    showToast('Failed to import settings', 'error');
                }
            };
            reader.readAsText(file);
        };
        input.click();
    };

    window.resetSettingsFunc = function() {
        if (confirm('Reset all settings to defaults? This action cannot be undone.')) {
            saveSettings(defaultSettings);
            renderSettingsPage();
            showToast('Settings reset to defaults', 'success');
        }
    };

    function getMigrationElements() {
        return {
            statusText: document.getElementById('migrationStatus'),
            statusIcon: document.getElementById('migrationStatusIcon'),
            statsList: document.getElementById('migrationStatsList'),
            dbPath: document.getElementById('v1DbPath'),
            executeButton: document.getElementById('executeMigrationBtn'),
            resultsContainer: document.getElementById('migrationResults'),
            resultsContent: document.getElementById('migrationResultsContent')
        };
    }

    function setMigrationStatus(variant, message, icon) {
        const { statusText, statusIcon } = getMigrationElements();
        if (statusText) {
            statusText.textContent = message;
            statusText.className = `migration-status-text migration-status--${variant}`;
        }
        if (statusIcon) {
            statusIcon.textContent = icon;
        }
    }

    function updateMigrationStats(stats) {
        const { statsList } = getMigrationElements();
        if (!statsList) return;

        if (!stats) {
            statsList.innerHTML = '';
            return;
        }

        statsList.innerHTML = `
            <li>Prompts: ${stats.prompt_count ?? stats.prompts ?? 0}</li>
            <li>Images: ${stats.image_count ?? stats.images ?? 0}</li>
            <li>Categories: ${stats.category_count ?? stats.categories ?? 0}</li>
        `;
    }

    function showMigrationResult(content) {
        const { resultsContainer, resultsContent } = getMigrationElements();
        if (!resultsContainer || !resultsContent) return;

        resultsContainer.classList.remove('is-hidden');
        resultsContent.innerHTML = content;
    }

    window.resetMigration = function() {
        if (!confirm('Reset migration status? This will allow the migration prompt to show again.')) {
            return;
        }

        if (window.MigrationService) {
            window.MigrationService.resetMigration();
        } else {
            localStorage.removeItem('promptmanager_v2_migrated');
            localStorage.removeItem('promptmanager_v2_migrated_date');
            localStorage.removeItem('promptmanager_v2_fresh_install');
            localStorage.removeItem('promptmanager_v2_fresh_install_date');
        }

        const { dbPath, executeButton, resultsContainer } = getMigrationElements();
        setMigrationStatus('info', 'Not checked', 'ℹ️');
        updateMigrationStats(null);
        if (dbPath) dbPath.textContent = '-';
        if (executeButton) executeButton.disabled = true;
        if (resultsContainer) resultsContainer.classList.add('is-hidden');

        showToast('Migration status reset. Reload page to see migration prompt.', 'success');
    };

    window.checkV1Database = async function() {
        const { dbPath, executeButton, resultsContainer } = getMigrationElements();
        setMigrationStatus('info', 'Checking migration status…', '🔄');
        updateMigrationStats(null);
        if (resultsContainer) resultsContainer.classList.add('is-hidden');

        if (!window.MigrationService) {
            console.warn('MigrationService not available');
            setMigrationStatus('error', 'Migration service unavailable', '❌');
            return null;
        }

        try {
            const info = await window.MigrationService.getMigrationInfo();
            const stats = info.v1Info || {};

            if (dbPath) {
                dbPath.textContent = stats.path || stats.v1_path || 'Unknown';
            }
            updateMigrationStats(stats);

            if (info.needed) {
                setMigrationStatus('success', 'V1 database found!', '✅');
                if (executeButton) executeButton.disabled = false;
                showToast('V1 database found! Migration is available.', 'success');
            } else {
                setMigrationStatus('warning', 'No v1 database found', '⚠️');
                if (executeButton) executeButton.disabled = true;
                showToast('No v1 database found or migration already completed', 'info');
            }

            return info;
        } catch (error) {
            console.error('Migration check failed:', error);
            setMigrationStatus('error', `Error: ${error.message}`, '❌');
            updateMigrationStats(null);
            if (executeButton) executeButton.disabled = true;
            showToast('Failed to check for v1 database', 'error');
            return null;
        }
    };

    window.executeMigration = async function() {
        if (!confirm('Execute migration from v1 to v2? This will copy all prompts and images to the new format.')) {
            return null;
        }

        if (!window.MigrationService) {
            console.warn('MigrationService not available');
            setMigrationStatus('error', 'Migration service unavailable', '❌');
            showToast('Migration service unavailable', 'error');
            return null;
        }

        const { executeButton, resultsContainer } = getMigrationElements();
        if (resultsContainer) {
            resultsContainer.classList.add('is-hidden');
        }
        if (executeButton) {
            executeButton.disabled = true;
            executeButton.textContent = '⏳ Opening modal...';
        }
        setMigrationStatus('info', 'Opening migration modal…', '🔄');

        try {
            const modalOpened = await window.MigrationService.triggerManualMigration();
            if (!modalOpened) {
                setMigrationStatus('warning', 'No v1 database found', '⚠️');
                return null;
            }

            if (executeButton) {
                executeButton.textContent = '⏳ Migrating...';
            }
            setMigrationStatus('info', 'Migration in progress…', '🔄');

            const result = await window.MigrationService.startMigration();
            const stats = result?.stats || {};

            if (result?.success) {
                setMigrationStatus('success', 'Migration completed!', '✅');
                updateMigrationStats({
                    prompt_count: stats.prompts_migrated ?? 0,
                    image_count: stats.images_migrated ?? stats.images_linked ?? 0,
                    category_count: stats.categories_migrated ?? 0
                });
                showMigrationResult(
                    `<p class="migration-result-message is-success">Migration completed via modal.</p>
                    <ul>
                        <li>Prompts migrated: ${stats.prompts_migrated ?? 0}</li>
                        <li>Images linked: ${stats.images_migrated ?? stats.images_linked ?? 0}</li>
                        <li>Categories preserved: ${stats.categories_migrated ?? 0}</li>
                    </ul>
                    <p class="migration-result-message">Backup saved to: ${stats.backup_path || 'N/A'}</p>`
                );
                showToast('Migration completed successfully!', 'success');
            } else {
                const errorMessage = stats.error || 'Migration failed';
                setMigrationStatus('error', `Migration failed: ${errorMessage}`, '❌');
                showMigrationResult(`<p class="migration-result-message is-error">${errorMessage}</p>`);
                showToast(`Migration failed: ${errorMessage}`, 'error');
            }

            return result;
        } catch (error) {
            console.error('Migration failed:', error);
            setMigrationStatus('error', `Error: ${error.message}`, '❌');
            showMigrationResult(`<p class="migration-result-message is-error">${error.message}</p>`);
            showToast('Migration failed', 'error');
            return null;
        } finally {
            if (executeButton) {
                executeButton.disabled = false;
                executeButton.textContent = '📦 Execute Migration';
            }
        }
    };


})();
