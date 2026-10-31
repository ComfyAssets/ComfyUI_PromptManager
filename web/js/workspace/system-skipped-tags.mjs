/**
 * System › Auto-tagging "Skipped tags" (#431, D202): words whose suggested tags are never offered, on every suggestion
 * path (the server filters; hand-typed and stored tags are never touched).
 *
 * A `.setting-block` after "Suggestions per image": the label and description, an "Add a word or words" input
 * (`#tag-skipped-input`) with Add (Enter adds too), the entries as removable chips in the Tag image dialog's D83 chip
 * markup, and a preview line (`role="status"`) that says how many known tags the list hides
 * (`POST /prompt_manager/autotag/skipped-tags/preview`: the library's tags plus the downloaded WD14 label lists). The
 * block carries `data-setting="tag-skipped"` and `data-key="autotag_skipped_tags"`, so a refused save highlights it.
 * The list is the host draft `autotag_skipped_tags` (an array), saved by the one Save row. The client checks repeat the
 * server's rules (the server is the authority): an empty, too long, comma or duplicate entry is not added and the line
 * under the input says why; the list holds at most 200 entries.
 */
import { createButton } from '../shared/toolkit/core.js';

export const SKIPPED_KEY = 'autotag_skipped_tags';
export const PREVIEW_URL = '/prompt_manager/autotag/skipped-tags/preview';
export const MAX_ENTRIES = 200;
export const MAX_ENTRY_CHARS = 50;
export const PREVIEW_DELAY_MS = 300;
export const SAMPLE_LIMIT = 12;

export const SKIPPED_COPY = Object.freeze({
    label: 'Skipped tags',
    description: 'Suggested tags that contain one of these words are hidden. Words are separated by spaces, _ or -; '
        + 'other characters such as ( ) belong to the word.',
    placeholder: 'Add a word or words',
    add: 'Add',
    chips: 'Skipped tags',
    empty: 'Enter a word.',
    comma: 'No commas or line breaks.',
    long: `At most ${MAX_ENTRY_CHARS} characters.`,
    duplicate: 'Already in the list.',
    full: `The list is full (${MAX_ENTRIES}).`,
    none: 'No tags are skipped.',
    checking: 'Checking…',
    failed: 'The preview could not be read.',
    unchanged: 'Tags already on images are not changed.',
    libraryOnly: 'Checked against library tags only; no WD14 label list is downloaded.',
    hidesNothing: 'Hides no known tag',
});

const plural = (count, word) => `${count.toLocaleString('en-US')} ${word}${count === 1 ? '' : 's'}`;

/** The words of an entry or a tag as the server splits them: NFC, lower case, split on space, `_` and `-`. */
export const entryWords = text => String(text ?? '').normalize('NFC').toLowerCase().split(/[ _-]+/).filter(Boolean);
const entryKey = text => entryWords(text).join(' ');

/** Why `raw` cannot be added to `entries` (the line under the input), or '' when it can. */
export function entryProblem(raw, entries) {
    const entry = String(raw ?? '').trim();
    if (!entryWords(entry).length) return SKIPPED_COPY.empty;
    // Commas and control characters (a pasted line break), as the server refuses them.
    if (/[,\u0000-\u001f\u007f-\u009f\u2028\u2029]/u.test(entry)) return SKIPPED_COPY.comma;
    if ([...entry].length > MAX_ENTRY_CHARS) return SKIPPED_COPY.long;
    const key = entryKey(entry);
    if (entries.some(item => entryKey(item) === key)) return SKIPPED_COPY.duplicate;
    if (entries.length >= MAX_ENTRIES) return SKIPPED_COPY.full;
    return '';
}

/** A chip's title and count text for an entry the last preview counted (`count` undefined: not counted yet). */
export function chipCount(count) {
    if (!Number.isSafeInteger(count)) return null;
    return count ? { title: `Hides ${plural(count, 'tag')}`, text: count.toLocaleString('en-US') }
        : { title: SKIPPED_COPY.hidesNothing, text: 'none' };
}

/**
 * The preview line for a preview answer (`{count, sample, sources}`): the bold lead "Hides N tags from suggestions",
 * then the sample (": a, b and K more. ") and the closing sentences. Returns `{lead, rest}`.
 */
export function previewText(result) {
    const count = Number.isSafeInteger(result?.count) && result.count > 0 ? result.count : 0;
    const sample = Array.isArray(result?.sample) ? result.sample.filter(name => typeof name === 'string') : [];
    const more = count - sample.length;
    const listed = count && sample.length ? `: ${sample.join(', ')}${more > 0 ? ` and ${more.toLocaleString('en-US')} more` : ''}. ` : '. ';
    const labels = Array.isArray(result?.sources?.wd14) && result.sources.wd14.length > 0;
    return {
        lead: `Hides ${plural(count, 'tag')} from suggestions`,
        rest: `${listed}${labels ? '' : `${SKIPPED_COPY.libraryOnly} `}${SKIPPED_COPY.unchanged}`,
    };
}

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

/**
 * Build the block; `drafts` is the System draft store, `api` the workspace client. Returns `{node, repaint}`: System
 * calls `repaint` whenever the drafts change (the chips follow the drafted list, and a changed list is previewed
 * `PREVIEW_DELAY_MS` after the last change, one request at a time: a newer list aborts the older request).
 */
export function skippedTagsBlock({ api, scope, drafts, delay = PREVIEW_DELAY_MS }) {
    const block = el('div', undefined, 'setting-block');
    block.dataset.setting = 'tag-skipped';
    block.dataset.key = SKIPPED_KEY;
    const text = el('div', undefined, 'setting-block-text');
    const label = el('label', SKIPPED_COPY.label);
    label.htmlFor = 'tag-skipped-input';
    text.append(label, el('p', SKIPPED_COPY.description));
    const addRow = el('div', undefined, 'skip-add');
    const input = el('input');
    input.id = 'tag-skipped-input';
    input.autocomplete = 'off';
    input.placeholder = SKIPPED_COPY.placeholder;
    input.maxLength = MAX_ENTRY_CHARS + 10;
    input.setAttribute('aria-describedby', 'tag-skipped-problem');
    const add = createButton(SKIPPED_COPY.add);
    add.id = 'tag-skipped-add';
    addRow.append(input, add);
    const problem = el('p', '', 'small skip-problem');
    problem.id = 'tag-skipped-problem';
    problem.setAttribute('role', 'alert');
    const chips = el('div', undefined, 'tags skip-chips');
    chips.setAttribute('aria-label', SKIPPED_COPY.chips);
    const preview = el('p', '', 'small skip-preview');
    preview.setAttribute('role', 'status');
    preview.setAttribute('aria-live', 'polite');
    block.append(text, addRow, problem, chips, preview);

    const entries = () => {
        const value = drafts.get('host', SKIPPED_KEY);
        return Array.isArray(value) ? value : null;
    };
    let counts = new Map();
    let shown = null; // the list the chips show (JSON)
    let pending = null; // the list the preview line is (or is being) made for (JSON)
    let timer = null;
    let request = null;
    scope.cleanup(() => { clearTimeout(timer); request?.abort(); });

    const say = message => {
        problem.textContent = message;
        if (message) input.setAttribute('aria-invalid', 'true');
        else input.removeAttribute('aria-invalid');
    };
    const setList = next => drafts.set('host', SKIPPED_KEY, next);

    function addEntry() {
        const list = entries();
        if (!list) return;
        const message = entryProblem(input.value, list);
        say(message);
        if (message) return;
        const entry = input.value.trim();
        input.value = '';
        setList([...list, entry]);
    }

    function removeEntry(index) {
        const list = entries();
        if (!list) return;
        const buttons = [...chips.querySelectorAll?.('button') ?? []];
        const focused = buttons.indexOf(document.activeElement);
        setList(list.filter((_entry, at) => at !== index));
        // The removed chip's focus moves to its neighbour's ×, else to the input.
        if (focused >= 0) {
            const left = [...chips.querySelectorAll?.('button') ?? []];
            (left[Math.min(focused, left.length - 1)] ?? input).focus?.();
        }
    }

    function renderChips(list) {
        chips.replaceChildren(...list.map((entry, index) => {
            const count = chipCount(counts.get(entry));
            const chip = el('span', undefined, `chip tag-suggestion${count?.text === 'none' ? ' none' : ''}`);
            if (count) chip.title = count.title;
            chip.append(el('span', entry));
            if (count) chip.append(el('span', count.text, 'tag-confidence'));
            const remove = createButton('×');
            remove.setAttribute('aria-label', `Remove ${entry}`);
            scope.listen(remove, 'click', () => removeEntry(index));
            chip.append(remove);
            return chip;
        }));
    }

    function showPreview(result) {
        const { lead, rest } = previewText(result);
        preview.replaceChildren(el('b', lead), rest);
    }

    async function runPreview(list) {
        request?.abort();
        const controller = new AbortController();
        request = controller;
        try {
            const result = await api.post(PREVIEW_URL, { entries: list }, { signal: controller.signal });
            if (controller.signal.aborted || scope.signal.aborted) return;
            counts = new Map((Array.isArray(result?.per_entry) ? result.per_entry : [])
                .filter(row => typeof row?.entry === 'string').map(row => [row.entry, row.count]));
            showPreview(result);
            renderChips(entries() ?? list);
        } catch {
            if (controller.signal.aborted || scope.signal.aborted) return;
            preview.textContent = SKIPPED_COPY.failed;
        } finally {
            if (request === controller) request = null;
        }
    }

    function schedule(list) {
        clearTimeout(timer);
        request?.abort();
        request = null;
        if (!list.length) {
            counts = new Map();
            preview.textContent = SKIPPED_COPY.none;
            return;
        }
        preview.textContent = SKIPPED_COPY.checking;
        timer = setTimeout(() => runPreview(list), delay);
    }

    function repaint() {
        const list = entries();
        input.disabled = add.disabled = list === null;
        if (list === null) return;
        const full = list.length >= MAX_ENTRIES;
        add.disabled = full;
        if (full && !problem.textContent) say(SKIPPED_COPY.full);
        if (!full && problem.textContent === SKIPPED_COPY.full) say('');
        const key = JSON.stringify(list);
        if (key !== shown) {
            shown = key;
            renderChips(list);
        }
        if (key !== pending) {
            pending = key;
            schedule(list);
        }
    }

    scope.listen(add, 'click', addEntry);
    scope.listen(input, 'keydown', event => {
        if (event.key !== 'Enter') return;
        event.preventDefault?.();
        addEntry();
    });
    scope.listen(input, 'input', () => { if (problem.textContent !== SKIPPED_COPY.full) say(''); });
    return { node: block, repaint };
}
