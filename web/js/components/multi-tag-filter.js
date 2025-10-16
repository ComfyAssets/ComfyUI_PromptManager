/**
 * Multi-Tag Filter Component
 * Provides a searchable multi-select tag filter with pills UI
 * @module MultiTagFilter
 */
const MultiTagFilter = (function() {
    'use strict';

    // Configuration
    const config = {
        apiEndpoint: '/api/v1/tags',
        searchDebounce: 300,
        maxVisibleTags: 50,
        placeholder: 'Search tags...',
        externalPillsContainer: null, // ID of external container for pills
    };

    // State
    let state = {
        allTags: [],
        filteredTags: [],
        selectedTags: [],
        isOpen: false,
        searchTerm: '',
        container: null,
        dropdownElement: null,
        pillsContainer: null, // External container for pills
        activeFiltersBar: null, // Active filters bar element
        searchInput: null,
        searchDebounceTimer: null,
    };

    // Private methods
    function createHTML(containerId) {
        const container = document.getElementById(containerId);
        if (!container) {
            console.error(`Container #${containerId} not found`);
            return null;
        }

        container.innerHTML = `
            <div class="multi-tag-filter">
                <div class="multi-tag-control">
                    <div class="multi-tag-search-wrapper">
                        <input
                            type="text"
                            class="multi-tag-search"
                            id="${containerId}-search"
                            placeholder="${config.placeholder}"
                            autocomplete="off"
                        />
                        <span class="multi-tag-dropdown-icon">▼</span>
                    </div>
                </div>
                <div class="multi-tag-dropdown" id="${containerId}-dropdown" style="display: none;">
                    <div class="multi-tag-list" id="${containerId}-list">
                        <!-- Tag options will appear here -->
                    </div>
                    <div class="multi-tag-empty" style="display: none;">
                        No tags found
                    </div>
                </div>
            </div>
        `;

        // Get external pills container if configured
        const pillsContainer = config.externalPillsContainer
            ? document.getElementById(config.externalPillsContainer)
            : null;

        const activeFiltersBar = pillsContainer
            ? pillsContainer.closest('.active-filters-bar')
            : null;

        return {
            container,
            pillsContainer,
            activeFiltersBar,
            searchInput: document.getElementById(`${containerId}-search`),
            dropdown: document.getElementById(`${containerId}-dropdown`),
            list: document.getElementById(`${containerId}-list`),
            emptyMessage: container.querySelector('.multi-tag-empty'),
        };
    }

    function attachEventListeners(elements) {
        // Search input focus - open dropdown
        elements.searchInput.addEventListener('focus', () => {
            state.isOpen = true;
            showDropdown(elements);
        });

        // Search input - filter tags
        elements.searchInput.addEventListener('input', (e) => {
            state.searchTerm = e.target.value.toLowerCase();

            // Debounce search
            clearTimeout(state.searchDebounceTimer);
            state.searchDebounceTimer = setTimeout(() => {
                filterTags();
                renderTagList(elements);
            }, config.searchDebounce);
        });

        // Click outside to close dropdown
        document.addEventListener('click', (e) => {
            if (!elements.container.contains(e.target)) {
                state.isOpen = false;
                hideDropdown(elements);
            }
        });

        // Prevent dropdown from closing when clicking inside
        elements.dropdown.addEventListener('click', (e) => {
            e.stopPropagation();
        });
    }

    async function fetchTags() {
        try {
            const response = await fetch(config.apiEndpoint);
            if (!response.ok) {
                throw new Error(`Failed to fetch tags: ${response.statusText}`);
            }

            const data = await response.json();
            if (data.success && Array.isArray(data.data)) {
                state.allTags = data.data;
                state.filteredTags = [...state.allTags];
                return true;
            } else {
                console.error('Invalid tags response format:', data);
                return false;
            }
        } catch (error) {
            console.error('Error fetching tags:', error);
            return false;
        }
    }

    function filterTags() {
        if (!state.searchTerm) {
            state.filteredTags = [...state.allTags];
        } else {
            state.filteredTags = state.allTags.filter(tag =>
                tag.name.toLowerCase().includes(state.searchTerm)
            );
        }

        // Limit visible tags for performance
        state.filteredTags = state.filteredTags.slice(0, config.maxVisibleTags);
    }

    function renderTagList(elements) {
        const { list, emptyMessage } = elements;

        // Clear existing content
        list.innerHTML = '';

        if (state.filteredTags.length === 0) {
            list.style.display = 'none';
            emptyMessage.style.display = 'block';
            return;
        }

        list.style.display = 'block';
        emptyMessage.style.display = 'none';

        // Render tag options
        state.filteredTags.forEach(tag => {
            const isSelected = state.selectedTags.includes(tag.name);

            const tagOption = document.createElement('div');
            tagOption.className = `multi-tag-option ${isSelected ? 'selected' : ''}`;
            tagOption.dataset.tagName = tag.name;

            tagOption.innerHTML = `
                <span class="multi-tag-checkbox">
                    ${isSelected ? '☑' : '☐'}
                </span>
                <span class="multi-tag-name">${escapeHtml(tag.name)}</span>
                <span class="multi-tag-count">${tag.usage_count}</span>
            `;

            tagOption.addEventListener('click', () => {
                toggleTag(tag.name, elements);
            });

            list.appendChild(tagOption);
        });
    }

    function renderPills(elements) {
        const { pillsContainer, activeFiltersBar } = elements;

        if (!pillsContainer) {
            return; // No external container configured
        }

        if (state.selectedTags.length === 0) {
            pillsContainer.innerHTML = '';
            if (activeFiltersBar) {
                activeFiltersBar.style.display = 'none';
            }
            return;
        }

        // Show active filters bar
        if (activeFiltersBar) {
            activeFiltersBar.style.display = 'flex';
        }

        pillsContainer.innerHTML = '';

        state.selectedTags.forEach(tagName => {
            const pill = document.createElement('div');
            pill.className = 'active-filter-pill';
            pill.dataset.tagName = tagName;

            pill.innerHTML = `
                <span class="active-filter-pill-text">${escapeHtml(tagName)}</span>
                <button class="active-filter-pill-remove" aria-label="Remove ${escapeHtml(tagName)}">
                    ×
                </button>
            `;

            const removeButton = pill.querySelector('.active-filter-pill-remove');
            removeButton.addEventListener('click', (e) => {
                e.stopPropagation();
                toggleTag(tagName, elements);
            });

            pillsContainer.appendChild(pill);
        });
    }

    function toggleTag(tagName, elements) {
        const index = state.selectedTags.indexOf(tagName);

        if (index === -1) {
            // Add tag
            state.selectedTags.push(tagName);
        } else {
            // Remove tag
            state.selectedTags.splice(index, 1);
        }

        // Re-render UI
        renderPills(elements);
        renderTagList(elements);

        // Emit change event for immediate filtering
        emitChangeEvent();
    }

    function emitChangeEvent() {
        // Emit to EventBus for dashboard integration
        if (typeof EventBus !== 'undefined') {
            EventBus.emit('multi-tag-filter.changed', {
                tags: [...state.selectedTags],
                count: state.selectedTags.length,
            });
        }

        // Also dispatch native event
        const event = new CustomEvent('multiTagFilterChanged', {
            detail: {
                tags: [...state.selectedTags],
                count: state.selectedTags.length,
            },
            bubbles: true,
        });

        if (state.container) {
            state.container.dispatchEvent(event);
        }
    }

    function showDropdown(elements) {
        elements.dropdown.style.display = 'block';
        renderTagList(elements);
    }

    function hideDropdown(elements) {
        elements.dropdown.style.display = 'none';
        elements.searchInput.value = '';
        state.searchTerm = '';
        filterTags();
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    // Public API
    const api = {
        /**
         * Initialize the multi-tag filter component
         * @param {string} containerId - ID of container element
         * @param {Object} options - Configuration options
         * @returns {Promise<boolean>} Success status
         */
        init: async function(containerId, options = {}) {
            // Merge config
            Object.assign(config, options);

            // Create HTML structure
            const elements = createHTML(containerId);
            if (!elements) {
                return false;
            }

            // Store references
            state.container = elements.container;
            state.pillsContainer = elements.pillsContainer;
            state.activeFiltersBar = elements.activeFiltersBar;
            state.searchInput = elements.searchInput;
            state.dropdownElement = elements.dropdown;

            // Attach event listeners
            attachEventListeners(elements);

            // Setup clear all button if active filters bar exists
            if (state.activeFiltersBar) {
                const clearAllBtn = state.activeFiltersBar.querySelector('#clearAllFiltersBtn');
                if (clearAllBtn) {
                    clearAllBtn.addEventListener('click', () => {
                        api.clearSelection();
                    });
                }
            }

            // Fetch tags from API
            const success = await fetchTags();
            if (!success) {
                console.error('Failed to initialize multi-tag filter');
                return false;
            }

            // Initial render
            renderPills(elements);

            return true;
        },

        /**
         * Get currently selected tags
         * @returns {string[]} Array of selected tag names
         */
        getSelectedTags: function() {
            return [...state.selectedTags];
        },

        /**
         * Set selected tags programmatically
         * @param {string[]} tags - Array of tag names to select
         */
        setSelectedTags: function(tags) {
            if (!Array.isArray(tags)) {
                console.error('setSelectedTags expects an array');
                return;
            }

            state.selectedTags = tags.filter(tag =>
                state.allTags.some(t => t.name === tag)
            );

            const elements = {
                pillsContainer: state.pillsContainer,
                list: state.dropdownElement?.querySelector('.multi-tag-list'),
                emptyMessage: state.dropdownElement?.querySelector('.multi-tag-empty'),
            };

            renderPills(elements);
            if (state.isOpen) {
                renderTagList(elements);
            }

            emitChangeEvent();
        },

        /**
         * Clear all selected tags
         */
        clearSelection: function() {
            state.selectedTags = [];

            const elements = {
                pillsContainer: state.pillsContainer,
                list: state.dropdownElement?.querySelector('.multi-tag-list'),
                emptyMessage: state.dropdownElement?.querySelector('.multi-tag-empty'),
            };

            renderPills(elements);
            if (state.isOpen) {
                renderTagList(elements);
            }

            emitChangeEvent();
        },

        /**
         * Refresh tags from API
         * @returns {Promise<boolean>} Success status
         */
        refresh: async function() {
            const success = await fetchTags();

            if (success) {
                filterTags();

                const elements = {
                    pillsContainer: state.pillsContainer,
                    list: state.dropdownElement?.querySelector('.multi-tag-list'),
                    emptyMessage: state.dropdownElement?.querySelector('.multi-tag-empty'),
                };

                renderPills(elements);
                if (state.isOpen) {
                    renderTagList(elements);
                }
            }

            return success;
        },

        /**
         * Subscribe to tag selection changes
         * @param {Function} callback - Function to call on change
         */
        onChange: function(callback) {
            if (typeof callback !== 'function') {
                console.error('onChange expects a function');
                return;
            }

            if (typeof EventBus !== 'undefined') {
                EventBus.on('multi-tag-filter.changed', callback);
            } else if (state.container) {
                state.container.addEventListener('multiTagFilterChanged', (e) => {
                    callback(e.detail);
                });
            }
        },

        /**
         * Clean up the component
         */
        destroy: function() {
            if (state.container) {
                state.container.innerHTML = '';
            }

            clearTimeout(state.searchDebounceTimer);

            state = {
                allTags: [],
                filteredTags: [],
                selectedTags: [],
                isOpen: false,
                searchTerm: '',
                container: null,
                dropdownElement: null,
                pillsContainer: null,
                searchInput: null,
                searchDebounceTimer: null,
            };
        }
    };

    return api;
})();

// Exports
if (typeof module !== 'undefined' && module.exports) {
    module.exports = MultiTagFilter;
}
if (typeof window !== 'undefined') {
    window.MultiTagFilter = MultiTagFilter;
}
