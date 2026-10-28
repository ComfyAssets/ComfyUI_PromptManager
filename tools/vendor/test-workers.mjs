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

// Under CI, running-tests-reporter.mjs names the tests a step timeout's signal cut off (`list`
// cannot: they never end), and a blob report beside the traces is uploaded with them.
export function reporters({ env = process.env, name } = {}) {
    if (!env.CI) return 'list';
    return [['list'], ['./running-tests-reporter.mjs'], ['blob', { outputDir: `./test-results/blob/${name}` }]];
}
