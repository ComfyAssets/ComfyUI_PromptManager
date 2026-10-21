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

test('overlay lint accepts only this spec\'s (#249) decision numbers', () => {
    const conceptCss = '.card { color: red; }\n';
    assert.deepEqual(lintOverlay(conceptCss, '/* D12 of #249 */ .card { color: blue; }').violations, []);
    assert.deepEqual(lintOverlay(conceptCss, '/* #249 D3: reason */ .card { color: blue; }').violations, []);
    // Another issue's decision numbers are not #249 rulings, so they never mark a concept override.
    assert.equal(lintOverlay(conceptCss, '/* D5 (#245) */ .card { color: blue; }').violations.length, 1);
    assert.equal(lintOverlay(conceptCss, '/* D2 of #240 and #249 */ .card { color: blue; }').violations.length, 1);
    assert.equal(lintOverlay(conceptCss, '/* #245 decision 5 */ .card { color: blue; }').violations.length, 1);
    assert.equal(lintOverlay(conceptCss, '/* AD5 */ .card { color: blue; }').violations.length, 1);
});

test('overlay rules on concept selectors carry a decision marker', () => {
    const overlay = join(root, 'web/js/workspace/workspace.css');
    assert.ok(existsSync(overlay), 'web/js/workspace/workspace.css is missing');
    const { violations } = lintOverlay(readFileSync(concept, 'utf8'), readFileSync(overlay, 'utf8'));
    assert.deepEqual(violations, []);
});

// #249 PR F-b Task 3: every page is rebuilt, so no rule waits for a page PR any more and the history page's
// production-only rules live in the overlay in the concept's tokens (D48). A comment that opens with a page PR
// ("PR F: ...", "PR E/F: ...") or defers to one ("until ... PR", "retired in PR F") is a waiting rule; a provenance
// label inside a heading ("(#249 PR E)") is not.
const WAITING_RULE = [
    /\/\*\s*PR [A-F](\/[A-F]|-[ab])?\b/i,
    /\buntil\b[^*]*\b(PR [A-F]?|rebuild)/i,
    /\bretired in PR\b/i,
    /\binterim page guards\b/i,
];
const waitingRules = css => WAITING_RULE.filter(pattern => pattern.test(css)).map(String);

test('the waiting-rule guard catches the retired comments and spares provenance labels', () => {
    // The exact comments this PR retired (workspace.css and library.css at 10a4847).
    for (const retired of [
        '/* Interim page guards: concept element rules (`.ca-tools dialog`, `pre`) now reach production dialogs and prompt text',
        '   that keep their own design until their page PR rebuilds them in concept markup. */',
        '.ca-tools dialog.prompt-sender::backdrop { background: #000b; } /* PR D */',
        '.ca-tools dialog.recorded-run-dialog { padding: 16px; } /* PR F */',
        '    .ca-tools .recorded-history .history-toolbar button { min-height: 44px; } /* PR F: concept coarse buttons are 40px */',
        '/* Until their pages rebuild (PR F history): the history page\'s and the recorded run\'s prompt blocks keep their',
        '/* PR E/F: the Archive dialog and the frozen field provenance keep `pre.prompt-text`, guarded against the concept `pre` rule. */',
        "   keeps the concept's) and are retired in PR F (history). */",
    ]) assert.notDeepEqual(waitingRules(retired), [], retired);
    for (const kept of [
        "/* Prompt library cards (#249 PR E): the concept's `article.prompt-card` (s653–708) with real prompts, titles and media. */",
        '/* History variants panel (#249 PR F). */',
    ]) assert.deepEqual(waitingRules(kept), [], kept);
});

test('the overlay keeps no rule waiting for a page rebuild', () => {
    const css = readFileSync(join(root, 'web/js/workspace/workspace.css'), 'utf8');
    assert.deepEqual(waitingRules(css), []);
    for (const retired of ['.library-chip', '--library-positive', '--library-negative', '--prompt-role']) {
        assert.equal(css.includes(retired), false, `${retired} is retired`);
    }
});

// #249 C8 and D3: the Images page and the inspector render the concept DOM, so the replaced production rules stay
// retired; the few production-only rules still consumed live in the overlay.
test('the overlay keeps no retired Images page or inspector rules', () => {
    const css = readFileSync(join(root, 'web/js/workspace/workspace.css'), 'utf8');
    // `.image-grid` and `.image-card-actions` are concept selectors now, so only the retired production names are listed.
    for (const retired of ['.image-facts', '.image-filters', '.image-toolbar', '.image-preview', '.image-collection', '.image-continuation',
        '.image-details', '.file-cover', '.file-inspector-inner', '.file-title', '.file-chip', '.file-facts', '.generation-facts', '.file-inspector-footer']) {
        assert.equal(css.includes(retired), false, `${retired} is retired`);
    }
});

test('only workspace.html loads workspace CSS', () => {
    const offenders = walk(join(root, 'web'))
        .filter(path => /\.(html|js|mjs)$/.test(path) && !path.includes('/lib/') && !path.includes('/shared/'))
        .filter(path => /\b(concept|workspace)\.css\b/.test(readFileSync(path, 'utf8')))
        .map(path => path.slice(root.length + 1));
    assert.ok(offenders.every(path => path === 'web/workspace.html'), `unexpected consumers: ${offenders.join(', ')}`);
});

// Task A2 order: the concept's own order (toolkit, Viewer.js, concept), then the production overlay last; the page
// stylesheets that waited for their PRs are all retired (PR F-b deleted the last, library.css).
test('workspace.html loads controls, viewer, concept and the overlay in order', () => {
    const html = readFileSync(join(root, 'web/workspace.html'), 'utf8');
    const hrefs = [...html.matchAll(/<link[^>]+rel="stylesheet"[^>]+href="([^"]+)"/g)].map(match => match[1]);
    assert.deepEqual(hrefs, [
        'js/shared/toolkit/controls.css',
        'lib/viewerjs/viewer.min.css',
        'js/workspace/concept.css',
        'js/workspace/workspace.css',
    ]);
});

test('the retired shell, vocabulary, editor, Images, inspector and library stylesheets stay deleted', () => {
    for (const name of ['style.css', 'vocabulary.css', 'library-editor.css', 'images.css', 'inspector.css', 'library.css']) {
        assert.equal(existsSync(join(root, 'web/js/workspace', name)), false, `web/js/workspace/${name} must not come back`);
    }
});
