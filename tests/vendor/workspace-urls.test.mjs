import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

// Mount-aware URLs (#164): Prompt Manager runs behind reverse proxies with a path prefix, so every src, href or
// new URL() the workspace builds from a "/prompt_manager/..." literal must go through resolveUrl (or the api client).
const root = join(dirname(fileURLToPath(import.meta.url)), '../..');
const SINK = /(\.(?:src|href)\s*=|setAttribute\(\s*['"](?:src|href)['"]\s*,|new URL\()/g;
const RAW = /['"`]\/prompt_manager\//;

export function rawUrlSinks(source) {
    const found = [];
    source.split('\n').forEach((line, index) => {
        for (const match of line.matchAll(SINK)) {
            const rest = line.slice(match.index + match[0].length);
            const literal = rest.search(RAW);
            if (literal !== -1 && !/\bresolve(?:Url)?\s*\(/.test(rest.slice(0, literal))) found.push(`${index + 1}: ${line.trim()}`);
        }
    });
    return found;
}

test('the scanner flags raw Prompt Manager URLs in src, href and new URL()', () => {
    assert.equal(rawUrlSinks("img.src = '/prompt_manager/images/serve/a.png';").length, 1);
    assert.equal(rawUrlSinks('link.href = `/prompt_manager/admin`;').length, 1);
    assert.equal(rawUrlSinks("node.setAttribute('href', '/prompt_manager/gallery');").length, 1);
    assert.equal(rawUrlSinks("const url = new URL('/prompt_manager/x', location.href);").length, 1);
    assert.deepEqual(rawUrlSinks("img.src = resolveUrl('/prompt_manager/images/serve/a.png');"), []);
    assert.deepEqual(rawUrlSinks('const u = new URL(resolve(`/prompt_manager/history/${id}`), location.href);'), []);
    assert.deepEqual(rawUrlSinks("link.href = resolveUrl(route.href);"), []);
});

test('workspace modules build Prompt Manager src, href and URLs only through resolveUrl', () => {
    const dir = join(root, 'web/js/workspace');
    const offenders = readdirSync(dir).filter(name => name.endsWith('.mjs'))
        .flatMap(name => rawUrlSinks(readFileSync(join(dir, name), 'utf8')).map(hit => `${name}:${hit}`));
    assert.deepEqual(offenders, []);
});
