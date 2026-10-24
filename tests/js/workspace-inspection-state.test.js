import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { extractGenerationFacts } = require('../../web/js/comfy-metadata.js');
const { ApiError } = require('../../web/js/api-client.js');
const module = await import('../../web/js/workspace/inspection-state.mjs').catch(error => {
    if (error.code !== 'ERR_MODULE_NOT_FOUND') throw error;
    return {};
});
const scopeA = 'a'.repeat(64), scopeB = 'b'.repeat(64);
const refreshMessage = 'The source changed. Refresh the image collection before inspecting this file.';
const fields = ['seed', 'steps', 'cfg', 'sampler', 'scheduler', 'model'];
function contract(name) { assert.equal(typeof module[name], 'function', `${name} contract is exported`); return module[name]; }
function image(root_scope = scopeA) {
    return { root_index: 0, path: 'folder/a #%.png', filename: 'a #%.png', url: `/prompt_manager/images/serve/folder/a%20%23%25.png?root=0${root_scope === undefined ? '' : `&root_scope=${root_scope}`}`, ...(root_scope === undefined ? {} : { root_scope }) };
}
function envelope(entry = image()) {
    return {
        success: true, inspection_version: 1, root_scope: entry.root_scope ?? scopeA,
        image: { root_index: entry.root_index, path: entry.path, filename: entry.filename },
        file: { observation: 'current-file', source_label: 'same label', media_type: 'image', extension: '.png', format: 'PNG', size: 123, width: 720, height: 480, modified_time: 1234 },
        prompts: { positive: { status: 'resolved', text: 'exact\npositive', source: 'execution' }, negative: { status: 'ambiguous', text: null, source: null } },
        metadata: {
            status: 'available', omitted_reason: null, raw: { prompt: '{"rounded":"raw copy"}' }, record_formats: { prompt: 'reserialized-json' },
            prompt_graph: { status: 'parsed', value: { s: { class_type: 'KSampler', inputs: { seed: '9007199254740993', steps: 28, cfg: '6.5000000000000000001', sampler_name: 'euler', scheduler: 'normal', model: ['model', 0] } }, model: { class_type: 'CheckpointLoaderSimple', inputs: { ckpt_name: 'model.safetensors' } } } },
            workflow_graph: { status: 'absent', value: null }, parameters: null,
        },
        resolution: { state: 'ambiguous', sampler_id: null, reason: 'unknown negative' },
        library: { status: 'linked', scope: 'saved-library', positive_id: 7, pair_id: 8, title: 'Recipe', category: 'Subject', tags: ['cat'], category_scope: 'pair', tags_scope: 'pair' },
    };
}
function pendingApi() {
    const requests = [];
    const api = { get: (url, { signal }) => new Promise((resolve, reject) => requests.push({ url, signal, resolve, reject })) };
    return { api, requests };
}

test('original media validation is strict before inspection and preserves rooted URLs', () => {
    const validate = contract('originalMediaDownload'), entry = image();
    assert.deepEqual(validate(entry), {url:entry.url,filename:'a #%.png'});
    for (const change of [{url:'https://other.test/prompt_manager/images/serve/folder/a%20%23%25.png?root=0'},
        {url:entry.url.replace('root=0','root=1')},{url:entry.url.replace(scopeA,scopeB)},
        {url:entry.url.replace('folder/a','elsewhere/a')},{url:entry.url.replace(`&root_scope=${scopeA}`,'')},
        {url:entry.url+'&root=0'},{url:entry.url+'#wrong'},{path:'../a.png'},{root_scope:''}]) {
        assert.throws(() => validate({...entry,...change}), TypeError);
    }
    const before = Object.getOwnPropertyDescriptor(globalThis,'window');
    Object.defineProperty(globalThis,'window',{configurable:true,value:{location:{href:'https://example.test/proxy/prompt_manager/workspace'},ApiClient:{resolveUrl:url=>`/proxy${url}`}}});
    try { assert.deepEqual(validate(entry),{url:`/proxy${entry.url}`,filename:'a #%.png'}); }
    finally { if (before) Object.defineProperty(globalThis,'window',before); else delete globalThis.window; }
});

test('record preserves exact projections and unknown roles without execution fact promotion', () => {
    const entry = image(), data = envelope(entry);
    const record = contract('toInspectionRecord')(data, entry, extractGenerationFacts);
    assert.deepEqual(record.identity, { kind: 'rooted-file', key: '0:folder/a #%.png', root_index: 0, path: entry.path, root_scope: scopeA });
    assert.equal(record.generation.seed.value, '9007199254740993');
    assert.equal(record.generation.cfg.value, '6.5000000000000000001');
    assert.equal(record.generation.seed.evidence, 'configuration');
    assert.equal(record.prompts.negative.status, 'ambiguous');
    assert.equal(record.prompts.negative.text, null);
    assert.equal(record.historyTarget, '#/library/7');
    assert.deepEqual(record.download, { url: entry.url, filename: entry.filename });
    assert.equal(record.file.source_label, 'same label');
    for (const status of ['none', 'ambiguous', 'unavailable', 'limited']) {
        const result = envelope(entry); result.library.status = status;
        assert.equal(module.toInspectionRecord(result, entry, extractGenerationFacts).historyTarget, null);
    }
});

test('record format validation never guesses original text', () => {
    const toRecord = contract('toInspectionRecord'), entry = image();
    for (const mutate of [
        data => delete data.metadata.record_formats,
        data => { data.metadata.record_formats = { prompt: 'guess' }; },
        data => { data.metadata.record_formats.extra = 'original-text'; },
        data => { data.metadata.record_formats.prompt = 'invalid'; },
        data => { data.metadata.raw.workflow = '{}'; },
        data => { data.metadata.raw.prompt = {}; },
        data => { data.metadata.raw.extra = 'secret'; },
        data => { data.metadata.parameters = 'manufactured'; },
        data => { data.metadata.prompt_graph = { status: 'absent', value: null }; },
        data => { data.metadata.record_formats = {}; data.metadata.raw = {}; },
        data => { data.metadata.status = 'limited'; },
    ]) {
        const data = envelope(entry); mutate(data);
        assert.throws(() => toRecord(data, entry, extractGenerationFacts), TypeError);
    }
    const data = envelope(entry);
    data.metadata.record_formats.workflow = 'invalid';
    data.metadata.workflow_graph = { status: 'malformed', value: null };
    assert.equal(toRecord(data, entry, extractGenerationFacts).metadata.record_formats.workflow, 'invalid');
    data.metadata.raw = {}; data.metadata.status = 'limited'; data.metadata.omitted_reason = 'response-size-limit';
    data.metadata.prompt_graph = { status: 'limited', value: null };
    data.metadata.workflow_graph = { status: 'limited', value: null };
    const limited = toRecord(data, entry, extractGenerationFacts);
    assert.deepEqual(limited.metadata.record_formats, { prompt: 'reserialized-json', workflow: 'invalid' });
    assert.equal(limited.generation.seed.status, 'unresolved');
    assert.equal(limited.generation.seed.reason, 'projection-limited');
    let seen;
    toRecord(envelope(entry), entry, input => { seen = input; return extractGenerationFacts(input); });
    assert.deepEqual(seen.recordFormats, { prompt: 'reserialized-json' });
    assert.equal(seen.raw, undefined, 'facts never receive raw graphs for reparsing');
});

test('record accepts actual atomic omission producer states', () => {
    const toRecord = contract('toInspectionRecord');
    for (const wholeRaw of [false, true]) {
        const data = envelope();
        data.metadata.record_formats.workflow = 'invalid';
        data.metadata.prompt_graph = { status: 'limited', value: null };
        data.metadata.workflow_graph = { status: 'limited', value: null };
        if (wholeRaw) Object.assign(data.metadata, { raw: {}, parameters: null, status: 'limited', omitted_reason: 'response-size-limit' });
        let record;
        assert.doesNotThrow(() => { record = toRecord(data, image(), extractGenerationFacts); }, 'actual producer omission remains renderable');
        assert.deepEqual(record.metadata.raw, wholeRaw ? {} : { prompt: '{"rounded":"raw copy"}' });
        assert.equal(record.metadata.status, wholeRaw ? 'limited' : 'available');
        assert.equal(record.metadata.omitted_reason, wholeRaw ? 'response-size-limit' : null);
        assert.deepEqual(record.metadata.record_formats, { prompt: 'reserialized-json', workflow: 'invalid' });
        assert.equal(record.prompts.positive.text, 'exact\npositive');
        assert.equal(record.download.url, image().url);
        assert.ok(Object.values(record.generation).every(fact => fact.status === 'unresolved' && fact.reason === 'projection-limited'));
    }
    const absent = envelope();
    Object.assign(absent.metadata, { status: 'limited', omitted_reason: 'response-size-limit', raw: {}, record_formats: { parameters: 'original-text' }, parameters: null, prompt_graph: { status: 'absent', value: null } });
    assert.equal(toRecord(absent, image(), extractGenerationFacts).generation.seed.reason, 'projection-limited');
});

test('record rejects contradictory omission provenance before extracting facts', () => {
    const toRecord = contract('toInspectionRecord');
    for (const mutate of [
        data => Object.assign(data.metadata, { status: 'limited', omitted_reason: 'response-size-limit' }),
        data => Object.assign(data.metadata, { status: 'limited', omitted_reason: 'response-size-limit', raw: {} }),
        data => { data.metadata.raw = {}; },
        data => { data.metadata.record_formats.workflow = 'original-text'; data.metadata.workflow_graph = { status: 'parsed', value: { nodes: [] } }; },
        data => { Object.assign(data.metadata, { status: 'limited', omitted_reason: 'response-size-limit', raw: {}, prompt_graph: { status: 'limited', value: null } }); data.metadata.record_formats.workflow = 'invalid'; data.metadata.workflow_graph = { status: 'malformed', value: null }; },
        data => { data.metadata.record_formats.workflow = 'invalid'; data.metadata.raw.workflow = 'null'; data.metadata.workflow_graph = { status: 'limited', value: null }; },
        // The response budget omits the workflow graph before the prompt graph, so an
        // omitted invalid prompt graph means the workflow graph was omitted too.
        data => { data.metadata.record_formats.prompt = 'invalid'; data.metadata.raw = {}; data.metadata.prompt_graph = { status: 'limited', value: null }; data.metadata.record_formats.workflow = 'original-text'; data.metadata.raw.workflow = '{"nodes":[]}'; data.metadata.workflow_graph = { status: 'parsed', value: { nodes: [] } }; },
    ]) {
        const data = envelope(); mutate(data); let extractions = 0;
        assert.throws(() => toRecord(data, image(), () => { extractions++; return extractGenerationFacts({}); }), TypeError);
        assert.equal(extractions, 0, 'contradictory transport cannot produce known facts');
    }
});

function limitedWorkflow(data = envelope()) {
    data.metadata.record_formats.workflow = 'limited';
    data.metadata.workflow_graph = { status: 'limited', value: null };
    return data;
}

test('record accepts limited workflow format without raw and with a limited graph', () => {
    const toRecord = contract('toInspectionRecord');
    const record = toRecord(limitedWorkflow(), image(), extractGenerationFacts);
    assert.deepEqual(record.metadata.record_formats, { prompt: 'reserialized-json', workflow: 'limited' });
    assert.deepEqual(record.metadata.raw, { prompt: '{"rounded":"raw copy"}' });
    assert.deepEqual(record.metadata.workflow_graph, { status: 'limited', value: null });
    assert.equal(record.metadata.status, 'available');
    assert.equal(record.generation.seed.value, '9007199254740993', 'a limited workflow never hides prompt-graph facts');
    const workflowOnly = limitedWorkflow();
    Object.assign(workflowOnly.metadata, { raw: {}, record_formats: { workflow: 'limited' }, prompt_graph: { status: 'absent', value: null } });
    const facts = toRecord(workflowOnly, image(), extractGenerationFacts).generation;
    assert.ok(Object.values(facts).every(fact => fact.status === 'unresolved' && fact.reason === 'projection-limited'));
    const omitted = limitedWorkflow();
    Object.assign(omitted.metadata, { raw: {}, parameters: null, status: 'limited', omitted_reason: 'response-size-limit', prompt_graph: { status: 'limited', value: null } });
    assert.deepEqual(toRecord(omitted, image(), extractGenerationFacts).metadata.record_formats, { prompt: 'reserialized-json', workflow: 'limited' });
});

test('record rejects limited format with raw present', () => {
    const toRecord = contract('toInspectionRecord');
    for (const mutate of [
        data => { data.metadata.raw.workflow = '{"nodes":[]}'; },
        data => { data.metadata.workflow_graph = { status: 'parsed', value: { nodes: [] } }; },
        data => { data.metadata.workflow_graph = { status: 'malformed', value: null }; },
        data => { data.metadata.workflow_graph = { status: 'absent', value: null }; },
        // Role records fail closed on the server: they are never limited.
        data => { data.metadata.record_formats.prompt = 'limited'; delete data.metadata.raw.prompt; data.metadata.prompt_graph = { status: 'limited', value: null }; },
        data => { data.metadata.record_formats.parameters = 'limited'; },
    ]) {
        const data = limitedWorkflow(); mutate(data); let extractions = 0;
        assert.throws(() => toRecord(data, image(), () => { extractions++; return extractGenerationFacts({}); }), TypeError);
        assert.equal(extractions, 0);
    }
});

test('record accepts the workflow-first response omission of an invalid workflow record', () => {
    const data = envelope();
    data.metadata.record_formats.workflow = 'invalid';
    data.metadata.workflow_graph = { status: 'limited', value: null };
    const record = contract('toInspectionRecord')(data, image(), extractGenerationFacts);
    assert.deepEqual(record.metadata.prompt_graph.status, 'parsed', 'prompt evidence outlives workflow evidence');
    assert.equal(record.generation.seed.value, '9007199254740993');
});

test('record accepts closed reason codes and rejects contradictory ones', () => {
    const toRecord = contract('toInspectionRecord');
    const codes = ['work-limit', 'depth-limit', 'text-limit', 'graph-ceiling', 'unsupported-node', 'runtime-value', 'composite', 'no-sampler', 'malformed', 'multistage-differs'];
    for (const reason_code of codes) {
        const data = envelope(); Object.assign(data.resolution, { state: 'unresolved', reason_code, node: null });
        assert.doesNotThrow(() => toRecord(data, image(), extractGenerationFacts), reason_code);
    }
    const node = envelope(); Object.assign(node.resolution, { state: 'unresolved', reason_code: 'unsupported-node', node: { id: '7', class_type: 'LoRAfromText' } });
    assert.doesNotThrow(() => toRecord(node, image(), extractGenerationFacts));
    node.resolution.node.class_type = null; // the server names a non-string class as null
    assert.doesNotThrow(() => toRecord(node, image(), extractGenerationFacts));
    for (const state of ['resolved', 'absent', 'ambiguous', 'unresolved']) {
        const data = envelope(); Object.assign(data.resolution, { state, reason_code: null, node: null });
        assert.doesNotThrow(() => toRecord(data, image(), extractGenerationFacts), state);
    }
    for (const change of [
        { state: 'unresolved', reason_code: 'guess' }, { state: 'unresolved', reason_code: 1 },
        { state: 'resolved', reason_code: 'work-limit' }, { state: 'absent', reason_code: 'no-sampler' },
        { state: 'unresolved', reason_code: null, node: { id: '7', class_type: 'X' } },
        { state: 'unresolved', reason_code: 'unsupported-node', node: { id: 7, class_type: 'X' } },
        { state: 'unresolved', reason_code: 'unsupported-node', node: { id: '7' } },
        { state: 'unresolved', reason_code: 'unsupported-node', node: { id: '7', class_type: 7 } },
        { state: 'unresolved', reason_code: 'unsupported-node', node: 'X' },
    ]) {
        const data = envelope(); Object.assign(data.resolution, change); let extractions = 0;
        assert.throws(() => toRecord(data, image(), () => { extractions++; return extractGenerationFacts({}); }), TypeError, JSON.stringify(change));
        assert.equal(extractions, 0);
    }
});

test('resolution validator accepts closed reason codes and a bounded node record, rejects others', () => {
    const toRecord = contract('toInspectionRecord');
    for (const reason_code of ['unsupported-node', 'runtime-value', 'composite']) {
        for (const node of [{ id: '7', class_type: 'N'.repeat(200) }, { id: '7'.repeat(200), class_type: null }, null]) {
            const data = envelope(); Object.assign(data.resolution, { state: 'unresolved', reason_code, node });
            assert.doesNotThrow(() => toRecord(data, image(), extractGenerationFacts), JSON.stringify({ reason_code, node }));
        }
    }
    for (const change of [
        { reason_code: 'unsupported-node', node: { id: '7', class_type: 'N'.repeat(201) } },
        { reason_code: 'unsupported-node', node: { id: '7'.repeat(201), class_type: 'X' } },
        { reason_code: 'unsupported-node', node: { id: '', class_type: 'X' } },
        { reason_code: 'unsupported-node', node: { id: '7', class_type: 'X', extra: true } },
        { reason_code: 'unsupported-node', node: [] },
        { reason_code: 'malformed', node: { id: '7', class_type: 'X' } },
        { reason_code: 'work-limit', node: { id: '7', class_type: 'X' } },
        { reason_code: 'no-sampler', node: { id: '7', class_type: 'X' } },
        { reason_code: 'Unsupported-Node', node: null },
        { reason_code: '', node: null },
    ]) {
        const data = envelope(); Object.assign(data.resolution, { state: 'unresolved', ...change });
        assert.throws(() => toRecord(data, image(), extractGenerationFacts), TypeError, JSON.stringify(change));
    }
});

test('role outcome lines are bounded text on non-resolved roles only', () => {
    const toRecord = contract('toInspectionRecord');
    const line = 'Prompt uses a node the inspector does not support (<img src=x onerror=alert(1)>).';
    const data = envelope(); data.prompts.negative = { status: 'unresolved', text: null, source: null, message: line };
    assert.equal(toRecord(data, image(), extractGenerationFacts).prompts.negative.message, line);
    for (const [role, record] of [
        ['positive', { status: 'resolved', text: 'exact', source: 'execution', message: 'Hidden reason.' }],
        ['negative', { status: 'unresolved', text: null, source: null, message: '' }],
        ['negative', { status: 'unresolved', text: null, source: null, message: 7 }],
        ['negative', { status: 'unresolved', text: null, source: null, message: null }],
        ['negative', { status: 'unresolved', text: null, source: null, message: 'x'.repeat(513) }],
    ]) {
        const bad = envelope(); bad.prompts[role] = record;
        assert.throws(() => toRecord(bad, image(), extractGenerationFacts), TypeError, JSON.stringify(record));
    }
});

test('record rejects root path media and invalid versioned fields with retryable TypeError', () => {
    const toRecord = contract('toInspectionRecord');
    for (const mutate of [
        data => { data.image.root_index = 1; }, data => { data.image.path = 'other.png'; },
        data => { data.inspection_version = 2; }, data => { data.file.width = 0; },
        data => { delete data.success; },
        data => { data.file.size = -1; }, data => { data.library.positive_id = 9007199254740992; },
        data => { data.metadata.prompt_graph = { status: 'limited', value: {} }; },
        data => { data.resolution.sampler_id = {}; },
        data => { data.library.tags_scope = 'recipe'; },
        data => { data.library.category_scope = 'recipe'; },
        data => { delete data.library.category_scope; },
        data => { delete data.library.tags_scope; },
    ]) {
        const data = envelope(); mutate(data);
        assert.throws(() => toRecord(data, image(), extractGenerationFacts), error => error instanceof TypeError && !error.refreshRequired);
    }
    for (const url of ['https://attacker.invalid/prompt_manager/images/serve/folder/a%20%23%25.png?root=0', 'data:text/plain,unsafe', '/other/a.png', '/prompt_manager/images/serve/other.png?root=0', `/prompt_manager/images/serve/folder/a%20%23%25.png?root=1&root_scope=${scopeA}`, `/prompt_manager/images/serve/folder/a%20%23%25.png?root=0&root_scope=${scopeB}`, `/prompt_manager/images/serve/folder/a%20%23%25.png?root=0&root=1&root_scope=${scopeA}`]) {
        assert.throws(() => toRecord(envelope(), { ...image(), url }, extractGenerationFacts), TypeError);
    }
});

test('record preserves proxy originals and rejects opaque or normalized path escapes', () => {
    const toRecord = contract('toInspectionRecord');
    const previousLocation = globalThis.location, previousApi = globalThis.ApiClient;
    globalThis.location = { href: 'https://workspace.example/nested/proxy/prompt_manager/workspace', pathname: '/nested/proxy/prompt_manager/workspace' };
    globalThis.ApiClient = require('../../web/js/api-client.js');
    try {
        assert.equal(toRecord(envelope(), image(), extractGenerationFacts).download.url, `/nested/proxy${image().url}`);
        const absolute = { ...image(), url: `https://workspace.example:443/nested/proxy${image().url}` };
        assert.equal(toRecord(envelope(), absolute, extractGenerationFacts).download.url, absolute.url);
        for (const url of [`https://workspace.example.attacker.invalid/nested/proxy${image().url}`, `/nested/proxy/prompt_manager/images/serve/folder/../a%20%23%25.png?root=0&root_scope=${scopeA}`, `//attacker.invalid${image().url}`]) assert.throws(() => toRecord(envelope(), { ...image(), url }, extractGenerationFacts), TypeError);
    } finally { globalThis.location = previousLocation; globalThis.ApiClient = previousApi; }
});

test('inspectPath does not coerce or strip malformed scopes', () => {
    const inspectPath = contract('inspectPath');
    assert.equal(inspectPath(image()), `/prompt_manager/images/inspect/folder/a%20%23%25.png?root=0&root_scope=${scopeA}`);
    assert.equal(inspectPath({ root_index: 2, path: 'a b/x#%.png' }), '/prompt_manager/images/inspect/a%20b/x%23%25.png?root=2');
    for (const root_scope of [null, true, 1, {}, 'a'.repeat(63), 'A'.repeat(64), scopeA + '\n']) {
        assert.throws(() => inspectPath({ ...image(), root_scope }), TypeError);
    }
    for (const entry of [{ root_index: '0', path: 'a.png' }, { root_index: 0, path: '../a.png' }, { root_index: 0, path: 'a//b.png' }, { root_index: 0, path: '/a.png' }, { root_index: 0, path: 'C:file.png' }]) assert.throws(() => inspectPath(entry), TypeError);
});

test('controller shares current request across subscribers', async () => {
    const { api, requests } = pendingApi(), updates = [[], []];
    const controller = contract('createInspectionController')({ api, extractFacts: extractGenerationFacts });
    const off = controller.subscribe(state => updates[0].push(state));
    controller.subscribe(state => updates[1].push(state));
    assert.equal(updates[0][0].record, null);
    const first = controller.select(image()), second = controller.select({ ...image() });
    assert.equal(requests.length, 1);
    assert.equal(controller.state.loading, true);
    requests[0].resolve(envelope()); await Promise.all([first, second]);
    assert.equal(controller.state.record.generation.seed.value, '9007199254740993');
    assert.equal(updates[0].at(-1), updates[1].at(-1));
    await controller.select(image()); assert.equal(requests.length, 1);
    off(); const count = updates[0].length;
    const retry = controller.retry(); assert.equal(controller.state.record, null); assert.equal(controller.state.prompts, undefined);
    assert.equal(requests.length, 2); assert.ok(requests[1].url.endsWith(`root_scope=${scopeA}`));
    requests[1].resolve(envelope()); await retry;
    assert.equal(updates[0].length, count);
    controller.clear(); assert.equal(controller.state.record, null); assert.equal(controller.state.loading, false);
    const reselect = controller.select(image()); assert.equal(requests.length, 3);
    requests[2].resolve(envelope()); await reselect; controller.dispose();
});

test('root scope echo and source-changed require refresh', async () => {
    const SourceChanged = contract('InspectionSourceChangedError'), toRecord = contract('toInspectionRecord');
    for (const root_scope of [undefined, scopeB]) {
        const data = envelope();
        if (root_scope === undefined) delete data.root_scope; else data.root_scope = root_scope;
        assert.throws(() => toRecord(data, image(), extractGenerationFacts), error => error instanceof SourceChanged && error.refreshRequired && error.message === refreshMessage);
        const { api, requests } = pendingApi();
        const controller = contract('createInspectionController')({ api, extractFacts: extractGenerationFacts });
        const load = controller.select(image()); requests[0].resolve(data); await load;
        assert.deepEqual([controller.state.error, controller.state.refreshRequired, controller.state.record, controller.state.prompts, controller.state.message], [true, true, null, undefined, refreshMessage]);
        await controller.retry(); assert.equal(requests.length, 1); controller.dispose();
    }
    for (const [status, code, refresh] of [[409, 'source-changed', true], [409, null, false], [409, 'file-changed', false], [500, 'source-changed', false]]) {
        const { api, requests } = pendingApi();
        const controller = module.createInspectionController({ api, extractFacts: extractGenerationFacts });
        const load = controller.select(image()); requests[0].reject(new ApiError(status, refreshMessage, code)); await load;
        assert.equal(controller.state.refreshRequired, refresh);
        const retry = controller.retry(); assert.equal(requests.length, refresh ? 1 : 2);
        if (!refresh) { requests[1].resolve(envelope()); await retry; assert.equal(controller.state.error, false); }
        controller.dispose();
    }
});

test('same image key with changed scope supersedes pending actions', async () => {
    const { api, requests } = pendingApi();
    const controller = contract('createInspectionController')({ api, extractFacts: extractGenerationFacts });
    const first = controller.select(image());
    requests[0].resolve(envelope()); await first;
    const changed = image(scopeB), next = controller.select(changed);
    assert.equal(controller.state.record, null); assert.equal(controller.state.prompts, undefined);
    assert.equal(requests.length, 2);
    requests[1].resolve(envelope(changed)); await next;
    assert.equal(controller.state.record.identity.root_scope, scopeB);
    assert.equal(controller.state.record.file.source_label, 'same label');
    controller.dispose();
});

test('unscoped role-only callers stay compatible', async () => {
    const toRecord = contract('toInspectionRecord'), entry = { root_index: 0, path: 'old.png' };
    const data = { prompts: { positive: { status: 'resolved', text: 'old', source: 'graph-inferred' }, negative: { status: 'unresolved', text: null, source: null } } };
    const record = toRecord(data, entry, extractGenerationFacts);
    assert.equal(record.identity.root_scope, null); assert.equal(record.identity.key, '0:old.png');
    for (const key of ['file', 'metadata', 'library', 'download', 'historyTarget']) assert.equal(record[key], null);
    for (const field of fields) assert.deepEqual(record.generation[field], { status: 'unresolved', value: null, source: null, evidence: null, reason: null, sampler_id: null });
    assert.deepEqual(record.prompts, data.prompts);
    const changes = [];
    const loader = contract('createInspectionLoader')({ get: async () => data }, value => changes.push(value));
    await loader.load(entry);
    assert.deepEqual(changes.at(-1).prompts, data.prompts); assert.equal(changes.at(-1).record.identity.key, '0:old.png');
    loader.dispose();
    assert.throws(() => toRecord(data, image(), extractGenerationFacts), error => error.refreshRequired === true);
    const scoped = toRecord({ ...data, root_scope: scopeA }, image(), extractGenerationFacts);
    assert.equal(scoped.metadata, null); assert.equal(scoped.download.url, image().url);
});

test('disposed or superseded responses never publish', async () => {
    const { api, requests } = pendingApi(), updates = [];
    const controller = contract('createInspectionController')({ api, extractFacts: extractGenerationFacts });
    controller.subscribe(state => updates.push(state));
    const first = controller.select(image()), newer = image(scopeB), second = controller.select(newer);
    assert.equal(requests[0].signal.aborted, true);
    requests[1].resolve(envelope(newer)); await second;
    const count = updates.length;
    requests[0].reject(new ApiError(409, refreshMessage, 'source-changed')); await first;
    assert.equal(updates.length, count); assert.equal(controller.state.record.identity.root_scope, scopeB);
    const third = controller.retry(); controller.clear();
    assert.equal(requests[2].signal.aborted, true);
    requests[2].resolve(envelope(newer)); await third;
    assert.equal(controller.state.record, null);
    const final = controller.select(image()); controller.dispose(); const disposedCount = updates.length;
    requests[3].resolve(envelope()); await final;
    await controller.select(newer); await controller.retry(); controller.clear();
    assert.equal(updates.length, disposedCount); assert.equal(requests.length, 4);
});

const invalidSelections = () => [null, undefined, 0, 'new.png', {}, { ...image(), path: '../new.png' }, { ...image(), root_scope: null }, { ...image(), root_scope: 'malformed' }];

test('invalid attempted selections clear completed actions and retry validation only', async () => {
    for (const invalid of invalidSelections()) {
        const { api, requests } = pendingApi();
        const controller = contract('createInspectionController')({ api, extractFacts: extractGenerationFacts });
        const previous = controller.select(image()); requests[0].resolve(envelope()); await previous;
        assert.equal(controller.state.prompts.positive.text, 'exact\npositive');
        let rejected, thrown;
        try { rejected = controller.select(invalid); } catch (error) { thrown = error; }
        assert.equal(controller.state.record, null, 'invalid navigation cannot retain completed actions'); assert.equal(controller.state.prompts, undefined);
        assert.equal(thrown, undefined, 'selection validation uses the retryable state');
        await rejected;
        assert.deepEqual([controller.state.loading, controller.state.error, controller.state.refreshRequired, controller.state.message], [false, true, false, 'Could not read details from this file.']);
        await controller.retry(); await controller.retry(); assert.equal(requests.length, 1);
        const valid = controller.select(image()); assert.equal(requests.length, 2);
        requests[1].resolve(envelope()); await valid;
        await controller.select({ ...image() }); assert.equal(requests.length, 2);
        controller.dispose();
    }
});

test('invalid attempted selections abort pending reads and suppress late results', async () => {
    for (const invalid of invalidSelections()) {
        const { api, requests } = pendingApi(), changes = [];
        const controller = contract('createInspectionController')({ api, extractFacts: extractGenerationFacts });
        controller.subscribe(state => changes.push(state));
        const previous = controller.select(image());
        let rejected, thrown;
        try { rejected = controller.select(invalid); } catch (error) { thrown = error; }
        assert.equal(requests[0].signal.aborted, true);
        assert.equal(thrown, undefined, 'selection validation uses the retryable state');
        assert.equal(controller.state.record, null); assert.equal(controller.state.prompts, undefined);
        await rejected; const count = changes.length;
        requests[0].resolve(envelope()); await previous;
        assert.equal(changes.length, count); assert.equal(controller.state.error, true);
        assert.equal(controller.state.record, null); assert.equal(controller.state.prompts, undefined);
        await controller.retry(); assert.equal(requests.length, 1);
        controller.dispose();
    }
});

test('explicit rooted adapter preserves the strict default identity and download contract', () => {
    assert.ok(module.rootedFileAdapter, 'the default rooted adapter is exported');
    const a = module.rootedFileAdapter, entry = image();
    assert.equal(a.key(entry), '0:folder/a #%.png'); assert.equal(a.scope(entry), scopeA);
    assert.equal(a.inspectionPath(entry), module.inspectPath(entry));
    assert.deepEqual(a.originalDownload(entry), module.originalMediaDownload(entry));
    assert.deepEqual(a.toRecord(envelope(), entry, extractGenerationFacts), module.toInspectionRecord(envelope(), entry, extractGenerationFacts));
    assert.throws(() => a.validate({kind:'reported-location',path:'a.png'}), TypeError);
});

test('rooted controller keeps legacy descriptors with ignored noncloneable fields source compatible', async () => {
    const {api,requests}=pendingApi(),controller=module.createInspectionController({api,extractFacts:extractGenerationFacts});
    const selected={...image(),ignoredCallback(){}};
    let pending;assert.doesNotThrow(()=>{pending=controller.select(selected);});
    requests[0].resolve(envelope());await pending;assert.equal(controller.state.record.identity.key,'0:folder/a #%.png');controller.dispose();
});

// The maintainer's standard pipeline: one PromptManager pair feeds a base pass, an
// upscale pass and a FaceDetailer, saved by a custom saver. Text and seeds are invented.
function detailerPipelineGraph() {
    return {
        1: { class_type: 'CheckpointLoaderSimple', inputs: { ckpt_name: 'Base/example_model.safetensors' } },
        5: { class_type: 'KSampler', inputs: { seed: 111, steps: 4, cfg: 1.0, sampler_name: 'lcm', scheduler: 'normal', denoise: 1.0, model: ['47', 0], positive: ['25', 0], negative: ['26', 0], latent_image: ['20', 0] } },
        6: { class_type: 'VAEDecode', inputs: { samples: ['5', 0], vae: ['1', 2] } },
        7: { class_type: 'PreviewImage', inputs: { images: ['6', 0] } },
        8: { class_type: 'UpscaleModelLoader', inputs: { model_name: 'upscaler.pth' } },
        9: { class_type: 'ImageUpscaleWithModel', inputs: { upscale_model: ['8', 0], image: ['6', 0] } },
        10: { class_type: 'ImageScaleBy', inputs: { upscale_method: 'lanczos', scale_by: 0.75, image: ['9', 0] } },
        11: { class_type: 'VAEEncode', inputs: { pixels: ['10', 0], vae: ['1', 2] } },
        12: { class_type: 'KSampler', inputs: { seed: 222, steps: 6, cfg: 1.0, sampler_name: 'lcm', scheduler: 'normal', denoise: 0.45, model: ['47', 0], positive: ['25', 0], negative: ['26', 0], latent_image: ['11', 0] } },
        13: { class_type: 'VAEDecode', inputs: { samples: ['12', 0], vae: ['1', 2] } },
        17: { class_type: 'FaceDetailer', inputs: { seed: 333, steps: 4, cfg: 1.0, sampler_name: 'lcm', scheduler: 'normal', denoise: 0.4, image: ['13', 0], model: ['47', 0], clip: ['47', 1], vae: ['1', 2], positive: ['25', 0], negative: ['26', 0] } },
        20: { class_type: 'EmptyLatentBatch', inputs: { width: 640, height: 1536, batch_size: 1 } },
        25: { class_type: 'PromptManager', inputs: { text: 'a lighthouse at dusk, oil painting', category: '', tags: '', search_text: '', prepend_text: '', append_text: '', clip: ['47', 1] } },
        26: { class_type: 'PromptManager', inputs: { text: 'blurry, watermark', category: '', tags: '', search_text: '', prepend_text: '', append_text: '', clip: ['47', 1] } },
        47: { class_type: 'Lora Loader (LoraManager)', inputs: { text: '', model: ['1', 0], clip: ['1', 1] } },
        48: { class_type: 'KikoSaveImage', inputs: { filename_prefix: ['49', 0], format: 'PNG', images: ['17', 0] } },
        49: { class_type: 'KikoProjectFilePath', inputs: { project_name: 'example', filename: 'image' } },
    };
}

test('workflow prompts and settings match the legacy parser when the server cannot decide', t => {
    const parser = require('../../web/js/comfy-metadata.js'), { extractGenerationParams } = parser;
    globalThis.ComfyMetadata = parser;
    t.after(() => { delete globalThis.ComfyMetadata; });
    const entry = image(), data = envelope(entry);
    data.prompts = { positive: { status: 'ambiguous', text: null, source: null }, negative: { status: 'ambiguous', text: null, source: null } };
    data.metadata.prompt_graph = { status: 'parsed', value: detailerPipelineGraph() };
    data.resolution = { state: 'ambiguous', sampler_id: null, reason: 'Multiple sampler branches' };
    const record = contract('toInspectionRecord')(data, entry, extractGenerationFacts);
    const legacy = extractGenerationParams({ prompt: detailerPipelineGraph() });
    assert.deepEqual(record.prompts.positive, { status: 'resolved', text: legacy.positivePrompt, source: 'graph-inferred' });
    assert.deepEqual(record.prompts.negative, { status: 'resolved', text: legacy.negativePrompt, source: 'graph-inferred' });
    assert.equal(record.prompts.positive.text, 'a lighthouse at dusk, oil painting');
    assert.equal(record.prompts.negative.text, 'blurry, watermark');
    for (const [field, key] of [['seed', 'seed'], ['steps', 'steps'], ['cfg', 'cfgScale'], ['sampler', 'sampler'], ['model', 'checkpoint']]) {
        assert.equal(record.generation[field].status, 'known', field);
        assert.equal(record.generation[field].value, String(legacy[key]), field);
    }
});

test('recorded and saved prompts are never replaced by the workflow parse', t => {
    globalThis.ComfyMetadata = require('../../web/js/comfy-metadata.js');
    t.after(() => { delete globalThis.ComfyMetadata; });
    const entry = image(), data = envelope(entry);
    data.metadata.prompt_graph = { status: 'parsed', value: detailerPipelineGraph() };
    const record = contract('toInspectionRecord')(data, entry, extractGenerationFacts);
    assert.deepEqual(record.prompts.positive, { status: 'resolved', text: 'exact\npositive', source: 'execution' });
    assert.equal(record.prompts.negative.text, 'blurry, watermark');
});

test('a definite server prompt is kept even when the legacy parser reads it differently', t => {
    globalThis.ComfyMetadata = require('../../web/js/comfy-metadata.js');
    t.after(() => { delete globalThis.ComfyMetadata; });
    const entry = image(), data = envelope(entry);
    // The legacy parser follows ConditioningZeroOut back to the positive text node.
    const graph = detailerPipelineGraph();
    graph[30] = { class_type: 'ConditioningZeroOut', inputs: { conditioning: ['25', 0] } };
    graph[5].inputs.negative = ['30', 0];
    data.metadata.prompt_graph = { status: 'parsed', value: graph };
    data.prompts = { positive: { status: 'ambiguous', text: null, source: null }, negative: { status: 'resolved', text: '', source: 'zeroed-conditioning' } };
    const record = contract('toInspectionRecord')(data, entry, extractGenerationFacts);
    assert.deepEqual(record.prompts.negative, { status: 'resolved', text: '', source: 'zeroed-conditioning' });
    assert.equal(record.prompts.positive.text, 'a lighthouse at dusk, oil painting');
});

// #249: a LoRA compare grid. Every sampler's negative is a ConditioningZeroOut of the
// positive encode; the legacy parser follows the zero-out back to the positive text.
function zeroedGridGraph(samplers = 3) {
    const graph = {
        1: { class_type: 'UNETLoader', inputs: { unet_name: 'base.safetensors', weight_dtype: 'default' } },
        2: { class_type: 'CLIPLoader', inputs: { clip_name: 'te.safetensors', type: 'stable_diffusion' } },
        4: { class_type: 'CLIPTextEncode', inputs: { text: 'a woman holding a coffee cup', clip: ['2', 0] } },
        5: { class_type: 'ConditioningZeroOut', inputs: { conditioning: ['4', 0] } },
        6: { class_type: 'EmptyLatentImage', inputs: { width: 512, height: 512, batch_size: 1 } },
    };
    for (let index = 0; index < samplers; index++) {
        graph[`1${index}`] = { class_type: 'LoraLoaderModelOnly', inputs: { lora_name: `epoch_${index}.safetensors`, strength_model: 1, model: ['1', 0] } };
        graph[`2${index}`] = { class_type: 'KSampler', inputs: { seed: 42, steps: 8, cfg: 1, sampler_name: 'euler', scheduler: 'simple', denoise: 1,
            model: [`1${index}`, 0], positive: ['4', 0], negative: ['5', 0], latent_image: ['6', 0] } };
    }
    return graph;
}

function undecided(graph) {
    const entry = image(), data = envelope(entry);
    data.prompts = { positive: { status: 'ambiguous', text: null, source: null }, negative: { status: 'ambiguous', text: null, source: null } };
    data.metadata.prompt_graph = { status: 'parsed', value: graph };
    data.resolution = { state: 'ambiguous', sampler_id: null, reason: 'Multiple sampler branches' };
    return contract('toInspectionRecord')(data, entry, extractGenerationFacts);
}

test('a zeroed negative is never filled with the positive text from the legacy parse (#249)', t => {
    const parser = require('../../web/js/comfy-metadata.js');
    globalThis.ComfyMetadata = parser;
    t.after(() => { delete globalThis.ComfyMetadata; });
    // The legacy pages keep their reading: the zero-out passes the positive through.
    assert.equal(parser.extractGenerationParams({ prompt: zeroedGridGraph() }).negativePrompt, 'a woman holding a coffee cup');
    const record = undecided(zeroedGridGraph());
    assert.deepEqual(record.prompts.positive, { status: 'resolved', text: 'a woman holding a coffee cup', source: 'graph-inferred' });
    assert.deepEqual(record.prompts.negative, { status: 'resolved', text: '', source: 'zeroed-conditioning' });
    // Behind an API-graph Reroute the legacy parse reads no negative, so there is nothing
    // to correct: the server's answer stays.
    const rerouted = zeroedGridGraph(1);
    rerouted[7] = { class_type: 'Reroute', inputs: { value: ['5', 0] } };
    rerouted[20].inputs.negative = ['7', 0];
    assert.equal(parser.legacyNegativeSource({ prompt: rerouted }), null);
    assert.deepEqual(undecided(rerouted).prompts.negative, { status: 'ambiguous', text: null, source: null });
});

test('the zero-out check follows the sampler the legacy parse read (#249)', t => {
    const parser = require('../../web/js/comfy-metadata.js');
    globalThis.ComfyMetadata = parser;
    t.after(() => { delete globalThis.ComfyMetadata; });
    const zeroed = { status: 'resolved', text: '', source: 'zeroed-conditioning' };
    // The legacy parse reads the first sampler (20): its negative is the zero-out.
    const mixed = zeroedGridGraph(2);
    mixed[8] = { class_type: 'CLIPTextEncode', inputs: { text: 'blurry', clip: ['2', 0] } };
    mixed[21].inputs.negative = ['8', 0];
    assert.deepEqual(undecided(mixed).prompts.negative, zeroed);
    // Reversed: the sampler it reads encodes "blurry", which it showed before and still does.
    const reversed = zeroedGridGraph(2);
    reversed[8] = { class_type: 'CLIPTextEncode', inputs: { text: 'blurry', clip: ['2', 0] } };
    reversed[20].inputs.negative = ['8', 0];
    assert.equal(parser.extractGenerationParams({ prompt: reversed }).negativePrompt, 'blurry');
    assert.deepEqual(undecided(reversed).prompts.negative, { status: 'resolved', text: 'blurry', source: 'graph-inferred' });
    // The positive fills from the legacy parse in both cases.
    assert.equal(undecided(mixed).prompts.positive.text, 'a woman holding a coffee cup');
    assert.equal(undecided(reversed).prompts.positive.text, 'a woman holding a coffee cup');
});

test('a zero-out mixed into the read negative leaves it as the server left it (#249)', t => {
    globalThis.ComfyMetadata = require('../../web/js/comfy-metadata.js');
    t.after(() => { delete globalThis.ComfyMetadata; });
    // A zero-out combined with another conditioning is not an empty negative.
    const combined = zeroedGridGraph(1);
    combined[8] = { class_type: 'CLIPTextEncode', inputs: { text: 'blurry', clip: ['2', 0] } };
    combined[9] = { class_type: 'ConditioningCombine', inputs: { conditioning_1: ['5', 0], conditioning_2: ['8', 0] } };
    combined[20].inputs.negative = ['9', 0];
    assert.deepEqual(undecided(combined).prompts.negative, { status: 'ambiguous', text: null, source: null });
});

function workflowOnly(workflow) {
    const entry = image(), data = envelope(entry);
    data.prompts = { positive: { status: 'unresolved', text: null, source: null }, negative: { status: 'unresolved', text: null, source: null } };
    Object.assign(data.metadata, { raw: { workflow: JSON.stringify(workflow) }, record_formats: { workflow: 'reserialized-json' },
        prompt_graph: { status: 'absent', value: null }, workflow_graph: { status: 'parsed', value: workflow } });
    return contract('toInspectionRecord')(data, entry, extractGenerationFacts);
}

// A UI workflow: encode 4 -> zero-out 5 -> sampler 3's negative, encode 4 -> positive.
function zeroedWorkflow({ zeroMode = 0, extraNodes = [], extraLinks = [], negativeLink = 3 } = {}) {
    return { nodes: [
        { id: 4, type: 'CLIPTextEncode', inputs: [], outputs: [{ links: [1, 2] }], widgets_values: ['a woman holding a coffee cup'] },
        { id: 5, type: 'ConditioningZeroOut', mode: zeroMode, inputs: [{ name: 'conditioning', type: 'CONDITIONING', link: 1 }], outputs: [{ links: [3] }] },
        { id: 1, type: 'UNETLoader', inputs: [], outputs: [{ links: [4] }], widgets_values: ['base.safetensors', 'default'] },
        { id: 3, type: 'KSampler', inputs: [{ name: 'model', link: 4 }, { name: 'positive', link: 2 }, { name: 'negative', link: negativeLink }], widgets_values: [42, 'fixed', 8, 1, 'euler', 'simple', 1] },
        ...extraNodes,
    ], links: [[1, 4, 0, 5, 0, 'CONDITIONING'], [2, 4, 0, 3, 1, 'CONDITIONING'], [3, 5, 0, 3, 2, 'CONDITIONING'], [4, 1, 0, 3, 0, 'MODEL'], ...extraLinks] };
}

test('workflow-only files follow the legacy reading of their zero-out (#249)', t => {
    const parser = require('../../web/js/comfy-metadata.js');
    globalThis.ComfyMetadata = parser;
    t.after(() => { delete globalThis.ComfyMetadata; });
    // A connected zero-out on the read sampler's negative is the zeroed empty negative.
    const direct = zeroedWorkflow();
    assert.equal(parser.extractGenerationParams({ workflow: direct }).negativePrompt, 'a woman holding a coffee cup', 'the legacy parse would fill it');
    assert.deepEqual(workflowOnly(direct).prompts.negative, { status: 'resolved', text: '', source: 'zeroed-conditioning' });
    // A bypassed zero-out (mode 4) passes its conditioning through: the legacy reading is right.
    const bypassed = workflowOnly(zeroedWorkflow({ zeroMode: 4 }));
    assert.deepEqual(bypassed.prompts.negative, { status: 'resolved', text: 'a woman holding a coffee cup', source: 'graph-inferred' });
    // An unconnected zero-out beside a text negative changes nothing.
    const unconnected = workflowOnly(zeroedWorkflow({ negativeLink: 6,
        extraNodes: [{ id: 6, type: 'CLIPTextEncode', inputs: [], outputs: [{ links: [6] }], widgets_values: ['blurry'] }],
        extraLinks: [[6, 6, 0, 3, 2, 'CONDITIONING']] }));
    assert.deepEqual(unconnected.prompts.negative, { status: 'resolved', text: 'blurry', source: 'graph-inferred' });
});

test('a negative read through a subgraph holding a zero-out is not filled (#249)', t => {
    const parser = require('../../web/js/comfy-metadata.js');
    globalThis.ComfyMetadata = parser;
    t.after(() => { delete globalThis.ComfyMetadata; });
    // The sampler's negative comes from a subgraph instance whose definition zeroes it.
    const definition = 'aaaaaaaa-0000-4000-8000-000000000001';
    const workflow = zeroedWorkflow({ negativeLink: 7,
        extraNodes: [{ id: 9, type: definition, inputs: [{ name: 'conditioning', type: 'CONDITIONING', link: 8 }], outputs: [{ links: [7] }] }],
        extraLinks: [[7, 9, 0, 3, 2, 'CONDITIONING'], [8, 4, 0, 9, 0, 'CONDITIONING']] });
    workflow.definitions = { subgraphs: [{ id: definition, nodes: [{ id: 1, type: 'ConditioningZeroOut', inputs: [], outputs: [] }], links: [] }] };
    assert.equal(parser.extractGenerationParams({ workflow }).negativePrompt, 'a woman holding a coffee cup', 'the legacy parse would fill it');
    assert.deepEqual(workflowOnly(workflow).prompts.negative, { status: 'unresolved', text: null, source: null });
    // The same instance of a definition without a zero-out keeps the legacy fill.
    workflow.definitions.subgraphs[0].nodes[0].type = 'Reroute';
    assert.equal(workflowOnly(workflow).prompts.negative.text, 'a woman holding a coffee cup');
});

test('callers without the browser global pass the legacy parser explicitly', () => {
    const parser = require('../../web/js/comfy-metadata.js');
    const entry = image(), data = envelope(entry);
    data.prompts = { positive: { status: 'ambiguous', text: null, source: null }, negative: { status: 'ambiguous', text: null, source: null } };
    data.metadata.prompt_graph = { status: 'parsed', value: detailerPipelineGraph() };
    const identity = { kind: 'rooted-file', key: '0:x', root_index: 0, path: entry.path, root_scope: scopeA };
    const record = contract('projectInspectionPayload')(data, identity, null, extractGenerationFacts, false, parser);
    assert.equal(record.prompts.positive.text, 'a lighthouse at dusk, oil painting');
    assert.equal(record.prompts.negative.text, 'blurry, watermark');
});

test('a setting the file does not record is never filled from the legacy parse', t => {
    globalThis.ComfyMetadata = require('../../web/js/comfy-metadata.js');
    t.after(() => { delete globalThis.ComfyMetadata; });
    const entry = image(), data = envelope(entry);
    data.metadata.prompt_graph = { status: 'parsed', value: detailerPipelineGraph() };
    const extract = input => ({ ...extractGenerationFacts(input), cfg: { status: 'not-recorded', value: null, source: null, evidence: null, reason: null, sampler_id: null } });
    const record = contract('toInspectionRecord')(data, entry, extract);
    assert.equal(record.generation.cfg.status, 'not-recorded');
    assert.equal(record.generation.cfg.value, null);
});

test('a workflow the legacy parser throws on keeps the server result instead of failing the record', t => {
    globalThis.ComfyMetadata = require('../../web/js/comfy-metadata.js');
    t.after(() => { delete globalThis.ComfyMetadata; });
    const entry = image(), data = envelope(entry);
    // The browser parser throws on a null workflow node (it reads properties of it).
    data.metadata.workflow_graph = { status: 'parsed', value: { nodes: [null], links: [] } };
    data.metadata.raw.workflow = '{"nodes":[null],"links":[]}';
    data.metadata.record_formats.workflow = 'reserialized-json';
    const record = contract('toInspectionRecord')(data, entry, extractGenerationFacts);
    assert.deepEqual(record.prompts.positive, { status: 'resolved', text: 'exact\npositive', source: 'execution' });
    assert.equal(record.prompts.negative.status, 'ambiguous');
});

// PR L2 Task 4(a): an adapter that reads by itself (the local file adapter) never reaches the API.
test('controller prefers adapter.read and never touches api', async () => {
    const createController = contract('createInspectionController');
    const forbidden = new Proxy({}, { get(_, key) { assert.fail(`api.${String(key)} touched`); } });
    const reads = [], records = [];
    const adapter = {
        validate(item) { if (!item?.key) throw new TypeError('bad'); }, key: item => item.key, scope: () => 'own', inspectionPath: item => `own:${item.key}`,
        read(item, options) { const pending = {}; reads.push({ item, options, pending }); return new Promise((resolve, reject) => Object.assign(pending, { resolve, reject })); },
        toRecord(result, item) { records.push([result, item.key]); return { identity: { kind: 'own', key: item.key }, prompts: result.prompts }; },
    };
    for (const api of [forbidden, null]) {
        reads.length = 0; records.length = 0;
        const controller = createController({ api, adapter, extractFacts: extractGenerationFacts });
        const first = controller.select({ key: 'a' });
        assert.equal(reads.length, 1);
        assert.equal(reads[0].item.key, 'a');
        assert.ok(reads[0].options.signal instanceof AbortSignal);
        assert.equal(controller.state.loading, true);
        const second = controller.select({ key: 'b' });
        assert.equal(reads[0].options.signal.aborted, true, 'a new selection aborts the previous read');
        reads[0].pending.resolve({ prompts: { positive: { status: 'resolved', text: 'late' } } });
        await first;
        assert.equal(controller.state.record, null, 'a superseded read never publishes');
        reads[1].pending.resolve({ prompts: { positive: { status: 'resolved', text: 'current' } } });
        await second;
        assert.deepEqual(records.map(([, key]) => key), ['b']);
        assert.equal(controller.state.record.prompts.positive.text, 'current');
        const retry = controller.retry();
        reads[2].pending.reject(new Error('read failed'));
        await retry;
        assert.equal(controller.state.error, true);
        assert.equal(controller.state.message, 'Could not read details from this file.');
        controller.dispose();
    }
});

test('rooted and history adapters are unchanged', async () => {
    const createController = contract('createInspectionController');
    const { reportedLocationAdapter } = await import('../../web/js/workspace/history-media.mjs');
    for (const adapter of [module.rootedFileAdapter, reportedLocationAdapter]) {
        assert.equal(adapter.read, undefined, 'server adapters read through the API');
        assert.equal(adapter.mediaUrl, undefined, 'server adapters keep the cache-busted original');
    }
    const { api, requests } = pendingApi();
    const rooted = createController({ api, extractFacts: extractGenerationFacts });
    rooted.select(image());
    assert.deepEqual(requests.map(request => request.url), [`/prompt_manager/images/inspect/folder/a%20%23%25.png?root=0&root_scope=${scopeA}`]);
    rooted.dispose();
    const run = '22222222-2222-2222-2222-222222222222', epoch = '11111111-1111-1111-1111-111111111111';
    const query = new URLSearchParams({ node: '1', ordinal: '1', epoch, current_scope: scopeA }), base = `/prompt_manager/history/runs/${run}/output/current`;
    const target = { kind: 'reported-location', location_key: 'c'.repeat(64), history_epoch: epoch, current_scope: scopeA, occurrence: { run_id: run, output_node_id: '1', image_ordinal: 1 },
        reference: { folder_type: 'output', subfolder: '', filename: 'a.png', path_style: 'posix', root_key: scopeB }, filename: 'a.png', is_video: false,
        url: `${base}/media?${query}`, thumbnail_url: `${base}/thumbnail?${query}`, inspection_url: `${base}/inspection?${query}` };
    const history = pendingApi(), recorded = createController({ api: history.api, adapter: reportedLocationAdapter, extractFacts: extractGenerationFacts });
    recorded.select(target);
    assert.deepEqual(history.requests.map(request => request.url), [target.inspection_url]);
    recorded.dispose();
});

test('fileFor rejects selected-local-file for rooted identity', () => {
    const entry = image(), data = envelope(entry);
    data.file = { ...data.file, observation: 'selected-local-file', source_label: 'Selected local file' };
    assert.throws(() => contract('toInspectionRecord')(data, entry, extractGenerationFacts), /Invalid inspection file/);
    const project = contract('projectInspectionPayload');
    assert.throws(() => project(data, { kind: 'rooted-file', key: 'k', path: 'a.png' }, null, extractGenerationFacts), /Invalid inspection file/);
    assert.throws(() => project(envelope(entry), { kind: 'local-file', key: 'local:1', path: 'a.png' }, null, extractGenerationFacts), /Invalid inspection file/);
    assert.equal(project(data, { kind: 'local-file', key: 'local:1', path: 'a.png' }, null, extractGenerationFacts).file.observation, 'selected-local-file');
});

// #249 PR C Task C3: the B3 envelope's `image_tags` facet, the file's own current tags (S1).
test('image_tags facet is kept exactly and absent stays null', () => {
    const entry = image(), project = contract('toInspectionRecord');
    assert.equal(project(envelope(entry), entry, extractGenerationFacts).imageTags, null);
    for (const facet of [{ state: 'current', tags: ['dunes', 'warm_light'] }, { state: 'current', tags: [] }, { state: 'none', tags: [] }, { state: 'unavailable', tags: [] }]) {
        const data = { ...envelope(entry), image_tags: facet };
        const record = project(data, entry, extractGenerationFacts);
        assert.deepEqual(record.imageTags, facet);
        assert.notEqual(record.imageTags.tags, facet.tags);
    }
    const hundred = Array.from({ length: 100 }, (_, index) => `tag_${index}`);
    assert.deepEqual(project({ ...envelope(entry), image_tags: { state: 'current', tags: hundred } }, entry, extractGenerationFacts).imageTags.tags, hundred);
});

test('rejects a malformed image_tags facet', () => {
    const entry = image();
    for (const facet of [null, [], 'current', { state: 'current' }, { tags: [] }, { state: 'current', tags: ['a'], extra: 1 },
        { state: 'stale', tags: [] }, { state: 'none', tags: ['a'] }, { state: 'unavailable', tags: ['a'] }, { state: 'current', tags: 'a' },
        { state: 'current', tags: [1] }, { state: 'current', tags: [''] }, { state: 'current', tags: [' a'] }, { state: 'current', tags: ['a,b'] },
        { state: 'current', tags: ['a\nb'] }, { state: 'current', tags: ['x'.repeat(51)] },
        { state: 'current', tags: Array.from({ length: 101 }, (_, index) => `tag_${index}`) }]) {
        assert.throws(() => module.toInspectionRecord({ ...envelope(entry), image_tags: facet }, entry, extractGenerationFacts), TypeError, JSON.stringify(facet));
    }
    assert.equal(module.toInspectionRecord({ ...envelope(entry), image_tags: { state: 'current', tags: ['é'.repeat(50)] } }, entry, extractGenerationFacts).imageTags.tags[0].length, 50);
});

test('refresh re-reads the selected file and keeps the shown record until the new read lands', async () => {
    const { api, requests } = pendingApi(), updates = [];
    const controller = contract('createInspectionController')({ api, extractFacts: extractGenerationFacts });
    assert.equal(typeof controller.refresh, 'function', 'refresh is part of the controller');
    controller.subscribe(state => updates.push(state));
    const load = controller.select(image()); requests[0].resolve(envelope()); await load;
    const shown = controller.state, count = updates.length;
    const refresh = controller.refresh();
    assert.equal(requests.length, 2);
    // No loading state is published: the panel keeps its content.
    assert.equal(controller.state, shown); assert.equal(updates.length, count);
    requests[1].resolve({ ...envelope(), image_tags: { state: 'current', tags: ['fresh'] } }); await refresh;
    assert.deepEqual(controller.state.record.imageTags, { state: 'current', tags: ['fresh'] });
    assert.equal(controller.state.loading, false); assert.equal(updates.length, count + 1);
    // A failed quiet re-read reports the failure as a read would.
    const failing = controller.refresh(); requests[2].reject(new Error('offline')); await failing;
    assert.equal(controller.state.error, true);
    // Without a shown record a refresh is an ordinary retry (loading published).
    const again = controller.refresh(); assert.equal(controller.state.loading, true);
    requests[3].resolve(envelope()); await again; assert.equal(controller.state.error, false);
    // A newer selection supersedes a refresh in flight.
    const stale = controller.refresh(); const other = controller.select({ ...image(), path: 'folder/b.png', filename: 'b.png', url: image().url.replace('a%20%23%25', 'b') });
    requests[5].resolve(envelope({ ...image(), path: 'folder/b.png', filename: 'b.png', url: image().url.replace('a%20%23%25', 'b') })); await other;
    requests[4].resolve(envelope()); await stale;
    assert.equal(controller.state.record.identity.path, 'folder/b.png');
    controller.dispose(); assert.equal(await controller.refresh(), undefined);
});

test('metadata off shows an explicit load and loads once on request', async () => {
    const { api, requests } = pendingApi(), updates = [];
    const controller = contract('createInspectionController')({ api, extractFacts: extractGenerationFacts, autoLoad: false });
    controller.subscribe(state => updates.push(state));
    // Selecting reads nothing: the state says the metadata waits for a request.
    assert.equal(controller.select(image()), undefined);
    assert.equal(requests.length, 0);
    assert.equal(controller.state.deferred, true);
    assert.equal(controller.state.loading, false);
    assert.equal(controller.state.record, null);
    // Selecting the same file again still reads nothing.
    controller.select({ ...image() });
    assert.equal(requests.length, 0);
    const load = controller.load();
    assert.equal(requests.length, 1);
    assert.equal(controller.state.loading, true);
    assert.equal(controller.state.deferred, false);
    // A second press while it reads, and the same file selected again once it loaded, read nothing more.
    controller.load();
    assert.equal(requests.length, 1);
    requests[0].resolve(envelope()); await load;
    assert.equal(controller.state.record.generation.seed.value, '9007199254740993');
    controller.load(); controller.select(image());
    assert.equal(requests.length, 1);
    // Another file waits for its own request again.
    controller.select({ ...image(), path: 'folder/b.png', filename: 'b.png', url: image().url.replace('a%20%23%25', 'b') });
    assert.equal(controller.state.deferred, true);
    assert.equal(requests.length, 1);
    // The default controller loads on selection, as before.
    const eager = contract('createInspectionController')({ api, extractFacts: extractGenerationFacts });
    eager.select(image());
    assert.equal(requests.length, 2);
    assert.equal(eager.state.deferred, false);
    eager.dispose(); controller.dispose();
    assert.equal(controller.load(), undefined);
});

test('cache off rereads the current selection; on reuses the one current record', async () => {
    for (const cache of [true, false]) {
        const { api, requests } = pendingApi();
        const controller = contract('createInspectionController')({ api, extractFacts: extractGenerationFacts, cache });
        const first = controller.select(image());
        // A read in flight is shared either way.
        controller.select(image());
        assert.equal(requests.length, 1);
        requests[0].resolve(envelope()); await first;
        const again = controller.select(image());
        assert.equal(requests.length, cache ? 1 : 2, `cache ${cache}`);
        if (!cache) { requests[1].resolve(envelope()); await again; assert.equal(controller.state.record.identity.path, 'folder/a #%.png'); }
        controller.dispose();
    }
});

test('cache off never rereads a selection whose source changed, and refresh never loads a deferred one', async () => {
    const { api, requests } = pendingApi();
    const controller = contract('createInspectionController')({ api, extractFacts: extractGenerationFacts, cache: false });
    const first = controller.select(image());
    requests[0].reject(new ApiError(409, refreshMessage, 'source-changed')); await first;
    assert.equal(controller.state.refreshRequired, true);
    controller.select(image());
    assert.equal(requests.length, 1);
    controller.dispose();
    const deferred = contract('createInspectionController')({ api, extractFacts: extractGenerationFacts, autoLoad: false });
    deferred.select(image());
    assert.equal(deferred.refresh(), undefined);
    assert.equal(requests.length, 1);
    assert.equal(deferred.state.deferred, true);
    deferred.dispose();
});

test('record carries the listing thumbnail as its preview, never another file (#249)', () => {
    const toRecord = contract('toInspectionRecord'), entry = image();
    const thumb = `/prompt_manager/images/serve/thumbnails/folder/a%20%23%25_thumb.png?root=0&root_scope=${scopeA}`;
    assert.deepEqual(toRecord(envelope(entry), { ...entry, thumbnail_url: thumb }, extractGenerationFacts).preview, { url: thumb });
    const versioned = `/prompt_manager/images/serve/thumbnails/folder/a%20%23%25_thumb.png?v=12&root=0&root_scope=${scopeA}`;
    assert.deepEqual(toRecord(envelope(entry), { ...entry, thumbnail_url: versioned }, extractGenerationFacts).preview, { url: versioned });
    // No thumbnail, or one naming another file, root or origin: no preview (the cover keeps the original).
    for (const thumbnail_url of [undefined, null, '', thumb.replace('a%20%23%25_thumb', 'b_thumb'), thumb.replace('root=0', 'root=1'),
        thumb.replace(scopeA, scopeB), `https://other.test${thumb}`, thumb.replace('thumbnails/', ''), `${thumb}#x`]) {
        assert.equal(toRecord(envelope(entry), { ...entry, thumbnail_url }, extractGenerationFacts).preview, null, String(thumbnail_url));
    }
    const video = { root_index: 0, path: 'clip.MP4', filename: 'clip.MP4', url: `/prompt_manager/images/serve/clip.MP4?root=0&root_scope=${scopeA}`, root_scope: scopeA,
        thumbnail_url: `/prompt_manager/images/serve/thumbnails/clip_thumb.jpg?root=0&root_scope=${scopeA}` };
    const data = envelope(video); data.file = { ...data.file, media_type: 'video', extension: '.mp4', format: 'MP4' };
    assert.deepEqual(toRecord(data, video, extractGenerationFacts).preview, { url: video.thumbnail_url });
});

test('the preview may be any thumbnail format the file owns (#317)', () => {
    const toRecord = contract('toInspectionRecord'), entry = image();
    const base = '/prompt_manager/images/serve/thumbnails/folder/a%20%23%25_thumb';
    for (const extension of ['.webp', '.jxl', '.jpg', '.png']) {
        const thumbnail_url = `${base}${extension}?v=3&root=0&root_scope=${scopeA}`;
        assert.deepEqual(toRecord(envelope(entry), { ...entry, thumbnail_url }, extractGenerationFacts).preview, { url: thumbnail_url }, extension);
    }
    // Not a thumbnail format, upper case, no extension, or a legacy name another file owns: no preview.
    for (const extension of ['.txt', '.WEBP', '', '.gif', '.jpeg', '.webp.png']) {
        const thumbnail_url = `${base}${extension}?root=0&root_scope=${scopeA}`;
        assert.equal(toRecord(envelope(entry), { ...entry, thumbnail_url }, extractGenerationFacts).preview, null, extension);
    }
    const video = { root_index: 0, path: 'clip.MP4', filename: 'clip.MP4', url: `/prompt_manager/images/serve/clip.MP4?root=0&root_scope=${scopeA}`, root_scope: scopeA };
    const data = envelope(video); data.file = { ...data.file, media_type: 'video', extension: '.mp4', format: 'MP4' };
    for (const [extension, owned] of [['.webp', true], ['.jxl', true], ['.jpg', true], ['.png', false]]) {
        const thumbnail_url = `/prompt_manager/images/serve/thumbnails/clip_thumb${extension}?root=0&root_scope=${scopeA}`;
        assert.deepEqual(toRecord(data, { ...video, thumbnail_url }, extractGenerationFacts).preview, owned ? { url: thumbnail_url } : null, extension);
    }
});

test("names the server quotes (parentheses, '!*') still match their original and preview (#249 M11)", () => {
    const toRecord = contract('toInspectionRecord');
    const entry = { root_index: 0, path: "folder/a (1)!'*.png", filename: "a (1)!'*.png", root_scope: scopeA,
        url: `/prompt_manager/images/serve/folder/a%20%281%29%21%27%2A.png?root=0&root_scope=${scopeA}`,
        thumbnail_url: `/prompt_manager/images/serve/thumbnails/folder/a%20%281%29%21%27%2A_thumb.png?root=0&root_scope=${scopeA}` };
    const record = toRecord(envelope(entry), entry, extractGenerationFacts);
    assert.equal(record.download.url, entry.url);
    assert.deepEqual(record.preview, { url: entry.thumbnail_url });
});
