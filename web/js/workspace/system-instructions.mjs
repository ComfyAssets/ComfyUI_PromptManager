/**
 * System › Auto-tagging "Saved instructions" (#431, D203): named language-model instructions and the default of each
 * language-model engine. It replaces the concept's "LLM tag template" textarea.
 *
 * A `.setting-block` (`data-setting="tag-instructions"`, `data-key="autotag_instructions"`): the label, the
 * description with the count ("N of 20 saved · Built\u2011in is not counted."), a `role="listbox"` list (Built-in first,
 * "Read-only", then the saved entries, each with its "Default for GGUF" / "Default for HuggingFace" chips), "+ New",
 * "Duplicate" and "Delete", and the editor beside the list (under it below 700 px): "Name" (60 characters, live count)
 * and "Text" ("N of 4,000 characters"), both read-only for Built-in with its note. Then two `.setting-row` selects,
 * "Default for GGUF JoyCaption" and "Default for HuggingFace JoyCaption" (`data-key="autotag_default_instructions"`),
 * and the note a deleted default leaves ("GGUF now uses Built-in."). Host drafts: `autotag_instructions` (the saved
 * entries, never Built-in) and `autotag_default_instructions`; the server pairs them on save. Built-in's text is the
 * read-only `autotag_builtin_instructions` of GET /settings. Ids are 12 lowercase hex characters made here.
 */
import { createButton } from '../shared/toolkit/core.js';
import { settingRow } from './system-controls.mjs';

export const INSTRUCTIONS_KEY = 'autotag_instructions';
export const DEFAULTS_KEY = 'autotag_default_instructions';
export const BUILTIN_TEXT_KEY = 'autotag_builtin_instructions';
export const BUILTIN_ID = 'builtin';
export const BUILTIN_NAME = 'Built-in';
export const MAX_SAVED = 20;
export const MAX_NAME_CHARS = 60;
export const MAX_TEXT_CHARS = 4000;
export const ENGINES = Object.freeze([['gguf', 'GGUF', 'GGUF JoyCaption'], ['hf', 'HuggingFace', 'HuggingFace JoyCaption']]);

export const INSTRUCTIONS_COPY = Object.freeze({
    label: 'Saved instructions',
    description: 'Instructions for the language-model engines. Built-in can’t be changed; duplicate it to make your own.',
    count: saved => `${saved} of ${MAX_SAVED} saved · Built\u2011in is not counted.`,
    readOnly: 'Read-only',
    defaultFor: short => `Default for ${short}`,
    newLabel: '+ New',
    duplicate: 'Duplicate',
    delete: 'Delete',
    cap: `At most ${MAX_SAVED} saved instructions.`,
    name: 'Name',
    text: 'Text',
    nameCount: length => `${length} of ${MAX_NAME_CHARS}`,
    textCount: length => `${length.toLocaleString('en-US')} of ${MAX_TEXT_CHARS.toLocaleString('en-US')} characters`,
    builtinNote: 'Built-in instructions can’t be changed. Duplicate them to make your own.',
    newName: 'New instructions',
    copyName: name => `${name} copy`,
    defaultRow: long => `Default for ${long}`,
    defaultDescription: 'The Tag image dialog and batches start from this.',
    reset: shorts => `${shorts.join(' and ')} now ${shorts.length > 1 ? 'use' : 'uses'} Built-in.`,
});

/** Characters as Python's `len` counts them (code points). */
const characters = text => [...String(text ?? '')].length;
const fold = text => String(text ?? '').trim().toLowerCase();

/** The saved entries of the drafts (`[]` when unread or damaged). */
export const savedEntries = values => (Array.isArray(values?.[INSTRUCTIONS_KEY]) ? values[INSTRUCTIONS_KEY] : []);

/** Built-in (its text from GET /settings) and then the saved entries. */
export function allEntries(values) {
    const text = typeof values?.[BUILTIN_TEXT_KEY] === 'string' ? values[BUILTIN_TEXT_KEY] : '';
    return [{ id: BUILTIN_ID, name: BUILTIN_NAME, text }, ...savedEntries(values)];
}

/** The drafted default entry of `engine` (`gguf` or `hf`); Built-in when the id names nothing. */
export function defaultEntry(values, engine) {
    const entries = allEntries(values);
    const id = values?.[DEFAULTS_KEY]?.[engine];
    return entries.find(entry => entry.id === id) ?? entries[0];
}

/** `base`, else `base (2)`, `base (3)`… whichever no entry uses (case-insensitive). */
export function freeName(base, entries) {
    const taken = new Set(entries.map(entry => fold(entry.name)));
    if (!taken.has(fold(base))) return base;
    for (let n = 2; ; n += 1) if (!taken.has(fold(`${base} (${n})`))) return `${base} (${n})`;
}

/** "<name> copy" (then "(2)"…), the name cut so the whole stays within the 60-character limit. */
export function copyName(name, entries) {
    for (let room = MAX_NAME_CHARS - ' copy'.length; room > 0; room -= 1) {
        const base = [...String(name ?? '').trim()].slice(0, room).join('').trimEnd();
        const candidate = freeName(INSTRUCTIONS_COPY.copyName(base), entries);
        if (characters(candidate) <= MAX_NAME_CHARS) return candidate;
    }
    return freeName(INSTRUCTIONS_COPY.copyName(BUILTIN_NAME), entries);
}

/** A new entry id: 12 lowercase hex characters from `random` (crypto.getRandomValues), never one already used. */
export function newId(entries, random = bytes => globalThis.crypto.getRandomValues(bytes)) {
    const used = new Set(entries.map(entry => entry.id));
    for (;;) {
        const id = [...random(new Uint8Array(6))].map(byte => byte.toString(16).padStart(2, '0')).join('');
        if (!used.has(id)) return id;
    }
}

/** The editor's note for a saved entry's name and text (the mock's short sentences), or ''. */
export function entryNote(entry, entries) {
    const name = String(entry.name ?? '').trim();
    if (!name) return 'Needs a name.';
    if (characters(name) > MAX_NAME_CHARS) return `The name must be at most ${MAX_NAME_CHARS} characters.`;
    if (/[\u0000-\u001f\u007f-\u009f\u2028\u2029]/u.test(name)) return 'The name must not contain a line break.';
    if (fold(name) === fold(BUILTIN_NAME)) return 'The name “Built-in” is reserved.';
    if (entries.some(other => other !== entry && other.id !== BUILTIN_ID && fold(other.name) === fold(name))) {
        return `The name “${name}” is already used.`;
    }
    if (!String(entry.text ?? '').trim()) return 'The text must not be empty.';
    if (characters(entry.text) > MAX_TEXT_CHARS) return `The text must be at most ${MAX_TEXT_CHARS.toLocaleString('en-US')} characters.`;
    return '';
}

/**
 * The server's sentence for the first saved entry it would refuse (Built-in is not counted), else null. The Save row
 * shows the server's own refusal; this one holds back Preview tagging review and the batch Review.
 */
export function listProblem(values) {
    const entries = savedEntries(values);
    for (const [index, entry] of entries.entries()) {
        // As the server checks them: a repeated name is the later entry's problem.
        const note = entryNote(entry, entries.slice(0, index + 1));
        if (!note) continue;
        const n = index + 1;
        if (note.startsWith('Needs')) return `Saved instructions: entry ${n} needs a name.`;
        if (note.includes('reserved')) return 'Saved instructions: the name “Built-in” is reserved.';
        if (note.includes('already used')) return `Saved instructions: entry ${n} name “${String(entry.name).trim()}” is already used.`;
        if (note.includes('line break')) return `Saved instructions: entry ${n} name must not contain a line break.`;
        if (note.startsWith('The name')) return `Saved instructions: entry ${n} name must be at most ${MAX_NAME_CHARS} characters.`;
        if (note.includes('empty')) return `Saved instructions: entry ${n} text must be non-empty text.`;
        return `Saved instructions: entry ${n} text must be at most ${MAX_TEXT_CHARS} characters.`;
    }
    return null;
}

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

/** A `.field` with a label row (text and a muted count) and its control. */
function field(id, label, control) {
    const wrap = el('div', undefined, 'field');
    const caption = el('label');
    caption.htmlFor = id;
    const count = el('span', '', 'small muted');
    caption.append(el('span', label), count);
    control.id = id;
    wrap.append(caption, control);
    return { wrap, count };
}

/**
 * Build the block and the two default rows; `drafts` is the System draft store. Returns `{nodes, repaint}`: System
 * appends `nodes` in order and calls `repaint` whenever the drafts change.
 */
export function savedInstructionsBlock({ scope, drafts, random }) {
    const block = el('div', undefined, 'setting-block');
    block.dataset.setting = 'tag-instructions';
    block.dataset.key = INSTRUCTIONS_KEY;
    const text = el('div', undefined, 'setting-block-text');
    const label = el('label', INSTRUCTIONS_COPY.label);
    label.id = 'instructions-label';
    const description = el('p', `${INSTRUCTIONS_COPY.description} `);
    const count = el('span', '');
    count.id = 'instructions-count';
    description.append(count);
    text.append(label, description);

    const layout = el('div', undefined, 'instructions-layout');
    const side = el('div', undefined, 'instructions-side');
    const list = el('div', undefined, 'instruction-list');
    list.id = 'instruction-list';
    list.setAttribute('role', 'listbox');
    list.setAttribute('aria-labelledby', 'instructions-label');
    list.tabIndex = 0;
    const actions = el('div', undefined, 'row-actions instruction-actions');
    const create = createButton(INSTRUCTIONS_COPY.newLabel);
    create.id = 'instruction-new';
    const duplicate = createButton(INSTRUCTIONS_COPY.duplicate);
    duplicate.id = 'instruction-duplicate';
    const remove = createButton(INSTRUCTIONS_COPY.delete, 'danger');
    remove.id = 'instruction-delete';
    actions.append(create, duplicate, remove);
    const cap = el('p', INSTRUCTIONS_COPY.cap, 'small');
    cap.id = 'instruction-cap';
    cap.hidden = true;
    side.append(list, actions, cap);

    const editor = el('div', undefined, 'instruction-editor');
    const nameInput = el('input');
    nameInput.autocomplete = 'off';
    const name = field('instruction-name', INSTRUCTIONS_COPY.name, nameInput);
    const textArea = el('textarea');
    textArea.rows = 7;
    const body = field('instruction-text', INSTRUCTIONS_COPY.text, textArea);
    const note = el('p', '', 'small');
    note.id = 'instruction-note';
    note.setAttribute('role', 'status');
    editor.append(name.wrap, body.wrap, note);
    layout.append(side, editor);
    block.append(text, layout);

    const selects = ENGINES.map(([engine, , long]) => {
        const row = settingRow({ id: `instruction-default-${engine}`, label: INSTRUCTIONS_COPY.defaultRow(long),
            description: INSTRUCTIONS_COPY.defaultDescription, control: { type: 'select', options: [[BUILTIN_ID, BUILTIN_NAME]] } });
        const select = row.children[1];
        select.dataset.key = DEFAULTS_KEY;
        return { engine, select, row };
    });
    const reset = el('p', '', 'small instruction-reset');
    reset.id = 'instruction-reset';
    reset.setAttribute('role', 'status');

    let selectedId = BUILTIN_ID;
    const values = () => ({ [INSTRUCTIONS_KEY]: drafts.get('host', INSTRUCTIONS_KEY),
        [DEFAULTS_KEY]: drafts.get('host', DEFAULTS_KEY), [BUILTIN_TEXT_KEY]: drafts.get('host', BUILTIN_TEXT_KEY) });
    const ready = () => Array.isArray(drafts.get('host', INSTRUCTIONS_KEY)) && drafts.get('host', DEFAULTS_KEY) !== undefined;
    const defaults = () => ({ ...(drafts.get('host', DEFAULTS_KEY) ?? {}) });
    const setEntries = next => drafts.set('host', INSTRUCTIONS_KEY, next);
    const selected = () => allEntries(values()).find(entry => entry.id === selectedId) ?? allEntries(values())[0];

    /** Replace the selected saved entry's `key` with `value` (a new list of new objects). */
    function edit(key, value) {
        if (selectedId === BUILTIN_ID) return;
        setEntries(savedEntries(values()).map(entry => (entry.id === selectedId ? { ...entry, [key]: value } : entry)));
    }

    function add(entry) {
        const entries = savedEntries(values());
        if (!ready() || entries.length >= MAX_SAVED) return;
        selectedId = entry.id;
        reset.textContent = '';
        setEntries([...entries, entry]);
    }

    function deleteSelected() {
        const entries = savedEntries(values());
        const index = entries.findIndex(entry => entry.id === selectedId);
        if (index < 0) return;
        const gone = entries[index];
        const next = defaults();
        const shorts = [];
        for (const [engine, short] of ENGINES) {
            if (next[engine] === gone.id) { next[engine] = BUILTIN_ID; shorts.push(short); }
        }
        const left = entries.filter(entry => entry.id !== gone.id);
        selectedId = (left[index] ?? left[index - 1])?.id ?? BUILTIN_ID;
        reset.textContent = shorts.length ? INSTRUCTIONS_COPY.reset(shorts) : '';
        if (shorts.length) drafts.set('host', DEFAULTS_KEY, next);
        setEntries(left);
    }

    function select(id) {
        selectedId = id;
        repaint();
    }

    function renderList(entries, chosen) {
        list.replaceChildren(...entries.map(entry => {
            const option = el('div');
            option.id = `instruction-option-${entry.id}`;
            option.dataset.id = entry.id;
            option.setAttribute('role', 'option');
            option.setAttribute('aria-selected', String(entry.id === selectedId));
            option.append(el('span', entry.name));
            const badges = el('span', undefined, 'badges');
            if (entry.id === BUILTIN_ID) badges.append(el('span', INSTRUCTIONS_COPY.readOnly, 'chip'));
            for (const [engine, short] of ENGINES) {
                if (chosen[engine] === entry.id) badges.append(el('span', INSTRUCTIONS_COPY.defaultFor(short), 'chip blue'));
            }
            if (badges.children.length) option.append(badges);
            return option;
        }));
        list.setAttribute('aria-activedescendant', `instruction-option-${selectedId}`);
    }

    function repaint() {
        // The note a deleted default left goes once the defaults are saved (or set back).
        if (!Object.hasOwn(drafts.changes('host'), DEFAULTS_KEY)) reset.textContent = '';
        const loaded = ready();
        for (const control of [create, duplicate, remove, nameInput, textArea, ...selects.map(item => item.select)]) {
            control.disabled = !loaded;
        }
        if (!loaded) return;
        const current = values();
        const entries = allEntries(current);
        if (!entries.some(entry => entry.id === selectedId)) selectedId = BUILTIN_ID;
        const chosen = defaults();
        const saved = savedEntries(current);
        renderList(entries, chosen);
        const entry = selected();
        const builtin = entry.id === BUILTIN_ID;
        // A focused field keeps what the user is typing.
        if (document.activeElement !== nameInput) nameInput.value = entry.name;
        if (document.activeElement !== textArea) textArea.value = entry.text;
        nameInput.readOnly = textArea.readOnly = builtin;
        name.count.textContent = builtin ? '' : INSTRUCTIONS_COPY.nameCount(characters(entry.name));
        body.count.textContent = INSTRUCTIONS_COPY.textCount(characters(entry.text));
        const problem = builtin ? '' : entryNote(entry, entries);
        note.textContent = builtin ? INSTRUCTIONS_COPY.builtinNote : problem;
        note.className = problem ? 'small skip-problem' : 'small';
        const full = saved.length >= MAX_SAVED;
        create.disabled = duplicate.disabled = full;
        remove.disabled = builtin;
        cap.hidden = !full;
        count.textContent = INSTRUCTIONS_COPY.count(saved.length);
        for (const { engine, select: menu } of selects) {
            menu.replaceChildren(...entries.map(item => {
                const option = el('option', item.name);
                option.value = item.id;
                return option;
            }));
            menu.value = chosen[engine] ?? BUILTIN_ID;
        }
    }

    scope.listen(create, 'click', () => {
        const entries = allEntries(values());
        add({ id: newId(entries, random), name: freeName(INSTRUCTIONS_COPY.newName, entries), text: '' });
        textArea.focus?.();
    });
    scope.listen(duplicate, 'click', () => {
        const entries = allEntries(values());
        const source = selected();
        add({ id: newId(entries, random), name: copyName(source.name, entries), text: source.text });
    });
    scope.listen(remove, 'click', deleteSelected);
    // One listener for every option, however often the list is rebuilt.
    scope.listen(list, 'click', event => {
        let node = event.target;
        while (node && node !== list && node.getAttribute?.('role') !== 'option') node = node.parentNode;
        if (node && node !== list) select(node.dataset.id);
    });
    scope.listen(nameInput, 'input', () => edit('name', nameInput.value));
    scope.listen(textArea, 'input', () => edit('text', textArea.value));
    scope.listen(list, 'keydown', event => {
        const ids = allEntries(values()).map(entry => entry.id);
        const at = ids.indexOf(selectedId);
        const next = { ArrowDown: at + 1, ArrowUp: at - 1, Home: 0, End: ids.length - 1 }[event.key];
        if (next === undefined) return;
        event.preventDefault?.();
        select(ids[Math.max(0, Math.min(ids.length - 1, next))]);
    });
    for (const { engine, select: menu } of selects) {
        scope.listen(menu, 'change', () => {
            reset.textContent = '';
            drafts.set('host', DEFAULTS_KEY, { ...defaults(), [engine]: menu.value });
        });
    }
    return { nodes: [block, ...selects.map(item => item.row), reset], repaint };
}
