"""Restore publication checks for snapshot-owned history reads."""

import os
from pathlib import Path
import sqlite3
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..database.operations import PromptDatabase

try:
    from ..database.execution_history import (
        HistoryEpochChanged,
        subscribe_history_restore,
    )
except ImportError:
    from database.execution_history import (
        HistoryEpochChanged,
        subscribe_history_restore,
    )


class HistoryReadFence:
    """Combine a fresh storage epoch with process-local restore notifications."""

    def __init__(self, db: "PromptDatabase") -> None:
        self._path = os.path.normcase(os.path.realpath(db.model.db_path))
        self._lock = threading.Lock()
        self._generation = 0
        self._closed = False
        self._unsubscribe = subscribe_history_restore(db.model.db_path, self._restored)

    def _restored(self) -> None:
        with self._lock:
            if not self._closed:
                self._generation += 1

    def _check(self, generation: int | None = None, epoch: str = "") -> int:
        if self._closed:
            raise sqlite3.OperationalError("History reads are closed")
        if generation is not None and generation != self._generation:
            raise HistoryEpochChanged(epoch)
        return self._generation

    def begin(self) -> int:
        with self._lock:
            return self._check()

    def validate(self, epoch: str, generation: int) -> None:
        """Validate after the caller's read transaction and response work ended."""
        with self._lock:
            self._check(generation, epoch)
        # An API restore retires its previous model. Never reopen that owner,
        # and never reuse the projection transaction's view of the epoch.
        conn = sqlite3.connect(
            Path(self._path).as_uri() + "?mode=ro", uri=True, timeout=0.25
        )
        try:
            row = conn.execute(
                "SELECT storage_epoch FROM execution_history_metadata "
                "WHERE singleton_id=1"
            ).fetchone()
            if row is None or not isinstance(row[0], str):
                raise sqlite3.DatabaseError("Missing history storage epoch")
            current_epoch = row[0]
        finally:
            conn.close()
        with self._lock:
            self._check(generation, epoch)
            if current_epoch != epoch:
                raise HistoryEpochChanged(current_epoch)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._generation += 1
        self._unsubscribe()
