"""
Route tests for the prompt API (py/api/prompts.py).
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

sys.modules.setdefault("folder_paths", MagicMock())
sys.modules.setdefault("server", MagicMock())

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import AioHTTPTestCase  # noqa: E402

from database.operations import PromptDatabase  # noqa: E402
from py.api import PromptManagerAPI  # noqa: E402
from py.api.prompts import (  # noqa: E402
    MAX_PAGE_LIMIT,
    MAX_PAGE_OFFSET,
    safe_error_message,
)
from utils.hashing import generate_prompt_hash  # noqa: E402


class PromptAPITestCase(AioHTTPTestCase):
    """App with PromptManager routes and a temporary SQLite database."""

    async def get_application(self):
        self._temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self._temp_db.close()

        app = web.Application()
        routes = web.RouteTableDef()

        self.api = PromptManagerAPI()
        self.api.db = PromptDatabase(self._temp_db.name)
        self.api.add_routes(routes)
        app.router.add_routes(routes)
        return app

    async def tearDownAsync(self):
        for path in (
            self._temp_db.name,
            self._temp_db.name + "-wal",
            self._temp_db.name + "-shm",
        ):
            if os.path.exists(path):
                os.unlink(path)

    def _save_prompt(self, text="Test prompt", **kwargs):
        return self.api.db.save_prompt(
            text=text, prompt_hash=generate_prompt_hash(text), **kwargs
        )


class TestSafeErrorMessage(unittest.TestCase):
    """Error strings returned to clients never contain absolute server paths."""

    def test_oserror_uses_strerror_and_basename(self):
        secret = os.path.join(os.sep, "srv", "comfy", "output", "hidden.png")
        exc = FileNotFoundError(2, "No such file or directory", secret)

        message = safe_error_message(exc)

        self.assertNotIn(os.path.dirname(secret), message)
        self.assertIn("No such file or directory", message)
        self.assertIn("hidden.png", message)

    def test_oserror_without_filename(self):
        exc = PermissionError(13, "Permission denied")

        self.assertEqual(safe_error_message(exc), "Permission denied")

    def test_plain_exception_uses_str(self):
        self.assertEqual(safe_error_message(ValueError("bad value")), "bad value")

    def test_empty_message_falls_back_to_type_name(self):
        self.assertEqual(safe_error_message(RuntimeError()), "RuntimeError")


class TestSearchBounds(PromptAPITestCase):
    """GET /prompt_manager/search clamps limit/offset and rejects junk."""

    async def test_limit_is_clamped_to_max_page_limit(self):
        resp = await self.client.request("GET", "/prompt_manager/search?limit=999999")

        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["success"])
        self.assertLessEqual(len(data["results"]), MAX_PAGE_LIMIT)
        self.assertEqual(data["pagination"]["limit"], MAX_PAGE_LIMIT)

    async def test_zero_limit_is_raised_to_one(self):
        self._save_prompt("only one")
        resp = await self.client.request("GET", "/prompt_manager/search?limit=0")

        data = await resp.json()
        self.assertEqual(data["pagination"]["limit"], 1)
        self.assertEqual(len(data["results"]), 1)

    async def test_non_integer_limit_is_400(self):
        resp = await self.client.request("GET", "/prompt_manager/search?limit=abc")

        self.assertEqual(resp.status, 400)
        data = await resp.json()
        self.assertFalse(data["success"])
        self.assertIn("error", data)

    async def test_non_integer_offset_is_400(self):
        resp = await self.client.request("GET", "/prompt_manager/search?offset=1.5")

        self.assertEqual(resp.status, 400)

    async def test_negative_offset_is_clamped_to_zero(self):
        # Clamped, not rejected: paging backwards past page one yields page one.
        resp = await self.client.request("GET", "/prompt_manager/search?offset=-5")

        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["pagination"]["offset"], 0)

    async def test_offset_beyond_int64_is_clamped_not_500(self):
        resp = await self.client.request(
            "GET", "/prompt_manager/search?offset=99999999999999999999999"
        )

        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["pagination"]["offset"], MAX_PAGE_OFFSET)

    async def test_offset_skips_results(self):
        for i in range(3):
            self._save_prompt(f"searchable {i}")

        resp = await self.client.request(
            "GET", "/prompt_manager/search?text=searchable&limit=2&offset=2"
        )

        data = await resp.json()
        self.assertEqual(len(data["results"]), 1)


class TestRecentBounds(PromptAPITestCase):
    """GET /prompt_manager/recent clamps limit/offset and rejects junk."""

    async def test_limit_is_clamped_to_max_page_limit(self):
        resp = await self.client.request("GET", "/prompt_manager/recent?limit=999999")

        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertLessEqual(len(data["results"]), MAX_PAGE_LIMIT)
        self.assertEqual(data["pagination"]["limit"], MAX_PAGE_LIMIT)

    async def test_non_integer_limit_is_400(self):
        resp = await self.client.request("GET", "/prompt_manager/recent?limit=abc")

        self.assertEqual(resp.status, 400)
        data = await resp.json()
        self.assertFalse(data["success"])

    async def test_non_integer_page_is_400(self):
        resp = await self.client.request("GET", "/prompt_manager/recent?page=two")

        self.assertEqual(resp.status, 400)

    async def test_negative_offset_is_clamped_to_zero(self):
        # Clamped, not rejected: paging backwards past page one yields page one.
        resp = await self.client.request("GET", "/prompt_manager/recent?offset=-5")

        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["pagination"]["offset"], 0)

    async def test_page_param_derives_offset(self):
        for i in range(5):
            self._save_prompt(f"paged {i}")

        resp = await self.client.request("GET", "/prompt_manager/recent?limit=2&page=3")

        data = await resp.json()
        self.assertEqual(data["pagination"]["offset"], 4)
        self.assertEqual(len(data["results"]), 1)


if __name__ == "__main__":
    unittest.main()
