/**
 * Pure state helpers for the paginated prompt and image lists.
 *
 * Loadable in the browser (window.ListState) and in Node for tests.
 */
(function (root) {
    "use strict";

    function nonNegativeInt(value) {
        return Number.isInteger(value) && value >= 0 ? value : null;
    }

    /**
     * Pagination state after a page fetch succeeded. Callers keep their current
     * state untouched while the request is in flight and only adopt the result.
     *
     * @param {{page: number, limit: number, total: number}} current
     * @param {{page?: number, limit?: number, total?: number}} response - the page
     *   that was requested and the total the server reported
     * @returns {{page: number, limit: number, total: number, totalPages: number}}
     */
    function nextPageState(current, response) {
        const limit = Number.isInteger(response.limit) && response.limit > 0 ? response.limit : current.limit;
        const total = nonNegativeInt(response.total) ?? current.total;
        const totalPages = Math.max(1, Math.ceil(total / limit));
        const requested = Number.isInteger(response.page) ? response.page : current.page;
        const page = Math.min(Math.max(1, requested), totalPages);
        return { page, limit, total, totalPages };
    }

    /** Whether an in-place prompt edit changed the text to something worth saving. */
    function shouldSaveEdit(originalText, newText) {
        if (typeof newText !== "string") return false;
        const trimmed = newText.trim();
        return trimmed !== "" && trimmed !== String(originalText ?? "").trim();
    }

    /**
     * Whether another page should be fetched from a capped list endpoint such as
     * /prompt_manager/images/all: the server said has_more and the last page was not empty.
     */
    function hasMorePages(response, receivedCount) {
        const pagination = response && response.pagination;
        return Boolean(pagination && pagination.has_more) && receivedCount > 0;
    }

    const api = { nextPageState, shouldSaveEdit, hasMorePages };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    else root.ListState = api;
})(typeof window !== "undefined" ? window : globalThis);
