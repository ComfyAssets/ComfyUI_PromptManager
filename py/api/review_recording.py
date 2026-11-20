"""Needs review recording for the output scan and the live monitor (#249).

The rule (maintainer test of #284/#288): a file is queued only when its
final outcome is ambiguous and unlinked. The strict resolver must find it
``ambiguous``, the scan's legacy fallback (the browser parser,
``graph_prompt_text``) must not link it, and no saved row may link it
already. A file the fallback links, an exact pair the ingester stored and an
already linked file are never queued. Neither is an ambiguous file with
nothing to choose: fewer than two of its candidates carry a prompt
(``utils.review_capture.reviewable``); the scan reports it unresolved ("could
not be traced") unless the inspection profile finds a choice in it. A file
the default profile calls unresolved gets the same inspection-profile chance
(#659 C1). ``review_dispatch`` is that one per-file decision.

The monitor queues what the scan would (maintainer decision 2026-11-19,
#659; it replaces the 2026-10-23 ruling that only scans add items). At a
file's final unlinked outcome, after run history had its say, the monitor
hands the path to ``queue_review_file``. One review worker thread runs
``record_review_file`` for it: the scan's own per-file steps for that file
(the ladder under the scan policy with the sealed history, the recorded-run
check), read only, then ``review_dispatch``. It never links a file, and a
file the scan would link is left to the next scan. The queue coalesces by
path and is bounded; a file deferred by a job blocker is kept and replayed
once no blocker remains.

The queue heals itself: a pending item whose file is now linked (by the
fallback, a later resolution or the monitor) is removed by the next scan
that lists the file or by the monitor's next pass over it. The next scan
also removes a pending item without a choice whose file is still ambiguous
without one (``settle_untraced_review_items``), and an older version's
item of bytes now untraced (``settle_stale_review_items``). Only an undecided item goes
(``pending``, revision 0, no decision row); a decision is never deleted or
changed (``settle_linked_review_items``).

The scan records through ``_record_scanned_review`` (best effort: a failure
is logged and counted), which goes through
``utils.review_capture.record_ambiguous_file`` on the API's capture store;
the review worker calls ``record_review_file``, also for an output the
monitor linked (its pending items leave the queue). Nothing here links a
file.

Recording changes records, so it follows the registry's ``records``
admission (#289). The scan records under its own ``records`` job. The
monitor's pass lasts one file and is not a job: it is refused while a
``records`` or ``guarded`` job runs (``deferred``); the review worker keeps
the path (at most ``REVIEW_DEFERRED_LIMIT``, in memory) and replays it once
the job registry reports no blocker. Thumbnail work (the job and the
gallery's generate and clear holds, kind ``thumbnails``) is ``guarded`` but
only writes and removes preview files under ``thumbnails/`` and never touches
the database, so it does not defer the recording (#249 S5 M7): a long
rebuild no longer holds Needs review back for its whole run.
"""

import functools
import hashlib
import os
import threading
from collections import OrderedDict
from pathlib import Path

from .admin import (
    SCAN_LOG_PREFIXES,
    _BatchHistory,
    _file_size,
    _plural,
    _scan_location,
    _scan_roots,
)
from .jobs import THUMBNAILS_JOB_KIND

# The kind the monitor's recording checks admission under (never registered).
REVIEW_RECORDING_KIND = "review_recording"
# Guarded kinds that change no records, so the recording may run beside them.
PREVIEW_ONLY_KINDS = (THUMBNAILS_JOB_KIND,)
# record_ambiguous_file outcomes that leave the file in the pending queue.
QUEUED_OUTCOMES = ("recorded", "limited", "duplicate")
NEW_OUTCOMES = ("recorded", "limited")

try:
    from ...utils import review_capture
    from ...utils.file_prompt import NEVER, Policy, decide_file_prompt
    from ...utils.parallel import note_refusal, refused_at_shutdown
    from ...utils.review_capture import record_ambiguous_file
except ImportError:
    from utils import review_capture
    from utils.file_prompt import NEVER, Policy, decide_file_prompt
    from utils.parallel import note_refusal, refused_at_shutdown
    from utils.review_capture import record_ambiguous_file

# The review worker (#659 C5): its thread's name, the bound of paths waiting
# for it (the oldest is dropped beyond it), the bound of paths a job blocker
# deferred, how often it asks the job registry again while paths are
# deferred, and how long the package teardown waits for it.
REVIEW_WORKER_NAME = "pm-review-recorder"
REVIEW_QUEUE_LIMIT = 4096
REVIEW_DEFERRED_LIMIT = 1000
REVIEW_RETRY_SECONDS = 5
REVIEW_STOP_SECONDS = 5
_REVIEW_WORKER_LOCK = threading.Lock()
# Guards the untraced memory, which the scan and the review worker share.
_UNTRACED_MEMORY_LOCK = threading.Lock()

# Untraced files remembered per process so a rescan of an unchanged file does
# not run the inspection profile again (oldest forgotten first). A plain dict
# of 16-byte digests (the locator, and its size, mtime_ns and item version):
# measured at 8.6 MiB with 65,536 entries (tracemalloc, CPython 3.14).
MAX_UNTRACED_MEMORY = 65_536


def _digest(text: str) -> bytes:
    data = text.encode("utf-8", "surrogatepass")
    return hashlib.blake2b(data, digest_size=16).digest()


def _memory_key(locator: str) -> bytes:
    """A 16-byte digest of ``locator`` (the untraced memory's key)."""
    return _digest(locator)


# The scan log's reason for items that leave the queue with nothing to choose.
NOTHING_TO_CHOOSE = "nothing to choose"


class ReviewRecordingMixin:
    """Record the scan's and the monitor's ambiguous files for Needs review."""

    def _record_scanned_review(self, media_file, roots, review=None, scanned=None):
        """Record one ambiguous, unlinked scanned file (logged and counted).

        ``scanned`` is the scan's ``(metadata, inspection_resolution)``; the
        resolution is None when the scan did not run the inspection profile.
        Returns the record outcome, None when recording failed.
        """
        try:
            outcome = self._record_review_sync(
                media_file, roots, review, inspection=True, scanned=scanned
            )
        except Exception:
            self.logger.warning(
                f"{media_file.name} could not be added to Needs review",
                exc_info=True,
            )
            if review is not None:
                review["errors"] += 1
            return None
        if review is not None:
            if outcome in NEW_OUTCOMES:
                review["added"] += 1
            if outcome in QUEUED_OUTCOMES:
                review["queued"] += 1
        return outcome

    def _record_review_sync(
        self, media_file, roots, review=None, inspection=False, scanned=None
    ):
        """``record_ambiguous_file`` for a file under one of ``roots`` (blocking).

        ``inspection`` (the scan only) lets a file without a choice under the
        default profile be recorded from the inspection profile's choices.
        An ``untraced`` file (its captured bytes offer nothing to choose) also
        loses its pending items that offer nothing either.
        """
        located = self._review_location(media_file, roots)
        if located is None:
            return "skipped"
        root, index, relative, _spelled = located
        outcome = record_ambiguous_file(
            self.db,
            self._capture_store(),
            Path(root),
            index,
            relative,
            inspection=inspection,
            scanned=scanned,
        )
        if outcome == "untraced":
            self._settle_untraced_reviews([media_file], roots, review)
        return outcome

    def review_dispatch(self, media_file, roots, decision, outcome, review=None):
        """The one per-file Needs review decision (#659): ``(scan_status,
        record_outcome)`` for a file the caller stored nothing for in this
        pass.

        ``decision`` is the scan-policy ladder's ``Decision`` (None: nothing
        to decide) and ``outcome`` the file's scan status. An ambiguous file
        with a choice is recorded unless a saved row links it
        (``unchanged``). An untraced file, or one the default profile calls
        unresolved that has a prompt graph, gets the inspection profile's
        choice and the saver anchor (``_untraced_scan_outcome``). Any other
        file keeps its status and records nothing (``record_outcome`` None).
        The scan uses ``scan_status``, the review worker ``record_outcome``.
        """
        if decision is None:
            return outcome, None
        if decision.review == "ambiguous" and outcome == "ambiguous":
            if self._review_file_linked(media_file, roots):
                return "unchanged", None  # already linked: not a review case
            # The records name the item versions that count; the saver
            # anchor (#390) is checked on the captured bytes.
            scanned = (decision.canonical, None)
            recorded = self._record_scanned_review(
                media_file, roots, review, scanned=scanned
            )
            return outcome, recorded
        untraced = decision.review == "untraced"
        unreadable = (
            decision.review is None
            and decision.default_unresolved
            and bool(decision.canonical.get("prompt"))
        )
        if outcome == "unresolved" and (untraced or unreadable):
            # The inspection profile may still offer a choice (queued for
            # review, never linked).
            return self._untraced_scan_outcome(
                media_file, roots, decision.canonical, review
            )
        return outcome, None

    def _untraced_scan_outcome(self, media_file, roots, canonical, review=None):
        """``(scan_status, record_outcome)`` of an unlinked file the default
        profile left ambiguous without a choice, or unresolved (blocking).

        ``ambiguous`` when the inspection profile offers a choice: the file is
        recorded for review from that profile (never linked), or ``unchanged``
        when a saved row links it already. Otherwise ``unresolved``
        (``untraced``), and its pending items without a choice leave the
        queue. The profile runs at
        most once per file state: not for bytes already queued (or decided)
        at the file's current size and ``mtime_ns`` under the version that
        its records depend on, and not again for a state it already found
        untraced in this process.
        """
        try:
            choice = self._inspection_choice(media_file, roots, canonical)
        except Exception:
            # One bad file never stops the scan: it stays "could not be traced".
            self.logger.warning(
                f"{media_file.name} could not be inspected for Needs review",
                exc_info=True,
            )
            if review is not None:
                review["errors"] += 1
            return "unresolved", None
        if choice is None:
            self._settle_untraced_reviews([media_file], roots, review)
            self._settle_stale_reviews(media_file, roots, canonical, review)
            return "unresolved", "untraced"
        if self._review_file_linked(media_file, roots):
            return "unchanged", None
        recorded = self._record_scanned_review(
            media_file, roots, review, scanned=choice
        )
        return "ambiguous", recorded

    def _inspection_choice(self, media_file, roots, canonical):
        """``(canonical, resolution_or_None)`` when the file offers a choice.

        The resolution is None when the file's current state is already in
        the queue (so the profile did not run). None when it offers none.
        """
        if canonical is None:
            return None
        version = review_capture.inspection_review_version(canonical)
        located = self._review_location(media_file, roots)
        if version is None or located is None:
            return None
        locator = os.path.normcase(located[3])
        try:
            facts = os.lstat(located[3])
        except OSError:
            facts = None
        state = None
        if facts is not None:
            state = _digest(f"{facts.st_size}:{facts.st_mtime_ns}:{version}")
            if self.db.review_decided_at(
                locator, facts.st_size, facts.st_mtime_ns
            ) or self.db.review_fingerprint_queued(
                locator, facts.st_size, facts.st_mtime_ns, version
            ):
                return canonical, None
        memory = self._untraced_memory()
        key = _memory_key(locator)
        # The scan and the review worker (#659) share the memory: every
        # read and change holds its lock (the resolve below does not).
        with _UNTRACED_MEMORY_LOCK:
            if state is not None and memory.get(key) == state:
                return None
        resolution = review_capture.inspection_choice(canonical, media_file.name)
        with _UNTRACED_MEMORY_LOCK:
            if resolution is not None:
                memory.pop(key, None)
                return canonical, resolution
            if state is not None:
                memory.pop(key, None)  # re-inserted last: the newest state
                memory[key] = state
                while len(memory) > MAX_UNTRACED_MEMORY:
                    del memory[next(iter(memory))]
        return None

    def _settle_stale_reviews(self, media_file, roots, canonical, review=None):
        """Remove the pending, undecided items of an untraced file's current
        bytes recorded under another version (best effort, counted in
        ``review["untraced"]``).

        The current resolver and table offer nothing to choose in these
        bytes, so an item of an older version would only refuse Apply.
        """
        located = self._review_location(media_file, roots)
        if located is None or canonical is None:
            return 0
        # An unusable semantics table says nothing about the current
        # versions; never let it retire items a working table recorded.
        if review_capture.inspection_review_version(canonical) is None:
            return 0
        try:
            facts = os.lstat(located[3])
            removed = self.db.settle_stale_review_items(
                os.path.normcase(located[3]),
                facts.st_size,
                facts.st_mtime_ns,
                review_capture.item_versions(canonical),
            )
        except Exception:
            self.logger.warning(
                f"Stale Needs review items of {media_file.name} could not be "
                "removed",
                exc_info=True,
            )
            if review is not None:
                review["errors"] += 1
            return 0
        if review is not None:
            review["untraced"] += removed
        return removed

    def _untraced_memory(self):
        with _UNTRACED_MEMORY_LOCK:
            memory = getattr(self, "_untraced_inspections", None)
            if memory is None:
                memory = self._untraced_inspections = {}
            return memory

    @staticmethod
    def _review_location(media_file, roots, real_roots=None):
        """``(root, root_index, relative, spelled)`` of a listed file, or None.

        ``spelled`` is the capture's spelling of the file (the real root joined
        with the relative path); ``os.path.normcase`` of it is the locator
        review items are stored under. ``real_roots`` caches each root's
        ``realpath`` across a batch.
        """
        relative, _label, index = _scan_location(media_file, roots)
        root = next((r for r, _l, i in roots if i == index), None)
        if index is None or root is None:
            return None
        if real_roots is None:
            real_roots = {}
        real = real_roots.get(index)
        if real is None:
            real = real_roots[index] = os.path.realpath(os.fspath(root))
        spelled = os.path.join(real, *relative.split("/"))
        return root, index, relative, spelled

    def _review_file_linked(self, media_file, roots):
        """True when a saved row links the scanned file (any spelling)."""
        located = self._review_location(media_file, roots)
        spellings = (str(media_file),) + ((located[3],) if located else ())
        return self.db.review_file_linked(spellings)

    def _settle_scanned_reviews(self, media_files, roots, review=None):
        """Remove the pending items of ``media_files`` that are now linked.

        Blocking; best effort like recording (a failure is logged and
        counted in ``review["errors"]``). Returns how many items left.
        """
        real_roots = {}
        files = []
        for media_file in media_files:
            located = self._review_location(media_file, roots, real_roots)
            if located is not None:
                spelled = located[3]
                files.append((os.path.normcase(spelled), (str(media_file), spelled)))
        try:
            settled = self.db.settle_linked_review_items(files) if files else 0
        except Exception:
            self.logger.warning(
                "Needs review items of linked files could not be removed",
                exc_info=True,
            )
            if review is not None:
                review["errors"] += 1
            return 0
        if review is not None:
            review["settled"] += settled
        return settled

    def _settle_untraced_reviews(self, media_files, roots, review=None):
        """Remove the pending items without a choice of untraced ``media_files``.

        ``media_files`` are files found ambiguous with fewer than two
        candidates that carry a prompt. Blocking; best effort like the linked
        settle (a failure is logged and counted in ``review["errors"]``).
        Returns how many items left.
        """
        locators = []
        for media_file in media_files:
            located = self._review_location(media_file, roots)
            if located is not None:
                locators.append(os.path.normcase(located[3]))
        try:
            removed = self.db.settle_untraced_review_items(locators) if locators else 0
        except Exception:
            self.logger.warning(
                "Needs review items without a choice could not be removed",
                exc_info=True,
            )
            if review is not None:
                review["errors"] += 1
            return 0
        if review is not None:
            review["untraced"] += removed
        return removed

    def record_review_file(self, image_path):
        """Apply the Needs review rule to one monitor output (review worker).

        The monitor queues what the scan would (maintainer decision
        2026-11-19, #659): it hands a file over at its final unlinked
        outcome, after run history had its say, and after each ambiguous
        output it links. Maps ``image_path`` to the gallery root that lists
        it; a file under no current root is ``skipped``. A linked file's
        pending items are removed and the answer is ``linked``. Any other
        file runs the scan's own per-file steps, read only
        (``_review_unlinked_file``): a file the scan would link, or that
        does not save, is ``skipped`` (the next scan links it); otherwise
        the answer is ``review_dispatch``'s record outcome (``recorded``,
        ``duplicate``, ``decided``, ``untraced``, ``limited``) or ``skipped``.
        While a job runs that a ``records`` job may not run beside, other
        than thumbnail work (``PREVIEW_ONLY_KINDS``), nothing is written and
        the answer is ``deferred`` (the worker replays it). Errors propagate:
        the worker logs them and the monitor keeps its link.
        """
        busy = self._jobs.blocker(
            REVIEW_RECORDING_KIND, admission="records", compatible=PREVIEW_ONLY_KINDS
        )
        if busy is not None:
            self.logger.info(
                f"Needs review was not updated for "
                f"{os.path.basename(str(image_path))} while {busy.title} runs; "
                "the next scan reads it again"
            )
            return "deferred"
        full = os.path.abspath(str(image_path))
        located = Path(os.path.realpath(os.path.dirname(full))) / os.path.basename(full)
        roots = _scan_roots(self._get_all_output_dirs())
        if self._review_location(located, roots) is None:
            return "skipped"
        if self._review_file_linked(located, roots):
            self._settle_scanned_reviews([located], roots)
            return "linked"
        return self._review_unlinked_file(located, roots)

    def _review_unlinked_file(self, media_file, roots):
        """The scan's per-file steps for one unlinked file, read only.

        The metadata read the scan uses, the ladder under ``Policy.SCAN``
        with the file's recorded history, then the recorded-run check; a
        file any step would link (or whose prompt does not save) is
        ``skipped``. Otherwise ``review_dispatch`` decides, and its record
        outcome is the answer (``skipped`` when it records nothing).
        """
        path = str(media_file)
        metadata = self._extract_comfyui_metadata(path)
        history = _BatchHistory(self, [media_file])
        decision = decide_file_prompt(
            metadata,
            file_name=media_file.name,
            file_size=functools.partial(_file_size, media_file),
            in_output=False,  # only the monitor policy reads it
            lookup=functools.partial(history.recorded, path),
            policy=Policy.SCAN,
        )
        if decision.kind != NEVER:
            return "skipped"  # it saves nothing, or the next scan links it
        if decision.try_recorded_run and history.get(path) is not None:
            return "skipped"  # the scan's recorded-run step links it
        _status, recorded = self.review_dispatch(
            media_file, roots, decision, decision.unlinked_status
        )
        return recorded or "skipped"

    def _review_worker(self):
        """The API's one review worker, made on first use (no thread yet)."""
        worker = getattr(self, "_review_recorder_worker", None)
        if worker is None:
            with _REVIEW_WORKER_LOCK:
                worker = getattr(self, "_review_recorder_worker", None)
                if worker is None:
                    worker = ReviewWorker(
                        lambda path: self.record_review_file(path),
                        self._review_admitted,
                        self.logger,
                    )
                    self._review_recorder_worker = worker
        return worker

    def _review_admitted(self):
        """True while no job blocks Needs review writes (any thread)."""
        busy = self._jobs.blocker(
            REVIEW_RECORDING_KIND, admission="records", compatible=PREVIEW_ONLY_KINDS
        )
        return busy is None

    def queue_review_file(self, image_path):
        """Queue ``image_path`` for the review worker; never blocks.

        The monitor's review recorder (#659). Returns ``queued``, or
        ``stopped`` once the worker was stopped (the next scan reads it).
        """
        return self._review_worker().queue(str(image_path))

    def review_worker_started(self):
        """Whether the review worker's thread was ever started."""
        worker = getattr(self, "_review_recorder_worker", None)
        return worker is not None and worker.started

    def stop_review_worker(self, timeout=REVIEW_STOP_SECONDS):
        """Stop the review worker for good and join it within ``timeout``.

        Queued and deferred paths are dropped (their count is logged; the
        next scan covers them) and later ``queue_review_file`` calls are
        refused. True when no worker thread is left running.
        """
        return self._review_worker().stop(timeout)

    @staticmethod
    def _log_review_items(job, review):
        """The scan log's Needs review summary lines (none when nothing moved)."""
        prefix = SCAN_LOG_PREFIXES["review"]
        if review["added"]:
            job.log_line(
                f"{prefix} {_plural(review['added'], 'image')} added to Needs review"
            )
        already = review["queued"] - review["added"]
        if already:
            job.log_line(
                f"{prefix} {_plural(already, 'image')} already in Needs review"
            )
        if review["settled"]:
            job.log_line(
                f"{SCAN_LOG_PREFIXES['known']} "
                f"{_plural(review['settled'], 'image')} left Needs review · now linked"
            )
        if review["untraced"]:
            job.log_line(
                f"{SCAN_LOG_PREFIXES['known']} "
                f"{_plural(review['untraced'], 'image')} left Needs review · "
                f"{NOTHING_TO_CHOOSE}"
            )
        if review["errors"]:
            job.log_line(
                f"{SCAN_LOG_PREFIXES['errors']} "
                f"{_plural(review['errors'], 'image')} could not be added to "
                "Needs review"
            )


class ReviewWorker:
    """One daemon thread that runs ``record(path)`` for queued paths (#659 C5).

    The queue coalesces by path (a path queued again while it waits keeps
    its place; one queued while it runs runs once more afterwards) and holds
    at most ``REVIEW_QUEUE_LIMIT`` paths, dropping the oldest with a log
    line, so ``queue`` never blocks the monitor's image worker. One thread
    means two recordings of one path never run at once. A ``deferred``
    answer keeps the path in a bounded, insertion-ordered set; the thread
    waits on its queue with a ``REVIEW_RETRY_SECONDS`` timeout and, on each
    wake, replays that set once ``admitted()`` (no job blocker) says so.
    Everything lives in memory: a restart drops it and the next scan covers
    the files. A failure of ``record`` is logged and the worker goes on.
    """

    def __init__(self, record, admitted, logger):
        self._record = record
        self._admitted = admitted
        self._logger = logger
        self._changed = threading.Condition()
        self._pending = OrderedDict()  # key -> path
        self._deferred = OrderedDict()  # key -> path
        self._in_hand = None
        self._thread = None
        self._stopped = False

    @property
    def started(self):
        return self._thread is not None

    @staticmethod
    def _key(path):
        return os.path.normcase(os.path.abspath(path))

    def queue(self, path):
        """Queue ``path``: ``queued``, or ``stopped`` after ``stop``."""
        key = self._key(path)
        with self._changed:
            if self._stopped:
                return "stopped"
            if key in self._pending:
                return "queued"
            self._pending[key] = path
            evicted = None
            if len(self._pending) > REVIEW_QUEUE_LIMIT:
                evicted = self._pending.popitem(last=False)[1]
            if self._thread is None and not self._start():
                self._pending.pop(key, None)
                return "stopped"
            self._changed.notify_all()
        if evicted is not None:
            self._logger.info(
                "Too many outputs wait for Needs review; "
                f"{os.path.basename(evicted)} is left to the next scan"
            )
        return "queued"

    def _start(self):
        """Start the thread (``_changed`` held); False when refused at exit."""
        thread = threading.Thread(
            target=self._run, name=REVIEW_WORKER_NAME, daemon=True
        )
        try:
            thread.start()
        except RuntimeError as error:
            if not refused_at_shutdown(error):
                raise
            note_refusal(self._logger, "The review worker")
            self._stopped = True
            return False
        self._thread = thread
        return True

    def pending_count(self):
        with self._changed:
            return len(self._pending)

    def deferred_count(self):
        with self._changed:
            return len(self._deferred)

    def deferred_paths(self):
        with self._changed:
            return list(self._deferred.values())

    def wait_idle(self, timeout):
        """True once no path waits or is in hand (deferred ones may stay)."""
        with self._changed:
            return self._changed.wait_for(
                lambda: not self._pending and self._in_hand is None, timeout
            )

    def _run(self):
        while True:
            with self._changed:
                path = self._next_locked()
                if path is None:
                    return
                self._in_hand = path
            outcome = None
            try:
                outcome = self._run_one(path)
            finally:
                with self._changed:
                    self._in_hand = None
                    if outcome == "deferred":
                        dropped = self._defer_locked(path)
                    else:
                        dropped = None
                    self._changed.notify_all()
            if dropped is not None:
                self._logger.info(
                    "Too many outputs wait for a job to end before Needs review; "
                    f"{os.path.basename(dropped)} is left to the next scan"
                )

    def _next_locked(self):
        """The next path to record, waiting for one; None once stopped."""
        while not self._stopped:
            if self._deferred and self._replay_admitted():
                for key, path in self._deferred.items():
                    self._pending.setdefault(key, path)
                self._deferred.clear()
            if self._pending:
                return self._pending.popitem(last=False)[1]
            self._changed.notify_all()  # idle: ``wait_idle`` may return
            # Deferred paths: ask the job registry again after a while
            self._changed.wait(REVIEW_RETRY_SECONDS if self._deferred else None)
        return None

    def _replay_admitted(self):
        try:
            return bool(self._admitted())
        except Exception:
            self._logger.debug("Job registry check failed", exc_info=True)
            return False

    def _run_one(self, path):
        try:
            outcome = self._record(path)
        except Exception:
            self._logger.warning(
                f"{os.path.basename(path)} could not be added to Needs review",
                exc_info=True,
            )
            return None
        self._logger.debug(f"Needs review {outcome}: {os.path.basename(path)}")
        return outcome

    def _defer_locked(self, path):
        """Keep a deferred path; returns the path dropped at the cap."""
        if self._stopped:
            return None
        key = self._key(path)
        self._deferred.pop(key, None)
        self._deferred[key] = path
        if len(self._deferred) > REVIEW_DEFERRED_LIMIT:
            return self._deferred.popitem(last=False)[1]
        return None

    def stop(self, timeout):
        """Stop for good, drop waiting paths, join within ``timeout``.

        True when no thread is left running. Called on the worker's own
        thread, it only signals (False).
        """
        with self._changed:
            self._stopped = True
            dropped = len(self._pending) + len(self._deferred)
            self._pending.clear()
            self._deferred.clear()
            thread = self._thread
            self._changed.notify_all()
        if dropped:
            self._logger.info(
                f"{_plural(dropped, 'output')} left for the next scan's Needs review"
            )
        if thread is None:
            return True
        if thread is threading.current_thread():
            return False
        thread.join(max(0.0, timeout))
        if thread.is_alive():
            self._logger.warning(
                f"review worker still running after {timeout} s; "
                "it stops after the file in hand"
            )
            return False
        return True
