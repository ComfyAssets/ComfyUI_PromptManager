/**
 * System › Appearance › Thumbnails (#317): the concept README keeps "Thumbnail quality, size limit" as Appearance
 * settings; they are the server's (`thumbnail_format`, `thumbnail_quality`, `thumbnail_edge`, saved with the settings
 * revision through the one Save row), so every preview writer follows them. One `section.surface` of the concept's
 * `.setting-row`s (system-controls.mjs), after "Browsing & display":
 *
 * - "Thumbnail format": WebP (default), JPEG XL, JPEG. D130: JPEG XL is not in the concept; it is offered only when
 *   the server found an encoder (`thumbnail_encoders.jxl`), else the option is disabled and the row says why, in the
 *   server's words. A row note also says when this browser cannot show JPEG XL thumbnails (thumbnail-support.mjs):
 *   such a browser shows the originals instead.
 * - "Thumbnail quality" (1–100) and "Thumbnail size limit" (128–2048 px); a value outside is refused like Appearance's
 *   timeout (no draft, the control highlighted).
 * - "Convert outdated thumbnails" opens Thumbnail maintenance on its Convert outdated scope; converting is never
 *   automatic. After a save that changed the format, its line says existing previews keep their old format until
 *   converted.
 *
 * Host rows stay disabled until the server settings were read, like Appearance's, and say "Server settings could not
 * be read." while a read failed.
 */
import { settingRow, operationRow } from './system-controls.mjs';
import { detectJxlSupport } from './thumbnail-support.mjs';
import { openThumbnailReview } from './thumbnail-review.mjs';
import { HOST_UNAVAILABLE } from './system-appearance.mjs';

export const FORMAT_OPTIONS = Object.freeze([['webp', 'WebP'], ['jxl', 'JPEG XL'], ['jpeg', 'JPEG']]);
export const QUALITY_LIMIT = Object.freeze({ min: 1, max: 100 });
export const EDGE_LIMIT = Object.freeze({ min: 128, max: 2048 });
export const BROWSER_NO_JXL = 'This browser can’t show JPEG XL thumbnails; it shows the originals instead.';
export const CONVERT_DESCRIPTION = 'Replace previews in an older format from their originals, then remove the old files.';
export const FORMAT_SAVED = 'Format saved. Existing previews keep their old format until you convert them.';

/** A whole number within `{min, max}` from a field's text, else null. */
export function wholeNumber(text, { min, max }) {
    const value = String(text).trim();
    if (!/^\d+$/.test(value)) return null;
    const number = Number(value);
    return number >= min && number <= max ? number : null;
}

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

const ROWS = Object.freeze([
    ['thumbnail-format', 'Thumbnail format', 'WebP is small and fast everywhere. JPEG XL is smaller still where your browser shows it.',
        'thumbnail_format', { type: 'select', options: FORMAT_OPTIONS }],
    ['thumbnail-quality', 'Thumbnail quality', 'From 1 to 100. 80 keeps previews sharp and small.', 'thumbnail_quality',
        { type: 'number', ...QUALITY_LIMIT }],
    ['thumbnail-edge', 'Thumbnail size limit', 'Longest edge in pixels, 128 to 2048. Rebuild all previews to apply a new size.',
        'thumbnail_edge', { type: 'number', ...EDGE_LIMIT }],
]);
const LIMITS = Object.freeze({ thumbnail_quality: QUALITY_LIMIT, thumbnail_edge: EDGE_LIMIT });

/**
 * Mount the Thumbnails surface; returns it. `support()` resolves whether this browser shows JPEG XL, `openReview`
 * opens Thumbnail maintenance (`{mode, trigger}`).
 */
export function mountThumbnailSettings(container, { scope, drafts, api, activity, support = detectJxlSupport,
    openReview = options => openThumbnailReview({ api, store: activity, scope, ...options }) }) {
    const surface = el('section', undefined, 'surface');
    surface.append(el('h2', 'Thumbnails'), el('p', 'Previews in the grid and the inspector cover. Originals are never changed.'));
    const controls = [];
    for (const [id, label, description, key, control] of ROWS) {
        let input;
        const onChange = value => {
            if (LIMITS[key]) {
                value = wholeNumber(input.value, LIMITS[key]);
                if (value === null) { drafts.reject('host', key); return; }
            }
            drafts.set('host', key, value);
        };
        const row = settingRow({ id, label, description, control, scope, onChange });
        input = row.children[1];
        input.dataset.key = key;
        surface.append(row);
        controls.push({ input, key, text: row.children[0] });
    }
    const operation = operationRow({ title: 'Convert outdated thumbnails', description: CONVERT_DESCRIPTION,
        label: 'Convert outdated', scope, onClick: trigger => openReview({ mode: 'convert', trigger }) });
    surface.append(operation);
    const operationText = operation.children[0].children[1];

    let browserJxl;
    let savedFormat;
    const format = controls[0];
    const repaint = () => {
        const encoders = drafts.get('host', 'thumbnail_encoders');
        for (const { input, key } of controls) {
            const value = drafts.get('host', key);
            input.disabled = value === undefined;
            if (value !== undefined && (document.activeElement !== input || key === 'thumbnail_format')) input.value = String(value);
        }
        const jxl = Array.from(format.input.children).find(option => option.value === 'jxl');
        const unavailable = encoders?.jxl?.available === false;
        if (jxl) jxl.disabled = unavailable;
        const failed = drafts.hostState?.() === 'failed';
        for (const row of controls) {
            for (const note of row.notes ?? []) note.remove();
            const lines = [failed && drafts.get('host', row.key) === undefined ? HOST_UNAVAILABLE : null];
            if (row === format) lines.push(unavailable ? encoders.jxl.reason : null, browserJxl === false ? BROWSER_NO_JXL : null);
            row.notes = lines.filter(Boolean).map(line => el('p', line, 'small'));
            row.text.append(...row.notes);
        }
        // A format save (not the first read) leaves the existing previews outdated: say so, never convert.
        const saved = drafts.saved('host').thumbnail_format;
        if (savedFormat !== undefined && saved !== undefined && saved !== savedFormat) operationText.textContent = FORMAT_SAVED;
        if (saved !== undefined) savedFormat = saved;
    };
    scope.cleanup(drafts.subscribe(repaint));
    repaint();
    Promise.resolve(support()).then(value => {
        if (scope.signal.aborted) return;
        browserJxl = value;
        repaint();
    }, () => {});
    return surface;
}
