/**
 * MetadataViewer Component
 * 
 * Professional JSON tree viewer with collapsible nodes, syntax highlighting,
 * search, and export capabilities. Perfect for ComfyUI workflow metadata.
 */

class MetadataViewer {
  constructor(container, options = {}) {
    this.container = typeof container === 'string' ? document.getElementById(container) : container;
    this.options = {
      collapsible: true,
      showLineNumbers: true,
      showDataTypes: true,
      maxDepth: 10,
      searchable: true,
      copyable: true,
      exportable: true,
      theme: 'dark',
      indentSize: 2,
      maxStringLength: 100,
      showArrayIndices: true,
      highlightSearch: true,
      ...options
    };

    // State
    this.data = null;
    this.searchTerm = '';
    this.searchResults = [];
    this.currentSearchIndex = -1;
    this.collapsedNodes = new Set();
    this.selectedPath = null;
    this.viewMode = 'tree'; // tree, raw, formatted

    // Initialize
    this.init();
  }

  /**
   * Initialize metadata viewer
   */
  init() {
    this.createStructure();
    this.bindEvents();
  }

  /**
   * Create component HTML structure
   */
  createStructure() {
    this.container.className = 'metadata-viewer';
    this.container.innerHTML = `
      <div class="metadata-header">
        <div class="header-left">
          <div class="view-mode-controls">
            <button class="mode-btn active" data-mode="tree" title="Tree view">
              <svg class="icon"><use href="#icon-tree"></use></svg>
              Tree
            </button>
            <button class="mode-btn" data-mode="raw" title="Raw JSON">
              <svg class="icon"><use href="#icon-code"></use></svg>
              Raw
            </button>
            <button class="mode-btn" data-mode="formatted" title="Formatted JSON">
              <svg class="icon"><use href="#icon-format"></use></svg>
              Pretty
            </button>
          </div>
        </div>

        <div class="header-center">
          <div class="search-controls" style="display: none;">
            <div class="search-input-wrapper">
              <input type="text" 
                     class="search-input" 
                     placeholder="Search keys, values, or paths..."
                     autocomplete="off">
              <button class="search-clear" title="Clear search">×</button>
            </div>
            
            <div class="search-navigation">
              <button class="search-nav-btn" data-action="searchPrevious" title="Previous match">
                <svg class="icon"><use href="#icon-chevron-up"></use></svg>
              </button>
              <span class="search-results-info">0/0</span>
              <button class="search-nav-btn" data-action="searchNext" title="Next match">
                <svg class="icon"><use href="#icon-chevron-down"></use></svg>
              </button>
            </div>
          </div>
        </div>

        <div class="header-right">
          <button class="header-btn" data-action="toggleSearch" title="Search (Ctrl+F)">
            <svg class="icon"><use href="#icon-search"></use></svg>
          </button>
          <button class="header-btn" data-action="expandAll" title="Expand all">
            <svg class="icon"><use href="#icon-expand-all"></use></svg>
          </button>
          <button class="header-btn" data-action="collapseAll" title="Collapse all">
            <svg class="icon"><use href="#icon-collapse-all"></use></svg>
          </button>
          <button class="header-btn" data-action="copy" title="Copy to clipboard">
            <svg class="icon"><use href="#icon-copy"></use></svg>
          </button>
          <button class="header-btn" data-action="export" title="Export JSON">
            <svg class="icon"><use href="#icon-download"></use></svg>
          </button>
          <button class="header-btn" data-action="settings" title="Settings">
            <svg class="icon"><use href="#icon-settings"></use></svg>
          </button>
        </div>
      </div>

      <div class="metadata-content">
        <div class="tree-view">
          <div class="summary-container" style="display: none;"></div>
          <div class="tree-container">
            <!-- Tree nodes will be rendered here -->
          </div>
        </div>

        <div class="raw-view" style="display: none;">
          <div class="code-editor">
            <textarea class="raw-editor" readonly spellcheck="false"></textarea>
          </div>
        </div>

        <div class="formatted-view" style="display: none;">
          <div class="formatted-container">
            <pre class="formatted-code"><code></code></pre>
          </div>
        </div>
      </div>

      <div class="metadata-footer">
        <div class="footer-left">
          <span class="object-info">No data</span>
          <span class="selected-path" style="display: none;"></span>
        </div>

        <div class="footer-center">
          <div class="breadcrumb-trail" style="display: none;">
            <!-- Breadcrumbs will be shown here -->
          </div>
        </div>

        <div class="footer-right">
          <span class="data-size">0 bytes</span>
          <span class="node-count">0 nodes</span>
        </div>
      </div>

      <!-- Context Menu -->
      <div class="context-menu" style="display: none;">
        <div class="menu-item" data-action="copyKey">Copy Key</div>
        <div class="menu-item" data-action="copyValue">Copy Value</div>
        <div class="menu-item" data-action="copyPath">Copy Path</div>
        <div class="menu-separator"></div>
        <div class="menu-item" data-action="expandNode">Expand Node</div>
        <div class="menu-item" data-action="collapseNode">Collapse Node</div>
        <div class="menu-separator"></div>
        <div class="menu-item" data-action="showType">Show Type Info</div>
      </div>

      <!-- Settings Modal -->
      <div class="settings-modal" style="display: none;">
        <div class="modal-backdrop"></div>
        <div class="modal-content">
          <div class="modal-header">
            <h3>Metadata Viewer Settings</h3>
            <button class="modal-close" data-action="closeSettings">×</button>
          </div>
          
          <div class="modal-body">
            <div class="settings-group">
              <h4>Display Options</h4>
              <label class="setting-item">
                <input type="checkbox" class="setting-checkbox" data-setting="showLineNumbers" checked>
                <span>Show line numbers</span>
              </label>
              <label class="setting-item">
                <input type="checkbox" class="setting-checkbox" data-setting="showDataTypes" checked>
                <span>Show data types</span>
              </label>
              <label class="setting-item">
                <input type="checkbox" class="setting-checkbox" data-setting="showArrayIndices" checked>
                <span>Show array indices</span>
              </label>
            </div>
            
            <div class="settings-group">
              <h4>Behavior</h4>
              <label class="setting-item">
                <span>Max string length:</span>
                <input type="number" class="setting-input" data-setting="maxStringLength" value="100" min="20" max="1000">
              </label>
              <label class="setting-item">
                <span>Indent size:</span>
                <input type="number" class="setting-input" data-setting="indentSize" value="2" min="1" max="8">
              </label>
              <label class="setting-item">
                <span>Max depth:</span>
                <input type="number" class="setting-input" data-setting="maxDepth" value="10" min="1" max="50">
              </label>
            </div>
          </div>
          
          <div class="modal-footer">
            <button class="modal-btn secondary" data-action="resetSettings">Reset</button>
            <button class="modal-btn primary" data-action="applySettings">Apply</button>
          </div>
        </div>
      </div>
    `;

    // Cache DOM elements
    this.elements = {
      modeButtons: this.container.querySelectorAll('.mode-btn'),
      searchControls: this.container.querySelector('.search-controls'),
      searchInput: this.container.querySelector('.search-input'),
      searchResults: this.container.querySelector('.search-results-info'),
      treeView: this.container.querySelector('.tree-view'),
      summaryContainer: this.container.querySelector('.summary-container'),
      treeContainer: this.container.querySelector('.tree-container'),
      rawView: this.container.querySelector('.raw-view'),
      rawEditor: this.container.querySelector('.raw-editor'),
      formattedView: this.container.querySelector('.formatted-view'),
      formattedCode: this.container.querySelector('.formatted-code code'),
      objectInfo: this.container.querySelector('.object-info'),
      selectedPath: this.container.querySelector('.selected-path'),
      breadcrumbTrail: this.container.querySelector('.breadcrumb-trail'),
      dataSize: this.container.querySelector('.data-size'),
      nodeCount: this.container.querySelector('.node-count'),
      contextMenu: this.container.querySelector('.context-menu'),
      settingsModal: this.container.querySelector('.settings-modal')
    };
  }

  /**
   * Bind event listeners
   */
  bindEvents() {
    // Header actions
    this.container.addEventListener('click', (e) => {
      const action = e.target.dataset.action;
      if (action) {
        e.preventDefault();
        this.handleAction(action, e);
      }
    });

    // View mode switching
    this.elements.modeButtons.forEach(btn => {
      btn.addEventListener('click', (e) => {
        const mode = e.target.dataset.mode;
        if (mode) {
          this.setViewMode(mode);
        }
      });
    });

    // Search input
    this.elements.searchInput.addEventListener('input', (e) => {
      this.performSearch(e.target.value);
    });

    this.elements.searchInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        if (e.shiftKey) {
          this.searchPrevious();
        } else {
          this.searchNext();
        }
      } else if (e.key === 'Escape') {
        this.hideSearch();
      }
    });

    // Tree node interactions
    this.elements.treeContainer.addEventListener('click', (e) => {
      this.handleTreeClick(e);
    });

    // Context menu
    this.elements.treeContainer.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      this.showContextMenu(e);
    });

    // Close context menu on outside click
    document.addEventListener('click', () => {
      this.hideContextMenu();
    });

    // Settings modal
    this.elements.settingsModal.addEventListener('click', (e) => {
      if (e.target.classList.contains('modal-backdrop') || 
          e.target.classList.contains('modal-close')) {
        this.hideSettings();
      }
    });

    // Keyboard shortcuts
    document.addEventListener('keydown', (e) => {
      if (this.container.contains(document.activeElement) || 
          this.container.querySelector('.search-input') === document.activeElement) {
        this.handleKeyboardShortcuts(e);
      }
    });
  }

  /**
   * Load and display metadata
   */
  loadData(data, title = 'Metadata') {
    this.data = data;
    this.searchTerm = '';
    this.searchResults = [];
    this.currentSearchIndex = -1;
    this.collapsedNodes.clear();
    this.selectedPath = null;

    // Update info
    this.updateDataInfo();

    // Render based on current view mode
    this.render();

    // Emit event
    events.emit('metadata:loaded', { data, title });
  }

  /**
   * Set view mode
   */
  setViewMode(mode) {
    if (this.viewMode === mode) return;

    this.viewMode = mode;

    // Update button states
    this.elements.modeButtons.forEach(btn => {
      btn.classList.toggle('active', btn.dataset.mode === mode);
    });

    // Show/hide views
    this.elements.treeView.style.display = mode === 'tree' ? 'block' : 'none';
    this.elements.rawView.style.display = mode === 'raw' ? 'block' : 'none';
    this.elements.formattedView.style.display = mode === 'formatted' ? 'block' : 'none';

    // Render content
    this.render();
  }

  /**
   * Render content based on current view mode
   */
  render() {
    if (!this.data) {
      this.renderEmpty();
      return;
    }

    this.renderSummary();

    switch (this.viewMode) {
      case 'tree':
        this.renderTree();
        break;
      case 'raw':
        this.renderRaw();
        break;
      case 'formatted':
        this.renderFormatted();
        break;
    }
  }

  /**
   * Render empty state
  */
  renderEmpty() {
    if (this.elements.summaryContainer) {
      this.elements.summaryContainer.style.display = 'none';
      this.elements.summaryContainer.innerHTML = '';
    }

    this.elements.treeContainer.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon">
          <svg class="icon"><use href="#icon-file-code"></use></svg>
        </div>
        <h3>No Metadata</h3>
        <p>No metadata to display. Select an image or prompt to view its metadata.</p>
      </div>
    `;
  }

  /**
   * Render tree view
   */
  renderTree() {
    const fragment = document.createDocumentFragment();
    const rootNode = this.createTreeNode(this.data, '', 0);
    fragment.appendChild(rootNode);
    
    this.elements.treeContainer.innerHTML = '';
    this.elements.treeContainer.appendChild(fragment);
    
    // Apply search highlighting if active
    if (this.searchTerm) {
      this.highlightSearchResults();
    }
  }

  /**
   * Render compact metadata summary
   */
  renderSummary() {
    const container = this.elements.summaryContainer;
    if (!container) return;

    if (!this.data || this.viewMode !== 'tree') {
      container.style.display = 'none';
      container.innerHTML = '';
      return;
    }

    const summary = this.getSummaryData();
    if (!summary) {
      container.style.display = 'none';
      container.innerHTML = '';
      return;
    }

    const rows = [
      ['Prompt - positive', summary.positivePrompt],
      ['Prompt - negative', summary.negativePrompt],
      ['Model', summary.model],
      ['Lora(s)', summary.loras],
      ['cfgScale', summary.cfgScale],
      ['steps', summary.steps],
      ['sampler', summary.sampler],
      ['seed', summary.seed],
      ['clipSkip', summary.clipSkip],
      ['Workflow', summary.workflow]
    ];

    const html = rows.map(([label, value]) => {
      const formattedValue = this.formatSummaryValue(value);
      return `
        <div class="summary-item">
          <span class="summary-label">${this.escapeHtml(label)}</span>
          <span class="summary-value">${formattedValue}</span>
        </div>
      `;
    }).join('');

    container.innerHTML = `
      <h3 class="summary-title">Generation Metadata</h3>
      <div class="summary-grid">
        ${html}
      </div>
    `;
    container.style.display = 'block';
  }

  /**
   * Extract summary data from metadata payload
   */
  getSummaryData() {
    const metadata = this.data;
    if (!metadata || typeof metadata !== 'object') return null;

    const custom = metadata.custom && Object.keys(metadata.custom).length ? metadata.custom : null;
    const comfy = metadata.comfy && Object.keys(metadata.comfy).length ? metadata.comfy : null;
    const source = custom && (custom.positivePrompt || custom.prompt || custom.model || custom.workflow)
      ? custom
      : comfy;

    if (!source) return null;

    const fallback = (value, alt = '') => {
      if (value === undefined || value === null || value === '') return alt;
      return value;
    };

    const loras = source.loras ?? source.lora ?? (custom ? custom.loras : undefined);
    const workflowInfo = source.workflowSummary ?? custom?.workflow ?? comfy?.workflowSummary ?? comfy?.workflow;

    const summary = {
      positivePrompt: fallback(source.positivePrompt ?? source.prompt, 'None'),
      negativePrompt: fallback(source.negativePrompt ?? source.negative_prompt, 'None'),
      model: fallback(source.model ?? source.checkpoint ?? source.model_name, 'None'),
      loras: fallback(loras, 'None'),
      cfgScale: fallback(source.cfgScale ?? source.cfg_scale ?? source.cfg, 'None'),
      steps: fallback(source.steps ?? source.num_steps, 'None'),
      sampler: fallback(source.sampler ?? source.sampler_name ?? source.scheduler, 'None'),
      seed: fallback(source.seed ?? source.noise_seed, 'None'),
      clipSkip: fallback(source.clipSkip ?? source.clip_skip, 'None'),
      workflow: fallback(workflowInfo, 'Not embedded')
    };

    return summary;
  }

  /**
   * Normalize summary value for display
   */
  formatSummaryValue(value) {
    const normalized = this.normalizeSummaryValue(value);
    return this.escapeHtml(normalized).replace(/\n/g, '<br>');
  }

  /**
   * Convert summary data to human-readable string
   */
  normalizeSummaryValue(value) {
    if (value === undefined || value === null) return 'None';

    if (typeof value === 'string') {
      const trimmed = value.trim();
      return trimmed ? trimmed : 'None';
    }

    if (typeof value === 'number' || typeof value === 'boolean') {
      return String(value);
    }

    if (Array.isArray(value)) {
      const parts = value
        .map(item => this.normalizeSummaryValue(item))
        .filter(Boolean);
      return parts.length ? parts.join(', ') : 'None';
    }

    if (typeof value === 'object') {
      if (value.name) {
        const strengths = [];
        if (value.strengthModel !== undefined) {
          strengths.push(`model ${value.strengthModel}`);
        }
        if (value.strengthClip !== undefined) {
          strengths.push(`clip ${value.strengthClip}`);
        }
        return strengths.length ? `${value.name} (${strengths.join(', ')})` : value.name;
      }

      if (value.text) {
        return this.normalizeSummaryValue(value.text);
      }

      if (value.workflowSummary) {
        return this.normalizeSummaryValue(value.workflowSummary);
      }

      return 'Embedded data';
    }

    return 'None';
  }

  /**
   * Create tree node element
   */
  createTreeNode(value, key, depth, path = '') {
    const container = document.createElement('div');
    container.className = 'tree-node';
    container.dataset.path = path;
    container.dataset.depth = depth;

    if (depth > this.options.maxDepth) {
      container.innerHTML = `
        <div class="tree-line">
          <span class="tree-key">${this.escapeHtml(key)}</span>
          <span class="tree-colon">:</span>
          <span class="tree-value truncated">[Max depth reached]</span>
        </div>
      `;
      return container;
    }

    const type = this.getDataType(value);
    const isCollapsible = this.isCollapsible(value);
    const isCollapsed = this.collapsedNodes.has(path);
    
    const line = document.createElement('div');
    line.className = 'tree-line';
    line.style.paddingLeft = `${depth * this.options.indentSize * 8}px`;

    let html = '';

    // Collapse/expand toggle
    if (isCollapsible) {
      html += `<button class="tree-toggle ${isCollapsed ? 'collapsed' : ''}" 
                       data-action="toggleNode" 
                       data-path="${path}">
                 <svg class="icon"><use href="#icon-chevron-${isCollapsed ? 'right' : 'down'}"></use></svg>
               </button>`;
    } else {
      html += '<span class="tree-spacer"></span>';
    }

    // Line numbers
    if (this.options.showLineNumbers && depth === 0) {
      html += `<span class="line-number">${this.getLineNumber(path)}</span>`;
    }

    // Key
    if (key !== '') {
      html += `<span class="tree-key" data-key="${this.escapeHtml(key)}">${this.escapeHtml(key)}</span>`;
      html += '<span class="tree-colon">:</span>';
    }

    // Value
    html += this.renderValue(value, type, path, isCollapsed);

    // Data type indicator
    if (this.options.showDataTypes && type !== 'string') {
      html += `<span class="tree-type">${type}</span>`;
    }

    line.innerHTML = html;
    container.appendChild(line);

    // Children (if not collapsed and has children)
    if (isCollapsible && !isCollapsed) {
      const children = this.getChildren(value);
      children.forEach(({ key: childKey, value: childValue, index }) => {
        const childPath = path ? `${path}.${childKey}` : childKey;
        const displayKey = this.options.showArrayIndices && Array.isArray(value) ? 
          `[${index}]` : childKey;
        
        const childNode = this.createTreeNode(childValue, displayKey, depth + 1, childPath);
        container.appendChild(childNode);
      });
    }

    return container;
  }

  /**
   * Render value based on type
   */
  renderValue(value, type, path, isCollapsed) {
    switch (type) {
      case 'null':
        return '<span class="tree-value null">null</span>';
      
      case 'undefined':
        return '<span class="tree-value undefined">undefined</span>';
      
      case 'boolean':
        return `<span class="tree-value boolean">${value}</span>`;
      
      case 'number':
        return `<span class="tree-value number">${this.formatNumber(value)}</span>`;
      
      case 'string':
        return this.renderString(value);
      
      case 'array':
        const arrayLength = value.length;
        if (isCollapsed) {
          return `<span class="tree-value array-collapsed">[${arrayLength} ${arrayLength === 1 ? 'item' : 'items'}]</span>`;
        }
        return `<span class="tree-bracket array-open">[</span>`;
      
      case 'object':
        const objectKeys = Object.keys(value);
        if (isCollapsed) {
          return `<span class="tree-value object-collapsed">{${objectKeys.length} ${objectKeys.length === 1 ? 'property' : 'properties'}}</span>`;
        }
        return `<span class="tree-bracket object-open">{</span>`;
      
      default:
        return `<span class="tree-value unknown">${this.escapeHtml(String(value))}</span>`;
    }
  }

  /**
   * Render string value with proper escaping and truncation
   */
  renderString(value) {
    const escaped = this.escapeHtml(value);
    let displayValue = escaped;
    
    if (escaped.length > this.options.maxStringLength) {
      displayValue = escaped.substring(0, this.options.maxStringLength) + '…';
    }
    
    return `<span class="tree-value string" title="${escaped}">"${displayValue}"</span>`;
  }

  /**
   * Get children of a value for tree rendering
   */
  getChildren(value) {
    if (Array.isArray(value)) {
      return value.map((item, index) => ({
        key: String(index),
        value: item,
        index
      }));
    } else if (value && typeof value === 'object') {
      return Object.entries(value).map(([key, val], index) => ({
        key,
        value: val,
        index
      }));
    }
    return [];
  }

  /**
   * Render raw JSON view
   */
  renderRaw() {
    this.elements.rawEditor.value = JSON.stringify(this.data, null, 0);
  }

  /**
   * Render formatted JSON view
   */
  renderFormatted() {
    const formatted = JSON.stringify(this.data, null, this.options.indentSize);
    this.elements.formattedCode.textContent = formatted;
    
    // Apply syntax highlighting if available
    if (window.hljs) {
      window.hljs.highlightElement(this.elements.formattedCode);
    }
  }

  /**
   * Handle tree node clicks
   */
  handleTreeClick(e) {
    const action = e.target.dataset.action;
    const path = e.target.dataset.path;
    
    if (action === 'toggleNode' && path !== undefined) {
      this.toggleNode(path);
      return;
    }

    // Select node
    const treeLine = e.target.closest('.tree-line');
    if (treeLine) {
      const nodePath = treeLine.parentElement.dataset.path;
      this.selectNode(nodePath);
    }
  }

  /**
   * Toggle node collapse/expand
   */
  toggleNode(path) {
    if (this.collapsedNodes.has(path)) {
      this.collapsedNodes.delete(path);
    } else {
      this.collapsedNodes.add(path);
    }
    
    this.render();
  }

  /**
   * Select node and update UI
   */
  selectNode(path) {
    // Remove previous selection
    const previousSelected = this.container.querySelector('.tree-line.selected');
    if (previousSelected) {
      previousSelected.classList.remove('selected');
    }

    // Add selection to new node
    const selectedNode = this.container.querySelector(`[data-path="${path}"] .tree-line`);
    if (selectedNode) {
      selectedNode.classList.add('selected');
    }

    this.selectedPath = path;
    this.updateSelectedInfo();
    this.updateBreadcrumb();
  }

  /**
   * Perform search
   */
  performSearch(term) {
    this.searchTerm = term.toLowerCase();
    this.searchResults = [];
    this.currentSearchIndex = -1;

    if (!term.trim()) {
      this.clearSearchHighlights();
      this.updateSearchInfo();
      return;
    }

    // Search through data
    this.searchInData(this.data, '', this.searchTerm);
    
    // Highlight results
    this.highlightSearchResults();
    
    // Update search info
    this.updateSearchInfo();
    
    // Navigate to first result
    if (this.searchResults.length > 0) {
      this.currentSearchIndex = 0;
      this.scrollToSearchResult(this.searchResults[0]);
    }
  }

  /**
   * Search recursively through data
   */
  searchInData(value, path, term) {
    const type = this.getDataType(value);
    
    // Check if current value matches
    const valueString = String(value).toLowerCase();
    if (valueString.includes(term) || path.toLowerCase().includes(term)) {
      this.searchResults.push({
        path,
        value,
        type,
        matchType: path.toLowerCase().includes(term) ? 'key' : 'value'
      });
    }

    // Recursively search children
    if (type === 'object' || type === 'array') {
      const children = this.getChildren(value);
      children.forEach(({ key, value: childValue }) => {
        const childPath = path ? `${path}.${key}` : key;
        this.searchInData(childValue, childPath, term);
      });
    }
  }

  /**
   * Navigate to next search result
   */
  searchNext() {
    if (this.searchResults.length === 0) return;
    
    this.currentSearchIndex = (this.currentSearchIndex + 1) % this.searchResults.length;
    this.scrollToSearchResult(this.searchResults[this.currentSearchIndex]);
    this.updateSearchInfo();
  }

  /**
   * Navigate to previous search result
   */
  searchPrevious() {
    if (this.searchResults.length === 0) return;
    
    this.currentSearchIndex = this.currentSearchIndex <= 0 ? 
      this.searchResults.length - 1 : this.currentSearchIndex - 1;
    this.scrollToSearchResult(this.searchResults[this.currentSearchIndex]);
    this.updateSearchInfo();
  }

  /**
   * Scroll to search result
   */
  scrollToSearchResult(result) {
    // Expand nodes in path to make result visible
    const pathParts = result.path.split('.');
    let currentPath = '';
    pathParts.forEach(part => {
      if (currentPath) currentPath += '.';
      currentPath += part;
      this.collapsedNodes.delete(currentPath);
    });

    // Re-render to show expanded nodes
    this.render();
    this.highlightSearchResults();

    // Scroll to result
    const element = this.container.querySelector(`[data-path="${result.path}"]`);
    if (element) {
      element.scrollIntoView({ behavior: 'smooth', block: 'center' });
      this.selectNode(result.path);
    }
  }

  /**
   * Highlight search results in tree
   */
  highlightSearchResults() {
    if (!this.searchTerm || this.viewMode !== 'tree') return;

    this.clearSearchHighlights();

    this.searchResults.forEach((result, index) => {
      const element = this.container.querySelector(`[data-path="${result.path}"]`);
      if (element) {
        element.classList.add('search-match');
        if (index === this.currentSearchIndex) {
          element.classList.add('search-current');
        }
      }
    });
  }

  /**
   * Clear search highlights
   */
  clearSearchHighlights() {
    this.container.querySelectorAll('.search-match').forEach(el => {
      el.classList.remove('search-match', 'search-current');
    });
  }

  /**
   * Update search info display
   */
  updateSearchInfo() {
    const current = this.searchResults.length > 0 ? this.currentSearchIndex + 1 : 0;
    const total = this.searchResults.length;
    this.elements.searchResults.textContent = `${current}/${total}`;
  }

  /**
   * Show/hide search controls
   */
  showSearch() {
    this.elements.searchControls.style.display = 'flex';
    this.elements.searchInput.focus();
  }

  hideSearch() {
    this.elements.searchControls.style.display = 'none';
    this.elements.searchInput.value = '';
    this.performSearch('');
  }

  /**
   * Handle various actions
   */
  async handleAction(action, event) {
    switch (action) {
      case 'toggleSearch':
        if (this.elements.searchControls.style.display === 'none') {
          this.showSearch();
        } else {
          this.hideSearch();
        }
        break;
      case 'searchNext':
        this.searchNext();
        break;
      case 'searchPrevious':
        this.searchPrevious();
        break;
      case 'expandAll':
        this.expandAll();
        break;
      case 'collapseAll':
        this.collapseAll();
        break;
      case 'copy':
        await this.copyToClipboard();
        break;
      case 'export':
        this.exportData();
        break;
      case 'settings':
        this.showSettings();
        break;
    }
  }

  /**
   * Expand all nodes
   */
  expandAll() {
    this.collapsedNodes.clear();
    this.render();
  }

  /**
   * Collapse all nodes
   */
  collapseAll() {
    this.findAllCollapsiblePaths(this.data, '').forEach(path => {
      this.collapsedNodes.add(path);
    });
    this.render();
  }

  /**
   * Find all collapsible paths recursively
   */
  findAllCollapsiblePaths(value, path) {
    const paths = [];
    
    if (this.isCollapsible(value)) {
      paths.push(path);
      
      const children = this.getChildren(value);
      children.forEach(({ key, value: childValue }) => {
        const childPath = path ? `${path}.${key}` : key;
        paths.push(...this.findAllCollapsiblePaths(childValue, childPath));
      });
    }
    
    return paths;
  }

  /**
   * Copy data to clipboard
   */
  async copyToClipboard() {
    try {
      let textToCopy;
      
      if (this.selectedPath) {
        const selectedData = this.getValueAtPath(this.selectedPath);
        textToCopy = JSON.stringify(selectedData, null, 2);
      } else {
        textToCopy = JSON.stringify(this.data, null, 2);
      }

      await navigator.clipboard.writeText(textToCopy);
      EventHelpers.notify('Copied to clipboard', 'success');
    } catch (error) {
      EventHelpers.error(error, 'Failed to copy to clipboard');
    }
  }

  /**
   * Export data as file
   */
  exportData() {
    try {
      const dataToExport = this.selectedPath ? 
        this.getValueAtPath(this.selectedPath) : this.data;
      
      const jsonString = JSON.stringify(dataToExport, null, 2);
      const blob = new Blob([jsonString], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      
      const link = document.createElement('a');
      link.href = url;
      link.download = 'metadata.json';
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      
      URL.revokeObjectURL(url);
      EventHelpers.notify('Metadata exported', 'success');
    } catch (error) {
      EventHelpers.error(error, 'Failed to export metadata');
    }
  }

  /**
   * Update data info display
   */
  updateDataInfo() {
    if (!this.data) {
      this.elements.objectInfo.textContent = 'No data';
      this.elements.dataSize.textContent = '0 bytes';
      this.elements.nodeCount.textContent = '0 nodes';
      return;
    }

    const type = this.getDataType(this.data);
    const size = JSON.stringify(this.data).length;
    const nodeCount = this.countNodes(this.data);
    
    this.elements.objectInfo.textContent = `${type} (${this.getObjectSummary(this.data)})`;
    this.elements.dataSize.textContent = this.formatBytes(size);
    this.elements.nodeCount.textContent = `${nodeCount} nodes`;
  }

  /**
   * Update selected node info
   */
  updateSelectedInfo() {
    if (!this.selectedPath) {
      this.elements.selectedPath.style.display = 'none';
      return;
    }

    this.elements.selectedPath.style.display = 'inline';
    this.elements.selectedPath.textContent = `Selected: ${this.selectedPath}`;
  }

  /**
   * Update breadcrumb trail
   */
  updateBreadcrumb() {
    if (!this.selectedPath) {
      this.elements.breadcrumbTrail.style.display = 'none';
      return;
    }

    const parts = this.selectedPath.split('.');
    let html = '';
    let currentPath = '';
    
    parts.forEach((part, index) => {
      if (index > 0) {
        html += ' <span class="breadcrumb-separator">›</span> ';
        currentPath += '.';
      }
      currentPath += part;
      
      html += `<span class="breadcrumb-item" data-path="${currentPath}">${this.escapeHtml(part)}</span>`;
    });

    this.elements.breadcrumbTrail.innerHTML = html;
    this.elements.breadcrumbTrail.style.display = 'flex';

    // Add click handlers to breadcrumb items
    this.elements.breadcrumbTrail.querySelectorAll('.breadcrumb-item').forEach(item => {
      item.addEventListener('click', () => {
        this.selectNode(item.dataset.path);
      });
    });
  }

  /**
   * Handle keyboard shortcuts
   */
  handleKeyboardShortcuts(event) {
    const { key, ctrlKey, altKey, shiftKey } = event;

    if (ctrlKey) {
      switch (key.toLowerCase()) {
        case 'f':
          event.preventDefault();
          this.showSearch();
          break;
        case 'g':
          event.preventDefault();
          if (shiftKey) {
            this.searchPrevious();
          } else {
            this.searchNext();
          }
          break;
        case 'c':
          event.preventDefault();
          this.copyToClipboard();
          break;
        case 'e':
          event.preventDefault();
          this.exportData();
          break;
      }
    }

    switch (key) {
      case 'Escape':
        this.hideSearch();
        break;
      case 'F3':
        event.preventDefault();
        if (shiftKey) {
          this.searchPrevious();
        } else {
          this.searchNext();
        }
        break;
    }
  }

  // Utility methods
  
  getDataType(value) {
    if (value === null) return 'null';
    if (value === undefined) return 'undefined';
    if (Array.isArray(value)) return 'array';
    return typeof value;
  }

  isCollapsible(value) {
    return (Array.isArray(value) && value.length > 0) || 
           (value && typeof value === 'object' && Object.keys(value).length > 0);
  }

  getObjectSummary(value) {
    const type = this.getDataType(value);
    if (type === 'array') {
      return `${value.length} items`;
    } else if (type === 'object') {
      return `${Object.keys(value).length} properties`;
    }
    return type;
  }

  countNodes(value, visited = new Set()) {
    if (visited.has(value)) return 0;
    
    let count = 1;
    const type = this.getDataType(value);
    
    if (type === 'object' || type === 'array') {
      visited.add(value);
      const children = this.getChildren(value);
      children.forEach(({ value: childValue }) => {
        count += this.countNodes(childValue, visited);
      });
    }
    
    return count;
  }

  getValueAtPath(path) {
    const parts = path.split('.');
    let current = this.data;
    
    for (const part of parts) {
      if (current == null) return undefined;
      current = current[part];
    }
    
    return current;
  }

  formatNumber(num) {
    if (Number.isInteger(num)) return num.toString();
    return num.toFixed(6).replace(/\.?0+$/, '');
  }

  formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`;
  }

  getLineNumber(path) {
    // Simple line numbering - could be enhanced
    return path.split('.').length;
  }

  escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  showSettings() {
    this.elements.settingsModal.style.display = 'flex';
  }

  hideSettings() {
    this.elements.settingsModal.style.display = 'none';
  }

  showContextMenu(event) {
    const x = event.clientX;
    const y = event.clientY;
    
    this.elements.contextMenu.style.left = `${x}px`;
    this.elements.contextMenu.style.top = `${y}px`;
    this.elements.contextMenu.style.display = 'block';
  }

  hideContextMenu() {
    this.elements.contextMenu.style.display = 'none';
  }

  /**
   * Destroy component and cleanup
   */
  destroy() {
    this.container.innerHTML = '';
  }
}

// Export component
window.MetadataViewer = MetadataViewer;
export default MetadataViewer;
