/**
 * Tag autocomplete for comma-separated tag inputs.
 *
 * The text before the last comma is the committed context; the text after it
 * is the tag being typed. Suggestions come from
 * GET /prompt_manager/tags/suggest, which offers only tags that co-occur with
 * every committed tag, so "asian, " narrows to tags seen together with asian.
 *
 * Loadable in the browser (window.TagAutocomplete) and in Node for tests.
 */
(function (root) {
    "use strict";

    const SUGGEST_URL = "/prompt_manager/tags/suggest";
    const DEFAULT_LIMIT = 15;

    /** @returns {{committed: string[], current: string}} */
    function splitTagInput(value) {
        const parts = String(value == null ? "" : value).split(",");
        const current = parts.pop().trim();
        const committed = [];
        const seen = new Set();
        for (const part of parts) {
            const tag = part.trim();
            if (!tag) continue;
            const key = tag.toLowerCase();
            if (seen.has(key)) continue;
            seen.add(key);
            committed.push(tag);
        }
        return { committed, current };
    }

    /** Replace the tag being typed with `tag`; the result ends with ", " for the next one. */
    function applySuggestion(value, tag) {
        const { committed } = splitTagInput(value);
        const known = new Set(committed.map((t) => t.toLowerCase()));
        const tags = known.has(tag.toLowerCase()) ? committed : committed.concat([tag]);
        return tags.join(", ") + ", ";
    }

    function suggestUrl(value, limit = DEFAULT_LIMIT) {
        const { committed, current } = splitTagInput(value);
        const params = new URLSearchParams();
        params.set("q", current);
        params.set("with", committed.join(","));
        params.set("limit", String(limit));
        return `${SUGGEST_URL}?${params.toString()}`;
    }

    /** Keyboard navigation: -1 means nothing selected; wraps at both ends. */
    function moveSelection(index, delta, count) {
        if (!count || count <= 0) return -1;
        if (index < 0) return delta > 0 ? 0 : count - 1;
        return (((index + delta) % count) + count) % count;
    }

    const api = { splitTagInput, applySuggestion, suggestUrl, moveSelection, SUGGEST_URL, DEFAULT_LIMIT };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    else root.TagAutocomplete = api;
})(typeof window !== "undefined" ? window : globalThis);
