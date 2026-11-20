/**
 * Inspect's **Choose prompt** (#659, design Part 5): when the inspection names the file's pending Needs review item
 * (`record.review.state === 'pending'`), the inspector shows this section after its prompt blocks. It is built from the
 * Needs review pieces over the review controller's single-item mode (review-state.mjs, `itemId`):
 *
 * - the item's detail (`GET /prompt_manager/review/items/{id}`) gives one "Sampler <id> · <positive> / <negative>"
 *   option per candidate in the shared **Sampler context** select (review-options.mjs), a candidate the server cannot
 *   apply disabled with its reason;
 * - the chosen option's positive and negative are the shared prompt blocks, with their Copy and Send;
 * - **Use this prompt** runs the Needs review apply flow (a fresh capture, then the decision with the candidate's
 *   hash); **Leave unresolved** decides the item as unresolved. A refusal shows the server's sentence in the section's
 *   `.warning-note`; an unanswered decision is resent once by the next press. A recorded decision shows Needs review's
 *   toast and calls `onDecided()`, which re-reads the inspection (the file then shows its link);
 * - with no candidate the picker can decide (a limited item, an unavailable file, nothing traceable) the section says
 *   so and offers **Open in Needs review** (`#/review`) instead of the options; a failed read offers Retry as well.
 *
 * The section is the inspector's own frame (`section.inspect-choice`, in the workspace overlay, no concept selector).
 */
import { createScope, createButton } from '../shared/toolkit/core.js';
import { createReviewController } from './review-state.mjs';
import { renderReviewOptions } from './review-options.mjs';
import { mountPromptBlocks } from './prompt-blocks.mjs';
import { field } from './workspace-dialogs.mjs';
import { navigateTo } from './state.mjs';
import { showWorkspaceToast } from './toast.mjs';

export const REVIEW_ROUTE = '#/review';
export const CHOICE_COPY = Object.freeze({
    title: 'Choose prompt',
    intro: 'More than one prompt could have made this image. Choose the one that did to link it, or leave it unresolved.',
    loading: 'Loading the possible prompts…',
    nothing: 'None of the possible prompts can be applied here. Decide this image in Needs review.',
    use: 'Use this prompt',
    leave: 'Leave unresolved',
    openReview: 'Open in Needs review',
    retry: 'Retry',
});

let choiceId = 0;

/**
 * What the section shows for a review state: `loading`, `error` (the read failed), `review` (nothing to decide here),
 * `choose`, or `decided`.
 */
export function choiceMode(state) {
    if (state.decided) return 'decided';
    if (state.detailStatus === 'error') return 'error';
    if (state.detailStatus !== 'ready' || !state.detail) return 'loading';
    const { detail } = state;
    if (detail.limited || detail.unavailable || detail.target === null || !state.options.some(option => !option.disabled)) return 'review';
    return 'choose';
}

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}
function setDisabled(button, disabled) {
    if (disabled) button.setAttribute('aria-disabled', 'true');
    else button.removeAttribute('aria-disabled');
}
const isDisabled = button => button.getAttribute('aria-disabled') === 'true';
function childScope(parent) {
    const scope = createScope(), dispose = () => scope.dispose();
    parent.signal.addEventListener('abort', dispose, { once: true });
    scope.cleanup(() => parent.signal.removeEventListener('abort', dispose));
    if (parent.signal.aborted) scope.dispose();
    return scope;
}

/**
 * Mount the section for item `itemId` under `parentScope` (the inspector's scope: the section outlives one render, so
 * a re-render keeps its choice and a decision in flight). Returns `{root, itemId, dispose}`; the inspector appends
 * `root` where it belongs on each render. `controller` is injectable for tests.
 */
export function mountInspectChoice({ api, itemId, sender, parentScope, onDecided = () => {}, controller = null }) {
    const root = el('section', undefined, 'inspect-choice');
    const headingId = `inspect-choice-title-${++choiceId}`;
    root.setAttribute('aria-labelledby', headingId);
    if (parentScope.signal.aborted) return { root, itemId, dispose() {} };
    const scope = childScope(parentScope);
    controller ??= createReviewController({ api, itemId });
    const heading = el('div', undefined, 'prompt-heading');
    const title = el('span', CHOICE_COPY.title, 'role-label');
    title.id = headingId;
    heading.append(title);
    const intro = el('p', CHOICE_COPY.intro, 'small');
    const status = el('p', '', 'small inspect-choice-status');
    status.setAttribute('role', 'status');
    const select = el('select');
    const context = field('Sampler context', select, `inspect-choice-context-${choiceId}`);
    const warning = el('p', '', 'warning-note'); // the states the concept lacks, as in Needs review
    warning.setAttribute('role', 'status');
    const blocks = el('div');
    const actions = el('div', undefined, 'dialog-actions');
    const retry = createButton(CHOICE_COPY.retry);
    const openReview = createButton(CHOICE_COPY.openReview);
    const leave = createButton(CHOICE_COPY.leave);
    const use = createButton(CHOICE_COPY.use, 'ca-primary');
    actions.append(retry, openReview, leave, use);
    root.append(heading, intro, status, context, warning, blocks, actions);

    const prompts = mountPromptBlocks(blocks, { prompts: null, sender, parentScope: scope, includePairAction: false, contextLabel: 'Chosen' });
    let shownPrompts, shownDecisions = 0, shownDecided = false, disposed = false;

    function render(state) {
        if (disposed || scope.signal.aborted) return;
        if (state.decisions !== shownDecisions) {
            shownDecisions = state.decisions;
            showWorkspaceToast(state.toast);
        }
        // Decided here or elsewhere (another tab, Needs review): the inspection is read again once.
        if (state.decided && !shownDecided) {
            shownDecided = true;
            onDecided();
        }
        const mode = choiceMode(state);
        // A decided item offers nothing; the re-read inspection removes the section.
        root.hidden = mode === 'decided';
        const choosing = mode === 'choose';
        status.textContent = mode === 'loading' ? CHOICE_COPY.loading : mode === 'review' ? CHOICE_COPY.nothing : '';
        status.hidden = !status.textContent;
        context.hidden = blocks.hidden = leave.hidden = use.hidden = !choosing;
        retry.hidden = mode !== 'error';
        openReview.hidden = mode !== 'review' && mode !== 'error';
        renderReviewOptions(select, state, { onChoose: value => controller.choose(value), scope });
        warning.textContent = state.warning;
        warning.hidden = !state.warning;
        if (state.prompts !== shownPrompts) {
            shownPrompts = state.prompts;
            prompts.setState({ prompts: state.prompts });
        }
        const idle = state.detailStatus === 'ready' && state.phase === 'idle';
        setDisabled(use, !idle || !state.choice);
        setDisabled(leave, !idle);
    }

    scope.listen(use, 'click', () => { if (!isDisabled(use)) controller.apply(); });
    scope.listen(leave, 'click', () => { if (!isDisabled(leave)) controller.leaveUnresolved(); });
    scope.listen(retry, 'click', () => controller.load());
    scope.listen(openReview, 'click', () => {
        // As the inspector's Prompt history does: the viewer closes, then the route opens.
        document.querySelector('dialog#viewer-dialog[open]')?.close();
        navigateTo(REVIEW_ROUTE);
    });
    const unsubscribe = controller.subscribe(render);
    render(controller.state);
    controller.load();

    function dispose() {
        if (disposed) return;
        disposed = true;
        unsubscribe();
        controller.dispose();
        root.remove();
        scope.dispose();
    }
    scope.cleanup(dispose);
    return { root, itemId, dispose };
}
