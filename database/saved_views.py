"""Persisted saved views (D16): a named Images or Library toolbar state.

A view's state holds exactly its page's toolbar values and its Refine-this-view
refinements, whitelisted per page and stored as compact JSON of at most 4 KB.
The same validation guards writes and restores. Optional in old backups,
required once a database is current (see ``library_schema.py``).
"""

import json
import re
import sqlite3
from typing import Optional

TABLE = "saved_views"
PAGES = ("images", "library")
# The schema check's literal SQL repeats the name and state caps (names in
# bytes, at most 4 per character); keep them in step.
MAX_NAME_LENGTH = 100
MAX_STATE_BYTES = 4096
MAX_TAGS = 20
MAX_TAG_LENGTH = 50
_IMAGE_SORTS = ("newest", "oldest", "name", "name-desc", "rating")
_LIBRARY_SORTS = ("newest", "oldest", "name", "rating")
_MEDIA = ("all", "image", "video")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SOURCE = re.compile(r"all|0|[1-9][0-9]{0,8}")

# (kind, limit-or-choices) per state key; "text" values are length-capped.
_STATE_FIELDS = {
    "images": {
        "q": ("text", 500),
        "category": ("text", 100),
        "sort": ("choice", _IMAGE_SORTS),
        "source": ("source", None),
        "folder": ("text", 1024),
        "media": ("choice", _MEDIA),
        "tags": ("tags", None),
        # The Favorites page's filter (#249 D51): a view saved there reopens it.
        "favorites": ("bool", None),
    },
    "library": {
        "q": ("text", 500),
        "category": ("text", 100),
        "sort": ("choice", _LIBRARY_SORTS),
        "tags": ("tags", None),
    },
}

_DDL = (
    f"CREATE TABLE {TABLE} (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "page TEXT NOT NULL CHECK(page IN ('images','library')), "
    "name TEXT NOT NULL, state TEXT NOT NULL, position INTEGER NOT NULL, "
    "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
)
_COLUMNS = (
    ("id", "INTEGER", 0, 1),
    ("page", "TEXT", 1, 0),
    ("name", "TEXT", 1, 0),
    ("state", "TEXT", 1, 0),
    ("position", "INTEGER", 1, 0),
    ("created_at", "TEXT", 1, 0),
    ("updated_at", "TEXT", 1, 0),
)
_SELECT = "SELECT id, page, name, state FROM saved_views"


def _compact(sql: str) -> str:
    return re.sub(r"\s+", "", sql).lower()


def validate_name(name: object) -> str:
    """The trimmed name, 1 to 100 characters without control characters."""
    if not isinstance(name, str):
        raise ValueError("Name must be a string")
    trimmed = name.strip()
    if not 1 <= len(trimmed) <= MAX_NAME_LENGTH:
        raise ValueError(f"Name must be 1 to {MAX_NAME_LENGTH} characters")
    if _CONTROL.search(trimmed):
        raise ValueError("Name contains control characters")
    return trimmed


def _validate_value(key: str, kind: str, limit, value: object) -> None:
    if kind == "tags":
        if not isinstance(value, list) or len(value) > MAX_TAGS:
            raise ValueError(f"State '{key}' must be a list of at most {MAX_TAGS}")
        for tag in value:
            if (
                not isinstance(tag, str)
                or not tag.strip()
                or len(tag) > MAX_TAG_LENGTH
                or "," in tag
                or _CONTROL.search(tag)
            ):
                raise ValueError(f"State '{key}' holds an invalid tag")
        return
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"State '{key}' must be true or false")
        return
    if not isinstance(value, str):
        raise ValueError(f"State '{key}' must be a string")
    if kind == "choice" and value not in limit:
        raise ValueError(f"State '{key}' is not a supported value")
    if kind == "source" and not _SOURCE.fullmatch(value):
        raise ValueError(f"State '{key}' must be 'all' or a source index")
    if kind == "text" and (len(value) > limit or _CONTROL.search(value)):
        raise ValueError(f"State '{key}' is too long or has control characters")


def encode_state(page: object, state: object) -> str:
    """Validate *state* for *page* and return its compact JSON (at most 4 KB)."""
    if page not in PAGES:
        raise ValueError("Page must be 'images' or 'library'")
    fields = _STATE_FIELDS[page]
    if not isinstance(state, dict):
        raise ValueError("State must be an object")
    if set(state) != set(fields):
        raise ValueError(
            f"State for {page} must contain exactly " + ", ".join(sorted(fields))
        )
    for key, (kind, limit) in fields.items():
        _validate_value(key, kind, limit, state[key])
    encoded = json.dumps(
        {key: state[key] for key in fields}, ensure_ascii=False, separators=(",", ":")
    )
    if len(encoded.encode("utf-8")) > MAX_STATE_BYTES:
        raise ValueError("State must encode to at most 4 KB")
    return encoded


def _row_error(page, name, state) -> bool:
    """True when a persisted row would not pass the write validation."""
    try:
        if validate_name(name) != name:
            return True
        decoded = json.loads(state)
        return encode_state(page, decoded) != state
    except (ValueError, TypeError):
        return True


def saved_views_schema_error(
    conn: sqlite3.Connection, *, allow_missing: bool = True
) -> Optional[str]:
    """Reject unsupported saved-views schemas and invalid persisted values.

    Reads only. A missing table is acceptable for backups that predate it
    unless ``allow_missing`` is false.
    """
    objects = conn.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE name=? "
        "OR (type IN ('index','trigger') AND tbl_name=?)",
        (TABLE, TABLE),
    ).fetchall()
    if not objects:
        return None if allow_missing else "Missing saved views schema"
    tables = [sql for kind, _name, sql in objects if kind == "table"]
    if len(tables) != 1 or len(objects) != 1:
        return "Saved views name is reserved by an unsupported schema object"
    if _compact(tables[0] or "") != _compact(_DDL):
        return "Saved views has an invalid table definition"
    columns = tuple(
        (row[1], row[2].upper(), row[3], row[5])
        for row in conn.execute(f"PRAGMA table_info({TABLE})")
    )
    if columns != _COLUMNS:
        return "Saved views has invalid columns"
    # Literal SQL: the table name and limits are constants of this module.
    if conn.execute(
        "SELECT 1 FROM saved_views WHERE typeof(page) != 'text' "
        "OR typeof(name) != 'text' OR typeof(state) != 'text' "
        "OR typeof(position) != 'integer' OR typeof(created_at) != 'text' "
        "OR typeof(updated_at) != 'text' "
        "OR length(CAST(name AS BLOB)) > 400 "
        "OR length(CAST(state AS BLOB)) > 4096 "
        "OR length(CAST(created_at AS BLOB)) > 64 "
        "OR length(CAST(updated_at AS BLOB)) > 64 LIMIT 1"
    ).fetchone():
        return "Saved views contains invalid values"
    for page, name, state in conn.execute("SELECT page, name, state FROM saved_views"):
        if _row_error(page, name, state):
            return "Saved views contains invalid values"
    return None


def migrate_saved_views(conn: sqlite3.Connection) -> None:
    """Install the table atomically; validate before and after creating it."""
    conn.execute("SAVEPOINT saved_views_schema")
    try:
        error = saved_views_schema_error(conn)
        if error:
            raise sqlite3.DatabaseError(error)
        conn.execute(_DDL.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1))
        error = saved_views_schema_error(conn, allow_missing=False)
        if error:
            raise sqlite3.DatabaseError(error)
        conn.execute("RELEASE saved_views_schema")
    except BaseException:
        conn.execute("ROLLBACK TO saved_views_schema")
        conn.execute("RELEASE saved_views_schema")
        raise


def _view(row) -> dict:
    view_id, page, name, state = row
    return {"id": view_id, "page": page, "name": name, "state": json.loads(state)}


class SavedViewOperations:
    """PromptDatabase mixin: list, create, rename and delete saved views."""

    def list_saved_views(self) -> list[dict]:
        """Every view, in list order (position, then creation)."""
        rows = self.model.get_connection().execute(_SELECT + " ORDER BY position, id")
        return [_view(row) for row in rows]

    def create_saved_view(self, page: object, name: object, state: object) -> dict:
        """Validate and append one view at the end of the list."""
        from .models import utc_now_iso  # models imports this module's schema

        encoded = encode_state(page, state)
        trimmed = validate_name(name)
        now = utc_now_iso()
        with self.model.get_connection() as conn:
            cursor = conn.execute(
                "INSERT INTO saved_views (page, name, state, position, "
                "created_at, updated_at) SELECT ?, ?, ?, "
                "COALESCE(MAX(position), -1) + 1, ?, ? FROM saved_views",
                (page, trimmed, encoded, now, now),
            )
            row = conn.execute(
                _SELECT + " WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        return _view(row)

    def rename_saved_view(self, view_id: int, name: object) -> Optional[dict]:
        """The renamed view, or None when no view has *view_id*."""
        from .models import utc_now_iso

        trimmed = validate_name(name)
        with self.model.get_connection() as conn:
            conn.execute(
                "UPDATE saved_views SET name = ?, updated_at = ? WHERE id = ?",
                (trimmed, utc_now_iso(), view_id),
            )
            row = conn.execute(_SELECT + " WHERE id = ?", (view_id,)).fetchone()
        return _view(row) if row else None

    def delete_saved_view(self, view_id: int) -> bool:
        """True when a view was removed."""
        with self.model.get_connection() as conn:
            cursor = conn.execute("DELETE FROM saved_views WHERE id = ?", (view_id,))
        return cursor.rowcount > 0
