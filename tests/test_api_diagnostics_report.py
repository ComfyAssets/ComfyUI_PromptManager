"""The shareable diagnostic report (#329, py/api/diagnostics_report.py).

``GET /prompt_manager/system/diagnostics`` builds the report on the server
from an explicit list of fields: the project version, the four legacy checks,
public paths only and no secret. A scrub replaces anything that still looks
like an absolute path or holds the CivitAI key and counts it in ``redacted``;
these tests require ``redacted == 0`` so a builder mistake fails instead of
being hidden.
"""

import json
import ntpath
import os
import posixpath
import re
import sqlite3
import sys
import unittest
from unittest import mock
from unittest.mock import MagicMock

_mock_server = MagicMock()
_mock_server.PromptServer.instance.routes = MagicMock()
sys.modules.setdefault("server", _mock_server)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from py.api import admin, diagnostics_report  # noqa: E402
from py.api.diagnostics_report import OUTSIDE, report_path, scrub  # noqa: E402
from py.config import (
    GalleryConfig,
    IntegrationConfig,
    PromptManagerConfig,
)  # noqa: E402

try:
    from tests.test_api_admin import AdminAPITestCase  # noqa: E402
except ImportError:  # discovered with tests/ as the top-level directory
    from test_api_admin import AdminAPITestCase  # noqa: E402

REPORT = "/prompt_manager/system/diagnostics"
KEY = "civitai-secret-0123456789abcdef"
SEEDED_PATHS = (
    "/home/someone/private/LoraManager",
    "/srv/someone/renders",
    "C:\\Users\\someone\\Documents\\renders",
    "D:/Users/someone/prompts.db",
    "\\\\nas\\share\\someone\\renders",
)
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")
TOP_KEYS = {
    "success",
    "generated_at",
    "version",
    "python",
    "platform",
    "viewerjs",
    "checks",
    "database_info",
    "sources",
    "logging",
    "health",
    "config",
    "redacted",
}
CONFIG = {
    "database": {"enable_duplicate_detection", "enable_auto_save"},
    "web_ui": {
        "result_timeout",
        "show_test_button",
        "webui_display_mode",
        "infinite_scroll",
    },
    "performance": {
        "max_search_results",
        "enable_fuzzy_search",
        "auto_backup_interval",
        "worker_threads",
    },
    "gallery": {
        "processing_delay",
        "prompt_timeout",
        "cleanup_interval",
        "auto_cleanup_missing_files",
        "max_image_age_days",
        "max_concurrent_processing",
        "metadata_extraction_timeout",
    },
    "integrations": {"lora_manager_enabled", "has_civitai_api_key"},
}


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


class DiagnosticsReportTestCase(AdminAPITestCase):
    async def get_application(self):
        app = await super().get_application()
        saved = IntegrationConfig.get_config()
        self.addCleanup(IntegrationConfig.update_config, saved)
        return app

    async def report(self, status=200):
        response = await self.client.request("GET", REPORT)
        text = await response.text()
        self.assertEqual(response.status, status, text)
        return json.loads(text), text

    def seed_secrets(self):
        IntegrationConfig.update_config(
            {
                "lora_manager": {
                    "enabled": True,
                    "path": SEEDED_PATHS[0],
                    "civitai_api_key": KEY,
                }
            }
        )
        GalleryConfig.MONITORING_DIRECTORIES = list(SEEDED_PATHS[1:3]) + [
            SEEDED_PATHS[4]
        ]
        PromptManagerConfig.update_config(
            {"database": {"default_path": SEEDED_PATHS[3]}}
        )

    def assert_no_paths_or_key(self, report, text):
        self.assertNotIn(KEY, text)
        self.assertNotIn(self.tmpdir, text)
        self.assertNotIn(self.tmpdir.replace("\\", "/"), text)
        for seeded in SEEDED_PATHS:
            self.assertNotIn(seeded, text)
            self.assertNotIn(json.dumps(seeded)[1:-1], text)
        self.assertNotIn("someone", text)
        for value in _strings(report):
            self.assertFalse(posixpath.isabs(value), value)
            self.assertFalse(ntpath.isabs(value), value)
            self.assertIsNone(_WINDOWS_ABSOLUTE.match(value), value)
            self.assertFalse(value.startswith("\\\\"), value)


class TestReport(DiagnosticsReportTestCase):
    async def test_report_has_version_viewerjs_and_the_four_checks(self):
        report, _text = await self.report()
        self.assertEqual(set(report), TOP_KEYS)
        self.assertTrue(report["success"])
        with open(
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "pyproject.toml"),
            encoding="utf-8",
        ) as handle:
            version = re.search(r'version\s*=\s*["\']([^"\']+)["\']', handle.read())
        self.assertEqual(report["version"], version.group(1))
        self.assertRegex(report["viewerjs"], r"^\d+\.\d+\.\d+$")
        self.assertRegex(report["python"], r"^3\.\d+\.\d+")
        self.assertEqual(set(report["platform"]), {"system", "release"})
        self.assertEqual(
            set(report["checks"]),
            {"database", "dependencies", "comfyui_output", "image_monitor"},
        )
        self.assertEqual(report["checks"]["database"]["status"], "ok")
        self.assertEqual(
            set(report["sources"]),
            {"configured", "effective", "watching", "unavailable"},
        )
        self.assertEqual(set(report["logging"]), {"config", "stats"})
        self.assertIsNone(report["health"])
        self.assertEqual(report["redacted"], 0)
        self.assertRegex(report["generated_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$")

    async def test_report_has_no_secret_or_absolute_path(self):
        self.seed_secrets()
        with mock.patch.object(
            admin.sqlite3,
            "connect",
            side_effect=sqlite3.DatabaseError("file is not a database"),
        ):
            report, text = await self.report()
        self.assertEqual(report["checks"]["database"]["status"], "error")
        self.assertTrue(report["config"]["integrations"]["has_civitai_api_key"])
        self.assertTrue(report["config"]["integrations"]["lora_manager_enabled"])
        self.assertEqual(report["sources"]["configured"], 3)
        self.assert_no_paths_or_key(report, text)
        self.assertEqual(report["redacted"], 0)

    async def test_report_leaves_get_config_unchanged(self):
        self.seed_secrets()
        before = json.dumps(PromptManagerConfig.get_config(), sort_keys=True)
        directories = GalleryConfig.MONITORING_DIRECTORIES
        await self.report()
        self.assertEqual(
            json.dumps(PromptManagerConfig.get_config(), sort_keys=True), before
        )
        self.assertIs(GalleryConfig.MONITORING_DIRECTORIES, directories)
        self.assertEqual(IntegrationConfig.CIVITAI_API_KEY, KEY)

    def test_outside_path_reads_outside_comfyui(self):
        inside = os.path.join(str(self.comfy_dir), "user", "__promptmanager", "logs")
        self.assertEqual(report_path(inside), "user/__promptmanager/logs")
        self.assertEqual(
            report_path(os.path.join(str(self.outside_dir), "vito")), "outside ComfyUI"
        )
        self.assertEqual(report_path(""), "")
        self.assertEqual(report_path(None), "")

    def test_a_filesystem_root_anchor_never_makes_a_path_public(self):
        # --output-directory /renders makes "/" an anchor; a path outside
        # ComfyUI must still read "outside ComfyUI", never "home/<user>/...".
        root = os.path.abspath(os.sep)
        anchors = [str(self.comfy_dir), root]
        with mock.patch.object(GalleryConfig, "path_anchors", return_value=anchors):
            self.assertEqual(
                report_path(os.path.join(str(self.outside_dir), "x")), OUTSIDE
            )
            self.assertEqual(
                report_path(os.path.join(str(self.comfy_dir), "output")), "output"
            )

    def test_scrub_removes_the_home_folder_without_its_root(self):
        home = os.path.expanduser("~")
        relative = os.path.splitdrive(home)[1].lstrip("\\/").replace("\\", "/")
        cleaned = scrub({"a": f"{relative}/area/prompts.db", "redacted": 0}, None)
        self.assertEqual(cleaned, {"a": "[removed]", "redacted": 1})

    def test_scrub_removes_a_leaked_path_and_counts_it(self):
        leaked = {
            "a": "/home/someone/x",
            "b": ["C:\\Users\\someone", "D:/data", "\\\\nas\\share", "ok"],
            "c": {"d": f"token {KEY} here", "e": 3, "f": None, "g": "relative/path"},
            "redacted": 0,
        }
        cleaned = scrub(leaked, KEY)
        self.assertEqual(cleaned["redacted"], 5)
        self.assertEqual(cleaned["a"], "[removed]")
        self.assertEqual(cleaned["b"], ["[removed]"] * 3 + ["ok"])
        self.assertEqual(
            cleaned["c"], {"d": "[removed]", "e": 3, "f": None, "g": "relative/path"}
        )
        # A short key is not searched for (it would match ordinary words).
        self.assertEqual(scrub({"x": "abc", "redacted": 0}, "abc")["redacted"], 0)

    async def test_report_refused_while_exclusive_job_runs(self):
        async with self.api._jobs.hold(
            "restore", "Database restore", admission="exclusive"
        ) as restore:
            report, _text = await self.report(status=409)
        self.assertEqual(report["code"], "busy")
        self.assertEqual(
            report["running"], {"id": restore.id, "title": "Database restore"}
        )

    async def test_report_lists_config_only_keys(self):
        self.seed_secrets()
        report, _text = await self.report()
        self.assertEqual({k: set(v) for k, v in report["config"].items()}, CONFIG)
        self.assertNotIn("default_path", json.dumps(report["config"]))
        self.assertNotIn("directories", json.dumps(report["config"]))
        self.assertNotIn(
            "civitai_api_key",
            json.dumps(report["config"]).replace("has_civitai_api_key", ""),
        )

    async def test_report_logging_has_public_directory(self):
        report, _text = await self.report()
        stats = report["logging"]["stats"]
        self.assertFalse(posixpath.isabs(stats["log_directory"]))
        self.assertIn("level", report["logging"]["config"])

    async def test_report_survives_an_unreadable_viewerjs_provenance(self):
        with mock.patch.object(
            diagnostics_report,
            "VIEWERJS_PROVENANCE",
            os.path.join(self.tmpdir, "nope.json"),
        ):
            report, _text = await self.report()
        self.assertEqual(report["viewerjs"], "unknown")

    async def test_a_build_failure_is_coded_without_details(self):
        with mock.patch.object(
            self.api, "build_report", side_effect=RuntimeError(self.tmpdir)
        ):
            with self.assertLogs("prompt_manager", "ERROR"):
                report, text = await self.report(status=500)
        self.assertNotIn(self.tmpdir, text)
        self.assertFalse(report["success"])


class TestLegacyDiagnostics(DiagnosticsReportTestCase):
    async def test_legacy_diagnostics_keeps_its_four_checks(self):
        response = await self.client.request("GET", "/prompt_manager/diagnostics")
        data = await response.json()
        self.assertEqual(response.status, 200)
        self.assertEqual(set(data), {"success", "diagnostics"})
        self.assertEqual(
            set(data["diagnostics"]),
            {"database", "dependencies", "comfyui_output", "image_monitor"},
        )
        report, _text = await self.report()
        self.assertEqual(report["checks"]["database"], data["diagnostics"]["database"])
        self.assertEqual(
            report["checks"]["dependencies"], data["diagnostics"]["dependencies"]
        )

    async def test_both_routes_build_their_checks_with_one_helper(self):
        with mock.patch.object(
            admin, "legacy_checks", wraps=admin.legacy_checks
        ) as spy:
            await self.client.request("GET", "/prompt_manager/diagnostics")
            await self.report()
        self.assertEqual(spy.call_count, 2)


if __name__ == "__main__":
    unittest.main()
