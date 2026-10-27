"""A per-process cache of LoraManager's layout for the image hot paths (#362).

Every Library list, prompt detail, inspect, reveal and serve request needs the
LoRA allow-list (``ImagesMixin._lora_image_dirs``). Building it means finding
the LoraManager install, reading its plugin settings file and its settings
file, and resolving every folder. ``LayoutCache`` keeps the last result and
reuses it while all of these hold:

- the key (the configured install path, stripped) and the home folder the
  folder guard compares against are the same;
- the entry is younger than ``MAX_AGE_SECONDS`` (monotonic clock);
- the plugin settings file and the settings file have the same signature
  (``st_mtime_ns``, ``st_size``, or ``MISSING``) as when it was stored;
- the install and every stored media root still resolve to themselves (a
  folder swapped for a symlink reloads, so the folder guard runs again).

A result is stored only when no watched file is "racy" (modified within
``RACY_SECONDS`` of the load, git's rule for racily clean index entries),
every watched file could be checked, the settings file was readable, and no
``clear()`` ran during the load. What each rule covers: signatures catch a
settings change on the next request; the racy rule catches a write during the
load, a half-written file and coarse timestamps; the maximum age bounds the
inputs no signature sees (``extra_model_paths.yaml``, environment, temporary
failures); ``clear()`` makes a finished import's new folders servable at once.

Hits and loads take no lock: a slow ``os.stat`` on a network share holds up
only its own request, and two threads that miss together both load (both
loads only read). A short lock guards the compare-and-store and the logging
state; no file I/O runs under it. Standard library only, so it is tested
without ComfyUI or LoraManager.
"""

import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple

from .lora_manager_layout import UNREADABLE, LoraManagerLayout, _canonical

# A watched file modified this recently is not trusted: LoraManager writes its
# settings in place, and FAT/exFAT store modification times in 2 s steps.
RACY_SECONDS = 3
# Bounds what no signature sees (unwatched inputs, temporary failures).
MAX_AGE_SECONDS = 30

# The signature of a watched file that does not exist (a valid state).
MISSING = object()
# ERROR_BAD_NETPATH and ERROR_BAD_NET_NAME, which Windows raises as
# FileNotFoundError.
_OFFLINE_SHARE_ERRORS = frozenset({53, 67})

logger = logging.getLogger("prompt_manager.lora_layout_cache")


@dataclass(frozen=True)
class _Entry:
    key: str
    home: str
    install: Optional[str]
    layout: LoraManagerLayout
    signatures: Tuple[Tuple[str, object], ...]
    stored_at: float


class _CannotValidate(Exception):
    """A watched file could not be checked (not merely missing)."""


class LayoutCache:
    """At most one ``(install, layout)`` for the active LoraManager config."""

    def __init__(
        self,
        resolve: Callable[[str], Optional[str]],
        load: Callable[[Optional[str]], LoraManagerLayout],
        *,
        wall_ns: Callable[[], int] = time.time_ns,
        monotonic: Callable[[], float] = time.monotonic,
        stat=os.stat,
        pathmod=os.path,
        home: Callable[[], str] = lambda: str(Path.home()),
    ):
        self._resolve = resolve
        self._load = load
        self._wall_ns = wall_ns
        self._monotonic = monotonic
        self._stat = stat
        self._pathmod = pathmod
        self._home = home
        self._entry: Optional[_Entry] = None
        self._generation = 0
        self._lock = threading.Lock()
        # What was last logged; survives clear() so a reload of the same
        # layout stays quiet.
        self._logged_layout: Optional[LoraManagerLayout] = None
        self._logged_refused: Tuple[str, ...] = ()

    def get(self, configured: str) -> Tuple[Optional[str], LoraManagerLayout]:
        """``(install, layout)`` for the configured path ("" auto-detects)."""
        key = (configured or "").strip()
        home = self._home()
        entry = self._entry
        if entry is not None and self._valid(entry, key, home):
            return entry.install, entry.layout
        with self._lock:
            if entry is not None and self._entry is entry:
                self._entry = None
            generation = self._generation
        install = self._resolve(key)
        layout = self._load(install)
        self._log_refused(layout)
        try:
            signatures = tuple(
                (path, self._signature(path)) for path in self._watched(install, layout)
            )
        except _CannotValidate:
            return install, layout
        if layout.error == UNREADABLE or self._racy(signatures, self._wall_ns()):
            return install, layout
        stored = _Entry(key, home, install, layout, signatures, self._monotonic())
        changed = False
        with self._lock:
            if self._generation == generation:
                self._entry = stored
                changed = layout != self._logged_layout
                if changed:
                    self._logged_layout = layout
        if changed:
            logger.info(
                f"LoraManager layout changed: {layout.source or 'no'} settings, "
                f"{layout.mode} storage, {len(layout.media_roots)} media folders"
            )
        return install, layout

    def clear(self) -> None:
        """Drop the entry; a load that started before this stores nothing."""
        with self._lock:
            self._entry = None
            self._generation += 1

    def _log_refused(self, layout: LoraManagerLayout) -> None:
        """One warning per default LoRA folder newly refused by the guard."""
        refused = tuple(layout.refused_defaults)
        with self._lock:
            if refused == self._logged_refused:
                return
            new = [folder for folder in refused if folder not in self._logged_refused]
            self._logged_refused = refused
        for folder in new:
            logger.warning(
                f"ComfyUI LoRA folder '{folder}' is neither scanned nor served: "
                "it is a filesystem root, the home folder or a folder that "
                "contains it"
            )

    # ── validation ──────────────────────────────────────────────────

    def _valid(self, entry: _Entry, key: str, home: str) -> bool:
        if entry.key != key or entry.home != home:
            return False
        if self._monotonic() - entry.stored_at >= MAX_AGE_SECONDS:
            return False
        try:
            for path, signature in entry.signatures:
                if self._signature(path) != signature:
                    return False
        except _CannotValidate:
            return False
        folders = entry.layout.media_roots
        if entry.install is not None:
            folders = (entry.install,) + tuple(folders)
        for folder in folders:
            try:
                if _canonical(folder, self._pathmod) != folder:
                    return False
            except (OSError, ValueError):
                return False
        return True

    def _watched(self, install, layout) -> Tuple[str, ...]:
        """The plugin settings file, then the settings file, each once."""
        if install is None:
            return ()
        paths = [self._pathmod.join(install, "settings.json")]
        if layout.settings_path is not None:
            paths.append(str(layout.settings_path))
        watched, seen = [], set()
        for path in paths:
            identity = self._pathmod.normcase(self._pathmod.abspath(path))
            if identity not in seen:
                seen.add(identity)
                watched.append(path)
        return tuple(watched)

    def _signature(self, path: str):
        try:
            result = self._stat(path)
        except (FileNotFoundError, NotADirectoryError) as error:
            # Windows reports an offline share as "not found": that is a
            # temporary failure, not a missing file.
            if getattr(error, "winerror", None) in _OFFLINE_SHARE_ERRORS:
                raise _CannotValidate(path) from error
            return MISSING
        except (OSError, ValueError) as error:
            raise _CannotValidate(path) from error
        return (result.st_mtime_ns, result.st_size)

    @staticmethod
    def _racy(signatures, now_ns: int) -> bool:
        threshold = now_ns - RACY_SECONDS * 10**9
        return any(
            signature is not MISSING and signature[0] > threshold
            for _path, signature in signatures
        )
