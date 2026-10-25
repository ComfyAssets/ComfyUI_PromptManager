"""System test hooks for the browser tests (#249 System S2 Task 6).

The data is the concept-shaped real fixture (``POST /__test__/concept/reset``,
``workspace_concept_fixture.py``): its parents, saved pairs, images, ratings
and runs are what the Overview, the Statistics dialog and the health check
read. These per-server hooks only change what a System test needs on top:

- ``POST /__test__/system/reset`` restores the configured sources, forgets the
  session's health report and ends every injected failure;
- ``POST /__test__/system/fail`` ``{path, times}`` answers the next ``times``
  requests for ``path`` (a ``/prompt_manager/system/`` route) with the routes'
  500 error envelope;
- ``POST /__test__/system/missing-source`` configures one more source that
  does not exist (an unmounted drive); with ``{"inside": true}`` it is an
  existing folder inside the output folder (``output/unplugged``), which
  ``POST /__test__/system/unplug`` then removes (a drive unplugged while the
  page shows it);
- ``POST /__test__/system/missing-file`` deletes the newest linked image's
  file from disk, leaving its database row (a missing-file link);
- ``POST /__test__/system/unthumbnailed`` ``{count}`` writes ``count`` plain
  PNGs with no thumbnail into the output folder;
- ``POST /__test__/system/monitor`` ``{watching, alive}`` stands in a fake
  image monitor whose observer is ``alive`` (default true) and schedules the
  named folders of this server's root (``["output"]``), so Sources reads
  watching, manual scan and restart pending states (S4).
- ``POST /__test__/system/library`` ``{state}`` (S6b): ``node_folder`` makes
  the live database read as a node-folder library that can move to
  ``user/__promptmanager``; Move library then runs a stand-in move that
  touches no file and ends with the library in the default folder.
  ``failing`` is the same library whose move fails verification. Reset
  restores the real resolver, the real move and "Keep it here".

- ``POST /__test__/system/duplicate-prompts`` (S7) adds two prompts whose
  text differs only in case and spacing (a duplicate prompt text group);
  ``{"again": true}`` adds a third, so a reviewed plan no longer matches;
- ``POST /__test__/system/duplicate-files`` (S7) writes one PNG's bytes three
  times: ``output/s7-dupes/same.png``, ``input/s7-dupes/same.png`` (the same
  relative path in the other source) and ``output/s7-dupes/copy.png``;
  ``{"groups": n}`` adds ``n`` more groups of two copies each;
- ``GET /__test__/system/digest`` (S7) answers the sha256 of the database's
  rows (a read-only dump) and of every media file under ``output`` and
  ``input`` (not the automatic previews in ``thumbnails``), so a test proves
  a Cancel wrote nothing.

Delays use the shared request holds (``request_hold.py``).
"""

import hashlib
import os
import shutil
import sqlite3
from pathlib import Path

from aiohttp import web
from PIL import Image

FAILABLE_PREFIX = "/prompt_manager/system/"
MAX_UNTHUMBNAILED = 50


def register_system_commands(app, routes, api, root: Path):
    # System › Appearance saves the host fields to this server's config.json (S3);
    # reset restores both. The file is this server's own, inside its temp root:
    # reset rewrites or deletes it, so a server whose configuration points
    # anywhere else (a real config.json) refuses to register these hooks.
    config_path = root / "config.json"
    configured_path = os.environ.get("PROMPT_MANAGER_CONFIG_PATH")
    if (
        configured_path is None
        or Path(configured_path).resolve() != config_path.resolve()
    ):
        raise RuntimeError(
            f"PROMPT_MANAGER_CONFIG_PATH must be {config_path}, not {configured_path!r}"
        )
    from py.config import GalleryConfig, PromptManagerConfig
    from utils import image_monitor

    from database import library_move
    from utils import library_location

    configured = list(GalleryConfig.MONITORING_DIRECTORIES)
    real_move = library_move.move_library
    keep = PromptManagerConfig.KEEP_LIBRARY_IN_NODE_FOLDER
    config_bytes = config_path.read_bytes() if config_path.exists() else None
    host_values = (
        PromptManagerConfig.RESULT_TIMEOUT,
        PromptManagerConfig.WEBUI_DISPLAY_MODE,
        PromptManagerConfig.WORKER_THREADS,
        GalleryConfig.MONITORING_ENABLED,
    )
    monitor = getattr(image_monitor, "_monitor_instance", None)
    failures: dict[str, int] = {}

    @web.middleware
    async def inject_failures(request, handler):
        left = failures.get(request.path, 0)
        if left <= 0:
            return await handler(request)
        failures[request.path] = left - 1
        return web.json_response(
            {"success": False, "code": None, "error": "Injected failure"}, status=500
        )

    app.middlewares.append(inject_failures)

    unplugged = root / "output" / "unplugged"

    dupes = [root / "output" / "s7-dupes", root / "input" / "s7-dupes"]

    def reset():
        failures.clear()
        for folder in dupes:
            shutil.rmtree(folder, ignore_errors=True)
        if unplugged.is_dir():
            unplugged.rmdir()
        GalleryConfig.MONITORING_DIRECTORIES = list(configured)
        api._health_report = None
        (
            PromptManagerConfig.RESULT_TIMEOUT,
            PromptManagerConfig.WEBUI_DISPLAY_MODE,
            PromptManagerConfig.WORKER_THREADS,
            GalleryConfig.MONITORING_ENABLED,
        ) = host_values
        image_monitor._monitor_instance = monitor
        api.__dict__.pop("_library_resolver", None)
        library_move.move_library = real_move
        PromptManagerConfig.KEEP_LIBRARY_IN_NODE_FOLDER = keep
        if config_bytes is None:
            config_path.unlink(missing_ok=True)
        else:
            # Replace in one step, as the server writes it (Windows preflight).
            staged = config_path.with_name(config_path.name + ".reset")
            staged.write_bytes(config_bytes)
            os.replace(staged, config_path)

    @routes.post("/__test__/system/reset")
    async def system_reset(request):
        reset()
        return web.json_response({"success": True})

    @routes.post("/__test__/system/fail")
    async def system_fail(request):
        data = await request.json()
        path, times = data.get("path"), data.get("times", 1)
        if (
            not isinstance(path, str)
            or not path.startswith(FAILABLE_PREFIX)
            or isinstance(times, bool)
            or not isinstance(times, int)
            or not 1 <= times <= 10
        ):
            raise web.HTTPBadRequest()
        failures[path] = times
        return web.json_response({"success": True})

    @routes.post("/__test__/system/missing-source")
    async def missing_source(request):
        data = await request.json() if request.can_read_body else {}
        if data.get("inside") is True:
            unplugged.mkdir(exist_ok=True)
            absent = str(unplugged)
        else:
            absent = str(root / "unmounted-drive")
            if os.path.exists(absent):
                raise web.HTTPConflict()
        GalleryConfig.MONITORING_DIRECTORIES = [*configured, absent]
        return web.json_response(
            {"success": True, "configured": len(GalleryConfig.MONITORING_DIRECTORIES)}
        )

    @routes.post("/__test__/system/unplug")
    async def unplug(request):
        if not unplugged.is_dir():
            raise web.HTTPConflict()
        unplugged.rmdir()
        return web.json_response({"success": True})

    @routes.post("/__test__/system/missing-file")
    async def missing_file(request):
        with api.db.model.get_connection() as conn:
            row = conn.execute(
                "SELECT file_path FROM generated_images ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            raise web.HTTPNotFound()
        Path(row[0]).unlink()
        api.invalidate_gallery_cache()
        return web.json_response({"success": True, "filename": Path(row[0]).name})

    @routes.post("/__test__/system/unthumbnailed")
    async def unthumbnailed(request):
        data = await request.json()
        count = data.get("count")
        if (
            isinstance(count, bool)
            or not isinstance(count, int)
            or not 1 <= count <= MAX_UNTHUMBNAILED
        ):
            raise web.HTTPBadRequest()
        folder = root / "output" / "unthumbnailed"
        folder.mkdir(exist_ok=True)
        for number in range(count):
            with Image.new("RGB", (64, 64), "#2b3442") as image:
                image.save(folder / f"plain_{number + 1:03}.png")
        api.invalidate_gallery_cache()
        return web.json_response({"success": True})

    @routes.post("/__test__/system/monitor")
    async def fake_monitor(request):
        data = await request.json()
        watching, alive = data.get("watching", []), data.get("alive", True)
        if (
            not isinstance(watching, list)
            or not all(name in ("output", "input") for name in watching)
            or not isinstance(alive, bool)
        ):
            raise web.HTTPBadRequest()
        image_monitor._monitor_instance = _FakeMonitor(
            [str(root / name) for name in watching], alive
        )
        return web.json_response({"success": True})

    @routes.post("/__test__/system/duplicate-prompts")
    async def duplicate_prompts(request):
        data = await request.json() if request.can_read_body else {}
        texts = [("S7 duplicate prompt", "2001-01-01 00:00:00")]
        texts.append(("  s7 DUPLICATE prompt", "2002-01-01 00:00:00"))
        if data.get("again") is True:
            texts = [("S7 Duplicate Prompt  ", "2003-01-01 00:00:00")]
        with api.db.model.get_connection() as conn:
            conn.executemany(
                "INSERT INTO prompts(text, created_at) VALUES(?, ?)", texts
            )
            conn.commit()
        return web.json_response({"success": True})

    @routes.post("/__test__/system/duplicate-files")
    async def duplicate_files(request):
        body = await request.json() if request.can_read_body else {}
        for folder in dupes:
            folder.mkdir(exist_ok=True)
        extra = body.get("groups", 0)
        if (
            isinstance(extra, bool)
            or not isinstance(extra, int)
            or not 0 <= extra <= 20
        ):
            raise web.HTTPBadRequest()
        for number in range(extra):
            # One more group each: two copies of a picture no other file has.
            with Image.new("RGB", (16, 16), (number * 9, 40, 90)) as image:
                image.save(dupes[0] / f"group_{number:02}_a.png")
            copy = (dupes[0] / f"group_{number:02}_a.png").read_bytes()
            (dupes[0] / f"group_{number:02}_b.png").write_bytes(copy)
        with Image.new("RGB", (48, 48), "#7a3b2e") as image:
            image.save(dupes[0] / "same.png")
        data = (dupes[0] / "same.png").read_bytes()
        (dupes[1] / "same.png").write_bytes(data)
        (dupes[0] / "copy.png").write_bytes(data)
        api.invalidate_gallery_cache()
        return web.json_response({"success": True})

    @routes.get("/__test__/system/digest")
    async def digest(request):
        uri = Path(api.db.model.db_path).as_uri() + "?mode=ro"
        rows = hashlib.sha256()
        conn = sqlite3.connect(uri, uri=True)
        try:
            for line in conn.iterdump():
                rows.update(line.encode("utf-8") + b"\n")
        finally:
            conn.close()
        media = hashlib.sha256()
        for name in ("output", "input"):
            for path in sorted((root / name).rglob("*")):
                # Automatic previews are written in the background (#316): media only.
                if path.is_file() and "thumbnails" not in path.relative_to(root).parts:
                    media.update(str(path.relative_to(root)).encode("utf-8"))
                    media.update(hashlib.sha256(path.read_bytes()).digest())
        return web.json_response({"rows": rows.hexdigest(), "media": media.hexdigest()})

    @routes.post("/__test__/system/library")
    async def library_state(request):
        data = await request.json()
        state = data.get("state")
        if state not in ("node_folder", "failing"):
            raise web.HTTPBadRequest()
        target = str(root / "user" / "__promptmanager")

        def located(source):
            return library_location.LibraryLocation(
                source,
                api.db.model.db_path,
                str(config_path),
                str(root),
                target if source == library_location.SOURCE_NODE_FOLDER else None,
                target_source=library_location.SOURCE_DEFAULT,
            )

        current = {"source": library_location.SOURCE_NODE_FOLDER}
        api._library_resolver = lambda: located(current["source"])

        def stand_in_move(plan, *, progress, **_kwargs):
            if state == "failing":
                raise library_move.MoveFailed("verify-failed")
            progress("Copying the database")
            counts = api.db.library_counts()
            current["source"] = library_location.SOURCE_DEFAULT
            return library_move.MoveResult(
                located(library_location.SOURCE_DEFAULT),
                {"prompts": counts["positive_prompts"], "generated_images": 0},
                ["prompts.db"],
                ["browser.db.moved-to-__promptmanager"],
            )

        library_move.move_library = stand_in_move
        return web.json_response({"success": True})

    return reset


class _FakeMonitor:
    """The image monitor's status surface (``get_status``) with no observer thread."""

    def __init__(self, directories, alive):
        self.monitored_directories = directories
        self._alive = alive

    def get_status(self):
        return {
            "running": True,
            "monitored_directories": list(self.monitored_directories),
            "handler_active": True,
            "observer_alive": self._alive,
        }
