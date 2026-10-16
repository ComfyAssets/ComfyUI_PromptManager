import { readFileSync, readdirSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';
import { repositoryRoot } from './vendor-viewerjs.mjs';

const metrics = ['lines', 'branches', 'functions', 'statements'];
export function checkCoverage(summary, baseline, sourceRoot, files) {
    const failures = [];
    for (const file of files) {
        const floor = baseline[file];
        if (!floor) { failures.push(`Missing baseline for ${file}`); continue; }
        const measured = summary[join(sourceRoot, file)];
        if (!measured) { failures.push(`Missing coverage for ${file}`); continue; }
        for (const metric of metrics) {
            if (!Number.isFinite(floor[metric]) || floor[metric] < 0 || floor[metric] > 100) {
                throw new Error(`Invalid baseline for ${file}: ${metric}`);
            }
            if (!Number.isFinite(measured[metric]?.pct) || measured[metric].pct < floor[metric]) {
                failures.push(`${file} ${metric}: ${measured[metric]?.pct ?? 'missing'} < ${floor[metric]}`);
            }
        }
    }
    return failures;
}

function run(args, env = {}) {
    const result = spawnSync(process.execPath, args, { cwd: repositoryRoot, stdio: 'inherit',
        env: { ...process.env, ...env } });
    if (result.status !== 0) throw new Error(`Command failed (${result.status}): ${args.join(' ')}`);
}

async function main() {
    const tools = dirname(fileURLToPath(import.meta.url));
    const reportRoot = join(repositoryRoot, 'coverage/js');
    const raw = join(reportRoot, 'raw');
    const sourceRoot = join(repositoryRoot, 'web/js');
    const files = readdirSync(sourceRoot).filter(file => file.endsWith('.js')).sort();
    rmSync(reportRoot, { recursive: true, force: true });
    mkdirSync(raw, { recursive: true });
    const tests = readdirSync(join(repositoryRoot, 'tests/js')).filter(file => file.endsWith('.test.js')).map(file => `tests/js/${file}`);
    run(['--test', ...tests], { NODE_V8_COVERAGE: raw });
    const playwright = join(tools, 'node_modules/@playwright/test/cli.js');
    run([playwright, 'test', '--config', join(tools, 'playwright.config.mjs')]);
    run([playwright, 'test', '--config', join(tools, 'playwright.workflows.config.mjs'),
        '--project', 'chromium', '--project', 'touch'], { PM_JS_COVERAGE: raw });
    run([join(tools, 'node_modules/c8/bin/c8.js'), 'report', '--all', '--src', sourceRoot,
        '--include', 'web/js/*.js', '--temp-directory', raw, '--reports-dir', reportRoot,
        '--reporter', 'text', '--reporter', 'json-summary', '--reporter', 'html']);
    const summary = JSON.parse(readFileSync(join(reportRoot, 'coverage-summary.json'), 'utf8'));
    const baselinePath = join(tools, 'frontend-coverage-baseline.json');
    if (process.argv.includes('--update-baseline')) {
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
    console.log(`Frontend coverage passed for ${files.length} files. Report: ${relative(repositoryRoot, reportRoot)}/index.html`);
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
    main().catch(error => { console.error(error.message); process.exitCode = 1; });
}
