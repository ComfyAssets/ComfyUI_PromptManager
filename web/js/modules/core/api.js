/**
 * API Communication Layer
 * Centralized API handling with error management and retries
 */
const API = (function() {
    'use strict';

    function createStub() {
        const noopPromise = () => Promise.resolve({ success: false, error: 'PromptManager UI inactive' });
        const nested = new Proxy({}, {
            get: () => noopPromise,
        });
        return {
            configure: () => {},
            request: noopPromise,
            get: noopPromise,
            post: noopPromise,
            put: noopPromise,
            delete: noopPromise,
            upload: noopPromise,
            prompts: new Proxy({}, { get: () => noopPromise }),
            settings: new Proxy({}, { get: () => noopPromise }),
            gallery: new Proxy({}, { get: () => noopPromise }),
            system: {
                health: noopPromise,
                stats: noopPromise,
                logs: noopPromise,
                cache: {
                    clear: noopPromise,
                    stats: noopPromise,
                },
            },
        };
    }

    if (typeof window === 'undefined') {
        return createStub();
    }
    const pmActive = (window.location?.pathname || '').includes('/prompt_manager');
    if (!pmActive) {
        console.info('[PromptManager] API client skipped outside PromptManager UI context');
        return createStub();
    }

    // Configuration
    const config = {
        baseURL: '/api/prompt_manager',
        timeout: 30000,
        retryAttempts: 3,
        retryDelay: 1000
    };

    // Request interceptor for common headers
    function prepareRequest(options = {}) {
        const defaults = {
            headers: {
                'Content-Type': 'application/json',
                ...options.headers
            },
            credentials: 'same-origin'
        };

        return Object.assign({}, defaults, options);
    }

    // Response handler
    async function handleResponse(response) {
        if (!response.ok) {
            const error = new Error(`HTTP ${response.status}: ${response.statusText}`);
            error.status = response.status;

            // Try to get error message from response
            try {
                const data = await response.json();
                error.message = data.error || data.message || error.message;
            } catch (e) {
                // Use default error message
            }

            throw error;
        }

        // Handle empty responses
        const contentType = response.headers.get('content-type');
        if (!contentType || !contentType.includes('application/json')) {
            return response.text();
        }

        return response.json();
    }

    // Retry logic for failed requests
    async function retryRequest(fn, attempts = config.retryAttempts) {
        try {
            return await fn();
        } catch (error) {
            if (attempts <= 1 || error.status < 500) {
                throw error;
            }

            await new Promise(resolve => setTimeout(resolve, config.retryDelay));
            return retryRequest(fn, attempts - 1);
        }
    }

    // Build URL with query parameters
    function buildURL(endpoint, params = {}) {
        const url = new URL(config.baseURL + endpoint, window.location.origin);

        Object.keys(params).forEach(key => {
            if (params[key] !== undefined && params[key] !== null) {
                url.searchParams.append(key, params[key]);
            }
        });

        return url.toString();
    }

    // Public API
    return {
        /**
         * Configure API settings
         */
        configure: function(options) {
            Object.assign(config, options);
        },

        /**
         * GET request
         */
        get: async function(endpoint, params = {}, options = {}) {
            const url = buildURL(endpoint, params);

            return retryRequest(async () => {
                const response = await fetch(url, prepareRequest({
                    method: 'GET',
                    ...options
                }));
                return handleResponse(response);
            });
        },

        /**
         * POST request
         */
        post: async function(endpoint, data = {}, options = {}) {
            const url = buildURL(endpoint);

            return retryRequest(async () => {
                const response = await fetch(url, prepareRequest({
                    method: 'POST',
                    body: JSON.stringify(data),
                    ...options
                }));
                return handleResponse(response);
            });
        },

        /**
         * PUT request
         */
        put: async function(endpoint, data = {}, options = {}) {
            const url = buildURL(endpoint);

            return retryRequest(async () => {
                const response = await fetch(url, prepareRequest({
                    method: 'PUT',
                    body: JSON.stringify(data),
                    ...options
                }));
                return handleResponse(response);
            });
        },

        /**
         * DELETE request
         */
        delete: async function(endpoint, options = {}) {
            const url = buildURL(endpoint);

            return retryRequest(async () => {
                const response = await fetch(url, prepareRequest({
                    method: 'DELETE',
                    ...options
                }));
                return handleResponse(response);
            });
        },

        /**
         * Upload file
         */
        upload: async function(endpoint, file, additionalData = {}, options = {}) {
            const url = buildURL(endpoint);
            const formData = new FormData();

            formData.append('file', file);
            Object.keys(additionalData).forEach(key => {
                formData.append(key, additionalData[key]);
            });

            return retryRequest(async () => {
                const response = await fetch(url, prepareRequest({
                    method: 'POST',
                    body: formData,
                    headers: {}, // Let browser set Content-Type for FormData
                    ...options
                }));
                return handleResponse(response);
            });
        },

        // Prompt-specific endpoints
        prompts: {
            list: (params) => API.get('/prompts', params),
            get: (id) => API.get(`/prompts/${id}`),
            create: (data) => API.post('/prompts', data),
            update: (id, data) => API.put(`/prompts/${id}`, data),
            delete: (id) => API.delete(`/prompts/${id}`),
            search: (query) => API.get('/prompts/search', { q: query }),
            duplicate: (id) => API.post(`/prompts/${id}/duplicate`),
            export: (format = 'json') => API.get('/prompts/export', { format }),
            import: (file) => API.upload('/prompts/import', file)
        },

        // Settings endpoints
        settings: {
            get: () => API.get('/settings'),
            update: (data) => API.post('/settings', data),
            reset: () => API.post('/settings/reset')
        },

        // Gallery endpoints
        gallery: {
            list: (params) => API.get('/gallery', params),
            metadata: (id) => API.get(`/gallery/${id}/metadata`),
            delete: (id) => API.delete(`/gallery/${id}`),
            cleanup: () => API.post('/gallery/cleanup')
        },

        // System endpoints
        system: {
            health: () => API.get('/health'),
            stats: () => API.get('/stats'),
            logs: (params) => API.get('/logs', params),
            cache: {
                clear: () => API.post('/cache/clear'),
                stats: () => API.get('/cache/stats')
            }
        }
    };
})();

// Export for use
if (typeof module !== 'undefined' && module.exports) {
    module.exports = API;
}

// Attach to window for global access
if (typeof window !== 'undefined') {
    window.API = API;
}
