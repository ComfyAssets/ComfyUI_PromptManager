"""Bounded read-only SQLite inspection for the owned execution fixture."""

import argparse
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from database.execution_contexts import ExecutionContextOperations  # noqa: E402
from database.execution_history import ExecutionHistoryOperations  # noqa: E402
from database.execution_outputs import ExecutionOutputOperations  # noqa: E402


class _ReadOnlyHistory(
    ExecutionContextOperations, ExecutionHistoryOperations, ExecutionOutputOperations
):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.model = SimpleNamespace(get_connection=lambda: connection)


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
        reader = _ReadOnlyHistory(connection)
        captures = {
            row["run_id"]: reader.get_execution_contexts(row["run_id"]) for row in rows
        }
        usage = {}
        snapshot_ids = {}
        for run_id, capture in captures.items():
            usage[run_id] = []
            snapshot_ids[run_id] = []
            for context in capture["contexts"]:
                for role in ("positive", "negative"):
                    prompt_id = context[f"{role}_prompt_id"]
                    if prompt_id is not None:
                        count = connection.execute(
                            "SELECT run_count FROM prompts WHERE id=?", (prompt_id,)
                        ).fetchone()[0]
                        usage[run_id].append({"role": role, "run_count": count})
                pair_id = context["pair_id"]
                if pair_id is not None:
                    count = connection.execute(
                        "SELECT run_count FROM prompt_pairs WHERE id=?", (pair_id,)
                    ).fetchone()[0]
                    usage[run_id].append({"role": "pair", "run_count": count})
                snapshot_ids[run_id].append(
                    dict(
                        connection.execute(
                            "SELECT positive_snapshot_id,negative_snapshot_id "
                            "FROM execution_contexts WHERE context_id=?",
                            (context["context_id"],),
                        ).fetchone()
                    )
                )
        return {
            "schemaAvailable": True,
            "rows": [dict(row) for row in rows],
            "captures": captures,
            "usage": usage,
            "snapshotIds": snapshot_ids,
            "outputs": {
                row["run_id"]: reader.get_execution_outputs(row["run_id"])
                for row in rows
            },
        }


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
