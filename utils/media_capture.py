"""Bounded private capture store for image annotations (#249 A3).

One explicit Tag or review action captures exactly one selected media file:
the rooted file is streamed to a private, owner-only temporary copy while its
whole-file SHA-256 is computed, and the identity of the opened descriptor is
checked against the path before and after the copy. Preview, inference and the
metadata read use only that copy, so what the user reviewed is what the model
saw and what Save re-checks. Everything is bounded: bytes per file, completed
captures, reserved bytes, concurrent capture/verify workers, idle and hard
lifetimes.

The contract is "these bytes at this location, last checked at this time".
Stat equality is never content proof: a same-size rewrite that restores
``mtime_ns`` is visible only through the change time between the two
``fstat`` reads, which Windows before Python 3.13 reports as the creation time
(no signal).
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import stat as stat_module
import tempfile
import threading
import time
import unicodedata
from dataclasses import dataclass
from typing import Callable

from PIL import Image

try:
    from .video_metadata import VIDEO_EXTENSIONS, is_video_path
except ImportError:  # imported with the repo root on sys.path
    from utils.video_metadata import VIDEO_EXTENSIONS, is_video_path

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024
DEFAULT_MAX_FILE = 256 * 2**20
DEFAULT_MAX_CAPTURES = 8
DEFAULT_MAX_BYTES = 512 * 2**20
DEFAULT_MAX_WORKERS = 2
DEFAULT_IDLE_SECONDS = 900
DEFAULT_HARD_SECONDS = 1800
# Background recording (Needs review from the scan and the monitor) may use
# every worker and capture slot but the last one, which stays free for an
# interactive Tag dialog or Apply; it waits this long for room before failing.
BACKGROUND_WAIT_SECONDS = 2.0
MAX_INFERENCE_SIDE = 4096
MAX_INFERENCE_PIXELS = 16_777_216
MAX_RELATIVE_LENGTH = 1024

VIDEO_ONLY_REASON = "Auto-tagging is available for supported still images only."
HEADER_REASON = "The image header could not be read."
DIMENSION_REASON = (
    "Images larger than 4096 px per side or 16,777,216 px are not supported."
)

# The listing's media allowlist (``py.api.inspection_service.SERVABLE_EXTENSIONS``),
# restated here so utilities never import the aiohttp API package; a test pins
# the two sets equal.
_STILL_EXTENSIONS = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif"}
)
CAPTURE_EXTENSIONS = _STILL_EXTENSIONS | frozenset(VIDEO_EXTENSIONS)

_DIRECTORY_PREFIX = "prompt-manager-captures-"
_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_BINARY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


class CaptureError(Exception):
    """Base of the capture outcomes; ``code`` is the API error code."""

    code = "capture-error"


class CaptureCapacity(CaptureError):
    """Count, byte or worker admission failed (interactive: nonblocking)."""

    code = "review-capacity"


class CaptureTooLarge(CaptureError):
    """The file exceeds the per-file byte cap on stat or on the stream."""

    code = "unsupported-media"


class CaptureChanged(CaptureError):
    """The file changed, vanished or was replaced across the copy (retryable)."""

    code = "file-changed"


class CaptureDenied(CaptureError):
    """The target is not a listed rooted media file (alias, escape, non-media)."""

    code = "source-changed"


@dataclass(frozen=True)
class FileFacts:
    """Whole-file facts of the current rooted file, without a retained copy."""

    locator: str
    sha256: str
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class Capture:
    """One completed private copy; ``path`` is the owner-only temporary file."""

    token: str
    locator: str
    root_index: int
    scope: str
    epoch: str
    sha256: str
    size: int
    mtime_ns: int
    path: str
    media_type: str
    width: int | None
    height: int | None
    inferable: bool
    inferable_reason: str | None


class _Entry:
    __slots__ = ("capture", "created_at", "last_used_at", "uses", "doomed")

    def __init__(self, capture: Capture, now: float):
        self.capture = capture
        self.created_at = now
        self.last_used_at = now
        self.uses = 0
        self.doomed = False


def _same_facts(before: os.stat_result, after: os.stat_result) -> bool:
    """The facts every stat API reports alike: size and modification time.

    This is the only comparison made across APIs (a path ``stat`` against the
    opened descriptor's ``fstat``). Windows reports the creation time from
    ``fstat`` but, on Python 3.12+, the change time from ``stat``, and may
    encode device and inode differently, so those fields are compared only
    between two reads of the same kind (``_same_identity``).
    """
    return before.st_size == after.st_size and before.st_mtime_ns == after.st_mtime_ns


def _same_identity(before: os.stat_result, after: os.stat_result) -> bool:
    """Full identity between two reads of the same kind (both ``fstat`` or both
    path stats): size, mtime, change time, and device/inode where meaningful."""
    if not _same_facts(before, after) or before.st_ctime_ns != after.st_ctime_ns:
        return False
    if before.st_ino and after.st_ino and before.st_ino != after.st_ino:
        return False
    if before.st_dev and after.st_dev and before.st_dev != after.st_dev:
        return False
    return True


def _reason(error: OSError) -> str:
    """The OS error text without the path, so messages never name server paths."""
    return error.strerror or type(error).__name__


def _unlink_quietly(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        # already gone, which is what was wanted
        pass
    except OSError as error:
        logger.warning("Capture file %s could not be removed: %s", path, error)


class CaptureStore:
    """Bounded, lock-protected registry of private media captures."""

    def __init__(
        self,
        temp_parent: os.PathLike | str | None = None,
        *,
        max_file: int = DEFAULT_MAX_FILE,
        max_captures: int = DEFAULT_MAX_CAPTURES,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_workers: int = DEFAULT_MAX_WORKERS,
        idle: float = DEFAULT_IDLE_SECONDS,
        hard: float = DEFAULT_HARD_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        for name, value in (
            ("max_file", max_file),
            ("max_captures", max_captures),
            ("max_bytes", max_bytes),
            ("max_workers", max_workers),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not (idle > 0 and hard > 0):
            raise ValueError("idle and hard must be positive")
        self.max_file = max_file
        self.max_captures = max_captures
        self.max_bytes = max_bytes
        self.max_workers = max_workers
        self.idle = idle
        self.hard = hard
        self._clock = clock
        parent = None if temp_parent is None else os.fspath(temp_parent)
        if parent is not None:
            os.makedirs(parent, exist_ok=True)
        # mkdtemp creates the directory with owner-only permissions.
        self.directory = tempfile.mkdtemp(prefix=_DIRECTORY_PREFIX, dir=parent)
        self._lock = threading.Lock()
        # Signalled whenever a worker or a capture slot frees up.
        self._room = threading.Condition(self._lock)
        self._entries: dict[str, _Entry] = {}
        self._completed_bytes = 0
        self._pending = 0
        self._pending_bytes = 0
        self._workers = 0
        self._closed = False
        self._stale: list[str] = []

    # -- public API ------------------------------------------------------------

    def capture(
        self,
        root: os.PathLike | str,
        relative: str,
        *,
        epoch: str,
        scope: str,
        root_index: int = 0,
        background: bool = False,
    ) -> Capture:
        """Copy and hash one rooted media file into a private capture.

        A ``background`` capture never takes the last worker or capture slot
        and waits up to ``BACKGROUND_WAIT_SECONDS`` for room; an interactive
        one is admitted or refused at once.
        """
        if isinstance(root_index, bool) or not isinstance(root_index, int):
            raise CaptureDenied("Invalid gallery root")
        if root_index < 0:
            raise CaptureDenied("Invalid gallery root")
        for name, value in (("epoch", epoch), ("scope", scope)):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        parts, extension = _relative_parts(relative)
        candidate, locator = _resolve(root, parts)
        before = self._pre_stat(candidate)
        self._admit(before.st_size, reserve=True, background=background)
        try:
            capture = self._copy(
                candidate, locator, extension, before, root_index, epoch, scope
            )
        finally:
            self._release_admission(before.st_size, reserve=True)
        return capture

    def verify(self, root: os.PathLike | str, relative: str) -> FileFacts:
        """Hash the current rooted file under the same bounds, keeping no copy."""
        parts, _extension = _relative_parts(relative)
        candidate, locator = _resolve(root, parts)
        before = self._pre_stat(candidate)
        self._admit(0, reserve=False)
        try:
            sha256 = self._stream(candidate, locator, before, None)
        finally:
            self._release_admission(0, reserve=False)
        return FileFacts(locator, sha256, before.st_size, before.st_mtime_ns)

    def get(self, token: str) -> Capture | None:
        """The live capture for *token*, touching its idle clock; else None.

        ``get`` does not pin the file: a reader that streams ``capture.path``
        (the preview route) must bracket the read with ``acquire`` and
        ``release_use``, or a concurrent ``sweep``/``release`` may unlink the
        file under it (and, on Windows, leak it until ``close``).
        """
        try:
            with self._lock:
                entry = self._live_locked(token)
                if entry is None:
                    return None
                entry.last_used_at = self._clock()
                return entry.capture
        finally:
            self._flush()

    def acquire(self, token: str) -> Capture | None:
        """Mark the capture in use (inference); it outlives expiry until released."""
        try:
            with self._lock:
                entry = self._live_locked(token)
                if entry is None:
                    return None
                entry.uses += 1
                entry.last_used_at = self._clock()
                return entry.capture
        finally:
            self._flush()

    def release_use(self, token: str) -> None:
        """End one ``acquire``; a doomed capture is removed with its last use."""
        with self._lock:
            entry = self._entries.get(token)
            if entry is not None:
                entry.uses = max(0, entry.uses - 1)
                entry.last_used_at = self._clock()
                if entry.doomed and entry.uses == 0:
                    self._remove_locked(entry)
        self._flush()

    def release(self, token: str) -> bool:
        """Explicit release; True when the token was live."""
        with self._lock:
            entry = self._entries.get(token)
            live = entry is not None and not entry.doomed
            if live:
                self._doom_locked(entry)
        self._flush()
        return live

    def invalidate_all(self) -> None:
        """Expire every capture now (storage epoch changed, restore, shutdown)."""
        with self._lock:
            for entry in list(self._entries.values()):
                self._doom_locked(entry)
        self._flush()

    def sweep(self) -> None:
        """Remove idle and hard-expired captures that are not in use."""
        with self._lock:
            self._sweep_locked(self._clock())
        self._flush()

    def expires_in(self, token: str) -> float | None:
        """Seconds until *token* expires without further use; None when gone."""
        try:
            with self._lock:
                entry = self._live_locked(token)
                if entry is None:
                    return None
                now = self._clock()
                remaining = self.hard - (now - entry.created_at)
                if entry.uses == 0:
                    remaining = min(remaining, self.idle - (now - entry.last_used_at))
                return max(0.0, remaining)
        finally:
            self._flush()

    def stats(self) -> dict:
        with self._lock:
            return {
                "captures": len(self._entries),
                "reserved_bytes": self._completed_bytes + self._pending_bytes,
                "workers": self._workers,
            }

    def close(self) -> None:
        """Remove every owned capture file and the owned directory. Idempotent.

        A capture still in flight finishes first: its worker removes its own
        temp file and then the directory (``_release_admission``).
        """
        with self._lock:
            self._closed = True
            for entry in list(self._entries.values()):
                self._remove_locked(entry)
            busy = self._pending > 0
        self._flush()
        if not busy:
            self._remove_directory()

    def _remove_directory(self) -> None:
        try:
            os.rmdir(self.directory)
        except FileNotFoundError:
            # already gone, which is what was wanted
            pass
        except OSError as error:
            logger.warning(
                "Capture directory %s could not be removed: %s", self.directory, error
            )

    # -- admission -------------------------------------------------------------

    def _admit(self, size: int, *, reserve: bool, background: bool = False) -> None:
        try:
            self._admit_locked(size, reserve=reserve, background=background)
        finally:
            self._flush()

    def _admit_locked(self, size: int, *, reserve: bool, background: bool) -> None:
        deadline = time.monotonic() + BACKGROUND_WAIT_SECONDS
        with self._lock:
            while True:
                if self._closed:
                    raise RuntimeError("The capture store is closed")
                self._sweep_locked(self._clock())
                try:
                    self._check_room_locked(size, reserve, background)
                    break
                except CaptureCapacity:
                    remaining = deadline - time.monotonic()
                    if not background or remaining <= 0:
                        raise
                    self._room.wait(remaining)
            if reserve:
                self._pending += 1
                self._pending_bytes += size
            self._workers += 1

    def _check_room_locked(self, size: int, reserve: bool, background: bool) -> None:
        """Raise ``CaptureCapacity`` unless one more capture or verify fits."""
        spare = 1 if background else 0
        if self._workers + spare >= self.max_workers:
            raise CaptureCapacity("Too many captures are in progress")
        if reserve:
            held = len(self._entries) + self._pending
            if held + spare >= self.max_captures:
                raise CaptureCapacity("Too many captures are held")
            reserved = self._completed_bytes + self._pending_bytes
            if reserved + size > self.max_bytes:
                raise CaptureCapacity("Capture byte budget exhausted")

    def _release_admission(self, size: int, *, reserve: bool) -> None:
        with self._lock:
            self._workers -= 1
            if reserve:
                self._pending -= 1
                self._pending_bytes -= size
            self._room.notify_all()
            last_out = self._closed and self._pending == 0 and self._workers == 0
        if last_out:
            self._remove_directory()

    def _register(self, capture: Capture) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("The capture store is closed")
            self._completed_bytes += capture.size
            self._entries[capture.token] = _Entry(capture, self._clock())

    # -- lifetime (all under the lock) -----------------------------------------

    def _expired(self, entry: _Entry, now: float) -> bool:
        if now - entry.created_at >= self.hard:
            return True
        return entry.uses == 0 and now - entry.last_used_at >= self.idle

    def _live_locked(self, token: str) -> _Entry | None:
        entry = self._entries.get(token)
        if entry is None or entry.doomed:
            return None
        if self._expired(entry, self._clock()):
            self._doom_locked(entry)
            return None
        return entry

    def _sweep_locked(self, now: float) -> None:
        for entry in list(self._entries.values()):
            if entry.doomed or self._expired(entry, now):
                self._doom_locked(entry)

    def _doom_locked(self, entry: _Entry) -> None:
        entry.doomed = True
        if entry.uses == 0:
            self._remove_locked(entry)

    def _remove_locked(self, entry: _Entry) -> None:
        """Forget the entry; its file is unlinked by ``_flush`` after the lock."""
        if self._entries.pop(entry.capture.token, None) is not None:
            self._completed_bytes -= entry.capture.size
            self._stale.append(entry.capture.path)
            self._room.notify_all()

    def _flush(self) -> None:
        """Unlink files released under the lock, without holding it (slow I/O)."""
        with self._lock:
            stale, self._stale = self._stale, []
        for path in stale:
            _unlink_quietly(path)

    # -- copy and hash -----------------------------------------------------------

    def _pre_stat(self, candidate: str) -> os.stat_result:
        try:
            facts = os.lstat(candidate)
        except FileNotFoundError:
            raise CaptureChanged("The file is no longer there") from None
        except OSError as error:
            logger.debug("Capture pre-stat failed for %s: %s", candidate, error)
            raise CaptureChanged(
                f"The file could not be read ({_reason(error)})"
            ) from None
        if stat_module.S_ISLNK(facts.st_mode):
            raise CaptureDenied("Symbolic links are not captured")
        if not stat_module.S_ISREG(facts.st_mode):
            raise CaptureDenied("Only regular files are captured")
        if facts.st_size > self.max_file:
            raise CaptureTooLarge("The file is larger than the capture limit")
        return facts

    def _copy(
        self,
        candidate: str,
        locator: str,
        extension: str,
        before: os.stat_result,
        root_index: int,
        epoch: str,
        scope: str,
    ) -> Capture:
        fd, temp_path = tempfile.mkstemp(
            prefix="capture-", suffix=extension, dir=self.directory
        )
        sink = os.fdopen(fd, "wb")
        completed = False
        try:
            sha256 = self._stream(candidate, locator, before, sink)
            sink.close()
            media_type = "video" if is_video_path(candidate) else "image"
            width, height, inferable, reason = self._probe(temp_path, media_type)
            capture = Capture(
                token=secrets.token_urlsafe(32),
                locator=locator,
                root_index=root_index,
                scope=scope,
                epoch=epoch,
                sha256=sha256,
                size=before.st_size,
                mtime_ns=before.st_mtime_ns,
                path=temp_path,
                media_type=media_type,
                width=width,
                height=height,
                inferable=inferable,
                inferable_reason=reason,
            )
            self._register(capture)
            completed = True
            return capture
        finally:
            if not sink.closed:
                sink.close()
            if not completed:
                _unlink_quietly(temp_path)

    def _read(self, stream, size: int) -> bytes:
        """One bounded read of the source; a seam for change-detection tests."""
        return stream.read(size)

    def _stream(self, candidate: str, locator: str, before, sink) -> str:
        """Hash (and copy into *sink*) the file; every handle closes here."""
        digest = hashlib.sha256()
        total = 0
        try:
            fd = os.open(candidate, _OPEN_FLAGS)
        except OSError as error:
            logger.debug("Capture open failed for %s: %s", candidate, error)
            raise CaptureChanged(
                f"The file could not be opened ({_reason(error)})"
            ) from None
        with os.fdopen(fd, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not _same_facts(before, opened):
                raise CaptureChanged("The file changed before it was read")
            while True:
                chunk = self._read(stream, CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > self.max_file:
                    raise CaptureTooLarge("The file grew past the capture limit")
                digest.update(chunk)
                if sink is not None:
                    sink.write(chunk)
            after = os.fstat(stream.fileno())
        if total != before.st_size or not _same_identity(opened, after):
            raise CaptureChanged("The file changed while it was read")
        try:
            current = os.lstat(candidate)
        except OSError:
            raise CaptureChanged("The file is no longer there") from None
        if not _same_identity(before, current):
            raise CaptureChanged("The path names another file now")
        if os.path.normcase(os.path.realpath(candidate)) != locator:
            raise CaptureChanged("The path resolves elsewhere now")
        return digest.hexdigest()

    def _probe(self, path: str, media_type: str):
        """Header-only dimensions of the capture; never decodes pixels.

        Only the image header decides whether Generate is offered: prompt
        metadata over the inspection budgets does not make a decodable image
        unreadable (the metadata itself is read elsewhere, under its budgets).
        """
        if media_type == "video":
            return None, None, False, VIDEO_ONLY_REASON
        try:
            width, height = header_dimensions(path)
        except HeaderTooLarge:
            return None, None, False, DIMENSION_REASON
        except HeaderUnreadable:
            return None, None, False, HEADER_REASON
        if (
            width > MAX_INFERENCE_SIDE
            or height > MAX_INFERENCE_SIDE
            or width * height > MAX_INFERENCE_PIXELS
        ):
            return width, height, False, DIMENSION_REASON
        return width, height, True, None


class HeaderUnreadable(Exception):
    """The file has no image header Pillow can read."""


class HeaderTooLarge(HeaderUnreadable):
    """The header declares a decompression bomb (no pixel is decoded)."""


def header_dimensions(path: str) -> tuple[int, int]:
    """``(width, height)`` from the image header alone; the file is closed.

    ``Image.open`` reads only the header. Pillow's decompression-bomb error
    (twice its warning size) is ``HeaderTooLarge``; a size between the two
    only warns, and the caller's inference pixel cap, far below Pillow's
    warning size, refuses it. The process-global warnings filters are never
    touched: this runs on capture workers inside the host process, and
    ``warnings.catch_warnings`` is not thread-safe.
    """
    try:
        with Image.open(path) as image:
            width, height = image.size
    except Image.DecompressionBombError as exc:
        raise HeaderTooLarge(str(exc)) from None
    except Exception as exc:  # any decoder failure means "no usable header"
        raise HeaderUnreadable(str(exc)) from None
    if not width or not height:
        raise HeaderUnreadable("The header declares no size")
    return int(width), int(height)


def _relative_parts(relative) -> tuple[list[str], str]:
    """Split a safe relative POSIX media path; anything else is denied."""
    if not isinstance(relative, str) or not relative:
        raise CaptureDenied("Invalid file path")
    if len(relative) > MAX_RELATIVE_LENGTH or "\x00" in relative:
        raise CaptureDenied("Invalid file path")
    if "\\" in relative or relative.startswith("/"):
        raise CaptureDenied("Invalid file path")
    parts = relative.split("/")
    for part in parts:
        if part in ("", ".", "..") or ":" in part:
            raise CaptureDenied("Invalid file path")
    extension = os.path.splitext(parts[-1])[1].lower()
    if extension not in CAPTURE_EXTENSIONS:
        raise CaptureDenied("Unsupported media type")
    return parts, extension


def _resolve(root, parts: list[str]) -> tuple[str, str]:
    """The candidate path and its locator; aliases and escapes are denied."""
    real_root = os.path.realpath(os.fspath(root))
    if not os.path.isdir(real_root):
        raise CaptureDenied("Unknown gallery root")
    candidate = os.path.join(real_root, *parts)
    locator = os.path.normcase(candidate)
    if os.path.normcase(os.path.realpath(candidate)) != locator:
        raise CaptureDenied("The file is not a listed rooted file")
    _deny_spelling_aliases(real_root, parts)
    return candidate, locator


def _spelling_key(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def _deny_spelling_aliases(real_root: str, parts: list[str]) -> None:
    """Deny a case or Unicode spelling of an entry the directory spells otherwise.

    On a case- or normalization-insensitive filesystem (macOS APFS, Windows)
    ``A.PNG`` or an NFD name opens the listed ``a.png`` or NFC name, while
    ``realpath`` keeps the requested spelling (macOS), so the tags would be
    stored under a second locator. Each part must be spelled as its directory
    lists it. A part with no entry of any spelling is left to the open that
    follows (a missing file keeps its usual outcome).
    """
    directory = real_root
    for part in parts:
        try:
            names = os.listdir(directory)
        except OSError:
            return
        if part not in names:
            key = _spelling_key(part)
            if any(_spelling_key(name) == key for name in names):
                raise CaptureDenied("The file is not a listed rooted file")
            return
        directory = os.path.join(directory, part)
