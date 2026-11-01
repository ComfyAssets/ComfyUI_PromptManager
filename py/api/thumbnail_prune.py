"""Remove thumbnails of removed sources (#320 Part 5, D200): a Thumbnail
maintenance scope, offered only in folder mode (31A).

*Removed* means a key of this install in the thumbnail folder's marker whose
identity matches no root in any root list. The job removes the
preview-grammar files and temps of those key folders, ``rmdir``s the empty
folders, drops the keys from the marker and lists anything else as "Left in
place". Kind ``thumbnails``, ``mode: "prune"``.
"""

import functools
import os
from pathlib import Path

from aiohttp import web

from . import preview_location, preview_store
from . import thumbnail_move_files as files
from .jobs import THUMBNAILS_JOB_KIND
from .thumbnail_location import location_refusal
from .thumbnail_move_plan import _number, _plural, _size

PRUNE_TITLE = "Remove thumbnails of removed sources"
PRUNE_GROUPS = ("removed", "left", "errors")


def _prune_line(count, files_count, size):
    if not count:
        return "No sources were removed: nothing to remove."
    return (
        f"{_plural(count, 'source')} no longer listed: "
        f"{_plural(files_count, 'thumbnail')} ({_size(size)}) are removed."
    )


class ThumbnailPruneMixin:
    """The prune plan and job."""

    def _prune_targets(self):
        """``[(key, store, scan)]`` of this install's unlisted keys (blocking)."""
        setting = preview_store.setting()
        location = preview_store.current()
        if setting.mode != preview_store.FOLDER_MODE or not location.available:
            return []
        marker = preview_location.read_marker(setting.path) or {}
        install = setting.install
        return self._unlisted_keys(setting, location, marker.get("keys") or {}, install)

    def _prune_payload(self, targets):
        files_count = sum(
            len(scan.copy) + len(scan.orphans) for _k, _s, scan in targets
        )
        size = sum(scan.bytes for _k, _s, scan in targets)
        return {
            "success": True,
            "mode": "prune",
            "sources": [
                {
                    "key": key,
                    "files": len(scan.copy) + len(scan.orphans),
                    "bytes": scan.bytes,
                    "left_in_place": len(scan.left),
                }
                for key, _store, scan in targets
            ],
            "targets": files_count,
            "bytes": size,
            "line": _prune_line(len(targets), files_count, size),
            "folder": preview_store.setting().path,
        }

    async def get_prune_plan(self):
        refused = location_refusal()
        if refused is not None:
            return refused
        if preview_store.current().mode != preview_store.FOLDER_MODE:
            return web.json_response(
                {
                    "success": False,
                    "code": "invalid-request",
                    "error": "Thumbnails are kept inside each source: no removed "
                    "source has thumbnails here.",
                },
                status=400,
            )
        targets = await self._run_in_executor(self._prune_targets)
        return web.json_response(
            self._prune_payload(targets), headers={"Cache-Control": "no-store"}
        )

    def start_thumbnail_prune(self):
        """Start the prune job (sync, on the loop); raises JobBusy."""
        return self._jobs.start(
            THUMBNAILS_JOB_KIND,
            PRUNE_TITLE,
            admission="guarded",
            cancellable=True,
            scope={"mode": "prune"},
            groups=PRUNE_GROUPS,
            runner=self._run_thumbnail_prune,
        )

    async def _run_thumbnail_prune(self, job):
        targets = await self._run_in_executor(self._prune_targets)
        job.update(total=len(targets), processed=0)
        setting = preview_store.setting()
        for done, (key, store, scan) in enumerate(targets, 1):
            if job.cancelled:
                break
            removed, failed = await self._run_in_executor(
                functools.partial(self._prune_store, store)
            )
            job.record_counted(
                "removed", removed, [{"path": key, "detail": "Thumbnails removed"}]
            )
            if failed:
                job.record_counted("errors", failed)
            if scan.left:
                job.record_counted(
                    "left",
                    len(scan.left),
                    [{"path": f"{key}/{'/'.join(item)}"} for item in scan.left],
                )
            await self._run_in_executor(
                functools.partial(self._forget_key, setting.path, key, store)
            )
            job.update(
                processed=done,
                progress=int(done * 100 / len(targets)),
                status_line=f"Removing thumbnails of {_number(done)} of "
                f"{_number(len(targets))} removed sources",
            )
            job.publish({"type": "progress", "summary": job.summary()})
        line = f"{_number(job.counters.get('removed', 0))} thumbnails removed"
        if job.counters.get("errors"):
            line += f" · {_plural(job.counters['errors'], 'error')}"
        job.update(status_line=line, progress=100)
        job.log_line(line)
        job.finish("cancelled" if job.cancelled else "finished")

    @staticmethod
    def _prune_store(store):
        """Remove the previews and temps of one key folder; ``(removed, failed)``."""
        removed = failed = 0
        store = Path(store)
        if not store.is_dir() or preview_location.is_link_like(store):
            return 0, 0
        for current, dirs, names in os.walk(store):
            dirs[:] = [
                d for d in dirs if not preview_location.is_link_like(Path(current) / d)
            ]
            for name in names:
                path = Path(current) / name
                if not (files.is_temp_name(name) or files._preview_parts(name)):
                    continue
                if preview_location.is_link_like(path):
                    continue
                if files.remove_file(path):
                    removed += 1
                else:
                    failed += 1
        files.remove_empty_folders(store)
        return removed, failed

    @staticmethod
    def _forget_key(folder, key, store):
        """Drop *key* from the marker once its folder is gone."""
        if os.path.exists(store):
            return

        def forget(marker):
            marker["keys"].pop(key, None)

        with preview_store.write_lock:
            preview_location.write_marker(folder, forget)
