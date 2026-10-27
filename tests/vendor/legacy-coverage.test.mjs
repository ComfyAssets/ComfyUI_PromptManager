import assert from 'node:assert/strict';
import test from 'node:test';
import { COLUMNS, STATUSES, declaredTitles, parseControl, parseCoverageTable, readCoverageTable, rowProblems }
    from '../../tools/vendor/legacy-coverage.mjs';

// TESTING.md "Legacy System capability coverage" (#249 System S12, #329): every legacy System capability names an
// owner, a known status, a well-formed native control and a test its file really declares.

const ROW = Object.freeze({ Capability: 'Clear/truncate logs', 'Legacy anchor': '`admin.html:915`', Owner: 'S12 Logs',
    Status: 'covered', 'Native control': '`diagnostics` · button · "Clear all logs…"', Test: 'clears the logs (clear.spec.mjs)' });
const declaring = source => ({ locate: file => `tests/browser/${file}`, read: () => source });

test('every coverage row has an owner, a known status and a declared test', () => {
    const rows = readCoverageTable();
    assert.ok(rows.length >= 54, `the table lists ${rows.length} capabilities`);
    for (const row of rows) {
        assert.deepEqual(rowProblems(row), [], `TESTING.md:${row.line} ${row.Capability}`);
        assert.ok(row.Owner.trim());
        assert.ok(STATUSES.includes(row.Status));
    }
    assert.ok(rows.some(row => row.Status === 'kept in legacy') && rows.some(row => row.Status === 'approved removal'));
    // A broken row fails: an empty owner, an unknown status, a test its file does not declare.
    const [first] = rows;
    assert.deepEqual(rowProblems({ ...first, Owner: '' }), ['no owner']);
    assert.ok(rowProblems({ ...first, Status: 'done' }).includes('unknown status "done"'));
    assert.match(rowProblems({ ...first, Test: first.Test.replace(/^(.+) \(/, '$1 again (') }).join(), /declares no test/);
    assert.match(rowProblems({ ...first, Test: 'some title (missing.spec.mjs)' }).join(), /no test file missing\.spec\.mjs/);
});

test('a title that appears only in a comment does not count', () => {
    const commented = [
        "// test('clears the logs', async () => {});",
        "/* test('clears the logs', async () => {}); */",
        '/**',
        " * it('clears the logs')",
        ' */',
        "const title = 'clears the logs';",
        "test.describe('clears the logs', () => {});",
    ].join('\n');
    assert.equal(declaredTitles(commented, 'clear.spec.mjs').has('clears the logs'), false);
    assert.deepEqual(rowProblems(ROW, declaring(commented)), ['clear.spec.mjs declares no test "clears the logs"']);
    const declared = `${commented}\nfor (const theme of THEMES) {\n    test('clears the logs', async ({ page }) => {});\n}\n`;
    assert.deepEqual(rowProblems(ROW, declaring(declared)), []);
    // Escaped quotes, template literals, modifiers and a title on the next line are declarations too.
    const titles = declaredTitles("test('a gate\\'s refusal', () => {});\ntest.skip(`b`, () => {});\nit(\n    'c', () => {});",
        'x.test.js');
    assert.deepEqual([...titles], ["a gate's refusal", 'b', 'c']);
    // Python: only a real `def test_…` counts, never a commented one.
    const python = '# def test_report_lists_config_only_keys(self):\nclass T:\n    def test_other(self):\n        pass\n';
    assert.deepEqual([...declaredTitles(python, 'test_x.py')], ['test_other']);
});

test('a malformed native control cell fails', () => {
    assert.deepEqual(parseControl('`diagnostics` · button · "Clear all logs…"'),
        { section: 'diagnostics', role: 'button', name: 'Clear all logs…' });
    assert.equal(parseControl('—'), null);
    for (const [cell, message] of [
        ['diagnostics · button · "Clear all logs…"', /malformed/],
        ['`diagnostics` · button · Clear all logs…', /malformed/],
        ['`diagnostics` - button - "Clear all logs…"', /malformed/],
        ['', /malformed/],
        ['`logs` · button · "Clear all logs…"', /unknown System section "logs"/],
        ['`diagnostics` · link · "Clear all logs…"', /unknown role "link"/],
    ]) {
        assert.throws(() => parseControl(cell), message, cell);
        assert.match(rowProblems({ ...ROW, 'Native control': cell }, declaring("test('clears the logs', () => {});")).join(), message);
    }
    // Only a covered row names a control.
    assert.deepEqual(rowProblems({ ...ROW, Status: 'kept in legacy' }, declaring("test('clears the logs', () => {});")),
        ['a kept in legacy row names a native control']);
    // The table itself must keep its six columns.
    assert.throws(() => parseCoverageTable(`### Legacy System capability coverage\n\n| Capability | Owner |\n| --- | --- |\n`), /columns/);
    assert.deepEqual(COLUMNS, ['Capability', 'Legacy anchor', 'Owner', 'Status', 'Native control', 'Test']);
});
