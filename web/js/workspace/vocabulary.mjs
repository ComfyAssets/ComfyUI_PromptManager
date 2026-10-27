/**
 * Native Tags & categories screen (concept renderTags, app.js:719-729).
 *
 * Composition, labels and order follow the approved concept: Auto-tag images
 * opens the Tag image dialog for the image selected in Images (aria-disabled
 * without one, D115) and the Images cells count the files carrying each tag
 * (#249 C2). The recorded deviation left is bounded lists with Load more. Rows
 * are real vocabulary pages from vocabulary-state.mjs; Browse hands the exact
 * constraint to the existing Library filters.
 */
import { createButton } from '../shared/toolkit/core.js';
import { browseFilters, createVocabularyCommand, createVocabularyStream } from './vocabulary-state.mjs';
import { countOf } from './plural.mjs';
import { browseLibrary, invalidateLibraryAfterWrite } from './library.mjs';
import { createVocabularyDialog } from './vocabulary-dialog.mjs';
import { pageHeading } from './heading.mjs';
import { selectedImage } from './images.mjs';
import { onImageTagsSaved, openImageTagging } from './image-tagging.mjs';
import { showWorkspaceToast } from './toast.mjs';

/** D115: why Auto-tag images (and System › Auto-tagging's Preview tagging review) only explains without a selection. */
export const NO_SELECTION = 'Select an image in Images first.';

const COPY = {
    tags: {empty: 'No tags yet.', error: 'Tags could not be loaded. Nothing was changed.', more: 'Load more tags'},
    categories: {empty: 'No categories yet.', error: 'Categories could not be loaded. Nothing was changed.', more: 'Load more categories'},
};
// Append the next page this far before the end of a list comes into view.
const LOAD_MARGIN = 250;

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

/** The concept's `.operation` row: h3 title, p description, one row action. */
function operation(title, description, label) {
    const row = el('div', undefined, 'operation');
    const copy = el('div');
    copy.append(el('h3', title), el('p', description));
    const actions = el('div', undefined, 'row-actions');
    const button = createButton(label);
    actions.append(button);
    row.append(copy, actions);
    return {row, button};
}

function tagRow(tag) {
    const row = el('tr');
    // The files carrying the tag (absent from an older server, which counts none).
    const images = el('td', String(tag.image_count ?? 0));
    const cell = el('td');
    const actions = el('div', undefined, 'row-actions');
    const browse = createButton('Browse');
    browse.dataset.action = 'browse';
    const rename = createButton('Rename / merge');
    rename.dataset.action = 'rename';
    actions.append(browse, rename);
    cell.append(actions);
    row.append(el('td', tag.name), el('td', String(tag.prompt_count)), images, cell);
    return row;
}

function categoryRow(category) {
    const {row, button} = operation(category.value, countOf(category.prompt_count, 'positive prompt'), 'Browse');
    button.dataset.action = 'browse';
    return row;
}

/**
 * One bounded list: rows append as pages arrive, the end of the list loads the
 * next page each time it scrolls into view, and `Load more` stays available while
 * more remain. Only a new intersection appends: when scroll anchoring keeps the end
 * in view (stacked layout below 700px), pages do not chain on their own. Before any row arrives (loading, failed) the list shows no rows, so a
 * failed read never looks like zero counts.
 */
function mountList(scope, api, kind, {content, rowsHost, renderRow, after}) {
    const copy = COPY[kind];
    const empty = el('p', copy.empty, 'empty');
    const continuation = el('div', undefined, 'vocabulary-continuation');
    const status = el('p', '', 'vocabulary-status');
    status.setAttribute('role', 'status');
    const alert = el('p', copy.error, 'vocabulary-error');
    alert.setAttribute('role', 'alert');
    const retry = createButton('Retry');
    const more = createButton(copy.more);
    continuation.append(status, alert, retry, more);
    after.after(empty, continuation);
    const items = new WeakMap();
    const listeners = new Set();
    let rendered = 0, first = null;

    function maybeLoad() {
        const {state} = stream;
        if (scope.signal.aborted || !state.hasMore || state.loading || state.error || state.resetRequired) return;
        const bounds = continuation.getBoundingClientRect();
        if (bounds.top <= window.innerHeight + LOAD_MARGIN && bounds.bottom >= -LOAD_MARGIN) stream.loadMore();
    }
    function render(state) {
        // A reset starts again from page one; otherwise pages only append.
        if (state.items.length < rendered || (rendered && state.items[0] !== first)) {
            rowsHost.replaceChildren();
            rendered = 0;
        }
        const appended = [];
        for (const item of state.items.slice(rendered)) {
            const row = renderRow(item);
            items.set(row, item);
            appended.push(row);
        }
        rowsHost.append(...appended);
        rendered = state.items.length;
        first = state.items[0] ?? null;
        const failed = Boolean(state.error);
        content.hidden = !rendered;
        empty.hidden = Boolean(rendered || state.loading || failed || state.hasMore);
        status.textContent = state.loading ? 'Loading…' : '';
        status.hidden = !state.loading;
        alert.hidden = !failed;
        retry.hidden = !failed;
        const hideMore = !state.hasMore || failed || !rendered;
        // Keyboard users keep a focus target: Retry after a failed read, else the first new (or first) row.
        if (hideMore && document.activeElement === more) {
            (failed ? retry : appended[0]?.querySelector('button') ?? rowsHost.querySelector('button'))?.focus();
        }
        more.hidden = hideMore;
        more.setAttribute('aria-disabled', String(state.loading));
        continuation.hidden = !(state.loading || failed || state.hasMore);
        for (const listener of listeners) listener(state);
    }
    const stream = createVocabularyStream({api, kind, onChange: render});
    scope.listen(more, 'click', () => stream.loadMore());
    scope.listen(retry, 'click', () => (stream.state.resetRequired ? stream.reset() : stream.loadMore()));
    let observer = null;
    if (typeof IntersectionObserver === 'function') {
        observer = new IntersectionObserver(entries => {
            if (entries.some(entry => entry.isIntersecting)) maybeLoad();
        }, {rootMargin: `${LOAD_MARGIN}px`});
        observer.observe(continuation);
    }
    scope.cleanup(() => {
        stream.dispose();
        observer?.disconnect();
    });
    render(stream.state);
    /** The rendered row whose item matches, if it is loaded. */
    function rowFor(match) {
        return [...rowsHost.children].find(row => {const item = items.get(row); return item !== undefined && match(item);}) ?? null;
    }
    /** Follow this list's state (a dialog reading further pages); returns the unsubscribe. */
    function subscribe(listener) {
        listeners.add(listener);
        return () => listeners.delete(listener);
    }
    return {stream, itemOf: row => items.get(row), rowFor, subscribe};
}

/** Mount the screen into `main` below the shell's heading; `scope` owns every request, listener and observer. */
export function mountVocabulary(main, api, scope) {
    main.classList.add('vocabulary-main');
    scope.cleanup(() => main.classList.remove('vocabulary-main'));
    const { description, actions } = pageHeading(main);
    description.textContent = 'Organize once. Reuse everywhere.';
    const create = createButton('+ Create tag');
    const autoTag = createButton('Auto-tag images', 'ca-primary');
    actions.append(create, autoTag);
    // The concept's `tag-review` for `state.selectedImage` (app.js a1511); D115: none selected, so it only explains.
    const selected = selectedImage();
    if (!selected) autoTag.setAttribute('aria-disabled', 'true');
    scope.listen(autoTag, 'click', () => {
        if (!selected) { showWorkspaceToast(NO_SELECTION); return; }
        openImageTagging({api, scope, image: selected, source: 'tags', trigger: autoTag});
    });

    const grid = el('div', undefined, 'two-col');
    const tagSurface = el('section', undefined, 'surface');
    const wrap = el('div', undefined, 'table-wrap');
    const table = el('table');
    const head = el('thead');
    const headRow = el('tr');
    for (const label of ['Tag', 'Prompts', 'Images', 'Actions']) {
        const cell = el('th', label);
        cell.scope = 'col';
        headRow.append(cell);
    }
    head.append(headRow);
    const body = el('tbody');
    table.append(el('caption', 'Reusable tags'), head, body);
    wrap.append(table);
    tagSurface.append(el('h2', 'Tag vocabulary'),
        el('p', 'Category/tag autocomplete uses this same vocabulary across nodes and dialogs.'), wrap);

    const categorySurface = el('section', undefined, 'surface');
    const categoryRows = el('div', undefined, 'vocabulary-categories');
    const manage = operation('Manage category', 'Rename or consolidate a category with a preview of affected recipes.', 'Review');
    categorySurface.append(el('h2', 'Categories'),
        el('p', 'Categories provide a broad home. Tags describe content; proposed collections group projects without changing either.'),
        categoryRows, manage.row);
    grid.append(tagSurface, categorySurface);
    main.append(grid);

    const tags = mountList(scope, api, 'tags', {content: wrap, rowsHost: body, renderRow: tagRow, after: wrap});
    const categories = mountList(scope, api, 'categories', {content: categoryRows, rowsHost: categoryRows, renderRow: categoryRow, after: categoryRows});
    scope.listen(body, 'click', event => {
        const button = event.target.closest('button[data-action="browse"]');
        const tag = button && tags.itemOf(button.closest('tr'));
        if (tag) browseLibrary(browseFilters('tags', tag.name));
    });
    scope.listen(categoryRows, 'click', event => {
        const button = event.target.closest('button[data-action="browse"]');
        const category = button && categories.itemOf(button.closest('.operation'));
        if (category) browseLibrary(browseFilters('categories', category.value));
    });

    let settled = Promise.resolve();
    /** Re-read both lists from page one: after any command, rows can move between pages. */
    function reload() {
        settled = Promise.all([tags.stream.reset(), categories.stream.reset()]).then(() => undefined);
        return settled;
    }
    /** A guarded command for one dialog; any outcome that may have changed storage re-reads the lists and the Library shelf. */
    function createCommand(onChange) {
        return createVocabularyCommand({api, onChange, onInvalidate: event => {
            invalidateLibraryAfterWrite({kind: 'vocabulary', receipt: event.receipt || {phase: event.phase}});
            reload();
        }});
    }
    const h1 = main.querySelector('h1');
    const actionOf = (row, action) => row?.querySelector(`button[data-action="${action}"]`) ?? null;
    const dialog = createVocabularyDialog({api, scope, createCommand,
        epoch: () => tags.stream.state.epoch ?? categories.stream.state.epoch,
        categories: {stream: categories.stream, subscribe: categories.subscribe},
        reload,
        // Rows are replaced on every reset, so a result's row exists only once the re-read has landed.
        settled: () => settled,
        heading: () => h1,
        tagAction: id => actionOf(tags.rowFor(item => item.id === id), 'rename'),
        categoryAction: value => actionOf(categories.rowFor(item => item.value === value), 'browse')});
    scope.listen(create, 'click', () => dialog.createTag(create));
    scope.listen(manage.button, 'click', () => dialog.manageCategories(manage.button));
    scope.listen(body, 'click', event => {
        const button = event.target.closest('button[data-action="rename"]');
        const tag = button && tags.itemOf(button.closest('tr'));
        if (tag) dialog.renameTag(tag, button);
    });
    // A saved image tag set changes the Images counts.
    scope.cleanup(onImageTagsSaved(() => { if (!scope.signal.aborted) reload(); }));
    reload();
    return {autoTag, create, manage: manage.button, tags: tags.stream, categories: categories.stream, reload, createCommand, dialog};
}
