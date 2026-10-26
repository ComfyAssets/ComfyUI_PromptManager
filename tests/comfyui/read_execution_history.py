"""Bounded read-only SQLite inspection for the owned execution fixture."""

import argparse
from contextlib import closing
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


def read_links(database: str, filenames: list, texts: list) -> dict:
    """Each output file's linked prompt text (None: unlinked), and which texts
    are stored as prompts (#342)."""
    uri = Path(database).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=0.25)) as connection:
        connection.execute("PRAGMA query_only=ON")
        links = {}
        for name in filenames[:64]:
            rows = connection.execute(
                "SELECT p.text FROM generated_images g JOIN prompts p "
                "ON p.id = g.prompt_id WHERE g.filename = ? LIMIT 2",
                (name,),
            ).fetchall()
            values = [row[0] for row in rows]
            links[name] = values[0] if len(values) == 1 else None
            if len(values) > 1:
                links[name] = "<several links>"
        stored = {
            text: connection.execute(
                "SELECT 1 FROM prompts WHERE text = ? LIMIT 1", (text,)
            ).fetchone()
            is not None
            for text in texts[:64]
        }
    return {"links": links, "stored": stored}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database")
    parser.add_argument("job_id", nargs="?")
    parser.add_argument("--session-id")
    parser.add_argument("--links", nargs="*", default=None)
    parser.add_argument("--texts", nargs="*", default=[])
    arguments = parser.parse_args()
    if arguments.links is not None:
        print(
            json.dumps(read_links(arguments.database, arguments.links, arguments.texts))
        )
    else:
        print(
            json.dumps(
                read_runs(arguments.database, arguments.job_id, arguments.session_id)
            )
        )
