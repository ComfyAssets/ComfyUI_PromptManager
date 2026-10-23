/**
 * System › Appearance (#249 System S3 Task 8): the concept's `renderSystem` "appearance" body (app.js 676–678), one
 * `section.surface` "Browsing & display" of `.setting-row`s whose values are the drafts of the one Save row.
 *
 * - The browser rows are the display defaults this browser keeps in the shared `gallerySettings` object
 *   (preferences.mjs, Q3): Default layout and Thumbnail size (`workspaceLayout`, `workspaceDensity`), the metadata,
 *   quality (Optimized→`medium`, Original→`high`, Fast→`low`), video and path rows. Images, the inspector and the
 *   viewer read them on their next visit; a toolbar change still applies to the current view only.
 * - "Open from ComfyUI" (New tab→`newtab`, Popup window→`popup`) and "Node search auto-hide (seconds)" (a whole number
 *   0–300) are the server's `webui_display_mode` and `result_timeout`, saved with the settings revision (#220).
 * - "Continuous browsing" is the concept's fixed row: there is no paging or column control (README ruling).
 * - D97: added rows for the legacy gallery settings the concept leaves out: "Show image details on cards"
 *   (`showImageInfo`), "Collapse viewer details by default" (`sidebarCollapsedByDefault`) and the operation "Reset
 *   display defaults", which drafts the gallery's defaults for these browser rows and touches no other key.
 *
 * Controls follow the store: a value read later (the host settings, another tab's change) repaints them. A host row
 * whose value could not be read stays disabled and says so ("Server settings could not be read.").
 */
import { settingRow, operationRow } from './system-controls.mjs';
import { preferenceDefaults } from './preferences.mjs';

export const RESULT_TIMEOUT_MAX = 300;
/** A host row's line while the server settings could not be read (its control stays disabled). */
export const HOST_UNAVAILABLE = 'Server settings could not be read.';
const BOOLEAN = Object.freeze({ type: 'toggle' });

/**
 * The concept's rows in its order, then the D97 rows: `[id, label, description, domain, key, control]`. A select's
 * options are `[stored value, concept label]`.
 */
export const APPEARANCE_ROWS = Object.freeze([
    ['default-view', 'Default layout', 'Choose your starting image layout.', 'browser', 'workspaceLayout',
        { type: 'select', options: [['grid', 'Grid'], ['list', 'List'], ['masonry', 'Masonry']] }],
    ['density', 'Thumbnail size', 'Readable previews by default; no fixed column-count setting.', 'browser', 'workspaceDensity',
        { type: 'select', options: [['comfortable', 'Comfortable'], ['large', 'Large'], ['compact', 'Compact']] }],
    'continuous',
    ['metadata-auto', 'Load metadata on selection', 'Use the same inspector in the gallery and image viewer.', 'browser', 'autoLoadMetadata', BOOLEAN],
    ['metadata-cache', 'Cache metadata in this browser', 'Reuse fetched metadata until it changes or you clear this cache.', 'browser', 'cacheMetadata', BOOLEAN],
    ['image-quality', 'Image loading quality', 'Preserve original / optimized / fast image-loading preferences.', 'browser', 'imageQuality',
        { type: 'select', options: [['medium', 'Optimized'], ['high', 'Original'], ['low', 'Fast']] }],
    ['video-auto', 'Autoplay videos', 'Videos stay within the same gallery and use native playback controls.', 'browser', 'videoAutoplay', BOOLEAN],
    ['video-muted', 'Mute videos by default', 'Keep playback quiet while browsing.', 'browser', 'videoMute', BOOLEAN],
    ['video-loop', 'Loop videos', 'Repeat a selected clip until you navigate away.', 'browser', 'videoLoop', BOOLEAN],
    ['paths', 'Show file paths', 'Paths appear under File & provenance, not across every card.', 'browser', 'showFilePaths', BOOLEAN],
    ['open-mode', 'Open from ComfyUI', 'Keep the node pack’s popup / new-tab preference.', 'host', 'webui_display_mode',
        { type: 'select', options: [['newtab', 'New tab'], ['popup', 'Popup window']] }],
    ['timeout', 'Node search auto-hide (seconds)', 'Node search behavior only. Use 0 to keep results visible.', 'host', 'result_timeout',
        { type: 'number' }],
    // D97: the legacy gallery's card and viewer settings.
    ['card-details', 'Show image details on cards', 'Show each card’s title and file details under the preview.', 'browser', 'showImageInfo', BOOLEAN],
    ['viewer-details', 'Collapse viewer details by default', 'Open the image viewer with its details panel hidden.', 'browser',
        'sidebarCollapsedByDefault', BOOLEAN],
]);

/** The browser keys "Reset display defaults" restores (D97). */
export const DISPLAY_KEYS = Object.freeze(APPEARANCE_ROWS.filter(row => Array.isArray(row) && row[3] === 'browser').map(row => row[4]));

/** A whole number of seconds from 0 to 300 (the server's `validate_result_timeout`), else null. */
export function resultTimeout(text) {
    const value = String(text).trim();
    if (!/^\d+$/.test(value)) return null;
    const seconds = Number(value);
    return seconds <= RESULT_TIMEOUT_MAX ? seconds : null;
}

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

/** The concept's fixed "Continuous browsing" row: `div > h3 + p` and the "Infinite scroll" chip. */
function continuousRow() {
    const row = el('div', undefined, 'setting-row');
    const text = el('div');
    text.append(el('h3', 'Continuous browsing'),
        el('p', 'Images load automatically as you scroll in Grid, List and Masonry. Batch sizing is handled internally.'));
    row.append(text, el('span', 'Infinite scroll', 'chip blue'));
    return row;
}

/** The row's control (`data-setting`), wherever the Toolkit switch nests it. */
function controlOf(node, id) {
    if (node.dataset?.setting === id) return node;
    for (const child of node.children ?? []) {
        const found = controlOf(child, id);
        if (found) return found;
    }
    return null;
}

/** Show a stored value in its control; a value not read yet leaves the control disabled. */
function paint(input, control, value) {
    input.disabled = value === undefined;
    if (value === undefined) return;
    if (control.type === 'toggle') input.checked = value === true;
    else if (document.activeElement !== input || control.type === 'select') input.value = String(value);
}

/** Mount Appearance in `container` (System's `.settings-layout`); returns the section's nodes. */
export function mountAppearance(container, { scope, drafts }) {
    const surface = el('section', undefined, 'surface');
    surface.append(el('h2', 'Browsing & display'), el('p', 'One set of defaults; toolbar changes apply to the current view.'));
    const controls = [];
    for (const row of APPEARANCE_ROWS) {
        if (row === 'continuous') { surface.append(continuousRow()); continue; }
        const [id, label, description, domain, key, control] = row;
        const onChange = value => {
            if (key === 'result_timeout') {
                const seconds = resultTimeout(input.value);
                if (seconds === null) { drafts.reject(domain, key); return; }
                value = seconds;
            }
            drafts.set(domain, key, value);
        };
        const node = settingRow({ id, label, description, control, scope, onChange });
        surface.append(node);
        const input = controlOf(node, id);
        input.dataset.key = key;
        controls.push({ input, control, domain, key, text: node.children[0], note: null });
    }
    surface.append(operationRow({ title: 'Reset display defaults',
        description: 'Restore this browser’s display defaults, then save. Other settings are kept.', label: 'Reset', scope,
        onClick: () => {
            const defaults = preferenceDefaults();
            for (const key of DISPLAY_KEYS) drafts.set('browser', key, defaults[key]);
        } }));
    const repaint = () => {
        const unavailable = drafts.hostState?.() === 'failed';
        for (const row of controls) {
            const value = drafts.get(row.domain, row.key);
            paint(row.input, row.control, value);
            if (row.domain !== 'host') continue;
            if (unavailable && value === undefined) {
                if (!row.note) { row.note = el('p', HOST_UNAVAILABLE, 'small'); row.text.append(row.note); }
            } else if (row.note) { row.note.remove(); row.note = null; }
        }
    };
    scope.cleanup(drafts.subscribe(repaint));
    repaint();
    return [surface];
}
