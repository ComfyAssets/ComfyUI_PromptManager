/**
 * Small JSON API client for the admin and gallery pages.
 *
 * Every method resolves with the parsed body of a successful response and
 * throws ApiError(status, message) for a non-2xx status, a `success: false`
 * body or a body that is not JSON, so callers have one failure path.
 *
 * latestOnly() guards list requests against out-of-order responses: each new
 * call aborts the previous in-flight request and a superseded call resolves
 * to STALE instead of its (outdated) result.
 *
 * Loadable in the browser (window.ApiClient) and in Node for tests.
 */
(function (root) {
    "use strict";

    class ApiError extends Error {
        constructor(status, message) {
            super(message);
            this.name = "ApiError";
            this.status = status;
        }
    }

    /** Result of a latestOnly() call that was superseded by a newer one. */
    const STALE = Symbol("stale response");

    /**
     * @param {typeof fetch} [fetchImpl] - defaults to the global fetch (bound to globalThis)
     */
    function createApiClient(fetchImpl) {
        const doFetch = fetchImpl || ((...args) => globalThis.fetch(...args));

        async function request(method, url, { body, signal } = {}) {
            const init = { method, headers: {} };
            if (body !== undefined) {
                init.headers["Content-Type"] = "application/json";
                init.body = JSON.stringify(body);
            }
            if (signal) init.signal = signal;

            const response = await doFetch(url, init);
            let data = null;
            let parsed = true;
            try {
                data = await response.json();
            } catch (_) {
                parsed = false;
            }

            const serverMessage = data && typeof data === "object" ? data.error || data.message : undefined;
            if (!response.ok) {
                throw new ApiError(response.status, serverMessage || response.statusText || `HTTP ${response.status}`);
            }
            if (!parsed) throw new ApiError(response.status, "Invalid JSON response");
            if (data && typeof data === "object" && data.success === false) {
                throw new ApiError(response.status, serverMessage || "Request failed");
            }
            return data;
        }

        return {
            get: (url, { signal } = {}) => request("GET", url, { signal }),
            post: (url, body, { signal } = {}) => request("POST", url, { body, signal }),
            put: (url, body, { signal } = {}) => request("PUT", url, { body, signal }),
            del: (url, { signal } = {}) => request("DELETE", url, { signal }),
        };
    }

    /**
     * @returns {(run: (signal: AbortSignal) => Promise<*>) => Promise<*>} wrapper that
     *   aborts the previous call's signal and resolves superseded calls to STALE
     */
    function latestOnly() {
        let current = null;
        return async function (run) {
            if (current) current.abort();
            const controller = new AbortController();
            current = controller;
            try {
                const result = await run(controller.signal);
                return controller === current ? result : STALE;
            } catch (error) {
                if (controller !== current) return STALE;
                throw error;
            }
        };
    }

    const api = { createApiClient, latestOnly, ApiError, STALE };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    else root.ApiClient = api;
})(typeof window !== "undefined" ? window : globalThis);
