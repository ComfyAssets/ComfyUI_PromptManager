"""Delete or archive selected Library prompts, and restore archived ones (#443).

The workspace Prompt library's Delete… (D152). The legacy ``POST
/prompt_manager/bulk/delete`` and ``DELETE /prompt_manager/delete/{id}`` are
separate and unchanged.

- ``POST /prompt_manager/library/delete/preview {prompt_ids, expected_epoch}``
  classifies the selection (reads only, under a lease): the ids to ``delete``
  (in no saved pair), to ``archive`` (paired), the ``protected`` ones (tagged
  ``__protected__``, kept), counts of ``already_archived`` and ``not_found``,
  the ``image_links`` a delete removes, the safety ``backup`` policy and,
  when a backup would be taken, the ``disk`` it needs.
- ``POST /prompt_manager/library/delete {archive_ids, delete_ids,
  expected_epoch, backup}`` executes that review under a ``records`` hold
  (refused 409 ``busy`` beside a scan, restore, maintenance, move or
  Optimize). When a delete remains and a backup was promised (the reviewed
  ``backup`` was "on" or the policy is on now) it checks the disk (507
  ``insufficient-space``) and takes a verified safety backup (500
  ``backup-failed``); then one transaction deletes the reviewed deletes whose
  fingerprint is unchanged since the backup and archives the paired ones.
- ``POST /prompt_manager/library/restore {prompt_ids, expected_epoch}``
  removes the archive mark (under a lease).

A changed storage epoch answers 409 ``storage-changed``; nothing is written.
"""

import sqlite3

from aiohttp import web

try:
    from ...database.library_commands import LibraryCommandError
    from ...database.safety_backup import SafetyBackupFailed
except ImportError:
    from database.library_commands import LibraryCommandError
    from database.safety_backup import SafetyBackupFailed

from . import maintenance as _maintenance
from .admin import _prune_safety_backups
from .job_routes import busy_response
from .jobs import PROMPT_DELETE_JOB_KIND, JobBusy
from .prompts import parse_bulk_prompt_ids

PREVIEW_URL = "/prompt_manager/library/delete/preview"
DELETE_URL = "/prompt_manager/library/delete"
RESTORE_URL = "/prompt_manager/library/restore"
DELETE_TITLE = "Delete prompts (Library)"
_SELECTION_KEYS = frozenset({"prompt_ids", "expected_epoch"})
_DELETE_KEYS = frozenset({"archive_ids", "delete_ids", "expected_epoch", "backup"})
# #482: Delete permanently, with its digests; both or neither (a #443 body).
_PAIRED_KEYS = frozenset({"delete_paired_ids", "paired_digests"})
STORAGE_CHANGED = "Prompt storage was restored; refresh the library"


def _failure(status, code, error, **extra):
    return web.json_response(
        {"success": False, "code": code, "error": error, **extra}, status=status
    )


def _invalid(message):
    return _failure(400, "invalid-request", message)


def _storage_changed(exc):
    return _failure(409, exc.code, STORAGE_CHANGED, library_epoch=exc.epoch)


async def _body(request, keys, optional=frozenset()):
    """The JSON object with exactly ``keys`` (plus all or none of ``optional``)."""
    try:
        data = await request.json()
    except ValueError:
        raise ValueError("Expected a JSON object") from None
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    extra = set(data) - keys
    if not keys <= set(data) or (extra and extra != optional):
        raise ValueError("Request must contain exactly " + ", ".join(sorted(keys)))
    return data


def _paired_delete(data, archive_ids):
    """#482: the reviewed archive ids to delete with their pairs -> digest."""
    if "delete_paired_ids" not in data:
        return {}
    ids = _reviewed_ids(data["delete_paired_ids"], "delete_paired_ids")
    digests = data["paired_digests"]
    if not isinstance(digests, dict):
        raise ValueError("paired_digests must map prompt ids to digests")
    if not set(ids) <= set(archive_ids):
        raise ValueError("delete_paired_ids must be reviewed archive ids")
    chosen = {}
    for pid in ids:
        digest = digests.get(str(pid))
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("Every delete_paired_ids entry needs its review digest")
        chosen[pid] = digest
    return chosen


def _reviewed_ids(value, name):
    """A reviewed id list: possibly empty, else as ``parse_bulk_prompt_ids``."""
    if value == []:
        return []
    try:
        return parse_bulk_prompt_ids(value)
    except ValueError as exc:
        raise ValueError(str(exc).replace("prompt_ids", name)) from None


class LibraryDeleteRoutesMixin:
    """Mixin providing the workspace Library's delete, archive and restore."""

    def _register_library_delete_routes(self, routes):
        routes.post(PREVIEW_URL)(self.preview_library_delete)
        routes.post(DELETE_URL)(self.delete_library_prompts)
        routes.post(RESTORE_URL)(self.restore_library_prompts)

    async def preview_library_delete(self, request):
        try:
            data = await _body(request, _SELECTION_KEYS)
            ids = parse_bulk_prompt_ids(data["prompt_ids"])
        except ValueError as exc:
            return _invalid(str(exc))
        db = self.db
        policy = self._safety_backup_policy()
        try:
            async with self._jobs.lease("Library delete review"):
                review = await self._run_in_executor(
                    db.classify_library_delete,
                    ids,
                    expected_epoch=data["expected_epoch"],
                )
                disk = None
                if (review["delete"] or review["deletable"]) and policy == "on":
                    public, short, _size = await self._run_in_executor(
                        _maintenance.disk_plan,
                        db.model.db_path,
                        backup=True,
                        vacuum=False,
                    )
                    disk = {
                        **public,
                        "message": (
                            None
                            if short is None
                            else _maintenance.short_space_message(short)
                        ),
                    }
        except JobBusy as busy:
            return busy_response(busy)
        except LibraryCommandError as exc:
            return _storage_changed(exc)
        except ValueError as exc:
            return _invalid(str(exc))
        except (OSError, sqlite3.Error):
            self.logger.exception("Library delete review failed")
            return _failure(500, "review-failed", "The delete could not be reviewed")
        return web.json_response(
            {
                "success": True,
                "library_epoch": review["library_epoch"],
                "delete": review["delete"],
                "archive": review["archive"],
                "protected": review["protected"],
                "already_archived": len(review["already_archived"]),
                "not_found": len(review["not_found"]),
                "image_links": review["image_links"],
                # #482: the archive ids Delete permanently may delete with pairs.
                "deletable": review["deletable"],
                "review_digests": {
                    str(pid): digest for pid, digest in review["review_digests"].items()
                },
                "paired_counts": review["paired_counts"],
                "paired_image_links": review["paired_image_links"],
                "backup": policy,
                "disk": disk,
            }
        )

    async def delete_library_prompts(self, request):
        try:
            data = await _body(request, _DELETE_KEYS, _PAIRED_KEYS)
            archive_ids = _reviewed_ids(data["archive_ids"], "archive_ids")
            delete_ids = _reviewed_ids(data["delete_ids"], "delete_ids")
            if not archive_ids and not delete_ids:
                raise ValueError("Nothing to delete or archive")
            if len(archive_ids) + len(delete_ids) > 1000:
                raise ValueError("At most 1000 prompts can be changed at once")
            if set(archive_ids) & set(delete_ids):
                raise ValueError("A prompt cannot be both archived and deleted")
            if data["backup"] not in ("on", "off"):
                raise ValueError("backup must be on or off")
            paired = _paired_delete(data, archive_ids)
        except ValueError as exc:
            return _invalid(str(exc))
        busy = self._jobs.blocker(PROMPT_DELETE_JOB_KIND, admission="records")
        if busy is not None:
            return busy_response(JobBusy(busy))
        try:
            async with self._jobs.hold(
                PROMPT_DELETE_JOB_KIND, DELETE_TITLE, admission="records"
            ) as job:
                response = await self._held_library_delete(
                    self.db, data, archive_ids, delete_ids, paired
                )
                if response.status >= 400:
                    job.finish("failed")
                return response
        except JobBusy as busy:
            return busy_response(busy)

    async def _held_library_delete(self, db, data, archive_ids, delete_ids, paired):
        epoch = data["expected_epoch"]
        promised = data["backup"] == "on" or self._safety_backup_policy() == "on"
        backup = None
        try:
            candidates = None
            if delete_ids or paired:
                review = await self._run_in_executor(
                    db.classify_library_delete,
                    [*delete_ids, *paired],
                    expected_epoch=epoch,
                )
                candidates = review["fingerprints"]
                # #482: a permanent delete of a prompt in a saved pair is backed up too.
                if (candidates or review["deletable"]) and promised:
                    _disk, short, _size = await self._run_in_executor(
                        _maintenance.disk_plan,
                        db.model.db_path,
                        backup=True,
                        vacuum=False,
                    )
                    if short is not None:
                        return _failure(
                            507,
                            "insufficient-space",
                            _maintenance.short_space_message(short),
                        )
                    backup = await self._run_in_executor(
                        _maintenance.take_safety_backup, db.model
                    )
            result = await self._run_in_executor(
                db.archive_or_delete_library_prompts,
                archive_ids,
                delete_ids,
                expected_epoch=epoch,
                candidates=candidates if candidates is not None else {},
                delete_paired=paired,
            )
        except LibraryCommandError as exc:
            return _storage_changed(exc)
        except SafetyBackupFailed as error:
            self.logger.error("Safety backup before a Library delete failed")
            return _failure(
                500, "backup-failed", _maintenance.backup_failed_message(error)
            )
        except ValueError as exc:
            return _invalid(str(exc))
        except (OSError, sqlite3.Error):
            self.logger.exception("Library delete failed")
            return _failure(500, "delete-failed", "The prompts could not be changed")
        finally:
            if backup is not None:
                await self._prune_after_delete(db)
        return web.json_response({"success": True, **result, "backup": backup})

    async def _prune_after_delete(self, db):
        try:
            await self._run_in_executor(_prune_safety_backups, db.model.db_path)
        except Exception:  # pruning old copies never fails the delete
            self.logger.exception("Could not prune old safety backups")

    async def restore_library_prompts(self, request):
        try:
            data = await _body(request, _SELECTION_KEYS)
            ids = parse_bulk_prompt_ids(data["prompt_ids"])
        except ValueError as exc:
            return _invalid(str(exc))
        try:
            async with self._jobs.lease("Library restore"):
                result = await self._run_in_executor(
                    self.db.restore_library_prompts,
                    ids,
                    expected_epoch=data["expected_epoch"],
                )
        except JobBusy as busy:
            return busy_response(busy)
        except LibraryCommandError as exc:
            return _storage_changed(exc)
        except ValueError as exc:
            return _invalid(str(exc))
        except (OSError, sqlite3.Error):
            self.logger.exception("Library restore failed")
            return _failure(500, "restore-failed", "The prompts could not be restored")
        return web.json_response({"success": True, **result})
