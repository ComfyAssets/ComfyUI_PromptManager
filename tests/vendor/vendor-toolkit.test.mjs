import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { syncToolkit } from '../../tools/vendor/vendor-toolkit.mjs';

function fixture(t) {
    const root = mkdtempSync(join(tmpdir(), 'pm-toolkit-'));
    t.after(() => rmSync(root, { recursive: true, force: true }));
    const tooling = join(root, 'tools/vendor');
    const dependency = join(tooling, 'node_modules/@comfyassets/toolkit');
    mkdirSync(join(dependency, 'ui'), { recursive: true });
    const resolved = 'https://codeload.github.com/ComfyAssets/ComfyAssets-Toolkit/tar.gz/' + 'a'.repeat(40);
    writeFileSync(join(tooling, 'package.json'), JSON.stringify({ devDependencies: { '@comfyassets/toolkit': resolved } }));
    writeFileSync(join(tooling, 'package-lock.json'), JSON.stringify({ packages: { 'node_modules/@comfyassets/toolkit': { version: '0.1.0', resolved, integrity: 'sha512-fixture' } } }));
    writeFileSync(join(dependency, 'package.json'), JSON.stringify({ name: '@comfyassets/toolkit', version: '0.1.0', license: 'MIT' }));
    for (const file of ['ui/core.js', 'ui/controls.css', 'LICENSE', 'SOURCE.md']) writeFileSync(join(dependency, file), file);
    return { root, tooling, dependency, destination: join(root, 'web/js/shared/toolkit') };
}

test('toolkit browser assets ship unchanged with deterministic locked provenance', t => {
    const f = fixture(t);
    syncToolkit({ root: f.root });
    for (const [name, source] of Object.entries({ 'core.js': 'ui/core.js', 'controls.css': 'ui/controls.css', LICENSE: 'LICENSE', 'SOURCE.md': 'SOURCE.md' })) assert.deepEqual(readFileSync(join(f.destination, name)), readFileSync(join(f.dependency, source)));
    const first = readFileSync(join(f.destination, 'provenance.json'), 'utf8');
    assert.equal(JSON.parse(first).integrity, 'sha512-fixture');
    syncToolkit({ root: f.root });
    assert.equal(readFileSync(join(f.destination, 'provenance.json'), 'utf8'), first);
    syncToolkit({ root: f.root, check: true });
    writeFileSync(join(f.destination, 'core.js'), 'local edit');
    assert.throws(() => syncToolkit({ root: f.root, check: true }), /drift/);
    assert.equal(readFileSync(join(f.destination, 'core.js'), 'utf8'), 'local edit');
});

test('toolkit sync rejects moving pins, identity mismatches and missing sources before writing', t => {
    const f = fixture(t);
    writeFileSync(join(f.tooling, 'package.json'), JSON.stringify({ devDependencies: { '@comfyassets/toolkit': 'main' } }));
    assert.throws(() => syncToolkit({ root: f.root }), /pin/);
    const second = fixture(t);
    writeFileSync(join(second.dependency, 'package.json'), JSON.stringify({ name: '@comfyassets/toolkit', version: '0.2.0', license: 'MIT' }));
    assert.throws(() => syncToolkit({ root: second.root }), /identity/);
    const third = fixture(t);
    rmSync(join(third.dependency, 'ui/controls.css'));
    assert.throws(() => syncToolkit({ root: third.root }), /ENOENT/);
    assert.throws(() => readFileSync(join(third.destination, 'core.js')), /ENOENT/);
});

test('toolkit sync rejects lookalike download hosts and archive paths before writing', t => {
    for (const prefix of [
        'https://codeloadXgithub.com/ComfyAssets/ComfyAssets-Toolkit/tar.gz/',
        'https://codeload.githubXcom/ComfyAssets/ComfyAssets-Toolkit/tar.gz/',
        'https://codeload.github.com/ComfyAssets/ComfyAssets-Toolkit/tarXgz/',
    ]) {
        const f = fixture(t);
        const resolved = prefix + 'a'.repeat(40);
        writeFileSync(join(f.tooling, 'package.json'), JSON.stringify({ devDependencies: { '@comfyassets/toolkit': resolved } }));
        writeFileSync(join(f.tooling, 'package-lock.json'), JSON.stringify({ packages: { 'node_modules/@comfyassets/toolkit': { version: '0.1.0', resolved, integrity: 'sha512-fixture' } } }));
        assert.throws(() => syncToolkit({ root: f.root }), /immutable commit pin/);
        assert.throws(() => readFileSync(join(f.destination, 'core.js')), /ENOENT/);
    }
});
