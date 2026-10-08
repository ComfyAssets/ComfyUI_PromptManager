"""
Route tests for the image API (py/api/images.py).

Uses aiohttp's test client against a real PromptManagerAPI wired to a
temporary SQLite database and a temporary output directory.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ComfyUI-only modules are stubbed so the API can be imported standalone.
sys.modules.setdefault("folder_paths", MagicMock())
sys.modules.setdefault("server", MagicMock())

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import AioHTTPTestCase  # noqa: E402
from PIL import Image  # noqa: E402

from database.operations import PromptDatabase  # noqa: E402
import py.api.images as images_module  # noqa: E402
from py.api import PromptManagerAPI  # noqa: E402
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
        self._temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self._temp_db.close()

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

        self.api = PromptManagerAPI()
        self.api.db = PromptDatabase(self._temp_db.name)
        self.api._get_all_output_dirs = lambda: [self.output_dir]
        self.api._find_comfyui_output_dir = lambda: str(self.output_dir)
        self.api.add_routes(routes)
        app.router.add_routes(routes)
        return app

    async def tearDownAsync(self):
        self._anchor_patch.stop()
        # Windows refuses to unlink a database that still has open handles.
        self.api.db.close_all()
        for path in (
            self._temp_db.name,
            self._temp_db.name + "-wal",
            self._temp_db.name + "-shm",
        ):
            if os.path.exists(path):
                os.unlink(path)
        self._output_tmp.cleanup()

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

    def _make_symlink(self, target, link):
        """Create a symlink or skip the test when the platform refuses."""
        try:
            os.symlink(str(target), str(link))
        except (OSError, NotImplementedError, AttributeError) as exc:
            self.skipTest(f"symlinks unavailable: {exc}")


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
            self.assertEqual(bad_index.status, 200)


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

    def _spy_executor(self):
        """Record the __name__ of every callable sent to _run_in_executor."""
        original = self.api._run_in_executor
        calls = []

        async def spy(func, *args, **kwargs):
            calls.append(getattr(func, "__name__", repr(func)))
            return await original(func, *args, **kwargs)

        self.api._run_in_executor = spy
        return calls

    def _thumb_path(self, name, suffix=".png"):
        return self.output_dir / "thumbnails" / f"{name}_thumb{suffix}"


class TestThumbnailProgressStream(ThumbnailTestCase):
    """POST /prompt_manager/images/generate-thumbnails/progress (SSE)."""

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
        # PIL work ran in the executor, once per file, plus the scan.
        self.assertEqual(calls.count("_generate_one"), 3)
        self.assertIn("_thumbnail_targets", calls)

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
        self.api._find_comfyui_output_dir = lambda: None

        resp = await self.client.request("POST", self.PROGRESS_URL)

        events = parse_sse(await resp.text())
        self.assertEqual(events[-1][0], "error")

    async def test_unknown_quality_falls_back_to_medium(self):
        make_png(self.output_dir / "one.png", size=(400, 400))

        resp = await self.client.request("POST", f"{self.PROGRESS_URL}?quality=huge")
        await resp.text()

        with Image.open(self._thumb_path("one")) as thumb:
            self.assertEqual(max(thumb.size), 300)


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
        self.api._find_comfyui_output_dir = lambda: None

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

    def test_thumbnail_size_ignores_non_string_quality(self):
        self.assertEqual(self.api._thumbnail_size({"w": 99999}), (300, 300))
        self.assertEqual(self.api._thumbnail_size(None), (300, 300))
        self.assertEqual(self.api._thumbnail_size("low"), (150, 150))


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

    async def test_out_of_range_root_index_searches_all_roots(self):
        make_png(self.output_dir / "r.png")
        resp = await self.client.request(
            "GET", "/prompt_manager/images/serve/r.png?root=7"
        )
        self.assertEqual(resp.status, 200)

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
            with (
                patch.object(IntegrationConfig, "LORA_MANAGER_ENABLED", True),
                patch("py.lora_utils.find_lora_directories", return_value=[lora]),
                patch("py.lora_utils.get_lora_image_cache_dir", return_value=cache),
            ):
                resp = await self.client.request(
                    "GET", f"/prompt_manager/images/{image_id}/file"
                )
                self.assertEqual(resp.status, 200)

            with (
                patch.object(IntegrationConfig, "LORA_MANAGER_ENABLED", True),
                patch("py.lora_utils.find_lora_directories", side_effect=RuntimeError),
            ):
                resp = await self.client.request(
                    "GET", f"/prompt_manager/images/{image_id}/file"
                )
                self.assertEqual(resp.status, 403)


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
        ghost = self.output_dir / "ghost.png"
        with patch.object(
            images_module, "_iter_media_files", return_value=iter([ghost])
        ):
            found = self.api._scan_gallery_files_sync(self.output_dir)
        self.assertEqual(found, [])


class TestThumbnailUnits(ImageRouteCoverageCase):

    SIZE = (64, 64)

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
            ok = self.api._generate_one(src, self._dst("clip", ".jpg"), self.SIZE, True)
        with patch.object(self.api, "_generate_video_thumbnail", return_value=False):
            bad = self.api._generate_one(
                src, self._dst("clip", ".jpg"), self.SIZE, True
            )
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
        self.assertTrue(self._dst("photo", ".jpg").is_file())

    async def test_emit_sse_swallows_write_errors(self):
        response = MagicMock()
        response.write = AsyncMock(side_effect=ConnectionResetError("gone"))
        await self.api._emit_sse(response, "progress", {"n": 1})
        response.write.assert_awaited_once()

    async def test_progress_stream_reports_internal_failure(self):
        self.api._thumbnail_targets = _raise
        resp = await self.client.request(
            "POST", "/prompt_manager/images/generate-thumbnails/progress"
        )
        events = parse_sse(await resp.text())
        self.assertEqual(events[-1][0], "error")

    async def test_post_generate_reports_internal_failure(self):
        self.api._find_comfyui_output_dir = _raise
        status, _ = await self._json(
            "POST", "/prompt_manager/images/generate-thumbnails", json={}
        )
        self.assertEqual(status, 500)

    def _dst(self, name, suffix=".png"):
        return self.output_dir / "thumbnails" / f"{name}_thumb{suffix}"


class TestVideoThumbnailFallbacks(ImageRouteCoverageCase):
    """_generate_video_thumbnail degrades: cv2 -> ffmpeg -> placeholder."""

    SIZE = (48, 48)

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

    def test_cv2_cannot_open_or_read(self):
        with patch.dict(sys.modules, {"cv2": self._fake_cv2(opened=False)}):
            closed = self.api._generate_video_thumbnail(
                self.video, self.thumb, self.SIZE
            )
        with patch.dict(sys.modules, {"cv2": self._fake_cv2(read_ok=False)}):
            unread = self.api._generate_video_thumbnail(
                self.video, self.thumb, self.SIZE
            )
        self.assertEqual((closed, unread), (False, False))

    def test_cv2_crash_is_contained(self):
        cv2 = MagicMock()
        cv2.VideoCapture.side_effect = RuntimeError("driver crash")
        with patch.dict(sys.modules, {"cv2": cv2}):
            ok = self.api._generate_video_thumbnail(self.video, self.thumb, self.SIZE)
        self.assertFalse(ok)

    def test_ffmpeg_absent_yields_placeholder(self):
        with (
            patch.dict(sys.modules, {"cv2": None}),
            patch("subprocess.run", side_effect=FileNotFoundError("ffmpeg")),
        ):
            ok = self.api._generate_video_thumbnail(self.video, self.thumb, self.SIZE)
        self.assertTrue(ok)
        with Image.open(self.thumb) as img:
            self.assertEqual(img.size, self.SIZE)

    def test_ffmpeg_failure_yields_placeholder(self):
        failed = MagicMock(returncode=1, stderr="no stream")
        with (
            patch.dict(sys.modules, {"cv2": None}),
            patch("subprocess.run", return_value=failed),
        ):
            ok = self.api._generate_video_thumbnail(self.video, self.thumb, self.SIZE)
        self.assertTrue(ok)
        self.assertTrue(self.thumb.is_file())

    def test_ffmpeg_success(self):
        with (
            patch.dict(sys.modules, {"cv2": None}),
            patch("subprocess.run", return_value=MagicMock(returncode=0)) as run,
        ):
            ok = self.api._generate_video_thumbnail(self.video, self.thumb, self.SIZE)
        self.assertTrue(ok)
        self.assertEqual(run.call_args.args[0][0], "ffmpeg")

    def test_placeholder_failure_returns_false(self):
        with (
            patch.dict(sys.modules, {"cv2": None}),
            patch("subprocess.run", side_effect=FileNotFoundError),
            patch.object(
                images_module.Image, "new", side_effect=RuntimeError("no PIL")
            ),
        ):
            ok = self.api._generate_video_thumbnail(self.video, self.thumb, self.SIZE)
        self.assertFalse(ok)


class TestClearThumbnails(ImageRouteCoverageCase):

    URL = "/prompt_manager/images/clear-thumbnails"

    async def test_missing_output_dir_is_404(self):
        self.api._find_comfyui_output_dir = lambda: None
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
        self.api._find_comfyui_output_dir = _raise
        status, _ = await self._json("POST", self.URL)
        self.assertEqual(status, 500)


class TestImagePromptLookup(ImageRouteCoverageCase):

    URL = "/prompt_manager/images/prompt/"

    async def test_relative_path_resolves_against_output_dir(self):
        self._link_image(make_png(self.output_dir / "gen.png"), text="the prompt")
        status, data = await self._json("GET", f"{self.URL}gen.png")
        self.assertEqual(status, 200)
        self.assertTrue(data["success"])
        self.assertEqual(data["prompt"]["text"], "the prompt")
        self.assertEqual(data["prompt"]["image_path"], str(self.output_dir / "gen.png"))

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


class TestOutputScanHelpers(ImageAPITestCase):
    """Stable ids for filesystem entries, no symlink reads, stop on disconnect."""

    def test_output_entry_id_is_a_stable_digest_of_the_path(self):
        import hashlib

        output = Path(self.output_dir)
        media = output / "stable.png"
        make_png(media)
        entry = images_module._output_image_entry(media, output, 0)
        expected = hashlib.sha1(str(media).encode("utf-8")).hexdigest()[:16]
        self.assertEqual(entry["id"], expected)

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

    async def test_thumbnail_stream_stops_when_the_client_disconnects(self):
        generated = []

        def fake_generate(src, dst, size, is_video):
            generated.append(src)
            return {
                "action": "generated",
                "file": src.name,
                "dir": src.parent.name,
                "type": "image",
            }

        self.api._generate_one = fake_generate

        class GoneResponse:
            async def write(self, data):
                raise ConnectionResetError("client went away")

        targets = [(Path(f"/x/{i}.png"), Path(f"/x/t{i}.jpg"), False) for i in range(3)]
        await self.api._stream_thumbnails(GoneResponse(), targets, (256, 256))
        self.assertEqual(generated, [])
