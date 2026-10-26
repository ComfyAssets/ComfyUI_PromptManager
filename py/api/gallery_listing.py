"""One shared listing per output root, and the pure helpers behind it (#348).

The API keeps one ``RootListing`` per output root (``_root_listings`` in
``py/api/__init__.py``). The record is read and written only on the event
loop; the walk itself, the comparison with the previous listing and both
folder sets run in the executor through ``walk_root``. Routes live in
``py/api/images.py`` (``_listing``); this module has no aiohttp import so
tests can exercise it without a server.
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
    folders = set()
    for path, _mtime in files:
        rel_dir = str(path.relative_to(root).parent)
        if rel_dir and rel_dir != ".":
            folders.add(rel_dir)
    with_ancestors = set(folders)
    for folder in folders:
        parts = folder.replace("\\", "/").split("/")
        for i in range(1, len(parts)):
            with_ancestors.add("/".join(parts[:i]))
    return frozenset(folders), frozenset(with_ancestors)


def same_listing(old, new) -> bool:
    """True when two listings hold the same ``(path, mtime)`` pairs in order."""
    if old is None or new is None:
        return False
    return old is new or old == new


def walk_root(
    scan_fn: Callable[[PurePath], list], root, previous_files
) -> tuple[list, frozenset, frozenset]:
    """The single executor job of a walk: ``(files, folders, with_ancestors)``.

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
    return files, folders, with_ancestors
