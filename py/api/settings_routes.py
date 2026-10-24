"""Settings API routes: GET and POST /prompt_manager/settings (#220).

A save validates the payload, commits it to config.json through the config
store and publishes it at runtime inside that commit; a save that cannot be
written changes nothing at runtime and says so. ``_config_commit_failure``
is shared with the LoRA integration's save.

GET also reports the image sources and the monitor (``sources.py``). A save
that changes the sources runs as a ``source_change`` registry hold, so it is
refused while a record-changing job (a scan) runs. ``monitoring_enabled``
takes effect at the next restart. ``POST /prompt_manager/settings/validate-source``
checks one path and writes nothing.
"""

import contextlib
import os

from aiohttp import web

from ..config_store import (
    ConfigUnreadable,
    RevisionConflict,
    get_config_store,
)
from . import sources
from . import thumbnail_encode
from . import thumbnail_settings
from .admin import _current_image_monitor
from .job_routes import busy_response
from .jobs import JobBusy

try:
    from ...utils.validators import validate_result_timeout, validate_worker_threads
except ImportError:
    from utils.validators import validate_result_timeout, validate_worker_threads


# Settings owned by POST /settings: config attribute -> its keys in config.json.
_SETTINGS_FILE_KEYS = {
    "RESULT_TIMEOUT": ("web_ui", "result_timeout"),
    "WEBUI_DISPLAY_MODE": ("web_ui", "webui_display_mode"),
    "INFINITE_SCROLL": ("web_ui", "infinite_scroll"),
    "WORKER_THREADS": ("performance", "worker_threads"),
    "MONITORING_ENABLED": ("gallery", "monitoring", "enabled"),
    "SAFETY_BACKUP": ("maintenance", "safety_backup"),
    "THUMBNAIL_FORMAT": ("thumbnails", "format"),
    "THUMBNAIL_QUALITY": ("thumbnails", "quality"),
    "THUMBNAIL_EDGE": ("thumbnails", "edge"),
}
# Attributes that live on GalleryConfig; the others on PromptManagerConfig.
_GALLERY_SETTINGS = frozenset({"MONITORING_ENABLED"})
SOURCE_CHANGE_JOB_KIND = "source_change"
SOURCE_CHANGE_JOB_TITLE = "Source change"


def _source_identity(path):
    """The case-folded spelling two roots are the same directory by (Windows)."""
    return os.path.normcase(path)


def _stat_identity(path):
    """``(st_dev, st_ino)`` of an existing directory, or None.

    Catches case variants on a case-insensitive filesystem whose spelling
    normcase keeps (macOS).
    """
    try:
        stat = os.stat(path)
    except (OSError, ValueError):
        return None
    return (stat.st_dev, stat.st_ino) if stat.st_ino else None


# Refusals that a reconnected drive or folder can lift.
_RECONNECTABLE = frozenset({"not-found", "not-directory"})


def _root_key(root):
    """The directory a configured root names (resolved), else its spelling."""
    from ..config import GalleryConfig

    try:
        return GalleryConfig.resolve_gallery_root(root)
    except (OSError, TypeError, ValueError, AttributeError):
        return root


def _request_error(message):
    return {"code": "invalid-request", "error": message}


def _invalid_source(index, entry, code, reason):
    """The 400 body for the source at ``index`` that cannot be used.

    Names it by its public spelling (computed as text: a refused path is
    never resolved), so the page can mark that row and say why.
    """
    source = sources.public_spelling(entry)
    advice = (
        "Remove it or reconnect it, then save."
        if code in _RECONNECTABLE
        else "Remove it, then save."
    )
    return {
        "code": "invalid-source",
        "source": source,
        "index": index,
        "reason": reason,
        "error": f"{source} can't be used: {reason}. {advice}",
    }


def _section(config, name):
    """``config[name]`` as a fresh dict (a non-object section starts empty)."""
    value = config.get(name)
    return dict(value) if isinstance(value, dict) else {}


def _settings_mutation(updates, new_roots):
    """The config commit for a settings save: only the submitted keys change.

    Every other section and key (integrations and the CivitAI key, database,
    unknown sections) is kept as it is on disk.
    """

    def mutate(config):
        for name, value in updates.items():
            *sections, key = _SETTINGS_FILE_KEYS[name]
            _set_nested(config, sections, key, value)
        if new_roots is not None:
            gallery = _section(config, "gallery")
            monitoring = _section(gallery, "monitoring")
            monitoring["directories"] = list(new_roots)
            gallery["monitoring"] = monitoring
            config["gallery"] = gallery
        return config

    return mutate


def _set_nested(config, sections, key, value):
    """``config[s1][s2]...[key] = value``, each section a fresh dict."""
    if not sections:
        config[key] = value
        return
    merged = _section(config, sections[0])
    _set_nested(merged, sections[1:], key, value)
    config[sections[0]] = merged


async def _read_json(request):
    """Parse a JSON object body through the package's capped reader."""
    from . import _read_json_body

    return await _read_json_body(request)


class SettingsRoutesMixin:
    """Mixin providing the settings read and save endpoints."""

    def _register_settings_routes(self, routes):
        @routes.get("/prompt_manager/settings")
        async def get_settings_route(request):
            return await self.get_settings(request)

        @routes.post("/prompt_manager/settings")
        async def save_settings_route(request):
            return await self.save_settings(request)

        @routes.post("/prompt_manager/settings/validate-source")
        async def validate_source_route(request):
            return await self.validate_source(request)

    async def get_settings(self, request):
        """Get current settings."""
        try:
            from ..config import PromptManagerConfig, GalleryConfig

            root_paths = [
                self._public_path(d) for d in GalleryConfig.MONITORING_DIRECTORIES
            ]
            revision = await self._run_in_executor(self._config_revision)
            source_status, monitor = await self._run_in_executor(self._source_status)
            # #317: which thumbnail formats can be written (a cached probe).
            encoders = await self._run_in_executor(thumbnail_encode.encoder_status)
            return web.json_response(
                {
                    "success": True,
                    **revision,
                    "sources": source_status,
                    "monitor": monitor,
                    "thumbnail_encoders": encoders,
                    "settings": {
                        "monitoring_enabled": GalleryConfig.MONITORING_ENABLED,
                        "result_timeout": PromptManagerConfig.RESULT_TIMEOUT,
                        "webui_display_mode": PromptManagerConfig.WEBUI_DISPLAY_MODE,
                        "infinite_scroll": PromptManagerConfig.INFINITE_SCROLL,
                        "worker_threads": PromptManagerConfig.WORKER_THREADS,
                        "safety_backup": PromptManagerConfig.SAFETY_BACKUP,
                        "thumbnail_format": PromptManagerConfig.THUMBNAIL_FORMAT,
                        "thumbnail_quality": PromptManagerConfig.THUMBNAIL_QUALITY,
                        "thumbnail_edge": PromptManagerConfig.THUMBNAIL_EDGE,
                        "cpu_count": PromptManagerConfig.max_worker_threads(),
                        "gallery_root_paths": root_paths,
                        "gallery_root_path": root_paths[0] if root_paths else "",
                        "monitored_directories": [
                            self._public_path(d) for d in self._monitored_directories()
                        ],
                    },
                }
            )
        except Exception as e:
            self.logger.error(f"Get settings error: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": "Failed to get settings"},
                status=500,
            )

    def _source_status(self):
        """``(sources, monitor)`` of GET /settings (blocking)."""
        from ..config import GalleryConfig

        output_dirs, auto_detected = self._output_dirs_with_origin()
        return sources.describe_sources(
            configured=list(GalleryConfig.MONITORING_DIRECTORIES),
            output_dirs=output_dirs,
            auto_detected=auto_detected,
            monitor=_current_image_monitor(),
            enabled=GalleryConfig.MONITORING_ENABLED,
        )

    async def validate_source(self, request):
        """Check one source path ``{path}``; writes nothing.

        200 ``{success, valid, label, path, code, reason}``: a valid source
        answers its label and public path, an invalid one its code and reason
        only. A body without a string ``path`` is a 400 ``invalid-request``.
        """
        data, error_response = await _read_json(request)
        if error_response is not None:
            too_large = error_response.status == 413
            return web.json_response(
                {
                    "success": False,
                    "code": "too-large" if too_large else "invalid-request",
                    "error": (
                        "Request body too large"
                        if too_large
                        else "Request body must be a JSON object"
                    ),
                },
                status=error_response.status,
            )
        path = data.get("path")
        if not isinstance(path, str):
            return web.json_response(
                {
                    "success": False,
                    "code": "invalid-request",
                    "error": "path must be a string",
                },
                status=400,
            )
        try:
            result = await self._run_in_executor(sources.validate_source, path)
        except Exception:
            self.logger.exception("Source validation failed")
            return web.json_response(
                {"success": False, "code": None, "error": "Failed to check the source"},
                status=500,
            )
        return web.json_response({"success": True, **result})

    def _monitored_directories(self):
        """Directories the running image monitor watches, else the configured roots."""
        from ..config import GalleryConfig

        monitor = _current_image_monitor()
        if monitor is not None:
            return list(getattr(monitor, "monitored_directories", []))
        return list(GalleryConfig.MONITORING_DIRECTORIES)

    async def save_settings(self, request):
        """Save settings: validate, commit to config.json, then publish (#220).

        Nothing changes at runtime until config.json holds the new values, so
        a save that cannot be written reports ``persisted: false`` and leaves
        the running settings as they were. ``expected_revision`` (the
        ``revision`` from GET) refuses to overwrite a file changed meanwhile;
        without it the payload merges into what is on disk (legacy page).
        """
        try:
            data, error_response = await _read_json(request)
            if error_response is not None:
                return error_response

            expected_revision = data.get("expected_revision")
            if expected_revision is not None and not isinstance(expected_revision, str):
                return web.json_response(
                    {"success": False, "error": "expected_revision must be a string"},
                    status=400,
                )
            try:
                updates = self._validate_settings(data)
            except ValueError as ve:
                return web.json_response(
                    {"success": False, "error": str(ve)}, status=400
                )
            if updates.get("THUMBNAIL_FORMAT") == "jxl":
                # #317: JPEG XL only with an encoder (a cached probe).
                encoder = await self._run_in_executor(
                    thumbnail_encode.detect_jxl_encoder
                )
                if encoder is None:
                    return web.json_response(
                        {
                            "success": False,
                            "code": "jxl-unavailable",
                            "error": thumbnail_encode.JXL_UNAVAILABLE,
                        },
                        status=400,
                    )

            # Off the loop: an accepted root is resolved (filesystem access).
            new_roots, error = await self._run_in_executor(
                self._parse_gallery_roots, data
            )
            if error:
                return web.json_response({"success": False, **error}, status=400)
            roots_change = new_roots is not None and await self._run_in_executor(
                self._roots_change, new_roots
            )

            published = {}

            def publish(_config):
                # Under the commit lock: saves reach the runtime in the order
                # they were written, so the runtime ends on the last one.
                published.update(self._publish_settings(updates, new_roots))

            async with contextlib.AsyncExitStack() as stack:
                hold = None
                if roots_change:
                    try:
                        hold = await stack.enter_async_context(
                            self._jobs.hold(
                                SOURCE_CHANGE_JOB_KIND,
                                SOURCE_CHANGE_JOB_TITLE,
                                admission="records",
                            )
                        )
                    except JobBusy as busy:
                        return busy_response(busy)
                try:
                    _saved, revision = await self._run_in_executor(
                        get_config_store().commit,
                        _settings_mutation(updates, new_roots),
                        expected_revision=expected_revision,
                        publish=publish,
                    )
                except (ConfigUnreadable, RevisionConflict, OSError) as exc:
                    if hold is not None:
                        hold.finish("failed")
                    return self._config_commit_failure(exc)

            return web.json_response(
                {
                    "success": True,
                    "message": "Settings saved successfully",
                    "restart_required": bool(published["restart_required_fields"]),
                    "persisted": True,
                    "revision": revision,
                    **published,
                }
            )
        except Exception as e:
            self.logger.error(f"Save settings error: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": "Failed to save settings"},
                status=500,
            )

    def _publish_settings(self, updates, new_roots):
        """Install committed settings at runtime (inside the config commit).

        The values are already durable. A publication that fails is reported
        as ``runtime_applied: false`` with the fields that need a restart;
        nothing is retried or rolled back. Never raises.
        """
        from ..config import GalleryConfig, PromptManagerConfig

        for name, value in updates.items():
            owner = GalleryConfig if name in _GALLERY_SETTINGS else PromptManagerConfig
            setattr(owner, name, value)
        restart_fields = []
        runtime_applied = True
        if new_roots is not None:
            try:
                if self._apply_gallery_roots(new_roots):
                    restart_fields.append("gallery_root_paths")
            except Exception as e:
                self.logger.error(
                    f"Saved gallery roots could not be applied: {e}", exc_info=True
                )
                runtime_applied = False
                restart_fields.append("gallery_root_paths")
        if "MONITORING_ENABLED" in updates:
            # The monitor starts with the server: a restart applies the setting
            # unless the observer already matches it.
            if updates["MONITORING_ENABLED"] != self._observer_alive():
                restart_fields.append("monitoring_enabled")
        return {
            "runtime_applied": runtime_applied,
            "restart_required_fields": restart_fields,
        }

    @staticmethod
    def _roots_change(new_roots):
        """Whether a save changes the sources (blocking: it resolves them).

        Compared as directories, not spellings: the legacy page resends the
        roots resolved, while config.json may hold them as hand-written
        (relative, another case), and an unchanged resend is no change.
        """
        from ..config import GalleryConfig

        if new_roots is None:
            return False
        return [_root_key(root) for root in new_roots] != [
            _root_key(root) for root in GalleryConfig.MONITORING_DIRECTORIES
        ]

    @staticmethod
    def _observer_alive():
        """Whether the image monitor's observer is running now. Never raises."""
        try:
            return sources.monitor_state(_current_image_monitor())[1]
        except Exception:
            return False

    def _config_commit_failure(self, exc):
        """The response for a config commit that wrote nothing."""
        not_saved = {"success": False, "persisted": False, "runtime_applied": False}
        if isinstance(exc, ConfigUnreadable):
            self.logger.warning(f"Settings not saved: {exc}")
            return web.json_response(
                {
                    **not_saved,
                    "code": "config-unreadable",
                    "error": (
                        "config.json cannot be read, so nothing was saved. "
                        "Fix or remove the file, then save again."
                    ),
                },
                status=409,
            )
        if isinstance(exc, RevisionConflict):
            return web.json_response(
                {
                    **not_saved,
                    "code": "settings-changed",
                    "error": "Settings changed since they were loaded. Reload them.",
                    "revision": exc.current,
                },
                status=409,
            )
        self.logger.error(f"Could not write config.json: {exc}")
        return web.json_response(
            {
                **not_saved,
                "code": "config-write-failed",
                "error": "Settings could not be written to config.json.",
            },
            status=500,
        )

    @staticmethod
    def _config_revision():
        """``{"revision": ...}``, or a null revision with ``config_error``."""
        try:
            _data, revision = get_config_store().read()
        except ConfigUnreadable:
            return {"revision": None, "config_error": "config-unreadable"}
        return {"revision": revision}

    @staticmethod
    def _validate_settings(data):
        """Scalar settings of a payload as ``{CONFIG_ATTR: value}``.

        Raises ValueError with a user-facing message on the first bad value;
        nothing is applied until every value passed.
        """
        from ..config import PromptManagerConfig

        updates = {}
        if "result_timeout" in data:
            updates["RESULT_TIMEOUT"] = validate_result_timeout(data["result_timeout"])
        if "webui_display_mode" in data:
            mode = data["webui_display_mode"]
            if mode not in ("popup", "newtab"):
                raise ValueError("webui_display_mode must be 'popup' or 'newtab'")
            updates["WEBUI_DISPLAY_MODE"] = mode
        if "infinite_scroll" in data:
            if not isinstance(data["infinite_scroll"], bool):
                raise ValueError("infinite_scroll must be true or false")
            updates["INFINITE_SCROLL"] = data["infinite_scroll"]
        if "monitoring_enabled" in data:
            if not isinstance(data["monitoring_enabled"], bool):
                raise ValueError("monitoring_enabled must be true or false")
            updates["MONITORING_ENABLED"] = data["monitoring_enabled"]
        if "safety_backup" in data:
            if not isinstance(data["safety_backup"], bool):
                raise ValueError("safety_backup must be true or false")
            updates["SAFETY_BACKUP"] = data["safety_backup"]
        if "worker_threads" in data:
            updates["WORKER_THREADS"] = validate_worker_threads(
                data["worker_threads"], PromptManagerConfig.max_worker_threads()
            )
        if "thumbnail_format" in data:
            updates["THUMBNAIL_FORMAT"] = thumbnail_settings.validate_format(
                data["thumbnail_format"]
            )
        if "thumbnail_quality" in data:
            updates["THUMBNAIL_QUALITY"] = thumbnail_settings.validate_quality(
                data["thumbnail_quality"]
            )
        if "thumbnail_edge" in data:
            updates["THUMBNAIL_EDGE"] = thumbnail_settings.validate_edge(
                data["thumbnail_edge"]
            )
        return updates

    def _parse_gallery_roots(self, data):
        """Extract and validate gallery roots from a settings payload.

        Accepts ``gallery_root_paths`` (list, preferred) or the legacy single
        ``gallery_root_path`` string. Blank entries are dropped.

        Each entry passes the same checks as validate-source
        (``sources.source_problem``): the form, then containment as text,
        before anything is resolved. Blocking (an accepted root is resolved).

        Returns:
            (roots, error): ``roots`` is the validated list, or ``None`` when
            the payload carries no gallery root key; ``error`` is the coded
            body of the 400 when validation failed (``roots`` is then None).
            An unusable source is ``invalid-source`` naming its public path
            and its position in the list.
        """
        if "gallery_root_paths" in data:
            raw = data["gallery_root_paths"]
            if not isinstance(raw, list):
                return None, _request_error("gallery_root_paths must be a list")
        elif "gallery_root_path" in data:
            raw = [data["gallery_root_path"]]
        else:
            return None, None

        from ..config import GalleryConfig

        roots = []
        seen = set()
        for index, entry in enumerate(raw):
            if not isinstance(entry, str):
                return None, _request_error("Gallery root paths must be strings")
            entry = entry.strip()
            if not entry:
                continue
            code, reason = sources.source_problem(entry)
            if code is not None:
                return None, _invalid_source(index, entry, code, reason)
            root = GalleryConfig.resolve_gallery_root(entry)
            keys = {("path", _source_identity(root)), ("file", _stat_identity(root))}
            keys.discard(("file", None))
            if keys & seen:
                continue  # another spelling of a root already listed
            seen |= keys
            roots.append(root)
        return roots, None

    def _apply_gallery_roots(self, roots):
        """Install validated gallery roots; returns True when they changed.

        Another spelling of the same directories installs the resolved
        spelling and is no change (nothing to restart for).
        """
        from ..config import GalleryConfig

        if roots == list(GalleryConfig.MONITORING_DIRECTORIES):
            return False
        if not self._roots_change(roots):
            GalleryConfig.MONITORING_DIRECTORIES = roots
            return False
        GalleryConfig.MONITORING_DIRECTORIES = roots
        self._cached_output_dir = None
        self.reset_gallery_roots_cache()
        return True
