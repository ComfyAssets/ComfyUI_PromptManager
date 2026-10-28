"""Records of duplicate image files: count them and move them (#249 S6, T17).

A removed copy's records move to its verified kept copy before the copy is
unlinked (``py/api/duplicate_files.py``), one file per short ``BEGIN
IMMEDIATE`` transaction, so no row ever names a deleted file:

- its image records (``generated_images``), found under every spelling the
  file is linked by. A record moves to the kept copy, unless its prompt
  already links the kept copy: then its saved-pair links join that record
  and it is deleted;
- its favorite;
- its collection memberships (#331), joined onto the kept copy's as their
  union under the same keys a favorite moves by;
- its image tags (``media_image_tags`` of its byte record), joined onto the
  kept copy's byte record as their union, in order;
- its pending Needs review items, settled: only an item still ``pending`` at
  revision 0 with no decision row is removed (``review._SETTLE_ITEM``), so a
  decided item is never touched.

Every statement is an index search (see ``FILE_LINK_SQL``).
"""

import os

from .annotation_schema import BYTES_TABLE, FILES_TABLE
from .annotations import _BUMP_INDEX
from .models import normalize_image_path, utc_now_iso
from .review import _BUMP_REVIEW_INDEX, _PENDING_OF_LOCATOR, _SETTLE_ITEM
from .review_changes import notify_review_change
from .workflow_documents import documents_of_rows, release_documents

LINKS_BY_FILE_SQL = "SELECT id, prompt_id FROM generated_images WHERE file_path = ?"
LEGACY_LINKS_SQL = (
    "SELECT id, prompt_id FROM generated_images "
    "WHERE image_path = ? AND file_path IS NULL"
)
KEEPER_LINK_SQL = (
    "SELECT id FROM generated_images WHERE prompt_id = ? AND file_path = ?"
)
MOVE_PAIR_LINKS_SQL = (
    "UPDATE OR IGNORE image_prompt_pairs SET image_id = ? WHERE image_id = ?"
)
MOVE_LINK_SQL = (
    "UPDATE generated_images SET image_path = ?, filename = ?, file_path = ? "
    "WHERE id = ?"
)
DELETE_LINK_SQL = "DELETE FROM generated_images WHERE id = ?"
FAVORITE_SQL = (
    "SELECT created_at FROM image_favorites WHERE root_key = ? AND relative_path = ?"
)
INSERT_FAVORITE_SQL = "INSERT OR IGNORE INTO image_favorites VALUES (?, ?, ?)"
DELETE_FAVORITE_SQL = (
    "DELETE FROM image_favorites WHERE root_key = ? AND relative_path = ?"
)
COPY_MEMBERSHIPS_SQL = (
    "INSERT OR IGNORE INTO image_collection_items "
    "(collection_id, root_key, relative_path, added_at) "
    "SELECT collection_id, ?, ?, added_at FROM image_collection_items "
    "WHERE root_key = ? AND relative_path = ?"
)
DELETE_MEMBERSHIPS_SQL = (
    "DELETE FROM image_collection_items WHERE root_key = ? AND relative_path = ?"
)
COPY_BYTE_SQL = (
    f"SELECT b.id FROM {FILES_TABLE} f JOIN {BYTES_TABLE} b "  # nosec B608
    "ON b.file_id=f.id WHERE f.locator=? AND b.sha256=?"
)
COPY_TAG_COUNT_SQL = (
    "SELECT COUNT(*) FROM media_image_tags WHERE byte_id=?"  # nosec B608
)
PENDING_COUNT_SQL = (
    f"SELECT COUNT(*) FROM {FILES_TABLE} f "  # nosec B608
    f"JOIN {BYTES_TABLE} b ON b.file_id=f.id "
    "JOIN media_review_items i ON i.byte_id=b.id "
    "WHERE f.locator=? AND +i.state='pending' AND i.revision=0 "
    "AND NOT EXISTS (SELECT 1 FROM media_review_decisions d WHERE d.item_id=i.id)"
)
FILE_LINK_SQL = (
    LINKS_BY_FILE_SQL,
    LEGACY_LINKS_SQL,
    KEEPER_LINK_SQL,
    MOVE_PAIR_LINKS_SQL,
    MOVE_LINK_SQL,
    DELETE_LINK_SQL,
    FAVORITE_SQL,
    DELETE_FAVORITE_SQL,
    COPY_MEMBERSHIPS_SQL,
    DELETE_MEMBERSHIPS_SQL,
    COPY_BYTE_SQL,
    COPY_TAG_COUNT_SQL,
    PENDING_COUNT_SQL,
)


def _file_link_rows(conn, spelled):
    """``(id, prompt_id)`` of the image records linking one spelling."""
    rows = list(conn.execute(LINKS_BY_FILE_SQL, (normalize_image_path(spelled),)))
    rows += list(conn.execute(LEGACY_LINKS_SQL, (spelled,)))
    return [tuple(row) for row in rows]


def _copy_byte(conn, locator, sha256):
    row = conn.execute(COPY_BYTE_SQL, (locator, sha256)).fetchone()
    return row[0] if row else None


class DuplicateCopyOperations:
    """PromptDatabase mixin: the records of duplicate copies."""

    def count_copy_records(self, copies) -> dict:
        """``{path: {links, tags, review}}`` for each copy (one read snapshot).

        ``copies`` maps each path to ``{spellings, locator, sha256}``:
        ``links`` counts the image records under any spelling, ``tags`` the
        image tags of its bytes and ``review`` its pending, undecided Needs
        review items.
        """
        from .maintenance import _read_snapshot

        def read(conn):
            found = {}
            for path, copy in copies.items():
                ids = {
                    row[0]
                    for spelled in copy["spellings"]
                    for row in _file_link_rows(conn, spelled)
                }
                byte_id = _copy_byte(conn, copy["locator"], copy["sha256"])
                tags = (
                    conn.execute(COPY_TAG_COUNT_SQL, (byte_id,)).fetchone()[0]
                    if byte_id
                    else 0
                )
                review = conn.execute(PENDING_COUNT_SQL, (copy["locator"],))
                found[path] = {
                    "links": len(ids),
                    "tags": tags,
                    "review": review.fetchone()[0],
                }
            return found

        return _read_snapshot(self.model.db_path, read)

    def move_copy_records(self, move) -> dict:
        """Move one removed copy's records to its kept copy (one transaction).

        ``move``: ``removed`` and ``kept`` (the spellings each file is linked
        by), ``targets`` (``{removed spelling: kept spelling}``, the spelling
        a moved record takes; else ``kept_path``), ``favorites`` (``(root key,
        removed key, kept root key, kept key)`` per listed root holding the
        file; they move its favorite and its collection memberships),
        ``removed_locator``, ``kept_locator``, ``sha256`` and the kept copy's
        ``kept_size`` and ``kept_mtime_ns``. Returns ``{moved, merged,
        favorites, tags, review, collections}`` (``collections``: the
        memberships inserted on the kept copy).
        """
        counts = dict.fromkeys(
            ("moved", "merged", "favorites", "tags", "review", "collections"), 0
        )
        kept_keys = [normalize_image_path(path) for path in move["kept"]]
        with self._pair_transaction() as conn:
            for spelled in dict.fromkeys(move["removed"]):
                target = move["targets"].get(spelled, move["kept_path"])
                keys = list(dict.fromkeys(kept_keys + [normalize_image_path(target)]))
                for image_id, prompt_id in _file_link_rows(conn, spelled):
                    existing = None
                    for key in keys:
                        existing = conn.execute(
                            KEEPER_LINK_SQL, (prompt_id, key)
                        ).fetchone()
                        if existing is not None:
                            break
                    if existing is not None:
                        conn.execute(MOVE_PAIR_LINKS_SQL, (existing[0], image_id))
                        documents = documents_of_rows(conn, "id", [image_id])
                        conn.execute(DELETE_LINK_SQL, (image_id,))
                        release_documents(conn, documents)
                        counts["merged"] += 1
                        continue
                    conn.execute(
                        MOVE_LINK_SQL,
                        (
                            target,
                            os.path.basename(target),
                            normalize_image_path(target),
                            image_id,
                        ),
                    )
                    counts["moved"] += 1
            for favorite in move.get("favorites", ()):
                counts["favorites"] += _move_favorite(conn, *favorite)
                counts["collections"] += _move_memberships(conn, *favorite)
            counts["tags"] = self._move_copy_tags(conn, move)
            for (item_id,) in conn.execute(
                _PENDING_OF_LOCATOR, (move["removed_locator"],)
            ).fetchall():
                counts["review"] += conn.execute(_SETTLE_ITEM, (item_id,)).rowcount
            if counts["review"]:
                conn.execute(_BUMP_REVIEW_INDEX)
        if counts["review"]:
            notify_review_change(self.model.db_path)
        return counts

    def _move_copy_tags(self, conn, move):
        """Join the removed copy's image tags onto the kept copy's; the count added."""
        removed = _copy_byte(conn, move["removed_locator"], move["sha256"])
        if removed is None:
            return 0
        tags = self._byte_tags(conn, removed)
        if not tags:
            return 0
        now = utc_now_iso()
        file_id = self._annotation_owner(conn, move["kept_locator"], now)
        byte_id, _revision, created, moved = self._annotation_byte(
            conn, file_id, move["sha256"], move["kept_size"], move["kept_mtime_ns"], now
        )
        current = self._byte_tags(conn, byte_id)
        union = current + [tag for tag in tags if tag not in current]
        if union != current:
            self._write_image_tags(conn, byte_id, union)
        elif created or moved:
            conn.execute(_BUMP_INDEX)
        return len(union) - len(current)


def _move_favorite(conn, root_key, removed_key, kept_root, kept_key):
    row = conn.execute(FAVORITE_SQL, (root_key, removed_key)).fetchone()
    if row is None:
        return 0
    conn.execute(INSERT_FAVORITE_SQL, (kept_root, kept_key, row[0]))
    conn.execute(DELETE_FAVORITE_SQL, (root_key, removed_key))
    return 1


def _move_memberships(conn, root_key, removed_key, kept_root, kept_key):
    """Copy a removed copy's memberships to the kept copy; the rows inserted."""
    inserted = conn.execute(
        COPY_MEMBERSHIPS_SQL, (kept_root, kept_key, root_key, removed_key)
    ).rowcount
    conn.execute(DELETE_MEMBERSHIPS_SQL, (root_key, removed_key))
    return inserted
