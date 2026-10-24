"""Automatic thumbnails (#249): previews are a cache the server keeps itself.

The approved mock has no repeated global thumbnail prompt
(``docs/concepts/workspace/README.md``), so the workspace never asks. Every
listing gives an image or video a thumbnail URL (``media_thumbnail_url``),
and the previews behind those URLs are made here, in three ways:

- **On first view.** ``GET /prompt_manager/images/serve/thumbnails/<rel>_thumb
  <ext>`` for a preview that does not exist yet maps it back to its media file
  under the same root (text containment first, then a regular file inside the
  root, never through a link), makes the preview and serves it. At most
  ``ON_DEMAND_LIMIT`` views write at once and one flight runs per file: a
  second view of a preview being made waits for it. When the preview cannot be
  made the original is served (``Cache-Control: no-store``) with its own
  content type. A file that cannot be decoded is remembered for this session
  (keyed by its path, size and mtime) so it is not tried again on every view,
  and counted for the System Overview (``failed`` in
  ``/images/thumbnails/status``). A preview another program holds open
  (Windows) is not remembered: the next view tries again.
- **On capture.** The image monitor hands every new output it settles to
  ``queue_capture_thumbnail``, which queues its preview for one background
  worker thread (``AutoThumbnailer``). Each wake takes at most Worker threads
  (``WORKER_THREADS``) files and writes them on that many threads; enqueuing
  is O(1), so the monitor and ComfyUI's queue never wait. A file already
  queued is not queued again, the queue is bounded (``MAX_QUEUED``) and a
  preview left out is made on its first view instead.
- **After a scan.** A scan that added files checks the missing previews; from
  ``SCAN_THUMBNAIL_JOB_THRESHOLD`` up it starts the thumbnail Activity job
  (missing only, visible and cancellable) instead of writing them silently,
  and fewer go through the worker.

Every preview is written by ``_generate_one`` with the thumbnail job's default
size and quality (``DEFAULT_THUMBNAIL_EDGE``, medium), into a temp sibling
renamed over the old one, so a reader never sees a partial file.

Holds: automatic previews only write files under ``thumbnails/`` and never
touch the database, like the thumbnail job, so they run beside a scan, image
tagging and the Needs review recording (S5 M7). They are not written while a
job runs that replaces the database or removes media and previews: a restore,
a duplicate removal, a sources change, the gallery's thumbnail clear or an
``exclusive`` job (``_auto_thumbnail_blocker``). A view then gets the original
and a queued file is left for its first view.
"""

import asyncio
import collections
import os
import threading
from pathlib import Path

from aiohttp import web

from .images import (
    GALLERY_IMAGE_EXTENSIONS,
    MEDIA_EXTENSIONS,
    PREVIEW_IN_USE,
    PREVIEW_NOT_WRITABLE,
    THUMBNAIL_CLEAR_TITLE,
    THUMBNAIL_ENCODINGS,
    VIDEO_EXTENSIONS,
    _has_traversal,
    _is_within,
    _thumbnail_is_fresh,
    _worker_threads,
    media_thumbnail_rel,
)
from .jobs import (
    DUPLICATE_CLEANUP_JOB_KIND,
    RESTORE_JOB_KIND,
    THUMBNAILS_JOB_KIND,
    JobBusy,
)
from .settings_routes import SOURCE_CHANGE_JOB_KIND
from .thumbnail_jobs import DEFAULT_THUMBNAIL_EDGE, ThumbnailOptions

try:
    from ...utils.parallel import map_parallel
except ImportError:  # tests import the package top-level
    from utils.parallel import map_parallel

AUTO_THUMBNAIL_EDGE = DEFAULT_THUMBNAIL_EDGE
AUTO_THUMBNAIL_SIZE = (AUTO_THUMBNAIL_EDGE, AUTO_THUMBNAIL_EDGE)
AUTO_THUMBNAIL_ENCODING = THUMBNAIL_ENCODINGS["medium"]
# Views that may write a preview at the same time; more wait for a slot.
ON_DEMAND_LIMIT = 3
# How long a view waits for a slot or another view's flight before it is
# answered with the original (the next view tries again).
ON_DEMAND_WAIT_SECONDS = 20
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

READY, FAILED, BUSY, DEFERRED = "ready", "failed", "busy", "deferred"
# _generate_one errors that say nothing about the file: not remembered.
_TRANSIENT_ERRORS = frozenset(
    {PREVIEW_IN_USE, PREVIEW_NOT_WRITABLE, "File no longer exists"}
)


def _fingerprint(src):
    """``(path, size, mtime_ns)`` of *src*, or None when it cannot be read."""
    try:
        stat = os.stat(src)
    except OSError:
        return None
    return (os.path.normcase(str(src)), stat.st_size, stat.st_mtime_ns)


def _is_preview_request(filepath):
    """Text check only: ``thumbnails/<...>/<name>_thumb<lower-case ext>``."""
    parts = filepath.replace("\\", "/").split("/")
    if len(parts) < 2 or parts[0] != "thumbnails" or "thumbnails" in parts[1:-1]:
        return False
    stem, extension = os.path.splitext(parts[-1])
    return (
        stem.endswith("_thumb")
        and len(stem) > len("_thumb")
        and extension == extension.lower()
        and extension in GALLERY_IMAGE_EXTENSIONS
    )


def _regular_file(path):
    """True for a regular file that is not a link (one lstat)."""
    try:
        return not path.is_symlink() and path.is_file()
    except OSError:
        return False


def _thumbnail_source(root, filepath):
    """``(src, dst, is_video)`` for the preview *filepath* under *root*, or None.

    *filepath* passed ``_is_preview_request`` and the serve route's traversal
    check. The source has the preview's name without ``_thumb`` and an
    extension that previews as the requested one (any case); a ``.jpg`` may
    also be a video's. It must be a regular file inside *root*. ``dst`` is
    None when the ``thumbnails`` folder lies outside *root* (a link): the
    original is then served and nothing is written there.
    """
    root = Path(root)
    parts = filepath.replace("\\", "/").split("/")
    folder = root.joinpath(*parts[1:-1])
    stem, extension = os.path.splitext(parts[-1])
    base = stem[: -len("_thumb")]
    wanted = [extension] + (sorted(VIDEO_EXTENSIONS) if extension == ".jpg" else [])
    names = [base + ext for ext in wanted] + [base + ext.upper() for ext in wanted]
    found = next((folder / n for n in names if _regular_file(folder / n)), None)
    if found is None:
        found = _scan_folder(folder, base, set(wanted))
    if found is None or not _is_within(found, root):
        return None
    rel = Path(*parts[1:-1], found.name)
    dst = root / media_thumbnail_rel(rel)
    if dst != root.joinpath(*parts):
        return None
    if not _is_within(root / "thumbnails", root) or not _is_within(
        dst, root / "thumbnails"
    ):
        dst = None  # a preview folder linked elsewhere is never written
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


class AutoThumbnailer:
    """Single-flight preview writes for views, and the capture worker.

    ``generate(src, dst, is_video)`` writes one preview and returns
    ``_generate_one``'s result dict; ``may_write()`` says whether automatic
    previews may be written now. Thread-safe: views call ``ensure`` on the
    executor, the monitor calls ``enqueue`` from its own thread.
    """

    def __init__(
        self,
        generate,
        may_write,
        logger,
        *,
        workers=_worker_threads,
        on_demand_limit=ON_DEMAND_LIMIT,
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
        self._followers = 0
        self._slot_waiters = 0
        self._batch = 0
        self._failures = collections.OrderedDict()
        self._slots = threading.BoundedSemaphore(on_demand_limit)
        self._queue = collections.deque()
        self._queued = set()
        self._worker = None
        self._closed = False
        self._busy = False

    # ── views ─────────────────────────────────────────────────────────

    @property
    def failure_count(self):
        with self._changed:
            return len(self._failures)

    def ensure(self, src, dst, is_video, *, on_demand=True):
        """Make *dst* for *src* unless it is fresh; returns the outcome.

        ``ready`` (the preview is there), ``failed`` (the file cannot be
        previewed, remembered), ``busy`` (no slot in time, a transient write
        error, or another flight that did not finish) or ``deferred`` (a hold
        refuses automatic writes). Blocking: call it off the event loop.
        """
        if _thumbnail_is_fresh(src, dst):
            return READY
        fingerprint = _fingerprint(src)
        if fingerprint is None:
            return BUSY
        key = os.path.normcase(str(dst))
        with self._changed:
            if fingerprint in self._failures:
                return FAILED
            flight = self._inflight.get(key)
            if flight is None:
                self._inflight[key] = done = threading.Event()
            else:
                self._followers += 1
                self._changed.notify_all()
        if flight is not None:
            try:
                flight.wait(ON_DEMAND_WAIT_SECONDS)
            finally:
                with self._changed:
                    self._followers -= 1
            return self._settled(src, dst, fingerprint)
        try:
            return self._lead(src, dst, is_video, fingerprint, on_demand)
        finally:
            with self._changed:
                self._inflight.pop(key, None)
                self._changed.notify_all()
            done.set()

    def _settled(self, src, dst, fingerprint):
        if _thumbnail_is_fresh(src, dst):
            return READY
        with self._changed:
            return FAILED if fingerprint in self._failures else BUSY

    def _lead(self, src, dst, is_video, fingerprint, on_demand):
        if not self._may_write():
            return DEFERRED
        if on_demand and not self._take_slot():
            return BUSY
        try:
            if _thumbnail_is_fresh(src, dst):
                return READY
            result = self._generate(src, dst, is_video)
        finally:
            if on_demand:
                self._slots.release()
        if result.get("action") in ("generated", "skipped"):
            return READY
        error = result.get("error") or "failed"
        if error in _TRANSIENT_ERRORS:
            return BUSY
        with self._changed:
            self._failures[fingerprint] = error
            while len(self._failures) > MAX_FAILURES_REMEMBERED:
                self._failures.popitem(last=False)
        self._logger.info(
            f"No preview for {os.path.basename(str(src))} ({error}); "
            "the original is shown"
        )
        return FAILED

    def _take_slot(self):
        if self._slots.acquire(blocking=False):
            return True
        with self._changed:
            self._slot_waiters += 1
            self._changed.notify_all()
        try:
            return self._slots.acquire(timeout=ON_DEMAND_WAIT_SECONDS)
        finally:
            with self._changed:
                self._slot_waiters -= 1

    # ── the capture worker ────────────────────────────────────────────

    def enqueue(self, src, dst, is_video):
        """Queue *dst*'s preview for the worker; False when already queued,
        full or closed. Never blocks on a write."""
        key = os.path.normcase(str(dst))
        with self._changed:
            if self._closed or key in self._queued:
                return False
            if len(self._queue) >= self._max_queued:
                return False
            self._queued.add(key)
            self._queue.append((key, Path(src), Path(dst), bool(is_video)))
            self._busy = True
            if self._start_worker and (
                self._worker is None or not self._worker.is_alive()
            ):
                self._worker = threading.Thread(
                    target=self._run,
                    name="PromptManagerThumbnailWorker",
                    daemon=True,
                )
                self._worker.start()
            self._changed.notify_all()
        return True

    def _run(self):
        while True:
            with self._changed:
                while not self._queue and not self._closed:
                    self._busy = False
                    self._changed.notify_all()
                    self._changed.wait()
                if self._closed:
                    self._busy = False
                    self._changed.notify_all()
                    return
                take = min(len(self._queue), max(1, int(self._workers() or 1)))
                batch = [self._queue.popleft() for _ in range(take)]
                self._batch = len(batch)
                self._changed.notify_all()
            try:
                map_parallel(self._write_queued, batch, len(batch))
            finally:
                with self._changed:
                    self._batch = 0
                    for key, *_rest in batch:
                        self._queued.discard(key)

    def _write_queued(self, item):
        _key, src, dst, is_video = item
        try:
            self.ensure(src, dst, is_video, on_demand=False)
        except Exception:  # one file never stops the worker
            self._logger.warning(
                f"Automatic preview failed for {src.name}", exc_info=True
            )

    def close(self):
        """Stop the worker after its current batch; queued files are dropped."""
        with self._changed:
            self._closed = True
            self._queue.clear()
            self._queued.clear()
            self._changed.notify_all()
            worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(ON_DEMAND_WAIT_SECONDS)

    # ── waits (tests and shutdown; never timers) ──────────────────────

    def _wait_for(self, predicate, timeout):
        with self._changed:
            return self._changed.wait_for(predicate, timeout)

    def wait_idle(self, timeout=None):
        """True once the worker has nothing queued or in hand."""
        return self._wait_for(lambda: not self._busy, timeout)

    def wait_followers(self, count, timeout=None):
        """True once *count* views wait on another view's flight."""
        return self._wait_for(lambda: self._followers >= count, timeout)

    def wait_slot_waiters(self, count, timeout=None):
        """True once *count* views wait for a write slot."""
        return self._wait_for(lambda: self._slot_waiters >= count, timeout)

    def wait_batch(self, size, timeout=None):
        """True once the worker holds a batch of *size* files."""
        return self._wait_for(lambda: self._batch == size, timeout)


class AutoThumbnailMixin:
    """Automatic previews on the API: views, the monitor's queue, scans."""

    _auto_thumbnailer = None
    _auto_thumbnailer_lock = threading.Lock()

    def _auto_thumbnails(self):
        """This API's AutoThumbnailer, created on first use."""
        with AutoThumbnailMixin._auto_thumbnailer_lock:
            if self._auto_thumbnailer is None:
                self._auto_thumbnailer = AutoThumbnailer(
                    self._write_auto_thumbnail,
                    lambda: self._auto_thumbnail_blocker() is None,
                    self.logger,
                )
            return self._auto_thumbnailer

    def close_auto_thumbnails(self):
        """Stop the capture worker and forget it (shutdown, a test's reset).

        Waits for the batch in hand, so nothing writes into the output tree
        after it returns; the next use starts a fresh one.
        """
        with AutoThumbnailMixin._auto_thumbnailer_lock:
            thumbnailer, self._auto_thumbnailer = self._auto_thumbnailer, None
        if thumbnailer is not None:
            thumbnailer.close()

    def _auto_thumbnail_failures(self):
        thumbnailer = self._auto_thumbnailer
        return 0 if thumbnailer is None else thumbnailer.failure_count

    def _write_auto_thumbnail(self, src, dst, is_video):
        return self._generate_one(
            src, dst, AUTO_THUMBNAIL_SIZE, is_video, encoding=AUTO_THUMBNAIL_ENCODING
        )

    def _auto_thumbnail_blocker(self):
        """The running job automatic previews wait for, or None (any thread)."""
        for job in self._jobs.running():
            if job.admission == "exclusive" or job.kind in AUTO_REFUSED_KINDS:
                return job
            if job.kind == THUMBNAILS_JOB_KIND and job.title == THUMBNAIL_CLEAR_TITLE:
                return job
        return None

    def _history_preview(self, src, dst, is_video):
        """A recorded run's missing preview, made like a view's (blocking)."""
        return self._auto_thumbnails().ensure(Path(src), Path(dst), is_video)

    # ── on first view ─────────────────────────────────────────────────

    def _preview_on_demand(self, root, filepath):
        """``(outcome, src, dst)`` for a missing preview under *root*, or None."""
        located = _thumbnail_source(root, filepath)
        if located is None:
            return None
        src, dst, is_video = located
        if dst is None:
            return DEFERRED, src, dst
        outcome = self._auto_thumbnails().ensure(src, dst, is_video)
        if outcome == READY and not dst.is_file():
            outcome = BUSY  # removed under us: show the original this once
        return outcome, src, dst

    async def _serve_missing_thumbnail(self, output_dirs, filepath):
        """The made preview, or the original when it cannot be made; None
        when *filepath* names no preview of a media file in *output_dirs*."""
        if _has_traversal(filepath) or not _is_preview_request(filepath):
            return None
        for root in output_dirs:
            answer = await self._run_in_executor(
                self._preview_on_demand, Path(root), filepath
            )
            if answer is None:
                continue
            outcome, src, dst = answer
            if outcome == READY:
                return self._file_response(dst)
            response = web.FileResponse(src)
            response.headers["Cache-Control"] = "no-store"
            return response
        return None

    # ── on capture ────────────────────────────────────────────────────

    def queue_capture_thumbnail(self, image_path):
        """Queue the preview of a new output (the monitor's thread).

        Returns ``queued``, ``fresh`` (its preview is current) or ``skipped``
        (not a media file under a current root, a link, a preview itself, or
        already queued). Never writes on the caller's thread.
        """
        located = self._capture_target(image_path)
        if located is None:
            return "skipped"
        src, dst, is_video = located
        if _thumbnail_is_fresh(src, dst):
            return "fresh"
        if self._auto_thumbnails().enqueue(src, dst, is_video):
            return "queued"
        return "skipped"

    def _capture_target(self, image_path):
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
            if "thumbnails" in rel.parts[:-1]:
                return None
            if not _is_within(root / "thumbnails", root):
                return None
            dst = root / media_thumbnail_rel(rel)
            return path, dst, path.suffix.lower() in VIDEO_EXTENSIONS
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
            options = ThumbnailOptions("missing", AUTO_THUMBNAIL_EDGE, "medium")
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
            thumbnailer.enqueue(src, dst, is_video)
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
