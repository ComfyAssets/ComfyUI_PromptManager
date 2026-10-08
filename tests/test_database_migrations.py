"""Schema initialisation and migration behaviour of PromptModel.

Every PromptDatabase() construction used to re-run table creation and all
migrations, migrations swallowed their errors, and a rebuild that failed
half-way left a prompts_new table behind that made every later start fail.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.models import PromptModel
from database.operations import PromptDatabase

LEGACY_PROMPTS_TABLE = (
    "CREATE TABLE prompts (id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " text TEXT NOT NULL, created_at TIMESTAMP, updated_at TIMESTAMP,"
    " category TEXT, tags TEXT, rating INTEGER, notes TEXT,"
    " hash TEXT UNIQUE, workflow_name TEXT)"
)
LEGACY_IMAGES_TABLE = (
    "CREATE TABLE generated_images (id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " prompt_id INTEGER NOT NULL, image_path TEXT NOT NULL,"
    " filename TEXT NOT NULL, generation_time TIMESTAMP, file_size INTEGER,"
    " width INTEGER, height INTEGER, format TEXT, workflow_data TEXT,"
    " prompt_metadata TEXT, parameters TEXT,"
    " FOREIGN KEY (prompt_id) REFERENCES prompts(id) ON DELETE CASCADE)"
)


class MigrationTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "prompts.db")
        self.dbs = []
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for db in self.dbs:
            db.close()
        PromptModel.reset_schema_cache()
        for name in os.listdir(self.tmpdir):
            os.unlink(os.path.join(self.tmpdir, name))
        os.rmdir(self.tmpdir)

    def _open(self):
        db = PromptDatabase(self.path)
        self.dbs.append(db)
        return db

    def _legacy_db(self, statements):
        """A 3.0-era database file (workflow_name column, no usage columns)."""
        conn = sqlite3.connect(self.path)
        try:
            conn.execute(LEGACY_PROMPTS_TABLE)
            conn.execute(LEGACY_IMAGES_TABLE)
            for sql in statements:
                conn.execute(sql)
            conn.commit()
        finally:
            conn.close()

    def _columns(self, table):
        conn = sqlite3.connect(self.path)
        try:
            return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        finally:
            conn.close()


class TestSchemaInitOncePerProcess(MigrationTestCase):
    def _spy(self):
        original = PromptModel._create_tables
        calls = []

        def spy(model, conn):
            calls.append(model.db_path)
            return original(model, conn)

        return patch.object(PromptModel, "_create_tables", spy), calls

    def test_second_construction_on_same_path_skips_schema_init(self):
        patcher, calls = self._spy()
        with patcher:
            self._open()
            self._open()
            # Same file spelled differently still counts as the same path
            self.dbs.append(
                PromptDatabase(os.path.join(self.tmpdir, ".", "prompts.db"))
            )
        self.assertEqual(len(calls), 1)

    def test_reset_schema_cache_forces_init_again(self):
        patcher, calls = self._spy()
        with patcher:
            self._open()
            PromptModel.reset_schema_cache()
            self._open()
        self.assertEqual(len(calls), 2)

    def test_deleted_database_file_is_recreated_on_next_construction(self):
        db = self._open()
        db.close()
        os.unlink(self.path)
        for suffix in ("-wal", "-shm"):
            if os.path.exists(self.path + suffix):
                os.unlink(self.path + suffix)

        reopened = self._open()
        self.assertEqual(reopened.model.get_database_info()["total_prompts"], 0)
        self.assertIn("prompts", self._tables())

    def test_close_then_unlink_succeeds(self):
        db = self._open()
        db.save_prompt(text="x", prompt_hash="x")
        db.close()
        os.unlink(self.path)
        self.assertFalse(os.path.exists(self.path))

    def _tables(self):
        conn = sqlite3.connect(self.path)
        try:
            return [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            ]
        finally:
            conn.close()


class TestIdempotentMigrations(MigrationTestCase):
    def test_stale_prompts_new_from_a_failed_rebuild_does_not_block_startup(self):
        self._legacy_db(
            [
                "INSERT INTO prompts (text, hash, workflow_name) VALUES ('a', 'h1', 'wf')",
                "CREATE TABLE prompts_new (id INTEGER PRIMARY KEY, junk TEXT)",
                "INSERT INTO prompts_new (junk) VALUES ('leftover')",
            ]
        )

        db = self._open()

        self.assertNotIn("workflow_name", self._columns("prompts"))
        self.assertEqual(db.get_prompt_by_id(1)["text"], "a")
        self.assertNotIn("prompts_new", self._table_names())

    def test_rebuild_keeps_linked_images(self):
        # DROP TABLE prompts with foreign keys ON cascades into generated_images
        self._legacy_db(
            [
                "INSERT INTO prompts (text, hash, workflow_name) VALUES ('a', 'h1', 'wf')",
                "INSERT INTO generated_images (prompt_id, image_path, filename)"
                " VALUES (1, '/out/a.png', 'a.png')",
            ]
        )

        db = self._open()

        self.assertNotIn("workflow_name", self._columns("prompts"))
        self.assertEqual(len(db.get_prompt_images(1)), 1)

    def test_migration_error_is_logged_and_raised(self):
        # The legacy table has no CHECK on rating; the rebuilt one does, so the
        # copy into prompts_new fails inside the migration.
        self._legacy_db(
            [
                "INSERT INTO prompts (text, hash, rating, workflow_name)"
                " VALUES ('a', 'h1', 9, 'wf')",
            ]
        )

        with self.assertLogs("prompt_manager.database.models", level="ERROR") as logs:
            with self.assertRaises(sqlite3.Error):
                self._open()

        self.assertTrue(any("igration" in line for line in logs.output))
        # Nothing was half-applied: the old table is intact and usable
        self.assertIn("workflow_name", self._columns("prompts"))
        self.assertNotIn("prompts_new", self._table_names())

    def test_migrated_database_opens_cleanly_a_second_time(self):
        self._legacy_db(
            ["INSERT INTO prompts (text, hash, workflow_name) VALUES ('a', 'h1', 'wf')"]
        )
        self._open().close()
        PromptModel.reset_schema_cache()
        db = self._open()
        self.assertEqual(db.get_prompt_by_id(1)["text"], "a")

    def test_image_table_is_rebuilt_with_uniqueness_by_full_path(self):
        self._legacy_db(
            [
                "INSERT INTO prompts (text, hash, workflow_name) VALUES ('a', 'h1', 'wf')",
                # same basename in two folders: both must survive
                "INSERT INTO generated_images (prompt_id, image_path, filename)"
                " VALUES (1, '/out/2026-01-01/a.png', 'a.png')",
                "INSERT INTO generated_images (prompt_id, image_path, filename)"
                " VALUES (1, '/out/2026-01-02/a.png', 'a.png')",
                # two spellings of one file: only the newest row survives
                "INSERT INTO generated_images (prompt_id, image_path, filename)"
                " VALUES (1, '/out/x/../b.png', 'b.png')",
                "INSERT INTO generated_images (prompt_id, image_path, filename)"
                " VALUES (1, '/out/b.png', 'b.png')",
            ]
        )

        db = self._open()

        images = db.get_prompt_images(1)
        self.assertEqual(len(images), 3)
        self.assertIn("file_path", self._columns("generated_images"))
        self.assertEqual(
            sorted(i["file_path"] for i in images),
            sorted(
                os.path.normcase(os.path.normpath(p))
                for p in (
                    "/out/2026-01-01/a.png",
                    "/out/2026-01-02/a.png",
                    "/out/b.png",
                )
            ),
        )
        self.assertEqual([i["id"] for i in images if i["filename"] == "b.png"], [4])
        self.assertEqual(
            self._unique_columns("generated_images"), ["file_path", "prompt_id"]
        )

    def test_image_uniqueness_migration_is_not_undone_on_the_next_start(self):
        self._legacy_db(
            ["INSERT INTO prompts (text, hash, workflow_name) VALUES ('a', 'h1', 'wf')"]
        )
        self._open().close()
        PromptModel.reset_schema_cache()
        db = self._open()
        self.assertEqual(
            self._unique_columns("generated_images"), ["file_path", "prompt_id"]
        )
        self.assertTrue(db.link_image_to_prompt(1, "/out/1/a.png"))
        self.assertTrue(db.link_image_to_prompt(1, "/out/2/a.png"))
        self.assertEqual(len(db.get_prompt_images(1)), 2)

    def _unique_columns(self, table):
        conn = sqlite3.connect(self.path)
        try:
            columns = []
            for idx in conn.execute(f"PRAGMA index_list({table})"):
                if idx[2] == 1:
                    columns.extend(
                        c[2] for c in conn.execute(f"PRAGMA index_info({idx[1]})")
                    )
            return sorted(columns)
        finally:
            conn.close()

    def _table_names(self):
        conn = sqlite3.connect(self.path)
        try:
            return [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            ]
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
