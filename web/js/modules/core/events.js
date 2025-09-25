/**
 * EventBus - Central event management system
 * Provides pub/sub pattern for decoupled module communication
 * @module EventBus
 */
const EventBus = (function() {
    'use strict';

    function createStub() {
        const stub = {
            on: () => {},
            once: () => {},
            off: () => {},
            offAll: () => {},
            emit: () => {},
            emitAsync: async () => {},
            hasListeners: () => false,
            listenerCount: () => 0,
            setMaxListeners: () => {},
            enableLogging: () => {},
            disableLogging: () => {},
            debug: () => ({ events: [], listenerCounts: {}, totalListeners: 0, config: {} }),
        };
        stub.withPrefix = () => stub;
        return stub;
    }

    if (typeof window === 'undefined') {
        return createStub();
    }
    const pmActive = (window.location?.pathname || '').includes('/prompt_manager');
    if (!pmActive) {
        console.info('[PromptManager] EventBus skipped outside PromptManager UI context');
        return createStub();
    }

    // Private storage for event listeners
    const events = {};
    const onceEvents = {};

    // Configuration
    const config = {
        maxListeners: 100,
        enableLogging: false,
        throwOnMaxListeners: false
    };

    // Private methods
    function log(message, data) {
        if (config.enableLogging) {
            console.log(`[EventBus] ${message}`, data || '');
        }
    }

    function validateEventName(event) {
        if (typeof event !== 'string' || event.length === 0) {
            throw new Error('Event name must be a non-empty string');
        }
    }

    function validateCallback(callback) {
        if (typeof callback !== 'function') {
            throw new Error('Callback must be a function');
        }
    }

    function checkMaxListeners(event) {
        const count = (events[event] || []).length;
        if (count >= config.maxListeners) {
            const message = `Max listeners (${config.maxListeners}) exceeded for event: ${event}`;
            if (config.throwOnMaxListeners) {
                throw new Error(message);
            } else {
                console.warn(`[EventBus] ${message}`);
            }
        }
    }

    // Public API
    const api = {
        /**
         * Configure EventBus settings
         * @param {Object} options - Configuration options
         */
        configure: function(options) {
            Object.assign(config, options);
            return this;
        },

        /**
         * Subscribe to an event
         * @param {string} event - Event name
         * @param {Function} callback - Callback function
         * @param {Object} context - Optional context for callback
         * @returns {Function} Unsubscribe function
         */
        on: function(event, callback, context) {
            validateEventName(event);
            validateCallback(callback);
            checkMaxListeners(event);

            if (!events[event]) {
                events[event] = [];
            }

            const listener = { callback, context };
            events[event].push(listener);

            log(`Subscribed to '${event}'`, listener);

            // Return unsubscribe function
            return () => this.off(event, callback, context);
        },

        /**
         * Subscribe to an event once
         * @param {string} event - Event name
         * @param {Function} callback - Callback function
         * @param {Object} context - Optional context for callback
         * @returns {Function} Unsubscribe function
         */
        once: function(event, callback, context) {
            validateEventName(event);
            validateCallback(callback);

            const wrapper = (data) => {
                callback.call(context, data);
                this.off(event, wrapper);
            };

            wrapper.originalCallback = callback;
            return this.on(event, wrapper, context);
        },

        /**
         * Unsubscribe from an event
         * @param {string} event - Event name
         * @param {Function} callback - Callback function to remove
         * @param {Object} context - Context used when subscribing
         */
        off: function(event, callback, context) {
            validateEventName(event);

            if (!events[event]) return this;

            if (!callback) {
                // Remove all listeners for this event
                delete events[event];
                log(`Removed all listeners for '${event}'`);
            } else {
                // Remove specific listener
                events[event] = events[event].filter(listener => {
                    const isMatch = listener.callback === callback ||
                                  listener.callback.originalCallback === callback;
                    const contextMatch = !context || listener.context === context;
                    return !(isMatch && contextMatch);
                });

                if (events[event].length === 0) {
                    delete events[event];
                }

                log(`Unsubscribed from '${event}'`, { callback, context });
            }

            return this;
        },

        /**
         * Emit an event
         * @param {string} event - Event name
         * @param {*} data - Data to pass to listeners
         * @returns {boolean} True if event had listeners
         */
        emit: function(event, data) {
            validateEventName(event);

            const listeners = events[event];
            if (!listeners || listeners.length === 0) {
                log(`No listeners for '${event}'`);
                return false;
            }

            log(`Emitting '${event}'`, data);

            // Clone array to prevent issues if listeners modify during emit
            const listenersClone = [...listeners];

            listenersClone.forEach(listener => {
                try {
                    listener.callback.call(listener.context || null, data);
                } catch (error) {
                    console.error(`[EventBus] Error in listener for '${event}':`, error);
                }
            });

            return true;
        },

        /**
         * Emit an event asynchronously
         * @param {string} event - Event name
         * @param {*} data - Data to pass to listeners
         * @returns {Promise} Resolves when all listeners have been called
         */
        emitAsync: async function(event, data) {
            validateEventName(event);

            const listeners = events[event];
            if (!listeners || listeners.length === 0) {
                return false;
            }

            log(`Emitting async '${event}'`, data);

            const promises = listeners.map(listener => {
                return new Promise((resolve) => {
                    setTimeout(() => {
                        try {
                            const result = listener.callback.call(listener.context || null, data);
                            resolve(result);
                        } catch (error) {
                            console.error(`[EventBus] Error in async listener for '${event}':`, error);
                            resolve(undefined);
                        }
                    }, 0);
                });
            });

            await Promise.all(promises);
            return true;
        },

        /**
         * Check if event has listeners
         * @param {string} event - Event name
         * @returns {boolean} True if event has listeners
         */
        hasListeners: function(event) {
            validateEventName(event);
            return events[event] && events[event].length > 0;
        },

        /**
         * Get listener count for an event
         * @param {string} event - Event name
         * @returns {number} Number of listeners
         */
        listenerCount: function(event) {
            if (!event) {
                // Return total listener count
                return Object.values(events).reduce((sum, arr) => sum + arr.length, 0);
            }
            validateEventName(event);
            return events[event] ? events[event].length : 0;
        },

        /**
         * Get all event names
         * @returns {Array<string>} Array of event names
         */
        eventNames: function() {
            return Object.keys(events);
        },

        /**
         * Remove all event listeners
         */
        clear: function() {
            Object.keys(events).forEach(event => {
                delete events[event];
            });
            log('Cleared all event listeners');
            return this;
        },

        /**
         * Create a namespaced event emitter
         * @param {string} namespace - Namespace prefix
         * @returns {Object} Namespaced emitter
         */
        namespace: function(namespace) {
            const prefix = namespace + ':';

            return {
                on: (event, callback, context) =>
                    api.on(prefix + event, callback, context),
                once: (event, callback, context) =>
                    api.once(prefix + event, callback, context),
                off: (event, callback, context) =>
                    api.off(prefix + event, callback, context),
                emit: (event, data) =>
                    api.emit(prefix + event, data),
                emitAsync: (event, data) =>
                    api.emitAsync(prefix + event, data),
                hasListeners: (event) =>
                    api.hasListeners(prefix + event),
                listenerCount: (event) =>
                    api.listenerCount(prefix + event)
            };
        },

        /**
         * Debug information
         * @returns {Object} Debug info
         */
        debug: function() {
            return {
                events: Object.keys(events),
                listenerCounts: Object.keys(events).reduce((acc, event) => {
                    acc[event] = events[event].length;
                    return acc;
                }, {}),
                totalListeners: this.listenerCount(),
                config: { ...config }
            };
        }
    };

    // Freeze API to prevent modification
    return Object.freeze(api);
})();

// Export for use
if (typeof module !== 'undefined' && module.exports) {
    module.exports = EventBus;
}

// Attach to window for global access
if (typeof window !== 'undefined') {
    window.EventBus = EventBus;
}

// Common event names (for documentation/consistency)
EventBus.Events = {
    // Prompt events
    PROMPT_CREATED: 'prompt.created',
    PROMPT_UPDATED: 'prompt.updated',
    PROMPT_DELETED: 'prompt.deleted',
    PROMPT_SELECTED: 'prompt.selected',
    PROMPT_SEARCH: 'prompt.search',

    // Gallery events
    GALLERY_IMAGE_SELECTED: 'gallery.image.selected',
    GALLERY_IMAGE_DELETED: 'gallery.image.deleted',
    GALLERY_FILTER_CHANGED: 'gallery.filter.changed',
    GALLERY_VIEW_CHANGED: 'gallery.view.changed',

    // Settings events
    SETTINGS_UPDATED: 'settings.updated',
    SETTINGS_RESET: 'settings.reset',

    // UI events
    MODAL_OPEN: 'modal.open',
    MODAL_CLOSE: 'modal.close',
    NOTIFICATION_SHOW: 'notification.show',
    THEME_CHANGED: 'theme.changed',

    // System events
    APP_READY: 'app.ready',
    APP_ERROR: 'app.error',
    API_REQUEST: 'api.request',
    API_RESPONSE: 'api.response',
    API_ERROR: 'api.error'
};
