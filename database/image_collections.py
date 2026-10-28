"""Collections (#331): named, hand-picked groups of image files.

A member names its file exactly as a favorite does (``image_favorites.py``):
the source folder's real, case-normalized path (``root_key``) and the path
below it (``relative_path``), both server-side only. A member whose file is
moved, renamed or deleted outside PromptManager is kept and simply not listed;
nothing follows a file to a new place.

Names are trimmed with each run of whitespace collapsed to one space, 1 to 100
characters without control characters; ``name_key`` (NFC of the case-folded
NFC name) is UNIQUE, so names are unique ignoring case and Unicode form.

The two tables install like ``image_favorites`` and ``saved_views``: optional
in old backups, required once a database is current, created empty with no
backfill. Startup checks only the structure, so a value saved under older
rules never stops the library from opening; restore checks the values too.
"""

import re
import sqlite3
import unicodedata
from typing import Optional

from .image_favorites import MAX_KEY_LENGTH

COLLECTIONS_TABLE = "image_collections"
ITEMS_TABLE = "image_collection_items"
FILE_INDEX = "idx_image_collection_items_file"
TABLES = (COLLECTIONS_TABLE, ITEMS_TABLE)
MAX_NAME_LENGTH = 100
_TIMESTAMP_BYTES = 64
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")

_TABLE_DDL = {
    COLLECTIONS_TABLE: (
        f"CREATE TABLE {COLLECTIONS_TABLE} (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE, "
        "created_at TEXT NOT NULL)"
    ),
    ITEMS_TABLE: (
        f"CREATE TABLE {ITEMS_TABLE} (collection_id INTEGER NOT NULL REFERENCES "
        f"{COLLECTIONS_TABLE}(id) ON DELETE CASCADE, root_key TEXT NOT NULL, "
        "relative_path TEXT NOT NULL, added_at TEXT NOT NULL, "
        "PRIMARY KEY (collection_id, root_key, relative_path))"
    ),
}
_INDEX_DDL = f"CREATE INDEX {FILE_INDEX} ON {ITEMS_TABLE}(root_key, relative_path)"
# (name, declared type, not null, default, primary key position)
_COLUMNS = {
    COLLECTIONS_TABLE: (
        ("id", "INTEGER", 0, None, 1),
        ("name", "TEXT", 1, None, 0),
        ("name_key", "TEXT", 1, None, 0),
        ("created_at", "TEXT", 1, None, 0),
    ),
    ITEMS_TABLE: (
        ("collection_id", "INTEGER", 1, None, 1),
        ("root_key", "TEXT", 1, None, 2),
        ("relative_path", "TEXT", 1, None, 3),
        ("added_at", "TEXT", 1, None, 0),
    ),
}
# (column, parent table, parent column, on delete)
_FOREIGN_KEYS = {
    COLLECTIONS_TABLE: set(),
    ITEMS_TABLE: {("collection_id", COLLECTIONS_TABLE, "id", "CASCADE")},
}


class CollectionInvalid(ValueError):
    """A name, id or target breaks the write rules (a 400)."""


def normalize_collection_name(name: object) -> str:
    """The stored spelling of *name*: trimmed, whitespace collapsed, checked.

    Raises ``CollectionInvalid`` unless the result is 1 to 100 characters
    without control characters.
    """
    if not isinstance(name, str):
        raise CollectionInvalid("Name must be a string")
    collapsed = _WHITESPACE.sub(" ", name).strip()
    if not 1 <= len(collapsed) <= MAX_NAME_LENGTH:
        raise CollectionInvalid(f"Name must be 1 to {MAX_NAME_LENGTH} characters")
    if _CONTROL.search(collapsed):
        raise CollectionInvalid("Name contains control characters")
    return collapsed


def collection_name_key(name: str) -> str:
    """The uniqueness key of a name: NFC of the case-folded NFC spelling."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", name).casefold())


def _compact(sql: str) -> str:
    return re.sub(r"\s+", "", sql).lower()


def _expected_catalog() -> set:
    objects = {("table", name, _compact(sql)) for name, sql in _TABLE_DDL.items()}
    objects.add(("index", FILE_INDEX, _compact(_INDEX_DDL)))
    objects |= {("index", f"sqlite_autoindex_{table}_1", "") for table in TABLES}
    return objects


def _catalog(conn: sqlite3.Connection) -> list:
    # sqlite_sequence (AUTOINCREMENT) is shared with other tables and ignored.
    return conn.execute(
        "SELECT type,name,sql FROM sqlite_master "
        "WHERE name IN (?,?,?) OR tbl_name IN (?,?)",
        (*TABLES, FILE_INDEX, *TABLES),
    ).fetchall()


def _structure_error(conn: sqlite3.Connection) -> Optional[str]:
    for table, expected in _COLUMNS.items():
        columns = tuple(
            (row[1], row[2].upper(), row[3], row[4], row[5])
            for row in conn.execute(f"PRAGMA table_info({table})")
        )
        if columns != expected:
            return f"Image collections table {table} has invalid columns"
        foreign_keys = {
            (row[3], row[2], row[4], row[6])
            for row in conn.execute(f"PRAGMA foreign_key_list({table})")
        }
        if foreign_keys != _FOREIGN_KEYS[table]:
            return f"Image collections table {table} has invalid foreign keys"
    return None


def _decoded_too_long(conn, sql: str, limit: int) -> bool:
    """Whether a byte-bounded candidate row decodes to over *limit* characters."""
    for row in conn.execute(sql):
        for raw in row:
            if len(bytes(raw).decode("utf-8", "replace")) > limit:
                return True
    return False


def _value_error(conn: sqlite3.Connection) -> Optional[str]:
    # Only this module's constants are interpolated into the SQL below. Bytes
    # bound characters from above (UTF-8 is at most 4 bytes each); the few
    # candidates over the character bound are measured decoded in Python.
    if conn.execute(
        f"SELECT 1 FROM {COLLECTIONS_TABLE} WHERE "  # nosec B608
        "typeof(id) != 'integer' OR typeof(name) != 'text' "
        "OR typeof(name_key) != 'text' OR typeof(created_at) != 'text' "
        f"OR length(CAST(name AS BLOB)) > {MAX_NAME_LENGTH * 4} "
        f"OR length(CAST(created_at AS BLOB)) > {_TIMESTAMP_BYTES} LIMIT 1"
    ).fetchone():
        return "Image collections contains invalid values"
    for name, name_key in conn.execute(
        f"SELECT name, name_key FROM {COLLECTIONS_TABLE}"  # nosec B608
    ):
        if (
            not 1 <= len(name) <= MAX_NAME_LENGTH
            or _CONTROL.search(name)
            or name != name.strip()
            or name_key != collection_name_key(name)
        ):
            return "Image collections contains invalid names"
    limit = MAX_KEY_LENGTH * 4
    if conn.execute(
        f"SELECT 1 FROM {ITEMS_TABLE} WHERE "  # nosec B608
        "typeof(collection_id) != 'integer' OR typeof(root_key) != 'text' "
        "OR typeof(relative_path) != 'text' OR typeof(added_at) != 'text' "
        f"OR length(CAST(root_key AS BLOB)) > {limit} "
        f"OR length(CAST(relative_path AS BLOB)) > {limit} "
        f"OR length(CAST(added_at AS BLOB)) > {_TIMESTAMP_BYTES} LIMIT 1"
    ).fetchone() or _decoded_too_long(
        conn,
        f"SELECT CAST(root_key AS BLOB), CAST(relative_path AS BLOB) "  # nosec B608
        f"FROM {ITEMS_TABLE} WHERE length(CAST(root_key AS BLOB)) > {MAX_KEY_LENGTH} "
        f"OR length(CAST(relative_path AS BLOB)) > {MAX_KEY_LENGTH}",
        MAX_KEY_LENGTH,
    ):
        return "Image collections contains invalid members"
    if conn.execute(f"PRAGMA foreign_key_check({ITEMS_TABLE})").fetchone():
        return "Image collections contains members of a missing collection"
    return None


def image_collections_schema_error(
    conn: sqlite3.Connection,
    *,
    allow_missing: bool = True,
    check_values: bool = True,
) -> Optional[str]:
    """Reject partial or unsupported collections schemas and invalid rows.

    Reads only: nothing is ever repaired. A database without either table is
    acceptable for backups that predate them unless ``allow_missing`` is
    false. ``check_values=False`` (live startup) checks the structure only.
    """
    objects = _catalog(conn)
    if not objects:
        return None if allow_missing else "Missing image collections schema"
    present = {name for kind, name, _sql in objects if kind == "table"}
    if present != set(TABLES):
        return "Incomplete image collections schema"
    actual = {(kind, name, _compact(sql or "")) for kind, name, sql in objects}
    if actual != _expected_catalog():
        return "Image collections has an invalid schema definition"
    error = _structure_error(conn)
    if error or not check_values:
        return error
    return _value_error(conn)


def migrate_image_collections(conn: sqlite3.Connection) -> None:
    """Install both tables atomically; validate before and after creating them."""
    conn.execute("SAVEPOINT image_collections_schema")
    try:
        error = image_collections_schema_error(conn, check_values=False)
        if error:
            raise sqlite3.DatabaseError(error)
        for sql in _TABLE_DDL.values():
            conn.execute(sql.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1))
        conn.execute(_INDEX_DDL.replace("INDEX", "INDEX IF NOT EXISTS", 1))
        error = image_collections_schema_error(
            conn, allow_missing=False, check_values=False
        )
        if error:
            raise sqlite3.DatabaseError(error)
        conn.execute("RELEASE image_collections_schema")
    except BaseException:
        conn.execute("ROLLBACK TO image_collections_schema")
        conn.execute("RELEASE image_collections_schema")
        raise
