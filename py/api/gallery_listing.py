"""One shared listing per output root, and the pure helpers behind it (#348).

The API keeps one ``RootListing`` per output root (``_root_listings`` in
``py/api/__init__.py``). The record is read and written only on the event
loop; the walk itself, the comparison with the previous listing and both
folder sets run in the executor through ``walk_root``. Routes live in
``py/api/images.py`` (``_listing``); this module has no aiohttp import so
tests can exercise it without a server.

A settled output the image monitor reports marks its root dirty
(``mark_root_dirty``); a dirty root gets one background walk, no sooner than
``rewalk_interval`` after its previous walk started (PR 2).
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
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
# least FLOOR and at most CAP seconds. Background walks then hold the GIL at
# most ~20% of the time; the cap keeps a new output listed within ~20 s on a
# slow machine. Read through this module so tests can patch them.
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
    # Monotonic start and duration of the last walk (REWALK_MIN_INTERVAL).
    last_started_at: float | None = None
    last_duration: float | None = None
    # A settled output was reported under this root; cleared when a walk starts.
    dirty: bool = False

    def drop(self) -> None:
        self.files = None
        self.folders = None
        self.folders_with_ancestors = None
        self.walked_at = None
        self.generation += 1


def folder_sets(root, files) -> tuple[frozenset, frozenset]:
    """``(folders, folders_with_ancestors)`` of the files listed under *root*.

    Folders keep the spelling ``/gallery/subfolders`` has always answered:
    ``str(relative.parent)`` (native separators) for the folders holding
    files, plus every ancestor joined by ``/``.
    """
    # From the path strings: relative_to per file was half of a large walk.
    # The separator is the path flavour's own (tests use PureWindowsPath).
    sep = str(type(root)("a", "b"))[1]
    prefix = str(root)
    if not prefix.endswith(sep):
        prefix += sep
    folders = set()
    for path, _mtime in files:
        text = str(path)
        if text.startswith(prefix):
            rel_dir = text[len(prefix) :].rpartition(sep)[0]
        else:
            rel_dir = str(path.relative_to(root).parent)
        if rel_dir and rel_dir != ".":
            folders.add(rel_dir)
    with_ancestors = set(folders)
    for folder in folders:
        parts = folder.replace("\\", "/").split("/")
        for i in range(1, len(parts)):
            with_ancestors.add("/".join(parts[:i]))
    return frozenset(folders), frozenset(with_ancestors)


def rewalk_interval(last_duration) -> float:
    """REWALK_MIN_INTERVAL after a walk that took *last_duration* seconds."""
    scaled = REWALK_FACTOR * (last_duration or 0.0)
    return min(REWALK_CAP, max(REWALK_FLOOR, scaled))


def root_contains(resolved_root: str, path, pathmod=os.path) -> bool:
    """True when the resolved *path* lies under *resolved_root* (no file I/O).

    *resolved_root* is already ``normcase``d; *path* is folded the same way,
    so Windows matches case-insensitively and with either separator. The
    match ends at a separator: ``/out2/x.png`` is not under ``/out``.
    """
    target = pathmod.normcase(str(path))
    prefix = resolved_root
    if not prefix.endswith(pathmod.sep):
        prefix += pathmod.sep
    return target.startswith(prefix)


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
