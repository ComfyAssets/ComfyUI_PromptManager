// Structural checks on the CI workflows (#666): ci.yml is the gate that runs once on a pull request,
// nightly.yml everything that blocks nothing, publish.yml the only workflow a push to main starts.
// The tooling has no YAML parser, so these read the workflow's fixed two-space layout: jobs at two
// spaces, their keys at four, steps at six.
import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtemp, mkdir, readdir, rm, writeFile } from 'node:fs/promises';
import { readdirSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const readRepoFile = path => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const readWorkflow = name => readRepoFile(`.github/workflows/${name}`);
const workflow = readWorkflow('ci.yml');
const nightly = readWorkflow('nightly.yml');
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
const nightlyJobs = jobs(nightly);
const BROWSER_TEST_STEP = /npm run --prefix tools\/vendor test:(workflows|coverage)/;
const browserJobs = [...allJobs, ...nightlyJobs].filter(([, body]) => BROWSER_TEST_STEP.test(body));
const mergeJob = () => allJobs.get('frontend-coverage-merge');
const verifyStep = () => steps(mergeJob()).find(step => /name: Verify every shard's raw coverage arrived/.test(step));
const escapeRegExp = text => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const jobIf = condition => new RegExp(`^ {4}if: ${escapeRegExp(`\${{ ${condition} }}`)}$`, 'm');
const triggersOf = text => {
    const lines = text.split('\n');
    const start = lines.indexOf('on:');
    assert.notEqual(start, -1, 'workflow has an on: section');
    const events = [];
    for (const line of lines.slice(start + 1)) {
        if (/^\S/.test(line)) break;
        const event = /^ {2}([a-z_]+):/.exec(line);
        if (event) events.push(event[1]);
    }
    return events.sort();
};

// The classes tools/ci/change_class.py prints, and the jobs each one asks for. Every other job must
// be skipped; a job that ran when its class did not ask for it is as wrong as one that failed.
const CODE_JOBS = ['python', 'lint', 'security', 'js-test', 'tooling', 'frontend-coverage', 'frontend-coverage-merge'];
const RELEASE_JOBS = ['package'];
const JOBS_OF_CLASS = { code: CODE_JOBS, release: RELEASE_JOBS, docs: [], inert: [] };

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

// ---- Triggers: one pass on the pull request, nothing on main but publish -------------------------

test('no workflow runs in a merge queue', () => {
    const directory = fileURLToPath(new URL('../../.github/workflows/', import.meta.url));
    const names = readdirSync(directory).filter(name => /\.ya?ml$/.test(name)).sort();
    assert.deepEqual(names, ['ci.yml', 'nightly.yml', 'publish.yml', 'viewerjs-update.yml'],
        'the old workflows are gone; a new workflow is added to this list and to the checks below');
    for (const name of names) {
        assert.doesNotMatch(readWorkflow(name), /merge_group/, `${name} has no merge_group trigger or condition`);
    }
});

test('the gate runs on pull requests and on demand, never on a push', () => {
    assert.deepEqual(triggersOf(workflow), ['pull_request', 'workflow_dispatch']);
    assert.match(workflow, /^ {2}pull_request:\n {4}branches: \[main\]$/m);
    assert.doesNotMatch(workflow, /^ {4}paths(-ignore)?:/m, 'no path filter may leave the required check pending');
    assert.match(workflow, /^ {2}cancel-in-progress: \$\{\{ github\.event_name == 'pull_request' \}\}$/m);
});

test('nightly runs on its schedule and on demand, never on a pull request or a push', () => {
    assert.deepEqual(triggersOf(nightly), ['schedule', 'workflow_dispatch']);
    assert.doesNotMatch(nightly, /cancel-in-progress: true|cancel-in-progress: \$/, 'no nightly run is cancelled');
    assert.doesNotMatch(nightly, /needs\.changes/, 'nightly tests main as it is: no change class');
});

test('a push to main starts only publish, and only when pyproject.toml changed', () => {
    const publish = readWorkflow('publish.yml');
    assert.deepEqual(triggersOf(publish), ['push', 'workflow_dispatch']);
    assert.match(publish, /^ {4}paths:\n {6}- "pyproject\.toml"$/m);
    for (const name of ['ci.yml', 'nightly.yml', 'viewerjs-update.yml']) {
        assert.ok(!triggersOf(readWorkflow(name)).includes('push'), `${name} does not run on a push`);
    }
});

// ---- The change class -----------------------------------------------------------------------------

test('the gate classifies the change first, from file names, and every other job waits for it', () => {
    assert.equal([...allJobs.keys()][0], 'changes', 'the classifier is the first job');
    const changes = allJobs.get('changes');
    assert.match(changes, /^ {4}name: change class$/m);
    assert.doesNotMatch(changes, /^ {4}(needs|if):/m, 'the classifier waits for nothing and always runs');
    assert.match(changes, /^ {6}class: \$\{\{ steps\.classify\.outputs\.class \}\}$/m);
    const [checkout, classify] = steps(changes);
    assert.match(checkout, /uses: actions\/checkout@v\d+/);
    assert.match(checkout, /^ {10}fetch-depth: 0$/m, 'the base commit must be in the clone');
    assert.match(checkout, /^ {10}filter: blob:none$/m, 'file names need no file contents');
    assert.match(checkout, /^ {10}persist-credentials: false$/m);
    assert.match(classify, /^ {8}id: classify$/m);
    assert.match(classify, /^ {10}EVENT_NAME: \$\{\{ github\.event_name \}\}$/m);
    assert.match(classify, /^ {10}BASE_SHA: \$\{\{ github\.event\.pull_request\.base\.sha \}\}$/m);
    assert.match(classify, /^ {10}HEAD_SHA: \$\{\{ github\.event\.pull_request\.head\.sha \|\| github\.sha \}\}$/m);
    assert.match(classify, /^ {8}run: python3 tools\/ci\/change_class\.py \| tee -a "\$GITHUB_OUTPUT"$/m);
    for (const [id, body] of allJobs) {
        if (id !== 'changes') assert.ok(needsOf(body).includes('changes'), `job ${id} waits for the classifier`);
    }
});

test('each job runs for exactly one change class', () => {
    assert.deepEqual([...allJobs.keys()].sort(), ['changes', 'gate', ...CODE_JOBS, ...RELEASE_JOBS].sort());
    // No status function in the condition, so the implicit success() also holds: a job never runs
    // after a failed classifier, and the merge never runs after a failed shard.
    for (const id of CODE_JOBS) assert.match(allJobs.get(id), jobIf("needs.changes.outputs.class == 'code'"), id);
    for (const id of RELEASE_JOBS) assert.match(allJobs.get(id), jobIf("needs.changes.outputs.class == 'release'"), id);
    const classes = [...readRepoFile('tools/ci/change_class.py').matchAll(/return "([a-z]+)"|found = "([a-z]+)"/g)]
        .map(match => match[1] ?? match[2]);
    assert.deepEqual([...new Set(classes)].sort(), Object.keys(JOBS_OF_CLASS).sort(), 'the gate knows every class the script prints');
});

test('a release runs the package-content check and nothing else', () => {
    const body = allJobs.get('package');
    assert.match(body, /^ {6}- run: python -m unittest -v tests\.test_package_contents$/m);
    assert.doesNotMatch(body, /pip install/, 'the check needs nothing installed');
});

// ---- The gate ---------------------------------------------------------------------------------------

test('gate is the one required context and needs every other job', () => {
    const gate = allJobs.get('gate');
    assert.ok(gate, 'the required context is the job id gate');
    assert.doesNotMatch(gate, /^ {4}name:/m, 'no display name may replace the context "gate"');
    assert.equal([...allJobs.keys()].at(-1), 'gate');
    const others = [...allJobs.keys()].filter(id => id !== 'gate');
    assert.deepEqual([...needsOf(gate)].sort(), [...others].sort(), 'gate needs every other job in the workflow');
    assert.match(gate, /^ {4}if: always\(\)$/m, 'gate runs (and fails) when a needed job failed or was skipped');
    assert.deepEqual([...gateVariables(gate).keys()].sort(), [...others].sort(), 'every needed result reaches the gate');
    assert.match(gate, /^ {10}CHANGE_CLASS: \$\{\{ needs\.changes\.outputs\.class \}\}$/m, 'the gate knows the change class');
    assert.doesNotMatch(gate, /github\.event_name|EVENT_NAME/, 'the gate asks the same of every event');
});

// The gate passes only when the classifier succeeded and every job did what the class asks: the
// class's jobs succeeded and every other job was skipped. Any other result of any job fails it, and
// so does a failed classifier or a class the script does not print.
test('gate passes only when every job did what the change class asks', () => {
    const gate = allJobs.get('gate');
    const variables = gateVariables(gate);
    for (const [changeClass, running] of Object.entries(JOBS_OF_CLASS)) {
        const expected = id => (id === 'changes' || running.includes(id) ? 'success' : 'skipped');
        const green = { CHANGE_CLASS: changeClass, ...Object.fromEntries([...variables].map(([id, name]) => [name, expected(id)])) };
        assert.equal(runGate(gate, green), true, `${changeClass}: the expected results pass`);
        for (const [id, name] of variables) {
            for (const result of ['success', 'failure', 'cancelled', 'skipped', '']) {
                if (result === expected(id)) continue;
                assert.equal(runGate(gate, { ...green, [name]: result }), false, `${changeClass}: ${id}=${result || 'nothing'} must fail the gate`);
            }
        }
        for (const unknown of ['', 'other', `${changeClass} `, 'tests-only']) {
            assert.equal(runGate(gate, { ...green, CHANGE_CLASS: unknown }), false, `${changeClass}: class "${unknown}" must fail the gate`);
        }
    }
});

test('branch protection requires only gate, on a branch that is up to date', () => {
    const protection = JSON.parse(readRepoFile('.github/branch-protection.json'));
    assert.deepEqual(protection.required_status_checks, { strict: true, contexts: ['gate'] });
    assert.equal(protection.enforce_admins, true);
    assert.equal(protection.allow_force_pushes, false);
});

// ---- The gate's jobs ------------------------------------------------------------------------------

test('the Python suite runs once, under coverage, through the watchdog runner', () => {
    const body = allJobs.get('python');
    const suiteRuns = body.match(/tests\.run_suite/g) ?? [];
    assert.equal(suiteRuns.length, 1, 'one run proves both the tests and the coverage floor');
    assert.match(body, /^ {8}run: python -m coverage run -m tests\.run_suite -v$/m);
    assert.match(body, /^ {10}python -m coverage combine$/m);
    assert.match(body, /^ {10}python -m coverage report --fail-under=85$/m);
    assert.doesNotMatch(body, /unittest discover/);
    assert.match(body, /^ {10}python-version: "3\.12"$/m);
    assert.doesNotMatch(body, /matrix/, 'other Pythons run nightly');
});

// CI hygiene (#352): lint tools are pinned, the bandit hook can fail, the JS job caches npm.
test('black and flake8 are pinned exactly and the lint job installs those pins', () => {
    const requirements = readRepoFile('requirements-dev.txt');
    assert.match(requirements, /^black==\d+\.\d+\.\d+$/m, 'black is pinned exactly');
    assert.match(requirements, /^flake8==\d+\.\d+\.\d+$/m, 'flake8 is pinned exactly');
    const lint = allJobs.get('lint');
    assert.match(lint, /pip install \$\(grep -E '\^\(black\|flake8\)==' requirements-dev\.txt\)/);
    assert.doesNotMatch(lint, /pip install black flake8/, 'lint no longer installs the latest releases');
});

test('the frontend modules job caches npm keyed on the vendor lockfile', () => {
    const setupNode = steps(allJobs.get('js-test')).find(step => /uses: actions\/setup-node@v\d+/.test(step));
    assert.match(setupNode, /^ {10}cache: npm$/m);
    assert.match(setupNode, /^ {10}cache-dependency-path: tools\/vendor\/package-lock\.json$/m);
    assert.match(allJobs.get('js-test'), /^ {8}run: node --test tests\/js\/\*\.test\.js$/m);
});

test('the bandit pre-commit hook fails at the severity CI enforces and passes every file', () => {
    assert.match(allJobs.get('security'), /^ {8}run: bandit -r \. -lll -q --exclude /m, 'CI fails on high severity');
    const hook = /- id: bandit\n([\s\S]*?)(?=\n {6}(?:- id:|#)|\n*$)/.exec(readRepoFile('.pre-commit-config.yaml'))?.[1];
    assert.ok(hook, 'the bandit hook exists');
    assert.match(hook, /^ {8}entry: bandit -lll -q$/m, 'the hook enforces the same -lll level as CI');
    assert.doesNotMatch(hook, /true|exit-zero|bash -c/, 'nothing masks the exit code or drops a file');
    assert.doesNotMatch(hook, /pass_filenames: false/, 'the hook scans the staged files');
});

test('the pre-push hook runs the suite through the classifier', () => {
    const hook = /- id: tests\n([\s\S]*?)(?=\n {6}(?:- id:|#)|\n*$)/.exec(readRepoFile('.pre-commit-config.yaml'))?.[1];
    assert.ok(hook, 'the tests hook exists');
    assert.match(hook, /^ {8}entry: python tools\/ci\/change_class\.py --unless-inert python -m tests\.run_suite -q$/m);
    assert.match(hook, /^ {8}stages: \[pre-push\]$/m);
});

test('the tooling job checks the pins and runs the tooling tests', () => {
    const body = allJobs.get('tooling');
    const commands = [...body.matchAll(/^ {6}- run: npm run --prefix tools\/vendor (\w+)$/gm)].map(match => match[1]);
    assert.deepEqual(commands, ['check', 'test']);
    assert.match(body, PINNED_IMAGE, 'the tooling tests see the image the browser jobs use');
    for (const id of ['frontend-coverage', 'frontend-coverage-merge']) {
        assert.doesNotMatch(allJobs.get(id), /tools\/vendor (check|test)$/m, `${id} runs no tooling step`);
    }
});

test('coverage runs as four shards that each upload their raw coverage', () => {
    const coverage = allJobs.get('frontend-coverage');
    assert.deepEqual(matrixList(coverage, 'shard'), ['1', '2', '3', '4']);
    assert.match(coverage, /^ {4}name: frontend coverage \(shard \$\{\{ matrix\.shard \}\}\/4\)$/m);
    assert.match(coverage, /npm run --prefix tools\/vendor test:coverage:shard -- \$\{\{ matrix\.shard \}\}\/4/);
    assert.match(coverage, /^ {6}fail-fast: false$/m);
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

// ---- Browser jobs, in the gate and nightly --------------------------------------------------------

test('every browser job sets PM_TEST_WORKERS and runs in the pinned image', () => {
    assert.deepEqual(browserJobs.map(([id]) => id).sort(), ['browser-workflows', 'frontend-coverage']);
    for (const [id, body] of browserJobs) {
        assert.match(body, /^ {6}PM_TEST_WORKERS: 3$/m, `${id} sets PM_TEST_WORKERS for the whole job`);
        assert.match(body, PINNED_IMAGE, `${id} keeps the pinned Playwright image`);
        assert.match(body, /PIP_SHA256: [0-9a-f]{64}/, `${id} keeps the pinned pip bootstrap`);
    }
    assert.match(mergeJob(), PINNED_IMAGE, 'the merge sees the same workspace paths as the shards');
});

// #473: two shards split by test count put the slow concept specs in shard 1, which reached its step limit.
test('nightly page workflows run Firefox, WebKit and touch in three shards each', () => {
    const workflows = nightlyJobs.get('browser-workflows');
    assert.deepEqual(matrixList(workflows, 'browser'), ['firefox', 'webkit', 'touch']);
    assert.deepEqual(matrixList(workflows, 'shard'), ['1', '2', '3']);
    assert.match(workflows, /test:workflows -- --project \$\{\{ matrix\.browser \}\} --shard \$\{\{ matrix\.shard \}\}\/3 /);
    assert.match(workflows, /^ {4}name: page workflows \(\$\{\{ matrix\.browser \}\}, shard \$\{\{ matrix\.shard \}\}\/3\)$/m);
    assert.match(workflows, /^ {6}fail-fast: false$/m);
    assert.match(workflows, /name: browser-traces-\$\{\{ matrix\.browser \}\}-shard-\$\{\{ matrix\.shard \}\}/);
});

test('every npm ci in a container restores the npm download cache keyed on the vendor lockfile first', () => {
    const installing = [...allJobs, ...nightlyJobs].filter(([, body]) => /npm ci --prefix tools\/vendor/.test(body) && /^ {4}container:$/m.test(body));
    assert.deepEqual(installing.map(([id]) => id).sort(), ['browser-workflows', 'comfyui', 'frontend-coverage', 'frontend-coverage-merge', 'tooling']);
    for (const [id, body] of installing) {
        const jobSteps = steps(body);
        const cacheAt = jobSteps.findIndex(step => /uses: actions\/cache@v\d+/.test(step) && /path: ~\/\.npm$/m.test(step));
        const installAt = jobSteps.findIndex(step => /npm ci --prefix tools\/vendor/.test(step));
        assert.ok(cacheAt >= 0 && cacheAt < installAt, `${id} restores the npm cache before npm ci`);
        assert.match(jobSteps[cacheAt], /hashFiles\('tools\/vendor\/package-lock\.json'\)/, `${id} keys the cache on the lockfile`);
        assert.match(jobSteps[installAt], /npm ci --prefix tools\/vendor --ignore-scripts/, `${id} still installs without scripts`);
        // The key names the job's own pinned image, so an image bump starts a fresh cache.
        const image = /^ {6}image: mcr\.microsoft\.com\/playwright:(v\d+\.\d+\.\d+)-noble@sha256:[0-9a-f]{64}$/m.exec(body)?.[1];
        assert.ok(image, `${id} runs in the pinned Playwright image`);
        assert.match(jobSteps[cacheAt], new RegExp(`key: npm-\\$\\{\\{ runner\\.os \\}\\}-playwright-${image.replaceAll('.', '\\.')}-\\$\\{\\{ hashFiles`),
            `${id} keys the cache on its image ${image}`);
    }
});

// A hang (#425) must end inside the test step, before the job limit, so the log and the always()
// uploads survive: the step's timeout-minutes < the job's.
test('each browser test step stops itself before its job limit and its traces upload anyway', () => {
    for (const [id, body] of browserJobs) {
        const jobMinutes = Number(/^ {4}timeout-minutes: (\d+)$/m.exec(body)[1]);
        const jobSteps = steps(body);
        const testAt = jobSteps.findIndex(step => BROWSER_TEST_STEP.test(step));
        const testStep = jobSteps[testAt];
        const stepMinutes = Number(/^ {8}timeout-minutes: (\d+)$/m.exec(testStep)?.[1]);
        assert.ok(stepMinutes > 0, `${id}: the test step sets its own timeout-minutes`);
        assert.equal(jobMinutes - stepMinutes, 2, `${id}: the step limit leaves 2 minutes for setup and the uploads`);
        const upload = jobSteps.slice(testAt + 1).find(step => /^ {10}path: tools\/vendor\/test-results\/$/m.test(step));
        assert.ok(upload, `${id} uploads tools/vendor/test-results/ (traces and blob report) after the tests`);
        assert.match(upload, /^ {8}if: always\(\)$/m, `${id} uploads them when the step failed or timed out`);
    }
});

// ---- Nightly ----------------------------------------------------------------------------------------

test('nightly runs what the gate leaves out: other Pythons, Windows, a real mount, real ComfyUI, the budget', () => {
    assert.deepEqual([...nightlyJobs.keys()].sort(),
        ['browser-workflows', 'comfyui', 'native-trash-linux', 'perf-budget', 'python-linux', 'python-windows', 'report']);
    const linux = nightlyJobs.get('python-linux');
    assert.deepEqual(matrixList(linux, 'python-version'), ['"3.13"', '"3.14"'], '3.12 is the gate');
    assert.match(linux, /^ {4}runs-on: ubuntu-latest$/m);
    const windows = nightlyJobs.get('python-windows');
    assert.deepEqual(matrixList(windows, 'python-version'), ['"3.12"', '"3.13"', '"3.14"']);
    assert.match(windows, /^ {4}runs-on: windows-latest$/m);
    for (const body of [linux, windows]) {
        assert.match(body, /^ {6}fail-fast: false$/m);
        assert.match(body, /^ {8}run: python -m tests\.run_suite -v$/m);
    }
    assert.doesNotMatch(nightly, /macos-/, 'macOS is not tested in CI (#571)');
    assert.match(nightlyJobs.get('native-trash-linux'), /PM_TRASH_MOUNT="\$RUNNER_TEMP\/drive" python -m unittest tests\.test_trash_mount_root -v$/m);
    assert.match(nightlyJobs.get('perf-budget'), /^ {10}PROMPTMANAGER_PERF_BUDGET: "1"$/m);
    const commands = [...nightlyJobs.get('comfyui').matchAll(/^ {8}run: npm run --prefix tools\/vendor (test:[a-z]+)$/gm)].map(match => match[1]);
    assert.deepEqual(commands, ['test:comfyui', 'test:executions', 'test:compat']);
});

test('nightly reports by issue after every other job, with the only write permission in CI', () => {
    const report = nightlyJobs.get('report');
    const others = [...nightlyJobs.keys()].filter(id => id !== 'report');
    assert.deepEqual([...needsOf(report)].sort(), others.sort(), 'the report waits for every other job');
    assert.match(report, jobIf("always() && github.ref == 'refs/heads/main'"), 'a failed job is reported; a branch run files nothing');
    assert.match(report, /^ {4}permissions:\n {6}actions: read\n {6}contents: read\n {6}issues: write$/m);
    assert.match(report, /^ {10}GH_TOKEN: \$\{\{ github\.token \}\}$/m);
    assert.match(report, /^ {8}run: python3 tools\/ci\/nightly_report\.py$/m);
    assert.match(nightly, /^permissions:\n {2}contents: read$/m, 'every other job only reads');
    assert.match(workflow, /^permissions:\n {2}contents: read$/m, 'the gate only reads');
    assert.doesNotMatch(workflow, /: write$/m);
    for (const [id, body] of nightlyJobs) {
        if (id !== 'report') assert.doesNotMatch(body, /^ {4}(needs|if):/m, `${id} always runs`);
    }
});

// ---- Time limits ------------------------------------------------------------------------------------

// Every job is bounded (#350): a hang ends at a measured limit instead of GitHub's 6-hour default.
// The rule (#493): ceil(1.5 x the slowest of three green runs) + 3 minutes, never under 5, with the
// runs and times written above jobs: as "<job id>: 7m26s / 7m22s / 7m54s -> 15 min". A job without
// three green runs yet has "estimate <job id>: <why> -> N min" instead. That the runs were green is
// the author's word; that every job has one line and uses its limit is checked here.
const BOUNDED_WORKFLOWS = ['ci.yml', 'nightly.yml', 'publish.yml'];
const limitOf = seconds => Math.max(5, Math.ceil(1.5 * Math.max(...seconds) / 60) + 3);

function writtenLimits(name) {
    const text = readWorkflow(name);
    const comment = text.slice(0, text.indexOf('\njobs:'));
    const runs = [...comment.matchAll(/^# Measured \d{4}-\d{2}-\d{2} on runs (\d+) \([0-9a-f]{7}\), (\d+) \([0-9a-f]{7}\), (\d+) \([0-9a-f]{7}\):$/gm)];
    assert.ok(runs.length > 0, `${name} names the runs it measured`);
    for (const run of runs) assert.equal(new Set(run.slice(1)).size, 3, `${name}: three different runs`);
    const limits = new Map();
    const time = '(\\d+)m([0-5]\\d)s';
    for (const match of comment.matchAll(new RegExp(`^#   ([\\w-]+): ${time} / ${time} / ${time} -> (\\d+) min$`, 'gm'))) {
        const seconds = [2, 4, 6].map(i => Number(match[i]) * 60 + Number(match[i + 1]));
        assert.equal(Number(match[8]), limitOf(seconds), `${name} ${match[1]}: the written limit follows the rule`);
        assert.ok(!limits.has(match[1]), `${name} ${match[1]}: one measurement, no older one beside it`);
        limits.set(match[1], Number(match[8]));
    }
    for (const match of comment.matchAll(/^# estimate ([\w-]+): [^\n]*(?:\n#   [^\n]*)*? -> (\d+) min$/gm)) {
        assert.ok(!limits.has(match[1]), `${name} ${match[1]}: a measurement or an estimate, not both`);
        assert.ok(Number(match[2]) >= 5, `${name} ${match[1]}: never under 5 minutes`);
        limits.set(match[1], Number(match[2]));
    }
    assert.equal(comment.match(/-> \d+ min/g).length, limits.size, `${name}: every "-> N min" is a job's line`);
    return limits;
}

test('every CI job uses the time limit written for it above jobs:', () => {
    for (const name of BOUNDED_WORKFLOWS) {
        const found = jobs(readWorkflow(name));
        assert.ok(found.size > 0, `${name} has jobs`);
        const limits = writtenLimits(name);
        assert.deepEqual([...limits.keys()].sort(), [...found.keys()].sort(), `${name}: one limit line per job`);
        for (const [id, body] of found) {
            const limit = /^ {4}timeout-minutes: (\d+)\s*$/m.exec(body);
            assert.ok(limit, `${name} job ${id} sets a numeric timeout-minutes`);
            assert.equal(Number(limit[1]), limits.get(id), `${name} job ${id} uses its written limit`);
        }
    }
});

test('the limit rule: 1.5 x the slowest run, rounded up, plus 3, never under 5', () => {
    assert.equal(limitOf([440, 456, 478]), 15);
    assert.equal(limitOf([20, 22, 24]), 5);
    assert.equal(limitOf([1221, 1239, 1266]), 35);
});
