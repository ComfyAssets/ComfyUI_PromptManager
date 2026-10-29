"""Library parent archive (#443, D152): prompts hidden from the shelf, restorable.

A row means the Library parent is archived; no row means it is on the shelf.
Nothing else about the prompt changes: its text, metadata, tags, run count,
image links, pairs and recorded runs stay. Restore deletes the row. Deleting
the prompt (any path) drops the row by cascade.

A table of its own, not a column: ``prompt_library_state`` is validated by its
exact definition, so a new column there would make older builds refuse the
upgraded database, while older builds ignore an unknown table (they show the
archived prompts again and keep the rows). Optional in old backups, required
once a database is current.
"""

import re
import sqlite3

TABLE = "prompt_archive"
MAX_STAMP_BYTES = 64
# What ``utc_now_iso`` writes starts with a date; anything else was not written here.
_STAMP_GLOB = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T*"
_DDL = (
    f"CREATE TABLE {TABLE} (prompt_id INTEGER PRIMARY KEY, "
    "archived_at TEXT NOT NULL, "
    "FOREIGN KEY(prompt_id) REFERENCES prompts(id) ON DELETE CASCADE)"
)
_COLUMNS = (("prompt_id", "INTEGER", 0, 1), ("archived_at", "TEXT", 1, 0))
_FOREIGN_KEYS = {("prompt_id", "prompts", "id", "CASCADE")}
# A predicate on ``p`` (the prompts alias): the parent is not archived.
SHELVED = f"NOT EXISTS(SELECT 1 FROM {TABLE} pa WHERE pa.prompt_id=p.id)"
ARCHIVED = f"EXISTS(SELECT 1 FROM {TABLE} pa WHERE pa.prompt_id=p.id)"


def _compact(sql: str) -> str:
    return re.sub(r"\s+", "", sql or "").lower()


def _structure_error(conn: sqlite3.Connection, allow_missing: bool) -> str | None:
    objects = conn.execute(
        "SELECT type,sql FROM sqlite_master WHERE name=? "
        "OR (type IN ('index','trigger') AND tbl_name=?)",
        (TABLE, TABLE),
    ).fetchall()
    if not objects:
        return None if allow_missing else "Missing prompt archive schema"
    tables = [sql for kind, sql in objects if kind == "table"]
    if len(tables) != 1 or len(objects) != 1:
        return "Prompt archive name is reserved by an unsupported schema object"
    if _compact(tables[0]) != _compact(_DDL):
        return "Prompt archive has an invalid table definition"
    columns = tuple(
        (row[1], row[2].upper(), row[3], row[5])
        for row in conn.execute(f"PRAGMA table_info({TABLE})")
    )
    if columns != _COLUMNS:
        return "Prompt archive has invalid columns"
    foreign_keys = {
        (row[3], row[2], row[4], row[6])
        for row in conn.execute(f"PRAGMA foreign_key_list({TABLE})")
    }
    if foreign_keys != _FOREIGN_KEYS:
        return "Prompt archive has invalid foreign key definitions"
    return None


def prompt_archive_schema_error(
    conn: sqlite3.Connection, *, allow_missing: bool = True
) -> str | None:
    """Reject an unsupported archive schema or values; reads only.

    A missing table is acceptable for backups that predate it unless
    ``allow_missing`` is false.
    """
    error = _structure_error(conn, allow_missing)
    if error is not None:
        return error
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
    ).fetchone():
        return None
    if conn.execute(f"PRAGMA foreign_key_check({TABLE})").fetchone():
        return "Prompt archive contains foreign key violations"
    if conn.execute(
        f"SELECT 1 FROM {TABLE} WHERE typeof(archived_at)!='text' "  # nosec B608
        f"OR length(CAST(archived_at AS BLOB))>{MAX_STAMP_BYTES} "
        f"OR archived_at NOT GLOB '{_STAMP_GLOB}' LIMIT 1"
    ).fetchone():
        return "Prompt archive contains invalid values"
    return None


def migrate_prompt_archive(conn: sqlite3.Connection) -> None:
    """Install the table atomically; validate before and after (no backfill)."""
    conn.execute("SAVEPOINT prompt_archive_schema")
    try:
        error = _structure_error(conn, True)
        if error:
            raise sqlite3.DatabaseError(error)
        conn.execute(_DDL.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1))
        error = _structure_error(conn, False)
        if error:
            raise sqlite3.DatabaseError(error)
        conn.execute("RELEASE prompt_archive_schema")
    except BaseException:
        conn.execute("ROLLBACK TO prompt_archive_schema")
        conn.execute("RELEASE prompt_archive_schema")
        raise
