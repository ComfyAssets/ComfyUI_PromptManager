"""Automatic thumbnails (#249): previews are a cache the server keeps itself.

The approved mock has no repeated global thumbnail prompt
(``docs/concepts/workspace/README.md``), so the workspace never asks. Every
listing gives an image or video a thumbnail URL (``media_thumbnail``), and the
previews behind those URLs are made here, in three ways:

- **On first view.** ``GET /prompt_manager/images/serve/thumbnails/<rel>_thumb
  <ext>`` for a preview that does not exist yet is admitted on the event loop
  (``_PreviewGate``): at most ``ON_DEMAND_LIMIT`` previews are written at once,
  on this API's interactive image pool (``IMAGE_POOL_SIZE`` threads, shared
  with card facts and inspector reads; never the default executor), one flight runs
  per file, and the requests waiting are capped (``MAX_WAITING``); past the
  cap a request gets its fallback at once. Inside the admission the preview is
  mapped back to its media file under the same root (the request text is
  checked first: no traversal, drive, UNC or empty segment; then a regular
  file inside the root, never through a link), made and served. When it
  cannot be made an image's original is served and a video's placeholder
  frame, both ``Cache-Control: no-store``: a video is never sent to an
  ``<img>``. A file that cannot be decoded is remembered for this session
  (keyed by its path, size and mtime) and counted for the System Overview
  (``failed`` in ``/images/thumbnails/status``); I/O errors and a preview
  another program holds open (Windows) are not remembered. The recorded
  history's thumbnail route goes through the same admission and flight key.
- **On capture.** The image monitor hands every new output it settles to
  ``queue_capture_thumbnail``, which queues its preview for one background
  worker thread (``AutoThumbnailer``). Each wake takes at most Worker threads
  (``WORKER_THREADS``) files and writes them on the long-lived job pool
  (``utils.parallel``, shared with scans and thumbnail jobs); enqueuing
  is O(1), so the monitor and ComfyUI's queue never wait. A file already
  queued is not queued again, the queue is bounded (``MAX_QUEUED``) and a
  preview left out is made on its first view instead.
- **After a scan.** A scan that added files checks the missing previews; from
  ``SCAN_THUMBNAIL_JOB_THRESHOLD`` up it starts the thumbnail Activity job
  (missing only, visible and cancellable) instead of writing them silently,
  and fewer go through the worker.

Every preview is written by ``_generate_one`` with the server's thumbnail
settings (format, quality, size limit; ``thumbnail_settings``, #317), into a
temp sibling verified and renamed over the old one, so a reader never sees a
partial file. A request for an older preview name (``_thumb.png``) is answered
with the preview in the current format.

Holds: automatic previews only write files under ``thumbnails/`` and never
touch the database, like the thumbnail job, so they run beside a scan, image
tagging and the Needs review recording (S5 M7). They are not written while a
job runs that replaces the database or removes media and previews: a restore,
a duplicate removal, a sources change, the gallery's thumbnail clear or an
``exclusive`` job (``_auto_thumbnail_blocker``). The holds are checked when a
write starts (after its slot) and again just before its rename.

Shutdown: ``close_auto_thumbnails`` stops the worker at a file boundary,
kills a running ffmpeg, waits a bounded time (and logs when the wait ran
out) and shuts the image pool down; after the app's cleanup
(``final=True``) nothing starts a worker again. Importing this module again
(a package reload) stops the workers the previous import left.
"""

import asyncio
import collections
import functools
import io
import os
import threading
from concurrent.futures import CancelledError, ThreadPoolExecutor
from pathlib import Path

from aiohttp import web

from . import preview_store, thumbnail_settings
from .images import (
    in_trash_below_roots,
    MEDIA_EXTENSIONS,
    THUMBNAIL_CLEAR_TITLE,
    VIDEO_EXTENSIONS,
    _has_traversal,
    _is_within,
    _thumbnail_is_fresh,
    _video_placeholder_image,
    _worker_threads,
    media_preview_path,
)
from .jobs import (
    DUPLICATE_CLEANUP_JOB_KIND,
    RESTORE_JOB_KIND,
    THUMBNAILS_JOB_KIND,
    JobBusy,
)
from .settings_routes import SOURCE_CHANGE_JOB_KIND
from .thumbnail_encode import shutdown_encoders, writer_settings
from .thumbnail_jobs import ThumbnailOptions

try:
    from ...utils.parallel import map_parallel, note_refusal, refused_at_shutdown
except ImportError:  # tests import the package top-level
    from utils.parallel import map_parallel, note_refusal, refused_at_shutdown

try:
    from ...utils.exit_signal import ExitRequested
except ImportError:  # tests import the package top-level
    from utils.exit_signal import ExitRequested

# The placeholder frame a video without a preview is answered with (JPEG
# bytes, never written to disk); written previews follow the settings.
PLACEHOLDER_SIZE = (thumbnail_settings.DEFAULT_EDGE, thumbnail_settings.DEFAULT_EDGE)
PLACEHOLDER_QUALITY = 85
# Source extensions in the order a shared preview name looks for them (R2).
_SOURCE_ORDER = thumbnail_settings.IMAGE_SOURCE_ORDER + tuple(sorted(VIDEO_EXTENSIONS))
# Threads of the interactive image pool (#523): view previews, card facts and
# inspector reads. Fixed, so image buffers stay in a few threads' arenas.
IMAGE_POOL_SIZE = 3
# Previews views may write at once (the whole image pool).
ON_DEMAND_LIMIT = IMAGE_POOL_SIZE
# View requests that may wait on the gate; one more gets its fallback at once.
MAX_WAITING = 64
# How long a pool thread waits for the capture worker's flight of the same file.
FLIGHT_WAIT_SECONDS = 20
# How long close() waits for the worker's batch in hand (ffmpeg's own limit).
CLOSE_WAIT_SECONDS = 30
# Files the capture worker holds; more are made on their first view.
MAX_QUEUED = 10_000
# Undecodable files remembered per session (oldest forgotten first).
MAX_FAILURES_REMEMBERED = 4096
# Missing previews after a scan from which the visible thumbnail job starts:
# a fresh install or a newly added source. Fewer go through the worker.
SCAN_THUMBNAIL_JOB_THRESHOLD = 200
# Jobs beside which no automatic preview is written (see the module docstring).
AUTO_REFUSED_KINDS = frozenset(
    {RESTORE_JOB_KIND, DUPLICATE_CLEANUP_JOB_KIND, SOURCE_CHANGE_JOB_KIND}
)
WORKER_NAME = "PromptManagerThumbnailWorker"
POOL_PREFIX = "pm-image"
# Identifies this import's workers: a reload stops the ones it did not start.
_MODULE_TOKEN = object()

READY, FAILED, BUSY, DEFERRED = "ready", "failed", "busy", "deferred"
# What the gate answers a request past MAX_WAITING.
OVER = object()


def _fingerprint(src):
    """``(path, size, mtime_ns)`` of *src*, or None when it cannot be read."""
    try:
        stat = os.stat(src)
    except OSError:
        return None
    return (os.path.normcase(str(src)), stat.st_size, stat.st_mtime_ns)


def flight_key(root, rel_parts):
    """The single-flight key of ``root/rel_parts``: its folder resolved, case
    folded. The serve route, the history route and the worker share it."""
    path = Path(root).joinpath(*rel_parts)
    return os.path.normcase(os.path.join(os.path.realpath(path.parent), path.name))


def _dst_key(dst):
    return flight_key(Path(dst).parent, (Path(dst).name,))


class DeferredPreview:
    """The preview of *rel* under *root*, resolved when its write starts
    (#320): a queued capture follows the thumbnail location at write time,
    not the one when it was queued."""

    __slots__ = ("root", "rel")

    def __init__(self, root, rel):
        self.root = Path(root)
        self.rel = Path(rel)

    def resolve(self):
        """The preview's path now, or None when nothing may be written."""
        dst = media_preview_path(self.root, self.rel)
        if dst is None or not preview_store.owns(self.root, dst):
            return None
        return dst


def _resolved(dst):
    return dst.resolve() if isinstance(dst, DeferredPreview) else Path(dst)


@functools.lru_cache(maxsize=1)
def video_placeholder_jpeg():
    """The placeholder frame as JPEG bytes (a video whose preview is missing)."""
    buffer = io.BytesIO()
    _video_placeholder_image(PLACEHOLDER_SIZE).save(
        buffer, "JPEG", quality=PLACEHOLDER_QUALITY, optimize=True
    )
    return buffer.getvalue()


def _is_preview_request(filepath):
    """Text check only: ``thumbnails/<...>/<name>_thumb<lower-case ext>``.

    Refuses, as text and before any path is joined, what a joined path could
    take as an anchor: a backslash, a drive or stream colon, and empty, ``.``
    or ``..`` segments (so a leading ``/`` and ``//server/share`` too).
    """
    if "\\" in filepath or ":" in filepath:
        return False
    parts = filepath.split("/")
    if len(parts) < 2 or parts[0] != "thumbnails":
        return False
    if any(part in ("", ".", "..") for part in parts) or "thumbnails" in parts[1:-1]:
        return False
    stem, extension = os.path.splitext(parts[-1])
    return (
        stem.endswith("_thumb")
        and len(stem) > len("_thumb")
        and extension == extension.lower()
        and extension in thumbnail_settings.THUMBNAIL_EXTENSIONS
    )


def _regular_file(path):
    """True for a regular file that is not a link (one lstat)."""
    try:
        return not path.is_symlink() and path.is_file()
    except OSError:
        return False


def _candidates(parts):
    """``(folder parts, base, source extensions)`` of a checked preview
    request: every media extension that owns the requested preview name
    (``thumbnail_settings.owns``), images first (ruling R2)."""
    stem, extension = os.path.splitext(parts[-1])
    wanted = [
        source
        for source in _SOURCE_ORDER
        if source in MEDIA_EXTENSIONS and thumbnail_settings.owns(extension, source)
    ]
    return parts[1:-1], stem[: -len("_thumb")], wanted


def _current_preview(root, parts):
    """The preview *parts* names, in the format written now (#317): a request
    for an older name (``_thumb.png`` before a conversion) is answered with
    the current file. None when the store of *root* has no place for it."""
    _folder, base, _wanted = _candidates(parts)
    name = base + thumbnail_settings.THUMB_SUFFIX + writer_settings().extension
    return preview_store.request_path(root, (*parts[:-1], name))


def _request_key(root, parts):
    """The flight key of the preview *parts* under *root*: the key of its
    disk path (so the serve and history routes share one flight)."""
    path = preview_store.request_path(root, parts)
    if path is None:
        return flight_key(root, parts)
    return _dst_key(path)


def _quick_source(root, parts):
    """``(src, is_video)`` by exact name only (no folder listing), or None."""
    root = Path(root)
    folder_parts, base, wanted = _candidates(parts)
    folder = root.joinpath(*folder_parts)
    for extension in wanted:
        for name in (base + extension, base + extension.upper()):
            path = folder / name
            if _regular_file(path) and _is_within(path, root):
                if in_trash_below_roots(path, [root]):
                    return None  # also the fallback past the gate's cap (#543)
                return path, extension in VIDEO_EXTENSIONS
    return None


def _thumbnail_source(root, parts):
    """``(src, dst, is_video)`` for the preview *parts* under *root*, or None.

    *parts* passed ``_is_preview_request``. The source has the preview's name
    without ``_thumb`` and an extension that owns the requested one (any case:
    exact names first, then one listing of the folder; ruling R4). It must be
    a regular file inside *root*. ``dst`` is the preview in the format written
    now (#317), whatever extension was requested; it is None when the
    preview's folder resolves outside ``root/thumbnails`` (a link): nothing is
    written there.
    """
    root = Path(root)
    folder_parts, base, wanted = _candidates(parts)
    quick = _quick_source(root, parts)
    if quick is not None:
        found = quick[0]
    else:
        found = _scan_folder(root.joinpath(*folder_parts), base, set(wanted))
        if found is None or not _is_within(found, root):
            return None
    if in_trash_below_roots(found, [root]):
        return None
    rel = Path(*folder_parts, found.name)
    dst = media_preview_path(root, rel)
    if dst is None or dst != _current_preview(root, parts):
        return None
    if not preview_store.owns(root, dst):
        dst = None
    return found, dst, found.suffix.lower() in VIDEO_EXTENSIONS


def _scan_folder(folder, base, extensions):
    """A mixed-case extension (``.Png``): one listing of *folder*, or None."""
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                stem, extension = os.path.splitext(entry.name)
                if stem == base and extension.lower() in extensions:
                    path = Path(entry.path)
                    if _regular_file(path):
                        return path
    except OSError:
        return None
    return None


def _deferred(roots, src, dst):
    """The ``DeferredPreview`` of a planned ``(src, dst)``: the root whose
    store the plan wrote *dst* into (nested roots plan one each); *dst* as
    planned when none matches."""
    src = Path(src)
    for root in roots:
        root = Path(root)
        if not src.is_relative_to(root):
            continue
        rel = src.relative_to(root)
        if media_preview_path(root, rel) == Path(dst):
            return DeferredPreview(root, rel)
    return dst


def _stop_stale_workers():
    """Stop capture workers a previous import of this module started."""
    for thread in threading.enumerate():
        token = getattr(thread, "pm_preview_token", None)
        close = getattr(thread, "pm_preview_close", None)
        if token is not None and token is not _MODULE_TOKEN and close is not None:
            close()


class AutoThumbnailer:
    """Single-flight preview writes and the capture worker (thread-safe).

    ``generate(src, dst, is_video, *, stop, guard, track)`` writes one
    preview and returns ``_generate_one``'s result dict; ``may_write()``
    says whether automatic previews may be written now. Views call
    ``ensure`` on the image pool, the monitor calls ``enqueue`` from its
    thread.
    """

    def __init__(
        self,
        generate,
        may_write,
        logger,
        *,
        workers=_worker_threads,
        max_queued=MAX_QUEUED,
        start_worker=True,
    ):
        self._generate = generate
        self._may_write = may_write
        self._logger = logger
        self._workers = workers
        self._max_queued = max_queued
        self._start_worker = start_worker
        self._changed = threading.Condition()
        self._inflight = {}
        self._failures = collections.OrderedDict()
        self._queue = collections.deque()
        self._queued = set()
        self._processes = []
        self._worker = None
        # True from a worker's start until it leaves (under the lock), so an
        # idle worker can exit and the next enqueue starts a fresh one.
        self._worker_running = False
        self._closed = False
        self._busy = False
        self._batch = 0

    @property
    def failure_count(self):
        with self._changed:
            return len(self._failures)

    @property
    def closed(self):
        return self._closed

    def _allowed(self):
        return not self._closed and self._may_write()

    def _track(self, process):
        """Keep an ffmpeg process so close() can kill it."""
        with self._changed:
            self._processes = [p for p in self._processes if p.poll() is None]
            self._processes.append(process)
            closed = self._closed
        if closed:
            process.kill()

    # ── one preview ───────────────────────────────────────────────────

    def ensure(self, src, dst, is_video):
        """Make *dst* for *src* unless it is fresh; returns the outcome.

        ``ready`` (the preview is there), ``failed`` (the file cannot be
        decoded, remembered), ``busy`` (an I/O error, the file is gone, or
        another flight did not finish) or ``deferred`` (a hold refuses
        automatic writes, checked when the write starts and before its
        rename). Blocking: call it off the event loop.
        """
        if _thumbnail_is_fresh(src, dst):
            return READY
        fingerprint = _fingerprint(src)
        if fingerprint is None:
            return BUSY
        key = _dst_key(dst)
        with self._changed:
            if fingerprint in self._failures:
                return FAILED
            flight = self._inflight.get(key)
            if flight is None:
                self._inflight[key] = done = threading.Event()
        if flight is not None:
            flight.wait(FLIGHT_WAIT_SECONDS)
            if _thumbnail_is_fresh(src, dst):
                return READY
            with self._changed:
                return FAILED if fingerprint in self._failures else BUSY
        try:
            return self._lead(src, dst, is_video, fingerprint)
        finally:
            with self._changed:
                self._inflight.pop(key, None)
                self._changed.notify_all()
            done.set()

    def _lead(self, src, dst, is_video, fingerprint):
        if not self._allowed():
            return DEFERRED
        if _thumbnail_is_fresh(src, dst):
            return READY
        result = self._generate(
            src,
            dst,
            is_video,
            stop=lambda: self._closed,
            guard=self._allowed,
            track=self._track,
        )
        if result.get("action") in ("generated", "skipped"):
            return READY
        if result.get("held"):
            return DEFERRED
        if not result.get("decode"):
            return BUSY  # an I/O error says nothing about the file
        error = result.get("error") or "failed"
        with self._changed:
            self._failures[fingerprint] = error
            while len(self._failures) > MAX_FAILURES_REMEMBERED:
                self._failures.popitem(last=False)
        self._logger.info(
            f"No preview for {os.path.basename(str(src))} ({error}); "
            "the original is shown"
        )
        return FAILED

    # ── the capture worker ────────────────────────────────────────────

    def enqueue(self, src, dst, is_video):
        """Queue *dst*'s preview for the worker; False when already queued,
        full, closed, nowhere to write it or its worker is refused at
        interpreter exit. Never blocks on a write. *dst* may be a
        ``DeferredPreview``: its path is resolved again when the write
        starts."""
        now = _resolved(dst)
        if now is None:
            return False
        key = _dst_key(now)
        if not isinstance(dst, DeferredPreview):
            dst = now
        with self._changed:
            if self._closed or key in self._queued:
                return False
            if len(self._queue) >= self._max_queued:
                return False
            self._queued.add(key)
            self._queue.append((key, Path(src), dst, bool(is_video)))
            self._busy = True
            started = True
            if self._start_worker and not self._worker_running:
                started = self._start_worker_locked()
            if not started:
                # Refused at interpreter exit: no worker will take it (#375).
                self._queue.pop()
                self._queued.discard(key)
            self._changed.notify_all()
        return started

    def _start_worker_locked(self):
        """Start the worker (lock held); False only when the interpreter is
        exiting (#375: logged once at DEBUG, the caller drops the file).
        Another start failure leaves the file queued and the next enqueue
        tries again."""
        worker = threading.Thread(target=self._run, name=WORKER_NAME, daemon=True)
        worker.pm_preview_token = _MODULE_TOKEN
        worker.pm_preview_close = self.close
        self._worker_running = True
        try:
            worker.start()
        except RuntimeError as error:
            self._worker_running = False
            self._busy = False
            if refused_at_shutdown(error):
                note_refusal(self._logger, "The automatic preview worker")
                return False
            self._logger.warning(
                "The automatic preview worker could not start; the next output "
                "tries again",
                exc_info=True,
            )
            return True
        self._worker = worker
        return True

    def _run(self):
        try:
            self._drain()
        except ExitRequested:
            pass  # #376: the interpreter is exiting; queued files are dropped
        except Exception:
            self._logger.warning("The automatic preview worker stopped", exc_info=True)
        finally:
            # Whatever ended the loop, the next enqueue starts a fresh worker.
            with self._changed:
                self._worker_running = False
                self._busy = False
                self._batch = 0
                self._changed.notify_all()

    def _drain(self):
        while True:
            with self._changed:
                if self._closed or not self._queue:
                    # Idle or closed: leave, so no thread outlives its work.
                    self._busy = False
                    self._worker_running = False
                    self._changed.notify_all()
                    return
                workers = max(1, int(self._workers() or 1))
                take = min(len(self._queue), workers)
                batch = [self._queue.popleft() for _ in range(take)]
                self._batch = len(batch)
                self._changed.notify_all()
            try:
                # The job pool's size is the setting; a smaller batch uses
                # fewer of its threads (#523: one long-lived pool).
                map_parallel(self._write_queued, batch, workers)
            finally:
                with self._changed:
                    self._batch = 0
                    for key, *_rest in batch:
                        self._queued.discard(key)

    def _write_queued(self, item):
        _key, src, dst, is_video = item
        try:
            dst = _resolved(dst)
            if dst is None:
                return  # nowhere to write it now: made on its first view
            self.ensure(src, dst, is_video)
        except Exception:  # one file never stops the worker
            self._logger.warning(
                f"Automatic preview failed for {src.name}", exc_info=True
            )

    def close(self, timeout=None):
        """Stop the worker at its next file boundary (queued files are
        dropped), kill a running ffmpeg and wait up to ``timeout`` seconds
        (None: CLOSE_WAIT_SECONDS, read now); a worker still running after
        that is logged.

        Returns True when there was no worker or it ended; False when it is
        still running, or when called on the worker itself (it only signals).
        """
        wait = CLOSE_WAIT_SECONDS if timeout is None else timeout
        with self._changed:
            self._closed = True
            self._queue.clear()
            self._queued.clear()
            processes, self._processes = self._processes, []
            self._changed.notify_all()
            worker = self._worker
        for process in processes:
            try:
                process.kill()
            except OSError:
                # the process has already exited
                pass
        if worker is None:
            return True
        if worker is threading.current_thread():
            return False
        worker.join(wait)
        if worker.is_alive():
            self._logger.warning(
                "The automatic preview worker is still running after "
                f"{wait} s; it stops at its next file"
            )
            return False
        return True

    # ── waits (tests and shutdown; never timers) ──────────────────────

    def _wait_for(self, predicate, timeout):
        with self._changed:
            return self._changed.wait_for(predicate, timeout)

    def wait_idle(self, timeout=None):
        """True once the worker has nothing queued or in hand."""
        return self._wait_for(lambda: not self._busy, timeout)

    def wait_batch(self, size, timeout=None):
        """True once the worker holds a batch of *size* files."""
        return self._wait_for(lambda: self._batch == size, timeout)


class _PreviewGate:
    """Admission of view requests on the event loop (#249 I2).

    Waiters are coroutines: at most ``limit`` previews are written at once
    on the image pool, one flight runs per key, and past ``MAX_WAITING``
    waiting requests ``run`` answers ``OVER`` at once.
    """

    def __init__(self, limit, logger):
        self.loop = asyncio.get_running_loop()
        self._slots = asyncio.Semaphore(limit)
        self._flights = {}
        self._tasks = set()
        self._logger = logger
        self._tick = asyncio.Event()
        self.waiting = 0

    def _changed(self):
        tick, self._tick = self._tick, asyncio.Event()
        tick.set()

    async def settled(self, predicate):
        """Return once ``predicate()`` holds (re-checked at each change)."""
        while not predicate():
            await self._tick.wait()

    async def run(self, key, work, pool):
        """``work()``'s answer for *key* (shared by every request of the
        flight), None when it raised, or OVER past the cap."""
        if self.waiting >= MAX_WAITING:
            return OVER
        flight = self._flights.get(key)
        if flight is None:
            flight = self.loop.create_future()
            self._flights[key] = flight
            task = self.loop.create_task(self._lead(key, flight, work, pool))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        self.waiting += 1
        self._changed()
        try:
            return await asyncio.shield(flight)
        finally:
            self.waiting -= 1
            self._changed()

    async def _lead(self, key, flight, work, pool):
        result = None
        try:
            async with self._slots:
                result = await self.loop.run_in_executor(pool, work)
        except Exception:
            self._logger.warning("Automatic preview failed", exc_info=True)
        finally:
            self._flights.pop(key, None)
            if not flight.done():
                flight.set_result(result)


def _on_image_thread():
    return threading.current_thread().name.startswith(POOL_PREFIX + "_")


class AutoThumbnailMixin:
    """Automatic previews on the API: views, the monitor's queue, scans."""

    _auto_thumbnailer = None
    _auto_thumbnails_shut = False
    _preview_gate_obj = None
    _image_pool_obj = None
    _auto_thumbnailer_lock = threading.Lock()

    def _auto_thumbnails(self):
        """This API's AutoThumbnailer, created on first use (no worker after
        the app's cleanup)."""
        with AutoThumbnailMixin._auto_thumbnailer_lock:
            if self._auto_thumbnailer is None:
                self._auto_thumbnailer = AutoThumbnailer(
                    self._write_auto_thumbnail,
                    lambda: self._auto_thumbnail_blocker() is None,
                    self.logger,
                    start_worker=not self._auto_thumbnails_shut,
                )
            return self._auto_thumbnailer

    def refuse_auto_thumbnails(self):
        """Refuse every later capture without stopping the worker yet (the
        package teardown, before ``close_auto_thumbnails(final=True)``)."""
        with AutoThumbnailMixin._auto_thumbnailer_lock:
            self._auto_thumbnails_shut = True

    def close_auto_thumbnails(self, final=False, timeout=None):
        """Stop the capture worker and the image pool, and forget them.

        Bounded by ``timeout`` (see AutoThumbnailer.close). ``final`` (the
        package teardown) also keeps a late monitor event from starting a
        worker again; a test reset passes False and the next use starts
        fresh ones. Returns True when no capture worker is left running.
        """
        with AutoThumbnailMixin._auto_thumbnailer_lock:
            if final:
                self._auto_thumbnails_shut = True
            thumbnailer, self._auto_thumbnailer = self._auto_thumbnailer, None
            pool, self._image_pool_obj = self._image_pool_obj, None
            self._preview_gate_obj = None
        closed = True
        if thumbnailer is not None:
            closed = thumbnailer.close(timeout)
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)
        if final:
            shutdown_encoders()  # kill JPEG XL / ffmpeg encoders still running
        return closed

    def _auto_thumbnail_failures(self):
        thumbnailer = self._auto_thumbnailer
        return 0 if thumbnailer is None else thumbnailer.failure_count

    def _write_auto_thumbnail(self, src, dst, is_video, *, stop, guard, track):
        return self._generate_one(
            src,
            dst,
            writer_settings(),
            is_video,
            stop=stop,
            guard=guard,
            track=track,
        )

    def _auto_thumbnail_blocker(self):
        """The running job automatic previews wait for, or None (any thread)."""
        for job in self._jobs.running():
            if job.admission == "exclusive" or job.kind in AUTO_REFUSED_KINDS:
                return job
            if job.kind == THUMBNAILS_JOB_KIND and job.title == THUMBNAIL_CLEAR_TITLE:
                return job
        return None

    # ── the view admission ────────────────────────────────────────────

    def _preview_gate(self):
        """The gate of the running loop (a new loop gets a new one)."""
        gate = self._preview_gate_obj
        if gate is None or gate.loop is not asyncio.get_running_loop():
            gate = self._preview_gate_obj = _PreviewGate(ON_DEMAND_LIMIT, self.logger)
        return gate

    def _image_pool(self):
        """The interactive image pool (#523): IMAGE_POOL_SIZE threads for view
        previews (behind their gate), card facts and inspector reads, so
        image buffers never spread over the default executor's threads.

        None after the package teardown (``close_auto_thumbnails(final=True)``):
        a late request then runs on the default executor (``run_in_executor``
        with None) and starts no thread the teardown no longer stops.
        """
        with AutoThumbnailMixin._auto_thumbnailer_lock:
            if self._image_pool_obj is None and not self._auto_thumbnails_shut:
                self._image_pool_obj = ThreadPoolExecutor(
                    IMAGE_POOL_SIZE, thread_name_prefix=POOL_PREFIX
                )
            return self._image_pool_obj

    async def _run_image(self, func, *args, **kwargs):
        """``_run_in_executor`` for interactive image work: the image pool.

        After the teardown, or with the pool shut down in between (a test
        reset), the call runs on the default executor as before.
        """
        call = functools.partial(func, *args, **kwargs)
        loop = asyncio.get_running_loop()
        try:
            future = loop.run_in_executor(self._image_pool(), call)
        except RuntimeError:  # submit refused: the pool was shut down
            future = loop.run_in_executor(None, call)
        return await future

    def _map_image(self, func, items):
        """``func`` over *items* on the image pool, in order (blocking).

        Called from an image pool thread, after the teardown, or with the
        pool shut down in between (its queued items are cancelled), the
        items it did not run run on the calling thread.
        """
        items = list(items)
        pool = None if not items or _on_image_thread() else self._image_pool()
        if pool is None:
            return [func(item) for item in items]
        futures = []
        try:
            for item in items:
                futures.append(pool.submit(func, item))
        except RuntimeError:
            pass  # shut down in between: the rest run here
        results = []
        for index, item in enumerate(items):
            future = futures[index] if index < len(futures) else None
            if future is None or future.cancelled():
                results.append(func(item))
                continue
            try:
                results.append(future.result())
            except CancelledError:  # cancelled while this caller waited
                results.append(func(item))
        return results

    def _image_pool_threads(self):
        pool = self._image_pool_obj
        return [] if pool is None else list(getattr(pool, "_threads", ()))

    def _video_placeholder_bytes(self):
        return video_placeholder_jpeg()

    async def _admit_preview(self, src, dst, is_video):
        """Make one preview through the view admission (the history route).

        Shares the serve route's flight key; every flight answers the
        ``ensure`` outcome (or None, or OVER), whichever route started it.
        """
        return await self._preview_gate().run(
            flight_key(Path(dst).parent, (Path(dst).name,)),
            functools.partial(self._ensure_view, src, dst, is_video),
            self._image_pool(),
        )

    def _ensure_view(self, src, dst, is_video):
        """``ensure``, reporting a preview removed under us as busy."""
        outcome = self._auto_thumbnails().ensure(src, dst, is_video)
        if outcome == READY and not Path(dst).is_file():
            return BUSY  # removed under us: the fallback this once
        return outcome

    # ── on first view ─────────────────────────────────────────────────

    def _preview_on_demand(self, root, parts):
        """The ``ensure`` outcome for the preview *parts* under *root*
        (``deferred`` when its folder is linked out of the root), or None
        when no media file previews as *parts*."""
        located = _thumbnail_source(root, parts)
        if located is None:
            return None
        src, dst, is_video = located
        if dst is None:
            return DEFERRED
        return self._ensure_view(src, dst, is_video)

    def _preview_response(self, outcome, src, dst, is_video):
        if outcome == READY and dst is not None:
            return self._file_response(dst)
        if is_video:
            return web.Response(
                body=video_placeholder_jpeg(),
                content_type="image/jpeg",
                headers={"Cache-Control": "no-store"},
            )
        response = web.FileResponse(src)
        response.headers["Cache-Control"] = "no-store"
        return response

    async def _serve_missing_thumbnail(self, output_dirs, filepath):
        """The made preview, or its fallback; None when *filepath* names no
        preview of a media file in *output_dirs*.

        The flight answers only the outcome (a history flight may be the one
        joined); the file it previews is derived here: the preview path is
        the request's own, and a fallback looks its source up again on the
        image pool (bounded by it, never the default executor).
        """
        if _has_traversal(filepath) or not _is_preview_request(filepath):
            return None
        parts = tuple(filepath.split("/"))
        gate = self._preview_gate()
        pool = self._image_pool()
        for root in output_dirs:
            root = Path(root)
            outcome = await gate.run(
                _request_key(root, parts),
                functools.partial(self._preview_on_demand, root, parts),
                pool,
            )
            if outcome is None:
                continue
            if outcome is OVER:
                # Past the cap: exact names only, no listing, no write.
                quick = _quick_source(root, parts)
                if quick is None:
                    continue
                return self._preview_response(BUSY, quick[0], None, quick[1])
            dst = _current_preview(root, parts)
            if outcome == READY and dst is not None:
                return self._file_response(dst)
            located = await gate.loop.run_in_executor(
                pool, _thumbnail_source, root, parts
            )
            if located is None:
                continue
            src, _dst, is_video = located
            return self._preview_response(outcome, src, None, is_video)
        return None

    # ── on capture ────────────────────────────────────────────────────

    def queue_capture_thumbnail(self, image_path):
        """Queue the preview of a new output (the monitor's thread).

        Returns ``queued``, ``fresh`` (its preview is current) or ``skipped``
        (not a media file under a current root, a link, a preview itself,
        already queued, after the app's cleanup, or when its worker is refused
        at interpreter exit). Never writes on the caller's thread.
        """
        if self._auto_thumbnails_shut:
            return "skipped"
        located = self._capture_source(image_path)
        if located is None:
            return "skipped"
        src, root, rel, is_video = located
        dst = DeferredPreview(root, rel).resolve()
        if dst is None:
            return "skipped"
        if _thumbnail_is_fresh(src, dst):
            return "fresh"
        if self._auto_thumbnails().enqueue(src, DeferredPreview(root, rel), is_video):
            return "queued"
        return "skipped"

    def _capture_target(self, image_path):
        """``(src, dst, is_video)`` of a new output's preview now, or None."""
        located = self._capture_source(image_path)
        if located is None:
            return None
        src, root, rel, is_video = located
        dst = DeferredPreview(root, rel).resolve()
        return None if dst is None else (src, dst, is_video)

    def _capture_source(self, image_path):
        """``(src, root, rel, is_video)`` of a media file under a current
        root (not a preview, a link or in a trash folder), or None."""
        path = Path(os.path.abspath(str(image_path)))
        if path.suffix.lower() not in MEDIA_EXTENSIONS or not _regular_file(path):
            return None
        folded = os.path.normcase(str(path))
        for root in self._get_all_output_dirs():
            root = Path(os.path.abspath(str(root)))
            base = os.path.normcase(str(root)).rstrip("\\/") + os.sep
            if not folded.startswith(base) or not _is_within(path, root):
                continue
            rel = Path(os.path.relpath(path, root))
            if "thumbnails" in rel.parts[:-1] or in_trash_below_roots(path, [root]):
                return None
            return path, root, rel, path.suffix.lower() in VIDEO_EXTENSIONS
        return None

    # ── after a scan ──────────────────────────────────────────────────

    async def _thumbnails_after_scan(self, job, output_dirs=None):
        """Hand a finished scan's missing previews on; returns what it did.

        None (the scan added nothing), ``job`` (the thumbnail job started for
        ``SCAN_THUMBNAIL_JOB_THRESHOLD`` or more missing previews), ``busy``
        (a job refused it; the previews are made on view), ``queued`` (fewer,
        through the worker) or ``none`` (nothing missing). It counts the
        sources the scan walked (``output_dirs``, narrowed to the job's
        ``roots``; every source when None). The server log says which.
        """
        if job.counters.get("added", 0) <= 0:
            return None
        if output_dirs is None:
            output_dirs = await self._run_in_executor(self._get_all_output_dirs)
        roots = job.scope.get("roots")
        if roots:
            output_dirs = [output_dirs[i] for i in roots if 0 <= i < len(output_dirs)]
        plan = await self._run_in_executor(self._plan_gallery_thumbnails, output_dirs)
        missing = len(plan["targets"]) + plan["remaining"]
        if missing >= SCAN_THUMBNAIL_JOB_THRESHOLD:
            options = ThumbnailOptions("missing", writer_settings())
            try:
                self._start_thumbnail_job(options)
            except JobBusy as busy:
                self.logger.info(
                    f"{missing:,} previews missing after the scan; the thumbnail "
                    f"job did not start while {busy.running.title} runs"
                )
                return "busy"
            self.logger.info(
                f"{missing:,} previews missing after the scan; thumbnail job started"
            )
            return "job"
        if not missing:
            return "none"
        thumbnailer = self._auto_thumbnails()
        for src, dst, is_video in plan["targets"]:
            thumbnailer.enqueue(src, _deferred(output_dirs, src, dst), is_video)
        self.logger.info(f"{missing:,} previews missing after the scan; queued")
        return "queued"

    async def _hand_off_scan_thumbnails(self, job, output_dirs=None):
        """``_thumbnails_after_scan`` for the scan runner: never raises."""
        try:
            return await self._thumbnails_after_scan(job, output_dirs)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.warning("Previews after the scan failed", exc_info=True)
            return None


# A package reload re-runs this module: stop the workers the old one left.
_stop_stale_workers()
