// Fast, browser-free check that frontend-coverage-baseline.json lists exactly the
// files the coverage gate measures. CI's `frontend coverage` job reports a missing
// entry only after ~20 minutes; this reports it in milliseconds.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { repositoryRoot } from './vendor-viewerjs.mjs';
import { discoverSources } from './frontend-coverage.mjs';

const baselinePath = join(dirname(fileURLToPath(import.meta.url)), 'frontend-coverage-baseline.json');

export function measuredSources() {
    return discoverSources(join(repositoryRoot, 'web/js'));
}

export function readBaseline() {
    return JSON.parse(readFileSync(baselinePath, 'utf8'));
}

export function baselineGaps(files, baseline) {
    const measured = new Set(files);
    return {
        missing: files.filter(file => !baseline[file]),
        stale: Object.keys(baseline).filter(file => !measured.has(file)).sort(),
    };
}

function main() {
    const { missing, stale } = baselineGaps(measuredSources(), readBaseline());
    if (!missing.length && !stale.length) {
        console.log('Coverage baseline matches the measured frontend files.');
        return;
    }
    if (missing.length) console.error(`Missing baseline for:\n  ${missing.join('\n  ')}\nRun: npm run --prefix tools/vendor coverage:update`);
    if (stale.length) console.error(`Baseline lists files that no longer exist:\n  ${stale.join('\n  ')}\nRemove them from tools/vendor/frontend-coverage-baseline.json`);
    process.exitCode = 1;
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) main();
