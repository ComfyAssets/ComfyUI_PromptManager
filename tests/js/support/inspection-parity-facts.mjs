// Facts for encoded inspection responses, through the workspace's own path:
// projectInspectionPayload validates the wire, then extractGenerationFacts reads
// the bounded projection. Used by tests/test_inspection_parity_facts.py, which
// encodes each parity vector's file with the real route pipeline.
// stdin: [{name, body}] (body is the exact response JSON text).
// stdout: {name: {field: {status, value}}}.
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const web = path.join(here, '..', '..', '..', 'web', 'js');
const require = createRequire(import.meta.url);
const parser = require(path.join(web, 'comfy-metadata.js')), { extractGenerationFacts } = parser;
const { projectInspectionPayload } = await import(pathToFileURL(path.join(web, 'workspace', 'inspection-state.mjs')).href);

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const output = {};
for (const { name, body } of JSON.parse(Buffer.concat(chunks).toString('utf8'))) {
    const identity = { kind: 'rooted-file', key: `0:${name}.png`, root_index: 0, path: `${name}.png`, root_scope: null };
    const record = projectInspectionPayload(JSON.parse(body), identity, null, extractGenerationFacts, false, parser);
    output[name] = Object.fromEntries(Object.entries(record.generation).map(([field, fact]) => [field, { status: fact.status, value: fact.value }]));
}
process.stdout.write(JSON.stringify(output));
