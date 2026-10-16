import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { syncViewer } from '../../tools/vendor/vendor-viewerjs.mjs';
import { updateViewer } from '../../tools/vendor/update-viewerjs.mjs';

function fixture(t) {
    const root = mkdtempSync(join(tmpdir(), 'pm-vendor-'));
    t.after(() => rmSync(root, { recursive: true, force: true }));
    const tooling = join(root, 'tools/vendor');
    const dependency = join(tooling, 'node_modules/viewerjs');
    mkdirSync(join(dependency, 'dist'), { recursive: true });
    writeFileSync(join(tooling, 'package.json'), JSON.stringify({ devDependencies: { viewerjs: '1.15.2' } }));
    writeFileSync(join(tooling, 'package-lock.json'), JSON.stringify({ packages: {
        'node_modules/viewerjs': { version: '1.15.2', resolved: 'https://registry.npmjs.org/viewerjs/-/viewerjs-1.15.2.tgz', integrity: 'sha512-fixture' },
    } }));
    writeFileSync(join(dependency, 'package.json'), JSON.stringify({ name: 'viewerjs', version: '1.15.2', license: 'MIT' }));
    for (const name of ['viewer.min.js', 'viewer.min.css']) {
        writeFileSync(join(dependency, 'dist', name), `/*! Viewer.js v1.15.2 */\n${name}`);
    }
    writeFileSync(join(dependency, 'LICENSE'), 'The MIT License (MIT)\nCopyright Chen Fengyuan\n');
    return { root, tooling, dependency, destination: join(root, 'web/lib/viewerjs') };
}

test('sync copies pristine assets and license and records locked provenance deterministically', t => {
    const f = fixture(t);
    syncViewer({ root: f.root });
    const first = readFileSync(join(f.destination, 'provenance.json'), 'utf8');
    const record = JSON.parse(first);
    assert.equal(record.version, '1.15.2');
    assert.equal(record.integrity, 'sha512-fixture');
    for (const [target, source] of Object.entries({ 'viewer.min.js': 'dist/viewer.min.js', 'viewer.min.css': 'dist/viewer.min.css', LICENSE: 'LICENSE' })) {
        const original = readFileSync(join(f.dependency, source));
        assert.deepEqual(readFileSync(join(f.destination, target)), original);
        assert.equal(record.files[target].sha256, createHash('sha256').update(original).digest('hex'));
    }
    syncViewer({ root: f.root });
    assert.equal(readFileSync(join(f.destination, 'provenance.json'), 'utf8'), first);
    syncViewer({ root: f.root, check: true });
});

test('check rejects drift without overwriting local changes', t => {
    const f = fixture(t);
    syncViewer({ root: f.root });
    writeFileSync(join(f.destination, 'viewer.min.js'), 'local edit');
    assert.throws(() => syncViewer({ root: f.root, check: true }), /viewer.min.js/);
    assert.equal(readFileSync(join(f.destination, 'viewer.min.js'), 'utf8'), 'local edit');
    rmSync(join(f.destination, 'LICENSE'));
    assert.throws(() => syncViewer({ root: f.root, check: true }), /LICENSE/);
});

test('sync refuses mismatched installed versions and unpinned dependencies', t => {
    const f = fixture(t);
    writeFileSync(join(f.dependency, 'package.json'), JSON.stringify({ name: 'viewerjs', version: '1.11.6', license: 'MIT' }));
    assert.throws(() => syncViewer({ root: f.root }), /version/i);
    writeFileSync(join(f.tooling, 'package.json'), JSON.stringify({ devDependencies: { viewerjs: '^1.15.2' } }));
    assert.throws(() => syncViewer({ root: f.root }), /exact|pin/i);
});

test('sync validates every upstream asset before writing anything', t => {
    const f = fixture(t);
    writeFileSync(join(f.dependency, 'dist/viewer.min.css'), '/*! Viewer.js v1.11.6 */');
    assert.throws(() => syncViewer({ root: f.root }), /version|banner/i);
    assert.throws(() => readFileSync(join(f.destination, 'viewer.min.js')), /ENOENT/);
});

test('updater resolves the stable release and installs it exactly with lifecycle scripts disabled', t => {
    const f = fixture(t);
    const calls = [];
    let synced = false;
    const run = (args, options) => {
        calls.push({ args, options });
        return args[0] === 'view' ? '"1.15.3"' : '';
    };
    assert.equal(updateViewer({ root: f.root, run, sync: () => { synced = true; } }), '1.15.3');
    assert.deepEqual(calls[1].args, ['install', '--save-dev', '--save-exact', '--ignore-scripts', 'viewerjs@1.15.3']);
    assert.equal(calls[1].options.cwd, f.tooling);
    assert.equal(synced, true);
});

test('updater is a no-op at the current release and rejects invalid version arguments', t => {
    const f = fixture(t);
    const run = () => { throw new Error('must not install'); };
    assert.equal(updateViewer({ root: f.root, version: '1.15.2', run }), null);
    for (const version of ['latest', '1.15.3-beta.1', '1.15.3;anything', '--global']) {
        assert.throws(() => updateViewer({ root: f.root, version, run }), /version/i);
    }
});

test('failed installation never copies vendor assets', t => {
    const f = fixture(t);
    let synced = false;
    assert.throws(() => updateViewer({ root: f.root, version: '1.15.3', run: () => { throw new Error('offline'); }, sync: () => { synced = true; } }), /offline/);
    assert.equal(synced, false);
});

test('updater accepts a single registry result array and rejects ambiguous metadata', t => {
    const f = fixture(t);
    const calls = [];
    const run = args => { calls.push(args); return '["1.15.2"]'; };
    assert.equal(updateViewer({ root: f.root, run }), null);
    assert.equal(calls.length, 1);
    assert.throws(() => updateViewer({ root: f.root, run: () => '["1.15.2", "1.15.3"]' }), /version/i);
});
