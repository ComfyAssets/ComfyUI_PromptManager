"""
ComfyUI_PromptManager: A comprehensive ComfyUI custom node that extends text encoding
with persistent prompt storage, advanced search capabilities, and an automatic image
gallery system.

This module provides prompt node types:
- PromptManager: CLIP encoding node that outputs CONDITIONING
- PromptManagerFocused: Editor-first layout using the same encoding contract
- PromptManagerSplit: Side-by-side editor and library using the same contract
- PromptManagerText: Text-only node that outputs STRING with prepend/append
  functionality

The nodes share the same database backend for persistent prompt storage and include
an automatic image gallery system that monitors ComfyUI output directories and links
generated images to their source prompts.

Features:
- SQLite-based persistent prompt storage with deduplication
- Advanced search and categorization system
- Real-time image gallery with metadata extraction
- Web-based admin dashboard for prompt management
- Comprehensive logging and diagnostics
"""

import atexit
import threading
import time
from pathlib import Path


def get_version():
    """Parse version from pyproject.toml (``utils/version.py``)."""
    from .utils.version import project_version

    try:
        root = Path(__file__).parent
    except Exception:
        return "unknown"
    return project_version(root)


from .prompt_manager import (  # noqa: E402
    PromptManager,
    PromptManagerFocused,
    PromptManagerSplit,
)
from .prompt_manager_pair import PromptManagerPair  # noqa: E402
from .prompt_manager_text import PromptManagerText  # noqa: E402
from .prompt_search_list import PromptSearchList  # noqa: E402

NODE_CLASS_MAPPINGS = {
    "PromptManager": PromptManager,
    "PromptManagerFocused": PromptManagerFocused,
    "PromptManagerSplit": PromptManagerSplit,
    "PromptManagerPair": PromptManagerPair,
    "PromptManagerText": PromptManagerText,
    "PromptSearchList": PromptSearchList,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "PromptManager": "Prompt Manager (Classic)",
    "PromptManagerFocused": "Prompt Manager (Focused)",
    "PromptManagerSplit": "Prompt Manager (Split)",
    "PromptManagerPair": "Prompt Manager (+/-)",
    "PromptManagerText": "Prompt Manager Text",
    "PromptSearchList": "Prompt Search List",
}

# Canvas extension only. ComfyUI loads every .js under this directory into the
# canvas page, so the admin/gallery bundles and vendored libraries under web/
# are served by our own routes instead (see py/api).
WEB_DIRECTORY = "web/comfy"

# Parts the package teardown stops (``_stop_background_work``). Each stays None
# when its start-up block below fails, and the teardown skips it.
api = None
_global_prompt_tracker = None
_global_image_monitor = None

# Add API routes (same pattern as ComfyUI_Assets)
try:
    # Set extension URI
    import os

    from .py import config
    from .py.api import PromptManagerAPI

    extension_uri = os.path.dirname(__file__)
    config.extension_uri = extension_uri

    # Register API routes using the same pattern as ComfyUI_Assets
    routes = config.routes
    api = PromptManagerAPI()
    api.add_routes(routes)
    # Temp files of preview writes a crash interrupted (#317), off the
    # import path: the walk may cover a large thumbnails folder.
    threading.Thread(
        target=api.sweep_thumbnail_temps,
        name="PromptManagerPreviewTempSweep",
        daemon=True,
    ).start()
    host_app = getattr(config.server_instance, "app", None)
    if host_app is not None and hasattr(host_app, "on_cleanup"):
        host_app.on_cleanup.append(api.cleanup_history_reads)
        host_app.on_cleanup.append(api.cleanup_annotation_captures)
        host_app.on_cleanup.append(api.cleanup_gallery_listings)
    if host_app is not None and hasattr(host_app, "on_startup"):
        # Build each output root's Images listing once the server's loop runs,
        # so the first open does not wait for the walk (#348).
        host_app.on_startup.append(api.warm_gallery_listings)
    if host_app is not None and hasattr(host_app, "on_shutdown"):
        # Open Needs review change streams end before the server waits on them.
        host_app.on_shutdown.append(api.close_review_events)

except Exception as e:
    # Log to internal logging system without console spam
    try:
        from .utils.logging_config import get_logger

        logger = get_logger("prompt_manager.init")
        logger.error(f"Failed to register API routes: {e}")
    except Exception:
        # logging failed too; a report must never stop the node loading
        pass

# Shared database, set up with the monitoring system below. Stays None when that
# fails so the queue hook can report the database as unavailable instead of crashing.
_global_db = None

# Start image monitoring globally at module import time
# This ensures images are linked regardless of which node is used in the workflow
try:
    from .database.operations import PromptDatabase
    from .utils.image_monitor import get_image_monitor
    from .utils.prompt_tracker import get_prompt_tracker
    from .utils.logging_config import get_logger

    _init_logger = get_logger("prompt_manager.init")
    _init_logger.info("Starting global image monitoring system...")

    # Initialize database and get singleton instances
    _global_db = PromptDatabase()
    _global_prompt_tracker = get_prompt_tracker(_global_db)
    _global_image_monitor = get_image_monitor(_global_db, _global_prompt_tracker)

    # The Needs review pass for ambiguous new outputs runs through the API's
    # capture store (the one the scan and the Tag dialog use): a linked output
    # is never queued and its pending items leave the queue. The package
    # teardown detaches it before that store is closed at shutdown.
    if api is not None:
        _global_image_monitor.set_review_recorder(api.record_review_file)
        # Every settled output's preview is queued for the API's background
        # worker (#249): the workspace never asks to generate thumbnails.
        _global_image_monitor.set_thumbnail_queue(api.queue_capture_thumbnail)
        # Every settled output walks its Images listing again (#348): the
        # API hops from the monitor's thread onto the server's loop.
        _global_image_monitor.set_output_listener(api.notify_output_settled)

    # Start monitoring
    _global_image_monitor.start_monitoring()
    _init_logger.info("Global image monitoring system started successfully")

except Exception as e:
    try:
        from .utils.logging_config import get_logger

        _init_logger = get_logger("prompt_manager.init")
        _init_logger.error(f"Failed to start image monitoring: {e}")
    except Exception:
        print(f"[ComfyUI-PromptManager] Warning: Failed to start image monitoring: {e}")


def _db_factory():
    """The shared database for the queue hook; called lazily per request."""
    if _global_db is None:
        raise RuntimeError("PromptManager database unavailable")
    return _global_db


# Count prompt runs when ComfyUI dequeues workflows (it skips unchanged nodes, so
# node execution alone misses re-runs). Separate from monitoring so either can fail.
try:
    from .py.config import server_instance
    from .utils.usage_tracking import register_queue_hook

    register_queue_hook(server_instance, _db_factory)
except Exception as e:
    try:
        from .utils.logging_config import get_logger

        get_logger("prompt_manager.init").error(f"Failed to register usage hook: {e}")
    except Exception:
        import logging

        logging.getLogger("prompt_manager.init").error(
            f"Failed to register usage hook: {e}"
        )

# A cached node counts what its last execution of the same text counted (#347).
try:
    from .py.config import server_instance
    from .utils.cached_runs import register_cached_runs

    register_cached_runs(server_instance, _db_factory)
except Exception as e:
    try:
        from .utils.logging_config import get_logger

        get_logger("prompt_manager.init").error(
            f"Failed to register cached-run counting: {e}"
        )
    except Exception:
        import logging

        logging.getLogger("prompt_manager.init").error(
            f"Failed to register cached-run counting: {e}"
        )


def _history_db_factory() -> "PromptDatabase":
    """Initialize history on its worker even if monitoring initialization failed."""
    if _global_db is not None:
        return _global_db
    from .database.operations import PromptDatabase

    return PromptDatabase()


_history_registration = None
try:
    from .py.config import server_instance
    from .utils.execution_history import register_history_hook

    _history_registration = register_history_hook(server_instance, _history_db_factory)
except Exception:
    try:
        from .utils.logging_config import get_logger

        get_logger("prompt_manager.init").error("Failed to register execution history")
    except Exception:
        import logging

        logging.getLogger("prompt_manager.init").error(
            "Failed to register execution history"
        )


# ── Package teardown (#301, #302) ────────────────────────────────────────────
# ComfyUI never runs aiohttp's cleanup hooks (it never calls
# ``runner.cleanup()``), so ``atexit`` is the live host's path: Ctrl+C, and a
# normal exit. It is registered after the history hook, so at exit (newest
# first) it runs before the history dispose. Hosts that do run ``on_cleanup``
# (our test servers) run it there, and the tests' package unload calls it.

TEARDOWN_SECONDS = 10.0
_clock = time.monotonic
_background_work_lock = threading.Lock()
_background_work_result = None


def _teardown_step(name, stop, logger):
    """Run one teardown step; a failure (even Ctrl+C) is logged and is False."""
    try:
        return bool(stop())
    except (Exception, KeyboardInterrupt):
        logger.error(f"Background work: stopping {name} failed", exc_info=True)
        return False


def _teardown_logger():
    """PromptManager's init logger; the standard one if that cannot load."""
    try:
        from .utils.logging_config import get_logger

        return get_logger("prompt_manager.init")
    except Exception:
        import logging

        return logging.getLogger("prompt_manager.init")


def _stop_background_work():
    """Stop the image monitor, the preview worker and the prompt cleanup
    thread against one TEARDOWN_SECONDS deadline, once.

    Returns ``{"monitor", "thumbnails", "tracker"}`` booleans (True: it
    stopped), leaving out parts that never started. Later calls return a
    copy of the first result. A Ctrl+C inside a step is logged and the
    step is False; one between steps is re-raised after the result (parts
    not reached are False) is stored and the ``atexit`` entry removed.
    """
    global _background_work_result
    with _background_work_lock:
        if _background_work_result is not None:
            return dict(_background_work_result)
        monitor, thumbnails, tracker = (
            _global_image_monitor,
            api,
            _global_prompt_tracker,
        )
        # A part whose global is set has started; False until its step says
        # otherwise, so an interrupt between steps leaves an honest result.
        started = (
            ("monitor", monitor),
            ("thumbnails", thumbnails),
            ("tracker", tracker),
        )
        result = {name: False for name, part in started if part is not None}
        try:
            logger = _teardown_logger()
            began = _clock()
            deadline = began + TEARDOWN_SECONDS

            def left(own_bound):
                return max(0.0, min(own_bound, deadline - _clock()))

            if monitor is not None:

                def stop_monitor():
                    from .utils.image_monitor import OBSERVER_JOIN_SECONDS

                    # Callbacks first: an image in hand queues no preview into a
                    # closing preview worker. The worker may have read the queue
                    # callback already, so the API refuses new previews too (at
                    # exit, Python 3.12+ refuses to start their worker thread).
                    if thumbnails is not None:
                        thumbnails.refuse_auto_thumbnails()
                    monitor.set_review_recorder(None)
                    monitor.set_thumbnail_queue(None)
                    monitor.set_output_listener(None)
                    return monitor.stop_monitoring(left(OBSERVER_JOIN_SECONDS))

                result["monitor"] = _teardown_step(
                    "the image monitor", stop_monitor, logger
                )
            if thumbnails is not None:

                def close_previews():
                    from .py.api.thumbnail_auto import CLOSE_WAIT_SECONDS

                    return thumbnails.close_auto_thumbnails(
                        final=True, timeout=left(CLOSE_WAIT_SECONDS)
                    )

                result["thumbnails"] = _teardown_step(
                    "the preview worker", close_previews, logger
                )
            if tracker is not None:

                def stop_tracker():
                    from .utils.prompt_tracker import TRACKER_JOIN_SECONDS

                    return tracker.shutdown(left(TRACKER_JOIN_SECONDS))

                result["tracker"] = _teardown_step(
                    "the prompt cleanup thread", stop_tracker, logger
                )
            elapsed = _clock() - began
            failed = [name for name, ok in result.items() if not ok]
            if failed:
                logger.warning(
                    f"Background work not stopped in time ({elapsed:.1f} s): "
                    + ", ".join(failed)
                )
            else:
                parts = " ".join(f"{name}=ok" for name in result)
                logger.info(f"Background work stopped in {elapsed:.1f} s: {parts}")
        finally:
            _background_work_result = result
            atexit.unregister(_stop_background_work)
        return dict(result)


async def _stop_background_work_on_cleanup(app):
    """aiohttp ``on_cleanup``: no request is served then, so it runs inline."""
    _stop_background_work()


def _kill_encoders_at_exit():
    """Run by the exit hook before Python joins the executor threads (#376):
    a JPEG XL or ffmpeg encoder (no console on Windows, so Ctrl+C never
    reaches it) is killed instead of being waited for."""
    from .py.api.thumbnail_encode import shutdown_encoders

    shutdown_encoders()


def _abort_downloads_at_exit():
    """Run by the exit hook (#376): huggingface_hub's Xet transfers ignore
    the chunk checkpoint, so they are aborted the way hub aborts on Ctrl+C."""
    import sys

    if "huggingface_hub" not in sys.modules:
        return  # no download ever ran: import nothing at exit
    from .py.autotag import abort_hub_downloads

    abort_hub_downloads()


try:
    # Before the executor join: running jobs stop at their next file, item or
    # chunk (#376). The teardown below still runs from atexit afterwards.
    from .utils import exit_signal as _exit_signal

    _exit_signal.install(_kill_encoders_at_exit)
    _exit_signal.install(_abort_downloads_at_exit)
except Exception as e:
    try:
        from .utils.logging_config import get_logger

        get_logger("prompt_manager.init").error(
            f"Failed to register the exit signal: {e}"
        )
    except Exception:
        # logging failed too; a report must never stop the node loading
        pass

try:
    atexit.register(_stop_background_work)
    from .py.config import server_instance as _teardown_server

    _teardown_host = getattr(_teardown_server, "app", None)
    if _teardown_host is not None and hasattr(_teardown_host, "on_cleanup"):
        _teardown_host.on_cleanup.insert(0, _stop_background_work_on_cleanup)
except Exception as e:
    try:
        from .utils.logging_config import get_logger

        get_logger("prompt_manager.init").error(
            f"Failed to register the background work teardown: {e}"
        )
    except Exception:
        # logging failed too; a report must never stop the node loading
        pass

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

# Print startup message with loaded tools
print()
print(f"\033[94m[ComfyUI-PromptManager] Version:\033[0m {get_version()}")
for node_key, display_name in NODE_DISPLAY_NAME_MAPPINGS.items():
    # ASCII only: a Windows console using a legacy code page cannot print emoji,
    # and a UnicodeEncodeError here would abort loading the whole node.
    print(f"  \033[94mLoaded:\033[0m {display_name}")
print(f"\033[94mTotal: {len(NODE_CLASS_MAPPINGS)} tools loaded\033[0m")
print()
