"""
Route tests for the image API (py/api/images.py).

Uses aiohttp's test client against a real PromptManagerAPI wired to a
temporary SQLite database and a temporary output directory.
"""

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


if __name__ == "__main__":
    unittest.main()
