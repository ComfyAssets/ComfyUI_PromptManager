/**
 * Needs review (#249 PR F): the concept's Metadata review page (docs/concepts/workspace/app.js `renderReview`
 * a749–760; style.css s407–415, s796–814 and their breakpoints) over the review controller (review-state.mjs).
 *
 * - Heading "Metadata review", its line, and **Open activity** (`#/activity`).
 * - `.notice`: "N images need a decision" and the concept's sentence; with an empty queue, D86 (plan D63, ruling R6)
 *   "No images need a decision" and the scan/new-generation sentence, and nothing else.
 * - `.gallery-layout`: the queue's concept media cards (image-card.mjs; the image opens the shared viewer, Info selects
 *   the item, Tag opens the shared Tag image dialog with the review scope note), then `section.surface` "Resolve the
 *   association": filename, Tag image, Sampler context, the chosen path's prompt blocks and the actions Leave for later
 *   and Apply association. Both return to Images (ruling R7).
 * - States the concept lacks (loading, a failed read, a refused or unanswered decision, a limited or unavailable file)
 *   are text: `p.warning-note` in the panel, or the page's status line (D119).
 * - The open page re-reads its queue on each `change` of the review change stream (`/prompt_manager/review/events`,
 *   F2): items a scan or the monitor added, and decisions made in other tabs. Each `ready` (the first one, and one
 *   after the browser reconnects a dropped stream) names the queue's version, and the page re-reads only when the
 *   queue it shows is older (F3: one read per visit). A re-read keeps the depth Load more had reached. Nothing polls.
 * - The SAVED VIEWS row's count follows the total the page shows (`onTotal`), so a change costs no count read.
 * - D119: the queue cards carry no selection checkbox and no star (this page has no selection bar, and the queue
 *   carries no favorite state); more than 40 items add the Images page's `.infinite-continuation` Load more after the
 *   layout; the toasts "Association accepted." and "Left unresolved." stand in for the concept's prototype toast.
 */
import { createButton } from '../shared/toolkit/core.js';
import { createReviewController, COPY, noticeTitle } from './review-state.mjs';
import { createImageCard } from './image-card.mjs';
import { isTagButton, openImageTagging } from './image-tagging.mjs';
import { mountPromptBlocks } from './prompt-blocks.mjs';
import { createPromptSender } from './prompt-sender.mjs';
import { createMediaViewer } from './media-viewer.mjs';
import { createInspectionController } from './inspection-state.mjs';
import { createWorkspaceIcon } from './icons.mjs';
import { field } from './workspace-dialogs.mjs';
import { pageHeading } from './heading.mjs';
import { navigateTo } from './state.mjs';
import { showWorkspaceToast } from './toast.mjs';
import { imageKey } from './images-state.mjs';

export const REVIEW_EVENTS_URL = '/prompt_manager/review/events';
const openEvents = url => new EventSource(window.ApiClient.resolveUrl(url));

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
const isDisabled = button => button.getAttribute('aria-disabled') === 'true';

/** A queue item as the card, the viewer and the Tag dialog read a listed image. */
export function imageOf(item) {
    const target = item.target ?? {};
    return { filename: item.filename, root_index: target.root_index, root_scope: target.root_scope, path: target.path,
        url: item.url, is_video: item.is_video === true, media_type: item.is_video ? 'video' : 'image' };
}

/** The queue version a `ready` event names, or null (the page then re-reads). */
export function readyVersion(event) {
    try {
        const version = JSON.parse(event?.data ?? 'null');
        return version && typeof version === 'object' ? version : null;
    } catch {
        return null;
    }
}

/**
 * Mount the page under the shell's heading. `onDecision()` runs after a recorded decision and `onTotal(total)` each
 * time the pending total the page shows changes (the shell's Needs review count); `controller` and `connect` (the
 * change stream's EventSource) are injectable for tests.
 */
export function mountReview(main, { api, scope, onDecision = () => {}, onTotal = () => {},
    connect = openEvents, controller = createReviewController({ api }) }) {
    scope.cleanup(() => controller.dispose());
    const heading = pageHeading(main);
    heading.h1.textContent = COPY.title;
    heading.description.textContent = COPY.description;
    const openActivity = createButton('Open activity');
    heading.actions.append(openActivity);
    scope.listen(openActivity, 'click', () => navigateTo('#/activity'));

    const status = el('p', COPY.loading, 'empty-state');
    status.setAttribute('role', 'status');
    const retryLoad = createButton('Retry');
    const notice = el('div', undefined, 'notice');
    const noticeCopy = el('div');
    const noticeTitleNode = el('h2');
    const noticeText = el('p');
    noticeCopy.append(noticeTitleNode, noticeText);
    notice.append(noticeCopy);

    const layout = el('div', undefined, 'gallery-layout');
    const grid = el('div', undefined, 'image-grid review-queue');
    grid.setAttribute('role', 'list');
    grid.setAttribute('aria-label', 'Review queue');
    const panel = el('section', undefined, 'surface review-panel');
    const panelTitle = el('h2', COPY.panelTitle);
    panelTitle.id = 'review-panel-title';
    panel.setAttribute('aria-labelledby', panelTitle.id);
    const filename = el('p', '', 'small');
    const tagRow = el('div', undefined, 'row-actions spaced-15');
    const tagButton = createButton('');
    tagButton.append(createWorkspaceIcon('tagImage'), ' Tag image');
    tagRow.append(tagButton);
    const select = el('select');
    const contextField = field('Sampler context', select, 'review-context');
    const warning = el('p', '', 'warning-note'); // D119: the states the concept lacks
    warning.setAttribute('role', 'status');
    const blocks = el('div');
    const actions = el('div', undefined, 'dialog-actions');
    const later = createButton('Leave for later');
    const apply = createButton('Apply association', 'ca-primary');
    actions.append(later, apply);
    panel.append(panelTitle, filename, tagRow, contextField, warning, blocks, actions);
    layout.append(grid, panel);

    const continuation = el('div', undefined, 'infinite-continuation'); // D119: Load more past 40 items
    const moreText = el('p', '', 'small');
    const more = createButton('Load more images');
    continuation.append(moreText, more);

    const sender = createPromptSender({ scope });
    const prompts = mountPromptBlocks(blocks, { prompts: null, sender, parentScope: scope, includePairAction: false,
        onRetry: () => { if (controller.state.selectedId) controller.select(controller.state.selectedId); } });
    const inspection = createInspectionController({ api });
    scope.cleanup(() => inspection.dispose());
    const viewer = createMediaViewer({ api, container: main, parentScope: scope, inspectionController: inspection,
        sender, onSelect: image => inspection.select(image) });

    const cards = new Map();
    let shownItems = null, shownSelected, shownOptions = null, shownPrompts, shownDetail;

    function renderCards(state) {
        if (state.items !== shownItems) {
            shownItems = state.items;
            const ids = new Set(state.items.map(item => item.id));
            for (const [id, card] of cards) if (!ids.has(id)) { card.remove(); cards.delete(id); }
            state.items.forEach((item, index) => {
                let card = cards.get(item.id);
                if (!card) {
                    // D119: no selection checkbox and no star on a queue card.
                    card = createImageCard(imageOf(item), { media: Boolean(item.url) });
                    card.dataset.reviewId = item.id;
                    card.item = item;
                    if (!item.url) setDisabled(card.querySelector('.image-open'), true);
                    if (!item.target) setDisabled(card.querySelector('.image-card-actions button:last-child'), true);
                    cards.set(item.id, card);
                }
                if (grid.children[index] !== card) grid.insertBefore(card, grid.children[index] ?? null);
            });
            shownSelected = undefined;
        }
        if (state.selectedId !== shownSelected) {
            shownSelected = state.selectedId;
            for (const [id, card] of cards) card.classList.toggle('selected', id === state.selectedId);
        }
    }

    function renderPanel(state) {
        const item = state.items.find(entry => entry.id === state.selectedId);
        setText(filename, item?.filename ?? '');
        setDisabled(tagButton, !item?.target || state.phase === 'applying');
        if (state.options !== shownOptions) {
            shownOptions = state.options;
            select.replaceChildren(...state.options.map(option => {
                const node = el('option', option.label);
                node.value = option.value;
                node.disabled = option.disabled;
                return node;
            }));
        }
        if (state.choice !== null && select.value !== state.choice) select.value = state.choice;
        select.disabled = state.detailStatus !== 'ready' || state.phase === 'applying';
        setText(warning, state.warning);
        warning.hidden = !state.warning;
        if (state.prompts !== shownPrompts || state.detailStatus !== shownDetail) {
            shownPrompts = state.prompts;
            shownDetail = state.detailStatus;
            if (state.detailStatus === 'ready') prompts.setState({ prompts: state.prompts });
            else prompts.setState({ loading: state.detailStatus === 'loading', error: state.detailStatus === 'error' });
        }
        setDisabled(apply, state.detailStatus !== 'ready' || state.phase !== 'idle');
        setDisabled(later, state.phase === 'applying');
    }

    function render(state) {
        if (scope.signal.aborted) return;
        if (state.phase === 'done') return finish(state);
        const ready = state.status === 'ready';
        if (!ready) {
            setText(status, state.status === 'error' ? COPY.loadFailed : COPY.loading);
            status.setAttribute('role', state.status === 'error' ? 'alert' : 'status');
            if (!status.isConnected) main.append(status);
            if (state.status === 'error' && !retryLoad.isConnected) main.append(retryLoad);
            if (state.status !== 'error') retryLoad.remove();
            if (state.status === 'error' || !state.items.length) { notice.remove(); layout.remove(); continuation.remove(); }
            return;
        }
        status.remove();
        retryLoad.remove();
        setText(noticeTitleNode, noticeTitle(state.total));
        setText(noticeText, state.total ? COPY.notice : COPY.empty); // D86
        if (!notice.isConnected) main.append(notice);
        if (!state.items.length) { layout.remove(); continuation.remove(); return; }
        if (!layout.isConnected) notice.after(layout);
        renderCards(state);
        renderPanel(state);
        if (state.nextCursor) {
            setText(moreText, `${state.items.length.toLocaleString('en-US')} of ${state.total.toLocaleString('en-US')} images`);
            setDisabled(more, state.more === 'loading');
            if (state.more === 'error') setText(moreText, COPY.moreFailed);
            if (!continuation.isConnected) layout.after(continuation);
        } else continuation.remove();
    }

    let finished = false;
    function finish(state) {
        if (finished) return;
        finished = true;
        if (state.toast) {
            showWorkspaceToast(state.toast);
            onDecision();
        }
        // Ruling R7: Apply association and Leave for later both return to Images.
        navigateTo('#/images');
    }

    scope.listen(retryLoad, 'click', () => controller.load());
    scope.listen(more, 'click', () => { if (!isDisabled(more)) controller.loadMore(); });
    scope.listen(select, 'change', () => controller.choose(select.value));
    scope.listen(apply, 'click', () => { if (!isDisabled(apply)) controller.apply(); });
    scope.listen(later, 'click', () => { if (!isDisabled(later)) controller.leave(); });
    scope.listen(tagButton, 'click', () => {
        const item = controller.state.items.find(entry => entry.id === controller.state.selectedId);
        if (isDisabled(tagButton) || !item?.target) return;
        openImageTagging({ api, scope, image: imageOf(item), source: 'review', trigger: tagButton });
    });
    scope.listen(grid, 'click', event => {
        const card = event.target.closest('article[data-review-id]');
        if (!card) return;
        const { item } = card;
        const button = event.target.closest('button');
        if (!button || isDisabled(button)) return;
        if (isTagButton(button)) {
            openImageTagging({ api, scope, image: imageOf(item), source: 'review', trigger: button });
            return;
        }
        if (button.matches('.image-open')) {
            const items = controller.state.items.filter(entry => entry.url).map(imageOf);
            viewer.open({ items, selectedKey: imageKey(imageOf(item)), trigger: button });
            return;
        }
        // Info selects the item, as the concept's `inspect` does on this page (app.js a1244–1248).
        controller.select(item.id);
    });

    let shownTotal = null;
    const reportTotal = state => {
        if (state.status !== 'ready' || state.phase === 'done' || state.total === shownTotal) return;
        shownTotal = state.total;
        onTotal(state.total);
    };
    scope.cleanup(controller.subscribe(render));
    scope.cleanup(controller.subscribe(reportTotal));
    render(controller.state);
    controller.load();
    const events = connect(REVIEW_EVENTS_URL);
    scope.cleanup(() => events.close());
    events.addEventListener('ready', event => { if (!scope.signal.aborted) controller.sync(readyVersion(event)); });
    events.addEventListener('change', () => { if (!scope.signal.aborted) controller.refresh(); });
    return { controller };
}
