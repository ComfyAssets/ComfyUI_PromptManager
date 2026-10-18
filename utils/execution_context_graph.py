"""Bounded projection of declared sampler roles, independent of the host."""

import re
from dataclasses import dataclass
from .execution_contexts import PromptSnapshot, TextEvidence, text_size
from .prompt_pair_resolver import _DEFAULT_ADAPTERS


@dataclass(frozen=True)
class SourceRef:
    node_id: str
    node_type: str
    output_slot: int


@dataclass(frozen=True)
class GraphRole:
    sources: tuple[SourceRef, ...]
    inferred: PromptSnapshot | None
    reason: str


@dataclass(frozen=True)
class DeclaredRecipe:
    anchor_id: str
    positive: GraphRole
    negative: GraphRole


@dataclass(frozen=True)
class GraphProjection:
    recipes: tuple[DeclaredRecipe, ...]
    sources: tuple[SourceRef, ...]
    reason: str


_SAMPLERS = {"KSampler", "KSamplerAdvanced", "SamplerCustom"}
_PM = {
    "PromptManager",
    "PromptManagerFocused",
    "PromptManagerSplit",
    "PromptManagerText",
    "PromptManagerPair",
}


class _Unknown(ValueError):
    pass


def _identifier(value: object) -> str:
    if type(value) is int:
        if abs(value) > 10**127:
            raise _Unknown("Invalid node identifier")
        value = str(value)
    try:
        text_size(value, 512)
        if len(value) > 128:
            raise ValueError("Oversized node identifier")
    except ValueError:
        raise _Unknown("Invalid node identifier") from None
    if not value:
        raise _Unknown("Invalid node identifier")
    return value


def _link(value: object) -> tuple[str, int]:
    if (
        type(value) not in (tuple, list)
        or len(value) != 2
        or type(value[1]) is not int
        or value[1] < 0
    ):
        raise _Unknown("Expected typed graph link")
    return _identifier(value[0]), value[1]


class _Projector:
    def __init__(self, graph: dict) -> None:
        self.graph = graph
        self.visits = 0
        self.transformed = False
        self.selected_seen = set()

    def node(self, node_id: str, depth: int) -> tuple[str, dict]:
        self.visits += 1
        if self.visits > 500 or depth > 64:
            raise _Unknown("Graph resolution limit reached")
        node = self.graph.get(node_id)
        if type(node) is not dict or type(node.get("inputs")) is not dict:
            raise _Unknown("Missing or malformed node")
        if len(node["inputs"]) > 500:
            raise _Unknown("Node input limit reached")
        kind = node.get("class_type")
        try:
            text_size(kind, 128)
        except ValueError:
            raise _Unknown("Invalid node type") from None
        return kind, node["inputs"]

    def text(
        self, value: object, kind: str, depth: int = 1, active: frozenset = frozenset()
    ) -> tuple[PromptSnapshot, tuple]:
        if type(value) is str and kind == "string":
            text_size(value)
            return self.snapshot(value, value, "", ""), ()
        node_id, slot = _link(value)
        key = (node_id, slot)
        if key in active:
            raise _Unknown("Cyclic prompt graph")
        name, inputs = self.node(node_id, depth)
        active = active | {key}
        if name == "Reroute" and slot == 0:
            keys = [key for key in ("value", "input", "in") if key in inputs]
            if len(keys) != 1:
                raise _Unknown("Ambiguous reroute")
            return self.text(inputs[keys[0]], kind, depth + 1, active)
        if (
            name in ("StringConcatenate", "Text Concatenate")
            and slot == 0
            and kind == "string"
        ):
            keys = (
                ["string_a", "string_b"]
                if name == "StringConcatenate"
                else sorted(
                    key
                    for key in inputs
                    if type(key) is str and re.fullmatch(r"text_[a-z]", key)
                )
            )
            delimiter = inputs.get(
                "delimiter", "" if name == "StringConcatenate" else " "
            )
            text_size(delimiter)
            clean_value = inputs.get("clean_whitespace", "true")
            if name == "Text Concatenate" and type(clean_value) not in (str, bool):
                raise _Unknown("Invalid whitespace mode")
            clean = name == "Text Concatenate" and (
                clean_value is True
                or type(clean_value) is str
                and clean_value.lower() == "true"
            )
            if not keys:
                raise _Unknown("Missing concatenation inputs")
            parts, sources, size = [], [], 0
            for input_name in keys:
                child, refs = self.text(
                    inputs.get(input_name, ""), "string", depth + 1, active
                )
                part = child.effective.value.strip() if clean else child.effective.value
                if part or name == "StringConcatenate":
                    size += text_size(part) + (text_size(delimiter) if parts else 0)
                    if size > 65536:
                        raise _Unknown("Text field limit reached")
                    parts.append(part)
                sources.extend(refs)
                if len(sources) > 64:
                    raise _Unknown("Source reference limit reached")
            self.transformed = self.transformed or bool(sources)
            text = delimiter.join(parts)
            return self.snapshot(text, text, "", ""), tuple(sources)
        adapter = _DEFAULT_ADAPTERS.get((name, slot))
        if adapter is None or adapter.kind != kind:
            raise _Unknown("Unsupported node semantics or output slot")
        child, sources = self.text(
            inputs.get(adapter.input_name), "string", depth + 1, active
        )
        if name in _PM:
            sources = (SourceRef(node_id, name, slot),)
        if adapter.compose:
            before, before_sources = self.text(
                inputs.get("prepend_text", ""), "string", depth + 1, active
            )
            after, after_sources = self.text(
                inputs.get("append_text", ""), "string", depth + 1, active
            )
            raw = child.effective.value
            prepend, append = before.effective.value, after.effective.value
            effective = " ".join(
                part for part in (prepend.strip(), raw, append.strip()) if part
            )
            text_size(effective)
            snapshot = self.snapshot(raw, effective, prepend, append)
            if name not in _PM:
                sources += before_sources + after_sources
        elif name in _PM:
            snapshot = self.snapshot(
                child.effective.value, child.effective.value, "", ""
            )
        else:
            snapshot = child
        if len(sources) > 64:
            raise _Unknown("Source reference limit reached")
        return snapshot, sources

    @staticmethod
    def snapshot(raw: str, effective: str, prepend: str, append: str) -> PromptSnapshot:
        return PromptSnapshot(
            *(
                TextEvidence(value, "graph-inferred", "graph", None)
                for value in (raw, effective, prepend, append)
            )
        )

    def role(self, value: object) -> GraphRole:
        try:
            self.transformed = False
            snapshot, sources = self.text(value, "conditioning")
            return GraphRole(
                sources, snapshot, "source-transformed" if self.transformed else ""
            )
        except ValueError as error:
            reason = str(error) if isinstance(error, _Unknown) else "Invalid text value"
            return GraphRole((), None, reason)

    def anchors(self, node_id: str, depth: int = 1) -> tuple[str, ...]:
        if node_id in self.selected_seen:
            return ()
        self.selected_seen.add(node_id)
        name, inputs = self.node(node_id, depth)
        anchors = [node_id] if name in _SAMPLERS else []
        for value in inputs.values():
            if type(value) not in (tuple, list):
                continue
            parent, _ = _link(value)
            anchors.extend(self.anchors(parent, depth + 1))
            if len(anchors) > 16:
                raise _Unknown("Recipe context limit reached")
        return tuple(anchors)


def project_contexts(graph: object, outputs: object) -> GraphProjection:
    """Keep declared roles and graph inference, never evidence of consumption.

    Sources are PM capture points. An inferred snapshot may compose text; its
    presence never promotes a source observation to recorded effective text.
    """
    try:
        if type(graph) is not dict or len(graph) > 500:
            raise _Unknown("Invalid or oversized graph")
        normalized = {_identifier(key): value for key, value in graph.items()}
        if len(normalized) != len(graph):
            raise _Unknown("Duplicate graph identifiers")
        resolver = _Projector(normalized)
        if outputs is None:
            anchors = [
                key
                for key, value in normalized.items()
                if type(value) is dict
                and type(value.get("class_type")) is str
                and value["class_type"] in _SAMPLERS
            ]
        elif type(outputs) in (list, tuple) and len(outputs) <= 500:
            anchors = []
            for output in outputs:
                anchors.extend(resolver.anchors(_identifier(output)))
            anchors = list(dict.fromkeys(anchors))
        else:
            raise _Unknown("Invalid output selection")
        if len(anchors) > 16:
            raise _Unknown("Recipe context limit reached")
        recipes, sources = [], []
        for anchor in anchors:
            _, inputs = resolver.node(anchor, 1)
            positive = resolver.role(inputs.get("positive"))
            negative = resolver.role(inputs.get("negative"))
            recipes.append(DeclaredRecipe(anchor, positive, negative))
            sources.extend(positive.sources + negative.sources)
        sources = tuple(dict.fromkeys(sources))
        if len(sources) > 64:
            raise _Unknown("Source reference limit reached")
        reason = "" if recipes else "No selected sampler anchors"
        if any(
            role.reason
            for recipe in recipes
            for role in (recipe.positive, recipe.negative)
        ):
            reason = "Some roles require inferred or unresolved text"
        return GraphProjection(tuple(recipes), sources, reason)
    except ValueError as error:
        reason = str(error) if isinstance(error, _Unknown) else "Invalid text value"
        return GraphProjection((), (), reason)
