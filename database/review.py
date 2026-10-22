"""Needs review items (#249): persist and read capture-bound ambiguous evidence.

An item is one ambiguous resolver result for one annotation byte record (the
whole-file SHA-256 of one physical file at one locator), keyed by
``(byte_id, resolver_version, evidence_hash)``. Recording is idempotent: the
same evidence for the same bytes is a duplicate and never touches an existing
item, its state, revision or decisions. Changed bytes are a new byte record
and therefore a new pending item; the old one stays.

Callers pass verified facts (the capture's digest, size and ``mtime_ns``);
nothing here reads the filesystem. Writes run in one ``_pair_transaction``
fenced by the storage epoch the capture was taken under.
"""

import json
import sqlite3
from typing import Iterator

from .annotation_schema import (
    BYTES_TABLE,
    FILES_TABLE,
    METADATA_TABLE as ANNOTATION_METADATA_TABLE,
)
from .annotations import _new_id, _validate_locator, _validate_target
from .models import utc_now_iso
from .review_schema import (
    ITEMS_TABLE,
    MAX_CANDIDATES,
    METADATA_TABLE,
    canonical_evidence,
    evidence_digest,
)

# Only this module's table constants are interpolated into the SQL below.
_INSERT_ITEM = (
    f"INSERT OR IGNORE INTO {ITEMS_TABLE}(id,byte_id,resolver_version,"  # nosec B608
    "evidence_hash,evidence,candidate_count,limited,state,revision,created_at,"
    "updated_at) VALUES(?,?,?,?,?,?,?,'pending',0,?,?)"
)
_BUMP_REVIEW_INDEX = (
    f"UPDATE {METADATA_TABLE} SET "  # nosec B608
    "review_index_revision=review_index_revision+1 WHERE singleton_id=1"
)
_REVIEW_INDEX = (
    f"SELECT review_index_revision FROM {METADATA_TABLE} "  # nosec B608
    "WHERE singleton_id=1"
)
_BUMP_IMAGE_INDEX = (
    f"UPDATE {ANNOTATION_METADATA_TABLE} SET "  # nosec B608
    "image_index_revision=image_index_revision+1 WHERE singleton_id=1"
)
_ITEMS_OF_LOCATOR = (
    "SELECT i.id,i.byte_id,i.resolver_version,i.evidence_hash,"  # nosec B608
    "i.evidence,i.candidate_count,i.limited,i.state,i.revision,i.created_at,"
    f"i.updated_at,b.sha256 FROM {FILES_TABLE} f "
    f"JOIN {BYTES_TABLE} b ON b.file_id=f.id "
    f"JOIN {ITEMS_TABLE} i ON i.byte_id=b.id "
    "WHERE f.locator=? ORDER BY i.created_at,i.id"
)
_ITEM_COLUMNS = (
    "id",
    "byte_id",
    "resolver_version",
    "evidence_hash",
    "evidence",
    "candidate_count",
    "limited",
    "state",
    "revision",
    "created_at",
    "updated_at",
    "sha256",
)


class ReviewOperations:
    """PromptDatabase mixin: insert and read Needs review items."""

    def record_review_item(
        self,
        *,
        locator: str,
        sha256: str,
        size: int,
        mtime_ns: int,
        epoch: str,
        resolver_version: int,
        evidence: dict,
        candidate_count: int,
        limited: bool,
    ) -> bool:
        """Store one ambiguous result for these exact bytes; True when new.

        The byte record is created (or its verified fingerprint refreshed)
        like an observe. Raises ``LibraryCommandError("storage-changed")`` when
        the storage epoch is no longer ``epoch`` (a restore since the capture)
        and ``ValueError`` for invalid facts or evidence.
        """
        _validate_target(locator, sha256, size, mtime_ns)
        if type(resolver_version) is not int or resolver_version < 1:
            raise ValueError("resolver_version must be a positive integer")
        if (
            type(candidate_count) is not int
            or not 0 <= candidate_count <= MAX_CANDIDATES
        ):
            raise ValueError(f"candidate_count must be between 0 and {MAX_CANDIDATES}")
        if not isinstance(limited, bool) or (limited and candidate_count):
            raise ValueError("A limited item carries no candidates")
        text = canonical_evidence(evidence)
        evidence_hash = evidence_digest(text)
        with self._pair_transaction() as conn:
            self._library_epoch(conn, epoch)
            now = utc_now_iso()
            file_id = self._annotation_owner(conn, locator, now)
            byte_id, _revision, created, moved = self._annotation_byte(
                conn, file_id, sha256, size, mtime_ns, now
            )
            if created or moved:
                conn.execute(_BUMP_IMAGE_INDEX)
            cursor = conn.execute(
                _INSERT_ITEM,
                (
                    _new_id(),
                    byte_id,
                    resolver_version,
                    evidence_hash,
                    text,
                    candidate_count,
                    int(limited),
                    now,
                    now,
                ),
            )
            inserted = cursor.rowcount > 0
            if inserted:
                conn.execute(_BUMP_REVIEW_INDEX)
            return inserted

    def review_index_revision(self) -> int:
        """The queue's revision (moved by every queue change)."""
        row = self.model.get_connection().execute(_REVIEW_INDEX).fetchone()
        if row is None:
            raise sqlite3.DatabaseError("Missing review queue metadata")
        return row[0]

    def review_items_for(self, locator: str) -> list:
        """Every item of the file at ``locator``, oldest first, evidence parsed."""
        _validate_locator(locator)
        return list(self._iter_review_items(locator))

    def _iter_review_items(self, locator: str) -> Iterator[dict]:
        conn = self.model.get_connection()
        for row in conn.execute(_ITEMS_OF_LOCATOR, (locator,)):
            item = dict(zip(_ITEM_COLUMNS, tuple(row)))
            item["evidence"] = json.loads(item["evidence"])
            item["limited"] = bool(item["limited"])
            yield item
