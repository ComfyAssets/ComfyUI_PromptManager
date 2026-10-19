import '../api-client.js';
import { createButton, createField, createScope, createSelect } from '../shared/toolkit/core.js';
import { routes, mountRouter, summarizeStats } from './state.mjs';
import { mountWorkspaceTheme } from './theme.mjs';
import { mountImages } from './images.mjs';
import { mountLibrary, mountNewPromptAction } from './library.mjs';
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
    const nav = document.querySelector('nav[aria-label="Workspace"]');
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
    const icons = { images: 'images', library: 'library', tags: 'tags', inspect: 'upload', overview: 'overview', system: 'settings' };
    const groups = [['primary', 'WORKSPACE', ['images', 'library', 'tags', 'inspect']], ['tools', 'TOOLS', ['overview', 'system']]];
    nav.replaceChildren(...groups.map(([name, label, ids]) => {
        const group = element('div', undefined, `workspace-nav-${name}`);
        group.append(element('p', label, 'sidebar-label'));
        for (const id of ids) {
            const route = routes.find(item => item.id === id);
            const link = element('a');
            link.append(createWorkspaceIcon(icons[id]), element('span', route.label));
            link.href = `#/${route.id}`;
            link.dataset.route = route.id;
            group.append(link);
        }
        return group;
    }));
    let viewScope;
    let firstRender = true;
    scope.cleanup(() => viewScope?.dispose());

    function render(route) {
        viewScope?.dispose();
        viewScope = createScope();
        const view = viewScope;
        for (const link of nav.querySelectorAll('[data-route]')) {
            if (link.dataset.route === route.id) link.setAttribute('aria-current', 'page');
            else link.removeAttribute('aria-current');
        }
        document.title = `${route.label} · Prompt Manager`;
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
        document.getElementById('workspace-breadcrumb').textContent = route.label;
        if (route.id === 'images') {
            mountNewPromptAction(main, api, view);
            mountImages(main, api, view);
            return;
        }
        if (route.id === 'library') {
            mountLibrary(main, api, view, {positiveId:route.positiveId});
            return;
        }
        if (route.id !== 'overview') {
            headingCopy.append(element('p', route.description, 'intro'));
            const panel = element('section', undefined, 'panel handoff');
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
        const tools = element('section', undefined, 'panel');
        tools.append(element('h2', 'Continue creating'), element('p', 'Your existing tools remain available while the new workspaces take shape.'));
        const cards = element('div', undefined, 'tool-grid');
        for (const target of routes.filter(item => item.href)) {
            const card = element('article', undefined, 'tool-card');
            card.append(element('h3', target.label), element('p', target.description), legacyLink(target));
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
                    const stat = element('div', undefined, 'panel stat');
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
