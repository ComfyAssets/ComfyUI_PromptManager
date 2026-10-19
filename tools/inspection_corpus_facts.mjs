// Facts for tools/inspection_corpus.py, through the workspace's own path:
// projectInspectionPayload validates the wire, then extractGenerationFacts reads
// the bounded projection. argv[2] is the code tree whose web/js is loaded.
// stdin: one exact inspection response JSON text per line.
// stdout: one line per input, {field: {status, reason}} or {error}.
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import readline from 'node:readline';

const web = path.join(path.resolve(process.argv[2]), 'web', 'js');
const require = createRequire(import.meta.url);
const { extractGenerationFacts } = require(path.join(web, 'comfy-metadata.js'));
const { projectInspectionPayload } = await import(pathToFileURL(path.join(web, 'workspace', 'inspection-state.mjs')).href);
const identity = { kind: 'rooted-file', key: '0:corpus.png', root_index: 0, path: 'corpus.png', root_scope: null };

function facts(line) {
    try {
        const record = projectInspectionPayload(JSON.parse(line), identity, null, extractGenerationFacts);
        return Object.fromEntries(Object.entries(record.generation).map(([field, fact]) => [field, { status: fact.status, reason: fact.reason ?? null }]));
    } catch (error) {
        return { error: error?.name || 'Error' };
    }
}

for await (const line of readline.createInterface({ input: process.stdin, crlfDelay: Infinity })) {
    process.stdout.write(`${JSON.stringify(facts(line))}\n`);
}
