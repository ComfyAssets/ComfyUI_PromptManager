"""Image categories (#332): an image file's own category.

An image shows its own category when it has one, otherwise the category its
link supplies (its recipe's, else its positive prompt's); this table holds
only the own values. A row is keyed by the file key: the deepest configured
output source that contains the file (its real, case-normalized path) and the
path below it, case-folded where the OS folds case
(``py/api/images.py`` ``image_category_key``). Without nested sources the key
equals a favorite's key. Nothing follows a moved file: its row stays and is
hidden until a file appears at the same path again.

Writes validate strictly (the route strips the value and applies
``validate_category``). Startup checks the structure only, so a value saved
under older rules never stops the library opening; restore also checks the
values. Optional in old backups, required once a database is current.
"""

import re
import sqlite3
from typing import Iterable

TABLE = "image_categories"
INDEX = "idx_image_categories_category"
MAX_KEY_LENGTH = 4096
MAX_CATEGORY_LENGTH = 100
_DDL = (
    f"CREATE TABLE {TABLE} (root_key TEXT NOT NULL, relative_path TEXT NOT NULL, "
    "category TEXT NOT NULL, updated_at TEXT NOT NULL, "
    "PRIMARY KEY (root_key, relative_path))"
)
_INDEX_DDL = f"CREATE INDEX {INDEX} ON {TABLE}(category)"
_AUTOINDEX = f"sqlite_autoindex_{TABLE}_1"
_COLUMNS = (
    ("root_key", "TEXT", 1, 1),
    ("relative_path", "TEXT", 1, 2),
    ("category", "TEXT", 1, 0),
    ("updated_at", "TEXT", 1, 0),
)
# A category may not hold a character below U+0020 (validate_category).
_CONTROL_GLOB = "*[" + "".join(chr(code) for code in range(1, 32)) + "]*"

CATEGORY_OF_SQL = (
    f"SELECT category FROM {TABLE} WHERE root_key = ? AND relative_path = ?"
)
CATEGORIES_OF_ROOT_SQL = (
    f"SELECT relative_path, category FROM {TABLE} WHERE root_key = ?"
)
UPSERT_SQL = (
    f"INSERT INTO {TABLE} (root_key, relative_path, category, updated_at) "
    "VALUES (?, ?, ?, ?) ON CONFLICT(root_key, relative_path) "
    "DO UPDATE SET category = excluded.category, updated_at = excluded.updated_at"
)
DELETE_SQL = f"DELETE FROM {TABLE} WHERE root_key = ? AND relative_path = ?"
# The duplicate cleanup's copy (#332 MC11): the removed copy's row is copied
# to the kept copy only when the kept copy has none; it is never deleted.
COPY_SQL = (
    f"INSERT OR IGNORE INTO {TABLE} (root_key, relative_path, category, updated_at) "
    f"SELECT ?, ?, category, updated_at FROM {TABLE} "
    "WHERE root_key = ? AND relative_path = ?"
)
STATEMENTS = (CATEGORY_OF_SQL, CATEGORIES_OF_ROOT_SQL, UPSERT_SQL, DELETE_SQL, COPY_SQL)


def _compact(sql: str) -> str:
    return re.sub(r"\s+", "", sql or "").lower()


def _structure_error(
    conn: sqlite3.Connection, allow_missing: bool, allow_missing_index: bool = False
) -> str | None:
    objects = conn.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE name=? "
        "OR (type IN ('index','trigger') AND tbl_name=?)",
        (TABLE, TABLE),
    ).fetchall()
    if not objects:
        return None if allow_missing else "Missing image categories schema"
    tables = [sql for kind, _name, sql in objects if kind == "table"]
    others = {(kind, name) for kind, name, _sql in objects if kind != "table"}
    expected = {("index", _AUTOINDEX), ("index", INDEX)}
    # Before its install, a table without the category index is repaired by
    # CREATE INDEX IF NOT EXISTS rather than refused.
    if allow_missing_index and others == {("index", _AUTOINDEX)}:
        expected = others
    if len(tables) != 1 or others != expected:
        return "Image categories name is reserved by an unsupported schema object"
    if _compact(tables[0]) != _compact(_DDL):
        return "Image categories has an invalid table definition"
    index_sql = next((sql for kind, name, sql in objects if name == INDEX), None)
    if ("index", INDEX) in expected and _compact(index_sql) != _compact(_INDEX_DDL):
        return "Image categories has an invalid index"
    autoindex_sql = next(sql for kind, name, sql in objects if name == _AUTOINDEX)
    if autoindex_sql is not None:
        return "Image categories has an invalid index"
    columns = tuple(
        (row[1], row[2].upper(), row[3], row[5])
        for row in conn.execute(f"PRAGMA table_info({TABLE})")
    )
    if columns != _COLUMNS:
        return "Image categories has invalid columns"
    return None


def _values_error(conn: sqlite3.Connection) -> str | None:
    # Bytes bound characters from above (UTF-8 is at most 4 bytes each).
    limit = MAX_KEY_LENGTH * 4
    if conn.execute(
        f"SELECT 1 FROM {TABLE} WHERE typeof(root_key) != 'text' "  # nosec B608
        "OR typeof(relative_path) != 'text' OR typeof(category) != 'text' "
        "OR typeof(updated_at) != 'text' "
        f"OR length(CAST(root_key AS BLOB)) > {limit} "
        f"OR length(CAST(relative_path AS BLOB)) > {limit} "
        "OR length(CAST(updated_at AS BLOB)) > 64 "
        "OR instr(CAST(category AS BLOB), x'00') > 0 "
        f"OR length(category) < 1 OR length(category) > {MAX_CATEGORY_LENGTH} "
        "OR category != TRIM(category) OR category GLOB ? LIMIT 1",
        (_CONTROL_GLOB,),
    ).fetchone():
        return "Image categories contains invalid values"
    for root, path in conn.execute(
        f"SELECT CAST(root_key AS BLOB), CAST(relative_path AS BLOB) "  # nosec B608
        f"FROM {TABLE} WHERE length(CAST(root_key AS BLOB)) > {MAX_KEY_LENGTH} "
        f"OR length(CAST(relative_path AS BLOB)) > {MAX_KEY_LENGTH}"
    ):
        for raw in (root, path):
            if len(bytes(raw).decode("utf-8", "replace")) > MAX_KEY_LENGTH:
                return "Image categories contains invalid values"
    return None


def image_categories_schema_error(
    conn: sqlite3.Connection,
    *,
    allow_missing: bool = True,
    check_values: bool = True,
) -> str | None:
    """Reject an unsupported image categories schema (and, optionally, values).

    Reads only. A missing table is acceptable for backups that predate it
    unless ``allow_missing`` is false. ``check_values`` also refuses a stored
    value the write path could never have written.
    """
    error = _structure_error(conn, allow_missing)
    if error is not None or not check_values:
        return error
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
    ).fetchone()
    return _values_error(conn) if exists else None


def migrate_image_categories(conn: sqlite3.Connection) -> None:
    """Install the table and its index atomically; structure is checked
    before and after. Reads only ``sqlite_master`` and this table's PRAGMAs
    and writes only its own two objects (no backfill)."""
    conn.execute("SAVEPOINT image_categories_schema")
    try:
        error = _structure_error(conn, True, allow_missing_index=True)
        if error:
            raise sqlite3.DatabaseError(error)
        conn.execute(_DDL.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1))
        conn.execute(
            _INDEX_DDL.replace("CREATE INDEX", "CREATE INDEX IF NOT EXISTS", 1)
        )
        error = image_categories_schema_error(
            conn, allow_missing=False, check_values=False
        )
        if error:
            raise sqlite3.DatabaseError(error)
        conn.execute("RELEASE image_categories_schema")
    except BaseException:
        conn.execute("ROLLBACK TO image_categories_schema")
        conn.execute("RELEASE image_categories_schema")
        raise


def copy_image_category(conn: sqlite3.Connection, removed_key, kept_key) -> int:
    """Give the kept copy the removed copy's category when it has none.

    Runs inside the caller's transaction; never deletes the removed copy's
    row, so a give-back or a failed delete loses nothing. Returns 1 when a
    row was inserted, else 0.
    """
    if tuple(removed_key) == tuple(kept_key):
        return 0
    return max(conn.execute(COPY_SQL, (*kept_key, *removed_key)).rowcount, 0)


class ImageCategoryOperations:
    """PromptDatabase mixin: set, clear and read images' own categories.

    Never reads or writes ``prompts``, ``prompt_pairs`` or
    ``generated_images``.
    """

    def image_category(self, root_key: str, relative_key: str) -> str | None:
        """One file's own category, or None."""
        row = (
            self.model.get_connection()
            .execute(CATEGORY_OF_SQL, (root_key, relative_key))
            .fetchone()
        )
        return row[0] if row else None

    def own_categories_of(self, root_keys: Iterable[str]) -> dict:
        """``{(root key, relative key): category}`` under the given sources.

        One primary-key range read per source.
        """
        found = {}
        conn = self.model.get_connection()
        for root_key in dict.fromkeys(root_keys):
            for relative, category in conn.execute(CATEGORIES_OF_ROOT_SQL, (root_key,)):
                found[(root_key, relative)] = category
        return found

    def set_image_categories(self, file_keys, category: str) -> dict:
        """Set *category* on every file key, in one transaction.

        ``file_keys``: ``(root key, relative key)`` pairs from any number of
        sources. Returns ``{updated, unchanged}``; a file that already had
        exactly this value is ``unchanged`` and keeps its ``updated_at``.
        """
        from .models import utc_now_iso  # models imports this module's schema

        keys = list(dict.fromkeys(tuple(key) for key in file_keys))
        counts = {"updated": 0, "unchanged": 0}
        now = utc_now_iso()
        with self._pair_transaction() as conn:
            for key in keys:
                row = conn.execute(CATEGORY_OF_SQL, key).fetchone()
                if row is not None and row[0] == category:
                    counts["unchanged"] += 1
                    continue
                conn.execute(UPSERT_SQL, (*key, category, now))
                counts["updated"] += 1
        return counts

    def clear_image_categories(self, file_keys) -> int:
        """Remove the own category of every file key (one transaction).

        Returns the number of rows removed.
        """
        keys = list(dict.fromkeys(tuple(key) for key in file_keys))
        removed = 0
        with self._pair_transaction() as conn:
            for key in keys:
                removed += conn.execute(DELETE_SQL, key).rowcount
        return removed
