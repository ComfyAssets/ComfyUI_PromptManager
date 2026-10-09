"""Route tests for the admin API (py/api/admin.py).

Uses aiohttp's test client against a PromptManagerAPI wired to a temporary
database and a stub ``folder_paths`` module, so the tests run without ComfyUI.
"""

import asyncio
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
sys.modules.setdefault("server", _mock_server)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import AioHTTPTestCase  # noqa: E402

from database.operations import PromptDatabase  # noqa: E402
from py.api import PromptManagerAPI  # noqa: E402
from py.config import GalleryConfig, PromptManagerConfig  # noqa: E402

CONFIG_PATH_ENV = "PROMPT_MANAGER_CONFIG_PATH"
EXTRA_ROOTS_ENV = "PROMPT_MANAGER_EXTRA_GALLERY_ROOTS"


class AdminAPITestCase(AioHTTPTestCase):
    """Test app with PromptManager routes, temp DB and config, fake ComfyUI tree."""

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
        await super().tearDownAsync()  # closes the aiohttp test client
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
        self.api.db.close_all()
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


def _write_png(path, prompt_text=None):
    """Write a tiny PNG, optionally carrying a ComfyUI-style 'prompt' chunk."""
    from PIL import Image, PngImagePlugin

    img = Image.new("RGB", (2, 2), (255, 0, 0))
    info = PngImagePlugin.PngInfo()
    if prompt_text is not None:
        graph = {
            "1": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt_text}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "bad"}},
            "3": {
                "class_type": "KSampler",
                "inputs": {"positive": ["1", 0], "negative": ["2", 0]},
            },
        }
        info.add_text("prompt", json.dumps(graph))
    img.save(str(path), pnginfo=info)


class TestScansRunOffTheEventLoop(AdminAPITestCase):
    """Blocking filesystem work must go through _run_in_executor."""

    def _spy_executor(self):
        calls = []
        original = self.api._run_in_executor

        async def spy(func, *args, **kwargs):
            calls.append(getattr(func, "__name__", repr(func)))
            return await original(func, *args, **kwargs)

        self.api._run_in_executor = spy
        return calls

    async def test_scan_duplicates_runs_in_executor(self):
        (self.output_dir / "a.png").write_bytes(b"dup")
        (self.output_dir / "b.png").write_bytes(b"dup")
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.output_dir)]
        calls = self._spy_executor()

        resp = await self.client.request("GET", "/prompt_manager/scan_duplicates")

        self.assertEqual(resp.status, 200)
        self.assertEqual((await resp.json())["duplicates"][0]["count"], 2)
        self.assertIn("_find_duplicate_images_sync", calls)

    async def test_delete_duplicates_runs_in_executor(self):
        (self.output_dir / "a.png").write_bytes(b"dup")
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.output_dir)]
        calls = self._spy_executor()

        resp = await self.client.request(
            "POST",
            "/prompt_manager/delete_duplicate_images",
            json={"image_paths": ["a.png"]},
        )

        self.assertEqual(resp.status, 200)
        self.assertEqual((await resp.json())["deleted_count"], 1)
        self.assertIn("_delete_duplicate_images_sync", calls)

    async def test_output_scan_runs_in_executor(self):
        _write_png(self.output_dir / "gen.png", prompt_text="a red square")
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.output_dir)]
        calls = self._spy_executor()

        resp = await self.client.request("POST", "/prompt_manager/scan")

        body = await resp.text()
        self.assertEqual(resp.status, 200)
        self.assertIn('"type": "complete"', body)
        self.assertIn('"added": 1', body)
        for name in (
            "_collect_output_media_sync",
            "_extract_batch_metadata_sync",
            "_ingest_scan_batch_sync",
        ):
            self.assertIn(name, calls)

    async def test_output_scan_links_existing_prompt_on_second_pass(self):
        _write_png(self.output_dir / "gen.png", prompt_text="a red square")
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.output_dir)]

        first = await (await self.client.request("POST", "/prompt_manager/scan")).text()
        second = await (
            await self.client.request("POST", "/prompt_manager/scan")
        ).text()

        self.assertIn('"added": 1', first)
        self.assertIn('"linked": 1', second)
        self.assertIn('"added": 0', second)
        prompts = self.api.db.get_recent_prompts(limit=10)["prompts"]
        self.assertEqual([p["text"] for p in prompts], ["a red square"])

    async def test_output_scan_without_directories_reports_error(self):
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.outside_dir)]
        sys.modules.pop("folder_paths", None)
        self.api._find_comfyui_output_dir = lambda: None

        body = await (await self.client.request("POST", "/prompt_manager/scan")).text()

        self.assertIn('"type": "error"', body)


class TestDuplicateScanSyncBody(AdminAPITestCase):
    """Behaviour of the synchronous duplicate scan against a real tree."""

    def _scan(self):
        return self.api._find_duplicate_images_sync(str(self.output_dir))

    def test_groups_identical_files_and_sorts_by_mtime(self):
        older = self.output_dir / "older.png"
        newer = self.output_dir / "sub" / "newer.PNG"
        newer.parent.mkdir()
        older.write_bytes(b"same")
        newer.write_bytes(b"same")
        os.utime(older, (1_000_000, 1_000_000))
        os.utime(newer, (2_000_000, 2_000_000))
        (self.output_dir / "unique.png").write_bytes(b"different")

        groups = self._scan()

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["count"], 2)
        self.assertEqual(
            [img["filename"] for img in groups[0]["images"]], ["older.png", "newer.PNG"]
        )
        self.assertEqual(
            groups[0]["images"][1]["path"], os.path.join("sub", "newer.PNG")
        )
        self.assertEqual(
            groups[0]["images"][1]["url"], "/prompt_manager/images/serve/sub/newer.PNG"
        )
        self.assertNotIn(self.tmpdir, json.dumps(groups))

    def test_thumbnails_dir_is_skipped_and_thumbnail_url_reported(self):
        (self.output_dir / "a.png").write_bytes(b"same")
        (self.output_dir / "b.png").write_bytes(b"same")
        thumbs = self.output_dir / "thumbnails"
        thumbs.mkdir()
        (thumbs / "a_thumb.png").write_bytes(b"same")
        (thumbs / "b_thumb.png").write_bytes(b"other")

        groups = self._scan()

        self.assertEqual(len(groups), 1)
        by_name = {img["filename"]: img for img in groups[0]["images"]}
        self.assertEqual(set(by_name), {"a.png", "b.png"})
        self.assertRegex(
            by_name["a.png"]["thumbnail_url"],
            r"^/prompt_manager/images/serve/thumbnails/a_thumb\.png\?v=\d+$",
        )
        self.assertRegex(
            by_name["b.png"]["thumbnail_url"],
            r"^/prompt_manager/images/serve/thumbnails/b_thumb\.png\?v=\d+$",
        )

    def test_videos_are_flagged_and_unreadable_entries_skipped(self):
        (self.output_dir / "clip.mp4").write_bytes(b"vid")
        (self.output_dir / "copy.mp4").write_bytes(b"vid")
        (self.output_dir / "not_a_file.png").mkdir()  # rglob matches, hashing fails

        groups = self._scan()

        self.assertEqual(len(groups), 1)
        self.assertTrue(all(img["is_video"] for img in groups[0]["images"]))
        self.assertTrue(
            all(img["media_type"] == "video" for img in groups[0]["images"])
        )

    def test_missing_output_dir_gives_empty_list(self):
        self.assertEqual(
            self.api._find_duplicate_images_sync(os.path.join(self.tmpdir, "nope")), []
        )


class TestDeleteDuplicatesSyncBody(AdminAPITestCase):

    def test_deletes_file_and_thumbnail_inside_output(self):
        target = self.output_dir / "sub" / "x.png"
        target.parent.mkdir()
        target.write_bytes(b"x")
        thumb = self.output_dir / "thumbnails" / "sub" / "x_thumb.png"
        thumb.parent.mkdir(parents=True)
        thumb.write_bytes(b"t")

        result = self.api._delete_duplicate_images_sync(
            [str(target)], str(self.output_dir)
        )

        self.assertEqual(result["deleted_count"], 1)
        self.assertEqual(result["failed_count"], 0)
        self.assertFalse(target.exists())
        self.assertFalse(thumb.exists())

    def test_refuses_paths_outside_output_and_reports_missing(self):
        secret = self.outside_dir / "secret.txt"
        secret.write_text("s")

        result = self.api._delete_duplicate_images_sync(
            [str(secret), "ghost.png"], str(self.output_dir)
        )

        self.assertEqual(result["deleted_count"], 0)
        self.assertEqual(result["failed_count"], 2)
        self.assertTrue(secret.exists())
        self.assertTrue(any("outside" in f for f in result["failed_files"]))
        self.assertTrue(any("not found" in f for f in result["failed_files"]))

    def test_without_output_dir_everything_fails(self):
        result = self.api._delete_duplicate_images_sync(["a.png"], None)
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(result["deleted_count"], 0)

    def test_refuses_non_media_files_inside_output(self):
        for name in ("notes.txt", "prompts.db", "script.py", "noext"):
            (self.output_dir / name).write_bytes(b"x")

        result = self.api._delete_duplicate_images_sync(
            ["notes.txt", "prompts.db", "script.py", "noext"], str(self.output_dir)
        )

        self.assertEqual(result["deleted_count"], 0)
        self.assertEqual(result["failed_count"], 4)
        for name in ("notes.txt", "prompts.db", "script.py", "noext"):
            self.assertTrue((self.output_dir / name).exists(), name)
        self.assertTrue(all("not a media file" in f for f in result["failed_files"]))

    def test_refuses_files_under_thumbnails(self):
        thumb = self.output_dir / "thumbnails" / "sub" / "x_thumb.png"
        thumb.parent.mkdir(parents=True)
        thumb.write_bytes(b"t")

        result = self.api._delete_duplicate_images_sync(
            [os.path.join("thumbnails", "sub", "x_thumb.png")], str(self.output_dir)
        )

        self.assertEqual(result["deleted_count"], 0)
        self.assertTrue(thumb.exists())
        self.assertIn("thumbnail", result["failed_files"][0])

    def test_every_scanned_media_extension_is_deletable(self):
        from py.api.admin import (
            DELETABLE_MEDIA_EXTENSIONS,
            IMAGE_EXTENSIONS,
            VIDEO_EXTENSIONS,
        )

        for ext in IMAGE_EXTENSIONS + VIDEO_EXTENSIONS + (".tif",):
            self.assertIn(ext, DELETABLE_MEDIA_EXTENSIONS)
        for ext in (".txt", ".db", ".py", ".json", ".html", ""):
            self.assertNotIn(ext, DELETABLE_MEDIA_EXTENSIONS)
        (self.output_dir / "a.WEBM").write_bytes(b"v")
        result = self.api._delete_duplicate_images_sync(
            ["a.WEBM"], str(self.output_dir)
        )
        self.assertEqual(result["deleted_count"], 1)


class TestCollectMediaFiles(AdminAPITestCase):
    """_collect_media_files: bounded walk that never follows symlinked files."""

    def _collect(self):
        from py.api.admin import (
            IMAGE_EXTENSIONS,
            VIDEO_EXTENSIONS,
            _collect_media_files,
        )

        found = _collect_media_files(
            [self.output_dir], IMAGE_EXTENSIONS + VIDEO_EXTENSIONS
        )
        return sorted(p.relative_to(self.output_dir).as_posix() for p in found)

    def test_matches_case_insensitively_and_skips_thumbnails(self):
        (self.output_dir / "a.png").write_bytes(b"x")
        (self.output_dir / "b.PNG").write_bytes(b"x")
        (self.output_dir / "c.Mp4").write_bytes(b"x")
        (self.output_dir / "d.txt").write_bytes(b"x")
        (self.output_dir / "sub").mkdir()
        (self.output_dir / "sub" / "e.jpg").write_bytes(b"x")
        (self.output_dir / "thumbnails").mkdir()
        (self.output_dir / "thumbnails" / "a_thumb.png").write_bytes(b"x")
        (self.output_dir / "sub" / "thumbnails").mkdir()
        (self.output_dir / "sub" / "thumbnails" / "e_thumb.jpg").write_bytes(b"x")
        self.assertEqual(self._collect(), ["a.png", "b.PNG", "c.Mp4", "sub/e.jpg"])

    def test_symlinked_files_and_directories_are_skipped(self):
        secret = Path(self.tmpdir) / "secret.png"
        secret.write_bytes(b"s")
        secret_dir = Path(self.tmpdir) / "secret_dir"
        secret_dir.mkdir()
        (secret_dir / "inner.png").write_bytes(b"s")
        (self.output_dir / "real.png").write_bytes(b"r")
        try:
            os.symlink(secret, self.output_dir / "link.png")
            os.symlink(secret_dir, self.output_dir / "linkdir")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not available")
        self.assertEqual(self._collect(), ["real.png"])

    def test_depth_is_capped(self):
        from py.api.admin import MAX_SCAN_DEPTH

        self.assertEqual(MAX_SCAN_DEPTH, 12)
        deep = self.output_dir
        for level in range(1, MAX_SCAN_DEPTH + 2):
            deep = deep / f"d{level}"
            deep.mkdir()
            (deep / f"f{level}.png").write_bytes(b"x")
        found = self._collect()
        self.assertIn(
            "/".join(f"d{i}" for i in range(1, MAX_SCAN_DEPTH + 1))
            + f"/f{MAX_SCAN_DEPTH}.png",
            found,
        )
        self.assertFalse(
            any(name.endswith(f"f{MAX_SCAN_DEPTH + 1}.png") for name in found)
        )
        self.assertEqual(len(found), MAX_SCAN_DEPTH)

    def test_file_count_is_capped(self):
        import py.api.admin as admin_module
        from unittest.mock import patch

        self.assertEqual(admin_module.MAX_SCAN_FILES, 50_000)
        for i in range(6):
            (self.output_dir / f"{i}.png").write_bytes(b"x")
        with patch.object(admin_module, "MAX_SCAN_FILES", 4):
            self.assertEqual(len(self._collect()), 4)

    def test_missing_root_is_skipped(self):
        from py.api.admin import _collect_media_files

        self.assertEqual(
            _collect_media_files([Path(self.tmpdir) / "nope"], (".png",)), []
        )


class TestSettingsReadMonitorThroughModule(AdminAPITestCase):
    """get_settings reaches the image monitor through utils.image_monitor."""

    def test_no_sys_modules_scan(self):
        import inspect

        from py.api.admin import AdminRoutesMixin

        source = inspect.getsource(AdminRoutesMixin.get_settings)
        self.assertNotIn("sys.modules", source)

    async def test_reports_directories_of_running_monitor(self):
        import utils.image_monitor as im_mod
        from utils.image_monitor import get_image_monitor

        orig = im_mod._monitor_instance
        im_mod._monitor_instance = None
        self.addCleanup(setattr, im_mod, "_monitor_instance", orig)
        monitor = get_image_monitor(MagicMock(), MagicMock())
        monitor.monitored_directories = [str(self.output_dir)]

        resp = await self.client.request("GET", "/prompt_manager/settings")

        data = await resp.json()
        self.assertEqual(resp.status, 200)
        self.assertEqual(data["settings"]["monitored_directories"], ["output"])

    async def test_falls_back_to_configured_roots_without_monitor(self):
        import utils.image_monitor as im_mod

        orig = im_mod._monitor_instance
        im_mod._monitor_instance = None
        self.addCleanup(setattr, im_mod, "_monitor_instance", orig)
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.output_dir)]

        resp = await self.client.request("GET", "/prompt_manager/settings")

        data = await resp.json()
        self.assertEqual(data["settings"]["monitored_directories"], ["output"])
        self.assertEqual(data["settings"]["gallery_root_paths"], ["output"])


def _raise(*args, **kwargs):
    raise RuntimeError("boom")


class TestStatsCleanupAndErrors(AdminAPITestCase):

    async def test_stats_success(self):
        resp = await self.client.request("GET", "/prompt_manager/stats")
        data = await resp.json()
        self.assertEqual(resp.status, 200)
        self.assertIn("total_prompts", data["stats"])

    async def test_stats_db_failure_is_500(self):
        self.api.db.get_statistics = _raise
        resp = await self.client.request("GET", "/prompt_manager/stats")
        self.assertEqual(resp.status, 500)
        self.assertFalse((await resp.json())["success"])

    async def test_cleanup_success(self):
        resp = await self.client.request("POST", "/prompt_manager/cleanup")
        data = await resp.json()
        self.assertEqual(resp.status, 200)
        self.assertEqual(data["duplicates_removed"], 0)

    async def test_cleanup_failure_is_500(self):
        self.api.db.cleanup_duplicates = _raise
        resp = await self.client.request("POST", "/prompt_manager/cleanup")
        self.assertEqual(resp.status, 500)

    async def test_scan_duplicates_failure_is_500(self):
        self.api._find_comfyui_output_dir = _raise
        resp = await self.client.request("GET", "/prompt_manager/scan_duplicates")
        body = await resp.text()
        self.assertEqual(resp.status, 500)
        self.assertNotIn("boom", body)

    async def test_scan_duplicates_without_output_dir_is_empty(self):
        self.api._find_comfyui_output_dir = lambda: None
        resp = await self.client.request("GET", "/prompt_manager/scan_duplicates")
        data = await resp.json()
        self.assertEqual(resp.status, 200)
        self.assertEqual(data["duplicates"], [])


class TestDeleteDuplicatesEndpoint(AdminAPITestCase):

    async def _post(self, payload):
        return await self.client.request(
            "POST", "/prompt_manager/delete_duplicate_images", json=payload
        )

    async def test_empty_list_is_400(self):
        resp = await self._post({"image_paths": []})
        self.assertEqual(resp.status, 400)

    async def test_non_list_is_400(self):
        resp = await self._post({"image_paths": "a.png"})
        self.assertEqual(resp.status, 400)

    async def test_malformed_json_is_400(self):
        resp = await self.client.request(
            "POST",
            "/prompt_manager/delete_duplicate_images",
            data=b"{",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.status, 400)

    async def test_failures_are_reported(self):
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.output_dir)]
        resp = await self._post({"image_paths": ["ghost.png"]})
        data = await resp.json()
        self.assertEqual(resp.status, 200)
        self.assertEqual(data["failed_count"], 1)
        self.assertIn("1 failed", data["message"])
        self.assertEqual(len(data["failed_files"]), 1)

    async def test_unexpected_error_is_500(self):
        self.api._find_comfyui_output_dir = _raise
        resp = await self._post({"image_paths": ["a.png"]})
        self.assertEqual(resp.status, 500)

    def test_thumbnail_removal_failure_does_not_fail_delete(self):
        target = self.output_dir / "x.png"
        target.write_bytes(b"x")
        thumb_dir = self.output_dir / "thumbnails" / "x_thumb.png"
        thumb_dir.mkdir(parents=True)
        (thumb_dir / "child").write_bytes(b"c")  # os.remove on a dir raises OSError

        result = self.api._delete_duplicate_images_sync(
            [str(target)], str(self.output_dir)
        )

        self.assertEqual(result["deleted_count"], 1)
        self.assertFalse(target.exists())
        self.assertTrue(thumb_dir.exists())

    def test_unexpected_error_per_file_is_reported_without_path(self):
        self.api._delete_one_duplicate = lambda *a: (_ for _ in ()).throw(
            PermissionError(13, "Permission denied", os.path.join(self.tmpdir, "x.png"))
        )
        result = self.api._delete_duplicate_images_sync(["x.png"], str(self.output_dir))
        self.assertEqual(result["failed_count"], 1)
        self.assertIn("Permission denied", result["failed_files"][0])
        self.assertNotIn(self.tmpdir, result["failed_files"][0])


class TestJsonBodyCap(AdminAPITestCase):
    """Every JSON POST in the admin API rejects bodies over the shared cap."""

    JSON_POSTS = (
        "/prompt_manager/settings",
        "/prompt_manager/delete_duplicate_images",
        "/prompt_manager/diagnostics/test-link",
        "/prompt_manager/maintenance",
    )

    async def test_oversized_bodies_are_413(self):
        body = json.dumps({"pad": "x" * 1_000_100}).encode()
        for path in self.JSON_POSTS:
            resp = await self.client.request(
                "POST", path, data=body, headers={"Content-Type": "application/json"}
            )
            self.assertEqual(resp.status, 413, path)
            self.assertFalse((await resp.json())["success"], path)


class TestDuplicateJobsAreSingleFlight(AdminAPITestCase):
    """A duplicate scan or delete refuses to overlap with a running one."""

    def _block_scan(self):
        import asyncio

        gate = asyncio.Event()

        async def slow_scan():
            await gate.wait()
            return []

        self.api.find_duplicate_images = slow_scan
        return gate

    async def test_second_scan_while_one_runs_is_409(self):
        import asyncio

        gate = self._block_scan()
        first = asyncio.ensure_future(
            self.client.request("GET", "/prompt_manager/scan_duplicates")
        )
        await asyncio.sleep(0.05)

        second = await asyncio.wait_for(
            self.client.request("GET", "/prompt_manager/scan_duplicates"), 5
        )
        self.assertEqual(second.status, 409)
        data = await second.json()
        self.assertFalse(data["success"])
        self.assertIn("already running", data["error"])

        gate.set()
        self.assertEqual((await first).status, 200)

    async def test_delete_while_scan_runs_is_409_and_scan_after_is_fine(self):
        import asyncio

        gate = self._block_scan()
        first = asyncio.ensure_future(
            self.client.request("GET", "/prompt_manager/scan_duplicates")
        )
        await asyncio.sleep(0.05)

        resp = await asyncio.wait_for(
            self.client.request(
                "POST",
                "/prompt_manager/delete_duplicate_images",
                json={"image_paths": ["a.png"]},
            ),
            5,
        )
        self.assertEqual(resp.status, 409)

        gate.set()
        await first
        resp = await self.client.request("GET", "/prompt_manager/scan_duplicates")
        self.assertEqual(resp.status, 200)

    async def test_lock_is_released_after_a_failed_scan(self):
        self.api._find_comfyui_output_dir = _raise
        resp = await self.client.request("GET", "/prompt_manager/scan_duplicates")
        self.assertEqual(resp.status, 500)
        self.api._find_comfyui_output_dir = lambda: None
        resp = await self.client.request("GET", "/prompt_manager/scan_duplicates")
        self.assertEqual(resp.status, 200)


class TestSettingsMisc(AdminAPITestCase):

    async def test_infinite_scroll_default_is_reported_saved_and_validated(self):
        data = await (
            await self.client.request("GET", "/prompt_manager/settings")
        ).json()
        self.assertIs(data["settings"]["infinite_scroll"], False)

        resp = await self.client.request(
            "POST", "/prompt_manager/settings", json={"infinite_scroll": True}
        )
        self.assertEqual(resp.status, 200)
        self.assertTrue(PromptManagerConfig.INFINITE_SCROLL)
        self.assertIs(self._read_config()["web_ui"]["infinite_scroll"], True)
        data = await (
            await self.client.request("GET", "/prompt_manager/settings")
        ).json()
        self.assertIs(data["settings"]["infinite_scroll"], True)

        resp = await self.client.request(
            "POST", "/prompt_manager/settings", json={"infinite_scroll": "on"}
        )
        self.assertEqual(resp.status, 400)
        self.assertTrue(PromptManagerConfig.INFINITE_SCROLL)

    async def test_result_timeout_and_display_mode_saved(self):
        resp = await self.client.request(
            "POST",
            "/prompt_manager/settings",
            json={"result_timeout": 9, "webui_display_mode": "popup"},
        )
        data = await resp.json()
        self.assertEqual(resp.status, 200)
        self.assertFalse(data["restart_required"])
        self.assertEqual(PromptManagerConfig.RESULT_TIMEOUT, 9)
        self.assertEqual(PromptManagerConfig.WEBUI_DISPLAY_MODE, "popup")
        saved = self._read_config()
        self.assertEqual(saved["web_ui"]["result_timeout"], 9)

    async def test_save_merges_into_existing_config_file(self):
        self._write_config(
            {
                "integrations": {"lora_manager": {"enabled": True, "path": "x"}},
                "database": {"default_path": "custom.db"},
                "web_ui": {"result_timeout": 1, "show_test_button": True},
            }
        )

        resp = await self.client.request(
            "POST",
            "/prompt_manager/settings",
            json={"result_timeout": 7, "gallery_root_paths": [str(self.output_dir)]},
        )

        self.assertEqual(resp.status, 200)
        saved = self._read_config()
        self.assertEqual(saved["integrations"]["lora_manager"]["enabled"], True)
        self.assertEqual(saved["integrations"]["lora_manager"]["path"], "x")
        self.assertEqual(saved["database"]["default_path"], "custom.db")
        self.assertEqual(saved["web_ui"]["result_timeout"], 7)
        self.assertEqual(saved["web_ui"]["show_test_button"], True)
        self.assertEqual(
            saved["gallery"]["monitoring"]["directories"], [str(self.output_dir)]
        )

    async def test_save_replaces_a_corrupt_config_file(self):
        with open(self.config_path, "w") as f:
            f.write("{not json")
        resp = await self.client.request(
            "POST", "/prompt_manager/settings", json={"result_timeout": 4}
        )
        self.assertEqual(resp.status, 200)
        self.assertEqual(self._read_config()["web_ui"]["result_timeout"], 4)

    async def test_saved_config_file_is_private_to_the_user(self):
        import stat

        resp = await self.client.request(
            "POST", "/prompt_manager/settings", json={"result_timeout": 4}
        )
        self.assertEqual(resp.status, 200)
        if os.name == "posix":
            mode = stat.S_IMODE(os.stat(self.config_path).st_mode)
            self.assertEqual(mode, 0o600)

    async def test_invalid_result_timeout_is_400(self):
        resp = await self.client.request(
            "POST", "/prompt_manager/settings", json={"result_timeout": "soon"}
        )
        self.assertEqual(resp.status, 400)

    async def test_malformed_json_is_400(self):
        resp = await self.client.request(
            "POST",
            "/prompt_manager/settings",
            data=b"{oops",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.status, 400)

    async def test_unchanged_roots_do_not_require_restart(self):
        GalleryConfig.MONITORING_DIRECTORIES = [
            os.path.normcase(os.path.realpath(self.output_dir))
        ]
        resp = await self.client.request(
            "POST",
            "/prompt_manager/settings",
            json={"gallery_root_paths": [str(self.output_dir)]},
        )
        data = await resp.json()
        self.assertEqual(resp.status, 200)
        self.assertFalse(data["restart_required"])

    async def test_unwritable_config_path_still_succeeds(self):
        os.environ[CONFIG_PATH_ENV] = os.path.join(self.tmpdir, "missing", "c.json")
        resp = await self.client.request(
            "POST", "/prompt_manager/settings", json={"result_timeout": 3}
        )
        self.assertEqual(resp.status, 200)

    async def test_save_unexpected_error_is_500(self):
        self.api._parse_gallery_roots = _raise
        resp = await self.client.request(
            "POST", "/prompt_manager/settings", json={"gallery_root_paths": []}
        )
        self.assertEqual(resp.status, 500)
        self.assertNotIn("boom", await resp.text())

    async def test_get_unexpected_error_is_500(self):
        self.api._monitored_directories = _raise
        resp = await self.client.request("GET", "/prompt_manager/settings")
        self.assertEqual(resp.status, 500)


class TestDiagnostics(AdminAPITestCase):

    def _install_monitor(self, monitor):
        import utils.image_monitor as im_mod

        orig = im_mod._monitor_instance
        im_mod._monitor_instance = monitor
        self.addCleanup(setattr, im_mod, "_monitor_instance", orig)

    async def _diag(self):
        resp = await self.client.request("GET", "/prompt_manager/diagnostics")
        return resp, (await resp.json())["diagnostics"]

    async def test_healthy_report(self):
        self.api.db.save_prompt(text="hello", prompt_hash="h1")
        self._install_monitor(FakeMonitor([str(self.output_dir)]))

        resp, diag = await self._diag()

        self.assertEqual(resp.status, 200)
        self.assertEqual(diag["database"]["status"], "ok")
        self.assertEqual(diag["database"]["prompt_count"], 1)
        self.assertTrue(diag["database"]["has_images_table"])
        self.assertEqual(diag["dependencies"]["status"], "ok")
        self.assertEqual(diag["comfyui_output"]["status"], "ok")
        self.assertEqual(diag["image_monitor"]["status"], "ok")

    async def test_corrupt_database_reported(self):
        bad = os.path.join(self.tmpdir, "bad.db")
        with open(bad, "wb") as f:
            f.write(b"not a database at all, definitely not sqlite")
        self.api.db.model.db_path = bad

        _, diag = await self._diag()

        self.assertEqual(diag["database"]["status"], "error")
        self.assertIn("Database error", diag["database"]["message"])

    async def test_monitor_not_running_and_no_output_dirs(self):
        self._install_monitor(None)
        sys.modules.pop("folder_paths", None)
        orig_cwd = os.getcwd()
        os.chdir(self.outside_dir)
        self.addCleanup(os.chdir, orig_cwd)

        _, diag = await self._diag()

        self.assertEqual(diag["image_monitor"]["status"], "error")
        self.assertIn("not initialized", diag["image_monitor"]["message"])
        self.assertEqual(diag["comfyui_output"]["status"], "warning")
        self.assertEqual(diag["comfyui_output"]["output_dirs"], [])

    async def test_relative_output_fallback_without_folder_paths(self):
        sys.modules.pop("folder_paths", None)
        orig_cwd = os.getcwd()
        os.chdir(self.comfy_dir)  # has an "output" child
        self.addCleanup(os.chdir, orig_cwd)

        _, diag = await self._diag()

        self.assertEqual(diag["comfyui_output"]["status"], "ok")
        self.assertEqual(diag["comfyui_output"]["output_dirs"], ["output"])

    async def test_monitor_status_failure_reported(self):
        broken = MagicMock()
        broken.get_status = _raise
        self._install_monitor(broken)

        _, diag = await self._diag()

        self.assertEqual(diag["image_monitor"]["status"], "error")
        self.assertIn("Failed to get monitor status", diag["image_monitor"]["message"])

    async def test_unexpected_error_is_500(self):
        import py.api.admin as admin_module

        orig = admin_module._diagnose_database
        admin_module._diagnose_database = _raise
        self.addCleanup(setattr, admin_module, "_diagnose_database", orig)
        resp = await self.client.request("GET", "/prompt_manager/diagnostics")
        self.assertEqual(resp.status, 500)


class TestTestImageLink(AdminAPITestCase):

    async def _post(self, payload):
        return await self.client.request(
            "POST", "/prompt_manager/diagnostics/test-link", json=payload
        )

    async def test_missing_prompt_id_is_400(self):
        resp = await self._post({})
        self.assertEqual(resp.status, 400)

    async def test_links_test_image(self):
        pid = self.api.db.save_prompt(text="hello", prompt_hash="h1")
        resp = await self._post({"prompt_id": pid})
        data = await resp.json()
        self.assertEqual(resp.status, 200)
        self.assertEqual(data["result"]["status"], "ok")
        self.assertIsInstance(data["result"]["image_id"], int)

    async def test_client_supplied_path_is_not_stored(self):
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.output_dir)]
        pid = self.api.db.save_prompt(text="hello", prompt_hash="h1")
        evil = os.path.join(self.tmpdir, "etc", "passwd")

        resp = await self._post({"prompt_id": pid, "image_path": evil})

        data = await resp.json()
        self.assertEqual(resp.status, 200, data)
        images = self.api.db.get_prompt_images(pid)
        self.assertEqual(len(images), 1)
        stored = Path(images[0]["image_path"])
        self.assertNotEqual(os.path.normcase(str(stored)), os.path.normcase(evil))
        self.assertTrue(
            stored.resolve().is_relative_to(self.output_dir.resolve()),
            stored,
        )
        self.assertIn("test", stored.name.lower())
        self.assertNotIn(self.tmpdir, json.dumps(data))

    async def test_without_output_dir_a_relative_marker_is_used(self):
        self.api._find_comfyui_output_dir = lambda: None
        pid = self.api.db.save_prompt(text="hello", prompt_hash="h1")
        resp = await self._post({"prompt_id": pid, "image_path": "/x/y.png"})
        self.assertEqual(resp.status, 200)
        images = self.api.db.get_prompt_images(pid)
        self.assertFalse(os.path.isabs(images[0]["image_path"]))

    async def test_db_failure_is_reported_in_result(self):
        self.api.db.link_image_to_prompt = _raise
        resp = await self._post({"prompt_id": 1})
        data = await resp.json()
        self.assertEqual(resp.status, 200)
        self.assertFalse(data["success"])
        self.assertEqual(data["result"]["status"], "error")

    async def test_malformed_json_is_400(self):
        resp = await self.client.request(
            "POST",
            "/prompt_manager/diagnostics/test-link",
            data=b"nope",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.status, 400)


class TestMaintenance(AdminAPITestCase):

    ALL_OPS = [
        "cleanup_duplicates",
        "vacuum",
        "cleanup_orphaned_images",
        "check_hash_duplicates",
        "statistics",
        "prune_orphaned_prompts",
        "check_consistency",
    ]

    async def _post(self, payload=None, **kwargs):
        if payload is None:
            return await self.client.request(
                "POST", "/prompt_manager/maintenance", **kwargs
            )
        return await self.client.request(
            "POST", "/prompt_manager/maintenance", json=payload, **kwargs
        )

    async def test_default_operations_without_body(self):
        resp = await self._post(data=b"", headers={"Content-Type": "text/plain"})
        data = await resp.json()
        self.assertEqual(resp.status, 200)
        self.assertEqual(data["operations_completed"], 3)
        self.assertTrue(data["all_successful"])
        self.assertEqual(
            set(data["results"]),
            {"cleanup_duplicates", "vacuum", "cleanup_orphaned_images"},
        )

    async def test_all_operations(self):
        self.api.db.save_prompt(text="orphan", prompt_hash="h1")
        resp = await self._post({"operations": self.ALL_OPS})
        data = await resp.json()
        self.assertEqual(resp.status, 200)
        self.assertEqual(data["operations_completed"], len(self.ALL_OPS))
        self.assertTrue(data["all_successful"], data["results"])
        self.assertIn("info", data["results"]["statistics"])
        self.assertEqual(data["results"]["check_consistency"]["issues_found"], 0)

    async def test_statistics_database_path_is_public(self):
        resp = await self._post({"operations": ["statistics"]})
        body = await resp.text()
        self.assertEqual(resp.status, 200)
        self.assertNotIn(self.tmpdir, body)
        info = json.loads(body)["results"]["statistics"]["info"]
        self.assertEqual(info["database_path"], "prompts.db")

    async def test_unknown_operation_does_nothing(self):
        resp = await self._post({"operations": ["reboot"]})
        data = await resp.json()
        self.assertEqual(data["operations_completed"], 0)
        self.assertTrue(data["all_successful"])

    async def test_each_operation_reports_its_own_failure(self):
        self.api.db.cleanup_duplicates = _raise
        self.api.db.model.vacuum_database = _raise
        self.api.db.cleanup_missing_images = _raise
        self.api.db.check_hash_duplicates = _raise
        self.api.db.model.get_database_info = _raise
        self.api.db.prune_orphaned_prompts = _raise
        self.api.db.check_consistency = _raise

        resp = await self._post({"operations": self.ALL_OPS})
        data = await resp.json()

        self.assertEqual(resp.status, 200)
        self.assertFalse(data["all_successful"])
        self.assertEqual(len(data["results"]), len(self.ALL_OPS))
        for name in self.ALL_OPS:
            self.assertFalse(data["results"][name]["success"], name)
            self.assertEqual(data["results"][name]["error"], "boom")

    async def test_unexpected_error_is_500(self):
        self.api._run_in_executor = _raise
        resp = await self._post({"operations": ["vacuum"]})
        self.assertEqual(resp.status, 500)


class TestBackupRestoreContract(AdminAPITestCase):
    """Contract tests only: status codes and envelope of backup/restore."""

    async def test_backup_downloads_sqlite_file(self):
        self.api.db.save_prompt(text="keep me", prompt_hash="h1")
        resp = await self.client.request("GET", "/prompt_manager/backup")
        body = await resp.read()
        self.assertEqual(resp.status, 200)
        self.assertIn("attachment", resp.headers["Content-Disposition"])
        self.assertTrue(body.startswith(b"SQLite format 3"))

    async def test_backup_without_db_file_is_404(self):
        self.api.db.model.db_path = os.path.join(self.tmpdir, "nope.db")
        resp = await self.client.request("GET", "/prompt_manager/backup")
        self.assertEqual(resp.status, 404)

    async def _restore(self, field_name, content):
        from aiohttp import FormData

        form = FormData()
        form.add_field(field_name, content, filename="upload.db")
        return await self.client.request("POST", "/prompt_manager/restore", data=form)

    async def test_restore_wrong_field_is_400(self):
        resp = await self._restore("other", b"x")
        self.assertEqual(resp.status, 400)

    async def test_restore_empty_file_is_400(self):
        resp = await self._restore("database_file", b"")
        self.assertEqual(resp.status, 400)

    async def test_restore_non_sqlite_is_400_and_db_untouched(self):
        self.api.db.save_prompt(text="keep me", prompt_hash="h1")
        resp = await self._restore("database_file", b"definitely not sqlite data")
        self.assertEqual(resp.status, 400)
        self.assertIsNotNone(self.api.db.get_prompt_by_hash("h1"))

    def _snapshot_upload(self, extra_sql=()):
        """Bytes of a self-contained copy of the live DB (WAL rows included)."""
        import sqlite3
        from contextlib import closing

        snapshot = os.path.join(self.tmpdir, "snapshot.db")
        with closing(sqlite3.connect(self.api.db.model.db_path)) as src:
            with closing(sqlite3.connect(snapshot)) as dst:
                src.backup(dst)
                for statement in extra_sql:
                    dst.execute(statement)
                dst.commit()
        with open(snapshot, "rb") as f:
            return f.read()

    async def test_restore_valid_db_succeeds(self):
        import sqlite3
        from contextlib import closing

        self.api.db.save_prompt(text="restored", prompt_hash="h1")
        upload = self._snapshot_upload()
        resp = await self._restore("database_file", upload)
        data = await resp.json()
        self.assertEqual(resp.status, 200, data)
        self.assertTrue(data["success"])
        self.assertEqual(data["prompt_count"], 1)
        with closing(sqlite3.connect(self.api.db.model.db_path)) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM prompts").fetchone()[0], 1
            )

    async def test_restore_backup_created_is_a_public_path(self):
        self.api.db.save_prompt(text="restored", prompt_hash="h1")
        resp = await self._restore("database_file", self._snapshot_upload())
        body = await resp.text()
        self.assertEqual(resp.status, 200, body)
        self.assertNotIn(self.tmpdir, body)
        data = json.loads(body)
        self.assertTrue(data["backup_created"].startswith("prompts.db.backup_"))

    async def test_restore_rejects_database_with_a_trigger(self):
        self.api.db.save_prompt(text="keep me", prompt_hash="h1")
        upload = self._snapshot_upload(
            [
                "CREATE TRIGGER evil AFTER INSERT ON prompts "
                "BEGIN DELETE FROM prompts; END"
            ]
        )
        resp = await self._restore("database_file", upload)
        data = await resp.json()
        self.assertEqual(resp.status, 400, data)
        self.assertIn("trigger", data["error"].lower())
        self.assertIsNotNone(self.api.db.get_prompt_by_hash("h1"))

    async def test_restore_rejects_database_with_a_view(self):
        self.api.db.save_prompt(text="keep me", prompt_hash="h1")
        upload = self._snapshot_upload(["CREATE VIEW peek AS SELECT text FROM prompts"])
        resp = await self._restore("database_file", upload)
        data = await resp.json()
        self.assertEqual(resp.status, 400, data)
        self.assertIn("view", data["error"].lower())

    async def test_restore_verifies_in_the_executor(self):
        self.api.db.save_prompt(text="restored", prompt_hash="h1")
        upload = self._snapshot_upload()
        calls = []
        original = self.api._run_in_executor

        async def spy(func, *args, **kwargs):
            calls.append(getattr(func, "__name__", repr(func)))
            return await original(func, *args, **kwargs)

        self.api._run_in_executor = spy
        resp = await self._restore("database_file", upload)
        self.assertEqual(resp.status, 200)
        self.assertIn("verify_database_file", calls)
        self.assertIn("restore_from_file", calls)

    async def test_restore_keeps_at_most_ten_safety_backups(self):
        import time

        from py.api.admin import MAX_SAFETY_BACKUPS

        self.assertEqual(MAX_SAFETY_BACKUPS, 10)
        db_path = self.api.db.model.db_path
        stale = []
        now = time.time()
        for i in range(12):
            path = f"{db_path}.backup_202001{i + 1:02d}_000000"
            with open(path, "wb") as f:
                f.write(b"old")
            os.utime(path, (now - 10_000 + i, now - 10_000 + i))
            stale.append(path)
        unrelated = os.path.join(self.tmpdir, "other.db.backup_20200101_000000")
        with open(unrelated, "wb") as f:
            f.write(b"x")
        self.api.db.save_prompt(text="restored", prompt_hash="h1")

        resp = await self._restore("database_file", self._snapshot_upload())

        self.assertEqual(resp.status, 200)
        remaining = sorted(
            name
            for name in os.listdir(self.tmpdir)
            if name.startswith("prompts.db.backup_")
        )
        self.assertEqual(len(remaining), MAX_SAFETY_BACKUPS)
        data = await resp.json()
        self.assertIn(data["backup_created"], remaining)
        for path in stale[:3]:
            self.assertFalse(os.path.exists(path), path)
        self.assertTrue(os.path.exists(unrelated))

    async def test_restore_failure_body_has_no_absolute_path(self):
        self.api.db.save_prompt(text="restored", prompt_hash="h1")
        upload = self._snapshot_upload()

        def boom(src_path):
            raise PermissionError(
                13, "Permission denied", os.path.join(self.tmpdir, "prompts.db")
            )

        self.api.db.model.restore_from_file = boom
        resp = await self._restore("database_file", upload)
        body = await resp.text()
        self.assertEqual(resp.status, 500)
        self.assertNotIn(self.tmpdir, body)
        self.assertIn("Permission denied", json.loads(body)["error"])

    async def test_backup_failure_body_has_no_absolute_path(self):
        def boom(path):
            raise PermissionError(
                13, "Permission denied", os.path.join(self.tmpdir, "x.db")
            )

        self.api.db.model.backup_database = boom
        resp = await self.client.request("GET", "/prompt_manager/backup")
        body = await resp.text()
        self.assertEqual(resp.status, 500)
        self.assertNotIn(self.tmpdir, body)
        self.assertIn("Permission denied", json.loads(body)["error"])


class TestOutputScanInternals(AdminAPITestCase):

    def test_metadata_extraction_failure_yields_empty(self):
        self.api._extract_comfyui_metadata = _raise
        results = self.api._extract_batch_metadata_sync([Path("a.png")])
        self.assertEqual(results, [(Path("a.png"), {})])

    def test_ingest_outcomes(self):
        f = Path("gen.png")
        meta = {
            "prompt": json.dumps(
                {"1": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat"}}}
            )
        }
        self.assertIsNone(self.api._ingest_scanned_file(f, {}))
        self.assertIsNone(self.api._ingest_scanned_file(f, {"prompt": "{}"}))
        self.assertEqual(
            self.api._ingest_scanned_file(f, {"parameters": "  \nNegative prompt: x"}),
            "found",
        )
        self.assertEqual(self.api._ingest_scanned_file(f, meta), "added")
        self.assertEqual(self.api._ingest_scanned_file(f, meta), "linked")

    def test_ingest_link_failures_are_tolerated(self):
        f = Path("gen.png")
        meta = {
            "prompt": json.dumps(
                {"1": {"class_type": "CLIPTextEncode", "inputs": {"text": "a dog"}}}
            )
        }
        self.api.db.link_image_to_prompt = _raise
        self.assertEqual(self.api._ingest_scanned_file(f, meta), "added")
        self.assertEqual(self.api._ingest_scanned_file(f, meta), "found")

    def test_ingest_save_failure_counts_as_found(self):
        f = Path("gen.png")
        meta = {
            "prompt": json.dumps(
                {"1": {"class_type": "CLIPTextEncode", "inputs": {"text": "a bird"}}}
            )
        }
        self.api.db.save_prompt = lambda *a, **k: None
        self.assertEqual(self.api._ingest_scanned_file(f, meta), "found")

    def test_batch_counts_and_per_file_errors(self):
        meta = {
            "prompt": json.dumps(
                {"1": {"class_type": "CLIPTextEncode", "inputs": {"text": "a fish"}}}
            )
        }
        self.api.db.get_prompt_by_hash = _raise
        counts = self.api._ingest_scan_batch_sync(
            [(Path("a.png"), meta), (Path("b.png"), {})]
        )
        self.assertEqual(counts, {"processed": 2, "found": 0, "added": 0, "linked": 0})

    async def test_scan_internal_error_streams_error_event(self):
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.output_dir)]
        self.api._collect_output_media_sync = _raise
        body = await (await self.client.request("POST", "/prompt_manager/scan")).text()
        self.assertIn('"type": "error"', body)
        self.assertNotIn("boom", body)


class TestOutputScanIsABackgroundJob(AdminAPITestCase):
    """The output scan keeps running when the browser goes away, and a second
    request attaches to the running scan instead of starting another one."""

    def _gate_collect(self):
        """Block the media collection step until the returned event is set."""
        import threading

        gate = threading.Event()
        calls = []
        original = self.api._collect_output_media_sync

        def gated(output_dirs):
            calls.append(output_dirs)
            gate.wait(5)
            return original(output_dirs)

        self.api._collect_output_media_sync = gated
        return gate, calls

    async def test_scan_survives_client_disconnect_and_finishes(self):
        from unittest.mock import AsyncMock, patch

        _write_png(self.output_dir / "gen.png", prompt_text="a red square")
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.output_dir)]

        with self.assertNoLogs("aiohttp.server", level="ERROR"):
            with patch.object(
                web.StreamResponse,
                "write",
                AsyncMock(side_effect=ConnectionResetError("closing transport")),
            ):
                resp = await self.client.request("POST", "/prompt_manager/scan")
                self.assertEqual(resp.status, 200)
                await resp.release()
            await asyncio.wait_for(self.api._scan_job.task, 10)

        prompts = self.api.db.get_recent_prompts(limit=10)["prompts"]
        self.assertEqual([p["text"] for p in prompts], ["a red square"])
        self.assertEqual(self.api._scan_job.last_event["type"], "complete")
        self.assertFalse(self.api._scan_job.running)

    async def test_second_request_attaches_to_the_running_scan(self):
        _write_png(self.output_dir / "gen.png", prompt_text="a red square")
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.output_dir)]
        gate, calls = self._gate_collect()

        first = asyncio.ensure_future(
            self.client.request("POST", "/prompt_manager/scan")
        )
        await asyncio.sleep(0.05)
        second = await asyncio.wait_for(
            self.client.request("POST", "/prompt_manager/scan"), 5
        )
        self.assertEqual(second.status, 200)
        self.assertIn("text/event-stream", second.headers["Content-Type"])

        gate.set()
        first_body = await (await first).text()
        second_body = await second.text()

        self.assertEqual(len(calls), 1)
        self.assertIn('"type": "complete"', first_body)
        self.assertIn('"type": "complete"', second_body)
        self.assertIn('"added": 1', second_body)

    async def test_status_reports_running_scan_and_its_last_event(self):
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.output_dir)]
        gate, _ = self._gate_collect()

        first = asyncio.ensure_future(
            self.client.request("POST", "/prompt_manager/scan")
        )
        await asyncio.sleep(0.05)
        resp = await self.client.request("GET", "/prompt_manager/scan/status")
        running = await resp.json()

        gate.set()
        await (await first).text()
        resp = await self.client.request("GET", "/prompt_manager/scan/status")
        finished = await resp.json()

        self.assertEqual(resp.status, 200)
        self.assertTrue(running["success"])
        self.assertTrue(running["running"])
        self.assertEqual(running["last_event"]["type"], "progress")
        self.assertFalse(finished["running"])
        self.assertEqual(finished["last_event"]["type"], "complete")

    async def test_status_before_any_scan_is_idle(self):
        resp = await self.client.request("GET", "/prompt_manager/scan/status")
        data = await resp.json()
        self.assertEqual(resp.status, 200)
        self.assertEqual(data, {"success": True, "running": False, "last_event": None})

    async def test_scan_after_a_finished_one_starts_fresh(self):
        _write_png(self.output_dir / "gen.png", prompt_text="a red square")
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.output_dir)]
        gate, calls = self._gate_collect()
        gate.set()

        await (await self.client.request("POST", "/prompt_manager/scan")).text()
        body = await (await self.client.request("POST", "/prompt_manager/scan")).text()

        self.assertEqual(len(calls), 2)
        self.assertIn('"linked": 1', body)

    async def test_failed_scan_is_not_left_running(self):
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.output_dir)]
        self.api._collect_output_media_sync = _raise

        await (await self.client.request("POST", "/prompt_manager/scan")).text()
        data = await (
            await self.client.request("GET", "/prompt_manager/scan/status")
        ).json()

        self.assertFalse(data["running"])
        self.assertEqual(data["last_event"]["type"], "error")


class TestWorkerThreadsSetting(AdminAPITestCase):
    """The worker-thread count is exposed, validated, persisted and used by the scan."""

    async def test_get_settings_reports_worker_threads_and_cpu_count(self):
        data = await (
            await self.client.request("GET", "/prompt_manager/settings")
        ).json()
        settings = data["settings"]
        self.assertEqual(
            settings["cpu_count"], PromptManagerConfig.max_worker_threads()
        )
        self.assertEqual(settings["worker_threads"], PromptManagerConfig.WORKER_THREADS)
        self.assertGreaterEqual(settings["worker_threads"], 1)

    async def test_save_persists_worker_threads_under_performance(self):
        self._write_config({"performance": {"max_search_results": 25}})

        resp = await self.client.request(
            "POST", "/prompt_manager/settings", json={"worker_threads": 1}
        )

        self.assertEqual(resp.status, 200)
        self.assertEqual(PromptManagerConfig.WORKER_THREADS, 1)
        saved = self._read_config()
        self.assertEqual(saved["performance"]["worker_threads"], 1)
        self.assertEqual(saved["performance"]["max_search_results"], 25)

    async def test_save_rejects_more_threads_than_cores(self):
        before = PromptManagerConfig.WORKER_THREADS
        too_many = PromptManagerConfig.max_worker_threads() + 1

        resp = await self.client.request(
            "POST", "/prompt_manager/settings", json={"worker_threads": too_many}
        )

        self.assertEqual(resp.status, 400)
        self.assertFalse((await resp.json())["success"])
        self.assertEqual(PromptManagerConfig.WORKER_THREADS, before)

    async def test_scan_reads_metadata_on_several_threads(self):
        import threading
        import time

        for i in range(8):
            _write_png(self.output_dir / f"gen{i}.png", prompt_text=f"prompt {i}")
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.output_dir)]
        PromptManagerConfig.WORKER_THREADS = 4
        seen = set()
        lock = threading.Lock()
        original = self.api._extract_comfyui_metadata

        def recording(path):
            with lock:
                seen.add(threading.current_thread().name)
            time.sleep(0.05)
            return original(path)

        self.api._extract_comfyui_metadata = recording

        body = await (await self.client.request("POST", "/prompt_manager/scan")).text()

        self.assertIn('"added": 8', body)
        self.assertGreaterEqual(len(seen), 2)

    async def test_scan_with_one_worker_still_completes(self):
        _write_png(self.output_dir / "gen.png", prompt_text="solo")
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.output_dir)]
        PromptManagerConfig.WORKER_THREADS = 1

        body = await (await self.client.request("POST", "/prompt_manager/scan")).text()

        self.assertIn('"added": 1', body)


def _write_mp4(path, comment):
    """Tiny black video with ``comment`` in its container tags (needs ffmpeg)."""
    import subprocess

    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "quiet",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=16x16:d=0.2",
            "-metadata",
            f"comment={comment}",
            "-y",
            str(path),
        ],
        check=True,
        timeout=60,
    )


class TestScanReadsVideos(AdminAPITestCase):
    """Videos saved by ComfyUI carry the graph in their comment tag."""

    def setUp(self):
        super().setUp()
        from utils import video_metadata

        video_metadata.ffprobe_path.cache_clear()
        self.addCleanup(video_metadata.ffprobe_path.cache_clear)

    @unittest.skipUnless(
        shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg not installed"
    )
    async def test_scan_adds_prompts_found_in_videos(self):
        graph = {"1": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat"}}}
        _write_mp4(
            self.output_dir / "clip.mp4", json.dumps({"prompt": json.dumps(graph)})
        )
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.output_dir)]

        with self.assertNoLogs(level="ERROR"):
            body = await (
                await self.client.request("POST", "/prompt_manager/scan")
            ).text()

        self.assertIn('"added": 1', body)
        prompts = self.api.db.get_recent_prompts(limit=10)["prompts"]
        self.assertEqual([p["text"] for p in prompts], ["a cat"])
        images = self.api.db.get_prompt_images(prompts[0]["id"])
        self.assertTrue(images[0]["image_path"].endswith("clip.mp4"))

    async def test_scan_without_ffprobe_skips_videos_quietly(self):
        from unittest.mock import patch
        from utils import video_metadata

        (self.output_dir / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypisom")
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.output_dir)]

        with patch.object(video_metadata.shutil, "which", return_value=None):
            with self.assertNoLogs("prompt_manager", level="WARNING"):
                body = await (
                    await self.client.request("POST", "/prompt_manager/scan")
                ).text()

        self.assertIn('"processed": 1', body)
        self.assertIn('"found": 0', body)

    async def test_unreadable_image_is_a_warning_not_an_error(self):
        (self.output_dir / "bad.png").write_bytes(b"\x89PNG not really")
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.output_dir)]

        with self.assertLogs(level="WARNING") as cm:
            body = await (
                await self.client.request("POST", "/prompt_manager/scan")
            ).text()

        self.assertIn('"type": "complete"', body)
        self.assertTrue(any("bad.png" in line for line in cm.output))
        self.assertEqual([line for line in cm.output if line.startswith("ERROR")], [])


def _sse_payloads(body):
    return [
        json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")
    ]


class TestDuplicateScanIsABackgroundJob(AdminAPITestCase):
    """The duplicate scan streams progress, survives the client, and is single-flight
    with the duplicate delete."""

    STREAM = "/prompt_manager/scan_duplicates/stream"
    STATUS = "/prompt_manager/scan_duplicates/status"

    def _two_dups_one_single(self):
        (self.output_dir / "a.png").write_bytes(b"same bytes")
        (self.output_dir / "b.png").write_bytes(b"same bytes")
        (self.output_dir / "c.png").write_bytes(b"other bytes")
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.output_dir)]

    def _gate_hashing(self):
        import threading

        gate = threading.Event()
        calls = []
        original = self.api._calculate_file_hash

        def gated(path):
            calls.append(path)
            gate.wait(5)
            return original(path)

        self.api._calculate_file_hash = gated
        return gate, calls

    async def test_stream_reports_progress_then_the_groups(self):
        self._two_dups_one_single()

        body = await (await self.client.request("POST", self.STREAM)).text()
        events = _sse_payloads(body)

        types = [e["type"] for e in events]
        self.assertIn("progress", types)
        self.assertEqual(types[-1], "complete")
        done = events[-1]
        self.assertEqual(done["processed"], 3)
        self.assertEqual(done["found"], 1)
        self.assertEqual(done["duplicates"][0]["count"], 2)
        self.assertEqual(
            sorted(i["filename"] for i in done["duplicates"][0]["images"]),
            ["a.png", "b.png"],
        )
        self.assertNotIn(self.tmpdir, body)

    async def test_second_request_attaches_and_delete_is_refused_meanwhile(self):
        self._two_dups_one_single()
        gate, calls = self._gate_hashing()

        first = asyncio.ensure_future(self.client.request("POST", self.STREAM))
        await asyncio.sleep(0.1)
        second = await asyncio.wait_for(self.client.request("POST", self.STREAM), 5)
        self.assertEqual(second.status, 200)
        delete = await self.client.request(
            "POST",
            "/prompt_manager/delete_duplicate_images",
            json={"image_paths": ["a.png"]},
        )
        self.assertEqual(delete.status, 409)
        running = await (await self.client.request("GET", self.STATUS)).json()
        self.assertTrue(running["running"])

        gate.set()
        first_body = await (await first).text()
        second_body = await second.text()
        finished = await (await self.client.request("GET", self.STATUS)).json()

        self.assertIn('"type": "complete"', first_body)
        self.assertIn('"type": "complete"', second_body)
        self.assertEqual(len(calls), 3)  # one scan, each file hashed once
        self.assertFalse(finished["running"])
        self.assertEqual(finished["last_event"]["found"], 1)

    async def test_status_is_idle_before_any_scan(self):
        data = await (await self.client.request("GET", self.STATUS)).json()
        self.assertEqual(data, {"success": True, "running": False, "last_event": None})

    async def test_failed_scan_streams_an_error_and_releases_the_lock(self):
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.output_dir)]
        self.api._find_comfyui_output_dir = _raise

        body = await (await self.client.request("POST", self.STREAM)).text()
        self.api._find_comfyui_output_dir = lambda: str(self.output_dir)
        legacy = await self.client.request("GET", "/prompt_manager/scan_duplicates")

        self.assertEqual(_sse_payloads(body)[-1]["type"], "error")
        self.assertNotIn("boom", body)
        self.assertEqual(legacy.status, 200)

    def test_hashing_runs_on_worker_threads_and_reports_progress(self):
        import threading
        import time

        for i in range(8):
            (self.output_dir / f"f{i}.png").write_bytes(f"bytes {i % 4}".encode())
        PromptManagerConfig.WORKER_THREADS = 4
        seen = set()
        lock = threading.Lock()
        original = self.api._calculate_file_hash

        def recording(path):
            with lock:
                seen.add(threading.current_thread().name)
            time.sleep(0.05)
            return original(path)

        self.api._calculate_file_hash = recording
        reports = []

        groups = self.api._find_duplicate_images_sync(
            str(self.output_dir),
            progress=lambda done, total: reports.append((done, total)),
        )

        self.assertEqual(len(groups), 4)
        self.assertGreaterEqual(len(seen), 2)
        self.assertEqual(reports[-1], (8, 8))
        self.assertEqual([t for _, t in reports], [8] * len(reports))
        self.assertEqual(sorted(d for d, _ in reports), [d for d, _ in reports])


if __name__ == "__main__":
    unittest.main()
