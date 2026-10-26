"""Immutable run-owned output references, independent of mutable galleries."""

import sqlite3
from contextlib import ExitStack
from typing import Callable, ContextManager

from .run_attribution import notify_outputs_sealed

try:
    from ..utils.execution_history_events import RunFinish, encode_command
    from ..utils.execution_outputs import OutputBundle, validate_output_bundle
except ImportError:
    from utils.execution_history_events import RunFinish, encode_command
    from utils.execution_outputs import OutputBundle, validate_output_bundle


_REFERENCE_FIELDS = (
    "output_node_id",
    "image_ordinal",
    "folder_type",
    "subfolder",
    "filename",
    "path_style",
    "root_key",
)
_REFERENCE_COLUMNS = ",".join(_REFERENCE_FIELDS)


def _capture(conn: sqlite3.Connection, run_id: str) -> sqlite3.Row | None:
    capture = conn.execute(
        "SELECT state,reason,format_version,source FROM execution_output_capture "
        "WHERE run_id=?",
        (run_id,),
    ).fetchone()
    # Legacy callers may disable FK enforcement; never adopt orphan evidence.
    if capture is None:
        if conn.execute(
            "SELECT 1 FROM execution_run_outputs WHERE run_id=? LIMIT 1", (run_id,)
        ).fetchone():
            raise sqlite3.DatabaseError("Output references have no capture header")
    elif (
        conn.execute(
            "SELECT 1 FROM execution_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        is None
    ):
        raise sqlite3.DatabaseError("Output capture has no execution parent")
    return capture


def _references(conn: sqlite3.Connection, run_id: str) -> list[tuple]:
    return [
        tuple(row)
        for row in conn.execute(
            f"SELECT {_REFERENCE_COLUMNS} FROM execution_run_outputs "
            "WHERE run_id=? ORDER BY output_node_id,image_ordinal",
            (run_id,),
        )
    ]


class ExecutionOutputOperations:
    """Compose with ExecutionHistoryOperations on PromptDatabase."""

    def write_execution_outputs_for_finish(
        self,
        command: RunFinish,
        bundle: OutputBundle,
        *,
        generation_guard: Callable[[], ContextManager[bool]] | None = None,
    ) -> bool:
        encode_command(command)
        if type(command) is not RunFinish:
            raise ValueError("Expected execution finish command")
        validate_output_bundle(bundle)
        header = (bundle.state, bundle.reason, bundle.format_version, bundle.source)
        prepared = [
            tuple(getattr(reference, field) for field in _REFERENCE_FIELDS)
            for reference in bundle.references
        ]
        # Enter after BEGIN and retain the guard until COMMIT/ROLLBACK finishes.
        with ExitStack() as guards:
            with self._execution_transaction(command.storage_epoch) as conn:
                if generation_guard is not None and not guards.enter_context(
                    generation_guard()
                ):
                    return False
                parent = conn.execute(
                    "SELECT run_id FROM execution_runs WHERE session_id=? "
                    "AND queue_item_id=? AND storage_epoch=?",
                    (command.session_id, command.queue_item_id, command.storage_epoch),
                ).fetchone()
                if parent is None:
                    return False
                run_id = parent[0]
                capture = _capture(conn, run_id)
                if capture is not None:
                    if capture[0] != "conflicting" and (
                        tuple(capture) != header
                        or _references(conn, run_id) != prepared
                    ):
                        conn.execute(
                            "UPDATE execution_output_capture SET "
                            "state='conflicting',reason='conflicting' WHERE run_id=?",
                            (run_id,),
                        )
                    return True
                conn.execute(
                    "INSERT INTO execution_output_capture "
                    "(run_id,state,reason,format_version,source) VALUES(?,?,?,?,?)",
                    (run_id, *header),
                )
                conn.executemany(
                    f"INSERT INTO execution_run_outputs (run_id,{_REFERENCE_COLUMNS}) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    [(run_id, *reference) for reference in prepared],
                )
        # Committed: images waiting for this run may now be attributed (#342)
        notify_outputs_sealed(
            run_id, [dict(zip(_REFERENCE_FIELDS, row)) for row in prepared]
        )
        return True

    def get_execution_outputs(self, run_id: str) -> dict:
        """Read frozen evidence in one snapshot without resolving its locators."""
        conn = self.model.get_connection()
        own_transaction = not conn.in_transaction
        if own_transaction:
            conn.execute("BEGIN")
        try:
            capture = _capture(conn, run_id)
            if capture is None:
                return {
                    "state": "not_captured",
                    "reason": None,
                    "format_version": None,
                    "source": None,
                    "references": [],
                }
            references = []
            for row in _references(conn, run_id):
                reference = dict(zip(_REFERENCE_FIELDS, row))
                reference.update(
                    file_identity="unverified",
                    newly_generated="unknown",
                    context_membership="unknown",
                    transient=reference["folder_type"] == "temp",
                )
                references.append(reference)
            return {
                "state": capture[0],
                "reason": capture[1],
                "format_version": capture[2],
                "source": capture[3],
                "references": references,
            }
        finally:
            if own_transaction:
                conn.rollback()
