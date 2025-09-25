# JavaScript Module Architecture

## Structure

```
web/js/
├── app.js                  # Main application entry point (slim)
├── modules/
│   ├── core/
│   │   ├── api.js         # API communication layer
│   │   ├── state.js       # Application state management
│   │   ├── router.js      # Page routing logic
│   │   └── utils.js       # Utility functions
│   ├── components/
│   │   ├── sidebar.js     # Sidebar navigation
│   │   ├── gallery.js     # Gallery view component
│   │   ├── settings.js    # Settings page component
│   │   ├── modal.js       # Modal dialogs
│   │   └── search.js      # Search functionality
│   ├── services/
│   │   ├── prompt.js      # Prompt management service
│   │   ├── webhook.js     # Webhook integration service
│   │   ├── storage.js     # LocalStorage wrapper
│   │   └── metadata.js    # Metadata extraction service
│   └── ui/
│       ├── theme.js       # Theme management
│       ├── animations.js  # Animation utilities
│       └── forms.js       # Form handling utilities
└── vendor/                 # Third-party libraries
```

## Module Pattern

Each module follows this pattern:

```javascript
// modules/components/sidebar.js
const SidebarManager = (function() {
    'use strict';

    // Private variables
    let container = null;
    let defaults = {
        position: 'bottom-right',
        duration: 3000
    };

    // Private functions
    function createContainer() {
        // ...
    }

    // Public API
    return {
        init: function(options) {
            defaults = {...defaults, ...options};
        },
        show: function(message, type) {
            // ...
        },
        clear: function() {
            // ...
        }
    };
})();

// Export for module loader
if (typeof module !== 'undefined' && module.exports) {
    module.exports = SidebarManager;
}
```

## Loading Strategy

1. **Development**: Load modules individually for easier debugging
2. **Production**: Bundle with a simple concatenation script or webpack

## Benefits

- **Maintainability**: Each module is self-contained and focused
- **Reusability**: Components can be reused across different pages
- **Testing**: Individual modules can be tested in isolation
- **Performance**: Only load what's needed
- **Collaboration**: Multiple developers can work on different modules