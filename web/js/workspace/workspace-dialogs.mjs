/**
 * The concept's shared page dialogs in its one modal shell (docs/concepts/workspace/index.html `dialog#modal`:
 * `.dialog-header` > `h2#modal-title` + `×` "Close dialog", then `#modal-body`). Opening a dialog while one is open
 * swaps its content, as the concept's `dialog()` does. Closing, by ×, Escape or an action, returns focus to the
 * control that opened it.
 *
 * - "Refine this view" (a1348–1364): Source / Folder / Media / Tags, each optional per page; "Apply filters" because
 *   every field is a real filter, and no "Prompt evidence" field because evidence is only known per file (D14).
 * - "Import & inspect" (a1483–1491): only the real operations, Inspect a local image and Scan an output folder →
 *   Rescan; no "Import portable recipes" (D15).
 * - "Copy prompt" (a1223–1241): the select-to-copy fallback when the clipboard is unavailable (D22).
 */
import { createButton } from '../shared/toolkit/core.js';

const FIELD_LABELS = Object.freeze({ source: 'Source', folder: 'Folder', media: 'Media', tags: 'Tags' });
const FIELD_ORDER = Object.keys(FIELD_LABELS);
const REFINE_NOTE = 'These filters apply to this view and combine with the search and category above.';

let shell = null;
let session = null;

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

function field(label, control, id) {
    const wrapper = el('div', undefined, 'field');
    const caption = el('label', label);
    caption.htmlFor = id;
    control.id = id;
    wrapper.append(caption, control);
    return wrapper;
}

function createShell() {
    const dialog = el('dialog');
    dialog.id = 'modal';
    dialog.setAttribute('aria-labelledby', 'modal-title');
    const header = el('div', undefined, 'dialog-header');
    const title = el('h2');
    title.id = 'modal-title';
    const close = createButton('×');
    close.id = 'modal-close';
    close.setAttribute('aria-label', 'Close dialog');
    const body = el('div');
    body.id = 'modal-body';
    header.append(title, close);
    dialog.append(header, body);
    close.addEventListener('click', () => finish());
    dialog.addEventListener('cancel', event => {
        event.preventDefault();
        finish();
    });
    // A browser may still close a modal on its own; that is a dismissal too.
    dialog.addEventListener('close', () => finish());
    return { dialog, title, body, close };
}

/** Close the open dialog, if any, and focus its trigger while that is still on the page. */
function finish() {
    if (!session) return;
    const { trigger } = session;
    session = null;
    if (shell?.dialog.open) shell.dialog.close();
    if (trigger?.isConnected) trigger.focus();
}

function show(heading, nodes, trigger, focusTarget) {
    shell ??= createShell();
    if (!shell.dialog.isConnected) document.body.append(shell.dialog);
    session = { trigger: trigger ?? session?.trigger ?? null };
    shell.title.textContent = heading;
    shell.body.replaceChildren(...nodes);
    if (!shell.dialog.open) shell.dialog.showModal();
    (focusTarget ?? shell.close).focus();
}

/** Close any open workspace dialog and remove the shell; a page calls this when it unmounts. */
export function closeWorkspaceDialog() {
    finish();
    shell?.dialog.remove();
    shell = null;
}

function selectControl(key, options) {
    if (!Array.isArray(options) || options.length === 0 ||
        options.some(pair => !Array.isArray(pair) || typeof pair[0] !== 'string' || typeof pair[1] !== 'string')) {
        throw new TypeError(`The ${key} filter needs [value, label] options`);
    }
    const select = el('select');
    select.replaceChildren(...options.map(([value, label]) => {
        const node = el('option', label);
        node.value = value;
        return node;
    }));
    return select;
}

/**
 * "Refine this view". `fields` lists the page's filters in any subset of Source, Folder, Media and Tags, as
 * `{key, options}` with `[value, label]` options for the selects (the first is the cleared value) and no options for
 * Tags. `values` holds the current value per key. Apply reports the edited values; Clear resets every field and
 * reports the cleared values. Both close the dialog first.
 */
export function openRefineDialog({ fields, values = {}, onApply, onClear, trigger = null, note = REFINE_NOTE }) {
    if (!Array.isArray(fields) || fields.some(item => !FIELD_ORDER.includes(item?.key))) {
        throw new TypeError('Refine fields are source, folder, media and tags');
    }
    const controls = FIELD_ORDER.flatMap(key => {
        const item = fields.find(candidate => candidate.key === key);
        if (!item) return [];
        if (key === 'tags') {
            const input = el('input');
            input.placeholder = 'e.g. cinematic';
            input.value = typeof values.tags === 'string' ? values.tags : '';
            return [{ key, control: input, cleared: '' }];
        }
        const select = selectControl(key, item.options);
        const known = item.options.some(([value]) => value === values[key]);
        select.value = known ? values[key] : item.options[0][0];
        return [{ key, control: select, cleared: item.options[0][0] }];
    });
    const read = () => Object.fromEntries(controls.map(({ key, control }) =>
        [key, key === 'tags' ? control.value.trim() : control.value]));

    const clear = createButton('Clear filters');
    const apply = createButton('Apply filters', 'ca-primary');
    clear.addEventListener('click', () => {
        for (const { control, cleared } of controls) control.value = cleared;
        const reset = read();
        finish();
        onClear(reset);
    });
    apply.addEventListener('click', () => {
        const applied = read();
        finish();
        onApply(applied);
    });
    const actions = el('div', undefined, 'dialog-actions');
    actions.append(clear, apply);
    show('Refine this view', [
        ...controls.map(({ key, control }) => field(FIELD_LABELS[key], control, `filter-${key}`)),
        el('p', note, 'small'),
        actions,
    ], trigger);
}

function operation(title, description, label, onAction) {
    const row = el('div', undefined, 'operation');
    const text = el('div');
    text.append(el('h3', title), el('p', description));
    const actions = el('div', undefined, 'row-actions');
    const action = createButton(label);
    action.addEventListener('click', () => {
        finish();
        onAction();
    });
    actions.append(action);
    row.append(text, actions);
    return row;
}

/** "Import & inspect" with the two real operations (D15). */
export function openImportDialog({ onInspect, onRescan, trigger = null }) {
    const list = el('div', undefined, 'operation-list');
    list.append(
        operation('Inspect a local image', 'Read embedded metadata before deciding to add it.', 'Open inspector', onInspect),
        operation('Scan an output folder', 'Find files added or removed in the output folders.', 'Rescan', onRescan),
    );
    show('Import & inspect', [
        el('p', 'Image inspection and output scanning have different effects. Choose the workflow you want.', 'small'),
        list,
    ], trigger);
}

/** "Copy prompt": the text in a readonly, pre-selected textarea for a manual copy. */
export function openCopyFallback(text, { trigger = null } = {}) {
    if (typeof text !== 'string') throw new TypeError('Copy text must be a string');
    const area = el('textarea');
    area.readOnly = true;
    area.value = text;
    show('Copy prompt', [
        field('Select and copy this text', area, 'copy-text'),
        el('p', 'Automatic clipboard access requires a secure browser context.', 'small'),
    ], trigger, area);
    area.select();
}
