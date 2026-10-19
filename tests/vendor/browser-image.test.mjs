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
