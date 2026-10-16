import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

export const repositoryRoot = fileURLToPath(new URL('../..', import.meta.url));
export const stableVersion = /^\d+\.\d+\.\d+$/;
const readJson = path => JSON.parse(readFileSync(path, 'utf8'));

export function syncViewer({ root = repositoryRoot, check = false } = {}) {
    const tooling = join(root, 'tools/vendor');
    const manifest = readJson(join(tooling, 'package.json'));
    const version = manifest.devDependencies.viewerjs;
    if (!stableVersion.test(version)) throw new Error('Viewer.js must use an exact stable version pin');
    const locked = readJson(join(tooling, 'package-lock.json')).packages['node_modules/viewerjs'];
    const installed = join(tooling, 'node_modules/viewerjs');
    const info = readJson(join(installed, 'package.json'));
    if (locked?.version !== version || info.version !== version) {
        throw new Error('Viewer.js version mismatch: run npm ci --ignore-scripts in tools/vendor');
    }
    if (!locked.integrity || !locked.resolved || info.name !== 'viewerjs' || info.license !== 'MIT') {
        throw new Error('Viewer.js package identity, integrity, or license is missing');
    }
    const files = {};
    const output = new Map();
    for (const [target, source] of Object.entries({
        'viewer.min.js': 'dist/viewer.min.js',
        'viewer.min.css': 'dist/viewer.min.css',
        LICENSE: 'LICENSE',
    })) {
        const content = readFileSync(join(installed, source));
        if (target !== 'LICENSE' && !content.toString('utf8').includes(`Viewer.js v${version}\n`) &&
            !content.toString('utf8').includes(`Viewer.js v${version} */`)) {
            throw new Error(`Viewer.js version banner mismatch in ${source}`);
        }
        files[target] = { source, sha256: createHash('sha256').update(content).digest('hex') };
        output.set(target, content);
    }
    output.set('provenance.json', Buffer.from(JSON.stringify({
        name: info.name,
        version,
        source: `https://github.com/fengyuanchen/viewerjs/tree/v${version}`,
        resolved: locked.resolved,
        integrity: locked.integrity,
        files,
    }, null, 2) + '\n'));
    const destination = join(root, 'web/lib/viewerjs');
    if (check) {
        const drift = [...output].filter(([name, content]) => {
            try { return !readFileSync(join(destination, name)).equals(content); }
            catch (error) { if (error.code === 'ENOENT') return true; throw error; }
        }).map(([name]) => name);
        if (drift.length) throw new Error(`Vendored Viewer.js drift: ${drift.join(', ')}. Run npm run sync in tools/vendor.`);
    } else {
        mkdirSync(destination, { recursive: true });
        for (const [name, content] of output) writeFileSync(join(destination, name), content);
    }
    return version;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
    try {
        const args = process.argv.slice(2);
        if (args.some(arg => arg !== '--check')) throw new Error('Usage: vendor-viewerjs.mjs [--check]');
        console.log(`Viewer.js ${syncViewer({ check: args.includes('--check') })}: ${args.includes('--check') ? 'verified' : 'synced'}`);
    } catch (error) {
        console.error(error.message);
        process.exitCode = 1;
    }
}
