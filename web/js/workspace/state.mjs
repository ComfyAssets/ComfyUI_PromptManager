export const routes = [
    { id: 'overview', label: 'Overview' },
    { id: 'images', label: 'Images', href: '/prompt_manager/gallery',
        description: 'Browse your images, inspect their metadata and review generated tags.', action: 'Open existing image gallery' },
    { id: 'library', label: 'Prompt library', href: '/prompt_manager/admin',
        description: 'Find, edit and organize the prompts already in your library.', action: 'Open existing prompt library' },
    { id: 'tags', label: 'Tags & categories', href: '/prompt_manager/admin#/tags',
        description: 'Explore your vocabulary and find the prompts associated with each tag.', action: 'Open existing tag manager' },
    { id: 'inspect', label: 'Inspect a file', href: '/prompt_manager/gallery.html',
        description: 'Inspect generation metadata from an image or video file.', action: 'Open existing metadata inspector' },
    { id: 'system', label: 'System', href: '/prompt_manager/admin',
        description: 'Open the current dashboard, then choose Settings or Maintenance for configuration, scans, backups and diagnostics.', action: 'Open existing administration' },
];

export function resolveRoute(hash) {
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
        const hash = `#/${route.id}`;
        if (win.location.hash !== hash) win.history.replaceState(win.history.state, '', hash);
        if (current === route) return;
        current = route;
        onRoute(route);
    };
    win.addEventListener('hashchange', update);
    update();
    return () => win.removeEventListener('hashchange', update);
}
