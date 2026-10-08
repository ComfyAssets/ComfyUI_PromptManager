"""Logging API routes for PromptManager."""

import os

from aiohttp import web

# Keys PromptManagerLogger.update_config() understands. Anything else is
# rejected so a client cannot inject arbitrary entries into the logging config.
LOG_CONFIG_KEYS = frozenset(
    {
        "level",
        "max_file_size",
        "backup_count",
        "console_logging",
        "file_logging",
        "buffer_size",
    }
)
_LOG_INT_KEYS = frozenset({"max_file_size", "backup_count", "buffer_size"})
_LOG_BOOL_KEYS = frozenset({"console_logging", "file_logging"})
_LOG_LEVEL_NAMES = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


def _validate_log_config(data):
    """Validate a log-config payload against LOG_CONFIG_KEYS.

    Returns:
        (clean, error): ``clean`` is the normalised dict to apply, ``error``
        a message describing the first problem (``clean`` is then ``None``).
    """
    if not isinstance(data, dict):
        return None, "Request body must be a JSON object"

    unknown = sorted(key for key in data if key not in LOG_CONFIG_KEYS)
    if unknown:
        return None, (
            f"Unknown logging config keys: {', '.join(unknown)}. "
            f"Allowed: {', '.join(sorted(LOG_CONFIG_KEYS))}"
        )

    clean = {}
    for key, value in data.items():
        if key == "level":
            if not isinstance(value, str) or value.upper() not in _LOG_LEVEL_NAMES:
                return None, (
                    f"Invalid log level. Must be one of: {sorted(_LOG_LEVEL_NAMES)}"
                )
            clean[key] = value.upper()
        elif key in _LOG_INT_KEYS:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                return None, f"{key} must be a non-negative integer"
            clean[key] = value
        elif key in _LOG_BOOL_KEYS:
            if not isinstance(value, bool):
                return None, f"{key} must be a boolean"
            clean[key] = value
    return clean, None


class LoggingRoutesMixin:
    """Mixin providing logging-related API endpoints."""

    def _register_logging_routes(self, routes):
        @routes.get("/prompt_manager/logs")
        async def get_logs_route(request):
            return await self.get_logs(request)

        @routes.get("/prompt_manager/logs/files")
        async def get_log_files_route(request):
            return await self.get_log_files(request)

        @routes.get("/prompt_manager/logs/download/{filename}")
        async def download_log_route(request):
            return await self.download_log_file(request)

        @routes.post("/prompt_manager/logs/truncate")
        async def truncate_logs_route(request):
            return await self.truncate_logs(request)

        @routes.get("/prompt_manager/logs/config")
        async def get_log_config_route(request):
            return await self.get_log_config(request)

        @routes.post("/prompt_manager/logs/config")
        async def update_log_config_route(request):
            return await self.update_log_config(request)

        @routes.get("/prompt_manager/logs/stats")
        async def get_log_stats_route(request):
            return await self.get_log_stats(request)

    def _get_logger_manager(self):
        """Get the logger manager instance."""
        try:
            from ...utils.logging_config import get_logger_manager
        except ImportError:
            import sys

            current_dir = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
            sys.path.insert(0, current_dir)
            from utils.logging_config import get_logger_manager
        return get_logger_manager()

    async def get_logs(self, request):
        """Get recent log entries."""
        try:
            logger_manager = self._get_logger_manager()

            limit = int(request.query.get("limit", 100))
            level = request.query.get("level", None)

            if limit > 1000:
                limit = 1000
            elif limit < 1:
                limit = 1

            logs = logger_manager.get_recent_logs(limit=limit, level=level)

            return web.json_response(
                {
                    "success": True,
                    "logs": logs,
                    "count": len(logs),
                    "level_filter": level,
                    "limit": limit,
                }
            )

        except Exception as e:
            self.logger.error(f"Get logs error: {e}")
            return web.json_response(
                {"success": False, "error": self._public_error(e), "logs": []},
                status=500,
            )

    async def get_log_files(self, request):
        """Get information about available log files."""
        try:
            logger_manager = self._get_logger_manager()
            log_files = [
                {**entry, "path": self._public_path(entry.get("path"))}
                for entry in logger_manager.get_log_files()
            ]

            return web.json_response(
                {"success": True, "files": log_files, "count": len(log_files)}
            )

        except Exception as e:
            self.logger.error(f"Get log files error: {e}")
            return web.json_response(
                {"success": False, "error": self._public_error(e), "files": []},
                status=500,
            )

    async def download_log_file(self, request):
        """Download a specific log file."""
        try:
            filename = request.match_info["filename"]
            logger_manager = self._get_logger_manager()

            if not filename or ".." in filename or "/" in filename or "\\" in filename:
                return web.json_response(
                    {"success": False, "error": "Invalid filename"}, status=400
                )

            log_file_path = logger_manager.log_dir / filename

            if not log_file_path.exists():
                return web.json_response(
                    {"success": False, "error": "Log file not found"}, status=404
                )

            with open(log_file_path, "rb") as f:
                file_content = f.read()

            return web.Response(
                body=file_content,
                content_type="text/plain",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "Content-Length": str(len(file_content)),
                },
            )

        except Exception as e:
            self.logger.error(f"Download log file error: {e}")
            return web.json_response(
                {"success": False, "error": self._public_error(e)}, status=500
            )

    async def truncate_logs(self, request):
        """Truncate all log files."""
        try:
            logger_manager = self._get_logger_manager()
            results = logger_manager.truncate_logs()

            return web.json_response(
                {
                    "success": True,
                    "message": f"Truncated {len(results['truncated'])} log files",
                    "results": results,
                }
            )

        except Exception as e:
            self.logger.error(f"Truncate logs error: {e}")
            return web.json_response(
                {"success": False, "error": self._public_error(e)}, status=500
            )

    async def get_log_config(self, request):
        """Get current logging configuration."""
        try:
            logger_manager = self._get_logger_manager()
            config = logger_manager.get_config()

            return web.json_response({"success": True, "config": config})

        except Exception as e:
            self.logger.error(f"Get log config error: {e}")
            return web.json_response(
                {"success": False, "error": self._public_error(e)}, status=500
            )

    async def update_log_config(self, request):
        """Update logging configuration (whitelisted keys only)."""
        try:
            try:
                data = await request.json()
            except ValueError:
                return web.json_response(
                    {"success": False, "error": "Request body must be valid JSON"},
                    status=400,
                )

            clean, error = _validate_log_config(data)
            if error:
                return web.json_response({"success": False, "error": error}, status=400)

            logger_manager = self._get_logger_manager()
            logger_manager.update_config(clean)

            return web.json_response(
                {
                    "success": True,
                    "message": "Logging configuration updated",
                    "config": logger_manager.get_config(),
                }
            )

        except Exception as e:
            self.logger.error(f"Update log config error: {e}")
            return web.json_response(
                {"success": False, "error": "Failed to update logging configuration"},
                status=500,
            )

    async def get_log_stats(self, request):
        """Get logging statistics."""
        try:
            logger_manager = self._get_logger_manager()
            stats = dict(logger_manager.get_log_stats())
            stats["log_directory"] = self._public_path(stats.get("log_directory"))

            return web.json_response({"success": True, "stats": stats})

        except Exception as e:
            self.logger.error(f"Get log stats error: {e}")
            return web.json_response(
                {"success": False, "error": self._public_error(e)}, status=500
            )
