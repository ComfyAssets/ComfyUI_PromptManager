// Tables, evidence reader, bounds and outcome lines for local-resolver.mjs (#249 local Inspect, PR L3 Tasks 8-9):
// the constants of utils/prompt_pair_resolver.py and utils/inspection_outcomes.py, utils/pair_execution.py
// `read_pair_execution` and the semantics table check of utils/inspection_semantics.py. No DOM.
import { PyFloat, PyValueError, isDict, isList, isInt, pyJsonLoads, pyLen, pySlice, pyStrip, pyTruthy, pyEqualsOne, pyStr, pyGet } from './py-json.mjs';

/** The semantics table is unusable: the server's `SemanticsError`. */
export class SemanticsError extends Error {
    constructor(message) {
        super(message);
        this.name = 'SemanticsError';
    }
}

// INSPECTION_PROFILE (prompt_pair_resolver.py 152-166); its drop_preview_only_stages is always on here.
export const PROFILE = Object.freeze({
    perCandidateVisits: 2000, totalVisits: 20000, maxDepth: 64, maxTextLength: 100000, nodeCeiling: 20000,
    maxPathEntries: 64 * 20000, maxBuiltChars: 16 * 100000, maxScannedUnits: 8000000,
});
export const EXECUTION_KEY = 'comfyassets_prompt_pairs';
export const ROLE_METADATA_KEYS = new Set(['prompt', 'parameters', EXECUTION_KEY]);
// pair_execution.py
export const MAX_EXECUTION_RECORDS = 16, MAX_EXECUTION_NODES = 64, MAX_EXECUTION_TEXT = 100000, MAX_EXECUTION_JSON = 500000;
export const EXECUTION_FIELDS = ['text', 'negative_text', 'positive_text', 'negative_effective_text', 'prepend_text', 'append_text'];

export const CANDIDATE_STRATEGIES = new Set(['ksampler', 'custom-advanced', 'detailer-stage']);
export const DETAILER = 'detailer-stage';
export const GUIDER_ABSENT_REASON = 'Guider has no negative input';
export const ZEROED_SOURCE = 'zeroed-conditioning';
export const MAX_BRANCH_CANDIDATES = 32;
export const RUNTIME_REASON = 'Text is decided at run time';
export const EMPTY_POSITIVE_REASON = 'Positive prompt is empty';
export const IDENTICAL_BRANCHES_REASON = 'Every sampler branch uses the same prompt pair';
export const MULTISTAGE_UNATTRIBUTED_REASON = 'Multistage sampler ancestry is ambiguous';
export const ADDED_CONDITIONING_REASON = 'A later stage adds conditioning to the base prompt';
export const RUNTIME_PARTS_SOURCE = 'runtime-parts-omitted';
export const RUNTIME_PARTS_REASON = 'Text added at run time, such as trigger words, is not included';
export const ADDED_CONDITIONING_SOURCE = 'added-conditioning';
export const RUNTIME_AND_ADDED_SOURCE = 'runtime-parts-omitted-and-added-conditioning';
export const MALFORMED_EVIDENCE_REASON = 'Prompt evidence is malformed';
export const UNREADABLE_METADATA_REASON = 'Media metadata could not be read';
export const SEMANTICS_UNAVAILABLE_REASON = 'Inspection node semantics are unavailable';
export const INCOMPATIBLE_SLOT = 'Output slot has an incompatible type';
export const OVERWRITTEN_PROMPT_REASON = 'Saved PromptManager text was overwritten by another node';
export const UNVERIFIABLE_CODES = new Set(['unsupported-node', 'runtime-value']);
export const MAX_JOIN_INPUTS = 1000;
export const SAMPLER_LIKE_INPUTS = ['latent_image', 'guider', 'positive', 'negative'];
export const NO_SAMPLER_NAMES = 3;
export const CLASS_NAME_LENGTH = 64;
export const MAX_SUBGRAPH_DEPTH = 8, MAX_WORKFLOW_NODES = 100000;

// inspection_outcomes.py
export const OUTCOME_NAME_LENGTH = 200;
export const LIMIT_CODES = ['work-limit', 'depth-limit', 'text-limit', 'graph-ceiling'];
export const NAMED_LINES = {
    'unsupported-node': 'Prompt uses a node the inspector does not support',
    'runtime-value': 'Prompt is chosen at run time',
    composite: 'Prompt combines several texts',
};
export const CODED_LINES = {
    ...Object.fromEntries(LIMIT_CODES.map(code => [code, 'Prompt exceeds inspection limits.'])),
    'no-sampler': 'No supported sampler was found.',
    malformed: 'Prompt metadata in this file is malformed.',
    'multistage-differs': 'Sampler stages have different prompts.',
};
export const UNCODED_LINES = {
    [EMPTY_POSITIVE_REASON]: 'Positive prompt is empty.',
    [MULTISTAGE_UNATTRIBUTED_REASON]: 'Several sampler stages were found; their prompts were not compared.',
    [SEMANTICS_UNAVAILABLE_REASON]: "The inspector's node table is unavailable.",
    [UNREADABLE_METADATA_REASON]: 'Metadata in this file could not be read.',
};

// Output adapters (prompt_pair_resolver.py 44-105): [inputName, kind, {compose, storageInput, comma, strip, default}].
export const adapter = (inputName, kind, options = {}) => Object.freeze({ inputName, kind, compose: false, storageInput: false, comma: false, strip: false, default: null, ...options });
export const slotKey = (name, slot) => `${name}\u0000${String(slot)}`;
export const ADAPTERS = new Map();
for (const name of ['PromptManager', 'PromptManagerFocused', 'PromptManagerSplit']) {
    ADAPTERS.set(slotKey(name, 0), adapter('text', 'conditioning', { compose: true }));
    ADAPTERS.set(slotKey(name, 1), adapter('text', 'string', { compose: true }));
}
ADAPTERS.set(slotKey('PromptManagerPair', 0), adapter('text', 'conditioning', { compose: true }));
ADAPTERS.set(slotKey('PromptManagerPair', 2), adapter('text', 'string', { compose: true }));
ADAPTERS.set(slotKey('PromptManagerPair', 1), adapter('negative_text', 'conditioning', { storageInput: true }));
ADAPTERS.set(slotKey('PromptManagerPair', 3), adapter('negative_text', 'string', { storageInput: true }));
ADAPTERS.set(slotKey('PromptManagerText', 0), adapter('text', 'string', { compose: true }));
ADAPTERS.set(slotKey('CLIPTextEncode', 0), adapter('text', 'conditioning'));
for (const name of ['PrimitiveString', 'PrimitiveStringMultiline']) ADAPTERS.set(slotKey(name, 0), adapter('value', 'string'));
for (const name of ['Text Multiline', 'String Literal', 'ShowText|pysssss']) ADAPTERS.set(slotKey(name, 0), adapter('text', 'string'));
// Historical PromptManager classes (inspection only).
export const COMMA = { comma: true }, NEGATIVE = { strip: true, default: '' };
const historical = [
    ['PromptManagerV2', 0, 'text', 'conditioning', COMMA], ['PromptManagerV2', 1, 'negative_text', 'conditioning', NEGATIVE],
    ['PromptManagerV2', 2, 'text', 'string', COMMA], ['PromptManagerV2', 3, 'negative_text', 'string', NEGATIVE],
    ['PromptManagerV2Text', 0, 'text', 'string', COMMA], ['PromptManagerV2Text', 1, 'negative_text', 'string', NEGATIVE],
    ['PromptManagerNegative', 0, 'text', 'conditioning', { strip: true }], ['PromptManagerNegative', 1, 'text', 'string', { strip: true }],
    ['PromptManagerPositiveText', 0, 'text', 'string', COMMA], ['PromptManagerNegativeText', 0, 'text', 'string', COMMA],
];
for (const name of ['PromptManagerPositive', 'PromptManagerDetailer']) historical.push([name, 0, 'text', 'conditioning', COMMA], [name, 1, 'text', 'string', COMMA]);
for (const [name, slot, input, kind, options] of historical) ADAPTERS.set(slotKey(name, slot), adapter(input, kind, options));
// `known_slots` in execution_record: every adapter slot per class.
export const ADAPTER_SLOTS = new Map();
for (const key of ADAPTERS.keys()) {
    const [name, slot] = key.split('\u0000');
    if (!ADAPTER_SLOTS.has(name)) ADAPTER_SLOTS.set(name, new Set());
    ADAPTER_SLOTS.get(name).add(slot);
}

export class Unresolved extends Error {
    constructor(message, code = null, node = null) {
        super(message);
        this.code = code;
        this.node = node;
    }
}
export class Diverging extends Error {
    constructor(key) {
        super('diverging');
        this.key = key;
    }
}

/** `_link`: a two-item list of (str or int) and a non-negative int, never a bool. */
export function isLink(value) {
    if (!isList(value) || value.length !== 2) return false;
    const [source, slot] = value;
    return (typeof source === 'string' || isInt(source)) && isInt(slot) && (typeof slot === 'bigint' ? slot >= 0n : slot >= 0);
}
/** `_reroute_value`: a Reroute's one input (`value`, `input` or `in`), else null. */
export function rerouteValue(inputs) {
    const keys = ['value', 'input', 'in'].filter(key => inputs.has(key));
    return keys.length === 1 ? inputs.get(keys[0]) : null;
}
export const nodeRef = (nodeId, classType) => ({ id: pySlice(nodeId, CLASS_NAME_LENGTH), class_type: typeof classType === 'string' ? pySlice(classType, CLASS_NAME_LENGTH) : null });

export function result(state, { positive = null, negative = null, samplerId = null, reason = '', candidates = [], reasonCode = null, node = null } = {}) {
    return { state, positive, negative, sampler_id: samplerId, reason, candidates, reason_code: reasonCode, node };
}

export class Budget {
    constructor(total) {
        this.total = total;
        this.allowance = total;
    }
    reset(allowance) { this.allowance = allowance; }
    spend() {
        if (this.total <= 0 || this.allowance <= 0) throw new Unresolved('Resolution visit limit reached', 'work-limit');
        this.total -= 1;
        this.allowance -= 1;
    }
    charge(cost) {
        if (cost > this.allowance) throw new Unresolved('Resolution visit limit reached', 'work-limit');
        this.allowance -= cost;
    }
}

/** `read_pair_execution`: one distinct record for a traced node, or null; malformed evidence throws `Unresolved`. */
export function readPairExecution(metadata, nodeId) {
    const evidence = message => new Unresolved(message, 'malformed');
    if (!isDict(metadata) || !metadata.has(EXECUTION_KEY)) return null;
    let envelope = metadata.get(EXECUTION_KEY);
    if (typeof envelope === 'string') {
        if (pyLen(envelope) > MAX_EXECUTION_JSON) throw evidence('Execution evidence exceeds the size limit');
        try { envelope = pyJsonLoads(envelope); } catch (error) {
            if (error instanceof PyValueError) throw evidence('Malformed execution evidence JSON');
            throw error;
        }
    }
    if (!isDict(envelope) || !pyEqualsOne(pyGet(envelope, 'version'))) throw evidence('Unsupported execution evidence format');
    const nodes = pyGet(envelope, 'nodes');
    if (pyTruthy(pyGet(envelope, 'truncated')) || !isDict(nodes) || nodes.size > MAX_EXECUTION_NODES) throw evidence('Truncated or malformed execution evidence');
    if (!nodes.has(nodeId)) return null;
    const node = nodes.get(nodeId);
    if (!isDict(node) || pyTruthy(pyGet(node, 'truncated'))) throw evidence('Truncated or malformed node execution evidence');
    const records = pyGet(node, 'records');
    if (!isList(records) || !records.length || records.length > MAX_EXECUTION_RECORDS) throw evidence('Malformed node execution records');
    const unique = new Map();
    for (const record of records) {
        if (!isDict(record) || EXECUTION_FIELDS.some(key => typeof pyGet(record, key) !== 'string' || pyLen(record.get(key)) > MAX_EXECUTION_TEXT)) {
            throw evidence('Malformed execution text values');
        }
        const clean = Object.fromEntries(EXECUTION_FIELDS.map(key => [key, record.get(key)]));
        if (clean.negative_effective_text !== clean.negative_text) throw evidence('Negative execution text must be unchanged');
        unique.set(JSON.stringify(EXECUTION_FIELDS.map(key => clean[key])), clean);
    }
    if (unique.size !== 1) throw evidence('Multiple distinct executions have no image anchor');
    return [...unique.values()][0];
}

/**
 * The validated semantics table (utils/inspection_semantics.py), from the parsed JSON document. Throws
 * `SemanticsError` for any shape the server rejects; the caller then answers "semantics unavailable".
 */
export function compileSemantics(doc) {
    const fail = message => { throw new SemanticsError(message); };
    const plain = value => value !== null && typeof value === 'object' && !Array.isArray(value);
    if (!plain(doc) || doc.schema !== 1) fail('unsupported semantics table');
    const owners = new Map();
    const claim = (name, category) => {
        if (owners.has(name) && owners.get(name) !== category) fail(`duplicate class ${name}`);
        owners.set(name, category);
    };
    const byClass = category => {
        if (!plain(doc[category])) fail(`category ${category} must be an object`);
        const table = new Map();
        for (const [name, entry] of Object.entries(doc[category])) {
            if (!plain(entry) || typeof entry.strategy !== 'string') fail(`${category}[${name}]`);
            claim(name, category);
            table.set(name, entry);
        }
        return table;
    };
    const bySlot = category => {
        if (!Array.isArray(doc[category])) fail(`category ${category} must be a list`);
        const table = new Map();
        for (const row of doc[category]) {
            if (!plain(row) || typeof row.class !== 'string' || !Number.isInteger(row.slot) || row.slot < 0 || row.slot > 255) fail(`${category} row`);
            const key = slotKey(row.class, row.slot);
            if (table.has(key)) fail(`${category}: duplicate output key`);
            claim(row.class, category);
            const { class: _name, slot: _slot, ...entry } = row;
            table.set(key, entry);
        }
        return table;
    };
    const bySet = category => {
        if (!Array.isArray(doc[category])) fail(`category ${category} must be a list`);
        const set = new Set();
        for (const name of doc[category]) {
            if (typeof name !== 'string' || set.has(name)) fail(`${category}: duplicate class`);
            claim(name, category);
            set.add(name);
        }
        return set;
    };
    const samplers = byClass('samplers'), guiders = byClass('guiders'), selectors = byClass('selectors');
    const composites = byClass('composites'), savers = byClass('savers');
    const conditioning = bySlot('conditioning'), encoders = bySlot('encoders'), strings = bySlot('strings');
    const runtime = bySet('runtime'), nonFileOutputs = bySet('non_file_outputs');
    // `lineage` describes an output's image or latent, not its prompt: its classes are not claimed.
    if (!Array.isArray(doc.lineage)) fail('category lineage must be a list');
    const lineage = new Map();
    for (const row of doc.lineage) {
        if (!plain(row) || typeof row.class !== 'string' || !Number.isInteger(row.slot) || row.slot < 0 || row.slot > 255) fail('lineage row');
        if (row.strategy !== 'continue' || typeof row.input !== 'string' || !row.input) fail('lineage row');
        const key = slotKey(row.class, row.slot);
        if (lineage.has(key)) fail('lineage: duplicate output key');
        lineage.set(key, { strategy: row.strategy, input: row.input });
    }
    const stageInputs = doc.stage_inputs;
    if (!plain(stageInputs) || !Array.isArray(stageInputs.shaped) || !Array.isArray(stageInputs.with_model)) fail('stage_inputs');
    return Object.freeze({
        samplers, guiders, selectors, composites, savers, conditioning, encoders, strings, runtime, nonFileOutputs, lineage,
        stageInputs: { shaped: [...stageInputs.shaped], withModel: [...stageInputs.with_model] }, allClasses: new Set(owners.keys()),
    });
}

/** `outcome_message`: one line for an unresolved outcome, or null to keep the status copy. */
export function outcomeMessage(resolution) {
    if (resolution?.state !== 'unresolved') return null;
    const code = typeof resolution.reason_code === 'string' ? resolution.reason_code : null;
    if (Object.hasOwn(NAMED_LINES, code)) {
        const node = resolution.node, raw = node !== null && typeof node === 'object' ? node.class_type : null;
        const name = typeof raw === 'string' && raw ? pySlice(raw, OUTCOME_NAME_LENGTH) : null;
        return name ? `${NAMED_LINES[code]} (${name}).` : `${NAMED_LINES[code]}.`;
    }
    if (code === null) return Object.hasOwn(UNCODED_LINES, resolution.reason) ? UNCODED_LINES[resolution.reason] : null;
    return Object.hasOwn(CODED_LINES, code) ? CODED_LINES[code] : null;
}

export const sideOf = (status, text = null, source = null) => ({ status, text, source });
export function explained(prompts, resolution) {
    const message = outcomeMessage(resolution);
    if (message === null) return prompts;
    return Object.fromEntries(Object.entries(prompts).map(([role, side]) => [role, side.status === 'unresolved' && !Object.hasOwn(side, 'message') ? { ...side, message } : side]));
}
export const EMPTY_POSITIVE = outcomeMessage({ state: 'unresolved', reason: EMPTY_POSITIVE_REASON });
