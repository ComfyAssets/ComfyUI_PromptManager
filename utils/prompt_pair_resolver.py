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
ComfyUI, database, configuration or filesystem dependencies.

Two ``ResolverProfile`` values exist. ``DEFAULT_PROFILE`` (ingestion: scan, live
monitor, admin import) keeps the historical behavior exactly, including the
500-node gate, and only adds ``reason_code: None``. ``INSPECTION_PROFILE`` bounds
work instead of graph size: a per-candidate visit allowance, a total visit budget
shared by every candidate and attribution walk, a node ceiling backstop, and
bounds on built provenance and text. It also names why a result is not resolved
(``reason_code``, and ``node`` for the first unsupported node), and a prompt graph
without a supported sampler is ``unresolved`` (``no-sampler``), never ``absent``.
"""

import re
from dataclasses import dataclass, replace
from typing import Any, Dict, Mapping, Optional, Tuple

from .pair_execution import ExecutionEvidenceError, read_pair_execution


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


@dataclass(frozen=True)
class ResolverProfile:
    """Resolution bounds. ``legacy_node_gate`` set means the historical code path.

    ``max_path_entries`` and ``max_built_chars`` bound memory across the whole
    resolution (provenance entries and newly built text); ``None`` disables them.
    ``semantics`` and ``selection`` are reserved for later slices and stay off.
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
    max_path_entries=64 * 20_000,
    max_built_chars=16 * 100_000,
)
_SAMPLER_LIKE_INPUTS = ("latent_image", "guider", "positive", "negative")
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


def _node_ref(node_id: str, class_type: Any) -> dict:
    name = class_type if isinstance(class_type, str) else None
    return {"id": node_id, "class_type": name}


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
        self.total -= 1
        self.allowance -= 1
        if self.total < 0 or self.allowance < 0:
            raise _Unresolved("Resolution visit limit reached", "work-limit")


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
    ) -> None:
        self.graph = graph
        self.adapters = {**_DEFAULT_ADAPTERS, **adapters}
        self.per_candidate_visits = max_visits
        self.budget = budget if budget is not None else _Budget(max_visits)
        self.budget.reset(max_visits)
        self.legacy = profile.legacy
        self.path_entries = profile.max_path_entries
        self.built_chars = profile.max_built_chars
        self.max_depth = max_depth
        self.max_text_length = max_text_length
        self.memo: dict = {}
        self.active: set = set()
        self.metadata = metadata or {}
        self.text_sources: dict = {}

    def node(self, node_id: str) -> Tuple[str, dict]:
        node = self.graph.get(node_id)
        if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
            raise _Unresolved("Missing or malformed graph node", "malformed")
        if any(not isinstance(key, str) for key in node["inputs"]):
            raise _Unresolved("Malformed node input names", "malformed")
        return str(node.get("class_type", "")), node["inputs"]

    def unsupported(self, message: str, node_id: str) -> _Unresolved:
        node = self.graph.get(node_id)
        class_type = node.get("class_type") if isinstance(node, dict) else None
        if isinstance(class_type, str):
            class_type = class_type[:_CLASS_NAME_LENGTH]
        return _Unresolved(message, "unsupported-node", _node_ref(node_id, class_type))

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
            return self.memo[key]
        self.budget.spend()
        name, inputs = self.node(node_id)
        self.active.add(key)
        try:
            if name == "Reroute" and slot == 0:
                keys = [key for key in ("value", "input", "in") if key in inputs]
                if len(keys) != 1:
                    raise _Unresolved(
                        "Reroute input is not uniquely identifiable", "malformed"
                    )
                text, path, storage_text = self.text(inputs[keys[0]], kind, depth + 1)
            elif (name, slot) in self.adapters:
                adapter = self.adapters[(name, slot)]
                if not isinstance(adapter, OutputAdapter) or adapter.kind != kind:
                    raise _Unresolved(
                        "Output slot has an incompatible type", "malformed"
                    )
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
            else:
                raise self.unsupported(
                    "Unsupported node semantics or output slot", node_id
                )
            # Memoized paths stay bounded even for diamond-shaped string graphs.
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
            self.memo[key] = (text, path, storage_text)
            return text, path, storage_text
        finally:
            self.active.discard(key)

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
                if str(error) == "Unsupported node semantics or output slot":
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

    def side(self, value: Any, sampler_id: str) -> dict:
        text, path, storage_text = self.text(value, "conditioning")
        origin = path[0]
        return {
            "text": text,
            "storage_text": storage_text,
            "provenance": {
                "format": "comfyui",
                "sampler_id": sampler_id,
                **origin,
                "path": path,
                "text_source": self.text_sources.get(
                    (origin["node_id"], origin["output_slot"]), "graph-inferred"
                ),
            },
        }

    def pair(self, sampler_id: str) -> dict:
        positive = negative = None
        self.budget.reset(self.per_candidate_visits)
        if not self.legacy:
            # Each candidate chain is costed on its own: its outcome never depends
            # on which other candidates were traced first.
            self.memo = {}
        if not self.legacy and self.budget.total <= 0:
            return _result(
                "unresolved",
                sampler_id=sampler_id,
                reason="Resolution visit limit reached",
                reason_code="work-limit",
            )
        try:
            _, inputs = self.node(sampler_id)
            # A second sampler in the latent ancestry denotes a multistage run;
            # associating the final image with one prompt pair would be a guess.
            if "latent_image" in inputs:
                self.latent_source(inputs["latent_image"], set(), 0)
            positive = self.side(inputs.get("positive"), sampler_id)
            if not positive["text"].strip():
                raise _Unresolved("Positive prompt is empty")
            if "negative" not in inputs:
                return _result(
                    "absent",
                    positive,
                    sampler_id=sampler_id,
                    reason="Negative input is absent",
                )
            negative = self.side(inputs["negative"], sampler_id)
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
        if name in _SAMPLERS:
            # ``multistage-differs`` is reserved for when stage texts are compared.
            raise _Unresolved("Multistage sampler ancestry is ambiguous")
        if name in ("EmptyLatentImage", "EmptySD3LatentImage") and slot == 0:
            return
        if name == "Reroute" and slot == 0 and "value" in inputs:
            self.latent_source(inputs["value"], seen, depth + 1)
            return
        raise self.unsupported("Unsupported latent ancestry", node_id)

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
            if name in _SAMPLERS and slot == 0:
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


def _parameters(value: Any, max_text_length: int) -> dict:
    if not isinstance(value, str) or not value:
        return _result("absent", reason="No A1111 parameters")
    if len(value) > max_text_length:
        return _result(
            "unresolved",
            reason="Parameters exceed the length limit",
            reason_code="text-limit",
        )
    footers = list(re.finditer(r"(?m)^Steps:\s*\d+\s*,[^\n]*$", value))
    if len(footers) != 1 or value[footers[0].end() :].strip():
        return _result(
            "unresolved",
            reason="A1111 settings footer is not unique",
            reason_code="malformed",
        )
    body = value[: footers[0].start()].rstrip("\r\n")
    delimiter = "\nNegative prompt:"
    if body.count(delimiter) > 1:
        return _result("ambiguous", reason="Multiple negative prompt delimiters")
    positive, found, negative = body.partition(delimiter)
    positive = positive.strip()
    if not positive:
        return _result("unresolved", reason="Positive prompt is empty")
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
        return _parameters(metadata["parameters"], profile.max_text_length)
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
    normalized = {str(node_id): node for node_id, node in graph.items()}
    if len(normalized) != len(graph):
        return _result(
            "unresolved",
            reason="Graph node IDs are not unique",
            reason_code="malformed",
        )
    resolver = _GraphResolver(
        normalized,
        adapters or {},
        profile.per_candidate_visits,
        profile.max_depth,
        profile.max_text_length,
        metadata if "prompt" in metadata else None,
        budget=_Budget(profile.total_visits),
        profile=profile,
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
            and node["class_type"] in _SAMPLERS
        ]
    if not sampler_ids:
        return _no_sampler(normalized, profile)
    if len(sampler_ids) > 1:
        return _result(
            "ambiguous",
            reason="Multiple sampler branches",
            candidates=[resolver.pair(node_id) for node_id in sampler_ids],
        )
    return resolver.pair(sampler_ids[0])


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
