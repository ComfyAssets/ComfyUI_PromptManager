"""Prove that a tracked prompt belongs to the run that saved an image (#342).

A PromptManager node registers the text it executed under a *run key*: a digest
of the node and everything upstream of it in the queued API graph. The image
embeds the same graph, so the image monitor can compute the same key for the
image's positive prompt node. Equal keys mean the node ran with exactly the
inputs (and upstream nodes) that produced this image.

Equal keys do not always mean equal text. ComfyUI's cache signature also folds
in each node's ``IS_CHANGED``, which the key cannot see: an upstream node that
forces a re-run (a random line, an "always run" LLM) can give two runs one key
and two texts. The key is therefore never used alone: the monitor also requires
the entry to come from the run the snapshot taken when the image appeared names
(``PromptTracker.pop_for_key``, ``ImageGenerationHandler._find_prompt_from_run``).

Pure functions; the only host dependency (the executing run id) is optional.
"""

import hashlib
import json
from typing import Any, Dict, List, Optional

try:
    from .prompt_graph import MAX_VISITS, run_prompt_nodes
except ImportError:
    from utils.prompt_graph import MAX_VISITS, run_prompt_nodes

RUN_KEY_VERSION = 1


def is_link(value: Any) -> bool:
    """ComfyUI's own test (``comfy_execution.graph_utils.is_link``), exactly.

    A two-item list of a ``str`` node id and an ``int`` or ``float`` slot (a
    ``bool`` slot passes too, as it does in ComfyUI). Tuples are values.
    """
    if not isinstance(value, list):
        return False
    if len(value) != 2:
        return False
    if not isinstance(value[0], str):
        return False
    if not isinstance(value[1], int) and not isinstance(value[1], float):
        return False
    return True


def _links(inputs: Any) -> List[str]:
    if not isinstance(inputs, dict):
        return []
    return [value[0] for value in inputs.values() if is_link(value)]


def upstream_closure(
    graph: Dict[str, Any], starts: List[str], limit: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    """``{node_id: [class_type, inputs]}`` of ``starts`` and everything upstream.

    None when a node or an upstream link's node is missing, or the closure
    exceeds ``limit`` nodes.
    """
    closure: Dict[str, Any] = {}
    stack = list(starts)
    while stack:
        current = stack.pop()
        if current in closure:
            continue
        node = graph.get(current)
        if not isinstance(node, dict):
            return None
        if limit is not None and len(closure) >= limit:
            return None
        inputs = node.get("inputs")
        closure[current] = [node.get("class_type"), inputs]
        stack.extend(_links(inputs))
    return closure


def upstream_nodes(graph: Any, starts: List[str]) -> List[str]:
    """Ids of ``starts`` and every node upstream of them that exists in ``graph``.

    Used to tell which nodes a job's outputs to execute need ("run selected
    output" jobs leave the rest of the graph out). Missing nodes are skipped.
    """
    if not isinstance(graph, dict):
        return []
    found: Dict[str, None] = {}
    stack = [str(node) for node in starts]
    while stack:
        current = stack.pop()
        if current in found:
            continue
        node = graph.get(current)
        if not isinstance(node, dict):
            continue
        found[current] = None
        stack.extend(_links(node.get("inputs")))
    return list(found)


def prompt_node_key(graph: Any, node_id: Any) -> Optional[str]:
    """Digest of ``node_id`` and its whole upstream closure, or None.

    Only ``class_type`` and ``inputs`` count (titles and other UI fields do not
    change what runs). None when the graph or node is missing, an upstream link
    points at a missing node, the closure exceeds ``MAX_VISITS`` nodes, or a
    value cannot be serialized: no key means no proof.
    """
    if not isinstance(graph, dict) or node_id is None:
        return None
    start = str(node_id)
    closure = upstream_closure(graph, [start], MAX_VISITS)
    if closure is None:
        return None
    try:
        encoded = json.dumps(
            [RUN_KEY_VERSION, start, closure],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def image_run_keys(metadata: Any) -> Dict[str, str]:
    """``{node_id: run key}`` for each positive PromptManager node of an image.

    Empty without an embedded API graph, and empty when any positive node has
    no key: one unkeyable node must not leave another looking like the only
    positive prompt (a two-pass workflow would link the wrong pass).
    """
    graph = metadata.get("prompt") if isinstance(metadata, dict) else None
    if isinstance(graph, str):
        try:
            graph = json.loads(graph)
        except (TypeError, ValueError, RecursionError):
            return {}
    if not isinstance(graph, dict):
        return {}
    keys = {}
    for node_id in run_prompt_nodes(graph):
        key = prompt_node_key(graph, node_id)
        if key is None:
            return {}
        keys[node_id] = key
    return keys


def executing_run_id() -> Optional[str]:
    """The ComfyUI prompt id currently executing on this thread, if the host says.

    None outside ComfyUI or on hosts without ``get_executing_context``.
    """
    try:
        from comfy_execution.utils import get_executing_context
    except Exception:
        return None
    try:
        context = get_executing_context()
    except Exception:
        return None
    run_id = getattr(context, "prompt_id", None)
    return run_id if isinstance(run_id, str) and run_id else None
