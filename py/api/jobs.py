"""The session job registry: every long operation as one tracked job.

A job runs as an asyncio task that outlives the HTTP request that started it,
so an accidental tab close never abandons half-finished work. Each job keeps
its counters, a capped list of file outcomes per outcome group and a capped
log, and fans its progress out to SSE subscribers: the request that started
it, a reconnecting page, a second tab. A subscriber receives the latest event
as a snapshot and then every event published after it, until the job ends.

Jobs live only in server memory and are gone after a restart. The registry
keeps the running jobs and this session's last ``MAX_FINISHED_JOBS`` finished
ones. Record-changing jobs (the metadata scan, and later maintenance, cleanup
and restore) never run together, and one job of a kind runs at a time.
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
SCAN_GROUP_NAMES = ("added", "known", "review", "no_metadata", "errors")
SCAN_GROUPS = {
    "added": "added",
    "linked": "added",
    "enriched": "added",
    "unchanged": "known",
    "ambiguous": "review",
    "conflict": "review",
    "unresolved": "review",
    "failed": "errors",
}


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
        self, kind, title, *, record_changing=False, scope=None, groups=(), clock=None
    ):
        self._clock = clock or time.time
        self.id = uuid.uuid4().hex
        self.kind = kind
        self.title = title
        self.record_changing = record_changing
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


class JobRegistry:
    """Every job of this server session, running and recently finished."""

    def __init__(self, logger_=None):
        self._logger = logger_ or logger
        self._jobs = {}

    def start(self, kind, title, *, record_changing, scope, runner, groups=()) -> Job:
        """Create a job and run ``runner(job)`` as a task.

        Raises JobBusy, naming the job in the way, when a job of ``kind`` is
        running or when ``record_changing`` and another record-changing job
        runs. Must be called from the event loop.
        """
        same_kind = self.active(kind)
        if same_kind is not None:
            raise JobBusy(same_kind)
        if record_changing:
            for job in self._jobs.values():
                if job.running and job.record_changing:
                    raise JobBusy(job)
        self._prune()
        job = Job(
            kind, title, record_changing=record_changing, scope=scope, groups=groups
        )
        self._jobs[job.id] = job
        job.task = asyncio.ensure_future(self._run(job, runner))
        return job

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
        """The running job of ``kind``, or None."""
        for job in self._jobs.values():
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

        Returns whether a cancellation was requested: False for a finished or
        unknown job, so a cancel after the end is a no-op.
        """
        job = self._jobs.get(job_id)
        if job is None or job.finished:
            return False
        job.cancelled = True
        return True
