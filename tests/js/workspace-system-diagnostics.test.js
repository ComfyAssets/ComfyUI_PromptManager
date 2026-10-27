// System › Diagnostics & logs (#249 System S12, #329): the concept's two surfaces (app.js 694–695) with live values: the
// meta-grid texts, the report's name and merge (D110), the log viewer's lines, Lines validation, statistics, retention
// and file rows, and the Clear all logs copy (D109).
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const diagnostics = import('../../web/js/workspace/system-diagnostics.mjs');

const CONCEPT = fs.readFileSync(path.join(__dirname, '../../docs/concepts/workspace/app.js'), 'utf8');
const SOURCE = fs.readFileSync(path.join(__dirname, '../../web/js/workspace/system-diagnostics.mjs'), 'utf8');
const SYSTEM = fs.readFileSync(path.join(__dirname, '../../web/js/workspace/system.mjs'), 'utf8');

const REPORT = {
    checks: { database: { status: 'ok', prompt_count: 4210, has_images_table: true, image_count: 19956 } },
    database_info: { database_path: 'user/__promptmanager/prompts.db', database_size_bytes: 1717986918 },
    sources: { configured: 2, effective: 2, watching: 2, unavailable: 0 },
    viewerjs: '1.15.2',
};

test('the section keeps the concept surfaces, terms, rows and labels; no sample copy ships', () => {
    const start = CONCEPT.indexOf('case "diagnostics":');
    const body = CONCEPT.slice(start, CONCEPT.indexOf('break;', start));
    const terms = [...body.matchAll(/<dt>([^<]+)<\/dt>/g)].map(([, term]) => term);
    assert.deepEqual(terms, ['Database', 'Sources', 'Viewer.js', 'Host renderers']);
    for (const text of ["'Diagnostics'", "'Logs'", "'Run checks'", "'Refresh'", "'Level'", "'Log files & retention'",
        "'Review log cleanup'", "'Runtime log level'", "'Console logging'", "'Image-link diagnostics'"]) {
        assert.ok(SOURCE.includes(text), text);
    }
    for (const id of ['runtime-level', 'console-log']) assert.ok(body.includes(`setting("${id}"`) && SOURCE.includes(`id: '${id}'`), id);
    assert.ok(SOURCE.includes("'toolbar spaced-16'") && body.includes('class="toolbar spaced-16"'));
    // Prototype copy is never shipped.
    for (const sample of ['sample', 'Sample']) assert.ok(!SOURCE.includes(`'${sample}`) && !/Download sample|Inspect sample/.test(SOURCE), sample);
});

test('D94 is retired: Diagnostics & logs is a delivered section and nothing hands off', () => {
    assert.ok(SYSTEM.includes('diagnostics: mountDiagnostics'));
    assert.ok(!SYSTEM.includes('SECTION_HANDOFFS'));
    assert.ok(!SYSTEM.includes('handoff('));
    assert.ok(!SYSTEM.includes('NOT_DELIVERED'));
});

test('database text covers readable, not readable, busy and loading', async () => {
    const { databaseText, LOADING, UNREADABLE } = await diagnostics;
    assert.equal(databaseText({ report: REPORT }), 'Readable · 1.6 GB · 19,956 images');
    assert.equal(databaseText({ report: { ...REPORT, checks: { database: { status: 'ok', image_count: 1 } },
        database_info: { database_size_bytes: 2048 } } }), 'Readable · 2.0 KB · 1 image');
    assert.equal(databaseText({ report: { checks: { database: { status: 'error', message: 'Database error: locked' } } } }),
        'Not readable: Database error: locked');
    assert.equal(databaseText({ loading: true }), LOADING);
    assert.equal(LOADING, 'Loading…');
    assert.equal(databaseText({ error: { code: 'busy', running: { id: 'j1', title: 'Restore' } } }), 'Busy: Restore is running.');
    assert.equal(databaseText({ error: { code: 'busy' } }), 'Busy: another job is running.');
    assert.equal(databaseText({ error: new Error('offline') }), UNREADABLE);
    assert.equal(UNREADABLE, 'Could not be read.');
});

test('sources text follows the precedence table', async () => {
    const { sourcesText } = await diagnostics;
    const of = sources => sourcesText({ report: { sources } });
    assert.equal(of({ configured: 0, effective: 1, watching: 1, unavailable: 0 }), 'ComfyUI output folder · watching');
    assert.equal(of({ configured: 0, effective: 1, watching: 0, unavailable: 0 }), 'ComfyUI output folder · manual scan');
    // Unavailable wins over watching.
    assert.equal(of({ configured: 3, effective: 2, watching: 2, unavailable: 1 }), '3 sources · 1 not available');
    assert.equal(of({ configured: 2, effective: 2, watching: 2, unavailable: 0 }), '2 sources · 2 watching');
    assert.equal(of({ configured: 1, effective: 1, watching: 0, unavailable: 0 }), '1 source · 0 watching');
    assert.equal(of(undefined), 'ComfyUI output folder · manual scan');
});

test('Viewer.js reads the pinned vendor version', async () => {
    const { viewerText } = await diagnostics;
    assert.equal(viewerText({ report: REPORT }), '1.15.2 · pinned vendor');
    assert.equal(viewerText({ report: {} }), 'unknown · pinned vendor');
});

test('report filename uses local time', async () => {
    const { reportFilename } = await diagnostics;
    assert.equal(reportFilename(new Date(2026, 9, 6, 7, 5)), 'prompt-manager-diagnostics-20261006-0705.json');
    assert.equal(reportFilename(new Date(2026, 11, 31, 23, 59)), 'prompt-manager-diagnostics-20261231-2359.json');
});

test('merged report adds only host_renderers', async () => {
    const { mergeReport } = await diagnostics;
    const merged = mergeReport(REPORT, 'Detected: Classic (1 ComfyUI window).');
    assert.deepEqual(Object.keys(merged).sort(), [...Object.keys(REPORT), 'host_renderers'].sort());
    assert.equal(merged.host_renderers, 'Detected: Classic (1 ComfyUI window).');
    assert.equal(mergeReport(REPORT, null).host_renderers, null);
    assert.equal(mergeReport(REPORT, undefined).host_renderers, null);
    // The server's fields are not changed.
    assert.equal(merged.sources, REPORT.sources);
});

test('sizes read as the server labels them (1 KB = 1024 bytes)', async () => {
    const { sizeText } = await diagnostics;
    assert.equal(sizeText(0), '0 bytes');
    assert.equal(sizeText(1), '1 byte');
    assert.equal(sizeText(1000), '1,000 bytes');
    assert.equal(sizeText(2048), '2.0 KB');
    assert.equal(sizeText(10 * 2 ** 20), '10.0 MB');
    assert.equal(sizeText(1717986918), '1.6 GB');
    assert.equal(sizeText(undefined), '0 bytes');
});

test("log line uses the concept's format", async () => {
    const { logLine } = await diagnostics;
    assert.equal(logLine({ timestamp: '2026-10-26T09:42:18.123456', level: 'INFO', message: 'Database backup verified' }),
        '09:42:18 INFO  Database backup verified');
    assert.equal(logLine({ timestamp: '2026-10-26T09:42:18', level: 'WARNING', message: '3 images have ambiguous prompt metadata' }),
        '09:42:18 WARNING 3 images have ambiguous prompt metadata');
    assert.match(logLine({ timestamp: '2026-10-26T09:42:18', level: 'ERROR', message: 'x' }), /^\d\d:\d\d:\d\d [A-Z]+ +\S/);
    // The concept's sample lines read the same way.
    assert.ok(CONCEPT.includes('09:42:18 INFO  Scan completed'));
});

test('Lines accepts whole numbers 1 to 500 only', async () => {
    const { validLimit, logsUrl } = await diagnostics;
    for (const [value, expected] of [['1', 1], ['500', 500], [' 20 ', 20], ['100', 100]]) assert.equal(validLimit(value), expected, value);
    for (const value of ['0', '501', '2.5', '', ' ', '-1', '1e2', 'abc', undefined]) assert.equal(validLimit(value), null, String(value));
    assert.equal(logsUrl('', 20), '/prompt_manager/logs?limit=20');
    assert.equal(logsUrl('WARNING', 20), '/prompt_manager/logs?limit=20&level=WARNING');
});

test('the level filter offers all six minimum levels; the runtime level adds CRITICAL after the concept four', async () => {
    const { LEVEL_OPTIONS, RUNTIME_LEVELS } = await diagnostics;
    assert.deepEqual(LEVEL_OPTIONS.map(([, label]) => label), ['All levels', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']);
    assert.equal(LEVEL_OPTIONS[0][0], '');
    assert.deepEqual(RUNTIME_LEVELS, ['INFO', 'WARNING', 'DEBUG', 'ERROR', 'CRITICAL']);
    assert.ok(CONCEPT.includes('["INFO", "WARNING", "DEBUG", "ERROR"]'));
});

test('statistics text uses singular forms for one', async () => {
    const { statsText } = await diagnostics;
    assert.equal(statsText({ buffer_count: 1000, level_counts: { INFO: 980, WARNING: 12, ERROR: 2, CRITICAL: 1 },
        log_files_count: 5, total_log_size: 43201024 }), '1,000 entries in memory · 3 errors · 12 warnings · 5 files · 41.2 MB on disk');
    assert.equal(statsText({ buffer_count: 1, level_counts: { WARNING: 1, CRITICAL: 1 }, log_files_count: 1, total_log_size: 1 }),
        '1 entry in memory · 1 error · 1 warning · 1 file · 1 byte on disk');
    assert.equal(statsText({}), '0 entries in memory · 0 errors · 0 warnings · 0 files · 0 bytes on disk');
});

test('retention, file line and file rows read the server values', async () => {
    const { retentionText, fileLine, fileDetail } = await diagnostics;
    assert.equal(retentionText({ max_file_size: 10485760, backup_count: 5, file_logging: true, buffer_size: 1000 }),
        'Rotates at 10 MB · keeps 5 rotated copies · file logging on · memory buffer 1,000 entries');
    assert.equal(retentionText({ max_file_size: 1572864, backup_count: 1, file_logging: false, buffer_size: 1 }),
        'Rotates at 1.5 MB · keeps 1 rotated copy · file logging off · memory buffer 1 entry');
    const files = [
        { filename: 'prompt_manager.log', kind: 'active', size: 1468006, modified: '2026-10-26T09:42:18' },
        { filename: 'prompt_manager.log.1', kind: 'rotated', size: 10485760, modified: '2026-10-25T14:02:00' },
        { filename: 'prompt_manager.log.4', kind: 'link', size: 20, modified: '2026-10-25T14:02:00' },
        { filename: 'prompt_manager.log.bak', kind: 'other', size: 0, modified: '2026-10-24T08:00:00' },
    ];
    assert.equal(fileLine(files), 'prompt_manager.log · 1.4 MB · rotated copies: 1');
    assert.equal(fileLine([]), 'prompt_manager.log · not written yet · rotated copies: 0');
    assert.equal(fileDetail(files[1]), '10.0 MB · 2026-10-25 14:02 · Rotated copy');
    assert.equal(fileDetail(files[2]), '20 bytes · 2026-10-25 14:02 · Link, not downloaded');
    assert.equal(fileDetail(files[3]), '0 bytes · 2026-10-24 08:00 · Other file');
    assert.equal(fileDetail(files[0]), '1.4 MB · 2026-10-26 09:42 · Active log');
});

test('Clear all logs states both counts and its toast says what was done', async () => {
    const { clearSentence, clearToast } = await diagnostics;
    const rotated = size => ({ kind: 'rotated', size });
    const files = [{ kind: 'active', size: 10 }, rotated(10 * 2 ** 20), rotated(10 * 2 ** 20), rotated(8.4 * 2 ** 20), { kind: 'other', size: 5 }];
    assert.equal(clearSentence(files), 'Empties prompt_manager.log, deletes 3 rotated copies (28.4 MB) and clears the lines shown '
        + 'here. The other files in the log folder are kept. This cannot be undone.');
    assert.equal(clearSentence([{ kind: 'active', size: 1 }]), 'Empties prompt_manager.log and clears the lines shown here. '
        + 'The other files in the log folder are kept. This cannot be undone.');
    assert.match(clearSentence(null), /^Empties prompt_manager.log, deletes its rotated copies and clears/);
    assert.equal(clearToast({ truncated: ['prompt_manager.log', 'prompt_manager.log.1', 'prompt_manager.log.2', 'prompt_manager.log.3'],
        errors: [] }), 'Logs cleared · 3 rotated copies deleted.');
    assert.equal(clearToast({ truncated: ['prompt_manager.log', 'prompt_manager.log.1', 'prompt_manager.log.2'],
        errors: ['Failed to truncate prompt_manager.log.3: in use'] }), 'Logs cleared · 2 rotated copies deleted · 1 could not be deleted.');
    assert.equal(clearToast({ truncated: ['prompt_manager.log', 'prompt_manager.log.1'], errors: [] }), 'Logs cleared · 1 rotated copy deleted.');
    assert.equal(clearToast(undefined), 'Logs cleared · 0 rotated copies deleted.');
});

test('the test-link dialog names the prompt and how Missing-file links removes the record', async () => {
    const { testLinkSentence, promptName } = await diagnostics;
    assert.equal(testLinkSentence({ id: 812, text: 'Misty harbor' }), 'Writes a placeholder image record '
        + '(prompt_manager_test_link.png) linked to prompt #812 “Misty harbor”. Maintenance › Missing-file links removes it later.');
    assert.equal(promptName({ text: '  a\n  b ' }), 'a b');
    assert.equal(promptName({ text: 'x'.repeat(80) }).length, 60);
});

test('runtime values and body map config.level to runtime_level and back', async () => {
    const { runtimeValues, runtimeBody } = await diagnostics;
    assert.deepEqual(runtimeValues({ level: 'INFO', console_logging: true, max_file_size: 1 }), { runtime_level: 'INFO', console_logging: true });
    assert.deepEqual(runtimeBody({ runtime_level: 'CRITICAL' }), { level: 'CRITICAL' });
    assert.deepEqual(runtimeBody({ console_logging: false }), { console_logging: false });
    assert.deepEqual(runtimeBody({ runtime_level: 'DEBUG', console_logging: true }), { level: 'DEBUG', console_logging: true });
    assert.deepEqual(runtimeBody({}), {});
});
