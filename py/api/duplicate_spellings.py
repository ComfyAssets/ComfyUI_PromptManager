"""Every path a duplicate copy is recorded under (#249 System S6, Task 17).

The search walks each source's real path, but a record names a file the way
it was seen when it was written: under the folder the image monitor watches,
the configured spelling, ComfyUI's output folder, a symlink to it, or a
parent of a source listed later. ``Spellings`` finds them by containment:
for each known folder spelling ``S`` (resolving to the folder ``R``) and a
file ``P`` inside ``R``, the file is also recorded as ``S + relpath(P, R)``.

Folders are compared by ``os.stat`` identity (``st_dev``, ``st_ino``), not
by text, so a case variant on a case-insensitive filesystem (macOS, Windows)
or a symlinked spelling matches. A volume that reports no file id falls
back to ``normcase(realpath)``. Favorites are keyed by a listed root and the
path below it, so ``favorites`` lists every listed root that holds a file.
Blocking: each lookup stats the file's folders once (cached).
"""

import os
from pathlib import Path


def _identity(path):
    try:
        info = os.stat(path)
    except OSError:
        return None
    if info.st_ino:
        return ("inode", info.st_dev, info.st_ino)
    return ("path", os.path.normcase(os.path.realpath(str(path))))


class Spellings:
    """Folder spellings and listed roots, matched to files by containment."""

    def __init__(self, folders, listed_roots):
        self._cache = {}
        self._folders = []
        for spelled in dict.fromkeys(str(f) for f in folders if f):
            identity = self._identity(spelled)
            if identity is not None:
                self._folders.append((spelled, identity))
        self._listed = []
        for root in dict.fromkeys(str(r) for r in listed_roots if r):
            identity = self._identity(root)
            if identity is not None:
                self._listed.append((root, identity))

    def _identity(self, path):
        if path not in self._cache:
            self._cache[path] = _identity(path)
        return self._cache[path]

    def _ancestors(self, path):
        """``{identity: folder}`` of every folder above ``path``, nearest first."""
        found = {}
        folder = os.path.dirname(os.path.abspath(str(path)))
        while True:
            identity = self._identity(folder)
            if identity is not None:
                found.setdefault(identity, folder)
            parent = os.path.dirname(folder)
            if parent == folder:
                return found
            folder = parent

    def of(self, path):
        """``{folder spelling: path spelled under it}``; ``""`` is ``path`` itself."""
        path = os.path.abspath(str(path))
        spelled = {"": path}
        ancestors = self._ancestors(path)
        for folder, identity in self._folders:
            inside = ancestors.get(identity)
            if inside is not None:
                spelled.setdefault(
                    folder, os.path.join(folder, os.path.relpath(path, inside))
                )
        return spelled

    def favorites(self, path):
        """``{listed root: path below it (POSIX)}`` for each listed root holding it."""
        path = os.path.abspath(str(path))
        ancestors = self._ancestors(path)
        held = {}
        for root, identity in self._listed:
            inside = ancestors.get(identity)
            if inside is not None:
                held[root] = Path(os.path.relpath(path, inside)).as_posix()
        return held
