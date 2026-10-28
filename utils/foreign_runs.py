"""The monitor's path for outputs of workflows without a PromptManager node (#247).

A foreign output (no positive PromptManager node in its graph) is decided by
the ladder the output scan uses (``utils.file_prompt.decide_file_prompt``)
with the ``MONITOR`` policy: certain files are stored at once, files only
recorded history can decide wait for their run's outputs to be sealed, and
everything else stays unlinked. This module holds the parts that do not
need the monitor's waiting sets: the setting, the decision call and the
store. The waiting and the re-check stay on ``ImageGenerationHandler``.
"""

import os
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Tuple

from .file_prompt import PAIR, POSITIVE, Decision, Policy, decide_file_prompt
from .hashing import generate_prompt_hash
from .usage_tracking import survive_switch

try:
    from ..database.pair_ingestion import RUN_MARKS, ingest_resolved_pair
    from ..database.run_attribution import recorded_output_lookup
    from ..database.scanned_prompts import store_scanned_prompt_or_raise
except ImportError:
    from database.pair_ingestion import RUN_MARKS, ingest_resolved_pair
    from database.run_attribution import recorded_output_lookup
    from database.scanned_prompts import store_scanned_prompt_or_raise

# Decisions that name a prompt to store
CERTAIN = (PAIR, POSITIVE)


@dataclass(frozen=True)
class ForeignRecheck:
    """``schedule`` snapshot: the one re-check of a foreign output after its
    run's outputs were sealed. ``reference`` is the sealed output reference,
    the spelling history recorded (``recorded_output_lookup(reference=)``)."""

    reference: Mapping[str, Any]


def save_foreign_runs() -> bool:
    """``GalleryConfig.SAVE_FOREIGN_RUNS``, read for every file; True when
    the configuration cannot be imported (the default)."""
    try:
        from ..py.config import GalleryConfig
    except ImportError:
        try:
            from py.config import GalleryConfig
        except ImportError:
            return True
    return GalleryConfig.SAVE_FOREIGN_RUNS is not False


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
    """The ``MONITOR`` ladder for one foreign output."""
    try:
        file_size = os.path.getsize(image_path)
    except OSError:
        file_size = None
    return decide_file_prompt(
        metadata,
        file_name=os.path.basename(image_path),
        file_size=file_size,
        in_output=in_output,
        lookup=lookup,
        policy=Policy.MONITOR,
    )


def store_foreign(
    db: Any, image_path: str, decision: Decision
) -> Tuple[str, Optional[int]]:
    """Store a certain decision: ``(status, prompt id)``.

    One write through ``survive_switch``, so it runs again on the moved
    library when a Move library switched while it waited (#249 S6b). Created
    prompts carry ``RUN_MARKS``. A file linked to another prompt is a
    ``conflict`` and nothing is written. Database errors are raised.
    """

    def store():
        if decision.kind == PAIR:
            result = ingest_resolved_pair(
                db, image_path, decision.canonical, decision.resolution, marks=RUN_MARKS
            )
            return result["status"], result.get("image_id")
        return store_scanned_prompt_or_raise(
            db, image_path, decision.canonical, decision.text, RUN_MARKS
        )

    status, linked = survive_switch(db, store)
    if decision.kind != PAIR or linked is None:
        return status, linked
    # The pair ingester reports the image row; its prompt is the positive's.
    prompt = db.get_prompt_by_hash(generate_prompt_hash(decision.text))
    return status, prompt["id"] if prompt else None
