"""REST API routes for PromptManager.

Implements clean RESTful endpoints with proper error handling,
validation, and response formatting.
"""

from __future__ import annotations

import json
import math
import logging
import os
import sqlite3
from ..database.connection_helper import DatabaseConnection
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from aiohttp import web

from ..config import config
# from src.repositories.image_repository import ImageRepository  # v1 - no longer used
from ..repositories.prompt_repository import PromptRepository
from ..repositories.generated_image_repository import GeneratedImageRepository
from ..galleries.image_gallery import ImageGallery
from ..metadata.extractor import MetadataExtractor
from ..database.migration import MigrationDetector, MigrationProgress
from ..services.migration_service import MigrationService
from ..services.hybrid_stats_service import HybridStatsService
from ..services.incremental_stats_service import IncrementalStatsService
from ..services.background_scheduler import StatsScheduler
from ..services.settings_service import SettingsService
from .realtime_events import RealtimeEvents
from utils.core.file_system import get_file_system
from utils.cache import CacheManager
from utils.logging import LogConfig

# Handler imports for orchestrator pattern
from .handlers.gallery import GalleryHandlers
from .handlers.metadata import MetadataHandlers
from .handlers.logs import LogsHandlers
from .handlers.migration import MigrationHandlers
from .handlers.prompts import PromptHandlers
from .handlers.system import SystemHandlers
from .handlers.maintenance import MaintenanceHandlers
from .handlers.tags import TagHandlers


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

        # Initialize settings service
        self.settings_service = SettingsService(self.db_path)
        self._init_maintenance_service()
        stats_db_path = str(get_file_system().get_database_path("prompts.db"))
        try:
            # Use IncrementalStatsService for better performance
            self.incremental_stats = IncrementalStatsService(self.prompt_repo, config)

            # Initialize background scheduler for periodic updates
            self.stats_scheduler = StatsScheduler(self.incremental_stats)
            self.stats_scheduler.start()
            self.logger.info("Started incremental stats background scheduler")

            # Use HybridStatsService for fast stats access
            self.stats_service: HybridStatsService | None = HybridStatsService(stats_db_path)
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

        # Initialize domain handlers (orchestrator pattern)
        self.gallery_handlers = GalleryHandlers(self)
        self.metadata_handlers = MetadataHandlers(self)
        self.logs_handlers = LogsHandlers(self)
        self.migration_handlers = MigrationHandlers(self)
        self.prompts_handlers = PromptHandlers(self)
        self.system_handlers = SystemHandlers(self)
        self.maintenance_handlers = MaintenanceHandlers(self)
        self.tag_handlers = TagHandlers(self)

        self.logger.info("PromptManager API initialized with 8 domain handlers")

        if run_migration_check:
            self._run_startup_migration_check()

    @staticmethod
    def _sanitize_for_json(value: Any) -> Any:
        """Recursively replace NaN, Inf values, and bytes objects so JSON is valid."""
        import base64

        if isinstance(value, dict):
            return {key: PromptManagerAPI._sanitize_for_json(sub_value) for key, sub_value in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [PromptManagerAPI._sanitize_for_json(item) for item in value]
        if isinstance(value, bytes):
            # Convert bytes to base64 string for JSON serialization
            return base64.b64encode(value).decode('ascii')
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
            # Use absolute import to avoid path resolution issues
            from ..database import PromptDatabase
            
            from .route_handlers.thumbnails import ThumbnailAPI

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
        try:
            # Check if ComfyUI output directory exists
            comfy_output = Path.home() / "ai-apps/ComfyUI-3.12/output"
            if not comfy_output.exists():
                # Try alternative common paths
                comfy_output = Path("../../output")
                if not comfy_output.exists():
                    comfy_output = Path("../../../ComfyUI/output")

            if comfy_output.exists():
                from ..services.comfyui_monitor import start_comfyui_monitor

                # Start monitoring ComfyUI output directory
                start_comfyui_monitor(str(comfy_output), self.db_path)
                self.logger.info(f"ComfyUI output monitor started for: {comfy_output}")
            else:
                self.logger.debug("ComfyUI output directory not found, monitor disabled")

        except Exception as e:
            self.logger.warning(f"Failed to start ComfyUI monitor: {e}")

    def _init_maintenance_service(self):
        """Initialize maintenance API service."""
        try:
            from .route_handlers.maintenance import MaintenanceAPI

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
        except Exception as e:
            self.logger.warning(f"Failed to resolve ComfyUI root: {e}")
            comfy_root = None

        if comfy_root is not None:
            comfy_root = self._safe_resolve_path(comfy_root)
            roots.append(comfy_root)
            self.logger.debug(f"Added ComfyUI root to allowed paths: {comfy_root}")

            for subdir in ("output", "user", "input"):
                candidate = comfy_root / subdir
                if candidate.exists():
                    resolved = self._safe_resolve_path(candidate)
                    roots.append(resolved)
                    self.logger.debug(f"Added {subdir} directory to allowed paths: {resolved}")

            # Also allow sibling ComfyUI directories (e.g., ComfyUI-3.12 when running from ComfyUI)
            # This handles cases where images were generated from other ComfyUI installations
            try:
                parent_dir = comfy_root.parent
                if parent_dir.exists():
                    for sibling in parent_dir.iterdir():
                        # Check if it looks like a ComfyUI installation
                        if sibling.is_dir() and sibling != comfy_root:
                            # Must have "ComfyUI" in name and have output or input directories
                            if "ComfyUI" in sibling.name or "comfyui" in sibling.name.lower():
                                for subdir in ("output", "input"):
                                    sibling_subdir = sibling / subdir
                                    if sibling_subdir.exists():
                                        resolved = self._safe_resolve_path(sibling_subdir)
                                        roots.append(resolved)
                                        self.logger.debug(f"Added sibling ComfyUI {subdir}: {resolved}")
                                        break  # Only need to verify one subdir exists
            except Exception as e:
                self.logger.debug(f"Could not scan for sibling ComfyUI directories: {e}")

        try:
            user_dir = fs.get_user_dir(create=False)
        except Exception as e:
            self.logger.warning(f"Failed to get user directory: {e}")
            user_dir = None

        if user_dir and user_dir.exists():
            resolved_user = self._safe_resolve_path(user_dir)
            roots.append(resolved_user)
            self.logger.debug(f"Added user directory to allowed paths: {resolved_user}")

        deduped = self._dedupe_existing_paths(roots)
        self.logger.debug(f"Image serving allowed from {len(deduped)} root directories: {[str(r) for r in deduped]}")
        return deduped

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

    def _validate_image_path(self, path: Path) -> tuple[bool, Optional[str]]:
        """Validate image path is within allowed roots.

        This method provides centralized path validation for all image file serving
        endpoints to prevent path traversal attacks.

        Args:
            path: Path to validate (can be relative or absolute)

        Returns:
            tuple: (is_valid, error_message)
                - is_valid: True if path is allowed, False otherwise
                - error_message: None if valid, error description if invalid

        Security Notes:
            - Resolves paths to absolute canonical form
            - Checks if resolved path is within allowed root directories
            - Returns 403-appropriate error messages for security violations
        """
        self.logger.debug(f"Validating image path: {path}")
        
        try:
            resolved = path.resolve(strict=True)
            self.logger.debug(f"Resolved path to: {resolved}")
        except FileNotFoundError:
            self.logger.warning(f"Image file not found during validation: {path}")
            return False, "Image file not found"
        except Exception as e:
            self.logger.error(f"Path resolution error for {path}: {e}")
            return False, "Invalid image path"

        allowed_roots = self._get_allowed_image_roots()
        if not allowed_roots:
            # No roots configured - this shouldn't happen in production
            # Log warning but allow for backward compatibility during transition
            self.logger.warning(
                "No allowed image roots configured - security validation bypassed. "
                "This should be fixed in production."
            )
            return True, None

        # Check if resolved path is within any allowed root
        is_allowed = any(
            resolved.is_relative_to(root) for root in allowed_roots
        )

        if not is_allowed:
            # Security violation - path outside allowed directories
            self.logger.warning(
                f"Security: Access denied to path outside allowed roots!\n"
                f"  Requested path: {path}\n"
                f"  Resolved path: {resolved}\n"
                f"  Allowed roots: {[str(r) for r in allowed_roots]}"
            )
            return False, "Access denied: image path outside allowed directories"

        self.logger.debug(f"Path validation successful: {resolved}")
        return True, None

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

        # Get actual server URL from ComfyUI
        try:
            from utils.comfyui_utils import get_comfyui_server_url
            server_url = get_comfyui_server_url()
        except Exception:
            server_url = "http://localhost:8188"

        banner_lines.extend(
            [
                "",
                f"  Visit {server_url}/prompt_manager/migration",
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

        # Prompt endpoints - delegated to PromptHandlers
        routes.post("/api/v1/prompts")(self.prompts_handlers.create_prompt)
        routes.get("/api/v1/prompts")(self.prompts_handlers.list_prompts)
        routes.get("/api/v1/prompts/{id}")(self.prompts_handlers.get_prompt)
        routes.put("/api/v1/prompts/{id}")(self.prompts_handlers.update_prompt)
        routes.delete("/api/v1/prompts/{id}")(self.prompts_handlers.delete_prompt)
        routes.post("/api/v1/prompts/search")(self.prompts_handlers.search_prompts)
        routes.get("/api/v1/prompts/recent")(self.system_handlers.get_recent_prompts)
        routes.get("/api/v1/prompts/popular")(self.system_handlers.get_popular_prompts)
        routes.get("/api/v1/prompts/categories")(self.system_handlers.get_prompt_categories)
        routes.get("/api/v1/prompts/{prompt_id}/images")(self.system_handlers.get_prompt_images)

        # Bulk operations - delegated to PromptHandlers
        routes.post("/api/v1/prompts/bulk")(self.prompts_handlers.bulk_create_prompts)
        routes.delete("/api/v1/prompts/bulk")(self.prompts_handlers.bulk_delete_prompts)

        # Tag endpoints - delegated to TagHandlers
        routes.get("/api/v1/tags")(self.tag_handlers.list_tags)
        routes.get("/api/v1/tags/search")(self.tag_handlers.search_tags)
        routes.get("/api/v1/tags/popular")(self.tag_handlers.get_popular_tags)
        routes.get("/api/v1/tags/stats")(self.tag_handlers.get_tag_stats)
        routes.get("/api/v1/tags/{id}")(self.tag_handlers.get_tag)
        routes.post("/api/v1/tags")(self.tag_handlers.create_tag)
        routes.post("/api/v1/tags/sync")(self.tag_handlers.sync_tags)
        routes.post("/api/v1/tags/update-counts")(self.tag_handlers.update_usage_counts)
        routes.post("/api/v1/tags/cleanup")(self.tag_handlers.cleanup_unused_tags)
        routes.delete("/api/v1/tags/{id}")(self.tag_handlers.delete_tag)

        # Gallery endpoints - delegated to GalleryHandlers
        routes.get("/api/v1/gallery/images")(self.gallery_handlers.list_gallery_images)
        routes.get("/api/v1/gallery/images/{id}")(self.gallery_handlers.get_gallery_image)
        routes.get("/api/v1/gallery/images/{id}/file")(self.gallery_handlers.get_gallery_image_file)
        routes.get("/api/v1/generated-images/{image_id}/metadata")(self.gallery_handlers.get_generated_image_metadata)
        routes.get("/api/v1/generated-images/{image_id}/file")(self.gallery_handlers.get_generated_image_file)
        routes.post("/api/v1/gallery/scan")(self.gallery_handlers.scan_for_images)
        routes.post("/api/scan")(self.gallery_handlers.scan_comfyui_images)  # Simple scan endpoint for frontend

        # Metadata endpoints - delegated to MetadataHandlers
        routes.get("/api/v1/metadata/{filename}")(self.metadata_handlers.get_image_metadata)
        routes.post("/api/v1/metadata/extract")(self.metadata_handlers.extract_metadata)
        routes.post("/prompt_manager/api/v1/metadata/extract")(self.metadata_handlers.extract_metadata)
        routes.post("/api/prompt_manager/api/v1/metadata/extract")(self.metadata_handlers.extract_metadata)

        # Migration endpoints - delegated to MigrationHandlers
        routes.get("/api/v1/migration/info")(self.migration_handlers.get_migration_info)
        routes.get("/api/v1/migration/status")(self.migration_handlers.get_migration_status)
        routes.get("/api/v1/migration/progress")(self.migration_handlers.get_migration_progress)
        routes.post("/api/v1/migration/start")(self.migration_handlers.start_migration)
        routes.post("/api/v1/migration/trigger")(self.migration_handlers.trigger_migration)

        # System endpoints - delegated to SystemHandlers
        routes.get("/api/v1/health")(self.system_handlers.health_check)
        routes.get("/api/v1/system/stats")(self.system_handlers.get_statistics)
        routes.get("/api/v1/stats/overview")(self.system_handlers.get_stats_overview)
        routes.get("/api/v1/stats/scheduler/status")(self.get_stats_scheduler_status)  # Keep in routes.py (stats scheduler)
        routes.get("/api/v1/stats/epic")(self.get_epic_stats)  # Keep in routes.py (legacy)
        routes.get("/api/prompt_manager/stats/epic")(self.get_epic_stats)  # Keep in routes.py (legacy)

        # Maintenance endpoints - only if service is available
        if self.maintenance_api:
            self.maintenance_api.add_routes(routes)
            self.logger.info("Maintenance API routes registered")
        routes.post("/api/v1/stats/scheduler/force")(self.force_stats_update)  # Keep in routes.py (stats scheduler)
        routes.post("/api/v1/system/vacuum")(self.system_handlers.vacuum_database)
        routes.post("/api/v1/system/backup")(self.system_handlers.backup_database)
        routes.post("/api/v1/system/database/verify")(self.system_handlers.verify_database_path)
        routes.post("/api/v1/system/database/apply")(self.system_handlers.apply_database_path)
        routes.post("/api/v1/system/database/migrate")(self.system_handlers.migrate_database_path)
        routes.get("/api/v1/system/categories")(self.system_handlers.get_categories)
        routes.get("/api/v1/system/settings")(self.system_handlers.get_system_settings)
        routes.put("/api/v1/system/settings")(self.update_system_settings)  # Keep in routes.py (uses _build_settings_payload)

        # Logs - delegated to LogsHandlers
        routes.get("/api/v1/logs")(self.logs_handlers.list_logs)
        routes.get("/api/v1/logs/{name}")(self.logs_handlers.get_log_file)
        routes.post("/api/v1/logs/clear")(self.logs_handlers.clear_logs)
        routes.post("/api/v1/logs/rotate")(self.logs_handlers.rotate_logs)
        routes.get("/api/v1/logs/download/{name}")(self.logs_handlers.download_log)

        # Thumbnail endpoints
        if self.thumbnail_api:
            # Thumbnail operations
            routes.post('/api/v1/thumbnails/scan')(self.thumbnail_api.scan_missing_thumbnails)
            routes.post('/api/v1/thumbnails/scan/all')(self.thumbnail_api.scan_all_thumbnails)
            routes.post('/api/v1/thumbnails/generate')(self.thumbnail_api.generate_thumbnails)
            routes.post('/api/v1/thumbnails/cancel')(self.thumbnail_api.cancel_generation)
            routes.get('/api/v1/thumbnails/status/{task_id}')(self.thumbnail_api.get_task_status)
            routes.post('/api/v1/thumbnails/rebuild')(self.thumbnail_api.rebuild_thumbnails)

            # V2 Reconciliation endpoints
            routes.post('/api/v1/thumbnails/comprehensive-scan')(self.thumbnail_api.comprehensive_scan)
            routes.post('/api/v1/thumbnails/rebuild-unified')(self.thumbnail_api.rebuild_unified)
            routes.post('/api/v1/thumbnails/rebuild-all-from-scratch')(self.thumbnail_api.rebuild_all_from_scratch)

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

        # Maintenance endpoints - delegated to MaintenanceHandlers
        routes.get("/api/prompt_manager/maintenance/stats")(self.maintenance_handlers.get_maintenance_stats)
        routes.post("/api/prompt_manager/maintenance/deduplicate")(self.maintenance_handlers.remove_duplicates)
        routes.post("/api/prompt_manager/maintenance/clean-orphans")(self.maintenance_handlers.clean_orphans)
        routes.post("/api/prompt_manager/maintenance/validate-paths")(self.maintenance_handlers.validate_paths)
        routes.post("/api/prompt_manager/maintenance/optimize")(self.maintenance_handlers.optimize_database)
        routes.post("/api/prompt_manager/maintenance/backup")(self.maintenance_handlers.create_backup)
        routes.post("/api/prompt_manager/maintenance/remove-missing")(self.maintenance_handlers.remove_missing_files)
        routes.post("/api/prompt_manager/maintenance/update-file-metadata")(self.maintenance_handlers.refresh_file_metadata)
        routes.post("/api/prompt_manager/maintenance/fix-broken-links")(self.maintenance_handlers.fix_broken_links)
        routes.post("/api/prompt_manager/maintenance/check-integrity")(self.maintenance_handlers.check_integrity)
        routes.post("/api/prompt_manager/maintenance/reindex")(self.maintenance_handlers.reindex_database)
        routes.post("/api/prompt_manager/maintenance/export")(self.maintenance_handlers.export_backup)
        routes.post("/api/prompt_manager/maintenance/tag-missing-images")(self.maintenance_handlers.tag_missing_images)
        routes.post("/api/prompt_manager/maintenance/calculate-epic-stats")(self.maintenance_handlers.calculate_epic_stats)
        routes.post("/api/prompt_manager/maintenance/calculate-word-cloud")(self.maintenance_handlers.calculate_word_cloud)

        # Settings endpoints
        routes.get("/api/prompt_manager/settings")(self.get_all_settings)
        routes.post("/api/prompt_manager/settings")(self.update_all_settings)
        routes.get("/api/prompt_manager/settings/community")(self.get_community_settings)
        routes.post("/api/prompt_manager/settings/generate_uuid")(self.generate_uuid)
        routes.get("/api/prompt_manager/settings/export")(self.export_settings)
        routes.post("/api/prompt_manager/settings/import")(self.import_settings)
        routes.get("/api/prompt_manager/settings/{key}")(self.get_setting)
        routes.post("/api/prompt_manager/settings/{key}")(self.set_setting)

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
                self.logger.warning(f"[CREATE] No prompt text found in data: {data}")
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

            order_field = request.query.get("order_by", "id")  # Use ID as default since created_at might be NULL
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
            # Handle search separately as it needs special processing
            search_term = request.query.get("search")

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

            # Use search method if search term provided, otherwise regular list
            if search_term:
                try:
                    search_columns = ["positive_prompt", "negative_prompt", "tags", "category", "notes"]

                    # Search with pagination
                    raw_records = self.prompt_repo.search(
                        search_term,
                        columns=search_columns,
                        limit=limit,
                        offset=offset
                    )

                    # Get total count for search results using the new search_count method
                    search_total = self.prompt_repo.search_count(
                        search_term,
                        columns=search_columns
                    )
                except Exception as e:
                    self.logger.error(f"Search error: {e}")
                    # Fall back to empty results on search error
                    raw_records = []
                    search_total = 0
            else:
                raw_records = list(self.prompt_repo.list(
                    limit=limit,
                    offset=offset,
                    order_by=order_clause,
                    **filter_kwargs,
                ))

            prompts = [
                self._format_prompt(
                    record,
                    include_images=include_images,
                    image_limit=image_limit,
                )
                for record in raw_records
            ]

            # Use appropriate total based on whether searching or not
            if search_term:
                total = search_total
            else:
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

    async def get_generated_image_metadata(self, request: web.Request) -> web.Response:
        """Return extracted metadata for a generated image.

        GET /api/v1/generated-images/{id}/metadata
        """

        if not getattr(self, "generated_image_repo", None):
            return web.json_response({"error": "Generated image repository unavailable"}, status=500)

        try:
            image_id = int(request.match_info["image_id"])
        except (KeyError, ValueError):
            return web.json_response({"error": "Invalid image id"}, status=400)

        record = self.generated_image_repo.read(image_id)
        if not record:
            return web.json_response({"error": "Image not found"}, status=404)

        metadata_raw = record.get("metadata")
        prompt_metadata_raw = record.get("prompt_metadata")
        workflow_raw = record.get("workflow_data") or record.get("workflow")

        def _parse_json(value: Any) -> Optional[Dict[str, Any]]:
            if value is None:
                return None
            if isinstance(value, dict):
                return value
            if isinstance(value, str) and value.strip():
                try:
                    return json.loads(value)
                except json.JSONDecodeError:
                    self.logger.debug("Generated image %s metadata field was not valid JSON", image_id)
                    return None
            return None

        metadata = _parse_json(metadata_raw)
        prompt_metadata = _parse_json(prompt_metadata_raw)
        workflow = _parse_json(workflow_raw)

        # Prefer prompt_metadata when general metadata missing
        if not metadata and prompt_metadata:
            metadata = prompt_metadata

        # Fallback to extracting directly from file when needed
        if not metadata:
            file_path = record.get("file_path") or record.get("image_path")
            if file_path and Path(file_path).expanduser().exists():
                try:
                    metadata = self.metadata_extractor.extract_from_file(file_path)
                except Exception as exc:  # pragma: no cover - defensive logging
                    self.logger.debug("Failed to extract metadata for image %s: %s", image_id, exc)

        metadata = self._sanitize_for_json(metadata) if metadata else {}
        prompt_metadata = self._sanitize_for_json(prompt_metadata) if prompt_metadata else None
        workflow = self._sanitize_for_json(workflow) if workflow else None

        response_payload: Dict[str, Any] = {
            "id": image_id,
            "prompt_id": record.get("prompt_id"),
            "metadata": metadata,
            "prompt_metadata": prompt_metadata,
            "workflow": workflow,
            "file_path": record.get("file_path") or record.get("image_path"),
            "positive_prompt": record.get("prompt_text") or metadata.get("positive_prompt") if isinstance(metadata, dict) else None,
            "negative_prompt": record.get("negative_prompt") or metadata.get("negative_prompt") if isinstance(metadata, dict) else None,
            "model": record.get("checkpoint") or metadata.get("model") if isinstance(metadata, dict) else None,
            "sampler": record.get("sampler") or metadata.get("sampler") if isinstance(metadata, dict) else None,
            "steps": record.get("steps") or metadata.get("steps") if isinstance(metadata, dict) else None,
            "cfg_scale": record.get("cfg_scale") or metadata.get("cfg_scale") if isinstance(metadata, dict) else None,
            "seed": record.get("seed") or metadata.get("seed") if isinstance(metadata, dict) else None,
            "comfy_parsed": metadata.get("comfy_parsed") if isinstance(metadata, dict) else None,
        }

        # Ensure nested structures are JSON-safe
        response_payload = self._sanitize_for_json(response_payload)

        return web.json_response({"success": True, "data": response_payload})

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

                # Security validation for thumbnail path
                is_valid, error_msg = self._validate_image_path(thumb_path)
                if not is_valid:
                    return web.json_response({"error": error_msg}, status=403)

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

        # Security validation for original image path
        is_valid, error_msg = self._validate_image_path(path)
        if not is_valid:
            return web.json_response({"error": error_msg}, status=403)

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
            from ..services.image_scanner import ImageScanner

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

        # Use HybridStatsService for fast stats access
        if self.stats_service:
            try:
                snapshot = self.stats_service.get_overview()

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

        # No stats service available
        return web.json_response(
            {"success": False, "error": "Statistics service unavailable"},
            status=503,
        )

    async def vacuum_database(self, request: web.Request) -> web.Response:
        """Vacuum database to optimize storage.

        POST /api/v1/system/vacuum
        """
        try:
            from ..database import PromptDatabase
            db = PromptDatabase(self.db_path)
            db.vacuum_database()

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
            candidate = Path(info["resolved"])
            if not candidate.exists():
                return web.json_response({"ok": False, "error": "Database not found at specified path"}, status=400)

            resolved = get_file_system().set_custom_database_path(str(candidate))
            config.database.path = str(resolved)
            self._refresh_repositories()
            return web.json_response({"ok": True, "data": {"path": str(resolved), "custom": True}})
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
            else:
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
        if not name or ".." in name or "/" in name or "\\" in name:
            return web.json_response({"success": False, "error": "Invalid log name"}, status=400)

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
            from ..services.maintenance_service import MaintenanceService
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
            from ..services.maintenance_service import MaintenanceService
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
            from ..services.maintenance_service import MaintenanceService
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
            from ..services.maintenance_service import MaintenanceService
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
            from ..services.maintenance_service import MaintenanceService
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
            from ..services.maintenance_service import MaintenanceService
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
            from ..services.maintenance_service import MaintenanceService
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
            from ..services.maintenance_service import MaintenanceService
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
            from ..services.maintenance_service import MaintenanceService

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
            from ..services.maintenance_service import MaintenanceService
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
            from ..services.maintenance_service import MaintenanceService
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

    async def tag_missing_images_maintenance(self, request: web.Request) -> web.Response:
        """Tag images with missing files.

        POST /api/prompt_manager/maintenance/tag-missing-images
        """
        try:
            from .services.missing_images_tagger import MissingImagesTagger
            tagger = MissingImagesTagger(self.db_path)

            # Get the action from request
            data = await request.json() if request.body_exists else {}
            action = data.get('action', 'tag')  # 'tag', 'untag', or 'summary'

            if action == 'summary':
                result = tagger.get_missing_images_summary()
            elif action == 'untag':
                stats = tagger.remove_missing_tag()
                result = {
                    "success": True,
                    "message": f"Removed 'missing' tag from {stats['tag_removed']} images that were found",
                    "stats": stats
                }
            else:  # Default to 'tag'
                stats = tagger.tag_missing_images()
                result = {
                    "success": True,
                    "message": f"Tagged {stats['tagged']} missing images out of {stats['total_missing']} total missing",
                    "stats": stats
                }

            # Broadcast realtime update
            if hasattr(self, 'sse'):
                await self.realtime.send_toast(result.get('message', 'Missing images processed'), 'success')

            return web.json_response(result)
        except Exception as e:
            self.logger.error(f"Error tagging missing images: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def export_backup_maintenance(self, request: web.Request) -> web.Response:
        """Export database backup.

        POST /api/prompt_manager/maintenance/export
        """
        try:
            from ..services.maintenance_service import MaintenanceService
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

    async def get_epic_stats(self, request: web.Request) -> web.Response:
        """Get epic statistics from the database.
        GET /api/v1/stats/epic
        """
        try:
            conn = DatabaseConnection.get_connection(self.db_path)
            cursor = conn.cursor()

            # Get list of ALL available tables (not just stats tables)
            cursor.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table'
            """)
            available_tables = {row[0] for row in cursor.fetchall()}

            # Initialize all data as None
            hero_stats = None
            generation_analytics = None
            time_patterns = []
            model_usage = []
            resolutions = []
            rating_trends = []

            # Get hero stats if table exists
            if 'stats_hero_stats' in available_tables:
                cursor.execute("""
                    SELECT * FROM stats_hero_stats
                    ORDER BY calculated_at DESC
                    LIMIT 1
                """)
                hero_stats = cursor.fetchone()

            # Get generation analytics if table exists
            if 'stats_generation_analytics' in available_tables:
                cursor.execute("""
                    SELECT * FROM stats_generation_analytics
                    ORDER BY calculated_at DESC
                    LIMIT 1
                """)
                generation_analytics = cursor.fetchone()

            # Get time patterns if table exists
            if 'stats_hourly_activity' in available_tables:
                cursor.execute("""
                    SELECT hour, prompt_count + image_count as total_generations, 0 as avg_quality_score
                    FROM stats_hourly_activity
                    ORDER BY hour
                """)
                time_patterns = cursor.fetchall()
            elif 'stats_time_patterns_detailed' in available_tables:
                cursor.execute("""
                    SELECT hour_of_day, total_generations, avg_quality_score
                    FROM stats_time_patterns_detailed
                    ORDER BY hour_of_day
                """)
                time_patterns = cursor.fetchall()
            elif 'stats_time_patterns' in available_tables:
                # Try the original table structure
                cursor.execute("""
                    SELECT
                        CAST(SUBSTR(pattern_value, 1, 2) AS INTEGER) as hour,
                        0 as generations,
                        0 as avg_quality
                    FROM stats_time_patterns
                    WHERE pattern_type = 'hourly'
                    LIMIT 24
                """)
                time_patterns = cursor.fetchall()

            # Get model usage if table exists
            if 'stats_model_usage' in available_tables:
                # Check which columns exist
                cursor.execute("PRAGMA table_info(stats_model_usage)")
                columns = {row[1] for row in cursor.fetchall()}

                if 'generation_count' in columns and 'percentage' in columns:
                    cursor.execute("""
                        SELECT model_name, generation_count, percentage
                        FROM stats_model_usage
                        ORDER BY generation_count DESC
                    """)
                elif 'usage_count' in columns:
                    cursor.execute("""
                        SELECT model_name, usage_count, 0
                        FROM stats_model_usage
                        ORDER BY usage_count DESC
                    """)
                else:
                    cursor.execute("""
                        SELECT model_name, 0, 0
                        FROM stats_model_usage
                        LIMIT 10
                    """)
                model_usage = cursor.fetchall()

            # Get resolution distribution if table exists
            if 'stats_resolution_distribution' in available_tables:
                cursor.execute("""
                    SELECT width, height, count
                    FROM stats_resolution_distribution
                    ORDER BY count DESC
                    LIMIT 5
                """)
                resolutions = cursor.fetchall()

            # Get rating trends if table exists
            if 'stats_rating_trends' in available_tables:
                cursor.execute("""
                    SELECT period_date, avg_rating, total_ratings
                    FROM stats_rating_trends
                    WHERE period_type = 'daily'
                    ORDER BY period_date DESC
                    LIMIT 30
                """)
                rating_trends = cursor.fetchall()

            # Fallback: if no stats tables exist, try to get basic counts from source tables
            if not hero_stats and 'generated_images' in available_tables:
                try:
                    # Check what columns exist in generated_images
                    cursor.execute("PRAGMA table_info(generated_images)")
                    columns = {row[1] for row in cursor.fetchall()}

                    # Build query based on available columns
                    if 'rating' in columns:
                        cursor.execute("""
                            SELECT
                                COUNT(*) as total_images,
                                COUNT(DISTINCT prompt_id) as unique_prompts,
                                COUNT(CASE WHEN rating > 0 THEN 1 END) as rated_count,
                                AVG(CASE WHEN rating > 0 THEN rating END) as avg_rating,
                                COUNT(CASE WHEN rating = 5 THEN 1 END) as five_star_count
                            FROM generated_images
                        """)
                    else:
                        # No rating column, just get basic counts
                        cursor.execute("""
                            SELECT
                                COUNT(*) as total_images,
                                COUNT(DISTINCT prompt_id) as unique_prompts,
                                0 as rated_count,
                                0 as avg_rating,
                                0 as five_star_count
                            FROM generated_images
                        """)

                    basic_stats = cursor.fetchone()
                    if basic_stats:
                        # Create a fake hero_stats tuple with the basic data
                        hero_stats = (1, basic_stats[0], basic_stats[1], basic_stats[2],
                                      basic_stats[3] or 0, basic_stats[4], 0,
                                      basic_stats[0] / max(1, basic_stats[1]), 0, None)
                except Exception as e:
                    self.logger.warning(f"Failed to get fallback stats from generated_images: {e}")
                    # Continue without fallback data

            conn.close()

            # Format the response
            response_data = {
                "success": True,
                "data": {
                    "generation_analytics": {
                        "total_generations": generation_analytics[1] if generation_analytics else 0,
                        "unique_prompts": generation_analytics[2] if generation_analytics else 0,
                        "avg_per_day": generation_analytics[3] if generation_analytics else 0,
                        "peak_day_count": generation_analytics[4] if generation_analytics else 0,
                        "peak_day": generation_analytics[5] if generation_analytics else None,
                        "total_generation_time": generation_analytics[6] if generation_analytics else 0,
                        "avg_generation_time": generation_analytics[7] if generation_analytics else 0,
                    } if generation_analytics else {},
                    "hero_stats": {
                        "total_images": hero_stats[1] if hero_stats else 0,
                        "total_prompts": hero_stats[2] if hero_stats else 0,
                        "rated_count": hero_stats[3] if hero_stats else 0,
                        "avg_rating": hero_stats[4] if hero_stats else 0,
                        "five_star_count": hero_stats[5] if hero_stats else 0,
                        "total_collections": hero_stats[6] if hero_stats else 0,
                        "images_per_prompt": hero_stats[7] if hero_stats else 0,
                        "generation_streak": hero_stats[8] if hero_stats else 0,
                    } if hero_stats else {},
                    "time_patterns": [
                        {
                            "hour": row[0],
                            "generations": row[1],
                            "avg_quality": row[2]
                        } for row in time_patterns
                    ],
                    "model_usage": [
                        {
                            "model": row[0],
                            "count": row[1],
                            "percentage": row[2]
                        } for row in model_usage
                    ],
                    "quality_metrics": {
                        "avg_quality_score": 0,  # Not in metadata
                        "high_quality_count": 0,  # Not in metadata
                        "low_quality_count": 0,  # Not tracked in current schema
                        "quality_trend": "stable",  # Default value
                        "top_resolutions": [
                            {"width": r[0], "height": r[1], "count": r[2]}
                            for r in resolutions
                        ] if resolutions else []
                    },
                    "rating_trends": [
                        {
                            "date": row[0],
                            "avg_rating": row[1],
                            "total_ratings": row[2]
                        } for row in rating_trends
                    ],
                    "calculated_at": hero_stats[9] if hero_stats else generation_analytics[8] if generation_analytics else None
                }
            }

            return web.json_response(response_data)

        except Exception as e:
            self.logger.error(f"Error getting epic stats: {e}")
            import traceback
            traceback.print_exc()
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

    # ==================== Additional Maintenance Endpoints ====================

    async def calculate_epic_stats_maintenance(self, request: web.Request) -> web.Response:
        """Calculate epic statistics.
        POST /api/prompt_manager/maintenance/calculate-epic-stats
        """
        try:
            from .services.epic_stats_calculator import EpicStatsCalculator
            calculator = EpicStatsCalculator(self.db_path)

            # This could be a long-running operation
            stats = calculator.calculate_all_stats(progress_callback=None)
            
            return web.json_response({
                "success": True,
                "message": "Epic stats calculated successfully",
                "stats": stats
            })
        except Exception as e:
            self.logger.error(f"Error calculating epic stats: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def calculate_word_cloud_maintenance(self, request: web.Request) -> web.Response:
        """Calculate word cloud data.
        POST /api/prompt_manager/maintenance/calculate-word-cloud
        """
        try:
            from .services.word_cloud_service import WordCloudService
            service = WordCloudService(self.db_path)

            # Calculate word frequencies
            frequencies = service.calculate_word_frequencies(limit=100)
            metadata = service.get_metadata()

            return web.json_response({
                "success": True,
                "message": f"Word cloud data generated with {len(frequencies)} unique words",
                "data": {
                    "frequencies": frequencies,
                    "metadata": metadata
                }
            })
        except Exception as e:
            self.logger.error(f"Error calculating word cloud: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    # ==================== Settings Endpoints ====================

    async def get_setting(self, request: web.Request) -> web.Response:
        """Get a specific setting value.
        GET /api/prompt_manager/settings/{key}
        """
        try:
            key = request.match_info['key']
            value = self.settings_service.get(key)

            return web.json_response({
                'success': True,
                'key': key,
                'value': value
            })
        except Exception as e:
            self.logger.error(f"Error getting setting {key}: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def set_setting(self, request: web.Request) -> web.Response:
        """Set a setting value.
        POST /api/prompt_manager/settings/{key}
        Body: { value: any, category?: string, description?: string }
        """
        try:
            key = request.match_info['key']
            data = await request.json()

            value = data.get('value')
            category = data.get('category', 'general')
            description = data.get('description')

            success = self.settings_service.set(key, value, category, description)

            return web.json_response({
                'success': success,
                'key': key,
                'value': value
            })
        except Exception as e:
            self.logger.error(f"Error setting {key}: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def get_all_settings(self, request: web.Request) -> web.Response:
        """Get all settings with metadata.
        GET /api/prompt_manager/settings
        """
        try:
            settings = self.settings_service.get_all_settings()

            return web.json_response({
                'success': True,
                'settings': settings
            })
        except Exception as e:
            self.logger.error(f"Error getting all settings: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def update_all_settings(self, request: web.Request) -> web.Response:
        """Update multiple settings at once.
        POST /api/prompt_manager/settings
        Body: { gallery: {...}, viewer: {...}, filmstrip: {...}, etc. }
        """
        try:
            settings_data = await request.json()
            updated_count = 0

            # Iterate through categories and update each setting
            for category, category_settings in settings_data.items():
                if isinstance(category_settings, dict):
                    for key, value in category_settings.items():
                        setting_key = f"{category}.{key}"
                        success = self.settings_service.set(setting_key, value, category)
                        if success:
                            updated_count += 1

            return web.json_response({
                'success': True,
                'updated': updated_count,
                'message': f'Updated {updated_count} settings'
            })
        except Exception as e:
            self.logger.error(f"Error updating all settings: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def get_community_settings(self, request: web.Request) -> web.Response:
        """Get all community settings.
        GET /api/prompt_manager/settings/community
        """
        try:
            settings = self.settings_service.get_category('community')

            # Mask the API key for security
            if 'civitai_api_key' in settings and settings['civitai_api_key']:
                settings['civitai_api_key'] = '***hidden***'

            return web.json_response({
                'success': True,
                'settings': settings
            })
        except Exception as e:
            self.logger.error(f"Error getting community settings: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def generate_uuid(self, request: web.Request) -> web.Response:
        """Generate a new PromptManager UUID.
        POST /api/prompt_manager/settings/generate_uuid
        """
        try:
            new_uuid = self.settings_service.generate_uuid()

            if new_uuid:
                return web.json_response({
                    'success': True,
                    'uuid': new_uuid
                })
            else:
                return web.json_response({
                    'success': False,
                    'error': 'Failed to generate UUID'
                }, status=500)
        except Exception as e:
            self.logger.error(f"Error generating UUID: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def export_settings(self, request: web.Request) -> web.Response:
        """Export all settings for backup.
        GET /api/prompt_manager/settings/export
        """
        try:
            export_data = self.settings_service.export_settings()

            return web.json_response({
                'success': True,
                'data': export_data
            })
        except Exception as e:
            self.logger.error(f"Error exporting settings: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def import_settings(self, request: web.Request) -> web.Response:
        """Import settings from backup.
        POST /api/prompt_manager/settings/import
        Body: { data: {category: {key: value}}, overwrite?: boolean }
        """
        try:
            data = await request.json()
            settings_data = data.get('data', {})
            overwrite = data.get('overwrite', False)

            count = self.settings_service.import_settings(settings_data, overwrite)

            return web.json_response({
                'success': True,
                'imported': count,
                'message': f'Imported {count} settings'
            })
        except Exception as e:
            self.logger.error(f"Error importing settings: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)