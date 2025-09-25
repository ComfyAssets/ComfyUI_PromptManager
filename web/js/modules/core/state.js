/**
 * State Management Module
 * Centralized application state management
 * @module StateManager
 */
const StateManager = (function() {
    'use strict';

    function createStub() {
        const stub = {
            getState: () => ({}),
            get: () => undefined,
            set: () => stub,
            update: () => stub,
            subscribe: () => () => {},
            unsubscribe: () => {},
            toggle: () => stub,
            reset: () => stub,
            destroy: () => {},
        };
        return stub;
    }

    if (typeof window === 'undefined') {
        return createStub();
    }
    const pmActive = (window.location?.pathname || '').includes('/prompt_manager');
    if (!pmActive) {
        console.info('[PromptManager] state manager skipped outside PromptManager UI context');
        return createStub();
    }

    // Private state storage
    let state = {
        prompts: [],
        filters: {
            search: '',
            category: 'all',
            sortBy: 'newest',
            pageSize: 50,
            currentPage: 1
        },
        ui: {
            sidebarOpen: false,
            modalOpen: false,
            selectedPrompts: new Set()
        },
        user: {
            preferences: {},
            stats: {}
        }
    };

    // State change listeners
    const listeners = new Map();

    // Private methods
    function notifyListeners(path, value) {
        const pathListeners = listeners.get(path) || [];
        pathListeners.forEach(callback => {
            try {
                callback(value, path);
            } catch (error) {
                console.error('State listener error:', error);
            }
        });

        // Notify wildcard listeners
        const wildcardListeners = listeners.get('*') || [];
        wildcardListeners.forEach(callback => {
            try {
                callback(state, path);
            } catch (error) {
                console.error('State wildcard listener error:', error);
            }
        });
    }

    function setNestedValue(obj, path, value) {
        const keys = path.split('.');
        const lastKey = keys.pop();
        const target = keys.reduce((acc, key) => {
            if (!acc[key]) acc[key] = {};
            return acc[key];
        }, obj);
        target[lastKey] = value;
    }

    function getNestedValue(obj, path) {
        return path.split('.').reduce((acc, key) => acc?.[key], obj);
    }

    // Public API
    return {
        /**
         * Initialize state with data
         * @param {Object} initialState - Initial state object
         */
        init: function(initialState = {}) {
            state = { ...state, ...initialState };
            return this;
        },

        /**
         * Get state value by path
         * @param {string} path - Dot-notation path (e.g., 'ui.sidebarOpen')
         * @returns {*} State value
         */
        get: function(path) {
            if (!path) return { ...state };
            return getNestedValue(state, path);
        },

        /**
         * Set state value by path
         * @param {string} path - Dot-notation path
         * @param {*} value - Value to set
         */
        set: function(path, value) {
            const oldValue = getNestedValue(state, path);
            if (oldValue === value) return this; // No change

            setNestedValue(state, path, value);
            notifyListeners(path, value);
            return this;
        },

        /**
         * Subscribe to state changes
         * @param {string} path - Path to watch (use '*' for all changes)
         * @param {Function} callback - Callback function
         * @returns {Function} Unsubscribe function
         */
        subscribe: function(path, callback) {
            if (!listeners.has(path)) {
                listeners.set(path, []);
            }
            listeners.get(path).push(callback);

            // Return unsubscribe function
            return () => {
                const callbacks = listeners.get(path);
                const index = callbacks.indexOf(callback);
                if (index > -1) {
                    callbacks.splice(index, 1);
                }
            };
        },

        /**
         * Update multiple state values
         * @param {Object} updates - Object with path-value pairs
         */
        update: function(updates) {
            Object.entries(updates).forEach(([path, value]) => {
                this.set(path, value);
            });
            return this;
        },

        /**
         * Toggle boolean state value
         * @param {string} path - Path to boolean value
         */
        toggle: function(path) {
            const current = getNestedValue(state, path);
            if (typeof current === 'boolean') {
                this.set(path, !current);
            }
            return this;
        },

        /**
         * Reset state to initial values
         */
        reset: function() {
            state = {
                prompts: [],
                filters: {
                    search: '',
                    category: 'all',
                    sortBy: 'newest',
                    pageSize: 50,
                    currentPage: 1
                },
                ui: {
                    sidebarOpen: false,
                    modalOpen: false,
                    selectedPrompts: new Set()
                },
                user: {
                    preferences: {},
                    stats: {}
                }
            };
            notifyListeners('*', state);
            return this;
        },

        /**
         * Clean up module
         */
        destroy: function() {
            listeners.clear();
            this.reset();
        }
    };
})();

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = StateManager;
}
if (typeof window !== 'undefined') {
    window.StateManager = StateManager;
}
