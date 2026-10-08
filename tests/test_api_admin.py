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


class FakeMonitor:
    def __init__(self, dirs):
        self.monitored_directories = list(dirs)

    def get_status(self):
        return {
            "running": True,
            "monitored_directories": self.monitored_directories,
            "handler_active": True,
            "observer_alive": True,
        }


class TestNoAbsolutePathsInResponses(AdminAPITestCase):
    """No admin response may reveal the server's absolute filesystem layout."""

    def _install_monitor(self, dirs):
        import utils.image_monitor as im_mod

        orig = im_mod._monitor_instance
        im_mod._monitor_instance = FakeMonitor(dirs)
        self.addCleanup(setattr, im_mod, "_monitor_instance", orig)

    async def _body(self, method, path, **kwargs):
        resp = await self.client.request(method, path, **kwargs)
        return resp, await resp.text()

    async def test_settings_show_roots_relative_to_comfyui(self):
        renders = self.output_dir / "renders"
        renders.mkdir()
        GalleryConfig.MONITORING_DIRECTORIES = [str(renders)]
        self._install_monitor([str(renders)])

        resp, body = await self._body("GET", "/prompt_manager/settings")

        self.assertEqual(resp.status, 200)
        self.assertNotIn(self.tmpdir, body)
        data = json.loads(body)
        self.assertEqual(data["settings"]["gallery_root_paths"], ["output/renders"])
        self.assertEqual(data["settings"]["gallery_root_path"], "output/renders")
        self.assertEqual(data["settings"]["monitored_directories"], ["output/renders"])

    async def test_settings_relative_root_round_trips(self):
        """What GET returns can be POSTed back unchanged from any CWD."""
        renders = self.output_dir / "renders"
        renders.mkdir()

        resp = await self.client.request(
            "POST",
            "/prompt_manager/settings",
            json={"gallery_root_paths": ["output/renders"]},
        )

        self.assertEqual(resp.status, 200)
        self.assertEqual(
            [os.path.normcase(p) for p in GalleryConfig.MONITORING_DIRECTORIES],
            [os.path.normcase(os.path.realpath(renders))],
        )

    async def test_diagnostics_paths_are_relative(self):
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.output_dir)]
        self._install_monitor([str(self.output_dir)])

        resp, body = await self._body("GET", "/prompt_manager/diagnostics")

        self.assertEqual(resp.status, 200)
        self.assertNotIn(self.tmpdir, body)
        data = json.loads(body)
        self.assertEqual(
            data["diagnostics"]["comfyui_output"]["output_dirs"], ["output"]
        )
        self.assertEqual(
            data["diagnostics"]["image_monitor"]["monitored_directories"], ["output"]
        )

    async def test_diagnostics_missing_db_message_has_no_path(self):
        self.api.db.model.db_path = os.path.join(self.tmpdir, "missing", "prompts.db")

        resp, body = await self._body("GET", "/prompt_manager/diagnostics")

        self.assertNotIn(self.tmpdir, body)
        data = json.loads(body)
        self.assertEqual(data["diagnostics"]["database"]["status"], "error")
        self.assertIn("prompts.db", data["diagnostics"]["database"]["message"])

    async def test_scan_duplicates_paths_are_relative_to_output(self):
        (self.output_dir / "a.png").write_bytes(b"same-bytes")
        (self.output_dir / "sub").mkdir()
        (self.output_dir / "sub" / "b.png").write_bytes(b"same-bytes")
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.output_dir)]

        resp, body = await self._body("GET", "/prompt_manager/scan_duplicates")

        self.assertEqual(resp.status, 200)
        self.assertNotIn(self.tmpdir, body)
        data = json.loads(body)
        self.assertEqual(len(data["duplicates"]), 1)
        paths = sorted(img["path"] for img in data["duplicates"][0]["images"])
        self.assertEqual(paths, ["a.png", os.path.join("sub", "b.png")])

    async def test_delete_duplicates_accepts_relative_paths(self):
        target = self.output_dir / "sub" / "b.png"
        target.parent.mkdir()
        target.write_bytes(b"bytes")
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.output_dir)]

        resp, body = await self._body(
            "POST",
            "/prompt_manager/delete_duplicate_images",
            json={"image_paths": [os.path.join("sub", "b.png")]},
        )

        self.assertEqual(resp.status, 200)
        data = json.loads(body)
        self.assertEqual(data["deleted_count"], 1)
        self.assertFalse(target.exists())

    async def test_scan_images_stream_has_no_absolute_paths(self):
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.output_dir)]

        resp, body = await self._body("POST", "/prompt_manager/scan")

        self.assertEqual(resp.status, 200)
        self.assertNotIn(self.tmpdir, body)
        self.assertIn('"directories": ["output"]', body)


if __name__ == "__main__":
    unittest.main()
