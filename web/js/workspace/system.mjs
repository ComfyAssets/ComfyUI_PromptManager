/**
 * Native System (#249 System S2 Task 5): the concept's `renderSystem` shell (app.js 698–706) at `#/system/<section>`.
 * Under the page heading it renders the section nav (`.section-nav`, one button per section, the shown one
 * `aria-pressed`; a press navigates to its hash), then `.settings-layout` with the section's body and the Save row
 * (`span.small#settings-status` + "Save preferences"). D92: no "Sample configuration" chip, the status copy is
 * truthful and "Save preferences" is aria-disabled while nothing is unsaved. A section not delivered yet shows the
 * per-section hand-off to its legacy tool (D94), never mock placeholders. Overview (system-overview.mjs) and
 * Appearance (system-appearance.mjs) are native.
 *
 * Switching sections keeps the shell (and focus on the pressed button) and disposes the old section's scope, so its
 * listeners and timers never outlive it. The drafts are module-level (Q10): they live for the page session.
 */
import { createButton, createScope } from '../shared/toolkit/core.js';
import { SYSTEM_SECTIONS, navigateTo } from './state.mjs';
import { createSystemDrafts, DRAFT_DOMAINS } from './system-state.mjs';
import { sectionNav } from './system-controls.mjs';
import { healthNotice, mountOverview } from './system-overview.mjs';
import { mountAppearance } from './system-appearance.mjs';
import { readPreferences, reconcilePreferences, subscribePreferences, writePreferences } from './preferences.mjs';

const SETTINGS_URL = '/prompt_manager/settings';
const ADMIN = Object.freeze({ href: '/prompt_manager/admin', label: 'Open existing administration' });
const GALLERY = Object.freeze({ href: '/prompt_manager/gallery', label: 'Open existing image gallery' });
/**
 * D94: where each section's capabilities live until its PR (the plan's legacy capability coverage): the admin page's
 * dashboard, Settings, Maintenance, Backup, Restore, Scan, Logs, Diagnostics and Auto-tag tools, and the gallery's
 * settings, thumbnails, duplicates and cache tools.
 */
export const SECTION_HANDOFFS = Object.freeze({
    sources: [ADMIN, GALLERY],
    integrations: [ADMIN],
    tagging: [ADMIN],
    maintenance: [ADMIN, GALLERY],
    backups: [ADMIN],
    diagnostics: [ADMIN],
});
export const NOT_DELIVERED = 'This setting is not available here yet.';

let drafts = null;

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

/** A domain whose settings no delivered section edits yet: nothing can be drafted, and a write would say it failed. */
const undelivered = async () => { throw new Error(NOT_DELIVERED); };

/**
 * The page session's drafts (Q10). The browser domain is this browser's display defaults (preferences.mjs): a write
 * says whether storage kept it, so the status never claims "Saved in this browser." for a change only this tab holds.
 * The host domain reads and saves the server settings with their revision (#220).
 */
export function systemDrafts(api) {
    drafts ??= createSystemDrafts({
        readBrowser: () => readPreferences().values,
        readRuntime: () => ({}),
        writeBrowser: changes => ({ persisted: writePreferences(changes).persisted }),
        writeRuntime: undelivered,
        async readHost() {
            const result = await api.get(SETTINGS_URL, { cache: 'no-store' });
            // The store reads a missing value as none and a missing revision as unreadable (no host save).
            return { values: result.settings, revision: result.revision };
        },
        writeHost: (changes, { expected_revision }) => api.post(SETTINGS_URL, { ...changes, expected_revision }),
    });
    return drafts;
}

/** D94: the shared hand-off panel, linking each legacy tool that still owns this section. */
function handoff(section) {
    const panel = el('section', undefined, 'surface handoff');
    panel.append(el('h2', 'Your current tools are ready'),
        el('p', 'This workspace is being introduced in stages. Continue in the existing tool with your saved data.'));
    for (const tool of SECTION_HANDOFFS[section]) {
        const link = el('a', tool.label, 'workspace-link');
        link.href = window.ApiClient.resolveUrl(tool.href);
        panel.append(link);
    }
    return [panel];
}

/** D92: highlight the controls of the settings the last save could not save. */
function markFailed(layout, store) {
    const failed = new Set(DRAFT_DOMAINS.flatMap(domain => [...store.failed(domain)]));
    for (const control of layout.querySelectorAll('[data-setting]')) {
        // A row's control names its stored key when that differs from the row id (Appearance's `data-key`).
        if (failed.has(control.dataset.key ?? control.dataset.setting)) control.setAttribute('aria-invalid', 'true');
        else control.removeAttribute('aria-invalid');
    }
}

/**
 * The sections delivered so far: Overview (S2 Task 6), Appearance (S3 Task 8), and Maintenance's notice with "Run health checks" above the
 * hand-off to the maintenance tools that are not native yet (D123).
 */
export const DELIVERED_SECTIONS = Object.freeze({
    overview: mountOverview,
    appearance: mountAppearance,
    maintenance: (container, { api, activity, scope }) => [healthNotice({ api, activity, scope }), ...handoff('maintenance')],
});

/**
 * Mount System under the heading `renderHeading` wrote in `main`. Returns `{show(section)}`, which shows another
 * section in place. `scope` is the page's view scope; `sections` maps a section id to a delivered section's mount
 * (`(container, {api, activity, scope, drafts}) => nodes`), and every other section hands off. `activity` is the page's
 * Activity store, which follows the jobs a section starts.
 *
 * The host values are read on mount (and by the store again after a conflict), so a save carries the current revision;
 * until a read returned one, a host save fails without a request (system-state.mjs). The browser values are read on
 * mount and again whenever another page changes them (a `storage` event).
 */
export function mountSystem(main, api, scope, { section, activity, sections = DELIVERED_SECTIONS, navigate = navigateTo }) {
    const store = systemDrafts(api);
    store.reloadHost();
    store.load('browser');
    // Another tab or the legacy gallery changed the display defaults: clean values follow it; a draft it overtook stays
    // and is flagged (D92's conflict status).
    scope.cleanup(subscribePreferences(window, next => {
        const { conflicts } = reconcilePreferences({ baseline: store.saved('browser'), drafts: store.changes('browser'), next: next.values });
        store.load('browser').then(() => store.conflict('browser', conflicts));
    }));
    const nav = sectionNav({ label: 'System sections', sections: SYSTEM_SECTIONS, active: section, scope,
        onSelect: id => navigate(`#/system/${id}`) });
    const layout = el('div', undefined, 'settings-layout');
    const saveRow = el('div', undefined, 'save-row');
    const status = el('span', undefined, 'small');
    status.id = 'settings-status';
    const save = createButton('Save preferences', 'ca-primary');
    saveRow.append(status, save);
    layout.append(saveRow);
    main.append(nav, layout);

    const paintStatus = () => {
        status.textContent = store.statusText();
        if (store.dirty() && !store.saving()) save.removeAttribute('aria-disabled');
        else save.setAttribute('aria-disabled', 'true');
        markFailed(layout, store);
    };
    scope.cleanup(store.subscribe(paintStatus));
    scope.listen(save, 'click', () => {
        if (save.getAttribute('aria-disabled') === 'true') return;
        const saving = store.save();
        paintStatus();
        saving.then(() => { if (!scope.signal.aborted) paintStatus(); });
    });

    let sectionScope = null;
    scope.cleanup(() => sectionScope?.dispose());
    function show(next) {
        sectionScope?.dispose();
        sectionScope = createScope();
        for (const button of nav.children) button.setAttribute('aria-pressed', String(button.dataset.value === next));
        while (layout.firstChild !== saveRow) layout.firstChild.remove();
        const mount = sections[next];
        const nodes = mount ? mount(layout, { api, activity, scope: sectionScope, drafts: store }) : handoff(next);
        layout.prepend(...nodes);
        paintStatus();
    }
    show(section);
    return { show };
}
