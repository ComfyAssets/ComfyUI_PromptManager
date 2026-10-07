"""Find which PromptManager nodes feed a sampler's positive input in a ComfyUI graph.

Works on the API-format prompt graph ({node_id: {"class_type", "inputs"}}) that
ComfyUI queues and embeds in saved images. Usage counting and image linking use it
so that negative-prompt PromptManager nodes never count as "the prompt" of a run.

Pure functions with no ComfyUI imports, so they are unit-testable. Mirrors the
link-tracing rules of web/js/comfy-metadata.js.
"""

import re
from typing import Any, Dict, List, Optional

PROMPT_MANAGER_TYPES = frozenset({"PromptManager", "PromptManagerText"})

# Upper bound on nodes expanded per graph; queued and embedded graphs are untrusted
MAX_VISITS = 500

# Inputs that carry positive conditioning or prompt text towards a sampler
_CONDITIONING_KEY = re.compile(r"^conditioning(_\w+)?$")
_TEXT_KEY = re.compile(r"^(text|string|prompt)(_?[a-z0-9]+)?$", re.IGNORECASE)


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


def _positive_roots(graph: Dict[str, Any]) -> List[Any]:
    """Links into samplers' positive inputs (KSampler, CFGGuider, BasicGuider, ...)."""
    roots = []
    for node in graph.values():
        inputs = _inputs(node)
        if not _is_link(inputs.get("model")):
            continue
        if _is_link(inputs.get("positive")) and _is_link(inputs.get("negative")):
            roots.append(inputs["positive"])
        elif (
            _is_link(inputs.get("conditioning"))
            and "guider" in str(node.get("class_type", "")).lower()
        ):
            roots.append(inputs["conditioning"])
    return roots


def _upstream_links(inputs: Dict[str, Any]) -> List[Any]:
    """Inputs to follow from a pass-through or encoder node, never `negative`."""
    links = []
    for key, value in inputs.items():
        if not _is_link(value):
            continue
        if key == "positive" or _CONDITIONING_KEY.match(key) or _TEXT_KEY.match(key):
            links.append(value)
    return links


def positive_prompt_nodes(graph: Any) -> List[str]:
    """Ids of PromptManager/PromptManagerText nodes that feed a positive input.

    Ordered by discovery, each id once. Malformed graphs yield an empty list.
    """
    if not isinstance(graph, dict):
        return []

    found: List[str] = []
    seen = set()
    stack = list(reversed(_positive_roots(graph)))
    while stack and len(seen) < MAX_VISITS:
        node_id = str(stack.pop()[0])
        if node_id in seen:
            continue
        seen.add(node_id)
        node = graph.get(node_id)
        if not isinstance(node, dict):
            continue
        if node.get("class_type") in PROMPT_MANAGER_TYPES:
            found.append(node_id)
            continue
        stack.extend(reversed(_upstream_links(_inputs(node))))
    return found


def run_prompt_nodes(graph: Any) -> List[str]:
    """Prompt nodes that represent a run: the positive ones, if any can be found.

    When no PromptManager node can be traced to a positive input (an unrecognised
    custom sampler, say), every PromptManager node counts, as before 3.2.4, so an
    exotic workflow never silently loses usage counting or image linking.
    """
    positive = positive_prompt_nodes(graph)
    if positive or not isinstance(graph, dict):
        return positive
    return [
        str(node_id)
        for node_id, node in graph.items()
        if isinstance(node, dict) and node.get("class_type") in PROMPT_MANAGER_TYPES
    ]


def _join_text_inputs(
    inputs: Dict[str, Any], keys: List[str], resolve
) -> Optional[str]:
    """WAS-style join: resolve each key, optionally strip, skip empty, join by delimiter."""
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
    seen = set()

    def resolve(value: Any) -> Optional[str]:
        if isinstance(value, str):
            return value
        if not _is_link(value) or len(seen) >= MAX_VISITS:
            return None
        source_id = str(value[0])
        if source_id in seen:
            return None
        seen.add(source_id)
        source = graph.get(source_id)
        if not isinstance(source, dict):
            return None
        return _resolve_string_node(
            str(source.get("class_type", "")), _inputs(source), resolve
        )

    seen.add(str(node_id))
    text = resolve(_inputs(graph.get(str(node_id))).get("text"))
    return text.strip() if isinstance(text, str) and text.strip() else None
