"""Move one media file into the system trash, or refuse (#480).

Two rules hold for every backend:

- **Rename or fail.** The only primitive is a no-replace rename within one
  filesystem. Nothing is ever copied, unlinked or overwritten: a file that
  cannot be moved recoverably stays where it is, and ``TrashRefused`` says why.
- **Never into a root.** A trash folder inside an output root would be listed
  as images, so it is refused (``trash-in-root``).

Linux and BSD use :class:`FreedesktopBackend`, the freedesktop.org Trash
specification 1.0: the home trash (``$XDG_DATA_HOME/Trash``) for a file on the
same mount, else the file's mount's ``.Trash/$uid`` (only under a real, sticky
``.Trash``) or ``.Trash-$uid``. Names are reserved by creating the
``.trashinfo`` with ``O_EXCL`` and the file is moved with
``renameat2(RENAME_NOREPLACE)``; without that call the move is refused.
Windows and macOS have no backend yet (:class:`UnavailableBackend`): their
own PRs add one with a native proof.

``BACKEND`` is the one replaceable seam; tests install a fake.
"""

from __future__ import annotations

import ctypes
import datetime
import errno
import os
import stat
import sys
import urllib.parse

NO_TRASH = "no-trash"
NETWORK = "network"
TRASH_IN_ROOT = "trash-in-root"
PATH_TOO_LONG = "path-too-long"
LOCKED = "locked"
FAILED = "failed"
CODES = frozenset({NO_TRASH, NETWORK, TRASH_IN_ROOT, PATH_TOO_LONG, LOCKED, FAILED})

HOME_TRASH_LABEL = "home trash"
RENAME_NOREPLACE = 1
MAX_NAME_ATTEMPTS = 1000
_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
# rename() errors that mean "this trash cannot take it", never a copy.
_NO_TRASH_ERRNOS = {
    errno.ENOSYS,
    errno.EINVAL,
    errno.EXDEV,
    errno.EACCES,
    errno.EPERM,
    errno.EROFS,
    getattr(errno, "ENOTSUP", errno.EOPNOTSUPP),
    errno.EOPNOTSUPP,
}


class TrashRefused(Exception):
    """The file was not moved; ``code`` is one of ``CODES``."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code


def parse_mountinfo(text: str) -> list[str]:
    """Mount points of ``/proc/self/mountinfo`` text (octal escapes decoded)."""
    points = []
    for line in text.splitlines():
        fields = line.split(" ")
        if len(fields) > 4:
            raw = fields[4]
            points.append(
                bytes(raw, "utf-8")
                .decode("unicode_escape")
                .encode("latin-1")
                .decode("utf-8", "surrogateescape")
            )
    return points


def _proc_mounts() -> list[str]:
    try:
        with open(
            "/proc/self/mountinfo", encoding="utf-8", errors="surrogateescape"
        ) as f:
            return parse_mountinfo(f.read())
    except OSError:
        return []


def _inside(path: str, folder: str) -> bool:
    folder = folder.rstrip("/") or "/"
    return path == folder or path.startswith(folder if folder == "/" else folder + "/")


def mount_of(path: str, mounts: list[str]) -> str:
    """The longest mount point holding *path* (both real paths)."""
    best = "/"
    for point in mounts:
        if _inside(path, point) and len(point) > len(best):
            best = point
    return best


def _ismount_walk(path: str) -> str:
    """Fallback without mountinfo (BSD): walk up until ``os.path.ismount``."""
    current = path
    while not os.path.ismount(current):
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return current


def _libc_renameat2():
    if os.name != "posix":
        return None  # Windows: no libc, and no freedesktop backend
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        function = libc.renameat2
    except (OSError, AttributeError, TypeError):
        return None
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    function.restype = ctypes.c_int
    return function


_RENAMEAT2 = _libc_renameat2()


def rename_noreplace(src_dir_fd: int, src: str, dst_dir_fd: int, dst: str) -> None:
    """``renameat2(..., RENAME_NOREPLACE)``; raises OSError (ENOSYS when missing)."""
    if _RENAMEAT2 is None:
        raise OSError(errno.ENOSYS, "renameat2 is not available")
    result = _RENAMEAT2(
        src_dir_fd, os.fsencode(src), dst_dir_fd, os.fsencode(dst), RENAME_NOREPLACE
    )
    if result != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def _canonical(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def _check_roots(trash_dir: str, roots) -> None:
    trash_key = _canonical(trash_dir)
    for root in roots:
        if _inside(trash_key, _canonical(str(root))):
            raise TrashRefused(TRASH_IN_ROOT)


def _owned_folder(path: str, uid: int, *, create: bool) -> bool:
    """Whether *path* is (or, with *create*, now is) a real folder we own."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        if not create:
            return False
        try:
            os.mkdir(path, 0o700)
        except OSError:
            return False
        info = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and info.st_uid == uid


class UnavailableBackend:
    """No trash on this system: every move is refused, nothing is touched."""

    def available(self, path: str, roots) -> str | None:
        return NO_TRASH

    def move(self, path: str, roots) -> str:
        raise TrashRefused(NO_TRASH)


class FreedesktopBackend:
    """The freedesktop.org Trash specification 1.0 (Linux, BSD)."""

    def __init__(self, data_home=None, mounts=None, rename=None, uid=None, now=None):
        self._data_home = data_home
        self._mounts = mounts or _proc_mounts
        self._rename = rename or rename_noreplace
        if uid is None:
            uid = os.getuid() if hasattr(os, "getuid") else -1
        self._uid = uid
        self._now = now or datetime.datetime.now

    def _home_data(self) -> str:
        if self._data_home:
            return self._data_home
        configured = os.environ.get("XDG_DATA_HOME")
        if configured and os.path.isabs(configured):
            return configured
        return os.path.join(os.path.expanduser("~"), ".local", "share")

    def _mount(self, real: str, mounts) -> str:
        return mount_of(real, mounts) if mounts else _ismount_walk(real)

    def _plan(self, path: str, roots, *, create: bool):
        """``(trash_dir, topdir_or_None, label)`` for *path*; raises TrashRefused."""
        real = os.path.realpath(path)
        mounts = [os.path.realpath(point) for point in self._mounts()]
        home_data = os.path.realpath(self._home_data())
        if self._mount(real, mounts) == self._mount(home_data, mounts):
            trash_dir = os.path.join(home_data, "Trash")
            _check_roots(trash_dir, roots)
            if create:
                os.makedirs(trash_dir, mode=0o700, exist_ok=True)
            return trash_dir, None, HOME_TRASH_LABEL
        top = self._mount(real, mounts)
        shared = os.path.join(top, ".Trash")
        try:
            info = os.lstat(shared)
            sticky = stat.S_ISDIR(info.st_mode) and bool(info.st_mode & stat.S_ISVTX)
        except OSError:
            sticky = False
        candidates = []
        if sticky:
            candidates.append(os.path.join(shared, str(self._uid)))
        candidates.append(os.path.join(top, f".Trash-{self._uid}"))
        for trash_dir in candidates:
            _check_roots(trash_dir, roots)
            if self._usable(trash_dir, create=create):
                label = f"{os.path.relpath(trash_dir, top)} on its drive"
                return trash_dir, top, label
        raise TrashRefused(NO_TRASH)

    def _usable(self, trash_dir: str, *, create: bool) -> bool:
        if os.path.lexists(trash_dir):
            return _owned_folder(trash_dir, self._uid, create=False)
        parent = os.path.dirname(trash_dir)
        if not create:
            return os.access(parent, os.W_OK | os.X_OK)
        return _owned_folder(trash_dir, self._uid, create=True)

    def available(self, path: str, roots) -> str | None:
        """None when *path* can be trashed, else the refusal code (no writes)."""
        try:
            self._plan(path, roots, create=False)
        except TrashRefused as refused:
            return refused.code
        except OSError:
            return NO_TRASH
        return None

    def move(self, path: str, roots) -> str:
        """Move *path* into its trash and return the trash's label."""
        try:
            trash_dir, top, label = self._plan(path, roots, create=True)
        except OSError as error:
            raise TrashRefused(NO_TRASH, str(error)) from error
        real = os.path.realpath(path)
        recorded = os.path.relpath(real, top) if top else real
        opened = []
        try:
            trash_fd = self._open_dir(trash_dir, opened)
            files_fd = self._subfolder(trash_fd, "files", opened)
            info_fd = self._subfolder(trash_fd, "info", opened)
            source_fd = self._open_dir(os.path.dirname(real), opened)
            self._move_named(
                source_fd, os.path.basename(real), files_fd, info_fd, recorded
            )
        finally:
            for fd in opened:
                os.close(fd)
        return label

    def _open_dir(self, path: str, opened: list) -> int:
        try:
            fd = os.open(path, _DIR_FLAGS)
        except OSError as error:
            raise TrashRefused(NO_TRASH, str(error)) from error
        opened.append(fd)
        return fd

    def _subfolder(self, parent_fd: int, name: str, opened: list) -> int:
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        except OSError as error:
            raise TrashRefused(NO_TRASH, str(error)) from error
        try:
            info = os.lstat(name, dir_fd=parent_fd)
        except OSError as error:
            raise TrashRefused(NO_TRASH, str(error)) from error
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != self._uid:
            raise TrashRefused(NO_TRASH, f"{name} is not our folder")
        try:
            fd = os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
        except OSError as error:
            raise TrashRefused(NO_TRASH, str(error)) from error
        opened.append(fd)
        return fd

    def _info_text(self, recorded: str) -> bytes:
        encoded = urllib.parse.quote(os.fsencode(recorded), safe="/")
        stamp = self._now().strftime("%Y-%m-%dT%H:%M:%S")
        return f"[Trash Info]\nPath={encoded}\nDeletionDate={stamp}\n".encode("utf-8")

    def _move_named(self, source_fd, name, files_fd, info_fd, recorded) -> None:
        stem, extension = os.path.splitext(name)
        for attempt in range(MAX_NAME_ATTEMPTS):
            candidate = name if attempt == 0 else f"{stem}.{attempt}{extension}"
            info_name = candidate + ".trashinfo"
            try:
                fd = os.open(
                    info_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=info_fd,
                )
            except FileExistsError:
                continue
            except OSError as error:
                raise TrashRefused(NO_TRASH, str(error)) from error
            try:
                os.write(fd, self._info_text(recorded))
            finally:
                os.close(fd)
            try:
                self._rename(source_fd, name, files_fd, candidate)
                return
            except OSError as error:
                os.unlink(info_name, dir_fd=info_fd)
                if error.errno == errno.EEXIST:
                    continue  # a stray trashed file has that name: never replace it
                if error.errno in _NO_TRASH_ERRNOS:
                    raise TrashRefused(NO_TRASH, str(error)) from error
                raise TrashRefused(FAILED, str(error)) from error
        raise TrashRefused(FAILED, "no free name in the trash")


def default_backend(platform: str = sys.platform):
    """The backend for *platform*; Windows and macOS have none yet (#480)."""
    if platform in ("win32", "darwin") or platform.startswith("cygwin"):
        return UnavailableBackend()
    return FreedesktopBackend()


BACKEND = default_backend()


def trash_available(path: str, roots) -> str | None:
    """None when *path* can be trashed, else the refusal code (blocking, no writes)."""
    return BACKEND.available(path, roots)


def move_to_trash(path: str, roots) -> str:
    """Move one regular file into the trash; returns where. Raises TrashRefused."""
    return BACKEND.move(path, roots)
