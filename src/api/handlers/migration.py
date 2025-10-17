"""Database migration API handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from ..routes import PromptManagerAPI


class MigrationHandlers:
    """Handles all database migration endpoints."""

    def __init__(self, api: PromptManagerAPI):
        """Initialize with API instance for access to repos/services.

        Args:
            api: PromptManagerAPI instance providing access to repositories and services
        """
        self.api = api
        self.migration_service = api.migration_service
        self.logger = api.logger

    async def get_migration_info(self, request: web.Request) -> web.Response:
        """Provide migration requirement details for the UI layer.

        GET /api/v1/migration/info
        """
        try:
            info = self.migration_service.get_migration_info()
            return web.json_response({"success": True, "data": info})
        except Exception as exc:
            self.logger.error("Failed to fetch migration info: %s", exc)
            return web.json_response({"success": False, "error": str(exc)}, status=500)

    async def get_migration_status(self, request: web.Request) -> web.Response:
        """Expose only the migration status enum value.

        GET /api/v1/migration/status
        """
        try:
            info = self.migration_service.get_migration_info()
            return web.json_response({"success": True, "data": info.get("status")})
        except Exception as exc:
            self.logger.error("Failed to fetch migration status: %s", exc)
            return web.json_response({"success": False, "error": str(exc)}, status=500)

    async def get_migration_progress(self, request: web.Request) -> web.Response:
        """Return the latest migration progress snapshot.

        GET /api/v1/migration/progress
        """
        try:
            snapshot = self.migration_service.get_progress()
            return web.json_response({"success": True, "data": snapshot})
        except Exception as exc:
            self.logger.error("Failed to fetch migration progress: %s", exc)
            return web.json_response({"success": False, "error": str(exc)}, status=500)

    async def start_migration(self, request: web.Request) -> web.Response:
        """Kick off a migration or fresh-start action based on payload.

        POST /api/v1/migration/start
        Body: {action?: "migrate" | "fresh_start"}
        """
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
        """Convenience endpoint that always triggers a standard migration run.

        POST /api/v1/migration/trigger
        """
        try:
            result = self.migration_service.start_migration("migrate")
            return web.json_response({"success": True, "data": result})
        except Exception as exc:
            self.logger.error("Migration trigger failed: %s", exc)
            return web.json_response({"success": False, "error": str(exc)}, status=500)
