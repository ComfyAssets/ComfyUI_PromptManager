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

// Jobs that run only in the merge queue, on main and on demand (#420): skipped on pull_request.
const QUEUE_ONLY_IF = /^ {4}if: github\.event_name != 'pull_request'$/m;
const EVENTS = ['pull_request', 'merge_group', 'push', 'workflow_dispatch'];

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

// A gate passes only when every always-on job succeeded and every queue-only job is skipped on a
// pull request and succeeded on every other event. Anything else fails it.
function assertGateBehaviour(jobText, queueOnly) {
    assert.match(jobText, /^ {10}EVENT_NAME: \$\{\{ github\.event_name \}\}$/m, 'the gate knows the event');
    const variables = gateVariables(jobText);
    for (const id of queueOnly) assert.ok(variables.has(id), `${id} reaches the gate`);
    for (const event of EVENTS) {
        const expected = id => (queueOnly.includes(id) && event === 'pull_request' ? 'skipped' : 'success');
        const green = Object.fromEntries([...variables].map(([id, name]) => [name, expected(id)]));
        assert.equal(runGate(jobText, { ...green, EVENT_NAME: event }), true, `${event}: the expected results pass`);
        for (const [id, name] of variables) {
            for (const result of ['success', 'failure', 'cancelled', 'skipped']) {
                if (result === expected(id)) continue;
                assert.equal(runGate(jobText, { ...green, EVENT_NAME: event, [name]: result }), false,
                    `${event}: ${id}=${result} must fail the gate`);
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
    assert.deepEqual([...needs].sort(), ['browser-workflows', 'frontend-coverage', 'frontend-coverage-merge']);
    assert.match(viewerjs, /^ {4}if: always\(\)$/m, 'viewerjs runs (and fails) when a needed job failed or was skipped');
    assert.deepEqual([...gateVariables(viewerjs).keys()].sort(), [...needs].sort(), 'every needed result reaches the check');
});

test('viewerjs requires the browser shards everywhere but on a pull request', () => {
    assertGateBehaviour(allJobs.get('viewerjs'), ['browser-workflows']);
});

test('Firefox/WebKit page workflows run only outside pull requests; coverage runs on every event', () => {
    assert.match(allJobs.get('browser-workflows'), QUEUE_ONLY_IF);
    for (const id of ['frontend-coverage', 'frontend-coverage-merge']) {
        assert.doesNotMatch(allJobs.get(id), /^ {4}if:/m, `${id} runs on every event`);
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

// A hang (#425) must end inside the test step, before the job limit, so the log and the always()
// uploads survive: Playwright's globalTimeout < the step's timeout-minutes < the job's.
const BROWSER_TEST_STEP = /npm run --prefix tools\/vendor test:(workflows|coverage)/;

test('each browser test step stops itself before its job limit and its traces upload anyway', () => {
    for (const id of browserJobIds) {
        const body = allJobs.get(id);
        const jobMinutes = Number(/^ {4}timeout-minutes: (\d+)$/m.exec(body)[1]);
        const jobSteps = steps(body);
        const testAt = jobSteps.findIndex(step => BROWSER_TEST_STEP.test(step));
        const testStep = jobSteps[testAt];
        const stepMinutes = Number(/^ {8}timeout-minutes: (\d+)$/m.exec(testStep)?.[1]);
        const globalMinutes = Number(/^ {10}PM_TEST_GLOBAL_TIMEOUT_MINUTES: (\d+)$/m.exec(testStep)?.[1]);
        assert.ok(stepMinutes > 0, `${id}: the test step sets its own timeout-minutes`);
        assert.ok(jobMinutes - stepMinutes >= 2, `${id}: the step limit leaves time for setup and the uploads`);
        assert.ok(globalMinutes > 0 && stepMinutes - globalMinutes >= 1,
            `${id}: Playwright's globalTimeout ends the run at least a minute before the step limit`);
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

test('the Python matrix is Linux 3.12 on a pull request and Linux 3.12-3.14 on every other event', () => {
    const body = testJobs().get('test');
    assert.deepEqual(matrixList(body, 'os'), ['ubuntu-latest'], 'Windows and macOS never gate a merge');
    const match = /^ {8}python-version: \$\{\{ github\.event_name == 'pull_request' && fromJSON\('(\[[^\]]*\])'\) \|\| fromJSON\('(\[[^\]]*\])'\) \}\}$/m.exec(body);
    assert.ok(match, 'python-version is chosen by event');
    assert.deepEqual({ pr: JSON.parse(match[1]), other: JSON.parse(match[2]) }, { pr: ['3.12'], other: ['3.12', '3.13', '3.14'] });
    assert.match(body, /^ {6}fail-fast: false$/m);
    assert.match(body, /^ {4}name: py\$\{\{ matrix\.python-version \}\} on \$\{\{ matrix\.os \}\}$/m);
});

test('Windows and macOS run 3.12-3.14 on main, nightly and on demand, never on a PR or in the queue', () => {
    const text = readWorkflow('portability.yml');
    const triggers = text.slice(text.indexOf('\non:'), text.indexOf('\nconcurrency:'));
    for (const event of ['push', 'schedule', 'workflow_dispatch']) assert.match(triggers, new RegExp(`^ {2}${event}:`, 'm'));
    for (const event of ['pull_request', 'merge_group']) assert.doesNotMatch(triggers, new RegExp(`^ {2}${event}`, 'm'));
    assert.match(triggers, /^ {4}branches: \[main\]$/m, 'push runs only for main');
    const body = jobs(text).get('test');
    assert.deepEqual(matrixList(body, 'os'), ['windows-latest', 'macos-latest']);
    assert.deepEqual(matrixList(body, 'python-version'), ['"3.12"', '"3.13"', '"3.14"']);
    assert.match(body, /^ {6}fail-fast: false$/m);
    assert.match(body, /^ {8}run: python -m tests\.run_suite -v$/m);
    assert.doesNotMatch(text, /cancel-in-progress: true|cancel-in-progress: \$/, 'no portability run is cancelled');
});

test('real ComfyUI runs only outside pull requests; the other test.yml checks run on every event', () => {
    const found = testJobs();
    assert.match(found.get('comfyui'), QUEUE_ONLY_IF);
    for (const id of ['test', 'coverage', 'lint', 'js-test']) {
        assert.doesNotMatch(found.get(id), /^ {4}if:/m, `${id} runs on every event`);
    }
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
    assertGateBehaviour(gate, ['comfyui']);
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
    assert.doesNotMatch(jobs(readWorkflow('code-quality.yml')).get('security'), /^ {4}if:/m, 'bandit runs on every event');
});

test('every required check reports on a pull request and the gates carry the full matrix', () => {
    const contexts = JSON.parse(readRepoFile('.github/branch-protection.json')).required_status_checks.contexts;
    assert.deepEqual([...contexts].sort(), [
        'all tests', 'bandit (high severity)', 'black and flake8', 'coverage gate (85%)', 'frontend modules',
        'py3.12 on ubuntu-latest', 'real ComfyUI compatibility', 'viewerjs',
    ].sort());
    assert.equal(contexts.filter(context => /^py/.test(context)).length, 1,
        'a dropped matrix leg never reports on a pull request, so it cannot be required');
    assert.ok(!contexts.some(context => /windows|macos/.test(context)), 'Windows and macOS never gate a merge');
});
