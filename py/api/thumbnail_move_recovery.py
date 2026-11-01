"""Move thumbnails after a restart and the unavailable folder's actions
(#320 Parts 3 and 4): undo an interrupted copy, finish a removal that
waited, Recreate folder and Use this folder."""

import os
import threading
from pathlib import Path

from aiohttp import web

from . import preview_location, preview_store
from . import thumbnail_move_files as files
from .job_routes import busy_response
from .jobs import THUMBNAILS_JOB_KIND, JobBusy
from .thumbnail_move_plan import (
    FINISHING_TITLE,
    MOVE_GROUPS,
    _read_json,
    _refusal,
    _remove,
)
from . import thumbnail_move_plan as move_plan


class ThumbnailMoveRecoveryMixin:
    """Startup recovery, the finishing job, Recreate and Use this folder."""

    _move_recover_lock = threading.Lock()
    # Per API: set once recover_thumbnail_move has run (a move waits for it).
    _move_recovered = False

    # ── startup ───────────────────────────────────────────────────────

    def recover_thumbnail_move(self):
        """Startup (B4): undo an interrupted copy, or forget a journal the
        switch already made true. Idempotent; the move start waits for it."""
        with self._move_recover_lock:
            if self._move_recovered:
                return
            try:
                self._recover_journal()
            except Exception:
                self.logger.warning(
                    "The interrupted thumbnail move check failed", exc_info=True
                )
            self._move_recovered = True

    def _recover_journal(self):
        path = move_plan.journal_path()
        journal = _read_json(path)
        if journal is None:
            return
        setting = preview_store.setting()
        target = journal.get("to") or {}
        now = setting.path if setting.mode == preview_store.FOLDER_MODE else None
        if setting.mode == target.get("mode") and (
            now is None
            or os.path.normcase(now) == os.path.normcase(target.get("path") or "")
        ):
            _remove(path)  # the switch happened; only the journal was left
            return
        for store in journal.get("stores") or []:
            if not isinstance(store, str) or not os.path.isdir(store):
                continue
            for current, dirs, names in os.walk(store):
                dirs[:] = [
                    d
                    for d in dirs
                    if not preview_location.is_link_like(Path(current) / d)
                ]
                for name in names:
                    if files.MOVE_TEMP.search(name):
                        files.remove_file(Path(current) / name)
        for folder in reversed(journal.get("created_dirs") or []):
            if not isinstance(folder, str) or not os.path.isdir(folder):
                continue
            for current, dirs, names in os.walk(folder):
                dirs[:] = [
                    d
                    for d in dirs
                    if not preview_location.is_link_like(Path(current) / d)
                ]
                for name in names:
                    if files._preview_parts(name) or files.is_temp_name(name):
                        files.remove_file(Path(current) / name)
            files.remove_empty_folders(folder)
        folder = target.get("path")
        if journal.get("created_folder") and folder and os.path.isdir(folder):
            names = set(os.listdir(folder))
            if names <= {preview_location.MARKER_NAME}:
                _remove(preview_location.marker_path(folder))
                files.remove_empty_folders(folder)
        _remove(path)
        self.logger.info("An interrupted thumbnail move was undone; nothing changed")

    def _cleanup_reachable(self, entry):
        folder = entry.get("folder")
        if folder:
            return os.path.isdir(folder)
        return True

    async def finish_thumbnail_move(self, _app=None):
        """Startup: recover, then start "Move thumbnails (finishing)" when old
        copies wait and every one is reachable (on the server's loop)."""
        await self._run_in_executor(self.recover_thumbnail_move)
        cleanup = list(preview_store.setting().cleanup)
        if not cleanup:
            return None
        reachable = await self._run_in_executor(
            lambda: all(self._cleanup_reachable(entry) for entry in cleanup)
        )
        if not reachable:
            return None
        try:
            return self._jobs.start(
                THUMBNAILS_JOB_KIND,
                FINISHING_TITLE,
                admission="guarded",
                cancellable=False,
                scope={"mode": "move", "phase": "remove"},
                groups=MOVE_GROUPS,
                runner=self._run_finishing,
            )
        except JobBusy:
            return None

    async def _run_finishing(self, job):
        await self._remove_old_copies(job, None)
        left = len(preview_store.setting().cleanup)
        self._end_move(
            job,
            "finished",
            (
                "Old thumbnails removed."
                if not left
                else (
                    "Some old thumbnails are still in use; they are removed at "
                    "the next start."
                )
            ),
        )

    # ── Recreate and Use this folder (Part 4) ─────────────────────────

    async def recreate_thumbnail_folder(self, _request):
        blocker = self._jobs.blocker(THUMBNAILS_JOB_KIND, admission="guarded")
        if blocker is not None:
            return busy_response(JobBusy(blocker))
        code = await self._run_in_executor(self._recreate_folder)
        if code is not None:
            status = (
                409 if code in ("location-has-marker", "location-not-empty") else 400
            )
            if code == "location-has-marker":
                return web.json_response(
                    {
                        "success": False,
                        "code": code,
                        "error": "The folder is back with its thumbnails. Nothing "
                        "was recreated.",
                    },
                    status=status,
                )
            return _refusal(code, status=status)
        return web.json_response(
            {
                "success": True,
                "location": self._thumbnail_location_payload(with_path=True),
            }
        )

    def _recreate_folder(self):
        setting = preview_store.setting()
        if setting.mode != preview_store.FOLDER_MODE or not setting.path:
            return "location-same"
        folder = setting.path
        if os.path.isdir(folder):
            if preview_location.read_marker(folder) is not None:
                return "location-has-marker"
            if not preview_location.holds_only_metadata(folder):
                return "location-not-empty"
        elif not os.path.isdir(os.path.dirname(folder)):
            return "location-parent-missing"
        else:
            try:
                os.mkdir(folder)
            except OSError:
                return "location-not-writable"
        try:
            marker_id = preview_location.init_folder(folder, created_by_pm=True)
        except OSError:
            return "location-not-writable"
        return self._commit_location_id(marker_id)

    async def adopt_thumbnail_folder(self, _request):
        blocker = self._jobs.blocker(THUMBNAILS_JOB_KIND, admission="guarded")
        if blocker is not None:
            return busy_response(JobBusy(blocker))
        code = await self._run_in_executor(self._adopt_folder)
        if code is not None:
            return _refusal(code)
        return web.json_response(
            {
                "success": True,
                "location": self._thumbnail_location_payload(with_path=True),
            }
        )

    def _adopt_folder(self):
        setting = preview_store.setting()
        if setting.mode != preview_store.FOLDER_MODE or not setting.path:
            return "location-same"
        try:
            marker = preview_location.read_marker(setting.path)
        except OSError:
            return "location-unavailable"
        if marker is None:
            return "location-unavailable"
        return self._commit_location_id(marker["id"])

    def _commit_location_id(self, marker_id):
        from ..config import PromptManagerConfig
        from ..config_store import get_config_store

        value = dict(preview_store.setting().to_config())
        value["id"] = marker_id

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

        try:
            get_config_store().commit(mutate, expected_revision=None, publish=publish)
        except Exception:
            self.logger.exception("The thumbnail folder could not be saved")
            return "location-not-writable"
        preview_store.refresh()
        return None
