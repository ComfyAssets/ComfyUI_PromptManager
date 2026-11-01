"""Move thumbnails (#320 Part 3): the reviewed job that changes where previews
live, the removal of old copies after it, recovery at startup and the
"Remove thumbnails of removed sources" scope (Part 5).

Kind ``thumbnails`` with ``mode: "move"`` (D1A): admission ``guarded``, so
scans, deletes, tagging, source changes and every other thumbnail job wait
for it, while the monitor's Needs review recording runs beside it.
Automatic previews are held for the whole job (D2A, ``_auto_thumbnail_blocker``).

Phases: review (``POST /images/thumbnails/plan``, no writes but the
validation probe), start (a plan token), hold (generation bump, journal,
target folder), copy (cancellable at every file boundary, on the job pool
in small chunks, D3A), switch (one config commit sets the location and
records the old stores under ``cleanup``; not cancellable from here),
remove old copies, end. Per-file errors switch anyway (D10A); a folder-wide
error or a cancel before the switch removes what the job wrote and changes
nothing. The move is the only writer of the setting (D4B).

Moving away from a thumbnail folder also removes the previews of this
install's sources that are no longer listed (Q320-8: remove).
"""

import functools
import os
import time
from pathlib import Path

from aiohttp import web

from . import preview_location, preview_store
from . import thumbnail_move_files as files
from .jobs import THUMBNAILS_JOB_KIND
from .thumbnail_move_plan import (
    MOVE_GROUPS,
    MOVE_TITLE,
    PLAN_TTL,
    _number,
    _plural,
    _remove,
    _roots_revision,
    _volume,
    _is_network,
    _write_json,
)
from . import thumbnail_move_plan as move_plan

try:
    from ...utils import exit_signal
    from ...utils.parallel import map_parallel
except ImportError:  # tests import the package top-level
    from utils import exit_signal
    from utils.parallel import map_parallel


class ThumbnailMoveMixin:
    """Start and run the move job; the removal of old copies."""

    # ── start ─────────────────────────────────────────────────────────

    def _take_move_plan(self, body):
        """The plan a start names, or ``(status, code)``."""
        token = body.get("token")
        plan = (self._move_plans or {}).get(token) if isinstance(token, str) else None
        if plan is None or time.monotonic() - plan.created > PLAN_TTL:
            return 409, "plan-stale"
        roots = [str(root) for root in self._get_all_output_dirs()]
        if _roots_revision(roots) != plan.revision:
            return 409, "plan-stale"
        if preview_store.setting() != plan.old:
            return 409, "plan-stale"
        if plan.confirm_required and body.get("confirmed") is not True:
            return 400, "confirmation-required"
        return plan

    def start_thumbnail_move(self, body):
        """Start the move job (sync, on the loop); raises JobBusy. Returns
        the job or a refusal response."""
        if not self._move_recovered:
            blocker = self._jobs.active(THUMBNAILS_JOB_KIND)
            return web.json_response(
                {
                    "success": False,
                    "code": "busy",
                    "error": "Prompt Manager is still checking an earlier move. "
                    "Try again in a moment.",
                    "running": None if blocker is None else {"id": blocker.id},
                },
                status=409,
            )
        taken = self._take_move_plan(body)
        if isinstance(taken, tuple):
            status, code = taken
            text = {
                "plan-stale": "The review is out of date. Review the move again.",
                "confirmation-required": "Confirm that Prompt Manager may "
                "create its folders there.",
            }[code]
            return web.json_response(
                {"success": False, "code": code, "error": text}, status=status
            )
        plan = taken
        self._move_plans.pop(plan.token, None)
        return self._jobs.start(
            THUMBNAILS_JOB_KIND,
            MOVE_TITLE,
            admission="guarded",
            cancellable=True,
            scope={
                "mode": "move",
                "from": plan.old.mode,
                "to": plan.target["mode"],
                "to_path": plan.target["path"],
            },
            groups=MOVE_GROUPS,
            runner=functools.partial(self._run_thumbnail_move, plan=plan),
        )

    # ── the job ───────────────────────────────────────────────────────

    async def _run_thumbnail_move(self, job, *, plan):
        state = {"written": [], "created": [], "created_folder": False}
        try:
            await self._run_in_executor(self._move_hold, job, plan, state)
        except (OSError, ValueError) as error:
            await self._run_in_executor(self._move_rollback, plan, state)
            self._end_move(
                job, "failed", "The new folder could not be made; nothing changed."
            )
            self.logger.warning(f"Move thumbnails could not start: {error}")
            return
        left = [
            {
                "path": "/".join(item),
                "root": s.label,
                "detail": "Not a thumbnail; left in place",
            }
            for s in plan.sources
            if s.scan is not None
            for item in s.scan.left
        ]
        if left:
            job.record_counted("left", len(left), left)
        items = [
            (source, item)
            for source in plan.sources
            if source.scan is not None
            for item in source.scan.copy
        ]
        total = len(items)
        job.update(total=total, processed=0, progress=0)
        chunk = await self._run_in_executor(self._move_chunk, plan)
        workers = self._move_workers()
        done = 0
        for start in range(0, total, chunk):
            if job.cancelled:
                await self._move_cancelled(job, plan, state)
                return
            batch = items[start : start + chunk]
            try:
                results = await self._run_in_executor(
                    map_parallel,
                    functools.partial(self._move_copy_one, state=state),
                    batch,
                    workers,
                )
            except exit_signal.ExitRequested:
                return  # exiting: the next start undoes the copies (journal)
            except files.FolderGone as error:
                self.logger.warning(f"Move thumbnails stopped: {error}")
                await self._run_in_executor(self._move_rollback, plan, state)
                self._end_move(
                    job,
                    "failed",
                    "The new folder could not be reached or is full; nothing changed.",
                )
                return
            except Exception:
                # Anything else before the switch: undo, never half a move.
                self.logger.exception("Move thumbnails failed while copying")
                await self._run_in_executor(self._move_rollback, plan, state)
                self._end_move(job, "failed", "The copy failed; nothing changed.")
                return
            for (source, item), (outcome, detail) in zip(batch, results):
                row = {
                    "path": "/".join(item[0]),
                    "root": source.label,
                    "detail": detail,
                }
                job.record(
                    "moved" if outcome == "moved" else outcome,
                    row,
                    f"{outcome}: {source.label} · {'/'.join(item[0])}",
                )
            done += len(batch)
            label = batch[-1][0].label
            job.update(
                processed=done,
                progress=int(done * 90 / total) if total else 90,
                status_line=(
                    f"Copying {_number(done)} of {_number(total)} previews · {label}"
                ),
            )
            job.publish({"type": "progress", "summary": job.summary()})
        if job.cancelled:
            await self._move_cancelled(job, plan, state)
            return
        # The switch: not cancellable from here (an exit still stops between
        # files and leaves the cleanup to the next start).
        job.cancellable = False
        job.update(status_line="Switching to the new location", progress=92)
        job.publish({"type": "progress", "summary": job.summary()})
        try:
            await self._run_in_executor(self._move_switch, plan)
        except Exception:
            self.logger.exception("Move thumbnails could not switch")
            await self._run_in_executor(self._move_rollback, plan, state)
            self._end_move(
                job, "failed", "The setting could not be saved; nothing changed."
            )
            return
        await self._remove_old_copies(job, plan)
        moved = job.counters.get("moved", 0) + job.counters.get("skipped", 0)
        errors = job.counters.get("errors", 0)
        where = plan.target["path"] or "inside each source"
        parts = [f"{_number(moved)} moved"]
        if errors:
            parts.append(_plural(errors, "error"))
        parts.append(
            "old copies removed"
            if not job.scope.get("cleanup_left")
            else "some old copies are removed at the next start"
        )
        self._end_move(
            job, "finished", f"Thumbnails moved to {where}: " + " · ".join(parts) + "."
        )

    def _end_move(self, job, state, line):
        job.update(status_line=line, progress=100)
        job.log_line(line)
        job.publish({"type": "complete", "summary": job.summary()})
        job.finish(state)

    def _move_workers(self):
        from .images import _worker_threads

        return max(1, int(_worker_threads() or 1))

    def _move_chunk(self, plan):
        """2 files at a time across volumes or to/from a share, else 4, never
        more than Worker threads (D3A: the pool keeps its size)."""
        sides = [plan.target["path"] or ""] + [s.old_store or "" for s in plan.sources]
        sides += [s.new_store for s in plan.sources]
        network = any(_is_network(side) for side in sides if side)
        volumes = {_volume(side) for side in sides if side}
        size = 2 if network or len(volumes) > 1 else 4
        return max(1, min(size, self._move_workers()))

    def _move_hold(self, job, plan, state):
        """Phase 3: bump the generation, write the journal, make the target."""
        preview_store.bump_generation()
        if plan.to_folder:
            folder = plan.target["path"]
            if not os.path.isdir(folder):
                if plan.target_default:
                    os.makedirs(folder, exist_ok=True)
                else:
                    os.mkdir(folder)
                state["created_folder"] = True
            plan.target_id = preview_location.init_folder(
                folder, created_by_pm=state["created_folder"]
            )
            install = plan.install

            def add_keys(marker):
                for source in plan.sources:
                    if source.key and source.key not in marker["keys"]:
                        marker["keys"][source.key] = {
                            "path": source.root,
                            "identity": os.path.normcase(os.path.realpath(source.root)),
                            "dev_ino": preview_location.file_identity(source.root),
                            "install": install,
                        }

            with preview_store.write_lock:
                preview_location.write_marker(folder, add_keys)
        _write_json(
            move_plan.journal_path(),
            {
                "state": "copying",
                "from": plan.old.to_config(),
                "to": plan.target,
                "stores": [s.new_store for s in plan.sources],
                # Stores that do not exist yet are made by this job only:
                # after a crash everything in them goes (B4).
                "created_dirs": [
                    s.new_store for s in plan.sources if not os.path.isdir(s.new_store)
                ],
                "created_folder": state["created_folder"],
                "install": plan.install,
            },
        )
        job.log_line(
            f"Move thumbnails started: {plan.old.path or 'inside each source'} → "
            f"{plan.target['path'] or 'inside each source'}"
        )

    def _move_copy_one(self, pair, *, state):
        """Copy one preview (a pool thread); ``(group, detail)``."""
        source, (relative, size, mtime_ns) = pair
        destination = Path(source.new_store).joinpath(*relative)
        try:
            if not os.path.isdir(destination.parent):
                base = Path(source.new_store)
                if not base.is_dir():
                    if plan_parent_missing(base):
                        raise files.FolderGone("the new folder is gone")
                    created = []
                    files._make_folders(base.parent, (base.name,), created)
                    state["created"].extend(created)
                created = []
                files._make_folders(base, relative[:-1], created)
                state["created"].extend(created)
            # Also when the folders already exist (Codex P1 on #592).
            files.refuse_links(source.new_store, relative[:-1])
            outcome = files.copy_one(
                Path(source.old_store).joinpath(*relative),
                destination,
                size,
                mtime_ns,
                state["created"],
            )
        except files.CopyFailed as error:
            return "errors", str(error)
        if outcome == "moved":
            state["written"].append(str(destination))
            return "moved", "Copied and checked"
        return "skipped", "Already there"

    async def _move_cancelled(self, job, plan, state):
        if exit_signal.requested():
            return  # the next start removes the partial copies (journal)
        await self._run_in_executor(self._move_rollback, plan, state)
        self._end_move(job, "cancelled", "Move cancelled: nothing changed.")

    def _move_rollback(self, plan, state):
        """Remove what this job wrote (files, folders it made, a folder and
        marker it created) and the journal: nothing changed."""
        for path in state["written"]:
            files.remove_file(path)
        for folder in reversed(state["created"]):
            files.remove_empty_folders(folder)
        if plan.to_folder and state.get("created_folder"):
            folder = plan.target["path"]
            try:
                names = os.listdir(folder)
            except OSError:
                names = None
            if names is not None and set(names) <= {preview_location.MARKER_NAME}:
                _remove(preview_location.marker_path(folder))
                files.remove_empty_folders(folder)
        _remove(move_plan.journal_path())

    def _move_switch(self, plan):
        """Phase 5: one config commit sets the location and records the old
        stores under ``cleanup``; the runtime follows under the write lock."""
        from ..config import PromptManagerConfig
        from ..config_store import get_config_store

        entry = {
            "from": plan.old.mode,
            "folder": plan.old.path if plan.from_folder else None,
            "stores": [s.old_store for s in plan.sources if s.old_store],
            "drop_keys": self._old_keys_to_drop(plan),
            "install": plan.install,
        }
        # An older entry never removes a store the move just filled (a move
        # back while old copies still waited).
        filled = {os.path.normcase(s.new_store) for s in plan.sources}
        cleanup = []
        for old_entry in plan.old.cleanup:
            stores = [
                store
                for store in old_entry.get("stores") or []
                if isinstance(store, str) and os.path.normcase(store) not in filled
            ]
            folder = old_entry.get("folder")
            drop = [
                key
                for key in old_entry.get("drop_keys") or []
                if not folder
                or os.path.normcase(os.path.join(folder, key)) not in filled
            ]
            cleanup.append({**old_entry, "stores": stores, "drop_keys": drop})
        cleanup.append(entry)
        if plan.to_folder:
            value = {
                "mode": preview_store.FOLDER_MODE,
                "path": plan.target["path"],
                "id": plan.target_id,
                "install": plan.install,
                "cleanup": cleanup,
            }
        else:
            value = {
                "mode": preview_store.SOURCE_MODE,
                "install": plan.install,
                "cleanup": cleanup,
            }

        def mutate(config):
            section = config.get("thumbnails")
            if not isinstance(section, dict):
                section = {}
            section["location"] = value
            config["thumbnails"] = section
            return config

        def publish(_config):
            PromptManagerConfig.THUMBNAIL_LOCATION = value
            preview_store.configure(value, check=False)

        get_config_store().commit(mutate, expected_revision=None, publish=publish)
        # The switch is made: what follows never undoes it.
        try:
            preview_store.refresh()
            _remove(move_plan.journal_path())
        except OSError as error:
            self.logger.warning(
                f"The thumbnail move journal stays until the next start: {error}"
            )

    def _old_keys_to_drop(self, plan):
        if not plan.from_folder:
            return []
        keys = [s.old_store and os.path.basename(s.old_store) for s in plan.sources]
        keys = [key for key in keys if key]
        if plan.to_folder and os.path.normcase(plan.old.path or "") == os.path.normcase(
            plan.target["path"] or ""
        ):
            return []
        return keys + [key for key, _store, _scan in plan.unlisted]

    async def _remove_old_copies(self, job, plan):
        """Phase 6: run the cleanup the switch recorded (and earlier ones)."""
        job.update(status_line="Removing old copies", progress=95)
        job.publish({"type": "progress", "summary": job.summary()})
        left = await self._run_in_executor(self._run_cleanup, job)
        if left:
            job.scope["cleanup_left"] = True

    def _run_cleanup(self, job=None):
        """Remove every recorded old store's previews, temps and empty
        folders; keep in ``cleanup`` what could not go. Returns how many
        entries are left (blocking)."""
        setting = preview_store.setting()
        remaining = []
        for entry in setting.cleanup:
            if not self._cleanup_entry(entry, job):
                remaining.append(entry)
        if list(setting.cleanup) != remaining:
            self._commit_cleanup(remaining)
        return len(remaining)

    def _cleanup_entry(self, entry, job):
        """One recorded removal; True when it is complete."""
        folder = entry.get("folder")
        stores = [
            store for store in entry.get("stores") or [] if isinstance(store, str)
        ]
        if folder:
            if not os.path.isdir(folder):
                return False  # the old folder is not reachable: kept for later
            folder_marker = preview_location.read_marker(folder) or {}
            keys = folder_marker.get("keys") or {}
            stores += [
                os.path.join(folder, key)
                for key in entry.get("drop_keys") or []
                if preview_location.valid_key(key) and key in keys
            ]
        complete = True
        for store in dict.fromkeys(stores):
            if not self._remove_store(store, folder, job):
                complete = False
        if folder and complete:
            drop = set(entry.get("drop_keys") or [])

            def forget(marker):
                for key in drop:
                    marker["keys"].pop(key, None)

            try:
                with preview_store.write_lock:
                    marker = preview_location.write_marker(folder, forget)
            except (OSError, ValueError):
                return False
            if not marker["keys"] and marker.get("created_by_pm"):
                names = set(os.listdir(folder))
                rest = {n for n in names if not files.is_os_metadata(n)}
                if rest <= {preview_location.MARKER_NAME}:
                    _remove(preview_location.marker_path(folder))
                    files.remove_empty_folders(folder)
        return complete

    def _remove_store(self, store, folder, job):
        """Remove the previews and temps of one old store; True when done."""
        store = Path(store)
        if folder:
            # A key folder directly in the old thumbnail folder, nothing else.
            if not preview_location.valid_key(store.name) or os.path.normcase(
                str(store.parent)
            ) != os.path.normcase(str(folder)):
                return True
        elif store.name != "thumbnails":
            return True  # a hand-edited entry never names another folder
        if not store.exists():
            return True
        if preview_location.is_link_like(store):
            return True  # never through a link; nothing of ours there
        complete = True
        removed = 0
        for current, dirs, names in os.walk(store):
            dirs[:] = [
                d for d in dirs if not preview_location.is_link_like(Path(current) / d)
            ]
            for name in names:
                path = Path(current) / name
                if files.is_temp_name(name) or files._preview_parts(name):
                    if preview_location.is_link_like(path):
                        continue
                    if exit_signal.requested():
                        return False  # exiting: the next start finishes it
                    if files.remove_file(path):
                        removed += 1
                    else:
                        complete = False
                        relative = path.relative_to(store).as_posix()
                        self.logger.warning(
                            f"Could not remove the old thumbnail {path}; it is "
                            "removed at the next start"
                        )
                        if job is not None:
                            # D10A: a removal that failed is an error the job lists.
                            job.record(
                                "errors",
                                {
                                    "path": relative,
                                    "root": store.name,
                                    "detail": "In use by another program; removed "
                                    "at the next start",
                                },
                                f"In use by another program; removed at the next "
                                f"start: {store.name}/{relative}",
                            )
        files.remove_empty_folders(store)
        if job is not None and removed:
            job.log_line(f"Removed {_number(removed)} old files from {store.name}")
        return complete

    def _commit_cleanup(self, remaining):
        from ..config import PromptManagerConfig
        from ..config_store import get_config_store

        def mutate(config):
            section = config.get("thumbnails")
            if isinstance(section, dict) and isinstance(section.get("location"), dict):
                section["location"]["cleanup"] = remaining
            return config

        def publish(config):
            value = (config.get("thumbnails") or {}).get("location")
            PromptManagerConfig.THUMBNAIL_LOCATION = value
            preview_store.set_cleanup(remaining)

        get_config_store().commit(mutate, expected_revision=None, publish=publish)


def plan_parent_missing(store):
    """A key folder's parent (the thumbnail folder) is gone mid-copy."""
    return not Path(store).parent.is_dir()
