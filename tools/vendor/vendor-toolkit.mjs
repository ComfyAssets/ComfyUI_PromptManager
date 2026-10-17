import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { repositoryRoot } from './vendor-viewerjs.mjs';

export function syncToolkit({ root = repositoryRoot, check = false } = {}) {
    const tooling = join(root, 'tools/vendor');
    const readJson = path => JSON.parse(readFileSync(path, 'utf8'));
    const pin = readJson(join(tooling, 'package.json')).devDependencies['@comfyassets/toolkit'];
    if (!/^https:\/\/codeload\.github\.com\/ComfyAssets\/ComfyAssets-Toolkit\/tar\.gz\/[a-f0-9]{40}$/.test(pin)) throw new Error('Toolkit needs an immutable commit pin');
    const locked = readJson(join(tooling, 'package-lock.json')).packages['node_modules/@comfyassets/toolkit'];
    const installed = join(tooling, 'node_modules/@comfyassets/toolkit');
    const info = readJson(join(installed, 'package.json'));
    if (locked?.resolved !== pin || !locked.integrity || locked.version !== info.version || info.name !== '@comfyassets/toolkit' || info.license !== 'MIT') throw new Error('Toolkit package identity or lock mismatch; run npm ci --ignore-scripts');
    const output = new Map();
    const files = {};
    for (const [name, source] of Object.entries({ 'core.js': 'ui/core.js', 'suggestions.js': 'ui/suggestions.js', 'prompt-bridge.js': 'ui/prompt-bridge.js', 'controls.css': 'ui/controls.css', LICENSE: 'LICENSE', 'SOURCE.md': 'SOURCE.md' })) {
        const content = readFileSync(join(installed, source));
        output.set(name, content);
        files[name] = { source, sha256: createHash('sha256').update(content).digest('hex') };
    }
    output.set('provenance.json', Buffer.from(JSON.stringify({ name: info.name, version: info.version, resolved: pin, integrity: locked.integrity, files }, null, 2) + '\n'));
    const destination = join(root, 'web/js/shared/toolkit');
    if (check) {
        const drift = [...output].filter(([name, content]) => {
            try { return !readFileSync(join(destination, name)).equals(content); }
            catch (error) { if (error.code === 'ENOENT') return true; throw error; }
        }).map(([name]) => name);
        if (drift.length) throw new Error(`Toolkit browser distribution drift: ${drift.join(', ')}`);
    } else {
        mkdirSync(destination, { recursive: true });
        for (const [name, content] of output) writeFileSync(join(destination, name), content);
    }
    return info.version;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
    try {
        if (process.argv.slice(2).some(arg => arg !== '--check')) throw new Error('Usage: vendor-toolkit.mjs [--check]');
        console.log(`Toolkit ${syncToolkit({ check: process.argv.includes('--check') })}: verified`);
    } catch (error) { console.error(error.message); process.exitCode = 1; }
}
