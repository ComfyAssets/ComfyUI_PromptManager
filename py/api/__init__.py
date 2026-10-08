"""REST API module for ComfyUI PromptManager.

Split into domain-specific mixins for maintainability:
- PromptRoutesMixin: prompt CRUD, tags, categories, bulk ops, export
- ImageRoutesMixin: gallery, thumbnails, image serving and linking
- AdminRoutesMixin: duplicates, stats, settings, diagnostics, maintenance, backup, scan
- LoggingRoutesMixin: log management endpoints
- AutotagRoutesMixin: auto-tagging model management and tagging
"""

import asyncio
import datetime
import functools
import gzip as gzip_module
import json
import ntpath
import os
import re
from pathlib import Path

from aiohttp import web
from PIL import Image

from .prompts import PromptRoutesMixin
from .images import ImageRoutesMixin
from .admin import AdminRoutesMixin
from .logging_routes import LoggingRoutesMixin
from .autotag_routes import AutotagRoutesMixin
from .lora_integration import LoraIntegrationMixin

try:
    from ...database.operations import PromptDatabase
    from ...utils.logging_config import get_logger
except ImportError:
    import sys

    sys.path.insert(
        0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )
    from database.operations import PromptDatabase
    from utils.logging_config import get_logger


def _get_project_root():
    """Get the project root directory (3 levels up from py/api/__init__.py)."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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


def _resolve_static_file(root, filepath):
    """Real path of ``filepath`` under ``root``, or None when it must not be served.

    The request path is untrusted. It is rejected when it is empty, carries a
    ``..`` segment, is absolute on any platform (a leading slash or backslash,
    a drive letter such as ``C:`` or a UNC ``\\\\server\\share`` prefix all
    re-anchor ``os.path.join`` on Windows), has an extension outside
    STATIC_MIME_TYPES, or resolves (symlinks included) outside ``root``.
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
    if os.path.splitext(filepath)[1].lower() not in STATIC_MIME_TYPES:
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

    response.body = compressed
    response.headers["Content-Encoding"] = "gzip"
    response.headers["Vary"] = "Accept-Encoding"
    return response


class PromptManagerAPI(
    PromptRoutesMixin,
    ImageRoutesMixin,
    AdminRoutesMixin,
    LoggingRoutesMixin,
    AutotagRoutesMixin,
    LoraIntegrationMixin,
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

    def __init__(self):
        """Initialize the PromptManager API with database connection and cleanup."""
        self.logger = get_logger("prompt_manager.api")
        self.logger.info("Initializing PromptManager API")

        self.db = PromptDatabase()
        self._cached_output_dir = None  # Lazy-cached by _find_comfyui_output_dir()
        self._html_cache = {}  # Cached HTML file contents keyed by path
        self._gallery_cache = {}  # dict: path_str -> (files, timestamp)
        self._gallery_cache_ttl = 30  # Cache TTL in seconds
        # Single-flight locks for long jobs, keyed by job family; see
        # _acquire_long_job(). Created lazily so no lock binds to an event
        # loop before the server's loop exists.
        self._long_jobs: dict = {}
        # The current output scan (py/api/scan_job.ScanJob), if one was started.
        self._scan_job = None

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

    def invalidate_gallery_cache(self):
        """Invalidate the cached gallery file listing.

        Called by the image monitor when new files are detected.
        """
        self._gallery_cache = {}

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

        @routes.get("/prompt_manager/web")
        async def serve_web_ui(request):
            return self._serve_html_page("index.html", "Web UI", cache=False)

        @routes.get("/prompt_manager/gallery.html")
        async def serve_gallery_ui(request):
            return self._serve_html_page("metadata.html", "Gallery")

        @routes.get("/prompt_manager/admin")
        async def serve_admin_ui(request):
            return self._serve_html_page("admin.html", "Admin UI")

        @routes.get("/prompt_manager/gallery")
        async def serve_gallery_admin_ui(request):
            return self._serve_html_page("gallery.html", "Gallery")

        # ── Static file serving ───────────────────────────────────────

        @routes.get("/prompt_manager/lib/{filepath:.*}")
        async def serve_lib_static(request):
            """Serve static library files (JS, CSS) from web/lib directory."""
            return self._serve_static_file("lib", request.match_info.get("filepath"))

        @routes.get("/prompt_manager/js/{filepath:.*}")
        async def serve_js_static(request):
            """Serve static JavaScript files from web/js directory."""
            return self._serve_static_file("js", request.match_info.get("filepath"))

        # ── Register domain-specific routes from mixins ───────────────

        self._register_prompt_routes(routes)
        self._register_image_routes(routes)
        self._register_admin_routes(routes)
        self._register_logging_routes(routes)
        self._register_autotag_routes(routes)
        self._register_lora_routes(routes)

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

    def _serve_static_file(self, subdir, filepath):
        """Serve ``web/<subdir>/<filepath>``; 403 outside it, 404 when missing."""
        root = os.path.join(_get_project_root(), "web", subdir)
        file_path = _resolve_static_file(root, filepath)
        if file_path is None:
            return web.Response(text="Forbidden", status=403)
        if not os.path.isfile(file_path):
            return web.Response(text="Not Found", status=404)
        ext = os.path.splitext(file_path)[1].lower()
        with open(file_path, "rb") as f:
            content = f.read()
        return web.Response(body=content, content_type=STATIC_MIME_TYPES[ext])

    def _serve_html_page(self, filename, title, cache=True):
        """Serve ``web/<filename>``; 404 when missing, 500 when unreadable."""
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
            return web.Response(text=content, content_type="text/html", charset="utf-8")
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
        """Add url and thumbnail_url to a flat list of image dicts."""
        from urllib.parse import quote as url_quote

        output_dirs = self._get_all_output_dirs()

        for image in images:
            image_path_str = image.get("image_path", "")
            if not image_path_str:
                continue

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

                    # Check for thumbnail
                    rel_no_ext = rel_path.with_suffix("")
                    thumb_rel = (
                        f"thumbnails/{rel_no_ext.as_posix()}_thumb{rel_path.suffix}"
                    )
                    thumb_abs = output_path / thumb_rel
                    if thumb_abs.exists():
                        image["thumbnail_url"] = (
                            "/prompt_manager/images/serve/"
                            f"{url_quote(thumb_rel, safe='/')}"
                        )
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
        """Recursively clean NaN values from nested data structures."""
        if isinstance(obj, dict):
            return {key: self._clean_nan_recursive(value) for key, value in obj.items()}
        elif isinstance(obj, list):
            return [self._clean_nan_recursive(item) for item in obj]
        elif isinstance(obj, float) and str(obj) == "nan":
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
        from ..config import GalleryConfig

        configured = list(GalleryConfig.MONITORING_DIRECTORIES)
        output_dirs = []
        for d in configured:
            ok, reason = GalleryConfig.validate_gallery_root(d)
            if ok:
                output_dirs.append(Path(os.path.realpath(d)))
            else:
                self.logger.warning(f"Ignoring configured gallery root: {reason}")

        if output_dirs:
            return output_dirs

        if configured:
            # Every configured root was rejected: never let auto-detection
            # resurrect it, serve ComfyUI's own output directory instead.
            return self._comfyui_output_dir_list()

        fallback = self._find_comfyui_output_dir()
        return [Path(fallback)] if fallback else []

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
        """Extract ComfyUI workflow metadata from PNG image files."""
        try:
            with Image.open(image_path) as img:
                metadata = {}
                if hasattr(img, "text"):
                    for key, value in img.text.items():
                        metadata[key] = value
                return metadata
        except Exception as e:
            self.logger.error(f"Error reading {image_path}: {e}")
            return {}

    def _parse_comfyui_prompt(self, metadata):
        """Parse ComfyUI and A1111 prompt data from metadata."""
        result = {
            "prompt": None,
            "workflow": None,
            "parameters": {},
            "positive_prompt": None,
            "negative_prompt": None,
        }

        # Check for A1111 style parameters first (like parse-metadata.py)
        if "parameters" in metadata:
            params = metadata["parameters"]
            lines = params.splitlines()
            if lines:
                result["positive_prompt"] = lines[0].strip()
            for line in lines:
                if line.lower().startswith("negative prompt:"):
                    result["negative_prompt"] = line.split(":", 1)[1].strip()
                    break
            # Store raw parameters too
            result["parameters"]["parameters"] = params

        # If no A1111 format found, proceed with ComfyUI parsing
        if result["positive_prompt"] is None:
            # Check for direct prompt field
            if "prompt" in metadata:
                try:
                    prompt_data = json.loads(metadata["prompt"])
                    result["prompt"] = prompt_data
                except json.JSONDecodeError:
                    result["prompt"] = metadata["prompt"]

            # Check for workflow
            if "workflow" in metadata:
                try:
                    workflow_data = json.loads(metadata["workflow"])
                    result["workflow"] = workflow_data
                except json.JSONDecodeError:
                    result["workflow"] = metadata["workflow"]

            # Check for other common ComfyUI fields
            common_fields = [
                "positive",
                "negative",
                "steps",
                "cfg",
                "sampler",
                "scheduler",
                "seed",
            ]
            for field in common_fields:
                if field in metadata:
                    try:
                        result["parameters"][field] = json.loads(metadata[field])
                    except json.JSONDecodeError:
                        result["parameters"][field] = metadata[field]

        return result

    def _extract_readable_prompt(self, parsed_data):
        """Extract human-readable prompt text from ComfyUI/A1111 data."""

        def safe_to_string(value):
            if isinstance(value, str):
                return value
            elif isinstance(value, list):
                return " ".join(str(item) for item in value if item)
            elif value is not None:
                return str(value)
            return None

        # First check if we already extracted a positive prompt (A1111 format)
        if parsed_data.get("positive_prompt"):
            return parsed_data["positive_prompt"]

        # Check if prompt is already a string
        if isinstance(parsed_data.get("prompt"), str):
            return parsed_data["prompt"]

        # Check if prompt is a simple value that can be converted
        if parsed_data.get("prompt") and not isinstance(
            parsed_data.get("prompt"), dict
        ):
            return safe_to_string(parsed_data["prompt"])

        prompt_data = parsed_data.get("prompt")
        if isinstance(prompt_data, dict):
            # Use the enhanced logic from parse-metadata.py
            positive_prompt = self._extract_positive_prompt_from_comfyui_data(
                prompt_data
            )
            if positive_prompt:
                return positive_prompt

        # Check workflow data if available
        workflow_data = parsed_data.get("workflow")
        if isinstance(workflow_data, dict):
            positive_prompt = self._extract_positive_prompt_from_comfyui_data(
                workflow_data
            )
            if positive_prompt:
                return positive_prompt

        # Check parameters for positive prompt
        if parsed_data.get("parameters", {}).get("positive"):
            return safe_to_string(parsed_data["parameters"]["positive"])

        return None

    def _get_node_inputs(self, node):
        """Safely get inputs from a node, handling both dict and list formats.

        Old format: inputs is a dict with direct key-value pairs
            inputs = {"text": "my prompt", "seed": 123}

        New format: inputs is a list of connection objects
            inputs = [
                {"name": "text", "type": "STRING", "link": null,
                 "widget": {"name": "text"}},
                {"name": "clip", "type": "CLIP", "link": 11}
            ]
        """
        if not isinstance(node, dict):
            return {}

        inputs = node.get("inputs", {})

        # If inputs is already a dict, return it
        if isinstance(inputs, dict):
            return inputs

        # If inputs is a list, convert to dict format
        if isinstance(inputs, list):
            inputs_dict = {}
            for input_item in inputs:
                if isinstance(input_item, dict) and "name" in input_item:
                    name = input_item["name"]
                    inputs_dict[name] = input_item
            return inputs_dict

        return {}

    def _find_text_in_node(self, node):
        """Try to find text content in a node using various strategies.

        Handles both old and new workflow formats.
        """
        if not isinstance(node, dict):
            return None

        # Strategy 1: Check normalized inputs for 'text' field
        inputs = self._get_node_inputs(node)
        if "text" in inputs and isinstance(inputs["text"], str):
            return inputs["text"]

        # Strategy 2: For text encoder nodes, check widgets_values
        class_type = node.get("class_type", node.get("type", ""))
        text_encoder_types = [
            "CLIPTextEncode",
            "CLIPTextEncodeSDXL",
            "CLIPTextEncodeSDXLRefiner",
            "CLIPTextEncodeFlux",
            "PromptManager",
            "PromptManagerText",
            "BNK_CLIPTextEncoder",
            "Text Encoder",
            "CLIP Text Encode",
        ]

        if any(
            encoder_type.lower() in class_type.lower()
            for encoder_type in text_encoder_types
        ):
            widgets_values = node.get("widgets_values", [])
            if widgets_values and len(widgets_values) > 0:
                if isinstance(widgets_values[0], str) and widgets_values[0].strip():
                    return widgets_values[0]

        return None

    def _extract_positive_prompt_from_comfyui_data(self, data):
        """Extract positive prompt from ComfyUI data.

        Handles both old and new formats.
        """
        if not isinstance(data, dict):
            return None

        # Build nodes dictionary
        nodes_by_id = {}
        if "nodes" in data:
            # Handle nodes array format
            for node in data["nodes"]:
                if isinstance(node, dict):
                    nid = node.get("id")
                    if nid is not None:
                        nodes_by_id[nid] = node
        else:
            # Handle flat dictionary format (node_id -> node_data)
            for nid_str, node in data.items():
                try:
                    nid = int(nid_str)
                except (ValueError, TypeError):
                    nid = nid_str
                if isinstance(node, dict):
                    if "id" in node:
                        nid = node["id"]
                    nodes_by_id[nid] = node

        if not nodes_by_id:
            return None

        # First, try to find positive/negative connection pattern
        pos_id = None
        for node in nodes_by_id.values():
            if isinstance(node, dict):
                inputs = self._get_node_inputs(node)
                if "positive" in inputs and "negative" in inputs:
                    try:
                        pos_input = inputs["positive"]
                        if isinstance(pos_input, list) and len(pos_input) > 0:
                            pos_id = int(pos_input[0])
                            break
                    except (ValueError, TypeError, IndexError):
                        continue

        # Get text from the positive node
        if pos_id is not None and pos_id in nodes_by_id:
            text_val = self._find_text_in_node(nodes_by_id[pos_id])
            if text_val:
                return text_val

        # Fallback: find any text encoder node with text content
        text_nodes = []
        for node in nodes_by_id.values():
            if isinstance(node, dict):
                text_val = self._find_text_in_node(node)
                if text_val:
                    # Try to determine if this is positive or negative
                    node_title = node.get("title", "").lower()
                    if "neg" not in node_title and "negative" not in node_title:
                        # Prioritize non-negative prompts
                        text_nodes.insert(0, text_val)
                    else:
                        text_nodes.append(text_val)

        # Return the first positive-looking prompt
        if text_nodes:
            return text_nodes[0]

        return None
