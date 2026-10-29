"""Fresh epoch validation survives ended snapshots and retired API owners."""

import importlib
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from database import restore_stage
from database.execution_history import HistoryEpochChanged, notify_history_restore
from tests import test_execution_history_database as history_tests


class HistoryReadFenceTests(unittest.TestCase):
    setUp = history_tests.HistoryDatabaseTests.setUp
    close_databases = history_tests.HistoryDatabaseTests.close_databases
    database = history_tests.HistoryDatabaseTests.database
    start = history_tests.HistoryDatabaseTests.start

    def fence(self, db):
        try:
            module = importlib.import_module("utils.history_read_fence")
        except ImportError:
            self.fail("HistoryReadFence must protect publication of history reads")
        fence = module.HistoryReadFence(db)
        self.addCleanup(fence.close)
        return fence

    def snapshot_file(self, db):
        path = str(Path(self.temp.name) / "source.db")
        self.assertTrue(db.model.backup_database(path))
        return path

    def test_final_epoch_is_fresh_after_snapshot_even_before_restore_notification(self):
        reader = self.database()
        writer = self.database()
        start = self.start(reader)
        reader.write_execution_start(start)
        source = self.snapshot_file(reader)
        fence = self.fence(reader)
        generation = fence.begin()
        with reader.recorded_history_snapshot() as epoch:
            original = reader.get_history_run(start.run_id)
            # The open snapshot keeps the WAL from being truncated; do not
            # sit out the full 30 s checkpoint wait for it (#487).
            with (
                patch("database.execution_history.notify_history_restore"),
                patch.object(restore_stage, "CHECKPOINT_BUSY_MS", 50),
            ):
                writer.model.restore_from_file(source)
            self.assertEqual(reader.get_execution_epoch(), epoch)
            self.assertEqual(original["run"]["storage_epoch"], epoch)
        with self.assertRaises(HistoryEpochChanged):
            fence.validate(epoch, generation)
        self.assertEqual(fence.begin(), generation)

    def test_restore_observer_rejects_same_epoch_even_when_storage_did_not_change(self):
        db = self.database()
        fence = self.fence(db)
        generation = fence.begin()
        epoch = db.get_execution_epoch()
        notify_history_restore(self.path)
        with self.assertRaises(HistoryEpochChanged):
            fence.validate(epoch, generation)
        self.assertGreater(fence.begin(), generation)
        fence.validate(epoch, fence.begin())

    def test_new_read_before_delayed_notification_can_return_coherent_new_epoch(self):
        db = self.database()
        writer = self.database()
        fence = self.fence(db)
        old_epoch = db.get_execution_epoch()
        source = self.snapshot_file(db)
        with patch("database.execution_history.notify_history_restore"):
            writer.model.restore_from_file(source)
        generation = fence.begin()
        with db.recorded_history_snapshot() as epoch:
            self.assertNotEqual(epoch, old_epoch)
            self.assertIsNone(
                db.get_history_run("11111111-1111-4111-8111-111111111111")
            )
        fence.validate(epoch, generation)
        notify_history_restore(self.path)
        with self.assertRaises(HistoryEpochChanged):
            fence.validate(epoch, generation)

    def test_restore_after_final_validation_is_later_staleness_not_relabeling(self):
        db = self.database()
        writer = self.database()
        start = self.start(db)
        db.write_execution_start(start)
        source = self.snapshot_file(db)
        fence = self.fence(db)
        generation = fence.begin()
        with db.recorded_history_snapshot() as epoch:
            detail = db.get_history_run(start.run_id)
        fence.validate(epoch, generation)
        writer.model.restore_from_file(source)
        self.assertEqual(detail["run"]["storage_epoch"], epoch)
        self.assertNotEqual(writer.get_execution_epoch(), epoch)

    def test_validation_does_not_reopen_retired_owner_and_closes_read_only_handle(self):
        db = self.database()
        epoch = db.get_execution_epoch()
        fence = self.fence(db)
        generation = fence.begin()
        db.close_all()
        module = importlib.import_module("utils.history_read_fence")
        connect = sqlite3.connect
        opened = []

        def tracked(*args, **kwargs):
            conn = connect(*args, **kwargs)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute(
                    "UPDATE execution_history_metadata SET storage_epoch=storage_epoch"
                )
            opened.append(conn)
            return conn

        with patch.object(
            db.model, "get_connection", side_effect=AssertionError("retired")
        ):
            with patch.object(module.sqlite3, "connect", side_effect=tracked):
                fence.validate(epoch, generation)
        self.assertEqual(len(opened), 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            opened[0].execute("SELECT 1")
        self.assertEqual(len(db.model._connections), 0)

    def test_observer_or_close_between_epoch_select_and_publish_rejects_read(self):
        db = self.database()
        module = importlib.import_module("utils.history_read_fence")
        original_connect = sqlite3.connect
        epoch = db.get_execution_epoch()
        database_path = self.path
        for close in (False, True):
            fence = self.fence(db)
            generation = fence.begin()

            class InterruptedConnection(sqlite3.Connection):
                def close(self):
                    super().close()
                    if close:
                        fence.close()
                    else:
                        notify_history_restore(database_path)

            def interrupted_connect(*args, **kwargs):
                return original_connect(*args, factory=InterruptedConnection, **kwargs)

            with patch.object(
                module.sqlite3, "connect", side_effect=interrupted_connect
            ):
                with self.assertRaises(sqlite3.Error if close else HistoryEpochChanged):
                    fence.validate(epoch, generation)

    def test_final_select_failure_always_closes_its_handle(self):
        db = self.database()
        fence = self.fence(db)
        module = importlib.import_module("utils.history_read_fence")
        epoch = db.get_execution_epoch()
        with db.model.get_connection() as conn:
            conn.execute("DROP TABLE execution_history_metadata")
        original_connect = sqlite3.connect
        opened = []

        def tracked(*args, **kwargs):
            conn = original_connect(*args, **kwargs)
            opened.append(conn)
            return conn

        with patch.object(module.sqlite3, "connect", side_effect=tracked):
            with self.assertRaises(sqlite3.DatabaseError):
                fence.validate(epoch, fence.begin())
        self.assertEqual(len(opened), 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            opened[0].execute("SELECT 1")

    def test_missing_metadata_and_connection_failure_are_not_empty_success(self):
        db = self.database()
        fence = self.fence(db)
        generation = fence.begin()
        epoch = db.get_execution_epoch()
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("DELETE FROM execution_history_metadata")
            conn.commit()
        with self.assertRaises(sqlite3.DatabaseError):
            fence.validate(epoch, generation)
        module = importlib.import_module("utils.history_read_fence")
        with patch.object(
            module.sqlite3, "connect", side_effect=OSError("private/path")
        ):
            with self.assertRaises(OSError):
                fence.validate(epoch, generation)

    def test_close_is_idempotent_unsubscribes_and_permanently_invalidates_reads(self):
        db = self.database()
        fence = self.fence(db)
        epoch = db.get_execution_epoch()
        generation = fence.begin()
        fence.close()
        fence.close()
        notify_history_restore(self.path)
        with self.assertRaises(sqlite3.Error):
            fence.begin()
        with self.assertRaises(sqlite3.Error):
            fence.validate(epoch, generation)
