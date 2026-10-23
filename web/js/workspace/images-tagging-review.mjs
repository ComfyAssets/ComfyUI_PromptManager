/**
 * D90 (ruling T3-bulk): "Review each image", the bulk Auto-tag session in the browser. The selected images are walked
 * one Tag image dialog at a time (image-tagging.mjs `actions: 'review'`), each opened with the session's model and
 * options and Generate already running. Approve saves that image as the dialog's Save does and advances; Skip
 * advances without a write; Stop reviewing, Escape and × end the session. Each image is one gated `suggest` call, so
 * the session holds no model between images; a refused one (`model-busy`) stays on its image with the dialog's own
 * status and Retry until the user retries or skips. Ending toasts "<n> of <M> images tagged."; the selection stays.
 */
import { closeWorkspaceDialog } from './workspace-dialogs.mjs';
import { openImageTagging } from './image-tagging.mjs';
import { showWorkspaceToast } from './toast.mjs';

/** The closing toast: approved saves of the session's images (the plan's copy verbatim). */
export function reviewOutcome(tagged, total) {
    return `${tagged} of ${total} images tagged.`;
}

/**
 * Walk `images` (listing items with a tagging target, in selection order) with `model_type` and `options`.
 * `onSaved(event)` follows every approved save. Returns `{stop()}`, which ends the session (its open dialog closes and
 * writes nothing more). `open`, `close` and `notify` are the dialog seams (the defaults in the page).
 */
export function startReviewSession({api, scope, images, model_type: model, options, trigger = null, onSaved = null,
    open = openImageTagging, close = closeWorkspaceDialog, notify = showWorkspaceToast}) {
    if (!Array.isArray(images) || !images.length) throw new TypeError('A review needs its images');
    const total = images.length;
    let index = 0, tagged = 0, ended = false, handle = null;

    function end() {
        if (ended) return;
        ended = true;
        handle = null;
        notify(reviewOutcome(tagged, total));
    }
    function advance() {
        if (ended) return;
        index += 1;
        if (index >= total) {
            // The last dialog handed over (advancing), so closing it is not a Stop.
            end();
            close();
            return;
        }
        show();
    }
    function show() {
        let current = true;
        const settled = () => {
            const was = current;
            current = false;
            return was;
        };
        handle = open({api, scope, image: images[index], source: 'bulk', trigger, preset: {model, options},
            autoGenerate: true, actions: 'review', position: {index: index + 1, total},
            onSaved: event => {
                // A save that settles after its dialog was left (Skip or Stop while it ran) still counts.
                tagged += 1;
                onSaved?.(event);
                if (settled()) advance();
            },
            onSkip: () => { if (settled()) advance(); },
            onStop: () => { if (settled()) end(); },
        });
    }

    show();
    return {
        stop() {
            if (ended) return;
            const open = handle;
            end();
            open?.close();
        },
    };
}
