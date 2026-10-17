"""Transactional attempt writes and process-local restore invalidation."""

import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Callable, Iterator

try:
    from ..utils.execution_history_events import (
        RunFinish,
        RunStart,
        classify_evidence,
        encode_command,
    )
    from ..utils.logging_config import get_logger
except ImportError:
    from utils.execution_history_events import (
        RunFinish,
        RunStart,
        classify_evidence,
        encode_command,
    )
    from utils.logging_config import get_logger


_logger = get_logger("prompt_manager.database.execution_history")
_restore_lock = threading.Lock()
_restore_observers: dict[str, dict[object, Callable[[], None]]] = {}


def _database_identity(db_path: str) -> str:
    return os.path.normcase(os.path.realpath(db_path))


def subscribe_history_restore(
    db_path: str, callback: Callable[[], None]
) -> Callable[[], None]:
    """Observe successful restores of the canonical database path."""
    identity = _database_identity(db_path)
    token = object()
    with _restore_lock:
        _restore_observers.setdefault(identity, {})[token] = callback

    def unsubscribe() -> None:
        with _restore_lock:
            observers = _restore_observers.get(identity)
            if observers is not None:
                observers.pop(token, None)
                if not observers:
                    _restore_observers.pop(identity, None)

    return unsubscribe


def notify_history_restore(db_path: str) -> None:
    """Notify outside the registry lock; observers cannot fail a restore."""
    with _restore_lock:
        callbacks = tuple(
            _restore_observers.get(_database_identity(db_path), {}).values()
        )
    for callback in callbacks:
        try:
            callback()
        except Exception:
            _logger.warning("Execution history restore observer failed", exc_info=True)


class HistoryEpochChanged(Exception):
    """A command belongs to storage replaced by a completed restore."""

    def __init__(self, storage_epoch: str):
        self.storage_epoch = storage_epoch
        super().__init__("Execution history storage epoch changed")


class ExecutionHistoryOperations:
    """Mixin using the calling thread's existing managed model connection."""

    def get_execution_epoch(self) -> str:
        row = (
            self.model.get_connection()
            .execute(
                "SELECT storage_epoch FROM execution_history_metadata "
                "WHERE singleton_id=1"
            )
            .fetchone()
        )
        if row is None:
            raise sqlite3.DatabaseError("Missing execution history metadata")
        return row[0]

    @contextmanager
    def _execution_transaction(
        self, expected_epoch: str
    ) -> Iterator[sqlite3.Connection]:
        conn = self.model.get_connection()
        if conn.in_transaction:
            raise sqlite3.ProgrammingError(
                "Execution history requires its own transaction"
            )
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        conn.execute("PRAGMA busy_timeout=250")
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                epoch = self.get_execution_epoch()
                if epoch != expected_epoch:
                    raise HistoryEpochChanged(epoch)
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        finally:
            conn.execute(f"PRAGMA busy_timeout={timeout}")

    def write_execution_start(self, command: RunStart) -> bool:
        encode_command(command)
        if type(command) is not RunStart:
            raise ValueError("Expected execution start command")
        with self._execution_transaction(command.storage_epoch) as conn:
            conn.execute(
                "INSERT INTO execution_runs "
                "(run_id,session_id,queue_item_id,job_id,storage_epoch,record_version,"
                "observed_dequeue_at,outcome,diagnostics_omitted) "
                "VALUES(?,?,?,?,?,1,?,'observed',0) "
                "ON CONFLICT(session_id,queue_item_id) DO NOTHING",
                (
                    command.run_id,
                    command.session_id,
                    command.queue_item_id,
                    command.job_id,
                    command.storage_epoch,
                    command.observed_dequeue_at,
                ),
            )
        return True

    def write_execution_finish(self, command: RunFinish) -> bool:
        encode_command(command)
        if type(command) is not RunFinish:
            raise ValueError("Expected execution finish command")
        with self._execution_transaction(command.storage_epoch) as conn:
            row = conn.execute(
                "SELECT * FROM execution_runs WHERE session_id=? AND queue_item_id=? "
                "AND storage_epoch=?",
                (command.session_id, command.queue_item_id, command.storage_epoch),
            ).fetchone()
            if row is None:
                return False
            completion = classify_evidence(row["job_id"], command.evidence)
            if row["outcome"] == "unknown":
                return True
            if row["outcome"] != "observed":
                contradictory = completion.reason == "conflicting_terminal" or (
                    completion.outcome in ("succeeded", "failed", "interrupted")
                    and completion.outcome != row["outcome"]
                )
                if contradictory:
                    conn.execute(
                        "UPDATE execution_runs SET outcome='unknown',"
                        "reason='conflicting_terminal' WHERE run_id=?",
                        (row["run_id"],),
                    )
                return True
            conn.execute(
                "UPDATE execution_runs SET observed_callback_at=?,"
                "executor_started_at=?,"
                "terminal_event_at=?,outcome=?,reason=?,diagnostics_omitted=?,"
                "node_id=?,"
                "node_type=?,exception_type=? WHERE run_id=?",
                (
                    command.observed_callback_at,
                    completion.executor_started_at,
                    completion.terminal_event_at,
                    completion.outcome,
                    completion.reason,
                    int(completion.diagnostics_omitted),
                    completion.node_id,
                    completion.node_type,
                    completion.exception_type,
                    row["run_id"],
                ),
            )
        return True

    def get_execution_run(self, run_id: str) -> dict | None:
        row = (
            self.model.get_connection()
            .execute("SELECT * FROM execution_runs WHERE run_id=?", (run_id,))
            .fetchone()
        )
        return dict(row) if row is not None else None
