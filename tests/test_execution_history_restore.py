"""Restore publishes a fresh epoch and preserves historical attempt records."""

import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from database.models import PromptModel

try:
    from tests import test_execution_history_database as history_tests
except ImportError:
    import test_execution_history_database as history_tests


class HistoryRestoreTests(unittest.TestCase):
    setUp = history_tests.HistoryDatabaseTests.setUp
    close_databases = history_tests.HistoryDatabaseTests.close_databases
    database = history_tests.HistoryDatabaseTests.database
    start = history_tests.HistoryDatabaseTests.start
    finish = history_tests.HistoryDatabaseTests.finish
    count = history_tests.HistoryDatabaseTests.count

    def test_committed_wal_backup_includes_history_and_epoch(self):
        db = self.database()
        conn = db.model.get_connection()
        conn.execute("PRAGMA wal_autocheckpoint=0")
        start = self.start(db)
        db.write_execution_start(start)
        db.write_execution_finish(self.finish(start))
        row = db.get_execution_run(start.run_id)
        backup = str(Path(self.temp.name) / "backup.db")
        self.assertGreater(Path(self.path + "-wal").stat().st_size, 0)
        self.assertTrue(db.model.backup_database(backup))
        restored = self.database(backup)
        self.assertEqual(restored.get_execution_epoch(), start.storage_epoch)
        self.assertEqual(restored.get_execution_run(start.run_id), row)

    def test_restore_fences_stale_commands_preserves_rows_and_notifies_after_publish(
        self,
    ):
        from database.execution_history import (
            HistoryEpochChanged,
            subscribe_history_restore,
        )

        db = self.database()
        start = self.start(db)
        db.write_execution_start(start)
        backup = str(Path(self.temp.name) / "snapshot.db")
        self.assertTrue(db.model.backup_database(backup))
        snapshot = Path(backup).read_bytes()
        old_row = db.get_execution_run(start.run_id)
        notices = []
        unsubscribe = subscribe_history_restore(
            str(Path(self.temp.name) / "." / "live.db"),
            lambda: notices.append(db.get_execution_epoch()),
        )
        self.addCleanup(unsubscribe)
        for _ in range(2):
            previous = db.get_execution_epoch()
            db.model.restore_from_file(backup)
            epoch = db.get_execution_epoch()
            self.assertNotEqual(epoch, previous)
            self.assertNotEqual(epoch, start.storage_epoch)
            self.assertEqual(notices[-1], epoch)
            self.assertEqual(db.get_execution_run(start.run_id), old_row)
            for command, write in (
                (start, db.write_execution_start),
                (self.finish(start), db.write_execution_finish),
            ):
                with self.assertRaises(HistoryEpochChanged) as rejected:
                    write(command)
                self.assertEqual(rejected.exception.storage_epoch, epoch)
            self.assertEqual(db.get_execution_run(start.run_id), old_row)
            fresh = self.start(db, item=1)
            self.assertTrue(db.write_execution_start(fresh))
            self.assertTrue(db.write_execution_finish(self.finish(fresh)))
            self.assertEqual(Path(backup).read_bytes(), snapshot)
        unsubscribe()
        unsubscribe()
        db.model.restore_from_file(backup)
        self.assertEqual(len(notices), 2)

    def test_old_backup_migrates_with_no_runs_without_changing_source(self):
        source = str(Path(self.temp.name) / "old.db")
        with closing(sqlite3.connect(source)) as conn, conn:
            conn.execute(
                "CREATE TABLE prompts (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " text TEXT NOT NULL, created_at TIMESTAMP, updated_at TIMESTAMP,"
                " category TEXT, tags TEXT, rating INTEGER, notes TEXT,"
                " hash TEXT UNIQUE, workflow_name TEXT)"
            )
            conn.execute(
                "INSERT INTO prompts(id,text,created_at) "
                "VALUES(7,'old prompt','2020-01-01')"
            )
        original = Path(source).read_bytes()
        self.assertEqual(PromptModel.verify_database_file(source), (True, "ok"))
        db = self.database()
        previous = db.get_execution_epoch()
        db.model.restore_from_file(source)
        self.assertNotEqual(db.get_execution_epoch(), previous)
        self.assertEqual(self.count(db), 0)
        self.assertEqual(Path(source).read_bytes(), original)
        self.assertEqual(
            tuple(
                db.model.get_connection()
                .execute("SELECT id,text FROM prompts")
                .fetchone()
            ),
            (7, "old prompt"),
        )

    def test_failed_staging_rotation_keeps_live_epoch_and_never_notifies(self):
        from database.execution_history import subscribe_history_restore

        db = self.database()
        start = self.start(db)
        db.write_execution_start(start)
        source = str(Path(self.temp.name) / "source.db")
        db.model.backup_database(source)
        source_bytes = Path(source).read_bytes()
        notices = []
        self.addCleanup(
            subscribe_history_restore(self.path, lambda: notices.append(True))
        )
        with patch(
            "database.models.rotate_history_epoch",
            side_effect=sqlite3.OperationalError("injected staging failure"),
        ):
            with self.assertRaisesRegex(sqlite3.OperationalError, "injected staging"):
                db.model.restore_from_file(source)
        self.assertEqual(db.get_execution_epoch(), start.storage_epoch)
        self.assertEqual(Path(source).read_bytes(), source_bytes)
        self.assertEqual(notices, [])
        self.assertTrue(db.write_execution_finish(self.finish(start)))

    def test_malformed_history_rejected_before_replacement(self):
        from database.execution_history import subscribe_history_restore

        db = self.database()
        epoch = db.get_execution_epoch()
        source = str(Path(self.temp.name) / "bad.db")
        other = self.database(source)
        with other.model.get_connection() as conn:
            conn.execute("DROP INDEX idx_execution_runs_identity")
        notices = []
        self.addCleanup(
            subscribe_history_restore(self.path, lambda: notices.append(True))
        )
        with self.assertRaises(ValueError):
            db.model.restore_from_file(source)
        self.assertEqual(db.get_execution_epoch(), epoch)
        self.assertEqual(notices, [])

    def test_extra_unique_constraints_reject_restore_without_touching_live_or_backup(
        self,
    ):
        from database.execution_history import subscribe_history_restore

        db = self.database()
        start = self.start(db)
        db.write_execution_start(start)
        row = db.get_execution_run(start.run_id)
        with db.model.get_connection() as conn:
            conn.execute("INSERT INTO prompts(text) VALUES('live library')")
        notices = []
        self.addCleanup(
            subscribe_history_restore(self.path, lambda: notices.append(True))
        )
        for table_constraint in (False, True):
            with self.subTest(table_constraint=table_constraint):
                source = str(
                    Path(self.temp.name) / f"unique-backup-{table_constraint}.db"
                )
                self.assertTrue(db.model.backup_database(source))
                with closing(sqlite3.connect(source)) as conn, conn:
                    if table_constraint:
                        sql = conn.execute(
                            "SELECT sql FROM sqlite_master "
                            "WHERE name='execution_runs'"
                        ).fetchone()[0]
                        conn.execute("ALTER TABLE execution_runs RENAME TO old_runs")
                        conn.execute(sql[:-1] + ", UNIQUE(job_id))")
                        conn.execute(
                            "INSERT INTO execution_runs SELECT * FROM old_runs"
                        )
                        conn.execute("DROP TABLE old_runs")
                        conn.execute(
                            "CREATE UNIQUE INDEX idx_execution_runs_identity "
                            "ON execution_runs(session_id, queue_item_id)"
                        )
                        conn.execute(
                            "CREATE INDEX idx_execution_runs_observed "
                            "ON execution_runs(observed_dequeue_at, run_id)"
                        )
                    else:
                        conn.execute(
                            "CREATE UNIQUE INDEX unexpected_job_uniqueness "
                            "ON execution_runs(job_id)"
                        )
                original = Path(source).read_bytes()
                with self.assertRaisesRegex(ValueError, "uniqu"):
                    db.model.restore_from_file(source)
                self.assertEqual(db.get_execution_epoch(), start.storage_epoch)
                self.assertEqual(db.get_execution_run(start.run_id), row)
                self.assertEqual(self.count(db), 1)
                self.assertEqual(
                    db.model.get_connection()
                    .execute("SELECT text FROM prompts")
                    .fetchone()[0],
                    "live library",
                )
                self.assertEqual(Path(source).read_bytes(), original)
                self.assertEqual(notices, [])
        self.assertTrue(db.write_execution_start(self.start(db, item=1)))
        self.assertEqual(self.count(db), 2)

    def test_observer_failure_does_not_change_completed_restore(self):
        from database.execution_history import subscribe_history_restore

        db = self.database()
        source = str(Path(self.temp.name) / "snapshot.db")
        db.model.backup_database(source)
        original_epoch = db.get_execution_epoch()

        def broken():
            raise RuntimeError("observer failure")

        self.addCleanup(subscribe_history_restore(self.path, broken))
        notices = []
        self.addCleanup(
            subscribe_history_restore(self.path, lambda: notices.append(True))
        )
        with self.assertLogs(
            "prompt_manager.database.execution_history", level="WARNING"
        ):
            db.model.restore_from_file(source)
        self.assertNotEqual(db.get_execution_epoch(), original_epoch)
        self.assertEqual(notices, [True])


if __name__ == "__main__":
    unittest.main()
