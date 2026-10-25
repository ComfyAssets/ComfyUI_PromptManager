"""Reviewed restore and verified backup routes (#249 System S8).

- ``POST /prompt_manager/restore/preview`` stages an upload and writes
  nothing live; ``DELETE .../preview/{token}`` discards it;
  ``POST /prompt_manager/restore/apply`` starts the ``exclusive`` restore
  job that publishes exactly the reviewed bytes.
- A restore and the health check, maintenance, Move library, a Needs review
  decision and a maintenance preview refuse each other or wait (carry-overs
  1 and 2).
- ``GET /prompt_manager/backup`` verifies its snapshot before sending a byte;
  ``GET /prompt_manager/backup/status`` lists the safety backups on disk and
  this session's verified download.

Ordering is driven by events and job tasks, never by timers.
"""

import asyncio
import glob
import hashlib
import os
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import aiohttp

from database import restore_stage
from database.models import PromptModel
from database.operations import PromptDatabase
from py.api import restore_plans
from py.api.backup_restore import RESTORE_JOB_TITLE
from utils.hashing import generate_prompt_hash

try:
    from tests.test_api_admin import AdminAPITestCase  # noqa: E402
except ImportError:  # discovered with tests/ as the top-level directory
    from test_api_admin import AdminAPITestCase  # noqa: E402

WAIT = 60  # a ceiling only a hung test reaches
PREVIEW = "/prompt_manager/restore/preview"
APPLY = "/prompt_manager/restore/apply"
SUMMARY_KEYS = {
    "prompts",
    "positives",
    "pairs",
    "archived_pairs",
    "image_links",
    "runs",
    "saved_views",
}


def _texts(path):
    with closing(sqlite3.connect(path)) as conn:
        return sorted(r[0] for r in conn.execute("SELECT text FROM prompts"))


class RestoreTestCase(AdminAPITestCase):
    @property
    def db_path(self):
        return os.path.join(self.tmpdir, "prompts.db")

    def save(self, text, db=None):
        return (db or self.api.db).save_prompt(
            text=text, prompt_hash=generate_prompt_hash(text)
        )

    def upload(self, texts, name="upload.db"):
        folder = os.path.join(self.tmpdir, "uploads")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        db = PromptDatabase(path)
        for text in texts:
            self.save(text, db)
        db.close_all()
        return Path(path).read_bytes()

    def form(self, data):
        form = aiohttp.FormData()
        form.add_field(
            "database_file",
            data,
            filename="backup.db",
            content_type="application/octet-stream",
        )
        return form

    async def preview(self, data, status=200):
        resp = await self.client.post(PREVIEW, data=self.form(data))
        body = await resp.json()
        self.assertEqual(resp.status, status, body)
        return body

    async def apply(self, token, status=202):
        resp = await self.client.post(APPLY, json={"token": token})
        body = await resp.json()
        self.assertEqual(resp.status, status, body)
        return body

    async def finish(self, job_id):
        job = self.api._jobs.get(job_id)
        await asyncio.wait_for(asyncio.shield(job.task), WAIT)
        return job

    def live_digest(self):
        return {
            suffix: hashlib.sha256(Path(self.db_path + suffix).read_bytes()).hexdigest()
            for suffix in ("", "-wal")
            if os.path.exists(self.db_path + suffix)
        }

    def backups(self):
        return sorted(glob.glob(self.db_path + ".backup_*"))

    def stages(self):
        return sorted(
            name
            for name in os.listdir(self.tmpdir)
            if name.startswith(restore_stage.STAGE_PREFIX)
        )

    def gated_job(self, kind, admission, title=None):
        gate = asyncio.Event()

        async def runner(job):
            await gate.wait()

        job = self.api._jobs.start(
            kind, title or kind.title(), admission=admission, scope={}, runner=runner
        )
        self.addAsyncCleanup(self._release, job, gate)
        return job, gate

    async def _release(self, job, gate):
        gate.set()
        await asyncio.gather(job.task, return_exceptions=True)


class TestPreview(RestoreTestCase):
    async def test_preview_never_replaces_or_backs_up(self):
        self.save("live one")
        self.api.db.model.get_connection()
        before = self.live_digest()
        body = await self.preview(self.upload(["restored a", "restored b"]))
        self.assertEqual(self.live_digest(), before)
        self.assertEqual(self.backups(), [])
        self.assertEqual(
            [p["text"] for p in self.api.db.search_prompts()], ["live one"]
        )
        self.assertEqual(set(body["summary"]), SUMMARY_KEYS)
        self.assertEqual(set(body["live"]), SUMMARY_KEYS)
        self.assertEqual((body["summary"]["prompts"], body["live"]["prompts"]), (2, 1))
        self.assertEqual(body["schema"], {"migrated": False, "library_state": True})
        self.assertEqual(body["integrity"], "ok")
        self.assertEqual(body["documents"], {"repaired": 0, "cleared": 0, "images": 0})
        self.assertEqual(
            body["safety_backup"],
            {"destination_label": "prompts.db.backup_<date and time>"},
        )
        self.assertNotIn(self.tmpdir, str(body))
        # Staged in the library's folder; the upload itself is gone.
        self.assertEqual(len(self.stages()), 1)
        self.assertEqual(
            [n for n in os.listdir(self.tmpdir) if "upload" in n and "pm-" in n], []
        )
        self.assertEqual(self.api._jobs.list(), [])

    async def test_invalid_upload_is_400_and_leaves_nothing(self):
        resp = await self.client.post(PREVIEW, data=self.form(b"not a database"))
        body = await resp.json()
        self.assertEqual((resp.status, body["code"]), (400, "invalid-backup"))
        self.assertEqual(self.stages(), [])

    async def test_expired_or_discarded_token_is_410(self):
        body = await self.preview(self.upload(["restored"]))
        resp = await self.client.delete(f"{PREVIEW}/{body['token']}")
        self.assertEqual(resp.status, 200)
        self.assertEqual(self.stages(), [])
        refused = await self.apply(body["token"], status=410)
        self.assertEqual(refused["code"], "plan-expired")
        resp = await self.client.delete(f"{PREVIEW}/{body['token']}")
        self.assertEqual(resp.status, 410)

        clock = [1000.0]
        self.api._restore_plan_store = restore_plans.RestorePlanStore(
            clock=lambda: clock[0]
        )
        body = await self.preview(self.upload(["restored"], "second.db"))
        clock[0] += restore_plans.STAGE_TTL_SECONDS + 1
        refused = await self.apply(body["token"], status=410)
        self.assertEqual(refused["code"], "plan-expired")
        self.assertEqual(self.stages(), [])

    async def test_stage_budget_429(self):
        data = self.upload(["restored"])
        first = await self.preview(data)
        await self.preview(data)
        refused = await self.preview(data, status=429)
        self.assertEqual(refused["code"], "too-large")
        self.assertEqual(len(self.stages()), 2)
        await self.client.delete(f"{PREVIEW}/{first['token']}")
        await self.preview(data)

    async def test_preview_reports_repaired_documents(self):
        upload = os.path.join(self.tmpdir, "uploads", "damaged.db")
        os.makedirs(os.path.dirname(upload), exist_ok=True)
        src = PromptDatabase(upload)
        prompt_id = self.save("with a workflow", src)
        image = src.link_image_to_prompt(
            prompt_id, os.path.join(self.tmpdir, "a.png"), {"workflow": {"v": 1}}
        )
        conn = src.model.get_connection()
        with conn:
            conn.execute(
                "UPDATE workflow_documents SET data=x'00' WHERE id=(SELECT "
                "workflow_document_id FROM generated_images WHERE id=?)",
                (image,),
            )
        src.close_all()
        body = await self.preview(Path(upload).read_bytes())
        self.assertEqual(body["documents"], {"repaired": 0, "cleared": 1, "images": 1})


class TestApply(RestoreTestCase):
    async def test_apply_restores_and_logs(self):
        self.save("old")
        old_db = self.api.db
        body = await self.preview(self.upload(["restored a", "restored b"]))
        started = await self.apply(body["token"])
        summary = started["job"]
        self.assertEqual(
            (summary["kind"], summary["title"], summary["cancellable"]),
            ("restore", RESTORE_JOB_TITLE, False),
        )
        self.assertEqual(summary["admission"], "exclusive")
        job = await self.finish(summary["id"])
        self.assertEqual(job.state, "finished", job.log)
        self.assertIsNot(self.api.db, old_db)
        self.assertEqual(_texts(self.db_path), ["restored a", "restored b"])
        [backup] = self.backups()
        self.assertEqual(_texts(backup), ["old"])
        log = "\n".join(job.log)
        self.assertIn(f"Safety backup {os.path.basename(backup)}", log)
        self.assertIn("Restored 2 prompts", log)
        self.assertNotIn(self.tmpdir, log)
        self.assertEqual(self.stages(), [])
        wal = self.db_path + "-wal"
        self.assertEqual(os.path.getsize(wal) if os.path.exists(wal) else 0, 0)
        used = await self.apply(body["token"], status=409)
        self.assertEqual(used["code"], "plan-used")
        self.assertEqual(used["job"]["id"], summary["id"])

    async def test_apply_binds_exact_staged_bytes(self):
        self.save("live")
        body = await self.preview(self.upload(["restored"]))
        held = self.api._restore_plans.peek(body["token"])
        with closing(sqlite3.connect(held.staged.path)) as conn:
            conn.execute("INSERT INTO prompts (text, hash) VALUES ('sneaked', 'x')")
            conn.commit()
        started = await self.apply(body["token"])
        job = await self.finish(started["job"]["id"])
        self.assertEqual(job.state, "failed")
        self.assertEqual(job.last_event["code"], "plan-changed")
        self.assertEqual(_texts(self.db_path), ["live"])
        self.assertEqual(self.backups(), [])
        self.assertEqual(self.stages(), [])

    async def test_live_change_after_preview_is_409(self):
        self.save("live")
        body = await self.preview(self.upload(["restored"]))
        self.save("written after the review")
        refused = await self.apply(body["token"], status=409)
        self.assertEqual(refused["code"], "plan-changed")
        self.assertEqual(
            sorted(p["text"] for p in self.api.db.search_prompts()),
            ["live", "written after the review"],
        )
        self.assertEqual(self.stages(), [])
        self.assertEqual(self.api._jobs.list(), [])

    async def test_publish_failure_keeps_safety_backup_and_reports(self):
        self.save("live")
        body = await self.preview(self.upload(["restored"]))
        failure = sqlite3.OperationalError("injected page copy failure")
        with patch.object(PromptModel, "_replace_contents_from", side_effect=failure):
            started = await self.apply(body["token"])
            job = await self.finish(started["job"]["id"])
        self.assertEqual(job.state, "failed")
        self.assertEqual(job.last_event["code"], "restore-failed")
        self.assertIn("The safety backup was kept.", job.status_line)
        [backup] = self.backups()
        self.assertEqual(_texts(backup), ["live"])
        self.assertEqual([p["text"] for p in self.api.db.search_prompts()], ["live"])
        self.assertEqual(self.stages(), [])

    async def test_jobs_survive_db_swap(self):
        earlier, gate = self.gated_job("health", "read", "Health check")
        gate.set()
        await earlier.task
        body = await self.preview(self.upload(["restored"]))
        job = await self.finish((await self.apply(body["token"]))["job"]["id"])
        self.assertEqual(job.state, "finished")
        listed = (await (await self.client.get("/prompt_manager/jobs")).json())["jobs"]
        self.assertEqual(
            [(j["kind"], j["state"]) for j in listed],
            [("restore", "finished"), ("health", "finished")],
        )


class TestRestoreAdmission(RestoreTestCase):
    """A restore never interleaves with other library work (carry-overs 1, 2)."""

    async def test_restore_job_refused_while_maintenance_runs(self):
        self.gated_job("maintenance", "records", "Duplicate prompt text")
        body = await self.preview(self.upload(["restored"]))
        refused = await self.apply(body["token"], status=409)
        self.assertEqual(refused["code"], "busy")
        self.assertEqual(refused["running"]["title"], "Duplicate prompt text")
        self.api._restore_plans.peek(body["token"])  # the token stays usable

    async def test_restore_and_health_check_refuse_each_other(self):
        health, gate = self.gated_job("health", "read", "Health check")
        body = await self.preview(self.upload(["restored"]))
        refused = await self.apply(body["token"], status=409)
        self.assertEqual(refused["running"]["title"], "Health check")
        legacy = await self.client.post(
            "/prompt_manager/restore", data=self.form(self.upload(["x"], "x.db"))
        )
        self.assertEqual(legacy.status, 409)
        gate.set()
        await health.task

        restore, release = self.gated_job("restore", "exclusive", RESTORE_JOB_TITLE)
        resp = await self.client.post("/prompt_manager/system/health")
        refusal = await resp.json()
        self.assertEqual((resp.status, refusal["code"]), (409, "busy"))
        self.assertEqual(refusal["running"]["title"], RESTORE_JOB_TITLE)

    async def test_restore_and_move_library_refuse_each_other(self):
        self.gated_job("library_move", "exclusive", "Move library")
        body = await self.preview(self.upload(["restored"]), status=409)
        self.assertEqual(body["running"]["title"], "Move library")

        self.api._jobs.list()[0].finish()
        body = await self.preview(self.upload(["restored"]))
        self.gated_job("restore", "exclusive", RESTORE_JOB_TITLE)
        resp = await self.client.post("/prompt_manager/system/library/move")
        self.assertEqual(resp.status, 409)
        self.assertEqual((await resp.json())["running"]["title"], RESTORE_JOB_TITLE)

    async def test_decision_and_preview_refused_while_restore_runs(self):
        self.gated_job("restore", "exclusive", RESTORE_JOB_TITLE)
        for url, payload in (
            ("/prompt_manager/review/items/1/decision", {"choice": "unresolved"}),
            ("/prompt_manager/maintenance/preview", {"operation": "optimize"}),
        ):
            resp = await self.client.post(url, json=payload)
            body = await resp.json()
            self.assertEqual((resp.status, body["code"]), (409, "busy"), url)
            self.assertEqual(body["running"]["title"], RESTORE_JOB_TITLE)
        resp = await self.client.post(PREVIEW, data=self.form(self.upload(["r"])))
        self.assertEqual(resp.status, 409)

    async def test_restore_waits_for_a_running_decision(self):
        self.save("live")
        body = await self.preview(self.upload(["restored"]))
        entered, release = threading.Event(), threading.Event()
        loop = asyncio.get_running_loop()

        def slow_item(item_id):
            entered.set()
            release.wait(WAIT)
            return None

        with patch.object(self.api.db, "review_item", side_effect=slow_item):
            decision = asyncio.ensure_future(
                self.client.post(
                    "/prompt_manager/review/items/1/decision",
                    json={
                        "expected_epoch": "00000000-0000-4000-8000-000000000000",
                        "expected_revision": 0,
                        "capture_token": None,
                        "sha256": "0" * 64,
                        "dependency_fingerprint": "0" * 64,
                        "choice": "unresolved",
                    },
                )
            )
            self.assertTrue(await loop.run_in_executor(None, entered.wait, WAIT))
            self.assertEqual(self.api._jobs.leases, {"Needs review decision": 1})
            # The apply's own check takes a lease too: it shares with the decision.
            started = await self.apply(body["token"])
            job = self.api._jobs.get(started["job"]["id"])
            while job.status_line != "Waiting for other requests to finish":
                await asyncio.sleep(0)
            self.assertEqual(_texts(self.db_path), ["live"])
            release.set()
            answered = await decision
        self.assertNotEqual(answered.status, 409)
        await self.finish(job.id)
        self.assertEqual(job.state, "finished", job.log)
        self.assertEqual(_texts(self.db_path), ["restored"])


class TestWorkerHandles(RestoreTestCase):
    """Carry-over 3: no worker holds the library open across a switch."""

    async def test_preview_workers_pause_and_library_connections_are_released(self):
        restore, _gate = self.gated_job("restore", "exclusive", RESTORE_JOB_TITLE)
        # Automatic previews (thumbnail workers) write nothing while it runs;
        # they never open the library file at all.
        self.assertIs(self.api._auto_thumbnail_blocker(), restore)

    async def test_a_restore_leaves_no_handle_on_the_stage_or_upload(self):
        from tests.open_handles import open_handles_under

        self.save("live")
        body = await self.preview(self.upload(["restored"]))
        job = await self.finish((await self.apply(body["token"]))["job"]["id"])
        self.assertEqual(job.state, "finished", job.log)
        leftovers = [
            hit
            for hit in open_handles_under(self.tmpdir)
            if hit.startswith(".pm-") or hit.startswith("staged")
        ]
        self.assertEqual(leftovers, [])
        self.assertEqual(
            [n for n in os.listdir(self.tmpdir) if n.startswith(".pm-")], []
        )


class TestLegacyRestore(RestoreTestCase):
    async def test_legacy_restore_unchanged(self):
        self.save("old")
        resp = await self.client.post(
            "/prompt_manager/restore", data=self.form(self.upload(["a", "b"]))
        )
        body = await resp.json()
        self.assertEqual(resp.status, 200, body)
        self.assertEqual(
            set(body), {"success", "message", "prompt_count", "backup_created"}
        )
        self.assertEqual(
            body["message"], "Database restored successfully. Found 2 prompts."
        )
        self.assertEqual(self.stages(), [])
        [job] = self.api._jobs.list()
        self.assertEqual((job.kind, job.admission), ("restore", "exclusive"))

    async def test_legacy_restore_reports_damaged_documents(self):
        upload = os.path.join(self.tmpdir, "uploads", "damaged.db")
        os.makedirs(os.path.dirname(upload), exist_ok=True)
        src = PromptDatabase(upload)
        prompt_id = self.save("with a workflow", src)
        src.link_image_to_prompt(
            prompt_id, os.path.join(self.tmpdir, "a.png"), {"workflow": {"v": 1}}
        )
        conn = src.model.get_connection()
        with conn:
            conn.execute("UPDATE workflow_documents SET data=x'00'")
        src.close_all()
        resp = await self.client.post(
            "/prompt_manager/restore", data=self.form(Path(upload).read_bytes())
        )
        body = await resp.json()
        self.assertEqual(resp.status, 200, body)
        self.assertEqual(
            body["workflow_documents"], {"repaired": 0, "cleared": 2, "images": 1}
        )  # the image's workflow and its prompt graph
        self.assertTrue(
            body["message"].endswith(
                "Could not repair 2 workflow documents: 1 image shows no workflow."
            ),
            body["message"],
        )
        ok, reason = PromptModel.verify_database_file(self.db_path)
        self.assertTrue(ok, reason)


class TestBackupDownload(RestoreTestCase):
    async def test_backup_verifies_snapshot_before_streaming(self):
        self.save("keep")
        with patch.object(
            PromptModel, "verify_database_file", return_value=(False, "bad")
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
        status = await (await self.client.get("/prompt_manager/backup/status")).json()
        self.assertIsNone(status["last_download"])

    async def test_temp_snapshot_removed_and_closed(self):
        self.save("keep")
        created = []
        from database import safety_backup

        original = safety_backup.safety_temp

        def spy(db_path):
            path = original(db_path)
            created.append(path)
            return path

        with patch("py.api.backup_restore.safety_temp", spy):
            resp = await self.client.get("/prompt_manager/backup")
            data = await resp.read()
        self.assertEqual(resp.status, 200)
        [temp] = created
        self.assertEqual(os.path.dirname(temp), self.tmpdir)
        self.assertFalse(os.path.exists(temp))
        downloaded = os.path.join(self.tmpdir, "downloaded.db")
        Path(downloaded).write_bytes(data)
        self.assertEqual(_texts(downloaded), ["keep"])
        with closing(sqlite3.connect(downloaded)) as conn:
            self.assertEqual(
                conn.execute("PRAGMA journal_mode").fetchone()[0], "delete"
            )

    async def test_status_null_download_on_fresh_server(self):
        resp = await self.client.get("/prompt_manager/backup/status")
        body = await resp.json()
        self.assertEqual(
            body, {"success": True, "safety_backups": [], "last_download": None}
        )
        await (await self.client.get("/prompt_manager/backup")).read()
        body = await (await self.client.get("/prompt_manager/backup/status")).json()
        download = body["last_download"]
        self.assertEqual(set(download), {"at", "bytes", "verified"})
        self.assertTrue(download["verified"])
        self.assertGreater(download["bytes"], 0)

    async def test_status_lists_safety_backups_from_disk(self):
        for index in range(12):
            path = Path(f"{self.db_path}.backup_2026010{index % 10}_00000{index}")
            path.write_bytes(b"x" * (index + 1))
            os.utime(path, (1_000_000 + index, 1_000_000 + index))
        Path(self.tmpdir, "other.db.backup_1").write_bytes(b"x")
        body = await (await self.client.get("/prompt_manager/backup/status")).json()
        listed = body["safety_backups"]
        self.assertEqual(len(listed), 10)
        self.assertEqual(listed[0]["bytes"], 12)
        self.assertEqual(listed[-1]["bytes"], 3)
        for row in listed:
            self.assertEqual(set(row), {"name", "bytes", "modified_at"})
            self.assertTrue(row["name"].startswith("prompts.db.backup_"))
            self.assertNotIn(os.sep, row["name"])
        self.assertNotIn(self.tmpdir, str(body))

    async def test_backup_refused_while_restore_runs(self):
        self.gated_job("restore", "exclusive", RESTORE_JOB_TITLE)
        resp = await self.client.get("/prompt_manager/backup")
        self.assertEqual(resp.status, 409)
        self.assertEqual(
            await resp.json(),
            {"success": False, "error": f"{RESTORE_JOB_TITLE} already running"},
        )

    async def test_backup_is_listed_while_it_streams(self):
        self.save("keep")
        resp = await self.client.get("/prompt_manager/backup")
        await resp.read()
        [job] = self.api._jobs.list()
        self.assertEqual(
            (job.kind, job.title, job.admission, job.state),
            ("backup", "Database backup", "guarded", "finished"),
        )
