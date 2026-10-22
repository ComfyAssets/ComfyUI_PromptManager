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
import { showWorkspaceToast } from './toast.mjs';

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

/**
 * Stops a preview from holding its blob: URL: media is paused and its src dropped before the session revokes the URL.
 * The media resets with load() in the next task: WebKit (GStreamer) blocked the page when a starting video was reset in
 * the same task that removed its src.
 */
function releaseMedia(node) {
    if (!node) return;
    if (node instanceof HTMLMediaElement) { node.pause(); node.removeAttribute('src'); setTimeout(() => node.load(), 0); }
    else node.removeAttribute('src');
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
    const sessionScope = createScope();
    const session = createLocalSession({ parentScope: sessionScope });
    const viewer = createMediaViewer({ api, container: main, parentScope: scope, inspectionController: session.controller, sender,
        adapter: localFileAdapter, onRestoreFocus: () => { if (open.isConnected) open.focus({ preventScroll: true }); } });

    let media = null, mediaUrl = null, statusLine = null, announced = null;
    function showStatus(message) {
        statusLine?.remove(); statusLine = null;
        if (!message) return;
        // /* D63 */ one status line for the states the mock never shows.
        statusLine = element('p', 'small spaced-16', message);
        statusLine.setAttribute('role', ALERTS.has(message) ? 'alert' : 'status');
        preview.append(statusLine);
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
        if (video) {
            // /* D62 */ native controls, no inline autoplay.
            node.controls = true; node.playsInline = true; node.preload = 'metadata'; node.muted = true;
        } else {
            node.alt = 'Local file preview';
        }
        // /* D65 */ the URL is not revoked on load: the viewer and Download original use it.
        node.addEventListener('error', () => {
            if (media !== node) return;
            releaseMedia(node); media = null;
            showStatus(LOCAL_SESSION_MESSAGES.previewFailed);
        }, { once: true, signal: scope.signal });
        node.src = url;
        media = node;
        preview.prepend(node);
    }
    scope.cleanup(session.subscribe(state => {
        showMedia(state);
        showStatus(state.message);
        const entry = session.entry;
        setAvailable(state.phase === 'ready' && !!entry);
        if (state.phase === 'ready' && entry && announced !== entry.key) {
            announced = entry.key;
            showWorkspaceToast(INSPECT_COPY.toast);
        }
    }));
    // A replacement or page leave revokes the URL after this subscriber dropped the media; a dispose closes the
    // viewer (on abort), drops the media, then ends the session.
    scope.cleanup(() => viewer.dispose());
    scope.cleanup(() => { releaseMedia(media); media = null; mediaUrl = null; });
    scope.cleanup(() => sessionScope.dispose());

    function choose(source) {
        const outcome = session.select(source);
        if (REJECTIONS[outcome]) showStatus(REJECTIONS[outcome]);
        return outcome;
    }
    // The chooser keeps showing the chosen name, as in the concept; it is cleared as the picker opens, so choosing
    // the same file again is a new selection.
    scope.listen(input, 'click', () => { input.value = ''; });
    scope.listen(input, 'change', () => {
        // A cancelled chooser carries no files: the page stays as it is.
        choose(input.files?.length ? input.files : null);
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
        // A dropped file replaces the chosen one, so the chooser no longer names the old file.
        if (choose(transfer ? { files: transfer.files, items: transfer.items } : null) === 'accepted') input.value = '';
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
