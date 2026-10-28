import { writeSync } from 'node:fs';
import { relative } from 'node:path';

// Names the tests still running when a run is cut off (#425). A browser test step that reaches its
// timeout-minutes gets SIGTERM, which ends Playwright at once: no reporter's onEnd runs, so neither
// `list` nor the blob report says what was running. On the first SIGTERM this reporter prints every
// started test that has not ended, then turns the signal into SIGINT, Playwright's own interrupt,
// which stops the workers, ends the run as interrupted (non-zero exit) and writes the blob report.
// A second SIGTERM gets the default behaviour: the process dies.
export default class RunningTestsReporter {
    constructor({ now = () => Date.now(), write = text => writeSync(1, text), host = process } = {}) {
        this.now = now;
        this.write = write;
        this.host = host;
        this.rootDir = host.cwd();
        this.running = new Map();
        this.onSigterm = () => {
            this.host.removeListener('SIGTERM', this.onSigterm);
            this.report('SIGTERM');
            this.host.kill(this.host.pid, 'SIGINT');
        };
    }

    printsToStdio() {
        return false;
    }

    onBegin(config) {
        this.rootDir = config.rootDir;
        this.host.on('SIGTERM', this.onSigterm);
    }

    onTestBegin(test, result) {
        this.running.set(`${test.id}#${result.retry}`, { test, startedAt: this.now() });
    }

    onTestEnd(test, result) {
        this.running.delete(`${test.id}#${result.retry}`);
    }

    describe({ test, startedAt }) {
        const project = test.parent?.project()?.name;
        const where = `${relative(this.rootDir, test.location.file)}:${test.location.line}`;
        const seconds = ((this.now() - startedAt) / 1000).toFixed(1);
        return `${project ? `[${project}] › ` : ''}${where} › ${test.title} (running ${seconds}s)`;
    }

    report(reason) {
        if (!this.running.size) return;
        const lines = [...this.running.values()].map(entry => `  ${this.describe(entry)}`);
        this.write(`\nStill running when the run ended (${reason}): ${this.running.size}\n${lines.join('\n')}\n`);
    }

    onEnd(result) {
        this.host.removeListener('SIGTERM', this.onSigterm);
        this.report(result.status);
    }
}
