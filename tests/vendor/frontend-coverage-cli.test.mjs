import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { plan, runCoverage } from '../../tools/vendor/frontend-coverage.mjs';

const steps = argv => plan(argv).map(({ step, ...options }) => (Object.keys(options).length ? { step, ...options } : step));

function tempPaths(t) {
    const root = mkdtempSync(join(tmpdir(), 'pm-coverage-cli-'));
    t.after(() => rmSync(root, { recursive: true, force: true }));
    const reportRoot = join(root, 'coverage/js');
    return { root, paths: { repositoryRoot: root, tools: join(root, 'tools'), reportRoot, raw: join(reportRoot, 'raw'), sourceRoot: join(root, 'web/js') } };
}

function recorder() {
    const calls = [];
    const run = (args, env = {}) => { calls.push({ args, env }); };
    return { calls, run };
}

test('no arguments plans the unchanged full run', () => {
    assert.deepEqual(steps([]), [
        'clean', 'node-units', 'viewer', { step: 'workflows', shard: null }, 'report', { step: 'check', updateBaseline: false },
    ]);
    assert.deepEqual(steps(['--update-baseline']), [
        'clean', 'node-units', 'viewer', { step: 'workflows', shard: null }, 'report', { step: 'check', updateBaseline: true },
    ]);
});

test('--shard 2/3 plans only workflows with --shard 2/3 and no report', () => {
    assert.deepEqual(steps(['--shard', '2/3']), ['prepare', { step: 'workflows', shard: '2/3' }]);
    assert.deepEqual(steps(['--shard=3/3']), ['prepare', { step: 'workflows', shard: '3/3' }]);
});

test('--shard 1/N also runs the node units and the viewer suite', () => {
    assert.deepEqual(steps(['--shard', '1/3']), ['prepare', 'node-units', 'viewer', { step: 'workflows', shard: '1/3' }]);
    assert.deepEqual(steps(['--shard', '1/1']), ['prepare', 'node-units', 'viewer', { step: 'workflows', shard: '1/1' }]);
});

test('--merge-only plans the report and the check over existing raw coverage', () => {
    assert.deepEqual(steps(['--merge-only']), ['require-raw', 'report', { step: 'check', updateBaseline: false }]);
    assert.deepEqual(steps(['--merge-only', '--update-baseline']), ['require-raw', 'report', { step: 'check', updateBaseline: true }]);
});

test('invalid shard and mode combinations are refused', () => {
    for (const argv of [['--shard'], ['--shard', '0/3'], ['--shard', '4/3'], ['--shard', '2'], ['--shard', 'a/b'], ['--shard', '1/0'], ['--shard', '1.5/3']]) {
        assert.throws(() => plan(argv), /--shard needs I\/N/, argv.join(' '));
    }
    assert.throws(() => plan(['--shard', '1/2', '--merge-only']), /either --shard or --merge-only/);
    assert.throws(() => plan(['--shard', '1/2', '--update-baseline']), /--update-baseline .* --merge-only or a full run/);
    assert.throws(() => plan(['--shard', '1/2', '--shard', '2/2']), /--shard once/);
    assert.throws(() => plan(['--report']), /Unknown argument: --report/);
});

test('--merge-only refuses an empty raw dir', async t => {
    const { paths } = tempPaths(t);
    const { calls, run } = recorder();
    await assert.rejects(runCoverage({ argv: ['--merge-only'], run, paths }), /no raw coverage .*coverage\/js\/raw.* run the --shard steps first/i);
    mkdirSync(paths.raw, { recursive: true });
    await assert.rejects(runCoverage({ argv: ['--merge-only'], run, paths }), /no raw coverage/i);
    writeFileSync(join(paths.raw, 'notes.txt'), 'not coverage');
    await assert.rejects(runCoverage({ argv: ['--merge-only'], run, paths }), /no raw coverage/i);
    assert.deepEqual(calls, [], 'nothing is reported or checked over partial or empty coverage');
});

test('a shard keeps raw coverage other shards wrote and passes its shard to Playwright', async t => {
    const { paths } = tempPaths(t);
    mkdirSync(paths.raw, { recursive: true });
    const earlier = join(paths.raw, 'browser-earlier-shard.json');
    writeFileSync(earlier, '{"result":[]}');
    const { calls, run } = recorder();
    await runCoverage({ argv: ['--shard', '2/3'], run, paths });
    assert.ok(existsSync(earlier), 'raw files from another shard survive');
    assert.equal(calls.length, 1, 'shard 2 runs only the workflows');
    const [{ args, env }] = calls;
    assert.deepEqual(args.slice(-6), ['--project', 'chromium', '--project', 'touch', '--shard', '2/3']);
    assert.equal(env.PM_JS_COVERAGE, paths.raw);
});

test('shard 1 runs the node units into the shared raw dir, then the viewer suite, then its workflows', async t => {
    const { root, paths } = tempPaths(t);
    mkdirSync(join(root, 'tests/js'), { recursive: true });
    writeFileSync(join(root, 'tests/js/a.test.js'), '');
    const { calls, run } = recorder();
    await runCoverage({ argv: ['--shard', '1/3'], run, paths });
    assert.equal(calls.length, 3);
    assert.deepEqual(calls[0].args, ['--test', 'tests/js/a.test.js']);
    assert.equal(calls[0].env.NODE_V8_COVERAGE, paths.raw);
    assert.ok(calls[1].args.includes(join(paths.tools, 'playwright.config.mjs')));
    assert.deepEqual(calls[2].args.slice(-2), ['--shard', '1/3']);
});
