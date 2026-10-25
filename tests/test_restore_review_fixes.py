"""PR #335 final review fixes (#249 System S8): I1, M1 to M7, N1 and N3.

- I1: once the stage replaced the library, the restore is done: later steps
  (pruning, the switch) only log, and the job and legacy route say so.
- M1: a backup download holds the registry only while its snapshot is made.
- M2: a restore preview leases only its live read; a busy drain keeps the
  reviewed stage.
- M3: the reviewed-preview store changes only on the event loop.
- M4: a document whose body is intact but whose key or length is damaged is
  re-keyed, not dropped.
- M5: a backup download is refused only for a corrupt snapshot; a semantic
  check failure downloads, logged and reported.
- M6: a cancelled preview removes what it staged.
- M7 / N1 / N3: repair reads one row at a time; images counted once; the
  fingerprint never commits a caller's transaction.

Ordering is driven by events and job state, never by timers.
"""

import asyncio
import hashlib
import os
import sqlite3
import threading
import zlib
from contextlib import closing
from unittest.mock import patch

from aiohttp import web

from database import restore_stage
from database import workflow_documents as wd
from database.managed_connection import ManagedCursor
from database.models import PromptModel
from database.operations import PromptDatabase
from py.api import admin as admin_mod
from py.api import backup_restore, jobs

try:
    from tests.test_restore_plans import RestoreTestCase, _texts
except ImportError:  # discovered with tests/ as the top-level directory
    from test_restore_plans import RestoreTestCase, _texts

WAIT = 60


class TestPostPublishSteps(RestoreTestCase):
    """I1: a failure after the copy never says the library was not changed."""

    async def test_job_finishes_when_pruning_fails_after_the_copy(self):
        self.save("old")
        old_db = self.api.db
        generation = self.api._db_generation
        body = await self.preview(self.upload(["restored"]))
        with (
            patch.object(admin_mod, "_prune_safety_backups", side_effect=OSError("x")),
            self.assertLogs(self.api.logger, "WARNING") as logs,
        ):
            started = await self.apply(body["token"])
            job = await self.finish(started["job"]["id"])
        self.assertEqual(job.state, "finished", job.log)
        self.assertIn("Restored 1 prompt", job.status_line)
        self.assertNotIn("not changed", "\n".join(job.log))
        self.assertIsNot(self.api.db, old_db)
        self.assertEqual(self.api._db_generation, generation + 1)
        self.assertEqual(_texts(self.db_path), ["restored"])
        self.assertIn("prune", "\n".join(logs.output).lower())

    async def test_job_adopts_the_library_when_closing_the_old_one_fails(self):
        self.save("old")
        old_db = self.api.db
        body = await self.preview(self.upload(["restored"]))
        with patch.object(old_db, "close_all", side_effect=RuntimeError("close")):
            started = await self.apply(body["token"])
            job = await self.finish(started["job"]["id"])
        self.assertEqual(job.state, "finished", job.log)
        self.assertIsNot(self.api.db, old_db)
        self.assertEqual(
            [p["text"] for p in self.api.db.search_prompts()], ["restored"]
        )

    async def test_job_finishes_when_opening_the_new_object_fails(self):
        self.save("old")
        generation = self.api._db_generation
        body = await self.preview(self.upload(["restored"]))
        with patch.object(
            backup_restore, "PromptDatabase", side_effect=RuntimeError("open")
        ):
            started = await self.apply(body["token"])
            job = await self.finish(started["job"]["id"])
        self.assertEqual(job.state, "finished", job.log)
        # The old object still points at the same, restored file.
        self.assertEqual(
            [p["text"] for p in self.api.db.search_prompts()], ["restored"]
        )
        self.assertEqual(self.api._db_generation, generation + 1)

    async def test_legacy_restore_succeeds_when_pruning_fails_after_the_copy(self):
        self.save("old")
        old_db = self.api.db
        with patch.object(admin_mod, "_prune_safety_backups", side_effect=OSError("x")):
            resp = await self.client.post(
                "/prompt_manager/restore", data=self.form(self.upload(["restored"]))
            )
        body = await resp.json()
        self.assertEqual(resp.status, 200, body)
        self.assertEqual(
            body["message"], "Database restored successfully. Found 1 prompts."
        )
        self.assertIsNot(self.api.db, old_db)


class TestBackupHoldScope(RestoreTestCase):
    """M1 and M5: the hold covers the snapshot only; semantic issues warn."""

    async def test_hold_ends_before_the_first_byte_is_sent(self):
        self.save("keep")
        seen = []
        real = web.StreamResponse.prepare

        async def prepare(response, request):
            seen.append(self.api._jobs.active("backup"))
            return await real(response, request)

        with patch.object(web.StreamResponse, "prepare", prepare):
            resp, data = await self.download_backup()
        self.assertEqual(resp.status, 200)
        self.assertTrue(seen)
        self.assertEqual(set(seen), {None})  # no hold while bytes are sent
        [job] = self.api._jobs.list()
        self.assertEqual((job.kind, job.state), ("backup", "finished"))
        self.assertTrue(data.startswith(b"SQLite format 3\x00"))

    async def test_a_corrupt_snapshot_is_refused(self):
        self.save("keep")
        with patch.object(
            backup_restore, "snapshot_corruption", return_value="Integrity check failed"
        ):
            resp = await self.client.get("/prompt_manager/backup")
        self.assertEqual(resp.status, 500)
        self.assertEqual(
            await resp.json(),
            {"success": False, "error": "Failed to create database backup"},
        )
        self.assertEqual(
            [n for n in os.listdir(self.tmpdir) if n.startswith(".pm-")], []
        )
        [job] = self.api._jobs.list()
        self.assertEqual(job.state, "failed")

    async def test_a_semantic_check_failure_still_downloads_and_says_so(self):
        self.save("keep")
        reason = "Image pair association has a different positive prompt owner"
        with (
            patch.object(
                PromptModel, "verify_database_file", return_value=(False, reason)
            ),
            self.assertLogs(self.api.logger, "WARNING") as logs,
        ):
            resp, data = await self.download_backup()
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.headers["X-PromptManager-Verify"], reason)
        self.assertTrue(data.startswith(b"SQLite format 3\x00"))
        self.assertIn(reason, "\n".join(logs.output))
        status = await (await self.client.get("/prompt_manager/backup/status")).json()
        self.assertFalse(status["last_download"]["verified"])

    async def test_a_verified_download_says_ok(self):
        self.save("keep")
        resp, _data = await self.download_backup()
        self.assertEqual(resp.headers["X-PromptManager-Verify"], "ok")


class TestPreviewLease(RestoreTestCase):
    """M2: upload and staging take no lease; a busy drain keeps the stage."""

    async def test_staging_holds_no_lease(self):
        leases = []
        real = PromptModel.stage_restore

        def stage(model, path):
            leases.append(dict(self.api._jobs.leases))
            return real(model, path)

        with patch.object(PromptModel, "stage_restore", stage):
            await self.preview(self.upload(["restored"]))
        self.assertEqual(leases, [{}])

    async def test_a_busy_drain_keeps_the_reviewed_stage(self):
        self.save("live")
        body = await self.preview(self.upload(["restored"]))
        lease = self.api._jobs.lease("Stuck preview")
        await lease.__aenter__()
        try:
            with patch.object(jobs, "LEASE_DRAIN_SECONDS", 0):
                started = await self.apply(body["token"])
                job = await self.finish(started["job"]["id"])
        finally:
            await lease.__aexit__(None, None, None)
        self.assertEqual(job.state, "failed")
        self.assertEqual(job.last_event["code"], "busy")
        self.assertIn("review is kept", job.status_line)
        self.assertEqual(len(self.stages()), 1)
        # The same review applies once the requests are done.
        job = await self.finish((await self.apply(body["token"]))["job"]["id"])
        self.assertEqual(job.state, "finished", job.log)
        self.assertEqual(_texts(self.db_path), ["restored"])


class TestStoreOnTheLoop(RestoreTestCase):
    """M3: entries leave the store on the loop; only file removal is threaded."""

    async def test_expired_entries_leave_the_store_before_their_files_go(self):
        clock = [1000.0]
        store = self.api._restore_plan_store = backup_restore.RestorePlanStore(
            clock=lambda: clock[0]
        )
        body = await self.preview(self.upload(["restored"]))
        held = store.peek(body["token"])
        entered, release = threading.Event(), threading.Event()
        real = held.staged.discard

        def slow_discard():
            entered.set()
            release.wait(WAIT)
            real()

        held.staged.discard = slow_discard
        clock[0] += 10_000
        cleanup = asyncio.ensure_future(self.api._drop_expired_restores())
        loop = asyncio.get_running_loop()
        self.assertTrue(await loop.run_in_executor(None, entered.wait, WAIT))
        # The removal runs on a worker; the store already forgot the entry.
        self.assertEqual(len(store), 0)
        second = await self.preview(self.upload(["other"], "other.db"))
        self.assertEqual(len(store), 1)
        release.set()
        await cleanup
        self.assertEqual(len(self.stages()), 1)
        store.peek(second["token"])

    async def test_an_expired_preview_is_removed_without_another_request(self):
        clock = [1000.0]
        self.api._restore_plan_store = backup_restore.RestorePlanStore(
            clock=lambda: clock[0], ttl=0
        )
        await self.preview(self.upload(["restored"]))
        # The expiry timer fires without another request: wait on its state.
        while self.stages() or len(self.api._restore_plans):
            await asyncio.sleep(0)
        self.assertEqual(self.stages(), [])


class TestCancelledPreview(RestoreTestCase):
    """M6: a cancelled preview leaves no stage behind."""

    async def test_cancel_while_reading_the_live_library(self):
        path = os.path.join(self.tmpdir, "uploads", "up.db")
        self.upload(["restored"], "up.db")
        entered, release = threading.Event(), threading.Event()
        real = self.api._live_fingerprint

        def slow(db):
            entered.set()
            release.wait(WAIT)
            return real(db)

        with patch.object(self.api, "_live_fingerprint", side_effect=slow):
            task = asyncio.ensure_future(
                self.api._review_upload(
                    self.api.db, self.api._database_identity(self.api.db), path
                )
            )
            loop = asyncio.get_running_loop()
            self.assertTrue(await loop.run_in_executor(None, entered.wait, WAIT))
            self.assertEqual(len(self.stages()), 1)
            task.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(self.stages(), [])
        self.assertEqual(len(self.api._restore_plans), 0)

    async def test_cancel_while_staging(self):
        path = os.path.join(self.tmpdir, "uploads", "up.db")
        self.upload(["restored"], "up.db")
        entered, release = threading.Event(), threading.Event()
        real = PromptModel.stage_restore
        done = threading.Event()

        def slow(model, src):
            entered.set()
            release.wait(WAIT)
            try:
                return real(model, src)
            finally:
                done.set()

        with patch.object(PromptModel, "stage_restore", slow):
            task = asyncio.ensure_future(
                self.api._review_upload(
                    self.api.db, self.api._database_identity(self.api.db), path
                )
            )
            loop = asyncio.get_running_loop()
            self.assertTrue(await loop.run_in_executor(None, entered.wait, WAIT))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            release.set()
            self.assertTrue(await loop.run_in_executor(None, done.wait, WAIT))
            # The worker's finished stage is removed without another request.
            while self.api._orphan_stages or self.stages():
                await asyncio.sleep(0)
        self.assertEqual(self.stages(), [])


class TestRekeyDocuments(RestoreTestCase):
    """M4: an intact body with a damaged key or length is re-keyed."""

    def library(self, damage):
        path = os.path.join(self.tmpdir, "uploads", "d.db")
        self.upload(["x"], "d.db")
        raw = b'{"nodes": [1, 2, 3]}'
        digest = hashlib.sha256(raw).digest()
        with closing(sqlite3.connect(path)) as conn:
            sha, length = damage(digest, len(raw))
            doc = conn.execute(
                "INSERT INTO workflow_documents (sha256, byte_length, data) "
                "VALUES (?,?,?)",
                (sha, length, zlib.compress(raw)),
            ).lastrowid
            prompt_id = conn.execute("SELECT id FROM prompts").fetchone()[0]
            conn.execute(
                "INSERT INTO generated_images (prompt_id, image_path, filename, "
                "workflow_document_id) VALUES (?, '/x/a.png', 'a.png', ?)",
                (prompt_id, doc),
            )
            conn.commit()
        return path, raw.decode()

    def staged_texts(self, staged):
        with closing(sqlite3.connect(staged.path)) as conn:
            return [
                wd.read_stored_text(conn, None, row[0])
                for row in conn.execute(
                    "SELECT workflow_document_id FROM generated_images"
                )
            ]

    def test_a_damaged_key_is_recomputed(self):
        def flip(digest, length):
            bad = bytearray(digest)
            bad[0] ^= 1
            return bytes(bad), length

        path, text = self.library(flip)
        staged = self.api.db.model.stage_restore(path)
        try:
            self.assertEqual(
                staged.documents, {"repaired": 1, "cleared": 0, "images": 0}
            )
            self.assertEqual(self.staged_texts(staged), [text])
        finally:
            staged.discard()

    def test_a_damaged_length_is_recomputed(self):
        path, text = self.library(lambda digest, length: (digest, length + 7))
        staged = self.api.db.model.stage_restore(path)
        try:
            self.assertEqual(staged.documents["repaired"], 1)
            self.assertEqual(self.staged_texts(staged), [text])
        finally:
            staged.discard()

    def test_a_key_that_another_document_holds_is_merged_into_it(self):
        path, text = self.library(lambda digest, length: (b"\0" * 32, length))
        with closing(sqlite3.connect(path)) as conn:
            raw = text.encode()
            conn.execute(
                "INSERT INTO workflow_documents (sha256, byte_length, data) "
                "VALUES (?,?,?)",
                (hashlib.sha256(raw).digest(), len(raw), zlib.compress(raw)),
            )
            conn.commit()
        staged = self.api.db.model.stage_restore(path)
        try:
            self.assertEqual(staged.documents["repaired"], 1)
            self.assertEqual(self.staged_texts(staged), [text])
            with closing(sqlite3.connect(staged.path)) as conn:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM workflow_documents").fetchone(),
                    (1,),
                )
        finally:
            staged.discard()


class TestRepairReadsOneRowAtATime(RestoreTestCase):
    """M7: repair never iterates a managed cursor (128-row prefetch)."""

    def test_repair_uses_fetchone(self):
        upload = os.path.join(self.tmpdir, "uploads", "dmg.db")
        os.makedirs(os.path.dirname(upload), exist_ok=True)
        src = PromptDatabase(upload)
        prompt_id = self.save("with a workflow", src)
        image = src.link_image_to_prompt(
            prompt_id, os.path.join(self.tmpdir, "a.png"), {"workflow": {"v": 1}}
        )
        conn = src.model.get_connection()
        with conn:
            text = wd.read_stored_text(
                conn,
                None,
                conn.execute(
                    "SELECT workflow_document_id FROM generated_images WHERE id=?",
                    (image,),
                ).fetchone()[0],
            )
            conn.execute(
                "INSERT INTO generated_images (prompt_id, image_path, filename, "
                "workflow_data) VALUES (?, '/x/b.png', 'b.png', ?)",
                (prompt_id, text),
            )
            conn.execute("UPDATE workflow_documents SET data=x'00'")
        try:
            with patch.object(
                ManagedCursor, "fetchmany", side_effect=AssertionError("prefetch")
            ):
                result = wd.repair_damaged_documents(conn)
            self.assertEqual(result["repaired"], 1)
        finally:
            src.close_all()


class TestFingerprintTransaction(RestoreTestCase):
    """N3: the fingerprint never commits a transaction it did not open."""

    def test_a_callers_transaction_is_left_open(self):
        conn = self.api.db.model.get_connection()
        conn.execute("BEGIN")
        conn.execute("INSERT INTO prompts (text, hash) VALUES ('pending', 'p')")
        restore_stage.live_fingerprint(conn)
        self.assertTrue(conn.in_transaction)
        conn.rollback()
        self.assertEqual([p["text"] for p in self.api.db.search_prompts()], [])
