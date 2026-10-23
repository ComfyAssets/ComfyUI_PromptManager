"""Needs review recording for the output scan and the live monitor (#249).

The scan hands every file the ingestion resolver finds ambiguous to
``_record_scanned_review`` (best effort: a failure is logged and counted);
the monitor calls ``record_review_file`` from its worker thread after its
fallback link. Both go through ``utils.review_capture.record_ambiguous_file``
on the API's capture store. Nothing here links a file.

Recording changes records, so it follows the registry's ``records``
admission (#289). The scan records under its own ``records`` job. The
monitor's recording lasts one file and is not a job: it is refused while a
``records`` or ``guarded`` job runs (``deferred``), and the next scan reads
the ambiguous file again.
"""

import os
from pathlib import Path

from .admin import SCAN_LOG_PREFIXES, _plural, _scan_location, _scan_roots

# The kind the monitor's recording checks admission under (never registered).
REVIEW_RECORDING_KIND = "review_recording"

try:
    from ...utils.review_capture import record_ambiguous_file
except ImportError:
    from utils.review_capture import record_ambiguous_file


class ReviewRecordingMixin:
    """Record the scan's and the monitor's ambiguous files for Needs review."""

    def _record_scanned_review(self, media_file, roots, review=None):
        """Record one ambiguous scanned file; a failure is logged and counted."""
        try:
            outcome = self._record_review_sync(media_file, roots)
        except Exception:
            self.logger.warning(
                f"{media_file.name} could not be added to Needs review",
                exc_info=True,
            )
            if review is not None:
                review["errors"] += 1
            return
        if review is not None and outcome in ("recorded", "limited"):
            review["recorded"] += 1

    def _record_review_sync(self, media_file, roots):
        """``record_ambiguous_file`` for a file under one of ``roots`` (blocking)."""
        relative, _label, index = _scan_location(media_file, roots)
        root = next((r for r, _l, i in roots if i == index), None)
        if index is None or root is None:
            return "skipped"
        return record_ambiguous_file(
            self.db, self._capture_store(), Path(root), index, relative
        )

    def record_review_file(self, image_path):
        """Needs review recording for the live monitor (its worker thread).

        Maps ``image_path`` to the gallery root that lists it (the listing's
        ``root_index``); a file under no current root is ``skipped``. While a
        job runs that a ``records`` job may not run beside, nothing is recorded
        and the answer is ``deferred``. Errors propagate: the monitor logs them
        and keeps its link.
        """
        busy = self._jobs.blocker(REVIEW_RECORDING_KIND, admission="records")
        if busy is not None:
            self.logger.info(
                f"{os.path.basename(str(image_path))} was not added to Needs "
                f"review while {busy.title} runs; the next scan reads it again"
            )
            return "deferred"
        full = os.path.abspath(str(image_path))
        located = Path(os.path.realpath(os.path.dirname(full))) / os.path.basename(full)
        return self._record_review_sync(
            located, _scan_roots(self._get_all_output_dirs())
        )

    @staticmethod
    def _log_review_items(job, review):
        """The scan log's Needs review summary lines (none when nothing moved)."""
        if review["recorded"]:
            job.log_line(
                f"{SCAN_LOG_PREFIXES['review']} "
                f"{_plural(review['recorded'], 'image')} added to Needs review"
            )
        if review["errors"]:
            job.log_line(
                f"{SCAN_LOG_PREFIXES['errors']} "
                f"{_plural(review['errors'], 'image')} could not be added to "
                "Needs review"
            )
