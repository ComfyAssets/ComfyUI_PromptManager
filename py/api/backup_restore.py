"""Database backup and restore routes (#249 System S8).

Legacy routes, responses unchanged:

- ``GET /prompt_manager/backup`` downloads a consistent copy of the library.
  While the copy is made it holds the registry as ``backup`` ("Database
  backup", ``guarded``): it is refused while a restore, maintenance apply or
  other record-changing job runs. The copy is written in the library's
  folder and made one standalone file; the hold ends there, so a slow or
  stalled download never keeps other work waiting. A copy that fails its
  integrity check is a 500 with no bytes. One that fails only a semantic
  check (``verify_database_file``) is still sent: a user can always get a
  backup. The reason is logged and sent as ``X-PromptManager-Verify``
  (``ok`` otherwise), and the status records it as not verified. The file
  is streamed, then closed and removed.
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
  backups and logs what was restored. Once the copy replaced the library,
  the restore is done: the later steps only log their failures.
- ``GET /prompt_manager/backup/status``: the safety backups on disk
  (``<db>.backup_*``, newest first, names only) and this session's last
  verified download (Q9: no sidecar file).
"""

import asyncio
import datetime
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

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
# S9 I1: the review goes back to the page that started the restore
# (``scope.review_kept``), which opens it again with Restore and Discard; no
# other place can start a kept review, so the line says where it is.
REVIEW_KEPT = (
    "Other requests are still reading the library; nothing was changed. "
    "The review was reopened in the page that started the restore: restore it "
    "again there, or discard it."
)
VERIFY_HEADER = "X-PromptManager-Verify"
SQLITE_HEADER = b"SQLite format 3\x00"
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


def snapshot_corruption(path):
    """Why the snapshot file is corrupt, or None (blocking).

    Only physical damage refuses a download: not a SQLite file, or a failed
    ``PRAGMA integrity_check``.
    """
    try:
        with open(path, "rb") as handle:
            if handle.read(len(SQLITE_HEADER)) != SQLITE_HEADER:
                return "Not a SQLite database file"
        uri = Path(os.path.abspath(path)).as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            row = conn.execute("PRAGMA integrity_check").fetchone()
    except (OSError, sqlite3.Error) as error:
        return f"Invalid SQLite database: {error}"
    verdict = row[0] if row else "no result"
    return None if verdict == "ok" else f"Integrity check failed: {verdict}"


def write_backup_snapshot(model):
    """A standalone copy of the library beside it: ``(path, warning)`` (blocking).

    Raises SafetyBackupFailed with nothing left on disk when the copy cannot
    be written or is corrupt (``snapshot_corruption``). ``warning`` is the
    reason a semantic check (``verify_database_file``, workflow documents
    not read) refused the copy, or None. The caller removes the file.
    """
    temp = safety_temp(model.db_path)
    try:
        if not model.backup_database(temp):
            raise SafetyBackupFailed("The backup could not be written")
        make_standalone(temp)
        corrupt = snapshot_corruption(temp)
        if corrupt is not None:
            raise SafetyBackupFailed("The backup is corrupt", corrupt)
        ok, reason = model.verify_database_file(temp, documents=False)
        fsync_file(temp)
        return temp, (None if ok else reason)
    except BaseException:
        remove_quietly(*(temp + suffix for suffix in SIDECARS))
        raise


def _header_text(text):
    """A reason as a one-line, latin-1 header value."""
    flat = " ".join(str(text).split())[:300]
    return flat.encode("latin-1", "replace").decode("latin-1")


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
        """Download a copy of the prompts database (WAL included)."""
        return await self._stream_backup(request)

    async def _stream_backup(self, request):
        model = self.db.model
        if not os.path.exists(model.db_path):
            return web.json_response(
                {"success": False, "error": "Database file not found"}, status=404
            )
        made = await self._run_held(
            BACKUP_JOB_KIND,
            BACKUP_HOLD_TITLE,
            "guarded",
            lambda: self._make_backup_snapshot(model),
        )
        if isinstance(made, web.StreamResponse):
            return made  # refused, or the snapshot failed
        temp, warning = made
        return await self._send_snapshot(request, temp, warning)

    async def _make_backup_snapshot(self, model):
        """``(path, warning)``, or the error response (inside the hold)."""
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(None, write_backup_snapshot, model)
        try:
            temp, warning = await asyncio.shield(future)
        except asyncio.CancelledError:
            # The worker cannot be stopped: remove what it writes once it is
            # done (#335 re-review R4).
            future.add_done_callback(
                lambda done: loop.create_task(self._remove_finished_snapshot(done))
            )
            raise
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
        if warning is not None:
            self.logger.warning(f"Backup sent with a failed check: {warning}")
        return temp, warning

    async def _remove_finished_snapshot(self, future):
        """Remove the snapshot a cancelled download's worker finished."""
        if future.cancelled() or future.exception() is not None:
            return  # nothing was left on disk
        temp, _warning = future.result()
        await self._run_in_executor(
            remove_quietly, *(temp + suffix for suffix in SIDECARS)
        )

    async def _send_snapshot(self, request, temp, warning):
        """Stream the snapshot (outside the hold), then close and remove it."""
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
                    VERIFY_HEADER: "ok" if warning is None else _header_text(warning),
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
                "verified": warning is None,
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
        """Prune old safety backups; a failure is only logged (never raises).

        It runs after a restore replaced the library, or after one failed:
        either way the restore's outcome stands.
        """
        try:
            removed = await self._run_in_executor(_admin._prune_safety_backups, db_path)
        except Exception as error:
            when = "failed" if failed else "completed"
            self.logger.warning(
                f"Could not prune safety backups after a {when} restore: "
                f"{type(error).__name__}"
            )
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

    async def _after_publish(self, db_path):
        """Everything after the copy replaced the library: best effort, logged.

        The restore is done once the copy succeeded, so nothing here fails it
        (#335 review I1). The library is adopted as ``self.db`` (the previous
        object, which points at the same file, stays when a new one cannot
        be opened), the generation is bumped (so the health report and
        reviewed plans of the old library are dropped), held restore
        previews are dropped and the previous object's connections closed.
        """
        await self._prune_after(db_path)
        previous = self.db
        try:
            self.db = PromptDatabase(db_path)
        except Exception:
            self.logger.exception("Could not reopen the restored library")
        try:
            self._database_replaced()
        except Exception:
            self.logger.exception("Could not drop the old library's reports")
        try:
            await self._discard_held(self._restore_plans.pop_all())
        except Exception:
            self.logger.exception("Could not remove the old restore previews")
        if previous is not self.db:
            try:
                # Its connections would otherwise linger (a lock on Windows).
                await self._run_in_executor(previous.close_all)
            except Exception:
                self.logger.exception("Could not close the previous library")

    async def _discard_held(self, held):
        """Remove the stage folders of dropped store entries (off the loop)."""
        for entry in held:
            try:
                await self._run_in_executor(entry.staged.discard)
            except Exception:
                self.logger.exception("Could not remove a restore stage")

    async def _discard_popped(self, token):
        """Drop ``token`` and remove its stage, only if the store still held it.

        A stage another apply's job has taken is not this request's to remove
        (#335 re-review R2).
        """
        popped = self._restore_plans.pop(token)
        if popped is not None:
            await self._discard_held([popped])

    async def _drop_expired_restores(self):
        """Drop expired previews from the store (on the loop), then their files."""
        await self._discard_held(self._restore_plans.pop_expired())

    def _schedule_restore_expiry(self):
        """Remove each preview's stage when it expires, without a request.

        A loop timer (``call_later``), so a server that stops with previews
        held leaves nothing pending; the startup sweep removes the folders.
        """
        store = self._restore_plans
        expires = store.next_expiry()
        if expires is None:
            return
        handle = getattr(self, "_restore_expiry_handle", None)
        if handle is not None:
            handle.cancel()
        loop = asyncio.get_running_loop()

        def fire():
            self._restore_expiry_handle = None
            task = loop.create_task(self._expire_restores())
            self._restore_expiry_task = task

        delay = max(0.0, expires - store.now())
        self._restore_expiry_handle = loop.call_later(delay, fire)

    async def _expire_restores(self):
        await self._drop_expired_restores()
        self._schedule_restore_expiry()

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
            # The library is replaced: from here on the restore succeeded.
            await self._after_publish(model.db_path)
            try:
                prompt_count = await self._run_in_executor(
                    lambda: self.db.model.get_database_info().get("total_prompts", 0)
                )
            except Exception:
                self.logger.exception("Could not count the restored prompts")
                prompt_count = staged.summary.get("prompts", 0)
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
        await self._drop_expired_restores()
        if self._restore_plans.full():
            return _too_many_reviews()
        uploads = []
        try:
            db = self.db
            identity = self._database_identity(db)
            temp_path, refused = await self._receive_restore_upload(request, uploads)
            if refused is not None:
                return refused
            return await self._review_upload(db, identity, temp_path, uploads)
        except Exception:
            self.logger.exception("Restore preview failed")
            return _failure("Failed to review the restore", 500)
        finally:
            await self._remove_uploads(uploads)

    async def _stage_for_review(self, db, temp_path, uploads):
        """Stage ``temp_path`` in the executor; a cancel discards the result.

        A cancelled handler (shutdown, a dropped client) cannot stop the
        worker thread: the stage it finishes is removed when it is done, and
        so is the upload it reads (``_orphan_stage_cleanup``).
        """
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(None, db.model.stage_restore, temp_path)
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            orphans = getattr(self, "_orphan_stages", None)
            if orphans is None:
                orphans = self._orphan_stages = set()
            entry = (future, tuple(uploads))
            orphans.add(entry)
            uploads.clear()  # still being read: removed with the stage
            future.add_done_callback(
                lambda _f: loop.create_task(self._orphan_stage_cleanup())
            )
            raise

    async def _orphan_stage_cleanup(self):
        """Remove what cancelled previews staged, once their workers are done."""
        for entry in list(getattr(self, "_orphan_stages", ()) or ()):
            future, uploads = entry
            if not future.done():
                continue
            self._orphan_stages.discard(entry)
            if not future.cancelled() and future.exception() is None:
                await self._discard_held([_Orphan(future.result())])
            await self._remove_uploads(uploads)

    async def _review_upload(self, db, identity, temp_path, uploads=None):
        """Check, stage and compare an upload; the preview response.

        Only the read of the live library takes a lease (#335 review M2):
        the upload and the staging never touch it, and a lease held for a
        long staging would make a restore's drain fail. A stage that is not
        handed to the store (a refusal, an error, a cancel) is removed.
        """
        uploads = [] if uploads is None else uploads  # the caller removes them
        reason = await self._upload_problem(temp_path)
        if reason is not None:
            return _failure(reason, 400, "invalid-backup")
        staged = None
        issued = False
        try:
            try:
                staged = await self._stage_for_review(db, temp_path, uploads)
            except ValueError as invalid:
                return _failure(str(invalid), 400, "invalid-backup")
            try:
                async with self._jobs.lease("Restore preview"):
                    live, fingerprint = await self._run_in_executor(
                        self._live_fingerprint, db
                    )
            except JobBusy as busy:
                return busy_response(busy)
            store = self._restore_plans
            if identity != self._database_identity(self.db):
                return _failure(
                    "The database was replaced while the restore was reviewed. "
                    "Review again.",
                    409,
                    "storage-changed",
                )
            if store.full():
                return _too_many_reviews()
            token = store.issue(staged, fingerprint, identity, live)
            issued = True
            self._schedule_restore_expiry()
        finally:
            if staged is not None and not issued:
                await self._discard_held([_Orphan(staged)])
        held = store.entry(token)
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
        held = self._restore_plans.pop(token)
        if held is None:
            await self._drop_expired_restores()
            return _failure("Unknown restore review.", 410, "plan-expired")
        await self._discard_held([held])
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
        await self._drop_expired_restores()
        held, refused = self._peek_restore(token)
        if refused is not None:
            await self._drop_expired_restores()  # one peek found expired
            return refused
        db = self.db
        if held.db_identity != self._database_identity(db):
            await self._discard_popped(token)
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
            await self._discard_popped(token)
            return _failure(PLAN_CHANGED, 409, "plan-changed")
        # The token may have been used, expired or discarded meanwhile.
        held, refused = self._peek_restore(token)
        if refused is not None:
            await self._drop_expired_restores()
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
                runner=lambda job: self._run_restore(job, db, held, token),
            )
        except JobBusy as busy:
            return busy_response(busy)  # the token stays usable
        # Same loop turn as the peek and the start: the runner has not run.
        self._restore_plans.take(token, job.id)
        return web.json_response({"success": True, "job": job.summary()}, status=202)

    async def _run_restore(self, job, db, held, token=None):
        from .maintenance import _fail_job

        staged = held.staged
        keep_stage = False
        try:
            job.update(status_line="Waiting for other requests to finish")
            if not await self._jobs.drain_leases():
                # Nothing ran: the reviewed stage stays usable (#335 M2),
                # unless it expired meanwhile or its slot was reused.
                if token is not None and self._restore_plans.give_back(token, held):
                    keep_stage = True
                    self._schedule_restore_expiry()
                    job.scope["review_kept"] = True
                    return _fail_job(job, "busy", REVIEW_KEPT)
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
        except Exception:
            self.logger.exception("Restore job failed")
            return _fail_job(job, None, NOT_RESTORED)
        finally:
            if not keep_stage:
                await self._discard_held([_Orphan(staged)])
        # The copy replaced the library: the restore is done (#335 review I1).
        # Every later step only logs a failure.
        await self._after_publish(model.db_path)
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


class _Orphan:
    """A stage with no store entry, in the shape ``_discard_held`` takes."""

    def __init__(self, staged):
        self.staged = staged


def _too_many_reviews():
    return _failure(
        "Two restores are already being reviewed. Discard one first.",
        429,
        "too-large",
    )
