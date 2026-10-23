"""Admin and maintenance API routes for PromptManager."""

import asyncio
import contextlib
from contextlib import closing
import datetime
import functools
import hashlib
import json
import math
import os
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

from aiohttp import web

from .prompts import thumbnail_url_for
from .jobs import (
    DEFAULT_SCAN_SCOPE,
    SCAN_GROUP_NAMES,
    SCAN_GROUPS,
    SCAN_SCOPES,
    DUPLICATE_CLEANUP_JOB_KIND,
    MAINTENANCE_JOB_KIND,
    RESTORE_JOB_KIND,
    Job,
    JobBusy,
)

try:
    from ...utils.parallel import map_parallel
except ImportError:
    from utils.parallel import map_parallel

try:
    from ...database.operations import PromptDatabase
    from ...database.pair_ingestion import ingest_resolved_pair
    from ...utils.generation_params import (
        PLACEHOLDERS,
        GenerationParamsError,
        extract_generation_params,
        parse_page_json,
    )
    from ...utils.prompt_pair_resolver import resolve_prompt_pair
except ImportError:
    from database.operations import PromptDatabase
    from database.pair_ingestion import ingest_resolved_pair
    from utils.generation_params import (
        PLACEHOLDERS,
        GenerationParamsError,
        extract_generation_params,
        parse_page_json,
    )
    from utils.prompt_pair_resolver import resolve_prompt_pair


try:
    from ...database.library import display_title
    from ...utils.hashing import generate_prompt_hash
except ImportError:
    from database.library import display_title
    from utils.hashing import generate_prompt_hash

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff")
VIDEO_EXTENSIONS = (".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v", ".wmv")
SCAN_BATCH_SIZE = 50  # Files per executor call during the output scan
MAX_SAFETY_BACKUPS = 10  # '<db>.backup_*' files kept next to the live database
MAX_SCAN_FILES = 50_000  # Upper bound on media files one scan will look at
SCAN_COUNT_KEYS = (
    "processed",
    "found",
    "added",
    "linked",
    "enriched",
    "unchanged",
    "ambiguous",
    "unresolved",
    "conflict",
    "failed",
    "pairs_added",
    "pair_links_added",
)
MAX_SCAN_DEPTH = 12  # Directory levels below an output root that are scanned
# Files the duplicate cleaner may delete: everything the scan reports, plus
# the common single-f TIFF spelling. Anything else inside the output
# directory (databases, scripts, notes) is never a "duplicate image".
DELETABLE_MEDIA_EXTENSIONS = frozenset(IMAGE_EXTENSIONS + VIDEO_EXTENSIONS + (".tif",))


def _collect_media_files(output_dirs, extensions):
    """Media files under ``output_dirs`` (blocking), bounded and symlink-free.

    Walks each root without following symlinks, skips ``thumbnails``
    directories and symlinked files, never descends more than MAX_SCAN_DEPTH
    levels and stops after MAX_SCAN_FILES files in total. Extensions match
    case-insensitively and each path is listed once.
    """
    wanted = {ext.lower() for ext in extensions}
    found = []
    seen = set()
    for output_dir in output_dirs:
        root = os.path.abspath(str(output_dir))
        if not os.path.isdir(root):
            continue
        for current, dirnames, filenames in os.walk(root, followlinks=False):
            depth = 0 if current == root else len(Path(current).relative_to(root).parts)
            dirnames[:] = sorted(
                d
                for d in dirnames
                if d != "thumbnails"
                and depth < MAX_SCAN_DEPTH
                and not os.path.islink(os.path.join(current, d))
            )
            for name in sorted(filenames):
                if os.path.splitext(name)[1].lower() not in wanted:
                    continue
                full = os.path.join(current, name)
                if os.path.islink(full) or not os.path.isfile(full):
                    continue
                key = os.path.normcase(full)
                if key in seen:
                    continue
                seen.add(key)
                found.append(Path(full))
                if len(found) >= MAX_SCAN_FILES:
                    return found
    return found


def _thumbnail_rel_path(rel_path, thumbnail_ext):
    """Relative path of the thumbnail the gallery would generate for ``rel_path``."""
    return f"thumbnails/{rel_path.with_suffix('').as_posix()}_thumb{thumbnail_ext}"


async def _read_json(request):
    """Parse a JSON object body through the package's capped reader.

    Returns:
        (data, None) on success, or (None, response) carrying a 400/413 reply.
    """
    from . import _read_json_body

    return await _read_json_body(request)


def _sse(payload):
    """Encode one server-sent event."""
    return f"data: {json.dumps(payload)}\n\n"


SCAN_JOB_KIND = "scan"
SCAN_JOB_TITLE = "Scan for metadata"
# The legacy final event type of a scan and the job state it ends in.
SCAN_FINAL_STATES = {
    "complete": "finished",
    "cancelled": "cancelled",
    "error": "failed",
}
# The scope as the scan log's first line names it (the review dialog's words).
SCAN_SCOPE_LOG = {"new": "new and unresolved files", "all": "all files"}
MIN_BATCHES_FOR_ESTIMATE = 2  # "about X min left" waits for this many batches
# Log line prefix per outcome group (spec: + added, = known, ? review, ! error,
# - no usable metadata).
SCAN_LOG_PREFIXES = {
    "added": "+",
    "known": "=",
    "review": "?",
    "errors": "!",
    "no_metadata": "-",
}
# What an outcome row says about a file: where it went (with the prompt's
# title) or why it was skipped. "error" is a file whose ingestion raised;
# None is a file without prompt metadata.
SCAN_TITLED_DETAILS = {
    "added": "Added to the Library as “{title}”",
    "linked": "Linked to “{title}”",
    "enriched": "Metadata added to “{title}”",
    "unchanged": "Already linked to “{title}”",
}
SCAN_DETAILS = {
    "added": "Added to the Library",
    "linked": "Linked to an existing prompt",
    "enriched": "Metadata added to its prompt",
    "unchanged": "Already linked to its prompt",
    "skipped": "Skipped · already linked",
    "ambiguous": "Needs review: more than one prompt could match",
    "unresolved": "Needs review: the prompt could not be traced in the workflow",
    "conflict": "Needs review: already linked to a different prompt",
    "failed": "Error: the prompt could not be stored",
    "error": "Error: the file could not be processed (see the server log)",
    "found": "Metadata found, but no prompt text to store",
    None: "No prompt metadata in the file",
}


def _plural(count, word):
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _time_left(done, total, batches, elapsed):
    """ " · about X min left" once enough batches ran, else "" (D56).

    Shared by every Activity job that reports "Processing file N of M": the
    scan counts its batches, the image tagging job each file.
    """
    if batches < MIN_BATCHES_FOR_ESTIMATE or done <= 0 or done >= total:
        return ""
    minutes = max(1, math.ceil(elapsed / done * (total - done) / 60))
    return f" · about {minutes} min left"


def _scan_status_line(done, total, batches, elapsed):
    """ "Processing file N of M", with an estimate once enough batches ran."""
    line = f"Processing file {done:,} of {total:,}"
    return line + _time_left(done, total, batches, elapsed)


def _file_size(path):
    """``path``'s size on disk, or None when it cannot be read."""
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def _with_file_size(canonical, media_file):
    """Record the file's size in ``file_info`` unless the metadata carries one.

    Stored as ``generated_images.file_size``; the "new" scan scope skips a known
    file only while its size is unchanged.
    """
    info = canonical.get("file_info")
    info = dict(info) if isinstance(info, dict) else {}
    if info.get("size") is None:
        size = _file_size(media_file)
        if size is not None:
            info["size"] = size
    if info:
        canonical["file_info"] = info


def _scan_roots(output_dirs):
    """(absolute root, public label, listing index), the most specific first.

    The index is the root's place in ``output_dirs`` (``_get_all_output_dirs``),
    the ``root_index`` the workspace listing names it by.
    """
    public_path, _ = _public_helpers()
    roots = [
        (os.path.abspath(str(d)), public_path(d), index)
        for index, d in enumerate(output_dirs)
    ]
    return sorted(roots, key=lambda root: len(root[0]), reverse=True)


def _scan_location(media_file, roots):
    """``media_file`` relative to its output root, that root's label and index."""
    full = os.path.abspath(str(media_file))
    folded = os.path.normcase(full)
    for root, label, index in roots:
        base = os.path.normcase(root).rstrip("\\/") + os.sep
        if folded.startswith(base):
            return Path(os.path.relpath(full, root)).as_posix(), label, index
    return Path(full).name, "", None


def _scan_outcome(media_file, roots, status, title=None):
    """The (group, row, log line) one scanned file records in its job."""
    if status in SCAN_GROUPS:
        group = SCAN_GROUPS[status]
    else:
        group = "errors" if status == "error" else "no_metadata"
    template = SCAN_TITLED_DETAILS.get(status) if title else None
    detail = template.format(title=title) if template else SCAN_DETAILS[status]
    path, label, index = _scan_location(media_file, roots)
    row = {"path": path, "root": label, "root_index": index, "detail": detail}
    where = f"{label}/{path}" if label else path
    return group, row, f"{SCAN_LOG_PREFIXES[group]} {where} · {detail}"


def _inspect_restore_upload(path):
    """Checks on a restore upload beyond PromptModel.verify_database_file.

    Opens the file read-only with ``PRAGMA trusted_schema=OFF`` (so nothing
    in the schema runs while it is inspected) and refuses databases that
    carry triggers or views: the prompts database never needs either, and a
    crafted upload could use them to run SQL against the live data later.

    Returns:
        None when acceptable, otherwise a human-readable reason (blocking).
    """
    uri = Path(os.path.abspath(path)).as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as e:
        return f"Invalid SQLite database: {e}"
    try:
        conn.execute("PRAGMA trusted_schema=OFF")
        rows = conn.execute(
            "SELECT type, name FROM sqlite_master "
            "WHERE type IN ('trigger', 'view') ORDER BY type, name"
        ).fetchall()
    except sqlite3.Error as e:
        return f"Invalid SQLite database: {e}"
    finally:
        conn.close()
    if rows:
        listed = ", ".join(f"{kind} {name}" for kind, name in rows[:5])
        return (
            "Database contains triggers or views, which a prompts database "
            f"never has: {listed}"
        )
    return None


def _prune_safety_backups(db_path, keep=None):
    """Delete the oldest ``<db_path>.backup_*`` files beyond ``keep`` (blocking).

    Returns the paths removed. Errors deleting a file are ignored so a
    stubborn old backup never fails the restore that just succeeded.
    """
    if keep is None:
        keep = MAX_SAFETY_BACKUPS
    db_path = os.path.abspath(db_path)
    directory = os.path.dirname(db_path)
    prefix = os.path.basename(db_path) + ".backup_"
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    candidates = [
        os.path.join(directory, name)
        for name in names
        if name.startswith(prefix) and os.path.isfile(os.path.join(directory, name))
    ]
    candidates.sort(key=lambda p: (os.path.getmtime(p), p))
    excess = candidates[: max(0, len(candidates) - keep)]
    removed = []
    for path in excess:
        try:
            os.remove(path)
            removed.append(path)
        except OSError:
            continue
    return removed


def _public_helpers():
    """The package's public-path helpers (imported lazily to avoid a cycle)."""
    from . import _public_error, _public_path

    return _public_path, _public_error


def _diagnose_database(db_path):
    """Row counts straight from the database file (small blocking query)."""
    _public_path, _public_error = _public_helpers()
    if not os.path.exists(db_path):
        return {
            "status": "error",
            "message": f"Database file not found: {os.path.basename(db_path)}",
        }
    try:
        with closing(sqlite3.connect(db_path)) as conn:
            prompt_count = conn.execute("SELECT COUNT(*) FROM prompts").fetchone()[0]
            has_images_table = (
                conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='generated_images'"
                ).fetchone()
                is not None
            )
            image_count = 0
            if has_images_table:
                image_count = conn.execute(
                    "SELECT COUNT(*) FROM generated_images"
                ).fetchone()[0]
        return {
            "status": "ok",
            "prompt_count": prompt_count,
            "has_images_table": has_images_table,
            "image_count": image_count,
        }
    except Exception as e:
        return {"status": "error", "message": f"Database error: {_public_error(e)}"}


def _diagnose_dependencies():
    """Presence of the optional runtime dependencies."""
    dependencies = {"sqlite3": True}
    for name in ("watchdog", "PIL"):
        try:
            __import__(name)
            dependencies[name] = True
        except ImportError:
            dependencies[name] = False
    return {
        "status": "ok" if all(dependencies.values()) else "error",
        "dependencies": dependencies,
    }


def _diagnose_output_dirs(configured_dirs):
    """Configured gallery roots, ComfyUI's output dir, or relative fallbacks."""
    _public_path, _ = _public_helpers()
    candidates = list(configured_dirs)
    try:
        import folder_paths

        candidates.append(folder_paths.get_output_directory())
    except ImportError:
        pass

    output_dirs = []
    seen = set()

    def add(path):
        # One entry per directory: resolve links and fold case (Windows)
        real = os.path.realpath(path)
        key = os.path.normcase(real)
        if os.path.exists(real) and key not in seen:
            seen.add(key)
            output_dirs.append(real)

    for candidate in candidates:
        if candidate:
            add(candidate)

    if not output_dirs:
        for rel in ("output", "../output", "../../output"):
            add(rel)

    return {
        "status": "ok" if output_dirs else "warning",
        "output_dirs": [_public_path(d) for d in output_dirs],
    }


def _diagnose_image_monitor():
    """Status of the running image monitor, with public directory names."""
    _public_path, _public_error = _public_helpers()
    try:
        monitor = _current_image_monitor()
        if monitor is None:
            return {"status": "error", "message": "Image monitor not initialized"}
        status = dict(monitor.get_status())
        status["monitored_directories"] = [
            _public_path(d) for d in status.get("monitored_directories", [])
        ]
        return {"status": "ok" if status.get("observer_alive") else "error", **status}
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to get monitor status: {_public_error(e)}",
        }


def _image_monitor_module():
    """The utils.image_monitor module under either package identity."""
    try:
        from ...utils import image_monitor
    except ImportError:
        from utils import image_monitor
    return image_monitor


def _current_image_monitor():
    """The running ImageMonitor singleton, or None when not started."""
    return getattr(_image_monitor_module(), "_monitor_instance", None)


class AdminRoutesMixin:
    """Mixin providing admin, diagnostics, and maintenance API endpoints."""

    def _register_admin_routes(self, routes):
        @routes.get("/prompt_manager/scan_duplicates")
        async def scan_duplicates_route(request):
            return await self.scan_duplicates_endpoint(request)

        @routes.post("/prompt_manager/scan_duplicates/stream")
        async def scan_duplicates_stream_route(request):
            return await self.scan_duplicates_stream(request)

        @routes.get("/prompt_manager/scan_duplicates/status")
        async def scan_duplicates_status_route(request):
            return await self.scan_duplicates_status(request)

        @routes.post("/prompt_manager/delete_duplicate_images")
        async def delete_duplicate_images_route(request):
            return await self.delete_duplicate_images_endpoint(request)

        @routes.post("/prompt_manager/cleanup")
        async def cleanup_duplicates_route(request):
            return await self.cleanup_duplicates_endpoint(request)

        @routes.post("/prompt_manager/maintenance")
        async def maintenance_route(request):
            return await self.run_maintenance(request)

        @routes.get("/prompt_manager/stats")
        async def get_stats_route(request):
            return await self.get_statistics(request)

        @routes.get("/prompt_manager/backup")
        async def backup_database_route(request):
            return await self.backup_database(request)

        @routes.post("/prompt_manager/restore")
        async def restore_database_route(request):
            return await self.restore_database(request)

        @routes.get("/prompt_manager/diagnostics")
        async def run_diagnostics_route(request):
            return await self.run_diagnostics(request)

        @routes.post("/prompt_manager/diagnostics/test-link")
        async def test_image_link_route(request):
            return await self.test_image_link(request)

        @routes.post("/prompt_manager/scan")
        async def scan_images_route(request):
            return await self.scan_images(request)

        @routes.post("/prompt_manager/scan/cancel")
        async def cancel_scan_route(request):
            return await self.cancel_scan(request)

        @routes.get("/prompt_manager/scan/status")
        async def scan_status_route(request):
            return await self.scan_status(request)

    LONG_JOB_DUPLICATES = "duplicates"

    async def scan_duplicates_endpoint(self, request):
        """Scan for duplicate images without removing them (single flight)."""
        job = self._acquire_long_job(self.LONG_JOB_DUPLICATES)
        if job is None:
            return self._long_job_busy_response("Duplicate scan")
        async with job:
            return await self._scan_duplicates(request)

    async def _scan_duplicates(self, request):
        try:
            duplicates = await self.find_duplicate_images()

            return web.json_response(
                {
                    "success": True,
                    "duplicates": duplicates,
                    "message": f"Found {len(duplicates)} groups of duplicate images",
                }
            )

        except Exception as e:
            self.logger.error(f"Scan duplicates error: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": "Failed to scan duplicate images"},
                status=500,
            )

    async def scan_duplicates_stream(self, request):
        """Run the duplicate scan as a background job and stream its progress.

        A request while a scan is running attaches to it. The job holds the
        duplicates lock, so a delete is refused (409) until it finishes.
        """
        job = self._duplicate_job
        if job is None or job.finished:
            guard = self._acquire_long_job(self.LONG_JOB_DUPLICATES)
            if guard is None:
                return self._long_job_busy_response("Duplicate scan or delete")
            job = Job("duplicates", "Duplicate scan")
            self._duplicate_job = job
            job.task = asyncio.ensure_future(self._run_duplicate_job(job, guard))
        return await self._stream_scan_job(request, job)

    async def scan_duplicates_status(self, request):
        """Whether a duplicate scan is running, and its latest event."""
        job = self._duplicate_job
        return web.json_response(
            {
                "success": True,
                "running": bool(job and job.running),
                "last_event": job.last_event if job else None,
            }
        )

    async def _run_duplicate_job(self, job, guard):
        loop = asyncio.get_running_loop()
        counts = {"processed": 0}

        def report(done, total):
            # Called from worker threads; hand the event to the loop.
            counts["processed"] = done
            pct = int(done / total * 100) if total else 100
            loop.call_soon_threadsafe(
                job.publish,
                {
                    "type": "progress",
                    "progress": pct,
                    "status": f"Hashing file {done}/{total}...",
                    "processed": done,
                    "found": 0,
                },
            )

        try:
            async with guard:
                job.publish(
                    {
                        "type": "progress",
                        "progress": 0,
                        "status": "Collecting media files...",
                        "processed": 0,
                        "found": 0,
                    }
                )
                output_dir = await self._run_in_executor(self._find_comfyui_output_dir)
                groups = await self._run_in_executor(
                    self._find_duplicate_images_sync, output_dir, report
                )
                job.publish(
                    {
                        "type": "complete",
                        "processed": counts["processed"],
                        "found": len(groups),
                        "duplicates": groups,
                        "message": f"Found {len(groups)} groups of duplicate images",
                    }
                )
        except Exception:
            self.logger.exception("Duplicate scan error")
            job.publish(
                {
                    "type": "error",
                    "message": (
                        "An internal error occurred. Check server logs for details."
                    ),
                }
            )
        finally:
            job.finish()

    async def cleanup_duplicates_endpoint(self, request):
        """Cleanup duplicate prompts endpoint (holds the registry: records)."""
        return await self._run_held(
            MAINTENANCE_JOB_KIND,
            "Prompt cleanup (admin page)",
            "records",
            self._cleanup_duplicates,
        )

    async def _cleanup_duplicates(self):
        try:
            removed_count = await self._run_in_executor(self.db.cleanup_duplicates)

            return web.json_response(
                {
                    "success": True,
                    "message": "Cleanup completed",
                    "duplicates_removed": removed_count,
                }
            )

        except Exception as e:
            self.logger.error(f"Cleanup error: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": "Failed to cleanup duplicates"},
                status=500,
            )

    async def find_duplicate_images(self):
        """Find duplicate media in the output directory, off the event loop."""
        output_dir = await self._run_in_executor(self._find_comfyui_output_dir)
        return await self._run_in_executor(self._find_duplicate_images_sync, output_dir)

    def _find_duplicate_images_sync(self, output_dir, progress=None):
        """Group media files under ``output_dir`` by content hash (blocking).

        Files are hashed on ``PromptManagerConfig.WORKER_THREADS`` threads.
        ``progress(done, total)`` is called from those threads about every
        percent and once at the end.
        """
        from ..config import PromptManagerConfig

        if not output_dir or not os.path.isdir(output_dir):
            self.logger.warning("ComfyUI output directory not found")
            return []

        output_path = Path(output_dir)
        extensions = IMAGE_EXTENSIONS + VIDEO_EXTENSIONS
        media_files = _collect_media_files([output_path], extensions)
        total = len(media_files)
        self.logger.info(f"Found {total} media files to analyze")
        step = max(1, total // 100)
        counter = {"done": 0}
        lock = threading.Lock()

        def describe(media_path):
            try:
                info = self._describe_media_file(media_path, output_path)
            except Exception as e:
                self.logger.error(f"Error processing file {media_path.name}: {e}")
                info = None
            with lock:
                counter["done"] += 1
                done = counter["done"]
            if done % 100 == 0:
                self.logger.info(
                    f"Processed {done}/{total} files " "for duplicate detection"
                )
            if progress and (done % step == 0 or done == total):
                progress(done, total)
            return info

        infos = map_parallel(describe, media_files, PromptManagerConfig.WORKER_THREADS)

        file_hashes = {}
        for info in infos:
            if info is not None:
                file_hashes.setdefault(info["hash"], []).append(info)

        duplicates = []
        for file_hash, images in file_hashes.items():
            if len(images) > 1:
                images.sort(key=lambda x: x["modified_time"])
                duplicates.append(
                    {"hash": file_hash, "images": images, "count": len(images)}
                )

        self.logger.info(f"Found {len(duplicates)} groups of duplicate images")
        return duplicates

    def _describe_media_file(self, media_path, output_path):
        """Hash one media file and describe it for the duplicates response."""
        stat = media_path.stat()
        rel_path = media_path.relative_to(output_path)
        extension = media_path.suffix.lower()
        is_video = extension in VIDEO_EXTENSIONS

        thumb_rel = _thumbnail_rel_path(rel_path, ".jpg" if is_video else extension)
        thumbnail_url = thumbnail_url_for(thumb_rel, output_path / thumb_rel)

        return {
            "id": str(hash(str(media_path))),
            "filename": media_path.name,
            "path": str(rel_path),
            "relative_path": str(rel_path),
            "url": f"/prompt_manager/images/serve/{rel_path.as_posix()}",
            "thumbnail_url": thumbnail_url,
            "size": stat.st_size,
            "modified_time": stat.st_mtime,
            "extension": extension,
            "media_type": "video" if is_video else "image",
            "is_video": is_video,
            "hash": self._calculate_file_hash(media_path),
        }

    def _calculate_file_hash(self, file_path):
        """Calculate SHA-256 hash of a file's content."""
        hash_sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_sha256.update(chunk)
        return hash_sha256.hexdigest()

    async def delete_duplicate_images_endpoint(self, request):
        """Delete duplicate image files from disk.

        Single flight with the duplicate scan, and a records hold on the
        registry, so no scan, restore or tagging job runs meanwhile. A body
        without paths is refused before the hold (it is no job).
        """
        data, error_response = await _read_json(request)
        if error_response is not None:
            return error_response
        image_paths = data.get("image_paths", [])
        if not image_paths or not isinstance(image_paths, list):
            return web.json_response(
                {"success": False, "error": "No image paths provided"},
                status=400,
            )
        guard = self._acquire_long_job(self.LONG_JOB_DUPLICATES)
        if guard is None:
            return self._long_job_busy_response("Duplicate scan or delete")

        async def delete():
            # No await since the check above: the lock is still free.
            async with guard:
                return await self._delete_duplicate_images(image_paths)

        return await self._run_held(
            DUPLICATE_CLEANUP_JOB_KIND,
            "Duplicate image delete (gallery page)",
            "records",
            delete,
        )

    async def _delete_duplicate_images(self, image_paths):
        try:
            output_dir = await self._run_in_executor(self._find_comfyui_output_dir)
            result = await self._run_in_executor(
                self._delete_duplicate_images_sync, image_paths, output_dir
            )
            if result["deleted_count"]:
                # The cached listing would keep (and count) the deleted files.
                self.invalidate_gallery_cache()

            response_data = {
                "success": True,
                "deleted_count": result["deleted_count"],
                "failed_count": result["failed_count"],
                "message": f"Deleted {result['deleted_count']} files successfully",
            }
            if result["failed_count"] > 0:
                response_data["failed_files"] = result["failed_files"]
                response_data["message"] += f", {result['failed_count']} failed"

            return web.json_response(response_data)

        except Exception as e:
            self.logger.error(f"Delete duplicate images error: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": "Failed to delete duplicate images"},
                status=500,
            )

    def _delete_duplicate_images_sync(self, image_paths, output_dir):
        """Delete the given files (absolute or output-relative) within
        ``output_dir``."""
        result = {"deleted_count": 0, "failed_count": 0, "failed_files": []}
        output_path = Path(output_dir) if output_dir else None

        for image_path in image_paths:
            if output_path is None:
                failure = "output directory not found"
            else:
                try:
                    failure = self._delete_one_duplicate(str(image_path), output_path)
                except Exception as e:
                    self.logger.error(f"Error deleting file {image_path}: {e}")
                    failure = self._public_error(e)

            if failure is None:
                result["deleted_count"] += 1
            else:
                result["failed_count"] += 1
                result["failed_files"].append(f"{image_path} ({failure})")

        return result

    def _delete_one_duplicate(self, image_path, output_path):
        """Delete one file and its thumbnail; return a failure reason or None."""
        file_path = Path(image_path)
        if not file_path.is_absolute():
            file_path = output_path / file_path

        try:
            rel_path = file_path.resolve().relative_to(output_path.resolve())
        except ValueError:
            self.logger.warning(
                f"Attempted to delete file outside output directory: {image_path}"
            )
            return "outside output directory"

        if file_path.suffix.lower() not in DELETABLE_MEDIA_EXTENSIONS:
            return "not a media file"
        if "thumbnails" in rel_path.parts:
            return "thumbnails are managed by the gallery"

        if not file_path.is_file():
            return "file not found"

        os.remove(file_path)
        self.logger.info(f"Deleted duplicate image: {image_path}")

        thumbnail_path = output_path / _thumbnail_rel_path(rel_path, file_path.suffix)
        try:
            if thumbnail_path.exists():
                os.remove(thumbnail_path)
                self.logger.debug(f"Deleted associated thumbnail: {thumbnail_path}")
        except OSError as e:
            self.logger.warning(f"Could not delete thumbnail for {image_path}: {e}")
        return None

    async def get_statistics(self, request):
        """Get database statistics."""
        try:
            stats = await self._run_in_executor(self.db.get_statistics)

            return web.json_response({"success": True, "stats": stats})

        except Exception as e:
            self.logger.error(f"Stats error: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": "Failed to get statistics"},
                status=500,
            )

    async def run_diagnostics(self, request):
        """Run comprehensive system diagnostics and health checks."""
        try:
            from ..config import GalleryConfig

            results = {
                "database": _diagnose_database(self.db.model.db_path),
                "dependencies": _diagnose_dependencies(),
                "comfyui_output": _diagnose_output_dirs(
                    GalleryConfig.MONITORING_DIRECTORIES
                ),
                "image_monitor": _diagnose_image_monitor(),
            }
            return web.json_response({"success": True, "diagnostics": results})

        except Exception as e:
            self.logger.error(f"Diagnostics error: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": "Diagnostics failed"}, status=500
            )

    async def test_image_link(self, request):
        """Test creating an image link."""
        try:
            data, error_response = await _read_json(request)
            if error_response is not None:
                return error_response
            prompt_id = data.get("prompt_id")
            if not prompt_id:
                return web.json_response(
                    {"success": False, "error": "prompt_id is required"}, status=400
                )

            # The client never chooses the stored path: a synthetic marker
            # under the output directory stands in for a generated image.
            output_dir = await self._run_in_executor(self._find_comfyui_output_dir)
            image_path = self._test_link_marker_path(output_dir)
            payload = await self._link_test_image(str(prompt_id), image_path)
            return web.json_response(payload)

        except Exception as e:
            self.logger.error(f"Test link error: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": "Test link failed"}, status=500
            )

    TEST_LINK_MARKER = "prompt_manager_test_link.png"

    @classmethod
    def _test_link_marker_path(cls, output_dir):
        """Path recorded by the test-link diagnostic: a marker under ``output_dir``
        (relative, when no output directory is known)."""
        if output_dir:
            return os.path.join(str(output_dir), cls.TEST_LINK_MARKER)
        return cls.TEST_LINK_MARKER

    async def _link_test_image(self, prompt_id, image_path):
        """Link a synthetic image record to ``prompt_id``; returns the envelope."""
        test_metadata = {
            "file_info": {"size": 1024000, "dimensions": [512, 512], "format": "PNG"},
            "workflow": {"test": True},
            "prompt": {"test_prompt": "This is a test image"},
        }
        try:
            image_id = await self._run_in_executor(
                self.db.link_image_to_prompt,
                prompt_id=prompt_id,
                image_path=image_path,
                metadata=test_metadata,
            )
        except Exception as e:
            return {
                "success": False,
                "result": {
                    "status": "error",
                    "message": f"Failed to create test link: {self._public_error(e)}",
                },
            }
        return {
            "success": True,
            "result": {
                "status": "ok",
                "image_id": image_id,
                "message": f"Test image linked successfully with ID {image_id}",
            },
        }

    DEFAULT_MAINTENANCE_OPERATIONS = (
        "cleanup_duplicates",
        "vacuum",
        "cleanup_orphaned_images",
    )
    # Operations that only read; a run of only these holds the registry as
    # "read", every other run as "records".
    READ_ONLY_MAINTENANCE_OPERATIONS = frozenset(
        {"check_hash_duplicates", "statistics", "check_consistency"}
    )

    async def run_maintenance(self, request):
        """Perform database maintenance operations and report each outcome."""
        try:
            data = {}
            if request.content_type == "application/json":
                data, error_response = await _read_json(request)
                if error_response is not None:
                    return error_response
            operations = data.get(
                "operations", list(self.DEFAULT_MAINTENANCE_OPERATIONS)
            )
        except Exception as e:
            self.logger.error(f"Maintenance error: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": "Maintenance failed"}, status=500
            )
        writes = not (
            isinstance(operations, list)
            and all(
                isinstance(name, str) and name in self.READ_ONLY_MAINTENANCE_OPERATIONS
                for name in operations
            )
        )
        return await self._run_held(
            MAINTENANCE_JOB_KIND,
            "Maintenance (admin page)",
            "records" if writes else "read",
            lambda: self._run_maintenance(operations),
        )

    async def _run_maintenance(self, operations):
        try:
            results = await self._run_in_executor(
                self._run_maintenance_operations, operations
            )
            all_successful = all(r.get("success", False) for r in results.values())

            return web.json_response(
                {
                    "success": True,
                    "operations_completed": len(results),
                    "all_successful": all_successful,
                    "results": results,
                    "message": (
                        f"Maintenance completed: {len(results)} operations processed"
                    ),
                }
            )

        except Exception as e:
            self.logger.error(f"Maintenance error: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": "Maintenance failed"}, status=500
            )

    def _run_maintenance_operations(self, operations):
        """Run each known operation in order; unknown names are ignored (blocking)."""
        results = {}
        for name in operations:
            runner = self._maintenance_runners().get(name)
            if runner is None:
                continue
            try:
                results[name] = {"success": True, **runner()}
            except Exception as e:
                results[name] = {
                    "success": False,
                    "error": self._public_error(e),
                    "message": f"Failed to run {name.replace('_', ' ')}",
                }
        return results

    def _maintenance_runners(self):
        """Map of operation name to a callable returning that operation's result."""
        db = self.db

        def count_result(count, noun):
            return {"removed_count": count, "message": f"Removed {count} {noun}"}

        def vacuum():
            db.model.vacuum_database()
            return {"message": "Database vacuum completed successfully"}

        return {
            "cleanup_duplicates": lambda: count_result(
                db.cleanup_duplicates(), "duplicate prompts"
            ),
            "vacuum": vacuum,
            "cleanup_orphaned_images": lambda: count_result(
                db.cleanup_missing_images(), "orphaned image records"
            ),
            "check_hash_duplicates": lambda: self._hash_duplicates_result(
                db.check_hash_duplicates()
            ),
            "statistics": lambda: {
                "info": self._public_database_info(db.model.get_database_info()),
                "message": "Database statistics retrieved",
            },
            "prune_orphaned_prompts": lambda: count_result(
                db.prune_orphaned_prompts(),
                "orphaned prompts (prompts with no linked images, "
                "excluding protected prompts)",
            ),
            "check_consistency": lambda: self._consistency_result(
                db.check_consistency()
            ),
        }

    def _public_database_info(self, info):
        """``get_database_info()`` with the absolute database_path made public."""
        info = dict(info or {})
        if info.get("database_path"):
            info["database_path"] = self._public_path(info["database_path"])
        return info

    @staticmethod
    def _hash_duplicates_result(groups):
        return {
            "duplicate_hashes": len(groups),
            "message": f"Found {len(groups)} duplicate hash groups",
        }

    @staticmethod
    def _consistency_result(issues):
        return {
            "issues_found": len(issues),
            "issues": issues[:10],
            "message": f"Found {len(issues)} consistency issues",
        }

    async def backup_database(self, request):
        """Download a consistent copy of the prompts database (WAL included)."""
        try:
            model = self.db.model
            db_path = model.db_path

            if not os.path.exists(db_path):
                return web.json_response(
                    {"success": False, "error": "Database file not found"}, status=404
                )

            fd, temp_path = tempfile.mkstemp(suffix=".db")
            os.close(fd)
            try:
                ok = await self._run_in_executor(model.backup_database, temp_path)
                if not ok:
                    return web.json_response(
                        {"success": False, "error": "Failed to create database backup"},
                        status=500,
                    )
                # Read once (the file is deleted right after) rather than
                # streaming so the temp file never outlives the request.
                with open(temp_path, "rb") as fh:
                    file_data = fh.read()
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)

            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"prompts_backup_{timestamp}.db"

            return web.Response(
                body=file_data,
                content_type="application/octet-stream",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "Content-Length": str(len(file_data)),
                },
            )

        except Exception as e:
            self.logger.error(f"Backup error: {e}", exc_info=True)
            return web.json_response(
                {
                    "success": False,
                    "error": f"Failed to backup database: {self._public_error(e)}",
                },
                status=500,
            )

    async def restore_database(self, request):
        """Restore the prompts database from an uploaded SQLite file.

        The upload is streamed to a temp file (capped at restore_max_bytes),
        verified with PromptModel.verify_database_file and only then copied
        over the live database, which is backed up first. The whole request
        holds the registry (records): it is refused while a scan or another
        record-changing job runs, and those are refused while it runs.
        """
        return await self._run_held(
            RESTORE_JOB_KIND,
            "Database restore (admin page)",
            "records",
            lambda: self._restore_database(request),
        )

    async def _restore_database(self, request):
        max_bytes = getattr(self, "restore_max_bytes", 100 * 1024 * 1024)
        temp_path = None
        try:
            reader = await request.multipart()
            field = await reader.next()

            if not field or field.name != "database_file":
                return web.json_response(
                    {
                        "success": False,
                        "error": (
                            "No database file uploaded. "
                            "Expected field name: database_file"
                        ),
                    },
                    status=400,
                )

            fd, temp_path = tempfile.mkstemp(suffix=".db")
            os.close(fd)
            size = 0
            with open(temp_path, "wb") as out:
                while True:
                    chunk = await field.read_chunk()
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        return web.json_response(
                            {
                                "success": False,
                                "error": (
                                    "File too large. Maximum size is "
                                    f"{max_bytes // (1024 * 1024)}MB"
                                ),
                            },
                            status=400,
                        )
                    out.write(chunk)

            if size == 0:
                return web.json_response(
                    {"success": False, "error": "Uploaded file is empty"}, status=400
                )

            model = self.db.model
            ok, reason = await self._run_in_executor(
                model.verify_database_file, temp_path
            )
            if ok:
                reason = await self._run_in_executor(_inspect_restore_upload, temp_path)
                ok = reason is None
            if not ok:
                return web.json_response(
                    {"success": False, "error": reason}, status=400
                )

            try:
                backup_path = await self._run_in_executor(
                    model.restore_from_file, temp_path
                )
            except sqlite3.OperationalError as exc:
                if (
                    getattr(exc, "_prompt_manager_restore_backup_created", False)
                    is True
                ):
                    try:
                        await self._run_in_executor(
                            _prune_safety_backups, model.db_path
                        )
                    except Exception:
                        self.logger.warning(
                            "Could not prune safety backups after failed restore"
                        )
                raise
            removed = await self._run_in_executor(_prune_safety_backups, model.db_path)
            if removed:
                self.logger.info(f"Removed {len(removed)} old safety backups")
            previous = self.db
            self.db = PromptDatabase(model.db_path)
            self._database_replaced()  # a health report described the old one
            # Its connections would otherwise linger (a file lock on Windows)
            previous.close_all()
            prompt_count = self.db.model.get_database_info().get("total_prompts", 0)

            return web.json_response(
                {
                    "success": True,
                    "message": (
                        "Database restored successfully. "
                        f"Found {prompt_count} prompts."
                    ),
                    "prompt_count": prompt_count,
                    "backup_created": (
                        self._public_path(backup_path) if backup_path else None
                    ),
                }
            )

        except Exception as e:
            self.logger.error(f"Restore error: {e}", exc_info=True)
            return web.json_response(
                {
                    "success": False,
                    "error": f"Failed to restore database: {self._public_error(e)}",
                },
                status=500,
            )
        finally:
            if temp_path:
                for suffix in ("", "-wal", "-shm", "-journal"):
                    if os.path.exists(temp_path + suffix):
                        os.unlink(temp_path + suffix)

    async def scan_images(self, request):
        """Scan ComfyUI output images for prompt metadata and add them to the database.

        The scan runs as the registry's ``scan`` job, which outlives this
        request, so a closed tab never leaves a half-finished scan. Progress
        streams as the legacy server-sent events; while a scan is running
        (started here or from the workspace), a new request attaches to it
        instead of starting another one.
        """
        job = self._jobs.active(SCAN_JOB_KIND)
        if job is None:
            try:
                job = self._start_scan_job("all")
            except JobBusy as busy:
                return self._long_job_busy_response(busy.running.title)
        return await self._stream_scan_job(request, job)

    async def cancel_scan(self, request):
        """Stop between file transactions; an in-flight extraction can finish."""
        job = self._scan_job
        requested = bool(job and job.running)
        if requested:
            job.cancelled = True
        return web.json_response({"success": True, "cancellation_requested": requested})

    async def scan_status(self, request):
        """Whether an output scan is running, and its latest progress event."""
        job = self._scan_job
        return web.json_response(
            {
                "success": True,
                "running": bool(job and job.running),
                "last_event": job.last_event if job else None,
            }
        )

    def _start_scan_job(self, files=DEFAULT_SCAN_SCOPE, roots=None, output_dirs=None):
        """Register the scan job synchronously; its runner starts on the loop.

        ``files`` is the scope (``SCAN_SCOPES``). ``roots`` selects sources by
        their index in ``output_dirs`` (the effective sources the caller
        resolved); None scans every source, resolved when the job starts.
        Raises JobBusy while another record-changing job runs.
        """
        if files not in SCAN_SCOPES:
            raise ValueError(f"Unknown scan scope: {files!r}")
        if roots is not None and (
            output_dirs is None or not roots or max(roots) >= len(output_dirs)
        ):
            raise ValueError("A scoped scan needs the sources its roots index")
        job = self._jobs.start(
            SCAN_JOB_KIND,
            SCAN_JOB_TITLE,
            record_changing=True,
            scope={"files": files, "roots": None if roots is None else list(roots)},
            runner=functools.partial(self._run_scan_job, output_dirs=output_dirs),
            groups=SCAN_GROUP_NAMES,
        )
        self._scan_job = job
        return job

    async def _run_scan_job(self, job, output_dirs=None):
        final = None
        async for payload in self._scan_images_events(job, output_dirs):
            job.publish(payload)
            final = payload.get("type")
        state = SCAN_FINAL_STATES.get(final, "finished")
        if state == "failed":
            job.log_line(f"Scan failed · {job.status_line}")
        else:
            verb = "cancelled" if state == "cancelled" else "finished"
            job.log_line(f"Scan {verb} · {_plural(job.processed, 'file')} processed")
        job.finish(state)

    async def _stream_scan_job(self, request, job):
        """Stream ``job`` as SSE; stop quietly if the client goes away."""
        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await response.prepare(request)
        try:
            async with contextlib.aclosing(job.events()) as events:
                async for payload in events:
                    await response.write(_sse(payload).encode("utf-8"))
            await response.write_eof()
        except Exception as e:  # the socket closed under us; the scan goes on
            self.logger.info(
                f"Scan progress client disconnected ({e}); "
                "the scan continues in the background"
            )
        return response

    async def _scan_images_events(self, job, output_dirs=None):
        """Yield progress payloads while scanning the output directories.

        Every effective source is walked, or only those ``job.scope["roots"]``
        selects (indexes into ``output_dirs``). The payloads are the legacy SSE
        events, unchanged; ``job`` also gets its status line, totals and one
        outcome per processed file. Rows keep each source's listing index.
        """
        try:
            self.logger.info("Starting image scan operation")
            if output_dirs is None:
                output_dirs = self._get_all_output_dirs()
            selected = job.scope.get("roots")
            walked = (
                output_dirs
                if selected is None
                else [output_dirs[index] for index in selected]
            )
            if not walked:
                self.logger.error("No output directories found")
                message = (
                    "No output directories found. "
                    "Configure scan directories in Settings."
                )
                job.update(status_line=message)
                yield {"type": "error", "message": message}
                return

            dir_names = [self._public_path(d) for d in walked]
            folders = _plural(len(walked), "output folder")
            job.scope["output_folders"] = len(walked)
            scope = SCAN_SCOPE_LOG[job.scope.get("files", "all")]
            job.log_line(f"Scan started · {folders} · {scope}")
            job.update(status_line=f"Looking for media files in {folders}")
            yield self._scan_progress(
                0, f"Scanning {len(walked)} directory(ies) for media files..."
            )

            media_files = await self._run_in_executor(
                self._collect_output_media_sync, walked
            )
            counts = dict.fromkeys(SCAN_COUNT_KEYS, 0)
            job.update(
                total=len(media_files),
                status_line=_scan_status_line(0, len(media_files), 0, 0),
            )
            if media_files:
                yield self._scan_progress(
                    5, f"Found {len(media_files)} media files to process...", counts
                )
            roots = _scan_roots(output_dirs)
            known = set()
            if media_files and job.scope.get("files") != "all":
                known = await self._known_scan_files(job, media_files)
            async for event in self._scan_batches(
                media_files, counts, job, roots, known
            ):
                yield event

            self.logger.info(
                f"Scan completed: processed={counts['processed']}, "
                f"found={counts['found']}, new_prompts_added={counts['added']}, "
                f"images_linked_to_existing={counts['linked']}"
            )
            final_type = "cancelled" if job.cancelled else "complete"
            if final_type == "complete":
                job.update(progress=100)
            yield {"type": final_type, **counts, "directories": dir_names}

        except Exception:
            self.logger.exception("Scan error")
            job.update(status_line="The scan stopped on an internal error.")
            yield {
                "type": "error",
                "message": "An internal error occurred. Check server logs for details.",
            }

    async def _known_scan_files(self, job, media_files):
        """The "new" scope's skip set; a failed read falls back to reading all."""
        try:
            return await self._run_in_executor(self._known_scan_files_sync, media_files)
        except Exception:
            self.logger.warning(
                "Known files could not be read; the scan reads every file",
                exc_info=True,
            )
            job.log_line("Known files could not be read · reading every file")
            return set()

    def _known_scan_files_sync(self, media_files):
        """Stat each scanned file and ask the database which are known (blocking)."""
        sizes = {str(media_file): _file_size(media_file) for media_file in media_files}
        return self.db.get_known_resolved_paths(sizes)

    async def _scan_batches(self, media_files, counts, job, roots=(), known=()):
        """Process ``media_files`` in executor batches, updating ``counts`` in place.

        Records each file's outcome in ``job`` and yields one progress event
        per batch. Files in ``known`` (path strings) are not read: they count
        as processed and unchanged and are recorded as skipped.
        """
        total = len(media_files)
        started = time.monotonic()
        batches = 0
        for batch_start in range(0, total, SCAN_BATCH_SIZE):
            if job.cancelled:
                return
            batch = media_files[batch_start : batch_start + SCAN_BATCH_SIZE]
            skipped = [f for f in batch if str(f) in known]
            to_read = [f for f in batch if str(f) not in known]
            outcomes = [_scan_outcome(f, roots, "skipped") for f in skipped]
            counts["processed"] += len(skipped)
            counts["unchanged"] += len(skipped)
            if to_read:
                batch_results = await self._run_in_executor(
                    self._extract_batch_metadata_sync, to_read
                )
                batch_counts = await self._run_in_executor(
                    self._ingest_scan_batch_sync,
                    batch_results,
                    roots=roots,
                    outcomes=outcomes,
                )
                for key in counts:
                    counts[key] += batch_counts[key]
            for group, row, log_line in outcomes:
                job.record(group, row, log_line)

            batches += 1
            done = min(batch_start + len(batch), total)
            job.update(
                processed=counts["processed"],
                status_line=_scan_status_line(
                    done, total, batches, time.monotonic() - started
                ),
            )
            yield self._scan_progress(
                int(done / total * 100), f"Processing file {done}/{total}...", counts
            )
            await asyncio.sleep(0)

    @staticmethod
    def _scan_progress(progress, status, counts=None):
        """Progress event payload for the output scan."""
        counts = counts or {}
        return {
            "type": "progress",
            "progress": progress,
            "status": status,
            "processed": counts.get("processed", 0),
            **{key: counts.get(key, 0) for key in SCAN_COUNT_KEYS},
        }

    def _collect_output_media_sync(self, output_dirs):
        """Collect all media files from the output directories (blocking I/O)."""
        return _collect_media_files(output_dirs, IMAGE_EXTENSIONS + VIDEO_EXTENSIONS)

    def _extract_batch_metadata_sync(self, file_batch):
        """Extract metadata from a batch of files in one executor call.

        Files are read on ``PromptManagerConfig.WORKER_THREADS`` threads:
        Pillow decodes the whole image to reach trailing text chunks and
        releases the GIL while doing so.
        """
        from ..config import PromptManagerConfig

        def extract(media_file):
            try:
                return (media_file, self._extract_comfyui_metadata(str(media_file)))
            except Exception:
                return (media_file, {})

        return map_parallel(extract, file_batch, PromptManagerConfig.WORKER_THREADS)

    def _ingest_scan_batch_sync(self, batch_results, roots=(), outcomes=None):
        """Store the prompts found in a metadata batch (blocking DB work).

        Returns the legacy counts. When ``outcomes`` is a list, every processed
        file appends its ``(group, row, log_line)`` for the scan job, with the
        path relative to its root in ``roots`` (see ``_scan_roots``).
        """
        counts = dict.fromkeys(SCAN_COUNT_KEYS, 0)

        def note(media_file, status, title=None):
            if outcomes is not None:
                outcomes.append(_scan_outcome(media_file, roots, status, title))

        for media_file, metadata in batch_results:
            if self._scan_job and self._scan_job.cancelled:
                break
            counts["processed"] += 1
            try:
                outcome = self._ingest_scanned_file(media_file, metadata, detailed=True)
            except Exception as e:
                self.logger.error(f"Error processing {media_file.name}: {e}")
                counts["failed"] += 1
                note(media_file, "error")
                continue
            if outcome is None:
                note(media_file, None)
                continue
            counts["found"] += 1
            status = outcome["status"] if isinstance(outcome, dict) else outcome
            if status in SCAN_GROUPS:
                counts[status] += 1
            if isinstance(outcome, dict):
                counts["pairs_added"] += outcome.get("pairs_added", 0)
                counts["pair_links_added"] += outcome.get("links_added", 0)
                note(media_file, status, outcome.get("title"))
            else:
                note(media_file, status)
        return counts

    @staticmethod
    def _graph_prompt_text(metadata):
        """The positive prompt the browser parser traces from the graphs, or None.

        Records parse as the legacy pages parsed them (NaN reads as null).
        """
        records = {}
        for key in ("prompt", "workflow"):
            value = metadata.get(key)
            records[key] = parse_page_json(value) if isinstance(value, str) else value
        try:
            positive = extract_generation_params(records)["positivePrompt"]
        except GenerationParamsError:
            return None
        if not isinstance(positive, str) or positive == PLACEHOLDERS["positivePrompt"]:
            return None
        return positive.strip() or None

    def _ingest_scanned_file(self, media_file, metadata, detailed=False):
        """Enrich exact pairs; retain legacy positive-only import for absent pairs.

        Returns the file's scan status, or None without prompt metadata. With
        ``detailed``, a stored or linked file returns a dict instead: its
        ``status``, the prompt's display ``title`` and, for a resolved pair,
        the pair counts.
        """
        if not metadata:
            return None
        canonical = dict(metadata)
        for key in ("prompt", "workflow"):
            if isinstance(canonical.get(key), str):
                try:
                    canonical[key] = json.loads(canonical[key])
                except (ValueError, TypeError):
                    # Preserve malformed metadata for conservative resolver rejection.
                    pass
        if not canonical.get("prompt") and not canonical.get("parameters"):
            return None
        _with_file_size(canonical, media_file)
        resolution = resolve_prompt_pair(canonical)
        if resolution["state"] == "resolved":
            result = ingest_resolved_pair(
                self.db, str(media_file), canonical, resolution
            )
            if not detailed:
                return result["status"]
            return {
                **result,
                "title": self._scan_title(media_file, resolution["positive"]["text"]),
            }
        if canonical.get("prompt") and resolution["state"] in (
            "ambiguous",
            "unresolved",
        ):
            # The resolver could not decide: link the prompt the inspector and the
            # legacy pages show for this file (the browser parser, in Python).
            # Graphs without a recognized sampler keep the legacy extractor below.
            prompt_text = self._graph_prompt_text(metadata)
            if not prompt_text:
                return resolution["state"]
        elif resolution["state"] == "ambiguous":
            return "ambiguous"
        elif resolution["state"] == "unresolved" and not isinstance(
            canonical.get("parameters"), str
        ):
            return resolution["state"]
        else:
            parsed = self._parse_comfyui_prompt(metadata)
            if not (parsed.get("prompt") or parsed.get("parameters")):
                return None
            prompt_text = self._extract_readable_prompt(parsed)
            if prompt_text is not None and not isinstance(prompt_text, str):
                prompt_text = str(prompt_text)
            if not (prompt_text and prompt_text.strip()):
                return "found"
            prompt_text = prompt_text.strip()

        status = self._store_scanned_prompt(media_file, canonical, prompt_text)
        if not detailed or status not in SCAN_TITLED_DETAILS:
            return status
        return {"status": status, "title": self._scan_title(media_file, prompt_text)}

    def _scan_title(self, media_file, text):
        """The linked prompt's Library title (its stored title, else ``text``'s
        first line), as the Library and the inspector name it."""
        try:
            path = str(media_file)
            summary = self.db.get_image_link_summaries([path], False).get(path)
            if summary and summary.get("title"):
                return summary["title"]
        except Exception:
            self.logger.warning("Scan row title could not be read; using its text")
        return display_title((), text)

    def _store_scanned_prompt(self, media_file, canonical, prompt_text):
        """Link ``media_file`` to ``prompt_text``'s prompt, saving it when new."""
        prompt_hash = generate_prompt_hash(prompt_text)
        existing = self.db.get_prompt_by_hash(prompt_hash)
        if existing:
            try:
                image_id = self.db.link_image_to_prompt(
                    existing["id"], str(media_file), metadata=canonical
                )
            except Exception as e:
                self.logger.error(
                    f"Failed to link {media_file.name} to existing prompt: {e}"
                )
                return "found"
            return "linked" if image_id else "unchanged"

        prompt_id = self.db.save_prompt(
            prompt_text,
            "scanned",
            ["auto-scanned"],
            None,
            f"Auto-scanned from {media_file.name}",
            prompt_hash,
        )
        if not prompt_id:
            return "found"
        try:
            self.db.link_image_to_prompt(prompt_id, str(media_file), metadata=canonical)
        except Exception as e:
            self.logger.error(f"Failed to link {media_file.name} to new prompt: {e}")
        return "added"
