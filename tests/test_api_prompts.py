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
from py.api.prompts import safe_error_message  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
