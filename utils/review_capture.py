"""Capture-bound Needs review items for ambiguous files (#249 E2).

The output scan and the live monitor hand every file the ingestion resolver
finds ``ambiguous`` to :func:`record_ambiguous_file`. It captures the file
(the bounded private copy of ``utils.media_capture``, hashed while copied),
reads the metadata again from the capture, resolves it with the default
(ingestion) profile and stores only an ``ambiguous`` result, bound to the
capture's byte record. The evidence a reviewer chooses from is therefore the
evidence of exactly those bytes. An ambiguous result is stored only when it
is ``reviewable``: at least two candidates carry a resolved prompt text. With
none the file could not be traced; with exactly one, the untraced branches
may still have made the file, so that text is not certain and the file stays
unresolved (the resolver never flattens unknown semantics into a guess).
Nothing here links a file, loads a model or decodes pixels; the capture is
released before the database write.

Evidence is small on purpose (startup validates every row): the resolver's
reason and, per candidate sampler, ``{sampler_id, state, reason, positive,
negative}`` where each side is ``{text, storage_text, provenance}``. A
candidate's ``hash`` is the SHA-256 of its canonical JSON without the hash.
More than ``MAX_CANDIDATES`` candidates or evidence over the 8 MiB bound make
a ``limited`` item without candidates (a result without any candidate is not
``reviewable`` and is never stored).

Inspection-profile choices (ruling a, after the maintainer's Qwen image-edit
batches): when the default profile is ambiguous without a choice, the scan
(``inspection=True``; the live monitor never) asks ``INSPECTION_PROFILE``,
which knows more encoder and string nodes. With at least two choosable
candidates there, the item is recorded from that profile: its evidence names
``profile: "inspection"`` and ``semantics``, the fingerprint of what the table
says about the graph's node classes, and its ``resolver_version`` is
``inspection_review_version(metadata)``, which folds the profile,
``RESOLVER_VERSION`` and that fingerprint together. A table edit about those
classes moves the version the way a resolver change does (I2); an edit about
other classes moves nothing. A pending item stays applicable while the bytes
still offer its chosen candidate, and a rescan's new item supersedes it only
while it is pending and undecided (never beside a decision, see
``PromptDatabase.record_review_item``). Apply re-resolves with the
profile the item recorded (``captured_review(..., item_profile(evidence))``).
The inspection profile only ever produces review choices; ingestion's own
links stay on the default profile, which never loads the table (D1).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

try:
    from ..database.library_commands import LibraryCommandError
    from ..database.review_schema import (
        MAX_CANDIDATES,
        MIN_CHOOSABLE,
        canonical_evidence,
        choosable,
    )
    from .inspection_semantics import (
        SemanticsError,
        get_semantics,
        semantics_class_version,
    )
    from .media_capture import CaptureCapacity, CaptureError
    from .prompt_inspector import MetadataReadError, read_inspection_file
    from .prompt_pair_resolver import (
        INSPECTION_PROFILE,
        RESOLVER_VERSION,
        resolve_prompt_pair,
    )
except ImportError:  # imported with the repo root on sys.path
    from database.library_commands import LibraryCommandError
    from database.review_schema import (
        MAX_CANDIDATES,
        MIN_CHOOSABLE,
        canonical_evidence,
        choosable,
    )
    from utils.inspection_semantics import (
        SemanticsError,
        get_semantics,
        semantics_class_version,
    )
    from utils.media_capture import CaptureCapacity, CaptureError
    from utils.prompt_inspector import MetadataReadError, read_inspection_file
    from utils.prompt_pair_resolver import (
        INSPECTION_PROFILE,
        RESOLVER_VERSION,
        resolve_prompt_pair,
    )

logger = logging.getLogger("prompt_manager.review_capture")

# Capture scope label: review captures are never handed to a client.
REVIEW_SCOPE = "needs-review"
# Nested ambiguous stages are flattened at most this deep.
MAX_CANDIDATE_DEPTH = 8
# The resolver profile an item's evidence was built with (``profile``).
DEFAULT = "default"
INSPECTION = "inspection"
# Inspection item versions: above every plausible ``RESOLVER_VERSION`` and,
# with 40 bits of the folded digest, below 2**53 (exact as a JSON number).
INSPECTION_VERSION_BASE = 1 << 40
_INSPECTION_VERSION_HEX = 10


# Above this many distinct node classes the whole table is the fingerprint.
MAX_FINGERPRINT_CLASSES = 1024


def graph_classes(metadata: Any) -> frozenset | None:
    """The node classes of ``metadata``'s prompt graph (empty without one).

    None when the graph names more than ``MAX_FINGERPRINT_CLASSES`` classes.
    """
    graph = metadata.get("prompt") if isinstance(metadata, dict) else None
    if not isinstance(graph, dict):
        return frozenset()
    classes = set()
    for node in graph.values():
        name = node.get("class_type") if isinstance(node, dict) else None
        if isinstance(name, str):
            classes.add(name)
            if len(classes) > MAX_FINGERPRINT_CLASSES:
                return None
    return frozenset(classes)


def semantics_version(metadata: Any = None) -> str | None:
    """The semantics table fingerprint that ``metadata``'s graph depends on.

    The table restricted to the graph's node classes
    (``inspection_semantics.class_version``), so an edit about other classes
    leaves it alone; the whole table's version without ``metadata`` or for a
    graph of very many classes. None when the table is unusable.
    """
    try:
        semantics = get_semantics()
    except SemanticsError:
        return None
    classes = graph_classes(metadata) if metadata is not None else None
    if classes is None:
        return semantics.version
    return semantics_class_version(semantics, classes)


def inspection_review_version(metadata: Any = None) -> int | None:
    """The ``resolver_version`` of an item recorded from the inspection profile.

    Folds the profile, ``RESOLVER_VERSION`` and ``semantics_version(metadata)``,
    so a resolver change or a table change about the graph's classes moves
    it. None when the table cannot be loaded.
    """
    table = semantics_version(metadata)
    if table is None:
        return None
    seed = f"{INSPECTION_PROFILE.name}:{RESOLVER_VERSION}:{table}"
    folded = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return INSPECTION_VERSION_BASE + int(folded[:_INSPECTION_VERSION_HEX], 16)


def resolve_inspection(metadata: dict) -> dict:
    """``metadata`` resolved with the inspection profile (bounded work)."""
    return resolve_prompt_pair(metadata, profile=INSPECTION_PROFILE)


def inspection_choice(metadata: dict) -> dict | None:
    """The inspection profile's ambiguous result when it offers a choice.

    The scan asks only for a file the default profile found ambiguous
    without a choice that nothing links.
    """
    if inspection_review_version(metadata) is None:
        return None
    resolution = resolve_inspection(metadata)
    if resolution.get("state") != "ambiguous" or not reviewable(resolution):
        return None
    return resolution


def item_profile(evidence: Any) -> str:
    """The profile an item's stored evidence was built with."""
    if isinstance(evidence, dict) and evidence.get("profile") == INSPECTION:
        return INSPECTION
    return DEFAULT


def item_versions(metadata: Any = None) -> tuple:
    """The item versions a file with ``metadata`` may already be queued under.

    Without ``metadata`` (the monitor), only the ingestion version.
    """
    version = inspection_review_version(metadata) if metadata is not None else None
    return (RESOLVER_VERSION,) if version is None else (RESOLVER_VERSION, version)


def _same_records(captured: dict, scanned: dict) -> bool:
    """True when the capture holds the records the scan resolved."""
    keys = (set(captured) | set(scanned)) - {"file_info"}
    return all(captured.get(key) == scanned.get(key) for key in keys)


def record_ambiguous_file(
    db,
    store,
    root,
    root_index: int,
    relative: str,
    *,
    inspection: bool = False,
    scanned: tuple | None = None,
) -> str:
    """Persist the ambiguous evidence of one rooted file.

    With ``inspection`` (the scan), a default result without a choice is
    resolved again with the inspection profile, and recorded from it when
    that offers a choice; the live monitor passes False. ``scanned`` is the
    scan's ``(metadata, inspection_resolution_or_None)`` for the file: its
    metadata names the item versions that count, and when the capture holds
    the same records its resolution is reused instead of resolving again.

    A new item supersedes the file's other items of the same bytes (an older
    resolver or table version) when every one is still pending and
    undecided; when one carries a decision, nothing new is stored.

    Returns ``recorded`` (new item with candidates), ``limited`` (new item
    without candidates), ``duplicate`` (these bytes already carry this
    evidence, or the file's current stat is the verified fingerprint of
    bytes an item cites, so it is not even copied, and that item is still
    pending), ``decided`` (as ``duplicate``, but the item carries a decision,
    so the file is not in the queue), ``untraced`` (ambiguous in its captured
    bytes, but not ``reviewable``: nothing to choose, nothing stored) or
    ``skipped`` (the file
    changed, vanished, is not ambiguous in its captured bytes, or storage was
    restored meanwhile).
    ``CaptureCapacity`` and database errors propagate to the caller, which
    logs and counts them.
    """
    versions = item_versions(scanned[0] if scanned is not None else None)
    spelled_path, queued = _queued_spelling(db, root, relative, versions)
    if queued is not None:
        return _already_queued(db, *queued, versions)
    epoch = db.get_execution_epoch()
    try:
        # Background: the last worker stays free for an interactive request.
        capture = store.capture(
            root,
            relative,
            epoch=epoch,
            scope=REVIEW_SCOPE,
            root_index=root_index,
            background=True,
        )
    except CaptureCapacity:
        raise
    except CaptureError as error:
        logger.debug("Review capture skipped (%s): %s", error.code, error)
        return "skipped"
    try:
        captured = captured_review(store, capture)
    finally:
        store.release(capture.token)
    resolution = captured[1] if captured is not None else None
    if resolution is None or resolution.get("state") != "ambiguous":
        return "skipped"
    profile, version = DEFAULT, RESOLVER_VERSION
    if not reviewable(resolution):
        version = inspection_review_version(captured[0]) if inspection else None
        if version is None:
            return "untraced"
        profile = INSPECTION
        if (
            scanned is not None
            and scanned[1] is not None
            and _same_records(captured[0], scanned[0])
        ):
            resolution = scanned[1]  # the same records: no second run
        else:
            # Heavier, so only for a file ambiguous with nothing to choose.
            resolution = resolve_inspection(captured[0])
        if resolution.get("state") != "ambiguous" or not reviewable(resolution):
            return "untraced"
    evidence, count, limited = review_evidence(
        resolution, profile, version, captured[0]
    )
    try:
        inserted = db.record_review_item(
            locator=capture.locator,
            spelled_path=spelled_path,
            sha256=capture.sha256,
            size=capture.size,
            mtime_ns=capture.mtime_ns,
            epoch=capture.epoch,
            resolver_version=version,
            evidence=evidence,
            candidate_count=count,
            limited=limited,
        )
    except LibraryCommandError:
        return "skipped"
    if not inserted:
        return _already_queued(
            db, capture.locator, capture.size, capture.mtime_ns, versions
        )
    return "limited" if limited else "recorded"


def _already_queued(db, locator: str, size: int, mtime_ns: int, versions) -> str:
    """``duplicate`` while an existing item is pending, else ``decided``."""
    for version in versions:
        if db.review_pending_at(locator, size, mtime_ns, version):
            return "duplicate"
    return "decided"


def _queued_spelling(db, root, relative: str, versions) -> tuple[str, tuple | None]:
    """The file's spelling, and ``(locator, size, mtime_ns)`` when queued.

    The second value is None unless the file's current stat is already
    queued.

    The spelling is the capture's own candidate path (the real root joined
    with the listed relative path), so it folds to the capture's locator. A
    file whose size and ``mtime_ns`` equal the verified fingerprint of bytes
    an item already cites is not copied or hashed again (maintainer ruling:
    the rescan stays cheap and the file stays in the default scope); its
    items take the current spelling. A stat failure leaves the decision to
    the capture. ``versions`` are the item versions that count; bytes whose
    item carries a decision count under any version (a new version never
    adds an item beside a decision).
    """
    spelled = os.path.join(os.path.realpath(os.fspath(root)), *relative.split("/"))
    try:
        facts = os.lstat(spelled)
    except OSError:
        return spelled, None
    locator = os.path.normcase(spelled)
    if db.review_decided_at(locator, facts.st_size, facts.st_mtime_ns) or any(
        db.review_fingerprint_queued(locator, facts.st_size, facts.st_mtime_ns, v)
        for v in versions
    ):
        # Not copied again, but a case-only rename keeps the stat: record
        # the current spelling so the item's target still opens.
        db.refresh_review_spelling(locator, spelled)
        return spelled, (locator, facts.st_size, facts.st_mtime_ns)
    return spelled, None


def captured_review(store, capture, profile: str = DEFAULT) -> tuple[dict, dict] | None:
    """``(metadata, resolution)`` of the capture's own bytes, else None.

    ``metadata`` is what the scan hands the ingester: prompt and workflow
    JSON-decoded and ``file_info`` (size, dimensions, format) from the
    capture. ``resolution`` comes from ``profile`` (``DEFAULT`` or
    ``INSPECTION``). None when the capture is gone or carries no prompt
    metadata.
    """
    if store.acquire(capture.token) is None:
        return None
    try:
        try:
            read = read_inspection_file(Path(capture.path))
        except MetadataReadError:
            return None
    finally:
        store.release_use(capture.token)
    canonical = dict(read.metadata)
    for key in ("prompt", "workflow"):
        if isinstance(canonical.get(key), str):
            try:
                canonical[key] = json.loads(canonical[key])
            except (ValueError, TypeError, RecursionError):
                # Malformed metadata stays as text for the resolver to reject.
                pass
    if not canonical.get("prompt") and not canonical.get("parameters"):
        return None
    info = {"size": capture.size}
    if read.width is not None and read.height is not None:
        info["dimensions"] = [read.width, read.height]
    if read.format is not None:
        info["format"] = read.format
    canonical["file_info"] = info
    if profile == INSPECTION:
        return canonical, resolve_inspection(canonical)
    return canonical, resolve_prompt_pair(canonical)


def reviewable(resolution: Any) -> bool:
    """True when an ambiguous result offers a choice: ``MIN_CHOOSABLE`` of its
    selectable candidates (nested ambiguous stages flattened) are choosable.

    Counted before the evidence is bounded, so a ``limited`` item (more than
    ``MAX_CANDIDATES`` candidates) with prompts to choose is still queued.
    """
    if not isinstance(resolution, dict) or resolution.get("state") != "ambiguous":
        return False
    count = 0
    for candidate in _flattened(resolution.get("candidates"), 0):
        if choosable(candidate):
            count += 1
            if count >= MIN_CHOOSABLE:
                return True
    return False


def review_evidence(
    resolution: dict,
    profile: str = DEFAULT,
    version: int | None = None,
    metadata: Any = None,
) -> tuple[dict, int, bool]:
    """``(evidence, candidate_count, limited)`` for one ambiguous result.

    Inspection evidence also names its ``profile`` and ``semantics``, the
    table fingerprint of ``metadata``'s graph it was resolved with;
    ``version`` is the item's version.
    """
    base = {
        "resolver_version": RESOLVER_VERSION if version is None else version,
        "reason": _text(resolution.get("reason")),
    }
    if profile == INSPECTION:
        base["profile"] = INSPECTION
        base["semantics"] = semantics_version(metadata)
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
