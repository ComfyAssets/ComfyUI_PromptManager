"""Show in folder (TESTING.md D38): the local-only reveal route and its openers.

No test launches a file manager: ``subprocess.Popen``, ``sys.platform`` and
``shutil.which`` are mocked, and route tests swap the API's opener.
"""

import ctypes
import os
import subprocess
import sys
import tempfile
import types
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
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        self.root = Path(os.path.realpath(temp.name)) / "out"
        self.path = self.root / "sub" / "a.png"
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b"png")
        self.popen = patch.object(subprocess, "Popen").start()
        self.reaped = patch.object(file_reveal, "_reap").start()
        self.addCleanup(patch.stopall)

    def reveal(self, path=None):
        reveal_in_file_manager(self.path if path is None else path, self.root)

    def which(self, available):
        def lookup(name, path=None):
            self.assertEqual(path, file_reveal.SYSTEM_PATH)
            return available.get(name)

        return patch.object(file_reveal.shutil, "which", side_effect=lookup)

    def argv(self, call=0):
        return self.popen.call_args_list[call].args[0]

    def assert_detached(self, call=0):
        options = self.popen.call_args_list[call].kwargs
        for stream in ("stdin", "stdout", "stderr"):
            self.assertIs(options[stream], subprocess.DEVNULL)
        self.assertIs(options["close_fds"], True)
        self.assertIs(options["start_new_session"], True)
        self.assertNotIn("shell", options)
        self.assertEqual(options["env"]["PATH"], file_reveal.SYSTEM_PATH)

    def windows(self, selected=True, startfile=None):
        """Windows without Windows: the shell call and os.startfile injected."""
        select = patch.object(file_reveal, "_windows_select", return_value=selected)
        start = patch.object(
            file_reveal.os, "startfile", startfile or MagicMock(), create=True
        )
        platform = patch.object(file_reveal.sys, "platform", "win32")
        return select, start, platform

    def test_windows_selects_through_the_shell_api_without_a_process(self):
        # A comma path splits Explorer's own command line (security review H1);
        # the shell API takes the path as one value, unchanged.
        path = self.root / "a," / "b,c.png"
        path.parent.mkdir()
        path.write_bytes(b"png")
        select, start, platform = self.windows(selected=True)
        with select as shell, start as startfile, platform:
            self.reveal(path)
        shell.assert_called_once_with(path)
        startfile.assert_not_called()
        self.popen.assert_not_called()

    def test_windows_falls_back_to_opening_the_folder(self):
        select, start, platform = self.windows(selected=False)
        with select as shell, start as startfile, platform:
            self.reveal()
        shell.assert_called_once_with(self.path)
        startfile.assert_called_once_with(str(self.path.parent), "explore")
        self.popen.assert_not_called()

    def test_windows_folder_start_failure_raises(self):
        select, start, platform = self.windows(
            selected=False, startfile=MagicMock(side_effect=OSError("denied"))
        )
        with select, start, platform, self.assertRaises(OSError):
            self.reveal()
        self.popen.assert_not_called()

    def test_windows_without_startfile_is_unavailable(self):
        with (
            patch.object(file_reveal, "_windows_select", return_value=False),
            patch.object(file_reveal.sys, "platform", "win32"),
            patch.object(file_reveal.os, "startfile", None, create=True),
            self.assertRaises(OpenerUnavailable),
        ):
            self.reveal()
        self.popen.assert_not_called()

    def test_macos_reveals_with_the_system_open(self):
        with (
            patch.object(file_reveal.sys, "platform", "darwin"),
            patch.object(file_reveal, "_macos_open", return_value="/usr/bin/open"),
            self.which({"open": "/planted/open"}),
        ):
            self.reveal()
        self.assertEqual(self.argv(), ["/usr/bin/open", "-R", str(self.path)])
        self.assert_detached()
        self.popen.return_value.wait.assert_not_called()
        self.reaped.assert_called_once_with(self.popen.return_value)

    def test_macos_open_is_the_fixed_system_path(self):
        self.assertEqual(file_reveal.MACOS_OPEN, "/usr/bin/open")
        with patch.object(file_reveal.os.path, "isfile", return_value=False):
            self.assertIsNone(file_reveal._macos_open())
        with (
            patch.object(file_reveal.os.path, "isfile", return_value=True),
            patch.object(file_reveal.os, "access", return_value=True),
        ):
            self.assertEqual(file_reveal._macos_open(), "/usr/bin/open")
        with (
            patch.object(file_reveal.sys, "platform", "darwin"),
            patch.object(file_reveal, "_macos_open", return_value=None),
            self.assertRaises(OpenerUnavailable),
        ):
            self.reveal()
        self.popen.assert_not_called()

    def test_relative_path_lookup_never_counts(self):
        with (
            patch.object(file_reveal.sys, "platform", "linux"),
            self.which({"dbus-send": "./dbus-send", "xdg-open": "bin/xdg-open"}),
            self.assertRaises(OpenerUnavailable),
        ):
            self.reveal()
        self.popen.assert_not_called()

    def test_children_get_a_minimal_environment(self):
        inherited = {
            "PATH": "/venv/bin:/usr/bin",
            "PYTHONPATH": "/comfy",
            "LD_LIBRARY_PATH": "/venv/lib",
            "VIRTUAL_ENV": "/venv",
            "CIVITAI_API_KEY": "secret",
            "HOME": "/home/u",
            "DISPLAY": ":0",
            "WAYLAND_DISPLAY": "wayland-0",
            "XDG_RUNTIME_DIR": "/run/user/1000",
            "XDG_CURRENT_DESKTOP": "KDE",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
            "LANG": "en_US.UTF-8",
        }
        with patch.dict(os.environ, inherited, clear=True):
            env = file_reveal._child_env()
        self.assertEqual(
            env,
            {
                "PATH": file_reveal.SYSTEM_PATH,
                "HOME": "/home/u",
                "DISPLAY": ":0",
                "WAYLAND_DISPLAY": "wayland-0",
                "XDG_RUNTIME_DIR": "/run/user/1000",
                "XDG_CURRENT_DESKTOP": "KDE",
                "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
                "LANG": "en_US.UTF-8",
            },
        )
        self.assertNotIn("/venv", file_reveal.SYSTEM_PATH)

    def test_linux_asks_the_file_manager_service_and_waits_for_its_reply(self):
        self.popen.return_value.wait.return_value = 0
        with (
            patch.object(file_reveal.sys, "platform", "linux"),
            self.which({"dbus-send": "/usr/bin/dbus-send", "xdg-open": "/x"}),
        ):
            self.reveal()
        self.assertEqual(
            self.argv(),
            [
                "/usr/bin/dbus-send",
                "--session",
                "--print-reply",
                "--reply-timeout=2000",
                "--dest=org.freedesktop.FileManager1",
                "--type=method_call",
                "/org/freedesktop/FileManager1",
                "org.freedesktop.FileManager1.ShowItems",
                f"array:string:{self.path.as_uri()}",
                "string:",
            ],
        )
        self.assertEqual(self.popen.call_count, 1)
        self.popen.return_value.wait.assert_called_once_with(
            timeout=file_reveal.DBUS_WAIT
        )
        self.assert_detached()
        self.reaped.assert_not_called()

    def test_linux_falls_back_to_xdg_open_on_the_folder(self):
        both = {"dbus-send": "/usr/bin/dbus-send", "xdg-open": "/usr/bin/xdg-open"}
        cases = {
            "missing": ({"xdg-open": "/usr/bin/xdg-open"}, None),
            "no reply": (both, 1),
            "start error": (both, OSError("no bus")),
            "hung": (both, subprocess.TimeoutExpired("dbus-send", 3)),
        }
        for name, (available, outcome) in cases.items():
            with self.subTest(name):
                self.popen.reset_mock(side_effect=True, return_value=True)
                self.reaped.reset_mock()
                dbus = MagicMock()
                if isinstance(outcome, OSError):
                    self.popen.side_effect = [outcome, MagicMock()]
                elif isinstance(outcome, subprocess.TimeoutExpired):
                    dbus.wait.side_effect = [outcome, None]
                    self.popen.side_effect = [dbus, MagicMock()]
                else:
                    self.popen.return_value.wait.return_value = outcome
                with (
                    patch.object(file_reveal.sys, "platform", "linux"),
                    self.which(available),
                ):
                    self.reveal()
                self.assertEqual(
                    self.popen.call_args.args[0],
                    ["/usr/bin/xdg-open", str(self.path.parent)],
                )
                self.assert_detached(self.popen.call_count - 1)
                self.reaped.assert_called_once()
                if name == "hung":
                    dbus.kill.assert_called_once_with()

    def test_linux_without_any_opener_is_unavailable(self):
        with (
            patch.object(file_reveal.sys, "platform", "linux"),
            self.which({}),
            self.assertRaises(OpenerUnavailable),
        ):
            self.reveal()
        self.popen.assert_not_called()

    def test_relative_paths_are_refused(self):
        with self.assertRaises(ValueError):
            reveal_in_file_manager(Path("sub/a.png"), self.root)
        self.popen.assert_not_called()

    def test_containment_is_rechecked_just_before_launching(self):
        # A parent swapped for a symlink after the route resolved the path
        # (security review L4) must not open a folder outside the root.
        outside = self.root.parent / "elsewhere"
        outside.mkdir()
        (outside / "a.png").write_bytes(b"png")
        (self.root / "sub" / "a.png").unlink()
        (self.root / "sub").rmdir()
        try:
            os.symlink(outside, self.root / "sub", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        missing = self.root / "gone.png"
        folder = self.root / "folder.png"
        folder.mkdir()
        for path in (self.path, missing, folder):
            for platform in ("linux", "darwin", "win32"):
                with (
                    self.subTest(path=path.name, platform=platform),
                    patch.object(file_reveal.sys, "platform", platform),
                    patch.object(file_reveal, "_windows_select") as shell,
                    self.assertRaises(file_reveal.RevealTargetChanged),
                ):
                    self.reveal(path)
                shell.assert_not_called()
        self.popen.assert_not_called()


class TestReaping(unittest.TestCase):
    def test_reap_waits_on_a_daemon_thread(self):
        process = MagicMock()
        with patch.object(file_reveal.threading, "Thread") as thread:
            file_reveal._reap(process)
        thread.assert_called_once()
        kwargs = thread.call_args.kwargs
        self.assertIs(kwargs["target"], process.wait)
        self.assertIs(kwargs["daemon"], True)
        thread.return_value.start.assert_called_once_with()

    def test_reveal_runs_on_its_own_single_worker(self):
        executor = file_reveal.reveal_executor()
        self.assertIs(file_reveal.reveal_executor(), executor)
        self.assertEqual(executor._max_workers, 1)
        name = executor.submit(lambda: file_reveal.threading.current_thread().name)
        self.assertTrue(name.result(timeout=5).startswith("pm-reveal"))


class FakeShell:
    """ole32 and shell32 stand-ins recording the shell calls (H1)."""

    def __init__(self, init=0, pidl=1234, select=0):
        self.calls = []
        self.ole32 = types.SimpleNamespace(
            CoInitializeEx=self.fn("CoInitializeEx", init),
            CoUninitialize=self.fn("CoUninitialize", None),
        )
        self.shell32 = types.SimpleNamespace(
            ILCreateFromPathW=self.fn("ILCreateFromPathW", pidl),
            SHOpenFolderAndSelectItems=self.fn("SHOpenFolderAndSelectItems", select),
            ILFree=self.fn("ILFree", None),
        )

    def fn(self, name, result):
        def call(*args):
            self.calls.append((name, args))
            if isinstance(result, Exception):
                raise result
            return result

        return MagicMock(side_effect=call)

    def names(self):
        return [name for name, _ in self.calls]


class TestWindowsSelect(unittest.TestCase):
    def select(self, shell, path=r"C:\out\a,\b.png"):
        with patch.object(
            file_reveal, "_windows_libraries", return_value=(shell.ole32, shell.shell32)
        ):
            return file_reveal._windows_select(Path(path)), shell

    def test_selects_the_item_and_frees_everything(self):
        path = r"C:\out\a,\Windows\System32\calc.exe,b\x.png"
        selected, shell = self.select(FakeShell(), path)
        self.assertIs(selected, True)
        self.assertEqual(
            shell.names(),
            [
                "CoInitializeEx",
                "ILCreateFromPathW",
                "SHOpenFolderAndSelectItems",
                "ILFree",
                "CoUninitialize",
            ],
        )
        self.assertEqual(dict(shell.calls)["ILCreateFromPathW"], (str(Path(path)),))
        self.assertEqual(
            dict(shell.calls)["SHOpenFolderAndSelectItems"], (1234, 0, None, 0)
        )
        self.assertEqual(dict(shell.calls)["ILFree"], (1234,))

    def test_declares_the_shell_signatures(self):
        _, shell = self.select(FakeShell())
        create = shell.shell32.ILCreateFromPathW
        self.assertEqual(create.argtypes, [ctypes.c_wchar_p])
        self.assertIs(create.restype, ctypes.c_void_p)
        open_select = shell.shell32.SHOpenFolderAndSelectItems
        self.assertEqual(
            open_select.argtypes,
            [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_ulong],
        )
        self.assertIs(open_select.restype, ctypes.c_long)
        self.assertEqual(shell.shell32.ILFree.argtypes, [ctypes.c_void_p])
        self.assertEqual(
            shell.ole32.CoInitializeEx.argtypes, [ctypes.c_void_p, ctypes.c_ulong]
        )
        self.assertIs(shell.ole32.CoInitializeEx.restype, ctypes.c_long)

    def test_already_initialized_com_is_used_and_left_alone(self):
        selected, shell = self.select(FakeShell(init=file_reveal.RPC_E_CHANGED_MODE))
        self.assertIs(selected, True)
        self.assertNotIn("CoUninitialize", shell.names())
        selected, shell = self.select(FakeShell(init=file_reveal.S_FALSE))
        self.assertIs(selected, True)
        self.assertEqual(shell.names()[-1], "CoUninitialize")

    def test_failures_report_false_without_raising(self):
        cases = {
            "com": FakeShell(init=-2147024882),
            "no pidl": FakeShell(pidl=None),
            "select failed": FakeShell(select=-2147467259),
            "raises": FakeShell(select=OSError("access violation")),
        }
        for name, shell in cases.items():
            with self.subTest(name):
                selected, shell = self.select(shell)
                self.assertIs(selected, False)
                if "ILCreateFromPathW" in shell.names() and name != "no pidl":
                    self.assertIn("ILFree", shell.names())
        with patch.object(file_reveal, "_windows_libraries", side_effect=OSError):
            self.assertIs(file_reveal._windows_select(Path("C:/a.png")), False)

    def test_libraries_are_unavailable_off_windows(self):
        if sys.platform == "win32":
            self.skipTest("loads the real shell libraries on Windows")
        with self.assertRaises((AttributeError, OSError)):
            file_reveal._windows_libraries()


class TestRevealRoute(ImageAPITestCase):
    async def get_application(self):
        app = await super().get_application()
        self.opened, self.roots, self.threads = [], [], []
        self.outcome = None

        def opener(path, root):
            self.threads.append(file_reveal.threading.current_thread().name)
            if self.outcome is not None:
                raise self.outcome
            self.opened.append(path)
            self.roots.append(root)

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
        self.assertEqual(self.roots, [self.output_dir])
        # Its own worker, never ComfyUI's shared default executor (L1).
        self.assertTrue(self.threads[0].startswith("pm-reveal"), self.threads)

    async def test_target_changed_before_launch_is_404(self):
        make_png(self.output_dir / "sub" / "a.png")
        self.outcome = file_reveal.RevealTargetChanged()
        status, data = await self.post(self.body())
        self.assertEqual(status, 404)
        self.assertEqual(data, {"success": False, "error": "Image file not found"})

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
