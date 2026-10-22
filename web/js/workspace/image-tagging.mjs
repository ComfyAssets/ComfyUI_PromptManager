/**
 * The concept's "Tag image" dialog (docs/concepts/workspace/app.js `tagReview` a1037–1061, `renderTagModelOptions`
 * a1062–1076, `generateTags` a1077–1135, `apply-tags` a1517–1547; style.css s1598–1694) over the tagging controller
 * (image-tagging-state.mjs), in the shared modal shell (workspace-dialogs.mjs) with the concept's `tagging-modal`
 * class, whose sticky `.dialog-actions` stay reachable while the content scrolls.
 *
 * Recorded deviations: D83 (#249 plan D60) the suggestions are removable chips, every one preselected, at most the
 * requested count, with "Remove anything you do not want to add." and the all-removed copy; D84 (plan D61) the
 * legacy engines' labels and sizes, GGUF in the concept's Ollama slot, instructions prefilled with the server's
 * default; D91 (plan D68) a Suggestions count field for every model. States the concept lacks (busy, conflict,
 * changed storage or file, lost answers, unavailable model, video) are only text in `#tag-generation-status`; Retry
 * (ruling T5) leads the actions wherever the controller allows it, as the other workspace dialogs place theirs.
 *
 * The capture is released when the dialog closes (×, Escape, Cancel, Save), when the route changes (the shell
 * closes its dialog) and on `pagehide`.
 */
import { createButton, createScope } from '../shared/toolkit/core.js';
import { createImageTaggingController, MODEL_OPTIONS, optionError } from './image-tagging-state.mjs';
import { closeWorkspaceDialog, field, openContentDialog } from './workspace-dialogs.mjs';
import { createWorkspaceIcon } from './icons.mjs';
import { showWorkspaceToast } from './toast.mjs';
import { cardTitle } from './images-state.mjs';

const MANUAL_HELP = 'Comma-separated. Generated suggestions are added only after you approve them.';
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
 * It follows `controller` until `scope` is disposed (reused by the bulk Auto-tag dialog).
 */
export function renderModelPanel({scope, controller, heading = 'Auto-tag this image'}) {
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
    panel.append(el('h3', heading), field('Tagging model', select, 'image-tag-model'), options, generate);
    const inputs = new Map();
    let shownModel = null;

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
        if (state.model !== shownModel) {
            shownModel = state.model;
            select.value = state.model;
            renderOptions(state);
        }
        setDisabled(generate, !state.canGenerate);
    }
    select.addEventListener('change', () => controller.setModel(select.value));
    generate.addEventListener('click', () => {
        const {state} = controller;
        if (disabled(generate) || !state.canGenerate) return;
        // The concept's native validity first (a1104–1114), then what the controller would refuse (a blank field).
        for (const input of inputs.values()) input.setCustomValidity('');
        const invalid = [...inputs.values()].find(input => !input.checkValidity());
        if (invalid) { invalid.reportValidity(); return; }
        const error = optionError(state.model, state.options);
        if (error) {
            const input = inputs.get(error.field);
            input.setCustomValidity(error.message);
            input.reportValidity();
            return;
        }
        controller.generate();
    });
    scope.cleanup(controller.subscribe(update));
    update(controller.state);
    return panel;
}

function previewOf(image, state) {
    const video = state?.image ? state.image.mediaType === 'video' : image?.is_video === true;
    const node = el(video ? 'video' : 'img', undefined, 'tag-image-preview');
    if (video) { node.muted = true; node.preload = 'metadata'; node.playsInline = true; }
    return node;
}

/**
 * Open "Tag image" for one listed `image` (its tagging target). `source` is the page (`images`, `library`, `review`,
 * `tags`); `onSaved(event)` runs after a confirmed save, as every `onImageTagsSaved` listener does.
 */
export function openImageTagging({api, scope, image, source = 'images', trigger = null, onSaved = null}) {
    const target = imageTarget(image);
    if (!target) throw new TypeError('The image has no tagging target');
    const controller = createImageTaggingController({api, target, source});
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
    const panel = renderModelPanel({scope: local, controller});
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
    const cancel = createButton('Cancel');
    const primary = createButton('Save manual tags', 'ca-primary');
    actions.append(cancel, primary);

    const nodes = [heading];
    if (controller.state.showScopeNote) nodes.push(el('p', SCOPE_NOTE, 'tag-scope-note small'));
    nodes.push(manualField, panel, suggestions, actions);

    let shownImage = null, shownRows = null, shownText = null, filled = false;
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
            // D83: removable chips instead of the concept's checkbox rows.
            const chips = el('div', undefined, 'tags');
            for (const row of state.suggestions) {
                const chip = el('span', undefined, 'chip tag-suggestion');
                const remove = createButton('×');
                remove.setAttribute('aria-label', `Remove ${row.name}`);
                remove.addEventListener('click', () => controller.remove(row.name));
                chip.append(el('span', row.name), el('span', row.label, 'tag-confidence'), remove);
                chips.append(chip);
            }
            list.replaceChildren(chips);
        }
        if (focused >= 0) {
            const buttons = [...list.querySelectorAll('button')];
            (buttons[Math.min(focused, buttons.length - 1)] ?? panel.querySelector('.ca-primary')).focus();
        }
    }
    function complete(state) {
        completed = true;
        const event = {target, filename: state.image.filename, tags: state.savedTags.slice(), source};
        closeWorkspaceDialog();
        announceSaved(event);
        onSaved?.(event);
        showWorkspaceToast(state.toast);
    }
    function render(state) {
        if (!open || completed) return;
        if (state.image && state.image !== shownImage) renderImage(state.image);
        if (!filled && state.phase !== 'observing' && state.image) {
            filled = true;
            manual.value = state.manual;
        }
        if (state.suggestions !== shownRows || state.suggestionsText !== shownText) renderSuggestions(state);
        setText(count, state.countText);
        setText(status, state.statusText);
        setText(primary, state.primaryLabel);
        setDisabled(primary, !state.canSave);
        if (state.canRetry !== retry.isConnected) {
            if (state.canRetry) actions.prepend(retry);
            else {
                if (document.activeElement === retry) primary.focus();
                retry.remove();
            }
        }
        if (state.phase === 'saved') complete(state);
    }

    cancel.addEventListener('click', () => closeWorkspaceDialog());
    retry.addEventListener('click', () => controller.retry());
    primary.addEventListener('click', () => {
        if (disabled(primary) || !controller.state.canSave) return;
        controller.save();
    });
    const pagehide = () => controller.dispose();
    window.addEventListener('pagehide', pagehide);

    function release() {
        if (!open) return;
        open = false;
        window.removeEventListener('pagehide', pagehide);
        document.getElementById('modal')?.classList.remove('tagging-modal');
        local.dispose();
        controller.dispose();
    }
    openContentDialog({scope, title: 'Tag image', nodes, trigger, onClose: release});
    document.getElementById('modal')?.classList.add('tagging-modal');
    local.cleanup(controller.subscribe(render));
    render(controller.state);
    controller.open();
    return {
        close() { if (open) closeWorkspaceDialog(); },
        get controller() { return controller; },
    };
}
