import { existsSync, readFileSync, readdirSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';
import { repositoryRoot } from './vendor-viewerjs.mjs';

import { discoverSources, checkCoverage } from '@comfyassets/toolkit/testing/coverage';
export { discoverSources, checkCoverage };
const metrics = ['lines', 'branches', 'functions', 'statements'];
const SHARD = /^([1-9]\d*)\/([1-9]\d*)$/;
const USAGE = 'usage: frontend-coverage.mjs [--update-baseline] | --shard I/N | --merge-only [--update-baseline]';

function runNode(args, env = {}) {
    const result = spawnSync(process.execPath, args, { cwd: repositoryRoot, stdio: 'inherit',
        env: { ...process.env, ...env } });
    if (result.status !== 0) throw new Error(`Command failed (${result.status}): ${args.join(' ')}`);
}

function defaultPaths() {
    const reportRoot = join(repositoryRoot, 'coverage/js');
    return { repositoryRoot, tools: dirname(fileURLToPath(import.meta.url)), reportRoot,
        raw: join(reportRoot, 'raw'), sourceRoot: join(repositoryRoot, 'web/js') };
}

function parseShard(value) {
    const match = SHARD.exec(value ?? '');
    if (!match || Number(match[1]) > Number(match[2])) {
        throw new Error(`--shard needs I/N with 1 <= I <= N, got ${value ?? 'nothing'}\n${USAGE}`);
    }
    return { index: Number(match[1]), total: Number(match[2]), value };
}

// Turn the command line into the ordered steps of one coverage run:
// - no mode: the full run (clean, node units, viewer suite, chromium+touch
//   workflows, report, check), exactly as before shards existed;
// - --shard I/N: one slice of the workflows into coverage/js/raw, without wiping
//   it or reporting; shard 1 also runs the node units and the viewer suite;
// - --merge-only: report and check over raw coverage the shards already wrote.
export function plan(argv) {
    let shard = null;
    let mergeOnly = false;
    let updateBaseline = false;
    for (let index = 0; index < argv.length; index += 1) {
        const arg = argv[index];
        if (arg === '--update-baseline') updateBaseline = true;
        else if (arg === '--merge-only') mergeOnly = true;
        else if (arg === '--shard' || arg.startsWith('--shard=')) {
            if (shard) throw new Error(`Pass --shard once\n${USAGE}`);
            shard = parseShard(arg === '--shard' ? argv[(index += 1)] : arg.slice('--shard='.length));
        } else throw new Error(`Unknown argument: ${arg}\n${USAGE}`);
    }
    if (shard && mergeOnly) throw new Error(`Pass either --shard or --merge-only\n${USAGE}`);
    if (shard && updateBaseline) throw new Error(`--update-baseline needs --merge-only or a full run, not a shard\n${USAGE}`);
    const check = { step: 'check', updateBaseline };
    if (mergeOnly) return [{ step: 'require-raw' }, { step: 'report' }, check];
    if (shard) {
        const first = shard.index === 1 ? [{ step: 'node-units' }, { step: 'viewer' }] : [];
        return [{ step: 'prepare' }, ...first, { step: 'workflows', shard: shard.value }];
    }
    return [{ step: 'clean' }, { step: 'node-units' }, { step: 'viewer' }, { step: 'workflows', shard: null },
        { step: 'report' }, check];
}

function rawCoverageFiles(raw) {
    return existsSync(raw) ? readdirSync(raw).filter(file => file.endsWith('.json')) : [];
}

function checkSummary({ tools, reportRoot, sourceRoot }, updateBaseline) {
    const files = discoverSources(sourceRoot);
    const summary = JSON.parse(readFileSync(join(reportRoot, 'coverage-summary.json'), 'utf8'));
    const baselinePath = join(tools, 'frontend-coverage-baseline.json');
    if (updateBaseline) {
        const previous = JSON.parse(readFileSync(baselinePath, 'utf8'));
        const failures = checkCoverage(summary, previous, sourceRoot, files.filter(file => previous[file]));
        if (failures.length) throw new Error(`Baseline updates cannot lower coverage:\n${failures.join('\n')}`);
        const next = Object.fromEntries(files.map(file => {
            const measured = summary[join(sourceRoot, file)];
            if (!measured || metrics.some(metric => !Number.isFinite(measured[metric]?.pct))) {
                throw new Error(`Missing coverage for ${file}`);
            }
            if (Object.keys(previous).length && !previous[file] && measured.lines.pct < 80) {
                throw new Error(`New frontend files need at least 80% line coverage: ${file}`);
            }
            return [file, Object.fromEntries(metrics.map(metric => [metric, Math.max(previous[file]?.[metric] || 0, Math.floor(measured[metric].pct) - 2, 0)]))];
        }));
        writeFileSync(baselinePath, JSON.stringify(next, null, 2) + '\n');
    } else {
        const baseline = JSON.parse(readFileSync(baselinePath, 'utf8'));
        const failures = checkCoverage(summary, baseline, sourceRoot, files);
        if (failures.length) throw new Error(`Frontend coverage regressed:\n${failures.join('\n')}`);
    }
    return files.length;
}

export async function runCoverage({ argv = process.argv.slice(2), run = runNode, paths = defaultPaths() } = {}) {
    const { repositoryRoot: root, tools, reportRoot, raw, sourceRoot } = paths;
    const playwright = join(tools, 'node_modules/@playwright/test/cli.js');
    const actions = {
        clean() {
            rmSync(reportRoot, { recursive: true, force: true });
            mkdirSync(raw, { recursive: true });
        },
        prepare() {
            mkdirSync(raw, { recursive: true });
        },
        'require-raw'() {
            if (!rawCoverageFiles(raw).length) {
                throw new Error(`No raw coverage in ${relative(root, raw) || raw}: run the --shard steps first, `
                    + 'or put every shard\'s raw files there');
            }
        },
        'node-units'() {
            const tests = readdirSync(join(root, 'tests/js')).filter(file => file.endsWith('.test.js'))
                .map(file => `tests/js/${file}`);
            run(['--test', ...tests], { NODE_V8_COVERAGE: raw });
        },
        viewer() {
            run([playwright, 'test', '--config', join(tools, 'playwright.config.mjs')]);
        },
        workflows({ shard }) {
            run([playwright, 'test', '--config', join(tools, 'playwright.workflows.config.mjs'),
                '--project', 'chromium', '--project', 'touch', ...(shard ? ['--shard', shard] : [])], { PM_JS_COVERAGE: raw });
        },
        report() {
            run([join(tools, 'node_modules/c8/bin/c8.js'), 'report', '--all', '--src', sourceRoot,
                '--include', 'web/js/**/*.js', '--include', 'web/js/**/*.mjs', '--temp-directory', raw, '--reports-dir', reportRoot,
                '--reporter', 'text', '--reporter', 'json-summary', '--reporter', 'html']);
        },
        check({ updateBaseline }) {
            const count = checkSummary(paths, updateBaseline);
            console.log(`Frontend coverage passed for ${count} files. Report: ${relative(root, reportRoot)}/index.html`);
        },
    };
    for (const { step, ...options } of plan(argv)) await actions[step](options);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
    runCoverage().catch(error => { console.error(error.message); process.exitCode = 1; });
}
