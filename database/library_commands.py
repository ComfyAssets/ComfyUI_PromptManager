"""Transactional native Library commands guarded by epoch and edit token."""

import re
import sqlite3

from .library import _ELIGIBLE
from .models import utc_now_iso

try:
    from ..utils.validators import (
        validate_category,
        validate_prompt_text,
        validate_tags,
    )
except ImportError:
    from utils.validators import validate_category, validate_prompt_text, validate_tags

METADATA_FIELDS = frozenset({"title", "category", "tags", "rating", "notes"})
MAX_TEXT_FIELD = 10000
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
_TOKEN = re.compile(r"[0-9a-f]{64}")
# Parent metadata stored on the prompt row; title lives in prompt_library_state.
_PROMPT_COLUMNS = ("category", "rating", "notes")


class LibraryCommandError(Exception):
    """A native command lost a race against storage or another writer.

    ``code`` is a wire code (``metadata-conflict``, ``storage-changed`` or
    ``vocabulary-conflict`` for a tag or category that changed since it was read),
    ``epoch`` the storage epoch read inside the transaction and ``positive``
    the current owner record when it may be disclosed.
    """

    def __init__(self, code: str, epoch: str, positive: dict | None = None):
        super().__init__(code)
        self.code = code
        self.epoch = epoch
        self.positive = positive


def validate_library_metadata(metadata: dict, *, require_title: bool = False) -> None:
    """Reject anything but a nonempty allowlisted parent metadata object."""
    if not isinstance(metadata, dict) or not metadata:
        raise ValueError("metadata must be a nonempty object")
    unknown = set(metadata) - METADATA_FIELDS
    if unknown:
        raise ValueError("Unknown metadata fields: " + ", ".join(sorted(unknown)))
    if require_title and (
        not isinstance(metadata.get("title"), str) or not metadata["title"].strip()
    ):
        raise ValueError("title is required")
    for name in ("title", "notes"):
        value = metadata.get(name)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{name} must be a string or null")
        if value is not None and len(value) > MAX_TEXT_FIELD:
            raise ValueError(f"{name} is too long")
    if metadata.get("category") is not None:
        validate_category(metadata["category"])
    if "tags" in metadata:
        if not isinstance(metadata["tags"], list):
            raise ValueError("tags must be a list of strings")
        validate_tags(metadata["tags"])
    rating = metadata.get("rating")
    if rating is not None and (type(rating) is not int or not 1 <= rating <= 5):
        raise ValueError("rating must be an integer between 1 and 5 or null")


def normalize_library_metadata(metadata: dict) -> dict:
    """Return validated metadata with category trimmed and blank stored as NULL."""
    if "category" not in metadata:
        return metadata
    category = metadata["category"]
    return {**metadata, "category": (category or "").strip() or None}


def normalize_library_tags(tags: list[str]) -> list[str]:
    """Trim and deduplicate validated tags, keeping first-seen order."""
    return list(dict.fromkeys(tag.strip() for tag in tags))


def _validate_epoch(expected_epoch: str) -> None:
    if not isinstance(expected_epoch, str) or not _UUID.fullmatch(expected_epoch):
        raise ValueError("expected_epoch must be a canonical UUID")


def _validate_guards(positive_id: int, expected_epoch: str, if_match: str) -> None:
    if type(positive_id) is not int or not 1 <= positive_id <= 2**63 - 1:
        raise ValueError("Invalid positive prompt ID")
    _validate_epoch(expected_epoch)
    if not isinstance(if_match, str) or not _TOKEN.fullmatch(if_match):
        raise ValueError("if_match must be 64 lowercase hexadecimal characters")


def _validate_negative_text(negative_text: str) -> None:
    """Require an explicit string; blank deliberately means the empty negative."""
    if not isinstance(negative_text, str):
        raise ValueError("negative_text must be explicit; use an empty string for none")
    if negative_text.strip():
        validate_prompt_text(negative_text)


class LibraryCommandOperations:
    """Connection-level parent commands mixed into PromptDatabase."""

    def _library_epoch(self, conn: sqlite3.Connection, expected_epoch: str) -> str:
        row = conn.execute(
            "SELECT storage_epoch FROM execution_history_metadata WHERE singleton_id=1"
        ).fetchone()
        if row is None:
            raise sqlite3.DatabaseError("Missing execution history metadata")
        if row[0] != expected_epoch:
            raise LibraryCommandError("storage-changed", row[0])
        return row[0]

    def _library_parent(
        self, conn: sqlite3.Connection, epoch: str, positive_id: int
    ) -> dict | None:
        rows = self._library_parents(
            conn, epoch, _ELIGIBLE + " AND p.id=?", [positive_id]
        )
        return rows[0] if rows else None

    def _ensure_library_parent(self, conn: sqlite3.Connection, prompt_id: int) -> None:
        """Register explicit parent intent once; an existing row is never touched."""
        if conn.execute(
            "SELECT 1 FROM prompt_library_state WHERE prompt_id=?", (prompt_id,)
        ).fetchone():
            return
        conn.execute(
            "INSERT INTO prompt_library_state(prompt_id,title) VALUES(?,NULL)",
            (prompt_id,),
        )

    def _apply_library_metadata(
        self, conn: sqlite3.Connection, current: dict, metadata: dict
    ) -> None:
        """Write only fields that differ; stamp ``prompts.updated_at`` once."""
        prompt_id = current["id"]
        changed = False
        if "title" in metadata and metadata["title"] != current["title"]:
            conn.execute(
                "UPDATE prompt_library_state SET title=? WHERE prompt_id=?",
                (metadata["title"], prompt_id),
            )
            changed = True
        columns = {
            name: metadata[name]
            for name in _PROMPT_COLUMNS
            if name in metadata and metadata[name] != current[name]
        }
        if "tags" in metadata:
            tags = normalize_library_tags(metadata["tags"])
            if set(tags) != set(current["tags"]):
                self._sync_prompt_tags(conn, prompt_id, tags)
                changed = True
        if columns or changed:
            columns["updated_at"] = utc_now_iso()
            conn.execute(
                "UPDATE prompts SET "
                + ",".join(f"{name}=?" for name in columns)
                + " WHERE id=?",
                [*columns.values(), prompt_id],
            )

    def update_library_parent(
        self,
        positive_id: int,
        *,
        expected_epoch: str,
        if_match: str,
        metadata: dict,
    ) -> dict | None:
        """Apply explicit parent metadata if the epoch and edit token still match.

        Returns ``{library_epoch, positive}`` read before commit, or None when
        the positive is missing or not a Library parent. Raises
        LibraryCommandError for a rotated epoch or a stale token.
        """
        _validate_guards(positive_id, expected_epoch, if_match)
        validate_library_metadata(metadata)
        metadata = normalize_library_metadata(metadata)
        with self._pair_transaction() as conn:
            epoch = self._library_epoch(conn, expected_epoch)
            current = self._library_parent(conn, epoch, positive_id)
            if current is None:
                return None
            if current["edit_token"] != if_match:
                raise LibraryCommandError("metadata-conflict", epoch, current)
            self._ensure_library_parent(conn, positive_id)
            self._apply_library_metadata(conn, current, metadata)
            positive = self._library_parent(conn, epoch, positive_id)
            if positive is None:
                raise sqlite3.DatabaseError("Library parent vanished during update")
            return {"library_epoch": epoch, "positive": positive}

    def _recipe_negative(
        self, conn: sqlite3.Connection, negative_text: str
    ) -> int | None:
        if not negative_text.strip():
            return None
        return self._pair_component_outcome(conn, negative_text)[0]

    def _recipe_receipt(
        self,
        conn: sqlite3.Connection,
        epoch: str,
        positive_id: int,
        pair_id: int,
        *,
        positive_created: bool,
        pair_created: bool,
    ) -> dict:
        """Read both owners inside the write transaction for the receipt."""
        positive = self._library_parent(conn, epoch, positive_id)
        pairs = self._read_pair_rows(conn, "pp.id = ?", [pair_id])
        if positive is None or not pairs:
            raise sqlite3.DatabaseError("Library recipe vanished during save")
        return {
            "library_epoch": epoch,
            "positive": positive,
            "pair": pairs[0],
            "positive_created": positive_created,
            "pair_created": pair_created,
            # Parent metadata is applied only to a newly inserted positive.
            "parent_metadata_applied": positive_created,
        }

    def create_library_recipe(
        self,
        positive_text: str,
        negative_text: str,
        *,
        expected_epoch: str,
        parent_metadata: dict,
    ) -> dict:
        """Create or reuse a positive and its ordered pair in one transaction.

        Submitted parent metadata applies only when the positive component is
        newly inserted; a reused component keeps its own metadata. New pairs get
        empty pair metadata. Reused pairs keep theirs, including archive state.
        Every success registers the positive as a Library parent.
        """
        _validate_epoch(expected_epoch)
        validate_prompt_text(positive_text)
        _validate_negative_text(negative_text)
        validate_library_metadata(parent_metadata, require_title=True)
        parent_metadata = normalize_library_metadata(parent_metadata)
        with self._pair_transaction() as conn:
            epoch = self._library_epoch(conn, expected_epoch)
            positive_id, positive_created = self._pair_component_outcome(
                conn, positive_text
            )
            negative_id = self._recipe_negative(conn, negative_text)
            pair_id, pair_created = self._save_pair_components(
                conn, positive_id, negative_id
            )
            self._ensure_library_parent(conn, positive_id)
            if positive_created:
                current = self._library_parent(conn, epoch, positive_id)
                if current is None:
                    raise sqlite3.DatabaseError("New Library parent is not eligible")
                self._apply_library_metadata(conn, current, parent_metadata)
            return self._recipe_receipt(
                conn,
                epoch,
                positive_id,
                pair_id,
                positive_created=positive_created,
                pair_created=pair_created,
            )

    def add_library_negative(
        self,
        positive_id: int,
        negative_text: str,
        *,
        expected_epoch: str,
        if_match: str,
    ) -> dict | None:
        """Create or reuse a pair under the exact stored positive.

        The stored positive ID and text are used as they are; only the negative
        is resolved. Returns None when the positive is missing or not a Library
        parent, and raises LibraryCommandError for a rotated epoch or a stale
        token, in both cases before any insertion.
        """
        _validate_guards(positive_id, expected_epoch, if_match)
        _validate_negative_text(negative_text)
        with self._pair_transaction() as conn:
            epoch = self._library_epoch(conn, expected_epoch)
            current = self._library_parent(conn, epoch, positive_id)
            if current is None:
                return None
            if current["edit_token"] != if_match:
                raise LibraryCommandError("metadata-conflict", epoch, current)
            negative_id = self._recipe_negative(conn, negative_text)
            pair_id, pair_created = self._save_pair_components(
                conn, positive_id, negative_id
            )
            self._ensure_library_parent(conn, positive_id)
            return self._recipe_receipt(
                conn,
                epoch,
                positive_id,
                pair_id,
                positive_created=False,
                pair_created=pair_created,
            )
