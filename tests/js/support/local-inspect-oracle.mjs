// The browser's local readers run over files on disk, for
// tests/test_local_inspect_parity.py, which compares every answer with the server's
// utils.prompt_inspector.read_inspection_file on the same file. Each file is opened
// as a Blob (fs.openAsBlob) so the reader slices it exactly as it slices a File.
// stdin: [{name, path}]
// stdout: {name: {classified, result}} where result is readPngMetadataFile's
//         answer for a PNG, readImageHeader's for another image, else null.
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const { classifyPrefix, readPngMetadataFile, readImageHeader } = require(path.join(here, '..', '..', '..', 'web', 'js', 'png-metadata.js'));

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const output = {};
for (const { name, path: filePath } of JSON.parse(Buffer.concat(chunks).toString('utf8'))) {
    const file = await fs.openAsBlob(filePath);
    const prefix = new Uint8Array(await file.slice(0, Math.min(32, file.size)).arrayBuffer());
    const classified = classifyPrefix(prefix);
    let result = null;
    if (classified?.kind === 'png') result = await readPngMetadataFile(file, { prefix });
    else if (classified?.media === 'image') result = await readImageHeader(file, classified.kind);
    output[name] = { classified, result };
}
process.stdout.write(JSON.stringify(output));
