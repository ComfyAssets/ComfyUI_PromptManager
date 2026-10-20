import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { repositoryRoot } from './vendor-viewerjs.mjs';

const CONCEPT = 'docs/concepts/workspace/style.css';
const COPY = 'web/js/workspace/concept.css';
const MARKER = /\/\*[^*]*\bD\d+\b[^*]*\*\//;
const SPEC_ISSUE = '#249';

// A decision marker names one of this spec's (#249) D-numbers. A comment that cites any other issue is that issue's
// ruling (for example #245's own D5), so it never marks a concept override here.
function isDecisionMarker(comment) {
    return MARKER.test(comment) && (comment.match(/#\d+\b/g) ?? []).every(issue => issue === SPEC_ISSUE);
}

// Minimal tokenizer: walks top-level rules and the bodies of @media/@supports,
// returning each rule's normalized selectors and the comments attached to it
// (those before its selector and those inside its block).
function parseRules(css) {
    const rules = [];
    let index = 0;
    const length = css.length;
    function readBlock(start) {
        let depth = 0;
        let cursor = start;
        for (; cursor < length; cursor += 1) {
            if (css.startsWith('/*', cursor)) { cursor = commentEnd(cursor) - 1; continue; }
            const char = css[cursor];
            if (char === '"' || char === "'") { cursor = stringEnd(cursor) - 1; continue; }
            if (char === '{') depth += 1;
            else if (char === '}') { depth -= 1; if (depth === 0) return cursor; }
        }
        return length - 1;
    }
    function commentEnd(at) { const end = css.indexOf('*/', at + 2); return end === -1 ? length : end + 2; }
    function stringEnd(at) {
        for (let cursor = at + 1; cursor < length; cursor += 1) {
            if (css[cursor] === '\\') cursor += 1;
            else if (css[cursor] === css[at]) return cursor + 1;
        }
        return length;
    }
    function walk(from, to) {
        let cursor = from;
        let comments = [];
        let prelude = '';
        while (cursor < to) {
            if (css.startsWith('/*', cursor)) {
                const end = commentEnd(cursor);
                comments.push(css.slice(cursor, end));
                cursor = end;
                continue;
            }
            const char = css[cursor];
            if (char === ';') { prelude = ''; comments = []; cursor += 1; continue; }
            if (char === '{') {
                const close = readBlock(cursor);
                const head = prelude.trim();
                const body = css.slice(cursor + 1, close);
                if (/^@(media|supports|layer)\b/i.test(head)) walk(cursor + 1, close);
                else if (!head.startsWith('@')) {
                    rules.push({ selectors: splitSelectors(head), comments: [...comments, ...(body.match(/\/\*[\s\S]*?\*\//g) ?? [])] });
                }
                prelude = ''; comments = []; cursor = close + 1;
                continue;
            }
            prelude += char;
            cursor += 1;
        }
    }
    walk(index, length);
    return rules;
}

function splitSelectors(head) {
    const out = [];
    let depth = 0;
    let current = '';
    for (const char of head) {
        if (char === '(' || char === '[') depth += 1;
        else if (char === ')' || char === ']') depth -= 1;
        if (char === ',' && depth === 0) { out.push(current); current = ''; } else current += char;
    }
    out.push(current);
    return out.map(selector => selector.replace(/\s+/g, ' ').trim()).filter(Boolean);
}

export function lintOverlay(conceptCss, overlayCss) {
    const owned = new Set(parseRules(conceptCss).flatMap(rule => rule.selectors));
    const violations = [];
    for (const rule of parseRules(overlayCss)) {
        const shared = rule.selectors.filter(selector => owned.has(selector));
        if (shared.length && !rule.comments.some(isDecisionMarker)) {
            violations.push(`${shared.join(', ')}: overlay rule on a concept selector needs a /* D<n> */ (#249) decision marker`);
        }
    }
    return { violations };
}

export function syncConceptCss({ root = repositoryRoot, check = false } = {}) {
    const source = readFileSync(join(root, CONCEPT));
    const target = join(root, COPY);
    if (check) {
        let current = null;
        try { current = readFileSync(target); } catch (error) { if (error.code !== 'ENOENT') throw error; }
        if (!current) throw new Error(`Concept stylesheet drift: ${COPY} is missing; run sync-concept-css.mjs --write`);
        if (!current.equals(source)) {
            let offset = 0;
            while (offset < source.length && offset < current.length && source[offset] === current[offset]) offset += 1;
            throw new Error(`Concept stylesheet drift: ${COPY} (${current.length} bytes) differs from ${CONCEPT} (${source.length} bytes) at byte ${offset}; run sync-concept-css.mjs --write`);
        }
    } else {
        mkdirSync(join(root, 'web/js/workspace'), { recursive: true });
        writeFileSync(target, source);
    }
    return source.length;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
    try {
        const args = process.argv.slice(2);
        const rootAt = args.indexOf('--root');
        const root = rootAt === -1 ? repositoryRoot : resolve(args[rootAt + 1]);
        const mode = args.filter((arg, at) => arg.startsWith('--') && arg !== '--root' && !(rootAt !== -1 && at === rootAt + 1));
        if (mode.length !== 1 || !['--write', '--check'].includes(mode[0])) throw new Error('Usage: sync-concept-css.mjs --write | --check [--root <dir>]');
        const bytes = syncConceptCss({ root, check: mode[0] === '--check' });
        console.log(`Concept stylesheet ${mode[0] === '--check' ? 'verified' : 'written'} (${bytes} bytes)`);
    } catch (error) { console.error(error.message); process.exitCode = 1; }
}
