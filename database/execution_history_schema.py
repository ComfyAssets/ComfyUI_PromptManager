"""Versioned additive schema for observed execution attempts."""

import re
import sqlite3
from uuid import uuid4

SCHEMA_VERSION = 1


def _uuid_check(column: str) -> str:
    return (
        f"typeof({column}) = 'text' AND length({column}) = 36 "
        f"AND substr({column}, 9, 1) = '-' AND substr({column}, 14, 1) = '-' "
        f"AND substr({column}, 19, 1) = '-' AND substr({column}, 24, 1) = '-' "
        f"AND length(replace({column}, '-', '')) = 32 "
        f"AND replace({column}, '-', '') NOT GLOB '*[^0-9a-f]*'"
    )


def _integer_check(column: str, *, nullable: bool = False) -> str:
    expression = f"typeof({column}) = 'integer' AND {column} >= 0"
    return f"{column} IS NULL OR ({expression})" if nullable else expression


_METADATA_CHECKS = (
    "singleton_id = 1",
    "schema_version = 1",
    _uuid_check("storage_epoch"),
)
_RUN_CHECKS = (
    *(
        _uuid_check(column)
        for column in ("run_id", "session_id", "job_id", "storage_epoch")
    ),
    _integer_check("queue_item_id"),
    "record_version = 1",
    _integer_check("observed_dequeue_at"),
    *(
        _integer_check(column, nullable=True)
        for column in (
            "observed_callback_at",
            "executor_started_at",
            "terminal_event_at",
        )
    ),
    "outcome IN ('observed', 'succeeded', 'failed', 'interrupted', 'unknown')",
    "(outcome = 'observed' AND reason IS NULL AND observed_callback_at IS NULL) OR "
    "(outcome != 'observed' AND reason IS NOT NULL "
    "AND observed_callback_at IS NOT NULL AND "
    "((outcome = 'succeeded' AND reason = 'terminal_success') OR "
    "(outcome = 'failed' AND reason = 'terminal_error') OR "
    "(outcome = 'interrupted' AND reason = 'terminal_interrupted') OR "
    "(outcome = 'unknown' AND reason IN ('missing_terminal', "
    "'conflicting_terminal', 'invalid_evidence', 'evidence_limit'))))",
    "diagnostics_omitted IN (0, 1)",
    *(
        f"{column} IS NULL OR (typeof({column}) = 'text' "
        f"AND length({column}) <= {limit})"
        for column, limit in (
            ("node_id", 128),
            ("node_type", 128),
            ("exception_type", 256),
        )
    ),
)
_TABLE_COLUMNS = {
    "execution_history_metadata": (
        ("singleton_id", "INTEGER", 1, 1),
        ("schema_version", "INTEGER", 1, 0),
        ("storage_epoch", "TEXT", 1, 0),
    ),
    "execution_runs": (
        ("run_id", "TEXT", 1, 1),
        ("session_id", "TEXT", 1, 0),
        ("queue_item_id", "INTEGER", 1, 0),
        ("job_id", "TEXT", 1, 0),
        ("storage_epoch", "TEXT", 1, 0),
        ("record_version", "INTEGER", 1, 0),
        ("observed_dequeue_at", "INTEGER", 1, 0),
        ("observed_callback_at", "INTEGER", 0, 0),
        ("executor_started_at", "INTEGER", 0, 0),
        ("terminal_event_at", "INTEGER", 0, 0),
        ("outcome", "TEXT", 1, 0),
        ("reason", "TEXT", 0, 0),
        ("diagnostics_omitted", "INTEGER", 1, 0),
        ("node_id", "TEXT", 0, 0),
        ("node_type", "TEXT", 0, 0),
        ("exception_type", "TEXT", 0, 0),
    ),
}
_TABLE_CHECKS = {
    "execution_history_metadata": _METADATA_CHECKS,
    "execution_runs": _RUN_CHECKS,
}
_INDEXES = {
    "idx_execution_runs_identity": (True, ("session_id", "queue_item_id")),
    "idx_execution_runs_observed": (False, ("observed_dequeue_at", "run_id")),
}


def _compact(sql: str) -> str:
    return "".join(
        token if token.startswith("'") else re.sub(r"\s+", "", token).lower()
        for token in re.findall(r"'(?:''|[^'])*'|[^']+", sql)
    )


def history_schema_error(
    conn: sqlite3.Connection, *, allow_missing: bool = True
) -> str | None:
    """Reject partial/unsupported history schemas and invalid persisted records."""
    tables = {
        row[0]: row[1]
        for row in conn.execute("SELECT name,sql FROM sqlite_master WHERE type='table'")
        if row[0] in _TABLE_COLUMNS
    }
    if not tables:
        return None if allow_missing else "Missing execution history schema"
    if tables.keys() != _TABLE_COLUMNS.keys():
        return "Incomplete execution history schema"
    for table, expected in _TABLE_COLUMNS.items():
        columns = tuple(
            (row[1], row[2].upper(), row[3], row[5])
            for row in conn.execute(f"PRAGMA table_info({table})")
        )
        if columns != expected:
            return f"{table} has invalid execution history columns"
        sql = _compact(tables[table])
        checks = _TABLE_CHECKS[table]
        if sql.count("check(") != len(checks) or any(
            f"check({_compact(check)})" not in sql for check in checks
        ):
            return f"{table} has invalid execution history checks"
        if conn.execute(f"PRAGMA foreign_key_list({table})").fetchone():
            return f"{table} has unexpected execution history foreign keys"
        predicate = " AND ".join(f"({check})" for check in checks)
        if conn.execute(
            f"SELECT 1 FROM {table} WHERE NOT ({predicate}) LIMIT 1"
        ).fetchone():
            return f"{table} contains invalid execution history records"
    metadata = conn.execute(
        "SELECT singleton_id,schema_version,storage_epoch "
        "FROM execution_history_metadata"
    ).fetchall()
    if len(metadata) != 1 or metadata[0][0] != 1 or metadata[0][1] != SCHEMA_VERSION:
        return "Unsupported execution history metadata/version"
    indexes = {row[1]: row for row in conn.execute("PRAGMA index_list(execution_runs)")}
    for name, (unique, columns) in _INDEXES.items():
        row = indexes.get(name)
        if row is None or bool(row[2]) != unique or row[4]:
            return f"Missing or invalid execution history index {name}"
        actual = tuple(conn.execute(f"PRAGMA index_xinfo({name})"))
        keys = tuple(item for item in actual if item[5])
        if tuple(item[2] for item in keys) != columns or any(
            item[3] or item[4] != "BINARY" for item in keys
        ):
            return f"Invalid execution history index {name}"
    return None


def migrate_execution_history(conn: sqlite3.Connection) -> None:
    """Install both tables and indexes atomically, without backfilling runs."""
    conn.execute("SAVEPOINT execution_history_schema")
    try:
        present = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name IN "
            "('execution_runs','execution_history_metadata') LIMIT 1"
        ).fetchone()
        if not present:
            for table, columns in _TABLE_COLUMNS.items():
                definitions = [
                    f"{name} {kind}{' NOT NULL' if required else ''}"
                    f"{' PRIMARY KEY' if primary else ''}"
                    for name, kind, required, primary in columns
                ]
                definitions.extend(f"CHECK({check})" for check in _TABLE_CHECKS[table])
                conn.execute(f"CREATE TABLE {table} ({', '.join(definitions)})")
            for name, (unique, columns) in _INDEXES.items():
                conn.execute(
                    f"CREATE {'UNIQUE ' if unique else ''}INDEX {name} "
                    f"ON execution_runs({', '.join(columns)})"
                )
            conn.execute(
                "INSERT INTO execution_history_metadata VALUES(1,?,?)",
                (SCHEMA_VERSION, str(uuid4())),
            )
        error = history_schema_error(conn, allow_missing=False)
        if error:
            raise sqlite3.DatabaseError(error)
        conn.execute("RELEASE execution_history_schema")
    except BaseException:
        conn.execute("ROLLBACK TO execution_history_schema")
        conn.execute("RELEASE execution_history_schema")
        raise


def rotate_history_epoch(conn: sqlite3.Connection) -> str:
    """Publish a fresh staging epoch without rewriting restored run epochs."""
    epoch = str(uuid4())
    with conn:
        cursor = conn.execute(
            "UPDATE execution_history_metadata SET storage_epoch=? "
            "WHERE singleton_id=1",
            (epoch,),
        )
        if cursor.rowcount != 1:
            raise sqlite3.DatabaseError("Missing execution history metadata")
    return epoch
