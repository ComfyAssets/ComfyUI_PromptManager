"""Route tests for the admin API (py/api/admin.py).

Uses aiohttp's test client against a PromptManagerAPI wired to a temporary
database and a stub ``folder_paths`` module, so the tests run without ComfyUI.
"""

import asyncio
import hashlib
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_mock_server = MagicMock()
_mock_server.PromptServer.instance.routes = MagicMock()
sys.modules.setdefault("server", _mock_server)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database.models import PromptModel  # noqa: E402

try:
    from tests.open_handles import assert_closed  # noqa: E402
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import assert_closed  # noqa: E402

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
        # realpath: macOS hands out /var/... for a /private/var/... directory and
        # the gallery roots are stored resolved
        self.tmpdir = os.path.realpath(tempfile.mkdtemp())
        self.comfy_dir = Path(self.tmpdir) / "ComfyUI"
        self.output_dir = self.comfy_dir / "output"
        self.output_dir.mkdir(parents=True)
        self.outside_dir = Path(self.tmpdir) / "outside"
        self.outside_dir.mkdir()

        self.addAsyncCleanup(self._drain_and_remove)
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
        self.api = PromptManagerAPI(db=PromptDatabase(db_path))
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

    async def _drain_and_remove(self):
        """The last cleanup: no job outlives the temporary database.

        Registered first, so it runs after every cleanup a test added (which
        may release its own gated jobs). A job still running then, such as a
        scan a failed assertion never awaited, is cancelled and awaited
        before the database and the tree are removed.
        """
        api = getattr(self, "api", None)
        jobs = api._jobs.list() if api is not None else []
        tasks = [
            job.task for job in jobs if job.task is not None and not job.task.done()
        ]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if api is not None:
            # A scan of an ambiguous file captures it for Needs review.
            api.close_annotation_captures()
        PromptModel.close_all_instances()  # restore may have swapped api.db
        assert_closed(self, self.tmpdir)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ── helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _root(path):
        """A gallery root as the config stores it: resolved, case-folded on Windows."""
        return GalleryConfig.resolve_gallery_root(str(path))

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
            json={"gallery_root_paths": [self._root(self.outside_dir)]},
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
            json={"gallery_root_paths": [self._root(good)]},
        )

        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["success"])
        self.assertTrue(data["restart_required"])
        self.assertEqual(GalleryConfig.MONITORING_DIRECTORIES, [self._root(good)])
        saved = self._read_config()
        self.assertEqual(
            saved["gallery"]["monitoring"]["directories"], [self._root(good)]
        )

    async def test_accepts_env_listed_directory(self):
        os.environ[EXTRA_ROOTS_ENV] = str(self.outside_dir)

        resp = await self.client.request(
            "POST",
            "/prompt_manager/settings",
            json={"gallery_root_paths": [self._root(self.outside_dir)]},
        )

        self.assertEqual(resp.status, 200)
        self.assertEqual(
            GalleryConfig.MONITORING_DIRECTORIES, [self._root(self.outside_dir)]
        )

    async def test_legacy_single_path_accepted(self):
        resp = await self.client.request(
            "POST",
            "/prompt_manager/settings",
            json={"gallery_root_path": str(self.output_dir)},
        )

        self.assertEqual(resp.status, 200)
        self.assertEqual(
            GalleryConfig.MONITORING_DIRECTORIES, [self._root(self.output_dir)]
        )

    async def test_empty_paths_clear_roots(self):
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]

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

    @unittest.skipIf(sys.platform == "win32", "symlinks need privileges on Windows")
    async def test_same_output_dir_spelled_twice_is_listed_once(self):
        alias = self.comfy_dir / "alias"
        os.symlink(self.output_dir, alias, target_is_directory=True)
        # Two spellings of one directory (Windows adds case differences too)
        GalleryConfig.MONITORING_DIRECTORIES = [str(alias), self._root(self.output_dir)]
        self._install_monitor([self._root(self.output_dir)])

        resp, body = await self._body("GET", "/prompt_manager/diagnostics")

        self.assertEqual(resp.status, 200)
        data = json.loads(body)
        self.assertEqual(
            data["diagnostics"]["comfyui_output"]["output_dirs"], ["output"]
        )
        self.assertEqual(len(self.api._get_all_output_dirs()), 1)

    async def test_diagnostics_paths_are_relative(self):
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]
        self._install_monitor([self._root(self.output_dir)])

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
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]

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
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]

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
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]

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
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]
        calls = self._spy_executor()

        resp = await self.client.request("GET", "/prompt_manager/scan_duplicates")

        self.assertEqual(resp.status, 200)
        self.assertEqual((await resp.json())["duplicates"][0]["count"], 2)
        self.assertIn("_find_duplicate_images_sync", calls)

    async def test_delete_duplicates_runs_in_executor(self):
        (self.output_dir / "a.png").write_bytes(b"dup")
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]
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
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]
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
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]

        first = await (await self.client.request("POST", "/prompt_manager/scan")).text()
        second = await (
            await self.client.request("POST", "/prompt_manager/scan")
        ).text()

        self.assertIn('"added": 1', first)
        self.assertIn('"unchanged": 1', second)
        self.assertIn('"added": 0', second)
        prompts = self.api.db.get_recent_prompts(limit=10)["prompts"]
        self.assertEqual({p["text"] for p in prompts}, {"a red square", "bad"})
        pairs = self.api.db.list_prompt_pairs()["pairs"]
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["negative_text"], "bad")

    async def test_output_scan_without_directories_reports_error(self):
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.outside_dir)]
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

        from py.api.settings_routes import SettingsRoutesMixin

        source = inspect.getsource(SettingsRoutesMixin.get_settings)
        self.assertNotIn("sys.modules", source)

    async def test_reports_directories_of_running_monitor(self):
        import utils.image_monitor as im_mod
        from utils.image_monitor import get_image_monitor

        orig = im_mod._monitor_instance
        im_mod._monitor_instance = None
        self.addCleanup(setattr, im_mod, "_monitor_instance", orig)
        monitor = get_image_monitor(MagicMock(), MagicMock())
        monitor.monitored_directories = [self._root(self.output_dir)]

        resp = await self.client.request("GET", "/prompt_manager/settings")

        data = await resp.json()
        self.assertEqual(resp.status, 200)
        self.assertEqual(data["settings"]["monitored_directories"], ["output"])

    async def test_falls_back_to_configured_roots_without_monitor(self):
        import utils.image_monitor as im_mod

        orig = im_mod._monitor_instance
        im_mod._monitor_instance = None
        self.addCleanup(setattr, im_mod, "_monitor_instance", orig)
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]

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
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]
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

    async def test_deleted_files_leave_the_cached_gallery_listing(self):
        # The listing is cached for 30 s; a delete must not leave the gallery
        # listing (and counting) a file that is gone.
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]
        for name in ("keep.png", "gone.png"):
            (self.output_dir / name).write_bytes(b"same bytes")

        async def listed():
            resp = await self.client.get("/prompt_manager/images/output")
            data = await resp.json()
            return data["total"], sorted(i["filename"] for i in data["images"])

        self.assertEqual(await listed(), (2, ["gone.png", "keep.png"]))
        resp = await self._post({"image_paths": ["gone.png"]})
        self.assertEqual((await resp.json())["deleted_count"], 1)
        self.assertEqual(await listed(), (1, ["keep.png"]))

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
        gate = asyncio.Event()

        async def slow_scan():
            await gate.wait()
            return []

        self.api.find_duplicate_images = slow_scan
        return gate

    async def test_second_scan_while_one_runs_is_409(self):
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

    async def test_invalid_payload_changes_nothing(self):
        before = (
            PromptManagerConfig.RESULT_TIMEOUT,
            PromptManagerConfig.WEBUI_DISPLAY_MODE,
            PromptManagerConfig.WORKER_THREADS,
            PromptManagerConfig.INFINITE_SCROLL,
            list(GalleryConfig.MONITORING_DIRECTORIES),
        )

        resp = await self.client.request(
            "POST",
            "/prompt_manager/settings",
            json={
                "result_timeout": 42,
                "webui_display_mode": "popup",
                "worker_threads": 1,
                "infinite_scroll": True,
                "gallery_root_paths": [os.path.abspath(os.sep)],  # rejected
            },
        )

        self.assertEqual(resp.status, 400)
        after = (
            PromptManagerConfig.RESULT_TIMEOUT,
            PromptManagerConfig.WEBUI_DISPLAY_MODE,
            PromptManagerConfig.WORKER_THREADS,
            PromptManagerConfig.INFINITE_SCROLL,
            list(GalleryConfig.MONITORING_DIRECTORIES),
        )
        self.assertEqual(after, before)
        self.assertFalse(os.path.exists(self.config_path))

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
            json={
                "result_timeout": 7,
                "gallery_root_paths": [self._root(self.output_dir)],
            },
        )

        self.assertEqual(resp.status, 200)
        saved = self._read_config()
        self.assertEqual(saved["integrations"]["lora_manager"]["enabled"], True)
        self.assertEqual(saved["integrations"]["lora_manager"]["path"], "x")
        self.assertEqual(saved["database"]["default_path"], "custom.db")
        self.assertEqual(saved["web_ui"]["result_timeout"], 7)
        self.assertEqual(saved["web_ui"]["show_test_button"], True)
        self.assertEqual(
            saved["gallery"]["monitoring"]["directories"], [self._root(self.output_dir)]
        )

    async def test_save_refuses_a_corrupt_config_file(self):
        # #220: a config.json that cannot be parsed is the user's file, never
        # silently replaced; the save is refused and nothing is published.
        Path(self.config_path).write_bytes(b"{not json")
        before_timeout = PromptManagerConfig.RESULT_TIMEOUT
        resp = await self.client.request(
            "POST", "/prompt_manager/settings", json={"result_timeout": 4}
        )
        body = await resp.json()
        self.assertEqual((resp.status, body["code"]), (409, "config-unreadable"))
        self.assertIs(body["success"], False)
        self.assertIs(body["persisted"], False)
        self.assertEqual(PromptManagerConfig.RESULT_TIMEOUT, before_timeout)
        self.assertEqual(Path(self.config_path).read_bytes(), b"{not json")

    async def test_get_settings_reports_an_unreadable_config(self):
        Path(self.config_path).write_bytes(b"{not json")
        resp = await self.client.request("GET", "/prompt_manager/settings")
        body = await resp.json()
        self.assertEqual(resp.status, 200)  # the legacy page still loads
        self.assertIsNone(body["revision"])
        self.assertEqual(body["config_error"], "config-unreadable")
        self.assertIn("result_timeout", body["settings"])

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
            json={"gallery_root_paths": [self._root(self.output_dir)]},
        )
        data = await resp.json()
        self.assertEqual(resp.status, 200)
        self.assertFalse(data["restart_required"])

    async def test_unwritable_config_path_reports_failure_without_runtime_change(self):
        # #220: the parent is a file, so config.json cannot be written. The
        # save reports the failure and publishes nothing.
        blocker = Path(self.tmpdir) / "not-a-dir"
        blocker.write_text("x")
        os.environ[CONFIG_PATH_ENV] = str(blocker / "c.json")
        GalleryConfig.MONITORING_DIRECTORIES = []
        before_timeout = PromptManagerConfig.RESULT_TIMEOUT
        with self.assertLogs("prompt_manager.api", level="ERROR"):
            resp = await self.client.request(
                "POST",
                "/prompt_manager/settings",
                json={
                    "result_timeout": before_timeout + 1,
                    "gallery_root_paths": [self._root(self.output_dir)],
                },
            )
        body = await resp.json()
        self.assertEqual(
            (resp.status, body["code"], body["persisted"]),
            (500, "config-write-failed", False),
        )
        self.assertIs(body["success"], False)
        self.assertIs(body["runtime_applied"], False)
        self.assertEqual(PromptManagerConfig.RESULT_TIMEOUT, before_timeout)
        self.assertEqual(GalleryConfig.MONITORING_DIRECTORIES, [])
        self.assertEqual(blocker.read_text(), "x")

    async def test_save_reports_the_commit(self):
        resp = await self.client.request(
            "POST", "/prompt_manager/settings", json={"result_timeout": 3}
        )
        body = await resp.json()
        self.assertEqual(resp.status, 200)
        self.assertEqual(body["message"], "Settings saved successfully")
        self.assertIs(body["persisted"], True)
        self.assertIs(body["runtime_applied"], True)
        self.assertIs(body["restart_required"], False)
        self.assertEqual(body["restart_required_fields"], [])
        digest = hashlib.sha256(Path(self.config_path).read_bytes()).hexdigest()
        self.assertEqual(body["revision"], "sha256:" + digest)

    async def test_changed_roots_name_the_restart_field(self):
        resp = await self.client.request(
            "POST",
            "/prompt_manager/settings",
            json={"gallery_root_paths": [self._root(self.output_dir)]},
        )
        body = await resp.json()
        self.assertEqual(resp.status, 200)
        self.assertIs(body["restart_required"], True)
        self.assertEqual(body["restart_required_fields"], ["gallery_root_paths"])

    async def test_runtime_publication_failure_after_commit_is_reported(self):
        # The values are durable; the response must not claim they are live.
        self.api.reset_gallery_roots_cache = _raise
        with self.assertLogs("prompt_manager.api", level="ERROR"):
            resp = await self.client.request(
                "POST",
                "/prompt_manager/settings",
                json={"gallery_root_paths": [self._root(self.output_dir)]},
            )
        body = await resp.json()
        self.assertEqual(resp.status, 200)
        self.assertIs(body["persisted"], True)
        self.assertIs(body["runtime_applied"], False)
        self.assertIs(body["restart_required"], True)
        self.assertEqual(body["restart_required_fields"], ["gallery_root_paths"])
        self.assertEqual(
            self._read_config()["gallery"]["monitoring"]["directories"],
            [self._root(self.output_dir)],
        )

    async def test_get_settings_reports_revision(self):
        resp = await self.client.request("GET", "/prompt_manager/settings")
        body = await resp.json()
        self.assertEqual(body["revision"], "absent")
        self.assertNotIn("config_error", body)

        self._write_config({"web_ui": {"result_timeout": 2}})
        body = await (
            await self.client.request("GET", "/prompt_manager/settings")
        ).json()
        digest = hashlib.sha256(Path(self.config_path).read_bytes()).hexdigest()
        self.assertEqual(body["revision"], "sha256:" + digest)

    async def test_native_save_with_stale_revision_is_409(self):
        self._write_config({"web_ui": {"result_timeout": 2}})
        body = await (
            await self.client.request("GET", "/prompt_manager/settings")
        ).json()
        seen = body["revision"]
        self._write_config({"web_ui": {"result_timeout": 6}})  # another writer
        before = Path(self.config_path).read_bytes()
        before_timeout = PromptManagerConfig.RESULT_TIMEOUT

        resp = await self.client.request(
            "POST",
            "/prompt_manager/settings",
            json={"result_timeout": 9, "expected_revision": seen},
        )
        body = await resp.json()
        self.assertEqual((resp.status, body["code"]), (409, "settings-changed"))
        self.assertIs(body["persisted"], False)
        digest = hashlib.sha256(before).hexdigest()
        self.assertEqual(body["revision"], "sha256:" + digest)
        self.assertEqual(Path(self.config_path).read_bytes(), before)
        self.assertEqual(PromptManagerConfig.RESULT_TIMEOUT, before_timeout)

    async def test_native_save_with_current_revision_commits(self):
        body = await (
            await self.client.request("GET", "/prompt_manager/settings")
        ).json()
        resp = await self.client.request(
            "POST",
            "/prompt_manager/settings",
            json={"result_timeout": 9, "expected_revision": body["revision"]},
        )
        self.assertEqual(resp.status, 200)
        self.assertEqual(self._read_config()["web_ui"]["result_timeout"], 9)

    async def test_expected_revision_must_be_a_string(self):
        resp = await self.client.request(
            "POST",
            "/prompt_manager/settings",
            json={"result_timeout": 9, "expected_revision": 5},
        )
        self.assertEqual(resp.status, 400)
        self.assertFalse(os.path.exists(self.config_path))

    async def test_legacy_partial_update_without_revision_still_merges(self):
        self._write_config(
            {
                "integrations": {"lora_manager": {"civitai_api_key": "secret"}},
                "web_ui": {"result_timeout": 1, "webui_display_mode": "popup"},
                "custom_section": {"keep": True},
            }
        )
        resp = await self.client.request(
            "POST", "/prompt_manager/settings", json={"result_timeout": 8}
        )
        self.assertEqual(resp.status, 200)
        self.assertNotIn("secret", await resp.text())
        saved = self._read_config()
        self.assertEqual(
            saved["integrations"]["lora_manager"]["civitai_api_key"], "secret"
        )
        self.assertEqual(saved["custom_section"], {"keep": True})
        self.assertEqual(saved["web_ui"]["result_timeout"], 8)
        self.assertEqual(saved["web_ui"]["webui_display_mode"], "popup")  # not sent

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
        self._install_monitor(FakeMonitor([self._root(self.output_dir)]))

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
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]
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

    async def test_incompatible_wal_restore_returns_failure_without_publication(
        self,
    ) -> None:
        import sqlite3
        from contextlib import closing

        from database.execution_history import subscribe_history_restore

        self.api.db.save_prompt(text="keep live", prompt_hash="live")
        previous = self.api.db
        live = previous.model.get_connection()
        epoch = previous.get_execution_epoch()
        self.assertEqual(live.execute("PRAGMA page_size").fetchone()[0], 4096)
        self.assertEqual(live.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        compatible = self._snapshot_upload()
        source_path = Path(self.tmpdir) / "large-pages.db"
        with closing(sqlite3.connect(source_path)) as conn:
            conn.execute("PRAGMA page_size=8192")
            conn.execute("VACUUM")
        source = PromptDatabase(str(source_path))
        try:
            source.save_prompt(text="do not publish", prompt_hash="source")
            self.assertEqual(
                source.model.get_connection().execute("PRAGMA page_size").fetchone()[0],
                8192,
            )
        finally:
            source.close_all()
        upload = source_path.read_bytes()
        notices = []
        self.addCleanup(
            subscribe_history_restore(
                previous.model.db_path, lambda: notices.append(True)
            )
        )

        resp = await self._restore("database_file", upload)

        body = await resp.text()
        self.assertEqual(resp.status, 500, body)
        data = json.loads(body)
        self.assertFalse(data["success"])
        self.assertIn("Failed to restore database", data["error"])
        self.assertLess(len(data["error"]), 300)
        self.assertNotIn(self.tmpdir, body)
        self.assertNotIn("/tmp/", body)
        self.assertNotIn("backup_created", data)
        self.assertIs(self.api.db, previous)
        self.assertEqual(previous.get_execution_epoch(), epoch)
        self.assertEqual(notices, [])
        self.assertEqual(
            live.execute("SELECT text FROM prompts").fetchone()[0], "keep live"
        )
        previous.save_prompt(text="still writable", prompt_hash="after")
        self.assertEqual(source_path.read_bytes(), upload)
        backups = list(Path(self.tmpdir).glob("prompts.db.backup_*"))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as conn:
            self.assertEqual(
                conn.execute("SELECT text FROM prompts").fetchone()[0], "keep live"
            )
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

        retry = await self._restore("database_file", compatible)
        self.assertEqual(retry.status, 200, await retry.text())
        self.assertTrue((await retry.json())["success"])
        self.assertEqual(notices, [True])
        self.assertNotEqual(self.api.db.get_execution_epoch(), epoch)
        self.assertEqual(
            [row["text"] for row in self.api.db.search_prompts()], ["keep live"]
        )

    async def test_restore_backup_created_is_a_public_path(self):
        self.api.db.save_prompt(text="restored", prompt_hash="h1")
        resp = await self._restore("database_file", self._snapshot_upload())
        body = await resp.text()
        self.assertEqual(resp.status, 200, body)
        self.assertNotIn(self.tmpdir, body)
        data = json.loads(body)
        self.assertTrue(data["backup_created"].startswith("prompts.db.backup_"))

    def _retention_upload(self) -> bytes:
        import sqlite3
        from contextlib import closing

        path = Path(self.tmpdir) / "retention-source.db"
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("PRAGMA page_size=8192")
            conn.execute("VACUUM")
        source = PromptDatabase(str(path))
        try:
            source.save_prompt(text="do not publish", prompt_hash="source")
            self.assertEqual(
                source.model.get_connection().execute("PRAGMA page_size").fetchone()[0],
                8192,
            )
        finally:
            source.close_all()
        return path.read_bytes()

    def _seed_retention_backups(self, count: int) -> list[Path]:
        snapshot = self._snapshot_upload()
        paths = []
        for index in range(count):
            path = Path(
                f"{self.api.db.model.db_path}.backup_202001{index + 1:02d}_000000"
            )
            path.write_bytes(snapshot)
            os.utime(path, (1_000_000 + index, 1_000_000 + index))
            paths.append(path)
        return paths

    async def test_refused_restore_retains_ten_backups_and_new_recovery_copy(
        self,
    ) -> None:
        import sqlite3
        from contextlib import closing

        from database.execution_history import subscribe_history_restore

        previous = self.api.db
        previous.save_prompt(text="keep live", prompt_hash="live")
        live = previous.model.get_connection()
        self.assertEqual(live.execute("PRAGMA page_size").fetchone()[0], 4096)
        self.assertEqual(live.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        epoch = previous.get_execution_epoch()
        old_backups = self._seed_retention_backups(10)
        upload = self._retention_upload()
        notices = []
        self.addCleanup(
            subscribe_history_restore(
                previous.model.db_path, lambda: notices.append(True)
            )
        )

        response = await self._restore("database_file", upload)

        data = await response.json()
        self.assertEqual(response.status, 500, data)
        self.assertFalse(data["success"])
        self.assertNotIn(self.tmpdir, data["error"])
        remaining = set(Path(self.tmpdir).glob("prompts.db.backup_*"))
        self.assertEqual(len(remaining), 10)
        self.assertEqual(remaining.intersection(old_backups), set(old_backups[1:]))
        new_backups = remaining.difference(old_backups)
        self.assertEqual(len(new_backups), 1)
        with closing(sqlite3.connect(new_backups.pop())) as conn:
            self.assertEqual(
                conn.execute("SELECT text FROM prompts").fetchone()[0], "keep live"
            )
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertIs(self.api.db, previous)
        self.assertEqual(previous.get_execution_epoch(), epoch)
        self.assertEqual(notices, [])
        self.assertEqual(
            live.execute("SELECT text FROM prompts").fetchone()[0], "keep live"
        )
        previous.save_prompt(text="still writes", prompt_hash="after")
        self.assertEqual(
            (Path(self.tmpdir) / "retention-source.db").read_bytes(), upload
        )

    async def test_prebackup_restore_failures_leave_existing_backups_untouched(
        self,
    ) -> None:
        import sqlite3

        self.api.db.save_prompt(text="keep live", prompt_hash="live")
        old_backups = self._seed_retention_backups(11)
        original = {path: path.read_bytes() for path in old_backups}
        epoch = self.api.db.get_execution_epoch()
        invalid = await self._restore("database_file", b"not a database")
        self.assertEqual(invalid.status, 400)
        self.assertFalse((await invalid.json())["success"])
        compatible = self._snapshot_upload()
        with patch(
            "database.models.rotate_history_epoch",
            side_effect=sqlite3.OperationalError("staging failed before safety backup"),
        ):
            staged = await self._restore("database_file", compatible)
        self.assertEqual(staged.status, 500)
        self.assertIn("staging failed", (await staged.json())["error"])
        self.assertEqual(
            set(Path(self.tmpdir).glob("prompts.db.backup_*")), set(old_backups)
        )
        self.assertEqual({path: path.read_bytes() for path in old_backups}, original)
        self.assertEqual(self.api.db.get_execution_epoch(), epoch)
        self.assertIsNotNone(self.api.db.get_prompt_by_hash("live"))

    async def test_retention_failure_keeps_original_restore_error_and_backup(
        self,
    ) -> None:
        import sqlite3
        from contextlib import closing

        self.api.db.save_prompt(text="keep live", prompt_hash="live")
        previous = self.api.db
        epoch = previous.get_execution_epoch()
        old_backups = self._seed_retention_backups(10)
        upload = self._retention_upload()
        with patch(
            "py.api.admin._prune_safety_backups",
            side_effect=OSError("retention cleanup failure /tmp/private"),
        ):
            with self.assertLogs(self.api.logger, level="WARNING") as logs:
                response = await self._restore("database_file", upload)
        data = await response.json()
        self.assertEqual(response.status, 500, data)
        self.assertFalse(data["success"])
        self.assertIn("readonly database", data["error"])
        self.assertNotIn("retention cleanup failure", data["error"])
        self.assertNotIn("/tmp/", data["error"])
        warnings = [
            record.getMessage()
            for record in logs.records
            if record.levelname == "WARNING"
        ]
        self.assertTrue(any("backup" in message.lower() for message in warnings))
        self.assertTrue(
            all("/tmp/" not in message and len(message) < 300 for message in warnings)
        )
        self.assertIs(self.api.db, previous)
        self.assertEqual(previous.get_execution_epoch(), epoch)
        self.assertIsNotNone(previous.get_prompt_by_hash("live"))
        remaining = set(Path(self.tmpdir).glob("prompts.db.backup_*"))
        self.assertEqual(len(remaining), 11)
        new_backups = remaining.difference(old_backups)
        self.assertEqual(len(new_backups), 1)
        with closing(sqlite3.connect(new_backups.pop())) as conn:
            self.assertEqual(
                conn.execute("SELECT text FROM prompts").fetchone()[0], "keep live"
            )
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

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
        self.assertEqual(self.api._ingest_scanned_file(f, meta), "unchanged")

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
        self.assertEqual(
            counts,
            {
                "processed": 2,
                "found": 0,
                "added": 0,
                "linked": 0,
                "enriched": 0,
                "unchanged": 0,
                "ambiguous": 0,
                "unresolved": 0,
                "conflict": 0,
                "failed": 1,
                "pairs_added": 0,
                "pair_links_added": 0,
            },
        )

    async def test_scan_internal_error_streams_error_event(self):
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]
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
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]

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
        self.assertEqual({p["text"] for p in prompts}, {"a red square", "bad"})
        pairs = self.api.db.list_prompt_pairs()["pairs"]
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["negative_text"], "bad")
        self.assertEqual(self.api._scan_job.last_event["type"], "complete")
        self.assertFalse(self.api._scan_job.running)

    async def test_second_request_attaches_to_the_running_scan(self):
        _write_png(self.output_dir / "gen.png", prompt_text="a red square")
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]
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
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]
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
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]
        gate, calls = self._gate_collect()
        gate.set()

        await (await self.client.request("POST", "/prompt_manager/scan")).text()
        body = await (await self.client.request("POST", "/prompt_manager/scan")).text()

        self.assertEqual(len(calls), 2)
        self.assertIn('"unchanged": 1', body)

    async def test_failed_scan_is_not_left_running(self):
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]
        self.api._collect_output_media_sync = _raise

        await (await self.client.request("POST", "/prompt_manager/scan")).text()
        data = await (
            await self.client.request("GET", "/prompt_manager/scan/status")
        ).json()

        self.assertFalse(data["running"])
        self.assertEqual(data["last_event"]["type"], "error")


class TestActivityJobs(AdminAPITestCase):
    """The scan runs as a registry job; legacy routes keep their payloads."""

    def _gate_collect(self):
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

    async def _json(self, method, url):
        resp = await self.client.request(method, url)
        return resp.status, await resp.json()

    async def test_legacy_scan_route_reuses_registry_job_and_payload_shape(self):
        from py.api.admin import SCAN_COUNT_KEYS

        _write_png(self.output_dir / "gen.png", prompt_text="a red square")
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]
        gate, calls = self._gate_collect()

        # The workspace starts the job; the legacy page then attaches to it.
        status, started = await self._json("POST", "/prompt_manager/jobs/scan")
        self.assertEqual(status, 202)
        self.assertTrue(started["success"])
        legacy = asyncio.ensure_future(
            self.client.request("POST", "/prompt_manager/scan", data="{}")
        )
        await asyncio.sleep(0.05)
        gate.set()
        body = await (await legacy).text()
        await asyncio.wait_for(self.api._scan_job.task, 10)

        self.assertEqual(len(calls), 1)
        payloads = _sse_payloads(body)
        progress = [p for p in payloads if p["type"] == "progress"]
        self.assertTrue(progress)
        legacy_keys = set(SCAN_COUNT_KEYS) | {"type", "progress", "status", "processed"}
        for payload in progress:
            self.assertEqual(set(payload), legacy_keys)
        final = payloads[-1]
        self.assertEqual(final["type"], "complete")
        self.assertEqual(set(final), set(SCAN_COUNT_KEYS) | {"type", "directories"})
        self.assertEqual(final["added"], 1)
        self.assertEqual(final["directories"], ["output"])

        status, listed = await self._json("GET", "/prompt_manager/jobs")
        self.assertEqual(status, 200)
        self.assertEqual(len(listed["jobs"]), 1)
        job = listed["jobs"][0]
        self.assertEqual(job["id"], started["job"]["id"])
        self.assertEqual(job["id"], self.api._scan_job.id)
        self.assertEqual((job["kind"], job["state"]), ("scan", "finished"))
        self.assertEqual(job["counters"]["added"], 1)
        self.assertEqual(job["progress"], 100)
        self.assertEqual(job["scope"]["output_folders"], 1)

    async def test_scan_records_outcomes_by_group(self):
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]
        (self.output_dir / "sub").mkdir()
        names = {
            "sub/added.png": {"status": "added", "title": "A red fox"},
            "linked.png": {"status": "linked", "title": "A grey cat"},
            "unchanged.png": {"status": "unchanged", "title": "A brown dog"},
            "ambiguous.png": {"status": "ambiguous"},
            "failed.png": RuntimeError(f"{self.tmpdir}/secret boom"),
            "none.png": None,
        }
        for name in names:
            (self.output_dir / name).write_bytes(b"not an image")

        def fake_ingest(
            media_file,
            metadata,
            detailed=False,
            ambiguous=None,
            untraced=None,
            records=None,
        ):
            key = Path(media_file).relative_to(self.output_dir).as_posix()
            outcome = names[key]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        self.api._ingest_scanned_file = fake_ingest
        with self.assertLogs("prompt_manager.api", level="ERROR"):
            status, started = await self._json("POST", "/prompt_manager/jobs/scan")
            self.assertEqual(status, 202)
            await asyncio.wait_for(self.api._scan_job.task, 10)

        resp = await self.client.request(
            "GET", f"/prompt_manager/jobs/{started['job']['id']}"
        )
        text = await resp.text()
        self.assertEqual(resp.status, 200)
        self.assertNotIn(self.tmpdir, text)
        job = json.loads(text)["job"]
        self.assertEqual(
            job["counters"],
            {"added": 2, "known": 1, "review": 1, "no_metadata": 1, "errors": 1},
        )
        outcomes = job["outcomes"]
        self.assertEqual(
            {k: len(v) for k, v in outcomes.items()},
            {"added": 2, "known": 1, "review": 1, "no_metadata": 1, "errors": 1},
        )
        added = {row["path"]: row for row in outcomes["added"]}
        self.assertEqual(set(added), {"sub/added.png", "linked.png"})
        self.assertIn("A red fox", added["sub/added.png"]["detail"])
        self.assertIn("A grey cat", added["linked.png"]["detail"])
        self.assertEqual(added["linked.png"]["root"], "output")
        self.assertIn("A brown dog", outcomes["known"][0]["detail"])
        self.assertIn("review", outcomes["review"][0]["detail"].lower())
        self.assertEqual(outcomes["errors"][0]["path"], "failed.png")
        self.assertTrue(outcomes["errors"][0]["detail"])
        self.assertEqual(outcomes["no_metadata"][0]["path"], "none.png")
        self.assertTrue(outcomes["no_metadata"][0]["detail"])
        prefixes = {line.split(" ", 2)[1] for line in job["log"][1:-1]}
        self.assertEqual(prefixes, {"+", "=", "?", "!", "-"})
        self.assertEqual(job["state"], "finished")
        self.assertTrue(job["status_line"].startswith("Processing file 6 of 6"))

        legacy = self.api._scan_job.last_event
        self.assertEqual(
            (legacy["processed"], legacy["found"], legacy["failed"]), (6, 4, 1)
        )

    async def test_report_is_attachment_without_absolute_paths(self):
        """Task 5: the report downloads as JSON, whole and without server paths."""
        import re

        from py.api.jobs import MAX_OUTCOME_ROWS

        async def runner(job):
            absolute = f"{self.tmpdir}/output/deep/abs.png"
            job.record(
                "added",
                {"path": absolute, "root": "output", "root_index": 0, "detail": "ok"},
                f"+ {absolute} · ok",
            )
            for index in range(MAX_OUTCOME_ROWS + 5):
                job.record(
                    "known",
                    {"path": f"k/{index}.png", "root": "output", "detail": "x"},
                    f"= k/{index}.png",
                )
            job.update(processed=MAX_OUTCOME_ROWS + 6, total=MAX_OUTCOME_ROWS + 6)

        job = self.api._jobs.start(
            "scan",
            "Scan for metadata",
            record_changing=True,
            scope={"files": "new", "output_folders": 1},
            runner=runner,
            groups=("added", "known", "review", "no_metadata", "errors"),
        )
        await asyncio.wait_for(job.task, 10)

        resp = await self.client.request("GET", f"/prompt_manager/jobs/{job.id}/report")
        text = await resp.text()
        self.assertEqual(resp.status, 200)
        self.assertIn("application/json", resp.headers["Content-Type"])
        disposition = resp.headers["Content-Disposition"]
        self.assertRegex(
            disposition,
            r'^attachment; filename="prompt-manager-scan-\d{8}-\d{4}\.json"$',
        )
        self.assertNotIn(self.tmpdir, text)
        self.assertIsNone(
            re.search(r"[A-Za-z]:[\\/]|(?<![\w.])/(?:tmp|home|var)/", text)
        )
        report = json.loads(text)
        self.assertEqual(report["job"]["id"], job.id)
        self.assertEqual(report["job"]["scope"]["files"], "new")
        self.assertEqual(report["job"]["counters"]["known"], MAX_OUTCOME_ROWS + 5)
        added = report["outcomes"]["added"]
        self.assertEqual(
            (added["count"], len(added["rows"]), added["truncated"]), (1, 1, False)
        )
        self.assertEqual(added["rows"][0]["path"], "abs.png")
        known = report["outcomes"]["known"]
        self.assertEqual(
            (known["count"], len(known["rows"]), known["truncated"]),
            (MAX_OUTCOME_ROWS + 5, MAX_OUTCOME_ROWS, True),
        )
        self.assertEqual(report["outcomes"]["errors"]["rows"], [])
        self.assertFalse(report["outcomes"]["errors"]["truncated"])
        self.assertEqual(report["log"], job.detail()["log"])
        self.assertIn("generated_at", report)

        resp = await self.client.request("GET", "/prompt_manager/jobs/nope/report")
        self.assertEqual(resp.status, 404)

    async def test_cancel_after_first_batch_keeps_partial_counts(self):
        """Review M7: the real scan stops at the batch boundary, keeping batch 1."""
        import threading
        from unittest import mock

        from py.api import admin

        _write_png(self.output_dir / "a.png", prompt_text="a red square")
        _write_png(self.output_dir / "b.png", prompt_text="a blue circle")
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]
        first_done, release = threading.Event(), threading.Event()
        original = self.api._ingest_scan_batch_sync

        def gated(batch_results, roots=(), outcomes=None, review=None):
            counts = original(
                batch_results, roots=roots, outcomes=outcomes, review=review
            )
            first_done.set()
            release.wait(5)
            return counts

        self.api._ingest_scan_batch_sync = gated
        with mock.patch.object(admin, "SCAN_BATCH_SIZE", 1):
            status, started = await self._json("POST", "/prompt_manager/jobs/scan")
            self.assertEqual(status, 202)
            job_id = started["job"]["id"]
            await asyncio.get_running_loop().run_in_executor(None, first_done.wait, 5)
            status, cancelled = await self._json(
                "POST", f"/prompt_manager/jobs/{job_id}/cancel"
            )
            self.assertEqual((status, cancelled["cancellation_requested"]), (200, True))
            release.set()
            await asyncio.wait_for(self.api._scan_job.task, 10)

        status, body = await self._json("GET", f"/prompt_manager/jobs/{job_id}")
        job = body["job"]
        self.assertEqual(job["state"], "cancelled")
        self.assertEqual((job["processed"], job["total"]), (1, 2))
        self.assertEqual(sum(job["counters"].values()), job["processed"])
        self.assertEqual(job["counters"]["added"], 1)
        listed = [row["path"] for rows in job["outcomes"].values() for row in rows]
        self.assertEqual(len(listed), 1)
        self.assertIn(listed[0], {"a.png", "b.png"})
        self.assertIn("Scan cancelled · 1 file processed", job["log"][-1])
        # A cancel after the end is a no-op.
        status, again = await self._json(
            "POST", f"/prompt_manager/jobs/{job_id}/cancel"
        )
        self.assertEqual((status, again["cancellation_requested"]), (200, False))

    async def test_scan_rows_carry_their_root_index(self):
        """Review M9: two roots labelled "output" stay apart by listing index."""
        second = self.outside_dir / "output"
        second.mkdir(parents=True)
        os.environ[EXTRA_ROOTS_ENV] = str(second)
        _write_png(self.output_dir / "same.png", prompt_text="a red square")
        _write_png(second / "same.png", prompt_text="a blue circle")
        GalleryConfig.MONITORING_DIRECTORIES = [
            self._root(self.output_dir),
            self._root(second),
        ]
        output_dirs = [Path(d) for d in self.api._get_all_output_dirs()]
        self.assertEqual(len(output_dirs), 2)
        status, started = await self._json("POST", "/prompt_manager/jobs/scan")
        await asyncio.wait_for(self.api._scan_job.task, 10)
        status, body = await self._json(
            "GET", f"/prompt_manager/jobs/{started['job']['id']}"
        )
        rows = body["job"]["outcomes"]["added"]
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["root"] for row in rows}, {"output"})
        by_index = {row["root_index"]: row for row in rows}
        self.assertEqual(set(by_index), {0, 1})
        for index, row in by_index.items():
            self.assertEqual(row["path"], "same.png")
            self.assertTrue((output_dirs[index] / row["path"]).is_file())
        texts = {
            index: (
                "red"
                if output_dirs[index] == Path(self._root(self.output_dir))
                else "blue"
            )
            for index in by_index
        }
        for index, row in by_index.items():
            self.assertIn(texts[index], row["detail"])

    async def test_scan_row_names_the_prompt_by_its_stored_title(self):
        """Review M10: a row names the prompt as the Library does."""
        _write_png(self.output_dir / "gen.png", prompt_text="a red square\nmore")
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]
        summaries = self.api.db.get_image_link_summaries

        def titled(paths, allow_filename_fallback=True):
            found = summaries(paths, allow_filename_fallback)
            return {
                path: {**summary, "title": "Stored square title"}
                for path, summary in found.items()
            }

        self.api.db.get_image_link_summaries = titled
        status, started = await self._json("POST", "/prompt_manager/jobs/scan")
        await asyncio.wait_for(self.api._scan_job.task, 10)
        status, body = await self._json(
            "GET", f"/prompt_manager/jobs/{started['job']['id']}"
        )
        (row,) = body["job"]["outcomes"]["added"]
        self.assertIn("“Stored square title”", row["detail"])

        # Without a stored title (or an unreadable one): the text's first line.
        def broken(paths, allow_filename_fallback=True):
            raise RuntimeError("unreadable")

        self.api.db.get_image_link_summaries = broken
        _write_png(self.output_dir / "next.png", prompt_text="a green cone\nmore")
        status, started = await self._json("POST", "/prompt_manager/jobs/scan")
        await asyncio.wait_for(self.api._scan_job.task, 10)
        status, body = await self._json(
            "GET", f"/prompt_manager/jobs/{started['job']['id']}"
        )
        details = {r["path"]: r["detail"] for r in body["job"]["outcomes"]["added"]}
        self.assertIn("“a green cone”", details["next.png"])

    async def test_jobs_routes_404_and_409(self):
        _write_png(self.output_dir / "gen.png", prompt_text="a red square")
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]
        for method, url in (
            ("GET", "/prompt_manager/jobs/nope"),
            ("GET", "/prompt_manager/jobs/nope/events"),
            ("POST", "/prompt_manager/jobs/nope/cancel"),
        ):
            status, data = await self._json(method, url)
            self.assertEqual(status, 404, url)
            self.assertFalse(data["success"])

        gate, calls = self._gate_collect()
        status, started = await self._json("POST", "/prompt_manager/jobs/scan")
        self.assertEqual(status, 202)
        job_id = started["job"]["id"]
        self.assertEqual(started["job"]["state"], "running")

        status, busy = await self._json("POST", "/prompt_manager/jobs/scan")
        self.assertEqual(status, 409)
        self.assertEqual(
            busy["running"], {"id": job_id, "title": started["job"]["title"]}
        )
        self.assertEqual((busy["success"], busy["code"]), (False, "busy"))

        watcher = asyncio.ensure_future(
            self.client.request("GET", f"/prompt_manager/jobs/{job_id}/events")
        )
        await asyncio.sleep(0.05)
        status, cancel = await self._json(
            "POST", f"/prompt_manager/jobs/{job_id}/cancel"
        )
        self.assertEqual((status, cancel["cancellation_requested"]), (200, True))
        gate.set()
        events = _sse_payloads(await (await watcher).text())
        await asyncio.wait_for(self.api._scan_job.task, 10)

        self.assertEqual(len(calls), 1)
        self.assertEqual(events[0]["id"], job_id)
        self.assertEqual(events[-1]["state"], "cancelled")
        status, cancel = await self._json(
            "POST", f"/prompt_manager/jobs/{job_id}/cancel"
        )
        self.assertEqual((status, cancel["cancellation_requested"]), (200, False))
        status, legacy = await self._json("GET", "/prompt_manager/scan/status")
        self.assertEqual(legacy["last_event"]["type"], "cancelled")

    async def test_scan_without_directories_fails_the_job(self):
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.outside_dir)]
        sys.modules.pop("folder_paths", None)
        self.api._find_comfyui_output_dir = lambda: None

        status, started = await self._json("POST", "/prompt_manager/jobs/scan")
        await asyncio.wait_for(self.api._scan_job.task, 10)
        status, data = await self._json(
            "GET", f"/prompt_manager/jobs/{started['job']['id']}"
        )
        self.assertEqual(data["job"]["state"], "failed")
        self.assertIn("No output directories", data["job"]["status_line"])

    async def test_legacy_scan_refused_while_another_record_job_runs(self):
        hold = asyncio.Event()

        async def runner(job):
            await hold.wait()

        other = self.api._jobs.start(
            "maintenance", "Maintenance", record_changing=True, scope={}, runner=runner
        )
        resp = await self.client.request("POST", "/prompt_manager/scan", data="{}")
        self.assertEqual(resp.status, 409)
        self.assertIn("Maintenance", (await resp.json())["error"])
        hold.set()
        await asyncio.wait_for(other.task, 5)


class TestScanStatusLine(unittest.TestCase):

    def test_estimate_appears_after_two_batches(self):
        from py.api.admin import _scan_status_line

        self.assertEqual(
            _scan_status_line(50, 200, 1, 10.0), "Processing file 50 of 200"
        )
        self.assertEqual(
            _scan_status_line(100, 1000, 2, 30.0),
            "Processing file 100 of 1,000 · about 5 min left",
        )
        self.assertEqual(
            _scan_status_line(100, 120, 2, 1.0),
            "Processing file 100 of 120 · about 1 min left",
        )
        self.assertEqual(
            _scan_status_line(120, 120, 3, 9.0), "Processing file 120 of 120"
        )


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
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]
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
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]
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
    """Videos carry the graph in per-key container tags (current savers) or in
    the old VideoHelperSuite comment envelope; the scan reads one carrier."""

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
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]

        with self.assertNoLogs(level="ERROR"):
            body = await (
                await self.client.request("POST", "/prompt_manager/scan")
            ).text()

        self.assertIn('"added": 1', body)
        prompts = self.api.db.get_recent_prompts(limit=10)["prompts"]
        self.assertEqual([p["text"] for p in prompts], ["a cat"])
        images = self.api.db.get_prompt_images(prompts[0]["id"])
        self.assertTrue(images[0]["image_path"].endswith("clip.mp4"))

    async def test_scan_links_per_key_tag_video_with_default_profile(self):
        """A current saver's per-key tags scan exactly like the same graph as PNG."""
        from utils import video_metadata

        _write_png(self.output_dir / "same.png", prompt_text="a cat")
        graph = {
            "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat"}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "bad"}},
            "3": {
                "class_type": "KSampler",
                "inputs": {"positive": ["1", 0], "negative": ["2", 0]},
            },
        }
        (self.output_dir / "clip.mkv").write_bytes(b"\x1aE\xdf\xa3 fixture")
        probed = {
            "format": {
                "tags": {
                    "PROMPT": json.dumps(graph),
                    "WORKFLOW": '{"nodes":[]}',
                    "COMMENT": json.dumps({"prompt": "must not win"}),
                }
            }
        }
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]

        with (
            patch.object(video_metadata.shutil, "which", return_value="/bin/ffprobe"),
            patch.object(
                video_metadata.subprocess,
                "run",
                return_value=MagicMock(returncode=0, stdout=json.dumps(probed)),
            ) as run,
            self.assertNoLogs(level="ERROR"),
        ):
            body = await (
                await self.client.request("POST", "/prompt_manager/scan")
            ).text()

        self.assertEqual(run.call_args.args[0][-1], str(self.output_dir / "clip.mkv"))
        self.assertIn('"found": 2', body)
        prompts = self.api.db.get_recent_prompts(limit=10)["prompts"]
        self.assertEqual(sorted(p["text"] for p in prompts), ["a cat", "bad"])
        positive = next(p for p in prompts if p["text"] == "a cat")
        images = self.api.db.get_prompt_images(positive["id"])
        self.assertEqual(
            sorted(os.path.basename(image["image_path"]) for image in images),
            ["clip.mkv", "same.png"],
        )
        self.assertNotIn("must not win", json.dumps(prompts))

    async def test_scan_without_ffprobe_skips_videos_quietly(self):
        from unittest.mock import patch
        from utils import video_metadata

        (self.output_dir / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypisom")
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]

        with patch.object(video_metadata.shutil, "which", return_value=None):
            with self.assertNoLogs("prompt_manager", level="WARNING"):
                body = await (
                    await self.client.request("POST", "/prompt_manager/scan")
                ).text()

        self.assertIn('"processed": 1', body)
        self.assertIn('"found": 0', body)

    async def test_unreadable_image_is_a_warning_not_an_error(self):
        (self.output_dir / "bad.png").write_bytes(b"\x89PNG not really")
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]

        with self.assertLogs("prompt_manager", level="WARNING") as cm:
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
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]

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
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]
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
