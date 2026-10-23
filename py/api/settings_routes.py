"""Settings API routes: GET and POST /prompt_manager/settings (#220).

A save validates the payload, commits it to config.json through the config
store and publishes it at runtime inside that commit; a save that cannot be
written changes nothing at runtime and says so. ``_config_commit_failure``
is shared with the LoRA integration's save.
"""

import os

from aiohttp import web

from ..config_store import (
    ConfigUnreadable,
    RevisionConflict,
    get_config_store,
)
from .admin import _current_image_monitor

try:
    from ...utils.validators import validate_result_timeout, validate_worker_threads
except ImportError:
    from utils.validators import validate_result_timeout, validate_worker_threads


# Settings owned by POST /settings: config attribute -> (section, key).
_SETTINGS_FILE_KEYS = {
    "RESULT_TIMEOUT": ("web_ui", "result_timeout"),
    "WEBUI_DISPLAY_MODE": ("web_ui", "webui_display_mode"),
    "INFINITE_SCROLL": ("web_ui", "infinite_scroll"),
    "WORKER_THREADS": ("performance", "worker_threads"),
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
            section, key = _SETTINGS_FILE_KEYS[name]
            merged = _section(config, section)
            merged[key] = value
            config[section] = merged
        if new_roots is not None:
            gallery = _section(config, "gallery")
            monitoring = _section(gallery, "monitoring")
            monitoring["directories"] = list(new_roots)
            gallery["monitoring"] = monitoring
            config["gallery"] = gallery
        return config

    return mutate


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

    async def get_settings(self, request):
        """Get current settings."""
        try:
            from ..config import PromptManagerConfig, GalleryConfig

            root_paths = [
                self._public_path(d) for d in GalleryConfig.MONITORING_DIRECTORIES
            ]
            revision = await self._run_in_executor(self._config_revision)
            return web.json_response(
                {
                    "success": True,
                    **revision,
                    "settings": {
                        "result_timeout": PromptManagerConfig.RESULT_TIMEOUT,
                        "webui_display_mode": PromptManagerConfig.WEBUI_DISPLAY_MODE,
                        "infinite_scroll": PromptManagerConfig.INFINITE_SCROLL,
                        "worker_threads": PromptManagerConfig.WORKER_THREADS,
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

            new_roots, error = self._parse_gallery_roots(data)
            if error:
                return web.json_response({"success": False, "error": error}, status=400)

            published = {}

            def publish(_config):
                # Under the commit lock: saves reach the runtime in the order
                # they were written, so the runtime ends on the last one.
                published.update(self._publish_settings(updates, new_roots))

            try:
                _saved, revision = await self._run_in_executor(
                    get_config_store().commit,
                    _settings_mutation(updates, new_roots),
                    expected_revision=expected_revision,
                    publish=publish,
                )
            except (ConfigUnreadable, RevisionConflict, OSError) as exc:
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
        from ..config import PromptManagerConfig

        for name, value in updates.items():
            setattr(PromptManagerConfig, name, value)
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
        return {
            "runtime_applied": runtime_applied,
            "restart_required_fields": restart_fields,
        }

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
        if "worker_threads" in data:
            updates["WORKER_THREADS"] = validate_worker_threads(
                data["worker_threads"], PromptManagerConfig.max_worker_threads()
            )
        return updates

    def _parse_gallery_roots(self, data):
        """Extract and validate gallery roots from a settings payload.

        Accepts ``gallery_root_paths`` (list, preferred) or the legacy single
        ``gallery_root_path`` string. Blank entries are dropped.

        Returns:
            (roots, error): ``roots`` is the validated list, or ``None`` when
            the payload carries no gallery root key; ``error`` is a message
            when validation failed (and ``roots`` is then ``None``).
        """
        if "gallery_root_paths" in data:
            raw = data["gallery_root_paths"]
            if not isinstance(raw, list):
                return None, "gallery_root_paths must be a list"
        elif "gallery_root_path" in data:
            raw = [data["gallery_root_path"]]
        else:
            return None, None

        from ..config import GalleryConfig

        roots = []
        for entry in raw:
            if not isinstance(entry, str):
                return None, "Gallery root paths must be strings"
            entry = entry.strip()
            if not entry:
                continue
            ok, reason = GalleryConfig.validate_gallery_root(entry)
            if not ok:
                return (
                    None,
                    f"Invalid gallery root '{os.path.basename(entry)}': {reason}",
                )
            roots.append(GalleryConfig.resolve_gallery_root(entry))
        return roots, None

    def _apply_gallery_roots(self, roots):
        """Install validated gallery roots; returns True when they changed."""
        from ..config import GalleryConfig

        if roots == list(GalleryConfig.MONITORING_DIRECTORIES):
            return False
        GalleryConfig.MONITORING_DIRECTORIES = roots
        self._cached_output_dir = None
        self.reset_gallery_roots_cache()
        return True
