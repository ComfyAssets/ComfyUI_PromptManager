"""Conservatively recover a prompt pair from one sampler or A1111 parameters.

``resolve_prompt_pair`` returns a dictionary with ``state``, ``positive``,
``negative``, ``sampler_id``, ``reason`` and ``candidates``. Each recovered side
contains effective ``text``, deduplicated-recipe ``storage_text`` and
``provenance``; ComfyUI provenance records the exact output
slot and complete followed link path. States distinguish a resolved pair (an
explicit empty negative is valid), absent data, unsupported/unresolved data and
ambiguous sampler selection. Unknown conditioning semantics are never flattened.

Future nodes can register declarative ``OutputAdapter`` entries by
``(class_type, output_slot)``. This does not prescribe a future paired-node schema.
Effective text uses a unique bounded execution record for traced paired nodes
when available; cached graph-only results remain explicitly inferred.
The resolver has no
ComfyUI, database or configuration dependencies; its only file read is the
inspection profile's node semantics table (``utils.inspection_semantics``).

Two ``ResolverProfile`` values exist. ``DEFAULT_PROFILE`` (ingestion: scan, live
monitor, admin import) keeps the historical behavior exactly, including the
500-node gate, and only adds ``reason_code: None``. ``INSPECTION_PROFILE`` bounds
work instead of graph size: a per-candidate visit allowance, a total visit budget
shared by every candidate and attribution walk, a node ceiling backstop, and
bounds on built provenance and text. It also names why a result is not resolved
(``reason_code``, and ``node`` for the first unsupported node), and a prompt graph
without a supported sampler is ``unresolved`` (``no-sampler``), never ``absent``.
When the semantics table cannot be loaded, inspection of a prompt graph is
``unresolved`` with a fixed reason; ingestion never loads the table.
"""

import logging
import re
from collections import deque
from dataclasses import dataclass, replace
from typing import Any, Dict, Mapping, Optional, Tuple

from .pair_execution import ExecutionEvidenceError, read_pair_execution

_LOGGER = logging.getLogger("prompt_manager.prompt_pair_resolver")


@dataclass(frozen=True)
class OutputAdapter:
    """A known output's text input, output kind, and optional PM composition.

    ``kind`` is ``conditioning`` or ``string``. Composition is opt-in and follows
    the existing PromptManager prepend/main/append rule. Adapters describe pure
    text provenance, not arbitrary executable transforms. ``storage_input`` keeps
    the resolved input as the recipe even without composition (negative editor).
    """

    input_name: str
    kind: str
    compose: bool = False
    storage_input: bool = False
    # Historical PromptManager classes (inspection only): ``comma`` strips the
    # text and joins prepend/append with ", " exactly as their code did;
    # ``strip`` only strips. ``default`` stands in for a missing optional input.
    comma: bool = False
    strip: bool = False
    default: Optional[str] = None


_SAMPLERS = frozenset({"KSampler", "KSamplerAdvanced", "SamplerCustom"})
_PROMPT_NODES = frozenset(
    {"PromptManager", "PromptManagerFocused", "PromptManagerSplit"}
)
_DEFAULT_ADAPTERS = {
    (name, slot): OutputAdapter("text", kind, True)
    for name in _PROMPT_NODES
    for slot, kind in ((0, "conditioning"), (1, "string"))
}
for _slot, _kind in ((0, "conditioning"), (2, "string")):
    _DEFAULT_ADAPTERS[("PromptManagerPair", _slot)] = OutputAdapter("text", _kind, True)
for _slot, _kind in ((1, "conditioning"), (3, "string")):
    _DEFAULT_ADAPTERS[("PromptManagerPair", _slot)] = OutputAdapter(
        "negative_text", _kind, storage_input=True
    )
_DEFAULT_ADAPTERS[("PromptManagerText", 0)] = OutputAdapter("text", "string", True)
_DEFAULT_ADAPTERS[("CLIPTextEncode", 0)] = OutputAdapter("text", "conditioning")
for _name in ("PrimitiveString", "PrimitiveStringMultiline"):
    _DEFAULT_ADAPTERS[(_name, 0)] = OutputAdapter("value", "string")
for _name in ("Text Multiline", "String Literal", "ShowText|pysssss"):
    _DEFAULT_ADAPTERS[(_name, 0)] = OutputAdapter("text", "string")
# This repository's node classes up to 07a5af3 (``prompt_manager_*.py``), still in
# saved workflows. Inspection only: the default profile keeps them unsupported.
_COMMA = OutputAdapter("text", "conditioning", comma=True)
_NEGATIVE = OutputAdapter("negative_text", "conditioning", strip=True, default="")
_HISTORICAL_ADAPTERS = {
    ("PromptManagerV2", 0): _COMMA,
    ("PromptManagerV2", 1): _NEGATIVE,
    ("PromptManagerV2", 2): replace(_COMMA, kind="string"),
    ("PromptManagerV2", 3): replace(_NEGATIVE, kind="string"),
    ("PromptManagerV2Text", 0): replace(_COMMA, kind="string"),
    ("PromptManagerV2Text", 1): replace(_NEGATIVE, kind="string"),
    ("PromptManagerNegative", 0): OutputAdapter("text", "conditioning", strip=True),
    ("PromptManagerNegative", 1): OutputAdapter("text", "string", strip=True),
    ("PromptManagerPositiveText", 0): replace(_COMMA, kind="string"),
    ("PromptManagerNegativeText", 0): replace(_COMMA, kind="string"),
}
for _name in ("PromptManagerPositive", "PromptManagerDetailer"):
    _HISTORICAL_ADAPTERS[(_name, 0)] = _COMMA
    _HISTORICAL_ADAPTERS[(_name, 1)] = replace(_COMMA, kind="string")


@dataclass(frozen=True)
class ResolverProfile:
    """Resolution bounds. ``legacy_node_gate`` set means the historical code path.

    ``max_path_entries`` and ``max_built_chars`` bound memory across the whole
    resolution (provenance entries and newly built text). ``max_scanned_units``
    bounds linear work across the whole resolution: input keys validated or
    scanned, characters stripped or joined, and provenance entries copied, each
    charged before the work is done. ``None`` disables each bound.
    ``semantics`` reads the declarative node table (inspection only; loaded
    lazily so the default profile never imports it). ``selection`` is reserved
    for a later slice and stays off.
    """

    name: str
    per_candidate_visits: int
    total_visits: int
    max_depth: int
    max_text_length: int
    node_ceiling: Optional[int]
    legacy_node_gate: Optional[int]
    semantics: bool = False
    selection: bool = False
    max_path_entries: Optional[int] = None
    max_built_chars: Optional[int] = None
    max_scanned_units: Optional[int] = None

    @property
    def legacy(self) -> bool:
        return self.legacy_node_gate is not None


DEFAULT_PROFILE = ResolverProfile(
    name="default",
    per_candidate_visits=500,
    total_visits=500,
    max_depth=64,
    max_text_length=100_000,
    node_ceiling=None,
    legacy_node_gate=500,
)
INSPECTION_PROFILE = ResolverProfile(
    name="inspection",
    per_candidate_visits=2_000,
    total_visits=20_000,
    max_depth=64,
    max_text_length=100_000,
    node_ceiling=20_000,
    legacy_node_gate=None,
    semantics=True,
    max_path_entries=64 * 20_000,
    max_built_chars=16 * 100_000,
    # About four passes over the 2 MiB role-record budget.
    max_scanned_units=8_000_000,
)
# Without the semantics table every sampler reads its pair directly.
_DIRECT_SAMPLER = {
    "strategy": "ksampler",
    "conditioning": {"positive": "positive", "negative": "negative"},
    "latent": "latent_image",
}
# Strategies whose nodes are prompt candidates. A detailer stage (D19) re-samples
# an upstream image: it is judged together with the samplers found in its image
# ancestry, and a sampler consumed only by detailers is not a candidate alone.
_CANDIDATE_STRATEGIES = frozenset({"ksampler", "custom-advanced", "detailer-stage"})
_DETAILER = "detailer-stage"
_GUIDER_ABSENT_REASON = "Guider has no negative input"
# ConditioningZeroOut discards its input text: an explicit empty prompt.
_ZEROED_SOURCE = "zeroed-conditioning"
# Runtime selector choices expanded per sampler (the spec's candidate cap).
_MAX_BRANCH_CANDIDATES = 32
# Statically undecidable text (a search, a list output): never pick an item.
_RUNTIME_REASON = "Text is decided at run time"
# Uncoded outcomes the inspector explains by their exact reason.
EMPTY_POSITIVE_REASON = "Positive prompt is empty"
MULTISTAGE_UNATTRIBUTED_REASON = "Multistage sampler ancestry is ambiguous"
_INCOMPATIBLE_SLOT = "Output slot has an incompatible type"
# Before 3.2.3 a save patch overwrote every PromptManager node's saved ``text``
# with the last-run node's prompt (comfy-metadata.js ``createContext``).
OVERWRITTEN_PROMPT_REASON = "Saved PromptManager text was overwritten by another node"
# Trace outcomes that leave an input unverifiable against execution evidence.
_UNVERIFIABLE_CODES = frozenset({"unsupported-node", "runtime-value"})
# JoinStringMulti's own ``inputcount`` range; anything else never executed.
_MAX_JOIN_INPUTS = 1000
_SAMPLER_LIKE_INPUTS = ("latent_image", "guider", "positive", "negative")
# The stage-walk input lists (``stage_inputs``) live in the semantics table; the
# default profile never reads them.
_NO_SAMPLER_NAMES = 3
_CLASS_NAME_LENGTH = 64


class _Unresolved(Exception):
    """A non-resolved outcome; ``code`` is the closed machine vocabulary."""

    def __init__(
        self, message: str, code: Optional[str] = None, node: Optional[dict] = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.node = node


class _Diverging(Exception):
    """A runtime selector whose branches trace to different texts.

    Raised from inside tracing; ``_GraphResolver.branches`` resolves the sampler
    again once per choice, so each candidate is internally consistent.
    """

    def __init__(self, key: Tuple[str, int]) -> None:
        super().__init__(key)
        # The condition output (node ID, slot): one runtime value, so every
        # selector it drives is forced together.
        self.key = key


def _node_ref(node_id: str, class_type: Any) -> dict:
    name = class_type[:_CLASS_NAME_LENGTH] if isinstance(class_type, str) else None
    return {"id": node_id[:_CLASS_NAME_LENGTH], "class_type": name}


class _Budget:
    """Visits shared by every candidate, plus a per-candidate allowance.

    With ``total`` equal to the allowance (the default profile) this is exactly
    the historical single counter, so the reset per sampler is a no-op there.
    """

    def __init__(self, total: int) -> None:
        self.total = total
        self.allowance = total

    def reset(self, allowance: int) -> None:
        self.allowance = allowance

    @property
    def remaining(self) -> int:
        return min(self.total, self.allowance)

    def spend(self) -> None:
        # Checked before decrementing, so an exhausted allowance costs no total.
        if self.total <= 0 or self.allowance <= 0:
            raise _Unresolved("Resolution visit limit reached", "work-limit")
        self.total -= 1
        self.allowance -= 1

    def charge(self, cost: int) -> None:
        """Charge a memoized subtree's visit cost to this candidate only."""
        if cost > self.allowance:
            raise _Unresolved("Resolution visit limit reached", "work-limit")
        self.allowance -= cost


def _link(value: Any) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) == 2
        and isinstance(value[0], (str, int))
        and not isinstance(value[0], bool)
        and isinstance(value[1], int)
        and not isinstance(value[1], bool)
        and value[1] >= 0
    )


def _result(
    state: str,
    positive: Optional[dict] = None,
    negative: Optional[dict] = None,
    sampler_id: Optional[str] = None,
    reason: str = "",
    candidates: Optional[list] = None,
    reason_code: Optional[str] = None,
    node: Optional[dict] = None,
) -> Dict[str, Any]:
    return {
        "state": state,
        "positive": positive,
        "negative": negative,
        "sampler_id": sampler_id,
        "reason": reason,
        "candidates": candidates or [],
        "reason_code": reason_code,
        "node": node,
    }


def _role_text(side: Any) -> Optional[str]:
    return side["text"] if isinstance(side, dict) else None


def _outcome_key(result: Dict[str, Any]) -> tuple:
    """What a reader sees of a result: state and the two role texts."""
    return (
        result["state"],
        _role_text(result["positive"]),
        _role_text(result["negative"]),
    )


def _legacy_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """The default profile's wire: today's keys plus ``reason_code: None``."""
    legacy = {key: value for key, value in result.items() if key != "node"}
    legacy["reason_code"] = None
    legacy["candidates"] = [_legacy_result(item) for item in result["candidates"]]
    return legacy


class _GraphResolver:
    def __init__(
        self,
        graph: dict,
        adapters: Mapping[Tuple[str, int], OutputAdapter],
        max_visits: int,
        max_depth: int,
        max_text_length: int,
        metadata: Optional[dict] = None,
        *,
        budget: Optional[_Budget] = None,
        profile: ResolverProfile = DEFAULT_PROFILE,
        semantics: Optional[Any] = None,
        overwritten: frozenset = frozenset(),
    ) -> None:
        # ``max_visits`` is the per-candidate allowance; ``profile`` supplies only
        # the path selection and whole-resolution bounds. ``resolve_text_input``
        # relies on the legacy default profile here.
        self.graph = graph
        historical = _HISTORICAL_ADAPTERS if semantics is not None else {}
        self.adapters = {**_DEFAULT_ADAPTERS, **historical, **adapters}
        # Nodes whose saved text is not their own (inspection; R1).
        self.overwritten = overwritten
        self.per_candidate_visits = max_visits
        self.budget = budget if budget is not None else _Budget(max_visits)
        self.budget.reset(max_visits)
        self.legacy = profile.legacy
        self.path_entries = profile.max_path_entries
        self.built_chars = profile.max_built_chars
        self.scanned = profile.max_scanned_units
        self.checked: dict = {}
        # Unfolded visit cost of each memo entry (inspection): a memo hit charges
        # it to the current candidate, so each candidate's outcome is independent
        # of tracing order while the shared work is done only once.
        self.costs: dict = {}
        # Height of each memo entry (deepest depth reached below it, relative to
        # its own depth): a hit raises ``depth-limit`` exactly where a fresh walk
        # from the reusing depth would, so depth too is independent of order.
        self.heights: dict = {}
        self.deepest = 0
        self.max_depth = max_depth
        self.max_text_length = max_text_length
        self.memo: dict = {}
        self.active: set = set()
        self.metadata = metadata or {}
        self.text_sources: dict = {}
        # Forced runtime selector choices (inspection); a result traced through
        # one is "tainted" and never memoized, since it depends on the choice.
        self.choices: dict = {}
        self.tainted = False
        # Latent-walk outcome per sampler (inspection): ``(error or None, visit
        # cost)``. It never depends on selector choices, so one walk serves every
        # branch run; ``latent_charged`` holds the samplers already paid for by
        # the current candidate.
        self.latent_walks: dict = {}
        self.latent_charged: set = set()
        # ``semantics`` (inspection) names the samplers and guiders it follows.
        self.semantics = semantics
        if semantics is None:
            self.sampler_classes = self.stage_classes = _SAMPLERS
        else:
            self.sampler_classes = frozenset(
                name
                for name, entry in semantics.samplers.items()
                if entry["strategy"] in _CANDIDATE_STRATEGIES
            )
            # Any listed sampler upstream in latent ancestry is an earlier stage.
            self.stage_classes = frozenset(semantics.samplers)

    def node(self, node_id: str) -> Tuple[str, dict]:
        if self.legacy:
            return self.checked_node(node_id)
        if node_id not in self.checked:
            try:
                self.checked[node_id] = self.checked_node(node_id)
            except _Unresolved as error:
                if error.code == "work-limit":
                    raise
                self.checked[node_id] = error
        checked = self.checked[node_id]
        if isinstance(checked, _Unresolved):
            raise _Unresolved(str(checked), checked.code, checked.node)
        return checked

    def checked_node(self, node_id: str) -> Tuple[str, dict]:
        node = self.graph.get(node_id)
        if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
            raise _Unresolved("Missing or malformed graph node", "malformed")
        self.scan(len(node["inputs"]))
        if any(not isinstance(key, str) for key in node["inputs"]):
            raise _Unresolved("Malformed node input names", "malformed")
        return str(node.get("class_type", "")), node["inputs"]

    def scan(self, units: int) -> None:
        """Charge linear work before doing it (inspection; no-op when legacy)."""
        if self.scanned is not None:
            if units > self.scanned:
                self.scanned = -1
                raise _Unresolved("Resolution work limit reached", "work-limit")
            self.scanned -= units

    def unsupported(self, message: str, node_id: str) -> _Unresolved:
        return self.named(message, "unsupported-node", node_id)

    def named(self, message: str, code: str, node_id: str) -> _Unresolved:
        """An outcome that names the node it is about (bounded class name)."""
        node = self.graph.get(node_id)
        class_type = node.get("class_type") if isinstance(node, dict) else None
        if isinstance(class_type, str):
            class_type = class_type[:_CLASS_NAME_LENGTH]
        return _Unresolved(message, code, _node_ref(node_id, class_type))

    def bounded(self, text: str) -> str:
        if len(text) > self.max_text_length:
            raise _Unresolved("Resolved text exceeds the length limit", "text-limit")
        return text

    def built(self, text: str) -> str:
        """Charge newly built text against the profile's whole-resolution bound."""
        if self.built_chars is not None:
            self.built_chars -= len(text)
            if self.built_chars < 0:
                raise _Unresolved("Resolution work limit reached", "work-limit")
        return text

    def text(self, value: Any, kind: str, depth: int = 0) -> Tuple[str, list, str]:
        if not self.legacy:
            self.deepest = max(self.deepest, depth)
        if depth >= self.max_depth:
            raise _Unresolved("Resolution depth limit reached", "depth-limit")
        if isinstance(value, str) and kind == "string":
            return self.bounded(value), [], value
        if not _link(value):
            raise _Unresolved("Expected a typed graph link or text input", "malformed")
        node_id, slot = str(value[0]), value[1]
        key = (node_id, slot, kind)
        if key in self.active:
            raise _Unresolved("Cyclic prompt graph", "malformed")
        if key in self.memo:
            if not self.legacy:
                height = self.heights[key]
                if depth + height >= self.max_depth:
                    raise _Unresolved("Resolution depth limit reached", "depth-limit")
                self.budget.charge(self.costs[key])
                self.deepest = max(self.deepest, depth + height)
            return self.memo[key]
        allowance = self.budget.allowance
        outer_deepest = self.deepest
        outer_tainted, self.tainted = self.tainted, False
        self.deepest = depth
        try:
            self.budget.spend()
            name, inputs = self.node(node_id)
            self.scan(len(inputs))
            if node_id in self.overwritten:
                raise _Unresolved(OVERWRITTEN_PROMPT_REASON, "malformed")
            self.active.add(key)
            if name == "Reroute" and slot == 0:
                keys = [key for key in ("value", "input", "in") if key in inputs]
                if len(keys) != 1:
                    raise _Unresolved(
                        "Reroute input is not uniquely identifiable", "malformed"
                    )
                text, path, storage_text = self.text(inputs[keys[0]], kind, depth + 1)
                if self.semantics is not None:
                    self.inherit_source(node_id, slot, [path])
            elif (name, slot) in self.adapters:
                adapter = self.adapters[(name, slot)]
                if not isinstance(adapter, OutputAdapter) or adapter.kind != kind:
                    raise _Unresolved(_INCOMPATIBLE_SLOT, "malformed")
                evidence = (
                    self.execution_record(node_id, inputs)
                    if name == "PromptManagerPair"
                    else None
                )
                if evidence is not None:
                    text = evidence[
                        (
                            "positive_text"
                            if adapter.input_name == "text"
                            else "negative_effective_text"
                        )
                    ]
                    storage_text = evidence[adapter.input_name]
                    path = []
                    self.text_sources[(node_id, slot)] = "execution"
                elif adapter.comma or adapter.strip:
                    text, path, storage_text = self.historical(adapter, inputs, depth)
                else:
                    text, path, storage_text = self.text(
                        inputs.get(adapter.input_name), "string", depth + 1
                    )
                    if adapter.compose or adapter.storage_input:
                        storage_text = text
                    if adapter.compose:
                        before, before_path, _ = self.text(
                            inputs.get("prepend_text", ""), "string", depth + 1
                        )
                        after, after_path, _ = self.text(
                            inputs.get("append_text", ""), "string", depth + 1
                        )
                        self.scan(
                            len(before)
                            + len(after)
                            + len(before_path)
                            + len(path)
                            + len(after_path)
                        )
                        text = self.built(
                            self.bounded(
                                " ".join(
                                    part
                                    for part in (before.strip(), text, after.strip())
                                    if part
                                )
                            )
                        )
                        path = before_path + path + after_path
            elif (
                name in ("StringConcatenate", "Text Concatenate")
                and slot == 0
                and kind == "string"
            ):
                if name == "StringConcatenate":
                    keys = ["string_a", "string_b"]
                    delimiter = inputs.get("delimiter", "")
                    clean = False
                else:
                    keys = sorted(
                        key for key in inputs if re.fullmatch(r"text_[a-z]", key)
                    )
                    delimiter = inputs.get("delimiter", " ")
                    clean = (
                        str(inputs.get("clean_whitespace", "true")).lower() == "true"
                    )
                if not keys or not isinstance(delimiter, str):
                    raise _Unresolved("Malformed concatenation", "malformed")
                parts, path, size = [], [], 0
                for input_name in keys:
                    part, followed, _ = self.text(
                        inputs.get(input_name, ""), "string", depth + 1
                    )
                    # Charged before stripping: stripped-away text is work too.
                    self.scan(len(part) + len(followed))
                    if clean:
                        part = part.strip()
                    path.extend(followed)
                    if part or name == "StringConcatenate":
                        size += len(part) + (len(delimiter) if parts else 0)
                        if size > self.max_text_length:
                            raise _Unresolved(
                                "Resolved text exceeds the length limit", "text-limit"
                            )
                        parts.append(part)
                text = self.built(delimiter.join(parts))
                storage_text = text
            elif self.semantics is not None and self.follows(name, slot, kind):
                text, path, storage_text = self.semantic(
                    node_id, name, slot, kind, inputs, depth
                )
            else:
                raise self.unsupported(
                    "Unsupported node semantics or output slot", node_id
                )
            # Memoized paths stay bounded even for diamond-shaped string graphs.
            self.scan(len(path) + 1)
            path = [{"node_id": node_id, "output_slot": slot}] + path
            if self.legacy:
                path_limit = self.max_depth * max(1, len(self.graph))
            else:
                path_limit = self.max_depth * max(1, self.per_candidate_visits)
            if self.path_entries is not None:
                self.path_entries -= len(path)
            if len(path) > path_limit or (
                self.path_entries is not None and self.path_entries < 0
            ):
                raise _Unresolved("Provenance path limit reached", "work-limit")
            if not self.tainted:
                self.memo[key] = (text, path, storage_text)
                self.costs[key] = allowance - self.budget.allowance
                self.heights[key] = self.deepest - depth
            return text, path, storage_text
        finally:
            self.deepest = max(outer_deepest, self.deepest)
            self.tainted = outer_tainted or self.tainted
            self.active.discard(key)

    def historical(
        self, adapter: OutputAdapter, inputs: dict, depth: int
    ) -> Tuple[str, list, str]:
        """``prompt_manager_*.py`` at 07a5af3: strip, then join with ", ".

        Prepend and append are stripped and skipped when empty; the separator is
        left out when either side is empty or already ends with a comma. The
        stripped main text is the recipe.
        """
        text, path, _ = self.text(
            inputs.get(adapter.input_name, adapter.default), "string", depth + 1
        )
        self.scan(len(text))
        text = text.strip()
        storage_text = text
        if adapter.comma:
            before, before_path, _ = self.text(
                inputs.get("prepend_text", ""), "string", depth + 1
            )
            after, after_path, _ = self.text(
                inputs.get("append_text", ""), "string", depth + 1
            )
            self.scan(len(before) + len(after) + len(before_path) + len(after_path))
            before, after = before.strip(), after.strip()
            if before:
                joint = ", " if text and not before.endswith(",") else ""
                text = f"{before}{joint}{text}"
            if after:
                joint = ", " if text and not text.endswith(",") else ""
                text = f"{text}{joint}{after}"
            text = self.built(self.bounded(text))
            path = before_path + path + after_path
        return text, path, storage_text

    def follows(self, name: str, slot: int, kind: str) -> bool:
        """Whether the semantics table traces this output (inspection only)."""
        semantics = self.semantics
        if name in semantics.runtime:
            return True
        if (name, slot) in semantics.encoders or (name, slot) in semantics.strings:
            return True
        if slot == 0 and name in semantics.selectors:
            return True
        return kind == "conditioning" and (
            (name, slot) in semantics.conditioning
            or (slot == 0 and name in semantics.composites)
        )

    def semantic(
        self, node_id: str, name: str, slot: int, kind: str, inputs: dict, depth: int
    ) -> Tuple[str, list, str]:
        """Trace a table-listed output: runtime, encoder, string, pass-through,
        zeroed, selector or composite.

        The caller (``text``) prepends this node to the returned path.
        """
        semantics = self.semantics
        if name in semantics.runtime:
            raise self.named(_RUNTIME_REASON, "runtime-value", node_id)
        for table, expected, build in (
            (semantics.encoders, "conditioning", self.encoded),
            (semantics.strings, "string", self.stringed),
        ):
            listed = table.get((name, slot))
            if listed is not None:
                if kind != expected:
                    raise _Unresolved(_INCOMPATIBLE_SLOT, "malformed")
                return build(node_id, listed, inputs, depth)
        entry = (
            semantics.conditioning.get((name, slot)) if kind == "conditioning" else None
        )
        if entry is not None and entry["strategy"] == "zeroed":
            # The upstream text is discarded, so it is not traced.
            self.text_sources[(node_id, slot)] = _ZEROED_SOURCE
            return "", [], ""
        if entry is not None:  # passthrough: one input carries the text
            traced = self.text(inputs.get(entry["input"]), kind, depth + 1)
            self.inherit_source(node_id, slot, [traced[1]])
            return traced
        if name in semantics.selectors:
            return self.select(node_id, semantics.selectors[name], inputs, kind, depth)
        parts = [
            self.text(inputs.get(input_name), kind, depth + 1)
            for input_name in semantics.composites[name]["inputs"]
        ]
        if len({part[0] for part in parts}) != 1:
            # Several texts are in effect at once: not one sendable prompt.
            raise self.named(
                "Combined conditioning has different texts", "composite", node_id
            )
        return self.merged(node_id, slot, parts)

    def select(
        self, node_id: str, entry: Mapping, inputs: dict, kind: str, depth: int
    ) -> Tuple[str, list, str]:
        """A literal condition picks one branch; a linked one is a runtime choice.

        A runtime choice traces both branches. Equal texts converge; different
        texts raise ``_Diverging`` unless this choice is already forced.
        """
        condition = inputs.get(entry["condition"])
        branches = entry["branches"]
        if type(condition) is bool:
            choice: Optional[bool] = condition
        elif not _link(condition):
            raise _Unresolved("Malformed selector condition", "malformed")
        elif (str(condition[0]), condition[1]) in self.choices:
            choice = self.choices[(str(condition[0]), condition[1])]
            self.tainted = True
        else:
            choice = None
        if choice is not None:
            branch = branches["true" if choice else "false"]
            traced = self.text(inputs.get(branch), kind, depth + 1)
            self.inherit_source(node_id, 0, [traced[1]])
            return traced
        parts = [
            self.text(inputs.get(branches[label]), kind, depth + 1)
            for label in ("true", "false")
        ]
        if parts[0][0] != parts[1][0]:
            raise _Diverging((str(condition[0]), condition[1]))
        return self.merged(node_id, 0, parts)

    def merged(self, node_id: str, slot: int, parts: list) -> Tuple[str, list, str]:
        """One text reached through several followed paths (deduplicated)."""
        text, merged, storage_text = self.joined_paths(parts, parts)
        self.inherit_source(node_id, slot, [part[1] for part in parts])
        return text, merged, storage_text

    def joined_paths(self, chosen: list, parts: list) -> Tuple[str, list, str]:
        """``chosen``'s one text with every part's followed path, deduplicated."""
        text = chosen[0][0]
        storages = {part[2] for part in chosen}
        storage_text = chosen[0][2] if len(storages) == 1 else text
        paths = [part[1] for part in parts]
        self.scan(sum(len(path) for path in paths))
        merged, seen = [], set()
        for path in paths:
            for step in path:
                identity = (step["node_id"], step["output_slot"])
                if identity not in seen:
                    seen.add(identity)
                    merged.append(step)
        return text, merged, storage_text

    def encoded(
        self, node_id: str, entry: Mapping, inputs: dict, depth: int
    ) -> Tuple[str, list, str]:
        """A text encoder's prompt (``single`` or ``parts-equal``).

        ``single`` reads one string input unchanged; image, VAE and other inputs
        are not text. ``parts-equal`` reads every listed part: identical parts
        are that text, exactly one non-empty part (exact ``""`` emptiness) is
        that text, and anything else is several texts in effect at once
        (``composite``, D6). Like ``CLIPTextEncode``, an encoder's text source
        stays ``graph-inferred``.
        """
        names = entry["inputs"]
        if entry["strategy"] == "single":
            return self.text(inputs.get(names[0]), "string", depth + 1)
        if entry["strategy"] == "flag-parts":
            return self.flagged(node_id, entry, inputs, depth)
        if entry["strategy"] != "parts-equal":
            raise self.unsupported("Unsupported encoder strategy", node_id)
        parts = [self.text(inputs.get(name), "string", depth + 1) for name in names]
        # Comparing the parts reads every character once.
        self.scan(sum(len(part[0]) for part in parts))
        if len({part[0] for part in parts}) == 1:
            return self.joined_paths(parts, parts)
        filled = [part for part in parts if part[0] != ""]
        if len(filled) == 1:
            return self.joined_paths(filled, parts)
        raise self.named("Encoder parts have different texts", "composite", node_id)

    def flagged(
        self, node_id: str, entry: Mapping, inputs: dict, depth: int
    ) -> Tuple[str, list, str]:
        """smZ CLIPTextEncode: ``text`` unless its SDXL flag may apply.

        With the literal flag false the first input is encoded. With it true,
        the connected CLIP's model class decides at run time which inputs are
        read, so the prompt is known only when every input is the same text.
        """
        names = entry["inputs"]
        if not self.setting(node_id, inputs, entry["params"]["flag"], bool):
            return self.text(inputs.get(names[0]), "string", depth + 1)
        parts = [self.text(inputs.get(name), "string", depth + 1) for name in names]
        self.scan(sum(len(part[0]) for part in parts))
        if len({part[0] for part in parts}) == 1:
            return self.joined_paths(parts, parts)
        raise self.named(_RUNTIME_REASON, "runtime-value", node_id)

    def stringed(
        self, node_id: str, entry: Mapping, inputs: dict, depth: int
    ) -> Tuple[str, list, str]:
        """A string source: ``literal``, ``strip-newlines`` or ``join-multi``.

        Each reproduces the pinned node's code exactly, and only from literal
        settings; a linked setting is never guessed.
        """
        strategy = entry["strategy"]
        source = entry["inputs"][0]
        if strategy == "literal":
            return self.text(inputs.get(source), "string", depth + 1)
        if strategy == "strip-newlines":
            # KJNodes StringConstantMultiline: replace("\n", "").strip().
            flag = self.setting(node_id, inputs, entry["params"]["flag"], bool)
            text, path, storage_text = self.text(
                inputs.get(source), "string", depth + 1
            )
            if not flag:
                return text, path, storage_text
            self.scan(len(text))
            text = self.built(self.bounded(text.replace("\n", "").strip()))
            return text, path, text
        if strategy == "join-multi":
            return self.join_multi(node_id, entry, inputs, depth)
        raise self.unsupported("Unsupported string strategy", node_id)

    def setting(self, node_id: str, inputs: dict, name: str, kind: type) -> Any:
        """A literal node setting; linked is ``unsupported-node``, else malformed."""
        if name not in inputs:
            raise _Unresolved("Missing string node setting", "malformed")
        value = inputs[name]
        if _link(value):
            raise self.unsupported("Linked string node setting", node_id)
        if type(value) is not kind:
            raise _Unresolved("Malformed string node setting", "malformed")
        return value

    def join_multi(
        self, node_id: str, entry: Mapping, inputs: dict, depth: int
    ) -> Tuple[str, list, str]:
        """KJNodes JoinStringMulti with a literal ``return_list`` of false.

        ``string_1`` is always kept (even empty); ``string_2`` .. ``string_N``
        (``N`` = ``inputcount``) are appended after the literal delimiter unless
        missing or exactly ``""``; inputs past ``N`` are ignored. A list output
        (or a linked flag) is decided at run time.
        """
        params = entry["params"]
        flag = params["list_flag"]
        if flag not in inputs:
            raise _Unresolved("Missing string node setting", "malformed")
        if _link(inputs[flag]) or inputs[flag] is True:
            raise self.named(_RUNTIME_REASON, "runtime-value", node_id)
        if inputs[flag] is not False:
            raise _Unresolved("Malformed string node setting", "malformed")
        count = self.setting(node_id, inputs, params["count"], int)
        delimiter = self.setting(node_id, inputs, params["delimiter"], str)
        if not 1 <= count <= _MAX_JOIN_INPUTS:
            raise _Unresolved("Malformed string node setting", "malformed")
        self.scan(count)
        names = [entry["inputs"][0]] + [
            f"{params['input_prefix']}{index}" for index in range(2, count + 1)
        ]
        parts: list = []
        path: list = []
        seen: set = set()
        size = 0
        for position, input_name in enumerate(names):
            if position and input_name not in inputs:
                continue
            part, followed, _ = self.text(inputs.get(input_name), "string", depth + 1)
            self.scan(len(part) + len(followed))
            for step in followed:  # one link read by several parts is listed once
                identity = (step["node_id"], step["output_slot"])
                if identity not in seen:
                    seen.add(identity)
                    path.append(step)
            if position and not part:
                continue
            size += len(part) + (len(delimiter) if parts else 0)
            if size > self.max_text_length:
                raise _Unresolved(
                    "Resolved text exceeds the length limit", "text-limit"
                )
            parts.append(part)
        text = self.built(delimiter.join(parts))
        return text, path, text

    def source_of(self, path: list) -> str:
        if not path:
            return "graph-inferred"
        origin = path[0]
        return self.text_sources.get(
            (origin["node_id"], origin["output_slot"]), "graph-inferred"
        )

    def inherit_source(self, node_id: str, slot: int, paths: list) -> None:
        """A forwarding node reports its input's text source when unambiguous."""
        sources = {self.source_of(path) for path in paths}
        if len(sources) == 1:
            self.text_sources[(node_id, slot)] = sources.pop()
        else:
            self.text_sources.pop((node_id, slot), None)

    def unverifiable(self, error: _Unresolved) -> bool:
        """Whether a failed input trace only means "cannot be read statically".

        Such an input is not checked against a pair's execution record. The
        default profile keeps its historical message test; inspection decides
        by code, so every unsupported or run-time source qualifies.
        """
        if self.semantics is None:
            return str(error) == "Unsupported node semantics or output slot"
        return error.code in _UNVERIFIABLE_CODES

    def execution_record(self, node_id: str, inputs: dict) -> Optional[dict]:
        try:
            record = read_pair_execution(self.metadata, node_id)
        except ExecutionEvidenceError as error:
            raise _Unresolved(str(error), "malformed") from error
        if record is None:
            return None
        for name in ("text", "negative_text", "prepend_text", "append_text"):
            try:
                expected = self.text(inputs.get(name, ""), "string")[0]
            except _Unresolved as error:
                if self.unverifiable(error):
                    value = inputs.get(name)
                    if _link(value):
                        source_name, _ = self.node(str(value[0]))
                        known_slots = {
                            slot
                            for (kind, slot) in self.adapters
                            if kind == source_name
                        }
                        if known_slots and value[1] not in known_slots:
                            raise
                        if (
                            source_name
                            in ("StringConcatenate", "Text Concatenate", "Reroute")
                            and value[1] != 0
                        ):
                            raise
                    continue
                raise
            if name in ("prepend_text", "append_text"):
                expected = expected.strip()
            if expected != record[name]:
                raise _Unresolved(
                    "Execution evidence does not match recorded node inputs",
                    "malformed",
                )
        return record

    def side(
        self,
        value: Any,
        sampler_id: str,
        guider_id: Optional[str] = None,
        depth: int = 0,
    ) -> dict:
        text, path, storage_text = self.text(value, "conditioning", depth)
        origin = path[0]
        provenance = {
            "format": "comfyui",
            "sampler_id": sampler_id,
            **origin,
            "path": path,
            "text_source": self.text_sources.get(
                (origin["node_id"], origin["output_slot"]), "graph-inferred"
            ),
        }
        if guider_id is not None:
            # The conditioning path starts below the guider the sampler reads.
            provenance["guider_id"] = guider_id
        return {"text": text, "storage_text": storage_text, "provenance": provenance}

    def guider(self, value: Any) -> Tuple[str, dict, Mapping, str, int]:
        """Follow a sampler's ``guider`` link (through Reroutes) to a listed guider.

        Returns the guider's ID and inputs, its conditioning input names, the
        reason used when it has no negative input, and the depth reached. Each
        hop draws a visit; an unlisted guider class is ``unsupported-node``.
        """
        seen: set = set()
        depth = 0
        while True:
            if depth >= self.max_depth:
                raise _Unresolved("Resolution depth limit reached", "depth-limit")
            if not _link(value):
                raise _Unresolved("Expected a guider link", "malformed")
            node_id, slot = str(value[0]), value[1]
            if node_id in seen:
                raise _Unresolved("Cyclic guider ancestry", "malformed")
            seen.add(node_id)
            self.budget.spend()
            name, inputs = self.node(node_id)
            self.scan(len(inputs))
            depth += 1
            if name == "Reroute" and slot == 0:
                keys = [key for key in ("value", "input", "in") if key in inputs]
                if len(keys) != 1:
                    raise _Unresolved(
                        "Reroute input is not uniquely identifiable", "malformed"
                    )
                value = inputs[keys[0]]
                continue
            entry = self.semantics.guiders.get(name)
            if entry is None or slot != 0:
                raise self.unsupported("Unsupported guider", node_id)
            conditioning = {
                "positive": entry["positive"],
                "negative": entry.get("negative"),
            }
            reason = entry.get("absent_reason", _GUIDER_ABSENT_REASON)
            return node_id, inputs, conditioning, reason, depth

    def sampler_entry(self, name: str) -> Mapping:
        if self.semantics is None:
            return _DIRECT_SAMPLER
        return self.semantics.samplers[name]

    def pair(self, sampler_id: str) -> dict:
        self.budget.reset(self.per_candidate_visits)
        self.latent_charged = set()
        if not self.legacy and self.budget.total <= 0:
            return _result(
                "unresolved",
                sampler_id=sampler_id,
                reason="Resolution visit limit reached",
                reason_code="work-limit",
            )
        if self.semantics is None:
            return self.sides(sampler_id)
        return self.branches(sampler_id)

    def stage(self, sampler_id: str, later: frozenset) -> dict:
        """One sampler's result; a detailer is compared with its upstream stages.

        ``later`` holds the detailers already being judged below this one, so a
        cyclic stage ancestry is malformed instead of recursing, and a chain
        deeper than ``max_depth`` stages is ``depth-limit``.
        """
        if len(later) >= self.max_depth:
            return _result(
                "unresolved",
                sampler_id=sampler_id,
                reason="Resolution depth limit reached",
                reason_code="depth-limit",
            )
        own = self.sides(sampler_id)
        if own["state"] == "unresolved":
            return own
        name, inputs = self.node(sampler_id)
        entry = self.semantics.samplers.get(name)
        if entry is None or entry["strategy"] != _DETAILER:
            return own
        try:
            upstream = self.upstream_stages(
                sampler_id, inputs.get(entry["image_stage"])
            )
            if any(node_id in later for node_id in upstream):
                raise _Unresolved("Cyclic sampler stage ancestry", "malformed")
        except _Unresolved as error:
            return _result(
                "unresolved",
                sampler_id=sampler_id,
                reason=str(error),
                reason_code=error.code,
                node=error.node,
            )
        if not upstream:
            return own
        later = later | {sampler_id}
        stages = [self.stage(node_id, later) for node_id in upstream]
        blocked = next((item for item in stages if item["state"] == "unresolved"), None)
        if blocked is not None:
            # A stage that cannot be read cannot be compared: never pick one.
            return _result(
                "unresolved",
                sampler_id=sampler_id,
                reason=blocked["reason"],
                reason_code=blocked["reason_code"],
                node=blocked["node"],
            )
        flat: list = []
        for item in [own] + stages:
            flat.extend(item["candidates"] if item["state"] == "ambiguous" else [item])
        if len({_outcome_key(item) for item in flat}) == 1:
            return own
        return _result(
            "ambiguous",
            sampler_id=sampler_id,
            reason="Sampler stages have different prompts",
            candidates=flat,
            reason_code="multistage-differs",
        )

    @staticmethod
    def walk_from(value: list) -> Tuple[deque, set]:
        """A breadth-first queue holding one start link, and its queued IDs."""
        start = str(value[0])
        return deque([(start, 0)]), {start}

    def enqueue(self, queue: deque, queued: set, inputs: dict, depth: int) -> None:
        """Queue each linked input once per walk; every edge is charged as scanned.

        Deduplicating at enqueue keeps a dense shared fan-in to one entry per
        node, so the queue never outgrows the nodes the visit budget pays for.
        Breadth-first, a node's first enqueue is at its least depth, as before.
        """
        self.scan(len(inputs))
        for item in inputs.values():
            if _link(item):
                key = str(item[0])
                if key not in queued:
                    queued.add(key)
                    queue.append((key, depth))

    def stage_shaped(self, name: str, inputs: dict) -> bool:
        """An unlisted node that may itself be a sampler stage."""
        if name in self.semantics.all_classes:
            return False
        stage_inputs = self.semantics.stage_inputs
        if any(_link(inputs.get(key)) for key in stage_inputs["shaped"]):
            return True
        return _link(inputs.get("model")) and any(
            _link(inputs.get(key)) for key in stage_inputs["with_model"]
        )

    def upstream_stages(self, origin: str, value: Any) -> list:
        """Samplers reached from a detailer's image input, in breadth-first order.

        Every link input of non-sampler nodes is walked (decoders, upscalers,
        compositors), stopping at listed samplers. Each node draws a visit.
        """
        if not _link(value):
            raise _Unresolved("Expected an image link", "malformed")
        found: list = []
        queue, queued = self.walk_from(value)
        while queue:
            node_id, depth = queue.popleft()
            if node_id == origin:
                raise _Unresolved("Cyclic sampler stage ancestry", "malformed")
            if depth >= self.max_depth:
                raise _Unresolved("Resolution depth limit reached", "depth-limit")
            self.budget.spend()
            name, inputs = self.node(node_id)
            if name in self.stage_classes:
                found.append(node_id)
                continue
            if self.stage_shaped(name, inputs):
                raise self.unsupported("Unsupported sampler stage ancestry", node_id)
            self.enqueue(queue, queued, inputs, depth + 1)
        return found

    def final_stages(self, sampler_ids: list) -> list:
        """Drop samplers that only feed detailers: each is judged in that chain.

        A sampler whose output also reaches a terminal node other than a later
        stage or a preview stays a candidate of its own (the file may be its
        output). Any walk that fails or runs out of budget absorbs nothing.
        """
        detailers = [
            node_id
            for node_id in sampler_ids
            if self.semantics.samplers[self.graph[node_id]["class_type"]]["strategy"]
            == _DETAILER
        ]
        if not detailers:
            return sampler_ids
        try:
            consumers = self.consumers()
        except _Unresolved:
            return sampler_ids
        absorbed: set = set()
        for detailer in detailers:
            self.budget.reset(self.per_candidate_visits)
            try:
                name, inputs = self.node(detailer)
                image = inputs.get(self.semantics.samplers[name]["image_stage"])
                for node_id in self.upstream_stages(detailer, image):
                    if not self.reaches_output(node_id, consumers):
                        absorbed.add(node_id)
            except _Unresolved:
                continue
        kept = [node_id for node_id in sampler_ids if node_id not in absorbed]
        return kept or sampler_ids

    def consumers(self) -> dict:
        """Reverse links: node ID to the IDs of nodes reading one of its outputs."""
        consumers: dict = {}
        for node_id, node in self.graph.items():
            if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
                continue
            self.scan(len(node["inputs"]))
            for value in node["inputs"].values():
                if _link(value):
                    consumers.setdefault(str(value[0]), []).append(node_id)
        return consumers

    def reaches_output(self, node_id: str, consumers: dict) -> bool:
        """Whether a sampler's output reaches a terminal node besides later stages."""
        seen, queue = {node_id}, deque([node_id])
        while queue:
            current = queue.popleft()
            for consumer in consumers.get(current, ()):
                if consumer in seen:
                    continue
                seen.add(consumer)
                self.budget.spend()
                name = str(self.graph[consumer].get("class_type", ""))
                if (
                    name in self.stage_classes
                    or name in self.semantics.non_file_outputs
                ):
                    continue
                if not consumers.get(consumer):
                    return True
                queue.append(consumer)
        return False

    def branches(self, sampler_id: str) -> dict:
        """Resolve once per runtime selector choice that changes a role text.

        Every run draws from the same candidate allowance. Results that agree
        collapse to one; otherwise the sampler is ``ambiguous`` with one
        candidate per choice set, in true-before-false order.
        """
        pending: list = [{}]
        results: list = []
        try:
            while pending:
                self.choices = pending.pop(0)
                try:
                    results.append(self.stage(sampler_id, frozenset()))
                except _Diverging as diverging:
                    if len(results) + len(pending) + 2 > _MAX_BRANCH_CANDIDATES:
                        raise _Unresolved("Too many selector branches", "work-limit")
                    pending.extend(
                        {**self.choices, diverging.key: choice}
                        for choice in (True, False)
                    )
        except _Unresolved as error:
            return _result(
                "unresolved",
                sampler_id=sampler_id,
                reason=str(error),
                reason_code=error.code,
                node=error.node,
            )
        finally:
            self.choices = {}
        if all(item["state"] == "unresolved" for item in results):
            return results[0]
        if len({_outcome_key(item) for item in results}) == 1:
            return results[0]
        return _result(
            "ambiguous",
            sampler_id=sampler_id,
            reason="Selector branches have different prompts",
            candidates=results,
        )

    def sides(self, sampler_id: str) -> dict:
        positive = negative = None
        try:
            name, inputs = self.node(sampler_id)
            entry = self.sampler_entry(name)
            self.check_overrides(sampler_id, entry, inputs)
            # A second sampler in the latent ancestry denotes a multistage run;
            # associating the final image with one prompt pair would be a guess.
            latent = entry.get("latent")
            if latent is not None and latent in inputs:
                if self.semantics is None:
                    self.latent_source(inputs[latent], set(), 0)
                else:
                    self.latent_checked(sampler_id, inputs[latent])
            guider_id, depth = None, 0
            conditioning = entry["conditioning"]
            absent_reason = "Negative input is absent"
            if entry["strategy"] == "custom-advanced":
                guider_id, inputs, conditioning, guider_reason, depth = self.guider(
                    inputs.get(conditioning["guider"])
                )
                if conditioning["negative"] is None:
                    absent_reason = guider_reason
            positive = self.side(
                inputs.get(conditioning["positive"]), sampler_id, guider_id, depth
            )
            if not positive["text"].strip():
                raise _Unresolved(EMPTY_POSITIVE_REASON)
            negative_name = conditioning["negative"]
            if negative_name is None or negative_name not in inputs:
                return _result(
                    "absent",
                    positive,
                    sampler_id=sampler_id,
                    reason=absent_reason,
                )
            negative = self.side(inputs[negative_name], sampler_id, guider_id, depth)
            return _result("resolved", positive, negative, sampler_id)
        except _Unresolved as error:
            return _result(
                "unresolved",
                positive,
                negative,
                sampler_id,
                str(error),
                reason_code=error.code,
                node=error.node,
            )

    def check_overrides(self, sampler_id: str, entry: Mapping, inputs: dict) -> None:
        """A set prompt override replaces the conditioning inputs' prompt.

        Exact ``""`` (or a missing input) leaves the prompt alone. A literal is
        processed at run time (a detailer wildcard); a link is a source the
        inspector does not follow, so it is named.
        """
        for name in entry.get("prompt_overrides", ()):
            value = inputs.get(name)
            if value is None or value == "":
                continue
            if _link(value):
                raise self.unsupported("Unsupported prompt override", str(value[0]))
            raise self.named(_RUNTIME_REASON, "runtime-value", sampler_id)

    def latent_source(self, value: Any, seen: set, depth: int) -> None:
        if not _link(value) or depth >= self.max_depth:
            code = "malformed" if not _link(value) else "depth-limit"
            raise _Unresolved("Unknown latent ancestry", code)
        node_id, slot = str(value[0]), value[1]
        if self.legacy:
            if node_id in seen or len(seen) >= max(0, self.budget.remaining):
                raise _Unresolved("Cyclic or excessive latent ancestry")
        elif node_id in seen:
            raise _Unresolved("Cyclic or excessive latent ancestry", "malformed")
        else:
            self.budget.spend()
        seen.add(node_id)
        name, inputs = self.node(node_id)
        if name in self.stage_classes:
            # ``multistage-differs`` is reserved for when stage texts are compared.
            raise _Unresolved(MULTISTAGE_UNATTRIBUTED_REASON)
        if name in ("EmptyLatentImage", "EmptySD3LatentImage") and slot == 0:
            return
        if name == "Reroute" and slot == 0 and "value" in inputs:
            self.latent_source(inputs["value"], seen, depth + 1)
            return
        raise self.unsupported("Unsupported latent ancestry", node_id)

    def latent_checked(self, sampler_id: str, value: Any) -> None:
        """``latent_stages`` once per sampler per resolution.

        A candidate reusing another candidate's walk is charged its visit cost
        (as the text memo does), so outcomes stay independent of order; branch
        runs of one candidate share its allowance and pay once. A walk that ran
        out of work is never reused, since it depends on the remaining budget.
        """
        if sampler_id in self.latent_walks:
            error, cost = self.latent_walks[sampler_id]
            if sampler_id not in self.latent_charged:
                self.budget.charge(cost)
                self.latent_charged.add(sampler_id)
        else:
            before = self.budget.allowance
            try:
                self.latent_stages(sampler_id, value)
                error = None
            except _Unresolved as raised:
                if raised.code == "work-limit":
                    raise
                error = raised
            self.latent_walks[sampler_id] = (error, before - self.budget.allowance)
            self.latent_charged.add(sampler_id)
        if error is not None:
            node = dict(error.node) if error.node is not None else None
            raise _Unresolved(str(error), error.code, node)

    def latent_stages(self, origin: str, value: Any) -> None:
        """Semantics table: is there an earlier sampler in the latent ancestry?

        Every link input of non-sampler nodes is walked breadth-first (encoders,
        decoders, masks, conditioning), stopping at listed samplers; each node
        draws a visit. No sampler found means a single stage whatever the origin
        (VAEEncode, EmptyLatentBatch, WanImageToVideo slot 2, ...). An unlisted
        node with sampler-shaped inputs could itself be a stage, so it is
        ``unsupported-node``. Budget or depth exhaustion is never single stage.
        """
        if not _link(value):
            raise _Unresolved("Unknown latent ancestry", "malformed")
        queue, queued = self.walk_from(value)
        while queue:
            node_id, depth = queue.popleft()
            if node_id == origin:
                raise _Unresolved("Cyclic latent ancestry", "malformed")
            if depth >= self.max_depth:
                raise _Unresolved("Resolution depth limit reached", "depth-limit")
            self.budget.spend()
            name, inputs = self.node(node_id)
            if name in self.stage_classes:
                # ``multistage-differs`` is reserved for when stage texts are
                # compared (PR3); until then no stage's prompt is chosen.
                raise _Unresolved(MULTISTAGE_UNATTRIBUTED_REASON)
            if self.stage_shaped(name, inputs):
                raise self.unsupported("Unsupported latent ancestry", node_id)
            self.enqueue(queue, queued, inputs, depth + 1)

    def anchor(self, node_id: str) -> str:
        seen = set()
        slot = 0
        while len(seen) < self.max_depth and (
            not self.legacy or len(seen) < self.budget.remaining
        ):
            if node_id in seen:
                raise _Unresolved("Cyclic output ancestry", "malformed")
            if not self.legacy:
                self.budget.spend()
            seen.add(node_id)
            name, inputs = self.node(node_id)
            if name in self.sampler_classes and slot == 0:
                return node_id
            key = {
                "SaveImage": "images",
                "PreviewImage": "images",
                "VAEDecode": "samples",
                "VAEDecodeTiled": "samples",
                "Reroute": "value",
            }.get(name)
            value = inputs.get(key) if key and slot == 0 else None
            if not _link(value):
                raise self.unsupported("Unsupported output ancestry", node_id)
            node_id, slot = str(value[0]), value[1]
        code = "depth-limit" if len(seen) >= self.max_depth else "work-limit"
        raise _Unresolved("Output ancestry limit reached", code)


_FOOTER = re.compile(r"(?m)^Steps:\s*\d+\s*,[^\n]*$")
# Inspection also reads a footer of ``Steps: N`` alone (no other settings).
_BARE_FOOTER = re.compile(r"(?m)^Steps:\s*\d+\s*$")
# sd-dynamic-prompts writes these one-line records after the settings footer.
_FOOTER_TRAILERS = ("Template:", "Negative Template:")


def _trailer_lines(text: str) -> bool:
    """Whether ``text`` after the footer is empty or only template records.

    At most one ``Template:`` line, then at most one ``Negative Template:``
    line; anything else (a multi-line template included) is not recognized.
    """
    lines = re.split(r"\r?\n|\r", text.strip()) if text.strip() else []
    prefixes = [
        next(
            (
                index
                for index, name in enumerate(_FOOTER_TRAILERS)
                if line.startswith(name)
            ),
            None,
        )
        for line in lines
    ]
    return None not in prefixes and prefixes == sorted(set(prefixes))


def _footer(value: str, inspection: bool) -> Optional[re.Match]:
    """The unique A1111 settings footer, or ``None``.

    The default profile keeps the historical rule (one ``Steps: N, ...`` line,
    and it is the last line). Inspection (R5) accepts a bare ``Steps: N``
    footer when there is no full one, and template records after it.
    """
    footers = list(_FOOTER.finditer(value))
    if inspection and not footers:
        footers = list(_BARE_FOOTER.finditer(value))
    if len(footers) != 1:
        return None
    rest = value[footers[0].end() :]
    if inspection and _trailer_lines(rest):
        return footers[0]
    return None if rest.strip() else footers[0]


def _parameters(value: Any, max_text_length: int, inspection: bool = False) -> dict:
    if not isinstance(value, str) or not value:
        return _result("absent", reason="No A1111 parameters")
    if len(value) > max_text_length:
        return _result(
            "unresolved",
            reason="Parameters exceed the length limit",
            reason_code="text-limit",
        )
    footer = _footer(value, inspection)
    if footer is None:
        return _result(
            "unresolved",
            reason="A1111 settings footer is not unique",
            reason_code="malformed",
        )
    body = value[: footer.start()].rstrip("\r\n")
    if inspection:
        # R4: text that starts with the delimiter has an empty positive (#165).
        body = "\n" + body
    delimiter = "\nNegative prompt:"
    if body.count(delimiter) > 1:
        return _result("ambiguous", reason="Multiple negative prompt delimiters")
    positive, found, negative = body.partition(delimiter)
    positive = positive.strip()
    if not positive:
        return _result("unresolved", reason=EMPTY_POSITIVE_REASON)
    provenance = {
        "format": "a1111",
        "field": "parameters",
        "text_source": "embedded-parameters",
    }
    positive_side = {
        "text": positive,
        "storage_text": positive,
        "provenance": {**provenance, "role": "positive"},
    }
    if not found:
        return _result(
            "absent", positive_side, reason="Negative prompt delimiter is absent"
        )
    negative_side = {
        "text": negative.strip(),
        "storage_text": negative.strip(),
        "provenance": {**provenance, "role": "negative"},
    }
    return _result("resolved", positive_side, negative_side)


def _profile_with(
    profile: ResolverProfile,
    max_visits: Optional[int],
    max_depth: Optional[int],
    max_text_length: Optional[int],
) -> ResolverProfile:
    """Explicit ``max_*`` keywords override the profile they are given with."""
    changes: Dict[str, Any] = {}
    if max_visits is not None:
        changes["per_candidate_visits"] = max_visits
        if profile.legacy:  # one historical number: gate, total and allowance
            changes.update(total_visits=max_visits, legacy_node_gate=max_visits)
    if max_depth is not None:
        changes["max_depth"] = max_depth
    if max_text_length is not None:
        changes["max_text_length"] = max_text_length
    return replace(profile, **changes) if changes else profile


def _sampler_like_classes(graph: dict) -> list:
    """Distinct classes, in graph order, of nodes shaped like samplers/guiders."""
    names: list = []
    for node in graph.values():
        if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
            continue
        name = node.get("class_type")
        if not isinstance(name, str) or not name or name in names:
            continue
        if any(_link(node["inputs"].get(key)) for key in _SAMPLER_LIKE_INPUTS):
            names.append(name[:_CLASS_NAME_LENGTH])
            if len(names) == _NO_SAMPLER_NAMES:
                break
    return names


def resolve_prompt_pair(
    metadata: Any,
    *,
    output_node_id: Optional[str] = None,
    adapters: Optional[Mapping[Tuple[str, int], OutputAdapter]] = None,
    profile: ResolverProfile = DEFAULT_PROFILE,
    max_visits: Optional[int] = None,
    max_depth: Optional[int] = None,
    max_text_length: Optional[int] = None,
) -> Dict[str, Any]:
    """Resolve parsed metadata; a present API graph is authoritative over text.

    Accepts ``{"prompt": api_graph, "parameters": optional_a1111_text}`` or a
    direct API graph. An optional saver/sampler node ID anchors branch selection.
    Unanchored multiple samplers are ambiguous, even when their texts are equal.
    ``profile`` selects the bounds; explicit ``max_*`` keywords override it.
    """
    profile = _profile_with(profile, max_visits, max_depth, max_text_length)
    result = _resolve(metadata, output_node_id, adapters, profile)
    return _legacy_result(result) if profile.legacy else result


SEMANTICS_UNAVAILABLE_REASON = "Inspection node semantics are unavailable"


def _inspection_semantics() -> Optional[Any]:
    """The validated node table, or ``None`` when it cannot be loaded.

    Imported here, not at module level: ingestion (the default profile) must
    keep working whatever state the table file is in.
    """
    from .inspection_semantics import SemanticsError, get_semantics

    try:
        return get_semantics()
    except SemanticsError as error:
        _LOGGER.error("Inspection node semantics table is unusable: %s", error)
        return None


def _limit_result(profile: ResolverProfile, graph: dict) -> Optional[Dict[str, Any]]:
    if profile.legacy:
        if (
            len(graph) > profile.legacy_node_gate
            or profile.max_depth <= 0
            or profile.max_text_length <= 0
        ):
            return _result("unresolved", reason="Resolution limit reached")
        return None
    if profile.node_ceiling is not None and len(graph) > profile.node_ceiling:
        return _result(
            "unresolved",
            reason="Prompt graph exceeds the inspection node ceiling",
            reason_code="graph-ceiling",
        )
    if profile.max_depth <= 0:
        return _result(
            "unresolved", reason="Resolution limit reached", reason_code="depth-limit"
        )
    if profile.max_text_length <= 0:
        return _result(
            "unresolved", reason="Resolution limit reached", reason_code="text-limit"
        )
    return None


def _resolve(
    metadata: Any,
    output_node_id: Optional[str],
    adapters: Optional[Mapping[Tuple[str, int], OutputAdapter]],
    profile: ResolverProfile,
) -> Dict[str, Any]:
    if not isinstance(metadata, dict) or not metadata:
        return _result("absent", reason="No parsed prompt metadata")
    if "prompt" in metadata:
        graph = metadata["prompt"]
    elif "parameters" in metadata:
        return _parameters(
            metadata["parameters"], profile.max_text_length, not profile.legacy
        )
    else:
        graph = metadata
    if not isinstance(graph, dict) or not graph:
        return _result(
            "unresolved",
            reason="API graph is missing or malformed",
            reason_code="malformed",
        )
    limited = _limit_result(profile, graph)
    if limited is not None:
        return limited
    semantics = None
    if profile.semantics:
        semantics = _inspection_semantics()
        if semantics is None:
            return _result("unresolved", reason=SEMANTICS_UNAVAILABLE_REASON)
    normalized = {str(node_id): node for node_id, node in graph.items()}
    if len(normalized) != len(graph):
        return _result(
            "unresolved",
            reason="Graph node IDs are not unique",
            reason_code="malformed",
        )
    overwritten = frozenset()
    if semantics is not None and "prompt" in metadata:
        overwritten = _overwritten_prompt_nodes(metadata.get("workflow"), normalized)
    resolver = _GraphResolver(
        normalized,
        adapters or {},
        profile.per_candidate_visits,
        profile.max_depth,
        profile.max_text_length,
        metadata if "prompt" in metadata else None,
        budget=_Budget(profile.total_visits),
        profile=profile,
        semantics=semantics,
        overwritten=overwritten,
    )
    if output_node_id is not None:
        try:
            sampler_ids = [resolver.anchor(str(output_node_id))]
        except _Unresolved as error:
            return _result(
                "unresolved", reason=str(error), reason_code=error.code, node=error.node
            )
    else:
        sampler_ids = [
            node_id
            for node_id, node in normalized.items()
            if isinstance(node, dict)
            and isinstance(node.get("class_type"), str)
            and node["class_type"] in resolver.sampler_classes
        ]
        if semantics is not None:
            sampler_ids = resolver.final_stages(sampler_ids)
    if not sampler_ids:
        return _no_sampler(normalized, profile)
    if len(sampler_ids) > 1:
        return _result(
            "ambiguous",
            reason="Multiple sampler branches",
            candidates=[resolver.pair(node_id) for node_id in sampler_ids],
        )
    return resolver.pair(sampler_ids[0])


def _overwritten_prompt_nodes(workflow: Any, graph: dict) -> frozenset:
    """API node IDs whose saved PromptManager text is another node's (R1).

    The pre-3.2.3 signature (comfy-metadata.js ``createContext``): two or more
    workflow nodes of a PromptManager type other than ``PromptManagerPair``
    share one API ``text`` while their first widget values differ. Their API
    class may have been rewritten to ``CLIPTextEncode``, so nodes are matched by
    ID. Unlike the legacy viewer, the widget text is never substituted: it was
    not recorded as executed, so these nodes are refused instead.
    """
    nodes = workflow.get("nodes") if isinstance(workflow, dict) else None
    if not isinstance(nodes, list):
        return frozenset()
    widgets: Dict[str, str] = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        kind, values, node_id = (
            node.get("type"),
            node.get("widgets_values"),
            node.get("id"),
        )
        if (
            isinstance(kind, str)
            and kind != "PromptManagerPair"
            and "promptmanager" in kind.lower()
            and isinstance(values, list)
            and values
            and isinstance(values[0], str)
            and isinstance(node_id, (str, int))
            and not isinstance(node_id, bool)
        ):
            widgets[str(node_id)] = values[0]
    by_text: Dict[str, list] = {}
    for node_id, widget in widgets.items():
        node = graph.get(node_id)
        inputs = node.get("inputs") if isinstance(node, dict) else None
        text = inputs.get("text") if isinstance(inputs, dict) else None
        if isinstance(text, str):
            by_text.setdefault(text, []).append(node_id)
    return frozenset(
        node_id
        for ids in by_text.values()
        if len(ids) > 1 and len({widgets[node_id] for node_id in ids}) > 1
        for node_id in ids
    )


def _no_sampler(graph: dict, profile: ResolverProfile) -> Dict[str, Any]:
    reason = "No supported sampler pair found"
    if profile.legacy:
        has_pair_inputs = any(
            isinstance(node, dict)
            and isinstance(node.get("inputs"), dict)
            and ("positive" in node["inputs"] or "negative" in node["inputs"])
            for node in graph.values()
        )
        return _result("unresolved" if has_pair_inputs else "absent", reason=reason)
    # A prompt graph is a record: its prompt is unresolved, never "not recorded".
    seen = _sampler_like_classes(graph)
    if seen:
        reason = f"{reason} (saw {', '.join(seen)})"
    return _result("unresolved", reason=reason, reason_code="no-sampler")


def resolve_text_input(
    graph: Any,
    node_id: str,
    input_name: str,
    *,
    max_visits: int = 500,
    max_depth: int = 64,
    max_text_length: int = 100_000,
) -> Optional[str]:
    """Resolve one raw STRING input; empty text is distinct from unknown input."""
    if not isinstance(graph, dict) or len(graph) > max_visits:
        return None
    normalized = {str(key): value for key, value in graph.items()}
    if len(normalized) != len(graph) or max_depth <= 0 or max_text_length <= 0:
        return None
    resolver = _GraphResolver(normalized, {}, max_visits, max_depth, max_text_length)
    try:
        _, inputs = resolver.node(str(node_id))
        return resolver.text(inputs.get(input_name), "string")[0]
    except _Unresolved:
        return None
