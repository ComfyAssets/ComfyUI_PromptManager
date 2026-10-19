/**
 * Tags & categories dialogs: Create tag, Rename or merge tag (with Delete tag…)
 * and Manage categories.
 *
 * One `dialog.vocabulary-dialog` in the recipe editor's modal shell (concept
 * simpleInput, app.js:1138-1143): header h2 + ×, `.field`, `.dialog-actions`
 * with Cancel then the primary action. Recorded deviations: "Apply in prototype"
 * is Apply, Manage categories adds a Category select and a Preview step (D9),
 * and Rename or merge carries Delete tag… (D14). Every write goes through
 * vocabulary-state.mjs; this module renders its phases as the editor does, with
 * inline `.warning-note` states, and never resends a write on its own.
 */
import { createButton } from '../shared/toolkit/core.js';
import { showWorkspaceToast } from './library-editor.mjs';
import { categoryPreviewText, deleteTagText, readTagImpact } from './vocabulary-state.mjs';

const RESTORED = 'Prompt storage was restored since this dialog opened. Nothing was changed.';
const UNKNOWN = 'The result could not be confirmed. The lists were re-read; check them before applying again.';
const TAG_CONFLICT = 'This tag changed since this dialog opened. Nothing was changed. The list was re-read; review it before trying again.';
const CATEGORY_CONFLICT = 'This category changed since this dialog opened. Nothing was changed. Preview again before applying.';
const NOT_LOADED = 'The vocabulary has not loaded yet. Nothing was changed.';
const CHECKING = 'Checking linked prompts…';
const IMPACT_FAILED = 'Linked prompts could not be read. Nothing was deleted.';
const SAVING = 'Applying… The dialog stays open until the server answers.';
const DELETING = 'Deleting… The dialog stays open until the server answers.';
const PREVIEWING = 'Previewing… Nothing is changed by a preview.';
const CATEGORIES_LOADING = 'Loading categories…';
const CATEGORIES_FAILED = 'Categories could not be loaded. Nothing was changed.';
// Manage categories reads at most this many categories (10 pages) into its select.
const CATEGORY_CAP = 1000;
const CATEGORIES_CAPPED = 'Only the first 1,000 categories are listed.';
// Command phases after which the delete confirmation's impact no longer describes storage.
const STALE_IMPACT = new Set(['conflict', 'storage-changed', 'unknown-outcome']);
// Phases that send nothing until the user acts: the primary stays disabled.
const BLOCKED = new Set(['saving', 'storage-changed']);

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
/** Re-parent only on a real change: moving a focused button would drop its focus. */
function setChildren(parent, nodes) {
    const current = [...parent.children];
    if (current.length !== nodes.length || current.some((node, index) => node !== nodes[index])) parent.replaceChildren(...nodes);
}

/** Toast copy for a confirmed receipt (decision D11). */
function toastOf(kind, receipt) {
    switch (kind) {
    case 'create-tag': return receipt.created ? 'Tag created.' : 'Tag already exists.';
    case 'rename-tag': return receipt.merged ? 'Tag merged without duplicate links.' : 'Tag renamed.';
    case 'delete-tag': return 'Tag deleted.';
    default: return receipt.consolidated ? 'Categories consolidated.' : 'Category renamed.';
    }
}

/** Inline state for a command phase; `conflict` is the kind-specific conflict copy, `failed` the refusal prefix. */
function statusOf(state, {conflict, saving, failed, kept = ' Your entries are kept.'}) {
    switch (state.phase) {
    case 'saving': return {text: state.kind === 'preview-category' ? PREVIEWING : saving};
    case 'write-error': return {text: `${state.kind === 'preview-category' ? 'Not previewed' : failed}: ${state.error?.message}${kept}`};
    case 'conflict': return {text: conflict};
    case 'storage-changed': return {text: RESTORED, reload: true};
    case 'unknown-outcome': return {text: UNKNOWN};
    default: return null;
    }
}

/**
 * `epoch()` reads the loaded lists' epoch and `categories` is the page's category
 * list (`{stream, subscribe}`), which Manage categories reads to the end (capped);
 * `reload()` re-reads both lists and
 * `settled()` resolves once the latest re-read landed; `tagAction(id)` and
 * `categoryAction(value)` find a loaded row's action for focus; `heading()` is the
 * page h1, the focus fallback.
 */
export function createVocabularyDialog({api, scope, createCommand, epoch, categories, reload, settled, heading, tagAction, categoryAction}) {
    const dialog = el('dialog', undefined, 'vocabulary-dialog');
    dialog.setAttribute('aria-labelledby', 'vocabulary-dialog-title');
    const header = el('div', undefined, 'dialog-header');
    const title = el('h2');
    title.id = 'vocabulary-dialog-title';
    const close = createButton('×');
    close.setAttribute('aria-label', 'Close dialog');
    const body = el('div');
    header.append(title, close);
    dialog.append(header, body);
    let session = null, disposed = false;

    const busy = () => session?.command.state.phase === 'saving';
    close.addEventListener('click', () => finish());
    dialog.addEventListener('cancel', event => {
        event.preventDefault();
        if (!busy()) finish();
    });
    // A browser may still force-close a modal; treat that as a dismissal.
    dialog.addEventListener('close', () => {if (!dialog.open) finish();});

    /** Close without writing. Focus goes to `target`, else the trigger, else the page heading. */
    function finish(target = null) {
        if (!session) return;
        const {command, trigger, views} = session;
        session = null;
        views.abort();
        command.dispose();
        if (dialog.open) dialog.close();
        dialog.remove();
        (target ?? (trigger?.isConnected ? trigger : heading()))?.focus();
    }

    /** A confirmed write: toast, close, then focus the resulting row once the re-read has landed. */
    async function complete(kind, receipt, focusTarget) {
        showWorkspaceToast(toastOf(kind, receipt));
        finish(heading());
        await settled();
        if (disposed || session) return;
        focusTarget()?.focus();
    }

    function show(text, view) {
        title.textContent = text;
        body.replaceChildren(...view);
        if (!dialog.isConnected) document.body.append(dialog);
        if (!dialog.open) dialog.showModal();
        close.focus();
    }

    /** Open a new view; an open dialog swaps its content, as the concept does. */
    function start(trigger, build) {
        if (disposed) return;
        const previous = session;
        if (previous) {
            previous.views.abort();
            previous.command.dispose();
        }
        const views = new AbortController();
        const next = {trigger: trigger ?? previous?.trigger ?? null, views, epoch: previous?.epoch ?? epoch(), render: () => {}};
        next.command = createCommand(state => {if (session === next) next.render(state);});
        session = next;
        build(next);
    }

    /** One status line, an optional Reload, and focus kept inside the dialog when a disabled control drops it. */
    function chrome(views) {
        const note = el('p', '', 'warning-note');
        note.id = 'vocabulary-dialog-status';
        note.setAttribute('role', 'status');
        note.hidden = true;
        const actions = el('div', undefined, 'dialog-actions');
        const reloadButton = createButton('Reload');
        reloadButton.addEventListener('click', () => {finish(heading()); reload();}, {signal: views.signal});
        return {note, actions, reloadButton, set(text) {
            if (note.textContent !== (text || '')) note.textContent = text || '';
            note.hidden = !text;
        }, refocus(...candidates) {
            if (!dialog.open || (document.activeElement && document.activeElement !== document.body)) return;
            (candidates.find(node => node?.isConnected && !node.disabled && !node.hidden) ?? close).focus();
        }};
    }

    /** The epoch this dialog opened with; null until the lists have loaded once. */
    function openedEpoch(current) {
        current.epoch ??= epoch();
        return current.epoch;
    }

    /** Create tag and Rename or merge tag: one required name field. */
    function nameView(current, {row}) {
        const {command, views} = current;
        const form = el('form');
        const input = el('input');
        input.required = true;
        if (row) input.value = row.name;
        const ui = chrome(views);
        const remove = row ? createButton('Delete tag…', 'danger') : null;
        const cancel = createButton('Cancel'), apply = createButton('Apply', 'ca-primary');
        apply.type = 'submit';
        form.append(field(row ? 'New name; existing names merge' : 'Tag name', input, row ? 'vocabulary-new-name' : 'vocabulary-tag-name'),
            ui.note, ui.actions);
        cancel.addEventListener('click', () => finish(), {signal: views.signal});
        remove?.addEventListener('click', () => start(null, next => deleteView(next, row)), {signal: views.signal});
        let message = null;
        form.addEventListener('submit', async event => {
            event.preventDefault();
            const expected = openedEpoch(current);
            if (!expected) {message = NOT_LOADED; render(command.state); return;}
            message = null;
            // After an unconfirmed result the user checked the lists; applying again is their explicit choice.
            if (command.state.phase === 'unknown-outcome') command.reset();
            await (row ? command.renameTag(row, input.value, expected) : command.createTag(input.value, expected));
            const {phase, receipt, kind} = command.state;
            if (phase === 'done' && receipt) complete(kind, receipt, () => tagAction(receipt.tag.id));
        }, {signal: views.signal});

        function render(state) {
            const status = statusOf(state, {conflict: TAG_CONFLICT, saving: SAVING, failed: 'Not changed'});
            ui.set(status?.text ?? message);
            const saving = state.phase === 'saving';
            apply.disabled = BLOCKED.has(state.phase) || state.phase === 'conflict';
            cancel.disabled = close.disabled = saving;
            if (remove) remove.disabled = saving;
            setChildren(ui.actions, [...(status?.reload ? [ui.reloadButton] : []), ...(remove ? [remove] : []), cancel, apply]);
            if (!saving && state.phase !== 'done') ui.refocus(status?.reload ? ui.reloadButton : null, apply, cancel);
        }
        current.render = render;
        show(row ? 'Rename or merge tag' : 'Create tag', [form]);
        render(command.state);
    }

    /** The Delete tag confirmation: the exact name and its real links, then Cancel / Delete. */
    function deleteView(current, row) {
        const {command, views} = current;
        const impactText = el('p', '', 'small');
        impactText.hidden = true;
        const ui = chrome(views);
        const retry = createButton('Retry');
        const cancel = createButton('Cancel'), confirm = createButton('Delete', 'danger');
        let read = 'loading';
        cancel.addEventListener('click', () => finish(), {signal: views.signal});
        retry.addEventListener('click', () => {
            // After an unconfirmed delete, Retry only re-reads the impact; it never resends the delete.
            if (command.state.phase === 'unknown-outcome') command.reset();
            readImpact();
        }, {signal: views.signal});
        confirm.addEventListener('click', async () => {
            await command.deleteTag(row, current.epoch);
            const {phase, receipt, kind} = command.state;
            if (phase === 'done' && receipt) complete(kind, receipt, () => null);
        }, {signal: views.signal});

        async function readImpact() {
            read = 'loading';
            render(command.state);
            try {
                const value = await readTagImpact(api, row, {signal: views.signal});
                if (views.signal.aborted) return;
                const expected = openedEpoch(current);
                if (expected && value.library_epoch !== expected) read = 'restored';
                else {
                    read = 'ready';
                    impactText.textContent = deleteTagText(row.name, value.impact);
                }
            } catch (error) {
                if (views.signal.aborted) return;
                // A missing tag is a changed tag: the lists are re-read for review.
                read = error?.status === 404 ? 'conflict' : 'failed';
                if (read === 'conflict') reload();
            }
            render(command.state);
        }

        function render(state) {
            const status = statusOf(state, {conflict: TAG_CONFLICT, saving: DELETING, failed: 'Not deleted', kept: ''})
                ?? {loading: {text: CHECKING}, failed: {text: IMPACT_FAILED, retry: true}, restored: {text: RESTORED, reload: true},
                    conflict: {text: TAG_CONFLICT}}[read] ?? null;
            ui.set(status?.text);
            const stale = STALE_IMPACT.has(state.phase);
            impactText.hidden = read !== 'ready' || stale;
            const saving = state.phase === 'saving';
            confirm.disabled = read !== 'ready' || saving || stale;
            cancel.disabled = close.disabled = saving;
            const recovery = status?.reload ? ui.reloadButton : status?.retry || state.phase === 'unknown-outcome' ? retry : null;
            setChildren(ui.actions, [...(recovery ? [recovery] : []), cancel, confirm]);
            if (!saving && state.phase !== 'done') ui.refocus(recovery, confirm, cancel);
        }
        current.render = render;
        show('Delete tag', [impactText, ui.note, ui.actions]);
        readImpact();
    }

    /** Manage categories: Category select, proposed name, Preview then Apply. */
    function manageView(current) {
        const {command, views} = current;
        const form = el('form');
        const {stream} = categories;
        // A category command carries the category list's own epoch.
        current.epoch = stream.state.epoch;
        const source = el('select');
        source.required = true;
        const target = el('input');
        target.required = true;
        // Its own id: the recipe editor already owns `datalist#categories`.
        target.setAttribute('list', 'vocabulary-category-options');
        const list = el('datalist');
        list.id = 'vocabulary-category-options';
        const retry = createButton('Retry');
        retry.addEventListener('click', () => stream.loadMore(), {signal: views.signal});
        let drain = 'loading', shown = null;
        function fill(values) {
            const key = values.join('\n');
            if (key === shown) return;
            shown = key;
            const selected = source.value;
            source.replaceChildren(...values.map(value => new Option(value, value)));
            if (values.includes(selected)) source.value = selected;
            list.replaceChildren(...values.map(value => Object.assign(document.createElement('option'), {value})));
        }
        /** Read the shared category list to its end (at most CATEGORY_CAP) before the select is offered. */
        function sync() {
            const state = stream.state;
            if (state.resetRequired) drain = 'restored';
            else if (state.error) drain = 'failed';
            // A reset emits its empty first state before it starts reading; the stream itself reads page one.
            else if (state.loading || state.epoch === null) drain = 'loading';
            else if (state.hasMore && state.items.length < CATEGORY_CAP) {
                drain = 'loading';
                stream.loadMore();
                return;
            } else {
                fill(state.items.slice(0, CATEGORY_CAP).map(item => item.value));
                drain = state.hasMore || state.items.length > CATEGORY_CAP ? 'capped' : 'ready';
            }
            render(command.state);
        }
        views.signal.addEventListener('abort', categories.subscribe(() => sync()), {once: true});
        const impactText = el('p', '', 'small');
        impactText.hidden = true;
        const ui = chrome(views);
        const cancel = createButton('Cancel'), primary = createButton('Preview', 'ca-primary');
        primary.type = 'submit';
        form.append(field('Category', source, 'vocabulary-category'), field('Proposed new category name', target, 'vocabulary-category-name'),
            list, impactText, ui.note, ui.actions);
        cancel.addEventListener('click', () => finish(), {signal: views.signal});
        for (const [control, type] of [[source, 'change'], [target, 'input']]) {
            control.addEventListener(type, () => command.edit(), {signal: views.signal});
        }
        let message = null;
        form.addEventListener('submit', async event => {
            event.preventDefault();
            const {phase, preview} = command.state;
            if (phase === 'previewed') {
                await command.applyCategory(preview);
                const {receipt, kind} = command.state;
                if (command.state.phase === 'done' && receipt) complete(kind, receipt, () => categoryAction(receipt.target));
                return;
            }
            const expected = current.epoch ??= stream.state.epoch;
            if (!expected) {message = NOT_LOADED; render(command.state); return;}
            message = null;
            if (phase === 'unknown-outcome') command.reset();
            await command.previewCategory(source.value, target.value, expected);
        }, {signal: views.signal});

        function render(state) {
            const previewed = state.phase === 'previewed';
            const status = statusOf(state, {conflict: CATEGORY_CONFLICT, saving: SAVING, failed: 'Not changed'})
                ?? (message ? {text: message} : null)
                ?? {loading: {text: CATEGORIES_LOADING}, failed: {text: CATEGORIES_FAILED, retry: true},
                    restored: {text: RESTORED, reload: true}, capped: {text: CATEGORIES_CAPPED}}[drain] ?? null;
            ui.set(status?.text);
            if (previewed) impactText.textContent = categoryPreviewText(state.preview);
            impactText.hidden = !previewed;
            primary.textContent = previewed ? 'Apply' : 'Preview';
            const saving = state.phase === 'saving';
            primary.disabled = BLOCKED.has(state.phase) || !['ready', 'capped'].includes(drain);
            cancel.disabled = close.disabled = saving;
            const recovery = status?.reload ? ui.reloadButton : status?.retry ? retry : null;
            setChildren(ui.actions, [...(recovery ? [recovery] : []), cancel, primary]);
            if (!saving && state.phase !== 'done') ui.refocus(recovery, primary, cancel);
        }
        current.render = render;
        show('Manage categories', [form]);
        sync();
    }

    function dispose() {
        if (disposed) return;
        finish();
        disposed = true;
    }
    scope.cleanup(dispose);
    return {
        createTag: trigger => start(trigger, current => nameView(current, {row: null})),
        renameTag: (row, trigger) => start(trigger, current => nameView(current, {row})),
        manageCategories: trigger => start(trigger, current => manageView(current)),
        dispose,
    };
}
