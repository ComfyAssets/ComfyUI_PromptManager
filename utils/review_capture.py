"""Capture-bound Needs review items for ambiguous files (#249 E2).

The output scan and the live monitor hand every file the ingestion resolver
finds ``ambiguous`` to :func:`record_ambiguous_file`. It captures the file
(the bounded private copy of ``utils.media_capture``, hashed while copied),
reads the metadata again from the capture, resolves it with the default
(ingestion) profile and stores only an ``ambiguous`` result, bound to the
capture's byte record. The evidence a reviewer chooses from is therefore the
evidence of exactly those bytes. Nothing here links a file, loads a model or
decodes pixels; the capture is released before the database write.

Evidence is small on purpose (startup validates every row): the resolver's
reason and, per candidate sampler, ``{sampler_id, state, reason, positive,
negative}`` where each side is ``{text, storage_text, provenance}``. A
candidate's ``hash`` is the SHA-256 of its canonical JSON without the hash.
More than ``MAX_CANDIDATES`` candidates, none at all, or evidence over the
8 MiB bound make a ``limited`` item without candidates.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

try:
    from ..database.library_commands import LibraryCommandError
    from ..database.review_schema import MAX_CANDIDATES, canonical_evidence
    from .media_capture import CaptureCapacity, CaptureError
    from .prompt_inspector import MetadataReadError, read_inspection_file
    from .prompt_pair_resolver import resolve_prompt_pair
except ImportError:  # imported with the repo root on sys.path
    from database.library_commands import LibraryCommandError
    from database.review_schema import MAX_CANDIDATES, canonical_evidence
    from utils.media_capture import CaptureCapacity, CaptureError
    from utils.prompt_inspector import MetadataReadError, read_inspection_file
    from utils.prompt_pair_resolver import resolve_prompt_pair

logger = logging.getLogger("prompt_manager.review_capture")

# The ingestion resolver's provenance version (``database.pair_ingestion``).
RESOLVER_VERSION = 1
# Capture scope label: review captures are never handed to a client.
REVIEW_SCOPE = "needs-review"
# Nested ambiguous stages are flattened at most this deep.
MAX_CANDIDATE_DEPTH = 8


def record_ambiguous_file(db, store, root, root_index: int, relative: str) -> str:
    """Persist the ambiguous evidence of one rooted file.

    Returns ``recorded`` (new item with candidates), ``limited`` (new item
    without candidates), ``duplicate`` (these bytes already carry this
    evidence) or ``skipped`` (the file changed, vanished, is not ambiguous in
    its captured bytes, or storage was restored meanwhile).
    ``CaptureCapacity`` and database errors propagate to the caller, which
    logs and counts them.
    """
    epoch = db.get_execution_epoch()
    try:
        capture = store.capture(
            root, relative, epoch=epoch, scope=REVIEW_SCOPE, root_index=root_index
        )
    except CaptureCapacity:
        raise
    except CaptureError as error:
        logger.debug("Review capture skipped (%s): %s", error.code, error)
        return "skipped"
    try:
        resolution = _captured_resolution(store, capture)
    finally:
        store.release(capture.token)
    if resolution is None or resolution.get("state") != "ambiguous":
        return "skipped"
    evidence, count, limited = review_evidence(resolution)
    try:
        inserted = db.record_review_item(
            locator=capture.locator,
            sha256=capture.sha256,
            size=capture.size,
            mtime_ns=capture.mtime_ns,
            epoch=capture.epoch,
            resolver_version=RESOLVER_VERSION,
            evidence=evidence,
            candidate_count=count,
            limited=limited,
        )
    except LibraryCommandError:
        return "skipped"
    if not inserted:
        return "duplicate"
    return "limited" if limited else "recorded"


def _captured_resolution(store, capture) -> dict | None:
    """The ingestion resolver's result for the capture's own metadata."""
    if store.acquire(capture.token) is None:
        return None
    try:
        try:
            metadata = read_inspection_file(Path(capture.path)).metadata
        except MetadataReadError:
            return None
    finally:
        store.release_use(capture.token)
    canonical = dict(metadata)
    for key in ("prompt", "workflow"):
        if isinstance(canonical.get(key), str):
            try:
                canonical[key] = json.loads(canonical[key])
            except (ValueError, TypeError, RecursionError):
                # Malformed metadata stays as text for the resolver to reject.
                pass
    if not canonical.get("prompt") and not canonical.get("parameters"):
        return None
    return resolve_prompt_pair(canonical)


def review_evidence(resolution: dict) -> tuple[dict, int, bool]:
    """``(evidence, candidate_count, limited)`` for one ambiguous result."""
    base = {
        "resolver_version": RESOLVER_VERSION,
        "reason": _text(resolution.get("reason")),
    }
    candidates: list[dict] = []
    seen: set[str] = set()
    total = 0
    for candidate in _flattened(resolution.get("candidates"), 0):
        total += 1
        try:
            projected = _projection(candidate)
            digest = hashlib.sha256(
                canonical_evidence(projected).encode("utf-8")
            ).hexdigest()
        except (ValueError, TypeError, KeyError):
            return _limited(base, total), 0, True
        if digest in seen:
            total -= 1
            continue
        seen.add(digest)
        candidates.append({**projected, "hash": digest})
    if not candidates or len(candidates) > MAX_CANDIDATES:
        return _limited(base, total), 0, True
    evidence = {**base, "candidates": candidates}
    try:
        canonical_evidence(evidence)
    except ValueError:
        return _limited(base, total), 0, True
    return evidence, len(candidates), False


def _limited(base: dict, total: int) -> dict:
    return {**base, "limited": True, "candidate_total": total, "candidates": []}


def _flattened(candidates: Any, depth: int):
    """Selectable candidates; a nested ambiguous stage yields its own."""
    if not isinstance(candidates, list):
        return
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        nested = candidate.get("candidates")
        if (
            candidate.get("state") == "ambiguous"
            and isinstance(nested, list)
            and nested
            and depth < MAX_CANDIDATE_DEPTH
        ):
            yield from _flattened(nested, depth + 1)
        else:
            yield candidate


def _projection(candidate: dict) -> dict:
    return {
        "sampler_id": candidate.get("sampler_id"),
        "state": _text(candidate.get("state")),
        "reason": _text(candidate.get("reason")),
        "positive": _side(candidate.get("positive")),
        "negative": _side(candidate.get("negative")),
    }


def _side(side: Any) -> dict | None:
    if not isinstance(side, dict):
        return None
    text = side["text"]
    return {
        "text": text,
        "storage_text": side.get("storage_text", text),
        "provenance": side.get("provenance"),
    }


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""
