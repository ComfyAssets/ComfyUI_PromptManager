"""System › Diagnostics & logs test hooks for the browser tests (#249 System S12).

The logger writes into ``<server root>/logs`` (``repoint_logs``, called by
``create_app`` before any record is written), so no test reads or deletes a
real log folder. Every route refuses a log folder outside the server root.

- ``POST /__test__/logs/reset`` restores the default logging configuration,
  empties the active log, removes every other entry of the log folder (a
  link is removed, never followed) and clears the in-memory buffer. The
  global ``/__test__/reset`` calls it too;
- ``POST /__test__/logs/seed`` ``{records, filler, rotated, active, link,
  other}`` writes buffer records with fixed times (``records``: the concept's
  four lines, then one DEBUG, ERROR and CRITICAL record, older; ``filler``
  more INFO records older still), ``active`` bytes into the active log,
  rotated copies ``prompt_manager.log.<n>`` of the listed sizes (fixed
  times), on POSIX a symlink ``prompt_manager.log.<n+1>`` to a file outside
  the folder (``link``) and a user's ``prompt_manager.log.bak`` (``other``).
  ``{mode: "concept"}`` reproduces the concept's Logs sample (app.js 695)
  for the comparison: the buffer holds only its four lines, the active log
  is 1.4 MB with three rotated copies ("prompt_manager.log · 1.4 MB · rotated
  copies: 3") and console logging is off, as the concept's toggle;
- ``POST /__test__/logs/rotate`` rolls the copies over as the handler does
  (``.2`` → ``.3``, ``.1`` → ``.2``, a new ``.1``), so a reviewed plan no
  longer matches;
- ``GET /__test__/logs/state`` answers the folder's entries (name, size, link)
  and the buffer's length.
"""

import datetime
import logging
import os
import shutil
from pathlib import Path

from aiohttp import web

ACTIVE = "prompt_manager.log"
SEED_DAY = datetime.datetime(2026, 10, 26)
# The concept's four lines (app.js 695), oldest first: the viewer shows them
# newest first.
CONCEPT_RECORDS = (
    ("09:38:52", logging.INFO, "Source monitor started: output"),
    ("09:39:04", logging.INFO, "Database backup verified"),
    ("09:42:18", logging.WARNING, "3 images have ambiguous prompt metadata"),
    ("09:42:18", logging.INFO, "Scan completed: 12 enriched, 0 duplicate links"),
)
# Older than the concept's lines, so they come after them: the levels the
# concept does not show.
LEVEL_RECORDS = (
    ("09:30:01", logging.DEBUG, "Thumbnail cache lookup"),
    ("09:30:02", logging.ERROR, "A source folder could not be read"),
    ("09:30:03", logging.CRITICAL, "The database could not be opened"),
)
ROTATED_TIME = datetime.datetime(2026, 10, 25, 14, 2)
# The concept's "prompt_manager.log · 1.4 MB · rotated copies: 3": the active
# log stays under 1.45 MB while the server writes its own lines.
CONCEPT_ACTIVE = int(1.4 * 1024 * 1024)
CONCEPT_ROTATED = (3072, 2048, 1024)
MAX_SEED_BYTES = 64 * 1024 * 1024
DEFAULT_CONFIG = {
    "level": "INFO",
    "max_file_size": 10 * 1024 * 1024,
    "backup_count": 5,
    "console_logging": True,
    "file_logging": True,
    "buffer_size": 1000,
}


def _manager():
    from utils.logging_config import get_logger_manager

    return get_logger_manager()


def repoint_logs(root: Path) -> Path:
    """Point the logger at ``<root>/logs`` before anything is logged.

    The logger is built while ``logs_dir`` answers that folder, so its first
    handler never opens a log file anywhere else (a node folder with a legacy
    database would otherwise name a real log folder).
    """
    from utils import library_location

    folder = Path(root) / "logs"
    folder.mkdir(exist_ok=True)
    original = library_location.logs_dir
    library_location.logs_dir = lambda **_kwargs: str(folder)
    try:
        manager = _manager()
    finally:
        library_location.logs_dir = original
    with manager._files_lock:
        manager.log_dir = folder
        manager._setup_loggers()
    return folder


def _require_inside(root: Path, folder: Path) -> None:
    resolved = Path(os.path.realpath(folder))
    if Path(os.path.realpath(root)) not in resolved.parents:
        raise web.HTTPForbidden(text="The log folder is outside the test server root")


def _record(time_text, level, message, index):
    record = logging.LogRecord(
        "prompt_manager.test", level, __file__, index, message, None, None
    )
    hours, minutes, seconds = (int(part) for part in time_text.split(":"))
    moment = SEED_DAY.replace(hour=hours, minute=minutes, second=seconds)
    # Equal times keep their order: each later record is a microsecond newer.
    record.created = moment.timestamp() + index / 1e6
    return record


def _touch(path: Path, size: int, moment: datetime.datetime) -> None:
    # A link at this name is replaced, never written through.
    if path.is_symlink():
        path.unlink()
    with open(path, "wb") as handle:
        handle.write(b"x" * size)
    stamp = moment.timestamp()
    os.utime(path, (stamp, stamp))


def _bounded(value, name, top=MAX_SEED_BYTES):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= top:
        raise web.HTTPBadRequest(text=f"{name} must be an integer 0..{top}")
    return value


def register_diagnostics_commands(app, routes, api, root: Path):
    """Register the hooks; returns ``reset()`` for the global reset."""

    def folder() -> Path:
        path = Path(_manager().log_dir)
        _require_inside(root, path)
        return path

    def reset() -> None:
        manager = _manager()
        log_dir = folder()
        if manager.get_config() != DEFAULT_CONFIG:
            manager.update_config(dict(DEFAULT_CONFIG))
        with manager._files_lock:
            held = manager._acquire_file_handlers()
            try:
                with os.scandir(log_dir) as entries:
                    found = list(entries)
                for entry in found:
                    path = Path(entry.path)
                    if entry.name == ACTIVE and entry.is_file(follow_symlinks=False):
                        manager._truncate_main_log(path)
                    elif entry.is_dir(follow_symlinks=False):
                        shutil.rmtree(path)
                    else:
                        os.unlink(path)
            finally:
                manager._release_handlers(held)
        with manager._buffer_lock:
            manager._log_buffer.clear()

    def seed_concept() -> None:
        manager = _manager()
        log_dir = folder()
        manager.update_config({"console_logging": False})
        with manager._buffer_lock:
            manager._log_buffer.clear()
        for index, (time_text, level, message) in enumerate(CONCEPT_RECORDS):
            record = _record(time_text, level, message, index)
            manager.add_to_buffer(record, record.getMessage())
        with open(log_dir / ACTIVE, "ab") as handle:
            handle.write(b"x" * CONCEPT_ACTIVE)
        for number, size in enumerate(CONCEPT_ROTATED, start=1):
            moment = ROTATED_TIME - datetime.timedelta(hours=number)
            _touch(log_dir / f"{ACTIVE}.{number}", size, moment)

    def seed(data) -> None:
        if data.get("mode") == "concept":
            seed_concept()
            return
        manager = _manager()
        log_dir = folder()
        with_records = data.get("records", True) is not False
        filler = _bounded(data.get("filler", 0), "filler", 900)
        older = [
            (f"08:{index // 60:02}:{index % 60:02}", logging.INFO, f"Filler {index}")
            for index in range(filler)
        ]
        named = list(LEVEL_RECORDS) + list(CONCEPT_RECORDS) if with_records else []
        ordered = older + named
        for index, (time_text, level, message) in enumerate(ordered):
            record = _record(time_text, level, message, index)
            manager.add_to_buffer(record, record.getMessage())
        active = _bounded(data.get("active", 0), "active")
        if active:
            with open(log_dir / ACTIVE, "ab") as handle:
                handle.write(b"x" * active)
        rotated = data.get("rotated", [])
        if not isinstance(rotated, list) or len(rotated) > 20:
            raise web.HTTPBadRequest(text="rotated must be a list of sizes")
        for number, size in enumerate(rotated, start=1):
            moment = ROTATED_TIME - datetime.timedelta(hours=number)
            _touch(log_dir / f"{ACTIVE}.{number}", _bounded(size, "size"), moment)
        if data.get("link") and os.name != "nt":
            target = Path(root) / "outside-log-target.txt"
            target.write_bytes(b"not a log of this folder")
            os.symlink(target, log_dir / f"{ACTIVE}.{len(rotated) + 1}")
        if data.get("other"):
            _touch(log_dir / f"{ACTIVE}.bak", 0, ROTATED_TIME)

    def rotate() -> None:
        manager = _manager()
        log_dir = folder()
        with manager._files_lock:
            held = manager._acquire_file_handlers()
            try:
                for number in (2, 1):
                    source = log_dir / f"{ACTIVE}.{number}"
                    if source.is_file() and not source.is_symlink():
                        os.replace(source, log_dir / f"{ACTIVE}.{number + 1}")
                _touch(log_dir / f"{ACTIVE}.1", 4096, datetime.datetime.now())
            finally:
                manager._release_handlers(held)

    def state():
        manager = _manager()
        log_dir = folder()
        entries = []
        with os.scandir(log_dir) as listing:
            found = sorted(listing, key=lambda item: item.name)
        for entry in found:
            info = entry.stat(follow_symlinks=False)
            entries.append(
                {"name": entry.name, "size": info.st_size, "link": entry.is_symlink()}
            )
        with manager._buffer_lock:
            buffered = len(manager._log_buffer)
        return {"entries": entries, "buffer": buffered, "config": manager.get_config()}

    async def body(request):
        data = await request.json() if request.can_read_body else {}
        if not isinstance(data, dict):
            raise web.HTTPBadRequest()
        return data

    @routes.post("/__test__/logs/reset")
    async def logs_reset(request: web.Request) -> web.Response:
        await api._run_in_executor(reset)
        return web.json_response({"success": True})

    @routes.post("/__test__/logs/seed")
    async def logs_seed(request: web.Request) -> web.Response:
        data = await body(request)
        await api._run_in_executor(seed, data)
        return web.json_response({"success": True})

    @routes.post("/__test__/logs/rotate")
    async def logs_rotate(request: web.Request) -> web.Response:
        await api._run_in_executor(rotate)
        return web.json_response({"success": True})

    @routes.get("/__test__/logs/state")
    async def logs_state(request: web.Request) -> web.Response:
        return web.json_response(await api._run_in_executor(state))

    return reset
