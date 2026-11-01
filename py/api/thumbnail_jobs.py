"""Thumbnail generation as an Activity job (#249 System S5, Task 12; #317).

- ``GET /prompt_manager/images/thumbnails/plan?mode=`` counts what a run
  would do over every gallery root and writes nothing: ``{success, targets,
  fresh, outdated, remaining, roots, mode, format, quality, size}``.
- ``POST /prompt_manager/jobs/thumbnails {mode}`` starts the ``thumbnails``
  job (202 ``{job}``). Its admission class is ``guarded``: it never runs
  beside a record-changing job, and one thumbnail job runs at a time. It
  stops at a file boundary when cancelled, keeping the previews it wrote.
  Its outcome groups are ``generated``, ``skipped`` (already fresh) and
  ``errors``.

``mode`` is ``missing`` (previews that are absent or older than their file),
``rebuild`` (every preview again, fresh ones too) or ``convert`` (Convert
outdated: previews in another format, see ``thumbnail_convert.py``). Format, quality and
size limit are the server's thumbnail settings (``thumbnail_settings``,
#317), read once when the job starts and shown in its scope; a ``size`` or
``quality`` an older client sends is ignored. Every preview is written next
to the old one, verified and renamed over it, so nothing is cleared first and
a failure keeps the old preview; originals are only read.

The legacy gallery stream ``POST /images/generate-thumbnails/progress``
starts this job (missing previews, the server's settings; its ``quality``
preset is ignored, #317 R3) or attaches to the running one. Its first frame,
``job {id, started}``, names that job and whether this stream started it (the
gallery cancels by that id, and only a job it started); then come the job's
legacy ``status``/``start``/``progress``/``file_error``/``complete`` frames. A
client that goes away no longer stops the work: the job outlives the request,
as a scan does.
"""

import asyncio
import functools
import json
import time
from dataclasses import dataclass

from aiohttp import web

from .admin import _plural, _scan_location, _scan_roots, _scan_status_line
from .images import (
    _BodyTooLarge,
    _new_thumbnail_stats,
    _read_json_body,
    _record_thumbnail_result,
    _thumbnail_batch_size,
    _thumbnail_complete_payload,
    _thumbnail_progress_payload,
    _worker_threads,
)
from .job_routes import busy_response
from .thumbnail_location import location_refusal
from .jobs import THUMBNAIL_GROUP_NAMES, THUMBNAILS_JOB_KIND, JobBusy
from .thumbnail_convert import CONVERT_JOB_TITLE
from .thumbnail_encode import writer_settings
from .thumbnail_settings import ThumbnailSettings

THUMBNAIL_JOB_TITLE = "Thumbnail generation"
THUMBNAIL_MODES = ("missing", "rebuild", "convert")
_OPTIONS_ERROR = 'mode must be "missing", "rebuild" or "convert"'
CANCELLED_ERROR = "Thumbnail generation was cancelled"
CANCELLED_BEFORE_WRITING = "Cancelled before any preview was written"
NOTHING_TO_PREVIEW = "No images or videos to preview"
_MODE_LOG = {"missing": "missing previews", "rebuild": "every preview"}
# Outcome rows: what happened to each file's preview.
_DETAILS = {
    "generated": "Preview written",
    "skipped": "Preview already up to date",
}
_LOG_PREFIXES = {"generated": "+", "errors": "!"}


@dataclass(frozen=True)
class ThumbnailOptions:
    """One run's choices: the mode and the thumbnail settings it writes with."""

    mode: str
    settings: ThumbnailSettings

    @property
    def rebuild(self):
        return self.mode == "rebuild"

    def scope(self):
        return {
            "mode": self.mode,
            "format": self.settings.format,
            "quality": self.settings.quality,
            "size": self.settings.edge,
        }


def thumbnail_options(raw, *, from_query=False):
    """``(ThumbnailOptions, None)`` from a body or query, or ``(None, message)``.

    An absent mode is ``missing``; the settings are the server's now.
    """
    if not isinstance(raw, dict) and not hasattr(raw, "get"):
        return None, _OPTIONS_ERROR
    mode = raw.get("mode", "missing")
    if mode not in THUMBNAIL_MODES:
        return None, _OPTIONS_ERROR
    return ThumbnailOptions(mode, writer_settings()), None


def _invalid(message):
    return web.json_response(
        {"success": False, "code": "invalid-request", "error": message}, status=400
    )


def _frame(event, data):
    """A legacy stream frame as the job publishes it."""
    return {"event": event, "data": data}


def _finished_line(plan, stats):
    """A finished job's status line: what it wrote, or that it had nothing to do."""
    if not plan["targets"]:
        if plan["skipped"]:
            return f"No previews to generate · {plan['skipped']:,} already up to date"
        return NOTHING_TO_PREVIEW
    parts = [f"{_plural(stats['generated'], 'preview')} written"]
    if stats["errors"]:
        parts.append(_plural(len(stats["errors"]), "error"))
    if plan["remaining"]:
        parts.append(f"{plan['remaining']:,} more on the next run")
    return " · ".join(parts)


class ThumbnailJobsMixin:
    """The thumbnail plan, the thumbnail job and the legacy stream on it."""

    async def get_thumbnail_plan(self, request):
        """What a thumbnail run would do (read-only; capped like a run).
        ``mode=prune`` lists the removed sources' thumbnails (#320)."""
        if request.query.get("mode") == "prune":
            return await self.get_prune_plan()
        options, error = thumbnail_options(request.query, from_query=True)
        if error:
            return _invalid(error)
        try:
            output_dirs = await self._run_in_executor(self._get_all_output_dirs)
            plan = await self._run_in_executor(
                self._plan_gallery_thumbnails,
                output_dirs,
                options.mode,
                settings=options.settings,
            )
        except Exception:
            self.logger.exception("Thumbnail plan failed")
            return web.json_response(
                {
                    "success": False,
                    "code": None,
                    "error": "The thumbnail plan could not be read",
                },
                status=500,
            )
        return web.json_response(
            {
                "success": True,
                "targets": len(plan["targets"]),
                "fresh": plan["fresh"],
                "outdated": plan["outdated"],
                "remaining": plan["remaining"],
                "roots": [self._public_path(root) for root in output_dirs],
                **options.scope(),
            },
            headers={"Cache-Control": "no-store"},
        )

    async def start_thumbnail_job(self, request):
        """Start the thumbnail job (202), or 409 naming the job in the way."""
        body = {}
        if request.can_read_body:
            try:
                body = await _read_json_body(request)
            except _BodyTooLarge:
                return web.json_response(
                    {"success": False, "code": "too-large", "error": "Body too large"},
                    status=413,
                )
            except (ValueError, UnicodeDecodeError):
                return _invalid(_OPTIONS_ERROR)
        if isinstance(body, dict) and body.get("mode") in ("move", "prune"):
            return await self._start_location_job(body)
        options, error = thumbnail_options(body)
        if error:
            return _invalid(error)
        refused = location_refusal()
        if refused is not None:
            return refused
        try:
            job = self._start_thumbnail_job(options)
        except JobBusy as busy:
            return busy_response(busy)
        return web.json_response({"success": True, "job": job.summary()}, status=202)

    async def _start_location_job(self, body):
        """Move thumbnails or Remove thumbnails of removed sources (#320)."""
        if body["mode"] == "prune":
            refused = location_refusal()
            if refused is not None:
                return refused
        try:
            if body["mode"] == "move":
                started = self.start_thumbnail_move(body)
            else:
                started = self.start_thumbnail_prune()
        except JobBusy as busy:
            return busy_response(busy)
        if isinstance(started, web.Response):
            return started
        return web.json_response(
            {"success": True, "job": started.summary()}, status=202
        )

    def _start_thumbnail_job(self, options):
        """Register the thumbnail job synchronously; raises JobBusy.

        ``convert`` runs Convert outdated (``thumbnail_convert.py``) under the
        same kind, admission, cancel and groups.
        """
        convert = options.mode == "convert"
        runner = self._run_convert_job if convert else self._run_thumbnail_job
        return self._jobs.start(
            THUMBNAILS_JOB_KIND,
            CONVERT_JOB_TITLE if convert else THUMBNAIL_JOB_TITLE,
            admission="guarded",
            cancellable=True,
            scope=options.scope(),
            runner=functools.partial(runner, options=options),
            groups=THUMBNAIL_GROUP_NAMES,
        )

    # ── the job ───────────────────────────────────────────────────────

    async def _run_thumbnail_job(self, job, options):
        try:
            state = await self._thumbnail_job_body(job, options)
        except Exception:
            self.logger.exception("Thumbnail generation failed")
            job.update(status_line="Thumbnail generation stopped on an internal error.")
            job.log_line("Thumbnails failed · internal error")
            job.publish(
                _frame(
                    "error",
                    {
                        "error": "An internal error occurred",
                        "message": "Thumbnail generation failed. Check server logs.",
                    },
                )
            )
            state = "failed"
        job.finish(state)
        await self._release_job_memory()

    async def _thumbnail_job_body(self, job, options):
        """Plan, then generate in worker batches; returns the final state.

        Every exit sets the job's final status line (I3): what was written,
        that there was nothing to do, or what a cancel kept.
        """
        output_dirs = await self._run_in_executor(self._get_all_output_dirs)
        if not output_dirs:
            message = "ComfyUI output directory not found"
            job.update(status_line=message)
            job.log_line(f"Thumbnails failed · {message}")
            job.publish(_frame("error", {"error": message}))
            return "failed"
        folders = _plural(len(output_dirs), "output folder")
        job.scope["output_folders"] = len(output_dirs)
        job.update(status_line=f"Looking for media files in {folders}")
        job.log_line(
            f"Thumbnails started · {folders} · {_MODE_LOG[options.mode]} · "
            f"{options.settings.describe()}"
        )
        job.publish(
            _frame(
                "status",
                {"phase": "scanning", "message": "Scanning for images and videos..."},
            )
        )
        plan = await self._plan_thumbnail_batch(job, options, output_dirs)
        if job.cancelled:  # M2: stopped during the walk, before any start frame
            return self._end_cancelled(job, 0, CANCELLED_BEFORE_WRITING)
        roots = _scan_roots(output_dirs)
        self._record_fresh(job, plan, roots)
        stats, start, left, attempted = await self._generate_in_batches(
            job, options, plan, roots, output_dirs
        )
        generated = _plural(stats["generated"], "preview")
        if job.cancelled:
            return self._end_cancelled(
                job, stats["generated"], f"Cancelled · {generated} written"
            )
        overall = {"targets": attempted, "skipped": plan["skipped"], "remaining": left}
        payload = _thumbnail_complete_payload(
            len(attempted) + plan["skipped"] + left,
            stats,
            start,
            remaining=left,
            processed=len(attempted),
        )
        job.update(progress=100, status_line=_finished_line(overall, stats))
        if left:
            job.log_line(f"{left:,} more previews are left for the next run")
        errors = _plural(len(stats["errors"]), "error")
        job.log_line(f"Thumbnails finished · {generated} written · {errors}")
        self.logger.info(payload["message"])
        job.publish(_frame("complete", payload))
        return "finished"

    async def _plan_thumbnail_batch(self, job, options, output_dirs, exclude=None):
        """One batch's plan (at most MAX_THUMBNAIL_FILES targets), off-loop."""
        return await self._run_in_executor(
            self._plan_gallery_thumbnails,
            output_dirs,
            options.mode,
            stop=lambda: job.cancelled,
            settings=options.settings,
            exclude=exclude,
        )

    async def _generate_in_batches(self, job, options, plan, roots, output_dirs):
        """Work through ``plan``, then plan again after each batch until
        nothing is left, the job is cancelled, or a batch made no progress
        (every file failed: they are not planned again, and the rest waits
        for the next run). Files worked on are never planned twice. Returns
        ``(stats, start, files left, files worked on)``; progress counts
        against the overall total."""
        run = {
            "index": 0,
            "batches": 0,
            "total": len(plan["targets"]) + plan["remaining"],
        }
        stats = _new_thumbnail_stats()
        stats["skipped"] = plan["skipped"]
        start = time.monotonic()
        self._publish_thumbnail_start(job, plan, run["total"])
        attempted = set()
        while True:
            before = stats["generated"] + stats["skipped"]
            await self._generate_thumbnail_targets(
                job, options, plan, roots, stats, run, start
            )
            attempted.update(src for src, _dst, _video in plan["targets"])
            progressed = stats["generated"] + stats["skipped"] > before
            if job.cancelled or not plan["remaining"] or not progressed:
                return stats, start, plan["remaining"], attempted
            plan = await self._plan_thumbnail_batch(
                job, options, output_dirs, exclude=attempted
            )
            if job.cancelled:
                return stats, start, 0, attempted
            run["total"] = run["index"] + len(plan["targets"]) + plan["remaining"]
            job.update(total=run["total"])
            if not plan["targets"]:
                return stats, start, plan["remaining"], attempted

    @staticmethod
    def _end_cancelled(job, generated, status_line):
        """Close a cancelled job: its status line, log line and error frame."""
        job.update(status_line=status_line)
        job.log_line(f"Thumbnails cancelled · {_plural(generated, 'preview')} written")
        job.publish(
            _frame("error", {"error": CANCELLED_ERROR, "message": CANCELLED_ERROR})
        )
        return "cancelled"

    def _record_fresh(self, job, plan, roots):
        """List the already fresh previews under Skipped (no log line each)."""
        rows = []
        for src in plan["fresh_files"]:
            path, label, index = _scan_location(src, roots)
            rows.append(
                {
                    "path": path,
                    "root": label,
                    "root_index": index,
                    "detail": _DETAILS["skipped"],
                }
            )
        job.record_counted("skipped", plan["skipped"], rows)
        if plan["skipped"]:
            job.log_line(f"= {plan['skipped']:,} previews already up to date")

    @staticmethod
    def _publish_thumbnail_start(job, plan, total):
        """The legacy stream's start frame, with the job's overall total."""
        targets = plan["targets"]
        video_count = sum(1 for _, _, is_video in targets if is_video)
        message = (
            f"Found {total - video_count} images and {video_count} videos to process"
        )
        if plan["skipped"]:
            message += f" ({plan['skipped']} already have thumbnails)"
        job.update(total=total)
        if total:  # never "Processing file 0 of 0" (I3)
            job.update(status_line=_scan_status_line(0, total, 0, 0))
        job.publish(
            _frame(
                "start",
                {
                    "total_images": total,
                    "skipped": plan["skipped"],
                    "remaining": 0,
                    "phase": "processing",
                    "image_count": total - video_count,
                    "video_count": video_count,
                    "message": message,
                },
            )
        )

    async def _generate_thumbnail_targets(
        self, job, options, plan, roots, stats, run, start
    ):
        """Generate one batch (``plan``'s targets) in worker batches,
        publishing each file against the job's overall ``run`` counters."""
        targets = plan["targets"]
        batch_size = _thumbnail_batch_size(_worker_threads())
        generate = {
            "rebuild": options.rebuild,
            "stop": lambda: job.cancelled,
        }
        for batch_start in range(0, len(targets), batch_size):
            if job.cancelled:
                break
            batch = targets[batch_start : batch_start + batch_size]
            results = await self._run_in_executor(
                self._generate_batch, batch, options.settings, **generate
            )
            for (src, _dst, _video), result in zip(batch, results):
                if result["action"] == "cancelled":
                    continue
                run["index"] += 1
                index, total = run["index"], max(run["total"], run["index"])
                _record_thumbnail_result(stats, result)
                self._record_thumbnail_outcome(job, src, result, roots)
                job.update(processed=index, progress=index * 100 // total)
                if result["action"] == "error":
                    job.publish(
                        _frame(
                            "file_error",
                            {"file": result["file"], "error": result["error"]},
                        )
                    )
                job.publish(
                    _frame(
                        "progress",
                        _thumbnail_progress_payload(index, total, stats, result, start),
                    )
                )
            run["batches"] += 1
            job.update(
                status_line=_scan_status_line(
                    run["index"], run["total"], run["batches"], time.monotonic() - start
                )
            )
            await asyncio.sleep(0)  # let other requests run between batches

    @staticmethod
    def _record_thumbnail_outcome(job, src, result, roots):
        path, label, index = _scan_location(src, roots)
        where = f"{label}/{path}" if label else path
        if result["action"] == "error":
            group, detail = "errors", result.get("error") or "failed"
        elif result["action"] == "skipped":
            group, detail = "skipped", _DETAILS["skipped"]
        else:
            group, detail = "generated", _DETAILS["generated"]
        row = {"path": path, "root": label, "root_index": index, "detail": detail}
        if group == "skipped":
            job.record_counted(group, 1, [row])
            return
        job.record(group, row, f"{_LOG_PREFIXES[group]} {where} · {detail}")

    # ── the legacy gallery stream ─────────────────────────────────────

    async def generate_thumbnails_with_progress(self, request):
        """The gallery's thumbnail stream (POST, server-sent events).

        Starts the thumbnail job (missing previews with the server's
        thumbnail settings; the gallery's ``quality`` preset is ignored, R3),
        or attaches to the running thumbnail job, and streams its frames. A
        legacy hold (the blocking generate or clear) or a record-changing
        job refuses it with the stream's error frame. The job goes on when
        the client goes away. Refused (409) while the thumbnail folder is
        unavailable (#320).
        """
        refused = location_refusal()
        if refused is not None:
            return refused
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await response.prepare(request)
        # M3: read the registry after the last await, so a job that started
        # while the headers went out is attached to, not refused.
        job, started, busy = self._legacy_stream_job()
        if busy is not None:
            message = f"{busy.title} already running"
            await self._emit_sse(
                response, "error", {"error": message, "message": message}
            )
            return response
        # No await between the start and the subscription: the lead frame is
        # sent after it, so the stream misses none of the runner's frames.
        events = job.events(lead=_frame("job", {"id": job.id, "started": started}))
        try:
            async for payload in events:
                frame = (
                    f"event: {payload['event']}\ndata: {json.dumps(payload['data'])}"
                )
                await response.write(f"{frame}\n\n".encode("utf-8"))
        except Exception as exc:  # a write failed: the client went away
            self.logger.info(
                f"Thumbnail progress client disconnected ({exc}); "
                "the thumbnails continue in the background"
            )
        finally:
            # Also when aiohttp cancels the handler on disconnect: the job is
            # its own task and goes on.
            await events.aclose()
        return response

    def _legacy_stream_job(self):
        """``(job, started, busy)`` for the legacy stream, synchronously.

        Attaches to the running cancellable thumbnail job, or starts one for
        the missing previews with the server's settings, or names the job in
        the way.
        """
        running = self._jobs.active(THUMBNAILS_JOB_KIND)
        if running is not None and running.scope.get("mode") in (
            "convert",
            "move",
            "prune",
        ):
            return None, False, running  # no legacy frames to follow (#317, #320)
        if running is not None and running.cancellable:
            return running, False, None
        busy = self._jobs.blocker(THUMBNAILS_JOB_KIND, admission="guarded")
        if busy is not None:
            return None, False, busy
        # blocker() said free and nothing awaited since, so the start is admitted.
        options = ThumbnailOptions("missing", writer_settings())
        return self._start_thumbnail_job(options), True, None
