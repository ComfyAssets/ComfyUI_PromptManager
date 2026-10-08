/**
 * HTML escaping for the admin, gallery and metadata pages.
 *
 * Escapes the five characters that matter in both text and attribute context
 * (& < > " '), so one function is safe for `innerHTML` text nodes and for
 * `data-*` / `value` / `title` attributes alike. The old `textContent` ->
 * `innerHTML` trick left quotes untouched and let a tag such as
 * `" onmouseover="alert(1)` break out of an attribute.
 *
 * Loadable in the browser (window.escapeHtml / window.HtmlEscape) and in Node
 * for tests.
 */
(function (root) {
    "use strict";

    const REPLACEMENTS = Object.freeze({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
    });
    const UNSAFE = /[&<>"']/g;

    /**
     * @param {unknown} value - anything; null/undefined become "", others go through String()
     * @returns {string} text safe to interpolate into HTML text or a quoted attribute
     */
    function escapeHtml(value) {
        if (value === null || value === undefined) return "";
        return String(value).replace(UNSAFE, (ch) => REPLACEMENTS[ch]);
    }

    const api = { escapeHtml, escapeAttr: escapeHtml };
    if (typeof module !== "undefined" && module.exports) {
        module.exports = api;
    } else {
        root.HtmlEscape = api;
        root.escapeHtml = escapeHtml;
        root.escapeAttr = escapeHtml;
    }
})(typeof window !== "undefined" ? window : globalThis);
