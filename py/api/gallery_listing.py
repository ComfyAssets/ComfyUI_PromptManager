"""One shared listing per output root, and the pure helpers behind it (#348).

The API keeps one ``RootListing`` per output root (``_root_listings`` in
``py/api/__init__.py``). The record is read and written only on the event
loop; the walk itself, the comparison with the previous listing and both
folder sets run in the executor through ``walk_root``. Routes live in
``py/api/images.py`` (``_listing``); this module has no aiohttp import so
tests can exercise it without a server.

A settled output the image monitor reports is added to its root's stored
listing without a walk (``output_settled``, ``insert_entry``), in the place
a walk would list it. When that cannot be exact, the root is marked dirty
(``mark_root_dirty``) instead; a dirty root gets one background walk, no
sooner than ``rewalk_interval`` after its previous walk started (PR 2).
"""

from __future__ import annotations

import asyncio
import os
from bisect import bisect_left
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Callable, NamedTuple

# A listing older than this (seconds) is served as it is, and one background
# walk replaces it. Read as ``gallery_listing.REFRESH_AFTER`` so tests can
# patch it.
REFRESH_AFTER = 30

# The stale read is served at once; its refresh walk starts this many seconds
# later, so the walk does not share the GIL with the page's own requests
# (#348 live test: reads during the walk took 2.2 s instead of 0.07 s).
REFRESH_DELAY = 1.0

# No background walk of a root starts sooner than rewalk_interval(last walk's
# duration) after its previous walk started: FACTOR times that duration, at
# least FLOOR and at most CAP seconds. Walks up to CAP / FACTOR (3 s) then
# hold the GIL at most ~20% of the time; the cap keeps a new output listed
# within ~20 s, at the cost of a higher share (back to back from 15 s) on
# slower walks. Read through this module so tests can patch them.
REWALK_FLOOR = 5
REWALK_FACTOR = 5
REWALK_CAP = 15

# A deferred walk is never armed sooner than this: no zero-delay loop.
MIN_REWALK_DELAY = 0.5

# A reader with no listing gives up (500) after this many walks in a row were
# discarded by invalidations made while they ran.
MAX_DISCARDED_WALKS = 3

WALK_STOPPED = "The listing walk was stopped"

STORED = "stored"
DISCARDED = "discarded"
FAILED = "failed"


class WalkOutcome(NamedTuple):
    """What one walk ended with; a walk's flight future resolves to this."""

    kind: str
    error: BaseException | None = None

    @classmethod
    def stored(cls) -> "WalkOutcome":
        return cls(STORED)

    @classmethod
    def discarded(cls) -> "WalkOutcome":
        return cls(DISCARDED)

    @classmethod
    def failed(cls, error: BaseException) -> "WalkOutcome":
        return cls(FAILED, error)


@dataclass
class RootListing:
    """The shared listing of one output root.

    A record is never deleted: ``drop()`` clears the listing and bumps
    ``generation``, so a walk that started before a drop can never be stored
    after it.
    """

    # (path, mtime) newest first, as _scan_gallery_files_sync returns it.
    files: list | None = None
    folders: frozenset | None = None
    folders_with_ancestors: frozenset | None = None
    # Monotonic time the last walk finished, stored or failed.
    walked_at: float | None = None
    # Number of the last walk started; a running walk is always that one.
    walk_seq: int = 0
    # Number of the walk whose files are stored (Rescan waits on it).
    stored_seq: int = 0
    generation: int = 0
    # The running walk's future (resolves to a WalkOutcome), or None.
    flight: asyncio.Future | None = None
    # True while walks keep failing: one warning per streak.
    failing: bool = False
    # The one deferred background walk not started yet (loop.call_later), or
    # None: a stale refresh or a dirty root's rewalk, whichever is due first.
    rewalk_handle: asyncio.TimerHandle | None = None
    # normcase(realpath(root)), computed by the walk in the executor: monitor
    # paths are matched against it without file I/O on the loop.
    resolved_root: str | None = None
    # Monotonic start and duration of the last walk (rewalk_interval).
    last_started_at: float | None = None
    last_duration: float | None = None
    # A settled output was reported under this root; cleared when a walk starts.
    dirty: bool = False
    # Dropped because the output roots changed: no background walk until a
    # reader (or the warm-up) asks for this root again. The monitor keeps
    # watching a removed root until ComfyUI restarts.
    retired: bool = False
    # Relative paths of settled outputs waiting to be added to ``files``
    # without a walk, and the one task adding them (``output_settled``).
    pending_adds: list = field(default_factory=list)
    add_task: asyncio.Task | None = None
    # Settled outputs added to the stored listing since the last walk stored.
    patched: int = 0

    def drop(self) -> None:
        self.files = None
        self.folders = None
        self.folders_with_ancestors = None
        self.walked_at = None
        self.pending_adds = []
        self.patched = 0
        self.generation += 1


def folder_sets(root, files) -> tuple[frozenset, frozenset]:
    """``(folders, folders_with_ancestors)`` of the files listed under *root*.

    Folders keep the spelling ``/gallery/subfolders`` has always answered:
    ``str(relative.parent)`` (native separators) for the folders holding
    files, plus every ancestor joined by ``/``.
    """
    # From the path strings: relative_to per file was half of a large walk.
    sep, prefix = _root_prefix(root)
    folders = set()
    for path, _mtime in files:
        rel_dir = _folder_of(root, path, sep, prefix)
        if rel_dir:
            folders.add(rel_dir)
    with_ancestors = set(folders)
    for folder in folders:
        with_ancestors.update(_ancestors(folder))
    return frozenset(folders), frozenset(with_ancestors)


def add_folder(root, path, folders, with_ancestors) -> tuple[frozenset, frozenset]:
    """Both folder sets once *path* is listed too (the same sets when it adds none)."""
    sep, prefix = _root_prefix(root)
    rel_dir = _folder_of(root, path, sep, prefix)
    if not rel_dir or rel_dir in folders:
        return folders, with_ancestors
    return folders | {rel_dir}, with_ancestors | {rel_dir, *_ancestors(rel_dir)}


def _root_prefix(root) -> tuple[str, str]:
    # The separator is the path flavour's own (tests use PureWindowsPath).
    sep = str(type(root)("a", "b"))[1]
    prefix = str(root)
    if not prefix.endswith(sep):
        prefix += sep
    return sep, prefix


def _folder_of(root, path, sep, prefix) -> str:
    """The folder ``/gallery/subfolders`` names for *path*; "" for the root."""
    text = str(path)
    if text.startswith(prefix):
        rel_dir = text[len(prefix) :].rpartition(sep)[0]
    else:
        rel_dir = str(path.relative_to(root).parent)
    return "" if rel_dir == "." else rel_dir


def _ancestors(folder: str) -> list[str]:
    parts = folder.replace("\\", "/").split("/")
    return ["/".join(parts[:i]) for i in range(1, len(parts))]


def relative_part(resolved_root: str, path, pathmod=os.path) -> str | None:
    """The part of the resolved *path* below *resolved_root*, as a walk spells it.

    Kept in *path*'s own case (``realpath`` gives a file's on-disk case on
    Windows) with the native separator. None when *path* is not below the
    root, or when the case folding changed the length so the slice would
    not be exact.
    """
    sep = pathmod.sep
    text = str(path)
    if sep == "\\":
        text = _plain(text.replace("/", sep), pathmod)
    folded = pathmod.normcase(text)
    prefix = _plain(resolved_root, pathmod)
    if not prefix.endswith(sep):
        prefix += sep
    if len(folded) != len(text) or not folded.startswith(prefix):
        return None
    relative = text[len(prefix) :]
    if any(part in ("", ".", "..") for part in relative.split(sep)):
        return None
    return relative


def insert_entry(files: list, entry: tuple, max_files: int) -> list | None:
    """A new listing with *entry* ``(path, mtime)`` where a walk would list it.

    *files* is newest first; an entry for the same path is replaced. Returns
    *files* itself when it already holds *entry*, and None when the place is
    not exact: another file has the same mtime (a walk orders those by its
    own visit order), or the listing is at the walk's *max_files* cap.
    Blocking (one pass over *files*): run it in the executor.
    """
    path, mtime = entry
    text = str(path)
    kept = files
    for index, (listed, listed_mtime) in enumerate(files):
        if str(listed) == text:
            if listed_mtime == mtime:
                return files
            kept = files[:index] + files[index + 1 :]
            break
    else:
        if len(files) >= max_files:
            return None
    index = bisect_left(kept, -mtime, key=lambda item: -item[1])
    if index < len(kept) and kept[index][1] == mtime:
        return None
    return [*kept[:index], entry, *kept[index:]]


# A listing that differs from the cached one by more entries than this is
# rebuilt in full by the workspace caches (listing_delta).
MAX_DELTA = 256


def listing_delta(old: list, new: list, limit: int = MAX_DELTA):
    """``(added, removed)`` entries from *old* to *new*, or None past *limit*.

    A listing patched by ``insert_entry`` shares every unchanged entry object
    with the one it came from, so entries are told apart by identity: a walk
    builds new tuples, shares none and is rebuilt in full. *old* must still
    be alive (the
    caller's cache holds it), so no identity was reused.
    """
    if abs(len(new) - len(old)) > limit:
        return None
    old_ids = set(map(id, old))
    added = []
    for entry in new:
        if id(entry) not in old_ids:
            added.append(entry)
            if len(added) > limit:
                return None
    if new and len(added) == len(new):
        return None  # nothing shared: a walk
    if len(old) + len(added) == len(new):
        return added, []
    new_ids = set(map(id, new))
    return added, [entry for entry in old if id(entry) not in new_ids]


def rewalk_interval(last_duration) -> float:
    """Seconds from a walk's start to the next background walk of its root."""
    scaled = REWALK_FACTOR * (last_duration or 0.0)
    return min(REWALK_CAP, max(REWALK_FLOOR, scaled))


def root_contains(resolved_root: str, path, pathmod=os.path) -> bool:
    """True when the resolved *path* lies under *resolved_root* (no file I/O).

    *resolved_root* is already ``normcase``d; *path* is folded the same way,
    so Windows matches case-insensitively and with either separator. The
    match ends at a separator: ``/out2/x.png`` is not under ``/out``.
    """
    target = _plain(pathmod.normcase(str(path)), pathmod)
    prefix = _plain(resolved_root, pathmod)
    if not prefix.endswith(pathmod.sep):
        prefix += pathmod.sep
    return target.startswith(prefix)


def _plain(path: str, pathmod) -> str:
    """*path* (normcased or not) without a Windows extended-length prefix.

    ``realpath`` may keep ``\\\\?\\`` on a file past MAX_PATH while its shorter
    root resolves without it.
    """
    if pathmod.sep != "\\":
        return path
    if path[:8].lower() == "\\\\?\\unc\\":
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def resolve_root(root) -> str:
    """``normcase(realpath(root))``, the spelling monitor paths are matched in.

    File I/O (realpath follows links): call it in the executor.
    """
    return os.path.normcase(os.path.realpath(str(root)))


def same_listing(old, new) -> bool:
    """True when two listings hold the same ``(path, mtime)`` pairs in order."""
    if old is None or new is None:
        return False
    return old is new or old == new


def walk_root(
    scan_fn: Callable[[PurePath], list], root, previous_files
) -> tuple[list, frozenset, frozenset, str]:
    """The single executor job of a walk.

    Returns ``(files, folders, with_ancestors, resolved_root)``.

    Opens the root first, so a missing or unreadable root raises ``OSError``
    instead of listing nothing. Returns *previous_files* itself when nothing
    changed, which keeps the identity-keyed caches built on it warm.
    """
    with os.scandir(root):
        pass
    files = scan_fn(root)
    if same_listing(previous_files, files):
        files = previous_files
    folders, with_ancestors = folder_sets(root, files)
    return files, folders, with_ancestors, resolve_root(root)
