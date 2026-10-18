"""Durable attempt identity, terminal reconciliation and additive migration."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from database.models import PromptModel
from database.operations import PromptDatabase
from utils.execution_history_events import make_finish, make_start

SESSION = "11111111-1111-4111-8111-111111111111"
JOB = "22222222-2222-4222-8222-222222222222"


class HistoryDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(PromptModel.reset_schema_cache)
        self.databases = []
        self.addCleanup(self.close_databases)
        self.path = str(Path(self.temp.name) / "live.db")

    def close_databases(self):
        for database in self.databases:
            database.close_all()

    def database(self, path=None):
        database = PromptDatabase(path or self.path)
        self.databases.append(database)
        return database

    def start(self, db, item=0, time=100):
        return make_start(
            SESSION, ((0, JOB, {}, {}), item), db.get_execution_epoch(), time
        )

    def finish(self, start, kind="execution_success", time=200, **fields):
        messages = [] if kind is None else [(kind, {"prompt_id": JOB, **fields})]
        return make_finish(
            start.session_id,
            start.queue_item_id,
            start.storage_epoch,
            SimpleNamespace(messages=messages),
            time,
        )

    def count(self, db):
        return (
            db.model.get_connection()
            .execute("SELECT COUNT(*) FROM execution_runs")
            .fetchone()[0]
        )

    def test_fresh_database_has_epoch_and_zero_runs(self):
        from database.execution_history_schema import history_schema_error

        db = self.database()
        self.assertEqual(str(UUID(db.get_execution_epoch())), db.get_execution_epoch())
        self.assertEqual(self.count(db), 0)
        self.assertIsNone(
            history_schema_error(db.model.get_connection(), allow_missing=False)
        )
        self.assertEqual(PromptModel.verify_database_file(self.path), (True, "ok"))

    def test_migration_preserves_all_existing_rows_and_counters(self):
        from database.execution_history_schema import migrate_execution_history

        db = self.database()
        conn = db.model.get_connection()
        with conn:
            conn.execute("INSERT INTO prompts(id,text,run_count) VALUES(7,'kept',19)")
            conn.execute(
                "INSERT INTO generated_images(id,prompt_id,image_path,filename,"
                "file_path) VALUES(4,7,'/tmp/a','a','/tmp/a')"
            )
            conn.execute(
                "INSERT INTO prompt_pairs(id,positive_prompt_id,run_count) "
                "VALUES(2,7,31)"
            )
            conn.execute(
                "INSERT INTO image_prompt_pairs(image_id,pair_id,provenance) "
                "VALUES(4,2,'{}')"
            )
        tables = ("prompts", "generated_images", "prompt_pairs", "image_prompt_pairs")
        before = {
            table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")]
            for table in tables
        }
        for pre_pair in (False, True):
            with self.subTest(pre_pair=pre_pair):
                with conn:
                    from database.execution_context_schema import TABLE_COLUMNS

                    for table in reversed(TABLE_COLUMNS):
                        conn.execute(f"DROP TABLE {table}")
                    conn.execute("DROP TABLE execution_runs")
                    conn.execute("DROP TABLE execution_history_metadata")
                if pre_pair:
                    with conn:
                        for table in (
                            "image_prompt_pairs",
                            "prompt_pair_tags",
                            "prompt_pairs",
                        ):
                            conn.execute(f"DROP TABLE {table}")
                    before = {
                        table: rows
                        for table, rows in before.items()
                        if table in ("prompts", "generated_images")
                    }
                db.close_all()
                PromptModel.reset_schema_cache()
                db = self.database()
                conn = db.model.get_connection()
                self.assertEqual(self.count(db), 0)
                for table, rows in before.items():
                    self.assertEqual(
                        [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")],
                        rows,
                    )
                epoch = db.get_execution_epoch()
                command = self.start(db)
                db.write_execution_start(command)
                row = db.get_execution_run(command.run_id)
                migrate_execution_history(conn)
                self.assertEqual(db.get_execution_epoch(), epoch)
                self.assertEqual(db.get_execution_run(command.run_id), row)
                self.assertEqual(
                    conn.execute("PRAGMA foreign_key_check").fetchall(), []
                )
                self.assertEqual(
                    conn.execute("PRAGMA integrity_check").fetchone()[0], "ok"
                )

    def test_failed_index_creation_rolls_back_both_tables_then_retry_succeeds(self):
        from database.execution_history_schema import migrate_execution_history

        class FailingConnection(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                if "CREATE" in sql.upper() and "INDEX" in sql.upper():
                    raise sqlite3.OperationalError("injected index failure")
                return super().execute(sql, parameters)

        with closing(sqlite3.connect(self.path, factory=FailingConnection)) as conn:
            with self.assertRaisesRegex(sqlite3.OperationalError, "injected"):
                migrate_execution_history(conn)
            self.assertEqual(
                conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall(),
                [],
            )
        with closing(sqlite3.connect(self.path)) as conn:
            migrate_execution_history(conn)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM execution_runs").fetchone()[0], 0
            )

    def test_replay_preserves_first_observation_and_public_job_is_nonunique(self):
        db = self.database()
        command = self.start(db)
        self.assertTrue(db.write_execution_start(command))
        self.assertTrue(
            db.write_execution_start(replace(command, observed_dequeue_at=999))
        )
        self.assertEqual(
            db.get_execution_run(command.run_id)["observed_dequeue_at"], 100
        )
        self.assertTrue(db.write_execution_start(self.start(db, item=1)))
        self.assertEqual(self.count(db), 2)
        self.assertEqual(db.get_execution_run(command.run_id)["outcome"], "observed")

    def test_unrelated_unique_conflicts_are_not_acknowledged_as_recorded(self):
        db = self.database()
        first = self.start(db)
        db.write_execution_start(first)
        with db.model.get_connection() as conn:
            conn.execute("CREATE UNIQUE INDEX unexpected_job ON execution_runs(job_id)")
        with self.assertRaises(sqlite3.IntegrityError):
            db.write_execution_start(self.start(db, item=1))
        self.assertEqual(self.count(db), 1)
        self.assertEqual(db.get_execution_run(first.run_id)["observed_dequeue_at"], 100)

    def test_primary_key_collision_for_another_attempt_is_not_a_replay(self):
        db = self.database()
        first = self.start(db)
        db.write_execution_start(first)
        with self.assertRaises(sqlite3.IntegrityError):
            db.write_execution_start(
                replace(self.start(db, item=1), run_id=first.run_id)
            )
        self.assertEqual(self.count(db), 1)

    def test_schema_rejects_extra_uniqueness_and_allows_nonunique_indexes(self):
        from database.execution_history_schema import history_schema_error

        for table, constraint in (
            ("execution_runs", "job_id"),
            ("execution_runs", "session_id, queue_item_id"),
            ("execution_history_metadata", "storage_epoch"),
        ):
            for table_constraint in (False, True):
                with self.subTest(
                    table=table,
                    table_constraint=table_constraint,
                    constraint=constraint,
                ):
                    path = str(
                        Path(self.temp.name) / f"unique-{len(self.databases)}.db"
                    )
                    db = self.database(path)
                    conn = db.model.get_connection()
                    with conn:
                        if table_constraint:
                            epoch = db.get_execution_epoch()
                            sql = conn.execute(
                                "SELECT sql FROM sqlite_master WHERE name=?", (table,)
                            ).fetchone()[0]
                            conn.execute(f"DROP TABLE {table}")
                            conn.execute(sql[:-1] + f", UNIQUE({constraint}))")
                            if table == "execution_runs":
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
                                    "INSERT INTO execution_history_metadata "
                                    "VALUES(1,2,?)",
                                    (epoch,),
                                )
                        else:
                            conn.execute(
                                f"CREATE UNIQUE INDEX unexpected_unique "
                                f"ON {table}({constraint})"
                            )
                    self.assertIsNotNone(history_schema_error(conn))
        db = self.database()
        with db.model.get_connection() as conn:
            conn.execute('CREATE INDEX "extra job lookup" ON execution_runs(job_id)')
            conn.execute(
                "CREATE INDEX extra_epoch_lookup "
                "ON execution_history_metadata(storage_epoch)"
            )
        self.assertIsNone(history_schema_error(conn))

    def test_absent_finish_never_fabricates_a_start(self):
        db = self.database()
        self.assertFalse(db.write_execution_finish(self.finish(self.start(db))))
        self.assertEqual(self.count(db), 0)
        self.assertIsNone(db.get_execution_run(self.start(db).run_id))

    def test_identical_terminal_replay_preserves_all_first_timestamps(self):
        db = self.database()
        start = self.start(db)
        db.write_execution_start(start)
        finish = self.finish(
            start, timestamp=150, node_id="4", exception_type="SafeType"
        )
        self.assertTrue(db.write_execution_finish(finish))
        original = db.get_execution_run(start.run_id)
        self.assertTrue(
            db.write_execution_finish(replace(finish, observed_callback_at=999))
        )
        self.assertEqual(db.get_execution_run(start.run_id), original)
        self.assertEqual(original["outcome"], "succeeded")
        self.assertEqual(original["observed_callback_at"], 200)
        self.assertEqual(original["terminal_event_at"], 150)
        self.assertIsNone(original["executor_started_at"])

    def test_conflicting_terminal_callbacks_are_irreversibly_unknown(self):
        for initial, next_kind in (
            ("execution_error", "execution_success"),
            ("execution_success", "execution_interrupted"),
        ):
            with self.subTest(initial=initial):
                db = self.database(str(Path(self.temp.name) / f"{initial}.db"))
                start = self.start(db)
                db.write_execution_start(start)
                db.write_execution_finish(self.finish(start, initial, timestamp=130))
                db.write_execution_finish(
                    self.finish(start, next_kind, time=300, timestamp=220)
                )
                row = db.get_execution_run(start.run_id)
                self.assertEqual(
                    (row["outcome"], row["reason"]), ("unknown", "conflicting_terminal")
                )
                db.write_execution_finish(self.finish(start, initial))
                self.assertEqual(db.get_execution_run(start.run_id), row)

    def test_empty_malformed_and_limited_duplicates_do_not_downgrade_proven_result(
        self,
    ):
        db = self.database()
        start = self.start(db)
        db.write_execution_start(start)
        db.write_execution_finish(self.finish(start, "execution_error", timestamp=180))
        original = db.get_execution_run(start.run_id)
        empty = self.finish(start, None)
        variants = (
            empty,
            replace(empty, evidence=replace(empty.evidence, invalid=True)),
            replace(empty, evidence=replace(empty.evidence, limited=True)),
        )
        for finish in variants:
            db.write_execution_finish(finish)
            self.assertEqual(db.get_execution_run(start.run_id), original)

    def test_conflicting_evidence_duplicate_downgrades_proven_result(self):
        db = self.database()
        start = self.start(db)
        db.write_execution_start(start)
        db.write_execution_finish(self.finish(start))
        finish = make_finish(
            SESSION,
            0,
            start.storage_epoch,
            SimpleNamespace(
                messages=[
                    ("execution_success", {"prompt_id": JOB}),
                    ("execution_error", {"prompt_id": JOB}),
                ]
            ),
            300,
        )
        db.write_execution_finish(finish)
        self.assertEqual(
            db.get_execution_run(start.run_id)["reason"], "conflicting_terminal"
        )

    def test_initial_unknown_is_never_promoted(self):
        db = self.database()
        start = self.start(db)
        db.write_execution_start(start)
        db.write_execution_finish(self.finish(start, None))
        row = db.get_execution_run(start.run_id)
        self.assertEqual(row["reason"], "missing_terminal")
        db.write_execution_finish(self.finish(start))
        self.assertEqual(db.get_execution_run(start.run_id), row)

    def test_writer_timeout_is_bounded_and_restored_for_existing_operations(self):
        db = self.database()
        start = self.start(db)
        conn = db.model.get_connection()
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        with closing(sqlite3.connect(self.path)) as locker:
            locker.execute("BEGIN IMMEDIATE")
            import time

            before = time.monotonic()
            with self.assertRaises(sqlite3.OperationalError):
                db.write_execution_start(start)
            self.assertLess(time.monotonic() - before, 1.5)
        self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], timeout)
        self.assertEqual(self.count(db), 0)
        self.assertTrue(db.write_execution_start(start))

    def test_sessions_keep_same_queue_item_and_public_job_separate(self):
        db = self.database()
        first = self.start(db)
        second = make_start(
            "33333333-3333-4333-8333-333333333333",
            ((0, JOB, {}, {}), 0),
            db.get_execution_epoch(),
            101,
        )
        db.write_execution_start(first)
        db.write_execution_start(second)
        db.write_execution_finish(self.finish(second, "execution_interrupted"))
        self.assertEqual(self.count(db), 2)
        self.assertEqual(db.get_execution_run(first.run_id)["outcome"], "observed")
        self.assertEqual(db.get_execution_run(second.run_id)["outcome"], "interrupted")

    def test_persisted_constraints_reject_invalid_identity_outcome_and_diagnostics(
        self,
    ):
        db = self.database()
        start = self.start(db)
        db.write_execution_start(start)
        conn = db.model.get_connection()
        for assignment in (
            "queue_item_id=-1",
            "job_id='not-a-uuid'",
            "record_version=2",
            "observed_dequeue_at=-1",
            "outcome='succeeded'",
            "reason='terminal_success'",
            "diagnostics_omitted=2",
            "node_id='" + "a" * 129 + "'",
            "exception_type='" + "a" * 257 + "'",
        ):
            with self.subTest(assignment=assignment):
                with self.assertRaises(sqlite3.IntegrityError), conn:
                    conn.execute(f"UPDATE execution_runs SET {assignment}")
        self.assertEqual(db.get_execution_run(start.run_id)["outcome"], "observed")
        with conn:
            conn.execute("PRAGMA ignore_check_constraints=ON")
            conn.execute("UPDATE execution_runs SET queue_item_id=-1")
        self.assertFalse(PromptModel.verify_database_file(self.path)[0])

    def test_bad_commands_cannot_bypass_transport_validation(self):
        db = self.database()
        start = self.start(db)
        with self.assertRaises(ValueError):
            db.write_execution_start(replace(start, observed_dequeue_at=-1))
        self.assertEqual(self.count(db), 0)

    def test_schema_verification_rejects_missing_tables_indexes_checks_and_versions(
        self,
    ):
        from database.execution_history_schema import history_schema_error

        for mutation in (
            "DROP TABLE execution_runs",
            "DROP TABLE execution_history_metadata",
            "DELETE FROM execution_history_metadata",
            "ALTER TABLE execution_runs ADD COLUMN extra_data TEXT",
            "DROP INDEX idx_execution_runs_identity",
            "DROP INDEX idx_execution_runs_observed",
            "DROP INDEX idx_execution_runs_identity; CREATE UNIQUE INDEX "
            "idx_execution_runs_identity ON execution_runs(job_id)",
            "DROP INDEX idx_execution_runs_observed; CREATE INDEX "
            "idx_execution_runs_observed ON "
            "execution_runs(observed_dequeue_at DESC,run_id)",
            "PRAGMA writable_schema=ON; UPDATE sqlite_master SET "
            "sql=replace(sql, 'queue_item_id >= 0', 'queue_item_id >= -1') "
            "WHERE name='execution_runs'; PRAGMA writable_schema=OFF",
            "PRAGMA ignore_check_constraints=ON; "
            "UPDATE execution_history_metadata SET schema_version=3",
            "PRAGMA writable_schema=ON; UPDATE sqlite_master SET "
            "sql=replace(sql, \"'observed'\", \"'OBSERVED'\") "
            "WHERE name='execution_runs'; PRAGMA writable_schema=OFF",
        ):
            with self.subTest(mutation=mutation):
                path = str(Path(self.temp.name) / f"schema-{len(self.databases)}.db")
                db = self.database(path)
                db.close_all()
                with closing(sqlite3.connect(path)) as conn, conn:
                    conn.executescript(mutation)
                with closing(sqlite3.connect(path)) as conn:
                    self.assertIsNotNone(history_schema_error(conn))
                self.assertFalse(PromptModel.verify_database_file(path)[0])


if __name__ == "__main__":
    unittest.main()
