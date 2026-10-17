import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
const sourceRoot = join(process.cwd(), 'web/js');
import { checkCoverage, discoverSources } from '../../tools/vendor/frontend-coverage.mjs';
const metrics = value => Object.fromEntries(['lines', 'branches', 'functions', 'statements'].map(key => [key, { pct: value }]));
const summary = { total: metrics(90), [join(sourceRoot, 'a.js')]: metrics(85) };
const baseline = { 'a.js': { lines: 80, branches: 80, functions: 80, statements: 80 } };
test('coverage gate accepts measured files at or above their reviewed floor', () => {
    assert.deepEqual(checkCoverage(summary, baseline, sourceRoot, ['a.js']), []);
});
test('coverage gate rejects a regression even when the aggregate percentage is high', () => {
    assert.match(checkCoverage({ ...summary, [join(sourceRoot, 'a.js')]: metrics(70) }, baseline, sourceRoot, ['a.js']).join('\n'), /a.js.*lines/);
});
test('coverage gate rejects absent source files and unreviewed new files', () => {
    assert.match(checkCoverage({ total: metrics(100) }, baseline, sourceRoot, ['a.js']).join('\n'), /missing.*a.js/i);
    assert.match(checkCoverage(summary, baseline, sourceRoot, ['a.js', 'new.js']).join('\n'), /baseline.*new.js/i);
});
test('coverage gate rejects invalid baselines and missing metrics', () => {
    assert.throws(() => checkCoverage(summary, { 'a.js': { lines: -1 } }, sourceRoot, ['a.js']), /baseline/i);
    assert.match(checkCoverage({ total: metrics(100), [join(sourceRoot, 'a.js')]: {} }, baseline, sourceRoot, ['a.js']).join('\n'), /lines/);
});

test('coverage discovers nested JS and MJS while excluding non-source files', t => {
    const root = mkdtempSync(join(tmpdir(), 'pm-coverage-'));
    t.after(() => rmSync(root, { recursive: true, force: true }));
    mkdirSync(join(root, 'shared/controls'), { recursive: true });
    for (const file of ['entry.js', 'shared/core.mjs', 'shared/controls/button.js', 'shared/theme.css', 'shared/notes.txt']) writeFileSync(join(root, file), '');
    assert.deepEqual(discoverSources(root), ['entry.js', 'shared/controls/button.js', 'shared/core.mjs']);
    assert.match(checkCoverage({}, {}, root, discoverSources(root)).join('\n'), /baseline.*shared\/controls\/button.js/i);
});
