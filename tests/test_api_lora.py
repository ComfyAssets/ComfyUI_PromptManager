"""Route tests for the LoraManager integration endpoints.

Filesystem detection and network downloads are patched out so the tests run
anywhere without a LoraManager install or a CivitAI connection.
"""

import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if "folder_paths" not in sys.modules:
    _stub = types.ModuleType("folder_paths")
    _stub.models_dir = tempfile.gettempdir()
    _stub.base_path = tempfile.gettempdir()
    _stub.get_output_directory = lambda: tempfile.gettempdir()
    _stub.get_folder_paths = lambda name: []
    sys.modules["folder_paths"] = _stub

if "server" not in sys.modules:
    _server = MagicMock()
    _server.PromptServer.instance.routes = MagicMock()
    sys.modules["server"] = _server

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from database.operations import PromptDatabase
from py.api import PromptManagerAPI
from py.config import IntegrationConfig
from py.lora_utils import get_trigger_cache

SECRET_KEY = "sk-civitai-super-secret-0123456789"


class LoraAPITestCase(AioHTTPTestCase):
    """Stands up the PromptManager routes with a temp DB and neutral LoRA config."""

    async def get_application(self):
        self._temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self._temp_db.close()
        self.tmp_root = Path(tempfile.mkdtemp())

        self._config_patch = patch.multiple(
            IntegrationConfig,
            LORA_MANAGER_ENABLED=False,
            LORA_MANAGER_PATH="",
            LORA_TRIGGER_WORDS_ENABLED=False,
            CIVITAI_API_KEY="",
        )
        self._config_patch.start()
        self._detect_patch = patch(
            "py.lora_utils.detect_lora_manager", return_value=None
        )
        self.detect_mock = self._detect_patch.start()
        get_trigger_cache().clear()

        app = web.Application()
        routes = web.RouteTableDef()
        self.api = PromptManagerAPI()
        self.api.db = PromptDatabase(self._temp_db.name)
        self.api.add_routes(routes)
        app.router.add_routes(routes)
        return app

    async def tearDownAsync(self):
        self._detect_patch.stop()
        self._config_patch.stop()
        get_trigger_cache().clear()
        for path in (
            self._temp_db.name,
            self._temp_db.name + "-wal",
            self._temp_db.name + "-shm",
        ):
            if os.path.exists(path):
                os.unlink(path)
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    async def _post_json(self, url, body):
        return await self.client.request(
            "POST",
            url,
            data=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )


class TestLoraStatusNeverReturnsKey(LoraAPITestCase):

    async def test_status_reports_flag_without_the_key(self):
        IntegrationConfig.CIVITAI_API_KEY = SECRET_KEY

        resp = await self.client.request("GET", "/prompt_manager/lora/status")

        self.assertEqual(resp.status, 200)
        body = await resp.text()
        data = json.loads(body)
        self.assertTrue(data["success"])
        self.assertIs(data["has_civitai_api_key"], True)
        self.assertNotIn("civitai_api_key", data)
        self.assertNotIn(SECRET_KEY, body)

    async def test_status_flag_is_false_without_a_key(self):
        resp = await self.client.request("GET", "/prompt_manager/lora/status")

        data = await resp.json()
        self.assertIs(data["has_civitai_api_key"], False)
        self.assertNotIn("civitai_api_key", data)


if __name__ == "__main__":
    unittest.main()
