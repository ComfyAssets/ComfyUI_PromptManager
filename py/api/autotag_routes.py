"""AutoTag API routes for PromptManager."""

import asyncio
import contextlib
import functools
import json
import os
import re
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

try:
    from ...utils.media_folders import in_trash_below_roots, is_skipped_folder
except ImportError:
    from utils.media_folders import in_trash_below_roots, is_skipped_folder

from aiohttp import web

_INTERNAL_ERROR = "An internal error occurred. Check server logs for details."

# Tag output images (#328, per image since #379).
AUTOTAG_JOB_TITLE = "Tag output images"
AUTOTAG_PREVIEW_LEASE = "Tag output images review"
AUTOTAG_OWNER_KIND = "autotag-job"
BATCH_PREVIEW_BODY_MAX = 1024
APPLY_BODY_MAX = 64 * 1024  # POST /autotag/apply: one image and its tags
_HEX64 = re.compile(r"[0-9a-f]{64}")
BATCH_JOB_BODY_MAX = 32 * 1024
# Skipped tags preview (#431): 200 entries of 50 characters, with room for JSON.
SKIPPED_PREVIEW_BODY_MAX = 64 * 1024
SKIPPED_PREVIEW_LEASE = "Skipped tags preview"
SKIPPED_PREVIEW_SAMPLE = 12
# WD14 label lists the preview found missing or unreadable (logged once each).
_MISSING_LABEL_LISTS = set()
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


def _instruction_engine(model_type: str) -> str:
    """The language-model engine whose default instructions ``model_type``
    reads: itself, or GGUF for a WD14 engine (which takes none)."""
    return model_type if model_type in ("gguf", "hf") else "gguf"


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


def package_entry(flags: dict) -> dict:
    """The public package fields of one status entry (#424): the pip names
    that cannot be imported, the install command and note (strings or null)."""
    missing = flags.get("missing_packages") or []
    text = {
        key: flags[key] if isinstance(flags.get(key), str) else None
        for key in ("install_command", "install_note")
    }
    return {"missing_packages": [str(name) for name in missing], **text}


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
        entry.update(package_entry(flags))
        models[model_type] = entry
    return models


# Only these may be handed to the tagging engine or listed by the scanner.
_MEDIA_EXTENSIONS = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif"}
)
# Bounds for scan_output_dir: a runaway output tree must not pin the server.
_SCAN_MAX_FILES = 50_000
_SCAN_MAX_DEPTH = 12


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

# The legacy pages' batch (#379): the ``autotag`` job, relayed as their frames.
LEGACY_BATCH_TITLE = "Tag output images (legacy page)"
LEGACY_FRAME_INTERVAL = 0.5  # progress frames at most this often
LEGACY_REPEAT_AFTER = 5.0  # a quiet job repeats its last frame (notices a closed page)
_LEGACY_PHASES = {
    "backup": (0, "Taking a safety backup…"),
    "model": (5, "Loading model…"),
    "listing": (8, "Listing output images…"),
}


def _legacy_busy():
    """The JSON 409 a legacy start gets while another legacy batch runs."""
    return web.json_response({"success": False, "error": _BUSY_ERROR}, status=409)


async def _error_frame(message):
    """The single error frame of a legacy batch that could not start."""
    yield {"type": "error", "status": "error", "message": message}


def _legacy_counts(job) -> dict:
    """Legacy counters: ``processed`` = images the model ran on."""
    counters = job.counters
    return {
        "processed": counters.get("tagged", 0)
        + counters.get("unchanged", 0)
        + counters.get("errors", 0),
        "tagged": counters.get("tagged", 0),
        "skipped": counters.get("skipped", 0),
    }


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

        @routes.post("/prompt_manager/autotag/skipped-tags/preview")
        async def preview_skipped_tags_route(request):
            return await self.preview_skipped_tags(request)

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
                    # The saved engine's default instructions (GGUF for a
                    # WD14 engine); kept one release for cached pages (#431).
                    "default_prompt": AutotagConfig.default_instructions_text(
                        _instruction_engine(AutotagConfig.MODEL_TYPE)
                    ),
                    # #431: Built-in first, with every text, and each
                    # language-model engine's default.
                    "instructions": AutotagConfig.all_instructions(),
                    "default_instructions": dict(AutotagConfig.DEFAULT_INSTRUCTIONS),
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
        admitted = False
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
            admitted = True
        except JobBusy as refused:
            return None, busy_response(refused)
        finally:
            if not admitted:  # JobBusy, or anything else: give the gate back.
                model_gate.release(owner)
                _download_titles.pop(owner, None)

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

    async def start_autotag(self, request):
        """The legacy pages' batch (POST only): the ``autotag`` job as frames.

        Its parameters (``model_type``, ``prompt``, ``skip_tagged``,
        ``keep_in_memory``, the thresholds or the saved ones) become the job's
        options, checked by ``validate_options``. The job, titled "Tag output
        images (legacy page)", tags each output image and never a prompt
        (#379). A second legacy batch answers JSON 409; every other refusal is
        one ``error`` frame (both pages read this route as a stream). Closing
        the stream cancels the job.
        """
        from ..autotag import MODELS
        from ..config import MAX_SUGGESTION_LIMIT, AutotagConfig
        from .annotation_suggest import OptionError, validate_options

        params = await self._read_start_params(request)
        if params is None:
            return self._bad_request("Request body must be a JSON object")
        model_type = params.get("model_type", "gguf")
        try:
            general, character = _thresholds_or_saved(
                params.get("general_threshold"), params.get("character_threshold")
            )
        except (ValueError, TypeError):
            return self._bad_request(
                "general_threshold and character_threshold must be numeric"
            )
        existing = (
            "skip"
            if str(params.get("skip_tagged", "true")).lower() == "true"
            else "merge"
        )
        keep_in_memory = str(params.get("keep_in_memory", "true")).lower() == "true"
        if not isinstance(model_type, str) or model_type not in MODELS:
            return await self._stream_sse(
                request, _error_frame(f"Invalid model type: {model_type}")
            )
        if model_type.startswith("wd14"):
            options = {"general_threshold": general, "character_threshold": character}
        else:
            prompt = params.get("prompt")
            template = prompt if isinstance(prompt, str) and prompt.strip() else None
            options = {
                "instructions": template
                or AutotagConfig.default_instructions_text(model_type)
            }
        try:
            _model, options = validate_options(
                model_type,
                options,
                default_limit=AutotagConfig.suggestion_limit(),
                max_limit=MAX_SUGGESTION_LIMIT,
            )
        except OptionError as error:
            return await self._stream_sse(request, _error_frame(str(error)))
        running = self._jobs.active(AUTOTAG_BATCH_JOB_KIND)
        if running is not None and running.title == LEGACY_BATCH_TITLE:
            return _legacy_busy()
        # A job in the way is named before the engine is asked anything.
        busy = self._jobs.blocker(AUTOTAG_BATCH_JOB_KIND, admission="records")
        if busy is not None:
            return await self._stream_sse(
                request, _error_frame(f"{busy.title} already running")
            )
        try:
            job, refusal = await self._start_autotag(
                model_type=model_type,
                existing=existing,
                options=options,
                keep_in_memory=keep_in_memory,
                title=LEGACY_BATCH_TITLE,
                owner_kind="legacy-batch",
            )
        except Exception:
            self.logger.exception("AutoTag error")
            return await self._stream_sse(request, _error_frame(_INTERNAL_ERROR))
        if refusal is not None:
            if refusal.message == f"{LEGACY_BATCH_TITLE} already running":
                # Another legacy start won the race past the check above.
                return _legacy_busy()
            return await self._stream_sse(request, _error_frame(refusal.message))
        return await self._stream_sse(
            request, self._legacy_batch_frames(job, keep_in_memory)
        )

    async def _legacy_batch_frames(self, job, keep_in_memory):
        """The legacy frames of a running ``autotag`` job, then its end frame.

        Phases map to fixed frames; progress frames come at most every 0.5 s;
        a job quiet for 5 s repeats the last frame, so a closed page is noticed
        (aiohttp sees a disconnect only on a write). A stream closed before the
        job ended cancels it.
        """
        events = job.events()
        pending = None
        last = None
        last_progress = None
        try:
            while True:
                if pending is None:
                    pending = asyncio.ensure_future(events.__anext__())
                done, _ = await asyncio.wait({pending}, timeout=LEGACY_REPEAT_AFTER)
                if not done:
                    if last is not None:
                        yield last
                    continue
                try:
                    payload = pending.result()
                except StopAsyncIteration:
                    pending = None
                    break
                pending = None
                frame = self._legacy_frame(job, payload)
                if frame is None:
                    continue
                if frame.get("phase") is None:
                    now = _monotonic()
                    if (
                        last_progress is not None
                        and now - last_progress < LEGACY_FRAME_INTERVAL
                    ):
                        continue
                    last_progress = now
                frame.pop("phase", None)
                last = frame
                yield frame
            yield self._legacy_end_frame(job, keep_in_memory)
        finally:
            if pending is not None:
                pending.cancel()
                with contextlib.suppress(BaseException):
                    await pending
            with contextlib.suppress(BaseException):
                await events.aclose()
            if job.running:
                self._jobs.cancel(job.id)

    @staticmethod
    def _legacy_frame(job, payload):
        """One job event as a legacy progress frame, or None (an end event)."""
        kind = payload.get("type")
        if kind == "phase":
            progress, status = _LEGACY_PHASES.get(payload.get("phase"), (0, ""))
            if not status:
                return None
            return {
                "type": "progress",
                "progress": progress,
                "status": status,
                "phase": True,
            }
        if kind != "progress" or not job.total:
            return None
        return {
            "type": "progress",
            "progress": 10 + job.progress * 85 // 100,
            "status": job.status_line,
            **_legacy_counts(job),
        }

    @staticmethod
    def _legacy_end_frame(job, keep_in_memory):
        if job.state == "finished":
            return {
                "type": "complete",
                "progress": 100,
                **_legacy_counts(job),
                "errors": job.counters.get("errors", 0),
                "skipped_outside_roots": 0,
                "status": "Complete" if job.total else "No still images found",
                "model_status": (
                    "Model kept in memory" if keep_in_memory else "Model unloaded"
                ),
                "model_loaded": keep_in_memory,
            }
        if job.state == "cancelled":
            return {"type": "cancelled", "message": BATCH_CANCELLED}
        return {"type": "error", "status": "error", "message": job.status_line}

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

    async def preview_skipped_tags(self, request):
        """Which known tags a Skipped tags list would hide (#431); no write.

        ``{entries}`` is checked as a save checks it (the same 400 sentence).
        The known tags are the library's plus the general and character names
        of each downloaded WD14 label list; no model is loaded and no image is
        opened. Refused (409 ``busy``) while an exclusive job runs.
        """
        from ..tagging_defaults import validate_skipped_tags

        data, error = await self._batch_body(
            request, ("entries",), SKIPPED_PREVIEW_BODY_MAX
        )
        if error is not None:
            return error
        try:
            entries = validate_skipped_tags(data["entries"])
        except ValueError as exc:
            return web.json_response({"success": False, "error": str(exc)}, status=400)
        try:
            async with self._jobs.lease(SKIPPED_PREVIEW_LEASE):
                result = await self._run_in_executor(self._skipped_preview, entries)
        except JobBusy as busy:
            return busy_response(busy)
        except Exception:
            self.logger.exception("The Skipped tags preview failed")
            return web.json_response(
                {"success": False, "error": _INTERNAL_ERROR}, status=500
            )
        return web.json_response({"success": True, **result})

    def _skipped_preview(self, entries):
        """Worker thread: the preview's counts over the known tags."""
        from ..autotag import MODELS, get_autotag_service
        from ..tagging_defaults import (
            WD14_MODEL_TYPES,
            SkipMatcher,
            wd14_label_names,
        )

        library = self.db.get_all_tags()
        known = {}  # case-folded -> (0 library / 1 label list, spelling)
        for name in library:
            known.setdefault(name.casefold(), (0, name))
        lists = []
        models_dir = getattr(get_autotag_service(), "models_dir", None)
        for model_type in sorted(WD14_MODEL_TYPES, key=lambda t: MODELS[t]["name"]):
            if models_dir is None:
                break
            path = Path(models_dir) / MODELS[model_type]["subdir"] / "selected_tags.csv"
            try:
                names = wd14_label_names(path)
            except (OSError, ValueError) as exc:
                if path not in _MISSING_LABEL_LISTS:  # logged once per list
                    _MISSING_LABEL_LISTS.add(path)
                    self.logger.debug(f"No WD14 label list for {model_type}: {exc}")
                continue
            _MISSING_LABEL_LISTS.discard(path)
            lists.append(MODELS[model_type]["name"])
            for name in names:
                known.setdefault(name.casefold(), (1, name))
        matchers = [(entry, SkipMatcher([entry])) for entry in entries]
        whole = SkipMatcher(entries)
        hidden = [item for item in known.values() if whole.matches(item[1])]
        hidden.sort(key=lambda item: (item[0], item[1].casefold()))
        per_entry = [
            {
                "entry": entry,
                "count": sum(1 for _, name in hidden if matcher.matches(name)),
            }
            for entry, matcher in matchers
        ]
        return {
            "count": len(hidden),
            "sample": [name for _, name in hidden[:SKIPPED_PREVIEW_SAMPLE]],
            "sample_limit": SKIPPED_PREVIEW_SAMPLE,
            "per_entry": per_entry,
            "sources": {"library": len(library), "wd14": lists},
        }

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
                    **package_entry(status.get(model_type) or {}),
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
        roots = list(self._get_all_output_dirs())
        for output_dir in roots:
            if path_is_within(raw_path, output_dir):
                real = os.path.realpath(raw_path)
                if in_trash_below_roots(real, roots):
                    return None  # thrown away, not an output (#543)
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

    def _root_of(self, resolved: str):
        """``(root_index, root, relative)`` of a resolved file inside a root.

        The first output root that holds it (as ``path_is_within`` checks);
        ``relative`` is ``/``-separated below the root's real path, so the
        capture store's alias check holds. None when no root holds it.
        """
        for index, root in enumerate(self._get_all_output_dirs()):
            if path_is_within(resolved, root):
                real_root = os.path.realpath(str(root))
                relative = os.path.relpath(resolved, real_root).replace(os.sep, "/")
                return index, root, relative
        return None

    async def _select_autotag_target(self, data: dict):
        """Pick the image a legacy single/apply body names.

        Returns ``(target, None)`` with ``target`` = ``{path, prompt_id,
        root_index, root, relative}``, or ``(None, error_response)``.
        """
        image_id = data.get("image_id")
        if image_id is not None:
            if isinstance(image_id, bool) or not isinstance(image_id, (int, str)):
                return None, self._bad_request("image_id must be an integer")
            try:
                image_id = int(image_id)
            except (TypeError, ValueError):
                return None, self._bad_request("image_id must be an integer")
            record = await self._run_in_executor(self.db.get_image_by_id, image_id)
            if not record or not record.get("image_path"):
                return None, web.json_response(
                    {"success": False, "error": f"Image {image_id} not found"},
                    status=404,
                )
            stored_path = record["image_path"]
            resolved = self._resolve_allowed_image_path(stored_path)
            if resolved is None:
                return None, self._forbidden(stored_path)
            return self._target_of(resolved, record.get("prompt_id"), stored_path)

        raw_path = data.get("path") or data.get("image_path")
        if not raw_path or not isinstance(raw_path, str):
            return None, self._bad_request("image_id or path is required")

        if "root_index" in data:
            root_index = data["root_index"]
            if isinstance(root_index, bool) or not isinstance(root_index, int):
                return None, self._bad_request("root_index must be an integer")
            roots = self._get_all_output_dirs()
            if not 0 <= root_index < len(roots):
                return None, self._forbidden(raw_path)
            root = roots[root_index]
            if Path(raw_path).is_absolute() or ".." in raw_path.replace(
                "\\", "/"
            ).split("/"):
                return None, self._forbidden(raw_path)
            raw_path = str(Path(root) / raw_path)
            if not path_is_within(raw_path, root):
                return None, self._forbidden(raw_path)

        resolved = self._resolve_allowed_image_path(raw_path)
        if resolved is None:
            return None, self._forbidden(raw_path)

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
        return self._target_of(resolved, prompt_id, raw_path)

    def _target_of(self, resolved, prompt_id, shown):
        located = self._root_of(resolved)
        if located is None:
            return None, self._forbidden(shown)
        index, root, relative = located
        return {
            "path": resolved,
            "prompt_id": prompt_id,
            "root_index": index,
            "root": root,
            "relative": relative,
        }, None

    @staticmethod
    def _bad_request(message: str):
        return web.json_response({"success": False, "error": message}, status=400)

    @staticmethod
    def _coded_error(status: int, code: str, message: str):
        return web.json_response(
            {"success": False, "code": code, "error": message}, status=status
        )

    def _capture_refusal(self, exc, target):
        """The legacy answer for a file the capture store refused (#379)."""
        from .image_tag_jobs import FILE_CHANGED, FILE_NOT_FOUND, TOO_LARGE

        name = type(exc).__name__
        if name == "CaptureTooLarge":
            return self._coded_error(413, "too-large", TOO_LARGE)
        if name == "CaptureCapacity":
            return self._coded_error(
                503, "busy", "The file could not be read now. Try again."
            )
        if name == "CaptureChanged" and os.path.lexists(target["path"]):
            return self._coded_error(409, "file-changed", FILE_CHANGED)
        if name in ("CaptureDenied", "CaptureChanged"):
            return self._coded_error(404, "file-not-found", FILE_NOT_FOUND)
        return self._coded_error(500, "read-failed", "The file could not be read.")

    def _single_options(self, data, model_type):
        """The validated suggestion options of a legacy single body."""
        from ..config import MAX_SUGGESTION_LIMIT, AutotagConfig
        from .annotation_suggest import validate_options

        general, character = _thresholds_or_saved(
            data.get("general_threshold"), data.get("character_threshold")
        )
        if model_type.startswith("wd14"):
            options = {"general_threshold": general, "character_threshold": character}
        else:
            prompt = data.get("prompt")
            template = prompt if isinstance(prompt, str) and prompt.strip() else None
            options = {
                "instructions": template
                or AutotagConfig.default_instructions_text(model_type)
            }
        use_gpu = data.get("use_gpu", True)
        options["use_gpu"] = use_gpu if isinstance(use_gpu, bool) else True
        return validate_options(
            model_type,
            options,
            default_limit=AutotagConfig.suggestion_limit(),
            max_limit=MAX_SUGGESTION_LIMIT,
        )[1]

    async def autotag_single(self, request):
        """Suggest tags for one image (the legacy review), never writing.

        ``existing: "skip"`` answers ``already_tagged`` without taking the
        model gate when the image's current bytes are auto-tagged (#379).
        Otherwise the image is captured and the model runs on the capture
        under the gate, with at most the saved "Suggestions per image";
        the answer carries the captured ``sha256`` that ``/autotag/apply``
        writes against, and the image's current ``image_tags``.
        """
        from ..autotag import MODELS
        from .annotation_suggest import OptionError

        try:
            data = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._bad_request("Request body must be valid JSON")
        if not isinstance(data, dict):
            return self._bad_request("Request body must be a JSON object")
        try:
            model_type = data.get("model_type", "gguf")
            if not isinstance(model_type, str) or model_type not in MODELS:
                return self._bad_request(
                    "model_type must be one of " + ", ".join(sorted(MODELS))
                )
            existing = data.get("existing", "merge")
            if existing not in ("skip", "merge"):
                return self._bad_request('existing must be "skip" or "merge"')
            try:
                options = self._single_options(data, model_type)
            except OptionError as error:
                return self._bad_request(str(error))
            except (ValueError, TypeError):
                return self._bad_request(
                    "general_threshold and character_threshold must be numeric"
                )
            target, error = await self._select_autotag_target(data)
            if error is not None:
                return error
            base = {
                "success": True,
                "prompt_id": target["prompt_id"],
                "filename": os.path.basename(target["path"]),
            }
            if existing == "skip":
                marked = await self._run_in_executor(self._marked_tags, target)
                if marked is not None:
                    return web.json_response(
                        {
                            **base,
                            "already_tagged": True,
                            "tags": [],
                            "image_tags": marked,
                        }
                    )
            return await self._suggest_single(target, model_type, options, base)
        except Exception as e:
            self.logger.error(f"AutoTag single error: {e}")
            return web.json_response(
                {"success": False, "error": self._public_error(e)}, status=500
            )

    def _locator_of(self, target) -> str:
        return os.path.normcase(
            os.path.join(
                os.path.realpath(str(target["root"])), *target["relative"].split("/")
            )
        )

    def _marked_tags(self, target):
        """Worker thread: the current image tags when the image is
        auto-tagged, else None. One stat, no hash."""
        from .images import _current_fingerprint

        fingerprint = _current_fingerprint(target["path"])
        if fingerprint is None:
            return None
        locator = self._locator_of(target)
        if not self.db.autotag_state({locator: fingerprint}).get(locator):
            return None
        return self.db.current_image_tags({locator: fingerprint}).get(locator, [])

    async def _suggest_single(self, target, model_type, options, base):
        from ..autotag import MODELS, MissingBackendPackages, get_autotag_service
        from ..config import AutotagConfig
        from ..tagging_defaults import SkipMatcher
        from .annotation_suggest import model_refusal
        from .annotations import _pinned_suggestions, _settled, _submit

        try:
            from ...utils.media_capture import CaptureError
        except ImportError:
            from utils.media_capture import CaptureError

        service = get_autotag_service()
        status = await self._run_in_executor(service.get_models_status)
        message = model_refusal(status, model_type, MODELS[model_type]["name"])
        if message is not None:
            return self._coded_error(409, "model-unavailable", message)
        owner = model_gate.new_owner("legacy-single")
        if not model_gate.try_acquire(owner):
            return model_busy_response()
        # #431: the legacy review hides the Skipped tags too (19A).
        skip = SkipMatcher(AutotagConfig.SKIPPED_TAGS)
        store = self._capture_store()
        try:
            epoch = await self._run_in_executor(self.db.get_execution_epoch)
            scope = self._gallery_root_scope(target["root"], target["root_index"])
            try:
                capture = await _settled(
                    _submit(
                        store.capture,
                        target["root"],
                        target["relative"],
                        epoch=epoch,
                        scope=scope,
                        root_index=target["root_index"],
                    ),
                    lambda made: made is not None and store.release(made.token),
                )
            except CaptureError as exc:
                return self._capture_refusal(exc, target)
            try:
                if not capture.inferable:
                    await self._mark_untaggable(capture, epoch)
                    return self._coded_error(
                        415, "not-a-still", "Not a supported still image"
                    )
                if store.acquire(capture.token) is None:
                    return self._coded_error(
                        409,
                        "storage-changed",
                        "Prompt storage was restored. Reload the page.",
                    )
                pairs = await model_gate.run(
                    owner,
                    _pinned_suggestions,
                    store,
                    capture.token,
                    service,
                    capture,
                    model_type,
                    options,
                    skip,
                    on_skipped=functools.partial(store.release_use, capture.token),
                )
            except MissingBackendPackages as exc:
                # A package gone since the status read (load_model logged it).
                return self._coded_error(409, "model-unavailable", str(exc))
            finally:
                store.release(capture.token)
        finally:
            model_gate.release(owner)
        current = await self._run_in_executor(
            self.db.read_image_tags, capture.locator, capture.sha256
        )
        return web.json_response(
            {
                **base,
                "tags": [str(name) for name, _score in pairs],
                "sha256": capture.sha256,
                "image_tags": current["tags"],
            }
        )

    async def _mark_untaggable(self, facts, epoch):
        try:
            await self._run_in_executor(
                functools.partial(
                    self.db.add_image_tags,
                    locator=facts.locator,
                    sha256=facts.sha256,
                    size=facts.size,
                    mtime_ns=facts.mtime_ns,
                    expected_epoch=epoch,
                    tags=[],
                    mark_autotagged=True,
                )
            )
        except Exception:
            self.logger.warning("An unsupported image could not be marked")

    async def apply_autotag(self, request):
        """Union reviewed tags into one image's tags and mark it (#379).

        The body names the image (``image_id``, or ``path`` with optional
        ``root_index``), the ``sha256`` ``/autotag/single`` returned and the
        ``tags``; it is written only onto those exact bytes. A body with
        ``prompt_id`` (a page loaded before the update) is refused: no path
        writes prompt tags.
        """
        try:
            from ...database.annotations import ImageTagLimitError, normalize_image_tags
            from ...database.library_commands import LibraryCommandError
            from ...utils.media_capture import CaptureError
        except ImportError:
            from database.annotations import ImageTagLimitError, normalize_image_tags
            from database.library_commands import LibraryCommandError
            from utils.media_capture import CaptureError

        data, error = await self._read_json_body(request, max_bytes=APPLY_BODY_MAX)
        if error is not None:
            return error
        if "prompt_id" in data:
            return self._bad_request("This page is out of date. Reload it.")
        keys = set(data)
        if keys not in (
            {"image_id", "sha256", "tags"},
            {"path", "sha256", "tags"},
            {"path", "root_index", "sha256", "tags"},
        ):
            return self._bad_request(
                "The body must be {image_id, sha256, tags} or "
                "{path, root_index?, sha256, tags}"
            )
        sha256 = data["sha256"]
        if not isinstance(sha256, str) or not _HEX64.fullmatch(sha256):
            return self._bad_request(
                "sha256 must be 64 lowercase hexadecimal characters"
            )
        try:
            tags = normalize_image_tags(data["tags"])
        except ImageTagLimitError as exc:
            return self._coded_error(400, "tag-limit", str(exc))
        except ValueError as exc:
            return self._bad_request(str(exc))
        try:
            epoch = await self._run_in_executor(self.db.get_execution_epoch)
            target, error = await self._select_autotag_target(data)
            if error is not None:
                return error
            store = self._capture_store()
            try:
                facts = await self._run_in_executor(
                    store.verify, target["root"], target["relative"]
                )
            except CaptureError as exc:
                return self._capture_refusal(exc, target)
            if facts.sha256 != sha256:
                return self._coded_error(
                    409,
                    "file-changed",
                    "The image changed since its tags were generated.",
                )
            try:
                receipt = await self._run_in_executor(
                    functools.partial(
                        self.db.add_image_tags,
                        locator=facts.locator,
                        sha256=facts.sha256,
                        size=facts.size,
                        mtime_ns=facts.mtime_ns,
                        expected_epoch=epoch,
                        tags=tags,
                        mark_autotagged=True,
                    )
                )
            except LibraryCommandError as exc:
                if exc.code == "storage-changed":
                    return self._coded_error(
                        409,
                        "storage-changed",
                        "Prompt storage was restored. Reload the page.",
                    )
                if exc.code == "tag-limit":
                    from .annotations import _MESSAGES

                    return self._coded_error(409, "tag-limit", _MESSAGES["tag-limit"])
                raise
        except Exception as e:
            self.logger.error(f"Apply autotag error: {e}")
            return web.json_response(
                {"success": False, "error": self._public_error(e)}, status=500
            )
        added = receipt.get("added", 0)
        return web.json_response(
            {
                "success": True,
                "added_tags": receipt["tags"][-added:] if added else [],
                "total_tags": len(receipt["tags"]),
            }
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

        Descends at most ``_SCAN_MAX_DEPTH`` levels, skips ``thumbnails`` and
        trash folders (``utils.media_folders``) and symlinked files, and stops
        after ``_SCAN_MAX_FILES`` files.
        Returns ``(paths, truncated)``.
        """
        found = []
        base_depth = len(output_path.parts)
        for dirpath, dirnames, filenames in os.walk(output_path):
            depth = len(Path(dirpath).parts) - base_depth
            dirnames[:] = sorted(
                name
                for name in dirnames
                if not is_skipped_folder(name) and depth + 1 <= _SCAN_MAX_DEPTH
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
            # #501: a file removed from the library is not offered for tagging.
            files = await self._run_in_executor(
                self._without_removed, output_path, files
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
