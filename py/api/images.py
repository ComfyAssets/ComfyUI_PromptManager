"""Image and gallery API routes for PromptManager."""

import asyncio
import hashlib
import json
import os
import re
import time as _time
import urllib.parse
from pathlib import Path

from aiohttp import web
from PIL import Image, UnidentifiedImageError

from .prompts import bad_request, parse_page_params, publish_image_paths
from .prompts import safe_error_message as _safe_error

try:
    from ...utils.parallel import map_parallel
except ImportError:
    from utils.parallel import map_parallel

# Only these file types are ever served by the image routes, regardless of
# what sits inside an allowed directory (defence in depth for the gallery).
IMAGE_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".bmp",
        ".tiff",
        ".tif",
        ".mp4",
        ".webm",
        ".mov",
    }
)


def _public_path(path):
    """Response-safe form of *path* (relative to the ComfyUI tree).

    Imported lazily: the package defines it after importing this module.
    """
    from . import _public_path as public_path

    return public_path(path)


def _canonical(path):
    """Resolve symlinks and normalise case so paths compare on every OS."""
    return Path(os.path.normcase(os.path.realpath(str(path))))


def _is_within(path, root):
    """True when *path* (after realpath) lives under *root* (after realpath)."""
    return _canonical(path).is_relative_to(_canonical(root))


def _has_traversal(relative):
    """True for absolute paths, drive-qualified paths or any ``..`` segment."""
    if not relative or os.path.isabs(relative) or os.path.splitdrive(relative)[0]:
        return True
    if relative[0] in ("/", "\\"):
        return True
    return ".." in re.split(r"[\\/]+", relative)


def _forbidden(message="Access denied"):
    return web.json_response({"success": False, "error": message}, status=403)


def _media_access_error(path, allowed_dirs):
    """Return a 403 response when *path* must not be served, else None.

    Fails closed: an empty *allowed_dirs* denies everything.
    """
    if Path(path).suffix.lower() not in IMAGE_EXTENSIONS:
        return _forbidden("Only media files can be served")
    if not allowed_dirs:
        return _forbidden("No allowed image directories configured")
    if not any(_is_within(path, root) for root in allowed_dirs):
        return _forbidden()
    return None


# Media types the gallery scans and thumbnails (a superset is never served).
GALLERY_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".webm", ".avi", ".mov", ".mkv", ".m4v", ".wmv"})
MEDIA_EXTENSIONS = GALLERY_IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

# Caps on request-triggered filesystem work.
MAX_SCAN_DEPTH = 12
MAX_THUMBNAIL_FILES = 10_000


def _worker_threads():
    """Configured thread count for thumbnail work.

    Imported lazily because py.config imports this package.
    """
    from ..config import PromptManagerConfig

    return PromptManagerConfig.WORKER_THREADS


def _thumbnail_batch_size(workers):
    """Files per executor job: enough to keep every worker thread busy."""
    return max(4, workers * 2)


def _write_image_thumbnail(src, dst, thumbnail_size):
    """Resize *src* into *dst* (blocking PIL work)."""
    with Image.open(src) as img:
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGB")
        img.thumbnail(thumbnail_size, Image.Resampling.LANCZOS)
        save_kwargs = {"quality": 85, "optimize": True}
        if dst.suffix.lower() == ".png":
            save_kwargs = {"optimize": True}
        img.save(dst, **save_kwargs)


def _new_thumbnail_stats():
    return {"generated": 0, "skipped": 0, "errors": []}


def _record_thumbnail_result(stats, result):
    action = result["action"]
    if action == "generated":
        stats["generated"] += 1
    elif action == "skipped":
        stats["skipped"] += 1
    else:
        stats["errors"].append(f"{result['file']}: {result.get('error', 'failed')}")


def _thumbnail_progress_payload(index, total, stats, result, start):
    elapsed = _time.monotonic() - start
    rate = index / elapsed if elapsed > 0 else 0
    eta = (total - index) / rate if rate > 0 else 0
    return {
        "processed": index,
        "total_images": total,
        "generated": stats["generated"],
        "skipped": stats["skipped"],
        "error_count": len(stats["errors"]),
        "percentage": round(index / total * 100, 1) if total else 100.0,
        "rate": round(rate, 1),
        "eta": round(eta),
        "elapsed": round(elapsed, 1),
        "current_file": f"{result['dir']}/{result['file']}",
        "file_type": result["type"],
        "action": result["action"],
    }


def _thumbnail_complete_payload(total, stats, start):
    elapsed = _time.monotonic() - start
    errors = stats["errors"]
    message = (
        f"Generated {stats['generated']} new thumbnails, "
        f"skipped {stats['skipped']} existing"
    )
    if errors:
        message += f" ({len(errors)} errors occurred)"
    return {
        "count": stats["generated"],
        "skipped": stats["skipped"],
        "total_images": total,
        "errors": errors[:10],
        "error_count": len(errors),
        "elapsed_time": round(elapsed, 2),
        "processing_rate": round(total / elapsed if elapsed > 0 else 0, 2),
        "message": message,
    }


# Caps on request-driven work (list sizes, scans, request bodies).
MAX_GALLERY_FILES = 50_000
MAX_BULK_LIMIT = 5_000
MAX_JSON_BODY_BYTES = 1024 * 1024
_READ_CHUNK = 64 * 1024


class _BodyTooLarge(Exception):
    """Raised when a request body exceeds MAX_JSON_BODY_BYTES."""


def _json_error(message, status):
    return web.json_response({"success": False, "error": message}, status=status)


async def _read_json_body(request):
    """Read a JSON object body in bounded chunks.

    Raises _BodyTooLarge past MAX_JSON_BODY_BYTES (declared or streamed) and
    ValueError when the body is not a JSON object.
    """
    limit = MAX_JSON_BODY_BYTES
    if request.content_length is not None and request.content_length > limit:
        raise _BodyTooLarge()
    chunks, size = [], 0
    while True:
        chunk = await request.content.read(_READ_CHUNK)
        if not chunk:
            break
        size += len(chunk)
        if size > limit:
            raise _BodyTooLarge()
        chunks.append(chunk)
    data = json.loads(b"".join(chunks).decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("JSON body must be an object")
    return data


async def _json_body_or_error(request):
    """Return ``(data, None)`` or ``(None, error_response)``."""
    try:
        return await _read_json_body(request), None
    except _BodyTooLarge:
        return None, _json_error("Request body too large", 413)
    except (ValueError, UnicodeDecodeError):
        return None, _json_error("Invalid JSON body", 400)


def _iter_media_files(output_path, max_files):
    """Yield media files under *output_path* with bounded effort.

    Symlinks are never followed, ``thumbnails/`` is skipped, descent stops
    at MAX_SCAN_DEPTH and at most *max_files* paths are produced.
    """
    output_path = Path(output_path)
    root_depth = len(output_path.parts)
    produced = 0
    for root, dirs, files in os.walk(output_path):
        root_path = Path(root)
        rel_parts = root_path.parts[root_depth:]
        if "thumbnails" in rel_parts:
            dirs[:] = []
            continue
        if len(rel_parts) >= MAX_SCAN_DEPTH:
            dirs[:] = []
        for name in sorted(files):
            if Path(name).suffix.lower() not in MEDIA_EXTENSIONS:
                continue
            if (root_path / name).is_symlink():
                continue  # never read through a link out of the output tree
            if produced >= max_files:
                return
            produced += 1
            yield root_path / name


def _output_image_entry(media_path, output_path, root_index, public_root=None):
    """Build one gallery entry for *media_path* (blocking stat).

    ``path`` is relative to its root (what delete/autotag resolve against) and
    ``root_dir`` is the root's public form; neither reveals the server layout.
    """
    if public_root is None:
        public_root = _public_path(output_path)
    stat = media_path.stat()
    rel_path = media_path.relative_to(output_path)
    extension = media_path.suffix.lower()
    is_video = extension in VIDEO_EXTENSIONS

    thumbnail_url = None
    thumb_rel = (
        f"thumbnails/{rel_path.with_suffix('').as_posix()}_thumb"
        f"{'.jpg' if is_video else extension}"
    )
    if (output_path / thumb_rel).exists():
        thumbnail_url = (
            f"/prompt_manager/images/serve/{urllib.parse.quote(thumb_rel, safe='/')}"
        )

    return {
        "id": hashlib.sha1(str(media_path).encode("utf-8")).hexdigest()[:16],
        "filename": media_path.name,
        "path": rel_path.as_posix(),
        "relative_path": str(rel_path),
        "root_dir": public_root,
        "root_index": root_index,
        "url": f"/prompt_manager/images/serve/{rel_path.as_posix()}",
        "thumbnail_url": thumbnail_url,
        "size": stat.st_size,
        "modified_time": stat.st_mtime,
        "extension": extension,
        "media_type": "video" if is_video else "image",
        "is_video": is_video,
    }


def _in_subfolder(rel_dir, subfolder):
    return rel_dir == subfolder or rel_dir.startswith(subfolder + os.sep)


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

        # POST only: the stream writes files, so it must not be reachable
        # from a prefetch, link preview or <img src>.
        @routes.post("/prompt_manager/images/generate-thumbnails/progress")
        async def generate_thumbnails_progress_route(request):
            return await self.generate_thumbnails_with_progress(request)

        @routes.post("/prompt_manager/images/clear-thumbnails")
        async def clear_thumbnails_route(request):
            return await self.clear_thumbnails(request)

        @routes.get("/prompt_manager/gallery/subfolders")
        async def get_gallery_subfolders_route(request):
            return await self.get_gallery_subfolders(request)

    def _present_images(self, images):
        """Image dicts ready for a response: urls added, server paths hidden."""
        self._enrich_images(images)
        return publish_image_paths(images, _public_path)

    async def get_prompt_images(self, request):
        """Get all images for a specific prompt."""
        try:
            prompt_id = request.match_info["prompt_id"]
            images = await self._run_in_executor(self.db.get_prompt_images, prompt_id)

            # Clean up any NaN values that cause JSON parsing errors (recursive)
            cleaned_images = [self._clean_nan_recursive(image) for image in images]

            # Add url and thumbnail_url so the frontend can serve them
            cleaned_images = self._present_images(cleaned_images)

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
            return web.json_response(
                {"success": False, "error": _safe_error(e)}, status=500
            )

    async def get_recent_images(self, request):
        """Get recently generated images (bounded window, newest first)."""
        try:
            try:
                limit, offset = parse_page_params(request.query)
            except ValueError:
                return bad_request("limit and offset must be integers")

            images = self._present_images(
                await self._run_in_executor(self.db.get_recent_images, limit, offset)
            )

            return web.json_response(
                {
                    "success": True,
                    "images": images,
                    "pagination": {
                        "limit": limit,
                        "offset": offset,
                        "count": len(images),
                    },
                }
            )
        except Exception as e:
            self.logger.error(f"Get recent images error: {e}")
            return web.json_response(
                {"success": False, "error": _safe_error(e)}, status=500
            )

    async def get_all_images(self, request):
        """Get generated images with linked prompts (bulk-capped pages)."""
        try:
            try:
                limit, offset = parse_page_params(
                    request.query,
                    default_limit=MAX_BULK_LIMIT,
                    max_limit=MAX_BULK_LIMIT,
                )
            except ValueError:
                return bad_request("limit and offset must be integers")

            images = self._present_images(
                await self._run_in_executor(
                    self.db.get_all_images, limit=limit, offset=offset
                )
            )

            return web.json_response(
                {
                    "success": True,
                    "images": images,
                    "count": len(images),
                    "pagination": {
                        "limit": limit,
                        "offset": offset,
                        "count": len(images),
                        "has_more": len(images) == limit,
                    },
                }
            )
        except Exception as e:
            self.logger.error(f"Get all images error: {e}")
            return web.json_response(
                {"success": False, "error": _safe_error(e)}, status=500
            )

    async def search_images(self, request):
        """Search images by prompt text."""
        try:
            query = request.query.get("q", "")
            if not query:
                return web.json_response(
                    {"success": False, "error": "Search query required"}, status=400
                )

            try:
                limit, offset = parse_page_params(request.query)
            except ValueError:
                return bad_request("limit and offset must be integers")

            images = self._present_images(
                await self._run_in_executor(
                    self.db.search_images_by_prompt, query, limit, offset
                )
            )

            return web.json_response(
                {
                    "success": True,
                    "images": images,
                    "query": query,
                    "pagination": {
                        "limit": limit,
                        "offset": offset,
                        "count": len(images),
                    },
                }
            )
        except Exception as e:
            self.logger.error(f"Search images error: {e}")
            return web.json_response(
                {"success": False, "error": _safe_error(e)}, status=500
            )

    def _scan_gallery_files_sync(self, output_path):
        """Scan output directory for media files (blocking I/O, run in executor).

        Returns list of (path, mtime) tuples sorted by mtime descending; the
        walk is bounded by MAX_SCAN_DEPTH and MAX_GALLERY_FILES.
        """
        found = []
        for media_path in _iter_media_files(output_path, MAX_GALLERY_FILES):
            try:
                found.append((media_path, media_path.stat().st_mtime))
            except OSError:
                continue
        if len(found) >= MAX_GALLERY_FILES:
            self.logger.warning(f"Gallery scan capped at {MAX_GALLERY_FILES} files")
        found.sort(key=lambda x: x[1], reverse=True)
        return found

    async def _get_gallery_files(self, output_path):
        """Get gallery files with per-directory TTL cache."""
        now = _time.monotonic()
        key = str(output_path)
        cached = self._gallery_cache.get(key)
        if cached and (now - cached[1]) < self._gallery_cache_ttl:
            return cached[0]

        files = await self._run_in_executor(self._scan_gallery_files_sync, output_path)
        self._gallery_cache[key] = (files, now)
        return files

    async def get_output_images(self, request):
        """Get all images from ComfyUI output folder(s)."""
        try:
            output_dirs = self._get_all_output_dirs()
            if not output_dirs:
                return web.json_response(
                    {
                        "success": False,
                        "error": "No output directories found",
                        "images": [],
                    },
                )
            try:
                limit, offset = parse_page_params(request.query, default_limit=100)
            except ValueError:
                return bad_request("limit and offset must be integers")
            subfolder = request.query.get("subfolder", "").strip()

            all_images = await self._collect_output_images(output_dirs, subfolder)
            total = len(all_images)
            page = all_images[offset : offset + limit]
            images = await self._run_in_executor(self._format_output_page, page)

            return web.json_response(
                {
                    "success": True,
                    "images": images,
                    "total": total,
                    "offset": offset,
                    "limit": limit,
                    "has_more": offset + limit < total,
                }
            )
        except Exception as e:
            self.logger.error(f"Output images error: {e}")
            return web.json_response(
                {"success": False, "error": _safe_error(e)}, status=500
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

            image_path = Path(image["image_path"])
            allowed_dirs = list(self._get_all_output_dirs()) + self._lora_image_dirs()

            denied = _media_access_error(image_path, allowed_dirs)
            if denied is not None:
                return denied

            if not image_path.is_file():
                return web.json_response(
                    {"success": False, "error": "Image file not found"}, status=404
                )

            return self._file_response(image_path)

        except ValueError:
            return web.json_response(
                {"success": False, "error": "Invalid image ID"}, status=400
            )
        except Exception as e:
            self.logger.error(f"Serve image error: {e}")
            return web.json_response(
                {"success": False, "error": _safe_error(e)}, status=500
            )

    def _lora_image_dirs(self):
        """Extra directories served when the LoRA integration is enabled."""
        try:
            from ..config import IntegrationConfig

            if not IntegrationConfig.LORA_MANAGER_ENABLED:
                return []
            from ..lora_utils import find_lora_directories, get_lora_image_cache_dir

            lora_root = IntegrationConfig.LORA_MANAGER_PATH or ""
            if lora_root.strip():
                # A configured path comes from config.json, which can be hand
                # edited: only a LoraManager install under custom_nodes may
                # widen the serving allow-list. An empty path keeps the
                # auto-detection, which itself only scans custom_nodes.
                from ..lora_utils import resolve_lora_manager_path

                lora_root = resolve_lora_manager_path(lora_root)
                if not lora_root:
                    return []

            dirs = [Path(d) for d in find_lora_directories(lora_root)]
            dirs.append(Path(get_lora_image_cache_dir()))
            return dirs
        except Exception:
            # LoRA integration is optional; never widen access on failure.
            return []

    @staticmethod
    def _file_response(path):
        response = web.FileResponse(path)
        response.headers["Cache-Control"] = "public, max-age=3600"
        return response

    @staticmethod
    def _select_root(output_dirs, root_param):
        """Narrow *output_dirs* to the ``?root=N`` entry when it is valid."""
        if root_param is None:
            return output_dirs
        try:
            idx = int(root_param)
        except ValueError:
            return output_dirs
        if 0 <= idx < len(output_dirs):
            return [output_dirs[idx]]
        return output_dirs

    async def serve_output_image(self, request):
        """Serve a media file from the ComfyUI output folder(s)."""
        try:
            filepath = request.match_info["filepath"]
            if _has_traversal(filepath):
                return _forbidden()
            if Path(filepath).suffix.lower() not in IMAGE_EXTENSIONS:
                return _forbidden("Only media files can be served")

            output_dirs = self._select_root(
                list(self._get_all_output_dirs()), request.query.get("root")
            )
            if not output_dirs:
                return _forbidden("No allowed image directories configured")

            for output_path in output_dirs:
                image_path = output_path / filepath
                if not image_path.exists():
                    continue
                # A symlink that points outside the root is a hard deny, not
                # a fall-through to the next root.
                if not _is_within(image_path, output_path):
                    return _forbidden()
                if image_path.is_file():
                    return self._file_response(image_path)

            return web.json_response(
                {"success": False, "error": "Image file not found"}, status=404
            )

        except Exception as e:
            self.logger.error(f"Serve output image error: {e}")
            return web.json_response(
                {"success": False, "error": _safe_error(e)}, status=500
            )

    async def get_gallery_subfolders(self, request):
        """Get distinct subfolders from gallery output directories."""
        try:
            output_dirs = self._get_all_output_dirs()
            subfolders = set()
            for output_path in output_dirs:
                files = await self._get_gallery_files(output_path)
                for f, _mtime in files:
                    rel_dir = str(f.relative_to(output_path).parent)
                    if rel_dir and rel_dir != ".":
                        subfolders.add(rel_dir)

            include_ancestors = (
                request.query.get("include_ancestors", "").lower() == "true"
            )
            if include_ancestors:
                ancestors = set()
                for folder in subfolders:
                    parts = folder.replace("\\", "/").split("/")
                    for i in range(1, len(parts)):
                        ancestors.add("/".join(parts[:i]))
                subfolders.update(ancestors)

            return web.json_response(
                {"success": True, "subfolders": sorted(subfolders)}
            )
        except Exception as e:
            self.logger.error(f"Subfolders error: {e}")
            return web.json_response(
                {"success": False, "error": _safe_error(e)}, status=500
            )

    # Fixed quality presets: clients choose a name, never a raw pixel size.
    THUMBNAIL_SIZES = {"low": (150, 150), "medium": (300, 300), "high": (600, 600)}

    @classmethod
    def _thumbnail_size(cls, quality):
        """Map a quality preset name to a pixel size; anything else is medium."""
        if not isinstance(quality, str):
            return cls.THUMBNAIL_SIZES["medium"]
        return cls.THUMBNAIL_SIZES.get(quality, cls.THUMBNAIL_SIZES["medium"])

    async def generate_thumbnails(self, request):
        """Generate every thumbnail in one executor job (POST, blocking)."""
        try:
            data, error = await _json_body_or_error(request)
            if error is not None:
                return error
            thumbnail_size = self._thumbnail_size(data.get("quality", "medium"))

            output_dir = self._find_comfyui_output_dir()
            if not output_dir:
                return web.json_response(
                    {"success": False, "error": "ComfyUI output directory not found"},
                    status=404,
                )

            output_path = Path(output_dir)
            result = await self._run_in_executor(
                self._generate_thumbnails_sync,
                output_path,
                output_path / "thumbnails",
                thumbnail_size,
            )
            self.invalidate_gallery_cache()
            return web.json_response(result)

        except Exception as e:
            self.logger.error(f"Generate thumbnails error: {e}")
            return web.json_response(
                {"success": False, "error": _safe_error(e)}, status=500
            )

    def _generate_thumbnails_sync(self, output_path, thumbnails_dir, thumbnail_size):
        """Blocking thumbnail generation loop (run in executor)."""
        start = _time.monotonic()
        targets = self._thumbnail_targets(output_path, thumbnails_dir)
        stats = _new_thumbnail_stats()
        for result in self._generate_batch(targets, thumbnail_size):
            _record_thumbnail_result(stats, result)
        payload = _thumbnail_complete_payload(len(targets), stats, start)
        payload["success"] = True
        payload["thumbnails_path"] = _public_path(thumbnails_dir)
        self.logger.info(payload["message"])
        return payload

    def _thumbnail_targets(self, output_path, thumbnails_dir):
        """Scan *output_path* for media (blocking); returns (src, dst, is_video).

        Bounded by _iter_media_files (no symlinks, MAX_SCAN_DEPTH) and
        MAX_THUMBNAIL_FILES per request.
        """
        output_path = Path(output_path)
        targets = []
        for src in _iter_media_files(output_path, MAX_THUMBNAIL_FILES):
            suffix = src.suffix.lower()
            is_video = suffix in VIDEO_EXTENSIONS
            rel_no_ext = src.relative_to(output_path).with_suffix("")
            thumb_suffix = ".jpg" if is_video else suffix
            dst = thumbnails_dir / f"{rel_no_ext.as_posix()}_thumb{thumb_suffix}"
            if not _is_within(dst, thumbnails_dir):
                self.logger.warning(f"Skipping thumbnail outside safe dir: {src.name}")
                continue
            targets.append((src, dst, is_video))
        if len(targets) >= MAX_THUMBNAIL_FILES:
            self.logger.warning(f"Thumbnail scan capped at {MAX_THUMBNAIL_FILES}")
        return targets

    def _generate_batch(self, targets, thumbnail_size):
        """Create thumbnails for ``targets`` on the configured worker threads.

        Blocking; returns one result dict per target, in order.
        """

        def one(target):
            src, dst, is_video = target
            return self._generate_one(src, dst, thumbnail_size, is_video)

        return map_parallel(one, targets, _worker_threads())

    def _generate_one(self, src, dst, thumbnail_size, is_video):
        """Create one thumbnail (blocking). Never raises; returns a result dict."""
        result = {
            "file": src.name,
            "dir": src.parent.name,
            "type": "video" if is_video else "image",
        }
        try:
            if not src.is_file():
                return {**result, "action": "error", "error": "File no longer exists"}
            if dst.exists() and dst.stat().st_mtime > src.stat().st_mtime:
                return {**result, "action": "skipped"}
            dst.parent.mkdir(parents=True, exist_ok=True)
            if is_video:
                if not self._generate_video_thumbnail(src, dst, thumbnail_size):
                    return {**result, "action": "error", "error": "Video decode failed"}
            else:
                _write_image_thumbnail(src, dst, thumbnail_size)
            return {**result, "action": "generated"}
        except UnidentifiedImageError:
            return {**result, "action": "error", "error": "Not a valid image file"}
        except Exception as e:
            self.logger.warning(f"Thumbnail failed for {src.name}", exc_info=True)
            return {**result, "action": "error", "error": _safe_error(e)}

    async def generate_thumbnails_with_progress(self, request):
        """Generate thumbnails with Server-Sent Events progress updates (POST).

        ``quality`` is read from the query string so a POST-capable SSE
        client can keep using the same URL shape. Every blocking step (scan,
        PIL/ffmpeg) runs in the executor; the event loop only writes SSE
        frames between files.
        """
        thumbnail_size = self._thumbnail_size(request.query.get("quality", "medium"))
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await response.prepare(request)
        try:
            output_dir = self._find_comfyui_output_dir()
            if not output_dir:
                await self._emit_sse(
                    response, "error", {"error": "ComfyUI output directory not found"}
                )
                return response

            output_path = Path(output_dir)
            await self._emit_sse(
                response,
                "status",
                {"phase": "scanning", "message": "Scanning for images and videos..."},
            )
            targets = await self._run_in_executor(
                self._thumbnail_targets, output_path, output_path / "thumbnails"
            )
            await self._stream_thumbnails(response, targets, thumbnail_size)
            self.invalidate_gallery_cache()
        except Exception:
            self.logger.exception("Thumbnail generation failed")
            await self._emit_sse(
                response,
                "error",
                {
                    "error": "An internal error occurred",
                    "message": "Thumbnail generation failed. Check server logs.",
                },
            )
        return response

    async def _stream_thumbnails(self, response, targets, thumbnail_size):
        """Process *targets* in worker-thread batches, emitting SSE per file."""
        total = len(targets)
        video_count = sum(1 for _, _, is_video in targets if is_video)
        connected = await self._emit_sse(
            response,
            "start",
            {
                "total_images": total,
                "phase": "processing",
                "image_count": total - video_count,
                "video_count": video_count,
                "message": (
                    f"Found {total - video_count} images and "
                    f"{video_count} videos to process"
                ),
            },
        )
        if not connected:
            return
        stats = _new_thumbnail_stats()
        start = _time.monotonic()
        batch_size = _thumbnail_batch_size(_worker_threads())
        index = 0
        for batch_start in range(0, total, batch_size):
            batch = targets[batch_start : batch_start + batch_size]
            results = await self._run_in_executor(
                self._generate_batch, batch, thumbnail_size
            )
            for result in results:
                index += 1
                _record_thumbnail_result(stats, result)
                if result["action"] == "error":
                    await self._emit_sse(
                        response,
                        "file_error",
                        {"file": result["file"], "error": result["error"]},
                    )
                connected = await self._emit_sse(
                    response,
                    "progress",
                    _thumbnail_progress_payload(index, total, stats, result, start),
                )
                if not connected:
                    self.logger.info("Thumbnail client disconnected; stopping early")
                    return
            await asyncio.sleep(0)  # let other requests run between batches
        payload = _thumbnail_complete_payload(total, stats, start)
        await self._emit_sse(response, "complete", payload)
        self.logger.info(payload["message"])

    async def _emit_sse(self, response, event, data) -> bool:
        """Write one SSE frame. Returns False once the client has gone away."""
        try:
            frame = f"event: {event}\ndata: {json.dumps(data)}\n\n"
            await response.write(frame.encode("utf-8"))
            return True
        except Exception as e:
            self.logger.warning(f"Failed to send SSE message: {e}")
            return False

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

            # Verify this is actually our thumbnails directory: a real child
            # of the output root (symlinks resolved), not merely a path that
            # shares its prefix such as "<root>2/thumbnails".
            try:
                if (
                    not _is_within(thumbnails_dir, output_path)
                    or thumbnails_dir.name != "thumbnails"
                ):
                    self.logger.error(
                        "Safety check failed: thumbnails directory path invalid: "
                        f"{thumbnails_dir}"
                    )
                    return web.json_response(
                        {
                            "success": False,
                            "error": "Safety check failed: invalid thumbnails "
                            "directory path",
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
                f"Thumbnail cleanup: cleared {cleared_count} files "
                f"({format_size(cleared_size)})"
            )

            return web.json_response(
                {
                    "success": True,
                    "cleared_files": cleared_count,
                    "cleared_size": cleared_size,
                    "cleared_size_formatted": format_size(cleared_size),
                    "message": f"Cleared {cleared_count} thumbnail files "
                    "({format_size(cleared_size)})",
                }
            )

        except Exception as e:
            self.logger.error(f"Clear thumbnails error: {e}")
            return web.json_response(
                {"success": False, "error": _safe_error(e)}, status=500
            )

    @staticmethod
    def _resolve_client_media_path(raw_path, allowed_dirs):
        """Absolute path for a client-supplied media path, or None when it
        does not name a file inside an allowed root.

        Absolute paths are used as given (the caller still runs the access
        check). Relative paths are the forms this API hands out: relative to
        a gallery root (``sub/a.png``) or to a ComfyUI anchor
        (``output/sub/a.png``), and must resolve inside an allowed root.
        """
        if os.path.isabs(raw_path):
            return raw_path
        from ..config import GalleryConfig

        for base in [*allowed_dirs, *GalleryConfig.path_anchors()]:
            candidate = Path(base) / raw_path
            if candidate.is_file() and any(
                _is_within(candidate, root) for root in allowed_dirs
            ):
                return str(candidate)
        return None

    async def link_image_to_prompt(self, request):
        """Link a generated image (inside an allowed directory) to a prompt."""
        try:
            data, error = await _json_body_or_error(request)
            if error is not None:
                return error
            prompt_id = data.get("prompt_id")
            image_path = data.get("image_path")
            metadata = data.get("metadata", {})

            if not prompt_id or not isinstance(image_path, str) or not image_path:
                return bad_request("prompt_id and image_path are required")
            if not os.path.isabs(image_path) and _has_traversal(image_path):
                return _forbidden()

            allowed_dirs = list(self._get_all_output_dirs())
            resolved = self._resolve_client_media_path(image_path, allowed_dirs)
            if resolved is None:
                if Path(image_path).suffix.lower() not in IMAGE_EXTENSIONS:
                    return _forbidden("Only media files can be served")
                return web.json_response(
                    {"success": False, "error": "Image file not found"}, status=404
                )
            image_path = resolved

            denied = _media_access_error(image_path, allowed_dirs)
            if denied is not None:
                return denied

            if not os.path.isfile(image_path):
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
            return web.json_response(
                {"success": False, "error": _safe_error(e)}, status=500
            )

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
                    prompt_data["image_path"] = _public_path(image_path)
                    return web.json_response({"success": True, "prompt": prompt_data})
                return web.json_response(
                    {
                        "success": False,
                        "error": "No prompt found for this image",
                        "image_path": _public_path(image_path),
                    }
                )

            except Exception as db_error:
                self.logger.error(f"Database error in get_image_prompt: {db_error}")
                return web.json_response(
                    {"success": False, "error": "Database error occurred"}, status=500
                )

        except Exception as e:
            self.logger.error(f"Get image prompt error: {e}")
            return web.json_response(
                {"success": False, "error": _safe_error(e)}, status=500
            )

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
            return web.json_response(
                {"success": False, "error": _safe_error(e)}, status=500
            )

    async def _collect_output_images(self, output_dirs, subfolder):
        """Merge cached per-root listings, newest first, optionally filtered."""
        merged = []
        for root_idx, output_path in enumerate(output_dirs):
            for img_path, mtime in await self._get_gallery_files(output_path):
                merged.append((img_path, mtime, output_path, root_idx))
        merged.sort(key=lambda x: x[1], reverse=True)
        if not subfolder:
            return merged
        return [
            entry
            for entry in merged
            if _in_subfolder(str(entry[0].relative_to(entry[2]).parent), subfolder)
        ]

    def _format_output_page(self, page):
        entries = []
        public_roots = {}
        for media_path, _mtime, output_path, root_index in page:
            if output_path not in public_roots:
                public_roots[output_path] = _public_path(output_path)
            try:
                entries.append(
                    _output_image_entry(
                        media_path, output_path, root_index, public_roots[output_path]
                    )
                )
            except Exception as e:
                self.logger.error(f"Error processing media {media_path.name}: {e}")
        return entries
