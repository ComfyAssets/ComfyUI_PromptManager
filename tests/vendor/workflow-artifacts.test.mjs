import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { isAbsolute, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import workflowConfig from '../../tools/vendor/playwright.workflows.config.mjs';

const configUrl = new URL('../../tools/vendor/playwright.workflows.config.mjs', import.meta.url);
const runner = fileURLToPath(new URL('../../tools/vendor/node_modules/@playwright/test/cli.js', import.meta.url));
const testModule = new URL('../../tools/vendor/node_modules/@playwright/test/index.mjs', import.meta.url).href;

async function evidenceFiles(directory) {
    const files = [];
    for (const entry of await readdir(directory, { withFileTypes: true })) {
        const path = join(directory, entry.name);
        if (entry.isDirectory()) files.push(...await evidenceFiles(path));
        else if (entry.name === 'evidence.json') files.push(path);
    }
    return files;
}

test('sequential workflow project runs retain all four project evidence sets', async t => {
    const root = await mkdtemp(join(tmpdir(), 'pm-workflow-artifacts-'));
    t.after(() => rm(root, { recursive: true, force: true }));
    const uploadedAncestor = resolve(root, 'test-results/workflows');
    const isolateOutput = value => {
        assert.equal(typeof value, 'string');
        assert.equal(isAbsolute(value), false, 'configured output must remain relative');
        const output = resolve(root, value);
        assert.ok(output.startsWith(root + sep), 'runner output must stay inside its owned temporary root');
        const uploadedRelative = relative(uploadedAncestor, output);
        assert.ok(uploadedRelative === '' || (!uploadedRelative.startsWith('..') && !isAbsolute(uploadedRelative)),
            'project evidence must remain beneath the uploaded workflow ancestor');
        return output;
    };
    // Validate every output before launching a runner that removes output directories.
    const outputDir = isolateOutput(workflowConfig.outputDir);
    const projects = workflowConfig.projects.map(project => ({
        ...project, outputDir: isolateOutput(project.outputDir ?? workflowConfig.outputDir),
    }));
    const testDir = join(root, 'specs');
    await mkdir(testDir);
    await writeFile(join(testDir, 'evidence-retention.spec.mjs'), `
import { test } from ${JSON.stringify(testModule)};
import { writeFile } from 'node:fs/promises';
test('writes project evidence without a browser or host', async ({}, testInfo) => {
    await writeFile(testInfo.outputPath('evidence.json'), JSON.stringify({ project: testInfo.project.name }));
});
`);
    const configPath = join(root, 'playwright.config.mjs');
    await writeFile(configPath, `
import config from ${JSON.stringify(configUrl.href)};
export default { ...config, testDir: ${JSON.stringify(testDir)}, webServer: undefined,
    outputDir: ${JSON.stringify(outputDir)}, projects: ${JSON.stringify(projects)} };
`);
    const run = names => execFileSync(process.execPath, [runner, 'test', '--config', configPath,
        ...names.flatMap(name => ['--project', name])], {
        cwd: root, encoding: 'utf8', timeout: 30_000,
        env: { ...process.env, PM_JS_COVERAGE: '', NODE_V8_COVERAGE: '' },
    });
    assert.match(run(['chromium', 'touch']), /2 passed/);
    const first = new Map(await Promise.all((await evidenceFiles(uploadedAncestor)).map(async path => [path, await readFile(path, 'utf8')])));
    assert.deepEqual([...first.values()].map(value => JSON.parse(value).project).sort(), ['chromium', 'touch']);
    assert.match(run(['firefox', 'webkit']), /2 passed/);
    const retained = new Map(await Promise.all((await evidenceFiles(uploadedAncestor)).map(async path => [path, await readFile(path, 'utf8')])));
    assert.deepEqual([...retained.values()].map(value => JSON.parse(value).project).sort(), ['chromium', 'firefox', 'touch', 'webkit']);
    for (const [path, contents] of first) assert.equal(retained.get(path), contents, 'later runs preserve earlier evidence bytes and paths');
});
