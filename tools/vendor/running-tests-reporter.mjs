import { relative } from 'node:path';

// Names the tests still running when a run ends (#425). When globalTimeout fires, Playwright's own
// teardown deadline has already passed, so a test stuck in a worker never gets onTestEnd and no
// built-in reporter lists it. This one remembers every started test and prints those that never
// ended, with how long each had been running, at the end of the log.
export default class RunningTestsReporter {
    constructor({ now = () => Date.now(), write = text => process.stdout.write(text), cwd = process.cwd() } = {}) {
        this.now = now;
        this.write = write;
        this.cwd = cwd;
        this.running = new Map();
    }

    printsToStdio() {
        return false;
    }

    onTestBegin(test, result) {
        this.running.set(`${test.id}#${result.retry}`, { test, startedAt: this.now() });
    }

    onTestEnd(test, result) {
        this.running.delete(`${test.id}#${result.retry}`);
    }

    describe({ test, startedAt }) {
        const project = test.parent?.project()?.name;
        const where = `${relative(this.cwd, test.location.file)}:${test.location.line}`;
        const seconds = ((this.now() - startedAt) / 1000).toFixed(1);
        return `${project ? `[${project}] › ` : ''}${where} › ${test.title} (running ${seconds}s)`;
    }

    onEnd(result) {
        if (!this.running.size) return;
        const lines = [...this.running.values()].map(entry => `  ${this.describe(entry)}`);
        this.write(`\nStill running when the run ended (${result.status}): ${this.running.size}\n${lines.join('\n')}\n`);
    }
}
