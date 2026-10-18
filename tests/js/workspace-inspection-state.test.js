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
        library: { status: 'linked', scope: 'saved-library', positive_id: 7, pair_id: 8, title: 'Recipe', category: 'Subject', tags: ['cat'], metadata_scope: 'pair' },
    };
}
function pendingApi() {
    const requests = [];
    const api = { get: (url, { signal }) => new Promise((resolve, reject) => requests.push({ url, signal, resolve, reject })) };
    return { api, requests };
}

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
        data => { data.metadata.record_formats.workflow = 'invalid'; data.metadata.workflow_graph = { status: 'limited', value: null }; },
    ]) {
        const data = envelope(); mutate(data); let extractions = 0;
        assert.throws(() => toRecord(data, image(), () => { extractions++; return extractGenerationFacts({}); }), TypeError);
        assert.equal(extractions, 0, 'contradictory transport cannot produce known facts');
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
