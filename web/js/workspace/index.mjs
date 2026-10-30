import '../api-client.js';
import { createButton, createScope } from '../shared/toolkit/core.js';
import { routes, mountRouter, summarizeStats, routeTitle, loadNavCounts, navigateTo, COLLECTION_ROUTE,
    LIBRARY_WRITE_COUNTS, NAV_COUNT_ROUTES, FAVORITE_WRITE_COUNTS, FAVORITE_COUNT_DELAY_MS, REVIEW_WRITE_COUNTS,
    JOB_END_COUNTS } from './state.mjs';
import { mountWorkspaceTheme } from './theme.mjs';
import { forgetImagesVisit, mountImages, onFavoriteChange } from './images.mjs';
import { forgetLibraryVisit, mountLibrary, mountNewPromptAction, onLibraryWrite } from './library.mjs';
import { mountVocabulary } from './vocabulary.mjs';
import { createWorkspaceIcon } from './icons.mjs';
import { bindCanvasLink, canvasHref } from './topbar.mjs';
import { renderHeading, pageHeading } from './heading.mjs';
import { closeReviewDialog, closeWorkspaceDialog } from './workspace-dialogs.mjs';
import { createSavedViews, openSaveViewDialog, openEditViewDialog, savedViewHash } from './saved-views.mjs';
import { createCollections, collectionHash, openEditCollectionDialog, missingNote, DELETED_PAGE_TEXT, OPEN_IMAGES_LINK,
    LIST_FAILED_TEXT } from './collections.mjs';
import { createActivityStore } from './activity-store.mjs';
import { mountActivityPage, mountActivityTopbar } from './activity.mjs';
import { mountActivityJobView } from './activity-job-view.mjs';
import { mountInspect } from './inspect.mjs';
import { mountReview } from './review.mjs';
import { createPromptSender } from './prompt-sender.mjs';
import { mountSystem } from './system.mjs';
import { createRestoreReload, onRestoreFinished, onRestoreHandedBack, showRestoreReview } from './system-backups.mjs';
import { mountLibraryBanner } from './library-location.mjs';
import { detectJxlSupport } from './thumbnail-support.mjs';

const { createApiClient, resolveUrl } = window.ApiClient;

function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

function cardLink(route) {
    const link = element('a', route.action, 'workspace-link');
    link.href = route.href;
    return link;
}

function mountWorkspace() {
    const scope = createScope();
    const api = createApiClient();
    const main = document.getElementById('workspace-main');
    const primaryNav = document.getElementById('primary-nav');
    const savedNav = document.getElementById('saved-nav');
    const collectionsNav = document.getElementById('collections-nav');
    const systemNav = document.getElementById('system-nav');
    scope.listen(document.querySelector('.skip'), 'click', event => {
        event.preventDefault();
        main.focus();
    });
    // D4: the concept's Theme select; "System" keeps following ComfyUI, then the OS.
    const theme = mountWorkspaceTheme({ root: document.body, scope });
    // #317: learn once whether this browser shows JPEG XL thumbnails (cards fall back to originals otherwise).
    detectJxlSupport();
    const preference = document.getElementById('theme');
    preference.value = theme.preference;
    scope.listen(preference, 'change', () => theme.setPreference(preference.value));
    // D6: ComfyUI ↗ opens the canvas; on Images with a resolved selection it sends that pair (a send is not an Activity job).
    let images = null;
    scope.cleanup(bindCanvasLink(document.querySelector('.topbar .canvas-link'), {
        href: canvasHref(resolveUrl, window.location.pathname),
        getSelectedPair: () => images?.selectedPair?.() ?? null,
        sender: {open: (roles, trigger) => images?.sender.open(roles, trigger)},
    }));
    // The concept's nav(): icon, label text, then a `.nav-count` only for a loaded real count.
    const navLinks = ids => ids.map(([id, icon]) => {
        const route = routes.find(item => item.id === id);
        const link = element('a');
        link.append(createWorkspaceIcon(icon), document.createTextNode(route.label));
        link.href = `#/${route.id}`;
        link.dataset.route = route.id;
        return link;
    });
    primaryNav.replaceChildren(...navLinks([['images', 'images'], ['library', 'library'], ['tags', 'tags'], ['inspect', 'upload']]));
    // The concept's tools-and-system group: Activity, System, then (D28) the production Overview hub.
    systemNav.replaceChildren(...navLinks([['activity', 'activity'], ['system', 'settings'], ['overview', 'overview']]));
    // Activity (#249 Activity-1): one store of this session's jobs feeds the top-bar button and pill, the sidebar
    // row's count and the Activity page, on every route.
    const activity = createActivityStore({ api, scope, connect: url => new EventSource(resolveUrl(url)) });
    mountActivityTopbar({ store: activity, scope });
    activity.load();
    // D129: a node-folder library is offered a move (or "Keep it here") above every page.
    mountLibraryBanner(document.getElementById('library-banner'), { api, activity, scope });
    const sidebar = document.querySelector('aside.sidebar');
    // The route key a nav link carries in `data-route`: a saved view's is `saved/<id>`, a collection's `collection/<id>`.
    const routeKey = route => route.id === 'saved' ? `saved/${route.savedId}`
        : route.id === 'collection' ? `collection/${route.collectionId}` : route.id;
    let currentKey = null;
    const markCurrent = () => {
        for (const link of sidebar.querySelectorAll('nav [data-route]')) {
            if (link.dataset.route === currentKey) link.setAttribute('aria-current', 'page');
            else link.removeAttribute('aria-current');
        }
    };
    // SAVED VIEWS (#249 D16): the concept's saved-nav rows (search icon, name, no count), loaded with the shell and
    // re-read after every change. /* D49 */ no "PROPOSED" badge. The first rows are the concept's Favorites (star icon,
    // count, D51) and Needs review (review icon, the exact pending count, ruling R5), kept across every re-read so
    // their counts stay.
    const [favoritesLink, reviewLink] = navLinks([['favorites', 'star'], ['review', 'review']]);
    savedNav.replaceChildren(favoritesLink, reviewLink);
    const savedViews = createSavedViews(api, scope.signal);
    scope.cleanup(savedViews.subscribe(({ views }) => {
        savedNav.replaceChildren(favoritesLink, reviewLink, ...views.map(view => {
            const link = element('a');
            link.append(createWorkspaceIcon('search'), document.createTextNode(view.name));
            link.href = savedViewHash(view.id);
            link.dataset.route = `saved/${view.id}`;
            return link;
        }));
        markCurrent();
    }));
    savedViews.load();
    // COLLECTIONS (#331 /* D136 */): the concept's collections-nav rows (folder icon, name, the listed-member count once
    // read), without the "PROPOSED" badge. The label shows only while a collection is listed. A failed read keeps the
    // last rows; a collection page says the list could not be loaded.
    const collectionsLabel = collectionsNav.previousElementSibling;
    const collections = createCollections(api, scope.signal);
    scope.cleanup(collections.subscribe(({ collections: listed }) => {
        collectionsLabel.hidden = listed.length === 0;
        collectionsNav.replaceChildren(...listed.map(item => {
            const link = element('a');
            link.append(createWorkspaceIcon('folder'), document.createTextNode(item.name));
            if (Number.isSafeInteger(item.count)) link.append(element('span', item.count.toLocaleString(), 'nav-count'));
            link.href = collectionHash(item.id);
            link.dataset.route = `collection/${item.id}`;
            return link;
        }));
        markCurrent();
    }));
    collections.load();
    /** The toolbar's Save view on a page: the concept's name dialog, then the store saves the page's full state. */
    const saveView = (page, view) => (trigger, state) => openSaveViewDialog({ scope: view, trigger, q: state.q,
        onSave: name => savedViews.create(page, name, state) });
    // One read per count, so a write that refreshes Library and Tags never supersedes an Images read in flight.
    const countRequests = new Map();
    // #494: a page that has begun to leave (beforeunload) starts no count read: WebKit fails one with "Fetch API cannot
    // load … due to access control checks." (a page error). Counts asked for meanwhile wait; a leave that is called off
    // (the page is still here) reads them on its next input, as the legacy admin page does (#290).
    let leaving = false;
    const deferredCounts = new Set();
    function refreshCounts(ids) {
        if (leaving) { for (const id of ids) deferredCounts.add(id); return; }
        for (const id of ids) {
            countRequests.get(id)?.abort();
            const request = new AbortController();
            countRequests.set(id, request);
            // Each badge lands as its own read settles; a count that failed to refresh is removed, never left stale.
            loadNavCounts(api, request.signal, (_, count) => {
                if (!request.signal.aborted) showCount(id, count);
            }, [id]);
        }
    }
    function showCount(id, count) {
        const link = sidebar.querySelector(`nav [data-route="${id}"]`);
        link?.querySelector('.nav-count')?.remove();
        if (link && Number.isSafeInteger(count)) link.append(element('span', count.toLocaleString(), 'nav-count'));
    }
    /** A count a page has just read itself: it replaces the badge, and an older read in flight no longer lands. */
    function setCount(id, count) {
        countRequests.get(id)?.abort();
        countRequests.delete(id);
        showCount(id, count);
    }
    scope.cleanup(() => { for (const request of countRequests.values()) request.abort(); });
    // A confirmed Library or vocabulary write cannot change the Images count, so only Library and Tags are re-read.
    scope.cleanup(onLibraryWrite(() => refreshCounts(LIBRARY_WRITE_COUNTS)));
    // Starring or unstarring anywhere (Images, Favorites, Library media) re-reads the Favorites count (D51), once
    // after a run of quick toggles settles rather than once per click (#249 M1).
    let favoriteCountTimer = 0;
    scope.cleanup(onFavoriteChange(() => {
        clearTimeout(favoriteCountTimer);
        favoriteCountTimer = setTimeout(() => { favoriteCountTimer = 0; refreshCounts(FAVORITE_WRITE_COUNTS); }, FAVORITE_COUNT_DELAY_MS);
    }));
    scope.cleanup(() => clearTimeout(favoriteCountTimer));
    // Any beforeunload: a page that stays (Stop, a download, a "stay" prompt) revives on its next input or when shown.
    scope.listen(window, 'beforeunload', () => {
        leaving = true;
        if (favoriteCountTimer) { clearTimeout(favoriteCountTimer); favoriteCountTimer = 0; for (const id of FAVORITE_WRITE_COUNTS) deferredCounts.add(id); }
        for (const [id, request] of countRequests) { request.abort(); deferredCounts.add(id); }
        countRequests.clear();
    });
    const revive = () => {
        if (!leaving) return;
        leaving = false;
        const ids = [...deferredCounts]; deferredCounts.clear();
        if (ids.length) refreshCounts(ids);
        if (restorePending) { restorePending = false; restoreReload.request(); }
    };
    // Capture, as the admin page does, so no handler that stops an input keeps the page from reviving.
    for (const type of ['pointerdown', 'keydown']) {
        window.addEventListener(type, revive, true);
        scope.cleanup(() => window.removeEventListener(type, revive, true));
    }
    scope.listen(document, 'visibilitychange', () => { if (document.visibilityState === 'visible') revive(); });
    // A job that ends re-reads the counts it can change, once per ended job: a metadata scan may have added items to
    // Needs review; deleting images (#480) hides or trashes them, with their favorites, review items and collection
    // memberships (#501).
    const runningJobs = new Map();
    scope.cleanup(activity.subscribe(() => {
        const ended = new Set();
        for (const job of activity.jobs()) {
            if (!JOB_END_COUNTS[job.kind]) continue;
            if (job.state === 'running') runningJobs.set(job.id, job.kind);
            else if (runningJobs.delete(job.id)) ended.add(job.kind);
        }
        if (!ended.size) return;
        refreshCounts([...new Set([...ended].flatMap(kind => JOB_END_COUNTS[kind]))]);
        if (ended.has('image_delete') && !leaving) collections.load();
    }));
    refreshCounts(NAV_COUNT_ROUTES);
    // S9: a finished restore replaced the library. The cached visits are dropped, the counts and saved views read
    // again, and the open page is shown again from the restored data; the Activity job view the restore opened stays.
    // #415: an open review closes (its plan no longer describes storage); while any other dialog is open, all of it
    // waits until the last one has closed, so the dialog keeps what was typed.
    // #494: a restore that ends while the page leaves reloads nothing then; a page that stays reloads on revive.
    let restorePending = false;
    const restoreReload = createRestoreReload(() => {
        if (leaving) { restorePending = true; return; }
        forgetImagesVisit();
        forgetLibraryVisit();
        refreshCounts(NAV_COUNT_ROUTES);
        savedViews.load();
        collections.load();
        if (currentKey !== 'activity') navigateTo(window.location.hash);
    });
    scope.cleanup(() => restoreReload.dispose());
    scope.cleanup(onRestoreFinished(activity, () => {
        closeReviewDialog();
        restoreReload.request();
    }));
    // S9 I1: a restore this page started that could not run yet (other requests kept reading) hands its review back:
    // it opens again over the page shown now, with Restore (again) and Discard; leaving it any other way discards it.
    scope.cleanup(onRestoreHandedBack(activity, review => {
        if (viewScope && !viewScope.signal.aborted) showRestoreReview({ api, store: activity, scope: viewScope, ...review, again: true });
    }));
    let viewScope;
    let firstRender = true;
    // D62: a page whose teardown must wait (Inspect, while a video is still starting) holds the next route until it
    // settles; the latest route wins.
    let leaveGuard = null, renderToken = 0;
    scope.cleanup(() => viewScope?.dispose());

    // The mounted System page: another of its sections is shown in place, keeping the shell and the drafts.
    let system = null;

    function render(route) {
        const token = ++renderToken;
        const hold = leaveGuard?.();
        if (hold) {
            hold.then(() => { if (token === renderToken && !scope.signal.aborted) renderNow(route); });
            return;
        }
        renderNow(route);
    }

    function renderNow(route) {
        leaveGuard = null;
        // A shared page dialog never stays open over the next route.
        closeWorkspaceDialog();
        if (route.id === 'system' && system && currentKey === 'system') {
            system.show(route.section);
            return;
        }
        system = null;
        viewScope?.dispose();
        images = null;
        viewScope = createScope();
        const view = viewScope;
        currentKey = routeKey(route);
        markCurrent();
        if (route.id === 'saved') {
            openSavedView(route, view);
            return;
        }
        if (route.id === 'collection') {
            openCollectionPage(route, view);
            return;
        }
        renderPage(route, view);
    }

    /** A saved view opens its page with its state; the heading, breadcrumb and title are the view's name. */
    async function openSavedView(route, view) {
        main.replaceChildren();
        await savedViews.settled();
        if (view.signal.aborted) return;
        // A view saved since the list loaded (another tab) is read once more before the link counts as gone.
        if (!savedViews.find(route.savedId) && savedViews.status === 'ready') {
            // settled(), not load()'s own promise: a newer read (a restore, another change) can supersede this one.
            savedViews.load();
            await savedViews.settled();
            if (view.signal.aborted) return;
        }
        const saved = savedViews.find(route.savedId);
        if (saved) {
            const page = routes.find(item => item.id === saved.page);
            renderPage({...page, label:saved.name, title:saved.name}, view, saved);
            mountSavedViewEdit(saved, view);
            return;
        }
        if (savedViews.status === 'ready') {
            // A removed or unknown view: the shell's default page, without a history entry for the dead link.
            window.location.replace('#/images');
            return;
        }
        const {description, h1} = renderHeading(main, {title:route.label});
        focusTitle(h1);
        description.textContent = 'Saved views could not be loaded. Your saved data has not changed.';
        description.setAttribute('role', 'alert');
        const retry = createButton('Retry');
        view.listen(retry, 'click', () => {
            savedViews.load();
            openSavedView(route, view);
        });
        main.append(retry);
    }

    // /* D51 */ An open sidebar item's Edit (maintainer ruling): first in the heading's actions; `open(trigger)` opens
    // its dialog. Saved views and collections (D137) share it.
    function mountEditAction(view, open) {
        // renderPage has just written the heading.
        const edit = createButton('Edit');
        pageHeading(main).actions.prepend(edit);
        view.listen(edit, 'click', () => open(edit));
    }

    // A rename re-reads the list and retitles the open page; a delete re-reads it and replaces the dead route with the
    // view's plain page.
    function mountSavedViewEdit(saved, view) {
        mountEditAction(view, trigger => openEditViewDialog({ scope: view, trigger, view: saved,
            onRename: async name => {
                const renamed = await savedViews.rename(saved.id, name);
                if (view.signal.aborted) return;
                saved = renamed;
                retitle(renamed.name);
            },
            onDelete: async () => {
                await savedViews.remove(saved.id);
                window.location.replace(`#/${saved.page}`);
            } }));
    }

    /** `name` becomes the h1; the breadcrumb and title too unless the page keeps its own (a collection's "Collection"). */
    function retitle(name, title = name) {
        // Only the mounted view retitles (its rename returns once aborted), so its heading is present.
        pageHeading(main).h1.textContent = name;
        document.getElementById('workspace-breadcrumb').textContent = title;
        document.title = `${title} · Prompt Manager`;
    }

    /**
     * #331 D137: `#/collection/<id>` is Images filtered to the collection, its name the h1, "Collection" the breadcrumb
     * and title (concept a1186–1189), with Edit and the note about members the sources do not list now. An unknown id
     * is read once more (another tab may have made it), then shows the deleted-collection page.
     */
    async function openCollectionPage(route, view) {
        main.replaceChildren();
        await collections.settled();
        if (view.signal.aborted) return;
        if (!collections.find(route.collectionId) && collections.status === 'ready') {
            // settled(), not load()'s own promise: the 300 ms count read or a restore can supersede this one.
            collections.load();
            await collections.settled();
            if (view.signal.aborted) return;
        }
        let collection = collections.find(route.collectionId);
        const showAgain = () => { if (!view.signal.aborted) navigateTo(window.location.hash); };
        if (collection) {
            renderPage({...COLLECTION_ROUTE, label:collection.name, title:routeTitle(COLLECTION_ROUTE)}, view, null, {collection:collection.id,
                // The listing found the collection gone meanwhile: the list is read again and, once it no longer lists
                // the collection, the page is shown again (the deleted-collection page). A failed read, or a list that
                // still holds it, leaves the listing's own error and Retry (images.mjs).
                onCollectionMissing:async () => {
                    collections.load();
                    await collections.settled();
                    if (collections.status === 'ready' && !collections.find(route.collectionId)) showAgain();
                }});
            // MC3: the note follows every read of the list for the page's life; none while nothing is missing.
            const note = element('p', '', 'small');
            const showNote = () => {
                const text = missingNote(collections.find(route.collectionId)?.missing);
                note.textContent = text;
                if (!text) note.remove();
                else if (!note.isConnected) pageHeading(main)?.description.after(note);
            };
            showNote();
            view.cleanup(collections.subscribe(showNote));
            // The dialog's Delete question uses the latest stored count.
            mountEditAction(view, trigger => openEditCollectionDialog({ scope: view, trigger,
                collection: collection = {...collection, ...collections.find(collection.id), name:collection.name},
                onRename: async name => {
                    const renamed = await collections.rename(collection.id, name);
                    if (view.signal.aborted) return;
                    collection = {...collection, ...renamed};
                    retitle(renamed.name, routeTitle(COLLECTION_ROUTE));
                },
                onDelete: async () => {
                    await collections.delete(collection.id);
                    window.location.replace('#/images');
                },
                onGone: () => collections.settled().then(showAgain) }));
            return;
        }
        const {description, h1} = renderHeading(main, {title:route.label});
        focusTitle(h1);
        document.title = `${routeTitle(route)} · Prompt Manager`;
        document.getElementById('workspace-breadcrumb').textContent = routeTitle(route);
        if (collections.status === 'ready') {
            // Deleted, or never existed: say so, with the way back to Images.
            description.textContent = DELETED_PAGE_TEXT;
            const link = element('a', OPEN_IMAGES_LINK, 'workspace-link');
            link.href = '#/images';
            main.append(link);
            return;
        }
        description.textContent = LIST_FAILED_TEXT;
        description.setAttribute('role', 'alert');
        const retry = createButton('Retry');
        view.listen(retry, 'click', () => {
            collections.load();
            openCollectionPage(route, view);
        });
        main.append(retry);
    }

    /** The concept focuses its page title on a hashchange navigation (app.js 1168), never on the first render. */
    function focusTitle(h1) {
        if (!firstRender) h1.focus();
        firstRender = false;
    }

    function renderPage(route, view, saved = null, {collection = null, onCollectionMissing = null} = {}) {
        document.title = `${routeTitle(route)} · Prompt Manager`;
        // The concept's heading(); each view fills its description and actions.
        // The concept focuses its page title on a hashchange navigation (app.js 1168); the skip link focuses `main`.
        const { description, h1 } = renderHeading(main, { title: route.label });
        focusTitle(h1);
        document.getElementById('workspace-breadcrumb').textContent = routeTitle(route);
        if (route.id === 'images' || route.id === 'favorites' || route.id === 'collection') {
            mountNewPromptAction(main, api, view);
            images = mountImages(main, api, view, {savedState:saved?.state, favorites:route.id === 'favorites',
                collection, onCollectionMissing, onSaveView:saveView('images', view), activity, collections});
            return;
        }
        if (route.id === 'library') {
            mountLibrary(main, api, view, {positiveId:route.positiveId, savedState:saved?.state, onSaveView:saveView('library', view)});
            return;
        }
        if (route.id === 'tags') {
            mountVocabulary(main, api, view);
            return;
        }
        if (route.id === 'activity') {
            // D56: `#/activity/<id>` is that job's own view; `#/activity` lists the session's jobs.
            if (route.jobId) mountActivityJobView(main, { store: activity, scope: view, api, jobId: route.jobId });
            else mountActivityPage(main, { store: activity, scope: view });
            return;
        }
        if (route.id === 'review') {
            // Needs review (ruling R5): a recorded decision re-reads the row's count, and the open page's total is the
            // count (F3: a change the page hears of costs no count read).
            mountReview(main, { api, scope: view, onDecision: () => refreshCounts(REVIEW_WRITE_COUNTS),
                onTotal: total => setCount('review', total) });
            return;
        }
        if (route.id === 'system') {
            // The concept's System (app.js renderSystem): its heading description, section nav and Save row.
            description.textContent = route.description;
            system = mountSystem(main, api, view, { section: route.section, activity });
            return;
        }
        if (route.id === 'inspect') {
            // The concept's Inspect a file (app.js renderMetadata): a local file, read only in this browser.
            description.textContent = route.description;
            ({ leaveGuard } = mountInspect(main, { parentScope: view, api, sender: createPromptSender({ scope: view }) }));
            return;
        }
        description.textContent = 'A home for your prompts, images and creative history.';
        const stats = element('section', undefined, 'overview-stats');
        stats.setAttribute('aria-label', 'Library statistics');
        main.append(stats);
        const tools = element('section', undefined, 'surface');
        tools.append(element('h2', 'Continue creating'), element('p', 'Pick up where you left off in your images, prompts and tools.'));
        const cards = element('div', undefined, 'tool-grid');
        for (const target of routes.filter(item => item.href)) {
            const card = element('article', undefined, 'tool-card');
            card.append(element('h3', target.label), element('p', target.description, 'small'), cardLink(target));
            cards.append(card);
        }
        tools.append(cards);
        main.append(tools);
        async function loadStats() {
            const status = element('p', 'Loading library statistics…');
            status.setAttribute('role', 'status');
            stats.replaceChildren(status);
            try {
                const result = await api.get('/prompt_manager/stats', { signal: view.signal });
                if (view.signal.aborted) return;
                const values = summarizeStats(result.stats);
                const list = element('dl', undefined, 'stat-grid');
                for (const [label, count] of values) {
                    const stat = element('div', undefined, 'stat');
                    stat.append(element('dt', label), element('dd', count.toLocaleString()));
                    list.append(stat);
                }
                stats.replaceChildren(list);
                if (values.every(([, count]) => count === 0)) {
                    stats.append(element('p', 'Your library is empty. Add a prompt in the prompt library or configure sources in System.', 'empty-state'));
                }
            } catch (error) {
                if (view.signal.aborted) return;
                const alert = element('p', 'Library statistics could not be loaded. Your saved data has not changed.');
                alert.setAttribute('role', 'alert');
                const retry = createButton('Retry');
                view.listen(retry, 'click', loadStats);
                stats.replaceChildren(alert, retry);
            }
        }
        loadStats();
    }
    scope.cleanup(mountRouter(window, render));
    scope.listen(window, 'pagehide', () => scope.dispose());
    return () => scope.dispose();
}

mountWorkspace();
window.addEventListener('pageshow', event => {
    if (event.persisted) mountWorkspace();
});
