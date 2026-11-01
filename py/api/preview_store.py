"""Where previews live on disk (#320): the one resolver.

Every mapping between an image source (a "root"), one of its media files and
that file's preview on disk goes through this module. URL text stays in
``thumbnail_settings`` (``thumbnail_rel``: ``thumbnails/<rel>_thumb<ext>``,
the shape the serve route, ``inspection-state.mjs`` and ``admin.js`` share);
this module answers where such a preview is on disk.

The store of a source is where its previews live. With the default location
(mode ``source``, "Inside each source") it is ``<source>/thumbnails``, exactly
the folder every caller joined by hand before, so nothing moves.

Writes and the switch: every preview writer takes ``write_guard`` when it
starts (it records the location's ``generation``); the final rename runs
under ``write_lock`` and only when the generation is unchanged and no hold
applies (``thumbnail_encode.replace_unless_held``). Changing the location
bumps the generation under the same lock, so no write that began before can
land in the old store afterwards. Nothing reads or commits config while
``write_lock`` is held.

All functions are blocking: call them off the event loop.
"""

import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import thumbnail_settings

try:
    from ...utils.media_folders import PREVIEW_FOLDER
except ImportError:  # tests import the package top-level
    from utils.media_folders import PREVIEW_FOLDER

SOURCE_MODE = "source"


@dataclass(frozen=True)
class Location:
    """One snapshot of the thumbnail location (take one per request or job)."""

    mode: str = SOURCE_MODE
    path: str | None = None
    available: bool = True
    problem: str | None = None
    generation: int = 0


write_lock = threading.Lock()
_generation = 0


def current():
    """The thumbnail location now."""
    return Location(generation=_generation)


def generation():
    return _generation


def bump_generation():
    """Invalidate every write that started before (under ``write_lock``)."""
    global _generation
    with write_lock:
        _generation += 1
        return _generation


def _canonical(path):
    """Resolved and case-folded, so paths compare on every OS."""
    return Path(os.path.normcase(os.path.realpath(str(path))))


def _within(path, folder):
    return _canonical(path).is_relative_to(_canonical(folder))


# ── stores ───────────────────────────────────────────────────────────


def store_container(root, location=None):
    """The folder a store of *root* must stay inside: the source itself."""
    return Path(root)


def store_dir(root, location=None):
    """The store of *root* (``<source>/thumbnails``)."""
    return Path(root) / PREVIEW_FOLDER


def ensure_store(root, location=None):
    """``store_dir`` for a writer. Preview folders below it are made by the
    write itself."""
    return store_dir(root, location)


def given_store(root, store):
    """A caller's explicit store argument: the default spelling
    (``<root>/thumbnails``) means the resolver's store; another folder (only
    tests pass one) is used as given."""
    store = Path(store)
    if store == Path(root) / PREVIEW_FOLDER:
        return store_dir(root)
    return store


def preview_path(root, media_rel, ext=None, location=None):
    """Where the preview of *media_rel* (relative to *root*) is, or is to be
    written, in *ext* (the extension written now when None)."""
    store = store_dir(root, location)
    if store is None:
        return None
    if ext is None:
        from .thumbnail_encode import writer_settings

        ext = writer_settings().extension
    return store / (thumbnail_settings.preview_stem(media_rel) + ext)


def request_path(root, parts, location=None):
    """The disk path of the URL parts ``("thumbnails", ...)``, or None when
    *parts* is not under an exact ``thumbnails`` first part. The text is
    checked before (``is_preview_text``)."""
    parts = tuple(parts)
    if len(parts) < 2 or parts[0] != PREVIEW_FOLDER:
        return None
    store = store_dir(root, location)
    if store is None:
        return None
    return store.joinpath(*parts[1:])


def owns(root, path, location=None):
    """Whether *path* lies inside the store of *root*, resolved: the store
    stays inside its container and no link takes *path* out of the store."""
    store = store_dir(root, location)
    if store is None:
        return False
    return _within(store, store_container(root, location)) and _within(path, store)


# ── preview text ─────────────────────────────────────────────────────


def is_preview_text(filepath):
    """URL text naming a preview: ``thumbnails/<...>/<name>_thumb<ext>``.

    Text only, before any join: no backslash, drive or stream colon, and no
    empty, ``.`` or ``..`` segment; ``thumbnails`` exactly as the first part
    and nowhere else among the folders; the extension, in any case, one a
    preview may carry (``X_thumb.JPG`` of an older release included).
    """
    if not filepath or "\\" in filepath or ":" in filepath:
        return False
    parts = filepath.split("/")
    if len(parts) < 2 or parts[0] != PREVIEW_FOLDER:
        return False
    if any(part in ("", ".", "..") for part in parts):
        return False
    if PREVIEW_FOLDER in parts[1:-1]:
        return False
    stem, extension = os.path.splitext(parts[-1])
    return (
        stem.endswith(thumbnail_settings.THUMB_SUFFIX)
        and len(stem) > len(thumbnail_settings.THUMB_SUFFIX)
        and extension.lower() in thumbnail_settings.THUMBNAIL_EXTENSIONS
    )


# ── writes ───────────────────────────────────────────────────────────


class _WriteGuard:
    """``guard()`` for one preview write: true while the location has not
    changed since the write started and *hold* (if any) allows it."""

    __slots__ = ("generation", "_hold")

    def __init__(self, hold=None):
        self.generation = _generation
        self._hold = hold

    def __call__(self):
        if self.generation != _generation:
            return False
        return self._hold is None or bool(self._hold())


def write_guard(hold=None):
    """Take when a preview write starts; hand to its final rename."""
    if isinstance(hold, _WriteGuard):
        return hold
    return _WriteGuard(hold)


@contextmanager
def committed(guard=None):
    """Around a preview's final rename: holds ``write_lock`` and yields
    whether the write may land (``guard()``, re-checked under the lock)."""
    with write_lock:
        yield guard is None or bool(guard())
