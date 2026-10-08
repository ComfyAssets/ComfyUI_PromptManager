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
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ComfyUI-only modules are stubbed so the API can be imported standalone.
sys.modules.setdefault("folder_paths", MagicMock())
sys.modules.setdefault("server", MagicMock())

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import AioHTTPTestCase  # noqa: E402
from PIL import Image  # noqa: E402

from database.operations import PromptDatabase  # noqa: E402
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
            f"GET", f"/prompt_manager/images/serve/{encoded}"
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


if __name__ == "__main__":
    unittest.main()
