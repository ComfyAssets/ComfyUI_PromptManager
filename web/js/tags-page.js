/**
 * TagsPageManager - Tag management page with two-panel layout
 * Left: searchable/sortable tag list with counts + infinite scroll
 * Right: prompt cards with image thumbnails for selected tag(s)
 */
class TagsPageManager {
    constructor(admin) {
        this.admin = admin;

        // Tag list state
        this.tags = [];
        this.tagOffset = 0;
        this.tagLimit = 50;
        this.hasMoreTags = false;
        this.tagSearch = '';
        this.tagSort = 'alpha_asc';
        this.isLoadingTags = false;
        this.maxTagCount = 1;
        this.untaggedCount = 0;

        // Selection state
        this.selectedTags = [];
        this.filterMode = 'and';

        // Prompts state
        this.prompts = [];
        this.promptOffset = 0;
        this.promptLimit = 20;
        this.promptTotal = 0;
        this.hasMorePrompts = false;
        this.isLoadingPrompts = false;

        // Observers
        this.tagObserver = null;
        this.promptObserver = null;

        // Debounce timer
        this.searchTimer = null;

        // Context menu state
        this.contextMenuTag = null;

        // Document listener refs for cleanup
        this._onDocClick = null;
        this._onDocKeydown = null;

        this.bindEvents();
        this.initInfiniteScroll();
        this.buildContextMenu();
        this.parseUrlState();
        this.loadTagsList();
    }

    // ── URL State Sync ────────────────────────────────────────

    parseUrlState() {
        const hash = window.location.hash;
        if (!hash.startsWith('#/tags/')) return;

        const rest = hash.substring('#/tags/'.length);
        if (!rest) return;

        // Parse query params from hash (e.g., #/tags/landscape,portrait?mode=or)
        const [tagsPart, queryPart] = rest.split('?');
        const tagNames = tagsPart.split(',').map(t => decodeURIComponent(t.trim())).filter(Boolean);

        if (tagNames.length > 0) {
            this.selectedTags = tagNames;
        }

        if (queryPart) {
            const params = new URLSearchParams(queryPart);
            const mode = params.get('mode');
            if (mode === 'or') this.filterMode = 'or';
        }
    }

    updateUrlHash() {
        if (this.selectedTags.length === 0) {
            if (window.location.hash !== '#/tags') {
                history.replaceState(null, '', '#/tags');
            }
            return;
        }

        const tagsStr = this.selectedTags.map(t => encodeURIComponent(t)).join(',');
        let hash = `#/tags/${tagsStr}`;
        if (this.filterMode === 'or' && this.selectedTags.length > 1) {
            hash += '?mode=or';
        }
        if (window.location.hash !== hash) {
            history.replaceState(null, '', hash);
        }
    }

    // ── Tag List Methods ──────────────────────────────────────

    async loadTagsList() {
        if (this.isLoadingTags) return;
        this.isLoadingTags = true;
        this.tagOffset = 0;
        this.tags = [];

        try {
            const params = new URLSearchParams({
                limit: this.tagLimit,
                offset: 0,
                sort: this.tagSort
            });
            if (this.tagSearch) params.set('search', this.tagSearch);

            const data = await this.fetchJson(`/prompt_manager/tags/stats?${params}`);

            if (data.success) {
                this.tags = data.tags;
                this.hasMoreTags = data.pagination.has_more;
                this.tagOffset = this.tagLimit;
                this.untaggedCount = data.untagged_count || 0;
                this.maxTagCount = Math.max(1, ...this.tags.map(t => t.count));
                this.renderTagsList();

                const badge = document.getElementById('tagsTotalBadge');
                if (badge) badge.textContent = `${data.pagination.total} tags`;

                // If we have pre-selected tags from URL, load prompts
                if (this.selectedTags.length > 0) {
                    this.updateActiveTagPills();
                    this.loadTagPrompts();
                }
            }
        } catch (err) {
            console.error('Failed to load tags:', err);
            const container = document.getElementById('tagsList');
            if (container) {
                container.replaceChildren();
                const errDiv = document.createElement('div');
                errDiv.className = 'text-center py-8 text-pm-error text-sm';
                errDiv.textContent = 'Failed to load tags. Try refreshing.';
                container.appendChild(errDiv);
            }
        } finally {
            this.isLoadingTags = false;
            requestAnimationFrame(() => this.refreshTagObserver());
        }
    }

    async loadMoreTags() {
        if (this.isLoadingTags || !this.hasMoreTags) return;
        this.isLoadingTags = true;

        const spinner = document.querySelector('#tagsLoader .tags-loader-spinner');
        if (spinner) spinner.classList.remove('hidden');

        try {
            const params = new URLSearchParams({
                limit: this.tagLimit,
                offset: this.tagOffset,
                sort: this.tagSort
            });
            if (this.tagSearch) params.set('search', this.tagSearch);

            const data = await this.fetchJson(`/prompt_manager/tags/stats?${params}`);

            if (data.success) {
                const newTags = data.tags;
                this.tags = this.tags.concat(newTags);
                this.hasMoreTags = data.pagination.has_more;
                this.tagOffset += this.tagLimit;
                this.maxTagCount = Math.max(1, ...this.tags.map(t => t.count));
                this.appendTagElements(newTags);
            }
        } catch (err) {
            console.error('Failed to load more tags:', err);
            this.hasMoreTags = false;
        } finally {
            this.isLoadingTags = false;
            if (spinner) spinner.classList.add('hidden');
            requestAnimationFrame(() => this.refreshTagObserver());
        }
    }

    appendTagElements(newTags) {
        const container = document.getElementById('tagsList');
        if (!container || newTags.length === 0) return;

        const fragment = document.createDocumentFragment();
        newTags.forEach(tag => {
            fragment.appendChild(this.createTagElement(tag));
        });
        container.appendChild(fragment);
    }

    renderTagsList() {
        const container = document.getElementById('tagsList');
        if (!container) return;

        const fragment = document.createDocumentFragment();

        // Untagged entry at top
        if (this.untaggedCount > 0) {
            fragment.appendChild(this.createUntaggedElement());
        }

        this.tags.forEach(tag => {
            fragment.appendChild(this.createTagElement(tag));
        });
        container.replaceChildren(fragment);
    }

    createUntaggedElement() {
        const isSelected = this.selectedTags.includes('__untagged__');
        const div = document.createElement('div');
        div.className = `flex items-center px-3 py-2 rounded-pm-md cursor-pointer border transition-all duration-200 relative overflow-hidden ${
            isSelected ? 'bg-pm-warning/20 border-pm-warning/50' : 'border-transparent hover:bg-pm-hover/50'
        }`;

        div.addEventListener('click', () => this.selectTag('__untagged__'));

        const icon = document.createElement('span');
        icon.className = 'mr-3 text-sm';
        icon.textContent = '\u2205';

        const nameSpan = document.createElement('span');
        nameSpan.className = 'flex-1 text-sm text-pm-warning italic truncate';
        nameSpan.textContent = '[untagged]';

        const countSpan = document.createElement('span');
        countSpan.className = 'text-xs text-pm-muted ml-2 font-mono';
        countSpan.textContent = this.untaggedCount;

        div.appendChild(icon);
        div.appendChild(nameSpan);
        div.appendChild(countSpan);

        return div;
    }

    createTagElement(tag) {
        const isSelected = this.selectedTags.includes(tag.name);
        const div = document.createElement('div');
        div.className = `flex items-center px-3 py-2 rounded-pm-md cursor-pointer border transition-all duration-200 relative overflow-hidden group ${
            isSelected ? 'bg-pm-success/20 border-pm-success/50' : 'border-transparent hover:bg-pm-hover/50'
        }`;
        div.dataset.tagName = tag.name;

        // Usage bar (background)
        const barWidth = (tag.count / this.maxTagCount) * 100;
        const bar = document.createElement('div');
        bar.className = 'absolute inset-y-0 left-0 bg-pm-success/8 pointer-events-none transition-all duration-300';
        bar.style.width = barWidth + '%';
        div.appendChild(bar);

        const tagName = tag.name;
        div.addEventListener('click', () => this.selectTag(tagName));
        div.addEventListener('contextmenu', (e) => {
            e.preventDefault();
            this.showContextMenu(e, tagName);
        });

        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.className = 'tag-filter-checkbox mr-3 rounded border-pm bg-pm-surface text-pm-success focus:ring-pm-accent cursor-pointer relative z-10';
        checkbox.checked = isSelected;
        checkbox.dataset.tag = tag.name;
        checkbox.addEventListener('click', (e) => {
            e.stopPropagation();
            this.toggleTagFilter(tagName);
        });

        const nameSpan = document.createElement('span');
        nameSpan.className = 'flex-1 text-sm text-pm truncate relative z-10';
        nameSpan.textContent = tag.name;

        const countSpan = document.createElement('span');
        countSpan.className = 'text-xs text-pm-muted ml-2 font-mono relative z-10';
        countSpan.textContent = tag.count;

        div.appendChild(checkbox);
        div.appendChild(nameSpan);
        div.appendChild(countSpan);

        return div;
    }

    // ── Context Menu ──────────────────────────────────────────

    buildContextMenu() {
        const menu = document.getElementById('tagContextMenu');
        if (!menu) return;

        const items = [
            { label: 'Rename Tag', icon: '\u270F\uFE0F', action: () => this.renameTag(this.contextMenuTag) },
            { label: 'Delete Tag', icon: '\uD83D\uDDD1\uFE0F', action: () => this.deleteTag(this.contextMenuTag) },
            { label: 'Merge Into\u2026', icon: '\uD83D\uDD00', action: () => this.mergeTag(this.contextMenuTag) }
        ];

        const fragment = document.createDocumentFragment();
        items.forEach(item => {
            const btn = document.createElement('button');
            btn.className = 'w-full text-left px-4 py-2 text-sm text-pm hover:bg-pm-hover flex items-center space-x-2';
            const iconSpan = document.createElement('span');
            iconSpan.textContent = item.icon;
            const labelSpan = document.createElement('span');
            labelSpan.textContent = item.label;
            btn.appendChild(iconSpan);
            btn.appendChild(labelSpan);
            btn.addEventListener('click', () => {
                this.hideContextMenu();
                item.action();
            });
            fragment.appendChild(btn);
        });
        menu.replaceChildren(fragment);

        // Close on click outside (store refs for cleanup)
        this._onDocClick = (e) => {
            if (!menu.contains(e.target)) this.hideContextMenu();
        };
        this._onDocKeydown = (e) => {
            if (e.key === 'Escape') this.hideContextMenu();
        };
        document.addEventListener('click', this._onDocClick);
        document.addEventListener('keydown', this._onDocKeydown);
    }

    showContextMenu(e, tagName) {
        const menu = document.getElementById('tagContextMenu');
        if (!menu) return;

        this.contextMenuTag = tagName;

        // Position at mouse, clamped to viewport
        let x = e.clientX;
        let y = e.clientY;
        menu.classList.remove('hidden');

        const rect = menu.getBoundingClientRect();
        if (x + rect.width > window.innerWidth) x = window.innerWidth - rect.width - 8;
        if (y + rect.height > window.innerHeight) y = window.innerHeight - rect.height - 8;

        menu.style.left = x + 'px';
        menu.style.top = y + 'px';
    }

    hideContextMenu() {
        const menu = document.getElementById('tagContextMenu');
        if (menu) menu.classList.add('hidden');
        this.contextMenuTag = null;
    }

    // ── Tag Operations ────────────────────────────────────────

    async renameTag(tagName) {
        if (!tagName) return;
        const newName = prompt(`Rename tag "${tagName}" to:`, tagName);
        if (!newName || newName.trim() === '' || newName.trim() === tagName) return;

        try {
            const data = await this.fetchJson(`/prompt_manager/tags/${encodeURIComponent(tagName)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ new_name: newName.trim() })
            });
            if (data.success) {
                let msg = `Renamed "${tagName}" → "${newName.trim()}" (${data.affected_count} prompts)`;
                if (data.warning) msg += ` — ${data.warning}`;
                this.admin.showNotification(msg, data.warning ? 'warning' : 'success');
                // Update selectedTags if the renamed tag was selected
                const idx = this.selectedTags.indexOf(tagName);
                if (idx >= 0) this.selectedTags[idx] = newName.trim();
                this.updateUrlHash();
                this.loadTagsList();
                if (this.selectedTags.length > 0) this.loadTagPrompts();
            } else {
                this.admin.showNotification(data.error || 'Rename failed', 'error');
            }
        } catch (err) {
            console.error('Rename tag error:', err);
            this.admin.showNotification('Failed to rename tag', 'error');
        }
    }

    async deleteTag(tagName) {
        if (!tagName) return;
        if (!confirm(`Delete tag "${tagName}" from all prompts? This cannot be undone.`)) return;

        try {
            const data = await this.fetchJson(`/prompt_manager/tags/${encodeURIComponent(tagName)}`, {
                method: 'DELETE'
            });
            if (data.success) {
                let msg = `Deleted tag "${tagName}" from ${data.affected_count} prompts`;
                if (data.warning) msg += ` — ${data.warning}`;
                this.admin.showNotification(msg, data.warning ? 'warning' : 'success');
                this.selectedTags = this.selectedTags.filter(t => t !== tagName);
                this.updateUrlHash();
                this.loadTagsList();
                if (this.selectedTags.length > 0) {
                    this.loadTagPrompts();
                } else {
                    this.clearPrompts();
                    this.updateActiveTagPills();
                }
            } else {
                this.admin.showNotification(data.error || 'Delete failed', 'error');
            }
        } catch (err) {
            console.error('Delete tag error:', err);
            this.admin.showNotification('Failed to delete tag', 'error');
        }
    }

    async mergeTag(tagName) {
        if (!tagName) return;
        const targetTag = prompt(`Merge "${tagName}" into which tag?`);
        if (!targetTag || targetTag.trim() === '' || targetTag.trim() === tagName) return;

        try {
            const data = await this.fetchJson('/prompt_manager/tags/merge', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ source_tags: [tagName], target_tag: targetTag.trim() })
            });
            if (data.success) {
                let msg = `Merged "${tagName}" → "${targetTag.trim()}" (${data.affected_count} prompts)`;
                if (data.warning) msg += ` — ${data.warning}`;
                this.admin.showNotification(msg, data.warning ? 'warning' : 'success');
                this.selectedTags = this.selectedTags.filter(t => t !== tagName);
                this.updateUrlHash();
                this.loadTagsList();
                if (this.selectedTags.length > 0) {
                    this.loadTagPrompts();
                } else {
                    this.clearPrompts();
                    this.updateActiveTagPills();
                }
            } else {
                this.admin.showNotification(data.error || 'Merge failed', 'error');
            }
        } catch (err) {
            console.error('Merge tag error:', err);
            this.admin.showNotification('Failed to merge tag', 'error');
        }
    }

    // ── Tag Selection ─────────────────────────────────────────

    selectTag(name) {
        this.selectedTags = [name];
        this.renderTagsList();
        this.updateActiveTagPills();
        this.updateUrlHash();
        this.loadTagPrompts();
    }

    scrollToTag(name) {
        const container = document.getElementById('tagsListContainer');
        if (!container) return;
        const tagEl = container.querySelector(`[data-tag-name="${CSS.escape(name)}"]`);
        if (tagEl) {
            tagEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
    }

    toggleTagFilter(name) {
        const idx = this.selectedTags.indexOf(name);
        if (idx >= 0) {
            this.selectedTags.splice(idx, 1);
        } else {
            // Don't allow mixing untagged with regular tags
            if (name === '__untagged__') {
                this.selectedTags = ['__untagged__'];
            } else {
                this.selectedTags = this.selectedTags.filter(t => t !== '__untagged__');
                this.selectedTags.push(name);
            }
        }

        this.renderTagsList();
        this.updateActiveTagPills();
        this.updateUrlHash();

        if (this.selectedTags.length > 0) {
            this.loadTagPrompts();
        } else {
            this.clearPrompts();
        }
    }

    setFilterMode(mode) {
        this.filterMode = mode;
        const andBtn = document.getElementById('filterModeAnd');
        const orBtn = document.getElementById('filterModeOr');

        if (mode === 'and') {
            andBtn.className = 'px-3 py-1 text-xs font-medium rounded bg-pm-success text-pm';
            orBtn.className = 'px-3 py-1 text-xs font-medium rounded bg-pm-surface text-pm-secondary hover:bg-pm-hover';
        } else {
            orBtn.className = 'px-3 py-1 text-xs font-medium rounded bg-pm-success text-pm';
            andBtn.className = 'px-3 py-1 text-xs font-medium rounded bg-pm-surface text-pm-secondary hover:bg-pm-hover';
        }

        this.updateUrlHash();

        if (this.selectedTags.length > 0) {
            this.loadTagPrompts();
        }
    }

    updateActiveTagPills() {
        const pillsArea = document.getElementById('activeTagsPills');
        const pillsContainer = document.getElementById('tagPillsContainer');
        const filterToggle = document.getElementById('filterModeToggle');
        const emptyState = document.getElementById('tagPromptsEmpty');
        const countBadge = document.getElementById('tagPromptsCountBadge');

        if (this.selectedTags.length === 0) {
            if (pillsArea) pillsArea.classList.add('hidden');
            if (filterToggle) filterToggle.classList.add('hidden');
            if (filterToggle) filterToggle.classList.remove('flex');
            if (emptyState) emptyState.classList.remove('hidden');
            if (countBadge) countBadge.textContent = '';
            return;
        }

        if (emptyState) emptyState.classList.add('hidden');
        if (pillsArea) pillsArea.classList.remove('hidden');

        // Show AND/OR toggle only for multi-tag selection (not untagged)
        const isUntagged = this.selectedTags.includes('__untagged__');
        if (this.selectedTags.length > 1 && !isUntagged) {
            if (filterToggle) {
                filterToggle.classList.remove('hidden');
                filterToggle.classList.add('flex');
            }
        } else {
            if (filterToggle) {
                filterToggle.classList.add('hidden');
                filterToggle.classList.remove('flex');
            }
        }

        // Build pills using safe DOM methods
        if (pillsContainer) {
            const fragment = document.createDocumentFragment();
            this.selectedTags.forEach(tag => {
                const pill = document.createElement('span');
                const isUntaggedPill = tag === '__untagged__';
                pill.className = `inline-flex items-center px-3 py-1 rounded-full text-sm ${
                    isUntaggedPill
                        ? 'bg-pm-warning/20 text-pm-warning border border-pm-warning/30'
                        : 'bg-pm-success/20 text-pm-success border border-pm-success/30'
                }`;

                const text = document.createTextNode(isUntaggedPill ? '[untagged]' : tag);
                pill.appendChild(text);

                const btn = document.createElement('button');
                btn.className = isUntaggedPill ? 'ml-2 text-pm-warning hover:text-pm' : 'ml-2 text-pm-success hover:text-pm';
                btn.textContent = '\u00d7';
                btn.addEventListener('click', () => this.removeTagFromFilter(tag));
                pill.appendChild(btn);

                fragment.appendChild(pill);
            });
            pillsContainer.replaceChildren(fragment);
        }
    }

    removeTagFromFilter(name) {
        this.selectedTags = this.selectedTags.filter(t => t !== name);
        this.renderTagsList();
        this.updateActiveTagPills();
        this.updateUrlHash();

        if (this.selectedTags.length > 0) {
            this.loadTagPrompts();
        } else {
            this.clearPrompts();
        }
    }

    // ── Prompts Display ───────────────────────────────────────

    async loadTagPrompts() {
        if (this.isLoadingPrompts) return;
        if (this.selectedTags.length === 0) return;

        this.isLoadingPrompts = true;
        this.promptOffset = 0;
        this.prompts = [];

        const grid = document.getElementById('tagPromptsGrid');
        if (grid) {
            grid.replaceChildren();
            const spinner = document.createElement('div');
            spinner.className = 'col-span-full text-center py-8';
            const spinEl = document.createElement('div');
            spinEl.className = 'w-8 h-8 border-2 border-pm-success/30 border-t-pm-success rounded-full animate-spin mx-auto';
            spinner.appendChild(spinEl);
            grid.appendChild(spinner);
        }

        try {
            let url;
            const params = new URLSearchParams({
                limit: this.promptLimit,
                offset: 0
            });

            const isUntagged = this.selectedTags.includes('__untagged__');

            if (isUntagged) {
                params.set('untagged', 'true');
                url = `/prompt_manager/tags/filter?${params}`;
            } else if (this.selectedTags.length === 1) {
                url = `/prompt_manager/tags/${encodeURIComponent(this.selectedTags[0])}/prompts?${params}`;
            } else {
                params.set('tags', this.selectedTags.join(','));
                params.set('mode', this.filterMode);
                url = `/prompt_manager/tags/filter?${params}`;
            }

            const data = await this.fetchJson(url);

            if (data.success) {
                this.prompts = data.prompts;
                this.hasMorePrompts = data.pagination.has_more;
                this.promptTotal = data.pagination.total;
                this.promptOffset = this.promptLimit;
                this.renderTagPrompts();
                this.updatePromptCountBadge();
                this.updateLoadMoreButton();
                requestAnimationFrame(() => this.refreshPromptObserver());
            }
        } catch (err) {
            console.error('Failed to load tag prompts:', err);
            if (grid) {
                grid.replaceChildren();
                const errDiv = document.createElement('div');
                errDiv.className = 'col-span-full text-center py-8 text-pm-error';
                errDiv.textContent = 'Failed to load prompts';
                grid.appendChild(errDiv);
            }
        } finally {
            this.isLoadingPrompts = false;
        }
    }

    async loadMoreTagPrompts() {
        if (this.isLoadingPrompts || !this.hasMorePrompts) return;
        this.isLoadingPrompts = true;

        const spinner = document.querySelector('#tagPromptsLoader .prompts-loader-spinner');
        if (spinner) spinner.classList.remove('hidden');

        try {
            const params = new URLSearchParams({
                limit: this.promptLimit,
                offset: this.promptOffset
            });

            let url;
            const isUntagged = this.selectedTags.includes('__untagged__');

            if (isUntagged) {
                params.set('untagged', 'true');
                url = `/prompt_manager/tags/filter?${params}`;
            } else if (this.selectedTags.length === 1) {
                url = `/prompt_manager/tags/${encodeURIComponent(this.selectedTags[0])}/prompts?${params}`;
            } else {
                params.set('tags', this.selectedTags.join(','));
                params.set('mode', this.filterMode);
                url = `/prompt_manager/tags/filter?${params}`;
            }

            const data = await this.fetchJson(url);

            if (data.success) {
                const startIndex = this.prompts.length;
                this.prompts = this.prompts.concat(data.prompts);
                this.hasMorePrompts = data.pagination.has_more;
                this.promptOffset += this.promptLimit;
                this.appendTagPrompts(data.prompts, startIndex);
                this.updatePromptCountBadge();
            }
        } catch (err) {
            console.error('Failed to load more prompts:', err);
            this.hasMorePrompts = false;
        } finally {
            this.isLoadingPrompts = false;
            if (spinner) spinner.classList.add('hidden');
            this.updateLoadMoreButton();
            requestAnimationFrame(() => this.refreshPromptObserver());
        }
    }

    updatePromptCountBadge() {
        const badge = document.getElementById('tagPromptsCountBadge');
        if (badge) {
            if (this.promptTotal > 0) {
                const remaining = this.promptTotal - this.prompts.length;
                badge.textContent = remaining > 0
                    ? `${this.prompts.length} of ${this.promptTotal} prompts — scroll for more`
                    : `${this.promptTotal} prompts`;
            } else {
                badge.textContent = '';
            }
        }
    }

    updateLoadMoreButton() {
        const btn = document.getElementById('loadMorePromptsBtn');
        if (!btn) return;
        if (this.hasMorePrompts) {
            const remaining = this.promptTotal - this.prompts.length;
            btn.textContent = `Load more prompts (${remaining} remaining)`;
            btn.classList.remove('hidden');
        } else {
            btn.classList.add('hidden');
        }
    }

    appendTagPrompts(newPrompts, startIndex) {
        const grid = document.getElementById('tagPromptsGrid');
        if (!grid || newPrompts.length === 0) return;

        const fragment = document.createDocumentFragment();
        newPrompts.forEach((p, i) => {
            const card = this.createPromptCard(p);
            card.style.opacity = '0';
            card.style.transform = 'translateY(8px)';
            card.style.transition = 'opacity 0.3s ease, transform 0.3s ease';
            card.style.transitionDelay = (i % 9) * 30 + 'ms';
            fragment.appendChild(card);
        });
        grid.appendChild(fragment);

        requestAnimationFrame(() => {
            const cards = grid.querySelectorAll('.tag-prompt-card');
            for (let i = startIndex; i < cards.length; i++) {
                cards[i].style.opacity = '1';
                cards[i].style.transform = 'translateY(0)';
            }
        });
    }

    renderTagPrompts() {
        const grid = document.getElementById('tagPromptsGrid');
        if (!grid) return;

        if (this.prompts.length === 0) {
            grid.replaceChildren();
            const emptyDiv = document.createElement('div');
            emptyDiv.className = 'col-span-full text-center py-12 text-pm-muted';
            const icon = document.createElement('span');
            icon.className = 'text-3xl block mb-2';
            icon.textContent = '\uD83D\uDCED';
            emptyDiv.appendChild(icon);
            const msg = document.createElement('span');
            msg.textContent = 'No prompts found with the selected tag(s)';
            emptyDiv.appendChild(msg);
            grid.appendChild(emptyDiv);
            return;
        }

        const fragment = document.createDocumentFragment();
        this.prompts.forEach((p, index) => {
            const card = this.createPromptCard(p);
            // Staggered fade-in
            card.style.opacity = '0';
            card.style.transform = 'translateY(8px)';
            card.style.transition = 'opacity 0.3s ease, transform 0.3s ease';
            card.style.transitionDelay = (index % 9) * 30 + 'ms';
            fragment.appendChild(card);
        });
        grid.replaceChildren(fragment);

        // Trigger animations
        requestAnimationFrame(() => {
            grid.querySelectorAll('.tag-prompt-card').forEach(card => {
                card.style.opacity = '1';
                card.style.transform = 'translateY(0)';
            });
        });
    }

    createPromptCard(prompt) {
        const card = document.createElement('div');
        card.className = 'tag-prompt-card bg-pm-surface/50 rounded-pm-md p-4 border border-pm/50 hover:border-pm-hover transition-colors cursor-pointer';

        // Click card → navigate to dashboard with search
        card.addEventListener('click', () => {
            const fragment = (prompt.text || '').substring(0, 60);
            window.location.hash = '';
            setTimeout(() => {
                const searchInput = document.getElementById('searchText');
                if (searchInput) {
                    searchInput.value = fragment;
                    if (this.admin && typeof this.admin.search === 'function') {
                        this.admin.search();
                    }
                }
            }, 100);
        });

        // Thumbnail row
        if (prompt.images && prompt.images.length > 0) {
            const thumbRow = document.createElement('div');
            thumbRow.className = 'flex gap-2 mb-3';

            prompt.images.forEach((img, imgIndex) => {
                const imgEl = document.createElement('img');
                imgEl.src = this.getThumbnailUrl(img);
                imgEl.alt = '';
                imgEl.loading = 'lazy';
                imgEl.className = 'w-20 h-20 object-cover rounded-pm-md bg-pm-surface cursor-zoom-in hover:ring-2 hover:ring-pm-success/50 transition-all';
                imgEl.onerror = function() {
                    this.onerror = null;
                    this.src = 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 80"><rect fill="#374151" width="80" height="80"/><text x="40" y="44" text-anchor="middle" fill="#6B7280" font-size="20">?</text></svg>');
                };
                // Click thumbnail → ViewerJS
                imgEl.addEventListener('click', (e) => {
                    e.stopPropagation();
                    this.openImageViewer(prompt, imgIndex);
                });
                thumbRow.appendChild(imgEl);
            });

            const moreCount = (prompt.image_count || 0) - prompt.images.length;
            if (moreCount > 0) {
                const moreSpan = document.createElement('span');
                moreSpan.className = 'w-20 h-20 rounded-pm-md bg-pm-surface flex items-center justify-center text-pm-secondary text-sm font-medium cursor-zoom-in hover:bg-pm-hover transition-colors';
                moreSpan.textContent = `+${moreCount}`;
                moreSpan.addEventListener('click', (e) => {
                    e.stopPropagation();
                    this.openImageViewer(prompt, prompt.images.length);
                });
                thumbRow.appendChild(moreSpan);
            }

            card.appendChild(thumbRow);
        }

        // Prompt text (truncated)
        const textP = document.createElement('p');
        textP.className = 'text-sm text-pm leading-relaxed line-clamp-3 mb-3';
        textP.textContent = prompt.text || '';
        card.appendChild(textP);

        // Meta row
        const metaRow = document.createElement('div');
        metaRow.className = 'flex items-center justify-between text-xs text-pm-muted mb-2';

        const metaLeft = document.createElement('div');
        metaLeft.className = 'flex items-center space-x-3';

        if (prompt.category) {
            const catSpan = document.createElement('span');
            catSpan.className = 'px-2 py-0.5 bg-pm-accent/20 text-pm-accent rounded';
            catSpan.textContent = prompt.category;
            metaLeft.appendChild(catSpan);
        }

        if (prompt.created_at) {
            const dateSpan = document.createElement('span');
            dateSpan.textContent = new Date(prompt.created_at).toLocaleDateString();
            metaLeft.appendChild(dateSpan);
        }

        // Rating stars
        const rating = prompt.rating || 0;
        const starsSpan = document.createElement('span');
        starsSpan.className = 'flex';
        for (let i = 1; i <= 5; i++) {
            const star = document.createElement('span');
            star.className = i <= rating ? 'text-pm-warning' : 'text-pm-muted';
            star.textContent = '\u2605';
            starsSpan.appendChild(star);
        }
        metaLeft.appendChild(starsSpan);

        metaRow.appendChild(metaLeft);

        if (prompt.image_count) {
            const imgCount = document.createElement('span');
            imgCount.className = 'text-pm-muted';
            imgCount.textContent = `${prompt.image_count} img${prompt.image_count !== 1 ? 's' : ''}`;
            metaRow.appendChild(imgCount);
        }

        card.appendChild(metaRow);

        // Tags (clickable — selects tag on left panel)
        if (prompt.tags && prompt.tags.length > 0) {
            const tagsDiv = document.createElement('div');
            tagsDiv.className = 'flex flex-wrap gap-1 mt-2';
            prompt.tags.forEach(tagName => {
                const tagSpan = document.createElement('span');
                const isActive = this.selectedTags.includes(tagName);
                tagSpan.className = `px-2 py-0.5 rounded text-xs cursor-pointer transition-colors ${
                    isActive
                        ? 'bg-pm-success text-pm hover:bg-pm-success/80'
                        : 'bg-pm-surface text-pm-secondary hover:bg-pm-success/40 hover:text-pm-success'
                }`;
                tagSpan.textContent = tagName;
                tagSpan.addEventListener('click', (e) => {
                    e.stopPropagation();
                    this.toggleTagFilter(tagName);
                });
                tagsDiv.appendChild(tagSpan);
            });
            card.appendChild(tagsDiv);
        }

        return card;
    }

    // ── Image Viewer ──────────────────────────────────────────

    async openImageViewer(prompt, startIndex) {
        // Load all images for this prompt
        let images = prompt.images || [];

        // If there might be more images, load them all
        if (prompt.image_count > images.length) {
            try {
                const data = await this.fetchJson(`/prompt_manager/prompts/${prompt.id}/images`);
                if (data.success && data.images) {
                    images = data.images;
                }
            } catch (err) {
                console.error('Failed to load all images:', err);
                this.admin.showNotification(
                    `Showing ${images.length} of ${prompt.image_count} images — full load failed`,
                    'warning'
                );
            }
        }

        if (images.length === 0) {
            this.admin.showNotification('No images found for this prompt', 'warning');
            return;
        }

        // Create temp container for ViewerJS
        const container = document.createElement('div');
        container.style.display = 'none';

        images.forEach((image, index) => {
            const imageUrl = this.admin.getImageUrl(image);
            if (!imageUrl) return;

            const img = document.createElement('img');
            img.src = imageUrl;
            img.alt = image.filename || `Image ${index + 1}`;
            container.appendChild(img);
        });

        document.body.appendChild(container);

        const viewer = new Viewer(container, {
            inline: false,
            navbar: true,
            toolbar: {
                zoomIn: 1,
                zoomOut: 1,
                oneToOne: 1,
                reset: 1,
                prev: 1,
                play: false,
                next: 1,
                rotateLeft: 1,
                rotateRight: 1,
                flipHorizontal: 1,
                flipVertical: 1
            },
            title: [1, (image) => image.alt || 'Image'],
            hidden: () => {
                viewer.destroy();
                container.remove();
            },
            initialViewIndex: Math.min(startIndex, images.length - 1)
        });

        viewer.show();
    }

    clearPrompts() {
        this.prompts = [];
        this.promptTotal = 0;
        this.hasMorePrompts = false;
        const grid = document.getElementById('tagPromptsGrid');
        if (grid) grid.replaceChildren();
        this.updateLoadMoreButton();
        const emptyState = document.getElementById('tagPromptsEmpty');
        if (emptyState) emptyState.classList.remove('hidden');
        this.updatePromptCountBadge();
    }

    // ── Infinite Scroll ───────────────────────────────────────

    initInfiniteScroll() {
        // Tag list observer
        const tagsLoader = document.getElementById('tagsLoader');
        if (tagsLoader) {
            this.tagObserver = new IntersectionObserver((entries) => {
                if (entries[0].isIntersecting && this.hasMoreTags && !this.isLoadingTags) {
                    this.loadMoreTags();
                }
            }, { root: document.getElementById('tagsListContainer'), threshold: 0.1 });
            this.tagObserver.observe(tagsLoader);
        }

        // Prompts grid observer
        const promptsLoader = document.getElementById('tagPromptsLoader');
        if (promptsLoader) {
            this.promptObserver = new IntersectionObserver((entries) => {
                if (entries[0].isIntersecting && this.hasMorePrompts && !this.isLoadingPrompts) {
                    this.loadMoreTagPrompts();
                }
            }, { root: document.getElementById('tagPromptsContainer'), threshold: 0.1 });
            this.promptObserver.observe(promptsLoader);
        }
    }

    refreshPromptObserver() {
        if (!this.promptObserver || !this.hasMorePrompts) return;
        const loader = document.getElementById('tagPromptsLoader');
        if (loader) {
            this.promptObserver.unobserve(loader);
            this.promptObserver.observe(loader);
        }
    }

    refreshTagObserver() {
        if (!this.tagObserver || !this.hasMoreTags) return;
        const loader = document.getElementById('tagsLoader');
        if (loader) {
            this.tagObserver.unobserve(loader);
            this.tagObserver.observe(loader);
        }
    }

    destroyInfiniteScroll() {
        if (this.tagObserver) {
            this.tagObserver.disconnect();
            this.tagObserver = null;
        }
        if (this.promptObserver) {
            this.promptObserver.disconnect();
            this.promptObserver = null;
        }
    }

    // ── Events ────────────────────────────────────────────────

    bindEvents() {
        // Tag search (debounced)
        const searchInput = document.getElementById('tagSearchInput');
        if (searchInput) {
            searchInput.addEventListener('input', () => {
                clearTimeout(this.searchTimer);
                this.searchTimer = setTimeout(() => {
                    this.tagSearch = searchInput.value.trim();
                    this.loadTagsList();
                }, 300);
            });
        }

        // Tag sort
        const sortSelect = document.getElementById('tagSortSelect');
        if (sortSelect) {
            sortSelect.addEventListener('change', () => {
                this.tagSort = sortSelect.value;
                this.loadTagsList();
            });
        }

        // Filter mode buttons
        const andBtn = document.getElementById('filterModeAnd');
        const orBtn = document.getElementById('filterModeOr');
        if (andBtn) andBtn.addEventListener('click', () => this.setFilterMode('and'));
        if (orBtn) orBtn.addEventListener('click', () => this.setFilterMode('or'));

        // Load More button (fallback for infinite scroll)
        const loadMoreBtn = document.getElementById('loadMorePromptsBtn');
        if (loadMoreBtn) {
            loadMoreBtn.addEventListener('click', () => this.loadMoreTagPrompts());
        }
    }

    destroy() {
        this.destroyInfiniteScroll();
        this.hideContextMenu();
        clearTimeout(this.searchTimer);
        if (this._onDocClick) {
            document.removeEventListener('click', this._onDocClick);
            this._onDocClick = null;
        }
        if (this._onDocKeydown) {
            document.removeEventListener('keydown', this._onDocKeydown);
            this._onDocKeydown = null;
        }
    }

    // ── Helpers ───────────────────────────────────────────────

    async fetchJson(url, options = {}) {
        const resp = await fetch(url, options);
        if (!resp.ok) {
            let errorMsg;
            try { errorMsg = (await resp.json()).error; } catch { errorMsg = resp.statusText; }
            throw new Error(`Server error ${resp.status}: ${errorMsg}`);
        }
        return resp.json();
    }

    getThumbnailUrl(image) {
        if (image.thumbnail_url) return image.thumbnail_url;
        if (image.url) return image.url;
        if (image.id) return `/prompt_manager/images/${image.id}/file`;
        return '';
    }
}

// Attach to window for global access
if (typeof window !== 'undefined') {
    window.TagsPageManager = TagsPageManager;
}
