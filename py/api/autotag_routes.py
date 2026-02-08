"""AutoTag API routes for PromptManager."""

import asyncio
import json
from pathlib import Path

from aiohttp import web


class AutotagRoutesMixin:
    """Mixin providing auto-tagging API endpoints."""

    def _register_autotag_routes(self, routes):
        @routes.get("/prompt_manager/autotag/models")
        async def get_autotag_models_route(request):
            return await self.get_autotag_models(request)

        @routes.get("/prompt_manager/autotag/download/{model_type}")
        async def download_autotag_model_route(request):
            return await self.download_autotag_model(request)

        @routes.get("/prompt_manager/autotag/start")
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
                }
            )

        except Exception as e:
            self.logger.error(f"Get autotag models error: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def download_autotag_model(self, request):
        """Download an AutoTag model with streaming progress."""
        model_type = request.match_info.get("model_type")

        async def stream_response():
            try:
                from ..autotag import get_autotag_service

                service = get_autotag_service()

                if model_type not in service.models_config:
                    yield f"data: {json.dumps({'type': 'error', 'message': f'Invalid model type: {model_type}'})}\n\n"
                    return

                yield f"data: {json.dumps({'type': 'progress', 'progress': 0, 'status': 'Starting download...'})}\n\n"

                progress_data = {"last_progress": 0}

                def progress_callback(status: str, progress: float):
                    progress_data["last_progress"] = progress

                loop = asyncio.get_event_loop()
                success = await loop.run_in_executor(
                    None, lambda: service.download_model(model_type, progress_callback)
                )

                if success:
                    yield f"data: {json.dumps({'type': 'complete', 'progress': 100, 'status': 'Download complete'})}\n\n"
                else:
                    yield f"data: {json.dumps({'type': 'error', 'message': 'Download failed'})}\n\n"

            except Exception as e:
                self.logger.exception("Download model error")
                yield f"data: {json.dumps({'type': 'error', 'message': 'An internal error occurred. Check server logs for details.'})}\n\n"

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

        async for chunk in stream_response():
            await response.write(chunk.encode("utf-8"))

        await response.write_eof()
        return response

    async def start_autotag(self, request):
        """Start batch auto-tagging with streaming progress."""
        model_type = request.query.get("model_type", "gguf")
        custom_prompt = request.query.get("prompt", "")
        skip_tagged = request.query.get("skip_tagged", "true").lower() == "true"
        keep_in_memory = request.query.get("keep_in_memory", "true").lower() == "true"
        use_gpu = True

        async def stream_response():
            try:
                from ..autotag import get_autotag_service
                import time as _time

                service = get_autotag_service()

                status = service.get_models_status()
                if not status.get(model_type, {}).get("downloaded"):
                    yield f"data: {json.dumps({'type': 'error', 'message': f'Model {model_type} not downloaded'})}\n\n"
                    return

                yield f"data: {json.dumps({'type': 'progress', 'progress': 0, 'status': 'Loading model...'})}\n\n"

                loop = asyncio.get_event_loop()
                try:
                    await loop.run_in_executor(
                        None, lambda: service.load_model(model_type, use_gpu)
                    )
                except Exception as e:
                    self.logger.error(f"Failed to load model: {e}")
                    yield f"data: {json.dumps({'type': 'error', 'message': 'Failed to load model. Check server logs for details.'})}\n\n"
                    return

                if custom_prompt:
                    service.custom_prompt = custom_prompt

                yield f"data: {json.dumps({'type': 'progress', 'progress': 5, 'status': 'Model loaded. Fetching all images from database...'})}\n\n"

                images = await self._run_in_executor(self.db.get_all_images)

                total_files = len(images)
                if total_files == 0:
                    yield f"data: {json.dumps({'type': 'complete', 'processed': 0, 'tagged': 0, 'skipped': 0, 'status': 'No images with linked prompts found'})}\n\n"
                    service.unload_model()
                    return

                yield f"data: {json.dumps({'type': 'progress', 'progress': 10, 'status': f'Found {total_files} images. Processing...'})}\n\n"

                processed = 0
                tagged = 0
                skipped = 0
                errors = 0
                tagged_prompt_ids = set()

                last_update_time = _time.monotonic()

                for i, image_data in enumerate(images):
                    image_path = image_data.get("image_path")
                    prompt_id = image_data.get("prompt_id")

                    if not image_path or not prompt_id:
                        skipped += 1
                        continue

                    if prompt_id in tagged_prompt_ids:
                        skipped += 1
                        now = _time.monotonic()
                        if (now - last_update_time) >= 0.5 or i == total_files - 1:
                            progress = 10 + int((i + 1) / total_files * 85)
                            yield f"data: {json.dumps({'type': 'progress', 'progress': progress, 'status': f'Skipping {i+1}/{total_files} (prompt already processed)...', 'processed': processed, 'tagged': tagged, 'skipped': skipped})}\n\n"
                            await asyncio.sleep(0.01)
                            last_update_time = now
                        continue

                    if not Path(image_path).exists():
                        skipped += 1
                        now = _time.monotonic()
                        if (now - last_update_time) >= 0.5 or i == total_files - 1:
                            progress = 10 + int((i + 1) / total_files * 85)
                            yield f"data: {json.dumps({'type': 'progress', 'progress': progress, 'status': f'Skipping {i+1}/{total_files} (file missing)...', 'processed': processed, 'tagged': tagged, 'skipped': skipped})}\n\n"
                            await asyncio.sleep(0.01)
                            last_update_time = now
                        continue

                    if skip_tagged:
                        prompt_tags = image_data.get("prompt_tags", [])
                        if isinstance(prompt_tags, str):
                            prompt_tags = [
                                t.strip() for t in prompt_tags.split(",") if t.strip()
                            ]
                        real_tags = [t for t in prompt_tags if t != "auto-scanned"]
                        if real_tags:
                            tagged_prompt_ids.add(prompt_id)
                            skipped += 1
                            now = _time.monotonic()
                            if (now - last_update_time) >= 0.5 or i == total_files - 1:
                                progress = 10 + int((i + 1) / total_files * 85)
                                yield f"data: {json.dumps({'type': 'progress', 'progress': progress, 'status': f'Skipping {i+1}/{total_files} (already tagged)...', 'processed': processed, 'tagged': tagged, 'skipped': skipped})}\n\n"
                                await asyncio.sleep(0.01)
                                last_update_time = now
                            continue

                    try:
                        tags = await loop.run_in_executor(
                            None, lambda p=str(image_path): service.generate_tags(p)
                        )

                        processed += 1

                        if tags:
                            existing_prompt = await self._run_in_executor(
                                self.db.get_prompt_by_id, prompt_id
                            )
                            if existing_prompt:
                                existing_tags = existing_prompt.get("tags", [])
                                if isinstance(existing_tags, str):
                                    existing_tags = [
                                        t.strip()
                                        for t in existing_tags.split(",")
                                        if t.strip()
                                    ]

                                new_tags = [t for t in tags if t not in existing_tags]
                                if new_tags:
                                    all_tags = existing_tags + new_tags
                                    await self._run_in_executor(
                                        self.db.update_prompt_metadata,
                                        prompt_id,
                                        tags=all_tags,
                                    )
                                    tagged += 1
                                else:
                                    skipped += 1
                            else:
                                skipped += 1
                        else:
                            skipped += 1

                        tagged_prompt_ids.add(prompt_id)

                        progress = 10 + int((i + 1) / total_files * 85)
                        yield f"data: {json.dumps({'type': 'progress', 'progress': progress, 'status': f'Processing {i+1}/{total_files}...', 'processed': processed, 'tagged': tagged, 'skipped': skipped})}\n\n"
                        await asyncio.sleep(0.01)
                        last_update_time = _time.monotonic()

                    except Exception as img_err:
                        self.logger.error(f"Error processing {image_path}: {img_err}")
                        errors += 1
                        processed += 1

                if not keep_in_memory:
                    service.unload_model()
                    model_status = "Model unloaded"
                else:
                    model_status = "Model kept in memory"

                yield f"data: {json.dumps({'type': 'complete', 'progress': 100, 'processed': processed, 'tagged': tagged, 'skipped': skipped, 'errors': errors, 'status': 'Complete', 'model_status': model_status, 'model_loaded': keep_in_memory})}\n\n"

            except Exception as e:
                self.logger.error(f"AutoTag error: {e}")
                import traceback

                self.logger.exception("AutoTag error")
                yield f"data: {json.dumps({'type': 'error', 'message': 'An internal error occurred. Check server logs for details.'})}\n\n"

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

        async for chunk in stream_response():
            await response.write(chunk.encode("utf-8"))

        await response.write_eof()
        return response

    async def autotag_single(self, request):
        """Generate tags for a single image."""
        try:
            data = await request.json()
            image_path = data.get("image_path")
            model_type = data.get("model_type", "gguf")
            custom_prompt = data.get("prompt")
            use_gpu = data.get("use_gpu", True)

            if not image_path:
                return web.json_response(
                    {"success": False, "error": "image_path is required"}, status=400
                )

            from ..autotag import get_autotag_service

            service = get_autotag_service()

            if (
                not service.is_model_loaded()
                or service.get_loaded_model_type() != model_type
            ):
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None, lambda: service.load_model(model_type, use_gpu)
                )

            if custom_prompt:
                service.custom_prompt = custom_prompt

            loop = asyncio.get_event_loop()
            tags = await loop.run_in_executor(
                None, lambda: service.generate_tags(image_path)
            )

            prompt_id = None
            try:
                prompt_id = await self._run_in_executor(
                    self.db.get_prompt_id_for_image, image_path
                )
            except Exception as e:
                self.logger.warning(f"Could not find linked prompt: {e}")

            return web.json_response(
                {
                    "success": True,
                    "tags": tags,
                    "prompt_id": prompt_id,
                    "image_path": image_path,
                }
            )

        except Exception as e:
            self.logger.error(f"AutoTag single error: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

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
            return web.json_response({"success": False, "error": str(e)}, status=500)

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
            return web.json_response({"success": False, "error": str(e)}, status=500)

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
            image_extensions = [
                ".png",
                ".jpg",
                ".jpeg",
                ".gif",
                ".webp",
                ".bmp",
                ".tiff",
            ]

            images = []
            seen_paths = set()

            for ext in image_extensions:
                for pattern in [f"*{ext.lower()}", f"*{ext.upper()}"]:
                    for image_path in output_path.rglob(pattern):
                        if "thumbnails" not in image_path.parts:
                            normalized_path = str(image_path).lower()
                            if normalized_path not in seen_paths:
                                seen_paths.add(normalized_path)

                                rel_path = image_path.relative_to(output_path)

                                thumbnail_url = None
                                thumbnails_dir = output_path / "thumbnails"
                                if thumbnails_dir.exists():
                                    rel_path_no_ext = rel_path.with_suffix("")
                                    thumbnail_rel_path = f"thumbnails/{rel_path_no_ext.as_posix()}_thumb{image_path.suffix}"
                                    thumbnail_abs_path = (
                                        thumbnails_dir
                                        / f"{rel_path_no_ext.as_posix()}_thumb{image_path.suffix}"
                                    )
                                    if thumbnail_abs_path.exists():
                                        from urllib.parse import quote

                                        thumbnail_url = f'/prompt_manager/images/serve/{quote(thumbnail_rel_path, safe="/")}'

                                from urllib.parse import quote as url_quote

                                images.append(
                                    {
                                        "filename": image_path.name,
                                        "path": str(image_path),
                                        "relative_path": str(rel_path),
                                        "url": f'/prompt_manager/images/serve/{url_quote(rel_path.as_posix(), safe="/")}',
                                        "thumbnail_url": thumbnail_url,
                                    }
                                )

            images.sort(key=lambda x: x["filename"])

            self.logger.info(f"Found {len(images)} images in output directory")

            return web.json_response(
                {"success": True, "images": images, "count": len(images)}
            )

        except Exception as e:
            self.logger.error(f"Scan output dir error: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)
