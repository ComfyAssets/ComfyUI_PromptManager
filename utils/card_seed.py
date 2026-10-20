"""Workspace card facts from the metadata records a header open already loaded.

The card shows what the inspector shows, so it reads the same shared parser
(``extract_generation_params``, the Python twin of the browser parser the
inspector fills from since #251) and nothing else: no separate seed heuristic,
no graph resolution and no extra file reads. One parse feeds both the seed and
the positive that confirms a first-line card title.
"""

import math
import re
from collections.abc import Mapping

from .generation_params import (
    PLACEHOLDERS,
    GenerationParamsError,
    extract_from_records,
    extract_generation_params,
    number_string,
    parse_js_json,
)

__all__ = ["MAX_RECORD_BYTES", "card_seed", "header_params", "parsed_positive"]

# Each record is parsed only up to this many UTF-8 bytes; past it, nothing is read.
MAX_RECORD_BYTES = 2 * 1024 * 1024
_RECORD_KEYS = ("prompt", "workflow")
_INTEGER = re.compile(r"-?[0-9]+\Z")
_MAX_SAFE_INTEGER = 2**53 - 1


def _utf8_size_over(text: str, limit: int) -> bool:
    """True when ``text`` is over ``limit`` UTF-8 bytes, encoding only if unclear.

    A code point takes one to four bytes, so most records are decided from
    their length alone without copying them.
    """
    if len(text) > limit:
        return True
    if len(text) * 4 <= limit:
        return False
    return len(text.encode("utf-8", "surrogatepass")) > limit


def header_params(info) -> dict | None:
    """The shared parser's result for the ``prompt``/``workflow`` text in ``info``.

    ``info`` is a Pillow ``Image.info`` (or the inspection read's metadata).
    None when neither record is present, a record is not text or is over
    ``MAX_RECORD_BYTES``, or the browser parser would throw on it.
    """
    if not isinstance(info, Mapping):
        return None
    records = {key: info.get(key) for key in _RECORD_KEYS}
    if all(text is None for text in records.values()):
        return None
    for text in records.values():
        if text is None:
            continue
        if not isinstance(text, str):
            return None
        if _utf8_size_over(text, MAX_RECORD_BYTES):
            return None
    try:
        params = extract_from_records(records["prompt"], records["workflow"])
    except (GenerationParamsError, RecursionError):
        # RecursionError: the Python parser recurses on deeply nested arrays
        # where JavaScript does not throw; a crafted file gives no card facts.
        return None
    return _with_exact_seed(params, records)


def _with_exact_seed(params: dict, records: dict) -> dict:
    """``params`` with a rounded seed replaced by the integer the file records.

    ``JSON.parse`` rounds integers beyond 2**53, but the inspector reads the
    record's own digits (its projection carries big numbers as text). ComfyUI's
    randomized seeds are mostly that large, so the same parser is run once more
    on an exact parse of the same records, and its seed is kept only when it is
    the one that rounded to the parsed value. Anything else leaves the seed
    as parsed.
    """
    seed = params.get("seed")
    if not isinstance(seed, float) or not math.isfinite(seed):
        return params
    if abs(seed) <= _MAX_SAFE_INTEGER or not seed.is_integer():
        return params
    try:
        exact = extract_generation_params(
            {
                key: parse_js_json(text, exact_integers=True)
                for key, text in records.items()
                if text is not None
            }
        )["seed"]
    except (GenerationParamsError, RecursionError):
        return params
    if isinstance(exact, bool) or not isinstance(exact, int) or float(exact) != seed:
        return params
    return {**params, "seed": exact}


def card_seed(params: dict | None) -> int | None:
    """The parsed seed as the integer the browser prints, else None.

    A placeholder, text, boolean or non-integral number gives None. A double
    that ``header_params`` could not make exact gives the integer whose decimal
    form equals the browser's ``String(seed)``.
    """
    if not isinstance(params, Mapping):
        return None
    value = params.get("seed")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    text = number_string(value)
    return int(text) if _INTEGER.match(text) else None


def parsed_positive(params: dict | None) -> str | None:
    """The parsed positive prompt text, or None for the placeholder or blank."""
    if not isinstance(params, Mapping):
        return None
    text = params.get("positivePrompt")
    if not isinstance(text, str) or text == PLACEHOLDERS["positivePrompt"]:
        return None
    return text if text.strip() else None
