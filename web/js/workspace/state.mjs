export const routes = [
    { id: 'images', label: 'Images', href: '/prompt_manager/gallery',
        description: 'Browse your images, inspect their metadata and review generated tags.', action: 'Open existing image gallery' },
    { id: 'library', label: 'Prompt library', href: '/prompt_manager/admin',
        description: 'Find, edit and organize the prompts already in your library.', action: 'Open existing prompt library' },
    // Native screen; `title` is the concept's breadcrumb/title, the href stays for Overview's card.
    { id: 'tags', label: 'Tags & categories', title: 'Tags', href: '/prompt_manager/admin#/tags',
        description: 'Explore your vocabulary and find the prompts associated with each tag.', action: 'Open existing tag manager' },
    { id: 'inspect', label: 'Inspect a file', href: '/prompt_manager/gallery.html',
        description: 'Inspect generation metadata from an image or video file.', action: 'Open existing metadata inspector' },
    { id: 'overview', label: 'Overview' },
    { id: 'system', label: 'System', href: '/prompt_manager/admin',
        description: 'Open the current dashboard, then choose Settings or Maintenance for configuration, scans, backups and diagnostics.', action: 'Open existing administration' },
];

const NATIVE_ROUTES = new Set(['images', 'library', 'tags', 'overview']);

/** Breadcrumb and document title: the concept names a few pages differently from their nav label. */
export function routeTitle(route) {
    return route.title || route.label;
}

/** Routes the workspace does not render yet show the generic legacy handoff panel. */
export function isHandoff(route) {
    return !NATIVE_ROUTES.has(route.id);
}

export function resolveRoute(hash) {
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

// The sidebar's real counts (D27): one bounded `limit=1` read each, whose `total` is the count.
const NAV_COUNT_SOURCES = Object.freeze([
    ['images', '/prompt_manager/images/output?mode=workspace&limit=1'],
    ['library', '/prompt_manager/library?limit=1'],
    ['tags', '/prompt_manager/vocabulary/tags?limit=1'],
]);

/** Route id -> count for the WORKSPACE links; null when a page is missing or its total is not a count. */
export function summarizeNavCounts(pages = {}) {
    return new Map(NAV_COUNT_SOURCES.map(([id]) => {
        const total = pages?.[id]?.total;
        return [id, Number.isSafeInteger(total) && total >= 0 ? total : null];
    }));
}

/**
 * Read the three counts; a failed read yields null for its route only. `onCount(id, count)` runs as each read
 * settles, so a slow listing (the Images output scan) never holds back the cheap Library and Tags counts.
 */
/** The WORKSPACE routes that carry a count. */
export const NAV_COUNT_ROUTES = Object.freeze(NAV_COUNT_SOURCES.map(([id]) => id));

/** The counts a confirmed Library or vocabulary write can change; the Images listing cannot. */
export const LIBRARY_WRITE_COUNTS = Object.freeze(['library', 'tags']);

/** Read the given WORKSPACE counts (all by default), reporting each as its own read settles. */
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
        if (current?.id === route.id && current?.positiveId === route.positiveId) return;
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
