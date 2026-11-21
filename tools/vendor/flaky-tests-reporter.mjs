import { appendFileSync } from 'node:fs';
import { relative } from 'node:path';

// Lists the tests that failed and then passed on a retry (#666). Only the nightly run retries
// (`--retries 1`), so a flaky test does not turn its job red there; this reporter puts each one in the
// job summary instead, where the morning's reader sees it. The gate runs without retries: there a
// test either passes or fails, and this reporter writes nothing.
export default class FlakyTestsReporter {
    constructor({ env = process.env, append = appendFileSync } = {}) {
        this.summaryFile = env.GITHUB_STEP_SUMMARY;
        this.append = append;
        this.rootDir = process.cwd();
        this.ended = new Set();
    }

    printsToStdio() {
        return false;
    }

    onBegin(config) {
        this.rootDir = config.rootDir;
    }

    onTestEnd(test) {
        this.ended.add(test);
    }

    describe(test) {
        const project = test.parent?.project()?.name;
        const where = `${relative(this.rootDir, test.location.file)}:${test.location.line}`;
        return `${project ? `[${project}] › ` : ''}${where} › ${test.title}`;
    }

    flaky() {
        return [...this.ended].filter(test => test.outcome() === 'flaky').map(test => this.describe(test)).sort();
    }

    onEnd() {
        const flaky = this.flaky();
        if (!flaky.length || !this.summaryFile) return;
        const lines = flaky.map(line => `- \`${line.replaceAll('`', "'")}\``);
        this.append(this.summaryFile, `### Flaky tests (failed, then passed on the retry): ${flaky.length}\n\n${lines.join('\n')}\n\n`);
    }
}
