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

export function mountRouter(win, onRoute) {
    let current;
    const update = () => {
        const route = resolveRoute(win.location.hash);
        const hash = route.hash || `#/${route.id}`;
        if (win.location.hash !== hash) win.history.replaceState(win.history.state, '', hash);
        if (current?.id === route.id && current?.positiveId === route.positiveId) return;
        current = route;
        onRoute(route);
    };
    win.addEventListener('hashchange', update);
    update();
    return () => win.removeEventListener('hashchange', update);
}
