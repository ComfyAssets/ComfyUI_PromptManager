import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { repositoryRoot, stableVersion, syncViewer } from './vendor-viewerjs.mjs';

function npm(args, options) {
    if (!process.env.npm_execpath) throw new Error('Run the updater through npm run update');
    return execFileSync(process.execPath, [process.env.npm_execpath, ...args], options);
}

export function updateViewer({ root = repositoryRoot, version, run = npm, sync = syncViewer } = {}) {
    const cwd = join(root, 'tools/vendor');
    const current = JSON.parse(readFileSync(join(cwd, 'package.json'), 'utf8')).devDependencies.viewerjs;
    const result = version ?? JSON.parse(run(['view', 'viewerjs', 'version', '--json'], { cwd, encoding: 'utf8' }));
    const target = Array.isArray(result) && result.length === 1 ? result[0] : result;
    if (typeof target !== 'string' || !stableVersion.test(target)) throw new Error('Expected a stable Viewer.js version such as 1.15.2');
    if (target === current) return null;
    run(['install', '--save-dev', '--save-exact', '--ignore-scripts', `viewerjs@${target}`], { cwd, stdio: 'inherit' });
    sync({ root });
    return target;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
    try {
        const args = process.argv.slice(2);
        if (args.length && (args.length !== 2 || args[0] !== '--version')) throw new Error('Usage: npm run update -- [--version X.Y.Z]');
        const version = updateViewer({ version: args[1] });
        console.log(version ? `Updated Viewer.js to ${version}` : 'Viewer.js is already current');
    } catch (error) {
        console.error(error.message);
        process.exitCode = 1;
    }
}
