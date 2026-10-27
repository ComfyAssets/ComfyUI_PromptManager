"""AutoTag API routes for PromptManager."""

import asyncio
import contextlib
import functools
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from .job_routes import busy_response
from .jobs import (
    AUTOTAG_BATCH_JOB_KIND,
    MODEL_DOWNLOAD_JOB_KIND,
    JobBusy,
)

from .maintenance import disk_plan
from .prompts import thumbnail_url_for
from . import thumbnail_settings
from .. import model_gate

from aiohttp import web

_INTERNAL_ERROR = "An internal error occurred. Check server logs for details."

# Tag output images (#328, per image since #379).
AUTOTAG_JOB_TITLE = "Tag output images"
AUTOTAG_PREVIEW_LEASE = "Tag output images review"
AUTOTAG_OWNER_KIND = "autotag-job"
BATCH_PREVIEW_BODY_MAX = 1024
BATCH_JOB_BODY_MAX = 32 * 1024
REVIEW_REQUIRED = (
    "Replacing tags needs a review of each image. Use Preview tagging review, "
    "or choose Skip or Merge for a batch run."
)
BATCH_CANCELLED = "Cancelled. Tags already added were kept."
_BUSY_ERROR = "Auto-tagging is already running"
MODEL_BUSY_ERROR = (
    "A tagging model is busy with another operation. Try again when it finishes."
)


# Who holds the model gate, as a 409 model-busy names it (``operation``). An
# owner is ``"<kind>:<uuid>"`` (``model_gate.new_owner``); a download names
# its own job, whose title says which model.
MODEL_DOWNLOAD_OWNER_KIND = "model-download"
OWNER_LABELS = {
    "autotag-job": "Tag output images",
    "legacy-batch": "Tag output images (legacy page)",
    "legacy-single": "Tagging one image (legacy page)",
    "legacy-unload": "Unloading the tagging model",
    "image-tag-job": "Auto-tag selected images",
    "image-annotations": "Generate suggestions",
}
ANOTHER_OPERATION = "Another tagging operation"
_DOWNLOAD_FAILED = "The download failed. Check server logs for details."
# POST /jobs/model-download carries only {model_type}.
MODEL_DOWNLOAD_BODY_MAX = 1024
# The running download's gate owner and job title. Only one download runs at a
# time and each needs the gate, so a new start replaces the entry.
_download_titles = {}


def _holder_label(holder: Optional[str]) -> str:
    if not holder:
        return ANOTHER_OPERATION
    kind = holder.split(":", 1)[0]
    if kind == MODEL_DOWNLOAD_OWNER_KIND:
        return _download_titles.get(holder, ANOTHER_OPERATION)
    return OWNER_LABELS.get(kind, ANOTHER_OPERATION)


def model_busy_response() -> web.Response:
    """``409 model-busy``: another operation holds the shared model gate.

    ``operation`` names the holder (``OWNER_LABELS``), or "Another tagging
    operation" when the gate was freed meanwhile or the kind is unknown.
    """
    return web.json_response(
        {
            "success": False,
            "code": "model-busy",
            "error": MODEL_BUSY_ERROR,
            "operation": _holder_label(model_gate.holder()),
        },
        status=409,
    )


_SIZE = re.compile(r"^(~?[0-9.]+)\s*([KMGT]?B)$")


def size_label(size: str) -> str:
    """The engine table's size with a space before the unit (``~400 MB``)."""
    match = _SIZE.match(str(size or "").strip())
    return f"{match.group(1)} {match.group(2)}" if match else str(size or "")


def public_models(status: dict, loaded_type: Optional[str]) -> dict:
    """Each engine model's public entry: names and sizes from ``MODELS``, the
    flags from ``status``; never a path (``model_path`` is dropped)."""
    from ..autotag import MODELS

    models = {}
    for model_type, config in MODELS.items():
        flags = status.get(model_type) or {}
        entry = {
            "name": config["name"],
            "label": config["label"],
            "description": config["description"],
            "size": config["size"],
            "size_label": size_label(config["size"]),
            "downloaded": bool(flags.get("downloaded")),
            "loaded": loaded_type == model_type,
        }
        for key in ("model_exists", "mmproj_exists"):
            if key in flags:
                entry[key] = bool(flags[key])
        models[model_type] = entry
    return models


# Only these may be handed to the tagging engine or listed by the scanner.
_MEDIA_EXTENSIONS = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif"}
)
# Bounds for scan_output_dir: a runaway output tree must not pin the server.
_SCAN_MAX_FILES = 50_000
_SCAN_MAX_DEPTH = 12

# One batch run at a time; a second autotag/start gets 409 while this is held.
_autotag_batch_lock = threading.Lock()


def _file_exists(path) -> bool:
    try:
        return Path(str(path)).exists()
    except (OSError, ValueError):
        return False


def path_is_within(candidate, root) -> bool:
    """True when ``candidate`` is strictly inside ``root`` on the real filesystem.

    Both sides go through ``os.path.realpath`` (symlinks, ``..``) and
    ``os.path.normcase`` (Windows case/separator folding) before the
    ``Path.is_relative_to`` check, so ``/out2`` is never treated as inside
    ``/out`` and a symlink inside ``root`` pointing elsewhere is rejected.
    """
    try:
        child = Path(os.path.normcase(os.path.realpath(str(candidate))))
        parent = Path(os.path.normcase(os.path.realpath(str(root))))
    except (OSError, RuntimeError, ValueError):
        return False
    return child != parent and child.is_relative_to(parent)


def _thresholds_or_saved(general, character):
    """The request's WD14 thresholds as floats; one it omits is the saved
    default (``autotag.defaults``, OQ4). Raises ValueError/TypeError."""
    from ..config import AutotagConfig

    return (
        AutotagConfig.GENERAL_THRESHOLD if general is None else float(general),
        AutotagConfig.CHARACTER_THRESHOLD if character is None else float(character),
    )


async def _single(event):
    """A stream of one event."""
    yield event


_monotonic = time.monotonic  # the legacy frames' throttle clock (patched in tests)

# What one image came to in a batch (``on_item``): fixed strings, never an
# exception's text.
SKIP_DETAILS = {
    "unlinked": "File not found",
    "prompt already processed": "Prompt handled by another image",
    "outside allowed directories": "Outside the output folders",
    "file missing": "File not found",
    "already tagged": "Prompt already tagged",
}


def _added(count):
    return f"Added {count} tag" if count == 1 else f"Added {count} tags"


async def _refusal(title):
    """The single error event of an auto-tag batch refused by another job."""
    message = f"{title} already running"
    yield {"type": "error", "status": "error", "message": message}


def wd14_device(service) -> str:
    """Worker thread: ``"gpu"`` when WD14 runs (or would run) on CUDA, else
    ``"cpu"``; a service without the probe is ``"cpu"``."""
    probe = getattr(service, "wd14_device", None)
    if not callable(probe):
        return "cpu"
    try:
        return "gpu" if probe() == "gpu" else "cpu"
    except Exception:
        return "cpu"


class AutotagRoutesMixin:
    """Mixin providing auto-tagging API endpoints."""

    def _register_autotag_routes(self, routes):
        @routes.get("/prompt_manager/autotag/models")
        async def get_autotag_models_route(request):
            return await self.get_autotag_models(request)

        @routes.post("/prompt_manager/autotag/download/{model_type}")
        async def download_autotag_model_route(request):
            return await self.download_autotag_model(request)

        @routes.post("/prompt_manager/autotag/batch/preview")
        async def preview_autotag_batch_route(request):
            return await self.preview_autotag_batch(request)

        @routes.post("/prompt_manager/autotag/start")
        async def start_autotag_route(request):
            return await self.start_autotag(request)

        @routes.post("/prompt_manager/autotag/single")
        async def autotag_single_route(request):
            return await self.autotag_single(request)

        @routes.post("/prompt_manager/autotag/apply")
        async def apply_autotag_route(request):
            return await self.apply_autotag(request)

        @routes.post("/prompt_manager/autotag/unload")
        async def unload_autotag_model_route(request):
            return await self.unload_autotag_model(request)

        @routes.get("/prompt_manager/scan_output_dir")
        async def scan_output_dir_route(request):
            return await self.scan_output_dir(request)

    async def get_autotag_models(self, request):
        """Get status of available AutoTag models."""
        try:
            from ..autotag import get_autotag_service
            from ..config import AutotagConfig

            service = get_autotag_service()
            # Off the loop: the HuggingFace check walks the whole HF cache.
            status = await self._run_in_executor(service.get_models_status)
            loaded = service.is_model_loaded()
            loaded_type = service.get_loaded_model_type() if loaded else None

            return web.json_response(
                {
                    "success": True,
                    "models": public_models(status, loaded_type),
                    # The saved tagging defaults (#328, System › Auto-tagging).
                    "default_prompt": AutotagConfig.TEMPLATE,
                    "model_loaded": loaded,
                    "loaded_model_type": loaded_type,
                    "wd14_general_threshold": AutotagConfig.GENERAL_THRESHOLD,
                    "wd14_character_threshold": AutotagConfig.CHARACTER_THRESHOLD,
                    "suggestion_limit": AutotagConfig.suggestion_limit(),
                    "model_type": AutotagConfig.MODEL_TYPE,
                    "existing": AutotagConfig.EXISTING,
                }
            )

        except Exception as e:
            self.logger.error(f"Get autotag models error: {e}")
            return web.json_response(
                {"success": False, "error": self._public_error(e)}, status=500
            )

    async def _stream_sse(self, request, events):
        """Write an async iterator of dict events as server-sent events.

        A client disconnect raises ConnectionResetError from ``write``; the
        event generator is then closed so its cleanup runs before re-raising
        (aiohttp logs premature disconnects at debug level).
        """
        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await response.prepare(request)
        try:
            async for event in events:
                await response.write(f"data: {json.dumps(event)}\n\n".encode("utf-8"))
        except (ConnectionResetError, asyncio.CancelledError):
            self.logger.info("Client disconnected; stopping autotag stream")
            await events.aclose()
            raise
        await response.write_eof()
        return response

    async def download_autotag_model(self, request):
        """Download a tagging model, streaming the legacy events (POST only).

        The download is the model_download Activity job: this request
        attaches to a running download of the same model or starts one, and
        streams the legacy frames until the job ends. A client disconnect
        ends only the stream; the download goes on. A running download of
        another model answers 409 model-busy (it holds the gate).
        """
        from ..autotag import MODELS, get_autotag_service

        model_type = request.match_info.get("model_type")
        if model_type not in MODELS:
            invalid = {"type": "error", "message": f"Invalid model type: {model_type}"}
            return await self._stream_sse(request, _single(invalid))
        try:
            job = self._jobs.active(MODEL_DOWNLOAD_JOB_KIND)
            if job is not None and job.scope.get("model_type") != model_type:
                return model_busy_response()
            if job is None:
                job, refusal = self._start_model_download(
                    model_type, get_autotag_service()
                )
                if refusal is not None:
                    return refusal
        except Exception:
            self.logger.exception("Download model error")
            failed = {"type": "error", "message": _INTERNAL_ERROR}
            return await self._stream_sse(request, _single(failed))
        return await self._stream_sse(request, self._download_frames(job))

    @staticmethod
    async def _download_frames(job):
        """The legacy download frames for job: start, then its end."""
        yield {"type": "progress", "progress": 0, "status": "Starting download..."}
        async with contextlib.aclosing(job.events()) as events:
            async for _payload in events:
                pass
        if job.state == "finished":
            yield {"type": "complete", "progress": 100, "status": "Download complete"}
        else:
            yield {"type": "error", "message": "Download failed"}

    # ── model download job (#328) ───────────────────────────────────────

    async def start_model_download_job(self, request):
        """``POST /jobs/model-download {model_type}``: 202 with the job.

        400 for a bad body, 409 ``busy`` while a download (or an exclusive
        job) runs, 409 ``model-busy`` while another operation holds the gate.
        """
        data, error = await self._read_json_body(
            request, max_bytes=MODEL_DOWNLOAD_BODY_MAX
        )
        if error is not None:
            return error
        if set(data) != {"model_type"}:
            return self._bad_request("The body must be exactly {model_type}")
        from ..autotag import MODELS, get_autotag_service

        model_type = data["model_type"]
        if not isinstance(model_type, str) or model_type not in MODELS:
            return self._bad_request(
                "model_type must be one of " + ", ".join(sorted(MODELS))
            )
        try:
            job, refusal = self._start_model_download(model_type, get_autotag_service())
        except Exception as e:
            self.logger.exception("Model download start error")
            return web.json_response(
                {"success": False, "error": self._public_error(e)}, status=500
            )
        if refusal is not None:
            return refusal
        return web.json_response({"success": True, "job": job.summary()}, status=202)

    def _start_model_download(self, model_type, service):
        """Register the download job: ``(job, None)`` or ``(None, refusal)``.

        Synchronous (no await between the checks and the start). The gate is
        taken before the job registers and released when the runner ends,
        also when the task is cancelled before the runner ever starts.
        """
        from ..autotag import MODELS

        busy = self._jobs.blocker(MODEL_DOWNLOAD_JOB_KIND, admission="read")
        if busy is not None:
            return None, busy_response(JobBusy(busy))
        owner = model_gate.new_owner(MODEL_DOWNLOAD_OWNER_KIND)
        if not model_gate.try_acquire(owner):
            return None, model_busy_response()
        config = MODELS[model_type]
        label = config["label"]
        title = f"Tagging model download · {label}"
        _download_titles.clear()
        _download_titles[owner] = title
        try:
            job = self._jobs.start(
                MODEL_DOWNLOAD_JOB_KIND,
                title,
                admission="read",
                cancellable=False,
                scope={
                    "model_type": model_type,
                    "label": label,
                    "size_label": size_label(config["size"]),
                },
                runner=functools.partial(
                    self._run_model_download, service, model_type, owner
                ),
            )
        except BaseException as refused:
            # JobBusy, or anything else the registry raised: give the gate back.
            model_gate.release(owner)
            _download_titles.pop(owner, None)
            if isinstance(refused, JobBusy):
                return None, busy_response(refused)
            raise

        def ended(_task):
            model_gate.release(owner)
            if model_gate.holder() != owner:  # not while a thread still runs
                _download_titles.pop(owner, None)

        job.task.add_done_callback(ended)
        return job, None

    async def _run_model_download(self, service, model_type, owner, job):
        label = job.scope["label"]
        job.update(status_line=f"Downloading {label} ({job.scope['size_label']})")
        job.log_line(f"Download started: {label}")
        job.publish({"type": "status", "status": job.status_line})
        try:
            ok = await model_gate.run(owner, service.download_model, model_type, None)
        except Exception:
            self.logger.exception(f"Tagging model download failed: {model_type}")
            ok = False
        finally:
            # Before the job ends, so a reader that sees the end finds the
            # gate free (a running download thread defers the release).
            model_gate.release(owner)
        if ok:
            job.update(status_line=f"Downloaded {label}")
            job.log_line(f"Download finished: {label}")
            job.publish({"type": "status", "status": job.status_line})
            job.finish("finished")
            return
        job.update(status_line=_DOWNLOAD_FAILED)
        job.log_line(f"Download failed: {label}")
        job.publish({"type": "status", "status": job.status_line})
        job.finish("failed")

    async def _read_start_params(self, request) -> Optional[dict]:
        """Merge query-string and optional JSON-body parameters for autotag/start.

        Returns None when a body is present but is not a JSON object.
        """
        params = dict(request.query)
        if request.can_read_body:
            try:
                body = await request.json()
            except (json.JSONDecodeError, UnicodeDecodeError):
                return None
            if not isinstance(body, dict):
                return None
            params.update(body)
        return params

    @staticmethod
    def _real_tags(tags) -> list:
        """Normalise a tag field to a list without bookkeeping markers."""
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        return [
            t
            for t in (tags or [])
            if t != "auto-scanned"
            and not t.startswith("prepend:")
            and not t.startswith("append:")
        ]

    async def _apply_tags_to_prompt(self, prompt_id, tags) -> int:
        """Merge ``tags`` into the prompt; how many new tags were added."""
        existing_prompt = await self._run_in_executor(
            self.db.get_prompt_by_id, prompt_id
        )
        if not existing_prompt:
            return 0
        existing_tags = existing_prompt.get("tags", [])
        if isinstance(existing_tags, str):
            existing_tags = [t.strip() for t in existing_tags.split(",") if t.strip()]
        new_tags = list(dict.fromkeys(t for t in tags if t not in existing_tags))
        if not new_tags:
            return 0
        await self._run_in_executor(
            self.db.update_prompt_metadata, prompt_id, tags=existing_tags + new_tags
        )
        return len(new_tags)

    async def _autotag_batch_events(self, service, opts: dict):
        """Yield SSE progress dicts while tagging every image linked to a prompt.

        Both batches run here: the legacy page's (its frames unchanged) and
        the ``autotag`` job's. ``opts``: ``model_type``, ``keep_in_memory``,
        ``existing`` (``skip`` leaves prompts with real tags alone, ``merge``
        adds to them), the two thresholds, and optionally ``gate_owner``,
        ``prompt`` (the template, passed to each call; the legacy page sets
        ``custom_prompt`` instead), ``cancelled`` (checked before each image:
        a cancelled run returns without a ``complete`` frame) and
        ``on_item(index, total, group, image_path, detail)`` (one outcome per
        image, ``SKIP_DETAILS`` for the skips).
        """
        model_type = opts["model_type"]
        keep_in_memory = opts["keep_in_memory"]
        cancelled = opts.get("cancelled") or (lambda: False)
        on_item = opts.get("on_item") or (lambda *_args: None)
        status = await self._run_in_executor(service.get_models_status)
        if not status.get(model_type, {}).get("downloaded"):
            yield {"type": "error", "message": f"Model {model_type} not downloaded"}
            return

        if cancelled():
            return
        yield {"type": "progress", "progress": 0, "status": "Loading model..."}
        run = self._gated_runner(opts.get("gate_owner"))
        try:
            await run(service.load_model, model_type, True)
        except Exception:
            self.logger.exception("Failed to load model")
            yield {
                "type": "error",
                "message": "Failed to load model. Check server logs for details.",
            }
            return

        if opts.get("custom_prompt"):
            service.custom_prompt = opts["custom_prompt"]
        tag_kwargs = {
            "general_threshold": opts["general_threshold"],
            "character_threshold": opts["character_threshold"],
        }
        if opts.get("prompt"):
            tag_kwargs["prompt"] = opts["prompt"]

        yield {
            "type": "progress",
            "progress": 5,
            "status": "Model loaded. Fetching all images from database...",
        }
        images = await self._run_in_executor(self.db.autotag_batch_candidates)
        total = len(images)
        if total == 0:
            yield {
                "type": "complete",
                "processed": 0,
                "tagged": 0,
                "skipped": 0,
                "errors": 0,
                "skipped_outside_roots": 0,
                "status": "No images with linked prompts found",
            }
            if not keep_in_memory:
                service.unload_model()
            return

        yield {
            "type": "progress",
            "progress": 10,
            "status": f"Found {total} images. Processing...",
        }

        counts = {
            "processed": 0,
            "tagged": 0,
            "skipped": 0,
            "errors": 0,
            "skipped_outside_roots": 0,
        }
        tagged_prompt_ids = set()
        last_update = _monotonic()
        finished = False

        def progress_event(i, message):
            return {
                "type": "progress",
                "progress": 10 + int((i + 1) / total * 85),
                "status": message,
                "processed": counts["processed"],
                "tagged": counts["tagged"],
                "skipped": counts["skipped"],
                "skipped_outside_roots": counts["skipped_outside_roots"],
            }

        try:
            for i, image_data in enumerate(images):
                if cancelled():
                    return
                image_path = image_data.get("image_path")
                prompt_id = image_data.get("prompt_id")
                skip_reason = None
                allowed_path = None
                if not image_path or not prompt_id:
                    skip_reason = "unlinked"
                elif prompt_id in tagged_prompt_ids:
                    skip_reason = "prompt already processed"
                else:
                    # Rows can point anywhere (restored DB, hand edits): only
                    # media files inside the served output roots are tagged.
                    allowed_path = self._resolve_allowed_image_path(str(image_path))
                    if allowed_path is None:
                        if _file_exists(image_path):
                            skip_reason = "outside allowed directories"
                            counts["skipped_outside_roots"] += 1
                        else:
                            skip_reason = "file missing"
                    elif opts["existing"] == "skip" and self._real_tags(
                        image_data.get("prompt_tags", [])
                    ):
                        tagged_prompt_ids.add(prompt_id)
                        skip_reason = "already tagged"

                if skip_reason is not None:
                    counts["skipped"] += 1
                    on_item(i, total, "skipped", image_path, SKIP_DETAILS[skip_reason])
                    now = _monotonic()
                    if (now - last_update) >= 0.5 or i == total - 1:
                        yield progress_event(
                            i, f"Skipping {i + 1}/{total} ({skip_reason})..."
                        )
                        await asyncio.sleep(0.01)
                        last_update = now
                    continue

                try:
                    tags = await run(service.generate_tags, allowed_path, **tag_kwargs)
                    counts["processed"] += 1
                    added = 0
                    if tags:
                        added = await self._apply_tags_to_prompt(prompt_id, tags)
                    if added:
                        counts["tagged"] += 1
                        on_item(i, total, "tagged", image_path, _added(added))
                    else:
                        counts["skipped"] += 1
                        on_item(i, total, "unchanged", image_path, "No new tags")
                    tagged_prompt_ids.add(prompt_id)
                except Exception as img_err:
                    self.logger.error(
                        f"Error processing {os.path.basename(str(image_path))}: "
                        f"{img_err}"
                    )
                    counts["errors"] += 1
                    counts["processed"] += 1
                    on_item(i, total, "errors", image_path, "Tagging failed")

                yield progress_event(i, f"Processing {i + 1}/{total}...")
                await asyncio.sleep(0.01)
                last_update = _monotonic()

            finished = True
            if not keep_in_memory:
                service.unload_model()
            yield {
                "type": "complete",
                "progress": 100,
                **counts,
                "status": "Complete",
                "model_status": (
                    "Model kept in memory" if keep_in_memory else "Model unloaded"
                ),
                "model_loaded": keep_in_memory,
            }
        finally:
            if not finished and not keep_in_memory:
                service.unload_model()

    async def start_autotag(self, request):
        """Start batch auto-tagging with streaming progress (POST only)."""
        params = await self._read_start_params(request)
        if params is None:
            return self._bad_request("Request body must be a JSON object")

        opts = {
            "model_type": params.get("model_type", "gguf"),
            "custom_prompt": params.get("prompt", ""),
            "existing": (
                "skip"
                if str(params.get("skip_tagged", "true")).lower() == "true"
                else "merge"
            ),
            "keep_in_memory": str(params.get("keep_in_memory", "true")).lower()
            == "true",
            "general_threshold": params.get("general_threshold"),
            "character_threshold": params.get("character_threshold"),
        }
        try:
            opts["general_threshold"], opts["character_threshold"] = (
                _thresholds_or_saved(
                    opts["general_threshold"], opts["character_threshold"]
                )
            )
        except (ValueError, TypeError):
            return self._bad_request(
                "general_threshold and character_threshold must be numeric"
            )

        errors = []  # an error event ends the run; Activity lists it failed

        async def events():
            try:
                from ..autotag import get_autotag_service

                async for event in self._autotag_batch_events(
                    get_autotag_service(), opts
                ):
                    if event.get("type") == "error":
                        errors.append(event)
                    yield event
            except Exception:
                self.logger.exception("AutoTag error")
                errors.append(_INTERNAL_ERROR)
                yield {
                    "type": "error",
                    "message": _INTERNAL_ERROR,
                }

        # The batch writes prompt tags: a records hold on the registry, so it
        # never runs beside a scan, restore or the workspace tagging job.
        busy = self._jobs.blocker(AUTOTAG_BATCH_JOB_KIND, admission="records")
        if busy is not None:
            # A second legacy batch keeps its JSON 409; the workspace's Tag
            # output images job is named in a stream frame.
            if busy.kind == AUTOTAG_BATCH_JOB_KIND and busy.title != AUTOTAG_JOB_TITLE:
                return web.json_response(
                    {"success": False, "error": _BUSY_ERROR}, status=409
                )
            # Both legacy pages read this route as a stream, where a 409 reads
            # as a lost connection: refuse with one error frame instead (the
            # admin page keys on ``type``, the gallery on ``status``).
            return await self._stream_sse(request, _refusal(busy.title))
        async with self._jobs.hold(
            AUTOTAG_BATCH_JOB_KIND,
            OWNER_LABELS["legacy-batch"],
            admission="records",
        ) as job:
            response = await self._start_autotag_batch(request, opts, events)
            if response.status >= 400 or errors:
                job.finish("failed")
            return response

    async def _start_autotag_batch(self, request, opts, events):
        if not _autotag_batch_lock.acquire(blocking=False):
            return web.json_response(
                {"success": False, "error": _BUSY_ERROR}, status=409
            )
        try:
            opts["gate_owner"] = model_gate.new_owner("legacy-batch")
            if not model_gate.try_acquire(opts["gate_owner"]):
                return model_busy_response()
            try:
                return await self._stream_sse(request, events())
            finally:
                model_gate.release(opts["gate_owner"])
        finally:
            _autotag_batch_lock.release()

    # ── Tag output images (#328) ────────────────────────────────────────

    async def _batch_body(self, request, keys, max_bytes):
        """``(data, None)`` for a JSON object with exactly ``keys``."""
        data, error = await self._read_json_body(request, max_bytes=max_bytes)
        if error is not None:
            return None, error
        if set(data) != set(keys):
            return None, self._bad_request(
                "The body must be exactly {" + ", ".join(keys) + "}"
            )
        return data, None

    @staticmethod
    def _batch_choice(data):
        """The model's engine entry and the policy, or a 400 message."""
        from ..autotag import MODELS
        from ..config import EXISTING_POLICIES

        model_type, existing = data.get("model_type"), data.get("existing")
        if not isinstance(model_type, str) or model_type not in MODELS:
            return None, "model_type must be one of " + ", ".join(sorted(MODELS))
        if not isinstance(existing, str) or existing not in EXISTING_POLICIES:
            return None, "existing must be one of " + ", ".join(EXISTING_POLICIES)
        return MODELS[model_type], None

    async def preview_autotag_batch(self, request):
        """Review Tag output images: counts, model, backup and disk; no write.

        Opens no image file. Refused (409 ``busy``) while an exclusive job runs.
        """
        try:
            async with self._jobs.lease(AUTOTAG_PREVIEW_LEASE):
                return await self._preview_autotag_batch(request)
        except JobBusy as busy:
            return busy_response(busy)

    async def _preview_autotag_batch(self, request):
        from ..autotag import get_autotag_service
        from ..config import AutotagConfig
        from ..tagging_defaults import WD14_MODEL_TYPES, wd14_seconds_per_image

        data, error = await self._batch_body(
            request, ("model_type", "existing"), BATCH_PREVIEW_BODY_MAX
        )
        if error is not None:
            return error
        config, message = self._batch_choice(data)
        if message:
            return self._bad_request(message)
        model_type, existing = data["model_type"], data["existing"]
        policy = self._safety_backup_policy()
        service = get_autotag_service()
        try:
            targets = await self._autotag_targets()
            marked = await self._run_in_executor(self.db.autotagged_locators)
            status = await self._run_in_executor(service.get_models_status)
            device = None
            if model_type in WD14_MODEL_TYPES:
                device = await self._run_in_executor(wd14_device, service)
            disk, _short, _size = await self._run_in_executor(
                functools.partial(
                    disk_plan,
                    self.db.model.db_path,
                    backup=policy != "off",
                    vacuum=False,
                )
            )
        except Exception as e:
            self.logger.exception("Tag output images could not be reviewed")
            return web.json_response(
                {"success": False, "error": self._public_error(e)}, status=500
            )
        images = len(targets)
        tagged = sum(1 for item in targets if item.locator in marked)
        to_tag = images - tagged if existing == "skip" else images
        estimate = None
        if device is not None:
            estimate = round(to_tag * wd14_seconds_per_image(device), 3)
        return web.json_response(
            {
                "success": True,
                "images": images,
                "already_tagged": tagged,
                "to_tag": to_tag,
                "estimate_seconds": estimate,
                "estimate_device": device,
                "suggestion_limit": AutotagConfig.suggestion_limit(),
                "model": {
                    "type": model_type,
                    "label": config["label"],
                    "size_label": size_label(config["size"]),
                    "downloaded": bool(
                        (status.get(model_type) or {}).get("downloaded")
                    ),
                },
                "existing": existing,
                "review_required": existing == "replace_after_review",
                "backup": {"policy": policy},
                "disk": disk,
            }
        )

    async def start_autotag_job(self, request):
        """``POST /jobs/autotag``: start Tag output images (202), or refuse.

        In order: 400 body or options, 400 ``review-required``; 409
        ``model-unavailable``; 507 ``insufficient-space``; 409 ``busy``
        (another records job, the legacy batch's hold included); 409
        ``model-busy``. No await follows the busy check. The job tags each
        output image (``_run_autotag_batch``), never a prompt (#379).
        """
        from ..config import MAX_SUGGESTION_LIMIT, AutotagConfig
        from .annotation_suggest import OptionError, validate_options

        data, error = await self._batch_body(
            request, ("model_type", "existing", "options"), BATCH_JOB_BODY_MAX
        )
        if error is not None:
            return error
        _config, message = self._batch_choice(data)
        if message:
            return self._bad_request(message)
        model_type, existing, options = (
            data["model_type"],
            data["existing"],
            data["options"],
        )
        if isinstance(options, dict) and {"suggestion_limit", "use_gpu"} & set(options):
            return self._bad_request("options take no suggestion_limit or use_gpu")
        try:
            _model, options = validate_options(
                model_type,
                options,
                default_limit=AutotagConfig.suggestion_limit(),
                max_limit=MAX_SUGGESTION_LIMIT,
            )
        except OptionError as error:
            return self._bad_request(str(error))
        if existing == "replace_after_review":
            return web.json_response(
                {"success": False, "code": "review-required", "error": REVIEW_REQUIRED},
                status=400,
            )
        try:
            job, refusal = await self._start_autotag(
                model_type=model_type,
                existing=existing,
                options=options,
                keep_in_memory=True,
                title=AUTOTAG_JOB_TITLE,
                owner_kind=AUTOTAG_OWNER_KIND,
            )
        except Exception as e:
            self.logger.exception("Tag output images could not start")
            return web.json_response(
                {"success": False, "error": self._public_error(e)}, status=500
            )
        if refusal is not None:
            return refusal.response
        return web.json_response({"success": True, "job": job.summary()}, status=202)

    def _gated_runner(self, owner):
        """Executor calls under the model gate *owner* holds (plain when None)."""
        if owner is None:
            return self._run_in_executor

        async def run(func, *args, **kwargs):
            return await model_gate.run(owner, func, *args, **kwargs)

        return run

    def _resolve_allowed_image_path(self, raw_path: str) -> Optional[str]:
        """Return the real path when it is a media file inside an output directory."""
        if Path(str(raw_path)).suffix.lower() not in _MEDIA_EXTENSIONS:
            return None
        for output_dir in self._get_all_output_dirs():
            if path_is_within(raw_path, output_dir):
                real = os.path.realpath(raw_path)
                return real if os.path.isfile(real) else None
        return None

    @staticmethod
    def _forbidden(raw_path: str):
        return web.json_response(
            {
                "success": False,
                "error": (
                    f"{os.path.basename(str(raw_path).rstrip('/' + os.sep))} is not "
                    "an image inside the configured output directories"
                ),
            },
            status=403,
        )

    async def _select_autotag_target(self, data: dict):
        """Pick the image to tag from an autotag/single body.

        Returns ``(image_path, prompt_id, None)`` on success or
        ``(None, None, error_response)`` when the request must be rejected.
        """
        image_id = data.get("image_id")
        if image_id is not None:
            if isinstance(image_id, bool) or not isinstance(image_id, (int, str)):
                return None, None, self._bad_request("image_id must be an integer")
            try:
                image_id = int(image_id)
            except (TypeError, ValueError):
                return None, None, self._bad_request("image_id must be an integer")
            record = await self._run_in_executor(self.db.get_image_by_id, image_id)
            if not record or not record.get("image_path"):
                return (
                    None,
                    None,
                    web.json_response(
                        {"success": False, "error": f"Image {image_id} not found"},
                        status=404,
                    ),
                )
            stored_path = record["image_path"]
            resolved = self._resolve_allowed_image_path(stored_path)
            if resolved is None:
                return None, None, self._forbidden(stored_path)
            return resolved, record.get("prompt_id"), None

        raw_path = data.get("path") or data.get("image_path")
        if not raw_path or not isinstance(raw_path, str):
            return None, None, self._bad_request("image_id or path is required")

        if "root_index" in data:
            root_index = data["root_index"]
            if isinstance(root_index, bool) or not isinstance(root_index, int):
                return None, None, self._bad_request("root_index must be an integer")
            roots = self._get_all_output_dirs()
            if not 0 <= root_index < len(roots):
                return None, None, self._forbidden(raw_path)
            root = roots[root_index]
            if Path(raw_path).is_absolute() or ".." in raw_path.replace(
                "\\", "/"
            ).split("/"):
                return None, None, self._forbidden(raw_path)
            raw_path = str(Path(root) / raw_path)
            if not path_is_within(raw_path, root):
                return None, None, self._forbidden(raw_path)

        resolved = self._resolve_allowed_image_path(raw_path)
        if resolved is None:
            return None, None, self._forbidden(raw_path)

        prompt_id = None
        try:
            prompt_id = await self._run_in_executor(
                self.db.get_prompt_id_for_image, raw_path
            )
            if prompt_id is None and resolved != raw_path:
                prompt_id = await self._run_in_executor(
                    self.db.get_prompt_id_for_image, resolved
                )
        except Exception as e:
            self.logger.warning(f"Could not find linked prompt: {e}")
        return resolved, prompt_id, None

    @staticmethod
    def _bad_request(message: str):
        return web.json_response({"success": False, "error": message}, status=400)

    async def autotag_single(self, request):
        """Generate tags for a single image selected by image_id or gallery path."""
        try:
            data = await request.json()
            if not isinstance(data, dict):
                return self._bad_request("Request body must be a JSON object")
            model_type = data.get("model_type", "gguf")
            custom_prompt = data.get("prompt")
            use_gpu = data.get("use_gpu", True)
            general_threshold = data.get("general_threshold")
            character_threshold = data.get("character_threshold")
            try:
                general_threshold, character_threshold = _thresholds_or_saved(
                    general_threshold, character_threshold
                )
            except (ValueError, TypeError):
                return self._bad_request(
                    "general_threshold and character_threshold must be numeric"
                )

            image_path, prompt_id, error = await self._select_autotag_target(data)
            if error is not None:
                return error

            from ..autotag import get_autotag_service

            service = get_autotag_service()
            owner = model_gate.new_owner("legacy-single")
            if not model_gate.try_acquire(owner):
                return model_busy_response()
            try:
                if (
                    not service.is_model_loaded()
                    or service.get_loaded_model_type() != model_type
                ):
                    await model_gate.run(owner, service.load_model, model_type, use_gpu)

                if custom_prompt:
                    service.custom_prompt = custom_prompt

                tags = await model_gate.run(
                    owner,
                    service.generate_tags,
                    image_path,
                    general_threshold=general_threshold,
                    character_threshold=character_threshold,
                )
            finally:
                model_gate.release(owner)

            return web.json_response(
                {
                    "success": True,
                    "tags": tags,
                    "prompt_id": prompt_id,
                    "filename": os.path.basename(image_path),
                }
            )

        except json.JSONDecodeError:
            return self._bad_request("Request body must be valid JSON")
        except Exception as e:
            self.logger.error(f"AutoTag single error: {e}")
            return web.json_response(
                {"success": False, "error": self._public_error(e)}, status=500
            )

    async def apply_autotag(self, request):
        """Apply selected tags to a prompt."""
        try:
            data = await request.json()
            prompt_id = data.get("prompt_id")
            tags = data.get("tags", [])

            if not prompt_id:
                return web.json_response(
                    {"success": False, "error": "prompt_id is required"}, status=400
                )

            if not tags:
                return web.json_response(
                    {"success": True, "message": "No tags to apply"}
                )

            prompt = await self._run_in_executor(self.db.get_prompt_by_id, prompt_id)
            if not prompt:
                return web.json_response(
                    {"success": False, "error": f"Prompt {prompt_id} not found"},
                    status=404,
                )

            existing_tags = prompt.get("tags", [])
            if isinstance(existing_tags, str):
                existing_tags = [
                    t.strip() for t in existing_tags.split(",") if t.strip()
                ]

            new_tags = [t for t in tags if t not in existing_tags]
            all_tags = existing_tags + new_tags

            await self._run_in_executor(
                self.db.update_prompt_metadata, prompt_id, tags=all_tags
            )

            return web.json_response(
                {"success": True, "added_tags": new_tags, "total_tags": len(all_tags)}
            )

        except Exception as e:
            self.logger.error(f"Apply autotag error: {e}")
            return web.json_response(
                {"success": False, "error": self._public_error(e)}, status=500
            )

    async def unload_autotag_model(self, request):
        """Manually unload the AutoTag model from memory."""
        try:
            from ..autotag import get_autotag_service

            service = get_autotag_service()
            owner = model_gate.new_owner("legacy-unload")
            if not model_gate.try_acquire(owner):
                return model_busy_response()
            try:
                if not service.is_model_loaded():
                    return web.json_response(
                        {"success": True, "message": "No model was loaded"}
                    )

                model_type = service.get_loaded_model_type()
                service.unload_model()
            finally:
                model_gate.release(owner)

            return web.json_response(
                {
                    "success": True,
                    "message": f"{model_type.upper()} model unloaded successfully",
                    "model_loaded": False,
                }
            )

        except Exception as e:
            self.logger.error(f"Unload autotag model error: {e}")
            return web.json_response(
                {"success": False, "error": self._public_error(e)}, status=500
            )

    @staticmethod
    def _collect_scan_files(output_path: Path):
        """Media files under ``output_path`` from a bounded, symlink-free walk.

        Descends at most ``_SCAN_MAX_DEPTH`` levels, skips ``thumbnails``
        trees and symlinked files, and stops after ``_SCAN_MAX_FILES`` files.
        Returns ``(paths, truncated)``.
        """
        found = []
        base_depth = len(output_path.parts)
        for dirpath, dirnames, filenames in os.walk(output_path):
            depth = len(Path(dirpath).parts) - base_depth
            dirnames[:] = sorted(
                name
                for name in dirnames
                if name != "thumbnails" and depth + 1 <= _SCAN_MAX_DEPTH
            )
            for name in sorted(filenames):
                if Path(name).suffix.lower() not in _MEDIA_EXTENSIONS:
                    continue
                candidate = Path(dirpath) / name
                if candidate.is_symlink():
                    continue
                if len(found) >= _SCAN_MAX_FILES:
                    return found, True
                found.append(candidate)
        return found, False

    async def scan_output_dir(self, request):
        """Scan ComfyUI output directory for images."""
        try:
            output_dir = self._find_comfyui_output_dir()
            if not output_dir:
                return web.json_response(
                    {"success": False, "error": "ComfyUI output directory not found"},
                    status=404,
                )

            output_path = Path(output_dir)
            files, truncated = await self._run_in_executor(
                self._collect_scan_files, output_path
            )
            has_thumbnails = (output_path / "thumbnails").is_dir()

            images = []
            for image_path in files:
                rel_path = image_path.relative_to(output_path)
                thumbnail_url = None
                if has_thumbnails:
                    # Any preview the file owns, the current format first (#317)
                    existing = thumbnail_settings.existing_thumbnail(
                        output_path, rel_path
                    )
                    if existing is not None:
                        thumb_rel = (
                            thumbnail_settings.thumbnail_stem_rel(rel_path)
                            + existing.suffix
                        )
                        thumbnail_url = thumbnail_url_for(thumb_rel, existing)
                images.append(
                    {
                        "filename": image_path.name,
                        "path": self._public_path(image_path),
                        "relative_path": rel_path.as_posix(),
                        "url": "/prompt_manager/images/serve/"
                        + quote(rel_path.as_posix(), safe="/"),
                        "thumbnail_url": thumbnail_url,
                    }
                )

            images.sort(key=lambda x: x["filename"])

            self.logger.info(f"Found {len(images)} images in output directory")

            return web.json_response(
                {
                    "success": True,
                    "images": images,
                    "count": len(images),
                    "truncated": truncated,
                }
            )

        except Exception as e:
            self.logger.error(f"Scan output dir error: {e}")
            return web.json_response(
                {"success": False, "error": self._public_error(e)}, status=500
            )
