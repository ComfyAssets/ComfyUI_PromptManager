"""ManagedConnection: never closed under the thread using it.

Closing a sqlite3 connection from one thread while another is inside a
statement on it is a use-after-free in SQLite ("Windows fatal exception:
access violation" in CI). A retired connection is closed at once when idle,
otherwise interrupted and closed by its owner as the call ends; it never
commits again.
"""

import contextlib
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.managed_connection import (  # noqa: E402
    ManagedConnection,
    ManagedCursor,
    await_closed,
    retire_connections,
)
from database.models import PromptModel  # noqa: E402
from tests.open_handles import assert_closed  # noqa: E402

WAIT = 30


class _Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "managed.db")
        with contextlib.closing(sqlite3.connect(self.path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE t(x INTEGER)")
            conn.execute("INSERT INTO t VALUES (0)")
            conn.commit()
        self.opened = []

    def tearDown(self):
        for conn in self.opened:
            conn.close()
        try:
            assert_closed(self, self._tmp.name)
        finally:
            self._tmp.cleanup()

    def connect(self, **kwargs):
        conn = sqlite3.connect(
            self.path,
            check_same_thread=False,
            timeout=WAIT,
            factory=ManagedConnection,
            **kwargs,
        )
        self.opened.append(conn)
        return conn

    def value(self):
        with contextlib.closing(sqlite3.connect(self.path)) as conn:
            return conn.execute("SELECT x FROM t").fetchone()[0]


class TestRetire(_Case):
    def test_an_idle_connection_retired_from_another_thread_closes_at_once(self):
        conn = self.connect()
        conn.execute("SELECT 1").fetchone()
        closed = []
        thread = threading.Thread(target=lambda: closed.append(conn.retire()))
        thread.start()
        thread.join(WAIT)
        self.assertEqual(closed, [True])
        self.assertTrue(conn.wait_closed(0))
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")
        self.assertTrue(conn.retire())  # retiring again is harmless

    def test_a_connection_in_use_is_closed_by_its_owner_when_the_call_ends(self):
        conn = self.connect()
        holder = sqlite3.connect(self.path, isolation_level=None)
        self.opened.append(holder)
        holder.execute("BEGIN IMMEDIATE")
        started = threading.Event()
        outcome = {}
        conn.set_trace_callback(
            lambda sql: started.set() if sql.startswith("UPDATE") else None
        )

        def write():
            try:
                with conn:
                    conn.execute("UPDATE t SET x = 1")
                outcome["ok"] = True
            except sqlite3.Error as error:
                outcome["error"] = error

        thread = threading.Thread(target=write)
        thread.start()
        self.assertTrue(started.wait(WAIT))
        # The owner is waiting on our lock inside its UPDATE: not closed here.
        self.assertFalse(conn.retire())
        self.assertFalse(conn.retire())
        self.assertFalse(conn.wait_closed(0))
        self.assertEqual(retire_connections([conn]), [conn])
        holder.execute("ROLLBACK")
        thread.join(WAIT)
        self.assertFalse(thread.is_alive())
        self.assertTrue(conn.wait_closed(WAIT))
        self.assertIsInstance(outcome.get("error"), sqlite3.Error)
        self.assertEqual(self.value(), 0)

    def test_a_write_retired_before_its_commit_is_rolled_back(self):
        conn = self.connect()
        conn.execute("UPDATE t SET x = 2")  # an implicit transaction is open
        thread = threading.Thread(target=conn.retire)
        thread.start()
        thread.join(WAIT)
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.commit()
        with self.assertRaises(sqlite3.ProgrammingError):
            with conn:
                pass
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.rollback()
        self.assertEqual(self.value(), 0)

    def test_close_is_retire(self):
        conn = self.connect()
        conn.close()
        self.assertTrue(conn.wait_closed(0))
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.commit()

    def test_plain_connections_are_closed_directly(self):
        plain = sqlite3.connect(self.path)
        self.assertEqual(retire_connections([plain]), [])
        with self.assertRaises(sqlite3.ProgrammingError):
            plain.execute("SELECT 1")

    def test_awaiting_a_connection_still_in_use_times_out_with_a_warning(self):
        conn = self.connect()
        conn._enter()  # its owner is inside a call
        self.assertEqual(retire_connections([conn]), [conn])
        logger = mock.Mock()
        self.assertFalse(await_closed([conn], 0, logger))
        logger.warning.assert_called_once()
        conn._leave()
        self.assertTrue(await_closed([conn], WAIT, logger))


class TestCalls(_Case):
    def test_every_cursor_call_works_and_is_refused_once_retired(self):
        conn = self.connect()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        self.assertIsInstance(cursor, ManagedCursor)
        conn.executemany("INSERT INTO t VALUES (?)", [(1,), (2,)])
        conn.executescript("INSERT INTO t VALUES (3);")
        self.assertEqual(
            [row["x"] for row in conn.execute("SELECT x FROM t ORDER BY x")],
            [0, 1, 2, 3],
        )
        cursor.execute("SELECT x FROM t ORDER BY x")
        self.assertEqual(cursor.fetchone()["x"], 0)
        self.assertEqual([r["x"] for r in cursor.fetchmany(2)], [1, 2])
        self.assertEqual([r["x"] for r in cursor.fetchall()], [3])
        cursor.executemany("UPDATE t SET x = x WHERE x = ?", [(1,)])
        cursor.executescript("SELECT 1;")
        cursor.close()
        conn.commit()
        conn.rollback()
        with conn:
            conn.execute("DELETE FROM t WHERE x = 3")
        with self.assertRaises(ZeroDivisionError):
            with conn:
                conn.execute("DELETE FROM t")
                raise ZeroDivisionError
        self.assertEqual(self.value(), 0)
        copy = sqlite3.connect(":memory:")
        self.opened.append(copy)
        conn.backup(copy)
        self.assertEqual(copy.execute("SELECT COUNT(*) FROM t").fetchone()[0], 3)

        reader = conn.execute("SELECT x FROM t")
        conn.retire()
        for call in (
            lambda: reader.fetchone(),
            lambda: reader.fetchmany(1),
            lambda: reader.fetchall(),
            lambda: next(reader),
            lambda: reader.execute("SELECT 1"),
            lambda: reader.executemany("SELECT ?", [(1,)]),
            lambda: reader.executescript("SELECT 1;"),
            lambda: conn.backup(copy),
        ):
            with self.assertRaises(sqlite3.ProgrammingError):
                call()
        # As with sqlite3's own cursors (the result varies by Python version).
        with contextlib.suppress(sqlite3.ProgrammingError):
            reader.close()


class TestModelConnections(_Case):
    def test_the_model_hands_out_managed_connections_and_closes_them(self):
        model = PromptModel(os.path.join(self._tmp.name, "model.db"))
        conn = model.get_connection()
        self.assertIsInstance(conn, ManagedConnection)
        model.close()
        self.assertTrue(conn.wait_closed(0))
        conn = model.get_connection()
        model.close_all()
        self.assertTrue(conn.wait_closed(0))


if __name__ == "__main__":
    unittest.main()
