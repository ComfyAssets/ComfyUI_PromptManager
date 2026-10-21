"""Show one gallery file in this computer's file manager (TESTING.md D38).

The route that calls this is local-only (``is_local_request``) and hands in a
path it has already resolved inside a gallery root. Windows goes through the
shell API (no command line exists to parse); macOS and Linux openers run as an
argument list with an absolute executable, no shell and no inherited streams,
so neither a file name nor ``PATH`` can change what runs or what it parses.
"""

import ipaddress
import os
import shutil

# Only POSIX argv launches of a fixed opener (see _launch); Windows never
# starts a process (see _windows_select).
import subprocess  # nosec B404
import sys
import threading
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

# A local reverse proxy makes every client look like loopback, so a request
# that carries any of these counts as remote.
PROXY_HEADERS = ("Forwarded", "X-Forwarded-For", "X-Real-IP", "X-Forwarded-Host")
# dbus-send waits this long for the FileManager1 reply, so its exit status
# says whether a file manager really took the request.
DBUS_REPLY_TIMEOUT_MS = 2000
# The dbus-send process itself gets a little longer before it is killed.
DBUS_WAIT = 3.0
FILE_MANAGER_DEST = "org.freedesktop.FileManager1"
# Openers are looked up here only, never on ComfyUI's (venv) PATH, and the
# children get this as their PATH.
SYSTEM_PATH = os.pathsep.join(
    ("/usr/local/bin", "/usr/bin", "/bin", "/run/current-system/sw/bin")
)
MACOS_OPEN = "/usr/bin/open"
# What a desktop opener needs from ComfyUI's environment; everything else
# (venv PATH, PYTHONPATH, LD_LIBRARY_PATH, API keys) stays behind.
CHILD_ENV_NAMES = (
    "HOME",
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XDG_RUNTIME_DIR",
    "XDG_CURRENT_DESKTOP",
    "XDG_SESSION_TYPE",
    "XDG_DATA_DIRS",
    "XDG_CONFIG_DIRS",
    "DBUS_SESSION_BUS_ADDRESS",
    "LANG",
    "LC_ALL",
)


class OpenerUnavailable(Exception):
    """No file manager can be started on this computer."""


class RevealTargetChanged(Exception):
    """The file is no longer a regular file inside its root."""


_executor_lock = threading.Lock()
_executor: ThreadPoolExecutor | None = None


def reveal_executor() -> ThreadPoolExecutor:
    """The one worker that runs openers, so a slow file manager or dbus reply
    never holds a thread of ComfyUI's shared default executor."""
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="pm-reveal"
            )
        return _executor


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
    """Absolute path of *name* on the fixed SYSTEM_PATH, never a relative hit."""
    found = shutil.which(name, path=SYSTEM_PATH)
    return found if found and os.path.isabs(found) else None


def _macos_open() -> str | None:
    """macOS's own ``open``, at its fixed system path."""
    if os.path.isfile(MACOS_OPEN) and os.access(MACOS_OPEN, os.X_OK):
        return MACOS_OPEN
    return None


def _child_env() -> dict[str, str]:
    env = {name: os.environ[name] for name in CHILD_ENV_NAMES if name in os.environ}
    return {"PATH": SYSTEM_PATH, **env}


def _reap(process: subprocess.Popen) -> None:
    """Wait for *process* on a daemon thread, so it never lingers as a zombie
    and is never waited on by the caller."""
    threading.Thread(target=process.wait, name="pm-reveal-reaper", daemon=True).start()


def _contained_file(path: Path, root: Path) -> Path:
    """*path*'s real path when it is still a regular file inside *root*.

    Run right before launching: a parent swapped for a symlink since the
    route resolved the path must not open a folder outside the root.
    """
    real = os.path.realpath(path)
    real_root = os.path.realpath(root)
    try:
        inside = os.path.commonpath(
            [os.path.normcase(real), os.path.normcase(real_root)]
        ) == os.path.normcase(real_root)
    except ValueError:
        inside = False
    if not inside or real == real_root or not os.path.isfile(real):
        raise RevealTargetChanged()
    return Path(real)


# HRESULTs as the signed 32-bit values ctypes.c_long returns.
S_OK = 0
S_FALSE = 1
RPC_E_CHANGED_MODE = -2147417850  # 0x80010106: COM already set up otherwise
COINIT_APARTMENTTHREADED = 0x2
COINIT_DISABLE_OLE1DDE = 0x4


def _windows_libraries():
    """Private ole32 and shell32 handles, so the signatures set here never
    change another library's view of ``ctypes.windll``."""
    import ctypes

    return ctypes.WinDLL("ole32"), ctypes.WinDLL("shell32")


def _declare_shell_signatures(ole32, shell32) -> None:
    import ctypes

    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None
    shell32.ILCreateFromPathW.argtypes = [ctypes.c_wchar_p]
    shell32.ILCreateFromPathW.restype = ctypes.c_void_p
    shell32.SHOpenFolderAndSelectItems.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.c_ulong,
    ]
    shell32.SHOpenFolderAndSelectItems.restype = ctypes.c_long
    shell32.ILFree.argtypes = [ctypes.c_void_p]
    shell32.ILFree.restype = None


def _windows_select(path: Path) -> bool:
    """Open Explorer on *path*'s folder with *path* selected, via the shell
    API. The path is one wide-string value, so no command line is ever
    parsed (Explorer splits its own on commas). False on any failure; never
    raises into the route."""
    try:
        ole32, shell32 = _windows_libraries()
        _declare_shell_signatures(ole32, shell32)
        result = ole32.CoInitializeEx(
            None, COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE
        )
        if result not in (S_OK, S_FALSE, RPC_E_CHANGED_MODE):
            return False
        try:
            pidl = shell32.ILCreateFromPathW(str(path))
            if not pidl:
                return False
            try:
                return shell32.SHOpenFolderAndSelectItems(pidl, 0, None, 0) >= 0
            finally:
                shell32.ILFree(pidl)
        finally:
            # Only a successful CoInitializeEx (S_OK or S_FALSE) is balanced.
            if result in (S_OK, S_FALSE):
                ole32.CoUninitialize()
    except Exception:  # any shell failure falls back to the folder
        return False


def _reveal_windows(path: Path) -> None:
    if _windows_select(path):
        return
    startfile = getattr(os, "startfile", None)
    if startfile is None:
        raise OpenerUnavailable()
    # ShellExecute on the folder itself: no command line is built or parsed.
    startfile(str(path.parent), "explore")


def _launch(argv: list[str]) -> subprocess.Popen:
    options = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
        # Its own session, so stopping ComfyUI does not take the window along.
        "start_new_session": True,
        "env": _child_env(),
    }
    # POSIX only (Windows uses the shell API): argv[0] is an absolute opener,
    # the rest are fixed flags and one absolute path inside a gallery root,
    # passed to execve as separate strings with no shell to re-parse them.
    return subprocess.Popen(argv, **options)  # nosec B603


def _show_items(path: Path) -> bool:
    """Ask the freedesktop FileManager1 service to select *path*; True only
    when the service replied (``--print-reply`` makes the exit status real)."""
    dbus = _executable("dbus-send")
    if dbus is None:
        return False
    argv = [
        dbus,
        "--session",
        "--print-reply",
        f"--reply-timeout={DBUS_REPLY_TIMEOUT_MS}",
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
        return process.wait(timeout=DBUS_WAIT) == 0
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        return False


def reveal_in_file_manager(path: Path, root: Path) -> None:
    """Open the file manager at *path*, a regular file inside *root*.

    Re-checks that containment first (RevealTargetChanged). Raises
    OpenerUnavailable when no opener exists and OSError when the one found
    cannot start. Never waits on the file manager itself.
    """
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("path must be absolute")
    path = _contained_file(path, Path(root))
    if sys.platform == "win32":
        _reveal_windows(path)
        return
    if sys.platform == "darwin":
        opener = _macos_open()
        if opener is None:
            raise OpenerUnavailable()
        _reap(_launch([opener, "-R", str(path)]))
        return
    if _show_items(path):
        return
    opener = _executable("xdg-open")
    if opener is None:
        raise OpenerUnavailable()
    _reap(_launch([opener, str(path.parent)]))
