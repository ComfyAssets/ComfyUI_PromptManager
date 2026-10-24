"""PR #315 scoped re-review fixes: R1, R3, R4, R5 and R6 (R2 is wording).

- R1: only a row's own cause (undecodable text, a text that would not come
  back) keeps that row inline; disk-full, I/O and corruption fail the batch.
- R3: nothing to rewrite is refused up front; one kept pre-migration backup
  ever, later applies take a pooled safety backup instead.
- R4: the live job loop lets a writer on another connection in.
- R5: the digest tool loads no package, adds no log handler and leaves no
  ``-wal``/``-shm`` beside a stopped database.
- R6: a damaged document is named, with what to do, in the verify reason and
  in the job's backup failure.
"""

import asyncio
import os
import sqlite3
import subprocess  # nosec B404
import sys
import threading
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "workflow_text_digests.py")
KEPT = "prompts.db.pre-workflow-storage_"
WAIT = 20


def disk_full(*_args, **_kwargs):
    raise sqlite3.OperationalError("database or disk is full")


class TestSystemErrorsFailTheBatch(MigrationTestCase):
    def test_a_disk_error_fails_the_batch_instead_of_listing_rows(self):
        _prompt_id, expected = self.library(count=6)
        for error in (
            sqlite3.OperationalError("database or disk is full"),
            sqlite3.OperationalError("disk I/O error"),
            sqlite3.DatabaseError("database disk image is malformed"),
            TypeError("a bug"),
        ):
            with self.subTest(error=str(error)):
                with patch.object(ws, "store_text", side_effect=error):
                    with self.assertRaises(type(error)):
                        self.migrate(max_rows=50)
                self.assertFalse(self.conn().in_transaction)
                self.assertEqual(self.inline_ids(), set(expected))
                self.assertEqual(self.texts(), expected)


class TestSystemErrorsInTheJob(WorkflowStorageTestCase):
    async def test_a_disk_error_ends_the_job_failed_with_a_truthful_line(self):
        self.old_library(count=4)
        before = self.texts()
        with patch.object(ws, "store_text", side_effect=disk_full):
            _plan, _summary, job = await self.start()
            await self.finish(job)
        self.assertEqual(job.state, "failed")
        self.assertIn("The batch in progress was rolled back", job.status_line)
        self.assertEqual(job.counters["errors"], 1)  # the job's one error row
        self.assertNotIn("could not be rewritten", job.status_line)
        self.assertIsNone(self.reclaim_job())
        self.assertEqual(self.texts(), before)


class TestOneKeptBackup(WorkflowStorageTestCase):
    def kept(self):
        return sorted(n for n in os.listdir(self.tmpdir) if n.startswith(KEPT))

    async def test_nothing_to_rewrite_is_said_and_refused_without_a_backup(self):
        self.save("no images")
        plan = await self.preview(OPERATION)
        self.assertEqual(plan["scope"]["count"], 0)
        self.assertEqual(plan["scope"]["label"], "Nothing to rewrite")
        reply = await self.apply(plan["token"], status=409)
        self.assertEqual(reply["code"], "nothing-to-do")
        self.assertEqual(
            reply["error"],
            "Every image record already stores its workflow once; nothing to do.",
        )
        self.assertEqual(self.kept(), [])
        self.assertEqual(self.backups(), [])
        self.assertEqual(self.api._jobs.list(), [])

    async def test_a_cancel_and_rerun_keep_one_kept_backup_and_pool_the_next(self):
        self.old_library(count=12)
        real = self.api.db.migrate_workflow_batch
        holder = {}

        def first_then_cancel(*args, **kwargs):
            result = real(*args, **kwargs)
            holder["job"].cancelled = True
            return result

        with (
            patch.object(ws, "BATCH_ROWS", 5),
            patch.object(
                self.api.db, "migrate_workflow_batch", side_effect=first_then_cancel
            ),
        ):
            plan = await self.preview(OPERATION)
            body = await self.apply(plan["token"])
            holder["job"] = job = self.api._jobs.get(body["job"]["id"])
            await self.finish(job)
        self.assertEqual(job.state, "cancelled")
        [kept] = self.kept()
        plan = await self.preview(OPERATION)
        self.assertEqual(plan["backup"]["policy"], "required")
        self.assertEqual(
            plan["backup"]["destination_label"], "prompts.db.backup_<date and time>"
        )
        self.assertIn(
            f"the pre-migration backup {kept} is already kept",
            " ".join(row["detail"] for row in plan["details"]),
        )
        body = await self.apply(plan["token"])
        again = self.api._jobs.get(body["job"]["id"])
        await self.finish(again)
        self.assertEqual(again.state, "finished", again.log)
        self.assertEqual(self.kept(), [kept])  # still one never-pruned copy
        [pooled] = self.backups()
        self.assertIn(f"Safety backup {pooled} verified", "\n".join(again.log))
        self.assertIn(f"pre-migration backup {kept} is kept", "\n".join(again.log))


class TestLiveLoopLetsWritersIn(WorkflowStorageTestCase):
    async def test_a_writer_on_another_connection_gets_in_during_the_job(self):
        prompt_id = self.save("writer")
        conn = self.api.db.model.get_connection()
        with conn:
            for index in range(200):
                conn.execute(
                    "INSERT INTO generated_images (prompt_id, image_path, filename, "
                    "file_path, workflow_data) VALUES (?, ?, 'x', ?, ?)",
                    (prompt_id, f"/x/{index}", f"/x/{index}", f'{{"n": {index}}}'),
                )
        started, done = threading.Event(), threading.Event()
        errors, written = [], []
        real = ws.store_text

        def slow(conn, text, *args, **kwargs):
            started.set()
            done.wait(0.01)  # a slow disk: each row takes a while
            return real(conn, text, *args, **kwargs)

        def writer():
            started.wait(WAIT)
            own = self.api.db.model.get_connection()  # this thread's connection
            own.execute("PRAGMA busy_timeout = 2000")
            try:
                for index in range(5):
                    written.append(
                        self.api.db.link_image_to_prompt(
                            prompt_id,
                            os.path.join(self.tmpdir, f"w{index}.png"),
                            {"workflow": {"w": index}},
                        )
                    )
            except sqlite3.OperationalError as error:  # pragma: no cover - RED
                errors.append(str(error))
            finally:
                self.api.db.model.close()

        thread = threading.Thread(target=writer)
        thread.start()
        try:
            with patch.object(ws, "store_text", side_effect=slow):
                _plan, _summary, job = await self.start()
                await asyncio.wait_for(asyncio.shield(job.task), 120)
            await self.finish(job)  # and Reclaim space after it
        finally:
            done.set()
            await asyncio.get_running_loop().run_in_executor(None, thread.join, WAIT)
        self.assertEqual(job.state, "finished", job.log)
        self.assertEqual(errors, [])
        self.assertEqual(len([image for image in written if image]), 5)


class TestDigestToolSideEffects(MigrationTestCase):
    def run_tool(self, path, cwd):
        out = os.path.join(cwd, "out.sha")
        code = (
            "import logging, runpy, sys\n"
            "sys.argv = ['tool', sys.argv[1], sys.argv[2]]\n"
            "try:\n"
            f"    runpy.run_path({TOOL!r}, run_name='__main__')\n"
            "except SystemExit:\n"
            "    pass\n"
            "loaded = sorted(m for m in sys.modules\n"
            "                if m.split('.')[0] in ('database', 'utils', 'py'))\n"
            "handlers = [h for n in ('', 'prompt_manager')\n"
            "            for h in logging.getLogger(n).handlers]\n"
            "print('LOADED', loaded, 'HANDLERS', len(handlers))\n"
        )
        result = subprocess.run(  # nosec B603
            [sys.executable, "-c", code, path, out],
            capture_output=True,
            text=True,
            cwd=cwd,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_a_stopped_database_gets_no_sidecars_and_no_log(self):
        self.library(count=3)
        self.migrate()
        PromptModel.close_all_instances()
        for suffix in ("-wal", "-shm"):
            self.assertFalse(os.path.exists(self.db_path + suffix))
        cwd = os.path.join(self.tmpdir, "run")
        os.mkdir(cwd)
        before = sorted(os.listdir(self.tmpdir))
        printed = self.run_tool(self.db_path, cwd)
        self.assertEqual(sorted(os.listdir(self.tmpdir)), before)
        self.assertEqual(sorted(os.listdir(cwd)), ["out.sha"])  # no logs/ folder
        self.assertIn("LOADED [] HANDLERS 0", printed)
        with open(os.path.join(cwd, "out.sha"), encoding="utf-8") as handle:
            self.assertEqual(len(handle.read().splitlines()), 3)

    def test_a_live_database_is_read_with_its_wal(self):
        prompt_id = self.prompt()
        self.old_form_image(prompt_id, "a.png", '{"in": "wal"}', None)
        # The connection stays open: the row may still be only in the WAL.
        cwd = os.path.join(self.tmpdir, "run")
        os.mkdir(cwd)
        self.run_tool(self.db_path, cwd)
        with open(os.path.join(cwd, "out.sha"), encoding="utf-8") as handle:
            self.assertEqual(len(handle.read().splitlines()), 1)


class TestDamagedDocumentIsNamed(WorkflowStorageTestCase):
    def damage(self):
        conn = self.api.db.model.get_connection()
        with conn:
            document_id = wd.store_text(conn, '{"damaged": true}')
            conn.execute(
                "UPDATE workflow_documents SET data=x'00' WHERE id=?", (document_id,)
            )
        return document_id

    async def test_verify_names_the_document_and_what_to_do(self):
        document_id = self.damage()
        ok, reason = PromptModel.verify_database_file(self.api.db.model.db_path)
        self.assertFalse(ok)
        self.assertEqual(reason, wd.damaged_message(document_id))
        self.assertIn(f"Workflow document {document_id} is damaged", reason)
        self.assertIn("restore a backup", reason)

    async def test_the_backup_failure_says_which_document(self):
        self.old_library(count=2)
        document_id = self.damage()
        _plan, _summary, job = await self.start()
        await self.finish(job)
        self.assertEqual(job.state, "failed")
        self.assertEqual(job.last_event["code"], "backup-failed")
        self.assertIn(f"Workflow document {document_id} is damaged", job.status_line)
