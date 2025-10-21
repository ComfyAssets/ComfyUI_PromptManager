"""Log file management API handlers."""

from __future__ import annotations

from collections import deque
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from aiohttp import web

from utils.logging import LogConfig

if TYPE_CHECKING:
    from ..routes import PromptManagerAPI


class LogsHandlers:
    """Handles all log file management endpoints."""

    def __init__(self, api: PromptManagerAPI):
        """Initialize with API instance for access to repos/services.

        Args:
            api: PromptManagerAPI instance providing access to repositories and services
        """
        self.api = api
        self.logger = api.logger
        self.logs_dir = getattr(api, 'logs_dir', None)
        self.log_file = getattr(api, 'log_file', None)

    async def list_logs(self, request: web.Request) -> web.Response:
        """Return available log files and the tail of the active selection.

        GET /api/v1/logs?file=<name>&tail=<lines>
        """
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
        """Return the tail of a log file.

        GET /api/v1/logs/{name}?tail=<lines>
        """
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
        """Remove all stored log files.

        POST /api/v1/logs/clear
        """
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
        """Trigger a manual log rotation.

        POST /api/v1/logs/rotate
        """
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
        """Stream the selected log file to the client.

        GET /api/v1/logs/download/{name}
        """
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

    # Helper methods
    def _parse_tail(self, value: Optional[str]) -> Optional[int]:
        """Parse tail parameter from query string."""
        if value is None:
            return 500
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 500
        return max(parsed, 0)

    def _collect_log_files(self) -> List[Dict[str, Any]]:
        """Collect all log files from logs directory."""
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
        """Determine which log file should be the active selection."""
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
        """Resolve log file path with security validation."""
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
        """Read the last N lines from a log file."""
        if tail in (None, 0):
            return path.read_text(encoding="utf-8", errors="replace")

        buffer: deque[str] = deque(maxlen=tail)
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                buffer.append(line)
        return "".join(buffer)
