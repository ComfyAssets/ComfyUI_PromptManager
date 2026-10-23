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

import hashlib
import json
import os
import re
import sqlite3
from typing import Iterator

try:
    from ..utils.hashing import generate_prompt_hash
except ImportError:
    from utils.hashing import generate_prompt_hash

from .annotation_schema import (
    BYTES_TABLE,
    FILES_TABLE,
    METADATA_TABLE as ANNOTATION_METADATA_TABLE,
)
from .annotations import _new_id, _validate_locator, _validate_target
from .models import normalize_image_path, utc_now_iso
from .review_changes import notify_review_change
from .review_schema import (
    DECISIONS_TABLE,
    ITEMS_TABLE,
    MAX_CANDIDATES,
    MAX_SPELLED_PATH_BYTES,
    METADATA_TABLE,
    canonical_evidence,
    evidence_digest,
)

# Only this module's table constants are interpolated into the SQL below.
_INSERT_ITEM = (
    f"INSERT OR IGNORE INTO {ITEMS_TABLE}(id,byte_id,spelled_path,"  # nosec B608
    "resolver_version,evidence_hash,evidence,candidate_count,limited,state,"
    "revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,'pending',0,?,?)"
)
_BUMP_REVIEW_INDEX = (
    f"UPDATE {METADATA_TABLE} SET "  # nosec B608
    "review_index_revision=review_index_revision+1 WHERE singleton_id=1"
)
# A later spelling of the same locator (a case-only rename on a case-folding
# filesystem) replaces the recorded one, so targets keep formatting.
_REFRESH_SPELLING = (
    f"UPDATE {ITEMS_TABLE} SET spelled_path=? "  # nosec B608
    f"WHERE spelled_path<>? AND byte_id IN (SELECT b.id FROM {BYTES_TABLE} b "
    f"JOIN {FILES_TABLE} f ON f.id=b.file_id WHERE f.locator=?)"
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
_QUEUED = (
    f"SELECT 1 FROM {FILES_TABLE} f "  # nosec B608
    f"JOIN {BYTES_TABLE} b ON b.file_id=f.id "
    f"JOIN {ITEMS_TABLE} i ON i.byte_id=b.id "
    "WHERE f.locator=? AND b.size=? AND b.mtime_ns=? AND i.resolver_version=? "
    "LIMIT 1"
)
_PENDING_AT = (
    f"SELECT 1 FROM {FILES_TABLE} f "  # nosec B608
    f"JOIN {BYTES_TABLE} b ON b.file_id=f.id "
    f"JOIN {ITEMS_TABLE} i ON i.byte_id=b.id "
    "WHERE f.locator=? AND b.size=? AND b.mtime_ns=? AND i.resolver_version=? "
    "AND i.state='pending' LIMIT 1"
)
# Pending items of one file: the candidates a link settles.
_PENDING_OF_LOCATOR = (
    f"SELECT i.id FROM {FILES_TABLE} f "  # nosec B608
    f"JOIN {BYTES_TABLE} b ON b.file_id=f.id "
    f"JOIN {ITEMS_TABLE} i ON i.byte_id=b.id "
    "WHERE f.locator=? AND i.state='pending'"
)
# Only an undecided item goes: a decision row (RESTRICT) is never deleted.
_SETTLE_ITEM = (
    f"DELETE FROM {ITEMS_TABLE} WHERE id=? AND state='pending' "  # nosec B608
    f"AND revision=0 AND NOT EXISTS (SELECT 1 FROM {DECISIONS_TABLE} d "
    f"WHERE d.item_id={ITEMS_TABLE}.id)"
)
_EPOCH = "SELECT storage_epoch FROM execution_history_metadata WHERE singleton_id=1"
_PENDING_COUNT = (
    f"SELECT COUNT(*) FROM {ITEMS_TABLE} WHERE state='pending'"  # nosec B608
)
_QUEUE_COLUMNS = (
    "id",
    "state",
    "revision",
    "created_at",
    "candidate_count",
    "limited",
    "spelled_path",
    "locator",
)
_QUEUE_SELECT = (
    "SELECT i.id,i.state,i.revision,i.created_at,i.candidate_count,"  # nosec B608
    f"i.limited,i.spelled_path,f.locator FROM {ITEMS_TABLE} i "
    f"JOIN {BYTES_TABLE} b ON b.id=i.byte_id "
    f"JOIN {FILES_TABLE} f ON f.id=b.file_id "
)
_QUEUE_FIRST = _QUEUE_SELECT + (
    "WHERE i.state='pending' ORDER BY i.created_at,i.id LIMIT ?"
)
_QUEUE_AFTER = _QUEUE_SELECT + (
    "WHERE i.state='pending' AND (i.created_at,i.id)>(?,?) "
    "ORDER BY i.created_at,i.id LIMIT ?"
)
_DETAIL_COLUMNS = _QUEUE_COLUMNS + (
    "byte_id",
    "resolver_version",
    "evidence_hash",
    "evidence",
    "updated_at",
    "sha256",
)
_DETAIL = (
    "SELECT i.id,i.state,i.revision,i.created_at,i.candidate_count,"  # nosec B608
    "i.limited,i.spelled_path,f.locator,i.byte_id,i.resolver_version,"
    "i.evidence_hash,i.evidence,i.updated_at,b.sha256 "
    f"FROM {ITEMS_TABLE} i JOIN {BYTES_TABLE} b ON b.id=i.byte_id "
    f"JOIN {FILES_TABLE} f ON f.id=b.file_id WHERE i.id=?"
)
# The legacy rows and pair links the ingester's compatibility rules read.
_DEPENDENT_IMAGES = (
    "SELECT gi.id,p.hash,gi.image_path FROM generated_images gi "
    "JOIN prompts p ON p.id=gi.prompt_id "
    "WHERE gi.file_path=? OR gi.image_path=? ORDER BY gi.id"
)
_DEPENDENT_LINKS = (
    "SELECT DISTINCT ip.pair_id,n.hash FROM image_prompt_pairs ip "
    "JOIN prompt_pairs pp ON pp.id=ip.pair_id "
    "LEFT JOIN prompts n ON n.id=pp.negative_prompt_id "
    "WHERE ip.image_id=? ORDER BY ip.pair_id"
)
# Rows whose spelling ends with the file's name: the candidates for another
# spelling of the same physical file (the link summaries' filename fallback).
_SAME_NAME_ROWS = (
    "SELECT id,image_path FROM generated_images "
    "WHERE image_path LIKE ? ESCAPE '\\' ORDER BY id"
)
_TITLES = (
    "SELECT p.hash,s.title FROM prompts p "
    "JOIN prompt_library_state s ON s.prompt_id=p.id "
    "WHERE p.hash IN ({marks}) AND s.title IS NOT NULL ORDER BY p.id"
)
_ITEM_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
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


def _validate_spelling(spelled_path: object, locator: str) -> None:
    if (
        not isinstance(spelled_path, str)
        or not spelled_path
        or len(spelled_path.encode("utf-8", "surrogatepass")) > MAX_SPELLED_PATH_BYTES
        or os.path.normcase(spelled_path) != locator
    ):
        raise ValueError("spelled_path must be the file's spelling of its locator")


def dependent_images(conn: sqlite3.Connection, spellings) -> list:
    """``(id, image_path, positive_hash)`` of every legacy row of the file.

    ``spellings`` are the paths the file is linked under (the Apply path and
    every alias of the same physical file); each is matched as the ingester
    matches one path. Rows are distinct and ordered by id.
    """
    found = {}
    for spelled in dict.fromkeys(spellings):
        for row in conn.execute(
            _DEPENDENT_IMAGES, (normalize_image_path(spelled), spelled)
        ):
            found[row[0]] = (row[0], row[2], row[1])
    return [found[key] for key in sorted(found)]


def same_name_rows(conn: sqlite3.Connection, name: str) -> list:
    """``(id, image_path)`` of rows whose spelling may end with ``name``."""
    # Imported here: operations composes this module's mixin.
    from .operations import escape_like

    return [
        (row[0], row[1])
        for row in conn.execute(_SAME_NAME_ROWS, (f"%{escape_like(name)}",))
    ]


def dependency_fingerprint(
    conn: sqlite3.Connection, image_path: str, aliases=()
) -> str:
    """SHA-256 of what Apply's compatibility depends on for ``image_path``.

    The exact legacy rows of the file under ``image_path`` and every alias
    spelling of it, with their positive hashes, and each row's pair links
    with the negative hash, read as the ingester reads them. Unrelated prompt
    changes (tags, titles) do not move it.
    """
    rows = []
    for image_id, _spelled, positive_hash in dependent_images(
        conn, (image_path, *aliases)
    ):
        links = conn.execute(_DEPENDENT_LINKS, (image_id,)).fetchall()
        rows.append([image_id, positive_hash, [list(link) for link in links]])
    text = canonical_evidence({"v": 1, "images": rows})
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ReviewOperations:
    """PromptDatabase mixin: insert and read Needs review items."""

    def record_review_item(
        self,
        *,
        locator: str,
        spelled_path: str,
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
        like an observe. ``spelled_path`` is the file's own spelling (it must
        fold to ``locator``); targets are formatted from it. Raises
        ``LibraryCommandError("storage-changed")`` when the storage epoch is
        no longer ``epoch`` (a restore since the capture) and ``ValueError``
        for invalid facts or evidence.
        """
        _validate_target(locator, sha256, size, mtime_ns)
        _validate_spelling(spelled_path, locator)
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
                    spelled_path,
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
            refreshed = _refresh_spelling(conn, locator, spelled_path)
            if inserted or refreshed:
                conn.execute(_BUMP_REVIEW_INDEX)
        if inserted or refreshed:
            notify_review_change(self.model.db_path)
        return inserted

    def refresh_review_spelling(self, locator: str, spelled_path: str) -> bool:
        """Record ``spelled_path`` as the spelling of every item at ``locator``.

        The rescan's skip calls it for a file it does not copy again: a
        case-only rename keeps size and ``mtime_ns``, and a target formatted
        from the old spelling would be refused. True when a spelling changed
        (the review index revision moves; item revisions do not, so an open
        decision stays valid).
        """
        _validate_locator(locator)
        _validate_spelling(spelled_path, locator)
        with self._pair_transaction() as conn:
            refreshed = _refresh_spelling(conn, locator, spelled_path)
            if refreshed:
                conn.execute(_BUMP_REVIEW_INDEX)
        if refreshed:
            notify_review_change(self.model.db_path)
        return refreshed

    def review_index_revision(self) -> int:
        """The queue's revision (moved by every queue change)."""
        row = self.model.get_connection().execute(_REVIEW_INDEX).fetchone()
        if row is None:
            raise sqlite3.DatabaseError("Missing review queue metadata")
        return row[0]

    def review_queue_version(self) -> dict:
        """``{library_epoch, review_revision}`` of the queue, from one snapshot.

        The pair a list read answers: equal pairs mean the same queue (a
        restore moves the epoch, every queue change moves the revision).
        """
        with self._annotation_read() as conn:
            epoch = conn.execute(_EPOCH).fetchone()
            revision = conn.execute(_REVIEW_INDEX).fetchone()
        if epoch is None or revision is None:
            raise sqlite3.DatabaseError("Missing review queue metadata")
        return {"library_epoch": epoch[0], "review_revision": revision[0]}

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

    # -- queue reads (#249 E3) -------------------------------------------------

    def review_fingerprint_queued(
        self, locator: str, size: int, mtime_ns: int, resolver_version: int
    ) -> bool:
        """True when an item cites a byte record verified at this exact stat.

        A rescan uses it to skip the copy and hash of a file it already
        queued: the same ``(size, mtime_ns)`` the bytes were verified with.
        """
        row = (
            self.model.get_connection()
            .execute(_QUEUED, (locator, size, mtime_ns, resolver_version))
            .fetchone()
        )
        return row is not None

    def review_pending_at(
        self, locator: str, size: int, mtime_ns: int, resolver_version: int
    ) -> bool:
        """True when a *pending* item cites bytes verified at this exact stat.

        ``review_fingerprint_queued`` also matches decided items; a file whose
        only item is decided is not in the queue.
        """
        row = (
            self.model.get_connection()
            .execute(_PENDING_AT, (locator, size, mtime_ns, resolver_version))
            .fetchone()
        )
        return row is not None

    def review_file_linked(self, spellings) -> bool:
        """True when a saved row links the file under any of ``spellings``."""
        with self._annotation_read() as conn:
            return bool(dependent_images(conn, spellings))

    def settle_linked_review_items(self, files) -> int:
        """Remove the pending, undecided items of files that are now linked.

        ``files`` holds ``(locator, spellings)`` per file. An item leaves the
        queue only when it is still ``pending`` at revision 0 with no decision
        row and a saved row links its file under one of ``spellings``; a
        decided item is never touched. Files without a pending item cost one
        indexed read and open no write transaction. Returns how many items
        were removed (the review index revision moves when any was).
        """
        files = [(locator, tuple(spellings)) for locator, spellings in files]
        for locator, _spellings in files:
            _validate_locator(locator)
        conn = self.model.get_connection()
        candidates = [
            (locator, spellings)
            for locator, spellings in files
            if conn.execute(_PENDING_OF_LOCATOR, (locator,)).fetchone() is not None
        ]
        if not candidates:
            return 0
        removed = 0
        with self._pair_transaction() as conn:
            for locator, spellings in candidates:
                if not dependent_images(conn, spellings):
                    continue
                for (item_id,) in conn.execute(
                    _PENDING_OF_LOCATOR, (locator,)
                ).fetchall():
                    removed += conn.execute(_SETTLE_ITEM, (item_id,)).rowcount
            if removed:
                conn.execute(_BUMP_REVIEW_INDEX)
        return removed

    def review_queue_page(self, *, limit: int, after: tuple | None = None) -> dict:
        """One page of pending items, oldest first, from one snapshot.

        ``after`` is the ``(created_at, id)`` keyset of the last item already
        shown. Returns ``{epoch, revision, total, items, more}``; ``total`` is
        the exact pending count and ``items`` carry their ``spelled_path`` and
        ``locator`` (server-side only).
        """
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")
        with self._annotation_read() as conn:
            epoch = conn.execute(_EPOCH).fetchone()
            revision = conn.execute(_REVIEW_INDEX).fetchone()
            if epoch is None or revision is None:
                raise sqlite3.DatabaseError("Missing review queue metadata")
            total = conn.execute(_PENDING_COUNT).fetchone()[0]
            if after is None:
                rows = conn.execute(_QUEUE_FIRST, (limit + 1,)).fetchall()
            else:
                rows = conn.execute(_QUEUE_AFTER, (*after, limit + 1)).fetchall()
        items = [_queue_row(row, _QUEUE_COLUMNS) for row in rows[:limit]]
        return {
            "epoch": epoch[0],
            "revision": revision[0],
            "total": total,
            "items": items,
            "more": len(rows) > limit,
        }

    def review_item(self, item_id: str) -> dict | None:
        """One item with its parsed evidence and the pending total, or None."""
        if not isinstance(item_id, str) or _ITEM_ID.fullmatch(item_id) is None:
            return None
        with self._annotation_read() as conn:
            epoch = conn.execute(_EPOCH).fetchone()
            if epoch is None:
                raise sqlite3.DatabaseError("Missing execution history metadata")
            row = conn.execute(_DETAIL, (item_id,)).fetchone()
            if row is None:
                return None
            item = _queue_row(row, _DETAIL_COLUMNS)
            item["evidence"] = json.loads(item["evidence"])
            item["epoch"] = epoch[0]
            item["total"] = conn.execute(_PENDING_COUNT).fetchone()[0]
        return item

    def review_dependency_fingerprint(self, image_path: str, aliases=()) -> str:
        with self._annotation_read() as conn:
            return dependency_fingerprint(conn, image_path, aliases)

    def review_same_name_rows(self, name: str) -> list:
        """``(id, image_path)`` of legacy rows that may spell a file ``name``."""
        with self._annotation_read() as conn:
            return same_name_rows(conn, name)

    def stored_prompt_titles(self, texts) -> dict:
        """Library titles of saved prompts by text (dedup hash), when set."""
        hashes = {generate_prompt_hash(text): text for text in texts if text.strip()}
        if not hashes:
            return {}
        marks = ",".join("?" * len(hashes))
        found: dict[str, tuple] = {}
        conn = self.model.get_connection()
        for prompt_hash, title in conn.execute(
            _TITLES.format(marks=marks), list(hashes)
        ):
            text = hashes[prompt_hash]
            found[text] = found.get(text, ()) + (title,)
        return found


def _refresh_spelling(conn: sqlite3.Connection, locator: str, spelled: str) -> bool:
    return conn.execute(_REFRESH_SPELLING, (spelled, spelled, locator)).rowcount > 0


def _queue_row(row, columns) -> dict:
    item = dict(zip(columns, tuple(row)))
    item["limited"] = bool(item["limited"])
    return item
