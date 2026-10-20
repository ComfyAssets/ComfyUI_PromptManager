"""Persisted image favorites, keyed by gallery root label + relative path.

The root label is the root's public form (``_public_path``), so a favorite
follows its folder when the configured roots are reordered. Optional in old
backups, required once a database is current (see ``library_schema.py``).
"""

import re
import sqlite3
from typing import Iterable

TABLE = "image_favorites"
MAX_KEY_LENGTH = 4096
_LOOKUP_CHUNK = 500
_DDL = (
    f"CREATE TABLE {TABLE} (root_label TEXT NOT NULL, relative_path TEXT NOT NULL, "
    "created_at TEXT NOT NULL, PRIMARY KEY(root_label, relative_path))"
)
_AUTOINDEX = f"sqlite_autoindex_{TABLE}_1"
_COLUMNS = (
    ("root_label", "TEXT", 1, 1),
    ("relative_path", "TEXT", 1, 2),
    ("created_at", "TEXT", 1, 0),
)


def _compact(sql: str) -> str:
    return re.sub(r"\s+", "", sql).lower()


def image_favorites_schema_error(
    conn: sqlite3.Connection, *, allow_missing: bool = True
) -> str | None:
    """Reject unsupported favorites schemas and invalid persisted values.

    Reads only. A missing table is acceptable for backups that predate it
    unless ``allow_missing`` is false.
    """
    objects = conn.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE name=? "
        "OR (type IN ('index','trigger') AND tbl_name=?)",
        (TABLE, TABLE),
    ).fetchall()
    if not objects:
        return None if allow_missing else "Missing image favorites schema"
    tables = [sql for kind, _name, sql in objects if kind == "table"]
    others = {(kind, name, sql) for kind, name, sql in objects if kind != "table"}
    if len(tables) != 1 or others != {("index", _AUTOINDEX, None)}:
        return "Image favorites name is reserved by an unsupported schema object"
    if _compact(tables[0] or "") != _compact(_DDL):
        return "Image favorites has an invalid table definition"
    columns = tuple(
        (row[1], row[2].upper(), row[3], row[5])
        for row in conn.execute(f"PRAGMA table_info({TABLE})")
    )
    if columns != _COLUMNS:
        return "Image favorites has invalid columns"
    # Bytes bound characters from above (UTF-8 is at most 4 bytes each).
    limit = MAX_KEY_LENGTH * 4
    if conn.execute(
        f"SELECT 1 FROM {TABLE} WHERE typeof(root_label) != 'text' "
        "OR typeof(relative_path) != 'text' OR typeof(created_at) != 'text' "
        f"OR length(CAST(root_label AS BLOB)) > {limit} "
        f"OR length(CAST(relative_path AS BLOB)) > {limit} "
        "OR length(CAST(created_at AS BLOB)) > 64 LIMIT 1"
    ).fetchone():
        return "Image favorites contains invalid values"
    for label, path in conn.execute(
        f"SELECT CAST(root_label AS BLOB), CAST(relative_path AS BLOB) FROM {TABLE} "
        f"WHERE length(CAST(root_label AS BLOB)) > {MAX_KEY_LENGTH} "
        f"OR length(CAST(relative_path AS BLOB)) > {MAX_KEY_LENGTH}"
    ):
        for raw in (label, path):
            if len(bytes(raw).decode("utf-8", "replace")) > MAX_KEY_LENGTH:
                return "Image favorites contains invalid values"
    return None


def migrate_image_favorites(conn: sqlite3.Connection) -> None:
    """Install the table atomically; validate before and after creating it."""
    conn.execute("SAVEPOINT image_favorites_schema")
    try:
        error = image_favorites_schema_error(conn)
        if error:
            raise sqlite3.DatabaseError(error)
        conn.execute(_DDL.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1))
        error = image_favorites_schema_error(conn, allow_missing=False)
        if error:
            raise sqlite3.DatabaseError(error)
        conn.execute("RELEASE image_favorites_schema")
    except BaseException:
        conn.execute("ROLLBACK TO image_favorites_schema")
        conn.execute("RELEASE image_favorites_schema")
        raise


class ImageFavoriteOperations:
    """PromptDatabase mixin: set, clear and look up image favorites."""

    def set_image_favorite(self, root_label: str, relative_path: str) -> bool:
        """Mark one file as a favorite; repeating it is a no-op. Returns True."""
        from .models import utc_now_iso  # models imports this module's schema

        with self.model.get_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO image_favorites VALUES (?, ?, ?)",
                (root_label, relative_path, utc_now_iso()),
            )
        return True

    def clear_image_favorite(self, root_label: str, relative_path: str) -> bool:
        """Remove one favorite; True when a row was removed."""
        with self.model.get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM image_favorites WHERE root_label=? AND relative_path=?",
                (root_label, relative_path),
            )
        return cursor.rowcount > 0

    def get_image_favorites(
        self, root_label: str, relative_paths: Iterable[str]
    ) -> set[str]:
        """The subset of *relative_paths* favorited under *root_label*."""
        wanted = list(dict.fromkeys(relative_paths))
        found: set[str] = set()
        conn = self.model.get_connection()
        for start in range(0, len(wanted), _LOOKUP_CHUNK):
            chunk = wanted[start : start + _LOOKUP_CHUNK]
            marks = ",".join("?" * len(chunk))
            rows = conn.execute(  # only "?" placeholders are interpolated
                "SELECT relative_path FROM image_favorites WHERE root_label=? "
                f"AND relative_path IN ({marks})",  # nosec B608
                (root_label, *chunk),
            )
            found.update(path for (path,) in rows)
        return found
