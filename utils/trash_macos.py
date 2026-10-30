"""The macOS Trash backend (#499): NSFileManager through ctypes, or refuse.

``-[NSFileManager trashItemAtURL:resultingItemURL:error:]`` moves a file into
its volume's trash folder (``~/.Trash`` on the startup volume,
``<volume>/.Trashes/<uid>`` elsewhere) or fails with an ``NSError``; it renames
name clashes itself and never replaces a file. Before calling it,
:class:`MacTrashBackend` refuses what it cannot show to be recoverable:
network volumes, read-only volumes, iCloud Drive and File Provider sync
folders, a trash folder inside an output root, and files macOS would refuse
for permission. Nothing here unlinks, copies or overwrites a file.

The :class:`Bridge` is the only code that touches the Objective-C runtime. It
sends ten fixed messages, each through ``objc_msgSend`` cast to its exact
prototype (required on arm64), checks every object for nil before a send (a
nil can raise an Objective-C exception, which would abort the process), and
runs each call inside its own autorelease pool. It is loaded lazily, on the
first ``available``/``move``; when it cannot load, every file answers
``no-trash`` as before. The facts behind these rules were measured on macOS
14, 15 (x86_64) and 26 (docs/egm/499-macos-trash/design.md §4).
"""

from __future__ import annotations

import ctypes
import logging
import os
import stat
import sys
import threading
from dataclasses import dataclass

from . import trash

logger = logging.getLogger("prompt_manager.trash")

COCOA = "NSCocoaErrorDomain"
POSIX = "NSPOSIXErrorDomain"
FEATURE_UNSUPPORTED = 3328
_ENOENT = 2
# Top-level Cocoa codes of a refused move (design §7.5); anything else is "failed".
_MOVE_CODES = {
    FEATURE_UNSUPPORTED: trash.NO_TRASH,
    642: trash.NO_TRASH,  # NSFileWriteVolumeReadOnlyError
    513: trash.NO_PERMISSION,  # NSFileWriteNoPermissionError
    257: trash.NO_PERMISSION,  # NSFileReadNoPermissionError
}
_LOCK_FLAGS = stat.UF_IMMUTABLE | stat.SF_IMMUTABLE | stat.UF_APPEND | stat.SF_APPEND
_CLOUD_FOLDERS = (("Library", "Mobile Documents"), ("Library", "CloudStorage"))


@dataclass(frozen=True)
class NSErr:
    domain: str
    code: int
    under_domain: str | None = None
    under_code: int | None = None


@dataclass(frozen=True)
class Facts:
    """Resource values of a file's URL; ``None`` means the lookup failed or was nil."""

    local: bool | None
    read_only: bool | None
    root_fs: bool | None
    volume: str | None
    ubiquitous: bool | None


def error_detail(error: NSErr | None) -> str:
    """``"<domain> <code> (<under_domain> <under_code>)"`` for the log."""
    if error is None:
        return "no NSError"
    text = f"{error.domain} {error.code}"
    if error.under_domain is not None:
        text += f" ({error.under_domain} {error.under_code})"
    return text


def refusal_code(error: NSErr | None) -> str:
    if error is None or error.domain != COCOA:
        return trash.FAILED
    return _MOVE_CODES.get(error.code, trash.FAILED)


def _within(path: str, folder: str) -> bool:
    path = os.path.normcase(path)
    folder = os.path.normcase(folder)
    trimmed = folder.rstrip("\\/") or folder
    return path == trimmed or path.startswith(trimmed.rstrip(os.sep) + os.sep)


def _identity(path: str):
    info = os.stat(path)
    return info.st_dev, info.st_ino


def inside_any_root(path: str, roots) -> bool:
    """Whether *path* (existing or not) lies in an output root, by file identity.

    Every existing folder from *path* up to the top is compared with the
    roots' ``(st_dev, st_ino)``, so case, NFC/NFD and symlinked spellings of a
    root all match.
    """
    identities = set()
    for root in roots:
        try:
            identities.add(_identity(str(root)))
        except OSError:
            continue
    current = os.path.abspath(path)
    while True:
        try:
            if _identity(current) in identities:
                return True
        except OSError:
            pass
        parent = os.path.dirname(current)
        if parent == current:
            return False
        current = parent


def _same_folder(left: str, right: str) -> bool:
    try:
        return _identity(left) == _identity(right)
    except OSError:
        return os.path.realpath(left) == os.path.realpath(right)


class MacTrashBackend:
    """``available``/``move`` for macOS; the bridge and file checks are seams."""

    def __init__(
        self, bridge=None, uid=None, home=None, lstat=os.lstat, access=os.access
    ):
        self._bridge = bridge
        self._load_failed = False
        self._lock = threading.Lock()
        if uid is None:
            uid = os.getuid() if hasattr(os, "getuid") else -1
        self._uid = uid
        self._home = home
        self._lstat = lstat
        self._access = access

    def _get_bridge(self):
        if self._bridge is None and not self._load_failed:
            with self._lock:
                if self._bridge is None and not self._load_failed:
                    try:
                        self._bridge = load_bridge()
                    except (OSError, AttributeError, ValueError) as error:
                        self._load_failed = True
                        logger.warning("The macOS trash is unavailable: %s", error)
        if self._bridge is None:
            raise trash.TrashRefused(trash.NO_TRASH, "Foundation bridge not loaded")
        return self._bridge

    def _home_dir(self) -> str:
        return os.path.realpath(self._home or os.path.expanduser("~"))

    def _plan(self, path: str, roots):
        """``(trash folder, facts)`` for *path*; raises TrashRefused (design §7.2)."""
        bridge = self._get_bridge()
        real = os.path.realpath(path)
        facts = bridge.facts(real)
        if facts.local is not True:
            raise trash.TrashRefused(trash.NETWORK)
        if facts.read_only is not False:
            raise trash.TrashRefused(trash.NO_TRASH, "read-only volume")
        home = self._home_dir()
        if facts.ubiquitous is True or any(
            _within(real, os.path.join(home, *parts)) for parts in _CLOUD_FOLDERS
        ):
            raise trash.TrashRefused(trash.CLOUD_SYNC)
        folder, error = bridge.trash_dir(real)
        if folder is None:
            folder = self._predict(facts, error)
        if inside_any_root(folder, roots):
            raise trash.TrashRefused(trash.TRASH_IN_ROOT)
        self._check_permission(real)
        return folder, facts

    def _predict(self, facts: Facts, error: NSErr | None) -> str:
        """``<volume>/.Trashes/<uid>`` for a volume whose trash is not made yet."""
        missing = (
            error is not None
            and error.domain == COCOA
            and error.code == FEATURE_UNSUPPORTED
            and error.under_domain == POSIX
            and error.under_code == _ENOENT
        )
        if not missing or facts.root_fs is not False or not facts.volume:
            raise trash.TrashRefused(trash.NO_TRASH, error_detail(error))
        trashes = os.path.join(facts.volume, ".Trashes")
        try:
            info = self._lstat(trashes)
        except FileNotFoundError:
            info = None
        except OSError as failure:
            raise trash.TrashRefused(trash.NO_TRASH, str(failure)) from failure
        if info is not None and not stat.S_ISDIR(info.st_mode):
            raise trash.TrashRefused(trash.NO_TRASH, ".Trashes is not a folder")
        return os.path.join(trashes, str(self._uid))

    def _check_permission(self, real: str) -> None:
        parent = os.path.dirname(real)
        if not self._access(parent, os.W_OK | os.X_OK):
            raise trash.TrashRefused(trash.NO_PERMISSION, "folder not writable")
        try:
            file_info = self._lstat(real)
            parent_info = self._lstat(parent)
        except OSError as failure:
            raise trash.TrashRefused(trash.FAILED, str(failure)) from failure
        for info in (file_info, parent_info):
            if getattr(info, "st_flags", 0) & _LOCK_FLAGS:
                raise trash.TrashRefused(trash.NO_PERMISSION, "immutable flag")
        if parent_info.st_mode & stat.S_ISVTX and file_info.st_uid != self._uid:
            raise trash.TrashRefused(trash.NO_PERMISSION, "sticky folder, foreign file")

    def _label(self, folder: str, facts: Facts) -> str:
        if _same_folder(folder, os.path.join(self._home_dir(), ".Trash")):
            return trash.HOME_TRASH_LABEL
        real = os.path.realpath(folder)
        volume = os.path.realpath(facts.volume) if facts.volume else None
        if volume and volume != os.path.realpath(os.sep) and _within(real, volume):
            relative = os.path.relpath(real, volume).replace(os.sep, "/")
            return f"{relative} on its drive"
        return real

    def available(self, path: str, roots) -> str | None:
        """None when *path* can be trashed, else the refusal code (no writes)."""
        try:
            self._plan(path, roots)
        except trash.TrashRefused as refused:
            return refused.code
        except OSError:
            return trash.NO_TRASH
        return None

    def move(self, path: str, roots) -> str:
        """Move *path* into its trash folder and return the label (design §7.4)."""
        try:
            planned, facts = self._plan(path, roots)
        except OSError as failure:
            raise trash.TrashRefused(trash.NO_TRASH, str(failure)) from failure
        real = os.path.realpath(path)
        ok, resulting, error = self._get_bridge().trash(real)
        if not ok:
            raise trash.TrashRefused(refusal_code(error), error_detail(error))
        if os.path.lexists(real):
            raise trash.TrashRefused(trash.FAILED, "reported moved, still in place")
        if not resulting or inside_any_root(os.path.dirname(resulting), roots):
            raise trash.TrashUnconfirmed(f"{real} -> {resulting!r}")
        folder = os.path.dirname(resulting)
        if not _same_folder(folder, planned):
            logger.warning(
                "macOS trashed %s to %s, not the planned %s", real, resulting, planned
            )
        return self._label(folder, facts)


# ── the Foundation bridge ────────────────────────────────────────────────

_NS_TRASH_DIRECTORY = 102
_NS_USER_DOMAIN_MASK = 1
_LIBOBJC = "/usr/lib/libobjc.A.dylib"
_FOUNDATION = "/System/Library/Frameworks/Foundation.framework/Foundation"
_KEYS = (
    "NSURLVolumeIsLocalKey",
    "NSURLVolumeIsReadOnlyKey",
    "NSURLVolumeIsRootFileSystemKey",
    "NSURLVolumeURLKey",
    "NSURLIsUbiquitousItemKey",
    "NSUnderlyingErrorKey",
)
_REQUIRED_SELECTORS = (
    ("NSURL", "fileURLWithFileSystemRepresentation:isDirectory:relativeToURL:"),
    ("NSURL", "getResourceValue:forKey:error:"),
    ("NSFileManager", "URLForDirectory:inDomain:appropriateForURL:create:error:"),
    ("NSFileManager", "trashItemAtURL:resultingItemURL:error:"),
)


class _Nil(Exception):
    """An Objective-C object was nil where a send needs one."""


class Bridge:  # pragma: no cover - macOS only; the native-trash job runs it
    """Foundation through ctypes: ``facts``, ``trash_dir`` and ``trash`` (design §6)."""

    def __init__(self):
        if sys.platform != "darwin":
            raise OSError("the Foundation bridge exists only on macOS")
        objc = ctypes.CDLL(_LIBOBJC)
        foundation = ctypes.CDLL(_FOUNDATION)
        void_p, char_p, byte = ctypes.c_void_p, ctypes.c_char_p, ctypes.c_byte
        objc.objc_getClass.argtypes = [char_p]
        objc.objc_getClass.restype = void_p
        objc.sel_registerName.argtypes = [char_p]
        objc.sel_registerName.restype = void_p
        objc.objc_autoreleasePoolPush.argtypes = []
        objc.objc_autoreleasePoolPush.restype = void_p
        objc.objc_autoreleasePoolPop.argtypes = [void_p]
        objc.objc_autoreleasePoolPop.restype = None
        self._objc = objc
        address = ctypes.cast(objc.objc_msgSend, void_p).value

        def send(restype, *argtypes):
            return ctypes.CFUNCTYPE(restype, void_p, void_p, *argtypes)(address)

        out = ctypes.POINTER(void_p)
        self._send_id = send(void_p)
        self._send_responds = send(byte, void_p)
        self._send_url = send(void_p, char_p, byte, void_p)
        self._send_resource = send(byte, out, void_p, out)
        self._send_bool = send(byte)
        self._send_dir = send(void_p, ctypes.c_ulong, ctypes.c_ulong, void_p, byte, out)
        self._send_trash = send(byte, void_p, out, out)
        self._send_cstr = send(char_p)
        self._send_long = send(ctypes.c_long)
        self._send_object = send(void_p, void_p)
        self._keys = {}
        for name in _KEYS:
            value = void_p.in_dll(foundation, name).value
            if not value:
                raise ValueError(f"Foundation constant {name} is nil")
            self._keys[name] = value
        for class_name, selector in _REQUIRED_SELECTORS:
            if not self._send_responds(
                self._class(class_name),
                self._sel("respondsToSelector:"),
                self._sel(selector),
            ) and not self._instances_respond(class_name, selector):
                raise ValueError(f"{class_name} does not respond to {selector}")

    # helpers (call inside a pool) ------------------------------------------------

    def _class(self, name: str):
        value = self._objc.objc_getClass(name.encode())
        if not value:
            raise ValueError(f"no Objective-C class {name}")
        return value

    def _sel(self, name: str):
        return self._objc.sel_registerName(name.encode())

    def _instances_respond(self, class_name: str, selector: str) -> bool:
        return (
            self._send_responds(
                self._class(class_name),
                self._sel("instancesRespondToSelector:"),
                self._sel(selector),
            )
            != 0
        )

    @staticmethod
    def _need(value):
        if not value:
            raise _Nil()
        return value

    def _manager(self):
        return self._need(
            self._send_id(self._class("NSFileManager"), self._sel("defaultManager"))
        )

    def _url(self, path: str):
        return self._need(
            self._send_url(
                self._class("NSURL"),
                self._sel(
                    "fileURLWithFileSystemRepresentation:isDirectory:relativeToURL:"
                ),
                os.fsencode(path),
                0,
                None,
            )
        )

    def _path(self, url) -> str | None:
        if not url:
            return None
        raw = self._send_cstr(url, self._sel("fileSystemRepresentation"))
        return os.fsdecode(raw) if raw else None

    def _text(self, obj) -> str | None:
        if not obj:
            return None
        raw = self._send_cstr(obj, self._sel("UTF8String"))
        return raw.decode("utf-8", "replace") if raw else None

    def _error(self, error) -> NSErr | None:
        if not error:
            return None
        domain = self._text(self._send_id(error, self._sel("domain"))) or "?"
        code = self._send_long(error, self._sel("code"))
        under_domain = under_code = None
        info = self._send_id(error, self._sel("userInfo"))
        if info:
            under = self._send_object(
                info, self._sel("objectForKey:"), self._keys["NSUnderlyingErrorKey"]
            )
            if under:
                under_domain = self._text(self._send_id(under, self._sel("domain")))
                under_code = self._send_long(under, self._sel("code"))
        return NSErr(domain, code, under_domain, under_code)

    def _resource(self, url, key_name: str):
        value, error = ctypes.c_void_p(), ctypes.c_void_p()
        ok = self._send_resource(
            url,
            self._sel("getResourceValue:forKey:error:"),
            ctypes.byref(value),
            self._keys[key_name],
            ctypes.byref(error),
        )
        return value.value if ok != 0 and value.value else None

    def _flag(self, url, key_name: str) -> bool | None:
        value = self._resource(url, key_name)
        if value is None:
            return None
        return self._send_bool(value, self._sel("boolValue")) != 0

    def _pooled(self, work):
        pool = self._objc.objc_autoreleasePoolPush()
        try:
            return work()
        except _Nil as nil:
            raise trash.TrashRefused(trash.NO_TRASH, "nil Objective-C object") from nil
        finally:
            self._objc.objc_autoreleasePoolPop(pool)

    # the bridge API (design §6.3) ----------------------------------------------

    def facts(self, path: str) -> Facts:
        def work():
            url = self._url(path)
            return Facts(
                local=self._flag(url, "NSURLVolumeIsLocalKey"),
                read_only=self._flag(url, "NSURLVolumeIsReadOnlyKey"),
                root_fs=self._flag(url, "NSURLVolumeIsRootFileSystemKey"),
                volume=self._path(self._resource(url, "NSURLVolumeURLKey")),
                ubiquitous=self._flag(url, "NSURLIsUbiquitousItemKey"),
            )

        return self._pooled(work)

    def trash_dir(self, path: str):
        def work():
            error = ctypes.c_void_p()
            folder = self._send_dir(
                self._manager(),
                self._sel("URLForDirectory:inDomain:appropriateForURL:create:error:"),
                _NS_TRASH_DIRECTORY,
                _NS_USER_DOMAIN_MASK,
                self._url(path),
                0,
                ctypes.byref(error),
            )
            if folder:
                return self._path(folder), None
            return None, self._error(error.value)

        return self._pooled(work)

    def trash(self, path: str):
        def work():
            resulting, error = ctypes.c_void_p(), ctypes.c_void_p()
            ok = self._send_trash(
                self._manager(),
                self._sel("trashItemAtURL:resultingItemURL:error:"),
                self._url(path),
                ctypes.byref(resulting),
                ctypes.byref(error),
            )
            if ok != 0:
                return True, self._path(resulting.value), None
            return False, None, self._error(error.value)

        return self._pooled(work)


def load_bridge() -> Bridge:
    """The real Foundation bridge; raises OSError/ValueError where it cannot load."""
    return Bridge()
