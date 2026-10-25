"""Route tests for the LoraManager integration endpoints.

Filesystem detection and network downloads are patched out so the tests run
anywhere without a LoraManager install or a CivitAI connection.
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import threading
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
from py import config_store, lora_import, lora_utils
from py.config import IntegrationConfig, PromptManagerConfig
from py.api import settings_routes as settings_module
from py.api import lora_integration as lora_module
from py.lora_utils import get_trigger_cache
from utils.hashing import generate_prompt_hash

try:
    from tests.lock_probe import ProbeLock
    from tests.open_handles import assert_closed
except ImportError:  # discovered with tests/ as the top-level directory
    from lock_probe import ProbeLock
    from open_handles import assert_closed

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
        # Never the repository's config.json (live user data): every save in
        # these tests goes to a file in this test's temp directory.
        self.config_file = self.tmp_root / "config.json"
        self._env_patch = patch.dict(
            os.environ, {"PROMPT_MANAGER_CONFIG_PATH": str(self.config_file)}
        )
        self._env_patch.start()

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
        self.api = PromptManagerAPI(db=PromptDatabase(self._temp_db.name))
        self.api.add_routes(routes)
        app.router.add_routes(routes)
        return app

    async def tearDownAsync(self):
        await super().tearDownAsync()  # closes the aiohttp test client
        self._env_patch.stop()
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
        assert_closed(self, self.tmp_root)
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def _saved(self):
        return json.loads(self.config_file.read_text())

    def _make_unwritable(self):
        """Point the config path under a regular file, so no write can succeed."""
        blocker = self.tmp_root / "not-a-dir"
        blocker.write_text("x")
        self.config_file = blocker / "config.json"
        os.environ["PROMPT_MANAGER_CONFIG_PATH"] = str(self.config_file)

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
        self.assertTrue(data["detected_usable"])

    async def test_status_says_a_detected_install_outside_custom_nodes_is_unusable(
        self,
    ):
        # S10 review N5: found (a linked install), but enabling it is refused.
        outside = _make_lora_manager(self.tmp_root)
        self.detect_mock.return_value = str(outside)
        resp = await self.client.request("GET", "/prompt_manager/lora/status")
        data = await resp.json()
        self.assertEqual((data["detected"], data["detected_usable"]), (True, False))

        self.detect_mock.return_value = None
        resp = await self.client.request("GET", "/prompt_manager/lora/status")
        self.assertFalse((await resp.json())["detected_usable"])

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
        self.assertFalse(self.config_file.exists())

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
        saved = self._saved()["integrations"]["lora_manager"]
        self.assertEqual(saved["civitai_api_key"], SECRET_KEY)
        self.assertEqual(saved["path"], IntegrationConfig.LORA_MANAGER_PATH)
        self.assertIs(saved["enabled"], True)
        self.assertIs(saved["trigger_words_enabled"], True)
        self.assertEqual(
            (data["persisted"], data["runtime_applied"], data["cache_loaded"]),
            (True, True, True),
        )
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
        self.assertFalse(self.config_file.exists())

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

        with patch.dict(os.environ, {"PROMPT_MANAGER_CONFIG_PATH": str(config_file)}):
            resp = await self._post_json(
                "/prompt_manager/lora/enable",
                {"enabled": False, "trigger_words_enabled": True},
            )

        self.assertEqual(resp.status, 200)
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
        self._make_unwritable()
        with self.assertLogs("prompt_manager.api", level="ERROR"):
            resp = await self._post_json(
                "/prompt_manager/lora/enable", {"enabled": False}
            )
        self.assertEqual(resp.status, 500)
        self.assertEqual((await resp.json())["code"], "config-write-failed")

    async def test_enable_write_failure_reports_and_keeps_runtime(self):
        # #220: a save that cannot be written publishes nothing and says so.
        lm = _make_lora_manager(self.custom_nodes)
        self.detect_mock.return_value = str(lm)
        IntegrationConfig.CIVITAI_API_KEY = SECRET_KEY
        self._make_unwritable()

        with self.assertLogs("prompt_manager.api", level="ERROR"):
            resp = await self._post_json(
                "/prompt_manager/lora/enable",
                {
                    "enabled": True,
                    "path": str(lm),
                    "trigger_words_enabled": True,
                    "clear_civitai_api_key": True,
                },
            )

        text = await resp.text()
        body = json.loads(text)
        self.assertEqual(resp.status, 500)
        self.assertEqual(body["code"], "config-write-failed")
        self.assertEqual((body["persisted"], body["runtime_applied"]), (False, False))
        self.assertFalse(IntegrationConfig.LORA_MANAGER_ENABLED)
        self.assertEqual(IntegrationConfig.LORA_MANAGER_PATH, "")
        self.assertFalse(IntegrationConfig.LORA_TRIGGER_WORDS_ENABLED)
        self.assertEqual(IntegrationConfig.CIVITAI_API_KEY, SECRET_KEY)
        self.assertFalse(get_trigger_cache().is_loaded)
        self.assertNotIn(SECRET_KEY, text)
        self.assertNotIn(str(self.tmp_root), text)

    async def test_enable_preserves_unknown_sections(self):
        stored = "stored-value"  # a plain marker: what is on disk must stay
        self.config_file.write_text(
            json.dumps(
                {
                    "custom_section": {"keep": True},
                    "web_ui": {"result_timeout": 11},
                    "integrations": {
                        "lora_manager": {"civitai_api_key": stored, "extra": 1},
                        "other_tool": {"on": True},
                    },
                }
            )
        )
        IntegrationConfig.CIVITAI_API_KEY = stored

        resp = await self._post_json(
            "/prompt_manager/lora/enable",
            {"enabled": False, "trigger_words_enabled": True},
        )

        self.assertEqual(resp.status, 200)
        saved = self._saved()
        self.assertEqual(saved["custom_section"], {"keep": True})
        self.assertEqual(saved["web_ui"], {"result_timeout": 11})
        self.assertEqual(saved["integrations"]["other_tool"], {"on": True})
        lora = saved["integrations"]["lora_manager"]
        self.assertEqual(lora["extra"], 1)
        self.assertEqual(lora["civitai_api_key"], stored)
        self.assertIs(lora["trigger_words_enabled"], True)
        self.assertNotIn("database", saved)  # no runtime defaults written

    async def test_enable_refuses_a_corrupt_config_file(self):
        self.config_file.write_bytes(b"{not json")
        resp = await self._post_json(
            "/prompt_manager/lora/enable",
            {"enabled": False, "trigger_words_enabled": True},
        )
        body = await resp.json()
        self.assertEqual((resp.status, body["code"]), (409, "config-unreadable"))
        self.assertEqual(self.config_file.read_bytes(), b"{not json")
        self.assertFalse(IntegrationConfig.LORA_TRIGGER_WORDS_ENABLED)

    async def test_enable_with_a_stale_revision_changes_nothing(self):
        # System S10: the Save row sends the revision it read; a file changed
        # since is a settings-changed conflict and nothing is written.
        self.config_file.write_text(json.dumps({"web_ui": {"result_timeout": 9}}))
        resp = await self.client.request("GET", "/prompt_manager/settings")
        revision = (await resp.json())["revision"]
        self.config_file.write_text(json.dumps({"web_ui": {"result_timeout": 10}}))
        before = self.config_file.read_bytes()

        resp = await self._post_json(
            "/prompt_manager/lora/enable",
            {
                "enabled": False,
                "trigger_words_enabled": True,
                "expected_revision": revision,
            },
        )
        body = await resp.json()

        self.assertEqual((resp.status, body["code"]), (409, "settings-changed"))
        self.assertEqual(self.config_file.read_bytes(), before)
        self.assertFalse(IntegrationConfig.LORA_TRIGGER_WORDS_ENABLED)

        resp = await self._post_json(
            "/prompt_manager/lora/enable",
            {
                "enabled": False,
                "trigger_words_enabled": True,
                "expected_revision": body["revision"],
            },
        )
        saved = await resp.json()
        self.assertEqual(resp.status, 200)
        self.assertTrue(saved["revision"].startswith("sha256:"))
        self.assertTrue(IntegrationConfig.LORA_TRIGGER_WORDS_ENABLED)

        resp = await self._post_json(
            "/prompt_manager/lora/enable",
            {"enabled": False, "expected_revision": 7},
        )
        self.assertEqual(resp.status, 400)

    async def test_enable_requires_boolean_flags_and_body_cap(self):
        for body in (
            {"enabled": "yes"},
            {"enabled": False, "trigger_words_enabled": 1},
            {"enabled": False, "clear_civitai_api_key": "true"},
        ):
            with self.subTest(body=body):
                resp = await self._post_json("/prompt_manager/lora/enable", body)
                self.assertEqual(resp.status, 400)
        resp = await self._post_json("/prompt_manager/lora/enable", [True])
        self.assertEqual(resp.status, 400)
        resp = await self._post_json(
            "/prompt_manager/lora/enable",
            {"enabled": False, "civitai_api_key": "k" * (2 * 1024 * 1024)},
        )
        self.assertEqual(resp.status, 413)
        self.assertFalse(self.config_file.exists())
        self.assertFalse(IntegrationConfig.LORA_MANAGER_ENABLED)

    async def test_blank_key_unchanged_and_explicit_clear(self):
        self.config_file.write_text(
            json.dumps({"integrations": {"lora_manager": {"civitai_api_key": "old"}}})
        )
        IntegrationConfig.CIVITAI_API_KEY = "old"

        resp = await self._post_json(
            "/prompt_manager/lora/enable", {"enabled": False, "civitai_api_key": "  "}
        )
        self.assertEqual(resp.status, 200)
        self.assertEqual(
            self._saved()["integrations"]["lora_manager"]["civitai_api_key"], "old"
        )
        self.assertEqual(IntegrationConfig.CIVITAI_API_KEY, "old")

        resp = await self._post_json(
            "/prompt_manager/lora/enable",
            {"enabled": False, "clear_civitai_api_key": True},
        )
        self.assertEqual(resp.status, 200)
        self.assertEqual(
            self._saved()["integrations"]["lora_manager"]["civitai_api_key"], ""
        )
        self.assertEqual(IntegrationConfig.CIVITAI_API_KEY, "")

    async def test_cache_load_failure_reports_persisted_but_unloaded(self):
        lm = _make_lora_manager(self.custom_nodes)
        self.detect_mock.return_value = str(lm)

        with (
            patch.object(
                type(get_trigger_cache()), "load", side_effect=OSError("unreadable")
            ),
            self.assertLogs("prompt_manager.api", level="WARNING"),
        ):
            resp = await self._post_json(
                "/prompt_manager/lora/enable",
                {"enabled": True, "path": str(lm), "trigger_words_enabled": True},
            )

        text = await resp.text()
        body = json.loads(text)
        self.assertEqual(resp.status, 200)
        self.assertIs(body["success"], True)
        self.assertEqual((body["persisted"], body["cache_loaded"]), (True, False))
        self.assertTrue(IntegrationConfig.LORA_MANAGER_ENABLED)
        self.assertIs(self._saved()["integrations"]["lora_manager"]["enabled"], True)
        self.assertNotIn("unreadable", text)


class TestConfigWritersShareOneStore(LoraAPITestCase):

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self._orig_timeout = PromptManagerConfig.RESULT_TIMEOUT

    async def asyncTearDown(self):
        PromptManagerConfig.RESULT_TIMEOUT = self._orig_timeout
        await super().asyncTearDown()

    async def test_settings_and_lora_saves_do_not_overwrite_each_other(self):
        # #220: the LoRA save is sent while the settings save is inside its
        # commit (lock held, file already read). The LoRA commit re-reads the
        # file under the same lock, so both edits survive and nothing else
        # is lost (before, the LoRA save rewrote the whole runtime config).
        self.config_file.write_text(json.dumps({"custom_section": {"keep": True}}))
        inside = threading.Event()
        release = threading.Event()
        real_mutation = settings_module._settings_mutation

        def gated_mutation(updates, new_roots):
            mutate = real_mutation(updates, new_roots)

            def gated(config):
                inside.set()
                release.wait(10)
                return mutate(config)

            return gated

        lora_calls = []
        real_lora_mutation = lora_module._lora_settings_mutation

        def recorded_lora_mutation(values):
            mutate = real_lora_mutation(values)

            def recorded(config):
                lora_calls.append(dict(config))
                return mutate(config)

            return recorded

        probe = ProbeLock()
        loop = asyncio.get_running_loop()
        with (
            patch.object(settings_module, "_settings_mutation", gated_mutation),
            patch.object(
                lora_module, "_lora_settings_mutation", recorded_lora_mutation
            ),
            patch.object(config_store, "_COMMIT_LOCK", probe),
        ):
            settings = asyncio.ensure_future(
                self._post_json("/prompt_manager/settings", {"result_timeout": 13})
            )
            self.assertTrue(await loop.run_in_executor(None, inside.wait, 10))
            lora = asyncio.ensure_future(
                self._post_json(
                    "/prompt_manager/lora/enable",
                    {"enabled": False, "trigger_words_enabled": True},
                )
            )
            # Wait until the LoRA commit is blocked on the lock the settings
            # commit holds; its mutation must not have run yet.
            self.assertTrue(await loop.run_in_executor(None, probe.contended.wait, 10))
            self.assertEqual(lora_calls, [])
            release.set()
            settings_resp, lora_resp = await asyncio.gather(settings, lora)
        self.assertEqual(len(lora_calls), 1)
        self.assertEqual(lora_calls[0]["web_ui"]["result_timeout"], 13)

        self.assertEqual((settings_resp.status, lora_resp.status), (200, 200))
        saved = self._saved()
        self.assertEqual(saved["custom_section"], {"keep": True})
        self.assertEqual(saved["web_ui"]["result_timeout"], 13)
        self.assertIs(
            saved["integrations"]["lora_manager"]["trigger_words_enabled"], True
        )
        settings_body = await settings_resp.json()
        lora_body = await lora_resp.json()
        self.assertNotEqual(settings_body["revision"], lora_body["revision"])

    def _delay_first_commit_result(self):
        """Hold back the first config commit's result from its handler.

        The commit itself has run (the file is written); only the handler's
        continuation waits until the returned event is set. Before #220's fix
        round the handler published after this point, so a later save could
        publish first and the earlier one overwrite it at runtime.
        """
        original = self.api._run_in_executor
        written = asyncio.Event()
        resume = asyncio.Event()
        delayed = []

        async def run(func, *args, **kwargs):
            result = await original(func, *args, **kwargs)
            if getattr(func, "__func__", None) is config_store.ConfigStore.commit:
                if not delayed:
                    delayed.append(func)
                    written.set()
                    await resume.wait()
            return result

        patcher = patch.object(self.api, "_run_in_executor", run)
        patcher.start()
        self.addCleanup(patcher.stop)
        return written, resume

    async def test_concurrent_settings_saves_end_on_the_last_written(self):
        written, resume = self._delay_first_commit_result()
        first = asyncio.ensure_future(
            self._post_json("/prompt_manager/settings", {"result_timeout": 11})
        )
        await asyncio.wait_for(written.wait(), 10)
        second = await self._post_json(
            "/prompt_manager/settings", {"result_timeout": 22}
        )
        resume.set()
        first = await first
        self.assertEqual((first.status, second.status), (200, 200))
        self.assertEqual(self._saved()["web_ui"]["result_timeout"], 22)
        self.assertEqual(PromptManagerConfig.RESULT_TIMEOUT, 22)

    async def test_concurrent_lora_saves_end_on_the_last_written(self):
        written, resume = self._delay_first_commit_result()
        first = asyncio.ensure_future(
            self._post_json(
                "/prompt_manager/lora/enable",
                {"enabled": False, "trigger_words_enabled": True},
            )
        )
        await asyncio.wait_for(written.wait(), 10)
        second = await self._post_json(
            "/prompt_manager/lora/enable",
            {"enabled": False, "trigger_words_enabled": False},
        )
        resume.set()
        first = await first
        self.assertEqual((first.status, second.status), (200, 200))
        lora = self._saved()["integrations"]["lora_manager"]
        self.assertIs(lora["trigger_words_enabled"], False)
        self.assertIs(IntegrationConfig.LORA_TRIGGER_WORDS_ENABLED, False)


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
        with patch.object(self.api.db, "replace_category_prompts", side_effect=err):
            resp = await self.client.request("POST", "/prompt_manager/lora/scan")
            body = await resp.text()  # the stream ends inside the patch
        self.assertNotIn(str(self.tmp_root), body)
        self.assertIn("x.db", body)
        # The admin page's modal shows the failure as its status and closes.
        error = self._sse_events(body)[-1]
        self.assertEqual(
            (error["type"], error["status"], error["progress"]),
            ("error", error["message"], 0),
        )

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

        # Preparing one LoRA's record fails: it is skipped, the import goes on.
        with patch.object(
            lora_import, "build_record", side_effect=RuntimeError("bad record")
        ):
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
