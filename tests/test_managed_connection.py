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
import weakref
import unittest.mock as mock

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
    def test_iteration_and_fetches_share_one_position(self) -> None:
        conn = self.connect()
        conn.executemany("INSERT INTO t VALUES (?)", [(x,) for x in range(1, 260)])
        cursor = conn.execute("SELECT x FROM t ORDER BY x")
        self.assertIs(iter(cursor), cursor)
        self.assertEqual(next(cursor), (0,))
        self.assertEqual(cursor.fetchone(), (1,))
        self.assertEqual(cursor.fetchmany(2), [(2,), (3,)])
        self.assertEqual(next(iter(cursor)), (4,))
        self.assertEqual(cursor.fetchall(), [(x,) for x in range(5, 260)])
        self.assertEqual(list(cursor), [])

    def test_a_partial_loop_can_resume_without_skipping_rows(self) -> None:
        cursor = self.connect().execute(
            "SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3"
        )
        for row in cursor:
            self.assertEqual(row, (1,))
            break
        self.assertEqual(list(cursor), [(2,), (3,)])

    def test_an_existing_iterator_refuses_reads_after_retire_or_close(self) -> None:
        for retire in (False, True):
            with self.subTest(retire=retire):
                conn = self.connect()
                cursor = conn.execute("SELECT 1 UNION ALL SELECT 2")
                iterator = iter(cursor)
                self.assertEqual(next(iterator), (1,))
                if retire:
                    conn.retire()
                else:
                    cursor.close()
                with self.assertRaises(sqlite3.ProgrammingError):
                    next(iterator)

    def test_buffered_fetchmany_matches_sqlite_sizes_and_validation(self) -> None:
        conn = self.connect()
        conn.executemany("INSERT INTO t VALUES (?)", [(x,) for x in range(1, 260)])
        conn.commit()
        with contextlib.closing(sqlite3.connect(self.path)) as plain:
            for size in (None, 0, 1, 2, 127, 128, 130, 300, -1):
                with self.subTest(size=size):
                    cursors = [
                        c.execute("SELECT x FROM t ORDER BY x") for c in (plain, conn)
                    ]
                    for cursor in cursors:
                        cursor.arraysize = 2
                        self.assertEqual(next(iter(cursor)), (0,))
                    results = []
                    for cursor in cursors:
                        try:
                            rows = (
                                cursor.fetchmany()
                                if size is None
                                else cursor.fetchmany(size)
                            )
                        except ValueError as error:  # Negative sizes on Python 3.14+.
                            rows = type(error)
                        results.append((rows, cursor.fetchall()))
                    self.assertEqual(*results)
        for args, kwargs in ((("bad",), {}), ((1, 2), {}), ((), {"unknown": 1})):
            cursor = conn.execute("SELECT 1 UNION ALL SELECT 2")
            self.assertEqual(next(iter(cursor)), (1,))
            with self.assertRaises(TypeError):
                cursor.fetchmany(*args, **kwargs)
            self.assertEqual(cursor.fetchone(), (2,))

    def test_reexecuting_a_cursor_matches_sqlite_result_lifetime(self) -> None:
        conn = self.connect()
        conn.row_factory = sqlite3.Row
        for method, args in (
            ("execute", ("SELECT 9 AS x",)),
            ("executemany", ("INSERT INTO t VALUES (?)", [(9,)])),
            ("executescript", ("SELECT 9 AS x;",)),
        ):
            with self.subTest(method=method):
                cursor = conn.execute("SELECT 1 AS x UNION ALL SELECT 2")
                self.assertEqual(next(iter(cursor))["x"], 1)
                self.assertIs(getattr(cursor, method)(*args), cursor)
                if method == "execute":
                    self.assertEqual(cursor.fetchone()["x"], 9)
                elif method == "executescript":
                    self.assertEqual(cursor.fetchone()["x"], 2)
                self.assertEqual(list(cursor), [])

    def test_failed_execution_matches_sqlite_result_lifetime(self) -> None:
        conn = self.connect()
        cases = (
            ("execute", (None,)),
            ("execute", ("invalid sql",)),
            ("execute", ("SELECT ?", ())),
            ("executemany", (None, [])),
            ("executemany", ("SELECT 9", [])),
            ("executescript", (None,)),
            ("executescript", ("invalid sql;",)),
        )
        with contextlib.closing(sqlite3.connect(self.path)) as plain:
            for method, args in cases:
                with self.subTest(method=method, args=args):
                    results = []
                    for connection in (plain, conn):
                        cursor = connection.execute("SELECT 1 UNION ALL SELECT 2")
                        self.assertEqual(next(iter(cursor)), (1,))
                        with self.assertRaises(
                            sqlite3.Error if isinstance(args[0], str) else TypeError
                        ):
                            getattr(cursor, method)(*args)
                        results.append(cursor.fetchall())
                    self.assertEqual(*results)

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


class _CursorOpenedDuringClose(weakref.WeakSet):
    """The cursor set of a connection being closed; a cursor opens mid-iteration.

    ``hook`` runs after the first cursor is yielded, where a thread switch
    could let the owner call ``conn.cursor()`` (Python 3.10-3.12 copy a
    WeakSet one item at a time). It runs in the closing thread itself, so
    the result does not depend on scheduling.
    """

    def __init__(self, items, hook):
        super().__init__(items)
        self.hook = hook

    def __iter__(self):
        hooked = False
        for item in super().__iter__():
            yield item
            if not hooked:
                hooked = True
                self.hook()


class TestReviewRound(_Case):
    """PR #321 review: I1 (cursor set race), M1 and M2 (unguarded paths)."""

    def test_a_cursor_opened_while_the_connection_closes_cannot_break_retire(self):
        conn = self.connect()
        kept = [conn.cursor() for _ in range(3)]
        refused = []

        def open_cursor():
            try:
                kept.append(conn.cursor())
            except sqlite3.ProgrammingError as error:
                refused.append(error)

        conn._cursors = _CursorOpenedDuringClose(conn._cursors, open_cursor)
        self.assertTrue(conn.retire())  # no "Set changed size during iteration"
        self.assertTrue(conn.wait_closed(0))
        self.assertEqual(len(refused), 1)
        self.assertEqual(len(kept), 3)

    def test_a_retired_connection_refuses_new_cursors_and_scripts(self):
        conn = self.connect()
        conn._enter()  # its owner is inside a call: retired, not yet closed
        try:
            self.assertFalse(conn.retire())
            for call in (
                conn.cursor,
                lambda: conn.executescript("CREATE TABLE late(x);"),
                lambda: conn.in_transaction,
                lambda: conn.create_function("f", 0, lambda: 1),
            ):
                with self.subTest(call=call):
                    with self.assertRaises(sqlite3.ProgrammingError):
                        call()
        finally:
            conn._leave()
        self.assertTrue(conn.wait_closed(0))
        with contextlib.closing(sqlite3.connect(self.path)) as check:
            tables = check.execute(
                "SELECT name FROM sqlite_master WHERE name='late'"
            ).fetchall()
        self.assertEqual(tables, [])

    def test_one_connection_failing_to_retire_does_not_skip_the_rest(self):
        class Broken(ManagedConnection):
            def retire(self):
                raise RuntimeError("retire failed")

        broken = sqlite3.connect(self.path, check_same_thread=False, factory=Broken)
        try:
            after = self.connect()
            logger = mock.Mock()
            pending = retire_connections([broken, after], logger)
            self.assertEqual(pending, [])
            self.assertTrue(after.wait_closed(0))
            logger.exception.assert_called_once()
        finally:
            ManagedConnection.retire(broken)

    def test_a_restore_marks_its_backup_target_in_use(self):
        model = PromptModel(os.path.join(self._tmp.name, "live.db"))
        try:
            self._restore_marks_its_target(model)
        finally:
            model.close_all()

    def _restore_marks_its_target(self, model):
        source_path = os.path.join(self._tmp.name, "source.db")
        PromptModel(source_path).close_all()
        target = model.get_connection()
        depths = []
        connect = sqlite3.connect

        class Source(sqlite3.Connection):
            def backup(self, into, *args, **kwargs):
                depths.append(into._depth)
                return super().backup(into, *args, **kwargs)

        def connect_source(*args, **kwargs):
            if "factory" in kwargs:
                return connect(*args, **kwargs)
            return connect(*args, **kwargs, factory=Source)

        with mock.patch("database.models.sqlite3.connect", connect_source):
            model._replace_contents_from(source_path)
        self.assertEqual(depths, [1])
        self.assertEqual(target._depth, 0)


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
