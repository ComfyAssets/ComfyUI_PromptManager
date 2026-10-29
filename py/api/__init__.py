"""REST API module for ComfyUI PromptManager.

Split into domain-specific mixins for maintainability:
- PromptRoutesMixin: prompt CRUD, tags, categories, bulk ops, export
- ImageRoutesMixin: gallery, thumbnails, image serving and linking
- AdminRoutesMixin: duplicates, stats, settings, diagnostics, maintenance, backup, scan
- LoggingRoutesMixin: log management endpoints
- LogCleanupMixin: the reviewed rotated-log cleanup (a maintenance plan)
- DiagnosticsReportMixin: the shareable diagnostic report, without secrets or paths
- AutotagRoutesMixin: auto-tagging model management and tagging
- JobRoutesMixin: the session job registry behind Activity (py/api/jobs.py)
- ImageTagJobsMixin: bulk Add tags / Auto-tag as image-tags jobs
- ThumbnailJobsMixin: thumbnail plan, thumbnail job and the gallery stream on it
- ThumbnailConvertMixin: Convert outdated thumbnails (#317), a thumbnail job mode
- AutoThumbnailMixin: previews made on first view, on capture and after a scan
- ReviewRoutesMixin: the Needs review queue, item detail and decisions
- ReviewRecordingMixin: Needs review recording for the scan and the monitor
- ReviewEventsMixin: the Needs review change stream an open page re-reads on
- SystemRoutesMixin: System health check, Overview and usage statistics
- LibraryLocationRoutesMixin: where the library lives, Move library, Keep it here
- MaintenanceRoutesMixin: reviewed maintenance plans (preview, apply)
- WorkflowStorageRoutesMixin: store each workflow once, then Reclaim space
- DuplicateFilesMixin: duplicate image files as find and removal jobs
- LoraImportRoutesMixin: the reviewed, staged LoRA reference import job
"""

import asyncio
import datetime
import functools
import gzip as gzip_module
import json
import math
import ntpath
import os
import re
import threading
from pathlib import Path

from aiohttp import web
from PIL import Image

from .prompts import PromptRoutesMixin
from .revalidation import revalidated_response
from .pairs import PairRoutesMixin
from .library import LibraryRoutesMixin
from .vocabulary import VocabularyRoutesMixin
from .annotations import AnnotationRoutesMixin
from .review import ReviewRoutesMixin
from .review_recording import ReviewRecordingMixin
from .review_events import ReviewEventsMixin
from .saved_views import SavedViewRoutesMixin
from .collections import CollectionRoutesMixin
from .history import HistoryRoutesMixin
from .images import ImageRoutesMixin
from .gallery_listing import WALK_STOPPED, RootListing, WalkOutcome
from .admin import AdminRoutesMixin
from .settings_routes import SettingsRoutesMixin
from .job_routes import JobRoutesMixin
from .image_tag_jobs import ImageTagJobsMixin
from .thumbnail_jobs import ThumbnailJobsMixin
from .thumbnail_convert import ThumbnailConvertMixin
from .thumbnail_auto import AutoThumbnailMixin
from .system import SystemRoutesMixin
from .library_location import LibraryLocationRoutesMixin
from .maintenance import MaintenanceRoutesMixin
from .backup_restore import BackupRestoreRoutesMixin
from .workflow_storage import WorkflowStorageRoutesMixin
from .duplicate_files import DuplicateFilesMixin
from .maintenance_plans import PlanStore
from .jobs import JobRegistry
from .logging_routes import LoggingRoutesMixin
from .log_cleanup import LogCleanupMixin
from .diagnostics_report import DiagnosticsReportMixin
from .autotag_routes import AutotagRoutesMixin
from .lora_integration import LoraIntegrationMixin
from .lora_import_routes import LoraImportRoutesMixin
from .image_categories import ImageCategoryRoutesMixin
from .image_delete import ImageDeleteMixin
from .image_return import ImageReturnMixin
from .source_rescope import SourceRescopeMixin

try:
    from ...database.operations import PromptDatabase
    from ...utils.file_prompt import parse_comfyui_prompt
    from ...utils.logging_config import get_logger
    from ...utils.video_metadata import is_video_path, read_video_metadata
except ImportError:
    import sys

    sys.path.insert(
        0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )
    from database.operations import PromptDatabase
    from utils.file_prompt import parse_comfyui_prompt
    from utils.logging_config import get_logger
    from utils.video_metadata import is_video_path, read_video_metadata


def _get_project_root():
    """Get the project root directory (3 levels up from py/api/__init__.py)."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# How long an executor thread waits for the loop to drop the listings.
_OFF_LOOP_DROP_WAIT = 30


def _running_loop():
    """The event loop running in this thread, or None."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _public_path(path) -> str:
    """Render a server path for an API response without revealing the layout.

    Returns the path relative to the ComfyUI base directory (or to the parent
    of an opted-in extra gallery root), as a POSIX-style string, or just the
    basename when the path is not under any of those anchors.
    """
    if not path:
        return ""
    from ..config import GalleryConfig

    try:
        canonical = Path(os.path.normcase(os.path.realpath(str(path))))
    except (OSError, ValueError):
        return os.path.basename(str(path))
    for anchor in GalleryConfig.path_anchors():
        if canonical.is_relative_to(Path(anchor)):
            return canonical.relative_to(Path(anchor)).as_posix() or "."
    return canonical.name


def _public_error(exc: BaseException) -> str:
    """Error text safe for API responses: OSError paths reduced to basenames."""
    if isinstance(exc, OSError):
        parts = [exc.strerror or type(exc).__name__]
        if exc.filename:
            parts.append(os.path.basename(str(exc.filename)))
        return ": ".join(parts)
    return str(exc) or type(exc).__name__


# ── Static file serving ────────────────────────────────────────────────
# Only these extensions are ever served from web/lib and web/js; the map
# doubles as the allow-list.
STATIC_MIME_TYPES = {
    ".js": "application/javascript",
    ".mjs": "application/javascript",
    ".css": "text/css",
    ".map": "application/json",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".html": "text/html",
}
_STATIC_SEPARATORS = re.compile(r"[\\/]+")
# Served by exact request path beside the extension allow-list: the browser's
# copy of the inspection node table (local Inspect resolves with it, #249).
STATIC_EXACT_FILES = {("js", "inspection-semantics.json"): "application/json"}


def _resolve_static_file(root, filepath, allowed=STATIC_MIME_TYPES):
    """Real path of ``filepath`` under ``root``, or None when it must not be served.

    The request path is untrusted. It is rejected when it is empty, carries a
    ``..`` segment, is absolute on any platform (a leading slash or backslash,
    a drive letter such as ``C:`` or a UNC ``\\\\server\\share`` prefix all
    re-anchor ``os.path.join`` on Windows), has an extension outside
    ``allowed`` (STATIC_MIME_TYPES), or resolves (symlinks included) outside
    ``root``.
    """
    if not isinstance(filepath, str) or not filepath:
        return None
    if (
        os.path.isabs(filepath)
        or ntpath.isabs(filepath)
        or ntpath.splitdrive(filepath)[0]
    ):
        return None
    segments = _STATIC_SEPARATORS.split(filepath)
    if any(segment in ("", ".", "..") for segment in segments):
        return None
    if os.path.splitext(filepath)[1].lower() not in allowed:
        return None
    try:
        root_real = Path(os.path.normcase(os.path.realpath(root)))
        candidate = Path(
            os.path.normcase(os.path.realpath(os.path.join(root, *segments)))
        )
    except (OSError, ValueError):
        return None
    if candidate == root_real or not candidate.is_relative_to(root_real):
        return None
    return str(candidate)


# ── JSON request bodies ────────────────────────────────────────────────
JSON_BODY_MAX_BYTES = 1_000_000
_JSON_READ_CHUNK = 64 * 1024


def _json_error(message, status):
    return web.json_response({"success": False, "error": message}, status=status)


async def _read_json_body(request, max_bytes=JSON_BODY_MAX_BYTES):
    """Read and parse a JSON object body of at most ``max_bytes``.

    The body is read in chunks so an oversized or chunked upload is refused
    as soon as it crosses the cap rather than buffered whole.

    Returns:
        (data, None) on success, or (None, response) where ``response`` is a
        413 (body too large) or 400 (invalid JSON, or not a JSON object).
    """
    too_large = _json_error(f"Request body too large (max {max_bytes} bytes)", 413)
    declared = request.content_length
    if declared is not None and declared > max_bytes:
        return None, too_large

    chunks = []
    size = 0
    async for chunk in request.content.iter_chunked(_JSON_READ_CHUNK):
        size += len(chunk)
        if size > max_bytes:
            return None, too_large
        chunks.append(chunk)

    try:
        data = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, _json_error("Request body must be valid JSON", 400)
    if not isinstance(data, dict):
        return None, _json_error("Request body must be a JSON object", 400)
    return data, None


class _LongJobGuard:
    """Async context manager handed out by ``_acquire_long_job``."""

    def __init__(self, lock):
        self._lock = lock

    async def __aenter__(self):
        # The caller checked ``locked()`` on this same loop iteration, so this
        # acquire never waits.
        await self._lock.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._lock.release()
        return False


# ── Gzip compression middleware ────────────────────────────────────────
_GZIP_MIN_SIZE = 1024  # Only compress bodies larger than 1 KB
_GZIP_TYPES = frozenset(
    (
        "application/json",
        "text/html",
        "text/css",
        "application/javascript",
        "text/plain",
    )
)
_gzip_registered = False


@web.middleware
async def _gzip_middleware(request, handler):
    """Compress PromptManager responses when the client accepts gzip."""
    response = await handler(request)

    if not request.path.startswith("/prompt_manager/"):
        return response

    # Only handle regular Response objects (not StreamResponse/WebSocket)
    if not isinstance(response, web.Response):
        return response

    if "gzip" not in request.headers.get("Accept-Encoding", ""):
        return response

    if "Content-Encoding" in response.headers:
        return response

    body = response.body
    if body is None or len(body) < _GZIP_MIN_SIZE:
        return response

    content_type = response.content_type or ""
    if not any(ct in content_type for ct in _GZIP_TYPES):
        return response

    compressed = gzip_module.compress(body, compresslevel=6)
    if len(compressed) >= len(body):
        return response

    # A length the handler declared is the uncompressed one: drop it so the
    # compressed body's own length is sent (#329: a stale length made the
    # browser wait for bytes that never came).
    response.headers.pop("Content-Length", None)
    response.body = compressed
    response.headers["Content-Encoding"] = "gzip"
    response.headers["Vary"] = "Accept-Encoding"
    return response


class PromptManagerAPI(
    PairRoutesMixin,
    LibraryRoutesMixin,
    VocabularyRoutesMixin,
    AnnotationRoutesMixin,
    ReviewRoutesMixin,
    ReviewRecordingMixin,
    ReviewEventsMixin,
    SavedViewRoutesMixin,
    CollectionRoutesMixin,
    HistoryRoutesMixin,
    PromptRoutesMixin,
    ImageRoutesMixin,
    AdminRoutesMixin,
    SettingsRoutesMixin,
    JobRoutesMixin,
    ImageTagJobsMixin,
    ThumbnailJobsMixin,
    ThumbnailConvertMixin,
    AutoThumbnailMixin,
    SystemRoutesMixin,
    LibraryLocationRoutesMixin,
    MaintenanceRoutesMixin,
    BackupRestoreRoutesMixin,
    WorkflowStorageRoutesMixin,
    DuplicateFilesMixin,
    LoggingRoutesMixin,
    LogCleanupMixin,
    DiagnosticsReportMixin,
    AutotagRoutesMixin,
    LoraIntegrationMixin,
    LoraImportRoutesMixin,
    ImageCategoryRoutesMixin,
    ImageDeleteMixin,
    ImageReturnMixin,
    SourceRescopeMixin,
):
    """REST API handler for PromptManager operations and web interface.

    This class provides comprehensive REST API endpoints for managing prompts,
    images, and system operations. It handles database interactions, file
    operations, image processing, and web UI serving.

    The API is designed to integrate seamlessly with ComfyUI's aiohttp server
    and provides both JSON API endpoints and static file serving for the
    web interface.

    Attributes:
        logger: Configured logger instance for API operations
        db (PromptDatabase): Database connection and operations handler
    """

    def __init__(self, db: PromptDatabase | None = None) -> None:
        """Initialize the API, using an injected database before any cleanup."""
        self.logger = get_logger("prompt_manager.api")
        self.logger.info("Initializing PromptManager API")

        self.db = db if db is not None else PromptDatabase()
        self._history_read_fence = None
        self._history_reads_closed = False
        # Private media captures of the annotation routes (py/api/annotations.py),
        # created on first use with their restore subscription.
        self._captures = None
        # Capture tokens Generate suggestions ran on: a save of one marks the
        # image auto-tagged (#379).
        self._suggested_tokens = set()
        self._captures_unsubscribe = None
        # Set by close_annotation_captures (shutdown): no store is recreated.
        self._captures_closed = False
        self._cached_output_dir = None  # Lazy-cached by _find_comfyui_output_dir()
        self._html_cache = {}  # Cached HTML file contents keyed by path
        # One shared listing per output root, keyed by str(root), and the
        # background walks refreshing them (py/api/gallery_listing.py, #348).
        self._root_listings: dict[str, RootListing] = {}
        self._listing_tasks: set = set()
        # The loop the walks run on, set by the startup warm-up or the first
        # walk; the monitor's thread hops onto it (notify_output_settled).
        self._listing_loop = None
        # Set by cleanup_gallery_listings: no background walk starts after it.
        self._listings_closed = False
        # The workspace caches rebuilt behind a stored walk that changed the
        # listing, once the host's startup warm-up has run (#348).
        self._workspace_warm_enabled = False
        self._workspace_warm_task = None
        self._workspace_warm_again = False
        # Workspace listing values and sort orders derived from a cached scan
        # (py/api/images.py _workspace_listed/_workspace_orders).
        self._workspace_listed_cache = {}
        self._workspace_order_cache = {}
        # Card facts (dimensions, seed, positive title) per root and
        # (path, mtime), kept and pruned with the per-file values
        # (py/api/images.py _card_facts).
        self._workspace_card_cache = {}
        # Favorite keys of each root's cached scan (images._favorite_listing).
        self._favorite_listing_cache = {}
        # Single-flight locks for long jobs, keyed by job family; see
        # _acquire_long_job(). Created lazily so no lock binds to an event
        # loop before the server's loop exists.
        self._long_jobs: dict = {}
        # This session's jobs (py/api/jobs.py): the metadata scan, and later
        # every other long operation the Activity page tracks.
        self._jobs = JobRegistry(self.logger)
        # The latest metadata scan job (also in self._jobs), if one was started.
        self._scan_job = None
        # Duplicate-file finds by job id, with the groups each one holds for
        # review (py/api/duplicate_files.py); the last few only.
        self._duplicate_finds = {}
        # The last System health report this session and the database
        # generation it describes, bumped when a restore replaces self.db
        # (py/api/system.py).
        self._health_report = None
        self._db_generation = 0
        # Reviewed maintenance plans under single-use tokens; a restart
        # forgets them (py/api/maintenance.py).
        self._maintenance_plans = PlanStore()
        # #480: the removal marks the workspace listing hides, read once now so
        # a listing page reads no table until a mark or the database changes.
        try:
            self._removal_marks()
        except Exception:
            self.logger.warning("Removal marks could not be read", exc_info=True)

        # A safety backup interrupted by a crash leaves its temp copy behind.
        try:
            from .maintenance import sweep_safety_temps

            from .backup_restore import restore_stage

            swept = sweep_safety_temps(self.db.model.db_path)
            swept += restore_stage.sweep_restore_temps(self.db.model.db_path)
            if swept:
                self.logger.info(f"Removed {len(swept)} interrupted backup copies")
        except Exception as e:
            self.logger.warning(f"Could not sweep interrupted backup copies: {e}")

        # A crash between Move library's switch and its rename left the old
        # node-folder files under their own names (#249 S6b): rename them now.
        self.finish_interrupted_library_move()

        # Run cleanup on initialization to remove any existing duplicates
        try:
            removed = self.db.cleanup_duplicates()
            if removed > 0:
                self.logger.info(
                    f"Startup cleanup: removed {removed} duplicate prompts"
                )
        except Exception as e:
            self.logger.error(f"Startup cleanup failed: {e}")

        self.logger.info("PromptManager API initialization completed")

    _public_path = staticmethod(_public_path)
    _public_error = staticmethod(_public_error)
    _read_json_body = staticmethod(_read_json_body)

    def _acquire_long_job(self, name):
        """Claim the single-flight slot for a long job family, or None when busy.

        Usage in a handler::

            job = self._acquire_long_job("duplicates")
            if job is None:
                return self._long_job_busy_response("Duplicate scan")
            async with job:
                ...

        Families are free-form strings ("duplicates", "thumbnails",
        "autotag", ...); the lock is released when the ``async with`` block
        exits, including on exceptions. Must be called from the event loop.
        """
        lock = self._long_jobs.setdefault(name, asyncio.Lock())
        if lock.locked():
            return None
        return _LongJobGuard(lock)

    @staticmethod
    def _long_job_busy_response(label):
        """409 envelope for a long job that is already running."""
        return _json_error(f"{label} already running", 409)

    async def _run_held(self, kind, title, admission, operation):
        """Run a legacy-page operation under a registry hold (D80).

        While ``operation()`` runs, the registry lists it as a running,
        non-cancellable job, so workspace jobs its admission class conflicts
        with are refused, naming it. When the registry refuses the hold, the
        legacy 409 envelope names the running job and nothing is done. A
        response of 400 or above ends the hold ``failed``.
        """
        busy = self._jobs.blocker(kind, admission=admission)
        if busy is not None:
            return self._long_job_busy_response(busy.title)
        async with self._jobs.hold(kind, title, admission=admission) as job:
            response = await operation()
            if getattr(response, "status", 200) >= 400:
                job.finish("failed")
            return response

    async def _run_in_executor(self, func, *args, **kwargs):
        """Run a blocking function in the default thread pool executor.

        Prevents synchronous database calls, file I/O, and PIL operations
        from blocking the aiohttp event loop.
        """
        loop = asyncio.get_event_loop()
        if kwargs:
            call = functools.partial(func, *args, **kwargs)
            return await loop.run_in_executor(None, call)
        return await loop.run_in_executor(None, func, *args)

    def _root_listing(self, root) -> RootListing:
        """The shared listing record of one output root (created empty)."""
        return self._root_listings.setdefault(str(root), RootListing())

    def invalidate_gallery_cache(self, retire=False):
        """Drop every root's listing, so the next read waits for a new walk.

        Called after files are deleted through the app (duplicate cleanup and
        the legacy duplicate delete), never for thumbnail writes. A walk
        running now is not stored (its generation moved on). The workspace
        keeps its per-file values and card facts, so the next request
        rebuilds only new or changed files (see ImageRoutesMixin.
        _workspace_listed and _card_facts); its sort orders are dropped, as
        the walk replaces them anyway, and so is the favorites lookup, which
        holds the old list.

        ``retire`` (the roots changed): no background walk of a root starts
        again until a reader or the warm-up asks for it.
        """
        loop = self._listing_loop
        if loop is not None and loop.is_running() and _running_loop() is not loop:
            # Called from an executor thread (a settings save, a test seed):
            # listing records are touched only on the loop, and the caller
            # expects the drop done when this returns.
            dropped = threading.Event()

            def drop():
                try:
                    self._drop_listings(retire)
                finally:
                    dropped.set()

            loop.call_soon_threadsafe(drop)
            if not dropped.wait(_OFF_LOOP_DROP_WAIT):
                self.logger.warning("The Images listing drop is still queued")
            return
        self._drop_listings(retire)

    def _drop_listings(self, retire=False):
        for record in list(self._root_listings.values()):
            record.drop()
            if retire:
                # The roots changed: a removed root must not come back through
                # a pending timer, a trailing walk or a monitor mark.
                record.retired = True
                record.dirty = False
                handle, record.rewalk_handle = record.rewalk_handle, None
                if handle is not None:
                    handle.cancel()
        self._workspace_order_cache = {}
        self._favorite_listing_cache = {}

    async def warm_gallery_listings(self, app=None):
        """Start one background walk per output root (aiohttp on_startup).

        Registered by the root ``__init__.py``, so the first Images open after
        ComfyUI starts finds the listing built. It never fails startup: a
        root that cannot be walked is retried by the first read.
        """
        # Each stored walk that changes a listing then rebuilds the workspace's
        # per-file values, orders and favorites behind it (_warm_workspace).
        self._workspace_warm_enabled = True
        self._listing_loop = asyncio.get_running_loop()
        self._listings_closed = False
        try:
            for root in self._get_all_output_dirs():
                self._root_listing(root).retired = False
                self._schedule_walk(root)
        except Exception as e:
            self.logger.warning(f"Could not warm the Images listing: {e}")

    def notify_output_settled(self, path):
        """The image monitor settled an output at *path* (any thread).

        Called on the monitor's worker thread with the resolved path. It only
        hands the path to the event loop (``output_settled``) and never
        touches a listing from this thread; before the loop is known, or once
        the listings are cleaned up, it does nothing.
        """
        loop = self._listing_loop
        if loop is None or self._listings_closed or not loop.is_running():
            return
        try:
            loop.call_soon_threadsafe(self.output_settled, path)
        except RuntimeError:
            pass  # the loop closed meanwhile (shutdown)

    async def cleanup_gallery_listings(self, app=None):
        """Stop the background listing walks (aiohttp on_cleanup)."""
        self._workspace_warm_enabled = False
        # No mark, deferred walk or trailing walk starts a walk from now on.
        self._listings_closed = True
        # Before any await, so no delayed refresh can start a walk meanwhile.
        for record in self._root_listings.values():
            handle, record.rewalk_handle = record.rewalk_handle, None
            if handle is not None:
                handle.cancel()
        warm, self._workspace_warm_task = self._workspace_warm_task, None
        if warm is not None:
            warm.cancel()
            await asyncio.gather(warm, return_exceptions=True)
        tasks = list(self._listing_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._listing_tasks.clear()
        # A walk cancelled before its first step never ran its own cleanup.
        for record in self._root_listings.values():
            flight, record.flight = record.flight, None
            if flight is not None and not flight.done():
                flight.set_result(WalkOutcome.failed(RuntimeError(WALK_STOPPED)))

    def reset_gallery_roots_cache(self):
        """Drop every cached listing and derived workspace value (roots changed)."""
        self.invalidate_gallery_cache(retire=True)
        self._workspace_listed_cache = {}
        self._workspace_card_cache = {}

    def add_routes(self, routes):
        """Register all API routes with the ComfyUI server.

        Args:
            routes: aiohttp RouteTableDef object from ComfyUI server instance.
        """

        # Test route to verify registration works
        @routes.get("/prompt_manager/test")
        async def test_route(request):
            return web.json_response(
                {
                    "success": True,
                    "message": "PromptManager API is working!",
                    "timestamp": str(datetime.datetime.now()),
                }
            )

        # ── Web UI serving routes ─────────────────────────────────────

        @routes.get("/prompt_manager/workspace")
        async def serve_workspace_ui(request: web.Request) -> web.Response:
            return self._serve_html_page(request, "workspace.html", "Workspace")

        @routes.get("/prompt_manager/web")
        async def serve_web_ui(request):
            return self._serve_html_page(request, "index.html", "Web UI", cache=False)

        @routes.get("/prompt_manager/gallery.html")
        async def serve_gallery_ui(request):
            return self._serve_html_page(request, "metadata.html", "Gallery")

        @routes.get("/prompt_manager/admin")
        async def serve_admin_ui(request):
            return self._serve_html_page(request, "admin.html", "Admin UI")

        @routes.get("/prompt_manager/gallery")
        async def serve_gallery_admin_ui(request):
            return self._serve_html_page(request, "gallery.html", "Gallery")

        # ── Static file serving ───────────────────────────────────────

        @routes.get("/prompt_manager/lib/{filepath:.*}")
        async def serve_lib_static(request):
            """Serve static library files (JS, CSS) from web/lib directory."""
            return self._serve_static_file(
                request, "lib", request.match_info.get("filepath")
            )

        @routes.get("/prompt_manager/js/{filepath:.*}")
        async def serve_js_static(request):
            """Serve static JavaScript files from web/js directory."""
            return self._serve_static_file(
                request, "js", request.match_info.get("filepath")
            )

        # ── Register domain-specific routes from mixins ───────────────

        self._register_pair_routes(routes)
        self._register_library_routes(routes)
        self._register_vocabulary_routes(routes)
        self._register_annotation_routes(routes)
        self._register_review_routes(routes)
        self._register_review_event_routes(routes)
        self._register_saved_view_routes(routes)
        self._register_collection_routes(routes)
        self._register_history_routes(routes)
        self._register_prompt_routes(routes)
        self._register_image_routes(routes)
        self._register_admin_routes(routes)
        self._register_settings_routes(routes)
        self._register_job_routes(routes)
        self._register_system_routes(routes)
        self._register_diagnostics_report_routes(routes)
        self._register_library_location_routes(routes)
        self._register_maintenance_routes(routes)
        self._register_backup_restore_routes(routes)
        self._register_duplicate_routes(routes)
        self._register_logging_routes(routes)
        self._register_autotag_routes(routes)
        self._register_lora_routes(routes)
        self._register_lora_import_routes(routes)
        self._register_image_category_routes(routes)
        self._register_image_delete_routes(routes)
        self._register_image_return_routes(routes)
        self._register_rescope_routes(routes)

        # Register gzip compression middleware (once)
        global _gzip_registered
        if not _gzip_registered:
            try:
                from ..config import server_instance

                server_instance.app.middlewares.append(_gzip_middleware)
                _gzip_registered = True
                self.logger.info("Gzip compression middleware registered")
            except Exception as e:
                self.logger.warning(f"Could not register gzip middleware: {e}")

        self.logger.info("All routes registered with decorator pattern")

    def _serve_static_file(self, request, subdir, filepath):
        """Serve ``web/<subdir>/<filepath>``; 403 outside it, 404 when missing.

        A served file is revalidated on every reuse (#478, ``revalidation``).
        """
        root = os.path.join(_get_project_root(), "web", subdir)
        exact = STATIC_EXACT_FILES.get((subdir, filepath))
        # An exact file passes the same containment check, its extension only.
        allowed = STATIC_MIME_TYPES if exact is None else {".json"}
        file_path = _resolve_static_file(root, filepath, allowed)
        if file_path is None:
            return web.Response(text="Forbidden", status=403)
        content_type = (
            exact or STATIC_MIME_TYPES[os.path.splitext(file_path)[1].lower()]
        )
        if not os.path.isfile(file_path):
            return web.Response(text="Not Found", status=404)
        with open(file_path, "rb") as f:
            content = f.read()
        return revalidated_response(request, content, content_type)

    def _serve_html_page(self, request, filename, title, cache=True):
        """Serve ``web/<filename>``; 404 when missing, 500 when unreadable.

        A served page is revalidated on every reuse (#478, ``revalidation``).
        """
        html_path = os.path.join(_get_project_root(), "web", filename)
        try:
            if cache and html_path in self._html_cache:
                content = self._html_cache[html_path]
            elif os.path.exists(html_path):
                with open(html_path, "r", encoding="utf-8") as f:
                    content = f.read()
                if cache:
                    self._html_cache[html_path] = content
            else:
                return web.Response(
                    text=f"<h1>{title} not found</h1>"
                    "<p>HTML file not located at expected path.</p>",
                    content_type="text/html",
                    status=404,
                )
            return revalidated_response(
                request, content.encode("utf-8"), "text/html", charset="utf-8"
            )
        except Exception:
            self.logger.exception(f"Failed to load {title}")
            return web.Response(
                text=f"<h1>Error</h1><p>Failed to load {title}. "
                "Check server logs for details.</p>",
                content_type="text/html",
                status=500,
            )

    # ── Shared utilities used by multiple mixins ──────────────────────

    def _enrich_images(self, images):
        """Add url and thumbnail_url to a flat list of image dicts.

        Each dict without ``is_video`` also gets ``is_video`` and
        ``media_type`` from its extension (#355: a linked LoraManager video
        outside the output folders is shown as a video, not a picture).
        """
        from urllib.parse import quote as url_quote

        output_dirs = self._get_all_output_dirs()

        for image in images:
            image_path_str = image.get("image_path", "")
            if not image_path_str:
                continue

            if "is_video" not in image:
                video = is_video_path(image_path_str)
                image["is_video"] = video
                image["media_type"] = "video" if video else "image"

            img_path = Path(image_path_str)

            # Set fallback url via image ID
            if image.get("id"):
                image["url"] = f"/prompt_manager/images/{image['id']}/file"

            # Try each root to compute relative path and thumbnail URL
            for output_path in output_dirs:
                try:
                    rel_path = img_path.resolve().relative_to(output_path.resolve())
                    image["relative_path"] = str(rel_path)
                    image["url"] = (
                        "/prompt_manager/images/serve/"
                        f"{url_quote(rel_path.as_posix(), safe='/')}"
                    )

                    # Any preview the file owns, the current format first (#317)
                    from . import thumbnail_settings
                    from .prompts import thumbnail_url_for

                    existing = thumbnail_settings.existing_thumbnail(
                        output_path, rel_path
                    )
                    thumbnail_url = None
                    if existing is not None:
                        thumb_rel = (
                            thumbnail_settings.thumbnail_stem_rel(rel_path)
                            + existing.suffix
                        )
                        thumbnail_url = thumbnail_url_for(thumb_rel, existing)
                    if thumbnail_url:
                        image["thumbnail_url"] = thumbnail_url
                    break  # Found matching root, stop searching
                except (ValueError, RuntimeError):
                    continue  # Try next root

        return images

    def _enrich_prompt_images(self, prompts):
        """Add url and thumbnail_url to each image in prompt results."""
        for prompt in prompts:
            self._enrich_images(prompt.get("images", []))
        return prompts

    def _clean_nan_recursive(self, obj):
        """Recursively replace NaN and Infinity in nested data with None."""
        if isinstance(obj, dict):
            return {key: self._clean_nan_recursive(value) for key, value in obj.items()}
        elif isinstance(obj, list):
            return [self._clean_nan_recursive(item) for item in obj]
        elif isinstance(obj, float) and not math.isfinite(obj):
            return None
        else:
            return obj

    def _find_comfyui_output_dir(self):
        """Locate the ComfyUI output directory using multiple detection strategies.

        Checks in order:
        1. User-configured directory (GalleryConfig.MONITORING_DIRECTORIES)
        2. Upward filesystem search for ComfyUI markers
        3. Common installation path patterns
        4. Common ComfyUI installation locations

        Results are cached after first successful lookup. The cache is
        invalidated when the user changes the configured directory via
        save_settings().

        Returns:
            str or None: Absolute path to ComfyUI output directory, or None
                        if no valid directory is found
        """
        if self._cached_output_dir is not None:
            return self._cached_output_dir

        # Method 0: Check user-configured directory first
        try:
            from ..config import GalleryConfig

            if GalleryConfig.MONITORING_DIRECTORIES:
                configured = GalleryConfig.MONITORING_DIRECTORIES[0]
                # Same check as the settings endpoint: a root hand-edited
                # into config.json must not open a directory outside ComfyUI
                ok, reason = GalleryConfig.validate_gallery_root(configured)
                if ok:
                    configured_dir = Path(configured).resolve()
                    self.logger.info(
                        f"Using configured monitoring directory: {configured_dir}"
                    )
                    self._cached_output_dir = str(configured_dir)
                    return self._cached_output_dir
                self.logger.warning(f"Ignoring configured directory: {reason}")
        except ImportError:
            self.logger.debug(
                "GalleryConfig not available, skipping configured directory check"
            )

        current_file = Path(__file__).resolve()
        self.logger.debug(f"Starting ComfyUI output search from: {current_file}")

        # Method 1: Search upward from current file location
        current_dir = current_file.parent
        max_depth = 10  # Prevent infinite loops

        for i in range(max_depth):
            # Check if current directory contains ComfyUI markers
            comfyui_markers = ["main.py", "nodes.py", "server.py"]
            if any((current_dir / marker).exists() for marker in comfyui_markers):
                output_dir = current_dir / "output"
                if output_dir.exists() and output_dir.is_dir():
                    self.logger.debug(
                        "Found ComfyUI output directory via upward search: "
                        f"{output_dir}"
                    )
                    self._cached_output_dir = str(output_dir)
                    return self._cached_output_dir

            # Move up one directory
            parent = current_dir.parent
            if parent == current_dir:  # Reached filesystem root
                break
            current_dir = parent

        # Method 2: Try common installation patterns relative to this file
        # File is at: custom_nodes/ComfyUI_PromptManager/py/api/__init__.py
        base_dir = current_file.parent  # .../py/api/
        possible_paths = [
            base_dir.parent.parent.parent.parent / "output",  # ../../../../output
            base_dir.parent.parent.parent / "output",  # ../../../output
            base_dir.parent.parent / "output",  # ../../output
            base_dir.parent / "output",  # ../output
        ]

        # Method 3: Add common ComfyUI installation locations
        common_locations = [
            Path.home() / "ComfyUI" / "output",
            Path.cwd() / "output",
            Path.cwd() / ".." / "output",
            Path.cwd() / ".." / ".." / "output",
        ]

        all_paths = possible_paths + common_locations

        for path in all_paths:
            try:
                abs_path = path.resolve()
                if abs_path.exists() and abs_path.is_dir():
                    self.logger.debug(f"Found ComfyUI output directory: {abs_path}")
                    self._cached_output_dir = str(abs_path)
                    return self._cached_output_dir
            except (OSError, RuntimeError):
                continue  # Skip invalid paths

        self.logger.warning("ComfyUI output directory not found. Searched paths:")
        for path in all_paths:
            try:
                self.logger.warning(f"  - {path.resolve()} (exists: {path.exists()})")
            except (OSError, RuntimeError):
                self.logger.warning(f"  - {path} (invalid path)")

        return None

    def _get_all_output_dirs(self):
        """Get the directories images may be served from.

        Configured gallery roots are filtered through
        ``GalleryConfig.validate_gallery_root`` so a root that was hand-edited
        into config.json (or persisted before validation existed) can never
        expose files outside ComfyUI's directories.

        Returns:
            List[Path]: Validated directory paths, possibly empty.
        """
        return self._output_dirs_with_origin()[0]

    def _output_dirs_with_origin(self):
        """``(dirs, auto_detected)``: the ``_get_all_output_dirs`` list, and
        whether it comes from detection or ComfyUI's output rather than from
        a valid configured root (False when there is no directory at all).
        """
        from ..config import GalleryConfig

        configured = list(GalleryConfig.MONITORING_DIRECTORIES)
        output_dirs = []
        seen = set()
        for d in configured:
            ok, reason = GalleryConfig.validate_gallery_root(d)
            if not ok:
                self.logger.warning(f"Ignoring configured gallery root: {reason}")
                continue
            real = os.path.realpath(d)
            key = os.path.normcase(real)  # one entry per directory, however spelled
            if key in seen:
                continue
            seen.add(key)
            output_dirs.append(Path(real))

        if output_dirs:
            return output_dirs, False

        if configured:
            # Every configured root was rejected: never let auto-detection
            # resurrect it, serve ComfyUI's own output directory instead.
            fallback_dirs = self._comfyui_output_dir_list()
        else:
            fallback = self._find_comfyui_output_dir()
            fallback_dirs = [Path(fallback)] if fallback else []
        return fallback_dirs, bool(fallback_dirs)

    def _comfyui_output_dir_list(self):
        """ComfyUI's output directory as a one-element list, or [] if unknown."""
        try:
            import folder_paths

            output_dir = folder_paths.get_output_directory()
        except (ImportError, AttributeError, OSError):
            return []
        if isinstance(output_dir, str) and os.path.isdir(output_dir):
            return [Path(os.path.realpath(output_dir))]
        return []

    def _extract_comfyui_metadata(self, image_path):
        """Embedded ComfyUI metadata of an image (PNG text chunks) or a video.

        Videos go through ffprobe (see utils/video_metadata). Unreadable files
        are a warning, not an error: a scan over a large output tree meets
        truncated and foreign files, and it carries on past them.
        """
        if is_video_path(image_path):
            return read_video_metadata(image_path)
        try:
            with Image.open(image_path) as img:
                return dict(getattr(img, "text", None) or {})
        except Exception as e:
            self.logger.warning(
                f"Unreadable image {os.path.basename(str(image_path))}: {e}"
            )
            return {}

    def _parse_comfyui_prompt(self, metadata):
        """Parse ComfyUI and A1111 prompt data from metadata."""
        return parse_comfyui_prompt(metadata)
