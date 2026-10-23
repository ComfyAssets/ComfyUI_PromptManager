"""The session job registry: every long operation as one tracked job.

A job runs as an asyncio task that outlives the HTTP request that started it,
so an accidental tab close never abandons half-finished work. Each job keeps
its counters, a capped list of file outcomes per outcome group and a capped
log, and fans its progress out to SSE subscribers: the request that started
it, a reconnecting page, a second tab. A subscriber receives the latest event
as a snapshot and then every event published after it, until the job ends.

Jobs live only in server memory and are gone after a restart. The registry
keeps the running jobs and this session's last ``MAX_FINISHED_JOBS`` finished
ones. One job of a kind runs at a time, and each job has an admission class
(``ADMISSIONS``) that decides what may run beside it:

- ``records`` changes library records or files (the metadata scan, bulk image
  tagging, and the legacy restore, maintenance, cleanup, duplicate delete,
  LoRA import and batch auto-tag routes). It is refused while any
  ``records`` or ``guarded`` job runs.
- ``guarded`` must not overlap record changes but may overlap other guarded
  work (thumbnail generation and clearing, later backup). It is refused while
  a ``records`` job runs.
- ``read`` only reads; it is refused only by a running job of its own kind.

A short legacy-page operation takes a ``hold``: a running, non-cancellable
job that lasts as long as the request, so the Activity page lists it and a
workspace job is refused while it runs (and the other way round).
"""

import asyncio
import itertools
import logging
import re
import time
import uuid
from pathlib import PurePosixPath, PureWindowsPath

logger = logging.getLogger(__name__)
# Start and finish order: wall-clock times can tie (coarse Windows clocks).
_sequence = itertools.count()

JOB_STATES = ("running", "finished", "cancelled", "failed")
FINAL_STATES = JOB_STATES[1:]
MAX_OUTCOME_ROWS = 200  # rows listed per outcome group; counters stay exact
MAX_LOG_LINES = 2000
LOG_CAP_LINE = "[Log capped at 2,000 lines]"
MAX_FINISHED_JOBS = 20  # finished jobs this session keeps

# The metadata scan's outcome groups, in counter-tile order of the spec's
# mapping, and the per-file scan status each group collects. Files without
# usable metadata have no status and go to "no_metadata".
# Which files a scan reads (#249 Activity-2): "new" skips the files the Library
# already links with a resolved link (get_known_resolved_paths) before reading
# them, so new, unlinked, unresolved and ambiguous files are read; "all" reads
# every file again. The workspace job defaults to "new"; legacy /scan is "all".
SCAN_SCOPES = ("new", "all")
DEFAULT_SCAN_SCOPE = "new"
SCAN_GROUP_NAMES = ("added", "known", "review", "no_metadata", "errors")
SCAN_GROUPS = {
    "added": "added",
    "linked": "added",
    "enriched": "added",
    "unchanged": "known",
    "skipped": "known",  # "new" scope: known and resolved, not read again
    "ambiguous": "review",
    "conflict": "review",
    "unresolved": "review",
    "failed": "errors",
}

# Bulk image tagging (#249 D1): Add tags and Auto-tag over selected images.
# Record-changing: it writes image tag sets and new vocabulary names.
IMAGE_TAG_JOB_KIND = "image-tags"
IMAGE_TAG_GROUP_NAMES = ("tagged", "unchanged", "skipped", "errors")

# Thumbnail generation (#249 System S5): previews written, already fresh, failed.
THUMBNAIL_GROUP_NAMES = ("generated", "skipped", "errors")

ADMISSIONS = ("read", "guarded", "records")
# What each admission class is refused beside (besides its own kind).
_REFUSED_BESIDE = {
    "read": frozenset(),
    "guarded": frozenset({"records"}),
    "records": frozenset({"records", "guarded"}),
}

# Kinds of the legacy-page operations that hold the registry (D80; titles in
# their handlers end in "(admin page)", "(gallery page)" or "(legacy page)").
RESTORE_JOB_KIND = "restore"
MAINTENANCE_JOB_KIND = "maintenance"
DUPLICATE_CLEANUP_JOB_KIND = "duplicate_cleanup"
THUMBNAILS_JOB_KIND = "thumbnails"
LORA_IMPORT_JOB_KIND = "lora_import"
AUTOTAG_BATCH_JOB_KIND = "autotag"
# The System health check (py/api/system.py): a read-only ``read`` job with
# no file outcomes, so it may run beside a scan.
HEALTH_JOB_KIND = "health"


def resolve_admission(admission=None, record_changing=None) -> str:
    """The admission class for a start call; ``record_changing`` is an alias.

    ``record_changing=True`` means ``records`` and ``False`` means ``read``;
    with neither given the class is ``read``. Raises ValueError for an
    unknown class or one that contradicts ``record_changing``.
    """
    if admission is None:
        return "records" if record_changing else "read"
    if admission not in ADMISSIONS:
        raise ValueError(f"Unknown job admission {admission!r}")
    if record_changing is not None and record_changing != (admission == "records"):
        raise ValueError(
            f"record_changing={record_changing!r} contradicts admission {admission!r}"
        )
    return admission


def _relative_path(path) -> str:
    """``path`` as a root-relative POSIX path; an absolute one keeps its name."""
    text = str(path or "")
    if (
        PurePosixPath(text).is_absolute()
        or PureWindowsPath(text).is_absolute()
        or PureWindowsPath(text).drive
    ):
        return re.split(r"[\\/]", text)[-1]
    return text.replace("\\", "/")


class JobBusy(Exception):
    """A job could not start because ``running`` holds its slot."""

    def __init__(self, running):
        super().__init__(f"{running.title} is already running")
        self.running = running


class Job:
    """One tracked operation: state, counters, outcomes, log and subscribers."""

    def __init__(
        self,
        kind,
        title,
        *,
        admission=None,
        record_changing=None,
        cancellable=True,
        scope=None,
        groups=(),
        clock=None,
    ):
        self._clock = clock or time.time
        self.id = uuid.uuid4().hex
        self.kind = kind
        self.title = title
        self.admission = resolve_admission(admission, record_changing)
        self.cancellable = bool(cancellable)
        self.scope = dict(scope or {})
        self.state = "running"
        self.started_at = self._clock()
        self._started_seq = next(_sequence)
        self._finished_seq = None
        self.finished_at = None
        self.progress = 0
        self.status_line = ""
        self.processed = 0
        self.total = 0
        self.counters = dict.fromkeys(groups, 0)
        self.outcomes = {group: [] for group in groups}
        self.log = []
        self.cancelled = False
        self.last_event = None
        self.task = None
        self._subscribers = set()

    @property
    def record_changing(self):
        return self.admission == "records"

    @property
    def running(self):
        return self.state == "running"

    @property
    def finished(self):
        return not self.running

    @property
    def subscriber_count(self):
        return len(self._subscribers)

    # ── progress ──────────────────────────────────────────────────────

    def update(self, *, progress=None, status_line=None, processed=None, total=None):
        """Set the summary's progress fields; ``None`` leaves one unchanged."""
        if progress is not None:
            self.progress = max(0, min(100, int(progress)))
        if status_line is not None:
            self.status_line = status_line
        if processed is not None:
            self.processed = processed
        if total is not None:
            self.total = total

    def publish(self, payload):
        """Record ``payload`` as the latest event and hand it to every subscriber."""
        if isinstance(payload.get("progress"), (int, float)):
            self.update(progress=payload["progress"])
        self.last_event = payload
        for queue in list(self._subscribers):
            queue.put_nowait(payload)

    def log_line(self, text):
        """Append a timestamped log line, up to MAX_LOG_LINES and one cap line."""
        if len(self.log) > MAX_LOG_LINES:
            return
        if len(self.log) == MAX_LOG_LINES:
            self.log.append(LOG_CAP_LINE)
            return
        stamp = time.strftime("%H:%M:%S", time.localtime(self._clock()))
        self.log.append(f"{stamp} {text}")

    def record(self, group, row, log_line):
        """Count one file outcome in ``group``, list its row and log it.

        ``row`` is ``{path, root, root_index, detail}``: the path relative to
        its output root, the root's public label and its listing index (the
        identity Images opens it by; None when unknown). An absolute path is
        cut to its file name, so a row never reveals the server's layout.
        """
        if group not in self.counters:
            raise ValueError(f"Unknown outcome group {group!r} for {self.kind}")
        self.counters[group] += 1
        raw = str(row.get("path") or "")
        path = _relative_path(raw)
        if raw and raw != path:
            log_line = log_line.replace(raw, path)
        rows = self.outcomes[group]
        index = row.get("root_index")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            index = None
        if len(rows) < MAX_OUTCOME_ROWS:
            rows.append(
                {
                    "path": path,
                    "root": str(row.get("root") or ""),
                    "root_index": index,
                    "detail": str(row.get("detail") or ""),
                }
            )
        self.log_line(log_line)

    def record_counted(self, group, count, rows=()):
        """Count ``count`` outcomes in ``group`` at once, listing ``rows``.

        For outcomes that need no log line each (a thumbnail job's already
        fresh previews): the counter is exact, ``rows`` (``record``'s row
        shape) are listed up to MAX_OUTCOME_ROWS, and nothing is logged.
        """
        if group not in self.counters:
            raise ValueError(f"Unknown outcome group {group!r} for {self.kind}")
        self.counters[group] += max(0, int(count))
        listed = self.outcomes[group]
        for row in rows:
            if len(listed) >= MAX_OUTCOME_ROWS:
                break
            index = row.get("root_index")
            if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                index = None
            listed.append(
                {
                    "path": _relative_path(row.get("path")),
                    "root": str(row.get("root") or ""),
                    "root_index": index,
                    "detail": str(row.get("detail") or ""),
                }
            )

    def finish(self, state="finished"):
        """End the job in ``state`` and release every subscriber.

        Each subscriber still gets the events queued before the release. A
        second finish is a no-op, so the first final state stands.
        """
        if state not in FINAL_STATES:
            raise ValueError(f"{state!r} is not a final job state")
        if self.finished:
            return
        self.state = state
        self.finished_at = self._clock()
        self._finished_seq = next(_sequence)
        for queue in list(self._subscribers):
            queue.put_nowait(None)

    async def events(self):
        """Yield the latest event, then each later one, until the job finishes.

        Consume it under ``contextlib.aclosing`` so a consumer that stops early
        (a closed socket) drops its queue at once instead of at garbage collection.
        """
        queue = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            if self.last_event is not None:
                yield self.last_event
            if self.finished:
                return
            while True:
                payload = await queue.get()
                if payload is None:
                    return
                yield payload
        finally:
            self._subscribers.discard(queue)

    # ── views ─────────────────────────────────────────────────────────

    def summary(self):
        """The job as the Activity list and pill show it."""
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "state": self.state,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "progress": self.progress,
            "status_line": self.status_line,
            "processed": self.processed,
            "total": self.total,
            "counters": dict(self.counters),
            "scope": dict(self.scope),
            # What detail() lists, so a view re-reads it only when it changed.
            "listed": {group: len(rows) for group, rows in self.outcomes.items()},
            "log_length": len(self.log),
            # A view offers Cancel only when the server can stop the job.
            "cancellable": self.cancellable,
            "admission": self.admission,
        }

    def detail(self):
        """The summary plus the listed outcome rows and the log."""
        return {
            **self.summary(),
            "outcomes": {
                group: [dict(row) for row in rows]
                for group, rows in self.outcomes.items()
            },
            "log": list(self.log),
        }

    def report(self):
        """The downloadable report: the summary, every outcome group and the log.

        Each group carries its exact count, its listed rows (at most
        MAX_OUTCOME_ROWS) and whether the list is cut short. Rows and log lines
        already hold root-relative paths only.
        """
        summary = self.summary()
        summary.pop("listed", None)
        summary.pop("log_length", None)
        return {
            "report_version": 1,
            "generated_at": self._clock(),
            "job": summary,
            "outcomes": {
                group: {
                    "count": self.counters.get(group, len(rows)),
                    "rows": [dict(row) for row in rows],
                    "truncated": self.counters.get(group, len(rows)) > len(rows),
                }
                for group, rows in self.outcomes.items()
            },
            "log": list(self.log),
        }

    def report_filename(self):
        """The download name: prompt-manager-KIND-YYYYMMDD-HHMM.json (local time)."""
        stamp = time.strftime("%Y%m%d-%H%M", time.localtime(self._clock()))
        return f"prompt-manager-{self.kind}-{stamp}.json"


class JobRegistry:
    """Every job of this server session, running and recently finished."""

    def __init__(self, logger_=None):
        self._logger = logger_ or logger
        self._jobs = {}

    def start(
        self,
        kind,
        title,
        *,
        admission=None,
        record_changing=None,
        cancellable=True,
        scope=None,
        runner,
        groups=(),
    ) -> Job:
        """Create a job and run ``runner(job)`` as a task.

        ``admission`` is one of ``ADMISSIONS``; ``record_changing=True`` is
        the older spelling of ``records`` and ``False`` of ``read``. One of
        the two is required (TypeError otherwise), so no new job runs as
        ``read`` beside a scan by omission. Raises JobBusy, naming the job in
        the way, when the admission class refuses it (see the module
        docstring). Must be called from the event loop.
        """
        if admission is None and record_changing is None:
            raise TypeError(
                f"JobRegistry.start({kind!r}) needs an admission class "
                "(admission= or record_changing=)"
            )
        job = self._admit(
            kind,
            title,
            admission=admission,
            record_changing=record_changing,
            cancellable=cancellable,
            scope=scope,
            groups=groups,
        )
        job.task = asyncio.ensure_future(self._run(job, runner))
        return job

    def hold(self, kind, title, *, admission, scope=None):
        """A running, non-cancellable job for a legacy-page operation.

        Returns an async context manager. Entering it registers the job and
        yields it, or raises JobBusy at once, without waiting, when the
        admission class refuses it; nothing is registered until then, so a
        hold that is never entered leaves no job behind. The job ends when
        the block exits: ``finished``, or ``failed`` when it raised (unless
        the body already ended it). A caller that answers a refusal in its
        own way checks ``blocker(kind, admission=...)`` first, with no await
        before the ``async with``.
        """
        resolve_admission(admission)  # an unknown class fails at the call
        return _Hold(
            lambda: self._admit(
                kind, title, admission=admission, cancellable=False, scope=scope
            )
        )

    def _admit(self, kind, title, *, admission, record_changing=None, **job_args):
        resolved = resolve_admission(admission, record_changing)
        blocker = self.blocker(kind, admission=resolved)
        if blocker is not None:
            raise JobBusy(blocker)
        self._prune()
        job = Job(kind, title, admission=resolved, **job_args)
        self._jobs[job.id] = job
        return job

    def blocker(self, kind, *, admission=None, record_changing=None):
        """The running job a new ``kind`` job would be refused for, or None.

        The same kind refuses first; then the oldest running job whose class
        the new job's admission class may not run beside.
        """
        same_kind = self.active(kind)
        if same_kind is not None:
            return same_kind
        refused = _REFUSED_BESIDE[resolve_admission(admission, record_changing)]
        # A snapshot: the monitor's worker thread asks too (Needs review).
        running = [job for job in tuple(self._jobs.values()) if job.running]
        running.sort(key=lambda job: job._started_seq)
        for job in running:
            if job.admission in refused:
                return job
        return None

    async def _run(self, job, runner):
        try:
            await runner(job)
        except asyncio.CancelledError:
            job.finish("cancelled")
            raise
        except Exception:
            self._logger.exception(f"Job {job.kind} failed")
            job.finish("failed")
        finally:
            if job.running:
                job.finish("cancelled" if job.cancelled else "finished")

    def _prune(self):
        """Drop the oldest finished jobs so a new one keeps the cap."""
        finished = [job for job in self._jobs.values() if job.finished]
        finished.sort(key=lambda job: job._finished_seq)
        for job in finished[: max(0, len(finished) - MAX_FINISHED_JOBS + 1)]:
            del self._jobs[job.id]

    def get(self, job_id):
        return self._jobs.get(job_id)

    def active(self, kind):
        """The running job of ``kind``, or None (safe off the event loop)."""
        for job in tuple(self._jobs.values()):
            if job.kind == kind and job.running:
                return job
        return None

    def list(self):
        """Running jobs first, then the last finished ones, newest first."""
        jobs = list(self._jobs.values())
        running = [job for job in jobs if job.running]
        finished = [job for job in jobs if job.finished]
        running.sort(key=lambda job: job._started_seq, reverse=True)
        finished.sort(key=lambda job: job._finished_seq, reverse=True)
        return running + finished[:MAX_FINISHED_JOBS]

    def cancel(self, job_id) -> bool:
        """Ask a running job to stop at its next safe boundary.

        Returns whether a cancellation was requested: False for a finished,
        unknown or non-cancellable job, so a cancel after the end is a no-op.
        """
        job = self._jobs.get(job_id)
        if job is None or job.finished or not job.cancellable:
            return False
        job.cancelled = True
        return True


class _Hold:
    """The async context manager ``JobRegistry.hold`` returns."""

    def __init__(self, admit):
        self._admit = admit
        self._job = None

    async def __aenter__(self):
        self._job = self._admit()
        return self._job

    async def __aexit__(self, exc_type, exc, tb):
        if self._job.running:
            self._job.finish("finished" if exc_type is None else "failed")
        return False
