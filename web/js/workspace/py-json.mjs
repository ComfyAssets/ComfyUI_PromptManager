// Python's value model for the local inspection resolver (#249 local Inspect, PR L3): the server's resolver
// (utils/prompt_pair_resolver.py) runs on `json.loads` output, so the browser port reads the same text the same way.
// Objects are `Map`s (insertion order, a repeated key keeps its first position and its last value, as a dict does),
// integers are numbers (or BigInt past 2^53 - 1), floats are `PyFloat`, None is null. Strings are measured, sliced and
// stripped as Python's `str` is: by code point, with `str.isspace()` whitespace. No DOM.

/** A Python float: distinct from an int (`1.0` is not a link slot, `type(x) is int` is false). */
export class PyFloat {
    constructor(value) {
        this.value = value;
        Object.freeze(this);
    }
}

/** `ValueError` from `json.loads` (malformed text, or an integer past `sys.int_info.str_digits_check_threshold`). */
export class PyValueError extends Error {
    constructor(message) {
        super(message);
        this.name = 'PyValueError';
    }
}

// CPython's default int max str digits (PEP 651 / gh-95778).
const INT_MAX_STR_DIGITS = 4300;
const MAX_SAFE_DIGITS = '9007199254740991';
const NUMBER = /-?(?:0|[1-9][0-9]*)(\.[0-9]+)?([eE][-+]?[0-9]+)?/y;
const STRING_STOP = /["\\\u0000-\u001f]/g;
const HEX4 = /^[0-9a-fA-F]{4}$/;
const ESCAPES = { '"': '"', '\\': '\\', '/': '/', b: '\b', f: '\f', n: '\n', r: '\r', t: '\t' };
// The C scanner's constants, in its order of checks.
const CONSTANTS = [['null', null], ['true', true], ['false', false], ['NaN', new PyFloat(NaN)],
    ['Infinity', new PyFloat(Infinity)], ['-Infinity', new PyFloat(-Infinity)]];

export const isDict = value => value instanceof Map;
export const isList = value => Array.isArray(value);
/** `isinstance(value, int) and not isinstance(value, bool)`. */
export const isInt = value => (typeof value === 'number' && Number.isInteger(value)) || typeof value === 'bigint';

function numberValue(match) {
    const [literal, fraction, exponent] = match;
    if (fraction || exponent) return new PyFloat(Number(literal));
    const digits = literal[0] === '-' ? literal.slice(1) : literal;
    if (digits.length > INT_MAX_STR_DIGITS) throw new PyValueError('Exceeds the limit for integer string conversion');
    if (digits.length > MAX_SAFE_DIGITS.length || (digits.length === MAX_SAFE_DIGITS.length && digits > MAX_SAFE_DIGITS)) return BigInt(literal);
    // `-0` is the int 0.
    return Number(literal) || 0;
}

/** `json.loads(text)` with Python's defaults (C scanner): NaN/Infinity accepted, a repeated key's last value wins. */
export function pyJsonLoads(text) {
    if (typeof text !== 'string') throw new TypeError('JSON text must be a string');
    if (text.startsWith('﻿')) throw new PyValueError('Unexpected UTF-8 BOM (decode using utf-8-sig)');
    let pos = 0;
    const fail = message => new PyValueError(`${message}: char ${pos}`);
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
            if (!stop) throw fail('Unterminated string starting at');
            out += text.slice(pos, stop.index);
            pos = stop.index + 1;
            if (stop[0] === '"') return out;
            if (stop[0] !== '\\') throw fail('Invalid control character at');
            const escape = text[pos];
            if (escape === 'u') {
                const hex = text.slice(pos + 1, pos + 5);
                if (!HEX4.test(hex)) throw fail('Invalid \\uXXXX escape');
                pos += 5;
                const code = parseInt(hex, 16);
                if (code >= 0xd800 && code <= 0xdbff && text[pos] === '\\' && text[pos + 1] === 'u') {
                    const low = text.slice(pos + 2, pos + 6);
                    if (!HEX4.test(low)) throw fail('Invalid \\uXXXX escape');
                    const lowCode = parseInt(low, 16);
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
        for (const [literal, value] of CONSTANTS) {
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
            const frame = { value: new Map(), object: true, key: null };
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
        for (;;) {
            if (!stack.length) { result = value; break; }
            const frame = stack[stack.length - 1];
            if (frame.object) frame.value.set(frame.key, value);
            else frame.value.push(value);
            skip();
            if (text[pos] === ',') {
                pos++; skip();
                if (frame.object) frame.key = key();
                break;
            }
            if (text[pos] !== (frame.object ? '}' : ']')) throw fail("Expecting ',' delimiter");
            pos++;
            stack.pop();
            value = frame.value;
        }
        if (!stack.length) break;
    }
    skip();
    if (pos !== text.length) throw fail('Extra data');
    return result;
}

/** `len(text)`: code points (a lone surrogate counts once). */
export function pyLen(text) {
    let pairs = 0;
    for (let index = 0; index < text.length - 1; index++) {
        const code = text.charCodeAt(index);
        if (code >= 0xd800 && code <= 0xdbff) {
            const next = text.charCodeAt(index + 1);
            if (next >= 0xdc00 && next <= 0xdfff) { pairs++; index++; }
        }
    }
    return text.length - pairs;
}

/** `text[:count]` by code points. */
export function pySlice(text, count) {
    if (text.length <= count) return text;
    let end = 0;
    for (let taken = 0; taken < count && end < text.length; taken++) {
        const code = text.charCodeAt(end);
        const next = text.charCodeAt(end + 1);
        end += code >= 0xd800 && code <= 0xdbff && next >= 0xdc00 && next <= 0xdfff ? 2 : 1;
    }
    return text.slice(0, end);
}

// `str.isspace()`: Unicode Zs plus the bidi classes B, S and WS (not U+FEFF, unlike `trim()`).
const SPACE = '\\t\\n\\x0b\\x0c\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000';
const LEADING = new RegExp(`^[${SPACE}]+`), TRAILING = new RegExp(`[${SPACE}]+$`);
/** `text.strip()`. */
export const pyStrip = text => text.replace(LEADING, '').replace(TRAILING, '');

/** `bool(value)`. */
export function pyTruthy(value) {
    if (value === null || value === undefined || value === false) return false;
    if (value === true) return true;
    if (typeof value === 'number') return value !== 0;
    if (typeof value === 'bigint') return value !== 0n;
    if (value instanceof PyFloat) return value.value !== 0;
    if (typeof value === 'string' || isList(value)) return value.length > 0;
    if (isDict(value)) return value.size > 0;
    return true;
}

/** `value == 1` (True and 1.0 equal it too). */
export const pyEqualsOne = value => value === 1 || value === 1n || value === true || (value instanceof PyFloat && value.value === 1);

/** `repr(float)`: the shortest round-trip digits, fixed for exponents -4..15, else `d.ddde+XX`. */
export function pyFloatRepr(value) {
    if (Number.isNaN(value)) return 'nan';
    if (!Number.isFinite(value)) return value > 0 ? 'inf' : '-inf';
    if (value === 0) return Object.is(value, -0) ? '-0.0' : '0.0';
    const [mantissa, exponentText] = value.toExponential().split('e');
    const exponent = Number(exponentText);
    const sign = mantissa.startsWith('-') ? '-' : '';
    const digits = mantissa.replace(/^-/, '').replace('.', '');
    if (exponent < -4 || exponent >= 16) {
        const rest = digits.slice(1);
        return `${sign}${digits[0]}${rest ? `.${rest}` : ''}e${exponent < 0 ? '-' : '+'}${String(Math.abs(exponent)).padStart(2, '0')}`;
    }
    if (exponent < 0) return `${sign}0.${'0'.repeat(-exponent - 1)}${digits}`;
    const whole = digits.slice(0, exponent + 1).padEnd(exponent + 1, '0'), fraction = digits.slice(exponent + 1);
    return `${sign}${whole}.${fraction || '0'}`;
}

/**
 * `str(value)` where the resolver compares the result with node class names (`str(node.get("class_type", ""))`).
 * A container's text never equals a class name, so it is a marker only.
 */
export function pyStr(value) {
    if (typeof value === 'string') return value;
    if (value === null || value === undefined) return 'None';
    if (value === true) return 'True';
    if (value === false) return 'False';
    if (typeof value === 'number' || typeof value === 'bigint') return String(value);
    if (value instanceof PyFloat) return pyFloatRepr(value.value);
    return '\u0000container';
}

/** `mapping.get(key, fallback)`. */
export const pyGet = (mapping, key, fallback = null) => mapping.has(key) ? mapping.get(key) : fallback;
