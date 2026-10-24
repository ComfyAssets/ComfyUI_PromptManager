"""Each workflow and prompt graph stored once, compressed (#249 S6 carry-over).

``generated_images.workflow_data`` (the UI workflow) and ``prompt_metadata``
(the API prompt graph) used to hold the same large JSON text again for every
image of a run. Now each distinct text is one ``workflow_documents`` row:

- ``sha256``: the SHA-256 of the text's exact UTF-8 bytes (unique key);
- ``byte_length``: the length of those bytes;
- ``data``: ``zlib.compress`` of those bytes.

An image row references its texts through ``workflow_document_id`` and
``prompt_document_id``. Two forms coexist until the migration has rewritten
every row, and both stay readable forever:

- old form: the text inline in ``workflow_data`` / ``prompt_metadata``, no id;
- new form: the inline column NULL and the document id set.

The text is stored and hashed exactly as written, never parsed and
re-serialized, because a JSON round trip can change bytes (key order,
spacing, number spelling, duplicate keys, ComfyUI's ``NaN`` tokens).

``read_stored_text`` is the one decoder. Inline text, when present, wins (a
row an older app version rewrote inline stays right); otherwise the document
is read by its rowid, decompressed within its recorded length and checked
against its length and hash. A document that fails a check reads as
unreadable (None, logged), never as other text. ``resolve_image_row`` puts
the texts back into a ``generated_images`` row and drops the id keys, so
every payload keeps its shape.

No trigger: every restore validator refuses triggers in a restored file. A
document is removed in code: ``release_documents`` in the same transaction
that drops a row's last reference, and ``sweep_documents`` (at startup, after
the migration and before Reclaim space) for anything a path missed.
"""

import hashlib
import logging
import re
import sqlite3
import zlib
from collections import OrderedDict
from typing import Any, Dict, Iterable, Optional, Set

logger = logging.getLogger("prompt_manager.database.workflow_documents")

TABLE = "workflow_documents"
# (inline text column, document id column) on generated_images.
TEXT_COLUMNS = (
    ("workflow_data", "workflow_document_id"),
    ("prompt_metadata", "prompt_document_id"),
)
DOCUMENT_ID_COLUMNS = tuple(reference for _column, reference in TEXT_COLUMNS)
INDEXES = {
    "workflow_document_id": "idx_image_workflow_document",
    "prompt_document_id": "idx_image_prompt_document",
}
COMPRESSION_LEVEL = 6
# A text larger than this stays inline: a document is decompressed in one
# piece, and its recorded length bounds that (a restored file cannot make a
# small row expand without limit).
MAX_DOCUMENT_BYTES = 256 * 2**20
ID_BATCH = 400  # ids bound per statement (under SQLite's old 999 limit)

_DDL = (
    f"CREATE TABLE {TABLE} (id INTEGER PRIMARY KEY, sha256 BLOB NOT NULL UNIQUE, "
    "byte_length INTEGER NOT NULL, data BLOB NOT NULL)"
)
_AUTOINDEX = f"sqlite_autoindex_{TABLE}_1"
_COLUMNS = (
    ("id", "INTEGER", 0, 1),
    ("sha256", "BLOB", 1, 0),
    ("byte_length", "INTEGER", 1, 0),
    ("data", "BLOB", 1, 0),
)
_REFERENCE_TYPE = f"INTEGER REFERENCES {TABLE}(id)"

_UNREFERENCED = (
    "NOT EXISTS (SELECT 1 FROM generated_images g "
    "WHERE g.workflow_document_id=workflow_documents.id) "
    "AND NOT EXISTS (SELECT 1 FROM generated_images g "
    "WHERE g.prompt_document_id=workflow_documents.id)"
)
LOOKUP_SQL = "SELECT id, byte_length FROM workflow_documents WHERE sha256=?"
READ_SQL = "SELECT sha256, byte_length, data FROM workflow_documents WHERE id=?"
INSERT_SQL = (
    "INSERT INTO workflow_documents (sha256, byte_length, data) VALUES (?, ?, ?)"
)
# Built from constants only; the ids are bound as "?" marks.
RELEASE_SQL = "DELETE FROM workflow_documents WHERE id IN ({marks}) AND "
RELEASE_SQL += _UNREFERENCED
# The sweep walks the ids through the small unique index, not the blobs.
SWEEP_SQL = (
    "DELETE FROM workflow_documents WHERE id IN (SELECT id FROM workflow_documents "
    "INDEXED BY sqlite_autoindex_workflow_documents_1 WHERE "
)
SWEEP_SQL += _UNREFERENCED + ")"
IMAGE_DOCUMENTS_SQL = (
    "SELECT workflow_document_id, prompt_document_id FROM generated_images WHERE "
)

# Statements whose query plans tests/test_workflow_documents.py records.
QUERY_PLAN_STATEMENTS = {
    "lookup by hash": (LOOKUP_SQL, (b"\0" * 32,)),
    "read by id": (READ_SQL, (1,)),
    "release": (RELEASE_SQL.format(marks="?,?"), (1, 2)),
    "sweep": (SWEEP_SQL, ()),
    "documents of images": (IMAGE_DOCUMENTS_SQL + "id IN (?,?)", (1, 2)),
    "documents of prompts": (IMAGE_DOCUMENTS_SQL + "prompt_id IN (?,?)", (1, 2)),
}


class DocumentMismatch(Exception):
    """A document would not give back exactly the text it was made from."""


def _compact(sql: str) -> str:
    return re.sub(r"\s+", "", sql or "").lower()


# ── schema ────────────────────────────────────────────────────────────


def _reference_columns(conn: sqlite3.Connection) -> Dict[str, tuple]:
    return {
        row[1]: tuple(row)
        for row in conn.execute("PRAGMA table_info(generated_images)")
        if row[1] in DOCUMENT_ID_COLUMNS
    }


def _reference_error(conn: sqlite3.Connection, columns: Dict[str, tuple]):
    for name, info in columns.items():
        # (cid, name, type, notnull, default, pk)
        if (str(info[2]).upper(), info[3], info[4], info[5]) != ("INTEGER", 0, None, 0):
            return "Workflow document references have an invalid column"
    keys = {
        (row[3], row[2], row[4])
        for row in conn.execute("PRAGMA foreign_key_list(generated_images)")
        if row[3] in DOCUMENT_ID_COLUMNS
    }
    if keys != {(name, TABLE, "id") for name in DOCUMENT_ID_COLUMNS}:
        return "Workflow document references have invalid foreign keys"
    for column, index in INDEXES.items():
        info = conn.execute(f'PRAGMA index_info("{index}")').fetchall()
        owner = conn.execute(
            "SELECT tbl_name FROM sqlite_master WHERE type='index' AND name=?",
            (index,),
        ).fetchone()
        if [row[2] for row in info] != [column] or not owner:
            return "Workflow document references are missing their index"
        if owner[0] != "generated_images":
            return "Workflow document references are missing their index"
    return None


def _value_error(conn: sqlite3.Connection):
    if conn.execute(
        "SELECT 1 FROM workflow_documents WHERE typeof(sha256)!='blob' "
        "OR length(sha256)!=32 OR typeof(byte_length)!='integer' OR byte_length<0 "
        "OR byte_length>? OR typeof(data)!='blob' LIMIT 1",
        (MAX_DOCUMENT_BYTES,),
    ).fetchone():
        return "Workflow documents contain invalid values"
    for column in DOCUMENT_ID_COLUMNS:
        # typeof() reads no overflow page; a dangling id is a rowid probe.
        # ``column`` is one of DOCUMENT_ID_COLUMNS, never caller data.
        sql = (
            f"SELECT 1 FROM generated_images g WHERE typeof(g.{column}) "  # nosec B608
            f"NOT IN ('null','integer') OR (g.{column} IS NOT NULL AND NOT EXISTS "
            f"(SELECT 1 FROM workflow_documents d WHERE d.id=g.{column})) LIMIT 1"
        )
        if conn.execute(sql).fetchone():
            return "Workflow document references point at missing documents"
    return None


def workflow_documents_schema_error(
    conn: sqlite3.Connection, *, allow_missing: bool = True
) -> Optional[str]:
    """Reject an unsupported document schema or invalid stored values (reads only).

    A database from before the dedupe has neither the table nor the id
    columns; that is acceptable unless ``allow_missing`` is false. Half of
    the schema never is.
    """
    objects = conn.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE name=? "
        "OR (type IN ('index','trigger') AND tbl_name=?)",
        (TABLE, TABLE),
    ).fetchall()
    has_images = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='generated_images'"
    ).fetchone()
    columns = _reference_columns(conn) if has_images else {}
    if not objects and not columns:
        if allow_missing or not has_images:
            return None
        return "Missing workflow document schema"
    if not objects or set(columns) != set(DOCUMENT_ID_COLUMNS):
        return "Incomplete workflow document schema"
    tables = [sql for kind, _name, sql in objects if kind == "table"]
    others = {(kind, name, sql) for kind, name, sql in objects if kind != "table"}
    if len(tables) != 1 or others != {("index", _AUTOINDEX, None)}:
        return "Workflow documents name is reserved by an unsupported schema object"
    if _compact(tables[0]) != _compact(_DDL):
        return "Workflow documents have an invalid table definition"
    shape = tuple(
        (row[1], str(row[2]).upper(), row[3], row[5])
        for row in conn.execute(f"PRAGMA table_info({TABLE})")
    )
    if shape != _COLUMNS:
        return "Workflow documents have invalid columns"
    return _reference_error(conn, columns) or _value_error(conn)


def migrate_workflow_documents(conn: sqlite3.Connection) -> None:
    """Install the table, the id columns and their indexes atomically.

    Runs after every older migration (the table rebuilds only run on
    databases without ``file_path``, before this). Validates before and
    after, then sweeps documents no row references.
    """
    conn.execute("SAVEPOINT workflow_documents_schema")
    try:
        error = workflow_documents_schema_error(conn)
        if error:
            raise sqlite3.DatabaseError(error)
        conn.execute(_DDL.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1))
        present = _reference_columns(conn)
        for column in DOCUMENT_ID_COLUMNS:
            if column not in present:
                conn.execute(
                    f"ALTER TABLE generated_images ADD COLUMN {column} "
                    f"{_REFERENCE_TYPE}"
                )
        for column, index in INDEXES.items():
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS {index} ON generated_images({column})"
            )
        error = workflow_documents_schema_error(conn, allow_missing=False)
        if error:
            raise sqlite3.DatabaseError(error)
        sweep_documents(conn)
        conn.execute("RELEASE workflow_documents_schema")
    except BaseException:
        conn.execute("ROLLBACK TO workflow_documents_schema")
        conn.execute("RELEASE workflow_documents_schema")
        raise


# ── write ─────────────────────────────────────────────────────────────


def storable(text: Any) -> bool:
    """Whether ``text`` can be a document (text within MAX_DOCUMENT_BYTES)."""
    if not isinstance(text, str):
        return False
    # UTF-8 takes at most 4 bytes a character: most texts need no encoding.
    if len(text) * 4 <= MAX_DOCUMENT_BYTES:
        return True
    return len(text.encode("utf-8")) <= MAX_DOCUMENT_BYTES


def store_text(conn: sqlite3.Connection, text: Optional[str]) -> Optional[int]:
    """The id of the document holding exactly ``text``; None for None.

    Reuses the document with the same hash and length, or inserts a new one
    whose compressed bytes were checked to decompress to ``text``'s bytes.
    Opens a write transaction first when none is open, so a concurrent
    ``release_documents`` cannot remove a reused document before the caller
    references it. Raises DocumentMismatch, TypeError for a non-text value
    and ValueError for a text over MAX_DOCUMENT_BYTES.
    """
    if text is None:
        return None
    if not isinstance(text, str):
        raise TypeError("Only text is stored as a workflow document")
    raw = text.encode("utf-8")
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise ValueError("Text is too large for a workflow document")
    digest = hashlib.sha256(raw).digest()
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    row = conn.execute(LOOKUP_SQL, (digest,)).fetchone()
    if row is not None:
        if row[1] != len(raw):
            raise DocumentMismatch("A stored document has another length")
        return row[0]
    data = zlib.compress(raw, COMPRESSION_LEVEL)
    if zlib.decompress(data) != raw:  # pragma: no cover - zlib is exact
        raise DocumentMismatch("Compression did not round-trip")
    return conn.execute(INSERT_SQL, (digest, len(raw), data)).lastrowid


def stored_values(
    conn: sqlite3.Connection, texts: Dict[str, Optional[str]]
) -> Dict[str, Any]:
    """New-form column values for ``{text column: text}`` (writers).

    Each text becomes ``{column: None, id column: document id}``; None
    stays ``{column: None, id column: None}``; a value that cannot be a
    document (not text, or too large) stays inline with no id.
    """
    references = dict(TEXT_COLUMNS)
    values: Dict[str, Any] = {}
    for column, text in texts.items():
        reference = references[column]
        if text is None or not storable(text):
            values[column], values[reference] = text, None
        else:
            values[column], values[reference] = None, store_text(conn, text)
    return values


# ── read ──────────────────────────────────────────────────────────────


def _decode(row, document_id) -> Optional[str]:
    if row is None:
        logger.warning(f"Workflow document {document_id} is missing")
        return None
    digest, length, data = bytes(row[0]), row[1], row[2]
    try:
        if not isinstance(length, int) or not 0 <= length <= MAX_DOCUMENT_BYTES:
            raise ValueError("invalid length")
        inflater = zlib.decompressobj()
        raw = inflater.decompress(bytes(data), length + 1)
        if len(raw) != length or inflater.unconsumed_tail or not inflater.eof:
            raise ValueError("length differs")
        if hashlib.sha256(raw).digest() != digest:
            raise ValueError("hash differs")
        return raw.decode("utf-8")
    except (zlib.error, ValueError, TypeError) as error:
        logger.warning(f"Workflow document {document_id} is unreadable: {error}")
        return None


def read_stored_text(
    conn: sqlite3.Connection, inline: Any, document_id: Optional[int]
) -> Any:
    """The stored text of one column: inline when present, else the document.

    Returns None when there is neither, or when the document is missing or
    fails its checks (logged). An inline value is returned unchanged.
    """
    if inline is not None:
        return inline
    if document_id is None:
        return None
    return _decode(conn.execute(READ_SQL, (document_id,)).fetchone(), document_id)


class DocumentReader:
    """``read_stored_text`` with a small memo for list reads on one connection."""

    MEMO = 32

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._memo: "OrderedDict[int, Optional[str]]" = OrderedDict()

    def text(self, inline: Any, document_id: Optional[int]) -> Any:
        if inline is not None or document_id is None:
            return read_stored_text(self.conn, inline, document_id)
        if document_id in self._memo:
            self._memo.move_to_end(document_id)
            return self._memo[document_id]
        text = read_stored_text(self.conn, None, document_id)
        self._memo[document_id] = text
        if len(self._memo) > self.MEMO:
            self._memo.popitem(last=False)
        return text


def resolve_image_row(reader: DocumentReader, row) -> Dict[str, Any]:
    """A ``generated_images`` row as a dict with its texts read back.

    The id columns are dropped, so a payload built from it has the shape it
    had before the dedupe. Columns the row did not select stay absent.
    """
    data = dict(row)
    for column, reference in TEXT_COLUMNS:
        has_reference = reference in data
        document_id = data.pop(reference, None)
        if column in data or has_reference:
            data[column] = reader.text(data.get(column), document_id)
    return data


# ── garbage ───────────────────────────────────────────────────────────


def _chunks(values):
    values = list(values)
    for start in range(0, len(values), ID_BATCH):
        yield values[start : start + ID_BATCH]


def image_documents(conn: sqlite3.Connection, where: str, params) -> Set[int]:
    """The document ids the image rows matching ``where`` reference.

    ``where`` is a fixed condition written in this package (``id IN (...)``,
    ``prompt_id IN (...)``), never caller data; values go in ``params``.
    """
    ids: Set[int] = set()
    sql = IMAGE_DOCUMENTS_SQL + where
    for row in conn.execute(sql, list(params)):  # nosec B608
        ids.update(value for value in row if value is not None)
    return ids


def documents_of_rows(
    conn: sqlite3.Connection, column: str, values: Iterable[Any]
) -> Set[int]:
    """Document ids of the image rows whose ``column`` is in ``values``.

    ``column`` is ``id`` or ``prompt_id`` (the rows a prompt delete cascades to).
    """
    if column not in ("id", "prompt_id"):
        raise ValueError(f"Unsupported column {column!r}")
    ids: Set[int] = set()
    for chunk in _chunks(values):
        marks = ",".join("?" for _ in chunk)
        ids |= image_documents(conn, f"{column} IN ({marks})", chunk)
    return ids


def release_documents(conn: sqlite3.Connection, ids: Iterable[Optional[int]]) -> int:
    """Delete the documents in ``ids`` that no image row references now.

    Runs in the caller's transaction, right after the delete or update that
    dropped the references. Returns how many documents were removed.
    """
    removed = 0
    for chunk in _chunks(sorted({i for i in ids if i is not None})):
        marks = ",".join("?" for _ in chunk)
        # Only "?" marks are interpolated.
        sql = RELEASE_SQL.format(marks=marks)
        removed += conn.execute(sql, chunk).rowcount  # nosec B608
    return removed


def sweep_documents(conn: sqlite3.Connection) -> int:
    """Delete every document no image row references; returns the count."""
    return conn.execute(SWEEP_SQL).rowcount
