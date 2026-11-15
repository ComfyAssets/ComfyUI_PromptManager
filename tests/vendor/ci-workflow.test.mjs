// Structural checks on .github/workflows/viewerjs-check.yml, plus the time limits of every CI workflow.
// The tooling has no YAML parser, so these read the workflow's fixed two-space layout: jobs at two
// spaces, their keys at four, steps at six.
import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtemp, mkdir, readdir, rm, writeFile } from 'node:fs/promises';
import { readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const workflow = readFileSync(new URL('../../.github/workflows/viewerjs-check.yml', import.meta.url), 'utf8');
const PINNED_IMAGE = /^\s+image: mcr\.microsoft\.com\/playwright:v\d+\.\d+\.\d+-noble@sha256:[0-9a-f]{64}$/m;

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

// #639: each workflow classifies the change first (tools/ci/change_class.py). Jobs that only a code
// change can affect skip for the classes "docs" and "inert", and run when the classifier failed.
const escapeRegExp = text => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const jobIf = condition => new RegExp(`^ {4}if: ${escapeRegExp(`\${{ ${condition} }}`)}$`, 'm');
const CODE_CLASS = "needs.changes.outputs.class != 'docs' && needs.changes.outputs.class != 'inert'";
const CODE_ONLY_IF = jobIf(`!cancelled() && ${CODE_CLASS}`);
// Jobs that run only in the merge queue, on main and on demand (#420): skipped on pull_request.
const QUEUE_ONLY_IF = jobIf(`!cancelled() && github.event_name != 'pull_request' && ${CODE_CLASS}`);
const EVENTS = ['pull_request', 'merge_group', 'push', 'workflow_dispatch'];
const CLASSES = ['code', 'docs', 'inert'];

function gateVariables(jobText) {
    return new Map([...jobText.matchAll(/^ {10}([A-Z_]+): \$\{\{ needs\.([\w-]+)\.result \}\}$/gm)]
        .map(match => [match[2], match[1]]));
}

function runGate(jobText, env) {
    const script = runScript(steps(jobText).find(step => /run: \|/.test(step)));
    try {
        execFileSync('bash', ['--noprofile', '--norc', '-eo', 'pipefail', '-c', script], { env: { PATH: process.env.PATH, ...env }, stdio: 'pipe' });
        return true;
    } catch {
        return false;
    }
}

// A gate passes only when the classifier succeeded and every job did what the change class and the
// event ask (#639). For a code change: every always-on and code-only job succeeded, and every
// queue-only job is skipped on a pull request and succeeded on every other event. For a docs or
// inert change: every always-on job succeeded and every other job is skipped. Anything else fails
// it, and so does a class the classifier does not print.
function assertGateBehaviour(jobText, { queueOnly, codeOnly, always = [] }) {
    assert.match(jobText, /^ {10}EVENT_NAME: \$\{\{ github\.event_name \}\}$/m, 'the gate knows the event');
    assert.match(jobText, /^ {10}CHANGE_CLASS: \$\{\{ needs\.changes\.outputs\.class \}\}$/m, 'the gate knows the change class');
    const variables = gateVariables(jobText);
    assert.deepEqual([...variables.keys()].sort(), ['changes', ...queueOnly, ...codeOnly, ...always].sort(),
        'every job the gate reads has an expected result');
    for (const changeClass of CLASSES) {
        for (const event of EVENTS) {
            const expected = id => {
                if (id === 'changes' || always.includes(id)) return 'success';
                if (changeClass !== 'code') return 'skipped';
                return queueOnly.includes(id) && event === 'pull_request' ? 'skipped' : 'success';
            };
            const where = `${changeClass} change on ${event}`;
            const green = { EVENT_NAME: event, CHANGE_CLASS: changeClass,
                ...Object.fromEntries([...variables].map(([id, name]) => [name, expected(id)])) };
            assert.equal(runGate(jobText, green), true, `${where}: the expected results pass`);
            for (const [id, name] of variables) {
                for (const result of ['success', 'failure', 'cancelled', 'skipped']) {
                    if (result === expected(id)) continue;
                    assert.equal(runGate(jobText, { ...green, [name]: result }), false, `${where}: ${id}=${result} must fail the gate`);
                }
            }
            for (const unknown of ['', 'other', 'code ']) {
                assert.equal(runGate(jobText, { ...green, CHANGE_CLASS: unknown }), false, `${where}: class "${unknown}" must fail the gate`);
            }
        }
    }
}

test('viewerjs keeps its name and needs every frontend job', () => {
    const viewerjs = allJobs.get('viewerjs');
    assert.ok(viewerjs, 'the required context is the job id viewerjs');
    assert.doesNotMatch(viewerjs, /^ {4}name:/m, 'no display name may replace the context "viewerjs"');
    const needs = needsOf(viewerjs);
    const others = [...allJobs.keys()].filter(id => id !== 'viewerjs');
    assert.deepEqual([...needs].sort(), [...others].sort(), 'viewerjs needs every other job in the workflow');
    assert.deepEqual([...needs].sort(), ['browser-workflows', 'changes', 'frontend-coverage', 'frontend-coverage-merge']);
    assert.match(viewerjs, /^ {4}if: always\(\)$/m, 'viewerjs runs (and fails) when a needed job failed or was skipped');
    assert.deepEqual([...gateVariables(viewerjs).keys()].sort(), [...needs].sort(), 'every needed result reaches the check');
});

test('viewerjs requires the browser shards everywhere but on a pull request, and nothing for a docs or inert change', () => {
    assertGateBehaviour(allJobs.get('viewerjs'), { queueOnly: ['browser-workflows'], codeOnly: ['frontend-coverage', 'frontend-coverage-merge'] });
});

test('Firefox/WebKit page workflows run only outside pull requests; coverage runs on every event; both for code changes only', () => {
    assert.match(allJobs.get('browser-workflows'), QUEUE_ONLY_IF);
    assert.match(allJobs.get('frontend-coverage'), CODE_ONLY_IF);
    // !cancelled() replaces the implicit success() check, so the merge names the shards' success itself.
    assert.match(allJobs.get('frontend-coverage-merge'), jobIf(`!cancelled() && needs.frontend-coverage.result == 'success' && ${CODE_CLASS}`));
});

test('coverage runs as four shards that each upload their raw coverage', () => {
    const coverage = allJobs.get('frontend-coverage');
    assert.deepEqual(matrixList(coverage, 'shard'), ['1', '2', '3', '4']);
    assert.match(coverage, /^ {4}name: frontend coverage \(shard \$\{\{ matrix\.shard \}\}\/4\)$/m);
    assert.match(coverage, /npm run --prefix tools\/vendor test:coverage:shard -- \$\{\{ matrix\.shard \}\}\/4/);
    const upload = steps(coverage).find(step => /name: coverage-raw-\$\{\{ matrix\.shard \}\}/.test(step));
    assert.ok(upload, 'each shard uploads coverage-raw-N');
    assert.match(upload, /^ {10}path: coverage\/js\/raw\/?$/m);
    assert.match(upload, /^ {10}retention-days: 3$/m);
    assert.match(upload, /^ {10}if-no-files-found: error$/m);
});

test('merge job downloads every shard and runs the merge-only report and check', () => {
    const merge = mergeJob();
    assert.ok(merge, 'frontend-coverage-merge exists');
    assert.deepEqual(needsOf(merge), ['changes', 'frontend-coverage']);
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

// #473: two shards split by test count put the slow concept specs in shard 1, which reached the 18-minute step limit.
test('page workflows run each browser in three shards', () => {
    const workflows = allJobs.get('browser-workflows');
    assert.deepEqual(matrixList(workflows, 'browser'), ['firefox', 'webkit']);
    assert.deepEqual(matrixList(workflows, 'shard'), ['1', '2', '3']);
    assert.match(workflows, /test:workflows -- --project \$\{\{ matrix\.browser \}\} --shard \$\{\{ matrix\.shard \}\}\/3/);
    assert.match(workflows, /^ {4}name: page workflows \(\$\{\{ matrix\.browser \}\}, shard \$\{\{ matrix\.shard \}\}\/3\)$/m);
    assert.match(workflows, /name: Page workflows in \$\{\{ matrix\.browser \}\}, shard \$\{\{ matrix\.shard \}\}\/3$/m);
    assert.doesNotMatch(workflows, /\/2\b/, 'no step still names two shards');
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
        const image = /^ {6}image: mcr\.microsoft\.com\/playwright:(v\d+\.\d+\.\d+)-noble@sha256:[0-9a-f]{64}$/m.exec(body)?.[1];
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

// A hang (#425) must end inside the test step, before the job limit, so the log and the always()
// uploads survive: the step's timeout-minutes < the job's.
const BROWSER_TEST_STEP = /npm run --prefix tools\/vendor test:(workflows|coverage)/;

test('each browser test step stops itself before its job limit and its traces upload anyway', () => {
    for (const id of browserJobIds) {
        const body = allJobs.get(id);
        const jobMinutes = Number(/^ {4}timeout-minutes: (\d+)$/m.exec(body)[1]);
        const jobSteps = steps(body);
        const testAt = jobSteps.findIndex(step => BROWSER_TEST_STEP.test(step));
        const testStep = jobSteps[testAt];
        const stepMinutes = Number(/^ {8}timeout-minutes: (\d+)$/m.exec(testStep)?.[1]);
        assert.ok(stepMinutes > 0, `${id}: the test step sets its own timeout-minutes`);
        assert.ok(jobMinutes - stepMinutes >= 2, `${id}: the step limit leaves time for setup and the uploads`);
        const upload = jobSteps.slice(testAt + 1).find(step => /^ {10}path: tools\/vendor\/test-results\/$/m.test(step));
        assert.ok(upload, `${id} uploads tools/vendor/test-results/ (traces and blob report) after the tests`);
        assert.match(upload, /^ {8}if: always\(\)$/m, `${id} uploads them when the step failed or timed out`);
    }
});

// Every job is bounded (#350): a hang ends at a measured limit instead of GitHub's 6-hour default.
const GATING_WORKFLOWS = ['test.yml', 'code-quality.yml', 'viewerjs-check.yml'];
const BOUNDED_WORKFLOWS = [...GATING_WORKFLOWS, 'portability.yml'];
const readWorkflow = name => readFileSync(new URL(`../../.github/workflows/${name}`, import.meta.url), 'utf8');

test('every job in the CI workflows has a numeric timeout-minutes', () => {
    for (const name of BOUNDED_WORKFLOWS) {
        const found = jobs(readWorkflow(name));
        assert.ok(found.size > 0, `${name} has jobs`);
        for (const [id, body] of found) {
            const limit = /^ {4}timeout-minutes: (\d+)\s*$/m.exec(body);
            assert.ok(limit, `${name} job ${id} sets a numeric timeout-minutes`);
            assert.ok(Number(limit[1]) >= 5, `${name} job ${id} allows at least 5 minutes`);
        }
    }
});

test('the Python matrix and coverage run the suite through the watchdog runner', () => {
    const found = jobs(readWorkflow('test.yml'));
    assert.match(found.get('test'), /^ {8}run: python -m tests\.run_suite -v$/m);
    assert.match(found.get('coverage'), /^ {10}python -m coverage run -m tests\.run_suite$/m);
    for (const id of ['test', 'coverage']) {
        assert.doesNotMatch(found.get(id), /unittest discover/, `${id} no longer runs the bare unittest discover`);
    }
});

// CI hygiene (#352): lint tools are pinned, the bandit hook can fail, the JS job caches npm.
const readRepoFile = path => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');

test('black and flake8 are pinned exactly and the lint job installs those pins', () => {
    const requirements = readRepoFile('requirements-dev.txt');
    assert.match(requirements, /^black==\d+\.\d+\.\d+$/m, 'black is pinned exactly');
    assert.match(requirements, /^flake8==\d+\.\d+\.\d+$/m, 'flake8 is pinned exactly');
    const lint = jobs(readWorkflow('test.yml')).get('lint');
    assert.match(lint, /pip install \$\(grep -E '\^\(black\|flake8\)==' requirements-dev\.txt\)/);
    assert.doesNotMatch(lint, /pip install black flake8/, 'lint no longer installs the latest releases');
});

test('the frontend modules job caches npm keyed on the vendor lockfile', () => {
    const setupNode = steps(jobs(readWorkflow('test.yml')).get('js-test'))
        .find(step => /uses: actions\/setup-node@v\d+/.test(step));
    assert.match(setupNode, /^ {10}cache: npm$/m);
    assert.match(setupNode, /^ {10}cache-dependency-path: tools\/vendor\/package-lock\.json$/m);
});

test('the bandit pre-commit hook fails at the severity CI enforces and passes every file', () => {
    const quality = readWorkflow('code-quality.yml');
    assert.match(quality, /^ {8}run: bandit -r \. -lll -q --exclude /m, 'CI fails on high severity');
    const hook = /- id: bandit\n([\s\S]*?)(?=\n {6}(?:- id:|#)|\n*$)/.exec(readRepoFile('.pre-commit-config.yaml'))?.[1];
    assert.ok(hook, 'the bandit hook exists');
    assert.match(hook, /^ {8}entry: bandit -lll -q$/m, 'the hook enforces the same -lll level as CI');
    assert.doesNotMatch(hook, /true|exit-zero|bash -c/, 'nothing masks the exit code or drops a file');
    assert.doesNotMatch(hook, /pass_filenames: false/, 'the hook scans the staged files');
});

// Event split (#420): pull requests run a fast subset, the merge queue and main run everything.
const testJobs = () => jobs(readWorkflow('test.yml'));

test('the Python matrix is Linux 3.12 on a pull request or a docs or inert change and Linux 3.12-3.14 otherwise', () => {
    const body = testJobs().get('test');
    assert.deepEqual(matrixList(body, 'os'), ['ubuntu-latest'], 'Windows never gates a merge');
    const match = /^ {8}python-version: \$\{\{ \(github\.event_name == 'pull_request' \|\| needs\.changes\.outputs\.class == 'docs' \|\| needs\.changes\.outputs\.class == 'inert'\) && fromJSON\('(\[[^\]]*\])'\) \|\| fromJSON\('(\[[^\]]*\])'\) \}\}$/m.exec(body);
    assert.ok(match, 'python-version is chosen by event and change class');
    assert.deepEqual({ small: JSON.parse(match[1]), other: JSON.parse(match[2]) }, { small: ['3.12'], other: ['3.12', '3.13', '3.14'] });
    assert.match(body, /^ {6}fail-fast: false$/m);
    assert.match(body, /^ {4}name: py\$\{\{ matrix\.python-version \}\} on \$\{\{ matrix\.os \}\}$/m);
});

// #639: a skipped matrix job reports under its unexpanded name, so the required context
// "py3.12 on ubuntu-latest" would never report. The job therefore always starts, and an inert change
// skips its steps instead.
test('the Python matrix always starts and skips its steps for an inert change', () => {
    const body = testJobs().get('test');
    assert.deepEqual(needsOf(body), ['changes']);
    assert.match(body, jobIf('!cancelled()'), 'it also runs, in full, when the classifier failed');
    const jobSteps = steps(body);
    const nothing = jobSteps.filter(step => /^ {8}if: needs\.changes\.outputs\.class == 'inert'$/m.test(step));
    assert.equal(nothing.length, 1, 'one step says nothing needs testing');
    assert.match(nothing[0], /^ {8}run: echo /m, 'and it only prints that');
    for (const step of jobSteps.filter(step => step !== nothing[0])) {
        assert.match(step, /^ {8}if: needs\.changes\.outputs\.class != 'inert'$/m, `every real step is skipped for an inert change: ${step.trim().split('\n')[0]}`);
    }
    assert.equal(jobSteps.length, 5);
});

test('Windows runs 3.12-3.14 on main, nightly and on demand, never on a PR or in the queue', () => {
    const text = readWorkflow('portability.yml');
    const triggers = text.slice(text.indexOf('\non:'), text.indexOf('\nconcurrency:'));
    for (const event of ['push', 'schedule', 'workflow_dispatch']) assert.match(triggers, new RegExp(`^ {2}${event}:`, 'm'));
    for (const event of ['pull_request', 'merge_group']) assert.doesNotMatch(triggers, new RegExp(`^ {2}${event}`, 'm'));
    assert.match(triggers, /^ {4}branches: \[main\]$/m, 'push runs only for main');
    const body = jobs(text).get('test');
    assert.deepEqual(matrixList(body, 'os'), ['windows-latest'], 'macOS is not tested in CI (#571)');
    assert.doesNotMatch(text, /macos-/, 'no macOS runner is used');
    assert.deepEqual(matrixList(body, 'python-version'), ['"3.12"', '"3.13"', '"3.14"']);
    assert.match(body, /^ {6}fail-fast: false$/m);
    assert.match(body, /^ {8}run: python -m tests\.run_suite -v$/m);
    assert.doesNotMatch(text, /cancel-in-progress: true|cancel-in-progress: \$/, 'no portability run is cancelled');
});

// #493: the portability limit follows the rule in its comment: ceil(1.5 x the slowest of three
// green main runs) + 3 minutes, never under 5, with those runs and times written above jobs:.
// The times are whole-job wall times. That the runs were green and on main is the author's word.
test('the portability limit is computed from three measured green main runs', () => {
    const text = readWorkflow('portability.yml');
    const comment = text.slice(0, text.indexOf('\njobs:'));
    const runs = [...comment.matchAll(/^# Measured \d{4}-\d{2}-\d{2} on main runs (\d+) \([0-9a-f]{7}\), (\d+) \([0-9a-f]{7}\), (\d+) \([0-9a-f]{7}\):$/gm)];
    assert.equal(runs.length, 1, 'the comment names the three runs it measured, once');
    assert.equal(new Set(runs[0].slice(1)).size, 3, 'three different runs');
    const time = '(\\d+)m([0-5]\\d)s \\(([^)]+)\\)';
    const slowest = new RegExp(`^#\\s+slowest leg: ${time} / ${time} / ${time} -> (\\d+) min$`, 'm').exec(comment);
    assert.ok(slowest, 'the comment gives the slowest leg of each run and the limit');
    const seconds = [1, 4, 7].map(i => Number(slowest[i]) * 60 + Number(slowest[i + 1]));
    const expected = Math.max(5, Math.ceil(1.5 * Math.max(...seconds) / 60) + 3);
    assert.equal(Number(slowest[10]), expected, 'the written limit follows the rule');
    assert.equal(comment.match(/-> \d+ min/g).length, 1, 'no older measurement stays beside it');
    const limit = Number(/^ {4}timeout-minutes: (\d+)\s*$/m.exec(jobs(text).get('test'))[1]);
    assert.equal(limit, expected, 'the job uses the written limit');
});

test('real ComfyUI runs only outside pull requests; the other test.yml checks run on every event; all for code changes only', () => {
    const found = testJobs();
    assert.match(found.get('comfyui'), QUEUE_ONLY_IF);
    for (const id of ['coverage', 'lint', 'js-test']) {
        assert.match(found.get(id), CODE_ONLY_IF, `${id} runs on every event for a code change`);
    }
    assert.match(found.get('perf-budget'),
        jobIf(`!cancelled() && (github.event_name == 'push' || github.event_name == 'workflow_dispatch') && ${CODE_CLASS}`));
});

test('all tests needs every gating test.yml job and requires the full set outside pull requests', () => {
    const found = testJobs();
    const gate = found.get('all-tests');
    assert.ok(gate, 'the aggregate gate exists');
    assert.match(gate, /^ {4}name: all tests$/m, 'the required context is "all tests"');
    assert.match(gate, /^ {4}if: always\(\)$/m);
    const gating = [...found.keys()].filter(id => !['all-tests', 'perf-budget'].includes(id));
    assert.deepEqual([...needsOf(gate)].sort(), gating.sort(), 'the gate needs every job but the non-gating perf budget');
    assert.deepEqual([...gateVariables(gate).keys()].sort(), gating.sort(), 'every needed result reaches the gate');
    assertGateBehaviour(gate, { queueOnly: ['comfyui'], codeOnly: ['coverage', 'lint', 'js-test'], always: ['test'] });
});

test('the CI workflows run on pull_request, merge_group and push without path filters', () => {
    for (const name of GATING_WORKFLOWS) {
        const text = readWorkflow(name);
        const triggers = text.slice(text.indexOf('\non:'), text.indexOf('\n', text.indexOf('\non:') + 1) + 400);
        for (const event of ['merge_group', 'pull_request', 'push']) {
            assert.match(triggers, new RegExp(`^ {2}${event}:`, 'm'), `${name} runs on ${event}`);
        }
        assert.doesNotMatch(text, /^ {4}paths(-ignore)?:/m, `${name} has no path filter that would leave a required check pending`);
    }
    assert.match(jobs(readWorkflow('code-quality.yml')).get('security'), CODE_ONLY_IF, 'bandit runs on every event for a code change');
});

// #639: the change is classified once per workflow, before anything else, from file names alone.
test('each gating workflow classifies the change first and every other job waits for it', () => {
    for (const name of GATING_WORKFLOWS) {
        const found = jobs(readWorkflow(name));
        assert.equal([...found.keys()][0], 'changes', `${name} starts with the classifier`);
        const changes = found.get('changes');
        assert.match(changes, /^ {4}name: change class$/m);
        assert.doesNotMatch(changes, /^ {4}(needs|if):/m, `${name}: the classifier waits for nothing and always runs`);
        assert.match(changes, /^ {6}class: \$\{\{ steps\.classify\.outputs\.class \}\}$/m);
        const [checkout, classify] = steps(changes);
        assert.match(checkout, /uses: actions\/checkout@v\d+/);
        assert.match(checkout, /^ {10}fetch-depth: 0$/m, 'the base commit must be in the clone');
        assert.match(checkout, /^ {10}filter: blob:none$/m, 'file names need no file contents');
        assert.match(checkout, /^ {10}persist-credentials: false$/m);
        assert.match(classify, /^ {8}id: classify$/m);
        assert.match(classify, /^ {10}EVENT_NAME: \$\{\{ github\.event_name \}\}$/m);
        assert.match(classify, /^ {10}BASE_SHA: \$\{\{ github\.event\.pull_request\.base\.sha \|\| github\.event\.merge_group\.base_sha \|\| github\.event\.before \}\}$/m);
        assert.match(classify, /^ {10}HEAD_SHA: \$\{\{ github\.event\.pull_request\.head\.sha \|\| github\.event\.merge_group\.head_sha \|\| github\.sha \}\}$/m);
        assert.match(classify, /^ {8}run: python3 tools\/ci\/change_class\.py \| tee -a "\$GITHUB_OUTPUT"$/m);
        for (const [id, body] of found) {
            if (id !== 'changes') assert.ok(needsOf(body).includes('changes'), `${name} job ${id} waits for the classifier`);
        }
    }
});

test('the pre-push hook runs the suite through the classifier', () => {
    const hook = /- id: tests\n([\s\S]*?)(?=\n {6}(?:- id:|#)|\n*$)/.exec(readRepoFile('.pre-commit-config.yaml'))?.[1];
    assert.ok(hook, 'the tests hook exists');
    assert.match(hook, /^ {8}entry: python tools\/ci\/change_class\.py --unless-inert python -m tests\.run_suite -q$/m);
    assert.match(hook, /^ {8}stages: \[pre-push\]$/m);
});

test('every required check reports on a pull request and the gates carry the full matrix', () => {
    const contexts = JSON.parse(readRepoFile('.github/branch-protection.json')).required_status_checks.contexts;
    assert.deepEqual([...contexts].sort(), [
        'all tests', 'bandit (high severity)', 'black and flake8', 'coverage gate (85%)', 'frontend modules',
        'py3.12 on ubuntu-latest', 'real ComfyUI compatibility', 'viewerjs',
    ].sort());
    assert.equal(contexts.filter(context => /^py/.test(context)).length, 1,
        'a dropped matrix leg never reports on a pull request, so it cannot be required');
    assert.ok(!contexts.some(context => /windows|macos/.test(context)), 'Windows never gates a merge');
});
