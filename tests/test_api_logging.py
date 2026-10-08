"""Route tests for the logging API (py/api/logging_routes.py)."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

_mock_server = MagicMock()
_mock_server.PromptServer.instance.routes = MagicMock()
sys.modules.setdefault("server", _mock_server)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import AioHTTPTestCase  # noqa: E402

from database.operations import PromptDatabase  # noqa: E402
from py.api import PromptManagerAPI  # noqa: E402
from py.api.logging_routes import LOG_CONFIG_KEYS  # noqa: E402


class FakeLoggerManager:
    """Stand-in for PromptManagerLogger with a scratch log directory."""

    def __init__(self, log_dir):
        self.log_dir = Path(log_dir)
        self.config = {
            "level": "INFO",
            "max_file_size": 10 * 1024 * 1024,
            "backup_count": 5,
            "console_logging": True,
            "file_logging": True,
            "buffer_size": 1000,
        }
        self.update_calls = []
        self.logs = []

    def update_config(self, new_config):
        self.update_calls.append(dict(new_config))
        self.config.update(new_config)

    def get_config(self):
        return dict(self.config)

    def get_recent_logs(self, limit=100, level=None):
        logs = self.logs
        if level:
            logs = [entry for entry in logs if entry["level"] == level.upper()]
        return list(reversed(logs[-limit:]))

    def get_log_files(self):
        files = []
        for path in sorted(self.log_dir.glob("*.log*")):
            files.append(
                {
                    "filename": path.name,
                    "path": str(path),
                    "size": path.stat().st_size,
                    "modified": "2026-10-07T00:00:00",
                    "is_main": path.name == "prompt_manager.log",
                }
            )
        return files

    def truncate_logs(self):
        truncated = []
        for path in self.log_dir.glob("prompt_manager.log*"):
            path.write_text("")
            truncated.append(path.name)
        return {"truncated": truncated, "errors": []}

    def get_log_stats(self):
        return {
            "buffer_count": len(self.logs),
            "level_counts": {},
            "log_files_count": len(self.get_log_files()),
            "total_log_size": 0,
            "log_directory": str(self.log_dir),
            "current_level": self.config["level"],
        }


class LoggingAPITestCase(AioHTTPTestCase):

    async def get_application(self):
        self.tmpdir = tempfile.mkdtemp()
        self.log_dir = Path(self.tmpdir) / "logs"
        self.log_dir.mkdir()
        self.manager = FakeLoggerManager(self.log_dir)
        # Public paths are rendered relative to ComfyUI's base directory. Other
        # test modules leave their own ``folder_paths`` stub behind, so declare
        # ours explicitly instead of depending on import order.
        self._saved_folder_paths = sys.modules.get("folder_paths")
        fake_folder_paths = MagicMock()
        fake_folder_paths.base_path = self.tmpdir
        fake_folder_paths.get_output_directory.return_value = os.path.join(
            self.tmpdir, "output"
        )
        sys.modules["folder_paths"] = fake_folder_paths

        app = web.Application()
        routes = web.RouteTableDef()
        self.api = PromptManagerAPI()
        self.api.db = PromptDatabase(os.path.join(self.tmpdir, "prompts.db"))
        self.api._get_logger_manager = lambda: self.manager
        self.api.add_routes(routes)
        app.router.add_routes(routes)
        return app

    async def tearDownAsync(self):
        await super().tearDownAsync()  # closes the aiohttp test client
        if self._saved_folder_paths is None:
            sys.modules.pop("folder_paths", None)
        else:
            sys.modules["folder_paths"] = self._saved_folder_paths
        self.api.db.close_all()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    async def _post_config(self, payload):
        return await self.client.request(
            "POST", "/prompt_manager/logs/config", json=payload
        )


class TestUpdateLogConfigWhitelist(LoggingAPITestCase):

    def test_whitelist_matches_logger_manager_keys(self):
        self.assertEqual(LOG_CONFIG_KEYS, frozenset(self.manager.config.keys()))

    async def test_unknown_keys_rejected_and_listed(self):
        resp = await self._post_config({"level": "INFO", "evil": 1, "log_dir": "/x"})

        self.assertEqual(resp.status, 400)
        data = await resp.json()
        self.assertFalse(data["success"])
        self.assertIn("evil", data["error"])
        self.assertIn("log_dir", data["error"])
        self.assertEqual(self.manager.update_calls, [])

    async def test_invalid_level_rejected(self):
        resp = await self._post_config({"level": "VERBOSE"})
        self.assertEqual(resp.status, 400)
        self.assertEqual(self.manager.update_calls, [])

    async def test_level_must_be_a_string(self):
        resp = await self._post_config({"level": 10})
        self.assertEqual(resp.status, 400)
        self.assertEqual(self.manager.update_calls, [])

    async def test_level_is_normalised_to_upper_case(self):
        resp = await self._post_config({"level": "debug"})
        self.assertEqual(resp.status, 200)
        self.assertEqual(self.manager.update_calls, [{"level": "DEBUG"}])

    async def test_negative_int_rejected(self):
        resp = await self._post_config({"backup_count": -1})
        self.assertEqual(resp.status, 400)
        self.assertEqual(self.manager.update_calls, [])

    async def test_numeric_string_rejected(self):
        resp = await self._post_config({"max_file_size": "1024"})
        self.assertEqual(resp.status, 400)
        self.assertEqual(self.manager.update_calls, [])

    async def test_bool_is_not_an_int(self):
        resp = await self._post_config({"buffer_size": True})
        self.assertEqual(resp.status, 400)
        self.assertEqual(self.manager.update_calls, [])

    async def test_flags_must_be_bool(self):
        resp = await self._post_config({"console_logging": "yes"})
        self.assertEqual(resp.status, 400)
        self.assertEqual(self.manager.update_calls, [])

    async def test_valid_payload_is_applied(self):
        payload = {
            "level": "WARNING",
            "max_file_size": 2 * 1024 * 1024,
            "backup_count": 0,
            "console_logging": False,
            "file_logging": True,
            "buffer_size": 50,
        }
        resp = await self._post_config(payload)

        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(self.manager.update_calls, [payload])
        self.assertEqual(data["config"]["level"], "WARNING")

    async def test_max_file_size_is_bounded_to_1_to_1024_mb(self):
        mb = 1024 * 1024
        for bad in (0, mb - 1, 1024 * mb + 1, 10**12):
            resp = await self._post_config({"max_file_size": bad})
            self.assertEqual(resp.status, 400, bad)
            self.assertIn("max_file_size", (await resp.json())["error"])
        self.assertEqual(self.manager.update_calls, [])
        for ok in (mb, 1024 * mb):
            resp = await self._post_config({"max_file_size": ok})
            self.assertEqual(resp.status, 200, ok)
        self.assertEqual(
            self.manager.update_calls,
            [{"max_file_size": mb}, {"max_file_size": 1024 * mb}],
        )

    async def test_backup_count_is_bounded_to_0_to_50(self):
        for bad in (-1, 51, 10**6):
            resp = await self._post_config({"backup_count": bad})
            self.assertEqual(resp.status, 400, bad)
            self.assertIn("backup_count", (await resp.json())["error"])
        self.assertEqual(self.manager.update_calls, [])
        for ok in (0, 50):
            resp = await self._post_config({"backup_count": ok})
            self.assertEqual(resp.status, 200, ok)

    async def test_body_must_be_an_object(self):
        resp = await self._post_config(["level", "DEBUG"])
        self.assertEqual(resp.status, 400)
        self.assertEqual(self.manager.update_calls, [])

    async def test_malformed_json_is_400(self):
        resp = await self.client.request(
            "POST",
            "/prompt_manager/logs/config",
            data=b"{not json",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.status, 400)
        self.assertEqual(self.manager.update_calls, [])

    async def test_oversized_body_is_413(self):
        body = json.dumps({"level": "x" * 1_000_100}).encode()
        resp = await self.client.request(
            "POST",
            "/prompt_manager/logs/config",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.status, 413)
        self.assertFalse((await resp.json())["success"])
        self.assertEqual(self.manager.update_calls, [])


class TestNoAbsolutePathsInLogResponses(LoggingAPITestCase):

    async def test_log_files_have_no_absolute_path(self):
        (self.log_dir / "prompt_manager.log").write_text("hello")

        resp = await self.client.request("GET", "/prompt_manager/logs/files")

        body = await resp.text()
        self.assertEqual(resp.status, 200)
        self.assertNotIn(self.tmpdir, body)
        data = json.loads(body)
        self.assertEqual(data["files"][0]["filename"], "prompt_manager.log")

    async def test_log_stats_directory_is_not_absolute(self):
        resp = await self.client.request("GET", "/prompt_manager/logs/stats")

        body = await resp.text()
        self.assertEqual(resp.status, 200)
        self.assertNotIn(self.tmpdir, body)
        self.assertEqual(json.loads(body)["stats"]["log_directory"], "logs")

    async def test_error_from_manager_does_not_leak_path(self):
        def boom():
            raise FileNotFoundError(
                2, "No such file", os.path.join(self.tmpdir, "x.log")
            )

        self.manager.get_log_stats = boom

        resp = await self.client.request("GET", "/prompt_manager/logs/stats")

        body = await resp.text()
        self.assertEqual(resp.status, 500)
        self.assertNotIn(self.tmpdir, body)
        self.assertIn("x.log", json.loads(body)["error"])


class TestGetLogs(LoggingAPITestCase):

    def _fill(self, n):
        for i in range(n):
            self.manager.logs.append(
                {"level": "INFO" if i % 2 else "ERROR", "message": f"m{i}"}
            )

    async def test_default_limit_and_count(self):
        self._fill(3)
        resp = await self.client.request("GET", "/prompt_manager/logs")
        data = await resp.json()
        self.assertEqual(resp.status, 200)
        self.assertTrue(data["success"])
        self.assertEqual(data["count"], 3)
        self.assertEqual(data["limit"], 100)
        self.assertIsNone(data["level_filter"])

    async def test_level_filter_passed_through(self):
        self._fill(4)
        resp = await self.client.request("GET", "/prompt_manager/logs?level=error")
        data = await resp.json()
        self.assertEqual(data["level_filter"], "error")
        self.assertEqual(data["count"], 2)

    async def test_limit_is_clamped_to_500(self):
        resp = await self.client.request("GET", "/prompt_manager/logs?limit=99999")
        self.assertEqual((await resp.json())["limit"], 500)

    async def test_limit_below_one_becomes_one(self):
        self._fill(3)
        resp = await self.client.request("GET", "/prompt_manager/logs?limit=0")
        data = await resp.json()
        self.assertEqual(data["limit"], 1)
        self.assertEqual(data["count"], 1)

    async def test_non_integer_limit_is_400(self):
        resp = await self.client.request("GET", "/prompt_manager/logs?limit=ten")
        self.assertEqual(resp.status, 400)
        self.assertFalse((await resp.json())["success"])

    async def test_manager_failure_is_500_with_empty_logs(self):
        def boom(**kwargs):
            raise RuntimeError("buffer gone")

        self.manager.get_recent_logs = boom
        resp = await self.client.request("GET", "/prompt_manager/logs")
        data = await resp.json()
        self.assertEqual(resp.status, 500)
        self.assertEqual(data["logs"], [])


class TestLogFilesAndDownload(LoggingAPITestCase):

    async def test_files_failure_is_500(self):
        def boom():
            raise RuntimeError("no dir")

        self.manager.get_log_files = boom
        resp = await self.client.request("GET", "/prompt_manager/logs/files")
        data = await resp.json()
        self.assertEqual(resp.status, 500)
        self.assertEqual(data["files"], [])

    async def test_download_existing_file(self):
        (self.log_dir / "prompt_manager.log").write_text("line1\nline2\n")

        resp = await self.client.request(
            "GET", "/prompt_manager/logs/download/prompt_manager.log"
        )

        self.assertEqual(resp.status, 200)
        self.assertIn("attachment", resp.headers["Content-Disposition"])
        self.assertEqual(await resp.text(), "line1\nline2\n")

    async def test_download_missing_file_is_404(self):
        resp = await self.client.request(
            "GET", "/prompt_manager/logs/download/nope.log"
        )
        self.assertEqual(resp.status, 404)

    async def test_download_rejects_traversal(self):
        resp = await self.client.request(
            "GET", "/prompt_manager/logs/download/..%2F..%2Fetc%2Fpasswd"
        )
        self.assertEqual(resp.status, 400)

    async def test_download_rejects_backslash(self):
        resp = await self.client.request(
            "GET", "/prompt_manager/logs/download/..%5Cx.log"
        )
        self.assertEqual(resp.status, 400)

    async def test_download_read_failure_is_500(self):
        (self.log_dir / "dir.log").mkdir()
        resp = await self.client.request("GET", "/prompt_manager/logs/download/dir.log")
        body = await resp.text()
        self.assertEqual(resp.status, 500)
        self.assertNotIn(self.tmpdir, body)


class TestTruncateConfigAndStats(LoggingAPITestCase):

    async def test_truncate_success(self):
        (self.log_dir / "prompt_manager.log").write_text("data")
        resp = await self.client.request("POST", "/prompt_manager/logs/truncate")
        data = await resp.json()
        self.assertEqual(resp.status, 200)
        self.assertEqual(data["results"]["truncated"], ["prompt_manager.log"])
        self.assertEqual((self.log_dir / "prompt_manager.log").read_text(), "")

    async def test_truncate_failure_is_500(self):
        def boom():
            raise RuntimeError("locked")

        self.manager.truncate_logs = boom
        resp = await self.client.request("POST", "/prompt_manager/logs/truncate")
        self.assertEqual(resp.status, 500)

    async def test_get_config(self):
        resp = await self.client.request("GET", "/prompt_manager/logs/config")
        data = await resp.json()
        self.assertEqual(resp.status, 200)
        self.assertEqual(data["config"]["level"], "INFO")

    async def test_get_config_failure_is_500(self):
        def boom():
            raise RuntimeError("gone")

        self.manager.get_config = boom
        resp = await self.client.request("GET", "/prompt_manager/logs/config")
        self.assertEqual(resp.status, 500)

    async def test_update_config_manager_failure_is_500(self):
        def boom(config):
            raise RuntimeError("handler error")

        self.manager.update_config = boom
        resp = await self.client.request(
            "POST", "/prompt_manager/logs/config", json={"level": "INFO"}
        )
        self.assertEqual(resp.status, 500)

    async def test_stats_success(self):
        resp = await self.client.request("GET", "/prompt_manager/logs/stats")
        data = await resp.json()
        self.assertEqual(resp.status, 200)
        self.assertEqual(data["stats"]["current_level"], "INFO")


class TestLoggerManagerResolution(LoggingAPITestCase):

    def test_default_manager_is_the_singleton(self):
        from py.api.logging_routes import LoggingRoutesMixin
        from utils.logging_config import get_logger_manager

        manager = LoggingRoutesMixin._get_logger_manager(self.api)
        self.assertIs(manager, get_logger_manager())


if __name__ == "__main__":
    unittest.main()
