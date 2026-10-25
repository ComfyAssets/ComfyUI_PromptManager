"""Database backup and restore routes (#249 System S8).

Legacy routes, responses unchanged:

- ``GET /prompt_manager/backup`` downloads a consistent copy of the library.
  It holds the registry as ``backup`` ("Database backup", ``guarded``): it is
  refused while a restore, maintenance apply or other record-changing job
  runs. The copy is written in the library's folder, made one standalone
  file and verified (integrity and schema) before a byte is sent; a copy that
  does not verify is a 500 with no bytes. It is streamed from the open file,
  then closed and removed.
- ``POST /prompt_manager/restore`` (multipart ``database_file``) restores in
  one request through the same stage and publish as the restore job, holding
  the registry as ``restore`` (``exclusive``).

Reviewed restore (new routes answer errors as ``{success:false, code, error}``):

- ``POST /prompt_manager/restore/preview`` (multipart ``database_file``, the
  same disk-space cap) stages the upload in the library's folder
  (``database/restore_stage.py``) and answers what it holds beside the live
  library: ``{token, expires_at, summary, schema:{migrated, library_state},
  integrity:"ok", documents:{repaired, cleared, images}, live, size,
  safety_backup:{destination_label}}``. Nothing live is written or backed
  up. At most two previews are held (``too-large``, 429).
- ``DELETE /prompt_manager/restore/preview/{token}`` discards a stage.
- ``POST /prompt_manager/restore/apply {token}`` starts the restore job (202
  ``{job}``): kind ``restore``, "Database restore", ``exclusive`` (it runs
  alone, like Move library), not cancellable, indeterminate. The runner waits
  for request leases to end, rechecks the live fingerprint (``plan-changed``),
  publishes the exact staged bytes, swaps ``self.db``, prunes old safety
  backups and logs what was restored.
- ``GET /prompt_manager/backup/status``: the safety backups on disk
  (``<db>.backup_*``, newest first, names only) and this session's last
  verified download (Q9: no sidecar file).
"""

import datetime
import os
import sqlite3
import tempfile

from aiohttp import web

from . import admin as _admin
from .job_routes import busy_response
from .jobs import RESTORE_JOB_KIND, JobBusy
from .maintenance_plans import PlanExpired, PlanUsed
from .restore_plans import RestorePlanStore

try:
    from ...database import restore_stage
    from ...database.operations import PromptDatabase
    from ...database.safety_backup import (
        SafetyBackupFailed,
        fsync_file,
        make_standalone,
        remove_quietly,
        safety_temp,
    )
except ImportError:
    from database import restore_stage
    from database.operations import PromptDatabase
    from database.safety_backup import (
        SafetyBackupFailed,
        fsync_file,
        make_standalone,
        remove_quietly,
        safety_temp,
    )

BACKUP_JOB_KIND = "backup"
BACKUP_HOLD_TITLE = "Database backup"
LEGACY_RESTORE_TITLE = "Database restore (admin page)"
RESTORE_JOB_TITLE = "Database restore"
RESTORE_GROUPS = ("errors",)
MAX_TOKEN_LENGTH = 128
APPLY_BODY_MAX = 1024
STREAM_CHUNK = 1024 * 1024
MAX_LISTED_BACKUPS = 10
SIDECARS = ("", "-wal", "-shm", "-journal")
REQUESTS_BUSY = (
    "Other requests are still reading the library; nothing was changed. "
    "Try again in a moment."
)
PLAN_CHANGED = (
    "The library changed since the review; nothing was changed. "
    "Review the restore again."
)
NOT_RESTORED = "The restore failed; the library was not changed."


def _failure(message, status, code=None, **extra):
    return web.json_response(
        {"success": False, "code": code, "error": message, **extra}, status=status
    )


def _iso(timestamp):
    return datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc).isoformat()


def _plural(count, word):
    return f"{count:,} {word}" + ("" if count == 1 else "s")


def documents_line(documents):
    """The repair report's sentence, or "" when nothing was damaged."""
    repaired, cleared = documents.get("repaired", 0), documents.get("cleared", 0)
    parts = []
    if repaired:
        parts.append(f"repaired {_plural(repaired, 'damaged workflow document')}")
    if cleared:
        images = documents.get("images", 0)
        parts.append(
            f"could not repair {_plural(cleared, 'workflow document')}: "
            f"{_plural(images, 'image')} {'shows' if images == 1 else 'show'} "
            "no workflow"
        )
    if not parts:
        return ""
    text = "; ".join(parts)
    return text[0].upper() + text[1:] + "."


def restored_line(summary):
    return (
        f"Restored {_plural(summary['prompts'], 'prompt')}, "
        f"{_plural(summary['image_links'], 'image link')}, "
        f"{_plural(summary['pairs'], 'saved pair')} and "
        f"{_plural(summary['runs'], 'run')}"
    )


def safety_backups(db_path, limit=MAX_LISTED_BACKUPS):
    """``<db>.backup_*`` files beside the database, newest first (blocking)."""
    db_path = os.path.abspath(db_path)
    folder = os.path.dirname(db_path)
    prefix = os.path.basename(db_path) + ".backup_"
    found = []
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    for name in names:
        if not name.startswith(prefix):
            continue
        try:
            stat = os.stat(os.path.join(folder, name))
        except OSError:
            continue
        if not os.path.isfile(os.path.join(folder, name)):
            continue
        found.append((stat.st_mtime, name, stat.st_size))
    found.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [
        {"name": name, "bytes": size, "modified_at": _iso(mtime)}
        for mtime, name, size in found[:limit]
    ]


def write_backup_snapshot(model):
    """A verified, standalone copy of the library beside it; its path (blocking).

    Raises SafetyBackupFailed with nothing left on disk when the copy cannot
    be written or does not verify. The caller removes the returned file.
    """
    temp = safety_temp(model.db_path)
    try:
        if not model.backup_database(temp):
            raise SafetyBackupFailed("The backup could not be written")
        make_standalone(temp)
        ok, reason = model.verify_database_file(temp, documents=False)
        if not ok:
            raise SafetyBackupFailed("The backup did not verify", reason)
        fsync_file(temp)
        return temp
    except BaseException:
        remove_quietly(*(temp + suffix for suffix in SIDECARS))
        raise


class BackupRestoreRoutesMixin:
    """Mixin providing the backup and restore endpoints."""

    def _register_backup_restore_routes(self, routes):
        @routes.post("/prompt_manager/restore/preview")
        async def restore_preview_route(request):
            return await self.preview_restore(request)

        @routes.delete("/prompt_manager/restore/preview/{token}")
        async def restore_discard_route(request):
            return await self.discard_restore_preview(request)

        @routes.post("/prompt_manager/restore/apply")
        async def restore_apply_route(request):
            return await self.apply_restore(request)

        @routes.get("/prompt_manager/backup/status")
        async def backup_status_route(request):
            return await self.backup_status(request)

    @property
    def _restore_plans(self):
        store = getattr(self, "_restore_plan_store", None)
        if store is None:
            store = self._restore_plan_store = RestorePlanStore()
        return store

    # ── backup ────────────────────────────────────────────────────────

    async def backup_database(self, request):
        """Download a verified copy of the prompts database (WAL included)."""
        return await self._run_held(
            BACKUP_JOB_KIND,
            BACKUP_HOLD_TITLE,
            "guarded",
            lambda: self._stream_backup(request),
        )

    async def _stream_backup(self, request):
        model = self.db.model
        if not os.path.exists(model.db_path):
            return web.json_response(
                {"success": False, "error": "Database file not found"}, status=404
            )
        try:
            temp = await self._run_in_executor(write_backup_snapshot, model)
        except SafetyBackupFailed as failed:
            self.logger.error(f"Backup failed: {failed} {failed.reason or ''}")
            return web.json_response(
                {"success": False, "error": "Failed to create database backup"},
                status=500,
            )
        except Exception as e:
            self.logger.error(f"Backup error: {e}", exc_info=True)
            return web.json_response(
                {
                    "success": False,
                    "error": f"Failed to backup database: {self._public_error(e)}",
                },
                status=500,
            )
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        handle = None
        try:
            size = os.path.getsize(temp)
            handle = open(temp, "rb")
            response = web.StreamResponse(
                headers={
                    "Content-Type": "application/octet-stream",
                    "Content-Disposition": (
                        f'attachment; filename="prompts_backup_{timestamp}.db"'
                    ),
                    "Content-Length": str(size),
                }
            )
            await response.prepare(request)
            while True:
                chunk = await self._run_in_executor(handle.read, STREAM_CHUNK)
                if not chunk:
                    break
                await response.write(chunk)
            await response.write_eof()
            self._last_backup_download = {
                "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "bytes": size,
                "verified": True,
            }
            return response
        finally:
            if handle is not None:
                handle.close()  # before the unlink (Windows)
            await self._run_in_executor(
                remove_quietly, *(temp + suffix for suffix in SIDECARS)
            )

    async def backup_status(self, request):
        try:
            backups = await self._run_in_executor(safety_backups, self.db.model.db_path)
        except Exception:
            self.logger.exception("Backup status failed")
            return _failure("Failed to read the backup status", 500)
        return web.json_response(
            {
                "success": True,
                "safety_backups": backups,
                "last_download": getattr(self, "_last_backup_download", None),
            },
            headers={"Cache-Control": "no-store"},
        )

    # ── the upload ────────────────────────────────────────────────────

    def _restore_refusal(self, size, ceiling, room, *, known):
        """``400 too-large`` past the ceiling, else ``507 insufficient-space``."""
        from .maintenance import restore_space_message

        if size > ceiling:
            return web.json_response(
                {
                    "success": False,
                    "code": "too-large",
                    "error": (
                        "File too large. Maximum size is "
                        f"{ceiling // (1024 * 1024)}MB"
                    ),
                },
                status=400,
            )
        return web.json_response(
            {
                "success": False,
                "code": "insufficient-space",
                "error": restore_space_message(size, room, known=known),
            },
            status=507,
        )

    async def _receive_restore_upload(self, request, uploads):
        """Stream ``database_file`` into the library's folder.

        Returns ``(path, None)`` or ``(None, refusal)``. Each temp path is
        appended to ``uploads`` as soon as it exists, so the caller removes
        it (and its sidecars) whatever happens.
        """
        from .maintenance import RESTORE_MAX_BYTES, restore_room

        ceiling = getattr(self, "restore_max_bytes", RESTORE_MAX_BYTES)
        db_path = self.db.model.db_path
        # Free disk space decides how large a backup may be (#313): the
        # upload, its staged copy and the swap must all fit.
        room = await self._run_in_executor(restore_room, db_path)
        max_bytes = min(ceiling, room)
        length = request.content_length
        if length is not None and length > max_bytes:
            return None, self._restore_refusal(length, ceiling, room, known=True)
        reader = await request.multipart()
        field = await reader.next()
        if not field or field.name != "database_file":
            return None, web.json_response(
                {
                    "success": False,
                    "error": (
                        "No database file uploaded. "
                        "Expected field name: database_file"
                    ),
                },
                status=400,
            )
        fd, temp_path = tempfile.mkstemp(
            prefix=restore_stage.UPLOAD_PREFIX,
            suffix=".tmp",
            dir=os.path.dirname(os.path.abspath(db_path)),
        )
        uploads.append(temp_path)
        size = 0
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = await field.read_chunk()
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    return None, self._restore_refusal(size, ceiling, room, known=False)
                out.write(chunk)
        if size == 0:
            return None, web.json_response(
                {"success": False, "error": "Uploaded file is empty"}, status=400
            )
        return temp_path, None

    async def _upload_problem(self, path):
        """None when the upload may be staged, else a readable reason."""
        ok, reason = await self._run_in_executor(
            self.db.model.verify_database_file, path, documents=False
        )
        if ok:
            reason = await self._run_in_executor(_admin._inspect_restore_upload, path)
            ok = reason is None
        return None if ok else reason

    async def _remove_uploads(self, uploads):
        for path in uploads:
            await self._run_in_executor(
                remove_quietly, *(path + suffix for suffix in SIDECARS)
            )

    async def _prune_after(self, db_path, *, failed=False):
        try:
            removed = await self._run_in_executor(_admin._prune_safety_backups, db_path)
        except Exception:
            if not failed:
                raise
            self.logger.warning("Could not prune safety backups after failed restore")
            return
        if removed:
            self.logger.info(f"Removed {len(removed)} old safety backups")

    async def _publish(self, model, staged):
        """Publish ``staged``; on a page-copy failure prune and re-raise."""
        try:
            return await self._run_in_executor(model.publish_staged, staged)
        except sqlite3.OperationalError as exc:
            if getattr(exc, "_prompt_manager_restore_backup_created", False) is True:
                await self._prune_after(model.db_path, failed=True)
            raise

    def _swap_database(self, db_path):
        """Open the restored library as ``self.db``; the previous one to close."""
        previous = self.db
        self.db = PromptDatabase(db_path)
        self._database_replaced()  # a health report described the old one
        self._restore_plans.discard_all()  # stages reviewed against the old one
        return previous

    # ── legacy one-step restore ───────────────────────────────────────

    async def restore_database(self, request):
        """Restore the prompts database from an uploaded SQLite file.

        The upload is streamed to a temp file in the library's folder. Free
        disk space decides how large it may be (``maintenance.restore_room``),
        under a 64 GiB ceiling (``restore_max_bytes``): a request whose
        Content-Length does not fit is refused before anything is written,
        and one without is refused as soon as its running size does not. The
        file is checked, staged, repaired and verified, then published over
        the live database, which is backed up first. The whole request holds
        the registry as ``restore`` (``exclusive``): it is refused while any
        job runs, and every job and request lease is refused while it runs.
        """
        return await self._run_held(
            RESTORE_JOB_KIND,
            LEGACY_RESTORE_TITLE,
            "exclusive",
            lambda: self._restore_database(request),
        )

    async def _restore_database(self, request):
        uploads = []
        try:
            temp_path, refused = await self._receive_restore_upload(request, uploads)
            if refused is not None:
                return refused
            reason = await self._upload_problem(temp_path)
            if reason is not None:
                return web.json_response(
                    {"success": False, "error": reason}, status=400
                )
            if not await self._jobs.drain_leases():
                return web.json_response(
                    {"success": False, "error": REQUESTS_BUSY}, status=409
                )
            model = self.db.model
            staged = await self._run_in_executor(model.stage_restore, temp_path)
            try:
                backup_path = await self._publish(model, staged)
            finally:
                await self._run_in_executor(staged.discard)
            await self._prune_after(model.db_path)
            previous = self._swap_database(model.db_path)
            # Its connections would otherwise linger (a file lock on Windows)
            await self._run_in_executor(previous.close_all)
            prompt_count = self.db.model.get_database_info().get("total_prompts", 0)
            message = f"Database restored successfully. Found {prompt_count} prompts."
            body = {
                "success": True,
                "message": message,
                "prompt_count": prompt_count,
                "backup_created": (
                    self._public_path(backup_path) if backup_path else None
                ),
            }
            repaired = documents_line(staged.documents)
            if repaired:
                body["message"] = f"{message} {repaired}"
                body["workflow_documents"] = dict(staged.documents)
            return web.json_response(body)
        except Exception as e:
            self.logger.error(f"Restore error: {e}", exc_info=True)
            return web.json_response(
                {
                    "success": False,
                    "error": f"Failed to restore database: {self._public_error(e)}",
                },
                status=500,
            )
        finally:
            await self._remove_uploads(uploads)

    # ── reviewed restore ──────────────────────────────────────────────

    def _live_fingerprint(self, db):
        return restore_stage.live_fingerprint(db.model.get_connection())

    async def preview_restore(self, request):
        """Stage an upload for review; nothing live is written (see module)."""
        store = self._restore_plans
        await self._run_in_executor(store.drop_expired)
        if store.full():
            return _failure(
                "Two restores are already being reviewed. Discard one first.",
                429,
                "too-large",
            )
        uploads = []
        staged = None
        try:
            async with self._jobs.lease("Restore preview"):
                db = self.db
                identity = self._database_identity(db)
                temp_path, refused = await self._receive_restore_upload(
                    request, uploads
                )
                if refused is not None:
                    return refused
                reason = await self._upload_problem(temp_path)
                if reason is not None:
                    return _failure(reason, 400, "invalid-backup")
                try:
                    staged = await self._run_in_executor(
                        db.model.stage_restore, temp_path
                    )
                except ValueError as invalid:
                    return _failure(str(invalid), 400, "invalid-backup")
                live, fingerprint = await self._run_in_executor(
                    self._live_fingerprint, db
                )
        except JobBusy as busy:
            return busy_response(busy)
        except Exception:
            self.logger.exception("Restore preview failed")
            if staged is not None:
                await self._run_in_executor(staged.discard)
            return _failure("Failed to review the restore", 500)
        finally:
            await self._remove_uploads(uploads)
        if identity != self._database_identity(self.db) or store.full():
            await self._run_in_executor(staged.discard)
            if store.full():
                return _failure(
                    "Two restores are already being reviewed. Discard one first.",
                    429,
                    "too-large",
                )
            return _failure(
                "The database was replaced while the restore was reviewed. "
                "Review again.",
                409,
                "storage-changed",
            )
        token = store.issue(staged, fingerprint, identity, live)
        held = store.peek(token)
        name = os.path.basename(db.model.db_path)
        return web.json_response(
            {
                "success": True,
                "token": token,
                "expires_at": _iso(held.expires_at),
                "summary": dict(staged.summary),
                "schema": dict(staged.schema),
                "integrity": "ok",
                "documents": dict(staged.documents),
                "live": dict(live),
                "size": staged.size,
                "safety_backup": {
                    "destination_label": f"{name}.backup_<date and time>"
                },
            },
            headers={"Cache-Control": "no-store"},
        )

    async def discard_restore_preview(self, request):
        token = request.match_info.get("token", "")
        if not 0 < len(token) <= MAX_TOKEN_LENGTH:
            return _failure("Unknown restore review.", 410, "plan-expired")
        discarded = await self._run_in_executor(self._restore_plans.discard, token)
        if not discarded:
            return _failure("Unknown restore review.", 410, "plan-expired")
        return web.json_response({"success": True})

    def _peek_restore(self, token):
        try:
            return self._restore_plans.peek(token), None
        except PlanUsed as used:
            job = self._jobs.get(used.job_id)
            return None, _failure(
                "This restore was already started.",
                409,
                "plan-used",
                job={"id": used.job_id, "title": job.title if job else None},
            )
        except PlanExpired:
            return None, _failure(
                "This review expired. Review the restore again.",
                410,
                "plan-expired",
            )

    async def apply_restore(self, request):
        data, error = await self._read_json_body(request, max_bytes=APPLY_BODY_MAX)
        if error is not None:
            if error.status == 413:
                return _failure("Request body too large", 413, "too-large")
            return _failure(
                "Request body must be a JSON object", 400, "invalid-request"
            )
        token = data.get("token")
        if set(data) != {"token"} or not isinstance(token, str):
            return _failure("token must be the review's token", 400, "invalid-request")
        if not 0 < len(token) <= MAX_TOKEN_LENGTH:
            return _failure("token must be the review's token", 400, "invalid-request")
        await self._run_in_executor(self._restore_plans.drop_expired)
        held, refused = self._peek_restore(token)
        if refused is not None:
            return refused
        db = self.db
        if held.db_identity != self._database_identity(db):
            await self._run_in_executor(self._restore_plans.discard, token)
            return _failure(
                "The database was replaced since the review. Review again.",
                409,
                "storage-changed",
            )
        try:
            async with self._jobs.lease("Restore review check"):
                _live, fingerprint = await self._run_in_executor(
                    self._live_fingerprint, db
                )
        except JobBusy as busy:
            return busy_response(busy)
        if fingerprint != held.fingerprint:
            # It can never match again: free the stage for a new review.
            await self._run_in_executor(self._restore_plans.discard, token)
            return _failure(PLAN_CHANGED, 409, "plan-changed")
        # The token may have been used, expired or discarded meanwhile.
        held, refused = self._peek_restore(token)
        if refused is not None:
            return refused
        if db is not self.db:
            return _failure(
                "The database was replaced since the review. Review again.",
                409,
                "storage-changed",
            )
        try:
            job = self._jobs.start(
                RESTORE_JOB_KIND,
                RESTORE_JOB_TITLE,
                # It replaces the whole database: it runs alone, beside no
                # job of any class, like Move library.
                admission="exclusive",
                cancellable=False,
                scope={},
                groups=RESTORE_GROUPS,
                runner=lambda job: self._run_restore(job, db, held),
            )
        except JobBusy as busy:
            return busy_response(busy)  # the token stays usable
        # Same loop turn as the peek and the start: the runner has not run.
        self._restore_plans.take(token, job.id)
        return web.json_response({"success": True, "job": job.summary()}, status=202)

    async def _run_restore(self, job, db, held):
        from .maintenance import _fail_job

        staged = held.staged
        try:
            job.update(status_line="Waiting for other requests to finish")
            if not await self._jobs.drain_leases():
                return _fail_job(job, "busy", REQUESTS_BUSY)
            job.update(status_line="Checking the library")
            _live, fingerprint = await self._run_in_executor(self._live_fingerprint, db)
            if db is not self.db or fingerprint != held.fingerprint:
                return _fail_job(job, "plan-changed", PLAN_CHANGED)
            job.update(status_line="Restoring the database")
            job.log_line("Restoring the reviewed backup")
            model = db.model
            try:
                backup_path = await self._publish(model, staged)
            except restore_stage.StageChanged:
                return _fail_job(job, "plan-changed", PLAN_CHANGED)
            except SafetyBackupFailed:
                return _fail_job(
                    job,
                    "backup-failed",
                    "The current library could not be backed up; nothing was "
                    "changed.",
                )
            except sqlite3.OperationalError as error:
                kept = (
                    " The safety backup was kept."
                    if getattr(error, "_prompt_manager_restore_backup_created", False)
                    else ""
                )
                self.logger.error(f"Restore failed: {error}")
                return _fail_job(job, "restore-failed", NOT_RESTORED + kept)
            await self._prune_after(model.db_path)
            previous = self._swap_database(model.db_path)
            await self._run_in_executor(previous.close_all)
            if backup_path:
                job.log_line(f"Safety backup {os.path.basename(backup_path)}")
            else:
                job.log_line("No safety backup: there was no library to keep")
            repaired = documents_line(staged.documents)
            if repaired:
                job.log_line(repaired)
            line = restored_line(staged.summary)
            job.log_line(line)
            job.update(progress=100, status_line=line)
        except Exception:
            self.logger.exception("Restore job failed")
            _fail_job(job, None, NOT_RESTORED)
        finally:
            await self._run_in_executor(staged.discard)
