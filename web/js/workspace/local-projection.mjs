// Browser port of utils/inspection_response.py: the strict parse (Decimal
// spelling, no duplicate keys, NaN/Infinity as null, integers past 2^53-1 as
// text), the prompt/workflow/carrier projection with its depth and node
// ceilings, `_metadata`, and the response-size omission steps of
// `_encode_inspection`. A local file must project exactly as the server does for
// the same bytes; tests/test_local_inspect_parity.py holds both to one answer.
// Pure: no DOM, no I/O.

export const MAX_RESPONSE_BYTES = 8 * 1024 * 1024;
export const MAX_PROJECTION_DEPTH = 64;
// Backstop, not an expected outcome: executable API nodes for a prompt; UI nodes
// including subgraph definitions for a workflow.
export const GRAPH_NODE_CEILING = 20000;
export const RECORD_KEYS = Object.freeze(['prompt', 'workflow', 'parameters', 'comfyassets_prompt_pairs']);
const FORMATS = new Set(['original-text', 'reserialized-json', 'invalid', 'limited']);
const PROMPT_NODE_KEYS = new Set(['class_type', 'inputs']);
const MAX_SAFE_DIGITS = '9007199254740991';
// Python's int() refuses more digits than sys.get_int_max_str_digits() (4300).
export const INT_MAX_STR_DIGITS = 4300;
// decimal's exact constructor (libmpdec, 64-bit) refuses values outside its
// maximal context: adjusted exponent above MAX_EMAX or exponent below Etiny.
const DECIMAL_MAX_EMAX = 999999999999999999n;
const DECIMAL_ETINY = -1999999999999999997n;
const NUMBER = /-?(?:0|[1-9][0-9]*)(\.[0-9]+)?([eE][-+]?[0-9]+)?/y;
const STRING_STOP = /["\\\u0000-\u001f]/g;
const HEX4 = /^[0-9a-fA-F]{4}$/;
const ESCAPES = { '"': '"', '\\': '\\', '/': '/', b: '\b', f: '\f', n: '\n', r: '\r', t: '\t' };
// Python's json: `null`/`true`/`false`, then the constants parse_constant receives.
const LITERALS = [['null', null], ['true', true], ['false', false], ['NaN', null], ['Infinity', null], ['-Infinity', null]];

/** A present graph cannot be projected within inspection limits (`_ProjectionLimit`). */
export class ProjectionLimit extends Error {
    constructor() {
        super('Projection exceeds inspection limits');
        this.name = 'ProjectionLimit';
    }
}

/** The server's ValueError/TypeError family: the text or tree is malformed. */
export class ProjectionError extends Error {
    constructor(message) {
        super(message);
        this.name = 'ProjectionError';
    }
}

const isArray = Array.isArray;
const isObject = value => value !== null && typeof value === 'object' && !isArray(value);
const isContainer = value => value !== null && typeof value === 'object';

/** Python's `str(Decimal(literal))` from the literal's digits and exponent, never through a float. */
export function spellDecimal(literal) {
    const negative = literal[0] === '-';
    const body = negative ? literal.slice(1) : literal;
    const exponentAt = body.search(/[eE]/);
    const mantissa = exponentAt < 0 ? body : body.slice(0, exponentAt);
    const dot = mantissa.indexOf('.');
    const integer = dot < 0 ? mantissa : mantissa.slice(0, dot), fraction = dot < 0 ? '' : mantissa.slice(dot + 1);
    const exponent = (exponentAt < 0 ? 0n : BigInt(body.slice(exponentAt + 1))) - BigInt(fraction.length);
    const digits = (integer + fraction).replace(/^0+/, '') || '0';
    const adjusted = exponent + BigInt(digits.length) - 1n;
    if (adjusted > DECIMAL_MAX_EMAX || exponent < DECIMAL_ETINY) throw new ProjectionError('JSON decimal exceeds supported range');
    const leftDigits = exponent + BigInt(digits.length);
    const dotPlace = exponent <= 0n && leftDigits > -6n ? leftDigits : 1n;
    let intPart, fracPart;
    if (dotPlace <= 0n) {
        intPart = '0'; fracPart = '.' + '0'.repeat(Number(-dotPlace)) + digits;
    } else if (dotPlace >= BigInt(digits.length)) {
        intPart = digits + '0'.repeat(Number(dotPlace - BigInt(digits.length))); fracPart = '';
    } else {
        intPart = digits.slice(0, Number(dotPlace)); fracPart = '.' + digits.slice(Number(dotPlace));
    }
    const shift = leftDigits - dotPlace;
    const exp = shift === 0n ? '' : `E${shift > 0n ? '+' : '-'}${shift < 0n ? -shift : shift}`;
    return (negative ? '-' : '') + intPart + fracPart + exp;
}

function numberValue(match) {
    const [literal, fraction, exponent] = match;
    if (fraction || exponent) return spellDecimal(literal);
    const digits = literal[0] === '-' ? literal.slice(1) : literal;
    if (digits.length > INT_MAX_STR_DIGITS) throw new ProjectionError('Exceeds the limit for integer string conversion');
    if (digits.length > MAX_SAFE_DIGITS.length || (digits.length === MAX_SAFE_DIGITS.length && digits > MAX_SAFE_DIGITS)) return literal;
    return Number(literal) || 0;
}

/**
 * `json.loads(text, parse_float=Decimal, object_pairs_hook=_pairs, parse_constant=_nonfinite)`
 * with its results already normalized: objects have a null prototype, fractions and
 * exponents are Decimal text, integers past 2^53-1 are their literal text, and
 * NaN/Infinity are null. Iterative, so nesting depth never overflows the stack.
 */
export function parseStrictJson(text) {
    if (typeof text !== 'string') throw new ProjectionError('JSON text must be a string');
    let pos = 0;
    const fail = message => new ProjectionError(`${message} at ${pos}`);
    const skip = () => {
        while (pos < text.length) {
            const c = text.charCodeAt(pos);
            if (c !== 0x20 && c !== 0x09 && c !== 0x0a && c !== 0x0d) break;
            pos++;
        }
    };
    function string() {
        pos++;
        let out = '';
        for (;;) {
            STRING_STOP.lastIndex = pos;
            const stop = STRING_STOP.exec(text);
            if (!stop) throw fail('Unterminated string');
            out += text.slice(pos, stop.index);
            pos = stop.index + 1;
            if (stop[0] === '"') return out;
            if (stop[0] !== '\\') throw fail('Invalid control character');
            const escape = text[pos];
            if (escape === 'u') {
                const hex = text.slice(pos + 1, pos + 5);
                if (!HEX4.test(hex)) throw fail('Invalid \\uXXXX escape');
                pos += 5;
                const code = parseInt(hex, 16);
                if (code >= 0xd800 && code <= 0xdbff && text[pos] === '\\' && text[pos + 1] === 'u') {
                    const low = text.slice(pos + 2, pos + 6);
                    const lowCode = HEX4.test(low) ? parseInt(low, 16) : -1;
                    if (lowCode >= 0xdc00 && lowCode <= 0xdfff) {
                        out += String.fromCharCode(code, lowCode);
                        pos += 6;
                        continue;
                    }
                }
                out += String.fromCharCode(code);
            } else if (Object.hasOwn(ESCAPES, escape)) {
                out += ESCAPES[escape];
                pos++;
            } else throw fail('Invalid \\escape');
        }
    }
    function key() {
        if (text[pos] !== '"') throw fail('Expecting property name enclosed in double quotes');
        const name = string();
        skip();
        if (text[pos] !== ':') throw fail("Expecting ':' delimiter");
        pos++;
        skip();
        return name;
    }
    function scalar() {
        if (text[pos] === '"') return string();
        for (const [literal, value] of LITERALS) {
            if (text.startsWith(literal, pos)) {
                pos += literal.length;
                return value;
            }
        }
        NUMBER.lastIndex = pos;
        const match = NUMBER.exec(text);
        if (!match) throw fail('Expecting value');
        pos += match[0].length;
        return numberValue(match);
    }
    skip();
    const stack = [];
    let result;
    for (;;) {
        let value;
        if (text[pos] === '{') {
            pos++; skip();
            const frame = { value: Object.create(null), object: true, key: null, duplicate: false };
            if (text[pos] === '}') pos++;
            else { frame.key = key(); stack.push(frame); continue; }
            value = frame.value;
        } else if (text[pos] === '[') {
            pos++; skip();
            const frame = { value: [], object: false };
            if (text[pos] === ']') pos++;
            else { stack.push(frame); continue; }
            value = frame.value;
        } else value = scalar();
        // Attach the finished value, closing containers whose last member it was.
        for (;;) {
            if (!stack.length) { result = value; break; }
            const frame = stack[stack.length - 1];
            if (frame.object) {
                if (Object.hasOwn(frame.value, frame.key)) frame.duplicate = true;
                frame.value[frame.key] = value;
            } else frame.value.push(value);
            skip();
            if (text[pos] === ',') {
                pos++; skip();
                if (frame.object) frame.key = key();
                break;
            }
            if (text[pos] !== (frame.object ? '}' : ']')) throw fail("Expecting ',' delimiter");
            pos++;
            stack.pop();
            // `_pairs` runs when the object closes, after everything inside it parsed.
            if (frame.object && frame.duplicate) throw new ProjectionError('Duplicate JSON key');
            value = frame.value;
        }
        if (!stack.length) break;
    }
    skip();
    if (pos !== text.length) throw fail('Extra data');
    return result;
}

/** `_normalize`: walk once; unretained values are only checked against the depth bound. */
function normalize(value, depth, retain) {
    if (!isContainer(value)) return retain ? value : null;
    if (depth > MAX_PROJECTION_DEPTH) throw new ProjectionLimit();
    if (isArray(value)) {
        const result = retain ? [] : null;
        for (const child of value) {
            const normalized = normalize(child, depth + 1, retain);
            if (retain) result.push(normalized);
        }
        return result;
    }
    const result = retain ? Object.create(null) : null;
    for (const key of Object.keys(value)) {
        const normalized = normalize(value[key], depth + 1, retain);
        if (retain) result[key] = normalized;
    }
    return result;
}

/** `_project_prompt`: each API node keeps exactly `class_type` and `inputs`; other keys are validated and dropped. */
function projectPrompt(tree, depth) {
    if (!isObject(tree)) throw new ProjectionError('Prompt graph must be an object of nodes');
    if (depth + 1 > MAX_PROJECTION_DEPTH) throw new ProjectionLimit();
    const result = Object.create(null);
    for (const id of Object.keys(tree)) {
        const node = tree[id];
        if (!isObject(node)) {
            result[id] = normalize(node, depth + 1, true);
            continue;
        }
        const projected = Object.create(null);
        for (const key of Object.keys(node)) {
            const keep = PROMPT_NODE_KEYS.has(key);
            const normalized = normalize(node[key], depth + 2, keep);
            if (keep) projected[key] = normalized;
        }
        result[id] = projected;
    }
    return result;
}

function project(tree, kind, depth) {
    if (kind === 'prompt') return projectPrompt(tree, depth);
    if (kind === 'workflow') return normalize(tree, depth, true);
    if (!isObject(tree)) throw new ProjectionError('Malformed carrier');
    const result = Object.create(null);
    for (const key of Object.keys(tree)) {
        const member = tree[key];
        if (key !== 'prompt' && key !== 'workflow') normalize(member, depth + 1, false);
        else if (typeof member === 'string') result[key] = member; // projected by its own kind when read
        else if (key === 'prompt' && !isObject(member)) result[key] = normalize(member, depth + 1, true);
        else result[key] = project(member, key, depth + 1);
    }
    return result;
}

/** `_strict_projection`: parse exactly and project by record kind ('prompt'|'workflow'|'carrier'). */
export function strictProjection(text, kind) {
    return project(parseStrictJson(text), kind, 1);
}

function nodeCount(value, workflow) {
    if (!workflow) return Object.keys(value).length;
    const nodes = value.nodes;
    let count = isArray(nodes) ? nodes.length : 0;
    const definitions = value.definitions;
    const subgraphs = isObject(definitions) ? definitions.subgraphs : null;
    for (const subgraph of isArray(subgraphs) ? subgraphs : []) {
        const inner = isObject(subgraph) ? subgraph.nodes : null;
        count += isArray(inner) ? inner.length : 0;
    }
    return count;
}

function graph(value, workflow) {
    if (!isObject(value)) return { status: 'malformed', value: null };
    if (nodeCount(value, workflow) > GRAPH_NODE_CEILING) return { status: 'limited', value: null };
    return { status: 'parsed', value };
}

function outcome(error) {
    if (error instanceof ProjectionLimit) return { status: 'limited', value: null };
    if (error instanceof ProjectionError) return { status: 'malformed', value: null };
    throw error;
}

/**
 * One record's graph as the inspector projects it: `{status, value}` with `status`
 * `parsed` (the projected graph the facts parser receives), `limited` (past the
 * depth or node bound) or `malformed`.
 */
export function projectGraph(text, kind) {
    try {
        return graph(strictProjection(text, kind), kind === 'workflow');
    } catch (error) {
        return outcome(error);
    }
}

/**
 * `_metadata` for one local read: `{records, record_formats, carrierText, limited}`
 * (the server's InspectionRead) or null when the file's metadata is unreadable.
 */
export function projectMetadata(inspection) {
    const formats = {}, raw = {};
    if (inspection) {
        for (const [key, value] of Object.entries(inspection.record_formats ?? {})) {
            if (RECORD_KEYS.includes(key) && FORMATS.has(value)) formats[key] = value;
        }
        // A record over its read budget has no text: like `invalid`, raw omits it,
        // and its graph is `limited` rather than parsed from nothing.
        for (const key of inspection.limited ?? []) if (RECORD_KEYS.includes(key)) formats[key] = 'limited';
        for (const [key, value] of Object.entries(inspection.records ?? {})) {
            if (RECORD_KEYS.includes(key) && typeof value === 'string' && formats[key] !== 'limited') raw[key] = value;
        }
    }
    const result = {
        status: inspection === null || inspection === undefined ? 'unreadable' : Object.keys(raw).length || Object.keys(formats).length ? 'available' : 'absent',
        omitted_reason: null, raw, record_formats: formats, parameters: Object.hasOwn(raw, 'parameters') ? raw.parameters : null,
    };
    let carrier = null, failed = null;
    if (inspection && inspection.carrierText !== null && inspection.carrierText !== undefined) {
        try { carrier = strictProjection(inspection.carrierText, 'carrier'); } catch (error) { failed = outcome(error).status; }
    }
    for (const [key, field] of [['prompt', 'prompt_graph'], ['workflow', 'workflow_graph']]) {
        let value = { status: 'absent', value: null };
        if (Object.hasOwn(raw, key) || Object.hasOwn(formats, key)) {
            if (failed || formats[key] === 'limited') value.status = failed || 'limited';
            else if (formats[key] === 'invalid') value.status = 'malformed';
            else if (carrier === null && formats[key] === 'reserialized-json') value.status = 'limited';
            else if (carrier === null) value = projectGraph(raw[key], key);
            else {
                try {
                    let member = carrier[key];
                    if (typeof member === 'string') member = strictProjection(member, key);
                    value = graph(member, key === 'workflow');
                } catch (error) { value.status = outcome(error).status; }
            }
        }
        result[field] = value;
    }
    return result;
}

/** UTF-8 length of `JSON.stringify(value)`: the server's `_bytes` count (lone surrogates are already escaped). */
export function inspectionBytes(value) {
    const text = JSON.stringify(value);
    let count = 0;
    for (let index = 0; index < text.length; index++) {
        const code = text.charCodeAt(index);
        if (code < 0x80) count += 1;
        else if (code < 0x800) count += 2;
        else if (code >= 0xd800 && code <= 0xdbff) { count += 4; index++; } else count += 3;
    }
    return count;
}

const withMetadata = (payload, changes) => ({ ...payload, metadata: { ...payload.metadata, ...changes } });

/**
 * The omission steps of `_encode_inspection`: the workflow graph first, then the
 * prompt graph, then all raw records atomically, each measured as encoded bytes.
 * Returns a new payload; throws when even the bare payload is over `limit`.
 */
export function fitInspectionPayload(payload, limit = MAX_RESPONSE_BYTES) {
    let fitted = payload, body = inspectionBytes(payload);
    for (const field of ['workflow_graph', 'prompt_graph']) {
        if (body <= limit) break;
        if (fitted.metadata[field].status !== 'absent') {
            fitted = withMetadata(fitted, { [field]: { status: 'limited', value: null } });
            body = inspectionBytes(fitted);
        }
    }
    if (body > limit) {
        fitted = withMetadata(fitted, { raw: {}, parameters: null, status: 'limited', omitted_reason: 'response-size-limit' });
        body = inspectionBytes(fitted);
    }
    if (body > limit) throw new RangeError('Inspection response exceeds limits');
    return fitted;
}
