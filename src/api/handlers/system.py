"""System management API handlers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from src.api.routes import PromptManagerAPI


class SystemHandlers:
    """Handles system management API endpoints."""
    
    def __init__(self, api):
        """Initialize with API instance for access to repos/services.
        
        Args:
            api: PromptManagerAPI instance providing access to repositories and services
        """
        self.api = api
        self.prompt_repo = api.prompt_repo
        self.image_repo = api.image_repo
        self.generated_image_repo = getattr(api, 'generated_image_repo', None)
        self.logger = api.logger
        self.db_path = api.db_path
        self.stats_service = getattr(api, 'stats_service', None)
        self.incremental_stats = getattr(api, 'incremental_stats', None)
    
    async def health_check(self, request) -> web.Response:
        """GET /api/v1/system/health - Health check."""
        try:
            # Check database connection
            db_healthy = self.prompt_repo.health_check() if hasattr(self.prompt_repo, 'health_check') else True
            
            # Check disk space
            import shutil
            stat = shutil.disk_usage(Path(self.db_path).parent)
            disk_free_gb = stat.free / (1024**3)
            
            return web.json_response({
                'success': True,
                'status': 'healthy' if db_healthy else 'degraded',
                'database': 'connected' if db_healthy else 'error',
                'disk_free_gb': round(disk_free_gb, 2),
                'timestamp': datetime.now().isoformat()
            })
        except Exception as e:
            self.logger.error(f"Health check failed: {e}")
            return web.json_response({
                'success': False,
                'status': 'unhealthy',
                'error': str(e)
            }, status=500)
    
    async def get_statistics(self, request: web.Request) -> web.Response:
        """Get system statistics.

        GET /api/v1/system/stats
        """
        try:
            stats = self.prompt_repo.get_statistics()
            if self.generated_image_repo:
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
                {"success": False, "error": str(e)},
                status=500
            )

    async def get_stats_overview(self, request: web.Request) -> web.Response:
        """Return aggregated analytics snapshot for the dashboard.

        OPTIMIZED: Uses hybrid stats approach - instant stats from snapshot + fast analytics.
        Returns in <10ms instead of 2-3 minutes!
        
        GET /api/v1/system/stats/overview
        """
        from typing import Dict, Any

        # NEW: Use hybrid approach - instant basic stats + fast analytics
        try:
            from ...services.hybrid_stats_service import HybridStatsService
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
                from ...services.stats_cache_service import StatsCacheService
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
                {"success": False, "error": str(e)},
                status=500
            )

    async def backup_database(self, request: web.Request) -> web.Response:
        """Create database backup.

        POST /api/v1/system/backup
        Body: {path?: str}
        """
        try:
            data = await request.json()
        except Exception:
            data = {}
            
        # Generate backup path if not provided
        backup_path = data.get("path")
        if not backup_path:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = f"prompts_backup_{timestamp}.db"
        
        try:
            success = self.prompt_repo.backup(backup_path)
            
            if success:
                return web.json_response({
                    "success": True,
                    "path": backup_path,
                    "message": "Database backed up successfully"
                })
            else:
                return web.json_response(
                    {"success": False, "error": "Backup failed"},
                    status=500
                )
        except Exception as e:
            self.logger.error(f"Error backing up database: {e}")
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

    async def verify_database_path(self, request: web.Request) -> web.Response:
        """Verify a database path is valid.
        
        POST /api/v1/system/database/verify
        Body: {path: str}
        """
        try:
            payload = await request.json()
        except Exception:
            payload = {}

        raw_path = (payload or {}).get("path")
        if not raw_path:
            return web.json_response({"success": False, "error": "path is required"}, status=400)

        try:
            from ...utils.file_system import get_file_system
            info = get_file_system().verify_database_path(raw_path)
            return web.json_response({"success": True, "data": info})
        except Exception as exc:
            self.logger.error("Failed to verify database path %s: %s", raw_path, exc)
            return web.json_response({"success": False, "error": str(exc)}, status=500)

    async def apply_database_path(self, request: web.Request) -> web.Response:
        """Apply a new database path.
        
        POST /api/v1/system/database/apply
        Body: {path?: str}  # None to reset to default
        """
        try:
            payload = await request.json()
        except Exception:
            payload = {}

        from ...utils.file_system import get_file_system
        from ...config import config
        
        fs = get_file_system()
        raw_path = (payload or {}).get("path")

        try:
            if not raw_path:
                resolved = fs.set_custom_database_path(None)
                config.database.path = str(resolved)
                self.api._refresh_repositories()
                return web.json_response({"success": True, "data": {"path": str(resolved), "custom": False}})

            info = fs.verify_database_path(raw_path)
            candidate = Path(info["resolved"])
            if not candidate.exists():
                return web.json_response({"success": False, "error": "Database not found at specified path"}, status=400)

            resolved = fs.set_custom_database_path(str(candidate))
            config.database.path = str(resolved)
            self.api._refresh_repositories()
            return web.json_response({"success": True, "data": {"path": str(resolved), "custom": True}})
        except Exception as exc:
            self.logger.error("Failed to apply database path %s: %s", raw_path, exc)
            return web.json_response({"success": False, "error": str(exc)}, status=500)

    async def migrate_database_path(self, request: web.Request) -> web.Response:
        """Move database to a new location.
        
        POST /api/v1/system/database/migrate
        Body: {path: str, mode?: "move" | "copy"}
        """
        try:
            payload = await request.json()
        except Exception:
            payload = {}

        raw_path = (payload or {}).get("path")
        if not raw_path:
            return web.json_response({"success": False, "error": "path is required"}, status=400)

        mode = (payload or {}).get("mode", "move")
        copy = mode == "copy"

        from ...utils.file_system import get_file_system
        from ...config import config
        
        fs = get_file_system()
        try:
            result = fs.move_database_file(raw_path, copy=copy)
            config.database.path = result.get("new_path", str(fs.get_database_path()))
            self.api._refresh_repositories()
            return web.json_response({"success": True, "data": result})
        except Exception as exc:
            self.logger.error("Failed to migrate database to %s: %s", raw_path, exc)
            return web.json_response({"success": False, "error": str(exc)}, status=500)

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
                {"success": False, "error": str(e)},
                status=500
            )

    async def get_prompt_categories(self, request: web.Request) -> web.Response:
        """Alias for get_categories.
        
        GET /api/v1/prompts/categories
        """
        return await self.get_categories(request)

    async def get_recent_prompts(self, request: web.Request) -> web.Response:
        """Get recent prompts.
        
        GET /api/v1/system/prompts/recent?limit=10&include_images=1&image_limit=4
        """
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
            self.api._format_prompt(p, include_images=include_images, image_limit=image_limit)
            for p in prompts
        ]
        return web.json_response({"success": True, "data": prompts})

    async def get_popular_prompts(self, request: web.Request) -> web.Response:
        """Get popular prompts by usage count.
        
        GET /api/v1/system/prompts/popular?limit=10&include_images=1&image_limit=4
        """
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
            self.api._format_prompt(p, include_images=include_images, image_limit=image_limit)
            for p in prompts
        ]
        return web.json_response({"success": True, "data": prompts})

    async def get_prompt_images(self, request: web.Request) -> web.Response:
        """Get images for a specific prompt.
        
        GET /api/v1/system/prompts/{prompt_id}/images?limit=12&order=desc
        """
        if not self.generated_image_repo:
            return web.json_response({"success": True, "data": [], "count": 0})

        try:
            prompt_id = int(request.match_info["prompt_id"])
        except (KeyError, ValueError):
            return web.json_response({"success": False, "error": "Invalid prompt id"}, status=400)

        try:
            limit = int(request.query.get("limit", 12))
        except (TypeError, ValueError):
            return web.json_response({"success": False, "error": "Invalid limit"}, status=400)

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
            return web.json_response({"success": False, "error": str(exc)}, status=500)

        payload = [self.api._format_generated_image(img) for img in images if img]
        return web.json_response({"success": True, "data": payload, "count": total})

    async def get_system_settings(self, request: web.Request) -> web.Response:
        """Return UI and integration settings.
        
        GET /api/v1/system/settings
        """
        payload = self.api._build_settings_payload()
        return web.json_response({"success": True, "data": payload})