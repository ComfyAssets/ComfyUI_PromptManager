"""Tests for the core API module (py/api/__init__.py): helpers and base routes."""

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

import py.api as api_module  # noqa: E402
from database.operations import PromptDatabase  # noqa: E402
from py.api import PromptManagerAPI, _public_error, _public_path  # noqa: E402
from py.config import GalleryConfig  # noqa: E402

EXTRA_ROOTS_ENV = "PROMPT_MANAGER_EXTRA_GALLERY_ROOTS"


class FolderPathsFixture(unittest.TestCase):
    """Temp ComfyUI tree exposed through a stub folder_paths module."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.comfy_dir = Path(self.tmpdir) / "ComfyUI"
        self.output_dir = self.comfy_dir / "output"
        self.output_dir.mkdir(parents=True)

        self._orig_folder_paths = sys.modules.get("folder_paths")
        self.addCleanup(self._restore_folder_paths)
        self._orig_extra = os.environ.pop(EXTRA_ROOTS_ENV, None)
        self.addCleanup(self._restore_extra_env)

        self.install_folder_paths(base_path=str(self.comfy_dir))

    def install_folder_paths(self, **attrs):
        attrs.setdefault("get_output_directory", lambda: str(self.output_dir))
        sys.modules["folder_paths"] = types.SimpleNamespace(**attrs)

    def _restore_folder_paths(self):
        if self._orig_folder_paths is None:
            sys.modules.pop("folder_paths", None)
        else:
            sys.modules["folder_paths"] = self._orig_folder_paths

    def _restore_extra_env(self):
        if self._orig_extra is None:
            os.environ.pop(EXTRA_ROOTS_ENV, None)
        else:
            os.environ[EXTRA_ROOTS_ENV] = self._orig_extra


class TestPublicPath(FolderPathsFixture):

    def test_path_under_base_is_relative_posix(self):
        target = self.output_dir / "renders" / "a.png"
        self.assertEqual(_public_path(str(target)), "output/renders/a.png")

    def test_base_itself_is_dot(self):
        self.assertEqual(_public_path(str(self.comfy_dir)), ".")

    def test_path_outside_base_is_basename_only(self):
        secret = Path(self.tmpdir) / "secret" / "passwords.txt"
        secret.parent.mkdir()
        secret.write_text("x")
        self.assertEqual(_public_path(str(secret)), "passwords.txt")
        self.assertNotIn(self.tmpdir, _public_path(str(secret)))

    def test_path_object_accepted(self):
        self.assertEqual(_public_path(self.output_dir), "output")

    def test_falls_back_to_output_parent_without_base_path(self):
        self.install_folder_paths()  # no base_path attribute
        self.assertEqual(_public_path(str(self.output_dir / "x.png")), "output/x.png")

    def test_env_extra_root_keeps_its_own_name(self):
        extra = Path(self.tmpdir) / "nas" / "gallery"
        (extra / "sub").mkdir(parents=True)
        os.environ[EXTRA_ROOTS_ENV] = str(extra)
        self.assertEqual(_public_path(str(extra / "sub")), "gallery/sub")

    def test_without_folder_paths_returns_basename(self):
        sys.modules.pop("folder_paths", None)
        self.assertEqual(_public_path(str(self.output_dir / "img.png")), "img.png")

    def test_empty_or_none_gives_empty_string(self):
        self.assertEqual(_public_path(""), "")
        self.assertEqual(_public_path(None), "")


class TestPublicError(FolderPathsFixture):

    def test_oserror_uses_strerror_and_basename(self):
        err = FileNotFoundError(
            2, "No such file or directory", str(self.tmpdir) + "/a/b.png"
        )
        text = _public_error(err)
        self.assertIn("No such file or directory", text)
        self.assertIn("b.png", text)
        self.assertNotIn(self.tmpdir, text)

    def test_oserror_without_filename(self):
        err = PermissionError(13, "Permission denied")
        self.assertEqual(_public_error(err), "Permission denied")

    def test_oserror_without_strerror_uses_type_name(self):
        err = OSError()
        self.assertEqual(_public_error(err), "OSError")

    def test_non_oserror_is_str(self):
        self.assertEqual(_public_error(ValueError("bad value")), "bad value")

    def test_non_oserror_with_empty_message_uses_type_name(self):
        self.assertEqual(_public_error(RuntimeError()), "RuntimeError")


# ── gzip middleware ───────────────────────────────────────────────────


class TestGzipMiddleware(AioHTTPTestCase):

    async def get_application(self):
        big = json.dumps({"data": "x" * 4000})

        async def big_json(request):
            return web.json_response(text=big)

        async def small_json(request):
            return web.json_response({"ok": True})

        async def already_encoded(request):
            return web.Response(
                text=big, content_type="text/plain", headers={"Content-Encoding": "br"}
            )

        async def binary(request):
            return web.Response(body=b"\x89PNG" * 1000, content_type="image/png")

        async def incompressible(request):
            return web.Response(body=os.urandom(4096), content_type="text/plain")

        async def streamed(request):
            resp = web.StreamResponse()
            await resp.prepare(request)
            await resp.write(b"x" * 4000)
            await resp.write_eof()
            return resp

        app = web.Application(middlewares=[api_module._gzip_middleware])
        app.router.add_get("/prompt_manager/big", big_json)
        app.router.add_get("/prompt_manager/small", small_json)
        app.router.add_get("/prompt_manager/encoded", already_encoded)
        app.router.add_get("/prompt_manager/binary", binary)
        app.router.add_get("/prompt_manager/random", incompressible)
        app.router.add_get("/prompt_manager/stream", streamed)
        app.router.add_get("/other/big", big_json)
        return app

    async def _get(self, path, accept="gzip"):
        headers = {"Accept-Encoding": accept} if accept else {}
        return await self.client.request(
            "GET",
            path,
            headers=headers,
            auto_decompress=False,
            skip_auto_headers=["Accept-Encoding"],
        )

    async def test_large_json_is_gzipped(self):
        resp = await self._get("/prompt_manager/big")
        self.assertEqual(resp.headers.get("Content-Encoding"), "gzip")
        self.assertEqual(resp.headers.get("Vary"), "Accept-Encoding")
        import gzip

        self.assertIn(b'"data"', gzip.decompress(await resp.read()))

    async def test_small_body_not_compressed(self):
        resp = await self._get("/prompt_manager/small")
        self.assertIsNone(resp.headers.get("Content-Encoding"))

    async def test_other_paths_untouched(self):
        resp = await self._get("/other/big")
        self.assertIsNone(resp.headers.get("Content-Encoding"))

    async def test_without_accept_encoding(self):
        resp = await self._get("/prompt_manager/big", accept=None)
        self.assertIsNone(resp.headers.get("Content-Encoding"))

    async def test_already_encoded_untouched(self):
        resp = await self._get("/prompt_manager/encoded")
        self.assertEqual(resp.headers.get("Content-Encoding"), "br")

    async def test_binary_type_untouched(self):
        resp = await self._get("/prompt_manager/binary")
        self.assertIsNone(resp.headers.get("Content-Encoding"))

    async def test_incompressible_body_untouched(self):
        resp = await self._get("/prompt_manager/random")
        self.assertIsNone(resp.headers.get("Content-Encoding"))

    async def test_stream_response_untouched(self):
        resp = await self._get("/prompt_manager/stream")
        self.assertIsNone(resp.headers.get("Content-Encoding"))
        self.assertEqual(len(await resp.read()), 4000)


# ── UI and static routes ──────────────────────────────────────────────


class CoreRoutesTestCase(AioHTTPTestCase):

    async def get_application(self):
        self.tmpdir = tempfile.mkdtemp()
        app = web.Application()
        routes = web.RouteTableDef()
        self.api = PromptManagerAPI()
        self.api.db = PromptDatabase(os.path.join(self.tmpdir, "prompts.db"))
        self.api.add_routes(routes)
        app.router.add_routes(routes)
        return app

    async def tearDownAsync(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _fake_root(self):
        """Point _get_project_root at the temp dir and return its web/ dir."""
        web_dir = Path(self.tmpdir) / "web"
        (web_dir / "js").mkdir(parents=True)
        (web_dir / "lib").mkdir()
        orig_root = api_module._get_project_root
        api_module._get_project_root = lambda: self.tmpdir
        self.addCleanup(setattr, api_module, "_get_project_root", orig_root)
        return web_dir


UI_PATHS = (
    "/prompt_manager/web",
    "/prompt_manager/gallery.html",
    "/prompt_manager/admin",
    "/prompt_manager/gallery",
)


class TestUiRoutes(CoreRoutesTestCase):

    async def test_pages_are_served(self):
        for path in UI_PATHS:
            resp = await self.client.request("GET", path)
            self.assertEqual(resp.status, 200, path)
            self.assertIn("text/html", resp.headers["Content-Type"])
            self.assertIn("<", await resp.text())

    async def test_pages_are_cached_after_first_read(self):
        await self.client.request("GET", "/prompt_manager/admin")
        self.assertTrue(any(p.endswith("admin.html") for p in self.api._html_cache))
        resp = await self.client.request("GET", "/prompt_manager/admin")
        self.assertEqual(resp.status, 200)

    async def test_missing_pages_are_404(self):
        self._fake_root()
        for path in UI_PATHS:
            resp = await self.client.request("GET", path)
            self.assertEqual(resp.status, 404, path)

    async def test_unreadable_pages_are_500(self):
        web_dir = self._fake_root()
        for name in ("index.html", "metadata.html", "admin.html", "gallery.html"):
            (web_dir / name).mkdir()  # a directory cannot be read as a file
        for path in UI_PATHS:
            resp = await self.client.request("GET", path)
            self.assertEqual(resp.status, 500, path)
            self.assertNotIn(self.tmpdir, await resp.text())

    async def test_test_route(self):
        resp = await self.client.request("GET", "/prompt_manager/test")
        self.assertTrue((await resp.json())["success"])


class TestStaticRoutes(CoreRoutesTestCase):

    async def test_js_served_with_mime(self):
        resp = await self.client.request("GET", "/prompt_manager/js/admin.js")
        self.assertEqual(resp.status, 200)
        self.assertIn("application/javascript", resp.headers["Content-Type"])

    async def test_lib_served(self):
        web_dir = self._fake_root()
        (web_dir / "lib" / "x.css").write_text("body{}")
        resp = await self.client.request("GET", "/prompt_manager/lib/x.css")
        self.assertEqual(resp.status, 200)
        self.assertIn("text/css", resp.headers["Content-Type"])
        self.assertEqual(await resp.text(), "body{}")

    async def test_real_lib_file_served_with_content_type(self):
        resp = await self.client.request(
            "GET", "/prompt_manager/lib/viewerjs/viewer.min.css"
        )
        self.assertEqual(resp.status, 200)
        self.assertIn("text/css", resp.headers["Content-Type"])

    async def test_allowed_asset_types_get_their_mime(self):
        web_dir = self._fake_root()
        expected = {
            "a.map": "application/json",
            "f.woff": "font/woff",
            "f.woff2": "font/woff2",
            "f.ttf": "font/ttf",
            "i.png": "image/png",
            "i.svg": "image/svg+xml",
            "i.ico": "image/x-icon",
            "p.html": "text/html",
        }
        for name in expected:
            (web_dir / "lib" / name).write_bytes(b"\x00\x01")
        for name, mime in expected.items():
            resp = await self.client.request("GET", f"/prompt_manager/lib/{name}")
            self.assertEqual(resp.status, 200, name)
            self.assertIn(mime, resp.headers["Content-Type"], name)

    async def test_disallowed_extensions_are_403_even_when_present(self):
        web_dir = self._fake_root()
        for name in ("x.bin", "x.py", "x.json", "noext", "x.JS.bak"):
            (web_dir / "lib" / name).write_bytes(b"\x00")
            (web_dir / "js" / name).write_bytes(b"\x00")
        for prefix in ("lib", "js"):
            for name in ("x.bin", "x.py", "x.json", "noext", "x.JS.bak"):
                resp = await self.client.request(
                    "GET", f"/prompt_manager/{prefix}/{name}"
                )
                self.assertEqual(resp.status, 403, f"{prefix}/{name}")

    async def test_traversal_is_403(self):
        for prefix in ("lib", "js"):
            for encoded in (
                "..%2F..%2Fpyproject.toml",
                "..%5C..%5Cpyproject.toml",
                "sub%2F..%2F..%2F..%2Fpyproject.toml",
                "%2E%2E%2Fpyproject.toml",
            ):
                resp = await self.client.request(
                    "GET", f"/prompt_manager/{prefix}/{encoded}"
                )
                self.assertEqual(resp.status, 403, f"{prefix}/{encoded}")

    async def test_absolute_drive_and_unc_paths_are_rejected_on_every_os(self):
        # Built literally so the Windows-only escapes are exercised everywhere:
        # os.path.join(root, "C:/x") drops root on Windows, "\\x" and UNC
        # shares re-anchor the path as well.
        candidates = (
            "C:/Windows/win.ini",
            "C:%5CWindows%5Cwin.ini",
            "c:win.ini",
            "%5C%5Cserver%5Cshare%5Cx.js",
            "%2F%2Fserver%2Fshare%2Fx.js",
            "%5CWindows%5Cwin.ini",
            "%2Fetc%2Fpasswd",
        )
        for prefix in ("lib", "js"):
            for encoded in candidates:
                resp = await self.client.request(
                    "GET", f"/prompt_manager/{prefix}/{encoded}"
                )
                self.assertIn(resp.status, (403, 404), f"{prefix}/{encoded}")
                self.assertNotIn("[extensions]", await resp.text())

    async def test_symlink_escaping_web_dir_is_403(self):
        web_dir = self._fake_root()
        secret = Path(self.tmpdir) / "secret.js"
        secret.write_text("secret")
        link = web_dir / "lib" / "link.js"
        try:
            os.symlink(secret, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not available")
        resp = await self.client.request("GET", "/prompt_manager/lib/link.js")
        self.assertEqual(resp.status, 403)

    async def test_empty_path_is_not_served(self):
        for prefix in ("lib", "js"):
            resp = await self.client.request("GET", f"/prompt_manager/{prefix}/")
            self.assertIn(resp.status, (403, 404), prefix)

    async def test_missing_is_404(self):
        for prefix in ("lib", "js"):
            resp = await self.client.request("GET", f"/prompt_manager/{prefix}/nope.js")
            self.assertEqual(resp.status, 404, prefix)

    async def test_directory_is_404(self):
        web_dir = self._fake_root()
        (web_dir / "lib" / "dir.js").mkdir()
        resp = await self.client.request("GET", "/prompt_manager/lib/dir.js")
        self.assertEqual(resp.status, 404)

    async def test_root_of_static_dir_is_not_listed(self):
        resp = await self.client.request("GET", "/prompt_manager/lib/tailwind")
        self.assertEqual(resp.status, 403)


# ── instance helpers ──────────────────────────────────────────────────


class TestInstanceHelpers(FolderPathsFixture):

    def setUp(self):
        super().setUp()
        self.api = PromptManagerAPI()
        self.api.db = PromptDatabase(os.path.join(self.tmpdir, "prompts.db"))
        orig_dirs = list(GalleryConfig.MONITORING_DIRECTORIES)
        self.addCleanup(setattr, GalleryConfig, "MONITORING_DIRECTORIES", orig_dirs)

    def _run(self, coro):
        import asyncio

        return asyncio.run(coro)

    def test_run_in_executor_with_kwargs(self):
        def add(a, b=0):
            return a + b

        self.assertEqual(self._run(self.api._run_in_executor(add, 1, b=2)), 3)
        self.assertEqual(self._run(self.api._run_in_executor(add, 1, 2)), 3)

    def test_invalidate_gallery_cache(self):
        self.api._gallery_cache = {"x": 1}
        self.api.invalidate_gallery_cache()
        self.assertEqual(self.api._gallery_cache, {})

    def test_enrich_images_adds_urls_and_thumbnails(self):
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.output_dir)]
        img = self.output_dir / "sub" / "a.png"
        img.parent.mkdir()
        img.write_bytes(b"x")
        thumb = self.output_dir / "thumbnails" / "sub" / "a_thumb.png"
        thumb.parent.mkdir(parents=True)
        thumb.write_bytes(b"t")
        outside = Path(self.tmpdir) / "elsewhere.png"
        outside.write_bytes(b"o")

        images = [
            {"id": 7, "image_path": str(img)},
            {"id": 8, "image_path": str(outside)},
            {"id": 9, "image_path": ""},
            {"image_path": str(self.output_dir / "missing.png")},
        ]
        result = self.api._enrich_images(images)

        self.assertEqual(result[0]["url"], "/prompt_manager/images/serve/sub/a.png")
        self.assertEqual(
            result[0]["thumbnail_url"],
            "/prompt_manager/images/serve/thumbnails/sub/a_thumb.png",
        )
        self.assertEqual(result[0]["relative_path"], os.path.join("sub", "a.png"))
        self.assertEqual(result[1]["url"], "/prompt_manager/images/8/file")
        self.assertNotIn("relative_path", result[1])
        self.assertNotIn("url", result[2])
        self.assertEqual(result[3]["url"], "/prompt_manager/images/serve/missing.png")
        self.assertNotIn("thumbnail_url", result[3])

    def test_enrich_prompt_images_walks_prompts(self):
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.output_dir)]
        prompts = [
            {"images": [{"id": 1, "image_path": str(self.output_dir / "p.png")}]},
            {},
        ]
        result = self.api._enrich_prompt_images(prompts)
        self.assertEqual(
            result[0]["images"][0]["url"], "/prompt_manager/images/serve/p.png"
        )

    def test_clean_nan_recursive(self):
        nan = float("nan")
        cleaned = self.api._clean_nan_recursive(
            {"a": nan, "b": [nan, 1.5, {"c": nan}], "d": "x"}
        )
        self.assertEqual(cleaned, {"a": None, "b": [None, 1.5, {"c": None}], "d": "x"})

    def test_find_output_dir_uses_configured_and_caches(self):
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.output_dir)]
        self.api._cached_output_dir = None
        first = self.api._find_comfyui_output_dir()
        self.assertEqual(first, str(self.output_dir.resolve()))
        GalleryConfig.MONITORING_DIRECTORIES = []
        self.assertEqual(self.api._find_comfyui_output_dir(), first)

    def test_find_output_dir_skips_missing_configured(self):
        GalleryConfig.MONITORING_DIRECTORIES = [os.path.join(self.tmpdir, "nope")]
        self.api._cached_output_dir = None
        result = self.api._find_comfyui_output_dir()
        self.assertNotEqual(result, os.path.join(self.tmpdir, "nope"))


# ── metadata parsing ──────────────────────────────────────────────────


class TestMetadataParsing(unittest.TestCase):

    def setUp(self):
        self.api = PromptManagerAPI()
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, True)

    def test_extract_metadata_from_png_and_garbage(self):
        from PIL import Image, PngImagePlugin

        path = os.path.join(self.tmpdir, "m.png")
        info = PngImagePlugin.PngInfo()
        info.add_text("prompt", "{}")
        Image.new("RGB", (1, 1)).save(path, pnginfo=info)
        self.assertEqual(self.api._extract_comfyui_metadata(path), {"prompt": "{}"})

        garbage = os.path.join(self.tmpdir, "g.png")
        with open(garbage, "wb") as f:
            f.write(b"nope")
        self.assertEqual(self.api._extract_comfyui_metadata(garbage), {})

    def test_parse_a1111_parameters(self):
        meta = {"parameters": "a cat\nNegative prompt: dog\nSteps: 20"}
        parsed = self.api._parse_comfyui_prompt(meta)
        self.assertEqual(parsed["positive_prompt"], "a cat")
        self.assertEqual(parsed["negative_prompt"], "dog")
        self.assertEqual(parsed["parameters"]["parameters"], meta["parameters"])
        self.assertEqual(self.api._extract_readable_prompt(parsed), "a cat")

    def test_parse_comfyui_fields(self):
        meta = {
            "prompt": json.dumps({"1": {"class_type": "X", "inputs": {}}}),
            "workflow": "not json",
            "steps": "20",
            "cfg": "not-json-either",
        }
        parsed = self.api._parse_comfyui_prompt(meta)
        self.assertIsInstance(parsed["prompt"], dict)
        self.assertEqual(parsed["workflow"], "not json")
        self.assertEqual(parsed["parameters"]["steps"], 20)
        self.assertEqual(parsed["parameters"]["cfg"], "not-json-either")

        parsed = self.api._parse_comfyui_prompt(
            {"prompt": "plain text", "workflow": json.dumps({"nodes": []})}
        )
        self.assertEqual(parsed["prompt"], "plain text")
        self.assertEqual(parsed["workflow"], {"nodes": []})

    def test_extract_readable_prompt_variants(self):
        extract = self.api._extract_readable_prompt
        self.assertEqual(extract({"prompt": "plain"}), "plain")
        self.assertEqual(extract({"prompt": ["a", "", "b"]}), "a b")
        self.assertEqual(extract({"prompt": 42}), "42")
        self.assertEqual(
            extract({"prompt": None, "parameters": {"positive": ["x", "y"]}}), "x y"
        )
        self.assertIsNone(extract({"prompt": {}, "parameters": {}}))

        graph = {
            "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "pos"}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "neg"}},
            "3": {
                "class_type": "KSampler",
                "inputs": {"positive": ["1", 0], "negative": ["2", 0]},
            },
        }
        self.assertEqual(extract({"prompt": graph}), "pos")
        self.assertEqual(extract({"prompt": {"x": 1}, "workflow": graph}), "pos")

    def test_node_inputs_and_text_lookup(self):
        self.assertEqual(self.api._get_node_inputs("nope"), {})
        self.assertEqual(self.api._get_node_inputs({"inputs": 5}), {})
        self.assertEqual(self.api._get_node_inputs({"inputs": {"a": 1}}), {"a": 1})
        listed = self.api._get_node_inputs(
            {"inputs": [{"name": "text", "link": None}, {"nope": 1}, "junk"]}
        )
        self.assertEqual(list(listed), ["text"])

        self.assertIsNone(self.api._find_text_in_node(None))
        self.assertEqual(self.api._find_text_in_node({"inputs": {"text": "hi"}}), "hi")
        self.assertEqual(
            self.api._find_text_in_node(
                {"type": "CLIPTextEncode", "widgets_values": ["w"]}
            ),
            "w",
        )
        self.assertIsNone(
            self.api._find_text_in_node(
                {"type": "CLIPTextEncode", "widgets_values": [" "]}
            )
        )
        self.assertIsNone(self.api._find_text_in_node({"type": "KSampler"}))

    def test_positive_prompt_from_nodes_array_and_fallbacks(self):
        extract = self.api._extract_positive_prompt_from_comfyui_data
        self.assertIsNone(extract("x"))
        self.assertIsNone(extract({}))
        self.assertIsNone(extract({"nodes": []}))

        workflow = {
            "nodes": [
                {"id": 1, "type": "CLIPTextEncode", "widgets_values": ["pos"]},
                {
                    "id": 2,
                    "type": "CLIPTextEncode",
                    "widgets_values": ["neg"],
                    "title": "Negative",
                },
                {"id": 3, "type": "KSampler", "inputs": [{"name": "seed", "link": 4}]},
                "junk",
            ]
        }
        self.assertEqual(extract(workflow), "pos")

        only_negative = {
            "nodes": [
                {
                    "id": 2,
                    "type": "CLIPTextEncode",
                    "widgets_values": ["neg"],
                    "title": "neg",
                },
            ]
        }
        self.assertEqual(extract(only_negative), "neg")

        bad_link = {
            "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "pos"}},
            "3": {
                "class_type": "KSampler",
                "inputs": {"positive": "oops", "negative": []},
            },
            "4": {
                "class_type": "KSampler",
                "inputs": {"positive": ["zz", 0], "negative": []},
            },
        }
        self.assertEqual(extract(bad_link), "pos")


if __name__ == "__main__":
    unittest.main()
