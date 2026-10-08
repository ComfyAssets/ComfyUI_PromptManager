"""Admin and maintenance API routes for PromptManager."""

import asyncio
import datetime
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

from aiohttp import web

try:
    from ...utils.validators import validate_result_timeout
except ImportError:
    from utils.validators import validate_result_timeout

try:
    from ...database.operations import PromptDatabase
except ImportError:
    from database.operations import PromptDatabase


try:
    from ...utils.hashing import generate_prompt_hash
except ImportError:
    from utils.hashing import generate_prompt_hash

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff")
VIDEO_EXTENSIONS = (".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v", ".wmv")
SCAN_BATCH_SIZE = 50  # Files per executor call during the output scan


def _collect_media_files(output_dirs, extensions):
    """All media files under ``output_dirs`` (blocking), skipping thumbnails.

    Matches extensions case-insensitively and de-duplicates by
    case-folded path so case-insensitive filesystems do not list a file twice.
    """
    found = []
    seen = set()
    for output_dir in output_dirs:
        for ext in extensions:
            for pattern in (f"*{ext}", f"*{ext.upper()}"):
                for media_path in Path(output_dir).rglob(pattern):
                    if "thumbnails" in media_path.parts:
                        continue
                    key = os.path.normcase(str(media_path))
                    if key not in seen:
                        seen.add(key)
                        found.append(media_path)
    return found


def _thumbnail_rel_path(rel_path, thumbnail_ext):
    """Relative path of the thumbnail the gallery would generate for ``rel_path``."""
    return f"thumbnails/{rel_path.with_suffix('').as_posix()}_thumb{thumbnail_ext}"


def _sse(payload):
    """Encode one server-sent event."""
    return f"data: {json.dumps(payload)}\n\n"


def _image_monitor_module():
    """The utils.image_monitor module under either package identity."""
    try:
        from ...utils import image_monitor
    except ImportError:
        from utils import image_monitor
    return image_monitor


def _current_image_monitor():
    """The running ImageMonitor singleton, or None when not started."""
    return getattr(_image_monitor_module(), "_monitor_instance", None)


class AdminRoutesMixin:
    """Mixin providing admin, diagnostics, and maintenance API endpoints."""

    def _register_admin_routes(self, routes):
        @routes.get("/prompt_manager/scan_duplicates")
        async def scan_duplicates_route(request):
            return await self.scan_duplicates_endpoint(request)

        @routes.post("/prompt_manager/delete_duplicate_images")
        async def delete_duplicate_images_route(request):
            return await self.delete_duplicate_images_endpoint(request)

        @routes.post("/prompt_manager/cleanup")
        async def cleanup_duplicates_route(request):
            return await self.cleanup_duplicates_endpoint(request)

        @routes.post("/prompt_manager/maintenance")
        async def maintenance_route(request):
            return await self.run_maintenance(request)

        @routes.get("/prompt_manager/stats")
        async def get_stats_route(request):
            return await self.get_statistics(request)

        @routes.get("/prompt_manager/settings")
        async def get_settings_route(request):
            return await self.get_settings(request)

        @routes.post("/prompt_manager/settings")
        async def save_settings_route(request):
            return await self.save_settings(request)

        @routes.get("/prompt_manager/backup")
        async def backup_database_route(request):
            return await self.backup_database(request)

        @routes.post("/prompt_manager/restore")
        async def restore_database_route(request):
            return await self.restore_database(request)

        @routes.get("/prompt_manager/diagnostics")
        async def run_diagnostics_route(request):
            return await self.run_diagnostics(request)

        @routes.post("/prompt_manager/diagnostics/test-link")
        async def test_image_link_route(request):
            return await self.test_image_link(request)

        @routes.post("/prompt_manager/scan")
        async def scan_images_route(request):
            return await self.scan_images(request)

    async def scan_duplicates_endpoint(self, request):
        """Scan for duplicate images without removing them."""
        try:
            duplicates = await self.find_duplicate_images()

            return web.json_response(
                {
                    "success": True,
                    "duplicates": duplicates,
                    "message": f"Found {len(duplicates)} groups of duplicate images",
                }
            )

        except Exception as e:
            self.logger.error(f"Scan duplicates error: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": "Failed to scan duplicate images"},
                status=500,
            )

    async def cleanup_duplicates_endpoint(self, request):
        """Cleanup duplicate prompts endpoint."""
        try:
            removed_count = await self._run_in_executor(self.db.cleanup_duplicates)

            return web.json_response(
                {
                    "success": True,
                    "message": "Cleanup completed",
                    "duplicates_removed": removed_count,
                }
            )

        except Exception as e:
            self.logger.error(f"Cleanup error: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": "Failed to cleanup duplicates"},
                status=500,
            )

    async def find_duplicate_images(self):
        """Find duplicate media in the output directory, off the event loop."""
        output_dir = await self._run_in_executor(self._find_comfyui_output_dir)
        return await self._run_in_executor(self._find_duplicate_images_sync, output_dir)

    def _find_duplicate_images_sync(self, output_dir):
        """Group media files under ``output_dir`` by content hash (blocking)."""
        if not output_dir or not os.path.isdir(output_dir):
            self.logger.warning("ComfyUI output directory not found")
            return []

        output_path = Path(output_dir)
        extensions = IMAGE_EXTENSIONS + VIDEO_EXTENSIONS
        media_files = _collect_media_files([output_path], extensions)
        self.logger.info(f"Found {len(media_files)} media files to analyze")

        file_hashes = {}
        for index, media_path in enumerate(media_files, start=1):
            try:
                info = self._describe_media_file(media_path, output_path)
            except Exception as e:
                self.logger.error(f"Error processing file {media_path}: {e}")
                continue
            file_hashes.setdefault(info["hash"], []).append(info)
            if index % 100 == 0:
                self.logger.info(
                    f"Processed {index}/{len(media_files)} files for duplicate detection"
                )

        duplicates = []
        for file_hash, images in file_hashes.items():
            if len(images) > 1:
                images.sort(key=lambda x: x["modified_time"])
                duplicates.append(
                    {"hash": file_hash, "images": images, "count": len(images)}
                )

        self.logger.info(f"Found {len(duplicates)} groups of duplicate images")
        return duplicates

    def _describe_media_file(self, media_path, output_path):
        """Hash one media file and describe it for the duplicates response."""
        from urllib.parse import quote

        stat = media_path.stat()
        rel_path = media_path.relative_to(output_path)
        extension = media_path.suffix.lower()
        is_video = extension in VIDEO_EXTENSIONS

        thumbnail_url = None
        thumb_rel = _thumbnail_rel_path(rel_path, ".jpg" if is_video else extension)
        if (output_path / thumb_rel).exists():
            thumbnail_url = f"/prompt_manager/images/serve/{quote(thumb_rel, safe='/')}"

        return {
            "id": str(hash(str(media_path))),
            "filename": media_path.name,
            "path": str(rel_path),
            "relative_path": str(rel_path),
            "url": f"/prompt_manager/images/serve/{rel_path.as_posix()}",
            "thumbnail_url": thumbnail_url,
            "size": stat.st_size,
            "modified_time": stat.st_mtime,
            "extension": extension,
            "media_type": "video" if is_video else "image",
            "is_video": is_video,
            "hash": self._calculate_file_hash(media_path),
        }

    def _calculate_file_hash(self, file_path):
        """Calculate SHA-256 hash of a file's content."""
        hash_sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_sha256.update(chunk)
        return hash_sha256.hexdigest()

    async def delete_duplicate_images_endpoint(self, request):
        """Delete duplicate image files from disk."""
        try:
            data = await request.json()
            image_paths = data.get("image_paths", [])

            if not image_paths or not isinstance(image_paths, list):
                return web.json_response(
                    {"success": False, "error": "No image paths provided"},
                    status=400,
                )

            output_dir = await self._run_in_executor(self._find_comfyui_output_dir)
            result = await self._run_in_executor(
                self._delete_duplicate_images_sync, image_paths, output_dir
            )

            response_data = {
                "success": True,
                "deleted_count": result["deleted_count"],
                "failed_count": result["failed_count"],
                "message": f"Deleted {result['deleted_count']} files successfully",
            }
            if result["failed_count"] > 0:
                response_data["failed_files"] = result["failed_files"]
                response_data["message"] += f", {result['failed_count']} failed"

            return web.json_response(response_data)

        except Exception as e:
            self.logger.error(f"Delete duplicate images error: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": "Failed to delete duplicate images"},
                status=500,
            )

    def _delete_duplicate_images_sync(self, image_paths, output_dir):
        """Delete the given files (absolute or output-relative) within ``output_dir``."""
        result = {"deleted_count": 0, "failed_count": 0, "failed_files": []}
        output_path = Path(output_dir) if output_dir else None

        for image_path in image_paths:
            if output_path is None:
                failure = "output directory not found"
            else:
                try:
                    failure = self._delete_one_duplicate(str(image_path), output_path)
                except Exception as e:
                    self.logger.error(f"Error deleting file {image_path}: {e}")
                    failure = self._public_error(e)

            if failure is None:
                result["deleted_count"] += 1
            else:
                result["failed_count"] += 1
                result["failed_files"].append(f"{image_path} ({failure})")

        return result

    def _delete_one_duplicate(self, image_path, output_path):
        """Delete one file and its thumbnail; return a failure reason or None."""
        file_path = Path(image_path)
        if not file_path.is_absolute():
            file_path = output_path / file_path

        try:
            rel_path = file_path.resolve().relative_to(output_path.resolve())
        except ValueError:
            self.logger.warning(
                f"Attempted to delete file outside output directory: {image_path}"
            )
            return "outside output directory"

        if not file_path.is_file():
            return "file not found"

        os.remove(file_path)
        self.logger.info(f"Deleted duplicate image: {image_path}")

        thumbnail_path = output_path / _thumbnail_rel_path(rel_path, file_path.suffix)
        try:
            if thumbnail_path.exists():
                os.remove(thumbnail_path)
                self.logger.debug(f"Deleted associated thumbnail: {thumbnail_path}")
        except OSError as e:
            self.logger.warning(f"Could not delete thumbnail for {image_path}: {e}")
        return None

    async def get_statistics(self, request):
        """Get database statistics."""
        try:
            stats = await self._run_in_executor(self.db.get_statistics)

            return web.json_response({"success": True, "stats": stats})

        except Exception as e:
            self.logger.error(f"Stats error: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": "Failed to get statistics"},
                status=500,
            )

    async def get_settings(self, request):
        """Get current settings."""
        try:
            from ..config import PromptManagerConfig, GalleryConfig

            root_paths = [
                self._public_path(d) for d in GalleryConfig.MONITORING_DIRECTORIES
            ]
            return web.json_response(
                {
                    "success": True,
                    "settings": {
                        "result_timeout": PromptManagerConfig.RESULT_TIMEOUT,
                        "webui_display_mode": PromptManagerConfig.WEBUI_DISPLAY_MODE,
                        "gallery_root_paths": root_paths,
                        "gallery_root_path": root_paths[0] if root_paths else "",
                        "monitored_directories": [
                            self._public_path(d) for d in self._monitored_directories()
                        ],
                    },
                }
            )
        except Exception as e:
            self.logger.error(f"Get settings error: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": "Failed to get settings"},
                status=500,
            )

    def _monitored_directories(self):
        """Directories the running image monitor watches, else the configured roots."""
        from ..config import GalleryConfig

        monitor = _current_image_monitor()
        if monitor is not None:
            return list(getattr(monitor, "monitored_directories", []))
        return list(GalleryConfig.MONITORING_DIRECTORIES)

    async def save_settings(self, request):
        """Save settings."""
        try:
            from ..config import PromptManagerConfig

            data = await request.json()

            if "result_timeout" in data:
                try:
                    PromptManagerConfig.RESULT_TIMEOUT = validate_result_timeout(
                        data["result_timeout"]
                    )
                except ValueError as ve:
                    return web.json_response(
                        {"success": False, "error": str(ve)}, status=400
                    )
            if "webui_display_mode" in data:
                PromptManagerConfig.WEBUI_DISPLAY_MODE = data["webui_display_mode"]

            new_roots, error = self._parse_gallery_roots(data)
            if error:
                return web.json_response({"success": False, "error": error}, status=400)

            restart_required = False
            if new_roots is not None:
                restart_required = self._apply_gallery_roots(new_roots)

            self._persist_settings()

            return web.json_response(
                {
                    "success": True,
                    "message": "Settings saved successfully",
                    "restart_required": restart_required,
                }
            )
        except Exception as e:
            self.logger.error(f"Save settings error: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": "Failed to save settings"},
                status=500,
            )

    def _parse_gallery_roots(self, data):
        """Extract and validate gallery roots from a settings payload.

        Accepts ``gallery_root_paths`` (list, preferred) or the legacy single
        ``gallery_root_path`` string. Blank entries are dropped.

        Returns:
            (roots, error): ``roots`` is the validated list, or ``None`` when
            the payload carries no gallery root key; ``error`` is a message
            when validation failed (and ``roots`` is then ``None``).
        """
        if "gallery_root_paths" in data:
            raw = data["gallery_root_paths"]
            if not isinstance(raw, list):
                return None, "gallery_root_paths must be a list"
        elif "gallery_root_path" in data:
            raw = [data["gallery_root_path"]]
        else:
            return None, None

        from ..config import GalleryConfig

        roots = []
        for entry in raw:
            if not isinstance(entry, str):
                return None, "Gallery root paths must be strings"
            entry = entry.strip()
            if not entry:
                continue
            ok, reason = GalleryConfig.validate_gallery_root(entry)
            if not ok:
                return (
                    None,
                    f"Invalid gallery root '{os.path.basename(entry)}': {reason}",
                )
            roots.append(GalleryConfig.resolve_gallery_root(entry))
        return roots, None

    def _apply_gallery_roots(self, roots):
        """Install validated gallery roots; returns True when they changed."""
        from ..config import GalleryConfig

        if roots == list(GalleryConfig.MONITORING_DIRECTORIES):
            return False
        GalleryConfig.MONITORING_DIRECTORIES = roots
        self._cached_output_dir = None
        self._gallery_cache = {}
        return True

    def _persist_settings(self):
        """Write the user-editable settings to the configured config.json."""
        from ..config import PromptManagerConfig, GalleryConfig

        config_file = PromptManagerConfig.get_config_path()
        config_data = {
            "web_ui": {
                "result_timeout": PromptManagerConfig.RESULT_TIMEOUT,
                "webui_display_mode": PromptManagerConfig.WEBUI_DISPLAY_MODE,
            },
            "gallery": {
                "monitoring": {"directories": GalleryConfig.MONITORING_DIRECTORIES}
            },
        }
        try:
            with open(config_file, "w") as f:
                json.dump(config_data, f, indent=2)
            self.logger.info(f"Settings saved to {config_file}")
        except OSError as save_err:
            self.logger.warning(f"Could not save config file: {save_err}")

    async def run_diagnostics(self, request):
        """Run comprehensive system diagnostics and health checks."""
        try:
            results = {}

            # Check database
            try:
                db_path = self.db.model.db_path
                if os.path.exists(db_path):
                    with sqlite3.connect(db_path) as conn:
                        conn.row_factory = sqlite3.Row
                        cursor = conn.execute("SELECT COUNT(*) as count FROM prompts")
                        prompt_count = cursor.fetchone()["count"]

                        cursor = conn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table' AND name='generated_images'"
                        )
                        has_images_table = cursor.fetchone() is not None

                        if has_images_table:
                            cursor = conn.execute(
                                "SELECT COUNT(*) as count FROM generated_images"
                            )
                            image_count = cursor.fetchone()["count"]
                        else:
                            image_count = 0

                        results["database"] = {
                            "status": "ok",
                            "prompt_count": prompt_count,
                            "has_images_table": has_images_table,
                            "image_count": image_count,
                        }
                else:
                    results["database"] = {
                        "status": "error",
                        "message": (
                            f"Database file not found: {os.path.basename(db_path)}"
                        ),
                    }
            except Exception as e:
                results["database"] = {
                    "status": "error",
                    "message": f"Database error: {self._public_error(e)}",
                }

            # Check dependencies
            dependencies = {}
            try:
                import watchdog

                dependencies["watchdog"] = True
            except ImportError:
                dependencies["watchdog"] = False

            try:
                from PIL import Image

                dependencies["PIL"] = True
            except ImportError:
                dependencies["PIL"] = False

            dependencies["sqlite3"] = True  # Always available in Python

            results["dependencies"] = {
                "status": "ok" if all(dependencies.values()) else "error",
                "dependencies": dependencies,
            }

            # Check output directories
            output_dirs = []

            # Check user-configured gallery directories from config.json
            try:
                from ..config import GalleryConfig

                for cfg_dir in GalleryConfig.MONITORING_DIRECTORIES:
                    abs_path = os.path.abspath(cfg_dir)
                    if os.path.exists(abs_path) and abs_path not in output_dirs:
                        output_dirs.append(abs_path)
            except Exception as e:
                self.logger.debug(f"Could not load gallery config: {e}")

            # Check ComfyUI's own output directory (respects --output-directory)
            try:
                import folder_paths

                comfyui_output = folder_paths.get_output_directory()
                if comfyui_output and os.path.exists(comfyui_output):
                    abs_path = os.path.abspath(comfyui_output)
                    if abs_path not in output_dirs:
                        output_dirs.append(abs_path)
            except ImportError:
                self.logger.debug(
                    "folder_paths not available, skipping ComfyUI output dir"
                )

            # Fallback: check common relative paths
            if not output_dirs:
                for dir_path in ["output", "../output", "../../output"]:
                    abs_path = os.path.abspath(dir_path)
                    if os.path.exists(abs_path) and abs_path not in output_dirs:
                        output_dirs.append(abs_path)

            results["comfyui_output"] = {
                "status": "ok" if output_dirs else "warning",
                "output_dirs": [self._public_path(d) for d in output_dirs],
            }

            # Check image monitor status
            try:
                monitor = _current_image_monitor()
                if monitor is not None:
                    monitor_status = dict(monitor.get_status())
                    monitor_status["monitored_directories"] = [
                        self._public_path(d)
                        for d in monitor_status.get("monitored_directories", [])
                    ]
                    results["image_monitor"] = {
                        "status": (
                            "ok" if monitor_status.get("observer_alive") else "error"
                        ),
                        **monitor_status,
                    }
                else:
                    results["image_monitor"] = {
                        "status": "error",
                        "message": "Image monitor not initialized",
                    }
            except Exception as e:
                results["image_monitor"] = {
                    "status": "error",
                    "message": f"Failed to get monitor status: {self._public_error(e)}",
                }

            return web.json_response({"success": True, "diagnostics": results})

        except Exception as e:
            self.logger.error(f"Diagnostics error: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": "Diagnostics failed"}, status=500
            )

    async def test_image_link(self, request):
        """Test creating an image link."""
        try:
            data = await request.json()
            prompt_id = data.get("prompt_id")
            test_image_path = data.get("image_path", "/test/fake/image.png")

            if not prompt_id:
                return web.json_response(
                    {"success": False, "error": "prompt_id is required"}, status=400
                )

            test_metadata = {
                "file_info": {
                    "size": 1024000,
                    "dimensions": [512, 512],
                    "format": "PNG",
                },
                "workflow": {"test": True},
                "prompt": {"test_prompt": "This is a test image"},
            }

            try:
                image_id = await self._run_in_executor(
                    self.db.link_image_to_prompt,
                    prompt_id=str(prompt_id),
                    image_path=test_image_path,
                    metadata=test_metadata,
                )

                return web.json_response(
                    {
                        "success": True,
                        "result": {
                            "status": "ok",
                            "image_id": image_id,
                            "message": f"Test image linked successfully with ID {image_id}",
                        },
                    }
                )
            except Exception as e:
                return web.json_response(
                    {
                        "success": False,
                        "result": {
                            "status": "error",
                            "message": (
                                f"Failed to create test link: {self._public_error(e)}"
                            ),
                        },
                    }
                )

        except Exception as e:
            self.logger.error(f"Test link error: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": "Test link failed"}, status=500
            )

    async def run_maintenance(self, request):
        """Perform comprehensive database maintenance and optimization."""
        try:
            data = (
                await request.json()
                if request.content_type == "application/json"
                else {}
            )
            operations = data.get(
                "operations",
                ["cleanup_duplicates", "vacuum", "cleanup_orphaned_images"],
            )

            results = {}

            def _run_maintenance():
                if "cleanup_duplicates" in operations:
                    try:
                        duplicates_removed = self.db.cleanup_duplicates()
                        results["cleanup_duplicates"] = {
                            "success": True,
                            "removed_count": duplicates_removed,
                            "message": f"Removed {duplicates_removed} duplicate prompts",
                        }
                    except Exception as e:
                        results["cleanup_duplicates"] = {
                            "success": False,
                            "error": self._public_error(e),
                            "message": "Failed to cleanup duplicates",
                        }

                if "vacuum" in operations:
                    try:
                        self.db.model.vacuum_database()
                        results["vacuum"] = {
                            "success": True,
                            "message": "Database vacuum completed successfully",
                        }
                    except Exception as e:
                        results["vacuum"] = {
                            "success": False,
                            "error": self._public_error(e),
                            "message": "Failed to vacuum database",
                        }

                if "cleanup_orphaned_images" in operations:
                    try:
                        orphaned_removed = self.db.cleanup_missing_images()
                        results["cleanup_orphaned_images"] = {
                            "success": True,
                            "removed_count": orphaned_removed,
                            "message": f"Removed {orphaned_removed} orphaned image records",
                        }
                    except Exception as e:
                        results["cleanup_orphaned_images"] = {
                            "success": False,
                            "error": self._public_error(e),
                            "message": "Failed to cleanup orphaned images",
                        }

                if "check_hash_duplicates" in operations:
                    try:
                        hash_duplicates = self.db.check_hash_duplicates()
                        results["check_hash_duplicates"] = {
                            "success": True,
                            "duplicate_hashes": len(hash_duplicates),
                            "message": f"Found {len(hash_duplicates)} duplicate hash groups",
                        }
                    except Exception as e:
                        results["check_hash_duplicates"] = {
                            "success": False,
                            "error": self._public_error(e),
                            "message": "Failed to check hash duplicates",
                        }

                if "statistics" in operations:
                    try:
                        db_info = self.db.model.get_database_info()
                        results["statistics"] = {
                            "success": True,
                            "info": db_info,
                            "message": "Database statistics retrieved",
                        }
                    except Exception as e:
                        results["statistics"] = {
                            "success": False,
                            "error": self._public_error(e),
                            "message": "Failed to get database statistics",
                        }

                if "prune_orphaned_prompts" in operations:
                    try:
                        removed_count = self.db.prune_orphaned_prompts()
                        results["prune_orphaned_prompts"] = {
                            "success": True,
                            "removed_count": removed_count,
                            "message": f"Removed {removed_count} orphaned prompts (prompts with no linked images, excluding protected prompts)",
                        }
                    except Exception as e:
                        results["prune_orphaned_prompts"] = {
                            "success": False,
                            "error": self._public_error(e),
                            "message": "Failed to prune orphaned prompts",
                        }

                if "check_consistency" in operations:
                    try:
                        consistency_issues = self.db.check_consistency()
                        results["check_consistency"] = {
                            "success": True,
                            "issues_found": len(consistency_issues),
                            "issues": consistency_issues[:10],
                            "message": f"Found {len(consistency_issues)} consistency issues",
                        }
                    except Exception as e:
                        results["check_consistency"] = {
                            "success": False,
                            "error": self._public_error(e),
                            "message": "Failed to check database consistency",
                        }

            await self._run_in_executor(_run_maintenance)

            all_successful = all(
                result.get("success", False) for result in results.values()
            )

            return web.json_response(
                {
                    "success": True,
                    "operations_completed": len(results),
                    "all_successful": all_successful,
                    "results": results,
                    "message": f"Maintenance completed: {len(results)} operations processed",
                }
            )

        except Exception as e:
            self.logger.error(f"Maintenance error: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": "Maintenance failed"}, status=500
            )

    async def backup_database(self, request):
        """Download a consistent copy of the prompts database (WAL included)."""
        try:
            model = self.db.model
            db_path = model.db_path

            if not os.path.exists(db_path):
                return web.json_response(
                    {"success": False, "error": "Database file not found"}, status=404
                )

            fd, temp_path = tempfile.mkstemp(suffix=".db")
            os.close(fd)
            try:
                loop = asyncio.get_running_loop()
                ok = await loop.run_in_executor(None, model.backup_database, temp_path)
                if not ok:
                    return web.json_response(
                        {"success": False, "error": "Failed to create database backup"},
                        status=500,
                    )
                # Read once (the file is deleted right after) rather than
                # streaming so the temp file never outlives the request.
                with open(temp_path, "rb") as fh:
                    file_data = fh.read()
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)

            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"prompts_backup_{timestamp}.db"

            return web.Response(
                body=file_data,
                content_type="application/octet-stream",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "Content-Length": str(len(file_data)),
                },
            )

        except Exception as e:
            self.logger.error(f"Backup error: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": f"Failed to backup database: {str(e)}"},
                status=500,
            )

    async def restore_database(self, request):
        """Restore the prompts database from an uploaded SQLite file.

        The upload is streamed to a temp file (capped at restore_max_bytes),
        verified with PromptModel.verify_database_file and only then copied
        over the live database, which is backed up first.
        """
        max_bytes = getattr(self, "restore_max_bytes", 100 * 1024 * 1024)
        temp_path = None
        try:
            reader = await request.multipart()
            field = await reader.next()

            if not field or field.name != "database_file":
                return web.json_response(
                    {
                        "success": False,
                        "error": "No database file uploaded. Expected field name: database_file",
                    },
                    status=400,
                )

            fd, temp_path = tempfile.mkstemp(suffix=".db")
            os.close(fd)
            size = 0
            with open(temp_path, "wb") as out:
                while True:
                    chunk = await field.read_chunk()
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        return web.json_response(
                            {
                                "success": False,
                                "error": f"File too large. Maximum size is {max_bytes // (1024 * 1024)}MB",
                            },
                            status=400,
                        )
                    out.write(chunk)

            if size == 0:
                return web.json_response(
                    {"success": False, "error": "Uploaded file is empty"}, status=400
                )

            model = self.db.model
            ok, reason = model.verify_database_file(temp_path)
            if not ok:
                return web.json_response(
                    {"success": False, "error": reason}, status=400
                )

            loop = asyncio.get_running_loop()
            backup_path = await loop.run_in_executor(
                None, model.restore_from_file, temp_path
            )
            self.db = PromptDatabase(model.db_path)
            prompt_count = self.db.model.get_database_info().get("total_prompts", 0)

            return web.json_response(
                {
                    "success": True,
                    "message": f"Database restored successfully. Found {prompt_count} prompts.",
                    "prompt_count": prompt_count,
                    "backup_created": backup_path or None,
                }
            )

        except Exception as e:
            self.logger.error(f"Restore error: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": f"Failed to restore database: {str(e)}"},
                status=500,
            )
        finally:
            if temp_path:
                for suffix in ("", "-wal", "-shm", "-journal"):
                    if os.path.exists(temp_path + suffix):
                        os.unlink(temp_path + suffix)

    async def scan_images(self, request):
        """Scan ComfyUI output images for prompt metadata and add them to the database.

        Streams progress as server-sent events; all filesystem and database
        work runs through the executor.
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

        async for chunk in self._scan_images_events():
            await response.write(chunk.encode("utf-8"))

        await response.write_eof()
        return response

    async def _scan_images_events(self):
        """Yield SSE strings while scanning every configured output directory."""
        try:
            self.logger.info("Starting image scan operation")
            output_dirs = self._get_all_output_dirs()
            if not output_dirs:
                self.logger.error("No output directories found")
                yield _sse(
                    {
                        "type": "error",
                        "message": "No output directories found. "
                        "Configure scan directories in Settings.",
                    }
                )
                return

            dir_names = [self._public_path(d) for d in output_dirs]
            yield self._scan_progress(
                0, f"Scanning {len(output_dirs)} directory(ies) for media files..."
            )

            media_files = await self._run_in_executor(
                self._collect_output_media_sync, output_dirs
            )
            counts = {"processed": 0, "found": 0, "added": 0, "linked": 0}
            if media_files:
                yield self._scan_progress(
                    5, f"Found {len(media_files)} media files to process...", counts
                )
            async for event in self._scan_batches(media_files, counts):
                yield event

            self.logger.info(
                f"Scan completed: processed={counts['processed']}, "
                f"found={counts['found']}, new_prompts_added={counts['added']}, "
                f"images_linked_to_existing={counts['linked']}"
            )
            yield _sse({"type": "complete", **counts, "directories": dir_names})

        except Exception:
            self.logger.exception("Scan error")
            yield _sse(
                {
                    "type": "error",
                    "message": "An internal error occurred. Check server logs for details.",
                }
            )

    async def _scan_batches(self, media_files, counts):
        """Process ``media_files`` in executor batches, updating ``counts`` in place.

        Yields one progress event per batch.
        """
        total = len(media_files)
        for batch_start in range(0, total, SCAN_BATCH_SIZE):
            batch = media_files[batch_start : batch_start + SCAN_BATCH_SIZE]
            batch_results = await self._run_in_executor(
                self._extract_batch_metadata_sync, batch
            )
            batch_counts = await self._run_in_executor(
                self._ingest_scan_batch_sync, batch_results
            )
            for key in counts:
                counts[key] += batch_counts[key]

            done = min(batch_start + len(batch), total)
            yield self._scan_progress(
                int(done / total * 100), f"Processing file {done}/{total}...", counts
            )
            await asyncio.sleep(0)

    @staticmethod
    def _scan_progress(progress, status, counts=None):
        """SSE progress event for the output scan."""
        counts = counts or {}
        return _sse(
            {
                "type": "progress",
                "progress": progress,
                "status": status,
                "processed": counts.get("processed", 0),
                "found": counts.get("found", 0),
            }
        )

    def _collect_output_media_sync(self, output_dirs):
        """Collect all media files from the output directories (blocking I/O)."""
        return _collect_media_files(output_dirs, IMAGE_EXTENSIONS + VIDEO_EXTENSIONS)

    def _extract_batch_metadata_sync(self, file_batch):
        """Extract metadata from a batch of files in one executor call."""
        results = []
        for media_file in file_batch:
            try:
                results.append(
                    (media_file, self._extract_comfyui_metadata(str(media_file)))
                )
            except Exception:
                results.append((media_file, {}))
        return results

    def _ingest_scan_batch_sync(self, batch_results):
        """Store the prompts found in a metadata batch (blocking DB work)."""
        counts = {"processed": 0, "found": 0, "added": 0, "linked": 0}
        for media_file, metadata in batch_results:
            counts["processed"] += 1
            try:
                outcome = self._ingest_scanned_file(media_file, metadata)
            except Exception as e:
                self.logger.error(f"Error processing {media_file.name}: {e}")
                continue
            if outcome is None:
                continue
            counts["found"] += 1
            if outcome in ("added", "linked"):
                counts[outcome] += 1
        return counts

    def _ingest_scanned_file(self, media_file, metadata):
        """Save or link one scanned file; returns 'added', 'linked', 'found' or None."""
        if not metadata:
            return None
        parsed = self._parse_comfyui_prompt(metadata)
        if not (parsed.get("prompt") or parsed.get("parameters")):
            return None

        prompt_text = self._extract_readable_prompt(parsed)
        if prompt_text is not None and not isinstance(prompt_text, str):
            prompt_text = str(prompt_text)
        if not (prompt_text and prompt_text.strip()):
            return "found"
        prompt_text = prompt_text.strip()

        prompt_hash = generate_prompt_hash(prompt_text)
        existing = self.db.get_prompt_by_hash(prompt_hash)
        if existing:
            try:
                self.db.link_image_to_prompt(existing["id"], str(media_file))
            except Exception as e:
                self.logger.error(
                    f"Failed to link {media_file.name} to existing prompt: {e}"
                )
                return "found"
            return "linked"

        prompt_id = self.db.save_prompt(
            prompt_text,
            "scanned",
            ["auto-scanned"],
            None,
            f"Auto-scanned from {media_file.name}",
            prompt_hash,
        )
        if not prompt_id:
            return "found"
        try:
            self.db.link_image_to_prompt(prompt_id, str(media_file))
        except Exception as e:
            self.logger.error(f"Failed to link {media_file.name} to new prompt: {e}")
        return "added"
