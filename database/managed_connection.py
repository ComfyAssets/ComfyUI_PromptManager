"""A SQLite connection that is never closed under the thread using it.

``PromptModel`` gives each thread its own connection, but Move library and
``close_all`` must retire every thread's connection from one thread. Closing
a ``sqlite3.Connection`` while its owner is inside a statement (Python runs
``sqlite3_step`` without the GIL) frees memory the owner then reads: on
Windows the process dies with "access violation", elsewhere the owner sees
garbage such as a ``MemoryError``.

``ManagedConnection`` counts the calls its owner is inside. ``retire()`` is
safe from any thread and never raises: an idle connection is closed at once;
one in use is closed by its owner as the call ends. A retired connection
refuses new work (cursors, statements, commits) with the error a closed
connection raises.

What retiring a connection in use does and does not do:

- It calls ``sqlite3_interrupt`` (thread-safe). That ends a running
  statement at its next step, but it does not wake a statement that is
  waiting for another connection's lock: that one waits until the lock is
  released (or the busy timeout ends it), and only then is it interrupted.
  An interrupt that arrives between two statements, for example between the
  implicit ``BEGIN`` and the DML or between ``executemany`` rows, is lost.
- Writes are kept out of a file the process has switched away from because
  a retired connection never commits: legacy-mode DML runs inside a
  transaction that only ``commit()`` (refused) or ``close()`` (rolls back)
  ends. A statement that commits by itself is not covered: DDL or a PRAGMA
  write outside a transaction, or an explicit ``COMMIT`` passed to
  ``execute``/``executescript`` (``executescript`` also commits first, in C,
  on Python before 3.12). Managed connections must never be used that way
  for library writes.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
import weakref
from typing import Iterable, Iterator, List, Optional

import sqlite3

CLOSED_MESSAGE = "Cannot operate on a closed database."
# Rows fetched per step when a cursor is iterated: the connection is marked
# in use once per chunk instead of once per row (iteration costs about what
# a plain cursor costs). A loop that stops early leaves up to this many rows
# fetched; ``fetchone``/``fetchmany`` after a partial loop skip them.
ITER_CHUNK = 128


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

    def __iter__(self) -> Iterator:
        return self._chunks()

    def _chunks(self) -> Iterator:
        while True:
            rows = self.fetchmany(ITER_CHUNK)
            if not rows:
                return
            yield from rows

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
        # Guards _depth, _retired, _closing and _cursors. Re-entrant so that
        # code running in the closing thread while it holds the lock (a
        # cursor() during the cursor-set copy) is refused instead of hanging.
        self._use_lock = threading.RLock()
        self._depth = 0
        self._retired = False
        self._closing = False
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

    @contextlib.contextmanager
    def using(self):
        """Mark the connection in use for a call this class cannot wrap.

        For example as the *target* of another connection's ``backup()``.
        """
        self._enter()
        try:
            yield self
        finally:
            self._leave()

    def cursor(self, factory=None):
        with self._use_lock:
            if self._retired:
                raise sqlite3.ProgrammingError(CLOSED_MESSAGE)
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

    def create_function(self, *args, **kwargs):
        self._enter()
        try:
            return super().create_function(*args, **kwargs)
        finally:
            self._leave()

    @property
    def in_transaction(self) -> bool:
        self._enter()
        try:
            return super().in_transaction
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
        """Close now if idle, else as soon as the running call ends.

        Unlike ``sqlite3.Connection.close``, a call from another thread while
        the owner is inside a call returns before the file is closed. A caller
        that must know the file is closed (before renaming or deleting it)
        waits for ``wait_closed`` (``PromptModel.close_detached`` does).
        """
        self.retire()

    def retire(self) -> bool:
        """Refuse new work and close; True when the connection is closed already.

        Safe from any thread, and never raises: failures are logged.
        """
        try:
            with self._use_lock:
                if not self._retired:
                    self._retired = True
                    if self._depth:
                        # Ends the running statement (see the module docstring
                        # for what it does not end). The owner cannot start
                        # closing the connection while this lock is held.
                        self.interrupt()
                        return False
                elif self._depth:
                    return False
                else:
                    return self._released.wait(0)
        except Exception:
            self._log_exception("Error retiring database connection")
            return self._released.wait(0)
        self._close_now()
        return True

    def wait_closed(self, timeout: float) -> bool:
        return self._released.wait(timeout)

    def _close_now(self) -> None:
        # Only ever called with no call into the connection running, and a
        # retired connection refuses new calls and cursors: its cursors are
        # idle, and the set is copied under the lock that guards it.
        with self._use_lock:
            if self._closing:
                return  # another thread closes it and sets _released
            self._closing = True
            cursors = list(self._cursors)
        try:
            for cursor in cursors:
                with contextlib.suppress(sqlite3.Error):
                    sqlite3.Cursor.close(cursor)
            super().close()
        except Exception:
            self._log_exception("Error closing database connection")
        finally:
            self._released.set()

    def _log_exception(self, message: str) -> None:
        if self.close_logger is not None:
            self.close_logger.exception(message)


def retire_connections(
    connections: Iterable[sqlite3.Connection], logger=None
) -> List[ManagedConnection]:
    """Retire every connection; return those whose owner has yet to close them.

    Safe while holding a lock the owners wait on: nothing here waits for them.
    One connection failing to retire is logged and never skips the rest.
    """
    pending = []
    for conn in connections:
        try:
            if isinstance(conn, ManagedConnection):
                if logger is not None:
                    conn.close_logger = logger
                if not conn.retire():
                    pending.append(conn)
            else:
                conn.close()
        except Exception:
            if logger is not None:
                logger.exception("Error closing database connection")
    return pending


def await_closed(
    connections: Iterable[ManagedConnection], timeout: float, logger=None
) -> bool:
    """Wait until the owners closed ``connections`` (retired); False on timeout.

    A connection still open after ``timeout`` (its owner is blocked, for
    example on another process's lock after a lost interrupt) is logged and
    left to close when its call ends; a Move library then reports the old
    files as still in use instead of renaming them.
    """
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
