"""Show in folder (TESTING.md D38): the local-only reveal route and its openers.

No test launches a file manager: ``subprocess.Popen``, ``sys.platform`` and
``shutil.which`` are mocked, and route tests swap the API's opener.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

from aiohttp import web
from multidict import CIMultiDict

try:
    from test_api_images import ImageAPITestCase, make_png
except ImportError:
    from tests.test_api_images import ImageAPITestCase, make_png

import utils.file_reveal as file_reveal
from utils.file_reveal import (
    OpenerUnavailable,
    is_local_request,
    reveal_in_file_manager,
)

REVEAL = "/prompt_manager/images/reveal"
PROXY_HEADERS = ("Forwarded", "X-Forwarded-For", "X-Real-IP", "X-Forwarded-Host")


def headers(**values):
    base = {"Host": "127.0.0.1:8188"}
    base.update({key.replace("_", "-"): value for key, value in values.items()})
    return CIMultiDict(base)


class TestIsLocalRequest(unittest.TestCase):
    def test_loopback_clients_by_loopback_name_are_local(self):
        for remote, host in (
            ("127.0.0.1", "127.0.0.1:8188"),
            ("127.8.9.10", "localhost:8188"),
            ("::1", "[::1]:8188"),
            ("::ffff:127.0.0.1", "comfy.localhost"),
        ):
            with self.subTest(remote=remote, host=host):
                self.assertTrue(is_local_request(remote, headers(Host=host)))

    def test_non_loopback_or_unknown_remote_is_not_local(self):
        for remote in ("192.168.1.5", "10.0.0.1", "::2", "fe80::1%eth0", "", None):
            with self.subTest(remote=remote):
                self.assertFalse(is_local_request(remote, headers()))

    def test_any_proxy_header_makes_loopback_remote(self):
        for name in PROXY_HEADERS:
            for spelling in (name, name.lower()):
                with self.subTest(header=spelling):
                    request_headers = headers()
                    request_headers[spelling] = "127.0.0.1"
                    self.assertFalse(is_local_request("127.0.0.1", request_headers))

    def test_foreign_host_or_origin_is_not_local(self):
        # A DNS-rebinding page reaches 127.0.0.1 under its own name.
        for host in ("evil.example:8188", "", "[bad"):
            with self.subTest(host=host):
                self.assertFalse(is_local_request("127.0.0.1", headers(Host=host)))
        self.assertFalse(
            is_local_request("127.0.0.1", CIMultiDict({"Origin": "http://localhost"}))
        )
        for origin in ("http://evil.example:8188", "null", "http://[bad"):
            with self.subTest(origin=origin):
                self.assertFalse(is_local_request("127.0.0.1", headers(Origin=origin)))
        self.assertTrue(
            is_local_request("127.0.0.1", headers(Origin="http://127.0.0.1:8188"))
        )


class TestRevealInFileManager(unittest.TestCase):
    def setUp(self):
        self.path = Path(os.path.abspath(os.path.join(os.sep, "out", "sub", "a.png")))
        self.popen = patch.object(subprocess, "Popen").start()
        self.addCleanup(patch.stopall)

    def which(self, available):
        return patch.object(
            file_reveal.shutil,
            "which",
            side_effect=lambda name: available.get(name),
        )

    def argv(self, call=0):
        return self.popen.call_args_list[call].args[0]

    def assert_detached(self, call=0):
        options = self.popen.call_args_list[call].kwargs
        for stream in ("stdin", "stdout", "stderr"):
            self.assertIs(options[stream], subprocess.DEVNULL)
        self.assertIs(options["close_fds"], True)
        self.assertNotIn("shell", options)

    def test_windows_selects_with_explorer_from_the_windows_directory(self):
        with tempfile.TemporaryDirectory() as windows:
            explorer = os.path.join(windows, "explorer.exe")
            Path(explorer).write_bytes(b"")
            with (
                patch.object(file_reveal.sys, "platform", "win32"),
                patch.dict(os.environ, {"SystemRoot": windows}),
                self.which({"explorer": "/planted/explorer.exe"}),
            ):
                reveal_in_file_manager(self.path)
        self.assertEqual(self.argv(), [explorer, "/select,", str(self.path)])
        self.assert_detached()
        self.assertNotIn("start_new_session", self.popen.call_args.kwargs)
        self.popen.return_value.wait.assert_not_called()

    def test_windows_without_explorer_is_unavailable(self):
        with tempfile.TemporaryDirectory() as windows:
            for env in ({"SystemRoot": windows}, {"SystemRoot": "relative"}, {}):
                with (
                    self.subTest(env=env),
                    patch.object(file_reveal.sys, "platform", "win32"),
                    patch.dict(os.environ, env),
                ):
                    if not env:
                        os.environ.pop("SystemRoot", None)
                        os.environ.pop("WINDIR", None)
                    with self.assertRaises(OpenerUnavailable):
                        reveal_in_file_manager(self.path)
        self.popen.assert_not_called()

    def test_macos_reveals_with_open(self):
        with (
            patch.object(file_reveal.sys, "platform", "darwin"),
            self.which({"open": "/usr/bin/open"}),
        ):
            reveal_in_file_manager(self.path)
        self.assertEqual(self.argv(), ["/usr/bin/open", "-R", str(self.path)])
        self.assert_detached()
        self.assertIs(self.popen.call_args.kwargs["start_new_session"], True)
        self.popen.return_value.wait.assert_not_called()

    def test_relative_path_lookup_never_counts(self):
        with (
            patch.object(file_reveal.sys, "platform", "darwin"),
            self.which({"open": "./open"}),
            self.assertRaises(OpenerUnavailable),
        ):
            reveal_in_file_manager(self.path)
        self.popen.assert_not_called()

    def test_linux_asks_the_file_manager_service_first(self):
        self.popen.return_value.wait.return_value = 0
        with (
            patch.object(file_reveal.sys, "platform", "linux"),
            self.which({"dbus-send": "/usr/bin/dbus-send", "xdg-open": "/x"}),
        ):
            reveal_in_file_manager(self.path)
        self.assertEqual(
            self.argv(),
            [
                "/usr/bin/dbus-send",
                "--session",
                "--dest=org.freedesktop.FileManager1",
                "--type=method_call",
                "/org/freedesktop/FileManager1",
                "org.freedesktop.FileManager1.ShowItems",
                f"array:string:{self.path.as_uri()}",
                "string:",
            ],
        )
        self.assertEqual(self.popen.call_count, 1)
        self.assert_detached()

    def test_linux_slow_service_start_counts_as_delivered(self):
        self.popen.return_value.wait.side_effect = subprocess.TimeoutExpired("d", 2)
        with (
            patch.object(file_reveal.sys, "platform", "linux"),
            self.which({"dbus-send": "/usr/bin/dbus-send", "xdg-open": "/x"}),
        ):
            reveal_in_file_manager(self.path)
        self.assertEqual(self.popen.call_count, 1)

    def test_linux_falls_back_to_xdg_open_on_the_folder(self):
        cases = {
            "missing": ({"xdg-open": "/usr/bin/xdg-open"}, None),
            "non-zero": (
                {"dbus-send": "/usr/bin/dbus-send", "xdg-open": "/usr/bin/xdg-open"},
                1,
            ),
            "start error": (
                {"dbus-send": "/usr/bin/dbus-send", "xdg-open": "/usr/bin/xdg-open"},
                OSError("no bus"),
            ),
        }
        for name, (available, outcome) in cases.items():
            with self.subTest(name):
                self.popen.reset_mock(side_effect=True, return_value=True)
                if isinstance(outcome, OSError):
                    self.popen.side_effect = [outcome, MagicMock()]
                else:
                    self.popen.return_value.wait.return_value = outcome
                with (
                    patch.object(file_reveal.sys, "platform", "linux"),
                    self.which(available),
                ):
                    reveal_in_file_manager(self.path)
                self.assertEqual(
                    self.popen.call_args.args[0],
                    ["/usr/bin/xdg-open", str(self.path.parent)],
                )
                self.assert_detached(self.popen.call_count - 1)

    def test_linux_without_any_opener_is_unavailable(self):
        with (
            patch.object(file_reveal.sys, "platform", "linux"),
            self.which({}),
            self.assertRaises(OpenerUnavailable),
        ):
            reveal_in_file_manager(self.path)
        self.popen.assert_not_called()

    def test_relative_paths_are_refused(self):
        with self.assertRaises(ValueError):
            reveal_in_file_manager(Path("sub/a.png"))
        self.popen.assert_not_called()


class TestRevealRoute(ImageAPITestCase):
    async def get_application(self):
        app = await super().get_application()
        self.opened = []
        self.outcome = None

        def opener(path):
            if self.outcome is not None:
                raise self.outcome
            self.opened.append(path)

        self.api._reveal_opener = opener
        return app

    def scope(self, index=0):
        roots = self.api._get_all_output_dirs()
        return self.api._gallery_root_scope(roots[index], index)

    async def post(self, body, **request_headers):
        response = await self.client.post(REVEAL, json=body, headers=request_headers)
        return response.status, await response.json()

    def body(self, path="sub/a.png", **changes):
        return {"root_index": 0, "root_scope": self.scope(), "path": path, **changes}

    async def test_loopback_request_opens_the_resolved_file(self):
        target = make_png(self.output_dir / "sub" / "a.png")
        status, data = await self.post(self.body())
        self.assertEqual((status, data), (200, {"success": True}))
        self.assertEqual(self.opened, [Path(os.path.realpath(target))])

    async def test_non_loopback_remote_is_not_local(self):
        make_png(self.output_dir / "sub" / "a.png")
        with patch.object(
            web.BaseRequest, "remote", new_callable=PropertyMock
        ) as remote:
            remote.return_value = "192.168.1.20"
            status, data = await self.post(self.body())
        self.assertEqual(status, 403)
        self.assertEqual(data["code"], "not-local")
        self.assertIs(data["success"], False)
        self.assertEqual(self.opened, [])

    async def test_loopback_with_a_proxy_header_is_not_local(self):
        make_png(self.output_dir / "sub" / "a.png")
        for name in PROXY_HEADERS:
            with self.subTest(header=name):
                status, data = await self.post(self.body(), **{name: "127.0.0.1"})
                self.assertEqual((status, data["code"]), (403, "not-local"))
        status, data = await self.post(self.body(), Host="evil.example:8188")
        self.assertEqual((status, data["code"]), (403, "not-local"))
        self.assertEqual(self.opened, [])

    async def test_changed_or_missing_scope_is_rejected(self):
        make_png(self.output_dir / "sub" / "a.png")
        status, data = await self.post(self.body(root_scope="0" * 64))
        self.assertEqual((status, data["code"]), (409, "source-changed"))
        status, data = await self.post(self.body(root_index=3))
        self.assertEqual((status, data["code"]), (409, "source-changed"))
        for scope in (None, "ABC", 7, "g" * 64):
            with self.subTest(scope=scope):
                status, _ = await self.post(self.body(root_scope=scope))
                self.assertEqual(status, 400)
        body = self.body()
        del body["root_scope"]
        status, _ = await self.post(body)
        self.assertEqual(status, 400)
        self.assertEqual(self.opened, [])

    async def test_body_types_are_strict(self):
        make_png(self.output_dir / "sub" / "a.png")
        for changes in (
            {"root_index": True},
            {"root_index": "0"},
            {"root_index": -1},
            {"root_index": 0.0},
            {"path": 5},
            {"path": ""},
            {"path": "a" * 5000 + ".png"},
            {"path": "sub/a\0.png"},
        ):
            with self.subTest(changes=changes):
                status, data = await self.post(self.body(**changes))
                self.assertEqual(status, 400, data)
        response = await self.client.post(REVEAL, data="[1]")
        self.assertEqual(response.status, 400)
        self.assertEqual(self.opened, [])

    async def test_traversal_and_absolute_paths_are_rejected(self):
        make_png(self.output_dir / "sub" / "a.png")
        outside = make_png(self.comfy_dir / "outside.png")
        for path in (
            "../outside.png",
            "sub/../../outside.png",
            "sub\\..\\..\\outside.png",
            str(outside),
            "/" + outside.name,
            "C:/outside.png",
            "C:outside.png",
        ):
            with self.subTest(path=path):
                status, data = await self.post(self.body(path=path))
                self.assertEqual(status, 400, data)
        self.assertEqual(self.opened, [])

    async def test_symlink_out_of_the_root_is_refused(self):
        outside = make_png(self.comfy_dir / "outside.png")
        self._make_symlink(outside, self.output_dir / "link.png")
        status, _ = await self.post(self.body(path="link.png"))
        self.assertIn(status, (403, 404))
        self.assertEqual(self.opened, [])

    async def test_missing_non_media_and_non_regular_files(self):
        (self.output_dir / "folder.png").mkdir()
        (self.output_dir / "notes.txt").write_text("x")
        for path, expected in (
            ("sub/missing.png", 404),
            ("folder.png", 404),
            ("notes.txt", 403),
        ):
            with self.subTest(path=path):
                status, data = await self.post(self.body(path=path))
                self.assertEqual(status, expected, data)
                self.assertIs(data["success"], False)
        self.assertEqual(self.opened, [])

    async def test_unavailable_opener_is_501_and_start_failure_500(self):
        make_png(self.output_dir / "sub" / "a.png")
        self.outcome = OpenerUnavailable()
        status, data = await self.post(self.body())
        self.assertEqual(status, 501)
        self.assertEqual(data, {**data, "success": False, "code": "unavailable"})
        self.outcome = OSError("cannot start")
        status, data = await self.post(self.body())
        self.assertEqual(status, 500)
        self.assertIs(data["success"], False)
        self.assertNotIn("cannot start", data["error"])

    async def test_default_opener_is_the_platform_file_manager(self):
        self.assertIs(type(self.api)._reveal_opener, file_reveal.reveal_in_file_manager)


if __name__ == "__main__":
    unittest.main()
