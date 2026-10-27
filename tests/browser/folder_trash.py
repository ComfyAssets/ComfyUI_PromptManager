"""Empty a browser-test media folder without waiting for the disk (#384).

A reset used to ``shutil.rmtree`` the output and input folders the last
fixture wrote. Unlinking a file whose pages are still being written back
waits for that write, so on a slow CI disk a reset stalled in ``rmtree``.
``replace_folder`` renames the old folder into ``<parent>/reset-trash`` (one
metadata change) and makes a new empty one; a background thread deletes the
trash, and ``close_trash`` stops that thread and removes what is left before
the server's temp root goes.
"""

from __future__ import annotations

import queue
import shutil
import threading
import uuid
from pathlib import Path

TRASH_NAME = "reset-trash"


class _Trash:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._queue: "queue.SimpleQueue[Path | None]" = queue.SimpleQueue()
        self._thread = threading.Thread(
            target=self._work, name="pm-reset-trash", daemon=True
        )
        self._thread.start()

    def _work(self) -> None:
        while (folder := self._queue.get()) is not None:
            shutil.rmtree(folder, ignore_errors=True)

    def discard(self, folder: Path) -> None:
        self.directory.mkdir(exist_ok=True)
        moved = self.directory / f"{folder.name}-{uuid.uuid4().hex}"
        folder.rename(moved)
        self._queue.put(moved)

    def close(self) -> None:
        self._queue.put(None)
        self._thread.join()
        shutil.rmtree(self.directory, ignore_errors=True)


_trashes: dict[Path, _Trash] = {}
# Trash folders whose server has cleaned up: a reset still running then
# deletes in place instead of starting a thread nobody would stop.
_closed: set[Path] = set()
_lock = threading.Lock()


def replace_folder(folder: Path) -> None:
    """Make ``folder`` an empty folder; its old content is deleted later."""
    folder = Path(folder)
    if folder.exists():
        directory = folder.parent / TRASH_NAME
        with _lock:
            trash = _trashes.get(directory)
            if trash is None and directory not in _closed:
                trash = _trashes[directory] = _Trash(directory)
            if trash is not None:
                trash.discard(folder)
        if folder.exists():
            shutil.rmtree(folder)
    folder.mkdir()


def close_trash(parent: Path) -> None:
    """Stop deleting under ``parent``'s trash and remove it (blocking)."""
    directory = Path(parent) / TRASH_NAME
    with _lock:
        _closed.add(directory)
        trash = _trashes.pop(directory, None)
    if trash is not None:
        trash.close()
