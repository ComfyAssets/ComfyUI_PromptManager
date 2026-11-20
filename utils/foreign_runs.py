"""The monitor's ladder path: foreign outputs (#247) and PromptManager runs (#659).

A foreign output (no positive PromptManager node in its graph) is decided by
the ladder the output scan uses (``utils.file_prompt.decide_file_prompt``)
with the ``MONITOR`` policy: certain files are stored at once, files only
recorded history can decide wait for their run's outputs to be sealed, and
everything else stays unlinked. A PromptManager run the image's metadata and
its tracked run cannot link takes the same path (#659 Part 3); its entries
carry the run kind ``PROMPTMANAGER``, which selects the settings gate and the
marks of a created prompt (``ladder_gate``). This module holds the parts that
do not need the monitor's waiting sets: the settings, the decision call and
the store. The waiting and the re-check stay on ``ImageGenerationHandler``.
"""

import os
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Tuple

from .file_prompt import (
    NOT_SAVED,
    NOT_SAVED_WIRED,
    PAIR,
    POSITIVE,
    Decision,
    Policy,
    decide_file_prompt,
)
from .hashing import generate_prompt_hash
from .usage_tracking import survive_switch

try:
    from ..database.pair_ingestion import RUN_MARKS, SCAN_MARKS, ingest_resolved_pair
    from ..database.run_attribution import recorded_output_lookup
    from ..database.scanned_prompts import store_scanned_prompt_or_raise
except ImportError:
    from database.pair_ingestion import RUN_MARKS, SCAN_MARKS, ingest_resolved_pair
    from database.run_attribution import recorded_output_lookup
    from database.scanned_prompts import store_scanned_prompt_or_raise

# Decisions that name a prompt to store
CERTAIN = (PAIR, POSITIVE)
# Decisions of a run whose prompt has Save to library off, or wired from a
# node the file cannot show (#90): final, nothing is stored, nothing waits
NOT_SAVED_KINDS = (NOT_SAVED, NOT_SAVED_WIRED)
# Sources certain without a recorded run: the graph alone, or its recorded saver
ANCHORED_SOURCES = ("inspector", "recorded-run")
# The run kinds of a ladder entry: an output of another workflow, or a
# PromptManager run its metadata and tracked run could not link (#659)
FOREIGN = "foreign"
PROMPTMANAGER = "promptmanager"


@dataclass(frozen=True)
class ForeignRecheck:
    """``schedule`` snapshot: the one ladder re-check of an output after its
    run's outputs were sealed. ``reference`` is the sealed output reference,
    the spelling history recorded (``recorded_output_lookup(reference=)``);
    ``kind`` is the entry's run kind (``FOREIGN`` or ``PROMPTMANAGER``)."""

    reference: Mapping[str, Any]
    kind: str = FOREIGN


def _gallery_config():
    """``GalleryConfig``, or None when the configuration cannot be imported."""
    try:
        from ..py.config import GalleryConfig
    except ImportError:
        try:
            from py.config import GalleryConfig
        except ImportError:
            return None
    return GalleryConfig


def monitoring_enabled() -> bool:
    """``GalleryConfig.MONITORING_ENABLED``, read for every file: saved off,
    it stops the monitor's ladder writes at once, although the running
    observer watches until a restart. True without the configuration."""
    config = _gallery_config()
    return config is None or config.MONITORING_ENABLED is not False


def ladder_gate(kind: str) -> Tuple[bool, Any]:
    """``(enabled, marks)`` of a ladder entry of run ``kind``, read now.

    ``FOREIGN``: ``save_foreign_runs()`` and ``RUN_MARKS`` (a prompt created
    from another workflow). ``PROMPTMANAGER``: ``monitoring_enabled()`` and
    the scan's ``SCAN_MARKS`` (the prompt belongs to a PromptManager run and
    usually exists already); the node's own Save to library switch applies
    through the ladder's opt-out steps.
    """
    if kind == FOREIGN:
        return save_foreign_runs(), RUN_MARKS
    return monitoring_enabled(), SCAN_MARKS


def save_foreign_runs() -> bool:
    """Whether a foreign output may be stored now, read for every file.

    ``GalleryConfig.SAVE_FOREIGN_RUNS`` and ``MONITORING_ENABLED``: a
    monitoring switch saved off stops foreign writes at once, although the
    running observer watches until a restart. True when the configuration
    cannot be imported (the defaults).
    """
    config = _gallery_config()
    if config is None:
        return True
    return config.SAVE_FOREIGN_RUNS is not False and monitoring_enabled()


def history_lookup(
    db: Any, image_path: str, reference: Optional[Mapping[str, Any]] = None
) -> Callable[[], Any]:
    """The ladder's ``lookup`` for one file: its ``RecordedOutput`` or None.

    With ``reference`` the file is spelled as history recorded it. Failures
    are logged by ``recorded_output_lookup`` and name nothing.
    """
    lookup = recorded_output_lookup(
        db, reference=dict(reference) if reference is not None else None
    )
    return lambda: lookup(image_path)


def decide_foreign(
    image_path: str,
    metadata: Any,
    *,
    in_output: bool,
    lookup: Optional[Callable[[], Any]],
) -> Decision:
    """The ``MONITOR`` ladder for one foreign output (the size is read only
    when the ladder needs it)."""

    def file_size() -> Optional[int]:
        try:
            return os.path.getsize(image_path)
        except OSError:
            return None

    return decide_file_prompt(
        metadata,
        file_name=os.path.basename(image_path),
        file_size=file_size,
        in_output=in_output,
        lookup=lookup,
        policy=Policy.MONITOR,
    )


def store_foreign(
    db: Any, image_path: str, decision: Decision, marks: Any = RUN_MARKS
) -> Tuple[str, Optional[int]]:
    """Store a certain decision: ``(status, prompt id)``.

    One write through ``survive_switch``, so it runs again on the moved
    library when a Move library switched while it waited (#249 S6b). Created
    prompts carry ``marks`` (``ladder_gate``). A file linked to another prompt is a
    ``conflict`` and nothing is written. Database errors are raised.
    """

    def store():
        if decision.kind == PAIR:
            result = ingest_resolved_pair(
                db, image_path, decision.canonical, decision.resolution, marks=marks
            )
            return result["status"], result.get("image_id")
        return store_scanned_prompt_or_raise(
            db, image_path, decision.canonical, decision.text, marks
        )

    status, linked = survive_switch(db, store)
    if decision.kind != PAIR or linked is None:
        return status, linked
    # The pair ingester reports the image row; its prompt is the positive's.
    prompt = db.get_prompt_by_hash(generate_prompt_hash(decision.text))
    return status, prompt["id"] if prompt else None
