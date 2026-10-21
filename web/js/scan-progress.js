/**
 * State of the output-image scan as seen by the dashboard.
 *
 * The scan runs on the server as a background job (see py/api/jobs.py) and
 * reports progress over SSE. This module turns those events into a small
 * immutable state object and answers the UI questions that depend on it:
 * what dismissing the modal should do, what the minimized pill should say, and
 * how to rebuild the view from GET /prompt_manager/scan/status after a reload.
 *
 * Loadable in the browser (window.ScanProgress) and in Node for tests.
 */
(function (root) {
    "use strict";

    const IDLE = "idle";
    const RUNNING = "running";
    const DONE = "done";
    const FAILED = "failed";

    function initialState() {
        return {
            status: IDLE,
            progress: 0,
            statusText: "",
            processed: 0,
            found: 0,
            added: 0,
            linked: 0,
            message: "",
        };
    }

    function num(value, fallback) {
        return typeof value === "number" && Number.isFinite(value) ? value : fallback;
    }

    function text(value, fallback) {
        return typeof value === "string" ? value : fallback;
    }

    /**
     * @param {object} state current state (never mutated)
     * @param {object} event a decoded SSE payload: {type: "progress"|"complete"|"error", ...}
     * @returns {object} the next state, or `state` itself when the event changes nothing
     */
    function reduceScanEvent(state, event) {
        if (!event || typeof event !== "object") return state;
        if (event.type === "progress") {
            return {
                ...state,
                status: RUNNING,
                progress: num(event.progress, state.progress),
                statusText: text(event.status, state.statusText),
                processed: num(event.processed, state.processed),
                found: num(event.found, state.found),
            };
        }
        if (event.type === "complete" || event.type === "cancelled") {
            const counts = Object.fromEntries(
                ['pairs_added','pair_links_added','enriched','unchanged','ambiguous','unresolved','conflict','failed']
                    .map(key => [key, num(event[key], state[key] || 0)])
            );
            return {
                ...state,
                ...counts,
                status: DONE,
                cancelled: event.type === "cancelled",
                progress: event.type === "cancelled" ? state.progress : 100,
                statusText: event.type === "cancelled" ? "Scan cancelled." : "Scan completed!",
                processed: num(event.processed, state.processed),
                found: num(event.found, state.found),
                added: num(event.added, state.added),
                linked: num(event.linked, state.linked),
            };
        }
        if (event.type === "error") {
            return { ...state, status: FAILED, message: text(event.message, "Scan failed") };
        }
        return state;
    }

    /** Rebuild the state from the /prompt_manager/scan/status payload. */
    function fromStatus(status) {
        if (!status || !status.running) return initialState();
        const state = reduceScanEvent(initialState(), status.last_event);
        if (state.status !== IDLE) return state;
        return { ...state, status: RUNNING, statusText: "Scanning..." };
    }

    /** Closing a running scan's modal only hides it; the scan continues. */
    function dismissAction(state) {
        return state && state.status === RUNNING ? "minimize" : "close";
    }

    /** Label for the minimized progress pill; empty when nothing is running. */
    function progressLabel(state) {
        if (!state || state.status !== RUNNING) return "";
        const pct = Math.round(state.progress);
        return `Scanning ${pct}% · ${state.processed} files · ${state.found} prompts`;
    }

    function completionLabel(state) {
        const review = (state.ambiguous || 0) + (state.unresolved || 0) + (state.conflict || 0);
        return `${state.cancelled ? 'Scan cancelled' : 'Scan completed'}! Processed ${state.processed} files, found ${state.found} prompts; ${state.added} new image records, ${state.linked} links, ${state.enriched || 0} enriched, ${state.unchanged || 0} unchanged; ${state.pairs_added || 0} new pairs, ${state.pair_links_added || 0} pair links; ${review} need review, ${state.failed || 0} failed.`;
    }

    const api = {
        initialState,
        completionLabel,
        reduceScanEvent,
        fromStatus,
        dismissAction,
        progressLabel,
        IDLE,
        RUNNING,
        DONE,
        FAILED,
    };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    else root.ScanProgress = api;
})(typeof window !== "undefined" ? window : globalThis);
