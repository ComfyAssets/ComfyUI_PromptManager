"""#443: the Library parent archive table, its migration and its validation."""

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from database.models import PromptModel
from database.operations import PromptDatabase
from database.prompt_archive import (
    TABLE,
    migrate_prompt_archive,
    prompt_archive_schema_error,
)
from utils.hashing import generate_prompt_hash


def _tables(conn):
    return {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


class TestPromptArchiveSchema(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "archive.db")
        self.db = PromptDatabase(self.path)

    def tearDown(self):
        self.db.close_all()
        self.temp.cleanup()

    def conn(self):
        return self.db.model.get_connection()

    def save(self, text):
        return self.db.save_prompt(text, prompt_hash=generate_prompt_hash(text))

    def test_startup_creates_the_table_and_it_validates(self):
        self.assertIn(TABLE, _tables(self.conn()))
        self.assertIsNone(prompt_archive_schema_error(self.conn(), allow_missing=False))

    def test_migration_is_idempotent(self):
        conn = self.conn()
        migrate_prompt_archive(conn)
        migrate_prompt_archive(conn)
        self.assertIsNone(prompt_archive_schema_error(conn, allow_missing=False))

    def test_a_missing_table_is_accepted_only_when_allowed(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        self.assertIsNone(prompt_archive_schema_error(conn))
        self.assertEqual(
            prompt_archive_schema_error(conn, allow_missing=False),
            "Missing prompt archive schema",
        )

    def test_deleting_a_prompt_drops_its_archive_row(self):
        pid = self.save("Archived then deleted")
        conn = self.conn()
        conn.execute(
            f"INSERT INTO {TABLE}(prompt_id, archived_at) VALUES (?, ?)",
            (pid, "2026-10-28T00:00:00+00:00"),
        )
        conn.commit()
        self.db.delete_prompt(pid)
        self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0], 0)

    def _broken(self, *statements):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.execute("CREATE TABLE prompts (id INTEGER PRIMARY KEY)")
        for statement in statements:
            conn.execute(statement)
        return conn

    def test_unsupported_structures_are_refused(self):
        good = (
            f"CREATE TABLE {TABLE} (prompt_id INTEGER PRIMARY KEY, "
            "archived_at TEXT NOT NULL, "
            "FOREIGN KEY(prompt_id) REFERENCES prompts(id) ON DELETE CASCADE)"
        )
        cases = {
            "other definition": (
                f"CREATE TABLE {TABLE} (prompt_id INTEGER PRIMARY KEY, "
                "archived_at TEXT)",
            ),
            "no cascade": (
                f"CREATE TABLE {TABLE} (prompt_id INTEGER PRIMARY KEY, "
                "archived_at TEXT NOT NULL, "
                "FOREIGN KEY(prompt_id) REFERENCES prompts(id))",
            ),
            "extra index": (good, f"CREATE INDEX x ON {TABLE}(archived_at)"),
            "trigger": (
                good,
                f"CREATE TRIGGER t AFTER INSERT ON {TABLE} BEGIN SELECT 1; END",
            ),
            "view name": (f"CREATE VIEW {TABLE} AS SELECT 1",),
        }
        for name, statements in cases.items():
            with self.subTest(name):
                self.assertIsNotNone(
                    prompt_archive_schema_error(self._broken(*statements))
                )

    def test_invalid_values_and_dangling_rows_are_refused(self):
        good = (
            f"CREATE TABLE {TABLE} (prompt_id INTEGER PRIMARY KEY, "
            "archived_at TEXT NOT NULL, "
            "FOREIGN KEY(prompt_id) REFERENCES prompts(id) ON DELETE CASCADE)"
        )
        for name, row in {
            "number": "(1, 5)",
            "blob": "(1, x'00')",
            "long": f"(1, '{'x' * 65}')",
            "dangling": "(99, '2026-10-28T00:00:00+00:00')",
        }.items():
            with self.subTest(name):
                conn = self._broken(good, "INSERT INTO prompts(id) VALUES (1)")
                conn.execute(f"INSERT INTO {TABLE} VALUES {row}")
                self.assertIsNotNone(prompt_archive_schema_error(conn))

    def test_a_refused_structure_stops_the_migration_without_changes(self):
        conn = self._broken(
            f"CREATE TABLE {TABLE} (prompt_id INTEGER PRIMARY KEY, archived_at TEXT)"
        )
        with self.assertRaises(sqlite3.DatabaseError):
            migrate_prompt_archive(conn)
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name=?", (TABLE,)
        ).fetchone()[0]
        self.assertNotIn("NOT NULL", sql)


class TestPromptArchiveBackups(unittest.TestCase):
    """Restore validation accepts an older backup and refuses a damaged table."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = PromptDatabase(str(self.root / "live.db"))

    def tearDown(self):
        self.db.close_all()
        self.temp.cleanup()

    def copy(self, name):
        target = self.root / name
        self.db.model.backup_database(str(target))
        return str(target)

    def test_a_backup_without_the_table_verifies_and_migrates(self):
        path = self.copy("old.db")
        with sqlite3.connect(path) as conn:
            conn.execute(f"DROP TABLE {TABLE}")
        conn.close()
        ok, reason = self.db.model.verify_database_file(path)
        self.assertTrue(ok, reason)
        model = PromptModel(path)
        try:
            self.assertIn(TABLE, _tables(model.get_connection()))
        finally:
            model.close_all()

    def test_a_backup_with_a_damaged_table_is_refused(self):
        path = self.copy("bad.db")
        with sqlite3.connect(path) as conn:
            conn.execute(f"DROP TABLE {TABLE}")
            conn.execute(f"CREATE TABLE {TABLE} (prompt_id INTEGER, archived_at TEXT)")
        conn.close()
        ok, reason = self.db.model.verify_database_file(path)
        self.assertFalse(ok)
        self.assertIn("archive", reason.lower())

    def test_an_archived_prompt_survives_a_backup_copy(self):
        pid = self.db.save_prompt("Kept", prompt_hash=generate_prompt_hash("Kept"))
        conn = self.db.model.get_connection()
        conn.execute(
            f"INSERT INTO {TABLE}(prompt_id, archived_at) VALUES (?, ?)",
            (pid, "2026-10-28T00:00:00+00:00"),
        )
        conn.commit()
        path = self.copy("copy.db")
        shutil.copy(path, path + ".2")
        with sqlite3.connect(path + ".2") as other:
            self.assertEqual(
                other.execute(f"SELECT prompt_id FROM {TABLE}").fetchall(), [(pid,)]
            )
        other.close()


if __name__ == "__main__":
    unittest.main()
