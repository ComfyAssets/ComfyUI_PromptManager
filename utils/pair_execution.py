"""Bounded per-node execution evidence embedded in ComfyUI PNG metadata."""

import json
from typing import Any, Dict, Optional

EXECUTION_KEY = "comfyassets_prompt_pairs"
MAX_EXECUTION_RECORDS = 16
MAX_EXECUTION_NODES = 64
MAX_EXECUTION_TEXT = 100_000
MAX_EXECUTION_JSON = 500_000
_FIELDS = (
    "text",
    "negative_text",
    "positive_text",
    "negative_effective_text",
    "prepend_text",
    "append_text",
)


class ExecutionEvidenceError(ValueError):
    """Execution metadata is malformed, stale, truncated or ambiguous."""


def record_pair_execution(
    extra_pnginfo: Any,
    node_id: Any,
    *,
    text: str,
    negative_text: str,
    positive_text: str,
    negative_effective_text: str,
    prepend_text: str = "",
    append_text: str = "",
) -> None:
    """Append evidence without changing workflow or sibling extension metadata."""
    if not isinstance(extra_pnginfo, dict) or node_id is None:
        return
    envelope = extra_pnginfo.get(EXECUTION_KEY)
    if not isinstance(envelope, dict) or envelope.get("version") != 1:
        envelope = {"version": 1, "nodes": {}}
        extra_pnginfo[EXECUTION_KEY] = envelope
    if envelope.get("truncated"):
        return
    nodes = envelope.get("nodes")
    if not isinstance(nodes, dict):
        envelope["truncated"] = True
        return
    if str(node_id) not in nodes and len(nodes) >= MAX_EXECUTION_NODES:
        envelope["truncated"] = True
        return
    node = nodes.setdefault(str(node_id), {"records": []})
    if not isinstance(node, dict) or not isinstance(node.get("records"), list):
        envelope["truncated"] = True
        return
    record = {
        "text": text,
        "negative_text": negative_text,
        "positive_text": positive_text,
        "negative_effective_text": negative_effective_text,
        "prepend_text": prepend_text,
        "append_text": append_text,
    }
    if any(
        not isinstance(value, str) or len(value) > MAX_EXECUTION_TEXT
        for value in record.values()
    ):
        node["truncated"] = True
        return
    if record not in node["records"]:
        if len(node["records"]) >= MAX_EXECUTION_RECORDS:
            node["truncated"] = True
            return
        node["records"].append(record)
    try:
        if len(json.dumps(envelope, ensure_ascii=False)) > MAX_EXECUTION_JSON:
            extra_pnginfo[EXECUTION_KEY] = {
                "version": 1,
                "truncated": True,
                "nodes": {},
            }
    except (TypeError, ValueError, RecursionError):
        extra_pnginfo[EXECUTION_KEY] = {"version": 1, "truncated": True, "nodes": {}}


def read_pair_execution(metadata: Any, node_id: str) -> Optional[Dict[str, str]]:
    """Return one distinct record for a traced node; missing evidence stays absent."""
    if not isinstance(metadata, dict) or EXECUTION_KEY not in metadata:
        return None
    envelope = metadata[EXECUTION_KEY]
    if isinstance(envelope, str):
        if len(envelope) > MAX_EXECUTION_JSON:
            raise ExecutionEvidenceError("Execution evidence exceeds the size limit")
        try:
            envelope = json.loads(envelope)
        except (ValueError, TypeError, RecursionError) as error:
            raise ExecutionEvidenceError("Malformed execution evidence JSON") from error
    if not isinstance(envelope, dict) or envelope.get("version") != 1:
        raise ExecutionEvidenceError("Unsupported execution evidence format")
    nodes = envelope.get("nodes")
    if (
        envelope.get("truncated")
        or not isinstance(nodes, dict)
        or len(nodes) > MAX_EXECUTION_NODES
    ):
        raise ExecutionEvidenceError("Truncated or malformed execution evidence")
    if node_id not in nodes:
        return None
    node = nodes[node_id]
    if not isinstance(node, dict) or node.get("truncated"):
        raise ExecutionEvidenceError("Truncated or malformed node execution evidence")
    records = node.get("records")
    if (
        not isinstance(records, list)
        or not records
        or len(records) > MAX_EXECUTION_RECORDS
    ):
        raise ExecutionEvidenceError("Malformed node execution records")
    unique = {}
    for record in records:
        if not isinstance(record, dict) or any(
            not isinstance(record.get(key), str)
            or len(record[key]) > MAX_EXECUTION_TEXT
            for key in _FIELDS
        ):
            raise ExecutionEvidenceError("Malformed execution text values")
        clean = {key: record[key] for key in _FIELDS}
        if clean["negative_effective_text"] != clean["negative_text"]:
            raise ExecutionEvidenceError("Negative execution text must be unchanged")
        unique[json.dumps(clean, sort_keys=True)] = clean
    if len(unique) != 1:
        raise ExecutionEvidenceError(
            "Multiple distinct executions have no image anchor"
        )
    return next(iter(unique.values()))


def clear_pair_execution(request: Any) -> None:
    """Discard client-carried execution records before a new validated run."""
    if not isinstance(request, dict):
        return
    extra = request.get("extra_data")
    png = extra.get("extra_pnginfo") if isinstance(extra, dict) else None
    if isinstance(png, dict):
        png.pop(EXECUTION_KEY, None)
