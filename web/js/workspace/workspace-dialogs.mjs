/**
 * The concept's shared page dialogs in its one modal shell (docs/concepts/workspace/index.html `dialog#modal`:
 * `.dialog-header` > `h2#modal-title` + `×` "Close dialog", then `#modal-body`). Opening a dialog while one is open
 * swaps its content, as the concept's `dialog()` does. Closing, by ×, Escape or an action, returns focus to the
 * control that opened it.
 *
 * - "Refine this view" (a1348–1364): Source / Folder / Media / Tags, each optional per page; "Apply filters" and no
 *   mock-status note because every field is a real filter, and no "Prompt evidence" field because evidence is only
 *   known per file (D14). Clear also clears the toolbar category, as the concept's does; the page applies it.
 * - "Import & inspect" (a1483–1491): only the real operations, Inspect a local image and Scan an output folder →
 *   Rescan; no "Import portable recipes" (D15). Rescan shows its running state (aria-disabled "Rescanning…"). D55: the
 *   concept's own "Scan an output folder → Review scan" row follows as "Scan for metadata".
 * - "Review metadata scan" (a1428–1432 `scan-review`, D55): Sources and Scope in a `dl.meta-grid`, the concept's
 *   sentence, a backup `.notice` ("Back up now") and Cancel / Start scan; no sample-plan note.
 * - "Copy prompt" (a1223–1241): the select-to-copy fallback when the clipboard is unavailable (D22).
 * - One-value input (a1138 `simpleInput`): a "Category"-style field (`input#action-input`) and Cancel / a primary
 *   action; used by the Images selection bar's "Set category" (#249 D8) and the toolbar's Save view.
 * - Content (a1282–1304 `dialog(title, body)`): a page-built body such as the history's "All images for this
 *   positive" and "Repeat run" media (#249 D26); `onClose` runs once when that body leaves the shell.
 *
 * Every opener takes the page `scope`: disposing it closes and removes the shell, so a dialog never outlives its page.
 */
import { createButton } from '../shared/toolkit/core.js';

const FIELD_LABELS = Object.freeze({ source: 'Source', folder: 'Folder', media: 'Media', tags: 'Tags' });
const FIELD_ORDER = Object.keys(FIELD_LABELS);

let shell = null;
let session = null;

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

/** A labelled `.field` row: `label` names `control`, which takes `id` (shared with the saved-view Edit dialog). */
export function field(label, control, id) {
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
    // A browser may still close a modal on its own; that is a dismissal too. `close` arrives as a later task, so it
    // is ignored once this shell was reopened or replaced.
    dialog.addEventListener('close', () => {
        if (shell?.dialog === dialog && !dialog.open) finish();
    });
    return { dialog, title, body, close };
}

/** Close the open dialog, if any, and focus its trigger while that is still on the page. */
function finish() {
    if (!session) return;
    const { trigger, onClose } = session;
    session = null;
    if (shell?.dialog.open) shell.dialog.close();
    onClose?.();
    if (trigger?.isConnected) trigger.focus();
}

const boundScopes = new WeakSet();

function requireScope(scope) {
    if (typeof scope?.cleanup !== 'function') throw new TypeError('Workspace dialogs need the page scope');
}

function requireCallbacks(callbacks) {
    for (const [name, callback] of Object.entries(callbacks)) {
        if (typeof callback !== 'function') throw new TypeError(`The dialog needs ${name}`);
    }
}

/** Disposing a page scope closes the shell unless a newer page's dialog is open in it. */
function bindScope(scope) {
    if (boundScopes.has(scope)) return;
    boundScopes.add(scope);
    scope.cleanup(() => {
        boundScopes.delete(scope);
        if (!session || session.scope === scope) closeWorkspaceDialog();
    });
}

function show(heading, nodes, { scope, trigger, focusTarget, onClose = null }) {
    shell ??= createShell();
    if (!shell.dialog.isConnected) document.body.append(shell.dialog);
    bindScope(scope);
    // A swapped-out body is gone from the shell, so its owner releases it.
    const replaced = session?.onClose;
    session = { scope, trigger: trigger ?? session?.trigger ?? null, onClose };
    replaced?.();
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

function fillSelect(select, key, options) {
    if (!Array.isArray(options) || options.length === 0 ||
        options.some(pair => !Array.isArray(pair) || typeof pair[0] !== 'string' || typeof pair[1] !== 'string')) {
        throw new TypeError(`The ${key} filter needs [value, label] options`);
    }
    select.replaceChildren(...options.map(([value, label]) => {
        const node = el('option', label);
        node.value = value;
        return node;
    }));
}

function selectControl(key, options) {
    const select = el('select');
    fillSelect(select, key, options);
    return select;
}

/** Show `value` when the select offers it, else its first (cleared) option. */
function choose(select, value) {
    const known = [...select.options].some(option => option.value === value);
    select.value = known ? value : select.options[0].value;
}

/**
 * "Refine this view". `fields` lists the page's filters in any subset of Source, Folder, Media and Tags, as
 * `{key, options}` with `[value, label]` options for the selects (the first is the cleared value) and no options for
 * Tags; Folder may add `optionsFor(source)`, the options of one Source, so its list follows the chosen Source and a
 * folder that Source lacks returns to the first option. `values` holds the current value per key. Apply reports the edited values; Clear resets every field and
 * reports the cleared values plus `category: ''`, so the page clears its toolbar category too. Both close first.
 * Returns `{setOptions(key, options)}` for options discovered after the dialog opened (a slow folder discovery): the
 * select is refilled and shows the value the field opened with, or the one chosen since, once it is offered; a Folder
 * that follows its Source is refilled from `optionsFor` again. It does nothing once this dialog closed or was replaced.
 */
export function openRefineDialog({ scope, fields, values = {}, onApply, onClear, trigger = null }) {
    requireScope(scope);
    requireCallbacks({ onApply, onClear });
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
        choose(select, values[key]);
        return [{ key, control: select, cleared: item.options[0][0], optionsFor: item.optionsFor }];
    });
    // Each select's wanted value: the one it opened with until the person picks another (see setOptions).
    const wanted = new Map(controls.map(({ key }) => [key, values[key]]));
    for (const { key, control } of controls) {
        if (key !== 'tags') control.addEventListener('change', () => wanted.set(key, control.value));
    }
    const source = controls.find(item => item.key === 'source');
    const folder = controls.find(item => item.key === 'folder' && typeof item.optionsFor === 'function');
    const followSource = (chosen = folder.control.value) => {
        fillSelect(folder.control, 'folder', folder.optionsFor(source.control.value));
        choose(folder.control, chosen);
    };
    if (source && folder) {
        followSource(values.folder);
        // A Source that lacks the chosen folder clears it, and that cleared folder is the wanted one from then on.
        source.control.addEventListener('change', () => { followSource(); wanted.set('folder', folder.control.value); });
    }
    const read = () => Object.fromEntries(controls.map(({ key, control }) =>
        [key, key === 'tags' ? control.value.trim() : control.value]));

    const clear = createButton('Clear filters');
    const apply = createButton('Apply filters', 'ca-primary');
    clear.addEventListener('click', () => {
        // The cleared Source's folders first, so the cleared Folder value is one of them.
        if (source && folder) { source.control.value = source.cleared; followSource(); }
        for (const { control, cleared } of controls) control.value = cleared;
        const reset = read();
        finish();
        onClear({ ...reset, category: '' });
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
        actions,
    ], { scope, trigger });
    const opened = session;
    function setOptions(key, options) {
        const item = controls.find(candidate => candidate.key === key);
        if (session !== opened || !item || key === 'tags') return;
        fillSelect(item.control, key, options);
        choose(item.control, wanted.get(key));
        if (source && folder && (item === source || item === folder)) followSource(wanted.get('folder'));
    }
    return { setOptions };
}

/** One operation row; a press on its busy (aria-disabled) action is ignored and keeps the dialog open. */
function operation(title, description, label, onAction) {
    const row = el('div', undefined, 'operation');
    const text = el('div');
    text.append(el('h3', title), el('p', description));
    const actions = el('div', undefined, 'row-actions');
    const action = createButton(label);
    action.addEventListener('click', () => {
        if (action.getAttribute('aria-disabled') === 'true') return;
        finish();
        onAction();
    });
    actions.append(action);
    row.append(text, actions);
    return { row, action };
}

const RESCAN_LABEL = 'Rescan';
const RESCAN_BUSY_LABEL = 'Rescanning…';

/**
 * "Import & inspect" with the real operations (D15). While `rescanBusy`, Rescan reads "Rescanning…" and is
 * aria-disabled, so a second press is visibly ignored. Returns `{setRescanBusy}` for the page to end that state.
 * With `onMetadataScan`, the concept's "Review scan" row follows as "Scan for metadata" (D55).
 */
export function openImportDialog({ scope, onInspect, onRescan, onMetadataScan = null, trigger = null, rescanBusy = false }) {
    requireScope(scope);
    requireCallbacks({ onInspect, onRescan, ...(onMetadataScan ? { onMetadataScan } : {}) });
    const list = el('div', undefined, 'operation-list');
    const inspect = operation('Inspect a local image', 'Read embedded metadata before deciding to add it.', 'Open inspector', onInspect);
    const rescan = operation('Scan an output folder', 'Find files added or removed in the output folders.', RESCAN_LABEL, onRescan);
    list.append(inspect.row, rescan.row);
    if (onMetadataScan) {
        // The opened review dialog replaces this one in the shell, so its Cancel returns focus to Import.
        const review = operation('Scan for metadata', 'Discover and enrich existing files using the configured sources.',
            'Review scan', () => onMetadataScan(trigger));
        list.append(review.row);
    }
    function setRescanBusy(busy) {
        rescan.action.textContent = busy ? RESCAN_BUSY_LABEL : RESCAN_LABEL;
        if (busy) rescan.action.setAttribute('aria-disabled', 'true');
        else rescan.action.removeAttribute('aria-disabled');
    }
    setRescanBusy(rescanBusy);
    show('Import & inspect', [
        el('p', 'Image inspection and output scanning have different effects. Choose the workflow you want.', 'small'),
        list,
    ], { scope, trigger });
    return { setRescanBusy };
}

/** "Copy prompt": the text in a readonly, pre-selected textarea for a manual copy. */
export function openCopyFallback(text, { scope, trigger = null } = {}) {
    requireScope(scope);
    if (typeof text !== 'string') throw new TypeError('Copy text must be a string');
    const area = el('textarea');
    area.readOnly = true;
    area.value = text;
    show('Copy prompt', [
        field('Select and copy this text', area, 'copy-text'),
        el('p', 'Automatic clipboard access requires a secure browser context.', 'small'),
    ], { scope, trigger, focusTarget: area });
    area.select();
}

/**
 * The concept's `simpleInput`: one labelled `input#action-input` (starting at `value`), then Cancel and the primary
 * `action`. The action reports the trimmed value and closes; an empty value keeps the dialog open on its field, or
 * reports `fallback` when one is given (Save view's "Saved view", a1339). A positive `maxLength` limits the field.
 */
export function openInputDialog({ scope, title, label, action, value = '', maxLength, fallback, onApply, trigger = null }) {
    requireScope(scope);
    requireCallbacks({ onApply });
    for (const [name, text] of Object.entries({ title, label, action })) {
        if (typeof text !== 'string' || !text) throw new TypeError(`The input dialog needs a ${name}`);
    }
    if (fallback !== undefined && (typeof fallback !== 'string' || !fallback.trim())) {
        throw new TypeError('The input dialog fallback must be text');
    }
    const input = el('input');
    input.value = typeof value === 'string' ? value : '';
    if (Number.isSafeInteger(maxLength) && maxLength > 0) input.maxLength = maxLength;
    const cancel = createButton('Cancel');
    const apply = createButton(action, 'ca-primary');
    cancel.addEventListener('click', () => finish());
    apply.addEventListener('click', () => {
        const entered = input.value.trim() || fallback;
        if (!entered) { input.focus(); return; }
        finish();
        onApply(entered);
    });
    const actions = el('div', undefined, 'dialog-actions');
    actions.append(cancel, apply);
    show(title, [field(label, input, 'action-input'), actions], { scope, trigger, focusTarget: input });
}

/**
 * The concept's plain `dialog(title, body)` with page-built `nodes`. `onClose` runs once when the dialog closes or
 * another dialog replaces its body, so the page can dispose what it mounted there.
 */
export function openContentDialog({ scope, title, nodes, onClose = null, trigger = null }) {
    requireScope(scope);
    if (typeof title !== 'string' || !title) throw new TypeError('The dialog needs a title');
    if (!Array.isArray(nodes) || nodes.some(node => !node || typeof node !== 'object')) throw new TypeError('The dialog needs its nodes');
    if (onClose !== null && typeof onClose !== 'function') throw new TypeError('The dialog onClose must be a function');
    show(title, nodes, { scope, trigger, onClose });
}

export const SCAN_REVIEW_SENTENCE = 'Enrich positive / negative associations, reuse canonical text and report unchanged files. ' +
    'Do not inflate run counts or recreate existing thumbnails.';
const BACKUP_LABEL = 'Back up now';
const BACKUP_BUSY_LABEL = 'Backing up…';

/**
 * The concept's "Review metadata scan" (`scan-review`, D55). `sources` is the number of output folders the scan reads;
 * the scope is fixed to "All files" in Activity-1. `onBackup` runs the database backup (the button is aria-disabled
 * and reads "Backing up…" until it settles, and the dialog stays open); Start scan closes the dialog, then `onStart`.
 */
export function openScanReviewDialog({ scope, sources, onBackup, onStart, trigger = null }) {
    requireScope(scope);
    requireCallbacks({ onBackup, onStart });
    if (!Number.isSafeInteger(sources) || sources < 0) throw new TypeError('The scan review needs its source count');
    const grid = el('dl', undefined, 'meta-grid');
    for (const [term, value] of [['Sources', `${sources.toLocaleString('en-US')} output ${sources === 1 ? 'folder' : 'folders'}`],
        ['Scope', 'All files']]) {
        const entry = el('div');
        entry.append(el('dt', term), el('dd', value));
        grid.append(entry);
    }
    const notice = el('div', undefined, 'notice');
    const copy = el('div');
    copy.append(el('h3', 'Back up the database first (recommended)'),
        el('p', 'Downloads a copy of the prompts database before the scan changes any records.'));
    const backupActions = el('div', undefined, 'row-actions');
    const backup = createButton(BACKUP_LABEL);
    backupActions.append(backup);
    notice.append(copy, backupActions);
    backup.addEventListener('click', async () => {
        if (backup.getAttribute('aria-disabled') === 'true') return;
        backup.setAttribute('aria-disabled', 'true');
        backup.textContent = BACKUP_BUSY_LABEL;
        try {
            await onBackup();
        } finally {
            backup.removeAttribute('aria-disabled');
            backup.textContent = BACKUP_LABEL;
        }
    });
    const cancel = createButton('Cancel');
    const start = createButton('Start scan', 'ca-primary');
    cancel.addEventListener('click', () => finish());
    start.addEventListener('click', () => {
        finish();
        onStart();
    });
    const actions = el('div', undefined, 'dialog-actions');
    actions.append(cancel, start);
    show('Review metadata scan', [grid, el('p', SCAN_REVIEW_SENTENCE, 'small'), notice, actions], { scope, trigger });
}
