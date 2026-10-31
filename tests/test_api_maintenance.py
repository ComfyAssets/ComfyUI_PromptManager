"""Maintenance routes (py/api/maintenance.py, #249 System S6).

``POST /prompt_manager/maintenance/preview`` answers a reviewed ``Plan`` for
one of four operations and writes nothing; its token binds a later apply to
exactly that plan. Errors are coded and never carry a server path or an
exception text.
"""

import asyncio
import datetime
import hashlib
import json
import re
import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_mock_server = MagicMock()
_mock_server.PromptServer.instance.routes = MagicMock()
sys.modules.setdefault("server", _mock_server)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import safety_backup  # noqa: E402
from database.operations import PromptDatabase  # noqa: E402
from py.api import PromptManagerAPI  # noqa: E402
from py.api.maintenance_plans import PlanExpired  # noqa: E402
from py.config import GalleryConfig, PromptManagerConfig  # noqa: E402
from utils.hashing import generate_prompt_hash  # noqa: E402

try:
    from tests.test_api_admin import AdminAPITestCase  # noqa: E402
except ImportError:  # discovered with tests/ as the top-level directory
    from test_api_admin import AdminAPITestCase  # noqa: E402

WAIT = 10
PREVIEW = "/prompt_manager/maintenance/preview"
OPERATIONS = ("duplicate_prompts", "optimize", "unused_components", "missing_links")
PLAN_KEYS = {
    "success",
    "token",
    "operation",
    "expires_at",
    "scope",
    "protected",
    "backup",
    "media",
    "details",
    "disk",
}
DISK_KEYS = {"free_bytes", "required_bytes", "sufficient"}


def _digest(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class MaintenanceAPITestCase(AdminAPITestCase):
    async def get_application(self):
        app = await super().get_application()
        self.api._find_comfyui_output_dir = lambda: (
            str(self.output_dir) if self.output_dir.is_dir() else None
        )
        return app

    def save(self, text, **metadata):
        return self.api.db.save_prompt(
            text, prompt_hash=generate_prompt_hash(text), **metadata
        )

    def image(self, prompt_id, name, *, exists=True, folder=None):
        path = Path(folder or self.output_dir) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if exists:
            path.write_bytes(b"png")
        return self.api.db.link_image_to_prompt(prompt_id, str(path))

    def database_bytes(self):
        conn = sqlite3.connect(self.api.db.model.db_path)
        try:
            size = conn.execute("PRAGMA page_size").fetchone()[0]
            return size * conn.execute("PRAGMA page_count").fetchone()[0]
        finally:
            conn.close()

    async def post(self, url, body, status):
        response = await self.client.request("POST", url, json=body)
        text = await response.text()
        self.assertEqual(response.status, status, text)
        self.assertNotIn(self.tmpdir, text)
        return json.loads(text)

    async def preview(self, operation, status=200, **body):
        return await self.post(PREVIEW, {"operation": operation, **body}, status)


class TestPreviewRoute(MaintenanceAPITestCase):
    async def test_preview_answers_a_plan_for_each_operation(self):
        prompt = self.save("Kept")
        self.image(prompt, "gone.png", exists=False)
        self.save("Unused")
        for operation in OPERATIONS:
            with self.subTest(operation=operation):
                plan = await self.preview(operation, options={})
                self.assertEqual(set(plan), PLAN_KEYS)
                self.assertTrue(plan["success"])
                self.assertEqual(plan["operation"], operation)
                self.assertIsInstance(plan["token"], str)
                self.assertEqual(set(plan["scope"]), {"count", "label"})
                self.assertEqual(set(plan["protected"]), {"count", "reasons"})
                expected = plan["backup"].pop("expected_bytes")
                self.assertEqual(
                    plan["backup"],
                    {
                        "policy": "on",
                        "destination_label": "prompts.db.backup_<date and time>",
                    },
                )
                self.assertEqual(expected, self.database_bytes())
                self.assertEqual(set(plan["disk"]), DISK_KEYS)
                self.assertFalse(plan["media"]["deletes_files"])
                self.assertLessEqual(len(plan["details"]), 50)
                self.assertTrue(plan["expires_at"].endswith("+00:00"))
                self.assertNotIn("fingerprint", json.dumps(plan))
        missing = await self.preview("missing_links")
        self.assertEqual(
            missing["scope"], {"count": 1, "label": "1 link to a missing file"}
        )
        unused = await self.preview("unused_components")
        self.assertEqual(unused["scope"]["count"], 1)

    async def test_preview_writes_nothing(self):
        prompt = self.save("Kept")
        self.image(prompt, "kept.png")
        self.image(prompt, "gone.png", exists=False)
        self.save("Unused")
        db_path = self.api.db.model.db_path
        self.api.db.get_statistics()
        files = [db_path, db_path + "-wal"]
        for operation in OPERATIONS:
            with self.subTest(operation=operation):
                before = [_digest(path) for path in files]
                shm = os.path.getsize(db_path + "-shm")
                media = {p.name: _digest(p) for p in self.output_dir.iterdir()}
                await self.preview(operation)
                self.assertEqual([_digest(path) for path in files], before)
                self.assertEqual(os.path.getsize(db_path + "-shm"), shm)
                self.assertEqual(
                    {p.name: _digest(p) for p in self.output_dir.iterdir()}, media
                )
        self.assertEqual(
            [p["text"] for p in self.api.db.search_prompts()], ["Unused", "Kept"]
        )

    async def test_unknown_operation_is_400(self):
        cases = [
            {"operation": "reboot"},
            {"operation": "duplicate_files"},  # S6 Task 17, not yet
            {"operation": 3},
            {},
            {"operation": "optimize", "options": {"all": True}},
            {"operation": "optimize", "options": []},
            {"operation": "optimize", "extra": 1},
        ]
        for body in cases:
            with self.subTest(body=body):
                reply = await self.post(PREVIEW, body, 400)
                self.assertEqual(reply["success"], False)
                self.assertEqual(reply["code"], "invalid-request")
        response = await self.client.request(
            "POST", PREVIEW, data=b"[1]", headers={"Content-Type": "application/json"}
        )
        self.assertEqual(response.status, 400)
        self.assertEqual((await response.json())["code"], "invalid-request")
        big = json.dumps({"operation": "optimize", "pad": "x" * 8192})
        response = await self.client.request(
            "POST", PREVIEW, data=big, headers={"Content-Type": "application/json"}
        )
        self.assertEqual(response.status, 413)
        self.assertEqual((await response.json())["code"], "too-large")

    async def test_preview_failure_is_coded_without_exception_text(self):
        with patch.object(
            self.api.db,
            "preview_maintenance",
            side_effect=sqlite3.OperationalError(f"boom {self.tmpdir}"),
        ):
            with self.assertLogs("prompt_manager", "ERROR"):
                reply = await self.preview("optimize", status=500)
        self.assertEqual(
            reply,
            {"success": False, "code": None, "error": "Failed to review maintenance"},
        )

    async def test_scope_too_large_is_413(self):
        from py.api.maintenance_plans import PlanStore

        self.api._maintenance_plans = PlanStore(max_bytes=10)
        self.save("Unused")
        reply = await self.preview("unused_components", status=413)
        self.assertEqual(reply["code"], "too-large")

    async def test_preview_during_a_restore_swap_is_storage_changed(self):
        real = self.api.db.preview_maintenance

        def swapped(*args, **kwargs):
            draft = real(*args, **kwargs)
            self.api._db_generation += 1  # a restore replaced the database
            return draft

        with patch.object(self.api.db, "preview_maintenance", side_effect=swapped):
            reply = await self.preview("optimize", status=409)
        self.assertEqual(reply["code"], "storage-changed")

    async def test_missing_links_preview_uses_the_health_roots(self):
        unmounted = Path(self.tmpdir) / "unmounted"
        unmounted.mkdir()
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.output_dir), str(unmounted)]
        prompt = self.save("Links")
        self.image(prompt, "gone.png", exists=False)
        self.image(prompt, "away.png", folder=unmounted, exists=False)
        unmounted.rmdir()
        plan = await self.preview("missing_links")
        self.assertEqual(plan["scope"]["count"], 1)
        self.assertEqual(
            plan["protected"],
            {"count": 1, "reasons": ["1 link under an unavailable source"]},
        )

    async def test_restart_loses_plans(self):
        plan = await self.preview("optimize")
        self.assertEqual(
            self.api._maintenance_plans.peek(plan["token"], "optimize").operation,
            "optimize",
        )
        restarted = PromptManagerAPI(db=PromptDatabase(self.api.db.model.db_path))
        try:
            with self.assertRaises(PlanExpired):
                restarted._maintenance_plans.peek(plan["token"], "optimize")
        finally:
            restarted.db.close_all()


APPLY = "/prompt_manager/maintenance/apply"
BACKUP_NAME = re.compile(r"^prompts\.db\.backup_\d{8}_\d{6}_\d{6}(_\d+)?$")


class ApplyTestCase(MaintenanceAPITestCase):
    def backups(self):
        return sorted(
            name
            for name in os.listdir(self.tmpdir)
            if name.startswith("prompts.db.backup_")
        )

    def strays(self):
        """Files beside the database that are neither it nor a safety backup."""
        known = {"prompts.db", "prompts.db-wal", "prompts.db-shm", "config.json"}
        return sorted(
            name
            for name in os.listdir(self.tmpdir)
            if os.path.isfile(os.path.join(self.tmpdir, name))
            and name not in known
            and not BACKUP_NAME.match(name)
        )

    async def apply(self, token, status=202):
        return await self.post(APPLY, {"token": token}, status)

    async def run_apply(self, operation):
        plan = await self.preview(operation)
        body = await self.apply(plan["token"])
        job = self.api._jobs.get(body["job"]["id"])
        await asyncio.wait_for(asyncio.shield(job.task), WAIT)
        return plan, body["job"], job


class TestApplyRoute(ApplyTestCase):
    async def test_apply_runs_as_a_records_job_with_a_verified_backup(self):
        orphan = self.save("Orphan")
        plan, summary, job = await self.run_apply("unused_components")
        self.assertEqual(summary["kind"], "maintenance")
        self.assertEqual(summary["title"], "Unused prompt components")
        self.assertEqual(summary["admission"], "records")
        self.assertFalse(summary["cancellable"])
        self.assertEqual(
            summary["counters"], {"changed": 0, "protected": 0, "errors": 0}
        )
        self.assertEqual(summary["scope"], {"operation": "unused_components"})
        self.assertEqual(job.state, "finished", job.log)
        self.assertEqual(job.counters, {"changed": 1, "protected": 0, "errors": 0})
        self.assertIsNone(self.api.db.get_prompt_by_id(orphan))
        [backup] = self.backups()
        self.assertRegex(backup, BACKUP_NAME)
        ok, _reason = self.api.db.model.verify_database_file(
            os.path.join(self.tmpdir, backup)
        )
        self.assertTrue(ok)
        self.assertEqual(self.strays(), [])
        self.assertIn(f"Safety backup {backup} verified", "\n".join(job.log))
        self.assertEqual(
            job.status_line,
            f"Removed 1 unused prompt component · Safety backup {backup}",
        )
        self.assertEqual(job.outcomes["changed"][0]["detail"], "Removed: Orphan")
        report = json.dumps(job.report())
        self.assertNotIn(self.tmpdir, report)
        if os.name == "nt":  # pragma: no cover - native path spelling
            self.assertNotIn(self.tmpdir.replace("\\", "/"), report)

    async def test_apply_rejects_changed_target(self):
        orphan, soon = self.save("Orphan"), self.save("Soon a recipe")
        plan = await self.preview("unused_components")
        with self.api.db.model.get_connection() as conn:
            conn.execute(
                "INSERT INTO prompt_library_state(prompt_id,title) VALUES(?,?)",
                (soon, "Recipe"),
            )
        body = await self.apply(plan["token"])
        job = self.api._jobs.get(body["job"]["id"])
        await asyncio.wait_for(asyncio.shield(job.task), WAIT)
        self.assertEqual(job.state, "failed")
        self.assertEqual(job.last_event["code"], "plan-changed")
        self.assertEqual(
            job.status_line,
            "The library changed since the review; nothing was changed. "
            "Review the plan again.",
        )
        self.assertEqual(job.counters["errors"], 1)
        self.assertIsNotNone(self.api.db.get_prompt_by_id(orphan))
        self.assertIsNotNone(self.api.db.get_prompt_by_id(soon))

    async def test_token_is_single_use(self):
        self.save("Orphan")
        plan, summary, job = await self.run_apply("unused_components")
        again = await self.apply(plan["token"], 409)
        self.assertEqual(again["code"], "plan-used")
        self.assertEqual(
            again["job"], {"id": summary["id"], "title": "Unused prompt components"}
        )
        kinds = [j.kind for j in self.api._jobs.list()]
        self.assertEqual(kinds.count("maintenance"), 1)

    async def test_apply_refused_while_restore_runs(self):
        orphan = self.save("Orphan")
        plan = await self.preview("unused_components")
        async with self.api._jobs.hold(
            "restore", "Database restore (admin page)", admission="records"
        ) as restore:
            busy = await self.apply(plan["token"], 409)
            self.assertEqual(busy["code"], "busy")
            self.assertEqual(
                busy["running"],
                {"id": restore.id, "title": "Database restore (admin page)"},
            )
        self.assertIsNotNone(self.api.db.get_prompt_by_id(orphan))
        # The refused apply kept the token: it applies once the restore ended.
        body = await self.apply(plan["token"])
        job = self.api._jobs.get(body["job"]["id"])
        await asyncio.wait_for(asyncio.shield(job.task), WAIT)
        self.assertIsNone(self.api.db.get_prompt_by_id(orphan))

    async def test_backup_failure_aborts_before_write(self):
        orphan = self.save("Orphan")
        model = self.api.db.model
        failures = {
            "backup": patch.object(model, "backup_database", return_value=False),
            "verify": patch.object(
                model, "verify_database_file", return_value=(False, "broken")
            ),
        }
        for name, failure in failures.items():
            with self.subTest(failure=name):
                with failure, patch.object(self.api.db, "apply_maintenance") as applied:
                    _plan, _summary, job = await self.run_apply("unused_components")
                applied.assert_not_called()
                self.assertEqual(job.state, "failed")
                self.assertEqual(job.last_event["code"], "backup-failed")
                self.assertEqual(
                    job.status_line,
                    "The safety backup could not be taken and verified; "
                    "nothing was changed.",
                )
                self.assertIsNotNone(self.api.db.get_prompt_by_id(orphan))
                self.assertEqual(self.backups(), [])
                self.assertEqual(self.strays(), [])

    async def test_policy_off_reports_no_backup(self):

        PromptManagerConfig.SAFETY_BACKUP = False
        orphan = self.save("Orphan")
        plan, _summary, job = await self.run_apply("unused_components")
        self.assertEqual(plan["backup"]["policy"], "off")
        self.assertEqual(job.state, "finished")
        self.assertIn("No safety backup (the policy is off)", "\n".join(job.log))
        self.assertEqual(
            job.status_line, "Removed 1 unused prompt component · No safety backup"
        )
        self.assertEqual(self.backups(), [])
        self.assertIsNone(self.api.db.get_prompt_by_id(orphan))

    async def test_a_plan_reviewed_with_the_backup_on_keeps_that_promise(self):

        self.save("Orphan")
        plan = await self.preview("unused_components")
        PromptManagerConfig.SAFETY_BACKUP = False  # turned off after the review
        body = await self.apply(plan["token"])
        job = self.api._jobs.get(body["job"]["id"])
        await asyncio.wait_for(asyncio.shield(job.task), WAIT)
        self.assertEqual(len(self.backups()), 1)

    async def test_backup_verified_before_write(self):
        self.save("Orphan")
        model, db = self.api.db.model, self.api.db
        order = []
        real_backup, real_verify = model.backup_database, model.verify_database_file
        real_apply = db.apply_maintenance

        def backup(path):
            order.append("backup")
            return real_backup(path)

        def verify(path, **kwargs):
            order.append("verify")
            return real_verify(path, **kwargs)

        def apply(*args):
            order.append("apply")
            self.assertEqual(len(self.backups()), 1)  # in place before the write
            return real_apply(*args)

        with (
            patch.object(model, "backup_database", side_effect=backup),
            patch.object(model, "verify_database_file", side_effect=verify),
            patch.object(db, "apply_maintenance", side_effect=apply),
        ):
            _plan, _summary, job = await self.run_apply("unused_components")
        self.assertEqual(job.state, "finished")
        self.assertEqual(order, ["backup", "verify", "apply"])

    async def test_missing_links_never_deletes_files(self):
        prompt = self.save("Links")
        kept = self.image(prompt, "kept.png")
        gone = self.image(prompt, "sub/gone.png", exists=False)
        other = Path(self.tmpdir) / "elsewhere"
        self.image(prompt, "x.png", folder=other)
        media = {
            str(path): _digest(path)
            for path in Path(self.tmpdir).rglob("*")
            if path.is_file() and "prompts.db" not in path.name
        }
        _plan, _summary, job = await self.run_apply("missing_links")
        self.assertEqual(job.state, "finished", job.log)
        self.assertEqual(job.counters["changed"], 1)
        self.assertEqual(
            job.outcomes["changed"][0],
            {
                "path": "sub/gone.png",
                "root": "output",
                "root_index": 0,
                "detail": "Link removed; the file was already missing",
            },
        )
        after = {
            str(path): _digest(path)
            for path in Path(self.tmpdir).rglob("*")
            if path.is_file() and "prompts.db" not in path.name and str(path) in media
        }
        self.assertEqual(after, media)
        self.assertIsNone(self.api.db.get_image_by_id(gone))
        self.assertIsNotNone(self.api.db.get_image_by_id(kept))

    async def test_duplicate_prompts_and_optimize_apply(self):
        kept = self.save("A cat")
        with self.api.db.model.get_connection() as conn:
            copy = conn.execute(
                "INSERT INTO prompts(text, created_at) VALUES('A CAT', '2999-01-01')"
            ).lastrowid
        _plan, _summary, job = await self.run_apply("duplicate_prompts")
        self.assertEqual(job.state, "finished", job.log)
        self.assertEqual(job.status_line.split(" · ")[0], "Merged 1 duplicate prompt")
        self.assertEqual(
            job.outcomes["changed"][0]["detail"],
            f"Prompt {copy} merged into prompt {kept}",
        )
        self.assertIsNone(self.api.db.get_prompt_by_id(copy))
        _plan, _summary, job = await self.run_apply("optimize")
        self.assertEqual(job.state, "finished", job.log)
        self.assertEqual(job.status_line.split(" · ")[0], "Optimized the database")

    async def test_protected_items_are_counted(self):
        self.save("Orphan")
        self.save("Kept", tags=["__protected__"])
        _plan, _summary, job = await self.run_apply("unused_components")
        self.assertEqual(job.counters, {"changed": 1, "protected": 1, "errors": 0})

    async def test_apply_failure_changes_nothing_and_is_coded(self):
        orphan = self.save("Orphan")
        with patch.object(
            self.api.db,
            "apply_maintenance",
            side_effect=sqlite3.OperationalError(f"locked {self.tmpdir}"),
        ):
            with self.assertLogs("prompt_manager", "ERROR"):
                _plan, _summary, job = await self.run_apply("unused_components")
        self.assertEqual(job.state, "failed")
        self.assertEqual(job.last_event["code"], None)
        self.assertEqual(job.status_line, "Maintenance failed; nothing was changed.")
        self.assertNotIn(self.tmpdir, json.dumps(job.report()))
        self.assertIsNotNone(self.api.db.get_prompt_by_id(orphan))

    async def test_apply_errors_are_coded(self):
        missing = await self.post(APPLY, {}, 400)
        self.assertEqual(missing["code"], "invalid-request")
        wrong = await self.post(APPLY, {"token": 7}, 400)
        self.assertEqual(wrong["code"], "invalid-request")
        extra = await self.post(APPLY, {"token": "x", "force": True}, 400)
        self.assertEqual(extra["code"], "invalid-request")
        expired = await self.apply("not-a-token", 410)
        self.assertEqual(expired["code"], "plan-expired")

    async def test_a_restore_after_the_review_is_storage_changed(self):
        orphan = self.save("Orphan")
        plan = await self.preview("unused_components")
        self.api._database_replaced()  # what the legacy restore calls
        reply = await self.apply(plan["token"], 409)
        self.assertEqual(reply["code"], "storage-changed")
        again = await self.apply(plan["token"], 410)  # the plan is forgotten
        self.assertEqual(again["code"], "plan-expired")
        self.assertIsNotNone(self.api.db.get_prompt_by_id(orphan))

    async def test_restart_loses_plans_for_apply(self):
        plan = await self.preview("optimize")
        self.api._maintenance_plans = type(self.api._maintenance_plans)()
        reply = await self.apply(plan["token"], 410)
        self.assertEqual(reply["code"], "plan-expired")

    async def test_safety_backups_are_pruned(self):
        from py.api.admin import MAX_SAFETY_BACKUPS

        for index in range(MAX_SAFETY_BACKUPS):
            old = os.path.join(
                self.tmpdir, f"prompts.db.backup_20000101_0000{index:02d}"
            )
            with open(old, "wb") as handle:
                handle.write(b"old")
            os.utime(old, (1000 + index, 1000 + index))
        self.save("Orphan")
        await self.run_apply("unused_components")
        backups = self.backups()
        self.assertEqual(len(backups), MAX_SAFETY_BACKUPS)
        self.assertNotIn("prompts.db.backup_20000101_000000", backups)


class TestSafetyBackup(ApplyTestCase):
    async def test_backup_names_unique_within_one_second(self):
        from py.api import maintenance

        frozen = datetime.datetime(2026, 10, 23, 12, 0, 0, 123456)
        model = self.api.db.model
        with patch.object(safety_backup, "_now", return_value=frozen):
            names = [maintenance.take_safety_backup(model) for _ in range(3)]
        self.assertEqual(
            names,
            [
                "prompts.db.backup_20261023_120000_123456",
                "prompts.db.backup_20261023_120000_123456_1",
                "prompts.db.backup_20261023_120000_123456_2",
            ],
        )
        for name in names:
            ok, _reason = model.verify_database_file(os.path.join(self.tmpdir, name))
            self.assertTrue(ok, name)
        self.assertEqual(self.strays(), [])

    async def test_backup_is_private_on_posix(self):
        from py.api import maintenance

        name = maintenance.take_safety_backup(self.api.db.model)
        if os.name != "nt":
            mode = os.stat(os.path.join(self.tmpdir, name)).st_mode & 0o777
            self.assertEqual(mode, 0o600)

    async def test_a_failed_replace_leaves_no_file(self):
        from py.api import maintenance

        # A filesystem without hard links falls back to reserve-and-replace.
        with (
            patch.object(maintenance.os, "link", side_effect=OSError("no hard links")),
            patch.object(maintenance.os, "replace", side_effect=OSError("busy")),
        ):
            with self.assertRaises(maintenance.SafetyBackupFailed):
                maintenance.take_safety_backup(self.api.db.model)
        self.assertEqual(self.backups(), [])
        self.assertEqual(self.strays(), [])
        with patch.object(maintenance.os, "link", side_effect=FileExistsError):
            with self.assertRaises(maintenance.SafetyBackupFailed):
                maintenance.take_safety_backup(self.api.db.model)
        with patch.object(maintenance.os, "link", side_effect=OSError("no hard links")):
            name = maintenance.take_safety_backup(self.api.db.model)
        self.assertEqual(self.backups(), [name])
        self.assertEqual(self.strays(), [])

    async def test_a_backup_that_cannot_be_finished_leaves_no_file(self):
        from py.api import maintenance

        real_connect = sqlite3.connect
        calls = []

        def connect(*args, **kwargs):
            calls.append(args[0])
            if len(calls) == 2:  # the backup is written; finishing it fails
                raise sqlite3.OperationalError("x")
            return real_connect(*args, **kwargs)

        with patch.object(maintenance.sqlite3, "connect", side_effect=connect):
            with self.assertRaises(maintenance.SafetyBackupFailed) as failed:
                maintenance.take_safety_backup(self.api.db.model)
        self.assertEqual(str(failed.exception), "The backup could not be finished")
        self.assertEqual(calls[0], calls[1])  # the temp copy, both times
        self.assertEqual((self.backups(), self.strays()), ([], []))

    async def test_a_safety_backup_restores_through_the_legacy_route(self):
        from aiohttp import FormData

        from py.api import maintenance

        self.save("Before the backup")
        name = maintenance.take_safety_backup(self.api.db.model)
        self.save("After the backup")
        with open(os.path.join(self.tmpdir, name), "rb") as handle:
            upload = handle.read()
        form = FormData()
        form.add_field("database_file", upload, filename=name)
        response = await self.client.request(
            "POST", "/prompt_manager/restore", data=form
        )
        self.assertEqual(response.status, 200, await response.text())
        texts = [p["text"] for p in self.api.db.search_prompts()]
        self.assertEqual(texts, ["Before the backup"])
        mode = self.api.db.model.get_connection().execute("PRAGMA journal_mode")
        self.assertEqual(mode.fetchone()[0], "wal")


class TestBackupPoolAndDiskSpace(ApplyTestCase):
    """Coordinator rulings: one backup pool; Apply refuses a short disk up front."""

    def old_backup(self, name, mtime):
        path = os.path.join(self.tmpdir, name)
        with open(path, "wb") as handle:
            handle.write(b"old")
        os.utime(path, (mtime, mtime))
        return name

    async def test_safety_backups_share_the_restore_pool(self):
        from py.api.admin import MAX_SAFETY_BACKUPS, _prune_safety_backups

        restore_style = [
            self.old_backup(f"prompts.db.backup_2000010{i}_000000", 1000 + i)
            for i in range(6)
        ]
        maintenance_style = [
            self.old_backup(f"prompts.db.backup_2001010{i}_000000_000000", 2000 + i)
            for i in range(5)
        ]
        self.save("Orphan")
        await self.run_apply("unused_components")
        backups = self.backups()
        # One pool of ten: the new backup plus the nine newest of the eleven.
        self.assertEqual(len(backups), MAX_SAFETY_BACKUPS)
        self.assertNotIn(restore_style[0], backups)
        self.assertNotIn(restore_style[1], backups)
        self.assertLessEqual(set(maintenance_style), set(backups))
        # A restore prunes the same pool, maintenance backups included.
        self.old_backup("prompts.db.backup_20020101_000000", 3000)
        _prune_safety_backups(self.api.db.model.db_path)
        self.assertEqual(len(self.backups()), MAX_SAFETY_BACKUPS)
        self.assertNotIn(restore_style[2], self.backups())

    async def test_the_pool_is_pruned_when_the_apply_fails_after_the_backup(self):
        from py.api.admin import MAX_SAFETY_BACKUPS

        for index in range(MAX_SAFETY_BACKUPS):
            self.old_backup(f"prompts.db.backup_2000010{index}_00000{index}", 1000)
        orphan = self.save("Orphan")
        plan = await self.preview("unused_components")
        self.save("Changed after the review")
        body = await self.apply(plan["token"])
        job = self.api._jobs.get(body["job"]["id"])
        await asyncio.wait_for(asyncio.shield(job.task), WAIT)
        self.assertEqual(job.last_event["code"], "plan-changed")
        self.assertIsNotNone(self.api.db.get_prompt_by_id(orphan))
        self.assertEqual(len(self.backups()), MAX_SAFETY_BACKUPS)

    async def test_preview_reports_the_backup_size_and_free_space(self):
        from py.api import maintenance

        self.save("Orphan")
        # SQLite's temporary folder on the database's disk: the needs add up.
        with (
            patch.object(maintenance, "_free_bytes", return_value=10 * 2**30),
            patch.object(maintenance, "sqlite_temp_dir", return_value=self.tmpdir),
        ):
            plan = await self.preview("unused_components")
            optimize = await self.preview("optimize")
        size = self.database_bytes()
        self.assertEqual(plan["backup"]["expected_bytes"], size)
        self.assertEqual(
            plan["disk"],
            {
                "free_bytes": 10 * 2**30,
                "required_bytes": size + maintenance.DISK_MARGIN_BYTES,
                "sufficient": True,
            },
        )
        # VACUUM rebuilds the database beside it, and the backup comes first.
        self.assertEqual(
            optimize["disk"]["required_bytes"],
            3 * size + maintenance.DISK_MARGIN_BYTES,
        )

    async def test_policy_off_reports_no_backup_size(self):
        from py.api import maintenance

        PromptManagerConfig.SAFETY_BACKUP = False
        self.save("Orphan")
        with patch.object(maintenance, "_free_bytes", return_value=0):
            plan = await self.preview("unused_components")
            reply = await self.apply(plan["token"])
        self.assertIsNone(plan["backup"]["expected_bytes"])
        self.assertEqual(
            plan["disk"], {"free_bytes": 0, "required_bytes": 0, "sufficient": True}
        )
        job = self.api._jobs.get(reply["job"]["id"])
        await asyncio.wait_for(asyncio.shield(job.task), WAIT)
        self.assertEqual(job.state, "finished")

    async def test_apply_refuses_up_front_when_the_backup_does_not_fit(self):
        from py.api import maintenance

        orphan = self.save("Orphan")
        plan = await self.preview("unused_components")
        with patch.object(maintenance, "_free_bytes", return_value=1024):
            reply = await self.apply(plan["token"], 507)
        self.assertEqual(reply["code"], "insufficient-space")
        needed = self.database_bytes() + maintenance.DISK_MARGIN_BYTES
        size_mb = f"{needed / 2**20:,.1f} MB"
        self.assertEqual(
            reply["error"],
            f"Not enough free disk space: the safety backup needs about {size_mb} "
            "in the database folder and 0.0 MB is free. Nothing was changed.",
        )
        self.assertEqual(self.api._jobs.list(), [])  # no job started
        self.assertEqual(self.backups(), [])
        self.assertIsNotNone(self.api.db.get_prompt_by_id(orphan))
        # The review is still usable once there is room.
        body = await self.apply(plan["token"])
        job = self.api._jobs.get(body["job"]["id"])
        await asyncio.wait_for(asyncio.shield(job.task), WAIT)
        self.assertIsNone(self.api.db.get_prompt_by_id(orphan))

    async def test_apply_refuses_up_front_when_vacuum_does_not_fit(self):
        from py.api import maintenance

        PromptManagerConfig.SAFETY_BACKUP = False
        size = self.database_bytes()
        plan = await self.preview("optimize")
        free = size  # VACUUM needs about twice the database
        with patch.object(maintenance, "_free_bytes", return_value=free):
            reply = await self.apply(plan["token"], 507)
        self.assertEqual(reply["code"], "insufficient-space")
        self.assertIn("Optimize needs about", reply["error"])
        self.assertTrue(reply["error"].endswith("Nothing was changed."))
        self.assertEqual(self.api._jobs.list(), [])


class TestSafetyBackupPolicy(MaintenanceAPITestCase):
    async def test_policy_round_trips_with_revision(self):
        settings = await self.client.request("GET", "/prompt_manager/settings")
        body = await settings.json()
        self.assertTrue(body["settings"]["safety_backup"])
        saved = await self.post(
            "/prompt_manager/settings",
            {"safety_backup": False, "expected_revision": body["revision"]},
            200,
        )
        self.assertTrue(saved["persisted"])
        self.assertNotEqual(saved["revision"], body["revision"])
        with open(self.config_path, encoding="utf-8") as handle:
            self.assertIs(json.load(handle)["maintenance"]["safety_backup"], False)
        again = await (
            await self.client.request("GET", "/prompt_manager/settings")
        ).json()
        self.assertFalse(again["settings"]["safety_backup"])
        self.assertEqual(again["revision"], saved["revision"])
        plan = await self.preview("optimize")
        self.assertEqual(plan["backup"]["policy"], "off")
        stale = await self.post(
            "/prompt_manager/settings",
            {"safety_backup": True, "expected_revision": body["revision"]},
            409,
        )
        self.assertEqual(stale["code"], "settings-changed")
        bad = await self.post("/prompt_manager/settings", {"safety_backup": "yes"}, 400)
        self.assertFalse(bad["success"])

    async def test_config_file_value_is_read_at_load(self):

        PromptManagerConfig.SAFETY_BACKUP = False
        self.assertFalse(PromptManagerConfig.SAFETY_BACKUP)
        PromptManagerConfig.update_config({"maintenance": {"safety_backup": "no"}})
        self.assertFalse(PromptManagerConfig.SAFETY_BACKUP)  # non-bool ignored
        self.assertEqual(
            PromptManagerConfig.get_config()["maintenance"], {"safety_backup": False}
        )


class TestLegacyMaintenance(MaintenanceAPITestCase):
    async def test_vacuum_failure_reported_not_success(self):
        with patch(
            "database.models.sqlite3.connect",
            side_effect=sqlite3.OperationalError("database is locked"),
        ):
            with self.assertLogs("prompt_manager", "ERROR"):
                reply = await self.post(
                    "/prompt_manager/maintenance", {"operations": ["vacuum"]}, 200
                )
        self.assertFalse(reply["all_successful"])
        self.assertFalse(reply["results"]["vacuum"]["success"])
        self.assertEqual(reply["results"]["vacuum"]["error"], "database is locked")

    async def test_legacy_orphaned_image_cleanup_skips_unavailable_sources(self):
        unmounted = Path(self.tmpdir) / "unmounted"
        unmounted.mkdir()
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.output_dir), str(unmounted)]
        prompt = self.save("Links")
        self.image(prompt, "gone.png", exists=False)
        away = self.image(prompt, "away.png", folder=unmounted, exists=False)
        unmounted.rmdir()
        reply = await self.post(
            "/prompt_manager/maintenance",
            {"operations": ["cleanup_orphaned_images"]},
            200,
        )
        self.assertEqual(
            reply["results"]["cleanup_orphaned_images"],
            {
                "success": True,
                "removed_count": 1,
                "message": "Removed 1 orphaned image records",
            },
        )
        self.assertIsNotNone(self.api.db.get_image_by_id(away))


if __name__ == "__main__":  # pragma: no cover
    import unittest

    unittest.main()


class TestFinalReviewFixes(ApplyTestCase):
    """PR #313 final review: I3, M1-M5."""

    def duplicate_of(self, kept_text, copy_text):
        kept = self.save(kept_text)
        with self.api.db.model.get_connection() as conn:
            copy = conn.execute(
                "INSERT INTO prompts(text, created_at) VALUES(?, '2999-01-01')",
                (copy_text,),
            ).lastrowid
        return kept, copy

    def test_sqlite_temp_dir_follows_sqlite_search_order(self):
        from py.api.maintenance import sqlite_temp_dir

        def usable(*good):
            return lambda path: path in good

        posix = {"windows": False}
        self.assertEqual(
            sqlite_temp_dir({}, usable=usable("/var/tmp", "/tmp"), **posix), "/var/tmp"
        )
        self.assertEqual(
            sqlite_temp_dir({}, usable=usable("/usr/tmp", "/tmp"), **posix), "/usr/tmp"
        )
        env = {"SQLITE_TMPDIR": "/fast", "TMPDIR": "/t"}
        self.assertEqual(
            sqlite_temp_dir(env, usable=usable("/fast", "/t", "/tmp"), **posix),
            "/fast",
        )
        self.assertEqual(
            sqlite_temp_dir(env, usable=usable("/t", "/tmp"), **posix), "/t"
        )
        self.assertEqual(
            sqlite_temp_dir({}, usable=usable(), **posix), os.path.abspath(".")
        )
        windows = {"windows": True, "usable": usable()}
        env = {"TMP": "C:\\a", "TEMP": "C:\\b", "USERPROFILE": "C:\\u"}
        self.assertEqual(sqlite_temp_dir(env, **windows), "C:\\a")
        self.assertEqual(sqlite_temp_dir({"TEMP": "C:\\b"}, **windows), "C:\\b")
        self.assertEqual(sqlite_temp_dir({"USERPROFILE": "C:\\u"}, **windows), "C:\\u")

    async def test_optimize_measures_sqlites_temp_folder(self):
        from py.api import maintenance

        measured = []

        def free(path):
            measured.append(path)
            return 10 * 2**30

        other = os.path.join(self.tmpdir, "sqlite-temp")
        os.mkdir(other)
        with (
            patch.object(maintenance, "sqlite_temp_dir", return_value=other),
            patch.object(maintenance, "_free_bytes", side_effect=free),
            patch.object(
                maintenance, "_device", side_effect=lambda path: path  # another disk
            ),
        ):
            plan = await self.preview("optimize")
        self.assertIn(other, measured)
        size = self.database_bytes()
        self.assertEqual(
            plan["disk"]["required_bytes"], 2 * size + maintenance.DISK_MARGIN_BYTES
        )

    async def test_a_plan_used_during_the_disk_check_is_plan_used(self):
        from py.api import maintenance

        self.save("Orphan")
        plan = await self.preview("unused_components")
        real = maintenance.disk_plan

        def racing(*args, **kwargs):
            # A parallel apply consumed the token while this one measured.
            self.api._maintenance_plans.consume(
                plan["token"], "unused_components", job_id="f" * 32
            )
            return real(*args, **kwargs)

        with patch.object(maintenance, "disk_plan", side_effect=racing):
            reply = await self.apply(plan["token"], 409)
        self.assertEqual(reply["code"], "plan-used")
        self.assertEqual(self.api._jobs.list(), [])

    async def test_an_apply_that_never_started_says_so_with_its_code(self):
        """S7 review I1: the server's own pre-start failure is definitive, so
        it carries a code the workspace can tell from a proxy's 5xx."""
        from py.api import maintenance

        self.save("Orphan")
        plan = await self.preview("unused_components")
        with patch.object(maintenance, "disk_plan", side_effect=OSError("disk")):
            reply = await self.apply(plan["token"], 500)
        self.assertEqual(reply["code"], "apply-failed")
        self.assertEqual(reply["error"], maintenance.APPLY_FAILED)
        self.assertEqual(self.api._jobs.list(), [])

    async def test_a_failed_apply_names_its_failure_in_its_scope(self):
        """S7 review M2: Activity offers Review again from the job's own record."""
        orphan, soon = self.save("Orphan"), self.save("Soon a recipe")
        plan = await self.preview("unused_components")
        with self.api.db.model.get_connection() as conn:
            conn.execute(
                "INSERT INTO prompt_library_state(prompt_id,title) VALUES(?,?)",
                (soon, "Recipe"),
            )
        body = await self.apply(plan["token"])
        job = self.api._jobs.get(body["job"]["id"])
        await asyncio.wait_for(asyncio.shield(job.task), WAIT)
        self.assertEqual(
            job.summary()["scope"],
            {"operation": "unused_components", "failure": "plan-changed"},
        )
        self.assertIsNotNone(self.api.db.get_prompt_by_id(orphan))

    async def test_the_backup_decision_is_frozen_when_the_job_starts(self):
        PromptManagerConfig.SAFETY_BACKUP = False
        self.save("Orphan")
        plan = await self.preview("unused_components")
        real = self.api._jobs.start

        def start(*args, **kwargs):
            job = real(*args, **kwargs)
            PromptManagerConfig.SAFETY_BACKUP = True  # switched on meanwhile
            return job

        with patch.object(self.api._jobs, "start", side_effect=start):
            body = await self.apply(plan["token"])
        job = self.api._jobs.get(body["job"]["id"])
        await asyncio.wait_for(asyncio.shield(job.task), WAIT)
        self.assertEqual(self.backups(), [])
        self.assertTrue(job.status_line.endswith("No safety backup"))

    async def test_startup_sweeps_interrupted_backup_copies(self):
        stray = os.path.join(self.tmpdir, ".pm-safety-crashed.tmp")
        with open(stray, "wb") as handle:
            handle.write(b"half a backup")
        PromptManagerAPI(db=self.api.db)
        self.assertFalse(os.path.exists(stray))

    async def test_a_backup_is_linked_into_place_without_a_reserved_name(self):
        from py.api import maintenance

        linked = []
        real = os.link

        def link(source, target):
            linked.append(target)
            return real(source, target)

        with (
            patch.object(maintenance.os, "link", side_effect=link),
            patch.object(
                safety_backup, "reserve_backup_name", side_effect=AssertionError
            ),
        ):
            name = maintenance.take_safety_backup(self.api.db.model)
        self.assertEqual([os.path.basename(p) for p in linked], [name])
        self.assertEqual(self.strays(), [])

    async def test_optimize_runs_alone_and_says_the_database_is_locked(self):
        from py.api.jobs import JobBusy

        plan = await self.preview("optimize")
        self.assertIn(
            "Runs alone: the database is locked while it runs",
            " ".join(row["detail"] for row in plan["details"]),
        )
        gate = asyncio.Event()

        async def held(job):
            await gate.wait()

        read = self.api._jobs.start(
            "health", "Health check", admission="read", runner=held
        )
        busy = await self.apply(plan["token"], 409)
        self.assertEqual(busy["running"]["id"], read.id)
        gate.set()
        await asyncio.wait_for(asyncio.shield(read.task), WAIT)
        gate.clear()
        body = await self.apply(plan["token"])
        self.assertEqual(body["job"]["admission"], "exclusive")
        with self.assertRaises(JobBusy):
            self.api._jobs.start(
                "health", "Health check", admission="read", runner=held
            )
        job = self.api._jobs.get(body["job"]["id"])
        await asyncio.wait_for(asyncio.shield(job.task), WAIT)

    async def test_a_link_collision_is_protected_not_swallowed(self):
        kept, copy = self.duplicate_of("A cat", "A CAT")
        path = os.path.join(str(self.output_dir), "both.png")
        self.api.db.link_image_to_prompt(kept, path)
        self.api.db.link_image_to_prompt(copy, path)
        plan, _summary, job = await self.run_apply("duplicate_prompts")
        self.assertEqual(plan["scope"]["count"], 0)
        self.assertEqual(
            plan["protected"]["reasons"], ["1 group links one image twice"]
        )
        self.assertIsNotNone(self.api.db.get_prompt_by_id(copy))
        self.assertEqual(len(self.api.db.get_prompt_images(copy)), 1)

    async def test_a_duplicate_with_a_library_title_is_protected(self):
        kept, copy = self.duplicate_of("A dog", "A DOG")
        with self.api.db.model.get_connection() as conn:
            conn.execute(
                "INSERT INTO prompt_library_state(prompt_id,title) VALUES(?,?)",
                (copy, "My dog recipe"),
            )
        plan, _summary, _job = await self.run_apply("duplicate_prompts")
        self.assertEqual(plan["scope"]["count"], 0)
        self.assertEqual(
            plan["protected"]["reasons"], ["1 group has its own Library title"]
        )
        self.assertIsNotNone(self.api.db.get_prompt_by_id(copy))

    def test_the_strict_merge_raises_instead_of_dropping_a_link(self):
        from database.maintenance import MergeConflict

        kept, copy = self.duplicate_of("A bird", "A BIRD")
        path = os.path.join(str(self.output_dir), "bird.png")
        self.api.db.link_image_to_prompt(kept, path)
        self.api.db.link_image_to_prompt(copy, path)
        with self.assertRaises(MergeConflict):
            with self.api.db._pair_transaction() as conn:
                self.api.db._merge_duplicate_group(conn, [kept, copy], strict=True)
        self.assertEqual(len(self.api.db.get_prompt_images(copy)), 1)
        # The legacy cleanup keeps its old behaviour (it logs and goes on).
        with self.assertLogs("prompt_manager.database", "ERROR"):
            self.assertEqual(self.api.db.cleanup_duplicates(), 1)
