"""User-facing lines for non-resolved prompt outcomes, from the closed vocabulary.

The inspector shows one line per unresolved role in place of the generic
"Prompt could not be resolved." Ambiguous and absent roles keep their status
copy, so a candidate is never presented as a confirmed prompt. The wire
projection accepts exactly the closed ``reason_code`` vocabulary and names a
blocking node only beside a code that names one.
"""

from typing import Any, Optional

from .prompt_pair_resolver import (
    EMPTY_POSITIVE_REASON,
    MULTISTAGE_UNATTRIBUTED_REASON,
    SEMANTICS_UNAVAILABLE_REASON,
)

REASON_CODES = frozenset(
    {
        "work-limit",
        "depth-limit",
        "text-limit",
        "graph-ceiling",
        "unsupported-node",
        "runtime-value",
        "composite",
        "no-sampler",
        "malformed",
        "multistage-differs",
    }
)
LIMIT_CODES = frozenset({"work-limit", "depth-limit", "text-limit", "graph-ceiling"})
# Codes whose outcome names the blocking node: ``node: {id, class_type}``.
NAMED_CODES = frozenset({"unsupported-node", "runtime-value", "composite"})
# Bound for a node ID or class name on the wire and in a line.
CLASS_NAME_LENGTH = 200
# Bound for a role's line on the wire, in UTF-16 code units (the client's count).
MESSAGE_LENGTH = 512
UNREADABLE_METADATA_REASON = "Media metadata could not be read"
_NODE_KEYS = frozenset({"id", "class_type"})

_NAMED_LINES = {
    "unsupported-node": "Prompt uses a node the inspector does not support",
    "runtime-value": "Prompt is chosen at run time",
    "composite": "Prompt combines several texts",
}
_CODED_LINES = {
    **dict.fromkeys(LIMIT_CODES, "Prompt exceeds inspection limits."),
    "no-sampler": "No supported sampler was found.",
    "malformed": "Prompt metadata in this file is malformed.",
    "multistage-differs": "Sampler stages have different prompts.",
}
# Uncoded outcomes, keyed by the resolver's exact reason.
_UNCODED_LINES = {
    EMPTY_POSITIVE_REASON: "Positive prompt is empty.",
    MULTISTAGE_UNATTRIBUTED_REASON: (
        "Several sampler stages were found; their prompts were not compared."
    ),
    SEMANTICS_UNAVAILABLE_REASON: "The inspector's node table is unavailable.",
    UNREADABLE_METADATA_REASON: "Metadata in this file could not be read.",
}


def _text(value: Any) -> Optional[str]:
    return value if isinstance(value, str) else None


def _class_name(node: Any) -> Optional[str]:
    if not isinstance(node, dict):
        return None
    name = node.get("class_type")
    return name[:CLASS_NAME_LENGTH] if isinstance(name, str) and name else None


def outcome_message(resolution: Any) -> Optional[str]:
    """One line for an unresolved outcome, or ``None`` to keep the status copy."""
    if not isinstance(resolution, dict) or resolution.get("state") != "unresolved":
        return None
    code = _text(resolution.get("reason_code"))
    if code in _NAMED_LINES:
        name = _class_name(resolution.get("node"))
        return f"{_NAMED_LINES[code]} ({name})." if name else f"{_NAMED_LINES[code]}."
    if code is None:
        return _UNCODED_LINES.get(_text(resolution.get("reason")))
    return _CODED_LINES.get(code)


def _node(code: Optional[str], node: Any) -> Optional[dict]:
    if code not in NAMED_CODES or not isinstance(node, dict) or set(node) != _NODE_KEYS:
        return None
    node_id, name = node["id"], node["class_type"]
    if not isinstance(node_id, str) or not node_id:
        return None
    if name is not None and not isinstance(name, str):
        return None
    return {
        "id": node_id[:CLASS_NAME_LENGTH],
        "class_type": name[:CLASS_NAME_LENGTH] if name else None,
    }


def project_resolution(resolution: dict) -> dict:
    """Fail closed: an unknown or contradictory code and its node are dropped."""
    code = _text(resolution.get("reason_code"))
    if code not in REASON_CODES or resolution.get("state") in ("resolved", "absent"):
        code = None
    return {
        **resolution,
        "reason_code": code,
        "node": _node(code, resolution.get("node")),
    }
