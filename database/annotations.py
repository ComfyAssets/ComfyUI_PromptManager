"""Per-image tag sets (#249): byte-exact storage in the shared vocabulary.

A tag set belongs to one byte record (``media_annotation_bytes``): the
whole-file SHA-256 of one physical file, owned by the file's private locator
(``media_annotation_files``). Callers pass verified facts (digest, size,
``mtime_ns``); nothing here reads the filesystem or resolves an owner through
prompt links, so tagging one image never changes another image, a prompt or a
pair.

Commands run in one ``_pair_transaction`` fenced by the storage epoch, like the
native Library and vocabulary commands. A save is a full replacement guarded
by the set's ``tag_revision``; the identical set sent again (a lost-response
retry, or the current set at its own revision) answers ``replayed: true`` and
writes nothing. ``add_image_tags`` unions into the existing set, keeping the
stored spelling. Names enter the global ``tags`` registry exactly as spelled,
so vocabulary rename, merge and delete carry image links with prompt and pair
links (``PromptDatabase._move_tag_links``).

Cheap reads (spec amendment S1) treat a byte record as *current* only while
the file's present ``(size, mtime_ns)`` equals the record's verified
fingerprint; a file without a current record shows no image tags.

A write made from a tagging model's result passes ``mark_autotagged`` (#379):
the byte record is then marked auto-tagged (``media_image_autotags``) in the
same transaction, so a mark never exists without the write it records. Hand
tags never mark.
"""

import re
import sqlite3
from contextlib import contextmanager
from typing import Iterator
from uuid import uuid4

from .annotation_schema import (
    BYTES_TABLE,
    FILES_TABLE,
    IMAGE_TAGS_TABLE,
    MAX_LOCATOR_LENGTH,
    METADATA_TABLE,
)
from .autotag_schema import AUTOTAG_TABLE
from .image_removal_schema import clear_marks
from .library_commands import _UUID, LibraryCommandError, _validate_epoch
from .models import utc_now_iso
from .vocabulary import validate_vocabulary_tag_name

MAX_IMAGE_TAGS = 100
_LOOKUP_CHUNK = 500
_DIGEST = re.compile(r"[0-9a-f]{64}")
_MAX_INT = 2**63 - 1
_MIN_INT = -(2**63)
# Only this module's table constants are interpolated into the SQL below.
_OWNER = f"SELECT id FROM {FILES_TABLE} WHERE locator=?"  # nosec B608
_INSERT_OWNER = (
    f"INSERT INTO {FILES_TABLE}(id,locator,created_at) VALUES(?,?,?)"  # nosec B608
)
_BYTE = (
    f"SELECT id,tag_revision,size,mtime_ns FROM {BYTES_TABLE} "  # nosec B608
    "WHERE file_id=? AND sha256=?"
)
_INSERT_BYTE = (
    f"INSERT INTO {BYTES_TABLE}(id,file_id,sha256,size,mtime_ns,"  # nosec B608
    "verified_at,first_seen_at,tag_revision) VALUES(?,?,?,?,?,?,?,0)"
)
_REFRESH_BYTE = (
    f"UPDATE {BYTES_TABLE} SET size=?,mtime_ns=?,verified_at=? WHERE id=?"  # nosec B608
)
_REFRESH_BY_LOCATOR = (
    f"UPDATE {BYTES_TABLE} SET size=?,mtime_ns=?,verified_at=? "  # nosec B608
    f"WHERE sha256=? AND file_id=({_OWNER})"
)
_BYTE_TAGS = (
    f"SELECT t.name FROM {IMAGE_TAGS_TABLE} mit "  # nosec B608
    "JOIN tags t ON t.id=mit.tag_id WHERE mit.byte_id=? ORDER BY mit.position"
)
_CLEAR_TAGS = f"DELETE FROM {IMAGE_TAGS_TABLE} WHERE byte_id=?"  # nosec B608
_INSERT_TAG = (
    f"INSERT INTO {IMAGE_TAGS_TABLE}(byte_id,tag_id,position) "  # nosec B608
    "VALUES(?,?,?)"
)
_BUMP_REVISION = (
    f"UPDATE {BYTES_TABLE} SET tag_revision=tag_revision+1 WHERE id=?"  # nosec B608
)
_REVISION = f"SELECT tag_revision FROM {BYTES_TABLE} WHERE id=?"  # nosec B608
_BUMP_INDEX = (
    f"UPDATE {METADATA_TABLE} SET "  # nosec B608
    "image_index_revision=image_index_revision+1 WHERE singleton_id=1"
)
_READ = (
    "SELECT b.id,b.tag_revision,"  # nosec B608
    f"(SELECT COUNT(*) FROM {BYTES_TABLE} o WHERE o.file_id=f.id AND o.sha256!=?) "
    f"FROM {FILES_TABLE} f LEFT JOIN {BYTES_TABLE} b "
    "ON b.file_id=f.id AND b.sha256=? WHERE f.locator=?"
)
_FINGERPRINTS = (
    "SELECT f.locator,b.id,b.size,b.mtime_ns,b.verified_at "  # nosec B608
    f"FROM {FILES_TABLE} f JOIN {BYTES_TABLE} b ON b.file_id=f.id "
    "WHERE f.locator IN ({marks})"
)
_TAGS_OF = (
    f"SELECT mit.byte_id,t.name FROM {IMAGE_TAGS_TABLE} mit "  # nosec B608
    "JOIN tags t ON t.id=mit.tag_id WHERE mit.byte_id IN ({marks}) "
    "ORDER BY mit.byte_id,mit.position"
)
_MARK = f"INSERT OR IGNORE INTO {AUTOTAG_TABLE}(byte_id) VALUES(?)"  # nosec B608
_MARKED_OF = (
    f"SELECT byte_id FROM {AUTOTAG_TABLE} WHERE byte_id IN ({{marks}})"  # nosec B608
)
_MARKED_LOCATORS = (
    f"SELECT DISTINCT f.locator FROM {AUTOTAG_TABLE} m "  # nosec B608
    f"JOIN {BYTES_TABLE} b ON b.id=m.byte_id JOIN {FILES_TABLE} f ON f.id=b.file_id"
)
# Tag search (#379): the names that tag at least one image (the tag index
# answers the EXISTS), then the locators carrying chosen names (by tag_id).
_IMAGE_TAG_NAMES = (
    "SELECT t.id,t.name FROM tags t WHERE EXISTS "  # nosec B608
    f"(SELECT 1 FROM {IMAGE_TAGS_TABLE} mit WHERE mit.tag_id=t.id)"
)
_TAG_LOCATORS = (
    f"SELECT DISTINCT f.locator FROM {IMAGE_TAGS_TABLE} mit "  # nosec B608
    f"JOIN {BYTES_TABLE} b ON b.id=mit.byte_id "
    f"JOIN {FILES_TABLE} f ON f.id=b.file_id WHERE mit.tag_id IN ({{marks}})"
)


class AnnotationCommandError(LibraryCommandError):
    """An image tag command lost a race or hit the set's bound.

    ``code`` is ``tag-conflict`` (the set changed since it was read) or
    ``tag-limit`` (the union would exceed ``MAX_IMAGE_TAGS``); ``current`` is
    the stored set ``{sha256, tags, tag_revision}`` read inside the
    transaction, so the caller can show it.
    """

    def __init__(self, code: str, epoch: str, current: dict | None = None):
        super().__init__(code, epoch)
        self.current = current


class ImageTagLimitError(ValueError):
    """More than ``MAX_IMAGE_TAGS`` distinct names in one set."""


def normalize_image_tags(names: object) -> list[str]:
    """Trim and validate each name, fold case-insensitive repeats, cap at 100.

    The first occurrence keeps its spelling and position. Raises ``ValueError``
    for anything but a list of valid vocabulary names, and its subclass
    ``ImageTagLimitError`` for more than ``MAX_IMAGE_TAGS`` distinct names.
    """
    if not isinstance(names, list):
        raise ValueError("tags must be a list of strings")
    result: list[str] = []
    seen: set[str] = set()
    for value in names:
        name = validate_vocabulary_tag_name(value)
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(name)
    if len(result) > MAX_IMAGE_TAGS:
        raise ImageTagLimitError(f"An image can carry at most {MAX_IMAGE_TAGS} tags")
    return result


def _validate_locator(locator: object) -> None:
    if not isinstance(locator, str) or not 0 < len(locator) <= MAX_LOCATOR_LENGTH:
        raise ValueError(
            f"locator must be a nonblank path of at most {MAX_LOCATOR_LENGTH} chars"
        )


def _validate_digest(sha256: object) -> None:
    if not isinstance(sha256, str) or not _DIGEST.fullmatch(sha256):
        raise ValueError("sha256 must be 64 lowercase hexadecimal characters")


def _validate_int(value: object, name: str, minimum: int) -> None:
    if type(value) is not int or not minimum <= value <= _MAX_INT:
        raise ValueError(f"{name} must be an integer not below {minimum}")


def _validate_target(locator: object, sha256: object, size: object, mtime_ns: object):
    _validate_locator(locator)
    _validate_digest(sha256)
    _validate_int(size, "size", 0)
    _validate_int(mtime_ns, "mtime_ns", _MIN_INT)


def _validate_fingerprint(locator: object, fingerprint: object) -> None:
    _validate_locator(locator)
    if (
        not isinstance(fingerprint, tuple)
        or len(fingerprint) != 2
        or any(type(value) is not int for value in fingerprint)
    ):
        raise ValueError("stats must map locators to (size, mtime_ns) integer pairs")


def _new_id() -> str:
    """A canonical UUID; the schema's CHECK lets NULL through, this does not."""
    value = str(uuid4())
    if not _UUID.fullmatch(value):
        raise ValueError("Annotation ids must be canonical UUIDs")
    return value


def _receipt(epoch: str, sha256: str, tags: list, revision: int, replayed: bool):
    return {
        "library_epoch": epoch,
        "sha256": sha256,
        "tags": list(tags),
        "tag_revision": revision,
        "replayed": replayed,
    }


class AnnotationOperations:
    """PromptDatabase mixin: image tag sets, fingerprints and cheap reads.

    ``_`` helpers take the open ``_pair_transaction`` connection.
    """

    @contextmanager
    def _annotation_read(self) -> Iterator[sqlite3.Connection]:
        """One deferred read transaction, so paired SELECTs see one snapshot."""
        conn = self.model.get_connection()
        if conn.in_transaction:
            raise sqlite3.ProgrammingError(
                "Annotation reads require their own snapshot"
            )
        with conn:
            conn.execute("BEGIN")
            yield conn

    @staticmethod
    def _annotation_owner(conn: sqlite3.Connection, locator: str, now: str) -> str:
        # A file with image tags or Needs review state is in the library: a
        # removal mark never hides it (#480).
        clear_marks(conn, [locator])
        row = conn.execute(_OWNER, (locator,)).fetchone()
        if row is not None:
            return row[0]
        file_id = _new_id()
        conn.execute(_INSERT_OWNER, (file_id, locator, now))
        return file_id

    @staticmethod
    def _annotation_byte(
        conn: sqlite3.Connection,
        file_id: str,
        sha256: str,
        size: int,
        mtime_ns: int,
        now: str,
    ) -> tuple[str, int, bool, bool]:
        """The byte record for these bytes, refreshed or created.

        Returns ``(id, tag_revision, created, refingerprinted)``; the last is
        True when an existing record's verified ``(size, mtime_ns)`` changed,
        which moves its currency for cheap reads (the caller bumps the index
        revision when nothing else does).
        """
        row = conn.execute(_BYTE, (file_id, sha256)).fetchone()
        if row is None:
            byte_id = _new_id()
            conn.execute(
                _INSERT_BYTE, (byte_id, file_id, sha256, size, mtime_ns, now, now)
            )
            return byte_id, 0, True, False
        conn.execute(_REFRESH_BYTE, (size, mtime_ns, now, row[0]))
        return row[0], row[1], False, (row[2], row[3]) != (size, mtime_ns)

    @staticmethod
    def _byte_tags(conn: sqlite3.Connection, byte_id: str) -> list[str]:
        return [row[0] for row in conn.execute(_BYTE_TAGS, (byte_id,))]

    def _write_image_tags(
        self, conn: sqlite3.Connection, byte_id: str, tags: list[str]
    ) -> int:
        """Rewrite the junction in order, bump both revisions; return the new one."""
        tag_ids = self._ensure_tags(conn, tags)
        conn.execute(_CLEAR_TAGS, (byte_id,))
        conn.executemany(
            _INSERT_TAG,
            [(byte_id, tag_ids[name], position) for position, name in enumerate(tags)],
        )
        conn.execute(_BUMP_REVISION, (byte_id,))
        conn.execute(_BUMP_INDEX)
        return conn.execute(_REVISION, (byte_id,)).fetchone()[0]

    def _current_set(self, conn, sha256, byte_id, revision) -> dict:
        return {
            "sha256": sha256,
            "tags": self._byte_tags(conn, byte_id),
            "tag_revision": revision,
        }

    def read_image_tags(self, locator: str, sha256: str) -> dict:
        """The stored set of these exact bytes, or an empty set at revision 0.

        ``prior_count`` is the number of other byte records at the locator
        (earlier or later bytes of the same file).
        """
        _validate_locator(locator)
        _validate_digest(sha256)
        with self._annotation_read() as conn:
            row = conn.execute(_READ, (sha256, sha256, locator)).fetchone()
            if row is None:
                return {"tags": [], "tag_revision": 0, "prior_count": 0}
            tags = self._byte_tags(conn, row[0]) if row[0] is not None else []
            return {"tags": tags, "tag_revision": row[1] or 0, "prior_count": row[2]}

    def replace_image_tags(
        self,
        *,
        locator: str,
        sha256: str,
        size: int,
        mtime_ns: int,
        expected_epoch: str,
        expected_tag_revision: int,
        tags: list,
        mark_autotagged: bool = False,
    ) -> dict:
        """Replace the set of these exact bytes, guarded by epoch and revision.

        Creates the owner and byte record when the bytes are new (an explicit
        empty set is a real save). The identical set at the current revision,
        or one behind it (a lost-response retry), is ``replayed`` and writes
        no tag; any other revision mismatch is ``tag-conflict`` with the
        current set and no writes. ``mark_autotagged`` marks the bytes
        auto-tagged on a saved or replayed receipt (the Tag image dialog's
        save after Generate suggestions).
        """
        _validate_target(locator, sha256, size, mtime_ns)
        _validate_epoch(expected_epoch)
        _validate_int(expected_tag_revision, "expected_tag_revision", 0)
        tags = normalize_image_tags(tags)
        with self._pair_transaction() as conn:
            epoch = self._library_epoch(conn, expected_epoch)
            now = utc_now_iso()
            file_id = self._annotation_owner(conn, locator, now)
            byte_id, revision, created, refingerprinted = self._annotation_byte(
                conn, file_id, sha256, size, mtime_ns, now
            )
            current = self._byte_tags(conn, byte_id)
            if (
                not created
                and current == tags
                and expected_tag_revision in (revision, revision - 1)
            ):
                if refingerprinted:
                    conn.execute(_BUMP_INDEX)
                if mark_autotagged:
                    conn.execute(_MARK, (byte_id,))
                return _receipt(epoch, sha256, current, revision, True)
            if expected_tag_revision != revision:
                raise AnnotationCommandError(
                    "tag-conflict",
                    epoch,
                    {"sha256": sha256, "tags": current, "tag_revision": revision},
                )
            revision = self._write_image_tags(conn, byte_id, tags)
            if mark_autotagged:
                conn.execute(_MARK, (byte_id,))
            return _receipt(epoch, sha256, tags, revision, False)

    def add_image_tags(
        self,
        *,
        locator: str,
        sha256: str,
        size: int,
        mtime_ns: int,
        expected_epoch: str,
        tags: list,
        mark_autotagged: bool = False,
    ) -> dict:
        """Union ``tags`` into the set of these exact bytes, keeping stored spelling.

        ``added`` counts the names that were new (case-insensitively). A union
        over ``MAX_IMAGE_TAGS`` is ``tag-limit`` with the current set and no
        writes; a union that adds nothing to an existing record is ``replayed``.
        ``mark_autotagged`` (a tagging model's result) also marks the bytes
        auto-tagged, replayed or not; only such a call may pass no tags (the
        model found none: the image is marked and its set is unchanged).
        """
        _validate_target(locator, sha256, size, mtime_ns)
        _validate_epoch(expected_epoch)
        tags = normalize_image_tags(tags)
        if not tags and not mark_autotagged:
            raise ValueError("tags must name at least one tag")
        with self._pair_transaction() as conn:
            epoch = self._library_epoch(conn, expected_epoch)
            now = utc_now_iso()
            file_id = self._annotation_owner(conn, locator, now)
            byte_id, revision, created, refingerprinted = self._annotation_byte(
                conn, file_id, sha256, size, mtime_ns, now
            )
            current = self._byte_tags(conn, byte_id)
            present = {name.casefold() for name in current}
            added = [name for name in tags if name.casefold() not in present]
            merged = current + added
            if len(merged) > MAX_IMAGE_TAGS:
                raise AnnotationCommandError(
                    "tag-limit",
                    epoch,
                    {"sha256": sha256, "tags": current, "tag_revision": revision},
                )
            if mark_autotagged:
                conn.execute(_MARK, (byte_id,))
            if not created and not added:
                if refingerprinted:
                    conn.execute(_BUMP_INDEX)
                return {**_receipt(epoch, sha256, current, revision, True), "added": 0}
            revision = self._write_image_tags(conn, byte_id, merged)
            return {
                **_receipt(epoch, sha256, merged, revision, False),
                "added": len(added),
            }

    def refresh_image_fingerprint(
        self, locator: str, sha256: str, size: int, mtime_ns: int
    ) -> bool:
        """Record that these exact bytes were just seen with this stat.

        True when a byte record was refreshed; nothing is created and no tag
        or tag revision changes. The image index revision moves because the
        record's currency for cheap reads may have changed.
        """
        _validate_target(locator, sha256, size, mtime_ns)
        with self.model.get_connection() as conn:
            cursor = conn.execute(
                _REFRESH_BY_LOCATOR, (size, mtime_ns, utc_now_iso(), sha256, locator)
            )
            if cursor.rowcount > 0:
                conn.execute(_BUMP_INDEX)
            return cursor.rowcount > 0

    @staticmethod
    def _validated_stats(stats: object) -> dict:
        if not isinstance(stats, dict):
            raise ValueError(
                "stats must map locators to (size, mtime_ns) integer pairs"
            )
        for locator, fingerprint in stats.items():
            _validate_fingerprint(locator, fingerprint)
        return stats

    @staticmethod
    def _current_byte_records(conn: sqlite3.Connection, stats: dict) -> dict:
        """``{locator: byte_id}`` of each locator's current byte record.

        The one rule every cheap read uses: a record is current while its
        verified ``(size, mtime_ns)`` equals the file's present fingerprint
        in ``stats``; the most recently verified wins (ties on ``(verified_at,
        byte_id)``). A locator without a current record is left out. Lookups
        go in chunks of 500 locators.
        """
        locators = list(stats)
        chosen: dict[str, tuple[tuple[str, str], str]] = {}
        for start in range(0, len(locators), _LOOKUP_CHUNK):
            chunk = locators[start : start + _LOOKUP_CHUNK]
            marks = ",".join("?" * len(chunk))
            for locator, byte_id, size, mtime_ns, verified_at in conn.execute(
                _FINGERPRINTS.format(marks=marks), chunk
            ):
                if (size, mtime_ns) != stats[locator]:
                    continue
                key = (verified_at, byte_id)
                if locator not in chosen or key > chosen[locator][0]:
                    chosen[locator] = (key, byte_id)
        return {locator: byte_id for locator, (_key, byte_id) in chosen.items()}

    def current_image_tags(self, stats: dict) -> dict:
        """Image tags of the files whose current ``(size, mtime_ns)`` is verified.

        ``stats`` maps locators to their present fingerprint. A locator is
        answered (possibly with an empty set) only when a byte record carries
        exactly that verified fingerprint; the most recently verified record
        wins when several do. Lookups go in chunks of 500 locators.
        """
        stats = self._validated_stats(stats)
        with self._annotation_read() as conn:
            current = self._current_byte_records(conn, stats)
            owners = {byte_id: locator for locator, byte_id in current.items()}
            result: dict[str, list[str]] = {locator: [] for locator in current}
            byte_ids = list(owners)
            for start in range(0, len(byte_ids), _LOOKUP_CHUNK):
                chunk = byte_ids[start : start + _LOOKUP_CHUNK]
                marks = ",".join("?" * len(chunk))
                for byte_id, name in conn.execute(_TAGS_OF.format(marks=marks), chunk):
                    result[owners[byte_id]].append(name)
        return result

    def autotag_state(self, stats: dict) -> dict:
        """``{locator: bool}``: is each file's current byte record auto-tagged?

        ``stats`` as for :meth:`current_image_tags`, whose rule picks the
        current record; a locator without one is False. One read snapshot.
        """
        stats = self._validated_stats(stats)
        with self._annotation_read() as conn:
            current = self._current_byte_records(conn, stats)
            byte_ids = list(dict.fromkeys(current.values()))
            marked: set[str] = set()
            for start in range(0, len(byte_ids), _LOOKUP_CHUNK):
                chunk = byte_ids[start : start + _LOOKUP_CHUNK]
                marks = ",".join("?" * len(chunk))
                marked.update(
                    row[0]
                    for row in conn.execute(_MARKED_OF.format(marks=marks), chunk)
                )
        return {locator: current.get(locator) in marked for locator in stats}

    def autotagged_locators(self) -> set:
        """Locators with at least one auto-tagged byte record, current or not.

        No fingerprint is checked (the Tag output images review's estimate).
        """
        with self._annotation_read() as conn:
            return {row[0] for row in conn.execute(_MARKED_LOCATORS)}

    def image_tag_ids(self, wanted, query: str) -> list[int]:
        """Ids of the tag names on at least one image that a search wants.

        A name counts when it is in ``wanted`` exactly (the Tags filter) or
        contains ``query`` case-folded (the text search; ``query`` is
        already folded). One read of the names that tag an image.
        """
        wanted = set(wanted)
        with self._annotation_read() as conn:
            return [
                tag_id
                for tag_id, name in conn.execute(_IMAGE_TAG_NAMES)
                if name in wanted or (query and query in name.casefold())
            ]

    def image_tag_locators(self, tag_ids) -> set[str]:
        """Locators with any byte record (current or not) carrying one of
        ``tag_ids``, through the tag index, in chunks of 500 ids."""
        ids = list(dict.fromkeys(tag_ids))
        found: set[str] = set()
        with self._annotation_read() as conn:
            for start in range(0, len(ids), _LOOKUP_CHUNK):
                chunk = ids[start : start + _LOOKUP_CHUNK]
                marks = ",".join("?" * len(chunk))
                found.update(
                    row[0]
                    for row in conn.execute(_TAG_LOCATORS.format(marks=marks), chunk)
                )
        return found
