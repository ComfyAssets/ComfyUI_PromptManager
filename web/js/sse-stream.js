/**
 * Server-sent events over fetch.
 *
 * EventSource can only GET. The auto-tag download/start and thumbnail
 * generation endpoints are POST (they have side effects), so their progress
 * streams are read with fetch and a small parser. `connect()` returns an
 * object with the EventSource surface the pages already use: onmessage,
 * onerror, onclose, readyState, close(), and addEventListener(name, fn) for
 * named `event:` frames.
 *
 * Loadable in the browser (window.SseStream) and in Node for tests.
 */
(function (root) {
    "use strict";

    const CONNECTING = 0;
    const OPEN = 1;
    const CLOSED = 2;

    /**
     * Parse complete events out of a buffer of SSE text.
     * @param {string} buffer
     * @returns {{events: Array<{data: string, event?: string}>, rest: string}} parsed
     *   events and the trailing, still incomplete block
     */
    function parseSseChunk(buffer) {
        const normalized = buffer.replace(/\r\n/g, "\n");
        const blocks = normalized.split("\n\n");
        const rest = blocks.pop();
        const events = [];
        for (const block of blocks) {
            const event = parseBlock(block);
            if (event) events.push(event);
        }
        return { events, rest };
    }

    function parseBlock(block) {
        const data = [];
        let name;
        for (const line of block.split("\n")) {
            if (line === "" || line.startsWith(":")) continue;
            const colon = line.indexOf(":");
            const field = colon === -1 ? line : line.slice(0, colon);
            let value = colon === -1 ? "" : line.slice(colon + 1);
            if (value.startsWith(" ")) value = value.slice(1);
            if (field === "data") data.push(value);
            else if (field === "event") name = value;
        }
        if (data.length === 0) return null;
        const event = { data: data.join("\n") };
        if (name !== undefined) event.event = name;
        return event;
    }

    /**
     * @param {string} url
     * @param {{method?: string, body?: string|FormData, fetchImpl?: typeof fetch}} [options]
     * @returns {{onmessage: Function|null, onerror: Function|null, onclose: Function|null,
     *   readyState: number, close: Function, CONNECTING: number, OPEN: number, CLOSED: number}}
     */
    function connect(url, { method = "GET", body, fetchImpl } = {}) {
        const doFetch = fetchImpl || ((...args) => globalThis.fetch(...args));
        const controller = new AbortController();
        const listeners = new Map();
        const source = {
            onmessage: null,
            onerror: null,
            onclose: null,
            readyState: CONNECTING,
            CONNECTING,
            OPEN,
            CLOSED,
            close() {
                if (source.readyState === CLOSED) return;
                source.readyState = CLOSED;
                controller.abort();
            },
            /** Named `event:` frames reach listeners for that name; unnamed ones reach "message". */
            addEventListener(name, listener) {
                if (!listeners.has(name)) listeners.set(name, new Set());
                listeners.get(name).add(listener);
            },
            removeEventListener(name, listener) {
                const set = listeners.get(name);
                if (set) set.delete(listener);
            },
        };

        function deliver(event) {
            if (source.onmessage) source.onmessage(event);
            const set = listeners.get(event.event || "message");
            if (!set) return;
            for (const listener of Array.from(set)) {
                if (source.readyState === CLOSED) return;
                listener(event);
            }
        }

        (async () => {
            try {
                const init = { method, headers: { Accept: "text/event-stream" }, signal: controller.signal };
                if (body !== undefined) init.body = body;
                const response = await doFetch(url, init);
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                if (source.readyState === CLOSED) return;
                source.readyState = OPEN;

                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = "";
                for (;;) {
                    const { value, done } = await reader.read();
                    if (source.readyState === CLOSED) return;
                    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
                    const { events, rest } = parseSseChunk(buffer);
                    buffer = rest;
                    for (const event of events) {
                        if (source.readyState === CLOSED) return;
                        deliver(event);
                    }
                    if (done) break;
                }
                source.readyState = CLOSED;
                if (source.onclose) source.onclose();
            } catch (error) {
                if (source.readyState === CLOSED) return; // closed by the caller
                source.readyState = CLOSED;
                if (source.onerror) source.onerror(error);
            }
        })();

        return source;
    }

    const api = { parseSseChunk, connect, CONNECTING, OPEN, CLOSED };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    else root.SseStream = api;
})(typeof window !== "undefined" ? window : globalThis);
