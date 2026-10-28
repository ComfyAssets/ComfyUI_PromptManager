// System › Sources & scans (#249 System S4 Task 11): the concept's "Image sources" list (app.js 679–681) built from the
// server's configured, effective and watched sources (GET /settings), the host draft of `gallery_root_paths`, the
// 3.2.0 update notice's dismissal and the Refresh files toast (D98).
const test = require('node:test');
const assert = require('node:assert/strict');

const sourcesModule = import('../../web/js/workspace/system-sources.mjs');

const STATUS = Object.freeze({
    configured: [
        { label: 'ComfyUI output', path: 'output', valid: true, code: null, reason: null },
        { label: 'ComfyUI input', path: 'input', valid: true, code: null, reason: null },
    ],
    effective: [
        { label: 'ComfyUI output', path: 'output', root_index: 0, watch: 'watching' },
        { label: 'ComfyUI input', path: 'input', root_index: 1, watch: 'manual' },
    ],
    auto_detected: false,
});

test('each effective source is the concept row: label, "<path> · watching|manual scan", Scan changes or Scan folder', async () => {
    const { sourceRows } = await sourcesModule;
    const saved = ['output', 'input'];
    const { rows, note } = sourceRows({ sources: STATUS, saved, current: saved });
    assert.equal(note, null);
    assert.deepEqual(rows, [
        { key: 'output', title: 'ComfyUI output', description: 'output · watching', scan: { label: 'Scan changes', root: 0, path: 'output' },
            remove: 'output' },
        { key: 'input', title: 'ComfyUI input', description: 'input · manual scan', scan: { label: 'Scan folder', root: 1, path: 'input' },
            remove: 'input' },
    ]);
});

test('watching only with a live observer; restart pending otherwise (D98)', async () => {
    const { sourceRows, WATCH_COPY } = await sourcesModule;
    assert.deepEqual(WATCH_COPY, { watching: 'watching', stopping: 'watching until restart', manual: 'manual scan',
        restart_pending: 'restart pending' });
    const sources = { ...STATUS, effective: STATUS.effective.map(source => ({ ...source, watch: 'restart_pending' })) };
    const { rows } = sourceRows({ sources, saved: ['output', 'input'], current: ['output', 'input'] });
    assert.deepEqual(rows.map(row => [row.description, row.scan.label]),
        [['output · restart pending', 'Scan folder'], ['input · restart pending', 'Scan folder']]);
});

test('a configured source that cannot be used shows the server reason and only Remove', async () => {
    const { sourceRows } = await sourcesModule;
    const sources = { ...STATUS, configured: [...STATUS.configured,
        { label: 'unmounted-drive', path: 'unmounted-drive', valid: false, code: 'not-found', reason: 'Gallery root does not exist' }] };
    const saved = ['output', 'input', 'unmounted-drive'];
    const { rows } = sourceRows({ sources, saved, current: saved });
    assert.deepEqual(rows.at(-1), { key: 'unmounted-drive', title: 'unmounted-drive',
        description: 'unmounted-drive · Gallery root does not exist', scan: null, remove: 'unmounted-drive' });
});

test('an added source is listed as not saved yet with its validated label; a removed one leaves the list', async () => {
    const { sourceRows } = await sourcesModule;
    const labels = new Map([['output/archive', 'archive']]);
    const { rows, note } = sourceRows({ sources: STATUS, saved: ['output', 'input'], current: ['input', 'output/archive'], labels });
    assert.equal(note, null);
    assert.deepEqual(rows.map(row => [row.title, row.description, row.scan?.label ?? null, row.remove]), [
        ['ComfyUI input', 'input · manual scan', 'Scan folder', 'input'],
        ['archive', 'output/archive · not saved yet', null, 'output/archive'],
    ]);
    // Without a recorded label (a reload of System), the folder name stands in.
    assert.equal(sourceRows({ sources: STATUS, saved: [], current: ['output/new-collection'] }).rows[0].title, 'new-collection');
});

test('removing the last entry states auto-detect (D98)', async () => {
    const { sourceRows, NOTES } = await sourcesModule;
    const { rows, note } = sourceRows({ sources: STATUS, saved: ['output', 'input'], current: [] });
    assert.deepEqual(rows, []);
    assert.equal(note, NOTES.removedAll);
    assert.equal(NOTES.removedAll, 'No source is configured. After saving, ComfyUI’s output folder is used.');
});

test('auto-detected sources are listed without Remove and read as the default: "ComfyUI/<path> · <watch> · default"', async () => {
    const { sourceRows, NOTES } = await sourcesModule;
    const auto = { configured: [], auto_detected: true,
        effective: [{ label: 'ComfyUI output', path: 'output', root_index: 0, watch: 'watching', in_comfyui: true }] };
    // The row says it is the default, so no line under the list repeats it; the scan keeps the server's public path.
    assert.deepEqual(sourceRows({ sources: auto, saved: [], current: [] }), { rows: [
        { key: 'auto:0', title: 'ComfyUI output', description: 'ComfyUI/output · watching · default',
            scan: { label: 'Scan changes', root: 0, path: 'output' }, remove: null },
    ], note: null });
    assert.equal('autoDetected' in NOTES, false);
    // An output folder outside the ComfyUI base (--output-directory) is shown by its own name.
    const outside = { ...auto, effective: [{ label: 'Renders', path: 'Renders', root_index: 0, watch: 'manual', in_comfyui: false }] };
    assert.equal(sourceRows({ sources: outside, saved: [], current: [] }).rows[0].description, 'Renders · manual scan · default');
    // Every configured source unusable: ComfyUI's output folder is used instead, and the line says why.
    const fallback = { ...auto, configured: [{ label: 'gone', path: 'gone', valid: false, code: 'not-found', reason: 'Gallery root does not exist' }] };
    const result = sourceRows({ sources: fallback, saved: ['gone'], current: ['gone'] });
    assert.deepEqual(result.rows.map(row => [row.title, row.description, row.remove]),
        [['gone', 'gone · Gallery root does not exist', 'gone'], ['ComfyUI output', 'ComfyUI/output · watching · default', null]]);
    assert.equal(result.note, NOTES.fallback);
    assert.equal(sourceRows({ sources: { configured: [], effective: [], auto_detected: false }, saved: [], current: [] }).note, NOTES.none);
});

test('a configured source keeps its plain public path even inside ComfyUI', async () => {
    const { sourceRows } = await sourcesModule;
    const sources = { ...STATUS, effective: STATUS.effective.map(source => ({ ...source, in_comfyui: true })) };
    const { rows } = sourceRows({ sources, saved: ['output', 'input'], current: ['output', 'input'] });
    assert.deepEqual(rows.map(row => row.description), ['output · watching', 'input · manual scan']);
});

test('a path listed twice in the draft is one row', async () => {
    const { sourceRows } = await sourcesModule;
    assert.equal(sourceRows({ sources: STATUS, saved: ['output', 'input'], current: ['output', 'output'] }).rows.length, 1);
});

test('roots equal to the saved list are the saved list itself, so the draft is clean again', async () => {
    const { draftRoots } = await sourcesModule;
    const saved = ['output', 'input'];
    assert.equal(draftRoots(['output', 'input'], saved), saved);
    assert.deepEqual(draftRoots(['input'], saved), ['input']);
    assert.notEqual(draftRoots(['input', 'output'], saved), saved);
    assert.deepEqual(draftRoots([], undefined), []);
});

test('notice Later persists the 3.2.0 dismissal (pm_update_notice_dismissed); storage that throws keeps it for this view', async () => {
    const { noticeDismissed, dismissNotice, NOTICE_KEY, NOTICE_VERSION } = await sourcesModule;
    assert.deepEqual([NOTICE_KEY, NOTICE_VERSION], ['pm_update_notice_dismissed', '3.2.0']);
    const values = new Map();
    const storage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, String(value)) };
    assert.equal(noticeDismissed(() => storage), false);
    values.set(NOTICE_KEY, '3.1.0');
    assert.equal(noticeDismissed(() => storage), false);
    assert.equal(dismissNotice(() => storage), true);
    assert.equal(values.get(NOTICE_KEY), '3.2.0');
    assert.equal(noticeDismissed(() => storage), true);
    const denied = () => { throw new Error('SecurityError'); };
    assert.equal(noticeDismissed(denied), false);
    assert.equal(dismissNotice(denied), false);
    assert.equal(dismissNotice(() => ({ setItem() { throw new Error('QuotaExceededError'); } })), false);
});

test('Refresh files toasts the rescan counts, or says it finished when they are missing', async () => {
    const { refreshedText } = await sourcesModule;
    assert.equal(refreshedText({ total: 1234, roots: 2 }), 'Files refreshed. 1,234 files in 2 output folders.');
    assert.equal(refreshedText({ total: 1, roots: 1 }), 'Files refreshed. 1 file in 1 output folder.');
    assert.equal(refreshedText({ total: -1, roots: 1 }), 'Files refreshed.');
    assert.equal(refreshedText(null), 'Files refreshed.');
});

test('monitoring switched off: a source still watched says so until the restart (D128)', async () => {
    const { sourceRows } = await sourcesModule;
    const sources = { ...STATUS, effective: [{ ...STATUS.effective[0], watch: 'stopping' }, STATUS.effective[1]] };
    const { rows } = sourceRows({ sources, saved: ['output', 'input'], current: ['output', 'input'] });
    assert.deepEqual(rows.map(row => [row.description, row.scan.label]),
        [['output · watching until restart', 'Scan changes'], ['input · manual scan', 'Scan folder']]);
});

test('a valid configured source missing from the effective list is another spelling of a listed folder (D128)', async () => {
    const { sourceRows, DUPLICATE_SOURCE } = await sourcesModule;
    assert.equal(DUPLICATE_SOURCE, 'same folder as another source');
    const sources = { ...STATUS, configured: [...STATUS.configured,
        { label: 'output', path: 'output/.', valid: true, code: null, reason: null }] };
    const saved = ['output', 'input', 'output/.'];
    assert.deepEqual(sourceRows({ sources, saved, current: saved }).rows.at(-1), { key: 'output/.', title: 'output',
        description: 'output/. · same folder as another source', scan: null, remove: 'output/.' });
});

test('D147: "Save prompts from other workflows" drafts save_foreign_runs, disabled while monitoring is off (#247)', async () => {
    const { FOREIGN_RUNS, foreignRunsState } = await sourcesModule;
    assert.deepEqual(FOREIGN_RUNS, { id: 'foreign-runs', key: 'save_foreign_runs', label: 'Save prompts from other workflows',
        description: 'New outputs of workflows without a PromptManager node add their prompt to the library when it is certain.',
        off: 'Applies while Watch for new outputs is on.' });
    assert.deepEqual(foreignRunsState({ value: true, monitoring: true }), { disabled: false, checked: true, note: null });
    assert.deepEqual(foreignRunsState({ value: false, monitoring: true }), { disabled: false, checked: false, note: null });
    // Monitoring drafted or saved off: nothing is watched, so the row says why it is disabled and keeps its value.
    assert.deepEqual(foreignRunsState({ value: true, monitoring: false }), { disabled: true, checked: true, note: FOREIGN_RUNS.off });
    // Not read yet (or the host read failed): disabled without a value, the host rows' own note applies.
    assert.deepEqual(foreignRunsState({ value: undefined, monitoring: undefined }), { disabled: true, checked: null, note: null });
    assert.deepEqual(foreignRunsState({ value: undefined, monitoring: false }), { disabled: true, checked: null, note: null });
});
