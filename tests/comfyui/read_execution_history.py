"""Bounded read-only SQLite inspection for the owned execution fixture."""

import argparse
import json
from pathlib import Path
import sqlite3
from uuid import UUID


def read_runs(database: str, job_id: str, session_id: str | None = None) -> dict:
    job_id = str(UUID(job_id))
    if session_id is not None:
        session_id = str(UUID(session_id))
    uri = Path(database).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=0.25) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='execution_runs'"
        ).fetchone()
        if table is None:
            return {"schemaAvailable": False, "rows": []}
        sql = "SELECT * FROM execution_runs WHERE job_id=?"
        parameters = [job_id]
        if session_id is not None:
            sql += " AND session_id=?"
            parameters.append(session_id)
        rows = connection.execute(
            sql + " ORDER BY rowid LIMIT 257", parameters
        ).fetchall()
        if len(rows) > 256:
            raise ValueError("Execution query exceeds the 256-row bound")
        return {"schemaAvailable": True, "rows": [dict(row) for row in rows]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database")
    parser.add_argument("job_id")
    parser.add_argument("--session-id")
    arguments = parser.parse_args()
    print(
        json.dumps(
            read_runs(arguments.database, arguments.job_id, arguments.session_id)
        )
    )
