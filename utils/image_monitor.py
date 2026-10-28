"""Image monitoring system for ComfyUI generated images.

This module provides real-time monitoring of ComfyUI output directories to
automatically detect newly generated images and associate them with their
corresponding prompts. The system uses filesystem watchers to detect image creation
events and extract metadata from the images to maintain a gallery system.

The main components are:
- ImageGenerationHandler: Handles filesystem events for new image creation
- ImageMonitor: Main monitoring system that manages directory watching

Typical usage:
    from utils.image_monitor import ImageMonitor

    monitor = ImageMonitor(db_manager, prompt_tracker)
    monitor.start_monitoring(['/path/to/comfyui/output'])

The system automatically:
- Detects new image files in monitored directories
- Extracts ComfyUI workflow metadata from PNG chunks
- Links images to tracked prompts only when the image's own graph proves the
  tracked entry belongs to its run (``utils.run_keys``)
- Leaves an image unlinked when nothing certain identifies its prompt (#342)
- Provides status information and monitoring control
"""

import os
import queue
import time
import threading
from collections import OrderedDict
from typing import Callable, Optional, Dict, Any, List, Tuple
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from .file_prompt import HISTORY
from .foreign_runs import (
    ANCHORED_SOURCES,
    CERTAIN,
    ForeignRecheck,
    decide_foreign,
    history_lookup,
    save_foreign_runs,
    store_foreign,
)
from .hashing import generate_prompt_hash
from .metadata_extractor import ComfyUIMetadataExtractor
from .parallel import note_refusal, refused_at_shutdown
from .logging_config import get_logger
from .prompt_graph import (
    metadata_opted_out,
    resolve_text,
    run_prompt_nodes,
    save_setting,
    side_opted_out,
)
from .prompt_pair_resolver import resolve_prompt_pair
from .run_keys import image_run_keys
from .usage_tracking import survive_switch

try:
    from ..database.pair_ingestion import ingest_resolved_pair
    from ..database.run_attribution import (
        host_output_directory,
        in_output_folder,
        stored_run_prompt,
        subscribe_outputs_sealed,
    )
except ImportError:
    from database.pair_ingestion import ingest_resolved_pair
    from database.run_attribution import (
        host_output_directory,
        in_output_folder,
        stored_run_prompt,
        subscribe_outputs_sealed,
    )

# Upper bound on images waiting to be processed (a burst beyond this is skipped)
MAX_PENDING_IMAGES = 1000
# A file counts as fully written once its size is unchanged across two reads
SETTLE_INTERVAL_SECONDS = 0.1
SETTLE_ATTEMPTS = 20
# Keyless images (no embedded API graph) left unlinked wait for their run's
# recorded outputs for one history-only re-check (#342): at most this many,
# each for at most this long.
MAX_AWAITING_HISTORY = 1000
AWAIT_HISTORY_SECONDS = 3600
# ``schedule`` snapshot meaning "history-only re-check" (not a new image)
HISTORY_ONLY = object()
# How long a stop waits for the image worker, and for the watchdog observer
# after its stop (#302). Every wait before the image in hand is interrupted
# at once, so a bound is reached only while one image is still being linked.
WORKER_JOIN_SECONDS = 5.0
OBSERVER_JOIN_SECONDS = 5.0


class ImageGenerationHandler(FileSystemEventHandler):
    """Filesystem event handler for detecting new image generation.

    This handler extends watchdog's FileSystemEventHandler to specifically handle
    new image file creation events in ComfyUI output directories. When a new image
    is detected, it attempts to:
    1. Extract ComfyUI metadata from the image
    2. Associate the image with the currently active prompt
    3. Store the relationship in the database

    The handler implements a small delay before processing to ensure files are
    completely written before attempting to read them.
    """

    def __init__(self, db_manager, prompt_tracker):
        """
        Initialize the image generation handler.

        Args:
            db_manager: Database manager instance for storing image-prompt relationships
            prompt_tracker: Prompt tracking instance for getting current active prompts
        """
        self.db_manager = db_manager
        self.prompt_tracker = prompt_tracker
        self.metadata_extractor = ComfyUIMetadataExtractor()
        self.logger = get_logger("prompt_manager.image_monitor")
        # The Needs review pass for ambiguous outputs (the API's
        # ``record_review_file``, wired at start-up): a linked output is
        # never queued and its pending items leave the queue. None: no pass.
        self.review_recorder: Optional[Callable[[str], str]] = None
        # Queues a settled output's preview (the API's
        # ``queue_capture_thumbnail``, wired at start-up; #249). It only
        # queues: the preview is written on its own worker. None: no queue.
        self.thumbnail_queue: Optional[Callable[[str], str]] = None
        # Told about every settled output, with its resolved path (the API's
        # ``notify_output_settled``, wired at start-up; #348): the Images
        # listing walks that root again. None: nobody is told.
        self.output_listener: Optional[Callable[[str], None]] = None

        # Read from GalleryConfig if available, otherwise use defaults
        try:
            from ..py.config import GalleryConfig

            self.processing_delay = GalleryConfig.PROCESSING_DELAY
            self.supported_extensions = tuple(GalleryConfig.SUPPORTED_EXTENSIONS)
        except Exception:
            self.processing_delay = 2.0
            self.supported_extensions = (".png", ".jpg", ".jpeg", ".webp", ".gif")

        # Settle check: the file size must be unchanged across two reads
        self.settle_interval = SETTLE_INTERVAL_SECONDS
        self.settle_attempts = SETTLE_ATTEMPTS

        # Images scheduled but not yet processed: path -> (prompt snapshot, due time).
        # One daemon worker processes them in order, so batch images pop the
        # prompt queue in creation order.
        self._pending: Dict[str, Tuple[Any, float]] = {}
        self._pending_lock = threading.Lock()
        # Each worker owns its queue and stop event (replaced on shutdown),
        # so a worker that is still draining never swallows a later image.
        self._work: "queue.Queue[Optional[str]]" = queue.Queue()
        self._stop = threading.Event()
        self._worker: Optional[threading.Thread] = None
        # Keyless unlinked images awaiting their run's recorded outputs:
        # normalized path -> (path, monotonic deadline), oldest first.
        self._awaiting: "OrderedDict[str, Tuple[str, float]]" = OrderedDict()
        # Sealed re-checks ``schedule`` refused (the path still pending, or the
        # pending set full): retried each time a pending image finishes.
        self._history_due: "OrderedDict[str, None]" = OrderedDict()
        # The same two sets for foreign outputs (#247), apart so a large
        # foreign run never evicts a waiting keyless file; the due set keeps
        # each file's sealed output reference.
        self._awaiting_foreign: "OrderedDict[str, Tuple[str, float]]" = OrderedDict()
        self._foreign_due: "OrderedDict[str, dict]" = OrderedDict()
        self._awaiting_lock = threading.Lock()
        self._sealed_subscribed = False
        # Set by ``close``: no new work, no new worker, ever (#302).
        self._closed = False
        # Workers a stop signalled that had not ended yet: every later stop
        # waits for them again, so it never reports True too early.
        self._finishing: List[threading.Thread] = []

    def on_created(self, event):
        """Handle filesystem creation events.

        This method is called by watchdog when a new file is created in a monitored
        directory. It filters for image files and schedules them for processing after
        a small delay to ensure the file is fully written.

        The prompt context is captured immediately (before the delay) to handle
        batch workflows where the prompt tracker advances before images are processed.

        Args:
            event: FileSystemEvent object containing event details
        """
        # Closed: return before any log or tracker lock, so watchdog's
        # ``observer.stop()`` never waits on this dispatch.
        if self._closed:
            return
        if event.is_directory or not self.is_image_file(event.src_path):
            return
        self.logger.info(f"New image detected: {event.src_path}")
        # Snapshot the tracked runs NOW, before the delay: a later run may
        # execute the same node again before this image is processed.
        self.schedule(event.src_path, self._keyed_snapshot())

    def _keyed_snapshot(self):
        """The tracker's latest entry per run key, or None when unavailable."""
        try:
            return self.prompt_tracker.keyed_snapshot()
        except Exception as error:
            self.logger.warning(f"Could not snapshot tracked prompts: {error}")
            return None

    def schedule(self, image_path: str, prompt_snapshot=None) -> bool:
        """Queue an image for processing on the worker thread, once per path.

        Images are processed in the order they were scheduled (batch images must
        pop the prompt queue in creation order), each after ``processing_delay``
        and once its size has stopped changing.

        Returns:
            False when the path is already pending, the pending set is full,
            the handler is closed or the interpreter is exiting (its worker
            cannot start)
        """
        with self._pending_lock:
            if self._closed:
                return False
            if image_path in self._pending:
                self.logger.debug(f"Already pending, not rescheduled: {image_path}")
                return False
            if len(self._pending) >= MAX_PENDING_IMAGES:
                self.logger.warning(
                    f"Too many images pending ({len(self._pending)}), "
                    f"skipping: {image_path}"
                )
                return False
            due = time.monotonic() + self.processing_delay
            self._pending[image_path] = (prompt_snapshot, due)
            try:
                self._ensure_worker()
            except RuntimeError as error:
                # No worker will ever take it: never leave it pending (#375).
                self._pending.pop(image_path, None)
                if not refused_at_shutdown(error):
                    raise
                note_refusal(self.logger, "The image worker")
                return False
            self._work.put(image_path)
        return True

    def pending_count(self) -> int:
        """Number of images scheduled but not yet processed."""
        with self._pending_lock:
            return len(self._pending)

    def shutdown(self, timeout: float = WORKER_JOIN_SECONDS) -> bool:
        """Stop the worker thread, drop every image still waiting, and wait
        for the worker within ``timeout`` seconds.

        The worker wakes from any delay or settle wait, finishes at most the
        image in hand and exits; the next ``schedule`` starts a fresh worker
        with its own queue (use ``close`` for a final stop). Safe to call
        repeatedly and before anything was scheduled.

        Returns:
            True when no worker is left running. False when the worker is
            still finishing its image after ``timeout`` (a warning is
            logged), or when called on the worker itself (it only signals).
        """
        with self._awaiting_lock:
            self._awaiting.clear()
            self._history_due.clear()
            self._awaiting_foreign.clear()
            self._foreign_due.clear()
        with self._pending_lock:
            self._pending.clear()
            worker, self._worker = self._worker, None
            work, self._work = self._work, queue.Queue()
            stop, self._stop = self._stop, threading.Event()
            if worker is not None:
                self._finishing.append(worker)
            finishing = [w for w in self._finishing if w.is_alive()]
            self._finishing = finishing
        stop.set()
        if worker is not None and worker.is_alive():
            work.put(None)  # sentinel: wake a worker idle on an empty queue
        if threading.current_thread() in finishing:
            self.logger.debug("shutdown called on the image worker itself")
            return False
        deadline = time.monotonic() + timeout
        for thread in finishing:
            thread.join(max(0.0, deadline - time.monotonic()))
        with self._pending_lock:
            self._finishing = [w for w in self._finishing if w.is_alive()]
            still_running = bool(self._finishing)
        if still_running:
            self.logger.warning(
                f"image worker still running after {timeout} s; "
                "it stops after the image in hand"
            )
            return False
        return True

    def close(self, timeout: float = WORKER_JOIN_SECONDS) -> bool:
        """Final stop: ``shutdown`` and refuse every later ``schedule``.

        After ``close`` no worker starts again, not even from a sealed run's
        history re-check on the history writer's thread. Returns what
        ``shutdown`` returns.
        """
        with self._pending_lock:
            self._closed = True
        return self.shutdown(timeout)

    def _ensure_worker(self) -> None:
        """Start the single daemon worker thread if it isn't running.

        Called with ``_pending_lock`` held.
        """
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(
                target=self._run_worker,
                args=(self._work, self._stop),
                name="PromptManagerImageWorker",
                daemon=True,
            )
            self._worker.start()

    def _run_worker(self, work: "queue.Queue[Optional[str]]", stop) -> None:
        while not stop.is_set():
            image_path = work.get()
            try:
                if image_path is None or stop.is_set():
                    break
                self._process_pending(image_path, stop)
            except Exception as e:
                self.logger.error(f"Image worker failed on {image_path}: {e}")
            finally:
                work.task_done()

    def _process_pending(self, image_path: str, stop=None) -> None:
        """Process one scheduled image; the pending entry is always cleared."""
        stop = stop if stop is not None else self._stop
        try:
            with self._pending_lock:
                prompt_snapshot, due = self._pending.get(image_path, (None, 0.0))
            remaining = due - time.monotonic()
            if remaining > 0 and stop.wait(remaining):
                return
            if not self.wait_until_settled(image_path, stop):
                if not stop.is_set():
                    self.logger.warning(
                        f"Image never settled or vanished, skipping: {image_path}"
                    )
                return
            try:
                self.process_new_image(image_path, prompt_snapshot=prompt_snapshot)
                self._queue_thumbnail(image_path)
            finally:
                # Linked or not, even if linking raised: the file exists.
                self._report_output(image_path)
        finally:
            with self._pending_lock:
                self._pending.pop(image_path, None)
            self._drain_history_due()

    def _queue_thumbnail(self, image_path: str) -> None:
        """Queue a settled output's preview, best effort (linked or not).

        A failure is logged, never raised: the preview is then made on its
        first view.
        """
        queue_preview = getattr(self, "thumbnail_queue", None)
        if queue_preview is None:
            return
        try:
            outcome = queue_preview(image_path)
        except Exception:
            self.logger.warning(
                f"Preview of {os.path.basename(image_path)} could not be queued",
                exc_info=True,
            )
            return
        self.logger.debug(f"Preview {outcome}: {os.path.basename(image_path)}")

    def _report_output(self, image_path: str) -> None:
        """Tell the output listener a settled output exists, best effort.

        The path is resolved here, on the worker thread, so the listener's
        event loop does no file I/O. A failure is logged, never raised: the
        output is then listed by the listing's 30 s refresh.
        """
        listener = getattr(self, "output_listener", None)
        if listener is None:
            return
        try:
            listener(os.path.realpath(image_path))
        except Exception:
            self.logger.warning(
                f"{os.path.basename(image_path)} could not be reported to the "
                "Images listing",
                exc_info=True,
            )

    def wait_until_settled(self, image_path: str, stop=None) -> bool:
        """True once the file's size is non-zero and unchanged across two reads.

        Returns False as soon as ``stop`` (an Event) is set.
        """
        stop = stop if stop is not None else self._stop
        previous = None
        for _ in range(self.settle_attempts):
            try:
                size = os.path.getsize(image_path)
            except OSError:
                return False
            if previous is not None and size == previous and size > 0:
                return True
            previous = size
            if stop.wait(self.settle_interval):
                return False
        return False

    def is_image_file(self, filepath: str) -> bool:
        """Check if file is a supported image format and not a thumbnail.

        Args:
            filepath: Path to the file to check

        Returns:
            True if the file has a supported image extension and is not
            inside a thumbnails directory, False otherwise
        """
        # Skip files in thumbnails directory - those are derivatives, not
        # generated images
        if "/thumbnails/" in filepath or "\\thumbnails\\" in filepath:
            return False
        return filepath.lower().endswith(self.supported_extensions)

    def process_new_image(self, image_path: str, prompt_snapshot=None):
        """Process a newly created image file for gallery integration.

        This method handles the complete processing pipeline for a new image:
        1. Verifies the file still exists
        2. Extracts ComfyUI metadata from the image
        3. Identifies the prompt from the image's metadata, else from a tracked
           run the image's graph proves (``_find_prompt_from_run``)
        4. Links the image to that prompt, or leaves it unlinked when nothing
           certain identifies it (logged at info; #342)

        Args:
            image_path: Full path to the newly created image file
            prompt_snapshot: The tracker's keyed snapshot taken when the image
                appeared (``PromptTracker.keyed_snapshot``); None reads it now
        """
        try:
            if prompt_snapshot is HISTORY_ONLY:
                self._recheck_history(image_path)
                return
            if isinstance(prompt_snapshot, ForeignRecheck):
                self._recheck_foreign(image_path, prompt_snapshot.reference)
                return
            self.logger.info(f"Processing image: {image_path}")

            if not os.path.exists(image_path):
                self.logger.warning(f"Image file no longer exists: {image_path}")
                return

            # Extract ComfyUI metadata first — needed both for linking and prompt lookup
            metadata = None
            try:
                metadata = self.metadata_extractor.extract_metadata(image_path)
                self.logger.debug(f"Extracted metadata: {bool(metadata)}")
            except Exception as meta_error:
                self.logger.warning(f"Metadata extraction failed: {meta_error}")

            # Strategy 1: The image's own metadata. It records the positive prompt
            # that produced this image, independent of node caching and of any
            # negative-prompt PromptManager nodes in the workflow.
            state, current_prompt = self._find_prompt_from_metadata(metadata)
            if state == "opted_out":
                self.logger.info(
                    "Save to library is off for this run's prompt, skipping image: "
                    f"{image_path}"
                )
                return
            if state == "foreign":
                # Another workflow saved this image: never a batch queue entry
                # of ours; its own graph may name its prompt (#247).
                self._ingest_foreign(image_path, metadata)
                return
            if current_prompt:
                self.logger.info(
                    f"Metadata match: prompt {current_prompt['id']} for "
                    f"{os.path.basename(image_path)}"
                )
            else:
                current_prompt = self._find_prompt_from_run(
                    image_path, metadata, prompt_snapshot
                )
            if not current_prompt:
                # Nothing certain: never guess from recency or a stale entry (#342)
                self.logger.info(
                    "No certain prompt for "
                    f"{os.path.basename(image_path)}; left unlinked"
                )
                return

            # Extend timeout if we have an active execution
            if current_prompt.get("execution_id"):
                self.prompt_tracker.extend_prompt_timeout(
                    current_prompt["execution_id"], 300
                )

            if metadata:
                self.logger.debug(
                    f"Linking image with full metadata to prompt {current_prompt['id']}"
                )
                self.link_image_to_prompt(image_path, current_prompt, metadata)
            else:
                self.logger.debug(
                    f"Linking image with basic info to prompt {current_prompt['id']}"
                )
                basic_metadata = self.get_basic_file_info(image_path)
                self.link_image_to_prompt(
                    image_path, current_prompt, {"file_info": basic_metadata}
                )

        except Exception as e:
            self.logger.error(f"Error processing image {image_path}: {e}")
            import traceback

            self.logger.error(traceback.format_exc())

    def _find_prompt_from_run(
        self, image_path: str, metadata, prompt_snapshot=None
    ) -> Optional[Dict[str, Any]]:
        """The prompt a tracked run proves for an image whose metadata can't say.

        Only an entry registered under the run key of the image's own positive
        node counts (``utils.run_keys``): never the newest prompt in the
        database and never an entry of another graph (#342).

        The snapshot taken when the image appeared names the key's latest run.

        1. Batch queue: the oldest queued entry of that key from that run.
           Batch items (text from PromptSearchList) share one key and are
           linked in order; an empty item's placeholder leaves its image
           unlinked; entries queued by later runs stay for their own images.
        2. The snapshot's entry itself, when its run is known and registered
           exactly one entry for the key (a batch's latest item says nothing
           about the other images). This covers a cached node that did not
           run again.
        3. Recorded execution history: the run that wrote this file names one
           stored prompt (``database.run_attribution``).
        """
        keys = image_run_keys(metadata)
        name = os.path.basename(image_path)
        if not keys:
            # No tracked run can be proven: history now, and once more after
            # the run's outputs are recorded (``_on_outputs_sealed``). A file
            # outside ComfyUI's output folder can never be attributed, so it
            # does not take a waiting slot.
            if not in_output_folder(image_path, host_output_directory()):
                return None
            self._await_history(image_path)
            prompt_id = self._history_prompt(image_path)
            if prompt_id is None:
                return None
            self._stop_awaiting(image_path)
            self.logger.info(f"History match: prompt {prompt_id} for {name}")
            return {"id": prompt_id, "from_history": True}
        if len(keys) == 1:
            (key,) = keys.values()
            if prompt_snapshot is None:
                prompt_snapshot = self._keyed_snapshot()
            # No entry of the key when the image appeared: no tracked run made it
            entry = (prompt_snapshot or {}).get(key)
            if entry is not None:
                context, run_id, count = entry
                queued = self.prompt_tracker.pop_for_key(key, run_id)
                if queued is not None:
                    if queued.get("placeholder"):
                        self.logger.info(f"Queue entry for {name} has no prompt text")
                        return None
                    self.logger.info(f"Queue match: prompt {queued['id']} for {name}")
                    return queued
                if context.get("placeholder"):
                    # Same rule as a popped placeholder: that run had no prompt
                    self.logger.info(f"Latest run for {name} had no prompt text")
                    return None
                if run_id and count == 1:
                    self.logger.info(f"Run match: prompt {context['id']} for {name}")
                    return context
        else:
            self.logger.debug(f"{len(keys)} positive prompt nodes for {name}")
        prompt_id = self._history_prompt(image_path)
        if prompt_id is None:
            return None
        self.logger.info(f"History match: prompt {prompt_id} for {name}")
        return {"id": prompt_id, "from_history": True}

    @staticmethod
    def _awaiting_key(image_path: str) -> str:
        """One spelling for a waiting file, through symlinks and junctions."""
        try:
            resolved = os.path.realpath(image_path)
        except (OSError, ValueError):
            resolved = os.path.abspath(image_path)
        return os.path.normcase(resolved)

    def _prune_awaiting(self, store=None) -> List[Tuple[str, bool]]:
        """Drop expired and surplus entries of ``store`` (the keyless set by
        default; ``_awaiting_lock`` held). Returns the dropped foreign files
        as ``(path, evicted)`` for ``_log_dropped`` after the lock."""
        store = self._awaiting if store is None else store
        now = time.monotonic()
        expired = [k for k, (_p, due) in store.items() if due <= now]
        dropped = [(store.pop(key)[0], False) for key in expired]
        while len(store) > MAX_AWAITING_HISTORY:
            dropped.append((store.popitem(last=False)[1][0], True))
        return dropped if store is self._awaiting_foreign else []

    def _log_dropped(self, dropped: List[Tuple[str, bool]]) -> None:
        """Log foreign files that stopped waiting (outside ``_awaiting_lock``)."""
        for image_path, evicted in dropped:
            name = os.path.basename(image_path)
            if evicted:
                self.logger.info(
                    f"Too many files wait for their run; {name} left unlinked"
                )
            else:
                self.logger.info(f"No recorded run for {name}; left unlinked")

    def _await_history(self, image_path: str, store=None) -> None:
        """Re-check ``image_path`` once, history only, when its run is sealed.

        ``store`` is the waiting set (``_awaiting_foreign`` for a foreign
        output). A closed handler re-checks nothing.
        """
        if self._closed:
            return
        if not self._sealed_subscribed:
            subscribe_outputs_sealed(self._on_outputs_sealed)
            self._sealed_subscribed = True
        store = self._awaiting if store is None else store
        key = self._awaiting_key(image_path)
        with self._awaiting_lock:
            store.pop(key, None)
            store[key] = (image_path, time.monotonic() + AWAIT_HISTORY_SECONDS)
            dropped = self._prune_awaiting(store)
        self._log_dropped(dropped)

    def _await_foreign(self, image_path: str) -> None:
        self._await_history(image_path, self._awaiting_foreign)

    def _stop_awaiting(self, image_path: str, store=None) -> None:
        store = self._awaiting if store is None else store
        with self._awaiting_lock:
            store.pop(self._awaiting_key(image_path), None)

    def awaiting_history(self) -> List[str]:
        """Normalized paths still waiting for their run's recorded outputs."""
        with self._awaiting_lock:
            self._prune_awaiting()
            return list(self._awaiting)

    def awaiting_foreign(self) -> List[str]:
        """Normalized paths of foreign outputs waiting for their run (#247)."""
        with self._awaiting_lock:
            dropped = self._prune_awaiting(self._awaiting_foreign)
            waiting = list(self._awaiting_foreign)
        self._log_dropped(dropped)
        return waiting

    def _on_outputs_sealed(self, run_id: str, references: List[dict]) -> None:
        """A run's outputs were recorded: re-check its waiting images once.

        Called on the history writer's thread, so it only schedules the work.
        """
        root = host_output_directory()
        if not root:
            return
        due = []
        with self._awaiting_lock:
            self._prune_awaiting()
            dropped = self._prune_awaiting(self._awaiting_foreign)
            for reference in references:
                if reference.get("folder_type") != "output":
                    continue
                key = self._awaiting_key(
                    os.path.join(
                        root, reference.get("subfolder", ""), reference["filename"]
                    )
                )
                waiting = self._awaiting.pop(key, None)
                if waiting is not None:
                    due.append((waiting[0], None))
                waiting = self._awaiting_foreign.pop(key, None)
                if waiting is not None:
                    due.append((waiting[0], dict(reference)))
        self._log_dropped(dropped)
        for image_path, reference in due:
            self._schedule_recheck(image_path, reference)

    @staticmethod
    def _recheck_snapshot(reference):
        return HISTORY_ONLY if reference is None else ForeignRecheck(reference)

    def _schedule_recheck(self, image_path: str, reference=None) -> None:
        """Schedule the history-only re-check (with the sealed ``reference``:
        a foreign output's), or keep it due for later.

        A closed handler drops it.
        """
        if self._closed:
            return
        if self.schedule(image_path, self._recheck_snapshot(reference)):
            return
        with self._awaiting_lock:
            # ``close`` sets the flag before ``shutdown`` clears this set, so
            # a close that raced the refusal above is seen here.
            if self._closed:
                return
            due = self._history_due if reference is None else self._foreign_due
            due.pop(image_path, None)
            due[image_path] = reference
            evicted = []
            while len(due) > MAX_AWAITING_HISTORY:
                evicted.append(due.popitem(last=False)[0])
        if reference is not None:
            self._log_dropped([(path, True) for path in evicted])

    def _drain_history_due(self) -> None:
        """Schedule due re-checks now that a pending slot or path is free."""
        with self._awaiting_lock:
            due = [(self._history_due, p, None) for p in self._history_due]
            due += [(self._foreign_due, *item) for item in self._foreign_due.items()]
        for store, image_path, reference in due:
            if self.schedule(image_path, self._recheck_snapshot(reference)):
                with self._awaiting_lock:
                    # A newer seal may have replaced it meanwhile: keep that one
                    if image_path in store and store[image_path] is reference:
                        del store[image_path]

    def _recheck_history(self, image_path: str) -> None:
        """The one history-only re-check of a keyless image (#342)."""
        name = os.path.basename(image_path)
        if not os.path.exists(image_path):
            return
        prompt_id = self._history_prompt(image_path)
        if prompt_id is None:
            self.logger.info(
                f"No certain prompt for {name} after its run ended; left unlinked"
            )
            return
        self.logger.info(f"History match after its run ended: {prompt_id} for {name}")
        try:
            metadata = self.metadata_extractor.extract_metadata(image_path)
        except Exception:
            metadata = None
        self.link_image_to_prompt(
            image_path,
            {"id": prompt_id, "from_history": True},
            metadata or {"file_info": self.get_basic_file_info(image_path)},
        )

    def _ingest_foreign(self, image_path: str, metadata) -> None:
        """A foreign output at settle: store its certain prompt, or wait for
        its run's recorded outputs when only history can decide (#247)."""
        name = os.path.basename(image_path)
        if not save_foreign_runs():
            self.logger.info(
                f"No PromptManager prompt in workflow, skipping image: {image_path}"
            )
            return
        in_output = in_output_folder(image_path, host_output_directory())
        decision = decide_foreign(
            image_path, metadata, in_output=in_output, lookup=None
        )
        if decision.kind == HISTORY:
            decision = self._settle_lookup(image_path, metadata)
            if decision is None:
                self.logger.debug(f"{name} waits for its run to end")
                return
            self._stop_awaiting(image_path, self._awaiting_foreign)
        if decision.kind in CERTAIN:
            self._store_foreign(image_path, decision)
        else:
            self.logger.info(f"No certain prompt for {name}; left unlinked")

    def _settle_lookup(self, image_path: str, metadata):
        """The settle-time history lookup of a file only history can decide.

        Registered first, so a run sealed before this lookup is not missed.
        Returns None to keep waiting: no record yet, and the answer is not
        the inspector's or the recorded saver's (the legacy floor waits for
        the re-check). With a record, any decision ends the wait.
        """
        self._await_foreign(image_path)
        found = []
        lookup = history_lookup(self.db_manager, image_path)

        def recorded():
            found.append(lookup())
            return found[-1]

        decision = decide_foreign(image_path, metadata, in_output=True, lookup=recorded)
        if any(record is not None for record in found):
            return decision
        if decision.kind in CERTAIN and decision.source in ANCHORED_SOURCES:
            return decision
        return None

    def _recheck_foreign(self, image_path: str, reference) -> None:
        """The one re-check of a foreign output after its run ended (#247)."""
        name = os.path.basename(image_path)
        if not os.path.exists(image_path):
            return
        if not save_foreign_runs():
            self.logger.info(f"Saving prompts from other workflows is off: {name}")
            return
        try:
            metadata = self.metadata_extractor.extract_metadata(image_path)
        except Exception as error:
            self.logger.warning(f"Metadata of {name} could not be read: {error}")
            return
        lookup = history_lookup(self.db_manager, image_path, reference)
        decision = decide_foreign(image_path, metadata, in_output=True, lookup=lookup)
        if decision.kind in CERTAIN:
            self._store_foreign(image_path, decision)
            return
        self.logger.info(
            f"No certain prompt for {name} after its run ended; left unlinked"
        )

    def _store_foreign(self, image_path: str, decision) -> None:
        name = os.path.basename(image_path)
        if not save_foreign_runs():  # switched off while this file was decided
            self.logger.info(f"Saving prompts from other workflows is off: {name}")
            return
        try:
            status, prompt_id = store_foreign(self.db_manager, image_path, decision)
        except Exception as error:
            self.logger.error(f"Failed to store the prompt of {name}: {error}")
            return
        self.logger.info(
            f"Foreign run: {status} prompt {prompt_id} for {name} ({decision.source})"
        )

    def _history_prompt(self, image_path: str) -> Optional[int]:
        """The stored prompt recorded history attributes to this file, if one."""
        try:
            return stored_run_prompt(
                self.db_manager, image_path, host_output_directory()
            )
        except Exception as error:
            self.logger.warning(f"History attribution failed: {error}")
            return None

    def _find_prompt_from_metadata(self, metadata) -> Tuple[str, Optional[Dict]]:
        """Identify the prompt that produced an image from its embedded metadata.

        Parses the ComfyUI workflow/prompt data embedded in the image to find the
        PromptManager node feeding the positive input, then matches its text
        against the database by hash.

        Args:
            metadata: Extracted metadata dict from the image, or None

        Returns:
            ``("linked", prompt)`` with a context dict holding 'id' and 'text';
            ``("foreign", None)`` when the workflow has no positive PromptManager
            node, so the image belongs to no prompt of ours;
            ``("opted_out", None)`` when a PromptManager node that would be the
            run's prompt has its ``save_to_library`` switch off (#90): the image
            is linked to no prompt, by no fallback;
            ``("unknown", None)`` when metadata can't decide (missing, a batch
            item whose text came from another node, or a lookup failure) and the
            caller's queue and tracker fallbacks apply.
        """
        if not metadata:
            return "unknown", None
        if metadata_opted_out(metadata):
            return "opted_out", None
        graph = metadata.get("prompt")
        if isinstance(graph, dict) and any(
            save_setting(graph, node_id) is None for node_id in run_prompt_nodes(graph)
        ):
            # A switch wired from a node the graph cannot read: only the node's
            # own run (it saw the real value) can link the image (#90)
            return "unknown", None

        prompt_text = None
        resolution = resolve_prompt_pair(metadata)
        if resolution["state"] == "resolved" and isinstance(graph, dict):
            positive = resolution["positive"]
            path = positive["provenance"].get("path", [])
            paired_source = any(
                isinstance(graph.get(item.get("node_id")), dict)
                and graph[item["node_id"]].get("class_type") == "PromptManagerPair"
                for item in path
            )
            if paired_source:
                try:
                    existing = self.db_manager.get_prompt_by_hash(
                        generate_prompt_hash(positive["storage_text"])
                    )
                    if existing:
                        return "linked", {
                            "id": existing["id"],
                            "text": existing["text"],
                            "from_metadata": True,
                        }
                except Exception as error:
                    self.logger.warning(f"Paired metadata lookup failed: {error}")

        # The executed graph: use the PromptManager node feeding the positive input
        prompt_data = metadata.get("prompt")
        if isinstance(prompt_data, dict):
            run_nodes = run_prompt_nodes(prompt_data)
            if not run_nodes:
                return "foreign", None
            for node_id in run_nodes:
                prompt_text = resolve_text(prompt_data, node_id)
                if prompt_text:
                    break
            if not prompt_text:
                # Positive text can't be known from the graph (batch item or unknown
                # node): leave it to the queue rather than guess from workflow widgets
                return "unknown", None

        # Fallback (no executed graph): check text_encoder_nodes from workflow, but
        # only if the text input is NOT connected (connected inputs override widget
        # values, so the widget value would be stale in batch workflows).
        if not prompt_text:
            text_nodes = metadata.get("text_encoder_nodes", [])
            if text_nodes and not any(
                "PromptManager" in (n.get("type") or n.get("class_type") or "")
                for n in text_nodes
                if isinstance(n, dict)
            ):
                return "foreign", None
            for node in text_nodes:
                node_type = node.get("type") or node.get("class_type") or ""
                if "PromptManager" in node_type:
                    # Check if text input is connected — if so, widget value is stale
                    text_connected = False
                    for inp in node.get("inputs", []):
                        if isinstance(inp, dict) and inp.get("name") == "text":
                            if inp.get("link") is not None:
                                text_connected = True
                            break
                    if text_connected:
                        self.logger.debug(
                            "PromptManager text input is connected — "
                            "skipping stale widget value, deferring to snapshot"
                        )
                        continue
                    widgets = node.get("widgets_values", [])
                    if widgets and isinstance(widgets[0], str) and widgets[0].strip():
                        prompt_text = widgets[0].strip()
                        break

        if not prompt_text:
            return "unknown", None

        # Look up by hash in database
        try:
            existing = self.db_manager.get_prompt_by_hash(
                generate_prompt_hash(prompt_text)
            )
            if existing:
                self.logger.debug(
                    f"Found DB prompt {existing['id']} from metadata text"
                )
                return "linked", {
                    "id": existing["id"],
                    "text": existing["text"],
                    "from_metadata": True,
                }
        except Exception as e:
            self.logger.warning(f"Metadata-based prompt lookup failed: {e}")

        return "unknown", None

    def get_basic_file_info(self, image_path: str) -> Dict[str, Any]:
        """Get basic file information when metadata extraction fails.

        Provides fallback file information when ComfyUI metadata cannot be extracted
        from the image. Includes file size, format, and dimensions when possible.

        Args:
            image_path: Path to the image file

        Returns:
            Dictionary containing basic file information:
            - size: File size in bytes
            - format: Image format (PNG, JPEG, etc.)
            - dimensions: Image width and height as list [width, height]
        """
        try:
            from PIL import Image

            stat = os.stat(image_path)
            file_info = {"size": stat.st_size, "format": None, "dimensions": None}

            # Try to get image dimensions
            try:
                with Image.open(image_path) as img:
                    file_info["dimensions"] = list(img.size)
                    file_info["format"] = img.format
            except Exception:
                pass

            return file_info
        except Exception as e:
            self.logger.error(f"Error getting file info: {e}")
            return {}

    def link_image_to_prompt(
        self, image_path: str, prompt_context: Dict, metadata: Dict
    ):
        """Link an image to a prompt in the database.

        Creates a database record associating the generated image with its source
        prompt, including any extracted metadata from the image file.

        Args:
            image_path: Full path to the image file
            prompt_context: Dictionary containing prompt information including ID
                and text
            metadata: Extracted metadata from the image file (workflow, parameters,
                etc.)
        """
        if metadata_opted_out(metadata):
            # Reached from a run or history: the file's own graph says no (#90)
            self.logger.info(
                f"Save to library is off for {os.path.basename(image_path)}; "
                "left unlinked"
            )
            return
        try:
            resolution = resolve_prompt_pair(metadata)
            graph = metadata.get("prompt") if isinstance(metadata, dict) else None
            # An opted-out negative is never stored: link the positive alone
            pair_allowed = not side_opted_out(graph, resolution.get("negative"))

            def link():
                # One write; run again on the moved library when a Move
                # library switched while it waited (#249 S6b).
                if resolution["state"] == "resolved" and pair_allowed:
                    positive = resolution["positive"]
                    expected = self.db_manager.get_prompt_by_hash(
                        generate_prompt_hash(
                            positive.get("storage_text", positive["text"])
                        )
                    )
                    if expected and expected["id"] == prompt_context["id"]:
                        ingest_resolved_pair(
                            self.db_manager, image_path, metadata, resolution
                        )
                        return None
                # The raising form: a failed write reaches survive_switch.
                return self.db_manager.link_image_to_prompt_or_raise(
                    prompt_id=prompt_context["id"],
                    image_path=image_path,
                    metadata=metadata,
                )

            image_id = survive_switch(self.db_manager, link)
            if image_id is None:
                return
            if image_id:  # 0: nothing new was linked (logged by the database)
                self.logger.debug(
                    f"Successfully linked image {image_id} to prompt "
                    f"{prompt_context['id']}"
                )
        except Exception as e:
            self.logger.error(f"Failed to link image to prompt: {e}")
            return
        if resolution["state"] == "ambiguous":
            self._record_review(image_path)

    def _record_review(self, image_path: str) -> None:
        """The Needs review pass for an ambiguous output, best effort.

        The output is linked by now, so it is never queued; a pending item
        an earlier scan left for its path leaves the queue. The link above
        stays whatever happens here: a failure is logged, never raised.
        """
        recorder = getattr(self, "review_recorder", None)
        if recorder is None:
            return
        try:
            outcome = recorder(image_path)
        except Exception:
            self.logger.warning(
                f"{os.path.basename(image_path)} could not be added to Needs review",
                exc_info=True,
            )
            return
        self.logger.debug(f"Needs review {outcome}: {os.path.basename(image_path)}")


class ImageMonitor:
    """Main image monitoring system for ComfyUI gallery integration.

    This class manages the overall image monitoring system, including:
    - Setting up filesystem watchers for output directories
    - Auto-detecting ComfyUI output locations
    - Managing the lifecycle of monitoring operations
    - Providing status information

    The monitor uses watchdog to efficiently watch filesystem changes and can
    monitor multiple directories simultaneously with recursive subdirectory support.
    """

    def __init__(self, db_manager, prompt_tracker):
        """
        Initialize the image monitor.

        Args:
            db_manager: Database manager instance for storing image relationships
            prompt_tracker: Prompt tracking instance for getting active prompt context
        """
        self.db_manager = db_manager
        self.prompt_tracker = prompt_tracker
        self.observer = None
        self.handler = None
        self.monitored_directories = []
        # (observer, handler) pairs a stop left running: the next stop waits
        # for them again instead of reporting True.
        self._unfinished: List[Tuple[Any, Any]] = []
        self.logger = get_logger("prompt_manager.image_monitor")
        self.review_recorder: Optional[Callable[[str], str]] = None
        self.thumbnail_queue: Optional[Callable[[str], str]] = None
        self.output_listener: Optional[Callable[[str], None]] = None

    def set_output_listener(self, listener: Optional[Callable[[str], None]]) -> None:
        """Report every settled output's resolved path to ``listener`` (None
        stops it), on the running handler and every later one (#348)."""
        self.output_listener = listener
        if self.handler is not None:
            self.handler.output_listener = listener

    def set_thumbnail_queue(
        self, queue_preview: Optional[Callable[[str], str]]
    ) -> None:
        """Queue every settled output's preview through ``queue_preview``
        (None stops it), on the running handler and every later one."""
        self.thumbnail_queue = queue_preview
        if self.handler is not None:
            self.handler.thumbnail_queue = queue_preview

    def set_review_recorder(self, recorder: Optional[Callable[[str], str]]) -> None:
        """Record ambiguous outputs through ``recorder`` (None stops recording).

        Applies to the running handler and to every handler started later.
        """
        self.review_recorder = recorder
        if self.handler is not None:
            self.handler.review_recorder = recorder

    def start_monitoring(self, output_directories: Optional[list] = None):
        """
        Start monitoring ComfyUI output directories for new images.

        Begins filesystem watching on the specified directories. If no directories
        are provided, the system will first check GalleryConfig.MONITORING_DIRECTORIES,
        then fall back to auto-detecting ComfyUI output locations.
        All monitoring is done recursively to catch images in subdirectories.

        Args:
            output_directories: List of directory paths to monitor. If None, uses
                config or auto-detection.
        """
        if self.observer:
            self.logger.warning("Image monitoring already running")
            return

        # Check config for monitoring settings
        config = self._gallery_config()
        if config is not None:
            if not config.MONITORING_ENABLED:
                self.logger.info("Image monitoring disabled in config")
                return
            # Use configured directories if set
            if not output_directories and config.MONITORING_DIRECTORIES:
                output_directories = config.MONITORING_DIRECTORIES
                self.logger.info(
                    f"Using configured monitoring directories: {output_directories}"
                )

        # Auto-detect ComfyUI output directory if still none
        if not output_directories:
            output_directories = self.detect_comfyui_output_dirs()

        valid_directories = []
        for output_dir in output_directories or []:
            if not os.path.isdir(output_dir):
                self.logger.warning(f"Directory does not exist: {output_dir}")
                continue
            # Same containment rule as the settings endpoint, so a root
            # hand-edited into config.json is never watched (and its files
            # never linked) when it lies outside ComfyUI's directories.
            validate = getattr(config, "validate_gallery_root", None)
            if callable(validate):
                ok, reason = validate(output_dir)
                if not ok:
                    self.logger.warning(f"Not watching {output_dir}: {reason}")
                    continue
            valid_directories.append(output_dir)
        if not valid_directories:
            self.logger.warning("No valid directories to monitor")
            return

        # Only a started observer becomes self.observer, so a failed start never
        # leaves the monitor stuck "already running" with nothing to stop.
        handler = ImageGenerationHandler(self.db_manager, self.prompt_tracker)
        handler.review_recorder = self.review_recorder
        handler.thumbnail_queue = self.thumbnail_queue
        handler.output_listener = self.output_listener
        observer = Observer()
        scheduled = []
        for output_dir in valid_directories:
            try:
                observer.schedule(handler, output_dir, recursive=True)
                scheduled.append(output_dir)
                self.logger.info(f"Monitoring directory (recursive): {output_dir}")
            except Exception as e:
                self.logger.warning(f"Cannot watch {output_dir}: {e}")
        if not scheduled:
            self.logger.warning("No directory could be watched")
            return
        try:
            observer.start()
        except Exception as e:
            self.logger.error(f"Image monitoring could not start: {e}")
            return

        self.handler = handler
        self.observer = observer
        self.monitored_directories = scheduled
        self.logger.info(f"Image monitoring started for {len(scheduled)} directories")

    @staticmethod
    def _gallery_config():
        """GalleryConfig when the package's config is importable, else None."""
        try:
            from ..py.config import GalleryConfig

            return GalleryConfig
        except Exception:
            return None

    @property
    def is_monitoring(self) -> bool:
        """Whether a filesystem observer is running."""
        return self.observer is not None

    def stop_monitoring(self, timeout: float = OBSERVER_JOIN_SECONDS) -> bool:
        """Stop the image monitoring system within one ``timeout`` deadline.

        Closes the handler first (its worker is joined and its dispatch
        returns at once), then stops the watchdog observer and joins it. The
        worker and the observer share the one deadline: the observer's join
        gets what the worker left, possibly 0 s.
        watchdog's ``observer.stop()`` joins its emitters without a limit;
        it counts against the deadline but is not bounded here. A part an
        earlier call left running is waited for again. Safe to call when
        monitoring never started and more than once.

        Returns:
            True when every worker and observer it stopped has ended.
        """
        deadline = time.monotonic() + timeout
        observer, self.observer = self.observer, None
        handler, self.handler = self.handler, None
        self.monitored_directories = []
        pairs, self._unfinished = self._unfinished, []
        if observer is not None:
            pairs.append((observer, handler))
        if not pairs:
            return True

        def remaining() -> float:
            return max(0.0, deadline - time.monotonic())

        for pair_observer, pair_handler in pairs:
            handler_ok = self._close_handler(pair_handler, remaining())
            observer_ok = self._stop_observer(pair_observer, remaining())
            if not (handler_ok and observer_ok):
                self._unfinished.append(
                    (
                        None if observer_ok else pair_observer,
                        None if handler_ok else pair_handler,
                    )
                )
        self.logger.debug("Image monitoring stopped")
        return not self._unfinished

    def _close_handler(self, handler, timeout: float) -> bool:
        if handler is None:
            return True
        try:
            return handler.close(timeout)
        except Exception as e:
            self.logger.warning(f"Error while stopping the image worker: {e}")
            return False

    def _stop_observer(self, observer, timeout: float) -> bool:
        """``observer.stop()`` (safe to repeat), then a join of ``timeout``
        seconds; a join that times out is logged with the time it got."""
        if observer is None:
            return True
        try:
            observer.stop()
            observer.join(timeout)
        except Exception as e:
            self.logger.warning(f"Error while stopping image monitoring: {e}")
            return False
        if observer.is_alive():
            self.logger.warning(
                f"watchdog observer still running after {timeout:.1f} s"
            )
            return False
        return True

    def detect_comfyui_output_dirs(self) -> list:
        """Auto-detect ComfyUI output directories.

        Attempts to locate ComfyUI output directories using multiple strategies:
        1. Import ComfyUI's folder_paths module to get the configured output directory
        2. Search common relative paths where ComfyUI output directories are
           typically located
        3. Verify that detected directories actually exist

        Returns:
            List of absolute paths to detected output directories
        """
        potential_dirs = []

        try:
            # Try to import ComfyUI's folder_paths
            import folder_paths

            output_dir = folder_paths.get_output_directory()
            if output_dir and os.path.exists(output_dir):
                potential_dirs.append(output_dir)
                self.logger.debug(f"Detected ComfyUI output directory: {output_dir}")
        except ImportError:
            self.logger.debug(
                "ComfyUI folder_paths not available, using fallback detection"
            )

        # Fallback: Look for common ComfyUI directory structures
        fallback_paths = [
            "output",
            "../output",
            "../../output",
            "ComfyUI/output",
            "../ComfyUI/output",
        ]

        for path in fallback_paths:
            abs_path = os.path.abspath(path)
            if os.path.exists(abs_path) and abs_path not in potential_dirs:
                potential_dirs.append(abs_path)
                self.logger.debug(f"Found output directory: {abs_path}")

        return potential_dirs

    def get_status(self) -> Dict[str, Any]:
        """Get monitoring status information.

        Returns:
            Dictionary containing:
            - running: Boolean indicating if monitoring is active
            - monitored_directories: List of currently monitored directory paths
            - handler_active: Boolean indicating if the event handler is active
            - observer_alive: Boolean indicating if observer thread is alive
        """
        observer_alive = False
        if self.observer is not None:
            try:
                observer_alive = self.observer.is_alive()
            except Exception:
                pass
        return {
            "running": self.observer is not None,
            "monitored_directories": self.monitored_directories,
            "handler_active": self.handler is not None,
            "observer_alive": observer_alive,
        }


# Singleton instance management
_monitor_instance: Optional[ImageMonitor] = None
_monitor_lock = threading.Lock()


def get_image_monitor(db_manager, prompt_tracker) -> ImageMonitor:
    """Get or create the singleton ImageMonitor instance.

    This ensures only one ImageMonitor exists across all PromptManager nodes,
    preventing duplicate image detection when multiple nodes are used.

    Args:
        db_manager: Database manager instance for storing image relationships
        prompt_tracker: Prompt tracking instance for getting active prompt context

    Returns:
        The singleton ImageMonitor instance
    """
    global _monitor_instance
    if _monitor_instance is None:
        with _monitor_lock:
            if _monitor_instance is None:
                _monitor_instance = ImageMonitor(db_manager, prompt_tracker)
    return _monitor_instance
