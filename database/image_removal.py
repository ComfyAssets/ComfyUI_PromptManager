"""Remove a file's records from the library, and its removal mark (#480).

A *removal* describes one physical file the way the API resolved it from the
walked listing (``py/api/image_delete.py``):

- ``spellings``: every path string records may name it by
  (``duplicate_spellings.Spellings.of``);
- ``locator``: its private canonical locator;
- ``favorites``: ``(root key, relative key)`` under every listed root holding
  it, the keys of its favorite and its collection memberships;
- ``category``: its own category's file key, or None.

``remove_image_records`` deletes, in one ``BEGIN IMMEDIATE`` transaction:

- its image records (``generated_images``) under every spelling, plus legacy
  rows matched by ``image_path``; their saved-pair links go by cascade and the
  workflow documents they held are released;
- the image tags and auto-tag marks of its byte records. The annotation file
  and byte rows go too, unless a Needs review item still cites them
  (``media_review_items.byte_id`` is ``ON DELETE RESTRICT``): a decided or
  revised item keeps its decision;
- its pending, undecided Needs review items (settled as duplicate removal
  settles them);
- its own category, favorite and collection memberships.

With ``mark`` it also writes the file's removal mark (library-only removal):
the file stays on disk, hidden until its bytes change. Prompts, recorded runs
and the media file itself are never touched here.
"""

import os

from .annotation_schema import BYTES_TABLE, FILES_TABLE
from .annotations import _BUMP_INDEX
from .autotag_schema import AUTOTAG_TABLE
from .duplicate_copies import (
    DELETE_FAVORITE_SQL,
    DELETE_LINK_SQL,
    DELETE_MEMBERSHIPS_SQL,
    _file_link_rows,
)
from .image_categories import DELETE_SQL as DELETE_CATEGORY_SQL
from .image_removal_schema import (
    META_TABLE,
    TABLE,
    bump_generation,
    clear_marks,
    has_marks_table,
    mark_of,
    write_mark as _write_mark,
)
from .library import display_title
from .models import utc_now_iso
from .review import _BUMP_REVIEW_INDEX, _PENDING_OF_LOCATOR, _SETTLE_ITEM
from .review_changes import notify_review_change
from .workflow_documents import documents_of_rows, release_documents

BYTES_OF_LOCATOR_SQL = (
    f"SELECT b.id FROM {FILES_TABLE} f JOIN {BYTES_TABLE} b "  # nosec B608
    "ON b.file_id=f.id WHERE f.locator=?"
)
TAG_COUNT_SQL = "SELECT COUNT(*) FROM media_image_tags WHERE byte_id=?"
AUTOTAG_COUNT_SQL = (
    f"SELECT COUNT(*) FROM {AUTOTAG_TABLE} WHERE byte_id=?"  # nosec B608
)
CLEAR_TAGS_SQL = "DELETE FROM media_image_tags WHERE byte_id=?"
CLEAR_AUTOTAG_SQL = f"DELETE FROM {AUTOTAG_TABLE} WHERE byte_id=?"  # nosec B608
BUMP_TAG_REVISION_SQL = (
    f"UPDATE {BYTES_TABLE} SET tag_revision=tag_revision+1 WHERE id=?"  # nosec B608
)
REVIEW_REFS_SQL = (
    f"SELECT COUNT(*) FROM {FILES_TABLE} f "  # nosec B608
    f"JOIN {BYTES_TABLE} b ON b.file_id=f.id "
    "JOIN media_review_items i ON i.byte_id=b.id WHERE f.locator=?"
)
DELETE_FILE_SQL = f"DELETE FROM {FILES_TABLE} WHERE locator=?"  # nosec B608
FILE_EXISTS_SQL = f"SELECT 1 FROM {FILES_TABLE} WHERE locator=?"  # nosec B608
FAVORITE_EXISTS_SQL = (
    "SELECT COUNT(*) FROM image_favorites WHERE root_key=? AND relative_path=?"
)
MEMBERSHIP_COUNT_SQL = (
    "SELECT COUNT(*) FROM image_collection_items WHERE root_key=? AND relative_path=?"
)
CATEGORY_EXISTS_SQL = (
    "SELECT COUNT(*) FROM image_categories WHERE root_key=? AND relative_path=?"
)
PAIR_LINK_COUNT_SQL = "SELECT COUNT(*) FROM image_prompt_pairs WHERE image_id=?"
PROMPT_LINKS_SQL = "SELECT id FROM generated_images WHERE prompt_id=?"
PROMPT_TITLE_SQL = (
    "SELECT p.text, s.title FROM prompts p "
    "LEFT JOIN prompt_library_state s ON s.prompt_id=p.id WHERE p.id=?"
)
MARKS_SQL = f"SELECT locator, size, mtime_ns FROM {TABLE}"  # nosec B608
REVISION_SQL = f"SELECT revision FROM {META_TABLE} WHERE singleton_id=1"  # nosec B608
MAX_ORPHAN_TITLES = 3
RECORD_KINDS = (
    "prompt_links",
    "pair_links",
    "image_tags",
    "auto_tagged",
    "categories",
    "collections",
    "favorites",
    "review_items",
)


def _link_ids(conn, removal):
    """``{image id: prompt id}`` of the records naming the file, any spelling."""
    found = {}
    for spelled in dict.fromkeys(removal["spellings"]):
        for image_id, prompt_id in _file_link_rows(conn, spelled):
            found[image_id] = prompt_id
    return found


def _byte_ids(conn, locator):
    return [row[0] for row in conn.execute(BYTES_OF_LOCATOR_SQL, (locator,))]


def file_locator(path) -> str:
    """The locator of an absolute path (``normcase(realpath)``)."""
    return os.path.normcase(os.path.realpath(str(path)))


def removed_from_library(conn, path) -> bool:
    """Whether *path* is hidden by a removal mark whose bytes still match.

    A mark whose file changed (other size or ``mtime_ns``) or is gone is
    stale: it is deleted on *conn* (the caller's write transaction), and the
    file is not hidden.
    """
    locator = file_locator(path)
    mark = mark_of(conn, locator)
    if mark is None:
        return False
    try:
        info = os.lstat(path)
    except OSError:
        clear_marks(conn, [locator])
        return False
    if (info.st_size, info.st_mtime_ns) == tuple(mark):
        return True
    clear_marks(conn, [locator])
    return False


def mark_hides(marks: dict, locator: str, path) -> bool:
    """Whether a removal mark in *marks* hides the file at *path* (blocking).

    *marks* is ``{locator: (size, mtime_ns)}``. The mark of *locator* hides
    the file while ``os.lstat(path)`` still has the size and ``mtime_ns`` it
    was removed with; a file that cannot be looked at is not hidden.
    """
    mark = marks.get(locator)
    if mark is None:
        return False
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return (info.st_size, info.st_mtime_ns) == tuple(mark)


def write_mark(conn, locator, size, mtime_ns):
    _write_mark(conn, locator, size, mtime_ns, utc_now_iso())


class ImageRemovalOperations:
    """PromptDatabase mixin: remove a file's records; removal marks."""

    def count_removal_records(self, removals) -> dict:
        """Counts of what removing *removals* deletes, in one read snapshot.

        ``{records: {kind: n}, orphaned_prompts: {count, titles}}``: prompts
        whose every image record is among these files lose their last image
        (they stay in the Library).
        """
        from .maintenance import _read_snapshot

        def read(conn):
            totals = dict.fromkeys(RECORD_KINDS, 0)
            links = {}
            for removal in removals:
                found = _link_ids(conn, removal)
                links.update(found)
                for image_id in found:
                    totals["pair_links"] += conn.execute(
                        PAIR_LINK_COUNT_SQL, (image_id,)
                    ).fetchone()[0]
                tagged = autotagged = False
                for byte_id in _byte_ids(conn, removal["locator"]):
                    tagged |= bool(
                        conn.execute(TAG_COUNT_SQL, (byte_id,)).fetchone()[0]
                    )
                    autotagged |= bool(
                        conn.execute(AUTOTAG_COUNT_SQL, (byte_id,)).fetchone()[0]
                    )
                totals["image_tags"] += int(tagged)
                totals["auto_tagged"] += int(autotagged)
                totals["review_items"] += len(
                    conn.execute(_PENDING_OF_LOCATOR, (removal["locator"],)).fetchall()
                )
                totals["favorites"] += int(
                    any(
                        conn.execute(FAVORITE_EXISTS_SQL, key).fetchone()[0]
                        for key in removal["favorites"]
                    )
                )
                totals["collections"] += sum(
                    conn.execute(MEMBERSHIP_COUNT_SQL, key).fetchone()[0]
                    for key in removal["favorites"]
                )
                if removal.get("category"):
                    totals["categories"] += conn.execute(
                        CATEGORY_EXISTS_SQL, removal["category"]
                    ).fetchone()[0]
            totals["prompt_links"] = len(links)
            return {
                "records": totals,
                "orphaned_prompts": self._orphaned_prompts(conn, links),
            }

        return _read_snapshot(self.model.db_path, read)

    @staticmethod
    def _orphaned_prompts(conn, links):
        removed = set(links)
        orphans = []
        for prompt_id in dict.fromkeys(links.values()):
            remaining = [
                row[0]
                for row in conn.execute(PROMPT_LINKS_SQL, (prompt_id,))
                if row[0] not in removed
            ]
            if not remaining:
                orphans.append(prompt_id)
        titles = []
        for prompt_id in orphans[:MAX_ORPHAN_TITLES]:
            row = conn.execute(PROMPT_TITLE_SQL, (prompt_id,)).fetchone()
            if row is not None:
                titles.append(display_title((row[1],), row[0]) or "")
        return {"count": len(orphans), "titles": titles}

    def remove_image_records(self, removal, *, mark=None) -> dict:
        """Delete every record of one file (one transaction); see the module.

        ``mark``: ``(size, mtime_ns)`` to write the file's removal mark, or
        None (the file was moved to the trash). Returns the counts deleted.
        """
        counts = dict.fromkeys(RECORD_KINDS, 0)
        locator = removal["locator"]
        with self._pair_transaction() as conn:
            links = _link_ids(conn, removal)
            if links:
                documents = documents_of_rows(conn, "id", list(links))
                for image_id in links:
                    counts["pair_links"] += conn.execute(
                        PAIR_LINK_COUNT_SQL, (image_id,)
                    ).fetchone()[0]
                    conn.execute(DELETE_LINK_SQL, (image_id,))
                release_documents(conn, documents)
            counts["prompt_links"] = len(links)
            counts.update(self._remove_annotations(conn, locator))
            for key in removal["favorites"]:
                counts["favorites"] += conn.execute(DELETE_FAVORITE_SQL, key).rowcount
                counts["collections"] += conn.execute(
                    DELETE_MEMBERSHIPS_SQL, key
                ).rowcount
            if removal.get("category"):
                counts["categories"] += conn.execute(
                    DELETE_CATEGORY_SQL, tuple(removal["category"])
                ).rowcount
            if mark is not None:
                write_mark(conn, locator, *mark)
        if counts["review_items"]:
            notify_review_change(self.model.db_path)
        return counts

    def _remove_annotations(self, conn, locator) -> dict:
        """Tags, auto-tag marks, pending review items and, when free, the rows."""
        counts = {"image_tags": 0, "auto_tagged": 0, "review_items": 0}
        bytes_ = _byte_ids(conn, locator)
        for byte_id in bytes_:
            tags = conn.execute(CLEAR_TAGS_SQL, (byte_id,)).rowcount
            autotags = conn.execute(CLEAR_AUTOTAG_SQL, (byte_id,)).rowcount
            if tags:
                conn.execute(BUMP_TAG_REVISION_SQL, (byte_id,))
            counts["image_tags"] |= bool(tags)
            counts["auto_tagged"] |= bool(autotags)
        for (item_id,) in conn.execute(_PENDING_OF_LOCATOR, (locator,)).fetchall():
            counts["review_items"] += conn.execute(_SETTLE_ITEM, (item_id,)).rowcount
        if counts["review_items"]:
            conn.execute(_BUMP_REVIEW_INDEX)
        if conn.execute(FILE_EXISTS_SQL, (locator,)).fetchone():
            if not conn.execute(REVIEW_REFS_SQL, (locator,)).fetchone()[0]:
                conn.execute(DELETE_FILE_SQL, (locator,))
            conn.execute(_BUMP_INDEX)
        counts["image_tags"] = int(counts["image_tags"])
        counts["auto_tagged"] = int(counts["auto_tagged"])
        return counts

    def removal_marks(self) -> dict:
        """``{locator: (size, mtime_ns)}`` of every removal mark (one read)."""
        conn = self.model.get_connection()
        if not has_marks_table(conn):
            return {}
        return {row[0]: (row[1], row[2]) for row in conn.execute(MARKS_SQL)}

    def removal_revision(self) -> int:
        conn = self.model.get_connection()
        if not has_marks_table(conn):
            return 0
        row = conn.execute(REVISION_SQL).fetchone()
        return row[0] if row else 0

    def clear_removal_marks(self, locators) -> int:
        """Show these files again (their marks go); the count removed."""
        locators = list(locators)
        if not locators:
            return 0
        with self._pair_transaction() as conn:
            removed = clear_marks(conn, locators)
        # #498: again after the commit, so a reader that cached the marks
        # between the in-transaction bump and the commit reloads them.
        bump_generation()
        return removed

    def clear_hiding_marks(self, files) -> set:
        """Delete the marks that still hide their file (#498, Return to library).

        *files* are ``(locator, path)`` pairs. In one ``BEGIN IMMEDIATE``
        transaction each mark is read again and deleted only while the file at
        *path* still has its size and ``mtime_ns``. Returns the locators whose
        marks went.
        """
        cleared = set()
        files = list(files)
        if not files:
            return cleared
        with self._pair_transaction() as conn:
            for locator, path in files:
                mark = mark_of(conn, locator)
                if mark is not None and mark_hides({locator: mark}, locator, path):
                    cleared.add(locator)
            clear_marks(conn, sorted(cleared))
        bump_generation()
        return cleared
