"""Delete selected Library prompts; archive the paired ones (#443, D152).

A selected Library parent is classified, in this order, as ``not_found``
(missing, or not a Library parent), ``protected`` (tagged ``__protected__``;
never changed), ``already_archived``, ``archive`` (the positive or negative of
any saved pair, archived pair or not: archiving hides it from the shelf and
removes nothing) or ``delete`` (in no pair: deleted exactly as the legacy
delete does, through ``delete_prompt_rows``). Every command checks the
storage epoch first and runs in one ``BEGIN IMMEDIATE`` transaction.

The write executes what the user reviewed: it is given the reviewed archive
and delete lists, and deletes only reviewed deletes the route backed up
(``candidates``) whose fingerprint is unchanged since that backup.
"""

import sqlite3
from typing import Iterable

from .library import _ELIGIBLE
from .library_commands import _validate_bulk_ids, _validate_epoch
from .models import utc_now_iso
from .prompt_archive import ARCHIVED as _ARCHIVED
from .prompt_archive import TABLE as ARCHIVE
from .workflow_documents import documents_of_rows, release_documents

try:
    from ..utils.hashing import generate_prompt_hash
except ImportError:
    from utils.hashing import generate_prompt_hash

# Ids bound per statement: the pair check binds each id twice, which stays
# under SQLite's historical 999-variable limit (maintenance.py ID_BATCH).
ID_BATCH = 400
_PROTECTED = (
    "EXISTS(SELECT 1 FROM prompt_tags pt JOIN tags t ON t.id=pt.tag_id "
    "WHERE pt.prompt_id=p.id AND t.name='__protected__')"
)
_PAIRED = (
    "EXISTS(SELECT 1 FROM prompt_pairs pp "
    "WHERE pp.positive_prompt_id=p.id OR pp.negative_prompt_id=p.id)"
)
# Classes in the order a prompt is tested; the first that holds wins.
CLASSES = ("not_found", "protected", "already_archived", "archive", "delete")


def _batches(ids: list) -> Iterable[list]:
    for start in range(0, len(ids), ID_BATCH):
        yield ids[start : start + ID_BATCH]


def delete_prompt_rows(conn: sqlite3.Connection, prompt_ids: list[int]) -> int:
    """Delete prompts and their image link rows; return the prompts deleted.

    The legacy delete's statements (``delete_prompt``, ``bulk_delete_prompts``)
    in the caller's transaction: image link rows first (``image_prompt_pairs``
    cascades, review decisions keep a NULL image), then the prompts (tags and
    Library state cascade), then the workflow documents no row references.
    Image files are never touched. The caller checks pairs first.
    """
    documents = documents_of_rows(conn, "prompt_id", prompt_ids)
    deleted = 0
    for chunk in _batches(list(prompt_ids)):
        marks = ",".join("?" for _ in chunk)
        conn.execute(
            f"DELETE FROM generated_images WHERE prompt_id IN ({marks})",  # nosec
            chunk,
        )
        deleted += conn.execute(
            f"DELETE FROM prompts WHERE id IN ({marks})", chunk  # nosec B608
        ).rowcount
    release_documents(conn, documents)
    return deleted


class LibraryArchiveOperations:
    """PromptDatabase mixin: classify, archive or delete, and restore parents."""

    def _classify_library_delete(
        self, conn: sqlite3.Connection, prompt_ids: list[int]
    ) -> dict:
        found = {}
        for chunk in _batches(prompt_ids):
            marks = ",".join("?" for _ in chunk)
            for row in conn.execute(
                f"SELECT p.id,{_PROTECTED},{_ARCHIVED},{_PAIRED} "  # nosec B608
                f"FROM prompts p WHERE p.id IN ({marks}) AND {_ELIGIBLE}",
                chunk,
            ):
                protected, archived, paired = row[1], row[2], row[3]
                if protected:
                    found[row[0]] = "protected"
                elif archived:
                    found[row[0]] = "already_archived"
                else:
                    found[row[0]] = "archive" if paired else "delete"
        classes = {name: [] for name in CLASSES}
        for pid in prompt_ids:
            classes[found.get(pid, "not_found")].append(pid)
        return classes

    @staticmethod
    def _image_links(conn: sqlite3.Connection, prompt_ids: list[int]) -> int:
        total = 0
        for chunk in _batches(prompt_ids):
            marks = ",".join("?" for _ in chunk)
            total += conn.execute(
                "SELECT COUNT(*) FROM generated_images "  # nosec B608
                f"WHERE prompt_id IN ({marks})",
                chunk,
            ).fetchone()[0]
        return total

    @staticmethod
    def _fingerprints(conn: sqlite3.Connection, prompt_ids: list[int]) -> dict:
        """What a safety backup holds of each prompt a delete would remove.

        The prompt row (usage included), its tag ids, its Library title and
        its image link rows (count and newest id), as a JSON-safe list.
        """
        prints = {}
        for chunk in _batches(prompt_ids):
            marks = ",".join("?" for _ in chunk)
            for row in conn.execute(
                "SELECT p.id,p.text,p.hash,p.category,p.rating,p.notes,"  # nosec
                "p.updated_at,p.run_count,p.last_used_at,"
                "(SELECT group_concat(tag_id) FROM (SELECT tag_id FROM prompt_tags "
                "WHERE prompt_id=p.id ORDER BY tag_id)),"
                "(SELECT title FROM prompt_library_state WHERE prompt_id=p.id),"
                "(SELECT COUNT(*) FROM generated_images WHERE prompt_id=p.id),"
                "(SELECT MAX(id) FROM generated_images WHERE prompt_id=p.id) "
                f"FROM prompts p WHERE p.id IN ({marks})",
                chunk,
            ):
                prints[row[0]] = [
                    value if isinstance(value, (int, str, type(None))) else repr(value)
                    for value in tuple(row)[1:]
                ]
        return prints

    @staticmethod
    def _checked_ids(prompt_ids: list[int], expected_epoch: str) -> list[int]:
        _validate_epoch(expected_epoch)
        _validate_bulk_ids(prompt_ids)
        return list(dict.fromkeys(prompt_ids))

    def classify_library_delete(
        self, prompt_ids: list[int], *, expected_epoch: str
    ) -> dict:
        """What a delete of ``prompt_ids`` would do now (reads only).

        Returns the id lists per class, in request order, ``image_links``
        (the image link rows the ``delete`` class would remove) and
        ``fingerprints`` of the ``delete`` class (``_fingerprints``).
        """
        prompt_ids = self._checked_ids(prompt_ids, expected_epoch)
        with self.library_snapshot() as (conn, _epoch, _ceiling):
            epoch = self._library_epoch(conn, expected_epoch)
            classes = self._classify_library_delete(conn, prompt_ids)
            return {
                "library_epoch": epoch,
                **classes,
                "image_links": self._image_links(conn, classes["delete"]),
                "fingerprints": self._fingerprints(conn, classes["delete"]),
            }

    @staticmethod
    def _repair_hash(conn: sqlite3.Connection, prompt_id: int) -> None:
        """Give an archived prompt its canonical hash, so ingestion finds it.

        Scans, the monitor and node saves resolve prompts by hash; a stale or
        missing one would make them insert a visible copy (Q3). When another
        row already holds the canonical hash, lookups find that row and no new
        copy is made, so nothing is written.
        """
        text, stored = conn.execute(
            "SELECT text,hash FROM prompts WHERE id=?", (prompt_id,)
        ).fetchone()
        canonical = generate_prompt_hash(text) if isinstance(text, str) else None
        if canonical is None or stored == canonical:
            return
        if conn.execute(
            "SELECT 1 FROM prompts WHERE hash=? AND id!=?", (canonical, prompt_id)
        ).fetchone():
            return
        conn.execute("UPDATE prompts SET hash=? WHERE id=?", (canonical, prompt_id))

    def archive_or_delete_library_prompts(
        self,
        archive_ids: list[int],
        delete_ids: list[int],
        *,
        expected_epoch: str,
        candidates: dict | None,
    ) -> dict:
        """Archive and delete what the user reviewed, all or nothing.

        ``archive_ids`` and ``delete_ids`` are the reviewed classes.
        ``candidates`` maps each reviewed delete the route backed up to its
        fingerprint then (None: every reviewed delete, unchecked; tests and
        callers without a backup). A reviewed delete is deleted only when it
        still classifies ``delete`` and its fingerprint is unchanged; one that
        is now paired, and a reviewed archive now unpaired, are archived (the
        restorable direction); anything else is kept and counted.
        """
        _validate_epoch(expected_epoch)
        for ids in (archive_ids, delete_ids):
            if not isinstance(ids, list) or any(
                type(pid) is not int or not 1 <= pid <= 2**63 - 1 for pid in ids
            ):
                raise ValueError("prompt ids must be lists of positive integers")
        reviewed_delete = set(delete_ids)
        if not archive_ids and not delete_ids:
            raise ValueError("Nothing to delete or archive")
        if reviewed_delete & set(archive_ids):
            raise ValueError("A prompt cannot be both archived and deleted")
        prompt_ids = list(dict.fromkeys([*archive_ids, *delete_ids]))
        with self._pair_transaction() as conn:
            epoch = self._library_epoch(conn, expected_epoch)
            classes = self._classify_library_delete(conn, prompt_ids)
            now_prints = self._fingerprints(conn, classes["delete"])
            deletes, archives, changed = [], list(classes["archive"]), 0
            for pid in classes["delete"]:
                if pid not in reviewed_delete:
                    archives.append(pid)  # reviewed as archive, now unpaired
                elif candidates is None or candidates.get(pid) == now_prints[pid]:
                    deletes.append(pid)
                else:
                    changed += 1
            changed += len(classes["protected"])
            image_links = self._image_links(conn, deletes)
            now = utc_now_iso()
            for pid in archives:
                self._repair_hash(conn, pid)
            conn.executemany(
                f"INSERT INTO {ARCHIVE}(prompt_id, archived_at) VALUES (?, ?)",
                [(pid, now) for pid in archives],
            )
            # A changed row is the newest edit the restore fingerprint reads.
            conn.executemany(
                "UPDATE prompts SET updated_at=? WHERE id=?",
                [(now, pid) for pid in archives],
            )
            deleted = delete_prompt_rows(conn, deletes)
            return {
                "library_epoch": epoch,
                "deleted": deleted,
                "archived": len(archives),
                "already_archived": len(classes["already_archived"]),
                "not_found": len(classes["not_found"]),
                "changed": changed,
                "image_links": image_links,
            }

    def restore_library_prompts(
        self, prompt_ids: list[int], *, expected_epoch: str
    ) -> dict:
        """Put archived prompts back on the shelf, all or nothing.

        Every requested id with an archive row is restored, whatever its
        protection or Library parent state: restoring only un-hides it.
        """
        prompt_ids = self._checked_ids(prompt_ids, expected_epoch)
        with self._pair_transaction() as conn:
            epoch = self._library_epoch(conn, expected_epoch)
            restored, existing = [], set()
            for chunk in _batches(prompt_ids):
                marks = ",".join("?" for _ in chunk)
                for pid, archived in conn.execute(
                    f"SELECT p.id,{_ARCHIVED} FROM prompts p "  # nosec B608
                    f"WHERE p.id IN ({marks})",
                    chunk,
                ):
                    existing.add(pid)
                    if archived:
                        restored.append(pid)
            now = utc_now_iso()
            conn.executemany(
                f"DELETE FROM {ARCHIVE} WHERE prompt_id=?", [(pid,) for pid in restored]
            )
            conn.executemany(
                "UPDATE prompts SET updated_at=? WHERE id=?",
                [(now, pid) for pid in restored],
            )
            return {
                "library_epoch": epoch,
                "restored": len(restored),
                "unchanged": len(existing) - len(restored),
                "not_found": len(prompt_ids) - len(existing),
            }
