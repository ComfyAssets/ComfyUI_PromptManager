"""Workspace card facts from the metadata records a header open already loaded.

The card shows what the inspector shows, so it reads exactly what the
inspector's browser parser reads: each ``prompt``/``workflow`` record through
the inspection projection (``project_graph``: strict JSON, the depth and node
bounds, big integers and decimals as text), and only graphs it parsed, through
the shared parser (``extract_generation_params``, the Python twin of the
browser parser the inspector fills from since #251). No separate seed
heuristic, no graph resolution and no extra file reads. One parse feeds both
the seed and the positive that confirms a first-line card title.
"""

import re
from collections.abc import Mapping

from .generation_params import (
    PLACEHOLDERS,
    GenerationParamsError,
    extract_generation_params,
)
from .inspection_response import project_graph

__all__ = [
    "MAX_RECORD_BYTES",
    "MAX_SEED_DIGITS",
    "card_seed",
    "header_params",
    "parsed_positive",
]

# Each record is parsed only up to this many UTF-8 bytes; past it, nothing is read.
MAX_RECORD_BYTES = 2 * 1024 * 1024
_RECORD_KEYS = ("prompt", "workflow")
# A digit-string seed has at most this many digits (2**64 has 20). Longer text
# is no seed, and never reaches int(), whose max-str-digits guard raises.
MAX_SEED_DIGITS = 20
_INTEGER = re.compile(r"-?[0-9]{1,%d}\Z" % MAX_SEED_DIGITS)


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
    """The shared parser's result for the inspector's view of ``info``'s records.

    ``info`` is a Pillow ``Image.info`` (or the inspection read's metadata).
    A record the projection does not parse is left out, as the inspector
    leaves it out. None when no record parses, a record is not text or is
    over ``MAX_RECORD_BYTES``, or the parser cannot use the graphs.
    """
    if not isinstance(info, Mapping):
        return None
    graphs = {}
    for key in _RECORD_KEYS:
        text = info.get(key)
        if text is None:
            continue
        if not isinstance(text, str) or _utf8_size_over(text, MAX_RECORD_BYTES):
            return None
        graph = project_graph(text, key)
        if graph["status"] == "parsed":
            graphs[key] = graph["value"]
    if not graphs:
        return None
    try:
        return extract_generation_params(graphs)
    except (GenerationParamsError, RecursionError):
        # Defence in depth: a parser failure gives no card facts, never a 500.
        return None


def card_seed(params: dict | None) -> str | None:
    """The parsed seed as a decimal string, else None.

    Always text: ``JSON.parse`` in the browser rounds a number past 2**53, and
    ComfyUI's randomized seeds are often that large. The projection carries
    integers beyond 2**53 as their exact digits, so a digit string is the
    recorded seed (the inspector shows it too), up to ``MAX_SEED_DIGITS``
    digits. A placeholder, other or longer text, a boolean or a decimal gives
    None.
    """
    if not isinstance(params, Mapping):
        return None
    value = params.get("seed")
    if isinstance(value, bool):
        return None
    try:
        if isinstance(value, int):
            return str(value)
        if isinstance(value, str) and _INTEGER.match(value):
            return str(int(value))
    except ValueError:
        # Python's integer string conversion limit: no seed, never a 500.
        return None
    return None


def parsed_positive(params: dict | None) -> str | None:
    """The parsed positive prompt text, or None for the placeholder or blank."""
    if not isinstance(params, Mapping):
        return None
    text = params.get("positivePrompt")
    if not isinstance(text, str) or text == PLACEHOLDERS["positivePrompt"]:
        return None
    return text if text.strip() else None
