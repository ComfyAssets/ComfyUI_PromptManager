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
                if (customData.summary && typeof customData.summary === 'object') {
                    metadata.custom = customData.summary;
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
        const dataView = new DataView(arrayBuffer);

        if (
            dataView.byteLength < 8 ||
            dataView.getUint32(0) !== 0x89504E47 ||
            dataView.getUint32(4) !== 0x0D0A1A0A
        ) {
            return null;
        }

        const latin1Decoder = new TextDecoder('latin1');
        const utf8Decoder = new TextDecoder('utf-8');
        const textChunks = {};
        let offset = 8;

        while (offset + 8 <= dataView.byteLength) {
            const length = dataView.getUint32(offset);
            const type = String.fromCharCode(
                dataView.getUint8(offset + 4),
                dataView.getUint8(offset + 5),
                dataView.getUint8(offset + 6),
                dataView.getUint8(offset + 7)
            );

            const nextOffset = offset + 12 + length;
            if (nextOffset > dataView.byteLength) {
                break;
            }

            if (type === 'tEXt') {
                const chunk = new Uint8Array(arrayBuffer, offset + 8, length);
                const separator = chunk.indexOf(0);
                if (separator !== -1) {
                    const key = latin1Decoder.decode(chunk.slice(0, separator));
                    const value = latin1Decoder.decode(chunk.slice(separator + 1));
                    if (key) {
                        textChunks[key] = value;
                    }
                }
            } else if (type === 'iTXt') {
                const chunk = new Uint8Array(arrayBuffer, offset + 8, length);
                const parsed = parseITXtChunk(chunk, latin1Decoder, utf8Decoder);
                if (parsed && parsed.key) {
                    textChunks[parsed.key] = parsed.value;
                }
            }

            if (type === 'IEND') {
                break;
            }

            offset = nextOffset;
        }

        const raw = { textChunks };

        const promptData = typeof textChunks.prompt === 'string' ? safeJsonParse(textChunks.prompt) : null;
        const workflowData = typeof textChunks.workflow === 'string' ? safeJsonParse(textChunks.workflow) : null;

        if (promptData) {
            raw.prompt = promptData;
        }
        if (workflowData) {
            raw.workflow = workflowData;
        }

        const comfySummary = promptData ? new ComfyPromptParser(promptData).summarize(workflowData) : null;
        if (comfySummary) {
            raw.comfy = comfySummary;
        }

        const a1111 = textChunks.parameters ? parseAIParameters(textChunks.parameters) : {};
        if (Object.keys(a1111).length) {
            raw.a1111 = a1111;
        }

        const summary = buildMetadataSummary({
            textChunks,
            comfySummary,
            a1111
        });

        return { summary, raw };
    }

    function parseAIParameters(text) {
        if (!text || typeof text !== 'string') {
            return {};
        }

        const params = {};
        const normalized = text.replace(/\r\n/g, '\n');

        const explicitPromptMatch = normalized.match(/(?:^|\n)prompt:\s*([^\n]+)/i);
        if (explicitPromptMatch) {
            params.prompt = explicitPromptMatch[1].trim();
        } else {
            const firstLine = normalized.split('\n')[0];
            if (firstLine && !/^negative\s*prompt:/i.test(firstLine)) {
                params.prompt = firstLine.trim();
            }
        }

        const negativeMatch = normalized.match(/negative\s*prompt:\s*([\s\S]*?)(?:\n[A-Za-z][^:\n]*?:|\n*$)/i);
        if (negativeMatch) {
            params.negativePrompt = negativeMatch[1].trim();
        }

        const pairPattern = /([A-Za-z0-9 _-]+):\s*([^,\n]+)(?:,|\n|$)/g;
        let match;

        while ((match = pairPattern.exec(normalized)) !== null) {
            const key = match[1].trim().toLowerCase().replace(/\s+/g, '');
            const rawValue = match[2].trim();

            if (!rawValue) continue;

            switch (key) {
                case 'steps': {
                    const num = Number(rawValue);
                    params.steps = Number.isNaN(num) ? rawValue : num;
                    break;
                }
                case 'cfgscale':
                case 'cfg': {
                    const num = Number(rawValue);
                    params.cfgScale = Number.isNaN(num) ? rawValue : num;
                    break;
                }
                case 'sampler':
                case 'samplername':
                case 'schedule':
                    params.sampler = rawValue;
                    break;
                case 'seed': {
                    if (/^\d+$/.test(rawValue)) {
                        const num = Number(rawValue);
                        params.seed = Number.isSafeInteger(num) ? num : rawValue;
                    } else {
                        params.seed = rawValue;
                    }
                    break;
                }
                case 'clipskip':
                case 'clip_skip': {
                    const num = Number(rawValue);
                    params.clipSkip = Number.isNaN(num) ? rawValue : num;
                    break;
                }
                case 'model':
                case 'modelhash':
                case 'modelname':
                    if (!params.model) {
                        params.model = rawValue;
                    }
                    break;
                default:
                    break;
            }
        }

        return params;
    }

    function safeJsonParse(value) {
        try {
            return JSON.parse(value);
        } catch (error) {
            return null;
        }
    }

    function parseITXtChunk(chunk, latin1Decoder, utf8Decoder) {
        try {
            let offset = 0;

            const readUntilNull = (decoder) => {
                const end = chunk.indexOf(0, offset);
                const sliceEnd = end === -1 ? chunk.length : end;
                const value = decoder.decode(chunk.slice(offset, sliceEnd));
                offset = sliceEnd + 1;
                return value;
            };

            const key = readUntilNull(latin1Decoder);
            if (!key) {
                return null;
            }

            const compressionFlag = chunk[offset++];
            offset++;

            readUntilNull(latin1Decoder);
            readUntilNull(utf8Decoder);

            let textBytes = chunk.slice(offset);

            if (compressionFlag === 1) {
                if (typeof pako !== 'undefined') {
                    try {
                        textBytes = pako.inflate(textBytes);
                    } catch (error) {
                        console.warn('Failed to inflate iTXt chunk:', error);
                        return null;
                    }
                } else {
                    console.warn('Compressed iTXt chunk encountered but no inflater is available.');
                    return null;
                }
            }

            return {
                key,
                value: utf8Decoder.decode(textBytes)
            };
        } catch (error) {
            console.warn('Failed to parse iTXt chunk:', error);
            return null;
        }
    }

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
                    return this.collectTextFromNode(nodeId, visited);
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

    function augmentWithComfySummary(metadata) {
        if (!metadata) {
            return;
        }

        const comfySummary = metadata.raw?.comfy || {};
        const summary = metadata.custom || {};

        const comfy = {
            positivePrompt: summary.positivePrompt || comfySummary.positivePrompt || '',
            negativePrompt: summary.negativePrompt || comfySummary.negativePrompt || '',
            model: summary.model || comfySummary.model || '',
            loras: Array.isArray(comfySummary.loras) ? comfySummary.loras : [],
            cfgScale: summary.cfgScale !== undefined && summary.cfgScale !== '' ? summary.cfgScale : comfySummary.cfgScale ?? null,
            steps: summary.steps !== undefined && summary.steps !== '' ? summary.steps : comfySummary.steps ?? null,
            sampler: summary.sampler || comfySummary.sampler || '',
            seed: summary.seed || comfySummary.seed || '',
            clipSkip: summary.clipSkip !== undefined && summary.clipSkip !== '' ? summary.clipSkip : comfySummary.clipSkip ?? null,
            workflow: metadata.raw?.workflow ?? comfySummary.workflow ?? null,
            workflowSummary: summary.workflow || formatWorkflowSummary(comfySummary.workflow),
            textChunks: metadata.raw?.textChunks || {}
        };

        metadata.comfy = comfy;
        metadata.raw = Object.assign({}, metadata.raw, { comfy });
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
