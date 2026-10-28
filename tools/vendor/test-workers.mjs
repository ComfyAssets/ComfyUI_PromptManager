import { availableParallelism } from 'node:os';

const MAX_LOCAL_WORKERS = 8;
const CPUS_PER_WORKER = 4;

// PM_TEST_WORKERS wins; CI without it keeps one worker, so CI only changes
// through explicit workflow edits; locally a quarter of the CPUs, at most eight.
export function workflowWorkers({ env = process.env, cpus = availableParallelism() } = {}) {
    if (env.PM_TEST_WORKERS !== undefined) {
        const workers = Number(env.PM_TEST_WORKERS);
        if (!Number.isInteger(workers) || workers < 1) {
            throw new Error(`PM_TEST_WORKERS must be a positive integer, got "${env.PM_TEST_WORKERS}"`);
        }
        return workers;
    }
    if (env.CI) return 1;
    return Math.max(1, Math.min(MAX_LOCAL_WORKERS, Math.floor(cpus / CPUS_PER_WORKER)));
}

const MINUTE_MS = 60_000;

// Playwright's globalTimeout (#425): under CI the run stops itself before the step and job limits in
// viewerjs-check.yml, so it names the tests still running instead of being killed with no log.
// `variable` (minutes, may be fractional) overrides it anywhere; locally there is no limit.
export function globalTimeoutMs({ env = process.env, ciMinutes, variable } = {}) {
    const override = variable === undefined ? undefined : env[variable];
    if (override !== undefined) {
        const minutes = Number(override);
        if (!Number.isFinite(minutes) || minutes <= 0) {
            throw new Error(`${variable} must be a positive number of minutes, got "${override}"`);
        }
        return Math.round(minutes * MINUTE_MS);
    }
    return env.CI ? ciMinutes * MINUTE_MS : 0;
}

// Under CI, running-tests-reporter.mjs names the tests a globalTimeout or a signal cut off (`list`
// cannot: they never end), and a blob report beside the traces is uploaded with them.
export function reporters({ env = process.env, name } = {}) {
    if (!env.CI) return 'list';
    return [['list'], ['./running-tests-reporter.mjs'], ['blob', { outputDir: `./test-results/blob/${name}` }]];
}
