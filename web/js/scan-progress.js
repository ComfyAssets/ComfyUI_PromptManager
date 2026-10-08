/**
 * State of the output-image scan as seen by the dashboard.
 *
 * The scan runs on the server as a background job (see py/api/scan_job.py) and
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
        if (event.type === "complete") {
            return {
                ...state,
                status: DONE,
                progress: 100,
                statusText: "Scan completed!",
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

    const api = {
        initialState,
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
