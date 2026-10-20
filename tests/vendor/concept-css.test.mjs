import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync, copyFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { lintOverlay } from '../../tools/vendor/sync-concept-css.mjs';

const root = join(dirname(fileURLToPath(import.meta.url)), '../..');
const script = join(root, 'tools/vendor/sync-concept-css.mjs');
const concept = join(root, 'docs/concepts/workspace/style.css');
const copy = join(root, 'web/js/workspace/concept.css');
const sha = path => createHash('sha256').update(readFileSync(path)).digest('hex');

function walk(dir) {
    return readdirSync(dir, { withFileTypes: true }).flatMap(entry => {
        const path = join(dir, entry.name);
        return entry.isDirectory() ? walk(path) : [path];
    });
}

test('concept copy is byte-identical', () => {
    assert.ok(existsSync(copy), 'web/js/workspace/concept.css is missing; run sync-concept-css.mjs --write');
    assert.equal(sha(copy), sha(concept));
    const result = spawnSync(process.execPath, [script, '--check'], { cwd: root, encoding: 'utf8' });
    assert.equal(result.status, 0, result.stderr);
});

test('check fails on a one-byte drift', () => {
    const temp = mkdtempSync(join(tmpdir(), 'concept-css-'));
    try {
        mkdirSync(join(temp, 'docs/concepts/workspace'), { recursive: true });
        mkdirSync(join(temp, 'web/js/workspace'), { recursive: true });
        copyFileSync(concept, join(temp, 'docs/concepts/workspace/style.css'));
        writeFileSync(join(temp, 'web/js/workspace/concept.css'), Buffer.concat([readFileSync(concept), Buffer.from(' ')]));
        const run = () => spawnSync(process.execPath, [script, '--check', '--root', temp], { encoding: 'utf8' });
        const drifted = run();
        assert.equal(drifted.status, 1);
        assert.match(drifted.stderr, /drift/i);
        assert.equal(spawnSync(process.execPath, [script, '--write', '--root', temp]).status, 0);
        assert.equal(run().status, 0);
    } finally { rmSync(temp, { recursive: true, force: true }); }
});

test('overlay lint requires a decision marker on concept selectors', () => {
    const conceptCss = '.card, .row { color: red; }\n@media (max-width: 700px) { .card .title { margin: 0; } }\n';
    assert.deepEqual(lintOverlay(conceptCss, '.error-row { color: red; }').violations, []);
    assert.equal(lintOverlay(conceptCss, '.x, .card { color: blue; }').violations.length, 1);
    assert.equal(lintOverlay(conceptCss, '@media (max-width: 700px) { .card .title { margin: 1px; } }').violations.length, 1);
    assert.deepEqual(lintOverlay(conceptCss, '/* D12 */\n.card { color: blue; }').violations, []);
    assert.deepEqual(lintOverlay(conceptCss, '.card { /* D3 */ color: blue; }').violations, []);
    assert.equal(lintOverlay(conceptCss, '/* tweak */ .card { color: blue; }').violations.length, 1);
    assert.equal(lintOverlay(conceptCss, '/* D1 */ .card { color: blue; }\n.row { gap: 1px; }').violations.length, 1);
});

test('overlay rules on concept selectors carry a decision marker', () => {
    const overlay = join(root, 'web/js/workspace/workspace.css');
    assert.ok(existsSync(overlay), 'web/js/workspace/workspace.css is missing');
    const { violations } = lintOverlay(readFileSync(concept, 'utf8'), readFileSync(overlay, 'utf8'));
    assert.deepEqual(violations, []);
});

test('only workspace.html loads workspace CSS', () => {
    const offenders = walk(join(root, 'web'))
        .filter(path => /\.(html|js|mjs)$/.test(path) && !path.includes('/lib/') && !path.includes('/shared/'))
        .filter(path => /\b(concept|workspace)\.css\b/.test(readFileSync(path, 'utf8')))
        .map(path => path.slice(root.length + 1));
    assert.ok(offenders.every(path => path === 'web/workspace.html'), `unexpected consumers: ${offenders.join(', ')}`);
});

// Task A2 order: the concept's own order (toolkit, Viewer.js, concept) with the page stylesheets that their own PRs
// retire, then the production overlay last.
test('workspace.html loads controls, viewer, concept, page CSS and the overlay in order', () => {
    const html = readFileSync(join(root, 'web/workspace.html'), 'utf8');
    const hrefs = [...html.matchAll(/<link[^>]+rel="stylesheet"[^>]+href="([^"]+)"/g)].map(match => match[1]);
    assert.deepEqual(hrefs, [
        'js/shared/toolkit/controls.css',
        'lib/viewerjs/viewer.min.css',
        'js/workspace/concept.css',
        'js/workspace/images.css',
        'js/workspace/library.css',
        'js/workspace/inspector.css',
        'js/workspace/workspace.css',
    ]);
});

test('the retired shell, vocabulary and editor stylesheets stay deleted', () => {
    for (const name of ['style.css', 'vocabulary.css', 'library-editor.css']) {
        assert.equal(existsSync(join(root, 'web/js/workspace', name)), false, `web/js/workspace/${name} must not come back`);
    }
});
