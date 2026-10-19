const paths = Object.freeze({
    "images": "M3 3h18v18H3z M3 16l5-5 5 5 4-4 4 4 M8 7h.01",
    "library": "M4 4h5v16H4z M11 4h4v16h-4z M17 5l3-1 3 15-3 1z",
    "tags": "M3 3h8l10 10-8 8L3 11V3ZM7 7h.01",
    "upload": "M12 16V3m-5 5 5-5 5 5M3 15v6h18v-6",
    "overview": "M3 12h4l3-8 4 16 3-8h4",
    "settings": "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8ZM12 2v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10 2 2M5 19l2-2M17 7l2-2",
    "grid": "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
    "list": "M9 5h12M9 12h12M9 19h12M3 5h.01M3 12h.01M3 19h.01",
    "masonry": "M3 3h7v11H3zM14 3h7v6h-7zM3 18h7v3H3zM14 13h7v8h-7z",
    "search": "M10.5 3a7.5 7.5 0 1 0 0 15 7.5 7.5 0 0 0 0-15Zm5.5 13 5 5",
    "info": "M12 8h.01M12 11v6M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Z",
    "copy": "M8 8h13v13H8z M16 8V3H3v13h5",
    "send": "m22 2-7 20-4-9-9-4L22 2ZM22 2 11 13",
    "plus": "M12 4v16M4 12h16",
    "branch": "M6 3v12M18 9a9 9 0 0 1-9 9M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0M21 6a3 3 0 1 1-6 0 3 3 0 0 1 6 0"
});

/** Create an approved decorative outline icon without injecting markup. */
export function createWorkspaceIcon(name, doc = document) {
    if (typeof name !== 'string' || !Object.hasOwn(paths, name)) {
        throw new TypeError('Unknown workspace icon');
    }
    const namespace = 'http://www.w3.org/2000/svg';
    const icon = doc.createElementNS(namespace, 'svg');
    for (const [attribute, value] of Object.entries({
        class: 'workspace-icon', viewBox: '0 0 24 24', 'aria-hidden': 'true',
        focusable: 'false', fill: 'none', stroke: 'currentColor',
        'stroke-width': '1.65', 'stroke-linecap': 'round', 'stroke-linejoin': 'round',
    })) icon.setAttribute(attribute, value);
    const path = doc.createElementNS(namespace, 'path');
    path.setAttribute('d', paths[name]);
    icon.append(path);
    return icon;
}
