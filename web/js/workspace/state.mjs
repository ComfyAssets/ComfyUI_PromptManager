export const routes = [
    // `href` and `action` are Overview's card (#525): every card opens its native page, never a legacy one.
    { id: 'images', label: 'Images', href: '#/images',
        description: 'Browse your images, inspect their metadata and review generated tags.', action: 'Open Images' },
    { id: 'library', label: 'Prompt library', href: '#/library',
        description: 'Find, edit and organize the prompts already in your library.', action: 'Open Prompt library' },
    // `title` is the concept's breadcrumb/title.
    { id: 'tags', label: 'Tags & categories', title: 'Tags', href: '#/tags',
        description: 'Explore your vocabulary and find the prompts associated with each tag.', action: 'Open Tags & categories' },
    // #249 local Inspect.
    { id: 'inspect', label: 'Inspect a file', href: '#/inspect',
        description: 'A standalone file uses the same image viewer and prompt actions.', action: 'Open Inspect a file' },
    { id: 'overview', label: 'Overview' },
    // SAVED VIEWS' first row (#249 D51): Images filtered to favorites. No href, so Overview lists no card for it.
    { id: 'favorites', label: 'Favorites' },
    // SAVED VIEWS' Needs review row (ruling R5): the concept's Metadata review page (app.js renderReview 749–760; its
    // breadcrumb is "Review", app.js render 1175–1188),
    // the queue of ambiguous sampler evidence. No href: no Overview card.
    { id: 'review', label: 'Needs review', title: 'Review' },
    // The concept's Activity (app.js renderActivity 732–738): this server session's jobs. No href: no Overview card.
    { id: 'activity', label: 'Activity' },
    // Native System (#249 System S2): `#/system/<section>`. Q12: Overview's card opens its Overview section; every
    // section is native (S12 retired D94's hand-off). The description is the concept's System heading (app.js renderSystem 700).
    { id: 'system', label: 'System', href: '#/system/overview',
        description: 'Configuration, maintenance and recovery—with browsing kept in the workspace.', action: 'Open System' },
];

/** D165 (#525): the legacy pages, which stay at their URLs; System's Overview section links them discreetly. */
export const LEGACY_PAGES = Object.freeze([
    { label: 'Dashboard', href: '/prompt_manager/admin' },
    { label: 'Tags', href: '/prompt_manager/admin#/tags' },
    { label: 'Gallery', href: '/prompt_manager/gallery' },
    { label: 'Metadata viewer', href: '/prompt_manager/gallery.html' },
].map(page => Object.freeze(page)));

const NATIVE_ROUTES = new Set(['images', 'library', 'tags', 'inspect', 'overview', 'saved', 'favorites', 'activity', 'review', 'system',
    'collection']);

/**
 * System's sections in the concept's order (app.js `systemSections` 635–644); the concept's `general` is production's
 * `overview`, so `#/system/overview` sits beside the production hub `#/overview` (D28).
 */
export const SYSTEM_SECTIONS = Object.freeze([
    ['overview', 'Overview'], ['appearance', 'Appearance'], ['sources', 'Sources & scans'], ['integrations', 'Integrations'],
    ['tagging', 'Auto-tagging'], ['maintenance', 'Maintenance'], ['backups', 'Backup & export'], ['diagnostics', 'Diagnostics & logs'],
].map(section => Object.freeze(section)));
const SYSTEM_SECTION_IDS = new Set(SYSTEM_SECTIONS.map(([id]) => id));

/** Whether `id` names a System section. */
export function isSystemSection(id) {
    return typeof id === 'string' && SYSTEM_SECTION_IDS.has(id);
}

/** A saved view (#249 D16): the shell resolves its id to the view's page, name and state. */
const SAVED_ROUTE = Object.freeze({ id: 'saved', label: 'Saved view' });

/**
 * A collection (#331 D137): `#/collection/<id>` is Images filtered to its members. Not a `routes` entry (no Overview
 * card, no WORKSPACE link); the shell names the page after the collection, the breadcrumb and title stay "Collection"
 * (concept render a1186–1189).
 */
export const COLLECTION_ROUTE = Object.freeze({ id: 'collection', label: 'Collection' });

/** Breadcrumb and document title: the concept names a few pages differently from their nav label. */
export function routeTitle(route) {
    return route.title || route.label;
}

/** Routes the workspace does not render yet show the generic legacy handoff panel. */
export function isHandoff(route) {
    return !NATIVE_ROUTES.has(route.id);
}

export function resolveRoute(hash) {
    const saved = /^#\/saved\/([1-9]\d*)$/.exec(hash);
    if (saved && Number.isSafeInteger(Number(saved[1]))) return {...SAVED_ROUTE, savedId:Number(saved[1]), hash};
    // A collection id is the routes' 1 to 9 digits without a leading zero.
    const collection = /^#\/collection\/([1-9]\d{0,8})$/.exec(hash);
    if (collection) return {...COLLECTION_ROUTE, collectionId:Number(collection[1]), hash};
    // One job of the Activity page (its id is the registry's uuid4 hex).
    const job = /^#\/activity\/([0-9a-f]{32})$/.exec(hash);
    if (job) return {...routes.find(route => route.id === 'activity'), jobId:job[1], hash};
    // `#/system` is the Overview section under the sidebar's own hash; an unknown child is Overview, its hash replaced.
    const system = /^#\/system(?:\/(.*))?$/.exec(hash);
    if (system) {
        const route = routes.find(item => item.id === 'system');
        if (system[1] === undefined) return {...route, section:'overview', hash};
        const section = isSystemSection(system[1]) ? system[1] : 'overview';
        return {...route, section, hash:`#/system/${section}`};
    }
    const match = /^#\/library\/([1-9]\d*)$/.exec(hash);
    if (match && Number.isSafeInteger(Number(match[1]))) {
        return {...routes.find(route => route.id === 'library'), positiveId:Number(match[1]), hash};
    }
    return routes.find(route => hash === `#/${route.id}`) || routes[0];
}

export function summarizeStats(stats) {
    return [['Prompts', 'total_prompts'], ['Images', 'total_images'], ['Tags', 'total_tags']]
        .map(([label, key]) => {
            const count = stats?.[key];
            if (!Number.isSafeInteger(count) || count < 0) throw new Error('Invalid library statistics');
            return [label, count];
        });
}

// The sidebar's real counts (D27): one bounded `limit=1` read each, whose `total` is the count. Favorites (D51) is the
// Favorites page's own listing, so its count is what that page lists.
const NAV_COUNT_SOURCES = Object.freeze([
    ['images', '/prompt_manager/images/output?mode=workspace&limit=1'],
    ['favorites', '/prompt_manager/images/output?mode=workspace&favorites=1&limit=1'],
    ['library', '/prompt_manager/library?limit=1'],
    ['tags', '/prompt_manager/vocabulary/tags?limit=1'],
    // Needs review (ruling R5): the queue's `total` is the exact pending count.
    ['review', '/prompt_manager/review/items?limit=1'],
]);

/** Route id -> count for the sidebar links; null when a page is missing or its total is not a count. */
export function summarizeNavCounts(pages = {}) {
    return new Map(NAV_COUNT_SOURCES.map(([id]) => {
        const total = pages?.[id]?.total;
        return [id, Number.isSafeInteger(total) && total >= 0 ? total : null];
    }));
}

/**
 * Read the counts; a failed read yields null for its route only. `onCount(id, count)` runs as each read settles, so a
 * slow listing (the Images output scan) never holds back the cheap Library and Tags counts.
 */
/** The sidebar routes that carry a count (WORKSPACE, plus SAVED VIEWS' Favorites and Needs review). */
export const NAV_COUNT_ROUTES = Object.freeze(NAV_COUNT_SOURCES.map(([id]) => id));

/** The counts a confirmed Library or vocabulary write can change; the Images listing cannot. */
export const LIBRARY_WRITE_COUNTS = Object.freeze(['library', 'tags']);

/** The count a confirmed favorite change can change. */
export const FAVORITE_WRITE_COUNTS = Object.freeze(['favorites']);
/** The count a review decision or a finished metadata scan can change. */
export const REVIEW_WRITE_COUNTS = Object.freeze(['review']);
/**
 * Job kind -> the counts re-read once when such a job ends: a scan feeds Needs review and can add prompts, and since
 * #498 "Return images to the library" (a scan) shows removed images again; #501: a delete hides images.
 */
export const JOB_END_COUNTS = Object.freeze({
    scan: Object.freeze(['review', 'library', 'images']),
    image_delete: Object.freeze(['images', 'favorites', 'review']),
});
// Trailing delay that coalesces quick star toggles into one Favorites count read (#249 M1).
export const FAVORITE_COUNT_DELAY_MS = 300;

/** Read the given sidebar counts (all by default), reporting each as its own read settles. */
export async function loadNavCounts(api, signal, onCount = () => {}, ids = NAV_COUNT_ROUTES) {
    const pages = {};
    await Promise.all(NAV_COUNT_SOURCES.filter(([id]) => ids.includes(id)).map(async ([id, url]) => {
        try { pages[id] = await api.get(url, { signal }); } catch { pages[id] = undefined; }
        onCount(id, summarizeNavCounts(pages).get(id));
    }));
    return new Map([...summarizeNavCounts(pages)].filter(([id]) => ids.includes(id)));
}

/** Fired on the window to render the current route again, where setting the same hash fires no `hashchange`. */
const ROUTE_REFRESH_EVENT = 'prompt-manager:route-refresh';

/** The concept's `go()` (app.js 292–297): a link to the route already shown re-renders it instead of doing nothing. */
export function navigateTo(hash, win = window) {
    if (win.location.hash === hash) win.dispatchEvent(new Event(ROUTE_REFRESH_EVENT));
    else win.location.hash = hash;
}

export function mountRouter(win, onRoute) {
    let current;
    const refresh = () => {
        current = undefined;
        update();
    };
    const update = () => {
        const route = resolveRoute(win.location.hash);
        const hash = route.hash || `#/${route.id}`;
        if (win.location.hash !== hash) win.history.replaceState(win.history.state, '', hash);
        if (current?.id === route.id && current?.positiveId === route.positiveId && current?.savedId === route.savedId &&
            current?.collectionId === route.collectionId && current?.jobId === route.jobId && current?.section === route.section) return;
        current = route;
        onRoute(route);
    };
    win.addEventListener('hashchange', update);
    win.addEventListener(ROUTE_REFRESH_EVENT, refresh);
    update();
    return () => {
        win.removeEventListener('hashchange', update);
        win.removeEventListener(ROUTE_REFRESH_EVENT, refresh);
    };
}
