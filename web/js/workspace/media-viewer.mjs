import {createScope, createButton} from '../shared/toolkit/core.js';
import {createViewerSession} from './viewer-state.mjs';
import {rootedFileAdapter} from './inspection-state.mjs';
import {mountFileInspector} from './file-inspector.mjs';

let viewerId = 0;
// Upper bound on waiting for a closed Viewer's in-flight image view before destroying it.
const PENDING_VIEW_DESTROY_MS = 10000;
export function readViewerPreferences(getStorage = () => window.localStorage, normalize = globalThis.GallerySettings.normalizeGallerySettings) {
    let stored;
    try { stored = JSON.parse(getStorage().getItem('gallerySettings')); } catch { stored = null; }
    const {videoAutoplay,videoMute,videoLoop} = normalize(stored);
    return {videoAutoplay,videoMute,videoLoop};
}
function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

export function createMediaViewer({container,parentScope,inspectionController,sender,onSelect,onRefresh,onRestoreFocus,onClose,adapter = rootedFileAdapter}) {
    const scope = createScope();
    let active = null, retryVersion = 0;
    const dispose = () => scope.dispose();
    const stopSlideshow = () => active?.stop();
    if (parentScope.signal.aborted) { scope.dispose(); return {open() {},dispose,stopSlideshow}; }
    parentScope.signal.addEventListener('abort',dispose,{once:true});
    scope.cleanup(() => parentScope.signal.removeEventListener('abort',dispose));
    scope.cleanup(() => active?.close(false));

    function open(options) {
        if (scope.signal.aborted) return;
        active?.close(false);
        const {items,selectedKey,trigger = document.activeElement} = options;
        let mounted = false, initial, entry, currentKey, viewer = null, mediaScope = null, closed = false;
        let originalDownload = null, inspectionState = inspectionController.state;
        const session = createViewerSession({adapter,items,initialKey:selectedKey,onChange:(state,selected) => {
            if (!mounted) { initial = [state,selected]; return; }
            update(state,selected);
        }});
        const local = createScope();
        const dialog = element('dialog','image-preview');
        dialog.setAttribute('aria-label','Image preview');
        const header = element('header','image-preview-header');
        const heading = element('div'), title = element('h2');
        const position = element('p','image-preview-position muted');
        position.setAttribute('role','status');
        heading.append(title,position);
        const close = createButton('×');close.setAttribute('aria-label','Close preview');close.autofocus = true;
        header.append(heading,close);
        const body = element('div','image-preview-body'), left = element('div','image-preview-left');
        const stage = element('section','image-preview-stage');stage.tabIndex = 0;stage.setAttribute('aria-label','Media preview');
        const media = element('div','image-preview-media'), failure = element('div','image-preview-failure');
        const error = element('p',undefined,'Media could not be loaded. Retry this file.');
        const retry = createButton('Retry media');failure.append(error,retry);failure.hidden = true;stage.append(media,failure);
        const toolbar = element('div','image-preview-toolbar'), controls = element('div','image-preview-controls');
        controls.setAttribute('role','group');controls.setAttribute('aria-label','Media controls');
        const reason = element('p','image-preview-reason muted');reason.id = `media-viewer-reason-${++viewerId}`;
        function button(label,text,action) {
            const node = createButton(text);node.setAttribute('aria-label',label);
            local.listen(node,'click',() => { if (node.getAttribute('aria-disabled') !== 'true') action(); });
            controls.append(node);return node;
        }
        const previous = button('Previous media','← Previous',() => session.navigate(-1));
        const transforms = [button('Zoom out','−',() => viewer.zoom(-0.2)),button('Zoom in','+',() => viewer.zoom(0.2)),
            button('Fit image','Fit',() => viewer.reset()),button('Rotate image 90 degrees','Rotate',() => viewer.rotate(90)),
            button('Flip image horizontally','Flip',() => viewer.scaleX(viewer.imageData.scaleX === 1 ? -1 : 1))];
        const slideshow = button('Play slideshow','Play',() => session.state.requested ? session.stop() : session.play());
        const slideshowReason = element('p','image-preview-slideshow-reason muted');slideshowReason.id = `media-viewer-slideshow-${viewerId}`;
        const download = element('a','image-preview-download','Download');download.setAttribute('aria-label','Download original');download.setAttribute('download','');
        local.listen(download,'click',event => {session.stop();if (download.getAttribute('aria-disabled') === 'true') event.preventDefault();});
        controls.append(download);
        const next = button('Next media','Next →',() => session.navigate(1));
        toolbar.append(controls,reason,slideshowReason);left.append(stage,toolbar);
        const details = element('aside','image-preview-details');details.setAttribute('aria-label','Preview prompt details');
        body.append(left,details);dialog.append(header,body);container.append(dialog);
        const localSender = {open(roles,trigger) {session.stop();sender.open(roles,trigger);}};
        const inspector = mountFileInspector(details,{sender:localSender,parentScope:local,onInteraction:() => session.stop(),onRetry:() => inspectionController.retry(),
            onRefresh:onRefresh ? () => { closeViewer();onRefresh(); } : undefined});
        function closeViewer(restore = true) {
            if (closed) return;
            closed = true;active = null;local.dispose();
            if (restore && !scope.signal.aborted) {
                if (trigger?.isConnected) trigger.focus({preventScroll:true});
                else onRestoreFocus?.(entry);
            }
            if (!scope.signal.aborted) onClose?.();
        }
        active = {close:closeViewer,stop:() => session.stop()};
        local.cleanup(() => {
            session.dispose();mediaScope?.dispose();inspector.dispose();
            if (dialog.open) dialog.close();dialog.remove();
        });
        local.listen(close,'click',() => closeViewer());local.listen(dialog,'close',() => closeViewer());
        // Finish focus restoration before a later key event can select another control.
        local.listen(dialog,'cancel',event => {event.preventDefault();closeViewer();});
        local.listen(document,'visibilitychange',() => {if (document.hidden) session.stop();});
        local.listen(window,'pagehide',() => closeViewer(false));
        local.listen(retry,'click',() => {if (!inspectionState.refreshRequired) rebuild(entry);});
        local.listen(dialog,'keydown',event => {
            if (!['ArrowLeft','ArrowRight'].includes(event.key) || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey ||
                (!stage.contains(event.target) && !controls.contains(event.target)) ||
                event.target.closest('video,input,textarea,select,pre,summary,[contenteditable]') ||
                !window.getSelection()?.isCollapsed || [...document.querySelectorAll('dialog[open]')].at(-1) !== dialog) return;
            event.preventDefault();session.navigate(event.key === 'ArrowLeft' ? -1 : 1);
        });
        function rebuild(selected) {
            mediaScope?.dispose();mediaScope = createScope();viewer = null;media.replaceChildren();
            failure.hidden = true;
            const mediaLocal = mediaScope, key = session.state.key;
            const current = () => !local.signal.aborted && !mediaLocal.signal.aborted && session.state.key === key;
            let original;
            try { original = adapter.originalDownload(selected); }
            catch { session.failed(key);return; }
            const url = new URL(original.url,window.location.href);
            url.searchParams.set('_pm_viewer',`${Date.now()}-${++retryVersion}`);
            const node = element(selected.is_video ? 'video' : 'img');
            mediaLocal.listen(node,'error',() => {if (current()) session.failed(key);});
            if (selected.is_video) {
                node.controls = true;node.playsInline = true;node.preload = 'metadata';
                const preferences = readViewerPreferences();
                node.muted = preferences.videoMute;node.loop = preferences.videoLoop;
                mediaLocal.listen(node,'loadedmetadata',() => {if (current()) session.ready(key);});
                mediaLocal.cleanup(() => {node.pause();node.removeAttribute('src');node.load();});
                if (preferences.videoAutoplay) mediaLocal.listen(node,'loadedmetadata',() => {
                    if (current()) node.play().catch(() => {});
                },{once:true});
            } else {
                node.alt = selected.filename;node.decoding = 'async';
                node.hidden = !!window.Viewer;
                if (!window.Viewer) mediaLocal.listen(node,'load',() => {if (current()) session.ready(key);});
                mediaLocal.cleanup(() => node.removeAttribute('src'));
            }
            node.src = url.href;media.append(node);
            if (!selected.is_video && window.Viewer) {
                let fitFrame = 0, fitWidth, fitHeight, viewSettled = false, released = false, fallback = 0;
                const release = () => { if (released) return; released = true; clearTimeout(fallback); instance.destroy(); };
                const scheduleFit = () => {
                    if (!current() || fitFrame) return;
                    fitFrame = requestAnimationFrame(() => {
                        fitFrame = 0;
                        if (!current() || !instance.viewed) return;
                        const width = media.offsetWidth, height = media.offsetHeight;
                        if (width === fitWidth && height === fitHeight) return;
                        fitWidth = width;fitHeight = height;instance.resize();
                    });
                };
                const instance = new window.Viewer(node,{inline:true,container:media,toolbar:false,navbar:false,title:false,button:false,
                    keyboard:false,fullscreen:false,transition:false,viewed() {
                        viewSettled = true;
                        if (mediaLocal.signal.aborted) { release(); return; }
                        if (!current()) return;
                        session.ready(key);scheduleFit();
                    }});
                const observer = new ResizeObserver(scheduleFit);observer.observe(stage);
                viewer = instance;mediaLocal.cleanup(() => {
                    observer.disconnect();cancelAnimationFrame(fitFrame);
                    // Inline Viewer.js keeps viewing a still-loading image after destroy(), and that view
                    // then reads the destroyed list; destroy once the view settles, fails or times out.
                    if (viewSettled) { release(); return; }
                    node.addEventListener('error',release,{once:true});
                    fallback = setTimeout(release,PENDING_VIEW_DESTROY_MS);
                });
            }
            updateControls(session.state);
        }
        function updateControls(state) {
            const unavailable = inspectionState.refreshRequired ? inspectionState.message : state.phase === 'error' ? 'Media could not be loaded. Retry this file.' : entry.is_video ?
                'Image transforms are unavailable for video.' : !viewer ?
                'Image transforms are unavailable because Viewer.js could not load.' : !state.mediaReady ? 'Image transforms are available when the image is ready.' : '';
            reason.textContent = unavailable;reason.hidden = !unavailable;
            for (const node of transforms) {
                node.setAttribute('aria-disabled',String(!!unavailable));
                if (unavailable) node.setAttribute('aria-describedby',reason.id);else node.removeAttribute('aria-describedby');
            }
            for (const node of [previous,next]) {
                node.setAttribute('aria-disabled',String(state.total === 1));
                if (state.total === 1) {node.setAttribute('aria-describedby',position.id);}
            }
            const action = state.phase === 'paused-video' ? 'Stop' : state.requested ? 'Pause' : 'Play';
            slideshow.textContent = action;slideshow.setAttribute('aria-label',`${action} slideshow`);
            const cannotPlay = !state.requested && (inspectionState.refreshRequired || state.phase === 'error' || state.total < 2 || entry.is_video);
            slideshow.setAttribute('aria-disabled',String(!!cannotPlay));
            slideshowReason.textContent = !cannotPlay ? '' : inspectionState.refreshRequired ? inspectionState.message : state.phase === 'error' ?
                'Media could not be loaded. Retry this file.' : state.total < 2 ? 'Slideshow requires at least two loaded media entries.' : 'Start slideshow on an image.';
            slideshowReason.hidden = !cannotPlay;
            if (cannotPlay) slideshow.setAttribute('aria-describedby',slideshowReason.id);else slideshow.removeAttribute('aria-describedby');
            const canDownload = originalDownload && !inspectionState.refreshRequired;
            download.setAttribute('aria-disabled',String(!canDownload));
            if (canDownload) {
                download.href = originalDownload.url;download.download = originalDownload.filename;
                download.removeAttribute('tabindex');download.removeAttribute('aria-describedby');
            } else {
                download.removeAttribute('href');download.tabIndex = 0;download.setAttribute('aria-describedby',reason.id);
            }
            failure.hidden = state.phase !== 'error' || inspectionState.refreshRequired;
        }
        function update(state,selected) {
            if (closed || state.phase === 'disposed') return;
            entry = selected;
            const status = {'waiting-image':'Slideshow waiting for image.','playing-image':'Slideshow playing.',
                'paused-video':'Slideshow paused for video. Next or Previous continues it on an image; Stop ends slideshow.',error:'Media could not be loaded.'}[state.phase];
            const label = `${state.index + 1} of ${state.total} loaded: ${entry.filename}${status ? ` — ${status}` : ''}`;
            if (position.textContent !== label) position.textContent = label;
            if (currentKey !== state.key) {
                currentKey = state.key;title.textContent = entry.filename;
                try { originalDownload = adapter.originalDownload(entry); } catch { originalDownload = null; }
                onSelect?.(entry);
                rebuild(entry);
            }
            updateControls(session.state);
        }
        position.id = `media-viewer-position-${viewerId}`;
        dialog.showModal();mounted = true;update(...initial);
        local.cleanup(inspectionController.subscribe(state => {
            inspectionState = state;updateControls(session.state);
            if (state.error) session.stop();
            inspector.setState(state);
            if (state.record?.identity.key === currentKey && state.record.library?.title) title.textContent = `${state.record.library.title} · ${entry.filename}`;
        }));
    }
    return {open,dispose,stopSlideshow};
}
