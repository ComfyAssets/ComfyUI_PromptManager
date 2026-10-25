"""Prove that a tracked prompt belongs to the run that saved an image (#342).

A PromptManager node registers the text it executed under a *run key*: a digest
of the node and everything upstream of it in the queued API graph. The image
embeds the same graph, so the image monitor can compute the same key for the
image's positive prompt node. Equal keys mean the node ran with exactly the
inputs (and upstream nodes) that produced this image. That is also the identity
ComfyUI's own output cache uses: a node whose upstream is unchanged is served
from the cache, so a re-run of an unchanged upstream reproduces the same text.

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


def _links(inputs: Any) -> List[str]:
    if not isinstance(inputs, dict):
        return []
    found = []
    for value in inputs.values():
        if (
            isinstance(value, (list, tuple))
            and len(value) == 2
            and isinstance(value[0], (str, int))
            and isinstance(value[1], int)
            and not isinstance(value[1], bool)
        ):
            found.append(str(value[0]))
    return found


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
    closure: Dict[str, Any] = {}
    stack = [start]
    while stack:
        current = stack.pop()
        if current in closure:
            continue
        node = graph.get(current)
        if not isinstance(node, dict):
            return None
        if len(closure) >= MAX_VISITS:
            return None
        inputs = node.get("inputs")
        closure[current] = [node.get("class_type"), inputs]
        stack.extend(_links(inputs))
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

    Empty without an embedded API graph: an image without one can never be
    proven to belong to a tracked run.
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
        if key is not None:
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
