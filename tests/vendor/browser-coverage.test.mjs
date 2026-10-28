import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { saveCoverage } from '../../tools/vendor/browser-coverage.mjs';

test('browser coverage maps nested JS/MJS and excludes unrelated and traversal paths', async t => {
    const directory = mkdtempSync(join(tmpdir(), 'pm-browser-coverage-'));
    const previous = process.env.PM_JS_COVERAGE;
    process.env.PM_JS_COVERAGE = directory;
    t.after(() => {
        if (previous === undefined) delete process.env.PM_JS_COVERAGE;
        else process.env.PM_JS_COVERAGE = previous;
        rmSync(directory, { recursive: true, force: true });
    });
    const urls = [
        '/prompt_manager/js/gallery.js',
        '/prompt_manager/js/shared/controls/button.mjs?v=1',
        '/prompt_manager/js/shared/panel.js',
        '/scripts/app.js', '/prompt_manager/js/../private.js',
        '/prompt_manager/js/shared/theme.css',
    ];
    await saveCoverage({ coverage: { stopJSCoverage: async () => urls.map(url => ({ url: `http://127.0.0.1:8765${url}`, functions: [] })) } }, 'chromium');
    const report = JSON.parse(readFileSync(join(directory, readdirSync(directory)[0]), 'utf8'));
    const root = fileURLToPath(new URL('../../web/js/', import.meta.url));
    assert.deepEqual(report.result.map(entry => fileURLToPath(entry.url)), [join(root, 'gallery.js'), join(root, 'shared/controls/button.mjs'), join(root, 'shared/panel.js')]);
});

// #432: every raw file repeated each script's full source (about 84% of its bytes) and the coverage merge ran out of
// memory. c8 reads the source from the mapped file on disk, so the entries keep only the URL, id and functions.
test('browser coverage writes no script source, only what c8 maps', async t => {
    const directory = mkdtempSync(join(tmpdir(), 'pm-browser-coverage-'));
    const previous = process.env.PM_JS_COVERAGE;
    process.env.PM_JS_COVERAGE = directory;
    t.after(() => {
        if (previous === undefined) delete process.env.PM_JS_COVERAGE;
        else process.env.PM_JS_COVERAGE = previous;
        rmSync(directory, { recursive: true, force: true });
    });
    const functions = [{ functionName: 'f', isBlockCoverage: true, ranges: [{ startOffset: 0, endOffset: 9, count: 1 }] }];
    await saveCoverage({ coverage: { stopJSCoverage: async () => [{ url: 'http://127.0.0.1:8765/prompt_manager/js/gallery.js',
        scriptId: '7', source: 'x'.repeat(100_000), functions }] } }, 'chromium');
    const report = JSON.parse(readFileSync(join(directory, readdirSync(directory)[0]), 'utf8'));
    assert.equal(report.result.length, 1);
    assert.deepEqual(Object.keys(report.result[0]).sort(), ['functions', 'scriptId', 'url']);
    assert.deepEqual(report.result[0].functions, functions);
});
