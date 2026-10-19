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
    });
}
