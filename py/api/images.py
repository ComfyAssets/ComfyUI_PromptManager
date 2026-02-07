"""Image and gallery API routes for PromptManager."""

import asyncio
import json
import os
import re
import time as _time
import urllib.parse
from pathlib import Path

from aiohttp import web
from PIL import Image


class ImageRoutesMixin:
    """Mixin providing image and gallery-related API endpoints."""

    def _register_image_routes(self, routes):
        @routes.get("/prompt_manager/prompts/{prompt_id}/images")
        async def get_prompt_images_route(request):
            return await self.get_prompt_images(request)

        @routes.get("/prompt_manager/images/recent")
        async def get_recent_images_route(request):
            return await self.get_recent_images(request)

        @routes.get("/prompt_manager/images/all")
        async def get_all_images_route(request):
            return await self.get_all_images(request)

        @routes.get("/prompt_manager/images/search")
        async def search_images_route(request):
            return await self.search_images(request)

        @routes.get("/prompt_manager/images/output")
        async def get_output_images_route(request):
            return await self.get_output_images(request)

        @routes.get("/prompt_manager/images/{image_id}/file")
        async def serve_image_route(request):
            return await self.serve_image(request)

        @routes.get("/prompt_manager/images/serve/{filepath:.*}")
        async def serve_output_image_route(request):
            return await self.serve_output_image(request)

        @routes.post("/prompt_manager/images/link")
        async def link_image_route(request):
            return await self.link_image_to_prompt(request)

        @routes.get("/prompt_manager/images/prompt/{image_path:.*}")
        async def get_image_prompt_route(request):
            return await self.get_image_prompt(request)

        @routes.delete("/prompt_manager/images/{image_id}")
        async def delete_image_route(request):
            return await self.delete_image(request)

        @routes.post("/prompt_manager/images/generate-thumbnails")
        async def generate_thumbnails_route(request):
            return await self.generate_thumbnails(request)

        @routes.get("/prompt_manager/images/generate-thumbnails/progress")
        async def generate_thumbnails_progress_route(request):
            return await self.generate_thumbnails_with_progress(request)

        @routes.post("/prompt_manager/images/clear-thumbnails")
        async def clear_thumbnails_route(request):
            return await self.clear_thumbnails(request)

    async def get_prompt_images(self, request):
        """Get all images for a specific prompt."""
        try:
            prompt_id = request.match_info["prompt_id"]
            images = await self._run_in_executor(self.db.get_prompt_images, prompt_id)

            # Clean up any NaN values that cause JSON parsing errors (recursive)
            cleaned_images = [self._clean_nan_recursive(image) for image in images]

            # Additional fallback: convert to JSON string and clean NaN values manually
            try:
                response_data = {"success": True, "images": cleaned_images}
                # Convert to JSON string
                json_str = json.dumps(response_data, default=str)

                # Clean any remaining NaN values with regex
                json_str = re.sub(r":\s*NaN", ": null", json_str)
                json_str = re.sub(r"\[\s*NaN\s*\]", "[null]", json_str)
                json_str = re.sub(r",\s*NaN\s*,", ", null,", json_str)
                json_str = re.sub(r",\s*NaN\s*\]", ", null]", json_str)
                json_str = re.sub(r"\[\s*NaN\s*,", "[null,", json_str)

                # Parse back to verify it's valid JSON
                cleaned_data = json.loads(json_str)

                return web.json_response(cleaned_data)
            except Exception as json_error:
                self.logger.error(f"JSON cleaning error: {json_error}")
                # Fallback to original response
                return web.json_response({"success": True, "images": cleaned_images})

        except Exception as e:
            self.logger.error(f"Get prompt images error: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def get_recent_images(self, request):
        """Get recently generated images."""
        try:
            limit = int(request.query.get("limit", 50))
            images = await self._run_in_executor(self.db.get_recent_images, limit)

            return web.json_response({"success": True, "images": images})
        except Exception as e:
            self.logger.error(f"Get recent images error: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def get_all_images(self, request):
        """Get all generated images with linked prompts."""
        try:
            images = await self._run_in_executor(self.db.get_all_images)

            return web.json_response(
                {"success": True, "images": images, "count": len(images)}
            )
        except Exception as e:
            self.logger.error(f"Get all images error: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def search_images(self, request):
        """Search images by prompt text."""
        try:
            query = request.query.get("q", "")
            if not query:
                return web.json_response(
                    {"success": False, "error": "Search query required"}, status=400
                )

            images = await self._run_in_executor(self.db.search_images_by_prompt, query)

            return web.json_response(
                {"success": True, "images": images, "query": query}
            )
        except Exception as e:
            self.logger.error(f"Search images error: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    def _scan_gallery_files_sync(self, output_path):
        """Scan output directory for media files (blocking I/O, run in executor)."""
        image_extensions = [".png", ".jpg", ".jpeg", ".webp", ".gif"]
        video_extensions = [".mp4", ".webm", ".avi", ".mov", ".mkv", ".m4v", ".wmv"]
        media_extensions = image_extensions + video_extensions
        all_images = []

        seen_paths = set()
        for ext in media_extensions:
            for pattern in [f"*{ext}", f"*{ext.upper()}"]:
                for media_path in output_path.rglob(pattern):
                    if "thumbnails" not in media_path.parts:
                        normalized_path = str(media_path).lower()
                        if normalized_path not in seen_paths:
                            seen_paths.add(normalized_path)
                            all_images.append(media_path)

        # Sort by modification time (newest first)
        all_images.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        return all_images

    async def _get_gallery_files(self, output_path):
        """Get gallery files with TTL cache. Invalidated by image monitor."""
        now = _time.monotonic()
        if (
            self._gallery_cache is not None
            and (now - self._gallery_cache_time) < self._gallery_cache_ttl
        ):
            return self._gallery_cache

        all_images = await self._run_in_executor(
            self._scan_gallery_files_sync, output_path
        )
        self._gallery_cache = all_images
        self._gallery_cache_time = now
        return all_images

    async def get_output_images(self, request):
        """Get all images from ComfyUI output folder."""
        try:
            from urllib.parse import quote

            # Find ComfyUI output directory
            output_dir = self._find_comfyui_output_dir()
            if not output_dir:
                return web.json_response(
                    {
                        "success": False,
                        "error": "ComfyUI output directory not found",
                        "images": [],
                    }
                )

            # Get pagination parameters
            limit = int(request.query.get("limit", 100))
            offset = int(request.query.get("offset", 0))

            output_path = Path(output_dir)
            thumbnails_dir = output_path / "thumbnails"
            video_extensions = [".mp4", ".webm", ".avi", ".mov", ".mkv", ".m4v", ".wmv"]

            # Use cached file listing (Fix 2.4)
            all_images = await self._get_gallery_files(output_path)

            # Apply pagination
            paginated_images = all_images[offset : offset + limit]

            # Format media data in executor (stat calls are blocking)
            def _format_page():
                images = []
                for media_path in paginated_images:
                    try:
                        stat = media_path.stat()
                        rel_path = media_path.relative_to(output_path)
                        extension = media_path.suffix.lower()
                        is_video = extension in video_extensions
                        media_type = "video" if is_video else "image"

                        thumbnail_url = None
                        if thumbnails_dir.exists():
                            thumbnail_ext = ".jpg" if is_video else extension
                            rel_path_no_ext = rel_path.with_suffix("")
                            thumbnail_rel_path = f"thumbnails/{rel_path_no_ext.as_posix()}_thumb{thumbnail_ext}"
                            thumbnail_abs_path = output_path / thumbnail_rel_path
                            if thumbnail_abs_path.exists():
                                thumbnail_url = f'/prompt_manager/images/serve/{quote(thumbnail_rel_path, safe="/")}'

                        images.append(
                            {
                                "id": str(hash(str(media_path))),
                                "filename": media_path.name,
                                "path": str(media_path),
                                "relative_path": str(rel_path),
                                "url": f"/prompt_manager/images/serve/{rel_path.as_posix()}",
                                "thumbnail_url": thumbnail_url,
                                "size": stat.st_size,
                                "modified_time": stat.st_mtime,
                                "extension": extension,
                                "media_type": media_type,
                                "is_video": is_video,
                            }
                        )
                    except Exception as e:
                        self.logger.error(f"Error processing media {media_path}: {e}")
                        continue
                return images

            images = await self._run_in_executor(_format_page)

            return web.json_response(
                {
                    "success": True,
                    "images": images,
                    "total": len(all_images),
                    "offset": offset,
                    "limit": limit,
                    "has_more": offset + limit < len(all_images),
                }
            )

        except Exception as e:
            self.logger.error(f"Get output images error: {e}")
            return web.json_response(
                {"success": False, "error": str(e), "images": []}, status=500
            )

    async def serve_image(self, request):
        """Serve the actual image file using streamed FileResponse."""
        try:
            image_id = int(request.match_info["image_id"])
            image = await self._run_in_executor(self.db.get_image_by_id, image_id)

            if not image:
                return web.json_response(
                    {"success": False, "error": "Image not found"}, status=404
                )

            image_path = Path(image["image_path"]).resolve()

            # Validate path is within the ComfyUI output directory
            output_dir = self._find_comfyui_output_dir()
            if output_dir:
                output_path = Path(output_dir).resolve()
                if not image_path.is_relative_to(output_path):
                    return web.json_response(
                        {"success": False, "error": "Access denied"}, status=403
                    )

            if not image_path.exists():
                return web.json_response(
                    {"success": False, "error": "Image file not found"}, status=404
                )

            response = web.FileResponse(image_path)
            response.headers["Cache-Control"] = "public, max-age=3600"
            return response

        except ValueError:
            return web.json_response(
                {"success": False, "error": "Invalid image ID"}, status=400
            )
        except Exception as e:
            self.logger.error(f"Serve image error: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def serve_output_image(self, request):
        """Serve image file directly from ComfyUI output folder using streamed FileResponse."""
        try:
            filepath = request.match_info["filepath"]

            # Find ComfyUI output directory
            output_dir = self._find_comfyui_output_dir()
            if not output_dir:
                return web.json_response(
                    {"success": False, "error": "ComfyUI output directory not found"},
                    status=404,
                )

            # Construct full image path
            image_path = Path(output_dir) / filepath

            # Security check: make sure the path is within the output directory
            try:
                image_path = image_path.resolve()
                output_path = Path(output_dir).resolve()
                if not image_path.is_relative_to(output_path):
                    return web.json_response(
                        {"success": False, "error": "Access denied"}, status=403
                    )
            except Exception:
                return web.json_response(
                    {"success": False, "error": "Invalid file path"}, status=400
                )

            if not image_path.exists():
                return web.json_response(
                    {"success": False, "error": "Image file not found"}, status=404
                )

            response = web.FileResponse(image_path)
            response.headers["Cache-Control"] = "public, max-age=3600"
            return response

        except Exception as e:
            self.logger.error(f"Serve output image error: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def generate_thumbnails(self, request):
        """Generate thumbnails for all images and videos in the ComfyUI output directory."""
        try:
            # Get request parameters
            data = await request.json()
            quality = data.get("quality", "medium")

            # Map quality to size
            size_map = {"low": (150, 150), "medium": (300, 300), "high": (600, 600)}
            thumbnail_size = size_map.get(quality, (300, 300))

            # Find ComfyUI output directory
            output_dir = self._find_comfyui_output_dir()
            if not output_dir:
                return web.json_response(
                    {"success": False, "error": "ComfyUI output directory not found"},
                    status=404,
                )

            output_path = Path(output_dir)
            thumbnails_dir = output_path / "thumbnails"

            # Run the entire thumbnail generation in executor (heavy PIL I/O)
            result = await self._run_in_executor(
                self._generate_thumbnails_sync,
                output_path,
                thumbnails_dir,
                thumbnail_size,
            )

            # Invalidate gallery cache since thumbnails changed
            self.invalidate_gallery_cache()

            return web.json_response(result)

        except ImportError:
            return web.json_response(
                {
                    "success": False,
                    "error": "PIL (Pillow) library not available. Install with: pip install Pillow",
                },
                status=500,
            )
        except Exception as e:
            self.logger.error(f"Generate thumbnails error: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    def _generate_thumbnails_sync(self, output_path, thumbnails_dir, thumbnail_size):
        """Blocking thumbnail generation loop (run in executor)."""
        import time

        thumbnails_dir.mkdir(exist_ok=True)

        self.logger.info("Scanning for media files to generate thumbnails...")
        image_extensions = [".png", ".jpg", ".jpeg", ".webp", ".gif"]
        video_extensions = [".mp4", ".webm", ".avi", ".mov", ".mkv", ".m4v", ".wmv"]
        media_extensions = image_extensions + video_extensions
        media_files = []
        for root, dirs, files in os.walk(output_path):
            if "thumbnails" in Path(root).parts:
                continue
            for file in files:
                if any(file.lower().endswith(ext) for ext in media_extensions):
                    media_files.append(Path(root) / file)

        total_images = len(media_files)
        self.logger.info(f"Found {total_images} media files to process for thumbnails")

        if total_images == 0:
            return {
                "success": True,
                "count": 0,
                "total_images": 0,
                "message": "No media files found to process",
                "errors": [],
            }

        generated_count = 0
        skipped_count = 0
        errors = []
        start_time = time.time()

        for i, media_file in enumerate(media_files):
            try:
                rel_path = media_file.relative_to(output_path)
                is_video = any(
                    media_file.name.lower().endswith(ext) for ext in video_extensions
                )

                rel_path_no_ext = rel_path.with_suffix("")
                if is_video:
                    thumbnail_path = (
                        thumbnails_dir / f"{rel_path_no_ext.as_posix()}_thumb.jpg"
                    )
                else:
                    thumbnail_path = (
                        thumbnails_dir
                        / f"{rel_path_no_ext.as_posix()}_thumb{rel_path.suffix}"
                    )

                if (
                    thumbnail_path.exists()
                    and thumbnail_path.stat().st_mtime > media_file.stat().st_mtime
                ):
                    skipped_count += 1
                    continue

                thumbnail_path.parent.mkdir(parents=True, exist_ok=True)

                if is_video:
                    if self._generate_video_thumbnail(
                        media_file, thumbnail_path, thumbnail_size
                    ):
                        generated_count += 1
                    else:
                        errors.append(
                            f"Failed to generate video thumbnail for {media_file.name}"
                        )
                else:
                    with Image.open(media_file) as img:
                        if img.mode in ("RGBA", "LA", "P"):
                            img = img.convert("RGB")
                        img.thumbnail(thumbnail_size, Image.Resampling.LANCZOS)
                        save_kwargs = {"quality": 85, "optimize": True}
                        if thumbnail_path.suffix.lower() == ".png":
                            save_kwargs = {"optimize": True}
                        img.save(thumbnail_path, **save_kwargs)
                        generated_count += 1

                if (
                    generated_count % 100 == 0
                    or (i + 1) % max(1, total_images // 10) == 0
                ):
                    progress = ((i + 1) / total_images) * 100
                    elapsed = time.time() - start_time
                    rate = (i + 1) / elapsed if elapsed > 0 else 0
                    eta = ((total_images - i - 1) / rate) if rate > 0 else 0
                    self.logger.info(
                        f"Thumbnail progress: {i+1}/{total_images} ({progress:.1f}%) - "
                        f"Generated: {generated_count}, Skipped: {skipped_count}, "
                        f"Rate: {rate:.1f} img/s, ETA: {eta:.0f}s"
                    )

            except Exception as e:
                error_msg = (
                    f"Failed to generate thumbnail for {media_file.name}: {str(e)}"
                )
                errors.append(error_msg)
                self.logger.warning(error_msg)

        elapsed_time = time.time() - start_time
        self.logger.info(
            f"Thumbnail generation completed: {generated_count} generated, "
            f"{skipped_count} skipped, {len(errors)} errors in {elapsed_time:.1f}s"
        )

        return {
            "success": True,
            "count": generated_count,
            "skipped": skipped_count,
            "total_images": total_images,
            "errors": errors,
            "thumbnails_path": str(thumbnails_dir),
            "elapsed_time": round(elapsed_time, 2),
            "processing_rate": round(
                (total_images / elapsed_time) if elapsed_time > 0 else 0, 2
            ),
        }

    async def generate_thumbnails_with_progress(self, request):
        """Generate thumbnails with Server-Sent Events progress updates."""
        try:
            import time

            # Parse query parameters
            quality = request.query.get("quality", "medium")

            # Map quality to size
            size_map = {"low": (150, 150), "medium": (300, 300), "high": (600, 600)}
            thumbnail_size = size_map.get(quality, (300, 300))

            # Set up SSE response
            response = web.StreamResponse(
                status=200,
                headers={
                    "Content-Type": "text/event-stream",
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "Access-Control-Allow-Origin": "*",
                },
            )
            await response.prepare(request)

            async def send_progress(event_type, data):
                """Send SSE event to client."""
                try:
                    message = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
                    await response.write(message.encode("utf-8"))
                    await asyncio.sleep(0.01)
                except Exception as e:
                    self.logger.warning(f"Failed to send SSE message: {e}")

            try:
                # Find ComfyUI output directory
                output_dir = self._find_comfyui_output_dir()
                if not output_dir:
                    await send_progress(
                        "error", {"error": "ComfyUI output directory not found"}
                    )
                    return response

                output_path = Path(output_dir)
                thumbnails_dir = output_path / "thumbnails"
                thumbnails_dir.mkdir(exist_ok=True)

                # Send scanning event
                await send_progress(
                    "status",
                    {
                        "phase": "scanning",
                        "message": f"Scanning {output_path} for images and videos to process...",
                    },
                )

                self.logger.info(
                    f"Starting thumbnail generation scan in: {output_path}"
                )

                # Find all media files (images and videos)
                image_extensions = [".png", ".jpg", ".jpeg", ".webp", ".gif"]
                video_extensions = [
                    ".mp4",
                    ".webm",
                    ".avi",
                    ".mov",
                    ".mkv",
                    ".m4v",
                    ".wmv",
                ]
                media_extensions = image_extensions + video_extensions
                media_files = []
                scanned_dirs = 0

                for root, dirs, files in os.walk(output_path):
                    if "thumbnails" in Path(root).parts:
                        continue

                    scanned_dirs += 1
                    if scanned_dirs % 5 == 0:
                        await send_progress(
                            "status",
                            {
                                "phase": "scanning",
                                "message": f"Scanning directories... ({scanned_dirs} checked, {len(media_files)} files found)",
                            },
                        )

                    for file in files:
                        if any(file.lower().endswith(ext) for ext in media_extensions):
                            media_files.append(Path(root) / file)

                self.logger.info(
                    f"Scan complete: Found {len(media_files)} media files in {scanned_dirs} directories"
                )

                total_images = len(media_files)

                # Count images vs videos for more detail
                image_count = sum(
                    1
                    for f in media_files
                    if any(f.name.lower().endswith(ext) for ext in image_extensions)
                )
                video_count = total_images - image_count

                await send_progress(
                    "start",
                    {
                        "total_images": total_images,
                        "phase": "processing",
                        "message": f"Found {image_count} images and {video_count} videos to process",
                        "image_count": image_count,
                        "video_count": video_count,
                    },
                )

                self.logger.info(
                    f"Starting thumbnail generation for {image_count} images and {video_count} videos"
                )

                if total_images == 0:
                    await send_progress(
                        "complete",
                        {
                            "count": 0,
                            "skipped": 0,
                            "total_images": 0,
                            "elapsed_time": 0,
                            "message": "No media files found to process",
                        },
                    )
                    return response

                generated_count = 0
                skipped_count = 0
                errors = []
                start_time = time.time()

                for i, media_file in enumerate(media_files):
                    try:
                        if not media_file.exists() or not media_file.is_file():
                            continue

                        is_video = any(
                            media_file.name.lower().endswith(ext)
                            for ext in video_extensions
                        )

                        rel_path = media_file.relative_to(output_path)

                        rel_path_no_ext = rel_path.with_suffix("")
                        if is_video:
                            thumbnail_path = (
                                thumbnails_dir
                                / f"{rel_path_no_ext.as_posix()}_thumb.jpg"
                            )
                        else:
                            thumbnail_path = (
                                thumbnails_dir
                                / f"{rel_path_no_ext.as_posix()}_thumb{rel_path.suffix}"
                            )

                        # Ensure thumbnail path is within our thumbnails directory
                        try:
                            thumbnail_path = thumbnail_path.resolve()
                            thumbnails_dir_resolved = thumbnails_dir.resolve()
                            if not str(thumbnail_path).startswith(
                                str(thumbnails_dir_resolved)
                            ):
                                self.logger.warning(
                                    f"Skipping thumbnail outside safe directory: {thumbnail_path}"
                                )
                                continue
                        except Exception as e:
                            self.logger.warning(
                                f"Path validation failed for {rel_path}: {e}"
                            )
                            continue

                        # Skip if thumbnail already exists and is newer than original
                        if (
                            thumbnail_path.exists()
                            and thumbnail_path.stat().st_mtime
                            > media_file.stat().st_mtime
                        ):
                            skipped_count += 1
                        else:
                            thumbnail_path.parent.mkdir(parents=True, exist_ok=True)

                            if is_video:
                                if self._generate_video_thumbnail(
                                    media_file, thumbnail_path, thumbnail_size
                                ):
                                    generated_count += 1
                                else:
                                    errors.append(
                                        f"Failed to generate video thumbnail for {media_file.name}"
                                    )
                            else:
                                with Image.open(media_file) as img:
                                    if img.mode in ("RGBA", "LA", "P"):
                                        img = img.convert("RGB")
                                    img.thumbnail(
                                        thumbnail_size, Image.Resampling.LANCZOS
                                    )
                                    save_kwargs = {"quality": 85, "optimize": True}
                                    if thumbnail_path.suffix.lower() == ".png":
                                        save_kwargs = {"optimize": True}
                                    img.save(thumbnail_path, **save_kwargs)
                                    generated_count += 1

                        # Send progress update
                        if (
                            (i + 1) % 5 == 0
                            or (i + 1) % max(1, total_images // 100) == 0
                            or i == total_images - 1
                        ):
                            elapsed = time.time() - start_time
                            progress_percent = ((i + 1) / total_images) * 100
                            rate = (i + 1) / elapsed if elapsed > 0 else 0
                            eta = ((total_images - i - 1) / rate) if rate > 0 else 0

                            file_info = {
                                "name": media_file.name,
                                "dir": media_file.parent.name,
                                "type": "video" if is_video else "image",
                                "action": (
                                    "skipped"
                                    if thumbnail_path.exists()
                                    else "generating"
                                ),
                            }

                            await send_progress(
                                "progress",
                                {
                                    "processed": i + 1,
                                    "total_images": total_images,
                                    "generated": generated_count,
                                    "skipped": skipped_count,
                                    "percentage": round(progress_percent, 1),
                                    "rate": round(rate, 1),
                                    "eta": round(eta, 0),
                                    "elapsed": round(elapsed, 1),
                                    "current_file": f"{file_info['dir']}/{file_info['name']}",
                                    "file_type": file_info["type"],
                                    "action": file_info["action"],
                                },
                            )

                            if (i + 1) % 50 == 0:
                                self.logger.info(
                                    f"Thumbnail progress: {i+1}/{total_images} ({progress_percent:.1f}%) - Generated: {generated_count}, Skipped: {skipped_count}"
                                )

                    except Exception as e:
                        error_msg = f"Failed to generate thumbnail for {media_file.name}: {str(e)}"
                        errors.append(error_msg)
                        self.logger.warning(error_msg)

                        if len(errors) <= 5:
                            await send_progress(
                                "status",
                                {
                                    "phase": "processing",
                                    "message": f"Error processing {media_file.name}: {str(e)}",
                                },
                            )

                elapsed_time = time.time() - start_time

                completion_message = f"Successfully generated {generated_count} new thumbnails, skipped {skipped_count} existing"
                if errors:
                    completion_message += f" ({len(errors)} errors occurred)"

                await send_progress(
                    "complete",
                    {
                        "count": generated_count,
                        "skipped": skipped_count,
                        "total_images": total_images,
                        "errors": errors[:10],
                        "error_count": len(errors),
                        "elapsed_time": round(elapsed_time, 2),
                        "processing_rate": round(
                            (total_images / elapsed_time) if elapsed_time > 0 else 0, 2
                        ),
                        "message": completion_message,
                    },
                )

                self.logger.info(
                    f"Thumbnail generation completed: {generated_count} generated, {skipped_count} skipped, {len(errors)} errors in {elapsed_time:.2f}s"
                )

            except Exception as e:
                await send_progress(
                    "error",
                    {
                        "error": str(e),
                        "message": f"Thumbnail generation failed: {str(e)}",
                    },
                )

            return response

        except ImportError:
            return web.json_response(
                {
                    "success": False,
                    "error": "PIL (Pillow) library not available. Install with: pip install Pillow",
                },
                status=500,
            )
        except Exception as e:
            self.logger.error(f"Generate thumbnails with progress error: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    def _generate_video_thumbnail(self, video_path, thumbnail_path, thumbnail_size):
        """Generate thumbnail from video file. Returns True if successful."""
        try:
            # Try using OpenCV first (most reliable)
            try:
                import cv2

                cap = cv2.VideoCapture(str(video_path))
                if not cap.isOpened():
                    return False

                frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                target_frame = max(1, int(frame_count * 0.1))
                cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)

                ret, frame = cap.read()
                cap.release()

                if not ret or frame is None:
                    return False

                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                img = Image.fromarray(frame_rgb)
                img.thumbnail(thumbnail_size, Image.Resampling.LANCZOS)
                img.save(thumbnail_path, "JPEG", quality=85, optimize=True)
                self.logger.debug(
                    f"Generated video thumbnail using OpenCV: {thumbnail_path}"
                )
                return True

            except ImportError:
                pass

            # Fallback to ffmpeg
            try:
                import subprocess

                cmd = [
                    "ffmpeg",
                    "-i",
                    str(video_path),
                    "-ss",
                    "00:00:01",
                    "-vframes",
                    "1",
                    "-s",
                    f"{thumbnail_size[0]}x{thumbnail_size[1]}",
                    "-y",
                    str(thumbnail_path),
                ]

                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    self.logger.debug(
                        f"Generated video thumbnail using ffmpeg: {thumbnail_path}"
                    )
                    return True
                else:
                    self.logger.warning(
                        f"ffmpeg failed for {video_path}: {result.stderr}"
                    )

            except (ImportError, subprocess.TimeoutExpired, FileNotFoundError):
                pass

            # Last resort: create a placeholder thumbnail
            try:
                from PIL import ImageDraw, ImageFont

                img = Image.new("RGB", thumbnail_size, color=(50, 50, 50))
                draw = ImageDraw.Draw(img)

                center_x, center_y = thumbnail_size[0] // 2, thumbnail_size[1] // 2
                triangle_size = min(thumbnail_size) // 4

                points = [
                    (center_x - triangle_size // 2, center_y - triangle_size // 2),
                    (center_x - triangle_size // 2, center_y + triangle_size // 2),
                    (center_x + triangle_size // 2, center_y),
                ]
                draw.polygon(points, fill=(255, 255, 255))

                try:
                    font = ImageFont.load_default()
                    text = "VIDEO"
                    bbox = draw.textbbox((0, 0), text, font=font)
                    text_width = bbox[2] - bbox[0]
                    draw.text(
                        (
                            center_x - text_width // 2,
                            center_y + triangle_size // 2 + 10,
                        ),
                        text,
                        fill=(255, 255, 255),
                        font=font,
                    )
                except (OSError, AttributeError):
                    pass

                img.save(thumbnail_path, "JPEG", quality=85)
                self.logger.debug(
                    f"Generated placeholder video thumbnail: {thumbnail_path}"
                )
                return True

            except Exception as e:
                self.logger.warning(
                    f"Failed to create placeholder thumbnail for {video_path}: {e}"
                )
                return False

        except Exception as e:
            self.logger.error(
                f"Video thumbnail generation failed for {video_path}: {e}"
            )
            return False

    async def clear_thumbnails(self, request):
        """Safely clear only our generated thumbnails, never touch original images."""
        try:
            import shutil

            # Find ComfyUI output directory
            output_dir = self._find_comfyui_output_dir()
            if not output_dir:
                return web.json_response(
                    {"success": False, "error": "ComfyUI output directory not found"},
                    status=404,
                )

            output_path = Path(output_dir)
            thumbnails_dir = output_path / "thumbnails"

            if not thumbnails_dir.exists():
                return web.json_response(
                    {
                        "success": True,
                        "message": "No thumbnails directory found - nothing to clear",
                        "cleared_files": 0,
                    }
                )

            # Verify this is actually our thumbnails directory
            try:
                thumbnails_dir_resolved = thumbnails_dir.resolve()
                output_path_resolved = output_path.resolve()

                if (
                    not str(thumbnails_dir_resolved).startswith(
                        str(output_path_resolved)
                    )
                    or thumbnails_dir.name != "thumbnails"
                ):
                    self.logger.error(
                        f"Safety check failed: thumbnails directory path invalid: {thumbnails_dir}"
                    )
                    return web.json_response(
                        {
                            "success": False,
                            "error": "Safety check failed: invalid thumbnails directory path",
                        },
                        status=400,
                    )

            except Exception as e:
                self.logger.error(f"Path validation failed: {e}")
                return web.json_response(
                    {"success": False, "error": "Path validation failed"}, status=500
                )

            # Count files before deletion
            cleared_count = 0
            cleared_size = 0

            for root, dirs, files in os.walk(thumbnails_dir):
                for file in files:
                    file_path = Path(root) / file

                    if "_thumb" in file.lower() and any(
                        file.lower().endswith(ext)
                        for ext in [".png", ".jpg", ".jpeg", ".webp", ".gif"]
                    ):
                        try:
                            file_size = file_path.stat().st_size
                            file_path.unlink()
                            cleared_count += 1
                            cleared_size += file_size
                            self.logger.debug(f"Cleared thumbnail: {file_path}")
                        except Exception as e:
                            self.logger.warning(
                                f"Failed to delete thumbnail {file_path}: {e}"
                            )

            # Remove empty directories within thumbnails folder
            try:
                for root, dirs, files in os.walk(thumbnails_dir, topdown=False):
                    if root != str(thumbnails_dir):
                        try:
                            Path(root).rmdir()
                        except OSError:
                            pass
            except Exception as e:
                self.logger.debug(f"Directory cleanup info: {e}")

            def format_size(bytes_size):
                for unit in ["B", "KB", "MB", "GB"]:
                    if bytes_size < 1024.0:
                        return f"{bytes_size:.1f} {unit}"
                    bytes_size /= 1024.0
                return f"{bytes_size:.1f} TB"

            self.logger.info(
                f"Thumbnail cleanup: cleared {cleared_count} files ({format_size(cleared_size)})"
            )

            return web.json_response(
                {
                    "success": True,
                    "cleared_files": cleared_count,
                    "cleared_size": cleared_size,
                    "cleared_size_formatted": format_size(cleared_size),
                    "message": f"Cleared {cleared_count} thumbnail files ({format_size(cleared_size)})",
                }
            )

        except Exception as e:
            self.logger.error(f"Clear thumbnails error: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def link_image_to_prompt(self, request):
        """Link a generated image to a prompt."""
        try:
            data = await request.json()
            prompt_id = data.get("prompt_id")
            image_path = data.get("image_path")
            metadata = data.get("metadata", {})

            if not prompt_id or not image_path:
                return web.json_response(
                    {
                        "success": False,
                        "error": "prompt_id and image_path are required",
                    },
                    status=400,
                )

            if not os.path.exists(image_path):
                return web.json_response(
                    {"success": False, "error": "Image file not found"}, status=404
                )

            image_id = await self._run_in_executor(
                self.db.link_image_to_prompt, prompt_id, image_path, metadata
            )

            return web.json_response(
                {
                    "success": True,
                    "image_id": image_id,
                    "message": "Image linked successfully",
                }
            )

        except Exception as e:
            self.logger.error(f"Link image error: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def get_image_prompt(self, request):
        """Get prompt information for a specific image path."""
        try:
            # Get the image path from URL
            raw_image_path = request.match_info.get("image_path", "")
            image_path = urllib.parse.unquote(raw_image_path)

            if not image_path:
                return web.json_response(
                    {"success": False, "error": "Image path is required"}, status=400
                )

            # Convert relative path to absolute if needed
            if not os.path.isabs(image_path):
                output_dir = self._find_comfyui_output_dir()
                if output_dir:
                    image_path = str(Path(output_dir) / image_path)

            # Look up the image in generated_images table
            try:
                prompt_data = await self._run_in_executor(
                    self.db.get_image_prompt_info, image_path
                )
                if prompt_data:
                    prompt_data["image_path"] = image_path
                if prompt_data:
                    return web.json_response({"success": True, "prompt": prompt_data})
                else:
                    return web.json_response(
                        {
                            "success": False,
                            "error": "No prompt found for this image",
                            "image_path": image_path,
                        }
                    )

            except Exception as db_error:
                self.logger.error(f"Database error in get_image_prompt: {db_error}")
                return web.json_response(
                    {"success": False, "error": "Database error occurred"}, status=500
                )

        except Exception as e:
            self.logger.error(f"Get image prompt error: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def delete_image(self, request):
        """Delete an image record."""
        try:
            image_id = int(request.match_info["image_id"])
            success = await self._run_in_executor(self.db.delete_image, image_id)

            if success:
                return web.json_response(
                    {"success": True, "message": "Image deleted successfully"}
                )
            else:
                return web.json_response(
                    {"success": False, "error": "Image not found"}, status=404
                )

        except ValueError:
            return web.json_response(
                {"success": False, "error": "Invalid image ID"}, status=400
            )
        except Exception as e:
            self.logger.error(f"Delete image error: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)
