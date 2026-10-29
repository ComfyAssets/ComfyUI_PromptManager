"""Tests for the core API module (py/api/__init__.py): helpers and base routes."""

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

try:
    from tests.open_handles import assert_closed  # noqa: E402
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import assert_closed  # noqa: E402
from database.models import PromptModel  # noqa: E402

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
        self.addCleanup(assert_closed, self, self.tmpdir)  # just before the rmtree
        self.addCleanup(PromptModel.close_all_instances)
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

        async def declared_length(request):
            # The log download (#329) declares its length before the middleware
            # compresses it.
            body = b"x" * 3000
            return web.Response(
                body=body,
                content_type="text/plain",
                headers={"Content-Length": str(len(body))},
            )

        app = web.Application(middlewares=[api_module._gzip_middleware])
        app.router.add_get("/prompt_manager/big", big_json)
        app.router.add_get("/prompt_manager/declared", declared_length)
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

    async def test_compressed_body_declares_its_own_length(self):
        # A stale Content-Length (the uncompressed size) makes a browser wait
        # for bytes that never come (#329: Download log hung on any log > 1 KB).
        resp = await self._get("/prompt_manager/declared")
        self.assertEqual(resp.headers.get("Content-Encoding"), "gzip")
        # Bounded: a stale length would otherwise wait for the missing bytes.
        body = await asyncio.wait_for(resp.read(), timeout=10)
        self.assertEqual(int(resp.headers["Content-Length"]), len(body))
        import gzip

        self.assertEqual(gzip.decompress(body), b"x" * 3000)

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


# ── JSON body reader ──────────────────────────────────────────────────


class TestReadJsonBody(AioHTTPTestCase):
    """_read_json_body: chunked read with a byte cap, object-only payloads."""

    async def get_application(self):
        async def echo(request):
            data, error = await api_module._read_json_body(request)
            if error is not None:
                return error
            return web.json_response({"success": True, "keys": sorted(data)})

        async def small(request):
            data, error = await api_module._read_json_body(request, max_bytes=64)
            if error is not None:
                return error
            return web.json_response({"success": True, "data": data})

        app = web.Application(client_max_size=4 * 1024 * 1024)
        app.router.add_post("/echo", echo)
        app.router.add_post("/small", small)
        return app

    async def test_valid_object_is_parsed(self):
        resp = await self.client.request("POST", "/echo", json={"b": 1, "a": 2})
        self.assertEqual(resp.status, 200)
        self.assertEqual((await resp.json())["keys"], ["a", "b"])

    async def test_default_cap_is_one_megabyte(self):
        self.assertEqual(api_module.JSON_BODY_MAX_BYTES, 1_000_000)
        body = json.dumps({"pad": "x" * 1_000_100}).encode()
        resp = await self.client.request(
            "POST", "/echo", data=body, headers={"Content-Type": "application/json"}
        )
        self.assertEqual(resp.status, 413)
        data = await resp.json()
        self.assertFalse(data["success"])
        self.assertIn("too large", data["error"].lower())

    async def test_body_just_under_cap_is_accepted(self):
        payload = {"pad": "x" * (1_000_000 - 20)}
        body = json.dumps(payload).encode()
        self.assertLessEqual(len(body), 1_000_000)
        resp = await self.client.request(
            "POST", "/echo", data=body, headers={"Content-Type": "application/json"}
        )
        self.assertEqual(resp.status, 200)

    async def test_chunked_upload_without_content_length_is_capped(self):
        async def gen():
            for _ in range(10):
                yield b'{"pad": "' + b"x" * 20 + b'"}'

        resp = await self.client.request(
            "POST",
            "/small",
            data=gen(),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.status, 413)

    async def test_declared_oversize_content_length_is_413(self):
        body = b'{"pad": "' + b"x" * 200 + b'"}'
        resp = await self.client.request(
            "POST", "/small", data=body, headers={"Content-Type": "application/json"}
        )
        self.assertEqual(resp.status, 413)

    async def test_invalid_json_is_400(self):
        for body in (b"{oops", b"\xff\xfe", b""):
            resp = await self.client.request(
                "POST",
                "/echo",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            self.assertEqual(resp.status, 400, body)
            self.assertFalse((await resp.json())["success"])

    async def test_non_object_json_is_400(self):
        for body in (b"[1, 2]", b'"text"', b"3", b"null", b"true"):
            resp = await self.client.request(
                "POST",
                "/echo",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            self.assertEqual(resp.status, 400, body)
            self.assertIn("object", (await resp.json())["error"])

    def test_helper_is_exposed_on_the_api_object(self):
        self.assertIs(PromptManagerAPI._read_json_body, api_module._read_json_body)


class TestLongJobLock(unittest.TestCase):
    """_acquire_long_job: one job per family at a time, released on exit."""

    def setUp(self):
        database = MagicMock()
        database.cleanup_duplicates.return_value = 0
        self.api = PromptManagerAPI(db=database)

    def _run(self, coro):
        import asyncio

        return asyncio.run(coro)

    def test_second_acquire_is_refused_until_first_releases(self):
        async def scenario():
            first = self.api._acquire_long_job("duplicates")
            self.assertIsNotNone(first)
            async with first:
                self.assertIsNone(self.api._acquire_long_job("duplicates"))
                # Other families are independent
                other = self.api._acquire_long_job("thumbnails")
                self.assertIsNotNone(other)
                async with other:
                    pass
            again = self.api._acquire_long_job("duplicates")
            self.assertIsNotNone(again)
            async with again:
                pass

        self._run(scenario())

    def test_lock_is_released_when_the_job_raises(self):
        async def scenario():
            with self.assertRaises(RuntimeError):
                async with self.api._acquire_long_job("duplicates"):
                    raise RuntimeError("boom")
            self.assertIsNotNone(self.api._acquire_long_job("duplicates"))

        self._run(scenario())

    def test_busy_response_is_409_with_envelope(self):
        resp = self.api._long_job_busy_response("Duplicate scan")
        self.assertEqual(resp.status, 409)
        data = json.loads(resp.body)
        self.assertFalse(data["success"])
        self.assertEqual(data["error"], "Duplicate scan already running")


# ── UI and static routes ──────────────────────────────────────────────


class CoreRoutesTestCase(AioHTTPTestCase):

    async def get_application(self):
        self.tmpdir = tempfile.mkdtemp()
        app = web.Application()
        routes = web.RouteTableDef()
        self.api = PromptManagerAPI(
            db=PromptDatabase(os.path.join(self.tmpdir, "prompts.db"))
        )
        self.api.add_routes(routes)
        app.router.add_routes(routes)
        return app

    async def tearDownAsync(self):
        await super().tearDownAsync()  # closes the aiohttp test client
        # The automatic preview worker writes into the temp tree: stop it first.
        self.api.close_auto_thumbnails()
        self.api.db.close_all()
        assert_closed(self, self.tmpdir)
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
    "/prompt_manager/workspace",
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
        for name in (
            "workspace.html",
            "index.html",
            "metadata.html",
            "admin.html",
            "gallery.html",
        ):
            (web_dir / name).mkdir()  # a directory cannot be read as a file
        for path in UI_PATHS:
            resp = await self.client.request("GET", path)
            self.assertEqual(resp.status, 500, path)
            self.assertNotIn(self.tmpdir, await resp.text())

    async def test_test_route(self):
        resp = await self.client.request("GET", "/prompt_manager/test")
        self.assertTrue((await resp.json())["success"])

    async def test_image_annotation_routes_are_registered(self):
        table = {
            (route.method, route.resource.canonical)
            for route in self.app.router.routes()
            if route.resource is not None
        }
        for entry in (
            ("POST", "/prompt_manager/image-annotations/observe"),
            ("GET", "/prompt_manager/image-annotations/capture/{token}"),
            ("DELETE", "/prompt_manager/image-annotations/capture/{token}"),
            ("PUT", "/prompt_manager/image-annotations/tags"),
        ):
            self.assertIn(entry, table)

    async def test_image_tag_job_route_is_registered_beside_the_scan_job(self):
        table = {
            (route.method, route.resource.canonical)
            for route in self.app.router.routes()
            if route.resource is not None
        }
        self.assertIn(("POST", "/prompt_manager/jobs/scan"), table)
        self.assertIn(("POST", "/prompt_manager/jobs/image-tags"), table)

    async def test_review_routes_are_registered(self):
        table = {
            (route.method, route.resource.canonical)
            for route in self.app.router.routes()
            if route.resource is not None
        }
        for entry in (
            ("GET", "/prompt_manager/review/items"),
            ("GET", "/prompt_manager/review/items/{item_id}"),
            ("POST", "/prompt_manager/review/items/{item_id}/decision"),
        ):
            self.assertIn(entry, table)


class TestStaticRoutes(CoreRoutesTestCase):

    async def test_js_served_with_mime(self):
        resp = await self.client.request("GET", "/prompt_manager/js/admin.js")
        self.assertEqual(resp.status, 200)
        self.assertIn("application/javascript", resp.headers["Content-Type"])

    async def test_workspace_module_served_with_javascript_mime(self):
        resp = await self.client.request(
            "GET", "/prompt_manager/js/workspace/index.mjs"
        )
        self.assertEqual(resp.status, 200)
        self.assertIn("application/javascript", resp.headers["Content-Type"])

    async def test_inspection_semantics_table_is_the_one_json_served(self):
        """Local Inspect reads the node table the server resolves with (#249 PR L3).

        Only that exact file is served; every other JSON stays forbidden.
        """
        resp = await self.client.request(
            "GET", "/prompt_manager/js/inspection-semantics.json"
        )
        self.assertEqual(resp.status, 200)
        self.assertIn("application/json", resp.headers["Content-Type"])
        table = Path(__file__).resolve().parents[1] / "web" / "js"
        self.assertEqual(
            await resp.read(), (table / "inspection-semantics.json").read_bytes()
        )
        for path in (
            "js/shared/toolkit/provenance.json",
            "lib/viewerjs/provenance.json",
            "lib/inspection-semantics.json",
            "js/workspace/..%2Finspection-semantics.json",
            "js/INSPECTION-SEMANTICS.json",
        ):
            resp = await self.client.request("GET", f"/prompt_manager/{path}")
            self.assertEqual(resp.status, 403, path)

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

    async def test_inspection_semantics_symlink_escaping_web_dir_is_403(self):
        """The exact-path JSON gets the same realpath containment as any file."""
        web_dir = self._fake_root()
        secret = Path(self.tmpdir) / "secret.json"
        secret.write_text('{"secret": true}')
        try:
            os.symlink(secret, web_dir / "js" / "inspection-semantics.json")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not available")
        resp = await self.client.request(
            "GET", "/prompt_manager/js/inspection-semantics.json"
        )
        self.assertEqual(resp.status, 403)
        self.assertNotIn("secret", await resp.text())

    async def test_inspection_semantics_missing_is_404(self):
        self._fake_root()
        resp = await self.client.request(
            "GET", "/prompt_manager/js/inspection-semantics.json"
        )
        self.assertEqual(resp.status, 404)

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


class TestRevalidatedCaching(CoreRoutesTestCase):
    """Every served page and static file is revalidated on reload (#478).

    ``Cache-Control: no-cache`` makes the browser ask before it reuses a copy,
    and the ETag lets an unchanged file answer 304 without a body, so an update
    reaches the browser on a normal reload and an unchanged reload stays cheap.
    """

    STATIC_PATHS = (
        "/prompt_manager/js/workspace/index.mjs",
        "/prompt_manager/js/admin.js",
        "/prompt_manager/js/inspection-semantics.json",
        "/prompt_manager/lib/viewerjs/viewer.min.css",
    )

    async def _assert_revalidated(self, path):
        resp = await self.client.request("GET", path)
        self.assertEqual(resp.status, 200, path)
        self.assertEqual(resp.headers.get("Cache-Control"), "no-cache", path)
        etag = resp.headers.get("ETag")
        self.assertTrue(etag, path)
        body = await resp.read()
        self.assertTrue(body, path)

        again = await self.client.request("GET", path, headers={"If-None-Match": etag})
        self.assertEqual(again.status, 304, path)
        self.assertEqual(again.headers.get("ETag"), etag, path)
        self.assertEqual(again.headers.get("Cache-Control"), "no-cache", path)
        self.assertEqual(await again.read(), b"", path)

        stale = await self.client.request(
            "GET", path, headers={"If-None-Match": 'W/"stale"'}
        )
        self.assertEqual(stale.status, 200, path)
        self.assertEqual(await stale.read(), body, path)
        return etag

    async def test_static_files_are_revalidated(self):
        for path in self.STATIC_PATHS:
            await self._assert_revalidated(path)

    async def test_pages_are_revalidated(self):
        for path in UI_PATHS:
            await self._assert_revalidated(path)

    async def test_etag_matches_in_a_list_and_as_wildcard(self):
        path = "/prompt_manager/js/workspace/index.mjs"
        etag = (await self.client.request("GET", path)).headers["ETag"]
        for header in (f'W/"other", {etag}', etag.removeprefix("W/"), "*"):
            resp = await self.client.request(
                "GET", path, headers={"If-None-Match": header}
            )
            self.assertEqual(resp.status, 304, header)

    async def test_changed_file_gets_a_new_etag_and_its_new_body(self):
        web_dir = self._fake_root()
        module = web_dir / "js" / "m.mjs"
        module.write_text("export const v = 1;")
        path = "/prompt_manager/js/m.mjs"
        first = await self.client.request("GET", path)
        etag = first.headers["ETag"]
        # Same size and, on coarse clocks, the same mtime: only the bytes differ.
        module.write_text("export const v = 2;")
        resp = await self.client.request("GET", path, headers={"If-None-Match": etag})
        self.assertEqual(resp.status, 200)
        self.assertNotEqual(resp.headers["ETag"], etag)
        self.assertEqual(await resp.text(), "export const v = 2;")

    async def test_gzipped_and_plain_bodies_share_a_weak_etag(self):
        """The gzip middleware changes the bytes, so the tag must be weak."""
        resp = await self.client.request("GET", "/prompt_manager/js/admin.js")
        self.assertTrue(resp.headers["ETag"].startswith('W/"'))

    async def test_errors_carry_no_validator(self):
        resp = await self.client.request("GET", "/prompt_manager/js/nope.js")
        self.assertEqual(resp.status, 404)
        self.assertNotIn("ETag", resp.headers)


@web.middleware
async def _comfyui_cache_control(request, handler):
    """ComfyUI's own middleware (middleware/cache_middleware.py), JS/CSS branch.

    It only fills a missing Cache-Control, and ``.mjs`` is not ``.js``: that is
    why the workspace modules went out with no header at all before #478.
    """
    response = await handler(request)
    if request.path.endswith(".js") or request.path.endswith(".css"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


class TestRevalidatedCachingBehindMiddlewares(CoreRoutesTestCase):
    """The host's middlewares keep the header and the 304 intact."""

    async def get_application(self):
        app = await super().get_application()
        app.middlewares.append(_comfyui_cache_control)
        app.middlewares.append(api_module._gzip_middleware)
        return app

    async def test_gzipped_files_keep_no_cache_and_answer_304(self):
        for path in (
            "/prompt_manager/js/workspace/index.mjs",
            "/prompt_manager/js/admin.js",
            "/prompt_manager/lib/viewerjs/viewer.min.css",
            "/prompt_manager/workspace",
        ):
            resp = await self.client.request(
                "GET", path, headers={"Accept-Encoding": "gzip"}
            )
            self.assertEqual(resp.status, 200, path)
            self.assertEqual(resp.headers.get("Content-Encoding"), "gzip", path)
            self.assertEqual(resp.headers["Cache-Control"], "no-cache", path)
            again = await self.client.request(
                "GET",
                path,
                headers={
                    "Accept-Encoding": "gzip",
                    "If-None-Match": resp.headers["ETag"],
                },
            )
            self.assertEqual(again.status, 304, path)
            self.assertEqual(again.headers["Cache-Control"], "no-cache", path)


# ── instance helpers ──────────────────────────────────────────────────


class TestInstanceHelpers(FolderPathsFixture):

    def setUp(self):
        super().setUp()
        self.api = PromptManagerAPI(
            db=PromptDatabase(os.path.join(self.tmpdir, "prompts.db"))
        )
        self.addCleanup(self.api.db.close_all)  # runs before the rmtree cleanup
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
        record = self.api._root_listing(self.output_dir)
        record.files = [(self.output_dir / "a.png", 1.0)]
        record.folders = record.folders_with_ancestors = frozenset()
        record.walked_at = 1.0
        self.api._favorite_listing_cache = {"x": 1}
        self.api._workspace_order_cache = {"x": 1}
        self.api.invalidate_gallery_cache()
        # The record stays: its generation outlives every drop (#348).
        self.assertIs(self.api._root_listings[str(self.output_dir)], record)
        self.assertIsNone(record.files)
        self.assertIsNone(record.folders)
        self.assertIsNone(record.walked_at)
        self.assertEqual(record.generation, 1)
        self.assertEqual(self.api._favorite_listing_cache, {})
        self.assertEqual(self.api._workspace_order_cache, {})
        self.api.reset_gallery_roots_cache()
        self.assertEqual(record.generation, 2)

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
        self.assertRegex(
            result[0]["thumbnail_url"],
            r"^/prompt_manager/images/serve/thumbnails/sub/a_thumb\.png\?v=\d+$",
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

    def test_find_output_dir_skips_configured_root_outside_comfyui(self):
        outside = Path(self.tmpdir) / "outside"
        outside.mkdir()
        GalleryConfig.MONITORING_DIRECTORIES = [str(outside)]
        self.api._cached_output_dir = None
        result = self.api._find_comfyui_output_dir()
        self.assertNotEqual(
            os.path.normcase(str(result or "")), os.path.normcase(str(outside))
        )

    def test_find_output_dir_skips_missing_configured(self):
        GalleryConfig.MONITORING_DIRECTORIES = [os.path.join(self.tmpdir, "nope")]
        self.api._cached_output_dir = None
        result = self.api._find_comfyui_output_dir()
        self.assertNotEqual(result, os.path.join(self.tmpdir, "nope"))


# ── metadata parsing ──────────────────────────────────────────────────


class TestMetadataParsing(unittest.TestCase):

    def setUp(self):
        database = MagicMock()
        database.cleanup_duplicates.return_value = 0
        self.api = PromptManagerAPI(db=database)
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


if __name__ == "__main__":
    unittest.main()
