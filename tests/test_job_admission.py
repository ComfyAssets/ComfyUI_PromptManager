"""Registry admission classes and the legacy routes that hold the registry.

Record-changing work never runs together (Activity design): a ``records``
job is refused while any ``records`` or ``guarded`` job runs, a ``guarded``
job (thumbnails) while a ``records`` job runs, and ``read`` jobs only share
the one-per-kind rule. The legacy admin and gallery routes that change
records or files take a registry hold for their duration, so a workspace job
and a legacy-page operation refuse each other, naming the one that runs.

Fake runners and gated executor calls only; ordering is driven by events.
"""

import asyncio
import hashlib
import json
import os
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_mock_server = MagicMock()
_mock_server.PromptServer.instance.routes = MagicMock()
sys.modules.setdefault("server", _mock_server)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from py.api.jobs import (  # noqa: E402
    ADMISSIONS,
    Job,
    JobBusy,
    JobRegistry,
)

try:
    from tests.test_api_admin import AdminAPITestCase  # noqa: E402
except ImportError:  # discovered with tests/ as the top-level directory
    from test_api_admin import AdminAPITestCase  # noqa: E402


def _gated(gate):
    async def runner(job):
        await gate.wait()

    return runner


async def _noop(job):
    return None


class TestAdmissionClasses(unittest.IsolatedAsyncioTestCase):

    def _start(self, registry, kind, admission, gate=None, **kwargs):
        return registry.start(
            kind,
            kind.title(),
            admission=admission,
            scope={},
            runner=_gated(gate) if gate is not None else _noop,
            **kwargs,
        )

    async def test_admission_names_and_default(self):
        self.assertEqual(ADMISSIONS, ("read", "guarded", "records"))
        registry = JobRegistry()
        job = registry.start("report", "Report", scope={}, runner=_noop)
        await asyncio.wait_for(job.task, 2)
        self.assertEqual(job.admission, "read")
        self.assertFalse(job.record_changing)
        with self.assertRaises(ValueError):
            registry.start("x", "X", admission="exclusive", scope={}, runner=_noop)
        with self.assertRaises(ValueError):
            registry.start(
                "x",
                "X",
                admission="read",
                record_changing=True,
                scope={},
                runner=_noop,
            )

    async def test_record_changing_is_an_alias_for_records(self):
        registry = JobRegistry()
        gate = asyncio.Event()
        job = registry.start(
            "scan", "Scan", record_changing=True, scope={}, runner=_gated(gate)
        )
        self.assertEqual(job.admission, "records")
        self.assertTrue(job.record_changing)
        self.assertEqual(job.summary()["admission"], "records")
        self.assertIs(
            registry.blocker("image-tags", record_changing=True), job
        )  # the existing call shape keeps working
        gate.set()
        await asyncio.wait_for(job.task, 2)

    async def test_records_job_refused_while_guarded_runs(self):
        registry = JobRegistry()
        gate = asyncio.Event()
        thumbs = self._start(registry, "thumbnails", "guarded", gate)
        with self.assertRaises(JobBusy) as raised:
            self._start(registry, "scan", "records")
        self.assertIs(raised.exception.running, thumbs)
        self.assertIs(registry.blocker("restore", admission="records"), thumbs)
        gate.set()
        await asyncio.wait_for(thumbs.task, 2)
        scan = self._start(registry, "scan", "records")
        await asyncio.wait_for(scan.task, 2)
        self.assertEqual(scan.state, "finished")

    async def test_guarded_refused_while_records_runs(self):
        registry = JobRegistry()
        gate = asyncio.Event()
        scan = self._start(registry, "scan", "records", gate)
        with self.assertRaises(JobBusy) as raised:
            self._start(registry, "thumbnails", "guarded")
        self.assertIs(raised.exception.running, scan)
        # A second guarded kind may run beside a guarded one, not beside records.
        gate.set()
        await asyncio.wait_for(scan.task, 2)
        hold = asyncio.Event()
        thumbs = self._start(registry, "thumbnails", "guarded", hold)
        backup = self._start(registry, "backup", "guarded")
        await asyncio.wait_for(backup.task, 2)
        self.assertEqual(backup.state, "finished")
        hold.set()
        await asyncio.wait_for(thumbs.task, 2)

    async def test_read_jobs_run_in_parallel(self):
        registry = JobRegistry()
        gate = asyncio.Event()
        scan = self._start(registry, "scan", "records", gate)
        health = self._start(registry, "health", "read", gate)
        duplicates = self._start(registry, "duplicates", "read", gate)
        self.assertTrue(all(j.running for j in (scan, health, duplicates)))
        with self.assertRaises(JobBusy) as raised:  # one job of a kind
            self._start(registry, "health", "read")
        self.assertIs(raised.exception.running, health)
        gate.set()
        await asyncio.wait_for(
            asyncio.gather(scan.task, health.task, duplicates.task), 2
        )

    async def test_non_cancellable_job_ignores_cancel(self):
        registry = JobRegistry()
        gate = asyncio.Event()
        job = self._start(registry, "restore", "records", gate, cancellable=False)
        self.assertIs(job.summary()["cancellable"], False)
        self.assertFalse(registry.cancel(job.id))
        self.assertFalse(job.cancelled)
        gate.set()
        await asyncio.wait_for(job.task, 2)
        self.assertEqual(job.state, "finished")

    async def test_hold_registers_a_running_non_cancellable_job(self):
        registry = JobRegistry()
        held = registry.hold(
            "restore", "Database restore (admin page)", admission="records"
        )
        async with held as job:
            self.assertIs(registry.active("restore"), job)
            self.assertEqual(job.state, "running")
            self.assertEqual(
                (job.summary()["cancellable"], job.summary()["admission"]),
                (False, "records"),
            )
            self.assertFalse(registry.cancel(job.id))
            with self.assertRaises(JobBusy) as raised:
                self._start(registry, "scan", "records")
            self.assertIn("Database restore (admin page)", str(raised.exception))
        self.assertEqual(job.state, "finished")
        self.assertIsNone(registry.active("restore"))
        self.assertIs(registry.list()[0], job)

    async def test_hold_refused_without_waiting(self):
        registry = JobRegistry()
        gate = asyncio.Event()
        scan = self._start(registry, "scan", "records", gate)
        with self.assertRaises(JobBusy) as raised:
            registry.hold("maintenance", "Maintenance", admission="records")
        self.assertIs(raised.exception.running, scan)
        self.assertEqual([j.kind for j in registry.list()], ["scan"])
        gate.set()
        await asyncio.wait_for(scan.task, 2)

    async def test_hold_released_on_exception(self):
        registry = JobRegistry()
        with self.assertRaises(RuntimeError):
            async with registry.hold(
                "maintenance", "Maintenance", admission="records"
            ) as job:
                raise RuntimeError("boom")
        self.assertEqual(job.state, "failed")
        again = registry.hold("maintenance", "Maintenance", admission="records")
        async with again as second:
            second.finish("failed")  # the body may end it itself
        self.assertEqual(second.state, "failed")

    def test_job_defaults_stay_read_and_cancellable(self):
        job = Job("duplicates", "Duplicate scan")
        self.assertEqual((job.admission, job.cancellable), ("read", True))


class _LegacyHoldCase(AdminAPITestCase):
    """The admin test app plus a running workspace job on demand."""

    def _run_job(self, kind="scan", title="Scan for metadata", admission="records"):
        gate = asyncio.Event()
        job = self.api._jobs.start(
            kind, title, admission=admission, scope={}, runner=_gated(gate)
        )
        self.addAsyncCleanup(self._release, gate, job)
        return gate, job

    @staticmethod
    async def _release(gate, job):
        gate.set()
        await asyncio.wait_for(job.task, 5)

    def _db_digest(self):
        path = Path(self.api.db.model.db_path)
        return {
            suffix: hashlib.sha256(Path(f"{path}{suffix}").read_bytes()).hexdigest()
            for suffix in ("", "-wal")
            if Path(f"{path}{suffix}").exists()
        }

    async def _busy(self, resp, title="Scan for metadata"):
        body = await resp.json()
        self.assertEqual(resp.status, 409)
        self.assertEqual(body, {"success": False, "error": f"{title} already running"})


class TestLegacyRoutesHoldTheRegistry(_LegacyHoldCase):

    async def test_hold_is_listed_as_running_job(self):
        held = self.api._jobs.hold(
            "restore", "Database restore (admin page)", admission="records"
        )
        async with held:
            resp = await self.client.request("GET", "/prompt_manager/jobs")
            jobs = (await resp.json())["jobs"]
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(
            (job["kind"], job["title"], job["state"]),
            ("restore", "Database restore (admin page)", "running"),
        )
        self.assertIs(job["cancellable"], False)
        self.assertEqual(job["admission"], "records")

    async def test_legacy_maintenance_and_cleanup_refused_while_scan_runs(self):
        self.api.db.save_prompt(text="keep me", prompt_hash="h1")
        before = self._db_digest()
        self._run_job()
        resp = await self.client.request(
            "POST",
            "/prompt_manager/maintenance",
            json={"operations": ["cleanup_duplicates", "vacuum"]},
        )
        await self._busy(resp)
        resp = await self.client.request("POST", "/prompt_manager/cleanup")
        await self._busy(resp)
        self.assertEqual(self._db_digest(), before)

    async def test_legacy_maintenance_refused_while_image_tags_runs(self):
        self._run_job("image-tags", "Add tags to 3 images")
        resp = await self.client.request("POST", "/prompt_manager/maintenance")
        await self._busy(resp, "Add tags to 3 images")

    async def test_scan_refused_while_legacy_maintenance_holds(self):
        entered = threading.Event()
        release = threading.Event()
        real = self.api._run_maintenance_operations

        def gated(operations):
            entered.set()
            release.wait(10)
            return real(operations)

        self.api._run_maintenance_operations = gated
        loop = asyncio.get_running_loop()
        maintenance = asyncio.ensure_future(
            self.client.request(
                "POST", "/prompt_manager/maintenance", json={"operations": ["vacuum"]}
            )
        )
        self.assertTrue(await loop.run_in_executor(None, entered.wait, 10))
        resp = await self.client.request("POST", "/prompt_manager/jobs/scan")
        body = await resp.json()
        self.assertEqual((resp.status, body["code"]), (409, "busy"))
        self.assertEqual(body["running"]["title"], "Maintenance (admin page)")
        release.set()
        self.assertEqual((await maintenance).status, 200)
        listed = (
            await (await self.client.request("GET", "/prompt_manager/jobs")).json()
        )["jobs"]
        self.assertEqual(
            [(j["kind"], j["state"]) for j in listed], [("maintenance", "finished")]
        )

    async def test_legacy_duplicate_delete_refused_while_scan_runs(self):
        victim = self.output_dir / "dup.png"
        victim.write_bytes(b"x")
        self._run_job()
        resp = await self.client.request(
            "POST",
            "/prompt_manager/delete_duplicate_images",
            json={"image_paths": [str(victim)]},
        )
        await self._busy(resp)
        self.assertTrue(victim.exists())
        # The duplicate family lock was not taken by the refused request.
        self.assertIsNotNone(self.api._acquire_long_job(self.api.LONG_JOB_DUPLICATES))

    async def test_lora_scan_refused_while_scan_runs(self):
        from py.config import IntegrationConfig

        lm = Path(self.tmpdir) / "custom_nodes" / "ComfyUI-Lora-Manager"
        (lm / "py").mkdir(parents=True)
        self._run_job()
        imported = []
        with (
            patch.multiple(
                IntegrationConfig, LORA_MANAGER_ENABLED=True, LORA_MANAGER_PATH=str(lm)
            ),
            patch("py.lora_utils.resolve_lora_manager_path", return_value=str(lm)),
            patch.object(
                self.api, "_lora_scan_stream", side_effect=lambda *a: imported.append(a)
            ),
        ):
            resp = await self.client.request("POST", "/prompt_manager/lora/scan")
            text = await resp.text()
        self.assertEqual(resp.status, 200)  # the legacy page reads a stream
        events = [json.loads(line[6:]) for line in text.split("\n\n") if line]
        self.assertEqual(
            events,
            [{"type": "error", "message": "Scan for metadata already running"}],
        )
        self.assertEqual(imported, [])

    async def test_legacy_autotag_refused_while_scan_runs(self):
        self._run_job()
        resp = await self.client.request(
            "POST", "/prompt_manager/autotag/start", json={}
        )
        await self._busy(resp)

    async def test_legacy_payloads_unchanged_when_idle(self):
        resp = await self.client.request(
            "POST", "/prompt_manager/maintenance", json={"operations": ["vacuum"]}
        )
        body = await resp.json()
        self.assertEqual(resp.status, 200)
        self.assertEqual(
            set(body),
            {"success", "operations_completed", "all_successful", "results", "message"},
        )
        resp = await self.client.request("POST", "/prompt_manager/cleanup")
        body = await resp.json()
        self.assertEqual(resp.status, 200)
        self.assertEqual(set(body), {"success", "message", "duplicates_removed"})
        resp = await self.client.request(
            "POST", "/prompt_manager/delete_duplicate_images", json={"image_paths": []}
        )
        self.assertEqual(
            (resp.status, await resp.json()),
            (400, {"success": False, "error": "No image paths provided"}),
        )
        # Each hold ended with the request; a refused input shows as failed.
        listed = (
            await (await self.client.request("GET", "/prompt_manager/jobs")).json()
        )["jobs"]
        self.assertEqual(
            [(j["kind"], j["state"]) for j in listed],
            [
                ("duplicate_cleanup", "failed"),
                ("maintenance", "finished"),
                ("maintenance", "finished"),
            ],
        )
        self.assertEqual(
            [j["title"] for j in listed],
            [
                "Duplicate image delete (gallery page)",
                "Prompt cleanup (admin page)",
                "Maintenance (admin page)",
            ],
        )


if __name__ == "__main__":
    unittest.main()
