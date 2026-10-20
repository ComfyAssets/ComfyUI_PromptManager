// The concept's page heading (docs/concepts/workspace/app.js heading(), 303-305), shared by every workspace route.

function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

function partsOf(heading) {
    return {
        heading,
        h1: heading.querySelector(':scope > div > h1'),
        description: heading.querySelector(':scope > div > p.muted'),
        actions: heading.querySelector(':scope > .heading-actions'),
    };
}

/**
 * Replace `main`'s content with `.heading` > div (`h1#page-title[tabindex=-1]`, `p.muted`) + `.heading-actions`.
 * The h1 is the focus target after a save, and the shell focuses it on every route change after the first, as the
 * concept focuses its page title on hashchange (app.js 1168). The skip link focuses `main` instead.
 */
export function renderHeading(main, { title = '', description = '', actions = [] } = {}) {
    const heading = element('div', 'heading');
    const copy = element('div');
    const h1 = element('h1', undefined, title);
    h1.id = 'page-title';
    h1.tabIndex = -1;
    copy.append(h1, element('p', 'muted', description));
    const actionRow = element('div', 'heading-actions');
    actionRow.append(...actions);
    heading.append(copy, actionRow);
    main.replaceChildren(heading);
    return partsOf(heading);
}

/** The heading the shell rendered into `main`, or null when a view is mounted without one. */
export function pageHeading(main) {
    const heading = main.querySelector(':scope > .heading');
    return heading ? partsOf(heading) : null;
}
