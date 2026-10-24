"""Store each workflow once, as a maintenance job (py/api/workflow_storage.py).

``POST /prompt_manager/maintenance/preview {operation:"workflow_storage"}``
reviews the rewrite; applying it starts a ``records`` job that takes the
verified safety backup first, rewrites old-form rows batch by batch (Cancel
stops after the current batch) and then starts "Reclaim space", an
``exclusive`` job with its own disk check. No image file is ever opened.
"""

import asyncio
import builtins
import contextlib
import hashlib
import json
import os
import sqlite3
from unittest.mock import patch

from PIL import Image, PngImagePlugin

from database import workflow_storage as ws
from database.workflow_documents import DocumentReader, resolve_image_row, store_text
from py.api import maintenance
from py.api.jobs import MAINTENANCE_JOB_KIND

try:
    from tests.test_api_maintenance import ApplyTestCase
    from tests.test_workflow_documents import sample_prompt_graph, sample_workflow
except ImportError:  # discovered with tests/ as the top-level directory
    from test_api_maintenance import ApplyTestCase
    from test_workflow_documents import sample_prompt_graph, sample_workflow

WAIT = 20
OPERATION = "workflow_storage"


def _digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class WorkflowStorageTestCase(ApplyTestCase):
    def old_library(self, count=12, media=False):
        """Old-form rows (text inline), four images per run."""
        prompt_id = self.save("a cat")
        conn = self.api.db.model.get_connection()
        for index in range(count):
            run = index // 4
            path = self.output_dir / f"{index}.png"
            workflow = json.dumps(sample_workflow(run, nodes=120))
            if media:
                info = PngImagePlugin.PngInfo()
                info.add_text("workflow", workflow)
                with Image.new("RGB", (4, 4)) as image:
                    image.save(path, pnginfo=info)
            with conn:
                conn.execute(
                    "INSERT INTO generated_images (prompt_id, image_path, filename, "
                    "file_path, workflow_data, prompt_metadata, parameters) "
                    "VALUES (?, ?, ?, ?, ?, ?, '{}')",
                    (
                        prompt_id,
                        str(path),
                        path.name,
                        str(path),
                        workflow,
                        json.dumps(sample_prompt_graph(run)),
                    ),
                )
        return prompt_id

    def texts(self):
        conn = self.api.db.model.get_connection()
        reader = DocumentReader(conn)
        return {
            row["id"]: resolve_image_row(reader, row)
            for row in conn.execute(
                "SELECT id, workflow_data, prompt_metadata, workflow_document_id, "
                "prompt_document_id FROM generated_images ORDER BY id"
            )
        }

    def inline_count(self):
        conn = self.api.db.model.get_connection()
        return conn.execute(
            "SELECT COUNT(*) FROM generated_images WHERE workflow_data IS NOT NULL"
        ).fetchone()[0]

    def reclaim_job(self):
        jobs = [job for job in self.api._jobs.list() if job.title == "Reclaim space"]
        return jobs[0] if jobs else None

    async def finish(self, job):
        await asyncio.wait_for(asyncio.shield(job.task), WAIT)
        reclaim = self.reclaim_job()
        if reclaim is not None and reclaim.task is not None:
            await asyncio.wait_for(asyncio.shield(reclaim.task), WAIT)
        return reclaim

    async def start(self):
        plan = await self.preview(OPERATION)
        body = await self.apply(plan["token"])
        return plan, body["job"], self.api._jobs.get(body["job"]["id"])


class TestWorkflowStorageJob(WorkflowStorageTestCase):
    async def test_preview_reviews_the_rewrite_and_writes_nothing(self):
        self.old_library()
        db_path = self.api.db.model.db_path
        before = _digest(db_path), _digest(db_path + "-wal")
        plan = await self.preview(OPERATION)
        self.assertEqual((_digest(db_path), _digest(db_path + "-wal")), before)
        self.assertEqual(plan["operation"], OPERATION)
        self.assertEqual(plan["scope"]["count"], 12)
        self.assertEqual(plan["scope"]["label"], "12 image records to rewrite")
        self.assertFalse(plan["media"]["deletes_files"])
        self.assertEqual(
            plan["media"]["label"],
            "No image or video file is opened or changed; only the database.",
        )
        self.assertEqual(plan["backup"]["policy"], "required")
        self.assertEqual(
            plan["backup"]["destination_label"],
            "prompts.db.pre-workflow-storage_<date and time>",
        )
        self.assertGreater(plan["backup"]["expected_bytes"], 0)
        self.assertTrue(plan["disk"]["sufficient"])

    async def test_apply_backs_up_rewrites_then_reclaims_space(self):
        prompt_id = self.old_library(count=40)
        before = self.texts()
        listed = await self.client.request(
            "GET", f"/prompt_manager/prompts/{prompt_id}/images"
        )
        listed_before = await listed.json()
        size_before = self.database_bytes()
        plan, summary, job = await self.start()
        self.assertEqual(summary["kind"], MAINTENANCE_JOB_KIND)
        self.assertEqual(summary["title"], "Store each workflow once")
        self.assertEqual(summary["admission"], "records")
        self.assertTrue(summary["cancellable"])
        self.assertEqual(summary["scope"], {"operation": OPERATION})
        reclaim = await self.finish(job)
        self.assertEqual(job.state, "finished", job.log)
        self.assertEqual(job.counters, {"changed": 40, "protected": 0, "errors": 0})
        self.assertEqual(job.total, 40)
        self.assertEqual(job.processed, 40)
        self.assertEqual(self.inline_count(), 0)
        self.assertEqual(self.texts(), before)
        self.assertEqual(self.backups(), [])  # never in the pruned pool
        [backup] = [
            name
            for name in os.listdir(self.tmpdir)
            if name.startswith("prompts.db.pre-workflow-storage_")
        ]
        self.assertIn(f"Pre-migration backup {backup} verified", "\n".join(job.log))
        self.assertEqual(
            job.status_line,
            f"Rewrote 40 image records · Pre-migration backup {backup}",
        )
        # The safety backup holds the old form, verified.
        conn = sqlite3.connect(os.path.join(self.tmpdir, backup))
        try:
            inline = conn.execute(
                "SELECT COUNT(*) FROM generated_images WHERE workflow_data IS NOT NULL"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(inline, 40)
        # Reclaim space ran alone afterwards and gave the space back.
        self.assertIsNotNone(reclaim)
        self.assertEqual(reclaim.admission, "exclusive")
        self.assertFalse(reclaim.cancellable)
        self.assertEqual(reclaim.state, "finished", reclaim.log)
        self.assertIn("Reclaim space started", "\n".join(job.log))
        self.assertLess(self.database_bytes(), size_before / 3)
        wal = self.api.db.model.db_path + "-wal"
        self.assertEqual(os.path.getsize(wal) if os.path.exists(wal) else 0, 0)
        self.assertRegex(reclaim.status_line, r"^Reclaimed [\d.,]+ MB · ")
        listed = await self.client.request(
            "GET", f"/prompt_manager/prompts/{prompt_id}/images"
        )
        self.assertEqual(await listed.json(), listed_before)
        self.assertEqual(self.strays(), [backup])  # only the kept backup
        self.assertNotIn(self.tmpdir, json.dumps(job.report()))

    async def test_a_backup_failure_rewrites_nothing(self):
        self.old_library()
        from py.api import workflow_storage as api_storage

        with patch.object(
            api_storage,
            "take_safety_backup",
            side_effect=maintenance.SafetyBackupFailed("no"),
        ):
            _plan, _summary, job = await self.start()
            await self.finish(job)
        self.assertEqual(job.state, "failed")
        self.assertEqual(job.last_event["code"], "backup-failed")
        self.assertEqual(self.inline_count(), 12)
        self.assertIsNone(self.reclaim_job())

    async def test_cancel_stops_after_the_current_batch_and_a_rerun_continues(self):
        self.old_library()
        before = self.texts()
        real = self.api.db.migrate_workflow_batch
        holder = {}

        def first_then_cancel(*args, **kwargs):
            result = real(*args, **kwargs)
            holder["job"].cancelled = True  # Cancel pressed during batch one
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
        self.assertEqual(job.counters["changed"], 5)
        self.assertEqual(self.inline_count(), 7)
        self.assertIn("run it again to continue", job.status_line)
        self.assertIsNone(self.reclaim_job())
        self.assertEqual(self.texts(), before)
        _plan, _summary, again = await self.start()
        await self.finish(again)
        self.assertEqual(again.state, "finished", again.log)
        self.assertEqual(again.counters["changed"], 7)
        self.assertEqual(self.inline_count(), 0)
        self.assertEqual(self.texts(), before)

    async def test_a_failed_batch_fails_the_job_and_every_record_still_reads(self):
        self.old_library()
        before = self.texts()
        real = self.api.db.migrate_workflow_batch
        calls = []

        def failing(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise sqlite3.OperationalError("disk I/O error")
            return real(*args, **kwargs)

        with (
            patch.object(ws, "BATCH_ROWS", 5),
            patch.object(self.api.db, "migrate_workflow_batch", side_effect=failing),
        ):
            _plan, _summary, job = await self.start()
            await self.finish(job)
        self.assertEqual(job.state, "failed")
        self.assertIn(
            "The batch in progress was rolled back; every record reads as before.",
            job.status_line,
        )
        self.assertNotIn("disk I/O", job.status_line)
        self.assertEqual(job.counters["changed"], 5)
        self.assertEqual(job.counters["errors"], 1)
        self.assertEqual(self.texts(), before)
        self.assertIsNone(self.reclaim_job())

    async def test_reclaim_space_that_cannot_start_says_why(self):
        self.old_library()
        release = asyncio.Event()

        async def held(job):
            await release.wait()

        reader = self.api._jobs.start(
            "health", "Health check", admission="read", runner=held
        )
        try:
            _plan, _summary, job = await self.start()
            await self.finish(job)
        finally:
            release.set()
            await asyncio.wait_for(reader.task, WAIT)
        self.assertEqual(job.state, "finished", job.log)
        self.assertIsNone(self.reclaim_job())
        self.assertIn(
            "Reclaim space did not start: Health check is running. "
            "Run Optimize database to give the space back.",
            "\n".join(job.log),
        )
        self.assertIn("Reclaim space did not start", job.status_line)

    async def test_reclaim_space_checks_the_disk_first(self):
        self.old_library()
        before = self.texts()
        _plan, _summary, job = await self.start()
        free = {"bytes": 10**12}

        def free_bytes(_path):
            return free["bytes"]

        with patch.object(maintenance, "_free_bytes", side_effect=free_bytes):
            await asyncio.wait_for(asyncio.shield(job.task), WAIT)
            reclaim = self.reclaim_job()
            free["bytes"] = 1024
            # The reclaim job checks when it runs, after the rewrite.
            await asyncio.wait_for(asyncio.shield(reclaim.task), WAIT)
        self.assertEqual(reclaim.state, "failed")
        self.assertEqual(reclaim.last_event["code"], "insufficient-space")
        self.assertIn("Not enough free disk space", reclaim.status_line)
        self.assertEqual(self.texts(), before)

    async def test_apply_refuses_up_front_when_the_backup_does_not_fit(self):
        self.old_library()
        plan = await self.preview(OPERATION)
        with patch.object(maintenance, "_free_bytes", return_value=1024):
            body = await self.apply(plan["token"], status=507)
        self.assertEqual(body["code"], "insufficient-space")
        self.assertEqual(self.inline_count(), 12)

    async def test_the_jobs_never_open_a_media_file(self):
        self.old_library(media=True)
        media = str(self.output_dir)
        before = {
            name: (
                _digest(os.path.join(media, name)),
                os.stat(os.path.join(media, name)),
            )
            for name in os.listdir(media)
        }
        opened = []
        real_open, real_os_open = builtins.open, os.open

        def tracking_open(file, *args, **kwargs):
            opened.append(str(file))
            return real_open(file, *args, **kwargs)

        def tracking_os_open(path, *args, **kwargs):
            opened.append(str(path))
            return real_os_open(path, *args, **kwargs)

        with patch("builtins.open", tracking_open), patch("os.open", tracking_os_open):
            with patch.object(Image, "open", side_effect=AssertionError("opened")):
                _plan, _summary, job = await self.start()
                reclaim = await self.finish(job)
        self.assertEqual(job.state, "finished", job.log)
        self.assertEqual(reclaim.state, "finished", reclaim.log)
        self.assertEqual([path for path in opened if path.startswith(media)], [])
        for name, (digest, stat) in before.items():
            path = os.path.join(media, name)
            self.assertEqual(_digest(path), digest)
            self.assertEqual(os.stat(path).st_mtime_ns, stat.st_mtime_ns)

    async def test_a_second_apply_of_the_token_is_plan_used(self):
        self.old_library(count=4)
        plan = await self.preview(OPERATION)
        body = await self.apply(plan["token"])
        await self.finish(self.api._jobs.get(body["job"]["id"]))
        again = await self.apply(plan["token"], status=409)
        self.assertEqual(again["code"], "plan-used")


class TestWorkflowStorageOutcomes(WorkflowStorageTestCase):
    async def test_kept_values_and_swept_documents_are_logged(self):
        prompt_id = self.old_library(count=4)
        conn = self.api.db.model.get_connection()
        with conn:
            conn.execute(
                "INSERT INTO generated_images (prompt_id, image_path, filename, "
                "file_path, workflow_data) VALUES (?, '/x/b', 'b', '/x/b', x'00')",
                (prompt_id,),
            )
            store_text(conn, "a document no record references")
        _plan, _summary, job = await self.start()
        await self.finish(job)
        log = "\n".join(job.log)
        self.assertEqual(job.counters["protected"], 1)
        self.assertIn(
            "Rewrote 4 image records; 1 stored value not text, kept as is", log
        )
        self.assertIn("Removed 1 unused workflow document", log)

    async def reclaim_with(self, **patches):
        self.old_library(count=4)
        before = self.texts()
        with contextlib.ExitStack() as stack:
            for name, value in patches.items():
                stack.enter_context(patch.object(self.api.db, name, value))
            _plan, _summary, job = await self.start()
            reclaim = await self.finish(job)
        self.assertEqual(self.texts(), before)
        return reclaim

    async def test_a_failed_reclaim_says_the_database_is_unchanged(self):
        reclaim = await self.reclaim_with(
            reclaim_space=lambda: (_ for _ in ()).throw(
                sqlite3.OperationalError("database is locked")
            )
        )
        self.assertEqual(reclaim.state, "failed")
        self.assertEqual(
            reclaim.status_line, "Reclaim space failed; the database is unchanged."
        )

    async def test_a_reclaim_whose_disk_cannot_be_measured_fails_cleanly(self):
        from py.api import workflow_storage as api_storage

        with patch.object(api_storage, "live_bytes", side_effect=OSError("gone")):
            reclaim = await self.reclaim_with()
        self.assertEqual(reclaim.state, "failed")
        self.assertNotIn("gone", reclaim.status_line)

    async def test_a_busy_checkpoint_says_the_rest_follows_a_restart(self):
        reclaim = await self.reclaim_with(
            reclaim_space=lambda: {
                "before_bytes": 3 * 2**20,
                "after_bytes": 2**20,
                "documents_removed": 0,
                "checkpoint_busy": True,
            }
        )
        self.assertEqual(
            reclaim.status_line,
            "Reclaimed 2.0 MB · the database is now 1.0 MB "
            "(the rest after the next restart)",
        )
