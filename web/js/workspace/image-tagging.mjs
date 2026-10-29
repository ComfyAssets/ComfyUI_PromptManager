/**
 * The concept's "Tag image" dialog (docs/concepts/workspace/app.js `tagReview` a1037–1061, `renderTagModelOptions`
 * a1062–1076, `generateTags` a1077–1135, `apply-tags` a1517–1547; style.css s1598–1694) over the tagging controller
 * (image-tagging-state.mjs), in the shared modal shell (workspace-dialogs.mjs) with the concept's `tagging-modal`
 * class, whose sticky `.dialog-actions` stay reachable while the content scrolls.
 *
 * Recorded deviations: D83 (#249 plan D60) the suggestions are removable chips, every one preselected, at most the
 * requested count, with "Remove anything you do not want to add." while chips exist and the all-removed copy; D84
 * (plan D61) the legacy engines' labels and sizes, GGUF in the concept's Ollama slot, instructions prefilled with the
 * server's default; D91 (plan D68) a Suggestions count field for every model. States the concept lacks (busy,
 * conflict, changed storage or file, lost answers, unavailable model, video) are only text in
 * `#tag-generation-status`; D116 (ruling T5) Retry leads the actions wherever the controller allows it, as the other
 * workspace dialogs place theirs.
 *
 * The capture is released when the dialog closes (×, Escape, Cancel, Save), when the route changes (the shell
 * closes its dialog) and on `pagehide` (which closes the dialog; the release is sent with `keepalive`). A save in
 * flight at close keeps its capture until it settles, and its outcome is still toasted and announced.
 */
import { createButton, createScope } from '../shared/toolkit/core.js';
import { createImageTaggingController, MODEL_OPTIONS, optionError } from './image-tagging-state.mjs';
import { closeWorkspaceDialog, field, openContentDialog } from './workspace-dialogs.mjs';
import { createWorkspaceIcon } from './icons.mjs';
import { showWorkspaceToast } from './toast.mjs';
import { cardTitle } from './images-state.mjs';

const MANUAL_HELP = 'Comma-separated. Generated suggestions are added only after you approve them.';
// #430: while the page looks for a renumbered source's file, and the preview that a refused capture leaves.
const FINDING_SOURCE = 'The source of this image changed. Finding it in the refreshed image collection…';
const PREVIEW_UNAVAILABLE = 'Preview unavailable';
const SCOPE_NOTE = 'Tags belong to this image. Resolve its prompt association separately in Needs review.';
// The option inputs per controller key; the classifier keys only exist for WD14 models.
const OPTION_IDS = Object.freeze({
    general_threshold: 'image-general-threshold',
    character_threshold: 'image-character-threshold',
    instructions: 'image-tag-instructions',
    suggestion_limit: 'image-suggestion-limit',
});

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}
function setText(node, text) {
    if (node.textContent !== text) node.textContent = text;
}
function setDisabled(button, disabled) {
    if (disabled) button.setAttribute('aria-disabled', 'true');
    else button.removeAttribute('aria-disabled');
}
const disabled = button => button.getAttribute('aria-disabled') === 'true';

const savedListeners = new Set();
/** Run `listener({target, filename, tags})` after every confirmed tag save; returns the unsubscribe. */
export function onImageTagsSaved(listener) {
    if (typeof listener !== 'function') throw new TypeError('A listener function is required');
    savedListeners.add(listener);
    return () => savedListeners.delete(listener);
}
/** How many pages follow tag saves (a mounted page's subscription ends with its scope). */
export function imageTagsListenerCount() {
    return savedListeners.size;
}
function announceSaved(event) {
    for (const listener of [...savedListeners]) {
        try {
            listener(event);
        } catch (error) {
            // One page's refresh failure never stops the others.
            console.error('Image tag listener failed', error);
        }
    }
}

const tagButtons = new WeakSet();
/** The concept card's Tag beside Info (app.js mediaCard a398), for Images and every Library-embedded gallery. */
export function createTagButton(image) {
    const button = createButton('', 'icon-btn');
    button.append(createWorkspaceIcon('tagImage'));
    button.setAttribute('aria-label', `Tag or auto-tag ${image.filename}`);
    tagButtons.add(button);
    return button;
}
/** Whether `node` is a card's Tag button. */
export function isTagButton(node) {
    return tagButtons.has(node);
}

/** A listed file's tagging identity (`root_index`, `root_scope`, `path`), else null. */
export function imageTarget(image) {
    const {root_index: index, root_scope: scope, path} = image ?? {};
    if (!Number.isSafeInteger(index) || index < 0 || typeof scope !== 'string' || !/^[0-9a-f]{64}$/.test(scope) ||
        typeof path !== 'string' || !path) return null;
    return {root_index: index, root_scope: scope, path};
}

function numberInput(value, {min, max, step}) {
    const input = el('input');
    Object.assign(input, {type: 'number', min, max, step});
    input.value = String(value);
    return input;
}

/**
 * The concept's `.tag-model-panel` (a1049): `heading`, the Tagging model select, the model's options and Generate.
 * It follows `controller` until `scope` is disposed (reused by the bulk Auto-tag dialog, D85, without Generate:
 * `generate: false`). Returns the `element`, `validate()` (the fields' native validity, then what the controller would
 * refuse; true when valid, else the first invalid field reports) and `requestGenerate()`, Generate's own path (field
 * validity first), for a Retry after a failed Generate.
 */
export function renderModelPanel({scope, controller, heading = 'Auto-tag this image', generate: withGenerate = true}) {
    const panel = el('div', undefined, 'tag-model-panel');
    const select = el('select');
    for (const option of MODEL_OPTIONS) {
        const node = el('option', option.label); // D84
        node.value = option.id;
        select.append(node);
    }
    const options = el('div');
    options.id = 'image-tag-model-options';
    const generate = createButton('', 'ca-primary');
    generate.append(createWorkspaceIcon('tagImage'), ' Generate suggestions');
    panel.append(el('h3', heading), field('Tagging model', select, 'image-tag-model'), options);
    if (withGenerate) panel.append(generate);
    const inputs = new Map();
    let shownModel = null, shownOptions = null;

    function renderOptions(state) {
        inputs.clear();
        const classifier = MODEL_OPTIONS.find(option => option.id === state.model).classifier;
        const rows = [];
        if (classifier) {
            const pair = el('div', undefined, 'two-col');
            for (const [key, label] of [['general_threshold', 'General confidence'], ['character_threshold', 'Character confidence']]) {
                const input = numberInput(state.options[key], {min: '0', max: '1', step: '0.05'});
                inputs.set(key, input);
                pair.append(field(label, input, OPTION_IDS[key]));
            }
            rows.push(pair);
        } else {
            const area = el('textarea');
            area.value = state.options.instructions;
            inputs.set('instructions', area);
            rows.push(field('Tagging instructions', area, OPTION_IDS.instructions));
        }
        // D91: the number of suggestions, for every model.
        const count = numberInput(state.options.suggestion_limit, {min: '1', max: '100', step: '1'});
        inputs.set('suggestion_limit', count);
        rows.push(field('Suggestions', count, OPTION_IDS.suggestion_limit));
        options.replaceChildren(...rows);
        for (const [key, input] of inputs) {
            input.addEventListener('input', () => {
                input.setCustomValidity('');
                controller.setOption(key, input.value);
            });
        }
    }
    function update(state) {
        // A model change or the observation's server defaults rewrite the fields; typing never does (I1).
        if (state.model !== shownModel || state.optionsRevision !== shownOptions) {
            shownModel = state.model;
            shownOptions = state.optionsRevision;
            select.value = state.model;
            renderOptions(state);
        }
        setDisabled(generate, !state.canGenerate);
    }
    select.addEventListener('change', () => controller.setModel(select.value));
    function validate() {
        const {state} = controller;
        // The concept's native validity first (a1104–1114), then what the controller would refuse (a blank field).
        for (const input of inputs.values()) input.setCustomValidity('');
        const invalid = [...inputs.values()].find(input => !input.checkValidity());
        if (invalid) { invalid.reportValidity(); return false; }
        const error = optionError(state.model, state.options);
        if (error) {
            const input = inputs.get(error.field);
            input.setCustomValidity(error.message);
            input.reportValidity();
            return false;
        }
        return true;
    }
    function requestGenerate() {
        if (controller.state.canGenerate && validate()) controller.generate();
    }
    generate.addEventListener('click', () => { if (!disabled(generate)) requestGenerate(); });
    scope.cleanup(controller.subscribe(update));
    update(controller.state);
    return {element: panel, requestGenerate, validate};
}

function previewOf(image, state) {
    const video = state?.image ? state.image.mediaType === 'video' : image?.is_video === true;
    const node = el(video ? 'video' : 'img', undefined, 'tag-image-preview');
    if (video) { node.muted = true; node.preload = 'metadata'; node.playsInline = true; }
    return node;
}
/** D147: the preview box a failed or refused capture leaves, saying so (the reason is beside the actions). */
function unavailablePreview() {
    const node = el('div', PREVIEW_UNAVAILABLE, 'tag-image-preview tag-preview-empty');
    node.setAttribute('role', 'img');
    node.setAttribute('aria-label', PREVIEW_UNAVAILABLE);
    return node;
}

/**
 * Open "Tag image" for one listed `image` (its tagging target). `source` is the page (`images`, `library`, `review`,
 * `tags`, `bulk`); `onSaved(event)` runs after a confirmed save, as every `onImageTagsSaved` listener does.
 *
 * D90 (the bulk review session, `images-tagging-review.mjs`): `actions: 'review'` with `position: {index, total}`
 * titles the dialog "Tag image · Image <index> of <total>" and makes its actions Stop reviewing, Skip and Approve and
 * next (Approve and finish on the last image). `preset` ({model, options}) applies the session's model and options,
 * and `autoGenerate` runs Generate as soon as the file is observed. Approve saves as Save does, without the per-image
 * toast (the session toasts its count), and calls `onSaved`; Skip writes nothing and calls `onSkip`; Stop reviewing,
 * Escape and × write nothing and call `onStop` once. Swapping the next image's dialog in is not a stop.
 */
export function openImageTagging({api, scope, image, source = 'images', trigger = null, onSaved = null, preset = null,
    autoGenerate = false, actions: mode = 'tag', position = null, onSkip = null, onStop = null, onSourceChanged = null,
    onRefresh = null}) {
    const target = imageTarget(image);
    if (!target) throw new TypeError('The image has no tagging target');
    const review = mode === 'review';
    if (review && !(Number.isSafeInteger(position?.index) && Number.isSafeInteger(position?.total) &&
        position.index >= 1 && position.index <= position.total)) throw new TypeError('A review needs its position');
    // Set once this dialog hands over to the session (Approve, Skip): its close is then not a Stop.
    let advancing = false;
    const saved = ({filename: name, tags, toast}) => {
        // A retargeted dialog (#430) reports the file under its current identity.
        const event = {target: {...controller.target}, filename: name, tags, source};
        announceSaved(event);
        if (toast) showWorkspaceToast(toast);
        onSaved?.(event);
    };
    // A save that settles after the dialog closed is still reported: its toast, and the pages' refresh (I5).
    const onLateSave = outcome => (outcome.saved ? saved(outcome) : showWorkspaceToast(outcome.message));
    const controller = createImageTaggingController({api, target, source, onLateSave, preset});
    const local = createScope();
    let open = true;
    let completed = false;
    const {resolveUrl} = window.ApiClient;

    // 2. The heading: preview, title, filename and size; the listing's values until the capture answers.
    const heading = el('div', undefined, 'tag-image-heading');
    let preview = previewOf(image, null);
    const copy = el('div');
    const title = el('h3', cardTitle(image));
    const filename = el('p', image.filename, 'small');
    const size = el('span', undefined, 'chip');
    copy.append(title, filename, size);
    heading.append(preview, copy);
    const hasSize = (width, height) => [width, height].every(value => Number.isFinite(value) && value > 0);
    if (hasSize(image.width, image.height)) size.textContent = `${image.width} × ${image.height}`;
    else size.remove();

    // 4. The manual field.
    const manual = el('input');
    manual.setAttribute('aria-describedby', 'manual-tags-help');
    const help = el('p', MANUAL_HELP, 'small');
    help.id = 'manual-tags-help';
    const manualField = field('Image tags · edit manually', manual, 'manual-image-tags');
    manualField.append(help);
    manual.addEventListener('input', () => controller.setManual(manual.value));

    // 5. The model panel; 6. the suggestions.
    const {element: panel, requestGenerate} = renderModelPanel({scope: local, controller});
    const suggestions = el('section', undefined, 'tag-suggestion-panel');
    suggestions.setAttribute('aria-labelledby', 'tag-suggestions-title');
    const panelHeading = el('div', undefined, 'panel-heading');
    const suggestionsTitle = el('h3', 'Review suggestions');
    suggestionsTitle.id = 'tag-suggestions-title';
    const count = el('span', 'Not generated', 'small');
    count.id = 'suggestion-count';
    panelHeading.append(suggestionsTitle, count);
    const list = el('div');
    list.id = 'tag-suggestions';
    const status = el('div', undefined, 'small');
    status.id = 'tag-generation-status';
    status.setAttribute('role', 'status');
    suggestions.append(panelHeading, list, status);

    // 7. The actions; Retry leads them only while the controller allows it.
    const actions = el('div', undefined, 'dialog-actions');
    const retry = createButton('Retry');
    const cancel = createButton(review ? 'Stop reviewing' : 'Cancel'); // D90
    const skip = createButton('Skip');
    const primary = createButton('Save manual tags', 'ca-primary');
    const last = review && position.index === position.total;
    // D90: Approve with suggestions on screen; after a model change cleared them (M2) Save keeps only the manual field.
    const reviewLabel = state => (state.generated ? (last ? 'Approve and finish' : 'Approve and next')
        : (last ? 'Save and finish' : 'Save and next'));
    if (review) actions.append(cancel, skip, primary);
    else actions.append(cancel, primary);
    // D147: the reason a state blocks Save (and Generate), on its own row above the buttons; for a changed source the
    // page is asked to find the file again (`onSourceChanged`), else Refresh is offered.
    const notice = el('div', undefined, 'tag-action-notice');
    const noticeText = el('span');
    noticeText.setAttribute('role', 'status');
    const refresh = createButton(onRefresh ? 'Refresh image collection' : 'Reload page');
    notice.append(noticeText);
    const tried = new Set();
    let finding = false, current = image;

    const nodes = [heading];
    if (controller.state.showScopeNote) nodes.push(el('p', SCOPE_NOTE, 'tag-scope-note small'));
    nodes.push(manualField, panel, suggestions, actions);

    let shownImage = null, shownRows = null, shownText = null, shownDraft = 0;
    function renderImage(info) {
        shownImage = info;
        const next = previewOf(null, {image: info});
        if (next.tagName !== preview.tagName) { preview.replaceWith(next); preview = next; }
        title.textContent = info.title || info.filename;
        filename.textContent = info.filename;
        preview.alt = title.textContent;
        if (info.width && info.height) { preview.width = info.width; preview.height = info.height; }
        preview.src = resolveUrl(info.previewUrl);
        if (info.dimensions) { size.textContent = info.dimensions; if (!size.isConnected) copy.append(size); }
        else size.remove();
    }
    function renderSuggestions(state) {
        // A removed chip's focus moves to its neighbour's ×, else to Generate.
        const focused = list.contains(document.activeElement) ? [...list.querySelectorAll('button')].indexOf(document.activeElement) : -1;
        shownRows = state.suggestions;
        shownText = state.suggestionsText;
        if (!state.suggestions.length) {
            list.replaceChildren(el('p', state.suggestionsText, 'small'));
        } else {
            // D83: removable chips instead of the concept's checkbox rows, under ruling T4's instruction.
            const chips = el('div', undefined, 'tags');
            for (const row of state.suggestions) {
                const chip = el('span', undefined, 'chip tag-suggestion');
                const remove = createButton('×');
                remove.setAttribute('aria-label', `Remove ${row.name}`);
                remove.addEventListener('click', () => controller.remove(row.name));
                chip.append(el('span', row.name), el('span', row.label, 'tag-confidence'), remove);
                chips.append(chip);
            }
            list.replaceChildren(el('p', state.chipHint, 'small'), chips);
        }
        if (focused >= 0) {
            const buttons = [...list.querySelectorAll('button')];
            (buttons[Math.min(focused, buttons.length - 1)] ?? panel.querySelector('.ca-primary')).focus();
        }
    }
    function complete(state) {
        completed = true;
        if (review) {
            // D90: the session swaps the next image in (or ends), so this dialog is not closed here.
            advancing = true;
            saved({filename: state.image.filename, tags: state.savedTags.slice(), toast: null});
            return;
        }
        closeWorkspaceDialog();
        saved({filename: state.image.filename, tags: state.savedTags.slice(), toast: state.toast});
    }
    /** #430: ask the page once per target where a renumbered source's file is now; a found file is retargeted. */
    function findSource() {
        const {root_index: index, root_scope: held} = controller.target;
        const attempt = `${index}:${held}`;
        if (!onSourceChanged || tried.has(attempt)) return;
        tried.add(attempt);
        finding = true;
        Promise.resolve().then(() => onSourceChanged({...current, ...controller.target})).catch(() => null).then(fresh => {
            finding = false;
            if (!open || completed) return;
            const next = imageTarget(fresh);
            if (next && controller.state.phase === 'source-changed') {
                current = fresh;
                controller.retarget(next);
            } else render(controller.state);
        });
    }
    function renderNotice(state) {
        const changed = state.phase === 'source-changed';
        const text = !state.blocked ? '' : changed && finding ? FINDING_SOURCE : state.statusText;
        setText(noticeText, text);
        const offer = changed && !finding;
        if (offer !== refresh.isConnected) {
            if (offer) notice.append(refresh);
            else refresh.remove();
        }
        // Its own first row; Retry still leads the buttons after it (D116).
        if (text && actions.firstElementChild !== notice) actions.prepend(notice);
        else if (!text && notice.isConnected) notice.remove();
        // The empty preview of a refused or failed capture says why it is empty.
        if (state.blocked && !state.image && preview.tagName !== 'DIV') {
            const next = unavailablePreview();
            preview.replaceWith(next);
            preview = next;
        }
    }
    function render(state) {
        if (!open || completed) return;
        if (state.phase === 'source-changed' && !finding) findSource();
        if (state.image && state.image !== shownImage) renderImage(state.image);
        // The observation's saved tags, a kept draft, or a conflict's current set ahead of the draft (I2).
        if (state.draftRevision !== shownDraft) {
            shownDraft = state.draftRevision;
            if (manual.value !== state.manual) manual.value = state.manual;
        }
        if (state.suggestions !== shownRows || state.suggestionsText !== shownText) renderSuggestions(state);
        setText(count, state.countText);
        // A blocking reason is shown once, beside the actions (D147).
        setText(status, state.blocked ? '' : state.statusText);
        setText(primary, review ? reviewLabel(state) : state.primaryLabel);
        setDisabled(primary, !state.canSave);
        // D90: while Approve's save is in flight the session cannot be left, so its count includes that save.
        if (review) for (const button of [skip, cancel]) setDisabled(button, state.phase === 'saving');
        if (state.canRetry !== retry.isConnected) {
            if (state.canRetry) actions.prepend(retry); // D116
            else {
                if (document.activeElement === retry) primary.focus();
                retry.remove();
            }
        }
        renderNotice(state);
        if (state.phase === 'saved') complete(state);
    }

    cancel.addEventListener('click', () => { if (!disabled(cancel)) closeWorkspaceDialog(); });
    refresh.addEventListener('click', () => {
        closeWorkspaceDialog();
        if (onRefresh) onRefresh();
        else window.location.reload();
    });
    skip.addEventListener('click', () => {
        if (!open || advancing || disabled(skip)) return;
        advancing = true;
        onSkip?.();
    });
    // Retry after a failed Generate takes Generate's own path, so an invalid field reports its validity (M4).
    retry.addEventListener('click', () => (controller.state.phase === 'generate-error' ? requestGenerate() : controller.retry()));
    primary.addEventListener('click', () => {
        if (disabled(primary) || !controller.state.canSave) return;
        controller.save();
    });
    // Leaving the page closes the dialog, so a page restored from the back/forward cache shows no dead dialog (I4).
    const pagehide = () => { if (open) closeWorkspaceDialog(); };
    window.addEventListener('pagehide', pagehide);

    function release() {
        if (!open) return;
        open = false;
        window.removeEventListener('pagehide', pagehide);
        document.getElementById('modal')?.classList.remove('tagging-modal');
        local.dispose();
        controller.dispose();
        if (review && !advancing) onStop?.();
    }
    const dialogTitle = review ? `Tag image · Image ${position.index} of ${position.total}` : 'Tag image'; // D90
    openContentDialog({scope, title: dialogTitle, nodes, trigger, onClose: release,
        canClose: review ? () => controller.state.phase !== 'saving' : null});
    document.getElementById('modal')?.classList.add('tagging-modal');
    local.cleanup(controller.subscribe(render));
    render(controller.state);
    controller.open().then(() => {
        if (open && autoGenerate && controller.state.canGenerate) controller.generate();
    });
    return {
        close() { if (open) closeWorkspaceDialog(); },
        get controller() { return controller; },
    };
}
