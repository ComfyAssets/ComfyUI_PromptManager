"""Route tests for the logging API (py/api/logging_routes.py)."""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

_mock_server = MagicMock()
_mock_server.PromptServer.instance.routes = MagicMock()
sys.modules["server"] = _mock_server

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

        app = web.Application()
        routes = web.RouteTableDef()
        self.api = PromptManagerAPI()
        self.api.db = PromptDatabase(os.path.join(self.tmpdir, "prompts.db"))
        self.api._get_logger_manager = lambda: self.manager
        self.api.add_routes(routes)
        app.router.add_routes(routes)
        return app

    async def tearDownAsync(self):
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
            "max_file_size": 2048,
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


if __name__ == "__main__":
    unittest.main()
