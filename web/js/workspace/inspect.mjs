// The concept's "Inspect a file" page (docs/concepts/workspace/app.js renderMetadata 740–748, selection 1715–1729):
// one local file, chosen or dropped, previews inline; the heading's one action opens the shared viewer with the full
// inspector on a record read entirely in this browser. Nothing is uploaded, imported or stored. The selection, its
// blob: URL and its record belong to one local session (local-session.mjs) that ends with this page.
import { createButton, createScope } from '../shared/toolkit/core.js';
import { pageHeading } from './heading.mjs';
import { createWorkspaceIcon } from './icons.mjs';
import { createLocalSession, LOCAL_SESSION_MESSAGES } from './local-session.mjs';
import { localFileAdapter } from './local-inspection.mjs';
import { createMediaViewer } from './media-viewer.mjs';
import { showWorkspaceToast, TOAST_MS } from './toast.mjs';

/** D60/D61 copy (maintainer ruling LI-3). */
export const INSPECT_COPY = Object.freeze({
    action: 'Open file metadata',
    heading: 'Inspect without importing',
    intro: 'Read embedded metadata in this browser. The file is not uploaded or added to your library.',
    chooser: 'Choose an image or video for a local preview',
    note: 'Your file stays in this browser. Its metadata opens with “Open file metadata”.',
    toast: 'Local preview only. No file uploaded; metadata is available above.',
});
// D62: the concept's image list plus the videos the local readers support (AVI/WMV preview only).
export const INSPECT_ACCEPT = 'image/png,image/jpeg,image/webp,video/*,.mp4,.mov,.m4v,.webm,.mkv,.avi,.wmv';
const REJECTIONS = Object.freeze({
    'rejected-multiple': LOCAL_SESSION_MESSAGES.multiple,
    'rejected-directory': LOCAL_SESSION_MESSAGES.directory,
    'rejected-empty': LOCAL_SESSION_MESSAGES.unsupported,
});
// D63: the status line is an alert for anything the person must act on; AVI/WMV's note is plain status.
const ALERTS = new Set([LOCAL_SESSION_MESSAGES.multiple, LOCAL_SESSION_MESSAGES.directory, LOCAL_SESSION_MESSAGES.unsupported,
    LOCAL_SESSION_MESSAGES.previewFailed, LOCAL_SESSION_MESSAGES.unreadable]);

function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

// The status shown for the selection; AVI/WMV's note and an empty status keep the toast's "metadata is available".
const TOASTABLE = new Set(['', LOCAL_SESSION_MESSAGES.containerOnly]);
// How long a released video still starting may take to settle before its deferred reset is dropped.
const DEFERRED_RESET_MS = 15000;

/**
 * Resets a released video once it settles. Removing `src` alone never aborts a load already under way, and WebKit
 * (GStreamer) blocked the page when a pipeline still starting was reset, so load() waits for metadata or an error.
 * The fallback only drops the waiting listeners (a WebKit load cut off by the revoked URL stalls and never settles),
 * so the detached node is never pinned.
 */
function resetWhenSettled(node) {
    const waiting = new AbortController();
    const fallback = setTimeout(() => waiting.abort(), DEFERRED_RESET_MS);
    const reset = () => { waiting.abort(); clearTimeout(fallback); node.load(); };
    for (const type of ['loadedmetadata', 'error']) node.addEventListener(type, reset, { once: true, signal: waiting.signal });
}

/**
 * Stops a preview from holding its blob: URL: its own listeners end, media is paused and its src dropped before the
 * session revokes the URL, and the node leaves the page. A settled video (metadata read, or failed) is then reset with
 * load() to free its decoder at once; one still starting is reset as soon as it settles.
 */
function releaseMedia(preview) {
    if (!preview) return;
    const { node, listeners } = preview;
    listeners.abort();
    if (node instanceof HTMLMediaElement) {
        const settled = node.readyState >= HTMLMediaElement.HAVE_METADATA || node.error !== null;
        node.pause(); node.removeAttribute('src');
        if (settled) node.load();
        else resetWhenSettled(node);
    } else node.removeAttribute('src');
    node.remove();
}

/**
 * Mounts the page into `main` below the heading the shell rendered. `sender` is the shared prompt sender; `api` is
 * only handed to the viewer (the local adapter never calls it). Returns `{dispose}`; the page also ends with
 * `parentScope`.
 */
export function mountInspect(main, { parentScope, api, sender }) {
    const scope = createScope();
    const dispose = () => scope.dispose();
    if (parentScope.signal.aborted) { scope.dispose(); return { dispose }; }
    parentScope.signal.addEventListener('abort', dispose, { once: true });
    scope.cleanup(() => parentScope.signal.removeEventListener('abort', dispose));

    // The concept DOM: section.surface > h2, p; div.file-drop.spaced-20 > icon, label, input, span.small; #local-preview.
    const surface = element('section', 'surface');
    const area = element('div', 'file-drop spaced-20');
    const label = element('label', undefined, INSPECT_COPY.chooser);
    label.htmlFor = 'local-file';
    const input = element('input');
    input.id = 'local-file'; input.type = 'file'; input.accept = INSPECT_ACCEPT;
    const note = element('span', 'small', INSPECT_COPY.note);
    note.id = 'local-file-note';
    area.append(createWorkspaceIcon('upload'), label, input, note);
    const preview = element('div');
    preview.id = 'local-preview';
    surface.append(element('h2', undefined, INSPECT_COPY.heading), element('p', undefined, INSPECT_COPY.intro), area, preview);
    main.append(surface);

    // /* D60 */ the concept's one primary heading action, unavailable until a classified file is chosen.
    const open = createButton(INSPECT_COPY.action, 'ca-primary');
    pageHeading(main)?.actions.append(open);
    scope.cleanup(() => open.remove());

    // The session ends after the viewer has closed and the inline media is gone, so no element holds its URL then.
    // The page owns page leave for the same reason: the session listens for no `pagehide` of its own.
    const sessionScope = createScope();
    const session = createLocalSession({ parentScope: sessionScope, pageTarget: null });
    const viewer = createMediaViewer({ api, container: main, parentScope: scope, inspectionController: session.controller, sender,
        adapter: localFileAdapter, onRestoreFocus: () => { if (open.isConnected) open.focus({ preventScroll: true }); } });

    let media = null, mediaUrl = null, statusLine = null, rejectionLine = null, rejectionTimer = 0, announced = null, chosen = null;
    function statusElement(message) {
        // /* D63 */ one status line for the states the mock never shows.
        const line = element('p', 'small spaced-16', message);
        line.setAttribute('role', ALERTS.has(message) ? 'alert' : 'status');
        return line;
    }
    function showStatus(message) {
        statusLine?.remove(); statusLine = null;
        if (!message) return;
        statusLine = statusElement(message);
        preview.insertBefore(statusLine, rejectionLine);
    }
    function clearRejection() {
        clearTimeout(rejectionTimer); rejectionTimer = 0;
        rejectionLine?.remove(); rejectionLine = null;
    }
    // /* D63 */ a rejected chooser or drop keeps the selection and its status: its alert is a second line that ends
    // with the next selection attempt or after the concept's toast interval.
    function showRejection(message) {
        clearRejection();
        rejectionLine = statusElement(message);
        preview.append(rejectionLine);
        rejectionTimer = setTimeout(clearRejection, TOAST_MS);
    }
    scope.cleanup(clearRejection);
    /** Keeps the chooser naming the current selection; assigning `files` fires no `change`. */
    function nameChosen() {
        if (!chosen) return;
        const transfer = new DataTransfer();
        transfer.items.add(chosen);
        input.files = transfer.files;
    }
    function setAvailable(available) {
        open.setAttribute('aria-disabled', String(!available));
        if (available) open.removeAttribute('aria-describedby');
        else open.setAttribute('aria-describedby', note.id);
    }
    function showMedia(state) {
        const url = state.preview?.url ?? null;
        if (url === mediaUrl) return;
        releaseMedia(media); media = null; mediaUrl = url;
        if (!url) return;
        const video = state.preview.media === 'video';
        const node = element(video ? 'video' : 'img', 'local-file-preview');
        // Each preview owns its listeners, so a released node is never held by the page.
        const current = { node, listeners: new AbortController() };
        if (video) {
            // /* D62 */ native controls, no inline autoplay.
            node.controls = true; node.playsInline = true; node.preload = 'metadata'; node.muted = true;
        } else {
            node.alt = 'Local file preview';
        }
        // /* D65 */ the URL is not revoked on load: the viewer and Download original use it.
        // The listener ends with the node's release, so it only fires for the current preview.
        node.addEventListener('error', () => {
            releaseMedia(current); media = null;
            showStatus(LOCAL_SESSION_MESSAGES.previewFailed);
        }, { once: true, signal: current.listeners.signal });
        node.src = url;
        media = current;
        preview.prepend(node);
    }
    scope.cleanup(session.subscribe(state => {
        showMedia(state);
        showStatus(state.message);
        const entry = session.entry;
        setAvailable(state.phase === 'ready' && !!entry);
        if (state.phase === 'ready' && entry && announced !== entry.key) {
            announced = entry.key;
            // An unreadable or failed preview says so in its alert; the toast never claims metadata is available.
            if (TOASTABLE.has(state.message)) showWorkspaceToast(INSPECT_COPY.toast);
        }
    }));
    // A replacement or page leave revokes the URL after this subscriber dropped the media; a dispose closes the
    // viewer (on abort), drops the media, then ends the session.
    scope.cleanup(() => viewer.dispose());
    scope.cleanup(() => { releaseMedia(media); media = null; mediaUrl = null; });
    scope.cleanup(() => sessionScope.dispose());

    // Page leave: the viewer closes and the inline media goes before the session revokes the URL, and the chooser is
    // cleared, so a page restored from the back-forward cache never names a file beside the empty selection.
    function leave() {
        clearRejection();
        viewer.close();
        releaseMedia(media); media = null; mediaUrl = null;
        session.clear();
        chosen = null; input.value = '';
    }
    scope.listen(window, 'pagehide', leave);

    function choose(source, file) {
        clearRejection();
        const outcome = session.select(source);
        if (outcome === 'accepted') chosen = file;
        if (REJECTIONS[outcome]) showRejection(REJECTIONS[outcome]);
        return outcome;
    }
    // The chooser keeps showing the chosen name, as in the concept; it is cleared as the picker opens, so choosing
    // the same file again is a new selection, and named again when the picker is cancelled.
    scope.listen(input, 'click', () => { clearRejection(); input.value = ''; });
    scope.listen(input, 'cancel', () => nameChosen());
    scope.listen(input, 'change', () => {
        // A cancelled chooser carries no files: the page stays as it is.
        if (!input.files?.length) { choose(null); nameChosen(); return; }
        choose(input.files, input.files[0]);
    });
    // /* D64 */ drop on the area, with no visual change; only `dataTransfer.files` is read, never its strings.
    scope.listen(area, 'dragover', event => {
        event.preventDefault();
        if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
    });
    scope.listen(area, 'drop', event => {
        event.preventDefault();
        if (document.querySelector('dialog[open]')) return;
        const transfer = event.dataTransfer;
        // A dropped file replaces the chosen one, so the chooser names the dropped file, never the old one.
        if (choose(transfer ? { files: transfer.files, items: transfer.items } : null, transfer?.files?.[0]) === 'accepted') nameChosen();
    });
    // A drop that misses the area never navigates away from the page while it is mounted.
    for (const type of ['dragover', 'drop']) scope.listen(window, type, event => event.preventDefault());
    scope.listen(open, 'click', () => {
        const entry = session.entry;
        if (open.getAttribute('aria-disabled') === 'true' || !entry || session.state.phase !== 'ready') return;
        viewer.open({ items: [entry], selectedKey: entry.key, trigger: open });
    });
    return { dispose };
}
