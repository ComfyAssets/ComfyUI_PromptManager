// The flake policy (#666, TESTING.md "Flaky tests and quarantine"): the gate never retries and skips
// tests tagged @quarantine; the nightly run retries once, runs the quarantined tests and lists what
// was flaky in the job summary.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
import FlakyTestsReporter from '../../tools/vendor/flaky-tests-reporter.mjs';
import { GATE_PROJECTS, QUARANTINE_TAG } from '../../tools/vendor/frontend-coverage.mjs';
import viewerConfig from '../../tools/vendor/playwright.config.mjs';
import workflowConfig from '../../tools/vendor/playwright.workflows.config.mjs';

const root = fileURLToPath(new URL('../../', import.meta.url));
const read = path => readFileSync(join(root, path), 'utf8');

test('the gate runs desktop Chromium only and no gate config retries', () => {
    assert.deepEqual(GATE_PROJECTS, ['chromium']);
    assert.equal(QUARANTINE_TAG, '@quarantine');
    assert.equal(workflowConfig.retries, 0, 'a retry hides a race');
    assert.equal(viewerConfig.retries, 0, 'the viewer suite runs in the gate too');
    assert.doesNotMatch(read('.github/workflows/ci.yml'), /--retries/, 'no gate step turns retries on');
});

test('the nightly page workflows retry once and run every project the gate leaves out', () => {
    const nightly = read('.github/workflows/nightly.yml');
    assert.match(nightly, /^ {8}browser: \[firefox, webkit, touch\]$/m);
    assert.match(nightly, /test:workflows -- --project \$\{\{ matrix\.browser \}\} --shard \$\{\{ matrix\.shard \}\}\/3 --retries 1$/m);
    assert.doesNotMatch(nightly, /grep-invert/, 'quarantined tests still run nightly');
    const names = workflowConfig.projects.map(project => project.name).sort();
    assert.deepEqual(names, [...GATE_PROJECTS, 'firefox', 'touch', 'webkit'].sort(), 'every project runs in the gate or nightly');
});

// A quarantined test names the issue that tracks it, in one fixed spelling, so the tag is never a
// way to forget a test: test('title', { tag: '@quarantine', annotation: { type: 'issue', description: '#613' } }, ...).
const QUARANTINED = /\{ tag: '@quarantine', annotation: \{ type: 'issue', description: '#\d+' \} \}/g;

test('every @quarantine tag carries its issue number', () => {
    const directory = join(root, 'tests/browser');
    for (const file of readdirSync(directory).filter(name => name.endsWith('.mjs'))) {
        const text = readFileSync(join(directory, file), 'utf8');
        const tags = text.match(/@quarantine/g)?.length ?? 0;
        const complete = text.match(QUARANTINED)?.length ?? 0;
        assert.equal(complete, tags, `${file}: each @quarantine is written as { tag: '@quarantine', annotation: { type: 'issue', description: '#N' } }`);
    }
    assert.match("test('t', { tag: '@quarantine', annotation: { type: 'issue', description: '#613' } }, async () => {});", QUARANTINED);
    assert.doesNotMatch("test('t', { tag: '@quarantine' }, async () => {});", QUARANTINED);
});

function fakeTest(title, outcome, project = 'webkit') {
    return { title, outcome: () => outcome, location: { file: '/repo/tests/browser/a.spec.mjs', line: 7 },
        parent: { project: () => ({ name: project }) } };
}

function summaryOf(tests, env = { GITHUB_STEP_SUMMARY: '/tmp/summary.md' }) {
    const written = [];
    const reporter = new FlakyTestsReporter({ env, append: (file, text) => written.push([file, text]) });
    assert.equal(reporter.printsToStdio(), false);
    reporter.onBegin({ rootDir: '/repo/tests/browser' });
    for (const item of tests) reporter.onTestEnd(item);
    reporter.onEnd();
    return written;
}

test('tests that passed only on the retry are listed once in the job summary', () => {
    const flaky = fakeTest('closes `fast`', 'flaky');
    const written = summaryOf([fakeTest('fine', 'expected'), flaky, flaky, fakeTest('broken', 'unexpected'),
        fakeTest('another', 'flaky', 'touch')]);
    assert.deepEqual(written, [['/tmp/summary.md',
        '### Flaky tests (failed, then passed on the retry): 2\n\n'
        + "- `[touch] › a.spec.mjs:7 › another`\n- `[webkit] › a.spec.mjs:7 › closes 'fast'`\n\n"]]);
});

test('no flaky test, or no job summary, writes nothing', () => {
    assert.deepEqual(summaryOf([fakeTest('fine', 'expected'), fakeTest('broken', 'unexpected')]), []);
    assert.deepEqual(summaryOf([fakeTest('wobbly', 'flaky')], {}), []);
});
