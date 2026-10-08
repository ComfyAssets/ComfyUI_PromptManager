"""Backup, verification and restore of the prompts database.

The database runs in WAL mode, so committed rows can sit in prompts.db-wal
for a long time. A backup must include them, a restore must reject files
that are not healthy SQLite databases, and the live database must stay
usable afterwards.
"""

import os
import sqlite3
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.models import PromptModel
from database.operations import PromptDatabase
from utils.hashing import generate_prompt_hash


def _count_prompts(path):
    """Row count read through a brand-new connection (closed before return)."""
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT COUNT(*) FROM prompts").fetchone()[0]
    finally:
        conn.close()


def _texts(path):
    conn = sqlite3.connect(path)
    try:
        return sorted(r[0] for r in conn.execute("SELECT text FROM prompts"))
    finally:
        conn.close()


class BackupTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "prompts.db")
        self.db = PromptDatabase(self.path)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        self.db.close()
        for name in os.listdir(self.tmpdir):
            os.unlink(os.path.join(self.tmpdir, name))
        os.rmdir(self.tmpdir)

    def _save(self, db, text):
        return db.save_prompt(text=text, prompt_hash=generate_prompt_hash(text))

    def _make_source_db(self, texts):
        """A second database file holding the given prompts, fully closed."""
        src_path = os.path.join(self.tmpdir, "source.db")
        src = PromptDatabase(src_path)
        for text in texts:
            self._save(src, text)
        src.close()
        return src_path


class TestBackupDatabase(BackupTestCase):
    def test_backup_contains_rows_that_only_exist_in_the_wal(self):
        for n in range(5):
            self._save(self.db, f"wal prompt {n}")
        wal = self.path + "-wal"
        self.assertTrue(os.path.exists(wal) and os.path.getsize(wal) > 0)

        dst = os.path.join(self.tmpdir, "backup.db")
        self.assertTrue(self.db.model.backup_database(dst))

        self.assertEqual(_count_prompts(dst), 5)
        self.assertFalse(os.path.exists(dst + "-wal"))
        # The live database is untouched and still usable
        self.assertEqual(len(self.db.search_prompts(text="wal prompt")), 5)

    def test_backup_overwrites_a_stale_destination(self):
        self._save(self.db, "one")
        dst = os.path.join(self.tmpdir, "backup.db")
        with open(dst, "wb") as fh:
            fh.write(b"not a database at all")
        self.assertTrue(self.db.model.backup_database(dst))
        self.assertEqual(_count_prompts(dst), 1)

    def test_backup_onto_the_live_file_is_refused(self):
        self._save(self.db, "one")
        self.assertFalse(self.db.model.backup_database(self.path))
        self.assertEqual(len(self.db.search_prompts(text="one")), 1)

    def test_backup_to_an_unwritable_location_returns_false(self):
        dst = os.path.join(self.tmpdir, "missing", "dir", "backup.db")
        self.assertFalse(self.db.model.backup_database(dst))


class TestVerifyDatabaseFile(BackupTestCase):
    def test_text_file_is_rejected_without_raising(self):
        path = os.path.join(self.tmpdir, "notes.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("just some text, definitely not sqlite")
        ok, reason = PromptModel.verify_database_file(path)
        self.assertFalse(ok)
        self.assertIn("SQLite", reason)

    def test_missing_file_is_rejected(self):
        ok, reason = PromptModel.verify_database_file(
            os.path.join(self.tmpdir, "nope.db")
        )
        self.assertFalse(ok)
        self.assertTrue(reason)

    def test_database_without_prompts_table_is_rejected(self):
        path = os.path.join(self.tmpdir, "other.db")
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE something (id INTEGER)")
        conn.commit()
        conn.close()
        ok, reason = PromptModel.verify_database_file(path)
        self.assertFalse(ok)
        self.assertIn("prompts", reason)

    def test_prompts_table_missing_required_columns_is_rejected(self):
        path = os.path.join(self.tmpdir, "partial.db")
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE prompts (id INTEGER PRIMARY KEY, text TEXT)")
        conn.commit()
        conn.close()
        ok, reason = PromptModel.verify_database_file(path)
        self.assertFalse(ok)
        self.assertIn("created_at", reason)

    def test_corrupted_database_fails_integrity_check(self):
        path = self._make_source_db([f"prompt {n}" for n in range(40)])
        with open(path, "r+b") as fh:
            page_size = int.from_bytes(fh.read(18)[16:18], "big") or 4096
            fh.seek(page_size * 2)  # scribble over pages 3 and 4 entirely
            fh.write(b"\xff" * (page_size * 2))
        ok, reason = PromptModel.verify_database_file(path)
        self.assertFalse(ok)
        self.assertTrue(reason)

    def test_valid_database_passes(self):
        path = self._make_source_db(["a", "b"])
        ok, reason = PromptModel.verify_database_file(path)
        self.assertTrue(ok, reason)

    def test_verification_does_not_lock_or_modify_the_file(self):
        path = self._make_source_db(["a"])
        before = os.path.getsize(path)
        PromptModel.verify_database_file(path)
        os.unlink(path)  # would fail on Windows if a connection were left open
        self.assertEqual(before, before)


class TestRestoreFromFile(BackupTestCase):
    def test_restore_replaces_content_and_backs_up_the_old_database(self):
        self._save(self.db, "old prompt")
        src = self._make_source_db(["restored one", "restored two"])

        backup_path = self.db.model.restore_from_file(src)

        self.assertTrue(backup_path.startswith(self.path + ".backup_"))
        self.assertTrue(os.path.exists(backup_path))
        self.assertEqual(_texts(backup_path), ["old prompt"])
        self.assertFalse(os.path.exists(self.path + "-wal"))
        self.assertFalse(os.path.exists(self.path + "-shm"))
        self.assertEqual(_texts(self.path), ["restored one", "restored two"])

    def test_database_is_usable_from_every_thread_after_restore(self):
        self._save(self.db, "old prompt")
        src = self._make_source_db(["restored"])

        ready = threading.Event()
        proceed = threading.Event()
        results = {}

        def worker():
            self.db.search_prompts(text="old")  # opens this thread's connection
            ready.set()
            proceed.wait(5)
            try:
                results["texts"] = [p["text"] for p in self.db.search_prompts()]
            except Exception as exc:  # noqa: BLE001 - reported through results
                results["error"] = exc
            finally:
                self.db.close()

        thread = threading.Thread(target=worker)
        thread.start()
        ready.wait(5)

        self.db.model.restore_from_file(src)
        proceed.set()
        thread.join(5)

        self.assertNotIn("error", results)
        self.assertEqual(results["texts"], ["restored"])
        self.assertEqual([p["text"] for p in self.db.search_prompts()], ["restored"])
        self.assertEqual(self._save(self.db, "after restore"), 2)

    def test_restore_applies_migrations_to_an_old_schema(self):
        src = os.path.join(self.tmpdir, "legacy.db")
        conn = sqlite3.connect(src)
        # The 3.0-era schema: workflow_name still present, no usage columns
        conn.execute(
            "CREATE TABLE prompts (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " text TEXT NOT NULL, created_at TIMESTAMP, updated_at TIMESTAMP,"
            " category TEXT, tags TEXT, rating INTEGER, notes TEXT,"
            " hash TEXT UNIQUE, workflow_name TEXT)"
        )
        conn.execute(
            "INSERT INTO prompts (text, created_at, tags, workflow_name)"
            " VALUES ('legacy', '2024-01-01T00:00:00', '[\"old\"]', 'wf')"
        )
        conn.commit()
        conn.close()

        self.db.model.restore_from_file(src)

        prompt = self.db.get_prompt_by_id(1)
        self.assertEqual(prompt["text"], "legacy")
        self.assertEqual(prompt["tags"], ["old"])
        self.assertEqual(prompt["run_count"], 1)
        self.assertNotIn("workflow_name", prompt)
        self.assertEqual(self.db.get_prompt_images(1), [])

    def test_restore_without_a_live_database_returns_empty_backup_path(self):
        src = self._make_source_db(["fresh"])
        self.db.close()
        os.unlink(self.path)
        for suffix in ("-wal", "-shm"):
            if os.path.exists(self.path + suffix):
                os.unlink(self.path + suffix)

        self.assertEqual(self.db.model.restore_from_file(src), "")
        self.assertEqual(_texts(self.path), ["fresh"])


if __name__ == "__main__":
    unittest.main()
