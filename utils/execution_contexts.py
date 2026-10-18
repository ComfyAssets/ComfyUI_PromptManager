"""Scalar contracts for bounded execution context capture."""

import hashlib
import json
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TextEvidence:
    value: str | None
    provenance: str
    source: str
    reason: str | None


@dataclass(frozen=True)
class PromptSnapshot:
    raw: TextEvidence
    effective: TextEvidence
    prepend: TextEvidence
    append: TextEvidence


@dataclass(frozen=True)
class RecipeContext:
    anchor_id: str
    positive: PromptSnapshot | None
    negative: PromptSnapshot | None
    role_provenance: str


@dataclass(frozen=True)
class ContextBundle:
    contexts: tuple[RecipeContext, ...]
    state: str
    reason: str
    format_version: int = 1


MAX_FIELD_BYTES = 64 * 1024
MAX_CONTEXT_BYTES = 256 * 1024
MAX_ATTEMPT_BYTES = 1024 * 1024


def text_size(value: str, limit: int = MAX_FIELD_BYTES) -> int:
    """Reject oversize strings before allocating encoded copies."""
    if type(value) is not str or len(value) > limit:
        raise ValueError("Invalid or oversized text")
    size = 0
    for char in value:
        code = ord(char)
        if 0xD800 <= code <= 0xDFFF:
            raise ValueError("Invalid Unicode")
        size += 1 if code < 128 else 2 if code < 2048 else 3 if code < 65536 else 4
        if size > limit:
            raise ValueError("Oversized text")
    return size


def canonical_content(kind: str, fields: tuple[str | None, ...]) -> bytes:
    text_size(kind, 128)
    if type(fields) is not tuple:
        raise ValueError("Invalid canonical fields")
    for value in fields:
        if value is not None:
            text_size(value)
    return json.dumps(
        [1, kind, *fields], ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")


def content_digest(kind: str, fields: tuple[str | None, ...]) -> str:
    return hashlib.sha256(canonical_content(kind, fields)).hexdigest()


def snapshot_digest(snapshot: PromptSnapshot) -> str:
    return content_digest(
        "snapshot",
        tuple(
            getattr(snapshot, field).value
            for field in ("raw", "effective", "prepend", "append")
        ),
    )


def context_digest(context: RecipeContext) -> str:
    return content_digest(
        "context",
        tuple(
            snapshot_digest(side) if side is not None else None
            for side in (context.positive, context.negative)
        ),
    )


BUNDLE_STATES = frozenset(
    {"complete", "partial", "ambiguous", "unavailable", "conflicting"}
)

CAPTURE_REASONS = frozenset(
    {
        "",
        "captured",
        "partial",
        "unavailable",
        "unsupported",
        "ambiguous",
        "limit_reached",
        "missing",
        "conflicting",
        "no_supported_context",
        "budget_exhausted",
        "invalid_evidence",
        "multiple_samples",
        "source_transformed",
        "field_limit",
        "graph_limit",
    }
)
UNKNOWN_REASONS = frozenset(
    {"unavailable", "unsupported", "ambiguous", "limit_reached", "missing"}
)
EVIDENCE_SOURCES = {
    "recorded": frozenset({"runtime_return", "host_cache_output"}),
    "inferred": frozenset({"graph_literal", "graph_semantics"}),
    "unknown": frozenset({"unknown"}),
}


def validate_bundle(bundle: ContextBundle) -> None:
    if (
        type(bundle) is not ContextBundle
        or type(bundle.format_version) is not int
        or bundle.format_version != 1
        or type(bundle.contexts) is not tuple
        or len(bundle.contexts) > 16
    ):
        raise ValueError("Invalid context bundle")
    total = text_size(bundle.state, 128) + text_size(bundle.reason, 128)
    if bundle.state not in BUNDLE_STATES:
        raise ValueError("Invalid bundle state")
    if bundle.reason and re.fullmatch(r"[a-z][a-z0-9_-]*", bundle.reason) is None:
        raise ValueError("Invalid bundle reason code")
    anchors = set()
    for context in bundle.contexts:
        if type(context) is not RecipeContext:
            raise ValueError("Invalid recipe context")
        size = text_size(context.anchor_id, 512)
        if len(context.anchor_id) > 128:
            raise ValueError("Oversized recipe anchor")
        if not context.anchor_id or context.anchor_id in anchors:
            raise ValueError("Duplicate or empty recipe anchor")
        anchors.add(context.anchor_id)
        size += text_size(context.role_provenance, 128)
        if context.role_provenance != "inferred":
            raise ValueError("Invalid declared role provenance")
        for snapshot in (context.positive, context.negative):
            if snapshot is None:
                continue
            if type(snapshot) is not PromptSnapshot:
                raise ValueError("Invalid prompt snapshot")
            for field in (
                snapshot.raw,
                snapshot.effective,
                snapshot.prepend,
                snapshot.append,
            ):
                if type(field) is not TextEvidence:
                    raise ValueError("Invalid text evidence")
                if (
                    type(field.provenance) is not str
                    or type(field.source) is not str
                    or field.source not in EVIDENCE_SOURCES.get(field.provenance, ())
                ):
                    raise ValueError("Invalid evidence provenance or source")
                if field.value is None:
                    if (
                        field.provenance != "unknown"
                        or type(field.reason) is not str
                        or field.reason not in UNKNOWN_REASONS
                    ):
                        raise ValueError("Unknown evidence requires a reason code")
                elif field.provenance == "unknown" or field.reason is not None:
                    raise ValueError("Known evidence requires a known provenance")
                if field.value is not None:
                    size += text_size(field.value)
                size += text_size(field.provenance, 128) + text_size(field.source, 128)
                if field.reason is not None:
                    size += text_size(field.reason, 128)
        if size > MAX_CONTEXT_BYTES:
            raise ValueError("Context payload limit reached")
        total += size
        if total > MAX_ATTEMPT_BYTES:
            raise ValueError("Attempt payload limit reached")
