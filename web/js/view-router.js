/**
 * Hash-route resolution for the admin page: which top-level view a
 * location.hash selects. Kept pure so the #/tags deep link is testable.
 *
 * Loadable in the browser (window.ViewRouter) and in Node for tests.
 */
(function (root) {
    "use strict";

    const VIEWS = Object.freeze(["prompts", "tags"]);
    const TAGS_PREFIX = "#/tags";

    /**
     * @param {string} hash - window.location.hash, including the leading "#"
     * @returns {"prompts"|"tags"}
     */
    function resolveView(hash) {
        if (typeof hash !== "string") return "prompts";
        if (hash === TAGS_PREFIX) return "tags";
        if (hash.startsWith(TAGS_PREFIX + "/")) return "tags";
        return "prompts";
    }

    const api = { resolveView, VIEWS };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    else root.ViewRouter = api;
})(typeof window !== "undefined" ? window : globalThis);
