"""REST API routes for PromptManager.

Implements clean RESTful endpoints with proper error handling,
validation, and response formatting.
"""

from __future__ import annotations

import json
import math
import logging
import os
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from aiohttp import web

from src.config import config
# from src.repositories.image_repository import ImageRepository  # v1 - no longer used
from src.repositories.prompt_repository import PromptRepository
from src.repositories.generated_image_repository import GeneratedImageRepository
from src.galleries.image_gallery import ImageGallery
from src.metadata.extractor import MetadataExtractor
from src.database.migration import MigrationDetector, MigrationProgress
from src.services.migration_service import MigrationService
from src.services.stats_service import StatsService
from src.services.incremental_stats_service import IncrementalStatsService
from src.services.background_scheduler import StatsScheduler
from src.api.realtime_events import RealtimeEvents
from utils.file_system import get_file_system
from utils.cache import CacheManager
from utils.logging import LogConfig


class PromptManagerAPI:
    """REST API handler for PromptManager operations.
    
    Provides clean RESTful endpoints for all PromptManager functionality
    with proper separation of concerns and error handling.
    """

    def __init__(
        self,
        db_path: str = "prompts.db",
        *,
        migration_service: MigrationService | None = None,
        run_migration_check: bool = True,
    ):
        """Initialize API with repositories.

        Args:
            db_path: Path to database file
        """
        self.logger = logging.getLogger("promptmanager.api")

        fs = get_file_system()
        try:
            if db_path and Path(db_path).resolve() != fs.get_database_path().resolve():
                fs.set_custom_database_path(db_path)
        except Exception as exc:
            self.logger.warning("Unable to apply custom database path %s: %s", db_path, exc)

        # Store db_path for later use (e.g., stats cache service)
        self.db_path = str(fs.get_database_path("prompts.db"))

        self.generated_image_repo: Optional[GeneratedImageRepository] = None
        self._refresh_repositories()
        # Initialize gallery with database path
        db_path = str(get_file_system().get_database_path("prompts.db"))
        self.gallery = ImageGallery(db_path)
        self.metadata_extractor = MetadataExtractor()
        self.migration_service = migration_service or self._build_migration_service()
        self._startup_migration_info: Dict[str, Any] | None = None
        self.realtime = RealtimeEvents()

        # Initialize cache and thumbnail service
        self.cache = CacheManager()
        self.thumbnail_api = None
        self._init_thumbnail_service()

        # Initialize maintenance API
        self.maintenance_api = None
        self._init_maintenance_service()
        stats_db_path = str(get_file_system().get_database_path("prompts.db"))
        try:
            # Use IncrementalStatsService for better performance
            self.incremental_stats = IncrementalStatsService(self.prompt_repo, config)

            # Initialize background scheduler for periodic updates
            self.stats_scheduler = StatsScheduler(self.incremental_stats)
            self.stats_scheduler.start()
            self.logger.info("Started incremental stats background scheduler")

            # Keep old StatsService for backward compatibility if needed
            self.stats_service: StatsService | None = StatsService(stats_db_path)
        except Exception as exc:  # pragma: no cover - defensive logging
            self.logger.warning("StatsService unavailable: %s", exc)
            self.stats_service = None
        self.incremental_stats = None
        self.stats_scheduler = None
        try:
            self.log_file = Path(config.logging.file).expanduser()
            self.logs_dir = self.log_file.resolve().parent
        except Exception:
            fs = get_file_system()
            self.log_file = fs.get_logs_dir() / "promptmanager.log"
            self.logs_dir = self.log_file.parent.resolve()

        # Get web directory path
        self.web_dir = Path(__file__).parent.parent.parent / "web"

        # Initialize ComfyUI output monitor if available
        self._init_comfyui_monitor()

        self.logger.info("PromptManager API initialized")

        if run_migration_check:
            self._run_startup_migration_check()

    @staticmethod
    def _sanitize_for_json(value: Any) -> Any:
        """Recursively replace NaN and Inf values so JSON is valid."""

        if isinstance(value, dict):
            return {key: PromptManagerAPI._sanitize_for_json(sub_value) for key, sub_value in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [PromptManagerAPI._sanitize_for_json(item) for item in value]
        if isinstance(value, float):
            if math.isnan(value) or math.isinf(value):
                return None
        return value

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _init_thumbnail_service(self):
        """Initialize the thumbnail service if available."""
        try:
            # Check for required dependencies first
            import importlib
            missing_deps = []
            
            # Check for PIL/Pillow
            try:
                importlib.import_module('PIL')
            except ImportError:
                missing_deps.append('Pillow')
            
            if missing_deps:
                self.logger.warning(
                    f"Thumbnail service disabled - missing dependencies: {', '.join(missing_deps)}. "
                    f"Install with: pip install {' '.join(missing_deps)}"
                )
                self.thumbnail_api = None
                return
            
            # Import Database for thumbnail service
            # Handle potential conflicts with ComfyUI's app/database module
            import sys
            from pathlib import Path
            
            # Save current sys.path
            original_path = sys.path.copy()
            
            try:
                # Temporarily prioritize our package directory
                parent_dir = Path(__file__).parent.parent.parent
                
                # Modify path to ensure our database module is found first
                sys.path = [str(parent_dir)] + [p for p in sys.path if p != str(parent_dir)]
                
                # Clear any cached database modules that might conflict
                if 'database' in sys.modules:
                    del sys.modules['database']
                    if 'database.operations' in sys.modules:
                        del sys.modules['database.operations']
                
                # Now import should find our database module first
                from src.database import PromptDatabase
                
            finally:
                # Restore original path
                sys.path = original_path
            
            from src.api.route_handlers.thumbnails import ThumbnailAPI

            # Create database instance for thumbnails
            db = PromptDatabase()

            # Initialize thumbnail API
            self.thumbnail_api = ThumbnailAPI(db, self.cache, self.realtime)
            self.logger.info("Thumbnail service initialized successfully")
        except ImportError as e:
            self.logger.warning(f"Thumbnail service not available (ImportError): {e}")
            import traceback
            self.logger.warning(f"Import traceback: {traceback.format_exc()}")
            self.thumbnail_api = None
        except Exception as e:
            self.logger.error(f"Failed to initialize thumbnail service: {e}")
            import traceback
            self.logger.error(f"Full traceback: {traceback.format_exc()}")
            self.thumbnail_api = None

    def _init_comfyui_monitor(self):
        """Initialize ComfyUI output monitor for automatic metadata extraction."""
        # DISABLED: Monitor causing database lock issues
        # Will be re-enabled after fixing concurrent access patterns
        self.logger.info("ComfyUI monitor temporarily disabled to prevent database locks")
        return

        # Original code commented out:
        # try:
        #     # Check if ComfyUI output directory exists
        #     comfy_output = Path.home() / "ai-apps/ComfyUI-3.12/output"
        #     if not comfy_output.exists():
        #         # Try alternative common paths
        #         comfy_output = Path("../../output")
        #         if not comfy_output.exists():
        #             comfy_output = Path("../../../ComfyUI/output")
        #
        #     if comfy_output.exists():
        #         from src.services.comfyui_monitor import start_comfyui_monitor
        #
        #         # Start monitoring ComfyUI output directory
        #         start_comfyui_monitor(str(comfy_output), self.db_path)
        #         self.logger.info(f"ComfyUI output monitor started for: {comfy_output}")
        #     else:
        #         self.logger.debug("ComfyUI output directory not found, monitor disabled")
        #
        # except Exception as e:
        #     self.logger.warning(f"Failed to start ComfyUI monitor: {e}")

    def _init_maintenance_service(self):
        """Initialize maintenance API service."""
        try:
            from src.api.route_handlers.maintenance import MaintenanceAPI

            self.maintenance_api = MaintenanceAPI(self.db_path)
            self.logger.info("Maintenance API service initialized")
        except Exception as e:
            self.logger.warning(f"Maintenance API not available: {e}")
            self.maintenance_api = None

    def _normalize_prompt_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Coerce incoming payload keys to repository-friendly names."""
        data = dict(payload)  # Work on a shallow copy to avoid side effects

        prompt_text = (
            data.pop("text", None)
            or data.get("positive_prompt")
            or data.get("prompt")
            or data.pop("positive", None)
            or data.pop("positivePrompt", None)
        )
        if prompt_text:
            data["positive_prompt"] = prompt_text
            data.setdefault("prompt", prompt_text)

        negative_text = data.pop("negative", None) or data.get("negative_prompt")
        if negative_text is not None:
            data["negative_prompt"] = negative_text

        return data

    def _format_prompt(
        self,
        prompt: Optional[Dict[str, Any]],
        *,
        include_images: bool = False,
        image_limit: int = 4,
    ) -> Optional[Dict[str, Any]]:
        """Ensure responses expose canonical prompt fields and optional assets."""
        if prompt is None:
            return None

        result = dict(prompt)
        if "positive_prompt" in result and "prompt" not in result:
            result["prompt"] = result["positive_prompt"]

        if include_images and self.generated_image_repo:
            images = self.generated_image_repo.list_for_prompt(
                result.get("id"),
                limit=image_limit,
                order_by="id DESC",
            )
            result["images"] = [self._format_generated_image(img) for img in images]
            try:
                result["image_count"] = self.generated_image_repo.count(prompt_id=result.get("id"))
            except Exception:
                # Fallback to length if count fails for any reason
                result["image_count"] = len(images)

        return result

    def _format_generated_image(self, image: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not image:
            return None

        data = dict(image)
        if isinstance(data.get("metadata"), str):
            try:
                data["metadata"] = json.loads(data["metadata"])  # type: ignore[arg-type]
            except json.JSONDecodeError:
                data["metadata"] = None

        rendered: Optional[Dict[str, Any]] = None
        try:
            rendered = self.gallery.render_item(data)
        except Exception:  # pragma: no cover - defensive
            rendered = None

        if rendered:
            data.update(rendered)
            if rendered.get("image_url"):
                data.setdefault("url", rendered["image_url"])
            if rendered.get("thumbnail_url"):
                data["thumbnail_url"] = rendered["thumbnail_url"]
            if rendered.get("thumbnail_variants"):
                data["thumbnail_variants"] = rendered["thumbnail_variants"]

        image_id = data.get("id")
        if image_id is not None:
            data.setdefault("url", self._build_generated_image_url(image_id))
            data.setdefault("thumbnail_url", self._build_generated_image_url(image_id, thumbnail=True))

        return data

    def _build_generated_image_url(self, image_id: int, *, thumbnail: bool = False) -> str:
        base = f"/api/v1/generated-images/{image_id}/file"
        return f"{base}?thumbnail=1" if thumbnail else base

    def _refresh_repositories(self) -> None:
        """Reinitialize repositories to point at the active database path."""
        db_path = str(get_file_system().get_database_path())
        self.prompt_repo = PromptRepository(db_path)
        # Use GeneratedImageRepository for v2 (generated_images table)
        self.image_repo = GeneratedImageRepository(db_path)
        self.generated_image_repo = GeneratedImageRepository(db_path)

    def _get_allowed_image_roots(self) -> list[Path]:
        """Return directories that are safe for serving generated images from."""

        roots: list[Path] = []
        fs = get_file_system()

        try:
            comfy_root = fs.resolve_comfyui_root()
        except Exception:
            comfy_root = None

        if comfy_root is not None:
            comfy_root = self._safe_resolve_path(comfy_root)
            roots.append(comfy_root)

            for subdir in ("output", "user", "input"):
                candidate = comfy_root / subdir
                if candidate.exists():
                    roots.append(self._safe_resolve_path(candidate))

        try:
            user_dir = fs.get_user_dir(create=False)
        except Exception:
            user_dir = None

        if user_dir and user_dir.exists():
            roots.append(self._safe_resolve_path(user_dir))

        return self._dedupe_existing_paths(roots)

    @staticmethod
    def _safe_resolve_path(path: Path) -> Path:
        try:
            return path.resolve()
        except Exception:
            return path

    @staticmethod
    def _dedupe_existing_paths(paths: list[Path]) -> list[Path]:
        deduped: list[Path] = []
        seen: set[str] = set()

        for path in paths:
            if not path.exists():
                continue
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(path)

        return deduped

    def _build_migration_service(self) -> MigrationService:
        """Construct the migration service anchored to the detected ComfyUI root."""
        fs = get_file_system()
        try:
            comfy_root = fs.resolve_comfyui_root()
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.error("Unable to resolve ComfyUI root: %s", exc)
            raise RuntimeError(
                f"Cannot determine ComfyUI root directory: {exc}\n"
                "Please ensure PromptManager is installed in ComfyUI/custom_nodes/ "
                "or set COMFYUI_PATH environment variable."
            ) from exc

        detector = MigrationDetector(comfyui_root=str(comfy_root))
        progress = MigrationProgress()
        return MigrationService(detector=detector, progress=progress)

    def _run_startup_migration_check(self) -> None:
        """Detect legacy databases on initialization and surface console guidance."""
        try:
            info = self.migration_service.get_migration_info()
            self._startup_migration_info = info
        except Exception as exc:  # pragma: no cover - defensive logging
            self.logger.error("Migration detection failed during startup: %s", exc)
            return

        if not info.get("needed"):
            self.logger.debug("No legacy PromptManager database detected at startup")
            return

        v1_info = info.get("v1_info", {})
        path = v1_info.get("path", "<unknown>")
        prompts = v1_info.get("prompt_count", 0)
        images = v1_info.get("image_count", 0)
        size_mb = v1_info.get("size_mb")
        size_line = f"  • Size: {size_mb} MB" if size_mb is not None else None

        banner_lines = [
            "=" * 60,
            "[PromptManager] DATABASE MIGRATION REQUIRED",
            "=" * 60,
            f"  • Legacy database detected at: {path}",
            f"  • Prompts: {prompts}",
            f"  • Images: {images}",
        ]
        if size_line:
            banner_lines.append(size_line)
        banner_lines.extend(
            [
                "",
                "  Visit http://localhost:8188/prompt_manager/migration",
                "  to migrate your data or start with a fresh library.",
                "",
            ]
        )

        banner = "\n".join(banner_lines)
        print("\n" + banner + "\n")
        self.logger.warning("Legacy v1 database detected at %s; migration required", path)

    def add_routes(self, routes: web.RouteTableDef) -> None:
        """Add all API routes to the route table.
        
        Args:
            routes: aiohttp route table
        """
        if getattr(self, '_routes_registered', False):
            return

        # Prompt endpoints
        routes.post("/api/v1/prompts")(self.create_prompt)
        routes.get("/api/v1/prompts")(self.list_prompts)
        routes.get("/api/v1/prompts/{id}")(self.get_prompt)
        routes.put("/api/v1/prompts/{id}")(self.update_prompt)
        routes.delete("/api/v1/prompts/{id}")(self.delete_prompt)
        routes.post("/api/v1/prompts/search")(self.search_prompts)
        routes.get("/api/v1/prompts/recent")(self.get_recent_prompts)
        routes.get("/api/v1/prompts/popular")(self.get_popular_prompts)
        routes.get("/api/v1/prompts/categories")(self.get_prompt_categories)
        routes.get("/api/v1/prompts/{prompt_id}/images")(self.get_prompt_images)

        # Bulk operations
        routes.post("/api/v1/prompts/bulk")(self.bulk_create_prompts)
        routes.delete("/api/v1/prompts/bulk")(self.bulk_delete_prompts)

        # Gallery endpoints
        routes.get("/api/v1/gallery/images")(self.list_gallery_images)
        routes.get("/api/v1/gallery/images/{id}")(self.get_gallery_image)
        routes.get("/api/v1/gallery/images/{id}/file")(self.get_gallery_image_file)
        routes.get("/api/v1/generated-images/{image_id}/file")(self.get_generated_image_file)
        routes.post("/api/v1/gallery/scan")(self.scan_for_images)
        routes.post("/api/scan")(self.scan_comfyui_images)  # Simple scan endpoint for frontend

        # Metadata endpoints
        routes.get("/api/v1/metadata/{filename}")(self.get_image_metadata)
        routes.post("/api/v1/metadata/extract")(self.extract_metadata)
        routes.post("/prompt_manager/api/v1/metadata/extract")(self.extract_metadata)
        routes.post("/api/prompt_manager/api/v1/metadata/extract")(self.extract_metadata)

        # Migration endpoints
        routes.get("/api/v1/migration/info")(self.get_migration_info)
        routes.get("/api/v1/migration/status")(self.get_migration_status)
        routes.get("/api/v1/migration/progress")(self.get_migration_progress)
        routes.post("/api/v1/migration/start")(self.start_migration)
        routes.post("/api/v1/migration/trigger")(self.trigger_migration)

        # System endpoints
        routes.get("/api/v1/health")(self.health_check)
        routes.get("/api/v1/system/stats")(self.get_statistics)
        routes.get("/api/v1/stats/overview")(self.get_stats_overview)
        routes.get("/api/v1/stats/scheduler/status")(self.get_stats_scheduler_status)

        # Maintenance endpoints - only if service is available
        if self.maintenance_api:
            self.maintenance_api.add_routes(routes)
            self.logger.info("Maintenance API routes registered")
        routes.post("/api/v1/stats/scheduler/force")(self.force_stats_update)
        routes.post("/api/v1/system/vacuum")(self.vacuum_database)
        routes.post("/api/v1/system/backup")(self.backup_database)
        routes.post("/api/v1/system/database/verify")(self.verify_database_path)
        routes.post("/api/v1/system/database/apply")(self.apply_database_path)
        routes.post("/api/v1/system/database/migrate")(self.migrate_database_path)
        routes.get("/api/v1/system/categories")(self.get_categories)
        routes.get("/api/v1/system/settings")(self.get_system_settings)
        routes.put("/api/v1/system/settings")(self.update_system_settings)

        # Logs
        routes.get("/api/v1/logs")(self.list_logs)
        routes.get("/api/v1/logs/{name}")(self.get_log_file)
        routes.post("/api/v1/logs/clear")(self.clear_logs)
        routes.post("/api/v1/logs/rotate")(self.rotate_logs)
        routes.get("/api/v1/logs/download/{name}")(self.download_log)

        # Thumbnail endpoints
        if self.thumbnail_api:
            # Thumbnail operations
            routes.post('/api/v1/thumbnails/scan')(self.thumbnail_api.scan_missing_thumbnails)
            routes.post('/api/v1/thumbnails/generate')(self.thumbnail_api.generate_thumbnails)
            routes.post('/api/v1/thumbnails/cancel')(self.thumbnail_api.cancel_generation)
            routes.get('/api/v1/thumbnails/status/{task_id}')(self.thumbnail_api.get_task_status)
            routes.post('/api/v1/thumbnails/rebuild')(self.thumbnail_api.rebuild_thumbnails)

            # Thumbnail serving
            routes.get('/api/v1/thumbnails/{image_id}/{size}')(self.thumbnail_api.serve_thumbnail)
            routes.get('/api/v1/thumbnails/{image_id}')(self.thumbnail_api.serve_thumbnail)

            # Cache management
            routes.get('/api/v1/thumbnails/cache/stats')(self.thumbnail_api.get_cache_stats)
            routes.delete('/api/v1/thumbnails/cache')(self.thumbnail_api.clear_cache)
            routes.delete('/api/v1/thumbnails/cache/{size}')(self.thumbnail_api.clear_cache_size)

            # Settings
            routes.get('/api/v1/settings/thumbnails')(self.thumbnail_api.get_settings)
            routes.put('/api/v1/settings/thumbnails')(self.thumbnail_api.update_settings)
            routes.get('/api/v1/thumbnails/disk-usage')(self.thumbnail_api.get_disk_usage)

            # FFmpeg testing
            routes.post('/api/v1/thumbnails/test-ffmpeg')(self.thumbnail_api.test_ffmpeg)

            # Health check endpoint
            routes.get('/api/v1/thumbnails/health')(self.thumbnail_api.health_check)

            self.logger.info("Thumbnail API routes registered")
        else:
            # Provide a minimal fallback endpoint so we know routes are working
            async def thumbnail_not_available(request: web.Request) -> web.Response:
                return web.json_response({
                    "error": "Thumbnail service not initialized",
                    "message": "Missing dependencies. Install Pillow with: pip install Pillow, then restart ComfyUI"
                }, status=503)

            routes.get('/api/v1/thumbnails/health')(thumbnail_not_available)
            routes.post('/api/v1/thumbnails/scan')(thumbnail_not_available)
            self.logger.warning("Thumbnail API not available - fallback routes registered")

        # Maintenance endpoints
        routes.get("/api/prompt_manager/maintenance/stats")(self.get_maintenance_stats)
        routes.post("/api/prompt_manager/maintenance/deduplicate")(self.remove_duplicates_maintenance)
        routes.post("/api/prompt_manager/maintenance/clean-orphans")(self.clean_orphans_maintenance)
        routes.post("/api/prompt_manager/maintenance/validate-paths")(self.validate_paths_maintenance)
        routes.post("/api/prompt_manager/maintenance/optimize")(self.optimize_database_maintenance)
        routes.post("/api/prompt_manager/maintenance/backup")(self.backup_database_maintenance)
        routes.post("/api/prompt_manager/maintenance/remove-missing")(self.remove_missing_files_maintenance)
        routes.post("/api/prompt_manager/maintenance/update-file-metadata")(self.refresh_file_metadata_maintenance)
        routes.post("/api/prompt_manager/maintenance/fix-broken-links")(self.fix_broken_links_maintenance)
        routes.post("/api/prompt_manager/maintenance/check-integrity")(self.check_integrity_maintenance)
        routes.post("/api/prompt_manager/maintenance/reindex")(self.reindex_database_maintenance)
        routes.post("/api/prompt_manager/maintenance/export")(self.export_backup_maintenance)

        # Note: Thumbnail API routes need to be registered separately since we don't have app instance here
        # This is handled in the ComfyUI server integration

        self._routes_registered = True

    # ==================== Prompt Endpoints ====================

    async def create_prompt(self, request: web.Request) -> web.Response:
        """Create new prompt.
        
        POST /api/v1/prompts
        Body: {text: str, category?: str, tags?: list, rating?: int, notes?: str}
        """
        try:
            incoming = await request.json()
            data = self._normalize_prompt_payload(incoming)

            prompt_text = data.get("positive_prompt") or data.get("prompt")
            if not prompt_text:
                return web.json_response(
                    {"error": "Prompt text is required"},
                    status=400
                )

            prompt_id = self.prompt_repo.create(data)
            prompt = self._format_prompt(self.prompt_repo.read(prompt_id))

            # Broadcast realtime update
            await self.realtime.notify_prompt_created(prompt)
            await self.realtime.send_toast(f"Prompt '{prompt.get('title', 'Untitled')}' created", 'success')

            return web.json_response({
                "success": True,
                "data": prompt
            }, status=201)
            
        except json.JSONDecodeError:
            return web.json_response(
                {"error": "Invalid JSON"},
                status=400
            )
        except Exception as e:
            self.logger.error(f"Error creating prompt: {e}")
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    async def list_prompts(self, request: web.Request) -> web.Response:
        """List prompts with pagination and filtering.
        
        GET /api/v1/prompts?page=1&limit=50&category=x&order_by=created_at
        """
        try:
            # Parse query parameters
            page = int(request.query.get("page", 1))
            limit = int(request.query.get("limit", 50))
            offset = (page - 1) * limit

            order_field = request.query.get("order_by", "created_at")
            order_dir = request.query.get("order_dir", "desc").upper()
            order_clause = f"{order_field} {'DESC' if order_dir == 'DESC' else 'ASC'}"

            filter_kwargs = {}
            if category := request.query.get("category"):
                filter_kwargs["category"] = category
            if rating := request.query.get("rating"):
                try:
                    filter_kwargs["rating"] = int(rating)
                except (TypeError, ValueError):
                    pass
            if search := request.query.get("search"):
                filter_kwargs["prompt"] = f"%{search}%"

            include_images_param = request.query.get("include_images") or request.query.get("include")
            include_images = False
            if include_images_param:
                include_images = any(
                    token.strip().lower() in {"1", "true", "yes", "images", "image"}
                    for token in include_images_param.split(",")
                )

            try:
                image_limit = int(request.query.get("image_limit", 4))
            except (TypeError, ValueError):
                image_limit = 4

            prompts = [
                self._format_prompt(
                    record,
                    include_images=include_images,
                    image_limit=image_limit,
                )
                for record in self.prompt_repo.list(
                    limit=limit,
                    offset=offset,
                    order_by=order_clause,
                    **filter_kwargs,
                )
            ]

            total = self.prompt_repo.count(**filter_kwargs)

            return web.json_response({
                "success": True,
                "data": prompts,
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "total": total,
                    "total_pages": (total + limit - 1) // limit
                }
            })
            
        except ValueError as e:
            return web.json_response(
                {"error": f"Invalid parameter: {e}"},
                status=400
            )
        except Exception as e:
            self.logger.error(f"Error listing prompts: {e}")
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    async def get_prompt(self, request: web.Request) -> web.Response:
        """Get prompt by ID.
        
        GET /api/v1/prompts/{id}
        """
        try:
            prompt_id = request.match_info["id"]

            include_images_param = request.query.get("include_images") or request.query.get("include")
            include_images = False
            if include_images_param:
                include_images = any(
                    token.strip().lower() in {"1", "true", "yes", "images", "image"}
                    for token in include_images_param.split(",")
                )
            try:
                image_limit = int(request.query.get("image_limit", 8))
            except (TypeError, ValueError):
                image_limit = 8

            prompt = self._format_prompt(
                self.prompt_repo.read(prompt_id),
                include_images=include_images,
                image_limit=image_limit,
            )
            
            if not prompt:
                return web.json_response(
                    {"error": "Prompt not found"},
                    status=404
                )
            
            return web.json_response({
                "success": True,
                "data": prompt
            })
            
        except Exception as e:
            self.logger.error(f"Error getting prompt: {e}")
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    async def update_prompt(self, request: web.Request) -> web.Response:
        """Update prompt by ID.
        
        PUT /api/v1/prompts/{id}
        Body: {text?: str, category?: str, tags?: list, rating?: int, notes?: str}
        """
        try:
            prompt_id = request.match_info["id"]
            raw_payload = await request.json()
            data = self._normalize_prompt_payload(raw_payload)

            if not self.prompt_repo.read(prompt_id):
                return web.json_response(
                    {"error": "Prompt not found"},
                    status=404
                )

            data.pop("positive_prompt", None)
            success = self.prompt_repo.update(prompt_id, data)

            if success:
                prompt = self._format_prompt(self.prompt_repo.read(prompt_id))

                # Broadcast realtime update
                await self.realtime.notify_prompt_updated(prompt)
                await self.realtime.send_toast(f"Prompt '{prompt.get('title', 'Untitled')}' updated", 'info')

                return web.json_response({
                    "success": True,
                    "data": prompt
                })
            else:
                return web.json_response(
                    {"error": "No fields to update"},
                    status=400
                )
            
        except json.JSONDecodeError:
            return web.json_response(
                {"error": "Invalid JSON"},
                status=400
            )
        except Exception as e:
            self.logger.error(f"Error updating prompt: {e}")
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    async def delete_prompt(self, request: web.Request) -> web.Response:
        """Delete prompt by ID.
        
        DELETE /api/v1/prompts/{id}
        """
        try:
            prompt_id = request.match_info["id"]
            
            if not self.prompt_repo.read(prompt_id):
                return web.json_response(
                    {"error": "Prompt not found"},
                    status=404
                )
            
            success = self.prompt_repo.delete(prompt_id)

            if success:
                # Broadcast realtime update
                await self.realtime.notify_prompt_deleted(int(prompt_id))
                await self.realtime.send_toast("Prompt deleted successfully", 'info')

            return web.json_response({
                "success": success,
                "message": "Prompt deleted successfully" if success else "Failed to delete prompt"
            })
            
        except Exception as e:
            self.logger.error(f"Error deleting prompt: {e}")
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    async def search_prompts(self, request: web.Request) -> web.Response:
        """Search prompts with advanced filters.
        
        POST /api/v1/prompts/search
        Body: {search?: str, category?: str, tags?: list, rating?: int}
        """
        try:
            data = await request.json()
            
            search_term = data.get("search")
            if not search_term:
                return web.json_response({"error": "Search term is required"}, status=400)

            prompts = [
                self._format_prompt(record)
                for record in self.prompt_repo.search(search_term, ["prompt", "negative_prompt", "category"])
            ]

            if category := data.get("category"):
                prompts = [p for p in prompts if p.get("category") == category]

            if tags := data.get("tags"):
                tag_set = set(tags if isinstance(tags, list) else [tags])
                prompts = [
                    p
                    for p in prompts
                    if isinstance(p.get("tags"), list) and tag_set.intersection(p.get("tags", []))
                ]

            if rating := data.get("rating"):
                try:
                    rating_value = int(rating)
                except (TypeError, ValueError):
                    rating_value = None
                if rating_value is not None:
                    prompts = [p for p in prompts if p.get("rating") == rating_value]

            limit = max(1, min(int(data.get("limit", 100)), 500))
            offset = max(0, int(data.get("offset", 0)))
            paginated = prompts[offset : offset + limit]

            return web.json_response({
                "success": True,
                "data": paginated,
                "count": len(prompts)
            })
            
        except json.JSONDecodeError:
            return web.json_response(
                {"error": "Invalid JSON"},
                status=400
            )
        except Exception as e:
            self.logger.error(f"Error searching prompts: {e}")
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    # ==================== Bulk Operations ====================

    async def bulk_create_prompts(self, request: web.Request) -> web.Response:
        """Bulk create prompts.
        
        POST /api/v1/prompts/bulk
        Body: {prompts: [{text: str, ...}, ...]}
        """
        try:
            data = await request.json()
            prompts_data = data.get("prompts", [])
            
            if not prompts_data:
                return web.json_response(
                    {"error": "No prompts provided"},
                    status=400
                )
            
            created = []
            errors = []
            
            for i, prompt_data in enumerate(prompts_data):
                try:
                    normalized = self._normalize_prompt_payload(prompt_data)
                    prompt_text = normalized.get("positive_prompt") or normalized.get("prompt")
                    if not prompt_text:
                        errors.append({
                            "index": i,
                            "error": "Text is required"
                        })
                        continue

                    prompt_id = self.prompt_repo.create(normalized)
                    created.append(prompt_id)

                except Exception as e:
                    errors.append({
                        "index": i,
                        "error": str(e)
                    })
            
            return web.json_response({
                "success": len(errors) == 0,
                "created": created,
                "errors": errors,
                "summary": {
                    "total": len(prompts_data),
                    "created": len(created),
                    "failed": len(errors)
                }
            })
            
        except json.JSONDecodeError:
            return web.json_response(
                {"error": "Invalid JSON"},
                status=400
            )
        except Exception as e:
            self.logger.error(f"Error in bulk create: {e}")
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    async def bulk_delete_prompts(self, request: web.Request) -> web.Response:
        """Bulk delete prompts.
        
        DELETE /api/v1/prompts/bulk
        Body: {ids: [1, 2, 3, ...]}
        """
        try:
            data = await request.json()
            ids = data.get("ids", [])
            
            if not ids:
                return web.json_response(
                    {"error": "No IDs provided"},
                    status=400
                )
            
            deleted = []
            errors = []
            
            for prompt_id in ids:
                try:
                    if self.prompt_repo.delete(prompt_id):
                        deleted.append(prompt_id)
                    else:
                        errors.append({
                            "id": prompt_id,
                            "error": "Not found"
                        })
                        
                except Exception as e:
                    errors.append({
                        "id": prompt_id,
                        "error": str(e)
                    })
            
            return web.json_response({
                "success": len(errors) == 0,
                "deleted": deleted,
                "errors": errors,
                "summary": {
                    "total": len(ids),
                    "deleted": len(deleted),
                    "failed": len(errors)
                }
            })
            
        except json.JSONDecodeError:
            return web.json_response(
                {"error": "Invalid JSON"},
                status=400
            )
        except Exception as e:
            self.logger.error(f"Error in bulk delete: {e}")
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    # ==================== Gallery Endpoints ====================

    async def get_generated_image_file(self, request: web.Request) -> web.StreamResponse:
        if not getattr(self, "generated_image_repo", None):
            return web.json_response({"error": "Generated image repository unavailable"}, status=500)

        try:
            image_id = int(request.match_info["image_id"])
        except (KeyError, ValueError):
            return web.json_response({"error": "Invalid image id"}, status=400)

        record = self.generated_image_repo.read(image_id)
        if not record:
            return web.json_response({"error": "Image not found"}, status=404)

        path = record.get("file_path")
        if request.query.get("thumbnail"):
            metadata = record.get("metadata") or {}
            if isinstance(metadata, dict) and metadata.get("thumbnail_path"):
                path = metadata.get("thumbnail_path")

        if not path:
            return web.json_response({"error": "Image path unavailable"}, status=404)

        candidate = Path(path).expanduser()
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError:
            return web.json_response({"error": "Image file not found"}, status=404)

        allowed_roots = self._get_allowed_image_roots()
        if allowed_roots:
            allowed = any(resolved.is_relative_to(root) for root in allowed_roots)
            if not allowed:
                return web.json_response({"error": "Access denied"}, status=403)

        return web.FileResponse(resolved)

    async def list_gallery_images(self, request: web.Request) -> web.Response:
        """List gallery images with pagination.

        GET /api/v1/gallery/images?page=1&limit=50
        """
        try:
            # Get parameters with defensive defaults
            page_str = request.query.get("page", "1")
            limit_str = request.query.get("limit", "50")
            
            # Parse with error handling
            try:
                page = int(page_str) if page_str else 1
            except (ValueError, TypeError):
                page = 1
                
            try:
                limit = int(limit_str) if limit_str else 50
            except (ValueError, TypeError):
                limit = 50
            
            # Ensure positive values
            page = max(1, page)
            limit = max(1, min(limit, 1000))  # Cap at 1000 for safety
            
            self.gallery.set_pagination(page, limit)
            result = self.gallery.get_paginated_items()
            
            return web.json_response({
                "success": True,
                "data": result["items"],
                "pagination": result["pagination"]
            })
            
        except ValueError as e:
            return web.json_response(
                {"error": f"Invalid parameter: {e}"},
                status=400
            )
        except Exception as e:
            self.logger.error(f"Error listing gallery images: {e}")
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    async def get_gallery_image(self, request: web.Request) -> web.Response:
        """Get gallery image details.

        GET /api/v1/gallery/images/{id}
        """
        try:
            image_id = request.match_info["id"]
            image = self.image_repo.read(image_id)
            
            if not image:
                return web.json_response(
                    {"error": "Image not found"},
                    status=404
                )
            
            # Add associated prompt if available
            if image.get("prompt_id"):
                image["prompt"] = self._format_prompt(
                    self.prompt_repo.read(image["prompt_id"])
                )
            
            return web.json_response({
                "success": True,
                "data": image
            })
            
        except Exception as e:
            self.logger.error(f"Error getting gallery image: {e}")
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    async def get_gallery_image_file(self, request: web.Request) -> web.StreamResponse:
        """Stream an image or thumbnail file to the browser."""
        try:
            image_id = int(request.match_info["id"])
        except (KeyError, ValueError):
            return web.json_response({"error": "Invalid image id"}, status=400)

        record = self.image_repo.read(image_id)
        if not record:
            return web.json_response({"error": "Image not found"}, status=404)

        use_thumbnail = request.query.get("thumbnail") is not None
        candidate_path: Optional[str]

        if use_thumbnail:
            candidate_path = record.get("thumbnail_path") or record.get("thumbnail_small_path")
            if candidate_path:
                thumb_path = Path(candidate_path).expanduser()
                if not thumb_path.exists():
                    candidate_path = None
                else:
                    return web.FileResponse(thumb_path)
        else:
            candidate_path = None

        # Fallback to the original asset when thumbnail is missing or requested directly
        if not candidate_path:
            candidate_path = record.get("file_path") or record.get("image_path")

        if not candidate_path:
            return web.json_response({"error": "Image path unavailable"}, status=404)

        path = Path(candidate_path).expanduser()
        if not path.exists():
            return web.json_response({"error": "Image file not found"}, status=404)

        return web.FileResponse(path)

    async def scan_for_images(self, request: web.Request) -> web.Response:
        """Scan directory for new images.

        POST /api/v1/gallery/scan
        Body: {directory: str}
        """
        try:
            data = await request.json()
            directory = data.get("directory", "")

            if not directory or not os.path.exists(directory):
                return web.json_response(
                    {"error": "Invalid directory"},
                    status=400
                )

            # Scan for images and add to database
            new_images = self.gallery.scan_directory(directory)

            for image_data in new_images:
                self.image_repo.create(image_data)

            return web.json_response({
                "success": True,
                "found": len(new_images),
                "message": f"Found {len(new_images)} new images"
            })

        except json.JSONDecodeError:
            return web.json_response(
                {"error": "Invalid JSON"},
                status=400
            )
        except Exception as e:
            self.logger.error(f"Error scanning for images: {e}")
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    async def scan_comfyui_images(self, request: web.Request) -> web.Response:
        """Scan ComfyUI output directory for images with prompts.

        POST /api/scan
        Returns: JSON response with scan results
        """
        try:
            # Import here to avoid circular imports
            from src.services.image_scanner import ImageScanner

            # Create scanner instance
            scanner = ImageScanner(self)

            # Track scan results
            files_scanned = 0
            prompts_found = 0
            prompts_added = 0
            images_linked = 0

            # Process scan results
            async for data in scanner.scan_images_generator():
                if data['type'] == 'progress':
                    files_scanned = data.get('processed', 0)
                    prompts_found = data.get('found', 0)
                elif data['type'] == 'complete':
                    files_scanned = data.get('processed', 0)
                    prompts_found = data.get('found', 0)
                    prompts_added = data.get('added', 0)
                    images_linked = data.get('linked', 0)
                    break
                elif data['type'] == 'error':
                    return web.json_response({
                        "success": False,
                        "message": data.get('message', 'Scan failed')
                    }, status=500)

            return web.json_response({
                "success": True,
                "files_scanned": files_scanned,
                "prompts_found": prompts_found,
                "prompts_added": prompts_added,
                "images_linked": images_linked,
                "message": f"Scan complete! Found {prompts_found} prompts, added {prompts_added} new ones"
            })

        except Exception as e:
            self.logger.error(f"Error in scan_comfyui_images: {e}")
            return web.json_response({
                "success": False,
                "message": str(e)
            }, status=500)

    # ==================== Metadata Endpoints ====================

    async def get_image_metadata(self, request: web.Request) -> web.Response:
        """Get metadata for image file.
        
        GET /api/v1/metadata/{filename}
        """
        try:
            filename = request.match_info["filename"]
            
            # Find image record
            image = self.image_repo.find_by_filename(filename)
            
            if not image:
                return web.json_response(
                    {"error": "Image not found"},
                    status=404
                )
            
            # Extract metadata from file
            metadata = self.metadata_extractor.extract_from_file(image["image_path"])
            
            return web.json_response(
                {
                    "success": True,
                    "data": metadata
                },
                dumps=lambda obj: json.dumps(obj, allow_nan=False)
            )
            
        except FileNotFoundError:
            return web.json_response(
                {"error": "Image file not found"},
                status=404
            )
        except Exception as e:
            self.logger.error(f"Error getting metadata: {e}")
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    async def extract_metadata(self, request: web.Request) -> web.Response:
        """Extract metadata from uploaded file.
        
        POST /api/v1/metadata/extract
        Body: multipart/form-data with 'file' field
        """
        try:
            self.logger.info("[metadata.extract] Incoming request from %s", request.remote)

            reader = await request.multipart()
            field = await reader.next()

            # Walk the multipart payload until we find the uploaded file
            while field is not None and field.name != "file":
                self.logger.debug("[metadata.extract] Skipping field '%s'", field.name)
                field = await reader.next()

            if field is None:
                self.logger.warning("[metadata.extract] No 'file' part found in multipart payload")
                return web.json_response(
                    {"error": "File field required"},
                    status=400
                )

            # Read file data
            data = await field.read()

            # Extract metadata
            metadata = self.metadata_extractor.extract_from_bytes(data)
            metadata = self._sanitize_for_json(metadata)

            self.logger.info("[metadata.extract] Metadata extracted successfully (%s bytes)", len(data))

            return web.json_response({
                "success": True,
                "data": metadata
            })

        except Exception as e:
            self.logger.exception("[metadata.extract] Error extracting metadata")
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    # ==================== System Endpoints ====================

    async def get_statistics(self, request: web.Request) -> web.Response:
        """Get system statistics.

        GET /api/v1/system/stats
        """
        try:
            stats = self.prompt_repo.get_statistics()
            if getattr(self, "generated_image_repo", None):
                stats["total_images"] = self.generated_image_repo.count()
            else:
                stats["total_images"] = self.image_repo.count()
            return web.json_response({
                "success": True,
                "data": stats
            })
            
        except Exception as e:
            self.logger.error(f"Error getting statistics: {e}")
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    async def get_stats_overview(self, request: web.Request) -> web.Response:
        """Return aggregated analytics snapshot for the dashboard.

        OPTIMIZED: Now uses instant stats from stats_snapshot table.
        Returns in <10ms instead of 2-3 minutes!
        """

        # NEW: Use hybrid approach - instant basic stats + fast analytics
        try:
            from src.services.hybrid_stats_service import HybridStatsService
            hybrid_service = HybridStatsService(self.db_path)
            snapshot = hybrid_service.get_overview()

            # Mark as hybrid for the frontend
            snapshot['loadTime'] = 'hybrid'
            snapshot['cached'] = False

            payload = {"success": True, "data": snapshot}
            response = web.json_response(payload)

            async def _json() -> Dict[str, Any]:
                return payload

            response.json = _json  # type: ignore[attr-defined]
            self.logger.info("Stats returned with hybrid approach (fast + complete)")
            return response

        except ImportError:
            self.logger.warning("HybridStatsService not available, using cache fallback")
            # Try simple cache as second fallback
            try:
                from src.services.stats_cache_service import StatsCacheService
                cache_service = StatsCacheService(self.db_path)
                snapshot = cache_service.get_overview()
                snapshot['loadTime'] = 'cache-only'
                payload = {"success": True, "data": snapshot}
                response = web.json_response(payload)
                async def _json() -> Dict[str, Any]:
                    return payload
                response.json = _json  # type: ignore[attr-defined]
                return response
            except Exception as exc2:
                self.logger.warning(f"Cache also failed: {exc2}")
        except Exception as exc:
            self.logger.warning(f"Hybrid stats error: {exc}, using fallback")

        # FALLBACK: Prefer incremental stats for better performance
        if self.incremental_stats:
            try:
                snapshot = self.incremental_stats.calculate_incremental_stats()
                payload = {"success": True, "data": snapshot}
                response = web.json_response(payload)

                async def _json() -> Dict[str, Any]:
                    return payload

                response.json = _json  # type: ignore[attr-defined]
                return response
            except Exception as exc:
                self.logger.warning("Incremental stats failed, falling back: %s", exc)
                # Fall back to original stats service

        if not self.stats_service:
            return web.json_response(
                {"success": False, "error": "Statistics service unavailable"},
                status=503,
            )

        # LAST RESORT: Old stats service (2-3 minutes!)
        force_param = (request.query or {}).get("force", "")
        force = str(force_param).lower() in {"1", "true", "yes", "force"}

        try:
            self.logger.warning("Using slow stats service - this will take 2-3 minutes!")
            snapshot = self.stats_service.get_overview(force=force)
            payload = {"success": True, "data": snapshot}
            response = web.json_response(payload)

            async def _json() -> Dict[str, Any]:
                return payload

            response.json = _json  # type: ignore[attr-defined]
            return response
        except Exception as exc:
            self.logger.error("Error building stats overview: %s", exc)
            payload = {"success": False, "error": str(exc)}
            response = web.json_response(payload, status=500)

            async def _json_error() -> Dict[str, Any]:
                return payload

            response.json = _json_error  # type: ignore[attr-defined]
            return response

    async def vacuum_database(self, request: web.Request) -> web.Response:
        """Vacuum database to optimize storage.
        
        POST /api/v1/system/vacuum
        """
        try:
            self.prompt_repo.vacuum()
            
            return web.json_response({
                "success": True,
                "message": "Database optimized successfully"
            })
            
        except Exception as e:
            self.logger.error(f"Error vacuuming database: {e}")
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    async def backup_database(self, request: web.Request) -> web.Response:
        """Create database backup.

        POST /api/v1/system/backup
        Body: {path?: str}
        """
        try:
            data = await request.json()
            
            # Generate backup path if not provided
            backup_path = data.get("path")
            if not backup_path:
                from datetime import datetime
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_path = f"prompts_backup_{timestamp}.db"
            
            success = self.prompt_repo.backup(backup_path)
            
            if success:
                return web.json_response({
                    "success": True,
                    "path": backup_path,
                    "message": "Database backed up successfully"
                })
            else:
                return web.json_response(
                    {"error": "Backup failed"},
                    status=500
                )
            
        except json.JSONDecodeError:
            return web.json_response(
                {"error": "Invalid JSON"},
                status=400
            )
        except Exception as e:
            self.logger.error(f"Error backing up database: {e}")
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    async def verify_database_path(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            payload = {}

        raw_path = (payload or {}).get("path")
        if not raw_path:
            return web.json_response({"ok": False, "error": "path is required"}, status=400)

        try:
            info = get_file_system().verify_database_path(raw_path)
            return web.json_response({"ok": True, "data": info})
        except Exception as exc:
            self.logger.error("Failed to verify database path %s: %s", raw_path, exc)
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    async def apply_database_path(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            payload = {}

        fs = get_file_system()
        raw_path = (payload or {}).get("path")

        try:
            if not raw_path:
                resolved = fs.set_custom_database_path(None)
                config.database.path = str(resolved)
                self._refresh_repositories()
                return web.json_response({"ok": True, "data": {"path": str(resolved), "custom": False}})

            info = fs.verify_database_path(raw_path)
            candidate = Path(info["resolved"])
            if not candidate.exists():
                return web.json_response({"ok": False, "error": "Database not found at specified path"}, status=400)

            resolved = fs.set_custom_database_path(str(candidate))
            config.database.path = str(resolved)
            self._refresh_repositories()
            return web.json_response({"ok": True, "data": {"path": str(resolved), "custom": True}})
        except Exception as exc:
            self.logger.error("Failed to apply database path %s: %s", raw_path, exc)
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    async def migrate_database_path(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            payload = {}

        raw_path = (payload or {}).get("path")
        if not raw_path:
            return web.json_response({"ok": False, "error": "path is required"}, status=400)

        mode = (payload or {}).get("mode", "move")
        copy = mode == "copy"

        fs = get_file_system()
        try:
            result = fs.move_database_file(raw_path, copy=copy)
            config.database.path = result.get("new_path", str(fs.get_database_path()))
            self._refresh_repositories()
            return web.json_response({"ok": True, "data": result})
        except Exception as exc:
            self.logger.error("Failed to migrate database to %s: %s", raw_path, exc)
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    async def get_categories(self, request: web.Request) -> web.Response:
        """Get list of prompt categories.

        GET /api/v1/system/categories
        """
        try:
            categories = self.prompt_repo.get_categories()

            return web.json_response({
                "success": True,
                "data": categories,
                "count": len(categories)
            })
            
        except Exception as e:
            self.logger.error(f"Error getting categories: {e}")
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    async def get_prompt_categories(self, request: web.Request) -> web.Response:
        return await self.get_categories(request)

    async def get_recent_prompts(self, request: web.Request) -> web.Response:
        try:
            limit = int(request.query.get("limit", 10))
        except (TypeError, ValueError):
            limit = 10
        prompts = self.prompt_repo.get_recent(limit)

        include_images_param = request.query.get("include_images") or request.query.get("include")
        include_images = True
        if include_images_param:
            include_images = any(
                token.strip().lower() in {"1", "true", "yes", "images", "image"}
                for token in include_images_param.split(",")
            )
        try:
            image_limit = int(request.query.get("image_limit", 4))
        except (TypeError, ValueError):
            image_limit = 4

        prompts = [
            self._format_prompt(p, include_images=include_images, image_limit=image_limit)
            for p in prompts
        ]
        return web.json_response({"success": True, "data": prompts})

    async def get_popular_prompts(self, request: web.Request) -> web.Response:
        try:
            limit = int(request.query.get("limit", 10))
        except (TypeError, ValueError):
            limit = 10
        prompts = self.prompt_repo.get_popular(limit)

        include_images_param = request.query.get("include_images") or request.query.get("include")
        include_images = True
        if include_images_param:
            include_images = any(
                token.strip().lower() in {"1", "true", "yes", "images", "image"}
                for token in include_images_param.split(",")
            )
        try:
            image_limit = int(request.query.get("image_limit", 4))
        except (TypeError, ValueError):
            image_limit = 4

        prompts = [
            self._format_prompt(p, include_images=include_images, image_limit=image_limit)
            for p in prompts
        ]
        return web.json_response({"success": True, "data": prompts})

    async def get_prompt_images(self, request: web.Request) -> web.Response:
        if not getattr(self, "generated_image_repo", None):
            return web.json_response({"success": True, "data": [], "count": 0})

        try:
            prompt_id = int(request.match_info["prompt_id"])
        except (KeyError, ValueError):
            return web.json_response({"error": "Invalid prompt id"}, status=400)

        try:
            limit = int(request.query.get("limit", 12))
        except (TypeError, ValueError):
            return web.json_response({"error": "Invalid limit"}, status=400)

        order_param = (request.query.get("order") or "desc").lower()
        order_by = "id ASC" if order_param in {"asc", "oldest"} else "id DESC"

        try:
            images = self.generated_image_repo.list_for_prompt(
                prompt_id,
                limit=limit,
                order_by=order_by,
            )
            total = self.generated_image_repo.count(prompt_id=prompt_id)
        except Exception as exc:
            self.logger.error("Failed to fetch generated images for prompt %s: %s", prompt_id, exc)
            return web.json_response({"error": str(exc)}, status=500)

        payload = [self._format_generated_image(img) for img in images if img]
        return web.json_response({"success": True, "data": payload, "count": total})

    async def get_system_settings(self, request: web.Request) -> web.Response:
        """Return UI and integration settings."""
        payload = self._build_settings_payload()
        return web.json_response({"success": True, "data": payload})

    async def get_migration_info(self, request: web.Request) -> web.Response:
        """Provide migration requirement details for the UI layer."""
        try:
            info = self.migration_service.get_migration_info()
            return web.json_response({"success": True, "data": info})
        except Exception as exc:
            self.logger.error("Failed to fetch migration info: %s", exc)
            return web.json_response({"success": False, "error": str(exc)}, status=500)

    async def get_migration_status(self, request: web.Request) -> web.Response:
        """Expose only the migration status enum value."""
        try:
            info = self.migration_service.get_migration_info()
            return web.json_response({"success": True, "data": info.get("status")})
        except Exception as exc:
            self.logger.error("Failed to fetch migration status: %s", exc)
            return web.json_response({"success": False, "error": str(exc)}, status=500)

    async def get_migration_progress(self, request: web.Request) -> web.Response:
        """Return the latest migration progress snapshot."""
        try:
            snapshot = self.migration_service.get_progress()
            return web.json_response({"success": True, "data": snapshot})
        except Exception as exc:
            self.logger.error("Failed to fetch migration progress: %s", exc)
            return web.json_response({"success": False, "error": str(exc)}, status=500)

    async def start_migration(self, request: web.Request) -> web.Response:
        """Kick off a migration or fresh-start action based on payload."""
        try:
            payload = await request.json()
        except Exception:
            payload = {}

        action = (payload or {}).get("action") or "migrate"

        try:
            result = self.migration_service.start_migration(action)
            return web.json_response({"success": True, "data": result})
        except ValueError as exc:
            return web.json_response({"success": False, "error": str(exc)}, status=400)
        except Exception as exc:
            self.logger.error("Migration start failed: %s", exc)
            return web.json_response({"success": False, "error": str(exc)}, status=500)

    async def trigger_migration(self, request: web.Request) -> web.Response:
        """Convenience endpoint that always triggers a standard migration run."""
        try:
            result = self.migration_service.start_migration("migrate")
            return web.json_response({"success": True, "data": result})
        except Exception as exc:
            self.logger.error("Migration trigger failed: %s", exc)
            return web.json_response({"success": False, "error": str(exc)}, status=500)

    async def update_system_settings(self, request: web.Request) -> web.Response:
        """Update subset of system settings."""
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON payload"}, status=400)

        try:
            comfy = data.get("comfyui", {})
            if "auto_track" in comfy:
                config.comfyui.auto_track = bool(comfy["auto_track"])
            if "extract_workflow" in comfy:
                config.comfyui.extract_workflow = bool(comfy["extract_workflow"])

            websocket_conf = data.get("websocket", {})
            if "enabled" in websocket_conf:
                config.websocket.enabled = bool(websocket_conf["enabled"])

            ui_conf = data.get("ui", {})
            if "compact_mode" in ui_conf:
                config.ui.compact_mode = bool(ui_conf["compact_mode"])

            config.save()
        except Exception as exc:
            self.logger.error(f"Failed to update settings: {exc}")
            return web.json_response(
                {"error": "Unable to update settings"},
                status=500
            )

        return web.json_response({
            "success": True,
            "data": self._build_settings_payload(),
        })

    async def list_logs(self, request: web.Request) -> web.Response:
        """Return available log files and the tail of the active selection."""
        try:
            tail = self._parse_tail(request.query.get("tail"))
        except Exception:  # pragma: no cover - defensive
            tail = 500

        requested_name = request.query.get("file") if hasattr(request, "query") else None
        try:
            files = self._collect_log_files()
        except Exception as exc:
            self.logger.error("Error listing log files: %s", exc)
            payload = {"success": False, "error": "Unable to read log directory"}
            response = web.json_response(payload, status=500)

            async def _json_error() -> Dict[str, Any]:
                return payload

            response.json = _json_error  # type: ignore[attr-defined]
            return response

        active_name = self._determine_active_log(requested_name, files)
        content = ""
        if active_name:
            path = self._resolve_log_path(active_name)
            if path:
                content = self._read_log_tail(path, tail)
            else:
                self.logger.warning("Requested log %s not found", active_name)
        payload = {
            "success": True,
            "files": files,
            "active": active_name,
            "content": content,
        }
        response = web.json_response(payload)

        async def _json() -> Dict[str, Any]:
            return payload

        response.json = _json  # type: ignore[attr-defined]
        return response

    async def get_log_file(self, request: web.Request) -> web.Response:
        """Return the tail of a log file."""
        name = request.match_info.get('name')
        if not name:
            payload = {"success": False, "error": "Missing log name"}
            response = web.json_response(payload, status=400)

            async def _json_missing() -> Dict[str, Any]:
                return payload

            response.json = _json_missing  # type: ignore[attr-defined]
            return response

        path = self._resolve_log_path(name)
        if not path:
            payload = {"success": False, "error": "Log file not found"}
            response = web.json_response(payload, status=404)

            async def _json_not_found() -> Dict[str, Any]:
                return payload

            response.json = _json_not_found  # type: ignore[attr-defined]
            return response

        tail = self._parse_tail(request.query.get("tail")) if hasattr(request, "query") else None
        try:
            data = self._read_log_tail(path, tail)
        except Exception as exc:
            self.logger.error("Error reading log file %s: %s", path, exc)
            payload = {"success": False, "error": "Unable to read log file"}
            response = web.json_response(payload, status=500)

            async def _json_error() -> Dict[str, Any]:
                return payload

            response.json = _json_error  # type: ignore[attr-defined]
            return response

        payload = {"success": True, "data": data}
        response = web.json_response(payload)

        async def _json_success() -> Dict[str, Any]:
            return payload

        response.json = _json_success  # type: ignore[attr-defined]
        return response

    async def clear_logs(self, _request: web.Request) -> web.Response:
        """Remove all stored log files."""
        try:
            LogConfig.clear_logs()
        except Exception as exc:  # pragma: no cover - defensive logging only
            self.logger.error("Error clearing logs: %s", exc)
            payload = {"success": False, "error": "Unable to clear logs"}
            response = web.json_response(payload, status=500)

            async def _json_error() -> Dict[str, Any]:
                return payload

            response.json = _json_error  # type: ignore[attr-defined]
            return response

        payload = {"success": True}
        response = web.json_response(payload)

        async def _json_success() -> Dict[str, Any]:
            return payload

        response.json = _json_success  # type: ignore[attr-defined]
        return response

    async def rotate_logs(self, _request: web.Request) -> web.Response:
        """Trigger a manual log rotation."""
        try:
            rotated = LogConfig.rotate_logs()
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.error("Error rotating logs: %s", exc)
            payload = {"success": False, "error": "Unable to rotate logs"}
            response = web.json_response(payload, status=500)

            async def _json_error() -> Dict[str, Any]:
                return payload

            response.json = _json_error  # type: ignore[attr-defined]
            return response

        if not rotated:
            payload = {"success": False, "error": "No active log file"}
            response = web.json_response(payload, status=400)

            async def _json_failure() -> Dict[str, Any]:
                return payload

            response.json = _json_failure  # type: ignore[attr-defined]
            return response

        payload = {"success": True}
        response = web.json_response(payload)

        async def _json_success() -> Dict[str, Any]:
            return payload

        response.json = _json_success  # type: ignore[attr-defined]
        return response

    async def download_log(self, request: web.Request) -> web.StreamResponse:
        """Stream the selected log file to the client."""
        name = request.match_info.get("name")
        if not name:
            return web.json_response({"success": False, "error": "Missing log name"}, status=400)

        path = self._resolve_log_path(name)
        if not path:
            return web.json_response({"success": False, "error": "Log file not found"}, status=404)

        headers = {
            "Content-Disposition": f"attachment; filename={path.name}",
            "Content-Type": "text/plain; charset=utf-8",
        }
        return web.FileResponse(path, headers=headers)

    def _parse_tail(self, value: Optional[str]) -> Optional[int]:
        if value is None:
            return 500
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 500
        return max(parsed, 0)

    def _collect_log_files(self) -> List[Dict[str, Any]]:
        files: List[Dict[str, Any]] = []
        if not getattr(self, "logs_dir", None):
            return files

        if not self.logs_dir.exists():
            return files

        for path in sorted(self.logs_dir.glob("*.log*"), key=lambda p: p.stat().st_mtime, reverse=True):
            stat = path.stat()
            files.append(
                {
                    "name": path.name,
                    "size": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                }
            )
        if getattr(self, "log_file", None):
            primary_name = Path(self.log_file).name
            for idx, entry in enumerate(files):
                if entry["name"] == primary_name:
                    if idx != 0:
                        files.insert(0, files.pop(idx))
                    break
        return files

    def _determine_active_log(self, requested: Optional[str], files: List[Dict[str, Any]]) -> Optional[str]:
        if requested and any(entry["name"] == requested for entry in files):
            return requested
        if getattr(self, "log_file", None):
            primary = Path(self.log_file).name
            if any(entry["name"] == primary for entry in files):
                return primary
        if files:
            return files[0]["name"]
        return None

    def _resolve_log_path(self, name: str) -> Optional[Path]:
        safe_name = Path(name).name
        candidate = (self.logs_dir / safe_name).resolve()
        try:
            logs_dir_resolved = self.logs_dir.resolve()
        except Exception:  # pragma: no cover - fallback to absolute path
            logs_dir_resolved = Path(str(self.logs_dir))
        if not str(candidate).startswith(str(logs_dir_resolved)):
            return None
        if not candidate.exists() or not candidate.is_file():
            return None
        return candidate

    def _read_log_tail(self, path: Path, tail: Optional[int]) -> str:
        if tail in (None, 0):
            return path.read_text(encoding="utf-8", errors="replace")

        from collections import deque

        buffer: deque[str] = deque(maxlen=tail)
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                buffer.append(line)
        return "".join(buffer)

    def _build_settings_payload(self) -> Dict[str, Any]:
        return {
            "comfyui": {
                "auto_track": config.comfyui.auto_track,
                "extract_workflow": getattr(config.comfyui, 'extract_workflow', True),
            },
            "websocket": {
                "enabled": config.websocket.enabled,
                "host": config.websocket.host,
                "port": config.websocket.port,
            },
            "ui": {
                "compact_mode": getattr(config.ui, 'compact_mode', False),
                "theme": config.ui.theme,
            },
        }

    async def health_check(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat() + 'Z',
        })

    # ==================== Maintenance Endpoints ====================

    async def get_maintenance_stats(self, request: web.Request) -> web.Response:
        """Get database maintenance statistics.

        GET /api/prompt_manager/maintenance/stats
        """
        try:
            from src.services.maintenance_service import MaintenanceService
            service = MaintenanceService(self)
            stats = service.get_statistics()

            return web.json_response({
                "success": True,
                "stats": stats
            })
        except Exception as e:
            self.logger.error(f"Error getting maintenance stats: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def remove_duplicates_maintenance(self, request: web.Request) -> web.Response:
        """Remove duplicate image links.

        POST /api/prompt_manager/maintenance/deduplicate
        """
        try:
            from src.services.maintenance_service import MaintenanceService
            service = MaintenanceService(self)
            result = service.remove_duplicates()

            # Broadcast realtime update
            if hasattr(self, 'sse'):
                await self.realtime.send_toast(result.get('message', 'Duplicates removed'), 'success' if result.get('success') else 'error')

            return web.json_response(result)
        except Exception as e:
            self.logger.error(f"Error removing duplicates: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def clean_orphans_maintenance(self, request: web.Request) -> web.Response:
        """Remove orphaned image links.

        POST /api/prompt_manager/maintenance/clean-orphans
        """
        try:
            from src.services.maintenance_service import MaintenanceService
            service = MaintenanceService(self)
            result = service.clean_orphans()

            # Broadcast realtime update
            if hasattr(self, 'sse'):
                await self.realtime.send_toast(result.get('message', 'Orphans cleaned'), 'success' if result.get('success') else 'error')

            return web.json_response(result)
        except Exception as e:
            self.logger.error(f"Error cleaning orphans: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def validate_paths_maintenance(self, request: web.Request) -> web.Response:
        """Validate file paths in database.

        POST /api/prompt_manager/maintenance/validate-paths
        """
        try:
            from src.services.maintenance_service import MaintenanceService
            service = MaintenanceService(self)
            result = service.validate_paths()

            # Broadcast realtime update
            if hasattr(self, 'sse'):
                await self.realtime.send_toast(result.get('message', 'Paths validated'), 'success' if result.get('success') else 'error')

            return web.json_response(result)
        except Exception as e:
            self.logger.error(f"Error validating paths: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def optimize_database_maintenance(self, request: web.Request) -> web.Response:
        """Optimize database.

        POST /api/prompt_manager/maintenance/optimize
        """
        try:
            from src.services.maintenance_service import MaintenanceService
            service = MaintenanceService(self)
            result = service.optimize_database()

            # Broadcast realtime update
            if hasattr(self, 'sse'):
                await self.realtime.send_toast(result.get('message', 'Database optimized'), 'success' if result.get('success') else 'error')

            return web.json_response(result)
        except Exception as e:
            self.logger.error(f"Error optimizing database: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def backup_database_maintenance(self, request: web.Request) -> web.Response:
        """Create database backup.

        POST /api/prompt_manager/maintenance/backup
        """
        try:
            from src.services.maintenance_service import MaintenanceService
            service = MaintenanceService(self)
            result = service.create_backup()

            # Broadcast realtime update
            if hasattr(self, 'sse'):
                await self.realtime.send_toast(result.get('message', 'Backup created'), 'success' if result.get('success') else 'error')

            return web.json_response(result)
        except Exception as e:
            self.logger.error(f"Error creating backup: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def fix_broken_links_maintenance(self, request: web.Request) -> web.Response:
        """Fix broken image links by finding relocated files.

        POST /api/prompt_manager/maintenance/fix-broken-links
        """
        try:
            from src.services.maintenance_service import MaintenanceService
            service = MaintenanceService(self)
            result = service.fix_broken_links()

            # Broadcast realtime update
            if hasattr(self, 'sse'):
                await self.realtime.send_toast(result.get('message', 'Fixed broken links'), 'success' if result.get('success') else 'error')

            return web.json_response(result)
        except Exception as e:
            self.logger.error(f"Error fixing broken links: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def remove_missing_files_maintenance(self, request: web.Request) -> web.Response:
        """Remove entries with missing files.

        POST /api/prompt_manager/maintenance/remove-missing
        """
        try:
            from src.services.maintenance_service import MaintenanceService
            service = MaintenanceService(self)
            result = service.remove_missing_files()

            # Broadcast realtime update
            if hasattr(self, 'sse'):
                await self.realtime.send_toast(result.get('message', 'Missing files removed'), 'success' if result.get('success') else 'error')

            return web.json_response(result)
        except Exception as e:
            self.logger.error(f"Error removing missing files: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def refresh_file_metadata_maintenance(self, request: web.Request) -> web.Response:
        """Recalculate file metadata such as size and dimensions.

        POST /api/prompt_manager/maintenance/update-file-metadata
        """
        try:
            from src.services.maintenance_service import MaintenanceService

            batch_size_param = request.query.get('batch_size', '500') or '500'
            try:
                batch_size = max(1, int(batch_size_param))
            except ValueError:
                batch_size = 500

            service = MaintenanceService(self)
            result = service.refresh_file_metadata(batch_size=batch_size)

            if hasattr(self, 'sse'):
                await self.realtime.send_toast(
                    result.get('message', 'File metadata refreshed'),
                    'success' if result.get('success') else 'error'
                )

            return web.json_response(result)
        except Exception as e:
            self.logger.error(f"Error refreshing file metadata: {e}")
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500,
            )

    async def check_integrity_maintenance(self, request: web.Request) -> web.Response:
        """Check database integrity.

        POST /api/prompt_manager/maintenance/check-integrity
        """
        try:
            from src.services.maintenance_service import MaintenanceService
            service = MaintenanceService(self)
            result = service.check_integrity()

            # Broadcast realtime update
            if hasattr(self, 'sse'):
                await self.realtime.send_toast(result.get('message', 'Integrity checked'), 'success' if result.get('success') else 'error')

            return web.json_response(result)
        except Exception as e:
            self.logger.error(f"Error checking integrity: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def reindex_database_maintenance(self, request: web.Request) -> web.Response:
        """Reindex database.

        POST /api/prompt_manager/maintenance/reindex
        """
        try:
            from src.services.maintenance_service import MaintenanceService
            service = MaintenanceService(self)
            result = service.reindex_database()

            # Broadcast realtime update
            if hasattr(self, 'sse'):
                await self.realtime.send_toast(result.get('message', 'Database reindexed'), 'success' if result.get('success') else 'error')

            return web.json_response(result)
        except Exception as e:
            self.logger.error(f"Error reindexing database: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def export_backup_maintenance(self, request: web.Request) -> web.Response:
        """Export database backup.

        POST /api/prompt_manager/maintenance/export
        """
        try:
            from src.services.maintenance_service import MaintenanceService
            service = MaintenanceService(self)
            result = service.export_backup()

            # Broadcast realtime update
            if hasattr(self, 'sse'):
                await self.realtime.send_toast(result.get('message', 'Backup exported'), 'success' if result.get('success') else 'error')

            return web.json_response(result)
        except Exception as e:
            self.logger.error(f"Error exporting backup: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def get_stats_scheduler_status(self, request: web.Request) -> web.Response:
        """Get status and statistics from the stats scheduler."""
        if not self.stats_scheduler:
            return web.json_response(
                {"success": False, "error": "Stats scheduler not initialized"},
                status=503
            )

        try:
            status = self.stats_scheduler.get_status()
            return web.json_response({
                "success": True,
                "data": status
            })
        except Exception as e:
            self.logger.error(f"Error getting scheduler status: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def force_stats_update(self, request: web.Request) -> web.Response:
        """Force an immediate stats update."""
        if not self.stats_scheduler:
            return web.json_response(
                {"success": False, "error": "Stats scheduler not initialized"},
                status=503
            )

        try:
            # Check for cache clear request
            data = await request.json() if request.body_exists else {}
            clear_cache = data.get('clear_cache', False)

            if clear_cache:
                self.stats_scheduler.clear_cache()
                message = "Cache cleared, forcing full recalculation"
            else:
                self.stats_scheduler.force_update()
                message = "Incremental update triggered"

            return web.json_response({
                "success": True,
                "message": message
            })
        except Exception as e:
            self.logger.error(f"Error forcing stats update: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    def cleanup(self):
        """Cleanup resources on shutdown."""
        if self.stats_scheduler:
            self.logger.info("Stopping stats scheduler")
            self.stats_scheduler.stop()
