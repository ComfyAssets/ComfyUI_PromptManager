"""
Route tests for the image API (py/api/images.py).

Uses aiohttp's test client against a real PromptManagerAPI wired to a
temporary SQLite database and a temporary output directory.
"""

import asyncio
import json
import os
import pathlib
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
from contextlib import ExitStack
from pathlib import Path, PurePath
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ComfyUI-only modules are stubbed so the API can be imported standalone.
try:
    from host_stubs import install_folder_paths_stub
except ImportError:  # imported as tests.test_api_images
    from tests.host_stubs import install_folder_paths_stub

install_folder_paths_stub()
sys.modules.setdefault("server", MagicMock())

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import AioHTTPTestCase  # noqa: E402
from PIL import Image  # noqa: E402

from database.models import PromptModel  # noqa: E402
from database.operations import PromptDatabase  # noqa: E402
import py.api.images as images_module
from py.api import gallery_listing  # noqa: E402
from py import lora_utils  # noqa: E402
from py import lora_manager_layout as layout_module  # noqa: E402
from py.api.thumbnail_settings import ThumbnailSettings  # noqa: E402
from py.api import PromptManagerAPI  # noqa: E402
from tests.private_temp import private_temp_db  # noqa: E402
from py.api.prompts import MAX_PAGE_LIMIT  # noqa: E402
from py.config import GalleryConfig  # noqa: E402
from utils.hashing import generate_prompt_hash  # noqa: E402


def make_png(path, size=(4, 4), color=(255, 0, 0)):
    """Write a tiny but valid PNG to *path* and return it as a Path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", size, color) as img:
        img.save(path, "PNG")
    return path


class ImageAPITestCase(AioHTTPTestCase):
    """App with PromptManager routes, a temp DB and a temp output directory."""

    async def get_application(self):
        self._temp_db = private_temp_db(self)

        self._output_tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        # A fake ComfyUI tree: <tmp>/output is the gallery root and <tmp> is the
        # anchor that public (response) paths are rendered relative to.
        self.comfy_dir = Path(os.path.realpath(self._output_tmp.name))
        self.output_dir = self.comfy_dir / "output"
        self.output_dir.mkdir()
        anchors = [os.path.normcase(str(self.comfy_dir))]
        self._anchor_patch = patch.object(
            GalleryConfig, "path_anchors", classmethod(lambda cls: list(anchors))
        )
        self._anchor_patch.start()

        app = web.Application()
        routes = web.RouteTableDef()

        self.api = PromptManagerAPI(db=PromptDatabase(self._temp_db.name))
        self.api._get_all_output_dirs = lambda: [self.output_dir]
        self.api._find_comfyui_output_dir = lambda: str(self.output_dir)
        self.api.add_routes(routes)
        app.router.add_routes(routes)
        return app

    async def tearDownAsync(self):
        await super().tearDownAsync()  # closes the aiohttp test client
        # The automatic preview worker writes into the temp tree: stop it first.
        self.api.close_auto_thumbnails()
        self._anchor_patch.stop()
        # Windows refuses to unlink a database that still has open handles.
        self.api.db.close_all()
        try:
            leaked = self._close_leaked_connections()
        finally:
            for path in (
                self._temp_db.name,
                self._temp_db.name + "-wal",
                self._temp_db.name + "-shm",
            ):
                if os.path.exists(path):
                    os.unlink(path)
            self._output_tmp.cleanup()
        if leaked:
            raise AssertionError(
                f"{leaked} connection(s) to the test database were still open at "
                "teardown; Windows cannot unlink it (WinError 32). Close every "
                "extra PromptDatabase before tearDownAsync, not in a cleanup."
            )

    def _close_leaked_connections(self):
        """Close and count connections other models still hold on the temp DB.

        Linux unlinks an open SQLite file, Windows does not; counting here makes
        a handle left open past teardown fail on every OS (#475).
        """
        key = os.path.normcase(os.path.realpath(self._temp_db.name))
        with PromptModel._instances_lock:
            models = list(PromptModel._instances)
        leaked = 0
        for model in models:
            if model._schema_key() != key:
                continue
            with model._conn_lock:
                leaked += len(model._connections)
            model.close_all()
        return leaked

    # -- helpers ---------------------------------------------------------

    def _save_prompt(self, text="Test prompt", **kwargs):
        return self.api.db.save_prompt(
            text=text, prompt_hash=generate_prompt_hash(text), **kwargs
        )

    def _link_image(self, image_path, text="Linked prompt"):
        """Create a prompt and link *image_path* to it; return the image id."""
        prompt_id = self._save_prompt(text)
        image_id = self.api.db.link_image_to_prompt(prompt_id, str(image_path))
        self.assertGreater(image_id, 0, "fixture: image link failed")
        return image_id

    def _make_symlink(self, target, link, target_is_directory=False):
        """Create a symlink or skip the test when the platform refuses."""
        try:
            os.symlink(str(target), str(link), target_is_directory=target_is_directory)
        except (OSError, NotImplementedError, AttributeError) as exc:
            self.skipTest(f"symlinks unavailable: {exc}")

    def _spy_executor(self):
        """Record the __name__ of every callable sent to _run_in_executor."""
        original = self.api._run_in_executor
        calls = []

        async def spy(func, *args, **kwargs):
            calls.append(getattr(func, "__name__", repr(func)))
            return await original(func, *args, **kwargs)

        self.api._run_in_executor = spy
        return calls


class TestServeOutputImage(ImageAPITestCase):
    """GET /prompt_manager/images/serve/{filepath}"""

    async def test_serves_png_inside_output_dir(self):
        make_png(self.output_dir / "sub" / "pic.png")

        resp = await self.client.request(
            "GET", "/prompt_manager/images/serve/sub/pic.png"
        )

        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.headers["Content-Type"], "image/png")
        body = await resp.read()
        self.assertTrue(body.startswith(b"\x89PNG"))

    async def test_non_media_extensions_are_forbidden(self):
        for name in ("secret.py", "prompts.db", "notes.txt"):
            (self.output_dir / name).write_text("not an image")
            resp = await self.client.request(
                "GET", f"/prompt_manager/images/serve/{name}"
            )
            self.assertEqual(resp.status, 403, name)
            data = await resp.json()
            self.assertFalse(data["success"])

    async def test_extension_check_is_case_insensitive(self):
        make_png(self.output_dir / "UPPER.PNG")

        resp = await self.client.request(
            "GET", "/prompt_manager/images/serve/UPPER.PNG"
        )

        self.assertEqual(resp.status, 200)

    async def test_empty_allowed_dirs_fails_closed(self):
        make_png(self.output_dir / "pic.png")
        self.api._get_all_output_dirs = lambda: []

        resp = await self.client.request("GET", "/prompt_manager/images/serve/pic.png")

        self.assertEqual(resp.status, 403)
        data = await resp.json()
        self.assertFalse(data["success"])

    async def test_dot_dot_traversal_is_forbidden(self):
        outside = Path(self._output_tmp.name).parent / "pm_outside_probe.png"
        make_png(outside)
        self.addCleanup(lambda: outside.exists() and outside.unlink())

        # "..%2f" survives URL normalisation and reaches the handler as "../".
        resp = await self.client.request(
            "GET", "/prompt_manager/images/serve/..%2fpm_outside_probe.png"
        )

        self.assertEqual(resp.status, 403)

    async def test_absolute_path_is_forbidden(self):
        outside = Path(self._output_tmp.name).parent / "pm_abs_probe.png"
        make_png(outside)
        self.addCleanup(lambda: outside.exists() and outside.unlink())

        encoded = str(outside).replace(os.sep, "%2f").replace("/", "%2f")
        resp = await self.client.request(
            "GET", f"/prompt_manager/images/serve/{encoded}"
        )

        self.assertEqual(resp.status, 403)

    async def test_symlink_escaping_output_dir_is_forbidden(self):
        with tempfile.TemporaryDirectory() as other:
            target = make_png(Path(other) / "elsewhere.png")
            self._make_symlink(target, self.output_dir / "escape.png")

            resp = await self.client.request(
                "GET", "/prompt_manager/images/serve/escape.png"
            )

            self.assertEqual(resp.status, 403)

    async def test_every_gallery_video_format_is_served(self):
        # The gallery lists these as playable cards; opening them must not be a 403.
        for ext in sorted(images_module.VIDEO_EXTENSIONS):
            (self.output_dir / f"clip{ext}").write_bytes(b"\x00\x00\x00\x18ftyp")

        for ext in sorted(images_module.VIDEO_EXTENSIONS):
            resp = await self.client.request(
                "GET", f"/prompt_manager/images/serve/clip{ext}"
            )
            await resp.read()
            self.assertEqual(resp.status, 200, ext)

    async def test_missing_file_is_404(self):
        resp = await self.client.request("GET", "/prompt_manager/images/serve/nope.png")

        self.assertEqual(resp.status, 404)
        data = await resp.json()
        self.assertFalse(data["success"])

    async def test_root_index_selects_single_root(self):
        with tempfile.TemporaryDirectory() as second:
            second_dir = Path(os.path.realpath(second))
            make_png(second_dir / "only_here.png")
            self.api._get_all_output_dirs = lambda: [self.output_dir, second_dir]

            hit = await self.client.request(
                "GET", "/prompt_manager/images/serve/only_here.png?root=1"
            )
            miss = await self.client.request(
                "GET", "/prompt_manager/images/serve/only_here.png?root=0"
            )
            bad_index = await self.client.request(
                "GET", "/prompt_manager/images/serve/only_here.png?root=abc"
            )

            self.assertEqual(hit.status, 200)
            self.assertEqual(miss.status, 404)
            self.assertEqual(bad_index.status, 403)
            for resp in (hit, miss, bad_index):
                await resp.read()  # release the served file before the directory goes


class TestServeImageById(ImageAPITestCase):
    """GET /prompt_manager/images/{image_id}/file"""

    async def test_serves_linked_png(self):
        png = make_png(self.output_dir / "linked.png")
        image_id = self._link_image(png)

        resp = await self.client.request(
            "GET", f"/prompt_manager/images/{image_id}/file"
        )

        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.headers["Content-Type"], "image/png")

    async def test_non_media_record_is_forbidden(self):
        txt = self.output_dir / "linked.txt"
        txt.write_text("plain")
        image_id = self._link_image(txt)

        resp = await self.client.request(
            "GET", f"/prompt_manager/images/{image_id}/file"
        )

        self.assertEqual(resp.status, 403)

    async def test_empty_allowed_dirs_fails_closed(self):
        png = make_png(self.output_dir / "linked.png")
        image_id = self._link_image(png)
        self.api._get_all_output_dirs = lambda: []

        resp = await self.client.request(
            "GET", f"/prompt_manager/images/{image_id}/file"
        )

        self.assertEqual(resp.status, 403)

    async def test_record_outside_allowed_dirs_is_forbidden(self):
        with tempfile.TemporaryDirectory() as other:
            png = make_png(Path(other) / "outside.png")
            image_id = self._link_image(png)

            resp = await self.client.request(
                "GET", f"/prompt_manager/images/{image_id}/file"
            )

            self.assertEqual(resp.status, 403)

    async def test_symlink_record_escaping_is_forbidden(self):
        with tempfile.TemporaryDirectory() as other:
            target = make_png(Path(other) / "elsewhere.png")
            link = self.output_dir / "escape.png"
            self._make_symlink(target, link)
            image_id = self._link_image(link)

            resp = await self.client.request(
                "GET", f"/prompt_manager/images/{image_id}/file"
            )

            self.assertEqual(resp.status, 403)

    async def test_missing_file_is_404(self):
        image_id = self._link_image(self.output_dir / "gone.png")

        resp = await self.client.request(
            "GET", f"/prompt_manager/images/{image_id}/file"
        )

        self.assertEqual(resp.status, 404)

    async def test_unknown_id_is_404(self):
        resp = await self.client.request("GET", "/prompt_manager/images/424242/file")

        self.assertEqual(resp.status, 404)

    async def test_non_numeric_id_is_400(self):
        resp = await self.client.request("GET", "/prompt_manager/images/abc/file")

        self.assertEqual(resp.status, 400)


class TestRecentImagesBounds(ImageAPITestCase):
    """GET /prompt_manager/images/recent clamps limit/offset and rejects junk."""

    async def test_limit_is_clamped_to_max_page_limit(self):
        resp = await self.client.request(
            "GET", "/prompt_manager/images/recent?limit=999999"
        )

        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["success"])
        self.assertLessEqual(len(data["images"]), MAX_PAGE_LIMIT)
        self.assertEqual(data["pagination"]["limit"], MAX_PAGE_LIMIT)

    async def test_non_integer_limit_is_400(self):
        resp = await self.client.request(
            "GET", "/prompt_manager/images/recent?limit=abc"
        )

        self.assertEqual(resp.status, 400)
        data = await resp.json()
        self.assertFalse(data["success"])
        self.assertIn("error", data)

    async def test_negative_offset_is_clamped_to_zero(self):
        # Negative offsets are clamped rather than rejected so that a
        # client paging backwards past the first page still gets page one.
        resp = await self.client.request(
            "GET", "/prompt_manager/images/recent?offset=-5"
        )

        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["pagination"]["offset"], 0)

    async def test_offset_skips_rows(self):
        for name in ("a.png", "b.png", "c.png"):
            self._link_image(make_png(self.output_dir / name), text=f"p-{name}")

        page = await self.client.request(
            "GET", "/prompt_manager/images/recent?limit=2&offset=2"
        )

        data = await page.json()
        self.assertEqual(len(data["images"]), 1)
        self.assertEqual(data["pagination"]["offset"], 2)


def parse_sse(text):
    """Turn an SSE body into a list of (event, data_dict) tuples."""
    events = []
    for block in text.strip().split("\n\n"):
        event, data = None, None
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data = json.loads(line[len("data:") :].strip())
        if event is not None:
            events.append((event, data))
    return events


class ThumbnailTestCase(ImageAPITestCase):
    """Shared helpers for the thumbnail routes."""

    PROGRESS_URL = "/prompt_manager/images/generate-thumbnails/progress"

    def _thumb_path(self, name, suffix=".webp"):
        return self.output_dir / "thumbnails" / f"{name}_thumb{suffix}"


class TestThumbnailProgressStream(ThumbnailTestCase):
    """POST /prompt_manager/images/generate-thumbnails/progress (SSE)."""

    def _record_threads(self):
        """Make every thumbnail write record its thread and take a moment."""
        import threading
        import time

        from py.api import images as images_module

        seen = set()
        lock = threading.Lock()
        original = images_module._write_image_thumbnail

        def recording(src, dst, size, *args):
            with lock:
                seen.add(threading.current_thread().name)
            time.sleep(0.05)
            return original(src, dst, size, *args)

        images_module._write_image_thumbnail = recording
        self.addCleanup(setattr, images_module, "_write_image_thumbnail", original)
        return seen

    async def test_thumbnails_are_generated_on_several_threads(self):
        from py.config import PromptManagerConfig

        for i in range(8):
            make_png(self.output_dir / f"p{i}.png", size=(64, 64))
        orig = PromptManagerConfig.WORKER_THREADS
        PromptManagerConfig.WORKER_THREADS = 4
        self.addCleanup(setattr, PromptManagerConfig, "WORKER_THREADS", orig)
        seen = self._record_threads()

        resp = await self.client.request("POST", self.PROGRESS_URL)
        events = parse_sse(await resp.text())

        names = [e for e, _ in events]
        self.assertEqual(names.count("progress"), 8)
        self.assertEqual(dict(events)["complete"]["count"], 8)
        self.assertGreaterEqual(len(seen), 2)
        for i in range(8):
            self.assertTrue(self._thumb_path(f"p{i}").is_file())

    async def test_blocking_endpoint_also_uses_the_worker_threads(self):
        from py.config import PromptManagerConfig

        for i in range(8):
            make_png(self.output_dir / f"p{i}.png", size=(64, 64))
        orig = PromptManagerConfig.WORKER_THREADS
        PromptManagerConfig.WORKER_THREADS = 4
        self.addCleanup(setattr, PromptManagerConfig, "WORKER_THREADS", orig)
        seen = self._record_threads()

        resp = await self.client.request(
            "POST", "/prompt_manager/images/generate-thumbnails", json={}
        )

        self.assertEqual((await resp.json())["count"], 8)
        self.assertGreaterEqual(len(seen), 2)

    async def test_get_is_method_not_allowed(self):
        # The stream writes thumbnails, so a plain GET (prefetch, link
        # preview, <img src>) must not be able to trigger it.
        make_png(self.output_dir / "a.png")

        resp = await self.client.request("GET", self.PROGRESS_URL)

        self.assertEqual(resp.status, 405)
        self.assertFalse(self._thumb_path("a").exists())

    async def test_three_pngs_yield_three_thumbnails_and_events(self):
        for name in ("a", "b", "c"):
            make_png(self.output_dir / f"{name}.png", size=(64, 64))
        calls = self._spy_executor()

        resp = await self.client.request("POST", f"{self.PROGRESS_URL}?quality=low")

        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.headers["Content-Type"], "text/event-stream")
        events = parse_sse(await resp.text())
        names = [e for e, _ in events]
        self.assertEqual(names.count("progress"), 3)
        self.assertEqual(names.count("complete"), 1)
        complete = dict(events)["complete"]
        self.assertEqual(complete["count"], 3)
        for name in ("a", "b", "c"):
            self.assertTrue(self._thumb_path(name).is_file(), name)
        with Image.open(self._thumb_path("a")) as thumb:
            self.assertLessEqual(max(thumb.size), 150)
        # PIL work ran in the executor in batches (never on the loop), plus the scan.
        self.assertGreaterEqual(calls.count("_generate_batch"), 1)
        self.assertNotIn("_generate_one", calls)
        self.assertIn("_plan_gallery_thumbnails", calls)

    async def test_a_run_past_the_batch_cap_goes_on_to_the_end(self):
        # The cap bounds one batch; the run plans again and finishes (#317).
        for name in ("a", "b", "c"):
            make_png(self.output_dir / f"{name}.png", size=(64, 64))

        with patch.object(images_module, "MAX_THUMBNAIL_FILES", 2):
            first = parse_sse(
                await (await self.client.request("POST", self.PROGRESS_URL)).text()
            )
        second = parse_sse(
            await (await self.client.request("POST", self.PROGRESS_URL)).text()
        )

        names = [e for e, _ in first]
        self.assertEqual(names.count("progress"), 3)  # only pending files are work
        self.assertEqual(dict(first)["start"]["total_images"], 3)
        self.assertEqual(
            [d["total_images"] for e, d in first if e == "progress"], [3] * 3
        )
        done = dict(first)["complete"]
        self.assertEqual(done["count"], 3)
        self.assertEqual(done["remaining"], 0)
        self.assertEqual(done["total_images"], 3)
        self.assertNotIn("more", done["message"])
        done = dict(second)["complete"]
        self.assertEqual(done["count"], 0)
        self.assertEqual(done["skipped"], 3)

    async def test_blocking_endpoint_reports_remaining(self):
        for name in ("a", "b", "c"):
            make_png(self.output_dir / f"{name}.png", size=(64, 64))

        with patch.object(images_module, "MAX_THUMBNAIL_FILES", 2):
            resp = await self.client.request(
                "POST", "/prompt_manager/images/generate-thumbnails", json={}
            )
        data = await resp.json()

        self.assertEqual(data["count"], 2)
        self.assertEqual(data["remaining"], 1)
        self.assertEqual(data["total_images"], 3)

    async def test_corrupt_png_is_reported_and_loop_continues(self):
        make_png(self.output_dir / "good1.png")
        (self.output_dir / "bad.png").write_bytes(b"\x89PNG definitely not a png")
        make_png(self.output_dir / "good2.png")

        resp = await self.client.request("POST", self.PROGRESS_URL)

        events = parse_sse(await resp.text())
        file_errors = [d for e, d in events if e == "file_error"]
        self.assertEqual(len(file_errors), 1)
        self.assertEqual(file_errors[0]["file"], "bad.png")
        complete = dict(events)["complete"]
        self.assertEqual(complete["count"], 2)
        self.assertEqual(complete["error_count"], 1)
        self.assertTrue(self._thumb_path("good1").is_file())
        self.assertTrue(self._thumb_path("good2").is_file())
        self.assertFalse(self._thumb_path("bad").exists())

    async def test_response_has_no_wildcard_cors_header(self):
        make_png(self.output_dir / "one.png")

        resp = await self.client.request("POST", self.PROGRESS_URL)
        await resp.text()

        self.assertNotIn("Access-Control-Allow-Origin", resp.headers)

    async def test_existing_newer_thumbnail_is_skipped(self):
        make_png(self.output_dir / "one.png")
        first = await self.client.request("POST", self.PROGRESS_URL)
        await first.text()

        second = await self.client.request("POST", self.PROGRESS_URL)

        complete = dict(parse_sse(await second.text()))["complete"]
        self.assertEqual(complete["skipped"], 1)
        self.assertEqual(complete["count"], 0)

    async def test_empty_output_dir_completes_with_zero(self):
        resp = await self.client.request("POST", self.PROGRESS_URL)

        events = parse_sse(await resp.text())
        self.assertEqual(events[-1][0], "complete")
        self.assertEqual(events[-1][1]["total_images"], 0)

    async def test_missing_output_dir_sends_error_event(self):
        self.api._get_all_output_dirs = lambda: []

        resp = await self.client.request("POST", self.PROGRESS_URL)

        events = parse_sse(await resp.text())
        self.assertEqual(events[-1][0], "error")

    async def test_any_quality_preset_uses_the_server_size_limit(self):
        # #317 R3: the server's thumbnail settings size every preview.
        make_png(self.output_dir / "one.png", size=(1000, 1000))

        resp = await self.client.request("POST", f"{self.PROGRESS_URL}?quality=huge")
        await resp.text()

        with Image.open(self._thumb_path("one")) as thumb:
            self.assertEqual(max(thumb.size), 512)


class TestGenerateThumbnailsPost(ThumbnailTestCase):
    """POST /prompt_manager/images/generate-thumbnails (blocking variant)."""

    async def test_generates_then_skips(self):
        make_png(self.output_dir / "sub" / "x.png")
        make_png(self.output_dir / "y.png")

        first = await self.client.request(
            "POST",
            "/prompt_manager/images/generate-thumbnails",
            json={"quality": "high"},
        )
        second = await self.client.request(
            "POST", "/prompt_manager/images/generate-thumbnails", json={}
        )

        self.assertEqual(first.status, 200)
        data = await first.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["count"], 2)
        self.assertTrue(self._thumb_path("sub/x").is_file())
        data2 = await second.json()
        self.assertEqual(data2["skipped"], 2)
        self.assertEqual(data2["count"], 0)

    async def test_missing_output_dir_is_404(self):
        self.api._get_all_output_dirs = lambda: []

        resp = await self.client.request(
            "POST", "/prompt_manager/images/generate-thumbnails", json={}
        )

        self.assertEqual(resp.status, 404)

    async def test_no_media_reports_zero(self):
        resp = await self.client.request(
            "POST", "/prompt_manager/images/generate-thumbnails", json={}
        )

        data = await resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["total_images"], 0)


class TestThumbnailRoutesHoldTheRegistry(ThumbnailTestCase):
    """Thumbnail work is ``guarded``: never beside a record-changing job."""

    def _run_scan(self):
        gate = asyncio.Event()

        async def runner(job):
            await gate.wait()

        job = self.api._jobs.start(
            "scan", "Scan for metadata", admission="records", scope={}, runner=runner
        )

        async def release():
            gate.set()
            await asyncio.wait_for(job.task, 5)

        self.addAsyncCleanup(release)
        return job

    async def test_thumbnail_generate_refused_while_scan_runs(self):
        make_png(self.output_dir / "a.png")
        self._run_scan()

        resp = await self.client.request(
            "POST", "/prompt_manager/images/generate-thumbnails", json={}
        )
        self.assertEqual(resp.status, 409)
        self.assertEqual(
            await resp.json(),
            {"success": False, "error": "Scan for metadata already running"},
        )

        resp = await self.client.request("POST", self.PROGRESS_URL)
        text = await resp.text()
        self.assertEqual(resp.status, 200)  # the gallery reads an SSE stream
        self.assertEqual(
            text,
            "event: error\ndata: "
            + json.dumps(
                {
                    "error": "Scan for metadata already running",
                    "message": "Scan for metadata already running",
                }
            )
            + "\n\n",
        )

        thumbs = self.output_dir / "thumbnails"
        make_png(thumbs / "b_thumb.png")
        resp = await self.client.request(
            "POST", "/prompt_manager/images/clear-thumbnails"
        )
        self.assertEqual(resp.status, 409)
        self.assertFalse(self._thumb_path("a").exists())
        self.assertTrue((thumbs / "b_thumb.png").exists())

    async def test_scan_refused_while_thumbnails_generate(self):
        make_png(self.output_dir / "a.png")
        entered = threading.Event()
        release = threading.Event()
        real = self.api._generate_gallery_thumbnails_sync

        def gated(*args):
            entered.set()
            release.wait(10)
            return real(*args)

        self.api._generate_gallery_thumbnails_sync = gated
        loop = asyncio.get_running_loop()
        generate = asyncio.ensure_future(
            self.client.request(
                "POST", "/prompt_manager/images/generate-thumbnails", json={}
            )
        )
        self.assertTrue(await loop.run_in_executor(None, entered.wait, 10))
        resp = await self.client.request("POST", "/prompt_manager/jobs/scan")
        body = await resp.json()
        self.assertEqual((resp.status, body["code"]), (409, "busy"))
        self.assertEqual(
            body["running"]["title"], "Thumbnail generation (gallery page)"
        )
        release.set()
        self.assertEqual((await generate).status, 200)
        self.assertTrue(self._thumb_path("a").exists())


class TestRecentImagesCaps(ImageAPITestCase):
    """Every path through /images/recent hands the DB a bounded window."""

    def _spy_db(self):
        seen = []
        original = self.api.db.get_recent_images

        def spy(limit, offset=0):
            seen.append((limit, offset))
            return original(limit, offset)

        self.api.db.get_recent_images = spy
        return seen

    async def test_absent_params_use_bounded_default(self):
        seen = self._spy_db()

        resp = await self.client.request("GET", "/prompt_manager/images/recent")

        data = await resp.json()
        self.assertEqual(data["pagination"]["limit"], 50)
        self.assertEqual(seen, [(50, 0)])

    async def test_huge_offset_is_clamped_before_reaching_db(self):
        seen = self._spy_db()

        resp = await self.client.request(
            "GET", "/prompt_manager/images/recent?limit=10&offset=999999999999"
        )

        self.assertEqual(resp.status, 200)
        data = await resp.json()
        # The offset is paged in SQL, so the DB sees the clamped value itself.
        self.assertEqual(seen[0], (10, data["pagination"]["offset"]))
        self.assertLess(data["pagination"]["offset"], 999999999999)

    async def test_float_string_limit_is_400(self):
        resp = await self.client.request(
            "GET", "/prompt_manager/images/recent?limit=1e9"
        )

        self.assertEqual(resp.status, 400)

    async def test_zero_limit_becomes_one(self):
        seen = self._spy_db()

        await self.client.request("GET", "/prompt_manager/images/recent?limit=0")

        self.assertEqual(seen, [(1, 0)])


class TestAllImagesCaps(ImageAPITestCase):
    """/images/all never asks the DB for an unbounded result."""

    def _spy_db(self):
        seen = []
        original = self.api.db.get_all_images

        def spy(limit=0, offset=0):
            seen.append((limit, offset))
            return original(limit=limit, offset=offset)

        self.api.db.get_all_images = spy
        return seen

    async def test_absent_params_use_bulk_ceiling(self):
        seen = self._spy_db()

        resp = await self.client.request("GET", "/prompt_manager/images/all")

        data = await resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["pagination"]["limit"], images_module.MAX_BULK_LIMIT)
        self.assertEqual(seen, [(images_module.MAX_BULK_LIMIT, 0)])

    async def test_limit_above_bulk_ceiling_is_clamped(self):
        seen = self._spy_db()

        await self.client.request("GET", "/prompt_manager/images/all?limit=99999999")

        self.assertEqual(seen[0][0], images_module.MAX_BULK_LIMIT)

    async def test_negative_offset_is_clamped(self):
        seen = self._spy_db()

        await self.client.request("GET", "/prompt_manager/images/all?offset=-9")

        self.assertEqual(seen[0][1], 0)

    async def test_non_integer_limit_is_400(self):
        resp = await self.client.request("GET", "/prompt_manager/images/all?limit=abc")

        self.assertEqual(resp.status, 400)

    async def test_returns_linked_images(self):
        self._link_image(make_png(self.output_dir / "one.png"))

        resp = await self.client.request("GET", "/prompt_manager/images/all?limit=1")

        data = await resp.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["pagination"]["count"], 1)


class TestOutputImagesCaps(ImageAPITestCase):
    """GET /prompt_manager/images/output clamps paging on every path."""

    URL = "/prompt_manager/images/output"

    async def test_absent_params_use_default_limit(self):
        resp = await self.client.request("GET", self.URL)

        data = await resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["limit"], 100)
        self.assertEqual(data["offset"], 0)

    async def test_limit_is_clamped_to_max_page_limit(self):
        resp = await self.client.request("GET", f"{self.URL}?limit=999999")

        data = await resp.json()
        self.assertEqual(data["limit"], MAX_PAGE_LIMIT)

    async def test_zero_limit_becomes_one(self):
        resp = await self.client.request("GET", f"{self.URL}?limit=0")

        data = await resp.json()
        self.assertEqual(data["limit"], 1)

    async def test_float_string_limit_is_400(self):
        resp = await self.client.request("GET", f"{self.URL}?limit=1e9")

        self.assertEqual(resp.status, 400)
        data = await resp.json()
        self.assertFalse(data["success"])

    async def test_negative_offset_is_clamped(self):
        resp = await self.client.request("GET", f"{self.URL}?offset=-5")

        data = await resp.json()
        self.assertEqual(data["offset"], 0)

    async def test_pages_and_filters_by_subfolder(self):
        make_png(self.output_dir / "sub" / "a.png")
        make_png(self.output_dir / "sub" / "b.png")
        make_png(self.output_dir / "top.png")
        make_png(self.output_dir / "thumbnails" / "top_thumb.png")

        page = await self.client.request("GET", f"{self.URL}?subfolder=sub&limit=1")
        rest = await self.client.request("GET", f"{self.URL}?limit=10")

        data = await page.json()
        self.assertEqual(data["total"], 2)
        self.assertEqual(len(data["images"]), 1)
        self.assertTrue(data["has_more"])
        self.assertEqual(data["images"][0]["root_index"], 0)
        all_data = await rest.json()
        names = sorted(i["filename"] for i in all_data["images"])
        self.assertEqual(names, ["a.png", "b.png", "top.png"])
        top = next(i for i in all_data["images"] if i["filename"] == "top.png")
        self.assertIn("top_thumb.png", top["thumbnail_url"])

    async def test_no_output_dirs_reports_failure(self):
        self.api._get_all_output_dirs = lambda: []

        resp = await self.client.request("GET", self.URL)

        data = await resp.json()
        self.assertFalse(data["success"])


class TestThumbnailFilesAreNeverSeenHalfWritten(ThumbnailTestCase):
    """A browser that lists the gallery mid-run must never fetch a partial file,
    and a regenerated thumbnail must never be served from a stale cache entry."""

    def test_image_thumbnail_write_is_atomic(self):
        import os

        make_png(self.output_dir / "a.png", size=(64, 64))
        thumbs = self.output_dir / "thumbnails"
        thumbs.mkdir()
        dst = thumbs / "a_thumb.webp"
        settings = ThumbnailSettings("webp", 80, 32)

        def crash_mid_write(self_img, fp, *args, **kwargs):
            # Simulates an interrupted encoder: bytes on disk, then failure.
            pathlib.Path(fp).write_bytes(b"\x89PNG partial")
            raise OSError("disk full")

        with patch.object(Image.Image, "save", crash_mid_write):
            with self.assertRaises(OSError):
                images_module._write_image_thumbnail(
                    self.output_dir / "a.png", dst, settings
                )
        self.assertFalse(dst.exists(), "a half-written thumbnail was left in place")
        self.assertEqual(os.listdir(thumbs), [])

        images_module._write_image_thumbnail(self.output_dir / "a.png", dst, settings)
        self.assertTrue(dst.is_file())
        self.assertEqual(os.listdir(thumbs), ["a_thumb.webp"])

    def test_thumbnail_url_is_versioned_by_its_mtime(self):
        import os
        import re

        make_png(self.output_dir / "a.png", size=(64, 64))
        self.api._generate_thumbnails_sync(
            self.output_dir,
            self.output_dir / "thumbnails",
            ThumbnailSettings("webp", 80, 32),
        )
        thumb = self._thumb_path("a")

        entry = images_module._output_image_entry(
            self.output_dir / "a.png", self.output_dir, 0
        )
        self.assertRegex(
            entry["thumbnail_url"],
            r"^/prompt_manager/images/serve/thumbnails/a_thumb\.webp\?v=\d+&root=0$",
        )
        first = int(re.search(r"v=(\d+)", entry["thumbnail_url"]).group(1))
        self.assertEqual(first, int(thumb.stat().st_mtime))

        later = thumb.stat().st_mtime + 10
        os.utime(thumb, (later, later))
        entry = images_module._output_image_entry(
            self.output_dir / "a.png", self.output_dir, 0
        )
        second = int(re.search(r"v=(\d+)", entry["thumbnail_url"]).group(1))
        self.assertEqual(second, first + 10)

    async def test_versioned_thumbnail_url_is_served(self):
        make_png(self.output_dir / "a.png", size=(64, 64))
        self.api._generate_thumbnails_sync(
            self.output_dir,
            self.output_dir / "thumbnails",
            ThumbnailSettings("webp", 80, 32),
        )
        entry = images_module._output_image_entry(
            self.output_dir / "a.png", self.output_dir, 0
        )

        resp = await self.client.request("GET", entry["thumbnail_url"])

        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.headers["Content-Type"], "image/webp")

    async def test_missing_image_404_is_not_cacheable(self):
        resp = await self.client.request(
            "GET", "/prompt_manager/images/serve/thumbnails/nope_thumb.png"
        )
        self.assertEqual(resp.status, 404)
        self.assertEqual(resp.headers.get("Cache-Control"), "no-store")


class TestMediaScanCaps(ImageAPITestCase):
    """Request-triggered filesystem walks are depth- and count-capped."""

    def test_gallery_scan_honours_file_cap(self):
        for name in ("a", "b", "c"):
            make_png(self.output_dir / f"{name}.png")

        with patch.object(images_module, "MAX_GALLERY_FILES", 2):
            found = self.api._scan_gallery_files_sync(self.output_dir)

        self.assertEqual(len(found), 2)

    def test_gallery_scan_honours_depth_cap(self):
        make_png(self.output_dir / "a" / "shallow.png")
        make_png(self.output_dir / "a" / "b" / "deep.png")

        with patch.object(images_module, "MAX_SCAN_DEPTH", 1):
            found = [
                p.name for p, _ in self.api._scan_gallery_files_sync(self.output_dir)
            ]

        self.assertEqual(found, ["shallow.png"])

    def test_gallery_scan_skips_symlinked_directories(self):
        with tempfile.TemporaryDirectory() as other:
            make_png(Path(other) / "outside.png")
            self._make_symlink(other, self.output_dir / "link")

            found = self.api._scan_gallery_files_sync(self.output_dir)

        self.assertEqual(found, [])

    def test_gallery_scan_skips_thumbnails_and_non_media(self):
        make_png(self.output_dir / "keep.png")
        make_png(self.output_dir / "thumbnails" / "keep_thumb.png")
        (self.output_dir / "notes.txt").write_text("x")

        found = [p.name for p, _ in self.api._scan_gallery_files_sync(self.output_dir)]

        self.assertEqual(found, ["keep.png"])

    def test_thumbnail_plan_skips_fresh_thumbnails_before_applying_the_cap(self):
        for name in ("a", "b", "c"):
            make_png(self.output_dir / f"{name}.png")
        self.api._generate_thumbnails_sync(
            self.output_dir,
            self.output_dir / "thumbnails",
            ThumbnailSettings("webp", 80, 64),
        )
        for name in ("d", "e", "f"):
            make_png(self.output_dir / f"{name}.png")

        with patch.object(images_module, "MAX_THUMBNAIL_FILES", 2):
            plan = self.api._plan_thumbnails(
                self.output_dir, self.output_dir / "thumbnails"
            )

        self.assertEqual(plan["skipped"], 3)
        self.assertEqual([t[0].name for t in plan["targets"]], ["d.png", "e.png"])
        self.assertEqual(plan["remaining"], 1)

    def test_thumbnail_walk_is_bounded_like_the_gallery_walk(self):
        for name in ("a", "b", "c"):
            make_png(self.output_dir / f"{name}.png")

        with patch.object(images_module, "MAX_GALLERY_FILES", 2):
            plan = self.api._plan_thumbnails(
                self.output_dir, self.output_dir / "thumbnails"
            )

        self.assertEqual(len(plan["targets"]), 2)
        self.assertEqual(plan["remaining"], 0)

    def test_thumbnail_targets_honour_file_cap(self):
        for name in ("a", "b", "c"):
            make_png(self.output_dir / f"{name}.png")

        with patch.object(images_module, "MAX_THUMBNAIL_FILES", 2):
            targets = self.api._thumbnail_targets(
                self.output_dir, self.output_dir / "thumbnails"
            )

        self.assertEqual(len(targets), 2)

    def test_thumbnail_targets_honour_depth_cap(self):
        make_png(self.output_dir / "a" / "shallow.png")
        make_png(self.output_dir / "a" / "b" / "deep.png")

        with patch.object(images_module, "MAX_SCAN_DEPTH", 1):
            targets = self.api._thumbnail_targets(
                self.output_dir, self.output_dir / "thumbnails"
            )

        self.assertEqual([src.name for src, _, _ in targets], ["shallow.png"])

    def test_thumbnail_targets_skip_symlinked_directories(self):
        with tempfile.TemporaryDirectory() as other:
            make_png(Path(other) / "outside.png")
            self._make_symlink(other, self.output_dir / "link")

            targets = self.api._thumbnail_targets(
                self.output_dir, self.output_dir / "thumbnails"
            )

        self.assertEqual(targets, [])


class TestJsonBodyCaps(ImageAPITestCase):
    """JSON bodies are read in chunks and rejected past MAX_JSON_BODY_BYTES."""

    LINK_URL = "/prompt_manager/images/link"
    THUMBS_URL = "/prompt_manager/images/generate-thumbnails"

    def _oversized(self, cap):
        return b'{"image_path": "' + b"a" * (cap + 16) + b'"}'

    async def test_oversized_content_length_is_413(self):
        with patch.object(images_module, "MAX_JSON_BODY_BYTES", 512):
            resp = await self.client.request(
                "POST",
                self.LINK_URL,
                data=self._oversized(512),
                headers={"Content-Type": "application/json"},
            )

        self.assertEqual(resp.status, 413)
        data = await resp.json()
        self.assertFalse(data["success"])

    async def test_oversized_chunked_body_is_413(self):
        with patch.object(images_module, "MAX_JSON_BODY_BYTES", 512):
            resp = await self.client.request(
                "POST",
                self.THUMBS_URL,
                data=self._oversized(512),
                chunked=True,
                headers={"Content-Type": "application/json"},
            )

        self.assertEqual(resp.status, 413)

    async def test_invalid_json_is_400(self):
        resp = await self.client.request(
            "POST",
            self.LINK_URL,
            data=b"{not json",
            headers={"Content-Type": "application/json"},
        )

        self.assertEqual(resp.status, 400)
        data = await resp.json()
        self.assertFalse(data["success"])

    async def test_non_object_json_is_400(self):
        resp = await self.client.request("POST", self.THUMBS_URL, json=[1, 2, 3])

        self.assertEqual(resp.status, 400)


class TestLinkImage(ImageAPITestCase):
    """POST /prompt_manager/images/link only links media inside allowed dirs."""

    URL = "/prompt_manager/images/link"

    async def test_links_png_inside_output_dir(self):
        prompt_id = self._save_prompt("to link")
        png = make_png(self.output_dir / "gen.png")

        resp = await self.client.request(
            "POST", self.URL, json={"prompt_id": prompt_id, "image_path": str(png)}
        )

        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["success"])
        self.assertGreater(data["image_id"], 0)

    async def test_missing_fields_is_400(self):
        resp = await self.client.request("POST", self.URL, json={"prompt_id": 1})

        self.assertEqual(resp.status, 400)

    async def test_path_outside_allowed_dirs_is_403(self):
        prompt_id = self._save_prompt("to link")
        with tempfile.TemporaryDirectory() as other:
            png = make_png(Path(other) / "outside.png")

            resp = await self.client.request(
                "POST", self.URL, json={"prompt_id": prompt_id, "image_path": str(png)}
            )

        self.assertEqual(resp.status, 403)

    async def test_non_media_path_is_403(self):
        prompt_id = self._save_prompt("to link")
        txt = self.output_dir / "secret.txt"
        txt.write_text("x")

        resp = await self.client.request(
            "POST", self.URL, json={"prompt_id": prompt_id, "image_path": str(txt)}
        )

        self.assertEqual(resp.status, 403)

    async def test_missing_file_is_404(self):
        prompt_id = self._save_prompt("to link")

        resp = await self.client.request(
            "POST",
            self.URL,
            json={
                "prompt_id": prompt_id,
                "image_path": str(self.output_dir / "no.png"),
            },
        )

        self.assertEqual(resp.status, 404)


def _raise(*_args, **_kwargs):
    raise RuntimeError("boom")


class ImageRouteCoverageCase(ImageAPITestCase):
    """Helpers for driving success and failure branches of each route."""

    def setUp(self):
        # #362: the LoraManager layout cache is per process; every test here
        # starts and ends with it empty (runs before the subclasses' setUpAsync).
        super().setUp()
        lora_utils.clear_layout_cache()
        self.addCleanup(lora_utils.clear_layout_cache)

    def _break_db(self, db_method):
        setattr(self.api.db, db_method, _raise)

    async def _json(self, method, path, **kwargs):
        resp = await self.client.request(method, path, **kwargs)
        return resp.status, await resp.json()


class TestPathHelpers(unittest.TestCase):

    def test_has_traversal(self):
        self.assertTrue(images_module._has_traversal(""))
        self.assertTrue(images_module._has_traversal("a/../b.png"))
        self.assertTrue(images_module._has_traversal("\\\\srv\\share\\x.png"))
        self.assertTrue(images_module._has_traversal(os.path.abspath(os.sep)))
        self.assertFalse(images_module._has_traversal("sub/x.png"))


class TestPromptImagesAndSearch(ImageRouteCoverageCase):

    async def test_prompt_images_success_and_nan_cleanup(self):
        png = make_png(self.output_dir / "p.png")
        self._link_image(png, text="owner")
        prompt_id = self.api.db.get_prompt_by_hash(generate_prompt_hash("owner"))["id"]

        status, data = await self._json(
            "GET", f"/prompt_manager/prompts/{prompt_id}/images"
        )

        self.assertEqual(status, 200)
        self.assertEqual(len(data["images"]), 1)
        self.assertTrue(data["images"][0]["url"].endswith("p.png"))

        self.api.db.get_prompt_images = lambda _pid: [
            {"id": 1, "image_path": str(png), "width": float("nan")}
        ]
        status, data = await self._json("GET", "/prompt_manager/prompts/1/images")
        self.assertEqual(status, 200)
        self.assertIsNone(data["images"][0]["width"])

    async def test_prompt_images_db_failure(self):
        self._break_db("get_prompt_images")
        status, data = await self._json("GET", "/prompt_manager/prompts/1/images")
        self.assertEqual(status, 500)
        self.assertFalse(data["success"])

    async def test_search_images_paths(self):
        self._link_image(make_png(self.output_dir / "s.png"), text="sunset beach")
        missing, _ = await self._json("GET", "/prompt_manager/images/search")
        ok, data = await self._json("GET", "/prompt_manager/images/search?q=sunset")
        self._break_db("search_images_by_prompt")
        err, _ = await self._json("GET", "/prompt_manager/images/search?q=x")
        self.assertEqual((missing, ok, err), (400, 200, 500))
        self.assertEqual(data["query"], "sunset")
        self.assertEqual(len(data["images"]), 1)

    async def test_list_routes_db_failures(self):
        self._break_db("get_recent_images")
        self._break_db("get_all_images")
        recent, _ = await self._json("GET", "/prompt_manager/images/recent")
        every, _ = await self._json("GET", "/prompt_manager/images/all")
        self.assertEqual((recent, every), (500, 500))


class TestServeErrorBranches(ImageRouteCoverageCase):

    async def test_serve_by_id_db_failure(self):
        self._break_db("get_image_by_id")
        status, _ = await self._json("GET", "/prompt_manager/images/1/file")
        self.assertEqual(status, 500)

    async def test_serve_output_lookup_failure(self):
        self.api._get_all_output_dirs = _raise
        status, _ = await self._json("GET", "/prompt_manager/images/serve/x.png")
        self.assertEqual(status, 500)

    async def test_out_of_range_root_index_is_rejected(self):
        make_png(self.output_dir / "r.png")
        resp = await self.client.request(
            "GET", "/prompt_manager/images/serve/r.png?root=7"
        )
        self.assertEqual(resp.status, 403)

    async def test_directory_named_like_media_is_404(self):
        (self.output_dir / "dir.png").mkdir()
        status, _ = await self._json("GET", "/prompt_manager/images/serve/dir.png")
        self.assertEqual(status, 404)

    async def test_lora_dirs_extend_allowed_roots(self):
        from py.config import IntegrationConfig

        with (
            tempfile.TemporaryDirectory() as lora,
            tempfile.TemporaryDirectory() as cache,
        ):
            png = make_png(Path(lora) / "preview.png")
            image_id = self._link_image(png)
            # An empty configured path means auto-detection; a developer's
            # own config.json may hold a real LoraManager path, so pin it.
            with (
                patch.object(IntegrationConfig, "LORA_MANAGER_ENABLED", True),
                patch.object(IntegrationConfig, "LORA_MANAGER_PATH", ""),
                patch("py.lora_utils.detect_lora_manager", return_value=None),
                patch("py.lora_utils.default_lora_roots", return_value=[lora]),
                patch("py.lora_utils.get_lora_image_cache_dir", return_value=cache),
            ):
                resp = await self.client.request(
                    "GET", f"/prompt_manager/images/{image_id}/file"
                )
                self.assertEqual(resp.status, 200)

            # The cache does not watch default_lora_roots (#362): without a
            # clear the next request would reuse the folders above.
            lora_utils.clear_layout_cache()
            with (
                patch.object(IntegrationConfig, "LORA_MANAGER_ENABLED", True),
                patch.object(IntegrationConfig, "LORA_MANAGER_PATH", ""),
                patch("py.lora_utils.detect_lora_manager", return_value=None),
                patch("py.lora_utils.default_lora_roots", side_effect=RuntimeError),
            ):
                resp = await self.client.request(
                    "GET", f"/prompt_manager/images/{image_id}/file"
                )
                self.assertEqual(resp.status, 403)


class TestLoraManagerPathAllowList(ImageRouteCoverageCase):
    """A hand-edited LoraManager path must not widen what serve_image reads."""

    def _enter_lora(self, stack, lora, cache, path, resolved):
        from py.config import IntegrationConfig

        stack.enter_context(
            patch.object(IntegrationConfig, "LORA_MANAGER_ENABLED", True)
        )
        stack.enter_context(patch.object(IntegrationConfig, "LORA_MANAGER_PATH", path))
        stack.enter_context(
            patch("py.lora_utils.default_lora_roots", return_value=[lora])
        )
        # Never the developer's LoraManager settings (#355).
        settings = stack.enter_context(tempfile.TemporaryDirectory())
        stack.enter_context(
            patch.dict(os.environ, {"LORA_MANAGER_SETTINGS_DIR": settings})
        )
        stack.enter_context(
            patch("py.lora_utils.get_lora_image_cache_dir", return_value=cache)
        )
        return stack.enter_context(
            patch(
                "py.lora_utils.resolve_lora_manager_path",
                return_value=resolved,
                create=True,
            )
        )

    async def test_configured_path_outside_custom_nodes_is_ignored(self):
        with ExitStack() as stack:
            lora = stack.enter_context(tempfile.TemporaryDirectory())
            cache = stack.enter_context(tempfile.TemporaryDirectory())
            image_id = self._link_image(make_png(Path(lora) / "preview.png"))
            self._enter_lora(stack, lora, cache, lora, None)

            self.assertEqual(self.api._lora_image_dirs(), [])
            resp = await self.client.request(
                "GET", f"/prompt_manager/images/{image_id}/file"
            )
            self.assertEqual(resp.status, 403)

    async def test_configured_custom_nodes_child_is_served(self):
        configured = "custom_nodes/ComfyUI-Lora-Manager"
        with ExitStack() as stack:
            lora = stack.enter_context(tempfile.TemporaryDirectory())
            cache = stack.enter_context(tempfile.TemporaryDirectory())
            image_id = self._link_image(make_png(Path(lora) / "preview.png"))
            resolve = self._enter_lora(stack, lora, cache, configured, lora)

            resp = await self.client.request(
                "GET", f"/prompt_manager/images/{image_id}/file"
            )
            self.assertEqual(resp.status, 200)
            await resp.read()  # release the served file before the directory goes
            resolve.assert_called_once_with(configured)


class TestLoraManagerMediaServing(ImageRouteCoverageCase):
    """#355: LoraManager's sidecar and example-image folders are served while
    the integration is on, only through the folder guard."""

    async def setUpAsync(self):
        await super().setUpAsync()
        from py.config import IntegrationConfig

        self._lm_tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._lm_tmp.cleanup)
        root = Path(os.path.realpath(self._lm_tmp.name))
        custom_nodes = root / "custom_nodes"
        self.lm = custom_nodes / "ComfyUI-Lora-Manager"
        (self.lm / "py").mkdir(parents=True)
        (self.lm / "__init__.py").write_text("")
        self.settings = root / "settings"
        self.settings.mkdir()
        self.sidecars = root / "sidecars"
        self.examples = root / "examples"
        self.cache = root / "cache"
        for folder in (self.sidecars, self.examples, self.cache):
            folder.mkdir()
        self.root = root
        self.write_settings(str(self.sidecars))
        self.enabled = patch.object(IntegrationConfig, "LORA_MANAGER_ENABLED", True)
        self.default_roots = patch("py.lora_utils.default_lora_roots", return_value=[])
        for item in (
            self.enabled,
            patch.object(IntegrationConfig, "LORA_MANAGER_PATH", str(self.lm)),
            patch(
                "py.lora_utils.custom_nodes_directories", return_value=[custom_nodes]
            ),
            self.default_roots,
            patch("py.lora_utils.get_lora_image_cache_dir", return_value=self.cache),
            patch.dict(os.environ, {"LORA_MANAGER_SETTINGS_DIR": str(self.settings)}),
        ):
            item.start()
            self.addCleanup(item.stop)

    def write_settings(self, sidecar_root, examples=None):
        path = self.settings / "settings.json"
        path.write_text(
            json.dumps(
                {
                    "sidecar_storage_mode": "centralized",
                    "sidecar_storage_path": sidecar_root,
                    "example_images_path": str(examples or self.examples),
                    "civitai_api_key": "never-served",
                }
            )
        )
        # Ten seconds old, so the layout cache (real clock here) stores the
        # first load instead of treating the file as being written (#362).
        stamp = time.time_ns() - 10 * 10**9
        os.utime(path, ns=(stamp, stamp))

    async def _status(self, image_id):
        resp = await self.client.request(
            "GET", f"/prompt_manager/images/{image_id}/file"
        )
        await resp.read()  # release the served file before the folder goes
        return resp.status

    async def test_sidecar_and_example_media_are_served_while_enabled(self):
        preview = self.sidecars / "loras-0a1b2c3d" / "neon.webp"
        preview.parent.mkdir()
        preview.write_bytes(b"RIFF")
        example = self.examples / "ab12" / "1.mp4"
        example.parent.mkdir()
        example.write_bytes(b"video")
        ids = [self._link_image(preview, "a"), self._link_image(example, "b")]

        for image_id in ids:
            self.assertEqual(await self._status(image_id), 200)
        # A warm layout cache does not outlive the integration (#362).
        self.assertIsNotNone(lora_utils._layout_cache._entry)
        self.enabled.stop()
        for image_id in ids:
            self.assertEqual(await self._status(image_id), 403)
        self.enabled.start()

    def _example(self, folder=None, name="1.mp4"):
        path = (folder or self.examples) / "ab12" / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"video")
        return path

    async def test_unchanged_settings_reuse_the_cached_layout(self):
        image_id = self._link_image(self._example())
        self.assertEqual(await self._status(image_id), 200)
        self.assertIsNotNone(lora_utils._layout_cache._entry)
        self.assertEqual(await self._status(image_id), 200)
        self.assertEqual(lora_utils.default_lora_roots.call_count, 1)

    async def test_a_settings_change_is_seen_without_a_clear(self):
        image_id = self._link_image(self._example())
        self.assertEqual(await self._status(image_id), 200)
        self.assertIsNotNone(lora_utils._layout_cache._entry)
        moved = self.root / "examples-moved"
        moved.mkdir()
        self.write_settings(str(self.sidecars), examples=moved)
        self.assertEqual(await self._status(image_id), 403)

    async def test_a_cached_folder_swapped_for_a_symlink_to_home_is_refused(self):
        # CUJ 7: the example-images folder becomes a symlink to a folder that
        # holds the home folder; the same recorded file is refused.
        outer = self.root / "outer"
        (outer / "home").mkdir(parents=True)
        self._example(outer)
        image_id = self._link_image(self._example())
        with patch.object(layout_module.Path, "home", return_value=outer / "home"):
            self.assertEqual(await self._status(image_id), 200)
            self.assertIsNotNone(lora_utils._layout_cache._entry)
            # A skip after the rmtree is safe: setUp builds a fresh tree.
            shutil.rmtree(self.examples)
            self._make_symlink(outer, self.examples, target_is_directory=True)
            self.assertEqual(await self._status(image_id), 403)

    async def test_a_configured_path_outside_custom_nodes_adds_nothing(self):
        from py.config import IntegrationConfig

        self.assertEqual(await self._status(self._link_image(self._example())), 200)
        self.assertIsNotNone(lora_utils._layout_cache._entry)
        with patch.object(IntegrationConfig, "LORA_MANAGER_PATH", str(self.sidecars)):
            self.assertEqual(self.api._lora_image_dirs(), [])
        # The warm entry for the real install was never handed out for it.
        self.assertIsNone(lora_utils.cached_lora_manager_layout(str(self.sidecars))[0])

    async def test_serve_image_builds_the_allow_list_in_the_executor(self):
        calls = self._spy_executor()
        self.assertEqual(await self._status(self._link_image(self._example())), 200)
        self.assertIn("_lora_image_dirs", calls)

    async def test_a_filesystem_root_as_sidecar_folder_adds_nothing(self):
        self.write_settings(os.path.abspath(os.sep))
        dirs = self.api._lora_image_dirs()
        # An unusable layout: the default LoRA roots (none here) and the cache.
        self.assertEqual(dirs, [self.cache])

    async def test_the_layout_folders_are_the_served_ones(self):
        dirs = self.api._lora_image_dirs()
        self.assertEqual(
            dirs,
            [
                Path(os.path.normcase(str(self.sidecars))),
                Path(os.path.normcase(str(self.examples))),
                self.cache,
            ],
        )


class TestEnrichMarksVideos(ImageAPITestCase):
    """#355: a linked video outside the output folders is marked as video."""

    async def test_enrich_sets_is_video_and_media_type(self):
        elsewhere = Path(tempfile.gettempdir()) / "lora-media"
        images = [
            {"id": 1, "image_path": str(elsewhere / "a.MP4")},
            {"id": 2, "image_path": str(elsewhere / "b.png")},
            {"id": 3, "image_path": str(elsewhere / "c.mp4"), "is_video": False},
            {"id": 4, "image_path": ""},
        ]
        self.api._enrich_images(images)
        self.assertEqual(
            [(i.get("is_video"), i.get("media_type")) for i in images],
            [(True, "video"), (False, "image"), (False, None), (None, None)],
        )
        self.assertEqual(images[0]["url"], "/prompt_manager/images/1/file")


class TestLinkImageRelativePaths(ImageAPITestCase):
    """POST /images/link accepts the path forms the API hands out."""

    URL = "/prompt_manager/images/link"

    async def setUpAsync(self):
        await super().setUpAsync()
        self.png = make_png(self.output_dir / "sub" / "gen.png")

    async def _link(self, image_path):
        prompt_id = self._save_prompt(f"link {image_path}")
        resp = await self.client.request(
            "POST", self.URL, json={"prompt_id": prompt_id, "image_path": image_path}
        )
        return resp.status, await resp.json()

    async def test_root_relative_and_base_relative_paths_link(self):
        for form in ("sub/gen.png", "output/sub/gen.png"):
            status, data = await self._link(form)

            self.assertEqual(status, 200, form)
            image = self.api.db.get_image_by_id(data["image_id"])
            self.assertEqual(Path(image["image_path"]), self.png, form)

    async def test_relative_traversal_is_forbidden(self):
        make_png(self.comfy_dir / "secret.png")  # removed with the temp tree

        status, _ = await self._link("../secret.png")

        self.assertEqual(status, 403)

    async def test_unknown_relative_path_is_404(self):
        status, _ = await self._link("sub/missing.png")

        self.assertEqual(status, 404)


class TestGalleryListing(ImageRouteCoverageCase):

    async def test_subfolders_with_and_without_ancestors(self):
        make_png(self.output_dir / "a" / "b" / "x.png")
        make_png(self.output_dir / "c" / "y.png")
        plain, data = await self._json("GET", "/prompt_manager/gallery/subfolders")
        _, with_anc = await self._json(
            "GET", "/prompt_manager/gallery/subfolders?include_ancestors=true"
        )
        self.assertEqual(plain, 200)
        self.assertEqual(data["subfolders"], [os.path.join("a", "b"), "c"])
        self.assertIn("a", with_anc["subfolders"])

    async def test_subfolders_and_output_lookup_failures(self):
        self.api._get_all_output_dirs = _raise
        sub, _ = await self._json("GET", "/prompt_manager/gallery/subfolders")
        out, _ = await self._json("GET", "/prompt_manager/images/output")
        self.assertEqual((sub, out), (500, 500))

    async def test_vanished_file_is_dropped_from_page(self):
        png = make_png(self.output_dir / "gone.png")
        await self._json("GET", "/prompt_manager/images/output")
        png.unlink()
        status, data = await self._json("GET", "/prompt_manager/images/output")
        self.assertEqual(status, 200)
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["images"], [])

    def test_gallery_scan_skips_unreadable_entries(self):
        make_png(self.output_dir / "real.png")
        listed = list(images_module._walk_media(self.output_dir, 10))
        ghost = MagicMock(path=str(self.output_dir / "ghost.png"))
        ghost.stat.side_effect = FileNotFoundError("gone")
        with patch.object(
            images_module, "_walk_media", return_value=iter([ghost, *listed])
        ):
            found = self.api._scan_gallery_files_sync(self.output_dir)
        self.assertEqual([path.name for path, _mtime in found], ["real.png"])


class TestThumbnailUnits(ImageRouteCoverageCase):

    SIZE = ThumbnailSettings("webp", 80, 64)

    def test_generate_one_reports_vanished_source(self):
        result = self.api._generate_one(
            self.output_dir / "nope.png", self._dst("nope"), self.SIZE, False
        )
        self.assertEqual(result["action"], "error")
        self.assertIn("no longer exists", result["error"])

    def test_generate_one_video_success_and_failure(self):
        src = self.output_dir / "clip.mp4"
        src.write_bytes(b"\x00")
        with patch.object(self.api, "_generate_video_thumbnail", return_value=True):
            ok = self.api._generate_one(src, self._dst("clip"), self.SIZE, True)
        with patch.object(self.api, "_generate_video_thumbnail", return_value=False):
            bad = self.api._generate_one(src, self._dst("clip"), self.SIZE, True)
        self.assertEqual((ok["action"], ok["type"]), ("generated", "video"))
        self.assertEqual(bad["action"], "error")

    def test_generate_one_unexpected_error_is_safe(self):
        src = make_png(self.output_dir / "boom.png")
        with patch.object(
            images_module, "_write_image_thumbnail", side_effect=RuntimeError("bad")
        ):
            result = self.api._generate_one(src, self._dst("boom"), self.SIZE, False)
        self.assertEqual(result["action"], "error")
        self.assertEqual(result["error"], "bad")

    def test_rgba_and_jpeg_sources(self):
        with Image.new("RGBA", (32, 32), (0, 255, 0, 128)) as img:
            img.save(self.output_dir / "alpha.png")
        with Image.new("RGB", (32, 32), (0, 0, 255)) as img:
            img.save(self.output_dir / "photo.jpg", "JPEG")
        result = self.api._generate_thumbnails_sync(
            self.output_dir, self.output_dir / "thumbnails", self.SIZE
        )
        self.assertEqual(result["count"], 2)
        self.assertTrue(self._dst("alpha").is_file())
        self.assertTrue(self._dst("photo").is_file())

    async def test_emit_sse_swallows_write_errors(self):
        response = MagicMock()
        response.write = AsyncMock(side_effect=ConnectionResetError("gone"))
        await self.api._emit_sse(response, "progress", {"n": 1})
        response.write.assert_awaited_once()

    async def test_progress_stream_reports_internal_failure(self):
        self.api._plan_thumbnails = _raise
        resp = await self.client.request(
            "POST", "/prompt_manager/images/generate-thumbnails/progress"
        )
        events = parse_sse(await resp.text())
        self.assertEqual(events[-1][0], "error")

    async def test_post_generate_reports_internal_failure(self):
        self.api._get_all_output_dirs = _raise
        status, _ = await self._json(
            "POST", "/prompt_manager/images/generate-thumbnails", json={}
        )
        self.assertEqual(status, 500)

    def _dst(self, name, suffix=".webp"):
        return self.output_dir / "thumbnails" / f"{name}_thumb{suffix}"


class TestVideoThumbnailFallbacks(ImageRouteCoverageCase):
    """_generate_video_thumbnail degrades: cv2 -> ffmpeg -> placeholder."""

    # JPEG keeps these checks format-specific; #317 settings carry the edge.
    SIZE = ThumbnailSettings("jpeg", 85, 48)

    @property
    def video(self):
        # output_dir only exists once the app is built, so create lazily.
        path = self.output_dir / "clip.mp4"
        if not path.exists():
            path.write_bytes(b"\x00\x00")
        return path

    @property
    def thumb(self):
        return self.output_dir / "clip_thumb.jpg"

    def _fake_cv2(self, opened=True, read_ok=True):
        numpy = self._numpy()
        cv2 = MagicMock()
        cap = cv2.VideoCapture.return_value
        cap.isOpened.return_value = opened
        cap.get.return_value = 30
        frame = numpy.zeros((8, 8, 3), dtype=numpy.uint8)
        cap.read.return_value = (read_ok, frame if read_ok else None)
        cv2.cvtColor.side_effect = lambda f, _code: f
        return cv2

    def _numpy(self):
        try:
            import numpy
        except ImportError:
            self.skipTest("numpy not installed")
        return numpy

    def test_cv2_success(self):
        with patch.dict(sys.modules, {"cv2": self._fake_cv2()}):
            ok = self.api._generate_video_thumbnail(self.video, self.thumb, self.SIZE)
        self.assertTrue(ok)
        with Image.open(self.thumb) as img:
            self.assertEqual(img.format, "JPEG")

    def test_cv2_cannot_open_or_read_falls_through_to_ffmpeg_then_placeholder(self):
        # #249 I1: an OpenCV that cannot decode the clip (AV1) no longer ends
        # the run; ffmpeg and then the placeholder are tried.
        for fake in (self._fake_cv2(opened=False), self._fake_cv2(read_ok=False)):
            self.thumb.unlink(missing_ok=True)
            with (
                patch.dict(sys.modules, {"cv2": fake}),
                patch("subprocess.Popen", side_effect=FileNotFoundError("ffmpeg")),
            ):
                ok = self.api._generate_video_thumbnail(
                    self.video, self.thumb, self.SIZE
                )
            self.assertTrue(ok)
            with Image.open(self.thumb) as img:
                self.assertEqual((img.format, img.size), ("JPEG", self.SIZE.size))

    def test_cv2_crash_is_contained(self):
        cv2 = MagicMock()
        cv2.VideoCapture.side_effect = RuntimeError("driver crash")
        with (
            patch.dict(sys.modules, {"cv2": cv2}),
            patch("subprocess.Popen", side_effect=FileNotFoundError("ffmpeg")),
        ):
            ok = self.api._generate_video_thumbnail(self.video, self.thumb, self.SIZE)
        self.assertTrue(ok, "the placeholder is written")

    def test_cv2_failure_keeps_an_existing_preview_on_rebuild(self):
        make_png(self.thumb)
        before = self.thumb.read_bytes()
        with (
            patch.dict(sys.modules, {"cv2": self._fake_cv2(opened=False)}),
            patch("subprocess.Popen", side_effect=FileNotFoundError("ffmpeg")),
        ):
            ok = self.api._generate_video_thumbnail(
                self.video, self.thumb, self.SIZE, keep_existing=True
            )
        self.assertFalse(ok)
        self.assertEqual(self.thumb.read_bytes(), before)

    def test_ffmpeg_absent_yields_placeholder(self):
        with (
            patch.dict(sys.modules, {"cv2": None}),
            patch("subprocess.Popen", side_effect=FileNotFoundError("ffmpeg")),
        ):
            ok = self.api._generate_video_thumbnail(self.video, self.thumb, self.SIZE)
        self.assertTrue(ok)
        with Image.open(self.thumb) as img:
            self.assertEqual(img.size, self.SIZE.size)

    @staticmethod
    def _process(returncode, stderr=b"", stdout=b""):
        process = MagicMock(returncode=returncode)
        process.communicate.return_value = (stdout, stderr)
        return process

    @staticmethod
    def _png_frame(size=(96, 64)):
        import io

        buffer = io.BytesIO()
        Image.new("RGB", size, (200, 0, 0)).save(buffer, "PNG")
        return buffer.getvalue()

    def test_ffmpeg_failure_yields_placeholder(self):
        with (
            patch.dict(sys.modules, {"cv2": None}),
            patch("subprocess.Popen", return_value=self._process(1, b"no stream")),
        ):
            ok = self.api._generate_video_thumbnail(self.video, self.thumb, self.SIZE)
        self.assertTrue(ok)
        self.assertTrue(self.thumb.is_file())

    def test_ffmpeg_success(self):
        with (
            patch.dict(sys.modules, {"cv2": None}),
            patch(
                "subprocess.Popen",
                return_value=self._process(0, stdout=self._png_frame()),
            ) as run,
        ):
            ok = self.api._generate_video_thumbnail(self.video, self.thumb, self.SIZE)
        self.assertTrue(ok)
        self.assertEqual(run.call_args.args[0][0], "ffmpeg")
        with Image.open(self.thumb) as img:
            # ffmpeg's frame (a PNG on stdout) fitted inside the edge (#317).
            self.assertEqual((img.format, img.size), ("JPEG", (48, 32)))

    def test_ffmpeg_is_tracked_and_a_hold_before_the_rename_writes_nothing(self):
        tracked = []

        def writing(cmd, **kwargs):
            return self._process(0, stdout=self._png_frame())

        with (
            patch.dict(sys.modules, {"cv2": None}),
            patch("subprocess.Popen", side_effect=writing),
        ):
            with self.assertRaises(images_module.PreviewWriteHeld):
                self.api._generate_video_thumbnail(
                    self.video,
                    self.thumb,
                    self.SIZE,
                    guard=lambda: False,
                    track=tracked.append,
                )
        self.assertEqual(len(tracked), 1)
        self.assertFalse(self.thumb.exists())
        self.assertEqual(
            [p.name for p in self.thumb.parent.iterdir() if "part" in p.name], []
        )

    def test_placeholder_failure_returns_false(self):
        with (
            patch.dict(sys.modules, {"cv2": None}),
            patch("subprocess.Popen", side_effect=FileNotFoundError),
            patch.object(
                images_module.Image, "new", side_effect=RuntimeError("no PIL")
            ),
        ):
            ok = self.api._generate_video_thumbnail(self.video, self.thumb, self.SIZE)
        self.assertFalse(ok)


class TestClearThumbnails(ImageRouteCoverageCase):

    URL = "/prompt_manager/images/clear-thumbnails"

    async def test_missing_output_dir_is_404(self):
        self.api._get_all_output_dirs = lambda: []
        status, _ = await self._json("POST", self.URL)
        self.assertEqual(status, 404)

    async def test_no_thumbnails_dir_is_noop(self):
        status, data = await self._json("POST", self.URL)
        self.assertEqual(status, 200)
        self.assertEqual(data["cleared_files"], 0)

    async def test_clears_only_generated_thumbnails(self):
        thumbs = self.output_dir / "thumbnails"
        make_png(thumbs / "sub" / "a_thumb.png")
        make_png(thumbs / "b_thumb.png")
        (thumbs / "readme.txt").write_text("keep")
        make_png(self.output_dir / "original.png")

        status, data = await self._json("POST", self.URL)

        self.assertEqual(status, 200)
        self.assertEqual(data["cleared_files"], 2)
        self.assertIn("cleared_size_formatted", data)
        self.assertFalse((thumbs / "sub").exists())
        self.assertTrue((thumbs / "readme.txt").is_file())
        self.assertTrue((self.output_dir / "original.png").is_file())

    async def test_unlink_failure_is_logged_not_raised(self):
        make_png(self.output_dir / "thumbnails" / "a_thumb.png")
        with patch.object(Path, "unlink", side_effect=PermissionError("locked")):
            status, data = await self._json("POST", self.URL)
        self.assertEqual(status, 200)
        self.assertEqual(data["cleared_files"], 0)

    async def test_internal_failure_is_500(self):
        self.api._get_all_output_dirs = _raise
        status, _ = await self._json("POST", self.URL)
        self.assertEqual(status, 500)

    async def test_sibling_prefix_directory_is_refused(self):
        # "<base>/out2/thumbnails" starts with "<base>/out" as a string but is
        # not inside it; a symlinked thumbnails dir pointing there must be
        # refused rather than emptied.
        out = self.comfy_dir / "out"
        out.mkdir()
        sibling = make_png(self.comfy_dir / "out2" / "thumbnails" / "a_thumb.png")
        self._make_symlink(sibling.parent, out / "thumbnails")
        self.api._get_all_output_dirs = lambda: [out]

        status, data = await self._json("POST", self.URL)

        self.assertEqual(status, 400)
        self.assertFalse(data["success"])
        self.assertTrue(sibling.is_file())

    async def test_clears_all_roots_and_combines_counts_without_touching_originals(
        self,
    ):
        second = self.comfy_dir / "extra-output"
        roots = [self.output_dir, second]
        self.api._get_all_output_dirs = lambda: roots
        expected_size = 0
        for root in roots:
            make_png(root / "original_thumb.png")
            thumb = make_png(root / "thumbnails" / "sub" / "generated_thumb.png")
            expected_size += thumb.stat().st_size
            (root / "thumbnails" / "notes.txt").write_text("keep")
        status, data = await self._json("POST", self.URL)
        self.assertEqual(status, 200)
        self.assertEqual(data["cleared_files"], 2)
        self.assertEqual(data["cleared_size"], expected_size)
        self.assertNotIn("{format_size", data["message"])
        for root in roots:
            self.assertTrue((root / "original_thumb.png").is_file())
            self.assertTrue((root / "thumbnails" / "notes.txt").is_file())
            self.assertFalse((root / "thumbnails" / "sub").exists())

    async def test_missing_thumbnails_in_first_root_does_not_skip_later_roots(self):
        second = self.comfy_dir / "extra-output"
        thumb = make_png(second / "thumbnails" / "a_thumb.png")
        self.api._get_all_output_dirs = lambda: [self.output_dir, second]
        status, data = await self._json("POST", self.URL)
        self.assertEqual(status, 200)
        self.assertEqual(data["cleared_files"], 1)
        self.assertFalse(thumb.exists())

    async def test_invalid_later_root_is_rejected_before_deleting_any_files(self):
        first_thumb = make_png(self.output_dir / "thumbnails" / "a_thumb.png")
        second = self.comfy_dir / "extra-output"
        second.mkdir()
        outside = make_png(self.comfy_dir / "elsewhere" / "a_thumb.png")
        self._make_symlink(outside.parent, second / "thumbnails")
        self.api._get_all_output_dirs = lambda: [self.output_dir, second]
        status, data = await self._json("POST", self.URL)
        self.assertEqual(status, 400)
        self.assertFalse(data["success"])
        self.assertTrue(first_thumb.is_file())
        self.assertTrue(outside.is_file())

    async def test_thumbnail_symlink_back_to_root_cannot_delete_originals(self):
        original = make_png(self.output_dir / "original_thumb.png")
        self._make_symlink(self.output_dir, self.output_dir / "thumbnails")
        status, data = await self._json("POST", self.URL)
        self.assertEqual(status, 400)
        self.assertFalse(data["success"])
        self.assertTrue(original.is_file())

    async def test_cleanup_runs_off_loop_and_keeps_the_listing(self):
        import threading

        make_png(self.output_dir / "thumbnails" / "a_thumb.png")
        make_png(self.output_dir / "a.png")
        await self._json("GET", "/prompt_manager/images/output")
        listing = self.api._root_listings[str(self.output_dir)].files
        loop_thread = threading.get_ident()
        deletion_threads = []
        original_unlink = Path.unlink

        def unlink(path, *args, **kwargs):
            deletion_threads.append(threading.get_ident())
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", unlink):
            status, data = await self._json("POST", self.URL)
        self.assertEqual(status, 200)
        self.assertEqual(data["cleared_files"], 1)
        self.assertTrue(deletion_threads)
        self.assertNotIn(loop_thread, deletion_threads)
        # Previews are not part of the listing (#348): it stays as it was.
        self.assertIs(self.api._root_listings[str(self.output_dir)].files, listing)


class TestImagePromptLookup(ImageRouteCoverageCase):

    URL = "/prompt_manager/images/prompt/"

    async def test_relative_path_resolves_against_output_dir(self):
        self._link_image(make_png(self.output_dir / "gen.png"), text="the prompt")
        status, data = await self._json("GET", f"{self.URL}gen.png")
        self.assertEqual(status, 200)
        self.assertTrue(data["success"])
        self.assertEqual(data["prompt"]["text"], "the prompt")
        self.assertEqual(data["prompt"]["image_path"], "output/gen.png")

    async def test_unknown_image_reports_no_prompt(self):
        status, data = await self._json("GET", f"{self.URL}unknown.png")
        self.assertEqual(status, 200)
        self.assertFalse(data["success"])
        self.assertIn("image_path", data)

    async def test_empty_path_is_400(self):
        status, _ = await self._json("GET", self.URL)
        self.assertEqual(status, 400)

    async def test_db_failure_is_500_without_details(self):
        self._break_db("get_image_prompt_info")
        status, data = await self._json("GET", f"{self.URL}x.png")
        self.assertEqual(status, 500)
        self.assertEqual(data["error"], "Database error occurred")

    async def test_output_dir_lookup_failure_is_500(self):
        self.api._find_comfyui_output_dir = _raise
        status, _ = await self._json("GET", f"{self.URL}x.png")
        self.assertEqual(status, 500)


class TestPublicPathsInResponses(ImageAPITestCase):
    """Image routes describe files relative to the ComfyUI tree, never absolutely."""

    async def setUpAsync(self):
        await super().setUpAsync()
        self.png = make_png(self.output_dir / "sub" / "gen.png")
        self.prompt_id = self._save_prompt("public paths")
        self.assertGreater(
            self.api.db.link_image_to_prompt(self.prompt_id, str(self.png)), 0
        )

    async def _body(self, method, path, **kwargs):
        resp = await self.client.request(method, path, **kwargs)
        text = await resp.text()
        self.assertNotIn(str(self.comfy_dir), text, path)
        return resp.status, json.loads(text)

    async def test_output_listing_paths_are_root_relative(self):
        status, data = await self._body("GET", "/prompt_manager/images/output")

        self.assertEqual(status, 200)
        entry = data["images"][0]
        self.assertEqual(entry["path"], "sub/gen.png")
        self.assertEqual(entry["root_dir"], "output")
        self.assertEqual(entry["root_index"], 0)

    async def test_image_lists_publish_image_path_and_drop_file_path(self):
        routes = (
            f"/prompt_manager/prompts/{self.prompt_id}/images",
            "/prompt_manager/images/recent",
            "/prompt_manager/images/all",
            "/prompt_manager/images/search?q=public",
        )
        for route in routes:
            status, data = await self._body("GET", route)

            self.assertEqual(status, 200, route)
            image = data["images"][0]
            self.assertEqual(image["image_path"], "output/sub/gen.png", route)
            self.assertNotIn("file_path", image, route)
            self.assertTrue(image["url"].endswith("sub/gen.png"), route)

    async def test_image_prompt_lookup_publishes_path(self):
        _, found = await self._body("GET", "/prompt_manager/images/prompt/sub/gen.png")
        _, missing = await self._body("GET", "/prompt_manager/images/prompt/sub/no.png")

        self.assertEqual(found["prompt"]["image_path"], "output/sub/gen.png")
        self.assertEqual(missing["image_path"], "output/sub/no.png")

    async def test_thumbnails_path_is_public(self):
        status, data = await self._body(
            "POST", "/prompt_manager/images/generate-thumbnails", json={}
        )

        self.assertEqual(status, 200)
        self.assertEqual(data["thumbnails_path"], "output/thumbnails")

    async def test_oserror_bodies_keep_only_the_basename(self):
        secret = str(self.comfy_dir / "hidden.png")

        def boom(*_args, **_kwargs):
            raise FileNotFoundError(2, "No such file", secret)

        self.api._get_all_output_dirs = boom
        self.api._find_comfyui_output_dir = boom
        self.api.db.get_prompt_images = boom
        self.api.db.delete_image = boom
        routes = (
            ("GET", "/prompt_manager/gallery/subfolders"),
            ("GET", "/prompt_manager/prompts/1/images"),
            ("DELETE", "/prompt_manager/images/1"),
            ("POST", "/prompt_manager/images/clear-thumbnails"),
            ("GET", "/prompt_manager/images/prompt/x.png"),
        )
        for method, route in routes:
            status, data = await self._body(method, route)

            self.assertEqual(status, 500, route)
            self.assertIn("hidden.png", data["error"], route)


class TestDeleteAndLinkErrors(ImageRouteCoverageCase):

    async def test_delete_image_paths(self):
        image_id = self._link_image(make_png(self.output_dir / "d.png"))
        ok, _ = await self._json("DELETE", f"/prompt_manager/images/{image_id}")
        gone, _ = await self._json("DELETE", f"/prompt_manager/images/{image_id}")
        bad, _ = await self._json("DELETE", "/prompt_manager/images/abc")
        self._break_db("delete_image")
        err, _ = await self._json("DELETE", "/prompt_manager/images/1")
        self.assertEqual((ok, gone, bad, err), (200, 404, 400, 500))

    async def test_link_db_failure_is_500(self):
        prompt_id = self._save_prompt("to link")
        png = make_png(self.output_dir / "l.png")
        self._break_db("link_image_to_prompt")
        status, _ = await self._json(
            "POST",
            "/prompt_manager/images/link",
            json={"prompt_id": prompt_id, "image_path": str(png)},
        )
        self.assertEqual(status, 500)


if __name__ == "__main__":
    unittest.main()


class TestGalleryRescan(ImageAPITestCase):
    """POST /prompt_manager/gallery/rescan drops the listing cache and rewalks."""

    URL = "/prompt_manager/images/output"

    async def _json(self, method, path, **kwargs):
        resp = await self.client.request(method, path, **kwargs)
        return resp.status, await resp.json()

    async def test_rescan_makes_new_files_visible_before_the_cache_expires(self):
        # The listing alone would hide the new file for an hour.
        refresh = patch.object(gallery_listing, "REFRESH_AFTER", 3600)
        refresh.start()
        self.addCleanup(refresh.stop)
        make_png(self.output_dir / "a.png")
        _, first = await self._json("GET", self.URL)
        self.assertEqual(first["total"], 1)

        make_png(self.output_dir / "b.png")
        _, stale = await self._json("GET", self.URL)
        self.assertEqual(stale["total"], 1)

        status, data = await self._json("POST", "/prompt_manager/gallery/rescan")
        _, fresh = await self._json("GET", self.URL)

        self.assertEqual(status, 200)
        self.assertTrue(data["success"])
        self.assertEqual(data["total"], 2)
        self.assertEqual(data["roots"], 1)
        self.assertEqual(fresh["total"], 2)

    async def test_rescan_is_post_only(self):
        resp = await self.client.request("GET", "/prompt_manager/gallery/rescan")
        self.assertEqual(resp.status, 405)

    async def test_rescan_reports_internal_failure(self):
        self.api._scan_gallery_files_sync = _raise
        status, data = await self._json("POST", "/prompt_manager/gallery/rescan")
        self.assertEqual(status, 500)
        self.assertFalse(data["success"])
        self.assertNotIn("boom", json.dumps(data))


class TestOutputScanHelpers(ImageAPITestCase):
    """Stable ids for filesystem entries and no symlink reads.

    The thumbnail stream no longer stops when its client goes away: see
    ``tests/test_thumbnail_jobs.py`` (the work is a job that outlives it).
    """

    def test_output_entry_id_is_a_stable_digest_of_the_path(self):
        import hashlib

        output = Path(self.output_dir)
        media = output / "stable.png"
        make_png(media)
        entry = images_module._output_image_entry(media, output, 0)
        expected = hashlib.sha1(str(media).encode("utf-8")).hexdigest()[:16]
        self.assertEqual(entry["id"], expected)

    def test_media_walk_matches_the_os_walk_reference(self):
        # The scandir walk (#348) must list exactly what the os.walk version
        # listed, in the same order: ties in mtime keep this order.
        output = Path(self.output_dir)
        for rel in (
            "z.png",
            "a.PNG",
            "notes.txt",
            "b/2.png",
            "b/1.webp",
            "b/c/deep.mp4",
            "b/c/d/too_deep.png",
            "thumbnails/t.png",
            "b/thumbnails/u.png",
            "e/.hidden.png",
            "e/f.jpeg",
            "e/..png",
            "e/...webp",
            "e/g.",
        ):
            make_png(output / rel)
        try:
            os.symlink(output / "z.png", output / "b" / "link.png")
            os.symlink(output / "b", output / "dirlink", target_is_directory=True)
        except (OSError, NotImplementedError):
            pass  # the rest of the tree still checks order and filters
        with patch.object(images_module, "MAX_SCAN_DEPTH", 2):
            listed = list(images_module._iter_media_files(output, 100))
            expected = list(_reference_iter_media_files(output, 100))
            capped = list(images_module._iter_media_files(output, 3))
        self.assertEqual(listed, expected)
        self.assertEqual(capped, expected[:3])
        # Path.suffix of Python 3.10-3.13, which CI and hosts run (3.14 changed
        # it): "..png" has the suffix ".png", ".png" and "g." have none.
        names = {path.name for path in listed}
        self.assertTrue({"..png", "...webp", ".hidden.png"} <= names, names)
        self.assertFalse({"g.", "notes.txt"} & names, names)
        self.assertTrue(all(isinstance(path, Path) for path in listed))

    def test_gallery_scan_reads_each_file_type_and_mtime_from_scandir(self):
        # Path.stat and Path.is_symlink per file were the other half of the
        # live walk's cost (#348); the scandir entries already carry both.
        output = Path(self.output_dir)
        make_png(output / "a.png")
        make_png(output / "sub" / "b.png")
        with (
            patch.object(Path, "stat", side_effect=AssertionError),
            patch.object(Path, "is_symlink", side_effect=AssertionError),
        ):
            found = self.api._scan_gallery_files_sync(output)
        self.assertEqual(
            sorted(path.name for path, _mtime in found), ["a.png", "b.png"]
        )
        for path, mtime in found:
            self.assertEqual(mtime, os.stat(path).st_mtime)

    def test_favorite_listing_slices_the_path_strings(self):
        # relative_to per file was the largest cold cost of the first open.
        output = Path(self.output_dir)
        files = [
            (output / "a.png", 1.0),
            (output / "Sub" / "B.png", 2.0),
            (output / "sub" / "deep" / "c.webp", 3.0),
        ]
        expected = {}
        for path, _mtime in files:
            posix = path.relative_to(output).as_posix()
            expected.setdefault(images_module._favorite_relative_key(posix), posix)
        with patch.object(PurePath, "relative_to", side_effect=AssertionError):
            listing = self.api._favorite_listing(output, files)
        self.assertEqual(listing, expected)

    def test_iter_media_files_skips_symlinked_files(self):
        output = Path(self.output_dir)
        make_png(output / "real.png")
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other) / "outside.png"
            make_png(outside)
            try:
                os.symlink(outside, output / "link.png")
            except (OSError, NotImplementedError):
                self.skipTest("symlinks not available")
            names = sorted(p.name for p in images_module._iter_media_files(output, 100))
        self.assertEqual(names, ["real.png"])


class TestThumbnailStartupStatus(ThumbnailTestCase):
    """Startup counts and generation cover the same configured collection."""

    STATUS_URL = "/prompt_manager/images/thumbnails/status"

    async def test_status_reports_exact_pending_count_without_generating(self):
        for name in ("ready", "missing", "outdated"):
            make_png(self.output_dir / f"{name}.png")
        thumbs = self.output_dir / "thumbnails"
        ready = make_png(thumbs / "ready_thumb.png")
        outdated = make_png(thumbs / "outdated_thumb.png")
        source_time = (self.output_dir / "ready.png").stat().st_mtime
        os.utime(ready, (source_time + 10, source_time + 10))
        os.utime(outdated, (source_time - 10, source_time - 10))
        calls = self._spy_executor()

        response = await self.client.get(self.STATUS_URL)

        self.assertEqual(response.status, 200)
        data = await response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["total_images"], 3)
        self.assertEqual(data["pending"], 2)
        self.assertEqual(data["ready"], 1)
        self.assertTrue(data["collection_id"])
        self.assertIn("_plan_gallery_thumbnails", calls)
        self.assertFalse((thumbs / "missing_thumb.png").exists())

    async def test_both_generation_routes_cover_all_gallery_roots(self):
        second = self.comfy_dir / "another-output"
        make_png(self.output_dir / "a.png")
        make_png(second / "b.png")
        self.api._get_all_output_dirs = lambda: [self.output_dir, second]
        for route in (
            "/prompt_manager/images/generate-thumbnails",
            self.PROGRESS_URL,
        ):
            with self.subTest(route=route):
                for root in (self.output_dir, second):
                    for thumb in (root / "thumbnails").glob("*_thumb.webp"):
                        thumb.unlink()
                response = await self.client.post(route, json={})
                if route == self.PROGRESS_URL:
                    data = dict(parse_sse(await response.text()))["complete"]
                else:
                    data = await response.json()
                self.assertEqual(data["count"], 2)
                self.assertTrue((second / "thumbnails" / "b_thumb.webp").exists())
                status = await (await self.client.get(self.STATUS_URL)).json()
                self.assertEqual(status["pending"], 0)
                self.assertEqual(status["total_images"], 2)

    async def test_pending_count_includes_files_beyond_the_generation_cap(self):
        for name in ("a", "b", "c"):
            make_png(self.output_dir / f"{name}.png")
        with patch.object(images_module, "MAX_THUMBNAIL_FILES", 1):
            response = await self.client.get(self.STATUS_URL)
            self.assertEqual(response.status, 200)
            data = await response.json()
        self.assertEqual(data["pending"], 3)

    async def test_generation_cap_is_shared_across_roots(self):
        second = self.comfy_dir / "another-output"
        make_png(self.output_dir / "a.png")
        make_png(second / "b.png")
        self.api._get_all_output_dirs = lambda: [self.output_dir, second]
        with patch.object(images_module, "MAX_THUMBNAIL_FILES", 1):
            plan = self.api._plan_gallery_thumbnails([self.output_dir, second])
            response = await self.client.post(self.PROGRESS_URL)
            data = dict(parse_sse(await response.text()))["complete"]
        # One batch holds one file across both roots; the run goes on (#317).
        self.assertEqual((len(plan["targets"]), plan["remaining"]), (1, 1))
        self.assertEqual(data["count"], 2)
        self.assertEqual(data["remaining"], 0)
        self.assertEqual(data["total_images"], 2)

    async def test_equal_timestamps_do_not_regenerate_existing_thumbnails(self):
        source = make_png(self.output_dir / "ready.png")
        thumb = make_png(self.output_dir / "thumbnails" / "ready_thumb.png")
        timestamp = int(source.stat().st_mtime)
        os.utime(source, (timestamp, timestamp))
        os.utime(thumb, (timestamp, timestamp))
        response = await self.client.post(self.PROGRESS_URL)
        data = dict(parse_sse(await response.text()))["complete"]
        self.assertEqual(data["count"], 0)
        self.assertEqual(data["skipped"], 1)


class TestGalleryRootIdentity(ThumbnailTestCase):
    async def test_colliding_original_and_thumbnail_urls_serve_their_own_roots(self):
        roots = [self.output_dir, self.comfy_dir / "second-output"]
        for index, root in enumerate(roots):
            make_png(root / "same.png", color=(index * 255, 0, 255 - index * 255))
        self.api._get_all_output_dirs = lambda: roots
        await self.client.post("/prompt_manager/images/generate-thumbnails", json={})
        data = await (await self.client.get("/prompt_manager/images/output")).json()
        self.assertEqual(len({image["url"] for image in data["images"]}), 2)
        self.assertEqual(len({image["thumbnail_url"] for image in data["images"]}), 2)
        for image in data["images"]:
            root = roots[image["root_index"]]
            for key, path in (
                ("url", root / "same.png"),
                ("thumbnail_url", root / "thumbnails" / "same_thumb.webp"),
            ):
                response = await self.client.get(image[key])
                self.assertEqual(response.status, 200)
                self.assertEqual(await response.read(), path.read_bytes())

    async def test_root_scoped_prompt_lookup_uses_the_matching_database_image(self):
        roots = [self.output_dir, self.comfy_dir / "second-output"]
        for index, root in enumerate(roots):
            self._link_image(make_png(root / "same.png"), text=f"root {index} prompt")
        self.api._get_all_output_dirs = lambda: roots
        for index in range(2):
            response = await self.client.get(
                f"/prompt_manager/images/prompt/same.png?root={index}"
            )
            self.assertEqual(response.status, 200)
            data = await response.json()
            self.assertEqual(data["prompt"]["text"], f"root {index} prompt")

    async def test_special_characters_in_paths_survive_serving_and_prompt_lookup(self):
        from urllib.parse import quote

        filenames = ["space #%20 café.png"]
        if os.name != "nt":
            filenames.append("space #?%20 café.png")
        for filename in filenames:
            self._link_image(make_png(self.output_dir / filename), text="special path")
        for filename in filenames:
            with self.subTest(filename=filename):
                original = self.output_dir / filename
                data = await (
                    await self.client.get("/prompt_manager/images/output")
                ).json()
                entry = next(
                    image for image in data["images"] if image["filename"] == filename
                )
                response = await self.client.get(entry["url"])
                self.assertEqual(response.status, 200)
                self.assertEqual(await response.read(), original.read_bytes())
                response = await self.client.get(
                    f"/prompt_manager/images/prompt/{quote(filename, safe='')}?root=0"
                )
                data = await response.json()
                self.assertEqual(data["prompt"]["text"], "special path")

    async def test_invalid_explicit_roots_never_fall_back_to_another_image(self):
        self._link_image(make_png(self.output_dir / "same.png"), text="first root")
        for root in ("bad", "-1", "7", ""):
            for route in ("serve", "prompt"):
                with self.subTest(root=root, route=route):
                    response = await self.client.get(
                        f"/prompt_manager/images/{route}/same.png?root={root}"
                    )
                    self.assertEqual(response.status, 403)

    async def test_explicit_root_prompt_lookup_rejects_paths_outside_that_root(self):
        from urllib.parse import quote

        outside = make_png(self.comfy_dir / "outside.png")
        self._link_image(outside, text="outside")
        response = await self.client.get(
            f"/prompt_manager/images/prompt/{quote(str(outside), safe='')}?root=0"
        )
        self.assertEqual(response.status, 403)

    async def test_unlinked_image_does_not_inherit_a_prompt_from_another_root(self):
        second = self.comfy_dir / "second-output"
        self._link_image(make_png(self.output_dir / "same.png"), text="first root")
        make_png(second / "same.png")
        self.api._get_all_output_dirs = lambda: [self.output_dir, second]
        response = await self.client.get(
            "/prompt_manager/images/prompt/same.png?root=1"
        )
        self.assertEqual(response.status, 200)
        self.assertFalse((await response.json())["success"])


class TestRootScopeServing(ImageAPITestCase):
    async def scoped_request(self, path="image.png", root="0", scope="0" * 64):
        params = {"root_scope": scope}
        if root is not None:
            params["root"] = root
        return await self.client.get(
            f"/prompt_manager/images/serve/{path}", params=params
        )

    async def assert_no_access(self, path, root="0", scope="0" * 64, status=409):
        original_stat = Path.stat

        def stat(candidate, *args, **kwargs):
            if candidate.name in ("image.png", "image_thumb.png"):
                raise AssertionError("candidate stat before scope guard")
            return original_stat(candidate, *args, **kwargs)

        with (
            patch.object(Path, "stat", stat),
            patch.object(
                self.api, "_file_response", side_effect=AssertionError("file response")
            ),
            patch.object(
                self.api.db,
                "get_image_prompt_info",
                side_effect=AssertionError("saved lookup"),
            ),
            patch(
                "py.api.inspection_service.read_inspection_file",
                side_effect=AssertionError("metadata read"),
            ),
            patch(
                "utils.prompt_inspector.subprocess.Popen",
                side_effect=AssertionError("probe"),
            ),
        ):
            response = await self.scoped_request(path, root, scope)
        self.assertEqual(response.status, status)
        result = await response.json()
        self.assertFalse(result["success"])
        if status == 409:
            self.assertEqual(result["code"], "source-changed")
            self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        self.assertNotIn(str(self.comfy_dir), json.dumps(result))

    async def test_root_scope_stale_original_and_thumbnail_fail_before_access(self):
        make_png(self.output_dir / "image.png")
        make_png(self.output_dir / "thumbnails" / "image_thumb.png")
        for path in ("image.png", "thumbnails/image_thumb.png"):
            await self.assert_no_access(path)

    async def test_root_scope_malformed_tokens_are_bad_requests(self):
        make_png(self.output_dir / "image.png")
        for scope in ("", "g" * 64, "a" * 63, "a" * 65, "a" * 4097, "A" * 64):
            await self.assert_no_access("image.png", scope=scope, status=400)
        for root in (None, "", "x", "-1", "0.0", "+0"):
            await self.assert_no_access("image.png", root=root, status=400)

    async def test_root_scope_rotated_key_and_removed_index_require_refresh(self):
        make_png(self.output_dir / "image.png")
        listing = await self.client.get("/prompt_manager/images/output?mode=workspace")
        descriptor = (await listing.json())["images"][0]
        self.assertRegex(descriptor.get("root_scope", ""), r"^[0-9a-f]{64}$")
        from utils.workspace_keys import WorkspaceKeys

        rotated = WorkspaceKeys(b"n" * 32, b"m" * 32, False)
        with patch.object(self.api, "_workspace_keys", return_value=rotated):
            await self.assert_no_access("image.png", scope=descriptor["root_scope"])
        self.api._get_all_output_dirs = lambda: []
        await self.assert_no_access("image.png", scope=descriptor["root_scope"])

    async def test_root_scope_keeps_containment_and_unscoped_access(self):
        make_png(self.output_dir / "image.png")
        listing = await self.client.get("/prompt_manager/images/output?mode=workspace")
        descriptor = (await listing.json())["images"][0]
        self.assertRegex(descriptor.get("root_scope", ""), r"^[0-9a-f]{64}$")
        scope = descriptor["root_scope"]
        self.assertEqual((await self.scoped_request(scope=scope)).status, 200)
        outside = make_png(self.comfy_dir / "outside.png")
        self._make_symlink(outside, self.output_dir / "escape.png")
        for path in ("..%2Foutside.png", "escape.png"):
            self.assertEqual((await self.scoped_request(path, scope=scope)).status, 403)
        self.assertEqual(
            (await self.client.get("/prompt_manager/images/serve/image.png")).status,
            200,
        )

    async def test_root_scope_equal_label_reorder_replacement_and_symlink_are_stale(
        self,
    ):
        roots = [self.comfy_dir / name / "output" for name in ("a", "b", "c")]
        for index, root in enumerate(roots):
            make_png(root / "image.png", color=(index, 0, 0))
            make_png(root / "thumbnails" / "image_thumb.png", color=(index, 0, 0))
        # Both roots have the same display label under disposable public anchors.
        with patch.object(
            GalleryConfig,
            "path_anchors",
            classmethod(lambda cls: [str(root.parent) for root in roots]),
        ):
            self.api._get_all_output_dirs = lambda: roots[:2]
            listing = await self.client.get(
                "/prompt_manager/images/output?mode=workspace"
            )
            descriptors = (await listing.json())["images"]
            self.assertEqual({entry["root_dir"] for entry in descriptors}, {"output"})
            token = next(
                entry for entry in descriptors if entry["root_index"] == 0
            ).get("root_scope", "0" * 64)
            for changed in ([roots[1], roots[0]], [roots[2], roots[1]]):
                self.api._get_all_output_dirs = lambda: changed
                for path in ("image.png", "thumbnails/image_thumb.png"):
                    await self.assert_no_access(path, scope=token)
            link = self.comfy_dir / "linked-output"
            self._make_symlink(roots[0], link)
            self.api._get_all_output_dirs = lambda: [link]
            listing = await self.client.get(
                "/prompt_manager/images/output?mode=workspace"
            )
            token = (await listing.json())["images"][0]["root_scope"]
            link.unlink()
            self._make_symlink(roots[1], link)
            for path in ("image.png", "thumbnails/image_thumb.png"):
                await self.assert_no_access(path, scope=token)


# Seconds any single wait in the shared-listing tests may take.
_HANG_LIMIT = 30


def _reference_iter_media_files(output_path, max_files):
    """``_iter_media_files`` as it was before the scandir walk (#348)."""
    output_path = Path(output_path)
    root_depth = len(output_path.parts)
    produced = 0
    for root, dirs, files in os.walk(output_path):
        root_path = Path(root)
        rel_parts = root_path.parts[root_depth:]
        if "thumbnails" in rel_parts:
            dirs[:] = []
            continue
        if len(rel_parts) >= images_module.MAX_SCAN_DEPTH:
            dirs[:] = []
        for name in sorted(files):
            # Path.suffix as Python 3.10-3.13 computed it (3.14 changed it).
            dot = name.rfind(".")
            suffix = name[dot:] if 0 < dot < len(name) - 1 else ""
            if suffix.lower() not in images_module.MEDIA_EXTENSIONS:
                continue
            if (root_path / name).is_symlink():
                continue
            if produced >= max_files:
                return
            produced += 1
            yield root_path / name


class _GatedScan:
    """Wraps the real scan, counts calls and can hold one call after it listed.

    ``hold_next()`` makes the next call list the files, then wait on the
    returned gate, so a test can change the disk or the cache while a walk
    is in flight and decide when it ends.
    """

    def __init__(self, real):
        self.real = real
        self.calls = 0
        self._gate = None
        self.entered = threading.Event()
        self._gates = []

    def hold_next(self):
        self._gate = threading.Event()
        self._gates.append(self._gate)
        self.entered.clear()
        return self._gate

    def release_all(self):
        for gate in self._gates:
            gate.set()

    def __call__(self, root):
        self.calls += 1
        files = self.real(root)
        gate, self._gate = self._gate, None
        if gate is not None:
            self.entered.set()
            gate.wait(30)
        return files

    async def wait_entered(self):
        loop = asyncio.get_running_loop()
        entered = await loop.run_in_executor(None, self.entered.wait, 30)
        if not entered:
            raise AssertionError("the walk never reached the gate")


class TestSharedListing(ImageAPITestCase):
    """One listing per output root, shared and refreshed in the background (#348)."""

    URL = "/prompt_manager/images/output"

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.scan = _GatedScan(self.api._scan_gallery_files_sync)
        self.api._scan_gallery_files_sync = self.scan
        self.addCleanup(self.scan.release_all)
        # The stale refresh waits REFRESH_DELAY after the read (#348); these
        # tests start it on the next loop turn and _settle waits for it.
        delay = patch.object(gallery_listing, "REFRESH_DELAY", 0)
        delay.start()
        self.addCleanup(delay.stop)

    async def _json(self, method, path, **kwargs):
        # Bounded, like every wait in this class: a stuck walk fails the test
        # with a traceback instead of hanging the whole run.
        async def call():
            resp = await self.client.request(method, path, **kwargs)
            return resp.status, await resp.json()

        return await asyncio.wait_for(call(), _HANG_LIMIT)

    def _record(self):
        return self.api._root_listings[str(self.output_dir)]

    def _pending(self):
        return any(
            record.rewalk_handle is not None
            for record in self.api._root_listings.values()
        )

    def _warming(self):
        task = self.api._workspace_warm_task
        return task is not None and not task.done()

    def _age(self, seconds=None):
        from py.api import gallery_listing

        record = self._record()
        shift = (seconds or gallery_listing.REFRESH_AFTER) + 1
        record.walked_at -= shift
        # An old listing's walk also started long ago (rewalk_interval).
        record.last_started_at -= shift

    async def _settle(self):
        """Wait for every background walk this API started."""

        async def settle():
            while self.api._listing_tasks or self._warming() or self._pending():
                if self._pending():
                    await asyncio.sleep(0.01)  # a delayed refresh not started yet
                await asyncio.gather(*list(self.api._listing_tasks))
                # A finished walk leaves the set in a done callback, which needs
                # a loop turn; gather over finished tasks does not yield one.
                await asyncio.sleep(0)
                if self._warming():
                    await asyncio.wait([self.api._workspace_warm_task])

        await asyncio.wait_for(settle(), _HANG_LIMIT)

    async def test_concurrent_cold_readers_share_one_walk(self):
        make_png(self.output_dir / "sub" / "a.png")
        results = await asyncio.gather(
            self._json("GET", self.URL),
            self._json("GET", self.URL + "?limit=1"),
            self._json("GET", self.URL + "?mode=workspace&limit=1"),
            self._json("GET", "/prompt_manager/gallery/subfolders"),
        )
        self.assertEqual([status for status, _ in results], [200] * 4)
        self.assertEqual(self.scan.calls, 1)
        self.assertEqual(results[0][1]["total"], 1)
        self.assertEqual(results[3][1]["subfolders"], ["sub"])

    async def test_a_stale_listing_is_served_at_once_and_refreshed_behind(self):
        make_png(self.output_dir / "a.png")
        _, first = await self._json("GET", self.URL)
        make_png(self.output_dir / "b.png")
        self._age()
        gate = self.scan.hold_next()
        _, stale = await self._json("GET", self.URL)
        # Served from the old listing while the walk is still held.
        self.assertEqual((first["total"], stale["total"]), (1, 1))
        await self.scan.wait_entered()
        # A second stale read joins the running walk instead of starting one.
        _, again = await self._json("GET", self.URL)
        self.assertEqual(again["total"], 1)
        gate.set()
        await self._settle()
        _, fresh = await self._json("GET", self.URL)
        self.assertEqual(fresh["total"], 2)
        self.assertEqual(self.scan.calls, 2)

    async def test_a_fresh_listing_is_not_walked_again(self):
        make_png(self.output_dir / "a.png")
        for _ in range(3):
            await self._json("GET", self.URL)
            await self._json("GET", "/prompt_manager/gallery/subfolders")
        self.assertEqual(self.scan.calls, 1)
        self.assertEqual(self.api._listing_tasks, set())

    async def test_rescan_waits_for_a_walk_started_after_the_click(self):
        make_png(self.output_dir / "a.png")
        await self._json("GET", self.URL)
        self._age()
        gate = self.scan.hold_next()
        await self._json("GET", self.URL)  # starts a background walk
        await self.scan.wait_entered()  # it has listed: it cannot see c.png
        make_png(self.output_dir / "c.png")
        rescan = asyncio.ensure_future(
            self._json("POST", "/prompt_manager/gallery/rescan")
        )
        await asyncio.sleep(0)
        self.assertFalse(rescan.done())
        gate.set()
        status, data = await rescan
        self.assertEqual(status, 200)
        self.assertEqual((data["total"], data["roots"]), (2, 1))
        self.assertEqual(self.scan.calls, 3)

    async def test_rescans_clicked_during_a_walk_share_the_next_one(self):
        make_png(self.output_dir / "a.png")
        await self._json("GET", self.URL)
        original = self.api._listing
        rescans = []
        clicked = asyncio.Event()

        async def listing(root, *, fresh=False):
            if fresh:
                rescans.append(root)
                if len(rescans) == 3:
                    clicked.set()
            # Runs on in this step up to its first wait, so the Rescan's
            # walk number is taken before the test resumes.
            return await original(root, fresh=fresh)

        self.api._listing = listing
        gate = self.scan.hold_next()
        first = asyncio.ensure_future(
            self._json("POST", "/prompt_manager/gallery/rescan")
        )
        await self.scan.wait_entered()
        # Clicked while the first Rescan's walk runs: neither may use it,
        # and both wait for the same next walk.
        later = [
            asyncio.ensure_future(self._json("POST", "/prompt_manager/gallery/rescan"))
            for _ in range(2)
        ]
        await asyncio.wait_for(clicked.wait(), 30)
        gate.set()
        results = await asyncio.gather(first, *later)
        self.assertEqual([status for status, _ in results], [200] * 3)
        self.assertEqual(self.scan.calls, 3)

    async def test_rescan_failure_with_a_previous_listing_is_500(self):
        make_png(self.output_dir / "a.png")
        await self._json("GET", self.URL)
        self.api._scan_gallery_files_sync = _raise
        with self.assertLogs(self.api.logger, "WARNING"):
            status, data = await self._json("POST", "/prompt_manager/gallery/rescan")
        self.assertEqual(status, 500)
        self.assertFalse(data["success"])
        self.assertNotIn("boom", json.dumps(data))
        # Plain readers keep the previous listing, and do not retry at once.
        status, listed = await self._json("GET", self.URL)
        self.assertEqual((status, listed["total"]), (200, 1))
        self.assertEqual(self.api._listing_tasks, set())

    async def test_a_cold_read_whose_walk_fails_is_500(self):
        self.api._scan_gallery_files_sync = _raise
        with self.assertLogs(self.api.logger, "WARNING"):
            status, data = await self._json("GET", self.URL)
        self.assertEqual(status, 500)
        self.assertFalse(data["success"])

    async def test_a_deleted_root_keeps_the_previous_listing_and_warns_once(self):
        import shutil

        make_png(self.output_dir / "a.png")
        await self._json("GET", self.URL)
        shutil.rmtree(self.output_dir)
        with self.assertLogs(self.api.logger, "WARNING") as logs:
            for _ in range(2):
                self._age()
                status, data = await self._json("GET", self.URL)
                self.assertEqual((status, data["total"]), (200, 1))
                await self._settle()
        warnings = [r for r in logs.records if r.levelname == "WARNING"]
        self.assertEqual(len(warnings), 1, [r.getMessage() for r in warnings])
        self.assertEqual(self.scan.calls, 1)  # the probe failed before the scan

    async def test_a_root_that_recovers_warns_again_on_its_next_failure(self):
        make_png(self.output_dir / "a.png")
        await self._json("GET", self.URL)
        real = self.api._scan_gallery_files_sync
        with self.assertLogs(self.api.logger, "WARNING") as logs:
            for scan in (_raise, real, _raise):
                self.api._scan_gallery_files_sync = scan
                self._age()
                await self._json("GET", self.URL)
                await self._settle()
        warnings = [r for r in logs.records if r.levelname == "WARNING"]
        self.assertEqual(len(warnings), 2)

    async def test_an_invalidation_during_a_walk_discards_it(self):
        make_png(self.output_dir / "a.png")
        gate = self.scan.hold_next()
        reader = asyncio.ensure_future(self._json("GET", self.URL))
        await self.scan.wait_entered()
        make_png(self.output_dir / "b.png")
        self.api.invalidate_gallery_cache()
        gate.set()
        status, data = await reader
        self.assertEqual((status, data["total"]), (200, 2))
        self.assertEqual(self.scan.calls, 2)

    async def test_an_invalidation_from_a_worker_thread_drops_on_the_loop(self):
        make_png(self.output_dir / "a.png")
        await self._json("GET", self.URL)
        record = self._record()
        loop_thread = threading.get_ident()
        threads = []
        original = record.drop

        def drop():
            threads.append(threading.get_ident())
            original()

        record.drop = drop
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.api.invalidate_gallery_cache)
        # Dropped on the loop, before invalidate_gallery_cache returned.
        self.assertEqual(threads, [loop_thread])
        self.assertIsNone(record.files)

    async def test_three_discarded_walks_are_a_500(self):
        loop = asyncio.get_running_loop()
        real = self.scan.real

        async def invalidate():
            self.api.invalidate_gallery_cache()

        def scan(root):
            asyncio.run_coroutine_threadsafe(invalidate(), loop).result(30)
            return real(root)

        self.scan.real = scan
        status, data = await self._json("GET", self.URL)
        self.assertEqual(status, 500)
        self.assertFalse(data["success"])
        self.assertEqual(self.scan.calls, 3)

    async def test_a_disconnected_waiter_does_not_cancel_the_walk(self):
        make_png(self.output_dir / "a.png")
        gate = self.scan.hold_next()
        waiter = asyncio.ensure_future(self.api._listing(self.output_dir))
        await self.scan.wait_entered()
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        flight = self._record().flight
        self.assertIsNotNone(flight)
        gate.set()
        await asyncio.shield(flight)
        self.assertEqual(len(self._record().files), 1)
        self.assertIsNone(self._record().flight)

    async def test_a_duplicate_delete_is_gone_on_the_very_next_read(self):
        make_png(self.output_dir / "keep.png")
        make_png(self.output_dir / "dupe.png")
        _, before = await self._json("GET", self.URL)
        self.assertEqual(before["total"], 2)
        status, data = await self._json(
            "POST",
            "/prompt_manager/delete_duplicate_images",
            json={"image_paths": ["dupe.png"]},
        )
        self.assertEqual((status, data["deleted_count"]), (200, 1))
        _, after = await self._json("GET", self.URL)
        self.assertEqual([image["filename"] for image in after["images"]], ["keep.png"])

    async def test_response_keys_are_unchanged(self):
        make_png(self.output_dir / "sub" / "a.png")
        _, legacy = await self._json("GET", self.URL)
        self.assertEqual(
            set(legacy),
            {"success", "images", "total", "offset", "limit", "has_more"},
        )
        _, workspace = await self._json("GET", self.URL + "?mode=workspace")
        self.assertEqual(
            set(workspace),
            {
                "success",
                "images",
                "total",
                "positive_count",
                "limit",
                "has_more",
                "next_cursor",
                # #498: Show removed images (N).
                "removed_total",
            },
        )
        _, folders = await self._json("GET", "/prompt_manager/gallery/subfolders")
        self.assertEqual(set(folders), {"success", "subfolders"})
        _, scoped = await self._json(
            "GET", "/prompt_manager/gallery/subfolders?mode=workspace"
        )
        self.assertEqual(set(scoped), {"success", "subfolders", "roots"})
        self.assertEqual(set(scoped["roots"][0]), {"index", "label", "subfolders"})
        _, rescan = await self._json("POST", "/prompt_manager/gallery/rescan")
        self.assertEqual(set(rescan), {"success", "total", "roots"})

    async def test_subfolders_with_ancestors_come_from_the_listing(self):
        make_png(self.output_dir / "a" / "b" / "c.png")
        _, plain = await self._json("GET", "/prompt_manager/gallery/subfolders")
        _, nested = await self._json(
            "GET", "/prompt_manager/gallery/subfolders?include_ancestors=true"
        )
        self.assertEqual(plain["subfolders"], [str(Path("a") / "b")])
        self.assertEqual(
            sorted(nested["subfolders"]), sorted(["a", str(Path("a") / "b")])
        )
        self.assertEqual(self.scan.calls, 1)

    async def test_warm_up_walks_every_root_before_the_first_read(self):
        second = self.comfy_dir / "second"
        make_png(second / "x.png")
        make_png(self.output_dir / "a.png")
        self.api._get_all_output_dirs = lambda: [self.output_dir, second]
        await self.api.warm_gallery_listings(web.Application())
        self.assertEqual(len(self.api._listing_tasks), 2)
        await self._settle()
        self.assertEqual(self.scan.calls, 2)
        _, data = await self._json("GET", self.URL)
        self.assertEqual(data["total"], 2)
        self.assertEqual(self.scan.calls, 2)

    async def test_warm_up_never_fails_startup(self):
        def broken():
            raise RuntimeError("no roots")

        self.api._get_all_output_dirs = broken
        with self.assertLogs(self.api.logger, "WARNING"):
            await self.api.warm_gallery_listings(web.Application())
        self.assertEqual(self.api._listing_tasks, set())

    async def test_warm_up_failure_is_retried_by_the_first_read(self):
        make_png(self.output_dir / "a.png")
        self.api._scan_gallery_files_sync = _raise
        with self.assertLogs(self.api.logger, "WARNING"):
            await self.api.warm_gallery_listings(web.Application())
            await self._settle()
        self.api._scan_gallery_files_sync = self.scan
        status, data = await self._json("GET", self.URL)
        self.assertEqual((status, data["total"]), (200, 1))

    async def test_cleanup_cancels_running_walks(self):
        make_png(self.output_dir / "a.png")
        gate = self.scan.hold_next()
        await self.api.warm_gallery_listings(web.Application())
        await self.scan.wait_entered()
        tasks = set(self.api._listing_tasks)
        flight = self._record().flight
        await self.api.cleanup_gallery_listings(web.Application())
        gate.set()
        self.assertTrue(all(task.cancelled() for task in tasks))
        self.assertEqual(self.api._listing_tasks, set())
        self.assertTrue(flight.done())
        self.assertIsNone(self._record().flight)
        self.assertIsNone(self._record().files)

    async def test_cleanup_resolves_a_walk_cancelled_before_it_ran(self):
        await self.api.warm_gallery_listings(web.Application())
        flight = self._record().flight
        waiter = asyncio.ensure_future(asyncio.shield(flight))
        # No await between the warm-up and the cleanup: the task never ran.
        await self.api.cleanup_gallery_listings(web.Application())
        outcome = await waiter
        self.assertEqual(outcome.kind, gallery_listing.FAILED)
        self.assertIsNone(self._record().flight)
        self.assertEqual(self.scan.calls, 0)

    async def test_a_stale_read_starts_its_refresh_after_a_delay(self):
        # The read is served from the old listing; the walk starts
        # REFRESH_DELAY later so it does not share the GIL with the page's
        # own requests (#348 live test: 2.2 s reads during the walk).
        make_png(self.output_dir / "a.png")
        await self._json("GET", self.URL)
        self._age()
        with patch.object(gallery_listing, "REFRESH_DELAY", 30):
            status, data = await self._json("GET", self.URL)
            await self._json("GET", self.URL)  # still one pending refresh
            record = self._record()
            handle = record.rewalk_handle
            self.assertEqual((status, data["total"]), (200, 1))
            self.assertIsNotNone(handle)
            self.assertIsNone(record.flight)
            self.assertEqual(self.scan.calls, 1)
            self.assertAlmostEqual(
                handle.when() - asyncio.get_running_loop().time(), 30, delta=2
            )
            handle.cancel()
            record.rewalk_handle = None

    async def test_the_delayed_refresh_walks_once_when_it_fires(self):
        make_png(self.output_dir / "a.png")
        await self._json("GET", self.URL)
        make_png(self.output_dir / "b.png")
        self._age()
        await self._json("GET", self.URL)
        await self._json("GET", self.URL)
        await self._settle()
        self.assertEqual(self.scan.calls, 2)
        self.assertIsNone(self._record().rewalk_handle)
        _, data = await self._json("GET", self.URL)
        self.assertEqual(data["total"], 2)

    async def test_a_walk_started_meanwhile_cancels_the_delayed_refresh(self):
        make_png(self.output_dir / "a.png")
        await self._json("GET", self.URL)
        self._age()
        with patch.object(gallery_listing, "REFRESH_DELAY", 30):
            await self._json("GET", self.URL)
            handle = self._record().rewalk_handle
            status, _ = await self._json("POST", "/prompt_manager/gallery/rescan")
        self.assertEqual(status, 200)
        self.assertTrue(handle.cancelled())
        self.assertIsNone(self._record().rewalk_handle)
        self.assertEqual(self.scan.calls, 2)

    async def test_cleanup_cancels_a_delayed_refresh(self):
        make_png(self.output_dir / "a.png")
        await self._json("GET", self.URL)
        self._age()
        with patch.object(gallery_listing, "REFRESH_DELAY", 30):
            await self._json("GET", self.URL)
            handle = self._record().rewalk_handle
            await self.api.cleanup_gallery_listings(web.Application())
        self.assertTrue(handle.cancelled())
        self.assertIsNone(self._record().rewalk_handle)
        self.assertEqual(self.scan.calls, 1)

    WORKSPACE_URL = "/prompt_manager/images/output?mode=workspace&limit=1"

    async def _warmed(self):
        await self.api.warm_gallery_listings(web.Application())
        await self._settle()

    def _workspace_ready(self):
        """True when a first workspace page needs no per-file rebuild."""
        files = self._record().files
        favorites = self.api._favorite_listing_cache.get(str(self.output_dir))
        listed = self.api._workspace_listed_cache.get((0, str(self.output_dir)))
        return (
            favorites is not None
            and favorites[0] is files
            and listed is not None
            and listed[0] is files
            and bool(self.api._workspace_order_cache)
        )

    async def test_warm_up_also_builds_the_workspace_caches(self):
        # The first open after a restart paid ~3 s building these (#348 live).
        make_png(self.output_dir / "a.png")
        make_png(self.output_dir / "sub" / "b.png")
        await self._warmed()
        self.assertTrue(self._workspace_ready())
        with patch.object(images_module, "_listed", side_effect=AssertionError):
            status, data = await self._json("GET", self.WORKSPACE_URL)
        self.assertEqual((status, data["total"]), (200, 2))

    async def test_a_changed_refresh_rebuilds_the_workspace_caches_behind(self):
        make_png(self.output_dir / "a.png")
        await self._warmed()
        make_png(self.output_dir / "b.png")
        self._age()
        await self._json("GET", self.URL)  # stale: one background walk
        await self._settle()
        self.assertEqual(len(self._record().files), 2)
        self.assertTrue(self._workspace_ready())

    async def test_an_unchanged_refresh_does_not_rebuild_them(self):
        make_png(self.output_dir / "a.png")
        await self._warmed()
        with patch.object(
            self.api, "_build_workspace_caches", side_effect=AssertionError
        ):
            self._age()
            await self._json("GET", self.URL)
            await self._settle()
        self.assertTrue(self._workspace_ready())

    async def test_without_the_warm_up_reads_start_no_workspace_build(self):
        make_png(self.output_dir / "a.png")
        await self._json("GET", self.URL)
        await self._settle()
        self.assertIsNone(self.api._workspace_warm_task)
        self.assertEqual(self.api._favorite_listing_cache, {})

    async def test_a_failed_workspace_build_warns_and_reads_still_work(self):
        make_png(self.output_dir / "a.png")
        with (
            patch.object(
                self.api, "_select_workspace_page", side_effect=RuntimeError("boom")
            ),
            self.assertLogs(self.api.logger, "WARNING") as logs,
        ):
            await self._warmed()
        self.assertTrue(any("Images page" in line for line in logs.output))
        status, data = await self._json("GET", self.URL)
        self.assertEqual((status, data["total"]), (200, 1))

    async def test_walks_during_a_workspace_build_coalesce_into_one_more(self):
        make_png(self.output_dir / "a.png")
        release, entered = threading.Event(), threading.Event()
        built = []
        real = self.api._build_workspace_caches

        def build(listings):
            built.append(len(listings[0][2]))
            if len(built) == 1:
                entered.set()
                release.wait(_HANG_LIMIT)
            real(listings)

        self.addCleanup(release.set)
        with patch.object(self.api, "_build_workspace_caches", side_effect=build):
            await self.api.warm_gallery_listings(web.Application())
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, entered.wait, _HANG_LIMIT)
            for name in ("b.png", "c.png"):  # two changed walks while it builds
                make_png(self.output_dir / name)
                self._age()
                await self._json("GET", self.URL)
                while self.api._listing_tasks:
                    await asyncio.gather(*list(self.api._listing_tasks))
                    await asyncio.sleep(0)
            release.set()
            await self._settle()
        self.assertEqual(built, [1, 3])
        self.assertEqual(self.scan.calls, 3)  # two stored walks, one more build
        self.assertTrue(self._workspace_ready())

    async def test_a_build_that_cannot_list_the_roots_only_warns(self):
        make_png(self.output_dir / "a.png")
        await self._warmed()

        def broken():
            raise RuntimeError("no roots")

        self.api._get_all_output_dirs = broken
        with self.assertLogs(self.api.logger, "WARNING") as logs:
            self.api._schedule_workspace_warm()
            task = self.api._workspace_warm_task
            await asyncio.wait([task])
        self.assertIsNone(task.exception())
        self.assertTrue(any("Images page" in line for line in logs.output))

    async def test_cleanup_stops_a_running_workspace_build(self):
        make_png(self.output_dir / "a.png")
        release, entered = threading.Event(), threading.Event()

        def build(_listings):
            entered.set()
            release.wait(_HANG_LIMIT)

        self.addCleanup(release.set)
        with patch.object(self.api, "_build_workspace_caches", side_effect=build):
            await self.api.warm_gallery_listings(web.Application())
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, entered.wait, _HANG_LIMIT)
            task = self.api._workspace_warm_task
            await self.api.cleanup_gallery_listings(web.Application())
            release.set()
        self.assertTrue(task.done())
        self.assertIsNone(self.api._workspace_warm_task)

    # -- monitor-triggered walks (#348, PR 2) ---------------------------

    def _interval(self, seconds):
        """Patch rewalk_interval() to *seconds*, whatever the walk took."""
        for name in ("REWALK_FLOOR", "REWALK_CAP"):
            patcher = patch.object(gallery_listing, name, seconds)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _output(self, name):
        """A new output on disk, spelled as the monitor reports it (resolved)."""
        return os.path.realpath(make_png(self.output_dir / name))

    async def _listed(self):
        """A stored listing of the root, built by a read."""
        await self._json("GET", self.URL)
        await self._settle()
        return self._record()

    def _fire(self, record):
        """Run the pending deferred walk now, as its timer would."""
        handle = record.rewalk_handle
        self.assertIsNotNone(handle, "no deferred walk is pending")
        handle.cancel()
        self.api._delayed_walk(self.output_dir, record)

    def _remaining(self, handle):
        return handle.when() - asyncio.get_running_loop().time()

    async def test_a_settled_output_is_listed_without_a_reload(self):
        make_png(self.output_dir / "a.png")
        record = await self._listed()
        self._interval(0)
        self.api.mark_root_dirty(self._output("b.png"))
        self.assertIsNotNone(record.flight)  # the interval allows: walks now
        self.assertFalse(record.dirty)
        await self._settle()
        self.assertEqual(self.scan.calls, 2)
        self.assertEqual(len(record.files), 2)
        _, data = await self._json("GET", self.URL)
        self.assertEqual(data["total"], 2)
        self.assertEqual(self.scan.calls, 2)

    async def test_the_monitor_thread_hops_onto_the_loop(self):
        make_png(self.output_dir / "a.png")
        record = await self._listed()
        self._interval(0)
        path = self._output("b.png")
        calls = []
        real = self.api.output_settled

        def settled(seen):
            calls.append(threading.get_ident())
            real(seen)

        self.api.output_settled = settled
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.api.notify_output_settled, path)
        # The hop was queued before the executor's result: one loop turn runs it.
        await asyncio.sleep(0)
        self.assertEqual(calls, [threading.get_ident()])
        await self._settle()
        self.assertEqual(len(record.files), 2)
        self.assertEqual(self.scan.calls, 1)  # added, not walked

    async def test_notify_before_the_loop_is_known_does_nothing(self):
        self.assertIsNone(self.api._listing_loop)
        with patch.object(self.api, "output_settled") as mark:
            self.api.notify_output_settled(self._output("b.png"))
            await asyncio.sleep(0)
        mark.assert_not_called()

    async def test_notify_after_cleanup_does_nothing(self):
        make_png(self.output_dir / "a.png")
        record = await self._listed()
        await self.api.cleanup_gallery_listings(web.Application())
        self._interval(0)
        with patch.object(self.api, "output_settled") as mark:
            self.api.notify_output_settled(self._output("b.png"))
            await asyncio.sleep(0)
        mark.assert_not_called()
        # A hop queued before the cleanup is dropped when it runs.
        self.api.output_settled(self._output("c.png"))
        self.api.mark_root_dirty(self._output("d.png"))
        self.assertEqual(self.api._listing_tasks, set())
        self.assertEqual(record.pending_adds, [])
        self.assertFalse(record.dirty)
        self.assertIsNone(record.flight)
        self.assertIsNone(record.rewalk_handle)

    async def test_notify_on_a_closed_loop_does_nothing(self):
        loop = asyncio.new_event_loop()
        loop.close()
        self.api._listing_loop = loop
        self.api.notify_output_settled(self._output("b.png"))  # no exception

    async def test_notify_racing_the_loop_closing_does_nothing(self):
        class ClosingLoop:
            def is_running(self):
                return True

            def call_soon_threadsafe(self, *_args):
                raise RuntimeError("Event loop is closed")

        self.api._listing_loop = ClosingLoop()
        self.api.notify_output_settled(self._output("b.png"))  # no exception

    async def test_an_output_outside_every_root_is_ignored(self):
        make_png(self.output_dir / "a.png")
        record = await self._listed()
        self._interval(0)
        elsewhere = make_png(self.comfy_dir / "output2" / "x.png")
        self.api.mark_root_dirty(os.path.realpath(elsewhere))
        self.assertFalse(record.dirty)
        self.assertIsNone(record.flight)
        self.assertIsNone(record.rewalk_handle)

    async def test_a_root_never_listed_is_not_walked_by_a_mark(self):
        # No listing, no walk running: the first read walks anyway.
        self.api.mark_root_dirty(self._output("b.png"))
        self.assertEqual(self.api._root_listings, {})
        self.assertEqual(self.scan.calls, 0)

    async def test_a_dropped_listing_is_left_to_the_next_read(self):
        make_png(self.output_dir / "a.png")
        record = await self._listed()
        self.api.invalidate_gallery_cache()
        self._interval(0)
        self.api.mark_root_dirty(self._output("b.png"))
        self.assertIsNone(record.flight)
        self.assertEqual(self.scan.calls, 1)

    async def test_the_walk_records_the_resolved_root_and_its_timing(self):
        make_png(self.output_dir / "a.png")
        before = images_module._time.monotonic()
        record = await self._listed()
        self.assertEqual(
            record.resolved_root, os.path.normcase(os.path.realpath(self.output_dir))
        )
        self.assertGreaterEqual(record.last_started_at, before)
        self.assertLessEqual(record.last_started_at, record.walked_at)
        # Measured when the walk ended, after its listing was stored.
        self.assertGreaterEqual(
            record.last_duration, record.walked_at - record.last_started_at
        )

    async def test_a_failed_walk_records_its_timing_and_clears_dirty(self):
        make_png(self.output_dir / "a.png")
        record = await self._listed()
        self._interval(30)
        self.api.mark_root_dirty(self._output("b.png"))
        self.assertTrue(record.dirty)
        self.api._scan_gallery_files_sync = _raise
        # Moved back, so the failed walk's start is later even on a coarse
        # monotonic clock (Windows ticks every ~15.6 ms).
        record.last_started_at -= 1000
        record.last_duration = None
        started = record.last_started_at
        with self.assertLogs(self.api.logger, "WARNING"):
            self._fire(record)
            await self._settle()
        self.assertGreater(record.last_started_at, started)
        self.assertIsNotNone(record.last_duration)
        self.assertFalse(record.dirty)
        self.assertIsNone(record.rewalk_handle)  # no trailing walk after a failure
        self.assertEqual(len(record.files), 1)  # the previous listing is kept

    async def test_a_burst_of_marks_coalesces_into_one_walk(self):
        make_png(self.output_dir / "a.png")
        record = await self._listed()
        self._interval(30)
        for i in range(5):
            self.api.mark_root_dirty(self._output(f"b{i}.png"))
        handle = record.rewalk_handle
        self.assertIsNotNone(handle)
        self.assertIsNone(record.flight)
        self.assertEqual(self.scan.calls, 1)
        # Due one interval after the last walk started, not 30 s from now.
        due = record.last_started_at + 30 - images_module._time.monotonic()
        self.assertAlmostEqual(self._remaining(handle), due, delta=1)
        self._fire(record)
        await self._settle()
        self.assertEqual(self.scan.calls, 2)
        self.assertEqual(len(record.files), 6)
        self.assertFalse(record.dirty)
        self.assertIsNone(record.rewalk_handle)

    async def test_a_mark_during_a_walk_gives_exactly_one_trailing_walk(self):
        make_png(self.output_dir / "a.png")
        record = await self._listed()
        self._interval(30)
        gate = self.scan.hold_next()
        rescan = asyncio.ensure_future(
            self._json("POST", "/prompt_manager/gallery/rescan")
        )
        await self.scan.wait_entered()  # listed before b.png exists
        for name in ("b.png", "c.png"):
            self.api.mark_root_dirty(self._output(name))
        # Never armed while a walk runs: the walk's end re-checks dirty.
        self.assertIsNone(record.rewalk_handle)
        self.assertTrue(record.dirty)
        gate.set()
        status, _ = await rescan
        self.assertEqual(status, 200)
        self.assertEqual(len(record.files), 1)
        handle = record.rewalk_handle
        self.assertIsNotNone(handle)  # the trailing walk, deferred by the interval
        self.assertGreaterEqual(self._remaining(handle), 0.5)
        self._fire(record)
        await self._settle()
        self.assertEqual(self.scan.calls, 3)
        self.assertEqual(len(record.files), 3)
        self.assertIsNone(record.rewalk_handle)
        self.assertFalse(record.dirty)

    async def test_a_walk_slower_than_the_interval_trails_at_once_and_stops(self):
        make_png(self.output_dir / "a.png")
        record = await self._listed()
        self._interval(0)  # the walk outlasts its interval
        gate = self.scan.hold_next()
        self.api.mark_root_dirty(self._output("b.png"))
        await self.scan.wait_entered()
        self.api.mark_root_dirty(self._output("c.png"))
        self.assertIsNone(record.rewalk_handle)
        gate.set()
        await self._settle()
        # One walk for the first mark, one trailing walk for the second, then
        # nothing: no mark arrived during the trailing walk.
        self.assertEqual(self.scan.calls, 3)
        self.assertEqual(len(record.files), 3)
        self.assertIsNone(record.rewalk_handle)
        self.assertIsNone(record.flight)

    async def test_a_read_triggered_walk_cancels_a_pending_rewalk(self):
        make_png(self.output_dir / "a.png")
        record = await self._listed()
        self._interval(30)
        self.api.mark_root_dirty(self._output("b.png"))
        handle = record.rewalk_handle
        status, data = await self._json("POST", "/prompt_manager/gallery/rescan")
        self.assertEqual((status, data["total"]), (200, 2))
        self.assertTrue(handle.cancelled())
        self.assertIsNone(record.rewalk_handle)
        self.assertFalse(record.dirty)
        await self._settle()
        self.assertEqual(self.scan.calls, 2)

    async def test_after_the_trailing_walk_a_new_mark_arms_again(self):
        make_png(self.output_dir / "a.png")
        record = await self._listed()
        self._interval(30)
        self.api.mark_root_dirty(self._output("b.png"))
        self._fire(record)
        await self._settle()
        self.assertIsNone(record.rewalk_handle)
        self.api.mark_root_dirty(self._output("c.png"))
        self.assertIsNotNone(record.rewalk_handle)
        self._fire(record)
        await self._settle()
        self.assertEqual(self.scan.calls, 3)
        self.assertEqual(len(record.files), 3)

    async def test_a_deferred_walk_waits_at_least_half_a_second(self):
        make_png(self.output_dir / "a.png")
        record = await self._listed()
        self._interval(30)
        # The interval ends in a few milliseconds: still never a zero delay.
        # The module's clock is held, so a slow runner cannot pass the end
        # of the interval between these lines (the walk would start at once).
        now = images_module._time.monotonic()
        record.last_started_at = now - 29.99
        held = types.SimpleNamespace(monotonic=lambda: now)
        with patch.object(images_module, "_time", held):
            self.api.mark_root_dirty(self._output("b.png"))
        handle = record.rewalk_handle
        self.assertIsNotNone(handle)
        self.assertGreaterEqual(self._remaining(handle), 0.45)
        handle.cancel()

    async def test_a_mark_moves_a_later_stale_refresh_earlier(self):
        make_png(self.output_dir / "a.png")
        record = await self._listed()
        self._age()
        self._interval(5)
        with patch.object(gallery_listing, "REFRESH_DELAY", 30):
            await self._json("GET", self.URL)
        stale = record.rewalk_handle
        self.assertGreater(self._remaining(stale), 25)
        # The interval has long passed: the mark's walk starts now, and that
        # walk is the refresh the stale read was waiting for.
        self.api.mark_root_dirty(self._output("b.png"))
        self.assertTrue(stale.cancelled())
        self.assertIsNotNone(record.flight)
        self.assertIsNone(record.rewalk_handle)
        await self._settle()
        self.assertEqual(self.scan.calls, 2)
        self.assertEqual(len(record.files), 2)

    async def test_a_stale_read_keeps_an_earlier_pending_rewalk(self):
        make_png(self.output_dir / "a.png")
        record = await self._listed()
        self._interval(20)
        self.api.mark_root_dirty(self._output("b.png"))
        pending = record.rewalk_handle
        when = pending.when()
        # Stale by its age, while the interval since the last walk still runs.
        record.walked_at -= gallery_listing.REFRESH_AFTER + 1
        with patch.object(gallery_listing, "REFRESH_DELAY", 30):
            await self._json("GET", self.URL)
        self.assertIs(record.rewalk_handle, pending)
        self.assertEqual(pending.when(), when)
        self.assertFalse(pending.cancelled())

    async def test_a_stale_read_moves_a_later_pending_rewalk_earlier(self):
        make_png(self.output_dir / "a.png")
        record = await self._listed()
        self._interval(15)
        self.api.mark_root_dirty(self._output("b.png"))
        pending = record.rewalk_handle
        self.assertGreater(self._remaining(pending), 10)
        # A listing older than REFRESH_AFTER whose interval is over.
        self._age()
        with patch.object(gallery_listing, "REFRESH_DELAY", 2):
            await self._json("GET", self.URL)
        self.assertTrue(pending.cancelled())
        replaced = record.rewalk_handle
        self.assertIsNotNone(replaced)
        self.assertLess(self._remaining(replaced), 3)
        self.assertEqual(self.scan.calls, 1)
        replaced.cancel()

    async def test_a_dirty_read_rearms_a_lost_rewalk(self):
        make_png(self.output_dir / "a.png")
        record = await self._listed()
        self._interval(30)
        record.dirty = True  # e.g. its timer was cancelled by hand
        await self._json("GET", self.URL)
        self.assertIsNotNone(record.rewalk_handle)
        self.assertEqual(self.scan.calls, 1)

    async def test_cleanup_cancels_a_pending_rewalk(self):
        make_png(self.output_dir / "a.png")
        record = await self._listed()
        self._interval(30)
        self.api.mark_root_dirty(self._output("b.png"))
        handle = record.rewalk_handle
        await self.api.cleanup_gallery_listings(web.Application())
        self.assertTrue(handle.cancelled())
        self.assertIsNone(record.rewalk_handle)
        self.assertEqual(self.scan.calls, 1)

    async def test_a_walk_cancelled_by_cleanup_starts_no_trailing_walk(self):
        make_png(self.output_dir / "a.png")
        record = await self._listed()
        self._interval(0)
        gate = self.scan.hold_next()
        self.api.mark_root_dirty(self._output("b.png"))
        await self.scan.wait_entered()
        self.api.mark_root_dirty(self._output("c.png"))
        self.assertTrue(record.dirty)
        await self.api.cleanup_gallery_listings(web.Application())
        gate.set()
        self.assertIsNone(record.flight)
        self.assertIsNone(record.rewalk_handle)
        self.assertEqual(self.api._listing_tasks, set())
        self.assertEqual(self.scan.calls, 2)

    async def test_a_warm_up_after_cleanup_listens_again(self):
        make_png(self.output_dir / "a.png")
        self._interval(0)
        await self._warmed()
        await self.api.cleanup_gallery_listings(web.Application())
        await self._warmed()
        record = self._record()
        self.api.mark_root_dirty(self._output("b.png"))
        await self._settle()
        self.assertEqual(len(record.files), 2)

    async def test_a_root_removed_from_the_roots_is_never_walked_again(self):
        make_png(self.output_dir / "a.png")
        record = await self._listed()
        self._interval(30)
        self.api.mark_root_dirty(self._output("b.png"))
        handle = record.rewalk_handle
        self.assertIsNotNone(handle)
        self.api.reset_gallery_roots_cache()  # e.g. the gallery root changed
        self.assertTrue(handle.cancelled())
        self.assertIsNone(record.rewalk_handle)
        self.assertFalse(record.dirty)
        self._interval(0)
        # The monitor still watches the old root until ComfyUI restarts.
        self.api.mark_root_dirty(self._output("c.png"))
        self.api._delayed_walk(self.output_dir, record)  # a timer that raced
        self.assertIsNone(record.flight)
        self.assertIsNone(record.files)
        self.assertEqual(self.scan.calls, 1)

    async def test_a_walk_running_when_the_roots_change_gets_no_trailing_walk(self):
        make_png(self.output_dir / "a.png")
        record = await self._listed()
        self._interval(0)
        gate = self.scan.hold_next()
        self.api.mark_root_dirty(self._output("b.png"))
        await self.scan.wait_entered()
        self.api.reset_gallery_roots_cache()
        self.api.mark_root_dirty(self._output("c.png"))
        gate.set()
        await self._settle()
        self.assertEqual(self.scan.calls, 2)
        self.assertIsNone(record.files)  # the running walk was discarded
        self.assertFalse(record.dirty)

    async def test_a_root_read_again_after_the_roots_change_listens_again(self):
        make_png(self.output_dir / "a.png")
        record = await self._listed()
        self.api.reset_gallery_roots_cache()
        record = await self._listed()  # still configured: read and walked
        self._interval(0)
        self.api.mark_root_dirty(self._output("b.png"))
        await self._settle()
        self.assertEqual(len(record.files), 2)

    async def test_a_cancelled_walk_starts_no_trailing_walk(self):
        make_png(self.output_dir / "a.png")
        record = await self._listed()
        self._interval(0)
        gate = self.scan.hold_next()
        self.api.mark_root_dirty(self._output("b.png"))
        await self.scan.wait_entered()
        self.api.mark_root_dirty(self._output("c.png"))
        (task,) = self.api._listing_tasks
        seq = record.walk_seq
        task.cancel()  # e.g. the loop shutting down without on_cleanup
        await asyncio.gather(task, return_exceptions=True)
        gate.set()
        await self._settle()
        self.assertTrue(task.cancelled())
        self.assertEqual(record.walk_seq, seq)  # no walk started after it
        self.assertEqual(self.scan.calls, 2)
        self.assertIsNone(record.flight)
        self.assertIsNone(record.rewalk_handle)
        self.assertEqual(self.api._listing_tasks, set())

    # -- settled outputs added without a walk (live test round) -----------

    def _walked(self):
        """The listing a full walk of the root gives now (not counted)."""
        return self.scan.real(self.output_dir)

    def _at(self, name, mtime):
        """A new output on disk with *mtime*, spelled as the monitor reports it."""
        path = make_png(self.output_dir / name)
        os.utime(path, (mtime, mtime))
        return os.path.realpath(path)

    def _hold_adds(self):
        """Hold each add batch in the executor until the returned gate is set."""
        entered, gate, batches = threading.Event(), threading.Event(), []
        real = images_module._patch_listing

        def held(*args):
            batches.append(list(args[4]))
            entered.set()
            gate.wait(_HANG_LIMIT)
            return real(*args)

        patcher = patch.object(images_module, "_patch_listing", held)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(gate.set)
        return entered, gate, batches

    async def _entered(self, event):
        loop = asyncio.get_running_loop()
        if not await loop.run_in_executor(None, event.wait, _HANG_LIMIT):
            raise AssertionError("the add never reached the executor")

    def _no_walk_pending(self, record):
        self.assertFalse(record.dirty)
        self.assertIsNone(record.flight)
        self.assertIsNone(record.rewalk_handle)

    async def test_a_settled_output_is_added_without_a_walk(self):
        self._at("a.png", 1000)
        record = await self._listed()
        self._interval(0)
        self.api.output_settled(self._at("b.png", 2000))
        self.assertIsNone(record.flight)
        await self._settle()
        self.assertEqual(self.scan.calls, 1)
        self.assertEqual(record.patched, 1)
        self._no_walk_pending(record)
        self.assertEqual(record.files, self._walked())
        self.assertEqual(str(record.files[0][0]), str(self.output_dir / "b.png"))
        _, data = await self._json("GET", self.URL)
        self.assertEqual(data["total"], 2)
        self.assertEqual(self.scan.calls, 1)

    async def test_adds_land_where_a_walk_lists_them_and_name_new_folders(self):
        for index, name in enumerate(("x/a.png", "x/y/b.png", "c.png", "z/d.png")):
            self._at(name, 1000 + 100 * index)
        record = await self._listed()
        old_folders = record.folders
        for name, mtime in (
            ("x/y/n1.png", 1150),
            ("new/deep/n2.png", 1250),
            ("n3.png", 50),
            ("x/n4.webp", 9000),
        ):
            make_png(self.output_dir / name.replace(".webp", ".png"))
            if name.endswith(".webp"):
                os.replace(self.output_dir / "x" / "n4.png", self.output_dir / name)
            os.utime(self.output_dir / name, (mtime, mtime))
            self.api.output_settled(os.path.realpath(self.output_dir / name))
        await self._settle()
        self.assertEqual(self.scan.calls, 1)
        walked = self._walked()
        self.assertEqual(record.files, walked)
        self.assertEqual(
            [str(path) for path, _ in record.files], [str(path) for path, _ in walked]
        )
        expected = gallery_listing.folder_sets(self.output_dir, walked)
        self.assertEqual((record.folders, record.folders_with_ancestors), expected)
        self.assertIsNot(record.folders, old_folders)
        _, data = await self._json("GET", "/prompt_manager/gallery/subfolders")
        self.assertIn(os.path.join("new", "deep"), data["subfolders"])

    async def test_a_rewritten_output_moves_to_its_new_place(self):
        self._at("a.png", 1000)
        self._at("b.png", 2000)
        record = await self._listed()
        self.api.output_settled(self._at("a.png", 3000))
        await self._settle()
        self.assertEqual(self.scan.calls, 1)
        self.assertEqual(record.files, self._walked())
        self.assertEqual([path.name for path, _ in record.files], ["a.png", "b.png"])

    async def test_a_repeated_report_changes_nothing(self):
        self._at("a.png", 1000)
        path = self._at("b.png", 2000)
        record = await self._listed()
        self.api.output_settled(path)
        await self._settle()
        files, patched = record.files, record.patched
        with patch.object(self.api, "_schedule_workspace_warm") as warm:
            self.api.output_settled(path)
            await self._settle()
        warm.assert_not_called()
        self.assertIs(record.files, files)
        self.assertEqual((record.patched, self.scan.calls), (patched, 1))

    async def test_the_next_full_walk_keeps_the_patched_listing(self):
        self._at("a.png", 1000)
        record = await self._listed()
        self.api.output_settled(self._at("sub/b.png", 2000))
        await self._settle()
        patched = record.files
        self._age()
        with patch.object(self.api, "_schedule_workspace_warm") as warm:
            await self._json("GET", self.URL)  # the 30 s rule walks behind
            await self._settle()
        self.assertEqual(self.scan.calls, 2)
        self.assertIs(record.files, patched)  # equal: the walk kept it
        self.assertEqual(record.patched, 0)
        warm.assert_not_called()

    async def test_the_walks_filters_apply_to_an_add(self):
        self._at("a.png", 1000)
        record = await self._listed()
        files = record.files
        deep = os.path.join(*[f"d{i}" for i in range(images_module.MAX_SCAN_DEPTH + 1)])
        skipped = [
            self._at(".png", 2000),  # no suffix, as Path.suffix on 3.10-3.13
            self._at("notes.txt", 2000),
            self._at(os.path.join("thumbnails", "t.png"), 2000),
            self._at(os.path.join("x", "thumbnails", "t.png"), 2000),
            self._at(os.path.join(deep, "too-deep.png"), 2000),
        ]
        for path in skipped:
            self.api.output_settled(path)
        await self._settle()
        self.assertIs(record.files, files)
        self.assertEqual(record.files, self._walked())
        self._no_walk_pending(record)
        self.assertEqual(self.scan.calls, 1)
        # The deepest listed folder and a "..png" name are listed by a walk.
        deepest = os.path.join(*[f"d{i}" for i in range(images_module.MAX_SCAN_DEPTH)])
        for path in (
            self._at("..png", 3000),
            self._at(os.path.join(deepest, "deepest.png"), 4000),
        ):
            self.api.output_settled(path)
        await self._settle()
        self.assertEqual(record.files, self._walked())
        self.assertEqual(len(record.files), 3)
        self.assertEqual(self.scan.calls, 1)

    async def test_a_symlinked_output_is_not_added(self):
        self._at("a.png", 1000)
        record = await self._listed()
        target = make_png(self.comfy_dir / "elsewhere.png")
        link = self.output_dir / "link.png"
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are not available")
        files = record.files
        self.api.output_settled(str(link))  # as an unresolved report would name it
        await self._settle()
        self.assertIs(record.files, files)
        self.assertEqual(record.files, self._walked())
        self._no_walk_pending(record)

    async def _assert_walks_instead(self, record, report):
        self._interval(0)
        self.api.output_settled(report)
        await self._settle()
        self.assertEqual(self.scan.calls, 2)
        self.assertEqual(record.patched, 0)
        self.assertEqual(record.files, self._walked())

    async def test_a_tie_in_mtime_walks_instead(self):
        self._at("a.png", 1000)
        record = await self._listed()
        await self._assert_walks_instead(record, self._at("b.png", 1000))

    async def test_a_report_that_cannot_be_read_walks_instead(self):
        self._at("a.png", 1000)
        record = await self._listed()
        gone = self._at("gone.png", 2000)
        os.unlink(gone)
        await self._assert_walks_instead(record, gone)

    async def test_a_listing_at_the_cap_walks_instead(self):
        self._at("a.png", 1000)
        record = await self._listed()
        with patch.object(images_module, "MAX_GALLERY_FILES", 1):
            self._interval(0)
            self.api.output_settled(self._at("b.png", 2000))
            await self._settle()
        self.assertEqual((self.scan.calls, record.patched), (2, 0))
        self.assertEqual(len(record.files), 1)  # the walk is capped too

    async def test_a_path_that_cannot_be_sliced_walks_instead(self):
        self._at("a.png", 1000)
        record = await self._listed()
        self._at("b.png", 2000)
        report = os.path.join(os.path.realpath(self.output_dir), ".", "b.png")
        await self._assert_walks_instead(record, report)

    async def test_an_on_disk_spelling_mismatch_walks_instead(self):
        self._at("a.png", 1000)
        record = await self._listed()
        report = self._at("b.png", 2000)
        with patch.object(images_module, "_on_disk_path", return_value="/x/B.png"):
            await self._assert_walks_instead(record, report)

    async def test_an_unexpected_add_failure_walks_instead(self):
        self._at("a.png", 1000)
        record = await self._listed()
        report = self._at("b.png", 2000)
        with patch.object(images_module, "_patch_listing", side_effect=ValueError):
            with self.assertLogs(self.api.logger, "WARNING"):
                await self._assert_walks_instead(record, report)
        self.assertIsNone(record.add_task)

    async def test_a_report_during_a_walk_gets_the_trailing_walk(self):
        self._at("a.png", 1000)
        record = await self._listed()
        self._interval(0)
        self._age()
        gate = self.scan.hold_next()
        await self._json("GET", self.URL)
        await self.scan.wait_entered()  # listed already: misses b.png
        self.api.output_settled(self._at("b.png", 2000))
        self.assertTrue(record.dirty)
        self.assertEqual(record.pending_adds, [])
        gate.set()
        await self._settle()
        self.assertEqual(self.scan.calls, 3)
        self.assertEqual(record.files, self._walked())

    async def test_a_walk_started_during_an_add_lists_it_and_the_add_is_dropped(self):
        self._at("a.png", 1000)
        record = await self._listed()
        files = record.files
        entered, gate, _ = self._hold_adds()
        self.api.output_settled(self._at("b.png", 2000))
        await self._entered(entered)
        walk = self.scan.hold_next()
        rescan = asyncio.ensure_future(
            self._json("POST", "/prompt_manager/gallery/rescan")
        )
        await self.scan.wait_entered()
        add = record.add_task
        gate.set()
        await asyncio.wait_for(asyncio.shield(add), _HANG_LIMIT)
        # The add ended while the Rescan walk runs: not stored over it.
        self.assertIs(record.files, files)
        self.assertEqual(record.patched, 0)
        walk.set()
        status, data = await rescan
        await self._settle()
        self.assertEqual((status, data["total"]), (200, 2))
        self.assertEqual(self.scan.calls, 2)
        self.assertEqual(record.files, self._walked())
        self._no_walk_pending(record)

    async def test_a_roots_change_during_an_add_drops_it(self):
        self._at("a.png", 1000)
        record = await self._listed()
        entered, gate, _ = self._hold_adds()
        self.api.output_settled(self._at("b.png", 2000))
        await self._entered(entered)
        self.api.reset_gallery_roots_cache()
        gate.set()
        await self._settle()
        self.assertIsNone(record.files)
        self.assertEqual((self.scan.calls, record.patched), (1, 0))
        self._no_walk_pending(record)

    async def test_an_invalidation_during_an_add_drops_it(self):
        self._at("a.png", 1000)
        record = await self._listed()
        entered, gate, batches = self._hold_adds()
        self.api.output_settled(self._at("b.png", 2000))
        await self._entered(entered)
        self.api.output_settled(self._at("c.png", 3000))  # waits for the batch
        self.api.invalidate_gallery_cache()
        gate.set()
        await self._settle()
        self.assertEqual(len(batches), 1)  # the dropped listing gets no second
        self.assertIsNone(record.files)  # the next read walks
        self.assertEqual((self.scan.calls, record.patched), (1, 0))
        self._no_walk_pending(record)
        _, data = await self._json("GET", self.URL)
        self.assertEqual((data["total"], self.scan.calls), (3, 2))

    async def test_an_add_failing_after_a_drop_walks_nothing(self):
        self._at("a.png", 1000)
        record = await self._listed()

        def drop_then_fail(*_args):
            self.api.invalidate_gallery_cache()  # a settings save, off the loop
            raise ValueError("boom")

        with patch.object(images_module, "_patch_listing", drop_then_fail):
            with self.assertLogs(self.api.logger, "WARNING"):
                self.api.output_settled(self._at("b.png", 2000))
                await self._settle()
        self.assertIsNone(record.files)
        self.assertEqual(self.scan.calls, 1)
        self._no_walk_pending(record)

    async def test_cleanup_during_an_add_cancels_it(self):
        self._at("a.png", 1000)
        record = await self._listed()
        files = record.files
        entered, gate, _ = self._hold_adds()
        self.api.output_settled(self._at("b.png", 2000))
        await self._entered(entered)
        self.api.output_settled(self._at("c.png", 3000))
        cleanup = asyncio.ensure_future(
            self.api.cleanup_gallery_listings(web.Application())
        )
        await asyncio.sleep(0)
        gate.set()
        await asyncio.wait_for(cleanup, _HANG_LIMIT)
        self.assertIs(record.files, files)
        self.assertIsNone(record.add_task)
        self.assertEqual(self.api._listing_tasks, set())
        self._no_walk_pending(record)

    async def test_a_removed_root_gets_no_add(self):
        self._at("a.png", 1000)
        record = await self._listed()
        self.api.reset_gallery_roots_cache()
        self.api.output_settled(self._at("b.png", 2000))
        await self._settle()
        self.assertIsNone(record.files)
        self.assertEqual(self.api._listing_tasks, set())
        self.assertEqual(self.scan.calls, 1)

    async def test_a_burst_is_added_in_batches_with_one_warm_build_each(self):
        self._at("a.png", 1000)
        await self.api.warm_gallery_listings(web.Application())
        await self._settle()
        record = self._record()
        entered, gate, batches = self._hold_adds()
        built = []
        real = self.api._build_workspace_caches

        def build(listings):
            built.append(len(listings[0][2]))
            real(listings)

        with patch.object(self.api, "_build_workspace_caches", side_effect=build):
            self.api.output_settled(self._at("b.png", 2000))
            await self._entered(entered)
            for index in range(4):  # land while the first batch is placed
                self.api.output_settled(self._at(f"c{index}.png", 3000 + index))
            gate.set()
            await self._settle()
        self.assertEqual([len(batch) for batch in batches], [1, 4])
        self.assertEqual(self.scan.calls, 1)
        self.assertEqual(record.patched, 2)
        self.assertEqual(record.files, self._walked())
        self.assertEqual(built[-1], 6)
        self.assertLessEqual(len(built), 2)
        self.assertTrue(self._workspace_ready())

    async def _workspace_pages(self, **params):
        """Every workspace order's full first page, as file names."""
        pages = {}
        for order in images_module.WORKSPACE_ORDERS:
            query = "&".join(f"{k}={v}" for k, v in {"order": order, **params}.items())
            _, data = await self._json(
                "GET", f"{self.URL}?mode=workspace&limit=100&{query}"
            )
            pages[order] = [image["filename"] for image in data["images"]]
        return pages

    def _drop_workspace_caches(self):
        self.api._workspace_listed_cache = {}
        self.api._workspace_order_cache = {}
        self.api._workspace_card_cache = {}
        self.api._favorite_listing_cache = {}

    async def test_workspace_pages_after_adds_match_a_cold_rebuild(self):
        for index, name in enumerate(("a.png", "x/b.png", "x/c.mp4", "d.png")):
            path = self.output_dir / name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b"x")
            os.utime(path, (1000 + index, 1000 + index))
        await self._warmed()
        for name in ("x/b.png", "d.png"):
            status, _ = await self._json(
                "PUT",
                "/prompt_manager/images/favorite",
                json={"root_index": 0, "path": name},
            )
            self.assertEqual(status, 200)
        await self._workspace_pages()
        await self._workspace_pages(favorites=1)
        self.api.output_settled(self._at("y/new.png", 5000))  # a new folder
        self.api.output_settled(self._at("x/b.png", 6000))  # rewritten favorite
        await self._settle()
        self.assertEqual(self.scan.calls, 1)
        patched = (
            await self._workspace_pages(),
            await self._workspace_pages(favorites=1),
        )
        self._drop_workspace_caches()
        cold = (await self._workspace_pages(), await self._workspace_pages(favorites=1))
        self.assertEqual(patched, cold)
        self.assertEqual(patched[0]["newest"][:2], ["b.png", "new.png"])
        self.assertEqual(sorted(patched[1]["newest"]), ["b.png", "d.png"])

    async def test_a_warm_build_after_an_add_builds_only_the_added_values(self):
        for index in range(300):
            path = self.output_dir / f"f{index:03d}.png"
            path.write_bytes(b"x")
            os.utime(path, (1000 + index, 1000 + index))
        await self._warmed()
        record = self._record()
        listed, keys = [], []
        real_listed, real_key = images_module._listed, images_module._newest_key

        def counted_listed(*args):
            listed.append(args[0].name)
            return real_listed(*args)

        def counted_key(entry):
            keys.append(entry)
            return real_key(entry)

        with (
            patch.object(images_module, "_listed", counted_listed),
            patch.object(images_module, "_newest_key", counted_key),
            patch.object(
                images_module,
                "_favorite_relative_key",
                wraps=images_module._favorite_relative_key,
            ) as favorite_keys,
        ):
            self.api.output_settled(self._at("new.png", 9000))
            await self._settle()
        self.assertEqual(self.scan.calls, 1)
        self.assertTrue(self._workspace_ready())
        self.assertEqual(listed, ["new.png"])
        self.assertLess(len(keys), 20)  # bisection, not a sort of 301 files
        self.assertLess(favorite_keys.call_count, 10)  # not one per file
        self.assertEqual(
            self.api._workspace_order([(0, self.output_dir, record.files)], "newest")[
                0
            ].path.name,
            "new.png",
        )

    async def test_a_rewritten_output_drops_its_old_card_facts(self):
        self._at("a.png", 1000)
        self._at("b.png", 2000)
        await self._warmed()
        await self._json("GET", f"{self.URL}?mode=workspace&limit=10")
        key = (0, str(self.output_dir))
        before = set(self.api._workspace_card_cache.get(key, {}))
        self.assertIn(Path(self._at("a.png", 1000)).name, {p.name for p, _ in before})
        self.api.output_settled(self._at("a.png", 3000))
        await self._settle()
        after = self.api._workspace_card_cache.get(key, {})
        self.assertNotIn((self.output_dir / "a.png", 1000.0), after)
        self.assertIn((self.output_dir / "b.png", 2000.0), after)

    def _synthetic(self, root, count, start=1000.0):
        return [(root / f"f{i:04d}.png", start + i) for i in range(count)][::-1]

    def test_the_newest_order_sorts_again_when_it_missed_a_step(self):
        root = self.output_dir
        files = self._synthetic(root, 50)
        listings = [(0, root, files)]
        self.api._workspace_orders(listings)
        one = gallery_listing.insert_entry(files, (root / "n1.png", 5000.0), 100)
        self.api._workspace_listed(0, root, one)  # values follow, the order not
        two = gallery_listing.insert_entry(one, (root / "n2.png", 6000.0), 100)
        newest = self.api._workspace_orders([(0, root, two)])["newest"]
        self.assertEqual([entry.path for entry in newest], [p for p, _m in two])

    def test_the_newest_order_follows_one_changed_root_of_two(self):
        first, second = self.output_dir, self.comfy_dir / "second"
        files = self._synthetic(first, 30)
        others = self._synthetic(second, 30, start=1000.5)
        self.api._workspace_orders([(0, first, files), (1, second, others)])
        patched = gallery_listing.insert_entry(others, (second / "n.png", 1010.25), 100)
        with patch.object(
            images_module, "_newest_key", wraps=images_module._newest_key
        ) as keys:
            newest = self.api._workspace_orders(
                [(0, first, files), (1, second, patched)]
            )["newest"]
        self.assertLess(keys.call_count, 20)
        expected = sorted(
            [(p, m, 0) for p, m in files] + [(p, m, 1) for p, m in patched],
            key=lambda item: -item[1],
        )
        self.assertEqual(
            [entry.path for entry in newest], [path for path, _m, _i in expected]
        )

    def test_the_on_disk_spelling_is_read_where_names_fold_case(self):
        read = images_module._on_disk_path
        self.assertIsNone(read(__file__, platform="linux"))
        fake_fcntl = MagicMock(F_GETPATH=50)
        fake_fcntl.fcntl.return_value = b"/Out/Sub/A.png" + bytes(10)
        fake_nt = MagicMock()
        fake_nt._getfinalpathname.return_value = "\\\\?\\C:\\Out\\A.png"
        with patch.dict(sys.modules, {"fcntl": fake_fcntl, "nt": fake_nt}):
            self.assertEqual(read(__file__, platform="darwin"), "/Out/Sub/A.png")
            self.assertEqual(read(__file__, platform="win32"), "\\\\?\\C:\\Out\\A.png")
        self.assertEqual(fake_fcntl.fcntl.call_args.args[1], 50)
        fake_nt._getfinalpathname.assert_called_once_with(__file__)

    def test_a_fifo_named_like_an_image_never_blocks_the_spelling_read(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("no FIFOs here")
        fifo = self.output_dir / "pipe.png"
        os.mkfifo(fifo)
        fake = MagicMock(F_GETPATH=50)
        fake.fcntl.return_value = os.fsencode(str(fifo))
        done = []

        def read():
            with patch.dict(sys.modules, {"fcntl": fake}):
                done.append(images_module._on_disk_path(fifo, platform="darwin"))

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        reader.join(_HANG_LIMIT)
        if reader.is_alive():
            # Unblock the open so the thread ends, then fail.
            with open(fifo, "wb"):
                pass
            self.fail("opening the FIFO blocked")
        self.assertEqual(done, [str(fifo)])

    async def test_a_folder_the_walk_cannot_list_gets_no_add(self):
        if os.name == "nt" or not hasattr(os, "geteuid") or os.geteuid() == 0:
            self.skipTest("needs POSIX permissions and a non-root user")
        self._at("a.png", 1000)
        record = await self._listed()
        report = self._at(os.path.join("locked", "b.png"), 2000)
        locked = self.output_dir / "locked"
        os.chmod(locked, 0o300)  # enter but not list
        try:
            self.assertEqual(self._walked(), record.files)  # the walk skips it
            files = record.files
            self.api.output_settled(report)
            await self._settle()
        finally:
            os.chmod(locked, 0o700)
        self.assertIs(record.files, files)
        self._no_walk_pending(record)

    async def test_a_folder_named_like_an_image_is_not_added(self):
        self._at("a.png", 1000)
        record = await self._listed()
        folder = self.output_dir / "folder.png"
        folder.mkdir()
        files = record.files
        self.api.output_settled(os.path.realpath(folder))
        await self._settle()
        self.assertIs(record.files, files)
        self._no_walk_pending(record)

    async def test_the_filters_read_the_stored_spelling(self):
        # Reported as thumbnails/, stored as Thumbnails/: a walk lists it, so
        # the add must not skip it as a preview folder; the spelling differs,
        # so the root is walked.
        self._at("a.png", 1000)
        record = await self._listed()
        report = self._at(os.path.join("thumbnails", "t.png"), 2000)
        stored = os.path.join(str(self.output_dir), "Thumbnails", "t.png")
        with patch.object(images_module, "_on_disk_path", return_value=stored):
            await self._assert_walks_instead(record, report)

    def test_a_stale_read_racing_a_newer_build_sorts_in_full(self):
        root = self.output_dir
        f0 = self._synthetic(root, 50)
        f1 = gallery_listing.insert_entry(f0, (root / "n1.png", 5000.0), 100)
        f2 = gallery_listing.insert_entry(f1, (root / "n2.png", 6000.0), 100)
        self.api._workspace_orders([(0, root, f0)])
        key = (0, str(root))
        built_from_f0 = self.api._workspace_listed_cache[key]
        real = self.api._workspace_listed
        armed = [True]

        def listed(index, part_root, files):
            values = real(index, part_root, files)
            if armed[0] and files is f1:
                armed[0] = False
                # Another executor thread read the f0 values before this one
                # stored f1's, and stores its f2 step (from f0) only now.
                self.api._workspace_listed_cache[key] = built_from_f0
                real(index, part_root, f2)
            return values

        with patch.object(self.api, "_workspace_listed", side_effect=listed):
            stale = self.api._workspace_orders([(0, root, f1)])["newest"]
        self.assertEqual([entry.path for entry in stale], [p for p, _m in f1])
        for files in (f1, f2, f1, f2):
            newest = self.api._workspace_orders([(0, root, files)])["newest"]
            self.assertEqual([entry.path for entry in newest], [p for p, _m in files])

    def test_a_roots_change_during_an_order_build_sorts_in_full(self):
        root = self.output_dir
        f0 = self._synthetic(root, 20)
        f1 = gallery_listing.insert_entry(f0, (root / "n1.png", 5000.0), 100)
        self.api._workspace_orders([(0, root, f0)])
        real = self.api._workspace_listed

        def listed(index, part_root, files):
            values = real(index, part_root, files)
            # A roots reset in another thread, between this call's two cache
            # reads: the per-file values are gone, the order not yet.
            self.api._workspace_listed_cache = {}
            return values

        with patch.object(self.api, "_workspace_listed", side_effect=listed):
            newest = self.api._workspace_orders([(0, root, f1)])["newest"]
        self.assertEqual([entry.path for entry in newest], [p for p, _m in f1])

    def test_the_favorite_listing_step_rebuilds_when_it_cannot_follow(self):
        root = self.output_dir
        listing = {"a.png": "a.png"}
        step = images_module._favorite_listing_step
        self.assertEqual(
            step(root, listing, [(root / "b.png", 2.0)], []),
            {"a.png": "a.png", "b.png": "b.png"},
        )
        self.assertEqual(listing, {"a.png": "a.png"})  # never changed in place
        # A removed file not added again, or a key another spelling holds.
        self.assertIsNone(step(root, listing, [], [(root / "a.png", 1.0)]))
        with patch.object(
            images_module, "_favorite_relative_key", side_effect=str.lower
        ):
            self.assertIsNone(step(root, listing, [(root / "A.png", 2.0)], []))

    async def test_monitor_walks_during_a_workspace_build_add_one_more_build(self):
        make_png(self.output_dir / "a.png")
        release, entered = threading.Event(), threading.Event()
        built = []
        real = self.api._build_workspace_caches

        def build(listings):
            built.append(len(listings[0][2]))
            if len(built) == 1:
                entered.set()
                release.wait(_HANG_LIMIT)
            real(listings)

        self.addCleanup(release.set)
        self._interval(0)
        with patch.object(self.api, "_build_workspace_caches", side_effect=build):
            await self.api.warm_gallery_listings(web.Application())
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, entered.wait, _HANG_LIMIT)
            for name in ("b.png", "c.png", "d.png"):  # three monitor walks
                self.api.mark_root_dirty(self._output(name))
                while self.api._listing_tasks:
                    await asyncio.gather(*list(self.api._listing_tasks))
                    await asyncio.sleep(0)
            release.set()
            await self._settle()
        self.assertEqual(built, [1, 4])
        self.assertEqual(self.scan.calls, 4)
        self.assertTrue(self._workspace_ready())


class TestGalleryListingHostHooks(unittest.IsolatedAsyncioTestCase):
    async def test_root_registers_the_warm_up_and_its_cleanup(self):
        try:
            from test_init_package import fake_server_module, load_package
        except ImportError:
            from tests.test_init_package import fake_server_module, load_package

        with tempfile.TemporaryDirectory() as directory:
            server = fake_server_module()
            server.PromptServer.instance.app = web.Application()
            with load_package(
                "gallery_listing_hooks_pkg",
                str(Path(directory) / "db.sqlite"),
                directory,
                server,
            ) as (package, host):
                self.assertIn(package.api.warm_gallery_listings, host.app.on_startup)
                self.assertIn(package.api.cleanup_gallery_listings, host.app.on_cleanup)

    async def test_root_connects_the_monitor_to_the_listing(self):
        try:
            from test_init_package import fake_server_module, load_package
        except ImportError:
            from tests.test_init_package import fake_server_module, load_package

        with tempfile.TemporaryDirectory() as directory:
            server = fake_server_module()
            server.PromptServer.instance.app = web.Application()
            with load_package(
                "gallery_listing_monitor_pkg",
                str(Path(directory) / "db.sqlite"),
                directory,
                server,
            ) as (package, host):
                monitor = package._global_image_monitor
                self.assertEqual(
                    monitor.output_listener, package.api.notify_output_settled
                )
                # The first cleanup hook stops the monitor's hand-offs.
                await host.app.on_cleanup[0](host.app)
                self.assertIsNone(monitor.output_listener)
