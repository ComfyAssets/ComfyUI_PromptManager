/**
 * Prompt list sorting for the admin dashboard (3.2.4, #90.1).
 *
 * Sorting happens on the server so it spans every page; this module only owns the
 * option list, validation and request URLs. The option values must match
 * SORT_ORDERS in database/operations.py (enforced by tests/test_sort_contract.py).
 *
 * Loadable in the browser (window.PromptListSort) and in Node for tests.
 */
(function (root) {
    "use strict";

    const SORT_OPTIONS = Object.freeze(
        [
            { value: "last_used_desc", label: "Recently Used" },
            { value: "created_desc", label: "Newest First" },
            { value: "created_asc", label: "Oldest First" },
            { value: "run_count_desc", label: "Most Used" },
            { value: "rating_desc", label: "Highest Rated" },
            { value: "rating_asc", label: "Lowest Rated" },
            { value: "text_asc", label: "A-Z" },
            { value: "text_desc", label: "Z-A" },
        ].map((option) => Object.freeze(option)),
    );

    const DEFAULT_SORT = "last_used_desc";
    const DEFAULT_LIMIT = 50;
    const KNOWN = new Set(SORT_OPTIONS.map((o) => o.value));

    /** A known sort key, or DEFAULT_SORT for anything else. */
    function normalizeSort(value) {
        return typeof value === "string" && KNOWN.has(value) ? value : DEFAULT_SORT;
    }

    /** URL for one page of /prompt_manager/recent. */
    function buildRecentUrl({ page = 1, limit = DEFAULT_LIMIT, sort } = {}) {
        const params = new URLSearchParams({
            limit: String(limit),
            offset: String((page - 1) * limit),
            page: String(page),
            sort: normalizeSort(sort),
        });
        return `/prompt_manager/recent?${params}`;
    }

    /** Copy of `params` with a validated sort key; the input is not modified. */
    function withSort(params, sort) {
        const copy = new URLSearchParams(params);
        copy.set("sort", normalizeSort(sort));
        return copy;
    }

    /** Badge text for a prompt's run count; empty when the count is unknown. */
    function formatRunCount(count) {
        if (!Number.isInteger(count) || count < 0) return "";
        if (count === 0) return "Never run";
        if (count === 1) return "1 run";
        return `${count.toLocaleString("en-US")} runs`;
    }

    const api = { SORT_OPTIONS, DEFAULT_SORT, normalizeSort, buildRecentUrl, withSort, formatRunCount };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    else root.PromptListSort = api;
})(typeof window !== "undefined" ? window : globalThis);
