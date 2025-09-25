/**
 * Word Cloud Module
 * Advanced word cloud generation from prompts with auto-refresh
 */

const WordCloudManager = (function() {
    'use strict';

    function createStub() {
        const target = {};
        return new Proxy(target, {
            get: (obj, prop) => {
                if (prop in obj) {
                    return obj[prop];
                }
                return () => {};
            },
            set: (obj, prop, value) => {
                obj[prop] = value;
                return true;
            },
        });
    }

    if (typeof window === 'undefined') {
        return createStub();
    }
    const pmActive = (window.location?.pathname || '').includes('/prompt_manager');
    if (!pmActive) {
        console.info('[PromptManager] wordcloud skipped outside PromptManager UI context');
        return createStub();
    }

    // Use the v1 API endpoints
    const API_BASE = '/api/v1';

    // Configuration
    const config = {
        minFontSize: 12,
        maxFontSize: 48,
        maxWords: 100,
        refreshInterval: 30000, // 30 seconds
        animationDuration: 500,
        colors: [
            '#4A90E2', '#50C878', '#FFB84D', '#FF6B9D', '#66D9EF',
            '#A6E22E', '#FD971F', '#AE81FF', '#F92672', '#E6DB74'
        ],
        stopWords: new Set([
            // Articles
            'a', 'an', 'the',
            // Pronouns
            'i', 'you', 'he', 'she', 'it', 'we', 'they', 'me', 'him', 'her', 'us', 'them',
            'my', 'your', 'his', 'her', 'its', 'our', 'their',
            'this', 'that', 'these', 'those',
            // Conjunctions
            'and', 'or', 'but', 'nor', 'for', 'yet', 'so',
            // Prepositions
            'in', 'on', 'at', 'to', 'from', 'by', 'with', 'without', 'about',
            'before', 'after', 'during', 'between', 'among', 'through',
            'over', 'under', 'above', 'below', 'up', 'down', 'out', 'off',
            // Common verbs
            'is', 'am', 'are', 'was', 'were', 'be', 'been', 'being',
            'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
            'could', 'should', 'may', 'might', 'must', 'shall', 'can',
            // Common ComfyUI/SD terms to filter
            'of', 'as', 'like', 'than', 'very', 'just', 'only',
            // Numbers and single letters
            '1', '2', '3', '4', '5', '6', '7', '8', '9', '0'
        ])
    };

    // State
    let refreshTimer = null;
    let currentWords = [];
    let selectedWord = null;
    let wordData = new Map();
    let actionsPanel = null;
    let actionsWord = null;
    let actionsDismissBound = false;

    // Private methods
    function parsePrompts(prompts) {
        const wordFrequency = new Map();
        const wordRecency = new Map();
        const wordAssociations = new Map();

        prompts.forEach((prompt, index) => {
            if (!prompt.positive_prompt) return;

            // Extract and clean words
            const words = extractWords(prompt.positive_prompt);
            const recencyScore = (prompts.length - index) / prompts.length; // More recent = higher score

            // Process each word
            words.forEach(word => {
                // Update frequency
                wordFrequency.set(word, (wordFrequency.get(word) || 0) + 1);

                // Update recency (keep max recency score)
                const currentRecency = wordRecency.get(word) || 0;
                wordRecency.set(word, Math.max(currentRecency, recencyScore));

                // Track word associations
                if (!wordAssociations.has(word)) {
                    wordAssociations.set(word, new Set());
                }

                // Add other words from same prompt as associations
                words.forEach(otherWord => {
                    if (otherWord !== word) {
                        wordAssociations.get(word).add(otherWord);
                    }
                });
            });
        });

        // Calculate combined scores
        const scoredWords = [];
        wordFrequency.forEach((freq, word) => {
            const recency = wordRecency.get(word) || 0;
            const associations = wordAssociations.get(word) || new Set();

            // Combined score: frequency * (1 + recency bonus)
            const score = freq * (1 + recency * 0.5);

            scoredWords.push({
                text: word,
                size: freq,
                score: score,
                frequency: freq,
                recency: recency,
                associations: Array.from(associations).slice(0, 10) // Top 10 associations
            });
        });

        // Sort by score and take top N words
        scoredWords.sort((a, b) => b.score - a.score);
        return scoredWords.slice(0, config.maxWords);
    }

    function extractWords(text) {
        // Extract meaningful words from prompt text
        const words = text
            .toLowerCase()
            .replace(/[^\w\s]/g, ' ') // Remove punctuation
            .split(/\s+/) // Split on whitespace
            .filter(word => {
                // Filter criteria
                return word.length > 2 && // Minimum length
                       word.length < 20 && // Maximum length
                       !config.stopWords.has(word) && // Not a stop word
                       !/^\d+$/.test(word); // Not pure numbers
            });

        // Also extract important phrases (2-word combinations)
        const phrases = [];
        for (let i = 0; i < words.length - 1; i++) {
            const phrase = words[i] + ' ' + words[i + 1];
            if (isImportantPhrase(phrase)) {
                phrases.push(phrase);
            }
        }

        return [...words, ...phrases];
    }

    function isImportantPhrase(phrase) {
        // Identify important multi-word concepts
        const importantPatterns = [
            /concept art/i,
            /digital art/i,
            /oil painting/i,
            /fantasy art/i,
            /sci fi/i,
            /high quality/i,
            /ultra detailed/i,
            /cinematic lighting/i,
            /golden hour/i,
            /depth of field/i,
            /rule of thirds/i,
            /wide angle/i,
            /close up/i,
            /full body/i,
            /half body/i,
            /character design/i,
            /environment design/i
        ];

        return importantPatterns.some(pattern => pattern.test(phrase));
    }

    function calculateLayout(words, container) {
        const width = container.clientWidth || 800;
        const height = container.clientHeight || 600;

        // Normalize sizes
        const maxSize = Math.max(...words.map(w => w.size));
        const minSize = Math.min(...words.map(w => w.size));
        const sizeRange = maxSize - minSize || 1;

        // Spiral layout algorithm
        const positions = [];
        const angleStep = 0.1;
        const radiusStep = 2;
        let angle = 0;
        let radius = 0;

        words.forEach((word, index) => {
            // Calculate font size
            const normalizedSize = (word.size - minSize) / sizeRange;
            const fontSize = config.minFontSize +
                           (config.maxFontSize - config.minFontSize) * normalizedSize;

            // Calculate position using spiral
            let placed = false;
            let attempts = 0;
            let x, y;

            while (!placed && attempts < 1000) {
                x = width / 2 + radius * Math.cos(angle);
                y = height / 2 + radius * Math.sin(angle);

                // Check for collisions
                const wordWidth = word.text.length * fontSize * 0.6;
                const wordHeight = fontSize * 1.2;

                const collision = positions.some(pos => {
                    return Math.abs(x - pos.x) < (wordWidth + pos.width) / 2 &&
                           Math.abs(y - pos.y) < (wordHeight + pos.height) / 2;
                });

                if (!collision) {
                    placed = true;
                    positions.push({
                        x, y,
                        width: wordWidth,
                        height: wordHeight
                    });
                }

                angle += angleStep;
                radius += radiusStep * 0.01;
                attempts++;
            }

            // Store layout data
            word.x = x || width / 2 + Math.random() * 200 - 100;
            word.y = y || height / 2 + Math.random() * 200 - 100;
            word.fontSize = fontSize;
            word.color = config.colors[index % config.colors.length];
            word.opacity = 0.7 + normalizedSize * 0.3;
        });

        return words;
    }

    function renderWordCloud(container, words) {
        // Clear container
        container.innerHTML = '';
        container.style.position = 'relative';
        container.style.overflow = 'hidden';

        // Calculate layout
        const layoutWords = calculateLayout(words, container);

        // Create word elements
        layoutWords.forEach((word, index) => {
            const element = document.createElement('div');
            element.className = 'word-cloud-item';
            element.textContent = word.text;

            // Style
            element.style.cssText = `
                position: absolute;
                left: ${word.x}px;
                top: ${word.y}px;
                font-size: ${word.fontSize}px;
                color: ${word.color};
                opacity: 0;
                font-weight: ${word.fontSize > 30 ? 'bold' : 'normal'};
                cursor: pointer;
                transition: all 0.3s ease;
                transform: translate(-50%, -50%);
                text-shadow: 1px 1px 2px rgba(0,0,0,0.5);
                user-select: none;
                z-index: ${Math.floor(word.fontSize)};
            `;

            // Add hover effect
            element.addEventListener('mouseenter', function() {
                this.style.transform = 'translate(-50%, -50%) scale(1.2)';
                this.style.opacity = '1';
                this.style.zIndex = '1000';
                showTooltip(element, word);
            });

            element.addEventListener('mouseleave', function() {
                this.style.transform = 'translate(-50%, -50%) scale(1)';
                this.style.opacity = word.opacity;
                this.style.zIndex = Math.floor(word.fontSize);
                hideTooltip();
            });

            // Add click handler
            element.addEventListener('click', function() {
                selectWord(word);
            });

            container.appendChild(element);

            // Animate in
            setTimeout(() => {
                element.style.opacity = word.opacity;
            }, index * 20);
        });

        // Store current words for reference
        currentWords = layoutWords;
        wordData = new Map(words.map(w => [w.text, w]));
        hideWordActions();
    }

    function showTooltip(element, word) {
        // Remove existing tooltip
        hideTooltip();

        const tooltip = document.createElement('div');
        tooltip.id = 'word-cloud-tooltip';
        tooltip.className = 'word-cloud-tooltip';

        const stats = `
            <strong>${word.text}</strong><br>
            Frequency: ${word.frequency}<br>
            Recency: ${(word.recency * 100).toFixed(0)}%<br>
            ${word.associations.length ? `Related: ${word.associations.slice(0, 5).join(', ')}` : ''}
        `;

        tooltip.innerHTML = stats;
        tooltip.style.cssText = `
            position: absolute;
            background: rgba(0, 0, 0, 0.9);
            color: white;
            padding: 10px;
            border-radius: 5px;
            font-size: 12px;
            pointer-events: none;
            z-index: 10000;
            max-width: 200px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.3);
        `;

        document.body.appendChild(tooltip);

        // Position tooltip
        const rect = element.getBoundingClientRect();
        tooltip.style.left = rect.left + rect.width / 2 - tooltip.offsetWidth / 2 + 'px';
        tooltip.style.top = rect.top - tooltip.offsetHeight - 10 + 'px';

        // Adjust if off screen
        if (tooltip.offsetLeft < 0) {
            tooltip.style.left = '10px';
        }
        if (tooltip.offsetLeft + tooltip.offsetWidth > window.innerWidth) {
            tooltip.style.left = window.innerWidth - tooltip.offsetWidth - 10 + 'px';
        }
        if (tooltip.offsetTop < 0) {
            tooltip.style.top = rect.bottom + 10 + 'px';
        }
    }

    function hideTooltip() {
        const tooltip = document.getElementById('word-cloud-tooltip');
        if (tooltip) {
            tooltip.remove();
        }
    }

    function showWordActions(element, word) {
        hideWordActions();
        actionsWord = word;

        actionsPanel = document.createElement('div');
        actionsPanel.className = 'word-cloud-actions';

        const title = document.createElement('div');
        title.className = 'word-cloud-actions__title';
        title.textContent = word.text;
        actionsPanel.appendChild(title);

        const actionsContainer = document.createElement('div');
        actionsContainer.className = 'word-cloud-actions__buttons';

        const ignoreButton = document.createElement('button');
        ignoreButton.type = 'button';
        ignoreButton.className = 'word-cloud-action';
        ignoreButton.dataset.action = 'ignore';
        ignoreButton.textContent = 'Ignore word';

        const closeButton = document.createElement('button');
        closeButton.type = 'button';
        closeButton.className = 'word-cloud-action word-cloud-action--secondary';
        closeButton.dataset.action = 'close';
        closeButton.textContent = 'Close';

        actionsContainer.appendChild(ignoreButton);
        actionsContainer.appendChild(closeButton);
        actionsPanel.appendChild(actionsContainer);

        actionsPanel.addEventListener('click', (event) => {
            const action = event.target.dataset.action;
            if (!action) {
                return;
            }
            event.stopPropagation();
            if (action === 'ignore') {
                emitIgnoreEvent(word);
                hideWordActions();
                clearSelection();
            } else if (action === 'close') {
                hideWordActions();
            }
        });

        document.body.appendChild(actionsPanel);
        positionWordActions(element);
        bindActionDismiss();
    }

    function positionWordActions(element) {
        if (!actionsPanel) {
            return;
        }

        const rect = element.getBoundingClientRect();
        const panelRect = actionsPanel.getBoundingClientRect();
        const top = Math.max(rect.top - panelRect.height - 12, 12);
        const left = Math.min(
            Math.max(rect.left + rect.width / 2 - panelRect.width / 2, 12),
            window.innerWidth - panelRect.width - 12
        );

        actionsPanel.style.top = `${top + window.scrollY}px`;
        actionsPanel.style.left = `${left + window.scrollX}px`;
    }

    function hideWordActions() {
        if (actionsPanel) {
            actionsPanel.remove();
            actionsPanel = null;
            actionsWord = null;
        }
    }

    function emitIgnoreEvent(word) {
        const detail = {
            word: word.text,
            frequency: word.frequency,
        };

        if (window.EventBus) {
            try {
                window.EventBus.emit('wordcloud.ignore', detail);
            } catch (error) {
                console.error('[WordCloudManager] Failed to emit wordcloud.ignore via EventBus', error);
            }
        }

        window.dispatchEvent(new CustomEvent('wordcloud.ignore', { detail }));
    }

    function bindActionDismiss() {
        if (actionsDismissBound) {
            return;
        }

        document.addEventListener('click', (event) => {
            if (!actionsPanel) {
                return;
            }

            const target = event.target;
            if (actionsPanel.contains(target)) {
                return;
            }

            if (target.closest && target.closest('.word-cloud-item')) {
                return;
            }

            hideWordActions();
            clearSelection();
        });

        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                hideWordActions();
                clearSelection();
            }
        });

        const reposition = () => {
            if (!actionsPanel || !actionsWord) {
                return;
            }
            const element = Array.from(document.querySelectorAll('.word-cloud-item'))
                .find(item => item.textContent === actionsWord.text);
            if (element) {
                positionWordActions(element);
            }
        };

        window.addEventListener('resize', reposition);
        window.addEventListener('scroll', reposition, { passive: true });

        actionsDismissBound = true;
    }

    function selectWord(word) {
        selectedWord = word;

        let selectedElement = null;

        // Highlight selected word
        document.querySelectorAll('.word-cloud-item').forEach(element => {
            if (element.textContent === word.text) {
                selectedElement = element;
                element.classList.add('selected');
                element.style.color = '#FFD700';
                element.style.fontWeight = 'bold';
            } else {
                element.classList.remove('selected');
                // Dim non-associated words
                if (!word.associations.includes(element.textContent)) {
                    element.style.opacity = '0.3';
                }
            }
        });

        if (selectedElement) {
            showWordActions(selectedElement, word);
        }

        // Trigger event for other components
        if (window.EventBus) {
            window.EventBus.emit('wordcloud.selected', {
                word: word.text,
                data: word
            });
        }
    }

    function clearSelection() {
        selectedWord = null;
        document.querySelectorAll('.word-cloud-item').forEach(element => {
            element.classList.remove('selected');
            const word = wordData.get(element.textContent);
            if (word) {
                element.style.opacity = word.opacity;
                element.style.color = word.color;
            }
        });
        hideWordActions();
    }

    async function refresh() {
        try {
            // Fetch fresh data (request larger batch for richer cloud)
            const response = await fetch('/api/v1/prompts?per_page=200&sort_by=created_at&sort_desc=true');
            if (!response.ok) throw new Error('Failed to fetch prompts');

            const data = await response.json();
            const prompts = data.prompts
                || data.items
                || data.data?.prompts
                || data.data?.items
                || [];

            // Parse and render
            const words = parsePrompts(prompts);
            const container = document.getElementById('word-cloud-container');

            if (container && words.length > 0) {
                renderWordCloud(container, words);
            }
        } catch (error) {
            console.error('Word cloud refresh failed:', error);
        }
    }

    function startAutoRefresh() {
        stopAutoRefresh();
        refreshTimer = setInterval(refresh, config.refreshInterval);
    }

    function stopAutoRefresh() {
        if (refreshTimer) {
            clearInterval(refreshTimer);
            refreshTimer = null;
        }
    }

    // Public API
    return {
        init: function(container, options = {}) {
            // Merge options
            Object.assign(config, options);

            // Initial render
            refresh();

            // Start auto-refresh
            startAutoRefresh();

            // Add resize handler
            window.addEventListener('resize', debounce(() => {
                const container = document.getElementById('word-cloud-container');
                if (container && currentWords.length > 0) {
                    renderWordCloud(container, currentWords);
                }
            }, 250));

            // Listen for data updates
            if (window.EventBus) {
                window.EventBus.on('prompts.updated', refresh);
            }

            return this;
        },

        refresh: refresh,

        clearSelection: clearSelection,

        getSelectedWord: function() {
            return selectedWord;
        },

        setMaxWords: function(max) {
            config.maxWords = max;
            refresh();
        },

        renderFromFrequencyMap: function(container, frequencyMap = {}, options = {}) {
            if (!container || !frequencyMap) {
                return;
            }

            const entries = Object.entries(frequencyMap);
            if (!entries.length) {
                container.innerHTML = '<p class="word-cloud-empty">Not enough prompt data yet.</p>';
                return;
            }

            stopAutoRefresh();

            const previousConfig = { ...config };
            Object.assign(config, options);

            container.style.position = 'relative';
            container.style.minHeight = container.style.minHeight || '400px';

            const words = entries.map(([word, count]) => ({
                text: word,
                size: count,
                score: count,
                frequency: count,
                recency: 0,
                associations: [],
            }));

            renderWordCloud(container, words);

            Object.assign(config, previousConfig);
        },

        destroy: function() {
            stopAutoRefresh();
            hideTooltip();
            clearSelection();

            if (window.EventBus) {
                window.EventBus.off('prompts.updated', refresh);
            }
        }
    };

    // Utility functions
    function debounce(func, wait) {
        let timeout;
        return function executedFunction(...args) {
            const later = () => {
                clearTimeout(timeout);
                func(...args);
            };
            clearTimeout(timeout);
            timeout = setTimeout(later, wait);
        };
    }
})();

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = WordCloudManager;
}

// Attach to window for global access
if (typeof window !== 'undefined') {
    window.WordCloudManager = WordCloudManager;
}
