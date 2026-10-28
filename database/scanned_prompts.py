"""Link an output file to the prompt of one text, creating it when new (#247).

The store step of ingestion from file metadata when the decision is a
positive text (``utils.file_prompt``). The prompt insert and the link run in
one ``BEGIN IMMEDIATE`` transaction: two writers never store one text twice,
a failed link leaves no new prompt behind, and a file stays linked to one
prompt (a file already linked to another prompt is a ``conflict``).
"""

import logging
import os
from typing import Any, Dict, Optional, Tuple

from .pair_ingestion import Marks, file_link_rows, link_conflict, mark_created

try:
    from ..utils.hashing import generate_prompt_hash
except ImportError:
    from utils.hashing import generate_prompt_hash

_LOGGER = logging.getLogger("prompt_manager.database.scanned_prompts")


def store_scanned_prompt_or_raise(
    db: Any, path: str, canonical: Dict[str, Any], text: str, marks: Marks
) -> Tuple[str, Optional[int]]:
    """``(status, prompt id)`` after linking ``path`` to ``text``'s prompt.

    ``added`` (prompt created, carrying ``marks``), ``linked`` (stored text
    reused), ``unchanged`` (already linked to it) or ``conflict`` (linked to
    another prompt; nothing written, id None). Database errors are raised
    and roll the whole store back, so a caller can run it again.
    """
    with db._pair_transaction() as conn:
        rows = file_link_rows(conn, path)
        if link_conflict(rows, generate_prompt_hash(text)):
            return "conflict", None
        if rows:
            return "unchanged", rows[0]["prompt_id"]
        prompt_id, created = db._pair_component_outcome(conn, text)
        if created:
            mark_created(conn, db, prompt_id, marks, os.path.basename(path))
        db._insert_image_link(conn, prompt_id, path, canonical)
        return ("added" if created else "linked"), prompt_id


def store_scanned_prompt(
    db: Any, path: str, canonical: Dict[str, Any], text: str, marks: Marks
) -> Tuple[str, Optional[int]]:
    """The scan's form: an error is logged and reported as ``("found", None)``."""
    try:
        return store_scanned_prompt_or_raise(db, path, canonical, text, marks)
    except Exception as error:
        _LOGGER.error(
            f"Failed to store the prompt of {os.path.basename(path)}: {error}"
        )
        return "found", None
