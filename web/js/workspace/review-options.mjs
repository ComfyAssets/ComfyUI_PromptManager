/**
 * The Sampler context option list of a review state (#659), shared by Needs review (review.mjs) and Inspect's Choose
 * prompt (inspect-choice.mjs). A module of its own, so Inspect does not import the Needs review page (which imports the
 * media viewer, which imports the inspector).
 */
function el(tag, text) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    return node;
}

// The options each select shows, so a render that changes nothing leaves the list (and its focus) alone.
const shownOptionsOf = new WeakMap();

/**
 * The Sampler context options of a review state in `select`, for Needs review and Inspect's Choose prompt (#659):
 * one option per candidate (an unusable one disabled, with the server's reason), the chosen one selected, and the select
 * disabled while the detail is not ready or a decision runs. `handlers.onChoose(value)` hears a change; `handlers.scope`
 * owns that listener, which is added on the first render of a select.
 */
export function renderReviewOptions(select, state, { onChoose, scope }) {
    if (!shownOptionsOf.has(select)) {
        shownOptionsOf.set(select, null);
        scope.listen(select, 'change', () => onChoose(select.value));
    }
    if (state.options !== shownOptionsOf.get(select)) {
        shownOptionsOf.set(select, state.options);
        select.replaceChildren(...state.options.map(option => {
            const node = el('option', option.label);
            node.value = option.value;
            node.disabled = option.disabled;
            return node;
        }));
    }
    if (state.choice !== null && select.value !== state.choice) select.value = state.choice;
    select.disabled = state.detailStatus !== 'ready' || state.phase === 'applying';
}

/**
 * #657 D3: the line under the options when the server kept only the most used prompts of a file with very many
 * (`detail.collapsed`: `{shown, total, paths}`), else ''.
 */
export function collapsedLine(detail) {
    const collapsed = detail?.collapsed;
    if (!collapsed) return '';
    return `Showing the ${collapsed.shown.toLocaleString('en-US')} most used of ${collapsed.total.toLocaleString('en-US')} prompts in this file.`;
}

/** Show `collapsedLine(state.detail)` in `node` (hidden without one). */
export function renderCollapsed(node, state) {
    const text = collapsedLine(state.detail);
    if (node.textContent !== text) node.textContent = text;
    node.hidden = !text;
}
