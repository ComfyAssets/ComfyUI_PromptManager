import {createScope, createButton} from '../shared/toolkit/core.js';
import {createViewerSession} from './viewer-state.mjs';
import {rootedFileAdapter} from './inspection-state.mjs';
import {mountFileInspector} from './file-inspector.mjs';
import {displayPreferences, readPreferences} from './preferences.mjs';

let viewerId = 0;
// Upper bound on waiting for a closed Viewer's in-flight image view before destroying it.
const PENDING_VIEW_DESTROY_MS = 10000;
/** The video defaults, read through the shared display defaults (preferences.mjs, #249 System S3). */
export function readViewerPreferences(getStorage = () => window.localStorage, normalize = globalThis.GallerySettings.normalizeGallerySettings) {
    const {videoAutoplay,videoMute,videoLoop} = readPreferences(getStorage,normalize).values;
    return {videoAutoplay,videoMute,videoLoop};
}
function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

/**
 * #331 D139: with the client store `collections`, the viewer's inspector offers "Collections…"; the changes it confirms
 * reach `onCollectionsChanged({added, removed})` once the viewer closes, so the viewer keeps its place while open.
 */
export function createMediaViewer({api,container,parentScope,inspectionController,sender,onSelect,onRefresh,onRestoreFocus,onClose,adapter = rootedFileAdapter,
    collections = null,onCollectionsChanged = null}) {
    const scope = createScope();
    let active = null, retryVersion = 0;
    const dispose = () => scope.dispose();
    const stopSlideshow = () => active?.stop();
    // Closes an open viewer without restoring focus (page leave); the viewer stays usable.
    const close = () => active?.close(false);
    if (parentScope.signal.aborted) { scope.dispose(); return {open() {},close,dispose,stopSlideshow}; }
    parentScope.signal.addEventListener('abort',dispose,{once:true});
    scope.cleanup(() => parentScope.signal.removeEventListener('abort',dispose));
    scope.cleanup(() => active?.close(false));

    function open(options) {
        if (scope.signal.aborted) return;
        active?.close(false);
        const {items,selectedKey,trigger = document.activeElement} = options;
        let mounted = false, initial, entry, currentKey, viewer = null, withoutViewer = false, mediaScope = null, closed = false;
        let originalDownload = null, inspectionState = inspectionController.state, collectionChanges = null;
        const session = createViewerSession({adapter,items,initialKey:selectedKey,onChange:(state,selected) => {
            if (!mounted) { initial = [state,selected]; return; }
            update(state,selected);
        }});
        const local = createScope();
        // The concept's viewer shell (index.html h84–103, app.js a889–926): `dialog#viewer-dialog` named by its title,
        // `.dialog-header`, then `.viewer-layout` > `.viewer-left` (`#viewer-stage`, `#viewer-toolbar`) and
        // `aside#viewer-details`. #249 D34: production keeps a focusable stage, its controls' accessible names, the
        // Download link and, after Next →, the position status and the two unavailability reasons.
        const dialog = element('dialog');dialog.id = 'viewer-dialog';dialog.setAttribute('aria-labelledby','viewer-title');
        const header = element('div','dialog-header');
        const heading = element('div'), title = element('h2');title.id = 'viewer-title';
        heading.append(title,element('p','muted small','One viewer · image, prompts, generation data, workflow'));
        const close = createButton('×');close.id = 'viewer-close';close.setAttribute('aria-label','Close image viewer');close.autofocus = true;
        header.append(heading,close);
        const body = element('div','viewer-layout'), left = element('div','viewer-left');
        const stage = element('div');stage.id = 'viewer-stage';stage.tabIndex = 0;
        stage.setAttribute('role','region');stage.setAttribute('aria-label','Media preview');
        const failure = element('div','media-failure');
        const error = element('p',undefined,'Media could not be loaded. Retry this file.');
        const retry = createButton('Retry media');failure.append(error,retry);failure.hidden = true;stage.append(failure);
        const controls = element('div','viewer-toolbar');controls.id = 'viewer-toolbar';
        controls.setAttribute('role','group');controls.setAttribute('aria-label','Media controls');
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
        const download = element('a','media-download','Download');download.setAttribute('aria-label','Download original');download.setAttribute('download','');
        local.listen(download,'click',event => {session.stop();if (download.getAttribute('aria-disabled') === 'true') event.preventDefault();});
        controls.append(download);
        const next = button('Next media','Next →',() => session.navigate(1));
        // D97: "Collapse viewer details by default" opens the viewer with its details hidden behind one toggle.
        const collapsed = displayPreferences()?.sidebarCollapsedByDefault === true;
        let detailsToggle = null;
        if (collapsed) {
            detailsToggle = createButton('Show details');detailsToggle.setAttribute('aria-expanded','false');
            detailsToggle.setAttribute('aria-controls','viewer-details');controls.append(detailsToggle);
        }
        const position = element('p','media-position muted');position.id = `media-viewer-position-${++viewerId}`;
        position.setAttribute('role','status');
        const reason = element('p','media-reason muted');reason.id = `media-viewer-reason-${viewerId}`;
        const slideshowReason = element('p','media-slideshow-reason muted');slideshowReason.id = `media-viewer-slideshow-${viewerId}`;
        controls.append(position,reason,slideshowReason);left.append(stage,controls);
        const details = element('aside');details.id = 'viewer-details';details.setAttribute('aria-label','Image metadata');
        if (detailsToggle) {
            details.hidden = true;
            local.listen(detailsToggle,'click',() => {
                details.hidden = !details.hidden;
                detailsToggle.textContent = details.hidden ? 'Show details' : 'Hide details';
                detailsToggle.setAttribute('aria-expanded',String(!details.hidden));
            });
        }
        body.append(left,details);dialog.append(header,body);container.append(dialog);
        const localSender = {open(roles,trigger) {session.stop();sender.open(roles,trigger);}};
        const inspector = mountFileInspector(details,{api,sender:localSender,parentScope:local,variant:'full',onInteraction:() => session.stop(),onRetry:() => inspectionController.retry(),
            onLoad:() => inspectionController.load?.(),
            onRefresh:onRefresh ? () => { closeViewer();onRefresh(); } : undefined,collections,
            onCollectionsChanged:({added,removed}) => {
                collectionChanges ??= {added:[],removed:[]};
                collectionChanges.added.push(...added);collectionChanges.removed.push(...removed);
            }});
        function closeViewer(restore = true) {
            if (closed) return;
            closed = true;active = null;local.dispose();
            if (restore && !scope.signal.aborted) {
                if (trigger?.isConnected) trigger.focus({preventScroll:true});
                else onRestoreFocus?.(entry);
            }
            if (!scope.signal.aborted) onClose?.();
            if (collectionChanges && !scope.signal.aborted) onCollectionsChanged?.(collectionChanges);
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
            mediaScope?.dispose();mediaScope = createScope();viewer = null;
            for (const child of [...stage.children]) if (child !== failure) child.remove();
            failure.hidden = true;
            const mediaLocal = mediaScope, key = session.state.key, ViewerJs = window.Viewer;
            // Transforms follow the Viewer.js this media was built with, which builds once the image has loaded.
            withoutViewer = !ViewerJs;
            const current = () => !local.signal.aborted && !mediaLocal.signal.aborted && session.state.key === key;
            // An adapter's own media URL (a session-owned blob:) is used exactly, so a retry reuses it; a server
            // original gets a fresh cache-buster on every build.
            let original, src;
            try { if (adapter.mediaUrl) src = adapter.mediaUrl(selected); else original = adapter.originalDownload(selected); }
            catch { session.failed(key);return; }
            if (adapter.mediaUrl) {
                if (typeof src !== 'string' || !src) { session.failed(key);return; }
            } else {
                const url = new URL(original.url,window.location.href);
                url.searchParams.set('_pm_viewer',`${Date.now()}-${++retryVersion}`);
                src = url.href;
            }
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
                node.hidden = !!ViewerJs;
                // With Viewer.js the src is released after the viewer (see release below).
                if (!ViewerJs) {
                    mediaLocal.listen(node,'load',() => {if (current()) session.ready(key);});
                    mediaLocal.cleanup(() => node.removeAttribute('src'));
                }
            }
            node.src = src;stage.append(node);
            if (!selected.is_video && ViewerJs) {
                // Viewer.js is built on an image that has loaded. Its own cancel removes its image listeners only
                // while `image.complete` is false, so an image left with its load event still queued would build a
                // viewer into the released image after destroy() (#293). Until then no viewer exists to cancel.
                let mountedViewer = false;
                mediaLocal.listen(node,'load',() => { if (current()) { mountedViewer = true;mountViewer(); } },{once:true});
                mediaLocal.cleanup(() => { if (!mountedViewer) node.removeAttribute('src'); });
            }
            function mountViewer() {
                let fitFrame = 0, fitWidth, fitHeight, viewSettled = false, released = false, fallback = 0;
                const release = () => {
                    if (released) return;
                    released = true;clearTimeout(fallback);instance.destroy();node.removeAttribute('src');
                };
                const scheduleFit = () => {
                    if (!current() || fitFrame) return;
                    fitFrame = requestAnimationFrame(() => {
                        fitFrame = 0;
                        if (!current() || !instance.viewed) return;
                        const width = stage.offsetWidth, height = stage.offsetHeight;
                        if (width === fitWidth && height === fitHeight) return;
                        fitWidth = width;fitHeight = height;instance.resize();
                    });
                };
                const instance = new ViewerJs(node,{inline:true,toolbar:false,navbar:false,title:false,button:false,
                    keyboard:false,fullscreen:false,transition:false,viewed() {
                        viewSettled = true;
                        if (mediaLocal.signal.aborted) { release(); return; }
                        if (!current()) return;
                        session.ready(key);scheduleFit();
                    }});
                const observer = new ResizeObserver(scheduleFit);observer.observe(stage);
                viewer = instance;mediaLocal.cleanup(() => {
                    observer.disconnect();cancelAnimationFrame(fitFrame);
                    // Before Viewer.js has built (its zero-delay build is pending), destroy() cancels that build;
                    // deferring it would let the build run into the image the stage has just dropped.
                    // Once built, inline Viewer.js keeps viewing a still-loading image after destroy(), and that
                    // view rebuilds its list from images that still have a src; keep both until the view settles,
                    // fails or times out.
                    if (viewSettled || !instance.ready) { release(); return; }
                    node.addEventListener('error',release,{once:true});
                    fallback = setTimeout(release,PENDING_VIEW_DESTROY_MS);
                });
            }
            updateControls(session.state);
        }
        function updateControls(state) {
            const unavailable = inspectionState.refreshRequired ? inspectionState.message : state.phase === 'error' ? 'Media could not be loaded. Retry this file.' : entry.is_video ?
                'Image transforms are unavailable for video.' : withoutViewer ?
                'Image transforms are unavailable because Viewer.js could not load.' : !state.mediaReady ? 'Image transforms are available when the image is ready.' : '';
            reason.textContent = unavailable;reason.hidden = !unavailable;
            for (const node of transforms) {
                node.setAttribute('aria-disabled',String(!!unavailable));
                if (unavailable) node.setAttribute('aria-describedby',reason.id);else node.removeAttribute('aria-describedby');
            }
            for (const node of [previous,next]) {
                node.setAttribute('aria-disabled',String(state.total === 1));
                if (state.total === 1) node.setAttribute('aria-describedby',position.id);else node.removeAttribute('aria-describedby');
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
                // A linked card already carries its title, so the header reads "<title> · <filename>" at once.
                const known = entry.linked?.status === 'linked' && entry.linked.title;
                currentKey = state.key;title.textContent = known ? `${known} · ${entry.filename}` : entry.filename;
                try { originalDownload = adapter.originalDownload(entry); } catch { originalDownload = null; }
                onSelect?.(entry);
                rebuild(entry);
            }
            updateControls(session.state);
        }
        dialog.showModal();mounted = true;update(...initial);
        local.cleanup(inspectionController.subscribe(state => {
            inspectionState = state;updateControls(session.state);
            if (state.error) session.stop();
            inspector.setState(state);
            if (state.record?.identity.key === currentKey && state.record.library?.title) title.textContent = `${state.record.library.title} · ${entry.filename}`;
        }));
    }
    return {open,close,dispose,stopSlideshow};
}
