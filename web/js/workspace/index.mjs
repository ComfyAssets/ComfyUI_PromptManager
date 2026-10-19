import '../api-client.js';
import { createButton, createField, createScope, createSelect } from '../shared/toolkit/core.js';
import { routes, mountRouter, summarizeStats, routeTitle, isHandoff, loadNavCounts } from './state.mjs';
import { mountWorkspaceTheme } from './theme.mjs';
import { mountImages } from './images.mjs';
import { mountLibrary, mountNewPromptAction, onLibraryWrite } from './library.mjs';
import { mountVocabulary } from './vocabulary.mjs';
import { createWorkspaceIcon } from './icons.mjs';

const { createApiClient, resolveUrl } = window.ApiClient;

function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

function legacyLink(route) {
    const link = element('a', route.action, 'workspace-link');
    link.href = resolveUrl(route.href);
    return link;
}

function mountWorkspace() {
    const scope = createScope();
    const api = createApiClient();
    const main = document.getElementById('workspace-main');
    const primaryNav = document.getElementById('primary-nav');
    const systemNav = document.getElementById('system-nav');
    const appearance = document.getElementById('appearance-control');
    scope.listen(document.querySelector('.skip'), 'click', event => {
        event.preventDefault();
        main.focus();
    });
    const theme = mountWorkspaceTheme({ root: document.body, scope });
    const preference = createSelect([['follow', 'Follow ComfyUI'], ['light', 'Light'], ['dark', 'Dark']], '');
    preference.value = theme.preference;
    const appearanceField = createField('Appearance', preference, 'workspace-appearance');
    appearanceField.classList.add('workspace-appearance');
    appearance.replaceChildren(appearanceField);
    scope.listen(preference, 'change', () => theme.setPreference(preference.value));
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
    // D28: System, then the production Overview hub, in the concept's tools-and-system group.
    systemNav.replaceChildren(...navLinks([['system', 'settings'], ['overview', 'overview']]));
    const sidebar = document.querySelector('aside.sidebar');
    let countsRequest;
    function refreshCounts() {
        countsRequest?.abort();
        const request = countsRequest = new AbortController();
        loadNavCounts(api, request.signal).then(counts => {
            if (request.signal.aborted) return;
            countsRequest = null;
            // A count that failed to refresh is removed, never left stale.
            for (const link of primaryNav.querySelectorAll('[data-route]')) {
                link.querySelector('.nav-count')?.remove();
                const count = counts.get(link.dataset.route);
                if (Number.isSafeInteger(count)) link.append(element('span', count.toLocaleString(), 'nav-count'));
            }
        });
    }
    scope.cleanup(() => countsRequest?.abort());
    scope.cleanup(onLibraryWrite(refreshCounts));
    refreshCounts();
    let viewScope;
    let firstRender = true;
    scope.cleanup(() => viewScope?.dispose());

    function render(route) {
        viewScope?.dispose();
        viewScope = createScope();
        const view = viewScope;
        for (const link of sidebar.querySelectorAll('nav [data-route]')) {
            if (link.dataset.route === route.id) link.setAttribute('aria-current', 'page');
            else link.removeAttribute('aria-current');
        }
        document.title = `${routeTitle(route)} · Prompt Manager`;
        const heading = element('h1', route.label);
        // Focus target after a save, as the concept's page title.
        heading.tabIndex = -1;
        const headingCopy = element('div', undefined, 'workspace-heading-copy');
        headingCopy.append(heading);
        const headingRegion = element('header', undefined, 'workspace-heading');
        headingRegion.append(headingCopy);
        main.replaceChildren(headingRegion);
        if (!firstRender) main.focus();
        firstRender = false;
        document.getElementById('workspace-breadcrumb').textContent = routeTitle(route);
        if (route.id === 'images') {
            mountNewPromptAction(main, api, view);
            mountImages(main, api, view);
            return;
        }
        if (route.id === 'library') {
            mountLibrary(main, api, view, {positiveId:route.positiveId});
            return;
        }
        if (route.id === 'tags') {
            mountVocabulary(main, api, view);
            return;
        }
        if (isHandoff(route)) {
            headingCopy.append(element('p', route.description, 'intro'));
            const panel = element('section', undefined, 'surface handoff');
            panel.append(element('h2', 'Your current tools are ready'),
                element('p', 'This workspace is being introduced in stages. Continue in the existing tool with your saved data.'),
                legacyLink(route));
            main.append(panel);
            return;
        }
        headingCopy.append(element('p', 'A home for your prompts, images and creative history.', 'intro'));
        const stats = element('section', undefined, 'overview-stats');
        stats.setAttribute('aria-label', 'Library statistics');
        main.append(stats);
        const tools = element('section', undefined, 'surface');
        tools.append(element('h2', 'Continue creating'), element('p', 'Your existing tools remain available while the new workspaces take shape.'));
        const cards = element('div', undefined, 'tool-grid');
        for (const target of routes.filter(item => item.href)) {
            const card = element('article', undefined, 'tool-card');
            card.append(element('h3', target.label), element('p', target.description, 'small'), legacyLink(target));
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
