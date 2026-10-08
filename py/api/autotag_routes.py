"""AutoTag API routes for PromptManager."""

import asyncio
import json
import os
import threading
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from aiohttp import web

_INTERNAL_ERROR = "An internal error occurred. Check server logs for details."
_BUSY_ERROR = "Auto-tagging is already running"

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


class AutotagRoutesMixin:
    """Mixin providing auto-tagging API endpoints."""

    def _register_autotag_routes(self, routes):
        @routes.get("/prompt_manager/autotag/models")
        async def get_autotag_models_route(request):
            return await self.get_autotag_models(request)

        @routes.post("/prompt_manager/autotag/download/{model_type}")
        async def download_autotag_model_route(request):
            return await self.download_autotag_model(request)

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

            service = get_autotag_service()
            models_status = service.get_models_status()

            return web.json_response(
                {
                    "success": True,
                    "models": models_status,
                    "default_prompt": service.default_prompt,
                    "model_loaded": service.is_model_loaded(),
                    "loaded_model_type": service.get_loaded_model_type(),
                    "wd14_general_threshold": service.wd14_general_threshold,
                    "wd14_character_threshold": service.wd14_character_threshold,
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
        """Download an AutoTag model with streaming progress (POST only)."""
        model_type = request.match_info.get("model_type")

        async def events():
            try:
                from ..autotag import get_autotag_service

                service = get_autotag_service()
                if model_type not in service.models_config:
                    yield {
                        "type": "error",
                        "message": f"Invalid model type: {model_type}",
                    }
                    return

                yield {
                    "type": "progress",
                    "progress": 0,
                    "status": "Starting download...",
                }

                def progress_callback(status: str, progress: float):
                    pass

                success = await self._run_in_executor(
                    service.download_model, model_type, progress_callback
                )
                if success:
                    yield {
                        "type": "complete",
                        "progress": 100,
                        "status": "Download complete",
                    }
                else:
                    yield {"type": "error", "message": "Download failed"}
            except Exception:
                self.logger.exception("Download model error")
                yield {
                    "type": "error",
                    "message": _INTERNAL_ERROR,
                }

        return await self._stream_sse(request, events())

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

    async def _apply_tags_to_prompt(self, prompt_id, tags) -> bool:
        """Merge ``tags`` into the prompt; True when something new was added."""
        existing_prompt = await self._run_in_executor(
            self.db.get_prompt_by_id, prompt_id
        )
        if not existing_prompt:
            return False
        existing_tags = existing_prompt.get("tags", [])
        if isinstance(existing_tags, str):
            existing_tags = [t.strip() for t in existing_tags.split(",") if t.strip()]
        new_tags = [t for t in tags if t not in existing_tags]
        if not new_tags:
            return False
        await self._run_in_executor(
            self.db.update_prompt_metadata, prompt_id, tags=existing_tags + new_tags
        )
        return True

    async def _autotag_batch_events(self, service, opts: dict):
        """Yield SSE progress dicts while tagging every image linked to a prompt."""
        import time as _time

        model_type = opts["model_type"]
        keep_in_memory = opts["keep_in_memory"]
        status = service.get_models_status()
        if not status.get(model_type, {}).get("downloaded"):
            yield {"type": "error", "message": f"Model {model_type} not downloaded"}
            return

        yield {"type": "progress", "progress": 0, "status": "Loading model..."}
        try:
            await self._run_in_executor(service.load_model, model_type, True)
        except Exception:
            self.logger.exception("Failed to load model")
            yield {
                "type": "error",
                "message": "Failed to load model. Check server logs for details.",
            }
            return

        if opts["custom_prompt"]:
            service.custom_prompt = opts["custom_prompt"]

        yield {
            "type": "progress",
            "progress": 5,
            "status": "Model loaded. Fetching all images from database...",
        }
        images = await self._run_in_executor(self.db.get_all_images)
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
        last_update = _time.monotonic()
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
                    elif opts["skip_tagged"] and self._real_tags(
                        image_data.get("prompt_tags", [])
                    ):
                        tagged_prompt_ids.add(prompt_id)
                        skip_reason = "already tagged"

                if skip_reason is not None:
                    counts["skipped"] += 1
                    now = _time.monotonic()
                    if (now - last_update) >= 0.5 or i == total - 1:
                        yield progress_event(
                            i, f"Skipping {i + 1}/{total} ({skip_reason})..."
                        )
                        await asyncio.sleep(0.01)
                        last_update = now
                    continue

                try:
                    tags = await self._run_in_executor(
                        service.generate_tags,
                        allowed_path,
                        general_threshold=opts["general_threshold"],
                        character_threshold=opts["character_threshold"],
                    )
                    counts["processed"] += 1
                    if tags and await self._apply_tags_to_prompt(prompt_id, tags):
                        counts["tagged"] += 1
                    else:
                        counts["skipped"] += 1
                    tagged_prompt_ids.add(prompt_id)
                except Exception as img_err:
                    self.logger.error(
                        f"Error processing {os.path.basename(str(image_path))}: "
                        f"{img_err}"
                    )
                    counts["errors"] += 1
                    counts["processed"] += 1

                yield progress_event(i, f"Processing {i + 1}/{total}...")
                await asyncio.sleep(0.01)
                last_update = _time.monotonic()

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
            "skip_tagged": str(params.get("skip_tagged", "true")).lower() == "true",
            "keep_in_memory": str(params.get("keep_in_memory", "true")).lower()
            == "true",
            "general_threshold": params.get("general_threshold"),
            "character_threshold": params.get("character_threshold"),
        }
        try:
            for key in ("general_threshold", "character_threshold"):
                if opts[key] is not None:
                    opts[key] = float(opts[key])
        except (ValueError, TypeError):
            return self._bad_request(
                "general_threshold and character_threshold must be numeric"
            )

        async def events():
            try:
                from ..autotag import get_autotag_service

                async for event in self._autotag_batch_events(
                    get_autotag_service(), opts
                ):
                    yield event
            except Exception:
                self.logger.exception("AutoTag error")
                yield {
                    "type": "error",
                    "message": _INTERNAL_ERROR,
                }

        if not _autotag_batch_lock.acquire(blocking=False):
            return web.json_response(
                {"success": False, "error": _BUSY_ERROR}, status=409
            )
        try:
            return await self._stream_sse(request, events())
        finally:
            _autotag_batch_lock.release()

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
                if general_threshold is not None:
                    general_threshold = float(general_threshold)
                if character_threshold is not None:
                    character_threshold = float(character_threshold)
            except (ValueError, TypeError):
                return self._bad_request(
                    "general_threshold and character_threshold must be numeric"
                )

            image_path, prompt_id, error = await self._select_autotag_target(data)
            if error is not None:
                return error

            from ..autotag import get_autotag_service

            service = get_autotag_service()

            if (
                not service.is_model_loaded()
                or service.get_loaded_model_type() != model_type
            ):
                await self._run_in_executor(service.load_model, model_type, use_gpu)

            if custom_prompt:
                service.custom_prompt = custom_prompt

            tags = await self._run_in_executor(
                service.generate_tags,
                image_path,
                general_threshold=general_threshold,
                character_threshold=character_threshold,
            )

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

            if not service.is_model_loaded():
                return web.json_response(
                    {"success": True, "message": "No model was loaded"}
                )

            model_type = service.get_loaded_model_type()
            service.unload_model()

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
                    thumb_rel = (
                        f"thumbnails/{rel_path.with_suffix('').as_posix()}"
                        f"_thumb{image_path.suffix}"
                    )
                    if (output_path / thumb_rel).exists():
                        thumbnail_url = "/prompt_manager/images/serve/" + quote(
                            thumb_rel, safe="/"
                        )
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
