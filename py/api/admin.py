"""Admin and maintenance API routes for PromptManager."""

import asyncio
import datetime
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import traceback
from pathlib import Path

from aiohttp import web


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
            self.logger.error(f"Scan duplicates error: {e}")
            return web.json_response(
                {
                    "success": False,
                    "error": f"Failed to scan duplicate images: {str(e)}",
                },
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
            self.logger.error(f"Cleanup error: {e}")
            return web.json_response(
                {"success": False, "error": f"Failed to cleanup duplicates: {str(e)}"},
                status=500,
            )

    async def find_duplicate_images(self):
        """Find duplicate images in ComfyUI output directory using content hashing."""
        self.logger.info("Scanning for duplicate images")

        try:
            output_dir = self._find_comfyui_output_dir()
            if not output_dir:
                self.logger.warning("ComfyUI output directory not found")
                return []

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
            video_extensions = [".mp4", ".avi", ".mov", ".mkv", ".webm", ".gif"]
            all_extensions = image_extensions + video_extensions

            media_files = []
            seen_paths = set()
            for ext in all_extensions:
                for pattern in [f"*{ext.lower()}", f"*{ext.upper()}"]:
                    for media_path in output_path.rglob(pattern):
                        if "thumbnails" not in media_path.parts:
                            normalized_path = str(media_path).lower()
                            if normalized_path not in seen_paths:
                                seen_paths.add(normalized_path)
                                media_files.append(media_path)

            self.logger.info(f"Found {len(media_files)} media files to analyze")

            file_hashes = {}
            processed = 0

            for media_path in media_files:
                try:
                    file_hash = self._calculate_file_hash(media_path)

                    if file_hash not in file_hashes:
                        file_hashes[file_hash] = []

                    stat = media_path.stat()
                    rel_path = media_path.relative_to(output_path)
                    extension = media_path.suffix.lower()
                    is_video = extension in [ext.lower() for ext in video_extensions]
                    media_type = "video" if is_video else "image"

                    # Check if thumbnail exists
                    thumbnail_url = None
                    thumbnails_dir = output_path / "thumbnails"
                    if thumbnails_dir.exists():
                        thumbnail_ext = ".jpg" if is_video else extension
                        rel_path_no_ext = rel_path.with_suffix("")
                        thumbnail_rel_path = f"thumbnails/{rel_path_no_ext.as_posix()}_thumb{thumbnail_ext}"
                        thumbnail_abs_path = output_path / thumbnail_rel_path

                        if thumbnail_abs_path.exists():
                            from urllib.parse import quote

                            thumbnail_url = f'/prompt_manager/images/serve/{quote(thumbnail_rel_path, safe="/")}'

                    image_info = {
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
                        "hash": file_hash,
                    }

                    file_hashes[file_hash].append(image_info)
                    processed += 1

                    if processed % 100 == 0:
                        self.logger.info(
                            f"Processed {processed}/{len(media_files)} files for duplicate detection"
                        )

                except Exception as e:
                    self.logger.error(f"Error processing file {media_path}: {e}")
                    continue

            # Find duplicates (groups with more than one file)
            duplicates = []
            for file_hash, images in file_hashes.items():
                if len(images) > 1:
                    images.sort(key=lambda x: x["modified_time"])
                    duplicates.append(
                        {"hash": file_hash, "images": images, "count": len(images)}
                    )

            self.logger.info(f"Found {len(duplicates)} groups of duplicate images")
            return duplicates

        except Exception as e:
            self.logger.error(f"Error finding duplicate images: {e}")
            return []

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

            if not image_paths:
                return web.json_response(
                    {"success": False, "error": "No image paths provided"},
                    status=400,
                )

            deleted_count = 0
            failed_count = 0
            failed_files = []

            for image_path in image_paths:
                try:
                    # Ensure the path is within the output directory for security
                    output_dir = self._find_comfyui_output_dir()
                    if not output_dir:
                        failed_files.append(
                            f"{image_path} (output directory not found)"
                        )
                        failed_count += 1
                        continue

                    output_path = Path(output_dir)
                    file_path = Path(image_path)

                    # Security check - ensure file is within output directory
                    try:
                        file_path.resolve().relative_to(output_path.resolve())
                    except ValueError:
                        self.logger.warning(
                            f"Attempted to delete file outside output directory: {image_path}"
                        )
                        failed_files.append(f"{image_path} (outside output directory)")
                        failed_count += 1
                        continue

                    if file_path.exists() and file_path.is_file():
                        os.remove(file_path)
                        deleted_count += 1
                        self.logger.info(f"Deleted duplicate image: {image_path}")

                        # Also try to delete associated thumbnail if it exists
                        try:
                            rel_path = file_path.relative_to(output_path)
                            rel_path_no_ext = rel_path.with_suffix("")
                            thumbnail_path = (
                                output_path
                                / "thumbnails"
                                / f"{rel_path_no_ext.as_posix()}_thumb{file_path.suffix}"
                            )
                            if thumbnail_path.exists():
                                os.remove(thumbnail_path)
                                self.logger.debug(
                                    f"Deleted associated thumbnail: {thumbnail_path}"
                                )
                        except Exception as e:
                            self.logger.warning(
                                f"Could not delete thumbnail for {image_path}: {e}"
                            )
                    else:
                        failed_files.append(f"{image_path} (file not found)")
                        failed_count += 1

                except Exception as e:
                    self.logger.error(f"Error deleting file {image_path}: {e}")
                    failed_files.append(f"{image_path} ({str(e)})")
                    failed_count += 1

            response_data = {
                "success": True,
                "deleted_count": deleted_count,
                "failed_count": failed_count,
                "message": f"Deleted {deleted_count} files successfully",
            }

            if failed_count > 0:
                response_data["failed_files"] = failed_files
                response_data["message"] += f", {failed_count} failed"

            return web.json_response(response_data)

        except Exception as e:
            self.logger.error(f"Delete duplicate images error: {e}")
            return web.json_response(
                {
                    "success": False,
                    "error": f"Failed to delete duplicate images: {str(e)}",
                },
                status=500,
            )

    async def get_statistics(self, request):
        """Get database statistics."""
        try:
            stats = await self._run_in_executor(self.db.get_statistics)

            return web.json_response({"success": True, "stats": stats})

        except Exception as e:
            self.logger.error(f"Stats error: {e}")
            return web.json_response(
                {"success": False, "error": f"Failed to get statistics: {str(e)}"},
                status=500,
            )

    async def get_settings(self, request):
        """Get current settings."""
        try:
            from ..config import PromptManagerConfig, GalleryConfig

            # Get monitored directories from image monitor singleton if available
            monitored_dirs = []
            try:
                import sys

                monitor_module = None
                for mod_name in list(sys.modules.keys()):
                    if "image_monitor" in mod_name and hasattr(
                        sys.modules[mod_name], "_monitor_instance"
                    ):
                        monitor_module = sys.modules[mod_name]
                        break

                if monitor_module and monitor_module._monitor_instance is not None:
                    monitored_dirs = getattr(
                        monitor_module._monitor_instance, "monitored_directories", []
                    )
                elif GalleryConfig.MONITORING_DIRECTORIES:
                    monitored_dirs = GalleryConfig.MONITORING_DIRECTORIES
            except Exception:
                if GalleryConfig.MONITORING_DIRECTORIES:
                    monitored_dirs = GalleryConfig.MONITORING_DIRECTORIES

            return web.json_response(
                {
                    "success": True,
                    "settings": {
                        "result_timeout": PromptManagerConfig.RESULT_TIMEOUT,
                        "webui_display_mode": PromptManagerConfig.WEBUI_DISPLAY_MODE,
                        "gallery_root_path": (
                            GalleryConfig.MONITORING_DIRECTORIES[0]
                            if GalleryConfig.MONITORING_DIRECTORIES
                            else ""
                        ),
                        "monitored_directories": monitored_dirs,
                    },
                }
            )
        except Exception as e:
            return web.json_response(
                {"success": False, "error": f"Failed to get settings: {str(e)}"},
                status=500,
            )

    async def save_settings(self, request):
        """Save settings."""
        try:
            from ..config import PromptManagerConfig, GalleryConfig

            data = await request.json()
            restart_required = False

            # Update in-memory config
            if "result_timeout" in data:
                PromptManagerConfig.RESULT_TIMEOUT = data["result_timeout"]
            if "webui_display_mode" in data:
                PromptManagerConfig.WEBUI_DISPLAY_MODE = data["webui_display_mode"]

            # Handle gallery root path
            if "gallery_root_path" in data:
                new_path = data["gallery_root_path"].strip()
                old_path = (
                    GalleryConfig.MONITORING_DIRECTORIES[0]
                    if GalleryConfig.MONITORING_DIRECTORIES
                    else ""
                )

                if new_path != old_path:
                    if new_path:
                        from pathlib import Path as _Path

                        resolved = _Path(new_path).resolve()
                        if not resolved.is_dir():
                            return web.json_response(
                                {
                                    "success": False,
                                    "error": f"Gallery path does not exist or is not a directory: {new_path}",
                                },
                                status=400,
                            )
                        blocked = [
                            "/etc",
                            "/usr",
                            "/bin",
                            "/sbin",
                            "/boot",
                            "/proc",
                            "/sys",
                            "/dev",
                            "/var/log",
                            "/root",
                            "C:\\Windows",
                            "C:\\Program Files",
                        ]
                        for b in blocked:
                            if str(resolved).startswith(b):
                                return web.json_response(
                                    {
                                        "success": False,
                                        "error": "Gallery path cannot point to a system directory",
                                    },
                                    status=400,
                                )
                        GalleryConfig.MONITORING_DIRECTORIES = [new_path]
                    else:
                        GalleryConfig.MONITORING_DIRECTORIES = []
                    restart_required = True

            # Save to config file for persistence
            config_dir = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
            config_file = os.path.join(config_dir, "config.json")

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
            except Exception as save_err:
                self.logger.warning(f"Could not save config file: {save_err}")

            return web.json_response(
                {
                    "success": True,
                    "message": "Settings saved successfully",
                    "restart_required": restart_required,
                }
            )
        except Exception as e:
            return web.json_response(
                {"success": False, "error": f"Failed to save settings: {str(e)}"},
                status=500,
            )

    async def run_diagnostics(self, request):
        """Run comprehensive system diagnostics and health checks."""
        try:
            results = {}

            # Check database
            try:
                db_path = "prompts.db"
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
                        "message": f"Database file not found: {db_path}",
                    }
            except Exception as e:
                results["database"] = {
                    "status": "error",
                    "message": f"Database error: {str(e)}",
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
            potential_dirs = ["output", "../output", "../../output"]

            for dir_path in potential_dirs:
                abs_path = os.path.abspath(dir_path)
                if os.path.exists(abs_path):
                    output_dirs.append(abs_path)

            results["comfyui_output"] = {
                "status": "ok" if output_dirs else "warning",
                "output_dirs": output_dirs,
            }

            # Check image monitor status
            try:
                from ...utils.image_monitor import _monitor_instance

                if _monitor_instance is not None:
                    monitor_status = _monitor_instance.get_status()
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
                    "message": f"Failed to get monitor status: {str(e)}",
                }

            return web.json_response({"success": True, "diagnostics": results})

        except Exception as e:
            self.logger.error(f"Diagnostics error: {e}", exc_info=True)
            return web.json_response({"success": False, "error": str(e)}, status=500)

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
                            "message": f"Failed to create test link: {str(e)}",
                        },
                    }
                )

        except Exception as e:
            self.logger.error(f"Test link error: {e}", exc_info=True)
            return web.json_response({"success": False, "error": str(e)}, status=500)

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
                            "error": str(e),
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
                            "error": str(e),
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
                            "error": str(e),
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
                            "error": str(e),
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
                            "error": str(e),
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
                            "error": str(e),
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
                            "error": str(e),
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
                {"success": False, "error": f"Maintenance failed: {str(e)}"}, status=500
            )

    async def backup_database(self, request):
        """Backup the entire prompts.db database file."""
        try:
            db_path = "prompts.db"

            if not os.path.exists(db_path):
                return web.json_response(
                    {"success": False, "error": "Database file not found"}, status=404
                )

            with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as temp_file:
                temp_path = temp_file.name

            shutil.copy2(db_path, temp_path)

            with open(temp_path, "rb") as f:
                file_data = f.read()

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
        """Restore the prompts.db database from uploaded file."""
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

            MAX_RESTORE_SIZE = 100 * 1024 * 1024  # 100MB
            file_data = await field.read()

            if not file_data:
                return web.json_response(
                    {"success": False, "error": "Uploaded file is empty"}, status=400
                )

            if len(file_data) > MAX_RESTORE_SIZE:
                return web.json_response(
                    {
                        "success": False,
                        "error": f"File too large. Maximum size is {MAX_RESTORE_SIZE // (1024*1024)}MB",
                    },
                    status=400,
                )

            with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as temp_file:
                temp_path = temp_file.name
                temp_file.write(file_data)

            try:
                with sqlite3.connect(temp_path) as conn:
                    conn.row_factory = sqlite3.Row

                    cursor = conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name='prompts'"
                    )
                    if not cursor.fetchone():
                        raise ValueError("Database does not contain a 'prompts' table")

                    cursor = conn.execute("PRAGMA table_info(prompts)")
                    columns = [row["name"] for row in cursor.fetchall()]
                    required_columns = ["id", "text", "created_at"]

                    for col in required_columns:
                        if col not in columns:
                            raise ValueError(f"Database missing required column: {col}")

                    cursor = conn.execute("SELECT COUNT(*) as count FROM prompts")
                    prompt_count = cursor.fetchone()["count"]

                db_path = "prompts.db"
                backup_path = f"{db_path}.backup_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"

                if os.path.exists(db_path):
                    shutil.copy2(db_path, backup_path)
                    self.logger.info(f"Current database backed up to: {backup_path}")

                shutil.copy2(temp_path, db_path)

                # Reinitialize the database connection
                try:
                    from ...database.operations import PromptDatabase
                except ImportError:
                    import sys

                    sys.path.insert(
                        0,
                        os.path.dirname(
                            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                        ),
                    )
                    from database.operations import PromptDatabase
                self.db = PromptDatabase()

                return web.json_response(
                    {
                        "success": True,
                        "message": f"Database restored successfully. Found {prompt_count} prompts.",
                        "prompt_count": prompt_count,
                        "backup_created": (
                            backup_path if os.path.exists(db_path) else None
                        ),
                    }
                )

            except sqlite3.Error as e:
                return web.json_response(
                    {"success": False, "error": f"Invalid SQLite database: {str(e)}"},
                    status=400,
                )
            except ValueError as e:
                return web.json_response(
                    {"success": False, "error": str(e)}, status=400
                )
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)

        except Exception as e:
            self.logger.error(f"Restore error: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": f"Failed to restore database: {str(e)}"},
                status=500,
            )

    async def scan_images(self, request):
        """Scan ComfyUI output images for prompt metadata and add them to the database."""

        async def stream_response():
            try:
                self.logger.info("Starting image scan operation")
                self.logger.info("Starting scan (timer clearing not implemented yet)")

                output_dir = self._find_comfyui_output_dir()
                if not output_dir:
                    self.logger.error("ComfyUI output directory not found")
                    yield f"data: {json.dumps({'type': 'error', 'message': 'ComfyUI output directory not found'})}\n\n"
                    return

                yield f"data: {json.dumps({'type': 'progress', 'progress': 0, 'status': 'Scanning for PNG files...', 'processed': 0, 'found': 0})}\n\n"

                png_files = await self._run_in_executor(
                    lambda: list(Path(output_dir).rglob("*.png"))
                )
                total_files = len(png_files)

                if total_files == 0:
                    yield f"data: {json.dumps({'type': 'complete', 'processed': 0, 'found': 0, 'added': 0})}\n\n"
                    return

                yield f"data: {json.dumps({'type': 'progress', 'progress': 5, 'status': f'Found {total_files} PNG files to process...', 'processed': 0, 'found': 0})}\n\n"

                processed_count = 0
                found_count = 0
                added_count = 0
                linked_count = 0

                try:
                    from ...utils.hashing import generate_prompt_hash
                except ImportError:
                    import sys

                    current_dir = os.path.dirname(
                        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    )
                    sys.path.insert(0, current_dir)
                    from utils.hashing import generate_prompt_hash

                for i, png_file in enumerate(png_files):
                    try:
                        metadata = await self._run_in_executor(
                            self._extract_comfyui_metadata, str(png_file)
                        )
                        processed_count += 1

                        if metadata:
                            self.logger.debug(
                                f"Found metadata in {os.path.basename(png_file)}: {list(metadata.keys())}"
                            )

                            parsed_data = self._parse_comfyui_prompt(metadata)
                            self.logger.debug(
                                f"Parsed data keys: {list(parsed_data.keys())}, has prompt: {bool(parsed_data.get('prompt'))}, has parameters: {bool(parsed_data.get('parameters'))}"
                            )

                            if parsed_data.get("prompt") or parsed_data.get(
                                "parameters"
                            ):
                                found_count += 1
                                prompt_text = self._extract_readable_prompt(parsed_data)

                                if prompt_text:
                                    self.logger.debug(
                                        f"Found prompt in {os.path.basename(png_file)} (type: {type(prompt_text)}): {str(prompt_text)[:100]}..."
                                    )
                                else:
                                    self.logger.debug(
                                        f"No readable prompt found in {os.path.basename(png_file)}, parsed_data keys: {list(parsed_data.keys())}"
                                    )

                                if prompt_text and not isinstance(prompt_text, str):
                                    self.logger.debug(
                                        f"Converting prompt_text from {type(prompt_text)} to string"
                                    )
                                    prompt_text = str(prompt_text)

                                if prompt_text and prompt_text.strip():
                                    try:
                                        prompt_hash = generate_prompt_hash(
                                            prompt_text.strip()
                                        )
                                        self.logger.debug(
                                            f"Generated hash for prompt: {prompt_hash[:16]}..."
                                        )

                                        existing = await self._run_in_executor(
                                            self.db.get_prompt_by_hash, prompt_hash
                                        )
                                        if existing:
                                            self.logger.debug(
                                                f"Found existing prompt ID {existing['id']} for image {os.path.basename(png_file)}"
                                            )
                                            try:
                                                await self._run_in_executor(
                                                    self.db.link_image_to_prompt,
                                                    existing["id"],
                                                    str(png_file),
                                                )
                                                linked_count += 1
                                                self.logger.debug(
                                                    f"Linked image {os.path.basename(png_file)} to existing prompt {existing['id']}"
                                                )
                                            except Exception as e:
                                                self.logger.error(
                                                    f"Failed to link image {png_file} to existing prompt: {e}"
                                                )
                                        else:
                                            self.logger.debug(
                                                f"Saving new prompt from {os.path.basename(png_file)}"
                                            )
                                            prompt_id = await self._run_in_executor(
                                                self.db.save_prompt,
                                                prompt_text.strip(),
                                                "scanned",
                                                ["auto-scanned"],
                                                f"Auto-scanned from {os.path.basename(png_file)}",
                                                prompt_hash,
                                            )

                                            if prompt_id:
                                                added_count += 1
                                                self.logger.info(
                                                    f"Successfully saved new prompt with ID {prompt_id} from {os.path.basename(png_file)}"
                                                )
                                                try:
                                                    await self._run_in_executor(
                                                        self.db.link_image_to_prompt,
                                                        prompt_id,
                                                        str(png_file),
                                                    )
                                                    self.logger.debug(
                                                        f"Linked image {os.path.basename(png_file)} to new prompt {prompt_id}"
                                                    )
                                                except Exception as e:
                                                    self.logger.error(
                                                        f"Failed to link image {png_file} to new prompt: {e}"
                                                    )
                                            else:
                                                self.logger.error(
                                                    f"Failed to save prompt from {os.path.basename(png_file)} - no ID returned"
                                                )

                                    except Exception as e:
                                        self.logger.error(
                                            f"Failed to save prompt from {png_file}: {e}"
                                        )

                        # Update progress every 10 files
                        if i % 10 == 0 or i == total_files - 1:
                            progress = int((i + 1) / total_files * 100)
                            status = f"Processing file {i + 1}/{total_files}..."

                            yield f"data: {json.dumps({'type': 'progress', 'progress': progress, 'status': status, 'processed': processed_count, 'found': found_count})}\n\n"

                            await asyncio.sleep(0.01)

                    except Exception as e:
                        self.logger.error(f"Error processing {png_file}: {e}")
                        continue

                self.logger.info(
                    f"Scan completed: processed={processed_count}, found={found_count}, new_prompts_added={added_count}, images_linked_to_existing={linked_count}"
                )
                yield f"data: {json.dumps({'type': 'complete', 'processed': processed_count, 'found': found_count, 'added': added_count, 'linked': linked_count})}\n\n"

            except Exception as e:
                self.logger.error(f"Scan error: {e}")
                self.logger.error(f"Scan error traceback: {traceback.format_exc()}")
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

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
