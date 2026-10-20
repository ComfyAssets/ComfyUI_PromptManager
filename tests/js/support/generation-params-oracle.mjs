// Runs the legacy browser parser (comfy-metadata.js extractGenerationParams) on
// one JSON case per input line and prints one JSON result per line. The Python
// port (utils/generation_params.py) must produce the same results.
//
// Input line:  {"prompt": <text or null>, "workflow": <text or null>}
//              Each present record is parsed with JSON.parse; a record that fails
//              to parse is treated as absent, as the browser pages do.
// Output line: {"ok": true, "result": {...}} or {"ok": false} when the parser throws.
import path from 'node:path';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const { extractGenerationParams } = require(path.join(here, '..', '..', '..', 'web', 'js', 'comfy-metadata.js'));

function parsed(text) {
    if (typeof text !== 'string') return undefined;
    try { return JSON.parse(text); } catch { return undefined; }
}

for (const line of fs.readFileSync(0, 'utf8').split('\n')) {
    if (!line.trim()) continue;
    const item = JSON.parse(line), data = {};
    const prompt = parsed(item.prompt), workflow = parsed(item.workflow);
    if (prompt !== undefined) data.prompt = prompt;
    if (workflow !== undefined) data.workflow = workflow;
    let out;
    try { out = { ok: true, result: extractGenerationParams(data) }; } catch { out = { ok: false }; }
    process.stdout.write(JSON.stringify(out) + '\n');
}
