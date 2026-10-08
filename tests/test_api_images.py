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
from unittest.mock import MagicMock, patch

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

        self._output_tmp = tempfile.TemporaryDirectory()
        self.output_dir = Path(os.path.realpath(self._output_tmp.name))

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
    """GET /prompt_manager/images/generate-thumbnails/progress (SSE)."""

    async def test_three_pngs_yield_three_thumbnails_and_events(self):
        for name in ("a", "b", "c"):
            make_png(self.output_dir / f"{name}.png", size=(64, 64))
        calls = self._spy_executor()

        resp = await self.client.request("GET", f"{self.PROGRESS_URL}?quality=low")

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

        resp = await self.client.request("GET", self.PROGRESS_URL)

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

        resp = await self.client.request("GET", self.PROGRESS_URL)
        await resp.text()

        self.assertNotIn("Access-Control-Allow-Origin", resp.headers)

    async def test_existing_newer_thumbnail_is_skipped(self):
        make_png(self.output_dir / "one.png")
        first = await self.client.request("GET", self.PROGRESS_URL)
        await first.text()

        second = await self.client.request("GET", self.PROGRESS_URL)

        complete = dict(parse_sse(await second.text()))["complete"]
        self.assertEqual(complete["skipped"], 1)
        self.assertEqual(complete["count"], 0)

    async def test_empty_output_dir_completes_with_zero(self):
        resp = await self.client.request("GET", self.PROGRESS_URL)

        events = parse_sse(await resp.text())
        self.assertEqual(events[-1][0], "complete")
        self.assertEqual(events[-1][1]["total_images"], 0)

    async def test_missing_output_dir_sends_error_event(self):
        self.api._find_comfyui_output_dir = lambda: None

        resp = await self.client.request("GET", self.PROGRESS_URL)

        events = parse_sse(await resp.text())
        self.assertEqual(events[-1][0], "error")

    async def test_unknown_quality_falls_back_to_medium(self):
        make_png(self.output_dir / "one.png", size=(400, 400))

        resp = await self.client.request("GET", f"{self.PROGRESS_URL}?quality=huge")
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

        def spy(limit):
            seen.append(limit)
            return original(limit)

        self.api.db.get_recent_images = spy
        return seen

    async def test_absent_params_use_bounded_default(self):
        seen = self._spy_db()

        resp = await self.client.request("GET", "/prompt_manager/images/recent")

        data = await resp.json()
        self.assertEqual(data["pagination"]["limit"], 50)
        self.assertEqual(seen, [50])

    async def test_huge_offset_is_clamped_before_reaching_db(self):
        seen = self._spy_db()

        resp = await self.client.request(
            "GET", "/prompt_manager/images/recent?limit=10&offset=999999999999"
        )

        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertLessEqual(seen[0], images_module.MAX_RECENT_IMAGES_WINDOW)
        self.assertEqual(
            data["pagination"]["offset"], images_module.MAX_RECENT_IMAGES_WINDOW - 10
        )

    async def test_float_string_limit_is_400(self):
        resp = await self.client.request(
            "GET", "/prompt_manager/images/recent?limit=1e9"
        )

        self.assertEqual(resp.status, 400)

    async def test_zero_limit_becomes_one(self):
        seen = self._spy_db()

        await self.client.request("GET", "/prompt_manager/images/recent?limit=0")

        self.assertEqual(seen, [1])


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


if __name__ == "__main__":
    unittest.main()
