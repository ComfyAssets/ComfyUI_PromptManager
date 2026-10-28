"""Reviewed rotated-log cleanup as a maintenance plan (#329, py/api/log_cleanup.py).

``POST /maintenance/preview {operation: "rotated_logs"}`` lists exactly the
rotated copies Rule R allows and writes nothing; ``POST /maintenance/apply``
deletes exactly those as a ``maintenance`` job (admission ``read``), or
nothing, failing ``plan-changed``, when any of them changed after the review.

Every test runs on a real ``PromptManagerLogger`` whose log folder is a
temporary folder, never the real one.
"""

import asyncio
import json
import logging
import ntpath
import os
import posixpath
import re
import sys
import threading
import unittest
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from py.api.maintenance_plans import PlanStore  # noqa: E402
from utils import logging_config  # noqa: E402
from utils.logging_config import PromptManagerLogger, get_logger_manager  # noqa: E402

try:
    from tests.open_handles import assert_closed  # noqa: E402
    from tests.test_api_maintenance import MaintenanceAPITestCase  # noqa: E402
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import assert_closed  # noqa: E402
    from test_api_maintenance import MaintenanceAPITestCase  # noqa: E402

WAIT = 10
PREVIEW = "/prompt_manager/maintenance/preview"
APPLY = "/prompt_manager/maintenance/apply"
CHANGED = "The log files changed since the review; nothing was deleted. Review again."
MEDIA = "Deletes only the listed log files. No image, video or library record changes."
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


class LogCleanupTestCase(MaintenanceAPITestCase):
    async def get_application(self):
        app = await super().get_application()
        package = os.path.join(self.tmpdir, "pkg")
        os.makedirs(os.path.join(package, "utils"))
        fake_file = os.path.join(package, "utils", "logging_config.py")
        with mock.patch.object(logging_config, "__file__", fake_file):
            self.manager = object.__new__(PromptManagerLogger)  # bypass singleton
            self.manager.__init__()
        self.addCleanup(self._restore_global_logging)
        self.manager.update_config({"console_logging": False})
        self._only_this_thread()
        self.log_dir = str(self.manager.log_dir)
        self.app_logger = logging.getLogger("prompt_manager.log_cleanup_test")
        self.app_logger.info("active log line")
        self.api._get_logger_manager = lambda: self.manager
        self.outside = os.path.join(self.tmpdir, "link-targets")
        os.makedirs(self.outside)
        return app

    def _only_this_thread(self):
        """Only this test's records reach the temporary log (not other threads')."""
        this_thread = threading.get_ident()
        for handler in self.manager.logger.handlers:
            handler.filters.clear()
            handler.addFilter(lambda record: record.thread == this_thread)

    def _restore_global_logging(self):
        for handler in logging.getLogger("prompt_manager").handlers[:]:
            handler.close()
        assert_closed(self, self.log_dir)
        get_logger_manager()._setup_loggers()

    def path(self, name):
        return os.path.join(self.log_dir, name)

    def write(self, name, data=b"rotated\n"):
        with open(self.path(name), "wb") as handle:
            handle.write(data)

    def symlink(self, name):
        target = os.path.join(self.outside, "secret.txt")
        with open(target, "wb") as handle:
            handle.write(b"secret " * 100)
        try:
            os.symlink(target, self.path(name))
        except (OSError, NotImplementedError):  # pragma: no cover - no privilege
            return False
        return True

    def names(self):
        return sorted(os.listdir(self.log_dir))

    def snapshot(self):
        """Every entry but the active log, with its bytes (None for a folder)."""
        state = {}
        for name in self.names():
            if name == "prompt_manager.log":
                continue
            path = self.path(name)
            if os.path.islink(path):
                state[name] = ("link", os.readlink(path))
            elif os.path.isdir(path):
                state[name] = None
            else:
                with open(path, "rb") as handle:
                    state[name] = handle.read()
        return state

    def seed(self):
        """CUJ 5's folder: three rotated copies, a .bak, a folder and a link."""
        self.write("prompt_manager.log.1", b"a" * 1000)
        self.write("prompt_manager.log.2", b"b" * 2000)
        self.write("prompt_manager.log.3", b"c" * 3000)
        self.write("prompt_manager.log.bak", b"mine")
        os.makedirs(self.path("prompt_manager.log.9"))
        return self.symlink("prompt_manager.log.4")

    async def preview(self, status=200, **body):
        return await self.post(
            PREVIEW, {"operation": "rotated_logs", "options": {}, **body}, status
        )

    async def apply(self, token, status=202):
        return await self.post(APPLY, {"token": token}, status)

    async def run_apply(self, plan):
        body = await self.apply(plan["token"])
        job = self.api._jobs.get(body["job"]["id"])
        await asyncio.wait_for(asyncio.shield(job.task), WAIT)
        return body["job"], job


class TestPreview(LogCleanupTestCase):
    async def test_preview_lists_only_rotated_copies(self):
        linked = self.seed()
        plan = await self.preview()
        self.assertEqual(
            set(plan),
            {
                "success",
                "token",
                "operation",
                "expires_at",
                "scope",
                "protected",
                "backup",
                "media",
                "details",
            },
        )
        self.assertEqual(plan["operation"], "rotated_logs")
        self.assertEqual(
            plan["scope"], {"count": 3, "label": "3 rotated log copies · 5.9 KB"}
        )
        self.assertEqual(
            [row["label"] for row in plan["details"]],
            ["prompt_manager.log.1", "prompt_manager.log.2", "prompt_manager.log.3"],
        )
        self.assertRegex(
            plan["details"][2]["detail"], r"^2\.9 KB · \d{4}-\d\d-\d\d \d\d:\d\d$"
        )
        reasons = ["Kept: the active log"]
        if linked:
            reasons.append("Kept: 1 link")
        reasons.append("Kept: 2 other files")
        self.assertEqual(
            plan["protected"], {"count": len(reasons) + 1, "reasons": reasons}
        )
        self.assertEqual(
            plan["backup"],
            {"policy": "none", "destination_label": None, "expected_bytes": None},
        )
        self.assertEqual(plan["media"], {"deletes_files": True, "label": MEDIA})
        self.assertTrue(plan["expires_at"].endswith("+00:00"))
        self.assertNotIn("mtime_ns", json.dumps(plan))

    async def test_preview_writes_nothing(self):
        self.seed()
        before = self.snapshot()
        await self.preview()
        self.assertEqual(self.snapshot(), before)

    async def test_preview_has_no_paths(self):
        self.seed()
        plan = await self.preview()
        for text in _strings(plan):
            self.assertFalse(posixpath.isabs(text), text)
            self.assertFalse(ntpath.isabs(text), text)
            self.assertIsNone(_WINDOWS_ABSOLUTE.match(text), text)

    async def test_preview_rejects_non_empty_options(self):
        for options in ({"all": True}, [], "x"):
            with self.subTest(options=options):
                reply = await self.preview(status=400, options=options)
                self.assertEqual(reply["code"], "invalid-request")

    async def test_preview_too_large_is_413(self):
        self.seed()
        self.api._maintenance_plans = PlanStore(max_bytes=10)
        reply = await self.preview(status=413)
        self.assertEqual(
            reply,
            {
                "success": False,
                "code": "too-large",
                "error": "Too many log files to review at once.",
            },
        )

    async def test_empty_folder_plan_has_scope_zero(self):
        plan = await self.preview()
        self.assertEqual(plan["scope"], {"count": 0, "label": "No rotated log copies"})
        self.assertEqual(plan["details"], [])
        self.assertEqual(
            plan["protected"], {"count": 1, "reasons": ["Kept: the active log"]}
        )

    async def test_preview_refused_while_an_exclusive_job_runs(self):
        async with self.api._jobs.hold(
            "restore", "Database restore", admission="exclusive"
        ):
            reply = await self.preview(status=409)
        self.assertEqual(reply["code"], "busy")


class TestApply(LogCleanupTestCase):
    async def test_apply_deletes_exactly_the_reviewed_files(self):
        linked = self.seed()
        plan = await self.preview()
        summary, job = await self.run_apply(plan)
        self.assertEqual(summary["kind"], "maintenance")
        self.assertEqual(summary["title"], "Rotated log copies")
        self.assertEqual(summary["admission"], "read")
        self.assertFalse(summary["cancellable"])
        self.assertEqual(summary["scope"], {"operation": "rotated_logs"})
        self.assertEqual(job.state, "finished", job.log)
        self.assertEqual(
            job.counters,
            {"changed": 3, "protected": plan["protected"]["count"], "errors": 0},
        )
        expected = [
            "prompt_manager.log",
            "prompt_manager.log.9",
            "prompt_manager.log.bak",
        ]
        if linked:
            expected.append("prompt_manager.log.4")
        self.assertEqual(self.names(), sorted(expected))
        self.assertEqual(job.status_line, "Deleted 3 rotated log copies")
        report = json.dumps(job.report())
        self.assertNotIn(self.tmpdir, report)

    async def test_apply_rows_name_each_file(self):
        self.seed()
        plan = await self.preview()
        _summary, job = await self.run_apply(plan)
        self.assertEqual(
            job.outcomes["changed"],
            [
                {
                    "path": "prompt_manager.log.1",
                    "root": "",
                    "root_index": None,
                    "detail": "Deleted · 1000 bytes",
                },
                {
                    "path": "prompt_manager.log.2",
                    "root": "",
                    "root_index": None,
                    "detail": "Deleted · 2.0 KB",
                },
                {
                    "path": "prompt_manager.log.3",
                    "root": "",
                    "root_index": None,
                    "detail": "Deleted · 2.9 KB",
                },
            ],
        )
        log = "\n".join(job.log)
        for n in (1, 2, 3):
            self.assertIn(f"prompt_manager.log.{n}", log)

    async def test_apply_after_rollover_fails_plan_changed_and_deletes_nothing(self):
        self.seed()
        plan = await self.preview()
        handler = next(
            h
            for h in self.manager.logger.handlers
            if isinstance(h, logging.FileHandler)
        )
        handler.acquire()
        try:
            handler.doRollover()  # every listed name now holds another file
        finally:
            handler.release()
        before = self.snapshot()
        _summary, job = await self.run_apply(plan)
        self.assertEqual(job.state, "failed")
        self.assertEqual(job.scope["failure"], "plan-changed")
        self.assertEqual(job.status_line, CHANGED)
        self.assertEqual(job.counters["changed"], 0)
        self.assertEqual(self.snapshot(), before)

    async def test_apply_reports_a_file_in_use(self):
        self.seed()
        plan = await self.preview()
        real_unlink = os.unlink
        held = self.path("prompt_manager.log.2")

        def unlink(path, *args, **kwargs):
            if os.fspath(path) == held:
                error = PermissionError(
                    13,
                    "The process cannot access the file because it is being "
                    "used by another process",
                    held,
                )
                error.winerror = 32
                raise error
            return real_unlink(path, *args, **kwargs)

        with mock.patch.object(logging_config.os, "unlink", unlink):
            _summary, job = await self.run_apply(plan)
        self.assertEqual(job.counters["changed"], 2)
        self.assertEqual(job.counters["errors"], 1)
        [row] = job.outcomes["errors"]
        self.assertEqual(row["path"], "prompt_manager.log.2")
        self.assertIn("being used by another process", row["detail"])
        self.assertNotIn(self.tmpdir, json.dumps(job.report()))
        self.assertIn("prompt_manager.log.2", self.names())
        self.assertEqual(
            job.status_line, "Deleted 2 rotated log copies, 1 could not be deleted"
        )

    async def test_token_is_single_use(self):
        self.seed()
        plan = await self.preview()
        summary, _job = await self.run_apply(plan)
        again = await self.apply(plan["token"], 409)
        self.assertEqual(again["code"], "plan-used")
        self.assertEqual(
            again["job"], {"id": summary["id"], "title": "Rotated log copies"}
        )

    async def test_apply_runs_beside_a_scan(self):
        self.seed()
        plan = await self.preview()
        async with self.api._jobs.hold("scan", "Metadata scan", admission="records"):
            _summary, job = await self.run_apply(plan)
        self.assertEqual(job.state, "finished", job.log)
        self.assertNotIn("prompt_manager.log.1", self.names())

    async def test_apply_refused_while_restore_runs(self):
        self.seed()
        plan = await self.preview()
        async with self.api._jobs.hold(
            "restore", "Database restore", admission="exclusive"
        ) as restore:
            busy = await self.apply(plan["token"], 409)
        self.assertEqual(busy["code"], "busy")
        self.assertEqual(
            busy["running"], {"id": restore.id, "title": "Database restore"}
        )
        self.assertIn("prompt_manager.log.1", self.names())
        # The refused apply kept the token.
        _summary, job = await self.run_apply(plan)
        self.assertEqual(job.state, "finished", job.log)

    async def test_apply_refused_beside_another_maintenance_job(self):
        self.seed()
        plan = await self.preview()
        async with self.api._jobs.hold(
            "maintenance", "Optimize database", admission="records"
        ):
            busy = await self.apply(plan["token"], 409)
        self.assertEqual(busy["code"], "busy")

    async def test_active_log_survives_and_keeps_growing(self):
        self.seed()
        plan = await self.preview()
        await self.run_apply(plan)
        self.app_logger.info("written after the cleanup")
        with open(self.path("prompt_manager.log"), "rb") as handle:
            content = handle.read()
        self.assertIn(b"active log line", content)
        self.assertIn(b"written after the cleanup", content)

    async def test_an_unexpected_failure_fails_the_job_without_details(self):
        self.seed()
        plan = await self.preview()
        with mock.patch.object(
            self.manager, "delete_rotated", side_effect=RuntimeError(self.tmpdir)
        ):
            with self.assertLogs("prompt_manager", "ERROR"):
                _summary, job = await self.run_apply(plan)
        self.assertEqual(job.state, "failed")
        self.assertNotIn(self.tmpdir, json.dumps(job.report()))
        self.assertEqual(job.status_line, "The log cleanup failed.")


TRUNCATE = "/prompt_manager/logs/truncate"
FILES = "/prompt_manager/logs/files"


class TestClearAllLogsConfirmed(LogCleanupTestCase):
    """#389: POST /logs/truncate deletes no more than the confirmation listed."""

    async def confirmed(self):
        response = await self.client.request("GET", FILES)
        data = await response.json()
        return [
            {"name": row["filename"], "identity": row["identity"]}
            for row in data["files"]
            if row["kind"] in ("active", "rotated")
        ]

    def handler(self):
        return next(
            h
            for h in self.manager.logger.handlers
            if isinstance(h, logging.FileHandler)
        )

    async def test_a_rollover_after_the_confirmation_is_refused(self):
        self.write("prompt_manager.log.1", b"a" * 100)
        self.write("prompt_manager.log.2", b"b" * 200)
        expected = await self.confirmed()
        self.app_logger.info("written after the confirmation")
        self.handler().doRollover()
        before = self.snapshot()
        body = await self.post(TRUNCATE, {"expected": expected}, 409)
        self.assertEqual(body["success"], False)
        self.assertEqual(body["code"], "logs-changed")
        self.assertIn("nothing was cleared", body["error"])
        self.assertEqual(self.snapshot(), before)

    async def test_the_confirmed_files_are_cleared(self):
        self.write("prompt_manager.log.1", b"a" * 100)
        expected = await self.confirmed()
        body = await self.post(TRUNCATE, {"expected": expected}, 200)
        self.assertEqual(
            sorted(body["results"]["truncated"]),
            ["prompt_manager.log", "prompt_manager.log.1"],
        )
        self.assertEqual(self.names(), ["prompt_manager.log"])

    async def test_the_legacy_call_without_a_body_still_clears(self):
        self.write("prompt_manager.log.1", b"a" * 100)
        response = await self.client.request("POST", TRUNCATE)
        self.assertEqual(response.status, 200, await response.text())
        self.assertEqual(self.names(), ["prompt_manager.log"])

    async def test_a_foreign_writer_is_refused_with_a_clear_message(self):
        self.write("prompt_manager.log.1", b"a" * 100)
        with open(self.path("prompt_manager.log"), "ab") as other:
            other.write(b"line from another ComfyUI\n")
        body = await self.post(TRUNCATE, {"expected": await self.confirmed()}, 409)
        self.assertEqual(body["code"], "logs-foreign-writer")
        self.assertIn("Another program is writing", body["error"])
        self.assertIn("prompt_manager.log.1", self.names())

    async def test_a_malformed_confirmation_is_400(self):
        self.write("prompt_manager.log.1", b"a" * 100)
        for expected in ("all", [{"name": "prompt_manager.log.1", "identity": 5}]):
            with self.subTest(expected=expected):
                await self.post(TRUNCATE, {"expected": expected}, 400)
        self.assertIn("prompt_manager.log.1", self.names())


if __name__ == "__main__":
    unittest.main()
