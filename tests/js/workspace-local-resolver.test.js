// PR L3 Task 8: the server's inspection resolver in this browser. The values below are what CPython answers
// (json.loads, len, str.strip, repr, utils/prompt_inspector.inspect_prompt_evidence); the full comparison with the
// server runs in tests/test_local_inspect_parity.py through tests/js/support/local-inspect-oracle.mjs.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const py = await import('../../web/js/workspace/py-json.mjs');
const resolver = await import('../../web/js/workspace/local-resolver.mjs');
const semantics = resolver.compileSemantics(JSON.parse(fs.readFileSync(new URL('../../web/js/inspection-semantics.json', import.meta.url), 'utf8')));

test('json.loads keeps Python value kinds and dict order', () => {
    const value = py.pyJsonLoads('{"9": 1, "10": 1.0, "a": NaN, "9": [true, -0, 9007199254740993, -Infinity]}');
    assert.ok(value instanceof Map);
    // A repeated key keeps its first position and takes its last value; integer-like keys keep text order.
    assert.deepEqual([...value.keys()], ['9', '10', 'a']);
    const [flag, zero, big, negative] = value.get('9');
    assert.equal(flag, true); assert.equal(zero, 0); assert.equal(big, 9007199254740993n);
    assert.ok(negative instanceof py.PyFloat); assert.equal(negative.value, -Infinity);
    assert.ok(value.get('10') instanceof py.PyFloat, '1.0 is a float, not an int');
    assert.ok(Number.isNaN(value.get('a').value));
    assert.equal(py.isInt(value.get('10')), false);
    assert.equal(py.pyJsonLoads('"\\ud83d\\ude00\\ud800"'), '\u{1f600}\ud800');
    for (const bad of ['﻿{}', '{"a":1,}', '[01]', '{"a" 1}', '"\u0001"', '"\\x"', '1 2', '-', `${'9'.repeat(4301)}`, '"\\ud800\\uzzzz"']) {
        assert.throws(() => py.pyJsonLoads(bad), py.PyValueError, bad);
    }
    assert.equal(py.pyJsonLoads(` ${'9'.repeat(4300)}\n`), BigInt('9'.repeat(4300)));
});

test('str helpers measure, slice and strip as CPython', () => {
    assert.equal(py.pyLen('a\u{1f600}\ud800'), 3);
    assert.equal(py.pySlice('\u{1f600}\u{1f600}x', 2), '\u{1f600}\u{1f600}');
    // str.isspace() includes \x1c-\x1f and \x85 and excludes ﻿, unlike String.prototype.trim.
    assert.equal(py.pyStrip('\x1c\x85 a b　\x1f'), 'a b');
    assert.equal(py.pyStrip('﻿a﻿'), '﻿a﻿');
    assert.deepEqual([1.0, 0.0001, 1e-5, 1e16, 123456789012345.6, -2.5, 1e22].map(py.pyFloatRepr),
        ['1.0', '0.0001', '1e-05', '1e+16', '123456789012345.6', '-2.5', '1e+22']);
    assert.deepEqual([null, true, 0, 1n, new py.PyFloat(2), '', [], new Map()].map(py.pyTruthy), [false, true, false, true, true, false, false, false]);
    assert.deepEqual([1, 1n, true, new py.PyFloat(1), '1', 2].map(py.pyEqualsOne), [true, true, true, true, false, false]);
});

const encode = text => ({class_type: 'CLIPTextEncode', inputs: {text, clip: ['4', 1]}});
const sampler = (extra = {}) => ({class_type: 'KSampler', inputs: {seed: 1, positive: ['6', 0], negative: ['7', 0], latent_image: ['5', 0], model: ['4', 0], ...extra}});
const base = (nodes = {}) => ({'3': sampler(), '4': {class_type: 'CheckpointLoaderSimple', inputs: {ckpt_name: 'm.safetensors'}},
    '5': {class_type: 'EmptyLatentImage', inputs: {width: 64, height: 64, batch_size: 1}}, '6': encode('a fox'), '7': encode('blurry'), ...nodes});

test('a single chain resolves both roles with their sources', () => {
    const evidence = resolver.resolveLocalEvidence({prompt: JSON.stringify(base())}, semantics);
    assert.deepEqual(evidence, {
        prompts: {positive: {status: 'resolved', text: 'a fox', source: 'graph-inferred'}, negative: {status: 'resolved', text: 'blurry', source: 'graph-inferred'}},
        resolution: {state: 'resolved', sampler_id: '3', reason: '', reason_code: null, node: null},
    });
});

test('records without roles, malformed text and a missing sampler answer as the server', () => {
    assert.deepEqual(resolver.resolveLocalEvidence({workflow: '{}'}, semantics).resolution,
        {state: 'absent', sampler_id: null, reason: 'No parsed prompt metadata', reason_code: null, node: null});
    const malformed = resolver.resolveLocalEvidence({prompt: '{broken'}, semantics);
    assert.equal(malformed.resolution.reason, 'Prompt evidence is malformed');
    assert.equal(malformed.prompts.positive.message, 'Prompt metadata in this file is malformed.');
    const none = resolver.resolveLocalEvidence({prompt: JSON.stringify({'1': encode('x'), '2': {class_type: 'MySampler', inputs: {positive: ['1', 0]}}})}, semantics);
    assert.deepEqual(none.resolution, {state: 'unresolved', sampler_id: null, reason: 'No supported sampler pair found (saw MySampler)', reason_code: 'no-sampler', node: null});
    assert.equal(none.prompts.negative.message, 'No supported sampler was found.');
    assert.equal(resolver.resolveLocalEvidence({prompt: JSON.stringify(base()), parameters: 'x'}, null).resolution.reason, 'Inspection node semantics are unavailable');
});

test('a float output slot is not a link, and a runtime node is named', () => {
    const floatSlot = resolver.resolveLocalEvidence({prompt: JSON.stringify(base()).replace('"positive":["6",0]', '"positive":["6",0.0]')}, semantics);
    assert.deepEqual(floatSlot.resolution, {state: 'unresolved', sampler_id: '3', reason: 'Expected a typed graph link or text input', reason_code: 'malformed', node: null});
    const runtime = resolver.resolveLocalEvidence({prompt: JSON.stringify(base({'6': {class_type: 'PromptSearchList', inputs: {}}}))}, semantics);
    assert.deepEqual(runtime.resolution.node, {id: '6', class_type: 'PromptSearchList'});
    assert.equal(runtime.prompts.positive.message, 'Prompt is chosen at run time (PromptSearchList).');
});

test('execution evidence replaces a PromptManagerPair text when it matches the node inputs', () => {
    const pair = {class_type: 'PromptManagerPair', inputs: {text: 'fox', negative_text: 'blur', prepend_text: '', append_text: ''}};
    const record = {text: 'fox', negative_text: 'blur', positive_text: 'fox, executed', negative_effective_text: 'blur', prepend_text: '', append_text: ''};
    const graph = base({'3': sampler({positive: ['8', 0], negative: ['8', 1]}), '8': pair});
    const evidence = resolver.resolveLocalEvidence({prompt: JSON.stringify(graph), comfyassets_prompt_pairs: JSON.stringify({version: 1, nodes: {'8': {records: [record]}}})}, semantics);
    assert.deepEqual(evidence.prompts.positive, {status: 'resolved', text: 'fox, executed', source: 'execution'});
    const stale = resolver.resolveLocalEvidence({prompt: JSON.stringify(graph), comfyassets_prompt_pairs: JSON.stringify({version: 1, nodes: {'8': {records: [{...record, text: 'other'}]}}})}, semantics);
    assert.equal(stale.resolution.reason, 'Execution evidence does not match recorded node inputs');
});

test('several samplers and A1111 parameters are left to Task 9', () => {
    assert.throws(() => resolver.resolveLocalEvidence({prompt: JSON.stringify(base({'9': sampler()}))}, semantics), resolver.LocalResolverGap);
    assert.throws(() => resolver.resolveLocalEvidence({parameters: 'a fox\nSteps: 20, Sampler: euler'}, semantics), resolver.LocalResolverGap);
});

test('an unusable semantics table is refused', () => {
    for (const doc of [null, {schema: 2}, {schema: 1, samplers: []}]) assert.throws(() => resolver.compileSemantics(doc), resolver.SemanticsError);
});

// The committed server answers (tests/fixtures/local-inspect/resolver-vectors.json, written by
// `python3 -m tests.local_resolver_cases --write` and checked current by the Python suite).
test('committed server answers equal the local evidence', () => {
    const {vectors} = JSON.parse(fs.readFileSync(new URL('../fixtures/local-inspect/resolver-vectors.json', import.meta.url), 'utf8'));
    const canonical = value => Array.isArray(value) ? value.map(canonical) :
        value !== null && typeof value === 'object' ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
    let compared = 0;
    for (const {name, records, gap, evidence} of vectors) {
        if (gap) {
            assert.throws(() => resolver.resolveLocalEvidence(records, semantics), resolver.LocalResolverGap, name);
            continue;
        }
        assert.deepEqual(canonical(resolver.resolveLocalEvidence(records, semantics)), canonical(evidence), name);
        compared++;
    }
    assert.ok(compared > 140, `compared ${compared}`);
});
