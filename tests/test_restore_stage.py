"""A restore is staged beside the library, then published (#249 System S8).

``PromptModel.stage_restore`` copies an upload into a stage folder in the
library's own folder, migrates, repairs and verifies it there, and never
touches the live database or the source. ``publish_staged`` re-checks the
staged bytes, keeps a uniquely named safety backup and copies the stage over
the live pages, then truncates the WAL. ``restore_from_file`` is the two in a
row, with the behavior it always had.
"""

import datetime
import hashlib
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
import zlib
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import restore_stage, safety_backup  # noqa: E402
from database import workflow_documents as wd  # noqa: E402
from database.models import PromptModel  # noqa: E402
from database.operations import PromptDatabase  # noqa: E402
from utils.hashing import generate_prompt_hash  # noqa: E402

try:
    from tests.open_handles import assert_closed  # noqa: E402
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import assert_closed  # noqa: E402

WAIT = 60  # a bound only a hung test reaches


def _texts(path):
    with closing(sqlite3.connect(path)) as conn:
        return sorted(r[0] for r in conn.execute("SELECT text FROM prompts"))


def _digests(path):
    """sha256 of the file and each sidecar that exists."""
    return {
        suffix: hashlib.sha256(Path(path + suffix).read_bytes()).hexdigest()
        for suffix in ("", "-wal", "-shm", "-journal")
        if os.path.exists(path + suffix)
    }


class StageTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.library = os.path.join(self.tmpdir, "library")
        os.mkdir(self.library)
        self.uploads = os.path.join(self.tmpdir, "uploads")
        os.mkdir(self.uploads)
        self.path = os.path.join(self.library, "prompts.db")
        self.db = PromptDatabase(self.path)

    def tearDown(self):
        PromptModel.close_all_instances()
        assert_closed(self, self.tmpdir)
        for root, dirs, files in os.walk(self.tmpdir, topdown=False):
            for name in files:
                os.unlink(os.path.join(root, name))
            for name in dirs:
                os.rmdir(os.path.join(root, name))
        os.rmdir(self.tmpdir)

    def save(self, db, text):
        return db.save_prompt(text=text, prompt_hash=generate_prompt_hash(text))

    def source(self, texts, name="source.db", journal="wal"):
        path = os.path.join(self.uploads, name)
        src = PromptDatabase(path)
        for text in texts:
            self.save(src, text)
        src.close_all()
        if journal != "wal":
            with closing(sqlite3.connect(path)) as conn:
                conn.execute(f"PRAGMA journal_mode={journal}").fetchone()
        return path

    def stage_dirs(self):
        return sorted(
            name
            for name in os.listdir(self.library)
            if name.startswith(restore_stage.STAGE_PREFIX)
        )

    def backups(self):
        return sorted(
            name
            for name in os.listdir(self.library)
            if name.startswith("prompts.db.backup_")
        )


class TestStageAndPublish(StageTestCase):
    def test_restore_from_file_unchanged_via_stage_publish(self):
        self.save(self.db, "old prompt")
        src = self.source(["restored one", "restored two"])
        model = self.db.model
        calls = []
        real_stage, real_publish = model.stage_restore, model.publish_staged

        def stage(path):
            calls.append("stage")
            return real_stage(path)

        def publish(staged):
            calls.append("publish")
            return real_publish(staged)

        with (
            patch.object(model, "stage_restore", stage),
            patch.object(model, "publish_staged", publish),
        ):
            backup_path = model.restore_from_file(src)

        self.assertEqual(calls, ["stage", "publish"])
        self.assertTrue(backup_path.startswith(self.path + ".backup_"))
        self.assertEqual(_texts(backup_path), ["old prompt"])
        self.assertEqual(_texts(self.path), ["restored one", "restored two"])
        self.assertEqual(self.stage_dirs(), [])

    def test_stage_never_touches_live_or_source(self):
        self.save(self.db, "live")
        self.db.model.get_connection()  # the live file stays open, WAL and all
        src = self.source(["restored"])
        live_before = _digests(self.path)
        source_before = _digests(src)[""]

        staged = self.db.model.stage_restore(src)
        try:
            self.assertEqual(_digests(self.path), live_before)
            # Read-only opens of a WAL-flagged file add empty sidecars, as
            # they always did; the source's bytes never change.
            self.assertEqual(_digests(src)[""], source_before)
            self.assertEqual(self.backups(), [])
            self.assertEqual(_texts(self.path), ["live"])
        finally:
            staged.discard()

    def test_stage_lives_in_db_directory_and_is_removed(self):
        src = self.source(["restored"])
        staged = self.db.model.stage_restore(src)
        stage_dir = os.path.dirname(staged.path)
        self.assertEqual(os.path.dirname(stage_dir), self.library)
        self.assertEqual(self.stage_dirs(), [os.path.basename(stage_dir)])
        # One self-contained file: no sidecar of an open or WAL database.
        self.assertEqual(os.listdir(stage_dir), [os.path.basename(staged.path)])
        staged.discard()
        self.assertEqual(self.stage_dirs(), [])
        staged.discard()  # a second discard is harmless

        # A failed stage removes its folder too.
        bad = os.path.join(self.uploads, "notes.db")
        Path(bad).write_bytes(b"not a database")
        with self.assertRaises(ValueError):
            self.db.model.stage_restore(bad)
        self.assertEqual(self.stage_dirs(), [])

    def test_staged_handles_closed_before_cleanup(self):
        src = self.source(["restored"])
        staged = self.db.model.stage_restore(src)
        assert_closed(self, os.path.dirname(staged.path))
        self.db.model.publish_staged(staged)
        assert_closed(self, os.path.dirname(staged.path))
        staged.discard()
        self.assertFalse(os.path.exists(os.path.dirname(staged.path)))

    def test_safety_backup_name_unique_within_one_second(self):
        self.save(self.db, "first")
        frozen = datetime.datetime(2026, 10, 25, 9, 30, 0, 123456)
        names = []
        with patch.object(safety_backup, "_now", return_value=frozen):
            for text in ("second", "third"):
                staged = self.db.model.stage_restore(self.source([text], f"{text}.db"))
                try:
                    names.append(os.path.basename(self.db.model.publish_staged(staged)))
                finally:
                    staged.discard()
        self.assertEqual(
            names,
            [
                "prompts.db.backup_20261025_093000_123456",
                "prompts.db.backup_20261025_093000_123456_1",
            ],
        )
        self.assertEqual(_texts(os.path.join(self.library, names[1])), ["second"])
        for name in names:  # standalone: no WAL flag, no sidecars when read
            with closing(sqlite3.connect(os.path.join(self.library, name))) as conn:
                self.assertEqual(
                    conn.execute("PRAGMA journal_mode").fetchone()[0], "delete"
                )
            if os.name != "nt":
                mode = os.stat(os.path.join(self.library, name)).st_mode & 0o777
                self.assertEqual(mode, 0o600)

    def test_publish_binds_the_staged_bytes(self):
        self.save(self.db, "live")
        staged = self.db.model.stage_restore(self.source(["restored"]))
        try:
            with closing(sqlite3.connect(staged.path)) as conn:
                conn.execute("INSERT INTO prompts (text, hash) VALUES ('sneaked', 'x')")
                conn.commit()
            with self.assertRaises(restore_stage.StageChanged):
                self.db.model.publish_staged(staged)
        finally:
            staged.discard()
        self.assertEqual(_texts(self.path), ["live"])
        self.assertEqual(self.backups(), [])

    def test_summary_schema_and_digest(self):
        prompt_id = self.save(self.db, "unused")
        src = self.source(["a", "b"])
        staged = self.db.model.stage_restore(src)
        try:
            self.assertEqual(staged.summary["prompts"], 2)
            self.assertEqual(staged.summary["positives"], 2)
            for key in ("pairs", "archived_pairs", "image_links", "runs"):
                self.assertEqual(staged.summary[key], 0, key)
            self.assertEqual(staged.summary["saved_views"], 0)
            self.assertEqual(staged.summary["collections"], 0)
            self.assertEqual(staged.schema, {"migrated": False, "library_state": True})
            self.assertEqual(
                staged.digest,
                "sha256:" + hashlib.sha256(Path(staged.path).read_bytes()).hexdigest(),
            )
            self.assertEqual(staged.size, os.path.getsize(staged.path))
            live = restore_stage.library_summary(self.db.model.get_connection())
            self.assertEqual(live["prompts"], 1)
            self.assertTrue(prompt_id)
        finally:
            staged.discard()


class TestCollectionsSummary(StageTestCase):
    """#331: collections join the review's summary and the live fingerprint."""

    def add_collection(self, db, name):
        with db.model.get_connection() as conn:
            conn.execute(
                "INSERT INTO image_collections (name, name_key, created_at) "
                "VALUES (?, ?, '2026-10-27T00:00:00+00:00')",
                (name, name.casefold()),
            )

    def test_summary_counts_collections(self):
        src_path = os.path.join(self.uploads, "with-collections.db")
        src = PromptDatabase(src_path)
        try:
            self.save(src, "kept")
            self.add_collection(src, "First")
            self.add_collection(src, "Second")
        finally:
            src.close_all()
        staged = self.db.model.stage_restore(src_path)
        try:
            self.assertEqual(staged.summary["collections"], 2)
        finally:
            staged.discard()
        self.assertIn("collections", restore_stage.SUMMARY_KEYS)

    def test_adding_a_collection_changes_the_fingerprint(self):
        conn = self.db.model.get_connection()
        summary, before = restore_stage.live_fingerprint(conn)
        self.assertEqual(summary["collections"], 0)
        self.add_collection(self.db, "After the review")
        summary, after = restore_stage.live_fingerprint(conn)
        self.assertEqual(summary["collections"], 1)
        self.assertNotEqual(after, before)
        # A delete and a re-add with the same count still changes it (rowid).
        with self.db.model.get_connection() as write:
            write.execute("DELETE FROM image_collections")
        self.add_collection(self.db, "Replacement")
        self.assertNotEqual(restore_stage.live_fingerprint(conn)[1], after)


class TestOlderBackups(StageTestCase):
    """Rollback-journal and older-schema backups restore exactly as before."""

    def test_rollback_journal_backup_restores(self):
        self.save(self.db, "live")
        src = self.source(["from a delete-mode file"], journal="delete")
        with closing(sqlite3.connect(src)) as conn:
            self.assertEqual(
                conn.execute("PRAGMA journal_mode").fetchone()[0], "delete"
            )
        before = _digests(src)
        self.db.model.restore_from_file(src)
        self.assertEqual(_texts(self.path), ["from a delete-mode file"])
        self.assertEqual(_digests(src), before)

    def test_older_schema_backup_is_migrated(self):
        src = os.path.join(self.uploads, "legacy.db")
        with closing(sqlite3.connect(src)) as conn:
            conn.execute(
                "CREATE TABLE prompts (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " text TEXT NOT NULL, created_at TIMESTAMP, updated_at TIMESTAMP,"
                " category TEXT, tags TEXT, rating INTEGER, notes TEXT,"
                " hash TEXT UNIQUE, workflow_name TEXT)"
            )
            conn.execute("INSERT INTO prompts (text) VALUES ('legacy')")
            conn.commit()
        staged = self.db.model.stage_restore(src)
        try:
            self.assertEqual(staged.schema, {"migrated": True, "library_state": False})
            self.assertEqual(staged.summary["prompts"], 1)
            self.db.model.publish_staged(staged)
        finally:
            staged.discard()
        self.assertEqual(_texts(self.path), ["legacy"])
        ok, reason = PromptModel.verify_database_file(
            self.path, require_library_state=True
        )
        self.assertTrue(ok, reason)


class TestWalAfterRestore(StageTestCase):
    """Carry-over 4: the live WAL is checkpointed and truncated after a restore."""

    def test_wal_is_truncated_after_restore(self):
        src = self.source(["restored"])
        with closing(sqlite3.connect(src)) as conn:
            conn.execute("CREATE TABLE padding(b BLOB)")
            conn.execute("INSERT INTO padding VALUES (zeroblob(?))", (4 * 2**20,))
            conn.commit()
        self.save(self.db, "live")
        # Another thread's idle connection (the image monitor, a node).
        bystander = PromptDatabase(self.path)
        opened = threading.Event()
        worker = threading.Thread(
            target=lambda: (bystander.search_prompts(), opened.set())
        )
        worker.start()
        worker.join(WAIT)
        self.assertTrue(opened.is_set())

        try:
            self.db.model.restore_from_file(src)

            self.assertEqual(_texts(self.path), ["restored"])
            wal = self.path + "-wal"
            self.assertEqual(os.path.getsize(wal) if os.path.exists(wal) else 0, 0)
            self.assertEqual(
                [p["text"] for p in bystander.search_prompts()], ["restored"]
            )
        finally:
            bystander.close_all()

    def test_a_reader_left_open_is_waited_for_then_logged(self):
        src = self.source(["restored"])
        self.save(self.db, "live")
        stuck = PromptDatabase(self.path)
        conn = stuck.model.get_connection()
        conn.execute("BEGIN")
        conn.execute("SELECT COUNT(*) FROM prompts").fetchone()  # holds a snapshot
        try:
            with (
                patch.object(restore_stage, "CHECKPOINT_BUSY_MS", 50),
                self.assertLogs(
                    "prompt_manager.database.restore_stage", "WARNING"
                ) as logs,
            ):
                self.db.model.restore_from_file(src)
        finally:
            conn.rollback()
            stuck.close_all()
        self.assertIn("could not be truncated", "\n".join(logs.output))
        self.assertEqual(_texts(self.path), ["restored"])


class TestDamagedDocuments(StageTestCase):
    """Carry-over 5: repair what can be repaired, report what cannot."""

    def library_with_documents(self, name, inline_copy):
        """A backup whose two documents are damaged; one has an inline copy."""
        path = os.path.join(self.uploads, name)
        src = PromptDatabase(path)
        prompt_id = self.save(src, "with workflows")
        first = src.link_image_to_prompt(
            prompt_id, os.path.join(self.uploads, "a.png"), {"workflow": {"v": 1}}
        )
        second = src.link_image_to_prompt(
            prompt_id, os.path.join(self.uploads, "b.png"), {"workflow": {"v": 2}}
        )
        conn = src.model.get_connection()
        ids = [
            conn.execute(
                "SELECT workflow_document_id FROM generated_images WHERE id=?", (i,)
            ).fetchone()[0]
            for i in (first, second)
        ]
        texts = [wd.read_stored_text(conn, None, document_id) for document_id in ids]
        with conn:
            if inline_copy:
                # An older row that still holds the first text inline.
                conn.execute(
                    "INSERT INTO generated_images (prompt_id, image_path, filename,"
                    " workflow_data) VALUES (?, ?, 'c.png', ?)",
                    (prompt_id, os.path.join(self.uploads, "c.png"), texts[0]),
                )
            for document_id in ids:
                conn.execute(
                    "UPDATE workflow_documents SET data=? WHERE id=?",
                    (zlib.compress(b"damaged"), document_id),
                )
        src.close_all()
        return path, ids, texts, (first, second)

    def test_damaged_documents_are_repaired_or_cleared_and_reported(self):
        src, ids, texts, images = self.library_with_documents("damaged.db", True)
        ok, reason = PromptModel.verify_database_file(src)
        self.assertFalse(ok)
        self.assertTrue(reason.startswith("Workflow document "), reason)
        before = _digests(src)

        staged = self.db.model.stage_restore(src)
        try:
            self.assertEqual(
                staged.documents, {"repaired": 1, "cleared": 1, "images": 1}
            )
            self.db.model.publish_staged(staged)
        finally:
            staged.discard()

        self.assertEqual(_digests(src), before)  # the upload is never changed
        ok, reason = PromptModel.verify_database_file(self.path)
        self.assertTrue(ok, reason)
        conn = self.db.model.get_connection()
        rows = {
            row[0]: (row[1], row[2])
            for row in conn.execute(
                "SELECT id, workflow_data, workflow_document_id FROM generated_images"
            )
        }
        repaired = rows[images[0]]
        self.assertEqual(repaired[1], ids[0])
        self.assertEqual(wd.read_stored_text(conn, None, repaired[1]), texts[0])
        self.assertEqual(rows[images[1]], (None, None))  # cleared, nothing readable

    def test_restore_from_file_accepts_a_backup_with_damaged_documents(self):
        src, _ids, _texts_, _images = self.library_with_documents("damaged.db", False)
        self.db.model.restore_from_file(src)
        ok, reason = PromptModel.verify_database_file(self.path)
        self.assertTrue(ok, reason)
        self.assertEqual(_texts(self.path), ["with workflows"])

    def test_an_intact_backup_reports_nothing_repaired(self):
        staged = self.db.model.stage_restore(self.source(["clean"]))
        try:
            self.assertEqual(
                staged.documents, {"repaired": 0, "cleared": 0, "images": 0}
            )
        finally:
            staged.discard()


class TestSweep(StageTestCase):
    def test_interrupted_stages_and_uploads_are_swept(self):
        stage = os.path.join(self.library, restore_stage.STAGE_PREFIX + "abc")
        os.mkdir(stage)
        Path(stage, "staged.db").write_bytes(b"x")
        upload = os.path.join(self.library, restore_stage.UPLOAD_PREFIX + "x.tmp")
        Path(upload).write_bytes(b"x")
        keep = os.path.join(self.library, "notes.txt")
        Path(keep).write_bytes(b"x")
        swept = restore_stage.sweep_restore_temps(self.path)
        self.assertEqual(
            sorted(swept), sorted([os.path.basename(stage), os.path.basename(upload)])
        )
        self.assertEqual(self.stage_dirs(), [])
        self.assertTrue(os.path.exists(keep))


if __name__ == "__main__":
    unittest.main()
