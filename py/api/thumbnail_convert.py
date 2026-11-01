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
   current preview at least as new as the source is already there and
   decodes;
2. only then are that file's old-format previews removed: regular files (no
   links) in its ``thumbnails/`` folder, owned by that source
   (``thumbnail_settings.owns``), with the source still present. One already
   gone counts as removed.

Files sharing a stem in one folder (``x.png`` and ``x.mp4``) share one
preview (ruling R2), so they are converted once, as a group: the preview is
made from the first source in ``thumbnail_settings.source_rank`` order
(``.png``, ``.jpg``, ``.jpeg``, ``.webp``, ``.gif``, then videos), the old
previews of every source in the group are removed, and each removed or
written file's bytes count once.

A failure keeps the old preview. Previews whose source is gone (orphans) are
never planned, so never touched. The format the job converts to is fixed
when it starts; Appearance refuses a thumbnail settings change while it runs
(409), and should the format still change underneath, the job stops at the
next file without removing anything in another format. The final status line
reads "N converted · F failed · <bytes before> → <bytes after>".
"""

import asyncio
import functools
import os
import time
from pathlib import Path

from . import thumbnail_settings
from .thumbnail_encode import ThumbnailVerifyError, verify_thumbnail, writer_settings
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
FORMAT_CHANGED = (
    "Stopped: the thumbnail format changed while converting · "
    "{converted} converted. Run Convert outdated again."
)
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


def _group_targets(targets):
    """``[[(src, dst, is_video)]]``: targets sharing a preview name (one
    folder, one stem) in one group, its first source first (R2)."""
    groups = {}
    for target in targets:
        dst = Path(target[1])
        key = os.path.normcase(str(dst.with_suffix("")))
        groups.setdefault(key, []).append(target)
    ordered = []
    for group in groups.values():
        group.sort(
            key=lambda t: (thumbnail_settings.source_rank(Path(t[0]).suffix), str(t[0]))
        )
        ordered.append(group)
    return ordered


def _same_file(a, b):
    """Whether *a* and *b* name one file (re-review N1: on a case-insensitive
    disk ``x_thumb.JPG`` is the ``x_thumb.jpg`` just written). Without inode
    numbers (st_ino 0 on some SMB/FAT mounts) the resolved, normcased paths
    are compared."""
    try:
        first, second = os.stat(a), os.stat(b)
    except OSError:
        return False
    if first.st_ino and second.st_ino:
        return (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(
        os.path.realpath(b)
    )


def _format_changed(settings):
    """Whether previews are written in another format than *settings* now."""
    return writer_settings().extension != settings.extension


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
        await self._release_job_memory()

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
        plan = await self._plan_conversion(job, options, output_dirs)
        if job.cancelled:
            return self._end_conversion(job, 0, CANCELLED_BEFORE_CONVERTING)
        if not plan["targets"]:
            job.update(progress=100, status_line=NOTHING_OUTDATED)
            job.log_line(f"Conversion finished · {NOTHING_OUTDATED.lower()}")
            return "finished"
        totals, left = await self._convert_in_batches(job, options, plan, output_dirs)
        if totals["stopped"]:
            job.update(status_line=FORMAT_CHANGED.format(converted=totals["converted"]))
            job.log_line(
                "Conversion stopped · the thumbnail format changed · "
                f"{_plural(totals['converted'], 'preview')} converted"
            )
            return "cancelled"
        if job.cancelled:
            return self._end_conversion(
                job,
                totals["converted"],
                f"Cancelled · {_plural(totals['converted'], 'preview')} converted",
            )
        line = _finished_line(totals, left)
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

    async def _plan_conversion(self, job, options, output_dirs, exclude=None):
        """One batch's plan (at most MAX_THUMBNAIL_FILES targets), off-loop."""
        return await self._run_in_executor(
            self._plan_gallery_thumbnails,
            output_dirs,
            "convert",
            stop=lambda: job.cancelled,
            settings=options.settings,
            exclude=exclude,
        )

    async def _convert_in_batches(self, job, options, plan, output_dirs):
        """Convert ``plan``, then plan again after each batch until nothing
        is left, the job is cancelled or stopped, or a batch converted
        nothing (files that fail are never planned again). Returns
        ``(totals across batches, files left)``."""
        totals = {"converted": 0, "failed": 0, "before": 0, "after": 0}
        totals["stopped"] = False
        run = {
            "index": 0,
            "batches": 0,
            "total": len(plan["targets"]) + plan["remaining"],
        }
        run["start"] = time.monotonic()
        job.update(
            total=run["total"], status_line=_scan_status_line(0, run["total"], 0, 0)
        )
        attempted = set()
        while True:
            before = totals["converted"]
            await self._convert_targets(
                job, options, plan["targets"], output_dirs, totals, run
            )
            attempted.update(src for src, _dst, _video in plan["targets"])
            stop = job.cancelled or totals["stopped"]
            if stop or not plan["remaining"] or totals["converted"] == before:
                return totals, plan["remaining"]
            plan = await self._plan_conversion(
                job, options, output_dirs, exclude=attempted
            )
            if job.cancelled:
                return totals, 0
            run["total"] = run["index"] + len(plan["targets"]) + plan["remaining"]
            job.update(total=run["total"])
            if not plan["targets"]:
                return totals, plan["remaining"]

    async def _convert_targets(self, job, options, targets, output_dirs, totals, run):
        """Convert one batch's *targets* in worker batches of preview groups,
        recording each file against the job's overall ``run`` counters."""
        roots = _scan_roots(output_dirs)
        groups = _group_targets(targets)
        batch_size = _thumbnail_batch_size(_worker_threads())
        for batch_start in range(0, len(groups), batch_size):
            if job.cancelled or totals["stopped"]:
                break
            batch = groups[batch_start : batch_start + batch_size]
            results = await self._run_in_executor(
                self._convert_batch, batch, options.settings, lambda: job.cancelled
            )
            for group, group_results in zip(batch, results):
                for (src, _dst, _video), result in zip(group, group_results):
                    if result["action"] == "stopped":
                        totals["stopped"] = True
                    if result["action"] in ("cancelled", "stopped"):
                        continue
                    run["index"] += 1
                    index, total = run["index"], max(run["total"], run["index"])
                    self._record_conversion(job, src, result, roots, totals)
                    job.update(processed=index, progress=index * 100 // total)
            run["batches"] += 1
            job.update(
                status_line=_scan_status_line(
                    run["index"],
                    run["total"],
                    run["batches"],
                    time.monotonic() - run["start"],
                )
            )
            await asyncio.sleep(0)  # let other requests run between batches

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

    def _convert_batch(self, groups, settings, stop):
        """Convert preview ``groups`` on the configured worker threads
        (blocking); one result list per group."""
        convert = functools.partial(self._convert_group, settings=settings, stop=stop)
        return map_parallel(convert, groups, _worker_threads())

    def _convert_group(self, group, settings, stop):
        """Convert the preview a group of same-stem files shares, once.

        The first source writes it; the others are recorded with the same
        outcome and no bytes of their own."""
        src, dst, is_video = group[0]
        siblings = [Path(target[0]) for target in group[1:]]
        result = self._convert_one(
            src, dst, is_video, settings, stop=stop, siblings=siblings
        )
        if result["action"] != "converted":
            return [result] * len(group)
        shared = {**result, "before": 0, "after": 0}
        shared["detail"] = f"{result['detail']} (shares {Path(src).name}'s preview)"
        return [result] + [shared] * len(siblings)

    def _convert_one(self, src, dst, is_video, settings, stop=None, siblings=()):
        """Convert one file's preview (blocking; never raises).

        ``converted`` with the bytes removed and written, ``error`` (the old
        preview kept), ``cancelled`` (a file boundary) or ``stopped`` (the
        format changed underneath, review I4). *siblings* share the preview
        (R2): their old previews go too.
        """
        if stop is not None and stop():
            return {"action": "cancelled"}
        if _format_changed(settings):
            return {"action": "stopped"}
        dst = Path(dst)
        try:
            old = self._old_group_previews([Path(src), *siblings], dst, settings)
            if not self._current_preview_usable(src, dst, settings):
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
            if _format_changed(settings):
                return {"action": "stopped"}
            old = [(path, size) for path, size in old if not _same_file(path, dst)]
            removed, kept = self._remove_old_previews(old)
            if kept:
                return {"action": "error", "error": OLD_NOT_REMOVED}
            label = thumbnail_settings.FORMAT_LABELS[settings.format]
            return {
                "action": "converted",
                "before": removed,
                "after": dst.stat().st_size,
                "detail": f"Converted to {label}",
            }
        except Exception as exc:
            self.logger.warning(f"Conversion failed for {src.name}", exc_info=True)
            return {"action": "error", "error": safe_error_message(exc)}

    @staticmethod
    def _old_group_previews(sources, dst, settings):
        """``[(path, size)]`` of every source's old previews, each path once."""
        seen, old = set(), []
        for source in sources:
            for path, size in _old_previews(source, dst, settings):
                key = os.path.normcase(str(path))
                if key not in seen:
                    seen.add(key)
                    old.append((path, size))
        return old

    @staticmethod
    def _current_preview_usable(src, dst, settings):
        """A current preview at least as new as *src* that decodes (M4)."""
        if not _thumbnail_is_fresh(src, dst):
            return False
        try:
            verify_thumbnail(dst, settings.format)
        except ThumbnailVerifyError:
            return False
        return True

    def _remove_old_previews(self, old):
        """``(bytes removed, [kept paths])``; a file already gone counts as
        removed, with no bytes (another run or a sibling removed it)."""
        removed, kept = 0, []
        for path, size in old:
            try:
                os.remove(path)
            except FileNotFoundError:
                continue
            except OSError as exc:
                self.logger.warning(f"Could not remove old preview {path}: {exc}")
                kept.append(path)
                continue
            removed += size
        return removed, kept
