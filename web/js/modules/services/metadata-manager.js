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
            showExportButton: false,
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
                positivePrompt: 'Prompt - positive',
                negativePrompt: 'Prompt - negative',
                model: 'Model',
                loras: 'Lora(s)',
                cfgScale: 'cfgScale',
                steps: 'steps',
                sampler: 'sampler',
                seed: 'seed',
                clipSkip: 'clipSkip',
                workflow: 'Workflow'
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
        } else if (typeof source === 'number') {
            return `id:${source}`;
        } else if (source instanceof File) {
            return `file:${source.name}:${source.size}:${source.lastModified}`;
        } else if (source instanceof Blob) {
            return `blob:${source.size}:${source.type}`;
        }
        return `unknown:${Date.now()}`;
    }

    function extractImageIdFromSource(source) {
        if (typeof source !== 'string') {
            return null;
        }

        const match = source.match(/(?:generated-images|gallery\/images)\/(\d+)/);
        if (match && match[1]) {
            const parsed = Number.parseInt(match[1], 10);
            return Number.isFinite(parsed) ? parsed : null;
        }

        return null;
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
            if (typeof source === 'string') {
                metadata.standard.filename = source.split('/').pop().split('?')[0];

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

            const img = await loadImage(source);
            metadata.standard.dimensions = `${img.width} × ${img.height}`;

            if (typeof EXIF !== 'undefined') {
                const exifData = await extractEXIF(source);
                if (exifData) {
                    metadata.exif = formatEXIFData(exifData);
                }
            }

            const customData = await extractCustomMetadata(source);
            if (customData) {
                console.debug('Custom metadata extracted:', customData);
                if (customData.summary && typeof customData.summary === 'object') {
                    metadata.custom = customData.summary;
                    // Also add summary at root level for compatibility
                    metadata.summary = customData.summary;
                } else if (typeof customData === 'object') {
                    metadata.custom = customData;
                }

                if (customData.raw && typeof customData.raw === 'object') {
                    metadata.raw = Object.assign({}, metadata.raw, customData.raw);
                }
            }

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

        augmentWithComfySummary(metadata);
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

    async function fetchMetadataByImageId(imageId) {
        const id = Number(imageId);
        if (!Number.isFinite(id)) {
            return null;
        }

        const endpoints = [
            `/api/v1/generated-images/${id}/metadata`,
            `/prompt_manager/api/v1/generated-images/${id}/metadata`,
            `/api/prompt_manager/api/v1/generated-images/${id}/metadata`,
            `/api/v1/gallery/images/${id}`,
            `/prompt_manager/api/v1/gallery/images/${id}`,
            `/api/prompt_manager/api/v1/gallery/images/${id}`,
            `/api/prompt_manager/images/${id}`,
            `/prompt_manager/api/prompt_manager/images/${id}`,
            `/api/prompt_manager/gallery/images/${id}`,
            `/prompt_manager/api/prompt_manager/gallery/images/${id}`
        ];

        for (const endpoint of endpoints) {
            try {
                const response = await fetch(endpoint, {
                    headers: {
                        'Accept': 'application/json'
                    }
                });

                if (!response.ok) {
                    continue;
                }

                const result = await response.json();
                if (result && result.success === false) {
                    continue;
                }

                const payload = result?.data || result;
                if (!payload) {
                    continue;
                }

                // For the metadata endpoint, the structure is different
                if (endpoint.includes('/metadata')) {
                    // The /generated-images/{id}/metadata endpoint returns data directly
                    // with fields like metadata, prompt_metadata, workflow, etc.
                    let extractedMetadata = payload.metadata || {};

                    // Parse if string
                    if (typeof extractedMetadata === 'string' && extractedMetadata !== '') {
                        try {
                            extractedMetadata = JSON.parse(extractedMetadata);
                        } catch (error) {
                            console.debug('Metadata field is not valid JSON');
                            extractedMetadata = {};
                        }
                    }

                    // Transform the full response to include all available data
                    const metadata = transformApiMetadata({
                        ...extractedMetadata,
                        ...payload,  // Include all fields from response
                        metadata: extractedMetadata,
                        comfy_parsed: payload.comfy_parsed,
                        positive_prompt: payload.positive_prompt || extractedMetadata.positive_prompt,
                        negative_prompt: payload.negative_prompt || extractedMetadata.negative_prompt,
                        seed: payload.seed || extractedMetadata.seed,
                        sampler: payload.sampler || extractedMetadata.sampler,
                        steps: payload.steps || extractedMetadata.steps,
                        cfg_scale: payload.cfg_scale || extractedMetadata.cfg_scale,
                        model: payload.model || extractedMetadata.model,
                        workflow: payload.workflow,
                    });

                    // Check if we have valid metadata in either custom or summary fields
                    if (metadata && (Object.keys(metadata.custom || {}).length > 0 || Object.keys(metadata.summary || {}).length > 0)) {
                        augmentWithComfySummary(metadata);
                        console.debug('[MetadataManager] Retrieved metadata from dedicated endpoint', {
                            endpoint,
                            imageId: id,
                            hasCustom: Boolean(metadata?.custom && Object.keys(metadata.custom).length > 0),
                            hasSummary: Boolean(metadata?.summary && Object.keys(metadata.summary).length > 0),
                        });
                        return metadata;
                    }
                }

                // Original logic for other endpoints
                let baseMetadata = payload.metadata || payload.prompt_metadata || payload.comfy_parsed || null;
                if (typeof baseMetadata === 'string' && baseMetadata !== '') {
                    try {
                        baseMetadata = JSON.parse(baseMetadata);
                    } catch (error) {
                        console.debug('Metadata API by ID returned non-JSON metadata string');
                        baseMetadata = null;
                    }
                }

                // Check if we have any useful data in the payload
                const hasUsefulData = baseMetadata || payload.summary || payload.custom || payload.standard ||
                                     payload.positive_prompt || payload.model || payload.workflow;
                if (!hasUsefulData) {
                    continue;
                }

                let metadata = baseMetadata || payload;
                if (!metadata.summary && !metadata.standard && !metadata.custom) {
                    metadata = transformApiMetadata({
                        metadata: baseMetadata || payload,
                        comfy_parsed: payload.comfy_parsed,
                        positive_prompt: payload.positive_prompt,
                        negative_prompt: payload.negative_prompt,
                        seed: payload.seed,
                        sampler: payload.sampler,
                        steps: payload.steps,
                        cfg_scale: payload.cfg_scale,
                        model: payload.model,
                        workflow: payload.workflow,
                    });
                }

                if (metadata && typeof metadata === 'object' && !metadata.raw) {
                    metadata.raw = baseMetadata || payload;
                }

                augmentWithComfySummary(metadata);
                console.debug('[MetadataManager] Retrieved metadata by image ID', {
                    endpoint,
                    imageId: id,
                    hasSummary: Boolean(metadata?.summary),
                });
                return metadata;
            } catch (error) {
                console.debug(`Metadata API by ID failed (${endpoint}):`, error.message || error);
            }
        }

        return null;
    }

    async function extractCustomMetadata(source) {
        // Extract custom metadata using the Python API
        // This ensures consistent parsing across the application

        try {
            // First, try to get metadata by filename if source is a URL with a filename
            if (typeof source === 'string') {
                // Extract filename from URL
                let filename = null;
                try {
                    // Handle both absolute and relative URLs
                    const url = new URL(source, window.location.origin);
                    const pathParts = url.pathname.split('/');
                    filename = pathParts[pathParts.length - 1];

                    // Remove query parameters if present
                    if (filename.includes('?')) {
                        filename = filename.split('?')[0];
                    }

                    // Decode URL encoding
                    filename = decodeURIComponent(filename);

                    console.debug('Extracting metadata for filename:', filename, 'from URL:', source);

                    // Try to get metadata directly by filename
                    if (filename && filename.includes('.')) {
                        // First try by image ID if we can extract it from the filename
                        const imageIdMatch = filename.match(/generated_(\d+)_/);
                        if (imageIdMatch) {
                            const imageId = imageIdMatch[1];
                            console.debug('Found image ID in filename:', imageId);
                            const metadataById = await fetchMetadataByImageId(imageId);

                            // Validate metadata before returning
                            if (metadataById) {
                                const invalidValues = ['None', 'Not found', ''];
                                const isValidPrompt = (value) => {
                                    if (!value) return false;
                                    if (invalidValues.includes(value)) return false;
                                    // Reject "Generate a prompt from..." which is UI placeholder text
                                    if (value.startsWith('Generate a prompt')) return false;
                                    return true;
                                };

                                const hasValidPrompts = (
                                    isValidPrompt(metadataById.positive_prompt) ||
                                    isValidPrompt(metadataById.negative_prompt) ||
                                    isValidPrompt(metadataById.comfy_parsed?.positive_prompt) ||
                                    isValidPrompt(metadataById.comfy_parsed?.negative_prompt) ||
                                    isValidPrompt(metadataById.summary?.positivePrompt) ||
                                    isValidPrompt(metadataById.summary?.positive_prompt) ||
                                    isValidPrompt(metadataById.custom?.positivePrompt) ||
                                    isValidPrompt(metadataById.custom?.positive_prompt)
                                );

                                // Only return if valid, otherwise continue to file upload method
                                if (hasValidPrompts) {
                                    console.debug('Found valid metadata by image ID:', imageId);
                                    return metadataById;
                                } else {
                                    console.debug('Metadata by image ID invalid, continuing to file extraction:', imageId);
                                }
                            }
                        }

                        const metadataEndpoints = [
                            `/api/v1/metadata/${encodeURIComponent(filename)}`,
                            `/prompt_manager/api/v1/metadata/${encodeURIComponent(filename)}`,
                            `/api/prompt_manager/api/v1/metadata/${encodeURIComponent(filename)}`
                        ];

                        for (const endpoint of metadataEndpoints) {
                            try {
                                console.debug('Trying metadata endpoint:', endpoint);
                                const response = await fetch(endpoint);
                                if (response.ok) {
                                    const result = await response.json();
                                    console.debug('Metadata API response:', result);
                                    if (result.success && result.data) {
                                        // Transform API response to match expected format
                                        return transformApiMetadata(result.data);
                                    }
                                } else {
                                    console.debug(`Metadata endpoint ${endpoint} returned status:`, response.status);
                                }
                            } catch (err) {
                                console.debug(`Metadata endpoint ${endpoint} failed:`, err);
                            }
                        }
                    }
                } catch (urlErr) {
                    console.debug('URL parsing failed:', urlErr);
                }
            }

            // Fallback to file upload method
            let file;

            // Convert source to File/Blob if needed
            if (source instanceof File || source instanceof Blob) {
                file = source;
            } else if (typeof source === 'string') {
                // If it's a URL, fetch it first
                const response = await fetch(source);
                const blob = await response.blob();
                file = new File([blob], 'image.png', { type: blob.type });
            } else {
                return null;
            }

            // Use the Python API for metadata extraction
            const formData = new FormData();
            formData.append('file', file);

            // Try multiple endpoint patterns for compatibility
            const endpoints = [
                '/api/v1/metadata/extract',
                '/prompt_manager/api/v1/metadata/extract',
                '/api/prompt_manager/api/v1/metadata/extract'
            ];

            for (const endpoint of endpoints) {
                try {
                    const response = await fetch(endpoint, {
                        method: 'POST',
                        body: formData
                    });

                    if (response.ok) {
                        const result = await response.json();
                        if (result.success && result.data) {
                            // Transform API response to match expected format
                            return transformApiMetadata(result.data);
                        }
                    }
                } catch (err) {
                    console.debug(`Metadata API endpoint ${endpoint} failed:`, err);
                }
            }
        } catch (error) {
            console.warn('Custom metadata extraction failed:', error);
        }

        return null;
    }

    function transformWorkflowMetadata(workflowData) {
        // Transform raw ComfyUI workflow nodes into displayable metadata
        const metadata = {
            summary: {},
            workflow: {},
            custom: {}
        };

        // Extract key information from workflow nodes
        let positivePrompt = '';
        let negativePrompt = '';
        let model = '';
        let loras = [];
        let sampler = '';
        let steps = '';
        let cfg = '';
        let seed = '';
        let width = '';
        let height = '';

        // Track if we found PromptManager nodes (they take priority)
        let foundPromptManager = false;

        // First pass: Look for PromptManager nodes (they are first-class citizens!)
        for (const [nodeId, node] of Object.entries(workflowData)) {
            const classType = node.class_type || '';
            const inputs = node.inputs || {};

            // PromptManager nodes are OUR nodes - they get absolute priority!
            if (classType === 'PromptManagerV2') {
                // V2 has both positive and negative in one node
                positivePrompt = inputs.text || inputs.positive_text || '';
                negativePrompt = inputs.negative_text || inputs.negative || '';
                foundPromptManager = true;
            } else if (classType === 'PromptManager') {
                // Original PromptManager - may have both or just positive
                const text = inputs.text || inputs.prompt_text || '';
                const negText = inputs.negative_text || inputs.negative || '';
                if (text) {
                    positivePrompt = text;
                    foundPromptManager = true;
                }
                if (negText) {
                    negativePrompt = negText;
                    foundPromptManager = true;
                }
            } else if (classType === 'PromptManagerPositive') {
                // Dedicated positive prompt node
                positivePrompt = inputs.prompt_text || inputs.text || inputs.positive || '';
                foundPromptManager = true;
            } else if (classType === 'PromptManagerNegative') {
                // Dedicated negative prompt node
                negativePrompt = inputs.prompt_text || inputs.text || inputs.negative || '';
                foundPromptManager = true;
            }
        }

        // Second pass: Extract other metadata and fallback to CLIP nodes if no PromptManager found
        for (const [nodeId, node] of Object.entries(workflowData)) {
            const classType = node.class_type || '';
            const inputs = node.inputs || {};

            // Only use CLIPTextEncode if we didn't find PromptManager nodes
            if (!foundPromptManager && classType === 'CLIPTextEncode') {
                if (inputs.text) {
                    // Skip if text is a node reference (array) - we only want direct text values
                    if (typeof inputs.text === 'string') {
                        // Check node title or position to determine positive/negative
                        const title = node._meta?.title || '';
                        if (title.toLowerCase().includes('positive') || !positivePrompt) {
                            positivePrompt = inputs.text;
                        } else if (title.toLowerCase().includes('negative') || !negativePrompt) {
                            negativePrompt = inputs.text;
                        }
                    }
                }
            }

            // Extract model info
            if (classType === 'CheckpointLoaderSimple' || classType === 'UNETLoader' ||
                classType === 'CheckpointLoader' || classType.includes('Checkpoint')) {
                model = inputs.ckpt_name || inputs.unet_name || inputs.checkpoint_name || model;
            }

            // Extract LoRA info - including LoraManager support
            if (classType.includes('Lora') || classType.includes('LoRA')) {
                // Special handling for LoraManager nodes
                if (classType === 'Lora Loader (LoraManager)' || classType === 'LoraLoader_LoraManager') {
                    // LoraManager uses a different structure
                    const lorasData = inputs.loras?.__value__ || inputs.loras;
                    if (Array.isArray(lorasData)) {
                        lorasData.forEach(lora => {
                            if (lora.active !== false && lora.name) {
                                const strength = lora.strength || lora.clipStrength || 1.0;
                                loras.push(`${lora.name} (${strength})`);
                            }
                        });
                    }
                    // Also check text field for inline LoRA syntax
                    const loraText = inputs.text || '';
                    const loraMatch = loraText.match(/<lora:([^:>]+):([0-9.]+)>/g);
                    if (loraMatch) {
                        loraMatch.forEach(match => {
                            const parts = match.match(/<lora:([^:>]+):([0-9.]+)>/);
                            if (parts) {
                                loras.push(`${parts[1]} (${parts[2]})`);
                            }
                        });
                    }
                } else {
                    // Standard LoRA loader nodes
                    const loraName = inputs.lora_name || inputs.model_name || '';
                    if (loraName) {
                        const strength = inputs.strength_model || inputs.strength || 1.0;
                        loras.push(`${loraName} (${strength})`);
                    }
                }
            }

            // Extract sampler settings
            if (classType.includes('KSampler') || classType.includes('Sampler')) {
                sampler = inputs.sampler_name || sampler;
                steps = inputs.steps || steps;
                cfg = inputs.cfg || inputs.cfg_scale || cfg;  // Handle both 'cfg' and 'cfg_scale'
                seed = inputs.seed || seed;
            }

            // Extract image dimensions
            if (classType === 'EmptyLatentImage' || classType === 'EmptyLatentBatch') {
                width = inputs.width || width;
                height = inputs.height || height;
            }
        }

        // Populate metadata structure - put ComfyUI metadata in 'custom' to match field definitions
        metadata.custom = {
            positivePrompt: positivePrompt || 'Not found',
            negativePrompt: negativePrompt || 'Not found',
            model: model || 'Unknown',
            loras: loras.length > 0 ? loras.join(', ') : undefined,
            cfgScale: cfg.toString() || 'N/A',
            steps: steps.toString() || 'N/A',
            sampler: sampler || 'Unknown',
            seed: seed.toString() || 'N/A',
            clipSkip: '', // Not extracted yet
            workflow: `Embedded (${Object.keys(workflowData).length} nodes)`
        };

        // Also keep summary for backward compatibility
        metadata.summary = {
            positivePrompt: positivePrompt || 'Not found',
            negativePrompt: negativePrompt || 'Not found',
            model: model || 'Unknown',
            sampler: sampler || 'Unknown',
            steps: steps.toString() || 'N/A',
            cfg: cfg.toString() || 'N/A',
            seed: seed.toString() || 'N/A',
            size: width && height ? `${width}x${height}` : 'Unknown'
        };

        if (loras.length > 0) {
            metadata.summary.loras = loras.join(', ');
        }

        // Add workflow stats
        metadata.workflow = {
            nodeCount: Object.keys(workflowData).length.toString(),
            nodes: Object.entries(workflowData)
                .map(([id, node]) => `${id}: ${node.class_type}`)
                .slice(0, 10) // Show first 10 nodes
                .join('\n')
        };

        return metadata;
    }

    function transformApiMetadata(apiData) {
        // Transform the Python API response to match the expected format
        // Handle both direct fields and nested comfy_parsed structure
        const comfyParsed = apiData.comfy_parsed || apiData.metadata || apiData;

        const summary = {
            // NOTE: Don't use apiData.prompt/comfyParsed.prompt as fallback - too generic, may contain UI instructions
            positivePrompt: apiData.positive_prompt || comfyParsed.positive_prompt || '',
            negativePrompt: apiData.negative_prompt || comfyParsed.negative_prompt || '',
            model: apiData.model || comfyParsed.model || apiData.checkpoint || comfyParsed.checkpoint || '',
            loras: apiData.loras || comfyParsed.loras || '',
            cfgScale: apiData.cfg_scale || comfyParsed.cfg_scale || apiData.cfgScale || comfyParsed.cfgScale || apiData.cfg || comfyParsed.cfg || '',
            steps: apiData.steps || comfyParsed.steps || apiData.total_steps || comfyParsed.total_steps || '',
            sampler: apiData.sampler || comfyParsed.sampler || apiData.sampler_name || comfyParsed.sampler_name || '',
            seed: apiData.seed || comfyParsed.seed || '',
            clipSkip: apiData.clip_skip || comfyParsed.clip_skip || apiData.clipSkip || comfyParsed.clipSkip || '',
            workflow: apiData.workflow || comfyParsed.workflow || apiData.workflow_name || comfyParsed.workflow_name || ''
        };

        // Clean up empty values
        Object.keys(summary).forEach(key => {
            const value = summary[key];
            if (value === '' || value === null || value === undefined) {
                summary[key] = 'None';
            } else if (typeof value === 'object') {
                // Handle arrays or objects (like loras)
                summary[key] = JSON.stringify(value);
            } else {
                summary[key] = String(value);
            }
        });

        const standard = {};
        const width = apiData.width || apiData.image_width || comfyParsed.width || comfyParsed.image_width;
        const height = apiData.height || apiData.image_height || comfyParsed.height || comfyParsed.image_height;
        const fileSize = apiData.file_size || apiData.size || comfyParsed.file_size;
        const filename = apiData.filename || apiData.file_name || comfyParsed.filename;
        const mimeType = apiData.mime_type || apiData.mimeType || comfyParsed.mime_type;
        const lastModified = apiData.last_modified || apiData.modified || comfyParsed.last_modified;

        if (filename) {
            standard.filename = String(filename);
        }
        if (width && height) {
            standard.dimensions = `${width} × ${height}`;
        }
        if (fileSize) {
            const sizeNum = Number(fileSize);
            if (Number.isFinite(sizeNum)) {
                const units = ['B', 'KB', 'MB', 'GB', 'TB'];
                let size = sizeNum;
                let unitIndex = 0;
                while (size >= 1024 && unitIndex < units.length - 1) {
                    size /= 1024;
                    unitIndex++;
                }
                standard.fileSize = `${size.toFixed(unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
            } else {
                standard.fileSize = String(fileSize);
            }
        }
        if (mimeType) {
            standard.mimeType = String(mimeType);
        }
        if (lastModified) {
            standard.lastModified = String(lastModified);
        }

        Object.keys(standard).forEach(key => {
            if (standard[key] === undefined || standard[key] === null || standard[key] === '') {
                delete standard[key];
            }
        });

        return {
            standard: standard,
            exif: {},
            custom: summary,
            summary: summary,
            raw: apiData
        };
    }

    // Removed client-side PNG parsing functions - now using Python API
    // The following functions have been removed:
    // - parsePNGMetadata: PNG chunk reading
    // - parseAIParameters: A1111 parameter parsing
    // - parseITXtChunk: Compressed PNG text chunks
    // - buildMetadataSummary: Metadata aggregation
    // - ComfyPromptParser: ComfyUI prompt graph parsing
    // All metadata extraction is now handled by the Python API at /api/v1/metadata/extract

    // Removed parseITXtChunk - no longer needed with Python API

    // Removed buildMetadataSummary - handled by Python API
    /*
    function buildMetadataSummary({ textChunks, comfySummary, a1111 }) {
        const summary = {
            positivePrompt: '',
            negativePrompt: '',
            model: '',
            loras: '',
            cfgScale: '',
            steps: '',
            sampler: '',
            seed: '',
            clipSkip: '',
            workflow: ''
        };

        summary.positivePrompt = coalesceText(
            comfySummary?.positivePrompt,
            textChunks.prompt,
            a1111.prompt
        );
        if (!summary.positivePrompt) {
            summary.positivePrompt = 'None';
        }

        summary.negativePrompt = coalesceText(
            comfySummary?.negativePrompt,
            textChunks.negative_prompt,
            textChunks['Negative prompt'],
            a1111.negativePrompt
        );
        if (!summary.negativePrompt) {
            summary.negativePrompt = 'None';
        }

        const model = coalesceText(
            comfySummary?.model,
            textChunks.model,
            textChunks.Model,
            a1111.model
        );
        summary.model = model ? String(model) : 'None';

        const steps = coalesceNumeric(
            comfySummary?.steps,
            a1111.steps,
            textChunks.steps
        );
        summary.steps = steps !== '' ? String(steps) : 'None';

        const cfgScale = coalesceNumeric(
            comfySummary?.cfgScale,
            a1111.cfgScale,
            textChunks.cfgScale,
            textChunks.cfg_scale
        );
        summary.cfgScale = cfgScale !== '' ? String(cfgScale) : 'None';

        const seed = coalesceText(
            comfySummary?.seed,
            a1111.seed,
            textChunks.seed
        );
        summary.seed = seed ? String(seed) : 'None';

        const sampler = coalesceText(
            comfySummary?.sampler,
            textChunks.sampler,
            a1111.sampler
        );
        summary.sampler = sampler ? String(sampler) : 'None';

        const clipSkip = coalesceNumeric(
            comfySummary?.clipSkip,
            a1111.clipSkip,
            textChunks.clipSkip,
            textChunks.clip_skip
        );
        summary.clipSkip = clipSkip !== '' ? String(clipSkip) : 'None';

        const loraSummary = comfySummary?.loras && comfySummary.loras.length
            ? comfySummary.loras.map(formatLoraEntry).filter(Boolean).join(', ')
            : coalesceText(textChunks.loras, textChunks.lora);

        summary.loras = loraSummary ? String(loraSummary) : 'None';

        const workflowSummary = formatWorkflowSummary(comfySummary?.workflow ?? null);
        summary.workflow = workflowSummary || 'Not embedded';

        return summary;
    }
    */

    function coalesceText(...values) {
        for (const value of values) {
            if (value === undefined || value === null) continue;
            if (typeof value === 'string') {
                const trimmed = value.trim();
                if (trimmed) {
                    return trimmed;
                }
            }
            if (typeof value === 'number' && Number.isFinite(value)) {
                return value.toString();
            }
        }
        return '';
    }

    function coalesceNumeric(...values) {
        for (const value of values) {
            const num = toNumber(value);
            if (num !== null) {
                return num;
            }
        }
        return '';
    }

    function toNumber(value) {
        if (value === undefined || value === null) {
            return null;
        }
        if (typeof value === 'number' && Number.isFinite(value)) {
            return value;
        }
        if (typeof value === 'string') {
            const trimmed = value.trim();
            if (!trimmed) {
                return null;
            }
            const parsed = Number(trimmed);
            if (!Number.isNaN(parsed)) {
                return parsed;
            }
        }
        return null;
    }

    function formatLoraEntry(entry) {
        if (!entry || !entry.name) {
            return '';
        }

        const strengths = [];
        if (entry.strengthModel !== undefined && entry.strengthModel !== null) {
            strengths.push(`model ${formatNumeric(entry.strengthModel)}`);
        }
        if (entry.strengthClip !== undefined && entry.strengthClip !== null) {
            strengths.push(`clip ${formatNumeric(entry.strengthClip)}`);
        }

        return strengths.length
            ? `${entry.name} (${strengths.join(', ')})`
            : entry.name;
    }

    function formatNumeric(value) {
        const num = toNumber(value);
        if (num === null) {
            return typeof value === 'string' ? value : '';
        }
        if (Number.isInteger(num)) {
            return num.toString();
        }
        return Number(num.toFixed(3)).toString();
    }

    function formatWorkflowSummary(workflow) {
        if (!workflow || typeof workflow !== 'object') {
            return '';
        }

        const nodeCount = countWorkflowNodes(workflow);
        if (nodeCount > 0) {
            return `Embedded workflow (${nodeCount} nodes)`;
        }
        return 'Embedded workflow';
    }

    function countWorkflowNodes(workflow) {
        if (!workflow || typeof workflow !== 'object') {
            return 0;
        }

        if (Array.isArray(workflow)) {
            return workflow.length;
        }

        if (workflow.nodes) {
            const nodes = workflow.nodes;
            if (Array.isArray(nodes)) {
                return nodes.length;
            }
            if (nodes && typeof nodes === 'object') {
                return Object.keys(nodes).length;
            }
        }

        try {
            return Object.keys(workflow).length;
        } catch (error) {
            return 0;
        }
    }

    const TEXT_NODE_TYPES = new Set([
        'CLIPTextEncode',
        'CLIPTextEncodeSDXL',
        'CLIPTextEncodeSDXLRefiner',
        'CLIPTextEncodeSVD',
        'DualCLIPTextEncode',
        'Note',
        'String',
        'Text',
        'TextBox',
        'TextMultiline',
        'PromptBookmark'
    ]);

    // Removed ComfyPromptParser - handled by Python API
    /*
    class ComfyPromptParser {
        constructor(promptGraph) {
            this.nodes = new Map();

            if (promptGraph && typeof promptGraph === 'object') {
                Object.entries(promptGraph).forEach(([id, node]) => {
                    if (node && typeof node === 'object') {
                        this.nodes.set(String(id), node);
                    }
                });
            }
        }

        summarize(workflowData) {
            if (!this.nodes.size) {
                return null;
            }

            const samplerInfo = this.findSamplerNode();
            if (!samplerInfo) {
                return null;
            }

            const samplerNode = samplerInfo.node;
            const inputs = samplerNode.inputs || {};

            const positiveKey = inputs.positive !== undefined ? 'positive' : (inputs.positive_conditioning !== undefined ? 'positive_conditioning' : null);
            const negativeKey = inputs.negative !== undefined ? 'negative' : (inputs.negative_conditioning !== undefined ? 'negative_conditioning' : null);

            const positivePrompt = positiveKey ? this.extractText(inputs[positiveKey]) : '';
            const negativePrompt = negativeKey ? this.extractText(inputs[negativeKey]) : '';

            const steps = this.extractNumberFromValue(this.resolveFirst(inputs, ['steps', 'num_steps', 'step']));
            const cfgScale = this.extractNumberFromValue(this.resolveFirst(inputs, ['cfg', 'cfg_scale', 'cfgScale']));
            const samplerName = this.extractTextValue(this.resolveFirst(inputs, ['sampler_name', 'sampler', 'sampler_type', 'scheduler']));
            const seed = this.extractSeedValue(inputs);

            const modelInfo = this.collectModelChain(this.resolveFirst(inputs, ['model', 'model_in', 'model_input', 'unet']));

            return {
                positivePrompt: positivePrompt || '',
                negativePrompt: negativePrompt || '',
                steps: steps !== null ? steps : undefined,
                cfgScale: cfgScale !== null ? cfgScale : undefined,
                sampler: samplerName || '',
                seed: seed,
                model: modelInfo.model || '',
                loras: modelInfo.loras,
                clipSkip: modelInfo.clipSkip !== null ? modelInfo.clipSkip : undefined,
                workflow: workflowData || null
            };
        }

        findSamplerNode() {
            const samplerTypes = new Set([
                'KSampler',
                'KSamplerAdvanced',
                'KSamplerSimple',
                'KSamplerSelect',
                'KSamplerSDXL',
                'KSamplerLatentUpscale',
                'Sampler',
                'SamplerCustom'
            ]);

            let fallback = null;

            for (const [id, node] of this.nodes.entries()) {
                if (!node || typeof node !== 'object') continue;
                const type = node.class_type || node.type || '';
                if (!samplerTypes.has(type)) continue;

                const inputs = node.inputs || {};
                if (inputs.positive !== undefined || inputs.positive_conditioning !== undefined) {
                    return { id, node };
                }
                if (!fallback) {
                    fallback = { id, node };
                }
            }

            return fallback;
        }

        resolveFirst(inputs, keys) {
            if (!inputs) return undefined;
            for (const key of keys) {
                if (inputs[key] !== undefined) {
                    return inputs[key];
                }
            }
            return undefined;
        }

        getNodeId(value) {
            if (value === undefined || value === null) {
                return null;
            }

            if (Array.isArray(value)) {
                if (!value.length) return null;

                const first = value[0];
                if (Array.isArray(first)) {
                    for (const entry of value) {
                        const resolved = this.getNodeId(entry);
                        if (resolved) return resolved;
                    }
                    return null;
                }

                if (typeof first === 'object' && first !== null) {
                    return this.getNodeId(first);
                }

                const candidate = String(first);
                return this.nodes.has(candidate) ? candidate : null;
            }

            if (typeof value === 'object') {
                if (value.node !== undefined) {
                    return this.getNodeId([value.node]);
                }
                if (value.id !== undefined) {
                    return this.getNodeId([value.id]);
                }
            }

            if (typeof value === 'string' || typeof value === 'number') {
                const candidate = String(value);
                return this.nodes.has(candidate) ? candidate : null;
            }

            return null;
        }

        getNode(id) {
            return this.nodes.get(String(id));
        }

        extractText(value) {
            return this.collectTextFromValue(value, new Set()).trim();
        }

        collectTextFromValue(value, visited) {
            if (value === undefined || value === null) {
                return '';
            }
            if (typeof value === 'string') {
                return value;
            }
            if (typeof value === 'number') {
                return value.toString();
            }
            if (Array.isArray(value)) {
                const nodeId = this.getNodeId(value);
                if (nodeId) {
                    const text = this.collectTextFromNode(nodeId, visited);
                    // If node reference failed to resolve, show error instead of falling through
                    if (!text && !this.getNode(nodeId)) {
                        return `[Broken reference: node ${nodeId} not found]`;
                    }
                    return text;
                }
                return value.map((item) => this.collectTextFromValue(item, visited)).filter(Boolean).join(' ');
            }
            if (typeof value === 'object') {
                if (value.node !== undefined) {
                    return this.collectTextFromValue([value.node, value.output ?? 0], visited);
                }
                if (value.text !== undefined) {
                    return this.collectTextFromValue(value.text, visited);
                }
                if (value.value !== undefined) {
                    return this.collectTextFromValue(value.value, visited);
                }
            }
            return '';
        }

        collectTextFromNode(nodeId, visited) {
            nodeId = String(nodeId);
            if (visited.has(nodeId)) {
                return '';
            }
            visited.add(nodeId);

            const node = this.getNode(nodeId);
            if (!node) {
                return '';
            }

            const type = node.class_type || node.type || '';
            const inputs = node.inputs || {};

            if (TEXT_NODE_TYPES.has(type)) {
                const textValue = inputs.text ?? inputs.value ?? '';
                const resolved = this.collectTextFromValue(textValue, visited);
                if (resolved) {
                    return resolved;
                }
            }

            const fragments = [];
            for (const [key, val] of Object.entries(inputs)) {
                if (key.toLowerCase().includes('clip')) continue;
                const resolved = this.collectTextFromValue(val, visited);
                if (resolved) {
                    fragments.push(resolved);
                }
            }

            return fragments.join(', ');
        }

        extractNumberFromValue(value) {
            const literal = this.resolveLiteral(value, new Set());
            if (typeof literal === 'number' && Number.isFinite(literal)) {
                return literal;
            }
            if (typeof literal === 'string') {
                const parsed = Number(literal);
                if (!Number.isNaN(parsed)) {
                    return parsed;
                }
            }
            return null;
        }

        extractTextValue(value) {
            const literal = this.resolveLiteral(value, new Set());
            if (literal === null || literal === undefined) {
                return '';
            }
            if (typeof literal === 'string') {
                return literal.trim();
            }
            if (typeof literal === 'number' && Number.isFinite(literal)) {
                return literal.toString();
            }
            return '';
        }

        extractSeedValue(inputs) {
            const seedCandidate = this.resolveFirst(inputs, ['seed', 'noise_seed', 'seed_delta', 'seed_1']);
            if (seedCandidate === undefined) {
                return '';
            }
            const literal = this.resolveLiteral(seedCandidate, new Set());
            if (literal === null || literal === undefined) {
                return '';
            }
            if (typeof literal === 'number' && Number.isFinite(literal)) {
                return literal;
            }
            if (typeof literal === 'string') {
                const trimmed = literal.trim();
                if (trimmed) {
                    return trimmed;
                }
            }
            return '';
        }

        collectModelChain(value) {
            const result = {
                model: '',
                loras: [],
                clipSkip: null
            };

            const visited = new Set();
            const stack = [];
            const startId = this.getNodeId(value);
            if (startId) {
                stack.push(startId);
            }

            while (stack.length) {
                const nodeId = stack.pop();
                if (visited.has(nodeId)) continue;
                visited.add(nodeId);

                const node = this.getNode(nodeId);
                if (!node) continue;

                const inputs = node.inputs || {};
                const type = node.class_type || node.type || '';

                if (!result.model) {
                    const modelName = this.extractTextValue(
                        this.resolveFirst(inputs, ['ckpt_name', 'model_name', 'checkpoint', 'checkpoint_name', 'filename'])
                    );
                    if (modelName) {
                        result.model = modelName;
                    }
                }

                if (result.clipSkip === null) {
                    const clipSkipCandidate = this.extractNumberFromValue(
                        this.resolveFirst(inputs, ['clip_skip', 'clipSkip', 'skip', 'clip_layers'])
                    );
                    if (clipSkipCandidate !== null) {
                        result.clipSkip = clipSkipCandidate;
                    }
                }

                if (/lora/i.test(type)) {
                    const entries = this.extractLoraEntries(node);
                    if (entries.length) {
                        result.loras.push(...entries);
                    }
                }

                const nextKeys = ['model', 'model_in', 'model1', 'model2', 'clip', 'clip_in', 'clip1', 'clip2', 'unet', 'unet_in'];
                nextKeys.forEach((key) => {
                    if (inputs[key] !== undefined) {
                        const nextId = this.getNodeId(inputs[key]);
                        if (nextId && !visited.has(nextId)) {
                            stack.push(nextId);
                        }
                    }
                });
            }

            return result;
        }

        extractLoraEntries(node) {
            const results = [];
            const inputs = node.inputs || {};

            const addEntry = (name, strengthModel, strengthClip) => {
                if (!name) return;
                results.push({
                    name,
                    strengthModel: strengthModel !== undefined ? strengthModel : null,
                    strengthClip: strengthClip !== undefined ? strengthClip : null
                });
            };

            const primaryName = this.extractTextValue(
                inputs.lora_name ?? inputs.lora ?? inputs.model ?? inputs.filename ?? inputs.name
            );
            if (primaryName) {
                const modelStrength = this.extractNumberFromValue(
                    inputs.strength_model ?? inputs.model_strength ?? inputs.strength ?? inputs.alpha
                );
                const clipStrength = this.extractNumberFromValue(
                    inputs.strength_clip ?? inputs.clip_strength
                );
                addEntry(primaryName, modelStrength, clipStrength);
            }

            if (Array.isArray(inputs.lora_stack)) {
                inputs.lora_stack.forEach((entry) => {
                    if (Array.isArray(entry)) {
                        const [name, modelStrength, clipStrength] = entry;
                        addEntry(
                            this.extractTextValue(name),
                            this.extractNumberFromValue(modelStrength),
                            this.extractNumberFromValue(clipStrength)
                        );
                    } else if (entry && typeof entry === 'object') {
                        addEntry(
                            this.extractTextValue(entry.name ?? entry.lora_name),
                            this.extractNumberFromValue(entry.strength_model ?? entry.model_strength),
                            this.extractNumberFromValue(entry.strength_clip ?? entry.clip_strength)
                        );
                    }
                });
            }

            return results;
        }

        resolveLiteral(value, visited) {
            if (value === undefined || value === null) {
                return null;
            }

            if (typeof value === 'number' || typeof value === 'boolean') {
                return value;
            }

            if (typeof value === 'string') {
                if (this.nodes.has(value)) {
                    return this.resolveLiteral([value], visited);
                }
                return value;
            }

            if (Array.isArray(value)) {
                const nodeId = this.getNodeId(value);
                if (nodeId) {
                    if (visited.has(nodeId)) {
                        return null;
                    }
                    visited.add(nodeId);

                    const node = this.getNode(nodeId);
                    if (!node) {
                        return null;
                    }

                    const inputs = node.inputs || {};
                    const type = node.class_type || node.type || '';

                    if (/clip.*skip/i.test(type)) {
                        const clipSkipValue = this.resolveLiteral(inputs.clip_skip ?? inputs.clipSkip, visited);
                        if (clipSkipValue !== null && clipSkipValue !== undefined) {
                            return clipSkipValue;
                        }
                    }

                    const constantKeys = ['value', 'number', 'float', 'int', 'seed', 'strength', 'strength_model', 'strength_clip'];
                    for (const key of constantKeys) {
                        if (inputs[key] !== undefined) {
                            const resolved = this.resolveLiteral(inputs[key], visited);
                            if (resolved !== null && resolved !== undefined) {
                                return resolved;
                            }
                        }
                    }

                    if (node.value !== undefined) return node.value;
                    if (node.number !== undefined) return node.number;
                    if (node.seed !== undefined) return node.seed;

                    return null;
                }

                const literals = value
                    .map((item) => this.resolveLiteral(item, visited))
                    .filter((item) => item !== null && item !== undefined);

                if (!literals.length) {
                    return null;
                }

                return literals.length === 1 ? literals[0] : literals;
            }

            if (typeof value === 'object') {
                if (value.node !== undefined) {
                    return this.resolveLiteral([value.node, value.output ?? 0], visited);
                }
                if (value.value !== undefined) {
                    return this.resolveLiteral(value.value, visited);
                }
                if (value.number !== undefined) {
                    return this.resolveLiteral(value.number, visited);
                }
                if (value.float !== undefined) {
                    return this.resolveLiteral(value.float, visited);
                }
                if (value.int !== undefined) {
                    return this.resolveLiteral(value.int, visited);
                }
                if (value.string !== undefined) {
                    return this.resolveLiteral(value.string, visited);
                }
            }

            return null;
        }
    }
    */

    function augmentWithComfySummary(metadata) {
        // This function now just passes through since Python API provides complete metadata
        if (!metadata) {
            return;
        }

        // The metadata from Python API already has everything we need
        // Just ensure the structure is consistent
        if (metadata.custom && metadata.custom.summary) {
            metadata.comfy = metadata.custom.summary;
        }
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
        console.debug('[MetadataManager] Creating panel with internal config:', config);
        const panelConfig = Object.assign({}, defaultConfig.panel, config);
        const panelId = generatePanelId();

        const panel = document.createElement('div');
        panel.className = `metadata-panel metadata-panel-${panelConfig.position}`;
        console.debug('[MetadataManager] Panel element created:', panel);
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
        if (!panelInstance) {
            console.error('[MetadataManager] displayMetadata: Panel not found:', panelId);
            return;
        }

        const sections = panelInstance.element.querySelector('.metadata-sections');
        if (!sections) {
            console.error('[MetadataManager] No .metadata-sections element found in panel');
            return;
        }

        sections.innerHTML = '';

        // Debug logging to understand metadata structure
        console.debug('[MetadataManager] displayMetadata called with:', {
            panelId,
            metadata,
            hasCustom: metadata?.custom !== undefined,
            hasSummary: metadata?.summary !== undefined,
            hasStandard: metadata?.standard !== undefined,
            keys: metadata ? Object.keys(metadata) : []
        });

        // Check if metadata is empty or null
        if (!metadata || (typeof metadata === 'object' && Object.keys(metadata).length === 0)) {
            sections.innerHTML = '<div class="metadata-empty">No metadata available for this image</div>';
            return;
        }

        // Transform raw workflow metadata if needed
        if (metadata && typeof metadata === 'object' && !metadata.summary && !metadata.standard && !metadata.custom) {

            // Check if this looks like raw ComfyUI workflow nodes (numeric keys with class_type)
            const isWorkflowData = Object.keys(metadata).some(key => {
                const node = metadata[key];
                return node && typeof node === 'object' && node.class_type;
            });

            if (isWorkflowData) {
                metadata = transformWorkflowMetadata(metadata);
            } else {
                // Check if this is API response with nested structure
                if (metadata.comfy_parsed || metadata.positive_prompt || metadata.negative_prompt ||
                    metadata.model || metadata.sampler || metadata.steps || metadata.cfg_scale || metadata.seed) {
                    // Transform API response to expected format
                    metadata = transformApiMetadata(metadata);
                } else {
                    // Wrap the raw data as custom metadata
                    metadata = {
                        custom: metadata,
                        standard: {},
                        summary: metadata
                    };
                }
            }
        }

        // Store current metadata
        panelInstance.currentMetadata = metadata;

        // Create enhanced metadata display matching the reference design
        let displayHtml = '';

        // Extract data from metadata - check all possible locations
        const data = metadata.summary || metadata.custom || metadata;

        // Validation helper - rejects invalid/placeholder prompts
        const isValidPrompt = (value) => {
            if (!value || value === '' || value === 'None' || value === 'Not found') return false;
            // Reject "Generate a prompt from..." which is UI placeholder text
            if (String(value).startsWith('Generate a prompt')) return false;
            return true;
        };

        // Also check for positivePrompt/negativePrompt (camelCase) in addition to snake_case
        // NOTE: Don't use data.prompt as fallback - it's too generic and may contain instructions/metadata
        // Try camelCase first, then snake_case, but validate each before using
        let positivePrompt = '';
        if (isValidPrompt(data.positivePrompt)) {
            positivePrompt = data.positivePrompt;
        } else if (isValidPrompt(data.positive_prompt)) {
            positivePrompt = data.positive_prompt;
        } else if (isValidPrompt(data.positive)) {
            positivePrompt = data.positive;
        }

        let negativePrompt = '';
        if (isValidPrompt(data.negativePrompt)) {
            negativePrompt = data.negativePrompt;
        } else if (isValidPrompt(data.negative_prompt)) {
            negativePrompt = data.negative_prompt;
        } else if (isValidPrompt(data.negative)) {
            negativePrompt = data.negative;
        }
        const model = data.model || data.checkpoint || data.model_name || '';
        const loras = data.loras || data.lora || '';
        const cfgScale = data.cfgScale || data.cfg_scale || data.cfg || '';
        const steps = data.steps || data.total_steps || '';
        const sampler = data.sampler || data.sampler_name || '';
        const seed = data.seed || '';
        const clipSkip = data.clipSkip || data.clip_skip || '';

        // Display character tags if available
        if (data.tags || data.character || data.style_tags) {
            displayHtml += '<div class="metadata-tags">';
            const tags = (data.tags || data.character || data.style_tags || '').split(',').map(t => t.trim()).filter(t => t);
            tags.forEach(tag => {
                displayHtml += `<span class="metadata-tag">${escapeHtml(tag)}</span>`;
            });
            displayHtml += '</div>';
        }

        // Display checkpoint first if available
        if (model) {
            // Clean up model name - remove path prefix, keep only filename
            let cleanModelName = model;
            // Remove any path components (handle both / and \)
            cleanModelName = cleanModelName.replace(/^.*[\/\\]/, '');
            // Also remove common prefixes like "checkpoints/" if present
            cleanModelName = cleanModelName.replace(/^(checkpoints?|models?|ckpt)[\/\\]/i, '');

            displayHtml += `
                <div class="metadata-checkpoint-section">
                    <div class="metadata-model-label">CHECKPOINT</div>
                    <div class="metadata-model-name" title="${escapeHtml(model)}">${escapeHtml(cleanModelName)}</div>
                </div>
            `;
        }

        // Display positive prompt with copy functionality
        if (positivePrompt && positivePrompt !== 'None' && positivePrompt !== 'Not found') {
            const promptId = `prompt-${panelId}-${Date.now()}`;
            const isLongPrompt = positivePrompt.length > 150; // Threshold for collapsing

            displayHtml += `
                <div class="metadata-prompt-section compact">
                    <div class="metadata-prompt-label">PROMPT</div>
                    <div class="metadata-prompt-content ${isLongPrompt ? 'collapsible' : ''}" data-prompt-id="${promptId}">
                        <div class="prompt-text ${isLongPrompt ? 'collapsed' : ''}">${escapeHtml(positivePrompt)}</div>
                        ${isLongPrompt ? `
                            <button class="metadata-expand-btn" onclick="
                                const container = document.querySelector('[data-prompt-id=\\'${promptId}\\']');
                                const text = container.querySelector('.prompt-text');
                                const btn = container.querySelector('.metadata-expand-btn');
                                if (text.classList.contains('collapsed')) {
                                    text.classList.remove('collapsed');
                                    btn.innerHTML = '<i class=\\'fa-solid fa-chevron-up\\'></i> Show less';
                                } else {
                                    text.classList.add('collapsed');
                                    btn.innerHTML = '<i class=\\'fa-solid fa-chevron-down\\'></i> Show more';
                                }
                            ">
                                <i class="fa-solid fa-chevron-down"></i> Show more
                            </button>
                        ` : ''}
                        <button class="metadata-copy-btn" data-value="${escapeHtml(positivePrompt)}" title="Copy prompt">
                            <i class="fa-solid fa-copy"></i>
                        </button>
                    </div>
                </div>
            `;
        }

        // Display negative prompt
        if (negativePrompt && negativePrompt !== 'None' && negativePrompt !== 'Not found') {
            const negPromptId = `neg-prompt-${panelId}-${Date.now() + 1}`;
            const isLongNegPrompt = negativePrompt.length > 150;

            displayHtml += `
                <div class="metadata-prompt-section compact">
                    <div class="metadata-prompt-label">NEGATIVE PROMPT</div>
                    <div class="metadata-prompt-content ${isLongNegPrompt ? 'collapsible' : ''}" data-prompt-id="${negPromptId}">
                        <div class="prompt-text ${isLongNegPrompt ? 'collapsed' : ''}">${escapeHtml(negativePrompt)}</div>
                        ${isLongNegPrompt ? `
                            <button class="metadata-expand-btn" onclick="
                                const container = document.querySelector('[data-prompt-id=\\'${negPromptId}\\']');
                                const text = container.querySelector('.prompt-text');
                                const btn = container.querySelector('.metadata-expand-btn');
                                if (text.classList.contains('collapsed')) {
                                    text.classList.remove('collapsed');
                                    btn.innerHTML = '<i class=\\'fa-solid fa-chevron-up\\'></i> Show less';
                                } else {
                                    text.classList.add('collapsed');
                                    btn.innerHTML = '<i class=\\'fa-solid fa-chevron-down\\'></i> Show more';
                                }
                            ">
                                <i class="fa-solid fa-chevron-down"></i> Show more
                            </button>
                        ` : ''}
                        <button class="metadata-copy-btn" data-value="${escapeHtml(negativePrompt)}" title="Copy negative prompt">
                            <i class="fa-solid fa-copy"></i>
                        </button>
                    </div>
                </div>
            `;
        }

        // Display technical parameters in a grid
        const params = [];
        if (sampler && sampler !== 'None') params.push({ label: 'Sampler', value: sampler });
        if (steps && steps !== 'None') params.push({ label: 'Steps', value: steps });
        if (cfgScale && cfgScale !== 'None') params.push({ label: 'CFG Scale', value: cfgScale });
        if (seed && seed !== 'None') params.push({ label: 'Seed', value: seed });
        if (clipSkip && clipSkip !== 'None') params.push({ label: 'Clip Skip', value: clipSkip });
        if (loras && loras !== 'None') params.push({ label: 'LoRAs', value: loras });
        if (data.width && data.height) params.push({ label: 'Size', value: `${data.width}x${data.height}` });
        else if (data.size) params.push({ label: 'Size', value: data.size });

        if (params.length > 0) {
            displayHtml += '<div class="metadata-params">';
            params.forEach(param => {
                displayHtml += `
                    <div class="metadata-param">
                        <div class="metadata-param-label">${escapeHtml(param.label)}</div>
                        <div class="metadata-param-value">${escapeHtml(String(param.value))}</div>
                    </div>
                `;
            });
            displayHtml += '</div>';
        }

        // Apply the HTML
        sections.innerHTML = displayHtml || '<div class="metadata-empty">No displayable metadata found</div>';

        // Add copy button handlers with improved visual feedback
        sections.querySelectorAll('.metadata-copy-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const value = btn.getAttribute('data-value');
                copyToClipboard(value);

                // Visual feedback
                btn.classList.add('copied');
                const originalHtml = btn.innerHTML;
                btn.innerHTML = '<i class="fa-solid fa-check"></i> Copied!';

                setTimeout(() => {
                    btn.classList.remove('copied');
                    btn.innerHTML = originalHtml;
                }, 2000);
            });
        });
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
        if (!panelInstance) {
            console.warn('[MetadataManager] Cannot show panel - not found:', panelId);
            return;
        }

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
                    const cachedData = cached.access();
                    
                    // Validate cached data before returning it
                    const invalidValues = ['None', 'Not found', ''];
                    const isValidPrompt = (value) => {
                        if (!value) return false;
                        if (invalidValues.includes(value)) return false;
                        // Reject "Generate a prompt from..." which is UI placeholder text
                        if (value.startsWith('Generate a prompt')) return false;
                        return true;
                    };

                    const hasValidPrompts = cachedData && (
                        isValidPrompt(cachedData.positive_prompt) ||
                        isValidPrompt(cachedData.negative_prompt) ||
                        isValidPrompt(cachedData.comfy_parsed?.positive_prompt) ||
                        isValidPrompt(cachedData.comfy_parsed?.negative_prompt) ||
                        isValidPrompt(cachedData.summary?.positivePrompt) ||
                        isValidPrompt(cachedData.summary?.positive_prompt) ||
                        isValidPrompt(cachedData.custom?.positivePrompt) ||
                        isValidPrompt(cachedData.custom?.positive_prompt)
                    );

                    // Only return cached data if it has valid prompts
                    if (hasValidPrompts) {
                        console.debug('[MetadataManager] Returning valid cached metadata for:', source);
                        return cachedData;
                    } else {
                        console.debug('[MetadataManager] Cached metadata invalid - re-extracting for:', source);
                        // Clear invalid cache entry
                        metadataCache.delete(cacheKey);
                    }
                }
            }

            if (typeof source === 'string') {
                const idFromUrl = extractImageIdFromSource(source);
                if (idFromUrl !== null) {
                    const metadataById = await this.getByImageId(idFromUrl, useCache);
                    if (metadataById) {
                        if (defaultConfig.enableCache) {
                            metadataCache.set(cacheKey, new CacheEntry(metadataById));
                        }
                        return metadataById;
                    }
                }
            }

            // Extract metadata
            const metadata = await extractFromImage(source);
            augmentWithComfySummary(metadata);

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
            const panelId = createMetadataPanel(config);
            return panelId;
        },

        /**
         * Attach panel to container
         * @param {string} panelId - Panel ID
         * @param {HTMLElement|string} container - Container element or selector
         */
        attachPanel: function(panelId, container) {
            const panelInstance = panels.get(panelId);
            if (!panelInstance) {
                console.error('[MetadataManager] Cannot attach panel - not found:', panelId);
                return false;
            }

            if (typeof container === 'string') {
                container = document.querySelector(container);
            }

            if (!container) {
                console.error('[MetadataManager] Cannot attach panel - container not found');
                return false;
            }
            container.appendChild(panelInstance.element);

            // Verify it was actually added
            if (document.body.contains(panelInstance.element)) {
            } else {
                console.error('[MetadataManager] Panel NOT in DOM after attachment!');
            }

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
         * Retrieve metadata by generated image ID.
         * @param {number|string} imageId - Generated image identifier
         * @param {boolean} useCache - Whether to use cache
         * @returns {Promise<Object|null>} Metadata object or null
         */
        getByImageId: async function(imageId, useCache = true) {
            if (imageId === null || imageId === undefined || imageId === '') {
                return null;
            }

            const cacheKey = getCacheKey(Number(imageId));

            if (useCache && defaultConfig.enableCache) {
                const cached = metadataCache.get(cacheKey);
                if (cached && !cached.isExpired(defaultConfig.cacheTimeout)) {
                    return cached.access();
                }
            }

            const metadata = await fetchMetadataByImageId(imageId);

            // Check if metadata has actual parsed prompt text (not just "None" placeholders or error messages)
            const invalidValues = ['None', 'Not found', ''];
            const isValidPrompt = (value) => {
                if (!value) return false;
                if (invalidValues.includes(value)) return false;
                // Reject "Generate a prompt from..." which is UI placeholder text
                if (value.startsWith('Generate a prompt')) return false;
                return true;
            };

            const hasValidPrompts = metadata && (
                isValidPrompt(metadata.positive_prompt) ||
                isValidPrompt(metadata.negative_prompt) ||
                isValidPrompt(metadata.comfy_parsed?.positive_prompt) ||
                isValidPrompt(metadata.comfy_parsed?.negative_prompt) ||
                isValidPrompt(metadata.summary?.positivePrompt) ||
                isValidPrompt(metadata.custom?.positivePrompt)
            );

            // If we don't have valid parsed prompts, return null so caller extracts from file
            if (!hasValidPrompts) {
                console.debug('[MetadataManager] Database metadata has no valid parsed prompts for image', imageId, '- caller should extract from file');
                return null;
            }

            if (metadata && defaultConfig.enableCache) {
                metadataCache.set(cacheKey, new CacheEntry(metadata));
            }

            return metadata;
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
         * Check if a panel has metadata loaded
         * @param {string} panelId - Panel ID
         * @returns {boolean} True if panel has metadata
         */
        hasMetadata: function(panelId) {
            const panelInstance = panels.get(panelId);
            if (!panelInstance) return false;
            
            // Check if we have metadata and it's not just an error
            return panelInstance.currentMetadata && 
                   Object.keys(panelInstance.currentMetadata).length > 0 &&
                   !panelInstance.currentMetadata.error;
        },

        /**
         * Get current metadata from panel
         * @param {string} panelId - Panel ID  
         * @returns {Object|null} Current metadata or null
         */
        getPanelMetadata: function(panelId) {
            const panelInstance = panels.get(panelId);
            return panelInstance ? panelInstance.currentMetadata : null;
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
