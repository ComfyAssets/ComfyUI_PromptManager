/**
 * PromptManager Component
 * 
 * Advanced prompt editor with syntax highlighting, auto-completion, templates,
 * and real-time preview. The centerpiece of our professional interface.
 */

class PromptManager {
  constructor(container, options = {}) {
    this.container = typeof container === 'string' ? document.getElementById(container) : container;
    this.options = {
      autoSave: true,
      autoSaveDelay: 2000,
      enableTemplates: true,
      enableAutoComplete: true,
      enableSyntaxHighlighting: true,
      enablePreview: true,
      maxHistory: 50,
      showWordCount: true,
      showCharCount: true,
      defaultPrompt: '',
      ...options
    };

    // State
    this.currentPrompt = {
      id: null,
      text: this.options.defaultPrompt,
      negative_text: '',
      metadata: {},
      created_at: null,
      updated_at: null
    };
    
    this.history = [];
    this.historyIndex = -1;
    this.templates = [];
    this.suggestions = [];
    this.isDirty = false;
    this.autoSaveTimer = null;
    this.selectionStart = 0;
    this.selectionEnd = 0;

    // Editor state
    this.isPreviewMode = false;
    this.isSplitView = false;
    this.isFullscreen = false;

    // Initialize
    this.init();
  }

  /**
   * Initialize prompt manager
   */
  async init() {
    this.createStructure();
    this.bindEvents();
    await this.loadTemplates();
    await this.loadSuggestions();
    this.initializeEditor();
    
    // Load saved prompt if exists
    const savedPromptId = localStorage.getItem('promptmanager_current_prompt');
    if (savedPromptId) {
      await this.loadPrompt(savedPromptId);
    }
  }

  /**
   * Create component HTML structure
   */
  createStructure() {
    this.container.className = 'prompt-manager';
    this.container.innerHTML = `
      <div class="prompt-manager-header">
        <div class="header-left">
          <button class="header-btn" data-action="new" title="New prompt (Ctrl+N)">
            <svg class="icon"><use href="#icon-plus"></use></svg>
            <span>New</span>
          </button>
          <button class="header-btn" data-action="open" title="Open prompt (Ctrl+O)">
            <svg class="icon"><use href="#icon-folder-open"></use></svg>
            <span>Open</span>
          </button>
          <button class="header-btn" data-action="save" title="Save prompt (Ctrl+S)">
            <svg class="icon"><use href="#icon-save"></use></svg>
            <span>Save</span>
          </button>
          <button class="header-btn" data-action="saveAs" title="Save as new prompt">
            <svg class="icon"><use href="#icon-save-as"></use></svg>
            <span>Save As</span>
          </button>
        </div>

        <div class="header-center">
          <input type="text" class="prompt-title" placeholder="Untitled Prompt" maxlength="100">
          <div class="prompt-status">
            <span class="status-indicator"></span>
            <span class="status-text">Ready</span>
          </div>
        </div>

        <div class="header-right">
          <button class="header-btn toggle-btn" data-action="togglePreview" title="Toggle preview">
            <svg class="icon"><use href="#icon-eye"></use></svg>
          </button>
          <button class="header-btn toggle-btn" data-action="toggleSplit" title="Split view">
            <svg class="icon"><use href="#icon-split"></use></svg>
          </button>
          <button class="header-btn toggle-btn" data-action="toggleFullscreen" title="Fullscreen">
            <svg class="icon"><use href="#icon-fullscreen"></use></svg>
          </button>
          <button class="header-btn" data-action="settings" title="Settings">
            <svg class="icon"><use href="#icon-settings"></use></svg>
          </button>
        </div>
      </div>

      <div class="prompt-manager-toolbar">
        <div class="toolbar-section">
          <label class="toolbar-label">Templates:</label>
          <select class="template-select">
            <option value="">Select template...</option>
          </select>
          <button class="toolbar-btn" data-action="manageTemplates" title="Manage templates">
            <svg class="icon"><use href="#icon-edit"></use></svg>
          </button>
        </div>

        <div class="toolbar-section">
          <button class="toolbar-btn" data-action="insertToken" data-token="[subject]" title="Insert subject token">
            Subject
          </button>
          <button class="toolbar-btn" data-action="insertToken" data-token="[style]" title="Insert style token">
            Style
          </button>
          <button class="toolbar-btn" data-action="insertToken" data-token="[quality]" title="Insert quality token">
            Quality
          </button>
          <button class="toolbar-btn" data-action="insertToken" data-token="[lighting]" title="Insert lighting token">
            Lighting
          </button>
        </div>

        <div class="toolbar-section">
          <button class="toolbar-btn" data-action="undo" title="Undo (Ctrl+Z)">
            <svg class="icon"><use href="#icon-undo"></use></svg>
          </button>
          <button class="toolbar-btn" data-action="redo" title="Redo (Ctrl+Y)">
            <svg class="icon"><use href="#icon-redo"></use></svg>
          </button>
          <button class="toolbar-btn" data-action="find" title="Find (Ctrl+F)">
            <svg class="icon"><use href="#icon-search"></use></svg>
          </button>
          <button class="toolbar-btn" data-action="replace" title="Replace (Ctrl+H)">
            <svg class="icon"><use href="#icon-replace"></use></svg>
          </button>
        </div>

        <div class="toolbar-section">
          <button class="toolbar-btn" data-action="formatPrompt" title="Format prompt">
            <svg class="icon"><use href="#icon-format"></use></svg>
            Format
          </button>
          <button class="toolbar-btn" data-action="analyzePrompt" title="Analyze prompt">
            <svg class="icon"><use href="#icon-analyze"></use></svg>
            Analyze
          </button>
        </div>
      </div>

      <div class="prompt-manager-content">
        <div class="editor-panel">
          <div class="editor-section positive-section">
            <div class="section-header">
              <h3>Positive Prompt</h3>
              <div class="section-stats">
                <span class="word-count">0 words</span>
                <span class="char-count">0/2000 chars</span>
              </div>
            </div>
            
            <div class="editor-wrapper">
              <div class="editor-container">
                <textarea class="prompt-editor positive-editor" 
                         placeholder="Describe what you want to see in your image..."
                         spellcheck="false"></textarea>
                <div class="editor-overlay">
                  <div class="autocomplete-popup" style="display: none;">
                    <ul class="autocomplete-list"></ul>
                  </div>
                </div>
              </div>
              
              <div class="editor-sidebar">
                <div class="suggestions-panel">
                  <h4>Suggestions</h4>
                  <div class="suggestion-categories">
                    <button class="suggestion-tab active" data-category="style">Style</button>
                    <button class="suggestion-tab" data-category="quality">Quality</button>
                    <button class="suggestion-tab" data-category="lighting">Lighting</button>
                    <button class="suggestion-tab" data-category="composition">Composition</button>
                  </div>
                  <div class="suggestion-list">
                    <!-- Suggestions will be populated here -->
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div class="editor-section negative-section">
            <div class="section-header">
              <h3>Negative Prompt</h3>
              <div class="section-stats">
                <span class="word-count">0 words</span>
                <span class="char-count">0/1000 chars</span>
              </div>
            </div>
            
            <div class="editor-wrapper">
              <div class="editor-container">
                <textarea class="prompt-editor negative-editor" 
                         placeholder="Describe what you don't want in your image..."
                         spellcheck="false"></textarea>
              </div>
            </div>
          </div>

          <div class="metadata-section" style="display: none;">
            <div class="section-header">
              <h3>Metadata & Parameters</h3>
              <button class="toggle-metadata" data-action="toggleMetadata">
                <svg class="icon"><use href="#icon-chevron-up"></use></svg>
              </button>
            </div>
            
            <div class="metadata-content">
              <div class="metadata-grid">
                <div class="metadata-field">
                  <label>Steps:</label>
                  <input type="number" name="steps" min="1" max="100" value="20">
                </div>
                <div class="metadata-field">
                  <label>CFG Scale:</label>
                  <input type="number" name="cfg_scale" min="1" max="30" step="0.1" value="7.0">
                </div>
                <div class="metadata-field">
                  <label>Sampler:</label>
                  <select name="sampler">
                    <option value="euler">Euler</option>
                    <option value="euler_ancestral">Euler Ancestral</option>
                    <option value="dpm_2">DPM++ 2M</option>
                    <option value="dpm_2_ancestral">DPM++ 2M Ancestral</option>
                  </select>
                </div>
                <div class="metadata-field">
                  <label>Seed:</label>
                  <input type="number" name="seed" placeholder="Random">
                </div>
                <div class="metadata-field">
                  <label>Width:</label>
                  <input type="number" name="width" value="512" step="64" min="64" max="2048">
                </div>
                <div class="metadata-field">
                  <label>Height:</label>
                  <input type="number" name="height" value="512" step="64" min="64" max="2048">
                </div>
              </div>
            </div>
          </div>
        </div>

        <div class="preview-panel" style="display: none;">
          <div class="preview-header">
            <h3>Preview</h3>
            <div class="preview-controls">
              <button class="preview-btn" data-action="refreshPreview" title="Refresh preview">
                <svg class="icon"><use href="#icon-refresh"></use></svg>
              </button>
              <button class="preview-btn" data-action="copyPreview" title="Copy formatted prompt">
                <svg class="icon"><use href="#icon-copy"></use></svg>
              </button>
            </div>
          </div>
          
          <div class="preview-content">
            <div class="formatted-prompt">
              <div class="formatted-positive">
                <h4>Positive:</h4>
                <div class="formatted-text"></div>
              </div>
              <div class="formatted-negative" style="display: none;">
                <h4>Negative:</h4>
                <div class="formatted-text"></div>
              </div>
            </div>
            
            <div class="prompt-analysis">
              <div class="analysis-metrics">
                <div class="metric">
                  <span class="metric-label">Complexity:</span>
                  <span class="metric-value">-</span>
                </div>
                <div class="metric">
                  <span class="metric-label">Keywords:</span>
                  <span class="metric-value">0</span>
                </div>
                <div class="metric">
                  <span class="metric-label">Estimated tokens:</span>
                  <span class="metric-value">0</span>
                </div>
              </div>
              
              <div class="keyword-tags">
                <!-- Keywords will be displayed here -->
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="prompt-manager-footer">
        <div class="footer-left">
          <span class="cursor-position">Ln 1, Col 1</span>
          <span class="selection-info" style="display: none;"></span>
        </div>

        <div class="footer-center">
          <div class="auto-save-status">
            <span class="auto-save-indicator"></span>
            <span class="auto-save-text">Auto-save enabled</span>
          </div>
        </div>

        <div class="footer-right">
          <button class="footer-btn" data-action="showHistory" title="Show history">
            <svg class="icon"><use href="#icon-history"></use></svg>
            History
          </button>
          <button class="footer-btn" data-action="showStatistics" title="Show statistics">
            <svg class="icon"><use href="#icon-chart"></use></svg>
            Stats
          </button>
        </div>
      </div>

      <!-- Find/Replace Dialog -->
      <div class="find-replace-dialog" style="display: none;">
        <div class="dialog-content">
          <div class="dialog-header">
            <h4>Find & Replace</h4>
            <button class="dialog-close" data-action="closeFindReplace">×</button>
          </div>
          
          <div class="dialog-body">
            <div class="find-section">
              <label>Find:</label>
              <input type="text" class="find-input" placeholder="Enter search term...">
              <div class="find-options">
                <label><input type="checkbox" class="case-sensitive"> Case sensitive</label>
                <label><input type="checkbox" class="whole-word"> Whole word</label>
                <label><input type="checkbox" class="regex"> Regular expression</label>
              </div>
            </div>
            
            <div class="replace-section">
              <label>Replace:</label>
              <input type="text" class="replace-input" placeholder="Enter replacement...">
            </div>
            
            <div class="dialog-actions">
              <button class="dialog-btn" data-action="findNext">Find Next</button>
              <button class="dialog-btn" data-action="findPrevious">Find Previous</button>
              <button class="dialog-btn" data-action="replaceNext">Replace</button>
              <button class="dialog-btn primary" data-action="replaceAll">Replace All</button>
            </div>
          </div>
        </div>
      </div>
    `;

    // Cache DOM elements
    this.elements = {
      titleInput: this.container.querySelector('.prompt-title'),
      statusIndicator: this.container.querySelector('.status-indicator'),
      statusText: this.container.querySelector('.status-text'),
      templateSelect: this.container.querySelector('.template-select'),
      positiveEditor: this.container.querySelector('.positive-editor'),
      negativeEditor: this.container.querySelector('.negative-editor'),
      previewPanel: this.container.querySelector('.preview-panel'),
      metadataSection: this.container.querySelector('.metadata-section'),
      autocompletePopup: this.container.querySelector('.autocomplete-popup'),
      suggestionList: this.container.querySelector('.suggestion-list'),
      suggestionTabs: this.container.querySelectorAll('.suggestion-tab'),
      findDialog: this.container.querySelector('.find-replace-dialog'),
      cursorPosition: this.container.querySelector('.cursor-position'),
      selectionInfo: this.container.querySelector('.selection-info'),
      autoSaveIndicator: this.container.querySelector('.auto-save-indicator'),
      autoSaveText: this.container.querySelector('.auto-save-text'),
      wordCounts: this.container.querySelectorAll('.word-count'),
      charCounts: this.container.querySelectorAll('.char-count')
    };
  }

  /**
   * Bind event listeners
   */
  bindEvents() {
    // Header and toolbar actions
    this.container.addEventListener('click', (e) => {
      const action = e.target.dataset.action;
      if (action) {
        e.preventDefault();
        this.handleAction(action, e);
      }
    });

    // Template selection
    this.elements.templateSelect.addEventListener('change', (e) => {
      if (e.target.value) {
        this.loadTemplate(e.target.value);
      }
    });

    // Editor events
    this.elements.positiveEditor.addEventListener('input', (e) => {
      this.handleEditorInput(e, 'positive');
    });

    this.elements.negativeEditor.addEventListener('input', (e) => {
      this.handleEditorInput(e, 'negative');
    });

    // Editor selection and cursor tracking
    [this.elements.positiveEditor, this.elements.negativeEditor].forEach(editor => {
      editor.addEventListener('selectionchange', () => {
        this.updateCursorInfo();
      });

      editor.addEventListener('keyup', () => {
        this.updateCursorInfo();
      });

      editor.addEventListener('mouseup', () => {
        this.updateCursorInfo();
      });

      // Auto-completion
      editor.addEventListener('keydown', (e) => {
        this.handleEditorKeydown(e);
      });
    });

    // Suggestion tabs
    this.elements.suggestionTabs.forEach(tab => {
      tab.addEventListener('click', (e) => {
        this.switchSuggestionCategory(e.target.dataset.category);
      });
    });

    // Title input
    this.elements.titleInput.addEventListener('input', () => {
      this.markDirty();
    });

    // Keyboard shortcuts
    document.addEventListener('keydown', (e) => {
      if (this.container.contains(document.activeElement)) {
        this.handleKeyboardShortcuts(e);
      }
    });

    // Auto-save on blur
    window.addEventListener('beforeunload', (e) => {
      if (this.isDirty && this.options.autoSave) {
        this.save();
      }
    });
  }

  /**
   * Initialize editor functionality
   */
  initializeEditor() {
    // Set initial content
    this.elements.positiveEditor.value = this.currentPrompt.text;
    this.elements.negativeEditor.value = this.currentPrompt.negative_text;
    this.elements.titleInput.value = this.currentPrompt.title || '';

    // Initialize syntax highlighting if enabled
    if (this.options.enableSyntaxHighlighting) {
      this.initSyntaxHighlighting();
    }

    // Update counters
    this.updateWordCounts();
    this.updateCursorInfo();
    
    // Set initial status
    this.setStatus('ready', 'Ready');
  }

  /**
   * Handle editor input
   */
  handleEditorInput(event, type) {
    const value = event.target.value;
    
    if (type === 'positive') {
      this.currentPrompt.text = value;
    } else {
      this.currentPrompt.negative_text = value;
    }

    this.updateWordCounts();
    this.markDirty();
    
    if (this.isPreviewMode) {
      this.updatePreview();
    }

    // Show auto-completion if enabled
    if (this.options.enableAutoComplete) {
      this.showAutoComplete(event.target);
    }

    // Schedule auto-save
    if (this.options.autoSave) {
      this.scheduleAutoSave();
    }
  }

  /**
   * Handle editor keydown for shortcuts and auto-completion
   */
  handleEditorKeydown(event) {
    const { key, ctrlKey, altKey } = event;

    // Handle auto-completion navigation
    if (this.autocompleteVisible) {
      switch (key) {
        case 'ArrowDown':
          event.preventDefault();
          this.selectNextAutocomplete();
          break;
        case 'ArrowUp':
          event.preventDefault();
          this.selectPreviousAutocomplete();
          break;
        case 'Enter':
        case 'Tab':
          event.preventDefault();
          this.insertSelectedAutocomplete();
          break;
        case 'Escape':
          this.hideAutoComplete();
          break;
      }
      return;
    }

    // Handle bracket matching
    if (['(', '[', '{'].includes(key)) {
      this.handleBracketInsertion(event);
    }
  }

  /**
   * Handle various actions
   */
  async handleAction(action, event) {
    switch (action) {
      case 'new':
        await this.newPrompt();
        break;
      case 'open':
        await this.openPrompt();
        break;
      case 'save':
        await this.save();
        break;
      case 'saveAs':
        await this.saveAs();
        break;
      case 'togglePreview':
        this.togglePreview();
        break;
      case 'toggleSplit':
        this.toggleSplitView();
        break;
      case 'toggleFullscreen':
        this.toggleFullscreen();
        break;
      case 'toggleMetadata':
        this.toggleMetadata();
        break;
      case 'insertToken':
        this.insertToken(event.target.dataset.token);
        break;
      case 'undo':
        this.undo();
        break;
      case 'redo':
        this.redo();
        break;
      case 'find':
        this.showFindDialog();
        break;
      case 'replace':
        this.showFindDialog(true);
        break;
      case 'formatPrompt':
        this.formatPrompt();
        break;
      case 'analyzePrompt':
        this.analyzePrompt();
        break;
      case 'manageTemplates':
        this.showTemplateManager();
        break;
    }
  }

  /**
   * Handle keyboard shortcuts
   */
  handleKeyboardShortcuts(event) {
    const { key, ctrlKey, altKey, shiftKey } = event;

    if (ctrlKey) {
      switch (key.toLowerCase()) {
        case 'n':
          event.preventDefault();
          this.newPrompt();
          break;
        case 'o':
          event.preventDefault();
          this.openPrompt();
          break;
        case 's':
          event.preventDefault();
          if (shiftKey) {
            this.saveAs();
          } else {
            this.save();
          }
          break;
        case 'z':
          event.preventDefault();
          if (shiftKey) {
            this.redo();
          } else {
            this.undo();
          }
          break;
        case 'y':
          event.preventDefault();
          this.redo();
          break;
        case 'f':
          event.preventDefault();
          this.showFindDialog();
          break;
        case 'h':
          event.preventDefault();
          this.showFindDialog(true);
          break;
      }
    }

    if (key === 'F11') {
      event.preventDefault();
      this.toggleFullscreen();
    }
  }

  /**
   * Load templates from API
   */
  async loadTemplates() {
    try {
      const response = await api.get('/templates');
      this.templates = response.data || [];
      this.populateTemplateSelect();
    } catch (error) {
      console.error('Failed to load templates:', error);
    }
  }

  /**
   * Load suggestions from API
   */
  async loadSuggestions() {
    try {
      const response = await api.get('/suggestions');
      this.suggestions = response.data || {};
      this.populateSuggestions('style'); // Default category
    } catch (error) {
      console.error('Failed to load suggestions:', error);
    }
  }

  /**
   * Populate template select dropdown
   */
  populateTemplateSelect() {
    const select = this.elements.templateSelect;
    select.innerHTML = '<option value="">Select template...</option>';
    
    this.templates.forEach(template => {
      const option = document.createElement('option');
      option.value = template.id;
      option.textContent = template.name;
      select.appendChild(option);
    });
  }

  /**
   * Populate suggestions panel
   */
  populateSuggestions(category) {
    const suggestions = this.suggestions[category] || [];
    const container = this.elements.suggestionList;
    
    container.innerHTML = '';
    
    suggestions.forEach(suggestion => {
      const element = document.createElement('div');
      element.className = 'suggestion-item';
      element.textContent = suggestion.text;
      element.title = suggestion.description || '';
      element.addEventListener('click', () => {
        this.insertText(suggestion.text);
      });
      container.appendChild(element);
    });
  }

  /**
   * Switch suggestion category
   */
  switchSuggestionCategory(category) {
    // Update tab states
    this.elements.suggestionTabs.forEach(tab => {
      tab.classList.toggle('active', tab.dataset.category === category);
    });
    
    // Load suggestions for category
    this.populateSuggestions(category);
  }

  /**
   * Update word and character counts
   */
  updateWordCounts() {
    const positiveText = this.elements.positiveEditor.value;
    const negativeText = this.elements.negativeEditor.value;
    
    const positiveWords = positiveText.trim() ? positiveText.trim().split(/\s+/).length : 0;
    const negativeWords = negativeText.trim() ? negativeText.trim().split(/\s+/).length : 0;
    
    // Update positive section stats
    const positiveSection = this.container.querySelector('.positive-section');
    positiveSection.querySelector('.word-count').textContent = `${positiveWords} words`;
    positiveSection.querySelector('.char-count').textContent = `${positiveText.length}/2000 chars`;
    
    // Update negative section stats
    const negativeSection = this.container.querySelector('.negative-section');
    negativeSection.querySelector('.word-count').textContent = `${negativeWords} words`;
    negativeSection.querySelector('.char-count').textContent = `${negativeText.length}/1000 chars`;
  }

  /**
   * Update cursor position info
   */
  updateCursorInfo() {
    const activeEditor = document.activeElement;
    if (![this.elements.positiveEditor, this.elements.negativeEditor].includes(activeEditor)) {
      return;
    }

    const start = activeEditor.selectionStart;
    const end = activeEditor.selectionEnd;
    const text = activeEditor.value;
    
    // Calculate line and column
    const textBeforeCursor = text.substring(0, start);
    const lines = textBeforeCursor.split('\n');
    const line = lines.length;
    const col = lines[lines.length - 1].length + 1;
    
    this.elements.cursorPosition.textContent = `Ln ${line}, Col ${col}`;
    
    // Show selection info if text is selected
    if (start !== end) {
      const selectedText = text.substring(start, end);
      const selectedWords = selectedText.trim() ? selectedText.trim().split(/\s+/).length : 0;
      this.elements.selectionInfo.textContent = `${end - start} chars, ${selectedWords} words selected`;
      this.elements.selectionInfo.style.display = 'inline';
    } else {
      this.elements.selectionInfo.style.display = 'none';
    }
  }

  /**
   * Mark prompt as dirty (unsaved changes)
   */
  markDirty() {
    this.isDirty = true;
    this.setStatus('modified', 'Modified');
    this.elements.titleInput.classList.add('modified');
  }

  /**
   * Mark prompt as clean (saved)
   */
  markClean() {
    this.isDirty = false;
    this.setStatus('saved', 'Saved');
    this.elements.titleInput.classList.remove('modified');
  }

  /**
   * Set status indicator
   */
  setStatus(state, text) {
    this.elements.statusIndicator.className = `status-indicator ${state}`;
    this.elements.statusText.textContent = text;
  }

  /**
   * Schedule auto-save
   */
  scheduleAutoSave() {
    if (this.autoSaveTimer) {
      clearTimeout(this.autoSaveTimer);
    }
    
    this.autoSaveTimer = setTimeout(() => {
      if (this.isDirty) {
        this.autoSave();
      }
    }, this.options.autoSaveDelay);
  }

  /**
   * Auto-save prompt
   */
  async autoSave() {
    try {
      await this.save(true);
      this.elements.autoSaveText.textContent = 'Auto-saved';
      this.elements.autoSaveIndicator.className = 'auto-save-indicator success';
      
      setTimeout(() => {
        this.elements.autoSaveText.textContent = 'Auto-save enabled';
        this.elements.autoSaveIndicator.className = 'auto-save-indicator';
      }, 2000);
    } catch (error) {
      this.elements.autoSaveText.textContent = 'Auto-save failed';
      this.elements.autoSaveIndicator.className = 'auto-save-indicator error';
    }
  }

  /**
   * Create new prompt
   */
  async newPrompt() {
    if (this.isDirty) {
      const save = confirm('You have unsaved changes. Save before creating new prompt?');
      if (save) {
        await this.save();
      }
    }

    this.currentPrompt = {
      id: null,
      text: '',
      negative_text: '',
      title: '',
      metadata: {},
      created_at: null,
      updated_at: null
    };

    this.elements.positiveEditor.value = '';
    this.elements.negativeEditor.value = '';
    this.elements.titleInput.value = '';
    
    this.markClean();
    this.updateWordCounts();
    this.elements.positiveEditor.focus();

    events.emit(AppEvents.PROMPT_CREATE, this.currentPrompt);
  }

  /**
   * Open prompt dialog
   */
  async openPrompt() {
    // This would typically open a prompt browser dialog
    // For now, emit event to let parent handle
    events.emit('prompt:open-dialog');
  }

  /**
   * Load specific prompt
   */
  async loadPrompt(promptId) {
    try {
      this.setStatus('loading', 'Loading...');
      
      const response = await api.get(`/prompts/${promptId}`);
      this.currentPrompt = response.data;
      
      this.elements.positiveEditor.value = this.currentPrompt.text || '';
      this.elements.negativeEditor.value = this.currentPrompt.negative_text || '';
      this.elements.titleInput.value = this.currentPrompt.title || '';
      
      this.markClean();
      this.updateWordCounts();
      
      // Save to local storage
      localStorage.setItem('promptmanager_current_prompt', promptId);
      
      events.emit(AppEvents.PROMPT_LOAD, this.currentPrompt);
      
    } catch (error) {
      EventHelpers.error(error, 'Failed to load prompt');
      this.setStatus('error', 'Load failed');
    }
  }

  /**
   * Save prompt
   */
  async save(isAutoSave = false) {
    try {
      if (!isAutoSave) {
        this.setStatus('saving', 'Saving...');
      }

      const promptData = {
        text: this.elements.positiveEditor.value,
        negative_text: this.elements.negativeEditor.value,
        title: this.elements.titleInput.value || 'Untitled Prompt',
        metadata: this.gatherMetadata()
      };

      let response;
      if (this.currentPrompt.id) {
        response = await api.put(`/prompts/${this.currentPrompt.id}`, promptData);
      } else {
        response = await api.post('/prompts', promptData);
      }

      this.currentPrompt = response.data;
      this.markClean();
      
      if (!isAutoSave) {
        EventHelpers.notify('Prompt saved successfully', 'success');
        events.emit(AppEvents.PROMPT_SAVE, this.currentPrompt);
      }

    } catch (error) {
      EventHelpers.error(error, 'Failed to save prompt');
      this.setStatus('error', 'Save failed');
    }
  }

  /**
   * Save prompt with new name
   */
  async saveAs() {
    const title = prompt('Enter prompt title:', this.elements.titleInput.value || 'Untitled Prompt');
    if (!title) return;

    const oldId = this.currentPrompt.id;
    this.currentPrompt.id = null; // Force creation of new prompt
    this.elements.titleInput.value = title;
    
    await this.save();
    
    if (this.currentPrompt.id) {
      EventHelpers.notify(`Prompt saved as "${title}"`, 'success');
    }
  }

  /**
   * Toggle preview mode
   */
  togglePreview() {
    this.isPreviewMode = !this.isPreviewMode;
    
    const previewPanel = this.elements.previewPanel;
    previewPanel.style.display = this.isPreviewMode ? 'block' : 'none';
    
    const toggleBtn = this.container.querySelector('[data-action="togglePreview"]');
    toggleBtn.classList.toggle('active', this.isPreviewMode);
    
    if (this.isPreviewMode) {
      this.updatePreview();
    }
  }

  /**
   * Update preview content
   */
  updatePreview() {
    if (!this.isPreviewMode) return;

    const positiveText = this.elements.positiveEditor.value;
    const negativeText = this.elements.negativeEditor.value;

    // Format and display positive prompt
    const formattedPositive = this.formatPromptText(positiveText);
    const positiveContainer = this.container.querySelector('.formatted-positive .formatted-text');
    positiveContainer.innerHTML = formattedPositive;

    // Format and display negative prompt
    if (negativeText.trim()) {
      const formattedNegative = this.formatPromptText(negativeText);
      const negativeContainer = this.container.querySelector('.formatted-negative');
      negativeContainer.style.display = 'block';
      negativeContainer.querySelector('.formatted-text').innerHTML = formattedNegative;
    } else {
      this.container.querySelector('.formatted-negative').style.display = 'none';
    }

    // Update analysis
    this.updatePromptAnalysis();
  }

  /**
   * Format prompt text for preview
   */
  formatPromptText(text) {
    if (!text.trim()) return '<em>Empty prompt</em>';
    
    return text
      .split(',')
      .map(part => part.trim())
      .filter(part => part)
      .map(part => `<span class="prompt-tag">${this.escapeHtml(part)}</span>`)
      .join(', ');
  }

  /**
   * Update prompt analysis metrics
   */
  updatePromptAnalysis() {
    const positiveText = this.elements.positiveEditor.value;
    const negativeText = this.elements.negativeEditor.value;
    const combinedText = positiveText + ' ' + negativeText;

    // Extract keywords
    const keywords = this.extractKeywords(combinedText);
    const keywordContainer = this.container.querySelector('.keyword-tags');
    keywordContainer.innerHTML = keywords
      .slice(0, 20) // Limit to 20 keywords
      .map(keyword => `<span class="keyword-tag">${this.escapeHtml(keyword)}</span>`)
      .join('');

    // Update metrics
    const complexity = this.calculateComplexity(positiveText);
    const estimatedTokens = this.estimateTokens(combinedText);

    const metrics = this.container.querySelectorAll('.metric-value');
    metrics[0].textContent = complexity;
    metrics[1].textContent = keywords.length;
    metrics[2].textContent = estimatedTokens;
  }

  /**
   * Extract keywords from text
   */
  extractKeywords(text) {
    return text
      .toLowerCase()
      .replace(/[^\w\s]/g, ' ')
      .split(/\s+/)
      .filter(word => word.length > 2)
      .filter((word, index, arr) => arr.indexOf(word) === index) // Remove duplicates
      .sort();
  }

  /**
   * Calculate prompt complexity
   */
  calculateComplexity(text) {
    const wordCount = text.trim().split(/\s+/).length;
    const uniqueWords = new Set(text.toLowerCase().split(/\s+/)).size;
    const avgWordLength = text.length / Math.max(wordCount, 1);
    
    let complexity = 'Simple';
    if (wordCount > 50 || uniqueWords / wordCount > 0.8 || avgWordLength > 6) {
      complexity = 'Complex';
    } else if (wordCount > 20 || uniqueWords / wordCount > 0.6) {
      complexity = 'Moderate';
    }
    
    return complexity;
  }

  /**
   * Estimate token count
   */
  estimateTokens(text) {
    // Rough estimation: ~1.3 tokens per word on average
    const words = text.trim().split(/\s+/).length;
    return Math.ceil(words * 1.3);
  }

  /**
   * Insert text at cursor position
   */
  insertText(text) {
    const activeEditor = document.activeElement;
    if (![this.elements.positiveEditor, this.elements.negativeEditor].includes(activeEditor)) {
      // Default to positive editor if none active
      activeEditor = this.elements.positiveEditor;
      activeEditor.focus();
    }

    const start = activeEditor.selectionStart;
    const end = activeEditor.selectionEnd;
    const currentValue = activeEditor.value;
    
    const newValue = currentValue.substring(0, start) + text + currentValue.substring(end);
    activeEditor.value = newValue;
    
    // Position cursor after inserted text
    const newPosition = start + text.length;
    activeEditor.setSelectionRange(newPosition, newPosition);
    
    // Trigger input event
    activeEditor.dispatchEvent(new Event('input', { bubbles: true }));
  }

  /**
   * Insert token at cursor
   */
  insertToken(token) {
    // Add space before token if needed
    const activeEditor = document.activeElement;
    if (activeEditor && activeEditor.value && !activeEditor.value.slice(-1).match(/\s/)) {
      this.insertText(' ' + token);
    } else {
      this.insertText(token);
    }
  }

  /**
   * Gather metadata from form
   */
  gatherMetadata() {
    const metadata = {};
    const metadataFields = this.container.querySelectorAll('.metadata-field input, .metadata-field select');
    
    metadataFields.forEach(field => {
      if (field.value) {
        metadata[field.name] = field.type === 'number' ? parseFloat(field.value) : field.value;
      }
    });
    
    return metadata;
  }

  // Utility methods
  escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  /**
   * Destroy component and cleanup
   */
  destroy() {
    if (this.autoSaveTimer) {
      clearTimeout(this.autoSaveTimer);
    }
    
    // Save current state if dirty
    if (this.isDirty && this.options.autoSave) {
      this.save();
    }
    
    this.container.innerHTML = '';
  }
}

// Export component
window.PromptManager = PromptManager;
export default PromptManager;