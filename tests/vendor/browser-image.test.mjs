import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// Concept comparisons measure layout, and layout depends on the fonts a browser falls back to (the concept names Inter,
// which neither environment ships). Page-workflow jobs therefore run in the same pinned Playwright image as local runs,
// so CI and a developer machine resolve identical fonts and engines.
const workflow = readFileSync(new URL('../../.github/workflows/viewerjs-check.yml', import.meta.url), 'utf8');
const lock = JSON.parse(readFileSync(new URL('../../tools/vendor/package-lock.json', import.meta.url), 'utf8'));
const version = lock.packages['node_modules/@playwright/test'].version;

function job(name) {
    const match = workflow.match(new RegExp(`^  ${name}:\\n([\\s\\S]*?)(?=^  [a-z][a-z-]*:\\n|(?![\\s\\S]))`, 'm'));
    assert.ok(match, `${name} job exists`);
    return match[1];
}

for (const name of ['frontend-coverage', 'browser-workflows']) {
    test(`${name} runs in the pinned Playwright image matching @playwright/test`, () => {
        const body = job(name);
        assert.match(body, new RegExp(`^    container:\\n      image: mcr\\.microsoft\\.com/playwright:v${version.replaceAll('.', '\\.')}-noble$`, 'm'));
        assert.doesNotMatch(body, /playwright install/, 'browsers and their fonts come from the image, not a runner install');
        // Toolcache interpreters are the runner's own builds: their entry points name host paths that do not exist in
        // the container (setup-python failed with `spawn .../bin/pip ENOENT`). Use the image's Python and Node.
        assert.doesNotMatch(body, /actions\/setup-(python|node)@/);
        assert.match(body, /python3 -m venv --without-pip /, 'a user-space venv on the image Python (no ensurepip, no root)');
        assert.match(body, /sha256sum -c/, 'the pip wheel that bootstraps the venv is checksum-pinned');
        assert.match(body, /node --version \| grep -q '\^v24\\\.'/, 'the image Node is the Node 24 the project requires');
    });
}

// The real-host job drives Chromium against a managed ComfyUI. `playwright install --with-deps` pulled the browser's
// system packages from Ubuntu's apt mirror on every run, and three runs (#349, #378, #382) crawled at ~26 kB/s until
// the job hit its limit after every test had passed. The pinned image ships Chromium and its libraries, so the job runs
// there; the image Python must be the reviewed host Python from tools/vendor/comfyui-runtime.json.
test('real ComfyUI compatibility runs in the pinned Playwright image on the reviewed host Python', () => {
    const testWorkflow = readFileSync(new URL('../../.github/workflows/test.yml', import.meta.url), 'utf8');
    const runtime = JSON.parse(readFileSync(new URL('../../tools/vendor/comfyui-runtime.json', import.meta.url), 'utf8'));
    const match = testWorkflow.match(/^ {2}comfyui:\n([\s\S]*?)(?=^ {2}[a-z][a-z-]*:\n|(?![\s\S]))/m);
    assert.ok(match, 'comfyui job exists');
    const body = match[1];
    assert.match(body, new RegExp(`^    container:\\n      image: mcr\\.microsoft\\.com/playwright:v${version.replaceAll('.', '\\.')}-noble$`, 'm'));
    assert.doesNotMatch(body, /playwright install/, 'Chromium and its system libraries come from the image, not apt');
    assert.doesNotMatch(body, /apt-get|actions\/setup-(python|node)@/);
    assert.match(body, /python3 -m venv --without-pip /, 'a user-space venv on the image Python');
    assert.match(body, /sha256sum -c/, 'the pip wheel that bootstraps the venv is checksum-pinned');
    assert.match(body, /node --version \| grep -q '\^v24\\\.'/, 'the image Node is Node 24');
    // The image Python is checked against the pin at run time, so a pin or image bump that disagrees fails loudly.
    assert.match(body, /steps\.runtime\.outputs\.pythonVersion/, 'the job compares the image Python with the reviewed pin');
    assert.match(runtime.pythonVersion, /^3\.12$/, 'the reviewed host Python is the image Python (3.12 on noble)');
});
