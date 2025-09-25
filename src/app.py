"""aiohttp application setup for PromptManager API.

This module creates and configures the aiohttp application that exposes
PromptManager's REST API and ancillary endpoints. The previous Flask-based
implementation has been replaced so that only aiohttp is used for serving
HTTP traffic.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import threading
from pathlib import Path
from typing import Optional

from aiohttp import web

from src.config import config
from src.api.routes import PromptManagerAPI
from src.api.websocket import ws_manager, WebSocketHandler

try:  # pragma: no cover - environment-specific import path
    from promptmanager.loggers import get_logger  # type: ignore
except Exception:  # pragma: no cover
    from loggers import get_logger  # type: ignore

# Optional tracker hook that ComfyUI can populate at runtime
GLOBAL_PROMPT_TRACKER = None

logger = get_logger("promptmanager.app")


def _load_database_module():
    """Safely import the standalone database module."""
    module_name = "promptmanager_src_database"
    if module_name in sys.modules:
        return sys.modules[module_name]

    module_path = Path(__file__).resolve().parent / "database.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader  # For mypy - loader is present
    spec.loader.exec_module(module)
    sys.modules[module_name] = module
    return module


database_module = _load_database_module()


def _attach_core_routes(app: web.Application, db: Database) -> None:
    """Register health and tracking helper routes."""

    async def health(_: web.Request) -> web.Response:
        return web.json_response(
            {
                "status": "healthy",
                "version": "2.0.0",
                "database": db.get_stats(),
            }
        )

    async def tracking_metrics(_: web.Request) -> web.Response:
        tracker = globals().get("GLOBAL_PROMPT_TRACKER")
        if tracker is None:
            return web.json_response({"available": False, "metrics": None, "active": 0})

        metrics = tracker.get_metrics()
        return web.json_response(
            {
                "available": True,
                "metrics": metrics,
                "active": metrics.get("active_prompts", 0),
            }
        )

    async def tracking_reset(_: web.Request) -> web.Response:
        tracker = globals().get("GLOBAL_PROMPT_TRACKER")
        if tracker is None:
            return web.json_response({"ok": False, "error": "tracker not available"}, status=404)

        tracker.reset_metrics()
        return web.json_response({"ok": True})

    app.router.add_get("/health", health)
    app.router.add_get("/api/tracking/metrics", tracking_metrics)
    app.router.add_post("/api/tracking/reset", tracking_reset)


def create_app(loop: Optional[asyncio.AbstractEventLoop] = None) -> web.Application:
    """Create and configure the aiohttp application."""
    if loop is not None:
        asyncio.set_event_loop(loop)

    # Ensure the database exists before wiring the API
    db = database_module.Database(config.database.path)
    db.initialize()

    app = web.Application()

    # Register REST API routes via the consolidated aiohttp implementation
    prompt_api = PromptManagerAPI(config.database.path)
    routes = web.RouteTableDef()
    prompt_api.add_routes(routes)
    app.add_routes(routes)

    _attach_core_routes(app, db)

    # Store references for other systems (e.g. ComfyUI runtime hooks)
    app["prompt_api"] = prompt_api
    app["database"] = db

    return app


def _run_websocket_server() -> None:
    """Run the WebSocket server in a dedicated event loop thread."""
    import websockets

    async def websocket_handler(websocket, path):
        handler = WebSocketHandler(ws_manager)
        await handler.handle_connection(websocket, path)

    async def start_server():
        await ws_manager.start()
        async with websockets.serve(
            websocket_handler,
            config.websocket.host,
            config.websocket.port,
        ):
            logger.info(
                "WebSocket server listening on %s:%s",
                config.websocket.host,
                config.websocket.port,
            )
            await asyncio.Future()  # Run forever

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(start_server())
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.error("WebSocket server error: %s", exc)
    finally:
        loop.close()


def run_app() -> None:
    """Start the aiohttp application and optional WebSocket server."""
    app = create_app()

    if config.websocket.enabled:
        ws_thread = threading.Thread(target=_run_websocket_server, daemon=True)
        ws_thread.start()
        logger.info("WebSocket server started in background thread")

    logger.info("Starting aiohttp server on %s:%s", config.api.host, config.api.port)
    web.run_app(
        app,
        host=config.api.host,
        port=config.api.port,
        handle_signals=False,
        print=None,
    )


if __name__ == "__main__":  # pragma: no cover
    run_app()
