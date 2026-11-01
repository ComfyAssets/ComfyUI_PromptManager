"""The thumbnail location on the API (#320): startup, the settings payload
and the refusals while the thumbnail folder is unavailable.

The setting itself is config.json ``thumbnails.location``
(``preview_location``); the runtime state is ``preview_store``. POST
``/settings`` never changes it (409 ``location-needs-move``): the Move
thumbnails job is its only writer.
"""

import os

from aiohttp import web

from . import preview_location, preview_store

try:
    from ...utils import library_location
except ImportError:  # tests import the package top-level
    from utils import library_location

# The node folder (its web/ is served and a reinstall wipes it).
NODE_FOLDER = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)


def default_path():
    """``<data folder>/data/thumbnails`` (D7B), or None when unknown."""
    try:
        return library_location.cache_dir("thumbnails")
    except Exception:  # no data folder can be decided: no default
        return None


def location_refusal(location=None):
    """The 409 ``location-unavailable`` response while the thumbnail folder
    is unavailable, else None (thumbnail generate, convert, clear, prune)."""
    location = location or preview_store.current()
    if location.mode != preview_store.FOLDER_MODE or location.available:
        return None
    status, text = preview_location.message("location-unavailable")
    return web.json_response(
        {"success": False, "code": "location-unavailable", "error": text},
        status=status,
    )


def needs_move_response():
    status, text = preview_location.message("location-needs-move")
    return web.json_response(
        {"success": False, "code": "location-needs-move", "error": text},
        status=status,
    )


class ThumbnailLocationMixin:
    """Startup check and payloads of the thumbnail location."""

    def _all_source_roots(self):
        """Every image source of every root list: the Images roots, the
        configured sources and the extra gallery roots (as strings)."""
        from ..config import GalleryConfig, _extra_gallery_roots

        roots = [str(root) for root in self._get_all_output_dirs()]
        roots += [str(root) for root in GalleryConfig.MONITORING_DIRECTORIES]
        roots += list(_extra_gallery_roots())
        return tuple(dict.fromkeys(root for root in roots if root))

    def _thumbnail_location_context(self):
        """What a thumbnail folder is validated against (blocking)."""
        contained = []
        try:
            import folder_paths

            for name in (
                "get_output_directory",
                "get_input_directory",
                "get_temp_directory",
            ):
                getter = getattr(folder_paths, name, None)
                folder = getter() if getter is not None else None
                if folder:
                    contained.append(str(folder))
        except ImportError:
            pass
        try:
            contained.append(library_location.config_location()[1])
        except Exception:  # no data folder can be decided
            self.logger.debug("No library folder for the thumbnail check")
        setting = preview_store.setting()
        return preview_location.Context(
            roots=self._all_source_roots(),
            contained=tuple(contained),
            node_folder=NODE_FOLDER,
            default_path=default_path(),
            current_path=(
                setting.path if setting.mode == preview_store.FOLDER_MODE else None
            ),
        )

    def use_preview_location(self):
        """Put the configured location in force at once, unchecked (the
        package's import, before any request): folder mode is unavailable
        until ``start_preview_location`` has checked it, so nothing is ever
        written to the sources meanwhile. No filesystem access."""
        from ..config import PromptManagerConfig

        try:
            return preview_store.configure(
                PromptManagerConfig.THUMBNAIL_LOCATION, check=False
            )
        except Exception:
            self.logger.warning("The thumbnail location is unusable", exc_info=True)
            preview_store.mark_invalid()
            return preview_store.current()

    def start_preview_location(self):
        """Parse and check the configured location once (startup thread);
        the probe takes over. Never raises; a check that fails leaves a
        folder location unavailable (``invalid``), never the sources."""
        from ..config import PromptManagerConfig

        try:
            value = PromptManagerConfig.THUMBNAIL_LOCATION
            parsed = preview_location.parse_location(value)
            context = None
            if parsed.mode == preview_store.FOLDER_MODE:
                context = self._thumbnail_location_context()
            location = preview_store.configure(value, context=context)
        except Exception:
            self.logger.warning("The thumbnail location check failed", exc_info=True)
            if (
                preview_location.parse_location(
                    PromptManagerConfig.THUMBNAIL_LOCATION
                ).mode
                == preview_store.FOLDER_MODE
            ):
                preview_store.mark_invalid()
            return preview_store.current()
        if location.mode == preview_store.FOLDER_MODE and not location.available:
            self.logger.warning(
                "The thumbnail folder is unavailable "
                f"({location.problem or 'checking'}): originals are shown and "
                "no thumbnails are written until it is back"
            )
        return location

    def _thumbnail_location_payload(self, *, with_path):
        """``{mode, available, problem}`` plus, for the settings GET only,
        ``path`` and ``default_path`` (D6A)."""
        location = preview_store.current()
        payload = {
            "mode": location.mode,
            "available": location.available,
            "problem": location.problem,
        }
        if with_path:
            payload["path"] = location.path
            payload["default_path"] = default_path()
            # Old copies a move still has to remove (#320 Part 3).
            payload["cleanup_pending"] = len(preview_store.setting().cleanup)
        return payload
