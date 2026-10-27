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

/** A Native control cell: `` `<section>` · <role> · "<name>" `` → {section, role, name}; `—` → null; else it throws. */
export function parseControl(cell) {
    if (cell === NONE) return null;
    const match = /^`([a-z]+)` · ([a-z]+) · "([^"]+)"$/.exec(cell);
    if (!match) throw new Error(`malformed native control "${cell}"`);
    const [, section, role, name] = match;
    if (!SECTIONS.includes(section)) throw new Error(`unknown System section "${section}"`);
    if (!ROLES.includes(role)) throw new Error(`unknown role "${role}"`);
    return { section, role, name };
}

/** A Test cell: `<title> (<file>)` → {title, file}. */
export function parseTest(cell) {
    const match = /^(.+) \(([\w.-]+\.(?:mjs|js|py))\)$/.exec(cell);
    if (!match) throw new Error(`malformed test cell "${cell}"`);
    return { title: match[1], file: match[2] };
}

/**
 * The titles a source declares: a line that starts with `test('…'` or `it('…'` (with `.only`, `.skip`, `.fixme`,
 * `.fail` or `.slow`) in JavaScript, `def test_…` in Python. Comments never count: a JavaScript line inside a block
 * comment or starting with `//` or `*`, and a Python line starting with `#`.
 */
export function declaredTitles(source, file) {
    if (file.endsWith('.py')) {
        return new Set([...source.matchAll(/^[ \t]*(?:async[ \t]+)?def[ \t]+(test_\w+)[ \t]*\(/gm)].map(match => match[1]));
    }
    let comment = false;
    const code = source.split('\n').map(line => {
        const text = line.trim();
        if (comment) {
            if (text.includes('*/')) comment = false;
            return '';
        }
        if (text.startsWith('/*')) {
            comment = !text.includes('*/');
            return '';
        }
        return text.startsWith('//') || text.startsWith('*') ? '' : line;
    }).join('\n');
    const declaration = /^[ \t]*(?:test|it)(?:\.(?:only|skip|fixme|fail|slow))?\(\s*(['"`])((?:\\.|(?!\1)[^\\])*)\1/gm;
    return new Set([...code.matchAll(declaration)].map(match => match[2].replace(/\\(.)/g, '$1')));
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
    let test;
    try {
        test = parseTest(row.Test);
    } catch (error) {
        problems.push(error.message);
        return problems;
    }
    const path = locate(test.file);
    if (!path) problems.push(`no test file ${test.file}`);
    else if (!declaredTitles(read(path), test.file).has(test.title)) problems.push(`${test.file} declares no test "${test.title}"`);
    return problems;
}

/** The table read from TESTING.md. */
export function readCoverageTable(root = ROOT) {
    return parseCoverageTable(readFileSync(join(root, 'TESTING.md'), 'utf8'));
}
