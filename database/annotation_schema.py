"""Image annotation schema group (#249): file locators, byte records, image tags.

The group installs like ``image_favorites`` and ``saved_views``: optional in old
backups, required once a database is current. It owns

- ``media_annotation_metadata``: one row, the group's schema version and the
  image index revision;
- ``media_annotation_files``: one private canonical locator per physical file
  (``normcase(join(realpath(root), relative))``, server-side only);
- ``media_annotation_bytes``: one record per observed whole-file SHA-256 of a
  file, with the size and ``mtime_ns`` verified when those bytes were last seen
  and the tag set's revision;
- ``media_image_tags``: the ordered image tag set of one byte record, joined
  into the global ``tags`` registry so vocabulary maintenance carries image
  links like prompt and pair links.

Installation never backfills anything from prompt or pair tags and never
touches the filesystem. Validation reads only: the catalog objects are compared
after whitespace compaction, columns, foreign keys and indexes through the
PRAGMAs, and persisted values in single bounded SQL passes.
"""

import re
import sqlite3
from typing import Optional

from .image_favorites import MAX_KEY_LENGTH

SCHEMA_VERSION = 1
METADATA_TABLE = "media_annotation_metadata"
FILES_TABLE = "media_annotation_files"
BYTES_TABLE = "media_annotation_bytes"
IMAGE_TAGS_TABLE = "media_image_tags"
TABLES = (METADATA_TABLE, FILES_TABLE, BYTES_TABLE, IMAGE_TAGS_TABLE)
MAX_LOCATOR_LENGTH = MAX_KEY_LENGTH
_TIMESTAMP_BYTES = 64

_TABLE_DDL = {
    METADATA_TABLE: (
        f"CREATE TABLE {METADATA_TABLE} (singleton_id INTEGER PRIMARY KEY "
        "CHECK(singleton_id=1), schema_version INTEGER NOT NULL "
        f"CHECK(schema_version={SCHEMA_VERSION}), "
        "image_index_revision INTEGER NOT NULL DEFAULT 0 "
        "CHECK(image_index_revision>=0))"
    ),
    FILES_TABLE: (
        f"CREATE TABLE {FILES_TABLE} (id TEXT PRIMARY KEY CHECK(length(id)=36), "
        "locator TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL)"
    ),
    BYTES_TABLE: (
        f"CREATE TABLE {BYTES_TABLE} (id TEXT PRIMARY KEY CHECK(length(id)=36), "
        f"file_id TEXT NOT NULL REFERENCES {FILES_TABLE}(id) ON DELETE CASCADE, "
        "sha256 TEXT NOT NULL CHECK(length(sha256)=64 AND sha256 NOT GLOB "
        "'*[^0-9a-f]*'), size INTEGER NOT NULL CHECK(size>=0), "
        "mtime_ns INTEGER NOT NULL, verified_at TEXT NOT NULL, "
        "first_seen_at TEXT NOT NULL, tag_revision INTEGER NOT NULL DEFAULT 0 "
        "CHECK(tag_revision>=0), UNIQUE(file_id, sha256))"
    ),
    IMAGE_TAGS_TABLE: (
        f"CREATE TABLE {IMAGE_TAGS_TABLE} (byte_id TEXT NOT NULL REFERENCES "
        f"{BYTES_TABLE}(id) ON DELETE CASCADE, tag_id INTEGER NOT NULL "
        "REFERENCES tags(id) ON DELETE CASCADE, position INTEGER NOT NULL "
        "CHECK(position>=0), PRIMARY KEY(byte_id, tag_id))"
    ),
}
_INDEX_DDL = {
    "idx_media_annotation_bytes_fingerprint": (
        "CREATE INDEX idx_media_annotation_bytes_fingerprint ON "
        f"{BYTES_TABLE}(file_id, size, mtime_ns)"
    ),
    "idx_media_image_tags_tag": (
        f"CREATE INDEX idx_media_image_tags_tag ON {IMAGE_TAGS_TABLE}(tag_id)"
    ),
    "idx_media_image_tags_position": (
        "CREATE UNIQUE INDEX idx_media_image_tags_position ON "
        f"{IMAGE_TAGS_TABLE}(byte_id, position)"
    ),
}
# SQLite names the indexes behind TEXT primary keys and UNIQUE constraints
# ``sqlite_autoindex_<table>_<n>``; their catalog rows carry no SQL.
_AUTOINDEXES = {FILES_TABLE: 2, BYTES_TABLE: 2, IMAGE_TAGS_TABLE: 1}
# (name, declared type, not null, default, primary key position)
_COLUMNS = {
    METADATA_TABLE: (
        ("singleton_id", "INTEGER", 0, None, 1),
        ("schema_version", "INTEGER", 1, None, 0),
        ("image_index_revision", "INTEGER", 1, "0", 0),
    ),
    FILES_TABLE: (
        ("id", "TEXT", 0, None, 1),
        ("locator", "TEXT", 1, None, 0),
        ("created_at", "TEXT", 1, None, 0),
    ),
    BYTES_TABLE: (
        ("id", "TEXT", 0, None, 1),
        ("file_id", "TEXT", 1, None, 0),
        ("sha256", "TEXT", 1, None, 0),
        ("size", "INTEGER", 1, None, 0),
        ("mtime_ns", "INTEGER", 1, None, 0),
        ("verified_at", "TEXT", 1, None, 0),
        ("first_seen_at", "TEXT", 1, None, 0),
        ("tag_revision", "INTEGER", 1, "0", 0),
    ),
    IMAGE_TAGS_TABLE: (
        ("byte_id", "TEXT", 1, None, 1),
        ("tag_id", "INTEGER", 1, None, 2),
        ("position", "INTEGER", 1, None, 0),
    ),
}
# (column, parent table, parent column, on delete)
_FOREIGN_KEYS = {
    METADATA_TABLE: set(),
    FILES_TABLE: set(),
    BYTES_TABLE: {("file_id", FILES_TABLE, "id", "CASCADE")},
    IMAGE_TAGS_TABLE: {
        ("byte_id", BYTES_TABLE, "id", "CASCADE"),
        ("tag_id", "tags", "id", "CASCADE"),
    },
}


def _compact(sql: str) -> str:
    return re.sub(r"\s+", "", sql).lower()


def _uuid(column: str) -> str:
    """SQL: *column* is a lowercase hyphenated 36-character UUID string."""
    return (
        f"typeof({column}) = 'text' AND length({column}) = 36 "
        f"AND substr({column}, 9, 1) = '-' AND substr({column}, 14, 1) = '-' "
        f"AND substr({column}, 19, 1) = '-' AND substr({column}, 24, 1) = '-' "
        f"AND length(replace({column}, '-', '')) = 32 "
        f"AND replace({column}, '-', '') NOT GLOB '*[^0-9a-f]*'"
    )


def _timestamp(column: str) -> str:
    return (
        f"typeof({column}) = 'text' "
        f"AND length(CAST({column} AS BLOB)) <= {_TIMESTAMP_BYTES}"
    )


def _non_negative(column: str) -> str:
    return f"typeof({column}) = 'integer' AND {column} >= 0"


# Every row of a table must satisfy its predicate; one pass each, LIMIT 1.
_ROW_PREDICATES = {
    FILES_TABLE: " AND ".join(
        (
            _uuid("id"),
            "typeof(locator) = 'text'",
            # UTF-8 is at most 4 bytes per character; the decoded length of
            # the few candidates above the byte bound is checked in Python.
            f"length(CAST(locator AS BLOB)) <= {MAX_LOCATOR_LENGTH * 4}",
            _timestamp("created_at"),
        )
    ),
    BYTES_TABLE: " AND ".join(
        (
            _uuid("id"),
            _uuid("file_id"),
            "typeof(sha256) = 'text' AND length(sha256) = 64 "
            "AND sha256 NOT GLOB '*[^0-9a-f]*'",
            _non_negative("size"),
            "typeof(mtime_ns) = 'integer'",
            _timestamp("verified_at"),
            _timestamp("first_seen_at"),
            _non_negative("tag_revision"),
        )
    ),
    IMAGE_TAGS_TABLE: " AND ".join(
        (_uuid("byte_id"), "typeof(tag_id) = 'integer'", _non_negative("position"))
    ),
}


def _expected_catalog() -> set:
    objects = {("table", name, _compact(sql)) for name, sql in _TABLE_DDL.items()}
    objects |= {("index", name, _compact(sql)) for name, sql in _INDEX_DDL.items()}
    objects |= {
        ("index", f"sqlite_autoindex_{table}_{n}", "")
        for table, count in _AUTOINDEXES.items()
        for n in range(1, count + 1)
    }
    return objects


def _catalog(conn: sqlite3.Connection) -> list:
    names = TABLES + tuple(_INDEX_DDL)
    marks = ",".join("?" * len(names))
    table_marks = ",".join("?" * len(TABLES))
    return conn.execute(  # only "?" placeholders are interpolated
        "SELECT type,name,sql FROM sqlite_master "
        f"WHERE name IN ({marks}) OR tbl_name IN ({table_marks})",  # nosec B608
        names + TABLES,
    ).fetchall()


def _structure_error(conn: sqlite3.Connection) -> Optional[str]:
    for table, expected in _COLUMNS.items():
        columns = tuple(
            (row[1], row[2].upper(), row[3], row[4], row[5])
            for row in conn.execute(f"PRAGMA table_info({table})")
        )
        if columns != expected:
            return f"Image annotations table {table} has invalid columns"
        foreign_keys = {
            (row[3], row[2], row[4], row[6])
            for row in conn.execute(f"PRAGMA foreign_key_list({table})")
        }
        if foreign_keys != _FOREIGN_KEYS[table]:
            return f"Image annotations table {table} has invalid foreign keys"
        if conn.execute(f"PRAGMA foreign_key_check({table})").fetchone():
            return f"Image annotations table {table} contains foreign key violations"
    return None


def _value_error(conn: sqlite3.Connection) -> Optional[str]:
    # Only this module's constants are interpolated into the SQL below.
    rows = conn.execute(
        "SELECT COUNT(*), MIN(typeof(singleton_id) = 'integer' "  # nosec B608
        "AND singleton_id = 1 AND typeof(schema_version) = 'integer' "
        f"AND schema_version = {SCHEMA_VERSION} "
        f"AND {_non_negative('image_index_revision')}) FROM {METADATA_TABLE}"
    ).fetchone()
    if rows[0] != 1 or rows[1] != 1:
        return "Image annotations metadata is invalid"
    for table, predicate in _ROW_PREDICATES.items():
        if conn.execute(
            f"SELECT 1 FROM {table} WHERE NOT ({predicate}) LIMIT 1"  # nosec B608
        ).fetchone():
            return f"Image annotations table {table} contains invalid values"
    # length() stops at an embedded NUL, so the SQL pass bounds bytes and the
    # few candidates over the character bound are measured decoded here.
    for (raw,) in conn.execute(
        f"SELECT CAST(locator AS BLOB) FROM {FILES_TABLE} "  # nosec B608
        f"WHERE length(CAST(locator AS BLOB)) > {MAX_LOCATOR_LENGTH}"
    ):
        if len(bytes(raw).decode("utf-8", "replace")) > MAX_LOCATOR_LENGTH:
            return f"Image annotations table {FILES_TABLE} contains invalid values"
    return None


def annotation_schema_error(
    conn: sqlite3.Connection, *, allow_missing: bool = True
) -> Optional[str]:
    """Reject partial or unsupported annotation schemas and invalid rows.

    Reads only: nothing is ever repaired. A database without any of the
    group's objects is acceptable for backups that predate it unless
    ``allow_missing`` is false.
    """
    objects = _catalog(conn)
    if not objects:
        return None if allow_missing else "Missing image annotation schema"
    present = {name for kind, name, _sql in objects if kind == "table"}
    if present != set(TABLES):
        return "Incomplete image annotation schema"
    actual = {(kind, name, _compact(sql or "")) for kind, name, sql in objects}
    if actual != _expected_catalog():
        return "Image annotations has an invalid schema definition"
    return _structure_error(conn) or _value_error(conn)


def migrate_annotation_state(conn: sqlite3.Connection) -> None:
    """Install the group atomically; validate before and after creating it."""
    conn.execute("SAVEPOINT media_annotation_schema")
    try:
        error = annotation_schema_error(conn)
        if error:
            raise sqlite3.DatabaseError(error)
        for sql in _TABLE_DDL.values():
            conn.execute(sql.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1))
        for sql in _INDEX_DDL.values():
            conn.execute(sql.replace("INDEX", "INDEX IF NOT EXISTS", 1))
        conn.execute(
            f"INSERT OR IGNORE INTO {METADATA_TABLE} "
            "(singleton_id, schema_version, image_index_revision) VALUES (1, ?, 0)",
            (SCHEMA_VERSION,),
        )
        error = annotation_schema_error(conn, allow_missing=False)
        if error:
            raise sqlite3.DatabaseError(error)
        conn.execute("RELEASE media_annotation_schema")
    except BaseException:
        conn.execute("ROLLBACK TO media_annotation_schema")
        conn.execute("RELEASE media_annotation_schema")
        raise
