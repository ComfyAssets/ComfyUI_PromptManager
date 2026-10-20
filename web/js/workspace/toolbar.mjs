/**
 * The concept's shared page toolbar (docs/concepts/workspace/app.js toolbar(), a347–360), used by Images and the
 * Prompt library: `search.toolbar` > `label.search-field` (search icon + `input#search`), `select#category-filter`,
 * `select#sort`, then `Filters`. Recorded deviations (#249): the sort lists the concept's three options first and a
 * page's extra ones after them (D14); there is no `Save view` until saved views are persisted (D16).
 */
import { createButton } from '../shared/toolkit/core.js';
import { createWorkspaceIcon } from './icons.mjs';

/** The search waits this long after the last keystroke before it reports a change. */
export const SEARCH_DELAY = 250;

// The concept's sort labels, in the concept's order. Matching is by exact label (note the en dash in "Name A–Z"); any
// other label is one of the page's appended options.
const CONCEPT_SORT_LABELS = Object.freeze(['Recently added', 'Name A–Z', 'Highest rated']);

const isText = value => typeof value === 'string';

/** `sorts` as [value, label] pairs, the concept's options first in its order, then the rest in the given order (D14). */
export function orderSorts(sorts) {
    if (!Array.isArray(sorts) || sorts.length === 0 ||
        sorts.some(pair => !Array.isArray(pair) || pair.length !== 2 || !isText(pair[0]) || !isText(pair[1]))) {
        throw new TypeError('Sorts must be a non-empty list of [value, label] pairs');
    }
    const rank = ([, label]) => {
        const index = CONCEPT_SORT_LABELS.indexOf(label);
        return index === -1 ? CONCEPT_SORT_LABELS.length : index;
    };
    return sorts.map((pair, index) => ({ pair, index }))
        .sort((a, b) => rank(a.pair) - rank(b.pair) || a.index - b.index)
        .map(({ pair }) => [...pair]);
}

function option(value, label) {
    const node = document.createElement('option');
    node.value = value;
    node.textContent = label;
    return node;
}

function categoryOptions(categories) {
    if (!Array.isArray(categories) || categories.some(value => !isText(value))) {
        throw new TypeError('Categories must be a list of strings');
    }
    const values = [...new Set(categories.filter(Boolean))];
    return [option('', 'All categories'), ...values.map(value => option(value, value))];
}

/**
 * Mount the toolbar into `container`. `value` is `{q, category, sort}`; `onChange(value)` receives the whole new
 * value after a select change or, debounced, after typing; `onFilters(trigger)` opens the page's "Refine this view".
 * The toolbar leaves `container` when `scope` is disposed. Returns `{element, value, setValue, setCategories}`.
 */
export function mountToolbar(container, { scope, categories = [], sorts, value = {}, onChange, onFilters }) {
    if (typeof scope?.listen !== 'function' || typeof scope.debounce !== 'function' || typeof scope.cleanup !== 'function') {
        throw new TypeError('The toolbar needs the page scope');
    }
    if (typeof onChange !== 'function' || typeof onFilters !== 'function') {
        throw new TypeError('The toolbar needs onChange and onFilters');
    }
    const root = document.createElement('search');
    root.className = 'toolbar';

    const label = document.createElement('label');
    label.className = 'search-field';
    label.htmlFor = 'search';
    const search = document.createElement('input');
    search.id = 'search';
    search.type = 'search';
    search.placeholder = 'Search prompts, tags, filenames…';
    // The listing API rejects longer search text (py/api/images.py _workspace_filters).
    search.maxLength = 500;
    search.setAttribute('aria-label', 'Search workspace');
    label.append(createWorkspaceIcon('search'), search);

    const category = document.createElement('select');
    category.id = 'category-filter';
    category.setAttribute('aria-label', 'Filter by category');
    category.replaceChildren(...categoryOptions(categories));

    const sort = document.createElement('select');
    sort.id = 'sort';
    sort.setAttribute('aria-label', 'Sort results');
    sort.replaceChildren(...orderSorts(sorts).map(([id, text]) => option(id, text)));

    const filters = createButton('Filters');
    root.append(label, category, sort, filters);

    const read = () => ({ q: search.value, category: category.value, sort: sort.value });
    function setValue(next) {
        if (isText(next?.q)) search.value = next.q;
        if (isText(next?.category)) category.value = next.category;
        if (isText(next?.sort)) sort.value = [...sort.options].some(node => node.value === next.sort) ? next.sort : sort.options[0].value;
    }
    function setCategories(next) {
        const selected = category.value;
        category.replaceChildren(...categoryOptions(next));
        category.value = [...category.options].some(node => node.value === selected) ? selected : '';
    }
    setValue(value);

    const report = () => onChange(read());
    scope.listen(search, 'input', scope.debounce(report, SEARCH_DELAY));
    scope.listen(category, 'change', report);
    scope.listen(sort, 'change', report);
    scope.listen(filters, 'click', () => onFilters(filters));
    scope.cleanup(() => root.remove());

    container.append(root);
    return { element: root, value: read, setValue, setCategories };
}
