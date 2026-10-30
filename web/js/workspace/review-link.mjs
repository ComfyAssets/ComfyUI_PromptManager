/**
 * Link to a prompt (#536, D181; the concept has no picker): search the Library, choose the prompt that made this image
 * and, when the prompt has recipes, its negative. The server's refusals stay in the dialog. No negative is taken from
 * the file: one recipe is preselected and visible, several must be chosen between, none means no negative.
 */
import { createButton } from '../shared/toolkit/core.js';
import { closeWorkspaceDialog, field, openContentDialog } from './workspace-dialogs.mjs';

export const SEARCH_DELAY_MS = 250;
export const RESULT_LIMIT = 20;
export const VARIANT_LIMIT = 40;
export const NO_NEGATIVE = 'none';
const LIBRARY_URL = '/prompt_manager/library';
const CHOOSE = '';

export const LINK_COPY = Object.freeze({
    title: 'Link to a prompt',
    sentence: name => `Choose the prompt that made ${name}. This records your choice; the file's metadata does not confirm it.`,
    search: 'Search the Library',
    searching: 'Searching…',
    empty: 'No prompt in the Library matches.',
    failed: 'The Library could not be searched. Try again.',
    negative: 'Negative prompt',
    chooseNegative: 'Choose a negative…',
    noNegative: 'No negative',
    negativeFailed: "This prompt's negatives could not be read. Choose it again.",
    action: 'Link image',
    cancel: 'Cancel',
});

const titleOf = prompt => prompt.title?.trim() || prompt.text;

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

/** The Negative options of a prompt's recipes: its value is the recipe's pair id, or `NO_NEGATIVE`. */
export function negativeOptions(variants) {
    const recipes = variants.filter(variant => !variant.explicit_empty)
        .map(variant => ({ value: String(variant.id), label: variant.title?.trim() || variant.negative_text }));
    const bare = variants.find(variant => variant.explicit_empty);
    const none = { value: bare ? String(bare.id) : NO_NEGATIVE, label: LINK_COPY.noNegative };
    if (recipes.length > 1) return [{ value: CHOOSE, label: LINK_COPY.chooseNegative }, ...recipes, none];
    return [...recipes, none];
}

/** The body fields of the chosen Negative value. */
export const pairIdOf = value => (value === NO_NEGATIVE ? null : Number(value));

/** Open the dialog; `controller.link` records the choice and the dialog closes once it is recorded. */
export function openPromptLink({ api, scope, controller, filename, trigger }) {
    // A re-read may open another item meanwhile (decided elsewhere): the dialog names this one, so it closes.
    const itemId = controller.state.selectedId;
    const stopWatching = controller.subscribe(state => { if (state.selectedId !== itemId && !closed) closeWorkspaceDialog(); });
    let timer = null, search = null, variantsRead = null, chosen = null, closed = false;

    const sentence = el('p', LINK_COPY.sentence(filename), 'small');
    const input = el('input');
    input.type = 'search';
    const results = el('div', undefined, 'review-link-results');
    results.setAttribute('role', 'group');
    results.setAttribute('aria-label', 'Matching prompts');
    const status = el('p', '', 'small');
    status.setAttribute('role', 'status');
    const select = el('select');
    const negative = field(LINK_COPY.negative, select, 'review-link-negative');
    negative.hidden = true;
    const warning = el('p', '', 'warning-note');
    warning.setAttribute('role', 'status');
    warning.hidden = true;
    const actions = el('div', undefined, 'dialog-actions review-link-actions');
    const cancel = createButton(LINK_COPY.cancel);
    const submit = createButton(LINK_COPY.action, 'ca-primary');
    actions.append(cancel, submit);

    const busy = () => controller.state.phase !== 'idle';
    const ready = () => chosen !== null && !negative.hidden && select.value !== CHOOSE;
    function syncSubmit() {
        if (ready() && !busy()) submit.removeAttribute('aria-disabled');
        else submit.setAttribute('aria-disabled', 'true');
    }
    function warn(text) {
        warning.textContent = text;
        warning.hidden = !text;
    }

    /** Every active recipe of the prompt, page by page. */
    async function readVariants(id, signal) {
        const variants = [];
        let cursor = null;
        do {
            const query = new URLSearchParams({ variant_limit: String(VARIANT_LIMIT) });
            if (cursor) query.set('variant_cursor', cursor);
            const detail = await api.get(`${LIBRARY_URL}/${encodeURIComponent(id)}?${query}`, { signal, cache: 'no-store' });
            variants.push(...(detail.variants ?? []));
            cursor = detail.variants_next_cursor ?? null;
        } while (cursor);
        return variants;
    }

    async function choose(prompt, button) {
        variantsRead?.abort();
        const read = variantsRead = new AbortController();
        chosen = prompt;
        for (const node of results.children) node.setAttribute('aria-pressed', String(node === button));
        negative.hidden = true;
        warn('');
        syncSubmit();
        try {
            const variants = await readVariants(prompt.id, read.signal);
            if (read !== variantsRead || closed) return;
            const options = negativeOptions(variants);
            select.replaceChildren(...options.map(option => {
                const node = el('option', option.label);
                node.value = option.value;
                return node;
            }));
            select.value = options[0].value;
            negative.hidden = false;
        } catch (error) {
            if (error?.name === 'AbortError' || read !== variantsRead || closed) return;
            warn(LINK_COPY.negativeFailed);
        }
        syncSubmit();
    }

    function show(items) {
        status.textContent = items.length ? '' : LINK_COPY.empty;
        results.replaceChildren(...items.map(prompt => {
            const button = createButton(titleOf(prompt));
            button.title = prompt.text;
            button.setAttribute('aria-pressed', String(prompt.id === chosen?.id));
            button.addEventListener('click', () => choose(prompt, button));
            return button;
        }));
    }

    async function find() {
        search?.abort();
        const read = search = new AbortController();
        status.textContent = LINK_COPY.searching;
        try {
            const query = new URLSearchParams({ text: input.value.trim(), limit: String(RESULT_LIMIT) });
            const page = await api.get(`${LIBRARY_URL}?${query}`, { signal: read.signal, cache: 'no-store' });
            if (read === search && !closed) show(page.items ?? []);
        } catch (error) {
            if (error?.name !== 'AbortError' && read === search && !closed) status.textContent = LINK_COPY.failed;
        }
    }

    input.addEventListener('input', () => {
        clearTimeout(timer);
        timer = setTimeout(find, SEARCH_DELAY_MS);
    });
    select.addEventListener('change', syncSubmit);
    cancel.addEventListener('click', () => { if (!busy()) closeWorkspaceDialog(); });
    submit.addEventListener('click', async () => {
        if (submit.getAttribute('aria-disabled') === 'true') return;
        warn('');
        submit.setAttribute('aria-disabled', 'true');
        const done = await controller.link({ itemId, positiveId: chosen.id, pairId: pairIdOf(select.value), title: titleOf(chosen) });
        if (closed) return;
        if (done) { closeWorkspaceDialog(); return; }
        warn(controller.state.warning);
        syncSubmit();
    });

    openContentDialog({ scope, title: LINK_COPY.title, trigger, focusTarget: input,
        nodes: [sentence, field(LINK_COPY.search, input, 'review-link-search'), status, results, negative, warning, actions],
        canClose: () => !busy(),
        onClose: () => {
            closed = true;
            stopWatching();
            clearTimeout(timer);
            search?.abort();
            variantsRead?.abort();
        } });
    syncSubmit();
    find();
}
