"""System routes (py/api/system.py): health check job, Overview and statistics.

The health check runs off-loop as a ``read`` registry job of kind ``health``
(one at a time, beside a scan), and the API keeps its last report. The
Overview reads only real values: Library-eligible counts, indexed images, the
last health result or null, no backup until one exists, and the effective
sources and whether they were auto-detected. The missing-thumbnail count is not
part of it: the Overview reads ``/images/thumbnails/status`` on its own (its
walk of every source must not hold the other values back).
"""

import asyncio
import os
import sqlite3
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

_mock_server = MagicMock()
_mock_server.PromptServer.instance.routes = MagicMock()
sys.modules.setdefault("server", _mock_server)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from py.api.jobs import HEALTH_JOB_KIND  # noqa: E402
from py.config import GalleryConfig  # noqa: E402
from utils.hashing import generate_prompt_hash  # noqa: E402

try:
    from tests.test_api_admin import AdminAPITestCase  # noqa: E402
except ImportError:  # discovered with tests/ as the top-level directory
    from test_api_admin import AdminAPITestCase  # noqa: E402

WAIT = 5


class SystemAPITestCase(AdminAPITestCase):
    async def get_application(self):
        app = await super().get_application()
        # Auto-detection searches the disk; here it finds the fake ComfyUI only.
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
        if exists:
            path.write_bytes(b"png")
        return self.api.db.link_image_to_prompt(prompt_id, str(path))

    async def json(self, method, url, status=200):
        response = await self.client.request(method, url)
        self.assertEqual(response.status, status, await response.text())
        return await response.json()

    def snapshot_form(self):
        """A multipart upload of a copy of the live database."""
        from contextlib import closing

        from aiohttp import FormData

        snapshot = os.path.join(self.tmpdir, "snapshot.db")
        with closing(sqlite3.connect(self.api.db.model.db_path)) as src:
            with closing(sqlite3.connect(snapshot)) as dst:
                src.backup(dst)
        with open(snapshot, "rb") as handle:
            upload = handle.read()
        form = FormData()
        form.add_field("database_file", upload, filename="upload.db")
        return form

    async def restore_snapshot(self):
        """Restore the live database from a copy of itself through the legacy route."""
        response = await self.client.request(
            "POST", "/prompt_manager/restore", data=self.snapshot_form()
        )
        self.assertEqual(response.status, 200, await response.text())

    async def run_health(self):
        body = await self.json("POST", "/prompt_manager/system/health", 202)
        job = self.api._jobs.get(body["job"]["id"])
        await asyncio.wait_for(asyncio.shield(job.task), WAIT)
        return body["job"], job


class TestHealthRoute(SystemAPITestCase):
    async def test_no_report_before_a_check(self):
        body = await self.json("GET", "/prompt_manager/system/health")
        self.assertEqual(body, {"success": True, "report": None})

    async def test_health_check_runs_as_a_read_job_and_keeps_its_report(self):
        prompt = self.save("Images")
        self.image(prompt, "kept.png")
        self.image(prompt, "gone.png", exists=False)
        summary, job = await self.run_health()
        self.assertEqual(summary["kind"], HEALTH_JOB_KIND)
        self.assertEqual(summary["title"], "Health check")
        self.assertEqual(summary["admission"], "read")
        self.assertFalse(summary["cancellable"])
        self.assertEqual(summary["counters"], {})
        self.assertEqual(job.state, "finished")
        self.assertEqual(job.progress, 100)
        self.assertEqual(job.status_line, "Health check incomplete")
        report = (await self.json("GET", "/prompt_manager/system/health"))["report"]
        self.assertEqual(report["status"], "incomplete")
        missing = next(c for c in report["checks"] if c["id"] == "missing_files")
        self.assertEqual((missing["status"], missing["count"]), ("issues", 1))
        ambiguous = next(c for c in report["checks"] if c["id"] == "ambiguous_metadata")
        self.assertEqual(ambiguous["status"], "unavailable")
        self.assertNotIn(self.tmpdir, str(report))

    async def test_health_check_is_single_flight_and_runs_beside_a_scan(self):
        release = threading.Event()
        entered = threading.Event()
        real = self.api.db.run_health_checks

        def gated(*args, **kwargs):
            entered.set()
            release.wait(WAIT)
            return real(*args, **kwargs)

        scan_gate = asyncio.Event()

        async def scanning(job):
            await scan_gate.wait()

        scan = self.api._jobs.start(
            "scan", "Metadata scan", admission="records", runner=scanning
        )
        with patch.object(self.api.db, "run_health_checks", side_effect=gated):
            first = await self.json("POST", "/prompt_manager/system/health", 202)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, entered.wait, WAIT)
            busy = await self.json("POST", "/prompt_manager/system/health", 409)
            self.assertEqual(busy["code"], "busy")
            self.assertEqual(
                busy["running"], {"id": first["job"]["id"], "title": "Health check"}
            )
            release.set()
            await asyncio.wait_for(self.api._jobs.get(first["job"]["id"]).task, WAIT)
        scan_gate.set()
        await asyncio.wait_for(scan.task, WAIT)

    async def test_failed_check_keeps_the_previous_report(self):
        await self.run_health()
        before = (await self.json("GET", "/prompt_manager/system/health"))["report"]
        with (
            patch.object(
                self.api.db, "run_health_checks", side_effect=RuntimeError("boom")
            ),
            self.assertLogs("prompt_manager.api", "ERROR"),
        ):
            body = await self.json("POST", "/prompt_manager/system/health", 202)
            job = self.api._jobs.get(body["job"]["id"])
            await asyncio.wait_for(asyncio.shield(job.task), WAIT)
        self.assertEqual(job.state, "failed")
        after = (await self.json("GET", "/prompt_manager/system/health"))["report"]
        self.assertEqual(after, before)

    async def test_absent_configured_source_is_reported_per_source(self):
        prompt = self.save("Images")
        self.image(prompt, "gone.png", exists=False)
        absent = self.comfy_dir / "output" / "unmounted"
        GalleryConfig.MONITORING_DIRECTORIES = [
            self._root(self.output_dir),
            str(absent),
        ]
        await self.run_health()
        report = (await self.json("GET", "/prompt_manager/system/health"))["report"]
        missing = next(c for c in report["checks"] if c["id"] == "missing_files")
        # Per source: the readable output is counted, the absent one is not.
        self.assertEqual(missing["status"], "issues")
        self.assertEqual(missing["count"], 1)
        self.assertTrue(missing["partial"])
        self.assertEqual(
            [(s["status"], s["count"]) for s in missing["sources"]],
            [("checked", 1), ("unavailable", None)],
        )
        self.assertEqual(report["status"], "incomplete")

    async def test_restore_clears_health_report(self):
        self.save("Any")
        await self.run_health()
        self.assertIsNotNone(
            (await self.json("GET", "/prompt_manager/system/health"))["report"]
        )
        await self.restore_snapshot()
        body = await self.json("GET", "/prompt_manager/system/health")
        self.assertEqual(body, {"success": True, "report": None})
        overview = await self.json("GET", "/prompt_manager/system/overview")
        self.assertIsNone(overview["health"])

    async def test_restore_refused_while_a_check_runs(self):
        # #249 S8 carry-over: a restore and the health check never interleave
        # (the restore is exclusive), so no check outlives the database it
        # read; a restore after the check forgets its report.
        self.save("Any")
        release = threading.Event()
        entered = threading.Event()
        real = self.api.db.run_health_checks

        def gated(*args, **kwargs):
            entered.set()
            release.wait(WAIT)
            return real(*args, **kwargs)

        with patch.object(self.api.db, "run_health_checks", side_effect=gated):
            body = await self.json("POST", "/prompt_manager/system/health", 202)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, entered.wait, WAIT)
            refused = await self.client.post(
                "/prompt_manager/restore", data=self.snapshot_form()
            )
            self.assertEqual(refused.status, 409)
            self.assertEqual(
                await refused.json(),
                {"success": False, "error": "Health check already running"},
            )
            release.set()
            job = self.api._jobs.get(body["job"]["id"])
            await asyncio.wait_for(asyncio.shield(job.task), WAIT)
        self.assertEqual(job.state, "finished")
        self.assertIsNotNone(
            (await self.json("GET", "/prompt_manager/system/health"))["report"]
        )
        await self.restore_snapshot()
        body = await self.json("GET", "/prompt_manager/system/health")
        self.assertEqual(body, {"success": True, "report": None})

    async def test_health_sources_resolve_off_the_event_loop(self):
        loop_thread = threading.get_ident()
        threads = []
        real = self.api._health_roots

        def recording():
            threads.append(threading.get_ident())
            return real()

        with patch.object(self.api, "_health_roots", side_effect=recording):
            await self.run_health()
        self.assertEqual(len(threads), 1)
        self.assertNotEqual(threads[0], loop_thread)

    async def test_health_start_failure_is_an_error_envelope(self):
        with (
            patch.object(self.api, "_health_roots", side_effect=RuntimeError("secret")),
            self.assertLogs("prompt_manager.api", "ERROR"),
        ):
            body = await self.json("POST", "/prompt_manager/system/health", 500)
        self.assertEqual(
            body,
            {
                "success": False,
                "code": None,
                "error": "Failed to start the health check",
            },
        )
        self.assertEqual(self.api._jobs.list(), [])


class TestOverviewRoute(SystemAPITestCase):
    async def test_overview_reports_real_counts(self):
        self.api.db.save_prompt_pair("Cat", "Blur")
        archived = self.api.db.save_prompt_pair("Cat", "Noise")
        self.api.db.archive_prompt_pair(archived)
        legacy = self.save("Legacy")
        self.image(legacy, "a.png")
        self.image(legacy, "b.png")
        body = await self.json("GET", "/prompt_manager/system/overview")
        self.assertTrue(body["success"])
        self.assertEqual(
            body["library"], {"positive_prompts": 2, "negative_variants": 1}
        )
        self.assertEqual(body["images"], {"indexed": 2, "rated": 0})
        self.assertEqual(
            set(body),
            {
                "success",
                "library",
                "images",
                "health",
                "backup",
                "needs_review",
                "sources",
                "location",
            },
        )

    async def test_overview_rated_counts_images_of_rated_prompts(self):
        rated = self.save("Rated", rating=5)
        self.image(rated, "a.png")
        self.image(rated, "b.png")
        self.image(self.save("Unrated"), "c.png")
        body = await self.json("GET", "/prompt_manager/system/overview")
        self.assertEqual(body["images"], {"indexed": 3, "rated": 2})

    async def test_overview_never_fabricates_backup_or_health(self):
        body = await self.json("GET", "/prompt_manager/system/overview")
        self.assertIsNone(body["health"])
        self.assertIsNone(body["backup"])
        self.assertEqual(body["needs_review"], {"pending": 0})
        self.save("Any")
        await self.run_health()
        report = (await self.json("GET", "/prompt_manager/system/health"))["report"]
        body = await self.json("GET", "/prompt_manager/system/overview")
        self.assertEqual(
            body["health"],
            {"checked_at": report["checked_at"], "status": "incomplete", "issues": 0},
        )
        self.assertIsNone(body["backup"])

    async def test_overview_health_counts_checks_with_issues(self):
        prompt = self.save("Images")
        self.image(prompt, "gone.png", exists=False)
        self.image(prompt, "gone-too.png", exists=False)
        await self.run_health()
        body = await self.json("GET", "/prompt_manager/system/overview")
        self.assertEqual(body["health"]["issues"], 1)

    async def test_overview_sources_distinguish_auto_detect(self):
        body = await self.json("GET", "/prompt_manager/system/overview")
        self.assertEqual(
            body["sources"], {"configured": 0, "effective": 1, "auto_detected": True}
        )
        second = self.output_dir / "archive"
        second.mkdir()
        GalleryConfig.MONITORING_DIRECTORIES = [
            self._root(self.output_dir),
            self._root(second),
        ]
        body = await self.json("GET", "/prompt_manager/system/overview")
        self.assertEqual(
            body["sources"], {"configured": 2, "effective": 2, "auto_detected": False}
        )
        # Every configured source unavailable: ComfyUI's output is used instead.
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.comfy_dir / "absent")]
        body = await self.json("GET", "/prompt_manager/system/overview")
        self.assertEqual(
            body["sources"], {"configured": 1, "effective": 1, "auto_detected": True}
        )

    async def test_overview_never_walks_the_sources_for_thumbnails(self):
        (self.output_dir / "one.png").write_bytes(b"png")
        with patch.object(
            self.api,
            "_plan_gallery_thumbnails",
            side_effect=AssertionError("walked"),
        ):
            body = await self.json("GET", "/prompt_manager/system/overview")
        self.assertTrue(body["success"])
        self.assertNotIn("thumbnails", body)

    async def test_overview_sources_resolve_off_the_event_loop(self):
        loop_thread = threading.get_ident()
        threads = []
        real = self.api._output_dirs_with_origin

        def recording():
            threads.append(threading.get_ident())
            return real()

        with patch.object(self.api, "_output_dirs_with_origin", side_effect=recording):
            body = await self.json("GET", "/prompt_manager/system/overview")
        self.assertEqual(body["sources"]["effective"], 1)
        self.assertEqual(len(threads), 1)
        self.assertNotEqual(threads[0], loop_thread)

    async def test_overview_failure_is_an_error_envelope(self):
        with (
            patch.object(
                self.api.db, "library_counts", side_effect=RuntimeError("secret")
            ),
            self.assertLogs("prompt_manager.api", "ERROR"),
        ):
            body = await self.json("GET", "/prompt_manager/system/overview", 500)
        self.assertEqual(
            body,
            {"success": False, "code": None, "error": "Failed to read the overview"},
        )


class TestStatisticsRoute(SystemAPITestCase):
    async def test_statistics_sum_real_run_counts(self):
        a = self.save("Alpha", category="Landscapes", rating=5)
        b = self.save("Beta", category="Portraits", rating=2)
        self.api.db.record_prompt_use(a, times=3)
        self.api.db.record_prompt_use(b)
        body = await self.json("GET", "/prompt_manager/system/statistics")
        self.assertEqual(
            body["by_category"],
            [
                {"category": "Landscapes", "runs": 3, "average_rating": 5.0},
                {"category": "Portraits", "runs": 1, "average_rating": 2.0},
            ],
        )
        self.assertEqual(body["totals"], self.api.db.get_statistics())
        self.assertTrue(body["success"])

    async def test_statistics_lists_rated_images_for_library_totals(self):
        # D96: the Statistics dialog's "Library totals" reads the rated count
        # with the runs (one read), from the same images the Overview counts.
        rated = self.save("Rated", rating=4)
        self.image(rated, "a.png")
        self.image(self.save("Unrated"), "b.png")
        body = await self.json("GET", "/prompt_manager/system/statistics")
        self.assertEqual(body["rated_images"], 1)
        self.assertEqual(
            set(body), {"success", "by_category", "totals", "rated_images"}
        )

    async def test_statistics_failure_is_an_error_envelope(self):
        with (
            patch.object(
                self.api.db, "usage_by_category", side_effect=RuntimeError("secret")
            ),
            self.assertLogs("prompt_manager.api", "ERROR"),
        ):
            body = await self.json("GET", "/prompt_manager/system/statistics", 500)
        self.assertEqual(
            body,
            {"success": False, "code": None, "error": "Failed to read statistics"},
        )


class TestFailedReadsSettleFirst(SystemAPITestCase):
    """A failed read answers only after the route's other reads finished.

    Answering on the first failure (``asyncio.gather`` without
    ``return_exceptions``) left the other executor reads running against a
    database the caller may already be closing; on Python 3.10 CI that
    crashed the test process inside SQLite.
    """

    async def assert_answers_after_reads(self, url, failing, slow, error):
        raised, release, finished = (threading.Event() for _ in range(3))
        real = getattr(*slow)

        def fail(*args, **kwargs):
            raised.set()
            raise RuntimeError("secret")

        def held(*args, **kwargs):
            release.wait()
            try:
                return real(*args, **kwargs)
            finally:
                finished.set()

        with (
            patch.object(*failing, side_effect=fail),
            patch.object(*slow, side_effect=held),
            self.assertLogs("prompt_manager.api", "ERROR"),
        ):
            answer = asyncio.ensure_future(self.json("GET", url, 500))
            try:
                await asyncio.get_running_loop().run_in_executor(None, raised.wait)
                done, _ = await asyncio.wait({answer}, timeout=0.5)
                self.assertEqual(done, set(), "answered while a read still ran")
            finally:
                release.set()
            body = await answer
        self.assertTrue(finished.is_set())
        self.assertEqual(body, {"success": False, "code": None, "error": error})

    async def test_statistics(self):
        await self.assert_answers_after_reads(
            "/prompt_manager/system/statistics",
            (self.api.db, "usage_by_category"),
            (self.api.db, "get_statistics"),
            "Failed to read statistics",
        )

    async def test_overview(self):
        await self.assert_answers_after_reads(
            "/prompt_manager/system/overview",
            (self.api.db, "library_counts"),
            (self.api, "_output_dirs_with_origin"),
            "Failed to read the overview",
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
