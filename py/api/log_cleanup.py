"""Reviewed rotated-log cleanup as a maintenance plan (#329 System S12).

``POST /prompt_manager/maintenance/preview {operation: "rotated_logs",
options: {}}`` lists the rotated copies of the active log that Rule R allows
(``utils/logging_config.py``: ``prompt_manager.log.<n>`` regular files that
are not links, directly in the log folder) and writes nothing. The plan keeps
each copy's name, size, ``mtime_ns`` and inode under its token; the response
is the maintenance ``Plan`` shape with basenames only, a ``none`` backup
policy (no library record changes) and no ``disk`` key.

``POST /prompt_manager/maintenance/apply {token}`` starts a ``maintenance``
job titled "Rotated log copies" with admission ``read``: it may run beside a
scan and is refused only beside another maintenance job or an ``exclusive``
one. The job deletes exactly the reviewed copies under the logger's files
lock and the file handler's lock, or nothing, failing ``plan-changed``, when
any of them is missing, is no longer a rotated copy or differs in size,
``mtime_ns`` or inode. A copy that cannot be deleted (open in another program
on Windows) is an ``errors`` row; the others are deleted.
"""

import datetime
import json

from aiohttp import web

try:
    from ...utils.logging_config import RotatedChanged, rotated_sort_key
except ImportError:  # loaded as the top-level ``py`` package (tests)
    from utils.logging_config import RotatedChanged, rotated_sort_key

from .jobs import MAINTENANCE_JOB_KIND
from .maintenance_plans import ScopeTooLarge

ROTATED_LOGS = "rotated_logs"
LOG_CLEANUP_TITLE = "Rotated log copies"
LOGS_CHANGED = (
    "The log files changed since the review; nothing was deleted. Review again."
)
LOG_CLEANUP_FAILED = "The log cleanup failed."
TOO_MANY_LOG_FILES = "Too many log files to review at once."
LOG_CLEANUP_MEDIA = (
    "Deletes only the listed log files. No image, video or library record changes."
)
_GROUPS = ("changed", "protected", "errors")


def size_label(size):
    """``1000 bytes``, ``2.0 KB``, ``10.0 MB`` or ``1.2 GB`` (1 KB = 1024 bytes)."""
    size = max(0, int(size or 0))
    if size < 1024:
        return f"{size} {'byte' if size == 1 else 'bytes'}"
    for unit, scale in (("KB", 2**10), ("MB", 2**20)):
        if size < scale * 1024:
            return f"{size / scale:.1f} {unit}"
    return f"{size / 2**30:.1f} GB"


def time_label(mtime_ns):
    """The local modification time as ``YYYY-MM-DD HH:MM``."""
    return datetime.datetime.fromtimestamp(mtime_ns / 1e9).strftime("%Y-%m-%d %H:%M")


def _count(count, singular, plural):
    return f"{count} {singular if count == 1 else plural}"


def _protected_reasons(rows):
    """The plan's Protected reasons: what the cleanup keeps, zero counts left out."""
    active = sum(1 for row in rows if row["kind"] == "active")
    links = sum(1 for row in rows if row["kind"] == "link")
    other = sum(1 for row in rows if row["kind"] == "other")
    reasons = []
    if active:
        reasons.append("Kept: the active log")
    if links:
        reasons.append(f"Kept: {_count(links, 'link', 'links')}")
    if other:
        reasons.append(f"Kept: {_count(other, 'other file', 'other files')}")
    return active + links + other, reasons


def _scope_label(files):
    if not files:
        return "No rotated log copies"
    total = sum(row["size"] for row in files)
    return (
        f"{_count(len(files), 'rotated log copy', 'rotated log copies')}"
        f" · {size_label(total)}"
    )


class LogCleanupMixin:
    """The ``rotated_logs`` maintenance plan and its job.

    The logger manager is reached only through ``_get_logger_manager``
    (``logging_routes.py``), so the tests can point it at a temporary folder.
    """

    async def preview_log_cleanup(self):
        """The ``rotated_logs`` plan (runs inside the preview's lease)."""
        from . import maintenance

        manager = self._get_logger_manager()
        try:
            rows = await self._run_in_executor(manager.scan_log_folder)
        except Exception:
            self.logger.exception("Rotated log review failed")
            return maintenance._failure("Failed to review maintenance", 500)
        rotated = sorted(
            (row for row in rows if row["kind"] == "rotated"),
            key=rotated_sort_key,
        )
        files = [
            {
                "name": row["name"],
                "size": row["size"],
                "mtime_ns": row["mtime_ns"],
                "ino": row["ino"],
            }
            for row in rotated
        ]
        protected, reasons = _protected_reasons(rows)
        store = self._maintenance_plans
        try:
            token = store.issue(
                ROTATED_LOGS,
                json.dumps(files, sort_keys=True),
                {"files": files, "protected": protected},
                (),
            )
        except ScopeTooLarge:
            return maintenance._failure(TOO_MANY_LOG_FILES, 413, "too-large")
        plan = store.peek(token, ROTATED_LOGS)
        return web.json_response(
            {
                "success": True,
                "token": token,
                "operation": ROTATED_LOGS,
                "expires_at": maintenance._iso(plan.expires_at),
                "scope": {"count": len(files), "label": _scope_label(files)},
                "protected": {"count": protected, "reasons": reasons},
                "backup": {
                    "policy": "none",
                    "destination_label": None,
                    "expected_bytes": None,
                },
                "media": {"deletes_files": True, "label": LOG_CLEANUP_MEDIA},
                "details": [
                    {
                        "label": row["name"],
                        "detail": (
                            f"{size_label(row['size'])} · {time_label(row['mtime_ns'])}"
                        ),
                    }
                    for row in files
                ],
            },
            headers={"Cache-Control": "no-store"},
        )

    def _start_log_cleanup(self, plan):
        """Register the cleanup job (on the loop); raises JobBusy when refused."""
        return self._jobs.start(
            MAINTENANCE_JOB_KIND,
            LOG_CLEANUP_TITLE,
            admission="read",
            cancellable=False,
            scope={"operation": ROTATED_LOGS},
            groups=_GROUPS,
            runner=lambda job: self._run_log_cleanup(job, plan),
        )

    async def _run_log_cleanup(self, job, plan):
        from . import maintenance

        files = plan.payload["files"]
        sizes = {row["name"]: row["size"] for row in files}
        job.update(total=len(files), status_line="Deleting rotated log copies")
        manager = self._get_logger_manager()
        try:
            result = await self._run_in_executor(manager.delete_rotated, files)
        except RotatedChanged:
            return maintenance._fail_job(job, "plan-changed", LOGS_CHANGED)
        except Exception:
            self.logger.exception("Rotated log cleanup failed")
            return maintenance._fail_job(job, None, LOG_CLEANUP_FAILED)
        for name in result["removed"]:
            detail = f"Deleted · {size_label(sizes.get(name, 0))}"
            job.record(
                "changed", {"path": name, "detail": detail}, f"- {name} · {detail}"
            )
        for name, error in result["failed"]:
            detail = self._public_error(error)
            job.record(
                "errors", {"path": name, "detail": detail}, f"! {name} · {detail}"
            )
        job.record_counted("protected", plan.payload["protected"])
        removed, failed = len(result["removed"]), len(result["failed"])
        line = f"Deleted {_count(removed, 'rotated log copy', 'rotated log copies')}"
        if failed:
            line += f", {failed} could not be deleted"
        job.log_line(line)
        job.update(processed=len(files), progress=100, status_line=line)
