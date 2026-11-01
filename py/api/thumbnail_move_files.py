"""The file side of Move thumbnails (#320 Part 3): what is copied, how a
copy is made and checked, and how old copies are removed.

No job, HTTP or config code lives here; ``thumbnail_move`` drives it.

Copy set (B5): each old store is walked with ``os.scandir``, never through a
symlink, junction or reparse point (counted as "skipped link"). A regular
file is

- copied when it has the preview grammar (``<name>_thumb<ext>``, any written
  or legacy extension) and its original still exists in the source;
- removed with the old copies, not copied, when it is an orphan (no
  original), lies under a trash folder, or is a temp sibling of a write
  (``.<stem>.part-…``) or of a move (``….pm-move-<8 hex>.tmp``);
- left in place otherwise ("Left in place"). OS metadata files (``Thumbs.db``,
  ``desktop.ini``, ``.DS_Store``, ``._*``) are removed only when they alone
  keep a preview folder from being removed.

A copy goes to ``<name>.pm-move-<8 hex>.tmp`` beside its destination, its
size is compared, its mtime is set to the source preview's rounded up to the
next 2-second step (FAT/exFAT keep it fresh, a stale preview stays stale),
then it is renamed into place. Every handle is closed before a rename or an
unlink (Windows).
"""

import errno
import logging
import os
import re
import secrets
import shutil
import stat as stat_module
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import preview_location, thumbnail_settings
from .thumbnail_encode import _TEMP_NAME

try:
    from ...utils.media_folders import in_trash_folder
except ImportError:  # tests import the package top-level
    from utils.media_folders import in_trash_folder

logger = logging.getLogger("prompt_manager.thumbnails")

MOVE_TEMP = re.compile(r"\.pm-move-[0-9a-f]{8}\.tmp$")
MOVE_TEMP_SUFFIX = ".pm-move-{token}.tmp"
# The temp name adds this much to a destination path (".pm-move-xxxxxxxx.tmp").
TEMP_EXTRA = 21
WINDOWS_FILE_LIMIT = 259
MTIME_STEP_NS = 2_000_000_000
# A file another program holds open (Windows) is retried this often.
REMOVE_RETRIES = (0.5, 1.0, 2.0)
_WINDOWS_IN_USE = {32, 33}  # ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION


def is_os_metadata(name):
    folded = name.lower()
    return folded in preview_location.OS_METADATA or folded.startswith("._")


def is_temp_name(name):
    return bool(_TEMP_NAME.match(name) or MOVE_TEMP.search(name))


def _preview_parts(name):
    """``(base, extension)`` of a preview file name, or None."""
    stem, extension = os.path.splitext(name)
    suffix = thumbnail_settings.THUMB_SUFFIX
    if not stem.endswith(suffix) or len(stem) <= len(suffix):
        return None
    if extension.lower() not in thumbnail_settings.THUMBNAIL_EXTENSIONS:
        return None
    return stem[: -len(suffix)], extension


def _has_original(root, rel_dir, base, extension):
    """Whether a media file in ``<root>/<rel_dir>`` owns this preview."""
    folder = Path(root).joinpath(*rel_dir)
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                stem, source_ext = os.path.splitext(entry.name)
                if stem != base or not source_ext:
                    continue
                if not thumbnail_settings.owns(extension, source_ext):
                    continue
                if entry.is_file(follow_symlinks=False):
                    return True
    except OSError:
        return False
    return False


@dataclass
class StoreScan:
    """One old store's files, sorted into what the move does with them."""

    store: Path
    copy: list = field(default_factory=list)  # (relative parts, size, mtime_ns)
    orphans: list = field(default_factory=list)  # relative parts
    temps: list = field(default_factory=list)
    left: list = field(default_factory=list)
    links: int = 0
    bytes: int = 0

    @property
    def removable(self):
        return [item[0] for item in self.copy] + self.orphans + self.temps


def scan_store(store, root, *, stop=None):
    """Sort the files of *store* (the previews of the source *root*).

    A store that does not exist, or is itself a link, scans empty (a link
    counts once).
    """
    store = Path(store)
    scan = StoreScan(store)
    try:
        if preview_location.is_link_like(store):
            scan.links += 1
            return scan
        if not store.is_dir():
            return scan
    except OSError:
        return scan
    _walk(scan, store, (), root, stop)
    return scan


def _walk(scan, folder, rel_dir, root, stop):
    try:
        with os.scandir(folder) as iterator:
            entries = list(iterator)
    except OSError:
        return
    for entry in entries:
        if stop is not None and stop():
            return
        relative = rel_dir + (entry.name,)
        path = Path(entry.path)
        try:
            if preview_location.is_link_like(path):
                scan.links += 1
                continue
            info = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if stat_module.S_ISDIR(info.st_mode):
            _walk(scan, path, relative, root, stop)
            continue
        if not stat_module.S_ISREG(info.st_mode):
            scan.left.append(relative)
            continue
        if is_temp_name(entry.name):
            scan.temps.append(relative)
            continue
        if is_os_metadata(entry.name):
            continue  # removed only when it alone keeps a folder (rmdir)
        parts = _preview_parts(entry.name)
        if parts is None:
            scan.left.append(relative)
            continue
        if in_trash_folder("/".join(relative)) or not _has_original(
            root, rel_dir, *parts
        ):
            scan.orphans.append(relative)
            continue
        scan.copy.append((relative, info.st_size, info.st_mtime_ns))
        scan.bytes += info.st_size


# ── copying ──────────────────────────────────────────────────────────


def rounded_mtime_ns(mtime_ns):
    """*mtime_ns* rounded up to the next 2-second step (FAT/exFAT)."""
    return -(-mtime_ns // MTIME_STEP_NS) * MTIME_STEP_NS


def too_long(path, long_paths=None):
    """A destination (with its temp name) over Windows' file limit."""
    if os.name != "nt" and long_paths is not False:
        return False
    if long_paths is None:
        long_paths = preview_location.long_paths_enabled()
    if long_paths:
        return False
    return len(str(path)) + TEMP_EXTRA > WINDOWS_FILE_LIMIT


class CopyFailed(Exception):
    """One preview could not be copied (a per-file error, D10A)."""


class FolderGone(Exception):
    """The destination cannot take writes any more (abort the move)."""


def _make_folders(base, relative_dir, created):
    """Make ``base/relative_dir`` one folder at a time (*base* must exist);
    every folder made is added to *created*."""
    current = Path(base)
    for part in relative_dir:
        current = current / part
        try:
            os.mkdir(current)
            created.append(str(current))
        except FileExistsError:
            pass  # already there: made by an earlier copy into this folder
        except OSError as error:
            if error.errno in (errno.ENOSPC, errno.EDQUOT, errno.EROFS):
                raise FolderGone(str(error)) from error
            raise CopyFailed(
                "Its folder could not be made; made again on view"
            ) from error
        if preview_location.is_link_like(current):
            raise CopyFailed("a link in the destination")


def refuse_links(base, relative_dir):
    """Raise CopyFailed when *base* or an existing folder on the way to
    ``base/relative_dir`` is a link (never copy through one)."""
    current = Path(base)
    for part in (None, *relative_dir):
        if part is not None:
            current = current / part
        if preview_location.is_link_like(current):
            raise CopyFailed("a link in the destination")


def copy_one(source, destination, size, mtime_ns, created, *, long_paths=None):
    """Copy one preview; returns ``"moved"`` or ``"skipped"`` (already
    there). Raises CopyFailed (this file) or FolderGone (the destination)."""
    destination = Path(destination)
    if too_long(destination, long_paths):
        raise CopyFailed("Path too long for Windows; the original is shown")
    wanted = rounded_mtime_ns(mtime_ns)
    try:
        existing = os.stat(destination, follow_symlinks=False)
    except FileNotFoundError:
        existing = None
    except OSError as error:
        raise FolderGone(str(error)) from error
    if (
        existing is not None
        and stat_module.S_ISREG(existing.st_mode)
        and existing.st_size == size
        and existing.st_mtime_ns == wanted
    ):
        return "skipped"
    temp = destination.with_name(
        destination.name + MOVE_TEMP_SUFFIX.format(token=secrets.token_hex(4))
    )
    try:
        shutil.copyfile(source, temp)
        if os.stat(temp).st_size != size:
            raise CopyFailed("The copy did not match; made again on view")
        os.utime(temp, ns=(wanted, wanted))
        os.replace(temp, destination)
    except CopyFailed:
        _unlink(temp)
        raise
    except FileNotFoundError as error:
        _unlink(temp)
        if not Path(source).exists():
            raise CopyFailed("Could not be read; made again on view") from error
        raise FolderGone(str(error)) from error
    except OSError as error:
        _unlink(temp)
        if error.errno in (errno.ENOSPC, errno.EDQUOT, errno.EROFS):
            raise FolderGone(str(error)) from error
        raise CopyFailed("Could not be copied; made again on view") from error
    return "moved"


def _unlink(path):
    """Remove a copy's temp file after a failed copy; True when it is gone.

    One that cannot go (a Windows lock, permissions) is logged with its path;
    the next start's temp sweep removes it (``.pm-move-*.tmp``)."""
    try:
        os.unlink(path)
        return True
    except FileNotFoundError:
        return True  # never written, or already renamed into place
    except OSError as error:
        logger.warning(f"Could not remove the move's temp file {path}: {error}")
        return False


# ── removing ─────────────────────────────────────────────────────────


def _in_use(error):
    return getattr(error, "winerror", None) in _WINDOWS_IN_USE


def remove_file(path, *, sleep=time.sleep):
    """Remove one file; a file another program holds open (Windows) is
    retried 3 times. Returns True when it is gone."""
    for wait in (0.0,) + REMOVE_RETRIES:
        if wait:
            sleep(wait)
        try:
            os.unlink(path)
            return True
        except FileNotFoundError:
            return True
        except OSError as error:
            if not _in_use(error):
                return False
    return False


def remove_empty_folders(top, *, keep_top=False):
    """``rmdir`` the empty folders below *top* (and *top*), bottom-up, never
    through a link. A folder holding only OS metadata files loses them first.
    Returns the folders that could not be removed."""
    top = Path(top)
    left = []
    if not top.is_dir() or preview_location.is_link_like(top):
        return left
    # Top-down, pruning every link, junction or reparse point, then bottom-up:
    # never into a folder outside *top* (Windows junctions included).
    folders = []
    for folder, dirs, _files in os.walk(top):
        folder = Path(folder)
        dirs[:] = [d for d in dirs if not preview_location.is_link_like(folder / d)]
        folders.append(folder)
    for folder in reversed(folders):
        if folder == top and keep_top:
            continue
        try:
            names = os.listdir(folder)
        except OSError:
            left.append(folder)
            continue
        if names and all(is_os_metadata(name) for name in names):
            for name in names:
                remove_file(folder / name)
        try:
            os.rmdir(folder)
        except OSError:
            left.append(folder)
    return left
