/**
 * The Images selection bar's tagging actions (#249 PR D, Mock inventory M3):
 *
 * - **Add tags** (the concept's `bulk-tags` simpleInput, app.js a1378–1383): "Tag selected images", "Tags, separated
 *   by commas", Apply. The names are parsed as the Tag image dialog's manual field parses them, then the `image-tags`
 *   Activity job adds them to every selected image.
 * - **Auto-tag selected** (D85, ruling L1, beyond the concept): "Auto-tag selected images" with the Tag image dialog's
 *   model panel (its fields show the server's defaults, D91's Suggestions included) and no Generate, then **How to
 *   apply** (D90, ruling T3-bulk): **Apply automatically** starts the job, **Review each image** starts the browser
 *   review session (images-tagging-review.mjs). A model that is not downloaded keeps Start disabled and says why.
 *
 * The toast says the job started (the Activity store's "Tagging started." / "Auto-tagging started."), or why it did
 * not: a running record-changing job (named, with its link), a held model, a refused body. Activity shows progress.
 * The selection stays either way. When a started job ends having tagged files, `onTagged(keys)` re-reads them.
 */
import { createButton, createScope } from '../shared/toolkit/core.js';
import { createTaggingOptionsController, parseManualTags } from './image-tagging-state.mjs';
import { imageTarget, renderModelPanel } from './image-tagging.mjs';
import { startReviewSession } from './images-tagging-review.mjs';
import { closeWorkspaceDialog, openContentDialog, openInputDialog } from './workspace-dialogs.mjs';
import { busyToast } from './activity-scan.mjs';
import { imageKey } from './images-state.mjs';
import { countOf } from './plural.mjs';
import { showWorkspaceToast } from './toast.mjs';

export const IMAGE_TAG_JOB = 'image-tags';
export const MAX_BULK_IMAGES = 500;
export const TOO_MANY = 'Select at most 500 images.';
export const NO_TAGS = 'Enter at least one tag.'; // D117
export const ADD_DIALOG = Object.freeze({title: 'Tag selected images', label: 'Tags, separated by commas', action: 'Apply'});
export const AUTO_TITLE = 'Auto-tag selected images';
export const APPLY_CHOICES = Object.freeze([
    Object.freeze({value: 'auto', label: 'Apply automatically', action: 'Start auto-tagging'}),
    Object.freeze({value: 'review', label: 'Review each image', action: 'Start review'}),
]);
// D117: the concept has no copy for these; they follow D55's "The scan could not start. Try again.".
const START_FAILED = Object.freeze({add: 'Tagging could not start. Try again.', auto: 'Auto-tagging could not start. Try again.'});
const FINAL = new Set(['finished', 'cancelled', 'failed']);

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

/**
 * The job targets of the checked `images` in listing order (their reveal identity), or null when more than 500 are
 * checked. A file without a tagging identity is left out.
 */
export function bulkTargets(images) {
    if (images.length > MAX_BULK_IMAGES) return null;
    return images.map(imageTarget).filter(Boolean);
}

/** The Add tags field's names, deduplicated as the Tag image dialog's manual field is. */
export const parseBulkTags = parseManualTags;

// Duck-typed: this ES module cannot import the UMD api-client.js.
const apiMessage = error => (error?.name === 'ApiError' && error.code && error.message ? error.message : null);

/**
 * Start one `image-tags` job with `body` through `store.start`. Returns the job, or null after toasting why not: the
 * busy answer naming the running job, a coded refusal's own sentence (`model-busy`, a bad body), else a fixed one.
 */
export async function startTagJob({store, scope, body, notify = showWorkspaceToast}) {
    try {
        const result = await store.start(IMAGE_TAG_JOB, body);
        if (scope.signal.aborted) return null;
        if (result.busy !== undefined) {
            notify(busyToast(result.busy));
            return null;
        }
        return result.job;
    } catch (error) {
        if (!scope.signal.aborted) notify(apiMessage(error) ?? START_FAILED[body.mode]);
        return null;
    }
}

/** Call `onEnd(job)` once the job `id` has ended in `store`; returns the unsubscribe. */
export function followJobEnd({store, scope, id, onEnd}) {
    let done = false;
    const check = () => {
        const job = store.find(id);
        if (done || !job || !FINAL.has(job.state)) return;
        done = true;
        stop();
        onEnd(job);
    };
    const stop = store.subscribe(check);
    scope.cleanup(stop);
    check();
    return stop;
}

/**
 * Wire one started job: when it ends having tagged files, `onTagged(keys)` gets the image keys it was started for.
 */
function followTagged({store, scope, job, images, onTagged}) {
    const keys = new Set(images.map(imageKey));
    followJobEnd({store, scope, id: job.id, onEnd: ended => { if ((ended.counters.tagged ?? 0) > 0) onTagged(keys); }});
}

/** Add tags: the concept's simpleInput, then the job over `images` (the checked files, taken now). */
export function openAddTags({scope, store, images, trigger, onTagged = () => {}, notify = showWorkspaceToast}) {
    const targets = bulkTargets(images);
    if (!targets) { notify(TOO_MANY); return; }
    openInputDialog({scope, trigger, ...ADD_DIALOG, onApply: async text => {
        const tags = parseBulkTags(text);
        if (!tags.length) { notify(NO_TAGS); return; }
        const job = await startTagJob({store, scope, body: {mode: 'add', targets, tags}, notify});
        if (job) followTagged({store, scope, job, images, onTagged});
    }});
}

/** The D90 How to apply radio group: Apply automatically (checked) or Review each image. */
function applyChoice(onChange) {
    const group = el('div', undefined, 'field');
    group.setAttribute('role', 'radiogroup');
    const caption = el('span', 'How to apply');
    caption.id = 'auto-tag-apply-label';
    group.setAttribute('aria-labelledby', caption.id);
    group.append(caption);
    for (const choice of APPLY_CHOICES) {
        const label = el('label');
        const input = el('input');
        Object.assign(input, {type: 'radio', name: 'auto-tag-apply', value: choice.value, checked: choice.value === 'auto'});
        input.addEventListener('change', () => { if (input.checked) onChange(choice); });
        label.append(input, ` ${choice.label}`);
        group.append(label);
    }
    return group;
}

/**
 * Auto-tag selected (D85): the model panel, How to apply (D90), Cancel and Start. Start validates the fields as
 * Generate does, then starts the job or the review session over `images` (the checked files, taken now).
 */
export function openAutoTag({api, scope, store, images, trigger, onTagged = () => {}, notify = showWorkspaceToast}) {
    const targets = bulkTargets(images);
    if (!targets) { notify(TOO_MANY); return; }
    const listed = images.filter(imageTarget);
    const local = createScope();
    const options = createTaggingOptionsController({api});
    // D85: the Tag image dialog's model panel without Generate; its Suggestions field is the job's count (D91).
    const {element: panel, validate} = renderModelPanel({scope: local, controller: options,
        heading: `Auto-tag ${countOf(listed.length, 'image')}`, generate: false}); // D117
    let choice = APPLY_CHOICES[0];
    const actions = el('div', undefined, 'dialog-actions');
    const cancel = createButton('Cancel');
    const start = createButton(choice.action, 'ca-primary');
    actions.append(cancel, start);
    // A model that is not downloaded: the reason, as the Tag image dialog's status line words it.
    const status = el('div', undefined, 'small');
    status.id = 'tag-generation-status';
    status.setAttribute('role', 'status');
    const choices = applyChoice(next => { choice = next; start.textContent = next.action; }); // D90
    const render = state => {
        if (status.textContent !== state.unavailable) status.textContent = state.unavailable;
        if (state.canStart) start.removeAttribute('aria-disabled');
        else start.setAttribute('aria-disabled', 'true');
    };
    local.cleanup(options.subscribe(render));
    render(options.state);
    cancel.addEventListener('click', () => closeWorkspaceDialog());
    start.addEventListener('click', () => {
        if (start.getAttribute('aria-disabled') === 'true' || !options.state.canStart || !validate()) return;
        const request = options.request();
        if (!request) return;
        if (choice.value === 'review') {
            // The first image's dialog replaces this one in the shell (D90).
            startReviewSession({api, scope, images: listed, ...request, trigger});
            return;
        }
        closeWorkspaceDialog();
        startTagJob({store, scope, body: {mode: 'auto', targets, ...request}, notify}).then(job => {
            if (job) followTagged({store, scope, job, images: listed, onTagged});
        });
    });
    openContentDialog({scope, title: AUTO_TITLE, nodes: [panel, choices, status, actions], trigger, onClose: () => {
        document.getElementById('modal')?.classList.remove('tagging-modal');
        local.dispose();
        options.dispose();
    }});
    document.getElementById('modal')?.classList.add('tagging-modal');
    options.open();
}
