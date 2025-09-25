/**
 * Sidebar Component Module
 * Shared sidebar navigation with quick actions and storage display
 * @module SidebarComponent
 */
const SidebarComponent = (function() {
    'use strict';

    function createStub() {
        const target = {};
        const stub = new Proxy(target, {
            get: (obj, prop) => {
                if (prop in obj) {
                    return obj[prop];
                }
                return (...args) => stub;
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
        console.info('[PromptManager] sidebar component skipped outside PromptManager UI context');
        return createStub();
    }

    // Configuration
    const config = {
        activeClass: 'active',
        navItemClass: 'nav-item',
        currentPage: null
    };

    // State
    let storageData = {
        prompts: { used: 1.2, total: 5, percentage: 24 },
        images: { used: 8.4, total: 20, percentage: 42 }
    };

    // Private methods
    function getSidebarHTML() {
        return `
    <aside class="sidebar">
      <div class="sidebar-header">
        <div class="sidebar-logo">PM</div>
        <div>
          <div class="text-sm uppercase tracking-wider text-pm-mute">PromptManager</div>
          <div class="text-xs text-pm-dim">Professional prompt management</div>
        </div>
      </div>

      <nav class="sidebar-nav">
        <a href="dashboard.html" class="nav-item" data-page="dashboard">
          <span>📊</span>
          <span>Dashboard</span>
        </a>
        <a href="prompts.html" class="nav-item" data-page="prompts">
          <span>📝</span>
          <span>Prompts</span>
        </a>
        <a href="gallery.html" class="nav-item" data-page="gallery">
          <span>🖼️</span>
          <span>Gallery</span>
        </a>
        <a href="metadata.html" class="nav-item" data-page="metadata">
          <span>🏷️</span>
          <span>Metadata</span>
        </a>
        <a href="statistics.html" class="nav-item" data-page="statistics">
          <span>📈</span>
          <span>Statistics</span>
        </a>
        <a href="#" class="nav-item" data-page="collections">
          <span>🗂️</span>
          <span>Collections</span>
        </a>
        <a href="settings.html" class="nav-item" data-page="settings">
          <span>⚙️</span>
          <span>Settings</span>
        </a>
      </nav>

      <!-- Quick Actions -->
      <div class="mt-4 px-2">
        <h3 class="text-xs uppercase tracking-wider text-pm-mute mb-2">Quick Actions</h3>
        <div class="space-y-1">
          <button class="w-full nav-item justify-start text-sm" data-action="new-prompt">
            <span>➕</span>
            <span>New Prompt</span>
          </button>
          <button class="w-full nav-item justify-start text-sm" data-action="import">
            <span>📤</span>
            <span>Import</span>
          </button>
          <button class="w-full nav-item justify-start text-sm" data-action="export">
            <span>📥</span>
            <span>Export</span>
          </button>
          <button class="w-full nav-item justify-start text-sm" data-action="search">
            <span>🔍</span>
            <span>Search</span>
          </button>
        </div>
      </div>

      <!-- Storage Usage -->
      <div class="mt-4 px-2">
        <h3 class="text-xs uppercase tracking-wider text-pm-mute mb-2">Storage</h3>
        <div class="space-y-2">
          <div>
            <div class="flex justify-between text-xs mb-1">
              <span class="text-pm-dim">Prompts</span>
              <span class="text-pm-text">${storageData.prompts.percentage}%</span>
            </div>
            <div class="h-1.5 bg-pm-border rounded-full overflow-hidden">
              <div class="h-full bg-gradient-to-r from-pm-brand to-pm-brand2" style="width: ${storageData.prompts.percentage}%"></div>
            </div>
          </div>
          <div>
            <div class="flex justify-between text-xs mb-1">
              <span class="text-pm-dim">Images</span>
              <span class="text-pm-text">${storageData.images.percentage}%</span>
            </div>
            <div class="h-1.5 bg-pm-border rounded-full overflow-hidden">
              <div class="h-full bg-gradient-to-r from-pm-brand to-pm-brand2" style="width: ${storageData.images.percentage}%"></div>
            </div>
          </div>
          <div class="text-xs text-pm-mute mt-2">
            ${(storageData.prompts.used + storageData.images.used).toFixed(1)} GB / ${storageData.prompts.total + storageData.images.total} GB used
          </div>
        </div>
      </div>

      <div class="mt-auto text-xs text-pm-mute px-2">
        <div class="opacity-70">© 2025 PromptManager v2</div>
      </div>
    </aside>`;
    }

    function setActivePage(pageName) {
        document.querySelectorAll('.nav-item[data-page]').forEach(item => {
            item.classList.remove(config.activeClass);
            if (item.dataset.page === pageName) {
                item.classList.add(config.activeClass);
            }
        });
    }

    function attachEventListeners() {
        // Navigation items
        document.querySelectorAll('.nav-item[data-page]').forEach(item => {
            item.addEventListener('click', function(e) {
                if (this.getAttribute('href') === '#') {
                    e.preventDefault();
                }
            });
        });

        // Quick action buttons
        document.querySelectorAll('button[data-action]').forEach(button => {
            button.addEventListener('click', function() {
                const action = this.dataset.action;
                handleQuickAction(action);
            });
        });
    }

    function handleQuickAction(action) {
        switch (action) {
            case 'new-prompt':
                console.log('Creating new prompt...');
                // Would open new prompt modal
                break;
            case 'import':
                console.log('Opening import dialog...');
                // Would open import dialog
                break;
            case 'export':
                console.log('Opening export dialog...');
                // Would open export dialog
                break;
            case 'search':
                console.log('Focusing search...');
                document.querySelector('.search-input')?.focus();
                break;
            default:
                console.log('Unknown action:', action);
        }

        // Emit event for other modules
        if (typeof EventBus !== 'undefined') {
            EventBus.emit('sidebar.action', { action });
        }
    }

    // Public API
    return {
        /**
         * Initialize the sidebar
         * @param {Object} options - Configuration options
         */
        init: function(options = {}) {
            Object.assign(config, options);

            // Get current page from URL or config
            if (!config.currentPage) {
                const path = window.location.pathname;
                if (path.includes('dashboard')) config.currentPage = 'dashboard';
                else if (path.includes('gallery')) config.currentPage = 'gallery';
                else if (path.includes('metadata')) config.currentPage = 'metadata';
                else if (path.includes('statistics')) config.currentPage = 'statistics';
                else if (path.includes('settings')) config.currentPage = 'settings';
                else if (path.includes('prompts') || path.includes('index')) config.currentPage = 'prompts';
            }

            return this;
        },

        /**
         * Render the sidebar
         * @param {string|Element} container - Container selector or element
         */
        render: function(container) {
            const targetElement = typeof container === 'string'
                ? document.querySelector(container)
                : container;

            if (!targetElement) {
                console.error('Sidebar container not found');
                return this;
            }

            // Check if sidebar already exists
            const existingSidebar = targetElement.querySelector('.sidebar');
            if (existingSidebar) {
                existingSidebar.remove();
            }

            // Insert sidebar HTML
            const sidebarHTML = getSidebarHTML();
            targetElement.insertAdjacentHTML('afterbegin', sidebarHTML);

            // Set active page
            if (config.currentPage) {
                setActivePage(config.currentPage);
            }

            // Attach event listeners
            attachEventListeners();

            return this;
        },

        /**
         * Update storage display
         * @param {Object} data - Storage data
         */
        updateStorage: function(data) {
            if (data.prompts) storageData.prompts = { ...storageData.prompts, ...data.prompts };
            if (data.images) storageData.images = { ...storageData.images, ...data.images };

            // Re-render storage section
            const storageSection = document.querySelector('.sidebar .mt-4.px-2:last-of-type');
            if (storageSection) {
                // Update the progress bars and text
                this.render();
            }

            return this;
        },

        /**
         * Set active page
         * @param {string} pageName - Name of the page
         */
        setActive: function(pageName) {
            config.currentPage = pageName;
            setActivePage(pageName);
            return this;
        },

        /**
         * Get current configuration
         * @returns {Object} Current configuration
         */
        getConfig: function() {
            return { ...config };
        },

        /**
         * Destroy the sidebar
         */
        destroy: function() {
            const sidebar = document.querySelector('.sidebar');
            if (sidebar) {
                sidebar.remove();
            }
        }
    };
})();

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = SidebarComponent;
}
if (typeof window !== 'undefined') {
    window.SidebarComponent = SidebarComponent;
}
