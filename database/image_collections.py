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
rules never stops the library from opening; restore checks the values too,
but never recomputes ``name_key`` (case tables differ between Python
versions).
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
# A name key's byte bound: case folding expands a character to at most
# three, each at most 4 UTF-8 bytes.
_MAX_KEY_BYTES = MAX_NAME_LENGTH * 3 * 4
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


def _has_control(text: str) -> bool:
    """Any Unicode control character (category Cc: C0, DEL and C1)."""
    return any(unicodedata.category(char) == "Cc" for char in text)


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
    if _has_control(collapsed):
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
        "OR length(CAST(name_key AS BLOB)) = 0 "
        f"OR length(CAST(name_key AS BLOB)) > {_MAX_KEY_BYTES} "
        f"OR length(CAST(created_at AS BLOB)) > {_TIMESTAMP_BYTES} LIMIT 1"
    ).fetchone():
        return "Image collections contains invalid values"
    # The key is not recomputed: Unicode case tables differ between Python
    # versions, and a key written by another version must stay restorable.
    # Its UNIQUE constraint keeps names distinct; writes compute it.
    for (name,) in conn.execute(f"SELECT name FROM {COLLECTIONS_TABLE}"):  # nosec B608
        if (
            not 1 <= len(name) <= MAX_NAME_LENGTH
            or _has_control(name)
            or name != name.strip()
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


class CollectionMissing(LookupError):
    """No collection has the id (a 404 ``collection-not-found``)."""


class CollectionNameTaken(ValueError):
    """Another collection already has the name's key (a 409 ``name-taken``)."""

    def __init__(self, collection_id: int):
        super().__init__("Another collection has this name")
        self.collection_id = collection_id


class CollectionFilesMissing(LookupError):
    """A new name with nothing to insert (a 404 ``files-not-found``)."""


LIST_SQL = f"SELECT id, name FROM {COLLECTIONS_TABLE} ORDER BY name_key, id"
GET_SQL = f"SELECT id, name FROM {COLLECTIONS_TABLE} WHERE id = ?"
BY_KEY_SQL = f"SELECT id, name FROM {COLLECTIONS_TABLE} WHERE name_key = ?"
MEMBERS_SQL = (
    f"SELECT relative_path FROM {ITEMS_TABLE} "
    "WHERE collection_id = ? AND root_key = ?"
)
ROOT_MEMBERS_SQL = (
    f"SELECT collection_id, relative_path FROM {ITEMS_TABLE} WHERE root_key = ?"
)
COUNTS_SQL = (
    f"SELECT c.id, (SELECT COUNT(*) FROM {ITEMS_TABLE} i "
    f"WHERE i.collection_id = c.id) FROM {COLLECTIONS_TABLE} c"
)
FILE_SQL = (
    f"SELECT collection_id FROM {ITEMS_TABLE} "
    "WHERE root_key = ? AND relative_path = ? ORDER BY collection_id"
)
# Every read, for the EXPLAIN QUERY PLAN check: none scans the items table and
# only the two whole-list reads (LIST_SQL, COUNTS_SQL) scan the collections.
READ_SQL = (
    LIST_SQL,
    GET_SQL,
    BY_KEY_SQL,
    MEMBERS_SQL,
    ROOT_MEMBERS_SQL,
    COUNTS_SQL,
    FILE_SQL,
)
_INSERT_ITEM_SQL = f"INSERT OR IGNORE INTO {ITEMS_TABLE} VALUES (?, ?, ?, ?)"
_DELETE_ITEM_SQL = (
    f"DELETE FROM {ITEMS_TABLE} "
    "WHERE collection_id = ? AND root_key = ? AND relative_path = ?"
)


def _collection_id(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 999_999_999:
        raise CollectionInvalid("Collection id must be a positive integer")
    return value


def _items(items: object) -> list:
    """Distinct ``(root key, relative key)`` pairs, in order."""
    if not isinstance(items, (list, tuple)):
        raise CollectionInvalid("Items must be a list")
    found = []
    for item in items:
        if (
            not isinstance(item, (list, tuple))
            or len(item) != 2
            or not all(
                isinstance(part, str) and 1 <= len(part) <= MAX_KEY_LENGTH
                for part in item
            )
        ):
            raise CollectionInvalid("Each item is a root key and a relative key")
        found.append((item[0], item[1]))
    return list(dict.fromkeys(found))


def _row(row) -> dict:
    return {"id": row[0], "name": row[1]}


class ImageCollectionOperations:
    """PromptDatabase mixin: collections and their members.

    Each write runs in its own ``BEGIN IMMEDIATE`` transaction
    (``_pair_transaction``), so two tabs creating one name are serialized.
    """

    def list_collections(self) -> list[dict]:
        """Every collection ``{id, name}`` in name-key order, then id."""
        rows = self.model.get_connection().execute(LIST_SQL)
        return [_row(row) for row in rows]

    def get_collection(self, collection_id: int) -> Optional[dict]:
        """``{id, name}`` of one collection, or None."""
        row = (
            self.model.get_connection()
            .execute(GET_SQL, (_collection_id(collection_id),))
            .fetchone()
        )
        return _row(row) if row else None

    def collection_exists(self, collection_id: int) -> bool:
        return self.get_collection(collection_id) is not None

    def collection_members(self, collection_id: int, root_key: str) -> set[str]:
        """The relative keys stored for one collection under one source."""
        rows = self.model.get_connection().execute(
            MEMBERS_SQL, (_collection_id(collection_id), root_key)
        )
        return {path for (path,) in rows}

    def root_collection_members(self, root_key: str) -> dict[int, set[str]]:
        """``{collection id: relative keys}`` stored under one source."""
        found: dict[int, set[str]] = {}
        for collection_id, path in self.model.get_connection().execute(
            ROOT_MEMBERS_SQL, (root_key,)
        ):
            found.setdefault(collection_id, set()).add(path)
        return found

    def collection_count_snapshot(self, root_keys: list) -> tuple[dict, dict]:
        """``({root key: {id: relative keys}}, {id: stored rows})`` read in
        one read transaction, so a write between the reads cannot skew them.
        """
        conn = self.model.get_connection()
        if conn.in_transaction:
            raise RuntimeError("Collection counts require their own transaction")
        conn.execute("BEGIN")
        try:
            members = {}
            for root_key in dict.fromkeys(root_keys):
                found: dict[int, set[str]] = {}
                for collection_id, path in conn.execute(ROOT_MEMBERS_SQL, (root_key,)):
                    found.setdefault(collection_id, set()).add(path)
                members[root_key] = found
            stored = {cid: count for cid, count in conn.execute(COUNTS_SQL)}
        finally:
            conn.rollback()  # it only read
        return members, stored

    def collection_counts(self) -> dict[int, int]:
        """``{collection id: stored rows}`` for every collection."""
        rows = self.model.get_connection().execute(COUNTS_SQL)
        return {collection_id: count for collection_id, count in rows}

    def file_collections(self, root_key: str, relative_path: str) -> list[int]:
        """Ids of the collections holding one file."""
        rows = self.model.get_connection().execute(FILE_SQL, (root_key, relative_path))
        return [collection_id for (collection_id,) in rows]

    def add_to_collection(
        self, *, items: object, collection_id: object = None, name: object = None
    ) -> dict:
        """Add files to an existing id or a name; create the name when needed.

        A name whose key exists uses that collection; otherwise a collection
        is created only when at least one item is inserted. Returns
        ``{collection, created, added, already}``.
        """
        from .image_removal_schema import clear_marks_of_keys
        from .models import utc_now_iso  # models imports this module's schema

        if (collection_id is None) == (name is None):
            raise CollectionInvalid("Give either a collection id or a name")
        pairs = _items(items)
        if name is not None:
            spelled = normalize_collection_name(name)
        else:
            collection_id = _collection_id(collection_id)
        now = utc_now_iso()
        with self._pair_transaction() as conn:
            created = False
            if name is not None:
                row = conn.execute(
                    BY_KEY_SQL, (collection_name_key(spelled),)
                ).fetchone()
                if row is None:
                    if not pairs:
                        raise CollectionFilesMissing("Nothing to add")
                    cursor = conn.execute(
                        f"INSERT INTO {COLLECTIONS_TABLE} "  # nosec B608
                        "(name, name_key, created_at) VALUES (?, ?, ?)",
                        (spelled, collection_name_key(spelled), now),
                    )
                    row, created = (cursor.lastrowid, spelled), True
            else:
                row = conn.execute(GET_SQL, (collection_id,)).fetchone()
                if row is None:
                    raise CollectionMissing("No such collection")
            collection = _row(row)
            added = sum(
                conn.execute(
                    _INSERT_ITEM_SQL, (collection["id"], root, path, now)
                ).rowcount
                for root, path in pairs
            )
            clear_marks_of_keys(conn, pairs)  # #480: a collected file is listed
        return {
            "collection": collection,
            "created": created,
            "added": added,
            "already": len(pairs) - added,
        }

    def remove_from_collection(self, collection_id: object, items: object) -> dict:
        """Remove files from one collection; ``{collection, removed}``."""
        collection_id = _collection_id(collection_id)
        pairs = _items(items)
        with self._pair_transaction() as conn:
            row = conn.execute(GET_SQL, (collection_id,)).fetchone()
            if row is None:
                raise CollectionMissing("No such collection")
            removed = sum(
                conn.execute(_DELETE_ITEM_SQL, (collection_id, root, path)).rowcount
                for root, path in pairs
            )
        return {"collection": _row(row), "removed": removed}

    def rename_collection(self, collection_id: object, name: object) -> dict:
        """The renamed ``{id, name}``; a case variant of its own name is fine."""
        collection_id = _collection_id(collection_id)
        spelled = normalize_collection_name(name)
        key = collection_name_key(spelled)
        with self._pair_transaction() as conn:
            if conn.execute(GET_SQL, (collection_id,)).fetchone() is None:
                raise CollectionMissing("No such collection")
            taken = conn.execute(BY_KEY_SQL, (key,)).fetchone()
            if taken is not None and taken[0] != collection_id:
                raise CollectionNameTaken(taken[0])
            conn.execute(
                f"UPDATE {COLLECTIONS_TABLE} SET name = ?, name_key = ? "  # nosec B608
                "WHERE id = ?",
                (spelled, key, collection_id),
            )
        return {"id": collection_id, "name": spelled}

    def delete_collection(self, collection_id: object) -> bool:
        """Delete one collection and its members (never a file). True."""
        collection_id = _collection_id(collection_id)
        with self._pair_transaction() as conn:
            cursor = conn.execute(
                f"DELETE FROM {COLLECTIONS_TABLE} WHERE id = ?",  # nosec B608
                (collection_id,),
            )
            if cursor.rowcount == 0:
                raise CollectionMissing("No such collection")
        return True
