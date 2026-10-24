"""PR #315 final review fixes: I1, I2, I3, M1, M2, M3 and M5.

- I1: the migration always takes a verified backup, whatever the policy.
- I2: that backup has its own name outside the pruned pool.
- I3: ``tools/workflow_text_digests.py`` proves byte identity across the rewrite.
- M1: a writer on another connection is never starved by the batches.
- M2: a row that cannot be rewritten stays inline, is counted, and the rest go on.
- M3: a damaged document is refused on restore and repaired before reuse.
- M5: the legacy table rebuilds refuse a file with document id columns.
"""

import hashlib
import os
import re
import sqlite3
import subprocess  # nosec B404
import sys
import threading
import zlib
from unittest.mock import patch

from database import workflow_documents as wd
from database import workflow_storage as ws
from database.models import PromptModel

try:
    from tests.test_api_workflow_storage import OPERATION, WorkflowStorageTestCase
    from tests.test_workflow_migration import MigrationTestCase
except ImportError:  # discovered with tests/ as the top-level directory
    from test_api_workflow_storage import OPERATION, WorkflowStorageTestCase
    from test_workflow_migration import MigrationTestCase

# After the API test modules, which stand in ComfyUI's ``server`` module.
from py.api.admin import _prune_safety_backups  # noqa: E402
from py.config import PromptManagerConfig  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRE_MIGRATION = re.compile(
    r"^prompts\.db\.pre-workflow-storage_\d{8}_\d{6}_\d{6}(_\d+)?$"
)
WAIT = 20


class TestMigrationBackup(WorkflowStorageTestCase):
    def pre_migration(self):
        return sorted(
            name for name in os.listdir(self.tmpdir) if PRE_MIGRATION.match(name)
        )

    async def test_the_backup_is_taken_even_with_the_policy_off(self):
        PromptManagerConfig.SAFETY_BACKUP = False
        self.old_library(count=4)
        plan = await self.preview(OPERATION)
        self.assertEqual(plan["backup"]["policy"], "required")
        self.assertGreater(plan["backup"]["expected_bytes"], 0)
        self.assertGreater(plan["disk"]["required_bytes"], 0)
        body = await self.apply(plan["token"])
        job = self.api._jobs.get(body["job"]["id"])
        await self.finish(job)
        self.assertEqual(job.state, "finished", job.log)
        [backup] = self.pre_migration()
        self.assertNotIn("No safety backup", "\n".join(job.log))
        self.assertIn(f"Pre-migration backup {backup} verified", "\n".join(job.log))

    async def test_the_backup_is_outside_the_pool_never_pruned_and_restores(self):
        self.old_library(count=4)
        before = self.texts()
        plan = await self.preview(OPERATION)
        self.assertIn(
            "prompts.db.pre-workflow-storage_<date and time>",
            " ".join(row["detail"] for row in plan["details"]),
        )
        _plan, _summary, job = await self.start()
        await self.finish(job)
        [backup] = self.pre_migration()
        self.assertEqual(self.backups(), [])  # not in the pruned pool
        self.assertIn(f"Pre-migration backup {backup}", job.status_line)
        path = os.path.join(self.tmpdir, backup)
        ok, reason = PromptModel.verify_database_file(path)
        self.assertTrue(ok, reason)
        _prune_safety_backups(self.api.db.model.db_path, keep=0)
        self.assertEqual(self.pre_migration(), [backup])
        # The way back: the legacy restore accepts it, old form included.
        self.api.db.model.restore_from_file(path)
        self.assertEqual(self.texts(), before)
        self.assertEqual(self.inline_count(), 4)


class TestTextDigestsTool(MigrationTestCase):
    def digests(self, path, out):
        result = subprocess.run(  # nosec B603
            [sys.executable, os.path.join(ROOT, "tools", "workflow_text_digests.py")]
            + [path, out],
            capture_output=True,
            text=True,
            cwd=self.tmpdir,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(out, encoding="utf-8") as handle:
            return handle.read()

    def test_digests_match_across_the_rewrite_and_read_only(self):
        _prompt_id, expected = self.library(count=12)
        PromptModel.close_all_instances()
        before = self.digests(self.db_path, os.path.join(self.tmpdir, "a.sha"))
        self.assertEqual(len(before.splitlines()), len(expected))
        self.migrate()
        PromptModel.close_all_instances()
        with open(self.db_path, "rb") as handle:
            frozen = handle.read()
        after = self.digests(self.db_path, os.path.join(self.tmpdir, "b.sha"))
        with open(self.db_path, "rb") as handle:
            self.assertEqual(handle.read(), frozen)
        self.assertEqual(after, before)

    def test_a_changed_text_shows_in_the_diff(self):
        prompt_id = self.prompt()
        self.old_form_image(prompt_id, "a.png", '{"a": 1}', None)
        PromptModel.close_all_instances()
        before = self.digests(self.db_path, os.path.join(self.tmpdir, "a.sha"))
        conn = self.conn()
        with conn:
            conn.execute("UPDATE generated_images SET workflow_data='{\"a\":1}'")
        PromptModel.close_all_instances()
        after = self.digests(self.db_path, os.path.join(self.tmpdir, "b.sha"))
        self.assertNotEqual(after, before)

    def test_main_in_process_hashes_undecodable_text_too(self):
        import contextlib
        import importlib.util
        import io

        spec = importlib.util.spec_from_file_location(
            "workflow_text_digests",
            os.path.join(ROOT, "tools", "workflow_text_digests.py"),
        )
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        prompt_id = self.prompt()
        conn = self.conn()
        with conn:
            conn.execute(
                "INSERT INTO generated_images (prompt_id, image_path, filename, "
                "file_path, workflow_data) VALUES (?, '/x', 'x', '/x', "
                "CAST(x'ff' AS TEXT))",
                (prompt_id,),
            )
        out = os.path.join(self.tmpdir, "c.sha")
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            self.assertEqual(tool.main([self.db_path, out]), 0)
        self.assertEqual(printed.getvalue(), "1 image records\n")
        with open(out, encoding="utf-8") as handle:
            line = handle.read().split()
        self.assertEqual(line[1], hashlib.sha256(b"\xff").hexdigest())
        self.assertEqual(line[2], "-")


class TestWriterIsNotStarved(MigrationTestCase):
    def test_a_writer_on_another_connection_gets_in_between_batches(self):
        prompt_id = self.prompt()
        for index in range(200):
            self.old_form_image(prompt_id, f"{index}.png", f'{{"n": {index}}}', None)
        started, done = threading.Event(), threading.Event()
        errors, written = [], []
        real = ws.store_text

        def slow(conn, text, *args, **kwargs):
            started.set()
            done.wait(0.01)  # a slow disk: each row takes a while
            return real(conn, text, *args, **kwargs)

        def writer():
            started.wait(WAIT)
            conn = self.db.model.get_connection()  # this thread's own connection
            conn.execute("PRAGMA busy_timeout = 2000")
            try:
                for index in range(5):
                    written.append(
                        self.db.link_image_to_prompt(
                            prompt_id,
                            os.path.join(self.tmpdir, f"w{index}.png"),
                            {"workflow": {"w": index}},
                        )
                    )
            except sqlite3.OperationalError as error:  # pragma: no cover - RED
                errors.append(str(error))
            finally:
                self.db.model.close()

        thread = threading.Thread(target=writer)
        thread.start()
        with patch.object(ws, "store_text", side_effect=slow):
            self.db.migrate_workflow_storage()
        done.set()
        thread.join(WAIT)
        self.assertEqual(errors, [])
        self.assertEqual(len([image for image in written if image]), 5)

    def test_a_batch_has_a_time_budget(self):
        prompt_id = self.prompt()
        for index in range(30):
            self.old_form_image(prompt_id, f"{index}.png", f'{{"n": {index}}}', None)
        ticks = iter(range(10**6))
        with patch.object(ws, "_clock", side_effect=lambda: next(ticks)):
            batch = ws.migrate_batch(self.conn(), 0, max_seconds=5)
        self.assertLess(batch["rows"], 30)
        self.assertGreater(batch["rows"], 0)


class TestRowErrors(MigrationTestCase):
    def test_an_unreadable_row_stays_inline_and_the_rest_are_rewritten(self):
        prompt_id, expected = self.library(count=10)
        conn = self.conn()
        with conn:
            bad = conn.execute(
                "INSERT INTO generated_images (prompt_id, image_path, filename, "
                "file_path, workflow_data) VALUES (?, '/x/bad', 'bad', '/x/bad', "
                "CAST(x'7b22ff227d' AS TEXT))",
                (prompt_id,),
            ).lastrowid
        result = self.migrate()
        self.assertEqual(result["errors"], [bad])
        self.assertEqual(result["rows"], len(expected))
        self.assertEqual(self.inline_ids_without(bad), set())
        raw = conn.execute(
            "SELECT CAST(workflow_data AS BLOB), workflow_document_id "
            "FROM generated_images WHERE id=?",
            (bad,),
        ).fetchone()
        self.assertEqual((bytes(raw[0]), raw[1]), (b'{"\xff"}', None))
        self.assertEqual(result["documents_removed"], 0)

    def inline_ids_without(self, image_id):
        return {
            row[0]
            for row in self.conn().execute(
                "SELECT id FROM generated_images WHERE id != ? AND "
                "(workflow_data IS NOT NULL OR prompt_metadata IS NOT NULL)",
                (image_id,),
            )
        }

    def test_a_row_whose_text_will_not_come_back_is_kept_and_counted(self):
        prompt_id = self.prompt()
        good = self.old_form_image(prompt_id, "good.png", '{"good": 1}', None)
        bad = self.old_form_image(prompt_id, "bad.png", '{"bad": 1}', None)
        real = ws.read_stored_text

        def lying(conn, inline, document_id):
            text = real(conn, inline, document_id)
            return "other" if text == '{"bad": 1}' else text

        with patch.object(ws, "read_stored_text", side_effect=lying):
            result = self.migrate()
        self.assertEqual(result["errors"], [bad])
        self.assertEqual(self.inline_ids(), {bad})
        self.assertEqual(self.texts()[good][0], '{"good": 1}')
        self.assertEqual(self.texts()[bad][0], '{"bad": 1}')
        self.assertEqual(self.document_count(), 1)  # the bad row's went


class TestRowErrorsInTheJob(WorkflowStorageTestCase):
    async def test_the_job_reports_kept_rows_and_reclaim_space_still_runs(self):
        prompt_id = self.old_library(count=4)
        conn = self.api.db.model.get_connection()
        with conn:
            conn.execute(
                "INSERT INTO generated_images (prompt_id, image_path, filename, "
                "file_path, workflow_data) VALUES (?, '/x/bad', 'bad', '/x/bad', "
                "CAST(x'7b22ff227d' AS TEXT))",
                (prompt_id,),
            )
        _plan, _summary, job = await self.start()
        reclaim = await self.finish(job)
        self.assertEqual(job.state, "finished", job.log)
        self.assertEqual(job.counters["changed"], 4)
        self.assertEqual(job.counters["errors"], 1)
        self.assertIn(
            "1 image record could not be rewritten and keeps its inline text",
            job.status_line,
        )
        self.assertIsNotNone(reclaim)
        self.assertEqual(reclaim.state, "finished", reclaim.log)


class TestDamagedDocuments(MigrationTestCase):
    def damage(self, document_id, data=b"not zlib"):
        conn = self.conn()
        with conn:
            conn.execute(
                "UPDATE workflow_documents SET data=? WHERE id=?", (data, document_id)
            )

    def test_verify_refuses_a_damaged_document(self):
        document_id = self.store('{"a": 1}')
        ok, _reason = PromptModel.verify_database_file(self.db_path)
        self.assertTrue(ok)
        for data in (b"not zlib", zlib.compress(b'{"a": 2}')):
            self.damage(document_id, data)
            with self.subTest(data=data):
                ok, reason = PromptModel.verify_database_file(self.db_path)
                self.assertFalse(ok)
                self.assertEqual(reason, wd.damaged_message(document_id))

    def test_a_damaged_document_is_repaired_before_it_is_reused(self):
        prompt_id = self.prompt()
        first = self.db.link_image_to_prompt(
            prompt_id, os.path.join(self.tmpdir, "a.png"), {"workflow": {"v": 1}}
        )
        document_id = self.raw(first)["workflow_document_id"]
        self.damage(document_id)
        with self.assertLogs(wd.logger, "WARNING"):
            second = self.db.link_image_to_prompt(
                prompt_id, os.path.join(self.tmpdir, "b.png"), {"workflow": {"v": 1}}
            )
        self.assertEqual(self.raw(second)["workflow_document_id"], document_id)
        for image_id in (first, second):
            self.assertEqual(self.texts()[image_id][0], '{"v": 1}')

    def test_store_verifies_a_reused_document_once_per_given_set(self):
        document_id = self.store("abc")
        verified = set()
        conn = self.conn()
        with conn:
            self.assertEqual(wd.store_text(conn, "abc", verified), document_id)
        self.assertEqual(verified, {document_id})
        self.damage(document_id)
        with conn:  # already verified in this set: not read again
            self.assertEqual(wd.store_text(conn, "abc", verified), document_id)


class TestRebuildGuards(MigrationTestCase):
    def test_the_legacy_rebuilds_refuse_document_columns(self):
        for column, kind in (("prompt_id", "TEXT"), ("file_path", None)):
            path = os.path.join(self.tmpdir, f"guard-{column}.db")
            conn = sqlite3.connect(path)
            try:
                columns = [
                    "id INTEGER PRIMARY KEY AUTOINCREMENT",
                    f"prompt_id {kind or 'INTEGER'} NOT NULL",
                    "image_path TEXT NOT NULL",
                    "filename TEXT NOT NULL",
                    "generation_time TIMESTAMP",
                    "workflow_data TEXT",
                    "prompt_metadata TEXT",
                    "parameters TEXT",
                    "workflow_document_id INTEGER",
                    "prompt_document_id INTEGER",
                ]
                if kind is not None:
                    columns.append("file_path TEXT")
                conn.executescript(
                    "CREATE TABLE prompts (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "text TEXT NOT NULL, created_at TIMESTAMP, updated_at TIMESTAMP, "
                    "category TEXT, tags TEXT, rating INTEGER, notes TEXT, "
                    "hash TEXT UNIQUE);"
                    f"CREATE TABLE generated_images ({', '.join(columns)});"
                )
                conn.commit()
            finally:
                conn.close()
            with self.subTest(column=column):
                with self.assertRaisesRegex(sqlite3.DatabaseError, "workflow document"):
                    PromptModel(path)
                PromptModel.close_all_instances()
                PromptModel.reset_schema_cache()


if __name__ == "__main__":  # pragma: no cover
    import unittest

    unittest.main()
