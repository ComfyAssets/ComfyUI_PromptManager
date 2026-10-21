import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { baselineGaps, measuredSources, readBaseline } from '../../tools/vendor/coverage-baseline-check.mjs';

const floor = { lines: 80, branches: 80, functions: 80, statements: 80 };

test('baseline gaps name measured files without a baseline entry', () => {
    const gaps = baselineGaps(['a.js', 'nested/new.mjs'], { 'a.js': floor });
    assert.deepEqual(gaps.missing, ['nested/new.mjs']);
    assert.deepEqual(gaps.stale, []);
});

test('baseline gaps name baseline entries whose file no longer exists', () => {
    const gaps = baselineGaps(['a.js'], { 'a.js': floor, 'gone.js': floor });
    assert.deepEqual(gaps.missing, []);
    assert.deepEqual(gaps.stale, ['gone.js']);
});

test('baseline gaps are empty when files and baseline agree', () => {
    assert.deepEqual(baselineGaps(['a.js'], { 'a.js': floor }), { missing: [], stale: [] });
});

test('the committed baseline covers exactly the measured frontend sources', () => {
    const gaps = baselineGaps(measuredSources(), readBaseline());
    assert.deepEqual(gaps.missing, [], `Missing baseline (run coverage:update): ${gaps.missing.join(', ')}`);
    assert.deepEqual(gaps.stale, [], `Stale baseline entries (file removed): ${gaps.stale.join(', ')}`);
});

test('the real gate and this check select files through the same function', () => {
    const gate = readFileSync(new URL('../../tools/vendor/frontend-coverage.mjs', import.meta.url), 'utf8');
    assert.match(gate, /discoverSources\(sourceRoot\)/);
});
