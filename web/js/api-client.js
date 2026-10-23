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
        constructor(status, message, code = null, recovery = null, current = null) {
            super(message);
            this.name = "ApiError";
            this.status = status;
            this.code = typeof code === "string" && /^[a-z0-9-]{1,64}$/.test(code) ? code : null;
            this.recovery = null;
            if (recovery && typeof recovery === "object" && !Array.isArray(recovery)) {
                const keys = Object.keys(recovery);
                if (keys.length === 1 && keys[0] === "action" &&
                    ["refresh-history", "reload-outputs", "retry-read"].includes(recovery.action)) {
                    this.recovery = { action: recovery.action };
                } else if (keys.length === 2 && keys.includes("action") && keys.includes("limit") &&
                    recovery.action === "smaller-limit" && Number.isInteger(recovery.limit) &&
                    recovery.limit >= 1 && recovery.limit <= 100) {
                    this.recovery = { action: "smaller-limit", limit: recovery.limit };
                }
            }
            // A tag-conflict answer carries the image's current tag set; only a
            // well-formed one is kept, copied, so the dialog can show it.
            this.current = null;
            if (current && Array.isArray(current.tags) && current.tags.length <= 100 &&
                current.tags.every(tag => typeof tag === "string") &&
                Number.isSafeInteger(current.tag_revision) && current.tag_revision >= 0) {
                this.current = { tags: current.tags.slice(), tag_revision: current.tag_revision };
            }
        }
    }

    /** Result of a latestOnly() call that was superseded by a newer one. */
    const STALE = Symbol("stale response");

    /** Keep Prompt Manager requests under the prefix that serves its page. */
    function resolveUrl(url, pathname = root.location && root.location.pathname) {
        if (typeof url !== "string" || !url.startsWith("/prompt_manager/")) return url;
        const match = (pathname || "").match(/^(.*)\/prompt_manager(?:\/|$)/);
        return match && match[1] ? match[1] + url : url;
    }

    /** Like fetch(), but keep Prompt Manager URLs under the page's proxy mount. */
    function fetchUrl(url, init) {
        return globalThis.fetch(resolveUrl(url), init);
    }

    /**
     * @param {typeof fetch} [fetchImpl] - defaults to the global fetch (bound to globalThis)
     */
    function createApiClient(fetchImpl) {
        const doFetch = fetchImpl || ((...args) => globalThis.fetch(...args));

        async function request(method, url, { body, signal, cache, keepalive } = {}) {
            const init = { method, headers: {} };
            if (cache !== undefined) init.cache = cache;
            // keepalive lets a write or a release finish while its page unloads (pagehide).
            if (keepalive) init.keepalive = true;
            if (body !== undefined) {
                init.headers["Content-Type"] = "application/json";
                init.body = JSON.stringify(body);
            }
            if (signal) init.signal = signal;

            const response = await doFetch(resolveUrl(url), init);
            let data = null;
            let parsed = true;
            try {
                data = await response.json();
            } catch (_) {
                parsed = false;
            }

            const serverMessage = data && typeof data === "object" ? data.error || data.message : undefined;
            if (!response.ok) {
                throw new ApiError(response.status, serverMessage || response.statusText || `HTTP ${response.status}`, data?.code, data?.recovery, data);
            }
            if (!parsed) throw new ApiError(response.status, "Invalid JSON response");
            if (data && typeof data === "object" && data.success === false) {
                throw new ApiError(response.status, serverMessage || "Request failed", data.code, data.recovery, data);
            }
            return data;
        }

        return {
            // cache is a fetch RequestCache mode such as "no-store"; omitted keeps the default.
            get: (url, { signal, cache } = {}) => request("GET", url, { signal, cache }),
            post: (url, body, { signal, keepalive } = {}) => request("POST", url, { body, signal, keepalive }),
            put: (url, body, { signal, keepalive } = {}) => request("PUT", url, { body, signal, keepalive }),
            patch: (url, body, { signal } = {}) => request("PATCH", url, { body, signal }),
            // A DELETE body is optional; the favorites store names its file in one.
            del: (url, { signal, body, keepalive } = {}) => request("DELETE", url, { body, signal, keepalive }),
        };
    }

    /**
     * @param {{lifetime?: AbortSignal}} [options] - `lifetime` ends with the page (its pagehide):
     *   then no call starts a request, the one in flight is aborted, and both resolve to STALE.
     *   WebKit fails a fetch that a leaving page starts and logs it as a page error.
     * @returns {(run: (signal: AbortSignal) => Promise<*>) => Promise<*>} wrapper that
     *   aborts the previous call's signal and resolves superseded calls to STALE
     */
    function latestOnly({ lifetime } = {}) {
        let current = null;
        lifetime?.addEventListener("abort", () => current?.abort(), { once: true });
        return async function (run) {
            if (current) current.abort();
            if (lifetime?.aborted) {
                current = null;
                return STALE;
            }
            const controller = new AbortController();
            current = controller;
            try {
                const result = await run(controller.signal);
                return controller === current && !lifetime?.aborted ? result : STALE;
            } catch (error) {
                if (controller !== current || lifetime?.aborted) return STALE;
                throw error;
            }
        };
    }

    const api = { createApiClient, latestOnly, resolveUrl, fetchUrl, ApiError, STALE };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    else root.ApiClient = api;
})(typeof window !== "undefined" ? window : globalThis);
