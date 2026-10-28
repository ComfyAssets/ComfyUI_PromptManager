import assert from 'node:assert/strict';
import test from 'node:test';
import { COLUMNS, CONTROL_EXEMPTIONS, PAGES, REMOVAL_COLUMNS, STATUSES, declaredTitles, duplicateRemovals, idUses, methodUses,
    parseControl, parseCoverageTable, parseRemovalTable, readCoverageTable, readPage, readRemovalTable, removalProblems,
    rowProblems } from '../../tools/vendor/legacy-coverage.mjs';

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
    const titles = declaredTitles("test('a gate\\'s refusal', () => {});\ntest.only(`b`, () => {});\nit(\n    'c', () => {});",
        'x.test.js');
    assert.deepEqual([...titles], ["a gate's refusal", 'b', 'c']);
    // A block comment that starts after code on a line, and a `//` or `/*` inside a string or a regular expression.
    const trailing = [
        "const url = 'http://host/*'; /* test('clears the logs', () => {});",
        "test('clears the logs', () => {}); */",
        "const pattern = /['\"`]\\/\\*/;",
        "test('after the pattern', () => {});",
    ].join('\n');
    assert.deepEqual([...declaredTitles(trailing, 'x.spec.mjs')], ['after the pattern']);
    // Python: only a real `def test_…` counts, never a commented one.
    const python = ['# def test_report_lists_config_only_keys(self):', 'class T:', '    \"\"\"Example:', '',
        '    def test_in_a_docstring(self):', '    \"\"\"', '    def test_other(self):', '        def test_nested():',
        '            pass', 'def test_module():', '    pass', ''].join('\n');
    assert.deepEqual([...declaredTitles(python, 'test_x.py')], ['test_other', 'test_module']);
});

test('a malformed native control cell fails', () => {
    assert.deepEqual(parseControl('`diagnostics` · button · "Clear all logs…"'),
        { section: 'diagnostics', role: 'button', name: 'Clear all logs…', row: null });
    assert.deepEqual(parseControl('`maintenance` · button · "Review plan" · in "Duplicate prompt text"'),
        { section: 'maintenance', role: 'button', name: 'Review plan', row: 'Duplicate prompt text' });
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

test('a skipped test does not prove a capability', () => {
    for (const modifier of ['skip', 'fixme', 'fail']) {
        assert.deepEqual(rowProblems(ROW, declaring(`test.${modifier}('clears the logs', async () => {});`)),
            ['clear.spec.mjs only skips "clears the logs"'], modifier);
    }
    for (const modifier of ['only', 'slow']) {
        assert.deepEqual(rowProblems(ROW, declaring(`test.${modifier}('clears the logs', async () => {});`)), [], modifier);
    }
    // A running declaration beside a skipped one counts.
    assert.deepEqual(rowProblems(ROW, declaring("test.skip('clears the logs', () => {});\ntest('clears the logs', () => {});")), []);
});

test('a covered row names its native control unless it is exempt', () => {
    const source = declaring("test('clears the logs', () => {});");
    assert.deepEqual(rowProblems({ ...ROW, 'Native control': '—' }, source), ['a covered row names no native control']);
    for (const status of ['kept in legacy', 'approved removal']) {
        assert.deepEqual(rowProblems({ ...ROW, Status: status, 'Native control': '—' }, source), [], status);
    }
    const [exempt] = Object.keys(CONTROL_EXEMPTIONS);
    assert.deepEqual(rowProblems({ ...ROW, Capability: exempt, 'Native control': '—' }, source), []);
    // Every exemption has a reason and names a covered row of the table that has no control.
    const rows = readCoverageTable();
    for (const [capability, reason] of Object.entries(CONTROL_EXEMPTIONS)) {
        assert.ok(reason.length > 20, capability);
        const row = rows.find(item => item.Capability === capability);
        assert.ok(row, `${capability} is in the table`);
        assert.deepEqual([row.Status, row['Native control']], ['covered', '—']);
    }
});

// TESTING.md "Legacy System UI removed by S13" (#330): a legacy element is removed only when its capability is
// `covered`, the removal copies that row's native test, and the element is really gone from its page.
const COVERAGE_HEADER = '| Capability | Legacy anchor | Owner | Status | Native control | Test |\n| --- | --- | --- | --- | --- | --- |';
const REMOVAL_HEADER = '| Capability | Page | Removed ids | Removed methods | Native test |\n| --- | --- | --- | --- | --- |';
const FIXTURE_COVERAGE = parseCoverageTable(['### Legacy System capability coverage', '', COVERAGE_HEADER,
    '| View logs | `admin.html:1` | S12 Logs | covered | `diagnostics` · combobox · "Level" | level filter works (logs.spec.mjs) |',
    '| Infinite scrolling default | `admin.html:2` | Legacy prompt list | kept in legacy | — | scrolls (list-state.test.js) |',
    ''].join('\n'));
const removalRow = cells => parseRemovalTable(['### Legacy System UI removed by S13', '', REMOVAL_HEADER, `| ${cells} |`, '']
    .join('\n'))[0];
const EMPTY_PAGE = () => ({ html: '<button id="kept">Kept</button>', script: 'class Admin {\n    kept() {}\n}\n' });

test('every removed legacy element cites a covered capability and its native test', () => {
    const coverage = readCoverageTable();
    const rows = readRemovalTable();
    for (const row of rows) {
        const cited = coverage.find(item => item.Capability === row.Capability);
        assert.ok(cited, `TESTING.md:${row.line} cites "${row.Capability}"`);
        assert.equal(cited.Status, 'covered', `TESTING.md:${row.line} ${row.Capability}`);
        assert.equal(row['Native test'], cited.Test, `TESTING.md:${row.line} ${row.Capability}`);
        assert.ok(Object.keys(PAGES).includes(row.Page), `TESTING.md:${row.line} page ${row.Page}`);
    }
    assert.deepEqual(duplicateRemovals(rows), []);
    assert.deepEqual(REMOVAL_COLUMNS, ['Capability', 'Page', 'Removed ids', 'Removed methods', 'Native test']);
});

test('a removed id or method is gone from its page', () => {
    const coverage = readCoverageTable();
    for (const row of readRemovalTable()) {
        assert.deepEqual(removalProblems(row, coverage, page => readPage(page)), [], `TESTING.md:${row.line} ${row.Capability}`);
    }
    // Each way a page can still hold an id or a method is found; a call through another object, or a longer name, is not
    // a definition.
    const page = {
        html: '<div id="logsModal"></div><button onclick="window.admin.closeLogs()">x</button>',
        script: [
            "document.getElementById('logLevel').value;",
            "this.showModal('diagnosticsModal');",
            '<span data-id="logsContainer"></span>',
            'document.querySelector("#logLimit option");',
            "const backdrops = ['settingsModal', 'duplicatesModal'];",
            'class Admin {',
            '    async refreshLogs(level) {',
            '    }',
            '}            displayLogs(logs) {',
            '    }',
            'this.loadLogStats();',
            '// downloadLogs() {',
            'other.formatBytesLater(1);',
        ].join('\n'),
    };
    assert.deepEqual(idUses('logsModal', page), ['id="logsModal" in the markup']);
    assert.deepEqual(idUses('logLevel', page), ["getElementById('logLevel')"]);
    assert.deepEqual(idUses('logLimit', page), ["querySelector('#logLimit')"]);
    assert.deepEqual(idUses('duplicatesModal', page), ["'duplicatesModal' in a modal list"]);
    assert.deepEqual(idUses('logLevelX', page), []);
    assert.deepEqual(idUses('diagnosticsModal', page), ["a …Modal('diagnosticsModal') call"]);
    assert.deepEqual(idUses('logsContainer', page), []);
    assert.deepEqual(methodUses('closeLogs', page), ['.closeLogs( called in the markup']);
    assert.deepEqual(methodUses('refreshLogs', page), ['refreshLogs() defined']);
    assert.deepEqual(methodUses('displayLogs', page), ['displayLogs() defined']);
    assert.deepEqual(methodUses('loadLogStats', page), ['.loadLogStats( called']);
    assert.deepEqual(methodUses('downloadLogs', page), []);
    assert.deepEqual(methodUses('formatBytes', page), []);
    // One id or method is listed on one row per page.
    const twice = [removalRow('View logs | admin | `#logsModal` | — | level filter works (logs.spec.mjs)'),
        { ...removalRow('View logs | admin | `#logsModal` | — | level filter works (logs.spec.mjs)'), line: 9 }];
    assert.match(duplicateRemovals(twice).join(), /admin #logsModal is listed on rows/);
    assert.deepEqual(removalProblems(removalRow('View logs | admin | `logsModal`, `#ok` | `close` | level filter works (logs.spec.mjs)'),
        FIXTURE_COVERAGE, EMPTY_PAGE), ['malformed entry "logsModal"', 'malformed entry "close"']);
});

test('a removal citing a kept-in-legacy capability fails', () => {
    const row = removalRow('Infinite scrolling default | admin | `#infiniteScrollDefault` | — | scrolls (list-state.test.js)');
    assert.deepEqual(removalProblems(row, FIXTURE_COVERAGE, EMPTY_PAGE), ['cites a kept in legacy capability']);
    const unknown = removalRow('Something else | admin | — | — | scrolls (list-state.test.js)');
    assert.deepEqual(removalProblems(unknown, FIXTURE_COVERAGE, EMPTY_PAGE), ['cites no single coverage row "Something else"']);
});

test('a removal whose native test differs fails', () => {
    const row = removalRow('View logs | admin | `#logsModal` | `showLogsModal()` | another title (logs.spec.mjs)');
    assert.deepEqual(removalProblems(row, FIXTURE_COVERAGE, EMPTY_PAGE),
        ['its native test differs from the coverage row\'s "level filter works (logs.spec.mjs)"']);
    assert.deepEqual(removalProblems({ ...row, 'Native test': 'level filter works (logs.spec.mjs)' }, FIXTURE_COVERAGE, EMPTY_PAGE), []);
});

test('a removed id still on its page fails', () => {
    const row = removalRow('View logs | admin | `#logsModal` | `showLogsModal()` | level filter works (logs.spec.mjs)');
    const page = () => ({ html: '<div id="logsModal" class="hidden"></div>', script: 'class Admin {\n    showLogsModal() {\n    }\n}\n' });
    assert.deepEqual(removalProblems(row, FIXTURE_COVERAGE, page), [
        '#logsModal is still on the admin page: id="logsModal" in the markup',
        'showLogsModal() is still on the admin page: showLogsModal() defined',
    ]);
    assert.deepEqual(removalProblems({ ...row, Page: 'metadata' }, FIXTURE_COVERAGE, page), ['unknown page "metadata"']);
});
