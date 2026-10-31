"""Library location routes (#249 S6b): where the library lives, Move, Keep.

- ``GET /prompt_manager/system/library`` → ``{success, location}``: the
  database's path (relative to ComfyUI, as the statistics show it), why it is
  there (``source``: ``default``, ``environment``, ``config`` or
  ``node_folder``), whether it can move and where to (``target``), the
  "Keep it here" choice (``keep``), whether to offer the move (``offer``:
  movable and not kept), a ``problem`` code and the database's size. The
  System Overview carries the same object as ``location``.
- ``POST /prompt_manager/system/library/move`` → 202 ``{job}``: the
  ``library_move`` job, ``exclusive`` (it runs alone and every other job,
  legacy-page operations included, is refused while it runs), not
  cancellable. It copies, verifies and switches the live library
  (``database/library_move.py``) and ends with one truthful line; a failure
  leaves the library in the node folder, unchanged. Refused before any job
  starts: 400 ``not-movable``/``same-folder``/``target-invalid``, 409
  ``target-has-library``/``target-conflict``/``busy``, 507
  ``insufficient-space``.
- ``POST /prompt_manager/system/library/keep {keep: bool}`` persists
  ``library.keep_in_node_folder`` through the config store and answers the
  new location.

Errors are ``{success:false, code, error}`` and never name a path.
"""

import asyncio
import functools
import os

from aiohttp import web

from .job_routes import busy_response
from .jobs import JobBusy

try:
    from ...database import library_move as lm
    from ...database.operations import resolve_library_location
    from ...utils import library_location as ll
except ImportError:
    from database import library_move as lm
    from database.operations import resolve_library_location
    from utils import library_location as ll

LIBRARY_MOVE_JOB_KIND = "library_move"
LIBRARY_MOVE_TITLE = "Move library"
LIBRARY_MOVE_GROUPS = ("files", "errors")
KEEP_BODY_MAX = 1024

_REFUSAL_STATUS = {
    "not-movable": 400,
    "same-folder": 400,
    "target-invalid": 400,
    "target-has-library": 409,
    "target-conflict": 409,
    "insufficient-space": 507,
}
STILL_THERE = "The library is still in the node folder, unchanged."
NOT_MOVED = "The library was not moved."
OLD_LEFT = {
    "old-in-use": (
        "The old database is still open elsewhere, so it was left in the node "
        "folder; it is no longer used."
    ),
    "old-locked": (
        "The old files could not be renamed, so they were left in the node "
        "folder; they are no longer used."
    ),
}


def _failure(message, status, code):
    return web.json_response(
        {"success": False, "code": code, "error": message}, status=status
    )


def _plural(count, one, many=None):
    return f"{count:,} {one if count == 1 else (many or one + 's')}"


def _same_file(first, second):
    try:
        return os.path.samefile(first, second)
    except OSError:
        return os.path.normcase(os.path.realpath(first)) == os.path.normcase(
            os.path.realpath(second)
        )


CUSTOM_NODES = "custom_nodes"


def _inside(path, folder):
    """``path`` relative to ``folder`` by text (normalised spellings), or None."""
    child, parent = os.path.normcase(path), os.path.normcase(folder)
    if child == parent:
        return ""
    prefix = parent.rstrip(os.sep) + os.sep
    return path[len(prefix) :] if child.startswith(prefix) else None


def _comfyui_custom_nodes():
    """ComfyUI's custom_nodes folders as it lists them (unresolved), or []."""
    try:
        from ..config import _import_folder_paths

        getter = getattr(_import_folder_paths(), "get_folder_paths", None)
        folders = getter(CUSTOM_NODES) if getter else []
        return [folder for folder in folders or [] if isinstance(folder, str)]
    except Exception:
        return []


def node_folder_public_path(path, extension_root, custom_nodes):
    """``custom_nodes/<node folder>/...`` for a path in the node folder, or None.

    The node folder is matched at its own unresolved place in a custom_nodes
    folder (one of ``custom_nodes``, or the parent of ``extension_root`` when
    that is named custom_nodes), so a node folder that is a symlink to a
    checkout outside ComfyUI still reads relative to ComfyUI. ``path`` may be
    spelled through the link or resolved. Never absolute, never ``..``:
    anything else is None and the caller uses ``_public_path``.
    """
    if not path or not extension_root:
        return None
    try:
        root = os.path.normpath(os.path.abspath(extension_root))
        name = os.path.basename(root)
        folders = [os.path.normpath(os.path.abspath(f)) for f in custom_nodes]
        parent = os.path.dirname(root)
        if os.path.normcase(os.path.basename(parent)) == CUSTOM_NODES:
            folders.append(parent)
        spellings = [os.path.normpath(os.path.abspath(path))]
        spellings.append(os.path.realpath(spellings[0]))
    except (OSError, ValueError, TypeError):
        return None
    for folder in folders:
        node = os.path.join(folder, name)
        for spelling, node_spelling in zip(spellings, (node, os.path.realpath(node))):
            rest = _inside(spelling, node_spelling)
            if rest is None:
                continue
            parts = [os.path.basename(folder), name] + (
                rest.split(os.sep) if rest else []
            )
            if all(part and part not in (".", "..") for part in parts):
                return "/".join(parts)
    return None


class LibraryLocationRoutesMixin:
    """Mixin for the /prompt_manager/system/library endpoints."""

    # Replaced in tests with a resolver over a temp tree.
    _library_resolver = staticmethod(resolve_library_location)

    def _register_library_location_routes(self, routes):
        @routes.get("/prompt_manager/system/library")
        async def library_location_route(request):
            return await self.get_library_location(request)

        @routes.post("/prompt_manager/system/library/move")
        async def library_move_route(request):
            return await self.start_library_move(request)

        @routes.post("/prompt_manager/system/library/keep")
        async def library_keep_route(request):
            return await self.keep_library(request)

    # ── location ──────────────────────────────────────────────────────

    def _library_public_path(self, path):
        """The library's path for display: through the node folder's place in
        custom_nodes when it is there (a symlinked node folder included), else
        ``_public_path``. Only the library location uses this; sources and
        jobs keep ``_public_path``, which their requests round-trip."""
        shown = node_folder_public_path(
            path, self._extension_root, _comfyui_custom_nodes()
        )
        return shown or self._public_path(path)

    def _live_location(self):
        """The resolved location, unless the live database is another file.

        A database the API was handed (tests, the browser fixture server) or
        one the resolver no longer names is reported as configured and is
        never moved.
        """
        location = self._library_resolver()
        live = self.db.model.db_path
        if _same_file(location.db_path, live):
            return location
        return ll.LibraryLocation(
            ll.SOURCE_CONFIG,
            live,
            location.config_path,
            os.path.dirname(live),
            config_explicit=location.config_explicit,
        )

    def _location_payload(self, location):
        """The public view of ``location`` (blocking: stats the database)."""
        from ..config import PromptManagerConfig

        keep = bool(PromptManagerConfig.KEEP_LIBRARY_IN_NODE_FOLDER)
        size = 0
        try:
            size = os.path.getsize(location.db_path)
        except OSError:
            # no library file yet: report size 0
            pass
        return {
            "path": self._library_public_path(location.db_path),
            # Where Move library would put it, and whether config.json goes too.
            "target_source": location.target_source if location.movable else None,
            "config_moves": location.movable and not location.config_explicit,
            "source": location.source,
            "movable": location.movable,
            "keep": keep,
            "offer": location.movable and not keep,
            "target": (
                self._library_public_path(location.target_dir)
                if location.movable
                else None
            ),
            "problem": location.problem or self._node_folder_risk(location),
            "size_bytes": size,
        }

    # The node folder (tests point it at a temp tree).
    _extension_root = ll.EXTENSION_ROOT

    def _node_folder_risk(self, location):
        """``inside-node-folder`` for a library that stays in the node folder.

        A configured database path, or a config.json the resolver still reads
        from the node folder, is deleted with the node by a reinstall, and
        it is never offered a move.
        """
        if location.source == ll.SOURCE_NODE_FOLDER:
            return None
        paths = [location.db_path] + (
            [] if location.config_explicit else [location.config_path]
        )
        root = self._extension_root
        for path in paths:
            parent = os.path.dirname(os.path.abspath(path))
            if ll.same_folder(parent, root) or os.path.normcase(
                os.path.realpath(parent)
            ).startswith(os.path.normcase(os.path.realpath(root)) + os.sep):
                return "inside-node-folder"
        return None

    def _library_payload(self):
        return self._location_payload(self._live_location())

    async def get_library_location(self, request):
        try:
            payload = await self._run_in_executor(self._library_payload)
        except Exception:
            self.logger.exception("Library location could not be read")
            return _failure(
                "The library location could not be read.", 500, "location-failed"
            )
        return web.json_response(
            {"success": True, "location": payload},
            headers={"Cache-Control": "no-store"},
        )

    # ── move ──────────────────────────────────────────────────────────

    def _plan_library_move(self):
        """The checked plan (blocking); raises MoveFailed."""
        plan = lm.plan_move(self._live_location())
        if lm.space_problem(plan) is not None:
            raise lm.MoveFailed("insufficient-space")
        return plan

    async def start_library_move(self, request):
        blocker = self._jobs.blocker(LIBRARY_MOVE_JOB_KIND, admission="exclusive")
        if blocker is not None:
            return busy_response(JobBusy(blocker))
        try:
            await self._run_in_executor(self._plan_library_move)
        except lm.MoveFailed as refused:
            status = _REFUSAL_STATUS.get(refused.code, 400)
            return _failure(refused.message, status, refused.code)
        except Exception:
            self.logger.exception("Move library could not be planned")
            return _failure(NOT_MOVED, 500, "move-failed")
        try:
            job = self._jobs.start(
                LIBRARY_MOVE_JOB_KIND,
                LIBRARY_MOVE_TITLE,
                admission="exclusive",
                cancellable=False,
                scope={},
                groups=LIBRARY_MOVE_GROUPS,
                runner=self._run_library_move,
            )
        except JobBusy as busy:
            return busy_response(busy)
        return web.json_response({"success": True, "job": job.summary()}, status=202)

    def _after_library_switch(self):
        """Forget what described the old file (best effort, after the switch).

        Plans, the health report and the history read fence named the old
        file; the fence is closed before it is dropped.
        """
        self._database_replaced()
        fence, self._history_read_fence = self._history_read_fence, None
        if fence is not None:
            fence.close()

    def finish_interrupted_library_move(self):
        """Startup: rename old files a crash left after a move (blocking)."""
        try:
            location = self._library_resolver()
            if _same_file(location.db_path, self.db.model.db_path):
                return lm.finish_interrupted_move(location)
        except Exception:
            self.logger.exception("Could not finish an interrupted library move")
        return []

    async def _run_library_move(self, job):
        from ..config_store import _COMMIT_LOCK

        loop = asyncio.get_running_loop()

        def progress(line):
            def show():
                job.log_line(line)
                job.update(status_line=line)

            loop.call_soon_threadsafe(show)

        job.update(status_line="Checking the new folder")
        try:
            plan = await self._run_in_executor(self._plan_library_move)
            target = self._library_public_path(plan.target_dir)
            job.log_line(f"Moving the library to {target}")
            result = await self._run_in_executor(
                functools.partial(
                    lm.move_library,
                    plan,
                    after_switch=self._after_library_switch,
                    config_lock=_COMMIT_LOCK,
                    progress=progress,
                )
            )
        except lm.MoveFailed as failed:
            self.logger.error(f"Move library failed: {failed.code}")
            return self._fail_library_move(
                job, failed.code, f"{failed.message} {STILL_THERE}"
            )
        except Exception:
            self.logger.exception("Move library failed")
            return self._fail_library_move(job, None, f"{NOT_MOVED} {STILL_THERE}")
        await asyncio.sleep(0)  # the progress lines come first
        job.record_counted("files", len(result.files))
        line = (
            f"Moved the library to {target}: "
            f"{_plural(result.counts.get('prompts', 0), 'prompt')} and "
            f"{_plural(result.counts.get('generated_images', 0), 'image')} verified"
        )
        if result.retire_problem:
            line += ". " + OLD_LEFT.get(result.retire_problem, OLD_LEFT["old-locked"])
        elif result.renamed:
            line += (
                f". The old files were renamed {', '.join(result.renamed)}; "
                "nothing was deleted"
            )
        line += ". Logs move at the next restart."
        job.log_line(line)
        job.update(progress=100, status_line=line)
        return None

    @staticmethod
    def _fail_library_move(job, code, message):
        from .maintenance import _fail_job

        _fail_job(job, code, message)

    # ── keep it here ─────────────────────────────────────────────────

    async def keep_library(self, request):
        from ..config import PromptManagerConfig
        from ..config_store import get_config_store

        try:
            if (request.content_length or 0) > KEEP_BODY_MAX:
                raise ValueError("too large")
            data = await request.json()
        except Exception:
            data = None
        if not isinstance(data, dict) or not isinstance(data.get("keep"), bool):
            return _failure("keep must be true or false.", 400, "invalid-request")
        keep = data["keep"]

        def mutate(config):
            library = config.get("library")
            library = dict(library) if isinstance(library, dict) else {}
            library["keep_in_node_folder"] = keep
            config["library"] = library
            return config

        def publish(_config):
            PromptManagerConfig.KEEP_LIBRARY_IN_NODE_FOLDER = keep

        try:
            await self._run_in_executor(
                functools.partial(
                    get_config_store().commit,
                    mutate,
                    expected_revision=None,
                    publish=publish,
                )
            )
        except Exception as exc:
            return self._config_commit_failure(exc)
        return await self.get_library_location(request)
