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
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing

from aiohttp import web

from .admin import _prune_safety_backups, _scan_location, _scan_roots
from .job_routes import busy_response
from .jobs import MAINTENANCE_JOB_KIND, JobBusy
from .maintenance_plans import PlanExpired, PlanUsed, ScopeTooLarge

try:
    from ...database.health import readonly_uri
    from ...database.maintenance import MAINTENANCE_OPERATIONS, PlanChanged
except ImportError:
    from database.health import readonly_uri
    from database.maintenance import MAINTENANCE_OPERATIONS, PlanChanged

PREVIEW_BODY_MAX = 4096
# Duplicate-file previews carry the reviewed keepers (py/api/duplicate_files.py).
DUPLICATE_PREVIEW_BODY_MAX = 1024 * 1024
DUPLICATE_FILES = "duplicate_files"
WORKFLOW_STORAGE = "workflow_storage"  # applied by py/api/workflow_storage.py
# Its backup's name, outside the pruned ``<db>.backup_*`` pool.
PRE_MIGRATION_LABEL = "pre-workflow-storage"
PREVIEW_OPERATIONS = MAINTENANCE_OPERATIONS + (DUPLICATE_FILES,)
# Room left over beyond what a step writes, so a disk is never filled to 0.
DISK_MARGIN_BYTES = 16 * 2**20
# The concept's Maintenance row titles, which name each operation's job.
MAINTENANCE_TITLES = {
    "duplicate_prompts": "Duplicate prompt text",
    "optimize": "Optimize database",
    "unused_components": "Unused prompt components",
    "missing_links": "Missing-file links",
    "workflow_storage": "Store each workflow once",
}
_OPERATION_ERROR = "operation must be one of: " + ", ".join(PREVIEW_OPERATIONS)
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


def _free_bytes(path):
    return shutil.disk_usage(path).free


def _device(path):
    try:
        return os.stat(path).st_dev
    except OSError:
        return path


def sqlite_temp_dir(environ=None, *, windows=None, usable=None):
    """The folder SQLite builds its temporary files in (VACUUM's copy).

    Mirrors SQLite's own search, which differs from ``tempfile``'s. On Unix
    (``unixTempFileDir``): ``SQLITE_TMPDIR``, ``TMPDIR``, then ``/var/tmp``,
    ``/usr/tmp`` and ``/tmp``, the first that is a writable directory, else
    the current directory. On Windows (``GetTempPathW``): ``TMP``, ``TEMP``,
    then ``USERPROFILE``, else the current directory.
    """
    environ = os.environ if environ is None else environ
    if windows is None:
        windows = os.name == "nt"
    if windows:
        for name in ("TMP", "TEMP", "USERPROFILE"):
            if environ.get(name):
                return environ[name]
        return os.path.abspath(".")
    if usable is None:

        def usable(path):
            return os.path.isdir(path) and os.access(path, os.W_OK | os.X_OK)

    candidates = [environ.get("SQLITE_TMPDIR"), environ.get("TMPDIR")]
    candidates += ["/var/tmp", "/usr/tmp", "/tmp"]
    for candidate in candidates:
        if candidate and usable(candidate):
            return candidate
    return os.path.abspath(".")


def database_bytes(db_path):
    """The size a safety backup of the database will be (blocking).

    SQLite's backup API copies every page, committed WAL content included, so
    the copy is ``page_count * page_size`` read on one read-only snapshot.
    """
    conn = sqlite3.connect(readonly_uri(db_path), uri=True)
    try:
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        return page_size * conn.execute("PRAGMA page_count").fetchone()[0]
    finally:
        conn.close()


def _megabytes(size):
    return f"{size / 2**20:,.1f} MB"


def disk_plan(db_path, *, backup, vacuum, vacuum_bytes=None):
    """Whether the disk has room for an apply's writes (blocking).

    ``backup``: the safety backup, one database-sized file beside the
    database. ``vacuum``: VACUUM writes the rebuilt database through the WAL
    beside it and builds it in a temporary copy in SQLite's temporary folder
    (``sqlite_temp_dir``; SQLite's documentation: up to twice the database
    in free space). Needs
    on one device add up. ``vacuum_bytes``, when given, is what VACUUM
    copies (the live pages, for Reclaim space) instead of the whole file.
    Returns ``(public, short)``: ``public`` is
    ``{free_bytes, required_bytes, sufficient}`` for the database folder,
    ``short`` None or ``(place, what, required, free)`` of the first place
    that is short, and ``size`` the database's size (0 when nothing is
    written).
    """
    db_path = os.path.abspath(db_path)
    folder = os.path.dirname(db_path)
    size = database_bytes(db_path) if backup or vacuum else 0
    needs = []  # (place, folder, bytes, what)
    if backup:
        needs.append(("database folder", folder, size, "the safety backup"))
    if vacuum:
        copy = size if vacuum_bytes is None else vacuum_bytes
        what = "Optimize" if vacuum_bytes is None else "Reclaim space"
        needs.append(("database folder", folder, copy, what))
        needs.append(("temporary folder", sqlite_temp_dir(), copy, what))
    devices = {}
    for place, path, needed, what in needs:
        entry = devices.setdefault(_device(path), {"place": place, "path": path})
        entry["bytes"] = entry.get("bytes", 0) + needed
        entry.setdefault("what", [])
        if what not in entry["what"]:
            entry["what"].append(what)
    short = None
    public = {"free_bytes": _free_bytes(folder), "required_bytes": 0}
    for device, entry in devices.items():
        required = entry["bytes"] + DISK_MARGIN_BYTES
        free = (
            public["free_bytes"]
            if entry["path"] == folder
            else _free_bytes(entry["path"])
        )
        if device == _device(folder):
            public["required_bytes"] = required
        if free < required and short is None:
            short = (entry["place"], " and ".join(entry["what"]), required, free)
    public["sufficient"] = short is None
    return public, short, size


def short_space_message(short):
    """The truthful refusal for ``disk_plan``'s ``short``."""
    place, what, required, free = short
    verb = "need" if " and " in what else "needs"
    return (
        f"Not enough free disk space: {what} {verb} about {_megabytes(required)} "
        f"in the {place} and {_megabytes(free)} is free. Nothing was changed."
    )


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


def _reserve_backup_name(db_path, label="backup"):
    """Create and return an unused ``<db>.<label>_<stamp>[_n]`` (O_EXCL)."""
    for candidate in _backup_names(db_path, label):
        try:
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        os.close(fd)
        return candidate
    raise SafetyBackupFailed("No free backup name")


def _backup_names(db_path, label="backup"):
    base = f"{db_path}.{label}_{_now().strftime('%Y%m%d_%H%M%S_%f')}"
    for attempt in range(_NAME_ATTEMPTS):
        yield base if attempt == 0 else f"{base}_{attempt}"


def _place_backup(temp, db_path, label="backup"):
    """Give the verified ``temp`` its unique backup name; return that path.

    ``os.link`` is atomic and refuses an existing name, so no empty or half
    file ever carries a backup name, even across a crash. A filesystem
    without hard links falls back to reserving the name (``O_EXCL``) and
    ``os.replace``. The temp file is removed by the caller.
    """
    for candidate in _backup_names(db_path, label):
        try:
            os.link(temp, candidate)
        except FileExistsError:
            continue
        except (OSError, NotImplementedError):
            break  # no hard links here
        else:
            return candidate
    else:
        raise SafetyBackupFailed("No free backup name")
    final = _reserve_backup_name(db_path, label)
    try:
        os.replace(temp, final)
    except OSError:
        _remove_quietly(final)
        raise
    return final


SAFETY_TEMP_PREFIX = ".pm-safety-"


def sweep_safety_temps(db_path):
    """Remove the temp copies an interrupted safety backup left (blocking).

    Called once at startup, before any job can take a backup. Returns the
    names removed.
    """
    folder = os.path.dirname(os.path.abspath(db_path))
    removed = []
    try:
        names = os.listdir(folder)
    except OSError:
        return removed
    for name in names:
        if not (name.startswith(SAFETY_TEMP_PREFIX) and ".tmp" in name):
            continue
        try:
            os.remove(os.path.join(folder, name))
            removed.append(name)
        except OSError:
            continue
    return removed


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


def take_safety_backup(model, label="backup"):
    """Write and verify a safety backup; return its file name (blocking).

    The copy is made in a temp file beside the database and only renamed
    onto its final, unique name ``<db>.<label>_<stamp>`` once it verified,
    so a backup file on disk is always a verified one. ``label`` "backup"
    joins the pruned pool; another label (the workflow storage migration's
    ``pre-workflow-storage``) is kept until the user removes it. Raises
    SafetyBackupFailed, leaving no file.
    """
    db_path = os.path.abspath(model.db_path)
    fd, temp = tempfile.mkstemp(
        dir=os.path.dirname(db_path), prefix=SAFETY_TEMP_PREFIX, suffix=".tmp"
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
        final = _place_backup(temp, db_path, label)
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

    async def _read_maintenance_body(self, request, allowed, cap=None):
        """``(data, None)`` for a capped JSON object with only ``allowed`` keys.

        ``cap(data)``, when given, is the body's byte limit for what it asks
        (read up to ``DUPLICATE_PREVIEW_BODY_MAX``); else PREVIEW_BODY_MAX.
        """
        limit = PREVIEW_BODY_MAX if cap is None else DUPLICATE_PREVIEW_BODY_MAX
        data, error = await self._read_json_body(request, max_bytes=limit)
        if error is not None:
            if error.status == 413:
                return None, _failure("Request body too large", 413, "too-large")
            return None, _invalid("Request body must be a JSON object")
        if cap is not None:
            size = len(json.dumps(data, separators=(",", ":")).encode("utf-8"))
            if size > cap(data):
                return None, _failure("Request body too large", 413, "too-large")
        unknown = sorted(set(data) - set(allowed))
        if unknown:
            return None, _invalid("Unknown field: " + ", ".join(unknown)[:200])
        return data, None

    async def preview_maintenance(self, request):
        data, error = await self._read_maintenance_body(
            request,
            ("operation", "options"),
            cap=lambda body: (
                DUPLICATE_PREVIEW_BODY_MAX
                if body.get("operation") == DUPLICATE_FILES
                else PREVIEW_BODY_MAX
            ),
        )
        if error is not None:
            return error
        operation = data.get("operation")
        options = data.get("options", {})
        if not isinstance(operation, str) or operation not in PREVIEW_OPERATIONS:
            return _invalid(_OPERATION_ERROR)
        if operation == DUPLICATE_FILES:
            return await self.preview_duplicate_files(options)
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
        return await self._plan_response(
            db,
            identity,
            draft.operation,
            draft.fingerprint,
            draft.public(),
            {
                "targets": draft.targets,
                "roots": roots,
                "protected": draft.protected["count"],
            },
        )

    async def _plan_response(
        self, db, identity, operation, fingerprint, public, payload
    ):
        """Issue the reviewed plan's token and answer the ``Plan``.

        Refused with ``storage-changed`` when the database was replaced while
        it was reviewed. The plan reports the safety backup's expected size
        and the disk space the apply needs (``disk_plan``).
        """
        if identity != self._database_identity(self.db):
            return _failure(
                "The database was replaced while it was reviewed. Review again.",
                409,
                "storage-changed",
            )
        policy = self._safety_backup_policy()
        destination = "backup"
        if operation == WORKFLOW_STORAGE:
            # Always backed up first, whatever the policy (the way back).
            policy, destination = "required", PRE_MIGRATION_LABEL
        try:
            disk, _short, size = await self._run_in_executor(
                disk_plan,
                db.model.db_path,
                backup=policy != "off",
                vacuum=operation == "optimize",
            )
        except (OSError, sqlite3.Error):
            self.logger.exception("Could not measure the disk space for a plan")
            return _failure("Failed to review maintenance", 500)
        store = self._maintenance_plans
        try:
            token = store.issue(
                operation, fingerprint, {**payload, "backup": policy}, identity
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
                **public,
                "backup": {
                    "policy": policy,
                    "destination_label": f"{name}.{destination}_<date and time>",
                    "expected_bytes": size if policy != "off" else None,
                },
                "disk": disk,
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
        plan, refused = self._peek_plan(token)
        if refused is not None:
            return refused
        store = self._maintenance_plans
        db = self.db
        if plan.db_identity != self._database_identity(db):
            store.discard(token)
            return _failure(
                "The database was replaced since the review. Review again.",
                409,
                "storage-changed",
            )
        backup = self._backup_promised(plan)
        try:
            _disk, short, _size = await self._run_in_executor(
                disk_plan,
                db.model.db_path,
                backup=backup,
                vacuum=plan.operation == "optimize",
            )
        except (OSError, sqlite3.Error):
            self.logger.exception("Could not measure the disk space for an apply")
            return _failure(APPLY_FAILED, 500)
        if short is not None:
            # Refused before any job starts: nothing fails halfway for space.
            return _failure(short_space_message(short), 507, "insufficient-space")
        # The token may have been used, expired or dropped while the disk was
        # measured: look again with no await before the job starts.
        plan, refused = self._peek_plan(token)
        if refused is not None:
            return refused
        try:
            if plan.operation == DUPLICATE_FILES:
                job = self._start_duplicate_cleanup(db, plan, backup)
            elif plan.operation == WORKFLOW_STORAGE:
                job = self._start_workflow_storage(db, backup)
            else:
                job = self._jobs.start(
                    MAINTENANCE_JOB_KIND,
                    MAINTENANCE_TITLES[plan.operation],
                    # VACUUM rewrites the whole file under the write lock: it
                    # runs alone, beside no other job of any class.
                    admission=(
                        "exclusive" if plan.operation == "optimize" else "records"
                    ),
                    cancellable=False,
                    scope={"operation": plan.operation},
                    groups=MAINTENANCE_GROUPS,
                    runner=lambda job: self._run_maintenance_plan(
                        job, db, plan, backup
                    ),
                )
        except JobBusy as busy:
            return busy_response(busy)  # the token stays usable
        # Same loop turn as the peek and the start: the runner has not run.
        store.consume(token, plan.operation, job_id=job.id)
        return web.json_response({"success": True, "job": job.summary()}, status=202)

    def _peek_plan(self, token):
        """``(plan, None)`` for a usable token, else ``(None, refusal)``."""
        try:
            return self._maintenance_plans.peek(token), None
        except PlanUsed as used:
            job = self._jobs.get(used.job_id)
            return None, _failure(
                "This plan was already applied.",
                409,
                "plan-used",
                job={"id": used.job_id, "title": job.title if job else None},
            )
        except PlanExpired:
            return None, _failure(
                "This review expired. Review the plan again.", 410, "plan-expired"
            )

    def _backup_promised(self, plan):
        """A plan reviewed with the policy on keeps that promise; so does on now.

        Decided once, when the apply starts (the disk check measured it), and
        handed to the job.
        """
        if plan.operation == WORKFLOW_STORAGE:
            return True  # never without its pre-migration backup
        return plan.payload["backup"] == "on" or self._safety_backup_policy() == "on"

    async def _safety_backup_step(self, job, db, backup_on):
        """``(ok, name)``: take the promised safety backup, or report none.

        ``name`` is the backup's file name, or None when no backup was
        promised. A failed backup ends the job ``backup-failed`` (``ok``
        False).
        """
        if not backup_on:
            job.log_line("No safety backup (the policy is off)")
            return True, None
        job.update(status_line="Taking a safety backup")
        job.log_line("Taking a safety backup of the database")
        try:
            backup = await self._run_in_executor(take_safety_backup, db.model)
        except SafetyBackupFailed:
            self.logger.error("Safety backup failed", exc_info=True)
            _fail_job(job, "backup-failed", BACKUP_FAILED)
            return False, None
        job.log_line(f"Safety backup {backup} verified")
        return True, backup

    async def _prune_backup_pool(self, job, db):
        """Keep the one ``<db>.backup_*`` pool (restore's too) at its cap."""
        removed = await self._run_in_executor(_prune_safety_backups, db.model.db_path)
        if removed:
            job.log_line(f"Removed {len(removed)} older safety backups")

    async def _run_maintenance_plan(self, job, db, plan, backup_on):
        ok, backup = await self._safety_backup_step(job, db, backup_on)
        if not ok:
            return
        try:
            await self._apply_maintenance_plan(job, db, plan, backup)
        finally:
            if backup:
                await self._prune_backup_pool(job, db)

    async def _apply_maintenance_plan(self, job, db, plan, backup):
        payload = plan.payload
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
        job.update(progress=100, status_line=f"{line} · {backup_note(backup)}")

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


def backup_note(backup):
    """The status line's backup part: the file name, or that there is none."""
    return f"Safety backup {backup}" if backup else NO_BACKUP


def _fail_job(job, code, message):
    """End ``job`` failed with ``message`` (one error row) and its code."""
    job.record("errors", {"path": "", "detail": message}, f"! {message}")
    job.update(status_line=message)
    job.publish({"type": "error", "code": code, "message": message})
    job.finish("failed")
