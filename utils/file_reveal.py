"""Show one gallery file in this computer's file manager (TESTING.md D38).

The route that calls this is local-only (``is_local_request``) and
single-flight (``RevealGate``), and hands in a path it has already resolved
inside a gallery root, which is re-checked just before launching. Windows
goes through the shell API (no command line exists to parse); macOS and Linux
openers run as an argument list with an absolute executable from a fixed
system PATH, no shell, no inherited streams and a minimal environment, so
neither a file name nor ``PATH`` can change what runs or what it parses.
"""

import ipaddress
import os
import posixpath
import shutil

# Only POSIX argv launches of a fixed opener (see _launch); Windows never
# starts a process (see _select_item).
import subprocess  # nosec B404
import sys
import threading
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

# A local reverse proxy makes every client look like loopback, so a request
# that carries any forwarding-style header counts as remote: these names, and
# every X-Forwarded-*, X-Original-* and *-Client-IP header.
FORWARDING_HEADERS = frozenset(
    (
        "forwarded",
        "via",
        "x-real-ip",
        "cf-connecting-ip",
        "true-client-ip",
        "x-client-ip",
        "x-cluster-client-ip",
        "fastly-client-ip",
    )
)
FORWARDING_PREFIXES = ("x-forwarded-", "x-original-")
FORWARDING_SUFFIXES = ("-client-ip",)
DEFAULT_PORTS = {"http": 80, "https": 443}
# One reveal at a time per process, then a pause before the next.
REVEAL_COOLDOWN = 1.0
# dbus-send waits this long for the FileManager1 reply, so its exit status
# says whether a file manager really took the request.
DBUS_REPLY_TIMEOUT_MS = 2000
# The dbus-send process itself gets a little longer before it is killed.
DBUS_WAIT = 3.0
# An opener that fails says so at once; one still running after this long has
# started a file manager (it may stay in the foreground).
OPENER_WAIT = 1.0
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
    "XDG_SESSION_DESKTOP",
    "DESKTOP_SESSION",
    "XDG_DATA_DIRS",
    "XDG_CONFIG_DIRS",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    # The X11 cookie: a non-default path under GDM or SDDM.
    "XAUTHORITY",
    "DBUS_SESSION_BUS_ADDRESS",
    "LANG",
    "LC_ALL",
    # What xdg-open reads to choose an opener (kde-open6 on Plasma 6).
    "KDE_FULL_SESSION",
    "KDE_SESSION_VERSION",
    "GNOME_DESKTOP_SESSION_ID",
    "MATE_DESKTOP_SESSION_ID",
    "LXQT_SESSION_CONFIG",
)


class OpenerUnavailable(Exception):
    """No file manager can be started on this computer."""


class OpenerFailed(Exception):
    """The file manager's opener ran and reported a failure."""


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


def _loopback_host(host: str) -> bool:
    """True for a Host name naming this computer.

    Rejects every other name, so a DNS-rebinding page whose name now resolves
    to 127.0.0.1 still reads as remote.
    """
    host = host.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    return _loopback_address(host.strip("[]"))


def _authority(authority: str) -> tuple[str, int | None] | None:
    """``(host, port)`` of a Host value, or None when it does not parse."""
    try:
        parts = urlsplit(f"//{authority}")
        host, port = parts.hostname, parts.port
    except ValueError:
        return None
    return (host, port) if host else None


def _forwarded(headers: Mapping) -> bool:
    for name in headers.keys():
        name = name.lower()
        if (
            name in FORWARDING_HEADERS
            or name.startswith(FORWARDING_PREFIXES)
            or name.endswith(FORWARDING_SUFFIXES)
        ):
            return True
    return False


def _same_origin(origin: str, scheme: str, host: tuple[str, int | None]) -> bool:
    """True when *origin* is exactly this request's scheme://host:port."""
    default = DEFAULT_PORTS.get(scheme)
    if default is None:
        return False
    try:
        parts = urlsplit(origin)
        origin_host, origin_port = parts.hostname, parts.port
    except ValueError:
        return False
    if (
        parts.scheme != scheme
        or not origin_host
        or parts.username is not None
        or parts.password is not None
        or parts.path
        or parts.query
        or parts.fragment
    ):
        return False
    host_name, host_port = host
    return (origin_host.rstrip("."), origin_port or default) == (
        host_name.rstrip("."),
        host_port or default,
    )


def is_local_request(remote: str | None, headers: Mapping, scheme="http") -> bool:
    """True only for a same-origin browser request from this computer.

    That is: no forwarding-style header, a loopback client, a loopback name
    in Host, an ``Origin`` that is exactly this request's scheme://host:port
    (browsers send it on every fetch POST, so a missing one is refused), and
    ``Sec-Fetch-Site: same-origin`` whenever that header is sent.
    """
    if _forwarded(headers):
        return False
    if not _loopback_address(remote):
        return False
    host = _authority(headers.get("Host", ""))
    if host is None or not _loopback_host(host[0]):
        return False
    origin = headers.get("Origin")
    if not origin or not _same_origin(origin, scheme, host):
        return False
    site = headers.get("Sec-Fetch-Site")
    return site is None or site == "same-origin"


class RevealGate:
    """Single flight plus a cooldown: at most one reveal runs per process,
    and the next may start only *cooldown* seconds after it finished."""

    def __init__(self, cooldown: float = REVEAL_COOLDOWN, clock=time.monotonic):
        self.cooldown = cooldown
        self._clock = clock
        self._lock = threading.Lock()
        self._busy = False
        self._finished: float | None = None

    def try_enter(self) -> bool:
        with self._lock:
            if self._busy:
                return False
            if (
                self._finished is not None
                and self._clock() - self._finished < self.cooldown
            ):
                return False
            self._busy = True
            return True

    def leave(self) -> None:
        with self._lock:
            self._busy = False
            self._finished = self._clock()


def _executable(name: str) -> str | None:
    """Absolute path of *name* on the fixed SYSTEM_PATH, never a relative hit.

    Only the Linux and macOS openers are looked up here, so absoluteness follows
    POSIX rules rather than the host's (Python 3.13 on Windows no longer counts
    "/usr/bin/x" as absolute).
    """
    found = shutil.which(name, path=SYSTEM_PATH)
    return found if found and posixpath.isabs(found) else None


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


def _shell_libraries():
    """The declared ole32 and shell32, or None where they cannot be loaded."""
    try:
        ole32, shell32 = _windows_libraries()
        _declare_shell_signatures(ole32, shell32)
    except Exception:  # no shell libraries: the folder fallback still runs
        return None
    return ole32, shell32


@contextmanager
def _com_apartment(ole32):
    """The calling thread inside COM for the block; yields whether COM is
    usable. Only a successful CoInitializeEx (S_OK or S_FALSE) is balanced,
    so the persistent reveal thread never drifts."""
    try:
        result = ole32.CoInitializeEx(
            None, COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE
        )
    except Exception:  # an unusable ole32 leaves COM alone
        result = None
    try:
        yield result in (S_OK, S_FALSE, RPC_E_CHANGED_MODE)
    finally:
        if result in (S_OK, S_FALSE):
            ole32.CoUninitialize()


def _select_item(shell32, path: Path) -> bool:
    """Open Explorer on *path*'s folder with *path* selected, via the shell
    API. The path is one wide-string value, so no command line is ever
    parsed (Explorer splits its own on commas). False on any failure; never
    raises into the route."""
    try:
        pidl = shell32.ILCreateFromPathW(str(path))
        if not pidl:
            return False
        try:
            return shell32.SHOpenFolderAndSelectItems(pidl, 0, None, 0) >= 0
        finally:
            shell32.ILFree(pidl)
    except Exception:  # any shell failure falls back to the folder
        return False


def _explore(folder: Path) -> None:
    startfile = getattr(os, "startfile", None)
    if startfile is None:
        raise OpenerUnavailable()
    # ShellExecute on the folder itself: no command line is built or parsed.
    startfile(str(folder), "explore")


def _reveal_windows(path: Path) -> None:
    libraries = _shell_libraries()
    if libraries is None:
        _explore(path.parent)
        return
    ole32, shell32 = libraries
    with _com_apartment(ole32) as ready:
        if ready and _select_item(shell32, path):
            return
        # ShellExecute inside the same apartment: shell extensions may need COM.
        _explore(path.parent)


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


def _launch_opener(argv: list[str]) -> None:
    """Start an opener and report its failure (OpenerFailed), never waiting on
    a file manager it stays attached to: that one is left to the reaper."""
    process = _launch(argv)
    try:
        status = process.wait(timeout=OPENER_WAIT)
    except subprocess.TimeoutExpired:
        _reap(process)
        return
    if status != 0:
        raise OpenerFailed(f"exit status {status}")


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
    OpenerUnavailable when no opener exists or Linux has no graphical session,
    OSError when the one found cannot start and OpenerFailed when it reports a
    failure. Never waits on the file manager itself.
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
        _launch_opener([opener, "-R", str(path)])
        return
    if _show_items(path):
        return
    opener = _executable("xdg-open")
    has_display = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    if opener is None or not has_display:
        raise OpenerUnavailable()
    _launch_opener([opener, str(path.parent)])
