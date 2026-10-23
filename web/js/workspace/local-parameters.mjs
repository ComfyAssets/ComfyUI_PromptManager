// A1111 `parameters` for local-resolver.mjs (#249 local Inspect, PR L3 Task 9): a port of
// utils/prompt_pair_resolver.py `_parameters`, `_footer` and `_trailer_lines` for the inspection profile.
// Python's `re` differs from JavaScript's in three ways that matter here, so the patterns spell them out:
// multiline `^`/`$` break only at "\n" (not "\r", U+2028 or U+2029), `\s` is the `str.isspace()` set and `\d`
// is any Unicode decimal digit (Nd). Lengths count code points. No DOM.
import { PY_SPACE, pyLen, pyStrip } from './py-json.mjs';
import { EMPTY_POSITIVE_REASON, result } from './local-resolver-support.mjs';

const START = '(?<=^|\\n)', END = '(?=\\n|$)', SPACE = `[${PY_SPACE}]*`;
// `_FOOTER` and `_BARE_FOOTER` (inspection reads a bare `Steps: N` when there is no full footer).
const FOOTER = new RegExp(`${START}Steps:${SPACE}\\p{Nd}+${SPACE},[^\\n]*${END}`, 'gu');
const BARE_FOOTER = new RegExp(`${START}Steps:${SPACE}\\p{Nd}+${SPACE}${END}`, 'gu');
const TRAILERS = ['Template:', 'Negative Template:'];
const DELIMITER = '\nNegative prompt:';

/** `_trailer_lines`: empty, or at most one `Template:` line then at most one `Negative Template:` line. */
function trailerLines(text) {
    const stripped = pyStrip(text);
    const lines = stripped ? stripped.split(/\r?\n|\r/) : [];
    const prefixes = lines.map(line => {
        const index = TRAILERS.findIndex(name => line.startsWith(name));
        return index === -1 ? null : index;
    });
    return !prefixes.includes(null) && prefixes.every((prefix, index) => index === 0 || prefix > prefixes[index - 1]);
}

/** `_footer(value, inspection=True)`: the unique settings footer match, or null. */
function footer(value) {
    let footers = [...value.matchAll(FOOTER)];
    if (!footers.length) footers = [...value.matchAll(BARE_FOOTER)];
    if (footers.length !== 1) return null;
    const [match] = footers;
    return trailerLines(value.slice(match.index + match[0].length)) ? match : null;
}

const rstripLineBreaks = text => text.replace(/[\r\n]+$/, '');

/** `_parameters(value, max_text_length, inspection=True)`. */
export function parameters(value, maxTextLength) {
    if (typeof value !== 'string' || !value) return result('absent', { reason: 'No A1111 parameters' });
    if (pyLen(value) > maxTextLength) return result('unresolved', { reason: 'Parameters exceed the length limit', reasonCode: 'text-limit' });
    const match = footer(value);
    if (match === null) return result('unresolved', { reason: 'A1111 settings footer is not unique', reasonCode: 'malformed' });
    // R4: text that starts with the delimiter has an empty positive (#165).
    const body = `\n${rstripLineBreaks(value.slice(0, match.index))}`;
    const parts = body.split(DELIMITER);
    if (parts.length > 2) return result('ambiguous', { reason: 'Multiple negative prompt delimiters' });
    const positive = pyStrip(parts[0]);
    if (!positive) return result('unresolved', { reason: EMPTY_POSITIVE_REASON });
    const provenance = { format: 'a1111', field: 'parameters', text_source: 'embedded-parameters' };
    const side = (text, role) => ({ text, storage_text: text, provenance: { ...provenance, role } });
    if (parts.length === 1) return result('absent', { positive: side(positive, 'positive'), reason: 'Negative prompt delimiter is absent' });
    const negative = pyStrip(parts[1]);
    return result('resolved', { positive: side(positive, 'positive'), negative: side(negative, 'negative') });
}
