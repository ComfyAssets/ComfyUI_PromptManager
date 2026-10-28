"""Find which PromptManager nodes feed a sampler's positive input in a ComfyUI graph.

Works on the API-format prompt graph ({node_id: {"class_type", "inputs"}}) that
ComfyUI queues and embeds in saved images. Usage counting and image linking use it
so that negative-prompt PromptManager nodes never count as "the prompt" of a run.

Pure functions with no ComfyUI imports, so they are unit-testable. Mirrors the
link-tracing rules of web/js/comfy-metadata.js.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

PROMPT_MANAGER_TYPES = frozenset(
    {
        "PromptManager",
        "PromptManagerText",
        "PromptManagerFocused",
        "PromptManagerSplit",
        "PromptManagerPair",
    }
)

# A node's switch (#90): False means PromptManager ignores that node's runs
SAVE_INPUT = "save_to_library"
# Core nodes whose output is a known boolean: (class_type) -> input holding it
_BOOLEAN_SOURCES = {"PrimitiveBoolean": "value"}

# Upper bound on nodes expanded per graph; queued and embedded graphs are untrusted
MAX_VISITS = 500
# Longest text resolve_text will build; StringConcatenate chains can double
# the text at every level, so anything larger is treated as unknowable
MAX_TEXT_LENGTH = 100_000
# Deepest chain of string nodes resolve_text follows (recursion stays bounded)
MAX_DEPTH = 64

# Inputs that carry positive conditioning or prompt text towards a sampler
_CONDITIONING_KEY = re.compile(r"^conditioning(_\w+)?$")
_TEXT_KEY = re.compile(r"^(text|string|prompt)(_?[a-z0-9]+)?$", re.IGNORECASE)
# Input names that carry a negative prompt (text_negative, neg_text, negative_prompt)
_NEGATIVE_KEY = re.compile(r"(^|_)neg(ative)?($|_)", re.IGNORECASE)


def follows_input(key: Any) -> bool:
    """Whether an input name carries positive conditioning or prompt text."""
    if not isinstance(key, str) or _NEGATIVE_KEY.search(key):
        return False
    return key == "positive" or bool(
        _CONDITIONING_KEY.match(key) or _TEXT_KEY.match(key)
    )


def _is_link(value: Any) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) == 2
        and isinstance(value[0], (str, int))
        and isinstance(value[1], int)
    )


def _inputs(node: Any) -> Dict[str, Any]:
    inputs = node.get("inputs") if isinstance(node, dict) else None
    return inputs if isinstance(inputs, dict) else {}


def _as_boolean(value: Any) -> bool:
    """ComfyUI's coercion of a BOOLEAN input before the node runs."""
    if isinstance(value, dict) and "__value__" in value:
        value = value["__value__"]
    return bool(value)


def save_setting(graph: Any, node_id: Any) -> Optional[bool]:
    """A PromptManager node's ``save_to_library`` as the node will receive it.

    True when the input is missing (every workflow and image from before the
    switch), the literal coerced like ComfyUI does, the value of a linked core
    ``PrimitiveBoolean``, and None when the switch is wired from a node whose
    output cannot be known from the graph.
    """
    node = graph.get(str(node_id)) if isinstance(graph, dict) else None
    inputs = _inputs(node)
    if SAVE_INPUT not in inputs:
        return True
    value = inputs[SAVE_INPUT]
    if not _is_link(value):
        return _as_boolean(value)
    source = graph.get(str(value[0]))
    if not isinstance(source, dict) or value[1] != 0:
        return None
    key = _BOOLEAN_SOURCES.get(str(source.get("class_type", "")))
    source_inputs = _inputs(source)
    if key is None or key not in source_inputs or _is_link(source_inputs[key]):
        return None
    return _as_boolean(source_inputs[key])


def opted_out(graph: Any, node_id: Any) -> bool:
    """Whether a PromptManager node's switch is off: PromptManager ignores it."""
    return save_setting(graph, node_id) is False


def side_opted_out(graph: Any, side: Any) -> bool:
    """Whether a resolved side's path passes a PromptManager node that is off.

    ``side`` is a resolver side (``positive`` or ``negative`` of a resolved
    pair): its text must not be stored when any PromptManager node on its
    provenance path has its switch off or wired from an unreadable node (#90).
    """
    if not isinstance(graph, dict) or not isinstance(side, dict):
        return False
    provenance = side.get("provenance")
    path = provenance.get("path") if isinstance(provenance, dict) else None
    for step in path if isinstance(path, list) else []:
        node_id = step.get("node_id") if isinstance(step, dict) else None
        node = graph.get(str(node_id)) if node_id is not None else None
        if (
            isinstance(node, dict)
            and node.get("class_type") in PROMPT_MANAGER_TYPES
            and save_setting(graph, node_id) is not True
        ):
            return True
    return False


def _workflow_node_opted_out(node: Any) -> bool:
    """A saved-workflow PromptManager node whose widget values hold ``False``.

    No other PromptManager widget holds a boolean, so the value is the switch.
    """
    if not isinstance(node, dict):
        return False
    if (node.get("type") or node.get("class_type")) not in PROMPT_MANAGER_TYPES:
        return False
    values = node.get("widgets_values")
    return isinstance(values, list) and any(value is False for value in values)


def metadata_opted_out(metadata: Any, *, unreadable: bool = False) -> bool:
    """Whether a file's metadata says its run's prompt does not save (#90).

    The executed graph (``prompt``) decides when present: a node of
    ``positive_opt_outs``, or with ``unreadable`` also a run prompt whose
    switch is wired from a node the graph cannot read. Without one, the
    saved workflow (``workflow`` nodes or the extractor's
    ``text_encoder_nodes``) is opted out when a PromptManager node stores
    ``False``.
    """
    if not isinstance(metadata, dict):
        return False
    graph = metadata.get("prompt")
    if isinstance(graph, dict):
        if positive_opt_outs(graph):
            return True
        return unreadable and any(
            save_setting(graph, node_id) is None for node_id in run_prompt_nodes(graph)
        )
    workflow = metadata.get("workflow")
    nodes = list(metadata.get("text_encoder_nodes") or [])
    if isinstance(workflow, dict) and isinstance(workflow.get("nodes"), list):
        nodes.extend(workflow["nodes"])
    return any(_workflow_node_opted_out(node) for node in nodes)


def _positive_roots(graph: Dict[str, Any]) -> List[Any]:
    """Links into samplers' positive inputs (KSampler, CFGGuider, BasicGuider, ...).

    A sampler is a node taking a model plus positive/negative conditioning, or a
    guider taking a model plus conditioning. Nodes named like a sampler also count
    without a model link, so a negative-only graph is never mistaken for one with
    no sampler at all.
    """
    roots = []
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        inputs = _inputs(node)
        class_name = str(node.get("class_type", "")).lower()
        has_model = _is_link(inputs.get("model"))
        if _is_link(inputs.get("positive")) and _is_link(inputs.get("negative")):
            if has_model or "sampler" in class_name:
                roots.append(inputs["positive"])
        elif has_model and _is_link(inputs.get("conditioning")):
            if "guider" in class_name:
                roots.append(inputs["conditioning"])
    return roots


def _upstream_links(inputs: Dict[str, Any]) -> List[Any]:
    """Inputs to follow from a pass-through or encoder node, never `negative`."""
    links = []
    for key, value in inputs.items():
        if not _is_link(value):
            continue
        if follows_input(key):
            links.append(value)
    return links


def has_sampler(graph: Any) -> bool:
    """Whether the graph contains a node recognised as a sampler or guider."""
    return isinstance(graph, dict) and bool(_positive_roots(graph))


def positive_prompt_components(graph: Any) -> List[Tuple[str, str]]:
    """Node/input identities reaching actual sampler positive wires.

    A node whose switch is off is not one, and nothing upstream of it is
    followed (#90): see ``positive_opt_outs``.
    """
    return _walk_positive(graph)[0]


def _walk_positive(graph: Any) -> Tuple[List[Tuple[str, str]], List[str]]:
    """(components of enabled nodes, opted-out nodes) on sampler positive wires."""
    if not isinstance(graph, dict):
        return [], []

    found: List[Tuple[str, str]] = []
    skipped: List[str] = []
    seen = set()
    stack = list(reversed(_positive_roots(graph)))
    while stack and len(seen) < MAX_VISITS:
        link = stack.pop()
        node_id, slot = str(link[0]), link[1]
        if (node_id, slot) in seen:
            continue
        seen.add((node_id, slot))
        node = graph.get(node_id)
        if not isinstance(node, dict):
            continue
        if node.get("class_type") in PROMPT_MANAGER_TYPES:
            if opted_out(graph, node_id):
                if node_id not in skipped:
                    skipped.append(node_id)
                continue
            field = "text"
            if node.get("class_type") == "PromptManagerPair":
                field = {
                    0: "text",
                    1: "negative_text",
                    2: "text",
                    3: "negative_text",
                }.get(slot)
                if field is None:
                    continue
            identity = (node_id, field)
            if identity not in found:
                found.append(identity)
            continue
        stack.extend(reversed(_upstream_links(_inputs(node))))
    return found, skipped


def _sampler_less_nodes(graph: Dict[str, Any]) -> List[str]:
    """Every PromptManager node of a graph without a recognisable sampler."""
    return [
        str(node_id)
        for node_id, node in graph.items()
        if isinstance(node, dict) and node.get("class_type") in PROMPT_MANAGER_TYPES
    ]


def positive_opt_outs(graph: Any) -> List[str]:
    """PromptManager nodes with their switch off that would be a run's prompt.

    Those on a sampler's positive wires, or, in a graph without a recognisable
    sampler, any. A run with one of them links no file to any prompt (#90):
    the file's graph cannot always say which sampler wrote it.
    """
    if not isinstance(graph, dict):
        return []
    found, skipped = _walk_positive(graph)
    if found or skipped or has_sampler(graph):
        return skipped
    return [
        node_id for node_id in _sampler_less_nodes(graph) if opted_out(graph, node_id)
    ]


def positive_prompt_nodes(graph: Any) -> List[str]:
    """Nodes whose main editor reaches a sampler's positive input."""
    return list(
        dict.fromkeys(
            node_id
            for node_id, field in positive_prompt_components(graph)
            if field == "text"
        )
    )


def run_prompt_components(graph: Any) -> List[Tuple[str, str]]:
    """Actual positive components, retaining standalone main-editor fallback."""
    components, skipped = _walk_positive(graph)
    if components or skipped or not isinstance(graph, dict) or has_sampler(graph):
        return components
    return [
        (node_id, "text")
        for node_id in _sampler_less_nodes(graph)
        if not opted_out(graph, node_id)
    ]


def run_prompt_nodes(graph: Any) -> List[str]:
    """Prompt nodes that represent a run: the positive ones, if any can be found.

    When the graph has no recognisable sampler (an unrecognised custom sampler,
    say), every PromptManager node counts, as before 3.2.4, so an exotic workflow
    never silently loses usage counting or image linking. When a sampler exists
    and no PromptManager node feeds its positive input, nothing counts: the only
    PromptManager nodes are negative prompts. A node whose switch is off never
    counts (#90).
    """
    return list(
        dict.fromkeys(
            node_id
            for node_id, field in run_prompt_components(graph)
            if field == "text"
        )
    )


def _join_text_inputs(
    inputs: Dict[str, Any], keys: List[str], resolve
) -> Optional[str]:
    """WAS-style join: resolve each key, optionally strip, skip empty, join by
    delimiter."""
    delimiter = inputs.get("delimiter", " ")
    if not isinstance(delimiter, str):
        return None
    clean = str(inputs.get("clean_whitespace", "true")).lower() == "true"
    parts = []
    for key in keys:
        value = resolve(inputs.get(key, ""))
        if value is None:
            return None
        value = value.strip() if clean else value
        if value:
            parts.append(value)
    return delimiter.join(parts)


def _resolve_string_node(
    class_type: str, inputs: Dict[str, Any], resolve
) -> Optional[str]:
    """Output of a known pure string node, or None when the type isn't known."""
    if class_type in ("PrimitiveString", "PrimitiveStringMultiline"):
        return resolve(inputs.get("value"))
    if class_type in ("ShowText|pysssss", "Text Multiline", "String Literal"):
        return resolve(inputs.get("text", inputs.get("string")))
    if class_type == "StringConcatenate":
        a, b = resolve(inputs.get("string_a", "")), resolve(inputs.get("string_b", ""))
        delimiter = inputs.get("delimiter", "")
        if a is None or b is None or not isinstance(delimiter, str):
            return None
        return f"{a}{delimiter}{b}"
    if class_type == "Text Concatenate":
        keys = sorted(k for k in inputs if re.fullmatch(r"text_[a-z]", k))
        return _join_text_inputs(inputs, keys, resolve)
    return None


def resolve_text(graph: Any, node_id: str) -> Optional[str]:
    """A prompt node's `text` as it will be at run time, if knowable from the graph.

    Typed text, or text produced by known pure string nodes (core primitives and
    StringConcatenate, WAS Text Concatenate/Multiline, pysssss ShowText). Anything
    else, including PromptSearchList batches, returns None rather than a guess.
    Callers compare the result with the text the node actually received.
    """
    if not isinstance(graph, dict):
        return None
    # Cycle detection is scoped to the current path so a node read by two
    # branches (a diamond, or StringConcatenate(string_a=X, string_b=X))
    # resolves in both. Each node's text is computed once and memoised, so a
    # chain of diamonds costs one expansion per node rather than one per path,
    # and the shared visit budget bounds total work whatever the shape. A node
    # that fails because of a cycle fails from every path (the cycle is in the
    # graph, not in the walk), so memoising None is sound.
    path = {str(node_id)}
    memo: Dict[str, Optional[str]] = {}
    budget = [MAX_VISITS]

    def resolve(value: Any) -> Optional[str]:
        if isinstance(value, str):
            return value if len(value) <= MAX_TEXT_LENGTH else None
        if not _is_link(value):
            return None
        source_id = str(value[0])
        if source_id in memo:
            return memo[source_id]
        if source_id in path or budget[0] <= 0 or len(path) > MAX_DEPTH:
            return None
        budget[0] -= 1
        source = graph.get(source_id)
        if not isinstance(source, dict):
            memo[source_id] = None
            return None
        path.add(source_id)
        try:
            text = _resolve_string_node(
                str(source.get("class_type", "")), _inputs(source), resolve
            )
        finally:
            path.discard(source_id)
        if isinstance(text, str) and len(text) > MAX_TEXT_LENGTH:
            text = None
        memo[source_id] = text
        return text

    text = resolve(_inputs(graph.get(str(node_id))).get("text"))
    return text.strip() if isinstance(text, str) and text.strip() else None


def intrinsic_pair_nodes(graph: Any) -> List[str]:
    """Paired nodes whose matching outputs feed the same sampler, or standalone.

    A pair whose switch is off is never one (#90).
    """
    return [
        node_id
        for node_id in _intrinsic_pair_nodes(graph)
        if not opted_out(graph, node_id)
    ]


def _intrinsic_pair_nodes(graph: Any) -> List[str]:
    if not isinstance(graph, dict) or len(graph) > MAX_VISITS:
        return []
    if not has_sampler(graph):
        return [
            str(key)
            for key, value in graph.items()
            if isinstance(value, dict)
            and value.get("class_type") == "PromptManagerPair"
        ]

    def origin(link: Any, role: str, kind: str = "conditioning") -> Optional[str]:
        seen = set()
        while _is_link(link) and len(seen) < MAX_DEPTH:
            node_id, slot = str(link[0]), link[1]
            if (node_id, slot, kind) in seen:
                return None
            seen.add((node_id, slot, kind))
            node = graph.get(node_id)
            if not isinstance(node, dict):
                return None
            name, inputs = node.get("class_type"), _inputs(node)
            if name == "PromptManagerPair":
                expected = {
                    ("positive", "conditioning"): 0,
                    ("negative", "conditioning"): 1,
                    ("positive", "string"): 2,
                    ("negative", "string"): 3,
                }
                return node_id if slot == expected[(role, kind)] else None
            if name == "CLIPTextEncode" and slot == 0 and kind == "conditioning":
                link, kind = inputs.get("text"), "string"
            elif name == "Reroute" and slot == 0:
                keys = [key for key in ("value", "input", "in") if key in inputs]
                if len(keys) != 1:
                    return None
                link = inputs[keys[0]]
            else:
                return None
        return None

    found = []
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        inputs = _inputs(node)
        if not _is_link(inputs.get("positive")) or not _is_link(inputs.get("negative")):
            continue
        if (
            not _is_link(inputs.get("model"))
            and "sampler" not in str(node.get("class_type", "")).lower()
        ):
            continue
        positive = origin(inputs["positive"], "positive")
        if (
            positive
            and positive == origin(inputs["negative"], "negative")
            and positive not in found
        ):
            found.append(positive)
    return found
