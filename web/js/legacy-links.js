/**
 * Where a bookmark of the retired admin page lands in the workspace (#668, D207).
 *
 * `/prompt_manager/admin` is now web/admin-redirect.html, which sends the browser
 * to the target this module computes from `location.hash` (the server never sees
 * the hash). The old routes were `#/tags` and `#/tags/<tag>,<tag>[?mode=or]`
 * (tags-page.js updateUrlHash: encodeURIComponent per tag, `?mode=or` only for
 * two or more tags; `__untagged__` stood for the prompts without a tag).
 *
 * Loadable in the browser (window.LegacyLinks) and in Node for tests.
 */
(function (root) {
    "use strict";

    const LIBRARY = "workspace#/library";
    const TAGS = "workspace#/tags";
    const TAGS_ROUTE = "#/tags";
    const UNTAGGED = "__untagged__";

    /** One tag of the old comma list; a segment that cannot be decoded is used as written. */
    function decodeTag(segment) {
        const raw = segment.trim();
        try {
            return decodeURIComponent(raw).trim();
        } catch {
            return raw;
        }
    }

    /**
     * @param {string} hash - window.location.hash of the old admin page, including the leading "#"
     * @returns {string} the workspace URL to open, relative to the old page (so a mount prefix is kept)
     */
    function adminTarget(hash) {
        if (typeof hash !== "string") return LIBRARY;
        if (hash === TAGS_ROUTE) return TAGS;
        if (!hash.startsWith(TAGS_ROUTE + "/")) return LIBRARY;
        // As the old page read it: tags before the first "?", its query up to the next one.
        const [tagsPart, queryPart = ""] = hash.slice(TAGS_ROUTE.length + 1).split("?");
        const tags = [...new Set(tagsPart.split(",").map(decodeTag).filter(Boolean))];
        if (!tags.length) return TAGS;
        const query = new URLSearchParams();
        if (tags.includes(UNTAGGED)) {
            // The old page asked for untagged prompts whenever the entry was in the list.
            query.set("tag_mode", "untagged");
        } else {
            for (const tag of tags) query.append("tag", tag);
            if (tags.length > 1 && new URLSearchParams(queryPart).get("mode") === "or") query.set("tag_mode", "any");
        }
        return `${LIBRARY}?${query}`;
    }

    const api = { adminTarget };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    else root.LegacyLinks = api;
})(typeof window !== "undefined" ? window : globalThis);
