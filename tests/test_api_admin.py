"""Route tests for the admin API (py/api/admin.py).

Uses aiohttp's test client against a PromptManagerAPI wired to a temporary
database and a stub ``folder_paths`` module, so the tests run without ComfyUI.
"""

import json
import os
import shutil
import sys
import tempfile
import types
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
from py.config import GalleryConfig, PromptManagerConfig  # noqa: E402

CONFIG_PATH_ENV = "PROMPT_MANAGER_CONFIG_PATH"
EXTRA_ROOTS_ENV = "PROMPT_MANAGER_EXTRA_GALLERY_ROOTS"


class AdminAPITestCase(AioHTTPTestCase):
    """Test app with PromptManager routes, temp DB, temp config and fake ComfyUI tree."""

    async def get_application(self):
        self.tmpdir = tempfile.mkdtemp()
        self.comfy_dir = Path(self.tmpdir) / "ComfyUI"
        self.output_dir = self.comfy_dir / "output"
        self.output_dir.mkdir(parents=True)
        self.outside_dir = Path(self.tmpdir) / "outside"
        self.outside_dir.mkdir()

        self.config_path = os.path.join(self.tmpdir, "config.json")
        self._orig_env = {
            key: os.environ.pop(key, None) for key in (CONFIG_PATH_ENV, EXTRA_ROOTS_ENV)
        }
        os.environ[CONFIG_PATH_ENV] = self.config_path

        self._orig_folder_paths = sys.modules.get("folder_paths")
        sys.modules["folder_paths"] = types.SimpleNamespace(
            base_path=str(self.comfy_dir),
            get_output_directory=lambda: str(self.output_dir),
        )

        self._orig_gallery_dirs = list(GalleryConfig.MONITORING_DIRECTORIES)
        self._orig_pm_config = PromptManagerConfig.get_config()
        GalleryConfig.MONITORING_DIRECTORIES = []

        db_path = os.path.join(self.tmpdir, "prompts.db")
        app = web.Application()
        routes = web.RouteTableDef()
        self.api = PromptManagerAPI()
        self.api.db = PromptDatabase(db_path)
        self.api.add_routes(routes)
        app.router.add_routes(routes)
        return app

    async def tearDownAsync(self):
        GalleryConfig.MONITORING_DIRECTORIES = self._orig_gallery_dirs
        PromptManagerConfig.update_config(self._orig_pm_config)
        for key, value in self._orig_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if self._orig_folder_paths is None:
            sys.modules.pop("folder_paths", None)
        else:
            sys.modules["folder_paths"] = self._orig_folder_paths
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ── helpers ────────────────────────────────────────────────────────

    def _write_config(self, content):
        with open(self.config_path, "w") as f:
            json.dump(content, f)

    def _read_config(self):
        with open(self.config_path) as f:
            return json.load(f)


class TestSaveSettingsGalleryRoots(AdminAPITestCase):

    async def test_rejects_filesystem_root_and_keeps_config(self):
        self._write_config({"marker": "untouched"})

        resp = await self.client.request(
            "POST",
            "/prompt_manager/settings",
            json={"gallery_root_paths": [os.path.abspath(os.sep)]},
        )

        self.assertEqual(resp.status, 400)
        data = await resp.json()
        self.assertFalse(data["success"])
        self.assertEqual(self._read_config(), {"marker": "untouched"})
        self.assertEqual(GalleryConfig.MONITORING_DIRECTORIES, [])

    async def test_rejects_directory_outside_comfyui(self):
        self._write_config({"marker": "untouched"})

        resp = await self.client.request(
            "POST",
            "/prompt_manager/settings",
            json={"gallery_root_paths": [str(self.outside_dir)]},
        )

        self.assertEqual(resp.status, 400)
        self.assertEqual(self._read_config(), {"marker": "untouched"})
        self.assertEqual(GalleryConfig.MONITORING_DIRECTORIES, [])

    async def test_one_bad_root_rejects_whole_request(self):
        good = self.output_dir / "good"
        good.mkdir()
        self._write_config({"marker": "untouched"})

        resp = await self.client.request(
            "POST",
            "/prompt_manager/settings",
            json={"gallery_root_paths": [str(good), str(self.outside_dir)]},
        )

        self.assertEqual(resp.status, 400)
        self.assertEqual(self._read_config(), {"marker": "untouched"})
        self.assertEqual(GalleryConfig.MONITORING_DIRECTORIES, [])

    async def test_legacy_single_path_rejected_too(self):
        self._write_config({"marker": "untouched"})

        resp = await self.client.request(
            "POST",
            "/prompt_manager/settings",
            json={"gallery_root_path": os.path.expanduser("~")},
        )

        self.assertEqual(resp.status, 400)
        self.assertEqual(self._read_config(), {"marker": "untouched"})

    async def test_accepts_subdir_of_output_and_persists(self):
        good = self.output_dir / "renders"
        good.mkdir()

        resp = await self.client.request(
            "POST",
            "/prompt_manager/settings",
            json={"gallery_root_paths": [str(good)]},
        )

        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["success"])
        self.assertTrue(data["restart_required"])
        self.assertEqual(GalleryConfig.MONITORING_DIRECTORIES, [str(good)])
        saved = self._read_config()
        self.assertEqual(saved["gallery"]["monitoring"]["directories"], [str(good)])

    async def test_accepts_env_listed_directory(self):
        os.environ[EXTRA_ROOTS_ENV] = str(self.outside_dir)

        resp = await self.client.request(
            "POST",
            "/prompt_manager/settings",
            json={"gallery_root_paths": [str(self.outside_dir)]},
        )

        self.assertEqual(resp.status, 200)
        self.assertEqual(GalleryConfig.MONITORING_DIRECTORIES, [str(self.outside_dir)])

    async def test_legacy_single_path_accepted(self):
        resp = await self.client.request(
            "POST",
            "/prompt_manager/settings",
            json={"gallery_root_path": str(self.output_dir)},
        )

        self.assertEqual(resp.status, 200)
        self.assertEqual(GalleryConfig.MONITORING_DIRECTORIES, [str(self.output_dir)])

    async def test_empty_paths_clear_roots(self):
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.output_dir)]

        resp = await self.client.request(
            "POST",
            "/prompt_manager/settings",
            json={"gallery_root_paths": ["", "   "]},
        )

        self.assertEqual(resp.status, 200)
        self.assertEqual(GalleryConfig.MONITORING_DIRECTORIES, [])
        self.assertEqual(
            self._read_config()["gallery"]["monitoring"]["directories"], []
        )

    async def test_paths_must_be_a_list(self):
        resp = await self.client.request(
            "POST",
            "/prompt_manager/settings",
            json={"gallery_root_paths": str(self.output_dir)},
        )
        self.assertEqual(resp.status, 400)

    async def test_path_entries_must_be_strings(self):
        resp = await self.client.request(
            "POST",
            "/prompt_manager/settings",
            json={"gallery_root_paths": [42]},
        )
        self.assertEqual(resp.status, 400)


if __name__ == "__main__":
    unittest.main()
