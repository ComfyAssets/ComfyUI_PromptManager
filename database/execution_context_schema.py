"""Owned execution-context v1 tables within execution-history schema v2.

Content IDs hash exact scalar values; evidence JSON contains metadata only.
All validation scans use cursors and bounded per-row payloads.
"""

import json
import sqlite3

from .execution_history_schema import _compact, _uuid_check

try:
    from ..utils.execution_contexts import (
        BUNDLE_STATES,
        CAPTURE_REASONS,
        EVIDENCE_SOURCES,
        UNKNOWN_REASONS,
        MAX_CONTEXT_BYTES,
        ContextBundle,
        PromptSnapshot,
        RecipeContext,
        TextEvidence,
        validate_bundle,
        content_digest,
        text_size,
    )
except ImportError:
    from utils.execution_contexts import (
        BUNDLE_STATES,
        CAPTURE_REASONS,
        EVIDENCE_SOURCES,
        UNKNOWN_REASONS,
        MAX_CONTEXT_BYTES,
        ContextBundle,
        PromptSnapshot,
        RecipeContext,
        TextEvidence,
        validate_bundle,
        content_digest,
        text_size,
    )


FIELDS = ("raw", "effective", "prepend", "append")
TABLE_COLUMNS = {
    "execution_context_texts": (
        ("text_id", "TEXT", 1, 1),
        ("text_value", "TEXT", 1, 0),
    ),
    "execution_prompt_snapshots": (
        ("snapshot_id", "TEXT", 1, 1),
        *((f"{field}_text_id", "TEXT", 0, 0) for field in FIELDS),
    ),
    "execution_contexts": (
        ("context_id", "TEXT", 1, 1),
        ("positive_snapshot_id", "TEXT", 0, 0),
        ("negative_snapshot_id", "TEXT", 0, 0),
    ),
    "execution_run_contexts": (
        ("run_id", "TEXT", 1, 1),
        ("anchor_id", "TEXT", 1, 2),
        ("context_id", "TEXT", 1, 0),
        ("evidence_json", "TEXT", 1, 0),
        ("positive_prompt_id", "INTEGER", 0, 0),
        ("negative_prompt_id", "INTEGER", 0, 0),
        ("pair_id", "INTEGER", 0, 0),
        ("reference_epoch", "TEXT", 0, 0),
        ("capture_version", "INTEGER", 1, 0),
    ),
    "execution_context_capture": (
        ("run_id", "TEXT", 1, 1),
        ("state", "TEXT", 1, 0),
        ("reason", "TEXT", 1, 0),
        ("format_version", "INTEGER", 1, 0),
    ),
}


def _digest_check(column: str, nullable: bool = False) -> str:
    check = (
        f"typeof({column}) = 'text' AND length({column}) = 64 "
        f"AND {column} NOT GLOB '*[^0-9a-f]*'"
    )
    return f"{column} IS NULL OR ({check})" if nullable else check


def _enum_check(column: str, values: frozenset[str]) -> str:
    return (
        f"typeof({column}) = 'text' AND {column} IN ("
        + ",".join("'" + value + "'" for value in sorted(values))
        + ")"
    )


TABLE_CHECKS = {
    "execution_context_texts": (
        _digest_check("text_id"),
        "typeof(text_value) = 'text' AND length(CAST(text_value AS BLOB)) <= 65536",
    ),
    "execution_prompt_snapshots": (
        _digest_check("snapshot_id"),
        *(_digest_check(f"{field}_text_id", True) for field in FIELDS),
    ),
    "execution_contexts": (
        _digest_check("context_id"),
        _digest_check("positive_snapshot_id", True),
        _digest_check("negative_snapshot_id", True),
        "positive_snapshot_id IS NOT NULL OR negative_snapshot_id IS NOT NULL",
    ),
    "execution_run_contexts": (
        _uuid_check("run_id"),
        "typeof(anchor_id) = 'text' AND length(anchor_id) BETWEEN 1 AND 128",
        _digest_check("context_id"),
        "typeof(evidence_json) = 'text' AND length(CAST(evidence_json AS "
        "BLOB)) <= 8192",
        *(
            f"{column} IS NULL OR (typeof({column}) = 'integer' AND {column} > 0)"
            for column in ("positive_prompt_id", "negative_prompt_id", "pair_id")
        ),
        f"reference_epoch IS NULL OR ({_uuid_check('reference_epoch')})",
        "typeof(capture_version) = 'integer' AND capture_version = 1",
    ),
    "execution_context_capture": (
        _uuid_check("run_id"),
        _enum_check("state", BUNDLE_STATES),
        _enum_check("reason", CAPTURE_REASONS),
        "length(CAST(reason AS BLOB)) <= 64",
        "typeof(format_version) = 'integer' AND format_version = 1",
    ),
}
# (source column, destination table, destination column); actions always NO ACTION.
TABLE_FOREIGN_KEYS = {
    "execution_context_texts": (),
    "execution_prompt_snapshots": tuple(
        (f"{field}_text_id", "execution_context_texts", "text_id") for field in FIELDS
    ),
    "execution_contexts": tuple(
        (f"{role}_snapshot_id", "execution_prompt_snapshots", "snapshot_id")
        for role in ("positive", "negative")
    ),
    "execution_run_contexts": (
        ("run_id", "execution_runs", "run_id"),
        ("context_id", "execution_contexts", "context_id"),
    ),
    "execution_context_capture": (("run_id", "execution_runs", "run_id"),),
}
# Only SQLite-generated primary-key indexes are unique. No secondary indexes required.


def install_context_schema(conn: sqlite3.Connection) -> None:
    """Install tables inside the caller's transaction, without committing."""
    for table, columns in TABLE_COLUMNS.items():
        definitions = [
            f"{name} {kind}{' NOT NULL' if required else ''}"
            for name, kind, required, _ in columns
        ]
        primary = [name for name, _, _, order in columns if order]
        definitions.append(f"PRIMARY KEY ({', '.join(primary)})")
        definitions.extend(f"CHECK({check})" for check in TABLE_CHECKS[table])
        definitions.extend(
            f"FOREIGN KEY ({column}) REFERENCES {target}({key}) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION"
            for column, target, key in TABLE_FOREIGN_KEYS[table]
        )
        conn.execute(f"CREATE TABLE {table} ({', '.join(definitions)})")


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate evidence key")
        result[key] = value
    return result


def _snapshot_values(conn: sqlite3.Connection, snapshot_id: str) -> tuple:
    row = conn.execute(
        "SELECT raw_text_id,effective_text_id,prepend_text_id,append_text_id "
        "FROM execution_prompt_snapshots WHERE snapshot_id=?",
        (snapshot_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Missing snapshot")
    values = []
    for text_id in row:
        if text_id is None:
            values.append(None)
        else:
            text = conn.execute(
                "SELECT text_value FROM execution_context_texts WHERE text_id=?",
                (text_id,),
            ).fetchone()
            if text is None:
                raise ValueError("Missing context text")
            text_size(text[0])
            values.append(text[0])
    return tuple(values)


def _validate_evidence(
    conn: sqlite3.Connection, anchor_id: str, context_id: str, encoded: str
) -> tuple[RecipeContext, bool]:
    text_size(encoded, 8192)
    evidence = json.loads(encoded, object_pairs_hook=_unique_object)
    if (
        type(evidence) is not dict
        or set(evidence)
        != {"format_version", "role_provenance", "positive", "negative"}
        or type(evidence["format_version"]) is not int
        or evidence["format_version"] != 1
        or evidence["role_provenance"] != "inferred"
    ):
        raise ValueError("Invalid context evidence")
    row = conn.execute(
        "SELECT positive_snapshot_id,negative_snapshot_id FROM "
        "execution_contexts WHERE context_id=?",
        (context_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Missing context")
    complete = True
    snapshots = []
    for role, snapshot_id in zip(("positive", "negative"), row):
        metadata = evidence[role]
        if snapshot_id is None:
            if metadata is not None:
                raise ValueError("Unexpected role evidence")
            complete = False
            snapshots.append(None)
            continue
        values = _snapshot_values(conn, snapshot_id)
        if type(metadata) is not dict or set(metadata) != set(FIELDS):
            raise ValueError("Invalid snapshot evidence")
        fields = []
        for field, value in zip(FIELDS, values):
            item = metadata[field]
            if type(item) is not dict or set(item) != {
                "provenance",
                "source",
                "reason",
            }:
                raise ValueError("Invalid field evidence")
            provenance, source, reason = (
                item["provenance"],
                item["source"],
                item["reason"],
            )
            if (
                type(provenance) is not str
                or type(source) is not str
                or source not in EVIDENCE_SOURCES.get(provenance, ())
            ):
                raise ValueError("Invalid evidence source")
            if value is None:
                complete = False
                if (
                    provenance != "unknown"
                    or type(reason) is not str
                    or reason not in UNKNOWN_REASONS
                ):
                    raise ValueError("Unknown evidence requires reason")
            elif provenance == "unknown" or reason is not None:
                raise ValueError("Known evidence metadata mismatch")
            fields.append(TextEvidence(value, provenance, source, reason))
        snapshots.append(PromptSnapshot(*fields))
    return RecipeContext(anchor_id, *snapshots, evidence["role_provenance"]), complete


def context_schema_error(conn: sqlite3.Connection) -> str | None:
    """Validate exact owned schema, bounded evidence, fingerprints and links."""
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view') LIMIT 1"
    ).fetchone():
        return "Unsupported execution context trigger/view"
    tables = {
        row[0]: row[1]
        for row in conn.execute("SELECT name,sql FROM sqlite_master WHERE type='table'")
        if row[0] in TABLE_COLUMNS
    }
    if tables.keys() != TABLE_COLUMNS.keys():
        return "Incomplete execution context schema"
    for table, expected in TABLE_COLUMNS.items():
        columns = tuple(
            (row[1], row[2].upper(), row[3], row[5])
            for row in conn.execute(f"PRAGMA table_xinfo({table})")
        )
        if columns != expected:
            return f"{table} has invalid execution context columns"
        sql = _compact(tables[table])
        checks = TABLE_CHECKS[table]
        if sql.count("check(") != len(checks) or any(
            f"check({_compact(check)})" not in sql for check in checks
        ):
            return f"{table} has invalid execution context checks"
        foreign = tuple(
            sorted(
                (row[3], row[2], row[4], row[5], row[6], row[7])
                for row in conn.execute(f"PRAGMA foreign_key_list({table})")
            )
        )
        expected_foreign = tuple(
            sorted(
                (*key, "NO ACTION", "NO ACTION", "NONE")
                for key in TABLE_FOREIGN_KEYS[table]
            )
        )
        if foreign != expected_foreign:
            return f"{table} has invalid execution context foreign keys"
        for index in conn.execute(f"PRAGMA index_list({table})"):
            if not index[2]:
                continue
            name = index[1].replace('"', '""')
            keys = tuple(
                row for row in conn.execute(f'PRAGMA index_xinfo("{name}")') if row[5]
            )
            primary = tuple(column[0] for column in expected if column[3])
            if (
                index[3] != "pk"
                or index[4]
                or tuple(key[2] for key in keys) != primary
                or any(key[3] or key[4] != "BINARY" for key in keys)
            ):
                return f"{table} has unexpected execution context uniqueness"
        predicate = " AND ".join(f"({check})" for check in checks)
        if conn.execute(
            f"SELECT 1 FROM {table} WHERE NOT ({predicate}) LIMIT 1"
        ).fetchone():
            return f"{table} contains invalid execution context records"
        if conn.execute(f"PRAGMA foreign_key_check({table})").fetchone():
            return f"{table} contains orphan execution context records"
    if conn.execute(
        "SELECT 1 FROM execution_run_contexts GROUP BY run_id HAVING "
        "COUNT(*) > 16 LIMIT 1"
    ).fetchone():
        return "Too many execution contexts per run"
    try:
        for text_id, text in conn.execute(
            "SELECT text_id,text_value FROM execution_context_texts"
        ):
            text_size(text)
            if content_digest("text", (text,)) != text_id:
                raise ValueError("Invalid text fingerprint")
        for (snapshot_id,) in conn.execute(
            "SELECT snapshot_id FROM execution_prompt_snapshots"
        ):
            if (
                content_digest("snapshot", _snapshot_values(conn, snapshot_id))
                != snapshot_id
            ):
                raise ValueError("Invalid snapshot fingerprint")
        for context_id, positive, negative in conn.execute(
            "SELECT context_id,positive_snapshot_id,negative_snapshot_id FROM "
            "execution_contexts"
        ):
            if content_digest("context", (positive, negative)) != context_id:
                raise ValueError("Invalid context fingerprint")
            payload_size = 0
            for snapshot_id in (positive, negative):
                if snapshot_id is not None:
                    payload_size += sum(
                        text_size(value)
                        for value in _snapshot_values(conn, snapshot_id)
                        if value is not None
                    )
            if payload_size > MAX_CONTEXT_BYTES:
                raise ValueError("Oversized detached context")
        previous_run = None
        contexts = ()
        for run_id, anchor_id, context_id, evidence in conn.execute(
            "SELECT run_id,anchor_id,context_id,evidence_json FROM "
            "execution_run_contexts ORDER BY run_id,anchor_id"
        ):
            if run_id != previous_run:
                contexts = ()
                previous_run = run_id
            context, complete = _validate_evidence(
                conn, anchor_id, context_id, evidence
            )
            status = conn.execute(
                "SELECT state,reason FROM execution_context_capture WHERE run_id=?",
                (run_id,),
            ).fetchone()
            state, reason = status if status else ("partial", "")
            if state == "complete" and not complete:
                raise ValueError("Incomplete evidence marked complete")
            candidate = (*contexts, context)
            validate_bundle(ContextBundle(candidate, state, reason))
            contexts = candidate
        if conn.execute(
            "SELECT 1 FROM execution_context_capture c WHERE state='complete' "
            "AND NOT EXISTS (SELECT 1 FROM execution_run_contexts r WHERE "
            "r.run_id=c.run_id) LIMIT 1"
        ).fetchone():
            raise ValueError("Complete capture has no contexts")
    except (ValueError, TypeError, RecursionError, OverflowError) as exc:
        return f"Invalid execution context content: {type(exc).__name__}"
    return None
