"""Needs review schema group (#249): capture-bound ambiguous items and decisions.

The group installs like ``image_annotations``: optional in old backups,
required once a database is current, and valid only beside a valid image
annotation group, because every item cites one annotation byte record. It owns

- ``media_review_metadata``: one row, the group's schema version and the
  review index revision (moved by every queue change);
- ``media_review_items``: one ambiguous resolver result for one byte record,
  keyed by ``(byte_id, resolver_version, evidence_hash)``, with the file's
  spelling when it was recorded (the locator is case-folded on Windows, a
  target is not), its canonical evidence, candidate count, ``limited`` flag,
  state and revision;
- ``media_review_decisions``: the auditable decision history of an item, one
  row per decision revision, with optional navigation to the pair and image
  the decision linked (nulled, never cascaded, when those are deleted).

Evidence is canonical JSON (:func:`canonical_evidence`): one object, sorted
keys, no whitespace, ASCII only (so characters, bytes and the 8 MiB bound
agree and lone surrogates survive), no NaN or infinity. ``evidence_hash`` is
the SHA-256 of the item's evidence text.

Installation never backfills anything and never touches the filesystem.
Validation reads only: catalog objects compared after whitespace compaction,
columns and foreign keys through the PRAGMAs, persisted values in single
bounded SQL passes, then one streamed pass that re-canonicalizes each evidence
document (one row in memory at a time).
"""

import hashlib
import json
import re
import sqlite3
from typing import Any, Optional

from .annotation_schema import BYTES_TABLE
from .annotation_schema import TABLES as ANNOTATION_TABLES
from .annotation_schema import _catalog as _annotation_catalog
from .annotation_schema import annotation_schema_error

try:
    from ..utils.json_strict import dumps_strict
except ImportError:
    from utils.json_strict import dumps_strict

SCHEMA_VERSION = 1
METADATA_TABLE = "media_review_metadata"
ITEMS_TABLE = "media_review_items"
DECISIONS_TABLE = "media_review_decisions"
TABLES = (METADATA_TABLE, ITEMS_TABLE, DECISIONS_TABLE)
MAX_EVIDENCE_BYTES = 8 * 1024 * 1024
MAX_CANDIDATES = 16
# The file's own spelling when it was recorded (bytes of UTF-8 text). Locators
# are case-folded on Windows, so a target is formatted from this instead.
MAX_SPELLED_PATH_BYTES = 16384
ITEM_STATES = ("pending", "selected", "unresolved")
DECISION_OUTCOMES = ("selected", "unresolved")
_TIMESTAMP_BYTES = 64
_HEX64 = "length({c})=64 AND {c} NOT GLOB '*[^0-9a-f]*'"
_EVIDENCE_CHECK = f"CHECK(length(CAST(evidence AS BLOB)) <= {MAX_EVIDENCE_BYTES})"

_TABLE_DDL = {
    METADATA_TABLE: (
        f"CREATE TABLE {METADATA_TABLE} (singleton_id INTEGER PRIMARY KEY "
        "CHECK(singleton_id=1), schema_version INTEGER NOT NULL "
        f"CHECK(schema_version={SCHEMA_VERSION}), "
        "review_index_revision INTEGER NOT NULL DEFAULT 0 "
        "CHECK(review_index_revision>=0))"
    ),
    ITEMS_TABLE: (
        f"CREATE TABLE {ITEMS_TABLE} (id TEXT PRIMARY KEY CHECK(length(id)=36), "
        f"byte_id TEXT NOT NULL REFERENCES {BYTES_TABLE}(id) ON DELETE RESTRICT, "
        "spelled_path TEXT NOT NULL CHECK(length(CAST(spelled_path AS BLOB)) "
        f"BETWEEN 1 AND {MAX_SPELLED_PATH_BYTES}), "
        "resolver_version INTEGER NOT NULL, evidence_hash TEXT NOT NULL "
        f"CHECK({_HEX64.format(c='evidence_hash')}), "
        f"evidence TEXT NOT NULL {_EVIDENCE_CHECK}, "
        "candidate_count INTEGER NOT NULL "
        f"CHECK(candidate_count BETWEEN 0 AND {MAX_CANDIDATES}), "
        "limited INTEGER NOT NULL CHECK(limited IN (0,1)), "
        "state TEXT NOT NULL CHECK(state IN ('pending','selected','unresolved')), "
        "revision INTEGER NOT NULL CHECK(revision>=0), created_at TEXT NOT NULL, "
        "updated_at TEXT NOT NULL, UNIQUE(byte_id, resolver_version, evidence_hash))"
    ),
    DECISIONS_TABLE: (
        f"CREATE TABLE {DECISIONS_TABLE} (id TEXT PRIMARY KEY "
        "CHECK(length(id)=36), item_id TEXT NOT NULL "
        f"REFERENCES {ITEMS_TABLE}(id) ON DELETE RESTRICT, "
        "decision_revision INTEGER NOT NULL, created_at TEXT NOT NULL, "
        "outcome TEXT NOT NULL CHECK(outcome IN ('selected','unresolved')), "
        "candidate_hash TEXT CHECK(candidate_hash IS NULL OR "
        f"({_HEX64.format(c='candidate_hash')})), "
        f"evidence TEXT NOT NULL {_EVIDENCE_CHECK}, "
        "pair_id INTEGER REFERENCES prompt_pairs(id) ON DELETE SET NULL, "
        "image_id INTEGER REFERENCES generated_images(id) ON DELETE SET NULL, "
        "UNIQUE(item_id, decision_revision), "
        "CHECK((outcome='selected') = (candidate_hash IS NOT NULL)))"
    ),
}
_INDEX_DDL = {
    # The queue reads pending items oldest first with a stable tiebreak.
    "idx_media_review_items_queue": (
        "CREATE INDEX idx_media_review_items_queue ON "
        f"{ITEMS_TABLE}(state, created_at, id)"
    ),
    # Deleting a pair or an image nulls decision navigation by index, not scan.
    "idx_media_review_decisions_pair": (
        "CREATE INDEX idx_media_review_decisions_pair ON " f"{DECISIONS_TABLE}(pair_id)"
    ),
    "idx_media_review_decisions_image": (
        "CREATE INDEX idx_media_review_decisions_image ON "
        f"{DECISIONS_TABLE}(image_id)"
    ),
}
# ``sqlite_autoindex_<table>_<n>``: the TEXT primary key and the UNIQUE key.
_AUTOINDEXES = {ITEMS_TABLE: 2, DECISIONS_TABLE: 2}
# (name, declared type, not null, default, primary key position)
_COLUMNS = {
    METADATA_TABLE: (
        ("singleton_id", "INTEGER", 0, None, 1),
        ("schema_version", "INTEGER", 1, None, 0),
        ("review_index_revision", "INTEGER", 1, "0", 0),
    ),
    ITEMS_TABLE: (
        ("id", "TEXT", 0, None, 1),
        ("byte_id", "TEXT", 1, None, 0),
        ("spelled_path", "TEXT", 1, None, 0),
        ("resolver_version", "INTEGER", 1, None, 0),
        ("evidence_hash", "TEXT", 1, None, 0),
        ("evidence", "TEXT", 1, None, 0),
        ("candidate_count", "INTEGER", 1, None, 0),
        ("limited", "INTEGER", 1, None, 0),
        ("state", "TEXT", 1, None, 0),
        ("revision", "INTEGER", 1, None, 0),
        ("created_at", "TEXT", 1, None, 0),
        ("updated_at", "TEXT", 1, None, 0),
    ),
    DECISIONS_TABLE: (
        ("id", "TEXT", 0, None, 1),
        ("item_id", "TEXT", 1, None, 0),
        ("decision_revision", "INTEGER", 1, None, 0),
        ("created_at", "TEXT", 1, None, 0),
        ("outcome", "TEXT", 1, None, 0),
        ("candidate_hash", "TEXT", 0, None, 0),
        ("evidence", "TEXT", 1, None, 0),
        ("pair_id", "INTEGER", 0, None, 0),
        ("image_id", "INTEGER", 0, None, 0),
    ),
}
# (column, parent table, parent column, on delete)
_FOREIGN_KEYS = {
    METADATA_TABLE: set(),
    ITEMS_TABLE: {("byte_id", BYTES_TABLE, "id", "RESTRICT")},
    DECISIONS_TABLE: {
        ("item_id", ITEMS_TABLE, "id", "RESTRICT"),
        ("pair_id", "prompt_pairs", "id", "SET NULL"),
        ("image_id", "generated_images", "id", "SET NULL"),
    },
}


class _NonCanonical(ValueError):
    pass


def _reject_constant(name: str) -> Any:
    raise _NonCanonical(f"JSON constant {name} is not allowed")


# Built once: validation re-encodes every stored document at startup.
_ENCODER = json.JSONEncoder(
    sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
)
_DECODER = json.JSONDecoder(parse_constant=_reject_constant)


def canonical_evidence(value: Any) -> str:
    """Serialize one evidence object canonically; ``ValueError`` when invalid.

    The text is a JSON object with sorted keys, no whitespace, ASCII only and
    no NaN/infinity, at most ``MAX_EVIDENCE_BYTES`` bytes. A non-finite number
    (ComfyUI stamps ``NaN`` into graphs) is written as ``null``, the store's
    policy everywhere (``utils.json_strict.dumps_strict``).
    """
    if not isinstance(value, dict):
        raise ValueError("Review evidence must be a JSON object")
    try:
        try:
            text = _ENCODER.encode(value)
        except ValueError as error:
            if "Out of range float" not in str(error):
                raise
            text = dumps_strict(
                value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            )
    except (TypeError, RecursionError) as error:
        raise ValueError(f"Review evidence is not serializable: {error}") from None
    if len(text) > MAX_EVIDENCE_BYTES:
        raise ValueError("Review evidence is larger than 8 MiB")
    return text


def choosable(candidate: Any) -> bool:
    """True when a candidate offers a prompt to choose: it is ``resolved`` and
    its positive side carries non-blank text. The page disables any other
    candidate, so it is never something to choose.
    """
    if not isinstance(candidate, dict) or candidate.get("state") != "resolved":
        return False
    positive = candidate.get("positive")
    if not isinstance(positive, dict):
        return False
    text = positive.get("text")
    return isinstance(text, str) and bool(text.strip())


# An item is worth a decision only when at least this many candidates offer
# a prompt; with fewer there is nothing to choose between (maintainer test of
# #284/#288: 39 items whose candidates were all "unresolved").
MIN_CHOOSABLE = 2


def evidence_offers_choice(evidence: Any) -> bool:
    """False when stored evidence provably offers nothing to choose.

    Unlimited evidence must list ``MIN_CHOOSABLE`` choosable candidates. A
    limited item stores none, only ``candidate_total``; it may offer a choice
    (more than ``MAX_CANDIDATES`` prompts) unless that total is too small.
    """
    if not isinstance(evidence, dict):
        return False
    if evidence.get("limited"):
        total = evidence.get("candidate_total")
        return type(total) is int and total >= MIN_CHOOSABLE
    candidates = evidence.get("candidates")
    if not isinstance(candidates, list):
        return False
    # The saver anchor's one pair (#390) is a suggestion to confirm.
    needed = 1 if evidence.get("anchor") == "saver" else MIN_CHOOSABLE
    return sum(1 for c in candidates if choosable(c)) >= needed


def evidence_digest(text: str) -> str:
    """SHA-256 of canonical evidence text (ASCII, hence UTF-8 identical)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_canonical(text: Any) -> bool:
    if not isinstance(text, str):
        return False
    try:
        value = _DECODER.decode(text)
        return canonical_evidence(value) == text
    except (ValueError, RecursionError):
        return False


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


def _hex64(column: str) -> str:
    return f"typeof({column}) = 'text' AND " + _HEX64.format(c=column)


def _evidence(column: str) -> str:
    # Type and size only: the streamed pass below proves the text canonical
    # (which implies printable ASCII), so a pattern scan here would read every
    # document a second time.
    return (
        f"typeof({column}) = 'text' "
        f"AND length(CAST({column} AS BLOB)) BETWEEN 2 AND {MAX_EVIDENCE_BYTES}"
    )


def _nullable_integer(column: str) -> str:
    return f"({column} IS NULL OR typeof({column}) = 'integer')"


def _in(column: str, values: tuple) -> str:
    return f"typeof({column}) = 'text' AND {column} IN ({repr(values)[1:-1]})"


# Every row of a table must satisfy its predicate; one pass each, LIMIT 1.
_ROW_PREDICATES = {
    ITEMS_TABLE: " AND ".join(
        (
            _uuid("id"),
            _uuid("byte_id"),
            "typeof(spelled_path) = 'text' AND length(CAST(spelled_path AS BLOB)) "
            f"BETWEEN 1 AND {MAX_SPELLED_PATH_BYTES}",
            "typeof(resolver_version) = 'integer'",
            _hex64("evidence_hash"),
            _evidence("evidence"),
            _non_negative("candidate_count"),
            f"candidate_count <= {MAX_CANDIDATES}",
            "typeof(limited) = 'integer' AND limited IN (0, 1)",
            _in("state", ITEM_STATES),
            _non_negative("revision"),
            _timestamp("created_at"),
            _timestamp("updated_at"),
        )
    ),
    DECISIONS_TABLE: " AND ".join(
        (
            _uuid("id"),
            _uuid("item_id"),
            _non_negative("decision_revision"),
            _timestamp("created_at"),
            _in("outcome", DECISION_OUTCOMES),
            "(CASE WHEN outcome = 'selected' THEN "
            f"{_hex64('candidate_hash')} ELSE candidate_hash IS NULL END)",
            _evidence("evidence"),
            _nullable_integer("pair_id"),
            _nullable_integer("image_id"),
        )
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
            return f"Review queue table {table} has invalid columns"
        foreign_keys = {
            (row[3], row[2], row[4], row[6])
            for row in conn.execute(f"PRAGMA foreign_key_list({table})")
        }
        if foreign_keys != _FOREIGN_KEYS[table]:
            return f"Review queue table {table} has invalid foreign keys"
        if conn.execute(f"PRAGMA foreign_key_check({table})").fetchone():
            return f"Review queue table {table} contains foreign key violations"
    return None


def _evidence_error(conn: sqlite3.Connection) -> Optional[str]:
    """Stream every evidence document once; one row is held at a time.

    Rows are read with ``fetchone``: iterating a managed connection's cursor
    would prefetch ``ITER_CHUNK`` (128) rows (``database/managed_connection.py``).
    """
    items = conn.execute(
        f"SELECT evidence_hash, evidence FROM {ITEMS_TABLE}"  # nosec B608
    )
    while (row := items.fetchone()) is not None:
        evidence_hash, text = row[0], row[1]
        if not _is_canonical(text) or evidence_digest(text) != evidence_hash:
            return f"Review queue table {ITEMS_TABLE} contains invalid evidence"
    decisions = conn.execute(f"SELECT evidence FROM {DECISIONS_TABLE}")  # nosec B608
    while (row := decisions.fetchone()) is not None:
        text = row[0]
        if not _is_canonical(text):
            return f"Review queue table {DECISIONS_TABLE} contains invalid evidence"
    return None


def _value_error(conn: sqlite3.Connection) -> Optional[str]:
    # Only this module's constants are interpolated into the SQL below.
    rows = conn.execute(
        "SELECT COUNT(*), MIN(typeof(singleton_id) = 'integer' "  # nosec B608
        "AND singleton_id = 1 AND typeof(schema_version) = 'integer' "
        f"AND schema_version = {SCHEMA_VERSION} "
        f"AND {_non_negative('review_index_revision')}) FROM {METADATA_TABLE}"
    ).fetchone()
    if rows[0] != 1 or rows[1] != 1:
        return "Review queue metadata is invalid"
    for table, predicate in _ROW_PREDICATES.items():
        if conn.execute(
            f"SELECT 1 FROM {table} WHERE NOT ({predicate}) LIMIT 1"  # nosec B608
        ).fetchone():
            return f"Review queue table {table} contains invalid values"
    return _evidence_error(conn)


def review_without_annotations_error(conn: sqlite3.Connection) -> Optional[str]:
    """Catalog only: a review group present without its annotation tables.

    Startup asks this before the annotation group installs, which would make
    a review group copied without it look valid. It reads no rows; the full
    validation runs once in :func:`migrate_review_state`.
    """
    if not _catalog(conn):
        return None
    present = {
        name for kind, name, _sql in _annotation_catalog(conn) if kind == "table"
    }
    if present != set(ANNOTATION_TABLES):
        return (
            "Review queue schema requires a valid image annotation schema: "
            "Missing image annotation schema"
        )
    return None


def review_schema_error(
    conn: sqlite3.Connection,
    *,
    allow_missing: bool = True,
    annotations_valid: bool = False,
) -> Optional[str]:
    """Reject partial or unsupported review schemas and invalid rows.

    Reads only: nothing is ever repaired. A database without any of the
    group's objects is acceptable for backups that predate it unless
    ``allow_missing`` is false. A present group requires a present, valid
    image annotation group; ``annotations_valid`` says the caller has just
    validated it (startup's annotation install), so only its presence is
    checked again.
    """
    objects = _catalog(conn)
    if not objects:
        return None if allow_missing else "Missing review queue schema"
    if annotations_valid:
        annotation_error = review_without_annotations_error(conn)
    else:
        annotation_error = annotation_schema_error(conn, allow_missing=False)
        if annotation_error:
            annotation_error = (
                "Review queue schema requires a valid image annotation schema: "
                + annotation_error
            )
    if annotation_error:
        return annotation_error
    present = {name for kind, name, _sql in objects if kind == "table"}
    if present != set(TABLES):
        return "Incomplete review queue schema"
    actual = {(kind, name, _compact(sql or "")) for kind, name, sql in objects}
    if actual != _expected_catalog():
        return "Review queue has an invalid schema definition"
    return _structure_error(conn) or _value_error(conn)


def migrate_review_state(conn: sqlite3.Connection) -> None:
    """Install the group atomically; validate before and after creating it.

    Runs after ``migrate_annotation_state`` (which has just validated the
    annotation group): the group is only valid beside the annotation group it
    cites. A present group is validated once, evidence included.
    """
    conn.execute("SAVEPOINT media_review_schema")
    try:
        installed = bool(_catalog(conn))
        error = review_schema_error(conn, annotations_valid=True)
        if error:
            raise sqlite3.DatabaseError(error)
        if installed:
            # Already complete and valid: nothing to create, and the evidence
            # pass is the expensive part of validation, so it runs once.
            conn.execute("RELEASE media_review_schema")
            return
        for sql in _TABLE_DDL.values():
            conn.execute(sql.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1))
        for sql in _INDEX_DDL.values():
            conn.execute(sql.replace("INDEX", "INDEX IF NOT EXISTS", 1))
        conn.execute(
            f"INSERT OR IGNORE INTO {METADATA_TABLE} "
            "(singleton_id, schema_version, review_index_revision) VALUES (1, ?, 0)",
            (SCHEMA_VERSION,),
        )
        error = review_schema_error(conn, allow_missing=False, annotations_valid=True)
        if error:
            raise sqlite3.DatabaseError(error)
        conn.execute("RELEASE media_review_schema")
    except BaseException:
        conn.execute("ROLLBACK TO media_review_schema")
        conn.execute("RELEASE media_review_schema")
        raise
