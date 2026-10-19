"""Optional-old, required-current schema for Library parent state."""

import re
import sqlite3

TABLE = "prompt_library_state"
MAX_TITLE_LENGTH = 10_000
_DDL = (
    f"CREATE TABLE {TABLE} (prompt_id INTEGER PRIMARY KEY, title TEXT, "
    "FOREIGN KEY(prompt_id) REFERENCES prompts(id) ON DELETE CASCADE)"
)
_COLUMNS = (("prompt_id", "INTEGER", 0, 1), ("title", "TEXT", 0, 0))
_FOREIGN_KEYS = {("prompt_id", "prompts", "id", "CASCADE")}


def _compact(sql: str) -> str:
    return re.sub(r"\s+", "", sql).lower()


def library_schema_error(
    conn: sqlite3.Connection, *, allow_missing: bool = True
) -> str | None:
    """Reject unsupported Library state schemas and invalid persisted values.

    Reads only: nothing is ever repaired. A missing table is acceptable for
    backups that predate it unless ``allow_missing`` is false.
    """
    objects = conn.execute(
        "SELECT type,sql FROM sqlite_master WHERE name=? "
        "OR (type IN ('index','trigger') AND tbl_name=?)",
        (TABLE, TABLE),
    ).fetchall()
    if not objects:
        return None if allow_missing else "Missing Library state schema"
    tables = [sql for kind, sql in objects if kind == "table"]
    if len(tables) != 1 or len(objects) != 1:
        return "Library state name is reserved by an unsupported schema object"
    if _compact(tables[0] or "") != _compact(_DDL):
        return "Library state has an invalid table definition"
    columns = tuple(
        (row[1], row[2].upper(), row[3], row[5])
        for row in conn.execute(f"PRAGMA table_info({TABLE})")
    )
    if columns != _COLUMNS:
        return "Library state has invalid columns"
    foreign_keys = {
        (row[3], row[2], row[4], row[6])
        for row in conn.execute(f"PRAGMA foreign_key_list({TABLE})")
    }
    if foreign_keys != _FOREIGN_KEYS:
        return "Library state has invalid foreign key definitions"
    if conn.execute(f"PRAGMA foreign_key_check({TABLE})").fetchone():
        return "Library state contains foreign key violations"
    if conn.execute(
        f"SELECT 1 FROM {TABLE} WHERE typeof(title) NOT IN ('null','text') "
        f"OR length(title) > {MAX_TITLE_LENGTH} LIMIT 1"
    ).fetchone():
        return "Library state contains invalid titles"
    return None


def migrate_library_state(conn: sqlite3.Connection) -> None:
    """Install the table atomically; validate before and after creating it."""
    conn.execute("SAVEPOINT prompt_library_state_schema")
    try:
        error = library_schema_error(conn)
        if error:
            raise sqlite3.DatabaseError(error)
        conn.execute(_DDL.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1))
        error = library_schema_error(conn, allow_missing=False)
        if error:
            raise sqlite3.DatabaseError(error)
        conn.execute("RELEASE prompt_library_state_schema")
    except BaseException:
        conn.execute("ROLLBACK TO prompt_library_state_schema")
        conn.execute("RELEASE prompt_library_state_schema")
        raise
