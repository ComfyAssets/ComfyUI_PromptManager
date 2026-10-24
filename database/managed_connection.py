"""A SQLite connection that is never closed under the thread using it.

``PromptModel`` gives each thread its own connection, but Move library and
``close_all`` must retire every thread's connection from one thread. Closing
a ``sqlite3.Connection`` while its owner is inside a statement (Python runs
``sqlite3_step`` without the GIL) frees memory the owner then reads: on
Windows the process dies with "access violation", elsewhere the owner sees
garbage such as a ``MemoryError``.

``ManagedConnection`` counts the calls its owner is inside. ``retire()`` is
safe from any thread: an idle connection is closed at once; one in use is
interrupted (``sqlite3_interrupt`` is thread-safe) and closed by its owner as
the call ends. A retired connection refuses new work with the error a closed
connection raises, and never commits: a write that started before the
retirement and waited for the database lock is rolled back, so it cannot
land in a file the process has switched away from.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
import weakref
from typing import Iterable, List, Optional

import sqlite3

CLOSED_MESSAGE = "Cannot operate on a closed database."


class ManagedCursor(sqlite3.Cursor):
    """A cursor whose calls into SQLite count as its connection being in use."""

    def execute(self, sql, parameters=(), /):
        conn = self.connection
        conn._enter()
        try:
            return super().execute(sql, parameters)
        finally:
            conn._leave()

    def executemany(self, sql, seq_of_parameters, /):
        conn = self.connection
        conn._enter()
        try:
            return super().executemany(sql, seq_of_parameters)
        finally:
            conn._leave()

    def executescript(self, sql_script, /):
        conn = self.connection
        conn._enter()
        try:
            return super().executescript(sql_script)
        finally:
            conn._leave()

    def fetchone(self):
        conn = self.connection
        conn._enter()
        try:
            return super().fetchone()
        finally:
            conn._leave()

    def fetchmany(self, *args, **kwargs):
        conn = self.connection
        conn._enter()
        try:
            return super().fetchmany(*args, **kwargs)
        finally:
            conn._leave()

    def fetchall(self):
        conn = self.connection
        conn._enter()
        try:
            return super().fetchall()
        finally:
            conn._leave()

    def __next__(self):
        conn = self.connection
        conn._enter()
        try:
            return super().__next__()
        finally:
            conn._leave()

    def close(self):
        conn = self.connection
        try:
            conn._enter()
        except sqlite3.ProgrammingError:
            return super().close()  # the connection is closed or closing
        try:
            return super().close()
        finally:
            conn._leave()


class ManagedConnection(sqlite3.Connection):
    """A connection only its owner closes while a call into it is running."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._use_lock = threading.Lock()
        self._depth = 0
        self._retired = False
        self._released = threading.Event()
        # Python before 3.12 keeps the file open after close() while a cursor
        # still holds a statement; these are closed first (see _close_now).
        self._cursors: "weakref.WeakSet[sqlite3.Cursor]" = weakref.WeakSet()
        self.close_logger: Optional[logging.Logger] = None

    # ── in use ───────────────────────────────────────────────────────

    def _enter(self) -> None:
        with self._use_lock:
            if self._retired:
                raise sqlite3.ProgrammingError(CLOSED_MESSAGE)
            self._depth += 1

    def _leave(self) -> None:
        with self._use_lock:
            self._depth -= 1
            close_now = self._retired and self._depth == 0
        if close_now:
            self._close_now()

    def cursor(self, factory=None):
        cursor = super().cursor(factory or ManagedCursor)
        self._cursors.add(cursor)
        return cursor

    def execute(self, sql, parameters=(), /):
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql, seq_of_parameters, /):
        return self.cursor().executemany(sql, seq_of_parameters)

    def executescript(self, sql_script, /):
        return self.cursor().executescript(sql_script)

    def commit(self):
        self._enter()
        try:
            return super().commit()
        finally:
            self._leave()

    def rollback(self):
        self._enter()  # a closed connection raises, as sqlite3's does
        try:
            return super().rollback()
        finally:
            self._leave()

    def backup(self, target, **kwargs):
        self._enter()
        try:
            return super().backup(target, **kwargs)
        finally:
            self._leave()

    def __exit__(self, exc_type, exc_value, traceback):
        # The C implementation calls its own commit/rollback, not these.
        if exc_type is None:
            try:
                self.commit()
            except BaseException:
                with contextlib.suppress(sqlite3.Error):
                    self.rollback()
                raise
        else:
            self.rollback()
        return False

    # ── closing ──────────────────────────────────────────────────────

    def close(self) -> None:
        """Close now if idle, else as soon as the running call ends (any thread)."""
        self.retire()

    def retire(self) -> bool:
        """Refuse new work and close; True when the connection is closed already."""
        with self._use_lock:
            if not self._retired:
                self._retired = True
                if self._depth:
                    # Aborts the running statement, or the one waiting for
                    # the lock as soon as it gets it. Thread-safe; the owner
                    # cannot close the connection while this lock is held.
                    self.interrupt()
                    return False
            elif self._depth:
                return False
            else:
                return self._released.wait(0)
        self._close_now()
        return True

    def wait_closed(self, timeout: float) -> bool:
        return self._released.wait(timeout)

    def _close_now(self) -> None:
        # Only ever called with no call into the connection running, and a
        # retired connection refuses new ones: its cursors are idle.
        for cursor in list(self._cursors):
            with contextlib.suppress(sqlite3.Error):
                sqlite3.Cursor.close(cursor)
        try:
            super().close()
        except sqlite3.Error as error:
            if self.close_logger is not None:
                self.close_logger.warning(f"Error closing database connection: {error}")
        finally:
            self._released.set()


def retire_connections(
    connections: Iterable[sqlite3.Connection], logger=None
) -> List[ManagedConnection]:
    """Retire every connection; return those whose owner has yet to close them.

    Safe while holding a lock the owners wait on: nothing here waits for them.
    """
    pending = []
    for conn in connections:
        if isinstance(conn, ManagedConnection):
            if logger is not None:
                conn.close_logger = logger
            if not conn.retire():
                pending.append(conn)
            continue
        try:
            conn.close()
        except sqlite3.Error as error:
            if logger is not None:
                logger.warning(f"Error closing database connection: {error}")
    return pending


def await_closed(
    connections: Iterable[ManagedConnection], timeout: float, logger=None
) -> bool:
    """Wait until the owners closed ``connections`` (retired); False on timeout."""
    deadline = time.monotonic() + timeout
    still_open = 0
    for conn in connections:
        if not conn.wait_closed(max(0.0, deadline - time.monotonic())):
            still_open += 1
    if still_open and logger is not None:
        logger.warning(
            f"{still_open} database connection(s) still in use after "
            f"{timeout:g}s; they close when their statement ends"
        )
    return not still_open
