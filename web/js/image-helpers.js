/**
 * Pure helpers for image entries rendered by the admin and gallery pages.
 *
 * Image rows come from the database (`width`, `height`, `file_size` are stored
 * from a POST body and may be strings) or from the output-folder scan (`id` is
 * a path digest, `path` is the file). Everything these helpers return for HTML
 * is already escaped, so callers can drop it into text or a quoted attribute.
 *
 * Loadable in the browser (window.ImageHelpers) and in Node for tests.
 */
(function (root) {
    "use strict";

    const escapeHtml =
        typeof module !== "undefined" && module.exports
            ? require("./html-escape.js").escapeHtml
            : root.escapeHtml;

    const UNKNOWN_SIZE = "Unknown size";

    /**
     * "640×480 | 1.2 MB" for a card body or a viewer caption, escaped.
     *
     * @param {{width?: unknown, height?: unknown, file_size?: unknown}|null|undefined} image
     * @param {{formatFileSize?: (bytes: number) => string, separator?: string}} [options]
     * @returns {string} escaped text; "Unknown size" when a dimension is missing
     */
    function formatImageCaption(image, { formatFileSize, separator = " | " } = {}) {
        const entry = image || {};
        const parts = [];
        if (entry.width && entry.height) {
            parts.push(`${escapeHtml(entry.width)}×${escapeHtml(entry.height)}`);
        } else {
            parts.push(UNKNOWN_SIZE);
        }
        const bytes = Number(entry.file_size);
        if (formatFileSize && Number.isFinite(bytes) && bytes > 0) {
            parts.push(escapeHtml(formatFileSize(bytes)));
        }
        return parts.join(escapeHtml(separator));
    }

    /**
     * A complete standalone page showing a workflow as pretty-printed JSON.
     * Written into a blank same-origin window, so the JSON must not be able
     * to close the <pre> and inject markup.
     *
     * @param {unknown} workflow
     * @returns {string} HTML document
     */
    function workflowDocumentHtml(workflow) {
        const json = escapeHtml(JSON.stringify(workflow, null, 2));
        return [
            "<!DOCTYPE html>",
            "<html>",
            "<head><meta charset=\"utf-8\"><title>ComfyUI Workflow Data</title></head>",
            "<body style=\"background: #111; color: #fff; font-family: monospace; padding: 20px;\">",
            "<h2>ComfyUI Workflow JSON</h2>",
            `<pre style="background: #222; padding: 15px; border-radius: 5px; overflow: auto;">${json}</pre>`,
            "</body>",
            "</html>",
        ].join("\n");
    }

    /**
     * Body for POST /prompt_manager/autotag/single.
     *
     * Database rows (admin review, /images/all) have an integer id the server
     * looks up; output-folder entries (gallery review, /images/output) have a
     * path digest as id, which would 404, so those are addressed by path.
     *
     * @param {{id?: unknown, path?: unknown}} image
     * @param {{modelType: string, generalThreshold?: number, characterThreshold?: number, prompt?: string}} settings
     * @returns {object} JSON-serialisable request body
     */
    function autotagSingleBody(image, settings) {
        const entry = image || {};
        const target =
            typeof entry.path === "string" && entry.path && !Number.isInteger(entry.id)
                ? { path: entry.path }
                : { image_id: entry.id };
        const modelType = String(settings.modelType || "");
        const params = modelType.startsWith("wd14")
            ? {
                  general_threshold: settings.generalThreshold,
                  character_threshold: settings.characterThreshold,
              }
            : { prompt: settings.prompt };
        return { ...target, model_type: modelType, ...params };
    }

    const api = { formatImageCaption, workflowDocumentHtml, autotagSingleBody };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    else root.ImageHelpers = api;
})(typeof window !== "undefined" ? window : globalThis);
