// The concept's "Inspect a file" page (docs/concepts/workspace/app.js renderMetadata 740–748, selection 1715–1729):
// one local file, chosen or dropped, previews inline and (D155, #448) opens the shared viewer with its details showing
// at once; the heading's one action opens it again. The record is read entirely in this browser. Nothing is uploaded, imported or stored. The selection, its
// blob: URL and its record belong to one local session (local-session.mjs) that ends with this page.
import { createButton, createScope } from '../shared/toolkit/core.js';
import { pageHeading } from './heading.mjs';
import { createWorkspaceIcon } from './icons.mjs';
import { createLocalSession, LOCAL_SESSION_MESSAGES } from './local-session.mjs';
import { localFileAdapter } from './local-inspection.mjs';
import { loadInspectionSemantics } from './local-semantics.mjs';
import { createMediaViewer } from './media-viewer.mjs';
import { showWorkspaceToast, TOAST_MS } from './toast.mjs';

/** D60/D61 copy (maintainer ruling LI-3). */
export const INSPECT_COPY = Object.freeze({
    action: 'Open file metadata',
    heading: 'Inspect without importing',
    intro: 'Read embedded metadata in this browser. The file is not uploaded or added to your library.',
    chooser: 'Choose an image or video for a local preview',
    // D155 (#448): choosing a file opens its metadata (D61's note named the heading action).
    note: 'Your file stays in this browser. Choosing it opens its metadata.',
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

// D155 (#448): a selection with no status, or one without a preview URL (its copy points at the viewer), opens the viewer
// at once. AVI/WMV's note keeps the toast instead (the viewer could not play it and reads no metadata), and an
// unreadable file stays on the page with its alert; the heading action opens either.
const AUTO_OPEN = new Set(['', LOCAL_SESSION_MESSAGES.previewFailed]);
const TOASTABLE = new Set([LOCAL_SESSION_MESSAGES.containerOnly]);
// How long a replaced video still starting may stay in place before it is released anyway.
const STARTING_VIDEO_MS = 15000;

/** A video whose pipeline is still starting: it has a source but neither metadata nor an error yet. */
function isStarting(node) {
    return node instanceof HTMLMediaElement && node.hasAttribute('src') && node.readyState < HTMLMediaElement.HAVE_METADATA && node.error === null;
}

/**
 * Stops a preview from holding its blob: URL: its own listeners end, media is paused, its src dropped and it is reset
 * with load() (which frees its decoder), and the node leaves the page, all before its URL is revoked.
 */
function releaseMedia(preview) {
    if (!preview) return;
    const { node, listeners } = preview;
    listeners.abort();
    if (node instanceof HTMLMediaElement) {
        node.pause(); node.removeAttribute('src'); node.load();
    } else node.removeAttribute('src');
    node.remove();
}

/**
 * Mounts the page into `main` below the heading the shell rendered. `sender` is the shared prompt sender; `api` is
 * only handed to the viewer (the local adapter never calls it). Returns `{dispose, leaveGuard}` (`{dispose}` when
 * `parentScope` has already ended); the page also ends with `parentScope`. `leaveGuard()` is a promise while a video
 * on the page is still starting (D62): the shell renders the next route once it settles, since detaching it then
 * deadlocked WebKit.
 */
export function mountInspect(main, { parentScope, api, sender }) {
    const scope = createScope();
    const dispose = () => scope.dispose();
    if (parentScope.signal.aborted) { scope.dispose(); return { dispose }; }
    parentScope.signal.addEventListener('abort', dispose, { once: true });
    scope.cleanup(() => parentScope.signal.removeEventListener('abort', dispose));
    // The node table the local roles resolve with, read before any file is chosen (one request per page).
    loadInspectionSemantics();

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
    // Replaced videos still starting, by URL. WebKit (GStreamer) deadlocked the page when a video left the page or
    // the viewport before its pipeline had started, so such a video stays where it is, inert, until it reports
    // metadata or an error; only then is it released and its URL, which the session asked to revoke, revoked.
    const retiring = new Map();
    function finishRetiring(url) {
        const entry = retiring.get(url);
        if (!entry) return;
        retiring.delete(url);
        entry.waiting.abort(); clearTimeout(entry.timer);
        releaseMedia(entry.preview);
        if (entry.revoke) revokeObjectURL(url);
        entry.done();
    }
    // #483 (D62): the viewer's own video, when a new selection closes the viewer while that video is still starting.
    // The viewer stays open until the video reports metadata or an error (or D62's 15 s), then closes; its URL, which
    // the session asked to revoke, goes after that. A viewer asked for meanwhile opens then: only the latest request,
    // and only if its selection is still current (an automatic open waits for its own inline video again).
    let held = null;
    function closeViewer() {
        const node = document.querySelector('#viewer-dialog video');
        if (!node || !isStarting(node)) { viewer.close(); return; }
        const waiting = new AbortController();
        held = { url: node.getAttribute('src'), revoke: false, waiting, pending: null, timer: setTimeout(finishHeld, STARTING_VIDEO_MS) };
        for (const type of ['loadedmetadata', 'error']) node.addEventListener(type, finishHeld, { once: true, signal: waiting.signal });
    }
    function endHeld() {
        const entry = held;
        if (!entry) return null;
        held = null;
        entry.waiting.abort(); clearTimeout(entry.timer);
        return entry;
    }
    function finishHeld() {
        const entry = endHeld();
        if (!entry) return;
        viewer.close();
        // Through the session's hook, so an inline video still starting on the same URL keeps it until it settles.
        if (entry.revoke) revokeObjectURL(entry.url);
        const request = entry.pending;
        if (!request || scope.signal.aborted || session.entry !== request.entry || session.state.phase !== 'ready') return;
        if (request.auto) autoOpen(request.entry);
        else openViewer(request.trigger);
    }
    function retire(preview, url) {
        const waiting = new AbortController();
        const entry = { preview, waiting, revoke: false, timer: setTimeout(() => finishRetiring(url), STARTING_VIDEO_MS) };
        entry.finished = new Promise(resolve => { entry.done = resolve; });
        retiring.set(url, entry);
        preview.listeners.abort();
        preview.node.inert = true; preview.node.setAttribute('aria-hidden', 'true');
        for (const type of ['loadedmetadata', 'error']) preview.node.addEventListener(type, () => finishRetiring(url), { once: true, signal: waiting.signal });
    }
    const finishAllRetiring = () => { for (const url of [...retiring.keys()]) finishRetiring(url); };
    const revokeObjectURL = url => {
        if (held?.url === url) { held.revoke = true; return; }
        const entry = retiring.get(url);
        if (entry) entry.revoke = true;
        else URL.revokeObjectURL(url);
    };
    const session = createLocalSession({ parentScope: sessionScope, pageTarget: null, revokeObjectURL });
    const viewer = createMediaViewer({ api, container: main, parentScope: scope, inspectionController: session.controller, sender,
        adapter: localFileAdapter, onRestoreFocus: () => { if (open.isConnected) open.focus({ preventScroll: true }); },
        // A held viewer closed by hand (Escape, Close) ends its hold at once: its video is gone.
        onClose: () => { viewerKey = null; if (held) finishHeld(); } });

    let viewerKey = null, media = null, mediaUrl = null, statusLine = null, rejectionLine = null, rejectionTimer = 0, announced = null, chosen = null;
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
        if (media && isStarting(media.node) && !retiring.has(mediaUrl)) retire(media, mediaUrl);
        else releaseMedia(media);
        media = null; mediaUrl = url;
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
        // A video still starting keeps its place, so the new preview goes after it and never moves it.
        const anchor = [...retiring.values()].map(entry => entry.preview.node).filter(item => item.parentNode === preview).at(-1);
        if (anchor) anchor.after(node);
        else preview.prepend(node);
    }
    scope.cleanup(session.subscribe(state => {
        // D155: a new selection can start while the viewer shows the previous one; it closes first, so no element holds
        // the old URL when the session revokes it (the new file's viewer opens once it is ready).
        if (viewerKey !== null && (state.phase !== 'ready' || session.entry?.key !== viewerKey)) { closeViewer(); viewerKey = null; }
        showMedia(state);
        showStatus(state.message);
        const entry = session.entry;
        setAvailable(state.phase === 'ready' && !!entry);
        if (state.phase === 'ready' && entry && announced !== entry.key) {
            announced = entry.key;
            // The session selects the record right after it publishes `ready`, so the viewer opens after that, and only
            // while this selection is still the current one and no other dialog is open (the viewer is replaced). D62:
            // a video opens it once the inline preview has settled, so a replacement while it starts never tears down a
            // second starting video in the viewer (that deadlocked WebKit).
            if (AUTO_OPEN.has(state.message)) autoOpen(entry);
            // An unreadable or failed preview says so in its alert; the toast never claims metadata is available.
            else if (TOASTABLE.has(state.message)) showWorkspaceToast(INSPECT_COPY.toast);
        }
    }));
    // A replacement or page leave revokes the URL after this subscriber dropped the media; a dispose closes the
    // viewer (on abort), drops the media, then ends the session.
    scope.cleanup(() => { const entry = endHeld(); viewer.dispose(); if (entry?.revoke) revokeObjectURL(entry.url); });
    scope.cleanup(() => { releaseMedia(media); media = null; mediaUrl = null; });
    scope.cleanup(finishAllRetiring);
    scope.cleanup(() => sessionScope.dispose());

    // Page leave: the viewer closes and the inline media goes before the session revokes the URL, and the chooser is
    // cleared, so a page restored from the back-forward cache never names a file beside the empty selection.
    function leave() {
        clearRejection();
        const entry = endHeld();
        viewer.close();
        releaseMedia(media); media = null; mediaUrl = null;
        finishAllRetiring();
        if (entry?.revoke) revokeObjectURL(entry.url);
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
    /** Runs `run` after this task, or once `preview`'s video stops starting (metadata, an error, or the D62 limit). */
    function whenSettled(preview, run) {
        if (!preview || !isStarting(preview.node)) { queueMicrotask(run); return; }
        // `run` checks that the page and the selection are still current, so the page's end may call `done` too.
        const settled = new AbortController();
        const done = () => { settled.abort(); clearTimeout(timer); run(); };
        const timer = setTimeout(done, STARTING_VIDEO_MS);
        for (const type of ['loadedmetadata', 'error']) preview.node.addEventListener(type, done, { once: true, signal: settled.signal });
        scope.cleanup(done);
    }
    /** D155: the viewer opens on `entry` once its inline preview has settled, while it is still the current selection. */
    function autoOpen(entry) {
        whenSettled(media, () => {
            if (scope.signal.aborted || session.entry !== entry || session.state.phase !== 'ready' || document.querySelector('dialog[open]:not(#viewer-dialog)')) return;
            // #483: the previous viewer is still closing around its starting video; this one opens once it has.
            if (held) { held.pending = { entry, auto: true }; return; }
            openViewer(input);
        });
    }
    /** The viewer on the current selection, details showing (D155 amends D97); focus returns to `trigger` on close. */
    function openViewer(trigger) {
        const entry = session.entry;
        if (!entry || session.state.phase !== 'ready') return;
        if (held) { held.pending = { entry, trigger, auto: false }; return; }
        viewer.open({ items: [entry], selectedKey: entry.key, trigger, details: 'shown' });
        viewerKey = entry.key;
    }
    scope.listen(open, 'click', () => {
        if (open.getAttribute('aria-disabled') !== 'true') openViewer(open);
    });
    /** Settles when no video on the page is still starting; `null` when none is. */
    function leaveGuard() {
        if (scope.signal.aborted) return null;
        const waits = [];
        for (const entry of retiring.values()) waits.push(entry.finished);
        if (media && isStarting(media.node)) {
            const { node } = media;
            waits.push(new Promise(resolve => {
                const settled = new AbortController();
                const done = () => { settled.abort(); clearTimeout(timer); resolve(); };
                const timer = setTimeout(done, STARTING_VIDEO_MS);
                for (const type of ['loadedmetadata', 'error']) node.addEventListener(type, done, { once: true, signal: settled.signal });
                scope.signal.addEventListener('abort', done, { once: true, signal: settled.signal });
            }));
        }
        return waits.length ? Promise.all(waits) : null;
    }
    return { dispose, leaveGuard };
}
