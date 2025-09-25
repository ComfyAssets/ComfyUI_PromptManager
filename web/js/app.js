/**
 * PromptManager App - Professional Edition
 * Modern dark theme with enhanced functionality
 */

(function() {
    'use strict';

    if (typeof window === 'undefined') {
        return;
    }
    const pmActive = (window.location?.pathname || '').includes('/prompt_manager');
    if (!pmActive) {
        console.info('[PromptManager] legacy app bootstrap skipped outside PromptManager UI context');
        return;
    }

    // State Management
    let prompts = [];
    let currentView = 'table';
    let currentEditId = null;

    // API Configuration
    const API_BASE_URL = '/api/prompt_manager';

    // DOM Helper
    const el = (id) => document.getElementById(id);

    const notify = (message, type = 'info') => {
        if (typeof window.showToast === 'function') {
            window.showToast(message, type);
        } else {
            console.log(`[${type}]`, message);
        }
    };

    // Fetch prompts from database
    async function fetchPrompts() {
        try {
            const response = await fetch(`${API_BASE_URL}/prompts`, {
                method: 'GET',
                headers: {
                    'Accept': 'application/json',
                }
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const data = await response.json();

            // Handle varied response shapes from API
            // Prefer explicit prompts array, then items, then nested under data
            const items = (
                data.prompts ||
                data.items ||
                data.data?.prompts ||
                data.data?.items ||
                []
            );

            // Transform the API response to match our frontend format
            prompts = items.map(p => ({
                id: p.id,
                prompt: p.positive_prompt || p.prompt || '',
                negative: p.negative_prompt || '',
                category: p.category || 'Uncategorized',
                tags: p.tags || [],
                rating: p.rating || 0,
                created: p.created_at ? new Date(p.created_at).toLocaleDateString() : new Date().toLocaleDateString(),
                selected: false,
                // Store image IDs for later use
                images: p.images || []
            }));

            return prompts;
        } catch (error) {
            console.error('Error fetching prompts:', error);
            // Fall back to empty array if API is not available
            prompts = [];
            return prompts;
        }
    }

    // Update total count
    function setTotal() {
        const totalEl = el('totalCount');
        if (totalEl) {
            totalEl.textContent = `(${prompts.length} total)`;
        }
    }

    // Render Table View
    function renderTable() {
        const tbody = el('tableBody');
        if (!tbody) return;

        tbody.innerHTML = '';
        for (const p of applyFilters(prompts)) {
            const tr = document.createElement('tr');
            tr.className = 'table-row';
            tr.innerHTML = `
                <td class="px-6 py-4" style="color: var(--pm-mute);">
                    <span class="badge">#${p.id}</span>
                </td>
                <td class="px-6 py-4">
                    <div class="flex items-start gap-3">
                        <input type="checkbox" ${p.selected ? 'checked' : ''} onchange="toggleSelect(${p.id}, this.checked)" class="mt-1 rounded card-checkbox" />
                        <div class="flex-1 min-w-0">
                            <div class="line-clamp-2 text-sm leading-relaxed" style="color: #f4f4f5;">${escapeHtml(p.prompt)}</div>
                            ${p.negative ? `<div class='neg mt-2'>
                                <div class='neg-head'>Negative Prompt</div>
                                <div class='neg-body line-clamp-2'>${escapeHtml(p.negative)}</div>
                            </div>` : ''}
                        </div>
                    </div>
                </td>
                <td class="px-6 py-4">
                    <span class="chip">${p.category}</span>
                </td>
                <td class="px-6 py-4">
                    <div class="flex flex-wrap gap-1">
                        ${p.tags.map(t => `<span class='chip'>${t}</span>`).join('')}
                    </div>
                </td>
                <td class="px-6 py-4">${renderStars(p.rating)}</td>
                <td class="px-6 py-4" style="color: var(--pm-mute);">${p.created}</td>
                <td class="px-6 py-4">
                    <div class="flex items-center gap-2">
                        <button class="icon-btn" title="Gallery" onclick="openGallery(${p.id})">🖼️</button>
                        <button class="icon-btn" title="Edit" onclick="openEdit(${p.id})">✏️</button>
                        <button class="icon-btn" title="Delete" onclick="deletePrompt(${p.id})" style="border-color: rgba(220, 38, 38, 0.3); background: rgba(127, 29, 29, 0.15);">🗑️</button>
                    </div>
                </td>`;
            tbody.appendChild(tr);
        }
    }

    // Render Cards View
    function renderCards() {
        const grid = el('cardsGrid');
        if (!grid) return;

        grid.innerHTML = '';
        for (const p of applyFilters(prompts)) {
            const card = document.createElement('div');
            card.className = 'card';
            card.innerHTML = `
                <div class="card-content">
                    <input type="checkbox" ${p.selected ? 'checked' : ''} onchange="toggleSelect(${p.id}, this.checked)" class="card-checkbox" />
                    <div class="card-main">
                        <div class="card-meta">
                            <span class="badge">#${p.id}</span>
                            <span class="chip">${p.category}</span>
                            <span class="card-meta-date">📅 ${p.created}</span>
                            <span class="ml-auto">${renderStars(p.rating)}</span>
                        </div>
                        <div class="line-clamp-3 text-sm leading-relaxed mb-3" style="color: #f4f4f5;">${escapeHtml(p.prompt)}</div>
                        ${p.negative ? `<div class='neg mb-3'>
                            <div class='neg-head'>
                                <span>Negative Prompt</span>
                                <span class="copy-icon">📋</span>
                            </div>
                            <div class='neg-body line-clamp-2'>${escapeHtml(p.negative)}</div>
                        </div>` : ''}
                        <div class="card-tags">
                            ${p.tags.map(t => `<span class='chip'>${t}</span>`).join('')}
                            <button class="add-tag-btn" onclick="openTagModal(${p.id})">+ Add Tag</button>
                        </div>
                    </div>
                    <div class="card-actions">
                        <button class="icon-btn" title="Gallery" onclick="openGallery(${p.id})">🖼️</button>
                        <button class="icon-btn" title="Edit" onclick="openEdit(${p.id})">✏️</button>
                        <button class="icon-btn" title="Delete" onclick="deletePrompt(${p.id})" style="border-color: rgba(220, 38, 38, 0.3); background: rgba(127, 29, 29, 0.15);">🗑️</button>
                    </div>
                </div>`;
            grid.appendChild(card);
        }
    }

    // Main render function
    function render() {
        setTotal();
        if (currentView === 'table') {
            renderTable();
        } else {
            renderCards();
        }
    }

    // Utility Functions
    function renderStars(n) {
        const full = '★'.repeat(n);
        const empty = '☆'.repeat(5 - n);
        return `<span style='color: var(--pm-brand2);'>${full}</span><span style='color: rgba(255,255,255,0.18);'>${empty}</span>`;
    }

    function escapeHtml(s) {
        return s.replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;','\'':'&#39;'}[m]));
    }

    // Filter System
    function applyFilters(list) {
        const q = el('filterText')?.value.trim().toLowerCase() || '';
        const cat = el('filterCategory')?.value.trim().toLowerCase() || '';
        const sort = el('filterSort')?.value || 'newest';

        let out = list.filter(p => {
            const matchesQ = !q || p.prompt.toLowerCase().includes(q) || p.negative.toLowerCase().includes(q) || p.tags.some(t => t.toLowerCase().includes(q));
            const matchesCat = !cat || p.category.toLowerCase() === cat;
            return matchesQ && matchesCat;
        });

        // Sorting
        if (sort === 'newest') out.sort((a,b) => b.id - a.id);
        if (sort === 'oldest') out.sort((a,b) => a.id - b.id);
        if (sort === 'rating') out.sort((a,b) => b.rating - a.rating || b.id - a.id);
        if (sort === 'alphabetical') out.sort((a,b) => a.prompt.localeCompare(b.prompt));

        const pageSize = parseInt(el('pageSize')?.value || '50', 10);
        return out.slice(0, pageSize);
    }

    // View Switching
    function switchView(mode) {
        currentView = mode;
        const viewTable = el('viewTable');
        const viewCards = el('viewCards');
        const viewToggle = el('viewToggle');

        if (viewTable) viewTable.classList.toggle('hidden', mode !== 'table');
        if (viewCards) viewCards.classList.toggle('hidden', mode !== 'cards');
        if (viewToggle) viewToggle.checked = mode === 'cards';

        // Save preference to both localStorage locations
        try {
            // Save to direct preference
            localStorage.setItem('promptViewMode', mode);

            // Also update the main settings storage
            const settingsStr = localStorage.getItem('promptManagerSettings');
            if (settingsStr) {
                const settings = JSON.parse(settingsStr);
                settings.promptViewMode = mode;
                localStorage.setItem('promptManagerSettings', JSON.stringify(settings));
            }
        } catch (e) {
            console.error('Failed to save view preference:', e);
        }

        render();
    }

    // Toggle view mode function for the new slider
    window.toggleViewMode = function(isCards) {
        switchView(isCards ? 'cards' : 'table');
    };

    // CRUD Operations
    window.toggleSelect = function(id, val) {
        const p = prompts.find(x => x.id === id);
        if (p) p.selected = val;
    };

    window.openEdit = function(id) {
        const p = prompts.find(x => x.id === id);
        if (!p) return;

        currentEditId = id;
        if (el('editPrompt')) el('editPrompt').value = p.prompt;
        if (el('editNegative')) el('editNegative').value = p.negative;
        if (el('editCategory')) el('editCategory').value = p.category;
        if (el('editTags')) el('editTags').value = p.tags.join(', ');
        if (el('editRating')) el('editRating').value = p.rating;

        const modal = el('modal');
        if (modal) {
            modal.classList.remove('hidden');
            modal.classList.add('flex');
        }
    };

    window.closeModal = function() {
        const modal = el('modal');
        if (modal) {
            modal.classList.add('hidden');
            modal.classList.remove('flex');
        }
    };

    window.saveEdit = async function() {
        const p = prompts.find(x => x.id === currentEditId);
        if (!p) return;

        const updateData = {
            positive_prompt: el('editPrompt')?.value.trim() || p.prompt,
            negative_prompt: el('editNegative')?.value.trim() || '',
            category: el('editCategory')?.value.trim() || p.category,
            tags: (el('editTags')?.value || '').split(',').map(t => t.trim()).filter(Boolean),
            rating: Math.max(0, Math.min(5, parseInt(el('editRating')?.value || '0', 10)))
        };

        try {
            const response = await fetch(`${API_BASE_URL}/prompts/${currentEditId}`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                },
                body: JSON.stringify(updateData)
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const updatedPrompt = await response.json();

            // Update local data
            p.prompt = updatedPrompt.positive_prompt || updatedPrompt.prompt || p.prompt;
            p.negative = updatedPrompt.negative_prompt || '';
            p.category = updatedPrompt.category || p.category;
            p.tags = updatedPrompt.tags || [];
            p.rating = updatedPrompt.rating || 0;

            closeModal();
            render();
        } catch (error) {
            console.error('Error updating prompt:', error);
            notify('Failed to update prompt. Please check if the API server is running.', 'error');
        }
    };

    window.deletePrompt = async function(id) {
        if (confirm('Delete this prompt? This action cannot be undone.')) {
            try {
                const response = await fetch(`${API_BASE_URL}/prompts/${id}`, {
                    method: 'DELETE',
                    headers: {
                        'Accept': 'application/json'
                    }
                });

                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }

                // Remove from local list
                prompts = prompts.filter(p => p.id !== id);
                render();
            } catch (error) {
                console.error('Error deleting prompt:', error);
                notify('Failed to delete prompt. Please check if the API server is running.', 'error');
            }
        }
    };

    window.openGallery = function(id) {
        // Open the full-featured gallery filtered by prompt
        const url = `gallery.html?prompt_id=${encodeURIComponent(id)}`;
        window.open(url, '_blank');
    };

    // Global functions for page
    window.handleExport = function() {
        notify('Export functionality coming soon', 'info');
    };

    window.showStats = function() {
        notify('Statistics page coming soon', 'info');
    };

    window.showSettings = function() {
        notify('Settings page coming soon', 'info');
    };

    // Page navigation
    window.loadPage = function(page) {
        const navLinks = document.querySelectorAll('.nav-link');
        const headerTitle = document.querySelector('.header-title');
        const headerSubtitle = document.querySelector('.header-subtitle');

        // Update active nav
        navLinks.forEach(link => {
            link.classList.remove('active');
            if (link.getAttribute('href') === '#' + page) {
                link.classList.add('active');
            }
        });

        // Update header
        const pageConfigs = {
            dashboard: {
                title: 'Dashboard',
                subtitle: 'Professional prompt management suite'
            },
            stats: {
                title: 'Statistics',
                subtitle: 'Analytics and insights'
            },
            collections: {
                title: 'Collections',
                subtitle: 'Organized prompt groups'
            },
            settings: {
                title: 'Settings',
                subtitle: 'Configure your preferences'
            }
        };

        const config = pageConfigs[page];
        if (config && headerTitle) {
            headerTitle.innerHTML = config.title + ' <span id="totalCount" style="color: var(--pm-mute); font-weight: 400;"></span>';
            if (headerSubtitle) headerSubtitle.textContent = config.subtitle;

            // Re-render to update total count
            setTotal();
        }

        // Load page content
        if (page === 'settings' && window.renderSettingsPage) {
            // Hide dashboard content
            const actionBar = document.querySelector('.action-bar');
            const viewTable = document.querySelector('#viewTable');
            const viewCards = document.querySelector('#viewCards');

            if (actionBar) actionBar.style.display = 'none';
            if (viewTable) viewTable.style.display = 'none';
            if (viewCards) viewCards.style.display = 'none';

            // Render settings page
            window.renderSettingsPage();
        } else if (page === 'dashboard') {
            // Show dashboard content
            const actionBar = document.querySelector('.action-bar');
            const viewTable = document.querySelector('#viewTable');
            const settingsContainer = document.querySelector('.settings-container');

            if (actionBar) actionBar.style.display = '';
            if (viewTable) viewTable.style.display = currentView === 'table' ? '' : 'none';
            if (settingsContainer) settingsContainer.remove();

            render();
        } else if (page !== 'dashboard') {
            notify(`${config.title} page coming soon`, 'info');
        }
    };

    // Event Listeners
    function setupEventListeners() {
        // View switcher is now handled by the onchange attribute in HTML

        // Search and filters
        const btnSearch = el('btnSearch');
        if (btnSearch) btnSearch.addEventListener('click', render);

        ['filterText','filterCategory','filterSort','pageSize'].forEach(id => {
            const elem = el(id);
            if (elem) elem.addEventListener('change', render);
        });

        // Select all
        const selectAll = el('selectAll');
        if (selectAll) {
            selectAll.addEventListener('change', (e) => {
                for (const p of prompts) p.selected = e.target.checked;
                render();
            });
        }

        // Bulk actions
        const btnDeleteSelected = el('btnDeleteSelected');
        if (btnDeleteSelected) {
            btnDeleteSelected.addEventListener('click', () => {
                if (confirm('Delete selected prompts? This action cannot be undone.')) {
                    prompts = prompts.filter(p => !p.selected);
                    render();
                }
            });
        }

        const btnNewPrompt = el('btnNewPrompt');
        if (btnNewPrompt) {
            btnNewPrompt.addEventListener('click', () => {
                openNewPromptModal();
            });
        }

        const btnAddTags = el('btnAddTags');
        if (btnAddTags) {
            btnAddTags.addEventListener('click', () => {
                const selected = prompts.filter(p => p.selected);
                if (selected.length === 0) {
                    notify('No prompts selected', 'warning');
                    return;
                }
                const newTags = prompt('Enter tags to add (comma-separated):');
                if (newTags) {
                    const tags = newTags.split(',').map(t => t.trim()).filter(Boolean);
                    selected.forEach(p => {
                        tags.forEach(tag => {
                            if (!p.tags.includes(tag)) p.tags.push(tag);
                        });
                    });
                    render();
                }
            });
        }

        const btnSetCategory = el('btnSetCategory');
        if (btnSetCategory) {
            btnSetCategory.addEventListener('click', () => {
                const selected = prompts.filter(p => p.selected);
                if (selected.length === 0) {
                    notify('No prompts selected', 'warning');
                    return;
                }
                const newCategory = prompt('Enter category:');
                if (newCategory) {
                    selected.forEach(p => p.category = newCategory);
                    render();
                }
            });
        }
    }

    // Keyboard Shortcuts
    function setupKeyboardShortcuts() {
        document.addEventListener('keydown', (e) => {
            // Cmd+K or Ctrl+K to focus search
            if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
                e.preventDefault();
                const searchField = el('filterText');
                if (searchField) {
                    searchField.focus();
                    searchField.select();
                }
            }
        });
    }

    // Category Management
    let categories = ['Portrait', 'Fantasy', 'Asian', 'Landscape', 'Abstract'];

    function loadCategories() {
        const savedCategories = localStorage.getItem('promptCategories');
        if (savedCategories) {
            try {
                categories = JSON.parse(savedCategories);
            } catch (e) {
                console.error('Failed to load categories:', e);
            }
        }
        updateCategorySelects();
    }

    function saveCategories() {
        localStorage.setItem('promptCategories', JSON.stringify(categories));
        updateCategorySelects();
    }

    function updateCategorySelects() {
        // Update filter category dropdown
        const filterCategory = el('filterCategory');
        if (filterCategory) {
            const currentValue = filterCategory.value;
            filterCategory.innerHTML = '<option value="">All Categories</option>';
            categories.forEach(cat => {
                const option = document.createElement('option');
                option.value = cat;
                option.textContent = cat;
                filterCategory.appendChild(option);
            });
            filterCategory.value = currentValue;
        }

        // Update edit modal category input (if open)
        const editCategory = el('editCategory');
        if (editCategory) {
            // Could enhance with datalist for suggestions
        }
    }

    window.showCategoryModal = function() {
        const modal = el('categoryModal');
        if (modal) {
            modal.classList.remove('hidden');
            renderCategoryList();
            el('newCategoryName').value = '';
            el('newCategoryName').focus();
        }
    };

    window.closeCategoryModal = function() {
        const modal = el('categoryModal');
        if (modal) {
            modal.classList.add('hidden');
        }
    };

    // ================================================
    // Tag Management
    // ================================================
    let currentTagPromptId = null;
    let selectedTags = new Set();
    let allTags = new Set();
    let autocompleteIndex = -1;

    // Collect all existing tags from prompts
    function collectAllTags() {
        allTags.clear();
        prompts.forEach(p => {
            if (p.tags && Array.isArray(p.tags)) {
                p.tags.forEach(tag => allTags.add(tag));
            }
        });
    }

    // Open tag modal
    window.openTagModal = function(promptId) {
        currentTagPromptId = promptId;
        selectedTags.clear();
        autocompleteIndex = -1;

        // Get current prompt's tags
        const prompt = prompts.find(p => p.id === promptId);
        if (prompt && prompt.tags) {
            prompt.tags.forEach(tag => selectedTags.add(tag));
        }

        // Collect all tags for autocomplete
        collectAllTags();

        // Show modal
        const modal = el('tagModal');
        if (modal) {
            modal.classList.remove('hidden');
            renderSelectedTags();

            const input = el('tagInput');
            if (input) {
                input.value = '';
                input.focus();
            }
        }
    };

    // Close tag modal
    window.closeTagModal = function() {
        const modal = el('tagModal');
        if (modal) {
            modal.classList.add('hidden');
        }
        currentTagPromptId = null;
        selectedTags.clear();
        hideAutocomplete();
    };

    // Render selected tags
    function renderSelectedTags() {
        const container = el('selectedTags');
        if (!container) return;

        if (selectedTags.size === 0) {
            container.innerHTML = '<span style="color: var(--pm-mute); font-size: 0.875rem;">No tags selected</span>';
        } else {
            container.innerHTML = Array.from(selectedTags).map(tag => `
                <div class="tag-chip">
                    <span>${tag}</span>
                    <span class="remove-tag" onclick="removeSelectedTag('${tag}')">×</span>
                </div>
            `).join('');
        }
    }

    // Add tag from input
    function addTagFromInput() {
        const input = el('tagInput');
        if (!input) return;

        const tag = input.value.trim();
        if (tag && !selectedTags.has(tag)) {
            selectedTags.add(tag);
            allTags.add(tag); // Add to autocomplete list
            renderSelectedTags();
            input.value = '';
            hideAutocomplete();
        }
    }

    // Remove selected tag
    window.removeSelectedTag = function(tag) {
        selectedTags.delete(tag);
        renderSelectedTags();
    };

    // Apply tags to prompt
    window.applyTags = async function() {
        if (currentTagPromptId === null) return;

        const prompt = prompts.find(p => p.id === currentTagPromptId);
        if (!prompt) return;

        const updateData = {
            positive_prompt: prompt.prompt,
            negative_prompt: prompt.negative || '',
            category: prompt.category,
            tags: Array.from(selectedTags),
            rating: prompt.rating
        };

        try {
            const response = await fetch(`${API_BASE_URL}/prompts/${currentTagPromptId}`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                },
                body: JSON.stringify(updateData)
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            // Update local data
            prompt.tags = Array.from(selectedTags);
            if (currentView === 'table') {
                renderTable();
            } else {
                renderCards();
            }

            closeTagModal();
        } catch (error) {
            console.error('Error updating tags:', error);
            notify('Failed to update tags. Please check if the API server is running.', 'error');
        }
    };

    // Show autocomplete suggestions
    function showAutocomplete(query) {
        const autocompleteEl = el('tagAutocomplete');
        if (!autocompleteEl) return;

        const suggestions = Array.from(allTags)
            .filter(tag => !selectedTags.has(tag) && tag.toLowerCase().includes(query.toLowerCase()))
            .slice(0, 10);

        if (suggestions.length === 0 || query === '') {
            hideAutocomplete();
            return;
        }

        autocompleteEl.innerHTML = suggestions.map((tag, idx) => `
            <div class="tag-suggestion ${idx === autocompleteIndex ? 'selected' : ''}"
                 onclick="selectSuggestion('${tag}')">${tag}</div>
        `).join('');

        autocompleteEl.classList.add('show');
    }

    // Hide autocomplete
    function hideAutocomplete() {
        const autocompleteEl = el('tagAutocomplete');
        if (autocompleteEl) {
            autocompleteEl.classList.remove('show');
            autocompleteEl.innerHTML = '';
        }
        autocompleteIndex = -1;
    }

    // Select autocomplete suggestion
    window.selectSuggestion = function(tag) {
        selectedTags.add(tag);
        renderSelectedTags();
        const input = el('tagInput');
        if (input) {
            input.value = '';
            input.focus();
        }
        hideAutocomplete();
    };

    // New Prompt Modal Functions
    let newPromptTags = new Set();
    let newPromptAutocompleteIndex = -1;

    // Open new prompt modal
    window.openNewPromptModal = function() {
        newPromptTags.clear();
        newPromptAutocompleteIndex = -1;

        // Reset form fields
        const contentEl = el('newPromptContent');
        const negativeEl = el('newPromptNegative');
        const categoryEl = el('newPromptCategory');
        const ratingEl = el('newPromptRating');
        const tagInputEl = el('newPromptTagInput');

        if (contentEl) contentEl.value = '';
        if (negativeEl) negativeEl.value = '';
        if (categoryEl) categoryEl.value = 'Uncategorized';
        if (ratingEl) ratingEl.value = '0';
        if (tagInputEl) tagInputEl.value = '';

        // Collect all tags for autocomplete
        collectAllTags();

        // Show modal
        const modal = el('newPromptModal');
        if (modal) {
            modal.classList.remove('hidden');
            renderNewPromptTags();
            if (contentEl) contentEl.focus();
        }
    };

    // Close new prompt modal
    window.closeNewPromptModal = function() {
        const modal = el('newPromptModal');
        if (modal) {
            modal.classList.add('hidden');
        }
        newPromptTags.clear();
        hideNewPromptAutocomplete();
    };

    // Render tags for new prompt
    function renderNewPromptTags() {
        const container = el('newPromptSelectedTags');
        if (!container) return;

        if (newPromptTags.size === 0) {
            container.innerHTML = '<span style="color: var(--pm-mute); font-size: 0.875rem;">No tags added</span>';
        } else {
            container.innerHTML = Array.from(newPromptTags).map(tag => `
                <div class="tag-chip">
                    <span>${tag}</span>
                    <span class="remove-tag" onclick="removeNewPromptTag('${tag}')">×</span>
                </div>
            `).join('');
        }
    }

    // Remove tag from new prompt
    window.removeNewPromptTag = function(tag) {
        newPromptTags.delete(tag);
        renderNewPromptTags();
    };

    // Add tag from new prompt input
    function addNewPromptTagFromInput() {
        const input = el('newPromptTagInput');
        if (!input) return;

        const tag = input.value.trim();
        if (tag && !newPromptTags.has(tag)) {
            newPromptTags.add(tag);
            allTags.add(tag); // Add to global autocomplete list
            renderNewPromptTags();
            input.value = '';
            hideNewPromptAutocomplete();
        }
    }

    // Show autocomplete for new prompt
    function showNewPromptAutocomplete(query) {
        const autocompleteEl = el('newPromptTagAutocomplete');
        if (!autocompleteEl) return;

        const suggestions = Array.from(allTags)
            .filter(tag => !newPromptTags.has(tag) && tag.toLowerCase().includes(query.toLowerCase()))
            .slice(0, 10);

        if (suggestions.length === 0 || query === '') {
            hideNewPromptAutocomplete();
            return;
        }

        autocompleteEl.innerHTML = suggestions.map((tag, idx) => `
            <div class="tag-suggestion ${idx === newPromptAutocompleteIndex ? 'selected' : ''}"
                 onclick="selectNewPromptSuggestion('${tag}')">${tag}</div>
        `).join('');

        autocompleteEl.classList.add('show');
    }

    // Hide autocomplete for new prompt
    function hideNewPromptAutocomplete() {
        const autocompleteEl = el('newPromptTagAutocomplete');
        if (autocompleteEl) {
            autocompleteEl.classList.remove('show');
            autocompleteEl.innerHTML = '';
        }
        newPromptAutocompleteIndex = -1;
    }

    // Select autocomplete suggestion for new prompt
    window.selectNewPromptSuggestion = function(tag) {
        newPromptTags.add(tag);
        renderNewPromptTags();
        const input = el('newPromptTagInput');
        if (input) {
            input.value = '';
            input.focus();
        }
        hideNewPromptAutocomplete();
    };

    // Create new prompt
    window.createNewPrompt = async function() {
        const contentEl = el('newPromptContent');
        const negativeEl = el('newPromptNegative');
        const categoryEl = el('newPromptCategory');
        const ratingEl = el('newPromptRating');

        if (!contentEl || !contentEl.value.trim()) {
            notify('Please enter prompt content', 'warning');
            return;
        }

        const newPromptData = {
            positive_prompt: contentEl.value.trim(),
            negative_prompt: negativeEl ? negativeEl.value.trim() : '',
            category: categoryEl ? categoryEl.value : 'Uncategorized',
            tags: Array.from(newPromptTags),
            rating: ratingEl ? parseInt(ratingEl.value) : 0
        };

        try {
            const response = await fetch(`${API_BASE_URL}/prompts`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                },
                body: JSON.stringify(newPromptData)
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const createdPrompt = await response.json();

            // Add the new prompt to our local list
            const newPrompt = {
                id: createdPrompt.id,
                prompt: createdPrompt.positive_prompt || createdPrompt.prompt || '',
                negative: createdPrompt.negative_prompt || '',
                category: createdPrompt.category || 'Uncategorized',
                tags: createdPrompt.tags || [],
                rating: createdPrompt.rating || 0,
                created: createdPrompt.created_at ? new Date(createdPrompt.created_at).toLocaleDateString() : new Date().toLocaleDateString(),
                selected: false,
                images: createdPrompt.images || []
            };

            prompts.unshift(newPrompt);
            render();
            closeNewPromptModal();
        } catch (error) {
            console.error('Error creating prompt:', error);
            notify('Failed to create prompt. Please check if the API server is running.', 'error');
        }
    };

    // Handle tag input events
    document.addEventListener('DOMContentLoaded', function() {
        // Handle existing tag modal input
        const tagInput = el('tagInput');
        if (tagInput) {
            // Handle input events
            tagInput.addEventListener('input', function(e) {
                const query = e.target.value.trim();

                // Check for comma or space to add tag
                if (e.target.value.includes(',') || e.target.value.includes(' ')) {
                    const tag = e.target.value.replace(/[,\s]+$/, '').trim();
                    if (tag) {
                        selectedTags.add(tag);
                        allTags.add(tag);
                        renderSelectedTags();
                        e.target.value = '';
                        hideAutocomplete();
                    }
                } else {
                    showAutocomplete(query);
                }
            });

            // Handle keyboard navigation
            tagInput.addEventListener('keydown', function(e) {
                const autocompleteEl = el('tagAutocomplete');
                const suggestions = autocompleteEl ? autocompleteEl.querySelectorAll('.tag-suggestion') : [];

                if (e.key === 'ArrowDown') {
                    e.preventDefault();
                    if (suggestions.length > 0) {
                        autocompleteIndex = Math.min(autocompleteIndex + 1, suggestions.length - 1);
                        showAutocomplete(e.target.value.trim());
                    }
                } else if (e.key === 'ArrowUp') {
                    e.preventDefault();
                    if (suggestions.length > 0) {
                        autocompleteIndex = Math.max(autocompleteIndex - 1, -1);
                        showAutocomplete(e.target.value.trim());
                    }
                } else if (e.key === 'Enter') {
                    e.preventDefault();
                    if (autocompleteIndex >= 0 && suggestions[autocompleteIndex]) {
                        const tag = suggestions[autocompleteIndex].textContent;
                        selectSuggestion(tag);
                    } else {
                        addTagFromInput();
                    }
                } else if (e.key === 'Escape') {
                    hideAutocomplete();
                } else if ((e.key === ' ' || e.key === ',') && e.target.value.trim()) {
                    e.preventDefault();
                    addTagFromInput();
                }
            });

            // Handle click outside to close autocomplete
            document.addEventListener('click', function(e) {
                if (!e.target.closest('.tag-input-container')) {
                    hideAutocomplete();
                }
            });
        }

        // Handle new prompt tag input
        const newPromptTagInput = el('newPromptTagInput');
        if (newPromptTagInput) {
            // Handle input events
            newPromptTagInput.addEventListener('input', function(e) {
                const query = e.target.value.trim();

                // Check for comma or space to add tag
                if (e.target.value.includes(',') || e.target.value.includes(' ')) {
                    const tag = e.target.value.replace(/[,\s]+$/, '').trim();
                    if (tag) {
                        newPromptTags.add(tag);
                        allTags.add(tag);
                        renderNewPromptTags();
                        e.target.value = '';
                        hideNewPromptAutocomplete();
                    }
                } else {
                    showNewPromptAutocomplete(query);
                }
            });

            // Handle keyboard navigation
            newPromptTagInput.addEventListener('keydown', function(e) {
                const autocompleteEl = el('newPromptTagAutocomplete');
                const suggestions = autocompleteEl ? autocompleteEl.querySelectorAll('.tag-suggestion') : [];

                if (e.key === 'ArrowDown') {
                    e.preventDefault();
                    if (suggestions.length > 0) {
                        newPromptAutocompleteIndex = Math.min(newPromptAutocompleteIndex + 1, suggestions.length - 1);
                        showNewPromptAutocomplete(e.target.value.trim());
                    }
                } else if (e.key === 'ArrowUp') {
                    e.preventDefault();
                    if (suggestions.length > 0) {
                        newPromptAutocompleteIndex = Math.max(newPromptAutocompleteIndex - 1, -1);
                        showNewPromptAutocomplete(e.target.value.trim());
                    }
                } else if (e.key === 'Enter') {
                    e.preventDefault();
                    if (newPromptAutocompleteIndex >= 0 && suggestions[newPromptAutocompleteIndex]) {
                        const tag = suggestions[newPromptAutocompleteIndex].textContent;
                        selectNewPromptSuggestion(tag);
                    } else {
                        addNewPromptTagFromInput();
                    }
                } else if (e.key === 'Escape') {
                    hideNewPromptAutocomplete();
                }
            });

            // Handle click outside to close autocomplete
            document.addEventListener('click', function(e) {
                if (!e.target.closest('#newPromptTagInput') && !e.target.closest('#newPromptTagAutocomplete')) {
                    hideNewPromptAutocomplete();
                }
            });
        }
    });

    window.addCategory = function() {
        const input = el('newCategoryName');
        if (!input) return;

        const name = input.value.trim();
        if (!name) {
            notify('Please enter a category name', 'warning');
            return;
        }

        if (categories.includes(name)) {
            notify('Category already exists', 'warning');
            return;
        }

        categories.push(name);
        saveCategories();
        renderCategoryList();
        input.value = '';
        input.focus();
    };

    window.editCategory = function(oldName) {
        const newName = prompt('Edit category name:', oldName);
        if (newName && newName.trim() && newName !== oldName) {
            const index = categories.indexOf(oldName);
            if (index !== -1) {
                categories[index] = newName.trim();
                saveCategories();
                renderCategoryList();

                // Update existing prompts with this category
                prompts.forEach(p => {
                    if (p.category === oldName) {
                        p.category = newName.trim();
                    }
                });
                render();
            }
        }
    };

    window.deleteCategory = function(name) {
        if (confirm(`Delete category "${name}"? Prompts with this category will become "Uncategorized".`)) {
            categories = categories.filter(c => c !== name);
            saveCategories();
            renderCategoryList();

            // Update prompts with this category
            prompts.forEach(p => {
                if (p.category === name) {
                    p.category = 'Uncategorized';
                }
            });
            render();
        }
    };

    function renderCategoryList() {
        const list = el('categoryList');
        if (!list) return;

        list.innerHTML = '';
        categories.forEach(cat => {
            const item = document.createElement('div');
            item.className = 'category-item';
            item.innerHTML = `
                <span class="category-item-name">${escapeHtml(cat)}</span>
                <div class="category-item-actions">
                    <button class="btn btn-sm" onclick="editCategory('${escapeHtml(cat)}')">✏️ Edit</button>
                    <button class="btn btn-danger btn-sm" onclick="deleteCategory('${escapeHtml(cat)}')">🗑️ Delete</button>
                </div>
            `;
            list.appendChild(item);
        });
    }

    // Initialize app
    async function init() {
        // Fetch prompts from database
        await fetchPrompts();
        setupEventListeners();
        setupKeyboardShortcuts();
        loadCategories();

        // Check for v1 migration
        if (typeof MigrationService !== 'undefined') {
            console.log('Migration service found, initializing...');
            MigrationService.init().then(result => {
                console.log('Migration init result:', result);
            }).catch(error => {
                console.error('Migration init error:', error);
            });

            // Add to window for debugging
            window.testMigration = async function() {
                console.log('Testing migration...');
                // Reset flags first
                MigrationService.resetMigration();
                // Wait a bit
                await new Promise(resolve => setTimeout(resolve, 100));
                // Try init again
                const result = await MigrationService.init();
                console.log('Test migration result:', result);
                return result;
            };

            window.forceMigration = function() {
                console.log('Forcing migration modal...');
                return MigrationService.triggerManualMigration();
            };

            window.resetMigrationFlags = function() {
                console.log('Resetting migration flags...');
                localStorage.removeItem('promptmanager_v2_migrated');
                localStorage.removeItem('promptmanager_v2_migrated_date');
                localStorage.removeItem('promptmanager_v2_fresh_install');
                localStorage.removeItem('promptmanager_v2_fresh_install_date');
                console.log('Migration flags reset. Reload page to see migration prompt.');
            };

            const setMigrationStatus = (variant, message, icon) => {
                const statusText = document.getElementById('migrationStatus');
                const statusIcon = document.getElementById('migrationStatusIcon');

                if (statusText) {
                    statusText.textContent = message;
                    statusText.className = `migration-status-text migration-status--${variant}`;
                }

                if (statusIcon) {
                    statusIcon.textContent = icon;
                }
            };

            const updateMigrationStatsList = (stats) => {
                const list = document.getElementById('migrationStatsList');
                if (!list) return;

                if (!stats) {
                    list.innerHTML = '';
                    return;
                }

                list.innerHTML = `
                    <li>Prompts: ${stats.prompt_count ?? stats.prompts ?? 0}</li>
                    <li>Images: ${stats.image_count ?? stats.images ?? 0}</li>
                    <li>Categories: ${stats.category_count ?? stats.categories ?? 0}</li>
                `;
            };

            // Global functions for settings page migration controls
            window.checkV1Database = async function() {
                console.log('Checking for v1 database...');

                const execBtn = document.getElementById('executeMigrationBtn');
                const resultsEl = document.getElementById('migrationResults');
                const resultsContent = document.getElementById('migrationResultsContent');
                const pathEl = document.getElementById('v1DbPath');

                setMigrationStatus('info', 'Checking migration status…', '🔄');
                updateMigrationStatsList(null);

                try {
                    const info = await MigrationService.getMigrationInfo();
                    const stats = info.v1Info || {};

                    if (pathEl) {
                        pathEl.textContent = stats.path || stats.v1_path || 'Unknown';
                    }
                    updateMigrationStatsList(stats);

                    if (info.needed) {
                        setMigrationStatus('success', 'V1 database found!', '✅');
                        if (execBtn) execBtn.disabled = false;
                        if (window.showToast) {
                            window.showToast('V1 database found! Migration is available.', 'success');
                        }
                    } else {
                        setMigrationStatus('warning', 'No v1 database found', '⚠️');
                        if (execBtn) execBtn.disabled = true;
                        if (window.showToast) {
                            window.showToast('No v1 database found or migration already completed', 'info');
                        }
                    }

                    if (resultsEl && resultsContent) {
                        const details = {
                            path: stats.path || stats.v1_path || 'N/A',
                            prompts: stats.prompt_count ?? stats.prompts ?? 0,
                            images: stats.image_count ?? stats.images ?? 0,
                            categories: stats.category_count ?? stats.categories ?? 0,
                            status: info.status
                        };
                        resultsEl.style.display = 'block';
                        resultsContent.innerHTML = `
                            <h5>Migration Details</h5>
                            <pre class="migration-debug-output">
${JSON.stringify(details, null, 2)}
                            </pre>
                        `;
                    }

                    return info;
                } catch (error) {
                    console.error('Error checking v1 database:', error);
                    setMigrationStatus('error', `Error: ${error.message}`, '❌');
                    updateMigrationStatsList(null);
                    if (execBtn) execBtn.disabled = true;
                    if (window.showToast) {
                        window.showToast('Error checking for v1 database', 'error');
                    }
                    return null;
                }
            };

            window.executeMigration = async function() {
                console.log('Executing migration via modal...');
                if (!MigrationService) {
                    console.warn('MigrationService not available');
                    if (window.showToast) {
                        window.showToast('Migration service unavailable', 'error');
                    }
                    return null;
                }

                const execBtn = document.getElementById('executeMigrationBtn');
                if (execBtn) {
                    execBtn.disabled = true;
                    execBtn.textContent = '⏳ Opening modal...';
                }
                setMigrationStatus('info', 'Opening migration modal…', '🔄');

                try {
                    const modalOpened = await MigrationService.triggerManualMigration();
                    if (!modalOpened) {
                        setMigrationStatus('warning', 'No v1 database found', '⚠️');
                        return null;
                    }

                    if (execBtn) {
                        execBtn.textContent = '⏳ Migrating...';
                    }
                    setMigrationStatus('info', 'Migration in progress…', '🔄');

                    const result = await MigrationService.startMigration();
                    const stats = result?.stats || {};

                    if (result?.success) {
                        setMigrationStatus('success', 'Migration completed!', '✅');
                        if (window.showToast) {
                            window.showToast('Migration completed successfully!', 'success');
                        }
                    } else {
                        const errorMessage = stats.error || 'Migration failed';
                        setMigrationStatus('error', `Migration failed: ${errorMessage}`, '❌');
                        if (window.showToast) {
                            window.showToast(`Migration failed: ${errorMessage}`, 'error');
                        }
                    }

                    return result;
                } catch (error) {
                    console.error('Migration error:', error);
                    setMigrationStatus('error', `Error: ${error.message}`, '❌');
                    if (window.showToast) {
                        window.showToast(`Migration error: ${error.message}`, 'error');
                    }
                    return null;
                } finally {
                    if (execBtn) {
                        execBtn.disabled = false;
                        execBtn.textContent = '📦 Execute Migration';
                    }
                }
            };

            window.resetMigration = function() {
                console.log('Resetting migration status...');
                MigrationService.resetMigration();
                const executeBtn = document.getElementById('executeMigrationBtn');
                if (executeBtn) {
                    executeBtn.disabled = true;
                }
                const resultsEl = document.getElementById('migrationResults');
                if (resultsEl) {
                    resultsEl.style.display = 'none';
                }
                const pathEl = document.getElementById('v1DbPath');
                if (pathEl) {
                    pathEl.textContent = '-';
                }
                setMigrationStatus('info', 'Not checked', 'ℹ️');
                updateMigrationStatsList(null);
                window.showToast('Migration flags reset. Reload page to check again.', 'success');
            };

        } else {
            console.warn('MigrationService not found!');
        }

        // Load saved view preference from settings
        try {
            // First check the main settings storage
            const settingsStr = localStorage.getItem('promptManagerSettings');
            if (settingsStr) {
                const settings = JSON.parse(settingsStr);
                if (settings.promptViewMode === 'cards' || settings.promptViewMode === 'table') {
                    currentView = settings.promptViewMode;
                    const viewToggle = el('viewToggle');
                    if (viewToggle) viewToggle.checked = settings.promptViewMode === 'cards';
                }
            }
            // Also check the direct preference (for backwards compatibility)
            const savedView = localStorage.getItem('promptViewMode');
            if (!settingsStr && savedView) {
                if (savedView === 'cards' || savedView === 'table') {
                    currentView = savedView;
                    const viewToggle = el('viewToggle');
                    if (viewToggle) viewToggle.checked = savedView === 'cards';
                }
            }
        } catch (e) {
            console.error('Failed to load view preference:', e);
        }

        // Apply initial view without animation
        const viewTable = el('viewTable');
        const viewCards = el('viewCards');
        if (viewTable) viewTable.classList.toggle('hidden', currentView !== 'table');
        if (viewCards) viewCards.classList.toggle('hidden', currentView !== 'cards');

        render();
    }

    // Start when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

})();
