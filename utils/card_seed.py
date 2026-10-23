"""Workspace card facts from the metadata records a header open already loaded.

The card shows what the inspector shows, so it reads exactly what the
inspector's browser parser reads: each ``prompt``/``workflow`` record through
the inspection projection (``project_graph``: strict JSON, the depth and node
bounds, big integers and decimals as text), and only graphs it parsed, through
the shared parser (``extract_generation_params``, the Python twin of the
browser parser the inspector fills from since #251). No separate seed
heuristic and no extra file reads. One parse feeds both the seed and the
positive that confirms a first-line card title. One exception keeps the card
from ever showing a seed the inspector does not: when sampler stages read
different seed inputs and the inspector's resolution names one of them for a
resolved pair (whose facts are that stage's), the card shows no seed when the
named stage's seed differs from the parser's. Only such graphs are resolved,
with the inspector's own profile.
"""

import json
import re
from collections.abc import Mapping

from .generation_params import (
    PLACEHOLDERS,
    GenerationParamsError,
    _is_sampler,
    extract_generation_params,
)
from .inspection_response import project_graph
from .inspection_semantics import SemanticsError, get_semantics
from .prompt_pair_resolver import INSPECTION_PROFILE, resolve_prompt_pair

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
        params = extract_generation_params(graphs)
    except (GenerationParamsError, RecursionError):
        # Defence in depth: a parser failure gives no card facts, never a 500.
        return None
    if isinstance(params, dict) and _inspector_names_another_seed(
        graphs, card_seed(params)
    ):
        return {**params, "seed": None}
    return params


_SEED_INPUTS = ("seed", "noise_seed", "noise")


def _stage_seed(graph: Mapping, node_id: str) -> str | None:
    """The seed the parser reads for one sampler node, or None when unknown.

    A literal seed input is that seed. A linked one is read by the parser
    itself when it treats the node as a sampler, with every other such node
    hidden from it (no ``positive`` input), so linked seeds resolve as they do
    on the card; any other linked seed is unknown.
    """
    node = graph.get(node_id)
    if not isinstance(node, Mapping) or not isinstance(node.get("inputs"), Mapping):
        return None
    inputs = node["inputs"]
    literal = next((inputs[k] for k in _SEED_INPUTS if k in inputs), None)
    if not isinstance(literal, (list, tuple)):
        # A literal needs no second parse (the cost is measured in TESTING.md).
        return card_seed({"seed": literal})
    if _is_sampler(node):
        only = {
            key: (
                {**other, "inputs": {**other["inputs"], "positive": None}}
                if key != node_id and _is_sampler(other)
                else other
            )
            for key, other in graph.items()
        }
        try:
            return card_seed(extract_generation_params({"prompt": only}))
        except (GenerationParamsError, RecursionError):
            return None
    return None


def _inspector_names_another_seed(graphs: dict, parsed: str | None) -> bool:
    """Whether the inspector shows another stage's seed than the parser's.

    True when the prompt graph's sampler stages read different seed inputs, the
    inspection resolution names one sampler for a resolved pair (the
    inspector's facts are then that stage's, D19), and that stage's seed is
    known and differs from ``parsed``. A seed that agrees is kept.
    """
    if parsed is None:
        return False
    graph = graphs.get("prompt")
    if not isinstance(graph, Mapping):
        return False
    try:
        samplers = get_semantics().samplers
    except SemanticsError:
        return False
    seeds = set()
    for node in graph.values():
        if not isinstance(node, Mapping) or not isinstance(node.get("inputs"), Mapping):
            continue
        if not isinstance(node.get("class_type"), str):
            continue
        if node["class_type"] not in samplers:
            continue
        inputs = node["inputs"]
        value = next((inputs[key] for key in _SEED_INPUTS if key in inputs), None)
        seeds.add(json.dumps(value, sort_keys=True, default=str))
    if len(seeds) < 2:
        return False
    try:
        resolution = resolve_prompt_pair(
            {"prompt": dict(graph)}, profile=INSPECTION_PROFILE
        )
    except RecursionError:
        return True
    named = resolution["sampler_id"]
    if resolution["state"] != "resolved" or named is None:
        return False
    seed = _stage_seed(graph, named)
    return seed is not None and seed != parsed


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
