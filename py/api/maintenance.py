"""Maintenance routes: reviewed, read-only plans (#249 System S6).

``POST /prompt_manager/maintenance/preview {operation, options:{}}`` reviews
one operation (``duplicate_prompts``, ``optimize``, ``unused_components`` or
``missing_links``; ``database/maintenance.py``) off the event loop and
answers a ``Plan``: ``{success, token, operation, expires_at, scope:{count,
label}, protected:{count, reasons}, backup:{policy, destination_label},
media:{deletes_files, label}, details:[≤50 {label, detail}]}``. The preview
writes nothing. The plan's fingerprint, its targets and the sources it judged
stay in the server's ``PlanStore`` under the token, bound to the database the
preview read (a restore that replaces it invalidates the plan).

The operation enum is strict, ``options`` must be an empty object and the
body is capped at ``PREVIEW_BODY_MAX`` bytes. Errors are ``{success:false,
code, error}`` with ``invalid-request`` (400), ``too-large`` (413: the body,
or a plan over the store's budget), ``storage-changed`` (409: the database
was replaced while the preview read it) or a null code (500); none carries a
server path or exception text.

``POST /prompt_manager/maintenance/apply {token}`` applies a reviewed plan as
an Activity job (202 ``{job}``): kind ``maintenance``, titled after the
operation, admission ``records`` (refused while a scan, restore, LoRA import
or other record-changing job runs, and refusing them meanwhile), not
cancellable, with outcome groups ``changed``, ``protected`` and ``errors``.
The token is consumed when the job starts (on the same loop turn, before its
runner runs); a refused start keeps it. The runner:

1. takes a safety backup when the plan was reviewed with the policy on or the
   policy (``maintenance.safety_backup``, default on) is on now: SQLite's
   backup API into a temp file beside the database, ``verify_database_file``,
   fsync, ``0o600`` on POSIX, then ``os.replace`` onto a unique
   ``<db>.backup_<YYYYmmdd_HHMMSS_ffffff>[_n]`` name. Any failure removes
   what it wrote and ends the job ``backup-failed`` with nothing changed;
2. applies the plan, which first rechecks the reviewed fingerprint inside
   ``BEGIN IMMEDIATE`` (``plan-changed`` writes nothing);
3. counts the changed and protected records, and prunes old safety backups.

The job log names the backup by its file name only, and the job says "No
safety backup" when none was taken. Apply errors: ``invalid-request`` (400),
``plan-expired`` (410: unknown, expired or forgotten after a restart),
``plan-used`` (409, with the job that applied it), ``storage-changed`` (409:
a restore replaced the database since the review) and ``busy`` (409).
"""

import datetime
import os
import sqlite3
import tempfile
from contextlib import closing

from aiohttp import web

from .admin import _prune_safety_backups, _scan_location, _scan_roots
from .job_routes import busy_response
from .jobs import MAINTENANCE_JOB_KIND, JobBusy
from .maintenance_plans import PlanExpired, PlanUsed, ScopeTooLarge

try:
    from ...database.maintenance import MAINTENANCE_OPERATIONS, PlanChanged
except ImportError:
    from database.maintenance import MAINTENANCE_OPERATIONS, PlanChanged

PREVIEW_BODY_MAX = 4096
# The concept's Maintenance row titles, which name each operation's job.
MAINTENANCE_TITLES = {
    "duplicate_prompts": "Duplicate prompt text",
    "optimize": "Optimize database",
    "unused_components": "Unused prompt components",
    "missing_links": "Missing-file links",
}
_OPERATION_ERROR = "operation must be one of: " + ", ".join(MAINTENANCE_OPERATIONS)
MAINTENANCE_GROUPS = ("changed", "protected", "errors")
MAX_TOKEN_LENGTH = 128
BACKUP_FAILED = (
    "The safety backup could not be taken and verified; nothing was changed."
)
PLAN_CHANGED = (
    "The library changed since the review; nothing was changed. "
    "Review the plan again."
)
APPLY_FAILED = "Maintenance failed; nothing was changed."
NO_BACKUP = "No safety backup"
_NAME_ATTEMPTS = 1000


class SafetyBackupFailed(Exception):
    """The safety backup could not be written, verified or put in place."""


def _now():
    return datetime.datetime.now()


def _changed_line(operation, count):
    if operation == "optimize":
        return "Optimized the database"
    if operation == "duplicate_prompts":
        noun = "duplicate prompt" if count == 1 else "duplicate prompts"
        return f"Merged {count:,} {noun}"
    if operation == "unused_components":
        noun = "unused prompt component" if count == 1 else "unused prompt components"
        return f"Removed {count:,} {noun}"
    noun = "link to a missing file" if count == 1 else "links to missing files"
    return f"Removed {count:,} {noun}"


def _remove_quietly(*paths):
    for path in paths:
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError:
            pass


def _reserve_backup_name(db_path):
    """Create and return an unused ``<db>.backup_<stamp>[_n]`` (O_EXCL)."""
    base = f"{db_path}.backup_{_now().strftime('%Y%m%d_%H%M%S_%f')}"
    for attempt in range(_NAME_ATTEMPTS):
        candidate = base if attempt == 0 else f"{base}_{attempt}"
        try:
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        os.close(fd)
        return candidate
    raise SafetyBackupFailed("No free backup name")


def _standalone(path):
    """Make the copy one self-contained file (rollback journal, not WAL).

    The backup API copies the live database's WAL flag; a WAL file opened
    read-only later (a verify, a restore preview) would leave ``-wal`` and
    ``-shm`` sidecars beside it.
    """
    try:
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("PRAGMA journal_mode=DELETE").fetchone()
    except sqlite3.Error as exc:
        raise SafetyBackupFailed("The backup could not be finished") from exc


def _fsync(path):
    with open(path, "rb+") as handle:
        os.fsync(handle.fileno())


def take_safety_backup(model):
    """Write and verify a safety backup; return its file name (blocking).

    The copy is made in a temp file beside the database and only renamed
    onto its final, unique name once it verified, so a backup file on disk
    is always a verified one. Raises SafetyBackupFailed, leaving no file.
    """
    db_path = os.path.abspath(model.db_path)
    fd, temp = tempfile.mkstemp(
        dir=os.path.dirname(db_path), prefix=".pm-safety-", suffix=".tmp"
    )
    os.close(fd)
    final = None
    try:
        if not model.backup_database(temp):
            raise SafetyBackupFailed("The backup could not be written")
        _standalone(temp)
        ok, _reason = model.verify_database_file(temp)
        if not ok:
            raise SafetyBackupFailed("The backup did not verify")
        _fsync(temp)
        if os.name != "nt":
            os.chmod(temp, 0o600)
        final = _reserve_backup_name(db_path)
        os.replace(temp, final)
        return os.path.basename(final)
    except OSError as exc:
        _remove_quietly(final)
        raise SafetyBackupFailed("The backup could not be put in place") from exc
    except SafetyBackupFailed:
        _remove_quietly(final)
        raise
    finally:
        _remove_quietly(temp, temp + "-journal", temp + "-wal", temp + "-shm")


def _failure(message, status, code=None, **extra):
    return web.json_response(
        {"success": False, "code": code, "error": message, **extra}, status=status
    )


def _invalid(message):
    return _failure(message, 400, "invalid-request")


def _iso(timestamp):
    return datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc).isoformat()


class MaintenanceRoutesMixin:
    """Mixin providing the /prompt_manager/maintenance plan endpoints."""

    def _register_maintenance_routes(self, routes):
        @routes.post("/prompt_manager/maintenance/preview")
        async def maintenance_preview_route(request):
            return await self.preview_maintenance(request)

        @routes.post("/prompt_manager/maintenance/apply")
        async def maintenance_apply_route(request):
            return await self.apply_maintenance_plan(request)

    def _database_identity(self, db):
        """What binds a plan to one database: the swap generation and its file."""
        path = os.path.normcase(os.path.realpath(db.model.db_path))
        return (self._db_generation, path)

    @staticmethod
    def _safety_backup_policy():
        """``on`` or ``off``: whether an apply takes a safety backup first."""
        from ..config import PromptManagerConfig

        return "on" if PromptManagerConfig.SAFETY_BACKUP else "off"

    async def _read_maintenance_body(self, request, allowed):
        """``(data, None)`` for a capped JSON object with only ``allowed`` keys."""
        data, error = await self._read_json_body(request, max_bytes=PREVIEW_BODY_MAX)
        if error is not None:
            if error.status == 413:
                return None, _failure("Request body too large", 413, "too-large")
            return None, _invalid("Request body must be a JSON object")
        unknown = sorted(set(data) - set(allowed))
        if unknown:
            return None, _invalid("Unknown field: " + ", ".join(unknown)[:200])
        return data, None

    async def preview_maintenance(self, request):
        data, error = await self._read_maintenance_body(
            request, ("operation", "options")
        )
        if error is not None:
            return error
        operation = data.get("operation")
        options = data.get("options", {})
        if not isinstance(operation, str) or operation not in MAINTENANCE_OPERATIONS:
            return _invalid(_OPERATION_ERROR)
        if not isinstance(options, dict) or options:
            return _invalid(f"{operation} takes no options")
        db = self.db
        identity = self._database_identity(db)
        try:
            roots = (
                await self._run_in_executor(self._health_roots)
                if operation == "missing_links"
                else []
            )
            draft = await self._run_in_executor(
                db.preview_maintenance, operation, roots
            )
        except Exception:
            self.logger.exception(f"Maintenance preview of {operation} failed")
            return _failure("Failed to review maintenance", 500)
        if identity != self._database_identity(self.db):
            return _failure(
                "The database was replaced while it was reviewed. Review again.",
                409,
                "storage-changed",
            )
        policy = self._safety_backup_policy()
        store = self._maintenance_plans
        try:
            token = store.issue(
                operation,
                draft.fingerprint,
                {
                    "targets": draft.targets,
                    "roots": roots,
                    "protected": draft.protected["count"],
                    "backup": policy,
                },
                identity,
            )
        except ScopeTooLarge:
            return _failure(
                "This plan is too large to review at once.", 413, "too-large"
            )
        plan = store.peek(token, operation)
        name = os.path.basename(db.model.db_path)
        return web.json_response(
            {
                "success": True,
                "token": token,
                "expires_at": _iso(plan.expires_at),
                **draft.public(),
                "backup": {
                    "policy": policy,
                    "destination_label": f"{name}.backup_<date and time>",
                },
            },
            headers={"Cache-Control": "no-store"},
        )

    # ── apply ─────────────────────────────────────────────────────────

    async def apply_maintenance_plan(self, request):
        data, error = await self._read_maintenance_body(request, ("token",))
        if error is not None:
            return error
        token = data.get("token")
        if not isinstance(token, str) or not 0 < len(token) <= MAX_TOKEN_LENGTH:
            return _invalid("token must be the plan's token")
        store = self._maintenance_plans
        try:
            plan = store.peek(token)
        except PlanUsed as used:
            job = self._jobs.get(used.job_id)
            return _failure(
                "This plan was already applied.",
                409,
                "plan-used",
                job={"id": used.job_id, "title": job.title if job else None},
            )
        except PlanExpired:
            return _failure(
                "This review expired. Review the plan again.", 410, "plan-expired"
            )
        db = self.db
        if plan.db_identity != self._database_identity(db):
            store.discard(token)
            return _failure(
                "The database was replaced since the review. Review again.",
                409,
                "storage-changed",
            )
        try:
            job = self._jobs.start(
                MAINTENANCE_JOB_KIND,
                MAINTENANCE_TITLES[plan.operation],
                admission="records",
                cancellable=False,
                scope={"operation": plan.operation},
                groups=MAINTENANCE_GROUPS,
                runner=lambda job: self._run_maintenance_plan(job, db, plan),
            )
        except JobBusy as busy:
            return busy_response(busy)  # the token stays usable
        # Same loop turn as the start: the runner has not run yet.
        store.consume(token, plan.operation, job_id=job.id)
        return web.json_response({"success": True, "job": job.summary()}, status=202)

    async def _run_maintenance_plan(self, job, db, plan):
        payload = plan.payload
        backup = None
        if payload["backup"] == "on" or self._safety_backup_policy() == "on":
            job.update(status_line="Taking a safety backup")
            job.log_line("Taking a safety backup of the database")
            try:
                backup = await self._run_in_executor(take_safety_backup, db.model)
            except SafetyBackupFailed:
                self.logger.error("Safety backup failed", exc_info=True)
                return _fail_job(job, "backup-failed", BACKUP_FAILED)
            job.log_line(f"Safety backup {backup} verified")
        else:
            job.log_line("No safety backup (the policy is off)")
        job.update(status_line=f"Applying: {job.title}")
        try:
            result = await self._run_in_executor(
                db.apply_maintenance,
                plan.operation,
                plan.fingerprint,
                payload["targets"],
                payload["roots"],
            )
        except PlanChanged:
            return _fail_job(job, "plan-changed", PLAN_CHANGED)
        except Exception:
            self.logger.exception(f"Maintenance {plan.operation} failed")
            return _fail_job(job, None, APPLY_FAILED)
        rows = await self._run_in_executor(
            self._maintenance_rows, plan.operation, result["rows"]
        )
        job.record_counted("changed", result["changed"], rows)
        job.record_counted("protected", payload["protected"])
        line = _changed_line(plan.operation, result["changed"])
        job.log_line(line)
        if backup:
            removed = await self._run_in_executor(
                _prune_safety_backups, db.model.db_path
            )
            if removed:
                job.log_line(f"Removed {len(removed)} older safety backups")
        job.update(
            progress=100,
            status_line=f"{line} · "
            + (f"Safety backup {backup}" if backup else NO_BACKUP),
        )

    def _maintenance_rows(self, operation, described):
        """Outcome rows for the changed records (root-relative paths only)."""
        if operation == "missing_links":
            roots = _scan_roots(self._get_all_output_dirs())
            rows = []
            for item in described:
                path, label, index = _scan_location(item["image_path"], roots)
                rows.append(
                    {
                        "path": path,
                        "root": label,
                        "root_index": index,
                        "detail": "Link removed; the file was already missing",
                    }
                )
            return rows
        if operation == "duplicate_prompts":
            return [
                {
                    "path": "",
                    "detail": (
                        f"Prompt {item['prompt_id']} merged into "
                        f"prompt {item['kept_id']}"
                    ),
                }
                for item in described
            ]
        return [
            {"path": "", "detail": f"Removed: {item.get('text') or item['prompt_id']}"}
            for item in described
        ]


def _fail_job(job, code, message):
    """End ``job`` failed with ``message`` (one error row) and its code."""
    job.record("errors", {"path": "", "detail": message}, f"! {message}")
    job.update(status_line=message)
    job.publish({"type": "error", "code": code, "message": message})
    job.finish("failed")
