// The legacy System capability coverage table (TESTING.md "Legacy System capability coverage", #249 System S12 #329):
// every capability of the legacy admin and gallery pages names its owner, a status, the native control that replaces it
// and a declared test. tests/vendor/legacy-coverage.test.mjs checks each row against the test files; the browser spec
// `every covered legacy System capability has its native control` finds each covered row's control on its System page.
import { readFileSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

export const ROOT = join(dirname(fileURLToPath(import.meta.url)), '../..');
export const HEADING = '### Legacy System capability coverage';
export const COLUMNS = Object.freeze(['Capability', 'Legacy anchor', 'Owner', 'Status', 'Native control', 'Test']);
export const STATUSES = Object.freeze(['covered', 'kept in legacy', 'approved removal']);
export const SECTIONS = Object.freeze(['overview', 'appearance', 'sources', 'integrations', 'tagging', 'maintenance', 'backups',
    'diagnostics']);
export const ROLES = Object.freeze(['button', 'combobox', 'switch', 'slider', 'spinbutton', 'textbox', 'group', 'status', 'term']);
// Where a Test cell's file is looked up, in order.
export const TEST_FOLDERS = Object.freeze(['tests/browser', 'tests/js', 'tests/vendor', 'tests']);
const NONE = '—';
/**
 * Covered rows whose replacement is not on a System section, so they name no native control (`—`), each with why. Every
 * other covered row must name one.
 */
export const CONTROL_EXEMPTIONS = Object.freeze({
    'Gallery per-image auto-tag / review': 'owned by the Images selection bar (Auto-tag selected), not a System section',
});

/** Split a Markdown table row into its cells (a `|` inside backticks is not a separator). */
function cells(line) {
    const out = [];
    let cell = '';
    let code = false;
    for (const char of line.trim().replace(/^\|/, '').replace(/\|$/, '')) {
        if (char === '`') code = !code;
        if (char === '|' && !code) {
            out.push(cell.trim());
            cell = '';
        } else {
            cell += char;
        }
    }
    out.push(cell.trim());
    return out;
}

/** The table's rows as objects keyed by COLUMNS, from the section that starts at HEADING. */
export function parseCoverageTable(markdown) {
    const lines = markdown.split('\n');
    const start = lines.findIndex(line => line.trim() === HEADING);
    if (start === -1) throw new Error(`TESTING.md has no "${HEADING}" section`);
    const header = lines.findIndex((line, index) => index > start && line.trim().startsWith('|'));
    if (header === -1 || lines.slice(start + 1, header).some(line => /^#{1,3} /.test(line))) {
        throw new Error('The coverage section has no table');
    }
    const columns = cells(lines[header]);
    if (columns.join('|') !== COLUMNS.join('|')) throw new Error(`The coverage table's columns are ${columns.join(', ')}`);
    const rows = [];
    for (let index = header + 2; index < lines.length && lines[index].trim().startsWith('|'); index += 1) {
        const values = cells(lines[index]);
        rows.push({ line: index + 1, ...Object.fromEntries(COLUMNS.map((column, at) => [column, values[at] ?? ''])) });
    }
    return rows;
}

/**
 * A Native control cell: `` `<section>` · <role> · "<name>" ``, optionally ` · in "<row title>"` when the name is shared
 * by several rows of the section → {section, role, name, row}; `—` → null; else it throws.
 */
export function parseControl(cell) {
    if (cell === NONE) return null;
    const match = /^`([a-z]+)` · ([a-z]+) · "([^"]+)"(?: · in "([^"]+)")?$/.exec(cell);
    if (!match) throw new Error(`malformed native control "${cell}"`);
    const [, section, role, name, row = null] = match;
    if (!SECTIONS.includes(section)) throw new Error(`unknown System section "${section}"`);
    if (!ROLES.includes(role)) throw new Error(`unknown role "${role}"`);
    return { section, role, name, row };
}

/** A Test cell: `<title> (<file>)` → {title, file}. */
export function parseTest(cell) {
    const match = /^(.+) \(([\w.-]+\.(?:mjs|js|py))\)$/.exec(cell);
    if (!match) throw new Error(`malformed test cell "${cell}"`);
    return { title: match[1], file: match[2] };
}

// Modifiers that still run the test; `.skip`, `.fixme` and `.fail` do not prove the capability.
const RUNS = new Set(['', 'only', 'slow']);
// After these characters (or at the start) a `/` opens a regular expression, not a division.
const REGEX_BEFORE = /[(,=:[!&|?{};+\-*%<>~^]$/;

/** Where the string, template literal or regular expression opened by `source[start]` ends (its closing index), or -1. */
function literalEnd(source, start) {
    const quote = source[start];
    let inClass = false;
    for (let end = start + 1; end < source.length; end += 1) {
        const at = source[end];
        if (at === '\\') end += 1;
        else if (quote === '/' && at === '[') inClass = true;
        else if (quote === '/' && at === ']') inClass = false;
        else if (at === quote && !inClass) return end;
        // A quoted string or a regular expression never spans lines.
        else if (at === '\n' && quote !== '`') return -1;
    }
    return -1;
}

/**
 * JavaScript `source` with every comment blanked (newlines kept), wherever it starts on a line. Strings, template
 * literals and regular expressions are skipped whole, so a `//` or `/*` inside them is not a comment.
 */
export function stripJsComments(source) {
    let out = '';
    let index = 0;
    while (index < source.length) {
        const char = source[index];
        const next = source[index + 1];
        if (char === '/' && next === '/') {
            while (index < source.length && source[index] !== '\n') index += 1;
            continue;
        }
        if (char === '/' && next === '*') {
            const close = source.indexOf('*/', index + 2);
            const stop = close === -1 ? source.length : close + 2;
            out += source.slice(index, stop).replace(/[^\n]/g, ' ');
            index = stop;
            continue;
        }
        const opens = char === "'" || char === '"' || char === '`' || (char === '/' && REGEX_BEFORE.test(out.trimEnd() || '('));
        const end = opens ? literalEnd(source, index) : -1;
        if (end === -1) {
            out += char;
            index += 1;
        } else {
            out += source.slice(index, end + 1);
            index = end + 1;
        }
    }
    return out;
}

/** Python `source` with its comments and triple-quoted strings (docstrings) blanked, newlines kept. */
export function stripPythonComments(source) {
    return source.replace(/("""|''')[\s\S]*?\1|#[^\n]*/g, text => text.replace(/[^\n]/g, ' '));
}

/**
 * The tests a source declares, title → whether it runs: a `test('…'` or `it('…'` call at a line's start (`.only` and
 * `.slow` run; `.skip`, `.fixme` and `.fail` do not) in JavaScript, a `def test_…` at function or method indentation in
 * Python. Comments, and Python docstrings, never count.
 */
export function declarations(source, file) {
    const found = new Map();
    const add = (title, runs) => found.set(title, found.get(title) || runs);
    if (file.endsWith('.py')) {
        for (const match of stripPythonComments(source).matchAll(/^(?: {4})?(?:async[ \t]+)?def[ \t]+(test_\w+)[ \t]*\(/gm)) {
            add(match[1], true);
        }
        return found;
    }
    const declaration = /^[ \t]*(?:test|it)(?:\.(only|skip|fixme|fail|slow))?\(\s*(['"`])((?:\\.|(?!\2)[^\\])*)\2/gm;
    for (const match of stripJsComments(source).matchAll(declaration)) add(match[3].replace(/\\(.)/g, '$1'), RUNS.has(match[1] ?? ''));
    return found;
}

/** The titles of the tests a source declares and runs. */
export function declaredTitles(source, file) {
    return new Set([...declarations(source, file)].filter(([, runs]) => runs).map(([title]) => title));
}

/** The repository path of a Test cell's file, or null. */
export function testFile(file, root = ROOT) {
    for (const folder of TEST_FOLDERS) {
        const path = join(root, folder, file);
        if (existsSync(path)) return path;
    }
    return null;
}

/** Every problem of one row (empty: the row is sound). `locate(file)` finds a test file's path, `read(path)` reads it. */
export function rowProblems(row, { root = ROOT, locate = file => testFile(file, root), read = path => readFileSync(path, 'utf8') } = {}) {
    const problems = [];
    if (!row.Capability) problems.push('no capability');
    if (!row['Legacy anchor']) problems.push('no legacy anchor');
    if (!row.Owner) problems.push('no owner');
    if (!STATUSES.includes(row.Status)) problems.push(`unknown status "${row.Status}"`);
    let control = null;
    try {
        control = parseControl(row['Native control']);
    } catch (error) {
        problems.push(error.message);
    }
    if (control && row.Status !== 'covered') problems.push(`a ${row.Status} row names a native control`);
    if (row.Status === 'covered' && row['Native control'] === NONE && !Object.hasOwn(CONTROL_EXEMPTIONS, row.Capability)) {
        problems.push('a covered row names no native control');
    }
    let test;
    try {
        test = parseTest(row.Test);
    } catch (error) {
        problems.push(error.message);
        return problems;
    }
    const path = locate(test.file);
    if (!path) problems.push(`no test file ${test.file}`);
    else {
        const runs = declarations(read(path), test.file).get(test.title);
        if (runs === undefined) problems.push(`${test.file} declares no test "${test.title}"`);
        else if (!runs) problems.push(`${test.file} only skips "${test.title}"`);
    }
    return problems;
}

/** The table read from TESTING.md. */
export function readCoverageTable(root = ROOT) {
    return parseCoverageTable(readFileSync(join(root, 'TESTING.md'), 'utf8'));
}
