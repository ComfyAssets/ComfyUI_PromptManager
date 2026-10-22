// One local selection at a time, owned end to end: the chosen File is classified
// by its bytes, a blob: URL is created only for a classified file, the inspection
// controller reads it through localFileAdapter, and replacement, dispose, parent
// abort and page leave each release the URL, the handle and the record exactly once.
import { createInspectionController } from './inspection-state.mjs';
import { createLocalFileHandle, localFileAdapter, releaseLocalFileHandle } from './local-inspection.mjs';

const PREFIX_BYTES = 32;
// Kinds whose metadata the server never reads (AVI, ASF/WMV): preview and original only.
const CONTAINER_ONLY = new Set(['avi', 'asf']);
/** D63 status copy; the page shows `multiple` and `directory` for the matching select() outcomes. */
export const LOCAL_SESSION_MESSAGES = Object.freeze({
    multiple: 'Drop one file at a time.',
    directory: 'Folders cannot be inspected. Choose one image or video file.',
    unsupported: 'This file type cannot be previewed or inspected here. Choose an image or video file.',
    previewFailed: 'The preview could not be displayed. Metadata and the original remain available in the viewer.',
    containerOnly: 'Metadata in AVI and WMV files is not read here. The preview and the original remain available.',
});

const abortError = () => new DOMException('The local selection was replaced', 'AbortError');
const defaultSchedulerYield = () => (typeof globalThis.scheduler?.yield === 'function' ? () => globalThis.scheduler.yield() : null);
const pngReader = () => globalThis.PngMetadata || globalThis.window?.PngMetadata;
const EMPTY = Object.freeze({ phase: 'empty', preview: null, message: '' });

/**
 * `yieldControl(signal)`: one turn of the event loop. It prefers `scheduler.yield`;
 * otherwise a zero timer that, on abort, is cleared and rejects with AbortError and
 * removes its abort listener. Rejects at once for an aborted signal.
 */
export function createYield({ schedulerYield = defaultSchedulerYield(), setTimer = setTimeout, clearTimer = clearTimeout } = {}) {
    return function yieldControl(signal) {
        if (signal?.aborted) return Promise.reject(abortError());
        if (typeof schedulerYield === 'function') {
            return Promise.resolve(schedulerYield()).then(() => { if (signal?.aborted) throw abortError(); });
        }
        return new Promise((resolve, reject) => {
            const onAbort = () => { clearTimer(timer); reject(abortError()); };
            const timer = setTimer(() => { signal?.removeEventListener('abort', onAbort); resolve(); }, 0);
            signal?.addEventListener('abort', onAbort, { once: true });
        });
    };
}

/** The one File a chooser or drop carries, or the select() outcome that keeps the previous selection. */
function chosenFile(input) {
    if (input === null || input === undefined) return { outcome: 'ignored' };
    const items = input.items ? [...input.items].filter(item => item?.kind === 'file') : null;
    const files = [...(input.files ?? input)].filter(file => file instanceof Blob);
    const count = Math.max(files.length, items?.length ?? 0);
    if (count === 0) return { outcome: 'rejected-empty' };
    if (count > 1) return { outcome: 'rejected-multiple' };
    if (items?.some(item => item.webkitGetAsEntry?.()?.isDirectory)) return { outcome: 'rejected-directory' };
    return files.length === 1 ? { file: files[0] } : { outcome: 'rejected-empty' };
}

export function createLocalSession({
    parentScope, createObjectURL = file => URL.createObjectURL(file), revokeObjectURL = url => URL.revokeObjectURL(url),
    schedulerYield = defaultSchedulerYield(), setTimer = setTimeout, clearTimer = clearTimeout,
    pageTarget = globalThis.window, extractFacts, pngMetadata,
} = {}) {
    const controller = createInspectionController({ api: null, adapter: localFileAdapter, extractFacts });
    const listeners = new Set();
    let state = EMPTY, handle = null, url = null, operation = null, generation = 0, disposed = false;
    const yieldControl = createYield({ schedulerYield, setTimer, clearTimer });

    function publish(update) {
        if (disposed) return;
        state = Object.freeze({ ...EMPTY, ...update });
        for (const listener of [...listeners]) listener(state);
    }
    /** Ends the current selection; idempotent. Subscribers drop the preview before its URL is revoked. */
    function release(next) {
        generation++;
        operation?.abort(); operation = null;
        if (handle) { releaseLocalFileHandle(handle); handle = null; }
        controller.clear();
        const previous = url; url = null;
        if (next) publish(next);
        if (previous) revokeObjectURL(previous);
    }
    const live = (token, signal) => !disposed && token === generation && !signal.aborted;

    async function classify(file, token, signal) {
        try {
            await yieldControl(signal);
            if (!live(token, signal)) return;
            const prefix = new Uint8Array(await file.slice(0, Math.min(PREFIX_BYTES, file.size)).arrayBuffer());
            if (!live(token, signal)) return;
            const reader = pngMetadata ?? pngReader();
            const classification = prefix.length ? reader.classifyPrefix(prefix) : null;
            if (!classification) { publish({ phase: 'unsupported', message: LOCAL_SESSION_MESSAGES.unsupported }); return; }
            // The URL exists only for a classified file, and is never revoked on load: the viewer and Download original use it.
            let created = null;
            try { created = createObjectURL(file); } catch { created = null; }
            url = typeof created === 'string' && created.startsWith('blob:') ? created : null;
            if (created && !url) revokeObjectURL(created);
            handle = createLocalFileHandle({ file, url, classification });
            const message = !url ? LOCAL_SESSION_MESSAGES.previewFailed : CONTAINER_ONLY.has(classification.kind) ? LOCAL_SESSION_MESSAGES.containerOnly : '';
            publish({ phase: 'ready', preview: url ? Object.freeze({ url, media: classification.media }) : null, message });
            controller.select(handle);
        } catch (error) {
            if (!live(token, signal) || error?.name === 'AbortError') return;
            publish({ phase: 'unsupported', message: LOCAL_SESSION_MESSAGES.unsupported });
        }
    }

    function dispose() {
        if (disposed) return;
        release(EMPTY);
        disposed = true;
        listeners.clear();
        controller.dispose();
        pageTarget?.removeEventListener?.('pagehide', leave);
        parentScope?.signal?.removeEventListener('abort', dispose);
    }
    const leave = () => { if (!disposed && (handle || url || operation || state.phase !== 'empty')) release(EMPTY); };

    if (parentScope?.signal?.aborted) {
        disposed = true;
        controller.dispose();
    } else {
        parentScope?.signal?.addEventListener('abort', dispose, { once: true });
        pageTarget?.addEventListener?.('pagehide', leave);
    }

    return {
        get state() { return state; },
        get entry() { return handle; },
        controller,
        select(input) {
            if (disposed) return 'ignored';
            const { file, outcome } = chosenFile(input);
            if (!file) return outcome;
            release({ phase: 'classifying' });
            const current = operation = new AbortController();
            classify(file, generation, current.signal);
            return 'accepted';
        },
        retry() {
            if (disposed || !handle) return undefined;
            return controller.retry();
        },
        subscribe(listener) {
            if (disposed) return () => {};
            listeners.add(listener); listener(state);
            return () => listeners.delete(listener);
        },
        dispose,
    };
}
