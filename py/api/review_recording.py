"""Needs review recording for the output scan and the live monitor (#249).

The rule (maintainer test of #284/#288): a file is queued only when its
final outcome is ambiguous and unlinked. The strict resolver must find it
``ambiguous``, the scan's legacy fallback (the browser parser,
``_graph_prompt_text``) must not link it, and no saved row may link it
already. A file the fallback links, an exact pair the ingester stored and an
already linked file are never queued. Neither is an ambiguous file with
nothing to choose: fewer than two of its candidates carry a prompt
(``utils.review_capture.reviewable``); the scan reports it unresolved ("could
not be traced") unless the inspection profile finds a choice in it.

Only scans add items (maintainer ruling, 2026-10-23). The monitor's pass
records nothing: it only removes the pending items of an output it linked.

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
the monitor calls ``record_review_file`` from its worker thread after its
own link. Nothing here links a file.

Recording changes records, so it follows the registry's ``records``
admission (#289). The scan records under its own ``records`` job. The
monitor's pass lasts one file and is not a job: it is refused while a
``records`` or ``guarded`` job runs (``deferred``), and the next scan reads
the file again. Thumbnail work (the job and the gallery's generate and clear
holds, kind ``thumbnails``) is ``guarded`` but only writes and removes preview
files under ``thumbnails/`` and never touches the database, so it does not
defer the recording (#249 S5 M7): a long rebuild no longer holds Needs review
back for its whole run.
"""

import hashlib
import os
from pathlib import Path

from .admin import SCAN_LOG_PREFIXES, _plural, _scan_location, _scan_roots
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
    from ...utils.review_capture import record_ambiguous_file
except ImportError:
    from utils import review_capture
    from utils.review_capture import record_ambiguous_file

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

        ``scanned`` is the scan's ``(metadata, inspection_resolution)``.
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
            return
        if review is None:
            return
        if outcome in NEW_OUTCOMES:
            review["added"] += 1
        if outcome in QUEUED_OUTCOMES:
            review["queued"] += 1

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

    def _untraced_scan_outcome(self, media_file, roots, canonical, review=None):
        """The scan status of an unlinked file the default profile left
        ambiguous without a choice (blocking).

        ``ambiguous`` when the inspection profile offers a choice: the file is
        recorded for review from that profile (never linked), or ``unchanged``
        when a saved row links it already. Otherwise ``unresolved``, and its
        pending items without a choice leave the queue. The profile runs at
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
            return "unresolved"
        if choice is None:
            self._settle_untraced_reviews([media_file], roots, review)
            self._settle_stale_reviews(media_file, roots, canonical, review)
            return "unresolved"
        if self._review_file_linked(media_file, roots):
            return "unchanged"
        self._record_scanned_review(media_file, roots, review, scanned=choice)
        return "ambiguous"

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
        if state is not None and memory.get(key) == state:
            return None
        resolution = review_capture.inspection_choice(canonical, media_file.name)
        if resolution is not None:
            memory.pop(key, None)
            return canonical, resolution
        if state is not None:
            memory.pop(key, None)  # re-inserted last: the newest state
            memory[key] = state
            while len(memory) > MAX_UNTRACED_MEMORY:
                del memory[next(iter(memory))]
        return None

    def _saver_anchored_review(self, media_file, roots, canonical, review=None):
        """True when the saver that wrote a default-ambiguous file resolves
        its branch (#390), so the file is not queued (blocking).

        Its current bytes' pending, undecided items then leave the queue
        (counted in ``review["untraced"]``: nothing to choose). A failure is
        logged and counted, and the file is queued as before.
        """
        try:
            if not review_capture.saver_anchored(canonical, media_file.name):
                return False
        except Exception:
            self.logger.warning(
                f"{media_file.name} could not be inspected for Needs review",
                exc_info=True,
            )
            if review is not None:
                review["errors"] += 1
            return False
        located = self._review_location(media_file, roots)
        if located is None:
            return True
        try:
            facts = os.lstat(located[3])
            # No version is current for these bytes: any pending item goes.
            removed = self.db.settle_stale_review_items(
                os.path.normcase(located[3]), facts.st_size, facts.st_mtime_ns, ()
            )
        except Exception:
            self.logger.warning(
                f"Needs review items of {media_file.name} could not be removed",
                exc_info=True,
            )
            if review is not None:
                review["errors"] += 1
            return True
        if review is not None:
            review["untraced"] += removed
        return True

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
        """Apply the Needs review rule to one monitor output (its worker thread).

        Only scans add items (maintainer ruling, 2026-10-23): the monitor
        only clears the items of outputs it links. Maps ``image_path`` to the
        gallery root that lists it; a file under no current root is
        ``skipped``. A linked file's pending items are removed and the answer
        is ``linked``. Any other file is ``skipped``: nothing is captured or
        recorded, and the next scan reads it. While a job runs that a
        ``records`` job may not run beside, other than thumbnail work
        (``PREVIEW_ONLY_KINDS``), nothing is written and the answer is
        ``deferred``. Errors propagate: the monitor logs them and keeps its
        link.
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
        return "skipped"

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
