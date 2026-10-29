"""The shareable diagnostic report (#329 System S12).

``GET /prompt_manager/system/diagnostics`` answers a report built on the
server from an explicit list of fields: the project and Viewer.js versions,
Python and the platform, the four checks of the legacy ``GET /diagnostics``
(one helper, ``admin.legacy_checks``), the database's public info, source
counts, the log configuration and statistics, the last health summary and
the configuration keys that only ``config.json`` holds. It never copies
``PromptManagerConfig.get_config()``: no path leaf is listed, and the CivitAI
key is reported only as ``has_civitai_api_key``.

Every path in the report goes through ``report_path``: relative to the
closest public anchor that is not a filesystem root, else ``"outside
ComfyUI"``, never a basename that could be a user name. As a backstop,
``scrub`` replaces any string that still looks like an absolute path, holds
the saved key or the home folder (with or without its root), and counts each
replacement in ``redacted`` (0 for a correct builder).

The report opens the database, so it takes a registry lease: it is refused
(409 ``busy``) while an ``exclusive`` job (a restore) runs, and a restore
waits for it. It is built off the event loop.
"""

import datetime
import json
import ntpath
import os
import platform
import posixpath
import re
from pathlib import Path, PurePosixPath, PureWindowsPath

from aiohttp import web

from . import admin as _admin
from .job_routes import busy_response
from .jobs import JobBusy
from .system import health_summary

try:
    from ...utils.version import project_version
except ImportError:  # loaded as the top-level ``py`` package (tests)
    from utils.version import project_version

DIAGNOSTICS_LEASE = "Diagnostics"
_ROOT = Path(__file__).resolve().parents[2]
VIEWERJS_PROVENANCE = str(_ROOT / "web" / "lib" / "viewerjs" / "provenance.json")
OUTSIDE = "outside ComfyUI"
REMOVED = "[removed]"
# The CivitAI key is searched for only when it is long enough to be a key.
MIN_KEY_LENGTH = 8
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")


def anchored_path(canonical, anchors, home, pathmod=os.path):
    """``canonical`` relative to the closest usable anchor, else ``outside ComfyUI``.

    All three are already canonical (``normcase``) in ``pathmod``'s spelling.
    A filesystem root is never usable, nor is a strict ancestor of ``home``
    (``/home``, ``C:\\Users``), whose relative paths would start with a user
    name; so a path inside the home folder needs an anchor at or below it.
    """
    flavor = PureWindowsPath if pathmod is ntpath else PurePosixPath
    target = flavor(canonical)
    home_path = flavor(home) if home else None
    usable = []
    for anchor in map(flavor, anchors):
        if anchor.parent == anchor:
            continue
        if home_path is not None and home_path != anchor:
            if home_path.is_relative_to(anchor):
                continue
        if target.is_relative_to(anchor):
            usable.append(anchor)
    if not usable:
        return OUTSIDE
    anchor = max(usable, key=lambda a: len(a.parts))
    return target.relative_to(anchor).as_posix() or "."


def _canonical(path):
    return os.path.normcase(os.path.realpath(str(path)))


def report_path(path):
    """``path`` relative to a public anchor (POSIX style), else ``outside ComfyUI``."""
    if not path:
        return ""
    from ..config import GalleryConfig

    try:
        canonical = _canonical(path)
        anchors = [_canonical(anchor) for anchor in GalleryConfig.path_anchors()]
        home = os.path.expanduser("~")
        home = _canonical(home) if home and home != "~" else None
    except (OSError, ValueError):
        return OUTSIDE
    return anchored_path(canonical, anchors, home)


def _looks_absolute(text):
    return (
        posixpath.isabs(text)
        or ntpath.isabs(text)
        or _WINDOWS_ABSOLUTE.match(text) is not None
        or text.startswith("\\\\")
    )


# The home folder is matched only as whole path parts, never inside a word.
_PART = r"(?<![\w.-]){}(?![\w.-])"
MIN_HOME_NAME = 3


def _home_patterns():
    """``(pattern, paths_only)`` for the home folder: in full, rootless, its name.

    Accepted residual risk: only this process's own home folder is known, so
    another user's home (an output folder under /home/<them> while ComfyUI
    runs as a service account) can still be named by a report path.
    """
    home = os.path.expanduser("~")
    if not home or home == "~" or len(home) < 4 or home in ("/", "\\"):
        return ()
    spellings = {home, home.replace("\\", "/")}
    relative = os.path.splitdrive(home)[1].strip("\\/")
    for spelling in (relative, relative.replace("\\", "/")):
        if "/" in spelling or "\\" in spelling:
            spellings.add(spelling)
    patterns = [
        (re.compile(_PART.format(re.escape(spelling)), re.IGNORECASE), False)
        for spelling in spellings
    ]
    name = re.split(r"[\\/]", relative)[-1] if relative else ""
    if len(name) >= MIN_HOME_NAME:
        # The bare name only inside a path ("info/x"), not a word ("INFO").
        patterns.append(
            (re.compile(_PART.format(re.escape(name)), re.IGNORECASE), True)
        )
    return tuple(patterns)


def scrub(report, key):
    """``report`` with leaked paths and the key replaced, counted in ``redacted``."""
    key = key if isinstance(key, str) and len(key) >= MIN_KEY_LENGTH else None
    homes = _home_patterns()
    count = 0

    def clean(value):
        nonlocal count
        if isinstance(value, str):
            if (
                _looks_absolute(value)
                or (key and key in value)
                or any(
                    pattern.search(value)
                    for pattern, paths_only in homes
                    if not paths_only or "/" in value or "\\" in value
                )
            ):
                count += 1
                return REMOVED
            return value
        if isinstance(value, dict):
            return {name: clean(item) for name, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [clean(item) for item in value]
        return value

    cleaned = clean(report)
    cleaned["redacted"] = int(cleaned.get("redacted") or 0) + count
    return cleaned


def viewerjs_version(path=None):
    """The pinned Viewer.js version from its provenance file, or ``unknown``."""
    try:
        with open(path or VIEWERJS_PROVENANCE, encoding="utf-8") as handle:
            version = json.load(handle).get("version")
    except (OSError, ValueError, AttributeError):
        return "unknown"
    return version if isinstance(version, str) and version else "unknown"


def public_config():
    """The configuration keys only ``config.json`` holds, from an explicit list."""
    from ..config import GalleryConfig, IntegrationConfig, PromptManagerConfig

    return {
        "database": {
            "enable_duplicate_detection": (
                PromptManagerConfig.ENABLE_DUPLICATE_DETECTION
            ),
            "enable_auto_save": PromptManagerConfig.ENABLE_AUTO_SAVE,
        },
        "web_ui": {
            "result_timeout": PromptManagerConfig.RESULT_TIMEOUT,
            "show_test_button": PromptManagerConfig.SHOW_TEST_BUTTON,
            "webui_display_mode": PromptManagerConfig.WEBUI_DISPLAY_MODE,
            "infinite_scroll": PromptManagerConfig.INFINITE_SCROLL,
        },
        "performance": {
            "max_search_results": PromptManagerConfig.MAX_SEARCH_RESULTS,
            "enable_fuzzy_search": PromptManagerConfig.ENABLE_FUZZY_SEARCH,
            "auto_backup_interval": PromptManagerConfig.AUTO_BACKUP_INTERVAL,
            "worker_threads": PromptManagerConfig.WORKER_THREADS,
        },
        "gallery": {
            "processing_delay": GalleryConfig.PROCESSING_DELAY,
            "prompt_timeout": GalleryConfig.PROMPT_TIMEOUT,
            "cleanup_interval": GalleryConfig.CLEANUP_INTERVAL,
            "auto_cleanup_missing_files": GalleryConfig.AUTO_CLEANUP_MISSING_FILES,
            "max_image_age_days": GalleryConfig.MAX_IMAGE_AGE_DAYS,
            "max_concurrent_processing": GalleryConfig.MAX_CONCURRENT_PROCESSING,
            "metadata_extraction_timeout": GalleryConfig.METADATA_EXTRACTION_TIMEOUT,
            # config.json gallery.monitoring.save_foreign_runs (#247)
            "save_foreign_runs": GalleryConfig.SAVE_FOREIGN_RUNS,
        },
        "integrations": {
            "lora_manager_enabled": bool(IntegrationConfig.LORA_MANAGER_ENABLED),
            "has_civitai_api_key": bool(IntegrationConfig.CIVITAI_API_KEY),
        },
    }


def _source_counts(sources):
    configured = sources.get("configured") or []
    effective = sources.get("effective") or []
    return {
        "configured": len(configured),
        "effective": len(effective),
        "watching": sum(1 for row in effective if row.get("watch") == "watching"),
        "unavailable": sum(1 for row in configured if row.get("valid") is False),
    }


class DiagnosticsReportMixin:
    """Mixin providing ``GET /prompt_manager/system/diagnostics``."""

    def _register_diagnostics_report_routes(self, routes):
        @routes.get("/prompt_manager/system/diagnostics")
        async def diagnostics_report_route(request):
            return await self.get_diagnostics_report(request)

    async def get_diagnostics_report(self, request):
        """The report, built off the loop under a lease (409 ``busy`` in a restore)."""
        from ..config import IntegrationConfig

        try:
            async with self._jobs.lease(DIAGNOSTICS_LEASE):
                report = await self._run_in_executor(self.build_report)
        except JobBusy as busy:
            return busy_response(busy)
        except Exception:
            self.logger.exception("The diagnostic report could not be built")
            return web.json_response(
                {
                    "success": False,
                    "code": None,
                    "error": "The diagnostic report could not be built.",
                },
                status=500,
            )
        report = scrub(report, IntegrationConfig.CIVITAI_API_KEY)
        return web.json_response(report, headers={"Cache-Control": "no-store"})

    def build_report(self):
        """The report's fields (blocking: reads the database and the log folder)."""
        db = self.db
        manager = self._get_logger_manager()
        stats = dict(manager.get_log_stats())
        stats["log_directory"] = report_path(stats.get("log_directory"))
        info = dict(db.model.get_database_info() or {})
        if "database_path" in info:
            info["database_path"] = report_path(info["database_path"])
        sources, _monitor = self._source_status()
        return {
            "success": True,
            "generated_at": datetime.datetime.now().replace(microsecond=0).isoformat(),
            "version": project_version(_ROOT),
            "python": platform.python_version(),
            "platform": {"system": platform.system(), "release": platform.release()},
            "viewerjs": viewerjs_version(VIEWERJS_PROVENANCE),
            "checks": _admin.legacy_checks(db.model.db_path, report_path),
            "database_info": info,
            "sources": _source_counts(sources),
            "logging": {"config": dict(manager.get_config()), "stats": stats},
            "health": health_summary(self._current_health_report()),
            "config": public_config(),
            "redacted": 0,
        }
