"""Removal marks (#480): files removed from the library that stay on disk.

``image_removals`` holds one row per hidden file: its private canonical
locator (the ``media_annotation_files.locator`` form) and the ``size`` and
``mtime_ns`` it had when it was removed. A file at that locator with other
bytes is a new file and shows again. ``image_removals_meta`` is one row, the
marks revision, bumped in the same transaction as every mark change so the
workspace listing caches can tell marks changed.

The group installs like ``image_favorites``: optional in old backups,
required once a database is current. Older builds ignore it.
"""

import os
import re
import sqlite3

from .annotation_schema import MAX_LOCATOR_LENGTH

TABLE = "image_removals"
META_TABLE = "image_removals_meta"
TABLES = (TABLE, META_TABLE)
_TABLE_DDL = {
    TABLE: (
        f"CREATE TABLE {TABLE} (locator TEXT PRIMARY KEY, "
        "size INTEGER NOT NULL CHECK(size>=0), mtime_ns INTEGER NOT NULL, "
        "removed_at TEXT NOT NULL) WITHOUT ROWID"
    ),
    META_TABLE: (
        f"CREATE TABLE {META_TABLE} (singleton_id INTEGER PRIMARY KEY "
        "CHECK(singleton_id=1), revision INTEGER NOT NULL DEFAULT 0 "
        "CHECK(revision>=0))"
    ),
}
_SEED_META = (
    f"INSERT OR IGNORE INTO {META_TABLE} (singleton_id, revision) VALUES (1, 0)"
)
_COLUMNS = {
    TABLE: (
        ("locator", "TEXT", 1, 1),
        ("size", "INTEGER", 1, 0),
        ("mtime_ns", "INTEGER", 1, 0),
        ("removed_at", "TEXT", 1, 0),
    ),
    META_TABLE: (
        ("singleton_id", "INTEGER", 0, 1),
        ("revision", "INTEGER", 1, 0),
    ),
}
_AUTOINDEX = f"sqlite_autoindex_{TABLE}_1"


def _compact(sql: str) -> str:
    return re.sub(r"\s+", "", sql or "").lower()


def _structure_error(conn: sqlite3.Connection, allow_missing: bool) -> str | None:
    rows = conn.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name IN (?,?) "
        "OR tbl_name IN (?,?)",
        TABLES + TABLES,
    ).fetchall()
    if not rows:
        return None if allow_missing else "Missing image removal marks schema"
    tables = {name: sql for kind, name, _tbl, sql in rows if kind == "table"}
    others = {(kind, name) for kind, name, _tbl, _sql in rows if kind != "table"}
    if set(tables) != set(TABLES) or others - {("index", _AUTOINDEX)}:
        return "Image removal marks name is reserved by an unsupported schema object"
    for table in TABLES:
        if _compact(tables[table]) != _compact(_TABLE_DDL[table]):
            return "Image removal marks have an invalid table definition"
        columns = tuple(
            (row[1], row[2].upper(), row[3], row[5])
            for row in conn.execute(f"PRAGMA table_info({table})")
        )
        if columns != _COLUMNS[table]:
            return "Image removal marks have invalid columns"
    return None


def _values_error(conn: sqlite3.Connection) -> str | None:
    limit = MAX_LOCATOR_LENGTH * 4
    if conn.execute(
        f"SELECT 1 FROM {TABLE} WHERE typeof(locator) != 'text' "  # nosec B608
        f"OR length(CAST(locator AS BLOB)) > {limit} OR locator = '' "
        "OR typeof(size) != 'integer' OR typeof(mtime_ns) != 'integer' "
        "OR typeof(removed_at) != 'text' OR length(CAST(removed_at AS BLOB)) > 64 "
        "LIMIT 1"
    ).fetchone():
        return "Image removal marks contain invalid values"
    meta = conn.execute(
        f"SELECT singleton_id, typeof(revision) FROM {META_TABLE}"  # nosec B608
    ).fetchall()
    if meta != [(1, "integer")]:
        return "Image removal marks have an invalid revision"
    return None


def image_removals_schema_error(
    conn: sqlite3.Connection, *, allow_missing: bool = True
) -> str | None:
    """Reject unsupported schemas and invalid persisted values (reads only)."""
    error = _structure_error(conn, allow_missing)
    if error:
        return error
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
    ).fetchone()
    return _values_error(conn) if exists else None


def migrate_image_removals(conn: sqlite3.Connection) -> None:
    """Install both tables atomically; validate before and after."""
    conn.execute("SAVEPOINT image_removals_schema")
    try:
        error = image_removals_schema_error(conn)
        if error:
            raise sqlite3.DatabaseError(error)
        for sql in _TABLE_DDL.values():
            conn.execute(sql.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1))
        conn.execute(_SEED_META)
        error = image_removals_schema_error(conn, allow_missing=False)
        if error:
            raise sqlite3.DatabaseError(error)
        conn.execute("RELEASE image_removals_schema")
    except BaseException:
        conn.execute("ROLLBACK TO image_removals_schema")
        conn.execute("RELEASE image_removals_schema")
        raise


_UNMARK_SQL = f"DELETE FROM {TABLE} WHERE locator=?"  # nosec B608
_MARK_SQL = (
    f"INSERT OR REPLACE INTO {TABLE} "  # nosec B608
    "(locator, size, mtime_ns, removed_at) VALUES (?, ?, ?, ?)"
)
_MARK_OF_SQL = f"SELECT size, mtime_ns FROM {TABLE} WHERE locator=?"  # nosec B608
BUMP_MARKS_SQL = (
    f"UPDATE {META_TABLE} SET revision=revision+1 WHERE singleton_id=1"  # nosec B608
)


def has_marks_table(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
        ).fetchone()
        is not None
    )


def locator_of_key(root_key: str, relative_key: str) -> str:
    """The locator of a favorite-style file key (root key + relative key)."""
    return os.path.normcase(os.path.join(root_key, *relative_key.split("/")))


def mark_of(conn: sqlite3.Connection, locator: str):
    """``(size, mtime_ns)`` of the file's removal mark, or None."""
    if not has_marks_table(conn):
        return None
    row = conn.execute(_MARK_OF_SQL, (locator,)).fetchone()
    return (row[0], row[1]) if row else None


def write_mark(
    conn: sqlite3.Connection, locator: str, size: int, mtime_ns: int, now: str
):
    conn.execute(_MARK_SQL, (locator, int(size), int(mtime_ns), now))
    conn.execute(BUMP_MARKS_SQL)


def clear_marks(conn: sqlite3.Connection, locators) -> int:
    """Delete the marks of *locators* on *conn* (no commit); the count removed.

    Every record write for a file calls this (#480): a file with records is
    in the library, so it is never hidden.
    """
    locators = list(dict.fromkeys(locators))
    if not locators or not has_marks_table(conn):
        return 0
    removed = 0
    for locator in locators:
        removed += conn.execute(_UNMARK_SQL, (locator,)).rowcount
    if removed:
        conn.execute(BUMP_MARKS_SQL)
    return removed


def clear_marks_of_keys(conn: sqlite3.Connection, file_keys) -> int:
    """``clear_marks`` for favorite-style ``(root key, relative key)`` pairs."""
    return clear_marks(conn, [locator_of_key(*key) for key in file_keys])
