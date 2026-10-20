// The seed the workspace inspector shows for encoded inspection responses, through
// its own path: projectInspectionPayload with the server facts and the legacy
// parser fill (comfy-metadata.js, passed explicitly; Node has no window global).
// Used by tests/test_api_workspace_images.py to compare card and inspector seeds.
// stdin: {listing, inspections: [{name, body}]}; each body is the exact response
// JSON text, and listing (optional) is the exact workspace listing response text.
// stdout: {inspector: {name: {status, value}}, card: {path: String(seed) | null}}.
// Inspector status is 'error' where the page cannot project the record. The card
// values come from JSON.parse of the listing, as the browser reads them, so a
// seed sent as a JSON number past 2**53 shows up rounded here.
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const web = path.join(here, '..', '..', '..', 'web', 'js');
const require = createRequire(import.meta.url);
const parser = require(path.join(web, 'comfy-metadata.js'));
const { projectInspectionPayload } = await import(pathToFileURL(path.join(web, 'workspace', 'inspection-state.mjs')).href);

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const { listing = null, inspections } = JSON.parse(Buffer.concat(chunks).toString('utf8'));
const inspector = {};
for (const { name, body } of inspections) {
    const identity = { kind: 'rooted-file', key: `0:${name}`, root_index: 0, path: name, root_scope: null };
    try {
        const record = projectInspectionPayload(JSON.parse(body), identity, null, parser.extractGenerationFacts, false, parser);
        inspector[name] = { status: record.generation.seed.status, value: record.generation.seed.value };
    } catch (error) {
        // The page shows no record here, so no seed either.
        inspector[name] = { status: 'error', value: String(error && error.name) };
    }
}
const card = {};
for (const item of listing === null ? [] : JSON.parse(listing).images) {
    card[item.path] = item.seed === null || item.seed === undefined ? null : String(item.seed);
}
process.stdout.write(JSON.stringify({ inspector, card }));
