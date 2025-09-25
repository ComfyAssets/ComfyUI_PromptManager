/**
 * MetadataManager Service
 * Handles metadata extraction, caching, and display for images
 * @module MetadataManager
 */
const MetadataManager = (function() {
    'use strict';

    function createStub() {
        const target = {};
        const stub = new Proxy(target, {
            get: (obj, prop) => {
                if (prop in obj) {
                    return obj[prop];
                }
                return (...args) => {
                    if (prop === 'loadMetadata' || prop === 'prefetchMetadata' || prop === 'getMetadata') {
                        return Promise.resolve(null);
                    }
                    return stub;
                };
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
        console.info('[PromptManager] metadata manager skipped outside PromptManager UI context');
        return createStub();
    }

    // Private variables
    const metadataCache = new Map();
    const extractors = new Map();
    const panels = new Map();

    const defaultConfig = {
        enableCache: true,
        cacheSize: 100, // Maximum number of cached entries
        cacheTimeout: 3600000, // 1 hour in milliseconds
        extractTimeout: 5000, // Extraction timeout
        panel: {
            position: 'right', // 'right', 'left', 'bottom', 'floating'
            width: 350,
            height: 'auto',
            collapsible: true,
            collapsed: false,
            autoShow: true,
            showCopyButtons: true,
            showExportButton: true,
            theme: 'dark'
        },
        fields: {
            // Define which fields to show and their display names
            standard: {
                filename: 'Filename',
                dimensions: 'Dimensions',
                fileSize: 'File Size',
                mimeType: 'Type',
                lastModified: 'Modified'
            },
            exif: {
                make: 'Camera Make',
                model: 'Camera Model',
                dateTime: 'Date Taken',
                exposureTime: 'Exposure',
                fNumber: 'F-Stop',
                iso: 'ISO',
                focalLength: 'Focal Length',
                lens: 'Lens'
            },
            custom: {
                prompt: 'Prompt',
                negativePrompt: 'Negative Prompt',
                model: 'Model',
                sampler: 'Sampler',
                steps: 'Steps',
                cfgScale: 'CFG Scale',
                seed: 'Seed',
                clipSkip: 'CLIP Skip'
            }
        }
    };

    // Cache entry structure
    class CacheEntry {
        constructor(data) {
            this.data = data;
            this.timestamp = Date.now();
            this.hits = 0;
        }

        isExpired(timeout) {
            return Date.now() - this.timestamp > timeout;
        }

        access() {
            this.hits++;
            return this.data;
        }
    }

    // Private methods
    function getCacheKey(source) {
        if (typeof source === 'string') {
            return source;
        } else if (source instanceof File) {
            return `file:${source.name}:${source.size}:${source.lastModified}`;
        } else if (source instanceof Blob) {
            return `blob:${source.size}:${source.type}`;
        }
        return `unknown:${Date.now()}`;
    }

    function cleanCache() {
        if (metadataCache.size <= defaultConfig.cacheSize) return;

        // Remove expired entries first
        const timeout = defaultConfig.cacheTimeout;
        for (const [key, entry] of metadataCache) {
            if (entry.isExpired(timeout)) {
                metadataCache.delete(key);
            }
        }

        // If still over limit, remove least recently used
        if (metadataCache.size > defaultConfig.cacheSize) {
            const entries = Array.from(metadataCache.entries());
            entries.sort((a, b) => a[1].hits - b[1].hits);

            const toRemove = entries.slice(0, Math.floor(defaultConfig.cacheSize * 0.2));
            toRemove.forEach(([key]) => metadataCache.delete(key));
        }
    }

    async function extractFromImage(source) {
        const metadata = {
            standard: {},
            exif: {},
            custom: {},
            raw: {}
        };

        try {
            // Extract standard metadata
            if (typeof source === 'string') {
                // URL source
                metadata.standard.filename = source.split('/').pop().split('?')[0];

                // Fetch image for further processing
                const response = await fetch(source);
                const blob = await response.blob();
                metadata.standard.fileSize = formatFileSize(blob.size);
                metadata.standard.mimeType = blob.type;
            } else if (source instanceof File) {
                metadata.standard.filename = source.name;
                metadata.standard.fileSize = formatFileSize(source.size);
                metadata.standard.mimeType = source.type;
                metadata.standard.lastModified = new Date(source.lastModified).toLocaleString();
            }

            // Load image to get dimensions
            const img = await loadImage(source);
            metadata.standard.dimensions = `${img.width} × ${img.height}`;

            // Extract EXIF data if available
            if (typeof EXIF !== 'undefined') {
                const exifData = await extractEXIF(source);
                if (exifData) {
                    metadata.exif = formatEXIFData(exifData);
                }
            }

            // Extract custom metadata (e.g., from PNG chunks)
            const customData = await extractCustomMetadata(source);
            if (customData) {
                metadata.custom = customData;
            }

            // Run registered extractors
            for (const [name, extractor] of extractors) {
                try {
                    const result = await extractor(source, metadata);
                    if (result) {
                        metadata[name] = result;
                    }
                } catch (error) {
                    console.warn(`Extractor ${name} failed:`, error);
                }
            }

        } catch (error) {
            console.error('Metadata extraction failed:', error);
            metadata.error = error.message;
        }

        return metadata;
    }

    function loadImage(source) {
        return new Promise((resolve, reject) => {
            const img = new Image();
            img.onload = () => resolve(img);
            img.onerror = reject;

            if (typeof source === 'string') {
                img.src = source;
            } else if (source instanceof File || source instanceof Blob) {
                const url = URL.createObjectURL(source);
                img.src = url;
                img.onload = () => {
                    URL.revokeObjectURL(url);
                    resolve(img);
                };
            } else {
                reject(new Error('Invalid image source'));
            }
        });
    }

    async function extractEXIF(source) {
        // This would use EXIF.js library if available
        // Placeholder for EXIF extraction
        return null;
    }

    async function extractCustomMetadata(source) {
        // Extract custom metadata from PNG chunks or other formats
        // This is specific to AI-generated images that embed parameters

        try {
            // For PNG images, check for text chunks
            if (source instanceof File || source instanceof Blob) {
                const arrayBuffer = await source.arrayBuffer();
                return parsePNGMetadata(arrayBuffer);
            } else if (typeof source === 'string') {
                const response = await fetch(source);
                const arrayBuffer = await response.arrayBuffer();
                return parsePNGMetadata(arrayBuffer);
            }
        } catch (error) {
            console.warn('Custom metadata extraction failed:', error);
        }

        return null;
    }

    function parsePNGMetadata(arrayBuffer) {
        const metadata = {};
        const dataView = new DataView(arrayBuffer);

        // Check PNG signature
        if (dataView.getUint32(0) !== 0x89504E47) {
            return metadata;
        }

        let offset = 8; // Skip PNG signature

        while (offset < dataView.byteLength) {
            const length = dataView.getUint32(offset);
            const type = String.fromCharCode(
                dataView.getUint8(offset + 4),
                dataView.getUint8(offset + 5),
                dataView.getUint8(offset + 6),
                dataView.getUint8(offset + 7)
            );

            if (type === 'tEXt' || type === 'iTXt') {
                const textData = new Uint8Array(arrayBuffer, offset + 8, length);
                const text = new TextDecoder('utf-8').decode(textData);
                const [key, value] = text.split('\0');

                if (key === 'parameters' || key === 'prompt') {
                    // Parse AI generation parameters
                    try {
                        const params = parseAIParameters(value);
                        Object.assign(metadata, params);
                    } catch (e) {
                        metadata[key] = value;
                    }
                } else {
                    metadata[key] = value;
                }
            }

            offset += length + 12; // Move to next chunk

            if (type === 'IEND') break;
        }

        return metadata;
    }

    function parseAIParameters(text) {
        const params = {};

        // Parse common AI generation formats
        // Format: "prompt: ..., negative prompt: ..., steps: 20, sampler: DPM++, ..."
        const patterns = {
            prompt: /^([^,\n]+?)(?:,|$)/,
            negativePrompt: /negative\s*prompt:\s*([^,\n]+?)(?:,|$)/i,
            model: /model:\s*([^,\n]+?)(?:,|$)/i,
            sampler: /sampler:\s*([^,\n]+?)(?:,|$)/i,
            steps: /steps:\s*(\d+)/i,
            cfgScale: /cfg\s*scale:\s*([0-9.]+)/i,
            seed: /seed:\s*(\d+)/i,
            clipSkip: /clip\s*skip:\s*(\d+)/i
        };

        for (const [key, pattern] of Object.entries(patterns)) {
            const match = text.match(pattern);
            if (match) {
                params[key] = match[1].trim();
            }
        }

        return params;
    }

    function formatEXIFData(exifData) {
        const formatted = {};

        const mappings = {
            'Make': 'make',
            'Model': 'model',
            'DateTime': 'dateTime',
            'ExposureTime': 'exposureTime',
            'FNumber': 'fNumber',
            'ISOSpeedRatings': 'iso',
            'FocalLength': 'focalLength',
            'LensModel': 'lens'
        };

        for (const [exifKey, metadataKey] of Object.entries(mappings)) {
            if (exifData[exifKey]) {
                formatted[metadataKey] = exifData[exifKey];
            }
        }

        return formatted;
    }

    function formatFileSize(bytes) {
        const units = ['B', 'KB', 'MB', 'GB'];
        let size = bytes;
        let unitIndex = 0;

        while (size >= 1024 && unitIndex < units.length - 1) {
            size /= 1024;
            unitIndex++;
        }

        return `${size.toFixed(2)} ${units[unitIndex]}`;
    }

    function createMetadataPanel(config = {}) {
        const panelConfig = Object.assign({}, defaultConfig.panel, config);
        const panelId = generatePanelId();

        const panel = document.createElement('div');
        panel.className = `metadata-panel metadata-panel-${panelConfig.position}`;
        panel.setAttribute('data-panel-id', panelId);
        panel.setAttribute('data-theme', panelConfig.theme);

        // Create header
        const header = document.createElement('div');
        header.className = 'metadata-panel-header';
        header.innerHTML = `
            <h3>Image Metadata</h3>
            <div class="metadata-panel-actions">
                ${panelConfig.showExportButton ? '<button class="metadata-export" title="Export">📤</button>' : ''}
                ${panelConfig.collapsible ? '<button class="metadata-collapse" title="Collapse">−</button>' : ''}
                <button class="metadata-close" title="Close">×</button>
            </div>
        `;

        // Create content area
        const content = document.createElement('div');
        content.className = 'metadata-panel-content';

        // Create sections for different metadata types
        const sections = document.createElement('div');
        sections.className = 'metadata-sections';

        panel.appendChild(header);
        panel.appendChild(content);
        content.appendChild(sections);

        // Set dimensions
        if (panelConfig.position === 'right' || panelConfig.position === 'left') {
            panel.style.width = `${panelConfig.width}px`;
        }

        if (panelConfig.height !== 'auto') {
            panel.style.height = `${panelConfig.height}px`;
        }

        // Attach event handlers
        attachPanelEventHandlers(panel, panelId, panelConfig);

        // Store panel instance
        panels.set(panelId, {
            id: panelId,
            element: panel,
            config: panelConfig,
            currentMetadata: null
        });

        return panelId;
    }

    function generatePanelId() {
        return `metadata_panel_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
    }

    function attachPanelEventHandlers(panel, panelId, config) {
        // Close button
        const closeBtn = panel.querySelector('.metadata-close');
        closeBtn.addEventListener('click', () => {
            hidePanel(panelId);
        });

        // Collapse button
        if (config.collapsible) {
            const collapseBtn = panel.querySelector('.metadata-collapse');
            const content = panel.querySelector('.metadata-panel-content');

            collapseBtn.addEventListener('click', () => {
                const isCollapsed = panel.classList.toggle('collapsed');
                collapseBtn.textContent = isCollapsed ? '+' : '−';
                content.style.display = isCollapsed ? 'none' : 'block';
            });
        }

        // Export button
        if (config.showExportButton) {
            const exportBtn = panel.querySelector('.metadata-export');
            exportBtn.addEventListener('click', () => {
                exportMetadata(panelId);
            });
        }

        // Make panel draggable if floating
        if (config.position === 'floating') {
            makePanelDraggable(panel);
        }
    }

    function makePanelDraggable(panel) {
        const header = panel.querySelector('.metadata-panel-header');
        let isDragging = false;
        let currentX;
        let currentY;
        let initialX;
        let initialY;

        header.style.cursor = 'move';

        header.addEventListener('mousedown', dragStart);

        function dragStart(e) {
            initialX = e.clientX - panel.offsetLeft;
            initialY = e.clientY - panel.offsetTop;

            if (e.target === header) {
                isDragging = true;
            }
        }

        document.addEventListener('mousemove', drag);
        document.addEventListener('mouseup', dragEnd);

        function drag(e) {
            if (!isDragging) return;

            e.preventDefault();
            currentX = e.clientX - initialX;
            currentY = e.clientY - initialY;

            panel.style.left = `${currentX}px`;
            panel.style.top = `${currentY}px`;
        }

        function dragEnd() {
            isDragging = false;
        }
    }

    function displayMetadata(panelId, metadata) {
        const panelInstance = panels.get(panelId);
        if (!panelInstance) return;

        const sections = panelInstance.element.querySelector('.metadata-sections');
        sections.innerHTML = '';

        // Store current metadata
        panelInstance.currentMetadata = metadata;

        // Display each metadata section
        const fieldGroups = defaultConfig.fields;

        for (const [groupName, fields] of Object.entries(fieldGroups)) {
            const groupData = metadata[groupName];
            if (!groupData || Object.keys(groupData).length === 0) continue;

            const section = document.createElement('div');
            section.className = 'metadata-section';
            section.innerHTML = `<h4>${groupName.charAt(0).toUpperCase() + groupName.slice(1)}</h4>`;

            const list = document.createElement('dl');
            list.className = 'metadata-list';

            for (const [key, label] of Object.entries(fields)) {
                if (groupData[key] !== undefined && groupData[key] !== null) {
                    const dt = document.createElement('dt');
                    dt.textContent = label;

                    const dd = document.createElement('dd');
                    dd.innerHTML = `
                        <span class="metadata-value">${escapeHtml(groupData[key])}</span>
                        ${panelInstance.config.showCopyButtons ?
                            `<button class="metadata-copy" data-value="${escapeHtml(groupData[key])}" title="Copy">📋</button>` :
                            ''}
                    `;

                    list.appendChild(dt);
                    list.appendChild(dd);
                }
            }

            section.appendChild(list);
            sections.appendChild(section);
        }

        // Add copy button handlers
        if (panelInstance.config.showCopyButtons) {
            sections.querySelectorAll('.metadata-copy').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    const value = e.target.getAttribute('data-value');
                    copyToClipboard(value);

                    // Visual feedback
                    e.target.textContent = '✓';
                    setTimeout(() => {
                        e.target.textContent = '📋';
                    }, 1000);
                });
            });
        }
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    function copyToClipboard(text) {
        if (navigator.clipboard) {
            navigator.clipboard.writeText(text);
        } else {
            // Fallback for older browsers
            const textarea = document.createElement('textarea');
            textarea.value = text;
            textarea.style.position = 'fixed';
            textarea.style.opacity = '0';
            document.body.appendChild(textarea);
            textarea.select();
            document.execCommand('copy');
            document.body.removeChild(textarea);
        }
    }

    function exportMetadata(panelId) {
        const panelInstance = panels.get(panelId);
        if (!panelInstance || !panelInstance.currentMetadata) return;

        const metadata = panelInstance.currentMetadata;
        const json = JSON.stringify(metadata, null, 2);

        // Create download link
        const blob = new Blob([json], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `metadata_${Date.now()}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }

    function showPanel(panelId) {
        const panelInstance = panels.get(panelId);
        if (!panelInstance) return;

        panelInstance.element.classList.add('visible');
    }

    function hidePanel(panelId) {
        const panelInstance = panels.get(panelId);
        if (!panelInstance) return;

        panelInstance.element.classList.remove('visible');
    }

    // Public API
    return {
        /**
         * Initialize MetadataManager
         * @param {Object} config - Configuration options
         */
        init: function(config = {}) {
            Object.assign(defaultConfig, config);

            // Inject required CSS
            this.injectStyles();

            // Set up periodic cache cleanup
            setInterval(cleanCache, 60000); // Clean every minute

            return this;
        },

        /**
         * Extract metadata from an image
         * @param {string|File|Blob} source - Image source
         * @param {boolean} useCache - Whether to use cache
         * @returns {Promise<Object>} Metadata object
         */
        extract: async function(source, useCache = true) {
            const cacheKey = getCacheKey(source);

            // Check cache
            if (useCache && defaultConfig.enableCache) {
                const cached = metadataCache.get(cacheKey);
                if (cached && !cached.isExpired(defaultConfig.cacheTimeout)) {
                    return cached.access();
                }
            }

            // Extract metadata
            const metadata = await extractFromImage(source);

            // Cache result
            if (defaultConfig.enableCache) {
                metadataCache.set(cacheKey, new CacheEntry(metadata));
                cleanCache();
            }

            return metadata;
        },

        /**
         * Register a custom metadata extractor
         * @param {string} name - Extractor name
         * @param {Function} extractor - Extractor function
         */
        registerExtractor: function(name, extractor) {
            if (typeof extractor !== 'function') {
                throw new Error('Extractor must be a function');
            }
            extractors.set(name, extractor);
        },

        /**
         * Unregister a metadata extractor
         * @param {string} name - Extractor name
         */
        unregisterExtractor: function(name) {
            extractors.delete(name);
        },

        /**
         * Create a metadata display panel
         * @param {Object} config - Panel configuration
         * @returns {string} Panel ID
         */
        createPanel: function(config = {}) {
            return createMetadataPanel(config);
        },

        /**
         * Attach panel to container
         * @param {string} panelId - Panel ID
         * @param {HTMLElement|string} container - Container element or selector
         */
        attachPanel: function(panelId, container) {
            const panelInstance = panels.get(panelId);
            if (!panelInstance) return false;

            if (typeof container === 'string') {
                container = document.querySelector(container);
            }

            if (!container) return false;

            container.appendChild(panelInstance.element);
            return true;
        },

        /**
         * Display metadata in panel
         * @param {string} panelId - Panel ID
         * @param {Object} metadata - Metadata to display
         */
        display: function(panelId, metadata) {
            displayMetadata(panelId, metadata);
            showPanel(panelId);
        },

        /**
         * Extract and display metadata
         * @param {string} panelId - Panel ID
         * @param {string|File|Blob} source - Image source
         */
        extractAndDisplay: async function(panelId, source) {
            const metadata = await this.extract(source);
            this.display(panelId, metadata);
            return metadata;
        },

        /**
         * Show panel
         * @param {string} panelId - Panel ID
         */
        showPanel: function(panelId) {
            showPanel(panelId);
        },

        /**
         * Hide panel
         * @param {string} panelId - Panel ID
         */
        hidePanel: function(panelId) {
            hidePanel(panelId);
        },

        /**
         * Destroy panel
         * @param {string} panelId - Panel ID
         */
        destroyPanel: function(panelId) {
            const panelInstance = panels.get(panelId);
            if (!panelInstance) return;

            if (panelInstance.element.parentNode) {
                panelInstance.element.parentNode.removeChild(panelInstance.element);
            }

            panels.delete(panelId);
        },

        /**
         * Clear metadata cache
         */
        clearCache: function() {
            metadataCache.clear();
        },

        /**
         * Get cache statistics
         * @returns {Object} Cache statistics
         */
        getCacheStats: function() {
            return {
                size: metadataCache.size,
                maxSize: defaultConfig.cacheSize,
                entries: Array.from(metadataCache.keys())
            };
        },

        /**
         * Update default configuration
         * @param {Object} config - New configuration
         */
        updateConfig: function(config) {
            Object.assign(defaultConfig, config);
        },

        /**
         * Inject required CSS styles
         */
        injectStyles: function() {
            if (document.getElementById('metadata-manager-styles')) return;

            const styles = document.createElement('style');
            styles.id = 'metadata-manager-styles';
            styles.textContent = `
                /* Metadata panel styles */
                .metadata-panel {
                    position: fixed;
                    background: #1a1a1a;
                    border: 1px solid #333;
                    color: #fff;
                    font-family: system-ui, -apple-system, sans-serif;
                    font-size: 14px;
                    z-index: 9100;
                    display: none;
                    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.3);
                    transition: transform 0.3s, opacity 0.3s;
                }

                .metadata-panel.visible {
                    display: block;
                }

                /* Position variations */
                .metadata-panel-right {
                    top: 0;
                    right: 0;
                    bottom: 0;
                    transform: translateX(100%);
                }

                .metadata-panel-right.visible {
                    transform: translateX(0);
                }

                .metadata-panel-left {
                    top: 0;
                    left: 0;
                    bottom: 0;
                    transform: translateX(-100%);
                }

                .metadata-panel-left.visible {
                    transform: translateX(0);
                }

                .metadata-panel-bottom {
                    bottom: 0;
                    left: 0;
                    right: 0;
                    transform: translateY(100%);
                }

                .metadata-panel-bottom.visible {
                    transform: translateY(0);
                }

                .metadata-panel-floating {
                    top: 50px;
                    right: 50px;
                    min-width: 350px;
                    max-width: 500px;
                    border-radius: 8px;
                }

                /* Panel header */
                .metadata-panel-header {
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                    padding: 12px 16px;
                    background: #0a0a0a;
                    border-bottom: 1px solid #333;
                }

                .metadata-panel-header h3 {
                    margin: 0;
                    font-size: 16px;
                    font-weight: 600;
                    color: #fff;
                }

                .metadata-panel-actions {
                    display: flex;
                    gap: 8px;
                }

                .metadata-panel-actions button {
                    background: transparent;
                    border: none;
                    color: #888;
                    font-size: 18px;
                    cursor: pointer;
                    padding: 4px 8px;
                    border-radius: 4px;
                    transition: all 0.2s;
                }

                .metadata-panel-actions button:hover {
                    background: rgba(255, 255, 255, 0.1);
                    color: #fff;
                }

                /* Panel content */
                .metadata-panel-content {
                    padding: 16px;
                    overflow-y: auto;
                    max-height: calc(100vh - 60px);
                }

                .metadata-sections {
                    display: flex;
                    flex-direction: column;
                    gap: 20px;
                }

                .metadata-section h4 {
                    margin: 0 0 12px 0;
                    font-size: 14px;
                    font-weight: 600;
                    color: #4fc3f7;
                    text-transform: uppercase;
                    letter-spacing: 0.5px;
                }

                .metadata-list {
                    display: grid;
                    grid-template-columns: 120px 1fr;
                    gap: 8px 12px;
                    margin: 0;
                }

                .metadata-list dt {
                    font-weight: 500;
                    color: #888;
                    text-align: right;
                    word-break: break-word;
                }

                .metadata-list dd {
                    margin: 0;
                    display: flex;
                    align-items: center;
                    gap: 8px;
                    word-break: break-word;
                }

                .metadata-value {
                    flex: 1;
                    color: #e0e0e0;
                    font-family: 'Courier New', monospace;
                    font-size: 13px;
                }

                .metadata-copy {
                    flex-shrink: 0;
                    background: transparent;
                    border: 1px solid #444;
                    border-radius: 4px;
                    padding: 2px 6px;
                    font-size: 12px;
                    cursor: pointer;
                    opacity: 0.5;
                    transition: opacity 0.2s;
                }

                .metadata-copy:hover {
                    opacity: 1;
                }

                /* Collapsed state */
                .metadata-panel.collapsed .metadata-panel-content {
                    display: none;
                }

                /* Dark theme (default) */
                .metadata-panel[data-theme="dark"] {
                    background: #1a1a1a;
                    color: #fff;
                }

                /* Light theme */
                .metadata-panel[data-theme="light"] {
                    background: #fff;
                    color: #333;
                    border-color: #ddd;
                }

                .metadata-panel[data-theme="light"] .metadata-panel-header {
                    background: #f5f5f5;
                    border-bottom-color: #ddd;
                }

                .metadata-panel[data-theme="light"] .metadata-panel-header h3 {
                    color: #333;
                }

                .metadata-panel[data-theme="light"] .metadata-list dt {
                    color: #666;
                }

                .metadata-panel[data-theme="light"] .metadata-value {
                    color: #333;
                }

                /* Responsive adjustments */
                @media (max-width: 768px) {
                    .metadata-panel-right,
                    .metadata-panel-left {
                        width: 100% !important;
                    }

                    .metadata-panel-floating {
                        top: 0;
                        right: 0;
                        left: 0;
                        max-width: 100%;
                        border-radius: 0;
                    }
                }
            `;
            document.head.appendChild(styles);
        }
    };
})();

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = MetadataManager;
}

// Attach to window for global access
if (typeof window !== 'undefined') {
    window.MetadataManager = MetadataManager;
}
