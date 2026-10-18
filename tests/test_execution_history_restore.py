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

    def test_legacy_identifier_and_diagnostic_bytes_rejected_before_fetch(self):
        try:
            from tests.test_execution_context_schema import legacy, RUN
        except ImportError:
            from test_execution_context_schema import legacy, RUN
        from database.execution_history_schema import history_schema_error

        for table, column, prefix in (
            ("execution_history_metadata", "storage_epoch", RUN),
            *(
                ("execution_runs", column, RUN)
                for column in (
                    "run_id",
                    "session_id",
                    "job_id",
                    "storage_epoch",
                )
            ),
            ("execution_runs", "node_id", "x" * 128),
            ("execution_runs", "node_type", "x" * 128),
            ("execution_runs", "exception_type", "x" * 256),
        ):
            with self.subTest(table=table, column=column):
                with closing(sqlite3.connect(":memory:")) as conn:
                    legacy(conn)
                    malicious = prefix + "\0" + "x" * 1024
                    conn.execute(f"UPDATE {table} SET {column}=?", (malicious,))

                    def bounded_text(value):
                        self.assertNotEqual(value, malicious.encode())
                        return value.decode()

                    conn.text_factory = bounded_text
                    self.assertIsNotNone(history_schema_error(conn))

    def test_legacy_metadata_version_payload_is_rejected_before_fetch(self):
        try:
            from tests.test_execution_context_schema import legacy
        except ImportError:
            from test_execution_context_schema import legacy
        from database.execution_history_schema import history_schema_error

        for malicious in ("x" * 1024, b"x" * 1024):
            with self.subTest(kind=type(malicious).__name__):
                with closing(sqlite3.connect(":memory:")) as conn:
                    legacy(conn)
                    conn.execute("PRAGMA ignore_check_constraints=ON")
                    conn.execute(
                        "UPDATE execution_history_metadata SET schema_version=?",
                        (malicious,),
                    )

                    def bounded_row(cursor, row):
                        self.assertFalse(any(value == malicious for value in row))
                        return row

                    conn.row_factory = bounded_row
                    self.assertIsNotNone(history_schema_error(conn))

    def test_legacy_four_byte_diagnostics_validate_and_migrate(self):
        try:
            from tests.test_execution_context_schema import legacy
        except ImportError:
            from test_execution_context_schema import legacy
        from database.execution_history_schema import (
            history_schema_error,
            migrate_execution_history,
        )

        with closing(sqlite3.connect(":memory:")) as conn:
            legacy(conn)
            conn.execute(
                "UPDATE execution_runs SET node_id=?,node_type=?,exception_type=?",
                ("😀" * 128, "😀" * 128, "😀" * 256),
            )
            self.assertIsNone(history_schema_error(conn))
            migrate_execution_history(conn)
            self.assertIsNone(history_schema_error(conn, allow_missing=False))
            self.assertEqual(
                conn.execute(
                    "SELECT node_id,node_type,exception_type FROM " "execution_runs"
                ).fetchone(),
                ("😀" * 128, "😀" * 128, "😀" * 256),
            )

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
                "CREATE TABLE prompts (id INTEGER PRIMARY KEY AUTOINCREMENT, text "
                "TEXT NOT NULL, created_at TIMESTAMP, updated_at TIMESTAMP, "
                "category TEXT, tags TEXT, rating INTEGER, notes TEXT, hash TEXT "
                "UNIQUE, workflow_name TEXT)"
            )
            conn.execute(
                "INSERT INTO prompts(id,text,created_at) VALUES(7,'old "
                "prompt','2020-01-01')"
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

    def test_wal_and_old_backup_staging_leave_source_unchanged(self):
        from database.execution_context_schema import TABLE_COLUMNS

        source = str(Path(self.temp.name) / "v1.db")
        old = self.database(source)
        start = self.start(old)
        old.write_execution_start(start)
        with old.model.get_connection() as conn:
            conn.execute("INSERT INTO prompts(id,text,run_count) VALUES(7,'old',19)")
            for table in reversed(TABLE_COLUMNS):
                conn.execute(f"DROP TABLE {table}")
            sql = (
                conn.execute(
                    "SELECT sql FROM sqlite_master WHERE "
                    "name='execution_history_metadata'"
                )
                .fetchone()[0]
                .replace("schema_version = 2", "schema_version = 1")
            )
            conn.execute("DROP TABLE execution_history_metadata")
            conn.execute(sql)
            conn.execute(
                "INSERT INTO execution_history_metadata VALUES(1,1,?)",
                (start.storage_epoch,),
            )
        conn.execute("PRAGMA wal_autocheckpoint=0")
        with conn:
            conn.execute("UPDATE prompts SET run_count=31 WHERE id=7")
        source_bytes = Path(source).read_bytes()
        wal_bytes = Path(source + "-wal").read_bytes()
        self.assertEqual(PromptModel.verify_database_file(source), (True, "ok"))
        live = self.database()
        live.model.restore_from_file(source)
        self.assertEqual(Path(source).read_bytes(), source_bytes)
        self.assertEqual(Path(source + "-wal").read_bytes(), wal_bytes)
        self.assertEqual(live.get_execution_run(start.run_id)["record_version"], 1)
        self.assertEqual(
            live.model.get_connection()
            .execute("SELECT run_count FROM prompts WHERE id=7")
            .fetchone()[0],
            31,
        )
        self.assertEqual(
            live.model.get_connection()
            .execute("SELECT COUNT(*) FROM execution_run_contexts")
            .fetchone()[0],
            0,
        )
        self.assertEqual(
            conn.execute(
                "SELECT schema_version FROM execution_history_metadata"
            ).fetchone()[0],
            1,
        )

    def test_malformed_v2_context_restore_keeps_live_database_unchanged(self):
        live = self.database()
        start = self.start(live)
        live.write_execution_start(start)
        with live.model.get_connection() as conn:
            conn.execute("INSERT INTO prompts(text,run_count) VALUES('kept',19)")
        before = "\n".join(live.model.get_connection().iterdump())
        for mutation in (
            "DROP TABLE execution_context_capture",
            "CREATE UNIQUE INDEX bad_context_unique ON "
            "execution_context_texts(text_value)",
            "INSERT INTO execution_context_texts VALUES('"
            + "0" * 64
            + "','wrong fingerprint')",
        ):
            with self.subTest(mutation=mutation):
                source = str(Path(self.temp.name) / "bad-context.db")
                self.assertTrue(live.model.backup_database(source))
                with closing(sqlite3.connect(source)) as conn, conn:
                    conn.execute(mutation)
                original = Path(source).read_bytes()
                with self.assertRaises(ValueError):
                    live.model.restore_from_file(source)
                self.assertEqual(
                    "\n".join(live.model.get_connection().iterdump()), before
                )
                self.assertEqual(Path(source).read_bytes(), original)

    def test_missing_capture_header_rejects_backup_verification_and_restore(self):
        try:
            from tests.test_execution_context_database import bundle
        except ImportError:
            from test_execution_context_database import bundle

        live = self.database()
        start = self.start(live)
        live.write_execution_start(start)
        live.write_execution_contexts(start.run_id, start.storage_epoch, bundle())
        self.assertEqual(live.get_execution_contexts(start.run_id)["state"], "complete")
        before = "\n".join(live.model.get_connection().iterdump())
        source = str(Path(self.temp.name) / "missing-capture.db")
        self.assertTrue(live.model.backup_database(source))
        with closing(sqlite3.connect(source)) as conn, conn:
            conn.execute("DELETE FROM execution_context_capture")
        original = Path(source).read_bytes()
        valid, _ = PromptModel.verify_database_file(source)
        self.assertFalse(valid)
        with self.assertRaises(ValueError):
            live.model.restore_from_file(source)
        self.assertEqual("\n".join(live.model.get_connection().iterdump()), before)
        self.assertEqual(Path(source).read_bytes(), original)

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
                            "CREATE UNIQUE INDEX idx_execution_runs_identity ON "
                            "execution_runs(session_id, queue_item_id)"
                        )
                        conn.execute(
                            "CREATE INDEX idx_execution_runs_observed ON "
                            "execution_runs(observed_dequeue_at, run_id)"
                        )
                    else:
                        conn.execute(
                            "CREATE UNIQUE INDEX unexpected_job_uniqueness ON "
                            "execution_runs(job_id)"
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

    def test_restore_preserves_context_scalars_but_never_reepochs_hints(self):
        try:
            from tests.test_execution_context_database import bundle
        except ImportError:
            from test_execution_context_database import bundle
        from database.execution_history import HistoryEpochChanged

        db = self.database()
        with db.model.get_connection() as conn:
            conn.execute(
                "INSERT INTO prompts(id,text) VALUES(1,'positive'),(2,'negative')"
            )
            conn.execute(
                "INSERT INTO prompt_pairs(id,positive_prompt_id,negative_prompt_id) "
                "VALUES(7,1,2)"
            )
        start = self.start(db)
        db.write_execution_start(start)
        db.write_execution_contexts(start.run_id, start.storage_epoch, bundle())
        original = db.get_execution_contexts(start.run_id)
        backup = str(Path(self.temp.name) / "contexts.db")
        self.assertTrue(db.model.backup_database(backup))
        source_bytes = Path(backup).read_bytes()
        for _ in range(2):
            db.model.restore_from_file(backup)
            read = db.get_execution_contexts(start.run_id)
            expected = original["contexts"][0].copy()
            expected.update(
                positive_prompt_id=None, negative_prompt_id=None, pair_id=None
            )
            self.assertEqual(read["contexts"], [expected])
            self.assertEqual(read["state"], "complete")
            self.assertEqual(
                read["contexts"][0]["reference_epoch"], start.storage_epoch
            )
            with self.assertRaises(HistoryEpochChanged):
                db.write_execution_contexts(start.run_id, start.storage_epoch, bundle())
            self.assertFalse(
                db.write_execution_contexts(
                    start.run_id, db.get_execution_epoch(), bundle()
                )
            )
            self.assertEqual(db.get_execution_contexts(start.run_id), read)
            self.assertEqual(Path(backup).read_bytes(), source_bytes)


if __name__ == "__main__":
    unittest.main()
