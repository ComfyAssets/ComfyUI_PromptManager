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
    _stub.get_output_directory = tempfile.gettempdir
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
from py import lora_utils
from py.config import IntegrationConfig, PromptManagerConfig
from py.lora_utils import get_trigger_cache
from utils.hashing import generate_prompt_hash

SECRET_KEY = "sk-civitai-super-secret-0123456789"


class LoraAPITestCase(AioHTTPTestCase):
    """Stands up the PromptManager routes with a temp DB and neutral LoRA config."""

    async def get_application(self):
        self._temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self._temp_db.close()
        self.tmp_root = Path(tempfile.mkdtemp())
        self.custom_nodes = self.tmp_root / "custom_nodes"
        self.custom_nodes.mkdir()
        self._custom_nodes_patch = patch(
            "py.lora_utils.custom_nodes_directories",
            return_value=[self.custom_nodes],
        )
        self._custom_nodes_patch.start()
        self._real_save = PromptManagerConfig.save_to_file

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
        self._save_patch = patch.object(PromptManagerConfig, "save_to_file")
        self.save_mock = self._save_patch.start()
        get_trigger_cache().clear()

        app = web.Application()
        routes = web.RouteTableDef()
        self.api = PromptManagerAPI()
        self.api.db = PromptDatabase(self._temp_db.name)
        self.api.add_routes(routes)
        app.router.add_routes(routes)
        return app

    async def tearDownAsync(self):
        await super().tearDownAsync()  # closes the aiohttp test client
        self._save_patch.stop()
        self._detect_patch.stop()
        self._config_patch.stop()
        self._custom_nodes_patch.stop()
        get_trigger_cache().clear()
        self.api.db.close_all()
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


def _png_bytes():
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (2, 2), (1, 2, 3)).save(buf, "PNG")
    return buf.getvalue()


class _FakeResponse:
    def __init__(self, body):
        self._body = body
        self._pos = 0
        self.headers = {}

    def read(self, amt=-1):
        if amt is None or amt < 0:
            amt = len(self._body)
        chunk = self._body[self._pos : self._pos + amt]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _make_lora_manager(where):
    lm = where / "ComfyUI-Lora-Manager"
    (lm / "py").mkdir(parents=True)
    (lm / "__init__.py").write_text("")
    return lm


def _write_metadata(directory, stem, words=None, images=None, prompt=None):
    meta = {"file_name": f"{stem}.safetensors", "model_name": stem, "civitai": {}}
    if words is not None:
        meta["civitai"]["trainedWords"] = words
    if images is not None:
        meta["civitai"]["images"] = [
            {"url": url, "meta": {"prompt": prompt} if prompt else {}} for url in images
        ]
    path = directory / f"{stem}.safetensors.metadata.json"
    path.write_text(json.dumps(meta))
    return path


class TestLoraDetectRoute(LoraAPITestCase):

    async def test_detected_path_is_public(self):
        lm = _make_lora_manager(self.custom_nodes)
        self.detect_mock.return_value = str(lm)
        resp = await self.client.request("GET", "/prompt_manager/lora/detect")
        body = await resp.text()
        data = json.loads(body)
        self.assertTrue(data["success"])
        self.assertTrue(data["detected"])
        self.assertEqual(data["path"], self.api._public_path(str(lm)))
        self.assertNotIn(str(self.tmp_root), body)

    async def test_not_detected(self):
        resp = await self.client.request("GET", "/prompt_manager/lora/detect")
        data = await resp.json()
        self.assertFalse(data["detected"])
        self.assertEqual(data["path"], "")

    async def test_detection_error_is_500(self):
        self.detect_mock.side_effect = RuntimeError("disk gone")
        resp = await self.client.request("GET", "/prompt_manager/lora/detect")
        self.assertEqual(resp.status, 500)
        self.assertFalse((await resp.json())["success"])


class TestLoraStatusRoute(LoraAPITestCase):

    async def test_status_reports_detection_and_cache_with_public_paths(self):
        lm = _make_lora_manager(self.custom_nodes)
        self.detect_mock.return_value = str(lm)
        IntegrationConfig.LORA_MANAGER_ENABLED = True
        IntegrationConfig.LORA_MANAGER_PATH = str(lm)

        resp = await self.client.request("GET", "/prompt_manager/lora/status")
        body = await resp.text()
        data = json.loads(body)

        self.assertTrue(data["enabled"])
        self.assertTrue(data["detected"])
        public = self.api._public_path(str(lm))
        self.assertEqual(data["path"], public)
        self.assertEqual(data["detected_path"], public)
        self.assertFalse(os.path.isabs(public))
        self.assertNotIn(str(self.tmp_root), body)
        self.assertFalse(data["trigger_cache_loaded"])

    async def test_status_error_is_500(self):
        with patch("py.lora_utils.get_trigger_cache", side_effect=RuntimeError("x")):
            resp = await self.client.request("GET", "/prompt_manager/lora/status")
        self.assertEqual(resp.status, 500)

    async def test_status_error_hides_the_server_path(self):
        err = PermissionError(13, "Permission denied", str(self.tmp_root / "c.json"))
        with patch("py.lora_utils.get_trigger_cache", side_effect=err):
            resp = await self.client.request("GET", "/prompt_manager/lora/status")
        self.assertEqual(resp.status, 500)
        body = await resp.text()
        self.assertNotIn(str(self.tmp_root), body)
        self.assertIn("c.json", body)


class TestLoraEnableRoute(LoraAPITestCase):

    async def test_enable_with_unknown_path_is_400(self):
        resp = await self._post_json(
            "/prompt_manager/lora/enable", {"enabled": True, "path": "/nowhere"}
        )
        self.assertEqual(resp.status, 400)
        self.assertFalse(IntegrationConfig.LORA_MANAGER_ENABLED)
        self.save_mock.assert_not_called()

    async def test_enable_persists_and_loads_trigger_cache(self):
        lm = _make_lora_manager(self.custom_nodes)
        loras = self.tmp_root / "loras"
        loras.mkdir()
        _write_metadata(loras, "neon", words=["glow"])
        self.detect_mock.return_value = str(lm)

        with patch.object(
            lora_utils, "find_lora_directories", return_value=[str(loras)]
        ):
            resp = await self._post_json(
                "/prompt_manager/lora/enable",
                {
                    "enabled": True,
                    "path": str(lm),
                    "trigger_words_enabled": True,
                    "civitai_api_key": SECRET_KEY,
                },
            )

        self.assertEqual(resp.status, 200)
        body = await resp.text()
        data = json.loads(body)
        self.assertTrue(data["success"])
        self.assertEqual(data["path"], self.api._public_path(str(lm)))
        self.assertNotIn(str(self.tmp_root), body)
        self.assertNotIn(SECRET_KEY, body)
        self.assertTrue(IntegrationConfig.LORA_MANAGER_ENABLED)
        self.assertEqual(
            IntegrationConfig.LORA_MANAGER_PATH,
            os.path.normcase(os.path.realpath(str(lm))),
        )
        self.assertEqual(IntegrationConfig.CIVITAI_API_KEY, SECRET_KEY)
        self.save_mock.assert_called_once_with()
        self.assertTrue(get_trigger_cache().is_loaded)
        self.assertEqual(get_trigger_cache().get_trigger_words("neon"), ["glow"])

    async def test_enable_accepts_the_public_relative_path(self):
        lm = _make_lora_manager(self.custom_nodes)
        self.detect_mock.return_value = str(lm)
        public = self.api._public_path(str(lm))
        self.assertFalse(os.path.isabs(public))

        resp = await self._post_json(
            "/prompt_manager/lora/enable", {"enabled": True, "path": public}
        )

        self.assertEqual(resp.status, 200)
        self.assertTrue(IntegrationConfig.LORA_MANAGER_ENABLED)
        self.assertEqual(
            IntegrationConfig.LORA_MANAGER_PATH,
            os.path.normcase(os.path.realpath(str(lm))),
        )

    async def test_enable_with_path_outside_custom_nodes_is_400(self):
        lm = _make_lora_manager(self.tmp_root)
        self.detect_mock.return_value = str(lm)

        resp = await self._post_json(
            "/prompt_manager/lora/enable", {"enabled": True, "path": str(lm)}
        )

        self.assertEqual(resp.status, 400)
        body = await resp.text()
        self.assertFalse(json.loads(body)["success"])
        self.assertNotIn(str(self.tmp_root), body)
        self.assertFalse(IntegrationConfig.LORA_MANAGER_ENABLED)
        self.save_mock.assert_not_called()

    async def test_enable_with_custom_node_lacking_lora_in_its_name_is_400(self):
        lm = _make_lora_manager(self.custom_nodes).parent / "ComfyUI-Other"
        (lm / "py").mkdir(parents=True)
        (lm / "__init__.py").write_text("")
        self.detect_mock.return_value = str(lm)

        resp = await self._post_json(
            "/prompt_manager/lora/enable", {"enabled": True, "path": str(lm)}
        )

        self.assertEqual(resp.status, 400)
        self.assertFalse(IntegrationConfig.LORA_MANAGER_ENABLED)

    async def test_enable_rejects_an_auto_detected_path_outside_custom_nodes(self):
        self.detect_mock.return_value = str(_make_lora_manager(self.tmp_root))

        resp = await self._post_json("/prompt_manager/lora/enable", {"enabled": True})

        self.assertEqual(resp.status, 400)
        self.assertFalse(IntegrationConfig.LORA_MANAGER_ENABLED)

    async def test_enable_writes_the_configured_config_file(self):
        config_file = self.tmp_root / "cfg" / "config.json"
        self.save_mock.side_effect = self._real_save

        with patch.dict(os.environ, {"PROMPT_MANAGER_CONFIG_PATH": str(config_file)}):
            resp = await self._post_json(
                "/prompt_manager/lora/enable",
                {"enabled": False, "trigger_words_enabled": True},
            )

        self.assertEqual(resp.status, 200)
        self.save_mock.assert_called_once_with()
        self.assertTrue(config_file.is_file())
        saved = json.loads(config_file.read_text())["integrations"]["lora_manager"]
        self.assertFalse(saved["enabled"])
        self.assertTrue(saved["trigger_words_enabled"])

    async def test_disable_clears_cache(self):
        get_trigger_cache()._cache = {"x": ["y"]}
        get_trigger_cache()._loaded = True

        resp = await self._post_json("/prompt_manager/lora/enable", {"enabled": False})

        self.assertEqual(resp.status, 200)
        self.assertFalse(get_trigger_cache().is_loaded)
        self.assertFalse(IntegrationConfig.LORA_MANAGER_ENABLED)

    async def test_empty_key_keeps_the_stored_key(self):
        IntegrationConfig.CIVITAI_API_KEY = SECRET_KEY

        resp = await self._post_json(
            "/prompt_manager/lora/enable", {"enabled": False, "civitai_api_key": ""}
        )

        self.assertEqual(resp.status, 200)
        self.assertEqual(IntegrationConfig.CIVITAI_API_KEY, SECRET_KEY)

    async def test_absent_key_keeps_the_stored_key(self):
        IntegrationConfig.CIVITAI_API_KEY = SECRET_KEY

        await self._post_json("/prompt_manager/lora/enable", {"enabled": False})

        self.assertEqual(IntegrationConfig.CIVITAI_API_KEY, SECRET_KEY)

    async def test_new_key_replaces_the_stored_key(self):
        IntegrationConfig.CIVITAI_API_KEY = SECRET_KEY

        await self._post_json(
            "/prompt_manager/lora/enable",
            {"enabled": False, "civitai_api_key": "  new-key  "},
        )

        self.assertEqual(IntegrationConfig.CIVITAI_API_KEY, "new-key")

    async def test_clear_flag_removes_the_stored_key(self):
        IntegrationConfig.CIVITAI_API_KEY = SECRET_KEY

        resp = await self._post_json(
            "/prompt_manager/lora/enable",
            {"enabled": False, "clear_civitai_api_key": True},
        )

        self.assertEqual(resp.status, 200)
        self.assertEqual(IntegrationConfig.CIVITAI_API_KEY, "")

    async def test_non_string_key_is_400(self):
        resp = await self._post_json(
            "/prompt_manager/lora/enable", {"enabled": False, "civitai_api_key": 123}
        )
        self.assertEqual(resp.status, 400)

    async def test_persist_failure_is_500(self):
        self.save_mock.side_effect = OSError("read-only")
        resp = await self._post_json("/prompt_manager/lora/enable", {"enabled": False})
        self.assertEqual(resp.status, 500)


class TestLoraScanRoute(LoraAPITestCase):

    def _enable(self):
        IntegrationConfig.LORA_MANAGER_ENABLED = True
        IntegrationConfig.LORA_MANAGER_PATH = str(_make_lora_manager(self.custom_nodes))
        IntegrationConfig.CIVITAI_API_KEY = SECRET_KEY

    async def test_configured_path_outside_custom_nodes_is_400(self):
        IntegrationConfig.LORA_MANAGER_ENABLED = True
        IntegrationConfig.LORA_MANAGER_PATH = str(_make_lora_manager(self.tmp_root))

        resp = await self.client.request("POST", "/prompt_manager/lora/scan")

        self.assertEqual(resp.status, 400)
        body = await resp.text()
        self.assertFalse(json.loads(body)["success"])
        self.assertNotIn(str(self.tmp_root), body)

    async def test_scan_error_hides_the_server_path(self):
        self._enable()
        err = PermissionError(13, "Permission denied", str(self.tmp_root / "x.db"))
        with patch.object(self.api.db, "delete_prompts_by_category", side_effect=err):
            resp = await self.client.request("POST", "/prompt_manager/lora/scan")
        body = await resp.text()
        self.assertNotIn(str(self.tmp_root), body)
        self.assertIn("x.db", body)

    @staticmethod
    def _sse_events(text):
        return [
            json.loads(line[len("data: ") :])
            for line in text.splitlines()
            if line.startswith("data: ")
        ]

    async def _scan(self, loras):
        opener_calls = []

        def fake_open(req, timeout=None):
            opener_calls.append(req)
            return _FakeResponse(_png_bytes())

        cache_dir = self.tmp_root / "img_cache"
        with (
            patch.object(
                lora_utils, "find_lora_directories", return_value=[str(loras)]
            ),
            patch.object(
                lora_utils, "get_lora_image_cache_dir", return_value=cache_dir
            ),
            patch.object(lora_utils, "_open_url", fake_open),
        ):
            resp = await self.client.request("POST", "/prompt_manager/lora/scan")
            text = await resp.text()
        return resp, self._sse_events(text), opener_calls

    async def test_not_enabled_is_400(self):
        resp = await self.client.request("POST", "/prompt_manager/lora/scan")
        self.assertEqual(resp.status, 400)

    async def test_no_path_is_400(self):
        IntegrationConfig.LORA_MANAGER_ENABLED = True
        resp = await self.client.request("POST", "/prompt_manager/lora/scan")
        self.assertEqual(resp.status, 400)

    async def test_scan_imports_loras_with_images_and_links_existing(self):
        self._enable()
        loras = self.tmp_root / "loras"
        loras.mkdir()
        _write_metadata(
            loras,
            "neon",
            words=["glow"],
            images=["https://civitai.com/a.png", "https://evil.example/b.png"],
            prompt="neon street",
        )
        (loras / "neon.png").write_bytes(_png_bytes())
        _write_metadata(loras, "plain")
        (loras / "broken.metadata.json").write_text("{{{")
        existing_id = self.api.db.save_prompt(
            text="plain",
            category=None,
            tags=[],
            rating=None,
            prompt_hash=generate_prompt_hash("plain"),
        )

        resp, events, opener_calls = await self._scan(loras)

        self.assertEqual(resp.status, 200)
        done = events[-1]
        self.assertEqual(done["type"], "complete")
        self.assertEqual(done["total"], 3)
        self.assertEqual(done["imported"], 1)
        self.assertEqual(done["skipped"], 2)
        self.assertEqual(
            [r.full_url for r in opener_calls], ["https://civitai.com/a.png"]
        )
        self.assertEqual(
            opener_calls[0].get_header("Authorization"), f"Bearer {SECRET_KEY}"
        )
        imported = self.api.db.search_prompts(category="lora-manager")
        self.assertEqual(len(imported), 1)
        self.assertEqual(imported[0]["text"], "neon street")
        self.assertIn("lora:neon", imported[0]["tags"])
        self.assertIn("glow", imported[0]["tags"])
        self.assertEqual(len(self.api.db.get_prompt_images(imported[0]["id"])), 2)
        self.assertEqual(len(self.api.db.get_prompt_images(existing_id)), 0)

    async def test_save_failure_is_counted_as_skipped(self):
        self._enable()
        loras = self.tmp_root / "loras"
        loras.mkdir()
        _write_metadata(loras, "neon", words=["glow"])

        with patch.object(self.api.db, "save_prompt", side_effect=RuntimeError("db")):
            resp, events, _ = await self._scan(loras)

        self.assertEqual(events[-1]["imported"], 0)
        self.assertEqual(events[-1]["skipped"], 1)


class TestLoraTriggerWordRoutes(LoraAPITestCase):

    async def test_trigger_words_not_enabled_is_400(self):
        resp = await self.client.request(
            "GET", "/prompt_manager/lora/trigger-words?name=x"
        )
        self.assertEqual(resp.status, 400)

    async def test_trigger_words_missing_name_is_400(self):
        IntegrationConfig.LORA_MANAGER_ENABLED = True
        resp = await self.client.request("GET", "/prompt_manager/lora/trigger-words")
        self.assertEqual(resp.status, 400)

    async def test_trigger_words_loads_cache_on_demand(self):
        IntegrationConfig.LORA_MANAGER_ENABLED = True
        IntegrationConfig.LORA_MANAGER_PATH = str(self.tmp_root)
        loras = self.tmp_root / "loras"
        loras.mkdir()
        _write_metadata(loras, "neon", words=["glow", "bright"])

        with patch.object(
            lora_utils, "find_lora_directories", return_value=[str(loras)]
        ):
            resp = await self.client.request(
                "GET", "/prompt_manager/lora/trigger-words?name=NEON"
            )

        data = await resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["trigger_words"], ["glow", "bright"])
        self.assertTrue(get_trigger_cache().is_loaded)

    async def test_trigger_words_error_is_500(self):
        IntegrationConfig.LORA_MANAGER_ENABLED = True
        with patch("py.lora_utils.get_trigger_cache", side_effect=RuntimeError("x")):
            resp = await self.client.request(
                "GET", "/prompt_manager/lora/trigger-words?name=x"
            )
        self.assertEqual(resp.status, 500)

    async def test_refresh_not_enabled_is_400(self):
        resp = await self.client.request("POST", "/prompt_manager/lora/refresh-cache")
        self.assertEqual(resp.status, 400)

    async def test_refresh_without_path_is_400(self):
        IntegrationConfig.LORA_MANAGER_ENABLED = True
        resp = await self.client.request("POST", "/prompt_manager/lora/refresh-cache")
        self.assertEqual(resp.status, 400)

    async def test_refresh_reloads_cache(self):
        IntegrationConfig.LORA_MANAGER_ENABLED = True
        IntegrationConfig.LORA_MANAGER_PATH = str(self.tmp_root)
        loras = self.tmp_root / "loras"
        loras.mkdir()
        _write_metadata(loras, "neon", words=["glow"])

        with patch.object(
            lora_utils, "find_lora_directories", return_value=[str(loras)]
        ):
            resp = await self.client.request(
                "POST", "/prompt_manager/lora/refresh-cache"
            )

        data = await resp.json()
        self.assertTrue(data["success"])
        # keyed by both the file_name stem and the metadata-file stem
        self.assertEqual(data["loras_with_trigger_words"], 2)

    async def test_refresh_error_is_500(self):
        IntegrationConfig.LORA_MANAGER_ENABLED = True
        IntegrationConfig.LORA_MANAGER_PATH = str(self.tmp_root)
        with patch("py.lora_utils.get_trigger_cache", side_effect=RuntimeError("x")):
            resp = await self.client.request(
                "POST", "/prompt_manager/lora/refresh-cache"
            )
        self.assertEqual(resp.status, 500)


if __name__ == "__main__":
    unittest.main()
