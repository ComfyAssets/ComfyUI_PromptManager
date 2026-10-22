// Structural checks on .github/workflows/viewerjs-check.yml. The tooling has no YAML parser, so these
// read the workflow's fixed two-space layout: jobs at two spaces, their keys at four, steps at six.
import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtemp, mkdir, readdir, rm, writeFile } from 'node:fs/promises';
import { readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const workflow = readFileSync(new URL('../../.github/workflows/viewerjs-check.yml', import.meta.url), 'utf8');
const PINNED_IMAGE = /^\s+image: mcr\.microsoft\.com\/playwright:v\d+\.\d+\.\d+-noble$/m;

function jobs(text) {
    const lines = text.split('\n');
    const start = lines.indexOf('jobs:');
    assert.notEqual(start, -1, 'workflow has a jobs section');
    const found = new Map();
    let current = null;
    for (const line of lines.slice(start + 1)) {
        const header = /^ {2}([\w-]+):\s*$/.exec(line);
        if (header) {
            current = header[1];
            found.set(current, []);
        } else if (current && (line.startsWith('    ') || line.trim() === '' || /^ {2}#/.test(line))) {
            found.get(current).push(line);
        } else if (current && /^\S/.test(line)) {
            break;
        }
    }
    return new Map([...found].map(([id, body]) => [id, body.join('\n')]));
}

function steps(jobText) {
    const parts = jobText.split(/^(?= {6}- )/m);
    return parts.slice(1);
}

function runScript(stepText) {
    const lines = stepText.split('\n');
    const index = lines.findIndex(line => /^ {8}run: \|\s*$/.test(line));
    assert.notEqual(index, -1, 'step has a block run script');
    const body = [];
    for (const line of lines.slice(index + 1)) {
        if (line.trim() !== '' && !line.startsWith('          ')) break;
        body.push(line.slice(10));
    }
    return body.join('\n');
}

function needsOf(jobText) {
    const match = /^ {4}needs: \[([^\]]*)\]\s*$/m.exec(jobText) ?? /^ {4}needs: ([\w-]+)\s*$/m.exec(jobText);
    assert.ok(match, 'job declares needs');
    return match[1].split(',').map(value => value.trim()).filter(Boolean);
}

function matrixList(jobText, key) {
    const match = new RegExp(`^ {8}${key}: \\[([^\\]]*)\\]\\s*$`, 'm').exec(jobText);
    assert.ok(match, `job has a ${key} matrix`);
    return match[1].split(',').map(value => value.trim());
}

const allJobs = jobs(workflow);
const browserJobIds = [...allJobs].filter(([, body]) => /npm run --prefix tools\/vendor test:(workflows|coverage)/.test(body))
    .map(([id]) => id);
const mergeJob = () => allJobs.get('frontend-coverage-merge');
const verifyStep = () => steps(mergeJob()).find(step => /name: Verify every shard's raw coverage arrived/.test(step));

test('viewerjs keeps its name and needs every frontend job', () => {
    const viewerjs = allJobs.get('viewerjs');
    assert.ok(viewerjs, 'the required context is the job id viewerjs');
    assert.doesNotMatch(viewerjs, /^ {4}name:/m, 'no display name may replace the context "viewerjs"');
    const needs = needsOf(viewerjs);
    const others = [...allJobs.keys()].filter(id => id !== 'viewerjs');
    assert.deepEqual([...needs].sort(), [...others].sort(), 'viewerjs needs every other job in the workflow');
    assert.deepEqual([...needs].sort(), ['browser-workflows', 'frontend-coverage', 'frontend-coverage-merge']);
    assert.match(viewerjs, /^ {4}if: always\(\)$/m, 'viewerjs runs (and fails) when a needed job failed or was skipped');
    const script = runScript(steps(viewerjs).find(step => /run: \|/.test(step)));
    for (const id of needs) {
        const variable = new RegExp(`^ {10}([A-Z_]+): \\$\\{\\{ needs\\.${id}\\.result \\}\\}$`, 'm').exec(viewerjs);
        assert.ok(variable, `${id}'s result reaches the check`);
        assert.match(script, new RegExp(`test "\\$${variable[1]}" = success`), `${id} must be exactly success`);
    }
});

test('viewerjs fails unless every needed job succeeded', () => {
    const viewerjs = allJobs.get('viewerjs');
    const script = runScript(steps(viewerjs).find(step => /run: \|/.test(step)));
    const variables = [...viewerjs.matchAll(/^ {10}([A-Z_]+): \$\{\{ needs\.[\w-]+\.result \}\}$/gm)].map(match => match[1]);
    const run = env => {
        try {
            execFileSync('bash', ['--noprofile', '--norc', '-eo', 'pipefail', '-c', script], { env: { PATH: process.env.PATH, ...env }, stdio: 'pipe' });
            return true;
        } catch {
            return false;
        }
    };
    const allSuccess = Object.fromEntries(variables.map(name => [name, 'success']));
    assert.equal(run(allSuccess), true);
    for (const name of variables) {
        for (const result of ['failure', 'cancelled', 'skipped']) {
            assert.equal(run({ ...allSuccess, [name]: result }), false, `${name}=${result} must fail viewerjs`);
        }
    }
});

test('coverage runs as three shards that each upload their raw coverage', () => {
    const coverage = allJobs.get('frontend-coverage');
    assert.deepEqual(matrixList(coverage, 'shard'), ['1', '2', '3']);
    assert.match(coverage, /^ {4}name: frontend coverage \(shard \$\{\{ matrix\.shard \}\}\/3\)$/m);
    assert.match(coverage, /npm run --prefix tools\/vendor test:coverage:shard -- \$\{\{ matrix\.shard \}\}\/3/);
    const upload = steps(coverage).find(step => /name: coverage-raw-\$\{\{ matrix\.shard \}\}/.test(step));
    assert.ok(upload, 'each shard uploads coverage-raw-N');
    assert.match(upload, /^ {10}path: coverage\/js\/raw\/?$/m);
    assert.match(upload, /^ {10}retention-days: 3$/m);
    assert.match(upload, /^ {10}if-no-files-found: error$/m);
});

test('merge job downloads every shard and runs the merge-only report and check', () => {
    const merge = mergeJob();
    assert.ok(merge, 'frontend-coverage-merge exists');
    assert.deepEqual(needsOf(merge), ['frontend-coverage']);
    assert.match(merge, /^ {4}name: frontend coverage \(merge\)$/m);
    const download = steps(merge).find(step => /uses: actions\/download-artifact@/.test(step));
    assert.ok(download, 'merge downloads the shard artifacts');
    assert.match(download, /^ {10}pattern: coverage-raw-\*$/m);
    assert.doesNotMatch(download, /merge-multiple: true/, 'each artifact keeps its own directory so the check can count them');
    assert.ok(verifyStep(), 'merge has an explicit artifact count check');
    assert.match(verifyStep(), new RegExp(`^ {10}EXPECTED_SHARDS: ${matrixList(allJobs.get('frontend-coverage'), 'shard').length}$`, 'm'),
        'the expected count matches the shard matrix');
    const order = steps(merge).map(step => (/name: (.*)/.exec(step) ?? [, step.trim().split('\n')[0]])[1]);
    const verifyAt = order.findIndex(name => /Verify every shard/.test(name));
    const mergeAt = steps(merge).findIndex(step => /npm run --prefix tools\/vendor coverage:merge/.test(step));
    assert.ok(verifyAt >= 0 && mergeAt > verifyAt, 'the count check runs before coverage:merge');
    const report = steps(merge).find(step => /name: frontend-test-reports$/m.test(step));
    assert.ok(report, 'merge uploads the HTML report as frontend-test-reports');
    assert.match(report, /!coverage\/js\/raw\/\*\*/);
});

async function stagedShards(root, shards) {
    const staged = join(root, 'staged');
    for (const [shard, files] of shards) {
        const directory = join(staged, `coverage-raw-${shard}`);
        await mkdir(directory, { recursive: true });
        for (const file of files) await writeFile(join(directory, file), '{"result":[]}');
    }
    await mkdir(staged, { recursive: true });
    return staged;
}

async function verify(t, shards) {
    const root = await mkdtemp(join(tmpdir(), 'pm-ci-merge-'));
    t.after(() => rm(root, { recursive: true, force: true }));
    const staged = await stagedShards(root, shards);
    try {
        execFileSync('bash', ['--noprofile', '--norc', '-eo', 'pipefail', '-c', runScript(verifyStep())], {
            cwd: root, stdio: 'pipe',
            env: { PATH: process.env.PATH, STAGED_RAW: staged, EXPECTED_SHARDS: '3' },
        });
    } catch (error) {
        return { ok: false, output: `${error.stdout}${error.stderr}` };
    }
    return { ok: true, raw: (await readdir(join(root, 'coverage/js/raw'))).sort() };
}

test('merge refuses when a shard\'s raw coverage is missing', async t => {
    const missing = await verify(t, [[1, ['a.json']], [3, ['c.json']]]);
    assert.equal(missing.ok, false);
    assert.match(missing.output, /2 of 3/);
    const empty = await verify(t, [[1, ['a.json']], [2, []], [3, ['c.json']]]);
    assert.equal(empty.ok, false, 'an artifact without raw files counts as missing');
    const collision = await verify(t, [[1, ['a.json']], [2, ['a.json']], [3, ['c.json']]]);
    assert.equal(collision.ok, false, 'two shards writing the same file name must not silently drop one');
    const complete = await verify(t, [[1, ['a.json', 'node.json']], [2, ['b.json']], [3, ['c.json']]]);
    assert.deepEqual(complete, { ok: true, raw: ['a.json', 'b.json', 'c.json', 'node.json'] });
});

test('every browser job sets PM_TEST_WORKERS and runs in the pinned image', () => {
    assert.deepEqual([...browserJobIds].sort(), ['browser-workflows', 'frontend-coverage']);
    for (const id of browserJobIds) {
        const body = allJobs.get(id);
        assert.match(body, /^ {6}PM_TEST_WORKERS: 3$/m, `${id} sets PM_TEST_WORKERS for the whole job`);
        assert.match(body, PINNED_IMAGE, `${id} keeps the pinned Playwright image`);
        assert.match(body, /PIP_SHA256: [0-9a-f]{64}/, `${id} keeps the pinned pip bootstrap`);
        assert.match(body, /^ {4}timeout-minutes: \d+$/m);
    }
    assert.match(mergeJob(), PINNED_IMAGE, 'the merge sees the same workspace paths as the shards');
});

test('page workflows run each browser in two shards', () => {
    const workflows = allJobs.get('browser-workflows');
    assert.deepEqual(matrixList(workflows, 'browser'), ['firefox', 'webkit']);
    assert.deepEqual(matrixList(workflows, 'shard'), ['1', '2']);
    assert.match(workflows, /test:workflows -- --project \$\{\{ matrix\.browser \}\} --shard \$\{\{ matrix\.shard \}\}\/2/);
    assert.match(workflows, /^ {6}fail-fast: false$/m);
    assert.match(workflows, /name: browser-traces-\$\{\{ matrix\.browser \}\}-shard-\$\{\{ matrix\.shard \}\}/);
});

test('every npm ci restores the npm download cache keyed on the vendor lockfile first', () => {
    const installing = [...allJobs].filter(([, body]) => /npm ci --prefix tools\/vendor/.test(body));
    assert.deepEqual(installing.map(([id]) => id).sort(), ['browser-workflows', 'frontend-coverage', 'frontend-coverage-merge']);
    for (const [id, body] of installing) {
        const jobSteps = steps(body);
        const cacheAt = jobSteps.findIndex(step => /uses: actions\/cache@v\d+/.test(step));
        const installAt = jobSteps.findIndex(step => /npm ci --prefix tools\/vendor/.test(step));
        assert.ok(cacheAt >= 0 && cacheAt < installAt, `${id} restores the npm cache before npm ci`);
        assert.match(jobSteps[cacheAt], /^ {10}path: ~\/\.npm$/m, `${id} caches npm's download cache, not node_modules`);
        assert.match(jobSteps[cacheAt], /hashFiles\('tools\/vendor\/package-lock\.json'\)/, `${id} keys the cache on the lockfile`);
        assert.match(jobSteps[installAt], /npm ci --prefix tools\/vendor --ignore-scripts/, `${id} still installs without scripts`);
        // The key names the job's own pinned image, so an image bump starts a fresh cache.
        const image = /^ {6}image: mcr\.microsoft\.com\/playwright:(v\d+\.\d+\.\d+)-noble$/m.exec(body)?.[1];
        assert.ok(image, `${id} runs in the pinned Playwright image`);
        assert.match(jobSteps[cacheAt], new RegExp(`key: npm-\\$\\{\\{ runner\\.os \\}\\}-playwright-${image.replaceAll('.', '\\.')}-\\$\\{\\{ hashFiles`),
            `${id} keys the cache on its image ${image}`);
    }
});

test('browser job timeouts keep a real margin over measured runs', () => {
    for (const id of ['frontend-coverage', 'browser-workflows']) {
        const minutes = Number(/^ {4}timeout-minutes: (\d+)$/m.exec(allJobs.get(id))[1]);
        assert.ok(minutes >= 20, `${id} allows at least 20 minutes, since runner speed varies`);
    }
});
