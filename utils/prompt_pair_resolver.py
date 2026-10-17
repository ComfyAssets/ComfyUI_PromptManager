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
"""

import re
from dataclasses import dataclass
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


class _Unresolved(Exception):
    pass


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
) -> Dict[str, Any]:
    return {
        "state": state,
        "positive": positive,
        "negative": negative,
        "sampler_id": sampler_id,
        "reason": reason,
        "candidates": candidates or [],
    }


class _GraphResolver:
    def __init__(
        self,
        graph: dict,
        adapters: Mapping[Tuple[str, int], OutputAdapter],
        max_visits: int,
        max_depth: int,
        max_text_length: int,
        metadata: Optional[dict] = None,
    ) -> None:
        self.graph = graph
        self.adapters = {**_DEFAULT_ADAPTERS, **adapters}
        self.budget = max_visits
        self.max_depth = max_depth
        self.max_text_length = max_text_length
        self.memo: dict = {}
        self.active: set = set()
        self.metadata = metadata or {}
        self.text_sources: dict = {}

    def node(self, node_id: str) -> Tuple[str, dict]:
        node = self.graph.get(node_id)
        if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
            raise _Unresolved("Missing or malformed graph node")
        if any(not isinstance(key, str) for key in node["inputs"]):
            raise _Unresolved("Malformed node input names")
        return str(node.get("class_type", "")), node["inputs"]

    def bounded(self, text: str) -> str:
        if len(text) > self.max_text_length:
            raise _Unresolved("Resolved text exceeds the length limit")
        return text

    def text(self, value: Any, kind: str, depth: int = 0) -> Tuple[str, list, str]:
        if depth >= self.max_depth:
            raise _Unresolved("Resolution depth limit reached")
        if isinstance(value, str) and kind == "string":
            return self.bounded(value), [], value
        if not _link(value):
            raise _Unresolved("Expected a typed graph link or text input")
        node_id, slot = str(value[0]), value[1]
        key = (node_id, slot, kind)
        if key in self.active:
            raise _Unresolved("Cyclic prompt graph")
        if key in self.memo:
            return self.memo[key]
        self.budget -= 1
        if self.budget < 0:
            raise _Unresolved("Resolution visit limit reached")
        name, inputs = self.node(node_id)
        self.active.add(key)
        try:
            if name == "Reroute" and slot == 0:
                keys = [key for key in ("value", "input", "in") if key in inputs]
                if len(keys) != 1:
                    raise _Unresolved("Reroute input is not uniquely identifiable")
                text, path, storage_text = self.text(inputs[keys[0]], kind, depth + 1)
            elif (name, slot) in self.adapters:
                adapter = self.adapters[(name, slot)]
                if not isinstance(adapter, OutputAdapter) or adapter.kind != kind:
                    raise _Unresolved("Output slot has an incompatible type")
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
                        text = self.bounded(
                            " ".join(
                                part
                                for part in (before.strip(), text, after.strip())
                                if part
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
                    raise _Unresolved("Malformed concatenation")
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
                            raise _Unresolved("Resolved text exceeds the length limit")
                        parts.append(part)
                text = delimiter.join(parts)
                storage_text = text
            else:
                raise _Unresolved("Unsupported node semantics or output slot")
            # Memoized paths stay bounded even for diamond-shaped string graphs.
            path = [{"node_id": node_id, "output_slot": slot}] + path
            if len(path) > self.max_depth * max(1, len(self.graph)):
                raise _Unresolved("Provenance path limit reached")
            self.memo[key] = (text, path, storage_text)
            return text, path, storage_text
        finally:
            self.active.discard(key)

    def execution_record(self, node_id: str, inputs: dict) -> Optional[dict]:
        try:
            record = read_pair_execution(self.metadata, node_id)
        except ExecutionEvidenceError as error:
            raise _Unresolved(str(error)) from error
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
                    "Execution evidence does not match recorded node inputs"
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
            return _result("unresolved", positive, negative, sampler_id, str(error))

    def latent_source(self, value: Any, seen: set, depth: int) -> None:
        if not _link(value) or depth >= self.max_depth:
            raise _Unresolved("Unknown latent ancestry")
        node_id, slot = str(value[0]), value[1]
        if node_id in seen or len(seen) >= max(0, self.budget):
            raise _Unresolved("Cyclic or excessive latent ancestry")
        seen.add(node_id)
        name, inputs = self.node(node_id)
        if name in _SAMPLERS:
            raise _Unresolved("Multistage sampler ancestry is ambiguous")
        if name in ("EmptyLatentImage", "EmptySD3LatentImage") and slot == 0:
            return
        if name == "Reroute" and slot == 0 and "value" in inputs:
            self.latent_source(inputs["value"], seen, depth + 1)
            return
        raise _Unresolved("Unsupported latent ancestry")

    def anchor(self, node_id: str) -> str:
        seen = set()
        slot = 0
        while len(seen) < self.max_depth and len(seen) < self.budget:
            if node_id in seen:
                raise _Unresolved("Cyclic output ancestry")
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
                raise _Unresolved("Unsupported output ancestry")
            node_id, slot = str(value[0]), value[1]
        raise _Unresolved("Output ancestry limit reached")


def _parameters(value: Any, max_text_length: int) -> dict:
    if not isinstance(value, str) or not value:
        return _result("absent", reason="No A1111 parameters")
    if len(value) > max_text_length:
        return _result("unresolved", reason="Parameters exceed the length limit")
    footers = list(re.finditer(r"(?m)^Steps:\s*\d+\s*,[^\n]*$", value))
    if len(footers) != 1 or value[footers[0].end() :].strip():
        return _result("unresolved", reason="A1111 settings footer is not unique")
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


def resolve_prompt_pair(
    metadata: Any,
    *,
    output_node_id: Optional[str] = None,
    adapters: Optional[Mapping[Tuple[str, int], OutputAdapter]] = None,
    max_visits: int = 500,
    max_depth: int = 64,
    max_text_length: int = 100_000,
) -> Dict[str, Any]:
    """Resolve parsed metadata; a present API graph is authoritative over text.

    Accepts ``{"prompt": api_graph, "parameters": optional_a1111_text}`` or a
    direct API graph. An optional saver/sampler node ID anchors branch selection.
    Unanchored multiple samplers are ambiguous, even when their texts are equal.
    """
    if not isinstance(metadata, dict) or not metadata:
        return _result("absent", reason="No parsed prompt metadata")
    if "prompt" in metadata:
        graph = metadata["prompt"]
    elif "parameters" in metadata:
        return _parameters(metadata["parameters"], max_text_length)
    else:
        graph = metadata
    if not isinstance(graph, dict) or not graph:
        return _result("unresolved", reason="API graph is missing or malformed")
    if len(graph) > max_visits or max_depth <= 0 or max_text_length <= 0:
        return _result("unresolved", reason="Resolution limit reached")
    normalized = {str(node_id): node for node_id, node in graph.items()}
    if len(normalized) != len(graph):
        return _result("unresolved", reason="Graph node IDs are not unique")
    resolver = _GraphResolver(
        normalized,
        adapters or {},
        max_visits,
        max_depth,
        max_text_length,
        metadata if "prompt" in metadata else None,
    )
    if output_node_id is not None:
        try:
            sampler_ids = [resolver.anchor(str(output_node_id))]
        except _Unresolved as error:
            return _result("unresolved", reason=str(error))
    else:
        sampler_ids = [
            node_id
            for node_id, node in normalized.items()
            if isinstance(node, dict)
            and isinstance(node.get("class_type"), str)
            and node["class_type"] in _SAMPLERS
        ]
    if not sampler_ids:
        has_pair_inputs = any(
            isinstance(node, dict)
            and isinstance(node.get("inputs"), dict)
            and ("positive" in node["inputs"] or "negative" in node["inputs"])
            for node in normalized.values()
        )
        return _result(
            "unresolved" if has_pair_inputs else "absent",
            reason="No supported sampler pair found",
        )
    if len(sampler_ids) > 1:
        return _result(
            "ambiguous",
            reason="Multiple sampler branches",
            candidates=[resolver.pair(node_id) for node_id in sampler_ids],
        )
    return resolver.pair(sampler_ids[0])


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
