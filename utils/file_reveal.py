"""Show one gallery file in this computer's file manager (TESTING.md D38).

The route that calls this is local-only (``is_local_request``) and hands in a
path it has already resolved inside a gallery root. Every opener runs as an
argument list with an absolute executable, no shell and no inherited streams,
so neither a file name nor ``PATH`` can change what runs or what it parses.
"""

import ipaddress
import os
import shutil

# Only argv launches of a fixed file manager (see _launch).
import subprocess  # nosec B404
import sys
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

# A local reverse proxy makes every client look like loopback, so a request
# that carries any of these counts as remote.
PROXY_HEADERS = ("Forwarded", "X-Forwarded-For", "X-Real-IP", "X-Forwarded-Host")
# dbus-send exits at once when it cannot reach the session bus; a start that
# outlives this is treated as delivered and is not waited on further.
DBUS_START_TIMEOUT = 2.0
FILE_MANAGER_DEST = "org.freedesktop.FileManager1"


class OpenerUnavailable(Exception):
    """No file manager can be started on this computer."""


def _loopback_address(value: str | None) -> bool:
    if not value:
        return False
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return (mapped or address).is_loopback


def _loopback_host(host: str | None) -> bool:
    """True for a Host or Origin host naming this computer.

    Rejects every other name, so a DNS-rebinding page whose name now resolves
    to 127.0.0.1 still reads as remote.
    """
    if not host:
        return False
    host = host.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    return _loopback_address(host.strip("[]"))


def _authority_host(authority: str) -> str | None:
    try:
        return urlsplit(f"//{authority}").hostname
    except ValueError:
        return None


def is_local_request(remote: str | None, headers: Mapping) -> bool:
    """True only for a direct loopback client addressing this computer by a
    loopback name, with no proxy header and no foreign Origin."""
    if any(name in headers for name in PROXY_HEADERS):
        return False
    if not _loopback_address(remote):
        return False
    if not _loopback_host(_authority_host(headers.get("Host", ""))):
        return False
    origin = headers.get("Origin")
    if origin is None:
        return True
    try:
        return _loopback_host(urlsplit(origin).hostname)
    except ValueError:
        return False


def _executable(name: str) -> str | None:
    """Absolute path of *name* on PATH; a relative PATH entry never counts."""
    found = shutil.which(name)
    return found if found and os.path.isabs(found) else None


def _windows_explorer() -> str | None:
    """Explorer from the Windows directory, never from PATH or the working
    directory (Windows searches the current directory first)."""
    root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if not root or not os.path.isabs(root):
        return None
    candidate = os.path.join(root, "explorer.exe")
    return candidate if os.path.isfile(candidate) else None


def _launch(argv: list[str]) -> subprocess.Popen:
    options = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform != "win32":
        # Its own session, so stopping ComfyUI does not take the window along.
        options["start_new_session"] = True
    # argv[0] is an absolute file manager path and every other element is a
    # fixed flag or an absolute path inside a gallery root; there is no shell.
    return subprocess.Popen(argv, **options)  # nosec B603


def _show_items(path: Path) -> bool:
    """Ask the freedesktop FileManager1 service to select *path*."""
    dbus = _executable("dbus-send")
    if dbus is None:
        return False
    argv = [
        dbus,
        "--session",
        f"--dest={FILE_MANAGER_DEST}",
        "--type=method_call",
        "/org/freedesktop/FileManager1",
        f"{FILE_MANAGER_DEST}.ShowItems",
        f"array:string:{path.as_uri()}",
        "string:",
    ]
    try:
        process = _launch(argv)
    except OSError:
        return False
    try:
        return process.wait(timeout=DBUS_START_TIMEOUT) == 0
    except subprocess.TimeoutExpired:
        return True


def reveal_in_file_manager(path: Path) -> None:
    """Open the file manager at *path* (absolute, an existing regular file).

    Raises OpenerUnavailable when no opener exists and OSError when the one
    found cannot start. Never waits on the file manager itself.
    """
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("path must be absolute")
    if sys.platform == "win32":
        explorer = _windows_explorer()
        if explorer is None:
            raise OpenerUnavailable()
        _launch([explorer, "/select,", str(path)])
        return
    if sys.platform == "darwin":
        opener = _executable("open")
        if opener is None:
            raise OpenerUnavailable()
        _launch([opener, "-R", str(path)])
        return
    if _show_items(path):
        return
    opener = _executable("xdg-open")
    if opener is None:
        raise OpenerUnavailable()
    _launch([opener, str(path.parent)])
