"""Logging API routes for PromptManager."""

import os

from aiohttp import web


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
                {"success": False, "error": str(e), "logs": []}, status=500
            )

    async def get_log_files(self, request):
        """Get information about available log files."""
        try:
            logger_manager = self._get_logger_manager()
            log_files = logger_manager.get_log_files()

            return web.json_response(
                {"success": True, "files": log_files, "count": len(log_files)}
            )

        except Exception as e:
            self.logger.error(f"Get log files error: {e}")
            return web.json_response(
                {"success": False, "error": str(e), "files": []}, status=500
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
            return web.json_response({"success": False, "error": str(e)}, status=500)

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
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def get_log_config(self, request):
        """Get current logging configuration."""
        try:
            logger_manager = self._get_logger_manager()
            config = logger_manager.get_config()

            return web.json_response({"success": True, "config": config})

        except Exception as e:
            self.logger.error(f"Get log config error: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def update_log_config(self, request):
        """Update logging configuration."""
        try:
            data = await request.json()
            logger_manager = self._get_logger_manager()

            if "level" in data:
                valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
                if data["level"].upper() not in valid_levels:
                    return web.json_response(
                        {
                            "success": False,
                            "error": f"Invalid log level. Must be one of: {valid_levels}",
                        },
                        status=400,
                    )
                data["level"] = data["level"].upper()

            logger_manager.update_config(data)

            return web.json_response(
                {
                    "success": True,
                    "message": "Logging configuration updated",
                    "config": logger_manager.get_config(),
                }
            )

        except Exception as e:
            self.logger.error(f"Update log config error: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def get_log_stats(self, request):
        """Get logging statistics."""
        try:
            logger_manager = self._get_logger_manager()
            stats = logger_manager.get_log_stats()

            return web.json_response({"success": True, "stats": stats})

        except Exception as e:
            self.logger.error(f"Get log stats error: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)
