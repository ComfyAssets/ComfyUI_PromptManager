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
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

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
        self.db.close_all()
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
        src.close_all()
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
    def test_incompatible_wal_restore_preserves_other_owner_and_allows_retry(
        self,
    ) -> None:
        from database.execution_history import subscribe_history_restore

        self._save(self.db, "keep live")
        live = self.db.model.get_connection()
        self.assertEqual(live.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(live.execute("PRAGMA page_size").fetchone()[0], 4096)
        epoch = self.db.get_execution_epoch()
        compatible = self._make_source_db(["compatible retry"])
        source_path = str(Path(self.tmpdir) / "large-pages.db")
        with closing(sqlite3.connect(source_path)) as conn:
            conn.execute("PRAGMA page_size=8192")
            conn.execute("VACUUM")
        source = PromptDatabase(source_path)
        try:
            self._save(source, "incompatible source")
            self.assertEqual(
                source.model.get_connection().execute("PRAGMA page_size").fetchone()[0],
                8192,
            )
        finally:
            source.close_all()
        original_source = Path(source_path).read_bytes()
        notices = []
        self.addCleanup(
            subscribe_history_restore(self.path, lambda: notices.append(True))
        )
        unrelated = PromptDatabase(str(Path(self.tmpdir) / "unrelated.db"))
        self.addCleanup(unrelated.close_all)
        self._save(unrelated, "unrelated")
        unrelated_connection = unrelated.model.get_connection()
        bystander = PromptDatabase(self.path)
        self.addCleanup(bystander.close_all)
        ready, release, refused = (threading.Event() for _ in range(3))
        results = {}

        def hold_reader_then_write() -> None:
            try:
                conn = bystander.model.get_connection()
                conn.execute("BEGIN")
                results["before"] = conn.execute("SELECT text FROM prompts").fetchone()[
                    0
                ]
                ready.set()
                if not release.wait(10):
                    raise TimeoutError("Restore did not release held reader")
                results["held"] = conn.execute("SELECT text FROM prompts").fetchone()[0]
                conn.rollback()
                # Never write through the original unsafe fallback during RED.
                if refused.is_set():
                    self._save(bystander, "other owner still writes")
                    results["after"] = conn.execute(
                        "SELECT text FROM prompts ORDER BY id"
                    ).fetchall()
            except Exception as exc:
                results["error"] = exc
            finally:
                ready.set()
                bystander.close()

        worker = threading.Thread(target=hold_reader_then_write)
        worker.start()
        try:
            self.assertTrue(ready.wait(10), "Held reader did not start")
            self.assertNotIn("error", results)
            with self.assertRaises(sqlite3.OperationalError):
                try:
                    self.db.model.restore_from_file(source_path)
                except sqlite3.OperationalError:
                    refused.set()
                    raise
        finally:
            release.set()
            worker.join(10)

        self.assertFalse(worker.is_alive(), "Held reader did not finish")
        self.assertNotIn("error", results)
        self.assertEqual(results["before"], "keep live")
        self.assertEqual(results["held"], "keep live")
        self.assertEqual(
            [row[0] for row in results["after"]],
            ["keep live", "other owner still writes"],
        )
        self.assertEqual(self.db.get_execution_epoch(), epoch)
        self.assertEqual(notices, [])
        self.assertEqual(_texts(self.path), ["keep live", "other owner still writes"])
        self._save(self.db, "restoring owner still writes")
        self.assertEqual(live.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(
            unrelated_connection.execute("SELECT text FROM prompts").fetchone()[0],
            "unrelated",
        )
        self.assertEqual(Path(source_path).read_bytes(), original_source)
        backups = list(Path(self.tmpdir).glob("prompts.db.backup_*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(_texts(str(backups[0])), ["keep live"])
        for path in (source_path, str(backups[0])):
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(
                    conn.execute("PRAGMA integrity_check").fetchone()[0], "ok"
                )

        self.db.model.restore_from_file(compatible)
        self.assertEqual(_texts(self.path), ["compatible retry"])
        self.assertNotEqual(self.db.get_execution_epoch(), epoch)
        self.assertEqual(notices, [True])

    def test_online_operational_error_preserves_live_data_and_closes_source(
        self,
    ) -> None:
        self._save(self.db, "keep live")
        source = self._make_source_db(["do not publish"])
        epoch = self.db.get_execution_epoch()
        live = self.db.model.get_connection()
        connect = sqlite3.connect
        failed_sources = []
        live_path = os.path.realpath(self.path)

        class FailingLiveBackup(sqlite3.Connection):
            def backup(self, target: sqlite3.Connection, *args, **kwargs) -> None:
                target_path = target.execute("PRAGMA database_list").fetchone()[2]
                if os.path.realpath(target_path) == live_path:
                    failed_sources.append(self)
                    raise sqlite3.OperationalError("injected live backup I/O failure")
                super().backup(target, *args, **kwargs)

        def connect_with_failure(*args, **kwargs) -> sqlite3.Connection:
            return connect(*args, **kwargs, factory=FailingLiveBackup)

        with patch("database.models.sqlite3.connect", side_effect=connect_with_failure):
            with self.assertRaises(sqlite3.OperationalError):
                self.db.model.restore_from_file(source)

        self.assertEqual(_texts(self.path), ["keep live"])
        self.assertEqual(self.db.get_execution_epoch(), epoch)
        self.assertEqual(
            live.execute("SELECT text FROM prompts").fetchone()[0], "keep live"
        )
        self.assertEqual(_texts(source), ["do not publish"])
        self.assertEqual(len(failed_sources), 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            failed_sources[0].execute("SELECT 1")

    def test_restore_replaces_content_and_backs_up_the_old_database(self):
        self._save(self.db, "old prompt")
        src = self._make_source_db(["restored one", "restored two"])

        backup_path = self.db.model.restore_from_file(src)

        self.assertTrue(backup_path.startswith(self.path + ".backup_"))
        self.assertTrue(os.path.exists(backup_path))
        self.assertEqual(_texts(backup_path), ["old prompt"])
        # The restore goes through SQLite's backup API into the live file, so
        # the WAL stays valid (and shared with other instances) rather than
        # being deleted under them; the content is what matters.
        self.assertEqual(_texts(self.path), ["restored one", "restored two"])

    def test_refusal_preserves_exception_identity_and_marks_completed_backup(
        self,
    ) -> None:
        self._save(self.db, "keep live")
        source = self._make_source_db(["replacement"])
        failure = sqlite3.OperationalError("online replacement refused")
        with patch.object(self.db.model, "_replace_contents_from", side_effect=failure):
            with self.assertRaises(sqlite3.OperationalError) as raised:
                self.db.model.restore_from_file(source)
        self.assertIs(raised.exception, failure)
        self.assertIs(
            getattr(raised.exception, "_prompt_manager_restore_backup_created", None),
            True,
        )
        self.assertEqual(_texts(self.path), ["keep live"])
        backups = list(Path(self.tmpdir).glob("prompts.db.backup_*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(_texts(str(backups[0])), ["keep live"])

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
        self.db.close_all()
        os.unlink(self.path)
        for suffix in ("-wal", "-shm"):
            if os.path.exists(self.path + suffix):
                os.unlink(self.path + suffix)

        self.assertEqual(self.db.model.restore_from_file(src), "")
        self.assertEqual(_texts(self.path), ["fresh"])


if __name__ == "__main__":
    unittest.main()


class TestConnectionsAcrossInstances(BackupTestCase):
    """Several PromptModel instances share one file (nodes, API, monitor)."""

    def test_close_all_closes_connections_opened_on_other_threads(self):
        opened = []

        def open_one():
            opened.append(self.db.model.get_connection())

        threads = [threading.Thread(target=open_one) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(opened), 3)

        self.db.close_all()

        for conn in opened:
            with self.assertRaises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")

    def test_restore_is_seen_by_another_instance_holding_an_open_connection(self):
        self.db.save_prompt(text="before", prompt_hash=generate_prompt_hash("before"))
        snapshot = os.path.join(self.tmpdir, "snapshot.db")
        other_db = PromptDatabase(snapshot)
        for i in range(3):
            other_db.save_prompt(
                text=f"after {i}", prompt_hash=generate_prompt_hash(f"after {i}")
            )
        other_db.close_all()

        # A second instance on the live file, with a connection kept open on
        # a worker thread for the whole restore (like the image monitor).
        bystander = PromptDatabase(self.path)
        ready, go, seen = threading.Event(), threading.Event(), []

        def hold_and_read():
            bystander.model.get_connection().execute("SELECT COUNT(*) FROM prompts")
            ready.set()
            go.wait(10)
            seen.append(len(bystander.search_prompts(limit=50)))

        worker = threading.Thread(target=hold_and_read)
        worker.start()
        self.assertTrue(ready.wait(10))
        try:
            self.db.model.restore_from_file(snapshot)
        finally:
            go.set()
            worker.join(10)
        bystander.close_all()

        self.assertEqual(seen, [3])
        self.assertEqual(sorted(_texts(self.path)), ["after 0", "after 1", "after 2"])
