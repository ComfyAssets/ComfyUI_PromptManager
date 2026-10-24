"""Convert outdated thumbnails (#317): an explicit, cancellable Activity job.

``POST /prompt_manager/jobs/thumbnails {mode: "convert"}`` (and
``GET /prompt_manager/images/thumbnails/plan?mode=convert`` for its count)
starts "Convert outdated thumbnails": the thumbnail job's kind, ``guarded``
admission (never beside a record-changing job such as a scan, a restore or
a duplicate removal; one thumbnail job at a time), its worker threads and
its outcome groups. Nothing converts on its own: changing the format in
Appearance only makes previews "outdated".

A preview is outdated when the file owns one in another format than the one
written now (``writer_settings``: a legacy ``_thumb.png``, or a format
switched away from). For each such file, at a file boundary (Cancel keeps
what was converted):

1. the new preview is written from the SOURCE media in the configured
   format (``_generate_one``: atomic, verified to decode; a video whose frame
   cannot be decoded fails rather than getting the placeholder), unless a
   current preview at least as new as the source is already there;
2. only then are that file's old-format previews removed: regular files (no
   links) in its ``thumbnails/`` folder, owned by that source
   (``thumbnail_settings.owns``), with the source still present.

A failure keeps the old preview. Previews whose source is gone (orphans) are
never planned, so never touched. The final status line reads
"N converted · F failed · <bytes before> → <bytes after>".
"""

import asyncio
import functools
import os
import time
from pathlib import Path

from . import thumbnail_settings
from .admin import _plural, _scan_location, _scan_roots, _scan_status_line
from .images import _thumbnail_is_fresh, _worker_threads, _thumbnail_batch_size
from .prompts import safe_error_message

try:
    from ...utils.parallel import map_parallel
except ImportError:  # tests import the package top-level
    from utils.parallel import map_parallel

CONVERT_JOB_TITLE = "Convert outdated thumbnails"
NOTHING_OUTDATED = "No outdated previews to convert"
CANCELLED_BEFORE_CONVERTING = "Cancelled before any preview was converted"
SOURCE_GONE = "The file is gone; its old preview was kept"
OLD_NOT_REMOVED = "Converted; the old preview could not be removed"
_UNITS = ("B", "KB", "MB", "GB", "TB")


def readable_bytes(count):
    """``1.5 KB``-style size (whole bytes below 1 KB)."""
    size = float(count)
    for unit in _UNITS:
        if size < 1024 or unit == _UNITS[-1]:
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} {_UNITS[-1]}"  # pragma: no cover - loop always returns


def _old_previews(src, dst, settings):
    """``[(path, size)]``: *src*'s previews beside *dst* in another format."""
    stem = dst.name[: -len(dst.suffix)] if dst.suffix else dst.name
    old = []
    for path in thumbnail_settings.owned_in_folder(
        dst.parent, stem, src.suffix, settings.extension
    ):
        if path.suffix == settings.extension:
            continue
        try:
            old.append((path, path.stat().st_size))
        except OSError:
            continue
    return old


def _finished_line(totals, remaining):
    line = (
        f"{totals['converted']:,} converted · {totals['failed']:,} failed · "
        f"{readable_bytes(totals['before'])} → {readable_bytes(totals['after'])}"
    )
    if remaining:
        line += f" · {remaining:,} more on the next run"
    return line


class ThumbnailConvertMixin:
    """The Convert outdated job (started through ``_start_thumbnail_job``)."""

    async def _run_convert_job(self, job, options):
        try:
            state = await self._convert_job_body(job, options)
        except Exception:
            self.logger.exception("Thumbnail conversion failed")
            job.update(status_line="Thumbnail conversion stopped on an internal error.")
            job.log_line("Conversion failed · internal error")
            state = "failed"
        job.finish(state)

    async def _convert_job_body(self, job, options):
        output_dirs = await self._run_in_executor(self._get_all_output_dirs)
        if not output_dirs:
            message = "ComfyUI output directory not found"
            job.update(status_line=message)
            job.log_line(f"Conversion failed · {message}")
            return "failed"
        folders = _plural(len(output_dirs), "output folder")
        job.scope["output_folders"] = len(output_dirs)
        job.update(status_line=f"Looking for outdated previews in {folders}")
        job.log_line(
            f"Conversion started · {folders} · to {options.settings.describe()}"
        )
        plan = await self._run_in_executor(
            self._plan_gallery_thumbnails,
            output_dirs,
            "convert",
            stop=lambda: job.cancelled,
            settings=options.settings,
        )
        if job.cancelled:
            return self._end_conversion(job, 0, CANCELLED_BEFORE_CONVERTING)
        targets = plan["targets"]
        if not targets:
            job.update(progress=100, status_line=NOTHING_OUTDATED)
            job.log_line(f"Conversion finished · {NOTHING_OUTDATED.lower()}")
            return "finished"
        totals = await self._convert_targets(job, options, targets, output_dirs)
        self.invalidate_gallery_cache()
        if job.cancelled:
            return self._end_conversion(
                job,
                totals["converted"],
                f"Cancelled · {_plural(totals['converted'], 'preview')} converted",
            )
        line = _finished_line(totals, plan["remaining"])
        job.update(progress=100, status_line=line)
        job.log_line(f"Conversion finished · {line}")
        self.logger.info(f"Thumbnail conversion: {line}")
        return "finished"

    @staticmethod
    def _end_conversion(job, converted, status_line):
        job.update(status_line=status_line)
        job.log_line(
            f"Conversion cancelled · {_plural(converted, 'preview')} converted"
        )
        return "cancelled"

    async def _convert_targets(self, job, options, targets, output_dirs):
        """Convert *targets* in worker batches, recording each file."""
        roots = _scan_roots(output_dirs)
        total = len(targets)
        job.update(total=total, status_line=_scan_status_line(0, total, 0, 0))
        totals = {"converted": 0, "failed": 0, "before": 0, "after": 0}
        start = time.monotonic()
        batch_size = _thumbnail_batch_size(_worker_threads())
        index = batches = 0
        for batch_start in range(0, total, batch_size):
            if job.cancelled:
                break
            batch = targets[batch_start : batch_start + batch_size]
            results = await self._run_in_executor(
                self._convert_batch, batch, options.settings, lambda: job.cancelled
            )
            for (src, _dst, _video), result in zip(batch, results):
                if result["action"] == "cancelled":
                    continue
                index += 1
                self._record_conversion(job, src, result, roots, totals)
                job.update(processed=index, progress=index * 100 // total)
            batches += 1
            job.update(
                status_line=_scan_status_line(
                    index, total, batches, time.monotonic() - start
                )
            )
            await asyncio.sleep(0)  # let other requests run between batches
        return totals

    @staticmethod
    def _record_conversion(job, src, result, roots, totals):
        path, label, index = _scan_location(src, roots)
        where = f"{label}/{path}" if label else path
        row = {"path": path, "root": label, "root_index": index}
        if result["action"] == "converted":
            totals["converted"] += 1
            totals["before"] += result["before"]
            totals["after"] += result["after"]
            detail = result["detail"]
            job.record("generated", {**row, "detail": detail}, f"+ {where} · {detail}")
            return
        totals["failed"] += 1
        detail = result.get("error") or "failed"
        job.record("errors", {**row, "detail": detail}, f"! {where} · {detail}")

    def _convert_batch(self, targets, settings, stop):
        """Convert ``targets`` on the configured worker threads (blocking)."""
        convert = functools.partial(self._convert_target, settings=settings, stop=stop)
        return map_parallel(convert, targets, _worker_threads())

    def _convert_target(self, target, settings, stop):
        src, dst, is_video = target
        return self._convert_one(src, dst, is_video, settings, stop=stop)

    def _convert_one(self, src, dst, is_video, settings, stop=None):
        """Convert one file's preview (blocking; never raises).

        ``converted`` with the bytes before and after, ``error`` (the old
        preview kept) or ``cancelled`` (a file boundary).
        """
        if stop is not None and stop():
            return {"action": "cancelled"}
        dst = Path(dst)
        try:
            old = _old_previews(src, dst, settings)
            if not _thumbnail_is_fresh(src, dst):
                made = self._generate_one(
                    src, dst, settings, is_video, rebuild=True, placeholder=False
                )
                if made["action"] != "generated":
                    return {"action": "error", "error": made.get("error")}
                dst = Path(made["path"])
            if dst.suffix != settings.extension or not dst.is_file():
                return {"action": "error", "error": "The new preview was not written"}
            if not os.path.isfile(src):
                return {"action": "error", "error": SOURCE_GONE}
            kept = []
            for path, _size in old:
                try:
                    os.remove(path)
                except OSError as exc:
                    self.logger.warning(f"Could not remove old preview {path}: {exc}")
                    kept.append(path)
            if kept:
                return {"action": "error", "error": OLD_NOT_REMOVED}
            label = thumbnail_settings.FORMAT_LABELS[settings.format]
            return {
                "action": "converted",
                "before": sum(size for _path, size in old),
                "after": dst.stat().st_size,
                "detail": f"Converted to {label}",
            }
        except Exception as exc:
            self.logger.warning(f"Conversion failed for {src.name}", exc_info=True)
            return {"action": "error", "error": safe_error_message(exc)}
