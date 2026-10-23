/**
 * System's row builders on the concept's markup (#249 System S2 Task 5), built with the DOM, never HTML strings:
 * - `settingRow` is the concept's `setting(id, label, description, type, options)` (app.js 624–634): `.setting-row` >
 *   `div` > `label[for]` + `p`, then the control. A toggle is the Toolkit switch, as the concept's `hydrateSwitches`
 *   (708–717) makes it; a range is the concept's `min=1 max=16` unless the row gives its own bounds.
 * - `operationRow` is `operation(title, desc, action, label, extra, danger)` (645–654): `.operation` > `div` > `h3` + `p`,
 *   then `.row-actions` with one button, `danger` when flagged.
 * - `sectionNav` is `sectionNav(items, active, action)` (306–308): `.section-nav[role=group]` buttons with `aria-pressed`.
 * Every listener is registered on the caller's `scope`, so disposing it leaves none behind.
 */
import { createButton, createSwitch } from '../shared/toolkit/core.js';

const INPUT_TYPES = new Set(['number', 'password', 'text', 'range']);
const RANGE_DEFAULTS = Object.freeze({ min: 1, max: 16 });

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

function requireText(values) {
    for (const [name, value] of Object.entries(values)) {
        if (typeof value !== 'string' || !value) throw new TypeError(`The row needs a ${name}`);
    }
}

function requireScope(scope, callback, name) {
    if (callback === undefined) return;
    if (typeof callback !== 'function') throw new TypeError(`The row ${name} must be a function`);
    if (typeof scope?.listen !== 'function') throw new TypeError('The row needs the section scope for its listener');
}

/** `[value, label]` pairs from strings or pairs. */
function optionPairs(options) {
    if (!Array.isArray(options) || options.length === 0) throw new TypeError('A select row needs its options');
    return options.map(option => (Array.isArray(option) ? option.map(String) : [String(option), String(option)]));
}

/** The control a spec describes, and how to read its value. */
function buildControl(id, spec) {
    if (spec?.type === 'toggle') {
        const field = createSwitch('', '', id);
        const input = field.children[0];
        // The concept's toggles are on unless configured off (`val !== false`).
        input.checked = spec.checked !== false;
        return { node: field, input, read: () => input.checked };
    }
    if (spec?.type === 'select') {
        const select = el('select');
        for (const [value, label] of optionPairs(spec.options)) {
            const option = el('option', label);
            option.value = value;
            select.append(option);
        }
        if (spec.value !== undefined) select.value = String(spec.value);
        return { node: select, input: select, read: () => select.value };
    }
    if (INPUT_TYPES.has(spec?.type)) {
        const input = el('input');
        input.type = spec.type;
        const bounds = spec.type === 'range' ? { ...RANGE_DEFAULTS, ...spec } : spec;
        for (const key of ['min', 'max', 'step']) if (bounds[key] !== undefined) input[key] = String(bounds[key]);
        input.value = spec.value === undefined || spec.value === null ? '' : String(spec.value);
        const numeric = spec.type === 'number' || spec.type === 'range';
        return { node: input, input, read: () => (numeric ? Number(input.value) : input.value) };
    }
    throw new TypeError(`Unknown setting control: ${spec?.type}`);
}

/**
 * One `.setting-row`. `control` is a spec (`{type:'toggle', checked}`, `{type:'select', options, value}` with string or
 * `[value, label]` options, `{type:'number'|'password'|'text'|'range', value, min, max, step}`) or a ready-made node,
 * which takes `id`. `onChange(value)` (on `scope`) reports the checked state, a number for number and range, else the
 * text.
 */
export function settingRow({ id, label, description, control, scope, onChange }) {
    requireText({ id, label, description });
    requireScope(scope, onChange, 'onChange');
    let built;
    if (control && typeof control === 'object' && typeof control.type !== 'string' && 'tagName' in control) {
        control.id = id;
        built = { node: control, input: control, read: () => control.value };
    } else {
        built = buildControl(id, control);
        built.input.id = id;
    }
    built.input.dataset.setting = id;
    const row = el('div', undefined, 'setting-row');
    const text = el('div');
    const caption = el('label', label);
    caption.htmlFor = id;
    text.append(caption, el('p', description));
    row.append(text, built.node);
    if (onChange) scope.listen(built.input, 'change', () => onChange(built.read()));
    return row;
}

/** One `.operation` row; `onClick(button)` runs on `scope`. */
export function operationRow({ title, description, label, danger = false, onClick, scope }) {
    requireText({ title, description, label });
    if (typeof onClick !== 'function') throw new TypeError('The operation needs onClick');
    requireScope(scope, onClick, 'onClick');
    const row = el('div', undefined, 'operation');
    const text = el('div');
    text.append(el('h3', title), el('p', description));
    const actions = el('div', undefined, 'row-actions');
    const action = createButton(label, danger ? 'danger' : '');
    scope.listen(action, 'click', () => onClick(action));
    actions.append(action);
    row.append(text, actions);
    return row;
}

/** The `.section-nav` group: one button per `[id, title]`, the `active` one pressed; `onSelect(id)` runs on `scope`. */
export function sectionNav({ label, sections, active, onSelect, scope }) {
    requireText({ label });
    if (typeof onSelect !== 'function') throw new TypeError('The section nav needs onSelect');
    requireScope(scope, onSelect, 'onSelect');
    const nav = el('div', undefined, 'section-nav');
    nav.setAttribute('role', 'group');
    nav.setAttribute('aria-label', label);
    for (const [id, title] of sections) {
        const item = createButton(title);
        item.dataset.value = id;
        item.setAttribute('aria-pressed', String(id === active));
        scope.listen(item, 'click', () => onSelect(id));
        nav.append(item);
    }
    return nav;
}
