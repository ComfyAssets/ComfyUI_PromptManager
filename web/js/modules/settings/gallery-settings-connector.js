/**
 * Gallery Settings Connector
 * Bridges the settings panel with gallery and ViewerJS components
 * Ensures all components respect user preferences
 */

const GallerySettingsConnector = (function() {
    'use strict';

    // Settings structure matching backend expectations
    const defaultSettings = {
        gallery: {
            itemsPerPage: 20,
            viewMode: 'grid', // grid, list, masonry
            sortOrder: 'date_desc',
            autoRefresh: false,
            refreshInterval: 30000
        },
        viewer: {
            theme: 'dark',
            toolbar: true,
            navbar: true,
            title: true,
            keyboard: true,
            backdrop: true,
            button: true,
            fullscreen: true,
            inline: false,
            viewed: true,
            tooltip: true,
            movable: true,
            zoomable: true,
            rotatable: true,
            scalable: true,
            transition: true,
            loading: true,
            loop: true,
            slideOnTouch: true,
            zoomRatio: 0.1,
            minZoomRatio: 0.01,
            maxZoomRatio: 100
        },
        filmstrip: {
            enabled: true,
            position: 'bottom', // bottom, top, left, right
            thumbnailSize: 'medium', // small, medium, large
            autoHide: false,
            scrollButtons: true
        },
        metadata: {
            enabled: true,
            position: 'right', // right, left, overlay
            fields: ['prompt', 'negative_prompt', 'model', 'settings', 'tags'],
            autoCollapse: false,
            copyButton: true
        },
        thumbnails: {
            quality: 85,
            maxWidth: 256,
            maxHeight: 256,
            format: 'jpeg', // jpeg, webp, png
            generateOnUpload: true,
            lazyGeneration: true
        }
    };

    let currentSettings = {};
    let settingsListeners = new Set();

    /**
     * Initialize settings connector
     */
    function init() {
        // Load settings from localStorage and backend
        loadSettings();

        // Setup settings panel UI
        setupSettingsPanel();

        // Listen for external setting changes
        setupEventListeners();

        // Sync with backend periodically
        setInterval(syncWithBackend, 60000); // Every minute

        return {
            get: getSettings,
            set: updateSettings,
            reset: resetSettings,
            subscribe: subscribeToChanges,
            unsubscribe: unsubscribeFromChanges
        };
    }

    /**
     * Load settings from storage
     */
    async function loadSettings() {
        // Try to load from localStorage first
        const localSettings = localStorage.getItem('promptManagerGallerySettings');
        if (localSettings) {
            try {
                currentSettings = JSON.parse(localSettings);
            } catch (e) {
                console.warn('Invalid local settings, using defaults');
                currentSettings = { ...defaultSettings };
            }
        } else {
            currentSettings = { ...defaultSettings };
        }

        // Sync with backend
        try {
            const response = await fetch('/api/prompt_manager/settings');
            if (response.ok) {
                const backendSettings = await response.json();
                // Merge backend settings (backend takes priority)
                currentSettings = deepMerge(currentSettings, backendSettings);
                saveToLocalStorage();
            }
        } catch (error) {
            console.warn('Failed to load backend settings:', error);
        }

        // Notify all components of loaded settings
        notifyListeners('load', currentSettings);
    }

    /**
     * Setup the settings panel UI
     */
    function setupSettingsPanel() {
        const panel = document.getElementById('gallery-settings-panel');
        if (!panel) return;

        // Gallery Settings Section
        const gallerySection = createSettingsSection('Gallery Display', [
            {
                type: 'select',
                id: 'viewMode',
                label: 'View Mode',
                options: [
                    { value: 'grid', label: 'Grid View' },
                    { value: 'list', label: 'List View' },
                    { value: 'masonry', label: 'Masonry View' }
                ],
                value: currentSettings.gallery.viewMode,
                onChange: (value) => updateSettings({ gallery: { viewMode: value } })
            },
            {
                type: 'number',
                id: 'itemsPerPage',
                label: 'Items Per Page',
                min: 10,
                max: 100,
                step: 10,
                value: currentSettings.gallery.itemsPerPage,
                onChange: (value) => updateSettings({ gallery: { itemsPerPage: parseInt(value) } })
            },
            {
                type: 'select',
                id: 'sortOrder',
                label: 'Sort Order',
                options: [
                    { value: 'date_desc', label: 'Newest First' },
                    { value: 'date_asc', label: 'Oldest First' },
                    { value: 'prompt_asc', label: 'Prompt A-Z' },
                    { value: 'prompt_desc', label: 'Prompt Z-A' }
                ],
                value: currentSettings.gallery.sortOrder,
                onChange: (value) => updateSettings({ gallery: { sortOrder: value } })
            }
        ]);

        // Viewer Settings Section
        const viewerSection = createSettingsSection('Image Viewer', [
            {
                type: 'select',
                id: 'theme',
                label: 'Theme',
                options: [
                    { value: 'dark', label: 'Dark' },
                    { value: 'light', label: 'Light' },
                    { value: 'auto', label: 'Auto' }
                ],
                value: currentSettings.viewer.theme,
                onChange: (value) => updateSettings({ viewer: { theme: value } })
            },
            {
                type: 'checkbox',
                id: 'toolbar',
                label: 'Show Toolbar',
                value: currentSettings.viewer.toolbar,
                onChange: (value) => updateSettings({ viewer: { toolbar: value } })
            },
            {
                type: 'checkbox',
                id: 'fullscreen',
                label: 'Enable Fullscreen',
                value: currentSettings.viewer.fullscreen,
                onChange: (value) => updateSettings({ viewer: { fullscreen: value } })
            },
            {
                type: 'checkbox',
                id: 'keyboard',
                label: 'Keyboard Navigation',
                value: currentSettings.viewer.keyboard,
                onChange: (value) => updateSettings({ viewer: { keyboard: value } })
            }
        ]);

        // Filmstrip Settings Section
        const filmstripSection = createSettingsSection('Filmstrip', [
            {
                type: 'checkbox',
                id: 'filmstripEnabled',
                label: 'Enable Filmstrip',
                value: currentSettings.filmstrip.enabled,
                onChange: (value) => updateSettings({ filmstrip: { enabled: value } })
            },
            {
                type: 'select',
                id: 'filmstripPosition',
                label: 'Position',
                options: [
                    { value: 'bottom', label: 'Bottom' },
                    { value: 'top', label: 'Top' },
                    { value: 'left', label: 'Left' },
                    { value: 'right', label: 'Right' }
                ],
                value: currentSettings.filmstrip.position,
                disabled: !currentSettings.filmstrip.enabled,
                onChange: (value) => updateSettings({ filmstrip: { position: value } })
            },
            {
                type: 'select',
                id: 'thumbnailSize',
                label: 'Thumbnail Size',
                options: [
                    { value: 'small', label: 'Small' },
                    { value: 'medium', label: 'Medium' },
                    { value: 'large', label: 'Large' }
                ],
                value: currentSettings.filmstrip.thumbnailSize,
                disabled: !currentSettings.filmstrip.enabled,
                onChange: (value) => updateSettings({ filmstrip: { thumbnailSize: value } })
            }
        ]);

        // Metadata Settings Section
        const metadataSection = createSettingsSection('Metadata Display', [
            {
                type: 'checkbox',
                id: 'metadataEnabled',
                label: 'Show Metadata',
                value: currentSettings.metadata.enabled,
                onChange: (value) => updateSettings({ metadata: { enabled: value } })
            },
            {
                type: 'select',
                id: 'metadataPosition',
                label: 'Position',
                options: [
                    { value: 'right', label: 'Right Panel' },
                    { value: 'left', label: 'Left Panel' },
                    { value: 'overlay', label: 'Overlay' }
                ],
                value: currentSettings.metadata.position,
                disabled: !currentSettings.metadata.enabled,
                onChange: (value) => updateSettings({ metadata: { position: value } })
            },
            {
                type: 'multiselect',
                id: 'metadataFields',
                label: 'Display Fields',
                options: [
                    { value: 'prompt', label: 'Prompt' },
                    { value: 'negative_prompt', label: 'Negative Prompt' },
                    { value: 'model', label: 'Model' },
                    { value: 'settings', label: 'Generation Settings' },
                    { value: 'tags', label: 'Tags' },
                    { value: 'workflow', label: 'Workflow' }
                ],
                value: currentSettings.metadata.fields,
                disabled: !currentSettings.metadata.enabled,
                onChange: (value) => updateSettings({ metadata: { fields: value } })
            }
        ]);

        // Add sections to panel
        panel.innerHTML = '';
        panel.appendChild(gallerySection);
        panel.appendChild(viewerSection);
        panel.appendChild(filmstripSection);
        panel.appendChild(metadataSection);

        // Add action buttons
        const actions = document.createElement('div');
        actions.className = 'settings-actions';

        const saveBtn = document.createElement('button');
        saveBtn.className = 'btn btn-primary';
        saveBtn.textContent = 'Save Settings';
        saveBtn.onclick = () => saveSettings();

        const resetBtn = document.createElement('button');
        resetBtn.className = 'btn btn-secondary';
        resetBtn.textContent = 'Reset to Defaults';
        resetBtn.onclick = () => resetSettings();

        actions.appendChild(saveBtn);
        actions.appendChild(resetBtn);
        panel.appendChild(actions);
    }

    /**
     * Create a settings section
     */
    function createSettingsSection(title, controls) {
        const section = document.createElement('div');
        section.className = 'settings-section';

        const header = document.createElement('h3');
        header.className = 'settings-section-title';
        header.textContent = title;
        section.appendChild(header);

        const content = document.createElement('div');
        content.className = 'settings-section-content';

        controls.forEach(control => {
            const controlEl = createControl(control);
            content.appendChild(controlEl);
        });

        section.appendChild(content);
        return section;
    }

    /**
     * Create a control element
     */
    function createControl(config) {
        const wrapper = document.createElement('div');
        wrapper.className = 'settings-control';

        const label = document.createElement('label');
        label.htmlFor = config.id;
        label.textContent = config.label;
        wrapper.appendChild(label);

        let input;

        switch (config.type) {
            case 'checkbox':
                input = document.createElement('input');
                input.type = 'checkbox';
                input.id = config.id;
                input.checked = config.value;
                input.disabled = config.disabled;
                input.onchange = (e) => config.onChange(e.target.checked);
                break;

            case 'select':
                input = document.createElement('select');
                input.id = config.id;
                input.disabled = config.disabled;
                config.options.forEach(opt => {
                    const option = document.createElement('option');
                    option.value = opt.value;
                    option.textContent = opt.label;
                    option.selected = opt.value === config.value;
                    input.appendChild(option);
                });
                input.onchange = (e) => config.onChange(e.target.value);
                break;

            case 'number':
                input = document.createElement('input');
                input.type = 'number';
                input.id = config.id;
                input.min = config.min;
                input.max = config.max;
                input.step = config.step;
                input.value = config.value;
                input.disabled = config.disabled;
                input.onchange = (e) => config.onChange(e.target.value);
                break;

            case 'multiselect':
                input = document.createElement('div');
                input.className = 'multiselect';
                input.id = config.id;

                config.options.forEach(opt => {
                    const checkWrapper = document.createElement('label');
                    checkWrapper.className = 'multiselect-option';

                    const check = document.createElement('input');
                    check.type = 'checkbox';
                    check.value = opt.value;
                    check.checked = config.value.includes(opt.value);
                    check.disabled = config.disabled;
                    check.onchange = () => {
                        const selected = Array.from(input.querySelectorAll('input:checked'))
                            .map(cb => cb.value);
                        config.onChange(selected);
                    };

                    checkWrapper.appendChild(check);
                    checkWrapper.appendChild(document.createTextNode(' ' + opt.label));
                    input.appendChild(checkWrapper);
                });
                break;
        }

        wrapper.appendChild(input);
        return wrapper;
    }

    /**
     * Update settings
     */
    function updateSettings(updates) {
        currentSettings = deepMerge(currentSettings, updates);
        saveToLocalStorage();
        notifyListeners('update', updates);
    }

    /**
     * Save settings to backend
     */
    async function saveSettings() {
        try {
            const response = await fetch('/api/prompt_manager/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(currentSettings)
            });

            if (response.ok) {
                showNotification('Settings saved successfully', 'success');
                notifyListeners('save', currentSettings);
            } else {
                throw new Error('Failed to save settings');
            }
        } catch (error) {
            console.error('Failed to save settings:', error);
            showNotification('Failed to save settings', 'error');
        }
    }

    /**
     * Reset settings to defaults
     */
    function resetSettings() {
        if (confirm('Are you sure you want to reset all settings to defaults?')) {
            currentSettings = { ...defaultSettings };
            saveToLocalStorage();
            setupSettingsPanel(); // Recreate UI with default values
            notifyListeners('reset', currentSettings);
            showNotification('Settings reset to defaults', 'info');
        }
    }

    /**
     * Get current settings or a specific path
     */
    function getSettings(path) {
        if (!path) return { ...currentSettings };

        const parts = path.split('.');
        let value = currentSettings;

        for (const part of parts) {
            value = value[part];
            if (value === undefined) return undefined;
        }

        return value;
    }

    /**
     * Subscribe to settings changes
     */
    function subscribeToChanges(listener) {
        settingsListeners.add(listener);
        return () => unsubscribeFromChanges(listener);
    }

    /**
     * Unsubscribe from settings changes
     */
    function unsubscribeFromChanges(listener) {
        settingsListeners.delete(listener);
    }

    /**
     * Notify all listeners of changes
     */
    function notifyListeners(type, data) {
        settingsListeners.forEach(listener => {
            try {
                listener({ type, data });
            } catch (error) {
                console.error('Settings listener error:', error);
            }
        });

        // Also dispatch a custom event
        window.dispatchEvent(new CustomEvent('gallerySettingsChanged', {
            detail: { type, data }
        }));
    }

    /**
     * Save to localStorage
     */
    function saveToLocalStorage() {
        try {
            localStorage.setItem('promptManagerGallerySettings', JSON.stringify(currentSettings));
        } catch (error) {
            console.warn('Failed to save to localStorage:', error);
        }
    }

    /**
     * Sync with backend periodically
     */
    async function syncWithBackend() {
        try {
            const response = await fetch('/api/prompt_manager/settings');
            if (response.ok) {
                const backendSettings = await response.json();

                // Only update if backend has newer data
                if (backendSettings.lastUpdated > currentSettings.lastUpdated) {
                    currentSettings = deepMerge(currentSettings, backendSettings);
                    saveToLocalStorage();
                    setupSettingsPanel(); // Update UI
                    notifyListeners('sync', currentSettings);
                }
            }
        } catch (error) {
            console.warn('Settings sync failed:', error);
        }
    }

    /**
     * Setup event listeners
     */
    function setupEventListeners() {
        // Listen for settings changes from other tabs
        window.addEventListener('storage', (e) => {
            if (e.key === 'promptManagerGallerySettings' && e.newValue) {
                try {
                    currentSettings = JSON.parse(e.newValue);
                    setupSettingsPanel(); // Update UI
                    notifyListeners('external', currentSettings);
                } catch (error) {
                    console.warn('Invalid settings from storage event');
                }
            }
        });

        // Listen for specific component requests
        window.addEventListener('requestGallerySettings', (e) => {
            e.detail.callback(getSettings(e.detail.path));
        });
    }

    /**
     * Deep merge objects
     */
    function deepMerge(target, source) {
        const result = { ...target };

        for (const key in source) {
            if (source.hasOwnProperty(key)) {
                if (source[key] && typeof source[key] === 'object' && !Array.isArray(source[key])) {
                    result[key] = deepMerge(result[key] || {}, source[key]);
                } else {
                    result[key] = source[key];
                }
            }
        }

        return result;
    }

    /**
     * Show notification
     */
    function showNotification(message, type = 'info') {
        // This would integrate with your notification system
        const notification = document.createElement('div');
        notification.className = `notification notification-${type}`;
        notification.textContent = message;
        document.body.appendChild(notification);

        setTimeout(() => {
            notification.remove();
        }, 3000);
    }

    // Public API
    return {
        init: init,
        get: getSettings,
        set: updateSettings,
        reset: resetSettings,
        subscribe: subscribeToChanges,
        unsubscribe: unsubscribeFromChanges
    };

})();

// Initialize on DOM ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => GallerySettingsConnector.init());
} else {
    GallerySettingsConnector.init();
}

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = GallerySettingsConnector;
}