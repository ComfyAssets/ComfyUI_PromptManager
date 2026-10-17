"""Pair schema installation and restore failures use disposable databases only."""

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

try:
    from tests.open_handles import assert_closed
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import assert_closed

from database.models import PromptModel


class PairSchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(assert_closed, self, self.temp.name)
        self.addCleanup(PromptModel.reset_schema_cache)
        self.models = []
        self.addCleanup(self.close_models)
        self.path = str(Path(self.temp.name) / "live.db")

    def close_models(self):
        for model in self.models:
            model.close_all()

    def model(self, path=None):
        model = PromptModel(path or self.path)
        self.models.append(model)
        return model

    def populate(self, model):
        with model.get_connection() as conn:
            conn.execute(
                "INSERT INTO prompts(id,text,hash,category,notes,run_count) VALUES"
                "(7,'positive','p','keep','notes',12)"
            )
            conn.execute("INSERT INTO prompts(id,text,hash) VALUES(9,'negative','n')")
            conn.execute(
                "INSERT INTO generated_images(id,prompt_id,image_path,filename,fil"
                "e_path) VALUES(4,7,'/tmp/image.png','image.png','/tmp/image.png')"
            )
            conn.execute("INSERT INTO tags(id,name) VALUES(3,'keep-tag')")
            conn.execute("INSERT INTO prompt_tags VALUES(7,3)")

    def legacy(self):
        model = self.model(str(Path(self.temp.name) / "legacy.db"))
        self.populate(model)
        with model.get_connection() as conn:
            for table in ("image_prompt_pairs", "prompt_pair_tags", "prompt_pairs"):
                conn.execute(f"DROP TABLE IF EXISTS {table}")
        return model

    def test_restore_scenarios_close_all_sqlite_handles(self):
        connections = []
        connect = sqlite3.connect

        def tracked_connect(*args, **kwargs):
            connection = connect(*args, **kwargs)
            connections.append(connection)
            return connection

        scenarios = (
            "test_pair_ddl_failure_rolls_back_and_retry_succeeds",
            "test_restore_legacy_wal_migrates_stage_and_preserves_safety_backup",
            "test_restore_migration_failure_keeps_original_and_source_untouched",
            "test_verification_rejects_partial_pair_schema_and_orphan_pair",
        )
        try:
            with patch.object(sqlite3, "connect", side_effect=tracked_connect):
                result = unittest.TestResult()
                unittest.TestSuite(type(self)(name) for name in scenarios).run(result)
            self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
            for index, connection in enumerate(connections):
                with self.subTest(connection=index):
                    with self.assertRaises(sqlite3.ProgrammingError):
                        connection.execute("SELECT 1")
        finally:
            for connection in connections:
                connection.close()

    def test_fresh_schema_constraints_and_foreign_keys(self):
        model = self.model()
        self.populate(model)
        with model.get_connection() as conn:
            conn.execute(
                "INSERT INTO prompt_pairs(id,positive_prompt_id,negative_prompt_id"
                ") VALUES(2,7,9)"
            )
            conn.execute("INSERT INTO prompt_pairs(positive_prompt_id) VALUES(7)")
            conn.execute("INSERT INTO prompt_pair_tags VALUES(2,3)")
            conn.execute(
                "INSERT INTO image_prompt_pairs(pair_id,image_id,provenance) VALUE"
                "S(2,4,'{}')"
            )
        for sql in (
            "INSERT INTO prompt_pairs(positive_prompt_id,negative_prompt_id) V"
            "ALUES(7,9)",
            "INSERT INTO prompt_pairs(positive_prompt_id) VALUES(7)",
            "INSERT INTO prompt_pairs(positive_prompt_id) VALUES(404)",
            "INSERT INTO prompt_pairs(positive_prompt_id,rating) VALUES(9,6)",
            "INSERT INTO prompt_pairs(positive_prompt_id,archived) VALUES(9,2)",
            "DELETE FROM prompts WHERE id=9",
            "DELETE FROM prompt_pairs WHERE id=2",
            "INSERT INTO image_prompt_pairs(pair_id,image_id,provenance) VALUE"
            "S(2,4,'{}')",
        ):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                with model.get_connection() as conn:
                    conn.execute(sql)
        with model.get_connection() as conn:
            conn.execute(
                "INSERT INTO prompt_pairs(positive_prompt_id,negative_prompt_id) V"
                "ALUES(9,7)"
            )
            conn.execute("DELETE FROM generated_images WHERE id=4")
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM image_prompt_pairs").fetchone()[0], 0
            )
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_legacy_upgrade_preserves_ids_metadata_images_counts_and_repeats(self):
        legacy = self.legacy()
        path = legacy.db_path
        legacy.close_all()
        PromptModel.reset_schema_cache()
        for _ in range(2):
            model = self.model(path)
            with model.get_connection() as conn:
                self.assertEqual(
                    tuple(
                        conn.execute(
                            "SELECT id,text,category,notes,run_count FROM prompts WHER"
                            "E id=7"
                        ).fetchone()
                    ),
                    (7, "positive", "keep", "notes", 12),
                )
                self.assertEqual(
                    conn.execute("SELECT id FROM generated_images").fetchone()[0], 4
                )
                self.assertEqual(
                    tuple(conn.execute("SELECT * FROM prompt_tags").fetchone()), (7, 3)
                )
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM prompt_pairs").fetchone()[0], 0
                )
            model.close_all()
            PromptModel.reset_schema_cache()

    def test_pair_ddl_failure_rolls_back_and_retry_succeeds(self):
        with patch.object(
            PromptModel,
            "_create_pair_indexes",
            side_effect=sqlite3.OperationalError("injected index failure"),
        ):
            with self.assertRaisesRegex(sqlite3.OperationalError, "injected"):
                self.model()
        with closing(sqlite3.connect(self.path)) as conn, conn:
            self.assertEqual(
                conn.execute(
                    "SELECT name FROM sqlite_master WHERE name IN ('prompt_pairs','pro"
                    "mpt_pair_tags','image_prompt_pairs')"
                ).fetchall(),
                [],
            )
        model = self.model()
        self.assertTrue(PromptModel.verify_database_file(model.db_path)[0])

    def test_verification_rejects_partial_pair_schema_and_orphan_pair(self):
        model = self.model()
        self.populate(model)
        with model.get_connection() as conn:
            conn.execute(
                "INSERT INTO prompt_pairs(positive_prompt_id,negative_prompt_id) V"
                "ALUES(7,9)"
            )
        model.close_all()
        with closing(sqlite3.connect(model.db_path)) as conn, conn:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("UPDATE prompt_pairs SET negative_prompt_id=404")
        ok, reason = PromptModel.verify_database_file(model.db_path)
        self.assertFalse(ok)
        self.assertIn("foreign key", reason.lower())
        with closing(sqlite3.connect(model.db_path)) as conn, conn:
            conn.execute("DROP TABLE prompt_pair_tags")
        ok, reason = PromptModel.verify_database_file(model.db_path)
        self.assertFalse(ok)
        self.assertIn("pair", reason.lower())

    def test_restore_migration_failure_keeps_original_and_source_untouched(self):
        live = self.model()
        self.populate(live)
        legacy = self.legacy()
        with live.get_connection() as conn:
            conn.execute("UPDATE prompts SET text='original live text' WHERE id=7")
        with patch.object(
            PromptModel,
            "_migrate_prompt_pairs",
            side_effect=sqlite3.OperationalError("migration failed"),
        ):
            with self.assertRaisesRegex(sqlite3.OperationalError, "migration failed"):
                live.restore_from_file(legacy.db_path)
        with live.get_connection() as conn:
            self.assertEqual(
                conn.execute("SELECT text FROM prompts WHERE id=7").fetchone()[0],
                "original live text",
            )
            self.assertEqual(
                conn.execute("SELECT run_count FROM prompts WHERE id=7").fetchone()[0],
                12,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM generated_images").fetchone()[0], 1
            )
        with closing(sqlite3.connect(legacy.db_path)) as conn, conn:
            self.assertIsNone(
                conn.execute(
                    "SELECT name FROM sqlite_master WHERE name='prompt_pairs'"
                ).fetchone()
            )

    def test_restore_legacy_wal_migrates_stage_and_preserves_safety_backup(self):
        live = self.model()
        with live.get_connection() as conn:
            conn.execute("INSERT INTO prompts(text,hash) VALUES('original','original')")
        legacy = self.legacy()
        with legacy.get_connection() as conn:
            conn.execute("INSERT INTO prompts(text,hash) VALUES('wal-only','wal-only')")
        self.assertGreater(os.path.getsize(legacy.db_path + "-wal"), 0)
        backup = live.restore_from_file(legacy.db_path)
        self.assertTrue(Path(backup).is_file())
        with closing(sqlite3.connect(backup)) as conn, conn:
            self.assertEqual(
                conn.execute("SELECT text FROM prompts").fetchall(), [("original",)]
            )
        with live.get_connection() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM prompts").fetchone()[0], 3
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM prompt_pairs").fetchone()[0], 0
            )
        self.assertTrue(PromptModel.verify_database_file(live.db_path)[0])

    def test_restore_staged_verification_failure_does_not_replace_live_pages(self):
        live = self.model()
        self.populate(live)
        source = self.legacy()
        with patch.object(
            PromptModel,
            "verify_database_file",
            side_effect=[(True, "ok"), (False, "staged validation failed")],
        ):
            with self.assertRaisesRegex(ValueError, "staged validation failed"):
                live.restore_from_file(source.db_path)
        with live.get_connection() as conn:
            self.assertEqual(
                conn.execute("SELECT run_count FROM prompts WHERE id=7").fetchone()[0],
                12,
            )

    def test_verification_rejects_missing_null_uniqueness(self):
        model = self.model()
        with model.get_connection() as conn:
            conn.execute("DROP INDEX idx_prompt_pairs_positive_only")
        ok, reason = PromptModel.verify_database_file(model.db_path)
        self.assertFalse(ok)
        self.assertIn("uniqueness", reason)

    def test_restore_round_trips_pair_metadata_tags_and_provenance(self):
        source = self.model(str(Path(self.temp.name) / "paired.db"))
        self.populate(source)
        with source.get_connection() as conn:
            conn.execute(
                "INSERT INTO prompt_pairs(id,positive_prompt_id,negative_prompt_id"
                ",title,notes,run_count,archived) VALUES(6,7,9,'pair title','pair "
                "notes',19,1)"
            )
            conn.execute("INSERT INTO prompt_pair_tags VALUES(6,3)")
            conn.execute(
                "INSERT INTO image_prompt_pairs(id,pair_id,image_id,provenance) VA"
                'LUES(8,6,4,\'{"execution":"kept"}\')'
            )
        live = self.model()
        live.restore_from_file(source.db_path)
        with live.get_connection() as conn:
            self.assertEqual(
                tuple(
                    conn.execute(
                        "SELECT id,title,notes,run_count,archived FROM prompt_pairs"
                    ).fetchone()
                ),
                (6, "pair title", "pair notes", 19, 1),
            )
            self.assertEqual(
                tuple(conn.execute("SELECT * FROM prompt_pair_tags").fetchone()), (6, 3)
            )
            self.assertEqual(
                conn.execute(
                    "SELECT provenance FROM image_prompt_pairs WHERE id=8"
                ).fetchone()[0],
                '{"execution":"kept"}',
            )

    def test_restore_rejects_trigger_before_running_migrations(self):
        live = self.model()
        source = self.legacy()
        with source.get_connection() as conn:
            conn.execute(
                "CREATE TRIGGER dangerous AFTER INSERT ON prompts BEGIN DELETE FRO"
                "M prompts; END"
            )
        with self.assertRaisesRegex(ValueError, "trigger"):
            live.restore_from_file(source.db_path)


if __name__ == "__main__":
    unittest.main()
