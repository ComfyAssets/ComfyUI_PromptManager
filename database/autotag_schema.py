"""Auto-tagged marks (#379): which image bytes a tagging model has handled.

One table, ``media_image_autotags``: one row per byte record of the image
annotation group (``media_annotation_bytes``) means "a tagging model looked at
these exact bytes and its result was stored", or that the bytes were found to
be untaggable. "Skip already tagged" skips an image whose current byte record
is marked; hand tags never mark. Rows are written only by the image tag store
(``database/annotations.py``), in the transaction of the tag write they
record.

The group installs like ``image_favorites``: optional in old backups,
required once a database is current, and never backfilled. Like the Needs
review group it is valid only beside a valid image annotation group, because
every mark cites one byte record (``ON DELETE CASCADE``). Validation reads
only: the catalog compared after whitespace compaction, the columns and the
foreign key through the PRAGMAs, then one bounded value pass and the foreign
key check.
"""

import re
import sqlite3
from typing import Optional

from .annotation_schema import BYTES_TABLE
from .annotation_schema import TABLES as ANNOTATION_TABLES
from .annotation_schema import _catalog as _annotation_catalog
from .annotation_schema import _uuid, annotation_schema_error

AUTOTAG_TABLE = "media_image_autotags"
_DDL = (
    f"CREATE TABLE {AUTOTAG_TABLE} (byte_id TEXT PRIMARY KEY "
    f"REFERENCES {BYTES_TABLE}(id) ON DELETE CASCADE) WITHOUT ROWID"
)
# (cid, name, declared type, not null, default, primary key position): the
# key of a WITHOUT ROWID table is implicitly NOT NULL.
_COLUMNS = [(0, "byte_id", "TEXT", 1, None, 1)]
# (column, parent table, parent column, on delete)
_FOREIGN_KEYS = {("byte_id", BYTES_TABLE, "id", "CASCADE")}

NEEDS_ANNOTATIONS = "Image auto-tag marks need the image annotation tables"
INVALID_SCHEMA = "Image auto-tag marks have an invalid schema"
INVALID_VALUES = "Image auto-tag marks contain invalid values"
MISSING = "Missing image auto-tag marks schema"


def _compact(sql: str) -> str:
    return re.sub(r"\s+", "", sql).lower()


def _catalog(conn: sqlite3.Connection) -> list:
    return conn.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE name=? OR tbl_name=?",
        (AUTOTAG_TABLE, AUTOTAG_TABLE),
    ).fetchall()


def autotag_without_annotations_error(conn: sqlite3.Connection) -> Optional[str]:
    """Catalog only: the marks table present without its annotation tables.

    Startup asks this before the annotation group installs, which would make
    a marks table copied without it look valid. It reads no rows.
    """
    if not _catalog(conn):
        return None
    present = {
        name for kind, name, _sql in _annotation_catalog(conn) if kind == "table"
    }
    if present != set(ANNOTATION_TABLES):
        return NEEDS_ANNOTATIONS
    return None


def autotag_schema_error(
    conn: sqlite3.Connection,
    *,
    allow_missing: bool = True,
    annotations_valid: bool = False,
) -> Optional[str]:
    """Reject an unsupported marks table or invalid marks; reads only.

    A database without the table is acceptable for backups that predate it
    unless ``allow_missing`` is false. A present table requires a present,
    valid image annotation group; ``annotations_valid`` says the caller has
    just validated it (startup's annotation install), so only its presence is
    checked again.
    """
    objects = _catalog(conn)
    if not objects:
        return None if allow_missing else MISSING
    if annotations_valid:
        if autotag_without_annotations_error(conn):
            return NEEDS_ANNOTATIONS
    elif annotation_schema_error(conn, allow_missing=False):
        return NEEDS_ANNOTATIONS
    actual = {(kind, name, _compact(sql or "")) for kind, name, sql in objects}
    if actual != {("table", AUTOTAG_TABLE, _compact(_DDL))}:
        return INVALID_SCHEMA
    columns = [
        (row[0], row[1], row[2].upper(), row[3], row[4], row[5])
        for row in conn.execute(f"PRAGMA table_info({AUTOTAG_TABLE})")
    ]
    if columns != _COLUMNS:
        return INVALID_SCHEMA
    foreign_keys = {
        (row[3], row[2], row[4], row[6])
        for row in conn.execute(f"PRAGMA foreign_key_list({AUTOTAG_TABLE})")
    }
    if foreign_keys != _FOREIGN_KEYS:
        return INVALID_SCHEMA
    # Only this module's constants are interpolated into the SQL below.
    if conn.execute(
        f"SELECT 1 FROM {AUTOTAG_TABLE} "  # nosec B608
        f"WHERE NOT ({_uuid('byte_id')}) LIMIT 1"
    ).fetchone():
        return INVALID_VALUES
    if conn.execute(f"PRAGMA foreign_key_check({AUTOTAG_TABLE})").fetchone():
        return INVALID_VALUES
    return None


def migrate_autotag_state(conn: sqlite3.Connection) -> None:
    """Install the table atomically; validate before and after creating it.

    Runs after ``migrate_annotation_state`` (which has just validated the
    annotation group). Creates one empty table: no row is read or copied.
    """
    conn.execute("SAVEPOINT media_autotag_schema")
    try:
        error = autotag_schema_error(conn, annotations_valid=True)
        if error:
            raise sqlite3.DatabaseError(error)
        conn.execute(_DDL.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1))
        error = autotag_schema_error(conn, allow_missing=False, annotations_valid=True)
        if error:
            raise sqlite3.DatabaseError(error)
        conn.execute("RELEASE media_autotag_schema")
    except BaseException:
        conn.execute("ROLLBACK TO media_autotag_schema")
        conn.execute("RELEASE media_autotag_schema")
        raise
