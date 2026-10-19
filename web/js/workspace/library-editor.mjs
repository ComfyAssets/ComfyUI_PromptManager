/**
 * Native recipe editor dialogs: New prompt, Edit recipe metadata, Create text
 * revision, Add a negative variant, and the pair archive confirmation.
 *
 * Markup, labels, order and native datalists follow the approved concept. All
 * reads, writes, receipts and recovery live in library-editor-state.mjs; this
 * module only renders `controller.state` and forwards user actions.
 */
import { createButton } from '../shared/toolkit/core.js';
import { createLibraryEditorController } from './library-editor-state.mjs';

const TITLES = {new: 'New prompt', metadata: 'Edit recipe metadata', revision: 'Create text revision', negative: 'Add a negative variant'};
const RATINGS = [['5', '5 / 5'], ['4', '4 / 5'], ['3', '3 / 5'], ['2', '2 / 5'], ['1', '1 / 5'], ['0', 'Unrated']];
const FIELD_IDS = {title: 'recipe-title', positive_text: 'recipe-positive', negative_text: 'recipe-negative', category: 'recipe-category',
    tags: 'recipe-tags', rating: 'recipe-rating', notes: 'recipe-notes'};
const SUBMITTABLE = new Set(['ready', 'write-error']);
const NEGATIVE_HELP = 'Leave empty for an explicitly empty negative. The positive stays unchanged.';
const NO_PAIR = 'Archive needs a selected saved negative variant.';
const ALREADY_ARCHIVED = 'This negative variant is already archived.';
const REVIEW = 'Loaded the latest saved values. Review your entries, then save.';
const REVIEW_ARCHIVE = 'Loaded the latest saved values. Review them, then archive.';
const TOAST_MS = 6500;
const REFRESH = ['Reload Library', 'refresh'];
// The concept has no discard confirmation (its Cancel and × simply close), so the state says it inline:
// every exit (Reload Library, Cancel, × or Escape) drops the draft.
const DISCARDS = 'Leaving this dialog discards your entries.';
let toastTimer;

/** Show one outcome in the shared workspace toast (role="status"); it clears itself like the concept's. */
export function showWorkspaceToast(message) {
    const toast = document.getElementById('workspace-toast');
    if (!toast) return;
    toast.textContent = message;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {toast.textContent = '';}, TOAST_MS);
}

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

/**
 * Inline state copy and its one recovery action; `archive` selects the pair-archive
 * wording. After a restore only New prompt may re-read: every other mode names an
 * owner by its old numeric ID, so it offers only the Library refresh exit, which
 * discards the draft.
 */
function statusOf(state, archive, mode = null) {
    const nothing = archive ? 'Nothing was archived.' : 'Nothing was saved.';
    const message = state.error?.message;
    switch (state.phase) {
    case 'initializing': return {text: 'Loading the latest saved values…'};
    case 'initialization-error': return {text: `The latest saved values could not be loaded. ${nothing}`, action: ['Retry', 'retryInitialization']};
    case 'saving': return {text: archive ? 'Archiving… The dialog stays open until the server answers.' : 'Saving… The dialog stays open until the server answers.'};
    case 'write-error': return {text: `${archive ? 'Not archived' : 'Not saved'}: ${message} Your entries are kept.`};
    case 'conflict': return {text: archive ? 'This negative variant changed since it was loaded. Reload it and review it before archiving.'
        : 'This prompt changed since it was loaded. Reload it and review your entries before saving.', action: ['Reload', 'reload']};
    case 'storage-changed': return mode === 'new' ? {text: `Prompt storage was restored since this dialog opened. ${nothing}`, action: ['Reload Library state', 'reload']}
        : {text: `Prompt storage was restored since this dialog opened. ${nothing}${archive ? '' : ` ${DISCARDS}`}`, action: REFRESH};
    case 'unknown-outcome': return {text: archive ? 'Archive could not be confirmed. Retry sends the same archive once.'
        : mode === 'metadata' ? 'Save could not be confirmed. Your entries are kept; Retry reloads the latest saved values for review and sends nothing.'
            : 'Save could not be confirmed. Your entries are kept; Retry sends the same save once.', action: ['Retry', 'retryUnconfirmed']};
    case 'saved-refresh-error':
        // A refresh that found restored storage cannot recover by re-reading; only reloading the Library view can.
        if (state.error?.code === 'storage-changed') {
            return {text: `${archive ? 'Archived' : 'Saved'}; Library refresh failed because prompt storage was restored. Reload the Library to see current data.`, action: REFRESH};
        }
        return {text: archive ? 'Archived; Library refresh failed.' : 'Saved; Library refresh failed.', action: ['Retry refresh', 'retryReconciliation']};
    case 'ready': return message ? {text: message} : state.reviewRequired ? {text: archive ? REVIEW_ARCHIVE : REVIEW} : null;
    default: return null;
    }
}

/** One persistent inline status in the concept's .warning-note style, so live updates are announced. */
function statusLine() {
    const note = el('p', '', 'warning-note');
    note.id = 'library-editor-status';
    note.setAttribute('role', 'status');
    note.hidden = true;
    return {note, set(text) {
        if (note.textContent !== (text || '')) note.textContent = text || '';
        note.hidden = !text;
    }};
}
/** Re-parent only on a real change: moving a focused button would drop its focus. */
function setChildren(parent, nodes) {
    const current = [...parent.children];
    if (current.length !== nodes.length || current.some((node, index) => node !== nodes[index])) parent.replaceChildren(...nodes);
}

/** Saving or re-reading disables or removes the focused action; focus would drop to the body. */
const SETTLING = new Set(['saving', 'initializing']);
const restoredStorage = state => state.phase === 'storage-changed' || (state.phase === 'saved-refresh-error' && state.error?.code === 'storage-changed');
/**
 * The action that held focus when a write or re-read began gets it back once that
 * settles; when it is gone or disabled (Save after an unconfirmed write), focus moves
 * to the first usable fallback, the recovery action first, and never to the body.
 */
function restoreFocus(button, state, fallbacks) {
    if (!button || SETTLING.has(state.phase)) return button;
    if (state.phase !== 'complete' && (!document.activeElement || document.activeElement === document.body)) {
        [button, ...fallbacks].find(node => node?.isConnected && !node.disabled)?.focus();
    }
    return null;
}
/** Remember a focused dialog action while the phase settles. */
function settlingFocus(actions, state, current) {
    return SETTLING.has(state.phase) && actions.contains(document.activeElement) ? document.activeElement : current;
}

function field(label, control, id) {
    const wrapper = el('div', undefined, 'field');
    const caption = el('label', label);
    caption.htmlFor = id;
    control.id = id;
    wrapper.append(caption, control);
    return wrapper;
}
function datalist(id, values) {
    const list = el('datalist');
    list.id = id;
    list.append(...values.map(value => Object.assign(document.createElement('option'), {value})));
    return list;
}

/**
 * `onReconciled(result, {outcome, draft})` receives the reconciled result with the
 * controller's outcome and submitted draft. `onRefreshRequired()` is the explicit
 * exit when prompt storage was restored; the dialog closes first.
 */
export function createLibraryEditor({api, parentScope, onCommitted = () => {}, onReconciled = () => {},
    onRefreshRequired = () => window.location.reload()}) {
    let session = null, disposed = false;
    const dialog = el('dialog', undefined, 'library-editor');
    dialog.setAttribute('aria-labelledby', 'library-editor-title');
    const header = el('div', undefined, 'dialog-header');
    const title = el('h2');
    title.id = 'library-editor-title';
    const close = createButton('×');
    close.setAttribute('aria-label', 'Close dialog');
    const body = el('div');
    header.append(title, close);
    dialog.append(header, body);

    const busy = () => session?.controller.state.phase === 'saving';
    close.addEventListener('click', () => finish());
    dialog.addEventListener('cancel', event => {
        event.preventDefault();
        if (!busy()) finish();
    });
    // A browser may still force-close a modal; treat that as a dismissal without claiming rollback.
    dialog.addEventListener('close', () => {if (!dialog.open) finish();});

    function finish() {
        if (!session) return;
        const {controller, trigger, views} = session;
        session = null;
        views.abort();
        controller.dispose();
        if (dialog.open) dialog.close();
        dialog.remove();
        if (trigger?.isConnected) trigger.focus();
    }

    function show(heading) {
        title.textContent = heading;
        close.disabled = false;
        if (!dialog.isConnected) document.body.append(dialog);
        if (!dialog.open) dialog.showModal();
        close.focus();
    }

    /** The one recovery action for a phase, created once per view and reused across renders. */
    function recoveryButton(cache, action, signal) {
        if (!action) return null;
        const [label, method] = action;
        if (!cache.has(label)) {
            const control = createButton(label);
            control.addEventListener('click', () => {
                const {controller} = session;
                if (method === 'refresh') {finish(); onRefreshRequired(); return;}
                if (method === 'reload') controller.reloadOwner({keepDraft: true});
                else controller[method]();
            }, {signal});
            cache.set(label, control);
        }
        return cache.get(label);
    }

    function controllerFor(mode, positiveId, pairId) {
        const controller = createLibraryEditorController({api, mode, positiveId, pairId, onCommitted,
            onReconciled: result => onReconciled(result, {outcome: controller.state.outcome, draft: controller.state.draft}),
            onChange: state => session?.render(state)});
        return controller;
    }

    function editorView({mode, positiveId, pairId, draft}) {
        const views = new AbortController();
        const controller = controllerFor(mode, positiveId, pairId);
        const negativeOnly = mode === 'negative';
        const readOnly = mode === 'metadata';
        const form = el('form');
        const controls = {};
        const textarea = (name, required = false) => {
            const node = el('textarea');
            node.required = required;
            node.readOnly = readOnly && (name === 'positive_text' || name === 'negative_text');
            return controls[name] = node;
        };
        const input = (name, required = false) => {
            const node = el('input');
            node.required = required;
            return controls[name] = node;
        };
        if (!negativeOnly) {
            form.append(field('Title', input('title', true), FIELD_IDS.title),
                field('Positive prompt', textarea('positive_text', true), FIELD_IDS.positive_text));
        }
        form.append(field(negativeOnly ? 'Negative prompt' : 'Negative prompt (optional)', textarea('negative_text'), FIELD_IDS.negative_text));
        const categories = datalist('categories', []), tags = datalist('tag-options', []);
        if (!negativeOnly) {
            const columns = el('div', undefined, 'two-col');
            const category = input('category'), tagInput = input('tags');
            category.setAttribute('list', 'categories');
            tagInput.setAttribute('list', 'tag-options');
            columns.append(field('Category', category, FIELD_IDS.category), field('Tags, separated by commas', tagInput, FIELD_IDS.tags));
            const rating = controls.rating = el('select');
            for (const [value, text] of RATINGS) rating.add(new Option(text, value));
            form.append(columns, categories, tags, field('Rating', rating, FIELD_IDS.rating), field('Notes', textarea('notes'), FIELD_IDS.notes));
        } else form.append(el('p', NEGATIVE_HELP, 'small'));
        const line = statusLine(), recovery = new Map();
        const actions = el('div', undefined, 'dialog-actions');
        const revise = createButton('Create text revision'), archive = createButton('Archive…', 'danger');
        const cancel = createButton('Cancel'), save = createButton('Save', 'ca-primary');
        save.type = 'submit';
        form.append(line.note, actions);

        for (const [name, node] of Object.entries(controls)) {
            node.addEventListener(node.tagName === 'SELECT' ? 'change' : 'input', () => {
                controller.setField(name, name === 'rating' && node.value === '0' ? null : node.value);
            }, {signal: views.signal});
        }
        form.addEventListener('submit', event => {
            event.preventDefault();
            controller.submit();
        }, {signal: views.signal});
        cancel.addEventListener('click', () => finish(), {signal: views.signal});
        revise.addEventListener('click', () => open({mode: 'revision', positiveId, pairId, trigger: session.trigger}), {signal: views.signal});
        archive.addEventListener('click', () => archiveView(), {signal: views.signal});
        if (!negativeOnly) loadVocabulary(tags, views.signal);

        let categoryKey = null, refocus = null;
        function render(state) {
            for (const [name, node] of Object.entries(controls)) {
                const value = name === 'rating' ? String(state.draft.rating ?? 0) : state.draft[name];
                if (node.value !== value) node.value = value;
            }
            const key = state.categories.join('\n');
            if (key !== categoryKey) {
                categoryKey = key;
                categories.replaceChildren(...datalist('', state.categories).children);
            }
            const saving = state.phase === 'saving';
            refocus = settlingFocus(actions, state, refocus);
            save.disabled = !SUBMITTABLE.has(state.phase);
            cancel.disabled = close.disabled = saving;
            // After a restore the numeric IDs may name another prompt; only the Library reload exit stays.
            revise.disabled = saving || restoredStorage(state);
            const pairProblem = readOnly && !state.pair && pairId === null ? NO_PAIR : state.pair?.archived ? ALREADY_ARCHIVED : null;
            archive.disabled = !SUBMITTABLE.has(state.phase) || !state.pair || state.pair.archived;
            const status = statusOf(state, false, mode);
            line.set(status?.text ?? pairProblem);
            if (!status && pairProblem) archive.setAttribute('aria-describedby', line.note.id);
            else archive.removeAttribute('aria-describedby');
            const retry = recoveryButton(recovery, status?.action, views.signal);
            setChildren(actions, [...(retry ? [retry] : []), ...(readOnly ? [revise, archive] : []), cancel, save]);
            refocus = restoreFocus(refocus, state, [retry, save]);
            if (state.phase === 'complete') finish();
        }

        body.replaceChildren(form);
        return {controller, views, render, start() {
            if (draft) for (const [name, value] of Object.entries(draft)) controller.setField(name, value);
            render(controller.state);
            controller.initialize();
        }};
    }

    async function loadVocabulary(list, signal) {
        try {
            const result = await api.get('/prompt_manager/tags', {signal, cache: 'no-store'});
            if (signal.aborted || !Array.isArray(result.tags)) return;
            list.replaceChildren(...datalist('', result.tags.filter(tag => typeof tag === 'string').map(tag => `${tag},`)).children);
        } catch {
            // Tag suggestions are auxiliary vocabulary; the field stays fully usable without them.
        }
    }

    /** Swap the open Edit dialog to the pair archive confirmation, reusing its controller. */
    function archiveView() {
        const {controller, trigger} = session;
        // An Edit save's failure or validation message is not about the pair; the confirmation starts clean.
        const inherited = controller.state.error;
        session.views.abort();
        const views = new AbortController();
        const block = el('section', undefined, 'prompt-block prompt-negative');
        block.setAttribute('aria-label', 'Negative prompt');
        const blockHeading = el('div', undefined, 'prompt-block-heading');
        blockHeading.append(el('h3', 'Negative prompt'));
        const negative = el('div');
        block.append(blockHeading, negative);
        const links = el('p', undefined, 'small');
        const restore = el('p', 'You can restore it from Show archived in Negative variants.', 'small');
        const line = statusLine(), recovery = new Map();
        let refocus = null;
        const actions = el('div', undefined, 'dialog-actions');
        const reread = createButton('Retry');
        reread.addEventListener('click', () => controller.refreshPair(), {signal: views.signal});
        const cancel = createButton('Cancel'), confirm = createButton('Archive', 'danger');
        cancel.addEventListener('click', () => finish(), {signal: views.signal});
        confirm.addEventListener('click', () => controller.setArchive(true), {signal: views.signal});
        let shownNegative = null;

        function render(state) {
            const {pair} = state;
            if (pair && pair.negative_text !== shownNegative) {
                shownNegative = pair.negative_text;
                negative.replaceChildren(shownNegative === '' ? el('p', 'Empty negative prompt', 'prompt-empty') : el('pre', shownNegative, 'prompt-text'));
            }
            // Counts come only from the controller's fresh exact-ID pair read.
            const counted = state.pairRead === 'fresh' && Boolean(pair), readFailed = state.pairRead === 'error';
            if (counted) {
                const images = pair.image_count, runs = pair.run_count;
                links.textContent = `${images} image${images === 1 ? '' : 's'} and ${runs} recorded run${runs === 1 ? '' : 's'} stay linked.`;
            }
            const status = state.error && state.error === inherited ? null : statusOf(state, true);
            line.set(status?.text ?? (readFailed ? 'Linked images and recorded runs could not be read. Nothing was archived.'
                : counted ? null : 'Checking linked images and recorded runs…'));
            const retry = recoveryButton(recovery, status?.action, views.signal) ?? (readFailed ? reread : null);
            links.hidden = !counted;
            const saving = state.phase === 'saving';
            refocus = settlingFocus(actions, state, refocus);
            confirm.disabled = !counted || !SUBMITTABLE.has(state.phase);
            cancel.disabled = close.disabled = saving;
            setChildren(actions, [...(retry ? [retry] : []), cancel, confirm]);
            refocus = restoreFocus(refocus, state, [retry, confirm]);
            if (state.phase === 'complete') finish();
        }

        body.replaceChildren(block, links, restore, line.note, actions);
        session = {controller, trigger, views, render};
        show('Archive negative variant');
        controller.refreshPair();
        render(controller.state);
    }

    function open({mode, positiveId = null, pairId = null, trigger = null, draft = null}) {
        if (disposed) return;
        const previous = session;
        if (previous) {
            // Swap the same open dialog's content, as the concept does; the old controller stops here.
            previous.views.abort();
            previous.controller.dispose();
        }
        const view = editorView({mode, positiveId, pairId, draft});
        session = {controller: view.controller, trigger: trigger ?? previous?.trigger ?? null, views: view.views, render: view.render};
        show(TITLES[mode]);
        view.start();
    }

    function dispose() {
        if (disposed) return;
        finish();
        disposed = true;
    }
    if (parentScope.signal.aborted) dispose();
    else parentScope.signal.addEventListener('abort', dispose, {once: true});
    return {open, dispose};
}
