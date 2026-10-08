/**
 * Dispatch for elements rendered with `data-action="..."`.
 *
 * The admin and gallery pages build HTML from server data with template
 * literals. Inline `onclick="...${value}..."` handlers would put that data
 * inside JavaScript source, where HTML escaping does not help; instead the
 * markup carries `data-*` attributes and one delegated click listener per page
 * calls `dispatch(element.dataset, handlers, element)`.
 *
 * Loadable in the browser (window.DataActions) and in Node for tests.
 */
(function (root) {
    "use strict";

    function toInt(value) {
        if (value === undefined || value === null || value === "") return null;
        const n = parseInt(value, 10);
        return Number.isNaN(n) ? null : n;
    }

    /**
     * @param {DOMStringMap|Object|null|undefined} dataset
     * @returns {{action: string, promptId: number|null, index: number|null} & Object<string, string>}
     */
    function readAction(dataset) {
        const data = dataset || {};
        return {
            ...data,
            action: typeof data.action === "string" ? data.action : "",
            promptId: toInt(data.promptId),
            index: toInt(data.index),
        };
    }

    /**
     * @param {DOMStringMap|Object} dataset - the clicked element's dataset
     * @param {Object<string, Function>} handlers - handler per action name
     * @param {...*} extra - passed to the handler after the parsed action
     * @returns {boolean} whether a handler ran
     */
    function dispatch(dataset, handlers, ...extra) {
        const parsed = readAction(dataset);
        if (!parsed.action || !Object.prototype.hasOwnProperty.call(handlers, parsed.action)) return false;
        handlers[parsed.action](parsed, ...extra);
        return true;
    }

    const api = { readAction, dispatch };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    else root.DataActions = api;
})(typeof window !== "undefined" ? window : globalThis);
