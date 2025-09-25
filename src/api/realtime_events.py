"""Realtime event helpers that piggyback on ComfyUI's WebSocket."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:  # pragma: no cover - compatibility with packaged installs
    from promptmanager.loggers import get_logger  # type: ignore
except ImportError:  # pragma: no cover
    from loggers import get_logger  # type: ignore


class RealtimeEvents:
    """Bridges PromptManager events onto the ComfyUI WebSocket."""

    def __init__(self) -> None:
        self.logger = get_logger("promptmanager.api.realtime")

    # Public API ---------------------------------------------------------

    async def notify_image_added(self, image_data: Dict[str, Any], *, client_id: Optional[str] = None) -> None:
        self._emit("prompt_manager.gallery.image_added", image_data, client_id)

    async def notify_image_deleted(self, image_id: int, *, client_id: Optional[str] = None) -> None:
        self._emit("prompt_manager.gallery.image_deleted", {"id": image_id}, client_id)

    async def notify_image_updated(self, image_data: Dict[str, Any], *, client_id: Optional[str] = None) -> None:
        self._emit("prompt_manager.gallery.image_updated", image_data, client_id)

    async def notify_gallery_refresh(self, *, client_id: Optional[str] = None) -> None:
        self._emit(
            "prompt_manager.gallery.refresh",
            {"timestamp": datetime.now(timezone.utc).isoformat()},
            client_id,
        )

    async def send_notification(
        self,
        notification_type: str,
        title: str,
        message: str,
        *,
        client_id: Optional[str] = None,
        **extra: Any,
    ) -> None:
        payload = {
            "type": notification_type,
            "title": title,
            "message": message,
            **extra,
        }
        self._emit("prompt_manager.notifications.notification", payload, client_id)

    async def send_toast(
        self,
        message: str,
        toast_type: str = "info",
        duration: int = 5000,
        *,
        client_id: Optional[str] = None,
    ) -> None:
        payload = {
            "message": message,
            "type": toast_type,
            "duration": duration,
        }
        self._emit("prompt_manager.notifications.toast", payload, client_id)

    async def send_progress(
        self,
        operation: str,
        progress: int,
        message: str = "",
        *,
        client_id: Optional[str] = None,
        **extra: Any,
    ) -> None:
        payload = {
            "operation": operation,
            "progress": progress,
            "message": message,
            **extra,
        }
        self._emit("prompt_manager.notifications.progress", payload, client_id)

    async def notify_prompt_created(
        self, prompt_data: Dict[str, Any], *, client_id: Optional[str] = None
    ) -> None:
        self._emit("prompt_manager.prompts.created", prompt_data, client_id)

    async def notify_prompt_updated(
        self, prompt_data: Dict[str, Any], *, client_id: Optional[str] = None
    ) -> None:
        self._emit("prompt_manager.prompts.updated", prompt_data, client_id)

    async def notify_prompt_deleted(
        self, prompt_id: int, *, client_id: Optional[str] = None
    ) -> None:
        self._emit("prompt_manager.prompts.deleted", {"id": prompt_id}, client_id)

    async def update_system_status(
        self,
        status: str,
        details: Optional[Dict[str, Any]] = None,
        *,
        client_id: Optional[str] = None,
    ) -> None:
        payload: Dict[str, Any] = {"status": status}
        if details:
            payload.update(details)
        self._emit("prompt_manager.system.status", payload, client_id)

    async def set_maintenance_mode(
        self,
        enabled: bool,
        message: str = "",
        estimated_time: Optional[int] = None,
        *,
        client_id: Optional[str] = None,
    ) -> None:
        payload: Dict[str, Any] = {
            "enabled": enabled,
            "message": message,
            "estimated_time": estimated_time,
        }
        self._emit("prompt_manager.system.maintenance", payload, client_id)

    # Internal helpers ---------------------------------------------------

    def _emit(self, event: str, payload: Dict[str, Any], client_id: Optional[str]) -> None:
        from server import PromptServer  # Local import to avoid startup cycles

        if "timestamp" not in payload:
            payload = {**payload, "timestamp": datetime.now(timezone.utc).isoformat()}

        server = getattr(PromptServer, "instance", None)
        if not server:  # pragma: no cover - happens in unit tests without ComfyUI
            self.logger.debug("PromptServer instance not available; dropping event %s", event)
            return

        try:
            server.send_sync(event, payload, client_id)
        except Exception as exc:  # pragma: no cover - network failures
            self.logger.error("Failed to emit realtime event %s: %s", event, exc)
