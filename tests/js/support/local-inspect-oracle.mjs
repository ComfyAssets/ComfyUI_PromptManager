// The browser's local readers and projection run over files on disk, for
// tests/test_local_inspect_parity.py, which compares every answer with the server's
// utils.prompt_inspector.read_inspection_file and utils.inspection_response._metadata
// on the same file. Each file is opened as a Blob (fs.openAsBlob) so the reader
// slices it exactly as it slices a File.
// stdin: [{name, path} | {name, tags, limited?} | {name, records, record_formats?, carrier_text?, limited?}]
// stdout: {name: {classified, result, metadata}} where result is readPngMetadataFile's
//         answer for a PNG, readImageHeader's for another image, for a video
//         {read: readVideoTags(...), selection: selectVideoCarrier(read.tags, {limited})}
//         (selection null unless the read completed), else null; metadata is
//         projectMetadata(...) of the local read (the server's _metadata) for every
//         classified file, else null.
//         A {tags} entry answers {classified: null, result: {selection}, metadata} for
//         the carrier rule alone, as the server selects from ffprobe's tag map.
//         A {records} entry (records null = an unreadable file) answers
//         {classified: null, result: null, metadata} for the projection alone.
// A {mode: 'resolve'} entry (PR L3) answers {evidence} or {gap: true}: resolveLocalEvidence of its
// {path}'s local read, or of its {records} texts, with the repository semantics table
// (gap: a path the port leaves to Task 9).
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { moduleUrl } from './module-url.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const pngMetadata = require(path.join(here, '..', '..', '..', 'web', 'js', 'png-metadata.js'));
const web = path.join(here, '..', '..', '..', 'web', 'js', 'workspace');
// ES modules import by file URL (a Windows path would parse as a URL scheme).
const { selectVideoCarrier } = await import(moduleUrl(path.join(web, 'local-video.mjs')));
const { projectMetadata } = await import(moduleUrl(path.join(web, 'local-projection.mjs')));
const { readLocalFile, toInspectionRead } = await import(moduleUrl(path.join(web, 'local-inspection.mjs')));
const { resolveLocalEvidence, compileSemantics, LocalResolverGap } = await import(moduleUrl(path.join(web, 'local-resolver.mjs')));
const semantics = compileSemantics(JSON.parse(fs.readFileSync(path.join(here, '..', '..', '..', 'web', 'js', 'inspection-semantics.json'), 'utf8')));

/** The local evidence for one set of record texts, or {gap: true}. */
function resolved(records) {
    try {
        return { evidence: resolveLocalEvidence(records, semantics) };
    } catch (error) {
        if (error instanceof LocalResolverGap) return { gap: true };
        throw error;
    }
}

const selectionRead = (selection) => selection.status === 'complete' ?
    { records: selection.records, record_formats: selection.record_formats, carrierText: selection.carrierText, limited: selection.limited } : null;

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const output = {};
for (const entry of JSON.parse(Buffer.concat(chunks).toString('utf8'))) {
    const { name } = entry;
    if (entry.mode === 'resolve') {
        if (entry.records !== undefined) { output[name] = resolved(entry.records); continue; }
        const { classification, result } = await readLocalFile(await fs.openAsBlob(entry.path), { pngMetadata });
        const read = classification === null ? null : toInspectionRead(classification, result).read;
        output[name] = read === null ? { unreadable: true } : resolved(read.records);
        continue;
    }
    if (entry.records !== undefined) {
        const read = entry.records === null ? null : {
            records: entry.records,
            record_formats: entry.record_formats ?? Object.fromEntries(Object.keys(entry.records).map((key) => [key, 'original-text'])),
            carrierText: entry.carrier_text ?? null, limited: entry.limited ?? [],
        };
        output[name] = { classified: null, result: null, metadata: projectMetadata(read) };
        continue;
    }
    if (entry.path === undefined) {
        const selection = selectVideoCarrier(entry.tags, { limited: entry.limited || [] });
        output[name] = { classified: null, result: { selection }, metadata: projectMetadata(selectionRead(selection)) };
        continue;
    }
    const file = await fs.openAsBlob(entry.path);
    const { classification, result } = await readLocalFile(file, { pngMetadata });
    const metadata = classification === null ? null : projectMetadata(toInspectionRead(classification, result).read);
    output[name] = { classified: classification, result, metadata };
}
process.stdout.write(JSON.stringify(output));
