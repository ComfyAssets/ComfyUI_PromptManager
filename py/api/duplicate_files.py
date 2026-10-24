"""Duplicate image files on the job registry (#249 System S6, Task 17).

- ``POST /prompt_manager/jobs/duplicates`` starts the find (202 ``{job}``):
  kind ``duplicates``, "Find duplicate image files", admission ``read``,
  cancellable, outcome group ``groups_found``. It hashes the media files of
  every effective source (``_hash_media_groups``, shared with the legacy
  scan) and the server keeps the groups it found under the job id: content
  hash, root-relative paths and each file's source index. A find is refused
  while a duplicate removal runs, and one find runs at a time.
- ``GET /prompt_manager/maintenance/duplicates/{job_id}?offset&limit`` pages
  those groups, at most ``MAX_GROUP_PAGE`` at a time: ``{success, job:{id,
  state}, total, offset, limit, groups:[{group_id, size, count, files}]}``.
  Each file names its root-relative ``path``, its source's public label and
  index and the Images serve URLs; never an absolute path.
- ``POST /prompt_manager/maintenance/preview {operation:"duplicate_files",
  options:{job_id, keep:[{group_id, root_index, path}]}}`` reviews a removal:
  in each group named, the files kept are the ones listed and every other
  copy is removed. Each keeper and each removal is checked against the held
  group, with containment decided as text before any filesystem access
  (``file_problem``): inside its source, a media file outside
  ``thumbnails``, then a regular file (not a link) whose resolved path is
  still inside the source, with the same size and content hash. A removal
  that fails, or whose kept copy fails, is protected and listed with the
  reason. The plan carries the files' database links, which move to the
  kept copy.
- ``POST /prompt_manager/maintenance/apply {token}`` runs the reviewed plan
  as the ``duplicate_cleanup`` job: admission ``records``, not cancellable,
  groups ``removed``, ``kept`` and ``errors``. It checks the sources are
  unchanged, takes the safety backup the policy promises (the database only;
  removed files are not in it), then for each removal rechecks its kept copy
  and the file itself (``file_problem``) and removes it with every handle
  closed (``_unlink``), together with its thumbnail. A file that changed is
  kept; the first unlink that fails stops the job, a locked file with the
  coded ``LOCKED_FILE`` message (never the OS text), and the files after it
  are not attempted. The database links of the files actually removed then
  move to their kept copy (``PromptDatabase.move_file_links``).

The legacy gallery routes (``/scan_duplicates``, its stream and status, and
``/delete_duplicate_images``) keep their shapes on the same registry kinds;
see ``py/api/admin.py``.
"""

import asyncio
import os
from pathlib import Path

from aiohttp import web

from .admin import DUPLICATE_FIND_LEGACY_TITLE, _remove_thumbnail
from .duplicate_review import (  # noqa: F401 (CHANGED, OUTSIDE_SOURCE: public)
    CHANGED,
    DELETE_FAILED,
    FIND_CANCELLED,
    FIND_ERROR_EVENT,
    FIND_FAILED,
    GONE,
    KEEPER,
    KEEPER_CHANGED,
    KEPT_COPY,
    LINKS_FAILED,
    LOCKED_FILE,
    NOT_ATTEMPTED,
    OUTSIDE_SOURCE,
    ROW_DETAILS,
    SOURCES_CHANGED,
    SOURCES_CHANGED_REVIEW,
    HeldFind,
    SourcesChanged,
    _fingerprint,
    _group_order,
    _identity,
    _parse_options,
    _plural,
    _public_plan,
    _select,
    file_problem,
    human_bytes,
)
from .images import _favorite_relative_key, _favorite_root_key, _output_image_entry
from .job_routes import busy_response
from .jobs import (
    DUPLICATE_CLEANUP_GROUP_NAMES,
    DUPLICATE_CLEANUP_JOB_KIND,
    DUPLICATE_GROUP_NAMES,
    DUPLICATES_JOB_KIND,
    JobBusy,
)
from .maintenance import DUPLICATE_FILES, _fail_job, _failure, _invalid, backup_note

try:
    from ...database.maintenance import FILE_LINK_SQL
except ImportError:
    from database.maintenance import FILE_LINK_SQL

DUPLICATE_FIND_TITLE = "Find duplicate image files"
DUPLICATE_CLEANUP_TITLE = "Remove duplicate image files"
MAX_GROUP_PAGE = 10  # groups a page lists (D69: ten at a time)
MAX_HELD_FINDS = 3  # finds whose groups the server keeps for review
favorite_root_key = _favorite_root_key


def query_plan_sql():
    """The link reconciliation's statements (EXPLAIN QUERY PLAN checks)."""
    return list(FILE_LINK_SQL)


def _unlink(path):
    """Remove one file (the caller closed every handle on it)."""
    os.remove(path)


class DuplicateFilesMixin:
    """Mixin providing the duplicate-file find, its groups and its removal."""

    def _register_duplicate_routes(self, routes):
        @routes.post("/prompt_manager/jobs/duplicates")
        async def start_duplicate_find_route(request):
            return await self.start_duplicate_find(request)

        @routes.get("/prompt_manager/maintenance/duplicates/{job_id}")
        async def duplicate_groups_route(request):
            return await self.duplicate_groups(request)

    # ── find ──────────────────────────────────────────────────────────

    async def start_duplicate_find(self, request):
        """Start the duplicate find (202), or 409 naming the job in the way."""
        try:
            job = self._start_duplicate_find(origin="workspace")
        except JobBusy as busy:
            return busy_response(busy)
        return web.json_response({"success": True, "job": job.summary()}, status=202)

    def _start_duplicate_find(self, origin):
        """Register a find job (on the loop); raises JobBusy.

        A workspace find walks every effective source; a gallery one the
        gallery's output folder, as the legacy scan did. Both resolve their
        folders when the job begins, so a source change later cannot move it.
        """
        cleanup = self._jobs.active(DUPLICATE_CLEANUP_JOB_KIND)
        if cleanup is not None:
            raise JobBusy(cleanup)
        held = HeldFind(origin)
        if origin == "workspace":
            title = DUPLICATE_FIND_TITLE
        else:
            title = DUPLICATE_FIND_LEGACY_TITLE
        job = self._jobs.start(
            DUPLICATES_JOB_KIND,
            title,
            admission="read",
            cancellable=True,
            groups=DUPLICATE_GROUP_NAMES,
            runner=lambda job: self._run_duplicate_find(job, held),
        )
        held.job_id = job.id
        self._remember_find(held)
        return job

    def _remember_find(self, held):
        finds = self._duplicate_finds
        finds[held.job_id] = held
        for job_id in list(finds):
            if self._jobs.get(job_id) is None:
                del finds[job_id]
        while len(finds) > MAX_HELD_FINDS:
            del finds[next(iter(finds))]

    def _find_covers(self, job, legacy_dir):
        held = self._duplicate_finds.get(job.id)
        return held is not None and held.covers(legacy_dir)

    def _latest_duplicate_find(self):
        """The running find, else the newest finished one this session."""
        for job in self._jobs.list():
            if job.kind == DUPLICATES_JOB_KIND and job.id in self._duplicate_finds:
                return job
        return None

    def _resolve_find_roots(self, held):
        """Fill in where a find looks (blocking): sources and the gallery folder."""
        listed = [os.path.abspath(str(d)) for d in self._get_all_output_dirs()]
        held.legacy_dir = self._find_comfyui_output_dir()
        if held.origin == "workspace":
            held.roots = list(enumerate(listed))
            return
        if not held.legacy_dir or not os.path.isdir(held.legacy_dir):
            self.logger.warning("ComfyUI output directory not found")
            held.roots = []
            return
        legacy = os.path.abspath(held.legacy_dir)
        index = next(
            (i for i, d in enumerate(listed) if _identity(d) == _identity(legacy)),
            None,
        )
        held.roots = [(index, legacy)]

    def _find_progress(self, job, done, total):
        if done < job.processed:
            return  # hashing threads report out of order; keep the count rising
        pct = int(done / total * 100) if total else 100
        job.update(
            processed=done,
            total=total,
            status_line=f"Hashing file {done:,} of {total:,}",
        )
        job.publish(
            {
                "type": "progress",
                "progress": pct,
                "status": f"Hashing file {done}/{total}...",
                "processed": done,
                "found": 0,
            }
        )

    async def _run_duplicate_find(self, job, held):
        loop = asyncio.get_running_loop()

        def report(done, total):  # from the hashing threads
            loop.call_soon_threadsafe(self._find_progress, job, done, total)

        try:
            job.update(status_line="Looking for media files")
            job.publish(
                {
                    "type": "progress",
                    "progress": 0,
                    "status": "Collecting media files...",
                    "processed": 0,
                    "found": 0,
                }
            )
            await self._run_in_executor(self._resolve_find_roots, held)
            job.scope["output_folders"] = len(held.roots)
            job.log_line(f"Searching {_plural(len(held.roots), 'source')}")
            groups = await self._run_in_executor(
                self._hash_media_groups,
                [root for _index, root in held.roots],
                report,
                lambda: job.cancelled,
            )
            if job.cancelled:
                job.update(status_line="Cancelled; nothing was changed.")
                job.publish({"type": "error", "message": FIND_CANCELLED})
                job.finish("cancelled")
                return
            await self._finish_find(job, held, groups)
        except Exception:
            self.logger.exception("Duplicate search failed")
            job.update(status_line=FIND_FAILED)
            job.publish({"type": "error", "message": FIND_ERROR_EVENT})
            job.finish("failed")

    async def _finish_find(self, job, held, groups):
        for group in groups:
            group["group_id"] = group["hash"]
            for record in group["files"]:
                index, root = held.roots[record.pop("slot")]
                record["root_index"] = index
                record["root"] = root
        groups.sort(key=_group_order)
        held.groups = groups
        labels = {root: self._public_path(root) for _index, root in held.roots}
        rows = [
            {
                "path": group["files"][0]["rel"],
                "root": labels[group["files"][0]["root"]],
                "root_index": group["files"][0]["root_index"],
                "detail": f"{len(group['files'])} identical files · "
                f"{human_bytes(group['size'])} each",
            }
            for group in groups
        ]
        job.record_counted("groups_found", len(groups), rows)
        line = f"Found {_plural(len(groups), 'group')} of duplicate files"
        job.log_line(line)
        legacy = []
        if held.covers(held.legacy_dir) and held.legacy_dir:
            legacy = await self._run_in_executor(
                self._legacy_duplicate_groups, groups, held.legacy_dir
            )
        # Every file was read by now (the last reports may still be queued).
        job.update(progress=100, status_line=line, processed=job.total)
        job.publish(
            {
                "type": "complete",
                "processed": job.processed,
                "found": len(legacy),
                "duplicates": legacy,
                "message": f"Found {len(legacy)} groups of duplicate images",
            }
        )

    # ── groups ────────────────────────────────────────────────────────

    def _held_result(self, job_id):
        """``(held, None)`` for a finished find's groups, else an error reply."""
        held = self._duplicate_finds.get(job_id)
        job = self._jobs.get(job_id)
        if held is None or job is None or job.kind != DUPLICATES_JOB_KIND:
            return None, _failure(
                "No duplicate search with this id. Search again.", 404, "not-found"
            )
        if job.running:
            return None, _failure(
                "The duplicate search is still running.", 409, "job-running"
            )
        if held.groups is None:
            return None, _failure(
                "This search did not finish. Search again.", 409, "no-groups"
            )
        return held, None

    async def duplicate_groups(self, request):
        """One page of a finished find's groups (at most MAX_GROUP_PAGE)."""
        try:
            offset = int(request.query.get("offset", "0"))
            limit = int(request.query.get("limit", str(MAX_GROUP_PAGE)))
        except ValueError:
            return _invalid("offset and limit must be whole numbers")
        if offset < 0 or not 0 < limit <= MAX_GROUP_PAGE:
            return _invalid(f"limit must be 1 to {MAX_GROUP_PAGE}; offset 0 or more")
        job_id = request.match_info["job_id"]
        held, error = self._held_result(job_id)
        if error is not None:
            return error
        page = held.groups[offset : offset + limit]
        groups = await self._run_in_executor(self._describe_groups, page)
        job = self._jobs.get(job_id)
        return web.json_response(
            {
                "success": True,
                "job": {"id": job.id, "state": job.state},
                "total": len(held.groups),
                "offset": offset,
                "limit": limit,
                "groups": groups,
            },
            headers={"Cache-Control": "no-store"},
        )

    def _describe_groups(self, groups):
        """Public group rows (blocking: one stat per file)."""
        return [
            {
                "group_id": group["group_id"],
                "size": group["size"],
                "count": len(group["files"]),
                "files": [self._describe_copy(record) for record in group["files"]],
            }
            for group in groups
        ]

    def _describe_copy(self, record):
        label = self._public_path(record["root"])
        base = {
            "filename": os.path.basename(record["rel"]),
            "path": record["rel"],
            "root": label,
            "root_index": record["root_index"],
            "url": None,
            "thumbnail_url": None,
            "size": record["size"],
            "modified_time": record["mtime"],
            "media_type": "image",
            "missing": False,
        }
        if record["root_index"] is None:
            return base
        try:
            entry = _output_image_entry(
                Path(record["abs"]), Path(record["root"]), record["root_index"], label
            )
        except (OSError, ValueError):
            return {**base, "missing": True}
        return {
            **base,
            "url": entry["url"],
            "thumbnail_url": entry["thumbnail_url"],
            "size": entry["size"],
            "modified_time": entry["modified_time"],
            "media_type": entry["media_type"],
        }

    # ── review ────────────────────────────────────────────────────────

    def _listed_sources(self):
        """``{identity: index}`` of the effective sources now (blocking)."""
        return {
            _identity(d): index for index, d in enumerate(self._get_all_output_dirs())
        }

    @staticmethod
    def _source_listed(listed, index, root):
        return index is not None and listed.get(_identity(root)) == index

    async def preview_duplicate_files(self, options):
        """The ``duplicate_files`` plan for one finished find and its keepers."""
        job_id, keep, message = _parse_options(options)
        if message:
            return _invalid(message)
        held, error = self._held_result(job_id)
        if error is not None:
            return error
        selection, message = _select(held, keep)
        if message:
            return _invalid(message)
        db = self.db
        identity = self._database_identity(db)
        try:
            review = await self._run_in_executor(
                self._review_duplicate_files, db, selection
            )
        except SourcesChanged:
            return _failure(SOURCES_CHANGED_REVIEW, 409, "sources-changed")
        except Exception:
            self.logger.exception("Duplicate file review failed")
            return _failure("Failed to review maintenance", 500)
        return await self._plan_response(
            db,
            identity,
            DUPLICATE_FILES,
            review["fingerprint"],
            review["public"],
            {
                "targets": review["targets"],
                "kept": review["kept"],
                "protected": review["public"]["protected"]["count"],
            },
        )

    def _review_duplicate_files(self, db, selection):
        """Check every keeper and removal now (blocking); the plan's parts."""
        listed = self._listed_sources()
        targets, kept, protected = [], [], {}
        hasher = self._calculate_file_hash
        for group, keepers in selection.values():
            removals = [r for r in group["files"] if r not in keepers]
            for record in removals:
                if record["root_index"] is not None and not self._source_listed(
                    listed, record["root_index"], record["root"]
                ):
                    raise SourcesChanged(record["root"])
            keeper = next(
                (
                    k
                    for k in keepers
                    if file_problem(
                        k["abs"], k["root"], group["hash"], k["size"], hasher
                    )
                    is None
                ),
                None,
            )
            reviewed = []
            for record in removals:
                if keeper is None:
                    problem = KEEPER
                elif record["root_index"] is None:
                    problem = OUTSIDE_SOURCE
                else:
                    problem = file_problem(
                        record["abs"],
                        record["root"],
                        group["hash"],
                        record["size"],
                        hasher,
                    )
                if problem:
                    protected[problem] = protected.get(problem, 0) + 1
                    continue
                reviewed.append(self._target(group, record, keeper))
            if reviewed:
                targets.extend(reviewed)
                kept.append(self._copy_row(keeper, KEPT_COPY))
        links = db.count_file_links([t["abs"] for t in targets]) if targets else {}
        return {
            "targets": targets,
            "kept": kept,
            "fingerprint": _fingerprint(targets),
            "public": _public_plan(targets, protected, links),
        }

    def _target(self, group, record, keeper):
        return {
            "group_id": group["group_id"],
            "hash": group["hash"],
            "size": record["size"],
            "root_index": record["root_index"],
            "root": record["root"],
            "root_label": self._public_path(record["root"]),
            "abs": record["abs"],
            "rel": record["rel"],
            "keeper_abs": keeper["abs"],
            "keeper_rel": keeper["rel"],
            "keeper_root": keeper["root"],
            "keeper_size": keeper["size"],
        }

    def _copy_row(self, record, detail):
        return {
            "path": record["rel"],
            "root": self._public_path(record["root"]),
            "root_index": record["root_index"],
            "detail": detail,
        }

    # ── removal ───────────────────────────────────────────────────────

    def _start_duplicate_cleanup(self, db, plan):
        """Register the removal job for a reviewed plan (on the loop)."""
        find = self._jobs.active(DUPLICATES_JOB_KIND)
        if find is not None:
            raise JobBusy(find)
        return self._jobs.start(
            DUPLICATE_CLEANUP_JOB_KIND,
            DUPLICATE_CLEANUP_TITLE,
            admission="records",
            cancellable=False,
            scope={"operation": DUPLICATE_FILES},
            groups=DUPLICATE_CLEANUP_GROUP_NAMES,
            runner=lambda job: self._run_duplicate_cleanup(job, db, plan),
        )

    def _sources_unchanged(self, targets):
        listed = self._listed_sources()
        return all(
            self._source_listed(listed, t["root_index"], t["root"]) for t in targets
        )

    async def _run_duplicate_cleanup(self, job, db, plan):
        targets = plan.payload["targets"]
        unchanged = await self._run_in_executor(self._sources_unchanged, targets)
        if not unchanged:
            return _fail_job(job, "sources-changed", SOURCES_CHANGED)
        ok, backup = await self._safety_backup_step(job, db, plan)
        if not ok:
            return
        try:
            await self._remove_duplicates(job, db, plan, backup)
        finally:
            if backup:
                await self._prune_backup_pool(job, db)

    async def _remove_duplicates(self, job, db, plan, backup):
        targets = plan.payload["targets"]
        for row in plan.payload["kept"]:
            job.record("kept", row, f"= {row['root']}/{row['path']} · {KEPT_COPY}")
        job.update(total=len(targets), status_line="Removing duplicate files")
        removed, failure, keepers = [], None, {}
        for done, target in enumerate(targets, start=1):
            if failure is not None:
                self._record_target(job, "kept", target, NOT_ATTEMPTED)
                continue
            outcome, code = await self._run_in_executor(
                self._remove_one_duplicate, target, keepers
            )
            if outcome == "removed":
                removed.append(target)
                detail = f"A copy of {target['keeper_rel']}"
                self._record_target(job, "removed", target, detail, "-")
            elif outcome == "kept":
                self._record_target(job, "kept", target, ROW_DETAILS[code])
            else:
                failure = code
                self._record_target(job, "errors", target, code, "!")
            job.update(processed=done, progress=int(done / len(targets) * 100))
        links_ok = await self._move_links(job, db, removed)
        if removed:
            self.invalidate_gallery_cache()
        line = f"Removed {_plural(len(removed), 'duplicate file')}"
        job.log_line(line)
        if failure is not None or not links_ok:
            line = "Stopped after an error · " + line if failure else line
            job.update(progress=100, status_line=f"{line} · {backup_note(backup)}")
            job.publish({"type": "error", "code": "delete-failed", "message": line})
            job.finish("failed")
            return
        job.update(progress=100, status_line=f"{line} · {backup_note(backup)}")

    @staticmethod
    def _record_target(job, group, target, detail, mark="="):
        row = {
            "path": target["rel"],
            "root": target["root_label"],
            "root_index": target["root_index"],
            "detail": detail,
        }
        job.record(
            group, row, f"{mark} {target['root_label']}/{target['rel']} · {detail}"
        )

    def _remove_one_duplicate(self, target, keepers):
        """``(outcome, detail or code)`` for one reviewed removal (blocking).

        ``keepers`` caches each group's kept-copy check for this job.
        """
        hasher = self._calculate_file_hash
        group_id = target["group_id"]
        if group_id not in keepers:
            keepers[group_id] = (
                file_problem(
                    target["keeper_abs"],
                    target["keeper_root"],
                    target["hash"],
                    target["keeper_size"],
                    hasher,
                )
                is None
            )
        if not keepers[group_id]:
            return "kept", KEEPER
        problem = file_problem(
            target["abs"], target["root"], target["hash"], target["size"], hasher
        )
        if problem:
            return "kept", problem
        try:
            _unlink(target["abs"])
        except FileNotFoundError:
            return "kept", GONE
        except PermissionError:
            self.logger.warning(f"Duplicate file is locked: {target['rel']}")
            return "error", LOCKED_FILE
        except OSError:
            self.logger.warning(
                f"Could not delete duplicate file {target['rel']}", exc_info=True
            )
            return "error", DELETE_FAILED
        self.logger.info(f"Deleted duplicate file: {target['rel']}")
        _remove_thumbnail(Path(target["root"]), Path(target["rel"]), self.logger)
        return "removed", None

    async def _move_links(self, job, db, removed):
        """Move the removed files' links to their kept copy; False on failure."""
        if not removed:
            return True
        moves = [(t["abs"], t["keeper_abs"]) for t in removed]
        favorites = [
            (
                _favorite_root_key(t["root"]),
                _favorite_relative_key(t["rel"]),
                _favorite_root_key(t["keeper_root"]),
                _favorite_relative_key(t["keeper_rel"]),
            )
            for t in removed
        ]
        try:
            counts = await self._run_in_executor(db.move_file_links, moves, favorites)
        except Exception:
            self.logger.exception("Moving duplicate file links failed")
            job.record(
                "errors", {"path": "", "detail": LINKS_FAILED}, f"! {LINKS_FAILED}"
            )
            return False
        links = counts["moved"] + counts["merged"]
        job.log_line(f"Moved {_plural(links, 'database link')} to the kept copies")
        if counts["favorites"]:
            job.log_line(
                f"Moved {_plural(counts['favorites'], 'favorite')} to the kept copies"
            )
        return True
