"""Owned schema and bounded restore validation for frozen output locators."""

import sqlite3

from .execution_history_schema import _compact, _uuid_check

try:
    from ..utils.execution_outputs import (
        BUNDLE_OVERHEAD,
        MAX_BUNDLE_BYTES,
        MAX_IMAGE_POSITIONS,
        MAX_REFERENCE_BYTES,
        REFERENCE_OVERHEAD,
        STATE_REASONS,
        OutputBundle,
        OutputReference,
        validate_output_bundle,
    )
except ImportError:
    from utils.execution_outputs import (
        BUNDLE_OVERHEAD,
        MAX_BUNDLE_BYTES,
        MAX_IMAGE_POSITIONS,
        MAX_REFERENCE_BYTES,
        REFERENCE_OVERHEAD,
        STATE_REASONS,
        OutputBundle,
        OutputReference,
        validate_output_bundle,
    )


TABLE_COLUMNS = {
    "execution_output_capture": (
        ("run_id", "TEXT", 1, 1),
        ("state", "TEXT", 1, 0),
        ("reason", "TEXT", 1, 0),
        ("format_version", "INTEGER", 1, 0),
        ("source", "TEXT", 1, 0),
    ),
    "execution_run_outputs": (
        ("run_id", "TEXT", 1, 1),
        ("output_node_id", "TEXT", 1, 2),
        ("image_ordinal", "INTEGER", 1, 3),
        ("folder_type", "TEXT", 1, 0),
        ("subfolder", "TEXT", 1, 0),
        ("filename", "TEXT", 1, 0),
        ("path_style", "TEXT", 1, 0),
        ("root_key", "TEXT", 0, 0),
    ),
}
TABLE_FOREIGN_KEYS = {
    "execution_output_capture": (("run_id", "execution_runs", "run_id"),),
    "execution_run_outputs": (("run_id", "execution_output_capture", "run_id"),),
}


def _text_check(column: str, maximum: int, *, characters: int | None = None) -> str:
    return (
        f"typeof({column}) = 'text' AND length({column}) <= "
        f"{maximum if characters is None else characters} "
        f"AND length(CAST({column} AS BLOB)) <= {maximum} "
        f"AND instr(CAST({column} AS BLOB), x'00') = 0"
    )


def _enum_check(column: str, values: tuple | frozenset) -> str:
    return (
        f"{_text_check(column, 32)} AND {column} IN ("
        + ",".join("'" + value + "'" for value in sorted(values))
        + ")"
    )


_RUN_ID_CHECK = f"{_uuid_check('run_id')} AND {_text_check('run_id', 36)}"
TABLE_CHECKS = {
    "execution_output_capture": (
        _RUN_ID_CHECK,
        _enum_check("state", tuple(STATE_REASONS)),
        _enum_check("reason", frozenset.union(*STATE_REASONS.values())),
        "typeof(format_version) = 'integer' AND format_version = 1",
        f"{_text_check('source', 18)} AND source = 'host_final_history'",
    ),
    "execution_run_outputs": (
        _RUN_ID_CHECK,
        f"{_text_check('output_node_id', 512, characters=128)} "
        "AND length(output_node_id) >= 1",
        "typeof(image_ordinal) = 'integer' AND image_ordinal BETWEEN 0 AND 255",
        _enum_check("folder_type", ("output", "temp")),
        _text_check("subfolder", 2048),
        f"{_text_check('filename', 1024)} AND length(filename) >= 1",
        _enum_check("path_style", ("posix", "windows")),
        f"root_key IS NULL OR ({_text_check('root_key', 64)} "
        "AND length(root_key) = 64 AND root_key NOT GLOB '*[^0-9a-f]*')",
    ),
}


def _table_sql(table: str) -> str:
    columns = TABLE_COLUMNS[table]
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
    return f"CREATE TABLE {table} ({', '.join(definitions)})"


def install_output_schema(conn: sqlite3.Connection) -> None:
    """Install the two owned tables within the caller's migration transaction."""
    for table in TABLE_COLUMNS:
        conn.execute(_table_sql(table))


def output_schema_error(conn: sqlite3.Connection) -> str | None:
    """Reject altered schemas and invalid rows before returning bounded DTOs."""
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view') LIMIT 1"
    ).fetchone():
        return "Unsupported execution output trigger/view"
    tables = {
        row[0]: row[1]
        for row in conn.execute("SELECT name,sql FROM sqlite_master WHERE type='table'")
        if row[0] in TABLE_COLUMNS
    }
    if tables.keys() != TABLE_COLUMNS.keys():
        return "Incomplete execution output schema"
    for table, expected in TABLE_COLUMNS.items():
        columns = tuple(
            (row[1], row[2].upper(), row[3], row[5])
            for row in conn.execute(f"PRAGMA table_xinfo({table})")
        )
        if columns != expected or _compact(tables[table]) != _compact(
            _table_sql(table)
        ):
            return f"{table} has invalid execution output schema"
        foreign = tuple(
            sorted(
                (row[3], row[2], row[4], row[5], row[6], row[7])
                for row in conn.execute(f"PRAGMA foreign_key_list({table})")
            )
        )
        if foreign != tuple(
            (*key, "NO ACTION", "NO ACTION", "NONE")
            for key in TABLE_FOREIGN_KEYS[table]
        ):
            return f"{table} has invalid execution output foreign keys"
        for index in conn.execute(f"PRAGMA index_list({table})"):
            name = index[1].replace('"', '""')
            keys = tuple(
                row for row in conn.execute(f'PRAGMA index_xinfo("{name}")') if row[5]
            )
            if (
                not index[2]
                or index[3] != "pk"
                or index[4]
                or tuple(key[2] for key in keys)
                != tuple(column[0] for column in expected if column[3])
                or any(key[3] or key[4] != "BINARY" for key in keys)
            ):
                return f"{table} has unexpected execution output index/uniqueness"
        # Run every scalar guard in SQLite before any evidence text is fetched.
        predicate = " AND ".join(f"({check})" for check in TABLE_CHECKS[table])
        if conn.execute(
            f"SELECT 1 FROM {table} WHERE NOT ({predicate}) LIMIT 1"
        ).fetchone():
            return f"{table} contains invalid execution output records"
        if conn.execute(f"PRAGMA foreign_key_check({table})").fetchone():
            return f"{table} contains orphan execution output records"
    reference_bytes = f"{REFERENCE_OVERHEAD} + " + " + ".join(
        f"coalesce(length(CAST({column} AS BLOB)), 0)"
        for column in (
            "output_node_id",
            "folder_type",
            "subfolder",
            "filename",
            "path_style",
            "root_key",
        )
    )
    if conn.execute(
        "SELECT 1 FROM execution_run_outputs "
        f"WHERE ({reference_bytes}) > {MAX_REFERENCE_BYTES} LIMIT 1"
    ).fetchone():
        return "Oversized execution output reference"
    if conn.execute(
        "SELECT 1 FROM execution_run_outputs GROUP BY run_id "
        f"HAVING COUNT(*) > {MAX_IMAGE_POSITIONS} OR "
        f"{BUNDLE_OVERHEAD} + SUM({reference_bytes}) > {MAX_BUNDLE_BYTES} LIMIT 1"
    ).fetchone():
        return "Oversized execution output bundle"
    try:
        for run_id, state, reason, version, source in conn.execute(
            "SELECT run_id,state,reason,format_version,source "
            "FROM execution_output_capture"
        ):
            references = tuple(
                OutputReference(*row)
                for row in conn.execute(
                    "SELECT output_node_id,image_ordinal,folder_type,subfolder,"
                    "filename,path_style,root_key FROM execution_run_outputs "
                    "WHERE run_id=? ORDER BY output_node_id,image_ordinal",
                    (run_id,),
                )
            )
            validate_output_bundle(
                OutputBundle(references, state, reason, version, source)
            )
    except (ValueError, TypeError, OverflowError, sqlite3.Error) as exc:
        return f"Invalid execution output content: {type(exc).__name__}"
    return None
